"""Bacará (Punto y Banca): `bacara`, una mesa por canal con un zapato compartido.

`/bacara` o `.bacara` abre la mesa del canal (o la baja al final del canal si
ya estaba abierta más arriba). Todos los del canal juegan las mismas manos con
el mismo zapato de 8 barajas, y ven la misma carretera grande: la cuadrícula de
bolas rojas y azules que cuenta las rachas del zapato.

**Una mano** pasa por tres momentos en el mismo mensaje:

1. **Apuestas**: la mesa con la última mano y la carretera. Botones: 🔵 Jugador
   (1:1), 🟢 Empate (8:1) y 🔴 Banca (0,95:1, mínimo 20 Y$) ponen tu ficha en ese
   lado (se puede pulsar varias veces y en varios lados); 🪙 Ficha la cambia;
   🔁 Repetir pone lo mismo que en la mano anterior y te marca listo; ⏫ Doblar
   dobla lo que llevas; 🃏 ¡Cartas! te marca listo. Con la primera apuesta
   empieza la cuenta atrás (`BET_SECONDS`); si todos los que han apostado están
   listos, se reparte ya. Quien juega solo reparte con 🔁 Repetir en un clic.
2. **Reparto**: el GIF con las cartas volando del zapato, el giro y el apretón
   de la tercera carta (`bot.services.bacara_render`).
3. **Resultado**: la mesa vuelve a abrirse con la mano ya jugada: quién ganó,
   cuánto se lleva cada uno, la mordida de Hacienda y lo que dicen las mascotas.

**Pintar antes de apostar.** Una mano no depende de las apuestas: las cartas
salen del zapato por la regla fija de la tercera carta. Así que en cuanto se
abre la ronda, la mano siguiente se reparte del zapato (sin enseñarla ni
guardarla en ningún sitio visible) y Node la pinta en segundo plano. Al cerrar
las apuestas el GIF ya está hecho. Repartirla al abrir o al cerrar da las mismas
probabilidades, y nadie la ve antes de que se cierren las apuestas. Si la mesa
se cierra sin apuestas, esa mano espera en el zapato a la siguiente sesión.

**El zapato vive en memoria**, por canal: un reinicio del bot abre zapato nuevo
y empieza la carretera de cero. Si la mesa pasa `IDLE_SECONDS` sin apuestas, se
cierra; el zapato sigue donde estaba para la próxima vez.

Dinero (`EconomyService`): cada ficha se cobra al ponerla (`place_bet`) y el
premio se paga al cerrar las apuestas (`pay_winnings`, con 0 si pierde), antes
de enseñar el GIF: así un apagado a mitad de la animación no deja a nadie sin
cobrar. Tratamiento fiscal: juego, como el resto del casino (premios del juego
como ganancia patrimonial de la base general, art. 33.1 LIRPF, y pérdidas que
solo compensan ganancias de juego, art. 33.5.d LIRPF): entra en la retención
diaria del casino y en la renta semanal. La comisión del 5 % de la Banca no es
un impuesto: es el margen de la casa, como el cero de la ruleta, y no va al
Estado. Si el bot se apaga con las apuestas abiertas, se devuelve lo apostado.

Solo se juega en los canales del casino que elige `garito` (sin elegir, los de `CASINO_CHANNEL_IDS`)
y en sus hilos. Permisos del bot en el canal: enviar mensajes, insertar enlaces, adjuntar archivos y
añadir reacciones (para confirmar `.bacara 500 banca`). No necesita intents especiales.
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
import random
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs import shop as tienda
from bot.cogs.casino import casino_channel_error, insufficient_text
from bot.services.achievements import baccarat_stats, casino_stats
from bot.services.bacara import (
    BANKER_MIN_STAKE,
    GAME,
    SIDES,
    Coup,
    Result,
    Shoe,
    Side,
    chop_run,
    payout,
    road_stats,
)
from bot.services.bacara_render import WIN_TITLES, Header, Media
from bot.services.bacara_scene import BacaraScene
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
from bot.services.taxes import TAX_COLLECTOR
from bot.services.telemetry import TimedLock
from bot.utils.interactions import ack, notify
from bot.utils.responder import ContextResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

DEFAULT_STAKE = 100
#: Cuenta atrás desde la primera apuesta de la mano. Si todos los que han
#: apostado están listos, se reparte antes.
BET_SECONDS = 20
#: Sin ninguna apuesta durante este tiempo, la mesa se cierra.
IDLE_SECONDS = 10 * 60
#: Margen tras el GIF antes de enseñar el resultado (lo que tarda en cargar).
REVEAL_MARGIN_SECONDS = 1.5
#: Apuestas y resultados que se listan por nombre; el resto se resume.
LIST_LIMIT = 10
#: Coletilla de la mano jugada, según quién gana.
COUP_LINES = {
    Side.BANKER: (
        "La Banca gana.",
        "Gana la Banca. Botín sonríe desde el palco.",
        "La Banca se lleva la mano.",
        "Gana la Banca: un rescate, pero al revés.",
    ),
    Side.PLAYER: (
        "Gana el Jugador.",
        "Gana el pueblo, que dirían en Galapagar.",
        "El Jugador se lleva la mano.",
        "¡Toma mangó! Gana el Jugador.",
    ),
    Side.TIE: (
        "Empate.",
        "Empate. Ni sí ni no, sino todo lo contrario.",
        "Tablas: nadie se lleva nada.",
    ),
}
#: Personas cuya mascota puede hablar en el resultado (para no llenar la mesa).
HINTS_LIMIT = 4
#: Racha de la carretera a partir de la cual la mesa lo avisa.
STREAK_NUDGE = 3
#: Manos alternas a partir de las que la mesa avisa del ping-pong.
CHOP_NUDGE = 4

TABLE_PNG = "mesa.png"
HAND_GIF = "mano.gif"

COLOR_OPEN = discord.Color.from_rgb(22, 110, 70)
COLOR_DEALING = discord.Color.from_rgb(212, 160, 40)
SIDE_COLORS = {
    Side.PLAYER: discord.Color.from_rgb(52, 120, 230),
    Side.BANKER: discord.Color.from_rgb(214, 52, 60),
    Side.TIE: discord.Color.from_rgb(40, 176, 96),
}
NO_MENTIONS = discord.AllowedMentions.none()


class BacaraError(Exception):
    """Una apuesta que no se puede hacer (mesa cerrada, mínimo de la Banca…)."""


class Phase(Enum):
    """Momento de la mano."""

    OPEN = "open"
    DEALING = "dealing"
    CLOSED = "closed"


@dataclass(slots=True, eq=False)
class Seat:
    """Lo que alguien lleva apostado en una mano.

    Attributes:
        bets: Lo puesto en cada lado.
        interaction: La última interacción con la que apostó (botón o slash),
            para el aviso de la Renta; `None` si solo apostó por texto.
        placed_at: Cuándo puso la primera ficha (las porras no cuentan las
            anteriores a su cierre).
        ready: Si ha pulsado 🃏 ¡Cartas! o 🔁 Repetir.
    """

    user: discord.abc.User
    bets: dict[Side, int] = field(default_factory=dict)
    interaction: discord.Interaction | None = None
    placed_at: float = 0.0
    ready: bool = False
    prize: int = 0
    settlement: BetSettlement | None = None
    hint: str | None = None

    @property
    def stake(self) -> int:
        """Total apostado en la mano."""
        return sum(self.bets.values())

    @property
    def net(self) -> int:
        """Ganado menos apostado."""
        return self.prize - self.stake

    def bets_text(self) -> str:
        """`🔴 200 · 🟢 50`, en el orden Jugador, Empate, Banca."""
        return " · ".join(
            f"{side.emoji} {format_amount(self.bets[side])}" for side in Side if side in self.bets
        )


@dataclass(slots=True)
class ShoeState:
    """El zapato de un canal: sobrevive a que la mesa se cierre por inactividad.

    Attributes:
        results: Resultados del zapato en curso (la carretera).
        last: La última mano jugada (la que enseña la mesa mientras se apuesta).
        pending: La mano siguiente, ya repartida del zapato pero sin jugar.
        hands: Manos jugadas por cada miembro en este zapato.
    """

    shoe: Shoe
    results: list[Result] = field(default_factory=list)
    last: Coup | None = None
    pending: Coup | None = None
    hands: dict[int, int] = field(default_factory=dict)
    #: El rótulo de la última mano jugada (su zapato puede no ser ya el de `shoe`).
    last_header: Header | None = None

    def header(self, coup: Coup | None = None) -> Header:
        """El rótulo de la mesa: con `coup` (del zapato en curso), el de esa mano; si
        no, el de la última jugada."""
        if coup is not None:
            return Header(self.shoe.number, coup.number, coup.left)
        if self.last_header is not None:
            return self.last_header
        return Header(self.shoe.number, 0, self.shoe.left)

    def take(self) -> Coup:
        """La mano siguiente: la que esperaba o una nueva (con zapato nuevo si toca)."""
        if self.pending is None:
            if self.shoe.finished:
                self.shoe = Shoe.open(number=self.shoe.number + 1)
            self.pending = self.shoe.deal()
        return self.pending

    def before(self, coup: Coup) -> list[Result]:
        """La carretera antes de `coup` (vacía si abre zapato)."""
        return [] if coup.number == 1 else list(self.results)

    def commit(self, coup: Coup) -> None:
        """Apunta la mano jugada en la carretera."""
        if coup.number == 1:
            self.results = []
            self.hands = {}
        self.results.append(Result.of(coup))
        self.last = coup
        self.last_header = self.header(coup)
        self.pending = None


@dataclass(slots=True)
class Round:
    """Una mano ya jugada: lo que enseña la mesa mientras se apuesta la siguiente."""

    coup: Coup
    seats: list[Seat]
    before: list[Result]


def short_name(name: str) -> str:
    """Nombre recortado para las listas."""
    return name if len(name) <= 16 else name[:15] + "…"


def tax_summary(seats: list[Seat]) -> str | None:
    """Subtexto con lo que retiene o devuelve Hacienda a cada uno en la mano."""
    taken, given = [], []
    for seat in seats:
        settlement = seat.settlement
        if settlement is None or not settlement.tax_delta:
            continue
        text = f"{format_amount(abs(settlement.tax_delta))} a {short_name(seat.user.display_name)}"
        (taken if settlement.tax_delta > 0 else given).append(text)
    parts = []
    if taken:
        parts.append("se lleva " + ", ".join(taken))
    if given:
        parts.append("devuelve " + ", ".join(given))
    return f"-# 🐶 {TAX_COLLECTOR} {' y '.join(parts)}." if parts else None


def score_text(coup: Coup) -> str:
    """`7 a 5`, con el ganador primero."""
    p, b = coup.player_total, coup.banker_total
    return f"{b} a {p}" if coup.winner is Side.BANKER else f"{p} a {b}"


def coup_line(coup: Coup) -> str:
    """`🔴 **Gana la Banca 7 a 5** · natural` para el resultado."""
    title = WIN_TITLES[coup.winner].capitalize()
    extra = []
    if coup.natural:
        extra.append("natural")
    if coup.cards == 6:
        extra.append("seis cartas")
    tail = f" · {', '.join(extra)}" if extra else ""
    return f"{coup.winner.emoji} **{title} {score_text(coup)}**{tail}"


def seat_result_line(seat: Seat) -> str:
    """`✅ Ana · 🔴 200 → +190`."""
    name = short_name(seat.user.display_name)
    if seat.net > 0:
        return f"✅ **{name}** · {seat.bets_text()} → **+{format_amount(seat.net)}**"
    if seat.net < 0:
        return f"❌ **{name}** · {seat.bets_text()} → -{format_amount(-seat.net)}"
    return f"↩️ **{name}** · {seat.bets_text()} → te lo devuelven"


def road_nudge(results: list[Result]) -> str | None:
    """Lo que avisa la carretera: la racha en curso o el ping-pong. No predice nada."""
    stats = road_stats(results)
    if stats.streak >= STREAK_NUDGE and stats.streak_side is not None:
        side = stats.streak_side
        who = "la Banca" if side is Side.BANKER else "el Jugador"
        beast = "🐉" if stats.streak >= 5 else "🔥"
        return f"{beast} {who.capitalize()} lleva **{stats.streak}** seguidas."
    chop = chop_run(results)
    if chop >= CHOP_NUDGE:
        return f"🏓 Ping-pong: **{chop}** manos alternando Banca y Jugador."
    return None


# -- Vistas ---------------------------------------------------------------------------------------


class FichaModal(discord.ui.Modal, title="🪙 Tu ficha"):
    """Pide la cantidad que pone cada clic en un lado."""

    amount: discord.ui.TextInput = discord.ui.TextInput(
        label="Cantidad (500, 2k, mitad, all…)", placeholder="100", max_length=12
    )

    def __init__(self, table: Table) -> None:
        super().__init__()
        self.table = table

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Guarda la ficha (leer el saldo va a la base de datos: se acepta antes)."""
        await ack(interaction)
        cog = self.table.cog
        balance = await cog.economy.balance(self.table.guild_id, interaction.user.id)
        try:
            stake = parse_amount(str(self.amount.value), balance)
        except ValueError as error:
            await notify(interaction, str(error))
            return
        cog.set_ficha(self.table.guild_id, interaction.user.id, stake)
        await notify(interaction, f"🪙 Tu ficha: **{format_amount(stake)}** por clic.")


