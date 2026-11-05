"""Casos de uso de los negocios de `pala`: montar, comprar, abrir turnos, las noches y cerrar.

Junta las reglas (`bot.services.business`), los minijuegos (`bot.services.business_games`),
la persistencia (`bot.repositories.business`) y el dinero (`EconomyService.business`; la
cuota de autónomos, `charge_self_employed_fee`). Las franjas y la batería son de la persona:
las da `WorkService` (`agenda`, `battery`, `spend_battery`). No sabe nada de Discord: el cog
`bot.cogs.business` dibuja y llama a esto.

Todo el dinero (y su tratamiento fiscal) está documentado en `EconomyService.business`:

- Montar: tasa de la apertura y acondicionamiento (con IGIC), enteros o nada; la fianza al
  depósito de fianzas. La primera cuota de autónomo, con la tarifa plana.
- Comprar al mayorista: base e IGIC, enteros o nada.
- Un turno: sus ventas, con la retención a cuenta sobre el margen y el embargo.
- Cada noche (`close_nights`): el alquiler (con su IGIC y la retención del casero), los
  suministros y lo que se debía del local, hasta donde llegue. A las `EVICTION_NIGHTS`
  noches sin pagar, el casero rescinde: la fianza cubre la deuda y el resto vuelve.
- Cada semana: la cuota de autónomo, si no la ha pagado ya (una por persona).
- Cerrar: la fianza vuelve, menos lo que se deba. Desde la fase 2, el stock y las mejoras se
  traspasan (con la clientela) o se liquidan a la baja (`closing_offer`).

Fase 2 (9.5.0): mejoras que se amortizan cada noche (`buy_equipment`), campañas de
publicidad (`start_campaign`), precio por producto (`set_price`), traslado a otro local o a
uno propio de `pisos` (`move`, `move_to_own`) y reseñas al acabar cada turno.

Fase 4 (9.8.0), la plantilla (`hire`, `dismiss`, `raise_wage`, `make_permanent`, `train`):
empleados artificiales que se contratan entre los candidatos del día. Juegan solos las
franjas de su turno (`staff_turns`, cada 10 minutos, y lo que falte antes de cerrar cada
noche) con el mismo minijuego que el dueño, y cobran su nómina cada noche, con la cotización
de la empresa y la retención (`_close_night`). Reglas en `bot.services.business_staff`.

Fase 5 (9.11.0), las cadenas: hasta `MAX_LOCALES` locales por dueño, cada uno un `Business`
que se busca por su número (`biz`; sin él, el más antiguo). Las franjas, la batería y la
cuota de autónomo son de la persona: los turnos de todos sus locales gastan las mismas. Si
constituye la sociedad, la sociedad se queda con todos (`company_id`, una sola caja) y abre
los que vengan; cada noche cierra los locales de cada dueño en orden y el último de la
sociedad paga la gestoría y, el domingo, Sociedades con lo de todos (`_close_night`).
Cerrar un local de una sociedad que tiene más no la liquida (`_close`).

Préstamos al negocio (9.7.0, `loan_desk`, `take_loan`, `repay_loan`): los pide el dueño en
`banco` (🏪 Negocio, también desde la portada). Entran en la caja del negocio y su noche cobra
las cuotas después del local (y de Sociedades): sus intereses y la comisión son gasto. Al
constituir la sociedad y al cerrar, lo que no pague la caja se lo queda su dueño como préstamo
personal. Reglas en `bot.services.loans` y `HousingRepository.take_business_loan_in`.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from bot.repositories.business import BusinessRepository
from bot.repositories.economy import BANK_LOAN_CONCEPT, company_account
from bot.repositories.housing import BusinessLoanDesk, HousingError
from bot.repositories.housing_finance import Loan, business_loans_in, close_loan_in
from bot.services import business_chain as chain
from bot.services import business_sl as sl
from bot.services import business_staff as staff
from bot.services import loans as lending
from bot.services.agenda import FRANJAS_DIA, Agenda
from bot.services.business import (
    EVICTION_NIGHTS,
    FLAT_RATE_DAYS,
    MAX_LOCALES,
    REPUTATION_MAX,
    Business,
    ClosingOffer,
    MoveQuote,
    NightlyBill,
    OpeningQuote,
    add_asset,
    add_reviews,
    amortize,
    book_value,
    boost,
    clean_name,
    closing_offer,
    daily_pool,
    drift,
    forget_on_move,
    move_quote,
    nightly_bill,
    opening_quote,
    pool_factor,
    price_reputation,
    review_of,
    setup_asset,
    size_for,
    spoil,
    spoil_units,
    stock_value,
    turn_clients,
    turn_reputation,
    wholesale,
)
from bot.services.business_catalog import (
    CAMPAIGN_BY_KEY,
    EMBLEMS,
    EQUIPMENT_BY_KEY,
    PALETTES,
    PRICE_BY_KEY,
    SECTOR_BY_KEY,
    SIZES,
)
from bot.services.business_chain import ImportQuote, Nave, NaveQuote, SaleOffer
from bot.services.business_games import (
    Client,
    TurnGame,
    TurnSummary,
    apply_loyalty,
    make_clients,
)
from bot.services.business_staff import Dismissal, Payroll
from bot.services.economy import (
    BalanceLimitError,
    BusinessCost,
    BusinessMove,
    BusinessPaid,
    EconomyService,
    InsufficientFundsError,
)
from bot.services.housing_catalog import BARRIO_BY_KEY, Listing
from bot.services.housing_market import HousingService
from bot.services.levels import TIMEZONE, local_day
from bot.services.pala import WorkService
from bot.services.taxes import igic
from bot.services.telemetry import TimedLock
from bot.services.work import (
    BATTERY_MIN,
    FLAT_RATE_FEE,
    SELF_EMPLOYED_FEE,
    SHIFT_COST,
    TIRED,
    week_of,
)

if TYPE_CHECKING:
    from bot.repositories.housing import HousingRepository

logger = logging.getLogger(__name__)

#: Noches que se cierran como mucho de una vez (si el bot estuvo apagado).
CATCH_UP_NIGHTS = 7
#: Lotes que se pueden comprar de una vez.
MAX_LOTS = 20


class BusinessError(Exception):
    """Algo que impide la acción; el mensaje es para el usuario."""


class StaffRefusedError(BusinessError):
    """Un empleado no acepta trabajar menos horas (art. 12.4.e ET): va con su logro."""


def _account(business: Business) -> int | None:
    """La caja que paga y cobra por el negocio: la de su sociedad, o `None` (su dueño)."""
    if not business.is_company:
        return None
    return company_account(business.company_id or business.id)


def _broke(business: Business, total: int) -> str:
    """El aviso de que no llega, de quien sea la caja."""
    if business.is_company:
        return (
            f"La caja de la sociedad no llega: son {total} Y$. Aporta o préstale dinero desde 🏢 "
            "Sociedad. O fináncialo: pide un préstamo al negocio en 🏦 Banco."
        )
    return (
        f"No te llega: son {total} Y$ con el IGIC. "
        "O fináncialo: pide un préstamo al negocio en 🏦 Banco."
    )


@dataclass(frozen=True, slots=True)
class CompanyView:
    """Lo que enseña la pantalla 🏢 Sociedad.

    Attributes:
        business: El negocio.
        cash: Lo que hay en la caja de la sociedad (0 si es de autónomo).
        equity: Su patrimonio neto.
        dividend_room: Lo más que se puede repartir ahora.
        retention: La retención del sueldo de administrador ahora.
        blocked: Por qué no puede administrar una sociedad su dueño (incompatibilidad), o `None`.
        dissolution: Si está en causa de disolución por pérdidas.
        bank: Lo que debe al banco de sus préstamos.
        locales: Los locales de la sociedad (o los del autónomo que pasarían a ella).
    """

    business: Business
    cash: int
    equity: int
    dividend_room: int
    retention: float
    blocked: sl.Incompatibility | None
    dissolution: bool
    bank: int = 0
    locales: tuple[Business, ...] = ()


@dataclass(frozen=True, slots=True)
class BusinessStatus:
    """Foto de un negocio para el panel.

    Attributes:
        business: El negocio, con los contadores de hoy al día.
        agenda: Las franjas de hoy de su dueño.
        bill: Lo que cuesta su noche.
        pool_left: Clientes que le quedan hoy al barrio para su sector.
        rivals: Otros negocios del mismo sector en su barrio.
        battery: Batería del dueño (es de la persona: la gasta aunque no tenga curro).
        now: Cuándo se hizo la foto.
    """

    business: Business
    agenda: Agenda
    bill: NightlyBill
    pool_left: float
    rivals: int
    battery: float
    now: float

    @property
    def blocker(self) -> str | None:
        """Lo que impide abrir un turno ahora, o `None`."""
        if not self.agenda.fits(1):
            return (
                "No te quedan franjas hoy, ni trasnochando: la jornada, las horas extra y "
                "la noche se las han comido."
            )
        if self.battery <= BATTERY_MIN:
            return "Estás tan reventado que te quedas dormido de pie. Descansa un poco."
        if not any(self.business.stock.get(p.key, 0) for p in self.business.sector_info.products):
            return "No tienes nada que vender: pasa por el 📦 almacén."
        if self.pool_left < 1:
            return "Hoy el barrio ya no da más de sí: todos tus clientes ya han comprado."
        return None


@dataclass(frozen=True, slots=True)
class TurnPlan:
    """El azar del próximo turno, sorteado antes de abrir (`BusinessService.plan`).

    Con él, el panel pinta el primer cliente antes de que se pulse 🔑 Abrir
    (`BusinessService.preview`) y `start_turn` saca la misma cola si nada ha cambiado.

    Attributes:
        luck: El redondeo de cuántos clientes vienen (`turn_clients`).
        seed: La semilla de la cola (`make_clients`).
    """

    luck: float
    seed: int


@dataclass(slots=True)
class Turn:
    """Un turno en marcha.

    Attributes:
        business: El negocio al empezar (con el turno ya apuntado).
        game: El minijuego.
        battery_before: Batería del dueño al empezar.
    """

    business: Business
    game: TurnGame
    battery_before: float

    @property
    def tired(self) -> bool:
        """Si empezó reventado."""
        return self.battery_before < TIRED


@dataclass(frozen=True, slots=True)
class TurnResult:
    """Lo que dejó un turno.

    Attributes:
        business: El negocio después del turno.
        summary: Lo que pasó con cada cliente.
        withheld: IRPF a cuenta de las ventas.
        garnished: Lo embargado por deudas con Hacienda.
        balance: Saldo final del dueño.
        bank_garnished: Lo embargado por deudas con el banco (art. 607 LEC).
        stars_before: Estrellas al empezar.
        best: Si es su mejor turno en ventas.
        hour: Hora canaria a la que empezó.
        weekday: Día de la semana en que empezó (0 = lunes).
        day: Día (ISO, hora canaria) en que empezó.
        reviews: Las reseñas que dejaron sus clientes: `(quién, estrellas, frase)`.
    """

    business: Business
    summary: TurnSummary
    withheld: int
    garnished: int
    balance: int
    stars_before: float
    best: bool
    hour: int
    weekday: int
    day: str = ""
    bank_garnished: int = 0
    reviews: tuple[tuple[str, int, int], ...] = ()

    @property
    def net(self) -> int:
        """Lo que entró en el bolsillo (ventas menos lo retenido y embargado)."""
        return self.summary.sales - self.withheld - self.garnished - self.bank_garnished


@dataclass(frozen=True, slots=True)
class NightRun:
    """Lo que pasó en la noche de un negocio.

    Attributes:
        user_id: Su dueño.
        name: Su nombre.
        day: La noche (el día que se cierra).
        due: Lo que había que pagar (con la deuda de antes).
        unpaid: Lo que no llegó.
        spoiled: Unidades perdidas por producto.
        evicted: Si el casero rescindió el contrato esta noche (o perdió su local propio).
        amortized: Lo amortizado esa noche.
        corporate: El Impuesto sobre Sociedades de la semana que se cerró esa noche (fase 3):
            `{"base", "compensated", "tax", "rate", "reserve", "profit"}`, o `None`.
        refund: El reintegro de la pluriactividad que le llegó esa noche (fase 3).
        loans: Cómo fue la cuota de cada préstamo al negocio: `pagada`, `sin_pagar`,
            `devuelto` (la última) o `vencido` (`HousingRepository.settle_business_night_in`).
        payroll: Lo que costó la plantilla esa noche (brutos, cuota de la empresa y el B).
        staff: Lo que pasó con la plantilla (`_settle_staff`): `sin_cobrar`, `inspeccion`,
            `curso`, `prueba_superada`, `fin_contrato`, `art50` y `dimision`.
        fine: La multa y las cuotas de la Inspección de Trabajo (0 si no pasó).
        turns: Los turnos de la plantilla de ese día que se jugaron antes de cerrarlo.
        nave: Lo que pasó esa noche en la nave del dueño (fase 5): `contenedor` por cada uno
            que llegó y `desahucio` si el casero la rescindió. Va en el primer local de la noche.
        manager: Lo que hizo el gerente del local (fase 5): `repuso`, `nave` (trajo de la
            nave), `contrato` (cubrió una baja) o `falta`.
        support: Los puestos de apoyo que vinieron ese día (9.41.0): sus claves (`limpieza`,
            `reponedor`, `redes`, `rrhh`).
        kept: Las unidades de lo perecedero que salvó esa noche el mozo de almacén.
        cleaned: La reputación que subió esa noche la limpieza.
        rescheduled: Cuántos empezaron un horario nuevo al día siguiente.
    """

    user_id: int
    name: str
    day: date
    due: int
    unpaid: int
    spoiled: dict[str, int] = field(default_factory=dict)
    evicted: bool = False
    amortized: int = 0
    corporate: dict[str, Any] | None = None
    refund: int = 0
    loans: tuple[str, ...] = ()
    payroll: int = 0
    staff: tuple[str, ...] = ()
    fine: int = 0
    turns: tuple[StaffTurn, ...] = ()
    nave: tuple[str, ...] = ()
    manager: tuple[str, ...] = ()
    support: tuple[str, ...] = ()
    kept: int = 0
    cleaned: float = 0.0
    rescheduled: int = 0


@dataclass(frozen=True, slots=True)
class Sale:
    """Cómo fue la venta de la sociedad (fase 5).

    Attributes:
        offer: Lo que se ofreció.
        name: El nombre de la sociedad (el de su local más antiguo).
        locales: Los locales que se fueron con ella.
        staff: Su plantilla, que sigue con el comprador.
        gain_tax: El IRPF de la ganancia (19 %, base del ahorro).
        balance: Saldo final del socio.
    """

    offer: SaleOffer
    name: str
    locales: int
    staff: int
    gain_tax: int
    balance: int


@dataclass(frozen=True, slots=True)
class StaffTurn:
    """Un turno de un empleado, que juega solo (`BusinessService.staff_turns`).

    Attributes:
        user_id: El dueño del negocio.
        name: El empleado.
        employee: Su número en la plantilla.
        day: El día.
        franja: La franja (0-7).
        summary: Lo que pasó con sus clientes, o `None` si no hubo turno.
        absent: Si faltó sin justificar.
        role: Su puesto (`business_staff.ROLES`): `""`, `encargado` o `repartidor`.
        led: Si atendió con el encargado de turno en la misma franja (sin contarle a él).
    """

    user_id: int
    name: str
    employee: int
    day: date
    franja: int
    summary: TurnSummary | None = None
    absent: bool = False
    role: str = ""
    led: bool = False


@dataclass(frozen=True, slots=True)
class Closing:
    """Cómo acabó un negocio que se cierra.

    Attributes:
        business: El negocio cerrado.
        refund: Fianza que vuelve.
        forfeit: Fianza que se queda el casero.
        balance: Saldo final del dueño.
        traspaso: Si se traspasó (si no, se liquidó lo que quedaba).
        stock: Lo cobrado por el stock.
        proceeds: Lo cobrado por las mejoras, la obra y la clientela.
        gain_tax: IRPF de la ganancia patrimonial (19 %, base del ahorro).
        reason: `cierre`, `traspaso`, `desahucio` o `sin_local`.
        company: Si era una sociedad (fase 3): lo de abajo es de su liquidación.
        liquidated: La cuota de liquidación que le llegó al socio.
        loan_repaid: Lo que se le devolvió de su préstamo.
        covered: Lo que pagó él de las deudas de la sociedad.
        derived_debt: Lo que se le queda como deuda con Hacienda (art. 43.1.b LGT).
        corporate_tax: El último Impuesto sobre Sociedades.
        bank_paid: Lo que pagó la caja de la sociedad de sus préstamos al liquidarla.
        bank_moved: Lo que queda de los préstamos del negocio, ahora personales de su dueño.
        staff: Cuántos empleados tenía (se van con el traspaso o se les despide).
        severance: Las indemnizaciones del cierre (despido objetivo, 20 días por año).
        fogasa: Lo que no se pudo pagar a la plantilla y paga el FOGASA (art. 33 ET).
    """

    business: Business
    refund: int
    forfeit: int
    balance: int
    traspaso: bool = False
    stock: int = 0
    proceeds: int = 0
    gain_tax: int = 0
    reason: str = "cierre"
    company: bool = False
    liquidated: int = 0
    loan_repaid: int = 0
    covered: int = 0
    derived_debt: int = 0
    corporate_tax: int = 0
    bank_paid: int = 0
    bank_moved: int = 0
    staff: int = 0
    severance: int = 0
    fogasa: int = 0


@dataclass(frozen=True, slots=True)
class OwnLocal:
    """Un local o nave entera de alguien en `pisos`, para llevar ahí su negocio.

    Attributes:
        listing: El inmueble.
        status: Su estado en `pisos` (`vacio`, `negocio`…).
        size: El tamaño que tendría el negocio ahí (índice de `SIZES`), o `None` si no cabe.
        fits: Si sirve para su sector (local o nave) y está libre.
    """

    listing: Listing
    status: str
    size: int | None
    fits: bool


class BusinessService:
    """Casos de uso de los negocios.

    Args:
        repository: Persistencia de los negocios.
        economy: Única puerta al dinero.
        work: El curro del dueño (franjas y batería).
        clock: Fuente de tiempo (epoch); inyectable en pruebas.
        rng: Azar de las colas de clientes; inyectable en pruebas.
        housing: Los inmuebles de `pisos`, para el local propio (fase 2). Sin ellos no hay
            traslado a un local tuyo.
    """

    def __init__(
        self,
        repository: BusinessRepository,
        economy: EconomyService,
        work: WorkService,
        *,
        clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
        housing: HousingRepository | None = None,
    ) -> None:
        self.repository = repository
        self.economy = economy
        self.work = work
        self.housing = housing
        #: Lo que ve el banco de los ingresos del dueño (los préstamos al negocio).
        self.lender = HousingService(housing, economy, clock=clock) if housing else None
        self._clock = clock
        self.rng = rng or random.Random()
        self._locks: dict[tuple[int, int], asyncio.Lock] = {}

    def now(self) -> float:
        """Hora actual según el reloj del servicio (epoch)."""
        return self._clock()

    def _lock(self, guild_id: int, user_id: int) -> asyncio.Lock:
        key = (guild_id, user_id)
        lock = self._locks.get(key)
        if lock is None:
            for other, value in list(self._locks.items()):
                if not value.locked():
                    del self._locks[other]
            lock = self._locks[key] = TimedLock("negocio:BusinessService._locks")
        return lock

    def _today(self, now: float) -> tuple[date, str]:
        today = local_day(now)
        return today, week_of(today)

    # -- Lo que pregunta la `pala` (`pala.BusinessLink`) --------------------------------

    async def franjas_today(self, guild_id: int, user_id: int) -> int:
        """Turnos de hoy en sus negocios, todos sus locales juntos (0 si no tiene)."""
        today = local_day(self._clock()).isoformat()
        return sum(
            b.turns_today for b in await self.repository.owned(guild_id, user_id) if b.day == today
        )

    async def reset_franjas(self, guild_id: int, user_id: int) -> int:
        """Pone a cero los turnos de hoy de sus negocios (el cupo del barrio no vuelve).

        Returns:
            Cuántos turnos había.
        """
        wiped = 0
        async with self._lock(guild_id, user_id):
            today = local_day(self._clock()).isoformat()
            for business in await self.repository.owned(guild_id, user_id):
                if business.day == today and business.turns_today:
                    wiped += business.turns_today
                    business.turns_today = 0
                    await self.repository.save(guild_id, business)
        return wiped

    async def last_turn_at(self, guild_id: int, user_id: int) -> float | None:
        """Cuándo abrió alguno de sus negocios por última vez (`None` si nunca abrió)."""
        owned = await self.repository.owned(guild_id, user_id)
        return max((b.last_turn_at for b in owned), default=0) or None

    # -- Consulta -----------------------------------------------------------------------

    async def business(
        self, guild_id: int, user_id: int, biz: int | None = None
    ) -> Business | None:
        """Un negocio de un miembro (con los contadores de hoy al día), o `None`.

        Args:
            biz: Cuál de sus locales (`None`: el más antiguo).
        """
        business = await self.repository.business(guild_id, user_id, biz)
        if business is not None:
            business.roll_over(*self._today(self._clock()))
        return business

    async def owned(self, guild_id: int, user_id: int) -> list[Business]:
        """Sus negocios (la cadena), del más antiguo al más nuevo, con los contadores al día."""
        businesses = await self.repository.owned(guild_id, user_id)
        today = self._today(self._clock())
        for business in businesses:
            business.roll_over(*today)
        return businesses

    async def status(
        self, guild_id: int, user_id: int, biz: int | None = None
    ) -> BusinessStatus | None:
        """Foto de un negocio de un miembro para el panel, o `None` si no tiene."""
        business = await self.business(guild_id, user_id, biz)
        if business is None:
            return None
        now = self._clock()
        today = local_day(now)
        used = await self.repository.pool_used(guild_id, business.barrio, business.sector, today)
        others = await self.repository.businesses(guild_id)
        rivals = sum(
            1
            for other in others
            if other.id != business.id
            and other.barrio == business.barrio
            and other.sector == business.sector
        )
        return BusinessStatus(
            business=business,
            agenda=await self.work.agenda(guild_id, user_id),
            bill=nightly_bill(business),
            pool_left=daily_pool(business.sector_info, business.barrio_info)
            * pool_factor(business, today)
            - used,
            rivals=rivals,
            battery=await self.work.battery(guild_id, user_id),
            now=now,
        )

    @staticmethod
    def quote(sector: str, barrio: str) -> OpeningQuote:
        """Lo que cuesta montar `sector` en `barrio`.

        Raises:
            BusinessError: Si el sector o el barrio no existen.
        """
        if sector not in SECTOR_BY_KEY or barrio not in BARRIO_BY_KEY:
            raise BusinessError("Ese negocio o ese barrio no existen.")
        return opening_quote(SECTOR_BY_KEY[sector], BARRIO_BY_KEY[barrio])

    async def closed(self, guild_id: int, user_id: int) -> list[sqlite3.Row]:
        """Los negocios que cerró un miembro."""
        return await self.repository.closed(guild_id, user_id)

    # -- Montar y retocar ---------------------------------------------------------------

    async def open(
        self,
        guild_id: int,
        user_id: int,
        *,
        sector: str,
        barrio: str,
        name: str,
        palette: int,
        emblem: int,
    ) -> tuple[Business, int]:
        """Monta un negocio: tasa, acondicionamiento y fianza, y la cuota de la semana.

        Desde la fase 5 se pueden llevar hasta `MAX_LOCALES` (una cadena), cada uno con su
        sector y su barrio; dos del mismo sector en el mismo barrio no, que se repartirían el
        cupo. Si los que tiene son de su sociedad, el nuevo también: la sociedad abre otro
        establecimiento, lo paga su caja y tributa con ella. La cuota de autónomo es una por
        persona, tenga los locales que tenga.

        Returns:
            `(el negocio, cuota de autónomo cobrada)`.

        Raises:
            BusinessError: Si ya tiene el máximo, algo no existe o no le llega.
        """
        quote = self.quote(sector, barrio)
        try:
            name = clean_name(name)
        except ValueError as error:
            raise BusinessError(str(error)) from error
        if not 0 <= palette < len(PALETTES) or not 0 <= emblem < len(EMBLEMS):
            raise BusinessError("Ese rótulo no existe.")
        async with self._lock(guild_id, user_id):
            now = self._clock()
            today, week = self._today(now)
            head = next(
                (b for b in await self.repository.owned(guild_id, user_id) if b.is_company), None
            )
            if head is not None and (blocked := await self.incompatible(guild_id, user_id, sector)):
                raise BusinessError(
                    f"Tu sociedad no puede abrir eso: {blocked.text} ({blocked.law})."
                )

            def prepare(connection: sqlite3.Connection) -> tuple[Business, BusinessMove]:
                owned = self.repository.owned_in(connection, guild_id, user_id)
                if len(owned) >= MAX_LOCALES:
                    raise BusinessError(
                        f"Ya llevas {MAX_LOCALES} locales: es lo más que da de sí una cadena."
                    )
                _same_barrio(owned, sector, barrio)
                company = next((b for b in owned if b.is_company), None)
                if (
                    company is not None
                    and company.company.get("zec")
                    and sector not in sl.ZEC_SECTORS
                ):
                    raise BusinessError(
                        "Tu sociedad está en la ZEC, que solo admite las actividades de su anexo: "
                        "de los negocios del juego, talleres."
                    )
                business = Business(
                    user_id=user_id,
                    sector=sector,
                    barrio=barrio,
                    name=name,
                    palette=palette,
                    emblem=emblem,
                    opened_at=now,
                    seed=self.rng.randrange(2**31),
                    deposit=quote.deposit,
                    # La primera noche que se cobra es la de hoy.
                    billed_day=(today - timedelta(days=1)).isoformat(),
                )
                if company is not None:
                    # Otro establecimiento de la misma sociedad: su caja, sus cuentas.
                    business.form = "sl"
                    business.company_id = company.company_id
                    business.company = company.company
                business.roll_over(today, week)
                business.add("apertura", -quote.license)
                setup_asset(business, quote.setup + quote.setup_igic)
                if company is not None:
                    sl.ric_invest(business.company, quote.setup + quote.setup_igic, today)
                self.repository.save_in(connection, guild_id, business)
                move = BusinessMove(
                    user_id=user_id,
                    payments=_opening_payments(quote),
                    deposit=quote.deposit,
                    account=_account(business),
                )
                return business, move

            try:
                business, _paid = await self.economy.business(guild_id, prepare)
            except InsufficientFundsError as error:
                raise BusinessError(
                    _broke(head, quote.total)
                    if head is not None
                    else f"No te llega: montarlo cuesta {quote.total} Y$ (con la fianza)."
                ) from error
            fee = await self._charge_fee(guild_id, business, now)
            return business, fee

    async def _charge_fee(self, guild_id: int, business: Business, now: float) -> int:
        """Cobra la cuota de autónomo de esta semana si aún no la ha pagado. Devuelve lo cobrado.

        Tratamiento fiscal: cotización al RETA (`charge_self_employed_fee`). La primera
        semana desde el alta, la tarifa plana (art. 38 ter de la Ley 20/2007). Una sola
        cuota por persona aunque también sea autónomo en la `pala`.
        """
        if await self.economy.self_employed_fee_paid(guild_id, business.user_id):
            return 0
        flat = now - await self._first_opened(guild_id, business) < FLAT_RATE_DAYS * 86_400
        amount = FLAT_RATE_FEE if flat else SELF_EMPLOYED_FEE
        charged, _balance = await self.economy.charge_self_employed_fee(
            guild_id, business.user_id, amount=amount, concept="autonomos"
        )
        if charged:
            fresh = await self.repository.business(guild_id, business.user_id, business.id)
            if fresh is not None:
                fresh.roll_over(*self._today(now))
                fresh.add("cuota", -charged)
                await self.repository.save(guild_id, fresh)
        return charged

    async def _first_opened(self, guild_id: int, business: Business) -> float:
        """Cuándo se dio de alta su dueño: el más antiguo de sus locales (la tarifa plana es
        de la primera alta, no de cada local)."""
        owned = await self.repository.owned(guild_id, business.user_id)
        return min((b.opened_at for b in owned), default=business.opened_at)

    async def _check_barrio(
        self, guild_id: int, user_id: int, sector: str, barrio: str, skip: int = 0
    ) -> None:
        """Que no lleve ya otro local de `sector` en `barrio` (sin contar `skip`)."""
        owned = [b for b in await self.repository.owned(guild_id, user_id) if b.id != skip]
        _same_barrio(owned, sector, barrio)

    async def restyle(
        self,
        guild_id: int,
        user_id: int,
        *,
        name: str | None = None,
        palette: int | None = None,
        emblem: int | None = None,
        price: str | None = None,
        biz: int | None = None,
    ) -> Business:
        """Cambia el nombre, el rótulo o el nivel de precios. No mueve dinero.

        Raises:
            BusinessError: Sin negocio o con algo que no existe.
        """
        async with self._lock(guild_id, user_id):
            business = await self.business(guild_id, user_id, biz)
            if business is None:
                raise BusinessError("No tienes negocio.")
            if name is not None:
                try:
                    business.name = clean_name(name)
                except ValueError as error:
                    raise BusinessError(str(error)) from error
            if palette is not None:
                if not 0 <= palette < len(PALETTES):
                    raise BusinessError("Esa paleta no existe.")
                business.palette = palette
            if emblem is not None:
                if not 0 <= emblem < len(EMBLEMS):
                    raise BusinessError("Ese emblema no existe.")
                business.emblem = emblem
            if price is not None:
                if price not in PRICE_BY_KEY:
                    raise BusinessError("Ese nivel de precios no existe.")
                business.price = price
            return await self.repository.save(guild_id, business)

    async def take_news(self, guild_id: int, user_id: int, biz: int | None = None) -> list[str]:
        """Los avisos guardados de un local (las noches) y los de la nave, y los borra."""
        async with self._lock(guild_id, user_id):
            news: list[str] = []
            nave = await self.repository.nave(guild_id, user_id)
            if nave is not None and nave.news:
                news, nave.news = list(nave.news), []
                await self.repository.save_nave(guild_id, nave)
            business = await self.repository.business(guild_id, user_id, biz)
            if business is None or not business.news:
                return news
            news, business.news = [*business.news, *news], []
            await self.repository.save(guild_id, business)
            return news

    # -- Fase 2: mejoras, campañas y precios ---------------------------------------------

    async def buy_equipment(
        self, guild_id: int, user_id: int, key: str, *, biz: int | None = None
    ) -> tuple[Business, int, int, int]:
        """Compra e instala una mejora del local.

        Tratamiento fiscal: compra con IGIC (al Estado); la base desaparece. No es gasto el
        día que se paga: base más IGIC (que el pequeño empresario no deduce) se amortizan
        cada noche (`business.amortize`, ver `EconomyService.business`).

        Returns:
            `(el negocio, base, IGIC, saldo final)`.

        Raises:
            BusinessError: Sin negocio, si no es para su sector, ya la tiene o no le llega.
        """
        item = EQUIPMENT_BY_KEY.get(key)
        if item is None:
            raise BusinessError("Esa mejora no existe.")
        base, tax = item.cost, igic(item.cost)
        async with self._lock(guild_id, user_id):
            today, week = self._today(self._clock())

            def prepare(connection: sqlite3.Connection) -> tuple[Business, BusinessMove]:
                business = self.repository.business_in(connection, guild_id, user_id, biz)
                if business is None:
                    raise BusinessError("No tienes negocio.")
                if business.sector not in item.sectors:
                    raise BusinessError("Esa mejora no es para tu negocio.")
                if key in business.equipment:
                    raise BusinessError("Ya la tienes instalada.")
                business.roll_over(today, week)
                business.equipment = [*business.equipment, key]
                add_asset(business, key, base + tax, item.kind)
                if business.is_company:
                    # Un activo nuevo en Canarias: materializa la RIC que falte por invertir.
                    sl.ric_invest(business.company, base + tax, today)
                self.repository.save_in(connection, guild_id, business)
                cost = BusinessCost(
                    "negocio:mejora", gone=base, taxes=(("igic", tax),), expense=False
                )
                move = BusinessMove(user_id=user_id, payments=(cost,), account=_account(business))
                return business, move

            current = await self.repository.business(guild_id, user_id, biz)
            try:
                business, paid = await self.economy.business(guild_id, prepare)
            except InsufficientFundsError as error:
                raise BusinessError(_broke(current, base + tax) if current else "") from error
            return business, base, tax, paid.company if business.is_company else paid.balance

    async def start_campaign(
        self, guild_id: int, user_id: int, key: str, *, biz: int | None = None
    ) -> tuple[Business, int, int, int]:
        """Lanza una campaña de publicidad: más clientes y más cupo unos días.

        Tratamiento fiscal: servicio con IGIC general (al Estado); la base desaparece. Es
        gasto entero de la actividad el día que se paga (art. 15.e LIS, por el art. 28.1
        LIRPF, y criterio de cobros y pagos).

        Returns:
            `(el negocio, base, IGIC, saldo final)`.

        Raises:
            BusinessError: Sin negocio, con otra campaña en marcha o si no le llega.
        """
        campaign = CAMPAIGN_BY_KEY.get(key)
        if campaign is None:
            raise BusinessError("Esa campaña no existe.")
        base, tax = campaign.cost, igic(campaign.cost)
        async with self._lock(guild_id, user_id):
            today, week = self._today(self._clock())

            def prepare(connection: sqlite3.Connection) -> tuple[Business, BusinessMove]:
                business = self.repository.business_in(connection, guild_id, user_id, biz)
                if business is None:
                    raise BusinessError("No tienes negocio.")
                if business.campaign and business.campaign_until >= today.isoformat():
                    raise BusinessError("Ya tienes una campaña en marcha: espera a que acabe.")
                business.roll_over(today, week)
                business.campaign = key
                business.campaign_until = (today + timedelta(days=campaign.days - 1)).isoformat()
                business.add("marketing", -(base + tax))
                self.repository.save_in(connection, guild_id, business)
                cost = BusinessCost("negocio:publicidad", gone=base, taxes=(("igic", tax),))
                move = BusinessMove(user_id=user_id, payments=(cost,), account=_account(business))
                return business, move

            current = await self.repository.business(guild_id, user_id, biz)
            try:
                business, paid = await self.economy.business(guild_id, prepare)
            except InsufficientFundsError as error:
                raise BusinessError(_broke(current, base + tax) if current else "") from error
            return business, base, tax, paid.company if business.is_company else paid.balance

    async def set_price(
        self,
        guild_id: int,
        user_id: int,
        product: str,
        level: str | None,
        *,
        biz: int | None = None,
    ) -> Business:
        """Pone el nivel de precios de un producto (`None`: el general). No mueve dinero.

        Raises:
            BusinessError: Sin negocio o con algo que no existe.
        """
        async with self._lock(guild_id, user_id):
            business = await self.business(guild_id, user_id, biz)
            if business is None:
                raise BusinessError("No tienes negocio.")
            if business.sector_info.product(product) is None:
                raise BusinessError("Eso no se vende en tu negocio.")
            if level is not None and level not in PRICE_BY_KEY:
                raise BusinessError("Ese nivel de precios no existe.")
            prices = dict(business.prices)
            if level is None or level == business.price:
                prices.pop(product, None)
            else:
                prices[product] = level
            business.prices = prices
            return await self.repository.save(guild_id, business)

    # -- Fase 2: trasladarse ----------------------------------------------------------

    def move_quote(self, business: Business, barrio: str, size: int) -> MoveQuote:
        """Lo que cuesta trasladar `business` a un local alquilado.

        Raises:
            BusinessError: Si el barrio o el tamaño no existen.
        """
        if barrio not in BARRIO_BY_KEY or not 0 <= size < len(SIZES):
            raise BusinessError("Ese local no existe.")
        return move_quote(business, BARRIO_BY_KEY[barrio], size)

    async def own_locals(
        self, guild_id: int, user_id: int, biz: int | None = None
    ) -> list[OwnLocal]:
        """Los locales y naves enteros suyos en `pisos`, con si sirven para su negocio."""
        business = await self.repository.business(guild_id, user_id, biz)
        if business is None or self.housing is None:
            return []
        housing = self.housing

        def read(connection: sqlite3.Connection) -> list[tuple[Listing, str]]:
            return housing.business_locals_in(connection, guild_id, user_id)

        sector = business.sector_info
        out = []
        for listing, status in await self.repository.read(read):
            size = size_for(sector, listing.m2) if listing.kind == sector.kind else None
            free = status == "vacio" or listing.key == business.own_listing
            out.append(OwnLocal(listing, status, size, size is not None and free))
        return out

    async def move(
        self, guild_id: int, user_id: int, barrio: str, size: int, *, biz: int | None = None
    ) -> tuple[Business, MoveQuote, int]:
        """Traslada el negocio a un local alquilado de `barrio` y tamaño `size`.

        Tratamiento fiscal: como montarlo (`open`): la tasa de la comunicación previa del
        traslado (art. 34.2 de la Ley 7/2011 de Canarias), el acondicionamiento (se
        amortiza) y la fianza nueva. La del local de ahora vuelve, menos lo que se deba al
        casero; lo que no cubra se paga entero antes de irse. Si deja un local propio, ese
        vuelve a estar vacío en `pisos`. La obra del local que deja se queda allí.

        Returns:
            `(el negocio, lo que costó, saldo final)`.

        Raises:
            BusinessError: Sin negocio, si es el mismo local o si no le llega.
        """
        async with self._lock(guild_id, user_id):
            current = await self.business(guild_id, user_id, biz)
            if current is None:
                raise BusinessError("No tienes negocio.")
            quote = self.move_quote(current, barrio, size)
            if quote.same_barrio and size == current.size and not current.own_listing:
                raise BusinessError("Ya estás en ese local.")
            await self._check_barrio(guild_id, user_id, current.sector, barrio, current.id)
            return await self._move(guild_id, user_id, current, quote, barrio, None)

    async def move_to_own(
        self, guild_id: int, user_id: int, key: str, *, biz: int | None = None
    ) -> tuple[Business, MoveQuote, int]:
        """Traslada el negocio a un local o nave entera suya de `pisos`: sin alquiler ni fianza.

        Tratamiento fiscal: la tasa del traslado y el acondicionamiento, como `move`. El
        local queda afecto a la actividad (`housing.OWN_BUSINESS`): no se alquila, no se
        vende y no imputa renta (art. 85.1 LIRPF). Sus recibos (IBI, comunidad, seguro) los
        sigue pagando `pisos` (simplificación: en la vida real serían gasto de la actividad).

        Raises:
            BusinessError: Sin negocio, si el local no sirve o si no le llega.
        """
        if self.housing is None:
            raise BusinessError("Ahora mismo no se puede mirar `pisos`.")
        async with self._lock(guild_id, user_id):
            current = await self.business(guild_id, user_id, biz)
            if current is None:
                raise BusinessError("No tienes negocio.")
            if current.own_listing == key:
                raise BusinessError("Ya estás en ese local.")
            if current.is_company:
                raise BusinessError(
                    "Tu sociedad no puede usar un local tuyo sin pagarte un alquiler (operación "
                    "vinculada, art. 18 LIS): de momento, solo locales alquilados."
                )
            local = next(
                (
                    o
                    for o in await self.own_locals(guild_id, user_id, current.id)
                    if o.listing.key == key
                ),
                None,
            )
            if local is None:
                raise BusinessError("Ese local no es entero tuyo.")
            if local.listing.kind != current.sector_info.kind:
                kind = "una nave" if current.sector_info.kind == "nave" else "un local"
                raise BusinessError(f"Tu negocio necesita {kind}.")
            if local.size is None:
                raise BusinessError("No cabe: el local es demasiado pequeño para tu negocio.")
            barrio = BARRIO_BY_KEY[local.listing.barrio]
            await self._check_barrio(guild_id, user_id, current.sector, barrio.key, current.id)
            quote = move_quote(current, barrio, local.size, own=True)
            return await self._move(guild_id, user_id, current, quote, barrio.key, key)

    async def _move(
        self,
        guild_id: int,
        user_id: int,
        current: Business,
        quote: MoveQuote,
        barrio: str,
        own: str | None,
    ) -> tuple[Business, MoveQuote, int]:
        """Mueve el dinero y el negocio de un traslado (con el candado ya cogido)."""
        now = self._clock()
        today, week = self._today(now)
        housing = self.housing

        def prepare(connection: sqlite3.Connection) -> tuple[Business, BusinessMove]:
            business = self.repository.business_in(connection, guild_id, user_id, current.id)
            if business is None:
                raise BusinessError("Tu negocio ya no existe.")
            business.roll_over(today, week)
            forfeit = min(business.deposit, business.rent_debt)
            owed = business.rent_debt - forfeit
            if housing is not None and business.own_listing:
                housing.free_business_local_in(
                    connection, guild_id, user_id, business.own_listing, today, now
                )
            if own is not None:
                assert housing is not None
                try:
                    housing.occupy_business_local_in(connection, guild_id, user_id, own, today, now)
                except HousingError as error:
                    raise BusinessError(str(error)) from error
            if barrio != business.barrio:
                forget_on_move(business)
            opening = quote.opening
            refund = business.deposit - forfeit
            business.barrio, business.size, business.own_listing = barrio, quote.size, own or ""
            business.deposit, business.rent_debt, business.unpaid_nights = opening.deposit, 0, 0
            business.assets = [a for a in business.assets if a["key"] != "obra"]
            setup_asset(business, opening.setup + opening.setup_igic)
            if business.is_company:
                sl.ric_invest(business.company, opening.setup + opening.setup_igic, local_day(now))
            business.add("apertura", -opening.license)
            if owed:
                business.add("local", -owed)
            business.tell(f"🚚 Te has trasladado a {BARRIO_BY_KEY[barrio].name}.")
            self.repository.save_in(connection, guild_id, business)
            payments = (
                *((BusinessCost("negocio:deuda_local", gone=owed),) if owed else ()),
                *_opening_payments(opening),
            )
            move = BusinessMove(
                user_id=user_id,
                refund=refund,
                forfeit=forfeit,
                payments=payments,
                deposit=opening.deposit,
                account=_account(business),
            )
            return business, move

        try:
            business, paid = await self.economy.business(guild_id, prepare)
        except InsufficientFundsError as error:
            raise BusinessError(
                _broke(current, quote.total)
                if current.is_company
                else f"No te llega: el traslado cuesta {quote.total} Y$ (con la fianza nueva)."
            ) from error
        return business, quote, paid.balance

    # -- Fase 3: la sociedad limitada ---------------------------------------------------

    async def incompatible(
        self, guild_id: int, user_id: int, sector: str
    ) -> sl.Incompatibility | None:
        """Lo que impide a un miembro administrar una sociedad por su curro (o `None`).

        De excedencia no hay incompatibilidad: no está en servicio activo.
        """
        contract = await self.work.repository.contract(guild_id, user_id)
        if contract is None or contract.on_excedencia:
            return None
        return sl.incompatibility(contract.job, contract.branch, sector)

    async def company_view(
        self, guild_id: int, user_id: int, biz: int | None = None
    ) -> CompanyView | None:
        """La foto de la sociedad (o de lo que costaría constituirla), o `None` sin negocio.

        Una sociedad lleva todos los locales de su dueño: la caja, el patrimonio y lo que
        factura son de todos.
        """
        business = await self.business(guild_id, user_id, biz)
        if business is None:
            return None
        group = await self._group(guild_id, business)
        blocked = await self._blocked(guild_id, user_id, group)
        if not business.is_company:
            return CompanyView(
                business, 0, 0, 0, sl.ADMIN_RETENTION, blocked, False, locales=tuple(group)
            )
        cash = await self.economy.account_balance(guild_id, _account(business) or 0)
        bank = await self._group_bank(guild_id, group)
        net = _equity(group, cash, bank, await self.repository.nave(guild_id, user_id))
        return CompanyView(
            business,
            cash,
            net,
            sl.dividend_room(business.company, cash),
            sl.admin_retention(_last_sales(group)),
            blocked,
            sl.in_dissolution(business.company, net),
            bank,
            tuple(group),
        )

    async def _group(self, guild_id: int, business: Business) -> list[Business]:
        """Los locales de su sociedad (con los contadores al día); de un autónomo, todos los
        suyos (los que pasarían a la sociedad al constituirla)."""
        owned = await self.owned(guild_id, business.user_id)
        if not business.is_company:
            return owned or [business]
        return [b for b in owned if b.company_id == business.company_id] or [business]

    async def _blocked(
        self, guild_id: int, user_id: int, group: list[Business]
    ) -> sl.Incompatibility | None:
        """Lo que impide a su dueño administrar una sociedad con estos locales (o `None`)."""
        for business in group:
            if blocked := await self.incompatible(guild_id, user_id, business.sector):
                return blocked
        return None

    async def _group_bank(self, guild_id: int, group: list[Business]) -> int:
        """Lo que deben al banco los préstamos de estos locales."""
        return sum([await self._bank_owed(guild_id, b.id) for b in group])

    async def _bank_owed(self, guild_id: int, business_id: int) -> int:
        """Lo que debe un negocio al banco de sus préstamos."""
        if self.housing is None:
            return 0
        return sum(loan.owed for loan in await self.housing.business_loans(guild_id, business_id))

    async def _company_income(self, guild_id: int, business: Business) -> int | None:
        """Lo que deja su sociedad al día para el banco (`None` si es de un autónomo): lo que
        dejaron la semana pasada todos sus locales (`business_sl.ebitda`)."""
        if not business.is_company:
            return None
        group = await self._group(guild_id, business)
        return max(0, sum(sl.ebitda(b.last_week) for b in group)) // 7

    async def constitute(
        self, guild_id: int, user_id: int, capital: int, *, biz: int | None = None
    ) -> tuple[Business, int]:
        """Convierte los negocios de un autónomo en una sociedad limitada unipersonal.

        Desde la fase 5, la sociedad se queda con todos sus locales (la cadena): `biz` es el
        que la constituye, y su `id` da la caja (`company_id`).

        Tratamiento fiscal: la constitución está exenta del ITP-AJD (art. 45.I.B.11 del
        TRLITPAJD). El dueño mete en la caja nueva el capital y lo que cuestan la notaría y el
        registro con su IGIC (aportación de socio), y la sociedad los paga: son su primer
        gasto. El negocio (stock, mejoras, obra y fianza) pasa a la sociedad por lo que valía
        en sus cuentas: es la aportación de una rama de actividad, neutral en el IRPF (art. 87
        LIS) y no sujeta al IGIC (art. 9.1.º de la Ley 20/1991). Desde ahí, el negocio tributa
        en el Impuesto sobre Sociedades y su dueño, por lo que cobre de él. Sigue en el RETA
        como autónomo societario (art. 305.2.b LGSS), con la misma cuota.

        Returns:
            `(el negocio, lo que salió del monedero)`.

        Raises:
            BusinessError: Sin negocio, si ya es sociedad, si su curro no lo deja, si algún
                local está en uno propio o si no le llega.
        """
        if capital not in sl.CAPITALS:
            raise BusinessError("Ese capital no está entre los que se pueden escriturar.")
        fees = sl.constitution_fees(capital)
        fees_igic = igic(fees)
        async with self._lock(guild_id, user_id):
            current = await self.business(guild_id, user_id, biz)
            if current is None:
                raise BusinessError("No tienes negocio.")
            if current.is_company:
                raise BusinessError("Tu negocio ya es una sociedad limitada.")
            group = await self._group(guild_id, current)
            if own := next((b for b in group if b.own_listing), None):
                raise BusinessError(
                    f"{own.name} está en un local tuyo: la sociedad tendría que alquilártelo "
                    "(operación vinculada, art. 18 LIS). Trasládalo a uno alquilado antes."
                )
            if blocked := await self._blocked(guild_id, user_id, group):
                raise BusinessError(
                    f"No puedes administrar una sociedad: {blocked.text} ({blocked.law})."
                )
            nave = await self.repository.nave(guild_id, user_id)
            if nave is not None and nave.own_listing:
                raise BusinessError(
                    "Tu nave es tuya: la sociedad tendría que alquilártela (operación vinculada, "
                    "art. 18 LIS). Déjala antes o cámbiala por una alquilada."
                )
            now = self._clock()
            today, week = self._today(now)
            total = capital + fees + fees_igic
            housing = self.housing

            def prepare(connection: sqlite3.Connection) -> tuple[Business, BusinessMove]:
                locales = self.repository.owned_in(connection, guild_id, user_id)
                business = next((b for b in locales if b.id == current.id), None)
                if (
                    business is None
                    or any(b.is_company for b in locales)
                    or {b.id for b in locales} != {b.id for b in group}
                ):
                    raise BusinessError("Tu negocio ha cambiado: vuelve a intentarlo.")
                company = sl.new_company(capital, total, now)
                # Lo de esta semana antes de constituirla es del autónomo: no entra en Sociedades.
                company["offset_week"] = week
                offset: dict[str, int] = {}
                for local in locales:
                    local.roll_over(today, week)
                    for key, value in local.week_totals.items():
                        offset[key] = offset.get(key, 0) + value
                stored = self.repository.nave_in(connection, guild_id, user_id)
                if stored is not None:
                    # La nave es de la rama de actividad: pasa a la sociedad con lo demás.
                    stored.roll_over(week)
                    for key, value in stored.week_totals.items():
                        offset[key] = offset.get(key, 0) + value
                company["offset"] = offset
                business.add("constitucion", -(fees + fees_igic))
                for local in locales:
                    local.form, local.company, local.company_id = "sl", company, business.id
                    local.tell(
                        f"🏢 {business.name}, S.L. ya es una sociedad limitada."
                        if local is business
                        else f"🏢 Este local ya es de {business.name}, S.L."
                    )
                    if (
                        housing is not None
                        and housing.business_loans_to_owner_in(connection, guild_id, local.id, now)[
                            1
                        ]
                    ):
                        # Lo prestó el banco al autónomo y se lo quedó él: siguen a su nombre.
                        local.tell(
                            "🏦 Los préstamos de este local siguen a tu nombre, como personales: "
                            "la sociedad empieza sin deudas con el banco."
                        )
                    self.repository.save_in(connection, guild_id, local)
                cost = BusinessCost(
                    "sociedad:constitucion", gone=fees, taxes=(("igic", fees_igic),), expense=False
                )
                move = BusinessMove(
                    user_id=user_id,
                    account=company_account(business.id),
                    contribution=total,
                    payments=(cost,),
                )
                return business, move

            try:
                business, _paid = await self.economy.business(guild_id, prepare)
            except InsufficientFundsError as error:
                raise BusinessError(
                    f"No te llega: el capital y la notaría suman {total} Y$."
                ) from error
            return business, total

    async def _company_move(
        self,
        guild_id: int,
        user_id: int,
        change: Callable[[Business], BusinessMove],
        broke: str,
        biz: int | None = None,
    ) -> tuple[Business, Any]:
        """Una operación entre el dueño y su sociedad (con el candado y la sociedad comprobada).

        Se hace desde cualquiera de sus locales (`biz`): la sociedad es la misma.
        """
        async with self._lock(guild_id, user_id):
            today, week = self._today(self._clock())

            def prepare(connection: sqlite3.Connection) -> tuple[Business, BusinessMove]:
                business = self.repository.business_in(connection, guild_id, user_id, biz)
                if business is None or not business.is_company:
                    raise BusinessError("Tu negocio no es una sociedad.")
                business.roll_over(today, week)
                move = change(business)
                self.repository.save_in(connection, guild_id, business)
                return business, move

            try:
                return await self.economy.business(guild_id, prepare)
            except InsufficientFundsError as error:
                raise BusinessError(broke) from error

    async def contribute(
        self, guild_id: int, user_id: int, amount: int, *, biz: int | None = None
    ) -> tuple[Business, Any]:
        """Mete dinero en la caja de la sociedad como aportación de socio (no se devuelve).

        Tratamiento fiscal: una aportación de socio (cuenta 118 del PGC) no es ingreso de la
        sociedad ni gasto del dueño; exenta de operaciones societarias (art. 45.I.B.11 del
        TRLITPAJD). Sube lo aportado, que se resta de lo que cobre al liquidarla.
        """
        if amount <= 0:
            raise BusinessError("La cantidad tiene que ser mayor que cero.")

        def change(business: Business) -> BusinessMove:
            business.company["contributed"] += amount
            return BusinessMove(user_id=user_id, account=_account(business), contribution=amount)

        return await self._company_move(
            guild_id, user_id, change, "No te llega para aportar eso.", biz
        )

    async def lend(
        self, guild_id: int, user_id: int, amount: int, *, biz: int | None = None
    ) -> tuple[Business, Any]:
        """Le presta dinero a la sociedad (se le devuelve cuando haya en caja).

        Tratamiento fiscal: sin intereses. Simplificación: entre una sociedad y su socio, el
        art. 18 LIS pide valorarlo a precio de mercado. Si la sociedad se liquida, el préstamo
        del socio cobra el último (es subordinado, arts. 281 y 283 TRLC).
        """
        if amount <= 0:
            raise BusinessError("La cantidad tiene que ser mayor que cero.")

        def change(business: Business) -> BusinessMove:
            business.company["loan"] += amount
            return BusinessMove(user_id=user_id, account=_account(business), loan_in=amount)

        return await self._company_move(
            guild_id, user_id, change, "No te llega para prestar eso.", biz
        )

    async def repay(
        self, guild_id: int, user_id: int, amount: int, *, biz: int | None = None
    ) -> tuple[Business, Any]:
        """La sociedad le devuelve al dueño parte de su préstamo. No es renta: es su dinero."""
        if amount <= 0:
            raise BusinessError("La cantidad tiene que ser mayor que cero.")

        def change(business: Business) -> BusinessMove:
            if amount > business.company["loan"]:
                raise BusinessError(f"Solo te debe {business.company['loan']} Y$ del préstamo.")
            business.company["loan"] -= amount
            return BusinessMove(user_id=user_id, account=_account(business), loan_back=amount)

        return await self._company_move(
            guild_id, user_id, change, "La caja de la sociedad no tiene tanto.", biz
        )

    async def pay_salary(
        self, guild_id: int, user_id: int, amount: int, *, biz: int | None = None
    ) -> tuple[Business, Any]:
        """La sociedad le paga a su administrador un sueldo de `amount` (bruto).

        Tratamiento fiscal: en el IRPF, rendimiento del trabajo (art. 17.2.e LIRPF) con la
        retención del 35 %, o del 19 % si la sociedad factura menos de 100.000 € al año (art.
        101.2 LIRPF y art. 80.1.3.º RIRPF); entra en la Renta del lunes, y en el IMV y el banco
        como una nómina. En Sociedades es gasto deducible: no es una liberalidad (art. 15.e LIS)
        porque los estatutos tipo del juego lo prevén (art. 217 LSC). Sin cotización aparte: el
        administrador ya paga su cuota de autónomo societario.

        Raises:
            BusinessError: Si su curro no le deja administrar o la caja no llega.
        """
        if amount <= 0:
            raise BusinessError("La cantidad tiene que ser mayor que cero.")
        business = await self.business(guild_id, user_id, biz)
        group = await self._group(guild_id, business) if business is not None else []
        if blocked := await self._blocked(guild_id, user_id, group):
            raise BusinessError(
                f"No puedes cobrar de administrador: {blocked.text} ({blocked.law})."
            )
        rate = sl.admin_retention(_last_sales(group))

        def change(business: Business) -> BusinessMove:
            business.add("sueldo", -amount)
            business.company["paid_out"] += amount
            return BusinessMove(
                user_id=user_id, account=_account(business), salary=amount, salary_rate=rate
            )

        return await self._company_move(
            guild_id, user_id, change, "La caja de la sociedad no tiene tanto.", biz
        )

    async def pay_dividend(
        self, guild_id: int, user_id: int, amount: int, *, biz: int | None = None
    ) -> tuple[Business, Any]:
        """La sociedad reparte `amount` de dividendo a su socio único.

        Tratamiento fiscal: rendimiento del capital mobiliario (art. 25.1.a LIRPF) de la base
        del ahorro (art. 46), con la retención del 19 % (art. 101.4). Simplificación: como las
        plusvalías del Rastro, ese 19 % es lo que paga, sin la escala del ahorro. Solo de lo
        que queda disponible tras el Impuesto sobre Sociedades y la reserva legal, y si el
        patrimonio neto no baja del capital (art. 273 LSC).

        Raises:
            BusinessError: Si no hay tanto para repartir.
        """
        if amount <= 0:
            raise BusinessError("La cantidad tiene que ser mayor que cero.")
        view = await self.company_view(guild_id, user_id, biz)
        if view is None or not view.business.is_company:
            raise BusinessError("Tu negocio no es una sociedad.")
        room = min(view.dividend_room, max(0, view.equity - view.business.company["capital"]))
        if amount > room:
            raise BusinessError(
                f"Solo puedes repartir {room} Y$: lo que quedó tras Sociedades y la reserva "
                "legal, sin dejar el patrimonio por debajo del capital."
            )

        def change(business: Business) -> BusinessMove:
            business.company["retained"] -= amount
            business.company["paid_out"] += amount
            return BusinessMove(user_id=user_id, account=_account(business), dividend=amount)

        return await self._company_move(
            guild_id, user_id, change, "La caja de la sociedad no tiene tanto.", biz
        )

    async def set_ric(
        self, guild_id: int, user_id: int, share: float, *, biz: int | None = None
    ) -> Business:
        """Cuánto de la base de cada semana lleva la sociedad a la RIC (de `RIC_SHARES`).

        Raises:
            BusinessError: Si no es una sociedad o la parte no está en la lista.
        """
        if share not in sl.RIC_SHARES:
            raise BusinessError("Esa dotación no está en la lista.")
        async with self._lock(guild_id, user_id):
            business = await self.business(guild_id, user_id, biz)
            if business is None or not business.is_company:
                raise BusinessError("La RIC es de las sociedades: tu negocio no lo es.")
            business.company["ric_share"] = share
            return await self.repository.save(guild_id, business)

    async def zec(
        self, guild_id: int, user_id: int, *, join: bool, biz: int | None = None
    ) -> Business:
        """Inscribe la sociedad en el registro de la ZEC (`join`) o la da de baja.

        Con varios locales cuenta los empleos de todos, y todos tienen que ser de una actividad
        del anexo; el mínimo de empleos es el de la isla que más pide.

        Raises:
            BusinessError: Si no es una sociedad o no cumple (`business_sl.zec_blocker`).
        """
        async with self._lock(guild_id, user_id):
            business = await self.business(guild_id, user_id, biz)
            if business is None or not business.is_company:
                raise BusinessError("La ZEC es para sociedades: tu negocio no lo es.")
            info = business.company
            if join:
                group = await self._group(guild_id, business)
                jobs, island = _zec_jobs(group)
                odd = next((b for b in group if b.sector not in sl.ZEC_SECTORS), business)
                blocked = sl.zec_blocker(odd.sector, island, jobs, local_day(self._clock()))
                if blocked is not None:
                    raise BusinessError(blocked)
                info.update(zec=True, zec_misses=0, zec_since=local_day(self._clock()).isoformat())
            else:
                info["zec"] = False
            return await self.repository.save(guild_id, business)

    # -- Préstamos al negocio (`banco`, 🏪 Negocio) -------------------------------------

    async def loan_desk(
        self, guild_id: int, user_id: int, biz: int | None = None
    ) -> tuple[Business, BusinessLoanDesk] | None:
        """El negocio y lo que ve el banco para prestarle, o `None` sin negocio o sin banco."""
        business = await self.business(guild_id, user_id, biz)
        if business is None or self.housing is None or self.lender is None:
            return None
        income, payroll = await self.lender.lender_view(guild_id, user_id)
        desk = await self.housing.business_desk(
            guild_id, user_id, business.id, income=income, payroll=payroll,
            company_income=await self._company_income(guild_id, business),
        )  # fmt: skip
        return business, desk

    async def take_loan(
        self,
        guild_id: int,
        user_id: int,
        *,
        principal: int,
        months: int,
        rate: float,
        biz: int | None = None,
    ) -> tuple[Business, Loan, lending.Quote, BusinessPaid]:
        """Firma un préstamo al negocio: lo prestado entra en su caja y la comisión sale.

        Tratamiento fiscal: lo prestado no es renta (se devuelve). La comisión de apertura,
        como los intereses de cada noche, es gasto de la actividad (art. 28.1 LIRPF, que remite
        al art. 15 LIS) o de la sociedad; el límite del art. 16 LIS a los gastos financieros
        no se alcanza por debajo de un millón de euros al año. No es crédito al consumo (art. 2
        de la Ley 16/2011), así que no hay desistimiento. El banco no existe: lo prestado se
        crea y lo que cobra desaparece.

        Args:
            rate: El tipo que enseñaba la oferta (si ha cambiado, no se firma).

        Raises:
            BusinessError: Sin negocio, con deudas con Hacienda o si el banco no presta (ver
                `HousingRepository.take_business_loan_in`).
        """
        housing, lender = self.housing, self.lender
        if housing is None or lender is None:
            raise BusinessError("El banco está cerrado ahora mismo.")
        if await self.economy.tax_debt(guild_id, user_id) > 0:
            # Lo prestado se embargaría al entrar: el banco no presta a quien debe a Hacienda.
            raise BusinessError(
                "Debes dinero a Hacienda y tendrías la cuenta embargada: el banco no presta. "
                "Liquida la deuda en `renta`."
            )
        income, payroll = await lender.lender_view(guild_id, user_id)
        async with self._lock(guild_id, user_id):
            now = self._clock()
            today, week = self._today(now)
            signed: list[tuple[Loan, lending.Quote]] = []
            current = await self.repository.business(guild_id, user_id, biz)
            if current is None:
                raise BusinessError("No tienes negocio.")
            company_income = await self._company_income(guild_id, current)

            def prepare(connection: sqlite3.Connection) -> tuple[Business, BusinessMove]:
                business = self.repository.business_in(connection, guild_id, user_id, current.id)
                if business is None:
                    raise BusinessError("No tienes negocio.")
                business.roll_over(today, week)
                loan, offer = housing.take_business_loan_in(
                    connection, guild_id, user_id, business.id, principal=principal,
                    months=months, rate=rate, income=income, payroll=payroll,
                    company_income=company_income, day=today, now=now,
                )  # fmt: skip
                business.add("intereses", -offer.fee)
                self.repository.save_in(connection, guild_id, business)
                signed.append((loan, offer))
                move = BusinessMove(
                    user_id=user_id,
                    account=_account(business),
                    bank_loan=principal,
                    payments=(BusinessCost(f"{BANK_LOAN_CONCEPT}apertura", gone=offer.fee),),
                )
                return business, move

            try:
                business, paid = await self.economy.business(guild_id, prepare)
            except HousingError as error:
                raise BusinessError(str(error)) from error
            except BalanceLimitError as error:
                raise BusinessError("No cabe más dinero en la caja del negocio.") from error
            loan, offer = signed[0]
            return business, loan, offer, paid

    async def repay_loan(
        self, guild_id: int, user_id: int, loan_id: int, *, biz: int | None = None
    ) -> tuple[Business, Loan, int, BusinessPaid]:
        """Devuelve entero un préstamo del negocio desde su caja, con la compensación.

        Tratamiento fiscal: lo devuelto no es gasto; la compensación por amortizar antes
        (`loans.early_fee`), sí, como los intereses.

        Returns:
            `(el negocio, el préstamo como estaba, la compensación, el dinero)`.

        Raises:
            BusinessError: Si no es de su negocio, ya está cerrado o la caja no llega.
        """
        housing = self.housing
        if housing is None:
            raise BusinessError("El banco está cerrado ahora mismo.")
        async with self._lock(guild_id, user_id):
            now = self._clock()
            today, week = self._today(now)
            closed: list[tuple[Loan, int]] = []

            def prepare(connection: sqlite3.Connection) -> tuple[Business, BusinessMove]:
                business = self.repository.business_in(connection, guild_id, user_id, biz)
                if business is None:
                    raise BusinessError("No tienes negocio.")
                business.roll_over(today, week)
                loan, fee = housing.repay_business_loan_in(
                    connection, guild_id, business.id, loan_id, now
                )
                business.add("intereses", -fee)
                self.repository.save_in(connection, guild_id, business)
                closed.append((loan, fee))
                move = BusinessMove(
                    user_id=user_id,
                    account=_account(business),
                    payments=(
                        BusinessCost(
                            f"{BANK_LOAN_CONCEPT}amortizacion", gone=loan.owed, expense=False
                        ),
                        BusinessCost(f"{BANK_LOAN_CONCEPT}compensacion", gone=fee),
                    ),
                )
                return business, move

            try:
                business, paid = await self.economy.business(guild_id, prepare)
            except HousingError as error:
                raise BusinessError(str(error)) from error
            except InsufficientFundsError as error:
                # `prepare` ya corrió (y se deshizo): dice cuánto era.
                loan, fee = closed[0]
                whose = "La caja de la sociedad no llega" if loan.corporate else "No te llega"
                raise BusinessError(f"{whose}: son {loan.owed + fee} Y$.") from error
            loan, fee = closed[0]
            return business, loan, fee, paid

    # -- Fase 4: la plantilla -----------------------------------------------------------

    def candidates(self, business: Business) -> list[dict[str, Any]]:
        """Los candidatos de hoy para `business` (siempre los mismos ese día)."""
        return staff.candidates(business, local_day(self._clock()))

    def dismissal_options(self, business: Business, employee: dict[str, Any]) -> list[Dismissal]:
        """Las formas de despedir hoy a `employee`, con lo que cuesta cada una."""
        return staff.dismissals(
            employee, local_day(self._clock()), losses=sl.tax_base(business.last_week) < 0
        )

    def payroll_tonight(self, business: Business) -> Payroll:
        """Las nóminas de esta noche."""
        return staff.payroll(business, local_day(self._clock()))

    async def _bonified(self, guild_id: int, user_id: int, business: Business, day: date) -> bool:
        """Si una contratación de hoy puede llevar bonificación: al corriente con Hacienda
        (art. 8 del RDL 1/2023) y sin la exclusión por un despido improcedente (art. 11.4)."""
        if business.bonus_ban >= day.isoformat():
            return False
        if business.is_company:
            return not business.company.get("tax_debt", 0)
        return await self.economy.tax_debt(guild_id, user_id) <= 0

    async def hire(
        self,
        guild_id: int,
        user_id: int,
        key: int,
        *,
        contract: str,
        jornada: int,
        shift: str,
        wage: int | None = None,
        biz: int | None = None,
        role: str = "",
        horario: str | None = None,
    ) -> tuple[Business, dict[str, Any]]:
        """Contrata a un candidato de hoy. No mueve dinero: la nómina se paga cada noche.

        `role` es su puesto (`business_staff.ROLES`, 9.41.0): el dependiente (`""`), el
        encargado de turno, el repartidor, el gerente o uno de apoyo (limpieza, mozo de
        almacén, community manager, recursos humanos). Todos menos el dependiente son uno por
        local, y todos menos el gerente ocupan uno de sus puestos (`business_staff.cap`).
        Cada puesto pide lo suyo (`business_staff.role_ask`). El gerente (fase 5) va con alta
        y a jornada completa; no atiende, pero cada noche repone y cubre las bajas
        (`_manage`).

        `horario` es la clave de uno de `business_staff.HORARIOS`; sin él, el de antes de la
        9.41.0 por su `jornada` y su `shift`. Empieza ya, en las franjas de su horario que aún
        no han acabado (el alta va antes de empezar, art. 139 LGSS). Con contrato `indefinido`
        o `temporal` (art. 15.2 ET, 6 meses: `business_staff.TEMPORARY_NIGHTS` noches), los
        dos con período de prueba; o en `b`, sin alta ni contrato.

        Args:
            wage: El bruto de cada noche que se le ofrece (por defecto, lo que pide).

        Raises:
            BusinessError: Sin negocio, sin sitio en el local o con el puesto ya cubierto, si
                el candidato ya no está o si no acepta lo ofrecido.
        """
        if contract not in ("indefinido", "temporal", "b"):
            raise BusinessError("Ese contrato no existe.")
        info = staff.ROLE_BY_KEY.get(role)
        if info is None:
            raise BusinessError("Ese puesto no existe.")
        manager = role == staff.MANAGER
        franjas: tuple[int, ...] | None = None
        if manager:
            if contract == "b":
                raise BusinessError(
                    "El gerente compra en nombre del negocio: tiene que estar dado de alta."
                )
            jornada = staff.FULL_DAY
        elif horario is not None:
            chosen = staff.HORARIO_BY_KEY.get(horario)
            if chosen is None:
                raise BusinessError("Ese horario no está en la lista.")
            franjas, jornada, shift = chosen.franjas, chosen.jornada, chosen.shift
        elif jornada not in (staff.FULL_DAY, staff.HALF_DAY):
            # Sin horario, los cuatro de antes de la 9.41.0: completa o media, mañana o tarde.
            raise BusinessError("La jornada es completa o media.")
        if shift not in staff.SHIFTS:
            raise BusinessError("El turno es de mañana o de tarde.")
        async with self._lock(guild_id, user_id):
            now = self._clock()
            today, week = self._today(now)
            business = await self.repository.business(guild_id, user_id, biz)
            if business is None:
                raise BusinessError("No tienes negocio.")
            business.roll_over(today, week)
            problem = staff.hire_problem(business, role)
            if problem is not None:
                raise BusinessError(problem)
            candidate = next(
                (c for c in staff.candidates(business, today) if c["key"] == key), None
            )
            if candidate is None:
                raise BusinessError("Ese candidato ya no está: mira los de hoy.")
            name = candidate["name"].split()[0]
            if candidate["part_time"] and jornada != staff.HALF_DAY:
                raise BusinessError(
                    f"{name} solo quiere media jornada"
                    + (": de gerente no le cuadra." if manager else ".")
                )
            asked = staff.role_ask(candidate["skills"], candidate["traits"], jornada, role)
            pay = asked if wage is None else wage
            if pay < asked:
                raise BusinessError(f"{name} pide al menos {asked} Y$ brutos cada noche.")
            bonified = contract == "indefinido" and await self._bonified(
                guild_id, user_id, business, today
            )
            business.staff_seq += 1
            employee = staff.new_employee(
                candidate, seq=business.staff_seq, contract=contract, jornada=jornada,
                shift=shift, wage=pay, start=today, bonified=bonified, role=role,
                franjas=franjas,
            )  # fmt: skip
            # Las franjas de hoy que ya han acabado no las trabaja.
            employee["from_franja"] = staff.franjas_done(datetime.fromtimestamp(now, TIMEZONE).hour)
            business.staff = [*business.staff, employee]
            business.seen = [*business.seen, key][-30:]
            await self.repository.save(guild_id, business)
            return business, employee

    def _employee(self, business: Business, employee_id: int) -> dict[str, Any]:
        found = next((e for e in business.staff if e["id"] == employee_id), None)
        if found is None:
            raise BusinessError("Esa persona ya no trabaja contigo.")
        return found

    async def _staff_change(
        self,
        guild_id: int,
        user_id: int,
        change: Callable[[Business], Any],
        biz: int | None = None,
    ) -> tuple[Business, Any]:
        """Cambia la plantilla sin mover dinero (`change` lanza `BusinessError` si no puede)."""
        async with self._lock(guild_id, user_id):
            business = await self.business(guild_id, user_id, biz)
            if business is None:
                raise BusinessError("No tienes negocio.")
            result = change(business)
            await self.repository.save(guild_id, business)
            return business, result

    async def raise_wage(
        self,
        guild_id: int,
        user_id: int,
        employee_id: int,
        share: float,
        *,
        biz: int | None = None,
    ) -> tuple[Business, dict[str, Any]]:
        """Le sube el sueldo un `share` (de `business_staff.RAISES`): le sube el humor.

        Bajarlo no se puede: sería una modificación sustancial de las condiciones (art. 41 ET),
        que pide causas que el juego no tiene.
        """
        if share not in staff.RAISES:
            raise BusinessError("Esa subida no está en la lista.")

        def change(business: Business) -> dict[str, Any]:
            employee = self._employee(business, employee_id)
            employee["wage"] = int(round(employee["wage"] * (1 + share), -1))
            staff.mood_change(employee, min(15.0, share * 100))
            return employee

        return await self._staff_change(guild_id, user_id, change, biz)

    async def reschedule(
        self,
        guild_id: int,
        user_id: int,
        employee_id: int,
        horario: str,
        *,
        biz: int | None = None,
    ) -> tuple[Business, dict[str, Any], staff.Reschedule]:
        """Le cambia el horario desde el día siguiente (`business_staff.reschedule`). No mueve
        dinero: la nómina de esta noche es la de siempre y la nueva, la de su horario nuevo.

        Con las mismas horas es una modificación sustancial (art. 41 ET) y le baja algo el
        humor; con más horas cobra más; con menos tiene que aceptar (art. 12.4.e ET).

        Raises:
            StaffRefusedError: Si no acepta trabajar menos horas.
            BusinessError: Si ya no trabaja contigo o el cambio no vale (`ValueError` de
                `business_staff.reschedule`).
        """
        today = local_day(self._clock())

        def change(business: Business) -> tuple[dict[str, Any], staff.Reschedule]:
            employee = self._employee(business, employee_id)
            try:
                plan = staff.reschedule(employee, horario, today)
            except staff.Refusal as error:
                raise StaffRefusedError(str(error)) from error
            except ValueError as error:
                raise BusinessError(str(error)) from error
            staff.plan_reschedule(employee, plan)
            return employee, plan

        business, (employee, plan) = await self._staff_change(guild_id, user_id, change, biz)
        return business, employee, plan

    async def make_permanent(
        self, guild_id: int, user_id: int, employee_id: int, *, biz: int | None = None
    ) -> tuple[Business, dict[str, Any]]:
        """Hace fijo a quien tiene un contrato temporal: ya no se acaba (ni cobra los 12 días)."""

        def change(business: Business) -> dict[str, Any]:
            employee = self._employee(business, employee_id)
            if employee["contract"] != "temporal":
                raise BusinessError("Solo se hace fijo a quien tiene un contrato temporal.")
            employee["contract"] = "indefinido"
            employee["until"] = ""
            staff.mood_change(employee, 10.0)
            return employee

        return await self._staff_change(guild_id, user_id, change, biz)

    async def train(
        self,
        guild_id: int,
        user_id: int,
        employee_id: int,
        skill: str,
        *,
        biz: int | None = None,
    ) -> tuple[Business, dict[str, Any], int, int]:
        """Le paga un curso de `skill`: la noche siguiente sube `COURSE_POINTS`.

        Tratamiento fiscal: lo cobra una academia con IGIC (al Estado); la base desaparece.
        Es gasto de personal deducible (art. 28.1 LIRPF o LIS). El crédito de formación de
        FUNDAE de la semana (`business_staff.fundae_week`) se descuenta de la base: en la vida
        real se bonifica en los seguros sociales. A quien está en B no le llega: no está de
        alta.

        Returns:
            `(el negocio, el empleado, lo pagado, lo bonificado por FUNDAE)`.
        """
        if skill not in staff.SKILLS:
            raise BusinessError("Ese curso no existe.")
        async with self._lock(guild_id, user_id):
            now = self._clock()
            today, week = self._today(now)
            current = await self.repository.business(guild_id, user_id, biz)
            if current is None:
                raise BusinessError("No tienes negocio.")
            held: list[tuple[dict[str, Any], int, int]] = []

            def prepare(connection: sqlite3.Connection) -> tuple[Business, BusinessMove]:
                business = self.repository.business_in(connection, guild_id, user_id, current.id)
                if business is None:
                    raise BusinessError("No tienes negocio.")
                business.roll_over(today, week)
                employee = self._employee(business, employee_id)
                if employee["course"]:
                    raise BusinessError(f"{employee['name']} ya está con un curso.")
                if business.fundae_week != week:
                    business.fundae_week, business.fundae_used = week, 0
                credit = 0
                if employee["contract"] != "b":
                    credit = min(staff.COURSE_COST, staff.fundae_week() - business.fundae_used)
                    business.fundae_used += credit
                base = staff.COURSE_COST - credit
                tax = igic(staff.COURSE_COST)
                employee["course"] = skill
                staff.mood_change(employee, 8.0)
                business.add("formacion", -(base + tax))
                self.repository.save_in(connection, guild_id, business)
                held.append((employee, base + tax, credit))
                cost = BusinessCost("negocio:formacion", gone=base, taxes=(("igic", tax),))
                return business, BusinessMove(
                    user_id=user_id, payments=(cost,), account=_account(business)
                )

            try:
                business, _paid = await self.economy.business(guild_id, prepare)
            except InsufficientFundsError as error:
                total = staff.COURSE_COST + igic(staff.COURSE_COST)
                raise BusinessError(_broke(current, total)) from error
            employee, paid, credit = held[0]
            return business, employee, paid, credit

    async def dismiss(
        self,
        guild_id: int,
        user_id: int,
        employee_id: int,
        kind: str,
        *,
        biz: int | None = None,
    ) -> tuple[Business, dict[str, Any], Dismissal, int]:
        """Despide a un empleado de la forma `kind` (`business_staff.dismissals`).

        Tratamiento fiscal: la indemnización es gasto de personal deducible y al empleado le
        está exenta (art. 7.e LIRPF) y no cotiza (art. 147.2.c LGSS); se paga entera o no se
        despide. Lo que se le debía de sueldo sigue en los atrasos de cada noche. Un despido
        improcedente de alguien bonificado obliga a devolver lo bonificado con recargo (art. 13
        del RDL 1/2023, a la Seguridad Social) y deja 12 meses sin bonificaciones (art. 11.4).

        Returns:
            `(el negocio, el empleado, el despido, lo devuelto de la bonificación)`.
        """
        async with self._lock(guild_id, user_id):
            now = self._clock()
            today, week = self._today(now)
            current = await self.repository.business(guild_id, user_id, biz)
            if current is None:
                raise BusinessError("No tienes negocio.")
            held: list[tuple[dict[str, Any], Dismissal, int]] = []

            def prepare(connection: sqlite3.Connection) -> tuple[Business, BusinessMove]:
                business = self.repository.business_in(connection, guild_id, user_id, current.id)
                if business is None:
                    raise BusinessError("No tienes negocio.")
                business.roll_over(today, week)
                employee = self._employee(business, employee_id)
                options = {
                    d.kind: d
                    for d in staff.dismissals(
                        employee, today, losses=sl.tax_base(business.last_week) < 0
                    )
                }
                choice = options.get(kind)
                if choice is None:
                    raise BusinessError("Ese despido no vale para esta persona.")
                if not choice.allowed:
                    raise BusinessError(f"No puedes: {choice.why}")
                refund = staff.bonus_refund(employee, today) if kind == "improcedente" else 0
                if refund:
                    business.bonus_ban = (
                        today + timedelta(days=staff.BONUS_BAN_NIGHTS)
                    ).isoformat()
                    business.add("multas", -refund)
                business.staff = [e for e in business.staff if e["id"] != employee_id]
                if choice.amount:
                    business.add("despidos", -choice.amount)
                self.repository.save_in(connection, guild_id, business)
                held.append((employee, choice, refund))
                payments = []
                if choice.amount:
                    payments.append(BusinessCost("negocio:despido", gone=choice.amount))
                if refund:
                    payments.append(
                        BusinessCost(
                            "negocio:bonificacion:reintegro",
                            taxes=(("cotizacion", refund),),
                            expense=False,
                        )
                    )
                return business, BusinessMove(
                    user_id=user_id, payments=tuple(payments), account=_account(business)
                )

            try:
                business, _paid = await self.economy.business(guild_id, prepare)
            except InsufficientFundsError as error:
                _employee, choice, refund = held[0]
                raise BusinessError(_broke(current, choice.amount + refund)) from error
            employee, choice, refund = held[0]
            return business, employee, choice, refund

    async def staff_dues(self, guild_id: int, user_id: int) -> tuple[int, int]:
        """`(nóminas de esta noche, atrasos con la plantilla)` de un autónomo, de todos sus
        locales (para `saldo`).

        Solo lee. Las de una sociedad las paga su caja: `(0, 0)`.
        """
        tonight = owed = 0
        for business in await self.repository.owned(guild_id, user_id):
            if business.is_company:
                continue
            pay = self.payroll_tonight(business)
            tonight += pay.cost + pay.black
            owed += business.wage_debt
        return tonight, owed

    async def staff_turns(
        self, guild_id: int, *, skip: Callable[[int], bool] | None = None
    ) -> list[StaffTurn]:
        """Juega las franjas de hoy que ya han acabado en los negocios con plantilla.

        Cada franja va en su propia transacción. `skip` dice de qué dueños no tocar el negocio
        ahora (los que atienden un turno: el stock es el mismo); se juega en la siguiente
        pasada. Si un negocio falla, se sigue con el resto.
        """
        now = self._clock()
        today = local_day(now)
        upto = staff.franjas_done(datetime.fromtimestamp(now, TIMEZONE).hour)
        played: list[StaffTurn] = []
        if not upto:
            return played
        for business in await self.repository.businesses(guild_id):
            if not business.staff or (skip is not None and skip(business.user_id)):
                continue
            try:
                async with self._lock(guild_id, business.user_id):
                    played.extend(
                        await self._staff_franjas(
                            guild_id, business.user_id, today, upto, business.id
                        )
                    )
            except (OSError, sqlite3.Error):
                logger.exception("No se pudieron jugar los turnos de la plantilla %s", business.id)
        return played

    async def _staff_franjas(
        self, guild_id: int, user_id: int, day: date, upto: int, biz: int
    ) -> list[StaffTurn]:
        """Juega las franjas de la plantilla del local `biz` de `day` que falten, hasta `upto`
        (sin incluirla).

        El candado lo tiene quien llama. Una franja en la que no trabaja nadie no abre
        transacción.
        """
        business = await self.repository.business(guild_id, user_id, biz)
        if business is None or not business.staff:
            return []
        iso = day.isoformat()
        if business.staff_day > iso:
            return []
        first = business.staff_done if business.staff_day == iso else 0
        played: list[StaffTurn] = []
        for franja in range(first, upto):
            if not any(staff.works(e, iso, franja) for e in business.staff):
                continue
            turns = await self._staff_franja(guild_id, user_id, day, franja, biz)
            if turns is None:
                break
            played.extend(turns)
        return played

    async def _staff_franja(
        self, guild_id: int, user_id: int, day: date, franja: int, biz: int
    ) -> list[StaffTurn] | None:
        """Una franja de la plantilla: cada uno en su sitio juega su turno con el minijuego.

        Las ventas entran en la caja como las del dueño (con su retención si es autónomo) y
        los clientes salen del mismo cupo del barrio; los del repartidor, del cupo a domicilio
        (`business_staff.DELIVERY_POOL`), sin listillos. Con el encargado de turno en la
        franja, los demás del mostrador fallan menos (`business_staff.LEAD_MISS`); con
        recursos humanos, aprenden más. La primera franja del día decide también quién de la
        plantilla de apoyo ha faltado. `None` si el negocio ya no existe.
        """
        holder: list[StaffTurn] = []
        iso = day.isoformat()
        today = local_day(self._clock())
        start = datetime(
            day.year, day.month, day.day, staff.franja_hour(franja), tzinfo=TIMEZONE
        ).timestamp()

        def prepare(connection: sqlite3.Connection) -> tuple[Business | None, BusinessMove]:
            business = self.repository.business_in(connection, guild_id, user_id, biz)
            if business is None:
                return None, BusinessMove(user_id=user_id)
            if business.staff_day > iso or (
                business.staff_day == iso and business.staff_done > franja
            ):
                return business, BusinessMove(user_id=user_id)
            if business.staff_day != iso:
                business.staff_day, business.staff_done = iso, 0
            if day == today:
                business.roll_over(day, week_of(day))
            pool_left = daily_pool(business.sector_info, business.barrio_info) * pool_factor(
                business, day
            ) - self.repository.pool_used_in(
                connection, guild_id, business.barrio, business.sector, day
            )
            delivery = staff.delivery_key(business.sector)
            delivery_left = daily_pool(
                business.sector_info, business.barrio_info
            ) * staff.DELIVERY_POOL * (1 + boost(business, day)) - self.repository.pool_used_in(
                connection, guild_id, business.barrio, delivery, day
            )
            # Las faltas de hoy: de quien juega esta franja y, con la primera, de la plantilla
            # de apoyo (lo que deja RR. HH. ya cuenta hoy). El gerente, por la noche.
            for employee in business.staff:
                playing = staff.works(employee, iso, franja)
                if not playing and not (staff.role_of(employee).effects):
                    continue
                if staff.roll_absence(employee, business.seed, day):
                    business.tell(
                        f"🚫 {employee['name']} no vino hoy y no avisó: es su falta número "
                        f"{employee['absences']}. Hoy no cobra."
                    )
                    holder.append(
                        StaffTurn(
                            user_id, employee["name"], employee["id"], day, franja,
                            absent=True, role=employee.get("role", ""),
                        )
                    )  # fmt: skip
            helped = staff.support(business, day)
            boss = next(
                (
                    e
                    for e in business.staff
                    if e.get("role") == "encargado"
                    and staff.works(e, iso, franja)
                    and e["absent_day"] != iso
                ),
                None,
            )
            sales = sold = 0
            for employee in business.staff:
                if not staff.works(employee, iso, franja) or employee["absent_day"] == iso:
                    continue
                name = employee["name"]
                role = staff.role_of(employee)
                away = role.plays == "reparto"
                led = boss is not None and employee is not boss and not away
                rng = random.Random(f"{business.seed}:plantilla:{iso}:{franja}:{employee['id']}")
                wanted = turn_clients(business, staff.demand_hour(franja), rng.random(), day)
                if away:
                    wanted = max(1, round(wanted * staff.DELIVERY_TURN))
                left = delivery_left if away else pool_left
                clients = make_clients(
                    business, min(wanted, math.floor(left)), rng.randrange(2**31),
                    thieves=not away,
                )  # fmt: skip
                if not clients:
                    holder.append(
                        StaffTurn(user_id, name, employee["id"], day, franja, role=role.key)
                    )
                    continue
                if away:
                    delivery_left -= len(clients)
                else:
                    pool_left -= len(clients)
                self.repository.take_pool_in(
                    connection, guild_id, business.barrio, delivery if away else business.sector,
                    day, len(clients),
                )  # fmt: skip
                game = staff.play_turn(
                    business, employee, clients, start=start, franja=franja,
                    lead=staff.lead(boss) if led and boss is not None else 0.0,
                )  # fmt: skip
                summary = game.summary()
                _apply_turn(business, summary, f"{start}:{employee['id']}", day)
                staff.learn(employee, boost=helped.learn)
                if led:
                    employee["led_day"] = iso
                if employee["grades_day"] != iso:
                    employee["grades_day"], employee["grades"] = iso, []
                employee["grades"].append(summary.grade)
                employee["turns"] += 1
                employee["clients"] += summary.served
                employee["sales"] += summary.sales
                employee["best"] = max(employee["best"], summary.sales)
                if day == today:
                    business.sales_today += summary.sales
                    business.clients_today += summary.served
                    for key, value in (
                        ("turns", 1), ("clients", summary.served), ("sales", summary.sales)
                    ):  # fmt: skip
                        business.staff_today[key] = business.staff_today.get(key, 0) + value
                sales += summary.sales
                sold += summary.sold_cost
                holder.append(
                    StaffTurn(
                        user_id, name, employee["id"], day, franja, summary, role=role.key,
                        led=led,
                    )
                )  # fmt: skip
            business.staff_done = franja + 1
            self.repository.save_in(connection, guild_id, business)
            return business, BusinessMove(
                user_id=user_id, sales=sales, sold_cost=sold, account=_account(business)
            )

        business, paid = await self.economy.business(guild_id, prepare)
        if business is None:
            return None
        if paid.withheld:
            business.add("irpf", -paid.withheld)
            await self.repository.save(guild_id, business)
        return holder

    # -- Comprar ------------------------------------------------------------------------

    async def buy(
        self, guild_id: int, user_id: int, product: str, lots: int, *, biz: int | None = None
    ) -> tuple[Business, int, int, int]:
        """Compra `lots` lotes de `product` al mayorista.

        Returns:
            `(el negocio, base, IGIC, saldo final)`.

        Raises:
            BusinessError: Sin negocio, con algo que no vende o si no le llega.
        """
        if not 1 <= lots <= MAX_LOTS:
            raise BusinessError(f"Se compran de 1 a {MAX_LOTS} lotes de una vez.")
        async with self._lock(guild_id, user_id):
            now = self._clock()
            today, week = self._today(now)
            current = await self.repository.business(guild_id, user_id, biz)
            if current is None:
                raise BusinessError("No tienes negocio.")
            item = current.sector_info.product(product)
            if item is None:
                raise BusinessError("Eso no se vende en tu negocio.")
            base, tax = wholesale(item, item.pack * lots)

            def prepare(connection: sqlite3.Connection) -> tuple[Business, BusinessMove]:
                business = self.repository.business_in(connection, guild_id, user_id, current.id)
                if business is None:
                    raise BusinessError("Tu negocio ya no existe.")
                business.roll_over(today, week)
                business.stock[product] = business.stock.get(product, 0) + item.pack * lots
                business.add("compras", -(base + tax))
                self.repository.save_in(connection, guild_id, business)
                taxes = (("igic", tax),) if tax else ()
                cost = BusinessCost("negocio:compras", gone=base, taxes=taxes)
                move = BusinessMove(user_id=user_id, payments=(cost,), account=_account(business))
                return business, move

            try:
                business, paid = await self.economy.business(guild_id, prepare)
            except InsufficientFundsError as error:
                raise BusinessError(_broke(current, base + tax)) from error
            return business, base, tax, paid.company if business.is_company else paid.balance

    # -- Un turno -----------------------------------------------------------------------

    def plan(self) -> TurnPlan:
        """Sortea el azar del próximo turno (lo gasta `start_turn`)."""
        return TurnPlan(self.rng.random(), self.rng.randrange(2**31))

    def preview(self, status: BusinessStatus, plan: TurnPlan) -> TurnGame | None:
        """El turno que saldría abriendo ahora con `plan`, sin guardar nada.

        `None` si ahora no se puede abrir. Es lo que pinta el panel antes del clic.
        """
        if status.blocker is not None:
            return None
        clients = self._queue(status, plan)
        if not clients:
            return None
        return TurnGame(status.business, clients, now=status.now, tired=status.battery < TIRED)

    @staticmethod
    def _queue(status: BusinessStatus, plan: TurnPlan) -> list[Client]:
        """La cola de un turno que empieza en el momento de `status`."""
        business = status.business
        hour = datetime.fromtimestamp(status.now, TIMEZONE).hour
        wanted = turn_clients(business, hour, plan.luck, local_day(status.now))
        count = min(wanted, math.floor(status.pool_left))
        return make_clients(business, count, plan.seed)

    async def start_turn(
        self,
        guild_id: int,
        user_id: int,
        plan: TurnPlan | None = None,
        *,
        biz: int | None = None,
    ) -> Turn:
        """Abre un turno: gasta una franja y batería, y sortea la cola de clientes.

        Con `plan` (el que usó el panel para pintar el primer cliente) la cola sale de ese
        azar; sin él, se sortea ahora. No mueve dinero (se cobra al acabar, `finish_turn`).
        La franja y el cupo del barrio se apuntan ya: abrir es abrir, aunque luego no venga
        nadie más.

        Raises:
            BusinessError: Sin negocio o si algo impide abrir (`BusinessStatus.blocker`).
        """
        async with self._lock(guild_id, user_id):
            status = await self.status(guild_id, user_id, biz)
            if status is None:
                raise BusinessError("No tienes negocio. Móntalo desde `pala`.")
            if (blocker := status.blocker) is not None:
                raise BusinessError(blocker)
            business, now = status.business, status.now
            clients = self._queue(status, plan or self.plan())
            if not clients:
                raise BusinessError("No tienes nada que vender: pasa por el 📦 almacén.")
            today = local_day(now)
            business.turns_today += 1
            business.last_turn_at = now
            if business.last_open_day != today.isoformat():
                yesterday = (today - timedelta(days=1)).isoformat()
                business.open_streak = (
                    business.open_streak + 1 if business.last_open_day == yesterday else 1
                )
                business.last_open_day = today.isoformat()
            await self.repository.begin_turn(guild_id, business, today, len(clients))
            before = await self.work.spend_battery(guild_id, user_id, SHIFT_COST)
            game = TurnGame(business, clients, now=now, tired=before < TIRED)
            return Turn(business=business, game=game, battery_before=before)

    async def finish_turn(self, guild_id: int, user_id: int, turn: Turn) -> TurnResult:
        """Cierra un turno: cobra las ventas y guarda el stock, la reputación y los habituales.

        Raises:
            BusinessError: Si el negocio ya no existe (cerró o lo desahuciaron a mitad).
        """
        async with self._lock(guild_id, user_id):
            now = self._clock()
            game = turn.game
            if not game.finished(now):
                game.finish(now)
            summary = game.summary()
            today, week = self._today(now)
            stars_before = turn.business.stars
            best: list[bool] = []
            reviews: list[tuple[str, int, int]] = []

            def prepare(connection: sqlite3.Connection) -> tuple[Business, BusinessMove]:
                business = self.repository.business_in(
                    connection, guild_id, user_id, turn.business.id
                )
                if business is None:
                    raise BusinessError("Tu negocio ya no existe: este turno no cuenta.")
                business.roll_over(today, week)
                reviews.extend(_apply_turn(business, summary, f"{game.started}", today))
                business.sales_today += summary.sales
                business.clients_today += summary.served
                best.append(summary.sales > business.best_turn)
                business.best_turn = max(business.best_turn, summary.sales)
                self.repository.save_in(connection, guild_id, business)
                move = BusinessMove(
                    user_id=user_id,
                    sales=summary.sales,
                    sold_cost=summary.sold_cost,
                    account=_account(business),
                )
                return business, move

            business, paid = await self.economy.business(guild_id, prepare)
            if paid.withheld:
                business.add("irpf", -paid.withheld)
                await self.repository.save(guild_id, business)
            started = datetime.fromtimestamp(game.started, TIMEZONE)
            return TurnResult(
                business=business,
                summary=summary,
                withheld=paid.withheld,
                garnished=paid.garnished,
                balance=paid.balance,
                stars_before=stars_before,
                best=bool(best and best[0]),
                hour=started.hour,
                weekday=started.weekday(),
                day=started.date().isoformat(),
                bank_garnished=paid.bank_garnished,
                reviews=tuple(reviews),
            )

    # -- Las noches y la semana ---------------------------------------------------------

    async def close_nights(self, guild_id: int) -> list[NightRun]:
        """Cierra las noches que falten de los negocios del servidor y cobra la cuota.

        Va dueño a dueño (`_close_owner_nights`). Cada noche de cada local va en su propia
        transacción. Si un dueño falla, se sigue con el resto.
        """
        runs: list[NightRun] = []
        owners: dict[int, None] = {}
        for business in await self.repository.businesses(guild_id):
            owners.setdefault(business.user_id)
        for user_id in owners:
            try:
                runs.extend(await self._close_owner_nights(guild_id, user_id))
            except (OSError, sqlite3.Error):
                logger.exception("No se pudieron cerrar las noches de los negocios de %s", user_id)
        return runs

    async def _close_owner_nights(self, guild_id: int, user_id: int) -> list[NightRun]:
        """Las noches que falten de los locales de un dueño, noche a noche.

        Cada noche cierra sus locales del más antiguo al más nuevo, así el último de una
        sociedad ve ya apuntado lo de los demás (Sociedades, `_close_night`). Antes de cerrar
        un local, su plantilla acaba las franjas de ese día. Al final, la cuota de la semana.
        """
        runs: list[NightRun] = []
        async with self._lock(guild_id, user_id):
            now = self._clock()
            today = local_day(now)
            owned = await self.repository.owned(guild_id, user_id)
            if not owned:
                return runs
            first = min(
                date.fromisoformat(b.billed_day) if b.billed_day else today - timedelta(days=1)
                for b in owned
            )
            day = max(first + timedelta(days=1), today - timedelta(days=CATCH_UP_NIGHTS))
            while day < today:
                tonight: list[NightRun] = []
                # La nave antes que los locales: el domingo, Sociedades ya ve lo suyo.
                nave_events = await self._close_nave_night(guild_id, user_id, day, now)
                for business in await self.repository.owned(guild_id, user_id):
                    if business.billed_day >= day.isoformat():
                        continue
                    turns = await self._staff_franjas(
                        guild_id, user_id, day, FRANJAS_DIA, business.id
                    )
                    run = await self._close_night(guild_id, user_id, business.id, day, now)
                    if run is None:
                        continue
                    done = await self._manage(guild_id, user_id, business.id, day, now)
                    tonight.append(replace(run, turns=tuple(turns), manager=tuple(done)))
                if tonight and nave_events:
                    tonight[0] = replace(tonight[0], nave=tuple(nave_events))
                if (
                    tonight
                    and day.weekday() == 6
                    and (back := await self._pluriactivity(guild_id, user_id, day))
                ):
                    tonight[-1] = replace(tonight[-1], refund=back)
                runs.extend(tonight)
                day += timedelta(days=1)
            fresh = await self.repository.owned(guild_id, user_id)
        if fresh:
            await self._charge_fee(guild_id, fresh[0], now)
        return runs

    async def _pluriactivity(self, guild_id: int, user_id: int, sunday: date) -> int:
        """El reintegro de la semana que acaba en `sunday` a quien cotizó por los dos lados.

        Tratamiento fiscal: el art. 313 LGSS (con el RDL 3/2026) devuelve el 50 % de lo que
        lo cotizado por cuenta ajena y en el RETA pase de 17.323,68 € al año, con el tope del
        50 % de lo pagado al RETA. Lo paga el Estado. Simplificación: no se apunta como renta
        (en la vida real reduce el gasto de la cuota que se dedujo). Una vez por semana.
        """
        owned = await self.repository.owned(guild_id, user_id)
        week = week_of(sunday)
        # Es de la persona: una vez por semana aunque tenga varios locales.
        if not owned or any(b.pluri_week >= week for b in owned):
            return 0
        for business in owned:
            business.pluri_week = week
            await self.repository.save(guild_id, business)
        start = datetime.combine(date.fromisoformat(week), datetime.min.time(), TIMEZONE)
        end = start + timedelta(days=7)
        general, reta = await self.economy.contributions(
            guild_id, user_id, start.timestamp(), end.timestamp()
        )
        refund = sl.pluriactivity_refund(general, reta)
        if refund:
            await self.economy.refund_contributions(guild_id, user_id, amount=refund)
            fresh = await self.repository.business(guild_id, user_id)
            if fresh is not None:
                fresh.tell(
                    f"💶 La Seguridad Social te devuelve {refund} Y$: cotizaste de más por la "
                    "pluriactividad (art. 313 LGSS)."
                )
                await self.repository.save(guild_id, fresh)
        return refund

    async def _close_night(
        self, guild_id: int, user_id: int, business_id: int, day: date, now: float
    ) -> NightRun | None:
        """Cobra una noche de un negocio y, si toca, lo desahucia. `None` si ya no existe.

        Los recibos van por orden y lo que no llega es de los últimos: lo que se debe a la
        plantilla y sus nóminas (los salarios, primero, art. 32 ET), lo que la sociedad debe a
        Hacienda, el local (su deuda, el alquiler, los suministros, la terraza y la gestoría),
        los seguros sociales y las retenciones de la plantilla, la Inspección, Sociedades y las
        cuotas del banco.

        Una sociedad con varios locales (fase 5) paga la gestoría y, el domingo, Sociedades una
        sola vez: en la noche del último de sus locales que cierra (`_close_owner_nights` los
        cierra del más antiguo al más nuevo), con lo que ya apuntaron los demás.
        """
        holder: list[NightRun] = []
        evict: list[str] = []
        #: Si este local es el último de su sociedad que cierra esta noche.
        leads: list[bool] = []

        def prepare(connection: sqlite3.Connection) -> tuple[Business | None, BusinessMove]:
            business = self.repository.business_in(connection, guild_id, user_id, business_id)
            if business is None or business.billed_day >= day.isoformat():
                return None, BusinessMove(user_id=user_id)
            _roll_support(business, day)
            helped = staff.support(business, day)
            helpers = tuple(
                e["role"]
                for e in business.staff
                if staff.role_of(e).effects and staff.present(e, day)
            )
            bill = nightly_bill(business)
            debt = business.rent_debt
            company = business.is_company
            siblings = (
                [
                    b
                    for b in self.repository.owned_in(connection, guild_id, user_id)
                    if b.company_id == business.company_id and b.id != business.id
                ]
                if company
                else []
            )
            # El último de la sociedad que cierra esta noche: los que faltan son los de más id.
            lead = company and not any(
                b.id > business.id and b.billed_day < day.isoformat() for b in siblings
            )
            leads.append(lead)
            pay = staff.payroll(business, day)
            groups: list[tuple[str, BusinessCost]] = []
            owed_staff = business.wage_debt
            if owed_staff:
                groups.append(
                    ("atrasos", BusinessCost("negocio:plantilla:atrasos", gone=owed_staff))
                )
            if pay.net:
                groups.append(("nominas", BusinessCost("negocio:nominas", gone=pay.net)))
            if pay.black:
                groups.append(
                    ("negro", BusinessCost("negocio:negro", gone=pay.black, expense=False))
                )
            old_tax = business.company.get("tax_debt", 0) if company else 0
            if old_tax:
                # Lo que la sociedad ya debía a Hacienda, antes que el local.
                groups.append(
                    (
                        "hacienda",
                        BusinessCost(
                            "sociedad:hacienda:deuda",
                            taxes=(("sociedades", old_tax),),
                            expense=False,
                        ),
                    )
                )
            if debt:
                groups.append(("local", BusinessCost("negocio:deuda_local", gone=debt)))
            groups.append(
                (
                    "local",
                    BusinessCost(
                        "negocio:alquiler",
                        gone=bill.rent - bill.withholding,
                        taxes=(("igic", bill.rent_igic), ("retencion", bill.withholding)),
                    ),
                )
            )
            groups.append(
                (
                    "local",
                    BusinessCost(
                        "negocio:suministros",
                        gone=bill.utilities,
                        taxes=(("igic", bill.utilities_igic),),
                    ),
                )
            )
            if bill.fees:
                groups.append(
                    ("local", BusinessCost("negocio:terraza", taxes=(("tasa", bill.fees),)))
                )
            due = debt + bill.total
            # El mozo de almacén salva parte de la merma; la limpieza sube la reputación y, con
            # el community manager, deja lo de mañana (`Business.support`).
            would = sum(spoil_units(business).values())
            spoiled = spoil(business, helped.keep)
            kept = would - sum(spoiled.values())
            if business.last_open_day != day.isoformat():
                drift(business)
            cleaned = min(helped.reputation, REPUTATION_MAX - business.reputation)
            business.reputation += max(0.0, cleaned)
            business.support = {
                key: round(value, 4)
                for key, value in (("clients", helped.clients), ("patience", helped.patience))
                if value
            }
            business.billed_day = day.isoformat()
            amortized = amortize(business)
            if business.campaign and business.campaign_until <= day.isoformat():
                name = CAMPAIGN_BY_KEY[business.campaign].name.lower()
                business.tell(f"📣 Se acabó tu campaña: {name}.")
                business.campaign, business.campaign_until = "", ""
            lost_local = (
                bool(business.own_listing)
                and housing is not None
                and not housing.business_local_ok_in(
                    connection, guild_id, user_id, business.own_listing
                )
            )
            books = sl.BOOKKEEPING_NIGHT + igic(sl.BOOKKEEPING_NIGHT) if lead else 0
            if lead:
                groups.append(
                    (
                        "local",
                        BusinessCost(
                            "sociedad:gestoria",
                            gone=sl.BOOKKEEPING_NIGHT,
                            taxes=(("igic", books - sl.BOOKKEEPING_NIGHT),),
                        ),
                    )
                )
            if pay.state:
                # Lo que un autónomo no paga de esto pasa a su deuda con Hacienda, con recargo.
                groups.append(
                    (
                        "seguros",
                        BusinessCost(
                            "negocio:seguros_sociales",
                            taxes=(("cotizacion", pay.ss), ("retencion_plantilla", pay.irpf)),
                            debt_surcharge=staff.LATE_SURCHARGE,
                        ),
                    )
                )
            caught = staff.inspection(business, day, pay.black)
            if caught is not None:
                fine, arrears = caught
                groups.append(
                    (
                        "inspeccion",
                        BusinessCost(
                            "negocio:inspeccion",
                            taxes=(("multa", fine), ("cotizacion", arrears)),
                            expense=False,
                            debt_surcharge=0.0,
                        ),
                    )
                )
            week_result: dict[str, Any] | None = None
            if lead and day.weekday() == 6:
                # La última noche de la semana fiscal, el Impuesto sobre Sociedades, con lo de
                # esta noche ya devengado (aunque no llegue para pagarlo) y lo de los otros
                # locales de la sociedad, que ya cerraron su noche.
                week = week_of(day)
                business.roll_over(day, week)
                info = business.company
                offset = info["offset"] if info["offset_week"] == week else None
                totals = dict(business.week_totals)
                stored = self.repository.nave_in(connection, guild_id, user_id)
                others = [o.week_totals for o in siblings if o.week == week]
                if stored is not None and stored.week == week:
                    others.append(stored.week_totals)
                for other_totals in others:
                    for key, value in other_totals.items():
                        totals[key] = totals.get(key, 0) + value
                carry = info.pop("carry", None) or {}
                if carry.get("week") != week:
                    carry = {}
                base = (
                    sl.tax_base(totals, offset)
                    + carry.get("base", 0)
                    - bill.total
                    - amortized
                    - books
                    - pay.cost
                )
                week_result = sl.settle_week(
                    info, base, weekly_sales=totals.get("ventas", 0) + carry.get("sales", 0),
                    day=day, zec=_zec_week(business, [business, *siblings]),
                )  # fmt: skip
                if week_result["tax"]:
                    groups.append(
                        (
                            "is",
                            BusinessCost(
                                "sociedad:hacienda:is",
                                taxes=(("sociedades", week_result["tax"]),),
                                expense=False,
                            ),
                        )
                    )
            # Las cuotas de sus préstamos, lo último: el banco cobra después del local y de
            # Hacienda. Los intereses son gasto; la parte de capital, no.
            installments = (
                housing.business_night_in(connection, guild_id, business.id, day)
                if housing is not None
                else []
            )
            loans_bills = []
            for item in installments:
                loans_bills.append(
                    BusinessCost(f"{BANK_LOAN_CONCEPT}intereses", gone=item.interest)
                )
                loans_bills.append(
                    BusinessCost(
                        f"{BANK_LOAN_CONCEPT}cuota", gone=item.due - item.interest, expense=False
                    )
                )
            loan_events: list[str] = []
            staff_events: list[str] = []

            def settle(connection: sqlite3.Connection, unpaid: int) -> None:
                business.roll_over(day, week_of(day))
                if installments:
                    assert housing is not None
                    unpaid = self._settle_loans_in(
                        connection, guild_id, business, installments, unpaid, now, loan_events
                    )
                missing = _tail_missing(groups, unpaid)
                local = missing.get("local", 0)
                if company:
                    tax = week_result["tax"] if week_result else 0
                    business.company["tax_debt"] = (
                        missing.get("hacienda", 0)
                        + missing.get("is", 0)
                        + missing.get("inspeccion", 0)
                        + round(missing.get("seguros", 0) * (1 + staff.LATE_SURCHARGE))
                    )
                    # Sociedades va por devengo: el local, la gestoría y la plantilla cuentan
                    # esta noche.
                    business.add("local", -(due - debt))
                    business.add("gestoria", -books)
                    business.add("personal", -pay.cost)
                    business.add("negro", -pay.black)
                    if tax:
                        business.add("sociedades", -tax)
                    if week_result is not None:
                        business.company["last"] = {"week": week_of(day), **week_result}
                        # Los resultados de las últimas semanas (los ejercicios cerrados): los
                        # mira quien compre la sociedad (art. 37.1.b LIRPF).
                        business.company["results"] = [
                            *business.company.get("results", []),
                            week_result["profit"],
                        ][-sl.SALE_WEEKS :]
                        _tell_ric(business, week_result)
                else:
                    business.add("local", -(due - local))
                    paid_staff = (
                        owed_staff - missing.get("atrasos", 0)
                        + pay.net - missing.get("nominas", 0)
                        + pay.state - missing.get("seguros", 0)
                    )  # fmt: skip
                    business.add("personal", -paid_staff)
                    business.add("negro", -(pay.black - missing.get("negro", 0)))
                if caught is not None:
                    business.add("multas", -sum(caught))
                business.add("amortizacion", -amortized)
                business.rent_debt = local
                business.unpaid_nights = business.unpaid_nights + 1 if local else 0
                if local:
                    whose = "la caja de la sociedad" if company else "para el local"
                    business.tell(
                        f"🧾 Anoche no llegó {whose}: debes {local} Y$ al casero "
                        f"({business.unpaid_nights} de {EVICTION_NIGHTS} noches)."
                    )
                if spoiled:
                    units = sum(spoiled.values())
                    business.tell(
                        f"🗑️ Se te estropearon {units} unidades de lo perecedero"
                        + (f" (el mozo de almacén salvó {kept})." if kept else ".")
                    )
                staff_events.extend(
                    _settle_staff(business, pay, missing, day, caught, shield=helped.shield)
                )
                self.repository.save_in(connection, guild_id, business)
                gone = business.unpaid_nights >= EVICTION_NIGHTS or lost_local
                holder.append(
                    NightRun(user_id, business.name, day, due, local, spoiled,
                             evicted=gone, amortized=amortized, corporate=week_result,
                             loans=tuple(loan_events), payroll=pay.cost + pay.black,
                             staff=tuple(staff_events), fine=sum(caught) if caught else 0,
                             support=helpers, kept=kept, cleaned=max(0.0, cleaned),
                             rescheduled=staff_events.count("horario"))
                )  # fmt: skip
                if gone:
                    evict.append("sin_local" if lost_local else "desahucio")

            return business, BusinessMove(
                user_id=user_id,
                bills=(*(c for _, c in groups), *loans_bills),
                settle=settle,
                amortization=amortized,
                account=_account(business),
            )

        housing = self.housing
        business, paid = await self.economy.business(guild_id, prepare)
        if business is None:
            return None
        run = holder[0]
        if evict:
            await self._close(guild_id, user_id, business.id, now, reason=evict[0])
        elif business.is_company and leads and leads[-1]:
            # Una vez por noche y sociedad: con el último de sus locales.
            await self._watch_losses(guild_id, business, paid.company, day)
        return run

    def _settle_loans_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        business: Business,
        installments: list[Any],
        unpaid: int,
        now: float,
        events: list[str],
    ) -> int:
        """Guarda las cuotas del banco de una noche y devuelve lo que no llegó de lo demás.

        Las cuotas van al final de los recibos, así que lo que no llegó es suyo primero, del
        último préstamo al primero. Los intereses cuentan en las cuentas de la semana: los de
        una sociedad por devengo (aunque no lleguen); los de un autónomo, lo pagado (caja).
        """
        assert self.housing is not None
        missing: dict[int, int] = {}
        for item in reversed(installments):
            take = min(unpaid, item.due)
            unpaid -= take
            if take:
                missing[item.loan.id] = take
        for item in installments:
            lost = missing.get(item.loan.id, 0)
            unpaid_interest = max(0, lost - (item.due - item.interest))
            booked = item.interest if business.is_company else item.interest - unpaid_interest
            business.add("intereses", -booked)
        results = self.housing.settle_business_night_in(
            connection, guild_id, installments, missing, now
        )
        for what, loan, amount in results:
            events.append(what)
            if what == "sin_pagar":
                business.tell(
                    f"🏦 Anoche no llegó para la cuota del préstamo #{loan.id}: debes "
                    f"{missing[loan.id]} Y$ al banco ({loan.unpaid_count + 1} de "
                    f"{lending.DEFAULT_INSTALLMENTS} cuotas; con todas, vence entero)."
                )
            elif what == "devuelto":
                business.tell(f"🏦 Pagada la última cuota del préstamo #{loan.id}.")
            elif what == "vencido" and business.is_company:
                # El banco reclama al socio, que lo avalaba; la sociedad le debe lo que pague
                # por ella (acción de reembolso, art. 1838 CC): es su préstamo de socio.
                business.company["loan"] += amount
                business.tell(
                    f"🏦 El banco da por vencido el préstamo #{loan.id} y te reclama a ti, que lo "
                    f"avalabas: debes {amount} Y$ al banco. Tu sociedad te lo debe a ti."
                )
            elif what == "vencido":
                business.tell(
                    f"🏦 El banco da por vencido el préstamo #{loan.id}: {amount} Y$ pasan a "
                    "deuda tuya con el banco, que se cobra cada noche y se embarga."
                )
        return unpaid

    async def _watch_losses(self, guild_id: int, business: Business, cash: int, day: date) -> None:
        """Apunta si la sociedad está en causa de disolución por pérdidas (art. 363.1.e LSC).

        Con dos noches (meses) así sin remediarla, su administrador responde de las deudas al
        liquidarla (art. 367 LSC, `business_sl.liable_for_all`).
        """
        group = await self._group(guild_id, business)
        business = next((b for b in group if b.id == business.id), business)
        info = business.company
        bank = await self._group_bank(guild_id, group)
        nave = await self.repository.nave(guild_id, business.user_id)
        cause = sl.in_dissolution(info, _equity(group, cash, bank, nave))
        since = info.get("cause_since", "")
        if cause == bool(since):
            return
        info["cause_since"] = day.isoformat() if cause else ""
        if cause:
            business.tell(
                "⚠️ Las pérdidas dejan a tu sociedad por debajo de la mitad de su capital: es "
                "causa de disolución. Aporta dinero en dos noches o responderás de sus deudas."
            )
        await self.repository.save(guild_id, business)

    # -- Cerrar -------------------------------------------------------------------------

    def offer(self, business: Business) -> ClosingOffer:
        """Lo que daría cerrar ahora: traspasándolo o liquidando lo que queda."""
        return closing_offer(business, self._clock())

    async def close(
        self, guild_id: int, user_id: int, *, traspaso: bool = False, biz: int | None = None
    ) -> Closing:
        """Cierra un negocio: traspasándolo o liquidando el stock y las mejoras.

        La fianza vuelve menos lo que se deba al casero. Tratamiento fiscal de lo cobrado en
        `EconomyService.business` («Traspasar»). La cuota de la semana ya pagada no se
        devuelve. Si es un local de una sociedad que tiene más, la sociedad sigue (`_close`).

        Raises:
            BusinessError: Si no tiene negocio o nadie quiere traspasarlo.
        """
        async with self._lock(guild_id, user_id):
            business = await self.repository.business(guild_id, user_id, biz)
            if business is None:
                raise BusinessError("No tienes negocio.")
            if traspaso and not self.offer(business).can_traspasar:
                raise BusinessError(
                    "Nadie quiere quedárselo: hace falta una semana abierto, 3 estrellas y "
                    "un local alquilado."
                )
            reason = "traspaso" if traspaso else "cierre"
            return await self._close(guild_id, user_id, business.id, self._clock(), reason=reason)

    async def _close(
        self, guild_id: int, user_id: int, business_id: int, now: float, *, reason: str
    ) -> Closing:
        """Borra el negocio y mueve la fianza y lo que se cobra (el candado lo tiene quien llama).

        Si no es un traspaso (cierre, desahucio, sin local), se liquida a la baja. Si es un
        local de una sociedad que tiene más (fase 5), la sociedad no se liquida: lo cobrado
        entra en su caja, lo que quedaba por pagar sale de ella, sus préstamos pasan a otro de
        sus locales y lo de la semana de este local entra en el Sociedades del domingo
        (`company["carry"]`). Cerrar un establecimiento es causa organizativa: a su plantilla se
        la despide como en un cierre (despido objetivo, art. 52.c ET).
        """
        traspaso = reason == "traspaso"
        housing = self.housing
        today = local_day(now)
        #: `(lo que pagó la caja a sus préstamos, lo que se queda el dueño)`.
        bank: list[tuple[int, int]] = []
        #: Lo que no llegó a la plantilla y paga el FOGASA.
        fogasa: list[int] = []

        def prepare(connection: sqlite3.Connection) -> tuple[Closing, BusinessMove]:
            business = self.repository.business_in(connection, guild_id, user_id, business_id)
            if business is None:
                raise BusinessError("No tienes negocio.")
            offer = closing_offer(business, now)
            forfeit = min(business.deposit, business.rent_debt)
            refund = business.deposit - forfeit
            # La plantilla: con el traspaso se va con el negocio (art. 44 ET, sin despido); si
            # no, despido objetivo por el cierre (20 días por año, art. 52.c ET). Lo que se le
            # debía va con sus finiquitos, antes que nada (art. 32 ET).
            severances = (
                0
                if traspaso
                else sum(
                    staff.severance(e, "objetivo")
                    for e in business.staff
                    if e["contract"] != "b" and not staff.in_trial(e, today)
                )
            )
            owed_staff = business.wage_debt + severances
            business.roll_over(today, week_of(today))
            if severances:
                business.add("despidos", -severances)
            finiquitos = (
                (BusinessCost("negocio:plantilla:finiquitos", gone=owed_staff),)
                if owed_staff
                else ()
            )
            team = len(business.staff)
            if traspaso:
                stock, proceeds = offer.stock, offer.book + offer.goodwill
                gain = offer.goodwill
            else:
                stock, proceeds = offer.liquidation_stock, offer.liquidation_equipment
                gain = proceeds - offer.equipment_book
            if housing is not None and business.own_listing:
                housing.free_business_local_in(
                    connection, guild_id, user_id, business.own_listing, today, now
                )
            self.repository.close_in(connection, guild_id, business, now, reason)
            last = not self.repository.owned_in(connection, guild_id, user_id)
            nave = self.repository.nave_in(connection, guild_id, user_id) if last else None
            shut = _shut_nave(nave, today) if nave is not None else None
            if nave is not None:
                if housing is not None and nave.own_listing:
                    housing.free_business_local_in(
                        connection, guild_id, user_id, nave.own_listing, today, now
                    )
                self.repository.delete_nave_in(connection, guild_id, user_id)
            heir = (
                next(
                    (
                        b
                        for b in self.repository.owned_in(connection, guild_id, user_id)
                        if b.company_id == business.company_id
                    ),
                    None,
                )
                if business.is_company
                else None
            )
            if heir is not None:
                # La sociedad sigue con sus otros locales: este cierra y no se liquida.
                _carry(
                    heir.company,
                    week_of(today),
                    sl.tax_base(business.week_totals) + stock + proceeds - offer.book,
                    business.week_totals.get("ventas", 0) + stock,
                )
                if housing is not None:
                    housing.move_business_loans_in(connection, guild_id, business.id, heir.id)
                heir.tell(
                    f"🚪 {business.name} ({business.barrio_info.name}) ha cerrado: tu sociedad "
                    "sigue con este local."
                )
                self.repository.save_in(connection, guild_id, heir)
                owed = business.rent_debt - forfeit
                bills = list(finiquitos)
                if owed:
                    bills.append(BusinessCost("negocio:deuda_local", gone=owed, expense=False))

                def settle_branch(connection: sqlite3.Connection, unpaid: int) -> None:
                    # Lo que no llega de los finiquitos (van los primeros) lo paga el FOGASA.
                    fogasa.append(max(0, unpaid - (sum(b.total for b in bills) - owed_staff)))

                closing = Closing(
                    business, refund, forfeit, 0, traspaso, stock, proceeds, reason=reason,
                    company=True, staff=team, severance=severances,
                )  # fmt: skip
                move = BusinessMove(
                    user_id=user_id,
                    account=_account(business),
                    refund=refund,
                    forfeit=forfeit,
                    sales=stock,
                    sold_cost=offer.stock,
                    proceeds=proceeds,
                    bills=tuple(bills),
                    settle=settle_branch,
                )
                return closing, move
            if business.is_company:
                info = business.company
                # El último Impuesto sobre Sociedades: lo de la semana hasta hoy, con la venta.
                offset = info["offset"] if info["offset_week"] == business.week else None
                base = sl.tax_base(business.week_totals, offset) + stock + proceeds - offer.book
                # La RIC que queda (sin invertir o sin mantener lo invertido) se integra.
                base += sl.ric_close(info)
                sales = business.week_totals.get("ventas", 0) + stock
                carry = info.get("carry") or {}
                if carry.get("week") == week_of(today):
                    # Lo de los locales que cerraron esta semana.
                    base += carry.get("base", 0)
                    sales += carry.get("sales", 0)
                if shut is not None:
                    base += shut.base
                    sales += shut.sales
                result = sl.settle_week(info, base, weekly_sales=sales)
                hacienda = info["tax_debt"] + result["tax"]
                owed = business.rent_debt - forfeit
                bills = list(finiquitos)
                if hacienda:
                    bills.append(
                        BusinessCost(
                            "sociedad:hacienda:cierre",
                            taxes=(("sociedades", hacienda),),
                            expense=False,
                        )
                    )
                if owed:
                    bills.append(BusinessCost("negocio:deuda_local", gone=owed, expense=False))
                if shut is not None and shut.owed:
                    bills.append(BusinessCost("negocio:nave:deuda", gone=shut.owed, expense=False))
                # Luego el banco (sus préstamos vencen al disolverla): lo que la caja no llegue
                # a pagar lo asume su socio, que lo avalaba (`business_loans_to_owner_in`).
                loans = business_loans_in(connection, guild_id, business.id) if housing else []
                bank_owed = sum(loan.owed for loan in loans)
                if bank_owed:
                    bills.append(
                        BusinessCost(
                            f"{BANK_LOAN_CONCEPT}cancelacion", gone=bank_owed, expense=False
                        )
                    )

                def settle(connection: sqlite3.Connection, unpaid: int) -> None:
                    if housing is not None and bank_owed:
                        paid = bank_owed - min(unpaid, bank_owed)
                        bank.append(
                            housing.business_loans_to_owner_in(
                                connection, guild_id, business.id, now, paid
                            )
                        )
                    # Lo que no llega de los finiquitos (los primeros: solo si no llega para
                    # nada) lo paga el FOGASA.
                    fogasa.append(max(0, unpaid - (sum(b.total for b in bills) - owed_staff)))

                limit = (
                    owed + hacienda
                    if sl.liable_for_all(info, today.isoformat())
                    else sl.guarantee(info)
                )
                closing = Closing(
                    business, refund, forfeit, 0, traspaso, stock, proceeds, reason=reason,
                    company=True, corporate_tax=result["tax"], staff=team, severance=severances,
                )  # fmt: skip
                move = BusinessMove(
                    user_id=user_id,
                    account=_account(business),
                    refund=refund + (shut.refund if shut else 0),
                    forfeit=forfeit + (shut.forfeit if shut else 0),
                    sales=stock + (shut.liquidation if shut else 0),
                    sold_cost=offer.stock,
                    proceeds=proceeds,
                    bills=tuple(bills),
                    liquidation=True,
                    liquidation_basis=info["contributed"],
                    liquidation_loan=info["loan"],
                    cover_limit=limit,
                    settle=settle,
                )
                return closing, move
            if housing is not None:
                # Los del autónomo ya eran suyos: siguen igual, ahora como personales.
                bank.append(
                    housing.business_loans_to_owner_in(connection, guild_id, business.id, now)
                )
            closing = Closing(
                business, refund, forfeit, 0, traspaso, stock, proceeds, reason=reason,
                staff=team, severance=severances,
            )  # fmt: skip

            def settle_staff(connection: sqlite3.Connection, unpaid: int) -> None:
                fogasa.append(unpaid)

            bills = list(finiquitos)
            if shut is not None and shut.owed:
                bills.append(BusinessCost("negocio:nave:deuda", gone=shut.owed))
            move = BusinessMove(
                user_id=user_id,
                refund=refund + (shut.refund if shut else 0),
                forfeit=forfeit + (shut.forfeit if shut else 0),
                sales=stock + (shut.liquidation if shut else 0),
                sold_cost=offer.stock + (shut.cost if shut else 0),
                proceeds=proceeds,
                gain=gain,
                bills=tuple(bills),
                settle=settle_staff,
            )
            return closing, move

        closing, paid = await self.economy.business(guild_id, prepare)
        bank_paid, bank_moved = bank[0] if bank else (0, 0)
        return Closing(
            closing.business,
            closing.refund,
            closing.forfeit,
            paid.balance,
            closing.traspaso,
            closing.stock,
            closing.proceeds,
            paid.gain_tax,
            closing.reason,
            closing.company,
            paid.liquidated,
            paid.loan_repaid,
            paid.covered,
            paid.derived_debt,
            closing.corporate_tax,
            bank_paid,
            bank_moved,
            closing.staff,
            closing.severance,
            fogasa[0] if fogasa else 0,
        )

    # -- Fase 5: la nave, importar, el gerente y vender la sociedad -----------------------

    async def nave(self, guild_id: int, user_id: int) -> Nave | None:
        """La nave de un miembro con sus cuentas de esta semana, o `None`."""
        nave = await self.repository.nave(guild_id, user_id)
        if nave is not None:
            nave.roll_over(week_of(local_day(self._clock())))
        return nave

    @staticmethod
    def nave_quote(barrio: str, size: int) -> NaveQuote:
        """Lo que cuesta alquilar una nave de tamaño `size` en `barrio`.

        Raises:
            BusinessError: Si el barrio o el tamaño no existen.
        """
        if barrio not in BARRIO_BY_KEY or not 0 <= size < len(chain.NAVE_SIZES):
            raise BusinessError("Esa nave no existe.")
        return chain.nave_quote(BARRIO_BY_KEY[barrio], size)

    async def own_naves(self, guild_id: int, user_id: int) -> list[OwnLocal]:
        """Las naves enteras suyas de `pisos`, con su tamaño y si están libres para la cadena."""
        if self.housing is None:
            return []
        housing = self.housing

        def read(connection: sqlite3.Connection) -> list[tuple[Listing, str]]:
            return housing.business_locals_in(connection, guild_id, user_id)

        current = await self.repository.nave(guild_id, user_id)
        out = []
        for listing, status in await self.repository.read(read):
            if listing.kind != "nave":
                continue
            size = chain.nave_size_for(listing.m2)
            free = status == "vacio" or (current is not None and current.own_listing == listing.key)
            out.append(OwnLocal(listing, status, size, size is not None and free))
        return out

    async def open_nave(
        self, guild_id: int, user_id: int, *, barrio: str, size: int, own: str = ""
    ) -> tuple[Nave, NaveQuote, int]:
        """Abre la nave de la cadena: alquilada en `barrio` o una nave entera suya (`own`).

        Tratamiento fiscal: la tasa municipal de la declaración responsable del almacén (art.
        20.4.i TRLRHL) va al Estado y es gasto de la actividad (un almacén sin mercancía
        peligrosa es actividad inocua: la Ley 7/2011 de Canarias no le pide comunicación previa,
        art. 2.3.c, pero las ordenanzas suelen pedir la declaración). La fianza, dos
        mensualidades (art. 36.1 LAU), al depósito de fianzas. Si la cadena es de su sociedad,
        lo paga su caja, y la sociedad no puede usar una nave de su socio (operación vinculada,
        art. 18 LIS). Una nave propia queda afecta a la actividad, como un local propio
        (`housing.OWN_BUSINESS`).

        Returns:
            `(la nave, lo que costó, saldo final de quien paga)`.

        Raises:
            BusinessError: Sin locales, con nave ya, si la nave propia no sirve o si no llega.
        """
        async with self._lock(guild_id, user_id):
            now = self._clock()
            today, week = self._today(now)
            owned = await self.repository.owned(guild_id, user_id)
            if not owned:
                raise BusinessError("Monta antes un local: la nave reparte a tus locales.")
            head = owned[0]
            if own:
                if head.is_company:
                    raise BusinessError(
                        "Tu sociedad no puede usar una nave tuya sin pagarte un alquiler "
                        "(operación vinculada, art. 18 LIS): de momento, solo alquiladas."
                    )
                found = next(
                    (o for o in await self.own_naves(guild_id, user_id) if o.listing.key == own),
                    None,
                )
                if found is None or found.size is None or not found.fits:
                    raise BusinessError("Esa nave no es entera tuya, está ocupada o es pequeña.")
                barrio, size = found.listing.barrio, found.size
            quote = (
                chain.nave_quote(BARRIO_BY_KEY[barrio], size, own=True)
                if own
                else self.nave_quote(barrio, size)
            )
            housing = self.housing

            def prepare(connection: sqlite3.Connection) -> tuple[Nave, BusinessMove]:
                if self.repository.nave_in(connection, guild_id, user_id) is not None:
                    raise BusinessError("Ya tienes una nave.")
                nave = Nave(
                    user_id=user_id,
                    barrio=barrio,
                    size=size,
                    own_listing=own,
                    opened_at=now,
                    deposit=quote.deposit,
                    billed_day=(today - timedelta(days=1)).isoformat(),
                )
                nave.roll_over(week)
                nave.add("apertura", -quote.license)
                if own:
                    assert housing is not None
                    try:
                        housing.occupy_business_local_in(
                            connection, guild_id, user_id, own, today, now
                        )
                    except HousingError as error:
                        raise BusinessError(str(error)) from error
                self.repository.save_nave_in(connection, guild_id, nave)
                cost = BusinessCost("negocio:nave:apertura", taxes=(("tasa", quote.license),))
                return nave, BusinessMove(
                    user_id=user_id,
                    payments=(cost,),
                    deposit=quote.deposit,
                    account=_account(head),
                )

            try:
                nave, paid = await self.economy.business(guild_id, prepare)
            except InsufficientFundsError as error:
                raise BusinessError(_broke(head, quote.total)) from error
            return nave, quote, paid.company if head.is_company else paid.balance

    async def import_container(
        self, guild_id: int, user_id: int, product: str
    ) -> tuple[Nave, ImportQuote, int]:
        """Pide a la Península un contenedor de `product` para la nave: llega en
        `business_chain.IMPORT_NIGHTS` noches.

        Tratamiento fiscal: es una importación (art. 8.1 de la Ley 20/1991: la Península es
        territorio tercero para el IGIC). El IGIC de la importación, al tipo de la entrega del
        mismo bien (art. 32.4 del DL 1/2025 de Canarias), y el AIEM de lo que está en su anexo
        (art. 92 y anexo 2.º) van al Estado. El pequeño empresario los paga y no los deduce (art.
        91.b): son coste. El fabricante y la naviera no existen: su parte desaparece. El agente
        de aduanas lleva IGIC general. Todo es gasto de la actividad el día que se paga
        (compras), como lo del mayorista; de una sociedad, de su Sociedades.

        Returns:
            `(la nave, el contenedor, saldo final de quien paga)`.

        Raises:
            BusinessError: Sin nave, si no lo vende ningún local de su isla, si se estropea en
                el barco, si no cabe o si no llega.
        """
        item = chain.product_by_key(product)
        if item is None:
            raise BusinessError("Ese producto no existe.")
        if not chain.can_import(item):
            raise BusinessError(
                f"{item.name} caduca: no aguanta lo que dura un contenedor. Eso, al mayorista."
            )
        quote = chain.import_quote(item)
        async with self._lock(guild_id, user_id):
            now = self._clock()
            today, week = self._today(now)
            owned = await self.repository.owned(guild_id, user_id)
            if not owned:
                raise BusinessError("No tienes negocio.")

            def prepare(connection: sqlite3.Connection) -> tuple[Nave, BusinessMove]:
                nave = self.repository.nave_in(connection, guild_id, user_id)
                if nave is None:
                    raise BusinessError("Primero necesitas una nave donde descargarlo.")
                sold = {
                    p.key
                    for b in owned
                    if b.barrio_info.island == nave.island
                    for p in b.sector_info.products
                }
                if product not in sold:
                    raise BusinessError("Ninguno de tus locales de la isla de tu nave vende eso.")
                room = nave.size_info.lots - chain.used_lots(nave)
                if room < chain.CONTAINER_LOTS:
                    raise BusinessError(
                        f"No cabe: un contenedor son {chain.CONTAINER_LOTS} lotes y en tu nave "
                        f"quedan {max(0, room)} libres (con lo que viene en camino)."
                    )
                nave.roll_over(week)
                nave.seq += 1
                arrives = today + timedelta(days=chain.IMPORT_NIGHTS - 1)
                nave.orders = [
                    *nave.orders,
                    {
                        "id": nave.seq,
                        "product": product,
                        "units": quote.units,
                        "arrives": arrives.isoformat(),
                    },
                ]
                nave.add("compras", -quote.total)
                self.repository.save_nave_in(connection, guild_id, nave)
                taxes = tuple((k, v) for k, v in (("igic", quote.igic), ("aiem", quote.aiem)) if v)
                payments = (
                    BusinessCost("negocio:importacion", gone=chain.import_base(quote), taxes=taxes),
                    BusinessCost(
                        "negocio:aduanas", gone=quote.customs, taxes=(("igic", quote.customs_igic),)
                    ),
                )
                return nave, BusinessMove(
                    user_id=user_id, payments=payments, account=_account(owned[0])
                )

            try:
                nave, paid = await self.economy.business(guild_id, prepare)
            except InsufficientFundsError as error:
                raise BusinessError(_broke(owned[0], quote.total)) from error
            return nave, quote, paid.company if owned[0].is_company else paid.balance

    async def from_nave(
        self, guild_id: int, user_id: int, product: str, lots: int, *, biz: int | None = None
    ) -> tuple[Business, int]:
        """Trae `lots` lotes de `product` de la nave al almacén del local. No mueve dinero: lo
        importado ya se pagó (simplificación: el reparto en la isla no cuesta).

        Returns:
            `(el negocio, unidades traídas)`.

        Raises:
            BusinessError: Sin nave, si es de otra isla, si el local no lo vende o si no queda.
        """
        if lots < 1:
            raise BusinessError("Al menos un lote.")
        async with self._lock(guild_id, user_id):
            today, week = self._today(self._clock())
            moved: list[int] = []

            def prepare(connection: sqlite3.Connection) -> tuple[Business, BusinessMove]:
                business = self.repository.business_in(connection, guild_id, user_id, biz)
                nave = self.repository.nave_in(connection, guild_id, user_id)
                if business is None:
                    raise BusinessError("No tienes negocio.")
                if nave is None:
                    raise BusinessError("No tienes nave.")
                if nave.island != business.barrio_info.island:
                    raise BusinessError("Tu nave está en otra isla: solo reparte en la suya.")
                item = business.sector_info.product(product)
                if item is None:
                    raise BusinessError("Eso no se vende en este local.")
                units = min(lots * item.pack, nave.stock.get(product, 0))
                if units <= 0:
                    raise BusinessError(f"En la nave no queda {item.name.lower()}.")
                business.roll_over(today, week)
                nave.stock[product] = nave.stock.get(product, 0) - units
                nave.stock = {k: v for k, v in nave.stock.items() if v > 0}
                business.stock[product] = business.stock.get(product, 0) + units
                self.repository.save_nave_in(connection, guild_id, nave)
                self.repository.save_in(connection, guild_id, business)
                moved.append(units)
                return business, BusinessMove(user_id=user_id)

            business, _paid = await self.economy.business(guild_id, prepare)
            return business, moved[0]

    async def close_nave(self, guild_id: int, user_id: int) -> tuple[int, int, int]:
        """Deja la nave: el mayorista recompra lo que hay dentro a la mitad y la fianza vuelve
        menos lo que se deba.

        Tratamiento fiscal: lo que paga el mayorista es venta de la actividad (art. 27 LIRPF), o
        ingreso de la sociedad; la fianza es dinero propio. Lo que se debía al casero se paga
        antes de irse (gasto). Una nave propia vuelve a estar vacía en `pisos`.

        Returns:
            `(lo cobrado por el stock, la fianza que vuelve, saldo final de quien cobra)`.

        Raises:
            BusinessError: Sin nave o con contenedores en camino.
        """
        async with self._lock(guild_id, user_id):
            now = self._clock()
            today, week = self._today(now)
            owned = await self.repository.owned(guild_id, user_id)
            housing = self.housing
            parts: list[_NaveShut] = []

            def prepare(connection: sqlite3.Connection) -> tuple[Nave, BusinessMove]:
                nave = self.repository.nave_in(connection, guild_id, user_id)
                if nave is None:
                    raise BusinessError("No tienes nave.")
                if nave.orders:
                    raise BusinessError("Tienes contenedores en camino: espera a que lleguen.")
                shut = _shut_nave(nave, today)
                parts.append(shut)
                company = bool(owned) and owned[0].is_company
                if company:
                    # La sociedad sigue: lo de la nave esta semana va a su Sociedades.
                    first = self.repository.owned_in(connection, guild_id, user_id)[0]
                    _carry(first.company, week, shut.base, shut.sales)
                    self.repository.save_in(connection, guild_id, first)
                if housing is not None and nave.own_listing:
                    housing.free_business_local_in(
                        connection, guild_id, user_id, nave.own_listing, today, now
                    )
                self.repository.delete_nave_in(connection, guild_id, user_id)
                bills = (
                    (BusinessCost("negocio:nave:deuda", gone=shut.owed, expense=not company),)
                    if shut.owed
                    else ()
                )
                return nave, BusinessMove(
                    user_id=user_id,
                    refund=shut.refund,
                    forfeit=shut.forfeit,
                    sales=shut.liquidation,
                    sold_cost=shut.cost,
                    bills=bills,
                    account=_account(owned[0]) if owned else None,
                )

            _nave, paid = await self.economy.business(guild_id, prepare)
            shut = parts[0]
            company = bool(owned) and owned[0].is_company
            return shut.liquidation, shut.refund, paid.company if company else paid.balance

    async def _close_nave_night(
        self, guild_id: int, user_id: int, day: date, now: float
    ) -> list[str]:
        """La noche de la nave: llegan los contenedores y se paga el alquiler y los
        suministros, hasta donde llegue (como un local: el alquiler lleva IGIC y
        la retención del casero). A las `NAVE_EVICTION_NIGHTS` noches sin pagar, o si una nave
        propia deja de ser entera suya, se cierra. El candado lo tiene quien llama.

        Returns:
            Lo que pasó: `contenedor` por cada uno que llegó y `desahucio` si se cerró.
        """
        events: list[str] = []
        lost_nave: list[bool] = []
        housing = self.housing

        def prepare(connection: sqlite3.Connection) -> tuple[Nave | None, BusinessMove]:
            nave = self.repository.nave_in(connection, guild_id, user_id)
            owned = self.repository.owned_in(connection, guild_id, user_id)
            if nave is None or not owned or nave.billed_day >= day.isoformat():
                return None, BusinessMove(user_id=user_id)
            company = owned[0].is_company
            iso = day.isoformat()
            keep = []
            for order in nave.orders:
                if order["arrives"] <= iso:
                    nave.stock[order["product"]] = (
                        nave.stock.get(order["product"], 0) + order["units"]
                    )
                    nave.containers += 1
                    item = chain.product_by_key(order["product"])
                    nave.tell(
                        f"🚢 Ha llegado a la nave el contenedor de "
                        f"{item.name.lower() if item else order['product']}."
                    )
                    events.append("contenedor")
                else:
                    keep.append(order)
            nave.orders = keep
            bill = chain.nave_bill(nave)
            debt = nave.rent_debt
            bills = []
            if debt:
                bills.append(BusinessCost("negocio:nave:deuda", gone=debt))
            bills.append(
                BusinessCost(
                    "negocio:nave:alquiler",
                    gone=bill.rent - bill.withholding,
                    taxes=(("igic", bill.rent_igic), ("retencion", bill.withholding)),
                )
            )
            bills.append(
                BusinessCost(
                    "negocio:nave:suministros",
                    gone=bill.utilities,
                    taxes=(("igic", bill.utilities_igic),),
                )
            )
            nave.billed_day = iso
            lost_nave.append(
                bool(nave.own_listing)
                and housing is not None
                and not housing.business_local_ok_in(
                    connection, guild_id, user_id, nave.own_listing
                )
            )

            def settle(connection: sqlite3.Connection, unpaid: int) -> None:
                nave.roll_over(week_of(day))
                owed = min(unpaid, debt + bill.total)
                # La sociedad lo apunta al devengo; el autónomo, lo pagado.
                nave.add("local", -(bill.total if company else debt + bill.total - owed))
                nave.rent_debt = owed
                nave.unpaid_nights = nave.unpaid_nights + 1 if owed else 0
                if owed:
                    nave.tell(
                        f"🧾 Anoche no llegó para la nave: debes {owed} Y$ al casero "
                        f"({nave.unpaid_nights} de {chain.NAVE_EVICTION_NIGHTS} noches)."
                    )
                self.repository.save_nave_in(connection, guild_id, nave)

            return nave, BusinessMove(
                user_id=user_id, bills=tuple(bills), settle=settle, account=_account(owned[0])
            )

        nave, _paid = await self.economy.business(guild_id, prepare)
        if nave is None:
            return events
        if nave.unpaid_nights >= chain.NAVE_EVICTION_NIGHTS or (lost_nave and lost_nave[0]):
            await self._evict_nave(guild_id, user_id, now)
            events.append("desahucio")
        return events

    async def _evict_nave(self, guild_id: int, user_id: int, now: float) -> None:
        """Cierra la nave sin preguntar (el casero la rescinde o ya no es suya): lo que viene
        en camino lo descarga el puerto y se pierde. El candado lo tiene quien llama."""
        nave = await self.repository.nave(guild_id, user_id)
        if nave is None:
            return
        if nave.orders:
            nave.orders = []
            await self.repository.save_nave(guild_id, nave)
        owned = await self.repository.owned(guild_id, user_id)
        today, week = self._today(now)
        housing = self.housing

        def prepare(connection: sqlite3.Connection) -> tuple[None, BusinessMove]:
            stored = self.repository.nave_in(connection, guild_id, user_id)
            if stored is None:
                return None, BusinessMove(user_id=user_id)
            shut = _shut_nave(stored, today)
            locales = self.repository.owned_in(connection, guild_id, user_id)
            company = bool(locales) and locales[0].is_company
            if locales:
                first = locales[0]
                if company:
                    _carry(first.company, week, shut.base, shut.sales)
                first.tell("🏭 Has perdido la nave: lo que había dentro se liquidó a la mitad.")
                self.repository.save_in(connection, guild_id, first)
            if housing is not None and stored.own_listing:
                housing.free_business_local_in(
                    connection, guild_id, user_id, stored.own_listing, today, now
                )
            self.repository.delete_nave_in(connection, guild_id, user_id)
            bills = (
                (BusinessCost("negocio:nave:deuda", gone=shut.owed, expense=not company),)
                if shut.owed
                else ()
            )
            return None, BusinessMove(
                user_id=user_id,
                refund=shut.refund,
                forfeit=shut.forfeit,
                sales=shut.liquidation,
                sold_cost=shut.cost,
                bills=bills,
                account=_account(owned[0]) if owned else None,
            )

        await self.economy.business(guild_id, prepare)

    async def _manage(
        self, guild_id: int, user_id: int, biz: int, day: date, now: float
    ) -> list[str]:
        """La noche del gerente de un local, después de cerrarla: repone el stock y cubre las
        bajas. El candado lo tiene quien llama.

        - **Reponer** (`business_chain.restock`): hasta día y medio de lo que se vendió, de la
          nave de su isla primero y luego del mayorista, con lo que haya en la caja (como una
          compra del dueño: IGIC al Estado y gasto de la actividad).
        - **Cubrir las bajas** (`Business.vacancies`): a quien se fue o acabó su contrato le
          sustituye el mejor candidato del día siguiente, indefinido y con lo que pide.

        Returns:
            Lo que hizo: `repuso`, `nave`, `contrato` o `falta`.
        """
        events: list[str] = []
        next_day = day + timedelta(days=1)
        owner_debt = await self.economy.tax_debt(guild_id, user_id)
        hour = datetime.fromtimestamp(now, TIMEZONE).hour
        today = local_day(now)

        def prepare(connection: sqlite3.Connection) -> tuple[Business | None, BusinessMove]:
            business = self.repository.business_in(connection, guild_id, user_id, biz)
            boss = staff.manager(business) if business is not None else None
            if business is None or boss is None or boss["start"] > day.isoformat():
                return None, BusinessMove(user_id=user_id)
            if boss["absent_day"] == day.isoformat():
                events.append("falta")
                return None, BusinessMove(user_id=user_id)
            name = boss["name"].split()[0]
            account = _account(business)
            row = connection.execute(
                "SELECT balance FROM economy_wallets WHERE guild_id = ? AND user_id = ?",
                (guild_id, account if account is not None else user_id),
            ).fetchone()
            budget = int(row[0]) if row else 0
            nave = self.repository.nave_in(connection, guild_id, user_id)
            near = nave if nave is not None and nave.island == business.barrio_info.island else None
            plan = chain.restock(
                business.stock, business.sector_info.products, business.sold_on(day),
                near.stock if near is not None else None, budget,
                price=lambda p, units: sum(wholesale(p, units)),
            )  # fmt: skip
            payments: list[BusinessCost] = []
            brought = bought = 0
            for item in plan:
                key = item.product.key
                if item.from_nave and near is not None:
                    near.stock[key] -= item.from_nave
                    business.stock[key] = business.stock.get(key, 0) + item.from_nave
                    brought += item.from_nave
                if item.lots:
                    units = item.lots * item.product.pack
                    base, tax = wholesale(item.product, units)
                    business.stock[key] = business.stock.get(key, 0) + units
                    business.roll_over(today, week_of(today))
                    business.add("compras", -(base + tax))
                    payments.append(
                        BusinessCost(
                            "negocio:compras", gone=base, taxes=(("igic", tax),) if tax else ()
                        )
                    )
                    bought += units
            if near is not None and brought:
                near.stock = {k: v for k, v in near.stock.items() if v > 0}
                self.repository.save_nave_in(connection, guild_id, near)
                events.append("nave")
            if payments:
                events.append("repuso")
            if brought or bought:
                business.tell(
                    f"🧑‍💼 {name} ha repuesto el almacén: "
                    + ", ".join(
                        part
                        for part in (
                            f"{brought} unidades de la nave" if brought else "",
                            f"{bought} del mayorista" if bought else "",
                        )
                        if part
                    )
                    + "."
                )
            # Las bajas: el mejor candidato de mañana para cada una, mientras quepa.
            bonified = business.bonus_ban < next_day.isoformat() and (
                not business.company.get("tax_debt", 0) if business.is_company else owner_debt <= 0
            )
            for vacancy in list(business.vacancies):
                business.vacancies = business.vacancies[1:]
                role = vacancy.get("role", "")
                if staff.hire_problem(business, role) is not None:
                    continue
                own = tuple(vacancy.get("franjas") or ()) or staff.legacy_franjas(
                    vacancy["shift"], vacancy["jornada"]
                )
                schedule = staff.HORARIO_BY_KEY.get(staff.horario_key(own))
                if schedule is None:
                    continue
                pool = [
                    c
                    for c in staff.candidates(business, next_day)
                    if not (c["part_time"] and schedule.jornada != staff.HALF_DAY)
                ]
                if not pool:
                    continue
                pick = max(pool, key=lambda c: sum(c["skills"].values()))
                business.staff_seq += 1
                hired = staff.new_employee(
                    pick, seq=business.staff_seq, contract="indefinido",
                    jornada=schedule.jornada, shift=schedule.shift,
                    wage=staff.role_ask(pick["skills"], pick["traits"], schedule.jornada, role),
                    start=next_day, bonified=bonified, role=role, franjas=schedule.franjas,
                )  # fmt: skip
                hired["from_franja"] = staff.franjas_done(hour) if next_day == today else 0
                business.staff = [*business.staff, hired]
                business.seen = [*business.seen, pick["key"]][-30:]
                what = staff.ROLE_BY_KEY[role].name.lower()
                business.tell(
                    f"🧑‍💼 {name} ha contratado a {pick['name']} de {what} "
                    f"({schedule.label.lower()}): cubre una baja."
                )
                events.append("contrato")
            self.repository.save_in(connection, guild_id, business)
            return business, BusinessMove(
                user_id=user_id, payments=tuple(payments), account=account
            )

        try:
            await self.economy.business(guild_id, prepare)
        except InsufficientFundsError:
            # Entre que miró la caja y que pagó, alguien la vació: lo intenta la noche que viene.
            logger.info("El gerente del negocio %s no pudo pagar la reposición", biz)
            return [e for e in events if e not in ("repuso", "nave", "contrato")]
        return events

    async def sale_offer(
        self, guild_id: int, user_id: int, biz: int | None = None
    ) -> SaleOffer | None:
        """Lo que pagaría un comprador por las participaciones de su sociedad (`None` si no es
        una sociedad). Ver `business_chain.sale_offer`."""
        view = await self.company_view(guild_id, user_id, biz)
        if view is None or not view.business.is_company:
            return None
        nave = await self.repository.nave(guild_id, user_id)
        debts = (
            view.business.company.get("tax_debt", 0)
            + sum(b.rent_debt + b.wage_debt for b in view.locales)
            + (nave.rent_debt if nave is not None else 0)
        )
        return chain.sale_offer(
            view.business.company, view.equity, weeks=sl.SALE_WEEKS, debts=debts
        )

    async def sell_company(
        self, guild_id: int, user_id: int, *, price: int, biz: int | None = None
    ) -> Sale:
        """Vende todas las participaciones de su sociedad (con toda la cadena) al comprador.

        Tratamiento fiscal: vender participaciones está exento de IVA y de ITP (art. 338 de la
        Ley 6/2023). Para el socio es una ganancia patrimonial (art. 33 LIRPF) de la base del
        ahorro (art. 46.b): el precio menos lo que metió, capital y aportaciones (que integran
        su valor de adquisición: art. 35.1 LIRPF y la consulta V1583-24 de la DGT), al 19 % en
        el momento, como los dividendos (simplificación: sin la escala del ahorro). Una pérdida
        no se compensa. El préstamo del socio se lo paga el comprador por su valor: no es renta.
        El precio es el del art. 37.1.b LIRPF (`business_chain.sale_offer`). El comprador no
        existe: lo que paga se crea, y la sociedad se va con él con su caja, sus locales, su
        nave y su plantilla, que sigue con sus contratos (no cambia el empleador: no es una
        sucesión del art. 44 ET). Sus préstamos del banco los cancela el comprador para que
        el banco libere tu aval (si no, seguirías avalando: arts. 1205 y 1847 CC).

        Args:
            price: El precio que vio (si la oferta ha cambiado, no se vende).

        Raises:
            BusinessError: Si no es una sociedad, no hay comprador o la oferta ha cambiado.
        """
        async with self._lock(guild_id, user_id):
            offer = await self.sale_offer(guild_id, user_id, biz)
            if offer is None:
                raise BusinessError("Solo se venden sociedades: tu negocio no lo es.")
            if offer.blocker is not None:
                raise BusinessError(offer.blocker)
            if offer.price != price:
                raise BusinessError("El comprador ha cambiado su oferta: mírala otra vez.")
            now = self._clock()
            held: list[tuple[str, int, int]] = []
            housing = self.housing

            def prepare(connection: sqlite3.Connection) -> tuple[None, BusinessMove]:
                locales = [
                    b
                    for b in self.repository.owned_in(connection, guild_id, user_id)
                    if b.is_company
                ]
                if not locales:
                    raise BusinessError("Tu sociedad ya no existe.")
                company_id = locales[0].company_id
                for business in locales:
                    if housing is not None:
                        for loan in business_loans_in(connection, guild_id, business.id):
                            close_loan_in(connection, loan.id, "pagado", now, paid=loan.owed)
                    self.repository.close_in(connection, guild_id, business, now, "venta")
                self.repository.delete_nave_in(connection, guild_id, user_id)
                held.append((locales[0].name, len(locales), sum(len(b.staff) for b in locales)))
                return None, BusinessMove(
                    user_id=user_id,
                    account=company_account(company_id),
                    sale=offer.price,
                    liquidation_basis=offer.basis,
                    liquidation_loan=offer.loan,
                )

            _none, paid = await self.economy.business(guild_id, prepare)
            name, count, team = held[0]
            return Sale(offer, name, count, team, paid.gain_tax, paid.balance)

    # -- Para `saldo` -------------------------------------------------------------------

    async def dues(self, guild_id: int, user_id: int) -> tuple[int, int, int]:
        """`(lo de esta noche, deuda del local, cuota de la semana por pagar)`, de todos sus
        locales. Solo lee."""
        owned = await self.repository.owned(guild_id, user_id)
        if not owned:
            return 0, 0, 0
        fee = 0
        if not await self.economy.self_employed_fee_paid(guild_id, user_id):
            flat = self._clock() - await self._first_opened(guild_id, owned[0])
            fee = FLAT_RATE_FEE if flat < FLAT_RATE_DAYS * 86_400 else SELF_EMPLOYED_FEE
        # Los locales de una sociedad los paga su caja, no tu monedero.
        mine = [b for b in owned if not b.is_company]
        tonight = sum(nightly_bill(b).total for b in mine)
        debt = sum(b.rent_debt for b in mine)
        nave = await self.repository.nave(guild_id, user_id)
        if nave is not None and mine:
            tonight += chain.nave_bill(nave).total
            debt += nave.rent_debt
        return tonight, debt, fee


def _tell_ric(business: Business, week: dict[str, Any]) -> None:
    """Los avisos de la RIC del domingo: lo que se dota y lo que venció sin invertir."""
    if week.get("integrated"):
        business.tell(
            f"🌴 {week['integrated']} Y$ de la RIC no se invirtieron en 3 semanas: tributan en "
            "el Sociedades de esta semana (art. 27.16 de la Ley 19/1994)."
        )
    if week.get("ric"):
        business.tell(
            f"🌴 {week['ric']} Y$ a la RIC: no pagan Sociedades si los inviertes en mejoras o en "
            f"la obra de un local antes del {sl.ric_due(business.company) or 'plazo'}."
        )


@dataclass(frozen=True, slots=True)
class _NaveShut:
    """El dinero de cerrar una nave (`_shut_nave`)."""

    refund: int
    forfeit: int
    owed: int
    liquidation: int
    cost: int
    base: int
    sales: int


def _shut_nave(nave: Nave, today: date) -> _NaveShut:
    """Lo que sale de cerrar una nave: la fianza que vuelve (menos lo que se debe), lo que se
    debe por encima de ella, lo que paga el mayorista por lo de dentro (a la mitad) y lo que va
    a Sociedades esa semana (sus cuentas y la venta)."""
    forfeit = min(nave.deposit, nave.rent_debt)
    cost = chain.stock_value(nave)
    liquidation = int(cost * chain.NAVE_LIQUIDATION)
    nave.roll_over(week_of(today))
    return _NaveShut(
        refund=nave.deposit - forfeit,
        forfeit=forfeit,
        owed=nave.rent_debt - forfeit,
        liquidation=liquidation,
        cost=cost,
        base=sl.tax_base(nave.week_totals) + liquidation,
        sales=liquidation,
    )


def _carry(company: dict[str, Any], week: str, base: int, sales: int) -> None:
    """Apunta en la sociedad lo que entra en el Sociedades de la semana de algo que ya no
    está (un local o la nave que se cerró)."""
    carry = company.get("carry") or {}
    if carry.get("week") != week:
        carry = {"week": week, "base": 0, "sales": 0}
    carry["base"] += base
    carry["sales"] += sales
    company["carry"] = carry


def _vacancy(business: Business, employee: dict[str, Any]) -> None:
    """Si hay gerente, apunta la baja de quien se va para que la cubra (no la suya): su
    puesto y su horario."""
    if staff.is_manager(employee) or staff.manager(business) is None:
        return
    business.vacancies = [
        *business.vacancies,
        {
            "jornada": employee["jornada"],
            "shift": employee["shift"],
            "role": employee.get("role", ""),
            "franjas": list(staff.franjas(employee)),
        },
    ]


def _roll_support(business: Business, day: date) -> None:
    """Las faltas de `day` de quien no atiende (el gerente y la plantilla de apoyo) que no se
    decidieron ya con la primera franja del día: se deciden la noche, antes de la nómina. Ese
    día no cobra y no deja su efecto (el gerente no repone)."""
    for employee in business.staff:
        role = staff.role_of(employee)
        if role.plays or not staff.roll_absence(employee, business.seed, day):
            continue
        tail = "Esta noche nadie repone." if role.key == staff.MANAGER else "Hoy no cobra."
        business.tell(
            f"🚫 {employee['name']} no vino hoy y no avisó: es su falta número "
            f"{employee['absences']}. {tail}"
        )


def _same_barrio(owned: list[Business], sector: str, barrio: str) -> None:
    """Que entre `owned` no haya ya un local de `sector` en `barrio`: se repartirían el cupo.

    Raises:
        BusinessError: Si lo hay.
    """
    for other in owned:
        if other.sector == sector and other.barrio == barrio:
            raise BusinessError(
                f"Ya tienes {other.name} ({other.sector_info.name.lower()}) en "
                f"{other.barrio_info.name}: os repartiríais los mismos clientes."
            )


def _last_sales(group: list[Business]) -> int:
    """Lo que vendieron la semana pasada estos locales (la de ahora, si aún no hay pasada)."""
    return sum(b.last_week.get("ventas", b.week_totals.get("ventas", 0)) for b in group)


def _zec_jobs(group: list[Business]) -> tuple[int, str]:
    """`(empleos con alta, isla)` de una sociedad para la ZEC: los de todos sus locales y la
    isla que más empleos pide."""
    jobs = sum(1 for b in group for e in b.staff if e["contract"] != "b")
    island = max((b.barrio_info.island for b in group), key=sl.zec_jobs)
    return jobs, island


def _zec_week(business: Business, group: list[Business]) -> tuple[int, str] | None:
    """`(empleos, isla)` si la sociedad tributa esta semana en la ZEC, o `None`.

    Inscrita, tiene que mantener el empleo mínimo (art. 31.2.e de la Ley 19/1994): una semana
    sin él tributa al tipo de siempre y, con `ZEC_MISSES` seguidas, se la da de baja. Con
    varios locales, los empleos de todos (`_zec_jobs`).
    """
    info = business.company
    if not info.get("zec"):
        return None
    jobs, island = _zec_jobs(group)
    if jobs >= sl.zec_jobs(island):
        info["zec_misses"] = 0
        return jobs, island
    info["zec_misses"] = info.get("zec_misses", 0) + 1
    if info["zec_misses"] >= sl.ZEC_MISSES:
        info["zec"] = False
        business.tell(
            "🏝️ Dos semanas sin el empleo mínimo: tu sociedad sale del registro de la ZEC y "
            "vuelve al tipo de siempre."
        )
    else:
        business.tell(
            f"🏝️ Esta semana no llegaste a {sl.zec_jobs(island)} empleos con alta: Sociedades al "
            "tipo de siempre. Una semana más así y sales de la ZEC."
        )
    return None


def _apply_turn(business: Business, summary: TurnSummary, seed: str, day: date) -> list[Any]:
    """Lo que deja un turno en el negocio (del dueño o de un empleado): el stock que sale, la
    reputación, la fidelidad de los habituales, las ventas de la semana y las reseñas.

    Las reseñas llevan un azar propio del turno (`seed`): no gastan el de las colas.
    """
    for key, units in summary.units.items():
        business.stock[key] = max(0, business.stock.get(key, 0) - units)
    business.note_sold(day, summary.units)
    change = turn_reputation(
        business.sector_info, summary.reputation, summary.served, price_reputation(business)
    )
    business.reputation = max(0.0, min(REPUTATION_MAX, business.reputation + change))
    business.regulars = apply_loyalty(business.regulars, summary.loyalty)
    business.add("ventas", summary.sales)
    rng = random.Random(f"{business.seed}:{seed}")
    left = [r for o in summary.outcomes if (r := review_of(o, rng)) is not None]
    add_reviews(business, day, left)
    return left


def _tail_missing(groups: list[tuple[str, BusinessCost]], unpaid: int) -> dict[str, int]:
    """Lo que no llegó de cada grupo de recibos: los recibos se cobran por orden, así que lo
    que falta es de los últimos."""
    missing: dict[str, int] = {}
    for group, cost in reversed(groups):
        take = min(unpaid, max(0, cost.total))
        if take:
            missing[group] = missing.get(group, 0) + take
            unpaid -= take
    return missing


def _settle_staff(
    business: Business,
    pay: Payroll,
    missing: dict[str, int],
    day: date,
    caught: tuple[int, int] | None,
    *,
    shield: float = 0.0,
) -> list[str]:
    """Lo que pasa con la plantilla al cerrar la noche, ya cobrada. Devuelve lo que ha pasado.

    - Lo pagado de los atrasos baja lo que se le debe a cada uno, del primero al último.
    - Lo que no llegó de las nóminas es de los últimos de la plantilla: se les debe.
    - El humor de cada uno (`business_staff.night_mood`, con lo que quita recursos humanos,
      `shield`, a todos menos a sí mismo, y lo que sube el encargado de turno a quien atendió
      con él), sus cursos y su período de prueba.
    - Quien acaba el contrato temporal (12 días por año, art. 49.1.c ET) o se va: quemado, o
      sin cobrar con la indemnización del improcedente (art. 50 ET). Lo que se les debe va a
      los atrasos, que se pagan los primeros cada noche.
    - Si pasó la Inspección, quien estaba en B pasa a fijo.
    - Quien cambió de horario empieza el nuevo al día siguiente (`horario`).
    """
    events: list[str] = []
    today = day.isoformat()
    company = business.is_company
    back = business.wage_debt - missing.get("atrasos", 0)
    business.wage_debt = missing.get("atrasos", 0)
    for employee in business.staff:
        take = min(employee["unpaid"], back)
        employee["unpaid"] -= take
        back -= take
    short = {"nominas": missing.get("nominas", 0), "negro": missing.get("negro", 0)}
    for employee, slip in reversed(pay.slips):
        group = "negro" if employee["contract"] == "b" else "nominas"
        take = min(short[group], slip.net)
        short[group] -= take
        if take:
            employee["unpaid"] += take
            employee["unpaid_days"] = [*employee["unpaid_days"][-6:], today]
            business.wage_debt += take
            events.append("sin_cobrar")
        elif employee["contract"] == "b":
            employee["b_paid"] = employee.get("b_paid", 0) + slip.gross
        employee["nights"] += 1
        grades = employee["grades"] if employee["grades_day"] == today else []
        staff.night_mood(
            employee,
            paid=not take,
            grades=grades,
            shield=0.0 if employee.get("role") == "rrhh" else shield,
            led=employee.get("led_day") == today,
        )
    if caught is not None:
        fine, arrears = caught
        workers = staff.unregistered(business, day)
        for employee in workers:
            staff.regularize(employee)
        business.tell(
            f"🕵️ Anoche pasó la Inspección de Trabajo y pilló a {len(workers)} sin dar de alta: "
            f"multa de {fine} Y$ y {arrears} Y$ de cuotas atrasadas con recargo. Ahora son "
            "fijos."
        )
        events.append("inspeccion")
    keep = []
    for employee in business.staff:
        name = employee["name"]
        if employee["course"]:
            key = employee["course"]
            employee["skills"][key] = min(100.0, employee["skills"][key] + staff.COURSE_POINTS)
            employee["course"] = ""
            business.tell(
                f"🎓 {name} acabó el curso: +{staff.COURSE_POINTS} de "
                f"{staff.SKILL_NAMES[key].lower()}."
            )
            events.append("curso")
        if employee["trial_until"] == today:
            business.tell(f"✅ {name} ha superado el período de prueba.")
            events.append("prueba_superada")
        if employee["until"] == today:
            _vacancy(business, employee)
            amount = staff.severance(employee, "fin")
            business.wage_debt += amount
            if company:
                business.add("despidos", -amount)
            business.tell(
                f"📄 Se acabó el contrato temporal de {name}: le tocan {amount} Y$ (12 días por "
                "año, art. 49.1.c ET), que se pagan la noche que viene."
            )
            events.append("fin_contrato")
            continue
        if staff.quits(employee, business.seed, day):
            _vacancy(business, employee)
            if staff.unpaid_recent(employee, day) >= staff.UNPAID_NIGHTS_TO_LEAVE:
                amount = (
                    0 if employee["contract"] == "b" else staff.severance(employee, "improcedente")
                )
                business.wage_debt += amount
                if company and amount:
                    business.add("despidos", -amount)
                business.tell(
                    f"⚖️ {name} se va: no le pagas. Pide lo que se le debe"
                    + (
                        f" y la indemnización del despido improcedente, {amount} Y$ (art. 50 ET)."
                        if amount
                        else "."
                    )
                )
                events.append("art50")
            else:
                business.tell(f"👋 {name} se ha despedido: estaba quemado.")
                events.append("dimision")
            if employee["contract"] == "b":
                # Se va enfadado y sin contrato: lo cuenta. La Inspección es más probable.
                business.tipoff = (day + timedelta(days=1)).isoformat()
            continue
        if staff.apply_reschedule(employee, day + timedelta(days=1)):
            schedule = staff.horario_of(employee)
            label = schedule.label.lower() if schedule is not None else ""
            business.tell(f"🕒 Desde hoy, {name} trabaja con su horario nuevo: {label}.")
            events.append("horario")
        keep.append(employee)
    business.staff = keep
    return events


def _opening_payments(quote: OpeningQuote) -> tuple[BusinessCost, ...]:
    """Los pagos de abrir un local: la tasa (gasto) y el acondicionamiento (se amortiza)."""
    return (
        BusinessCost("negocio:apertura", taxes=(("tasa", quote.license),)),
        BusinessCost(
            "negocio:acondicionamiento",
            gone=quote.setup,
            taxes=(("igic", quote.setup_igic),),
            expense=False,
        ),
    )


def _equity(group: list[Business], cash: int, bank: int = 0, nave: Nave | None = None) -> int:
    """El patrimonio neto de una sociedad con estos locales (y su nave), `cash` en caja y `bank`
    debido al banco."""
    assets = sum(stock_value(b) + book_value(b) for b in group)
    debts = sum(b.rent_debt for b in group) + bank
    if nave is not None:
        assets += chain.stock_value(nave)
        debts += nave.rent_debt
    return sl.equity(group[0].company, cash, assets, debts)
