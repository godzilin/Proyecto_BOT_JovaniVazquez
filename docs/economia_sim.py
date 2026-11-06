"""Simulador de la economía de un jugador: sueldo, pisos, IRPF, Patrimonio, pensión y Falcon.

Usa las funciones reales del bot (nómina con el tipo estable, declaración de los lunes con
los pisos y sus arrastres, Patrimonio con el límite conjunto, pensión con la jubilación
activa, experiencia) y el ritmo de ascensos medido con las partidas reales
(`docs/ritmo_pala.py`, guardado en `docs/ritmo_pala.json`). Sirve para calibrar `W` (cómo se
siente el impuesto), la curva de sueldos y los días de cada puesto (el ritmo de la carrera y
del Falcon), y el peso de cada rama en la pensión y la experiencia (lo que tarda en compensar
cambiar de oficio).

Uso, desde la raíz del repo (`PYTHONIOENCODING=utf-8` en la consola de Windows):

    python docs/economia_sim.py                     # informe completo
    python docs/economia_sim.py --nota 85           # jugador bueno
    python docs/economia_sim.py --turnos 2          # media jornada
    python docs/economia_sim.py --oficio oficina    # camino de referencia en otro oficio
    python docs/economia_sim.py --solo escala       # solo la tabla de sueldos

Caminos:

- **Referencia:** un oficio y sus dos ramas (la segura primero; `--arriesgada`, al revés),
  con pisos (hipoteca, hipoteca y rehipotecas, o al contado) o solo sueldo. Con rehipotecas
  (9.1.0, `docs/garantias.md`), cuando ya no le llega para comprar con hipoteca, saca con el
  35 % que le sobra lo que deja la tasación de lo que tiene, si el mejor inmueble libre renta
  más que la cuota, y sigue comprando. Da el día del Falcon, el del primer P10 y el de la
  segunda rama completa.
- **Acumulador:** todos los oficios uno detrás de otro, cada uno con sus dos ramas, solo
  sueldo y pensión. Da cuándo acaba cada oficio y cómo sube el ingreso diario.

Simplificaciones: 4 turnos ordinarios al día con la nota media pedida (sin guardias, extras
ni bonus de tareas), asciende en cuanto puede si le llega para la formación, no juega al
casino ni cobra premios, invierte lo que le sobra en los pisos con mejor rentabilidad antes
de impuestos a precio de salida (índice 1) y cobra la renta media medida de cada nivel
(`RENT_LEVEL_FACTOR`, vacíos e impagos incluidos). Sin vivienda habitual.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from bot.services import housing as h  # noqa: E402
from bot.services import housing_solars as solar_rules  # noqa: E402
from bot.services import loans as lending  # noqa: E402
from bot.services import taxes  # noqa: E402
from bot.services.housing_catalog import KINDS, LISTINGS  # noqa: E402
from bot.services.housing_streets import STREET_UNITS  # noqa: E402

#: Los inmuebles en que puede invertir: los 180 destacados y, con `--calles`, las unidades de
#: las calles (9.14.0).
MARKET: list = list(LISTINGS)
from bot.services.shop_catalog import CATALOG  # noqa: E402
from bot.services.taxes import (  # noqa: E402
    IGIC_GENERAL_RATE,
    IRPF_YAPDOLLARS_PER_EURO,
    SS_WORKER_RATE,
    Pace,
    PropertyWeek,
    WeekIncome,
    compute_payslip,
    compute_pension_payslip,
    compute_self_employed_payslip,
    igic,
    wealth_quote,
    week_return,
    week_tax,
)
from bot.services.work import (  # noqa: E402
    BRANCH_DAYS,
    BRANCH_GROWTH,
    TRUNK_STEPS,
    VETERAN_MAX,
    VETERAN_PAY,
    BranchKind,
    Job,
    Position,
    active_share,
    contribution_quality,
    experience_factor,
    pension_amount,
    required_days,
)
from bot.services.work_catalog import JOBS  # noqa: E402

#: El Falcon de Moncloa, con su IGIC (lo que hay que tener líquido para comprarlo).
FALCON_ENTRY = max(CATALOG, key=lambda e: e.price)
FALCON = FALCON_ENTRY.price + igic(FALCON_ENTRY.price)
#: Renta cobrada media frente a la de referencia, por nivel (Monte Carlo con `advance_day`,
#: vacíos e impagos incluidos; `docs/pisos_sim.py`).
RENT_LEVEL_FACTOR = {"referencia": 0.925, "mercado": 1.068}
MORTGAGE_RATE = h.EURIBOR_START + h.MORTGAGE_SPREAD
REMORTGAGE_RATE = h.EURIBOR_START + lending.REMORTGAGE_SPREAD
BUY_COST = 1 + h.ITP_RATE + h.NOTARY_RATE * (1 + h.SERVICES_IGIC)
#: Lo que se guarda el jugador antes de invertir (formación, cuotas, Patrimonio).
CASH_BUFFER = 20_000


def shift_factor(nota: float) -> float:
    """Lo que multiplica la base un turno ordinario con esa nota (`work.shift_pay`)."""
    return 0.7 + 0.6 * nota / 100


def load_pace(nota: float) -> dict[str, float]:
    """Días medidos por puesto (`docs/ritmo_pala.json`) para esa nota (75 u 85)."""
    data = json.loads((ROOT / "docs" / "ritmo_pala.json").read_text(encoding="utf-8"))
    key = str(int(nota))
    if key not in data:
        raise SystemExit(f"No hay ritmo medido para la nota {key}: python docs/ritmo_pala.py")
    return data[key]


def task_days(pace: dict[str, float]) -> dict[str, float]:
    """Días que piden las tareas de cada puesto del catálogo, cuando mandan más que los días.

    `docs/ritmo_pala.py` mide lo que se tarda en llenar la barra y cumplir las tareas, sin
    los días que pide el puesto (los pone `days_needed`, con la experiencia).
    """
    out = {}
    for job in JOBS:
        for branch, ladder in job.ladders:
            for pos in ladder:
                trunk = pos.level <= job.trunk_top
                key = f"{job.key}/{'' if trunk else branch}/{pos.level}"
                out[key] = pace.get(key, 0.0)
    return out


def days_needed(tasks: dict[str, float], job: Job, branch: str, pos: Position, done: int) -> int:
    """Días para cumplir el puesto: los días pedidos o las tareas, con la experiencia."""
    trunk = pos.level <= job.trunk_top
    task = tasks.get(f"{job.key}/{'' if trunk else branch}/{pos.level}", 0.0)
    factor = experience_factor(done, trunk=trunk)
    return max(required_days(pos, job, done), math.ceil(task * factor))


@dataclass(frozen=True)
class Curve:
    """Sueldos y días para probar otra curva (`--curva`); por defecto, los del bot.

    El catálogo ya sale de `work.PayCurve` con estas constantes: `with_curve` sirve para ver
    qué pasaría con otras sin tocarlo (P1-P3 del catálogo, el resto por pasos geométricos).

    Attributes:
        p4: P4 / P3 (bruto). p5: P5 / P4.
        growth: Paso de cada puesto de rama sobre el anterior, por clase de rama.
        branch_days: Días pedidos de P6 a P9.
        veteran: Plus de sueldo por cada rama completada en otros oficios (0 = sin plus).
        veteran_cap: Lo más que puede sumar ese plus.
    """

    p4: float = TRUNK_STEPS[0]
    p5: float = TRUNK_STEPS[1]
    growth: tuple[tuple[str, float], ...] = tuple(BRANCH_GROWTH.items())
    branch_days: tuple[int, ...] = tuple(BRANCH_DAYS.values())
    veteran: float = VETERAN_PAY
    veteran_cap: float = VETERAN_MAX


def _scaled(pos: Position, base: int, days: int) -> Position:
    training = pos.training
    if training is not None:
        price = round(training.price * base / pos.base_pay, -3)
        training = replace(training, price=price)
    return replace(pos, base_pay=base, days=days, training=training)


def with_curve(job: Job, curve: Curve) -> Job:
    """El oficio con los sueldos, días y formaciones de la curva (las formaciones, a escala)."""
    trunk = list(job.positions)
    base = trunk[2].base_pay
    for i, ratio in ((3, curve.p4), (4, curve.p5)):
        base = int(round(base * ratio, -2))
        trunk[i] = _scaled(trunk[i], base, trunk[i].days)
    growth = dict(curve.growth)
    branches = []
    for branch in job.branches:
        positions, pay = [], float(base)
        for pos in branch.positions:
            pay *= growth[branch.kind]
            step = pos.level - job.trunk_top - 1
            days = curve.branch_days[step] if step < len(curve.branch_days) else pos.days
            positions.append(_scaled(pos, int(round(pay, -2)), days))
        branches.append(replace(branch, positions=tuple(positions)))
    return replace(job, positions=tuple(trunk), branches=tuple(branches))


def monthly_payment(principal: float) -> float:
    """Cuota de una hipoteca francesa a `MORTGAGE_MONTHS` meses (`housing.installment`)."""
    r = MORTGAGE_RATE / 12
    return principal * r / (1 - (1 + r) ** -h.MORTGAGE_MONTHS)


def expected_repairs(listing) -> float:  # type: ignore[no-untyped-def]
    """Lo que cuestan de media al día los imprevistos del inmueble entero."""
    community = h._euros(KINDS[listing.kind].community)  # noqa: SLF001
    total = 0.0
    for ev in h.EVENTS:
        if ev.kinds and listing.kind not in ev.kinds:
            continue
        cost = community * ev.community_months + h._euros(sum(ev.euros) / 2)  # noqa: SLF001
        total += ev.chance * cost
    return total


@dataclass(frozen=True)
class Eco:
    """Lo que renta y cuesta al día del bot (un mes) un inmueble entero."""

    key: str
    price: int
    rent: float
    costs: int
    repairs: float
    cadastral: int
    reduction: float

    def yield_rate(self) -> float:
        return (self.rent - self.costs - self.repairs) / (self.price * BUY_COST)


def build_market(level: str) -> dict[str, Eco]:
    out = {}
    for listing in MARKET:
        if h.is_promotion(listing.key):
            continue
        out[listing.key] = Eco(
            key=listing.key,
            price=h.price(listing),
            rent=h.reference_rent(listing) * RENT_LEVEL_FACTOR[level],
            costs=h.monthly_costs(listing).total,
            repairs=expected_repairs(listing),
            cadastral=h.cadastral_value(listing),
            reduction=h.contract_reduction(listing, level, index=1.0, rent=1, last_rent=0),
        )
    return out


@dataclass
class Holding:
    shares: int = 0
    paid: float = 0.0
    balance: float = 0.0
    payment: float = 0.0
    #: Rehipotecas sobre este inmueble: lo que queda y la cuota (sus intereses no deducen).
    rebalance: float = 0.0
    repayment: float = 0.0


@dataclass
class Retired:
    job: str
    branch: str
    kind: str
    quality: float


@dataclass
class Career:
    """Dónde va el jugador en su carrera: el plan es una lista de `(oficio, rama)`."""

    plan: list[tuple[Job, str]]
    index: int = 0
    level: int = 1
    days_in: int = 0
    first_p10: int | None = None
    branch_done: list[int] = field(default_factory=list)
    jobs_done: list[tuple[str, int]] = field(default_factory=list)

    @property
    def job(self) -> Job:
        return self.plan[self.index][0]

    @property
    def branch(self) -> str:
        return self.plan[self.index][1]

    def position(self) -> Position:
        level = self.level
        return self.job.position(level, self.branch if level > self.job.trunk_top else "")


class Simulation:
    """Un jugador día a día con las reglas reales."""

    def __init__(
        self,
        plan: list[tuple[Job, str]],
        *,
        nota: float = 75,
        shifts: int = 4,
        invest: str = "lev",
        level: str = "referencia",
        curve: Curve | None = None,
        solares: bool = False,
    ) -> None:
        self.c = Career(plan)
        self.nota = nota
        self.shifts = shifts
        self.tasks = task_days(load_pace(nota))
        self.curve = curve or Curve()
        self.invest = invest
        self.market = build_market(level)
        self.day = 0
        self.cash = 1_000.0
        self.debt = 0.0
        self.holdings: dict[str, Holding] = {}
        self.carry: dict[str, list[int]] = {}
        self.retired: list[Retired] = []
        self.first_retired: int | None = None
        self.branch_gross = 0.0
        self.branch_days = 0
        self.falcon_day: int | None = None
        self.totals: dict[str, float] = {}
        self.daily: list[tuple[float, float, float]] = []
        self.weeks: list[dict[str, float]] = []
        self.checkpoints: list[float] = []
        self._new_week()
        self.previous_rate = 0.0
        self.previous_wages = 0
        #: Solares (9.56.0): si construye en la rama Ladrillo, la obra en marcha
        #: `(día que acaba, lo pagado, lo que valen las unidades)` y los solares que quedan.
        self.solares = solares
        self.works: tuple[int, float, float] | None = None
        self.free_solars = list(solar_rules.SOLARS)

    def _new_week(self) -> None:
        self.income = WeekIncome()
        self.wages = 0
        self.withheld = 0

    def add(self, key: str, value: float) -> None:
        self.totals[key] = self.totals.get(key, 0.0) + value

    def pace(self) -> Pace:
        return Pace(self.previous_rate, self.previous_wages, self.wages, self.income)

    def done_elsewhere(self) -> int:
        return sum(1 for r in self.retired if r.job != self.c.job.key)

    # -- Un día --------------------------------------------------------------------------

    def veteran_plus(self) -> float:
        return min(self.curve.veteran_cap, self.curve.veteran * self.done_elsewhere())

    def work_day(self) -> float:
        pos = self.c.position()
        gross = round(pos.base_pay * (1 + self.veteran_plus()) * shift_factor(self.nota))
        net = 0
        for _ in range(self.shifts):
            if pos.self_employed:
                slip = compute_self_employed_payslip(gross, self.pace())
                self.income = self.income.plus(business=gross)
            else:
                slip = compute_payslip(gross, self.pace())
                self.wages += gross
                self.income = self.income.plus(work=gross, social=slip.ss_worker)
            self.withheld += slip.irpf
            net += slip.net
            self.add("bruto", gross)
        self.cash += net
        if self.c.level > self.c.job.trunk_top:
            self.branch_days += 1
            self.branch_gross += gross * self.shifts
        return net

    def pension_day(self, working: bool) -> float:
        if not self.retired:
            return 0.0
        full = pension_amount([(r.kind, r.quality) for r in self.retired])
        assert self.first_retired is not None
        share = active_share((self.day - self.first_retired) // 7) if working else 1.0
        amount = round(full * share)
        if amount <= 0:
            return 0.0
        slip = compute_pension_payslip(amount, self.pace())
        self.withheld += slip.irpf
        self.income = self.income.plus(work=amount)
        self.cash += slip.net
        self.add("pension", amount)
        return slip.net

    def rents_day(self) -> float:
        net = 0.0
        for key, hold in self.holdings.items():
            eco = self.market[key]
            share = hold.shares / h.SHARES
            rent, costs, repairs = eco.rent * share, eco.costs * share, eco.repairs * share
            interest = hold.balance * MORTGAGE_RATE / 12
            paid = min(hold.payment, hold.balance + interest) if hold.balance > 0 else 0.0
            hold.balance = max(0.0, hold.balance + interest - paid)
            if hold.rebalance > 0:
                reinterest = hold.rebalance * REMORTGAGE_RATE / 12
                repaid = min(hold.repayment, hold.rebalance + reinterest)
                hold.rebalance = max(0.0, hold.rebalance + reinterest - repaid)
                paid += repaid
                self.add("rehipoteca_cuotas", repaid)
            item = PropertyWeek(
                ref=key,
                income=round(rent),
                finance=round(interest + repairs),
                costs=round(costs + h.amortization(round(hold.paid))),
                reduction=eco.reduction,
            )
            withheld = round(max(0, item.income - item.finance - item.costs) * self.previous_rate)
            self.income = self.income.plus(prop=item)
            self.withheld += withheld
            net += rent - costs - repairs - paid - withheld
            self.add("alquiler", rent)
        self.cash += net
        return net

    def close_week(self) -> None:
        """La declaración del lunes, la cuota de autónomo y el Patrimonio de la semana."""
        pos = self.c.position()
        if pos.self_employed:
            self.cash -= pos.fee
            self.income = self.income.plus(business=-pos.fee)
            self.add("autonomo", pos.fee)
        props = tuple(
            PropertyWeek(
                ref=p.ref, income=p.income, finance=p.finance, costs=p.costs,
                reduction=p.reduction, carry=sum(self.carry.get(p.ref, [])),
            )
            for p in self.income.properties
        )  # fmt: skip
        income = WeekIncome(
            work=self.income.work, social=self.income.social, business=self.income.business,
            properties=props,
        )  # fmt: skip
        filed = week_return(income, withheld=self.withheld)
        for ref, used, left in filed.tax.settlements:
            queue = self.carry.setdefault(ref, [])
            while used > 0 and queue:
                take = min(used, queue[0])
                queue[0] -= take
                used -= take
                if not queue[0]:
                    queue.pop(0)
            queue.append(left)
            del queue[:-4]
        self.cash -= filed.result
        self.add("irpf", filed.due)
        worth = (
            self.cash
            - self.debt
            + sum(
                max(self.market[k].cadastral * hold.shares / h.SHARES, hold.paid)
                - hold.balance
                - hold.rebalance
                for k, hold in self.holdings.items()
            )
        )
        quote = wealth_quote(
            max(0, round(worth)), irpf_base=filed.tax.base, irpf_quota=filed.tax.quota
        )
        self.cash -= quote.due
        self.add("patrimonio", quote.due)
        gross = income.work + income.business + sum(p.income for p in props)
        self.weeks.append(
            {
                "gross": gross,
                "due": filed.due,
                "result": filed.result,
                "rate": filed.tax.rate,
                "wealth": quote.due,
                "worth": worth,
            }
        )
        if self.cash < 0:
            self.debt -= self.cash
            self.cash = 0.0
        self.previous_rate = filed.tax.rate
        self.previous_wages = self.wages
        self._new_week()

    def liquid(self) -> float:
        homes = sum(
            self.market[k].price
            * hold.shares
            / h.SHARES
            * (1 - h.SALE_FEE_RATE * (1 + h.SERVICES_IGIC))
            - hold.balance
            - hold.rebalance
            for k, hold in self.holdings.items()
        )
        works = self.works[1] if self.works else 0.0  # la obra en marcha, a lo que costó
        return self.cash - self.debt + homes + works

    # -- Carrera ---------------------------------------------------------------------------

    def advance_career(self) -> None:
        c = self.c
        job = c.job
        if c.level >= job.top:
            if c.first_p10 is None:
                c.first_p10 = self.day
            self.retire()
            return
        c.days_in += 1
        need = days_needed(self.tasks, job, c.branch, c.position(), self.done_elsewhere())
        if c.days_in < need:
            return
        level = c.level + 1
        cost = self.next_training()
        if self.cash < cost:
            return
        self.cash -= cost
        self.add("formacion", cost)
        c.level = level
        c.days_in = 0
        if c.level >= job.top:
            self.advance_career()

    def next_training(self) -> int:
        """Lo que cuesta, con IGIC, la formación del puesto siguiente (0 si no hay)."""
        c = self.c
        level = c.level + 1
        if level > c.job.top:
            return 0
        following = c.job.position(level, c.branch if level > c.job.trunk_top else "")
        price = following.training.price if following.training else 0
        return price + igic(price, IGIC_GENERAL_RATE)

    def retire(self) -> None:
        c = self.c
        job = c.job
        branch = job.branch(c.branch)
        assert branch is not None
        if any(r.job == job.key and r.branch == branch.key for r in self.retired):
            return
        quality = contribution_quality(self.branch_gross / max(1, self.branch_days))
        self.retired.append(Retired(job.key, branch.key, branch.kind, quality))
        self.branch_gross, self.branch_days = 0.0, 0
        if self.first_retired is None:
            self.first_retired = self.day
        c.branch_done.append(self.day)
        if c.index + 1 >= len(c.plan):
            c.jobs_done.append((job.key, self.day))
            return
        same = c.plan[c.index + 1][0].key == job.key
        if not same:
            c.jobs_done.append((job.key, self.day))
        c.index += 1
        c.level = job.trunk_top + 1 if same else 1
        c.days_in = 0

    # -- Pisos -----------------------------------------------------------------------------

    def invest_step(self) -> None:
        if self.invest == "none":
            return
        pos = self.c.position()
        avail = (
            self.cash - CASH_BUFFER - (pos.fee if pos.self_employed else 0) - self.next_training()
        )
        if avail <= 0:
            return
        recent = self.daily[-h.INCOME_DAYS :]
        income = sum(w + p + h.RENT_INCOME_SHARE * r for w, p, r in recent) / max(1, len(recent))
        current = sum(hold.payment for hold in self.holdings.values() if hold.balance > 0)
        current += sum(hold.repayment for hold in self.holdings.values() if hold.rebalance > 0)
        options = sorted(
            (e for e in self.market.values() if self.free_shares(e.key) >= 1),
            key=lambda e: -e.yield_rate(),
        )
        avail, current = self.buy_step(options, avail, income, current)
        if self.invest == "rehip" and options and avail < options[0].price / h.SHARES:
            # Lo último: con el 35 % que sobra tras comprar con hipoteca (más barata y que sí
            # desgrava), rehipotecar lo que ya tiene y seguir comprando.
            before = avail
            avail, current = self.remortgage_step(options[0], avail, income, current)
            if avail > before:
                self.buy_step(options, avail, income, current)

    def buy_step(
        self, options: list[Eco], avail: float, income: float, current: float
    ) -> tuple[float, float]:
        """Compra participaciones de lo que más renta con lo disponible (y la hipoteca)."""
        for eco in options:
            per_share = eco.price / h.SHARES
            loan = per_share * h.MORTGAGE_LTV if self.invest in ("lev", "rehip") else 0.0
            payment = monthly_payment(loan)
            if loan and (eco.rent - eco.costs - eco.repairs) / h.SHARES <= payment:
                loan, payment = 0.0, 0.0
            entry = per_share * BUY_COST - loan
            most = max(0.0, h.DTI_MAX * income - current) / payment if payment else math.inf
            n = math.floor(min(self.free_shares(eco.key), avail // entry, most))
            if n < 1:
                continue
            hold = self.holdings.setdefault(eco.key, Holding())
            hold.shares += n
            hold.paid += n * per_share
            hold.balance += n * loan
            hold.payment += n * payment
            self.cash -= n * entry
            avail -= n * entry
            current += n * payment
            self.add("compras", n * per_share * BUY_COST)
            if avail < per_share:
                break
        return avail, current

    def remortgage_step(
        self, best: Eco, avail: float, income: float, current: float
    ) -> tuple[float, float]:
        """Rehipoteca lo que deja la tasación si lo mejor que hay libre renta más que la cuota.

        Returns:
            `(dinero disponible, cuotas al mes)` después de firmar.
        """
        rate = REMORTGAGE_RATE / 12
        factor = (1 - (1 + rate) ** -h.MORTGAGE_MONTHS) / rate
        # Lo que da cada Y$ invertido en lo mejor que hay libre, contra lo que cuesta al mes.
        if best.yield_rate() <= 1 / factor:
            return avail, current
        for key, hold in self.holdings.items():
            eco = self.market[key]
            listing = next(x for x in MARKET if x.key == key)
            value = eco.price * hold.shares / h.SHARES
            room = lending.remortgage_room(
                round(value), round(hold.balance + hold.rebalance),
                home=listing.kind in h.HOME_KINDS,
            )  # fmt: skip
            dti = max(0.0, h.DTI_MAX * income - current)
            amount = min(room, dti * factor)
            if amount < lending.REMORTGAGE_MIN:
                continue
            quote = lending.remortgage_quote(round(amount), h.MORTGAGE_MONTHS, REMORTGAGE_RATE,
                                             round(value))  # fmt: skip
            hold.rebalance += quote.principal
            hold.repayment += quote.payment
            current += quote.payment
            self.cash += quote.received
            avail += quote.received
            self.add("rehipotecado", quote.principal)
            self.add("rehipoteca_gastos", quote.principal - quote.received)
        return avail, current

    def solar_step(self) -> None:
        """Construye en los solares (rama Ladrillo de la obra, 9.56.0) y vende al acabar.

        Es el promotor que más rinde: al acabar vende todas las unidades al mercado (agencia con
        IGIC y el IRPF de la ganancia) y empieza otra, la más grande que paga al contado con su
        puesto, en el solar que más margen deja. Sin burbuja: es el techo de lo que da construir.
        """
        if self.works is not None and self.day >= self.works[0]:
            _until, paid, value = self.works
            fee = value * h.SALE_FEE_RATE * (1 + h.SERVICES_IGIC)
            gain = round(value - fee - paid)
            received = value - fee - taxes.capital_gain_tax(gain)
            self.cash += received
            self.add("solares_ganancia", received - paid)
            self.works = None
        c = self.c
        if (
            self.works is not None
            or c.job.key != "obra"
            or c.branch != "ladrillo"
            or c.level < 6
            or not self.free_solars
        ):
            return
        pos = c.position()
        avail = self.cash - CASH_BUFFER - (pos.fee if pos.self_employed else 0)
        best = None
        for solar in self.free_solars:
            for project in solar_rules.projects_for(solar):
                if project.level > c.level:
                    continue
                quote = solar_rules.build_quote(solar, project)
                if quote.total <= avail and (best is None or quote.margin > best[2].margin):
                    best = (solar, project, quote)
        if best is None:
            return
        solar, _project, quote = best
        self.free_solars.remove(solar)
        self.cash -= quote.total
        self.works = (self.day + quote.days, quote.total, quote.value)
        self.add("solares_obras", 1)
        self.add("solares_invertido", quote.total)

    def free_shares(self, key: str) -> int:
        hold = self.holdings.get(key)
        return h.SHARES - (hold.shares if hold else 0)

    # -- Bucle -----------------------------------------------------------------------------

    def run(self, days: int) -> Simulation:
        for day in range(1, days + 1):
            self.day = day
            wages = self.work_day()
            pension = self.pension_day(working=True)
            rents = self.rents_day()
            self.daily.append((wages, pension, rents))
            self.advance_career()
            if self.debt and self.cash > 0:
                pay = min(self.debt, self.cash)
                self.debt -= pay
                self.cash -= pay
            if self.solares:
                self.solar_step()
            self.invest_step()
            if day % 7 == 0:
                self.close_week()
            if self.falcon_day is None and self.liquid() >= FALCON:
                self.falcon_day = day
            if day % 100 == 0:
                self.checkpoints.append(self.liquid())
        return self


def reference_plan(job: Job, risky_first: bool = False) -> list[tuple[Job, str]]:
    order = sorted(job.branches, key=lambda b: (b.kind == BranchKind.SAFE) == risky_first)
    return [(job, b.key) for b in order]


def accumulator_plan(jobs: list[Job]) -> list[tuple[Job, str]]:
    return [pair for job in jobs for pair in reference_plan(job)]


def full_week(pos: Position, nota: float) -> tuple[int, int]:
    """`(bruto, neto)` de una semana a jornada completa en el puesto, solo con el sueldo."""
    gross = round(pos.base_pay * shift_factor(nota)) * 28
    if pos.self_employed:
        income = WeekIncome(business=gross - pos.fee)
        return gross, gross - pos.fee - week_tax(income).quota
    social = round(gross * SS_WORKER_RATE)
    return gross, gross - social - week_tax(WeekIncome(work=gross, social=social)).quota


def ladder_report(jobs: list[Job], nota: float) -> list[str]:
    """Por puesto: base del turno, lo que se va (IRPF + SS o cuota) y la subida del neto."""
    out = []
    for job in jobs:
        for branch, ladder in job.ladders:
            rows = [full_week(pos, nota) for pos in ladder]
            cells = []
            for i, (pos, (gross, net)) in enumerate(zip(ladder, rows, strict=True)):
                step = f" {(net / rows[i - 1][1] - 1) * 100:+.0f}%" if i else ""
                cells.append(f"{pos.base_pay / 1000:.1f}k {1 - net / gross:.0%}{step}")
            out.append(f"{job.key:13} {branch or '-':11} | " + " | ".join(cells))
    return out


def recovery(
    job1: Job, job2: Job, *, days: int = 700, **opts: object
) -> tuple[int, int | None, int | None]:
    """Cambiar de oficio frente a quedarse en P10: `(día del cambio, días hasta igualar el neto
    diario, días hasta igualar el neto acumulado)`, solo con sueldo y pensión."""
    stay = Simulation(reference_plan(job1), invest="none", **opts).run(days)  # type: ignore[arg-type]
    move = Simulation(reference_plan(job1) + reference_plan(job2), invest="none", **opts).run(days)  # type: ignore[arg-type]
    start = stay.c.jobs_done[0][1]
    a = [w + p for w, p, _r in stay.daily]
    b = [w + p for w, p, _r in move.daily]
    daily = next(
        (d - start for d in range(start + 7, days) if sum(b[d - 7 : d]) >= sum(a[d - 7 : d])), None
    )
    total = next(
        (d - start for d in range(start + 1, days) if sum(b[start:d]) >= sum(a[start:d])), None
    )
    return start, daily, total


def describe(sim: Simulation, label: str) -> str:
    c = sim.c
    rates = [w["due"] / w["gross"] for w in sim.weeks if w["gross"]]
    last = sim.weeks[-1]
    totals = ", ".join(f"{k} {v / 1e6:.1f} M" for k, v in sorted(sim.totals.items()))
    return (
        f"\n== {label} ==\n"
        f"Falcon el día {sim.falcon_day} · primer P10 el día {c.first_p10} · ramas completadas "
        f"los días {c.branch_done} · inmuebles {len(sim.holdings)}\n"
        f"Líquido cada 100 días: {' '.join(f'{x / 1e6:.1f} M' for x in sim.checkpoints)}\n"
        f"Totales: {totals}\n"
        f"Cuota del IRPF sobre el bruto: media {sum(rates) / max(1, len(rates)):.1%}, "
        f"última semana {rates[-1]:.1%} · Patrimonio la última semana {last['wealth']:,.0f} "
        f"(riqueza {last['worth'] / 1e6:.1f} M)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--nota", type=float, default=75)
    parser.add_argument("--turnos", type=int, default=4)
    parser.add_argument("--oficio", default="obra")
    parser.add_argument("--arriesgada", action="store_true")
    parser.add_argument("--dias", type=int, default=400)
    parser.add_argument("--nivel", default="referencia", choices=sorted(RENT_LEVEL_FACTOR))
    parser.add_argument("--solo", choices=["escala", "referencia", "acumulador", "cambio"])
    parser.add_argument("--curva", action="store_true", help="rehace el catálogo con `Curve`")
    parser.add_argument("--veterania", type=float, default=VETERAN_PAY, help="plus por rama")
    parser.add_argument("--patrimonio", type=float, help="rebaja máxima del límite conjunto")
    parser.add_argument("--calles", action="store_true", help="invierte también en las calles")
    parser.add_argument("--solares", action="store_true", help="construye en los solares")
    args = parser.parse_args()

    if args.calles:
        MARKET.extend(STREET_UNITS)
    if args.patrimonio is not None:
        taxes.WEALTH_MAX_REDUCTION = args.patrimonio
    curve = Curve(veteran=args.veterania, veteran_cap=args.veterania * VETERAN_MAX / VETERAN_PAY)
    jobs = [with_curve(job, curve) for job in JOBS] if args.curva else list(JOBS)
    by_key = {job.key: job for job in jobs}
    opts = {"nota": args.nota, "shifts": args.turnos, "curve": curve, "solares": args.solares}

    print(
        f"W = {IRPF_YAPDOLLARS_PER_EURO} Y$/€ · Falcon {FALCON:,} Y$ · nota {args.nota} · "
        f"{args.turnos} turnos · rebaja máx. del Patrimonio {taxes.WEALTH_MAX_REDUCTION:.0%} · "
        f"{'catálogo rehecho con Curve' if args.curva else 'catálogo del bot'}"
    )
    if args.solo in (None, "escala"):
        print("\n== Base del turno, lo que se va (IRPF + SS) y subida del neto, por puesto ==")
        print("\n".join(ladder_report(jobs, args.nota)))
    if args.solo in (None, "referencia"):
        job = by_key[args.oficio]
        for label, invest in (
            ("hipoteca", "lev"), ("hipoteca y rehipotecas", "rehip"), ("al contado", "cash"),
            ("solo sueldo", "none"),
        ):  # fmt: skip
            sim = Simulation(
                reference_plan(job, args.arriesgada), invest=invest, level=args.nivel, **opts
            ).run(args.dias)
            print(describe(sim, f"Referencia {job.key}, {label}"))
    if args.solo in (None, "acumulador"):
        for label, invest in (("hipoteca", "lev"), ("solo sueldo", "none")):
            sim = Simulation(accumulator_plan(jobs), invest=invest, **opts).run(args.dias * 2)
            print(describe(sim, f"Acumulador (todos los oficios), {label}"))
            done = ", ".join(f"{j} {d}" for j, d in sim.c.jobs_done)
            print(f"Oficios terminados (oficio, día): {done}")
            pension = pension_amount([(r.kind, r.quality) for r in sim.retired])
            print(f"Pensión completa al final: {pension:,} Y$/día con {len(sim.retired)} ramas")
            step = 28
            nets = [
                sum(w + p for w, p, _r in sim.daily[i : i + step]) / step
                for i in range(0, len(sim.daily), step)
            ]
            print(
                "Neto al día (sueldo + pensión), cada 4 semanas: "
                + " ".join(f"{n / 1000:.0f}k" for n in nets)
            )
    if args.solo in (None, "cambio"):
        print("\n== Cambiar de oficio tras acabar el primero (solo sueldo y pensión) ==")
        first = by_key[args.oficio]
        for second in jobs:
            if second.key == first.key:
                continue
            start, daily, total = recovery(first, second, **opts)
            print(
                f"{first.key} -> {second.key:13} cambio el día {start}: neto diario igualado "
                f"en {daily} días, acumulado en {total}"
            )


if __name__ == "__main__":
    main()
