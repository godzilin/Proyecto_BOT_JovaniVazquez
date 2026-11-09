"""Casos de uso del trabajo (`pala`): contratar, fichar, cobrar, ascender y el café.

Junta las reglas (`bot.services.work`), el catálogo (`bot.services.work_catalog`),
los minijuegos (`bot.services.work_games`), la persistencia
(`bot.repositories.work`) y el dinero (`bot.services.economy`). No sabe nada de
Discord: el cog `bot.cogs.work` dibuja y llama a esto.

Todo el dinero pasa por `EconomyService`:

- Turno ordinario o extra: `pay_salary` (nómina con IRPF y Seguridad Social;
  los autónomos, solo IRPF).
- Turno en B, propinas al bolsillo y sobres: `pay_undeclared`. Si te pillan,
  `sanction` (lo cobrado más un 20 % de recargo) y `suspend_imv`.
- Cuota de autónomos: `charge_self_employed_fee`, una vez por semana.
- Baja por accidente: `pay_salary` con la prestación (tributa como
  rendimiento del trabajo, art. 17.1.b LIRPF).
- Café y formación: `purchase`, con IGIC general. Billetes de avión a Hong Kong:
  `purchase` exento (transporte internacional).
- Turnos desde Hong Kong: `pay_foreign_salary` (MPF y salaries tax a Hong
  Kong; IRPF español solo mientras sigue siendo residente, con la exención del
  art. 7.p LIRPF). Al irse, `set_residence` (sin IMV); al volver con 5 «años»
  fuera, Ley Beckham (`pay_salary(beckham=True)`).
- Stock options del CTO: una parte del bruto no se cobra y se acumula. Si la
  startup sale a bolsa o la compran (exit), se cobran: exentas hasta 50.000 €
  (`grant`, Ley 28/2022 para empleados de empresas emergentes) y el resto como
  nómina en especie (`pay_salary`). Si quiebra, se pierden.
- Reparto, funcionariado e influencer no tienen caminos de dinero propios: la
  lluvia, la valoración de la app, los trienios y la viralidad solo cambian el
  bruto del turno, que se cobra como cualquier nómina (`pay_salary`; los
  autónomos, con su cuota, que en los falsos autónomos es la tarifa plana).
  Estudiar la oposición no paga nada.
- Deseo del día: el turno que lo cumple paga `WISH_PAY` veces más; es el mismo
  bruto de la nómina, sin camino propio.
- Pensión de jubilación (`claim_pension`): una vez al día, la pensión de las ramas de
  las que se jubiló (`work.pension_amount`; mientras trabaja, la parte de la jubilación
  activa), por `pay_salary(pension=True)` (rendimiento del trabajo, art. 17.2.a.1ª
  LIRPF: IRPF sin cotizaciones). Quien se jubiló antes de la 8.0.0 nunca cobra menos
  que entonces.
- Veteranía: las ramas completadas en otros oficios suben el bruto del turno
  (`work.veteran_bonus`); es el mismo bruto de la nómina, sin camino propio.

Franjas (`bot.services.agenda`): la jornada reserva las suyas y lo de fuera de ella
(horas extra, B, guardias, estudio) gasta las libres, que comparte el negocio propio. El
negocio entra por `WorkService.business` (`BusinessLink`): cuántas franjas ha usado hoy
y cuándo trabajó por última vez (jubilación activa). La cuota de autónomo es una por
persona y semana (`EconomyService.self_employed_fee_paid`). La reducción de jornada
(`set_jornada`) y la excedencia (`request_excedencia`, `end_excedencia`) no mueven dinero.
"""

from __future__ import annotations

import asyncio
import logging
import random
import sqlite3
import time
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from bot.services.agenda import (
    EXTRA,
    FRANJAS_NOCHE,
    GUARD,
    GUARD_FRANJAS,
    JORNADAS,
    STUDY,
    Agenda,
)
from bot.services.economy import EconomyService, InsufficientFundsError
from bot.services.home import Tier
from bot.services.levels import TIMEZONE, local_day
from bot.services.taxes import (
    EXEMPT_7P_PER_DAY,
    IGIC_GENERAL_RATE,
    ForeignPayslip,
    Payslip,
    igic,
    weekly_yd,
)
from bot.services.telemetry import TimedLock
from bot.services.work import (
    ABROAD_PAY,
    BANKRUPT_CHANCE,
    BATTERY_MAX,
    BATTERY_MIN,
    BECKHAM_WEEKS,
    EVENT_CHANCE,
    EXIT_CHANCE,
    EXIT_MULTIPLIER,
    FAVOR_FLOOR,
    FLIGHT_PRICE,
    GOOD_SCORE,
    GUARD_REST_SECONDS,
    HK_TIMEZONE,
    HONG_KONG,
    IMV_SUSPENSION_SECONDS,
    INSPECTION_SURCHARGE,
    JET_LAG,
    LEAVE_AFTER_DAYS,
    MAX_COFFEES,
    MISSED_GUARD_PENALTY,
    MOM_REPAY_SHARE,
    OPTIONS_SHARE,
    PERFORMANCE_MAX,
    PERFORMANCE_START,
    RAIN_ACCIDENT,
    RAIN_PAY,
    RATING_BLOCK,
    RATING_BLOCK_SECONDS,
    RATING_MAX,
    RATING_MIN,
    RATING_RESET,
    REMOTE_PING_CHANCE,
    SICK_LEAVE_SECONDS,
    SICK_PAY_RATE,
    TASK_STATS,
    TIRED,
    TRIENNIUM_PAY,
    WISH_PAY,
    WISH_PERFORMANCE,
    Branch,
    Career,
    Contract,
    Job,
    Mechanic,
    Position,
    ShiftKind,
    ShiftRecord,
    Wish,
    WishFacts,
    accident_chance,
    active_share,
    agenda_for,
    apply_performance,
    battery_now,
    beckham_eligible,
    catch_up,
    change_family,
    contribution_quality,
    excedencia_blocker,
    excedencia_return_at,
    family_breakdown,
    family_now,
    family_ready_at,
    family_tier,
    follower_gain,
    holiday,
    home_relief,
    inspection_chance,
    inspection_surcharge,
    is_night,
    next_shift_kind,
    on_leave,
    open_branches,
    pension_amount,
    performance_delta,
    promote,
    promotion_blockers,
    rating_delta,
    rating_pay,
    residence_phase,
    rested_at,
    roll,
    roll_over_day,
    settle,
    settle_branch,
    shift_cost,
    shift_pay,
    triennia,
    veteran_bonus,
    viral_roll,
    weather,
    week_of,
    wish_for,
    wish_streak,
)
from bot.services.work_catalog import (
    AVAL_EXECUTION_FAMILY,
    AVAL_SECONDS,
    AVAL_SPENT,
    BUILDER_BRANCH,
    BUILDER_DAYS,
    BUILDER_JOB,
    CAUGHT_SUSPENDS_IMV,
    COFFEE_BY_KEY,
    EVENT_BY_KEY,
    FAVOR_BY_KEY,
    GIFT_BY_KEY,
    JOB_BY_KEY,
    MOTIVATED_TIME,
    PARDON_CHANCE,
    RESIGN_EVENT,
    TUPPER_BATTERY,
    Event,
    Favor,
    Gift,
    events_for,
)
from bot.services.work_games import MiniGame, new_game
from bot.services.work_tools import NO_PERKS, Perks, perks_for, with_rewards

if TYPE_CHECKING:
    from bot.repositories.housing import HousingRepository
    from bot.repositories.work import RetiredBranch, WorkRepository

logger = logging.getLogger(__name__)

#: Exención de las acciones de empresas emergentes entregadas a empleados (art. 42.3.f
#: LIRPF, redacción de la Ley 28/2022): 50.000 € por trabajador y año. La semana es el año
#: fiscal: en Y$, los de una semana con la vara del IRPF, por cada exit.
OPTIONS_EXEMPT = round(weekly_yd(50_000))


def aval_active(contract: Contract, now: float) -> bool:
    """Si el favor del aval sigue en vigor: pedido hace menos de `AVAL_SECONDS` y sin gastar."""
    asked = contract.family_used.get("aval")
    if asked is None or now - asked >= AVAL_SECONDS:
        return False
    return contract.family_used.get(AVAL_SPENT, 0.0) < asked


def rotation_day(now: float) -> int:
    """Día de la rotación de minijuegos (`Position.on`): el de Canarias, como número."""
    return local_day(now).toordinal()


class WorkError(Exception):
    """Algo que impide la acción; el mensaje es para el usuario."""


class BusinessLink(Protocol):
    """Lo que la `pala` necesita saber del negocio propio (`bot.services.business`)."""

    async def franjas_today(self, guild_id: int, user_id: int) -> int:
        """Turnos de hoy en su negocio (0 si no tiene)."""
        ...

    async def last_turn_at(self, guild_id: int, user_id: int) -> float | None:
        """Cuándo abrió su negocio por última vez (`None` si nunca)."""
        ...

    async def reset_franjas(self, guild_id: int, user_id: int) -> int:
        """Pone a cero sus turnos de hoy en el negocio y dice cuántos había."""
        ...


def branch_quality(contract: Contract) -> float:
    """Calidad de lo cotizado en la rama en curso (`work.contribution_quality`).

    Sin días apuntados (una rama empezada antes de la 8.0.0 y sin fichar desde), entera.
    """
    if not contract.branch_days:
        return 1.0
    return contribution_quality(contract.branch_gross / contract.branch_days)


def _legacy_floor(records: Collection[RetiredBranch]) -> int:
    """Lo que cobraba antes de la 8.0.0 por las ramas de entonces (el suelo de su pensión)."""
    floor = 0
    for record in records:
        job = JOB_BY_KEY.get(record.job)
        branch = job.branch(record.branch) if job is not None else None
        if branch is not None and record.legacy:
            floor += branch.pension
    return floor


def _completed(records: Collection[RetiredBranch]) -> list[tuple[str, float]]:
    """`(clase, calidad)` de las ramas jubiladas que siguen en el catálogo."""
    out = []
    for record in records:
        job = JOB_BY_KEY.get(record.job)
        branch = job.branch(record.branch) if job is not None else None
        if branch is not None:
            out.append((branch.kind, record.quality))
    return out


