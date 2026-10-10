"""Cog `reinicio` y avisos de novedades: el bot al día con `main`.

Solo pueden usarlo las personas de `DEPLOYERS` (quien mantiene el bot y quien
lo hostea). El bot no se reinicia a sí mismo: deja una nota en el buzón
compartido con el NAS (`bot.services.deploy`) y `actualizar.sh --solicitud`,
lanzado cada minuto por cron, descarga `main`, reconstruye y reinicia. Cuando
el script termina, este cog publica el resultado en el canal donde se pidió,
lo haga el bot viejo (si no hubo reinicio) o el nuevo.

Novedades: cada vez que el NAS despliega commits nuevos (de noche o por
`reinicio`), deja la lista de PR en el buzón. Este cog pide a GitHub la
descripción de cada uno (`bot.services.changelog`) y publica el aviso en cada
servidor: la lista de cambios con su enlace y autor y, en modo detallado, una
ficha por PR con su descripción, como en GitHub. Canal, detalle y si se
publica o no se configuran con `cambios` (cog `Admin`); el canal
puede ser un hilo. El aviso es
informativo: sin botones, logros ni bromas.

`reinicio` no tiene logros: es una utilidad interna de dos personas (excepción
de la Biblia, sección "Logros"). No sale en `ayuda`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.repositories.news import NewsSettings
from bot.services.changelog import (
    Fetch,
    NewsEntry,
    NewsItem,
    fetch_pulls,
    github_fetcher,
    parse_news,
)
from bot.services.deploy import DeployRequest, DeployResult, Mailbox
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

logger = logging.getLogger(__name__)

#: Quién puede pedir el reinicio: Yeyo y Dani (el que hostea el bot en su NAS).
DEPLOYERS: frozenset[int] = frozenset({403646452414545921, 498473711687434241})

#: Cada cuánto se mira si el NAS ha dejado el resultado o novedades.
POLL_SECONDS = 15

NEWS_CHANNEL_NAME = "chat-general"
NEWS_COLOR = discord.Color.from_rgb(200, 160, 60)
NEWS_TITLE = "📜 Novedades del bot"
#: Cambios que se enseñan en un aviso; el resto se resume en "y N más".
NEWS_LIMIT = 12
#: Largo máximo de cada línea de la lista, para que quepa en un embed.
NEWS_LINE_CHARS = 160
#: Largo máximo de la descripción de un PR en su ficha (un embed admite 4.096).
DETAIL_CHARS = 3_000
#: Límites de Discord por mensaje: 10 embeds y 6.000 caracteres entre todos.
EMBEDS_PER_MESSAGE = 10
CHARS_PER_MESSAGE = 6_000


def result_message(result: DeployResult) -> str:
    """Texto público con el resultado del reinicio."""
    who = f"<@{result.request.user_id}> " if result.request else ""
    summary = result.summary or "sin detalles; mira `.despliegue/actualizar.log` en el NAS"
    if result.ok:
        return f"✅ {who}Ya estoy de vuelta, actualizaíto y con flow. {summary}"
    return f"❌ {who}El reinicio no ha salido, sigo con la versión de antes. {summary}"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _summary_line(entry: NewsEntry) -> str:
    """Una línea de la lista, como «What's Changed» de GitHub: título, PR y autor."""
    text = f"• {_clip(entry.item.headline, NEWS_LINE_CHARS)}"
    pull = entry.pull
    if pull is None:
        return text
    text += f" · [#{pull.number}]({pull.url})"
    if pull.author:
        text += f" · {pull.author}"
    return text


def news_summary(entries: list[NewsEntry], limit: int = NEWS_LIMIT) -> discord.Embed:
    """Primer embed del aviso: la lista de cambios desplegados."""
    lines = [_summary_line(entry) for entry in entries[:limit]]
    if len(entries) > limit:
        lines.append(f"…y {len(entries) - limit} más.")
    header = "Cambios incluidos en la versión que acaba de desplegarse:"
    return discord.Embed(
        title=NEWS_TITLE, description="\n".join([header, "", *lines]), color=NEWS_COLOR
    )


def _cut_body(body: str, url: str) -> str:
    """Recorta la descripción por un salto de línea y enlaza al resto."""
    if len(body) <= DETAIL_CHARS:
        return body
    more = f"\n\n[Sigue en GitHub]({url})"
    cut = body[: DETAIL_CHARS - len(more) - 1]
    newline = cut.rfind("\n")
    if newline > len(cut) // 2:
        cut = cut[:newline]
    return cut.rstrip() + "\n…" + more


