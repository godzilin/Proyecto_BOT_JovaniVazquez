"""Simula tres estilos de jugar a `pala` para calibrar la familia, con el código real.

Uso, desde la raíz del repo: `python docs/familia_pala.py` (imprime una tabla).
Con `--casa`, repite los tres estilos viviendo con sus padres y en cada tramo de `casa`
(`bot.services.home.TIERS`: recarga de la batería y de la familia y desgaste de cada turno).

Los estilos, durante 28 días, en un puesto 1 de obra (base 1.800 Y$, nota 75):

- **casual**: dos turnos al día (13:00 y 21:00, más o menos).
- **normal**: los cuatro ordinarios, repartidos (mañana, mediodía, tarde y noche).
- **grinder**: despierto de 9:00 a 1:00, ficha cada vez que puede (extras, B y
  tres barraquitos al día para seguir).

Cuenta por día: turnos, bruto aproximado (sin impuestos; el B, descontando lo que
se lleva la Inspección de media), lo que sube la barra (los ascensos) y en qué
tramo de familia pasa las horas. Los accidentes dejan 24 h de baja.

Resultado con las cifras de `work.py` (familia que se recupera 1,5 por hora;
recarga ×1,5 / ×1 / ×0,5 / ×0,25 según el tramo): el casual y el normal pasan el
100 % del tiempo en casa feliz; el grinder ficha 5 turnos al día, saca un ~24 %
más de bruto que el normal (con las reglas de antes, un ~75 %) y llena la barra
a la mitad de ritmo, con unos 15 accidentes al mes. El dinero rápido le cuesta
los ascensos. Con 2 por hora el grinder se quedaba en un +37 % y casi la misma
barra que el normal.
"""

import random
import sys
from datetime import datetime

sys.path.insert(0, "src")
from bot.services.home import TIERS, Tier
from bot.services.levels import TIMEZONE, local_day
from bot.services.work import (
    ACCIDENT_TIRED,
    ACCIDENT_ZOMBIE,
    BATTERY_MAX,
    BATTERY_MIN,
    INSPECTION_CHANCE,
    INSPECTION_SURCHARGE,
    OVERTIME_PAY,
    SICK_LEAVE_SECONDS,
    TIRED,
    Contract,
    ShiftKind,
    battery_now,
    change_family,
    family_breakdown,
    family_now,
    family_tier,
    home_relief,
    next_shift_kind,
    performance_delta,
    roll_over_day,
    settle,
    shift_cost,
)

BASE, SCORE = 1_800, 75
FACTOR = 0.7 + 0.6 * SCORE / 100
DAYS, SEEDS = 28, 20
#: Lunes 5 de octubre de 2026, 0:00 en Canarias.
START = datetime(2026, 10, 5, tzinfo=TIMEZONE).timestamp()
COFFEES, BARRAQUITO = 3, 20


def schedule(style: str, rng: random.Random) -> list[float]:
    """Horas del día (locales) a las que la persona mira `pala`."""
    if style == "casual":
        return [13 + rng.random(), 21 + rng.random()]
    if style == "normal":
        return [9.5 + rng.random(), 14 + rng.random(), 18 + rng.random(), 22 + rng.random()]
    return [9 + i / 4 for i in range(16 * 4)]


