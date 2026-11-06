"""Perfil: `perfil [miembro]`, todo lo de un miembro en un mismo menú.

Junta en un solo mensaje lo que antes eran cuatro comandos (`nivel`,
`patrimonio`, `logros` y `mochila`) y añade el curro y las rachas. Un
desplegable cambia de sección en el mismo mensaje:

- 📋 **Resumen**: un renglón de cada sección, debajo del título que luce el
  miembro si tiene uno (`shop.title_of`).
- 📊 **Nivel**: nivel, XP y racha de días (`MessageStats.level_embed`).
- 🏰 **Patrimonio**: activos y lo que cobraría el Patrimonio
  (`Patrimonio.patrimonio_embed`, que también apunta sus logros).
- 🪏 **Trabajo**: contrato y vida laboral (`Work.work_embed`). Para fichar,
  `pala`: ahí está el minijuego.
- 🏆 **Logros**: la portada de logros; el botón 🏆 abre el menú por
  categorías en un mensaje aparte (`Achievements.build_view`).
- 🔥 **Rachas**: la racha vigente de días escribiendo y los récords de todas
  las rachas que cuentan los logros.
- 🎒 **Objetos**: la mochila en texto; el botón 🎒 la abre en un mensaje
  aparte para usar objetos y ponerse roles (`Tienda.backpack_view`).
- ⭐ **Deseos**: la lista de deseos de la tienda, que es pública
  (`Tienda.wishlist_embed`). Se edita desde la ficha de cada artículo.
- 🐓 **Gallo**: la ficha del gallo de pelea (`Gallos.gallo_embed`).
- 🔴 **4 en raya**: la ficha, la racha y los retos de hoy (`Enraya.enraya_embed`).
- ♟️ **Backgammon**: la ficha, los gammons, la racha y los retos de hoy
  (`Backgammon.backgammon_embed`).
- 🏡 **Casa**: el Confort de tu vivienda, sus bufos y los suministros (`Casa.home_embed`).

Cada sección la pinta el cog dueño de esos datos y este cog solo la pide con
`find_cog` (ver Biblia, «Cogs y comandos»). Si un cog no está cargado, su
sección lo dice en vez de romper el panel.

No mueve dinero. Solo quien abre el perfil cambia de sección; los demás
reciben un aviso para abrir el suyo. Mirar el perfil cuenta para los logros
de mirar logros y patrimonio, propios o ajenos, y la sección 📋 Resumen deja
hablar a la mascota activa (`Event.PROFILE`).

Permisos del bot: enviar mensajes e insertar enlaces.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import donations as donaciones
from bot.cogs import pets as mascotas
from bot.cogs import shop as tienda
from bot.cogs.achievements import Achievements, progress_bar
from bot.cogs.backgammon import Backgammon
from bot.cogs.casa import Casa
from bot.cogs.enraya import Enraya
from bot.cogs.gallos import Gallos
from bot.cogs.message_stats import MessageStats
from bot.cogs.patrimonio import Patrimonio
from bot.cogs.shop import Tienda
from bot.cogs.work import Work
from bot.services.achievements import (
    BY_ID,
    COUNTED,
    PERFIL_SECTIONS,
    PERFIL_SEEN_PREFIX,
    StatDelta,
    points,
)
from bot.services.economy import format_amount
from bot.services.levels import TIMEZONE, calculate_level_progress, current_streak, local_day
from bot.services.pets import Event, Moment
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit, notify

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

COLOR = discord.Color.from_rgb(52, 152, 219)
VIEW_TIMEOUT = 600
#: Secciones del menú, en orden: `(clave, emoji, nombre, descripción)`.
SECTIONS: tuple[tuple[str, str, str, str], ...] = (
    ("resumen", "📋", "Resumen", "Un vistazo a todo"),
    ("nivel", "📊", "Nivel", "Nivel, XP y racha de días"),
    ("patrimonio", "🏰", "Patrimonio", "Todo lo que tienes, valorado"),
    ("trabajo", "🪏", "Trabajo", "Tu curro y tu vida laboral"),
    ("logros", "🏆", "Logros", "Puntos, categorías y los últimos"),
    ("rachas", "🔥", "Rachas", "Tus mejores rachas de todo"),
    ("objetos", "🎒", "Objetos", "Lo que llevas en la mochila"),
    ("deseos", "⭐", "Deseos", "Tu lista de deseos de la tienda"),
    ("gallo", "🐓", "Gallo", "Tu gallo de pelea y su historial"),
    ("enraya", "🔴", "4 en raya", "Tu ficha, tu racha y los retos de hoy"),
    ("backgammon", "♟️", "Backgammon", "Tu ficha, tus gammons y los retos de hoy"),
    ("casa", "🏡", "Casa", "Tu casa: Confort, bufos y suministros"),
)
SECTION_KEYS = tuple(key for key, *_ in SECTIONS)
assert SECTION_KEYS == PERFIL_SECTIONS, "el logro «Expediente completo» cuenta estas secciones"
#: Horas (canarias) de «Crisis existencial de madrugada».
NIGHT_HOURS = range(3, 5)

#: Récords de rachas que guardan los logros: `(estadística, emoji, qué es, unidad)`.
STREAKS: tuple[tuple[str, str, str, str], ...] = (
    ("activity_streak_max", "💬", "Días seguidos hablando", "días"),
    ("work_streak_max", "🪏", "Días seguidos fichando", "días"),
    ("imv_streak_max", "🏛️", "Días seguidos cobrando el IMV", "días"),
    ("pet_streak_max", "🐾", "Días seguidos cuidando a las mascotas", "días"),
    ("beer_streak_max", "🍻", "Días seguidos de beernight", "días"),
    ("slots_daily_streak", "🎰", "Días seguidos con el giro del día", "días"),
    ("interest_capped_streak", "🏦", "Días seguidos cobrando los intereses máximos", "días"),
    ("casino_win_streak_max", "🍀", "Jugadas seguidas ganando en el casino", "jugadas"),
    ("casino_loss_streak_max", "🥀", "Jugadas seguidas perdiendo en el casino", "jugadas"),
    ("roulette_streak_max", "🎡", "Tiradas seguidas ganando en una mesa de ruleta", "tiradas"),
    ("porra_win_streak_max", "🎫", "Porras seguidas acertadas", "porras"),
    ("mines_streak_max", "💎", "Casillas destapadas en una partida de minas", "casillas"),
    ("tower_floor_max", "🐉", "Pisos subidos en una partida de la Torre del dragón", "pisos"),
    ("war_win_streak_max", "⚔️", "Manos seguidas ganadas en una mesa de la Guerra", "manos"),
    ("bj_streak_max", "🃏", "Manos seguidas ganadas en una mesa de blackjack", "manos"),
    ("voice_mute_streak_max", "🔇", "Minutos seguidos en voz silenciado", "minutos"),
)


def _name(user: discord.abc.User) -> str:
    return discord.utils.escape_markdown(user.display_name)


def missing_embed(title: str) -> discord.Embed:
    """Sección cuyo cog no está cargado: se dice en vez de romper el panel."""
    return discord.Embed(
        title=title, description="Esta parte del bot no está disponible ahora.", color=COLOR
    )


def streaks_embed(name: str, stats: dict[str, int], writing_now: int) -> discord.Embed:
    """Sección 🔥 Rachas: la racha vigente de días escribiendo y los récords."""
    embed = discord.Embed(title=f"🔥 Rachas de {name}", color=COLOR)
    embed.description = (
        f"## 💬 {writing_now} días seguidos escribiendo"
        if writing_now
        else "Ahora mismo no llevas racha de días escribiendo. Hoy es buen día para empezar."
    )
    records = [
        f"{emoji} {label}: **{stats[stat]:,}** {unit}".replace(",", ".")
        for stat, emoji, label, unit in STREAKS
        if stats.get(stat, 0) > 0
    ]
    embed.add_field(
        name="🏅 Récords",
        value="\n".join(records) if records else "Ni una racha todavía. Todo por delante.",
        inline=False,
    )
    embed.set_footer(
        text="Los récords son los mejores de siempre; los logros de rachas salen de aquí."
    )
    return embed


def perfil_stats(
    viewer: discord.abc.User, target: discord.abc.User, now: float | None = None
) -> StatDelta:
    """Logros de abrir un perfil: propio o ajeno, de madrugada y la sección del resumen."""
    if viewer.id != target.id:
        return StatDelta(add={"perfil_others": 1})
    add = {"perfil_views": 1, f"{PERFIL_SEEN_PREFIX}resumen": 1}
    hour = datetime.fromtimestamp(now if now is not None else time.time(), TIMEZONE).hour
    if hour in NIGHT_HOURS:
        add["perfil_night"] = 1
    return StatDelta(add=add)


class SectionSelect(discord.ui.Select):
    """Desplegable de secciones del perfil."""

    def __init__(self, view: PerfilView) -> None:
        options = [
            discord.SelectOption(
                label=label, value=key, emoji=emoji, description=text, default=key == view.key
            )
            for key, emoji, label, text in SECTIONS
        ]
        super().__init__(
            placeholder="Elige una sección", options=options, row=0, custom_id="perfil:seccion"
        )
        self.perfil = view

    async def callback(self, interaction: discord.Interaction) -> None:
        # Cada sección lee la base de datos: se acepta el clic antes.
        await ack(interaction)
        await self.perfil.show(interaction, self.values[0])


class PerfilView(discord.ui.View):
    """Panel de `perfil`: una sección a la vista y un menú para cambiar.

    Solo quien lo abrió lo maneja. Los botones de 🏆 y 🎒 abren la vista
    completa de logros o la mochila en un mensaje que solo ve quien pulsa.
    """

    def __init__(
        self,
        cog: Perfil,
        *,
        guild: discord.Guild,
        owner: discord.abc.User,
        target: discord.abc.User,
        channel: object,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild = guild
        self.owner = owner
        self.target = target
        self.channel = channel
        self.key = "resumen"
        self.message: discord.Message | None = None
        self.rebuild()

    def rebuild(self) -> None:
        """Vuelve a poner el menú y los botones de la sección actual."""
        self.clear_items()
        self.add_item(SectionSelect(self))
        if self.key == "logros":
            self._button("🏆 Ver por categorías y ranking", self._open_logros, "perfil:logros")
        elif self.key == "objetos":
            mine = self.target.id == self.owner.id
            self._button(
                "🎒 Abrir la mochila" + ("" if mine else " (solo mirar)"),
                self._backpack,
                "perfil:mochila",
            )

    def _button(
        self,
        label: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        custom_id: str,
    ) -> None:
        button: discord.ui.Button = discord.ui.Button(
            label=label, style=discord.ButtonStyle.primary, row=1, custom_id=custom_id
        )
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Cada uno maneja su propio perfil."""
        if interaction.user.id == self.owner.id:
            return True
        await notify(
            interaction, "Este perfil lo maneja quien lo abrió. Abre el tuyo con `perfil`."
        )
        return False

    async def show(self, interaction: discord.Interaction, key: str) -> None:
        """Cambia a la sección `key` en el mismo mensaje (tras `ack`)."""
        self.key = key
        embed = await self.cog.section(self, key)
        self.rebuild()
        await edit(interaction, embed=embed, view=self)
        if self.target.id == self.owner.id:
            delta = StatDelta(add={f"{PERFIL_SEEN_PREFIX}{key}": 1})
            logros.note(self.cog.bot, self.guild.id, self.owner.id, delta)

    async def _open_logros(self, interaction: discord.Interaction) -> None:
        await ack(interaction, new_message=True)
        achievements = find_cog(self.cog.bot, Achievements)
        if achievements is None:
            await edit(interaction, content="Los logros no están disponibles ahora.")
            return
        view = await achievements.build_view(self.guild, self.owner.id, self.target)
        await edit(interaction, embed=view.page("summary"), view=view)

    async def _backpack(self, interaction: discord.Interaction) -> None:
        await ack(interaction, new_message=True)
        shop = find_cog(self.cog.bot, Tienda)
        if shop is None:
            await edit(interaction, content="La tienda no está disponible ahora.")
            return
        view = await shop.backpack_view(self.guild, self.target, self.owner)
        await edit(interaction, view=view, allowed_mentions=discord.AllowedMentions.none())
        view.interaction = interaction

    async def on_timeout(self) -> None:
        """Al caducar, el menú se queda gris para que no parezca que funciona."""
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                logger.debug("No se pudo cerrar un perfil caducado", exc_info=True)


