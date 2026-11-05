"""La beernight: noches de llamada con mandamientos, eventos y chivatazos.

Comando: `beernight` (`/beernight` y `.beernight`). Es la excepción a los 8
caracteres de la Biblia: el evento del servidor se llama así.

- Sin opciones abre el panel. Si no hay noche, deja empezarla, ver el
  histórico, proponer mandamientos y tocar los ajustes. Si hay una en marcha,
  vuelve a publicar el panel de la noche al final del canal.
- `/beernight sonido:<momento> archivo:<audio>` (o `.beernight sonido
  <momento>` con el audio adjunto) sube un audio para ese momento de la
  noche (`SoundSlot`). Puede subirlo cualquiera; los borra quien gestiona.

Durante la noche:

- **Mandamientos activos** que rotan solos. Quien cae lo confiesa (🍺) o
  alguien se chiva (🚨) y otra persona lo confirma; si dos dicen que es
  mentira (una, con tres personas o menos), bebe el chivato.
- **Eventos aleatorios** cada pocos minutos: sorbos directos, duelos, retos,
  repartos, decretos con un mandamiento temporal y remodelaciones.
- **Participantes:** quien está en la llamada del anfitrión al empezar, quien
  entra después y quien pulsa algún botón. Los eventos solo tocan a quien
  sigue en la llamada y no se ha retirado (🚪).
- **Sonidos:** si el servidor ha subido audios y el bot no está ya en voz (la
  música manda), entra, suena el clip y se va, como los sonidos de entrada.
- Gestionan la noche (eventos a mano, rotar, ajustes, terminar) quien la
  empezó y los administradores.

Todo queda en el histórico (`bot.repositories.beernight`). Si el bot se
reinicia con una noche abierta, al volver la cierra con lo que había y
publica el resumen. No mueve yapdollars; los logros (🍻 Beernight) pagan su
premio como cualquier otro.

Requisitos: intent de estados de voz (incluido en `Intents.default()`) y,
para los sonidos, los permisos **Conectar** y **Hablar** en la llamada.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import random
import re
import time
from collections import Counter, deque
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import pets as mascotas
from bot.repositories.beernight import AllTime, BeernightRepository, CustomMandate, Night
from bot.repositories.beernight_sounds import BeernightSoundStore
from bot.services.achievements import (
    StatDelta,
    beernight_close_stats,
    beernight_drink_stats,
    beernight_streak,
)
from bot.services.beernight import (
    ACTIVE_BOUNDS,
    CAP_BOUNDS,
    CAP_WINDOW_SECONDS,
    DRINK_SOUND_GAP,
    EVENT_BOUNDS,
    MAX_CUSTOM_LENGTH,
    MAX_CUSTOM_SIPS,
    MAX_SOUND_SECONDS,
    REPORT_SECONDS,
    ROTATION_BOUNDS,
    SOUND_LABELS,
    VOTE_SECONDS,
    BeernightError,
    Bounds,
    Reason,
    Settings,
    SipRecord,
    SoundSlot,
    Summary,
    apply_cap,
    cast_event,
    clean_custom_text,
    custom_mandate,
    decree_mandate,
    duration_text,
    event_text,
    lie_votes_needed,
    liters,
    mandate_pool,
    next_event_delay,
    parse_number,
    pick_event,
    pick_mandates,
    rotation_size,
    summarize,
)
from bot.services.beernight_catalog import (
    CUSTOM_FAMILY,
    DECREE_FAMILY,
    EVENT_BY_KEY,
    FAMILIES,
    FAMILY_BY_KEY,
    MANDATE_BY_KEY,
    EventKind,
    Mandate,
    NightEvent,
)
from bot.services.entrance_sound import (
    MAX_UPLOAD_BYTES,
    EntranceSoundError,
    build_source_clip,
    check_duration,
    play_packets,
    probe_audio_duration,
    read_opus_packets,
    wait_until_audible,
)
from bot.services.levels import TIMEZONE
from bot.services.pets import Event, Moment
from bot.services.telemetry import TimedLock
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit, notify

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

COLOR = discord.Color.from_rgb(240, 170, 30)
END_COLOR = discord.Color.from_rgb(120, 80, 20)
NO_MENTIONS = discord.AllowedMentions.none()
#: Cada cuánto mira el bucle de la noche si toca evento, rotación o caducidad.
TICK_SECONDS = 15.0
#: Líneas del registro que enseña el panel.
LOG_LINES = 4
#: Eventos recientes que no se repiten.
RECENT_EVENTS = 12
#: Puestos del marcador en el panel y en el resumen.
SCOREBOARD_SIZE = 10
#: Tiempo de los menús efímeros (confesar, chivarse, ajustes, histórico).
MENU_TIMEOUT = 180.0
CONNECT_TIMEOUT_SECONDS = 10.0
PRE_ROLL_SECONDS = 0.3
ACCEPTED_MIME_PREFIXES = ("audio/", "video/", "application/ogg")
USAGE = (
    "Uso: `/beernight` abre el panel. Para subir un audio: `/beernight sonido:<momento> "
    "archivo:<audio>` o `.beernight sonido <momento>` con el audio adjunto "
    f"(hasta {MAX_SOUND_SECONDS:.0f} s). Momentos: "
    + ", ".join(f"`{slot.value}`" for slot in SoundSlot)
    + "."
)


def mention(user_id: int | None) -> str:
    """Mención de un miembro (o un guion si no hay nadie)."""
    return f"<@{user_id}>" if user_id is not None else "—"


def display(guild: discord.Guild | None, user_id: int) -> str:
    """Nombre visible de un miembro, o su mención si ya no está."""
    member = guild.get_member(user_id) if guild is not None else None
    return discord.utils.escape_markdown(member.display_name) if member else f"<@{user_id}>"


def is_manager(member: discord.abc.User, host_id: int | None) -> bool:
    """Si puede gestionar la noche: el anfitrión o quien administra el servidor."""
    if host_id is not None and member.id == host_id:
        return True
    permissions = getattr(member, "guild_permissions", None)
    return bool(permissions and (permissions.administrator or permissions.manage_guild))


def mandate_line(mandate: Mandate, *, until: float | None = None, now: float = 0.0) -> str:
    """Una línea de mandamiento para el panel: familia, texto y sorbos."""
    family = FAMILY_BY_KEY.get(mandate.family)
    if mandate.family == CUSTOM_FAMILY.key:
        emoji = CUSTOM_FAMILY.title.split()[0]
    elif mandate.family == DECREE_FAMILY.key:
        emoji = DECREE_FAMILY.title.split()[0]
    else:
        emoji = family.title.split()[0] if family else "📜"
    text = discord.utils.escape_markdown(mandate.text)
    line = f"{emoji} {text} · **{mandate.sips}** 🍺"
    if until is not None:
        line += f" · ⏳ {max(1, int((until - now) // 60) + 1)} min"
    return line


# -- Estado de una noche ------------------------------------------------------------------


@dataclass(slots=True)
class ActiveMandate:
    """Un mandamiento en juego y, si es de un decreto, cuándo caduca."""

    mandate: Mandate
    since: float
    until: float | None = None


@dataclass(slots=True)
class NightState:
    """Lo que vive en memoria mientras dura una noche."""

    night: Night
    settings: Settings
    custom: list[CustomMandate]
    active: list[ActiveMandate] = field(default_factory=list)
    joined: dict[int, float] = field(default_factory=dict)
    present: set[int] = field(default_factory=set)
    retired: set[int] = field(default_factory=set)
    sips: Counter[int] = field(default_factory=Counter)
    log: deque[str] = field(default_factory=lambda: deque(maxlen=LOG_LINES))
    recent_events: deque[str] = field(default_factory=lambda: deque(maxlen=RECENT_EVENTS))
    next_event_at: float = 0.0
    next_rotation_at: float = 0.0
    panel: tuple[int, int] | None = None
    task: asyncio.Task[None] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_drink_sound: float = 0.0
    closing: bool = False

    @property
    def guild_id(self) -> int:
        return self.night.guild_id

    @property
    def host_id(self) -> int:
        return self.night.host_id

    def custom_mandates(self) -> list[Mandate]:
        """Los mandamientos de la casa, como mandamientos."""
        return [custom_mandate(c.id, c.text, c.sips) for c in self.custom]

    def pool(self) -> list[Mandate]:
        """Repertorio del que salen los mandamientos de esta noche."""
        return mandate_pool(self.settings, self.custom_mandates())

    def players(self) -> list[int]:
        """A quién le pueden tocar los eventos: presentes y sin retirar.

        Si la noche no tiene llamada (el anfitrión no estaba en voz), cuenta
        todo el que participa.
        """
        base = self.present if self.night.voice_channel_id is not None else set(self.joined)
        return sorted(p for p in base if p not in self.retired)

    def newest(self) -> int | None:
        """El último en llegar de los que pueden jugar."""
        players = self.players()
        if not players:
            return None
        return max(players, key=lambda p: self.joined.get(p, 0.0))

    def find_mandate(self, key: str) -> Mandate | None:
        """Un mandamiento en juego por su clave."""
        for item in self.active:
            if item.mandate.key == key:
                return item.mandate
        return None


@dataclass(slots=True)
class Pending:
    """Algo que espera a que la gente lo resuelva con botones.

    Attributes:
        kind: `report` (chivatazo), `duel`, `challenge` o `gift`.
        who: El chivato, quien hace el reto o quien reparte.
        target: El acusado de un chivatazo.
        a: Primer duelista.
        b: Segundo duelista.
        mandate: Mandamiento del chivatazo.
        event: Evento que lo lanzó.
        confirms: Quien ha confirmado (o dicho «cumplido»).
        denies: Quien ha dicho «mentira» (o «no cumple»).
    """

    id: int
    night_id: int
    guild_id: int
    kind: str
    sips: int
    created_at: float
    who: int | None = None
    target: int | None = None
    a: int | None = None
    b: int | None = None
    mandate: Mandate | None = None
    event: NightEvent | None = None
    confirms: set[int] = field(default_factory=set)
    denies: set[int] = field(default_factory=set)
    message: tuple[int, int] | None = None
    done: bool = False

    @property
    def expires_at(self) -> float:
        seconds = REPORT_SECONDS if self.kind == "report" else VOTE_SECONDS
        return self.created_at + seconds


# -- Embeds -------------------------------------------------------------------------------


def idle_embed(last: str | None, settings: Settings, custom_count: int) -> discord.Embed:
    """Panel sin noche en marcha: qué es y cómo empezar."""
    lines = [
        "La noche estrella del servidor: a la llamada, a jugar a lo que sea y a beber lo que "
        "cada uno tenga. Nadie está obligado a nada.",
        "",
        "**Cómo va:** hay unos cuantos mandamientos activos que cambian solos. Quien cae lo "
        "confiesa con 🍺; si no, alguien se chiva con 🚨 y otra persona lo confirma. Si el "
        "chivatazo es mentira, bebe el chivato. Cada pocos minutos salta un evento: duelos, "
        "retos, repartos, decretos…",
        "",
        f"⏱️ Eventos cada {settings.event_min}-{settings.event_max} min · "
        f"📜 {settings.active} mandamientos, cambian cada {settings.rotation} min · "
        f"🧢 {'sin tope' if settings.cap == 0 else f'tope de {settings.cap} sorbos por hora'}",
        f"✍️ Mandamientos de la casa: {custom_count}",
    ]
    if last:
        lines += ["", f"🗓️ Última: {last}"]
    embed = discord.Embed(title="🍻 Beernight", description="\n".join(lines), color=COLOR)
    embed.set_footer(text="Pulsa ▶️ Empezar desde la llamada: entra quien esté en ella.")
    return embed


def live_embed(guild: discord.Guild | None, state: NightState, now: float) -> discord.Embed:
    """Panel de la noche en marcha: mandamientos, marcador y lo último que ha pasado."""
    night = state.night
    voice = f" en <#{night.voice_channel_id}>" if night.voice_channel_id else ""
    header = (
        f"Anfitrión: {mention(night.host_id)}{voice} · "
        f"lleva {duration_text(now - night.started_at)}"
    )
    mandates = [
        f"`{i}.` {mandate_line(item.mandate, until=item.until, now=now)}"
        for i, item in enumerate(state.active, start=1)
    ] or ["Sin mandamientos: enciende alguna familia en ⚙️ Ajustes."]
    embed = discord.Embed(title="🍻 Beernight en marcha", description=header, color=COLOR)
    embed.add_field(name="📜 Mandamientos", value="\n".join(mandates)[:1024], inline=False)
    board = scoreboard(guild, state)
    embed.add_field(name="🍺 Marcador", value=board[:1024], inline=False)
    if state.log:
        embed.add_field(name="📣 Lo último", value="\n".join(state.log)[:1024], inline=False)
    next_event = max(0, int((state.next_event_at - now) // 60))
    next_rotation = max(0, int((state.next_rotation_at - now) // 60))
    embed.set_footer(
        text=f"Próximo evento en ~{next_event} min · cambian mandamientos en ~{next_rotation} min"
    )
    return embed


def scoreboard(guild: discord.Guild | None, state: NightState) -> str:
    """Marcador de la noche: sorbos por persona, con quien está fuera o retirado."""
    people = set(state.joined) | set(state.sips)
    if not people:
        return "Aún no hay nadie. Entra en la llamada o pulsa cualquier botón."
    ordered = sorted(people, key=lambda p: (-state.sips[p], state.joined.get(p, 0.0)))
    lines = []
    for user_id in ordered[:SCOREBOARD_SIZE]:
        marks = ""
        if user_id in state.retired:
            marks = " · 🚪"
        elif state.night.voice_channel_id is not None and user_id not in state.present:
            marks = " · 🔇 fuera"
        lines.append(f"{display(guild, user_id)} — **{state.sips[user_id]}**{marks}")
    if len(ordered) > SCOREBOARD_SIZE:
        lines.append(f"…y {len(ordered) - SCOREBOARD_SIZE} más")
    return "\n".join(lines)


def summary_embed(
    guild: discord.Guild | None,
    night: Night,
    summary: Summary,
    people: int,
    *,
    names: Callable[[str], str | None],
    note: str | None = None,
) -> discord.Embed:
    """Resumen de una noche cerrada (al acabar y en el histórico).

    Args:
        people: Participantes de la noche.
        names: Texto de un mandamiento por su clave, si se conoce.
        note: Línea extra (por qué se cerró, la mascota del MVP…).
    """
    ended = night.ended_at or night.started_at
    started = datetime.fromtimestamp(night.started_at, TIMEZONE)
    lines = [
        f"🗓️ {started:%d/%m/%Y %H:%M} · ⏱️ {duration_text(ended - night.started_at)} · "
        f"👥 {people} · 🎩 {mention(night.host_id)}",
        f"🍺 **{summary.total}** sorbos (≈ {liters(summary.total)} L, según el INE de la birra)",
    ]
    forgiven = sum(summary.forgiven.values())
    if forgiven:
        lines.append(f"🧢 El tope perdonó {forgiven} sorbos.")
    awards = []
    if (mvp := summary.mvp()) is not None:
        awards.append(f"🏆 MVP: {mention(mvp)} ({summary.sips[mvp]} 🍺)")
    if (snitch := summary.snitch()) is not None:
        awards.append(f"🕵️ Chivato: {mention(snitch)} ({summary.reports_ok[snitch]} confirmados)")
    if (liar := summary.liar()) is not None:
        awards.append(f"🤥 Bulero: {mention(liar)} ({summary.lies[liar]} chivatazos falsos)")
    if (broken := summary.most_broken()) is not None:
        text = names(broken) or broken
        awards.append(
            f"📜 Más incumplido: «{discord.utils.escape_markdown(text)}» "
            f"({summary.broken[broken]} veces)"
        )
    if awards:
        lines += ["", *awards]
    if note:
        lines += ["", note]
    embed = discord.Embed(
        title="🏁 Se acabó la beernight", description="\n".join(lines), color=END_COLOR
    )
    ranking = [
        f"`{i}.` {display(guild, user_id)} — **{sips}**"
        for i, (user_id, sips) in enumerate(summary.sips.most_common(SCOREBOARD_SIZE), start=1)
    ]
    if ranking:
        embed.add_field(name="🍺 Marcador final", value="\n".join(ranking)[:1024], inline=False)
    return embed


# -- Componentes persistentes -----------------------------------------------------------

#: Botones del panel: acción -> (texto, emoji, estilo, fila).
PANEL_BUTTONS: dict[str, tuple[str, str, discord.ButtonStyle, int]] = {
    "empezar": ("Empezar", "▶️", discord.ButtonStyle.success, 0),
    "historico": ("Histórico", "📜", discord.ButtonStyle.secondary, 0),
    "proponer": ("Proponer", "✍️", discord.ButtonStyle.secondary, 0),
    "ajustes": ("Ajustes", "⚙️", discord.ButtonStyle.secondary, 0),
    "caigo": ("He caído", "🍺", discord.ButtonStyle.primary, 0),
    "chivo": ("Chivatazo", "🚨", discord.ButtonStyle.danger, 0),
    "brindis": ("Brindis", "🥂", discord.ButtonStyle.secondary, 0),
    "retiro": ("Me retiro / vuelvo", "🚪", discord.ButtonStyle.secondary, 0),
    "evento": ("Evento ya", "🎲", discord.ButtonStyle.secondary, 1),
    "rotar": ("Rotar", "🔄", discord.ButtonStyle.secondary, 1),
    "gestion": ("Ajustes", "⚙️", discord.ButtonStyle.secondary, 1),
    "fin": ("Terminar", "🏁", discord.ButtonStyle.danger, 1),
}
IDLE_ACTIONS = ("empezar", "historico", "proponer", "ajustes")
LIVE_ACTIONS = ("caigo", "chivo", "brindis", "retiro", "proponer", "evento", "rotar", "gestion")
LIVE_ACTIONS += ("fin",)


class PanelButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"birra:p:(?P<action>[a-z]+):(?P<night>\d+)",
):
    """Botón del panel. Persistente: sigue funcionando tras reiniciar el bot."""

    def __init__(self, action: str, night_id: int) -> None:
        label, emoji, style, row = PANEL_BUTTONS[action]
        if action == "proponer" and night_id:
            row = 1
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji=emoji,
                style=style,
                row=row,
                custom_id=f"birra:p:{action}:{night_id}",
            )
        )
        self.action = action
        self.night_id = night_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
        /,
    ) -> PanelButton:
        action = match["action"]
        if action not in PANEL_BUTTONS:
            raise ValueError(action)
        return cls(action, int(match["night"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = find_cog(interaction.client, Beernight)  # type: ignore[arg-type]
        if cog is not None:
            await cog.panel_action(interaction, self.action, self.night_id)


#: Botones de voto: elección -> (texto, emoji, estilo). `a`/`b` son «pierde A/B».
VOTE_BUTTONS: dict[str, tuple[str, str, discord.ButtonStyle]] = {
    "si": ("Confirmo", "✅", discord.ButtonStyle.success),
    "no": ("Mentira", "🤥", discord.ButtonStyle.danger),
    "ok": ("Cumplido", "✅", discord.ButtonStyle.success),
    "ko": ("No cumple", "🍺", discord.ButtonStyle.danger),
    "a": ("Pierde", "🍺", discord.ButtonStyle.primary),
    "b": ("Pierde", "🍺", discord.ButtonStyle.primary),
}


class VoteButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"birra:v:(?P<pending>\d+):(?P<choice>[a-z]+)",
):
    """Botón para resolver un chivatazo, un duelo o un reto."""

    def __init__(self, pending_id: int, choice: str, label: str | None = None) -> None:
        default_label, emoji, style = VOTE_BUTTONS[choice]
        super().__init__(
            discord.ui.Button(
                label=(label or default_label)[:80],
                emoji=emoji,
                style=style,
                custom_id=f"birra:v:{pending_id}:{choice}",
            )
        )
        self.pending_id = pending_id
        self.choice = choice

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
        /,
    ) -> VoteButton:
        choice = match["choice"]
        if choice not in VOTE_BUTTONS:
            raise ValueError(choice)
        return cls(int(match["pending"]), choice, item.label)

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = find_cog(interaction.client, Beernight)  # type: ignore[arg-type]
        if cog is not None:
            await cog.vote(interaction, self.pending_id, self.choice)


class GiftSelect(
    discord.ui.DynamicItem[discord.ui.UserSelect],
    template=r"birra:g:(?P<pending>\d+)",
):
    """Menú con el que quien reparte elige a su víctima."""

    def __init__(self, pending_id: int) -> None:
        super().__init__(
            discord.ui.UserSelect(
                placeholder="🎁 ¿A quién le toca?",
                custom_id=f"birra:g:{pending_id}",
                min_values=1,
                max_values=1,
            )
        )
        self.pending_id = pending_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.UserSelect,
        match: re.Match[str],
        /,
    ) -> GiftSelect:
        return cls(int(match["pending"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = find_cog(interaction.client, Beernight)  # type: ignore[arg-type]
        if cog is not None and self.item.values:
            await cog.gift(interaction, self.pending_id, self.item.values[0])


def panel_view(night_id: int) -> discord.ui.View:
    """Botones del panel: los de empezar si `night_id` es 0, los de la noche si no."""
    view = discord.ui.View(timeout=None)
    for action in LIVE_ACTIONS if night_id else IDLE_ACTIONS:
        view.add_item(PanelButton(action, night_id))
    return view


def vote_view(pending: Pending, guild: discord.Guild | None) -> discord.ui.View:
    """Botones para resolver algo pendiente."""
    view = discord.ui.View(timeout=None)
    if pending.kind == "report":
        view.add_item(VoteButton(pending.id, "si"))
        view.add_item(VoteButton(pending.id, "no"))
    elif pending.kind == "challenge":
        view.add_item(VoteButton(pending.id, "ok"))
        view.add_item(VoteButton(pending.id, "ko"))
    elif pending.kind == "duel":
        for choice, user_id in (("a", pending.a), ("b", pending.b)):
            name = display(guild, user_id) if user_id is not None else "?"
            view.add_item(VoteButton(pending.id, choice, f"Pierde {name}"))
    elif pending.kind == "gift":
        view.add_item(GiftSelect(pending.id))
    return view


# -- Menús efímeros ---------------------------------------------------------------------


def mandate_options(state: NightState) -> list[discord.SelectOption]:
    """Opciones de un desplegable con los mandamientos en juego."""
    return [
        discord.SelectOption(
            label=item.mandate.text[:100],
            value=item.mandate.key,
            description=f"{item.mandate.sips} sorbo{'s' if item.mandate.sips != 1 else ''}",
        )
        for item in state.active[:25]
    ]


class ConfessView(discord.ui.View):
    """«He caído»: elige qué mandamiento has incumplido."""

    def __init__(self, cog: Beernight, state: NightState) -> None:
        super().__init__(timeout=MENU_TIMEOUT)
        self.cog = cog
        self.night_id = state.night.id
        select: discord.ui.Select = discord.ui.Select(
            placeholder="🍺 ¿En qué has caído?", options=mandate_options(state)
        )
        select.callback = self._chosen  # type: ignore[method-assign]
        self.select = select
        self.add_item(select)

    async def _chosen(self, interaction: discord.Interaction) -> None:
        await self.cog.confess(interaction, self.night_id, self.select.values[0])


class ReportView(discord.ui.View):
    """«Chivatazo»: elige a quién y por qué mandamiento."""

    def __init__(self, cog: Beernight, state: NightState) -> None:
        super().__init__(timeout=MENU_TIMEOUT)
        self.cog = cog
        self.night_id = state.night.id
        self.mandate_key: str | None = None
        self.accused: int | None = None
        mandate: discord.ui.Select = discord.ui.Select(
            placeholder="📜 ¿Qué mandamiento ha incumplido?", options=mandate_options(state)
        )
        mandate.callback = self._mandate  # type: ignore[method-assign]
        self.mandate = mandate
        self.add_item(mandate)
        accused: discord.ui.UserSelect = discord.ui.UserSelect(placeholder="🫵 ¿Quién?")
        accused.callback = self._accused  # type: ignore[method-assign]
        self.accused_select = accused
        self.add_item(accused)
        send = discord.ui.Button(label="Chivarse", emoji="🚨", style=discord.ButtonStyle.danger)
        send.callback = self._send  # type: ignore[method-assign]
        self.add_item(send)

    async def _mandate(self, interaction: discord.Interaction) -> None:
        self.mandate_key = self.mandate.values[0]
        await interaction.response.defer()

    async def _accused(self, interaction: discord.Interaction) -> None:
        self.accused = self.accused_select.values[0].id
        await interaction.response.defer()

    async def _send(self, interaction: discord.Interaction) -> None:
        if self.mandate_key is None or self.accused is None:
            await interaction.response.send_message(
                "Elige el mandamiento y a la persona antes de chivarte.", ephemeral=True
            )
            return
        await self.cog.report(interaction, self.night_id, self.accused, self.mandate_key)


class RhythmModal(discord.ui.Modal, title="⏱️ Ritmo de la beernight"):
    """Formulario con los ajustes numéricos."""

    def __init__(self, cog: Beernight, guild_id: int, settings: Settings) -> None:
        super().__init__(timeout=MENU_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.events: discord.ui.TextInput = discord.ui.TextInput(
            label="Minutos entre eventos (mínimo-máximo)",
            default=f"{settings.event_min}-{settings.event_max}",
            max_length=7,
        )
        self.active: discord.ui.TextInput = discord.ui.TextInput(
            label=f"Mandamientos activos ({ACTIVE_BOUNDS.low}-{ACTIVE_BOUNDS.high})",
            default=str(settings.active),
            max_length=2,
        )
        self.rotation: discord.ui.TextInput = discord.ui.TextInput(
            label=f"Minutos entre rotaciones ({ROTATION_BOUNDS.low}-{ROTATION_BOUNDS.high})",
            default=str(settings.rotation),
            max_length=3,
        )
        self.cap: discord.ui.TextInput = discord.ui.TextInput(
            label="Tope de sorbos por hora (0 = sin tope)",
            default=str(settings.cap),
            max_length=3,
        )
        for item in (self.events, self.active, self.rotation, self.cap):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.save_rhythm(
            interaction,
            self.guild_id,
            events=self.events.value,
            active=self.active.value,
            rotation=self.rotation.value,
            cap=self.cap.value,
        )


class CustomModal(discord.ui.Modal, title="✍️ Proponer un mandamiento"):
    """Formulario para añadir un mandamiento de la casa."""

    def __init__(self, cog: Beernight, guild_id: int) -> None:
        super().__init__(timeout=MENU_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.text: discord.ui.TextInput = discord.ui.TextInput(
            label="Mandamiento",
            placeholder="Quien diga «wepa» sin acento boricua.",
            max_length=MAX_CUSTOM_LENGTH,
        )
        self.sips: discord.ui.TextInput = discord.ui.TextInput(
            label=f"Sorbos (1-{MAX_CUSTOM_SIPS})", default="1", max_length=1
        )
        self.add_item(self.text)
        self.add_item(self.sips)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.add_custom(interaction, self.guild_id, self.text.value, self.sips.value)


class SettingsView(discord.ui.View):
    """Ajustes del servidor: ritmo, sonido, familias, mandamientos propios y audios."""

    def __init__(
        self,
        cog: Beernight,
        guild: discord.Guild,
        settings: Settings,
        custom: list[CustomMandate],
        sounds: list[tuple[SoundSlot, str, int]],
    ) -> None:
        super().__init__(timeout=MENU_TIMEOUT)
        self.cog = cog
        self.guild_id = guild.id
        self.settings = settings

        rhythm = discord.ui.Button(label="Ritmo", emoji="⏱️", row=0)
        rhythm.callback = self._rhythm  # type: ignore[method-assign]
        self.add_item(rhythm)
        sound = discord.ui.Button(
            label="Sonidos: sí" if settings.sound else "Sonidos: no",
            emoji="🔊" if settings.sound else "🔇",
            row=0,
        )
        sound.callback = self._toggle_sound  # type: ignore[method-assign]
        self.add_item(sound)

        families: discord.ui.Select = discord.ui.Select(
            placeholder="🗂️ Familias de mandamientos encendidas",
            min_values=1,
            max_values=len(FAMILIES),
            options=[
                discord.SelectOption(
                    label=family.title,
                    value=family.key,
                    default=family.key not in settings.disabled_families,
                )
                for family in FAMILIES
            ],
            row=1,
        )
        families.callback = self._families  # type: ignore[method-assign]
        self.families = families
        self.add_item(families)

        if custom:
            remove: discord.ui.Select = discord.ui.Select(
                placeholder="🗑️ Borrar un mandamiento de la casa",
                options=[
                    discord.SelectOption(
                        label=c.text[:100],
                        value=str(c.id),
                        description=f"{c.sips} 🍺 · de {display(guild, c.author_id)}"[:100],
                    )
                    for c in custom[:25]
                ],
                row=2,
            )
            remove.callback = self._remove_custom  # type: ignore[method-assign]
            self.remove = remove
            self.add_item(remove)
        if sounds:
            audio: discord.ui.Select = discord.ui.Select(
                placeholder="🔇 Borrar un audio",
                options=[
                    discord.SelectOption(
                        label=f"{SOUND_LABELS[slot]} · {display(guild, uploader)}"[:100],
                        value=f"{slot.value}|{name}",
                    )
                    for slot, name, uploader in sounds[:25]
                ],
                row=3,
            )
            audio.callback = self._remove_sound  # type: ignore[method-assign]
            self.audio = audio
            self.add_item(audio)

    async def _rhythm(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(RhythmModal(self.cog, self.guild_id, self.settings))

    async def _toggle_sound(self, interaction: discord.Interaction) -> None:
        await self.cog.toggle_sound(interaction, self.guild_id)

    async def _families(self, interaction: discord.Interaction) -> None:
        await self.cog.save_families(interaction, self.guild_id, list(self.families.values))

    async def _remove_custom(self, interaction: discord.Interaction) -> None:
        await self.cog.remove_custom(interaction, self.guild_id, int(self.remove.values[0]))

    async def _remove_sound(self, interaction: discord.Interaction) -> None:
        slot, name = self.audio.values[0].split("|", 1)
        await self.cog.remove_sound(interaction, self.guild_id, SoundSlot(slot), name)


class HistoryView(discord.ui.View):
    """Histórico: un desplegable para ver el resumen de una noche concreta."""

    def __init__(self, cog: Beernight, guild_id: int, nights: list[Night]) -> None:
        super().__init__(timeout=MENU_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        select: discord.ui.Select = discord.ui.Select(
            placeholder="🗓️ Ver una noche",
            options=[
                discord.SelectOption(
                    label=f"{datetime.fromtimestamp(n.started_at, TIMEZONE):%d/%m/%Y %H:%M}",
                    value=str(n.id),
                    description=duration_text((n.ended_at or n.started_at) - n.started_at),
                )
                for n in nights[:25]
            ],
        )
        select.callback = self._chosen  # type: ignore[method-assign]
        self.select = select
        self.add_item(select)

    async def _chosen(self, interaction: discord.Interaction) -> None:
        await self.cog.show_night(interaction, self.guild_id, int(self.select.values[0]))


# -- El cog -------------------------------------------------------------------------------


class Beernight(commands.Cog):
    """La beernight del servidor: panel, mandamientos, eventos, sonidos e histórico."""

    def __init__(
        self,
        bot: commands.Bot,
        repository: BeernightRepository,
        sounds: BeernightSoundStore,
        *,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.bot = bot
        self.repository = repository
        self.sounds = sounds
        self.rng = rng or random.Random()
        self.clock = clock
        self.nights: dict[int, NightState] = {}
        self.pending: dict[int, Pending] = {}
        self._ids = itertools.count(1)
        self._voice_locks: dict[int, asyncio.Lock] = {}
        self._background: set[asyncio.Task[None]] = set()
        self._start_locks: dict[int, asyncio.Lock] = {}
        #: Si ya se cerraron las noches que dejó abiertas el último reinicio.
        self._recovered = False

    async def cog_load(self) -> None:
        """Registra los botones persistentes, que siguen vivos tras un reinicio."""
        self.bot.add_dynamic_items(PanelButton, VoteButton, GiftSelect)

    async def cog_unload(self) -> None:
        """Para los bucles y los audios. Las noches abiertas se cierran al volver."""
        self.bot.remove_dynamic_items(PanelButton, VoteButton, GiftSelect)
        tasks = [s.task for s in self.nights.values() if s.task is not None]
        tasks += list(self._background)
        for task in tasks:
            task.cancel()
        # `return_exceptions` recoge las cancelaciones sin dejar avisos sueltos.
        await asyncio.gather(*tasks, return_exceptions=True)

    def _spawn(self, coro: Coroutine[object, object, None]) -> None:
        """Lanza una tarea en segundo plano que se cancela al descargar el cog."""
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # -- Comando ------------------------------------------------------------------------

    @app_commands.command(
        name="beernight",
        description="La beernight: mandamientos, eventos y chivatazos para beber en llamada.",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        sonido="Sube un audio para un momento de la noche (con archivo).",
        archivo=f"Audio de hasta {MAX_SOUND_SECONDS:.0f} s (mp3, ogg, wav, m4a…).",
    )
    @app_commands.choices(
        sonido=[app_commands.Choice(name=SOUND_LABELS[s], value=s.value) for s in SoundSlot]
    )
    async def beernight(
        self,
        interaction: discord.Interaction,
        sonido: app_commands.Choice[str] | None = None,
        archivo: discord.Attachment | None = None,
    ) -> None:
        """`/beernight`: abre el panel o, con `sonido` y `archivo`, sube un audio."""
        guild, user, channel = interaction.guild, interaction.user, interaction.channel
        if guild is None or not isinstance(user, discord.Member) or channel is None:
            return
        if sonido is not None or archivo is not None:
            if sonido is None or archivo is None:
                await interaction.response.send_message(USAGE, ephemeral=True)
                return
            await interaction.response.defer(thinking=True, ephemeral=True)
            text = await self.upload_sound(guild.id, user, SoundSlot(sonido.value), archivo)
            await interaction.followup.send(text, ephemeral=True)
            return
        embed, view = await self._panel_for(guild)
        await interaction.response.send_message(
            embed=embed, view=view, allowed_mentions=NO_MENTIONS
        )
        message = await interaction.original_response()
        await self._adopt_panel(guild.id, message)

    @commands.command(name="beernight")
    @commands.guild_only()
    async def beernight_text(self, ctx: commands.Context, *args: str) -> None:
        """Versión de texto: `.beernight` o `.beernight sonido <momento>` con el audio adjunto."""
        guild, author = ctx.guild, ctx.author
        if guild is None or not isinstance(author, discord.Member):
            return
        if args:
            slot = parse_sound_args(args)
            attachment = ctx.message.attachments[0] if ctx.message.attachments else None
            if slot is None or attachment is None:
                await ctx.send(USAGE, allowed_mentions=NO_MENTIONS)
                return
            async with ctx.typing():
                text = await self.upload_sound(guild.id, author, slot, attachment)
            await ctx.send(text, allowed_mentions=NO_MENTIONS)
            return
        embed, view = await self._panel_for(guild)
        message = await ctx.send(embed=embed, view=view, allowed_mentions=NO_MENTIONS)
        await self._adopt_panel(guild.id, message)

    async def _panel_for(self, guild: discord.Guild) -> tuple[discord.Embed, discord.ui.View]:
        """Embed y botones del panel según haya noche o no."""
        state = self.nights.get(guild.id)
        if state is not None:
            return live_embed(guild, state, self.clock()), panel_view(state.night.id)
        settings = await self.repository.get_settings(guild.id)
        custom = await self.repository.list_custom(guild.id)
        recent = await self.repository.recent_nights(guild.id, limit=1)
        last = None
        if recent:
            night = recent[0]
            when = datetime.fromtimestamp(night.started_at, TIMEZONE)
            last = (
                f"{when:%d/%m/%Y} · "
                f"{duration_text((night.ended_at or night.started_at) - night.started_at)}"
            )
        return idle_embed(last, settings, len(custom)), panel_view(0)

    async def _adopt_panel(self, guild_id: int, message: discord.Message) -> None:
        """Si hay noche, el panel recién publicado pasa a ser el suyo y se borra el viejo."""
        state = self.nights.get(guild_id)
        if state is None:
            return
        old = state.panel
        state.panel = (message.channel.id, message.id)
        if old is not None and old[1] != message.id:
            channel = self.bot.get_channel(old[0])
            if isinstance(channel, discord.abc.Messageable):
                try:
                    await channel.get_partial_message(old[1]).delete()  # type: ignore[attr-defined]
                except discord.HTTPException:
                    pass  # Ya no está o no se puede borrar: el nuevo manda igual.

    # -- Botones del panel --------------------------------------------------------------

    async def panel_action(
        self, interaction: discord.Interaction, action: str, night_id: int
    ) -> None:
        """Reparte la pulsación de un botón del panel."""
        guild, user = interaction.guild, interaction.user
        if guild is None or not isinstance(user, discord.Member):
            return
        if action == "empezar":
            await self.start(interaction)
            return
        if action == "historico":
            await self.show_history(interaction)
            return
        if action == "proponer":
            await interaction.response.send_modal(CustomModal(self, guild.id))
            return
        state = self.nights.get(guild.id)
        if action == "ajustes":
            host = state.host_id if state is not None else None
            if not is_manager(user, host):
                await interaction.response.send_message(
                    "Los ajustes los toca el anfitrión de la noche o un administrador.",
                    ephemeral=True,
                )
                return
            await self.show_settings(interaction)
            return
        if state is None or state.night.id != night_id:
            await interaction.response.send_message(
                "Esta beernight ya terminó. Abre otra con `/beernight`.", ephemeral=True
            )
            return
        if action == "caigo":
            if not state.active:
                await interaction.response.send_message(
                    "No hay mandamientos en juego.", ephemeral=True
                )
                return
            await interaction.response.send_message(
                "¿En qué has caído?", view=ConfessView(self, state), ephemeral=True
            )
        elif action == "chivo":
            if not state.active:
                await interaction.response.send_message(
                    "No hay mandamientos en juego.", ephemeral=True
                )
                return
            await interaction.response.send_message(
                "🚨 ¿A quién has pillado? Otra persona tendrá que confirmarlo.",
                view=ReportView(self, state),
                ephemeral=True,
            )
        elif action == "brindis":
            await self.toast(interaction, state)
        elif action == "retiro":
            await self.toggle_retire(interaction, state)
        elif action in ("evento", "rotar", "gestion", "fin"):
            if not is_manager(user, state.host_id):
                await interaction.response.send_message(
                    "Eso lo hace el anfitrión de la noche o un administrador.", ephemeral=True
                )
                return
            if action == "evento":
                await interaction.response.send_message("🎲 Marchando un evento.", ephemeral=True)
                logros.note(self.bot, guild.id, user.id, StatDelta(add={"beer_events_forced": 1}))
                await self.fire_event(state)
            elif action == "rotar":
                # Se contesta antes del candado: lo retienen las rondas, que van a la base de datos.
                await interaction.response.send_message("🔄 Mandamientos nuevos.", ephemeral=True)
                async with state.lock:
                    self._rotate(state, everything=True)
                state.log.append("🔄 El anfitrión ha cambiado los mandamientos.")
                await self._refresh_panel(state)
            elif action == "gestion":
                await self.show_settings(interaction)
            else:
                await interaction.response.defer(thinking=True)
                await self.finish(state, interaction=interaction)

    # -- Empezar ------------------------------------------------------------------------

    async def start(self, interaction: discord.Interaction) -> None:
        """Abre una noche: participantes, mandamientos, panel y bucle de eventos."""
        guild, user, channel = interaction.guild, interaction.user, interaction.channel
        if guild is None or not isinstance(user, discord.Member) or channel is None:
            return
        # Abrir la noche guarda a cada persona de la llamada en la base de datos:
        # se acepta el clic antes.
        await ack(interaction)
        lock = self._start_locks.setdefault(guild.id, TimedLock("beernight:Beernight._start_locks"))
        async with lock:
            if guild.id in self.nights:
                await notify(
                    interaction,
                    "Ya hay una beernight en marcha. Pulsa sus botones o usa `/beernight`.",
                )
                return
            voice = user.voice.channel if user.voice is not None else None
            now = self.clock()
            try:
                night = await self.repository.open_night(
                    guild.id, user.id, channel.id, voice.id if voice else None, now
                )
            except BeernightError as error:
                await notify(interaction, str(error))
                return
            settings = await self.repository.get_settings(guild.id)
            custom = await self.repository.list_custom(guild.id)
            state = NightState(night=night, settings=settings, custom=custom)
            people = {user.id}
            if voice is not None:
                state.present = {m.id for m in voice.members if not m.bot}
                people |= state.present
            for person in people:
                await self.repository.add_participant(night.id, person, now)
                state.joined[person] = now
            self._rotate(state, everything=True)
            state.next_event_at = now + next_event_delay(settings, self.rng)
            state.next_rotation_at = now + settings.rotation * 60
            state.log.append(f"▶️ {mention(user.id)} abre la barra.")
            self.nights[guild.id] = state
        await edit(interaction, embed=live_embed(guild, state, now), view=panel_view(night.id))
        if interaction.message is not None:
            state.panel = (interaction.message.channel.id, interaction.message.id)
        state.task = asyncio.create_task(self._run(state), name=f"beernight-{guild.id}")
        self._play(state, SoundSlot.START)

    # -- Bucle de la noche --------------------------------------------------------------

    async def _run(self, state: NightState) -> None:
        """Mira cada `TICK_SECONDS` si toca evento, rotación o caducidad.

        Un fallo en una vuelta se registra y el bucle sigue: si no, una
        excepción dejaría la noche sin eventos hasta que alguien la cerrara.
        """
        while not state.closing:
            await asyncio.sleep(TICK_SECONDS)
            try:
                await self.tick(state)
            except Exception:
                logger.exception("Error en una vuelta de la beernight de %s", state.guild_id)

    async def tick(self, state: NightState) -> None:
        """Una vuelta del bucle: caducidades, rotación y evento si toca."""
        now = self.clock()
        changed = False
        async with state.lock:
            before = len(state.active)
            state.active = [a for a in state.active if a.until is None or a.until > now]
            if len(state.active) != before:
                state.log.append("⌛ Ha caducado un decreto.")
                changed = True
            if now >= state.next_rotation_at:
                self._rotate(state)
                state.next_rotation_at = now + state.settings.rotation * 60
                state.log.append("🔄 Han cambiado los mandamientos.")
                changed = True
        await self._expire_pending(state, now)
        if now >= state.next_event_at:
            await self.fire_event(state)
            changed = False  # `fire_event` ya refresca el panel.
        if changed:
            await self._refresh_panel(state)

    def _rotate(self, state: NightState, *, everything: bool = False) -> None:
        """Cambia los mandamientos: todos, o un tercio de los más antiguos."""
        now = self.clock()
        decrees = [a for a in state.active if a.until is not None]
        regular = [a for a in state.active if a.until is None]
        regular.sort(key=lambda a: a.since)
        if everything:
            leaving = list(regular)
        else:
            leaving = regular[: rotation_size(state.settings.active)]
        staying = [a for a in regular if a not in leaving]
        wanted = state.settings.active - len(staying)
        excluded = {a.mandate.key for a in state.active}
        chosen = pick_mandates(
            state.pool(),
            wanted,
            self.rng,
            exclude=excluded,
            avoid_families={a.mandate.family for a in staying},
        )
        if len(chosen) < wanted:
            # No hay bastantes nuevos: se quedan algunos de los que tocaba quitar.
            staying += leaving[: wanted - len(chosen)]
        state.active = staying + [ActiveMandate(m, now) for m in chosen] + decrees

    async def _expire_pending(self, state: NightState, now: float) -> None:
        """Archiva lo que nadie ha resuelto a tiempo."""
        for pending in list(self.pending.values()):
            if pending.night_id != state.night.id or pending.done or now < pending.expires_at:
                continue
            pending.done = True
            self.pending.pop(pending.id, None)
            text = {
                "report": "⌛ Chivatazo sobreseído: nadie lo confirmó.",
                "gift": "⌛ Nadie eligió víctima. Sorbos perdonados.",
            }.get(pending.kind, "⌛ Nadie lo resolvió a tiempo. Se perdona.")
            await self._edit_pending(pending, text)

    # -- Eventos ------------------------------------------------------------------------

    async def fire_event(self, state: NightState, event: NightEvent | None = None) -> None:
        """Lanza un evento aleatorio (o el indicado) y programa el siguiente."""
        guild = self.bot.get_guild(state.guild_id)
        now = self.clock()
        state.next_event_at = now + next_event_delay(state.settings, self.rng)
        players = state.players()
        event = event or pick_event(len(players), self.rng, recent=state.recent_events)
        if event is None:
            await self._refresh_panel(state)
            return
        state.recent_events.append(event.key)
        host = state.host_id if state.host_id in players else None
        try:
            casting = cast_event(
                event, players, self.rng, sips=state.sips, host=host, newest=state.newest()
            )
        except BeernightError:
            await self._refresh_panel(state)
            return
        text = event_text(event, casting, mention)
        involved = [u for u in (casting.who, casting.a, casting.b, *casting.drinkers) if u]
        extra: list[str] = []
        view: discord.ui.View | None = None
        pending: Pending | None = None
        if event.kind is EventKind.DRINK:
            records = await self._drink(
                state,
                [
                    (user_id, event.sips, Reason.EVENT, event.key, None)
                    for user_id in casting.drinkers
                ],
                sound=False,
            )
            extra += forgiven_lines(records)
            if len(casting.drinkers) == 1 and (
                pet := await mascotas.cameo(
                    self.bot, state.guild_id, casting.drinkers[0], Moment(Event.BEER)
                )
            ):
                extra.append(pet)
        elif event.kind is EventKind.DECREE:
            mandate = decree_mandate(event)
            async with state.lock:
                state.active = [a for a in state.active if a.mandate.key != mandate.key]
                state.active.append(ActiveMandate(mandate, now, now + event.minutes * 60))
        elif event.kind is EventKind.RESHUFFLE:
            async with state.lock:
                self._rotate(state, everything=True)
                state.next_rotation_at = now + state.settings.rotation * 60
        else:
            kind = {
                EventKind.DUEL: "duel",
                EventKind.CHALLENGE: "challenge",
                EventKind.GIFT: "gift",
            }[event.kind]
            pending = Pending(
                id=next(self._ids),
                night_id=state.night.id,
                guild_id=state.guild_id,
                kind=kind,
                sips=event.sips,
                created_at=now,
                who=casting.who,
                a=casting.a,
                b=casting.b,
                event=event,
            )
            self.pending[pending.id] = pending
            view = vote_view(pending, guild)
            extra.append(pending_hint(pending))
        state.log.append(short(text))
        message = await self._post(state, "\n".join([text, *extra]), view=view, mentions=involved)
        if pending is not None and message is not None:
            pending.message = (message.channel.id, message.id)
        self._play(state, SoundSlot.EVENT)
        await self._refresh_panel(state)

    # -- Sorbos -------------------------------------------------------------------------

    async def _drink(
        self,
        state: NightState,
        wanted: list[tuple[int, int, Reason, str | None, int | None]],
        *,
        sound: bool = True,
    ) -> list[SipRecord]:
        """Apunta sorbos (con el tope), suma logros y pone el sonido de «alguien bebe».

        Args:
            wanted: `(quién, sorbos, motivo, mandamiento o evento, quién lo provoca)`.
            sound: `False` cuando ya va a sonar otro audio (el de un evento).
        """
        now = self.clock()
        records = []
        async with state.lock:
            for user_id, sips, reason, mandate, by in wanted:
                await self._join(state, user_id, now)
                recent = 0
                if state.settings.cap:
                    recent = await self.repository.sips_since(
                        state.night.id, user_id, now - CAP_WINDOW_SECONDS
                    )
                drunk, forgiven = apply_cap(sips, recent, state.settings.cap)
                record = SipRecord(user_id, drunk, reason, mandate, by, now, forgiven)
                records.append(record)
                state.sips[user_id] += drunk
            await self.repository.add_sips(state.night.id, state.guild_id, records)
        channel_id = state.night.channel_id
        for record in records:
            logros.note(
                self.bot,
                state.guild_id,
                record.user_id,
                beernight_drink_stats(record.reason.value, record.sips, forgiven=record.forgiven),
                channel_id,
            )
            author = self._custom_author(state, record)
            if author is not None and author != record.user_id:
                logros.note(
                    self.bot,
                    state.guild_id,
                    author,
                    StatDelta(add={"beer_custom_broken": 1}),
                    channel_id,
                )
        gap_ok = now - state.last_drink_sound >= DRINK_SOUND_GAP
        if sound and gap_ok and any(r.sips for r in records):
            state.last_drink_sound = now
            self._play(state, SoundSlot.DRINK)
        return records

    @staticmethod
    def _custom_author(state: NightState, record: SipRecord) -> int | None:
        """Autor del mandamiento de la casa incumplido, si lo es."""
        if record.reason not in (Reason.CONFESSION, Reason.REPORT) or not record.mandate:
            return None
        for custom in state.custom:
            if f"c{custom.id}" == record.mandate:
                return custom.author_id
        return None

    async def _join(self, state: NightState, user_id: int, now: float) -> None:
        """Apunta a alguien en la noche si aún no estaba."""
        if user_id in state.joined:
            return
        state.joined[user_id] = now
        await self.repository.add_participant(state.night.id, user_id, now)

    async def confess(self, interaction: discord.Interaction, night_id: int, key: str) -> None:
        """«He caído»: quien pulsa bebe por el mandamiento elegido."""
        state = self._state_for(interaction, night_id)
        if state is None:
            await interaction.response.send_message("Esta beernight ya terminó.", ephemeral=True)
            return
        mandate = state.find_mandate(key)
        if mandate is None:
            await interaction.response.send_message(
                "Ese mandamiento ya no está en juego.", ephemeral=True
            )
            return
        user = interaction.user
        await interaction.response.edit_message(content="🍺 Apuntado. ¡Salud!", view=None)
        records = await self._drink(state, [(user.id, mandate.sips, Reason.CONFESSION, key, None)])
        lines = [
            f"🍺 {mention(user.id)} confiesa: «{discord.utils.escape_markdown(mandate.text)}». "
            f"**{records[0].sips}** sorbo{'s' if records[0].sips != 1 else ''}.",
            *forgiven_lines(records),
        ]
        if pet := await mascotas.cameo(self.bot, state.guild_id, user.id, Moment(Event.BEER)):
            lines.append(pet)
        state.log.append(f"🍺 {mention(user.id)} confiesa ({records[0].sips})")
        await self._post(state, "\n".join(lines), mentions=[])
        await self._refresh_panel(state)

    async def toast(self, interaction: discord.Interaction, state: NightState) -> None:
        """«Brindis»: un sorbo por gusto, sin mandamiento."""
        user = interaction.user
        await interaction.response.send_message("🥂 ¡Salud!", ephemeral=True)
        records = await self._drink(state, [(user.id, 1, Reason.TOAST, None, None)])
        state.log.append(f"🥂 {mention(user.id)} brinda por gusto")
        await self._post(
            state,
            f"🥂 {mention(user.id)} brinda porque quiere. Nadie se lo ha pedido.",
            mentions=[],
        )
        if records:
            await self._refresh_panel(state)

    async def toggle_retire(self, interaction: discord.Interaction, state: NightState) -> None:
        """«Me retiro / vuelvo»: los eventos dejan (o vuelven) a contar con quien pulsa."""
        user = interaction.user
        # Apuntarse espera al candado de la noche y escribe: se acepta el clic antes.
        await ack(interaction, new_message=True)
        async with state.lock:
            await self._join(state, user.id, self.clock())
            if user.id in state.retired:
                state.retired.discard(user.id)
                text = "🙋 Has vuelto. Los eventos vuelven a contar contigo."
                state.log.append(f"🙋 {mention(user.id)} vuelve a la barra")
            else:
                state.retired.add(user.id)
                text = "🚪 Te has retirado. Los eventos ya no te tocan; pulsa otra vez para volver."
                state.log.append(f"🚪 {mention(user.id)} se retira a sus aposentos")
                logros.note(self.bot, state.guild_id, user.id, StatDelta(add={"beer_retired": 1}))
        await edit(interaction, content=text)
        await self._refresh_panel(state)

    # -- Chivatazos, duelos, retos y repartos --------------------------------------------

    async def report(
        self, interaction: discord.Interaction, night_id: int, accused: int, key: str
    ) -> None:
        """Publica un chivatazo para que otra persona lo confirme o lo tumbe."""
        state = self._state_for(interaction, night_id)
        if state is None:
            await interaction.response.send_message("Esta beernight ya terminó.", ephemeral=True)
            return
        mandate = state.find_mandate(key)
        guild = interaction.guild
        member = guild.get_member(accused) if guild is not None else None
        if mandate is None:
            await interaction.response.send_message(
                "Ese mandamiento ya no está en juego.", ephemeral=True
            )
            return
        if member is not None and member.bot:
            await interaction.response.send_message("Los bots no beben. Todavía.", ephemeral=True)
            return
        user = interaction.user
        if accused == user.id:
            await interaction.response.send_message(
                "Para chivarte de ti mismo está 🍺 He caído.", ephemeral=True
            )
            return
        await interaction.response.edit_message(content="🚨 Chivatazo enviado.", view=None)
        async with state.lock:
            await self._join(state, user.id, self.clock())
        pending = Pending(
            id=next(self._ids),
            night_id=state.night.id,
            guild_id=state.guild_id,
            kind="report",
            sips=mandate.sips,
            created_at=self.clock(),
            who=user.id,
            target=accused,
            mandate=mandate,
        )
        self.pending[pending.id] = pending
        text = (
            f"🚨 {mention(user.id)} acusa a {mention(accused)}: "
            f"«{discord.utils.escape_markdown(mandate.text)}» ({mandate.sips} 🍺)\n"
            f"{pending_hint(pending, players=len(state.joined))}"
        )
        message = await self._post(state, text, view=vote_view(pending, guild), mentions=[accused])
        if message is not None:
            pending.message = (message.channel.id, message.id)
        self._play(state, SoundSlot.REPORT)

    async def vote(self, interaction: discord.Interaction, pending_id: int, choice: str) -> None:
        """Un voto en algo pendiente: confirmar o tumbar, cumplido o no, quién pierde."""
        pending = self.pending.get(pending_id)
        if pending is None or pending.done:
            await interaction.response.send_message(
                "Eso ya está resuelto (o caducó).", ephemeral=True
            )
            return
        state = self.nights.get(pending.guild_id)
        if state is None or state.night.id != pending.night_id:
            await interaction.response.send_message("Esta beernight ya terminó.", ephemeral=True)
            return
        user = interaction.user
        if pending.kind == "report":
            await self._vote_report(interaction, state, pending, user.id, choice)
        elif pending.kind == "challenge":
            await self._vote_challenge(interaction, state, pending, user.id, choice)
        elif pending.kind == "duel":
            await self._vote_duel(interaction, state, pending, user.id, choice)
        else:
            await interaction.response.send_message("Eso no se vota.", ephemeral=True)

    async def _vote_report(
        self,
        interaction: discord.Interaction,
        state: NightState,
        pending: Pending,
        voter: int,
        choice: str,
    ) -> None:
        if voter == pending.who:
            await interaction.response.send_message(
                "No puedes votar tu propio chivatazo.", ephemeral=True
            )
            return
        if choice == "si":
            if voter == pending.target:
                await interaction.response.send_message(
                    "Si has caído, confiésalo con 🍺 He caído. Aquí solo puedes decir «mentira».",
                    ephemeral=True,
                )
                return
            pending.done = True
            self.pending.pop(pending.id, None)
            await interaction.response.defer()
            assert pending.target is not None and pending.mandate is not None
            records = await self._drink(
                state,
                [(pending.target, pending.sips, Reason.REPORT, pending.mandate.key, pending.who)],
            )
            add = {"beer_reports_ok": 1, "beer_given": records[0].sips}
            if pending.target == state.host_id:
                add["beer_snitch_host"] = 1
            logros.note(self.bot, state.guild_id, pending.who, StatDelta(add=add))
            logros.note(self.bot, state.guild_id, voter, StatDelta(add={"beer_confirms": 1}))
            lines = [
                f"✅ Confirmado por {mention(voter)}. {mention(pending.target)} bebe "
                f"**{records[0].sips}**. Chivato: {mention(pending.who)}.",
                *forgiven_lines(records),
            ]
            if pet := await mascotas.cameo(
                self.bot, state.guild_id, pending.target, Moment(Event.BEER)
            ):
                lines.append(pet)
            state.log.append(f"🚨 {mention(pending.target)} pillado ({records[0].sips})")
            await self._edit_pending(pending, "\n".join(lines), interaction=interaction)
            await self._refresh_panel(state)
            return
        if voter in pending.denies:
            await interaction.response.send_message("Ya habías votado «mentira».", ephemeral=True)
            return
        pending.denies.add(voter)
        logros.note(self.bot, state.guild_id, voter, StatDelta(add={"beer_denies": 1}))
        needed = lie_votes_needed(len(state.joined))
        if len(pending.denies) < needed:
            await interaction.response.send_message(
                f"🤥 Voto apuntado ({len(pending.denies)}/{needed}).", ephemeral=True
            )
            return
        pending.done = True
        self.pending.pop(pending.id, None)
        await interaction.response.defer()
        assert pending.who is not None and pending.mandate is not None
        records = await self._drink(
            state, [(pending.who, pending.sips, Reason.LIE, pending.mandate.key, None)]
        )
        voters = ", ".join(mention(v) for v in sorted(pending.denies))
        lines = [
            f"🤥 Chivatazo tumbado por {voters}. Bulo de {mention(pending.who)}, que bebe "
            f"**{records[0].sips}**.",
            *forgiven_lines(records),
        ]
        state.log.append(f"🤥 {mention(pending.who)} pillado mintiendo ({records[0].sips})")
        await self._edit_pending(pending, "\n".join(lines), interaction=interaction)
        await self._refresh_panel(state)

    async def _vote_challenge(
        self,
        interaction: discord.Interaction,
        state: NightState,
        pending: Pending,
        voter: int,
        choice: str,
    ) -> None:
        if choice not in ("ok", "ko"):
            await interaction.response.send_message("Ese botón no es de aquí.", ephemeral=True)
            return
        if voter == pending.who and choice == "ok":
            await interaction.response.send_message(
                "Si lo has cumplido, que lo diga otra persona.", ephemeral=True
            )
            return
        pending.done = True
        self.pending.pop(pending.id, None)
        await interaction.response.defer()
        assert pending.who is not None and pending.event is not None
        if choice == "ok":
            logros.note(
                self.bot, state.guild_id, pending.who, StatDelta(add={"beer_challenges_ok": 1})
            )
            text = f"✅ {mention(pending.who)} cumple el reto (según {mention(voter)}). Se libra."
            state.log.append(f"✅ {mention(pending.who)} cumple el reto")
        else:
            records = await self._drink(
                state, [(pending.who, pending.sips, Reason.CHALLENGE, pending.event.key, None)]
            )
            logros.note(
                self.bot,
                state.guild_id,
                pending.who,
                StatDelta(add={"beer_challenges_failed": 1}),
            )
            text = "\n".join(
                [
                    f"🍺 {mention(pending.who)} no cumple el reto (según {mention(voter)}): "
                    f"**{records[0].sips}**.",
                    *forgiven_lines(records),
                ]
            )
            state.log.append(f"🎤 {mention(pending.who)} falla el reto ({records[0].sips})")
        await self._edit_pending(pending, text, interaction=interaction)
        await self._refresh_panel(state)

    async def _vote_duel(
        self,
        interaction: discord.Interaction,
        state: NightState,
        pending: Pending,
        voter: int,
        choice: str,
    ) -> None:
        if choice not in ("a", "b"):
            await interaction.response.send_message("Ese botón no es de aquí.", ephemeral=True)
            return
        loser = pending.a if choice == "a" else pending.b
        winner = pending.b if choice == "a" else pending.a
        if voter == winner:
            await interaction.response.send_message(
                "No vale declararte ganador. Que lo diga otra persona (o que se rinda).",
                ephemeral=True,
            )
            return
        pending.done = True
        self.pending.pop(pending.id, None)
        await interaction.response.defer()
        assert loser is not None and winner is not None and pending.event is not None
        records = await self._drink(
            state, [(loser, pending.sips, Reason.DUEL, pending.event.key, winner)]
        )
        logros.note(self.bot, state.guild_id, winner, StatDelta(add={"beer_duels_won": 1}))
        logros.note(self.bot, state.guild_id, loser, StatDelta(add={"beer_duels_lost": 1}))
        text = "\n".join(
            [
                f"⚔️ Gana {mention(winner)}. {mention(loser)} bebe **{records[0].sips}**.",
                *forgiven_lines(records),
            ]
        )
        state.log.append(f"⚔️ {mention(loser)} pierde el duelo ({records[0].sips})")
        await self._edit_pending(pending, text, interaction=interaction)
        await self._refresh_panel(state)

    async def gift(
        self, interaction: discord.Interaction, pending_id: int, chosen: discord.abc.User
    ) -> None:
        """Quien reparte elige a quién le tocan los sorbos."""
        pending = self.pending.get(pending_id)
        if pending is None or pending.done:
            await interaction.response.send_message(
                "Ese reparto ya está hecho (o caducó).", ephemeral=True
            )
            return
        state = self.nights.get(pending.guild_id)
        if state is None or state.night.id != pending.night_id:
            await interaction.response.send_message("Esta beernight ya terminó.", ephemeral=True)
            return
        if interaction.user.id != pending.who:
            await interaction.response.send_message(
                f"Reparte {mention(pending.who)}, no tú.", ephemeral=True
            )
            return
        if chosen.bot:
            await interaction.response.send_message("Los bots no beben. Todavía.", ephemeral=True)
            return
        pending.done = True
        self.pending.pop(pending.id, None)
        await interaction.response.defer()
        assert pending.event is not None and pending.who is not None
        records = await self._drink(
            state, [(chosen.id, pending.sips, Reason.GIFT, pending.event.key, pending.who)]
        )
        logros.note(
            self.bot, state.guild_id, pending.who, StatDelta(add={"beer_given": records[0].sips})
        )
        target = "a sí mismo" if chosen.id == pending.who else f"a {mention(chosen.id)}"
        text = "\n".join(
            [
                f"🎁 {mention(pending.who)} se lo da {target}: **{records[0].sips}** sorbos.",
                *forgiven_lines(records),
            ]
        )
        state.log.append(f"🎁 {mention(chosen.id)} recibe un reparto ({records[0].sips})")
        await self._edit_pending(pending, text, interaction=interaction)
        await self._refresh_panel(state)

    def _state_for(self, interaction: discord.Interaction, night_id: int) -> NightState | None:
        guild = interaction.guild
        state = self.nights.get(guild.id) if guild is not None else None
        if state is None or state.night.id != night_id or state.closing:
            return None
        return state

    # -- Terminar -----------------------------------------------------------------------

    async def finish(
        self, state: NightState, *, interaction: discord.Interaction | None = None
    ) -> None:
        """Cierra la noche: histórico, resumen, logros y sonido de despedida."""
        if state.closing:
            return
        state.closing = True
        if state.task is not None and state.task is not asyncio.current_task():
            state.task.cancel()
        self.nights.pop(state.guild_id, None)
        for pending in [p for p in self.pending.values() if p.night_id == state.night.id]:
            pending.done = True
            self.pending.pop(pending.id, None)
            await self._edit_pending(pending, "🏁 La noche acabó antes de resolverlo.")
        self._play(state, SoundSlot.END)
        now = self.clock()
        await self.repository.close_night(state.night.id, now)
        night = await self.repository.get_night(state.guild_id, state.night.id) or state.night
        embed = await self._close_summary(night, state.custom)
        channel = self.bot.get_channel(night.channel_id)
        if interaction is not None:
            await interaction.followup.send(embed=embed, allowed_mentions=NO_MENTIONS)
        elif isinstance(channel, discord.abc.Messageable):
            try:
                await channel.send(embed=embed, allowed_mentions=NO_MENTIONS)
            except discord.HTTPException:
                logger.warning("No se pudo publicar el resumen de la beernight", exc_info=True)
        if state.panel is not None:
            panel_channel = self.bot.get_channel(state.panel[0])
            if isinstance(panel_channel, discord.abc.Messageable):
                try:
                    await panel_channel.get_partial_message(state.panel[1]).edit(  # type: ignore[attr-defined]
                        embed=discord.Embed(
                            title="🏁 Beernight terminada",
                            description="El resumen está justo debajo. `/beernight` para otra.",
                            color=END_COLOR,
                        ),
                        view=None,
                    )
                except discord.HTTPException:
                    pass  # El panel ya no está: el resumen sale igual.

    async def _close_summary(
        self, night: Night, custom: list[CustomMandate], *, note: str | None = None
    ) -> discord.Embed:
        """Resume una noche cerrada y reparte los logros del cierre."""
        guild = self.bot.get_guild(night.guild_id)
        records = await self.repository.night_records(night.id)
        participants = await self.repository.participants(night.id)
        summary = summarize(records)
        ended = night.ended_at or self.clock()
        started_local = datetime.fromtimestamp(night.started_at, TIMEZONE)
        ended_local = datetime.fromtimestamp(ended, TIMEZONE)
        mvp = summary.mvp()
        channel = self.bot.get_channel(night.channel_id)
        for user_id, joined in participants.items():
            dates = await self.repository.night_dates(night.guild_id, user_id)
            streak = beernight_streak(datetime.fromtimestamp(d, TIMEZONE).date() for d in dates)
            delta = beernight_close_stats(
                sips=summary.sips[user_id],
                minutes=int((ended - max(joined, night.started_at)) // 60),
                crowd=len(participants),
                mvp=user_id == mvp,
                host=user_id == night.host_id,
                started=started_local,
                ended=ended_local,
                streak=streak,
            )
            member = guild.get_member(user_id) if guild is not None else None
            if member is not None:
                await logros.track(self.bot, night.guild_id, member, channel, delta)
            else:
                logros.note(self.bot, night.guild_id, user_id, delta)
        lines = [note] if note else []
        if mvp is not None and (
            pet := await mascotas.cameo(self.bot, night.guild_id, mvp, Moment(Event.BEER))
        ):
            lines.append(pet)
        texts = {f"c{c.id}": c.text for c in custom}
        return summary_embed(
            guild,
            night,
            summary,
            len(participants),
            names=lambda key: mandate_text(key, texts),
            note="\n".join(lines) or None,
        )

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """La primera vez que conecta, cierra lo que dejó abierto un reinicio.

        `on_ready` se repite en cada reconexión: el indicador evita repetirlo.
        """
        if self._recovered:
            return
        self._recovered = True
        try:
            await self.recover()
        except Exception:
            logger.exception("No se pudieron cerrar las beernights que quedaron abiertas")

    async def recover(self) -> None:
        """Cierra las noches que dejó abiertas un reinicio y publica su resumen."""
        for night in await self.repository.open_nights():
            if night.guild_id in self.nights:
                continue
            records = await self.repository.night_records(night.id)
            ended = max((r.created_at for r in records), default=night.started_at)
            await self.repository.close_night(night.id, max(ended, night.started_at))
            closed = await self.repository.get_night(night.guild_id, night.id) or night
            custom = await self.repository.list_custom(night.guild_id)
            embed = await self._close_summary(
                closed, custom, note="🔌 El bot se reinició a mitad de noche: se cierra aquí."
            )
            channel = self.bot.get_channel(night.channel_id)
            if isinstance(channel, discord.abc.Messageable):
                try:
                    await channel.send(embed=embed, allowed_mentions=NO_MENTIONS)
                except discord.HTTPException:
                    logger.warning("No se pudo publicar una beernight recuperada", exc_info=True)

    # -- Histórico ----------------------------------------------------------------------

    async def show_history(self, interaction: discord.Interaction) -> None:
        """Ranking de siempre y últimas noches, con un desplegable para ver una."""
        guild = interaction.guild
        if guild is None:
            return
        await ack(interaction, new_message=True)
        all_time = await self.repository.all_time(guild.id)
        nights = await self.repository.recent_nights(guild.id, limit=25)
        embed = history_embed(guild, all_time, nights)
        kwargs: dict[str, object] = {"embed": embed}
        if nights:
            kwargs["view"] = HistoryView(self, guild.id, nights)
        await edit(interaction, **kwargs)

    async def show_night(
        self, interaction: discord.Interaction, guild_id: int, night_id: int
    ) -> None:
        """Resumen de una noche del histórico."""
        await ack(interaction, new_message=True)
        night = await self.repository.get_night(guild_id, night_id)
        if night is None or night.ended_at is None:
            await edit(interaction, content="Esa noche no está.")
            return
        records = await self.repository.night_records(night.id)
        participants = await self.repository.participants(night.id)
        custom = await self.repository.list_custom(guild_id)
        texts = {f"c{c.id}": c.text for c in custom}
        embed = summary_embed(
            interaction.guild,
            night,
            summarize(records),
            len(participants),
            names=lambda key: mandate_text(key, texts),
        )
        embed.title = "📜 Una noche del histórico"
        await edit(interaction, embed=embed)

    # -- Ajustes ------------------------------------------------------------------------

    async def _settings_view(self, guild: discord.Guild) -> tuple[discord.Embed, SettingsView]:
        settings = await self.repository.get_settings(guild.id)
        custom = await self.repository.list_custom(guild.id)
        sounds: list[tuple[SoundSlot, str, int]] = []
        for slot in SoundSlot:
            for sound in await self.sounds.list_sounds(guild.id, slot):
                sounds.append((slot, sound.name, sound.uploader_id))
        return settings_embed(settings, custom, sounds), SettingsView(
            self, guild, settings, custom, sounds
        )

    async def show_settings(self, interaction: discord.Interaction) -> None:
        """Abre los ajustes (solo para quien gestiona)."""
        guild = interaction.guild
        if guild is None:
            return
        await ack(interaction, new_message=True)
        embed, view = await self._settings_view(guild)
        await edit(interaction, embed=embed, view=view)

    async def _can_manage(self, interaction: discord.Interaction, guild_id: int) -> bool:
        state = self.nights.get(guild_id)
        if is_manager(interaction.user, state.host_id if state else None):
            # Lo que viene después guarda ajustes en la base de datos: se acepta ya.
            await ack(interaction)
            return True
        await interaction.response.send_message(
            "Los ajustes los toca el anfitrión de la noche o un administrador.", ephemeral=True
        )
        return False

    async def _after_settings(
        self, interaction: discord.Interaction, guild_id: int, note: str
    ) -> None:
        """Aplica los ajustes nuevos a la noche en marcha y redibuja el menú."""
        settings = await self.repository.get_settings(guild_id)
        state = self.nights.get(guild_id)
        if state is not None:
            async with state.lock:
                state.settings = settings
                state.custom = await self.repository.list_custom(guild_id)
                keys = {m.key for m in state.pool()}
                state.active = [
                    a for a in state.active if a.until is not None or a.mandate.key in keys
                ]
                regular = [a for a in state.active if a.until is None]
                if len(regular) > settings.active:
                    extra = regular[: len(regular) - settings.active]
                    state.active = [a for a in state.active if a not in extra]
                elif len(regular) < settings.active:
                    now = self.clock()
                    chosen = pick_mandates(
                        state.pool(),
                        settings.active - len(regular),
                        self.rng,
                        exclude={a.mandate.key for a in state.active},
                        avoid_families={a.mandate.family for a in regular},
                    )
                    state.active += [ActiveMandate(m, now) for m in chosen]
            await self._refresh_panel(state)
        guild = interaction.guild
        if guild is None:
            return
        embed, view = await self._settings_view(guild)
        embed.description = f"{note}\n\n{embed.description or ''}"
        await edit(interaction, embed=embed, view=view)

    async def save_rhythm(
        self,
        interaction: discord.Interaction,
        guild_id: int,
        *,
        events: str,
        active: str,
        rotation: str,
        cap: str,
    ) -> None:
        """Guarda los ajustes numéricos del formulario ⏱️ Ritmo."""
        if not await self._can_manage(interaction, guild_id):
            return
        try:
            low_raw, _, high_raw = events.replace(" ", "").partition("-")
            low = parse_number(low_raw, EVENT_BOUNDS)
            high = parse_number(high_raw or low_raw, EVENT_BOUNDS)
            current = await self.repository.get_settings(guild_id)
            settings = Settings(
                event_min=low,
                event_max=high,
                active=parse_number(active, ACTIVE_BOUNDS),
                rotation=parse_number(rotation, ROTATION_BOUNDS),
                cap=parse_number(cap, CAP_BOUNDS),
                sound=current.sound,
                disabled_families=current.disabled_families,
            ).validated()
        except BeernightError as error:
            await notify(interaction, f"❌ {error}")
            return
        await self.repository.save_settings(guild_id, settings)
        state = self.nights.get(guild_id)
        if state is not None:
            now = self.clock()
            state.next_event_at = min(
                state.next_event_at, now + next_event_delay(settings, self.rng)
            )
            state.next_rotation_at = min(state.next_rotation_at, now + settings.rotation * 60)
        await self._after_settings(interaction, guild_id, "✅ Ritmo guardado.")

    async def toggle_sound(self, interaction: discord.Interaction, guild_id: int) -> None:
        """Enciende o apaga los sonidos en la llamada."""
        if not await self._can_manage(interaction, guild_id):
            return
        current = await self.repository.get_settings(guild_id)
        settings = replace(current, sound=not current.sound)
        await self.repository.save_settings(guild_id, settings)
        note = "🔊 Sonidos encendidos." if settings.sound else "🔇 Sonidos apagados."
        await self._after_settings(interaction, guild_id, note)

    async def save_families(
        self, interaction: discord.Interaction, guild_id: int, enabled: list[str]
    ) -> None:
        """Guarda qué familias de serie están encendidas."""
        if not await self._can_manage(interaction, guild_id):
            return
        current = await self.repository.get_settings(guild_id)
        disabled = frozenset(f.key for f in FAMILIES if f.key not in set(enabled))
        settings = replace(current, disabled_families=disabled)
        await self.repository.save_settings(guild_id, settings)
        await self._after_settings(interaction, guild_id, "✅ Familias guardadas.")

    async def add_custom(
        self, interaction: discord.Interaction, guild_id: int, text: str, sips_raw: str
    ) -> None:
        """Apunta un mandamiento de la casa (lo puede proponer cualquiera)."""
        await ack(interaction)
        try:
            cleaned = clean_custom_text(text)
            sips = parse_number(sips_raw, Bounds(1, MAX_CUSTOM_SIPS, "Sorbos"))
            custom = await self.repository.add_custom(
                guild_id, interaction.user.id, cleaned, sips, self.clock()
            )
        except BeernightError as error:
            await notify(interaction, f"❌ {error}")
            return
        logros.note(
            self.bot, guild_id, interaction.user.id, StatDelta(add={"beer_custom_added": 1})
        )
        state = self.nights.get(guild_id)
        if state is not None:
            async with state.lock:
                state.custom.append(custom)
        await notify(
            interaction,
            f"✍️ Apuntado en el repertorio: «{discord.utils.escape_markdown(cleaned)}» "
            f"({sips} 🍺). Saldrá cuando le toque.",
        )

    async def remove_custom(
        self, interaction: discord.Interaction, guild_id: int, custom_id: int
    ) -> None:
        """Borra un mandamiento de la casa."""
        if not await self._can_manage(interaction, guild_id):
            return
        removed = await self.repository.delete_custom(guild_id, custom_id)
        note = "🗑️ Mandamiento borrado." if removed else "Ese mandamiento ya no estaba."
        await self._after_settings(interaction, guild_id, note)

    async def remove_sound(
        self, interaction: discord.Interaction, guild_id: int, slot: SoundSlot, name: str
    ) -> None:
        """Borra un audio de la beernight."""
        if not await self._can_manage(interaction, guild_id):
            return
        removed = await self.sounds.delete(guild_id, slot, name)
        note = "🔇 Audio borrado." if removed else "Ese audio ya no estaba."
        await self._after_settings(interaction, guild_id, note)

    # -- Sonidos ------------------------------------------------------------------------

    async def upload_sound(
        self,
        guild_id: int,
        member: discord.Member,
        slot: SoundSlot,
        attachment: discord.Attachment,
    ) -> str:
        """Convierte y guarda un audio para un momento de la noche.

        Returns:
            El texto para quien lo sube (éxito o el motivo del fallo).
        """
        content_type = attachment.content_type
        if content_type is not None and not content_type.startswith(ACCEPTED_MIME_PREFIXES):
            return "Ese archivo no parece un audio."
        if attachment.size > MAX_UPLOAD_BYTES:
            return f"El archivo es demasiado grande (máximo {MAX_UPLOAD_BYTES // 2**20} MB)."
        work = await asyncio.to_thread(self.sounds.make_work_dir)
        try:
            work_dir = Path(work.name)
            upload = work_dir / "upload"
            await asyncio.to_thread(upload.write_bytes, await attachment.read())
            check_duration(await probe_audio_duration(upload), MAX_SOUND_SECONDS)
            clip = work_dir / "clip.ogg"
            await build_source_clip(upload, clip, MAX_SOUND_SECONDS)
            read_opus_packets(await asyncio.to_thread(clip.read_bytes))
            await self.sounds.save(guild_id, slot, member.id, clip, self.clock())
        except (EntranceSoundError, BeernightError) as error:
            return f"❌ {error}"
        except discord.HTTPException:
            logger.warning("No se pudo descargar un audio de la beernight", exc_info=True)
            return "No se pudo descargar el archivo. Inténtalo de nuevo."
        except Exception:
            logger.exception("Error inesperado al guardar un audio de la beernight")
            return "Algo salió mal al preparar el audio."
        finally:
            await asyncio.to_thread(work.cleanup)
        logros.note(self.bot, guild_id, member.id, StatDelta(add={"beer_sound_saved": 1}))
        return f"🔊 Audio guardado para «{SOUND_LABELS[slot]}». Sonará en la próxima beernight."

    def _play(self, state: NightState, slot: SoundSlot) -> None:
        """Pone un audio del momento en la llamada de la noche, en segundo plano."""
        if state.settings.sound and state.night.voice_channel_id is not None:
            self._spawn(self._play_now(state.guild_id, state.night.voice_channel_id, slot))

    async def _play_now(self, guild_id: int, channel_id: int, slot: SoundSlot) -> None:
        """Entra ensordecido, suena el clip y se va. Si el bot ya está en voz, no suena."""
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        lock = self._voice_locks.setdefault(guild_id, TimedLock("beernight:Beernight._voice_locks"))
        if lock.locked():
            return  # Ya está sonando otro: mejor uno que dos pisándose.
        async with lock:
            if guild.voice_client is not None:
                return  # La música (o un sonido de entrada) tiene prioridad.
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.VoiceChannel | discord.StageChannel):
                return
            permissions = channel.permissions_for(guild.me)
            if not (permissions.connect and permissions.speak):
                return
            clip = await self.sounds.read_random(guild_id, slot, self.rng)
            if clip is None:
                return
            try:
                packets = read_opus_packets(clip)
            except EntranceSoundError:
                logger.warning("Audio de beernight dañado en %s (%s)", guild_id, slot.value)
                return
            voice: discord.VoiceClient | None = None
            try:
                voice = await channel.connect(timeout=CONNECT_TIMEOUT_SECONDS, self_deaf=True)
                # Sin esto el audio sale antes de que el cifrado de voz esté listo.
                await wait_until_audible(voice)
                await asyncio.sleep(PRE_ROLL_SECONDS)
                await play_packets(voice, packets)
            except (discord.ClientException, discord.HTTPException, TimeoutError):
                logger.warning("No se pudo poner un audio de beernight", exc_info=True)
            finally:
                if voice is not None and guild.voice_client is voice:
                    try:
                        await voice.disconnect(force=False)
                    except discord.HTTPException:
                        logger.warning("Error al salir de la llamada", exc_info=True)

    # -- Mensajes -----------------------------------------------------------------------

    async def _post(
        self,
        state: NightState,
        text: str,
        *,
        view: discord.ui.View | None = None,
        mentions: list[int],
    ) -> discord.Message | None:
        """Publica en el canal de la noche, mencionando solo a quien toca."""
        channel = self.bot.get_channel(state.night.channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            return None
        kwargs: dict[str, object] = {
            "content": text[:2000],
            "allowed_mentions": discord.AllowedMentions(
                everyone=False,
                roles=False,
                users=[discord.Object(u) for u in dict.fromkeys(mentions)],
            ),
        }
        if view is not None:
            kwargs["view"] = view
        try:
            return await channel.send(**kwargs)  # type: ignore[arg-type]
        except discord.HTTPException:
            logger.warning("No se pudo publicar en la beernight", exc_info=True)
            return None

    async def _edit_pending(
        self,
        pending: Pending,
        text: str,
        *,
        interaction: discord.Interaction | None = None,
    ) -> None:
        """Cierra el mensaje de algo pendiente: deja el resultado y quita los botones."""
        if interaction is not None and interaction.message is not None:
            try:
                await interaction.message.edit(
                    content=text[:2000], view=None, allowed_mentions=NO_MENTIONS
                )
                return
            except discord.HTTPException:
                pass
        if pending.message is None:
            return
        channel = self.bot.get_channel(pending.message[0])
        if not isinstance(channel, discord.abc.Messageable):
            return
        try:
            await channel.get_partial_message(
                pending.message[1]
            ).edit(  # type: ignore[attr-defined]
                content=text[:2000], view=None, allowed_mentions=NO_MENTIONS
            )
        except discord.HTTPException:
            pass  # Lo han borrado: no pasa nada.

    async def _refresh_panel(self, state: NightState) -> None:
        """Redibuja el panel de la noche, si sigue ahí."""
        if state.panel is None or state.closing:
            return
        channel = self.bot.get_channel(state.panel[0])
        if not isinstance(channel, discord.abc.Messageable):
            return
        guild = self.bot.get_guild(state.guild_id)
        try:
            await channel.get_partial_message(state.panel[1]).edit(  # type: ignore[attr-defined]
                embed=live_embed(guild, state, self.clock()),
                view=panel_view(state.night.id),
                allowed_mentions=NO_MENTIONS,
            )
        except discord.NotFound:
            state.panel = None  # Lo borraron: `/beernight` lo vuelve a publicar.
        except discord.HTTPException:
            logger.warning("No se pudo redibujar el panel de la beernight", exc_info=True)

    # -- Voz ----------------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Quien entra en la llamada de la noche se apunta; quien sale deja de contar."""
        if member.bot or before.channel == after.channel:
            return
        state = self.nights.get(member.guild.id)
        if state is None or state.night.voice_channel_id is None or state.closing:
            return
        voice_id = state.night.voice_channel_id
        changed = False
        if after.channel is not None and after.channel.id == voice_id:
            async with state.lock:
                state.present.add(member.id)
                if member.id not in state.joined:
                    await self._join(state, member.id, self.clock())
                    state.log.append(f"👋 {mention(member.id)} se une a la barra")
            changed = True
        elif before.channel is not None and before.channel.id == voice_id:
            state.present.discard(member.id)
            changed = True
        if changed:
            await self._refresh_panel(state)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra todo lo de la beernight cuando el bot sale de un servidor."""
        state = self.nights.pop(guild.id, None)
        if state is not None and state.task is not None:
            state.task.cancel()
        await self.repository.delete_guild_data(guild.id)
        await self.sounds.delete_guild(guild.id)


# -- Textos sueltos -----------------------------------------------------------------------


def parse_sound_args(args: tuple[str, ...]) -> SoundSlot | None:
    """Lee `sonido <momento>` de `.beernight`. `None` si no se entiende."""
    if len(args) != 2 or args[0].lower() != "sonido":
        return None
    try:
        return SoundSlot(args[1].lower())
    except ValueError:
        return None


def forgiven_lines(records: list[SipRecord]) -> list[str]:
    """Aviso pequeño de lo que ha perdonado el tope."""
    return [
        f"-# 🧢 Tope por hora: a {mention(r.user_id)} se le perdonan {r.forgiven}."
        for r in records
        if r.forgiven
    ]


def pending_hint(pending: Pending, *, players: int = 0) -> str:
    """Instrucciones para resolver algo pendiente."""
    minutes = (REPORT_SECONDS if pending.kind == "report" else VOTE_SECONDS) // 60
    if pending.kind == "report":
        needed = lie_votes_needed(players)
        return (
            f"-# Que lo confirme otra persona (✅). Con {needed} voto"
            f"{'s' if needed != 1 else ''} de 🤥 bebe el chivato. Caduca en {minutes} min."
        )
    if pending.kind == "duel":
        return f"-# Pulsad quién ha perdido. Caduca en {minutes} min."
    if pending.kind == "challenge":
        return f"-# Que otra persona diga si lo ha cumplido. Caduca en {minutes} min."
    return f"-# Elige a tu víctima en el menú. Caduca en {minutes} min."


def short(text: str, limit: int = 90) -> str:
    """Primera línea de un anuncio, recortada para el registro del panel."""
    line = text.splitlines()[0]
    return line if len(line) <= limit else line[: limit - 1] + "…"


def mandate_text(key: str, custom: dict[str, str]) -> str | None:
    """Texto de un mandamiento por su clave: de serie, de la casa o de un decreto."""
    if key in MANDATE_BY_KEY:
        return MANDATE_BY_KEY[key].text
    if key in custom:
        return custom[key]
    if key.startswith("ev:") and (event := EVENT_BY_KEY.get(key[3:])) is not None:
        return event.rule
    return None


def settings_embed(
    settings: Settings,
    custom: list[CustomMandate],
    sounds: list[tuple[SoundSlot, str, int]],
) -> discord.Embed:
    """Resumen de los ajustes del servidor."""
    families = [
        f"{'✅' if f.key not in settings.disabled_families else '⬜'} {f.title}" for f in FAMILIES
    ]
    counts = Counter(slot for slot, _name, _uploader in sounds)
    audio = [f"{SOUND_LABELS[slot]}: {counts[slot]}" for slot in SoundSlot]
    lines = [
        f"⏱️ Eventos cada **{settings.event_min}-{settings.event_max}** min",
        f"📜 **{settings.active}** mandamientos activos, cambian cada **{settings.rotation}** min",
        "🧢 "
        + ("Sin tope de sorbos" if settings.cap == 0 else f"Tope de **{settings.cap}** por hora"),
        f"{'🔊' if settings.sound else '🔇'} Sonidos en la llamada: "
        f"**{'sí' if settings.sound else 'no'}**",
        "",
        "**Familias**",
        *families,
        f"✍️ De la casa: {len(custom)}",
        "",
        "**Audios** (se suben con `/beernight sonido:… archivo:…`)",
        *audio,
    ]
    return discord.Embed(
        title="⚙️ Ajustes de la beernight", description="\n".join(lines), color=COLOR
    )


def history_embed(guild: discord.Guild, all_time: AllTime, nights: list[Night]) -> discord.Embed:
    """Ranking de siempre y lista de las últimas noches."""
    total = sum(all_time.sips.values())
    embed = discord.Embed(
        title="📜 Histórico de la beernight",
        description=(
            f"🗓️ {all_time.total_nights} noches · 🍺 {total} sorbos (≈ {liters(total)} L)"
            if all_time.total_nights
            else "Aún no hay ninguna noche cerrada. ¿A qué esperáis?"
        ),
        color=COLOR,
    )
    if all_time.sips:
        ranking = [
            f"`{i}.` {display(guild, user_id)} — **{sips}** 🍺 · "
            f"{all_time.nights[user_id]} noches · {all_time.mvps[user_id]} MVP"
            for i, (user_id, sips) in enumerate(all_time.sips.most_common(SCOREBOARD_SIZE), 1)
        ]
        embed.add_field(name="🏆 Ranking de siempre", value="\n".join(ranking)[:1024], inline=False)
    if all_time.reports_ok:
        snitches = [f"{display(guild, u)} — {n}" for u, n in all_time.reports_ok.most_common(3)]
        embed.add_field(name="🕵️ Chivatos", value="\n".join(snitches), inline=True)
    if all_time.lies:
        liars = [f"{display(guild, u)} — {n}" for u, n in all_time.lies.most_common(3)]
        embed.add_field(name="🤥 Buleros", value="\n".join(liars), inline=True)
    if nights:
        recent = []
        for night in nights[:8]:
            when = datetime.fromtimestamp(night.started_at, TIMEZONE)
            length = duration_text((night.ended_at or night.started_at) - night.started_at)
            recent.append(f"• {when:%d/%m %H:%M} · {length} · 🎩 {display(guild, night.host_id)}")
        embed.add_field(name="🗓️ Últimas noches", value="\n".join(recent)[:1024], inline=False)
    return embed


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con el histórico y los audios del bot."""
    await bot.add_cog(Beernight(bot, bot.beernight, bot.beernight_sounds))
