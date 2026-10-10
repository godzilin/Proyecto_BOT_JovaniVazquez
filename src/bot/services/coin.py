"""Reglas de Cara o cruz (`moneda`): doble o nada con una moneda que a veces cae de canto.

Lógica pura, sin Discord ni dinero. El cog (`bot.cogs.coin`) cobra al
empezar, paga al cobrar y pinta la moneda (`bot.services.coin_scene`).

El jugador elige 👑 cara o ✈️ cruz y se lanza la moneda. Si acierta, lo que
tiene en juego se dobla y decide: 💰 cobrar o volver a lanzar (doble o
nada), eligiendo lado otra vez. Si falla, lo pierde todo. A los
`MAX_FLIPS` aciertos seguidos cobra solo el premio gordo (×1.024).

**El canto es la ventaja de la casa.** Uno de cada cien lanzamientos
(`EDGE_CHANCE`) la moneda se queda de pie y se pierde, se haya pedido lo que
se haya pedido. Así acertar es un 49,5 % y no un 50 %, y cada lanzamiento
devuelve de media el 99 % de lo que hay en juego, como Minas, el Crash o el
Pollo. Se nota en el texto («Perro Sanxe se la queda») y en la imagen (la
moneda se tambalea y se queda de pie), en vez de en una cuota rara de ×1,98.
Lo perdido en un canto es una pérdida de juego como otra cualquiera: no va a
la cuenta del Estado, que solo recibe impuestos con su norma (Biblia,
«Dinero, impuestos y la Renta»).

**Doble o nada compone la ventaja.** Cobrar tras `k` aciertos devuelve de
media 0,99^k: ×2 deja el 99 %, ×1.024 el 90 %. Es lo que pasa con cualquier
doble o nada de verdad, y se dice en el texto del comando, no se esconde.

**El siguiente lanzamiento se sortea por adelantado** (`upcoming`), igual
que el carril del coche en el Pollo: misma probabilidad que sortearlo al
lanzar, y permite decir al cobrar «la siguiente habría salido cruz».
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from fractions import Fraction

#: Probabilidad de que la moneda caiga de canto en un lanzamiento.
EDGE_CHANCE = Fraction(1, 100)
#: Aciertos seguidos que cobran solos el premio gordo (×1.024).
MAX_FLIPS = 10


class Side(Enum):
    """Lo que se puede pedir (y lo que sale, menos el canto).

    Cada valor es `(clave, emoji, nombre)`. La cara lleva la corona y la cruz
    el Falcon, que es lo que se ve en la moneda.
    """

    CARA = ("cara", "👑", "Cara")
    CRUZ = ("cruz", "✈️", "Cruz")

    def __init__(self, key: str, emoji: str, label: str) -> None:
        self.key = key
        self.emoji = emoji
        self.label = label

    @property
    def other(self) -> Side:
        """El otro lado."""
        return Side.CRUZ if self is Side.CARA else Side.CARA


SIDE_BY_KEY: dict[str, Side] = {s.key: s for s in Side}
_ALIASES = {
    "c": "cara",
    "heads": "cara",
    "rey": "cara",
    "corona": "cara",
    "x": "cruz",
    "tails": "cruz",
    "falcon": "cruz",
}


def parse_side(text: str) -> Side:
    """`cara`, `rey`, `heads` → cara; `cruz`, `falcon`, `tails` → cruz.

    Raises:
        ValueError: Con un mensaje mostrable si no es ningún lado.
    """
    key = text.strip().lower()
    key = _ALIASES.get(key, key)
    if key not in SIDE_BY_KEY:
        raise ValueError("Elige `cara` o `cruz`.")
    return SIDE_BY_KEY[key]


class Outcome(Enum):
    """Cómo cae la moneda. `EDGE` es el canto."""

    CARA = "cara"
    CRUZ = "cruz"
    EDGE = "canto"

    @property
    def side(self) -> Side | None:
        """El lado que sale, o `None` si cae de canto."""
        return SIDE_BY_KEY.get(self.value)

    @property
    def label(self) -> str:
        """Cómo se dice: «👑 Cara», «✈️ Cruz» o «🪙 De canto»."""
        side = self.side
        return f"{side.emoji} {side.label}" if side is not None else "🪙 De canto"


def toss(rng: random.Random) -> Outcome:
    """Lanza la moneda: canto con `EDGE_CHANCE`; si no, cara o cruz al 50 %."""
    if rng.random() < EDGE_CHANCE:
        return Outcome.EDGE
    return Outcome.CARA if rng.random() < 0.5 else Outcome.CRUZ


def win_chance() -> Fraction:
    """Probabilidad de acertar un lanzamiento (49,5 %)."""
    return (1 - EDGE_CHANCE) / 2


def multiplier(wins: int) -> int:
    """Multiplicador tras `wins` aciertos seguidos: ×1, ×2, ×4… ×1.024."""
    if not 0 <= wins <= MAX_FLIPS:
        raise ValueError("Esa racha no existe.")
    return 2**wins


def format_multiplier(value: int) -> str:
    """`1024` → `×1.024`."""
    return "×" + f"{value:,}".replace(",", ".")


class Status(Enum):
    """Estado de una partida."""

    PLAYING = "playing"
    LOST = "lost"
    EDGE = "edge"
    CASHED = "cashed"


class CoinError(Exception):
    """Acción no válida en la partida; el mensaje se puede enseñar al usuario."""


@dataclass(frozen=True, slots=True)
class Flip:
    """Un lanzamiento: lo que se pidió y lo que salió."""

    pick: Side
    outcome: Outcome

    @property
    def won(self) -> bool:
        """Si acertó."""
        return self.outcome.side is self.pick


@dataclass(slots=True)
class CoinGame:
    """Una partida de doble o nada.

    Attributes:
        stake: Lo apostado (ya cobrado por la economía).
        upcoming: Cómo caerá el siguiente lanzamiento. Se sortea por
            adelantado y no se enseña hasta acabar.
        flips: Lanzamientos hechos, en orden.
    """

    stake: int
    upcoming: Outcome
    flips: list[Flip] = field(default_factory=list)
    status: Status = Status.PLAYING

    @classmethod
    def new(cls, stake: int, rng: random.Random, *, upcoming: Outcome | None = None) -> CoinGame:
        """Empieza una partida y sortea el primer lanzamiento.

        Args:
            upcoming: El primer lanzamiento, si ya se sorteó antes (la mesa lo
                sortea por adelantado para tener pintado su GIF).

        Raises:
            ValueError: Si la apuesta no es positiva.
        """
        if stake <= 0:
            raise ValueError("La apuesta debe ser positiva.")
        return cls(stake=stake, upcoming=upcoming if upcoming is not None else toss(rng))

    # -- Estado -----------------------------------------------------------------------

    @property
    def playing(self) -> bool:
        """Si se puede seguir lanzando."""
        return self.status is Status.PLAYING

    @property
    def wins(self) -> int:
        """Aciertos seguidos (todos los lanzamientos menos el último si falló)."""
        return sum(1 for f in self.flips if f.won)

    @property
    def maxed(self) -> bool:
        """Si llegó a los `MAX_FLIPS` aciertos."""
        return self.wins == MAX_FLIPS

    @property
    def multiplier(self) -> int:
        """Multiplicador de lo que hay en juego ahora (o había al perder)."""
        return multiplier(self.wins)

    @property
    def pot(self) -> int:
        """Lo que se cobraría ahora mismo (o se perdió)."""
        return self.stake * self.multiplier

    @property
    def payout(self) -> int:
        """Lo cobrado al terminar: 0 si falló o cayó de canto."""
        return self.pot if self.status is Status.CASHED else 0

    @property
    def net(self) -> int:
        """Ganancia o pérdida neta de la partida terminada."""
        return self.payout - self.stake

    @property
    def last(self) -> Flip | None:
        """El último lanzamiento, si hubo."""
        return self.flips[-1] if self.flips else None

    # -- Acciones ---------------------------------------------------------------------

    def flip(self, pick: Side, rng: random.Random) -> Flip:
        """Lanza pidiendo `pick` y sortea el siguiente.

        Si es el acierto `MAX_FLIPS` no cobra solo: el cog llama a
        `cash_out` justo después (`maxed`).

        Raises:
            CoinError: Si la partida terminó o ya está en el tope.
        """
        if not self.playing or self.maxed:
            raise CoinError("La partida ya ha terminado.")
        flip = Flip(pick, self.upcoming)
        self.flips.append(flip)
        self.upcoming = toss(rng)
        if flip.outcome is Outcome.EDGE:
            self.status = Status.EDGE
        elif not flip.won:
            self.status = Status.LOST
        return flip

    def cash_out(self) -> int:
        """Se retira y devuelve lo que cobra.

        Raises:
            CoinError: Si la partida terminó o aún no ha acertado ninguna.
        """
        if not self.playing:
            raise CoinError("La partida ya ha terminado.")
        if not self.wins:
            raise CoinError("Acierta al menos una antes de cobrar.")
        self.status = Status.CASHED
        return self.pot


def milestone(wins: int) -> str | None:
    """Frase para las rachas que merecen celebrarse; `None` para el resto."""
    return {
        3: "🔥 ¡Tres seguidas!",
        5: "🔥🔥 ¡Cinco! Esto ya no es suerte, es un don.",
        7: "🚀 ¡Siete seguidas! Ni el CIS lo vio venir.",
        8: "😰 ¡Ocho! Dos más y es leyenda…",
        9: "🤯 ¡NUEVE! Una más para la moneda de oro.",
        MAX_FLIPS: "🏆 ¡DIEZ SEGUIDAS! ¡LA MONEDA DE ORO!",
    }.get(wins)
