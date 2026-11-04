"""Timba: Texas Hold'em sin límite entre jugadores, una mesa por canal (`poker` modo timba).

No tiene comando propio: `/poker timba` o `.poker timba` (o `poker` y el botón
♣️ Timba) abre la mesa del canal (o la baja al final si ya estaba más arriba) y
te sienta, con `open_and_sit`; `/poker timba 50` la abre con ciega grande de 50
(la pequeña es la mitad). Se juega de dos a `MAX_SEATS`; los asientos se quedan
de una mano a otra hasta que pulsas 🚪 Levantarse. Reglas en `bot.services.timba`.

**Una mano.** Con dos o más sentados, empieza cuando todos pulsan ✅ Listo o, si
no, a los `START_SECONDS`: se ponen las
ciegas, cada uno ve sus cartas en privado con 🂠 Mis cartas y habla por turnos
(`TURN_SECONDS` cada uno): ✅ Pasar o 📞 Igualar, ⬆️ Subir (mínimo, medio bote,
bote, todo o 🔢 una cantidad) y 🏳️ Retirarse. Los botones solo valen para quien
habla. Quien se duerme pasa si puede y, si no, se retira; a las
`MAX_TIMEOUTS` seguidas se le levanta de la mesa. Al acabar, la mesa enseña las
cartas del enfrentamiento y lo que gana o pierde cada uno, y empieza la siguiente.

**El dinero** (`EconomyService`): al empezar la mano se aparta de cada uno lo que
se juega en ella, su saldo hasta `MAX_BUY_BB` ciegas grandes (`place_bet`); al
acabar se le devuelve lo que no gastó más lo que gana (`settle_timba`, que
ajusta el IRPF del día). Así no hay fichas guardadas: un reinicio a mitad de mano
la anula y devuelve lo apartado. Tratamiento fiscal: juego (art. 33.1 LIRPF). La
comisión de la casa (5 % de cada bote disputado que llega al flop, con tope)
va al Estado como Impuesto sobre Actividades de Juego, como en las porras, y
sale en `hacienda`. Para sentarse hacen falta `MIN_BUY_BB` ciegas grandes.

**Lo que se ve.** Una imagen quieta de la mesa en cada turno (la misma escena del
póker en modo timba: `PokerScene.timba`), con el bote, las comunitarias que se
ven, las cartas boca abajo de cada uno, quién habla, el botón y la última acción
de cada asiento. Las cartas solo se enseñan en el enfrentamiento.

Solo se juega en los canales del casino que elige `garito` (sin elegir, los de `CASINO_CHANNEL_IDS`)
y en sus hilos. Permisos del bot en el canal: enviar mensajes, insertar enlaces, adjuntar archivos y
añadir reacciones (para confirmar `.poker timba`).
"""

from __future__ import annotations

import asyncio
import copy
import io
import logging
import math
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs import shop as tienda
from bot.cogs.casino import casino_channel_error
from bot.services.achievements import casino_stats, timba_stats
from bot.services.casino_channels import CasinoChannelIds
from bot.services.economy import (
    BalanceLimitError,
    BetSettlement,
    EconomyService,
    InsufficientFundsError,
    format_amount,
    parse_amount,
)
from bot.services.levels import TIMEZONE
from bot.services.pets import bet_moment
from bot.services.poker import GAME as POKER_GAME
from bot.services.poker import evaluate
from bot.services.poker_render import TimbaSeat, timba_meta, timba_state
from bot.services.poker_scene import PokerScene
from bot.services.taxes import TAX_COLLECTOR
from bot.services.telemetry import TimedLock
from bot.services.timba import (
    GAME,
    MAX_BUY_BB,
    MAX_SEATS,
    MIN_BUY_BB,
    RAKE_CAP_BB,
    RAKE_PCT,
    Act,
    Event,
    TimbaError,
    TimbaHand,
)
from bot.utils.interactions import ack, notify

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

DEFAULT_BIG_BLIND = 20
#: Pausa antes de cada mano, para que se siente quien quiera (✅ Listo la corta).
START_SECONDS = 2 * 60
#: Tiempo para hablar; quien se duerme pasa si puede y, si no, se retira.
TURN_SECONDS = 30
#: A las tantas seguidas dormido, se le levanta de la mesa.
MAX_TIMEOUTS = 2
#: Sin nadie sentado (o con uno solo) durante este tiempo, la mesa se cierra.
IDLE_SECONDS = 10 * 60
#: Desde este bote (en ciegas grandes) se anuncia en el canal.
SHOUT_POT_BB = 100
#: Acciones que se listan en la mesa.
LOG_LIMIT = 6
#: Personas cuya mascota puede hablar en el resultado.
HINTS_LIMIT = 4

TABLE_PNG = "timba.png"
COLOR = discord.Color.from_rgb(30, 90, 60)
COLOR_TURN = discord.Color.from_rgb(212, 160, 40)
NO_MENTIONS = discord.AllowedMentions.none()

QUIPS = (
    "Aquí no hay banca: solo colegas y Hacienda.",
    "Lo que pasa en la timba se queda en la timba (y en el libro de Hacienda).",
    "Timba de trastienda.",
    "Cara de póker y a callar.",
    "Hazme café, que esta mano va para largo. ¡Quiero café!",
)


#: Una imagen de la mesa por adelantado: `id` de la mano real y sus acciones hasta entonces.
PaintKey = tuple[int, tuple[Event, ...]]


def paint_key(hand: TimbaHand) -> PaintKey:
    return (id(hand), tuple(hand.events))


class Phase(Enum):
    """En qué está la mesa."""

    LOBBY = "lobby"
    PLAYING = "playing"
    CLOSED = "closed"


@dataclass(slots=True, eq=False)
class Chair:
    """Alguien sentado en la mesa (se queda de una mano a otra).

    Attributes:
        interaction: Su última interacción, para el aviso de la Renta.
        leaving: Si ha pulsado 🚪 Levantarse: se va al acabar la mano.
        ready: Si ha pulsado ✅ Listo para la mano siguiente.
        timeouts: Veces seguidas que se ha dormido.
        stack: Lo que se juega en la mano en curso (0 si no la juega).
    """

    user: discord.abc.User
    interaction: discord.Interaction | None = None
    leaving: bool = False
    ready: bool = False
    timeouts: int = 0
    stack: int = 0
    hand_timeouts: int = 0
    joined_at: float = 0.0


def short_name(name: str) -> str:
    """Nombre recortado para las listas."""
    return name if len(name) <= 16 else name[:15] + "…"


