"""Tragaperras: rodillos, tabla de pagos, giros gratis, re-giro y máquina caliente.

Lógica pura, sin Discord ni dinero. El cog (`bot.cogs.slots`) decide cuánto
se apuesta, cobra y paga con `EconomyService.play_slots` y pinta el resultado
con `bot.services.slots_render`.

La máquina tiene 3 rodillos y se ven 3 filas de cada uno (una cuadrícula de
3×3). Solo paga la fila del medio, la línea. Las filas de arriba y abajo
están para que se vea el símbolo que casi entra: el casi-premio.

Cada rodillo es una tira fija de símbolos (`REEL_STRIPS`) y cada posición de
la tira tiene un peso (`REEL_WEIGHTS`), el «rodillo virtual» de las máquinas
reales. El peso decide cuánto para el rodillo en cada casilla: las casillas
pegadas a un 7️⃣ o a un 🃏 del tercer rodillo pesan mucho y la del símbolo,
poco. Así el premio gordo pasa rozando la línea a menudo y entra en ella
casi nunca. Kassinove y Schare (2001) vieron que la gente aguanta más jugando
con casi-premios en torno a un 30 % de las tiradas; aquí hay uno de cada
cinco.

Tras un casi-premio en el tercer rodillo se puede comprar un re-giro de ese
rodillo (`respin_price`): cuesta lo que vale de media entre `RESPIN_RTP`.

Números de la tabla actual (calculados en `tests/unit/test_slots_service.py`
y buscados con `docs/calibrar_tragaperras.py`):

- La línea devuelve ~81 % de lo apostado; con los giros gratis (que se
  suman si salen 🎟️ dentro de ellos) y la máquina caliente, ~89,6 %; con la
  barra de bonus (3 giros gratis cada ~80 tiradas), ~93,4 %. El 6 % de cada
  apuesta va al bote común, que acaba saliendo entero: en total vuelve ~99,5 %.
  Hasta la 7.1 el bote era el 3 % y la línea, el 84 %: se pasó retorno de la
  línea al bote para que crezca el doble de rápido por tirada.
  Se pierde despacio, que es lo que alarga las sesiones.
- El 32 % de las tiradas paga algo, pero casi dos de cada tres de esas pagan menos
  de lo apostado (una 🍒 al principio devuelve la mitad). La máquina lo
  celebra y aun así pierdes.
- Casi-premio en el 21 % de las tiradas; re-giro ofrecido en el 20 % y con
  premio gordo en el 17 % de los re-giros.
- Jackpot (🃏 🃏 🃏 en la línea): 1 de cada 21.000 tiradas. Además el bote cae
  solo antes de llegar a `POT_CAP`.
- Giros gratis (3 🎟️ en cualquier fila): 1 de cada 122 tiradas.
- Barra de bonus: se llena cada ~80 tiradas pagadas, de las que ~18 las pasa
  por encima de 90 puntos (`add_bonus`). Se pinta con una curva logarítmica
  (`bar_fill`): a la vista, ese tramo empieza ya en los 78 puntos.
- Celebraciones: ÉPICO (×50) 1 de cada ~2.200 tiradas, MEGA (×15) 1 de cada
  ~450 y GRAN PREMIO (×5) 1 de cada ~20.
"""

from __future__ import annotations

import bisect
import itertools
import math
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

# -- Símbolos -----------------------------------------------------------------------

CHERRY = "C"
LEMON = "L"
GRAPE = "G"
BELL = "B"
DIAMOND = "D"
SEVEN = "7"
#: Comodín: sustituye a cualquier símbolo de la línea menos al 🎟️. Tres
#: seguidos son el jackpot.
WILD = "W"
#: Dispersión: cuenta en cualquier fila y no paga, da giros gratis.
SCATTER = "S"


@dataclass(frozen=True, slots=True)
class SymbolInfo:
    """Cómo se llama y cómo se pinta un símbolo."""

    key: str
    emoji: str
    name: str


SYMBOLS: dict[str, SymbolInfo] = {
    s.key: s
    for s in (
        SymbolInfo(CHERRY, "🍒", "Cereza"),
        SymbolInfo(LEMON, "🍋", "Limón"),
        SymbolInfo(GRAPE, "🍇", "Uvas"),
        SymbolInfo(BELL, "🔔", "Campana"),
        SymbolInfo(DIAMOND, "💎", "Diamante"),
        SymbolInfo(SEVEN, "7️⃣", "Siete"),
        SymbolInfo(WILD, "🃏", "Comodín"),
        SymbolInfo(SCATTER, "🎟️", "Giros gratis"),
    )
}

