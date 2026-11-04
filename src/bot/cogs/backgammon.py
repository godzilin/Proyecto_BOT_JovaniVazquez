"""Backgammon: `backgammon`, contra Jovani (la máquina) o en duelo contra otra persona.

- `backgammon` abre tu mesa contra Jovani, un panel que solo toca su dueño. Antes
  de empezar se elige el nivel de Jovani (de 🍹 en el chiringuito a 🔥 con café de
  Puerto Rico) y 🎲 **Empezar** hace la tirada de salida: sale quien saque más.
  En tu turno mueves **ficha a ficha** con un desplegable por dado («Mover un 5»),
  ↩️ deshace y ✅ confirma la jugada; entonces Jovani tira, mueve y te salen tus
  dados. 🏳️ te rinde (si ya ha empezado a sacar y tú no, es gammon) y 🎯 enseña tu
  ficha y los retos de hoy. Contra Jovani **no se apuesta**: es un juego de
  habilidad y con cuotas fijas quien juega bien sacaría dinero sin parar. Pagan
  los tres **retos del día** y la racha de días jugando (`bot.services.backgammon`).
- `backgammon @alguien [cantidad]` le reta a un duelo, con apuesta por cabeza o
  amistoso sin cantidad. Al aceptar se sortean los colores y se tira la salida.
  Con apuesta hay **cubo de doblar** (hasta ×8): antes de tirar, quien tiene el
  cubo (o nadie lo tiene) puede doblar; el rival acepta (y se queda el cubo) o
  pasa (y pierde lo que valía la partida). El gammon cobra ×2 y el backgammon ×3,
  multiplicados por el cubo. Cada turno dura `TURN_SECONDS`: quien se duerme,
  pierde. Mientras se juegan los primeros `GRADA_TURNS` turnos, el público
  apuesta a la porra por ⚪ o ⚫ con las reglas de la grada de los gallos.

Cada jugada es un GIF (`bot.services.backgammon_scene`, canvas en Node): los
dados ruedan, las fichas vuelan en arco y las comidas se van a la barra. **Se
pinta antes del clic** (`bot.utils.ahead`): en cada momento la mesa pinta en
segundo plano lo de cada opción del desplegable, ✅ (con la respuesta de Jovani y
tus dados siguientes ya sorteados), 🏳️, la tirada de salida de cada nivel, y en
los duelos tirar, doblar, aceptar, pasar y dormirse. Al pulsar solo queda subirlo.

Dinero (`EconomyService`):

- Retos del día: premio con IRPF (`pay_income`), como ganancia patrimonial que
  no procede de una transmisión (art. 33.1 LIRPF), igual que los del 4 en raya.
- Duelos y grada: juego, como el resto del casino (premios como ganancia
  patrimonial de la base general, art. 33.1 LIRPF, y pérdidas que solo
  compensan ganancias de juego, art. 33.5.d LIRPF): entra en la retención diaria
  del casino y en la renta semanal. Al aceptar se retiene a cada jugador
  `escrow` (la apuesta ×3, que cubre un backgammon) con `place_bet`; cada doblado
  retiene lo que cubre el cubo nuevo (`double_extra`), a quien dobla al doblar y
  a quien acepta al aceptar. Al acabar se devuelve a cada uno lo retenido menos
  lo que perdió o más lo que ganó (`duel_returns`) con `pay_winnings`, antes de
  enseñar el final. La comisión se destruye (es el margen de la casa, no un
  impuesto). La grada, como la del 4 en raya. Si el bot se apaga a mitad de un
  duelo, se devuelve todo lo retenido.

Solo se juega en los canales del casino que elige `garito` (sin elegir, los de `CASINO_CHANNEL_IDS`)
y en sus hilos. Permisos del bot en el canal: enviar mensajes, insertar enlaces y adjuntar archivos.
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
import secrets
import time
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs import pets as mascotas
from bot.cogs import shop as tienda
from bot.cogs.casino import casino_channel_error, insufficient_text
from bot.repositories.backgammon import BackgammonRepository, GameRecord
from bot.services.achievements import (
    StatDelta,
    backgammon_bet_stats,
    backgammon_stats,
    casino_stats,
)
from bot.services.backgammon import (
    BAR,
    DEFAULT_LEVEL,
    GAME,
    GRADA_TURNS,
    LEVEL_BY_KEY,
    LEVELS,
    MAX_CUBE,
    OFF,
    TIER_NAMES,
    Ending,
    GameEvent,
    Level,
    PlayerRecord,
    Position,
    Side,
    Step,
    Turn,
    at_risk,
    can_double,
    daily_challenges,
    dice_of,
    double_extra,
    duel_returns,
    escrow,
    event_for,
    format_play,
    format_step,
    game_kind,
    grada_odds,
    grada_open,
    grada_payouts,
    grada_totals,
    jovani_play,
    legal_steps,
    opening_roll,
    plan_seed,
    progress_for,
    remaining_dice,
    resign_kind,
    reward_for,
)
from bot.services.backgammon_render import (
    Cube,
    Media,
    Player,
    banner,
    board_state,
    chain,
    cube_steps,
    end_steps,
    meta_state,
    move_steps,
    opening_steps,
    rest_dice,
    roll_steps,
)
from bot.services.backgammon_scene import BackgammonScene
from bot.services.casino_channels import CasinoChannelIds
from bot.services.economy import (
    BalanceLimitError,
    BetSettlement,
    EconomyService,
    InsufficientFundsError,
    format_amount,
    gambling_tax_line,
    parse_amount,
)
from bot.services.gallos import DUEL_CUT, POOL_CUT, PoolBet, duel_prize, format_odds
from bot.services.levels import TIMEZONE
from bot.services.pets import Event, Moment, bet_moment
from bot.services.poker_club import streak_multiplier_pct
from bot.services.telemetry import TimedLock
from bot.utils.ahead import Ahead
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

COMMAND = "backgammon"
#: Lo que aguanta abierta la mesa contra Jovani sin tocarla (menos que los 15 min del token).
VIEW_TIMEOUT = 600
#: Cuánto espera un reto a que lo acepten.
CHALLENGE_SECONDS = 120
#: Lo que dura un turno (o pensarse un doblado) en un duelo; quien se duerme, pierde.
TURN_SECONDS = 90
#: Desde aquí se anuncia en el canal un duelo ganado.
SHOUT_NET = 10_000
#: Margen tras el GIF antes de enseñar el final: lo que tarda en llegar y empezar.
REVEAL_MARGIN_SECONDS = 0.5
#: Pausa quieta entre tu jugada y la tirada de Jovani en el GIF.
THINK_MS = 350
GIF_NAME = "backgammon.gif"
PNG_NAME = "backgammon.png"
TITLE = "BACKGAMMON"
JOVANI = "Jovani"
#: Lo que vale una partida, por `game_kind`.
KIND_NAMES = {1: "partida sencilla", 2: "gammon", 3: "backgammon"}

COLOR_IDLE = discord.Color.from_rgb(46, 122, 80)
COLOR_WIN = discord.Color.from_rgb(255, 196, 0)
COLOR_LOST = discord.Color.from_rgb(80, 84, 92)
COLOR_DUEL = discord.Color.from_rgb(150, 34, 40)

# -- Frases ---------------------------------------------------------------------------------

#: Lo que dice Jovani cuando te come una ficha.
JOVANI_HITS = (
    "«¡A la barra!»",
    "«Ficha comida.»",
    "«¿Qué? ¿Qué? ¡Toma mangó! A la barra.»",
    "«Esa ficha estaba sola y con frío.»",
    "«Comida con más hambre que Hacienda en junio.»",
    "«Ficha suelta, ficha que vuela.»",
    "«Te la mando de vuelta a empezar.»",
    "«Desayuna con huevo… y con ficha.»",
    "«Ñam.»",
)
#: Lo que dice cuando le comes una suya.
JOVANI_HIT = (
    "«Me has comido una.»",
    "«Eso duele.»",
    "«Recurro al Constitucional.»",
    "«Sofía, cállate, que me desconcentro.»",
    "«Estoy destruido, pero por dentro estoy nuevo.»",
    "«Vale, una a la barra. Ya volverá.»",
    "«Me la has birlado delante de mis narices.»",
    "«Eso no venía en el Manual de resistencia.»",
    "«Lagartijo, iguana, lagarto… y ficha comida.»",
)
#: Lo que dice cuando saca dobles.
JOVANI_DOUBLES = (
    "«¡Dobles!»",
    "«¡Dobles! Ni el Gordo de Navidad.»",
    "«Doble ración, como el café: ¡quiero café!»",
    "«¡Dobles y para casa!»",
    "«¡Dobles! ¡Azul, azul, como el mar azul!»",
    "«¡Dobles! Esto está trucado… a mi favor.»",
    "«¡Dobles! Como los sueldos de los ministros.»",
)
#: Lo que dice cuando no puede mover.
JOVANI_STUCK = (
    "«No me puedo mover.»",
    "«Bloqueado como el CGPJ.»",
    "«Atado y bien atado, pero al revés.»",
    "«Me has encerrado.»",
)
#: Lo que dice de vez en cuando en una jugada sin más.
JOVANI_CHATTER = (
    "«¿Con pulpa? ¡Sin pulpa!»",
    "«Cuento los pips como Hacienda tus tiques.»",
    "«Yo soy yo… y así me tienes que soportar.»",
    "«Y Víctor le dijo a Joel: móntate en mi ficha.»",
    "«Muevo con calma.»",
    "«Esta partida la tengo controlada.»",
    "«Me lo ha recomendado el algoritmo de TikTok.»",
    "«Hazme café, que esto es largo.»",
    "«Ficha al seis y a esperar.»",
    "«Jota Uve, ¡pre!»",
    "«Muevo con la calma de un pleno del Senado.»",
    "«Cinco mil años de juego y aún me sorprende.»",
)
WIN_LINES = (
    "¡Le ganas a Jovani!",
    "¡Quince fuera!",
    "¡Toma mangó!",
    "¡Jovani pide recuento de pips!",
    "¡Fichas fuera y para casa!",
    "Jovani se rasca la cabeza.",
    "¡Victoria en el tablero!",
)
GAMMON_LINES = (
    "¡Gammon! Jovani no ha sacado ni una.",
    "¡Gammon! Paliza doble.",
    "¡Gammon! Ni el NO-DO lo maquilla.",
)
BACKGAMMON_LINES = (
    "¡BACKGAMMON! Con una ficha suya todavía en tu casa.",
    "¡BACKGAMMON! Paliza triple.",
    "¡BACKGAMMON! Jovani pide que no se lo cuentes a nadie.",
)
LOSE_LINES = (
    "¡Jovani saca las quince antes!",
    "Jovani gana esta.",
    "¡Te ha dejado más atrás que el AVE a Extremadura!",
    "Estoy destruido, pero por fuera… por dentro estoy nuevo.",
    "Los dados no estaban de tu parte.",
    "¡Jovani gobierna con mayoría absoluta!",
)
RESIGN_LINES = (
    "Dimisión irrevocable, como las de antes.",
    "Se rinde.",
    "Bandera blanca.",
    "Se retira con dignidad.",
)
SLEEP_LINES = (
    "se ha dormido como el Senado en agosto",
    "se ha quedado frito como un funcionario en la siesta",
    "se ha dormido con el cubilete en la mano",
    "se ha ido a echar la siesta",
)
DOUBLE_LINES = (
    "dobla la apuesta sin pestañear",
    "sube el cubo con más descaro que una subida de la luz",
    "dobla, que de algo hay que pagar el Falcon",
    "pone el cubo encima de la mesa",
)
DROP_LINES = (
    "No acepta el doblado: retirada a tiempo.",
    "Pasa del cubo. Más vale una derrota pequeña que un rescate.",
    "Se baja del cubo.",
)
WAITING_LINES = (
    "🎲 Los dados ruedan…",
    "⚪ Las fichas vuelan…",
    "👀 Todo el barrio mirando el tablero…",
    "🎲 Rueda, rueda…",
    "☕ Jovani pide otro café…",
)


def short_name(name: str) -> str:
    """Nombre recortado para las listas."""
    return name if len(name) <= 16 else name[:15] + "…"


def player_line(record: PlayerRecord) -> str:
    """`12 partidas · 8 victorias · 2 gammons · racha 3 · mejor 5`."""
    if not record.games:
        return "Aún no ha jugado ninguna partida."
    parts = [
        f"{record.games} partida{'s' if record.games != 1 else ''}",
        f"{record.wins} victoria{'s' if record.wins != 1 else ''}",
    ]
    if record.gammons:
        parts.append(f"{record.gammons} gammon{'s' if record.gammons != 1 else ''}")
    if record.streak:
        parts.append(f"🔥 racha {record.streak}")
    parts.append(f"mejor racha {record.best_streak}")
    return " · ".join(parts)


def card_tag(record: PlayerRecord) -> str:
    """La línea pequeña bajo el nombre en el tablero (sin emojis: la escena no los tiene)."""
    if not record.games:
        return "Debuta hoy"
    return f"{record.wins} V · racha {record.streak}"


def roll_text(roll: tuple[int, int]) -> str:
    """`5-3`, o `4-4 (dobles)`."""
    return f"{roll[0]}-{roll[1]}" + (" (dobles)" if roll[0] == roll[1] else "")


def marks_of(steps: list[Step] | tuple[Step, ...]) -> list[int]:
    """Las casillas que toca una jugada (orígenes y destinos), para resaltarlas."""
    return sorted({cell for step in steps for cell in (step.source, step.target)})


def step_value(step: Step) -> str:
    """Valor de una opción del desplegable: `origen:destino`."""
    return f"{step.source}:{step.target}"


# -- El turno en curso ----------------------------------------------------------------------


@dataclass
class Hand:
    """La jugada que alguien está haciendo ficha a ficha: de dónde parte y lo que lleva.

    Attributes:
        start: La posición al empezar el turno (lo que vale si se rinde).
        side: Quien mueve.
        roll: Sus dados.
        steps: Lo que ha movido ya en este turno.
    """

    start: Position
    side: Side
    roll: tuple[int, int]
    steps: list[Step] = field(default_factory=list)
    #: Posición tras cada paso (`positions[0]` es `start`).
    positions: list[Position] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.positions:
            self.positions = [self.start]

    @property
    def position(self) -> Position:
        return self.positions[-1]

    @property
    def dice(self) -> tuple[int, ...]:
        """Los dados que quedan por gastar."""
        dice = dice_of(self.roll)
        for step in self.steps:
            dice = remaining_dice(dice, step)
        return dice

    def legal(self) -> list[Step]:
        """Los pasos que puede dar ahora sin saltarse la regla del máximo de dados."""
        dice = self.dice
        return legal_steps(self.position, self.side, dice) if dice else []

    @property
    def done(self) -> bool:
        """Si ya no le queda nada que mover (o no podía mover nada)."""
        return not self.legal()

    def find(self, value: str, die: int) -> Step | None:
        """El paso legal de una opción del desplegable, o `None` si ya no vale."""
        return next((s for s in self.legal() if step_value(s) == value and s.die == die), None)

    def play(self, step: Step) -> None:
        """Mueve una ficha (ya comprobada con `legal`)."""
        self.positions.append(self.position.apply(self.side, step))
        self.steps.append(step)

    def undo(self) -> None:
        """Deshace la última ficha movida en este turno."""
        if self.steps:
            self.steps.pop()
            self.positions.pop()

    def turn(self) -> Turn:
        """El turno para el registro de la partida."""
        return Turn(self.side, self.roll, tuple(self.steps))

    def key(self) -> tuple[Any, ...]:
        return (self.start, self.side, self.roll, tuple(self.steps))

    def state(self, *, cube: Cube, labels: Side) -> dict[str, Any]:
        """Fotograma quieto de la jugada a medias: dados gastados apagados y lo movido, marcado."""
        return board_state(
            self.position,
            turn=self.side,
            dice=rest_dice(self.side, self.roll, used=len(self.steps)),
            cube=cube,
            labels=labels,
            marks=marks_of(self.steps),
        )


def step_label(step: Step, side: Side) -> tuple[str, str | None]:
    """Etiqueta y descripción de una opción del desplegable (números vistos por `side`)."""
    label = format_step(step, side)
    if step.source == BAR:
        return label, "Entra desde la barra" + (" y come" if step.hit else "")
    if step.target == OFF:
        return label, "Saca la ficha"
    return label, "Come una ficha" if step.hit else None


def add_step_selects(
    view: ui.View,
    hand: Hand,
    callback: Callable[[discord.Interaction, int], Awaitable[None]],
    *,
    disabled: bool = False,
) -> None:
    """Un desplegable por cada dado distinto que queda, con las fichas que puede mover.

    Van en las filas 0 y 1 (con dos dados distintos, uno por fila). Discord admite
    25 opciones por desplegable; con 15 fichas como mucho nunca hay más de 15
    orígenes por dado.
    """
    legal = hand.legal()
    dice = hand.dice
    for row, die in enumerate(sorted(set(dice), reverse=True)):
        steps = [s for s in legal if s.die == die]
        if not steps:
            continue
        left = dice.count(die)
        options = []
        for step in steps[:25]:
            label, description = step_label(step, hand.side)
            options.append(
                discord.SelectOption(label=label, value=step_value(step), description=description)
            )
        placeholder = f"🎲 Mover un {die}" + (f" (quedan {left})" if left > 1 else "")
        select: ui.Select = ui.Select(
            placeholder=placeholder,
            row=row,
            custom_id=f"{GAME}:die{die}",
            options=options,
            disabled=disabled,
        )

        def make(die: int = die) -> Callable[[discord.Interaction], Awaitable[None]]:
            async def chosen(interaction: discord.Interaction) -> None:
                await callback(interaction, die)

            return chosen

        select.callback = make()  # type: ignore[method-assign]
        view.add_item(select)


def hand_text(hand: Hand, labels: Side) -> list[str]:
    """Las líneas del turno en curso: dados, lo movido y qué hacer."""
    lines = [f"🎲 Dados: **{roll_text(hand.roll)}**"]
    if hand.steps:
        lines.append(f"Llevas: **{format_play(hand.steps, labels)}**")
    if not hand.steps and hand.done:
        lines.append("🚧 No puedes mover nada con esos dados. Pulsa ✅ para pasar.")
    elif hand.done:
        lines.append("Jugada completa: ✅ para confirmarla o ↩️ para deshacer.")
    else:
        dice = " y ".join(str(d) for d in hand.dice)
        lines.append(f"Elige ficha en el desplegable (te queda{'n' if len(hand.dice) > 1 else ''} "
                     f"{dice}).")  # fmt: skip
    return lines


# -- Resultados de una jugada ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Advance:
    """Lo que pasa al confirmar una jugada (o al tirar la salida), ya pintado.

    Attributes:
        position: La posición tras todo lo que se ve en el GIF.
        turns: Los turnos completos que entran en el registro.
        hand: El turno que empieza (con sus dados ya tirados), o `None` si acaba.
        media: El GIF y el PNG del final.
        note: Lo que dice Jovani (o lo que hizo el rival).
        winner: Quién ha ganado, si se acaba la partida.
        kind: Lo que vale la partida si se acaba.
    """

    position: Position
    turns: tuple[Turn, ...]
    hand: Hand | None
    media: Media
    note: str | None = None
    winner: Side | None = None
    kind: int = 1


@dataclass(slots=True)
class Settled:
    """Lo que dejó una partida en la ficha de un miembro: retos, racha y Y$ cobrados."""

    record: GameRecord | None = None
    lines: list[str] = field(default_factory=list)
    paid: int = 0
    tax: int = 0
    retos_full: bool = False


def jovani_note(play: tuple[Step, ...], roll: tuple[int, int], rng: random.Random) -> str:
    """Lo que dice Jovani al mover (con su jugada, numerada como la ves tú)."""
    where = f"⚫ Jovani saca **{roll_text(roll)}** y juega **{format_play(play, Side.WHITE)}**."
    if not play:
        return f"{where} {rng.choice(JOVANI_STUCK)}"
    if any(step.hit for step in play):
        return f"{where} {rng.choice(JOVANI_HITS)}"
    if roll[0] == roll[1]:
        return f"{where} {rng.choice(JOVANI_DOUBLES)}"
    if rng.random() < 0.3:
        return f"{where} {rng.choice(JOVANI_CHATTER)}"
    return where


def safe_event(
    log: list[Turn],
    side: Side,
    *,
    start: Position,
    ending: Ending,
    level: Level | None,
    kind: int,
    cube: int = 1,
    stake: int = 0,
) -> GameEvent:
    """La partida vista por `side` (`event_for`); si el registro no cuadra, solo lo básico.

    No debería pasar nunca, pero si pasa, la partida se apunta igual (victoria,
    retos, dinero ya pagado) y el fallo queda en el log, en vez de perderla.
    """
    try:
        return event_for(
            log, side, ending=ending, level=level, kind=kind, cube=cube, stake=stake, start=start
        )
    except ValueError:
        logger.exception("El registro de una partida de backgammon no cuadra")
        return GameEvent(ending, side, level, kind, cube=cube, stake=stake)


def win_headline(kind: int) -> str:
    if kind == 3:
        return random.choice(BACKGAMMON_LINES)
    if kind == 2:
        return random.choice(GAMMON_LINES)
    return random.choice(WIN_LINES)


# -- Contra Jovani --------------------------------------------------------------------------


class SoloTable(ui.View):
    """La mesa de un miembro contra Jovani. Sin dinero: solo los retos pagan.

    El miembro lleva siempre las blancas (abajo) y los puntos se numeran desde su
    lado. `phase` es `start` (antes de la primera partida), `play` u `over`.
    """

    def __init__(
        self, cog: Backgammon, *, guild_id: int, owner: discord.abc.User, channel: object
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        self.channel = channel
        self.level: Level = LEVEL_BY_KEY[DEFAULT_LEVEL]
        self.record = PlayerRecord()
        self.phase = "start"
        self.position = Position.initial()
        #: Desde dónde empieza el registro de la partida (`log`): la salida.
        self.origin = Position.initial()
        self.log: list[Turn] = []
        self.hand: Hand | None = None
        #: Imágenes enseñadas antes de cada ficha movida en este turno (para ↩️) y la de ahora.
        self.shown: list[bytes] = []
        self.shown_png = b""
        self.games = 0
        self.seed = cog.rng.randrange(1, 2**31)
        #: La tirada de salida de la próxima partida y los dados que vienen (secretos).
        self.opening = opening_roll(cog.rng)
        self.upcoming = self._draw()
        self.note: str | None = None
        self.result: str | None = None
        self.message: discord.Message | None = None
        self._last_interaction: discord.Interaction | None = None
        self._busy = False
        self.ahead = Ahead(cog.ahead, name="el backgammon")

    def _draw(self) -> tuple[tuple[int, int], tuple[int, int], int]:
        """Los dados de Jovani, los tuyos siguientes y la semilla del turno."""
        rng = self.cog.rng
        roll = lambda: (rng.randint(1, 6), rng.randint(1, 6))  # noqa: E731
        return roll(), roll(), rng.randrange(1, 2**31)

    # -- Texto e imagen ---------------------------------------------------------------------

    def skin(self) -> dict[str, object]:
        """El tapete que lleva ahora el dueño de la mesa (`tienda.skin_params`)."""
        return tienda.skin_params(self.cog.bot, self.guild_id, self.owner.id, GAME)

    def meta(self, level: Level | None = None) -> dict[str, Any]:
        level = level or self.level
        return meta_state(
            Player(self.owner.display_name, card_tag(self.record)),
            Player(JOVANI, level.name),
            title=TITLE,
            subtitle=f"Contra {level.name}",
            seed=self.seed,
            skin=self.skin(),
        )

    def still_state(self) -> dict[str, Any]:
        if self.hand is not None and self.phase == "play":
            return self.hand.state(cube=Cube(), labels=Side.WHITE)
        return board_state(self.position, labels=Side.WHITE)

    def description(self) -> str:
        if self.result is not None:
            return self.result
        level = self.level
        lines = [f"### {level.emoji} {level.name}", f"*{level.blurb}*"]
        if self.phase == "start":
            lines.append(
                "Elige el nivel y pulsa 🎲 **Empezar**: sale quien saque más en la salida."
            )
        else:
            if self.note:
                lines.append(self.note)
            if self.hand is not None:
                lines += hand_text(self.hand, Side.WHITE)
            pips = (self.hand.position if self.hand else self.position).pip
            lines.append(f"-# Pips: tú {pips(Side.WHITE)} · Jovani {pips(Side.BLACK)}")
        lines.append(f"-# {player_line(self.record)}")
        lines.append("-# Contra Jovani no se apuesta: pagan los 🎯 retos del día.")
        return "\n".join(lines)

    def embed(
        self, *, image: str = PNG_NAME, text: str | None = None, color: discord.Color | None = None
    ) -> discord.Embed:
        embed = discord.Embed(
            title=f"♟️ Backgammon · {self.owner.display_name} 🆚 Jovani",
            description=text if text is not None else self.description(),
            color=color or COLOR_IDLE,
        )
        embed.set_image(url=f"attachment://{image}")
        return embed

    # -- Componentes ------------------------------------------------------------------------

    def rebuild(self) -> None:
        """Monta los desplegables y botones según el momento de la partida."""
        self.clear_items()
        if self.phase != "play" or self.hand is None:
            label = "🎲 Empezar" if self.phase == "start" else "🔁 Revancha"
            self._button(label, "start", self._start, discord.ButtonStyle.success, 0)
            self._button("🎯 Retos", "retos", self._retos, discord.ButtonStyle.secondary, 0)
            self._paint(0)
            self._level_select(1)
            return
        hand = self.hand
        add_step_selects(self, hand, self._step)
        self._button(
            "↩️", "undo", self._undo, discord.ButtonStyle.secondary, 2, disabled=not hand.steps
        )
        self._button(
            "✅ Pasar" if not hand.steps and hand.done else "✅",
            "confirm",
            self._confirm,
            discord.ButtonStyle.success,
            2,
            disabled=not hand.done,
        )
        self._button("🏳️", "resign", self._resign, discord.ButtonStyle.danger, 2)
        self._button("🎯", "retos", self._retos, discord.ButtonStyle.secondary, 2)
        self._paint(2)

    def _paint(self, row: int) -> None:
        """El 🎨: elegir o comprar el tapete de esta mesa."""
        if (paint := tienda.skin_button(self.cog.bot, GAME, row=row)) is not None:
            self.add_item(paint)

    def _level_select(self, row: int) -> None:
        select: ui.Select = ui.Select(
            placeholder="Nivel de Jovani",
            row=row,
            custom_id=f"{GAME}:level",
            options=[
                discord.SelectOption(
                    label=level.name,
                    value=level.key,
                    emoji=level.emoji,
                    description=level.blurb[:100],
                    default=level.key == self.level.key,
                )
                for level in LEVELS
            ],
        )
        select.callback = self._choose_level  # type: ignore[method-assign]
        self.add_item(select)

    def _button(
        self,
        label: str,
        custom_id: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        style: discord.ButtonStyle,
        row: int,
        *,
        disabled: bool = False,
    ) -> None:
        button: ui.Button = ui.Button(
            label=label, style=style, custom_id=f"{GAME}:{custom_id}", row=row, disabled=disabled
        )
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)

    def disable_all(self) -> None:
        for item in self.children:
            if isinstance(item, ui.Button | ui.Select):
                item.disabled = True

    # -- Ciclo de vida ----------------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño juega en su mesa (el 🎨 también es suyo)."""
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message(
            f"Esta mesa es de {self.owner.display_name}. Abre la tuya con `{COMMAND}`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Caduca: si había partida a medias, cuenta como rendirse (para que no se escape nadie)."""
        self.cog.panels.discard(self)
        self.ahead.drop()
        if self.phase == "play" and not self._busy:
            self.phase = "over"
            await self._settle(Ending.RESIGN, resign_kind(self.position, Side.WHITE))
        self.disable_all()
        try:
            if self._last_interaction is not None:
                await self._last_interaction.edit_original_response(view=self)
            elif self.message is not None:
                await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar la mesa del backgammon", exc_info=True)

    # -- Pintar antes del clic --------------------------------------------------------------

    def _key(self) -> tuple[Any, ...]:
        skin = self.skin().get("key")
        if self.phase != "play" or self.hand is None:
            return (self.phase, self.games, self.level.key, self.opening, skin)
        return ("play", self.games, len(self.log), self.hand.key(), self.level.key, skin)

    def prepare(self) -> None:
        """Pinta en segundo plano lo de cada opción de este momento.

        Antes de una partida: la salida con el nivel elegido y el tablero con cada
        otro nivel. En un turno: ✅ si la jugada está completa, cada ficha que se
        puede mover y rendirse.
        """
        key = self._key()
        level, meta = self.level, self.meta()
        if self.phase != "play" or self.hand is None:
            order: list[Hashable] = ["start"]
            order += [f"level:{lv.key}" for lv in LEVELS if lv.key != level.key]

            async def paint_start(item: Hashable) -> Any:
                if item == "start":
                    return await self._opening(level, meta)
                other = LEVEL_BY_KEY[str(item).split(":", 1)[1]]
                return await self.cog.renderer.still(
                    self.meta(other), board_state(Position.initial(), labels=Side.WHITE)
                )

            self.ahead.plan(key, order, paint_start)
            return
        hand = self.hand
        snapshot = Hand(hand.start, hand.side, hand.roll, list(hand.steps), list(hand.positions))
        upcoming = self.upcoming
        order = ["confirm"] if hand.done else []
        order += [(s.die, step_value(s)) for s in hand.legal()]
        order.append("resign")

        async def paint(item: Hashable) -> Any:
            if item == "confirm":
                return await self._advance(snapshot, level, upcoming, meta)
            if item == "resign":
                return await self._resign_media(snapshot.start, meta)
            assert isinstance(item, tuple)
            step = snapshot.find(item[1], item[0])
            if step is None:
                return None
            after = Hand(
                snapshot.start, snapshot.side, snapshot.roll,
                list(snapshot.steps), list(snapshot.positions),
            )  # fmt: skip
            after.play(step)
            return await self.cog.renderer.still(meta, after.state(cube=Cube(), labels=Side.WHITE))

        self.ahead.plan(key, order, paint)

    async def _opening(self, level: Level, meta: dict[str, Any]) -> Advance:
        """La tirada de salida y, si sale Jovani, su jugada y tus dados."""
        roll = self.opening
        start = Position.initial()
        parts = [opening_steps(start, roll, seed=self.seed, labels=Side.WHITE)]
        dice = (max(roll), min(roll))
        if roll[0] > roll[1]:
            hand = Hand(start, Side.WHITE, dice)
            steps, final = chain(*parts)
            media = await self.cog.renderer.animate(meta, steps, final)
            note = f"⚪ Sales tú con **{roll_text(dice)}**."
            return Advance(start, (), hand, media, note)
        rng = plan_seed(self.seed, 1)
        play = await asyncio.to_thread(jovani_play, start, Side.BLACK, dice, level, rng)
        after = start
        for step in play:
            after = after.apply(Side.BLACK, step)
        mine = self.upcoming[1]
        parts.append(move_steps(start, Side.BLACK, play, dice, labels=Side.WHITE))
        parts.append(roll_steps(after, Side.WHITE, mine, seed=self.seed + 1, labels=Side.WHITE))
        steps, final = chain(*parts, pause_ms=THINK_MS)
        media = await self.cog.renderer.animate(meta, steps, final)
        note = "⚫ Sale Jovani. " + jovani_note(play, dice, rng)
        return Advance(
            after, (Turn(Side.BLACK, dice, play),), Hand(after, Side.WHITE, mine), media, note
        )

    async def _advance(
        self,
        hand: Hand,
        level: Level,
        upcoming: tuple[tuple[int, int], tuple[int, int], int],
        meta: dict[str, Any],
    ) -> Advance:
        """Tu jugada confirmada, la de Jovani y tus dados siguientes, en un GIF."""
        mine = hand.position
        turns = [hand.turn()]
        if mine.winner is Side.WHITE:
            kind = game_kind(mine, Side.WHITE)
            end = banner("¡GANAS!", f"{KIND_NAMES[kind].capitalize()} a {level.name}", kind="win")
            steps, final = end_steps(mine, end=end, winner=Side.WHITE, labels=Side.WHITE)
            media = await self.cog.renderer.animate(meta, steps, final)
            return Advance(mine, tuple(turns), None, media, None, Side.WHITE, kind)
        roll_j, roll_h, seed = upcoming
        rng = plan_seed(seed, 0)
        play = await asyncio.to_thread(jovani_play, mine, Side.BLACK, roll_j, level, rng)
        after = mine
        for step in play:
            after = after.apply(Side.BLACK, step)
        turns.append(Turn(Side.BLACK, roll_j, play))
        note = jovani_note(play, roll_j, rng)
        parts = [roll_steps(mine, Side.BLACK, roll_j, seed=seed, labels=Side.WHITE)]
        if after.winner is Side.BLACK:
            kind = game_kind(after, Side.BLACK)
            end = banner("¡GANA JOVANI!", f"{KIND_NAMES[kind].capitalize()}", kind="lose")
            parts.append(
                move_steps(
                    mine, Side.BLACK, play, roll_j, labels=Side.WHITE, end=end, winner=Side.BLACK
                )
            )
            steps, final = chain(*parts, pause_ms=THINK_MS)
            media = await self.cog.renderer.animate(meta, steps, final)
            return Advance(after, tuple(turns), None, media, note, Side.BLACK, kind)
        parts.append(move_steps(mine, Side.BLACK, play, roll_j, labels=Side.WHITE))
        parts.append(roll_steps(after, Side.WHITE, roll_h, seed=seed + 1, labels=Side.WHITE))
        steps, final = chain(*parts, pause_ms=THINK_MS)
        media = await self.cog.renderer.animate(meta, steps, final)
        return Advance(after, tuple(turns), Hand(after, Side.WHITE, roll_h), media, note)

    async def _resign_media(self, position: Position, meta: dict[str, Any]) -> Media:
        kind = resign_kind(position, Side.WHITE)
        sub = "Dimisión irrevocable" if kind == 1 else f"Y es {KIND_NAMES[kind]}"
        end = banner("TE RINDES", sub, kind="lose")
        steps, final = end_steps(position, end=end, labels=Side.WHITE)
        return await self.cog.renderer.animate(meta, steps, final)

    # -- Botones ----------------------------------------------------------------------------

    async def _start(self, interaction: discord.Interaction) -> None:
        """🎲 Empezar / 🔁 Revancha: la tirada de salida (pintada de antes si se puede)."""
        if self._busy or self.phase == "play":
            await interaction.response.defer()
            return
        await ack(interaction)
        self._busy = True
        self._last_interaction = interaction
        try:
            advance = await self.ahead.take(self._key(), "start")
            self.ahead.drop()
            if advance is None:
                advance = await self._cover(interaction, self._opening(self.level, self.meta()))
            if self.phase == "over":
                self.games += 1
            self.phase = "play"
            self.result = None
            self.log = list(advance.turns)
            self.origin = Position.initial()
            self.position = advance.position
            self.hand = advance.hand
            self.shown = []
            self.note = advance.note
            self.opening = opening_roll(self.cog.rng)
            self.upcoming = self._draw()
            self.rebuild()
            await edit(
                interaction,
                embed=self.embed(image=GIF_NAME),
                attachments=[discord.File(io.BytesIO(advance.media.gif), filename=GIF_NAME)],
                view=self,
            )
            self.shown_png = advance.media.png
            self.prepare()
        except discord.HTTPException:
            logger.warning("No se pudo empezar una partida de backgammon", exc_info=True)
        finally:
            self._busy = False

    async def _step(self, interaction: discord.Interaction, die: int) -> None:
        """Una opción de un desplegable: mueve esa ficha y enseña el tablero."""
        values = interaction.data.get("values", []) if interaction.data else []
        hand = self.hand
        if self._busy or hand is None or self.phase != "play" or not values:
            await interaction.response.defer()
            return
        step = hand.find(values[0], die)
        if step is None:
            await interaction.response.send_message(
                "Esa ficha ya no se puede mover.", ephemeral=True
            )
            return
        await ack(interaction)
        self._busy = True
        self._last_interaction = interaction
        try:
            png = await self.ahead.take(self._key(), (die, values[0]))
            self.ahead.drop()
            self.shown.append(self.shown_png)
            hand.play(step)
            if png is None:
                png = await self.cog.renderer.still(
                    self.meta(), hand.state(cube=Cube(), labels=Side.WHITE)
                )
            self.shown_png = png
            self.rebuild()
            await edit(
                interaction,
                embed=self.embed(),
                attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
                view=self,
            )
            self.prepare()
        except discord.HTTPException:
            logger.warning("No se pudo mover una ficha del backgammon", exc_info=True)
        finally:
            self._busy = False

    async def _undo(self, interaction: discord.Interaction) -> None:
        """↩️: deshace la última ficha movida en este turno."""
        hand = self.hand
        if self._busy or hand is None or not hand.steps or not self.shown:
            await interaction.response.defer()
            return
        await ack(interaction)
        self._busy = True
        self._last_interaction = interaction
        try:
            self.ahead.drop()
            hand.undo()
            png = self.shown.pop()
            self.shown_png = png
            self.rebuild()
            await edit(
                interaction,
                embed=self.embed(),
                attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
                view=self,
            )
            self.prepare()
        except discord.HTTPException:
            logger.warning("No se pudo deshacer en el backgammon", exc_info=True)
        finally:
            self._busy = False

    async def _confirm(self, interaction: discord.Interaction) -> None:
        """✅: confirma la jugada; Jovani tira y mueve, y te salen los dados."""
        hand = self.hand
        if self._busy or hand is None or self.phase != "play" or not hand.done:
            await interaction.response.defer()
            return
        await ack(interaction)
        self._busy = True
        self._last_interaction = interaction
        try:
            advance = await self.ahead.take(self._key(), "confirm")
            self.ahead.drop()
            if advance is None:
                advance = await self._cover(
                    interaction, self._advance(hand, self.level, self.upcoming, self.meta())
                )
            self.log += advance.turns
            self.position = advance.position
            self.hand = advance.hand
            self.note = advance.note
            self.shown = []
            if advance.winner is not None:
                ending = Ending.WIN if advance.winner is Side.WHITE else Ending.LOSS
                await self._finish(interaction, advance.media, ending, advance.kind)
                return
            self.upcoming = self._draw()
            self.rebuild()
            await edit(
                interaction,
                embed=self.embed(image=GIF_NAME),
                attachments=[discord.File(io.BytesIO(advance.media.gif), filename=GIF_NAME)],
                view=self,
            )
            # ↩️ vuelve a esta imagen: el PNG del final del GIF.
            self.shown_png = advance.media.png
            self.prepare()
        except discord.HTTPException:
            logger.warning("No se pudo enseñar una jugada del backgammon", exc_info=True)
        finally:
            self._busy = False

    async def _cover(self, interaction: discord.Interaction, work: Awaitable[Any]) -> Any:
        """Tapa la espera: apaga los botones mientras se pinta lo que no estaba pintado."""
        self.disable_all()

        async def show() -> None:
            try:
                await edit(interaction, view=self)
            except discord.HTTPException:
                logger.debug("No se pudieron apagar los botones del backgammon", exc_info=True)

        _, result = await asyncio.gather(show(), work)
        return result

    async def _resign(self, interaction: discord.Interaction) -> None:
        """🏳️: te rindes (cuenta como perder, y como gammon si ya iba a serlo)."""
        if self._busy or self.phase != "play":
            await interaction.response.defer()
            return
        await ack(interaction)
        self._busy = True
        self._last_interaction = interaction
        try:
            media = await self.ahead.take(self._key(), "resign")
            self.ahead.drop()
            if media is None:
                media = await self._cover(
                    interaction, self._resign_media(self.position, self.meta())
                )
            self.hand = None
            await self._finish(
                interaction, media, Ending.RESIGN, resign_kind(self.position, Side.WHITE)
            )
        except discord.HTTPException:
            logger.warning("No se pudo enseñar la rendición del backgammon", exc_info=True)
        finally:
            self._busy = False

    async def _choose_level(self, interaction: discord.Interaction) -> None:
        """Cambia el nivel de Jovani (antes de empezar o entre partidas)."""
        values = interaction.data.get("values", []) if interaction.data else []
        if self._busy or self.phase == "play" or not values or values[0] not in LEVEL_BY_KEY:
            await interaction.response.defer()
            return
        level = LEVEL_BY_KEY[values[0]]
        if self.phase == "over":
            # Entre partidas la imagen es el final de la anterior: solo cambia el texto.
            self.level = level
            self.rebuild()
            await interaction.response.edit_message(view=self)
            self.prepare()
            return
        await ack(interaction)
        self._busy = True
        self._last_interaction = interaction
        try:
            png = await self.ahead.take(self._key(), f"level:{level.key}")
            self.ahead.drop()
            self.level = level
            if png is None:
                png = await self.cog.renderer.still(
                    self.meta(), board_state(Position.initial(), labels=Side.WHITE)
                )
            self.rebuild()
            await edit(
                interaction,
                embed=self.embed(),
                attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
                view=self,
            )
            self.prepare()
        except discord.HTTPException:
            logger.warning("No se pudo cambiar el nivel del backgammon", exc_info=True)
        finally:
            self._busy = False

    async def _retos(self, interaction: discord.Interaction) -> None:
        """🎯: tu ficha y los retos de hoy, en privado."""
        await ack(interaction, new_message=True)
        embed = await self.cog.backgammon_embed(self.guild_id, self.owner)
        await edit(interaction, embed=embed)

    # -- Final ------------------------------------------------------------------------------

    async def _finish(
        self, interaction: discord.Interaction, media: Media, ending: Ending, kind: int
    ) -> None:
        """Enseña el GIF del final, apunta la partida (retos) y luego el resultado."""
        self.phase = "over"
        self.hand = None
        self.disable_all()
        waiting = f"# {random.choice(WAITING_LINES)}"
        await edit(
            interaction,
            embed=self.embed(image=GIF_NAME, text=waiting),
            attachments=[discord.File(io.BytesIO(media.gif), filename=GIF_NAME)],
            view=self,
        )
        settle = asyncio.create_task(self._settle(ending, kind))
        await self.cog.sleep(media.seconds + REVEAL_MARGIN_SECONDS)
        settled = await settle
        self.result = await self._result_text(ending, kind, settled)
        self.rebuild()
        color = COLOR_WIN if ending is Ending.WIN else COLOR_LOST
        await edit(
            interaction,
            embed=self.embed(color=color),
            attachments=[discord.File(io.BytesIO(media.png), filename=PNG_NAME)],
            view=self,
        )
        self.prepare()
        if settled.paid:
            await renta.remind(self.cog.bot, interaction)

    def event(self, ending: Ending, kind: int) -> GameEvent:
        return safe_event(
            self.log, Side.WHITE, start=self.origin, ending=ending, level=self.level, kind=kind
        )

    async def _settle(self, ending: Ending, kind: int) -> Settled:
        """Apunta la partida en la ficha, paga los retos y alimenta los logros."""
        event = self.event(ending, kind)
        settled = await self.cog.settle(self.guild_id, self.owner, event)
        if settled.record is not None:
            self.record = settled.record.after
        delta = backgammon_stats(
            event,
            self.record,
            when=datetime.fromtimestamp(self.cog.wall_clock(), TIMEZONE),
            retos=len(settled.record.completed) if settled.record else 0,
            retos_full=settled.retos_full,
        )
        await logros.track(self.cog.bot, self.guild_id, self.owner, self.channel, delta)
        return settled

    async def _result_text(self, ending: Ending, kind: int, settled: Settled) -> str:
        level = self.level
        lines = []
        if ending is Ending.WIN:
            lines.append(f"# 🏆 {win_headline(kind)}")
            lines.append(
                f"Le ganas a {level.emoji} **{level.name}**: {KIND_NAMES[kind]}"
                f"{f' (×{kind})' if kind > 1 else ''}."
            )
        elif ending is Ending.LOSS:
            lines.append(f"# 💀 {random.choice(LOSE_LINES)}")
            lines.append(f"{level.emoji} **{level.name}** te gana: {KIND_NAMES[kind]}.")
        else:
            lines.append(f"# 🏳️ {random.choice(RESIGN_LINES)}")
            lines.append(
                f"Te rindes ante {level.emoji} **{level.name}**"
                + (f", y cuenta como {KIND_NAMES[kind]}." if kind > 1 else ".")
            )
        lines += settled.lines
        lines.append(f"-# {player_line(self.record)}")
        moment = Moment(Event.WIN if ending is Ending.WIN else Event.LOSE)
        if settled.paid:
            hint = await renta.hint(self.cog.bot, self.guild_id, self.owner.id, moment)
        else:
            hint = await mascotas.cameo(self.cog.bot, self.guild_id, self.owner.id, moment)
        if hint:
            lines.append(hint)
        lines.append("-# 🔁 Revancha para otra · 🎯 tus retos de hoy.")
        return "\n".join(lines)


# -- Duelos ---------------------------------------------------------------------------------


class Phase(Enum):
    """Momento de un duelo."""

    CHALLENGE = "reto"
    PLAYING = "partida"
    DONE = "fin"


class Stage(Enum):
    """Qué espera un duelo en marcha."""

    #: Quien toca decide si dobla o tira.
    DECIDE = "decidir"
    #: Hay un doblado ofrecido: el rival acepta o pasa.
    OFFER = "doblado"
    #: Quien toca mueve sus fichas.
    MOVE = "mover"


@dataclass(slots=True)
class Spectator:
    """Un apostante de grada: su apuesta y la última interacción (para su resultado privado)."""

    member: discord.abc.User
    side: Side
    stake: int
    interaction: discord.Interaction | None = None
    settlement: BetSettlement | None = None
    payout: int = 0

    @property
    def bet(self) -> PoolBet:
        return PoolBet(self.side.corner, self.stake)


class BetModal(ui.Modal, title="🎟️ Apuesta de grada"):
    """Pide cuánto apuesta el público por un color."""

    amount: ui.TextInput = ui.TextInput(
        label="Cantidad (500, 2k, all…)", placeholder="100", max_length=12
    )

    def __init__(self, duel: Duel, side: Side) -> None:
        super().__init__()
        self.duel = duel
        self.side = side

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Cobra la apuesta y la apunta en la grada."""
        # Leer el saldo y cobrar van a la base de datos: se acepta antes.
        await ack(interaction)
        await self.duel.place(interaction, self.side, str(self.amount.value))


class DuelView(ui.View):
    """Los botones de un duelo; quién puede pulsar cada uno lo decide el duelo."""

    def __init__(self, duel: Duel) -> None:
        super().__init__(timeout=None)
        self.duel = duel

    def _button(
        self,
        label: str,
        custom_id: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        row: int = 0,
        disabled: bool = False,
    ) -> None:
        button: ui.Button = ui.Button(
            label=label[:80],
            style=style,
            custom_id=f"{GAME}:{custom_id}",
            row=row,
            disabled=disabled,
        )
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)

    def challenge(self) -> DuelView:
        """Botones del reto: aceptar, rechazar o retirarlo."""
        self.clear_items()
        self._button("✅ Aceptar", "accept", self.duel.accept, style=discord.ButtonStyle.success)
        self._button("❌ Rechazar", "reject", self.duel.reject, style=discord.ButtonStyle.danger)
        self._button("🗑️ Retirar", "withdraw", self.duel.withdraw)
        return self

    def playing(self, *, busy: bool = False) -> DuelView:
        """Lo de cada momento (mover, decidir o contestar un doblado), rendirse y la grada."""
        self.clear_items()
        duel = self.duel
        row = 0
        if duel.stage is Stage.MOVE and duel.hand is not None:
            hand = duel.hand
            add_step_selects(self, hand, duel.step, disabled=busy)
            row = 2
            self._button("↩️", "undo", duel.undo, row=row, disabled=busy or not hand.steps)
            self._button(
                "✅ Pasar" if not hand.steps and hand.done else "✅",
                "confirm",
                duel.confirm,
                style=discord.ButtonStyle.success,
                row=row,
                disabled=busy or not hand.done,
            )
        elif duel.stage is Stage.DECIDE:
            self._button(
                "🎲 Tirar", "roll", duel.roll, style=discord.ButtonStyle.success, disabled=busy
            )
            self._button(
                f"⏫ Doblar a ×{duel.cube.value * 2}",
                "double",
                duel.double,
                style=discord.ButtonStyle.primary,
                disabled=busy,
            )
        else:
            self._button(
                f"✅ Acepto ×{duel.cube.value * 2}",
                "take",
                duel.take,
                style=discord.ButtonStyle.success,
                disabled=busy,
            )
            self._button(
                "🙅 Paso", "drop", duel.drop, style=discord.ButtonStyle.danger, disabled=busy
            )
        self._button("🏳️", "resign", duel.resign, row=row, disabled=busy)
        if (paint := tienda.skin_button(duel.cog.bot, GAME, row=row)) is not None:
            self.add_item(paint)
        if grada_open(len(duel.log)):
            for side in Side:
                self._button(
                    f"{side.emoji} Apostar por {short_name(duel.member(side).display_name)}",
                    f"bet_{side.value}",
                    duel.bet_callback(side),
                    row=row + 1,
                    disabled=busy,
                )
        return self


class Duel:
    """Un duelo entre dos miembros, del reto al resultado, en un mismo mensaje.

    Guarda quién apuesta y cuánto solo para repartir: el dinero se cobra y se
    paga siempre por la economía. `held` es lo que cada jugador tiene retenido.
    """

    def __init__(
        self,
        cog: Backgammon,
        *,
        guild_id: int,
        channel: object,
        challenger: discord.abc.User,
        rival: discord.abc.User,
        stake: int,
    ) -> None:
        self.cog = cog
        self.guild_id = guild_id
        self.channel = channel
        self.challenger = challenger
        self.rival = rival
        self.stake = stake
        self.players: dict[Side, discord.abc.User] = {Side.WHITE: challenger, Side.BLACK: rival}
        #: Ficha de cada jugador, por id (se lee al retar: los colores se sortean al aceptar).
        self.records: dict[int, PlayerRecord] = {}
        self.position = Position.initial()
        #: Desde dónde empieza el registro de la partida (`log`): la salida.
        self.origin = Position.initial()
        self.log: list[Turn] = []
        self.hand: Hand | None = None
        #: A quién le toca (decidir o mover) y qué espera el duelo.
        self.turn = Side.WHITE
        self.stage = Stage.MOVE
        self.cube = Cube()
        self.held: dict[Side, int] = dict.fromkeys(Side, 0)
        self.phase = Phase.CHALLENGE
        self.view = DuelView(self)
        self.message: discord.Message | None = None
        self.crowd: dict[int, Spectator] = {}
        self.interactions: dict[int, discord.Interaction] = {}
        self.settlements: dict[Side, BetSettlement | None] = {}
        self.deadline = 0.0
        self.started_at = 0.0
        self.seed = cog.rng.randrange(1, 2**31)
        #: Sorteado de antemano y en secreto: colores, salida y los dados del siguiente.
        self.swap = cog.rng.random() < 0.5
        self.opening = opening_roll(cog.rng)
        self.next_roll = (cog.rng.randint(1, 6), cog.rng.randint(1, 6))
        self.timer: asyncio.Task[None] | None = None
        self.ahead = Ahead(cog.ahead, name="el backgammon")
        self._lock = TimedLock("backgammon:Duel._lock")
        self._busy = False
        self.note: str | None = None
        #: Imágenes enseñadas antes de cada ficha movida en este turno (para ↩️).
        self.shown: list[bytes] = []
        self.shown_png = b""
        #: Quien acaba de doblar (para los logros y el texto).
        self.doubler: Side | None = None

    def member(self, side: Side) -> discord.abc.User:
        return self.players[side]

    def record_of(self, side: Side) -> PlayerRecord:
        return self.records.get(self.players[side].id, PlayerRecord())

    def side_of(self, user_id: int) -> Side | None:
        for side, member in self.players.items():
            if member.id == user_id:
                return side
        return None

    @property
    def members(self) -> list[discord.abc.User]:
        return [self.challenger, self.rival]

    @property
    def actor(self) -> Side:
        """Quién tiene que hacer algo ahora (el que contesta un doblado, o el que toca)."""
        return self.turn.other if self.stage is Stage.OFFER else self.turn

    # -- Texto e imagen ---------------------------------------------------------------------

    def skin(self) -> dict[str, object]:
        """El tapete de quien reta: en una mesa compartida se ve el de quien la abre."""
        return tienda.skin_params(self.cog.bot, self.guild_id, self.challenger.id, GAME)

    def meta(self, players: dict[Side, discord.abc.User] | None = None) -> dict[str, Any]:
        players = players or self.players
        white, black = players[Side.WHITE], players[Side.BLACK]
        subtitle = (
            f"Duelo · {format_amount(self.stake)} por cabeza" if self.stake else "Duelo amistoso"
        )
        return meta_state(
            Player(white.display_name, card_tag(self.records.get(white.id, PlayerRecord()))),
            Player(black.display_name, card_tag(self.records.get(black.id, PlayerRecord()))),
            title=TITLE,
            subtitle=subtitle,
            seed=self.seed,
            skin=self.skin(),
        )

    def embed(
        self, text: str, *, image: str = PNG_NAME, color: discord.Color = COLOR_DUEL
    ) -> discord.Embed:
        embed = discord.Embed(
            title=f"♟️ Backgammon: {self.challenger.display_name} 🆚 {self.rival.display_name}",
            description=text,
            color=color,
        )
        embed.set_image(url=f"attachment://{image}")
        return embed

    def _money_line(self) -> str:
        if not self.stake:
            return "Duelo **amistoso**: sin apuesta ni cubo, pero cuenta para la ficha y los retos."
        return (
            f"**{format_amount(self.stake)}** por cabeza × cubo (gammon ×2, backgammon ×3). "
            f"Se retiene a cada uno {format_amount(escrow(self.stake, self.cube.value))}; la casa "
            f"se queda el {int(DUEL_CUT * 100)} % de lo que se gana."
        )

    def challenge_text(self) -> str:
        lines = [
            f"# 🥊 {self.challenger.mention} reta a {self.rival.mention} a un backgammon",
            self._money_line(),
        ]
        if self.stake:
            lines.append(
                f"Con cubo de doblar hasta ×{MAX_CUBE}: cada doblado retiene la parte nueva."
            )
        lines.append(
            f"-# {self.rival.display_name} tiene <t:{int(self.deadline)}:R> para aceptar. "
            "Los colores se sortean y sale quien saque más."
        )
        return "\n".join(lines)

    def playing_text(self) -> str:
        position = self.hand.position if self.hand and self.stage is Stage.MOVE else self.position
        lines = [
            f"{side.emoji} {self.players[side].mention} · {position.pip(side)} pips · "
            f"{player_line(self.record_of(side))}"
            for side in Side
        ]
        lines.append(self._money_line())
        if self.stake:
            owner = (
                "en el centro"
                if self.cube.owner is None
                else f"de {self.players[self.cube.owner].display_name}"
            )
            lines.append(f"🎲 Cubo: **×{self.cube.value}** ({owner}).")
        skin = self.skin()
        if skin.get("key") not in (None, "serie"):
            lines.append(f"-# 🎨 Tapete de {self.challenger.display_name}.")
        if self.note:
            lines.append(self.note)
        who = self.players[self.actor].mention
        if self.stage is Stage.OFFER:
            lines.append(
                f"## {self.actor.emoji} {who}: ¿aceptas el cubo a ×{self.cube.value * 2}? · "
                f"decide <t:{int(self.deadline)}:R>"
            )
            lines.append(
                f"-# Si pasas, pierdes {format_amount(at_risk(self.stake, self.cube.value, 1))}."
            )
        elif self.stage is Stage.DECIDE:
            lines.append(
                f"## {self.turn.emoji} Le toca a {who}: 🎲 tirar o ⏫ doblar · "
                f"se duerme <t:{int(self.deadline)}:R>"
            )
        else:
            lines.append(
                f"## {self.turn.emoji} Le toca a {who} · se duerme <t:{int(self.deadline)}:R>"
            )
            if self.hand is not None:
                lines += hand_text(self.hand, self.turn)
                lines.append(f"-# Los puntos van numerados desde las {self.turn.label}.")
        bets = {k: s.bet for k, s in self.crowd.items()}
        if grada_open(len(self.log)) or self.crowd:
            totals = grada_totals(bets)
            odds = grada_odds(bets)
            state = (
                f"abierta los {GRADA_TURNS} primeros turnos"
                if grada_open(len(self.log))
                else "cerrada"
            )
            lines.append(f"### 🎟️ Grada ({state})")
            for side in Side:
                backers = sum(1 for s in self.crowd.values() if s.side is side)
                pays = format_odds(odds[side]) if odds[side] is not None else "sin apuestas"
                lines.append(
                    f"{side.emoji} {format_amount(totals[side])} de {backers} "
                    f"persona{'s' if backers != 1 else ''} · paga {pays}"
                )
            lines.append(
                f"-# La grada se reparte lo apostado entre los que acierten, menos el "
                f"{int(POOL_CUT * 100)} % de la casa."
            )
        return "\n".join(lines)

    async def edit_message(self, **kwargs: Any) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(**kwargs)
        except discord.HTTPException:
            logger.debug("No se pudo editar un duelo de backgammon", exc_info=True)

    # -- Reto -------------------------------------------------------------------------------

    async def open(self, send: Callable[..., Awaitable[discord.Message]]) -> None:
        """Manda el reto y espera a que lo acepten (mientras, pinta la salida)."""
        for member in self.members:
            self.records[member.id] = await self.cog.repository.get(self.guild_id, member.id)
        self.deadline = self.cog.wall_clock() + CHALLENGE_SECONDS
        png = await self.cog.renderer.still(
            self.meta(), board_state(self.position, labels=Side.WHITE)
        )
        sent = await send(
            content=self.rival.mention,
            embed=self.embed(self.challenge_text()),
            file=discord.File(io.BytesIO(png), filename=PNG_NAME),
            view=self.view.challenge(),
            allowed_mentions=discord.AllowedMentions(users=[self.rival]),
        )
        # Un duelo puede pasar de los 15 minutos que vale el token de la respuesta
        # del slash: el mensaje se edita como bot, por el canal.
        partial = getattr(self.channel, "get_partial_message", None)
        self.message = (
            partial(sent.id)
            if isinstance(self.channel, discord.abc.Messageable) and callable(partial)
            else sent
        )
        self.timer = asyncio.create_task(self._expire(), name=f"{GAME}-reto")
        self.prepare()

    async def _expire(self) -> None:
        await self.cog.sleep(CHALLENGE_SECONDS)
        async with self._lock:
            if self.phase is not Phase.CHALLENGE:
                return
            self.phase = Phase.DONE
        self.ahead.drop()
        self.cog.release(self)
        await self.edit_message(
            content=None,
            embed=self.embed("⌛ Nadie contestó al reto. Las fichas vuelven a la caja."),
            view=None,
        )

    async def _only(
        self, interaction: discord.Interaction, user: discord.abc.User, what: str
    ) -> bool:
        if interaction.user.id == user.id:
            return True
        await interaction.response.send_message(what, ephemeral=True)
        return False

    def _colors(self) -> dict[Side, discord.abc.User]:
        if self.swap:
            return {Side.WHITE: self.rival, Side.BLACK: self.challenger}
        return {Side.WHITE: self.challenger, Side.BLACK: self.rival}

    async def _opening_media(self, meta: dict[str, Any]) -> Advance:
        """La tirada de salida: quien saque más mueve con esos dos dados."""
        roll = self.opening
        first = Side.WHITE if roll[0] > roll[1] else Side.BLACK
        dice = (max(roll), min(roll))
        steps, final = opening_steps(self.position, roll, seed=self.seed, labels=first)
        media = await self.cog.renderer.animate(meta, steps, final)
        return Advance(self.position, (), Hand(self.position, first, dice), media)

    async def accept(self, interaction: discord.Interaction) -> None:
        """✅ Aceptar: retiene la apuesta a los dos, sortea los colores y tira la salida."""
        if not await self._only(interaction, self.rival, "Este reto no es para ti."):
            return
        await ack(interaction)
        # La salida se pintó con la clave del reto: se pide con ella, antes de cambiar de fase.
        key = self._key()
        async with self._lock:
            if self.phase is not Phase.CHALLENGE:
                return
            if self.stake and (error := await self._charge_players()):
                await notify(interaction, error)
                return
            self.phase = Phase.PLAYING
            self.interactions[interaction.user.id] = interaction
            self.started_at = time.time()
            self.players = self._colors()
        if self.timer is not None:
            self.timer.cancel()
        advance = await self.ahead.take(key, "opening")
        self.ahead.drop()
        if advance is None:
            advance = await self._opening_media(self.meta())
        assert advance.hand is not None
        self.hand = advance.hand
        self.turn = advance.hand.side
        self.stage = Stage.MOVE
        self.note = (
            f"🎲 Salida: ⚪ {self.opening[0]} · ⚫ {self.opening[1]}. "
            f"Sale {self.turn.emoji} {self.players[self.turn].display_name}."
        )
        self.next_roll = (self.cog.rng.randint(1, 6), self.cog.rng.randint(1, 6))
        self.deadline = self.cog.wall_clock() + TURN_SECONDS
        self.shown_png = advance.media.png
        await edit(
            interaction,
            content=None,
            embed=self.embed(self.playing_text(), image=GIF_NAME),
            attachments=[discord.File(io.BytesIO(advance.media.gif), filename=GIF_NAME)],
            view=self.view.playing(),
        )
        self._arm_timer()
        self.prepare()
        if self.stake:
            await renta.remind(self.cog.bot, interaction)

    async def _charge(self, member: discord.abc.User, amount: int) -> InsufficientFundsError | None:
        """Retiene `amount` más a `member` (`place_bet`); el error si no le llega."""
        try:
            await self.cog.economy.place_bet(self.guild_id, member.id, game=GAME, stake=amount)
        except InsufficientFundsError as error:
            return error
        return None

    async def _charge_players(self) -> str | None:
        """Retiene la apuesta ×3 a los dos; si al segundo no le llega, devuelve la del primero."""
        amount = escrow(self.stake, 1)
        charged: list[discord.abc.User] = []
        for member in (self.rival, self.challenger):
            if error := await self._charge(member, amount):
                for refunded in charged:
                    await self.cog.pay(self.guild_id, refunded.id, amount)
                if member is self.rival:
                    return (
                        f"Para aceptar se retienen {format_amount(amount)} (cubre un backgammon). "
                        + insufficient_text(error.balance, amount)
                    )
                return f"A {member.display_name} ya no le llega para el duelo."
            charged.append(member)
        # Los dos retienen lo mismo, así que da igual qué color salga a cada uno.
        self.held = dict.fromkeys(Side, amount)
        return None

    async def reject(self, interaction: discord.Interaction) -> None:
        """❌ Rechazar: el reto se acaba sin mover dinero."""
        if not await self._only(interaction, self.rival, "Este reto no es para ti."):
            return
        if self._lock.locked():
            # Están aceptando (cobro en la base de datos): sin esperar al candado.
            await interaction.response.defer()
            return
        async with self._lock:
            if self.phase is not Phase.CHALLENGE:
                await interaction.response.defer()
                return
            self.phase = Phase.DONE
        if self.timer is not None:
            self.timer.cancel()
        self.ahead.drop()
        self.cog.release(self)
        await edit(
            interaction,
            content=None,
            embed=self.embed(
                f"🙅 {self.rival.mention} rechaza el reto. Ni con la amnistía se sienta a jugar."
            ),
            view=None,
        )
        await logros.track(
            self.cog.bot,
            self.guild_id,
            self.rival,
            self.channel,
            StatDelta(add={"backgammon_rejects": 1}),
        )
        await logros.track(
            self.cog.bot,
            self.guild_id,
            self.challenger,
            self.channel,
            StatDelta(add={"backgammon_rejected": 1}),
        )

    async def withdraw(self, interaction: discord.Interaction) -> None:
        """🗑️ Retirar: quien reta se echa atrás antes de que acepten."""
        if not await self._only(interaction, self.challenger, "Solo quien reta puede retirarlo."):
            return
        if self._lock.locked():
            # Están aceptando (cobro en la base de datos): sin esperar al candado.
            await interaction.response.defer()
            return
        async with self._lock:
            if self.phase is not Phase.CHALLENGE:
                await interaction.response.defer()
                return
            self.phase = Phase.DONE
        if self.timer is not None:
            self.timer.cancel()
        self.ahead.drop()
        self.cog.release(self)
        await edit(
            interaction,
            content=None,
            embed=self.embed("🗑️ Reto retirado. Aquí no ha pasado nada."),
            view=None,
        )

    # -- Grada ------------------------------------------------------------------------------

    def bet_callback(self, side: Side) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await self._open_bet(interaction, side)

        return callback

    async def _open_bet(self, interaction: discord.Interaction, side: Side) -> None:
        """Abre el formulario de la apuesta (las comprobaciones solo miran memoria)."""
        if self.phase is not Phase.PLAYING or not grada_open(len(self.log)):
            await interaction.response.send_message("La grada ya está cerrada.", ephemeral=True)
            return
        if self.side_of(interaction.user.id) is not None:
            await interaction.response.send_message(
                "Tú ya te juegas la partida: la grada es para el público.", ephemeral=True
            )
            return
        if interaction.user.id in self.crowd:
            await interaction.response.send_message(
                "Ya tienes tu apuesta en este duelo.", ephemeral=True
            )
            return
        await interaction.response.send_modal(BetModal(self, side))

    async def place(self, interaction: discord.Interaction, side: Side, text: str) -> None:
        """Cobra una apuesta de grada (el formulario ya está aceptado)."""
        user = interaction.user
        balance = await self.cog.economy.balance(self.guild_id, user.id)
        try:
            stake = parse_amount(text, balance)
        except ValueError as error:
            await notify(interaction, str(error))
            return
        if stake <= 0 or stake > balance:
            await notify(interaction, insufficient_text(balance, stake))
            return
        async with self._lock:
            if self.phase is not Phase.PLAYING or not grada_open(len(self.log)):
                await notify(interaction, "La grada ya está cerrada.")
                return
            if user.id in self.crowd:
                await notify(interaction, "Ya tienes tu apuesta en este duelo.")
                return
            try:
                await self.cog.economy.place_bet(self.guild_id, user.id, game=GAME, stake=stake)
            except InsufficientFundsError as error:
                await notify(interaction, insufficient_text(error.balance, stake))
                return
            self.crowd[user.id] = Spectator(user, side, stake, interaction)
        await notify(
            interaction,
            f"🎟️ {format_amount(stake)} por {side.emoji} **{self.member(side).display_name}**. "
            "Te digo cómo acaba en cuanto salga la última ficha.",
        )
        if not self._busy:
            await self.edit_message(embed=self.embed(self.playing_text()), view=self.view.playing())
        await renta.remind(self.cog.bot, interaction)

    # -- Pintar antes del clic --------------------------------------------------------------

    def _key(self) -> tuple[Any, ...]:
        skin = self.skin().get("key")
        if self.phase is Phase.CHALLENGE:
            return ("reto", skin)
        hand = self.hand.key() if self.hand is not None and self.stage is Stage.MOVE else None
        return ("duel", len(self.log), self.stage, self.turn, self.cube, hand, skin)

    def prepare(self) -> None:
        """Pinta en segundo plano lo de cada botón de este momento.

        En el reto, la salida. Al mover: ✅ si la jugada está completa, cada ficha y
        dormirse. Al decidir: tirar, doblar y dormirse. Con un doblado: aceptar,
        pasar y dormirse. Rendirse, de los dos, siempre al final.
        """
        meta = self.meta()
        if self.phase is Phase.CHALLENGE:
            # Los colores ya están sorteados (en secreto): se pinta con los que saldrán.
            colored = self.meta(self._colors())
            self.ahead.plan(self._key(), ["opening"], lambda _i: self._opening_media(colored))
            return
        if self.phase is not Phase.PLAYING:
            return
        position, turn, cube, roll = self.position, self.turn, self.cube, self.next_roll
        order: list[Hashable] = []
        snapshot = None
        if self.stage is Stage.MOVE and self.hand is not None:
            hand = self.hand
            snapshot = Hand(
                hand.start, hand.side, hand.roll, list(hand.steps), list(hand.positions)
            )
            order = ["confirm"] if hand.done else []
            order += [(s.die, step_value(s)) for s in hand.legal()]
        elif self.stage is Stage.DECIDE:
            order = ["roll", "double"]
        else:
            order = ["take", "drop"]
        order += ["timeout", *Side]

        async def paint(item: Hashable) -> Any:
            if item == "confirm":
                assert snapshot is not None
                return await self._after_move(snapshot, cube, roll, meta)
            if item == "roll":
                return await self._roll_media(position, turn, roll, cube, meta)
            if item == "double":
                after = Cube(cube.value * 2, cube.owner, offered=True)
                return await self.cog.renderer.still(
                    meta, board_state(position, turn=turn, cube=after, labels=turn)
                )
            if item == "take":
                return await self._take_media(position, turn, cube, roll, meta)
            if item == "drop":
                return await self._end_media(position, cube, turn, Ending.DROP, meta)
            if item == "timeout":
                loser = turn.other if self.stage is Stage.OFFER else turn
                ending = Ending.DROP if self.stage is Stage.OFFER else Ending.TIMEOUT
                return await self._end_media(position, cube, loser.other, ending, meta)
            if isinstance(item, Side):
                return await self._end_media(position, cube, item.other, Ending.RESIGN, meta)
            assert isinstance(item, tuple) and snapshot is not None
            step = snapshot.find(item[1], item[0])
            if step is None:
                return None
            after_hand = Hand(
                snapshot.start, snapshot.side, snapshot.roll,
                list(snapshot.steps), list(snapshot.positions),
            )  # fmt: skip
            after_hand.play(step)
            return await self.cog.renderer.still(meta, after_hand.state(cube=cube, labels=turn))

        self.ahead.plan(self._key(), order, paint)

    # -- Imágenes ---------------------------------------------------------------------------

    def _end_banner(self, winner: Side, ending: Ending, kind: int) -> dict[str, Any]:
        name = self.players[winner].display_name
        if ending is Ending.TIMEOUT:
            return banner("¡SE DUERME!", f"Gana {name} por tiempo", kind="time")
        if ending is Ending.RESIGN:
            return banner("SE RINDE", f"Gana {name}", kind="lose")
        if ending is Ending.DROP:
            return banner("NO ACEPTA", f"Gana {name} el cubo", kind="lose")
        return banner(f"¡GANA {name.upper()[:14]}!", KIND_NAMES[kind].capitalize(), kind="win")

    async def _end_media(
        self, position: Position, cube: Cube, winner: Side, ending: Ending, meta: dict[str, Any]
    ) -> Media:
        kind = 1 if ending is Ending.DROP else resign_kind(position, winner.other)
        end = self._end_banner(winner, ending, kind)
        steps, final = end_steps(position, end=end, cube=cube, labels=winner)
        return await self.cog.renderer.animate(meta, steps, final)

    async def _roll_media(
        self, position: Position, side: Side, roll: tuple[int, int], cube: Cube, meta: dict
    ) -> Media:
        steps, final = roll_steps(
            position, side, roll, seed=self.seed + len(self.log), cube=cube, labels=side
        )
        return await self.cog.renderer.animate(meta, steps, final)

    async def _take_media(
        self,
        position: Position,
        doubler: Side,
        cube: Cube,
        roll: tuple[int, int],
        meta: dict[str, Any],
    ) -> Media:
        """Aceptar: el cubo pasa a quien acepta al doble y quien dobló tira."""
        offered = Cube(cube.value * 2, cube.owner, offered=True)
        taken = Cube(cube.value * 2, doubler.other)
        steps, final = chain(
            cube_steps(position, offered, taken, turn=doubler, labels=doubler),
            roll_steps(
                position, doubler, roll, seed=self.seed + len(self.log), cube=taken, labels=doubler
            ),
        )
        return await self.cog.renderer.animate(meta, steps, final)

    async def _after_move(
        self, hand: Hand, cube: Cube, roll: tuple[int, int], meta: dict[str, Any]
    ) -> Advance:
        """Una jugada confirmada: si gana, el final; si el rival no puede doblar, su tirada."""
        after = hand.position
        side = hand.side
        if after.winner is side:
            kind = game_kind(after, side)
            end = self._end_banner(side, Ending.WIN, kind)
            steps, final = end_steps(after, end=end, winner=side, cube=cube, labels=side)
            media = await self.cog.renderer.animate(meta, steps, final)
            return Advance(after, (hand.turn(),), None, media, winner=side, kind=kind)
        rival = side.other
        if can_double(cube.value, cube.owner, rival, self.stake):
            png = await self.cog.renderer.still(
                meta, board_state(after, turn=rival, cube=cube, labels=rival)
            )
            media = Media(gif=b"", png=png, seconds=0.0)
            return Advance(after, (hand.turn(),), None, media)
        steps, final = roll_steps(
            after, rival, roll, seed=self.seed + len(self.log) + 1, cube=cube, labels=rival
        )
        media = await self.cog.renderer.animate(meta, steps, final)
        return Advance(after, (hand.turn(),), Hand(after, rival, roll), media)

    # -- Partida ----------------------------------------------------------------------------

    async def _enter(
        self, interaction: discord.Interaction, who: Side | None, stages: tuple[Stage, ...]
    ) -> bool:
        """Comprueba (solo memoria) que quien pulsa puede hacerlo ahora; si no, le contesta."""
        side = self.side_of(interaction.user.id)
        if side is None:
            await interaction.response.send_message(
                "Tú no juegas este duelo. Apuesta en la grada o reta a alguien con "
                f"`{COMMAND} @alguien`.",
                ephemeral=True,
            )
            return False
        if self.phase is not Phase.PLAYING or self._busy or self.stage not in stages:
            await interaction.response.defer()
            return False
        if who is not None and side is not who:
            await interaction.response.send_message("Ahora no te toca.", ephemeral=True)
            return False
        return True

    def _show_media(self, media: Media) -> tuple[str, bytes]:
        """El GIF si lo hay; si no (solo cambia la mesa), el PNG."""
        return (GIF_NAME, media.gif) if media.gif else (PNG_NAME, media.png)

    async def _show(self, interaction: discord.Interaction, media: Media) -> None:
        name, data = self._show_media(media)
        self.shown_png = media.png
        await edit(
            interaction,
            embed=self.embed(self.playing_text(), image=name),
            attachments=[discord.File(io.BytesIO(data), filename=name)],
            view=self.view.playing(),
        )

    async def _cover(self, interaction: discord.Interaction, work: Awaitable[Any]) -> Any:
        """Tapa la espera: apaga los botones mientras se pinta lo que no estaba pintado."""
        self.view.playing(busy=True)

        async def show() -> None:
            try:
                await edit(interaction, view=self.view)
            except discord.HTTPException:
                logger.debug("No se pudo apagar el duelo", exc_info=True)

        _, result = await asyncio.gather(show(), work)
        return result

    async def step(self, interaction: discord.Interaction, die: int) -> None:
        """Una opción de un desplegable: quien toca mueve esa ficha."""
        if not await self._enter(interaction, self.turn, (Stage.MOVE,)):
            return
        values = interaction.data.get("values", []) if interaction.data else []
        hand = self.hand
        step = hand.find(values[0], die) if hand is not None and values else None
        if step is None or hand is None:
            await interaction.response.send_message(
                "Esa ficha ya no se puede mover.", ephemeral=True
            )
            return
        await ack(interaction)
        self.interactions[interaction.user.id] = interaction
        async with self._lock:
            if self._busy or self.hand is not hand or hand.find(values[0], die) is None:
                return
            self._busy = True
            try:
                png = await self.ahead.take(self._key(), (die, values[0]))
                self.ahead.drop()
                self.shown.append(self.shown_png)
                hand.play(step)
                if png is None:
                    png = await self.cog.renderer.still(
                        self.meta(), hand.state(cube=self.cube, labels=self.turn)
                    )
                self.shown_png = png
                await edit(
                    interaction,
                    embed=self.embed(self.playing_text()),
                    attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
                    view=self.view.playing(),
                )
                self.prepare()
            except discord.HTTPException:
                logger.warning("No se pudo mover una ficha de un duelo", exc_info=True)
            finally:
                self._busy = False

    async def undo(self, interaction: discord.Interaction) -> None:
        """↩️: deshace la última ficha movida en este turno."""
        if not await self._enter(interaction, self.turn, (Stage.MOVE,)):
            return
        hand = self.hand
        if hand is None or not hand.steps or not self.shown:
            await interaction.response.defer()
            return
        await ack(interaction)
        async with self._lock:
            if self._busy or self.hand is not hand or not hand.steps:
                return
            self._busy = True
            try:
                self.ahead.drop()
                hand.undo()
                png = self.shown.pop()
                self.shown_png = png
                await edit(
                    interaction,
                    embed=self.embed(self.playing_text()),
                    attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
                    view=self.view.playing(),
                )
                self.prepare()
            except discord.HTTPException:
                logger.warning("No se pudo deshacer en un duelo", exc_info=True)
            finally:
                self._busy = False

    async def confirm(self, interaction: discord.Interaction) -> None:
        """✅: confirma la jugada. Gana, o le toca al rival (que dobla o tira)."""
        if not await self._enter(interaction, self.turn, (Stage.MOVE,)):
            return
        hand = self.hand
        if hand is None or not hand.done:
            await interaction.response.defer()
            return
        await ack(interaction)
        self.interactions[interaction.user.id] = interaction
        async with self._lock:
            if self._busy or self.hand is not hand:
                return
            self._busy = True
            try:
                advance = await self.ahead.take(self._key(), "confirm")
                self.ahead.drop()
                if advance is None:
                    advance = await self._cover(
                        interaction, self._after_move(hand, self.cube, self.next_roll, self.meta())
                    )
                if self.timer is not None:
                    self.timer.cancel()
                side = hand.side
                self.log += advance.turns
                self.position = advance.position
                self.note = f"{side.emoji} {self.players[side].display_name} juega **" + (
                    f"{format_play(hand.steps, side)}** con {roll_text(hand.roll)}."
                )
                self.shown = []
                if advance.winner is not None:
                    await self._finish(advance.media, advance.winner, Ending.WIN, advance.kind)
                    return
                self.turn = side.other
                self.hand = advance.hand
                self.stage = Stage.MOVE if advance.hand is not None else Stage.DECIDE
                if advance.hand is not None:
                    self.next_roll = (self.cog.rng.randint(1, 6), self.cog.rng.randint(1, 6))
                self.deadline = self.cog.wall_clock() + TURN_SECONDS
                await self._show(interaction, advance.media)
                self._arm_timer()
                self.prepare()
            except discord.HTTPException:
                logger.warning("No se pudo enseñar una jugada de un duelo", exc_info=True)
            finally:
                self._busy = False

    async def roll(self, interaction: discord.Interaction) -> None:
        """🎲 Tirar: quien toca tira sin doblar."""
        if not await self._enter(interaction, self.turn, (Stage.DECIDE,)):
            return
        await ack(interaction)
        self.interactions[interaction.user.id] = interaction
        async with self._lock:
            if self._busy or self.stage is not Stage.DECIDE:
                return
            self._busy = True
            try:
                media = await self.ahead.take(self._key(), "roll")
                self.ahead.drop()
                roll = self.next_roll
                if media is None:
                    media = await self._cover(
                        interaction,
                        self._roll_media(self.position, self.turn, roll, self.cube, self.meta()),
                    )
                if self.timer is not None:
                    self.timer.cancel()
                self.hand = Hand(self.position, self.turn, roll)
                self.stage = Stage.MOVE
                self.note = None
                self.next_roll = (self.cog.rng.randint(1, 6), self.cog.rng.randint(1, 6))
                self.deadline = self.cog.wall_clock() + TURN_SECONDS
                await self._show(interaction, media)
                self._arm_timer()
                self.prepare()
            except discord.HTTPException:
                logger.warning("No se pudo enseñar una tirada de un duelo", exc_info=True)
            finally:
                self._busy = False

    async def double(self, interaction: discord.Interaction) -> None:
        """⏫ Doblar: retiene la parte del cubo nuevo a quien dobla y se lo ofrece al rival."""
        if not await self._enter(interaction, self.turn, (Stage.DECIDE,)):
            return
        await ack(interaction)
        self.interactions[interaction.user.id] = interaction
        async with self._lock:
            if self._busy or self.stage is not Stage.DECIDE:
                return
            if not can_double(self.cube.value, self.cube.owner, self.turn, self.stake):
                await notify(interaction, "Ahora no puedes doblar.")
                return
            self._busy = True
            try:
                extra = double_extra(self.stake, self.cube.value)
                if error := await self._charge(interaction.user, extra):
                    await notify(
                        interaction,
                        f"Para doblar se retienen {format_amount(extra)} más. "
                        + insufficient_text(error.balance, extra),
                    )
                    return
                self.held[self.turn] += extra
                png = await self.ahead.take(self._key(), "double")
                self.ahead.drop()
                offered = Cube(self.cube.value * 2, self.cube.owner, offered=True)
                if png is None:
                    png = await self.cog.renderer.still(
                        self.meta(),
                        board_state(self.position, turn=self.turn, cube=offered, labels=self.turn),
                    )
                if self.timer is not None:
                    self.timer.cancel()
                self.stage = Stage.OFFER
                self.doubler = self.turn
                name = self.players[self.turn].display_name
                self.note = f"⏫ {name} {random.choice(DOUBLE_LINES)}: **×{offered.value}**."
                self.deadline = self.cog.wall_clock() + TURN_SECONDS
                await self._show(interaction, Media(gif=b"", png=png, seconds=0.0))
                self._arm_timer()
                self.prepare()
                await logros.track(
                    self.cog.bot,
                    self.guild_id,
                    interaction.user,
                    self.channel,
                    StatDelta(add={"backgammon_doubles_offered": 1}),
                )
            except discord.HTTPException:
                logger.warning("No se pudo enseñar un doblado", exc_info=True)
            finally:
                self._busy = False

    async def take(self, interaction: discord.Interaction) -> None:
        """✅ Acepto: retiene la parte nueva a quien acepta, que se queda el cubo; el otro tira."""
        if not await self._enter(interaction, self.turn.other, (Stage.OFFER,)):
            return
        await ack(interaction)
        self.interactions[interaction.user.id] = interaction
        async with self._lock:
            if self._busy or self.stage is not Stage.OFFER:
                return
            self._busy = True
            try:
                taker = self.turn.other
                extra = double_extra(self.stake, self.cube.value)
                if error := await self._charge(interaction.user, extra):
                    await notify(
                        interaction,
                        f"Para aceptar se retienen {format_amount(extra)} más. "
                        + insufficient_text(error.balance, extra)
                        + " Solo puedes pasar.",
                    )
                    return
                self.held[taker] += extra
                media = await self.ahead.take(self._key(), "take")
                self.ahead.drop()
                roll = self.next_roll
                if media is None:
                    media = await self._cover(
                        interaction,
                        self._take_media(self.position, self.turn, self.cube, roll, self.meta()),
                    )
                if self.timer is not None:
                    self.timer.cancel()
                self.cube = Cube(self.cube.value * 2, taker)
                self.hand = Hand(self.position, self.turn, roll)
                self.stage = Stage.MOVE
                self.note = (
                    f"✅ {self.players[taker].display_name} acepta: el cubo es suyo a "
                    f"**×{self.cube.value}**."
                )
                self.next_roll = (self.cog.rng.randint(1, 6), self.cog.rng.randint(1, 6))
                self.deadline = self.cog.wall_clock() + TURN_SECONDS
                await self._show(interaction, media)
                self._arm_timer()
                self.prepare()
                await logros.track(
                    self.cog.bot,
                    self.guild_id,
                    interaction.user,
                    self.channel,
                    StatDelta(add={"backgammon_doubles_taken": 1}),
                )
            except discord.HTTPException:
                logger.warning("No se pudo enseñar un doblado aceptado", exc_info=True)
            finally:
                self._busy = False

    async def drop(self, interaction: discord.Interaction) -> None:
        """🙅 Paso: no acepta el doblado y pierde lo que valía la partida antes de doblar."""
        if not await self._enter(interaction, self.turn.other, (Stage.OFFER,)):
            return
        await ack(interaction)
        self.interactions[interaction.user.id] = interaction
        async with self._lock:
            if self._busy or self.stage is not Stage.OFFER:
                return
            self._busy = True
            try:
                if self.timer is not None:
                    self.timer.cancel()
                media = await self.ahead.take(self._key(), "drop")
                self.ahead.drop()
                if media is None:
                    media = await self._end_media(
                        self.position, self.cube, self.turn, Ending.DROP, self.meta()
                    )
                self.note = f"🙅 {random.choice(DROP_LINES)}"
                await self._finish(media, self.turn, Ending.DROP, 1)
            finally:
                self._busy = False

    def _arm_timer(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
        self.timer = asyncio.create_task(self._turn_timeout(self._key()), name=f"{GAME}-turno")

    async def _turn_timeout(self, key: tuple[Any, ...]) -> None:
        await self.cog.sleep(max(0.0, self.deadline - self.cog.wall_clock()))
        async with self._lock:
            if self.phase is not Phase.PLAYING or self._key() != key or self._busy:
                return
            self._busy = True
            try:
                loser = self.actor
                ending = Ending.DROP if self.stage is Stage.OFFER else Ending.TIMEOUT
                media = await self.ahead.take(key, "timeout")
                self.ahead.drop()
                if media is None:
                    media = await self._end_media(
                        self.position, self.cube, loser.other, ending, self.meta()
                    )
                kind = 1 if ending is Ending.DROP else resign_kind(self.position, loser)
                await self._finish(media, loser.other, Ending.TIMEOUT, kind)
            finally:
                self._busy = False

    async def resign(self, interaction: discord.Interaction) -> None:
        """🏳️: el que lo pulsa se rinde (cuando sea, toque o no)."""
        side = self.side_of(interaction.user.id)
        if side is None:
            await interaction.response.send_message("Tú no juegas este duelo.", ephemeral=True)
            return
        if self.phase is not Phase.PLAYING or self._busy:
            await interaction.response.defer()
            return
        await ack(interaction)
        self.interactions[interaction.user.id] = interaction
        async with self._lock:
            if self.phase is not Phase.PLAYING or self._busy:
                return
            self._busy = True
            try:
                if self.timer is not None:
                    self.timer.cancel()
                media = await self.ahead.take(self._key(), side)
                self.ahead.drop()
                if media is None:
                    media = await self._end_media(
                        self.position, self.cube, side.other, Ending.RESIGN, self.meta()
                    )
                await self._finish(
                    media, side.other, Ending.RESIGN, resign_kind(self.position, side)
                )
            finally:
                self._busy = False

    # -- Final ------------------------------------------------------------------------------

    def _ending_for(self, side: Side, winner: Side, ending: Ending) -> Ending:
        """Cómo acaba para `side` un duelo que gana `winner` de la manera `ending`."""
        if side is winner:
            return Ending.WIN
        return Ending.LOSS if ending is Ending.WIN else ending

    async def _finish(self, media: Media, winner: Side, ending: Ending, kind: int) -> None:
        """Paga, enseña el final y apunta la partida de los dos (se llama con el candado).

        Args:
            winner: Quien gana.
            ending: Cómo acaba para quien pierde (`WIN` si fue jugando hasta el final).
            kind: 1, 2 o 3 (lo que vale la partida, antes del cubo).
        """
        self.phase = Phase.DONE
        self.ahead.drop()
        self.hand = None
        if self.stake:
            returns = duel_returns(self.held, self.stake, self.cube.value, kind, winner)
            for side in Side:
                self.settlements[side] = await self.cog.pay(
                    self.guild_id, self.players[side].id, returns[side]
                )
        payouts = grada_payouts({k: s.bet for k, s in self.crowd.items()}, winner)
        for user_id, spectator in self.crowd.items():
            spectator.payout = payouts[user_id]
            spectator.settlement = await self.cog.pay(self.guild_id, user_id, spectator.payout)
        waiting = f"# {random.choice(WAITING_LINES)}"
        if media.gif:
            await self.edit_message(
                content=None,
                embed=self.embed(waiting, image=GIF_NAME),
                attachments=[discord.File(io.BytesIO(media.gif), filename=GIF_NAME)],
                view=None,
            )
        settle = asyncio.create_task(self._settle_both(winner, ending, kind))
        await self.cog.sleep(media.seconds + REVEAL_MARGIN_SECONDS)
        settled = await settle
        lines = self.result_lines(winner, ending, kind)
        for side in Side:
            lines += settled[side].lines
        await self.edit_message(
            content=None,
            embed=self.embed("\n".join(lines), color=COLOR_WIN),
            attachments=[discord.File(io.BytesIO(media.png), filename=PNG_NAME)],
            view=None,
        )
        self.cog.release(self)
        await self.tell_everyone(winner, kind, settled)
        await self.track(winner, ending, kind, settled)

    def net_of(self, side: Side, winner: Side, kind: int) -> int:
        """Lo ganado (o perdido, en negativo) por `side` sin contar lo retenido."""
        if not self.stake:
            return 0
        risked = at_risk(self.stake, self.cube.value, kind)
        return duel_prize(risked) - risked if side is winner else -risked

    def result_lines(self, winner: Side, ending: Ending, kind: int) -> list[str]:
        loser = winner.other
        champ, beaten = self.players[winner], self.players[loser]
        if ending is Ending.TIMEOUT:
            lines = [f"# 😴 {beaten.display_name} {random.choice(SLEEP_LINES)}"]
            lines.append(f"Gana {winner.emoji} {champ.mention} por tiempo.")
        elif ending is Ending.RESIGN:
            lines = [f"# 🏳️ {random.choice(RESIGN_LINES)}"]
            lines.append(f"{beaten.mention} se rinde y gana {winner.emoji} {champ.mention}.")
        elif ending is Ending.DROP:
            lines = [f"# 🙅 {random.choice(DROP_LINES)}"]
            lines.append(
                f"{beaten.mention} no acepta el cubo y gana {winner.emoji} {champ.mention}."
            )
        else:
            lines = [f"# 🏆 ¡Gana {winner.emoji} {champ.display_name}! {win_headline(kind)}"]
            lines.append(f"{champ.mention} saca las quince antes que {beaten.mention}.")
        if kind > 1 and ending is not Ending.DROP:
            lines.append(f"Cuenta como **{KIND_NAMES[kind]}** (×{kind}).")
        if self.stake:
            net = self.net_of(winner, winner, kind)
            cube = f" con el cubo a ×{self.cube.value}" if self.cube.value > 1 else ""
            lines.append(f"Se lleva **+{format_amount(net)}**{cube}.")
        if self.crowd:
            refund = all(s.payout == s.stake for s in self.crowd.values())
            winners = [s for s in self.crowd.values() if s.payout > s.stake]
            if refund:
                lines.append("🎟️ La grada recupera lo suyo.")
            elif winners:
                best = max(winners, key=lambda s: s.payout - s.stake)
                lines.append(
                    f"🎟️ {len(winners)} de {len(self.crowd)} en la grada aciertan. El que más: "
                    f"{best.member.mention}, +{format_amount(best.payout - best.stake)}."
                )
            else:
                lines.append("🎟️ Nadie en la grada lo vio venir.")
        return lines

    def event(self, side: Side, winner: Side, ending: Ending, kind: int) -> GameEvent:
        return safe_event(
            self.log,
            side,
            start=self.origin,
            ending=self._ending_for(side, winner, ending),
            level=None,
            kind=kind,
            cube=self.cube.value,
            stake=self.stake,
        )

    async def _settle_both(self, winner: Side, ending: Ending, kind: int) -> dict[Side, Settled]:
        """Ficha, retos y racha de los dos jugadores."""
        result: dict[Side, Settled] = {}
        for side in Side:
            member = self.players[side]
            event = self.event(side, winner, ending, kind)
            settled = await self.cog.settle(self.guild_id, member, event, name=member.display_name)
            if settled.record is not None:
                self.records[member.id] = settled.record.after
            result[side] = settled
        return result

    async def tell_everyone(self, winner: Side, kind: int, settled: dict[Side, Settled]) -> None:
        """A cada uno, en privado, lo suyo: lo cobrado, Hacienda y su mascota."""
        for side in Side:
            member = self.players[side]
            if not self.stake and not settled[side].paid:
                continue
            net = self.net_of(side, winner, kind)
            stake = at_risk(self.stake, self.cube.value, kind)
            if not self.stake:
                text = "🎯 Retos cobrados."
            elif side is winner:
                text = f"🏆 Ganas el duelo: +{format_amount(net)}"
            else:
                text = f"💀 Pierdes el duelo: -{format_amount(-net)}"
            await self._tell(
                self.interactions.get(member.id), member.id, text, self.settlements.get(side),
                stake, net,
            )  # fmt: skip
            if settled[side].paid and (interaction := self.interactions.get(member.id)):
                await renta.remind(self.cog.bot, interaction)
        for spectator in self.crowd.values():
            net = spectator.payout - spectator.stake
            if spectator.payout == spectator.stake:
                text = f"🎟️ Te devuelven tus {format_amount(spectator.stake)}."
            elif spectator.payout:
                text = (
                    f"🎟️ ¡Acertaste! Cobras {format_amount(spectator.payout)} "
                    f"(+{format_amount(net)})."
                )
            else:
                text = f"🎟️ Fallaste: -{format_amount(spectator.stake)}."
            await self._tell(
                spectator.interaction, spectator.member.id, text, spectator.settlement,
                spectator.stake, net,
            )  # fmt: skip

    async def _tell(
        self,
        interaction: discord.Interaction | None,
        user_id: int,
        text: str,
        settlement: BetSettlement | None,
        stake: int,
        net: int,
    ) -> None:
        if interaction is None:
            return
        if settlement is not None and (tax := gambling_tax_line(settlement)):
            text += f"\n{tax}"
        balance = settlement.balance if settlement is not None else 0
        moment = (
            bet_moment(stake=stake, net=net, balance_after=balance)
            if stake
            else Moment(Event.MONEY)
        )
        if hint := await renta.hint(self.cog.bot, self.guild_id, user_id, moment):
            text += f"\n{hint}"
        try:
            await interaction.followup.send(text, ephemeral=True)
        except discord.HTTPException:
            logger.debug("No se pudo mandar el resultado privado de un duelo", exc_info=True)

    async def track(
        self, winner: Side, ending: Ending, kind: int, settled: dict[Side, Settled]
    ) -> None:
        """Logros y estadísticas del casino de cada uno, después de enseñar el resultado."""
        when = datetime.fromtimestamp(self.cog.wall_clock(), TIMEZONE)
        bets = {k: s.bet for k, s in self.crowd.items()}
        crowd_pool = sum(grada_totals(bets).values())
        refund = all(s.payout == s.stake for s in self.crowd.values())
        risked = at_risk(self.stake, self.cube.value, kind)
        rake = (2 * risked - duel_prize(risked)) // 2 if self.stake else 0
        for side in Side:
            member = self.players[side]
            mine = settled[side]
            won = side is winner
            net = self.net_of(side, winner, kind)
            record = mine.record.after if mine.record else self.record_of(side)
            delta = backgammon_stats(
                self.event(side, winner, ending, kind),
                record,
                when=when,
                net=net,
                crowd_pool=crowd_pool,
                rake=rake,
                rival_slept=won and ending is Ending.TIMEOUT,
                rival_dropped=won and ending is Ending.DROP,
                retos=len(mine.record.completed) if mine.record else 0,
                retos_full=mine.retos_full,
            )
            if not self.stake:
                await logros.track(self.cog.bot, self.guild_id, member, self.channel, delta)
                continue
            settlement = self.settlements.get(side)
            balance = settlement.balance if settlement else 0
            tax = settlement.tax_delta if settlement else 0
            delta.merge(casino_stats(stake=risked, net=net, balance_after=balance, tax_delta=tax))
            await logros.casino_play(
                self.cog.bot, self.guild_id, member, self.channel, delta, net=net
            )
            await apuestas.record(
                self.cog.bot,
                self.guild_id,
                member,
                game=GAME,
                stake=risked,
                net=net,
                balance_after=balance,
                tax=tax,
                details=(("won", int(won)), ("started", int(self.started_at))),
            )
            if won and net >= SHOUT_NET:
                await self.cog.shout(
                    self.channel,
                    f"📣 ♟️ ¡{member.mention} gana un backgammon de "
                    f"{format_amount(self.stake)} por cabeza! **+{format_amount(net)}**",
                    member,
                )
        for spectator in self.crowd.values():
            stake = spectator.stake
            net = spectator.payout - stake
            settlement = spectator.settlement
            balance = settlement.balance if settlement else 0
            tax = settlement.tax_delta if settlement else 0
            won = spectator.side is winner
            odds_cents = spectator.payout * 100 // stake if won and not refund else 0
            delta = backgammon_bet_stats(
                won=won,
                refunded=refund,
                odds_cents=odds_cents,
                rake=0 if refund else int(stake * POOL_CUT),
                when=when,
            )
            delta.merge(casino_stats(stake=stake, net=net, balance_after=balance, tax_delta=tax))
            await logros.casino_play(
                self.cog.bot, self.guild_id, spectator.member, self.channel, delta, net=net
            )
            await apuestas.record(
                self.cog.bot,
                self.guild_id,
                spectator.member,
                game=GAME,
                stake=stake,
                net=net,
                balance_after=balance,
                tax=tax,
                details=(("won", int(won)), ("started", int(self.started_at))),
            )

    async def shutdown(self) -> None:
        """Cierre ordenado: con la partida a medias se devuelve todo lo retenido."""
        async with self._lock:
            if self.phase is Phase.PLAYING:
                for side, member in self.players.items():
                    if self.held[side]:
                        await self.cog.pay(self.guild_id, member.id, self.held[side])
                for user_id, spectator in self.crowd.items():
                    await self.cog.pay(self.guild_id, user_id, spectator.stake)
                self.crowd.clear()
                text = "🛑 Duelo suspendido. Se devuelve lo retenido."
            elif self.phase is Phase.CHALLENGE:
                text = "🛑 Reto cancelado: el bot cierra la mesa."
            else:
                return
            self.phase = Phase.DONE
        if self.timer is not None:
            self.timer.cancel()
        self.ahead.drop()
        await self.edit_message(content=None, embed=self.embed(text), view=None, attachments=[])


# -- Cog ------------------------------------------------------------------------------------


class Backgammon(commands.Cog, name="Backgammon"):
    """Backgammon contra Jovani (con retos del día) y duelos entre miembros con cubo y grada."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        repository: BackgammonRepository,
        casino_channel_ids: CasinoChannelIds = frozenset(),
        rng: random.Random | None = None,
        renderer: BackgammonScene | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        wall_clock: Callable[[], float] = time.time,
        ahead: bool | None = None,
    ) -> None:
        """
        Args:
            ahead: Si las mesas pintan por adelantado cada opción. Por defecto, solo
                con el dibujo de verdad: con un `renderer` de prueba cada dibujo de
                más gastaría el azar de guion.
        """
        self.bot = bot
        self.economy = economy
        self.repository = repository
        self.casino_channel_ids = casino_channel_ids
        # `secrets` usa el azar del sistema operativo: no se puede predecir.
        self.rng = rng or secrets.SystemRandom()
        self.ahead = renderer is None if ahead is None else ahead
        self.renderer = renderer or BackgammonScene()
        self.sleep = sleep
        self.wall_clock = wall_clock
        self.panels: set[SoloTable] = set()
        self.duels: set[Duel] = set()
        #: Quién está en un reto o un duelo (servidor, miembro): uno a la vez.
        self.busy: set[tuple[int, int]] = set()

    async def cog_unload(self) -> None:
        """Al apagar, devuelve lo retenido en los duelos a medias y cierra el pintor."""
        for duel in list(self.duels):
            try:
                await duel.shutdown()
            except Exception:
                logger.exception("No se pudo cerrar un duelo de backgammon al apagar")
        self.duels.clear()
        for panel in list(self.panels):
            panel.ahead.drop()
            panel.stop()
        self.panels.clear()
        await self.renderer.close()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra las fichas y los retos del servidor que deja el bot."""
        await self.repository.forget_guild(guild.id)

    def release(self, duel: Duel) -> None:
        """Libera a los dos jugadores de un duelo terminado."""
        self.duels.discard(duel)
        for member in duel.members:
            self.busy.discard((duel.guild_id, member.id))

    def today(self) -> date:
        return datetime.fromtimestamp(self.wall_clock(), TIMEZONE).date()

    async def pay(self, guild_id: int, user_id: int, amount: int) -> BetSettlement | None:
        """Devuelve lo retenido en un duelo o paga la grada (`pay_winnings`, 0 si pierde todo).

        Tratamiento fiscal: juego (ver la docstring del módulo). `None` si el
        premio pasaría del saldo máximo y no se pudo pagar.
        """
        try:
            return await self.economy.pay_winnings(guild_id, user_id, game=GAME, amount=amount)
        except BalanceLimitError:
            logger.warning("Premio del backgammon por encima del saldo máximo; no se paga.")
            return None

    async def settle(
        self, guild_id: int, member: discord.abc.User, event: GameEvent, *, name: str = ""
    ) -> Settled:
        """Apunta una partida en la ficha de `member` y paga los retos que cumpla.

        Tratamiento fiscal de los retos: premio con retención de IRPF
        (`EconomyService.pay_income`), ganancia patrimonial que no procede de una
        transmisión (art. 33.1 LIRPF), como los retos del 4 en raya.

        Args:
            name: Si se da, las líneas lo nombran (en un duelo salen las de los dos).
        """
        today = self.today()
        retos = daily_challenges(guild_id, member.id, today)
        settled = Settled()
        try:
            record = await self.repository.record_game(
                guild_id,
                member.id,
                event,
                day=today,
                progress=progress_for(retos, event),
                goals={c.key: c.goal for c in retos},
            )
        except Exception:
            logger.exception("No se pudo apuntar una partida del backgammon")
            return settled
        settled.record = record
        who = f"{short_name(name)} " if name else ""
        by_key = {c.key: c for c in retos}
        for key in record.completed:
            challenge = by_key[key]
            gross = reward_for(challenge, record.after.day_streak)
            try:
                paid = await self.economy.pay_income(
                    guild_id, member.id, gross=gross, concept=f"{GAME}:reto:{key}"
                )
            except BalanceLimitError:
                continue
            settled.paid += gross
            settled.tax += paid.tax
            tax = f" (IRPF -{format_amount(paid.tax)})" if paid.tax else ""
            settled.lines.append(
                f"-# 🎯 {who}cumple el reto «{challenge.label}»: +{format_amount(gross)}{tax}"
            )
        done = sum(1 for c in retos if record.progress.get(c.key, 0) >= c.goal)
        if record.completed and done == len(retos):
            settled.retos_full = True
            settled.lines.append(f"-# 🏅 {who}completa los tres retos de hoy.")
        if record.after.day_streak > record.before.day_streak and record.after.day_streak >= 2:
            pct = streak_multiplier_pct(record.after.day_streak)
            settled.lines.append(
                f"-# 🔥 {who}lleva {record.after.day_streak} días seguidos jugando: retos +{pct} %."
            )
        return settled

    async def shout(self, channel: object, text: str, user: discord.abc.User) -> None:
        """Anuncia en el canal los duelos gordos."""
        if not isinstance(channel, discord.abc.Messageable):
            return
        try:
            await channel.send(
                text, allowed_mentions=discord.AllowedMentions(users=[user], everyone=False)
            )
        except discord.HTTPException:
            logger.debug("No se pudo anunciar un duelo de backgammon", exc_info=True)

    async def backgammon_embed(self, guild_id: int, member: discord.abc.User) -> discord.Embed:
        """La sección ♟️ Backgammon de `perfil` (y el botón 🎯): ficha, racha y retos de hoy."""
        record = await self.repository.get(guild_id, member.id)
        today = self.today()
        progress = await self.repository.daily(guild_id, member.id, today)
        ranking = await self.repository.ranking(guild_id, limit=100)
        place = next((i for i, (uid, _r) in enumerate(ranking, 1) if uid == member.id), None)
        alive = record.last_day is not None and (today - record.last_day).days <= 1
        streak = record.day_streak if alive else 0
        lines = [player_line(record)]
        if record.games:
            lines.append(
                f"{record.losses} derrota{'s' if record.losses != 1 else ''} · "
                f"{round(100 * record.wins / record.games)} % de victorias"
            )
        if record.backgammons:
            lines.append(
                f"{record.backgammons} backgammon{'s' if record.backgammons != 1 else ''} ganados"
            )
        if record.best_level >= 0:
            best = LEVELS[record.best_level]
            lines.append(f"Mejor victoria contra Jovani: {best.emoji} {best.name}")
        if place is not None:
            lines.append(f"🏟️ Puesto {place} del servidor")
        pct = streak_multiplier_pct(max(streak, 1))
        days = "día" if streak == 1 else "días"
        lines.append(f"🔥 Racha: **{streak} {days}** jugando (retos +{pct} %, hasta +100 %).")
        lines.append("### 🎯 Retos de hoy")
        for challenge in daily_challenges(guild_id, member.id, today):
            got, done = progress.get(challenge.key, (0, False))
            reward = reward_for(challenge, max(streak, 1))
            mark = "✅" if done else "▫️"
            lines.append(
                f"{mark} **{TIER_NAMES[challenge.tier]}** · {challenge.label} "
                f"({min(got, challenge.goal)}/{challenge.goal}) · {format_amount(reward)}"
            )
        lines.append(f"-# Se renuevan a medianoche. `{COMMAND}` para jugar.")
        return discord.Embed(
            title=f"♟️ Backgammon · {member.display_name}",
            description="\n".join(lines),
            color=COLOR_IDLE,
        )

    # -- backgammon -------------------------------------------------------------------------

    async def _backgammon_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        rival: discord.abc.User | None,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> Duel | None:
        """Lógica compartida de `/backgammon` y `.backgammon`: abre tu mesa o reta a alguien.

        Returns:
            El duelo si ha retado a alguien (para guardar la interacción de quien reta).
        """
        if guild is None:
            await send_error("El backgammon solo se juega dentro de un servidor.")
            return None
        if error := casino_channel_error(self.casino_channel_ids, channel, "El backgammon"):
            await send_error(error)
            return None
        if rival is None:
            if amount_text:
                await send_error(
                    "Contra Jovani no se apuesta: pagan los 🎯 retos del día. "
                    f"Para jugarte dinero, reta a alguien: `{COMMAND} @alguien 500`."
                )
                return None
            await self._open_panel(guild.id, channel, user, send)
            return None
        if rival.id == user.id:
            await send_error("No puedes retarte a ti mismo. Para eso está Jovani.")
            return None
        if rival.bot:
            await send_error(f"Los bots no juegan. Para eso está Jovani: `{COMMAND}`.")
            return None
        stake = 0
        if amount_text:
            balance = await self.economy.balance(guild.id, user.id)
            # Se retiene la apuesta ×3 (cubre un backgammon): «all» es lo máximo que cabe.
            try:
                stake = parse_amount(amount_text, balance // 3)
            except ValueError as error:
                await send_error(str(error))
                return None
            if stake <= 0 or escrow(stake, 1) > balance:
                await send_error(
                    "Al aceptar se retiene la apuesta ×3 (cubre un backgammon). "
                    + insufficient_text(balance, escrow(max(stake, 1), 1))
                )
                return None
        for member in (user, rival):
            if (guild.id, member.id) in self.busy:
                await send_error(f"{member.display_name} ya está en un duelo de backgammon.")
                return None
        duel = Duel(
            self, guild_id=guild.id, channel=channel, challenger=user, rival=rival, stake=stake
        )
        self.busy.update({(guild.id, user.id), (guild.id, rival.id)})
        self.duels.add(duel)
        try:
            await duel.open(send)
        except Exception:
            self.release(duel)
            raise
        await logros.track(
            self.bot, guild.id, user, channel, StatDelta(add={"backgammon_challenges": 1})
        )
        return duel

    async def _open_panel(
        self,
        guild_id: int,
        channel: object,
        user: discord.abc.User,
        send: Callable[..., Awaitable[discord.Message]],
    ) -> None:
        panel = SoloTable(self, guild_id=guild_id, owner=user, channel=channel)
        panel.record = await self.repository.get(guild_id, user.id)
        png = await self.renderer.still(panel.meta(), panel.still_state())
        panel.rebuild()
        panel.message = await send(
            embed=panel.embed(), file=discord.File(io.BytesIO(png), filename=PNG_NAME), view=panel
        )
        self.panels.add(panel)
        panel.prepare()

    @app_commands.command(
        name=COMMAND,
        description="Backgammon: contra Jovani (con retos del día) o reta a alguien, con cubo.",
    )
    @app_commands.describe(
        rival="Persona a la que retar (si no, juegas contra Jovani)",
        cantidad="Apuesta del duelo por cabeza: 500, 2k, all… (sin cantidad, amistoso)",
    )
    @app_commands.guild_only()
    async def backgammon(
        self,
        interaction: discord.Interaction,
        rival: discord.Member | None = None,
        cantidad: str | None = None,
    ) -> None:
        """Abre tu mesa contra Jovani o reta a alguien.

        Solo en los canales del casino que elige `garito` (sin elegir, los de `CASINO_CHANNEL_IDS`)
        y en sus hilos. En un duelo con apuesta se retiene a los dos al aceptar.
        """
        responder = InteractionResponder(interaction)

        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            return await interaction.original_response()

        duel = await self._backgammon_impl(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            rival=rival,
            send=send,
            send_error=responder.send_error,
        )
        if duel is not None:
            # Quien reta recibe aquí su resultado privado del duelo.
            duel.interactions[interaction.user.id] = interaction

    @commands.command(name=COMMAND)
    @commands.guild_only()
    async def backgammon_text(self, ctx: commands.Context, *args: str) -> None:
        """Versión de texto: `.backgammon`, `.backgammon @alguien`, `.backgammon @alguien 500`.

        La cantidad y la mención van en cualquier orden.
        """
        responder = ContextResponder(ctx)
        rival = next((m for m in ctx.message.mentions if m.id != ctx.author.id), None)
        amounts = [a for a in args if not (a.startswith("<@") and a.endswith(">"))]
        if len(amounts) > 1:
            await responder.send_error(f"No entiendo tanto. Ejemplo: `.{COMMAND} @alguien 500`.")
            return

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        await self._backgammon_impl(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            amount_text=amounts[0] if amounts else None,
            rival=rival,
            send=send,
            send_error=responder.send_error,
        )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía y las fichas del backgammon del bot."""
    await bot.add_cog(
        Backgammon(
            bot,
            economy=bot.economy,
            repository=bot.backgammon,
            casino_channel_ids=bot.casino_channels,
        )
    )