class TableView(discord.ui.View):
    """Botones de la mesa. Los pulsa cualquiera: la mesa es de todos."""

    def __init__(self, table: Table, *, disabled: bool = False) -> None:
        super().__init__(timeout=None)
        self.table = table
        for side, style in (
            (Side.PLAYER, discord.ButtonStyle.primary),
            (Side.TIE, discord.ButtonStyle.success),
            (Side.BANKER, discord.ButtonStyle.danger),
        ):
            self._add(
                f"{side.label} {side.pays}",
                side.emoji,
                style,
                table.bet_callback(side),
                0,
                f"apuesta:{side.key}",
            )
        self._add("Ficha", "🪙", discord.ButtonStyle.secondary, table.set_ficha, 1, "ficha")
        self._add("Repetir", "🔁", discord.ButtonStyle.secondary, table.repeat, 1, "repetir")
        self._add("Doblar", "⏫", discord.ButtonStyle.secondary, table.double, 1, "doblar")
        self._add("¡Cartas!", "🃏", discord.ButtonStyle.success, table.mark_ready, 1, "listo")
        # El 🎨: el tapete de cada uno, que se ve en las mesas que abra.
        if (paint := tienda.skin_button(table.cog.bot, GAME, row=1)) is not None:
            self.add_item(paint)
        if disabled:
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True

    def _add(
        self,
        label: str,
        emoji: str,
        style: discord.ButtonStyle,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        row: int,
        custom_id: str,
    ) -> None:
        button: discord.ui.Button = discord.ui.Button(
            label=label, emoji=emoji, style=style, row=row, custom_id=f"{GAME}:{custom_id}"
        )
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)