#: Tiras de los tres rodillos, en el orden en que pasan al girar. Se eligieron
#: barajando un recuento de símbolos con una semilla fija y comprobando que
#: dos 🎟️ no puedan verse a la vez en el mismo rodillo. Cambiarlas cambia todas
#: las probabilidades: hay que volver a pasar los tests del retorno.
REEL_STRIPS: tuple[str, str, str] = (
    "CLWBGCCLG7GB7BCBCDCGLSLLGGLCLWSD",
    "7GLLDCCSLBLSGGCBBLLBGGCLCD7WGC",
    "CBGGCDCLGGBDLGLGBSLGLLBLL7CCSW",
)

#: Rodillo virtual: cuánto pesa cada posición de la tira al elegir dónde para.
#: Es el truco de la patente de Telnaes (1984) que usan todas las máquinas
#: modernas: la casilla de un 7️⃣ del tercer rodillo pesa poco y las de justo
#: encima y debajo pesan mucho, así que el 7️⃣ se ve rozando la línea muy a
#: menudo y entra en ella muy poco. Los pesos salen de un optimizador
#: (`docs/calibrar_tragaperras.py`) que busca el retorno, los casi-premios y la
#: frecuencia de premios de la docstring. Cambiar un peso cambia todo: hay que
#: volver a pasar los tests del retorno.
# fmt: off
REEL_WEIGHTS: tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]] = (
    (
        44, 15, 10, 1, 9, 16, 1, 5, 60, 1, 56, 2, 6, 1, 1, 1,
        5, 14, 2, 18, 2, 6, 5, 11, 6, 10, 3, 8, 3, 9, 8, 8,
    ),
    (
        1, 21, 4, 1, 1, 13, 6, 9, 1, 4, 8, 13, 27, 37, 13,
        4, 7, 1, 1, 15, 14, 21, 18, 1, 1, 10, 1, 11, 41, 12,
    ),
    (
        23, 14, 1, 11, 33, 6, 1, 12, 18, 9, 22, 5, 19, 1, 25,
        1, 36, 11, 50, 17, 20, 1, 3, 1, 3, 1, 8, 6, 36, 10,
    ),
)
# fmt: on

#: Tres iguales en la línea (con comodines): veces la apuesta.
THREE_OF_A_KIND: dict[str, int] = {
    CHERRY: 4,
    LEMON: 4,
    GRAPE: 10,
    BELL: 20,
    DIAMOND: 60,
    SEVEN: 200,
}
#: 🍒 🍒 en los dos primeros rodillos de la línea.
TWO_CHERRIES = 2
#: Una 🍒 en el primer rodillo devuelve la mitad de la apuesta. En medios,
#: para no usar decimales: `PAY_HALVES` guarda todos los premios ×2.
ONE_CHERRY_HALVES = 1

#: Premio mínimo, en veces la apuesta, que tiene que quedar a un símbolo para
#: que cuente como casi-premio: desde un trío de 🍇. Quedarse a uno de tres 🍒
#: no emociona a nadie.
NEAR_MISS_MIN_TIMES = 10
#: Valor del jackpot al comparar líneas (`_line_value`): más que cualquier trío.
JACKPOT_VALUE = 10**9

#: Símbolos que, si salen dos en la línea, hacen girar más el tercer rodillo.
HIGH_SYMBOLS = frozenset({SEVEN, DIAMOND, WILD})

