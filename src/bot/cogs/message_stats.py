"""Registro de mensajes, experiencia y comandos de consulta de niveles.

Fuentes de XP en vivo (las reglas están en `bot.services.levels`):

- `on_message`: XP por mensaje, bonus del primer mensaje del día y racha.
- `on_raw_reaction_add`: XP para el autor de un mensaje cuando otro reacciona.
- `_voice_tick`: cada minuto, XP para quien está en voz sin mutear. Lee la
  caché de estados de voz de discord.py, así que no hace llamadas a la API.

Subir de nivel paga yapdollars con retención de IRPF (`EconomyService`).

Nada de esto corre hasta que un administrador enciende los niveles con el
comando `niveles` (cog `Admin`), que usa `start_import`, `activate` y
`overview` de este cog. La importación del historial convierte cada mensaje
antiguo en XP y, la primera vez que termina, enciende los niveles sola.

El nivel de cada uno se consulta en la sección 📊 Nivel de `perfil`
(`level_embed`); aquí queda `ranking`, que pone junto a cada nombre el
título que luce en la tienda (`shop.title_of`).
"""

from __future__ import annotations

import asyncio
import logging
import random
import sqlite3
import time
from collections import Counter, OrderedDict
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.cogs import achievements as logros
from bot.cogs import pets as mascotas
from bot.cogs import shop as tienda
from bot.repositories.message_stats import ImportStatus, LevelAward, MessageStatsRepository
from bot.services.achievements import StatDelta
from bot.services.birthdays import anniversary_xp
from bot.services.economy import (
    CURRENCY_EMOJI,
    BalanceLimitError,
    EconomyService,
    IncomeResult,
    format_amount,
    tax_line,
)
from bot.services.levels import (
    HISTORICAL_XP_PER_MESSAGE,
    MAX_MESSAGE_XP,
    MAX_VOICE_XP,
    MIN_MESSAGE_XP,
    MIN_VOICE_XP,
    MemberActivity,
    calculate_level_progress,
    current_streak,
    local_day,
    message_award,
    reaction_award,
    rewards_between,
    streak_multiplier,
    voice_award,
)
from bot.services.lootboxes import FREE_BOX_LEVELS
from bot.services.pets import Event, Moment
from bot.utils.cogs import find_cog
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

RANK_MEDALS = ("🥇", "🥈", "🥉")
LEVEL_COLOR = discord.Color.from_rgb(88, 101, 242)
#: Casillas de la barra de avance de nivel en `perfil`.
LEVEL_BAR = 16
NOT_READY = (
    "Los niveles están apagados en este servidor. "
    "Un administrador los enciende con `/niveles` (o `.niveles`)."
)
RANKING_PAGE_SIZE = 10
#: Reacciones (mensaje, quien reacciona) ya premiadas que se recuerdan para
#: que quitar y volver a poner la misma reacción no dé XP otra vez.
REACTION_MEMORY = 5_000


def build_ranking_embed(
    guild: discord.Guild,
    page: int,
    entries: Sequence[tuple[int, str, int]],
) -> discord.Embed:
    """Construye una tarjeta visual de ranking con podio y progreso de nivel.

    Args:
        guild: Servidor cuyo ranking se presenta.
        page: Número de página, empezando por 1.
        entries: Posición global, nombre legible y XP total de cada usuario.

    Returns:
        Embed listo para enviarse como respuesta de Discord.
    """
    embed = discord.Embed(
        title="🏆 Ranking de niveles",
        description="Los miembros con más experiencia de este servidor.",
        color=discord.Color.from_rgb(242, 177, 52),
    )
    embed.set_author(
        name=guild.name,
        icon_url=guild.icon.url if guild.icon is not None else None,
    )
    if guild.icon is not None:
        embed.set_thumbnail(url=guild.icon.url)

    for index, (_position, display_name, total_xp) in enumerate(entries[:3]):
        progress = calculate_level_progress(total_xp)
        progress_bar = _format_progress_bar(progress.xp_in_level, progress.xp_for_next_level)
        medal = RANK_MEDALS[index]
        embed.add_field(
            name=f"{medal} {display_name}",
            value=(
                f"**Nivel {progress.level}** · {total_xp:,} XP\n"
                f"`{progress_bar}`\n"
                f"{progress.xp_in_level:,}/{progress.xp_for_next_level:,} al siguiente"
            ),
            inline=True,
        )

    remaining = entries[3:]
    if remaining:
        lines = []
        for position, display_name, total_xp in remaining:
            progress = calculate_level_progress(total_xp)
            lines.append(
                f"**#{position} · {display_name}**\n"
                f"Nivel {progress.level}　·　{total_xp:,} XP　·　"
                f"{progress.xp_in_level:,}/{progress.xp_for_next_level:,} al siguiente"
            )
        embed.add_field(name="Clasificación", value="\n".join(lines), inline=False)

    embed.set_footer(text=f"Página {page} · Se muestran hasta {RANKING_PAGE_SIZE} miembros")
    return embed