# -- Mesa -----------------------------------------------------------------------------------------


class Table:
    """La mesa de un canal: sus apuestas, su mensaje y su bucle de manos.

    El dinero nunca vive aquí: se cobra y se paga siempre por la economía.
    """

    def __init__(
        self,
        cog: Bacara,
        *,
        guild_id: int,
        channel: discord.abc.Messageable,
        state: ShoeState,
        png: bytes,
        skin: dict[str, object] | None = None,
        skin_label: str | None = None,
    ) -> None:
        self.cog = cog
        #: El tapete de quien abre la mesa (es compartida): vale hasta que se cierra.
        self.skin = skin
        #: Su nombre para el pie (`🟥 Tapete granate`), si no es el de serie.
        self.skin_label = skin_label
        self.guild_id = guild_id
        self.channel = channel
        self.state = state
        #: La imagen de la mesa quieta (la última mano o la mesa vacía).
        self.png = png
        self.message: discord.Message | None = None
        self.phase = Phase.OPEN
        self.seats: dict[int, Seat] = {}
        #: Cuándo se reparte (se fija con la primera apuesta).
        self.deadline: float | None = None
        self.opened_at = cog.wall_clock()
        self.last_round: Round | None = None
        #: La mano siguiente ya pintada (`prepare`).
        self.prepared: asyncio.Task[tuple[Coup, Media]] | None = None
        self.task: asyncio.Task[None] | None = None
        self._lock = TimedLock("bacara:Table._lock")
        self._edit_task: asyncio.Task[None] | None = None
        self._dirty = False
        #: Se activa con la primera apuesta y cuando todos están listos.
        self.first_bet = asyncio.Event()
        self.go = asyncio.Event()
        #: Quién dio el último ¡Cartas! (logro del crupier).
        self.starter: int | None = None
        self.view = TableView(self)
        #: Los mismos botones apagados, mientras se reparte. Uno por mesa: discord.py
        #: guarda cada vista sin tiempo límite hasta que se para.
        self.closed_view = TableView(self, disabled=True)
        self.stopping = False
        self.sleeping = False

    # -- Vista -----------------------------------------------------------------------------

    def bets_block(self) -> str:
        """Lista de apuestas de la mano en curso, con tope de líneas."""
        seats = list(self.seats.values())
        lines = [
            f"{'✅' if s.ready else '🎟️'} {short_name(s.user.display_name)} · {s.bets_text()}"
            for s in seats[:LIST_LIMIT]
        ]
        if len(seats) > LIST_LIMIT:
            lines.append(f"… y {len(seats) - LIST_LIMIT} más")
        return "\n".join(lines)

    def _round_block(self) -> list[str]:
        """La mano anterior: resultado, quién gana y quién pierde, Hacienda y mascotas."""
        played = self.last_round
        if played is None:
            return []
        coup = played.coup
        # Una coletilla por mano, no por redibujo: el embed se repinta varias veces.
        quip = random.Random(coup.number).choice(COUP_LINES[coup.winner])
        lines = [f"Mano {coup.number}: {coup_line(coup)}", f"-# {quip}"]
        seats = sorted(played.seats, key=lambda s: -s.net)
        for seat in seats[:LIST_LIMIT]:
            lines.append(seat_result_line(seat))
            if seat.hint:
                lines.append(seat.hint)
        if len(seats) > LIST_LIMIT:
            lines.append(f"… y {len(seats) - LIST_LIMIT} más")
        if note := tax_summary(seats):
            lines.append(note)
        return lines

    def open_embed(self) -> discord.Embed:
        """La mesa abierta: la mano anterior, la carretera y las apuestas de esta."""
        state = self.state
        lines = self._round_block()
        if lines:
            lines.append("")
        if nudge := road_nudge(state.results):
            lines.append(nudge)
        if state.last is not None and state.last.last:
            lines.append("✂️ Salió la carta de corte: esta mano abre zapato nuevo.")
        if self.seats:
            assert self.deadline is not None
            ready = sum(s.ready for s in self.seats.values())
            lines.append(f"### 🃏 Cartas <t:{math.ceil(self.deadline)}:R>")
            lines.append(f"✅ Listos {ready}/{len(self.seats)}: si estáis todos, se reparte ya.")
        else:
            closes = math.ceil(self.opened_at + IDLE_SECONDS)
            lines.append(
                "Pulsa un lado para poner tu ficha. Sin apuestas, la mesa se cierra "
                f"<t:{closes}:R>."
            )
        winner = self.last_round.coup.winner if self.last_round else None
        embed = discord.Embed(
            title="🍸 Bacará · Punto y Banca",
            description="\n".join(lines)[:4_000],
            color=SIDE_COLORS[winner] if winner else COLOR_OPEN,
        )
        if self.seats:
            total = sum(s.stake for s in self.seats.values())
            embed.add_field(
                name=f"Apuestas ({len(self.seats)}) · {format_amount(total)}",
                value=self.bets_block()[:1_024],
                inline=False,
            )
        embed.set_image(url=f"attachment://{TABLE_PNG}")
        embed.set_footer(
            text=(
                f"Jugador 1:1 · Empate 8:1 · Banca 0,95:1 (mín. {BANKER_MIN_STAKE} Y$) · "
                "🔁 Repetir pone lo de la mano anterior y reparte si estás solo"
                + (f" · Tapete: {self.skin_label}" if self.skin_label else "")
            )
        )
        return embed

    def dealing_embed(self, coup: Coup) -> discord.Embed:
        """¡No va más!: el GIF y las apuestas, sin destripar nada."""
        state = self.state
        embed = discord.Embed(
            title="🍸 ¡No va más!",
            description=f"Mano {coup.number} del zapato {state.shoe.number}.",
            color=COLOR_DEALING,
        )
        if self.seats:
            embed.add_field(
                name=f"Apuestas ({len(self.seats)})", value=self.bets_block()[:1_024], inline=False
            )
        embed.set_image(url=f"attachment://{HAND_GIF}")
        return embed

    async def edit(self, **kwargs: Any) -> None:
        """Edita el mensaje de la mesa; un fallo de Discord no rompe nada."""
        if self.message is None:
            return
        try:
            await self.message.edit(**kwargs)
        except discord.HTTPException:
            logger.debug("No se pudo editar la mesa de bacará", exc_info=True)

    def edit_soon(self) -> None:
        """Repinta la mesa abierta sin esperar; agrupa los cambios que llegan seguidos.

        Los botones nunca editan el mensaje ellos mismos: si un clic tardío lo
        hiciera mientras se reparte, taparía el GIF con la mesa abierta. Aquí
        solo se repinta mientras la mano siga abierta, y el reparto espera a
        que acabe la edición en curso antes de poner la suya.
        """
        self._dirty = True
        if self._edit_task is None or self._edit_task.done():
            self._edit_task = asyncio.create_task(self._flush())

    async def _flush(self) -> None:
        while self._dirty and self.phase is Phase.OPEN:
            self._dirty = False
            await self.edit(embed=self.open_embed(), view=self.view)

    def table_file(self) -> discord.File:
        """La imagen de la mesa quieta para adjuntarla."""
        return discord.File(io.BytesIO(self.png), filename=TABLE_PNG)

    # -- Apuestas --------------------------------------------------------------------------

    async def place(
        self,
        user: discord.abc.User,
        side: Side,
        stake: int,
        *,
        interaction: discord.Interaction | None = None,
    ) -> Seat:
        """Cobra una ficha y la pone en `side`.

        Raises:
            InsufficientFundsError: Si no le llega.
            BacaraError: Si la mano ya se reparte o no llega al mínimo de la Banca.
        """
        if stake < 1:
            raise BacaraError("La ficha tiene que ser de al menos 1 Y$.")
        async with self._lock:
            if self.phase is not Phase.OPEN:
                raise BacaraError("¡No va más! Espera a la mano siguiente.")
            seat = self.seats.get(user.id)
            on_banker = seat.bets.get(Side.BANKER, 0) if seat else 0
            if side is Side.BANKER and on_banker + stake < BANKER_MIN_STAKE:
                raise BacaraError(
                    f"La Banca pide al menos {format_amount(BANKER_MIN_STAKE)}: "
                    "con menos, su 5 % de comisión no llega a 1 Y$."
                )
            await self.cog.economy.place_bet(self.guild_id, user.id, game=GAME, stake=stake)
            if seat is None:
                seat = Seat(user=user, placed_at=self.cog.wall_clock())
                self.seats[user.id] = seat
            seat.bets[side] = seat.bets.get(side, 0) + stake
            if interaction is not None:
                seat.interaction = interaction
            if self.deadline is None:
                self.deadline = self.cog.wall_clock() + BET_SECONDS
                self.first_bet.set()
            return seat

    def _ready(self, seat: Seat) -> bool:
        """Marca listo a alguien; si ya lo están todos, se reparte. Devuelve si se reparte."""
        seat.ready = True
        if all(s.ready for s in self.seats.values()):
            self.starter = seat.user.id
            self.go.set()
            return True
        return False

    async def _check_balance(self, interaction: discord.Interaction, needed: int) -> bool:
        balance = await self.cog.economy.balance(self.guild_id, interaction.user.id)
        if balance <= 0 or needed > balance:
            await notify(interaction, insufficient_text(balance, needed))
            return False
        return True

    async def _place_many(
        self, interaction: discord.Interaction, bets: dict[Side, int]
    ) -> Seat | None:
        """Pone varias fichas seguidas (Repetir y Doblar) y avisa si algo falla."""
        if not await self._check_balance(interaction, sum(bets.values())):
            return None
        seat: Seat | None = None
        try:
            for side, stake in bets.items():
                seat = await self.place(interaction.user, side, stake, interaction=interaction)
        except InsufficientFundsError as error:
            await notify(interaction, insufficient_text(error.balance, sum(bets.values())))
        except BacaraError as error:
            await notify(interaction, str(error))
        return seat

    def bet_callback(self, side: Side) -> Callable[[discord.Interaction], Awaitable[None]]:
        """El callback del botón de un lado: pone tu ficha en él."""

        async def callback(interaction: discord.Interaction) -> None:
            if self.phase is not Phase.OPEN:
                await interaction.response.send_message(
                    "¡No va más! Espera a la mano siguiente.", ephemeral=True
                )
                return
            stake = self.cog.ficha(self.guild_id, interaction.user.id)
            # Saldo y cobro van a la base de datos: se acepta el clic antes.
            await ack(interaction)
            if not await self._check_balance(interaction, stake):
                return
            try:
                await self.place(interaction.user, side, stake, interaction=interaction)
            except InsufficientFundsError as error:
                await notify(interaction, insufficient_text(error.balance, stake))
                return
            except BacaraError as error:
                await notify(interaction, str(error))
                return
            self.edit_soon()
            await renta.remind(self.cog.bot, interaction)

        return callback

    async def set_ficha(self, interaction: discord.Interaction) -> None:
        """🪙 Ficha: abre el formulario (tiene que ser la primera respuesta)."""
        await interaction.response.send_modal(FichaModal(self))

    async def repeat(self, interaction: discord.Interaction) -> None:
        """🔁 Repetir: lo mismo que en tu mano anterior, y listo."""
        if self.phase is not Phase.OPEN:
            await interaction.response.send_message(
                "¡No va más! Espera a la mano siguiente.", ephemeral=True
            )
            return
        seat = self.seats.get(interaction.user.id)
        if seat is not None:
            # Ya ha apostado en esta mano: Repetir hace de ¡Cartas!.
            await interaction.response.defer()
            self._ready(seat)
            self.edit_soon()
            return
        last = self.cog.last_bets.get((self.guild_id, interaction.user.id))
        if not last:
            await interaction.response.send_message(
                "No tienes mano anterior que repetir: pulsa un lado para apostar.",
                ephemeral=True,
            )
            return
        await ack(interaction)
        seat = await self._place_many(interaction, dict(last))
        if seat is None:
            return
        self._ready(seat)
        self.edit_soon()
        await renta.remind(self.cog.bot, interaction)

    async def double(self, interaction: discord.Interaction) -> None:
        """⏫ Doblar: vuelve a poner todo lo que llevas en esta mano."""
        seat = self.seats.get(interaction.user.id)
        if self.phase is not Phase.OPEN or seat is None or not seat.bets:
            await interaction.response.send_message(
                "Primero apuesta en esta mano: ⏫ Doblar dobla lo que llevas.", ephemeral=True
            )
            return
        await ack(interaction)
        if await self._place_many(interaction, dict(seat.bets)) is not None:
            self.edit_soon()
            await renta.remind(self.cog.bot, interaction)

    async def mark_ready(self, interaction: discord.Interaction) -> None:
        """🃏 ¡Cartas!: te marca listo; si lo están todos, se reparte.

        Solo mira y cambia memoria, así que contesta directamente.
        """
        seat = self.seats.get(interaction.user.id)
        if self.phase is not Phase.OPEN or self.go.is_set():
            await interaction.response.send_message("Ya se está repartiendo.", ephemeral=True)
            return
        if seat is None:
            await interaction.response.send_message(
                "Primero apuesta: pulsa 🔵 Jugador, 🟢 Empate o 🔴 Banca.", ephemeral=True
            )
            return
        await interaction.response.defer()
        self._ready(seat)
        self.edit_soon()

    # -- Ciclo -----------------------------------------------------------------------------

    async def nap(self, seconds: float, *, wake: asyncio.Event | None = None) -> None:
        """Espera del bucle; el único sitio donde el apagado lo puede cortar.

        Con `wake`, la espera acaba antes si ese evento se activa.
        """
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

    def open_round(self, *, prepare: bool = True) -> None:
        """Abre las apuestas de una mano nueva y, con `prepare`, empieza a pintarla."""
        self.seats = {}
        self.deadline = None
        self.starter = None
        self.first_bet.clear()
        self.go.clear()
        self.opened_at = self.cog.wall_clock()
        self.phase = Phase.OPEN
        if prepare:
            self.prepare()

    def prepare(self) -> None:
        """Reparte la mano siguiente del zapato y la pinta en segundo plano.

        Es justo: la mano no depende de las apuestas (la tercera carta sigue
        una regla fija) y no se enseña ni se guarda en ningún sitio visible
        hasta que se cierran las apuestas.
        """
        if self.cog.ahead:
            self.prepared = asyncio.create_task(self._prepare(), name="bacara-dibujo")
        else:
            self.prepared = None

    async def _prepare(self) -> tuple[Coup, Media]:
        state = self.state
        coup = state.take()
        burned = len(state.shoe.burned) if coup.number == 1 else 0
        media = await self.cog.renderer.coup(
            coup,
            state.before(coup),
            state.header(coup),
            burned=burned,
            seed=self.cog.rng.getrandbits(32),
            skin=self.skin,
        )
        return coup, media

    async def run(self) -> None:
        """Manos y más manos hasta que nadie apuesta en `IDLE_SECONDS`."""
        try:
            while True:
                await self.nap(IDLE_SECONDS, wake=self.first_bet)
                if not self.seats:
                    await self.close()
                    return
                assert self.deadline is not None
                await self.nap(max(0.0, self.deadline - self.cog.wall_clock()), wake=self.go)
                await self.deal()
        except asyncio.CancelledError:
            if not self.stopping:
                raise
        except Exception:
            logger.exception("La mesa de bacará ha fallado; se devuelve lo apostado")
            await self.shutdown()
        finally:
            self.cog.release(self)

    async def deal(self) -> None:
        """Cierra las apuestas, paga, enseña el GIF y vuelve a abrir la mesa."""
        async with self._lock:
            self.phase = Phase.DEALING
            seats = list(self.seats.values())
        if self._edit_task is not None:
            await asyncio.gather(self._edit_task, return_exceptions=True)
        state = self.state
        coup: Coup | None = None
        media: Media | None = None
        if self.prepared is not None:
            if not self.prepared.done():
                # Que se note ya que no va más: botones apagados mientras se acaba de pintar.
                await self.edit(view=self.closed_view)
            try:
                coup, media = await self.prepared
            except Exception:
                logger.exception("No se pudo pintar la mano de bacará por adelantado")
        if coup is None:
            coup = state.take()
        before = state.before(coup)
        if media is None:
            burned = len(state.shoe.burned) if coup.number == 1 else 0
            media = await self.cog.renderer.coup(
                coup,
                before,
                state.header(coup),
                burned=burned,
                seed=self.cog.rng.getrandbits(32),
                skin=self.skin,
            )
        state.commit(coup)
        # La mano siguiente se pinta ya, mientras se ve el GIF de esta: así quien
        # vuelve a apostar nada más ver el resultado tampoco espera.
        self.prepare()
        await self.pay(coup, seats)
        if media.gif is not None:
            await self.edit(
                embed=self.dealing_embed(coup),
                view=self.closed_view,
                attachments=[discord.File(io.BytesIO(media.gif), filename=HAND_GIF)],
            )
            await self.nap(media.seconds + REVEAL_MARGIN_SECONDS)
        self.png = media.png
        for seat in seats[:HINTS_LIMIT]:
            seat.hint = await renta.hint(
                self.cog.bot,
                self.guild_id,
                seat.user.id,
                bet_moment(
                    stake=seat.stake,
                    net=seat.net,
                    balance_after=seat.settlement.balance if seat.settlement else 0,
                ),
            )
        self.last_round = Round(coup=coup, seats=seats, before=before)
        starter = self.starter
        self.open_round(prepare=False)
        await self.edit(embed=self.open_embed(), view=self.view, attachments=[self.table_file()])
        await self.track(coup, seats, before, starter)

    async def pay(self, coup: Coup, seats: list[Seat]) -> None:
        """Paga a cada uno lo que le devuelven sus lados (0 si pierde todo)."""
        winner = coup.winner
        for seat in seats:
            seat.prize = sum(payout(side, stake, winner) for side, stake in seat.bets.items())
            try:
                seat.settlement = await self.cog.economy.pay_winnings(
                    self.guild_id, seat.user.id, game=GAME, amount=seat.prize
                )
            except BalanceLimitError:
                logger.warning("Premio de bacará por encima del saldo máximo; no se paga.")
                seat.prize = 0

    async def track(
        self, coup: Coup, seats: list[Seat], before: list[Result], starter: int | None
    ) -> None:
        """Renta, logros, estadísticas del casino y porras de cada uno, tras enseñar la mano."""
        state = self.state
        when = datetime.fromtimestamp(self.cog.wall_clock(), TIMEZONE)
        winner = coup.winner
        mains = [
            next(iter(s.bets)) if len(s.bets) == 1 else None for s in seats
        ]  # el único lado de cada uno, si solo puso en uno
        for seat, single in zip(seats, mains, strict=True):
            user_id = seat.user.id
            state.hands[user_id] = state.hands.get(user_id, 0) + 1
            self.cog.last_bets[(self.guild_id, user_id)] = dict(seat.bets)
            streak = self.cog.streaks.get((self.guild_id, user_id), 0)
            if seat.net > 0:
                streak += 1
            elif seat.net < 0:
                streak = 0
            self.cog.streaks[(self.guild_id, user_id)] = streak
            same = sum(1 for m in mains if m is single) if single else 0
            opposite = (
                sum(1 for m in mains if m is not None and m is not single and m is not Side.TIE)
                if single in (Side.BANKER, Side.PLAYER)
                else 0
            )
            alone_win = single is winner and single is not Side.TIE and same == 1 and opposite >= 2
            herd = len(seats) >= 3 and single is not None and same == len(seats)
            herd_loss = herd and seat.net < 0
            settlement = seat.settlement
            balance = settlement.balance if settlement else 0
            tax = settlement.tax_delta if settlement else 0
            delta = baccarat_stats(
                coup,
                bets=seat.bets,
                prize=seat.prize,
                before=before,
                players=len(seats),
                alone_win=alone_win,
                herd_loss=herd_loss,
                starter=user_id == starter,
                shoe_hands=state.hands[user_id],
                win_streak=streak if seat.net > 0 else 0,
                when=when,
            )
            delta.merge(
                casino_stats(stake=seat.stake, net=seat.net, balance_after=balance, tax_delta=tax)
            )
            if seat.interaction is not None:
                await renta.remind(self.cog.bot, seat.interaction)
            await logros.casino_play(
                self.cog.bot, self.guild_id, seat.user, self.channel, delta, net=seat.net
            )
            natural = any(coup.natural_of(side) is not None for side in seat.bets if side is winner)
            await apuestas.record(
                self.cog.bot,
                self.guild_id,
                seat.user,
                game=GAME,
                stake=seat.stake,
                net=seat.net,
                balance_after=balance,
                tax=tax,
                details=(
                    ("started", int(seat.placed_at)),
                    ("natural", int(natural)),
                    ("banca", int(Side.BANKER in seat.bets)),
                ),
            )

    async def close(self) -> None:
        """Mesa sin apuestas: se cierra y el mensaje pierde los botones."""
        self.phase = Phase.CLOSED
        if self.prepared is not None:
            self.prepared.cancel()
        embed = self.open_embed()
        embed.title = "🍸 Bacará · mesa cerrada"
        embed.description = (
            "\n".join(self._round_block())
            + "\n\nNadie apuesta: el crupier recoge. `bacara` la vuelve a abrir con el mismo "
            "zapato."
        ).strip()
        embed.clear_fields()
        embed.set_footer(text=None)
        await self.edit(embed=embed, view=None)
        self.view.stop()
        self.closed_view.stop()

    async def shutdown(self) -> None:
        """Cierre ordenado: con las apuestas abiertas, se devuelve lo apostado.

        Si ya se estaba repartiendo, los premios ya están pagados (se pagan antes
        de enseñar el GIF) y no hay nada que devolver.
        """
        async with self._lock:
            if self.phase is Phase.OPEN:
                for seat in self.seats.values():
                    try:
                        await self.cog.economy.pay_winnings(
                            self.guild_id, seat.user.id, game=GAME, amount=seat.stake
                        )
                    except BalanceLimitError:
                        logger.warning("No se pudo devolver una apuesta de bacará.")
                self.seats.clear()
            self.phase = Phase.CLOSED
        embed = discord.Embed(
            title="🍸 Bacará · mesa cerrada",
            description="El casino cierra. Se devuelve lo apostado en la mano abierta.",
            color=COLOR_OPEN,
        )
        await self.edit(embed=embed, view=None)
        self.view.stop()
        self.closed_view.stop()

    async def move(self) -> None:
        """Vuelve a publicar la mesa al final del canal y borra la de arriba.

        Raises:
            BacaraError: Si se está repartiendo (no se puede mover a mitad de mano).
        """
        async with self._lock:
            if self.phase is not Phase.OPEN:
                raise BacaraError("Se está repartiendo. En cuanto acabe la mano, la tienes abajo.")
            old = self.message
            self.message = await self.channel.send(
                embed=self.open_embed(),
                view=self.view,
                file=self.table_file(),
                allowed_mentions=NO_MENTIONS,
            )
        if old is not None:
            try:
                await old.delete()
            except discord.HTTPException:
                logger.debug("No se pudo borrar la mesa de bacará vieja", exc_info=True)