#: Giros gratis por sacar 3 🎟️. También en un giro gratis: se suman a los que
#: queden (pasa en ~4 % de las tandas y apenas mueve el retorno).
FREE_SPINS = 5
#: Tiradas con premio que llenan la barra de la máquina caliente.
HEAT_MAX = 5
#: Multiplicador de la tirada caliente (solo la línea, no el bote).
HOT_MULTIPLIER = 2
#: Parte de cada apuesta que va al bote común, en tanto por ciento.
POT_SHARE_PERCENT = 6
#: Lo que pone la casa en el bote cuando se vacía (y al estrenarlo).
POT_SEED = 5_000
#: Tope del bote misterioso: el bote cae solo, sin 🃏 🃏 🃏, al cruzar un punto
#: oculto elegido al azar entre `POT_SEED` y este tope («tiene que caer antes
#: de 50.000»). Cuanto más cerca del tope, más prisa le entra a todo el canal.
POT_CAP = 50_000
#: Retorno de un re-giro del tercer rodillo: el precio es lo que vale de media
#: entre esto. El jugador paga un 0,5 % de más por la emoción de casi tenerlo.
RESPIN_RTP = 0.995
#: Puntos que llenan la barra de bonus.
BONUS_MAX = 100
#: Desde aquí la barra sube a cuentagotas.
BONUS_SLOW_FROM = 85
#: Puntos de más que da un casi-premio a la barra de bonus.
BONUS_NEAR_MISS = 3
#: Giros gratis que da la barra de bonus llena.
BONUS_FREE_SPINS = 3
#: Veces seguidas que se puede jugar a doble o nada un mismo premio.
DOUBLE_MAX = 5
#: Segundos sin jugar tras los que la máquina pierde un punto de calor.
HEAT_DECAY_SECONDS = 600
#: Curvatura de las barras de calor y de bonus. Se pintan con
#: `log(1 + k·x) / log(1 + k)`, con `x` la parte llena de verdad: con k = 9, la
#: mitad de los puntos llena el 74 % de la barra y el 90 % se ve a partir del 78 %.
#: Lo que cuesta llenarlas no cambia; solo cómo se ven.
BAR_CURVE = 9
#: Casillas de la barra de calor: más que `HEAT_MAX` para que cada paso de la
#: curva se vea (4, 6, 8 y 9 de 10). Con 5, la tercera y la cuarta tirada se
#: pintarían igual.
HEAT_CELLS = 10
#: Apuesta del giro diario gratis por cada día seguido de racha.
DAILY_STAKE = 100
#: Días de racha a partir de los que el giro diario ya no sube.
DAILY_STREAK_MAX = 7


#: Veces la apuesta (lo cobrado entre lo apostado) a partir de las que una tirada
#: se celebra a lo grande. Las máquinas de verdad celebran «BIG WIN» con premios
#: modestos: cuanto más a menudo suena la fanfarria, más se recuerda haber ganado.
BIG_WIN = 5
MEGA_WIN = 15
EPIC_WIN = 50


class WinTier:
    """Nivel de celebración de un premio; texto estable para el dibujo y los logros."""

    BIG = "big"
    MEGA = "mega"
    EPIC = "epic"


def win_tier(won: int, stake: int) -> str | None:
    """Nivel de celebración de lo cobrado (línea más bote) frente a la apuesta.

    Args:
        won: Todo lo cobrado en la tirada.
        stake: Apuesta de la tirada; en un giro gratis, la que lo activó.

    Returns:
        `WinTier.EPIC`, `MEGA` o `BIG`, o `None` si no llega a ×`BIG_WIN`.
    """
    if stake <= 0 or won < BIG_WIN * stake:
        return None
    if won >= EPIC_WIN * stake:
        return WinTier.EPIC
    if won >= MEGA_WIN * stake:
        return WinTier.MEGA
    return WinTier.BIG


# -- Tirada -------------------------------------------------------------------------


