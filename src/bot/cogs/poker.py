"""Póker: `poker`, el único comando de los dos pókers del casino, y la mesa contra la banca.

`/poker [modo] [cantidad]` tiene dos modos: ♠️ **contra la banca** (Casino
Hold'em, esta mesa) y ♣️ **timba** entre jugadores (Texas Hold'em, el cog
`bot.cogs.timba`, sin comando propio para no gastar otro slash command). Sin
modo, el bot pregunta con un botón por modo (`ModePicker`), como `/lista` con la
prioridad. `.poker banca 500`, `.poker timba 50` o `.poker` hacen lo mismo con
texto.

**Contra la banca.** Abre la mesa del canal (o la baja al final si ya estaba
abierta más arriba); con una cantidad además fija tu ante y te sienta. Hasta
`MAX_SEATS` jugadores juegan la misma mano contra la misma banca: cada uno con
sus dos cartas y las mismas cinco comunitarias. Reglas y pagos en
`bot.services.poker`.

**Una mano** pasa por cuatro momentos en el mismo mensaje:

1. **Apuestas**: 🪑 Sentarse cobra tu ante (y la apuesta del 💎 Bote si lo
   juegas) y te da sitio; 🪙 Ante lo cambia y 💎 Bote lo enciende o lo apaga
   para las manos siguientes (se recuerda). Con el primero que se sienta empieza
   la cuenta atrás (`BET_SECONDS`); si todos los sentados pulsan 🃏 ¡Cartas!, se
   reparte ya. Quien juega solo reparte en cuanto pulsa ¡Cartas!.
2. **Reparto**: el GIF con las cartas de cada asiento y el flop.
3. **Decidir** (`DECIDE_SECONDS`): cada uno pulsa ✅ Ir (pone el doble del ante)
   o 🏳️ Retirarse. Quien no decide a tiempo se retira: ir pondría dinero que no
   ha decidido poner. En cuanto deciden todos, se sigue.
4. **Final**: el GIF con el turn, el river, la banca y el resultado de cada
   asiento; después la mesa vuelve a abrirse con lo que ha pasado, los retos
   cumplidos, las subidas de rango y lo que dicen las mascotas.

**El club** (`bot.services.poker_club`, botón 🏆 Club): cada mano con un ante de
al menos una tirada da experiencia para el rango, cuenta para la racha de días
y para los tres retos del día, que se cobran solos al cumplirse. El rango abre
las mesas de ante alto: sentarse con un ante por encima del tope de tu rango no
se puede.

**El 💎 Bote**: apuesta aparte de `BONUS_STAKE`, contra la casa y el bote común
del servidor, que se resuelve con tus siete cartas aunque te retires (ver
`poker.bonus`).

**Pintar.** El reparto depende de cuántos se sientan y sus nombres, y el final
de lo que decide cada uno, así que se pintan al cerrar las apuestas y al cerrar
las decisiones; mientras, la mesa enseña «¡No va más!» con los botones apagados.

Dinero (`EconomyService`): el ante y la apuesta del bote se cobran al sentarse y
la de ir al ir (`place_bet`); todo se paga al final de la mano con `pay_poker`
(premio de la mano, parte fija del bote y lo que se lleve del bote común), que
ajusta el IRPF del día. Tratamiento fiscal: juego, como el resto del casino
(art. 33.1 LIRPF). Los retos son premios que da el bot: ganancia patrimonial con
IRPF (`pay_income`, art. 33.1 LIRPF). Si el bot se apaga con las apuestas
abiertas, se devuelve lo apostado; si ya se estaba decidiendo, quien no había
decidido se retira y la mano se paga.

Solo se juega en los canales del casino que elige `garito` (sin elegir, los de `CASINO_CHANNEL_IDS`)
y en sus hilos. Permisos del bot en el canal: enviar mensajes, insertar enlaces, adjuntar archivos y
añadir reacciones (para confirmar `.poker 500`).
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
from datetime import datetime, timedelta
from enum import Enum
from functools import partial
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs import shop as tienda
from bot.cogs.casino import casino_channel_error, insufficient_text
from bot.cogs.timba import Timba
from bot.repositories.poker import HandRecord, PokerRepository
from bot.services.achievements import casino_stats, poker_club_stats, poker_stats
from bot.services.casino_channels import CasinoChannelIds
from bot.services.economy import (
    BalanceLimitError,
    EconomyService,
    InsufficientFundsError,
    PokerSettlement,
    format_amount,
    parse_amount,
)
from bot.services.levels import TIMEZONE
from bot.services.pets import bet_moment
from bot.services.poker import (
    BONUS_SHARE,
    BONUS_STAKE,
    CALL_FACTOR,
    GAME,
    MAX_SEATS,
    POT_SEED,
    Outcome,
    PokerHand,
    Rank,
    TableDeal,
    bonus,
    bonus_table,
    deal_table,
    payout,
    paytable,
    qualifies,
    showdown,
)
from bot.services.poker_club import (
    TIER_NAMES,
    HandEvent,
    daily_challenges,
    min_rank_for,
    next_rank,
    progress_for,
    rank_of,
    reward_for,
    streak_multiplier_pct,
    xp_for,
)
from bot.services.poker_render import Media, SeatView
from bot.services.poker_scene import PokerScene
from bot.services.taxes import TAX_COLLECTOR
from bot.services.telemetry import TimedLock
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import ContextResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

DEFAULT_STAKE = 100
#: Cuenta atrás desde que se sienta el primero. Si todos están listos, se reparte antes.
BET_SECONDS = 20
#: Tiempo para decidir entre ir y retirarse; quien no decide, se retira.
DECIDE_SECONDS = 25
#: Sin nadie sentado durante este tiempo, la mesa se cierra.
IDLE_SECONDS = 10 * 60
#: Margen tras el GIF antes de enseñar lo siguiente (lo que tarda en cargar).
REVEAL_MARGIN_SECONDS = 1.5
#: Desde esta jugada (full) se anuncia en el canal la mano cobrada.
SHOUT_RANK = Rank.FULL_HOUSE
#: Personas cuya mascota puede hablar en el resultado (para no llenar la mesa).
HINTS_LIMIT = 4

TABLE_PNG = "mesa.png"
HAND_GIF = "mano.gif"

COLOR_OPEN = discord.Color.from_rgb(22, 110, 70)
COLOR_DEALING = discord.Color.from_rgb(212, 160, 40)
COLOR_DECIDE = discord.Color.from_rgb(88, 101, 242)
NO_MENTIONS = discord.AllowedMentions.none()

# Textos en el tono de Jovani Vázquez.
DEALING_LINES = (
    "🃏 ¡No va más! Reparte la banca…",
    "🃏 Reparte el croupier del Casino del Estado…",
    "🕶️ Gafas de sol puestas, cara de póker…",
    "🃏 Las cartas vuelan por el tapete…",
    "🍹 Jugo de china de el bueno y a repartir…",
    "🃏 Barajando…",
)
SHOWDOWN_LINES = (
    "👀 La banca levanta las cartas…",
    "🙏 Que no juegue la banca, que no juegue…",
    "😬 Llega el river…",
    "🃏 Cartas boca arriba…",
    "🎙️ «Es el vecino el que elige al alcalde», y la banca la que enseña…",
)
QUIPS = {
    "win": (
        "La banca paga, por una vez.",
        "Cara de póker y bolsillo lleno.",
        "Ni Felipe en el 82.",
        "¡Toma mangó!",
    ),
    "nq": (
        "La banca no se presenta.",
        "La banca no da ni para pareja de cuatros.",
        "Incomparecencia de la banca, como en un debate.",
    ),
    "lose": (
        "La banca gana.",
        "Sé fuerte, que dijo aquel.",
        "Estoy destruido, pero por fuera.",
        "La banca llevaba mejores cartas.",
    ),
}


class PokerError(Exception):
    """Algo que no se puede hacer en la mesa (sin sitio, ya sentado, ante alto…)."""


class Phase(Enum):
    """Momento de la mano."""

    OPEN = "open"
    DEALING = "dealing"
    DECIDING = "deciding"
    SHOWDOWN = "showdown"
    CLOSED = "closed"


@dataclass(slots=True, eq=False)
class Seat:
    """Un jugador sentado en la mano.

    Attributes:
        ante: Su ante.
        bonus: Si juega el 💎 Bote en esta mano (ya cobrado).
        interaction: La última interacción con la que jugó, para el aviso de la Renta.
        placed_at: Cuándo se sentó (las porras no cuentan las anteriores a su cierre).
        ready: Si ha pulsado 🃏 ¡Cartas!.
        called: `True` si va, `False` si se retira, `None` mientras decide.
        paid: Lo que se le paga de la mano y de la parte fija del bote.
        bonus_paid: La parte fija del bote dentro de `paid`.
    """

    user: discord.abc.User
    ante: int
    bonus: bool = False
    interaction: discord.Interaction | None = None
    placed_at: float = 0.0
    ready: bool = False
    called: bool | None = None
    hand: PokerHand | None = None
    settlement: PokerSettlement | None = None
    paid: int = 0
    bonus_paid: int = 0
    record: HandRecord | None = None
    club_lines: list[str] = field(default_factory=list)
    retos_done: int = 0
    hint: str | None = None

    @property
    def staked(self) -> int:
        """Todo lo apostado en la mano: ante, ir y la apuesta del bote."""
        main = self.ante * (1 + (CALL_FACTOR if self.called else 0))
        return main + (BONUS_STAKE if self.bonus else 0)

    @property
    def pot_won(self) -> int:
        """Lo que se ha llevado del bote común."""
        return self.settlement.pot_won if self.settlement else 0

    @property
    def net(self) -> int:
        """Ganado menos apostado, bote incluido."""
        return self.paid + self.pot_won - self.staked

    @property
    def main_net(self) -> int:
        """Lo que gana o pierde la mano contra la banca (sin el bote)."""
        stake = self.ante * (1 + (CALL_FACTOR if self.called else 0))
        return self.paid - self.bonus_paid - stake


def short_name(name: str) -> str:
    """Nombre recortado para las listas."""
    return name if len(name) <= 16 else name[:15] + "…"


def tax_summary(seats: list[Seat]) -> str | None:
    """Subtexto con lo que retiene o devuelve Hacienda a cada uno en la mano."""
    taken, given = [], []
    for seat in seats:
        settlement = seat.settlement
        if settlement is None or not settlement.bet.tax_delta:
            continue
        delta = settlement.bet.tax_delta
        text = f"{format_amount(abs(delta))} a {short_name(seat.user.display_name)}"
        (taken if delta > 0 else given).append(text)
    parts = []
    if taken:
        parts.append("se lleva " + ", ".join(taken))
    if given:
        parts.append("devuelve " + ", ".join(given))
    return f"-# 🐶 {TAX_COLLECTOR} {' y '.join(parts)}." if parts else None


def seat_line(seat: Seat) -> str:
    """`✅ **Ana** · full de reyes y doses → **+900 Y$** · 💎 +120 Y$`."""
    hand = seat.hand
    assert hand is not None
    name = short_name(seat.user.display_name)
    label = hand.cards.player_value.label.lower()
    gem = ""
    if seat.bonus:
        won = seat.bonus_paid + seat.pot_won
        gem = f" · 💎 +{format_amount(won)}" if won else " · 💎 nada"
    if not seat.called:
        would = payout(hand.cards, seat.ante, called=True) - seat.ante * (1 + CALL_FACTOR)
        if would > 0:
            hint = f"habría ganado {format_amount(would)}"
        elif would < 0:
            hint = "bien retirado"
        else:
            hint = "habría empatado"
        return f"🏳️ **{name}** · se retira -{format_amount(seat.ante)} · 🔮 {hint}{gem}"
    net = seat.main_net
    match hand.outcome:
        case Outcome.WIN | Outcome.NO_QUALIFY:
            return f"✅ **{name}** · {label} → **+{format_amount(net)}**{gem}"
        case Outcome.TIE:
            return f"🤝 **{name}** · {label} → empate{gem}"
    return f"❌ **{name}** · {label} → -{format_amount(-net)}{gem}"


@dataclass(slots=True)
class Round:
    """Una mano ya jugada: lo que enseña la mesa mientras se apuesta la siguiente."""

    number: int
    table: TableDeal
    seats: list[Seat]


# -- Vistas ---------------------------------------------------------------------------------------


class AnteModal(discord.ui.Modal, title="🪙 Tu ante"):
    """Pide el ante con el que te sientas."""

    amount: discord.ui.TextInput = discord.ui.TextInput(
        label="Ante (500, 2k, mitad…; ir cuesta el doble)", placeholder="100", max_length=12
    )

    def __init__(self, table: Table) -> None:
        super().__init__()
        self.table = table

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Guarda el ante (leer el saldo va a la base de datos: se acepta antes)."""
        await ack(interaction)
        cog = self.table.cog
        balance = await cog.economy.balance(self.table.guild_id, interaction.user.id)
        try:
            stake = parse_amount(str(self.amount.value), balance)
        except ValueError as error:
            await notify(interaction, str(error))
            return
        cog.set_ante(self.table.guild_id, interaction.user.id, stake)
        await notify(
            interaction,
            f"🪙 Tu ante: **{format_amount(stake)}**. Ir cuesta "
            f"{format_amount(stake * CALL_FACTOR)} más: ten {format_amount(stake * 3)} "
            "para poder ir.",
        )


