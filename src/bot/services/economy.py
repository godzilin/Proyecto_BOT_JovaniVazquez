"""Reglas de la economía del bot: la moneda, los yapdollars, y cómo se mueven.

Este es el único punto de entrada al dinero para el resto del bot. El casino,
el IMV (la recompensa diaria) y cualquier sistema futuro (tienda, trabajos, premios
por nivel) deben llamar a `EconomyService` en vez de tocar saldos, para que
todas las reglas compartan la misma moneda, el mismo libro de movimientos y
las mismas garantías de atomicidad (ver `bot.repositories.economy`).

La moneda es ficticia: no se compra ni se canjea por nada con valor real.
"""

from __future__ import annotations

import random
import re
import sqlite3
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import partial
from typing import TypeVar

from bot.repositories.economy import (
    HK_ACCOUNT_ID,
    MARKET_ACCOUNT_ID,
    SHOP_ACCOUNT_ID,
    STATE_ACCOUNT_ID,
    BalanceLimitError,
    BetSettlement,
    BizumReceipt,
    BusinessCost,
    BusinessMove,
    BusinessPaid,
    DailyClaim,
    DonationReceipt,
    EconomyRepository,
    FiscalWeek,
    HousingMove,
    HousingRent,
    InsufficientFundsError,
    InterestNotice,
    InterestRun,
    IrpfPayment,
    IrpfReturn,
    IrpfRun,
    JackpotRecord,
    LedgerEntry,
    LotteryPayout,
    MarketMove,
    MarketPayment,
    MonteMove,
    PokerSettlement,
    PorraBetReceipt,
    PorraCapError,
    PorraClosedError,
    PorraPayment,
    PorraSideError,
    PredictBet,
    PredictBetTakenError,
    PredictStakeCapError,
    SavingsRun,
    SlotsSettlement,
    SportsBet,
    SportsBetTakenError,
    SportsCombo,
    SportsLeg,
    SportsStakeCapError,
    TillEmptyError,
    Treasury,
    WealthCharge,
    WealthRun,
)
from bot.services.bizum import MIN_AMOUNT as BIZUM_MIN_AMOUNT
from bot.services.interest import (
    INTEREST_DAILY_MAX,
    INTEREST_TOP,
    DayOutcome,
    InterestPayment,
    LedgerRow,
    Streaks,
    analyze_day,
    interest_for,
    project_today,
    savings_settlement,
    settle_day,
    withholding,
)
from bot.services.levels import TIMEZONE, local_day
from bot.services.taxes import (
    CLAIM_WEEKS,
    GAMBLING_EXEMPT,
    IGIC_GENERAL_RATE,
    INCOME_BECKHAM,
    INCOME_BUSINESS,
    INCOME_PENSION,
    INCOME_PRIZE,
    INCOME_PROPERTY,
    INCOME_WORK,
    LOTTERY_EXEMPT,
    PAY_DAYS,
    PROJECTION_WINDOW_SECONDS,
    SECOND_INSTALLMENT_DAYS,
    WAGE_YAPDOLLARS_PER_EURO,
    WEALTH_MINIMUM,
    YAPDOLLARS_PER_EURO,
    ForeignPayslip,
    Pace,
    Payslip,
    WeekIncome,
    WeekReturn,
    compute_beckham_payslip,
    compute_hk_payslip,
    compute_payslip,
    compute_pension_payslip,
    compute_self_employed_payslip,
    compute_withholding,
    format_rate,
    gambling_day_tax,
    image_rights_withholding,
    legacy_donation_deduction,
    legacy_weekly_refund,
    property_withholding,
    savings_tax,
    stable_rate,
    wealth_quote,
    week_return,
    week_tax,
)

__all__ = [
    "BalanceLimitError",
    "BetSettlement",
    "BizumReceipt",
    "BusinessCost",
    "BusinessMove",
    "BusinessPaid",
    "CURRENCY_EMOJI",
    "CURRENCY_NAME",
    "CURRENCY_SYMBOL",
    "DailyResult",
    "Declaration",
    "DonationReceipt",
    "WealthCharge",
    "WealthRun",
    "RentaClaim",
    "EconomyService",
    "HousingMove",
    "SalaryResult",
    "imv_after_work",
    "IncomeResult",
    "InsufficientFundsError",
    "InterestCatchUp",
    "InterestNotice",
    "InterestPayment",
    "InterestPreview",
    "InterestRun",
    "IrpfPayment",
    "IrpfReturn",
    "IrpfRun",
    "IrpfStatement",
    "SavingsRun",
    "JackpotRecord",
    "LotteryPayout",
    "MARKET_ACCOUNT_ID",
    "MarketMove",
    "MarketPayment",
    "MonteMove",
    "PorraBetReceipt",
    "PorraCapError",
    "PorraClosedError",
    "PorraPayment",
    "PorraSideError",
    "PokerSettlement",
    "PredictBet",
    "PredictBetTakenError",
    "PredictStakeCapError",
    "SlotsSettlement",
    "SportsBet",
    "SportsCombo",
    "SportsLeg",
    "SportsBetTakenError",
    "SportsStakeCapError",
    "STARTING_BALANCE",
    "SHOP_ACCOUNT_ID",
    "STATE_ACCOUNT_ID",
    "TillEmptyError",
    "Treasury",
    "fiscal_week",
    "format_amount",
    "week_label",
    "week_start",
    "gambling_tax_line",
    "is_all_in",
    "parse_amount",
    "tax_tag",
    "tax_line",
    "treasury_rules",
]

T = TypeVar("T")

CURRENCY_NAME = "yapdollars"
CURRENCY_SYMBOL = "Y$"
CURRENCY_EMOJI = "🪙"

#: Saldo con el que empieza cualquier miembro la primera vez que usa la economía.
STARTING_BALANCE = 1_000

#: IMV (recompensa diaria): base más un extra por cada día seguido, con tope.
#: Exento de IRPF, como el real (art. 7.y LIRPF). Se cobra una vez por día
#: natural en hora canaria (`next_daily_at`): a medianoche vuelve a tocar, y la
#: racha se pierde si se salta un día entero.
DAILY_BASE = 500
DAILY_STREAK_BONUS = 100
DAILY_MAX_BONUS = 1_000

#: Compatibilidad del IMV con el trabajo (`pala`), inspirada en el incentivo al
#: empleo de los arts. 3 y 4 del RD 789/2022 (redacción del RD 240/2026): no
#: cuenta el 100 % del aumento de ingresos del trabajo hasta 6.000 € al año y
#: sí la mitad de lo que pase. En el juego se mira el neto de las nóminas de
#: los últimos 7 días (pensiones y lo que deja el negocio propio incluidos: el IMV real
#: mira todos los ingresos, también los de actividades económicas, art. 18 Ley 19/2021): los
#: primeros `IMV_WORK_EXEMPT` (6.000 € al año, a la escala de juego de las nóminas y
#: pasados a una semana) no cuentan, y de lo que pase se quita del IMV la mitad de su
#: valor en euros, repartida entre los 7 días. El IMV va a la escala de los precios (10
#: Y$ por euro), así que cada Y$ de nómina de más quita 0,05 Y$ de IMV
#: (`IMV_WAGE_RATIO`). Son cifras de juego: la vara del IRPF no las toca.
#: Simplificación: la ley compara con el año anterior y aquí con la última semana.
IMV_WORK_WINDOW_SECONDS = 7 * 86_400
IMV_WORK_EXEMPT = 6_000 * WAGE_YAPDOLLARS_PER_EURO * 7 // 365
IMV_WORK_TAPER = 0.5
#: Y$ de nómina por cada Y$ del IMV (100 frente a 10 Y$ por euro).
IMV_WAGE_RATIO = WAGE_YAPDOLLARS_PER_EURO // YAPDOLLARS_PER_EURO
#: El IMV nunca baja de esta parte de lo que tocaría por la racha (decisión del
#: proyecto: trabajar mucho lo reduce, pero no lo quita).
IMV_FLOOR_SHARE = 0.20

#: Primera semana (lunes) de la Renta de la 8.0.0 en producción: aunque el bot nuevo se
#: despliegue antes (el NAS se actualiza cada día a las 5:00), la semana que ya se ha
#: vivido con las reglas de antes se liquida con ellas. Lo pasa `bot.app` al repositorio.
IRPF_START = date(2026, 10, 19)


def format_amount(amount: int) -> str:
    """Formatea una cantidad al estilo español: `1.250 Y$`."""
    return f"{amount:,}".replace(",", ".") + f" {CURRENCY_SYMBOL}"


def tax_line(gross: int, tax: int, rate: float) -> str:
    """Línea pequeña (subtexto de Discord) con lo que se queda Hacienda.

    La usan los premios por nivel y cualquier otro cobro con retención.
    """
    if tax <= 0:
        return "-# 🐶 Sin retención: con tu renta de la semana no llegas al mínimo."
    return (
        f"-# 🐶 IRPF: −{format_amount(tax)} ({format_rate(rate)}, tu tipo esta semana, "
        f"de {format_amount(gross)})."
    )


def tax_tag(tax: int, rate: float) -> str:
    """La retención en pocas letras (`🐶 −38 Y$ (19,00 %)`), o vacío si no hay.

    La usa el aviso de logros, en la misma línea que el premio.
    """
    if tax <= 0:
        return ""
    return f"🐶 −{format_amount(tax)} ({format_rate(rate)})"


def gambling_tax_line(settlement: BetSettlement) -> str | None:
    """Subtexto con lo que retiene o devuelve Hacienda en una jugada, si algo cambia."""
    if settlement.tax_delta > 0:
        return (
            f"-# 🐶 IRPF: −{format_amount(settlement.tax_delta)} (el 19 %; el lunes, a tu "
            f"tipo). Hoy vas {format_amount(settlement.day_net)} arriba y llevas "
            f"{format_amount(settlement.day_withheld)} retenidos."
        )
    if settlement.tax_delta < 0:
        return (
            f"-# 🐶 Hacienda te devuelve {format_amount(-settlement.tax_delta)}: "
            "tus pérdidas de hoy compensan lo que habías ganado."
        )
    return None


def treasury_rules() -> str:
    """Qué se cobra en el servidor, para la pestaña 📜 Impuestos de `hacienda`."""
    return (
        "IRPF con una sola vara para toda la renta: retención con tu tipo de la semana "
        "en nóminas, premios y alquileres, el 19% de lo ganado cada día en el casino "
        f"(si pasa de {format_amount(GAMBLING_EXEMPT)}) y "
        "declaración cada lunes con la Renta (a pagar, entera o en dos plazos; a "
        "devolver, al presentarla) · Quien debe a Hacienda paga el 30% de lo que cobra · "
        "Patrimonio cada lunes por lo que pase "
        f"de {format_amount(WEALTH_MINIMUM)} entre efectivo y pisos · ITP (6,5%), IBI e "
        "IRPF de los alquileres en `pisos` · IGIC en las compras de la `tienda` "
        f"(general del {IGIC_GENERAL_RATE:.0%}) · Los donativos a ONGs desgravan en la renta"
        " · Los `bizum` entre miembros, exentos de Donaciones"
        f" · Gravamen especial del 20% en los premios de `loteria` que pasen de "
        f"{format_amount(LOTTERY_EXEMPT)}"
        " · Nóminas de la `pala`: IRPF y Seguridad Social del trabajador y de la empresa"
        " (se apunta a quien cobra) · Negocios de la `pala`: IGIC de lo que compran,"
        " Seguridad Social e IRPF de su plantilla, IRPF del dueño y Sociedades"
        " · Multas de la Inspección"
        " · Intereses diarios del monedero: retención del 19% en cada pago (con el tope"
        f" de {format_amount(INTEREST_DAILY_MAX)} brutos al día, nunca se sale de ese"
        " tramo del ahorro)"
    ).replace("%", " %")


_ALL_IN_WORDS = {"all", "allin", "all-in", "todo", "max"}
_HALF_WORDS = {"mitad", "half"}
_SUFFIXES = {"k": 1_000, "m": 1_000_000}
_SUFFIXED = re.compile(r"^(\d+(?:[.,]\d+)?)([km])$")


def is_all_in(text: str) -> bool:
    """Si el texto pide apostar todo el saldo (`all`, `todo`, `max`…)."""
    return text.strip().lower().replace(" ", "") in _ALL_IN_WORDS


def parse_amount(text: str, balance: int) -> int:
    """Convierte lo que escribe el usuario en una cantidad entera.

    Acepta `500`, `1.500` (punto como separador de miles), `2k`, `2.5k`,
    `1m`, `all`/`todo` (todo el saldo) y `mitad`.

    Raises:
        ValueError: Si el texto no es una cantidad válida o no es positiva.
            El mensaje está pensado para mostrarse al usuario.
    """
    value = text.strip().lower().replace(" ", "")
    if value in _ALL_IN_WORDS:
        amount = balance
    elif value in _HALF_WORDS:
        amount = balance // 2
    elif match := _SUFFIXED.match(value):
        number = float(match.group(1).replace(",", "."))
        amount = int(number * _SUFFIXES[match.group(2)])
    elif re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+|\d+", value):
        amount = int(re.sub(r"[.,]", "", value))
    else:
        raise ValueError(f"No entiendo `{text}` como cantidad. Prueba `500`, `2k` o `all`.")
    if amount <= 0:
        raise ValueError("La cantidad tiene que ser mayor que cero.")
    return amount


@dataclass(frozen=True, slots=True)
class IncomeResult:
    """Un ingreso cobrado con su retención de IRPF.

    Attributes:
        gross: Cantidad bruta.
        tax: Retención que se queda Hacienda.
        rate: Tipo de retención aplicado (0–1).
        balance: Saldo tras cobrar.
    """

    gross: int
    tax: int
    rate: float
    balance: int

    @property
    def net(self) -> int:
        """Lo que llega al bolsillo."""
        return self.gross - self.tax


