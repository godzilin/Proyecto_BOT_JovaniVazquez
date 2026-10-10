"""Estadísticas del casino (`apuestas`): reglas, cifras derivadas y textos.

Cada jugada terminada de cualquier juego del casino se apunta una vez en
`casino_plays` (`bot.repositories.casino_stats`) con su apuesta, lo que
devolvió, el IRPF que movió y el saldo con el que quedó el jugador. De ahí
sale todo lo que enseña `apuestas`: cuántas partidas, cuánto se apuesta de
media, qué juego devuelve más de verdad, a qué hora se pierde más dinero,
los récords y el ranking.

Lo que no estaba apuntado antes de que existiera la tabla se saca del libro de
la economía (`EconomyService.casino_ledger`): los movimientos `<juego>:apuesta`,
`<juego>:premio` y `<juego>:bote` llevan ahí desde el primer día, así que el
dinero total apostado y pagado es completo aunque las jugadas no lo sean.

Este módulo no toca Discord ni la base de datos: recibe números del
repositorio y devuelve números y textos. El cog (`bot.cogs.apuestas`) solo
los pinta.

Qué es una «jugada»:

- Una tirada de ruleta, una mano de blackjack (con dobles y separaciones), una
  tirada de tragaperras, una tanda de pachinko, una ronda de crash por jugador,
  una partida de minas, de pollo, de cara o cruz o de dados (de la salida a
  que se decide, con las Odds dentro) y una tirada base de los botes.
- El bonus de los botes y los giros gratis de la tragaperras son jugadas
  **gratis** (apuesta 0): suman a lo pagado y a los premios, pero no cuentan
  como apuestas ni bajan la apuesta media.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from enum import Enum

from bot.services.economy import format_amount
from bot.services.taxes import TAX_COLLECTOR

#: Juegos que se apuntan, con su emoji y su nombre visible. La clave es la que
#: usa cada juego en el libro de la economía (`ruleta:apuesta`…), así que las
#: jugadas y el libro se pueden cruzar. Los botes guardan su tema en el libro
#: (`volcan:apuesta`), por eso `LEDGER_ALIASES` los junta en `botes`.
GAMES: dict[str, tuple[str, str]] = {
    "ruleta": ("🎡", "Ruleta"),
    "blackjack": ("🃏", "Blackjack"),
    "tragaperras": ("🎰", "Tragaperras"),
    "botes": ("🌋", "Botes"),
    "crash": ("🚀", "Crash"),
    "minas": ("💣", "Minas"),
    "pollo": ("🐔", "Pollo"),
    "moneda": ("🪙", "Cara o cruz"),
    "autobus": ("🚌", "Autobús"),
    "dados": ("🎲", "Dados"),
    "pachinko": ("🌸", "Pachinko"),
    "caballos": ("🏇", "Caballos"),
    "porra": ("🎫", "Porras"),
}

#: Prefijos del libro que pertenecen a otro juego de `GAMES`.
LEDGER_ALIASES: dict[str, str] = {"volcan": "botes", "olimpo": "botes", "filon": "botes"}

#: Una tirada de referencia del casino (Biblia, «Equilibrio: medir en tiradas»).
REFERENCE_STAKE = 100

#: Tramos de apuesta para la distribución: (hasta, etiqueta). El último no tiene tope.
STAKE_BUCKETS: tuple[tuple[int | None, str], ...] = (
    (99, "< 100"),
    (100, "100 justos"),
    (999, "101–999"),
    (9_999, "1.000–9.999"),
    (99_999, "10.000–99.999"),
    (None, "≥ 100.000"),
)

#: Partidas mínimas para entrar en los rankings de porcentaje (RTP, victorias):
#: con menos, el que juega una vez y gana sale primero.
MIN_PLAYS_FOR_RATES = 50

#: Días que enseña la gráfica diaria.
DAILY_WINDOW = 14

#: Cuántas filas tiene cada lista de récords y de ranking.
TOP = 5

WEEKDAYS = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")


class Period(Enum):
    """Ventana de tiempo que se enseña."""

    TODAY = ("hoy", "Hoy")
    WEEK = ("semana", "7 días")
    MONTH = ("mes", "30 días")
    ALL = ("siempre", "Siempre")

    @property
    def key(self) -> str:
        """Clave estable (la de los botones)."""
        return self.value[0]

    @property
    def label(self) -> str:
        """Nombre visible."""
        return self.value[1]


def period_start(period: Period, today_start: float) -> float | None:
    """Epoch desde el que cuenta `period`; `None` para «siempre».

    Args:
        today_start: Medianoche de hoy en hora canaria (epoch).
    """
    if period is Period.TODAY:
        return today_start
    if period is Period.WEEK:
        return today_start - 6 * 86_400
    if period is Period.MONTH:
        return today_start - 29 * 86_400
    return None


def ledger_game(prefix: str) -> str | None:
    """Juego de `GAMES` al que pertenece un prefijo del libro (`volcan` → `botes`)."""
    if prefix in GAMES:
        return prefix
    return LEDGER_ALIASES.get(prefix)


def game_label(game: str) -> str:
    """`ruleta` → `🎡 Ruleta`; un juego desconocido sale tal cual."""
    emoji, name = GAMES.get(game, ("🎲", game.capitalize()))
    return f"{emoji} {name}"


# -- Datos -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Play:
    """Una jugada terminada, tal y como se guarda.

    Attributes:
        game: Clave de `GAMES`.
        stake: Lo apostado en la jugada (0 en las gratis).
        payout: Lo devuelto, apuesta incluida (0 si se pierde todo).
        tax: IRPF que movió: positivo si se retuvo, negativo si se devolvió.
        balance_after: Saldo del jugador al terminar.
        details: Datos propios del juego, como pares `(clave, valor)`, para las
            propuestas de las porras (`bot.services.porras`): `boom` en minas,
            `splat` en el pollo, `bust` y `natural` en el blackjack y `started`
            (epoch de cuando se cobró la apuesta) en los juegos que duran. No
            se guardan en `casino_plays`.
    """

    game: str
    stake: int
    payout: int
    tax: int
    balance_after: int
    details: tuple[tuple[str, int], ...] = ()

    def detail(self, key: str, default: int = 0) -> int:
        """Valor de `details` para `key`, o `default` si el juego no lo manda."""
        return dict(self.details).get(key, default)

    @property
    def net(self) -> int:
        """Ganancia (positiva) o pérdida (negativa) antes de impuestos."""
        return self.payout - self.stake

    @property
    def all_in(self) -> bool:
        """Si se jugó todo el saldo (la misma regla que los logros)."""
        before = self.balance_after - self.net
        return self.stake > 0 and self.stake >= before > 0


@dataclass(slots=True)
class Totals:
    """Lo agregado de un grupo de jugadas (un juego o todo el casino).

    Las cuentas son sumas, así que dos `Totals` se pueden juntar (`merge`).
    Los porcentajes se calculan al pedirlos para no guardar dos veces lo mismo.
    """

    plays: int = 0
    free_plays: int = 0
    wagered: int = 0
    paid: int = 0
    free_paid: int = 0
    wins: int = 0
    pushes: int = 0
    losses: int = 0
    busts: int = 0
    won_amount: int = 0
    lost_amount: int = 0
    max_stake: int = 0
    best: int = 0
    worst: int = 0
    max_mult: float = 0.0
    withheld: int = 0
    refunded: int = 0
    all_ins: int = 0
    all_in_wins: int = 0
    broke: int = 0

    def merge(self, other: Totals) -> Totals:
        """Suma `other` a este (y lo devuelve, para encadenar)."""
        for name in (
            "plays", "free_plays", "wagered", "paid", "free_paid", "wins", "pushes",
            "losses", "busts", "won_amount", "lost_amount", "withheld", "refunded",
            "all_ins", "all_in_wins", "broke",
        ):  # fmt: skip
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.max_stake = max(self.max_stake, other.max_stake)
        self.best = max(self.best, other.best)
        self.worst = min(self.worst, other.worst)
        self.max_mult = max(self.max_mult, other.max_mult)
        return self

    @property
    def net(self) -> int:
        """Resultado del jugador antes de impuestos: pagado menos apostado."""
        return self.paid - self.wagered

    @property
    def after_tax(self) -> int:
        """Resultado del jugador después de lo que se quedó Hacienda."""
        return self.net - self.withheld + self.refunded

    @property
    def rtp(self) -> float | None:
        """Retorno al jugador real: pagado / apostado. `None` sin apuestas."""
        return self.paid / self.wagered if self.wagered else None

    @property
    def house_edge(self) -> float | None:
        """Lo que se ha quedado la casa por cada Y$ apostado."""
        rtp = self.rtp
        return None if rtp is None else 1 - rtp

    @property
    def average_stake(self) -> float:
        """Apuesta media de las jugadas pagadas."""
        return self.wagered / self.plays if self.plays else 0.0

    @property
    def average_net(self) -> float:
        """Resultado medio por jugada pagada."""
        return self.net / self.plays if self.plays else 0.0

    @property
    def win_rate(self) -> float | None:
        """Parte de las jugadas pagadas que dejaron dinero."""
        return self.wins / self.plays if self.plays else None

    @property
    def average_win(self) -> float:
        """Ganancia media de las jugadas ganadas."""
        return self.won_amount / self.wins if self.wins else 0.0

    @property
    def average_loss(self) -> float:
        """Pérdida media (positiva) de las jugadas perdidas."""
        return self.lost_amount / self.losses if self.losses else 0.0

    @property
    def gross_gains(self) -> int:
        """Lo ganado en jugadas ganadoras más lo cobrado en jugadas gratis."""
        return self.won_amount + self.free_paid

    @property
    def effective_tax(self) -> float | None:
        """IRPF neto del juego sobre las ganancias brutas (`gross_gains`)."""
        if not self.gross_gains:
            return None
        return (self.withheld - self.refunded) / self.gross_gains


@dataclass(frozen=True, slots=True)
class Highlight:
    """Una jugada destacada para los récords."""

    user_id: int
    game: str
    stake: int
    payout: int
    created_at: float

    @property
    def net(self) -> int:
        """Ganancia o pérdida de la jugada."""
        return self.payout - self.stake

    @property
    def mult(self) -> float:
        """Multiplicador (devuelto / apostado); 0 si fue gratis."""
        return self.payout / self.stake if self.stake else 0.0


@dataclass(frozen=True, slots=True)
class PlayerRow:
    """Lo de un jugador en el ranking."""

    user_id: int
    plays: int
    wagered: int
    paid: int
    best: int
    all_ins: int
    broke: int
    withheld: int
    refunded: int

    @property
    def net(self) -> int:
        """Resultado antes de impuestos."""
        return self.paid - self.wagered

    @property
    def rtp(self) -> float | None:
        """Pagado / apostado."""
        return self.paid / self.wagered if self.wagered else None


@dataclass(frozen=True, slots=True)
class LedgerTotals:
    """Lo que dice el libro de la economía desde el primer día.

    Attributes:
        by_game: Por juego, `(apuestas, apostado, pagado)`. Las apuestas son
            movimientos `:apuesta`, así que un doble del blackjack cuenta dos.
        withheld: IRPF del juego retenido (`irpf:juego`).
        refunded_day: IRPF devuelto en el día por perder después (`devolucion:irpf:juego`).
        refunded_renta: Devuelto en la declaración semanal (`devolucion:renta`).
        first_at: Primer movimiento de casino (epoch), o `None`.
    """

    by_game: dict[str, tuple[int, int, int]] = field(default_factory=dict)
    withheld: int = 0
    refunded_day: int = 0
    refunded_renta: int = 0
    first_at: float | None = None

    @property
    def wagered(self) -> int:
        """Total apostado en todos los juegos."""
        return sum(row[1] for row in self.by_game.values())

    @property
    def paid(self) -> int:
        """Total pagado en todos los juegos."""
        return sum(row[2] for row in self.by_game.values())

    @property
    def bets(self) -> int:
        """Movimientos de apuesta en el libro."""
        return sum(row[0] for row in self.by_game.values())

    @property
    def sanxe_net(self) -> int:
        """Lo que se ha quedado Hacienda del juego después de todas las devoluciones."""
        return self.withheld - self.refunded_day - self.refunded_renta


@dataclass(frozen=True, slots=True)
class Report:
    """Todo lo que enseña `apuestas` para un ámbito (servidor o miembro) y un periodo.

    Attributes:
        by_game: `Totals` de cada juego con alguna jugada.
        players: Jugadores distintos (1 si el ámbito es un miembro con jugadas).
        days: Días (ISO, hora canaria) con alguna jugada, ordenados.
        first_at: Primera jugada apuntada del periodo (epoch), o `None`.
        median_stake: Mediana de la apuesta de las jugadas pagadas.
        buckets: Jugadas pagadas por tramo de `STAKE_BUCKETS`.
        by_hour: `(jugadas, neto)` por hora del día (0–23, hora canaria).
        by_weekday: `(jugadas, neto)` por día de la semana (0 = lunes).
        daily: `(día, jugadas, neto)` de los últimos `DAILY_WINDOW` días.
        top_wins / top_losses / top_mults / top_stakes: Récords del periodo.
        ranking: Jugadores del periodo (vacío si el ámbito es un miembro).
        ledger: El libro desde el primer día (no depende del periodo).
        recorded_since: Primera jugada apuntada en la tabla, de siempre.
    """

    by_game: dict[str, Totals]
    players: int
    days: tuple[str, ...]
    first_at: float | None
    median_stake: float
    buckets: tuple[int, ...]
    by_hour: tuple[tuple[int, int], ...]
    by_weekday: tuple[tuple[int, int], ...]
    daily: tuple[tuple[str, int, int], ...]
    top_wins: tuple[Highlight, ...]
    top_losses: tuple[Highlight, ...]
    top_mults: tuple[Highlight, ...]
    top_stakes: tuple[Highlight, ...]
    ranking: tuple[PlayerRow, ...]
    ledger: LedgerTotals
    recorded_since: float | None

    @property
    def total(self) -> Totals:
        """Todos los juegos juntos."""
        out = Totals()
        for totals in self.by_game.values():
            out.merge(totals)
        return out

    @property
    def favorite(self) -> str | None:
        """Juego con más jugadas (pagadas y gratis)."""
        if not self.by_game:
            return None
        return max(self.by_game, key=lambda g: self.by_game[g].plays + self.by_game[g].free_plays)

    @property
    def empty(self) -> bool:
        """Si no hay ninguna jugada en el periodo."""
        return not self.by_game


def with_ledger(report: Report, ledger: LedgerTotals) -> Report:
    """El mismo informe con el libro rellenado (el libro vive en otro repositorio)."""
    return replace(report, ledger=ledger)


# -- Cálculos -----------------------------------------------------------------------------


def bucket_index(stake: int) -> int:
    """Tramo de `STAKE_BUCKETS` en el que cae una apuesta pagada."""
    for index, (top, _label) in enumerate(STAKE_BUCKETS):
        if top is None or stake <= top:
            return index
    return len(STAKE_BUCKETS) - 1


def median(values: Sequence[int]) -> float:
    """Mediana; 0 si no hay valores."""
    return float(statistics.median(values)) if values else 0.0


def day_streaks(days: Iterable[str], today: date) -> tuple[int, int]:
    """Racha más larga y racha actual de días seguidos jugando.

    La racha actual sigue viva si el último día es hoy o ayer: hoy todavía se
    puede jugar.

    Returns:
        `(más larga, actual)`.
    """
    ordered = sorted({date.fromisoformat(day) for day in days})
    if not ordered:
        return 0, 0
    best = run = 1
    for previous, current in zip(ordered, ordered[1:], strict=False):
        run = run + 1 if current - previous == timedelta(days=1) else 1
        best = max(best, run)
    current_run = run if today - ordered[-1] <= timedelta(days=1) else 0
    return best, current_run


def ledger_from_rows(
    rows: Iterable[tuple[str, int, int]],
    *,
    withheld: int,
    refunded_day: int,
    refunded_renta: int,
    first_at: float | None,
) -> LedgerTotals:
    """Junta las filas del libro `(motivo, movimientos, suma)` por juego.

    Los motivos son `<prefijo>:apuesta` (suma negativa), `<prefijo>:premio` y
    `<prefijo>:bote` (positivas). Los prefijos que no son de un juego se ignoran.
    """
    by_game: dict[str, list[int]] = {}
    for reason, count, amount in rows:
        prefix, _, kind = reason.partition(":")
        game = ledger_game(prefix)
        if game is None:
            continue
        slot = by_game.setdefault(game, [0, 0, 0])
        if kind == "apuesta":
            slot[0] += count
            slot[1] += -amount
        elif kind in ("premio", "bote"):
            slot[2] += amount
    return LedgerTotals(
        by_game={game: (v[0], v[1], v[2]) for game, v in by_game.items()},
        withheld=withheld,
        refunded_day=refunded_day,
        refunded_renta=refunded_renta,
        first_at=first_at,
    )


# -- Formato ------------------------------------------------------------------------------


def pct(value: float | None, digits: int = 1) -> str:
    """`0.9731` → `97,3 %`; `None` → `—`."""
    if value is None:
        return "—"
    return f"{value * 100:.{digits}f}".replace(".", ",") + " %"


def money(amount: float) -> str:
    """Cantidad redondeada al Y$, con su signo si es negativa."""
    value = round(amount)
    return ("−" if value < 0 else "") + format_amount(abs(value))


def signed(amount: float) -> str:
    """Cantidad con signo siempre: `+1.200 Y$`, `−300 Y$`, `0 Y$`."""
    value = round(amount)
    if value > 0:
        return "+" + format_amount(value)
    return money(value)


def number(value: float) -> str:
    """Número entero al estilo español: `12.345`."""
    return f"{round(value):,}".replace(",", ".")


def mult(value: float) -> str:
    """`12.5` → `×12,50`."""
    return "×" + f"{value:.2f}".replace(".", ",")


def bar(value: float, top: float, width: int = 12) -> str:
    """Barra de texto proporcional (`█` llenos y `·` vacíos), sin depender del color."""
    if top <= 0 or value <= 0:
        return "·" * width
    filled = max(1, round(width * value / top))
    return "█" * min(filled, width) + "·" * max(0, width - filled)


def verdict(totals: Totals) -> str:
    """Una frase con guasa según cómo le va a alguien (o al servidor) en el casino."""
    if not totals.plays and not totals.free_plays:
        return "Ni una apuesta. Sanxe no te conoce y eso, mi pana, es un lujo."
    rtp = totals.rtp or 0.0
    if rtp >= 1.5:
        return "La casa te tiene en una lista negra. Sanxe, en una lista de prioridades."
    if rtp >= 1.0:
        return "Vas ganando a la casa. Disfrútalo, que la estadística tiene memoria."
    if rtp >= 0.9:
        return "Lo normal: la casa se lleva lo suyo, despacito, como un IBI."
    if rtp >= 0.7:
        return "Cliente preferente. El casino ya te manda felicitaciones por Navidad."
    return "Has financiado medio casino. Que te pongan una placa en la entrada, papi."


def sanxe_line(withheld: int, refunded: int) -> str:
    """Frase del IRPF del juego: lo retenido y lo devuelto."""
    net = withheld - refunded
    if withheld <= 0:
        return f"🐶 {TAX_COLLECTOR} todavía no ha olido tus premios."
    return (
        f"🐶 {TAX_COLLECTOR} retuvo {format_amount(withheld)}, devolvió "
        f"{format_amount(refunded)} y se queda {money(net)}."
    )