def _button(
    view: discord.ui.View,
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
    view.add_item(button)


class TableView(discord.ui.View):
    """Botones de la mesa mientras se apuesta. Los pulsa cualquiera: la mesa es de todos."""

    def __init__(self, table: Table, *, disabled: bool = False) -> None:
        super().__init__(timeout=None)
        grey = discord.ButtonStyle.secondary
        _button(self, "Sentarse", "🪑", discord.ButtonStyle.primary, table.sit_click, 0, "sit")
        _button(self, "¡Cartas!", "🃏", discord.ButtonStyle.success, table.mark_ready, 0, "ready")
        _button(self, "Ante", "🪙", grey, table.set_ante, 0, "ante")
        _button(self, "Bote", "💎", grey, table.toggle_bonus, 0, "bonus")
        _button(self, "Club", "🏆", grey, table.club_panel, 1, "club")
        _button(self, "Tabla", "📋", grey, table.paytable, 1, "table")
        # El 🎨: el tapete de cada uno, que se ve en las mesas que abra.
        if (paint := tienda.skin_button(table.cog.bot, GAME, row=1)) is not None:
            self.add_item(paint)
        if disabled:
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True


class DecideView(discord.ui.View):
    """Botones de la mesa mientras se decide: cada uno pulsa por su asiento."""

    def __init__(self, table: Table) -> None:
        super().__init__(timeout=None)
        grey = discord.ButtonStyle.secondary
        _button(self, "Ir", "✅", discord.ButtonStyle.success, table.call_click, 0, "call")
        _button(self, "Retirarse", "🏳️", discord.ButtonStyle.danger, table.fold_click, 0, "fold")
        _button(self, "Club", "🏆", grey, table.club_panel, 0, "club")
        _button(self, "Tabla", "📋", grey, table.paytable, 0, "table")


#: Los dos modos de `poker`: clave → (texto del botón y de la opción, emoji).
BANCA, TIMBA = "banca", "timba"
MODES: dict[str, tuple[str, str]] = {
    BANCA: ("♠️ Contra la banca (Casino Hold'em)", "♠️"),
    TIMBA: ("♣️ Timba entre jugadores (Texas Hold'em)", "♣️"),
}
#: Palabras que valen para cada modo en `.poker`.
MODE_ALIASES: dict[str, str] = {
    "banca": BANCA, "casino": BANCA, "holdem": BANCA, "hold'em": BANCA,
    "timba": TIMBA, "jugadores": TIMBA, "texas": TIMBA, "colegas": TIMBA,
}  # fmt: skip
#: Sin elegir modo en este tiempo, la pregunta caduca.
PICK_TIMEOUT = 60


def parse_mode(text: str | None) -> str | None:
    """El modo que dice `text` (`banca` o `timba`), o `None` si no es un modo."""
    if text is None:
        return None
    return MODE_ALIASES.get(text.strip().lower())


class ModePicker(discord.ui.View):
    """Un botón por modo, para `poker` sin modo. Solo los pulsa quien lo pidió.

    Args:
        amount: La cantidad que traía el comando, si traía (ante o ciega).
        prompt: La interacción de `/poker` cuya respuesta privada es esta pregunta,
            o `None` con `.poker`, que guarda su mensaje en `message`.
        channel: El canal del comando de texto (con `/poker`, el de la interacción).
    """

    def __init__(
        self,
        cog: Poker,
        author: discord.abc.User,
        amount: str | None,
        *,
        prompt: discord.Interaction | None = None,
        channel: object = None,
    ) -> None:
        super().__init__(timeout=PICK_TIMEOUT)
        self.cog, self.author, self.amount = cog, author, amount
        self.prompt = prompt
        self.channel = channel if channel is not None else getattr(prompt, "channel", None)
        self.message: discord.Message | None = None
        self.done = False
        for key, (label, emoji) in MODES.items():
            button: discord.ui.Button = discord.ui.Button(
                label=label.split(" ", 1)[1],
                emoji=emoji,
                style=discord.ButtonStyle.primary if key == BANCA else discord.ButtonStyle.success,
                custom_id=f"{GAME}:modo:{key}",
            )
            button.callback = partial(self.choose, key)  # type: ignore[method-assign]
            self.add_item(button)

    def question(self) -> str:
        """El texto de la pregunta."""
        return (
            f"🃏 {self.author.mention}, ¿a qué póker juegas?\n"
            "-# ♠️ **Contra la banca**: hasta seis contra la misma banca, con 💎 Bote y club. "
            "♣️ **Timba**: Texas Hold'em entre vosotros, con ciegas y botes."
        )

    async def choose(self, mode: str, interaction: discord.Interaction) -> None:
        """Clic en un modo: abre su mesa y cambia la pregunta por la respuesta."""
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "Esa pregunta no es tuya: pide la tuya con `/poker`.", ephemeral=True
            )
            return
        if self.done:
            await ack(interaction)
            return
        self.done = True
        self.stop()
        # Abrir la mesa dibuja y escribe: se acepta el clic antes.
        await ack(interaction)

        async def answer(text: str) -> None:
            await edit(interaction, content=text, view=None, allowed_mentions=NO_MENTIONS)

        async def confirm(text: str) -> None:
            await answer(text or f"{MODES[mode][1]} Mesa abierta: abajo del todo.")

        await self.cog.play_mode(
            mode,
            guild=interaction.guild,
            channel=self.channel,
            user=interaction.user,
            amount_text=self.amount,
            confirm=confirm,
            send_error=answer,
            interaction=interaction,
        )
        await renta.remind(self.cog.bot, interaction)

    async def on_timeout(self) -> None:
        """Sin elegir: la pregunta dice que ha caducado."""
        if self.done:
            return
        self.done = True
        text = "⌛ Sin elegir modo. Pide otra vez `poker` cuando quieras."
        try:
            if self.message is not None:
                await self.message.edit(content=text, view=None)
            elif self.prompt is not None:
                await self.prompt.edit_original_response(content=text, view=None)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar la pregunta del póker", exc_info=True)