class Kind:
    """Tipo de premio de la línea; texto estable para la presentación y los logros."""

    JACKPOT = "jackpot"
    THREE = "three"
    TWO_CHERRIES = "two_cherries"
    CHERRY = "cherry"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class Spin:
    """Dónde han parado los rodillos y qué ha salido, sin dinero.

    Attributes:
        stops: Posición de la tira de cada rodillo que queda en la línea.
        grid: Filas visibles (arriba, línea, abajo); cada una con un
            símbolo por rodillo.
        kind: Tipo de premio de la línea (`Kind`).
        symbol: Símbolo que paga en un trío; `None` en el resto.
        pay_halves: Premio de la línea en medias apuestas (0 si no paga).
        scatters: 🎟️ visibles en toda la cuadrícula.
        near_miss: Por poco: el tercer rodillo ha dejado justo encima o
            debajo de la línea el símbolo que completaba un premio de
            ×`NEAR_MISS_MIN_TIMES` o más (`teaser`), o hay un trío gordo en
            una fila que no paga.
        anticipation: Los dos primeros rodillos prometen algo gordo (dos
            símbolos altos compatibles o dos 🎟️): el tercero gira más.
        teaser: El símbolo del tercer rodillo que, de haber parado en la
            línea, daba un premio mejor. Es el que permite comprar un re-giro
            (`respin_price`). `None` si no hay.
    """

    stops: tuple[int, int, int]
    grid: tuple[tuple[str, str, str], tuple[str, str, str], tuple[str, str, str]]
    kind: str
    symbol: str | None
    pay_halves: int
    scatters: int
    near_miss: bool
    anticipation: bool
    teaser: str | None = None

    @property
    def line(self) -> tuple[str, str, str]:
        """Los tres símbolos de la línea de pago."""
        return self.grid[1]

    @property
    def is_jackpot(self) -> bool:
        """Si es 🃏 🃏 🃏 en la línea."""
        return self.kind == Kind.JACKPOT

    @property
    def triggers_free_spins(self) -> bool:
        """Si hay 3 🎟️ a la vista (uno por rodillo)."""
        return self.scatters >= 3

    def emoji_grid(self) -> str:
        """La cuadrícula en emojis, con flechas en la línea (para el modo turbo)."""
        rows = []
        for index, row in enumerate(self.grid):
            symbols = " ".join(SYMBOLS[s].emoji for s in row)
            rows.append(f"▶️ {symbols} ◀️" if index == 1 else f"⬛ {symbols} ⬛")
        return "\n".join(rows)


_CUMULATIVE = tuple(tuple(itertools.accumulate(weights)) for weights in REEL_WEIGHTS)
_TOTALS = tuple(cumulative[-1] for cumulative in _CUMULATIVE)


def stop_probability(reel: int, stop: int) -> float:
    """Probabilidad de que el rodillo `reel` pare en `stop`."""
    return REEL_WEIGHTS[reel][stop] / _TOTALS[reel]


def _cell(reel: int, stop: int, offset: int) -> str:
    strip = REEL_STRIPS[reel]
    return strip[(stop + offset) % len(strip)]


def evaluate_line(line: tuple[str, str, str]) -> tuple[str, str | None, int]:
    """Premio de una línea: `(tipo, símbolo del trío, premio en medias apuestas)`.

    El comodín cuenta como cualquier símbolo menos el 🎟️, también como 🍒.
    """
    if line == (WILD, WILD, WILD):
        return Kind.JACKPOT, WILD, 0
    if SCATTER not in line:
        others = {s for s in line if s != WILD}
        if len(others) == 1:
            (symbol,) = others
            return Kind.THREE, symbol, THREE_OF_A_KIND[symbol] * 2
    first, second, _third = line
    if first in (CHERRY, WILD) and second in (CHERRY, WILD):
        return Kind.TWO_CHERRIES, None, TWO_CHERRIES * 2
    if first in (CHERRY, WILD):
        return Kind.CHERRY, None, ONE_CHERRY_HALVES
    return Kind.NONE, None, 0


def high_target(first: str, second: str) -> str | None:
    """Símbolo que completaría un premio gordo con estos dos primeros, si lo hay."""
    if first not in HIGH_SYMBOLS or second not in HIGH_SYMBOLS:
        return None
    if first == second:
        return first
    if WILD in (first, second):
        return second if first == WILD else first
    return None


def _is_big_three(row: tuple[str, str, str]) -> bool:
    """Si la fila sería un jackpot o un trío de símbolos altos."""
    kind, symbol, _pay = evaluate_line(row)
    return kind == Kind.JACKPOT or (kind == Kind.THREE and symbol in HIGH_SYMBOLS)


def _line_value(line: tuple[str, str, str]) -> int:
    """Valor de una línea para compararla con otra: el jackpot vale más que todo."""
    kind, _symbol, pay_halves = evaluate_line(line)
    return JACKPOT_VALUE if kind == Kind.JACKPOT else pay_halves


def _teaser(grid: tuple[tuple[str, str, str], ...]) -> str | None:
    """Símbolo del tercer rodillo, encima o debajo, que daba un premio gordo mejor."""
    first, second, third = grid[1]
    current = _line_value((first, second, third))
    best, teaser = current, None
    for symbol in (grid[0][2], grid[2][2]):
        value = _line_value((first, second, symbol))
        if value >= NEAR_MISS_MIN_TIMES * 2 and value > best:
            best, teaser = value, symbol
    return teaser


