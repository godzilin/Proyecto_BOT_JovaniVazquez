"""Reglas del blackjack: zapato, manos, acciones de jugador y banca, y pagos.

Lógica pura, sin Discord ni base de datos. El dinero no se toca aquí: la
partida dice cuánto hay que cobrar antes de cada acción (`extra_stake`) y
cuánto devolver al terminar (`total_return`), y el cog lo mueve a través de
la economía.

Reglas (las habituales de un casino, salvo el tope de apuesta):

- Zapato de 6 barajas, barajado de nuevo en cada mano (no se pueden contar
  cartas).
- La banca se planta en 17, también en 17 blando.
- La banca mira si tiene blackjack cuando enseña un as o una figura/10; si
  lo tiene, la mano acaba ahí y se pierde la apuesta (empate si el jugador
  también tiene blackjack).
- Seguro cuando la banca enseña un as: media apuesta que paga 2:1 si la
  banca tiene blackjack. Se decide antes de que la banca mire su carta.
  Con blackjack propio no se ofrece.
- Blackjack paga 3:2 (redondeando hacia abajo los céntimos que no existen).
- Doblar con cualquier par de cartas, también tras separar.
- Separar una vez dos cartas del mismo valor. Los ases separados reciben
  una carta cada uno y no cuentan como blackjack si suman 21.
- Sin rendición.
- Apuesta inicial de `MAX_STAKE` como mucho.

Con estrategia básica (sin seguro) el juego devuelve ≈ 99,7 % de lo apostado.
El seguro devuelve ≈ 92 % de lo que se mete en él: la banca solo tiene
blackjack 4 de cada 13 veces que enseña un as, y pagar 2:1 necesitaría una
de cada tres. Hasta este cambio el blackjack de la banca era empate y el
juego devolvía ≈ 103,6 %. Cifras sacadas simulando manos con el código real.
"""

from __future__ import annotations

import random
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum

DECKS = 6
SUITS = ("♠", "♥", "♦", "♣")
RANK_LABELS = {1: "A", 11: "J", 12: "Q", 13: "K"}
DEALER_STANDS_ON = 17
#: Apuesta inicial máxima por mano (50 tiradas). Doblar y separar pueden
#: llevar lo que hay en juego hasta el doble o el cuádruple.
MAX_STAKE = 5_000
#: El seguro paga 2:1: se devuelve lo asegurado más el doble.
INSURANCE_PAYS = 2


@dataclass(frozen=True, slots=True)
class Card:
    """Una carta. `rank` va de 1 (as) a 13 (rey); `suit` indexa `SUITS`."""

    rank: int
    suit: int

    @property
    def value(self) -> int:
        """Valor base: as = 1 (se sube a 11 al sumar la mano), figuras = 10."""
        return min(self.rank, 10)

    @property
    def label(self) -> str:
        """Texto corto: `A`, `10`, `K`…"""
        return RANK_LABELS.get(self.rank, str(self.rank))

    @property
    def is_red(self) -> bool:
        """Corazones y diamantes."""
        return self.suit in (1, 2)

    def __str__(self) -> str:
        return f"{self.label}{SUITS[self.suit]}"


def hand_total(cards: Sequence[Card]) -> tuple[int, bool]:
    """Mejor total de la mano y si es "blando" (un as cuenta como 11)."""
    total = sum(card.value for card in cards)
    if any(card.rank == 1 for card in cards) and total + 10 <= 21:
        return total + 10, True
    return total, False


def is_blackjack(cards: Sequence[Card]) -> bool:
    """Un as y una carta de valor 10 como dos primeras cartas."""
    return len(cards) == 2 and hand_total(cards)[0] == 21


def new_shoe(shuffle: Callable[[list[Card]], None] | None = None) -> list[Card]:
    """Zapato barajado. Se reparte desde el final (`pop`).

    Args:
        shuffle: Función que baraja en sitio. Por defecto usa el generador
            criptográfico del sistema, que no se puede predecir.
    """
    cards = [Card(rank, suit) for _ in range(DECKS) for suit in range(4) for rank in range(1, 14)]
    (shuffle or random.SystemRandom(secrets.randbits(64)).shuffle)(cards)
    return cards


class Result(Enum):
    """Desenlace de una mano frente a la banca."""

    BLACKJACK = "blackjack"
    WIN = "win"
    PUSH = "push"
    LOSE = "lose"
    BUST = "bust"


