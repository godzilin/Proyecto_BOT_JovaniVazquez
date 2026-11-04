"""Logros: el seguimiento de la actividad, los avisos al desbloquear y su vista.

Qué se cuenta y cómo:

- **Mensajes y reacciones** (`on_message`, `on_raw_reaction_add`): se suman en
  memoria y se escriben una vez por minuto en una sola transacción por
  servidor, para no añadir una escritura en disco por cada mensaje. Si el
  bot se cae, se pierde como mucho el último minuto. Del mensaje solo se
  miran unas pocas propiedades (largo, hora, si tiene enlace…); el texto
  nunca se guarda.
  Las ediciones (`on_message_edit`) cuentan si cambia el texto. El contexto
  de cada canal (monólogos, ecos, cadenas de risa…) lo lleva `ChatTracker`,
  que solo guarda un hash del último mensaje.
- **Voz** (`_tick`, cada minuto): lee la caché de estados de voz de
  discord.py, sin llamadas a la API. Un minuto cuenta si hay al menos dos
  personas sin ensordecer en el canal y no es el canal AFK (ese suma sus
  propios minutos de AFK). Entradas, cambios de canal y huidas rápidas
  salen de `on_voice_state_update`.
- **Música, imágenes, entradas y babel**: llaman a `note_for` al terminar.
- **Juegos y economía**: los juegos del casino (ruleta, blackjack,
  tragaperras, Botes, Crash, Minas, Pollo, pachinko y caballos), el IMV, la renta, los niveles y los
  cumpleaños llaman a `track`, `casino_play` o `note` de este
  módulo al terminar cada acción. Si el cog no está cargado no pasa nada, y
  un fallo aquí nunca rompe el juego que lo llama.

Desbloquear un logro paga yapdollars según su rareza. Es un premio de un
concurso del servidor: ganancia patrimonial (art. 33.1 LIRPF) sujeta a
retención como los premios (art. 75.2.c RIRPF), así que se cobra con
`EconomyService.pay_income`, que retiene IRPF y manda la retención al Estado.
Se anuncia en el canal donde se consiguió. No llega como interacción propia
(salta dentro de otra acción), así que el aviso de la Renta lo pone la
acción que lo provoca, como manda la Biblia.

Los logros de cada uno se consultan en la sección 🏆 Logros de `perfil`
(`build_view`, con el menú por categorías y el ranking).

Permisos: enviar mensajes e insertar enlaces en los canales donde se juega
y se habla (también en el chat de texto de los canales de voz).
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections import Counter, OrderedDict
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

from bot.cogs import pets as mascotas
from bot.repositories.achievements import AchievementRepository, Profile
from bot.services.achievements import (
    BY_ID,
    CATALOG,
    CATEGORY_BY_KEY,
    COUNTED,
    GROUPS,
    Achievement,
    Category,
    ChatTracker,
    Rarity,
    StatDelta,
    group_sections,
    is_laugh_emoji,
    is_night,
    laugh_reply_stats,
    menu_entries,
    message_delta,
    meta_stats,
    newly_unlocked,
    points,
    progress,
    reaction_stats,
    total_reward,
    voice_minute_stats,
    voice_move_stats,
    with_derived,
)
from bot.services.economy import (
    CURRENCY_EMOJI,
    BalanceLimitError,
    EconomyService,
    IncomeResult,
    format_amount,
    tax_tag,
)
from bot.services.levels import TIMEZONE, calculate_level_progress
from bot.services.pets import Event, Moment
from bot.services.robuso import ROBUSO_ID
from bot.services.robuso import echoes as robuso_echoes
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit

if TYPE_CHECKING:
    from bot.app import BotClient
    from bot.repositories.message_stats import MessageStatsRepository

logger = logging.getLogger(__name__)

#: Mensajes cuyos reactores se recuerdan, para que quitar y volver a poner
#: una reacción no cuente dos veces ni infle "Viral".
REACTION_MEMORY = 5_000
#: Derrotas seguidas en el casino para que reírse cuente como «Ríe por no llorar».
LOSING_STREAK = 5
#: Personas en un canal de voz para que quien lo abrió vacío cuente como anfitrión.
VOICE_FULL_ROOM = 5
#: Segundos que la vista de logros sigue respondiendo.
VIEW_TIMEOUT = 300
RANKING_SIZE = 10
COLOR = discord.Color.from_rgb(255, 190, 40)


# -- Formato --------------------------------------------------------------------------


def format_value(value: int, unit: str = "") -> str:
    """Cantidad legible: minutos como horas, dinero en Y$ y miles con punto."""
    if unit == "money":
        return format_amount(value)
    if unit == "min":
        if value < 60:
            return f"{value} min"
        if value < 600 and value % 60:
            return f"{value / 60:.1f} h".replace(".", ",")
        return f"{value // 60:,} h".replace(",", ".")
    return f"{value:,}".replace(",", ".")


def progress_bar(current: int, goal: int, width: int = 8) -> str:
    """Barra compacta de progreso: `▰▰▰▱▱▱▱▱`."""
    filled = 0 if goal <= 0 else min(width, current * width // goal)
    return "▰" * filled + "▱" * (width - filled)


def percent(holders: int, members: int) -> str:
    """Qué parte del servidor tiene un logro, con un decimal si es poco."""
    if members <= 0:
        return "0 %"
    value = holders * 100 / members
    if 0 < value < 10:
        return f"{value:.1f} %".replace(".", ",")
    return f"{value:.0f} %"


def with_unlocked_count(stats: Mapping[str, int], unlocked: Mapping[str, float]) -> dict[str, int]:
    """Estadísticas más las de Coleccionista (`meta_stats`), para enseñar su progreso."""
    full = dict(stats)
    full.update(meta_stats(unlocked))
    return full


def unlock_embeds(
    name: str,
    avatar_url: str | None,
    achievement_ids: Sequence[str],
    income: IncomeResult | None,
) -> list[discord.Embed]:
    """Aviso público de logros recién desbloqueados, con el premio cobrado.

    Compacto para no llenar el chat: el trofeo va junto al nombre y el premio, con
    la retención de Hacienda si la hay, en la línea del logro. Con un logro son
    tres líneas:

        Diego · 🏆 ¡Logro desbloqueado!
        ▫️ **Cinco veces** · Raro · 🪙 **+162 Y$** · 🐶 −38 Y$ (19,00 %)
        -# Cobra en ×5 o más.

    Lista todos los logros; si no caben en un embed, siguen en los siguientes.
    """
    achievements = [BY_ID[i] for i in achievement_ids if i in BY_ID]
    best = max(achievements, key=lambda a: list(Rarity).index(a.rarity))
    headline = (
        "🏆 ¡Logro desbloqueado!"
        if len(achievements) == 1
        else f"🏆 ¡{len(achievements)} logros desbloqueados!"
    )
    money = ""
    if income is not None:
        tax = tax_tag(income.tax, income.rate)
        money = f"{CURRENCY_EMOJI} **+{format_amount(income.net)}**" + (f" · {tax}" if tax else "")
    if len(achievements) == 1:
        a = achievements[0]
        head = f"{a.rarity.emoji} **{a.name}** · {a.rarity.label}"
        lines = [
            f"{head} · {money}\n-# {a.description}" if money else f"{head}\n-# {a.description}"
        ]
    else:
        lines = [
            f"{a.rarity.emoji} **{a.name}** · {a.rarity.label}\n-# {a.description}"
            for a in achievements
        ]
        if money:
            lines.append(money)
    lines += [f"\n{a.story}" for a in achievements if a.story]
    color = discord.Color.from_rgb(*best.rarity.rgb)
    # Páginas de 3.800 caracteres: el último embed deja sitio a las skins y la mascota.
    embeds = [discord.Embed(description="\n".join(page), color=color) for page in paginate(lines)]
    embeds[0].set_author(name=f"{name} · {headline}", icon_url=avatar_url)
    return embeds


def _achievement_line(
    achievement: Achievement,
    stats: Mapping[str, int],
    unlocked: Mapping[str, float],
    holders: Mapping[str, int],
    members: int,
) -> str:
    rarity = achievement.rarity
    share = percent(holders.get(achievement.id, 0), members)
    if achievement.id in unlocked:
        return f"✅ {rarity.emoji} **{achievement.name}** · {share}\n-# {achievement.description}"
    if achievement.upcoming:
        return f"⏳ {rarity.emoji} **{achievement.name}**\n-# {achievement.description}"
    if achievement.secret:
        return f"🔒 {rarity.emoji} **???** · secreto · {share}"
    current, goal = progress(achievement, stats, derived=True)
    return (
        f"⬛ {rarity.emoji} **{achievement.name}** · `{progress_bar(current, goal)}` "
        f"{format_value(current, achievement.unit)}/{format_value(goal, achievement.unit)}\n"
        f"-# {achievement.description}"
    )


#: Caracteres de logros por página de una categoría. Un embed admite 4.096 en
#: la descripción; el resto queda para la cabecera.
CATEGORY_PAGE_CHARS = 3_800


def _category_lines(
    category: Category,
    profile: Profile,
    holders: Mapping[str, int],
    members: int,
) -> list[str]:
    # Una vez por página, no por logro (`progress(..., derived=True)`).
    stats = with_derived(with_unlocked_count(profile.stats, profile.unlocked))
    items = [a for a in CATALOG if a.category == category.key]
    return [_achievement_line(a, stats, profile.unlocked, holders, members) for a in items]


def paginate(lines: Sequence[str], limit: int = CATEGORY_PAGE_CHARS) -> list[list[str]]:
    """Reparte las líneas en páginas de `limit` caracteres como mucho (siempre una al menos)."""
    pages: list[list[str]] = [[]]
    size = 0
    for line in lines:
        if pages[-1] and size + len(line) + 1 > limit:
            pages.append([])
            size = 0
        pages[-1].append(line)
        size += len(line) + 1
    return pages


def category_page_count(
    category: Category, profile: Profile, holders: Mapping[str, int], members: int
) -> int:
    """Páginas que ocupa una categoría."""
    return len(paginate(_category_lines(category, profile, holders, members)))


def category_embed(
    category: Category,
    name: str,
    profile: Profile,
    holders: Mapping[str, int],
    members: int,
    page: int = 0,
) -> discord.Embed:
    """Página de una categoría: cada logro con su estado o su progreso.

    Las categorías grandes se reparten en varias páginas (`page`, desde 0).
    """
    items = [a for a in CATALOG if a.category == category.key]
    done = sum(1 for a in items if a.id in profile.unlocked)
    header = (
        "Llegan con la tragaperras. Todavía no se pueden conseguir."
        if category.upcoming
        else f"**{done}/{len(items)}** conseguidos"
    )
    pages = paginate(_category_lines(category, profile, holders, members))
    page = max(0, min(page, len(pages) - 1))
    embed = discord.Embed(
        title=f"{category.title} · {name}",
        description=header + "\n\n" + "\n".join(pages[page]),
        color=COLOR,
    )
    legend = "▫️ Común · 🔹 Raro · 💠 Épico · 🌟 Legendario · 👑 Mítico"
    if len(pages) > 1:
        legend = f"Página {page + 1}/{len(pages)} · {legend}"
    embed.set_footer(text=legend)
    return embed


def group_embed(
    group_key: str,
    name: str,
    profile: Profile,
) -> discord.Embed:
    """Portada de un grupo (🎰 Casino): progreso de cada sección y el total.

    Los logros de cada juego se ven eligiendo la sección en el segundo menú.
    """
    group = GROUPS[group_key]
    sections = group_sections(group_key)
    lines = []
    total = done = 0
    for section in sections:
        items = [a for a in CATALOG if a.category == section.key]
        got = sum(1 for a in items if a.id in profile.unlocked)
        total += len(items)
        done += got
        lines.append(f"{section.title} · `{progress_bar(got, len(items))}` {got}/{len(items)}")
    embed = discord.Embed(
        title=f"{group.title} · {name}",
        description=(
            f"**{done}/{total}** conseguidos\n\n"
            + "\n".join(lines)
            + "\n\n-# Elige un juego en el segundo menú para ver sus logros."
        ),
        color=COLOR,
    )
    return embed


def summary_embed(
    name: str,
    avatar_url: str | None,
    profile: Profile,
    holders: Mapping[str, int],
    members: int,
) -> discord.Embed:
    """Portada de los logros (🏆 en `perfil`): total, puntos, categorías y los últimos."""
    unlocked = {i: t for i, t in profile.unlocked.items() if i in BY_ID}
    # Las categorías secretas van aparte del total (`Category.secret`).
    available_ids = {a.id for a in COUNTED}
    done = sum(1 for i in unlocked if i in available_ids)
    total = len(COUNTED)
    embed = discord.Embed(
        title="🏆 Logros",
        description=(
            f"**{done}/{total}** logros · **{points(unlocked):,}** puntos\n".replace(",", ".")
            + f"`{progress_bar(done, total, 16)}` {done * 100 // total} %"
        ),
        color=COLOR,
    )
    embed.set_author(name=name, icon_url=avatar_url)

    per_category = []
    for key, title in menu_entries(unlocked):
        if key in GROUPS:
            keys = {c.key for c in group_sections(key) if not c.upcoming}
        elif CATEGORY_BY_KEY[key].upcoming:
            per_category.append(f"{title} · próximamente")
            continue
        else:
            keys = {key}
        items = [a for a in CATALOG if a.category in keys]
        got = sum(1 for a in items if a.id in unlocked)
        per_category.append(f"{title} · {got}/{len(items)}")
    embed.add_field(name="Categorías", value="\n".join(per_category), inline=False)

    recent = sorted(unlocked.items(), key=lambda item: item[1], reverse=True)[:5]
    if recent:
        embed.add_field(
            name="Últimos",
            value="\n".join(
                f"{BY_ID[i].rarity.emoji} {BY_ID[i].name} · <t:{int(t)}:R>" for i, t in recent
            ),
            inline=False,
        )

    # Una vez para todos: con `with_derived` en cada logro, el resumen paraba el bucle
    # varios segundos (informe del 04/11/2026, «Bloqueos del bucle»).
    stats = with_derived(with_unlocked_count(profile.stats, unlocked))
    candidates = []
    for achievement in COUNTED:
        if achievement.id in unlocked or achievement.secret:
            continue
        current, goal = progress(achievement, stats, derived=True)
        if 0 < current < goal:
            candidates.append((current / goal, achievement, current))
    candidates.sort(key=lambda item: item[0], reverse=True)
    if candidates:
        embed.add_field(
            name="Casi lo tienes",
            value="\n".join(
                f"{a.rarity.emoji} {a.name} · {format_value(cur, a.unit)}/"
                f"{format_value(a.goal, a.unit)}"
                for _ratio, a, cur in candidates[:3]
            ),
            inline=False,
        )

    if unlocked and members:
        rarest = min(unlocked, key=lambda i: (holders.get(i, 0), -BY_ID[i].rarity.points))
        embed.add_field(
            name="El más raro",
            value=(
                f"{BY_ID[rarest].rarity.emoji} {BY_ID[rarest].name} · lo tiene el "
                f"{percent(holders.get(rarest, 0), members)} del servidor"
            ),
            inline=False,
        )
    embed.set_footer(text="Elige una categoría en el menú para ver cada logro.")
    return embed


def ranking_embed(entries: Sequence[tuple[str, int, int]]) -> discord.Embed:
    """Ranking por puntos: `(nombre, puntos, logros)` de mayor a menor."""
    medals = ("🥇", "🥈", "🥉")
    lines = [
        f"{medals[i] if i < 3 else f'**{i + 1}.**'} {name} · "
        f"{format_value(pts)} puntos · {count} logros"
        for i, (name, pts, count) in enumerate(entries)
    ]
    return discord.Embed(
        title="🏆 Ranking de logros",
        description="\n".join(lines) or "Nadie tiene logros todavía.",
        color=COLOR,
    )


def _with_echoes(user_id: int, delta: StatDelta) -> StatDelta:
    """Lo de Robuso suma además su eco en 🎸 Robuso (`bot.services.robuso.ECHOES`)."""
    extra = robuso_echoes(delta.add) if user_id == ROBUSO_ID else {}
    if not extra:
        return delta
    return StatDelta(add={**delta.add, **extra}, peak=dict(delta.peak))


# -- Vista de logros -------------------------------------------------------------------


class CategorySelect(discord.ui.Select):
    """Menú para saltar entre el resumen y las categorías."""

    def __init__(self, view: AchievementsView) -> None:
        options = [discord.SelectOption(label="Resumen", value="summary", emoji="🏆")]
        for key, full_title in menu_entries(view.profile.unlocked):
            emoji, _, title = full_title.partition(" ")
            options.append(discord.SelectOption(label=title, value=key, emoji=emoji))
        super().__init__(
            placeholder="Elige una categoría",
            options=options,
            row=0,
            custom_id="logros:categoria",
        )
        self.achievements_view = view

    async def callback(self, interaction: discord.Interaction) -> None:
        """Cambia de página."""
        await self.achievements_view.show(interaction, self.values[0])


#: Secciones que caben en un desplegable (Discord admite 25 opciones; la primera es la portada).
SECTIONS_PER_MENU = 24


class SectionSelect(discord.ui.Select):
    """Segundo menú dentro de un grupo (💬 Chat, 🎙️ Voz, 🎰 Casino): una sección por opción.

    Un grupo con más de `SECTIONS_PER_MENU` secciones (el casino, desde el pico) se parte
    en dos desplegables: el primero con la portada y las 24 primeras, el segundo
    (`part=1`, en la fila de abajo) con el resto.
    """

    def __init__(self, view: AchievementsView, group_key: str, current: str, part: int = 0) -> None:
        options = []
        if part == 0:
            options.append(
                discord.SelectOption(
                    label="Todo el casino" if group_key == "casino_group" else "Portada",
                    value=group_key,
                    emoji="📊",
                    default=current == group_key,
                )
            )
        sections = group_sections(group_key)
        start = part * SECTIONS_PER_MENU
        for section in sections[start : start + SECTIONS_PER_MENU]:
            emoji, _, title = section.title.partition(" ")
            options.append(
                discord.SelectOption(
                    label=title, value=section.key, emoji=emoji, default=current == section.key
                )
            )
        placeholder = "Elige un juego" if group_key == "casino_group" else "Elige una sección"
        if part:
            placeholder = "Más juegos" if group_key == "casino_group" else "Más secciones"
        super().__init__(
            placeholder=placeholder,
            options=options,
            row=1 if part == 0 else 3,
            custom_id="logros:seccion" if part == 0 else f"logros:seccion{part + 1}",
        )
        self.achievements_view = view

    async def callback(self, interaction: discord.Interaction) -> None:
        """Cambia de sección."""
        await self.achievements_view.show(interaction, self.values[0])


class AchievementsView(discord.ui.View):
    """Páginas de logros de un miembro (botón 🏆 de `perfil`). Solo quien lo pidió las cambia."""

    def __init__(
        self,
        cog: Achievements,
        *,
        guild: discord.Guild,
        owner_id: int,
        target: discord.abc.User,
        profile: Profile,
        holders: Mapping[str, int],
        members: int,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild = guild
        self.owner_id = owner_id
        self.target = target
        self.profile = profile
        self.holders = holders
        self.members = members
        #: Página que se enseña y, si es una categoría larga, en qué hoja va.
        self.key = "summary"
        self.sheet = 0
        self.add_item(CategorySelect(self))
        self._refresh_pager()

    def page(self, key: str) -> discord.Embed:
        """Embed de la página `key` (`summary`, un grupo o una categoría)."""
        name = self.target.display_name
        if key in GROUPS:
            return group_embed(key, name, self.profile)
        if key in CATEGORY_BY_KEY:
            return category_embed(
                CATEGORY_BY_KEY[key],
                name,
                self.profile,
                self.holders,
                self.members,
                self.sheet if key == self.key else 0,
            )
        return summary_embed(
            name, self.target.display_avatar.url, self.profile, self.holders, self.members
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Cada uno navega por sus propios logros."""
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Abre los tuyos en `perfil` (o `perfil @alguien`), sección 🏆 Logros.",
            ephemeral=True,
        )
        return False

    def set_sections(self, key: str) -> None:
        """Pone o quita el segundo menú según si la página es de un grupo."""
        for item in list(self.children):
            if isinstance(item, SectionSelect):
                self.remove_item(item)
        group = key if key in GROUPS else None
        if group is None and key in CATEGORY_BY_KEY:
            group = CATEGORY_BY_KEY[key].group
        if group is not None:
            menus = -(-len(group_sections(group)) // SECTIONS_PER_MENU)
            for part in range(menus):
                self.add_item(SectionSelect(self, group, key, part))

    def _sheets(self) -> int:
        category = CATEGORY_BY_KEY.get(self.key)
        if category is None:
            return 1
        return category_page_count(category, self.profile, self.holders, self.members)

    def _refresh_pager(self) -> None:
        """Activa ◀ y ▶ solo si la categoría tiene más de una página."""
        sheets = self._sheets()
        self.previous_sheet.disabled = self.sheet <= 0
        self.next_sheet.disabled = self.sheet >= sheets - 1

    async def show(self, interaction: discord.Interaction, key: str) -> None:
        """Enseña la página elegida (desde su primera hoja)."""
        self.key = key
        self.sheet = 0
        self.set_sections(key)
        self._refresh_pager()
        await interaction.response.edit_message(embed=self.page(key), view=self)

    async def _turn(self, interaction: discord.Interaction, step: int) -> None:
        self.sheet = max(0, min(self.sheet + step, self._sheets() - 1))
        self._refresh_pager()
        await interaction.response.edit_message(embed=self.page(self.key), view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, row=2)
    async def previous_sheet(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        """Hoja anterior de una categoría larga."""
        await self._turn(interaction, -1)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=2)
    async def next_sheet(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        """Hoja siguiente de una categoría larga."""
        await self._turn(interaction, 1)

    @discord.ui.button(label="🏆 Ranking", style=discord.ButtonStyle.primary, row=2)
    async def ranking(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        """Ranking del servidor por puntos de logros."""
        # El ranking se calcula con los logros de todo el servidor: se acepta el clic antes.
        await ack(interaction)
        self.cog.note(self.guild.id, interaction.user.id, StatDelta(add={"logros_ranking": 1}))
        self.key, self.sheet = "ranking", 0
        self._refresh_pager()
        await edit(interaction, embed=await self.cog.ranking(self.guild), view=self)


# -- Cog ------------------------------------------------------------------------------


class Achievements(commands.Cog):
    """Cuenta la actividad, desbloquea logros, los paga y los enseña."""

    def __init__(
        self,
        bot: commands.Bot,
        repository: AchievementRepository,
        *,
        economy: EconomyService | None = None,
        message_stats: MessageStatsRepository | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.bot = bot
        self.repository = repository
        self.economy = economy
        self.message_stats = message_stats
        self._clock = clock
        #: Cambios pendientes de escribir, por servidor y miembro.
        self._pending: dict[int, dict[int, StatDelta]] = {}
        #: Canal donde avisar a cada miembro con cambios pendientes.
        self._pending_channels: dict[int, dict[int, int]] = {}
        #: Minutos seguidos en llamada, por (servidor, miembro).
        self._sessions: dict[tuple[int, int], int] = {}
        #: Quién ha reaccionado ya a cada mensaje (los últimos `REACTION_MEMORY`).
        self._reactors: OrderedDict[int, set[int]] = OrderedDict()
        #: Racha de casino en curso: positiva si gana, negativa si pierde.
        self._casino_streaks: dict[tuple[int, int], int] = {}
        #: Miembros a los que ya se ha cargado el historial anterior a los logros.
        self._seeded: set[tuple[int, int]] = set()
        #: Contexto de cada canal para monólogos, ecos, cadenas de risa…
        self._chat = ChatTracker()
        #: Quién se ha reído (con reacción) de cada mensaje (los últimos `REACTION_MEMORY`).
        self._laugh_reactors: OrderedDict[int, set[int]] = OrderedDict()
        #: Minutos seguidos silenciado en llamada, por (servidor, miembro).
        self._mute_streaks: dict[tuple[int, int], int] = {}
        #: Cuándo entró cada miembro a voz, para pillar a los que entran y salen.
        self._voice_joined: dict[tuple[int, int], float] = {}
        #: Emojis que ya ha puesto cada persona en cada mensaje (para no contarlos dos veces).
        self._emoji_reactors: OrderedDict[int, set[int]] = OrderedDict()
        #: Quién abrió cada canal de voz vacío y si ya se le ha contado la sala llena.
        self._voice_openers: dict[int, tuple[int, bool]] = {}

    async def cog_load(self) -> None:
        """Arranca la tarea de cada minuto (voz y escritura de lo pendiente)."""
        self._tick.start()

    async def cog_unload(self) -> None:
        """Para la tarea y guarda lo que quedara pendiente."""
        self._tick.cancel()
        await self.flush()

    # -- Entrada de datos -----------------------------------------------------------

    def note(
        self,
        guild_id: int,
        user_id: int,
        delta: StatDelta,
        channel_id: int | None = None,
        *,
        replace_channel: bool = True,
    ) -> None:
        """Apunta cambios para la próxima escritura (como mucho, en un minuto).

        Args:
            channel_id: Dónde avisar si sale algún logro.
            replace_channel: Si sustituye al canal ya apuntado (los mensajes
                sí; la voz solo si no había otro).
        """
        pending = self._pending.setdefault(guild_id, {})
        pending.setdefault(user_id, StatDelta()).merge(_with_echoes(user_id, delta))
        if channel_id is not None:
            channels = self._pending_channels.setdefault(guild_id, {})
            if replace_channel or user_id not in channels:
                channels[user_id] = channel_id

    async def apply(
        self,
        guild_id: int,
        user_id: int,
        delta: StatDelta,
        channel: discord.abc.Messageable | None,
    ) -> list[str]:
        """Escribe ya los cambios de un miembro y avisa de lo desbloqueado.

        Returns:
            Ids de los logros recién conseguidos.
        """
        now = self._clock()
        updates = {user_id: _with_echoes(user_id, delta)}
        await self._add_seeds(guild_id, updates)
        unlocked = await self.repository.record(guild_id, updates, newly_unlocked, now=now)
        ids = unlocked.get(user_id, [])
        if ids:
            await self._reward_and_announce(guild_id, user_id, ids, channel)
        return ids

    async def casino_play(
        self,
        guild_id: int,
        user_id: int,
        channel: discord.abc.Messageable | None,
        delta: StatDelta,
        *,
        net: int,
    ) -> list[str]:
        """Registra una jugada de casino y actualiza las rachas entre juegos.

        Las rachas viven en memoria: un reinicio del bot las corta.
        """
        key = (guild_id, user_id)
        streak = self._casino_streaks.get(key, 0)
        if net > 0:
            streak = streak + 1 if streak > 0 else 1
            delta.peak["casino_win_streak_max"] = streak
        elif net < 0:
            streak = streak - 1 if streak < 0 else -1
            delta.peak["casino_loss_streak_max"] = -streak
        self._casino_streaks[key] = streak
        return await self.apply(guild_id, user_id, delta, channel)

    async def _add_seeds(self, guild_id: int, updates: dict[int, StatDelta]) -> None:
        """La primera vez que se ve a alguien, recupera lo que ya tenía.

        Los mensajes del historial importado y el nivel actual entran como
        máximos (`peak`), así que repetirlo tras un reinicio no suma de más.
        El recuento de `message_stats` es solo el historial importado: los
        mensajes nuevos los cuentan los logros.
        """
        if self.message_stats is None:
            return
        for user_id, delta in updates.items():
            key = (guild_id, user_id)
            if key in self._seeded:
                continue
            try:
                imported = await self.message_stats.message_count(guild_id, user_id)
                xp = await self.message_stats.member_xp(guild_id, user_id)
            except (OSError, sqlite3.Error):
                logger.exception("No se pudo leer el historial de %s para los logros", user_id)
                continue
            self._seeded.add(key)
            delta.peak["messages_imported"] = max(delta.peak.get("messages_imported", 0), imported)
            level = calculate_level_progress(xp).level
            delta.peak["level_max"] = max(delta.peak.get("level_max", 0), level)

    # -- Escritura periódica --------------------------------------------------------

    @tasks.loop(seconds=60)
    async def _tick(self) -> None:
        """Cada minuto: suma la voz y escribe todo lo pendiente."""
        try:
            self.collect_voice(self._clock())
        except Exception:
            # Una excepción sin capturar pararía la tarea para siempre.
            logger.exception("Error contando la voz para los logros")
        await self.flush()

    @_tick.before_loop
    async def _before_tick(self) -> None:
        await self.bot.wait_until_ready()

    def collect_voice(self, now: float) -> None:
        """Suma un minuto de voz a quien esté en llamada con alguien más.

        También cuenta el canal AFK, a quien está ensordecido, la música del
        bot y las rachas de silencio. Todo sale de la caché de discord.py.
        """
        local = datetime.fromtimestamp(now, TIMEZONE)
        hour = local.hour
        weekend = local.weekday() >= 5
        new_year = local.month == 1 and local.day == 1 and hour == 0
        christmas = local.month == 12 and local.day in (24, 25)
        seen: set[tuple[int, int]] = set()
        muted_now: set[tuple[int, int]] = set()
        for guild in self.bot.guilds:
            afk_id = guild.afk_channel.id if guild.afk_channel is not None else None
            music = self._music_channel_id(guild)
            for channel in guild.voice_channels:
                humans = [m for m in channel.members if not m.bot and m.voice is not None]
                if channel.id == afk_id:
                    for member in humans:
                        self.note(
                            guild.id,
                            member.id,
                            StatDelta(add={"voice_afk": 1}),
                            replace_channel=False,
                        )
                    continue
                listening = [m for m in humans if not (m.voice.self_deaf or m.voice.deaf)]
                if listening and len(humans) >= 2:
                    for member in humans:
                        if member not in listening:
                            self.note(
                                guild.id,
                                member.id,
                                StatDelta(add={"voice_deaf": 1}),
                                channel.id,
                                replace_channel=False,
                            )
                if len(listening) == 1:
                    (alone,) = listening
                    self.note(
                        guild.id,
                        alone.id,
                        StatDelta(add={"voice_alone": 1}),
                        channel.id,
                        replace_channel=False,
                    )
                    continue
                if len(listening) < 2:
                    continue
                for member in listening:
                    key = (guild.id, member.id)
                    seen.add(key)
                    session = self._sessions.get(key, 0) + 1
                    self._sessions[key] = session
                    voice = member.voice
                    assert voice is not None  # filtrado arriba
                    add = {"voice_minutes": 1}
                    peak = {"voice_session_max": session, "voice_crowd_max": len(listening)}
                    if voice.self_mute or voice.mute:
                        add["voice_muted"] = 1
                        muted_now.add(key)
                        streak = self._mute_streaks.get(key, 0) + 1
                        self._mute_streaks[key] = streak
                        peak["voice_mute_streak_max"] = streak
                    if voice.mute:
                        add["voice_server_muted"] = 1
                    if voice.self_stream:
                        add["voice_stream"] = 1
                        if len(listening) >= 5:
                            add["voice_stream_crowd"] = 1
                    if voice.self_video:
                        add["voice_video"] = 1
                        if voice.self_stream:
                            add["voice_multitask"] = 1
                    if is_night(hour):
                        add["voice_night"] = 1
                    if 6 <= hour < 8:
                        add["voice_morning"] = 1
                    if 15 <= hour < 17:
                        add["voice_siesta"] = 1
                    if weekend:
                        add["voice_weekend"] = 1
                    if new_year:
                        add["voice_new_year"] = 1
                    if christmas:
                        add["voice_christmas"] = 1
                    if len(listening) == 2:
                        add["voice_duo"] = 1
                    if channel.id == music:
                        add["voice_music"] = 1
                    games = [
                        a.name
                        for a in getattr(member, "activities", ())
                        if a.type is discord.ActivityType.playing and a.name
                    ]
                    spotify = any(
                        isinstance(a, discord.Spotify) for a in getattr(member, "activities", ())
                    )
                    add.update(
                        voice_minute_stats(
                            local,
                            others=(m.id for m in listening if m.id != member.id),
                            games=games,
                            spotify=spotify,
                        )
                    )
                    self.note(
                        guild.id,
                        member.id,
                        StatDelta(add=add, peak=peak),
                        channel.id,
                        replace_channel=False,
                    )
        # Quien ya no está en llamada (o se quedó solo) empieza sesión nueva.
        for key in list(self._sessions):
            if key not in seen:
                del self._sessions[key]
        for key in list(self._mute_streaks):
            if key not in muted_now:
                del self._mute_streaks[key]

    @staticmethod
    def _music_channel_id(guild: discord.Guild) -> int | None:
        """Canal de voz donde el bot está poniendo música ahora mismo, si lo hay."""
        voice = getattr(guild, "voice_client", None)
        if not isinstance(voice, discord.VoiceClient) or not voice.is_playing():
            return None
        channel = voice.channel
        return channel.id if channel is not None else None

    async def flush(self) -> None:
        """Escribe lo pendiente, una transacción por servidor, y avisa."""
        pending, self._pending = self._pending, {}
        channels, self._pending_channels = self._pending_channels, {}
        now = self._clock()
        for guild_id, updates in pending.items():
            try:
                await self._add_seeds(guild_id, updates)
                unlocked = await self.repository.record(guild_id, updates, newly_unlocked, now=now)
            except (OSError, sqlite3.Error):
                logger.exception("No se pudieron guardar los logros del servidor %s", guild_id)
                continue
            guild = self.bot.get_guild(guild_id)
            for user_id, ids in unlocked.items():
                channel_id = channels.get(guild_id, {}).get(user_id)
                channel = (
                    guild.get_channel_or_thread(channel_id)
                    if guild is not None and channel_id is not None
                    else None
                )
                if channel is None and guild is not None:
                    channel = guild.system_channel
                try:
                    await self._reward_and_announce(
                        guild_id,
                        user_id,
                        ids,
                        channel if isinstance(channel, discord.abc.Messageable) else None,
                    )
                except Exception:
                    logger.exception("No se pudo avisar de un logro a %s", user_id)

    # -- Premio y aviso -------------------------------------------------------------

    async def _reward_and_announce(
        self,
        guild_id: int,
        user_id: int,
        ids: list[str],
        channel: discord.abc.Messageable | None,
    ) -> None:
        """Paga los logros (con IRPF), da sus skins y los anuncia en `channel`."""
        from bot.cogs import shop as tienda  # evita el import circular

        income = await self._pay(guild_id, user_id, ids)
        skins = await tienda.grant_reward_skins(self.bot, guild_id, user_id, ids)
        if channel is None:
            return
        guild = self.bot.get_guild(guild_id)
        member = guild.get_member(user_id) if guild is not None else None
        name = member.display_name if member is not None else f"Miembro {user_id}"
        avatar = member.display_avatar.url if member is not None else None
        embeds = unlock_embeds(name, avatar, ids, income)
        embed = embeds[-1]
        if skins:
            gifts = ", ".join(f"**{skin.label}**" for skin in skins)
            embed.description = f"{embed.description}\n🎨 De regalo, en la mochila: {gifts}."
        if pet := await mascotas.cameo(self.bot, guild_id, user_id, Moment(Event.ACHIEVEMENT)):
            embed.description = f"{embed.description}\n{pet}"
        try:
            # Uno por mensaje: Discord no deja más de 6.000 caracteres entre todos sus embeds.
            for embed in embeds:
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("No se pudo anunciar un logro en el servidor %s", guild_id)

    async def _pay(self, guild_id: int, user_id: int, ids: list[str]) -> IncomeResult | None:
        """Cobra el premio de los logros como renta con retención de IRPF.

        Ver la docstring del módulo para el tratamiento fiscal. La retención
        cuenta para los logros de Hacienda en la siguiente escritura.
        """
        gross = total_reward(ids)
        if self.economy is None or gross <= 0:
            return None
        concept = f"logro:{ids[0]}" if len(ids) == 1 else "logro:varios"
        try:
            income = await self.economy.pay_income(guild_id, user_id, gross=gross, concept=concept)
        except (OSError, sqlite3.Error, BalanceLimitError):
            logger.exception("No se pudo pagar el premio de logros a %s", user_id)
            return None
        self.note(
            guild_id,
            user_id,
            StatDelta(add={"tax_paid": income.tax}, peak={"balance_max": income.balance}),
        )
        return income

    # -- Eventos de Discord ---------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Cuenta el mensaje para los logros de chat. No guarda el contenido."""
        if (
            message.guild is None
            or message.author.bot
            or message.webhook_id is not None
            or message.is_system()
        ):
            return
        guild_id = message.guild.id
        author = message.author
        me = self.bot.user
        birthdays = self.bot.get_cog("Birthdays")
        own_birthday = bool(
            birthdays is not None
            and hasattr(birthdays, "is_birthday_today")
            and birthdays.is_birthday_today(guild_id, author.id)
        )
        local = message.created_at.astimezone(TIMEZONE)
        is_reply = message.reference is not None and message.type is discord.MessageType.reply
        people = {u.id for u in message.mentions if u.id != author.id and not u.bot}
        delta = message_delta(
            message.content,
            when=local,
            attachments=len(message.attachments),
            stickers=len(message.stickers),
            is_reply=is_reply,
            mentions_others=bool(people),
            mentions_bot=me is not None and any(u.id == me.id for u in message.mentions),
            own_birthday=own_birthday,
            mention_everyone=message.mention_everyone,
            people_mentioned=len(people),
            files=[(a.content_type or "", a.filename, a.is_spoiler()) for a in message.attachments],
            voice_note=message.flags.voice,
            poll=getattr(message, "poll", None) is not None,
            voice=self._voice_context(author, message.channel.id),
        )
        laughed = bool(delta.add.get("msg_laughs"))
        # Reírse con una racha de derrotas en el casino: ríe por no llorar.
        if laughed and self._casino_streaks.get((guild_id, author.id), 0) <= -LOSING_STREAK:
            delta.add["laugh_losing"] = 1
        self.note(guild_id, author.id, delta, message.channel.id)

        others: dict[int, StatDelta] = {}
        if laughed and is_reply:
            replied = message.reference.resolved if message.reference else None
            # Si el mensaje respondido se borró (o no está en caché) no hay autor.
            replied_author = getattr(replied, "author", None)
            others = laugh_reply_stats(
                author_id=author.id,
                replied_author_id=replied_author.id if replied_author is not None else None,
                replied_is_bot=bool(replied_author is not None and replied_author.bot),
            )
        context = self._chat.observe(
            guild_id,
            message.channel.id,
            author.id,
            at=message.created_at.timestamp(),
            local=local,
            content=message.content,
            laughed=laughed,
        )
        for changes in (others, context):
            for user_id, extra in changes.items():
                # Al gracioso o a los de la cadena se les avisa en el mismo canal.
                self.note(guild_id, user_id, extra, message.channel.id, replace_channel=False)

    @staticmethod
    def _voice_context(author: object, channel_id: int) -> tuple[bool, bool, bool] | None:
        """`(en el chat del canal de voz, compartiendo pantalla, con cámara)` si está en voz."""
        voice = getattr(author, "voice", None)
        if voice is None or voice.channel is None:
            return None
        return voice.channel.id == channel_id, bool(voice.self_stream), bool(voice.self_video)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        """Cuenta las ediciones de verdad (cambia el texto, no solo un embed)."""
        if (
            after.guild is None
            or after.author.bot
            or after.webhook_id is not None
            or before.content == after.content
        ):
            return
        self.note(after.guild.id, after.author.id, StatDelta(add={"msg_edits": 1}))

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """Cuenta reacciones dadas y recibidas (una por persona y mensaje)."""
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
        author = guild.get_member(author_id) if guild is not None else None
        if author is None or author.bot:
            return
        emoji = getattr(payload, "emoji", None)
        if emoji is not None and is_laugh_emoji(emoji.name or str(emoji)):
            self._note_laugh_reaction(payload, author_id)
        reactors = self._remember(self._reactors, payload.message_id)
        seen = self._remember(self._emoji_reactors, payload.message_id)
        if emoji is not None and hash((payload.user_id, str(emoji))) not in seen:
            seen.add(hash((payload.user_id, str(emoji))))
            custom = emoji.id is not None and guild is not None and guild.get_emoji(emoji.id)
            given, received = reaction_stats(
                str(emoji) if emoji.id is None else (emoji.name or ""),
                custom=bool(custom),
                first=not reactors,
            )
            self.note(payload.guild_id, payload.user_id, given)
            if received.add:
                self.note(
                    payload.guild_id, author_id, received, payload.channel_id, replace_channel=False
                )
        if payload.user_id in reactors:
            return
        reactors.add(payload.user_id)
        self.note(payload.guild_id, payload.user_id, StatDelta(add={"reactions_given": 1}))
        self.note(
            payload.guild_id,
            author_id,
            StatDelta(
                add={"reactions_received": 1},
                peak={"reactions_on_message_max": len(reactors)},
            ),
            payload.channel_id,
            replace_channel=False,
        )

    @staticmethod
    def _remember(memory: OrderedDict[int, set[int]], message_id: int) -> set[int]:
        """Conjunto de quién ha reaccionado a un mensaje, olvidando los más viejos."""
        reactors = memory.get(message_id)
        if reactors is None:
            reactors = memory[message_id] = set()
            if len(memory) > REACTION_MEMORY:
                memory.popitem(last=False)
        return reactors

    def _note_laugh_reaction(self, payload: discord.RawReactionActionEvent, author_id: int) -> None:
        """Reacción de risa (😂, 💀, `:kekw:`…): una por persona y mensaje."""
        assert payload.guild_id is not None  # comprobado por quien llama
        laughers = self._remember(self._laugh_reactors, payload.message_id)
        if payload.user_id in laughers:
            return
        laughers.add(payload.user_id)
        self.note(payload.guild_id, payload.user_id, StatDelta(add={"laugh_reacts_given": 1}))
        self.note(
            payload.guild_id,
            author_id,
            StatDelta(
                add={"laugh_reacts_received": 1},
                peak={"laugh_reacts_on_message_max": len(laughers)},
            ),
            payload.channel_id,
            replace_channel=False,
        )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Entradas, cambios de canal, huidas rápidas y pantallas compartidas."""
        if member.bot:
            return
        delta = voice_move_stats(
            before_channel=before.channel.id if before.channel else None,
            after_channel=after.channel.id if after.channel else None,
            started_stream=bool(after.self_stream and not before.self_stream),
            joined_at=self._voice_joined.get((member.guild.id, member.id)),
            now=self._clock(),
        )
        key = (member.guild.id, member.id)
        if after.channel is None:
            self._voice_joined.pop(key, None)
        elif before.channel is None:
            self._voice_joined[key] = self._clock()
        if before.channel is not None and before.channel != after.channel:
            if not [m for m in before.channel.members if not m.bot]:
                self._voice_openers.pop(before.channel.id, None)
                # Cerrar la llamada: el último en irse de una sala en la que había compañía.
                if key in self._sessions:
                    delta.add["voice_last_out"] = 1
        if after.channel is not None and before.channel != after.channel:
            humans = [m for m in after.channel.members if not m.bot]
            opener = self._voice_openers.get(after.channel.id)
            if len(humans) == 1:
                self._voice_openers[after.channel.id] = (member.id, False)
            elif opener is not None and not opener[1] and len(humans) >= VOICE_FULL_ROOM:
                self._voice_openers[after.channel.id] = (opener[0], True)
                self.note(
                    member.guild.id,
                    opener[0],
                    StatDelta(add={"voice_host": 1}),
                    replace_channel=False,
                )
        if delta:
            self.note(member.guild.id, member.id, delta, replace_channel=False)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra los logros del servidor cuando el bot sale de él."""
        self._pending.pop(guild.id, None)
        self._pending_channels.pop(guild.id, None)
        await self.repository.delete_guild_data(guild.id)

    # -- Comando --------------------------------------------------------------------

    async def ranking(self, guild: discord.Guild) -> discord.Embed:
        """Ranking del servidor por puntos de logros."""
        rows, _members = await self.repository.guild_unlocks(guild.id)
        by_user: dict[int, list[str]] = {}
        for user_id, achievement_id in rows:
            if achievement_id in BY_ID:
                by_user.setdefault(user_id, []).append(achievement_id)
        ordered = sorted(
            by_user.items(), key=lambda item: (-points(item[1]), -len(item[1]), item[0])
        )[:RANKING_SIZE]
        entries = []
        for user_id, ids in ordered:
            member = guild.get_member(user_id)
            name = (
                discord.utils.escape_markdown(member.display_name)
                if member is not None
                else f"<@{user_id}>"
            )
            entries.append((name, points(ids), len(ids)))
        return ranking_embed(entries)

    async def build_view(
        self, guild: discord.Guild, owner_id: int, target: discord.abc.User
    ) -> AchievementsView:
        """Prepara la vista de logros con los datos de `target`."""
        await self.flush()  # para que se vea lo del último minuto
        profile = await self.repository.profile(guild.id, target.id)
        rows, members = await self.repository.guild_unlocks(guild.id)
        holders = Counter(achievement_id for _user, achievement_id in rows)
        return AchievementsView(
            self,
            guild=guild,
            owner_id=owner_id,
            target=target,
            profile=profile,
            holders=holders,
            members=members,
        )

    def note_view(self, guild_id: int, viewer_id: int, target_id: int) -> None:
        """Cuenta que alguien ha mirado sus logros o los de otro (logros de cotilla)."""
        stat = "logros_views" if viewer_id == target_id else "logros_others"
        self.note(guild_id, viewer_id, StatDelta(add={stat: 1}))

    async def fresh_profile(self, guild_id: int, user_id: int) -> Profile:
        """Estadísticas y logros de un miembro, con lo del último minuto ya escrito."""
        await self.flush()
        return await self.repository.profile(guild_id, user_id)


# -- Puntos de entrada para otros cogs ----------------------------------------------------


def _cog(bot: commands.Bot) -> Achievements | None:
    return find_cog(bot, Achievements)


def note(
    bot: commands.Bot,
    guild_id: int,
    user_id: int,
    delta: StatDelta,
    channel_id: int | None = None,
) -> None:
    """Apunta estadísticas para la escritura de cada minuto. No hace nada sin el cog."""
    if (cog := _cog(bot)) is not None:
        cog.note(guild_id, user_id, delta, channel_id, replace_channel=False)


def note_for(bot: commands.Bot, responder: object, delta: StatDelta) -> None:
    """`note` para quien lanza un comando, sacando servidor, miembro y canal del
    `CommandResponder`. No hace nada fuera de un servidor ni sin el cog."""
    guild = getattr(responder, "guild", None)
    member = getattr(responder, "member", None)
    channel = getattr(responder, "channel", None)
    if guild is None or member is None or member.bot:
        return
    note(bot, guild.id, member.id, delta, getattr(channel, "id", None))


async def track(
    bot: commands.Bot,
    guild_id: int,
    user: discord.abc.User,
    channel: object,
    delta: StatDelta,
) -> None:
    """Registra ya una acción (IMV, renta, nivel…) y avisa si salta algún logro.

    Nunca lanza: si algo falla se registra y la acción que llama sigue.
    """
    cog = _cog(bot)
    if cog is None or user.bot:
        return
    target = channel if isinstance(channel, discord.abc.Messageable) else None
    try:
        await cog.apply(guild_id, user.id, delta, target)
    except Exception:
        logger.exception("No se pudieron registrar los logros de %s", user.id)


async def casino_play(
    bot: commands.Bot,
    guild_id: int,
    user: discord.abc.User,
    channel: object,
    delta: StatDelta,
    *,
    net: int,
) -> None:
    """Registra una jugada de casino (con las rachas entre juegos). Nunca lanza."""
    cog = _cog(bot)
    if cog is None or user.bot:
        return
    target = channel if isinstance(channel, discord.abc.Messageable) else None
    try:
        await cog.casino_play(guild_id, user.id, target, delta, net=net)
    except Exception:
        logger.exception("No se pudieron registrar los logros de casino de %s", user.id)


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con el repositorio, la economía y las estadísticas del bot."""
    await bot.add_cog(
        Achievements(
            bot,
            bot.achievements,
            economy=bot.economy,
            message_stats=bot.message_stats,
        )
    )