def news_detail(entry: NewsEntry) -> discord.Embed | None:
    """Ficha de un PR con su descripción; `None` si GitHub no lo encontró."""
    pull = entry.pull
    if pull is None:
        return None
    if pull.body:
        description = _cut_body(pull.body, pull.url)
    elif entry.item.commits:
        description = "\n".join(f"• {commit}" for commit in entry.item.commits)
    else:
        description = "Sin descripción."
    embed = discord.Embed(
        title=_clip(entry.item.headline, 256),
        url=pull.url,
        description=description,
        color=NEWS_COLOR,
        timestamp=pull.merged_at,
    )
    if pull.author:
        embed.set_author(name=pull.author, url=pull.author_url, icon_url=pull.author_avatar)
    return embed.set_footer(text=f"{pull.repo} · PR #{pull.number}")


def news_messages(entries: list[NewsEntry], *, detailed: bool) -> list[list[discord.Embed]]:
    """Embeds del aviso repartidos en mensajes que caben en los límites de Discord."""
    embeds = [news_summary(entries)]
    if detailed:
        embeds += [e for entry in entries[:NEWS_LIMIT] if (e := news_detail(entry)) is not None]
    messages: list[list[discord.Embed]] = []
    size = 0
    for embed in embeds:
        if (
            not messages
            or len(messages[-1]) == EMBEDS_PER_MESSAGE
            or (size + len(embed) > CHARS_PER_MESSAGE)
        ):
            messages.append([])
            size = 0
        messages[-1].append(embed)
        size += len(embed)
    return messages


#: Dónde se puede publicar el aviso: un canal de texto o un hilo (también un post de foro).
NewsTarget = discord.TextChannel | discord.Thread


async def resolve_target(guild: discord.Guild, channel_id: int) -> NewsTarget | None:
    """El canal de texto o hilo con ese ID, aunque el hilo esté archivado.

    Discord no guarda los hilos archivados en la caché del bot: si no está,
    se pide a la API. `None` si no existe, el bot no lo ve o no es un sitio
    donde se pueda publicar (un canal de voz, un foro entero).
    """
    channel = guild.get_channel_or_thread(channel_id)
    if channel is None:
        try:
            channel = await guild.fetch_channel(channel_id)
        except discord.HTTPException:
            return None
    return channel if isinstance(channel, NewsTarget) else None


async def news_channel(guild: discord.Guild, settings: NewsSettings) -> NewsTarget | None:
    """Canal del aviso: el elegido con `cambios`, si no `#chat-general` o el del sistema.

    Si el elegido es un hilo archivado, al escribir en él Discord lo desarchiva
    solo. Si se ha borrado o el bot ya no lo ve, se vuelve al canal de serie.
    """
    if settings.channel_id is not None:
        channel = await resolve_target(guild, settings.channel_id)
        if channel is not None:
            return channel
    return discord.utils.get(guild.text_channels, name=NEWS_CHANNEL_NAME) or guild.system_channel


def can_post(channel: NewsTarget, me: discord.Member) -> bool:
    """Si el bot puede escribir ahí: en un hilo cuenta el permiso de hilos, no el de mensajes."""
    permissions = channel.permissions_for(me)
    if isinstance(channel, discord.Thread):
        if channel.locked and not permissions.manage_threads:
            return False
        return permissions.send_messages_in_threads
    return permissions.send_messages


class OldNewsButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"novedades:leido:(?P<edition>\d+)",
):
    """Botón 📜 Leído que llevaban los avisos de novedades antiguos.

    Los avisos nuevos no tienen botón, pero los ya publicados siguen en los
    canales; sin esto, pulsarlos daría «Esta interacción ha fallado».
    """

    def __init__(self, edition: int) -> None:
        super().__init__(
            discord.ui.Button(label="Leído", emoji="📜", custom_id=f"novedades:leido:{edition}")
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
        /,
    ) -> OldNewsButton:
        return cls(int(match["edition"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        # Solo memoria: se contesta directamente.
        await interaction.response.send_message(
            "Este botón ya no hace nada. Las novedades se publican sin él.", ephemeral=True
        )


class Deploy(commands.Cog, name="Despliegue"):
    """Comando `reinicio`, aviso de su resultado y avisos de novedades."""

    def __init__(
        self,
        bot: commands.Bot,
        mailbox: Mailbox | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        fetch: Fetch | None = None,
    ) -> None:
        self.bot = bot
        self.mailbox = mailbox or Mailbox()
        self._clock = clock or (lambda: datetime.now(UTC))
        #: Cómo se piden los PR; sin él, a la API de GitHub (las pruebas lo cambian).
        self._fetch = fetch

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(OldNewsButton)
        self._poll.start()

    async def cog_unload(self) -> None:
        self._poll.cancel()

    async def request_impl(self, responder: CommandResponder) -> None:
        """Lógica compartida entre `/reinicio` y `.reinicio`."""
        member = responder.member
        if member is None or member.id not in DEPLOYERS:
            await responder.send_error("🚫 Este botón rojo no es pa' ti, mi pana.")
            return
        channel_id = getattr(responder.channel, "id", None)
        if channel_id is None:
            await responder.send_error("No sé en qué canal avisarte; pídelo desde un canal.")
            return

        now = self._clock()
        pending = self.mailbox.pending(now)
        if pending is not None:
            minutes = int((now - pending.requested_at).total_seconds() // 60)
            await responder.send_error(
                f"⏳ Ya hay un reinicio pedido por <@{pending.user_id}> hace {minutes} min. "
                "Si en un par de minutos no pasa nada, el cron del NAS no está puesto "
                "(README, «Actualización automática»)."
            )
            return

        try:
            self.mailbox.request(DeployRequest(channel_id, member.id, now))
        except OSError:
            logger.exception("No se pudo dejar la petición de reinicio en el buzón")
            await responder.send_error(
                "No puedo escribir en el buzón del NAS (`.despliegue/buzon`). "
                "¿Se arrancó el bot con `actualizar.sh`? Mira el README."
            )
            return
        logger.info("Reinicio pedido por %s", member.id)
        await responder.send(
            "🔁 Pedido. En menos de un minuto el NAS baja lo último de GitHub, "
            "reconstruye y me reinicia (unos minutos). Aviso aquí cuando acabe.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="reinicio", description="Reinicia el bot con lo último de GitHub.")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def request_slash(self, interaction: discord.Interaction) -> None:
        """Pide al NAS que actualice y reinicie el bot.

        Solo para los IDs de `DEPLOYERS`, comprobado en cada uso; que Discord
        oculte el `/` a quien no es administrador es solo estética. Efecto
        visible: el bot se reinicia unos segundos y luego publica el resultado
        en este canal.
        """
        await self.request_impl(InteractionResponder(interaction))

    @commands.command(name="reinicio", hidden=True)
    @commands.guild_only()
    async def request_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.reinicio`) de `/reinicio`."""
        await self.request_impl(ContextResponder(ctx))

    async def announce(self) -> bool:
        """Publica el resultado del NAS si ya lo hay. Devuelve si había."""
        result = self.mailbox.take_result()
        if result is None:
            return False
        logger.info("Resultado del reinicio: ok=%s %s", result.ok, result.summary)
        if result.request is None:
            return True
        channel = self.bot.get_channel(result.request.channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            logger.warning("No encuentro el canal %s para el aviso", result.request.channel_id)
            return True
        try:
            await channel.send(
                result_message(result),
                allowed_mentions=discord.AllowedMentions(users=True, everyone=False, roles=False),
            )
        except discord.HTTPException:
            logger.exception("No se pudo publicar el resultado del reinicio")
        return True

    async def announce_news(self) -> bool:
        """Publica en cada servidor las novedades que haya dejado el NAS.

        GitHub se consulta una vez por aviso, no por servidor.

        Returns:
            Si había novedades (aunque no se hayan podido publicar en todos).
        """
        lines = self.mailbox.take_news()
        if lines is None:
            return False
        items = parse_news(lines)
        logger.info("Novedades desplegadas: %s", [item.headline for item in items])
        entries = await self._with_pulls(items)
        for guild in self.bot.guilds:
            settings = await self._settings(guild.id)
            channel = await news_channel(guild, settings) if settings.enabled else None
            if channel is None:
                continue
            try:
                for embeds in news_messages(entries, detailed=settings.detailed):
                    await channel.send(embeds=embeds)
            except discord.HTTPException:
                logger.exception("No se pudieron publicar las novedades en %s", guild.id)
        return True

    async def _with_pulls(self, items: list[NewsItem]) -> list[NewsEntry]:
        if self._fetch is not None:
            return await fetch_pulls(items, self._fetch)
        async with aiohttp.ClientSession() as session:
            return await fetch_pulls(items, github_fetcher(session))

    async def _settings(self, guild_id: int) -> NewsSettings:
        repository = getattr(self.bot, "news", None)
        if repository is None:
            return NewsSettings()
        try:
            return await repository.settings(guild_id)
        except Exception:
            # Mejor el aviso con los ajustes de serie que ninguno.
            logger.exception("No se pudieron leer los ajustes de novedades de %s", guild_id)
            return NewsSettings()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra los ajustes del aviso del servidor que el bot abandona."""
        repository = getattr(self.bot, "news", None)
        if repository is not None:
            await repository.delete_guild_data(guild.id)

    @tasks.loop(seconds=POLL_SECONDS)
    async def _poll(self) -> None:
        await self.announce()
        await self.announce_news()

    @_poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    """Punto de entrada usado por `bot.load_extension` para registrar el cog."""
    await bot.add_cog(Deploy(bot))