@dataclass(frozen=True, slots=True)
class SalaryResult:
    """Una nómina cobrada.

    Attributes:
        payslip: Desglose (bruto, cotizaciones, IRPF y neto).
        balance: Saldo tras cobrar.
    """

    payslip: Payslip
    balance: int


@dataclass(frozen=True, slots=True)
class ForeignSalaryResult:
    """Una nómina cobrada en Hong Kong."""

    payslip: ForeignPayslip
    balance: int


@dataclass(frozen=True, slots=True)
class Declaration:
    """Declaración semanal pendiente: la semana (lunes) y lo que sale a devolver."""

    week_start: date
    refund: int


@dataclass(frozen=True, slots=True)
class RentaClaim:
    """Resultado de presentar la renta."""

    declarations: tuple[Declaration, ...]
    refunded: int
    balance: int


@dataclass(frozen=True, slots=True)
class IrpfStatement:
    """Una declaración cerrada con lo necesario para explicarla (`renta`).

    Attributes:
        week: Lunes de la semana declarada.
        filed: Lo que salió y lo que se hizo (cobrado, deuda, a devolver…).
        detail: La declaración rehecha con sus entradas, paso a paso (`None` si no se
            guardaron).
        income: La renta de la semana por clases.
        withheld_by: Lo retenido por fuente (`taxes.INCOME_*`, `juego` o `antes`).
        opened: Si su dueño ya la ha abierto.
        arguments: El resto de entradas de `taxes.week_return` (lo retenido, lo donado…),
            para rehacerla cambiando una (el consejo de los donativos).
    """

    week: date
    filed: IrpfReturn
    detail: WeekReturn | None
    income: WeekIncome
    withheld_by: dict[str, int]
    opened: bool
    arguments: dict[str, int] = field(default_factory=dict)


def week_start(day: date) -> date:
    """Lunes de la semana de `day`."""
    return day - timedelta(days=day.weekday())


def week_label(start: date) -> str:
    """`2026-09-28` → `28/09–04/10`."""
    end = start + timedelta(days=6)
    return f"{start:%d/%m}–{end:%d/%m}"


def day_bounds(day: date) -> tuple[float, float]:
    """Medianoche inicial y final de `day` en hora canaria (epoch).

    Se construye cada medianoche por separado en vez de sumar 86.400 s: así el
    día del cambio de hora de octubre (25 horas) y el de marzo (23) duran lo que
    duran de verdad.
    """
    start = datetime.combine(day, datetime.min.time(), TIMEZONE)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), TIMEZONE)
    return start.timestamp(), end.timestamp()


def _settle_member(
    start: float, end: float, user_id: int, opening: int, rows: list[LedgerRow], streaks: Streaks
) -> DayOutcome:
    """Une el análisis del día y el pago (los dos en `bot.services.interest`)."""
    return settle_day(user_id, analyze_day(opening, rows, start, end), streaks)


#: Días atrás que se pagan como mucho si el bot ha estado caído.
INTEREST_CATCH_UP_DAYS = 7


@dataclass(frozen=True, slots=True)
class InterestCatchUp:
    """Lo que ha hecho una pasada de la cuenta remunerada en un servidor.

    Attributes:
        days: Días pagados ahora, en orden: `(día, resultado)`.
        savings: Semana liquidada ahora con la escala del ahorro, si tocaba.
    """

    days: tuple[tuple[date, InterestRun], ...] = ()
    savings: tuple[date, SavingsRun] | None = None


@dataclass(frozen=True, slots=True)
class InterestPreview:
    """Cómo va hoy la cuenta de un miembro, para `saldo`.

    Attributes:
        average: Saldo medio de hoy si el dinero no se mueve hasta medianoche.
        gross: Intereses brutos que daría ese saldo medio.
        tax: Su retención.
        yesterday: Lo cobrado ayer, `(bruto, retención)`, o `None` si nada.
    """

    average: int
    gross: int
    tax: int
    yesterday: tuple[int, int] | None

    @property
    def net(self) -> int:
        """Lo que cobraría mañana, ya sin la retención."""
        return self.gross - self.tax

    @property
    def to_top(self) -> int:
        """Saldo medio que falta para cobrar el máximo diario."""
        return max(0, INTEREST_TOP - self.average)


def _week_end(week_iso: str) -> float:
    start = datetime.combine(date.fromisoformat(week_iso), datetime.min.time(), TIMEZONE)
    return (start + timedelta(days=7)).timestamp()


def monday_epoch(day: date) -> float:
    """Lunes a las 00:00 (hora canaria) de la semana de `day`, en epoch."""
    return datetime.combine(week_start(day), datetime.min.time(), TIMEZONE).timestamp()


def fiscal_week(day: date) -> FiscalWeek:
    """La semana fiscal de `day`: de lunes a lunes, a las 00:00 en hora canaria.

    En el bot el año fiscal es la semana: la renta se reinicia cada lunes, el día de la
    declaración. Cada medianoche se construye por separado, así que una semana con
    cambio de hora dura lo que dura de verdad.
    """
    monday = week_start(day)
    return FiscalWeek(
        monday=monday.isoformat(),
        start=monday_epoch(monday),
        end=monday_epoch(monday + timedelta(days=7)),
        previous_start=monday_epoch(monday - timedelta(days=7)),
    )


def installment_deadlines(week: FiscalWeek) -> tuple[float, float]:
    """`(final del plazo para pagar, cobro del segundo plazo)` de la declaración de `week`.

    La declaración sale el lunes en que acaba la semana: se paga (o se fracciona) hasta
    el final de ese lunes y el 40 % se cobra al final del jueves (`taxes.PAY_DAYS`), en
    hora canaria.
    """
    monday = datetime.fromtimestamp(week.end, TIMEZONE)
    return (
        (monday + timedelta(days=PAY_DAYS)).timestamp(),
        (monday + timedelta(days=SECOND_INSTALLMENT_DAYS)).timestamp(),
    )


def irpf_totals(income: WeekIncome, savings: int) -> tuple[int, int]:
    """`(suma de bases, suma de cuotas)` del IRPF de una semana, general y del ahorro.

    Es lo que mira el límite conjunto del Patrimonio (art. 31 de la Ley 19/1991).
    """
    tax = week_tax(income)
    return tax.base + max(0, savings), tax.quota + savings_tax(savings)


def withhold_business(margin: int, pace: Pace) -> int:
    """Retención a cuenta de un turno de un negocio: el tipo estable sobre su margen."""
    if margin <= 0:
        return 0
    return round(margin * stable_rate(pace, pace.current.plus(business=margin)))


def withhold_rent(rent: HousingRent, pace: Pace) -> int:
    """Retención de un cobro de `pisos`, según su clase de renta.

    La renta de un alquiler retiene sobre su rendimiento neto reducido; el Bono
    Alquiler Joven, como un premio; la comisión del promotor, como su actividad. Todo
    con el tipo estable de la semana (`taxes.stable_rate`).
    """
    if rent.kind == INCOME_PROPERTY:
        return property_withholding(rent.property_week, pace).tax
    if rent.kind == INCOME_BUSINESS:
        return round(rent.gross * stable_rate(pace, pace.current.plus(business=rent.gross)))
    if rent.kind == INCOME_PRIZE:
        return compute_withholding(rent.gross, pace).tax
    return 0


@dataclass(frozen=True, slots=True)
class DailyResult:
    """Resultado de intentar cobrar el IMV (la recompensa diaria).

    Attributes:
        claimed: Si se ha cobrado ahora.
        amount: Cantidad cobrada (0 si no se cobró).
        balance: Saldo tras la operación.
        streak: Racha de días seguidos tras la operación.
        next_claim_at: Momento (epoch) desde el que se puede volver a cobrar.
        full_amount: Lo que tocaba por la racha antes de descontar el trabajo.
        work_net: Neto de las nóminas de los últimos 7 días.
        suspended_until: Si está suspendido por la Inspección, hasta cuándo
            (epoch); 0 si no.
        abroad: País donde vive, si vive fuera (y entonces no cobra).
    """

    claimed: bool
    amount: int
    balance: int
    streak: int
    next_claim_at: float
    full_amount: int = 0
    work_net: int = 0
    suspended_until: float = 0.0
    abroad: str | None = None

    @property
    def reduction(self) -> int:
        """Lo que se ha quitado del IMV por trabajar."""
        return max(0, self.full_amount - self.amount)


def next_daily_at(claimed_at: float) -> float:
    """Medianoche canaria siguiente a `claimed_at`: cuándo vuelve a tocar el IMV."""
    tomorrow = local_day(claimed_at) + timedelta(days=1)
    return datetime.combine(tomorrow, datetime.min.time(), TIMEZONE).timestamp()


def daily_amount(streak: int) -> int:
    """Cantidad del IMV para una racha (1 = primer día)."""
    return DAILY_BASE + min((streak - 1) * DAILY_STREAK_BONUS, DAILY_MAX_BONUS)


def next_streak(previous: DailyClaim | None, today: date) -> int | None:
    """La racha que tendría cobrando el IMV `today`, o `None` si ya lo cobró ese día.

    Sigue si el último cobro fue ayer; si se saltó un día, vuelve a 1.
    """
    if previous is None:
        return 1
    last = local_day(previous.last_claimed_at)
    if last >= today:
        return None
    return previous.streak + 1 if last == today - timedelta(days=1) else 1