@dataclass(slots=True)
class Hand:
    """Una mano del jugador con su apuesta."""

    cards: list[Card]
    stake: int
    from_split: bool = False
    doubled: bool = False
    done: bool = False
    result: Result | None = None

    @property
    def total(self) -> int:
        """Mejor total de la mano."""
        return hand_total(self.cards)[0]

    @property
    def busted(self) -> bool:
        """Si se ha pasado de 21."""
        return self.total > 21

    @property
    def natural(self) -> bool:
        """Blackjack de verdad: dos cartas, 21 y no viene de separar."""
        return not self.from_split and is_blackjack(self.cards)

    def payout(self) -> int:
        """Lo que se devuelve al jugador por esta mano (apuesta incluida)."""
        if self.result is Result.BLACKJACK:
            return self.stake + self.stake * 3 // 2
        if self.result is Result.WIN:
            return self.stake * 2
        if self.result is Result.PUSH:
            return self.stake
        return 0


class Action(Enum):
    """Acciones del jugador sobre la mano activa."""

    HIT = "hit"
    STAND = "stand"
    DOUBLE = "double"
    SPLIT = "split"


class IllegalAction(Exception):
    """La acción no está permitida en el estado actual de la mano."""


@dataclass(slots=True)
class BlackjackGame:
    """Una mano de blackjack de principio a fin.

    Uso: `deal()`, luego `act()` mientras `player_turn`; después
    `reveal_hole()` y `dealer_draw()` mientras `dealer_should_draw()`;
    por último `settle()`. El cog hace las pausas entre pasos para que la
    banca robe carta a carta en pantalla.
    """

    stake: int
    shoe: list[Card] = field(default_factory=new_shoe)
    dealer: list[Card] = field(default_factory=list)
    hands: list[Hand] = field(default_factory=list)
    active: int = 0
    hole_revealed: bool = False
    settled: bool = False
    #: La banca enseña un as y falta decidir el seguro (no se juega hasta entonces).
    insurance_pending: bool = False
    #: Lo apostado al seguro (0 si no se tomó).
    insurance: int = 0

    # -- Reparto -------------------------------------------------------------------

    def _draw(self) -> Card:
        return self.shoe.pop()

    def deal(self) -> None:
        """Reparte dos cartas a cada uno y resuelve los blackjacks iniciales.

        Si alguien tiene blackjack la mano termina aquí: la banca enseña su
        carta tapada y no hay turno de jugador. Si la banca enseña un as, la
        mano espera a `decide_insurance` antes de que la banca mire.
        """
        if self.stake <= 0:
            raise ValueError("La apuesta debe ser positiva.")
        player = [self._draw()]
        self.dealer.append(self._draw())
        player.append(self._draw())
        self.dealer.append(self._draw())
        self.hands = [Hand(player, self.stake)]

        if self.dealer[0].rank == 1 and not self.hands[0].natural:
            self.insurance_pending = True
            return
        self._peek()

    def _peek(self) -> None:
        """La banca mira su carta tapada: con blackjack (o el del jugador) se acaba."""
        dealer_peeks = self.dealer[0].value in (1, 10)
        dealer_bj = dealer_peeks and is_blackjack(self.dealer)
        if dealer_bj or self.hands[0].natural:
            self.hands[0].done = True
            self.hole_revealed = True

    # -- Seguro --------------------------------------------------------------------

    @property
    def insurance_cost(self) -> int:
        """Lo que cuesta el seguro: media apuesta, sin céntimos (0 si la ficha es 1)."""
        return self.stake // 2

    def can_insure(self) -> bool:
        """Si se puede tomar el seguro ahora mismo."""
        return self.insurance_pending and self.insurance_cost > 0

    def decide_insurance(self, take: bool) -> None:
        """Toma o rechaza el seguro; después la banca mira si tiene blackjack.

        Raises:
            IllegalAction: Si no hay seguro que decidir o no se puede tomar.
        """
        if not self.insurance_pending or (take and not self.can_insure()):
            raise IllegalAction("insurance")
        self.insurance_pending = False
        if take:
            self.insurance = self.insurance_cost
        self._peek()

    @property
    def insurance_paid(self) -> bool:
        """Si el seguro se tomó y la banca tenía blackjack."""
        return self.insurance > 0 and self.hole_revealed and is_blackjack(self.dealer)

    @property
    def insurance_return(self) -> int:
        """Lo que devuelve el seguro (lo asegurado y el premio 2:1, o nada)."""
        return self.insurance * (1 + INSURANCE_PAYS) if self.insurance_paid else 0

    # -- Turno del jugador ---------------------------------------------------------

    @property
    def player_turn(self) -> bool:
        """Si queda alguna mano por jugar (no mientras se decide el seguro)."""
        return not self.insurance_pending and any(not hand.done for hand in self.hands)

    @property
    def current(self) -> Hand:
        """Mano que se está jugando."""
        return self.hands[self.active]

    def can(self, action: Action) -> bool:
        """Si la acción es legal ahora mismo."""
        if not self.player_turn:
            return False
        hand = self.current
        if action in (Action.HIT, Action.STAND):
            return True
        if action is Action.DOUBLE:
            return len(hand.cards) == 2
        # Separar: una sola vez, dos cartas del mismo valor.
        return (
            len(self.hands) == 1
            and len(hand.cards) == 2
            and hand.cards[0].value == hand.cards[1].value
        )

    def extra_stake(self, action: Action) -> int:
        """Cuánto hay que cobrar antes de la acción (doblar y separar)."""
        if action in (Action.DOUBLE, Action.SPLIT):
            return self.current.stake
        return 0

    def act(self, action: Action) -> None:
        """Aplica una acción a la mano activa.

        Raises:
            IllegalAction: Si la acción no es legal ahora.
        """
        if not self.can(action):
            raise IllegalAction(action.value)
        hand = self.current
        if action is Action.HIT:
            hand.cards.append(self._draw())
            if hand.total >= 21:
                hand.done = True
        elif action is Action.STAND:
            hand.done = True
        elif action is Action.DOUBLE:
            hand.stake *= 2
            hand.doubled = True
            hand.cards.append(self._draw())
            hand.done = True
        else:
            self._split()
        self._advance()

    def _split(self) -> None:
        first, second = self.current.cards
        aces = first.rank == 1
        self.hands = [
            Hand([first, self._draw()], self.stake, from_split=True),
            Hand([second, self._draw()], self.stake, from_split=True),
        ]
        for hand in self.hands:
            # Los ases separados reciben una sola carta; un 21 se planta solo.
            if aces or hand.total == 21:
                hand.done = True

    def _advance(self) -> None:
        while self.active < len(self.hands) - 1 and self.hands[self.active].done:
            self.active += 1

    def stand_all(self) -> None:
        """Planta todas las manos abiertas (al caducar la mesa o al apagar).

        Un seguro sin decidir cuenta como rechazado.
        """
        self.insurance_pending = False
        for hand in self.hands:
            hand.done = True

    # -- Turno de la banca ---------------------------------------------------------

    def reveal_hole(self) -> None:
        """Destapa la segunda carta de la banca."""
        self.hole_revealed = True

    @property
    def dealer_total(self) -> int:
        """Total de la banca (con la carta tapada incluida)."""
        return hand_total(self.dealer)[0]

    @property
    def visible_dealer_total(self) -> int:
        """Total que ve el jugador: solo la carta descubierta si la otra va tapada."""
        cards = self.dealer if self.hole_revealed else self.dealer[:1]
        return hand_total(cards)[0]

    def dealer_should_draw(self) -> bool:
        """Si la banca debe pedir otra carta.

        No roba si todas las manos se pasaron o si hubo blackjack inicial:
        el resultado ya está decidido.
        """
        if self.player_turn or not self.hole_revealed:
            return False
        if all(hand.busted for hand in self.hands):
            return False
        if len(self.hands) == 1 and self.hands[0].natural:
            return False
        if is_blackjack(self.dealer):
            return False
        return self.dealer_total < DEALER_STANDS_ON

    def dealer_draw(self) -> Card:
        """La banca roba una carta."""
        card = self._draw()
        self.dealer.append(card)
        return card

    # -- Cierre --------------------------------------------------------------------

    def settle(self) -> int:
        """Decide el resultado de cada mano y devuelve el total a pagar.

        Raises:
            RuntimeError: Si aún queda turno del jugador o de la banca.
        """
        if (
            self.insurance_pending
            or self.player_turn
            or self.dealer_should_draw()
            or not self.hole_revealed
        ):
            raise RuntimeError("La mano no ha terminado.")
        dealer_bj = is_blackjack(self.dealer)
        dealer_total = self.dealer_total
        for hand in self.hands:
            if hand.busted:
                hand.result = Result.BUST
            elif hand.natural and not dealer_bj:
                hand.result = Result.BLACKJACK
            elif dealer_bj:
                # La banca lo mira en el reparto y la mano acaba ahí: solo
                # empata quien también tiene blackjack.
                hand.result = Result.PUSH if hand.natural else Result.LOSE
            elif dealer_total > 21 or hand.total > dealer_total:
                hand.result = Result.WIN
            elif hand.total == dealer_total:
                hand.result = Result.PUSH
            else:
                hand.result = Result.LOSE
        self.settled = True
        return self.total_return

    @property
    def total_stake(self) -> int:
        """Total apostado en la mano (dobles, separaciones y seguro incluidos)."""
        return sum(hand.stake for hand in self.hands) + self.insurance

    @property
    def total_return(self) -> int:
        """Total que se devuelve al jugador (0 hasta `settle`), seguro incluido."""
        if not self.settled:
            return 0
        return sum(hand.payout() for hand in self.hands) + self.insurance_return

    @property
    def net(self) -> int:
        """Ganancia o pérdida neta de la mano completa."""
        return self.total_return - self.total_stake