# -- Mesa -----------------------------------------------------------------------------------------


class Table:
    """La mesa de un canal: sus asientos, su mensaje y su bucle de manos.

    El dinero nunca vive aquí: se cobra y se paga siempre por la economía.
    """

    def __init__(
        self,
        cog: Poker,
        *,
        guild_id: int,
        channel: discord.abc.Messageable,
        png: bytes,
        pot: int,
        skin: dict[str, object] | None = None,
        skin_label: str | None = None,
    ) -> None:
        self.cog = cog
        #: El tapete de quien abre la mesa (es compartida): vale hasta que se cierra.
        self.skin = skin
        self.skin_label = skin_label
        self.guild_id = guild_id
        self.channel = channel
        #: La imagen de la mesa quieta (la última mano o la mesa vacía).
        self.png = png
        #: Lo que llevaba el 💎 Bote la última vez que se miró.
        self.pot = pot
        self.message: discord.Message | None = None
        self.phase = Phase.OPEN
        self.seats: dict[int, Seat] = {}
        self.deadline: float | None = None
        self.opened_at = cog.wall_clock()
        self.hands = 0
        self.last_round: Round | None = None
        self.current: TableDeal | None = None
        self.task: asyncio.Task[None] | None = None
        self._lock = TimedLock("poker:Table._lock")
        self._edit_task: asyncio.Task[None] | None = None
        self._dirty = False
        #: Se activan con el primero que se sienta, cuando están todos listos y
        #: cuando han decidido todos.
        self.first_seat = asyncio.Event()
        self.go = asyncio.Event()
        self.decided = asyncio.Event()
        self.view = TableView(self)
        #: Los mismos botones apagados, mientras se reparte o se enseña el final.
        self.closed_view = TableView(self, disabled=True)
        self.decide_view = DecideView(self)
        self.stopping = False
        self.sleeping = False

    # -- Vista -----------------------------------------------------------------------------

    def seats_block(self) -> str:
        """Los sentados de la mano en curso."""
        lines = []
        for seat in self.seats.values():
            mark = "✅" if seat.ready else "🪑"
            if self.phase in (Phase.DECIDING, Phase.SHOWDOWN):
                mark = {None: "⏳", True: "✅ va", False: "🏳️ se retira"}[seat.called]
            gem = " 💎" if seat.bonus else ""
            name = short_name(seat.user.display_name)
            lines.append(f"{mark} {name} · ante {format_amount(seat.ante)}{gem}")
        return "\n".join(lines)

    def _round_block(self) -> list[str]:
        """La mano anterior: la banca, cada asiento, el club, Hacienda y las mascotas."""
        played = self.last_round
        if played is None:
            return []
        dealer = played.table.seat(0).dealer_value
        plays = qualifies(dealer)
        verb = "juega" if plays else "no juega"
        lines = [f"Mano {played.number} · la banca lleva **{dealer.label.lower()}** y {verb}."]
        won = any(s.called and s.main_net > 0 for s in played.seats)
        kind = "nq" if not plays else "win" if won else "lose"
        lines.append(f"-# {random.Random(played.number).choice(QUIPS[kind])}")
        for seat in sorted(played.seats, key=lambda s: -s.net):
            lines.append(seat_line(seat))
            lines.extend(seat.club_lines)
            if seat.hint:
                lines.append(seat.hint)
        if note := tax_summary(played.seats):
            lines.append(note)
        return lines

    def open_embed(self) -> discord.Embed:
        """La mesa abierta: la mano anterior, el bote y los sentados de esta."""
        lines = self._round_block()
        if lines:
            lines.append("")
        lines.append(f"💎 **Bote común: {format_amount(self.pot)}**")
        if self.seats:
            assert self.deadline is not None
            ready = sum(s.ready for s in self.seats.values())
            lines.append(f"### 🃏 Cartas <t:{math.ceil(self.deadline)}:R>")
            lines.append(
                f"✅ Listos {ready}/{len(self.seats)}: si estáis todos, se reparte ya. "
                f"Sitios: {len(self.seats)}/{MAX_SEATS}."
            )
        else:
            closes = math.ceil(self.opened_at + IDLE_SECONDS)
            lines.append(
                "Pulsa 🪑 Sentarse para jugar la próxima mano contra la banca. "
                f"Sin nadie sentado, la mesa se cierra <t:{closes}:R>."
            )
        embed = discord.Embed(
            title="♠️ Póker · Casino Hold'em",
            description="\n".join(lines)[:4_000],
            color=COLOR_OPEN,
        )
        if self.seats:
            embed.add_field(
                name=f"En la mesa ({len(self.seats)})", value=self.seats_block()[:1_024]
            )
        embed.set_image(url=f"attachment://{TABLE_PNG}")
        embed.set_footer(
            text=(
                "Ir cuesta el doble del ante · la banca juega con pareja de 4 · "
                f"💎 Bote: {format_amount(BONUS_STAKE)} por mano"
                + (f" · Tapete: {self.skin_label}" if self.skin_label else "")
            )
        )
        return embed

    def waiting_embed(self, lines: tuple[str, ...], image: str = HAND_GIF) -> discord.Embed:
        """¡No va más!: la espera del GIF, sin destripar nada."""
        embed = discord.Embed(
            title="♠️ Póker · Casino Hold'em",
            description=f"# {random.choice(lines)}",
            color=COLOR_DEALING,
        )
        if self.seats:
            embed.add_field(
                name=f"En la mesa ({len(self.seats)})", value=self.seats_block()[:1_024]
            )
        embed.set_image(url=f"attachment://{image}")
        return embed

    def decide_embed(self) -> discord.Embed:
        """Decidir: qué lleva cada uno y quién ha decidido ya."""
        assert self.current is not None and self.deadline is not None
        lines = [f"### ⏳ Decidid <t:{math.ceil(self.deadline)}:R>"]
        flop = " ".join(f"**{c}**" for c in self.current.flop)
        lines.append(f"Flop: {flop}")
        for seat in self.seats.values():
            assert seat.hand is not None
            hand = seat.hand.cards
            cards = " ".join(f"**{c}**" for c in hand.player)
            mark = {None: "⏳", True: "✅", False: "🏳️"}[seat.called]
            lines.append(
                f"{mark} **{short_name(seat.user.display_name)}** · {cards} · "
                f"{hand.flop_value.label.lower()} · ir {format_amount(seat.ante * CALL_FACTOR)}"
            )
        lines.append(
            "-# ✅ Ir pone el doble del ante y se ve el final; 🏳️ Retirarse pierde el ante. "
            "Quien no decide a tiempo, se retira."
        )
        embed = discord.Embed(
            title="♠️ Póker · ¿Vas o te retiras?",
            description="\n".join(lines)[:4_000],
            color=COLOR_DECIDE,
        )
        embed.set_image(url=f"attachment://{TABLE_PNG}")
        return embed

    async def edit(self, **kwargs: Any) -> None:
        """Edita el mensaje de la mesa; un fallo de Discord no rompe nada."""
        if self.message is None:
            return
        try:
            await self.message.edit(**kwargs)
        except discord.HTTPException:
            logger.debug("No se pudo editar la mesa de póker", exc_info=True)

    def edit_soon(self) -> None:
        """Repinta la mesa (abierta o decidiendo) sin esperar; agrupa los cambios seguidos.

        Los botones nunca editan el mensaje ellos mismos: si un clic tardío lo
        hiciera mientras sale un GIF, lo taparía. Aquí solo se repinta mientras
        la mano siga en la misma fase.
        """
        self._dirty = True
        if self._edit_task is None or self._edit_task.done():
            self._edit_task = asyncio.create_task(self._flush())

    async def _flush(self) -> None:
        while self._dirty and self.phase in (Phase.OPEN, Phase.DECIDING):
            self._dirty = False
            if self.phase is Phase.OPEN:
                await self.edit(embed=self.open_embed(), view=self.view)
            else:
                await self.edit(embed=self.decide_embed(), view=self.decide_view)

    def table_file(self) -> discord.File:
        """La imagen de la mesa quieta para adjuntarla."""
        return discord.File(io.BytesIO(self.png), filename=TABLE_PNG)

    async def _wait_edits(self) -> None:
        if self._edit_task is not None:
            await asyncio.gather(self._edit_task, return_exceptions=True)

    # -- Sentarse ----------------------------------------------------------------------------

    async def sit(
        self, user: discord.abc.User, *, interaction: discord.Interaction | None = None
    ) -> Seat:
        """Sienta a alguien con su ante (y el bote si lo juega) y se lo cobra.

        Raises:
            InsufficientFundsError: Si no le llega.
            PokerError: Si la mano ya se reparte, no hay sitio, ya está sentado o
                su rango no le deja ese ante.
        """
        ante = self.cog.ante(self.guild_id, user.id)
        player = await self.cog.club.player(self.guild_id, user.id)
        rank = rank_of(player.xp)
        if rank.max_ante is not None and ante > rank.max_ante:
            need = min_rank_for(ante)
            raise PokerError(
                f"Con tu rango ({rank.emoji} {rank.name}) el ante máximo es "
                f"{format_amount(rank.max_ante)}. Para {format_amount(ante)} hace falta "
                f"{need.emoji} {need.name}: juega más manos o baja el ante con 🪙."
            )
        async with self._lock:
            if self.phase is not Phase.OPEN:
                raise PokerError("¡No va más! Espera a la mano siguiente.")
            if user.id in self.seats:
                raise PokerError("Ya estás sentado en esta mano: pulsa 🃏 ¡Cartas! cuando quieras.")
            if len(self.seats) >= MAX_SEATS:
                raise PokerError(f"La mesa está llena ({MAX_SEATS} sitios). A la próxima.")
            stake = ante + (BONUS_STAKE if player.bonus else 0)
            await self.cog.economy.place_bet(self.guild_id, user.id, game=GAME, stake=stake)
            seat = Seat(
                user=user,
                ante=ante,
                bonus=player.bonus,
                interaction=interaction,
                placed_at=self.cog.wall_clock(),
            )
            self.seats[user.id] = seat
            if self.deadline is None:
                self.deadline = self.cog.wall_clock() + BET_SECONDS
                self.first_seat.set()
            return seat

    async def sit_click(self, interaction: discord.Interaction) -> None:
        """🪑 Sentarse: cobra el ante y te da sitio en la mano."""
        if self.phase is not Phase.OPEN:
            await interaction.response.send_message(
                "¡No va más! Espera a la mano siguiente.", ephemeral=True
            )
            return
        if interaction.user.id in self.seats:
            await interaction.response.send_message(
                "Ya estás sentado: pulsa 🃏 ¡Cartas! cuando quieras.", ephemeral=True
            )
            return
        # Rango, saldo y cobro van a la base de datos: se acepta el clic antes.
        await ack(interaction)
        try:
            seat = await self.sit(interaction.user, interaction=interaction)
        except InsufficientFundsError as error:
            ante = self.cog.ante(self.guild_id, interaction.user.id)
            await notify(interaction, insufficient_text(error.balance, ante))
            return
        except PokerError as error:
            await notify(interaction, str(error))
            return
        if seat.bonus:
            await notify(
                interaction,
                f"💎 Juegas el Bote ({format_amount(BONUS_STAKE)}). Se apaga con 💎 Bote.",
            )
        self.edit_soon()
        await renta.remind(self.cog.bot, interaction)

    def _ready(self, seat: Seat) -> None:
        """Marca listo a alguien; si ya lo están todos, se reparte."""
        seat.ready = True
        if all(s.ready for s in self.seats.values()):
            self.go.set()

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
                "Primero siéntate con 🪑 Sentarse.", ephemeral=True
            )
            return
        await interaction.response.defer()
        self._ready(seat)
        self.edit_soon()

    async def set_ante(self, interaction: discord.Interaction) -> None:
        """🪙 Ante: abre el formulario (tiene que ser la primera respuesta)."""
        await interaction.response.send_modal(AnteModal(self))

    async def toggle_bonus(self, interaction: discord.Interaction) -> None:
        """💎 Bote: lo enciende o lo apaga para las manos siguientes (se recuerda)."""
        await ack(interaction)
        player = await self.cog.club.player(self.guild_id, interaction.user.id)
        on = not player.bonus
        await self.cog.club.set_bonus(self.guild_id, interaction.user.id, on)
        if on:
            text = (
                f"💎 **Juegas el Bote**: {format_amount(BONUS_STAKE)} más por mano. Con escalera "
                f"real te llevas el bote entero (ahora {format_amount(self.pot)}); con escalera "
                "de color, el 10 %; y póker, full o color pagan fijo. Vale desde la próxima vez "
                "que te sientes."
            )
        else:
            text = "💎 Bote apagado: desde la próxima vez que te sientes, solo el ante."
        await notify(interaction, text)

    async def paytable(self, interaction: discord.Interaction) -> None:
        """📋 Las tablas del ante y del bote, en privado (solo memoria: se contesta ya)."""
        pays = "\n".join(f"**{label}** · {p}" for label, p in paytable())
        bonuses = "\n".join(f"**{label}** · {p}" for label, p in bonus_table())
        embed = discord.Embed(
            title="📋 Póker · Casino Hold'em",
            description=(
                "Pones el ante y ves tus dos cartas y el flop. **Ir** cuesta el doble del "
                "ante; **retirarse** pierde el ante. Cada uno juega su mejor mano de cinco con "
                "sus dos cartas y las cinco de la mesa, contra la misma banca.\n\n"
                "La banca juega con **pareja de cuatros o mejor**. Si no juega, el ante cobra "
                "según la tabla y la de ir se devuelve. Si juega y le ganas, el ante cobra según "
                "la tabla y la de ir paga 1 a 1; si empatáis, se devuelve todo.\n\n"
                "**El ante paga:**\n" + pays + "\n\n"
                f"**💎 Bote** ({format_amount(BONUS_STAKE)} aparte, con tus siete cartas aunque "
                f"te retires; {format_amount(BONUS_SHARE)} de cada apuesta van al bote común, "
                f"que ahora tiene {format_amount(self.pot)}):\n" + bonuses + "\n\n"
                "-# Con la mejor estrategia (ir casi siempre), la casa se queda de media un "
                "2,16 % del ante. El bote devuelve de media un 76 %."
            ),
            color=COLOR_OPEN,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def club_panel(self, interaction: discord.Interaction) -> None:
        """🏆 Club: tu rango, tu racha y tus retos de hoy, en privado."""
        await ack(interaction, new_message=True)
        embed = await self.cog.club_embed(self.guild_id, interaction.user)
        await edit(interaction, embed=embed)

    # -- Decidir ---------------------------------------------------------------------------

    async def decide(self, interaction: discord.Interaction, called: bool) -> None:
        """✅ Ir o 🏳️ Retirarse por el asiento de quien pulsa."""
        seat = self.seats.get(interaction.user.id)
        if self.phase is not Phase.DECIDING or seat is None:
            text = (
                "No estás en esta mano: siéntate en la siguiente."
                if seat is None
                else "Ya no se puede decidir."
            )
            await interaction.response.send_message(text, ephemeral=True)
            return
        if seat.called is not None:
            await interaction.response.send_message("Ya has decidido.", ephemeral=True)
            return
        if not called:
            # Retirarse solo cambia memoria: se contesta directamente.
            await interaction.response.defer()
            seat.called = False
        else:
            await ack(interaction)
            async with self._lock:
                if self.phase is not Phase.DECIDING or seat.called is not None:
                    return
                try:
                    await self.cog.economy.place_bet(
                        self.guild_id, seat.user.id, game=GAME, stake=seat.ante * CALL_FACTOR
                    )
                except InsufficientFundsError as error:
                    await notify(
                        interaction,
                        f"{insufficient_text(error.balance, seat.ante * CALL_FACTOR)} "
                        "Solo puedes retirarte.",
                    )
                    return
                seat.called = True
                seat.interaction = interaction
        if all(s.called is not None for s in self.seats.values()):
            self.decided.set()
        self.edit_soon()
        if called:
            await renta.remind(self.cog.bot, interaction)

    async def call_click(self, interaction: discord.Interaction) -> None:
        """✅ Ir: pone el doble del ante y se ve el final."""
        await self.decide(interaction, True)

    async def fold_click(self, interaction: discord.Interaction) -> None:
        """🏳️ Retirarse: pierde el ante (el bote sigue en juego)."""
        await self.decide(interaction, False)

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

    def open_round(self) -> None:
        """Abre las apuestas de una mano nueva."""
        self.seats = {}
        self.deadline = None
        self.current = None
        self.first_seat.clear()
        self.go.clear()
        self.decided.clear()
        self.opened_at = self.cog.wall_clock()
        self.phase = Phase.OPEN

    def views(self) -> list[SeatView]:
        """Los asientos tal y como los pinta la mesa."""
        return [
            SeatView(name=s.user.display_name, ante=s.ante, bonus=s.bonus)
            for s in self.seats.values()
        ]

    async def run(self) -> None:
        """Manos y más manos hasta que nadie se sienta en `IDLE_SECONDS`."""
        try:
            while True:
                await self.nap(IDLE_SECONDS, wake=self.first_seat)
                if not self.seats:
                    await self.close()
                    return
                assert self.deadline is not None
                await self.nap(max(0.0, self.deadline - self.cog.wall_clock()), wake=self.go)
                await self.deal()
                await self.nap(DECIDE_SECONDS, wake=self.decided)
                await self.finish()
        except asyncio.CancelledError:
            if not self.stopping:
                raise
        except Exception:
            logger.exception("La mesa de póker ha fallado; se cierra")
            await self.shutdown()
        finally:
            self.cog.release(self)

    async def _show(self, media: Media, lines: tuple[str, ...]) -> None:
        """El GIF con su texto de espera; luego su último fotograma queda como mesa."""
        if media.gif is not None:
            await self.edit(
                embed=self.waiting_embed(lines),
                view=self.closed_view,
                attachments=[discord.File(io.BytesIO(media.gif), filename=HAND_GIF)],
            )
            await self.nap(media.seconds + REVEAL_MARGIN_SECONDS)
        self.png = media.png

    async def _cover(self, lines: tuple[str, ...]) -> None:
        """Apaga los botones y lo dice ya, mientras se pinta el GIF (en paralelo)."""
        await self.edit(embed=self.waiting_embed(lines, TABLE_PNG), view=self.closed_view)

    async def deal(self) -> None:
        """Cierra las apuestas, reparte, enseña el reparto y abre las decisiones."""
        async with self._lock:
            self.phase = Phase.DEALING
        await self._wait_edits()
        seats = list(self.seats.values())
        self.hands += 1
        table = deal_table(self.cog.rng, len(seats))
        self.current = table
        for i, seat in enumerate(seats):
            seat.hand = PokerHand(ante=seat.ante, cards=table.seat(i))
        self.pot = await self.cog.economy.poker_pot(self.guild_id, seed=POT_SEED)
        render = asyncio.create_task(
            self.cog.renderer.deal(
                table, self.views(), pot=self.pot, seed=self.cog.rng.getrandbits(32), skin=self.skin
            )
        )
        await self._cover(DEALING_LINES)
        media = await render
        await self._show(media, DEALING_LINES)
        self.deadline = self.cog.wall_clock() + DECIDE_SECONDS
        self.phase = Phase.DECIDING
        await self.edit(
            embed=self.decide_embed(), view=self.decide_view, attachments=[self.table_file()]
        )

    async def finish(self) -> None:
        """Cierra las decisiones, paga, enseña el final y vuelve a abrir la mesa."""
        async with self._lock:
            self.phase = Phase.SHOWDOWN
            for seat in self.seats.values():
                if seat.called is None:
                    seat.called = False
        await self._wait_edits()
        seats = list(self.seats.values())
        table = self.current
        assert table is not None
        await self.pay(seats)
        render = asyncio.create_task(
            self.cog.renderer.showdown(
                table,
                self.views(),
                [bool(s.called) for s in seats],
                pot=self.pot,
                seed=self.cog.rng.getrandbits(32),
                skin=self.skin,
            )
        )
        await self._cover(SHOWDOWN_LINES)
        media = await render
        await self._show(media, SHOWDOWN_LINES)
        await self.club(seats)
        for seat in seats[:HINTS_LIMIT]:
            seat.hint = await renta.hint(
                self.cog.bot,
                self.guild_id,
                seat.user.id,
                bet_moment(
                    stake=seat.staked,
                    net=seat.net,
                    balance_after=seat.settlement.bet.balance if seat.settlement else 0,
                ),
            )
        self.pot = await self.cog.economy.poker_pot(self.guild_id, seed=POT_SEED)
        self.last_round = Round(number=self.hands, table=table, seats=seats)
        self.open_round()
        await self.edit(embed=self.open_embed(), view=self.view, attachments=[self.table_file()])
        await self.track(seats)

    async def pay(self, seats: list[Seat]) -> None:
        """Paga a cada uno su mano, la parte fija del bote y lo que se lleve del bote común."""
        for seat in seats:
            hand = seat.hand
            assert hand is not None
            hand.called = bool(seat.called)
            prize = hand.payout
            share = permille = 0
            if seat.bonus:
                result = bonus(hand.cards.player_value.rank)
                seat.bonus_paid = result.fixed
                prize += result.fixed
                share, permille = BONUS_SHARE, result.pot_permille
            seat.paid = prize
            try:
                seat.settlement = await self.cog.economy.pay_poker(
                    self.guild_id,
                    seat.user.id,
                    game=GAME,
                    payout=prize,
                    share=share,
                    pot_permille=permille,
                    seed=POT_SEED,
                )
            except BalanceLimitError:
                logger.warning("Premio de póker por encima del saldo máximo; no se paga.")
                seat.paid = seat.bonus_paid = 0

    async def club(self, seats: list[Seat]) -> None:
        """XP, racha y retos de cada uno; paga los retos cumplidos y prepara sus líneas."""
        today = datetime.fromtimestamp(self.cog.wall_clock(), TIMEZONE).date()
        others = len(seats) - 1
        for seat in seats:
            hand = seat.hand
            assert hand is not None
            event = HandEvent(
                ante=seat.ante,
                called=bool(seat.called),
                outcome=showdown(hand.cards),
                rank=hand.cards.player_value.rank,
                net=seat.main_net,
                bonus=seat.bonus,
                bonus_won=bool(seat.bonus_paid or seat.pot_won),
                others=others,
                pocket_aces=sorted(c.rank for c in hand.cards.player) == [1, 1],
            )
            retos = daily_challenges(self.guild_id, seat.user.id, today)
            try:
                record = await self.cog.club.record_hand(
                    self.guild_id,
                    seat.user.id,
                    day=today,
                    xp=xp_for(event),
                    counts=event.counts,
                    progress=progress_for(retos, event),
                    goals={c.key: c.goal for c in retos},
                )
            except Exception:
                logger.exception("No se pudo apuntar la mano de póker en el club")
                continue
            seat.record = record
            name = short_name(seat.user.display_name)
            by_key = {c.key: c for c in retos}
            for key in record.completed:
                challenge = by_key[key]
                gross = reward_for(challenge, record.after.streak)
                try:
                    paid = await self.cog.economy.pay_income(
                        self.guild_id, seat.user.id, gross=gross, concept=f"poker:reto:{key}"
                    )
                except BalanceLimitError:
                    continue
                tax = f" (IRPF -{format_amount(paid.tax)})" if paid.tax else ""
                seat.club_lines.append(
                    f"-# 🎯 {name} cumple el reto «{challenge.label}»: +{format_amount(gross)}{tax}"
                )
            seat.retos_done = sum(1 for c in retos if record.progress.get(c.key, 0) >= c.goal)
            if record.completed and seat.retos_done == len(retos):
                seat.club_lines.append(f"-# 🏅 {name} completa los tres retos de hoy.")
            before, after = rank_of(record.before.xp), rank_of(record.after.xp)
            if after.level > before.level:
                cap = (
                    f" Ante hasta {format_amount(after.max_ante)}."
                    if after.max_ante is not None
                    else " Sin tope de ante."
                )
                seat.club_lines.append(
                    f"⬆️ **{name}** sube a {after.emoji} **{after.name}** (nivel {after.level})."
                    + cap
                )
            if record.after.streak > record.before.streak and record.after.streak >= 2:
                pct = streak_multiplier_pct(record.after.streak)
                seat.club_lines.append(
                    f"-# 🔥 {name} lleva {record.after.streak} días seguidos: retos +{pct} %."
                )

    async def track(self, seats: list[Seat]) -> None:
        """Renta, logros, estadísticas del casino, porras y anuncios, tras enseñar la mano."""
        when = datetime.fromtimestamp(self.cog.wall_clock(), TIMEZONE)
        winners = [s for s in seats if s.called and s.main_net > 0]
        for seat in seats:
            hand = seat.hand
            assert hand is not None
            settlement = seat.settlement
            balance = settlement.bet.balance if settlement else 0
            tax = settlement.bet.tax_delta if settlement else 0
            record = seat.record
            delta = poker_stats(hand, when=when)
            delta.merge(
                poker_club_stats(
                    bonus=seat.bonus,
                    bonus_paid=seat.bonus_paid,
                    pot_won=seat.pot_won,
                    rank=hand.cards.player_value.rank,
                    players=len(seats),
                    lone_win=seat in winners and len(winners) == 1 and len(seats) >= 3,
                    retos=len(record.completed) if record else 0,
                    retos_full=bool(record and record.completed and seat.retos_done == 3),
                    level=rank_of(record.after.xp).level if record else 0,
                    streak=record.after.streak if record else 0,
                )
            )
            delta.merge(
                casino_stats(stake=seat.staked, net=seat.net, balance_after=balance, tax_delta=tax)
            )
            if seat.interaction is not None:
                await renta.remind(self.cog.bot, seat.interaction)
            await logros.casino_play(
                self.cog.bot, self.guild_id, seat.user, self.channel, delta, net=seat.net
            )
            await apuestas.record(
                self.cog.bot,
                self.guild_id,
                seat.user,
                game=GAME,
                stake=seat.staked,
                net=seat.net,
                balance_after=balance,
                tax=tax,
                details=(
                    ("made", hand.cards.player_value.rank.order),
                    ("fold", int(not seat.called)),
                    ("started", int(seat.placed_at)),
                ),
            )
            await self.cog.shout(seat, self.channel)

    async def close(self) -> None:
        """Mesa sin nadie: se cierra y el mensaje pierde los botones."""
        self.phase = Phase.CLOSED
        embed = self.open_embed()
        embed.title = "♠️ Póker · mesa cerrada"
        embed.description = (
            "\n".join(self._round_block())
            + "\n\nNadie se sienta: el croupier recoge. `poker` la vuelve a abrir."
        ).strip()
        embed.clear_fields()
        embed.set_footer(text=None)
        await self.edit(embed=embed, view=None)
        for view in (self.view, self.closed_view, self.decide_view):
            view.stop()

    async def shutdown(self) -> None:
        """Cierre ordenado.

        Con las apuestas abiertas, se devuelve lo cobrado al sentarse. Si ya se
        habían repartido las cartas, quien no ha decidido se retira y la mano se
        paga; si se estaba enseñando el final, ya estaba pagada.
        """
        async with self._lock:
            phase = self.phase
            seats = list(self.seats.values())
            self.phase = Phase.CLOSED
        if phase is Phase.OPEN or (phase is Phase.DEALING and any(s.hand is None for s in seats)):
            for seat in seats:
                try:
                    await self.cog.economy.pay_winnings(
                        self.guild_id, seat.user.id, game=GAME, amount=seat.staked
                    )
                except BalanceLimitError:
                    logger.warning("No se pudo devolver una apuesta de póker.")
        elif phase in (Phase.DEALING, Phase.DECIDING):
            for seat in seats:
                if seat.called is None:
                    seat.called = False
            await self.pay(seats)
        self.seats.clear()
        embed = discord.Embed(
            title="♠️ Póker · mesa cerrada",
            description="El casino cierra. Las manos a medias se han pagado o devuelto.",
            color=COLOR_OPEN,
        )
        await self.edit(embed=embed, view=None)
        for view in (self.view, self.closed_view, self.decide_view):
            view.stop()

    async def move(self) -> None:
        """Vuelve a publicar la mesa al final del canal y borra la de arriba.

        Raises:
            PokerError: Si hay una mano en marcha (no se puede mover a mitad).
        """
        async with self._lock:
            if self.phase is not Phase.OPEN:
                raise PokerError("Hay una mano en marcha. En cuanto acabe, la tienes abajo.")
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
                logger.debug("No se pudo borrar la mesa de póker vieja", exc_info=True)


# -- Cog ------------------------------------------------------------------------------------------


class Poker(commands.Cog, name="Póker"):
    """El póker del casino: una mesa por canal contra la banca, con bote y club."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        club: PokerRepository,
        renderer: PokerScene | None = None,
        casino_channel_ids: CasinoChannelIds = frozenset(),
        rng: Any = None,
        wall_clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.club = club
        self.renderer = renderer or PokerScene()
        self.casino_channel_ids = casino_channel_ids
        # `secrets` usa el azar del sistema operativo: no se puede predecir.
        self.rng = rng or secrets.SystemRandom()
        self.wall_clock = wall_clock
        self.sleep = sleep
        #: Mesa abierta de cada canal.
        self.tables: dict[int, Table] = {}
        #: Canales en los que se está abriendo una mesa (para no abrir dos).
        self._opening: set[int] = set()
        # Ante por (servidor, miembro): crece como mucho hasta los que han jugado y se
        # pierde al reiniciar (vuelve a 100).
        self._antes: dict[tuple[int, int], int] = {}

    def ante(self, guild_id: int, user_id: int) -> int:
        """Ante recordado de un miembro."""
        return self._antes.get((guild_id, user_id), DEFAULT_STAKE)

    def set_ante(self, guild_id: int, user_id: int, stake: int) -> None:
        """Recuerda el ante de un miembro."""
        self._antes[(guild_id, user_id)] = max(1, stake)

    def release(self, table: Table) -> None:
        """Libera el canal cuando su mesa se cierra."""
        channel_id = getattr(table.channel, "id", 0)
        if self.tables.get(channel_id) is table:
            del self.tables[channel_id]

    async def cog_unload(self) -> None:
        """Al apagar, devuelve o paga las manos de las mesas abiertas y cierra el pintor."""
        for table in list(self.tables.values()):
            table.stopping = True
            if table.task is not None:
                if table.sleeping:
                    table.task.cancel()
                await asyncio.gather(table.task, return_exceptions=True)
            try:
                await table.shutdown()
            except Exception:
                logger.exception("No se pudo cerrar una mesa de póker al apagar")
        self.tables.clear()
        await self.renderer.close()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """El bot sale de un servidor: se borra su club del póker."""
        await self.club.forget_guild(guild.id)

    async def shout(self, seat: Seat, channel: object) -> None:
        """Anuncia en el canal las manos cobradas con full o mejor y los botes que caen."""
        if not isinstance(channel, discord.abc.Messageable) or seat.hand is None:
            return
        user = seat.user
        value = seat.hand.cards.player_value
        if seat.pot_won:
            text = (
                f"📣 💎 ¡{user.mention} se lleva **{format_amount(seat.pot_won)}** del bote del "
                f"póker con **{value.label.lower()}**!"
            )
        elif seat.called and seat.main_net > 0 and value.rank.order >= SHOUT_RANK.order:
            text = (
                f"📣 ♠️ ¡{user.mention} liga **{value.label.lower()}** al póker! "
                f"**+{format_amount(seat.main_net)}**"
            )
        else:
            return
        try:
            await channel.send(
                text, allowed_mentions=discord.AllowedMentions(users=[user], everyone=False)
            )
        except discord.HTTPException:
            logger.debug("No se pudo anunciar una mano del póker", exc_info=True)

    async def club_embed(self, guild_id: int, user: discord.abc.User) -> discord.Embed:
        """La ficha del club de un miembro: rango, experiencia, racha y retos de hoy."""
        player = await self.club.player(guild_id, user.id)
        today = datetime.fromtimestamp(self.wall_clock(), TIMEZONE).date()
        progress = await self.club.daily(guild_id, user.id, today)
        rank = rank_of(player.xp)
        upcoming = next_rank(player.xp)
        alive = player.last_day in (today, today - timedelta(days=1))
        streak = player.streak if alive else 0
        lines = [f"## {rank.emoji} {rank.name} · nivel {rank.level}"]
        if upcoming is not None:
            filled = round(10 * (player.xp - rank.xp) / (upcoming.xp - rank.xp))
            lines.append(
                f"`{'█' * filled}{'░' * (10 - filled)}` {player.xp}/{upcoming.xp} XP → "
                f"{upcoming.emoji} {upcoming.name}"
            )
        else:
            lines.append(f"{player.xp} XP: no hay rango más alto. Respeto.")
        cap = f"hasta {format_amount(rank.max_ante)}" if rank.max_ante is not None else "sin tope"
        lines.append(f"-# Ante {cap}. Cada mano con ante de 100 Y$ o más da 1 XP, y 2 si la ganas.")
        pct = streak_multiplier_pct(max(streak, 1))
        days = "día" if streak == 1 else "días"
        lines.append(f"🔥 Racha: **{streak} {days}** (retos +{pct} %, hasta +100 % a los 11 días).")
        lines.append("### 🎯 Retos de hoy")
        for challenge in daily_challenges(guild_id, user.id, today):
            got, done = progress.get(challenge.key, (0, False))
            reward = reward_for(challenge, max(streak, 1))
            mark = "✅" if done else "▫️"
            lines.append(
                f"{mark} **{TIER_NAMES[challenge.tier]}** · {challenge.label} "
                f"({min(got, challenge.goal)}/{challenge.goal}) · {format_amount(reward)}"
            )
        gem = "Juegas" if player.bonus else "No juegas"
        lines.append(f"-# Se renuevan a medianoche. 💎 {gem} el Bote.")
        return discord.Embed(
            title=f"🏆 Club del póker · {user.display_name}",
            description="\n".join(lines),
            color=COLOR_OPEN,
        )

    # -- Abrir y sentarse ----------------------------------------------------------------------

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
        try:
            skin = tienda.skin_params(self.bot, guild_id, opener_id, GAME)
            worn = tienda.skin_of(self.bot, guild_id, opener_id, GAME)
            pot = await self.economy.poker_pot(guild_id, seed=POT_SEED)
            png = await self.renderer.board(pot=pot, skin=skin)
            table = Table(
                self,
                guild_id=guild_id,
                channel=channel,
                png=png,
                pot=pot,
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
        finally:
            self._opening.discard(channel_id)
        self.tables[channel_id] = table
        self.start(table)
        return table, True

    def start(self, table: Table) -> None:
        """Arranca el bucle de una mesa recién abierta."""
        channel_id = getattr(table.channel, "id", 0)
        table.task = asyncio.create_task(table.run(), name=f"poker-{channel_id}")

    async def _poker_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        confirm: Callable[[str], Awaitable[None]],
        send_error: Callable[[str], Awaitable[None]],
        interaction: discord.Interaction | None = None,
    ) -> None:
        """Lógica de `/poker` y `.poker`: abre (o baja) la mesa y, con cantidad, te sienta."""
        if guild is None or not isinstance(channel, discord.abc.Messageable):
            await send_error("El póker solo se juega dentro de un servidor.")
            return
        if error := casino_channel_error(self.casino_channel_ids, channel, "El póker"):
            await send_error(error)
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
            self.set_ante(guild.id, user.id, stake)
        table, new = await self.ensure_table(guild.id, channel, user.id)
        if table is None:
            await send_error("Se está abriendo la mesa en este canal; un segundo.")
            return
        if not new and stake is None:
            try:
                await table.move()
            except PokerError as error:
                await send_error(str(error))
                return
        if stake is None:
            await confirm("")
            return
        try:
            await table.sit(user, interaction=interaction)
        except InsufficientFundsError as error:
            await send_error(insufficient_text(error.balance, stake))
            return
        except PokerError as error:
            await send_error(str(error))
            return
        table.edit_soon()
        await confirm(
            f"🪑 Sentado con **{format_amount(stake)}** de ante. Pulsa 🃏 ¡Cartas! en la mesa "
            "cuando quieras."
        )

    async def play_mode(
        self,
        mode: str,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        confirm: Callable[[str], Awaitable[None]],
        send_error: Callable[[str], Awaitable[None]],
        interaction: discord.Interaction | None = None,
    ) -> None:
        """Juega al modo elegido: `banca` (esta mesa) o `timba` (la del cog `Timba`).

        En la timba, `amount_text` es la ciega grande de una mesa nueva. La timba se
        busca con `find_cog`: si no está cargada, se avisa.
        """
        if mode == TIMBA:
            timba = find_cog(self.bot, Timba)
            if timba is None:
                await send_error("La timba no está disponible ahora mismo.")
                return
            await timba.open_and_sit(
                guild=guild,
                channel=channel,
                user=user,
                blind_text=amount_text,
                confirm=confirm,
                send_error=send_error,
                interaction=interaction,
            )
            return
        await self._poker_impl(
            guild=guild,
            channel=channel,
            user=user,
            amount_text=amount_text,
            confirm=confirm,
            send_error=send_error,
            interaction=interaction,
        )

    @app_commands.command(
        name="poker",
        description="Póker: contra la banca o timba entre jugadores. Abre la mesa del canal.",
    )
    @app_commands.describe(
        modo="Contra la banca (Casino Hold'em) o timba entre jugadores. Si no, te lo pregunta.",
        cantidad="Contra la banca: tu ante (te sienta). En la timba: la ciega grande.",
    )
    @app_commands.choices(
        modo=[app_commands.Choice(name=label, value=key) for key, (label, _) in MODES.items()]
    )
    @app_commands.guild_only()
    async def poker(
        self,
        interaction: discord.Interaction,
        modo: app_commands.Choice[str] | None = None,
        cantidad: str | None = None,
    ) -> None:
        """Abre la mesa de póker del modo elegido en el canal (o la baja) y te sienta.

        Sin `modo`, contesta en privado con un botón por modo (`ModePicker`), como `/lista` con la
        prioridad. Solo en los canales del casino que elige `garito` (sin elegir, los de
        `CASINO_CHANNEL_IDS`) y en sus hilos.
        """
        if modo is None:
            picker = ModePicker(self, interaction.user, cantidad, prompt=interaction)
            await interaction.response.send_message(picker.question(), view=picker, ephemeral=True)
            return
        # Abrir la mesa dibuja la imagen y publica el mensaje: puede pasar de 3 s.
        await interaction.response.defer(ephemeral=True, thinking=True)

        async def confirm(text: str) -> None:
            await interaction.followup.send(text or "Mesa abierta: abajo del todo.", ephemeral=True)

        async def send_error(text: str) -> None:
            await interaction.followup.send(text, ephemeral=True)

        await self.play_mode(
            modo.value,
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            confirm=confirm,
            send_error=send_error,
            interaction=interaction,
        )
        await renta.remind(self.bot, interaction)

    @commands.command(name="poker")
    @commands.guild_only()
    async def poker_text(
        self, ctx: commands.Context, primero: str | None = None, segundo: str | None = None
    ) -> None:
        """Versión de texto: `.poker`, `.poker banca 500`, `.poker timba 50` o `.poker 500`.

        Sin modo, pregunta con los botones en un mensaje que solo puede pulsar quien
        lo pidió.
        """
        mode = parse_mode(primero)
        amount = segundo if mode is not None else primero
        if mode is None:
            picker = ModePicker(self, ctx.author, amount, channel=ctx.channel)
            picker.message = await ctx.send(
                picker.question(), view=picker, allowed_mentions=NO_MENTIONS
            )
            return

        async def confirm(text: str) -> None:
            if not text:
                return
            # Sin mensajes privados en comandos de texto: una reacción basta.
            try:
                await ctx.message.add_reaction(MODES[mode][1])
            except discord.HTTPException:
                await ctx.send(text, allowed_mentions=NO_MENTIONS)

        await self.play_mode(
            mode,
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            amount_text=amount,
            confirm=confirm,
            send_error=ContextResponder(ctx).send_error,
        )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía y el club del bot."""
    await bot.add_cog(
        Poker(
            bot,
            economy=bot.economy,
            club=bot.poker,
            casino_channel_ids=bot.casino_channels,
        )
    )