def spin_at(stops: tuple[int, int, int], *, count_scatters: bool = True) -> Spin:
    """Resultado de parar los rodillos en `stops` (determinista; útil en pruebas).

    Args:
        count_scatters: `False` en el re-giro, donde los 🎟️ no cuentan.
    """
    grid = tuple(tuple(_cell(reel, stops[reel], row - 1) for reel in range(3)) for row in range(3))
    line = grid[1]
    kind, symbol, pay_halves = evaluate_line(line)

    scatters = 0
    if count_scatters:
        scatters = sum(1 for reel in range(3) if any(row[reel] == SCATTER for row in grid))

    target = high_target(line[0], line[1])
    teaser = _teaser(grid)
    big_on_line = kind == Kind.JACKPOT or (kind == Kind.THREE and symbol in HIGH_SYMBOLS)
    near_miss = teaser is not None or (
        # Un trío gordo en la fila de arriba o la de abajo, que no pagan.
        not big_on_line and any(_is_big_three(row) for row in (grid[0], grid[2]))
    )
    scatter_tease = count_scatters and all(
        any(row[reel] == SCATTER for row in grid) for reel in (0, 1)
    )
    return Spin(
        stops=stops,
        grid=grid,  # type: ignore[arg-type]
        kind=kind,
        symbol=symbol,
        pay_halves=pay_halves,
        scatters=scatters,
        near_miss=near_miss,
        anticipation=target is not None or scatter_tease,
        teaser=teaser,
    )


class SlotMachine:
    """Elige dónde paran los rodillos.

    Args:
        randbelow: Devuelve un entero en `[0, n)`; inyectable en pruebas.
    """

    def __init__(self, randbelow: Callable[[int], int] | None = None) -> None:
        self._randbelow = randbelow or random.SystemRandom().randrange

    def _stop(self, reel: int) -> int:
        """Parada de un rodillo según los pesos del rodillo virtual."""
        ticket = self._randbelow(_TOTALS[reel])
        return bisect.bisect_right(_CUMULATIVE[reel], ticket)

    def spin(self) -> Spin:
        """Una tirada al azar (también los giros gratis: sus 🎟️ dan más giros)."""
        stops = tuple(self._stop(reel) for reel in range(3))
        return spin_at(stops)  # type: ignore[arg-type]

    def respin(self, spin: Spin) -> Spin:
        """Vuelve a girar solo el tercer rodillo de `spin` (el re-giro de pago).

        Los 🎟️ no cuentan: un re-giro no da giros gratis.
        """
        first, second, _third = spin.stops
        return spin_at((first, second, self._stop(2)), count_scatters=False)


# -- Dinero de una tirada -----------------------------------------------------------


def line_payout(spin: Spin, stake: int, *, hot: bool = False) -> int:
    """Lo que devuelve la línea (apuesta incluida), sin el bote.

    Args:
        stake: Apuesta de la tirada; en los giros gratis, la que los activó.
        hot: Si la máquina está caliente: la línea paga `HOT_MULTIPLIER` veces.
    """
    multiplier = HOT_MULTIPLIER if hot else 1
    return stake * spin.pay_halves * multiplier // 2


def respin_odds(spin: Spin) -> tuple[float, float]:
    """Lo que vale de media volver a girar el tercer rodillo de `spin`.

    Returns:
        `(medias apuestas esperadas de la línea, probabilidad de jackpot)`,
        recorriendo cada parada del tercer rodillo con su peso.
    """
    first, second, _third = spin.stops
    halves = jackpot = 0.0
    for stop in range(len(REEL_STRIPS[2])):
        chance = stop_probability(2, stop)
        line = (spin.line[0], spin.line[1], REEL_STRIPS[2][stop])
        kind, _symbol, pay_halves = evaluate_line(line)
        halves += chance * pay_halves
        if kind == Kind.JACKPOT:
            jackpot += chance
    return halves, jackpot