@dataclass(frozen=True, slots=True)
class Status:
    """Foto del contrato para el panel.

    Attributes:
        battery: Batería ahora mismo (con la recarga).
        next_kind: Qué sería el próximo turno.
        blockers: Lo que falta para el ascenso (vacío = ya toca).
        days_in_position: Días distintos fichando en el puesto.
        on_leave: Si vuelve de una excedencia.
        phase: Situación fiscal (`work.residence_phase`).
        weather: El tiempo de esta hora si el oficio lo nota (`""` si no).
        triennia: Trienios cumplidos si el oficio los cobra.
        retired: Ramas de este oficio de las que ya se jubiló.
        wish: El deseo del día (`None` si ya lo cumplió hoy).
        family: Familia ahora mismo (con lo que se ha recuperado sola).
        veteran: Ramas completadas en otros oficios (experiencia y veteranía).
        pension_after: Si puede jubilarse, la pensión entera que tendría al hacerlo (0 si no;
            nunca menos que el suelo de sus jubilaciones de antes de la 8.0.0).
        agenda: Sus franjas de hoy (con las del negocio).
    """

    contract: Contract
    job: Job
    position: Position
    battery: float
    next_kind: ShiftKind
    blockers: list[str]
    days_in_position: int
    trainings: frozenset[str]
    on_leave: bool
    now: float
    phase: str = "casa"
    weather: str = ""
    triennia: int = 0
    retired: frozenset[str] = frozenset()
    wish: Wish | None = None
    family: float = 100.0
    veteran: int = 0
    pension_after: int = 0
    agenda: Agenda = field(default_factory=Agenda)

    @property
    def veteran_bonus(self) -> float:
        """Lo que sube el sueldo del turno por la veteranía (0–0,5)."""
        return veteran_bonus(self.veteran)

    @property
    def fork(self) -> tuple[Branch, ...]:
        """Ramas entre las que elegir en el próximo ascenso (vacío si no toca elegir)."""
        if not self.job.forks(self.contract.level, self.contract.branch):
            return ()
        return open_branches(self.job, self.retired)

    @property
    def branch(self) -> Branch | None:
        """Rama en la que está, si está en una."""
        return self.job.branch(self.contract.branch)

    @property
    def can_retire(self) -> bool:
        """Si puede jubilarse: en lo más alto de una rama de la que no se ha jubilado."""
        return (
            self.branch is not None
            and self.contract.level >= self.job.top
            and self.branch.key not in self.retired
        )

    @property
    def blocked(self) -> bool:
        """Si la app le tiene desconectado por mala valoración."""
        # En una rama ya no hay app que te desconecte, aunque te pillara justo antes.
        return self.job.rates(self.contract.branch) and self.contract.blocked_until > self.now

    @property
    def can_study(self) -> bool:
        """Si en su puesto se estudia la oposición."""
        return self.job.can_study(self.contract.level)

    @property
    def off_duty(self) -> bool:
        """Si está saliente de guardia."""
        return self.contract.off_duty_until > self.now

    @property
    def can_guard(self) -> bool:
        """Si en su puesto hay guardias."""
        return self.job.has_guards(self.contract.level)

    @property
    def can_go_abroad(self) -> bool:
        """Si puede irse a Hong Kong (y aún no está allí)."""
        return self.job.can_go_abroad(self.contract.level) and not self.contract.abroad

    @property
    def beckham(self) -> bool:
        """Si tributa por la Ley Beckham."""
        return self.contract.beckham_until > self.now

    @property
    def promotion_ready(self) -> bool:
        """Si le toca la oferta de ascenso."""
        return not self.blockers

    @property
    def next_position(self) -> Position | None:
        """Puesto siguiente, si lo hay y no toca elegir rama."""
        return self.job.next_position(self.contract.level, self.contract.branch)

    @property
    def sick(self) -> bool:
        """Si está de baja."""
        return self.contract.sick_until > self.now


@dataclass(slots=True)
class Shift:
    """Un turno en marcha: el minijuego y lo que se sabía al fichar."""

    job: Job
    position: Position
    kind: ShiftKind
    game: MiniGame
    battery_before: float
    tremors: bool
    returned_from_leave: bool
    remote: bool = False
    perks: Perks = NO_PERKS
    #: El tiempo al fichar (`""` si el oficio no lo nota).
    weather: str = ""
    #: Franjas de noche que ocupa (`Agenda.late`): cuestan familia (`work.FAMILY_LATE`).
    late: int = 0
    #: Su primera franja de noche (de 1 a `agenda.FRANJAS_NOCHE`; 0 si empieza de día): con
    #: ella sale el riesgo de Inspección del B (`work.inspection_chance`).
    late_franja: int = 0


@dataclass(slots=True)
class ShiftOutcome:
    """Todo lo que ha pasado al cerrar un turno (para el resultado y los logros).

    Attributes:
        payslip: Nómina, si el turno fue declarado.
        black: Lo cobrado en negro (turno en B).
        caught: Si la Inspección te pilló en este turno.
        fine: Lo que te cobraron de multa.
        performance: `"warned"`, `"demoted"` o `None`.
        accident: Si hubo accidente laboral (te vas de baja).
        sick_pay: Prestación cobrada por la baja (bruto).
        intervention: Si la familia llegó a 0 en este turno.
        fee: Cuota de autónomos cobrada en este turno.
        event: Evento que sale, si sale.
        week_taxes: `(impuestos, neto)` de los últimos 7 días.
        remote: Si fue en teletrabajo.
        abroad: País desde donde se trabajó (`""` = en casa).
        phase: Situación fiscal al cobrar (`work.residence_phase`).
        foreign: Nómina de fuera, si se trabajó fuera.
        beckham: Si la nómina fue por la Ley Beckham.
        options_added: Stock options acumuladas en este turno.
        exit_payout: Lo cobrado por un exit (bruto).
        bankrupt: Si la startup quebró (adiós opciones).
        missed_guards: Guardias mínimas que faltaron la semana pasada.
        weather: El tiempo del turno (`""` si el oficio no lo nota).
        rating: Valoración de la app después del turno (reparto).
        blocked: Si la app te ha desconectado en este turno.
        viral: Lo que multiplicó la viralidad el sueldo (influencer; 0 si no aplica).
        followers: Seguidores después del turno; `followers_gained`, los de este turno.
        triennia: Trienios que se cobraron en el turno.
        temario: Preguntas del temario acertadas (turno de estudio).
        wish: El deseo del día que cumplió este turno (`None` si no cumplió ninguno).
        wish_streak: Días seguidos cumpliendo el deseo, si lo cumplió.
        family_parts: Lo que movió la familia el turno, partida a partida.
        mom_repaid: Lo que se le devolvió a tu madre del Bizum en este turno.
        late: Franjas de noche que ocupó (trasnochando).
        late_franja: Su primera franja de noche (de 1 a `agenda.FRANJAS_NOCHE`; 0 de día).
    """

    job: Job
    position: Position
    kind: ShiftKind
    score: int
    gross: int
    balance: int
    battery_before: float
    battery_after: float
    family_before: float
    family_after: float
    now: float
    shifts_today: int
    streak_days: int
    coffees: int
    game: MiniGame
    payslip: Payslip | None = None
    black: int = 0
    caught: bool = False
    fine: int = 0
    performance: str | None = None
    accident: bool = False
    sick_pay: int = 0
    intervention: bool = False
    fee: int = 0
    event: Event | None = None
    promotion_ready: bool = False
    week_taxes: tuple[int, int] = (0, 0)
    remote: bool = False
    abroad: str = ""
    phase: str = "casa"
    foreign: ForeignPayslip | None = None
    beckham: bool = False
    options_added: int = 0
    options_total: int = 0
    exit_payout: int = 0
    bankrupt: bool = False
    missed_guards: int = 0
    weather: str = ""
    rating: int = 0
    blocked: bool = False
    viral: float = 0.0
    followers: int = 0
    followers_gained: int = 0
    triennia: int = 0
    temario: int = 0
    wish: Wish | None = None
    wish_streak: int = 0
    family_parts: list[tuple[str, int]] = field(default_factory=list)
    mom_repaid: int = 0
    late: int = 0
    late_franja: int = 0

    @property
    def night(self) -> bool:
        """Si fue de madrugada donde trabajaba."""
        return is_night(self.now, HK_TIMEZONE if self.abroad else TIMEZONE)

    @property
    def canary_night(self) -> bool:
        """Si en Canarias era de madrugada (trabajando desde lejos: jet lag)."""
        return is_night(self.now)

    @property
    def sunday(self) -> bool:
        """Si fue en domingo."""
        return local_day(self.now).weekday() == 6

    @property
    def holiday(self) -> str | None:
        """Festivo en el que se fichó, si lo era."""
        return holiday(local_day(self.now))

    @property
    def net(self) -> int:
        """Lo que llegó al bolsillo por el turno."""
        if self.foreign is not None:
            return self.foreign.net
        return (self.payslip.net if self.payslip else 0) + (0 if self.caught else self.black)


@dataclass(slots=True)
class EventResult:
    """Lo que pasa al elegir una opción de un evento."""

    event: Event
    text: str
    paid: Payslip | ForeignPayslip | None = None
    black: int = 0
    caught: bool = False
    caught_by: str = ""
    pardoned: bool = False
    fine: int = 0
    demoted: bool = False
    stats: dict[str, int] = field(default_factory=dict)
    #: Evento que viene a continuación (la pregunta de la dimisión).
    follow_up: Event | None = None


@dataclass(frozen=True, slots=True)
class FavorResult:
    """Lo que pasa al pedirle un favor a la familia.

    Attributes:
        favor: El favor.
        family: Familia después.
        amount: Lo que llegó por el Bizum de mamá (0 en los demás).
        balance: Saldo después, si el favor movió dinero.
    """

    favor: Favor
    family: float
    amount: int = 0
    balance: int | None = None


@dataclass(frozen=True, slots=True)
class Pension:
    """La pensión de un miembro.

    Attributes:
        branches: `(oficio, rama)` de las que se jubiló y siguen en el catálogo, por
            orden de jubilación.
        amount: Bruto diario que cobra hoy.
        due: Si hoy aún no la ha cobrado (y hay algo que cobrar).
        full: La pensión entera (`work.pension_amount`), la que cobra sin trabajar.
        share: La parte que cobra (jubilación activa; 1 si no trabaja).
        floor: Lo que cobraba antes de la 8.0.0 por las ramas de entonces (nunca menos).
    """

    branches: tuple[tuple[Job, Branch], ...]
    amount: int
    due: bool
    full: int = 0
    share: float = 1.0
    floor: int = 0


@dataclass(frozen=True, slots=True)
class Retirement:
    """Lo que pasa al jubilarse de una rama.

    Attributes:
        job: El oficio.
        branch: La rama de la que se jubila.
        title: El puesto que deja (lo más alto de la rama).
        following: La rama en la que sigue, desde su primer puesto (`None` si ya
            se jubiló de todas y se queda donde está).
        status: El contrato después.
        quality: Calidad de lo cotizado en la rama (`work.contribution_quality`).
    """

    job: Job
    branch: Branch
    title: str
    following: Branch | None
    status: Status
    quality: float = 1.0