def _format_progress_bar(current_xp: int, required_xp: int, width: int = 10) -> str:
    """Representa el progreso del nivel como una barra compacta."""
    filled = min(width, int(current_xp / required_xp * width))
    return "█" * filled + "░" * (width - filled)


def _describe_import(status: ImportStatus | None, *, running: bool) -> str:
    """Una línea con el estado de la importación del historial."""
    if status is None:
        return "sin importar"
    progress = (
        f"{status.scanned_channels}/{status.total_channels} canales, "
        f"{status.messages_counted:,} mensajes contados"
    )
    if status.status == "running" and running:
        return f"⏳ importando ({progress})"
    if status.status == "completed":
        return f"✅ importado ({progress})"
    if status.status == "partial":
        return (
            f"⚠️ importado a medias ({progress}); {status.failed_channels} sin permiso de "
            "«Leer el historial de mensajes». Dale el permiso y repite `importar` para sumarlos."
        )
    return f"⏸️ cortado a medias ({progress})"


#: Coletilla del aviso de subir de nivel.
LEVEL_LINES = (
    "¡Enhorabuena!",
    "¡Toma mangó!",
    "Sube más que el alquiler.",
    "Sale mañana en el BOE.",
    "Así se hace.",
    "Un nivel más, y los que quedan.",
    "Hacienda ya ha subido su parte.",
    "Estoy destruido, pero por fuera… ¡por dentro estoy nuevo!",
    "¡Jota Uve! ¡Pre!",
    "Desayuna con huevo y jugo de china del bueno: hoy se celebra.",
    "Trump ya dice que el mérito es suyo.",
    "Cada mensaje cuenta.",
    "¡Arriba, arriba!",
    "Escalafón de funcionario, pero sin oposición.",
)

IMPORT_DONE_LINES = (
    "Me he leído {mensajes} mensajes de {canales} canales. Niveles encendidos: cada mensaje y "
    "cada rato en voz suma.",
    "Contados {mensajes} mensajes en {canales} canales. `.ranking` y a presumir.",
    "Hecho: {mensajes} mensajes de {canales} canales, archivados mejor que el NO-DO. Los "
    "niveles ya cuentan; `.perfil` para ver el tuyo.",
    "{mensajes} mensajes en {canales} canales: más horas de lectura que un discurso de Fidel. "
    "Niveles encendidos; `.ranking` para ver quién manda.",
    "Recuento terminado: {mensajes} mensajes en {canales} canales. Niveles encendidos, y cada "
    "nivel paga yapdollars.",
    "¿Qué? ¿Qué? ¡Toma mangó! Ya me leí {mensajes} mensajes de {canales} canales y cada uno "
    "tiene su XP. Mirad dónde estáis con `.perfil` y `.ranking`.",
    "¡Jota Uve! ¡Pre! Me he leído {mensajes} mensajes de {canales} canales. `.ranking` y a "
    "presumir.",
    "Hazme café… ¡quiero café!, que me he leído {mensajes} mensajes en {canales} canales. "
    "Niveles encendidos: cada mensaje suma.",
)