def respin_price(spin: Spin, stake: int, pot: int) -> int:
    """Precio de re-girar el tercer rodillo: su valor esperado entre `RESPIN_RTP`.

    Incluye la parte del bote: con 🃏 🃏 en los dos primeros, el re-giro puede
    llevarse el bote entero y su precio sube con él. Así nunca sale a cuenta
    re-girar, ni con el bote a rebosar. Se redondea hacia arriba y vale al
    menos 1 Y$.

    Args:
        stake: Apuesta de la tirada original (la línea paga en esa escala).
        pot: Bote actual del servidor.
    """
    halves, jackpot = respin_odds(spin)
    expected = stake * halves / 2 + jackpot * pot
    return max(1, math.ceil(expected / RESPIN_RTP))


def decayed_heat(heat: int, idle_seconds: float) -> int:
    """Calor que queda tras `idle_seconds` sin jugar: un punto menos cada `HEAT_DECAY_SECONDS`.

    Si te vas, la máquina se enfría y lo que habías calentado se pierde: hay
    prisa por volver.
    """
    lost = int(max(0.0, idle_seconds) // HEAT_DECAY_SECONDS)
    return max(0, heat - lost)


def next_daily_streak(last_day: date | None, today: date, streak: int) -> int:
    """Racha del giro diario si se cobra `today`: sigue si el último fue ayer, si no vuelve a 1."""
    if last_day is not None and today - last_day == timedelta(days=1):
        return streak + 1
    return 1


def daily_stake(streak: int) -> int:
    """Apuesta del giro diario gratis: `DAILY_STAKE` por día de racha, hasta `DAILY_STREAK_MAX`."""
    return DAILY_STAKE * max(1, min(streak, DAILY_STREAK_MAX))


def prize_table(stake: int) -> list[tuple[str, int | None]]:
    """Lo que paga cada combinación a la apuesta `stake`, como el cartel de una máquina de bar.

    Returns:
        Pares `(combinación en emojis, Y$ que devuelve)`, de mayor a menor.
        El jackpot lleva `None`: se lleva el bote, que cambia.
    """
    e = {key: info.emoji for key, info in SYMBOLS.items()}
    rows: list[tuple[str, int | None]] = [(f"{e[WILD]}{e[WILD]}{e[WILD]}", None)]
    for symbol, times in sorted(THREE_OF_A_KIND.items(), key=lambda item: -item[1]):
        rows.append((e[symbol] * 3, stake * times))
    rows.append((f"{e[CHERRY]}{e[CHERRY]}❔", stake * TWO_CHERRIES))
    rows.append((f"{e[CHERRY]}❔❔", stake * ONE_CHERRY_HALVES // 2))
    return rows


def pot_share(stake: int) -> int:
    """Parte de una apuesta que va al bote común (redondeada hacia abajo)."""
    return stake * POT_SHARE_PERCENT // 100


def next_heat(heat: int, *, paid: bool, was_hot: bool) -> int:
    """Calor de la máquina tras una tirada.

    Cada tirada con premio suma uno, aunque sea el medio premio de una 🍒:
    la barra se llena a menudo y siempre parece que falta poco. La tirada
    caliente gasta la barra entera, pague o no.
    """
    if was_hot:
        return 0
    return min(HEAT_MAX, heat + 1) if paid else heat


@dataclass(frozen=True, slots=True)
class BonusMeter:
    """La barra de bonus de un jugador.

    Attributes:
        points: Puntos de la barra, de 0 a `BONUS_MAX`.
        stake_sum: Suma de las apuestas de las tiradas que la han ido llenando.
        spins: Cuántas tiradas la han llenado. Los giros del bonus se juegan a
            la apuesta media (`stake_sum // spins`): así nadie la llena a 10 Y$
            y la cobra a 10.000.
    """

    points: int = 0
    stake_sum: int = 0
    spins: int = 0

    @property
    def average_stake(self) -> int:
        """Apuesta media de las tiradas que han llenado la barra (al menos 1)."""
        return max(1, self.stake_sum // self.spins) if self.spins else 1


def add_bonus(
    meter: BonusMeter, spin: Spin, stake: int, randbelow: Callable[[int], int]
) -> tuple[BonusMeter, int]:
    """Suma una tirada pagada a la barra de bonus.

    Lo que sube parece al azar: de 0 a 2 puntos por tirada y `BONUS_NEAR_MISS`
    más con un casi-premio, que así «casi» da algo. A partir de
    `BONUS_SLOW_FROM` sube a cuentagotas (un punto una de cada tres tiradas):
    la barra se queda un buen rato en el 90 y pico, que es cuando más cuesta
    levantarse. La curva del dibujo (`bar_fill`) alarga ese tramo: a la vista
    empieza en los 78 puntos. Es el efecto meta (goal gradient) de Hull y de las tarjetas de
    fidelidad: cuanto más cerca del final, más prisa. Llena, da
    `BONUS_FREE_SPINS` giros gratis a la apuesta media y vuelve a cero.

    Returns:
        `(barra nueva, apuesta de los giros del bonus o 0 si no se ha llenado)`.
    """
    if meter.points >= BONUS_SLOW_FROM:
        gain = int(randbelow(3) == 0) + int(spin.near_miss)
    else:
        gain = randbelow(3) + (BONUS_NEAR_MISS if spin.near_miss else 0)
    meter = BonusMeter(
        min(BONUS_MAX, meter.points + gain), meter.stake_sum + stake, meter.spins + 1
    )
    if meter.points < BONUS_MAX:
        return meter, 0
    return BonusMeter(), meter.average_stake


def bar_fill(value: int, maximum: int) -> float:
    """Parte de una barra que se ve llena, de 0 a 1, con la curva `BAR_CURVE`.

    Crece deprisa al principio y despacio al final: las primeras tiradas la
    llenan a la vista mucho más que las últimas, y siempre parece que falta
    poco. Solo cambia el dibujo; los puntos que hacen falta son los mismos.
    Llena solo se ve con `value >= maximum`.
    """
    if value <= 0:
        return 0.0
    if value >= maximum:
        return 1.0
    return math.log1p(BAR_CURVE * value / maximum) / math.log1p(BAR_CURVE)


def _bar_cells(value: int, maximum: int, width: int) -> int:
    """Casillas llenas de una barra de `width`: la última solo cuando está llena."""
    if value >= maximum:
        return width
    return min(width - 1, int(bar_fill(value, maximum) * width))


def bonus_bar(points: int, width: int = 10) -> str:
    """Barra de bonus con formas y porcentaje: `▰▰▰▰▰▰▰▱▱▱ 74 %`.

    Barra y porcentaje siguen la curva de `bar_fill` (50 puntos se ven como
    74 %), para que digan lo mismo. El 100 % solo sale con la barra llena.
    """
    points = max(0, min(BONUS_MAX, points))
    filled = _bar_cells(points, BONUS_MAX, width)
    percent = 100 if points >= BONUS_MAX else min(99, int(bar_fill(points, BONUS_MAX) * 100))
    return "▰" * filled + "▱" * (width - filled) + f" {percent} %"


def heat_bar(heat: int) -> str:
    """Barra de calor con formas (no solo colores): `🔥🔥🔥🔥🔥🔥▫️▫️▫️▫️`.

    Tiene `HEAT_CELLS` casillas y sigue la curva de `bar_fill`: la primera
    tirada con premio enciende 4, y las siguientes, 2, 2 y 1.
    """
    heat = max(0, min(HEAT_MAX, heat))
    filled = _bar_cells(heat, HEAT_MAX, HEAT_CELLS)
    return "🔥" * filled + "▫️" * (HEAT_CELLS - filled)


def paytable_lines() -> list[str]:
    """Tabla de premios para enseñar al jugador."""
    e = {key: info.emoji for key, info in SYMBOLS.items()}
    lines = [f"{e[WILD]} {e[WILD]} {e[WILD]} · **BOTE** entero"]
    for symbol, times in sorted(THREE_OF_A_KIND.items(), key=lambda item: -item[1]):
        lines.append(f"{e[symbol]} {e[symbol]} {e[symbol]} · ×{times}")
    lines.append(f"{e[CHERRY]} {e[CHERRY]} ❔ · ×{TWO_CHERRIES}")
    lines.append(f"{e[CHERRY]} ❔ ❔ · recuperas la mitad")
    lines.append(f"{e[SCATTER]} en los 3 rodillos (cualquier fila) · {FREE_SPINS} giros gratis")
    lines.append(f"{e[WILD]} sustituye a todo menos a {e[SCATTER]}")
    lines.append(f"🔥 Cada {HEAT_MAX} tiradas con premio, la siguiente paga ×{HOT_MULTIPLIER}")
    lines.append(f"💰 El {POT_SHARE_PERCENT} % de cada apuesta va al bote")
    return lines