class WorkService:
    """Casos de uso del trabajo.

    Args:
        repository: Persistencia del trabajo.
        economy: Única puerta al dinero.
        clock: Fuente de tiempo (epoch); inyectable en pruebas.
        rng: Azar de minijuegos, accidentes y eventos; inyectable en pruebas.
        housing: `pisos`: quien vive en su casa (o de alquiler en la de otro miembro)
            tiene los bufos de su tramo de `casa` (`home.Tier`). Sin él, como siempre.

    Attributes:
        business: El negocio propio (`BusinessLink`); lo pone `BotClient` después de
            crear los dos servicios. Sin él, nadie tiene negocio.
    """

    def __init__(
        self,
        repository: WorkRepository,
        economy: EconomyService,
        *,
        clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
        housing: HousingRepository | None = None,
    ) -> None:
        self.repository = repository
        self.economy = economy
        self.housing = housing
        self._clock = clock
        self.rng = rng or random.Random()
        self._locks: dict[tuple[int, int], asyncio.Lock] = {}
        self.business: BusinessLink | None = None

    async def _business_franjas(self, guild_id: int, user_id: int) -> int:
        """Turnos de hoy en su negocio; 0 sin negocio o si falla la lectura."""
        if self.business is None:
            return 0
        try:
            return await self.business.franjas_today(guild_id, user_id)
        except (OSError, sqlite3.Error):
            logger.warning("No se pudo mirar el negocio de %s", user_id, exc_info=True)
            return 0

    def now(self) -> float:
        """Hora actual según el reloj del servicio (epoch)."""
        return self._clock()

    async def _contract(self, guild_id: int, user_id: int) -> Contract | None:
        """El contrato, con los bufos de la casa en la que vive (`casa`).

        Simplificación: los bufos nuevos valen desde que se apuntó la batería y la familia
        por última vez, aunque se mudara después.
        """
        contract = await self.repository.contract(guild_id, user_id)
        if contract is not None:
            await self._apply_home(guild_id, user_id, contract)
        return contract

    async def home_tier(self, guild_id: int, user_id: int) -> Tier | None:
        """El tramo de su casa que cuenta ahora, o `None` si vive con sus padres, le han
        cortado la luz o no se puede leer (todo como siempre)."""
        if self.housing is None:
            return None
        try:
            status = await self.housing.home_status(guild_id, user_id)
        except (OSError, sqlite3.Error):
            logger.warning("No se pudo mirar la casa de %s en pisos", user_id, exc_info=True)
            return None
        if status is None or status.cut(local_day(self.now())):
            return None
        return status.comfort.tier

    async def _apply_home(self, guild_id: int, user_id: int, contract: Contract) -> None:
        tier = await self.home_tier(guild_id, user_id)
        if tier is not None:
            contract.home_regen = tier.regen
            contract.home_family = tier.family
            contract.home_strain = tier.strain

    async def _person(self, guild_id: int, user_id: int) -> Contract:
        """Quien no tiene contrato: uno sin oficio que solo lleva su batería (`work_people`).

        La familia sale entera: sin curro no hay nada que se la gaste.
        """
        saved = await self.repository.person_battery(guild_id, user_id)
        battery, at = saved or (float(BATTERY_MAX), 0.0)
        person = Contract(job="", level=0, battery=battery, battery_at=at)
        await self._apply_home(guild_id, user_id, person)
        return person

    def _lock(self, guild_id: int, user_id: int) -> asyncio.Lock:
        key = (guild_id, user_id)
        lock = self._locks.get(key)
        if lock is None:
            # Se quitan los que nadie usa para que el diccionario no crezca sin fin.
            for other, value in list(self._locks.items()):
                if not value.locked():
                    del self._locks[other]
            lock = self._locks[key] = TimedLock("pala:WorkService._locks")
        return lock

    # -- Consulta -----------------------------------------------------------------------

    async def status(self, guild_id: int, user_id: int) -> Status | None:
        """Estado del contrato, o `None` si no trabaja."""
        contract = await self._contract(guild_id, user_id)
        if contract is None:
            return None
        return await self._status(guild_id, user_id, contract)

    async def _status(self, guild_id: int, user_id: int, contract: Contract) -> Status:
        now = self._clock()
        leave = on_leave(contract, now)
        roll_over_day(contract, now)
        job = JOB_BY_KEY[contract.job]
        settle_branch(contract, job)
        days = await self.repository.days_in_position(
            guild_id, user_id, contract.job, contract.level, contract.position_since
        )
        trainings = await self.repository.trainings(guild_id, user_id)
        records = await self.repository.retired_branches(guild_id, user_id)
        retired = frozenset(r.branch for r in records if r.job == job.key)
        veteran = len(records) - len(retired)
        branch = job.branch(contract.branch)
        pension_after = 0
        if branch is not None and contract.level >= job.top and branch.key not in retired:
            completed = [*_completed(records), (branch.kind, branch_quality(contract))]
            pension_after = max(pension_amount(completed), _legacy_floor(records))
        today = local_day(now).isoformat()
        blockers = promotion_blockers(
            contract,
            job,
            days_in_position=days,
            trainings=trainings,
            today=today,
            retired=retired,
            completed_elsewhere=veteran,
        )
        wish = None if contract.wish_day == today else wish_for(user_id, today, job, contract.level)
        earns = job.earns_triennia(contract.level, contract.branch)
        agenda = agenda_for(contract, await self._business_franjas(guild_id, user_id))
        return Status(
            contract=contract,
            job=job,
            # El minijuego del día: de P5 en adelante rota (`Position.on`).
            position=job.position(contract.level, contract.branch).on(rotation_day(now)),
            battery=battery_now(contract, now),
            next_kind=next_shift_kind(contract, now),
            blockers=blockers,
            days_in_position=days,
            trainings=frozenset(trainings),
            on_leave=leave,
            now=now,
            phase=residence_phase(contract, now),
            weather=weather(now) if job.weather else "",
            triennia=triennia(contract, now) if earns else 0,
            retired=retired,
            wish=wish,
            family=family_now(contract, now),
            veteran=veteran,
            pension_after=pension_after,
            agenda=agenda,
        )

    async def agenda(self, guild_id: int, user_id: int) -> Agenda:
        """Las franjas de hoy de un miembro, tenga curro o no (solo lee)."""
        contract = await self.repository.contract(guild_id, user_id)
        if contract is not None:
            roll_over_day(contract, self._clock())
        return agenda_for(contract, await self._business_franjas(guild_id, user_id))

    async def switch_outlook(
        self, guild_id: int, user_id: int, status: Status
    ) -> dict[str, tuple[int, float] | None]:
        """Lo que costaría empezar cada oficio que no ha probado frente al puesto de ahora.

        `{oficio: (días de fichar cobrando menos, lo que sube lo más alto frente a lo de
        ahora)}` con la veteranía y la experiencia que tendría allí (`work.catch_up`);
        `None` si ni arriba cobraría lo de ahora. Los oficios ya empezados no salen: se
        vuelve al puesto en el que se dejaron.
        """
        retired = await self.repository.retirements(guild_id, user_id)
        history = await self.repository.history(guild_id, user_id)
        now_pay = round(status.position.base_pay * (1 + status.veteran_bonus))
        outlook = {}
        for job in JOB_BY_KEY.values():
            if job.key == status.job.key or job.key in history:
                continue
            elsewhere = sum(1 for retired_job, _branch in retired if retired_job != job.key)
            outlook[job.key] = catch_up(job, now_pay, elsewhere)
        return outlook

    async def history(self, guild_id: int, user_id: int) -> dict[str, Career]:
        """`{oficio: Career(puesto en el que se quedó, puesto máximo, rama)}`."""
        return await self.repository.history(guild_id, user_id)

    # -- Contratar ----------------------------------------------------------------------

    async def hire(self, guild_id: int, user_id: int, job_key: str) -> Status:
        """Firma (o cambia de) oficio. Se entra en el puesto en el que se dejó.

        La batería y la familia son de la persona, no del oficio: se conservan. En el primer
        oficio, la batería gastada en el negocio propio (`work_people`) pasa al contrato.

        Raises:
            WorkError: Si el oficio no existe o ya trabaja en él.
        """
        job = JOB_BY_KEY.get(job_key)
        if job is None:
            raise WorkError("Ese oficio no existe (todavía).")
        async with self._lock(guild_id, user_id):
            now = self._clock()
            current = await self._contract(guild_id, user_id)
            if current is not None and current.job == job_key:
                title = job.position(current.level, current.branch).title
                raise WorkError(f"Ya trabajas de {title}.")
            if current is not None and current.abroad:
                raise WorkError("Estás en Hong Kong. Vuelve primero y luego cambias de curro.")
            history = await self.repository.history(guild_id, user_id)
            career = history.get(job_key, Career(1, 1))
            contract = Contract(
                job=job_key,
                level=career.level,
                branch=career.branch,
                position_since=now,
                battery_at=now,
                job_since=now,
            )
            settle_branch(contract, job)
            person = None if current else await self.repository.person_battery(guild_id, user_id)
            if person is not None:
                contract.battery, contract.battery_at = person
            if current is not None:
                settle(current, now)
                for name in (
                    "battery",
                    "family",
                    "family_at",
                    "family_used",
                    "mom_debt",
                    "motivated",
                    "last_shift_at",
                    "shift_day",
                    "shifts_today",
                    "week",
                    "extras_week",
                    "streak_days",
                    "fee_week",
                    "coffee_day",
                    "coffees",
                    "sick_until",
                    "no_extras_day",
                    "guards_week",
                    "guard_week",
                    "off_duty_until",
                    "beckham_until",
                    # La valoración de la app y los seguidores son de la persona.
                    "rating",
                    "blocked_until",
                    "followers",
                    # El deseo del día también: cambiar de curro no da otro.
                    "wish_day",
                    "wish_streak",
                    # Las franjas de hoy y la última excedencia son de la persona.
                    "franjas_day",
                    "franjas",
                    "excedencia_last",
                ):
                    setattr(contract, name, getattr(current, name))
            await self.repository.save_contract(guild_id, user_id, contract)
            return await self._status(guild_id, user_id, contract)

    # -- Fichar -------------------------------------------------------------------------

    async def start_shift(
        self,
        guild_id: int,
        user_id: int,
        *,
        black_ok: bool = False,
        guard: bool = False,
        remote: bool = False,
        study: bool = False,
        tools: Collection[str] = (),
    ) -> Shift:
        """Comprueba que se puede fichar y prepara el minijuego. No guarda nada.

        Args:
            black_ok: Si ya aceptó cobrar en B (cuando el próximo turno sería
                extra pasado el límite legal).
            guard: Si es una guardia (sanidad).
            remote: Si es en teletrabajo (oficina).
            study: Si es un turno de estudio de la oposición (funcionariado).
            tools: Claves de la tienda que tiene el miembro; las herramientas de
                curro que sirvan para el puesto ayudan en el minijuego
                (`bot.services.work_tools`).

        Raises:
            WorkError: Si no puede fichar (sin contrato, de baja, saliente de
                guardia, sin batería, intervención familiar) o si hace falta
                aceptar el B (`NeedsBlack`).
        """
        status = await self.status(guild_id, user_id)
        if status is None:
            raise WorkError("No tienes curro. Abre `pala` y elige uno.")
        now = status.now
        contract = status.contract
        if status.sick:
            raise WorkError(
                f"🩹 Estás de baja por accidente laboral hasta <t:{int(contract.sick_until)}:t>. "
                "La mutua te vigila."
            )
        if status.off_duty:
            raise OffDuty(contract.off_duty_until)
        if status.blocked and not study:
            raise WorkError(
                f"📵 **La app te ha desconectado** por mala valoración hasta "
                f"<t:{int(contract.blocked_until)}:t>. Ni un pedido, ni una explicación."
            )
        if contract.on_excedencia:
            raise WorkError(
                "🏝️ Estás de excedencia. Vuelve al curro desde el menú de la jornada para fichar."
            )
        if status.battery <= BATTERY_MIN:
            back = int(rested_at(contract, status.now))
            raise WorkError(
                "🪫 Estás tan reventado que te quedas dormido de pie. Vuelve a fichar "
                f"<t:{back}:R> o tómate un barraquito."
            )
        if guard and not status.can_guard:
            raise WorkError("En tu puesto no hay guardias.")
        if remote and not status.job.remote:
            raise WorkError("En tu curro no se teletrabaja.")
        if study and not status.can_study:
            raise WorkError("En tu puesto no hay nada que opositar.")
        kind = ShiftKind.STUDY if study else ShiftKind.GUARD if guard else status.next_kind
        if contract.reduced and kind in (ShiftKind.EXTRA, ShiftKind.BLACK, ShiftKind.GUARD):
            raise WorkError(
                "🧸 Con la reducción de jornada no hay horas extra ni guardias: hoy ya has "
                f"hecho tus {contract.jornada} turnos."
            )
        late = late_franja = 0
        if kind is not ShiftKind.ORDINARY:
            need = GUARD_FRANJAS if kind is ShiftKind.GUARD else 1
            if not status.agenda.fits(need):
                raise WorkError(
                    f"🕗 No te quedan franjas hoy (tienes {status.agenda.left} y hacen falta "
                    f"{need}): ya has trasnochado las {FRANJAS_NOCHE} de la noche. Mañana más."
                )
            late, late_franja = status.agenda.late(need), status.agenda.next_night
        if study:
            return self._study_shift(status, tools, late, late_franja)
        if contract.abroad and kind is ShiftKind.BLACK:
            # En Hong Kong no hay límite de horas extra como el del art. 35.2 ET.
            kind = ShiftKind.EXTRA
        today = local_day(now).isoformat()
        if kind is not ShiftKind.ORDINARY and contract.no_extras_day == today:
            raise WorkError(
                "👪 **Intervención familiar.** Hoy tu familia te ha confiscado la pala: "
                "nada de horas extra ni guardias. Mañana ya veremos."
            )
        if kind is ShiftKind.BLACK and not black_ok:
            raise NeedsBlack(late_franja)
        perks = self._perks(status, status.position.mechanic, tools)
        game = new_game(
            status.position,
            self.rng,
            now=now,
            tired=status.battery < TIRED,
            guard=guard,
            perks=perks,
        )
        return Shift(
            job=status.job,
            position=status.position,
            kind=kind,
            game=game,
            battery_before=status.battery,
            tremors=contract.coffees > MAX_COFFEES,
            returned_from_leave=status.on_leave,
            remote=remote,
            perks=perks,
            weather=status.weather,
            late=late,
            late_franja=late_franja,
        )

    @staticmethod
    def _perks(status: Status, mechanic: Mechanic, tools: Collection[str]) -> Perks:
        """Herramientas de la tienda que sirven más las ventajas ganadas en el oficio."""
        contract = status.contract
        perks = with_rewards(
            perks_for(status.job.key, mechanic, tools),
            status.job.perks(contract.level, contract.branch),
        )
        if contract.motivated:
            perks = replace(perks, extra_time=perks.extra_time + MOTIVATED_TIME)
        return perks

    def _study_shift(
        self, status: Status, tools: Collection[str], late: int = 0, late_franja: int = 0
    ) -> Shift:
        """Prepara un turno de estudio: el test del temario, sin sueldo.

        `late` y `late_franja`: si cae trasnochando (ver `Shift`).
        """
        job = status.job
        assert job.study is not None
        position = Position(
            status.contract.level,
            "Estudiando la oposición",
            0,
            Mechanic.DIALOGUE,
            40,
            (),
            0,
            job.study,
        )
        perks = self._perks(status, Mechanic.DIALOGUE, tools)
        game = new_game(
            position, self.rng, now=status.now, tired=status.battery < TIRED, perks=perks
        )
        return Shift(
            job=job,
            position=position,
            kind=ShiftKind.STUDY,
            game=game,
            battery_before=status.battery,
            tremors=status.contract.coffees > MAX_COFFEES,
            returned_from_leave=False,
            perks=perks,
            late=late,
            late_franja=late_franja,
        )

    async def finish_shift(self, guild_id: int, user_id: int, shift: Shift) -> ShiftOutcome:
        """Cierra el turno: cobra, gasta batería, mueve la barra y tira los dados.

        Raises:
            WorkError: Si el contrato cambió mientras jugaba (otro oficio).
        """
        async with self._lock(guild_id, user_id):
            return await self._finish_shift(guild_id, user_id, shift)

    async def _pay(
        self,
        guild_id: int,
        user_id: int,
        contract: Contract,
        gross: int,
        *,
        concept: str,
        now: float,
    ) -> tuple[Payslip | ForeignPayslip, int]:
        """Paga un bruto por el camino que toque: nómina, autónomo, Beckham o Hong Kong."""
        position = JOB_BY_KEY[contract.job].position(contract.level, contract.branch)
        if contract.abroad:
            today = local_day(now).isoformat()
            if contract.exempt_day != today:
                contract.exempt_day, contract.exempt_used = today, 0
            phase = residence_phase(contract, now)
            result = await self.economy.pay_foreign_salary(
                guild_id,
                user_id,
                gross=gross,
                concept=concept,
                resident=phase == "residente",
                exempt_left=EXEMPT_7P_PER_DAY - contract.exempt_used,
            )
            contract.exempt_used += result.payslip.exempt
            return result.payslip, result.balance
        result = await self.economy.pay_salary(
            guild_id,
            user_id,
            gross=gross,
            concept=concept,
            self_employed=position.self_employed,
            beckham=contract.beckham_until > now,
            public=JOB_BY_KEY[contract.job].paid_by_state(position),
        )
        return result.payslip, result.balance

    async def _finish_shift(self, guild_id: int, user_id: int, shift: Shift) -> ShiftOutcome:
        now = self._clock()
        shift.game.finish(now)
        contract = await self._contract(guild_id, user_id)
        if contract is None or contract.job != shift.job.key:
            raise WorkError("Cambiaste de curro a mitad de turno. Este no cuenta.")
        if shift.kind is ShiftKind.STUDY:
            return await self._finish_study(guild_id, user_id, shift, contract, now)
        if shift.returned_from_leave:
            contract.performance = PERFORMANCE_START
            contract.warned = False
        roll_over_day(contract, now)
        today = local_day(now)
        week = week_of(today)
        job = shift.job
        position = shift.position
        kind = shift.kind
        score = shift.game.score()
        battery_before = battery_now(contract, now)
        family_before = family_now(contract, now)
        settle(contract, now)
        concept = f"pala:{job.key}"
        abroad = contract.abroad
        phase = residence_phase(contract, now)

        # Guardias mínimas del residente: se revisan al empezar una semana nueva.
        missed = 0
        if contract.guard_week != week:
            if contract.guard_week:
                required = 0 if contract.reduced else job.required_guards(contract.level)
                missed = max(0, required - contract.guards_week)
                if missed:
                    contract.performance = max(
                        0, contract.performance - missed * MISSED_GUARD_PENALTY
                    )
            contract.guard_week = week
            contract.guards_week = 0

        # Batería, familia, jornada y racha.
        in_branch = contract.level > job.trunk_top
        if contract.shift_day != today.isoformat():
            contract.streak_days += 1
            contract.shift_day = today.isoformat()
            if in_branch:
                contract.branch_days += 1
        cost = shift_cost(kind, remote=shift.remote, calima=shift.weather == "calima")
        contract.battery = battery_before - cost * (1 - shift.perks.battery)
        family_parts = family_breakdown(
            kind,
            now,
            remote=shift.remote,
            tz=HK_TIMEZONE if abroad else TIMEZONE,
            abroad=bool(abroad),
            late=shift.late,
        )
        # Desde Hong Kong no se vuelve a casa al acabar: la casa no rebaja nada.
        family_parts = home_relief(family_parts, 1.0 if abroad else contract.home_strain)
        intervention = change_family(contract, sum(v for _, v in family_parts), now)
        contract.motivated = False
        if kind is ShiftKind.GUARD:
            contract.guards_week += 1
            contract.off_duty_until = now + GUARD_REST_SECONDS
            contract.franjas[GUARD] = contract.franjas.get(GUARD, 0) + GUARD_FRANJAS
        else:
            contract.shifts_today += 1
            if kind is not ShiftKind.ORDINARY:
                contract.extras_week += 1
                contract.franjas[EXTRA] = contract.franjas.get(EXTRA, 0) + 1
        contract.last_shift_at = now

        # Dinero del turno.
        tenured = job.tenured(contract.level, contract.branch)
        gross = shift_pay(position.base_pay, score, kind, tenured=tenured)
        elsewhere = sum(
            1 for j, _b in await self.repository.retirements(guild_id, user_id) if j != job.key
        )
        if elsewhere:
            gross = round(gross * (1 + veteran_bonus(elsewhere)))
        if abroad:
            gross = round(gross * ABROAD_PAY)
        if job.rates(contract.branch):
            gross = round(gross * rating_pay(contract.rating))
        if shift.weather == "lluvia":
            gross = round(gross * RAIN_PAY)
        years = (
            triennia(contract, now) if job.earns_triennia(contract.level, contract.branch) else 0
        )
        gross = round(gross * (1 + TRIENNIUM_PAY * years))
        viral = viral_roll(self.rng) if job.viral else 0.0
        if viral:
            gross = max(1, round(gross * viral))
        wish = self._wish(user_id, contract, job, shift, battery_before, viral, now)
        if wish is not None:
            gross = round(gross * WISH_PAY)
        payslip: Payslip | None = None
        foreign: ForeignPayslip | None = None
        black = fine = options_added = exit_payout = 0
        caught = bankrupt = False
        if kind is ShiftKind.BLACK:
            balance = await self.economy.pay_undeclared(
                guild_id, user_id, amount=gross, concept=job.key
            )
            black = gross
            if roll(self.rng, inspection_chance(shift.late_franja)):
                caught = True
                fine, balance = await self._caught(
                    guild_id,
                    user_id,
                    gross,
                    "inspeccion",
                    surcharge=inspection_surcharge(shift.late_franja),
                )
        else:
            cash = gross
            if job.has_options(contract.level, contract.branch):
                options_added = round(gross * OPTIONS_SHARE)
                cash = gross - options_added
                contract.options += options_added
            slip, balance = await self._pay(
                guild_id, user_id, contract, cash, concept=concept, now=now
            )
            if isinstance(slip, ForeignPayslip):
                foreign = slip
            else:
                payslip = slip
            if in_branch:
                contract.branch_gross += cash

        # Stock options: exit o quiebra (las que ya tienes siguen vivas en cualquier puesto).
        if contract.options:
            if roll(self.rng, EXIT_CHANCE):
                low, high = EXIT_MULTIPLIER
                exit_payout = round(contract.options * self.rng.uniform(low, high))
                contract.options = 0
                balance = await self._cash_options(guild_id, user_id, exit_payout, contract, week)
            elif roll(self.rng, BANKRUPT_CHANCE):
                bankrupt = True
                contract.options = 0

        # Cuota de autónomos, una vez por semana (después de cobrar, para que llegue).
        fee = 0
        if position.self_employed and contract.fee_week != week:
            contract.fee_week = week
            # Una sola cuota por persona: si ya la ha pagado su negocio, no hay otra.
            if not await self.economy.self_employed_fee_paid(guild_id, user_id):
                fee, balance = await self.economy.charge_self_employed_fee(
                    guild_id, user_id, amount=position.fee, concept="autonomos"
                )

        # Tareas y rendimiento (las tareas primero: si te bajan de puesto, se
        # quedan en el puesto que estabas, que es el que se reinicia).
        progress = contract.progress
        game = shift.game
        if score >= GOOD_SCORE:
            progress["good"] = progress.get("good", 0) + 1
        if kind is ShiftKind.GUARD:
            progress["guards"] = progress.get("guards", 0) + 1
        elif kind is not ShiftKind.ORDINARY:
            progress["extras"] = progress.get("extras", 0) + 1
        # Las tareas cuentan con el minijuego de siempre del puesto: el día que rota a
        # otro, lo que se acierta allí suma en el mismo contador.
        usual = position.task_mechanic
        if usual is Mechanic.DIG and game.broken == 0 and game.correct:
            progress["clean"] = progress.get("clean", 0) + 1
        if usual is Mechanic.MEMORY:
            # Una ronda perfecta de memoria vale por dos aciertos del juego del día.
            hits = game.perfect_rounds if game.mechanic is Mechanic.MEMORY else game.correct // 2
            progress["perfect_rounds"] = progress.get("perfect_rounds", 0) + hits
        elif (stat := TASK_STATS.get(usual)) is not None:
            progress[stat] = progress.get(stat, 0) + game.correct
        delta = performance_delta(
            score,
            kind,
            remote=shift.remote,
            cap=job.ordinary_cap,
            family=family_tier(family_before),
        )
        if wish is not None:
            delta += WISH_PERFORMANCE
        performance = apply_performance(
            contract, delta, tenured=job.tenured(contract.level, contract.branch)
        )
        if performance == "demoted":
            contract.position_since = now
            settle_branch(contract, job)

        # Valoración de la app: por debajo del mínimo, desconexión.
        blocked = False
        if job.rates(contract.branch):
            contract.rating = max(
                RATING_MIN, min(RATING_MAX, contract.rating + rating_delta(score))
            )
            if contract.rating < RATING_BLOCK:
                blocked = True
                contract.blocked_until = now + RATING_BLOCK_SECONDS
                contract.rating = RATING_RESET

        # Seguidores: los trae cada turno, más si se hace viral.
        gained = follower_gain(score, contract.level, viral) if job.viral else 0
        contract.followers += gained

        # Accidente laboral (con lluvia, aunque vayas descansado).
        chance = accident_chance(battery_before)
        if shift.weather == "lluvia":
            chance += RAIN_ACCIDENT
        accident = roll(self.rng, chance)
        sick_pay = 0
        if accident:
            contract.sick_until = now + SICK_LEAVE_SECONDS
            # Base reguladora: el bruto declarado de los últimos 30 días, por día.
            month = await self.repository.gross_since(guild_id, user_id, now - 30 * 86_400)
            declared = payslip.gross if payslip else foreign.gross if foreign else 0
            sick_pay = round(SICK_PAY_RATE * (month + declared) / 30)
            if sick_pay > 0:
                _, balance = await self._pay(
                    guild_id, user_id, contract, sick_pay, concept="pala:baja", now=now
                )

        net = payslip.net if payslip else foreign.net if foreign else 0 if caught else black
        mom_repaid = 0
        if contract.mom_debt and net > 0:
            # Nunca más de lo que hay: la cuota o una multa pueden haberse llevado parte.
            mom_repaid = min(contract.mom_debt, max(1, round(net * MOM_REPAY_SHARE)), balance)
            if mom_repaid > 0:
                balance = await self.economy.repay_mom(guild_id, user_id, amount=mom_repaid)
                contract.mom_debt -= mom_repaid
            mom_repaid = max(0, mom_repaid)
        await self.repository.save_contract(guild_id, user_id, contract)
        await self.repository.add_shift(
            guild_id,
            user_id,
            ShiftRecord(
                job=job.key,
                level=position.level,
                day=today.isoformat(),
                created_at=now,
                score=score,
                kind=kind.value,
                gross=gross,
                net=net,
            ),
        )

        event = None
        if not accident:
            if shift.remote and roll(self.rng, REMOTE_PING_CHANCE):
                event = EVENT_BY_KEY["desconexion"]
            elif roll(self.rng, EVENT_CHANCE):
                options = events_for(
                    job.key,
                    contract.level,
                    contract.family,
                    abroad=bool(abroad),
                    phase=phase,
                    branch=contract.branch,
                )
                event = self.rng.choice(options) if options else None

        status = await self._status(guild_id, user_id, contract)
        week_taxes = await self.economy.week_tax_burden(guild_id, user_id)
        return ShiftOutcome(
            job=job,
            position=position,
            kind=kind,
            score=score,
            gross=gross,
            balance=balance,
            battery_before=battery_before,
            battery_after=contract.battery,
            family_before=family_before,
            family_after=contract.family,
            now=now,
            shifts_today=contract.shifts_today,
            streak_days=contract.streak_days,
            coffees=contract.coffees,
            game=game,
            payslip=payslip,
            black=black,
            caught=caught,
            fine=fine,
            performance=performance,
            accident=accident,
            sick_pay=sick_pay,
            intervention=intervention,
            fee=fee,
            event=event,
            promotion_ready=status.promotion_ready,
            week_taxes=week_taxes,
            remote=shift.remote,
            abroad=abroad,
            phase=phase,
            foreign=foreign,
            beckham=payslip is not None and contract.beckham_until > now,
            options_added=options_added,
            options_total=contract.options,
            exit_payout=exit_payout,
            bankrupt=bankrupt,
            missed_guards=missed,
            weather=shift.weather,
            rating=contract.rating if job.rates(contract.branch) else 0,
            blocked=blocked,
            viral=viral,
            followers=contract.followers if job.viral else 0,
            followers_gained=gained,
            triennia=years,
            wish=wish,
            wish_streak=contract.wish_streak if wish is not None else 0,
            family_parts=family_parts,
            mom_repaid=mom_repaid,
            late=shift.late,
            late_franja=shift.late_franja,
        )

    def _wish(
        self,
        user_id: int,
        contract: Contract,
        job: Job,
        shift: Shift,
        battery_before: float,
        viral: float,
        now: float,
    ) -> Wish | None:
        """El deseo del día, si este turno lo cumple (y lo apunta en el contrato).

        Se mira con el puesto de antes de mover la barra, el mismo que veía el
        panel al fichar. Debe llamarse después de contar el turno en la jornada.
        """
        today = local_day(now)
        if contract.wish_day == today.isoformat():
            return None
        game = shift.game
        facts = WishFacts(
            score=game.score(),
            kind=shift.kind,
            battery_before=battery_before,
            hour=datetime.fromtimestamp(now, TIMEZONE).hour,
            weather=shift.weather,
            time_left=game.time_left,
            seconds=game.seconds,
            completed=game.completed,
            shifts_today=contract.shifts_today,
            remote=shift.remote,
            viral=viral,
        )
        wish = wish_for(user_id, today.isoformat(), job, contract.level)
        if not wish.met(facts):
            return None
        contract.wish_streak = wish_streak(contract, today)
        contract.wish_day = today.isoformat()
        return wish

    async def _finish_study(
        self, guild_id: int, user_id: int, shift: Shift, contract: Contract, now: float
    ) -> ShiftOutcome:
        """Cierra un turno de estudio: cansa y suma temario, pero no paga ni es jornada.

        No se guarda en `work_shifts`: estudiar no cuenta como día en el puesto
        ni entra en la base de la baja. Tampoco hay accidentes ni eventos.
        """
        roll_over_day(contract, now)
        battery_before = battery_now(contract, now)
        family_before = family_now(contract, now)
        settle(contract, now)
        contract.battery = battery_before - shift_cost(ShiftKind.STUDY)
        family_parts = home_relief(
            family_breakdown(ShiftKind.STUDY, now, late=shift.late), contract.home_strain
        )
        intervention = change_family(contract, sum(v for _, v in family_parts), now)
        contract.motivated = False
        contract.franjas[STUDY] = contract.franjas.get(STUDY, 0) + 1
        game = shift.game
        contract.progress["temario"] = contract.progress.get("temario", 0) + game.correct
        await self.repository.save_contract(guild_id, user_id, contract)
        status = await self._status(guild_id, user_id, contract)
        balance = await self.economy.balance(guild_id, user_id)
        return ShiftOutcome(
            job=shift.job,
            position=shift.position,
            kind=ShiftKind.STUDY,
            score=game.score(),
            gross=0,
            balance=balance,
            battery_before=battery_before,
            battery_after=contract.battery,
            family_before=family_before,
            family_after=contract.family,
            now=now,
            shifts_today=contract.shifts_today,
            streak_days=contract.streak_days,
            coffees=contract.coffees,
            game=game,
            intervention=intervention,
            promotion_ready=status.promotion_ready,
            temario=game.correct,
            family_parts=family_parts,
            late=shift.late,
            late_franja=shift.late_franja,
        )

    async def _cash_options(
        self, guild_id: int, user_id: int, payout: int, contract: Contract, week: str
    ) -> int:
        """Cobra un exit: exento hasta `OPTIONS_EXEMPT` y el resto como nómina.

        Tratamiento fiscal: rendimiento del trabajo en especie, con la exención
        de la Ley 28/2022 para las acciones de empresas emergentes entregadas a
        sus empleados (50.000 € al año: aquí, por semana, y la lleva el contrato en
        `options_exempt`). La parte exenta va por `grant`; el resto, por `pay_salary`
        (IRPF y cotización, como cualquier retribución en especie).

        Returns:
            El saldo final.
        """
        if contract.options_week != week:
            contract.options_week, contract.options_exempt = week, 0
        exempt = min(payout, max(0, OPTIONS_EXEMPT - contract.options_exempt))
        contract.options_exempt += exempt
        balance = await self.economy.balance(guild_id, user_id)
        if exempt:
            balance = await self.economy.grant(
                guild_id, user_id, amount=exempt, reason="pala:opciones_exentas"
            )
        if payout > exempt:
            balance = (
                await self.economy.pay_salary(
                    guild_id, user_id, gross=payout - exempt, concept="pala:opciones"
                )
            ).balance
        return balance

    async def _caught(
        self,
        guild_id: int,
        user_id: int,
        black: int,
        by: str,
        *,
        surcharge: float = INSPECTION_SURCHARGE,
    ) -> tuple[int, int]:
        """Regulariza lo cobrado en negro: lo devuelves con recargo (`surcharge`).

        La Inspección de Trabajo y la UCO, además, te dejan sin IMV unos días.

        Returns:
            `(multa cobrada, saldo final)`.
        """
        amount = round(black * (1 + surcharge))
        fine, balance = await self.economy.sanction(guild_id, user_id, amount=amount, concept=by)
        if by in CAUGHT_SUSPENDS_IMV:
            await self.economy.suspend_imv(guild_id, user_id, seconds=IMV_SUSPENSION_SECONDS)
        return fine, balance

    # -- Hong Kong ----------------------------------------------------------------------

    async def move_abroad(self, guild_id: int, user_id: int) -> Status:
        """Se va a trabajar a Hong Kong: billete, jet lag y adiós al IMV.

        Tratamiento fiscal del billete: una compra exenta de IGIC (el transporte aéreo
        internacional lo está, art. 12.13 de la Ley 20/1991). Todo va a la caja de la
        tienda; al Estado, nada.

        Raises:
            WorkError: Si su puesto no puede, ya está fuera o no le llega.
        """
        async with self._lock(guild_id, user_id):
            status = await self.status(guild_id, user_id)
            if status is None:
                raise WorkError("No tienes curro.")
            if not status.can_go_abroad:
                raise WorkError("Desde tu puesto no te mandan a Hong Kong (todavía).")
            await self._fly(guild_id, user_id, "vuelo:hk")
            contract = status.contract
            now = status.now
            contract.abroad = HONG_KONG
            contract.abroad_since = now
            contract.beckham_until = 0.0
            contract.exempt_day, contract.exempt_used = "", 0
            contract.battery = max(float(BATTERY_MIN), battery_now(contract, now) + JET_LAG)
            contract.battery_at = now
            await self.economy.set_residence(guild_id, user_id, HONG_KONG)
            await self.repository.save_contract(guild_id, user_id, contract)
            return await self._status(guild_id, user_id, contract)

    async def come_home(self, guild_id: int, user_id: int) -> tuple[Status, bool, float]:
        """Vuelve a España. Con 5 «años» fuera, le toca la Ley Beckham.

        Returns:
            `(estado, si le toca la Ley Beckham, días que ha estado fuera)`.

        Raises:
            WorkError: Si no está fuera o no le llega para el billete.
        """
        async with self._lock(guild_id, user_id):
            status = await self.status(guild_id, user_id)
            if status is None or not status.contract.abroad:
                raise WorkError("No estás fuera.")
            await self._fly(guild_id, user_id, "vuelo:vuelta")
            contract = status.contract
            now = status.now
            beckham = beckham_eligible(contract, now)
            days = (now - contract.abroad_since) / 86_400
            contract.abroad = ""
            contract.abroad_since = 0.0
            if beckham:
                contract.beckham_until = now + BECKHAM_WEEKS * 7 * 86_400
            await self.economy.set_residence(guild_id, user_id, None)
            await self.repository.save_contract(guild_id, user_id, contract)
            return await self._status(guild_id, user_id, contract), beckham, days

    async def _fly(self, guild_id: int, user_id: int, concept: str) -> None:
        try:
            await self.economy.purchase(
                guild_id,
                user_id,
                base=FLIGHT_PRICE,
                tax=0,
                concept=concept,
                reserve=lambda _connection: None,
            )
        except InsufficientFundsError as error:
            raise WorkError(f"No te llega para el billete: {FLIGHT_PRICE} Y$.") from error

    # -- Eventos ------------------------------------------------------------------------

    async def resolve_event(
        self, guild_id: int, user_id: int, event_key: str, option: int
    ) -> EventResult:
        """Aplica la opción elegida de un evento.

        Raises:
            WorkError: Si el evento no existe o ya no tiene contrato.
        """
        event = RESIGN_EVENT if event_key == RESIGN_EVENT.key else EVENT_BY_KEY.get(event_key)
        if event is None or option not in (0, 1):
            raise WorkError("Ese marrón ya no existe.")
        async with self._lock(guild_id, user_id):
            contract = await self._contract(guild_id, user_id)
            if contract is None:
                raise WorkError("No tienes curro.")
            now = self._clock()
            job = JOB_BY_KEY[contract.job]
            base = job.position(contract.level, contract.branch).base_pay
            outcome = event.options[option][1]
            result = EventResult(event=event, text=outcome.text)
            if outcome.stat:
                result.stats[outcome.stat] = 1
            if outcome.pay > 0:
                result.paid, _ = await self._pay(
                    guild_id,
                    user_id,
                    contract,
                    max(1, round(base * outcome.pay)),
                    concept=f"pala:{contract.job}",
                    now=now,
                )
            if outcome.black > 0:
                result.black = max(1, round(base * outcome.black))
                await self.economy.pay_undeclared(
                    guild_id, user_id, amount=result.black, concept=f"{contract.job}:{event.key}"
                )
                if roll(self.rng, outcome.risk):
                    result.caught = True
                    result.caught_by = outcome.caught_by
                    if outcome.caught_by == "uco" and roll(self.rng, PARDON_CHANCE):
                        result.pardoned = True
                        result.stats["work_pardoned"] = 1
                    else:
                        result.fine, _ = await self._caught(
                            guild_id, user_id, result.black, outcome.caught_by
                        )
                    result.stats[f"work_caught_{outcome.caught_by}"] = 1
                    if outcome.caught_by == "uco" and contract.job == "politica":
                        result.follow_up = RESIGN_EVENT
            elif outcome.fine > 0 and roll(self.rng, outcome.risk):
                result.caught = True
                result.caught_by = outcome.caught_by
                amount = max(1, round(base * outcome.fine))
                result.fine, _ = await self.economy.sanction(
                    guild_id, user_id, amount=amount, concept=outcome.caught_by
                )
                result.stats[f"work_caught_{outcome.caught_by}"] = 1
            contract.performance = max(
                0, min(PERFORMANCE_MAX, contract.performance + outcome.performance)
            )
            if outcome.family:
                change_family(contract, outcome.family, now)
            if outcome.rating and job.rates(contract.branch):
                contract.rating = max(RATING_MIN, min(RATING_MAX, contract.rating + outcome.rating))
            if outcome.followers and job.viral:
                contract.followers = max(0, round(contract.followers * (1 + outcome.followers)))
            if outcome.battery:
                contract.battery = min(
                    float(BATTERY_MAX), battery_now(contract, now) + outcome.battery
                )
                contract.battery_at = now
            if outcome.demote and contract.level > 1:
                contract.level -= 1
                contract.performance = PERFORMANCE_START
                contract.progress = {}
                contract.position_since = now
                contract.legacy_days = False
                settle_branch(contract, job)
                result.demoted = True
            if event is not RESIGN_EVENT:
                contract.progress["events"] = contract.progress.get("events", 0) + 1
            await self.repository.save_contract(guild_id, user_id, contract)
            return result

    # -- Ascensos -----------------------------------------------------------------------

    async def accept_promotion(self, guild_id: int, user_id: int, branch: str = "") -> Status:
        """Acepta el ascenso si toca.

        Args:
            branch: Rama elegida, obligatoria si el ascenso sale del tronco (y
                entonces tiene que ser una de las que quedan, `Status.fork`).

        Raises:
            WorkError: Si todavía no toca (con lo que falta) o la rama no vale.
        """
        async with self._lock(guild_id, user_id):
            status = await self.status(guild_id, user_id)
            if status is None:
                raise WorkError("No tienes curro.")
            if not status.promotion_ready:
                raise WorkError("Aún no te toca:\n- " + "\n- ".join(status.blockers))
            if status.fork:
                if branch not in {b.key for b in status.fork}:
                    raise WorkError("Elige una de las ramas que te ofrecen.")
            else:
                branch = ""
            promote(status.contract, status.now, branch)
            await self.repository.save_contract(guild_id, user_id, status.contract)
            return await self._status(guild_id, user_id, status.contract)

    async def force_promotion(
        self, guild_id: int, user_id: int, steps: int = 1, branch: str = ""
    ) -> tuple[int, Status]:
        """Sube `steps` puestos sin pedir nada: el `enchufe` de Yeyo (`bot.cogs.admin`).

        Cada subida es un ascenso normal (`promote`). Para en lo más alto o donde toca
        elegir rama, si `branch` (clave o nombre) no es una de las que quedan.

        Returns:
            Cuántos puestos ha subido y cómo queda.

        Raises:
            WorkError: Si no tiene curro.
        """
        async with self._lock(guild_id, user_id):
            status = await self.status(guild_id, user_id)
            if status is None:
                raise WorkError("No tiene curro.")
            contract, job = status.contract, status.job
            wanted = branch.strip().casefold()
            done = 0
            while done < steps and contract.level < job.top:
                chosen = ""
                if job.forks(contract.level, contract.branch):
                    options = open_branches(job, status.retired)
                    match = [b for b in options if wanted in (b.key, b.name.casefold())]
                    if not match:
                        break
                    chosen = match[0].key
                promote(contract, status.now, chosen)
                done += 1
            if not done:
                return 0, status
            await self.repository.save_contract(guild_id, user_id, contract)
            return done, await self._status(guild_id, user_id, contract)

    async def retire(self, guild_id: int, user_id: int) -> Retirement:
        """Se jubila de la rama en la que está (en lo más alto de ella).

        Desde ese mismo día cobra su pensión (`claim_pension`). Sigue en el oficio
        por la otra rama, desde su primer puesto, con la barra a la mitad; si ya
        se jubiló de todas, se queda donde está.

        Raises:
            WorkError: Si no está en lo más alto de una rama o ya se jubiló de ella.
        """
        async with self._lock(guild_id, user_id):
            status = await self.status(guild_id, user_id)
            if status is None or not status.can_retire:
                raise WorkError("Para jubilarte tienes que llegar a lo más alto de una rama.")
            job, branch = status.job, status.branch
            assert branch is not None
            contract = status.contract
            title = status.position.title
            quality = branch_quality(contract)
            contract.branch_gross = contract.branch_days = 0
            remaining = open_branches(job, status.retired | {branch.key})
            following = remaining[0] if remaining else None
            if following is not None:
                contract.branch = following.key
                contract.level = job.trunk_top + 1
                contract.performance = PERFORMANCE_START
                contract.warned = False
                contract.progress = {}
                contract.position_since = status.now
                contract.legacy_days = False
                contract.declined_day = ""
            try:
                await self.repository.retire(
                    guild_id, user_id, branch.key, contract, status.now, quality
                )
            except ValueError as error:
                raise WorkError(str(error)) from error
            after = await self._status(guild_id, user_id, contract)
            return Retirement(job, branch, title, following, after, quality)

    async def pension(self, guild_id: int, user_id: int) -> Pension:
        """La pensión de un miembro (con o sin contrato).

        Una sola pensión por todas las ramas (`work.pension_amount`). Mientras trabaja
        (ha fichado en los últimos `LEAVE_AFTER_DAYS` días) cobra la parte de la jubilación activa,
        que sube cada semana desde la primera jubilación (`work.active_share`). Quien se
        jubiló antes de la 8.0.0 cobra al menos la suma de las pensiones de entonces.
        """
        now = self._clock()
        records = await self.repository.retired_branches(guild_id, user_id)
        branches: list[tuple[Job, Branch]] = []
        for record in records:
            job = JOB_BY_KEY.get(record.job)
            branch = job.branch(record.branch) if job is not None else None
            if job is not None and branch is not None:
                branches.append((job, branch))
        floor = _legacy_floor(records)
        full = pension_amount(_completed(records))
        share = 1.0
        # Trabaja quien ha fichado en los últimos días, tenga contrato o no: dimitir justo
        # antes de cobrar no da la pensión entera.
        last = await self.repository.last_shift_at(guild_id, user_id) if records else None
        if records and self.business is not None:
            # La jubilación activa vale también por cuenta propia (art. 214 TRLGSS).
            try:
                turn = await self.business.last_turn_at(guild_id, user_id)
            except (OSError, sqlite3.Error):
                logger.warning("No se pudo mirar el negocio de %s", user_id, exc_info=True)
                turn = None
            if turn is not None and (last is None or turn > last):
                last = turn
        if last is not None and now - last < LEAVE_AFTER_DAYS * 86_400:
            first = min(record.retired_at for record in records)
            share = active_share(int(max(0.0, now - first) // (7 * 86_400)))
        amount = max(floor, round(full * share))
        today = local_day(now).isoformat()
        claimed = await self.repository.pension_day(guild_id, user_id)
        return Pension(tuple(branches), amount, amount > 0 and claimed != today, full, share, floor)

    async def dues(self, guild_id: int, user_id: int) -> tuple[int, int]:
        """Lo que el trabajo aún le va a cobrar: `(cuota de autónomo, deuda con su madre)`.

        La cuota es la de esta semana si es autónomo y aún no ha fichado (se cobra en el
        primer turno); la deuda, lo que queda del Bizum de mamá (se devuelve con cada
        turno). Solo lee (para `saldo`): `(0, 0)` si no tiene contrato.
        """
        contract = await self.repository.contract(guild_id, user_id)
        job = JOB_BY_KEY.get(contract.job) if contract is not None else None
        if contract is None or job is None:
            return 0, 0
        try:
            position = job.position(contract.level, contract.branch)
        except ValueError:  # un puesto que ya no está en el catálogo
            return 0, contract.mom_debt
        week = week_of(local_day(self._clock()))
        fee = position.fee if position.self_employed and contract.fee_week != week else 0
        if fee and await self.economy.self_employed_fee_paid(guild_id, user_id):
            fee = 0
        return fee, contract.mom_debt

    async def claim_pension(self, guild_id: int, user_id: int) -> tuple[Payslip, int]:
        """Cobra la pensión del día.

        Tratamiento fiscal: rendimiento del trabajo (art. 17.2.a.1ª LIRPF), con
        retención de IRPF y sin cotizaciones (`pay_salary(pension=True)`). El
        día se apunta antes de pagar, para que dos clics no cobren dos veces; si
        el pago falla, se deshace.

        Returns:
            `(nómina, saldo final)`.

        Raises:
            WorkError: Si no tiene pensión o ya la cobró hoy.
        """
        async with self._lock(guild_id, user_id):
            pension = await self.pension(guild_id, user_id)
            if not pension.amount:
                raise WorkError("No tienes pensión. Para eso hay que jubilarse.")
            today = local_day(self._clock()).isoformat()
            previous = await self.repository.claim_pension_day(guild_id, user_id, today)
            if previous is None:
                raise WorkError("Ya cobraste la pensión de hoy. Vuelve mañana.")
            try:
                result = await self.economy.pay_salary(
                    guild_id, user_id, gross=pension.amount, concept="pala:pension", pension=True
                )
            except BaseException:
                await self.repository.restore_pension_day(guild_id, user_id, previous)
                raise
            return result.payslip, result.balance

    # -- Jornada y excedencia --------------------------------------------------------

    async def set_jornada(self, guild_id: int, user_id: int, franjas: int) -> Status:
        """Pide la reducción de jornada (o volver a la completa) para mañana.

        Reducción de jornada por cuidado de un familiar (art. 37.6 ET): de 1/8 a 1/2 de la
        jornada, con el sueldo reducido en proporción (cada turno paga lo mismo; hay menos).
        El preaviso (art. 37.7 ET) es el cambio de día. Pedir el tramo que ya se tiene
        anula lo pedido. No mueve dinero.

        Raises:
            WorkError: Sin contrato, de excedencia o con un tramo que no existe.
        """
        if franjas not in JORNADAS:
            raise WorkError("Ese tramo de jornada no existe.")
        async with self._lock(guild_id, user_id):
            now = self._clock()
            contract = await self._contract(guild_id, user_id)
            if contract is None:
                raise WorkError("No tienes curro. Abre `pala` y elige uno.")
            if contract.on_excedencia:
                raise WorkError("Estás de excedencia: primero vuelve al curro.")
            roll_over_day(contract, now)
            if franjas == contract.jornada:
                contract.jornada_next, contract.jornada_from = 0, ""
            else:
                tomorrow = local_day(now).toordinal() + 1
                contract.jornada_next = franjas
                contract.jornada_from = datetime.fromordinal(tomorrow).date().isoformat()
            await self.repository.save_contract(guild_id, user_id, contract)
            return await self._status(guild_id, user_id, contract)

    async def request_excedencia(self, guild_id: int, user_id: int) -> Status:
        """Pide la excedencia voluntaria (art. 46.2 ET): deja de reservar franjas.

        Lo que impide pedirla está en `work.excedencia_blocker`. El contrato se conserva,
        pero no se ficha hasta volver (`end_excedencia`). No mueve dinero.

        Raises:
            WorkError: Sin contrato o si no se puede pedir.
        """
        async with self._lock(guild_id, user_id):
            now = self._clock()
            contract = await self._contract(guild_id, user_id)
            if contract is None:
                raise WorkError("No tienes curro. Abre `pala` y elige uno.")
            if (blocker := excedencia_blocker(contract, now)) is not None:
                raise WorkError(blocker)
            roll_over_day(contract, now)
            settle(contract, now)
            contract.excedencia_since = now
            contract.jornada_next, contract.jornada_from = 0, ""
            await self.repository.save_contract(guild_id, user_id, contract)
            return await self._status(guild_id, user_id, contract)

    async def end_excedencia(self, guild_id: int, user_id: int) -> Status:
        """Vuelve de la excedencia al mismo puesto, con la barra a la mitad.

        En la vida real solo hay derecho preferente al reingreso cuando haya vacante
        (art. 46.5 ET); aquí se vuelve al mismo puesto (simplificación del proyecto), como
        al volver tras `LEAVE_AFTER_DAYS` días sin fichar. No mueve dinero.

        Raises:
            WorkError: Sin contrato, sin excedencia o antes de los «cuatro meses».
        """
        async with self._lock(guild_id, user_id):
            now = self._clock()
            contract = await self._contract(guild_id, user_id)
            if contract is None or not contract.on_excedencia:
                raise WorkError("No estás de excedencia.")
            back = excedencia_return_at(contract)
            if now < back:
                raise WorkError(
                    "La excedencia dura al menos cuatro «meses» (art. 46.2 ET). Podrás volver "
                    f"<t:{int(back)}:R>."
                )
            contract.excedencia_since = 0.0
            contract.excedencia_last = now
            contract.performance = PERFORMANCE_START
            contract.warned = False
            await self.repository.save_contract(guild_id, user_id, contract)
            return await self._status(guild_id, user_id, contract)

    async def spend_battery(self, guild_id: int, user_id: int, cost: float) -> float:
        """Gasta batería por un turno en el negocio propio: la batería es de la persona.

        Sin contrato se gasta la de `work_people`, que pasa al contrato al firmar el primer
        oficio. Devuelve la batería de antes del turno (para saber si iba reventado). No
        mueve dinero.
        """
        async with self._lock(guild_id, user_id):
            now = self._clock()
            contract = await self._contract(guild_id, user_id)
            if contract is None:
                before = battery_now(await self._person(guild_id, user_id), now)
                await self.repository.save_person_battery(guild_id, user_id, before - cost, now)
                return before
            before = battery_now(contract, now)
            settle(contract, now)
            contract.battery = before - cost
            await self.repository.save_contract(guild_id, user_id, contract)
            return before

    async def reset_day(self, guild_id: int, user_id: int) -> bool:
        """Empieza el día de nuevo: el `amanece` de Yeyo (`bot.cogs.admin`).

        Vacía las franjas de hoy del curro (jornada fichada, horas extra, guardias y
        estudio) y del negocio, el límite semanal de horas extra, y llena la batería.
        El saliente de guardia sigue. Sin contrato, solo la batería y el negocio.

        Si devuelve algo ya gastado, lo que cobre hasta medianoche no lo ve el banco
        (`EconomyService.hide_from_bank`): no se nota en lo que le presta.

        Returns:
            Si tiene contrato.
        """
        wiped = 0
        async with self._lock(guild_id, user_id):
            now = self._clock()
            contract = await self._contract(guild_id, user_id)
            if contract is None:
                await self.repository.save_person_battery(
                    guild_id, user_id, float(BATTERY_MAX), now
                )
            else:
                roll_over_day(contract, now)
                wiped = contract.shifts_today + sum(contract.franjas.values())
                wiped += contract.extras_week
                settle(contract, now)
                contract.battery = float(BATTERY_MAX)
                contract.shifts_today = contract.extras_week = 0
                contract.franjas = {}
                await self.repository.save_contract(guild_id, user_id, contract)
        # Fuera del candado: el negocio coge el suyo y luego el de la pala (`start_turn`).
        if self.business is not None:
            wiped += await self.business.reset_franjas(guild_id, user_id)
        if wiped:
            # shortcut: esconde todo lo cobrado hasta medianoche aunque le quedaran franjas
            # sin gastar, y no los días siguientes de la semana con las horas extra libres;
            # para afinar, contar turno a turno las franjas devueltas.
            midnight = datetime.combine(
                local_day(now) + timedelta(days=1), datetime.min.time(), TIMEZONE
            )
            await self.economy.hide_from_bank(guild_id, user_id, now, midnight.timestamp())
        return contract is not None

    async def battery(self, guild_id: int, user_id: int) -> float:
        """Batería de ahora (con la recarga), tenga contrato o no. Solo lee."""
        contract = await self._contract(guild_id, user_id)
        return battery_now(contract or await self._person(guild_id, user_id), self._clock())

    async def decline_promotion(self, guild_id: int, user_id: int) -> None:
        """Rechaza el ascenso de hoy (mañana te lo vuelven a ofrecer)."""
        async with self._lock(guild_id, user_id):
            contract = await self._contract(guild_id, user_id)
            if contract is None:
                raise WorkError("No tienes curro.")
            contract.declined_day = local_day(self._clock()).isoformat()
            await self.repository.save_contract(guild_id, user_id, contract)

    # -- Compras ------------------------------------------------------------------------

    async def buy_training(self, guild_id: int, user_id: int) -> tuple[str, int, int, int]:
        """Compra la formación del puesto siguiente.

        Tratamiento fiscal: una compra más, con IGIC general (ver
        `EconomyService.purchase`). Simplificación: algunas formaciones reales
        estarían exentas (enseñanza reglada), pero aquí todas pagan el general.

        Returns:
            `(nombre, base, igic, saldo final)`.

        Raises:
            WorkError: Si no hay formación pendiente, ya la tiene o no le llega.
        """
        status = await self.status(guild_id, user_id)
        if status is None:
            raise WorkError("No tienes curro.")
        following = status.next_position
        if following is None or following.training is None:
            raise WorkError("Para tu próximo puesto no hace falta ningún curso.")
        training = following.training
        tax = igic(training.price, IGIC_GENERAL_RATE)
        try:
            _, balance = await self.economy.purchase(
                guild_id,
                user_id,
                base=training.price,
                tax=tax,
                concept=f"formacion:{training.key}",
                reserve=self.repository.reserve_training(
                    guild_id, user_id, training.key, self._clock()
                ),
            )
        except ValueError as error:
            raise WorkError(str(error)) from error
        except InsufficientFundsError as error:
            raise WorkError(
                f"No te llega: {training.name} cuesta {training.price + tax} Y$ con IGIC."
            ) from error
        return training.name, training.price, tax, balance

    async def buy_coffee(self, guild_id: int, user_id: int, key: str) -> tuple[float, int, int]:
        """Se toma un café: recarga batería. Desde el cuarto del día, temblores.

        Tratamiento fiscal: una compra con IGIC general (ver `EconomyService.purchase`).

        Returns:
            `(batería después, cafés de hoy, saldo final)`.

        Raises:
            WorkError: Si no tiene curro, el café no existe o no le llega.
        """
        coffee = COFFEE_BY_KEY.get(key)
        if coffee is None:
            raise WorkError("Esa máquina no tiene eso.")
        async with self._lock(guild_id, user_id):
            contract = await self._contract(guild_id, user_id)
            if contract is None:
                raise WorkError("La máquina de café es solo para quien tiene curro.")
            now = self._clock()
            roll_over_day(contract, now)
            tax = igic(coffee.price, IGIC_GENERAL_RATE)
            try:
                _, balance = await self.economy.purchase(
                    guild_id,
                    user_id,
                    base=coffee.price,
                    tax=tax,
                    concept=f"cafe:{coffee.key}",
                    reserve=lambda _connection: None,
                )
            except InsufficientFundsError as error:
                raise WorkError("No te llega ni para un café. ¡Quiero café!") from error
            contract.battery = min(float(BATTERY_MAX), battery_now(contract, now) + coffee.battery)
            contract.battery_at = now
            contract.coffees += 1
            await self.repository.save_contract(guild_id, user_id, contract)
            return contract.battery, contract.coffees, balance

    # -- Familia: regalos y favores -----------------------------------------------------

    async def give_gift(
        self,
        guild_id: int,
        user_id: int,
        key: str,
        consume: Callable[[], Awaitable[bool]],
    ) -> tuple[Gift, float]:
        """Da un regalo de la mochila: sube la familia y se gasta.

        No mueve dinero: lo que tributó fue la compra en la tienda (IGIC).

        Args:
            consume: Gasta una unidad del regalo en la tienda; `False` si ya no
                la tiene. Se llama dentro del candado, después de comprobar la
                espera, para no gastar un regalo que no se puede dar.

        Returns:
            `(regalo, familia después)`.

        Raises:
            WorkError: Sin curro, si no es un regalo, si aún está en espera o si
                ya no lo tiene.
        """
        gift = GIFT_BY_KEY.get(key)
        if gift is None:
            raise WorkError("Eso no es un regalo para la familia.")
        async with self._lock(guild_id, user_id):
            contract = await self._contract(guild_id, user_id)
            if contract is None:
                raise WorkError("Los regalos se dan desde `pala`: primero búscate un curro.")
            now = self._clock()
            ready = family_ready_at(contract, gift.key, gift.cooldown)
            if ready > now:
                raise WorkError(
                    f"{gift.emoji} Tu familia todavía está con el último. Otro <t:{int(ready)}:R>."
                )
            if not await consume():
                raise WorkError("Ese regalo no está en tu mochila: cómpralo en 🎁 Regalos.")
            change_family(contract, gift.family, now)
            contract.family_used[gift.key] = now
            await self.repository.save_contract(guild_id, user_id, contract)
            return gift, contract.family

    @staticmethod
    def _check_favor(contract: Contract, favor: Favor, now: float) -> None:
        """Espera y familia mínima de un favor.

        Raises:
            WorkError: Si aún está en espera o dejaría la familia en crisis.
        """
        ready = family_ready_at(contract, favor.key, favor.cooldown)
        if ready > now:
            raise WorkError(f"{favor.emoji} {favor.name}: otra vez <t:{int(ready)}:R>.")
        family = family_now(contract, now)
        if family - favor.cost < FAVOR_FLOOR:
            raise WorkError(
                f"{family_tier(family).emoji} Tu familia no está para favores "
                f"({round(family)}/100; este cuesta {favor.cost}). Descansa o regálale algo."
            )

    async def ask_favor(self, guild_id: int, user_id: int, key: str) -> FavorResult:
        """Le pide un favor a la familia: cuesta familia, no dinero (salvo el Bizum).

        El Bizum de mamá adelanta un sueldo base del puesto (`economy.mom_bizum`)
        y apunta la deuda, que se devuelve con parte de cada turno
        (`MOM_REPAY_SHARE`, en `finish_shift`).

        Raises:
            WorkError: Sin curro, si no existe, si no sirve ahora (no hay saliente
                que cubrir, quedan turnos ordinarios, ya debes el Bizum…), si
                está en espera o si la familia no da para más.
        """
        favor = FAVOR_BY_KEY.get(key)
        if favor is None or favor.key == "excusa":
            # La excusa se pide en un marrón (`dodge_event`).
            raise WorkError("Eso no se le pide a la familia.")
        async with self._lock(guild_id, user_id):
            status = await self.status(guild_id, user_id)
            if status is None:
                raise WorkError("Sin curro no hay a quién darle pena. Abre `pala` y elige uno.")
            contract, now = status.contract, status.now
            self._check_favor(contract, favor, now)
            amount = 0
            balance = None
            if favor.key == "tupper":
                settle(contract, now)
                contract.battery = min(float(BATTERY_MAX), contract.battery + TUPPER_BATTERY)
            elif favor.key == "ninos":
                if contract.motivated:
                    raise WorkError("Ya vas motivado para el próximo turno.")
                contract.motivated = True
            elif favor.key == "cubre":
                if not status.off_duty:
                    raise WorkError("No estás saliente de guardia: no hay nada que cubrir.")
                contract.off_duty_until = now
            elif favor.key == "cambio":
                if contract.shifts_today < contract.jornada:
                    raise WorkError("Aún te quedan turnos ordinarios hoy: guárdate a tu hermana.")
                contract.shifts_today -= 1
            elif favor.key == "bizum":
                if contract.mom_debt:
                    raise WorkError(
                        f"Todavía le debes {contract.mom_debt} Y$ a tu madre. Primero, eso."
                    )
                amount = status.position.base_pay
                balance = await self.economy.mom_bizum(guild_id, user_id, amount=amount)
                contract.mom_debt = amount
            # El aval (`aval`) no hace nada aquí: `pisos` lo mira con `parents_aval`.
            change_family(contract, -favor.cost, now)
            contract.family_used[favor.key] = now
            await self.repository.save_contract(guild_id, user_id, contract)
            return FavorResult(favor, contract.family, amount, balance)

    # -- La obra nueva de `pisos` ----------------------------------------------------

    async def ladrillo_level(self, guild_id: int, user_id: int) -> int:
        """El puesto de alguien en la rama Ladrillo de la obra (6 a 10), o 0 si no está en ella.

        Decide qué puede construir en los solares de `pisos` (`housing_solars.PROJECTS`).
        """
        contract = await self.repository.contract(guild_id, user_id)
        if contract is None or contract.job != BUILDER_JOB or contract.branch != BUILDER_BRANCH:
            return 0
        return contract.level

    async def ladrillo_builders(self, guild_id: int) -> tuple[int, ...]:
        """Quienes han fichado la última semana en la rama Ladrillo de la obra.

        Son los que levantan la obra nueva de `pisos` cada lunes, por turnos.
        """
        since = self._clock() - BUILDER_DAYS * 86_400
        return await self.repository.branch_workers(guild_id, BUILDER_JOB, BUILDER_BRANCH, since)

    # -- El aval de tus padres para `pisos` ------------------------------------------

    async def parents_aval(self, guild_id: int, user_id: int) -> bool:
        """Si tus padres te avalan ahora: pediste el favor hace menos de 7 días y no lo usaste."""
        contract = await self.repository.contract(guild_id, user_id)
        return contract is not None and aval_active(contract, self._clock())

    async def spend_parents_aval(self, guild_id: int, user_id: int) -> None:
        """Gasta el aval: ya ha avalado una hipoteca o un alquiler y no vale para otro."""
        async with self._lock(guild_id, user_id):
            contract = await self.repository.contract(guild_id, user_id)
            now = self._clock()
            if contract is None or not aval_active(contract, now):
                return
            contract.family_used[AVAL_SPENT] = now
            await self.repository.save_contract(guild_id, user_id, contract)

    async def parents_lose_home(self, guild_id: int, user_id: int) -> None:
        """El banco ejecuta una hipoteca que avalaron tus padres: la familia se hunde.

        No mueve dinero (los padres no son miembros: lo que pierden es el piso del
        pueblo, que no existe en el juego), solo la familia de `pala`.
        """
        async with self._lock(guild_id, user_id):
            contract = await self.repository.contract(guild_id, user_id)
            if contract is None:
                return
            change_family(contract, -AVAL_EXECUTION_FAMILY, self._clock())
            await self.repository.save_contract(guild_id, user_id, contract)

    async def dodge_event(self, guild_id: int, user_id: int, event_key: str) -> FavorResult:
        """La excusa familiar: te libras de un marrón sin elegir ninguna opción.

        No vale para la dimisión: de esa no te salva ni tu madre.

        Raises:
            WorkError: Sin curro, si el marrón no existe, en espera o sin familia.
        """
        favor = FAVOR_BY_KEY["excusa"]
        if event_key == RESIGN_EVENT.key or event_key not in EVENT_BY_KEY:
            raise WorkError("De esta no te saca ni tu madre.")
        async with self._lock(guild_id, user_id):
            contract = await self._contract(guild_id, user_id)
            if contract is None:
                raise WorkError("No tienes curro.")
            now = self._clock()
            self._check_favor(contract, favor, now)
            change_family(contract, -favor.cost, now)
            contract.family_used[favor.key] = now
            await self.repository.save_contract(guild_id, user_id, contract)
            return FavorResult(favor, contract.family)

    # -- Canales ------------------------------------------------------------------------

    async def channels(self, guild_id: int) -> frozenset[int]:
        """Canales donde se permite `pala` (vacío = cualquiera)."""
        return await self.repository.channels(guild_id)

    async def toggle_channel(self, guild_id: int, channel_id: int) -> tuple[bool, frozenset[int]]:
        """Añade o quita un canal. Devuelve `(añadido, canales)`."""
        current = set(await self.repository.channels(guild_id))
        added = channel_id not in current
        if added:
            current.add(channel_id)
        else:
            current.discard(channel_id)
        await self.repository.set_channels(guild_id, frozenset(current))
        return added, frozenset(current)

    async def clear_channels(self, guild_id: int) -> None:
        """Vuelve a permitir `pala` en cualquier canal."""
        await self.repository.set_channels(guild_id, frozenset())


class OffDuty(WorkError):
    """Saliente de guardia: no se puede fichar hasta `until`."""

    def __init__(self, until: float) -> None:
        self.until = until
        super().__init__(
            f"🛌 Estás saliente de guardia hasta <t:{int(until)}:t>. A dormir, que mañana operas."
        )


class NeedsBlack(WorkError):
    """El próximo turno sería extra pasado el límite legal: hay que aceptar el B.

    Attributes:
        late_franja: La franja de noche en la que caería (0 de día): el panel enseña con
            ella el riesgo de Inspección (`work.inspection_chance`).
    """

    def __init__(self, late_franja: int = 0) -> None:
        super().__init__(
            "Ya has gastado las horas extra legales de la semana (art. 35.2 ET). "
            "Tu jefe te mira: «¿Te lo pago en B?»."
        )
        self.late_franja = late_franja
