"""La Rueda: una vuelta gratis al día con premios de dinero, XP, cajas y objetos únicos.

Cada miembro gira una vez al día (el día va de 00:00 a 00:00 en hora canaria,
`levels.TIMEZONE`). La rueda no pide apuesta: es un sorteo promocional, no un
juego del casino (no pasa por `settle_bet` ni por la retención diaria del casino).

**Porciones del tamaño de su probabilidad.** La rueda tiene 32 porciones
(`WEDGES`) y cada una mide lo que pesa: un premio de peso 38 sobre 1.000 ocupa
13,7° y el expositor (peso 2) es una raya de 0,72°. Los premios frecuentes van
repartidos en varias porciones para que la rueda no quede con un trozo enorme,
pero la suma de sus pesos es su probabilidad (`PRIZES`), así que lo que se ve es
lo que hay. Los pesos suman `TOTAL_WEIGHT` (1.000).

**Lo que vale de media una vuelta** (`expected_cash`, `expected_in_kind`): unos
380 Y$ en efectivo (una cuarta parte del IMV máximo, que también cuesta un clic) y
unos 650 Y$ en especie a precio de catálogo (cajas, piezas y el expositor). Las
pruebas fijan las cifras para que nadie las suba sin querer.

**Premios** (`PrizeKind`):

- Dinero: `pay_income`, con su IRPF (ver `EconomyService.pay_income`).
- XP: se suma como la de los mensajes. Sin niveles en el servidor, la rueda no
  tiene porciones de XP (`layout(levels=False)`): no se puede prometer lo que no
  se va a dar.
- Cajas de la tienda (`lootboxes`), una chuche para la mascota y una pieza de la
  colección de la Feria (`rueda_catalog`): van a la mochila y, como premio en
  especie, su precio de catálogo cuenta en la Renta de la semana
  (`EconomyService.record_prize_in_kind`).
- 🔁 Otra vuelta: se gira otra vez en el acto.
- 🔎 Inspección: no hay premio.
- 🏆 El expositor: el título numerado de la semana (`showcase_for`). Si ya no
  quedan, se paga `SHOWCASE_FALLBACK` en dinero.
- 🧊 El Tapete Hielo Negro (`skins.HIELO`): la porción más fina (1 de 1.000). Solo
  sale aquí y, muy rara vez, en las cajas. Si ya se tiene, se paga `SHOWCASE_FALLBACK`.

**El sorteo se guarda al abrir el panel.** El resultado de la próxima vuelta se
sortea (`draw`) cuando el miembro abre la rueda y se guarda en la base
(`repositories.rueda`) antes de enseñar nada. Así la mesa puede pintar el GIF
antes del clic sin que reabrir el panel, o esperar a un reinicio del bot,
cambie lo que toca. Lo sorteado no se enseña hasta girar.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from bot.services.levels import TIMEZONE

GAME = "rueda"
#: Suma de los pesos de todas las porciones: la probabilidad de un premio es su peso
#: entre esto.
TOTAL_WEIGHT = 1_000
#: Lo que se paga si sale el expositor y ya no quedan títulos (el premio más alto en
#: dinero de la rueda).
SHOWCASE_FALLBACK = 15_000


class PrizeKind(StrEnum):
    """Qué da un premio. El valor es estable (se guarda y lo cuentan los logros)."""

    MONEY = "dinero"
    XP = "xp"
    BOX = "caja"
    TREAT = "chuche"
    PIECE = "pieza"
    AGAIN = "otra"
    NOTHING = "nada"
    SHOWCASE = "expositor"
    SKIN = "tapete"


@dataclass(frozen=True, slots=True)
class Prize:
    """Un premio de la rueda.

    Attributes:
        key: Clave estable: se guarda en cada vuelta y la cuentan los logros.
        kind: Qué da.
        amount: Yapdollars (dinero) o puntos de XP.
        item: Clave del artículo de la tienda que da (cajas y chuche).
        label: Lo que pone en la rueda, corto.
        name: Cómo se dice en el resultado («una Caja de cartón»).
        emoji: Icono en los textos.
    """

    key: str
    kind: PrizeKind
    label: str
    name: str
    emoji: str
    amount: int = 0
    item: str | None = None


# fmt: off
PRIZES: tuple[Prize, ...] = (
    Prize("y300", PrizeKind.MONEY, "300", "300 Y$", "💶", amount=300),
    Prize("y600", PrizeKind.MONEY, "600", "600 Y$", "💶", amount=600),
    Prize("y1000", PrizeKind.MONEY, "1.000", "1.000 Y$", "💵", amount=1_000),
    Prize("y2500", PrizeKind.MONEY, "2.500", "2.500 Y$", "💵", amount=2_500),
    Prize("y5000", PrizeKind.MONEY, "5.000", "5.000 Y$", "💰", amount=5_000),
    Prize("y15000", PrizeKind.MONEY, "15.000", "15.000 Y$", "💰", amount=15_000),
    Prize("xp250", PrizeKind.XP, "250 XP", "250 XP", "✨", amount=250),
    Prize("xp1000", PrizeKind.XP, "1.000 XP", "1.000 XP", "🌟", amount=1_000),
    Prize("carton", PrizeKind.BOX, "CAJA", "una Caja de cartón", "📦", item="caja_carton"),
    Prize("regalo", PrizeKind.BOX, "REGALO", "una Caja de regalo", "🎁", item="caja_regalo"),
    Prize("maletin", PrizeKind.BOX, "MALETÍN", "el Maletín del ministro", "💼",
          item="maletin_ministro"),
    Prize("chuche", PrizeKind.TREAT, "CHUCHE", "una Piruleta de feria", "🍭",
          item="rueda_piruleta"),
    Prize("pieza", PrizeKind.PIECE, "PIEZA", "una pieza de la colección de la Feria", "🧩"),
    Prize("otra", PrizeKind.AGAIN, "OTRA", "otra vuelta", "🔁"),
    Prize("nada", PrizeKind.NOTHING, "NADA", "nada", "🔎"),
    Prize("expositor", PrizeKind.SHOWCASE, "EXPOSITOR", "el expositor de la semana", "🏆"),
    Prize("hielo", PrizeKind.SKIN, "HIELO", "el Tapete Hielo Negro", "🧊",
          item="skin_tapete_hielo"),
)
# fmt: on
PRIZE_BY_KEY: dict[str, Prize] = {p.key: p for p in PRIZES}


@dataclass(frozen=True, slots=True)
class Wedge:
    """Una porción de la rueda, en el orden en que van alrededor.

    Attributes:
        index: Posición alrededor de la rueda (0 es la primera tras el ángulo 0).
        prize: Lo que da.
        weight: Lo que pesa; su ángulo es `weight / total × 360`.
        start: Ángulo en grados donde empieza, en el sentido de las agujas del reloj
            desde las 12, con la rueda en su posición 0.
        end: Ángulo donde acaba.
    """

    index: int
    prize: Prize
    weight: int
    start: float
    end: float

    @property
    def middle(self) -> float:
        return (self.start + self.end) / 2

    @property
    def width(self) -> float:
        return self.end - self.start

    @property
    def sliver(self) -> bool:
        """Si es una raya (menos de 4°): sin letrero, solo el color."""
        return self.width < 4.0


#: Las porciones alrededor de la rueda: `(premio, peso)`. Los premios frecuentes van
#: en varias, separados; las rayas (los premios gordos) quedan entre porciones
#: normales para que la aguja pase cerca a menudo.
# fmt: off
_RING: tuple[tuple[str, int], ...] = (
    ("expositor", 2), ("y300", 38), ("xp250", 40), ("y600", 38), ("carton", 40),
    ("y1000", 30), ("chuche", 40), ("y300", 38), ("nada", 25), ("y2500", 30),
    ("y5000", 8), ("y600", 38), ("pieza", 27), ("xp1000", 40), ("y300", 38),
    ("carton", 40), ("y1000", 30), ("maletin", 5), ("regalo", 30), ("y600", 37),
    ("xp250", 40), ("y300", 38), ("otra", 30), ("chuche", 40), ("y1000", 30),
    ("y15000", 2), ("carton", 40), ("y600", 37), ("nada", 24), ("hielo", 1),
    ("pieza", 26),
    ("xp250", 40), ("y300", 38),
)
# fmt: on


def layout(*, levels: bool = True) -> tuple[Wedge, ...]:
    """Las porciones de la rueda con sus ángulos.

    Args:
        levels: Si el servidor tiene los niveles encendidos. Sin ellos se quitan las
            porciones de XP y el resto se reparte la rueda entera.
    """
    ring = [(key, w) for key, w in _RING if levels or PRIZE_BY_KEY[key].kind is not PrizeKind.XP]
    total = sum(w for _, w in ring)
    wedges = []
    angle = 0.0
    for index, (key, weight) in enumerate(ring):
        width = 360.0 * weight / total
        wedges.append(Wedge(index, PRIZE_BY_KEY[key], weight, angle, angle + width))
        angle += width
    return tuple(wedges)


def chances(wedges: Sequence[Wedge]) -> dict[str, float]:
    """Probabilidad de cada premio (sumando sus porciones)."""
    total = sum(w.weight for w in wedges)
    out: dict[str, float] = {}
    for wedge in wedges:
        out[wedge.prize.key] = out.get(wedge.prize.key, 0.0) + wedge.weight / total
    return out


# -- Sorteo ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Draw:
    """Lo que ha tocado en una vuelta, antes de girar.

    Attributes:
        wedge: Índice de la porción donde para la aguja.
        prize: Clave del premio.
        stop: Dónde para la aguja dentro de la porción, de 0 (su principio) a 1 (su
            final). Nunca en el borde, para que no haya dudas al verla.
        seed: Semilla del giro (cuántas vueltas, temblores), para que el GIF pintado
            antes y el pintado al pulsar salgan iguales.
        item: Lo concreto que da, si hace falta decidirlo antes (la pieza de la
            colección o el título del expositor).
    """

    wedge: int
    prize: str
    stop: float
    seed: int
    item: str | None = None


def draw(rng: random.Random, wedges: Sequence[Wedge]) -> tuple[Wedge, float, int]:
    """Sortea la porción por su peso, dónde para dentro de ella y la semilla del giro."""
    total = sum(w.weight for w in wedges)
    pick = rng.randrange(total)
    for wedge in wedges:
        if pick < wedge.weight:
            break
        pick -= wedge.weight
    # En las rayas, al centro; en las demás, lejos de los bordes.
    stop = 0.5 if wedge.sliver else rng.uniform(0.18, 0.82)
    return wedge, stop, rng.randrange(1, 2**31)


def landing_angle(wedge: Wedge, stop: float) -> float:
    """Ángulo de la rueda (en grados, sentido horario) con el que `stop` queda bajo la aguja.

    La aguja está a las 12. Girar la rueda `θ` grados en sentido horario lleva bajo la
    aguja el punto que en reposo estaba en `-θ`, así que para dejar ahí el punto `p`
    hay que girarla `-p` (módulo 360).
    """
    point = wedge.start + wedge.width * stop
    return (-point) % 360.0


def wedge_at(wedges: Sequence[Wedge], angle: float) -> Wedge:
    """La porción que hay bajo la aguja con la rueda girada `angle` grados."""
    point = (-angle) % 360.0
    for wedge in wedges:
        if wedge.start <= point < wedge.end:
            return wedge
    return wedges[-1]


# -- Valor esperado ------------------------------------------------------------------------


def expected_cash(wedges: Sequence[Wedge] | None = None) -> float:
    """Yapdollars en efectivo que da de media una vuelta (sin contar «otra vuelta»).

    El expositor se cuenta aparte (`expected_in_kind`): solo da dinero si se agotan
    los títulos.
    """
    odds = chances(wedges or layout())
    return sum(odds.get(p.key, 0.0) * p.amount for p in PRIZES if p.kind is PrizeKind.MONEY)


def expected_in_kind(
    prices: dict[str, int],
    piece_value: float,
    showcase_value: int,
    wedges: Sequence[Wedge] | None = None,
) -> float:
    """Valor de catálogo medio de lo que da una vuelta en especie.

    Args:
        prices: Precio de catálogo de cada artículo que da la rueda (`Prize.item`).
        piece_value: Valor medio de una pieza de la colección (`rueda_catalog`).
        showcase_value: Precio de catálogo de un título del expositor.
    """
    odds = chances(wedges or layout())
    total = 0.0
    for prize in PRIZES:
        chance = odds.get(prize.key, 0.0)
        if prize.item is not None:
            total += chance * prices[prize.item]
        elif prize.kind is PrizeKind.PIECE:
            total += chance * piece_value
        elif prize.kind is PrizeKind.SHOWCASE:
            total += chance * showcase_value
    return total


# -- Días y semanas --------------------------------------------------------------------------


def day_of(now: float) -> date:
    """El día de la rueda (hora canaria) del instante `now`."""
    return datetime.fromtimestamp(now, TIMEZONE).date()


def week_of(day: date) -> date:
    """El lunes de la semana de `day`: el expositor cambia cada lunes."""
    return day - timedelta(days=day.weekday())


def next_reset(now: float) -> float:
    """Instante (epoch) de la próxima medianoche canaria, cuando vuelve a haber vuelta."""
    local = datetime.fromtimestamp(now, TIMEZONE)
    tomorrow = (local + timedelta(days=1)).date()
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=TIMEZONE).timestamp()


def countdown(seconds: float) -> str:
    """«3 h 12 min» o «12 min» hasta la próxima vuelta."""
    minutes = max(1, math.ceil(seconds / 60))
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if hours else f"{minutes} min"


def streak(days: Sequence[date], today: date) -> int:
    """Días seguidos girando que acaban en `today` (o en ayer, si hoy aún no ha girado).

    Args:
        days: Días distintos en los que ha girado, en cualquier orden.
    """
    have = set(days)
    cursor = today if today in have else today - timedelta(days=1)
    count = 0
    while cursor in have:
        count += 1
        cursor -= timedelta(days=1)
    return count


def showcase_for(week: date, keys: Sequence[str], sold_out: frozenset[str]) -> str | None:
    """El título del expositor de la semana del lunes `week`, o `None` si se han agotado.

    Va rotando por orden, uno distinto cada semana, saltando los que ya no quedan.
    """
    left = [k for k in keys if k not in sold_out]
    if not left:
        return None
    ordinal = week.toordinal() // 7
    return left[ordinal % len(left)]