def run(style: str, seed: int, home: Tier | None = None) -> dict[str, float]:
    rng = random.Random(seed)
    contract = Contract(job="obra", level=1, battery_at=START, family_at=START)
    if home is not None:
        contract.home_regen, contract.home_family = home.regen, home.family
        contract.home_strain = home.strain
    stats = {"shifts": 0, "gross": 0.0, "performance": 0, "accidents": 0}
    hours: dict[str, int] = {}
    for day in range(DAYS):
        coffees = 0
        checks = [(h, False) for h in schedule(style, rng)] + [(h, True) for h in range(24)]
        for hour, sample in sorted(checks):
            now = START + day * 86_400 + hour * 3600
            if sample:
                name = family_tier(family_now(contract, now)).name
                hours[name] = hours.get(name, 0) + 1
                continue
            roll_over_day(contract, now)
            if contract.sick_until > now:
                continue
            kind = next_shift_kind(contract, now)
            if style != "grinder" and kind is not ShiftKind.ORDINARY:
                continue
            if (
                kind is not ShiftKind.ORDINARY
                and contract.no_extras_day == local_day(now).isoformat()
            ):
                continue
            cost = shift_cost(kind)
            settle(contract, now)
            while (
                style == "grinder" and contract.battery - cost < BATTERY_MIN and coffees < COFFEES
            ):
                contract.battery = min(float(BATTERY_MAX), contract.battery + BARRAQUITO)
                coffees += 1
            battery = battery_now(contract, now)
            if battery <= BATTERY_MIN:
                continue
            family_before = family_now(contract, now)
            pay = BASE * FACTOR
            if kind is ShiftKind.EXTRA:
                pay *= OVERTIME_PAY
            elif kind is ShiftKind.BLACK:
                pay *= 1 - INSPECTION_CHANCE * (1 + INSPECTION_SURCHARGE)
            stats["gross"] += pay
            stats["shifts"] += 1
            stats["performance"] += performance_delta(
                SCORE, kind, family=family_tier(family_before)
            )
            contract.battery = battery - cost
            parts = home_relief(family_breakdown(kind, now), contract.home_strain)
            change_family(contract, sum(v for _, v in parts), now)
            contract.shifts_today += 1
            if kind is not ShiftKind.ORDINARY:
                contract.extras_week += 1
            contract.shift_day = local_day(now).isoformat()
            chance = ACCIDENT_ZOMBIE if battery < 0 else ACCIDENT_TIRED if battery < TIRED else 0
            if rng.random() < chance:
                stats["accidents"] += 1
                contract.sick_until = now + SICK_LEAVE_SECONDS
    total = sum(hours.values())
    stats.update({name: hours.get(name, 0) / total for name in hours})
    return stats


def by_home() -> None:
    """`--casa`: lo que cambia cada tramo de `casa` frente a vivir con tus padres."""
    print(f"{DAYS} días, puesto 1 de obra. Medias por día según la casa:")
    for style in ("casual", "normal", "grinder"):
        for home in (None, *reversed(TIERS)):
            runs = [run(style, seed, home) for seed in range(SEEDS)]
            shifts = sum(r["shifts"] for r in runs) / len(runs) / DAYS
            gross = sum(r["gross"] for r in runs) / len(runs) / DAYS
            bar = sum(r["performance"] for r in runs) / len(runs) / DAYS
            accidents = sum(r["accidents"] for r in runs) / len(runs)
            happy = sum(r.get("En casa feliz", 0) for r in runs) / len(runs)
            name = "con sus padres" if home is None else home.name
            print(
                f"{style:8} {name:15} turnos {shifts:4.2f} · bruto {gross:6.0f} · barra "
                f"+{bar:4.1f} · accidentes {accidents:4.1f} · casa feliz {happy * 100:3.0f} %"
            )


def main() -> None:
    if "--casa" in sys.argv:
        by_home()
        return
    print(f"{DAYS} días, puesto 1 de obra (base {BASE}), nota {SCORE}. Medias por día:")
    for style in ("casual", "normal", "grinder"):
        runs = [run(style, seed) for seed in range(SEEDS)]

        def mean(key: str, runs: list[dict[str, float]] = runs) -> float:
            return sum(r.get(key, 0) for r in runs) / len(runs)

        tiers = " · ".join(
            f"{name} {round(mean(name) * 100)} %"
            for name in ("En casa feliz", "Tranquila", "Mosqueada", "En crisis")
        )
        print(
            f"{style:8} turnos {mean('shifts') / DAYS:4.1f} · bruto {mean('gross') / DAYS:6.0f} · "
            f"barra +{mean('performance') / DAYS:4.1f} · accidentes {mean('accidents'):4.1f} "
            f"en total · {tiers}"
        )


if __name__ == "__main__":
    main()