def event_text(event: Event, name: str) -> str:
    """`Ana sube a 80`, `Leo se retira`, `Eva va con todo (300)`."""
    if event.act is Act.RAISE:
        text = f"{name} sube a {format_amount(event.amount)}"
    elif event.act is Act.CALL:
        text = f"{name} iguala {format_amount(event.amount)}"
    else:
        text = f"{name} {event.act.label}"
    return text + (" · ¡con todo!" if event.all_in else "")


def badge_for(event: Event) -> tuple[str, str]:
    """La placa de un asiento con su última acción."""
    if event.all_in:
        return "lose", "ALL-IN"
    return {
        Act.FOLD: ("fold", "RETIRADO"),
        Act.CHECK: ("fold", "PASA"),
        Act.CALL: ("tie", "IGUALA"),
        Act.RAISE: ("win", f"SUBE {event.amount}"),
    }[event.act]


# -- Vistas ---------------------------------------------------------------------------------------


def _button(
    view: discord.ui.View,
    label: str,
    emoji: str,
    style: discord.ButtonStyle,
    callback: Callable[[discord.Interaction], Awaitable[None]],
    row: int,
    custom_id: str,
    *,
    disabled: bool = False,
) -> None:
    button: discord.ui.Button = discord.ui.Button(
        label=label,
        emoji=emoji,
        style=style,
        row=row,
        custom_id=f"{GAME}:{custom_id}",
        disabled=disabled,
    )
    button.callback = callback  # type: ignore[method-assign]
    view.add_item(button)