class Perfil(commands.Cog):
    """`perfil`: nivel, patrimonio, trabajo, logros, rachas y objetos en un solo menú."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -- Secciones ----------------------------------------------------------------------

    async def section(self, view: PerfilView, key: str) -> discord.Embed:
        """Embed de una sección para el perfil de `view.target`."""
        guild, target = view.guild, view.target
        if key == "nivel":
            levels = find_cog(self.bot, MessageStats)
            if levels is None:
                return missing_embed("📊 Nivel")
            return await levels.level_embed(guild, target)
        if key == "patrimonio":
            wealth = find_cog(self.bot, Patrimonio)
            if wealth is None:
                return missing_embed("🏰 Patrimonio")
            return await wealth.patrimonio_embed(guild, view.owner, target, view.channel)
        if key == "trabajo":
            work = find_cog(self.bot, Work)
            if work is None:
                return missing_embed("🪏 Trabajo")
            return await work.work_embed(guild.id, target)
        if key == "logros":
            achievements = find_cog(self.bot, Achievements)
            if achievements is None:
                return missing_embed("🏆 Logros")
            achievements.note_view(guild.id, view.owner.id, target.id)
            return (await achievements.build_view(guild, view.owner.id, target)).page("summary")
        if key == "rachas":
            return streaks_embed(
                _name(target),
                await self._stats(guild.id, target.id),
                await self._writing_streak(guild.id, target.id),
            )
        if key == "objetos":
            shop = find_cog(self.bot, Tienda)
            if shop is None:
                return missing_embed("🎒 Objetos")
            return await shop.backpack_embed(guild, target)
        if key == "deseos":
            shop = find_cog(self.bot, Tienda)
            if shop is None:
                return missing_embed("⭐ Deseos")
            return await shop.wishlist_embed(guild, target)
        if key == "gallo":
            gallos = find_cog(self.bot, Gallos)
            if gallos is None:
                return missing_embed("🐓 Gallo")
            return await gallos.gallo_embed(guild.id, target)
        if key == "enraya":
            enraya = find_cog(self.bot, Enraya)
            if enraya is None:
                return missing_embed("🔴 4 en raya")
            return await enraya.enraya_embed(guild.id, target)
        if key == "backgammon":
            backgammon = find_cog(self.bot, Backgammon)
            if backgammon is None:
                return missing_embed("♟️ Backgammon")
            return await backgammon.backgammon_embed(guild.id, target)
        if key == "casa":
            house = find_cog(self.bot, Casa)
            if house is None:
                return missing_embed("🏡 Casa")
            return await house.home_embed(guild.id, target)
        return await self.summary(view)

    async def _stats(self, guild_id: int, user_id: int) -> dict[str, int]:
        achievements = find_cog(self.bot, Achievements)
        if achievements is None:
            return {}
        return dict((await achievements.fresh_profile(guild_id, user_id)).stats)

    async def _writing_streak(self, guild_id: int, user_id: int) -> int:
        levels = find_cog(self.bot, MessageStats)
        if levels is None:
            return 0
        activity = await levels.repository.member_activity(guild_id, user_id)
        return current_streak(activity, local_day(time.time()))

    async def summary(self, view: PerfilView) -> discord.Embed:
        """Sección 📋 Resumen: un renglón por sección, con lo que haya cargado."""
        guild, target = view.guild, view.target
        embed = discord.Embed(title=f"👤 Perfil de {_name(target)}", color=COLOR)
        embed.set_thumbnail(url=target.display_avatar.url)
        lines: list[str] = []
        if (title := tienda.title_of(self.bot, guild.id, target.id)) is not None:
            lines.append(f"### {title}")

        levels = find_cog(self.bot, MessageStats)
        if levels is not None:
            activity = await levels.repository.member_activity(guild.id, target.id)
            progress = calculate_level_progress(activity.total_xp)
            lines.append(
                f"📊 **Nivel {progress.level}** · {activity.total_xp:,} XP".replace(",", ".")
            )
        wealth = find_cog(self.bot, Patrimonio)
        if wealth is not None:
            worth, rank, people = await wealth.net_worth(guild.id, target.id)
            place = f" · puesto {rank} de {people}" if worth.total > 0 else ""
            lines.append(f"🏰 **{format_amount(worth.total)}** de patrimonio{place}")
        work = find_cog(self.bot, Work)
        if work is not None:
            lines.append(f"🪏 {await work.job_line(guild.id, target.id)}")
        achievements = find_cog(self.bot, Achievements)
        if achievements is not None:
            profile = await achievements.fresh_profile(guild.id, target.id)
            stats = profile.stats
            unlocked = [i for i in profile.unlocked if i in BY_ID]
            done = sum(1 for a in COUNTED if a.id in profile.unlocked)
            lines.append(
                f"🏆 **{done}/{len(COUNTED)}** logros · {points(unlocked):,} puntos".replace(
                    ",", "."
                )
                + f"\n`{progress_bar(done, len(COUNTED), 16)}`"
            )
            # El mejor récord en días: minutos o casillas no se comparan con días.
            best = max(
                (
                    (stats.get(stat, 0), emoji, label, unit)
                    for stat, emoji, label, unit in STREAKS
                    if unit == "días"
                ),
                default=(0, "", "", ""),
            )
            writing = await self._writing_streak(guild.id, target.id)
            streak_text = (
                f"🔥 {writing} días seguidos escribiendo" if writing else "🔥 Sin racha hoy"
            )
            if best[0] > 0:
                streak_text += f" · récord: {best[1]} {best[0]:,} {best[3]}".replace(",", ".")
            lines.append(streak_text)
        shop = find_cog(self.bot, Tienda)
        if shop is not None:
            count = await shop.item_count(guild.id, target.id)
            lines.append(f"🎒 {count} {'cosa' if count == 1 else 'cosas'} en la mochila")
        if patron := await donaciones.patron_line(self.bot, guild.id, target.id):
            lines.append(patron)
        embed.description = "\n".join(lines) or "Nada que enseñar todavía."
        # Habla la mascota de quien mira su propio perfil (la de otro no es asunto suyo).
        line = None
        if target.id == view.owner.id:
            line = await mascotas.cameo(self.bot, guild.id, target.id, Moment(Event.PROFILE))
        if line is not None:
            embed.add_field(name="\u200b", value=line, inline=False)
        embed.set_footer(text="Elige una sección en el menú para ver el detalle.")
        return embed

    # -- Comando ------------------------------------------------------------------------

    async def open(
        self,
        *,
        guild: discord.Guild | None,
        author: discord.abc.User,
        target: discord.abc.User | None,
        channel: object,
    ) -> tuple[discord.Embed, PerfilView] | str:
        """Prepara el panel, o devuelve el error que hay que enseñar."""
        if guild is None:
            return "El perfil solo se mira dentro de un servidor."
        who = target or author
        if who.bot:
            logros.note(self.bot, guild.id, author.id, StatDelta(add={"perfil_bot": 1}))
            return "Los bots no tienen perfil: no tienen ni nivel, ni curro, ni vergüenza."
        view = PerfilView(self, guild=guild, owner=author, target=who, channel=channel)
        embed = await self.summary(view)
        await logros.track(self.bot, guild.id, author, channel, perfil_stats(author, who))
        return embed, view

    @app_commands.command(
        name="perfil",
        description="Tu nivel, patrimonio, trabajo, logros, rachas y objetos en un menú.",
    )
    @app_commands.describe(miembro="De quién ver el perfil (por defecto, el tuyo)")
    @app_commands.guild_only()
    async def perfil(
        self, interaction: discord.Interaction, miembro: discord.Member | None = None
    ) -> None:
        """Abre el perfil con su menú de secciones. Público; solo lo maneja quien lo abre."""
        # El resumen lee varias tablas: «pensando…» mientras tanto.
        await interaction.response.defer(thinking=True)
        result = await self.open(
            guild=interaction.guild,
            author=interaction.user,
            target=miembro,
            channel=interaction.channel,
        )
        if isinstance(result, str):
            await edit(interaction, content=result)
            return
        embed, view = result
        await edit(interaction, embed=embed, view=view)
        view.message = await interaction.original_response()

    @commands.command(name="perfil")
    @commands.guild_only()
    async def perfil_text(
        self, ctx: commands.Context, miembro: discord.Member | None = None
    ) -> None:
        """Versión de texto: `.perfil` o `.perfil @alguien`."""
        result = await self.open(
            guild=ctx.guild, author=ctx.author, target=miembro, channel=ctx.channel
        )
        if isinstance(result, str):
            await ctx.send(result)
            return
        embed, view = result
        view.message = await ctx.send(
            embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none()
        )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog del perfil."""
    await bot.add_cog(Perfil(bot))
