"""Apuestas deportivas: cuotas fijas sobre partidos de verdad, en yapdollars.

Lógica pura y el cliente de la API de cuotas, sin Discord ni base de datos. El cog
(`bot.cogs.sports`) enseña los partidos y mueve los botones; los partidos se guardan en
`bot.repositories.sports` y el dinero pasa por `EconomyService.sports_bet` y
`EconomyService.settle_sports_bet`.

De dónde salen los partidos. The Odds API (the-odds-api.com) da los próximos partidos de
cada competición de `SPORTS` con las cuotas 1X2 y de más/menos goles (`totals`) de las
casas de apuestas europeas (`OddsClient.odds`) y, al acabar, el marcador
(`OddsClient.scores`). El plan gratis da 500 créditos al mes: cada consulta de cuotas
cuesta 1 por mercado (0 si no hay partidos) y cada consulta de resultados con
`daysFrom`, 2. Por eso las consultas se espacian (`odds_due`, `scores_due`), el más/menos
solo va en algunas (`totals_due`) y se guarda una reserva para los resultados
(`QUOTA_RESERVE`). Con LaLiga y la Champions salen unos 330 créditos al mes con el 1X2 y
unos 60 más con el más/menos; otra competición sumaría unos 170 y no cabría.

Cómo se ponen las cuotas (`house_odds`). Cada casa de apuestas lleva su margen: sus
probabilidades implícitas (1 / cuota) suman más de 1. Se quitan normalizándolas, se
hace la media entre casas (`fair_probabilities`) y la casa del bot pone la suya con su
retorno: `cuota = RTP / probabilidad`, redondeada hacia abajo a la centésima. Con
`RTP` = 0,94 el jugador recupera de media el 94 % de lo que apuesta, como en las casas de
verdad (y algo menos que los caballos, por las noticias que la cuota del bot tarda en
recoger). Es un sumidero de dinero, como el resto del casino: lo apostado desaparece y el
premio se crea, y de media se crea menos de lo que desaparece. Un signo cuya cuota no
llegaría a `MIN_ODDS` sin regalar dinero no se ofrece.

Los goles (`fit_goals`). La API solo da los marcadores finales, así que los mercados
extra son los que se resuelven con ellos: más/menos goles (`GOAL_LINES`), ambos marcan y
resultado exacto (`Market`). Las casas no venden «ambos marcan» ni el exacto en la
consulta de cuotas (habría que pedirlos partido a partido, y no cabe en el plan gratis),
así que salen de un modelo: los goles de cada equipo siguen una Poisson con la corrección
de Dixon y Coles para los marcadores bajos (`RHO`), y sus dos medias se ajustan para que
den el 1X2 y el más/menos de las casas (sin su margen). De ahí sale la probabilidad de
cada marcador (`score_grid`) y, sumando marcadores, la de cada mercado (`side_odds`),
salvo las líneas de más/menos que dan las casas, que se cotizan con su probabilidad.
Medido el 25/10/2026 con 32 partidos y 4 consultas de «ambos marcan» y exacto: el modelo
ajustado al 1X2 y a una línea acierta las demás líneas de las casas por 1,5 puntos de
media (4,8 como mucho) y «ambos marcan», por 1-2 puntos. Solo con el 1X2 fallaba hasta
20 puntos: sin más/menos no hay mercados extra. Cada mercado tiene su retorno
(`Market.rtp`): el exacto, más bajo, como en las casas de verdad, porque es donde el
modelo más se equivoca. Si el ajuste no cuadra (`FIT_TOLERANCE`), el partido se queda
solo con el 1X2.

Qué se apuesta. El resultado al final del partido (1, X o 2) y los mercados de goles,
una apuesta por mercado, partido y persona y, sumando todos los mercados del partido, de
`MIN_STAKE` a `MAX_STAKE`. El tope existe porque las cuotas del bot se refrescan cada
pocas horas: quien se entere antes de una baja de última hora podría ganarle a una cuota
vieja, y el tope limita cuánto, también si carga varios mercados del mismo partido. Se
apuesta hasta el comienzo del partido. Lo que la API no da por terminado `VOID_AFTER`
después del comienzo (aplazado, suspendido o sin datos) se anula y se devuelve.

Combinadas (`combo_odds`, `combo_outcome`). Un boleto con de `MIN_LEGS` a `MAX_LEGS`
patas, cada una de un partido distinto, que se cobra solo si aciertan todas. La cuota es
el producto de las de las patas, así que el margen también se multiplica: con 3 patas el
jugador recupera de media 0,94³ ≈ 83 % y con 5, un 73 %, como en las casas de verdad. Dos
patas del mismo partido no se dejan juntar: no son independientes (quien gana 3-1 acierta
«1» y «más de 2,5») y multiplicar sus cuotas pagaría de más. El exacto no entra
(`COMBO_MARKETS`). Lo que se cobra tiene tope (`MAX_COMBO_PAYOUT`). Una pata fallada pierde
la combinada al momento; una anulada cuenta como 1,00 y, si se anulan todas, se devuelve.
Lo apostado cuenta entero en el tope de cada partido que toca (`MAX_STAKE`): el tope existe
por las cuotas viejas, y una combinada multiplica lo que se gana con una.

Limitación conocida: el marcador de la API es el final, así que en una eliminatoria con
prórroga cuenta lo que pasó en ella (en todos los mercados). En LaLiga y en la fase de
liga de la Champions no hay prórroga.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import aiohttp

#: Clave del juego en el libro (`deportes:apuesta`), en `casino_stats.GAMES` y en los logros.
GAME = "deportes"


@dataclass(frozen=True, slots=True)
class Sport:
    """Una competición con apuestas.

    Attributes:
        key: Clave de The Odds API (`soccer_spain_la_liga`).
        emoji: Icono en el panel.
        name: Nombre corto que se enseña.
    """

    key: str
    emoji: str
    name: str

    @property
    def label(self) -> str:
        """`🇪🇸 LaLiga`."""
        return f"{self.emoji} {self.name}"


#: Competiciones con apuestas, en el orden del panel. Añadir una gasta unos 150 créditos
#: más al mes de la API (ver la cabecera del módulo).
SPORTS: tuple[Sport, ...] = (
    Sport("soccer_spain_la_liga", "🇪🇸", "LaLiga"),
    Sport("soccer_uefa_champs_league", "🏆", "Champions"),
)
SPORT_BY_KEY: dict[str, Sport] = {sport.key: sport for sport in SPORTS}

#: Retorno al jugador: de cada 100 Y$ apostados vuelven 94 de media.
RTP = 0.94
#: Cuotas en centésimas (`185` = paga 1,85 veces lo apostado). La más baja que se
#: ofrece y la más alta: por encima se recorta, que solo baja el retorno.
MIN_ODDS = 101
MAX_ODDS = 2_500
#: Casas de apuestas con los tres signos que hacen falta para fiarse de un partido.
MIN_BOOKMAKERS = 3
#: Nombre del empate entre los resultados del mercado `h2h` de The Odds API.
DRAW_NAME = "Draw"
#: Apuesta mínima y máxima por partido.
MIN_STAKE = 10
MAX_STAKE = 50_000
#: Combinadas: de 2 a `MAX_LEGS` patas, cada una de un partido distinto.
MIN_LEGS = 2
MAX_LEGS = 5
#: Lo más que cobra una combinada, apuesta incluida. Del orden de lo que cobra una
#: sencilla al máximo (50.000 a 25,00 son 1,25 millones): sin tope, cinco patas a 25,00
#: pagarían cien millones de veces lo apostado.
MAX_COMBO_PAYOUT = 1_000_000

#: Cuánto hacia delante se piden partidos.
LOOKAHEAD_SECONDS = 7 * 86_400
#: Cada cuánto se refrescan las cuotas de una competición.
ODDS_EVERY = 8 * 3_600
#: Antes de cada comienzo, las cuotas se refrescan otra vez si tienen más de
#: `PRE_MATCH_EVERY`: así la última hora no se apuesta con cuotas de la mañana.
PRE_MATCH_WINDOW = 90 * 60
PRE_MATCH_EVERY = 60 * 60
#: Tras el comienzo, cuándo se empieza a preguntar el resultado (un partido dura unas
#: 1 h 55 min con el descanso) y cada cuánto se vuelve a preguntar.
RESULT_DELAY = 115 * 60
SCORES_EVERY = 20 * 60
#: Lo que no tiene resultado tras este tiempo se anula. La API solo devuelve resultados
#: de hasta 3 días atrás (`daysFrom`), así que tiene que ser algo menos.
VOID_AFTER = 70 * 3_600
#: Créditos de la API que se guardan para los resultados: por debajo, las cuotas solo se
#: piden una vez cada `LOW_QUOTA_EVERY`, lo justo para notar cuando la API renueva los
#: créditos a principio de mes.
QUOTA_RESERVE = 60
LOW_QUOTA_EVERY = 86_400
#: El más/menos (que cuesta otro crédito por consulta) va en la consulta de cuotas una vez
#: al día y, antes de un comienzo, si el que hay tiene más de `TOTALS_PRE_MATCH_EVERY`.
TOTALS_EVERY = 86_400
TOTALS_PRE_MATCH_EVERY = 6 * 3_600

#: Líneas de más/menos goles que se ofrecen.
GOAL_LINES = (1.5, 2.5, 3.5)
#: El exacto ofrece del 0-0 al `EXACT_MAX`-`EXACT_MAX`; el resto es «otro resultado».
EXACT_MAX = 3
#: Casas con una línea de más/menos que hacen falta para usarla en el ajuste.
MIN_TOTAL_BOOKS = 2
#: Corrección de Dixon y Coles: con ρ < 0 hay más 0-0 y 1-1 y menos 1-0 y 0-1 que en una
#: Poisson. −0,10 es la que mejor cuadra con el 1X2 de las casas (medido el 25/10/2026).
RHO = -0.10
#: Goles por equipo hasta los que se suma la rejilla de marcadores (lo de más es <0,01 %).
MAX_GOALS = 10
#: Error medio (en probabilidad) por encima del cual el ajuste no se cree y el partido
#: no tiene mercados de goles.
FIT_TOLERANCE = 0.03


class Pick(StrEnum):
    """Signo de la quiniela: gana el de casa, empate o gana el de fuera."""

    HOME = "1"
    DRAW = "X"
    AWAY = "2"


class Market(StrEnum):
    """Mercado de una apuesta. Se guarda con ella: una apuesta por mercado y partido."""

    RESULT = "1x2"
    GOALS = "goles"
    BOTH = "ambos"
    EXACT = "exacto"

    @property
    def label(self) -> str:
        """Nombre que se enseña."""
        return {
            Market.RESULT: "Resultado",
            Market.GOALS: "Más/menos goles",
            Market.BOTH: "Ambos marcan",
            Market.EXACT: "Resultado exacto",
        }[self]

    @property
    def rtp(self) -> float:
        """Retorno al jugador del mercado. El exacto deja el 15 % (las casas, 20-50 %)."""
        return 0.85 if self is Market.EXACT else RTP

    @property
    def picks(self) -> tuple[str, ...]:
        """Las opciones del mercado, en el orden del panel."""
        return MARKET_PICKS[self]


def line_text(line: float) -> str:
    """`2.5` → `2,5`."""
    return f"{line:.1f}".replace(".", ",")


#: Opciones de cada mercado: `1`/`X`/`2`; `+2.5` o `-2.5` (más o menos de 2,5 goles);
#: `si`/`no` (marcan los dos o no); `2-1` u `otro` (algún equipo mete más de `EXACT_MAX`).
MARKET_PICKS: dict[Market, tuple[str, ...]] = {
    Market.RESULT: tuple(pick.value for pick in Pick),
    Market.GOALS: tuple(f"{sign}{line}" for line in GOAL_LINES for sign in "+-"),
    Market.BOTH: ("si", "no"),
    Market.EXACT: (
        *(f"{h}-{a}" for h in range(EXACT_MAX + 1) for a in range(EXACT_MAX + 1)),
        "otro",
    ),
}
_MARKET_OF = {pick: market for market, picks in MARKET_PICKS.items() for pick in picks}
#: Mercados que pueden ser pata de una combinada. El exacto no: su retorno ya es el más
#: bajo y es donde más se equivoca el modelo, y multiplicarlo agranda las dos cosas.
COMBO_MARKETS = frozenset({Market.RESULT, Market.GOALS, Market.BOTH})


def market_of(pick: str) -> Market | None:
    """El mercado de una opción, o `None` si no es ninguna."""
    return _MARKET_OF.get(pick)


def pick_text(pick: str, home: str, away: str) -> str:
    """Lo apostado en palabras: `1 (Real Madrid)`, `Más de 2,5 goles`, `Exacto 2-1`…"""
    market = market_of(pick)
    if market is Market.RESULT:
        return {"1": f"1 ({home})", "X": "X (empate)", "2": f"2 ({away})"}[pick]
    if market is Market.GOALS:
        return f"{'Más' if pick[0] == '+' else 'Menos'} de {line_text(float(pick[1:]))} goles"
    if market is Market.BOTH:
        return "Marcan los dos" if pick == "si" else "No marcan los dos"
    if market is Market.EXACT:
        return f"Exacto {pick}" if pick != "otro" else "Otro resultado exacto"
    return pick


def wins(pick: str, score: Score) -> bool:
    """Si la opción `pick` gana con el marcador final `score`."""
    market = market_of(pick)
    if market is Market.RESULT:
        return score.pick == pick
    if market is Market.GOALS:
        over = score.home + score.away > float(pick[1:])
        return over if pick[0] == "+" else not over
    if market is Market.BOTH:
        return (score.home > 0 and score.away > 0) == (pick == "si")
    if market is Market.EXACT:
        if pick == "otro":
            return max(score.home, score.away) > EXACT_MAX
        return pick == f"{score.home}-{score.away}"
    return False


@dataclass(frozen=True, slots=True)
class Goals:
    """Goles que se esperan de cada equipo (las medias de sus Poisson)."""

    home: float
    away: float


@dataclass(frozen=True, slots=True)
class Match:
    """Un partido con sus cuotas.

    Attributes:
        event_id: Id de The Odds API.
        sport: Clave de `SPORTS`.
        home: Equipo de casa.
        away: Equipo de fuera.
        commence: Comienzo (epoch).
        odds: Cuota de cada signo en centésimas; 0 si no se ofrece.
        totals: Probabilidad sin margen de pasar de cada línea de goles (`{2.5: 0.52}`),
            media de las casas; vacío si no se sabe.
        goals: Los goles esperados ajustados a las casas (`fit_goals`), o `None` si no
            hay más/menos o el ajuste no cuadra: entonces solo se ofrece el 1X2.
        side: Cuota de cada opción de los mercados de goles (`side_odds`); vacío sin
            `goals`.
    """

    event_id: str
    sport: str
    home: str
    away: str
    commence: float
    odds: Mapping[Pick, int]
    totals: Mapping[float, float] = field(default_factory=dict)
    goals: Goals | None = None
    side: Mapping[str, int] = field(default_factory=dict)

    def team(self, pick: Pick) -> str:
        """El nombre del signo: el equipo, o «Empate»."""
        return {Pick.HOME: self.home, Pick.DRAW: "Empate", Pick.AWAY: self.away}[pick]

    @property
    def title(self) -> str:
        """`Real Madrid – Barcelona`."""
        return f"{self.home} – {self.away}"

    @property
    def open_picks(self) -> list[Pick]:
        """Los signos que se pueden apostar."""
        return [pick for pick in Pick if self.odds.get(pick, 0) >= MIN_ODDS]

    def price(self, pick: str) -> int:
        """Cuota de cualquier opción en centésimas; 0 si no se ofrece."""
        if market_of(pick) is Market.RESULT:
            odds = self.odds.get(Pick(pick), 0)
        else:
            odds = self.side.get(pick, 0)
        return odds if odds >= MIN_ODDS else 0

    def markets(self) -> list[Market]:
        """Los mercados con alguna opción a la venta."""
        return [m for m in Market if any(self.price(pick) for pick in m.picks)]


def priced(match: Match, totals: Mapping[float, float], fair: Mapping[Pick, float]) -> Match:
    """`match` con su más/menos, los goles ajustados y las cuotas de los mercados de goles.

    Args:
        totals: Probabilidades de pasar de cada línea (vacío: sin mercados de goles).
        fair: Probabilidades sin margen del 1X2, para el ajuste.
    """
    goals = fit_goals(fair, totals) if totals else None
    return replace(
        match, totals=dict(totals), goals=goals, side=side_odds(goals, totals) if goals else {}
    )


@dataclass(frozen=True, slots=True)
class Score:
    """Marcador final de un partido."""

    home: int
    away: int

    @property
    def pick(self) -> Pick:
        """El signo que ha salido."""
        if self.home > self.away:
            return Pick.HOME
        if self.home < self.away:
            return Pick.AWAY
        return Pick.DRAW


# -- Cuotas -------------------------------------------------------------------------------


def fair_probabilities(books: Iterable[Mapping[Pick, float]]) -> dict[Pick, float]:
    """Probabilidad de cada signo sin el margen de las casas, media de todas.

    Args:
        books: Las cuotas decimales de cada casa (`{Pick.HOME: 1.85, …}`), con los
            tres signos.

    Returns:
        Probabilidades que suman 1, o `{}` si no hay casas.
    """
    totals = dict.fromkeys(Pick, 0.0)
    count = 0
    for book in books:
        implied = {pick: 1 / book[pick] for pick in Pick}
        overround = sum(implied.values())
        for pick in Pick:
            totals[pick] += implied[pick] / overround
        count += 1
    if not count:
        return {}
    return {pick: total / count for pick, total in totals.items()}


def house_odds(probabilities: Mapping[Pick, float]) -> dict[Pick, int]:
    """Cuota del bot de cada signo, en centésimas (0 si no se ofrece).

    Hacia abajo y con tope arriba, así nunca devuelve más de `RTP`. Un signo tan
    favorito que su cuota no llega a `MIN_ODDS` se queda sin cuota: subirla a 1,01
    regalaría dinero.
    """
    odds = {}
    for pick in Pick:
        probability = probabilities.get(pick, 0.0)
        cents = math.floor(100 * RTP / probability) if probability > 0 else MAX_ODDS
        odds[pick] = min(cents, MAX_ODDS) if cents >= MIN_ODDS else 0
    return odds


def payout(stake: int, odds: int) -> int:
    """Lo que cobra una apuesta acertada, apuesta incluida (hacia abajo)."""
    return stake * odds // 100


# -- Combinadas ---------------------------------------------------------------------------


def combo_odds(legs: Iterable[int]) -> int:
    """Cuota de una combinada en centésimas: el producto de sus patas, hacia abajo.

    Se redondea hacia abajo en cada paso, así que nunca pasa del producto exacto: el
    margen de cada pata se queda entero.
    """
    total = 100
    for odds in legs:
        total = total * odds // 100
    return total


def combo_payout(stake: int, odds: int) -> int:
    """Lo que cobra una combinada acertada, apuesta incluida, con el tope del boleto."""
    return min(payout(stake, odds), MAX_COMBO_PAYOUT)


class LegStatus(StrEnum):
    """Cómo está cada pata de una combinada (y la combinada entera)."""

    OPEN = "open"
    WON = "won"
    LOST = "lost"
    VOID = "void"


def leg_status(pick: str, score: Score | None, *, commence: float, now: float) -> LegStatus:
    """Cómo queda una pata con el marcador que hay ahora (`None`, sin resultado).

    Sin resultado sigue abierta hasta `VOID_AFTER` después del comienzo y luego se anula,
    igual que una apuesta sencilla.
    """
    if score is not None:
        return LegStatus.WON if wins(pick, score) else LegStatus.LOST
    if now >= commence + VOID_AFTER:
        return LegStatus.VOID
    return LegStatus.OPEN


def combo_outcome(legs: Iterable[tuple[LegStatus, int]]) -> tuple[LegStatus, int]:
    """Cómo queda una combinada con el estado y la cuota de cada pata.

    - Con una pata fallada, perdida al momento, sin esperar a las demás.
    - Con alguna aún abierta, abierta.
    - Todas anuladas: anulada (se devuelve lo apostado).
    - Si no, ganada a la cuota de las acertadas: una pata anulada cuenta como 1,00.

    Returns:
        El estado y la cuota con la que se cobra (0 si no se cobra).
    """
    statuses = list(legs)
    if any(status is LegStatus.LOST for status, _ in statuses):
        return LegStatus.LOST, 0
    if any(status is LegStatus.OPEN for status, _ in statuses):
        return LegStatus.OPEN, 0
    won = [odds for status, odds in statuses if status is LegStatus.WON]
    if not won:
        return LegStatus.VOID, 0
    return LegStatus.WON, combo_odds(won)


def _cents(probability: float, rtp: float) -> int:
    """Cuota en centésimas con retorno `rtp`, como `house_odds`: 0 si no llega a `MIN_ODDS`."""
    if probability <= 0:
        return MAX_ODDS
    cents = math.floor(100 * rtp / probability)
    return min(cents, MAX_ODDS) if cents >= MIN_ODDS else 0


# -- Goles --------------------------------------------------------------------------------


def score_grid(goals: Goals) -> list[list[float]]:
    """Probabilidad de cada marcador (`grid[casa][fuera]`) hasta `MAX_GOALS`, sumando 1.

    Dos Poisson independientes con la corrección de Dixon y Coles (`RHO`) en 0-0, 1-0,
    0-1 y 1-1.
    """
    home = [math.exp(-goals.home) * goals.home**k / math.factorial(k) for k in range(MAX_GOALS + 1)]
    away = [math.exp(-goals.away) * goals.away**k / math.factorial(k) for k in range(MAX_GOALS + 1)]
    grid = [[h * a for a in away] for h in home]
    grid[0][0] *= 1 - goals.home * goals.away * RHO
    grid[0][1] *= 1 + goals.home * RHO
    grid[1][0] *= 1 + goals.away * RHO
    grid[1][1] *= 1 - RHO
    total = sum(map(sum, grid))
    return [[cell / total for cell in row] for row in grid]


def _chance(grid: list[list[float]], pick: str) -> float:
    """Probabilidad de que gane `pick` según la rejilla de marcadores."""
    return sum(
        cell for h, row in enumerate(grid) for a, cell in enumerate(row) if wins(pick, Score(h, a))
    )


def _fit_chances(goals: Goals, lines: Iterable[float]) -> list[float]:
    """1, X, 2 y pasar de cada línea, sumando la rejilla a mano (`_chance` es lenta aquí)."""
    grid = score_grid(goals)
    home = draw = 0.0
    by_total = [0.0] * (2 * MAX_GOALS + 1)
    for h, row in enumerate(grid):
        for a, cell in enumerate(row):
            by_total[h + a] += cell
            if h > a:
                home += cell
            elif h == a:
                draw += cell
    return [home, draw, 1 - home - draw] + [sum(by_total[math.ceil(line) :]) for line in lines]


def fit_goals(fair: Mapping[Pick, float], totals: Mapping[float, float]) -> Goals | None:
    """Los goles esperados de cada equipo que mejor dan el 1X2 y el más/menos de las casas.

    Mínimos cuadrados sobre las tres probabilidades del 1X2 y la de pasar de cada línea,
    por Gauss-Newton en logaritmos (así las medias nunca son negativas).

    Args:
        fair: Probabilidades sin margen del 1X2.
        totals: Probabilidad sin margen de pasar de cada línea (`{2.5: 0.52}`).

    Returns:
        Los goles esperados, o `None` si falta el 1X2 o el ajuste no cuadra
        (`FIT_TOLERANCE`).
    """
    if len(fair) != len(Pick) or not totals:
        return None
    lines = list(totals)
    targets = [fair[pick] for pick in Pick] + [totals[line] for line in lines]

    def residuals(x: tuple[float, float]) -> list[float]:
        chances = _fit_chances(Goals(math.exp(x[0]), math.exp(x[1])), lines)
        return [c - t for c, t in zip(chances, targets, strict=True)]

    x = (math.log(1.4), math.log(1.1))
    step = 1e-5
    for _ in range(40):
        r = residuals(x)
        j0 = [(a - b) / step for a, b in zip(residuals((x[0] + step, x[1])), r, strict=True)]
        j1 = [(a - b) / step for a, b in zip(residuals((x[0], x[1] + step)), r, strict=True)]
        a = sum(v * v for v in j0)
        b = sum(u * v for u, v in zip(j0, j1, strict=True))
        c = sum(v * v for v in j1)
        g0 = sum(u * v for u, v in zip(j0, r, strict=True))
        g1 = sum(u * v for u, v in zip(j1, r, strict=True))
        det = a * c - b * b
        if det <= 1e-15:
            return None
        d0 = max(-1.0, min(1.0, (c * g0 - b * g1) / det))
        d1 = max(-1.0, min(1.0, (a * g1 - b * g0) / det))
        x = (x[0] - d0, x[1] - d1)
        if abs(d0) + abs(d1) < 1e-9:
            break
    goals = Goals(math.exp(x[0]), math.exp(x[1]))
    if not (0.05 <= goals.home <= 8 and 0.05 <= goals.away <= 8):
        return None
    error = residuals(x)
    if math.sqrt(sum(e * e for e in error) / len(error)) > FIT_TOLERANCE:
        return None
    return goals


def side_odds(goals: Goals, totals: Mapping[float, float] | None = None) -> dict[str, int]:
    """Cuota de cada opción de los mercados de goles, con el retorno de su mercado.

    Las líneas de más/menos que dan las casas (`totals`) se cotizan con su probabilidad,
    como el 1X2: una Poisson no clava a la vez varias líneas con colas más gordas que
    las suyas, y medido contra las casas alguna pasaba del 100 % de retorno. El modelo
    queda para las líneas que no dan, «ambos marcan» y el exacto.
    """
    grid = score_grid(goals)
    odds = {
        pick: _cents(_chance(grid, pick), market.rtp)
        for market in Market
        if market is not Market.RESULT
        for pick in market.picks
    }
    for line, over in (totals or {}).items():
        if line in GOAL_LINES:
            odds[f"+{line}"] = _cents(over, Market.GOALS.rtp)
            odds[f"-{line}"] = _cents(1 - over, Market.GOALS.rtp)
    return odds


def parse_odds(
    events: Sequence[Mapping[str, Any]],
    *,
    now: float,
    known_totals: Mapping[str, Mapping[float, float]] | None = None,
) -> list[Match]:
    """Los partidos de una respuesta de cuotas de The Odds API (`dateFormat=unix`).

    Se quedan los que no han empezado y tienen al menos `MIN_BOOKMAKERS` casas con
    los tres signos del mercado `h2h`. Lo que no tenga la forma esperada se salta.

    Args:
        known_totals: El más/menos que ya se sabía de cada partido (por `event_id`), para
            los que no lo traen (la consulta solo lo pide a veces, `totals_due`).
    """
    known = known_totals or {}
    matches = []
    for event in events:
        try:
            home, away = str(event["home_team"]), str(event["away_team"])
            commence = float(event["commence_time"])
            event_id, sport = str(event["id"]), str(event["sport_key"])
            bookmakers = _items(event.get("bookmakers"))
        except (KeyError, TypeError, ValueError):
            continue
        if commence <= now or home == away:
            continue
        books = [book for b in bookmakers if (book := _book(b, home, away)) is not None]
        if len(books) < MIN_BOOKMAKERS:
            continue
        fair = fair_probabilities(books)
        totals = _totals(bookmakers) or known.get(event_id, {})
        match = Match(event_id, sport, home, away, commence, house_odds(fair))
        matches.append(priced(match, totals, fair))
    return sorted(matches, key=lambda m: (m.commence, m.home))


def _totals(bookmakers: list[Any]) -> dict[float, float]:
    """Probabilidad sin margen de pasar de cada línea de goles, media de las casas.

    Solo líneas de medio gol (sin empate posible) que den al menos `MIN_TOTAL_BOOKS`
    casas; una casa con la línea mal formada no cuenta.
    """
    seen: dict[float, list[float]] = {}
    for bookmaker in bookmakers:
        if not isinstance(bookmaker, Mapping):
            continue
        for market in _items(bookmaker.get("markets")):
            if not isinstance(market, Mapping) or market.get("key") != "totals":
                continue
            prices: dict[float, dict[str, float]] = {}
            for outcome in _items(market.get("outcomes")):
                try:
                    line = float(outcome["point"])
                    price = float(outcome["price"])
                    side = str(outcome["name"])
                except (KeyError, TypeError, ValueError):
                    continue
                if math.isfinite(price) and price > 1 and (line * 2) % 2 == 1:
                    prices.setdefault(line, {})[side] = price
            for line, sides in prices.items():
                if {"Over", "Under"} <= sides.keys():
                    over, under = 1 / sides["Over"], 1 / sides["Under"]
                    seen.setdefault(line, []).append(over / (over + under))
    return {
        line: sum(values) / len(values)
        for line, values in sorted(seen.items())
        if len(values) >= MIN_TOTAL_BOOKS
    }


def _book(bookmaker: object, home: str, away: str) -> dict[Pick, float] | None:
    """Las tres cuotas `h2h` de una casa, o `None` si falta alguna o no tiene sentido.

    Un resultado que no es ninguno de los equipos ni `DRAW_NAME`, o uno repetido,
    descarta la casa: no se adivina qué quería decir.
    """
    if not isinstance(bookmaker, Mapping):
        return None
    names = {home: Pick.HOME, away: Pick.AWAY, DRAW_NAME: Pick.DRAW}
    for market in _items(bookmaker.get("markets")):
        if not isinstance(market, Mapping) or market.get("key") != "h2h":
            continue
        book: dict[Pick, float] = {}
        for outcome in _items(market.get("outcomes")):
            if not isinstance(outcome, Mapping) or not isinstance(outcome.get("name"), str):
                return None
            pick = names.get(outcome["name"])
            if pick is None or pick in book:
                return None
            try:
                price = float(outcome["price"])
            except (KeyError, TypeError, ValueError):
                return None
            if not (math.isfinite(price) and price > 1):
                return None
            book[pick] = price
        return book if len(book) == len(Pick) else None
    return None


def _items(value: object) -> list[Any]:
    """`value` si es una lista; si no (falta o viene mal), una lista vacía."""
    return value if isinstance(value, list) else []


def parse_scores(events: Sequence[Mapping[str, Any]]) -> dict[str, Score]:
    """Marcadores de los partidos terminados de una respuesta de resultados."""
    results = {}
    for event in events:
        if not event.get("completed"):
            continue
        try:
            goals = {str(s["name"]): int(s["score"]) for s in event.get("scores") or []}
            score = Score(goals[str(event["home_team"])], goals[str(event["away_team"])])
            results[str(event["id"])] = score
        except (KeyError, TypeError, ValueError):
            continue
    return results


# -- Cuándo preguntar -------------------------------------------------------------------


def odds_due(
    *, now: float, fetched_at: float | None, next_kickoff: float | None, remaining: int | None
) -> bool:
    """Si toca pedir las cuotas de una competición.

    Cada `ODDS_EVERY` y, en los `PRE_MATCH_WINDOW` antes de un comienzo, si las que hay
    tienen más de `PRE_MATCH_EVERY`. Con menos de `QUOTA_RESERVE` créditos
    (`remaining`), solo una vez cada `LOW_QUOTA_EVERY`.
    """
    if fetched_at is None:
        return True
    age = now - fetched_at
    if not quota_allows_odds(remaining):
        return age >= LOW_QUOTA_EVERY
    if age >= ODDS_EVERY:
        return True
    soon = next_kickoff is not None and 0 <= next_kickoff - now <= PRE_MATCH_WINDOW
    return soon and age >= PRE_MATCH_EVERY


def scores_due(*, now: float, checked_at: float | None, oldest: float | None) -> bool:
    """Si toca pedir resultados: hay apuestas de un partido que ya debería haber acabado.

    Args:
        oldest: Comienzo del partido más antiguo con apuestas sin resolver.
    """
    if oldest is None or now < oldest + RESULT_DELAY:
        return False
    return checked_at is None or now - checked_at >= SCORES_EVERY


def days_from(*, now: float, oldest: float) -> int:
    """`daysFrom` para pedir resultados: 1 basta salvo que algo lleve casi un día."""
    return 1 if now - oldest < 20 * 3_600 else 3


def totals_due(
    *, now: float, fetched_at: float | None, next_kickoff: float | None, remaining: int | None
) -> bool:
    """Si la consulta de cuotas que toca lleva también el más/menos (un crédito más).

    Una vez cada `TOTALS_EVERY` y, en los `PRE_MATCH_WINDOW` antes de un comienzo, si el
    que hay tiene más de `TOTALS_PRE_MATCH_EVERY` (las alineaciones cambian los goles).
    Nunca tocando la reserva de los resultados.
    """
    if not quota_allows_odds(remaining):
        return False
    if fetched_at is None or now - fetched_at >= TOTALS_EVERY:
        return True
    soon = next_kickoff is not None and 0 <= next_kickoff - now <= PRE_MATCH_WINDOW
    return soon and now - fetched_at >= TOTALS_PRE_MATCH_EVERY


def quota_allows_odds(remaining: int | None) -> bool:
    """Si quedan créditos para pedir cuotas sin tocar la reserva de los resultados."""
    return remaining is None or remaining > QUOTA_RESERVE


# -- Cliente de la API ------------------------------------------------------------------


class FeedError(Exception):
    """La API de cuotas no ha contestado bien. El mensaje nunca lleva la clave."""


@dataclass(frozen=True, slots=True)
class FeedReply:
    """Una respuesta de la API.

    Attributes:
        events: Los partidos que devuelve.
        remaining: Créditos que quedan este mes (cabecera `x-requests-remaining`), o
            `None` si no viene.
    """

    events: list[dict[str, Any]]
    remaining: int | None


class OddsClient:
    """Cliente mínimo de The Odds API v4.

    Args:
        session: Sesión HTTP del cog (la cierra él).
        api_key: Clave de la API. Es secreta: va en la consulta y nunca en un error.
    """

    BASE_URL = "https://api.the-odds-api.com/v4/sports"

    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        self.session = session
        self.api_key = api_key

    async def odds(self, sport: str, *, now: float, totals: bool = False) -> FeedReply:
        """Cuotas 1X2 (y, con `totals`, más/menos) de las casas europeas para los próximos
        `LOOKAHEAD_SECONDS`. Cada mercado cuesta un crédito."""
        return await self._get(
            f"{self.BASE_URL}/{sport}/odds/",
            {
                "regions": "eu",
                "markets": "h2h,totals" if totals else "h2h",
                "oddsFormat": "decimal",
                "dateFormat": "unix",
                "commenceTimeFrom": _iso(now),
                "commenceTimeTo": _iso(now + LOOKAHEAD_SECONDS),
            },
        )

    async def scores(self, sport: str, *, days: int) -> FeedReply:
        """Partidos en juego y terminados de los últimos `days` días (1 a 3)."""
        return await self._get(
            f"{self.BASE_URL}/{sport}/scores/", {"daysFrom": str(days), "dateFormat": "unix"}
        )

    async def _get(self, url: str, params: dict[str, str]) -> FeedReply:
        try:
            async with self.session.get(url, params={**params, "apiKey": self.api_key}) as reply:
                remaining = _int_header(reply.headers.get("x-requests-remaining"))
                if reply.status != 200:
                    raise FeedError(f"La API de cuotas contesta {reply.status}")
                payload = await reply.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as error:
            # Sin encadenar: el error de aiohttp lleva la URL, y la URL lleva la clave.
            raise FeedError(f"La API de cuotas no responde ({type(error).__name__})") from None
        if not isinstance(payload, list):
            raise FeedError("La API de cuotas devuelve algo que no es una lista")
        return FeedReply([e for e in payload if isinstance(e, dict)], remaining)


def _iso(epoch: float) -> str:
    """Fecha en el formato que pide la API: `2026-10-20T18:00:00Z`."""
    return datetime.fromtimestamp(int(epoch), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _int_header(value: str | None) -> int | None:
    """Una cabecera numérica (la API manda `"497"` o `"497.0"`), o `None`."""
    try:
        return int(float(value)) if value is not None else None
    except ValueError:
        return None