class RaiseModal(discord.ui.Modal, title="🔢 ¿A cuánto subes?"):
    """Pide a cuánto sube (su total en la ronda)."""

    amount: discord.ui.TextInput = discord.ui.TextInput(
        label="Subir a (500, 2k…)", placeholder="100", max_length=12
    )

    def __init__(self, table: Table) -> None:
        super().__init__()
        self.table = table

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Sube a esa cantidad (solo cambia memoria: se contesta directamente)."""
        try:
            target = parse_amount(str(self.amount.value), 10**15)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await self.table.play(interaction, Act.RAISE, target)


def raise_targets(hand: TimbaHand) -> list[tuple[str, str, int]]:
    """Las subidas con botón propio (mínimo, ½ bote y bote): sin repetir ni llegar a todo."""
    opts = hand.options()
    targets: list[tuple[str, str, int]] = []
    if not opts.can_raise:
        return targets
    half = hand.current_bet + (hand.pot + opts.to_call) // 2
    full = hand.current_bet + hand.pot + opts.to_call
    for name, emoji, value in (
        ("Subir a", "⬆️", opts.min_raise),
        ("½ bote:", "🌗", half),
        ("Bote:", "💰", full),
    ):
        value = max(opts.min_raise, min(value, opts.max_raise))
        if value < opts.max_raise and all(value != t for *_, t in targets):
            targets.append((name, emoji, value))
    return targets


class TableView(discord.ui.View):
    """Botones de la mesa: entre manos, sentarse y levantarse; en una mano, lo que puede hacer
    quien habla."""

    def __init__(self, table: Table) -> None:
        super().__init__(timeout=None)
        grey = discord.ButtonStyle.secondary
        hand = table.hand
        if table.phase is Phase.PLAYING and hand is not None and not hand.finished:
            opts = hand.options()
            if opts.to_call:
                label, emoji = f"Igualar {format_amount(opts.to_call)}", "📞"
            else:
                label, emoji = "Pasar", "✅"
            success = discord.ButtonStyle.success
            _button(self, label, emoji, success, table.call_click, 0, "call")
            primary = discord.ButtonStyle.primary
            for name, emoji_, value in raise_targets(hand):
                _button(
                    self,
                    f"{name} {format_amount(value)}",
                    emoji_,
                    primary,
                    table.raise_callback(value),
                    0,
                    f"raise:{name}",
                )
            if opts.can_raise:
                danger = discord.ButtonStyle.danger
                _button(
                    self,
                    f"Todo {format_amount(opts.max_raise)}",
                    "🔥",
                    danger,
                    table.raise_callback(opts.max_raise),
                    0,
                    "allin",
                )
            _button(self, "Retirarse", "🏳️", discord.ButtonStyle.danger, table.fold_click, 1, "fold")
            _button(
                self, "Cantidad", "🔢", grey, table.raise_modal, 1, "amount",
                disabled=not opts.can_raise,
            )  # fmt: skip
            _button(self, "Mis cartas", "🂠", grey, table.my_cards, 1, "cards")
        else:
            success = discord.ButtonStyle.success
            _button(self, "Listo", "✅", success, table.mark_ready, 2, "ready")
        _button(self, "Sentarse", "🪑", discord.ButtonStyle.primary, table.sit_click, 2, "sit")
        _button(self, "Levantarse", "🚪", grey, table.leave_click, 2, "leave")
        _button(self, "Reglas", "📋", grey, table.rules, 2, "rules")
        if (paint := tienda.skin_button(table.cog.bot, POKER_GAME, row=2)) is not None:
            self.add_item(paint)


# -- Mesa -----------------------------------------------------------------------------------------


class Table:
    """La timba de un canal: sus sillas, su mensaje y su bucle de manos.

    El dinero nunca vive aquí: se aparta y se paga siempre por la economía.
    """

    def __init__(
        self,
        cog: Timba,
        *,
        guild_id: int,
        channel: discord.abc.Messageable,
        big_blind: int,
        png: bytes,
        skin: dict[str, object] | None = None,
    ) -> None:
        self.cog = cog
        self.guild_id = guild_id
        self.channel = channel
        self.big_blind = big_blind
        self.small_blind = max(1, big_blind // 2)
        self.skin = skin
        self.png = png
        #: Imágenes pintadas por adelantado, por (mano, sus acciones): la mano real
        #: (que la mantiene viva, para que su `id` no se repita) y el PNG.
        self.painted: dict[PaintKey, tuple[TimbaHand, asyncio.Future[bytes | None]]] = {}
        self._painting: asyncio.Task[None] | None = None
        #: La próxima mano, repartida (sin enseñarla) y pintada durante la espera.
        self.next_deal: TimbaHand | None = None
        self._dealing: asyncio.Task[None] | None = None
        self.message: discord.Message | None = None
        self.phase = Phase.LOBBY
        self.chairs: list[Chair] = []
        self.button = 0
        self.hand: TimbaHand | None = None
        self.playing: list[Chair] = []
        self.hands = 0
        self.turn_deadline: float | None = None
        self.next_hand_at: float | None = None
        self.last_lines: list[str] = []
        self.task: asyncio.Task[None] | None = None
        self._lock = TimedLock("timba:Table._lock")
        self._edit_task: asyncio.Task[None] | None = None
        self._dirty = False
        self.seated = asyncio.Event()
        self.acted = asyncio.Event()
        #: Todos los sentados están listos: la mano empieza sin esperar.
        self.go = asyncio.Event()
        self.stopping = False
        self.sleeping = False
        self.opened_at = cog.wall_clock()
        #: La comisión que salió de lo que ganó cada uno en la última mano.
        self.rakes: dict[int, int] = {}

    # -- Cuentas ----------------------------------------------------------------------------

    @property
    def max_buy(self) -> int:
        """Lo máximo que uno se juega en una mano."""
        return self.big_blind * MAX_BUY_BB

    @property
    def min_buy(self) -> int:
        """Lo mínimo que hay que tener para sentarse."""
        return self.big_blind * MIN_BUY_BB

    def blinds_text(self) -> str:
        return (
            f"CIEGAS {format_amount(self.small_blind)}/{format_amount(self.big_blind)} · "
            f"MÁX. {format_amount(self.max_buy)} POR MANO"
        )

    def chair(self, user_id: int) -> Chair | None:
        return next((c for c in self.chairs if c.user.id == user_id), None)

    def name_of(self, user_id: int) -> str:
        chair = self.chair(user_id) or next((c for c in self.playing if c.user.id == user_id), None)
        return short_name(chair.user.display_name) if chair else "Alguien"

    # -- Vista -----------------------------------------------------------------------------

    def seat_views(self, hand: TimbaHand | None = None, *, reveal: bool = False) -> list[TimbaSeat]:
        """Los asientos de la mano en curso (o de `hand`) tal y como los pinta la mesa."""
        hand = hand or self.hand
        assert hand is not None
        last: dict[int, Event] = {e.user_id: e for e in hand.events}
        result = hand.result
        # Por id: la mano repartida por adelantado aún no es la de `playing`.
        users = {c.user.id: c.user for c in (*self.playing, *self.chairs)}
        views = []
        for i, player in enumerate(hand.players):
            badge = badge_for(last[player.user_id]) if player.user_id in last else None
            won = False
            hand_label = None
            show = reveal and result is not None and result.showdown and not player.folded
            if result is not None:
                diff = result.won.get(player.user_id, 0) - player.total
                won = result.won.get(player.user_id, 0) > 0
                if player.folded:
                    badge = ("fold", "RETIRADO")
                elif diff > 0:
                    badge = ("win", f"+{format_amount(diff)}")
                elif diff < 0:
                    badge = ("lose", f"-{format_amount(-diff)}")
                else:
                    badge = ("tie", "EMPATA")
                if show:
                    hand_label = result.values[player.user_id].rank.label.upper()
            views.append(
                TimbaSeat(
                    name=users[player.user_id].display_name,
                    stack=player.stack,
                    cards=player.hole,
                    reveal=show,
                    folded=player.folded,
                    bet=player.street,
                    turn=hand.to_act == i,
                    button=hand.button == i,
                    badge=badge,
                    hand=hand_label,
                    won=won and show,
                )
            )
        return views

    async def paint(self, hand: TimbaHand, *, reveal: bool = False) -> bytes:
        """PNG de la mesa de `hand` (su final, con `reveal`)."""
        views = self.seat_views(hand, reveal=reveal)
        state = timba_state(hand.visible_board, views, pot=hand.pot)
        meta = {**timba_meta(self.blinds_text()), "skin": self.skin}
        return await self.cog.renderer.timba(meta, state)

    async def render(self, *, reveal: bool = False) -> None:
        """Pinta la mesa de la mano en curso (o su final, con `reveal`).

        Si ya se pintó por adelantado (`prepare_turn`, `plan_deal`), se usa esa.
        """
        hand = self.hand
        assert hand is not None
        entry = self.painted.pop(paint_key(hand), None)
        self.painted.clear()  # las demás eran de lo que no pasó
        if entry is not None and (png := await entry[1]) is not None:
            self.png = png
            return
        self.png = await self.paint(hand, reveal=reveal)

    # -- Pintar por adelantado ---------------------------------------------------------

    def _queue(self, jobs: list[tuple[TimbaHand, TimbaHand, Callable[[], bool]]]) -> None:
        """Pinta en segundo plano, de una en una, cada `(mano real, cómo quedará, sigue valiendo)`.

        La imagen queda en `painted` para que `render` la coja si la mano real
        acaba así. Nunca se cortan a medias (`NodeScene` tendría que reiniciarse):
        antes de empezar cada una se mira si aún puede hacer falta y, si no, se salta.
        """
        loop = asyncio.get_running_loop()
        work: list[tuple[TimbaHand, Callable[[], bool], asyncio.Future[bytes | None]]] = []
        for real, ahead, needed in jobs:
            key = (id(real), tuple(ahead.events))
            if key in self.painted:
                continue
            future: asyncio.Future[bytes | None] = loop.create_future()
            self.painted[key] = (real, future)
            work.append((ahead, needed, future))
        if work:
            self._painting = asyncio.create_task(self._paint_queue(work, self._painting))

    async def _paint_queue(
        self,
        work: list[tuple[TimbaHand, Callable[[], bool], asyncio.Future[bytes | None]]],
        previous: asyncio.Task[None] | None,
    ) -> None:
        # Una cola detrás de otra: el pintor solo pinta una cosa a la vez.
        if previous is not None:
            await asyncio.gather(previous, return_exceptions=True)
        for ahead, needed, future in work:
            png = None
            if self.phase is not Phase.CLOSED and needed():
                try:
                    png = await self.paint(ahead, reveal=ahead.finished)
                except Exception:
                    logger.warning("No se pudo pintar la timba por adelantado", exc_info=True)
            if not future.done():
                future.set_result(png)

    def _leaving(self, user_id: int) -> bool:
        return any(c.user.id == user_id and c.leaving for c in self.playing)

    def prepare_turn(self) -> None:
        """Pinta la mesa tal y como quedará con cada botón de quien habla y si se duerme.

        La de dormirse va primero (es la que seguro hace falta si se acaba el
        tiempo); luego pasar o igualar, retirarse y cada subida con botón. La 🔢
        Cantidad no se puede saber.
        """
        hand = self.hand
        assert hand is not None and hand.current is not None
        user_id = hand.current.user_id
        base = tuple(hand.events)
        opts = hand.options()
        moves: list[tuple[Act, int] | None] = [None, (Act.CALL, 0), (Act.FOLD, 0)]
        moves += [(Act.RAISE, value) for *_, value in raise_targets(hand)]
        if opts.can_raise:
            moves.append((Act.RAISE, opts.max_raise))
        jobs: list[tuple[TimbaHand, TimbaHand, Callable[[], bool]]] = []
        for move in moves:
            ahead = copy.deepcopy(hand)
            try:
                if move is None:
                    ahead.timeout()
                else:
                    ahead.act(user_id, *move)
                # Como el bucle: quien se levanta se retira sin que se pinte su turno.
                while (nxt := ahead.current) is not None and self._leaving(nxt.user_id):
                    ahead.act(nxt.user_id, Act.FOLD)
            except TimbaError:
                continue
            after = tuple(ahead.events)

            def needed(after: tuple[Event, ...] = after) -> bool:
                return self.hand is hand and tuple(hand.events) in (base, after)

            jobs.append((hand, ahead, needed))
        self._queue(jobs)

    def deal_hand(self, stacks: list[tuple[int, int]]) -> TimbaHand:
        """Reparte una mano a `(user_id, lo que se juega)` con el botón que toca."""
        return TimbaHand.deal(
            stacks,
            button=self.button % len(stacks),
            small_blind=self.small_blind,
            big_blind=self.big_blind,
            rng=self.cog.rng,
        )

    def plan_deal(self) -> None:
        """Durante la espera entre manos, reparte ya la próxima (sin enseñarla) y la pinta.

        Se vuelve a hacer cada vez que se sienta o se levanta alguien. Si al empezar
        cambia quién juega o lo que se juega cada uno, se reparte de nuevo.
        """
        if self.phase is not Phase.LOBBY or self.next_hand_at is None or len(self.chairs) < 2:
            return
        self._dealing = asyncio.create_task(self._plan_deal(self.hands))

    async def _plan_deal(self, hands: int) -> None:
        try:
            stacks: list[tuple[int, int]] = []
            for chair in list(self.chairs):
                balance = await self.cog.economy.balance(self.guild_id, chair.user.id)
                if balance >= self.big_blind:
                    stacks.append((chair.user.id, min(balance, self.max_buy)))
            if len(stacks) < 2 or self.phase is not Phase.LOBBY or self.hands != hands:
                return
            hand = self.deal_hand(stacks)
            self.next_deal = hand

            def needed() -> bool:
                return self.next_deal is hand or self.hand is hand

            self._queue([(hand, hand, needed)])
        except Exception:
            logger.warning("No se pudo repartir la timba por adelantado", exc_info=True)

    def log_lines(self) -> list[str]:
        hand = self.hand
        if hand is None:
            return []
        lines = []
        for event in hand.events[-LOG_LIMIT:]:
            lines.append(
                f"-# {event.street.label}: {event_text(event, self.name_of(event.user_id))}"
            )
        return lines

    def play_embed(self) -> discord.Embed:
        """La mano en curso: quién habla, la mesa, el bote y lo último que ha pasado."""
        hand = self.hand
        assert hand is not None
        lines = []
        current = hand.current
        if current is not None and self.turn_deadline is not None:
            opts = hand.options()
            name = self.name_of(current.user_id)
            owe = f"iguala {format_amount(opts.to_call)}" if opts.to_call else "puede pasar"
            lines.append(
                f"### 🎯 Le toca a **{name}** <t:{math.ceil(self.turn_deadline)}:R> · {owe}"
            )
        board = " ".join(f"**{c}**" for c in hand.visible_board) or "—"
        lines.append(f"{hand.street.label} · mesa: {board} · 💰 bote **{format_amount(hand.pot)}**")
        lines.extend(self.log_lines())
        lines.append("-# 🂠 Mis cartas te enseña las tuyas en privado.")
        embed = discord.Embed(
            title=f"♣️ Timba · mano {self.hands}",
            description="\n".join(lines)[:4_000],
            color=COLOR_TURN,
        )
        embed.set_image(url=f"attachment://{TABLE_PNG}")
        embed.set_footer(text=self.footer())
        return embed

    def footer(self) -> str:
        return (
            f"Ciegas {self.small_blind}/{self.big_blind} · hasta {MAX_BUY_BB} ciegas por mano · "
            f"comisión {RAKE_PCT} % (tope {RAKE_CAP_BB} ciegas) al Estado"
        )

    def lobby_embed(self) -> discord.Embed:
        """Entre manos: la anterior, quién está sentado y cuándo empieza la siguiente."""
        lines = list(self.last_lines)
        if lines:
            lines.append("")
        if self.chairs:
            seated = ", ".join(
                f"{'✅ ' if c.ready else ''}{short_name(c.user.display_name)}"
                f"{' 🚪' if c.leaving else ''}"
                for c in self.chairs
            )
            lines.append(f"🪑 **Sentados ({len(self.chairs)}/{MAX_SEATS}):** {seated}")
        if len(self.chairs) >= 2 and self.next_hand_at is not None:
            ready = sum(c.ready for c in self.chairs)
            lines.append(f"### 🃏 Próxima mano <t:{math.ceil(self.next_hand_at)}:R>")
            lines.append(f"✅ Listos {ready}/{len(self.chairs)}: si estáis todos, empieza ya.")
        else:
            closes = math.ceil(self.opened_at + IDLE_SECONDS)
            lines.append(
                f"Hacen falta dos para jugar. Siéntate con 🪑 (necesitas "
                f"{format_amount(self.min_buy)}). La mesa se cierra sola <t:{closes}:R>."
            )
        embed = discord.Embed(
            title="♣️ Timba · Texas Hold'em sin límite",
            description="\n".join(lines)[:4_000],
            color=COLOR,
        )
        embed.set_image(url=f"attachment://{TABLE_PNG}")
        embed.set_footer(text=self.footer())
        return embed

    def table_file(self) -> discord.File:
        return discord.File(io.BytesIO(self.png), filename=TABLE_PNG)

    async def edit(self, **kwargs: Any) -> None:
        """Edita el mensaje de la mesa; un fallo de Discord no rompe nada."""
        if self.message is None:
            return
        try:
            await self.message.edit(**kwargs)
        except discord.HTTPException:
            logger.debug("No se pudo editar la timba", exc_info=True)

    def edit_soon(self) -> None:
        """Repinta el texto de la mesa entre manos sin esperar; agrupa los cambios seguidos."""
        self._dirty = True
        if self._edit_task is None or self._edit_task.done():
            self._edit_task = asyncio.create_task(self._flush())

    async def _flush(self) -> None:
        while self._dirty and self.phase is Phase.LOBBY:
            self._dirty = False
            await self.edit(embed=self.lobby_embed(), view=TableView(self))

    async def _wait_edits(self) -> None:
        if self._edit_task is not None:
            await asyncio.gather(self._edit_task, return_exceptions=True)

    async def publish(self) -> None:
        """Pinta la mano en curso y la pone en el mensaje con los botones de quien habla."""
        await self.render()
        await self.edit(
            embed=self.play_embed(), view=TableView(self), attachments=[self.table_file()]
        )

    # -- Sentarse y levantarse -------------------------------------------------------------

    async def sit(
        self, user: discord.abc.User, *, interaction: discord.Interaction | None = None
    ) -> str:
        """Sienta a alguien (juega desde la mano siguiente) y dice cómo.

        Raises:
            TimbaError: Sin sitio, ya sentado o sin saldo para la ciega mínima.
        """
        balance = await self.cog.economy.balance(self.guild_id, user.id)
        async with self._lock:
            if self.phase is Phase.CLOSED:
                raise TimbaError("La mesa está cerrada.")
            chair = self.chair(user.id)
            if chair is not None:
                if chair.leaving:
                    chair.leaving = False
                    return "🪑 Te quedas en la mesa."
                raise TimbaError("Ya estás sentado.")
            if len(self.chairs) >= MAX_SEATS:
                raise TimbaError(f"La mesa está llena ({MAX_SEATS} sitios).")
            if balance < self.min_buy:
                raise TimbaError(
                    f"Para sentarte hacen falta {format_amount(self.min_buy)} "
                    f"({MIN_BUY_BB} ciegas grandes) y tienes {format_amount(balance)}."
                )
            self.chairs.append(
                Chair(user=user, interaction=interaction, joined_at=self.cog.wall_clock())
            )
            self.seated.set()
            self._check_ready()
            self.plan_deal()
        playing = " Entras en la mano siguiente." if self.phase is Phase.PLAYING else ""
        return (
            f"🪑 Sentado. En cada mano te juegas tu saldo hasta {format_amount(self.max_buy)}; "
            f"lo que no gastes vuelve al acabar.{playing}"
        )

    async def sit_click(self, interaction: discord.Interaction) -> None:
        """🪑 Sentarse (mira el saldo: se acepta el clic antes)."""
        await ack(interaction)
        try:
            text = await self.sit(interaction.user, interaction=interaction)
        except TimbaError as error:
            await notify(interaction, str(error))
            return
        await notify(interaction, text)
        if self.phase is Phase.LOBBY:
            self.edit_soon()

    async def leave_click(self, interaction: discord.Interaction) -> None:
        """🚪 Levantarse: se va al acabar la mano (o ya, si no juega). Solo memoria."""
        chair = self.chair(interaction.user.id)
        if chair is None:
            await interaction.response.send_message("No estás sentado.", ephemeral=True)
            return
        in_hand = self.phase is Phase.PLAYING and chair in self.playing
        if in_hand:
            chair.leaving = True
            text = "🚪 Te levantas al acabar la mano; si te toca, te retiras."
            if self.hand is not None and self.hand.current is not None:
                if self.hand.current.user_id == chair.user.id:
                    self.acted.set()
        else:
            self.chairs.remove(chair)
            self._check_ready()
            self.plan_deal()
            text = "🚪 Te levantas de la timba."
        await interaction.response.send_message(text, ephemeral=True)
        if self.phase is Phase.LOBBY:
            self.edit_soon()

    def _check_ready(self) -> None:
        """Suelta la mano si hay dos o más sentados y todos están listos."""
        if len(self.chairs) >= 2 and all(c.ready for c in self.chairs):
            self.go.set()
        else:
            self.go.clear()

    async def mark_ready(self, interaction: discord.Interaction) -> None:
        """✅ Listo: si lo están todos los sentados, la mano empieza ya. Solo memoria."""
        chair = self.chair(interaction.user.id)
        if chair is None:
            await interaction.response.send_message(
                "Primero siéntate con 🪑 Sentarse.", ephemeral=True
            )
            return
        if self.phase is not Phase.LOBBY:
            await interaction.response.send_message("Ya hay una mano en juego.", ephemeral=True)
            return
        chair.ready = True
        self._check_ready()
        await interaction.response.defer()
        self.edit_soon()

    async def rules(self, interaction: discord.Interaction) -> None:
        """📋 Las reglas, en privado (solo memoria)."""
        embed = discord.Embed(
            title="📋 Timba · Texas Hold'em sin límite",
            description=(
                "Dos cartas para cada uno y cinco comunitarias (flop, turn y river). Gana "
                "quien haga la mejor mano de cinco con sus dos y las de la mesa, o el último "
                "que quede si los demás se retiran.\n\n"
                "**Ciegas:** la pequeña y la grande las ponen los dos que siguen al botón (a "
                "dos, el botón pone la pequeña). **Hablar:** pasar si no hay nada que igualar, "
                "igualar, subir (como mínimo lo que subió el último, y nunca menos de la ciega "
                "grande) o retirarse. Ir con todo vale siempre; si no llega a una subida "
                "entera, no deja volver a subir a quien ya había hablado. **Botes "
                "secundarios:** quien va con todo solo puede ganar lo que igualó.\n\n"
                f"**Dinero:** en cada mano te juegas tu saldo hasta {MAX_BUY_BB} ciegas grandes "
                f"({format_amount(self.max_buy)}); lo que no gastas vuelve. Sentarse pide "
                f"{format_amount(self.min_buy)}. La casa se queda el {RAKE_PCT} % de cada bote "
                f"que llega al flop (tope {RAKE_CAP_BB} ciegas) y se lo da a {TAX_COLLECTOR} "
                "como Impuesto sobre Actividades de Juego.\n\n"
                f"**Tiempo:** {TURN_SECONDS} s para hablar; si no, pasas o te retiras. A las "
                f"{MAX_TIMEOUTS} seguidas, te levantan."
            ),
            color=COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -- Jugar -----------------------------------------------------------------------------

    async def play(self, interaction: discord.Interaction, act: Act, amount: int = 0) -> None:
        """Una acción de quien habla. Solo cambia memoria: se contesta directamente."""
        hand = self.hand
        if self.phase is not Phase.PLAYING or hand is None or hand.finished:
            await interaction.response.send_message("No hay ninguna mano en juego.", ephemeral=True)
            return
        try:
            hand.act(interaction.user.id, act, amount)
        except TimbaError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        chair = self.chair(interaction.user.id)
        if chair is not None:
            chair.timeouts = 0
            chair.interaction = interaction
        await interaction.response.defer()
        self.acted.set()

    async def call_click(self, interaction: discord.Interaction) -> None:
        """✅ Pasar o 📞 Igualar."""
        await self.play(interaction, Act.CALL)

    async def fold_click(self, interaction: discord.Interaction) -> None:
        """🏳️ Retirarse."""
        await self.play(interaction, Act.FOLD)

    def raise_callback(self, target: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        """El callback de un botón de subir a `target`."""

        async def callback(interaction: discord.Interaction) -> None:
            await self.play(interaction, Act.RAISE, target)

        return callback

    async def raise_modal(self, interaction: discord.Interaction) -> None:
        """🔢 Cantidad: abre el formulario (tiene que ser la primera respuesta)."""
        hand = self.hand
        current = hand.current if hand is not None else None
        if current is None or current.user_id != interaction.user.id:
            await interaction.response.send_message("No es tu turno.", ephemeral=True)
            return
        await interaction.response.send_modal(RaiseModal(self))

    async def my_cards(self, interaction: discord.Interaction) -> None:
        """🂠 Tus cartas y lo que llevas, en privado (solo memoria)."""
        hand = self.hand
        if hand is None or hand.finished:
            await interaction.response.send_message("No hay ninguna mano en juego.", ephemeral=True)
            return
        try:
            player = hand.players[hand.index_of(interaction.user.id)]
        except TimbaError:
            await interaction.response.send_message(
                "No juegas esta mano: entras en la siguiente.", ephemeral=True
            )
            return
        cards = " ".join(f"**{c}**" for c in player.hole)
        seen = (*player.hole, *hand.visible_board)
        best = f" · llevas **{evaluate(seen).label.lower()}**" if len(seen) >= 5 else ""
        status = " (te has retirado)" if player.folded else ""
        await interaction.response.send_message(
            f"🂠 Tus cartas: {cards}{best}{status}. Te quedan {format_amount(player.stack)}.",
            ephemeral=True,
        )

    # -- Ciclo -----------------------------------------------------------------------------

    async def nap(self, seconds: float, *, wake: asyncio.Event | None = None) -> None:
        """Espera del bucle; el único sitio donde el apagado lo puede cortar."""
        if self.stopping:
            raise asyncio.CancelledError
        self.sleeping = True
        try:
            if wake is None:
                await self.cog.sleep(seconds)
                return
            waiters = {
                asyncio.ensure_future(self.cog.sleep(seconds)),
                asyncio.ensure_future(wake.wait()),
            }
            try:
                await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for waiter in waiters:
                    waiter.cancel()
                await asyncio.gather(*waiters, return_exceptions=True)
        finally:
            self.sleeping = False

    async def run(self) -> None:
        """Manos y más manos hasta que no quedan dos en `IDLE_SECONDS`."""
        try:
            while True:
                if len(self.chairs) < 2:
                    self.next_hand_at = None
                    self.seated.clear()
                    self.opened_at = self.cog.wall_clock()
                    self.edit_soon()
                    await self.nap(IDLE_SECONDS, wake=self.seated)
                    if len(self.chairs) < 2 and not self.seated.is_set():
                        await self.close()
                        return
                    continue
                self.next_hand_at = self.cog.wall_clock() + START_SECONDS
                self.edit_soon()
                self.plan_deal()
                await self.nap(START_SECONDS, wake=self.go)
                if len(self.chairs) >= 2:
                    await self.play_hand()
        except asyncio.CancelledError:
            if not self.stopping:
                raise
        except Exception:
            logger.exception("La timba ha fallado; se devuelve lo apartado")
            await self.shutdown()
        finally:
            self.cog.release(self)

    async def _reserve(self) -> list[Chair]:
        """Aparta de cada sentado lo que se juega en la mano; levanta a quien no llega."""
        playing: list[Chair] = []
        for chair in list(self.chairs):
            balance = await self.cog.economy.balance(self.guild_id, chair.user.id)
            if balance < self.big_blind:
                self.chairs.remove(chair)
                self.last_lines.append(
                    f"-# 🚪 {short_name(chair.user.display_name)} se levanta: no le llega ni "
                    "para la ciega."
                )
                continue
            stack = min(balance, self.max_buy)
            try:
                await self.cog.economy.place_bet(
                    self.guild_id, chair.user.id, game=GAME, stake=stack
                )
            except InsufficientFundsError:
                continue
            chair.stack = stack
            chair.hand_timeouts = 0
            playing.append(chair)
        return playing

    async def _refund(self, chairs: list[Chair]) -> None:
        for chair in chairs:
            if chair.stack:
                try:
                    await self.cog.economy.pay_winnings(
                        self.guild_id, chair.user.id, game=GAME, amount=chair.stack
                    )
                except BalanceLimitError:
                    logger.warning("No se pudo devolver lo apartado en la timba.")
                chair.stack = 0

    async def play_hand(self) -> None:
        """Una mano entera: apartar, repartir, turnos, pagar, enseñar y apuntar."""
        self.last_lines = []
        for chair in self.chairs:
            chair.ready = False
        self.go.clear()
        playing = await self._reserve()
        if len(playing) < 2:
            await self._refund(playing)
            return
        self.hands += 1
        self.playing = playing
        stacks = [(c.user.id, c.stack) for c in playing]
        planned, self.next_deal = self.next_deal, None
        if (
            planned is not None
            and [(p.user_id, p.stack + p.total) for p in planned.players] == stacks
            and planned.button == self.button % len(playing)
        ):
            self.hand = planned  # repartida y pintada durante la espera
        else:
            self.hand = self.deal_hand(stacks)
        self.button += 1
        self.phase = Phase.PLAYING
        # Una edición de entre manos aún en vuelo (la del último ✅ Listo) llegaría a
        # Discord después de la mano y taparía sus botones: quien habla se dormiría.
        await self._wait_edits()
        hand = self.hand
        while not hand.finished:
            current = hand.current
            assert current is not None
            chair = next(c for c in playing if c.user.id == current.user_id)
            if chair.leaving:
                hand.act(current.user_id, Act.FOLD)
                continue
            self.acted.clear()
            self.turn_deadline = self.cog.wall_clock() + TURN_SECONDS
            await self.publish()
            self.prepare_turn()
            # Hasta el plazo que se enseña: pintar y subir la mesa ya ha gastado parte.
            await self.nap(max(0.0, self.turn_deadline - self.cog.wall_clock()), wake=self.acted)
            if self.phase is Phase.CLOSED:
                return  # se ha cerrado a mitad de mano: `shutdown` ya lo devolvió todo
            if hand.finished or hand.current is not current:
                continue
            if chair.leaving:
                hand.act(current.user_id, Act.FOLD)
                continue
            if not self.acted.is_set():
                hand.timeout()
                chair.timeouts += 1
                chair.hand_timeouts += 1
        self.turn_deadline = None
        settlements = await self.settle()
        await self.render(reveal=True)
        await self.finish_lines(settlements)
        self.phase = Phase.LOBBY
        for chair in list(self.chairs):
            if chair.leaving or chair.timeouts >= MAX_TIMEOUTS:
                self.chairs.remove(chair)
                if chair.timeouts >= MAX_TIMEOUTS:
                    self.last_lines.append(
                        f"-# 💤 {short_name(chair.user.display_name)} se ha dormido "
                        f"{MAX_TIMEOUTS} veces seguidas: le levantamos de la mesa."
                    )
        await self.edit(
            embed=self.lobby_embed(), view=TableView(self), attachments=[self.table_file()]
        )
        await self.track(settlements)

    async def settle(self) -> dict[int, BetSettlement]:
        """Devuelve a cada uno lo que no gastó más lo que gana, y la comisión al Estado."""
        hand = self.hand
        assert hand is not None and hand.result is not None
        result = hand.result
        payouts = {p.user_id: p.stack + result.won.get(p.user_id, 0) for p in hand.players}
        rakes: dict[int, int] = {}
        for pot in result.pots:
            if not pot.rake:
                continue
            share, odd = divmod(pot.rake, len(pot.winners))
            for k, uid in enumerate(pot.winners):
                rakes[uid] = rakes.get(uid, 0) + share + (1 if k < odd else 0)
        try:
            settlements = await self.cog.economy.settle_timba(
                self.guild_id, game=GAME, payouts=payouts, rakes=rakes
            )
        except BalanceLimitError:
            logger.warning("Premio de timba por encima del saldo máximo; se devuelve lo apartado.")
            await self._refund(self.playing)
            return {}
        for chair in self.playing:
            chair.stack = 0
        self.rakes = rakes
        return settlements

    async def finish_lines(self, settlements: dict[int, BetSettlement]) -> None:
        """El resultado de la mano para la mesa entre manos."""
        hand = self.hand
        assert hand is not None and hand.result is not None
        result = hand.result
        board = " ".join(f"**{c}**" for c in hand.board[: max(hand.street.value, 0)][:5])
        lines = [f"Mano {self.hands} · mesa: {board or '—'} · bote **{format_amount(hand.pot)}**"]
        lines.append(f"-# {QUIPS[self.hands % len(QUIPS)]}")
        chairs = {c.user.id: c for c in self.playing}
        for player in sorted(hand.players, key=lambda p: -(result.won.get(p.user_id, 0) - p.total)):
            chair = chairs[player.user_id]
            name = short_name(chair.user.display_name)
            diff = result.won.get(player.user_id, 0) - player.total
            value = result.values.get(player.user_id)
            shown = (
                f" · {value.label.lower()}"
                if result.showdown and value and not player.folded
                else ""
            )
            if player.folded:
                lines.append(f"🏳️ **{name}** se retira · -{format_amount(player.total)}")
            elif diff > 0:
                lines.append(f"✅ **{name}**{shown} → **+{format_amount(diff)}**")
            elif diff < 0:
                lines.append(f"❌ **{name}**{shown} → -{format_amount(-diff)}")
            else:
                lines.append(f"🤝 **{name}**{shown} → ni gana ni pierde")
        if result.rake:
            lines.append(
                f"-# 🐶 {TAX_COLLECTOR} se lleva {format_amount(result.rake)} de comisión (IAJ)."
            )
        taxed = []
        for uid, settlement in settlements.items():
            if settlement.tax_delta:
                sign = "retiene" if settlement.tax_delta > 0 else "devuelve"
                taxed.append(
                    f"{sign} {format_amount(abs(settlement.tax_delta))} a {self.name_of(uid)}"
                )
        if taxed:
            lines.append(f"-# 🐶 IRPF del juego: {TAX_COLLECTOR} {', '.join(taxed)}.")
        for chair in self.playing[:HINTS_LIMIT]:
            uid = chair.user.id
            settlement = settlements.get(uid)
            player = hand.players[hand.index_of(uid)]
            net = result.won.get(uid, 0) - player.total
            if hint := await renta.hint(
                self.cog.bot,
                self.guild_id,
                uid,
                bet_moment(
                    stake=max(player.total, 1),
                    net=net,
                    balance_after=settlement.balance if settlement else 0,
                ),
            ):
                lines.append(hint)
        self.last_lines = lines

    async def track(self, settlements: dict[int, BetSettlement]) -> None:
        """Renta, logros, estadísticas del casino, porras y anuncios, tras enseñar la mano."""
        hand = self.hand
        assert hand is not None and hand.result is not None
        result = hand.result
        when = datetime.fromtimestamp(self.cog.wall_clock(), TIMEZONE)
        rakes = self.rakes
        busted = tuple(
            p.user_id for p in hand.players if p.stack + result.won.get(p.user_id, 0) == 0
        )
        for chair in self.playing:
            uid = chair.user.id
            player = hand.players[hand.index_of(uid)]
            settlement = settlements.get(uid)
            balance = settlement.balance if settlement else 0
            tax = settlement.tax_delta if settlement else 0
            net = result.won.get(uid, 0) - player.total
            delta = timba_stats(
                hand,
                uid,
                stack=player.stack + player.total,
                rake=rakes.get(uid, 0),
                busted=busted,
                timeouts=chair.hand_timeouts,
                when=when,
            )
            if player.total:
                delta.merge(
                    casino_stats(stake=player.total, net=net, balance_after=balance, tax_delta=tax)
                )
            if chair.interaction is not None:
                await renta.remind(self.cog.bot, chair.interaction)
            await logros.casino_play(
                self.cog.bot, self.guild_id, chair.user, self.channel, delta, net=net
            )
            if player.total:
                await apuestas.record(
                    self.cog.bot,
                    self.guild_id,
                    chair.user,
                    game=GAME,
                    stake=player.total,
                    net=net,
                    balance_after=balance,
                    tax=tax,
                    details=(("started", int(chair.joined_at)),),
                )
        biggest = max(result.won.values(), default=0)
        if biggest >= SHOUT_POT_BB * self.big_blind and isinstance(
            self.channel, discord.abc.Messageable
        ):
            winner = max(result.won, key=lambda uid: result.won[uid])
            chair = next(c for c in self.playing if c.user.id == winner)
            try:
                await self.channel.send(
                    f"📣 ♣️ ¡{chair.user.mention} se lleva un bote de "
                    f"**{format_amount(biggest)}** en la timba!",
                    allowed_mentions=discord.AllowedMentions(users=[chair.user], everyone=False),
                )
            except discord.HTTPException:
                logger.debug("No se pudo anunciar un bote de la timba", exc_info=True)

    async def close(self) -> None:
        """Mesa sin gente: se cierra y el mensaje pierde los botones."""
        self.phase = Phase.CLOSED
        embed = self.lobby_embed()
        embed.title = "♣️ Timba · mesa cerrada"
        embed.description = (
            "\n".join(self.last_lines)
            + "\n\nNo hay timba sin gente. `poker timba` la vuelve a abrir."
        ).strip()
        await self.edit(embed=embed, view=None)

    async def shutdown(self) -> None:
        """Cierre ordenado: una mano a medias se anula y se devuelve lo apartado."""
        async with self._lock:
            phase = self.phase
            self.phase = Phase.CLOSED
        if phase is Phase.PLAYING:
            await self._refund(self.playing)
        embed = discord.Embed(
            title="♣️ Timba · mesa cerrada",
            description="El casino cierra. Si había una mano a medias, se ha devuelto todo.",
            color=COLOR,
        )
        await self.edit(embed=embed, view=None)

    async def move(self) -> None:
        """Vuelve a publicar la mesa al final del canal (entre manos) y borra la de arriba.

        Raises:
            TimbaError: Si hay una mano en marcha.
        """
        if self.phase is not Phase.LOBBY:
            raise TimbaError("Hay una mano en marcha: en cuanto acabe, pide la mesa otra vez.")
        old = self.message
        self.message = await self.channel.send(
            embed=self.lobby_embed(),
            view=TableView(self),
            file=self.table_file(),
            allowed_mentions=NO_MENTIONS,
        )
        if old is not None:
            try:
                await old.delete()
            except discord.HTTPException:
                logger.debug("No se pudo borrar la timba vieja", exc_info=True)


# -- Cog ------------------------------------------------------------------------------------------


class Timba(commands.Cog, name="Timba"):
    """La timba: Texas Hold'em sin límite entre miembros, una mesa por canal."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        renderer: PokerScene | None = None,
        casino_channel_ids: CasinoChannelIds = frozenset(),
        rng: Any = None,
        wall_clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.renderer = renderer or PokerScene()
        self.casino_channel_ids = casino_channel_ids
        # `secrets` usa el azar del sistema operativo: no se puede predecir.
        self.rng = rng or secrets.SystemRandom()
        self.wall_clock = wall_clock
        self.sleep = sleep
        self.tables: dict[int, Table] = {}
        self._opening: set[int] = set()

    def release(self, table: Table) -> None:
        """Libera el canal cuando su mesa se cierra."""
        channel_id = getattr(table.channel, "id", 0)
        if self.tables.get(channel_id) is table:
            del self.tables[channel_id]

    async def cog_unload(self) -> None:
        """Al apagar, anula las manos a medias (devuelve lo apartado) y cierra el pintor."""
        for table in list(self.tables.values()):
            table.stopping = True
            if table.task is not None:
                if table.sleeping:
                    table.task.cancel()
                await asyncio.gather(table.task, return_exceptions=True)
            try:
                await table.shutdown()
            except Exception:
                logger.exception("No se pudo cerrar una timba al apagar")
        self.tables.clear()
        await self.renderer.close()

    async def ensure_table(
        self, guild_id: int, channel: discord.abc.Messageable, opener_id: int, big_blind: int
    ) -> tuple[Table | None, bool]:
        """La timba del canal: la que hay o una nueva con esa ciega grande.

        Returns:
            La mesa (`None` si otra se está abriendo) y si es nueva.
        """
        channel_id = getattr(channel, "id", 0)
        table = self.tables.get(channel_id)
        if table is not None:
            return table, False
        if channel_id in self._opening:
            return None, False
        self._opening.add(channel_id)
        try:
            skin = tienda.skin_params(self.bot, guild_id, opener_id, POKER_GAME)
            table = Table(
                self, guild_id=guild_id, channel=channel, big_blind=big_blind, png=b"", skin=skin
            )
            table.png = await self.renderer.timba(
                {**timba_meta(table.blinds_text()), "skin": skin}, timba_state((), [], pot=0)
            )
            table.message = await channel.send(
                embed=table.lobby_embed(),
                view=TableView(table),
                file=table.table_file(),
                allowed_mentions=NO_MENTIONS,
            )
        finally:
            self._opening.discard(channel_id)
        self.tables[channel_id] = table
        self.start(table)
        return table, True

    def start(self, table: Table) -> None:
        """Arranca el bucle de una mesa recién abierta."""
        channel_id = getattr(table.channel, "id", 0)
        table.task = asyncio.create_task(table.run(), name=f"timba-{channel_id}")

    async def open_and_sit(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        blind_text: str | None,
        confirm: Callable[[str], Awaitable[None]],
        send_error: Callable[[str], Awaitable[None]],
        interaction: discord.Interaction | None = None,
    ) -> None:
        """`poker` en modo timba: abre (o baja) la mesa del canal y te sienta.

        Lo llama `bot.cogs.poker` (`/poker timba`), que es el único comando: la
        timba no tiene comando propio, para no gastar otro slash command.
        """
        if guild is None or not isinstance(channel, discord.abc.Messageable):
            await send_error("La timba solo se juega dentro de un servidor.")
            return
        if error := casino_channel_error(self.casino_channel_ids, channel, "La timba"):
            await send_error(error)
            return
        big_blind = DEFAULT_BIG_BLIND
        if blind_text is not None:
            try:
                big_blind = parse_amount(blind_text, 10**15)
            except ValueError as error:
                await send_error(str(error))
                return
            if big_blind < 2:
                await send_error("La ciega grande tiene que ser de al menos 2 Y$.")
                return
        table, new = await self.ensure_table(guild.id, channel, user.id, big_blind)
        if table is None:
            await send_error("Se está abriendo la timba en este canal; un segundo.")
            return
        if not new and blind_text is not None and big_blind != table.big_blind:
            await send_error(
                f"Esta timba va con ciegas {table.small_blind}/{table.big_blind}: para otras, "
                "otro canal o esperar a que se cierre."
            )
            return
        if not new:
            try:
                await table.move()
            except TimbaError:
                pass
        try:
            text = await table.sit(user, interaction=interaction)
        except TimbaError as error:
            await send_error(str(error))
            return
        if table.phase is Phase.LOBBY:
            table.edit_soon()
        await confirm(text)


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(Timba(bot, economy=bot.economy, casino_channel_ids=bot.casino_channels))
