"""Reglas del Autobús (Ride the Bus): cuatro cartas, cuatro preguntas y cobrar a tiempo.

Lógica pura, sin Discord ni dinero. El cog (`bot.cogs.bus`) cobra al
empezar, paga al cobrar y pinta la mesa (`bot.services.bus_scene`).

Se adivina carta a carta, y cada acierto lleva el autobús a la siguiente
parada:

1. **Color:** 🔴 rojo o ⚫ negro.
2. **Altura:** ⬆️ mayor, ⬇️ menor o 🟰 igual que la primera carta.
3. **Rango:** ↔️ dentro, 🔀 fuera o 🎯 en el poste (igual que una de las
   dos primeras).
4. **Palo:** ♠️ picas, ♥️ corazones, ♦️ diamantes o ♣️ tréboles.

Tras cada acierto se puede 💰 cobrar o seguir; si se falla, se pierde todo.
Quien llega al final puede jugarse lo ganado a 🔄 **la vuelta**, un doble o
nada a rojo o negro, o cobrar.

El as es la carta más alta (14). «Mayor» y «menor» son estrictos: si sale la
misma altura, solo gana «igual». Igual con «dentro» y «fuera»: la carta que
cae justo en una de las dos alturas solo la gana «poste».

**Cada carta sale de una baraja entera** (`draw_card`): la que ya ha salido
no cambia lo que viene, como en un zapato de infinitas barajas. Así la
probabilidad de cada opción depende solo de las cartas que hay en la mesa y
la tabla de pagos es fija: con un 7 delante, «mayor» paga siempre lo mismo.
Sin reponer, cada probabilidad dependería de todas las cartas anteriores y
habría miles de filas.

**Pagos.** Acertar una opción con probabilidad `p` multiplica lo que hay en
juego por `1/p`, y la primera mano lleva además el retorno al jugador
(`RTP`, 99 %, como Minas, el Pollo y el Crash):

    multiplicador = 0,99 × (1/p₁) × (1/p₂) × …

Cada mano posterior es una apuesta justa sobre lo que ya hay en juego, así
que cobrar en cualquier parada, con cualquier elección, devuelve de media
el 99 % de lo apostado. Ningún estilo de juego le gana a la casa ni sale
peor parado que otro. Se calcula con fracciones exactas y el cobro se
redondea hacia abajo.

**Las cartas se sortean al empezar** (`BusGame.cards`), igual que el carril
del coche en el Pollo: misma probabilidad que sacarlas en cada mano, y
permite dibujar la animación de la siguiente carta mientras el jugador
piensa (lo que sale no depende de lo que elija) y enseñar al cobrar cuál
venía.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from enum import Enum
from fractions import Fraction

from bot.services.blackjack import SUITS, Card

#: Retorno al jugador: 99 %, ventaja de la casa 1 %.
RTP = Fraction(99, 100)
#: Alturas de la baraja, del 2 al as (14).
HEIGHTS = range(2, 15)
#: Manos normales; la quinta es la vuelta (doble o nada).
HANDS = 4
#: Cartas que se sortean por partida: las cuatro manos y la vuelta.
CARDS = HANDS + 1


def height(card: Card) -> int:
    """Altura de la carta para comparar: del 2 al 14 (el as es la más alta)."""
    return 14 if card.rank == 1 else card.rank


def draw_card(rng: random.Random) -> Card:
    """Una carta de una baraja entera de 52 (con reposición)."""
    return Card(rank=rng.randint(1, 13), suit=rng.randrange(4))


def deal(rng: random.Random) -> tuple[Card, ...]:
    """Las cinco cartas de una partida, en orden."""
    return tuple(draw_card(rng) for _ in range(CARDS))


class Hand(Enum):
    """Las preguntas, en orden. El valor es el número de la mano (1-5)."""

    COLOR = 1
    HEIGHT = 2
    RANGE = 3
    SUIT = 4
    TURN = 5

    @property
    def title(self) -> str:
        """Cómo se llama la mano en el texto."""
        return {
            Hand.COLOR: "¿Rojo o negro?",
            Hand.HEIGHT: "¿Mayor, menor o igual?",
            Hand.RANGE: "¿Dentro, fuera o poste?",
            Hand.SUIT: "¿Qué palo?",
            Hand.TURN: "La vuelta: ¿rojo o negro?",
        }[self]

    @property
    def stop(self) -> str:
        """Nombre corto de la parada (para la imagen y los botones)."""
        return {
            Hand.COLOR: "Color",
            Hand.HEIGHT: "Altura",
            Hand.RANGE: "Rango",
            Hand.SUIT: "Palo",
            Hand.TURN: "La vuelta",
        }[self]


class Pick(Enum):
    """Lo que se puede pedir. Cada valor es `(clave, emoji, nombre, mano)`."""

    RED = ("rojo", "🔴", "Rojo", Hand.COLOR)
    BLACK = ("negro", "⚫", "Negro", Hand.COLOR)
    HIGHER = ("mayor", "⬆️", "Mayor", Hand.HEIGHT)
    LOWER = ("menor", "⬇️", "Menor", Hand.HEIGHT)
    EQUAL = ("igual", "🟰", "Igual", Hand.HEIGHT)
    INSIDE = ("dentro", "↔️", "Dentro", Hand.RANGE)
    OUTSIDE = ("fuera", "🔀", "Fuera", Hand.RANGE)
    POST = ("poste", "🎯", "Poste", Hand.RANGE)
    SPADES = ("picas", "♠️", "Picas", Hand.SUIT)
    HEARTS = ("corazones", "♥️", "Corazones", Hand.SUIT)
    DIAMONDS = ("diamantes", "♦️", "Diamantes", Hand.SUIT)
    CLUBS = ("treboles", "♣️", "Tréboles", Hand.SUIT)
    TURN_RED = ("vuelta_rojo", "🔴", "Rojo", Hand.TURN)
    TURN_BLACK = ("vuelta_negro", "⚫", "Negro", Hand.TURN)

    def __init__(self, key: str, emoji: str, label: str, hand: Hand) -> None:
        self.key = key
        self.emoji = emoji
        self.label = label
        self.hand = hand


PICK_BY_KEY: dict[str, Pick] = {p.key: p for p in Pick}
#: Palo de cada opción de la cuarta mano (índice de `blackjack.SUITS`: ♠ ♥ ♦ ♣).
SUIT_OF_PICK = {Pick.SPADES: 0, Pick.HEARTS: 1, Pick.DIAMONDS: 2, Pick.CLUBS: 3}
assert len(SUITS) == len(SUIT_OF_PICK)


def picks_for(hand: Hand) -> list[Pick]:
    """Las opciones de una mano, en el orden de los botones."""
    return [p for p in Pick if p.hand is hand]


def wins(pick: Pick, card: Card, table: list[Card]) -> bool:
    """Si `pick` acierta con `card`, dadas las cartas que ya hay en la mesa.

    Args:
        table: Las cartas anteriores, en orden (la segunda mano mira la
            primera; la tercera, las dos primeras).
    """
    h = height(card)
    match pick.hand:
        case Hand.COLOR | Hand.TURN:
            return card.is_red == (pick in (Pick.RED, Pick.TURN_RED))
        case Hand.HEIGHT:
            ref = height(table[0])
            if pick is Pick.HIGHER:
                return h > ref
            if pick is Pick.LOWER:
                return h < ref
            return h == ref
        case Hand.RANGE:
            low, high = sorted((height(table[0]), height(table[1])))
            if pick is Pick.INSIDE:
                return low < h < high
            if pick is Pick.OUTSIDE:
                return h < low or h > high
            return h in (low, high)
        case Hand.SUIT:
            return card.suit == SUIT_OF_PICK[pick]
    raise AssertionError(pick)  # pragma: no cover


def chance(pick: Pick, table: list[Card]) -> Fraction:
    """Probabilidad exacta de que `pick` acierte con la mesa `table`.

    Cuenta cuántas de las 52 cartas ganan: como cada carta sale de una
    baraja entera, eso es la probabilidad.
    """
    winners = sum(
        1 for rank in range(1, 14) for suit in range(4) if wins(pick, Card(rank, suit), table)
    )
    return Fraction(winners, 52)


def factor(pick: Pick, table: list[Card]) -> Fraction:
    """Por cuánto multiplica lo que hay en juego acertar `pick` (`1/p`).

    Raises:
        ValueError: Si la opción no puede acertar (p. ej. «mayor» con un as).
    """
    p = chance(pick, table)
    if not p:
        raise ValueError("Esa opción no puede salir.")
    return 1 / p


def format_multiplier(value: Fraction) -> str:
    """`Fraction(99, 50)` → `×1,98`; los miles llevan punto. Se redondea hacia abajo."""
    cents = math.floor(value * 100)
    whole, frac = divmod(cents, 100)
    return "×" + f"{whole:,}".replace(",", ".") + f",{frac:02d}"


def format_chance(p: Fraction) -> str:
    """`Fraction(6, 13)` → `46 %`; por debajo del 10 %, con un decimal (`7,7 %`)."""
    value = float(p) * 100
    if value < 10:
        return f"{value:.1f}".replace(".", ",").replace(",0", "") + " %"
    return f"{round(value)} %"


@dataclass(frozen=True, slots=True)
class Option:
    """Una opción de la mano en juego, tal como se enseña al jugador.

    Attributes:
        pick: Lo que se pide.
        chance: Probabilidad de acertar (0 si no puede salir).
        multiplier: Multiplicador total si acierta (lo que hay en juego por
            `1/p`); `None` si no puede salir.
    """

    pick: Pick
    chance: Fraction
    multiplier: Fraction | None


class Status(Enum):
    """Estado de una partida."""

    PLAYING = "playing"
    LOST = "lost"
    CASHED = "cashed"


class BusError(Exception):
    """Acción no válida en la partida; el mensaje se puede enseñar al usuario."""


@dataclass(frozen=True, slots=True)
class Guess:
    """Una mano jugada: lo que se pidió, la carta que salió y si acertó."""

    pick: Pick
    card: Card
    won: bool
    chance: Fraction


@dataclass(slots=True)
class BusGame:
    """Una partida del Autobús.

    Attributes:
        stake: Lo apostado (ya cobrado por la economía).
        cards: Las cinco cartas de la partida, sorteadas al empezar. No se
            enseñan hasta que se juega su mano (o se cobra, la siguiente).
        guesses: Manos jugadas, en orden.
    """

    stake: int
    cards: tuple[Card, ...]
    guesses: list[Guess] = field(default_factory=list)
    status: Status = Status.PLAYING

    @classmethod
    def new(
        cls, stake: int, rng: random.Random, *, cards: tuple[Card, ...] | None = None
    ) -> BusGame:
        """Empieza una partida con sus cartas.

        Args:
            cards: Las cartas ya sorteadas (`deal`), si se sortearon antes para
                dibujar la animación por adelantado; si no, se sortean aquí.

        Raises:
            ValueError: Si la apuesta no es positiva o no hay cinco cartas.
        """
        if stake <= 0:
            raise ValueError("La apuesta debe ser positiva.")
        cards = cards if cards is not None else deal(rng)
        if len(cards) != CARDS:
            raise ValueError("Una partida lleva cinco cartas.")
        return cls(stake=stake, cards=cards)

    # -- Estado -----------------------------------------------------------------------

    @property
    def playing(self) -> bool:
        """Si se puede seguir jugando."""
        return self.status is Status.PLAYING

    @property
    def wins(self) -> int:
        """Manos acertadas."""
        return sum(1 for g in self.guesses if g.won)

    @property
    def hand(self) -> Hand | None:
        """La mano que toca jugar, o `None` si la partida terminó o no queda ninguna."""
        if not self.playing or len(self.guesses) >= CARDS:
            return None
        return Hand(len(self.guesses) + 1)

    @property
    def table(self) -> list[Card]:
        """Las cartas ya descubiertas, en orden."""
        return [g.card for g in self.guesses]

    @property
    def next_card(self) -> Card | None:
        """La carta de la mano siguiente (secreta mientras se juega)."""
        return self.cards[len(self.guesses)] if len(self.guesses) < CARDS else None

    @property
    def completed(self) -> bool:
        """Si acertó las cuatro manos (el autobús entero)."""
        return self.wins >= HANDS

    @property
    def turned(self) -> bool:
        """Si acertó también la vuelta (el premio gordo)."""
        return self.wins == CARDS

    @property
    def multiplier(self) -> Fraction:
        """Multiplicador de lo que hay en juego ahora (o había al perder).

        Antes del primer acierto es 1 (lo apostado). Ver la docstring del
        módulo: el 99 % se aplica una vez, en la primera mano.
        """
        won = [g for g in self.guesses if g.won]
        if not won:
            return Fraction(1)
        value = RTP
        for guess in won:
            value /= guess.chance
        return value

    @property
    def pot(self) -> int:
        """Lo que se cobraría ahora mismo (o se perdió), redondeado hacia abajo."""
        return math.floor(self.stake * self.multiplier)

    @property
    def payout(self) -> int:
        """Lo cobrado al terminar: 0 si falló."""
        return self.pot if self.status is Status.CASHED else 0

    @property
    def net(self) -> int:
        """Ganancia o pérdida neta de la partida terminada."""
        return self.payout - self.stake

    @property
    def last(self) -> Guess | None:
        """La última mano jugada, si hubo."""
        return self.guesses[-1] if self.guesses else None

    def options(self) -> list[Option]:
        """Las opciones de la mano en juego con su probabilidad y su multiplicador.

        Es lo que enseñan los botones y el texto mientras se decide.
        """
        hand = self.hand
        if hand is None:
            return []
        table = self.table
        base = self.multiplier if self.guesses else RTP
        result = []
        for pick in picks_for(hand):
            p = chance(pick, table)
            result.append(Option(pick, p, base / p if p else None))
        return result

    # -- Acciones ---------------------------------------------------------------------

    def play(self, pick: Pick) -> Guess:
        """Juega la mano en curso pidiendo `pick` y descubre su carta.

        Si acierta la vuelta no cobra solo: el cog llama a `cash_out` justo
        después (`turned`).

        Raises:
            BusError: Si la partida terminó, la opción es de otra mano o no
                puede salir.
        """
        hand = self.hand
        if hand is None:
            raise BusError("La partida ya ha terminado.")
        if pick.hand is not hand:
            raise BusError(f"Ahora toca: {hand.title}")
        table = self.table
        p = chance(pick, table)
        if not p:
            raise BusError("Esa opción no puede salir con estas cartas.")
        card = self.cards[len(self.guesses)]
        guess = Guess(pick, card, wins(pick, card, table), p)
        self.guesses.append(guess)
        if not guess.won:
            self.status = Status.LOST
        return guess

    def cash_out(self) -> int:
        """Se baja del autobús y devuelve lo que cobra.

        Raises:
            BusError: Si la partida terminó o aún no ha acertado ninguna.
        """
        if not self.playing:
            raise BusError("La partida ya ha terminado.")
        if not self.wins:
            raise BusError("Acierta al menos una antes de cobrar.")
        self.status = Status.CASHED
        return self.pot

    def missed(self) -> list[Pick]:
        """Al cobrar: las opciones que habrían acertado la carta siguiente."""
        hand = Hand(len(self.guesses) + 1) if len(self.guesses) < CARDS else None
        if hand is None:
            return []
        card = self.cards[len(self.guesses)]
        return [p for p in picks_for(hand) if wins(p, card, self.table)]


def paytable() -> list[tuple[str, str]]:
    """La tabla de pagos fija, para la ayuda: (situación, cuánto multiplica).

    Es la misma cuenta que `factor`, escrita para leerla: un factor por
    opción y situación de la mesa.
    """
    rows = [("🔴 Rojo / ⚫ Negro", "×2 (×1,98 con el 1 % de la casa)")]
    for h in HEIGHTS:
        higher, lower = 14 - h, h - 2
        label = {11: "J", 12: "Q", 13: "K", 14: "A"}.get(h, str(h))
        cells = []
        if higher:
            cells.append(f"⬆️ {format_multiplier(Fraction(13, higher))}")
        if lower:
            cells.append(f"⬇️ {format_multiplier(Fraction(13, lower))}")
        rows.append((f"Con un {label}", " · ".join(cells)))
    rows.append(("🟰 Igual", "×13"))
    for gap in range(0, 13):
        inside = max(gap - 1, 0)
        posts = 1 if gap == 0 else 2
        outside = 13 - inside - posts
        cells = []
        if inside:
            cells.append(f"↔️ {format_multiplier(Fraction(13, inside))}")
        if outside:
            cells.append(f"🔀 {format_multiplier(Fraction(13, outside))}")
        cells.append(f"🎯 {format_multiplier(Fraction(13, posts))}")
        rows.append((f"Separadas por {gap}", " · ".join(cells)))
    rows.append(("♠️ ♥️ ♦️ ♣️ Palo", "×4"))
    rows.append(("🔄 La vuelta", "×2"))
    return rows


def milestone(game: BusGame) -> str | None:
    """Frase para las paradas que merecen celebrarse; `None` para el resto."""
    if game.turned:
        return "🚌🔥 ¡AUTOBÚS Y VUELTA! Ni la EMT da tantas vueltas."
    if game.completed and game.wins == HANDS:
        return "🚌 ¡AUTOBÚS COMPLETO! Fin de trayecto… ¿o te juegas la vuelta?"
    if game.wins == 3:
        return "🔥 ¡Tres paradas! Solo queda el palo."
    return None