# -- Cog ------------------------------------------------------------------------------------------


class Bacara(commands.Cog, name="Bacará"):
    """El bacará del casino: una mesa por canal con zapato y carretera compartidos."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        renderer: BacaraScene | None = None,
        casino_channel_ids: CasinoChannelIds = frozenset(),
        rng: Any = None,
        wall_clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        ahead: bool = True,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.renderer = renderer or BacaraScene()
        self.casino_channel_ids = casino_channel_ids
        # `secrets` usa el azar del sistema operativo: no se puede predecir.
        self.rng = rng or secrets.SystemRandom()
        self.wall_clock = wall_clock
        self.sleep = sleep
        #: Si se pinta la mano siguiente mientras se apuesta. Las pruebas que
        #: cambian el dibujo por un doble lo apagan.
        self.ahead = ahead
        #: Mesa abierta de cada canal.
        self.tables: dict[int, Table] = {}
        #: Zapato de cada canal (sigue aunque la mesa se cierre). Uno por canal
        #: en el que se ha jugado; se pierde al reiniciar.
        self.shoes: dict[int, ShoeState] = {}
        #: Canales en los que se está abriendo una mesa (para no abrir dos).
        self._opening: set[int] = set()
        # Ficha, última apuesta y racha por (servidor, miembro). Crecen como mucho
        # hasta el número de miembros que han jugado; se pierden al reiniciar.
        self._fichas: dict[tuple[int, int], int] = {}
        self.last_bets: dict[tuple[int, int], dict[Side, int]] = {}
        self.streaks: dict[tuple[int, int], int] = {}

    def ficha(self, guild_id: int, user_id: int) -> int:
        """Ficha recordada de un miembro."""
        return self._fichas.get((guild_id, user_id), DEFAULT_STAKE)

    def set_ficha(self, guild_id: int, user_id: int, stake: int) -> None:
        """Recuerda la ficha de un miembro."""
        self._fichas[(guild_id, user_id)] = max(1, stake)

    def release(self, table: Table) -> None:
        """Libera el canal cuando su mesa se cierra."""
        channel_id = getattr(table.channel, "id", 0)
        if self.tables.get(channel_id) is table:
            del self.tables[channel_id]

    async def cog_unload(self) -> None:
        """Al apagar, devuelve lo apostado en las mesas abiertas."""
        for table in list(self.tables.values()):
            table.stopping = True
            if table.prepared is not None and table.phase is Phase.OPEN:
                table.prepared.cancel()
            if table.task is not None:
                if table.sleeping:
                    table.task.cancel()
                await asyncio.gather(table.task, return_exceptions=True)
            try:
                await table.shutdown()
            except Exception:
                logger.exception("No se pudo cerrar una mesa de bacará al apagar")
        self.tables.clear()
        await self.renderer.close()

    # -- Abrir y apostar -----------------------------------------------------------------------

    def shoe_for(self, channel_id: int) -> ShoeState:
        """El zapato del canal (uno nuevo si nunca se ha jugado aquí)."""
        state = self.shoes.get(channel_id)
        if state is None:
            state = ShoeState(shoe=Shoe.open())
            self.shoes[channel_id] = state
        return state

    async def ensure_table(
        self, guild_id: int, channel: discord.abc.Messageable, opener_id: int = 0
    ) -> tuple[Table | None, bool]:
        """La mesa del canal: la que hay o una nueva que se publica ya.

        Una mesa nueva lleva el tapete de quien la abre (`opener_id`).

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
        table = None
        try:
            state = self.shoe_for(channel_id)
            skin = tienda.skin_params(self.bot, guild_id, opener_id, GAME)
            worn = tienda.skin_of(self.bot, guild_id, opener_id, GAME)
            png = await self.renderer.board(state.results, state.header(), state.last, skin=skin)
            table = Table(
                self,
                guild_id=guild_id,
                channel=channel,
                state=state,
                png=png,
                skin=skin,
                skin_label=worn.label if worn else None,
            )
            table.open_round()
            table.message = await channel.send(
                embed=table.open_embed(),
                view=table.view,
                file=table.table_file(),
                allowed_mentions=NO_MENTIONS,
            )
        except Exception:
            if table is not None and table.prepared is not None:
                table.prepared.cancel()
            raise
        finally:
            self._opening.discard(channel_id)
        self.tables[channel_id] = table
        self.start(table)
        return table, True

    def start(self, table: Table) -> None:
        """Arranca el bucle de una mesa recién abierta."""
        channel_id = getattr(table.channel, "id", 0)
        table.task = asyncio.create_task(table.run(), name=f"bacara-{channel_id}")

    async def _bacara_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        side_text: str | None,
        confirm: Callable[[str], Awaitable[None]],
        send_error: Callable[[str], Awaitable[None]],
        interaction: discord.Interaction | None = None,
    ) -> None:
        """Lógica de `/bacara` y `.bacara`: abre (o baja) la mesa y, si hay lado, apuesta."""
        if guild is None or not isinstance(channel, discord.abc.Messageable):
            await send_error("El bacará solo se juega dentro de un servidor.")
            return
        if error := casino_channel_error(self.casino_channel_ids, channel, "El bacará"):
            await send_error(error)
            return
        side: Side | None = None
        if side_text is not None:
            side = SIDES.get(side_text.strip().lower().replace("á", "a"))
            if side is None:
                aliases = {"b": Side.BANKER, "j": Side.PLAYER, "p": Side.PLAYER, "e": Side.TIE}
                aliases |= {"punto": Side.PLAYER, "tablas": Side.TIE}
                side = aliases.get(side_text.strip().lower())
            if side is None:
                await send_error("Lado no válido: `jugador`, `empate` o `banca`.")
                return
        stake: int | None = None
        if amount_text is not None:
            balance = await self.economy.balance(guild.id, user.id)
            try:
                stake = parse_amount(amount_text, balance)
            except ValueError as error:
                await send_error(str(error))
                return
            if balance <= 0 or stake > balance:
                await send_error(insufficient_text(balance, stake))
                return
            self.set_ficha(guild.id, user.id, stake)
        table, new = await self.ensure_table(guild.id, channel, user.id)
        if table is None:
            await send_error("Se está abriendo la mesa en este canal; un segundo.")
            return
        if not new and side is None:
            try:
                await table.move()
            except BacaraError as error:
                await send_error(str(error))
                return
        if side is None:
            if stake is not None:
                await confirm(f"🪙 Tu ficha: **{format_amount(stake)}** por clic.")
            else:
                await confirm("")
            return
        amount = stake if stake is not None else self.ficha(guild.id, user.id)
        try:
            await table.place(user, side, amount, interaction=interaction)
        except InsufficientFundsError as error:
            await send_error(insufficient_text(error.balance, amount))
            return
        except BacaraError as error:
            await send_error(str(error))
            return
        table.edit_soon()
        await confirm(
            f"{side.emoji} **{format_amount(amount)}** al **{side.label}** "
            f"(paga {side.pays}). Pulsa 🃏 ¡Cartas! en la mesa cuando quieras."
        )

    @app_commands.command(
        name="bacara",
        description="Bacará (Punto y Banca): abre la mesa del canal o apuesta en ella.",
    )
    @app_commands.describe(
        cantidad="Ficha: 500, 2k, mitad, all… (por defecto la tuya o 100)",
        lado="A qué apuestas (si no, solo abre la mesa)",
    )
    @app_commands.choices(
        lado=[app_commands.Choice(name=f"{s.label} ({s.pays})", value=s.key) for s in Side]
    )
    @app_commands.guild_only()
    async def bacara(
        self,
        interaction: discord.Interaction,
        cantidad: str | None = None,
        lado: str | None = None,
    ) -> None:
        """Abre la mesa del canal, la baja al final o apuesta en ella.

        Solo en los canales del casino que elige `garito` (sin elegir, los de `CASINO_CHANNEL_IDS`)
        y en sus hilos. Cobra cada ficha al ponerla y paga al repartir.
        """
        # Abrir la mesa dibuja la imagen y publica el mensaje: puede pasar de 3 s.
        await interaction.response.defer(ephemeral=True, thinking=True)

        async def confirm(text: str) -> None:
            await interaction.followup.send(
                text or "🍸 Mesa abierta: abajo del todo.", ephemeral=True
            )

        async def send_error(text: str) -> None:
            await interaction.followup.send(text, ephemeral=True)

        await self._bacara_impl(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            side_text=lado,
            confirm=confirm,
            send_error=send_error,
            interaction=interaction,
        )
        await renta.remind(self.bot, interaction)

    @commands.command(name="bacara")
    @commands.guild_only()
    async def bacara_text(
        self, ctx: commands.Context, cantidad: str | None = None, lado: str | None = None
    ) -> None:
        """Versión de texto: `.bacara`, `.bacara 500`, `.bacara 500 banca`, `.bacara 200 e`."""

        async def confirm(text: str) -> None:
            if not text:
                return
            # Sin mensajes privados en comandos de texto: una reacción basta.
            try:
                await ctx.message.add_reaction("🍸")
            except discord.HTTPException:
                await ctx.send(text, allowed_mentions=NO_MENTIONS)

        await self._bacara_impl(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            amount_text=cantidad,
            side_text=lado,
            confirm=confirm,
            send_error=ContextResponder(ctx).send_error,
        )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(Bacara(bot, economy=bot.economy, casino_channel_ids=bot.casino_channels))
