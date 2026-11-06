"""Simulador del negocio de `pala`: cuánto deja al día frente a la `pala` y a alquilar el local.

Juega días enteros con las funciones reales del bot: la cola de clientes y los tres
minijuegos (`business_games`), la demanda con su hora, sus estrellas y su precio
(`business.turn_clients`), las mermas y la reputación de cada noche, el recibo del local
(`nightly_bill`), la cuota de autónomo y el IRPF con la declaración de la semana
(`taxes.week_tax`, la actividad como única renta). El jugador compra stock para el día
siguiente y abre sus turnos a horas de jugador (por la mañana y por la tarde).

Sirve para calibrar los sectores de `business_catalog` (clientes, precios, costes, alquiler,
acondicionamiento): ver «Equilibrio» en `docs/negocios.md`.

Uso, desde la raíz del repo (`PYTHONIOENCODING=utf-8` en la consola de Windows):

    python docs/negocio_sim.py               # la tabla completa
    python docs/negocio_sim.py --dias 42     # más días
    python docs/negocio_sim.py --fase2       # mejoras, tamaños y campañas (fase 2)
    python docs/negocio_sim.py --sociedad    # autónomo o sociedad limitada (fase 3)
    python docs/negocio_sim.py --plantilla   # empleados (fase 4)
    python docs/negocio_sim.py --cadena      # importar con nave, el gerente y vender (fase 5)
    python docs/negocio_sim.py --puestos     # horarios y puestos de la plantilla (9.41.0)

Con `--fase2`, un jugador `bueno` en el barrio del medio de cada sector, con el cupo del
barrio (`daily_pool`) y la amortización en el IRPF: lo que suma al día cada mejora, cada
tamaño de local y cada campaña frente al negocio sin nada, y en cuántos días se paga.

Con `--sociedad`, para cada beneficio semanal del negocio (antes de los impuestos del
dueño), lo que se lleva el dueño al bolsillo como autónomo (IRPF de la actividad y la cuota)
y como sociedad (gestoría, Sociedades, su sueldo con su IRPF y el resto en dividendos al
19 %), con el mejor reparto entre sueldo y dividendos. La cuota del RETA es la misma.

Con `--plantilla`, el barrio del medio de cada sector con el cupo: lo que deja al día el
negocio llevado por empleados (con sus turnos jugados por `business_staff.play_turn`, su
nómina con la cuota de la empresa y su humor) frente al dueño solo, según lo que saben.

Con `--cadena`, el barrio del medio de cada sector con el cupo: lo que gasta al día cada
local en el mayorista, lo que se ahorraría importándolo (`business_chain.import_quote`, solo
lo que no caduca), cuántos locales de ese sector pagan una nave pequeña en un barrio obrero,
lo que cuesta un gerente y lo que pagaría un comprador por una cadena así (art. 37.1.b LIRPF).

Con `--puestos`, el barrio del medio de cada sector con el cupo: el mejor horario para un
empleado solo (de los 21 de `business_staff.HORARIOS`) y lo que suma o resta cada puesto nuevo
(encargado de turno, repartidor, limpieza, mozo de almacén, community manager y recursos
humanos) a dos plantillas (el dueño a 4 turnos con un dependiente de tarde, y dos dependientes
de mañana y de tarde), con el humor medio al final. El simulador repone el stock como el
jugador, así que del mozo de almacén solo mide la merma que salva, no su reposición.

Jugadores: `perfecto` (todo bien y rápido), `bueno` (falla 1 de cada 8 clientes) y
`flojo` (falla 1 de cada 3). Dedicación: 4 turnos al día (con curro a jornada completa) u
8 (de excedencia o sin curro). La tabla da el neto medio diario de los días 15 al final
(con la reputación ya asentada), tras IRPF y cuota, y lo compara con el neto de cuatro
turnos de la `pala` en P1, P3 y P5 de obra.
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from bot.services import business_sl as sl  # noqa: E402
from bot.services import business_staff as staff_rules  # noqa: E402
from bot.services.business import (  # noqa: E402
    REPUTATION_MAX,
    Business,
    add_asset,
    amortize,
    boost,
    daily_pool,
    drift,
    nightly_bill,
    opening_quote,
    pool_factor,
    price_reputation,
    setup_asset,
    spoil,
    turn_clients,
    turn_reputation,
    wholesale,
)
from bot.services.business_catalog import (  # noqa: E402
    CAMPAIGN_BY_KEY,
    CAMPAIGNS,
    EQUIPMENT_BY_KEY,
    SECTORS,
    SIZES,
    Sector,
    equipment_for,
)
from bot.services.business_games import (  # noqa: E402
    AUTOPLAY_GAMES,
    COINS,
    TurnGame,
    apply_loyalty,
    autoplay,
    make_clients,
)
from bot.services.housing_catalog import BARRIOS, Barrio  # noqa: E402
from bot.services.taxes import (  # noqa: E402
    SS_WORKER_RATE,
    WeekIncome,
    igic,
    week_tax,
)
from bot.services.work import FLAT_RATE_FEE, SELF_EMPLOYED_FEE  # noqa: E402
from bot.services.work_catalog import JOB_BY_KEY  # noqa: E402

#: Horas a las que abre el jugador según sus turnos del día.
HOURS = {4: (10, 12, 17, 19), 8: (8, 9, 10, 11, 12, 17, 18, 19)}
#: Cada cuánto falla cada jugador.
PLAYERS = {"perfecto": 0.0, "bueno": 1 / 8, "flojo": 1 / 3}
#: Segundos por pulsación (el jugador perfecto va rápido).
CLICK_SECONDS = 1.5


def play_client(game: TurnGame, now: float, rng: random.Random, miss: float) -> float:
    """Atiende al cliente de ahora, bien o con un fallo típico. Devuelve la hora al acabar."""
    client = game.current
    assert client is not None
    wrong = rng.random() < miss
    kind = game.sector.game
    if kind in AUTOPLAY_GAMES:  # los minijuegos de la 9.48.0
        return autoplay(game, now, click=CLICK_SECONDS, wrong=wrong, rng=rng)
    if kind == "comandas":
        order = dict(client.order)
        if wrong:  # sirve otra cosa
            key = next(iter(order))
            order[key] = order[key] + 1
        for key, n in order.items():
            for _ in range(n):
                now += CLICK_SECONDS
                game.press(f"p:{key}", now)
        game.press("serve", now)
    elif kind == "caja":
        if client.hidden and not wrong:
            game.press("thief", now)
        due = game.due() + (rng.choice((50, -50)) if wrong else 0)  # da de más o de menos
        for coin in COINS:
            while due >= coin:
                now += CLICK_SECONDS
                game.press(f"c:{coin}", now)
                due -= coin
        game.press("charge", now)
    else:
        fault = client.fault
        if wrong:
            fault = next(k for k in client.options if k != client.fault)
        now += 3 * CLICK_SECONDS
        game.press(f"d:{fault}", now)
        if game.current is client and game.step == "presupuesto":
            game.press("q:1", now)
        elif game.current is client:  # sin el recambio
            game.press("nopart", now)
    return now + CLICK_SECONDS


def restock(business: Business, need: dict[str, float]) -> int:
    """Compra lotes para que el stock cubra 1,3 días de lo que se gasta. Devuelve lo pagado."""
    paid = 0
    for product in business.sector_info.products:
        want = need.get(product.key, product.pack) * 1.3
        have = business.stock.get(product.key, 0)
        if have >= want:
            continue
        lots = max(1, int((want - have) // product.pack) + 1)
        base, tax = wholesale(product, lots * product.pack)
        business.stock[product.key] = have + lots * product.pack
        paid += base + tax
    return paid


@dataclass
class Run:
    """Lo que dejó una simulación."""

    net_day: float
    stars: float
    opening: int
    payback: float
    #: Unidades que gasta al día cada producto (media de los días 15 al final).
    units_day: dict[str, float] | None = None
    #: El humor medio de la plantilla al final (`nan` sin plantilla).
    humor: float = float("nan")


#: El día 0 de la simulación (un lunes), para las campañas.
DAY0 = date(2026, 10, 5)


def simulate(
    sector: Sector,
    barrio: Barrio,
    turns: int,
    miss: float,
    days: int,
    seed: int,
    *,
    size: int = 0,
    equipment: tuple[str, ...] = (),
    campaign: str = "",
    pool: bool = False,
    staff: tuple[dict, ...] = (),
) -> Run:
    """Juega `days` días. Con la fase 2: tamaño, mejoras, una campaña que se repite y el cupo.

    Con `staff` (fase 4), sus empleados juegan sus franjas y cobran su nómina cada noche.
    """
    rng = random.Random(seed)
    business = Business(1, sector.key, barrio.key, "Sim", seed=seed, size=size)
    # La plantilla va en el negocio: de ahí leen sus efectos (`business_staff.support`).
    business.staff = list(staff)
    quote = opening_quote(sector, barrio, SIZES[size])
    setup_asset(business, quote.setup + quote.setup_igic)
    invested = 0
    for key in equipment:
        item = EQUIPMENT_BY_KEY[key]
        business.equipment.append(key)
        add_asset(business, key, item.cost + igic(item.cost), item.kind)
        invested += item.cost + igic(item.cost)
    need = {p.key: float(p.pack) for p in sector.products}
    nets: list[float] = []
    spent_units: dict[str, int] = {}
    week_net = 0
    cash = -quote.total + quote.deposit - invested  # la fianza vuelve al cerrar: no es gasto
    payback = float("nan")
    for day in range(days):
        today = DAY0 + timedelta(days=day)
        spent = restock(business, need)
        if campaign and (
            not business.campaign_until or business.campaign_until < today.isoformat()
        ):
            item = CAMPAIGN_BY_KEY[campaign]
            business.campaign = campaign
            business.campaign_until = (today + timedelta(days=item.days - 1)).isoformat()
            spent += item.cost + igic(item.cost)
        left = daily_pool(sector, barrio) * pool_factor(business, today) if pool else math.inf
        away_left = (
            daily_pool(sector, barrio) * staff_rules.DELIVERY_POOL * (1 + boost(business, today))
            if pool
            else math.inf
        )
        used: dict[str, int] = {}
        sales = 0
        iso = today.isoformat()
        shifts: list[tuple[int, dict | None, int]] = [(h, None, -1) for h in HOURS.get(turns, ())]
        for emp in staff:
            if staff_rules.absent(emp, seed, today):
                emp["absent_day"] = iso
            elif staff_rules.plays(emp):
                shifts += [(staff_rules.demand_hour(f), emp, f) for f in staff_rules.franjas(emp)]
        helped = staff_rules.support(business, today)
        grades: dict[int, list[str]] = {}
        for hour, emp, franja in sorted(shifts, key=lambda s: s[0]):
            away = emp is not None and staff_rules.role_of(emp).plays == "reparto"
            count = turn_clients(business, hour, rng.random(), today)
            if away:
                count = min(max(1, round(count * staff_rules.DELIVERY_TURN)), math.floor(away_left))
                away_left -= count
            else:
                count = min(count, math.floor(left))
                left -= count
            clients = make_clients(business, count, rng.randrange(2**31), thieves=not away)
            if not clients:
                continue
            if emp is None:
                game = TurnGame(business, clients, now=0.0)
                now = 0.0
                while not game.finished(now):
                    now = play_client(game, now, rng, miss)
            else:
                boss = next(
                    (
                        e
                        for e in staff
                        if e.get("role") == "encargado"
                        and franja in staff_rules.franjas(e)
                        and e["absent_day"] != iso
                    ),
                    None,
                )
                led = boss is not None and boss is not emp and not away
                start = (day * 24 + hour) * 3600.0
                game = staff_rules.play_turn(
                    business, emp, clients, start=start, franja=franja,
                    lead=staff_rules.lead(boss) if led and boss is not None else 0.0,
                )  # fmt: skip
                staff_rules.learn(emp, boost=helped.learn)
                if led:
                    emp["led_day"] = iso
            summary = game.summary()
            if emp is not None:
                grades.setdefault(emp["id"], []).append(summary.grade)
            for key, units in summary.units.items():
                business.stock[key] = max(0, business.stock.get(key, 0) - units)
                used[key] = used.get(key, 0) + units
            change = turn_reputation(
                sector, summary.reputation, summary.served, price_reputation(business)
            )
            business.reputation = max(0.0, min(REPUTATION_MAX, business.reputation + change))
            business.regulars = apply_loyalty(business.regulars, summary.loyalty)
            sales += summary.sales
        need = {k: 0.7 * need.get(k, 0) + 0.3 * used.get(k, 0) for k in need}
        bill = nightly_bill(business).total
        spoil(business, helped.keep)
        if turns == 0:
            drift(business)
        business.reputation = min(REPUTATION_MAX, business.reputation + helped.reputation)
        business.support = {"clients": helped.clients, "patience": helped.patience}
        fee = (FLAT_RATE_FEE if day < 7 else SELF_EMPLOYED_FEE) if day % 7 == 0 else 0
        payroll = 0
        for emp in staff:
            docked = emp["absent_day"] == iso
            payroll += staff_rules.payslip(emp, sector.key, today, docked=docked).cost
            staff_rules.night_mood(
                emp, paid=True, grades=grades.get(emp["id"], []),
                shield=0.0 if emp.get("role") == "rrhh" else helped.shield,
                led=emp.get("led_day") == iso,
            )  # fmt: skip
            emp["nights"] += 1
        day_net = sales - spent - bill - fee - payroll
        week_net += day_net - amortize(business)  # la amortización baja la base, no la caja
        tax = 0
        if day % 7 == 6:  # la declaración del lunes: la actividad es toda su renta
            tax = week_tax(WeekIncome(business=max(0, week_net))).quota
            week_net = 0
        cash += day_net - tax
        if payback != payback and cash >= 0:  # el primer día que recupera lo invertido
            payback = day + 1
        if day >= 14:
            nets.append(day_net - tax)  # el IRPF de la semana, el día que se declara
            for key, units in used.items():
                spent_units[key] = spent_units.get(key, 0) + units
    return Run(
        net_day=sum(nets) / max(1, len(nets)),
        stars=business.stars,
        opening=quote.total,
        payback=payback,
        units_day={k: v / max(1, len(nets)) for k, v in spent_units.items()},
        humor=sum(e["humor"] for e in staff) / len(staff) if staff else float("nan"),
    )


def pala_net(level: int) -> float:
    """Neto diario de cuatro turnos de jornada de obra en el puesto `level` (nota media)."""
    job = JOB_BY_KEY["obra"]
    gross_day = job.position(level, "").base_pay * 4
    week = gross_day * 7
    social = round(week * SS_WORKER_RATE)
    tax = week_tax(WeekIncome(work=week, social=social)).quota
    return (week - social - tax) / 7


def barrios_for(sector: Sector) -> list[Barrio]:
    """El barrio más barato, el del medio y el más caro para un sector."""
    ranked = sorted(BARRIOS, key=lambda b: opening_quote(sector, b).rent)
    return [ranked[0], ranked[len(ranked) // 2], ranked[-1]]


#: Semillas que se promedian en la fase 2: con una sola, la suerte de las colas pesa más
#: que una mejora.
SEEDS = (7, 8, 9, 10, 11, 12)


def mean_run(sector: Sector, barrio: Barrio, turns: int, miss: float, days: int, **extra) -> Run:  # noqa: ANN003
    """La media de `simulate` con el cupo del barrio en `SEEDS`."""
    runs = [simulate(sector, barrio, turns, miss, days, seed, pool=True, **extra) for seed in SEEDS]
    return Run(
        net_day=sum(r.net_day for r in runs) / len(runs),
        stars=sum(r.stars for r in runs) / len(runs),
        opening=runs[0].opening,
        payback=float("nan"),
    )


def phase2(days: int) -> None:
    """Lo que suma cada mejora, tamaño y campaña a un jugador bueno, y en cuánto se paga."""
    miss = PLAYERS["bueno"]
    print(f"{'sector':<10} {'variante':<28} {'t':>2} {'neto/día':>9} {'suma/día':>9} "
          f"{'cuesta':>8} {'se paga':>9}")  # fmt: skip
    for sector in SECTORS:
        barrio = barrios_for(sector)[1]
        for turns in (4, 8):
            base = mean_run(sector, barrio, turns, miss, days)
            variants: list[tuple[str, dict, int]] = []
            for item in equipment_for(sector.key):
                variants.append(
                    (item.name, {"equipment": (item.key,)}, item.cost + igic(item.cost))
                )
            for size in (1, 2):
                extra = (
                    opening_quote(sector, barrio, SIZES[size]).total
                    - opening_quote(sector, barrio).total
                )
                variants.append((f"Local {SIZES[size].name.lower()}", {"size": size}, extra))
            for campaign in CAMPAIGNS:
                variants.append((campaign.name, {"campaign": campaign.key}, 0))
            print(
                f"{sector.key:<10} {'(sin nada)':<28} {turns:>2} {base.net_day:>9,.0f}".replace(
                    ",", "."
                )
            )
            for name, extra, cost in variants:
                run = mean_run(sector, barrio, turns, miss, days, **extra)
                gain = run.net_day - base.net_day
                pays = (
                    f"{cost / gain:.0f} días"
                    if cost and gain > 0
                    else ("—" if not cost else "nunca")
                )
                print(
                    f"{'':<10} {name:<28} {turns:>2} {run.net_day:>9,.0f} {gain:>+9,.0f} "
                    f"{cost:>8,} {pays:>9}".replace(",", ".")
                )


def company_take(profit: int, salary: int) -> int:
    """Lo que se lleva el dueño de una sociedad con `profit` de beneficio y `salary` de sueldo.

    La gestoría de cada noche, Sociedades sobre lo que queda tras el sueldo, la reserva legal
    ya completa (la sociedad lleva tiempo) y el resto a dividendos con su 19 %.
    """
    books = 7 * (sl.BOOKKEEPING_NIGHT + igic(sl.BOOKKEEPING_NIGHT))
    base = profit - salary - books
    tax, _rate = sl.corporate_tax(base, weekly_sales=profit * 3)
    dividends = max(0, base - tax)
    pocket_salary = salary - week_tax(WeekIncome(work=salary)).quota
    return pocket_salary + dividends - round(dividends * 0.19) - SELF_EMPLOYED_FEE


def society(profits: tuple[int, ...]) -> None:
    """La tabla de autónomo contra sociedad limitada."""
    print(f"{'beneficio/semana':>17} {'autónomo':>10} {'sociedad':>10} {'mejor sueldo':>13} "
          f"{'diferencia':>11}")  # fmt: skip
    for profit in profits:
        alone = (
            profit
            - SELF_EMPLOYED_FEE
            - week_tax(WeekIncome(business=profit - SELF_EMPLOYED_FEE)).quota
        )
        books = 7 * (sl.BOOKKEEPING_NIGHT + igic(sl.BOOKKEEPING_NIGHT))
        room = profit - books  # el sueldo no puede llevarse lo que cuesta la gestoría
        best = max(
            (company_take(profit, salary), salary) for salary in range(0, room + 1, room // 50)
        )
        print(
            f"{profit:>17,} {alone:>10,} {best[0]:>10,} {best[1]:>13,} "
            f"{best[0] - alone:>+11,}".replace(",", ".")
        )


def hire(
    level: float, shift: str, seq: int, jornada: int = 4, *, role: str = "", horario: str = ""
) -> dict:
    """Un empleado de prueba con las tres habilidades a `level`, sin rasgos, indefinido.

    Con `horario` (una clave de `business_staff.HORARIOS`) manda sobre `shift` y `jornada`;
    `role` es su puesto, y cobra lo que pide en él (`business_staff.role_ask`).
    """
    skills = dict.fromkeys(staff_rules.SKILLS, level)
    candidate = {
        "key": seq, "name": f"E{seq}", "age": 30, "profile": "general", "traits": [],
        "skills": skills,
    }  # fmt: skip
    franjas = staff_rules.HORARIO_BY_KEY[horario].franjas if horario else None
    if franjas is not None:
        jornada = len(franjas)
    wage = staff_rules.role_ask(skills, (), jornada, role)
    return staff_rules.new_employee(
        candidate, seq=seq, contract="indefinido", jornada=jornada, shift=shift, wage=wage,
        start=DAY0, role=role, franjas=franjas,
    )  # fmt: skip


def puestos(days: int) -> None:
    """El mejor horario de cada sector y lo que suma cada puesto nuevo (9.41.0)."""

    def team_run(
        sector: Sector, barrio: Barrio, specs: list[tuple[str, str]], turns: int = 0
    ) -> Run:
        runs = []
        for seed in SEEDS:
            team = tuple(
                hire(50, "manana", i + 1, role=role, horario=key)
                for i, (role, key) in enumerate(specs)
            )
            runs.append(
                simulate(sector, barrio, turns, PLAYERS["bueno"], days, seed, pool=True, staff=team)
            )
        return Run(
            net_day=sum(r.net_day for r in runs) / len(runs),
            stars=sum(r.stars for r in runs) / len(runs),
            opening=runs[0].opening,
            payback=float("nan"),
            humor=sum(r.humor for r in runs) / len(runs),
        )

    print("Un empleado solo (todo a 50): los tres mejores horarios y la completa de mañana")
    for sector in SECTORS:
        barrio = barrios_for(sector)[1]
        rows = sorted(
            ((team_run(sector, barrio, [("", h.key)]), h) for h in staff_rules.HORARIOS),
            key=lambda r: -r[0].net_day,
        )
        top = ", ".join(f"{h.label} {run.net_day:,.0f}" for run, h in rows[:3])
        morning = next(run for run, h in rows if h.key == "0123")
        print(f"  {sector.key:<10} {top} · completa de mañana {morning.net_day:,.0f}"
              .replace(",", "."))  # fmt: skip
    print()
    extras: list[tuple[str, list[tuple[str, str]]]] = [
        ("dependiente partida", [("", "1256")]),
        ("repartidor media tarde", [("repartidor", "45")]),
        ("repartidor de tarde", [("repartidor", "4567")]),
        ("limpieza media", [("limpieza", "67")]),
        ("mozo de almacén media", [("reponedor", "01")]),
        ("community manager media", [("redes", "23")]),
        ("community manager completa", [("redes", "0123")]),
        ("recursos humanos media", [("rrhh", "23")]),
    ]
    print("Encargado en vez de un dependiente (tres a jornada completa: 8-16, 10-18 y 16-24)")
    trio = [("", "0123"), ("", "1234"), ("", "4567")]
    for sector in SECTORS:
        barrio = barrios_for(sector)[1]
        ref = team_run(sector, barrio, trio)
        run = team_run(sector, barrio, [("encargado", "0123"), *trio[1:]])
        print(f"  {sector.key:<10} {ref.net_day:>7,.0f} -> {run.net_day:>7,.0f} "
              f"({run.net_day - ref.net_day:+,.0f}) · humor {ref.humor:.0f} -> {run.humor:.0f}"
              .replace(",", "."))  # fmt: skip
    print()
    print("Lo que suma cada puesto al día (todo a 50), frente a dos plantillas: el dueño a 4")
    print("turnos y un dependiente de tarde / dos dependientes, de mañana y de tarde")
    bases = ((4, [("", "4567")]), (0, [("", "0123"), ("", "4567")]))
    for sector in SECTORS:
        barrio = barrios_for(sector)[1]
        refs = [team_run(sector, barrio, specs, turns) for turns, specs in bases]
        print(f"{sector.key:<10} {'(referencia)':<28} "
              + " / ".join(f"{r.net_day:>7,.0f} ({r.stars:.1f}★)" for r in refs)
              .replace(",", "."))  # fmt: skip
        for label, more in extras:
            if not staff_rules.ROLE_BY_KEY[more[0][0]].fits(sector.key):
                continue
            runs = [team_run(sector, barrio, [*specs, *more], turns) for turns, specs in bases]
            print(f"{'':<10} {label:<28} "
                  + " / ".join(f"{r.net_day - ref.net_day:>+7,.0f} (humor {r.humor:.0f})"
                               for r, ref in zip(runs, refs, strict=True))
                  .replace(",", "."))  # fmt: skip


def plantilla(days: int) -> None:
    """El negocio con empleados frente al dueño solo (jugador bueno a 4 turnos)."""
    print(f"SMI de una noche a jornada completa: {staff_rules.smi_night():,} Y$".replace(",", "."))
    print(f"{'sector':<10} {'quién abre':<34} {'sabe':>4} {'neto/día':>9} {'nómina/día':>10}")
    for sector in SECTORS:
        barrio = barrios_for(sector)[1]
        base = mean_run(sector, barrio, 4, PLAYERS["bueno"], days)
        print(f"{sector.key:<10} {'dueño, 4 turnos (bueno)':<34} {'':>4} "
              f"{base.net_day:>9,.0f}".replace(",", "."))  # fmt: skip
        for label, turns, specs in (
            ("1 de mañana", 0, (("manana", 4),)),
            ("mañana y tarde", 0, (("manana", 4), ("tarde", 4))),
            ("dueño 4 turnos + 1 de tarde", 4, (("tarde", 4),)),
            ("2 de mañana y 2 de tarde", 0,
             (("manana", 4), ("manana", 4), ("tarde", 4), ("tarde", 4))),
        ):  # fmt: skip
            for level in (30, 50, 70):
                runs = []
                payroll = 0
                for seed in SEEDS:
                    team = tuple(hire(level, s, i + 1, j) for i, (s, j) in enumerate(specs))
                    payroll = sum(staff_rules.payslip(e, sector.key, DAY0).cost for e in team)
                    runs.append(
                        simulate(sector, barrio, turns, PLAYERS["bueno"], days, seed,
                                 pool=True, staff=team)
                    )  # fmt: skip
                net = sum(r.net_day for r in runs) / len(runs)
                print(f"{'':<10} {label:<34} {level:>4} {net:>9,.0f} "
                      f"{payroll:>10,}".replace(",", "."))  # fmt: skip


def cadena(days: int) -> None:
    """Importar con nave frente al mayorista, el gerente y vender (fase 5)."""
    from bot.services import business_chain as chain_rules

    nave = chain_rules.Nave(1, "telde", 0)
    nave_night = chain_rules.nave_bill(nave).total
    print(f"Nave pequeña en Telde: {nave_night:,} Y$ la noche".replace(",", "."))
    print(f"{'sector':<10} {'quién':<16} {'mayorista/día':>13} {'ahorro/día':>10} "
          f"{'locales/nave':>12}")  # fmt: skip
    for sector in SECTORS:
        barrio = barrios_for(sector)[1]
        for label, turns, specs in (
            ("dueño 4 turnos", 4, ()),
            ("mañana y tarde", 0, (("manana", 4), ("tarde", 4))),
        ):
            runs = [
                simulate(
                    sector, barrio, turns, PLAYERS["bueno"], days, seed, pool=True,
                    staff=tuple(hire(50, s, i + 1, j) for i, (s, j) in enumerate(specs)),
                )
                for seed in SEEDS
            ]  # fmt: skip
            units: dict[str, float] = {}
            for run in runs:
                for key, value in (run.units_day or {}).items():
                    units[key] = units.get(key, 0.0) + value / len(runs)
            spend = saving = 0.0
            for product in sector.products:
                daily = units.get(product.key, 0.0)
                base, tax = wholesale(product, 1)
                spend += daily * (base + tax)
                if chain_rules.can_import(product):
                    quote = chain_rules.import_quote(product)
                    saving += daily * quote.saving / quote.units
            need = math.ceil(nave_night / saving) if saving > 0 else 0
            print(f"{sector.key:<10} {label:<16} {spend:>13,.0f} {saving:>10,.0f} "
                  f"{need if need else '—':>12}".replace(",", "."))  # fmt: skip
    print()
    print("Contenedor por producto (lo que no caduca):")
    for sector in SECTORS:
        for product in sector.products:
            if not chain_rules.can_import(product):
                continue
            quote = chain_rules.import_quote(product)
            print(f"  {sector.key:<10} {product.key:<12} {quote.total:>8,} frente a "
                  f"{quote.wholesale:>8,} · AIEM {quote.aiem:>6,} · "
                  f"{quote.saving:>+8,}".replace(",", "."))  # fmt: skip
    boss = hire(50, "manana", 9)
    boss["role"] = staff_rules.MANAGER
    boss["wage"] = staff_rules.manager_ask(boss["skills"], ())
    print()
    print(f"Gerente con todo a 50: {staff_rules.payslip(boss, 'tienda', DAY0).cost:,} Y$ la "
          "noche (bruto y cuota de la empresa)".replace(",", "."))  # fmt: skip
    print("Vender la sociedad: el comprador paga 5 veces la media de sus 3 últimas semanas")
    for weekly in (100_000, 250_000, 500_000):
        print(f"  {weekly:>9,} Y$ a la semana -> {weekly * chain_rules.CAPITALIZE:>10,} Y$"
              .replace(",", "."))  # fmt: skip


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dias", type=int, default=28)
    parser.add_argument("--fase2", action="store_true", help="Mejoras, tamaños y campañas.")
    parser.add_argument("--sociedad", action="store_true", help="Autónomo o sociedad.")
    parser.add_argument("--plantilla", action="store_true", help="Empleados (fase 4).")
    parser.add_argument("--cadena", action="store_true", help="Nave, gerente y venta (fase 5).")
    parser.add_argument("--puestos", action="store_true", help="Horarios y puestos (9.41.0).")
    args = parser.parse_args()
    if args.puestos:
        puestos(args.dias)
        return
    if args.cadena:
        cadena(args.dias)
        return
    if args.plantilla:
        plantilla(args.dias)
        return
    if args.sociedad:
        society((50_000, 100_000, 150_000, 250_000, 400_000, 700_000, 1_200_000))
        return
    if args.fase2:
        phase2(args.dias)
        return
    print("Pala (obra), neto diario a 4 turnos:")
    for level in (1, 3, 5):
        print(f"  P{level}: {pala_net(level):>8,.0f} Y$".replace(",", "."))
    print("(★ = estrellas al final; «a 5★» = día en que llega a las 5 estrellas)")
    print()
    print(f"{'sector':<10} {'barrio':<18} {'alquiler':>8} {'montar':>8} {'t':>2} "
          f"{'jugador':<9} {'neto/día':>9} {'★':>4} {'recupera':>9}")  # fmt: skip
    for sector in SECTORS:
        for barrio in barrios_for(sector):
            rent = opening_quote(sector, barrio).rent
            for turns in (4, 8):
                for player, miss in PLAYERS.items():
                    run = simulate(sector, barrio, turns, miss, args.dias, seed=7)
                    payback = f"día {run.payback:.0f}" if run.payback == run.payback else "nunca"
                    print(
                        f"{sector.key:<10} {barrio.name:<18} {rent:>8} {run.opening:>8} "
                        f"{turns:>2} {player:<9} {run.net_day:>9,.0f} {run.stars:>4} "
                        f"{payback:>9}".replace(",", ".")
                    )


if __name__ == "__main__":
    main()