def imv_after_work(amount: int, weekly_net: int) -> int:
    """IMV de un día tras descontar lo cobrado trabajando (ver `IMV_WORK_EXEMPT`).

    Args:
        amount: Lo que toca por la racha (`daily_amount`).
        weekly_net: Neto de las nóminas y del negocio de los últimos 7 días (Y$ de nómina).
    """
    excess = max(0, weekly_net - IMV_WORK_EXEMPT)
    reduction = -(-round(round(excess * IMV_WORK_TAPER) / IMV_WAGE_RATIO) // 7)  # hacia arriba
    floor = round(amount * IMV_FLOOR_SHARE)
    return max(floor, amount - reduction)


class EconomyService:
    """Casos de uso de la economía, independientes de Discord.

    Args:
        repository: Persistencia de monederos y movimientos.
        clock: Fuente de tiempo en segundos epoch; inyectable en pruebas.
    """

    def __init__(
        self, repository: EconomyRepository, *, clock: Callable[[], float] = time.time
    ) -> None:
        self.repository = repository
        self._clock = clock

    def now(self) -> float:
        """La hora del servicio en epoch (la del reloj inyectado, en las pruebas)."""
        return self._clock()

    async def contributions(
        self, guild_id: int, user_id: int, start: float, end: float
    ) -> tuple[int, int]:
        """`(por cuenta ajena, RETA)`: lo cotizado entre `start` y `end` (la pluriactividad)."""
        return await self.repository.contributions(guild_id, user_id, start, end)

    async def refund_contributions(self, guild_id: int, user_id: int, *, amount: int) -> int:
        """El reintegro de cuotas de la pluriactividad (art. 313 LGSS): del Estado al miembro.

        Tratamiento fiscal: devolución de cotizaciones; simplificación, sin IRPF (en la vida
        real minora el gasto de la cuota que se dedujo). Devuelve el saldo final.
        """
        if amount <= 0:
            raise ValueError("El reintegro debe ser positivo.")
        return await self.repository.transfer_from_state(
            guild_id, user_id, amount=amount, reason="seguridad_social:reintegro"
        )

    async def account_balance(self, guild_id: int, account: int) -> int:
        """El saldo de una cuenta que no es de un miembro (la caja de una sociedad), sin abrirla."""
        return await self.repository.account_balance(guild_id, account)

    async def balance(self, guild_id: int, user_id: int) -> int:
        """Saldo actual; abre el monedero con `STARTING_BALANCE` si no existía."""
        return await self.repository.balance(guild_id, user_id)

    async def _gamble(
        self,
        guild_id: int,
        user_id: int,
        entries: list[LedgerEntry],
        *,
        adjust_tax: bool,
    ) -> BetSettlement:
        now = self._clock()
        return await self.repository.settle_gamble(
            guild_id,
            user_id,
            entries,
            day=local_day(now).isoformat(),
            now=now,
            adjust_tax=adjust_tax,
            day_tax=gambling_day_tax,
        )

    async def settle_bet(
        self, guild_id: int, user_id: int, *, game: str, stake: int, payout: int
    ) -> BetSettlement:
        """Cobra una apuesta, paga su premio y ajusta el IRPF del día, todo junto.

        Args:
            game: Nombre corto del juego, usado como motivo en el libro.
            stake: Cantidad apostada; debe ser positiva.
            payout: Cantidad total devuelta al jugador (apuesta incluida);
                0 si pierde.

        Raises:
            InsufficientFundsError: Si el saldo no cubre la apuesta. No se
                cobra ni se paga nada.
            BalanceLimitError: Si el premio superaría el saldo máximo.
        """
        if stake <= 0:
            raise ValueError("La apuesta debe ser positiva.")
        if payout < 0:
            raise ValueError("El premio no puede ser negativo.")
        entries = [LedgerEntry(-stake, f"{game}:apuesta")]
        if payout:
            entries.append(LedgerEntry(payout, f"{game}:premio"))
        return await self._gamble(guild_id, user_id, entries, adjust_tax=True)

    async def place_bet(
        self, guild_id: int, user_id: int, *, game: str, stake: int
    ) -> BetSettlement:
        """Cobra una apuesta de un juego que se resuelve más tarde (p. ej. blackjack).

        A diferencia de `settle_bet`, el premio se paga después con
        `pay_winnings`, cuando el juego termina, y es entonces cuando se
        ajusta el IRPF. Así el dinero en juego sale del saldo desde el primer
        momento y no se puede gastar dos veces.

        Raises:
            InsufficientFundsError: Si el saldo no cubre la apuesta.
        """
        if stake <= 0:
            raise ValueError("La apuesta debe ser positiva.")
        return await self._gamble(
            guild_id, user_id, [LedgerEntry(-stake, f"{game}:apuesta")], adjust_tax=False
        )

    async def pay_winnings(
        self, guild_id: int, user_id: int, *, game: str, amount: int
    ) -> BetSettlement:
        """Paga lo devuelto por un juego cobrado con `place_bet` y ajusta el IRPF del día.

        Raises:
            BalanceLimitError: Si el saldo superaría el máximo.
        """
        if amount < 0:
            raise ValueError("El premio no puede ser negativo.")
        entries = [LedgerEntry(amount, f"{game}:premio")] if amount else []
        return await self._gamble(guild_id, user_id, entries, adjust_tax=True)

    async def play_slots(
        self,
        guild_id: int,
        user_id: int,
        *,
        game: str,
        stake: int,
        payout: int,
        share: int,
        jackpot: bool,
        seed: int,
        cap: int = 0,
        draw_hit: Callable[[int, int], int] | None = None,
    ) -> SlotsSettlement:
        """Cobra y paga una tirada de tragaperras y mueve el bote común.

        Tratamiento fiscal: juego, igual que `settle_bet`. Los premios de
        máquinas y casinos son ganancia patrimonial que va a la base general
        (art. 33.1 LIRPF) y las pérdidas solo compensan ganancias de juego
        (art. 33.5.d LIRPF): por eso entra en la retención diaria del casino y
        en la declaración semanal. El jackpot también: el gravamen especial
        del 20 % (disposición adicional 33ª LIRPF) es solo para loterías del
        Estado, ONCE y Cruz Roja, no para tragaperras. El bote misterioso
        (el que cae solo al llegar a la cifra oculta) es el mismo bote pagado
        por la misma máquina: juego, con los mismos asientos y el mismo IRPF
        que el jackpot.

        La parte de la apuesta que va al bote no es un impuesto ni sale del
        bolsillo del jugador aparte: es dinero de la apuesta que la casa no
        se queda. Un giro gratis (`stake=0`) no aporta nada, ni cuenta como
        tirada sin bote.

        Con `cap > 0` el bote «tiene que caer antes de `cap`»: cada vez que se
        siembra se sortea una cifra oculta entre `seed` y `cap`, y la tirada
        pagada que lo hace llegar a ella se lo lleva entero
        (`SlotsSettlement.mystery`). Ver `EconomyRepository.settle_slots`.

        Args:
            game: Motivo corto para el libro (`"tragaperras"`).
            stake: Apuesta; 0 en los giros gratis.
            payout: Lo que devuelve la línea, apuesta incluida.
            share: Parte de la apuesta que va al bote.
            jackpot: Si se lleva el bote entero.
            seed: Lo que pone la casa en un bote nuevo o recién vaciado.
            cap: Tope del bote misterioso; 0 lo desactiva.
            draw_hit: Sorteo de la cifra oculta, `(low, high) -> entero` con
                ambos extremos incluidos. Por defecto, `SystemRandom().randint`;
                las pruebas lo sustituyen para forzar el azar.

        Raises:
            InsufficientFundsError: Si el saldo no cubre la apuesta.
            BalanceLimitError: Si el saldo superaría el máximo.
        """
        now = self._clock()
        return await self.repository.settle_slots(
            guild_id,
            user_id,
            game=game,
            stake=stake,
            payout=payout,
            share=share,
            jackpot=jackpot,
            seed=seed,
            day=local_day(now).isoformat(),
            now=now,
            day_tax=gambling_day_tax,
            cap=cap,
            draw_hit=draw_hit or random.SystemRandom().randint,
        )

    async def porra_bet(
        self,
        guild_id: int,
        porra_id: int,
        user_id: int,
        *,
        outcome: int,
        stake: int,
        cap: int,
        game: str = "porra",
    ) -> PorraBetReceipt:
        """Apuesta a una porra (`bot.services.porras`); el dinero espera en el depósito.

        Tratamiento fiscal: juego, como `place_bet`. Resta en el día del casino del
        jugador y su IRPF se ajusta al liquidar la porra (`settle_porra`).

        Raises:
            PorraClosedError: La porra ya está liquidada.
            PorraSideError: Ya apostó a otra opción de esta porra.
            PorraCapError: No cabe en el bote; `room` dice cuánto cabe.
            InsufficientFundsError: No le llega.
        """
        now = self._clock()
        return await self.repository.porra_bet(
            guild_id,
            porra_id,
            user_id,
            outcome=outcome,
            stake=stake,
            cap=cap,
            game=game,
            day=local_day(now).isoformat(),
            now=now,
            day_tax=gambling_day_tax,
        )

    async def porra_bets(self, guild_id: int, porra_id: int) -> list[tuple[int, int, int]]:
        """Apuestas de una porra: `(miembro, opción, apuesta)`, en orden de llegada."""
        return await self.repository.porra_bets(guild_id, porra_id)

    async def porra_settled(self, guild_id: int, porra_id: int) -> bool:
        """Si la porra ya está liquidada (pagada o devuelta)."""
        return await self.repository.porra_settled(guild_id, porra_id)

    async def settle_porra(
        self,
        guild_id: int,
        porra_id: int,
        *,
        payouts: dict[int, int],
        taxes: dict[int, int],
        refund: bool,
        image: int,
        subject_id: int,
        game: str = "porra",
    ) -> PorraPayment:
        """Liquida una porra: reparte el depósito en una sola transacción.

        Tratamiento fiscal:

        - **Apostantes:** juego. Lo cobrado entra en su día del casino y ajusta
          la retención diaria del IRPF como cualquier `pay_winnings` (ganancia
          patrimonial, art. 33.5.d LIRPF solo deja compensar pérdidas de juego con
          ganancias de juego). Las devoluciones deshacen la apuesta.
        - **IAJ:** el 10 % de cada apuesta va al Estado como Impuesto sobre
          Actividades de Juego (art. 48 de la Ley 13/2011; ver
          `taxes.GAMING_TAX_RATE`). Se apunta a nombre de cada apostante para
          `hacienda`.
        - **Derechos de imagen del protagonista:** rendimiento del capital
          mobiliario (art. 25.4.d LIRPF) con retención fija del 24 % (art. 101
          LIRPF, `taxes.image_rights_withholding`). La retención va al Estado y
          queda en `economy_tax_records` como cualquier otro ingreso.

        El reparto lo calcula `bot.services.porras.split` (o `refund_split`).

        Raises:
            PorraClosedError: Si ya estaba liquidada. No se mueve nada.
            ValueError: Si el reparto no cuadra con lo apostado.
        """
        now = self._clock()
        return await self.repository.settle_porra(
            guild_id,
            porra_id,
            payouts=payouts,
            taxes=taxes,
            refund=refund,
            image=image,
            subject_id=subject_id,
            game=game,
            day=local_day(now).isoformat(),
            now=now,
            day_tax=gambling_day_tax,
            week=fiscal_week(local_day(now)),
            image_withhold=lambda gross, _pace: image_rights_withholding(gross),
        )

    async def sports_bet(
        self, bet: SportsBet, *, game: str, cap: int | None = None
    ) -> tuple[SportsBet, int]:
        """Apuesta a un partido de `deportes`; el dinero espera en el depósito.

        Tratamiento fiscal: juego, como el resto del casino. Es una apuesta de
        contrapartida (art. 3.c de la Ley 13/2011: se apuesta contra el operador a una
        cuota fijada al apostar), y su premio es ganancia patrimonial de la base general
        (art. 33.1 LIRPF) cuyas pérdidas solo compensan ganancias de juego (art. 33.5.d).
        No tiene el gravamen especial de las loterías (disposición adicional 33.ª LIRPF:
        solo loterías del Estado, ONCE y Cruz Roja) ni IAJ aparte: el operador es la casa
        del bot, que no existe, como en los caballos. Al apostar no se toca el día del
        casino: la ganancia o pérdida se imputa cuando se sabe el resultado (art. 14.1.c
        LIRPF), así que entra entera en `settle_sports_bet`.

        Args:
            bet: La apuesta sin guardar, con su mercado (`bet.market`).
            game: Prefijo de los motivos del libro.
            cap: Lo más que se puede apostar al partido, sumando sus mercados
                (`bot.services.sports.MAX_STAKE`; `None`, sin tope).

        Returns:
            La apuesta guardada y el saldo de quien apuesta.

        Raises:
            SportsBetTakenError: Ya tiene una apuesta a ese mercado del partido.
            SportsStakeCapError: Pasaría el tope del partido (`room`, lo que cabe).
            InsufficientFundsError: No le llega.
        """
        return await self.repository.sports_bet(bet, game=game, cap=cap)

    async def settle_sports_bet(
        self, guild_id: int, bet_id: int, *, payout: int, game: str
    ) -> tuple[SportsBet, BetSettlement] | None:
        """Resuelve una apuesta de `deportes` con su premio (0 si ha fallado).

        Tratamiento fiscal: juego (ver `sports_bet`). Lo apostado y lo cobrado entran a
        la vez en el día del casino de hoy y ajustan su retención como `settle_bet`. El
        premio sale de la nada y lo apostado desaparece, como en el resto del casino: lo
        compensa el margen de las cuotas (`bot.services.sports.RTP`).

        Returns:
            La apuesta resuelta y lo movido en el día del casino, o `None` si ya estaba
            resuelta.

        Raises:
            BalanceLimitError: Si el premio pasaría el saldo máximo. No se mueve nada.
        """
        now = self._clock()
        return await self.repository.settle_sports_bet(
            guild_id,
            bet_id,
            payout=payout,
            game=game,
            day=local_day(now).isoformat(),
            now=now,
            day_tax=gambling_day_tax,
        )

    async def void_sports_bet(
        self, guild_id: int, bet_id: int, *, game: str
    ) -> tuple[SportsBet, int] | None:
        """Anula una apuesta de `deportes` (partido aplazado o sin resultado) y la devuelve.

        Tratamiento fiscal: ninguno. Es el dinero propio que vuelve sin que haya habido
        juego, así que no entra en el casino ni se embarga.

        Returns:
            La apuesta anulada y el saldo de su dueño, o `None` si ya estaba resuelta.
        """
        return await self.repository.void_sports_bet(guild_id, bet_id, game=game, now=self._clock())

    async def open_sports_bets(self) -> list[SportsBet]:
        """Las apuestas de `deportes` sin resolver de todos los servidores."""
        return await self.repository.open_sports_bets()

    async def sports_match_bets(
        self, guild_id: int, user_id: int, event_id: str
    ) -> list[SportsBet]:
        """Las apuestas de alguien a un partido, una por mercado."""
        return await self.repository.sports_match_bets(guild_id, user_id, event_id)

    async def sports_bets(self, guild_id: int, user_id: int, *, limit: int) -> list[SportsBet]:
        """Las apuestas de `deportes` de alguien: abiertas primero y luego las últimas."""
        return await self.repository.sports_bets(guild_id, user_id, limit=limit)

    async def predict_bet(
        self, bet: PredictBet, *, game: str, cap: int | None = None
    ) -> tuple[PredictBet, int]:
        """Apuesta a una pregunta de `predice`; el dinero espera en su depósito.

        Tratamiento fiscal: juego, como `deportes` y el resto del casino. Es una apuesta de
        contrapartida (art. 3.c de la Ley 13/2011: se apuesta contra el operador a una
        cuota fijada al apostar, aquí sacada del precio de Polymarket), y su premio es
        ganancia patrimonial de la base general (art. 33.1 LIRPF) cuyas pérdidas solo
        compensan ganancias de juego (art. 33.5.d). Sin gravamen especial de las loterías
        (disposición adicional 33.ª LIRPF) ni IAJ aparte: el operador es la casa del bot,
        que no existe. Al apostar no se toca el día del casino: la ganancia o pérdida se
        imputa cuando se sabe el resultado (art. 14.1.c LIRPF), en `settle_predict_bet`.

        Args:
            bet: La predicción sin guardar.
            game: Prefijo de los motivos del libro.
            cap: Lo más que se puede apostar al evento, sumando sus preguntas
                (`bot.services.predice.MAX_STAKE`; `None`, sin tope).

        Returns:
            La predicción guardada y el saldo de quien apuesta.

        Raises:
            PredictBetTakenError: Ya tiene una predicción a esa pregunta.
            PredictStakeCapError: Pasaría el tope del evento (`room`, lo que cabe).
            InsufficientFundsError: No le llega.
        """
        return await self.repository.predict_bet(bet, game=game, cap=cap)

    async def settle_predict_bet(
        self, guild_id: int, bet_id: int, *, payout: int, game: str
    ) -> tuple[PredictBet, BetSettlement] | None:
        """Resuelve una predicción con su premio (0 si ha fallado).

        Tratamiento fiscal: juego (ver `predict_bet`). Lo apostado y lo cobrado entran a la
        vez en el día del casino de hoy y ajustan su retención como `settle_bet`. El premio
        sale de la nada y lo apostado desaparece, como en el resto del casino: lo compensa
        el margen de las cuotas (`bot.services.predice.RTP`).

        Returns:
            La predicción resuelta y lo movido en el día del casino, o `None` si ya estaba
            resuelta.

        Raises:
            BalanceLimitError: Si el premio pasaría el saldo máximo. No se mueve nada.
        """
        now = self._clock()
        return await self.repository.settle_predict_bet(
            guild_id,
            bet_id,
            payout=payout,
            game=game,
            day=local_day(now).isoformat(),
            now=now,
            day_tax=gambling_day_tax,
        )

    async def void_predict_bet(
        self, guild_id: int, bet_id: int, *, game: str
    ) -> tuple[PredictBet, int] | None:
        """Anula una predicción (resuelta al 50 % o sin resolver a tiempo) y la devuelve.

        Tratamiento fiscal: ninguno. Es el dinero propio que vuelve sin que haya habido
        juego, así que no entra en el casino ni se embarga.

        Returns:
            La predicción anulada y el saldo de su dueño, o `None` si ya estaba resuelta.
        """
        return await self.repository.void_predict_bet(
            guild_id, bet_id, game=game, now=self._clock()
        )

    async def open_predict_bets(self) -> list[PredictBet]:
        """Las predicciones sin resolver de todos los servidores."""
        return await self.repository.open_predict_bets()

    async def predict_event_bets(
        self, guild_id: int, user_id: int, event_id: str
    ) -> list[PredictBet]:
        """Las predicciones de alguien a un evento, una por pregunta."""
        return await self.repository.predict_event_bets(guild_id, user_id, event_id)

    async def predict_bets(self, guild_id: int, user_id: int, *, limit: int) -> list[PredictBet]:
        """Las predicciones de alguien: abiertas primero y luego las últimas resueltas."""
        return await self.repository.predict_bets(guild_id, user_id, limit=limit)

    async def sports_staked(
        self, guild_id: int, user_id: int, event_ids: list[str]
    ) -> dict[str, int]:
        """Lo que alguien lleva apostado a cada partido, sencillas y combinadas abiertas."""
        return await self.repository.sports_staked(guild_id, user_id, event_ids)

    async def sports_combo_bet(
        self, combo: SportsCombo, *, game: str, cap: int | None = None
    ) -> tuple[SportsCombo, int]:
        """Apuesta una combinada de `deportes`; el dinero espera en el depósito.

        Tratamiento fiscal: juego, igual que `sports_bet`. Una combinada es una apuesta de
        contrapartida más (art. 3.c de la Ley 13/2011: se juega contra el operador a una
        cuota fijada al apostar, aquí el producto de las de sus patas), y su premio es
        ganancia patrimonial de la base general (art. 33.1 LIRPF) cuyas pérdidas solo
        compensan ganancias de juego (art. 33.5.d). Sin gravamen especial de loterías ni
        IAJ aparte, como las sencillas. Al apostar no se toca el día del casino: se imputa
        cuando se sabe el resultado (art. 14.1.c LIRPF), en `settle_sports_combo`.

        Args:
            combo: La combinada sin guardar, con sus patas.
            game: Prefijo de los motivos del libro.
            cap: Lo más que se puede apostar a cada partido, sumando sencillas y
                combinadas (`bot.services.sports.MAX_STAKE`; `None`, sin tope).

        Returns:
            La combinada guardada y el saldo de quien apuesta.

        Raises:
            SportsStakeCapError: Pasaría el tope de algún partido (`room`, lo que cabe).
            InsufficientFundsError: No le llega.
        """
        return await self.repository.sports_combo_bet(combo, game=game, cap=cap)

    async def mark_sports_legs(
        self, guild_id: int, combo_id: int, statuses: dict[str, str]
    ) -> SportsCombo | None:
        """Apunta las patas ya resueltas de una combinada abierta. No mueve dinero."""
        return await self.repository.mark_sports_legs(guild_id, combo_id, statuses)

    async def settle_sports_combo(
        self,
        guild_id: int,
        combo_id: int,
        *,
        statuses: dict[str, str],
        payout: int,
        game: str,
    ) -> tuple[SportsCombo, BetSettlement] | None:
        """Resuelve una combinada de `deportes` con su premio (0 si ha fallado).

        Tratamiento fiscal: juego (ver `sports_combo_bet`). Lo apostado y lo cobrado entran
        a la vez en el día del casino de hoy y ajustan su retención como `settle_bet`. El
        premio sale de la nada y lo apostado desaparece: lo compensa el margen, que en una
        combinada se multiplica (`bot.services.sports.combo_odds`).

        Args:
            statuses: Estado de las patas que acaban de resolverse, por partido.

        Returns:
            La combinada resuelta y lo movido en el día del casino, o `None` si ya estaba
            resuelta.

        Raises:
            BalanceLimitError: Si el premio pasaría el saldo máximo. No se mueve nada.
        """
        now = self._clock()
        return await self.repository.settle_sports_combo(
            guild_id,
            combo_id,
            statuses=statuses,
            payout=payout,
            game=game,
            day=local_day(now).isoformat(),
            now=now,
            day_tax=gambling_day_tax,
        )

    async def void_sports_combo(
        self, guild_id: int, combo_id: int, *, statuses: dict[str, str], game: str
    ) -> tuple[SportsCombo, int] | None:
        """Anula una combinada de `deportes` (todas sus patas sin resultado) y la devuelve.

        Tratamiento fiscal: ninguno, como `void_sports_bet`. Es dinero propio que vuelve sin
        que haya habido juego.

        Returns:
            La combinada anulada y el saldo de su dueño, o `None` si ya estaba resuelta.
        """
        return await self.repository.void_sports_combo(
            guild_id, combo_id, statuses=statuses, game=game, now=self._clock()
        )

    async def open_sports_combos(self) -> list[SportsCombo]:
        """Las combinadas de `deportes` sin resolver de todos los servidores."""
        return await self.repository.open_sports_combos()

    async def sports_combos(self, guild_id: int, user_id: int, *, limit: int) -> list[SportsCombo]:
        """Las combinadas de alguien: abiertas primero y luego las últimas."""
        return await self.repository.sports_combos(guild_id, user_id, limit=limit)

    async def slots_pot(self, guild_id: int, *, seed: int) -> int:
        """Bote común de la tragaperras del servidor (lo siembra si es nuevo)."""
        return await self.repository.slots_pot(guild_id, seed=seed)

    async def poker_pot(self, guild_id: int, *, seed: int) -> int:
        """El 💎 Bote progresivo del póker del servidor (lo siembra si es nuevo)."""
        return await self.repository.poker_pot(guild_id, seed=seed)

    async def pay_poker(
        self,
        guild_id: int,
        user_id: int,
        *,
        game: str,
        payout: int,
        share: int = 0,
        pot_permille: int = 0,
        seed: int = 0,
    ) -> PokerSettlement:
        """Paga una mano de póker cobrada con `place_bet` y mueve el 💎 Bote.

        Tratamiento fiscal: juego, como `pay_winnings`. La mano, la parte fija del
        bote y lo que se lleve del bote común son premios de juego, ganancia
        patrimonial de la base general (art. 33.1 LIRPF), y entran en la retención
        diaria del casino y en la declaración semanal. El bote progresivo no es
        lotería: el gravamen especial del 20 % (disposición adicional 33ª LIRPF)
        no se aplica, igual que al jackpot de la tragaperras.

        La parte de la apuesta del bote que pasa al bote común (`share`) no es un
        impuesto: es dinero de la apuesta que la casa no se queda.

        Args:
            game: Motivo corto para el libro (`"poker"`).
            payout: Lo que devuelven la mano y la parte fija del bote, apuestas
                incluidas; 0 si no cobra nada.
            share: Parte de la apuesta del bote que va al bote común.
            pot_permille: Tanto por mil del bote que se lleva (1000, entero).
            seed: Lo que pone la casa en un bote nuevo o recién vaciado.

        Raises:
            BalanceLimitError: Si el saldo superaría el máximo.
        """
        now = self._clock()
        return await self.repository.settle_poker(
            guild_id,
            user_id,
            game=game,
            payout=payout,
            share=share,
            pot_permille=pot_permille,
            seed=seed,
            day=local_day(now).isoformat(),
            now=now,
            day_tax=gambling_day_tax,
        )

    async def settle_timba(
        self,
        guild_id: int,
        *,
        game: str,
        payouts: dict[int, int],
        rakes: dict[int, int],
    ) -> dict[int, BetSettlement]:
        """Paga una mano de la timba (póker entre jugadores) a todos los que la jugaron.

        Tratamiento fiscal: juego. Lo que cada uno se jugaba se cobró con
        `place_bet` al empezar la mano; aquí se le devuelve lo que no gastó más lo
        que gana, que es ganancia patrimonial de la base general (art. 33.1
        LIRPF) y entra en la retención diaria del casino y en la renta semanal.
        La comisión de la casa (el *rake*) va entera al Estado como Impuesto sobre
        Actividades de Juego (inspirado en el art. 48 de la Ley 13/2011, como el
        de las porras): en la vida real es el ingreso del operador, que paga el
        impuesto sobre él; aquí el operador no existe. El dinero pasa de unos
        miembros a otros y al Estado: la mano no crea ni destruye nada.

        Args:
            game: Motivo corto para el libro (`"timba"`).
            payouts: Lo que se devuelve a cada uno (0 si lo perdió todo).
            rakes: La comisión que sale de lo que gana cada ganador.

        Raises:
            BalanceLimitError: Si un saldo superaría el máximo.
        """
        now = self._clock()
        return await self.repository.settle_timba(
            guild_id,
            game=game,
            payouts=payouts,
            rakes=rakes,
            day=local_day(now).isoformat(),
            now=now,
            day_tax=gambling_day_tax,
        )

    async def last_jackpot(self, guild_id: int) -> JackpotRecord | None:
        """Último jackpot de la tragaperras del servidor."""
        return await self.repository.last_jackpot(guild_id)

    async def slots_spins_since(self, guild_id: int) -> int:
        """Tiradas pagadas de la tragaperras desde el último bote (0 si no ha habido)."""
        return await self.repository.slots_spins_since(guild_id)

    async def grant(self, guild_id: int, user_id: int, *, amount: int, reason: str) -> int:
        """Da dinero que no es renta (p. ej. un regalo de cumpleaños): sin IRPF.

        Args:
            amount: Cantidad positiva.
            reason: Motivo corto y estable para el libro, p. ej. `"cumple:regalo"`.

        Returns:
            El saldo final.
        """
        if amount <= 0:
            raise ValueError("La cantidad debe ser positiva.")
        # Exento de IRPF no es inembargable: si debe a Hacienda, paga el 30 %.
        return await self.repository.apply(
            guild_id, user_id, [LedgerEntry(amount, reason)], garnish_at=self._clock()
        )

    async def revoke(
        self, guild_id: int, user_id: int, *, amount: int, reason: str
    ) -> tuple[int, int]:
        """Quita dinero sin dárselo a nadie: lo contrario de `grant`. Si no le llega, todo.

        Returns:
            Lo quitado y el saldo final.
        """
        if amount <= 0:
            raise ValueError("La cantidad debe ser positiva.")
        try:
            balance = await self.repository.apply(guild_id, user_id, [LedgerEntry(-amount, reason)])
        except InsufficientFundsError as error:
            if error.balance <= 0:
                return 0, error.balance
            # shortcut: si el saldo baja entre los dos intentos, el segundo lanza; basta para admin.
            amount = error.balance
            balance = await self.repository.apply(guild_id, user_id, [LedgerEntry(-amount, reason)])
        return amount, balance

    def _week(self, now: float | None = None) -> FiscalWeek:
        """La semana fiscal en curso (o la de `now`)."""
        return fiscal_week(local_day(self._clock() if now is None else now))

    async def pay_income(
        self, guild_id: int, user_id: int, *, gross: int, concept: str
    ) -> IncomeResult:
        """Paga un ingreso sujeto a IRPF (p. ej. el premio por subir de nivel).

        Tratamiento fiscal: ganancia patrimonial no derivada de una transmisión (art.
        33.1 LIRPF), de la base general. Retiene con el tipo estable de la semana
        (`taxes.compute_withholding`) y la declaración del lunes ajusta el resto.

        Args:
            gross: Cantidad bruta; debe ser positiva.
            concept: Motivo corto y estable para el libro, p. ej. `"nivel:12"`.
        """
        if gross <= 0:
            raise ValueError("El ingreso debe ser positivo.")
        now = self._clock()
        tax, balance = await self.repository.credit_income(
            guild_id,
            user_id,
            gross=gross,
            concept=concept,
            now=now,
            withhold=lambda amount, pace: compute_withholding(amount, pace).tax,
            week=self._week(now),
            kind=INCOME_PRIZE,
        )
        return IncomeResult(gross=gross, tax=tax, rate=tax / gross, balance=balance)

    async def record_prize_in_kind(
        self, guild_id: int, user_id: int, *, value: int, concept: str
    ) -> None:
        """Apunta en la Renta de la semana un premio en especie (un objeto, no dinero).

        Tratamiento fiscal: un premio en especie de un sorteo es una ganancia patrimonial
        no derivada de una transmisión (art. 33.1 LIRPF), de la base general, y se valora
        por su valor de mercado (art. 37.1.g LIRPF), que aquí es su precio de catálogo. En
        la vida real quien da el premio hace un ingreso a cuenta del 19 % (art. 105
        RIRPF); aquí quien lo da (la Rueda) no existe, así que no se retiene nada al
        ganarlo: el valor entra en la base de la semana y la declaración del lunes cobra
        lo que toque, como el resto de la renta (Biblia, «una sola renta y una sola
        semana»). No mueve dinero ni se embarga: no hay cobro que embargar.

        Args:
            value: Valor de catálogo en Y$; si no es positivo, no se apunta nada.
            concept: Motivo corto y estable, p. ej. `"rueda:especie"`.
        """
        if value <= 0:
            return
        await self.repository.record_in_kind(
            guild_id, user_id, gross=value, concept=concept, now=self._clock()
        )

    def _renta_rules(self) -> dict[str, object]:
        return {
            "current_week": week_start(local_day(self._clock())).isoformat(),
            "week_end": _week_end,
            "refund_for": legacy_weekly_refund,
            "window_seconds": PROJECTION_WINDOW_SECONDS,
            "claim_weeks": CLAIM_WEEKS,
            "deduction_for": legacy_donation_deduction,
        }

    async def pending_declarations(self, guild_id: int, user_id: int) -> list[Declaration]:
        """Declaraciones de semanas cerradas que salen a devolver y no se han presentado."""
        rows = await self.repository.pending_declarations(guild_id, user_id, **self._renta_rules())
        return [Declaration(date.fromisoformat(week), refund) for week, refund in rows]

    async def claim_declarations(self, guild_id: int, user_id: int) -> RentaClaim:
        """Presenta las declaraciones pendientes y cobra la devolución."""
        rows, total, balance = await self.repository.claim_declarations(
            guild_id, user_id, now=self._clock(), **self._renta_rules()
        )
        return RentaClaim(
            declarations=tuple(Declaration(date.fromisoformat(w), r) for w, r in rows),
            refunded=total,
            balance=balance,
        )

    async def close_irpf_weeks(self, guild_id: int) -> list[tuple[date, IrpfRun]]:
        """Cierra el IRPF de las semanas acabadas que falten: la declaración de los lunes.

        Tratamiento fiscal: autoliquidación del IRPF (arts. 96 y 97 LIRPF) de cada
        semana, que es el año fiscal del bot. La cuota sale de `taxes.week_return` con
        toda la renta de la semana; si sale a pagar queda pendiente hasta el final del
        lunes, para pagarla entera o fraccionada (`pay_irpf_return`), y si no se elige se
        cobra entera (`settle_irpf_payments`); si sale a devolver,
        primero paga deudas (compensación, art. 73 de la Ley 58/2003) y el resto se
        presenta con la Renta (`claim_declarations`, desde la cuenta del Estado). Detalle
        en `EconomyRepository.close_irpf_week`.

        Es idempotente: una semana ya cerrada no se toca. Si el bot ha estado caído,
        cierra todas las que falten desde la primera de la 8.0.0 del servidor, en orden
        (los arrastres de una pasan a la siguiente).

        Returns:
            `(lunes, resultado)` de cada semana cerrada ahora, de la más vieja a la última.
        """
        now = self._clock()
        current = self._week(now)
        since, last = await self.repository.irpf_weeks_closed(guild_id)
        if since is None:
            since = await self.repository.irpf_regime(guild_id, current.monday)
        first = date.fromisoformat(since)
        if last is not None:
            first = max(first, date.fromisoformat(last) + timedelta(days=7))
        closed: list[tuple[date, IrpfRun]] = []
        monday = first
        while monday.isoformat() < current.monday:
            run = await self.repository.close_irpf_week(
                guild_id,
                week=fiscal_week(monday),
                current_week=current.monday,
                now=now,
                returns=week_return,
                savings_tax=savings_tax,
                deadlines=installment_deadlines(fiscal_week(monday)),
            )
            if not (run.done_before or run.legacy):
                closed.append((monday, run))
            monday += timedelta(days=7)
        return closed

    async def take_irpf_notices(self, guild_id: int, user_id: int) -> list[date]:
        """Semanas con declaración cerrada de las que aún no se avisó; quedan avisadas."""
        weeks = await self.repository.take_irpf_notices(guild_id, user_id)
        return [date.fromisoformat(week) for week in weeks]

    async def irpf_statement(
        self, guild_id: int, user_id: int, week: date | None = None
    ) -> IrpfStatement | None:
        """Una declaración cerrada con todo para explicarla (la última, sin `week`).

        Se rehace con las mismas entradas con las que se cerró (`taxes.week_return` es
        determinista), así el detalle cuadra al Y$ con lo que se cobró o se devolvió.
        """
        found = await self.repository.irpf_statement(
            guild_id, user_id, week.isoformat() if week else None
        )
        if found is None:
            return None
        monday, filed, inputs, opened = found
        income = WeekIncome.from_dict(inputs.get("income", {}))  # type: ignore[arg-type]
        arguments = dict(inputs.get("arguments", {}))  # type: ignore[call-overload]
        withheld_by = {
            str(k): int(v)
            for k, v in dict(inputs.get("withheld_by", {})).items()  # type: ignore[call-overload]
        }
        return IrpfStatement(
            week=date.fromisoformat(monday),
            filed=filed,
            detail=week_return(income, **arguments) if inputs else None,
            income=income,
            withheld_by=withheld_by,
            opened=opened,
            arguments={str(k): int(v) for k, v in arguments.items()},
        )

    async def open_irpf_return(self, guild_id: int, user_id: int, week: date) -> None:
        """Marca como abierta una declaración: su dueño ya ha visto el resultado."""
        await self.repository.open_irpf_return(guild_id, user_id, week.isoformat())

    async def pay_irpf_return(
        self, guild_id: int, user_id: int, week: date, *, split: bool
    ) -> IrpfPayment | None:
        """Paga la declaración a pagar de `week`: entera o el 60 % (el 40 %, el jueves).

        Tratamiento fiscal: ingreso de la autoliquidación (art. 97 LIRPF) y, fraccionada,
        el pago en dos plazos sin intereses ni recargo del art. 62.2 del Reglamento del
        IRPF. Va al Estado; lo que no llegue queda como deuda con Hacienda. Detalle en
        `EconomyRepository.pay_irpf_return`.

        Returns:
            Lo pagado, o `None` si ya no estaba pendiente.
        """
        return await self.repository.pay_irpf_return(
            guild_id, user_id, week.isoformat(), split=split
        )

    async def settle_irpf_payments(self, guild_id: int) -> tuple[IrpfPayment, ...]:
        """Cobra los plazos vencidos: las que nadie pagó, enteras, y los 40 % del jueves."""
        return await self.repository.settle_irpf_payments(guild_id, self._clock())

    async def pay_tax_debt(self, guild_id: int, user_id: int) -> tuple[int, int, int]:
        """Liquida la deuda con Hacienda del monedero: `(pagado, lo que queda, saldo)`.

        Tratamiento fiscal: pago voluntario de una deuda tributaria (art. 62 de la Ley
        58/2003), al Estado y sin recargo.
        """
        return await self.repository.pay_tax_debt(guild_id, user_id, self._clock())

    async def irpf_notices(
        self, guild_id: int, user_id: int, since: float, until: float
    ) -> tuple[int, int, int, int]:
        """Lo cobrado por Hacienda sola entre `since` y `until` (`renta.hint`).

        Returns:
            `(embargado, segundo plazo pagado, segundo plazo que quedó a deber, deuda que
            queda)`.
        """
        return await self.repository.irpf_notices(guild_id, user_id, since, until)

    async def irpf_rate(self, guild_id: int, user_id: int) -> float:
        """El tipo con el que retiene ahora mismo un cobro (el estable de la semana)."""
        income, pace = await self.repository.week_income(guild_id, user_id, self._week())
        return stable_rate(pace, income)

    async def charge_wealth_tax(
        self,
        guild_id: int,
        *,
        assets: Callable[[sqlite3.Connection], dict[int, int]] | None = None,
        goods: Callable[[sqlite3.Connection], dict[int, int]] | None = None,
    ) -> tuple[date, WealthRun]:
        """Cobra el Impuesto sobre el Patrimonio de la semana que acaba de cerrar.

        Tratamiento fiscal: Impuesto sobre el Patrimonio (Ley 19/1991), escala
        y mínimo en `bot.services.taxes.wealth_tax`, sobre el efectivo, los
        inmuebles a su valor fiscal (`assets`, art. 10 de la Ley 19/1991) y los
        objetos de la tienda (`goods`, arts. 18 y 19), con el límite conjunto con
        el IRPF de esa misma semana: sus bases y sus cuotas, general y del ahorro
        (art. 31, `taxes.wealth_quote`). Quien pasa de 37.000.000 Y$ declara además el
        Impuesto de Solidaridad de las Grandes Fortunas (art. 3 de la Ley 38/2022), que
        descuenta el Patrimonio pagado y en Canarias sale a cero. Lo recaudado va al
        Estado. Es idempotente: si la semana ya se cobró, no hace nada.

        Args:
            assets: Valor fiscal de lo que tiene cada miembro además del efectivo,
                leído en la misma transacción (`bot.repositories.housing.assets_in`).
            goods: Valor de sus objetos de la tienda, leído en la misma transacción
                (`bot.repositories.shop.wealth_goods_in`).

        Returns:
            `(lunes de la semana cobrada, resultado)`.
        """
        now = self._clock()
        week = week_start(local_day(now)) - timedelta(days=7)
        run = await self.repository.charge_wealth_tax(
            guild_id,
            week=week.isoformat(),
            now=now,
            tax_for=lambda worth, unproductive, base, quota: wealth_quote(
                worth, unproductive=unproductive, irpf_base=base, irpf_quota=quota
            ),
            assets=assets,
            goods=goods,
            fiscal=fiscal_week(week),
            irpf_of=irpf_totals,
        )
        return week, run

    async def week_irpf(self, guild_id: int, user_id: int) -> tuple[int, int]:
        """`(bases, cuotas)` del IRPF de esta semana hasta ahora, para el límite conjunto."""
        week = self._week()
        income, _pace = await self.repository.week_income(guild_id, user_id, week)
        savings = await self.repository.week_savings(guild_id, user_id, week)
        return irpf_totals(income, savings)

    async def solidarity_summary(self, guild_id: int) -> tuple[int, int, int]:
        """Grandes Fortunas del servidor: `(declaraciones, contribuyentes, lo pagado)`."""
        return await self.repository.solidarity_summary(guild_id)

    async def pay_interest(self, guild_id: int) -> InterestCatchUp:
        """Paga los intereses de los días cerrados que falten y liquida la semana pasada.

        Tratamiento fiscal: rendimiento del capital mobiliario (art. 25.2 LIRPF).
        Cada pago lleva la retención fija del 19 % (art. 101.4 LIRPF) y cada
        lunes la semana anterior se liquida con la escala del ahorro (arts. 66.1
        y 76 LIRPF, `bot.services.taxes.savings_tax`): lo que falte se cobra
        entonces. Todo lo retenido y liquidado va al Estado. El bruto se crea de
        la nada: el banco no existe (ver `bot.services.interest`).

        Es idempotente: un día o una semana ya procesados no se vuelven a tocar.
        Si el bot ha estado caído, paga hasta `INTEREST_CATCH_UP_DAYS` días
        atrás, con el saldo medio exacto de cada uno (sale del libro).
        """
        now = self._clock()
        yesterday = local_day(now) - timedelta(days=1)
        last = await self.repository.last_interest_day(guild_id)
        first = yesterday if last is None else date.fromisoformat(last) + timedelta(days=1)
        first = max(first, yesterday - timedelta(days=INTEREST_CATCH_UP_DAYS - 1))
        paid: list[tuple[date, InterestRun]] = []
        day = first
        while day <= yesterday:
            start, end = day_bounds(day)
            run = await self.repository.pay_interest_day(
                guild_id,
                day=day.isoformat(),
                start=start,
                end=end,
                now=now,
                settle=partial(_settle_member, start, end),
            )
            if not run.done_before:
                paid.append((day, run))
            day += timedelta(days=1)
        savings = None
        week = week_start(local_day(now)) - timedelta(days=7)
        sunday = week + timedelta(days=6)
        last = await self.repository.last_interest_day(guild_id)
        if last is not None and date.fromisoformat(last) >= sunday:
            run = await self.repository.settle_savings(
                guild_id,
                week=week.isoformat(),
                first_day=week.isoformat(),
                last_day=sunday.isoformat(),
                now=now,
                settle=savings_settlement,
            )
            if not run.done_before:
                savings = (week, run)
        return InterestCatchUp(days=tuple(paid), savings=savings)

    async def interest_preview(self, guild_id: int, user_id: int) -> InterestPreview:
        """Saldo medio de hoy, lo que daría mañana y el último cobro de un miembro."""
        now = self._clock()
        today = local_day(now)
        start, end = day_bounds(today)
        opening, rows = await self.repository.interest_day_rows(guild_id, user_id, start)
        average = project_today(opening, rows, start, end, now)
        gross = interest_for(average)
        last = await self.repository.last_interest(guild_id, user_id)
        yesterday = (today - timedelta(days=1)).isoformat()
        return InterestPreview(
            average=average,
            gross=gross,
            tax=withholding(gross),
            yesterday=(last[1], last[2]) if last and last[0] == yesterday else None,
        )

    async def take_interest_notice(self, guild_id: int, user_id: int) -> InterestNotice:
        """Intereses y liquidaciones que el miembro no ha visto; quedan como vistos."""
        return await self.repository.take_interest_notice(guild_id, user_id)

    async def last_savings(
        self, guild_id: int, user_id: int
    ) -> tuple[str, int, int, int, int, int] | None:
        """Última liquidación del ahorro (ver `EconomyRepository.last_savings`)."""
        return await self.repository.last_savings(guild_id, user_id)

    async def donate(
        self, guild_id: int, user_id: int, *, ong_key: str, ong_account: int, amount: int
    ) -> DonationReceipt:
        """Dona `amount` a una ONG. Es un gasto: el dinero se queda en la ONG.

        Tratamiento fiscal: sin IGIC ni Donaciones (ver `bot.services.donations`);
        desgrava en el IRPF (art. 19.1 de la Ley 49/2002) y la deducción sale a
        devolver en la renta de la semana (`donation_deduction`), desde la
        cuenta del Estado.

        Raises:
            InsufficientFundsError: Si no le llega el saldo.
        """
        if amount <= 0:
            raise ValueError("El donativo debe ser positivo.")
        now = self._clock()
        return await self.repository.donate(
            guild_id,
            user_id,
            ong=ong_key,
            ong_account=ong_account,
            amount=amount,
            week=week_start(local_day(now)).isoformat(),
            now=now,
        )

    async def bizum(
        self, guild_id: int, sender_id: int, receiver_id: int, *, amount: int
    ) -> BizumReceipt:
        """Manda un Bizum: `amount` pasa entero de un miembro a otro.

        Tratamiento fiscal: exento del Impuesto sobre Sucesiones y Donaciones
        por decisión del proyecto, y sin IRPF para quien recibe (art. 6.4
        LIRPF: lo sujeto a Donaciones no tributa por IRPF). En la vida real
        sería una donación sujeta (art. 3.1.b de la Ley 29/1987) que Canarias
        solo bonifica al 99,9 % entre familia de los grupos I y II (art. 26
        sexies del Decreto Legislativo 1/2009); el bot trata a todo el servidor
        como grupo II y redondea el 0,1 % a cero. Detalle en
        `bot.services.bizum`. Como no hay impuesto, el Estado no recibe nada.

        Args:
            amount: Cantidad; al menos `bot.services.bizum.MIN_AMOUNT`. Los
                máximos de Bizum no se aplican aquí: se informan en el
                resultado (`sent_today`) para los logros.

        Raises:
            ValueError: Si la cantidad no llega al mínimo o el destino no es
                otro miembro. El mensaje se puede enseñar al usuario.
            InsufficientFundsError: Si no le llega. No se mueve nada.
            BalanceLimitError: Si quien recibe superaría el saldo máximo.
        """
        if amount < BIZUM_MIN_AMOUNT:
            raise ValueError(f"Bizum no deja mandar menos de {format_amount(BIZUM_MIN_AMOUNT)}.")
        if sender_id == receiver_id:
            raise ValueError("Un Bizum a ti mismo no es un Bizum, es mirar el saldo.")
        now = self._clock()
        day_start = datetime.combine(local_day(now), datetime.min.time(), TIMEZONE).timestamp()
        return await self.repository.bizum(
            guild_id, sender_id, receiver_id, amount=amount, now=now, day_start=day_start
        )

    async def market(
        self,
        guild_id: int,
        prepare: Callable[[sqlite3.Connection], tuple[T, MarketMove]],
    ) -> tuple[T, dict[int, int]]:
        """Mueve el dinero de una operación del Rastro (`bot.services.rastro`).

        Tratamiento fiscal:

        - **Quien compra** paga el Impuesto sobre Transmisiones Patrimoniales
          Onerosas (art. 7.1.A del RDL 1/1993; sujeto pasivo, art. 8.a), no IGIC:
          quien vende es un particular, no un empresario (art. 4 de la Ley
          20/1991). Tipo de los bienes muebles en Canarias, 5,5 % (art. 31.1.d
          del Decreto Legislativo 1/2009), sobre el mayor entre el precio y el
          valor de referencia (art. 10.1 RDL 1/1993 y art. 57.1.b de la Ley
          58/2003; `taxes.itp`).
        - **La parte regalada** (precio por debajo del valor) está exenta de
          Donaciones por decisión del proyecto, como el Bizum.
        - **Quien vende** tributa por la ganancia patrimonial (art. 33.1 LIRPF)
          en la base del ahorro, al 19 % y en el momento (`taxes.capital_gain_tax`;
          la declaración de los lunes es de la base general). Se le descuenta de lo
          que cobra.

        Lo pujado espera en el depósito del Rastro (`MARKET_ACCOUNT_ID`) y los
        dos impuestos van al Estado en la misma transacción. Ver
        `EconomyRepository.market` para el orden de los movimientos.

        Raises:
            InsufficientFundsError: Si a quien puja o compra no le llega.
            BalanceLimitError: Si a alguien no le cabe lo que cobra.
        """
        return await self.repository.market(guild_id, now=self._clock(), prepare=prepare)

    async def monte(
        self,
        guild_id: int,
        prepare: Callable[[sqlite3.Connection], tuple[T, MonteMove]],
    ) -> tuple[T, int]:
        """Mueve el dinero de una operación del Monte de Piedad (`bot.services.monte`).

        El Monte compra y presta con la caja de la tienda (`SHOP_ACCOUNT_ID`): lo que se
        gastó en la tienda vuelve a circular y lo que se le devuelve vuelve a la caja. No
        se crea ni se destruye nada.

        Tratamiento fiscal:

        - **Vender al Monte:** quien vende es un particular, así que no repercute IGIC
          (art. 4 de la Ley 20/1991). El ITP de la compra lo pagaría el Monte (art. 8.a del
          RDL 1/1993), que no es un miembro: no se cobra. Para quien vende es una
          transmisión (art. 33.1 LIRPF), pero siempre por menos de lo que le costó (el
          Monte paga una parte de lo pagado en la tienda), así que no hay ganancia (art.
          34.1.a LIRPF); la pérdida no se computa, por decisión del proyecto, como en el
          Rastro. Es un cobro: paga el embargo de la deuda con Hacienda (`garnish`).
        - **Empeñar:** un préstamo con prenda (arts. 1857 y 1863 del Código Civil). Lo
          prestado no es renta, porque se devuelve. Exento de ITP y AJD (art. 45.I.B.15 del
          RDL 1/1993; la prenda tributa como el préstamo, art. 15.1). No se embarga, igual
          que el Bizum de mamá: es dinero que se devuelve.
        - **Desempeñar:** el interés es el precio del préstamo, exento de IGIC como
          operación financiera (art. 18 del Decreto Legislativo 1/2025 de Canarias). No se
          deduce en el IRPF: un préstamo para consumo no da derecho a nada.

        Raises:
            TillEmptyError: Si la caja de la tienda no llega para pagar.
            InsufficientFundsError: Si al miembro no le llega para desempeñar.
            BalanceLimitError: Si al miembro no le cabe lo que cobra.
        """
        return await self.repository.monte(guild_id, now=self._clock(), prepare=prepare)

    async def housing(
        self,
        guild_id: int,
        prepare: Callable[[sqlite3.Connection], tuple[T, HousingMove]],
    ) -> tuple[T, dict[int, int]]:
        """Mueve el dinero de una operación de `pisos` (`bot.services.housing`).

        Tratamiento fiscal:

        - **Comprar** un inmueble de segunda mano paga el ITP de los inmuebles de
          Canarias, 6,5 % (art. 31 del Decreto Legislativo 1/2009), a cargo de
          quien compra (art. 8.a del RDL 1/1993), sin IGIC: quien vende es un
          particular (art. 4 de la Ley 20/1991). Notaría, registro y gestoría no
          son impuestos: desaparecen.
        - **La renta** es rendimiento del capital inmobiliario (art. 22 LIRPF) de
          la base general, inmueble por inmueble: ingresos menos gastos (IBI,
          comunidad, seguro, amortización) y menos intereses y reparaciones, estos
          con tope en lo que renta el inmueble y lo que sobra pasa a las cuatro
          semanas siguientes (art. 23.1.a). Sobre el neto positivo de una vivienda,
          la reducción del art. 23.2 (Ley 12/2023: 50 %, o 90 % en zona tensionada si
          el contrato nuevo baja la renta más de un 5 %); un neto negativo rebaja el
          resto de la base (art. 48): eso es desgravar la hipoteca. Entre particulares
          no hay retención (art. 101.8 LIRPF solo obliga a las empresas que
          alquilan), pero el bot retiene a cuenta con el tipo estable de la semana,
          como cualquier otra renta general, y la declaración del lunes ajusta.
        - **Los pisos vacíos** que no son la vivienda habitual imputan el 1,1 % de su
          valor catastral al año (art. 85 LIRPF): no se cobra nada, suma a la base.
        - **Deducciones canarias** (Decreto Legislativo 1/2009): lo pagado por la
          hipoteca de la vivienda habitual (art. 14), por el alquiler de la vivienda
          habitual (art. 15) y por las reparaciones y el seguro de una vivienda
          alquilada (art. 15 ter) se apunta para la declaración del lunes.
        - **El IBI** (art. 60 del RDL 2/2004) va al Estado: en el juego no hay
          ayuntamientos. Comunidad y seguro desaparecen.
        - **Vender** paga la comisión de la agencia (desaparece) y, si se gana, el
          19 % de la ganancia patrimonial (art. 33.1 LIRPF, base del ahorro,
          `taxes.capital_gain_tax`), como el Rastro. Simplificación: no hay
          plusvalía municipal. Venderle al fondo buitre es una venta más.
        - **La hipoteca** no es renta: el banco no existe, así que lo prestado se
          crea de la nada y las cuotas desaparecen. El AJD lo paga el banco (RDL
          17/2018) y amortizar no tiene comisión (decisión del proyecto; la Ley
          5/2019 la limita). Los intereses del mes se restan de la renta de ese
          inmueble (art. 23.1.a LIRPF). En la ejecución, la subasta al 70 % (art. 671
          LEC) cancela lo que llegue y el resto queda como deuda con el banco.
        - **Los préstamos personales** (`banco`, reglas en `bot.services.loans`): el mismo
          banco que no existe, así que lo prestado se crea de la nada y las cuotas y la
          comisión de apertura desaparecen. No es renta (se devuelve). Exento de ITP y AJD
          (art. 45.I.B.15 del RDL 1/1993); intereses y comisiones, exentos de IGIC como
          operaciones financieras (art. 18 del Decreto Legislativo 1/2025 de Canarias), y sin
          deducción en el IRPF (son para consumo). La prima del seguro de protección de
          pagos desaparece (la aseguradora no existe) y lleva el IPS del 8 % (art. 12 de la
          Ley 13/1996), al Estado; lo que paga el seguro no mueve dinero (paga al banco). Al
          desistir (art. 28 de la Ley 16/2011) la comisión vuelve, y amortizar antes paga la
          compensación del art. 30. La deuda de un préstamo vencido se suma a la deuda con
          el banco y se embarga de las nóminas (`EconomyRepository.credit_salary`).
        - **El anticipo de rentas** (`banco`, 💸 Anticipos, `HousingRepository.advance`):
          un préstamo más, con su inmueble; lo adelantado se crea y la comisión
          desaparece. No es renta: la renta del inmueble tributa cada noche al
          devengarse (art. 14.1.a LIRPF), no al adelantarla, y el coste del anticipo no
          deduce (art. 23.1.a.1.º LIRPF). Interés y comisión, exentos de IGIC (art. 18
          del Decreto Legislativo 1/2025 de Canarias).
        - **La VPO** se compra a la promoción pública con IGIC al tipo cero
          (primera entrega de vivienda protegida, art. 52 de la Ley 4/2012) y solo
          paga notaría; el precio va a la cuenta del Estado, que la recompra al
          mismo precio si se devuelve (sin ganancia, no hay IRPF).
        - **El alquiler entre miembros**: lo que paga el inquilino es la renta del
          casero, con el mismo IRPF de arriba. El inquilino no tiene deducción
          estatal desde 2015, pero sí la canaria del art. 15.
        - **El Bono Alquiler Joven** (RD 42/2022) lo paga la cuenta del Estado y es
          una subvención: ganancia patrimonial de la base general (art. 33.1 LIRPF),
          con la misma retención a cuenta. Si el Estado no llega, no se paga.
        - **Vacacional:** la estancia lleva dentro el IGIC general del 7 % (art. 51
          de la Ley 4/2012), que el dueño ingresa en el Estado; la licencia es una
          tasa al Estado. El recargo del IBI de la vivienda vacía (art. 72.4 del
          RDL 2/2004) va al Estado como el IBI.
        - **La SOCIMI** tributa al 0 % en Sociedades (art. 9 de la Ley 11/2009) y su
          dueño paga el 19 % del dividendo en la base del ahorro (art. 66 LIRPF) en
          lugar del IRPF del alquiler; la salida a bolsa desaparece.

        Los inquilinos y los compradores no existen: la renta y lo cobrado al
        vender se crean de la nada, y lo pagado al comprar desaparece. Ver
        `EconomyRepository.housing` para el orden de los movimientos.

        Raises:
            InsufficientFundsError: Si a quien compra no le llega.
            BalanceLimitError: Si a alguien no le cabe lo que cobra.
        """
        now = self._clock()
        return await self.repository.housing(
            guild_id, now=now, week=self._week(now), withhold=withhold_rent, prepare=prepare
        )

    async def business(
        self,
        guild_id: int,
        prepare: Callable[[sqlite3.Connection], tuple[T, BusinessMove]],
    ) -> tuple[T, BusinessPaid]:
        """Mueve el dinero de una operación de un negocio de `pala` (`bot.services.negocio`).

        El negocio es de un autónomo (fase 1): no tiene caja propia y todo sale y entra del
        monedero de su dueño. Tratamiento fiscal:

        - **Las ventas** de cada turno son rendimiento de actividades económicas (art. 27
          LIRPF), imputadas con el criterio de cobros y pagos, por el que opta el autónomo
          del juego (art. 7.2 RIRPF: ingreso al cobrar, gasto al pagar). La demanda no
          existe: se crean de la nada. Retienen a cuenta con el tipo estable de la semana
          sobre el margen del turno (ventas menos el coste de lo vendido) en lugar del 20 %
          del pago fraccionado (art. 110.1.a RIRPF): una sola vara para toda la renta, como
          pide la Biblia. Les alcanza el embargo del 30 %.
        - **Las compras al mayorista** pagan IGIC (tipo cero el pan y la leche; el resto, el
          general del 7 %, art. 32.1 del Decreto Legislativo 1/2025 de Canarias), que va al
          Estado; la base desaparece. El autónomo está en el régimen especial del pequeño
          empresario (arts. 89-91 del mismo decreto): sus ventas están exentas (art. 90.1),
          así que no repercute IGIC ni deduce el soportado, y base e IGIC son gasto de la
          actividad. Simplificación: no se mira su tope de facturación (30.000 € al año;
          50.000 € desde 2027).
        - **Montar el negocio:** la tasa de la apertura es la municipal de la comunicación
          previa (art. 20.4.i TRLRHL; Ley 7/2011 de Canarias y, en la tienda, Ley 12/2012) y
          va al Estado porque no hay ayuntamientos; la fianza del local, dos mensualidades
          (art. 36.1 de la Ley 29/1994), va al depósito de fianzas y vuelve al cerrar; el
          acondicionamiento lleva IGIC y es gasto entero de esa semana. Simplificación del
          proyecto: la libertad de amortización (art. 12.3.e LIS, por el art. 28.1 LIRPF)
          solo vale para elementos nuevos de hasta 300 € y 25.000 € al año; unas obras se
          amortizarían.
        - **Cada noche,** el alquiler del local lleva IGIC y la retención del 19 % del IRPF
          del casero (arts. 100 RIRPF y 101.8 LIRPF), que el inquilino ingresa en el
          Estado; el resto se lo lleva el casero, que no existe. Los suministros llevan IGIC.
          Se cobra lo que llegue y el resto queda como deuda del local. Todo es gasto de la
          actividad. La terraza paga cada noche su tasa municipal por ocupar la vía
          pública (art. 20.3.l TRLRHL), que va al Estado.
        - **Fase 2.** Las mejoras del local y el acondicionamiento no son gasto el día que
          se pagan: se amortizan cada noche con la tabla simplificada (Orden de 27 de marzo
          de 1998, art. 30 RIRPF), sobre lo pagado con el IGIC, que el pequeño empresario no
          deduce (norma 2.ª del PGC). Interpretación: con el criterio de cobros y pagos se
          sigue amortizando por la dotación, porque la amortización no es un pago. Las
          campañas de publicidad son gasto entero (art. 15.e LIS, por el art. 28.1 LIRPF).
        - **Traspasar** (cesión del local con el negocio, art. 32 LAU): el stock se cobra
          como venta de la actividad (art. 27 LIRPF) y lo demás (mejoras, obra y clientela)
          es ganancia patrimonial de elementos afectos (art. 28.2 LIRPF) en la base del
          ahorro (art. 46.b), sobre su valor neto contable (art. 40 RIRPF): el 19 % en el
          momento, como el Rastro. Sin IGIC: es una unidad económica autónoma (art. 9.1.º de
          la Ley 20/1991). Liquidar (cerrar sin traspaso) cobra igual, a la baja.

        La cuota de autónomos va aparte (`charge_self_employed_fee`). Ver
        `EconomyRepository.business` para el orden de los movimientos.

        Raises:
            InsufficientFundsError: Si no llega para un pago entero.
            BalanceLimitError: Si no le cabe lo que cobra.
        """
        now = self._clock()
        return await self.repository.business(
            guild_id, now=now, week=self._week(now), withhold=withhold_business, prepare=prepare
        )

    async def mom_bizum(self, guild_id: int, user_id: int, *, amount: int) -> int:
        """El Bizum de mamá de `pala`: tu madre te adelanta `amount`.

        Tratamiento fiscal: el mismo que un Bizum entre miembros (`bizum`), por
        decisión del proyecto: exento de Sucesiones y Donaciones y sin IRPF
        (art. 6.4 LIRPF). En la vida real un préstamo familiar sin intereses no
        es renta (lo prestado se devuelve) y Hacienda solo pide que conste por
        escrito. Tu madre no es un miembro, así que el dinero se crea de la nada,
        pero vuelve a ella entero con `repay_mom`: la suma es cero.

        Returns:
            El saldo final.
        """
        if amount <= 0:
            raise ValueError("El Bizum tiene que ser positivo.")
        return await self.repository.apply(guild_id, user_id, [LedgerEntry(amount, "bizum:mama")])

    async def repay_mom(self, guild_id: int, user_id: int, *, amount: int) -> int:
        """Devuelve parte del Bizum de mamá: sale del saldo y desaparece.

        Tratamiento fiscal: como `mom_bizum`, exento. Es la otra mitad del
        préstamo: lo que se creó al pedirlo se destruye al devolverlo.

        Raises:
            InsufficientFundsError: Si no le llega. No se mueve nada.

        Returns:
            El saldo final.
        """
        if amount <= 0:
            raise ValueError("La devolución tiene que ser positiva.")
        return await self.repository.apply(
            guild_id, user_id, [LedgerEntry(-amount, "bizum:mama_devuelto")]
        )

    async def purchase(
        self,
        guild_id: int,
        user_id: int,
        *,
        base: int,
        tax: int,
        concept: str,
        reserve: Callable[[sqlite3.Connection], T],
    ) -> tuple[T, int]:
        """Cobra una compra de la tienda con su IGIC.

        Tratamiento fiscal: IGIC. Una compra es una entrega de bienes o una
        prestación de servicios a título oneroso hecha en Canarias (art. 4 de
        la Ley 20/1991), así que paga IGIC al tipo del artículo (arts. 51 a 59
        de la Ley 4/2012; ver `bot.services.taxes.IGIC_RATES`) sobre la base
        ya rebajada (art. 22 de la Ley 20/1991). No hay IRPF: gastar no es
        renta. La base va a la caja de la tienda (`SHOP_ACCOUNT_ID`) y el
        IGIC al Estado, en la misma transacción.

        Args:
            base: Base imponible (precio tras la rebaja); positiva.
            tax: IGIC de esa base.
            concept: Motivo corto y estable para el libro (`"rol"`, `"xp"`…).
            reserve: Comprobaciones y apuntes de la tienda dentro de la
                transacción (ver `EconomyRepository.purchase`).

        Returns:
            `(lo que devuelva reserve, saldo final)`.

        Raises:
            InsufficientFundsError: Si no le llega. No se mueve nada.
        """
        return await self.purchase_lines(
            guild_id, user_id, lines=[(base, tax, concept)], reserve=reserve
        )

    async def purchase_lines(
        self,
        guild_id: int,
        user_id: int,
        *,
        lines: Sequence[tuple[int, int, str]],
        reserve: Callable[[sqlite3.Connection], T],
    ) -> tuple[T, int]:
        """Cobra un carrito de la tienda: varias líneas `(base, IGIC, concepto)` de una vez.

        Mismo tratamiento fiscal que `purchase`, línea a línea (cada una con su
        tipo de IGIC). Todo o nada: si `reserve` lanza o no le llega para el
        total, no se cobra ninguna.

        Raises:
            InsufficientFundsError: Si no le llega para el total. No se mueve nada.
        """
        return await self.repository.purchase_lines(
            guild_id, user_id, lines=lines, now=self._clock(), reserve=reserve
        )

    async def refund_purchase(
        self,
        guild_id: int,
        user_id: int,
        *,
        base: int,
        tax: int,
        concept: str,
        release: Callable[[sqlite3.Connection], None],
    ) -> int:
        """Devuelve una compra que no se pudo entregar (p. ej. un rol que no se pudo dar).

        Es una factura rectificativa: la caja devuelve la base y el Estado
        el IGIC, que deja de contar como recaudado.

        Returns:
            El saldo final.
        """
        return await self.repository.refund_purchase(
            guild_id,
            user_id,
            base=base,
            tax=tax,
            concept=concept,
            now=self._clock(),
            release=release,
        )

    async def state_balance(self, guild_id: int) -> int:
        """Saldo de la cuenta del Estado."""
        return await self.repository.state_balance(guild_id)

    async def lottery(
        self,
        guild_id: int,
        *,
        charges: Sequence[tuple[int, int, str]] = (),
        payouts: Sequence[LotteryPayout] = (),
        hook: Callable[[sqlite3.Connection], T],
    ) -> tuple[T, dict[int, int]]:
        """Cobra boletos de lotería y paga premios, con el Estado de banca.

        Tratamiento fiscal:

        - **Compra:** sin IGIC. Las loterías, apuestas y juegos de la SELAE y
          la ONCE están exentos (art. 10.1.19º de la Ley 20/1991 del REF de
          Canarias). No es gasto deducible ni pérdida de juego: lo jugado a la
          lotería no compensa nada en la renta (art. 33.5.d LIRPF solo deja
          compensar pérdidas de juego con ganancias de juego, y estos premios
          ni siquiera están en la base general). El dinero va al Estado.
        - **Premio:** gravamen especial del 20 % sobre lo que pase de 40.000 €
          por décimo o apuesta (disposición adicional 33ª LIRPF), retenido
          por el pagador y definitivo. No entra en la retención diaria del
          casino ni en la renta semanal. Lo calcula `taxes.lottery_tax`.
        - **Banca:** los premios salen de la cuenta del Estado. Si no le llega,
          emite deuda pública por la diferencia (`economy_public_debt`).

        Args:
            charges: `(user_id, importe, motivo)` de cada compra.
            payouts: Premios con su gravamen ya calculado.
            hook: Apuntes de la lotería dentro de la misma transacción.

        Returns:
            `(lo que devuelva hook, saldo final de cada miembro tocado)`.

        Raises:
            InsufficientFundsError: Si a alguien no le llega. No se mueve nada.
        """
        return await self.repository.lottery(
            guild_id, charges=charges, payouts=payouts, now=self._clock(), hook=hook
        )

    async def ong_totals(self, guild_id: int) -> dict[str, tuple[int, int]]:
        """Por ONG: `(recaudado, donantes)` en el servidor."""
        return await self.repository.ong_totals(guild_id)

    async def ong_board(
        self, guild_id: int, ong: str | None = None, limit: int = 10
    ) -> list[tuple[int, int]]:
        """El muro de donantes (ver `EconomyRepository.ong_board`)."""
        return await self.repository.ong_board(guild_id, ong, limit)

    async def ong_member(self, guild_id: int, user_id: int) -> dict[str, int]:
        """Lo donado por un miembro a cada ONG (ver `EconomyRepository.ong_member`)."""
        return await self.repository.ong_member(guild_id, user_id)

    async def draw_ong_raffles(
        self,
        guild_id: int,
        window: tuple[datetime, datetime],
        draw: Callable[[str, dict[int, tuple[int, int]]], int | None],
    ) -> list[tuple[str, int]]:
        """Los sorteos semanales de las ONGs (ver `EconomyRepository.draw_ong_raffles`).

        Args:
            window: `(desde, hasta)` de `donations.raffle_window`; el sorteo es `hasta`.

        No mueven dinero: el premio es un artículo de la tienda que regala la ONG.
        """
        start, end = window
        return await self.repository.draw_ong_raffles(
            guild_id,
            end.date().isoformat(),
            (start.timestamp(), end.timestamp()),
            draw,
            self._clock(),
        )

    async def casino_ledger(
        self, guild_id: int, user_id: int | None = None
    ) -> tuple[list[tuple[str, int, int]], int, int, int, float | None]:
        """Apuestas, premios e IRPF del casino según el libro, desde el primer día.

        Solo lee; lo usa `apuestas` (`bot.services.casino_stats.ledger_from_rows`
        lo convierte en cifras por juego). Ver `EconomyRepository.casino_ledger`.
        """
        return await self.repository.casino_ledger(guild_id, user_id)

    async def ledger_summary(
        self,
        guild_id: int,
        *,
        account: int | None,
        reasons: Sequence[str],
        since: float | None = None,
    ) -> dict[str, tuple[int, int]]:
        """`{motivo: (movimientos, suma)}` del libro de una cuenta o de los miembros (solo lee).

        Ver `EconomyRepository.ledger_summary`.
        """
        return await self.repository.ledger_summary(
            guild_id, account=account, reasons=reasons, since=since
        )

    async def system_balances(self, guild_id: int, accounts: Sequence[int]) -> dict[int, int]:
        """Saldo de cuentas de la casa (la caja de la tienda, los botes), sin abrirlas."""
        return await self.repository.system_balances(guild_id, accounts)

    async def member_balances(self, guild_id: int) -> dict[int, int]:
        """Saldo de cada miembro del servidor (para `fortunas`). Solo lee."""
        return await self.repository.member_balances(guild_id)

    async def tax_breakdown(
        self, guild_id: int, *, since: float | None = None
    ) -> dict[int, dict[str, int]]:
        """Impuestos pagados por cada miembro, por tipo (ver `EconomyRepository.tax_breakdown`)."""
        return await self.repository.tax_breakdown(guild_id, since=since)

    async def tax_debt(self, guild_id: int, user_id: int) -> int:
        """Lo que un miembro aún debe a Hacienda (Patrimonio o la declaración del IRPF).

        Se cobra del monedero el lunes, se embarga el 30 % de todo lo que cobra
        (`taxes.GARNISH_SHARE`) y se puede liquidar con la Renta (`pay_tax_debt`).
        """
        return await self.repository.tax_debt(guild_id, user_id)

    async def housing_rent_totals(self, guild_id: int) -> dict[int, int]:
        """Toda la renta de `pisos` que ha cobrado cada miembro, antes del IRPF."""
        return await self.repository.reason_totals(guild_id, "pisos:renta")

    async def housing_tax_breakdown(self, guild_id: int) -> dict[int, dict[str, int]]:
        """Lo que cada miembro ha pagado por sus pisos (ver `EconomyRepository`)."""
        return await self.repository.housing_tax_breakdown(guild_id)

    async def business_tax_breakdown(self, guild_id: int) -> dict[int, dict[str, int]]:
        """Lo que cada dueño ha pagado por sus negocios (ver `EconomyRepository`)."""
        return await self.repository.business_tax_breakdown(guild_id)

    async def treasury(self, guild_id: int, *, since: float, top: int = 5) -> Treasury:
        """Cuenta del Estado: saldo, recaudación total y desde `since`, y quién más paga."""
        return await self.repository.treasury(guild_id, since, top)

    async def claim_daily(self, guild_id: int, user_id: int) -> DailyResult:
        """Cobra el IMV si ya toca; si no, informa de cuándo.

        Lo cobrado trabajando la última semana lo reduce (`imv_after_work`) y
        una sanción de la Inspección de Trabajo lo suspende unos días.
        """
        now = self._clock()
        abroad = await self.repository.residence(guild_id, user_id)
        if abroad is not None:
            # Ley 19/2021: hay que residir en España (art. 10) y comunicar las
            # salidas de más de 90 días al año (art. 36.e). Quien se ha mudado
            # a trabajar fuera, no cobra.
            state = await self.repository.daily_state(guild_id, user_id)
            return DailyResult(
                claimed=False,
                amount=0,
                balance=await self.balance(guild_id, user_id),
                streak=state.streak if state else 0,
                next_claim_at=now,
                abroad=abroad,
            )
        suspended = await self.repository.imv_suspended_until(guild_id, user_id)
        if suspended > now:
            state = await self.repository.daily_state(guild_id, user_id)
            return DailyResult(
                claimed=False,
                amount=0,
                balance=await self.balance(guild_id, user_id),
                streak=state.streak if state else 0,
                next_claim_at=suspended,
                suspended_until=suspended,
            )
        work_net = await self.repository.work_net(guild_id, user_id, now - IMV_WORK_WINDOW_SECONDS)
        full: list[int] = []

        today = local_day(now)

        def decide(previous: DailyClaim | None) -> tuple[int, int] | None:
            streak = next_streak(previous, today)
            if streak is None:
                return None
            full.append(daily_amount(streak))
            return imv_after_work(full[-1], work_net), streak

        claimed = await self.repository.claim_daily(guild_id, user_id, now=now, decide=decide)
        if claimed is not None:
            amount, balance = claimed
            state = await self.repository.daily_state(guild_id, user_id)
            assert state is not None  # se acaba de escribir en la misma operación
            return DailyResult(
                claimed=True,
                amount=amount,
                balance=balance,
                streak=state.streak,
                next_claim_at=next_daily_at(now),
                full_amount=full[-1],
                work_net=work_net,
            )

        state = await self.repository.daily_state(guild_id, user_id)
        assert state is not None  # solo se rechaza si ya hubo un cobro previo
        return DailyResult(
            claimed=False,
            amount=0,
            balance=await self.balance(guild_id, user_id),
            streak=state.streak,
            next_claim_at=next_daily_at(state.last_claimed_at),
            work_net=work_net,
        )

    async def daily_preview(self, guild_id: int, user_id: int) -> tuple[int, int] | None:
        """El IMV que aún puede cobrar hoy: `(cantidad, racha que se juega)`, o `None`.

        Solo lee (para `saldo`): `None` si ya lo cobró hoy, vive fuera o está suspendido.
        La racha es la que pierde si no lo cobra antes de medianoche (0 si ya no tenía).
        """
        now = self._clock()
        if await self.repository.residence(guild_id, user_id) is not None:
            return None
        if await self.repository.imv_suspended_until(guild_id, user_id) > now:
            return None
        previous = await self.repository.daily_state(guild_id, user_id)
        streak = next_streak(previous, local_day(now))
        if streak is None:
            return None
        work_net = await self.repository.work_net(guild_id, user_id, now - IMV_WORK_WINDOW_SECONDS)
        return imv_after_work(daily_amount(streak), work_net), streak - 1

    # -- Trabajo (`pala`) -------------------------------------------------------------

    async def pay_salary(
        self,
        guild_id: int,
        user_id: int,
        *,
        gross: int,
        concept: str,
        self_employed: bool = False,
        beckham: bool = False,
        pension: bool = False,
    ) -> SalaryResult:
        """Paga la nómina de un turno de `pala` (o la pensión de un jubilado).

        Tratamiento fiscal: rendimiento del trabajo (art. 17.1 LIRPF) con
        retención de IRPF y cotización a la Seguridad Social (ver la nómina en
        `bot.services.taxes`). Al Estado van la retención, la cotización del
        trabajador y la de la empresa. La de la empresa se crea de la nada,
        porque la empresa no existe: es una decisión del proyecto para que el
        trabajo engorde las arcas (y los botes de la lotería).

        Un autónomo cobra rendimientos de actividades económicas (art. 27
        LIRPF): sin cotización por turno (paga la cuota con
        `charge_self_employed_fee`, que la declaración resta como gasto) y con el
        mismo tipo estable que el resto de su renta.

        Quien vuelve de trabajar fuera puede tributar por la Ley Beckham
        (`beckham=True`, art. 93 LIRPF): IRPF al 24 % en vez de la escala.

        La retención usa el tipo estable de toda la renta del miembro (el de la
        semana pasada o, si ya es mayor, el de esta: `taxes.stable_rate`), no solo
        el de las nóminas, y la declaración del lunes ajusta el resto.

        La pensión de jubilación de `pala` (`pension=True`) también es
        rendimiento del trabajo (art. 17.2.a.1ª LIRPF): con los gastos y la
        reducción del trabajo, pero sin cotizaciones (`taxes.compute_pension_payslip`).

        Args:
            gross: Bruto del turno; positivo.
            concept: Motivo corto y estable para el libro (`"pala:obra"`).
            self_employed: Si quien cobra es autónomo.
            beckham: Si tributa por la Ley Beckham.
            pension: Si es una pensión de jubilación.
        """
        if gross <= 0:
            raise ValueError("El sueldo debe ser positivo.")
        rule: Callable[[int, Pace], Payslip]
        if pension:
            rule, kind = compute_pension_payslip, INCOME_PENSION
        elif self_employed:
            rule, kind = compute_self_employed_payslip, INCOME_BUSINESS
        elif beckham:
            rule, kind = compute_beckham_payslip, INCOME_BECKHAM
        else:
            rule, kind = compute_payslip, INCOME_WORK
        now = self._clock()
        slip, balance = await self.repository.credit_salary(
            guild_id,
            user_id,
            gross=gross,
            concept=concept,
            now=now,
            payslip_for=rule,
            week=self._week(now),
            kind=kind,
        )
        return SalaryResult(payslip=slip, balance=balance)

    async def pay_foreign_salary(
        self,
        guild_id: int,
        user_id: int,
        *,
        gross: int,
        concept: str,
        resident: bool,
        exempt_left: int,
    ) -> ForeignSalaryResult:
        """Paga la nómina de un turno trabajado desde Hong Kong.

        Tratamiento fiscal (detalle en `bot.services.taxes.compute_hk_payslip`):
        MPF y salaries tax de Hong Kong, que van a su cuenta (`HK_ACCOUNT_ID`),
        no al Estado. Si sigue siendo residente fiscal en España, la parte que
        pasa de la exención del art. 7.p LIRPF paga IRPF con la deducción por
        doble imposición del art. 80 LIRPF; si ya no lo es, España no cobra nada.

        Args:
            resident: Si sigue siendo residente fiscal en España.
            exempt_left: Exención del art. 7.p que le queda hoy, en Y$.
        """
        if gross <= 0:
            raise ValueError("El sueldo debe ser positivo.")

        now = self._clock()

        def payslip_for(amount: int, recent_hk: int, pace: Pace) -> ForeignPayslip:
            return compute_hk_payslip(
                amount,
                recent_hk=recent_hk,
                resident=resident,
                exempt_left=exempt_left,
                pace=pace,
            )

        slip, balance = await self.repository.credit_foreign_salary(
            guild_id,
            user_id,
            gross=gross,
            concept=concept,
            country="hk",
            account_id=HK_ACCOUNT_ID,
            now=now,
            payslip_for=payslip_for,
            week=self._week(now),
            foreign_window_seconds=PROJECTION_WINDOW_SECONDS,
        )
        return ForeignSalaryResult(payslip=slip, balance=balance)

    async def set_residence(self, guild_id: int, user_id: int, country: str | None) -> None:
        """Apunta que se va a vivir fuera (`country`) o que vuelve (`None`)."""
        await self.repository.set_residence(guild_id, user_id, country, self._clock())

    async def foreign_taxes(self, guild_id: int, user_id: int, country: str = "hk") -> int:
        """Lo que se ha quedado otro país de las nóminas de un miembro."""
        return await self.repository.foreign_taxes(guild_id, user_id, country)

    async def charge_self_employed_fee(
        self, guild_id: int, user_id: int, *, amount: int, concept: str
    ) -> tuple[int, int]:
        """Cobra la cuota semanal de autónomos.

        Tratamiento fiscal: cotización al RETA (sistema de cotización por
        ingresos reales del RDL 13/2022; en el juego, cuota fija). Va al Estado
        como Seguridad Social. Si no llega el saldo, se cobra lo que haya.

        Returns:
            `(cobrado, saldo_final)`.
        """
        if amount <= 0:
            raise ValueError("La cuota debe ser positiva.")
        return await self.repository.charge_contribution(
            guild_id, user_id, amount=amount, concept=concept, now=self._clock()
        )

    async def self_employed_fee_paid(self, guild_id: int, user_id: int) -> bool:
        """Si ya ha pagado la cuota de autónomos de esta semana (fiscal).

        Una persona paga una sola cuota al RETA aunque tenga dos actividades (un puesto de
        autónomo en la `pala` y su negocio): la cotización depende de sus rendimientos, no
        de cuántas actividades tenga (RDL 13/2022). Solo lee.
        """
        week = self._week()
        return await self.repository.contribution_paid(guild_id, user_id, start=week.start)

    async def pay_undeclared(
        self, guild_id: int, user_id: int, *, amount: int, concept: str
    ) -> int:
        """Paga dinero en negro: sin retención, sin cotizar y sin que lo vea el IMV.

        Tratamiento fiscal: debería tributar como rendimiento del trabajo
        (art. 17.1 LIRPF) y cotizar (art. 147 LGSS), pero nadie lo declara: es
        el chiste. Si la Inspección lo descubre, `sanction` lo regulariza. No
        deja rastro en `economy_tax_records` ni en `economy_payroll`.

        Returns:
            El saldo final.
        """
        if amount <= 0:
            raise ValueError("La cantidad debe ser positiva.")
        return await self.repository.apply(
            guild_id, user_id, [LedgerEntry(amount, f"negro:{concept}")]
        )

    async def sanction(
        self, guild_id: int, user_id: int, *, amount: int, concept: str
    ) -> tuple[int, int]:
        """Cobra una multa o regularización al Estado (Inspección de Trabajo, UCO…).

        Tratamiento fiscal: sanción administrativa; no es un impuesto, pero va
        al Estado y `hacienda` la cuenta. Si no llega el saldo, se cobra lo que
        haya (no se generan deudas).

        Returns:
            `(cobrado, saldo_final)`.
        """
        if amount <= 0:
            raise ValueError("La multa debe ser positiva.")
        return await self.repository.sanction(
            guild_id, user_id, amount=amount, concept=concept, now=self._clock()
        )

    async def suspend_imv(self, guild_id: int, user_id: int, *, seconds: float) -> float:
        """Suspende el IMV `seconds` segundos desde ahora. Devuelve hasta cuándo."""
        until = self._clock() + seconds
        await self.repository.suspend_imv(guild_id, user_id, until)
        return until

    async def work_week_net(self, guild_id: int, user_id: int) -> int:
        """Neto de las nóminas y del negocio de los últimos 7 días (lo que mira el IMV)."""
        return await self.repository.work_net(
            guild_id, user_id, self._clock() - IMV_WORK_WINDOW_SECONDS
        )

    async def work_net_days(self, guild_id: int, user_id: int, days: int) -> int:
        """Neto de nóminas, pensiones y negocio de los últimos `days` días (lo mira el banco).

        Sin lo cobrado en los tramos de `hide_from_bank`; el IMV (`work_week_net`) sí lo ve.
        """
        return await self.repository.work_net(
            guild_id, user_id, self._clock() - days * 86_400, bank=True
        )

    async def hide_from_bank(self, guild_id: int, user_id: int, since: float, until: float) -> None:
        """Lo que cobre en `pala` o en su negocio entre `since` y `until` no lo ve el banco."""
        await self.repository.hide_from_bank(guild_id, user_id, since, until)

    async def week_tax_burden(self, guild_id: int, user_id: int) -> tuple[int, int]:
        """`(impuestos, neto)` de los últimos 7 días, para «Socio de Hacienda».

        Los impuestos son la Seguridad Social de las nóminas (las dos partes),
        su IRPF y además el IGIC de las compras, el Patrimonio y el IRPF de los
        intereses de la semana.
        El neto es lo cobrado en nómina.
        """
        since = self._clock() - IMV_WORK_WINDOW_SECONDS
        _gross, ss, irpf, net = await self.repository.payroll_totals(guild_id, user_id, since)
        other = await self.repository.other_taxes(guild_id, user_id, since)
        return ss + irpf + other, net

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra la economía del servidor (el bot ha salido de él)."""
        await self.repository.delete_guild_data(guild_id)