class MessageStats(commands.Cog):
    """Registra XP por mensaje y expone consultas de nivel y ranking."""

    def __init__(
        self,
        bot: commands.Bot,
        repository: MessageStatsRepository,
        economy: EconomyService | None = None,
    ) -> None:
        self.bot = bot
        self.repository = repository
        self.economy = economy
        self._scan_tasks: dict[int, asyncio.Task[None]] = {}
        self._rewarded_reactions: OrderedDict[tuple[int, int], None] = OrderedDict()

    async def cog_load(self) -> None:
        """Arranca la tarea de XP por voz."""
        self._voice_tick.start()

    async def cog_unload(self) -> None:
        """Detiene la tarea de XP por voz."""
        self._voice_tick.cancel()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Da XP por mensaje (con su enfriamiento); nunca guarda el contenido."""
        if (
            message.guild is None
            or message.author.bot
            or message.webhook_id is not None
            or message.is_system()
        ):
            return

        guild_id = message.guild.id
        now = time.time()
        # Los potenciadores de la tienda multiplican el XP base (ver bot.cogs.shop), y
        # la mascota activa suma su pequeño bonus encima (ver bot.cogs.pets).
        boost = (
            tienda.xp_multiplier(self.bot, guild_id, message.author.id, now)
            * mascotas.xp_bonus(self.bot, guild_id, message.author.id, voice=False)
            * anniversary_xp(now)
        )
        base_xp = round(random.randint(MIN_MESSAGE_XP, MAX_MESSAGE_XP) * boost)

        def decide(_user_id: int, state: MemberActivity, cooldown: int) -> MemberActivity | None:
            return message_award(state, now=now, cooldown_seconds=cooldown, base_xp=base_xp)

        awards = await self.repository.grant_activity(guild_id, [message.author.id], decide)
        award = awards.get(message.author.id)
        if award is not None:
            logros.note(
                self.bot,
                guild_id,
                message.author.id,
                StatDelta(peak={"activity_streak_max": award.streak_days}),
            )
            await self._handle_level_up(message.guild, message.channel, message.author, award)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """Da XP al autor de un mensaje cuando otra persona reacciona."""
        author_id = payload.message_author_id
        if (
            payload.guild_id is None
            or author_id is None
            or author_id == payload.user_id
            or payload.member is None
            or payload.member.bot
        ):
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        author = guild.get_member(author_id)
        if author is None or author.bot:
            return
        key = (payload.message_id, payload.user_id)
        if key in self._rewarded_reactions:
            return
        self._rewarded_reactions[key] = None
        if len(self._rewarded_reactions) > REACTION_MEMORY:
            self._rewarded_reactions.popitem(last=False)

        now = time.time()

        def decide(_user_id: int, state: MemberActivity, _cooldown: int) -> MemberActivity | None:
            return reaction_award(state, now=now)

        awards = await self.repository.grant_activity(guild.id, [author_id], decide)
        award = awards.get(author_id)
        channel = guild.get_channel_or_thread(payload.channel_id)
        if award is not None and isinstance(channel, discord.abc.Messageable):
            await self._handle_level_up(guild, channel, author, award)

    # -- Voz -----------------------------------------------------------------------------

    @tasks.loop(seconds=60)
    async def _voice_tick(self) -> None:
        """Cada minuto: XP por voz."""
        try:
            enabled = await self.repository.enabled_guild_ids()
        except (OSError, sqlite3.Error):
            logger.exception("No se pudo leer qué servidores tienen niveles")
            return
        now = time.time()
        for guild in self.bot.guilds:
            if guild.id not in enabled:
                continue
            try:
                for channel in guild.voice_channels:
                    await self._award_voice_channel(guild, channel, now)
            except Exception:
                # Una excepción sin capturar pararía la tarea para siempre; se
                # registra y el siguiente minuto se vuelve a intentar.
                logger.exception("Error dando XP de voz en el servidor %s", guild.id)

    @_voice_tick.before_loop
    async def _before_voice_tick(self) -> None:
        await self.bot.wait_until_ready()

    async def _award_voice_channel(
        self, guild: discord.Guild, channel: discord.VoiceChannel, now: float
    ) -> None:
        """Da XP de voz a quien esté hablando de verdad en `channel`."""
        if guild.afk_channel is not None and channel.id == guild.afk_channel.id:
            return
        eligible = [member for member in channel.members if is_voice_active(member)]
        # Hace falta alguien más para que cuente: nadie farmea solo.
        if len(eligible) < 2:
            return
        base = {
            member.id: round(
                random.randint(MIN_VOICE_XP, MAX_VOICE_XP)
                * tienda.xp_multiplier(self.bot, guild.id, member.id, now)
                * mascotas.xp_bonus(self.bot, guild.id, member.id, voice=True)
                * anniversary_xp(now)
            )
            for member in eligible
        }

        def decide(user_id: int, state: MemberActivity, _cooldown: int) -> MemberActivity:
            return voice_award(state, now=now, base_xp=base[user_id])

        awards = await self.repository.grant_activity(guild.id, list(base), decide)
        by_id = {member.id: member for member in eligible}
        for user_id, award in awards.items():
            await self._handle_level_up(guild, channel, by_id[user_id], award)

    # -- Subidas de nivel ---------------------------------------------------------------

    async def _handle_level_up(
        self,
        guild: discord.Guild,
        channel: discord.abc.Messageable,
        member: discord.abc.User,
        award: LevelAward,
    ) -> None:
        """Si `award` hace subir de nivel, paga el premio y lo anuncia."""
        previous_level = calculate_level_progress(award.previous_xp).level
        current_level = calculate_level_progress(award.total_xp).level
        if current_level <= previous_level:
            return
        reward = await self._pay_level_reward(guild, member, previous_level, current_level)
        # Una caja de regalo cada FREE_BOX_LEVELS niveles (no mueve dinero: ver `lootboxes`).
        gifts = current_level // FREE_BOX_LEVELS - previous_level // FREE_BOX_LEVELS
        gift = None
        if gifts > 0:
            gift = await tienda.grant_box(
                self.bot,
                guild.id,
                member.id,
                "caja_regalo",
                because=f"llegar al nivel {current_level}",
                count=gifts,
            )
        channel = self._announce_target(guild, award, channel)
        await self._announce_level_up(
            guild, channel, member, current_level, reward=reward, gift=gift
        )
        delta = StatDelta(peak={"level_max": current_level})
        if reward is not None:
            delta.add["tax_paid"] = reward.tax
            delta.peak["balance_max"] = reward.balance
        await logros.track(self.bot, guild.id, member, channel, delta)

    @staticmethod
    def _announce_target(
        guild: discord.Guild, award: LevelAward, fallback: discord.abc.Messageable
    ) -> discord.abc.Messageable:
        """Canal de anuncios configurado con `niveles`, o donde se subió si no hay."""
        if award.announce_channel_id is None:
            return fallback
        channel = guild.get_channel(award.announce_channel_id)
        return channel if isinstance(channel, discord.abc.Messageable) else fallback

    async def _pay_level_reward(
        self,
        guild: discord.Guild,
        member: discord.abc.User,
        previous_level: int,
        current_level: int,
    ) -> IncomeResult | None:
        """Paga los yapdollars de los niveles alcanzados; `None` si no se pudo."""
        gross = rewards_between(previous_level, current_level)
        if self.economy is None or gross <= 0:
            return None
        try:
            return await self.economy.pay_income(
                guild.id, member.id, gross=gross, concept=f"nivel:{current_level}"
            )
        except (OSError, sqlite3.Error, BalanceLimitError):
            logger.exception(
                "No se pudo pagar el premio de nivel %s a %s en el servidor %s",
                current_level,
                member.id,
                guild.id,
            )
            return None

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra las estadísticas del servidor cuando el bot deja de pertenecer a él."""
        await self.repository.delete_guild_data(guild.id)
        logger.info("Se eliminaron las estadísticas del servidor %s", guild.id)

    # -- Administración (la usa el comando `niveles` del cog Admin) --------------------

    async def start_import(
        self,
        guild: discord.Guild,
        report_to: discord.abc.Messageable | None = None,
    ) -> str:
        """Inicia o reanuda la importación del historial en segundo plano.

        La tarea se separa del comando porque puede durar mucho más que una
        interacción de Discord. Al terminar, si es la primera vez, enciende
        los niveles y lo anuncia en `report_to`.

        Returns:
            Texto para quien pidió la importación.
        """
        current_task = self._scan_tasks.get(guild.id)
        if current_task is not None and not current_task.done():
            return "⏳ Ya hay una importación en curso; mira cómo va con `/niveles`."

        status = await self.repository.import_status(guild.id)
        if status is not None and status.status == "completed":
            return "✅ El historial ya está importado entero; no se repite para no contar doble."
        if status is not None and status.status == "running":
            # La base de datos dice que sigue, pero no hay tarea viva: el bot
            # se reinició a medias. Se marca como interrumpida para reanudarla.
            await self.repository.mark_import_interrupted(guild.id)

        channels = await self._discover_readable_channels(guild)
        cutoff_id = discord.utils.time_snowflake(datetime.now(UTC), high=False)
        if not await self.repository.start_import(guild.id, list(channels), cutoff_id):
            return "⏳ Ya hay una importación en curso; mira cómo va con `/niveles`."

        self._scan_tasks[guild.id] = asyncio.create_task(
            self._run_import(guild, channels, report_to),
            name=f"message-history-import:{guild.id}",
        )
        resumed = status is not None
        return (
            f"{'🔁 Importación reanudada' if resumed else '📥 Importación iniciada'}: "
            f"{len(channels)} canales e hilos. Cuando acabe, los niveles se encienden solos "
            "y lo aviso en este canal."
        )

    async def activate(self, guild: discord.Guild) -> str:
        """Enciende los niveles; la primera vez convierte el historial en XP.

        Returns:
            Texto para quien lo pidió.
        """
        enabled, seeded = await self.repository.enable_levels(
            guild.id, historical_xp_per_message=HISTORICAL_XP_PER_MESSAGE
        )
        if enabled:
            text = "🟢 Niveles encendidos."
            if seeded:
                text += f" {seeded:,} perfiles con su XP del historial."
            return text
        status = await self.repository.import_status(guild.id)
        if status is not None and status.status == "running":
            return "⏳ La importación sigue en marcha; al acabar se encienden solos."
        return (
            "Primero hay que importar el historial: "
            "`/niveles accion:importar` o `.niveles importar`."
        )

    async def overview(self, guild: discord.Guild) -> str:
        """Resumen del estado de los niveles del servidor, para administradores."""
        settings = await self.repository.level_settings(guild.id)
        status = await self.repository.import_status(guild.id)
        running = guild.id in self._scan_tasks and not self._scan_tasks[guild.id].done()

        lines = [
            "📊 **Niveles:** "
            + ("🟢 encendidos" if settings is not None and settings.enabled else "🔴 apagados")
        ]
        lines.append(f"Historial: {_describe_import(status, running=running)}")
        cooldown = settings.cooldown_seconds if settings is not None else None
        if cooldown is not None:
            lines.append(f"XP por mensaje: como mucho una vez cada {cooldown} s")
        channel_id = settings.announce_channel_id if settings is not None else None
        target = guild.get_channel(channel_id) if channel_id is not None else None
        if channel_id is None:
            lines.append("Anuncios de nivel: en el canal donde se sube")
        elif target is None:
            lines.append("Anuncios de nivel: ⚠️ el canal elegido ya no existe; se usa el de subida")
        else:
            lines.append(f"Anuncios de nivel: {target.mention}")

        if status is None:
            lines.append("\nSiguiente paso: `/niveles accion:importar` (o `.niveles importar`).")
        elif not running and status.status == "interrupted":
            lines.append("\nSe cortó a medias: `/niveles accion:importar` la reanuda.")
        return "\n".join(lines)

    async def message_count(
        self,
        interaction: discord.Interaction,
        miembro: discord.Member | None = None,
    ) -> None:
        """Consulta el total histórico y en vivo de un miembro del servidor."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Este comando solo está disponible dentro de un servidor.",
                ephemeral=True,
            )
            return

        member = miembro or interaction.user
        count = await self.repository.message_count(guild.id, member.id)
        await interaction.response.send_message(
            f"{member.mention} tiene **{count:,} mensajes** registrados en este servidor.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def level_embed(self, guild: discord.Guild, member: discord.abc.User) -> discord.Embed:
        """Sección 📊 Nivel de `perfil`: nivel, XP, avance y racha de días.

        Si los niveles están apagados en el servidor, lo dice en el embed.
        """
        name = discord.utils.escape_markdown(member.display_name)
        embed = discord.Embed(title=f"📊 Nivel de {name}", color=LEVEL_COLOR)
        settings = await self.repository.level_settings(guild.id)
        if settings is None or not settings.historical_seeded:
            embed.description = NOT_READY
            return embed
        activity = await self.repository.member_activity(guild.id, member.id)
        progress = calculate_level_progress(activity.total_xp)
        streak = current_streak(activity, local_day(time.time()))
        bonus = round((streak_multiplier(streak) - 1) * 100)
        filled = progress.xp_in_level * LEVEL_BAR // max(1, progress.xp_for_next_level)
        embed.description = (
            f"# Nivel {progress.level}\n"
            f"`{'█' * filled}{'░' * (LEVEL_BAR - filled)}` "
            + f"{progress.xp_in_level:,}/{progress.xp_for_next_level:,} XP hacia el "
            f"{progress.level + 1}\n".replace(",", ".")
            + f"XP total: **{activity.total_xp:,}**".replace(",", ".")
        )
        embed.add_field(
            name="🔥 Racha",
            value=(
                f"**{streak}** días seguidos escribiendo"
                + (f" · +{bonus} % de XP" if bonus else "")
                if streak
                else "Sin racha: escribe hoy y empieza una."
            ),
            inline=False,
        )
        embed.set_footer(text="La racha sube un día por cada día con mensajes (hora canaria).")
        return embed

    async def _ranking_impl(self, responder: CommandResponder, pagina: int) -> None:
        """Lógica compartida entre `/ranking` y `.ranking`."""
        guild = responder.guild
        if guild is None:
            await responder.send_error("Este comando solo está disponible dentro de un servidor.")
            return

        settings = await self.repository.level_settings(guild.id)
        if settings is None or not settings.historical_seeded:
            await responder.send_error(NOT_READY)
            return

        page_size = RANKING_PAGE_SIZE
        entries = await self.repository.level_leaderboard(
            guild.id, page_size, (pagina - 1) * page_size
        )
        if not entries:
            await responder.send_error(f"No hay perfiles en la página {pagina}.")
            return

        await responder.start_progress("🔄 Preparando el ranking...")
        name_semaphore = asyncio.Semaphore(5)

        async def resolve_entry(position: int, entry: tuple[int, int]) -> tuple[int, str, int]:
            user_id, total_xp = entry
            async with name_semaphore:
                display_name = await self._resolve_display_name(guild, user_id)
            if (title := tienda.title_of(self.bot, guild.id, user_id)) is not None:
                display_name = f"{display_name} · {title}"
            return position, display_name, total_xp

        resolved_entries = await asyncio.gather(
            *(
                resolve_entry(position, entry)
                for position, entry in enumerate(
                    entries,
                    start=(pagina - 1) * page_size + 1,
                )
            )
        )

        await responder.finish(
            embed=build_ranking_embed(guild, pagina, resolved_entries),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="ranking", description="Muestra el ranking del servidor.")
    @app_commands.guild_only()
    async def ranking(
        self,
        interaction: discord.Interaction,
        pagina: app_commands.Range[int, 1, 100] = 1,
    ) -> None:
        """Muestra hasta diez perfiles por página, ordenados por XP."""
        await self._ranking_impl(InteractionResponder(interaction), pagina)

    @commands.command(name="ranking")
    @commands.guild_only()
    async def ranking_text(self, ctx: commands.Context, pagina: int = 1) -> None:
        """Versión de texto (`.ranking`) de `/ranking`."""
        if not (1 <= pagina <= 100):
            await ctx.send("La página debe estar entre 1 y 100.")
            return
        await self._ranking_impl(ContextResponder(ctx), pagina)

    async def _resolve_display_name(self, guild: discord.Guild, user_id: int) -> str:
        """Resuelve el nombre actual del miembro o su nombre global de Discord.

        Los miembros que no están en caché se consultan individualmente para
        no requerir el intent privilegiado de lista de miembros. Si el usuario
        ya no pertenece al servidor, se intenta obtener su nombre global.
        """
        member = guild.get_member(user_id)
        if member is not None:
            return discord.utils.escape_markdown(member.display_name)

        try:
            member = await guild.fetch_member(user_id)
        except discord.NotFound:
            member = None
        except (discord.Forbidden, discord.HTTPException):
            logger.warning(
                "No se pudo resolver el miembro %s en el servidor %s",
                user_id,
                guild.id,
                exc_info=True,
            )
            member = None

        if member is not None:
            return discord.utils.escape_markdown(member.display_name)

        try:
            user = await self.bot.fetch_user(user_id)
        except discord.NotFound:
            logger.info("La cuenta Discord %s ya no está disponible", user_id)
            return "Cuenta no disponible"
        except (discord.Forbidden, discord.HTTPException):
            logger.warning(
                "No se pudo obtener el nombre global del usuario %s",
                user_id,
                exc_info=True,
            )
            return "Nombre no disponible"

        return discord.utils.escape_markdown(user.global_name or user.name)

    async def _announce_level_up(
        self,
        guild: discord.Guild,
        channel: discord.abc.Messageable,
        member: discord.abc.User,
        level: int,
        *,
        reward: IncomeResult | None = None,
        gift: str | None = None,
    ) -> None:
        """Publica el aviso en el canal donde se ganó el nivel, con el premio cobrado.

        `gift` es la línea de la caja de regalo que se gana cada 5 niveles, si toca.
        """
        text = (
            f"¡{discord.utils.escape_markdown(member.display_name)} "
            f"ha alcanzado el nivel **{level}**! {random.choice(LEVEL_LINES)}"
        )
        if reward is not None:
            text += (
                f" {CURRENCY_EMOJI} +{format_amount(reward.net)}\n"
                f"{tax_line(reward.gross, reward.tax, reward.rate)}"
            )
        if gift:
            text += f"\n{gift}"
        if pet := await mascotas.cameo(self.bot, guild.id, member.id, Moment(Event.LEVEL)):
            text += f"\n{pet}"
        try:
            await channel.send(
                text,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.warning(
                "No se pudo publicar la subida de nivel en canal %s del servidor %s",
                getattr(channel, "id", "desconocido"),
                guild.id,
                exc_info=True,
            )

    async def _discover_readable_channels(
        self, guild: discord.Guild
    ) -> dict[int, discord.abc.Messageable]:
        """Reúne canales de texto e hilos accesibles, incluidos hilos archivados."""
        channels: dict[int, discord.abc.Messageable] = {}
        for channel in guild.text_channels:
            channels[channel.id] = channel
        for thread in guild.threads:
            channels[thread.id] = thread

        parents = [
            channel
            for channel in guild.channels
            if isinstance(channel, (discord.TextChannel, discord.ForumChannel))
        ]
        for parent in parents:
            await self._add_archived_threads(parent, channels, private=False)
            await self._add_archived_threads(parent, channels, private=True)

        return channels

    async def _add_archived_threads(
        self,
        parent: discord.TextChannel | discord.ForumChannel,
        channels: dict[int, discord.abc.Messageable],
        *,
        private: bool,
    ) -> None:
        """Añade hilos archivados; las restricciones de acceso quedan explícitas en logs."""
        if isinstance(parent, discord.ForumChannel):
            if private:
                return
            joined_modes = (False,)
        else:
            joined_modes = (True, False) if private else (False,)

        for joined in joined_modes:
            try:
                if isinstance(parent, discord.ForumChannel):
                    archived_threads: AsyncIterator[discord.Thread] = parent.archived_threads(
                        limit=None
                    )
                else:
                    archived_threads = parent.archived_threads(
                        limit=None,
                        private=private,
                        joined=joined,
                    )
                async for thread in archived_threads:
                    channels[thread.id] = thread
            except (discord.Forbidden, discord.HTTPException) as error:
                logger.info(
                    "No se pudieron enumerar hilos archivados del canal %s "
                    "(privados=%s, joined=%s): %s",
                    parent.id,
                    private,
                    joined,
                    error,
                )

    async def _run_import(
        self,
        guild: discord.Guild,
        channels: dict[int, discord.abc.Messageable],
        report_to: discord.abc.Messageable | None = None,
    ) -> None:
        """Cuenta cada canal pendiente y guarda su agregado de forma reanudable."""
        guild_id = guild.id
        try:
            status = await self.repository.import_status(guild_id)
            if status is None:
                logger.error("Falta el estado de importación del servidor %s", guild_id)
                return

            channel_by_id = channels
            for channel_id in await self.repository.pending_channel_ids(guild_id):
                channel = channel_by_id.get(channel_id)
                if channel is None:
                    await self.repository.mark_channel_failed(
                        guild_id, channel_id, "El canal ya no existe o no es accesible."
                    )
                    continue

                counts: Counter[int] = Counter()
                messages_scanned = 0
                messages_counted = 0
                await self.repository.update_channel_progress(guild_id, channel_id, 0, 0)
                try:
                    history = channel.history(
                        limit=None,
                        before=discord.Object(id=status.cutoff_id),
                        oldest_first=False,
                    )
                    async for message in history:
                        messages_scanned += 1
                        if (
                            message.author.bot
                            or message.webhook_id is not None
                            or message.is_system()
                        ):
                            continue
                        counts[message.author.id] += 1
                        messages_counted += 1
                        if messages_scanned % 100 == 0:
                            await self.repository.update_channel_progress(
                                guild_id,
                                channel_id,
                                messages_scanned,
                                messages_counted,
                            )
                except (discord.Forbidden, discord.NotFound, discord.HTTPException) as error:
                    await self.repository.mark_channel_failed(
                        guild_id, channel_id, type(error).__name__
                    )
                    logger.warning(
                        "No se pudo leer el historial del canal %s en el servidor %s: %s",
                        channel_id,
                        guild_id,
                        error,
                    )
                    continue

                await self.repository.update_channel_progress(
                    guild_id,
                    channel_id,
                    messages_scanned,
                    messages_counted,
                )
                await self.repository.save_channel_counts(guild_id, channel_id, dict(counts))

            await self.repository.finish_import(guild_id)
            final_status = await self.repository.import_status(guild_id)
            if final_status is not None:
                logger.info(
                    "Importación histórica del servidor %s: estado=%s, canales=%d/%d, fallidos=%d",
                    guild_id,
                    final_status.status,
                    final_status.scanned_channels,
                    final_status.total_channels,
                    final_status.failed_channels,
                )
                await self._activate_after_import(guild, final_status, report_to)
        except asyncio.CancelledError:
            raise
        except (OSError, sqlite3.Error):
            logger.exception(
                "Error de almacenamiento durante la importación del servidor %s",
                guild_id,
            )
            await self.repository.mark_import_interrupted(guild_id)
        except Exception:
            logger.exception(
                "Error inesperado durante la importación del servidor %s",
                guild_id,
            )
            await self.repository.mark_import_interrupted(guild_id)
        finally:
            self._scan_tasks.pop(guild_id, None)

    async def _activate_after_import(
        self,
        guild: discord.Guild,
        status: ImportStatus,
        report_to: discord.abc.Messageable | None,
    ) -> None:
        """Enciende los niveles la primera vez que termina una importación.

        Si ya se habían encendido antes (una reimportación de canales que
        fallaron), no se tocan: un administrador pudo haberlos apagado.
        """
        settings = await self.repository.level_settings(guild.id)
        if settings is not None and settings.historical_seeded:
            return
        enabled, _seeded = await self.repository.enable_levels(
            guild.id, historical_xp_per_message=HISTORICAL_XP_PER_MESSAGE
        )
        if not enabled or report_to is None:
            return
        text = random.choice(IMPORT_DONE_LINES).format(
            mensajes=f"{status.messages_counted:,}",
            canales=status.scanned_channels,
        )
        if status.failed_channels:
            text += (
                f"\n⚠️ {status.failed_channels} canales no los pude leer por permisos; "
                "un admin puede darme «Leer el historial de mensajes» "
                "y repetir `.niveles importar`."
            )
        try:
            await report_to.send(text, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            logger.warning(
                "No se pudo anunciar el fin de la importación en el servidor %s",
                guild.id,
                exc_info=True,
            )


async def levels_enabled(bot: commands.Bot, guild_id: int) -> bool:
    """Si el servidor tiene los niveles encendidos; `False` si el cog no está o falla.

    La Rueda lo mira para no enseñar porciones de XP que no se podrían dar.
    """
    if (cog := find_cog(bot, MessageStats)) is None:
        return False
    try:
        return guild_id in await cog.repository.enabled_guild_ids()
    except Exception:
        logger.exception("No se pudo leer si %s tiene los niveles encendidos", guild_id)
        return False


async def give_xp(
    bot: commands.Bot,
    guild: discord.Guild,
    member: discord.abc.User,
    channel: object,
    xp: int,
) -> bool:
    """Suma `xp` a un miembro (un premio, como el de la Rueda) y anuncia si sube de nivel.

    No mira el enfriamiento ni los topes de los mensajes: es un premio, no actividad.
    Subir de nivel paga su premio de siempre, como con cualquier otra XP. Nunca lanza.

    Returns:
        Si se ha sumado (no, con los niveles apagados o si algo falla).
    """
    if xp <= 0 or (cog := find_cog(bot, MessageStats)) is None:
        return False

    def decide(_user_id: int, state: MemberActivity, _cooldown: int) -> MemberActivity:
        return replace(state, total_xp=state.total_xp + xp)

    try:
        awards = await cog.repository.grant_activity(guild.id, [member.id], decide)
        award = awards.get(member.id)
        if award is None:
            return False
        if isinstance(channel, discord.abc.Messageable):
            await cog._handle_level_up(guild, channel, member, award)
    except Exception:
        logger.exception("No se pudo dar la XP de premio a %s", member.id)
        return False
    return True


def is_voice_active(member: discord.Member) -> bool:
    """Si un miembro cuenta para el XP de voz: persona, sin mute ni ensordecer."""
    voice = member.voice
    return (
        not member.bot
        and voice is not None
        and not (voice.self_mute or voice.self_deaf or voice.mute or voice.deaf)
    )


async def setup(bot: commands.Bot) -> None:
    """Registra los comandos de estadísticas y el contador de mensajes en vivo."""
    repository = bot.message_stats
    await bot.add_cog(MessageStats(bot, repository, getattr(bot, "economy", None)))
