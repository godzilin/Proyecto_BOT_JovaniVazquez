"""Simulador de la economía de los gallos: entrenar, la forma y lo que deja pelear con la casa.

Para calibrar las cifras de `bot.services.gallos` (precios del entrenamiento, forma,
ventaja y su tope) con las funciones reales, no a ojo (Biblia, «Equilibrio: medir en
tiradas»). Simula jugadores con una estrategia fija durante N días y cuenta lo que
gastan en entrenar (con IGIC), lo que ganan o pierden peleando, cuántos gallos se les
mueren y el poder con que acaban. La vara es el IMV máximo: 1.500 Y$ al día.

Uso:

    .venv/Scripts/python docs/gallos_sim.py               # todas las estrategias, 90 días
    .venv/Scripts/python docs/gallos_sim.py --dias 30 --jugadores 500
    .venv/Scripts/python docs/gallos_sim.py --solo constante

Cada día el jugador entrena sus sesiones (el stat más bajo, repartido entre los tres),
juega el minijuego de cada una con `gallos_gym.autoplay` (con el combo y los eventos de
forma; el suplemento no se lo toma) y luego pelea mientras le quede forma para la
ventaja (o las peleas fijas de su estrategia), apostando lo que cubre la ventaja
(`edge_cap`) o la apuesta fija de la estrategia. La pelea se sortea con `house_odds`, la
muerte con `dies` y la lesión con `fight_injury` (lesionado, ese día ya no pelea), como
en el cog; el final (KO, huida o puntos) sale de `simulate`. No cuenta impuestos del
juego (la retención del 19 % del neto diario): es el margen antes de Hacienda.
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
from dataclasses import dataclass, replace
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bot.services import gallos  # noqa: E402
from bot.services import gallos_gym as gym  # noqa: E402
from bot.services.gallos import (  # noqa: E402
    RIVAL_BY_KEY,
    STAT_MAX,
    TRAIN_COOLDOWN,
    Corner,
    Finish,
    RoosterRecord,
    Stat,
    after_fight,
    after_train,
    dies,
    draw_winner,
    edge_cap,
    fight_injury,
    form_now,
    house_odds,
    reborn,
    simulate,
    train_price,
)
from bot.services.taxes import igic  # noqa: E402

IMV = 1_500
DAY = 86_400


@dataclass(frozen=True)
class Strategy:
    """Cómo juega un jugador simulado.

    Attributes:
        name: Clave para `--solo`.
        sessions: Entrenamientos al día (como mucho `TRAIN_PER_DAY`).
        rival: Clave del rival de la casa contra el que pelea.
        fights: Peleas fijas al día; `None` = mientras la forma cubra una pelea entera.
        stake: Apuesta fija; `None` = lo que cubre la ventaja (`edge_cap`).
        min_form: Con peleas libres, solo pelea mientras la forma no baje de esto.
        policy: Cómo juega los minijuegos (`gallos_gym.autoplay`): "best" o "random".
    """

    name: str
    sessions: int
    rival: str
    fights: int | None = None
    stake: int | None = None
    min_form: int = 50
    policy: str = "best"


STRATEGIES = (
    Strategy("constante", 6, "marte"),
    Strategy("constante_media", 6, "credito"),
    Strategy("constante_facil", 6, "falcon"),
    Strategy("agotador", 6, "credito", min_form=10),
    Strategy("torpe", 6, "credito", policy="random"),
    Strategy("casual", 3, "arancel"),
    Strategy("vago", 0, "falcon", fights=5, stake=1_000),
    Strategy("vago_marte", 0, "marte", fights=5, stake=1_000),
)


@dataclass
class Outcome:
    trained: int = 0
    fought: int = 0
    deaths: int = 0
    power: float = 0.0
    fights: int = 0
    late: int = 0  # neto de la segunda mitad (ya entrenado)
    lives: list[int] | None = None  # días que vivió cada gallo muerto


def _weakest(record: RoosterRecord) -> Stat:
    return min(Stat, key=lambda s: (record.stat(s) >= STAT_MAX, record.stat(s)))


def play(strategy: Strategy, days: int, rng: random.Random) -> Outcome:
    out = Outcome(lives=[])
    born = 0
    record = RoosterRecord(name="Sim")
    rival = RIVAL_BY_KEY[strategy.rival]
    start = date(2026, 11, 2)
    for d in range(days):
        day = start + timedelta(days=d)
        now = d * DAY + 9 * 3600.0
        spent_today = 0
        for _ in range(strategy.sessions):
            stat = _weakest(record)
            base = train_price(record.stat(stat))
            spent_today += base + igic(base)
            record = after_train(record, stat, now=now, day=day)
            game = gym.start(gym.pick_kind(stat, rng), rng, now)
            score = gym.autoplay(game, strategy.policy, rng, now)
            record, _ = gym.after_session(
                record, stat, score, trait=gym.Trait.BALANCED, now=now, day=day
            )
            event = gym.roll_event(rng)
            if event is not None and not event.choice:
                record = gym.apply_event(record, event, now)
            now += TRAIN_COOLDOWN
        net_today = 0
        fights = 0
        while True:
            form = form_now(record, now)
            if strategy.fights is not None:
                if fights >= strategy.fights:
                    break
            elif form < max(strategy.min_form, gallos.FORM_PER_FIGHT):
                break
            stake = strategy.stake or edge_cap(record.power)
            chance, mult = house_odds(rival, record.power, form, stake)
            fight = simulate(draw_winner(chance, rng), rng)
            won = fight.winner is Corner.RED
            net_today += int(stake * mult) - stake if won else -stake
            record = after_fight(record, won=won, finish=fight.finish, day=day, now=now)
            fights += 1
            now += 120
            if not won and fight.finish is Finish.KO and dies(record, rng, form):
                out.deaths += 1
                out.lives.append(d - born)
                born = d
                record = replace(reborn(record, 1), form=0.0, form_at=now)
                break
            if not won and fight.finish is Finish.KO and fight_injury(form, rng):
                break  # lesionado: hoy ya no pelea
        out.trained += spent_today
        out.fought += net_today
        out.fights += fights
        if d >= days // 2:
            out.late += net_today - spent_today
    out.power = record.power
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dias", type=int, default=90)
    parser.add_argument("--jugadores", type=int, default=300)
    parser.add_argument("--solo", help="Una estrategia (clave de STRATEGIES).")
    parser.add_argument("--semilla", type=int, default=1)
    parser.add_argument(
        "--fijar",
        action="append",
        default=[],
        metavar="CONSTANTE=VALOR",
        help="Prueba otra cifra de bot.services.gallos (p. ej. EDGE_MAX=0.2, FORM_PER_TRAIN=15).",
    )
    args = parser.parse_args()
    for item in args.fijar:
        name, value = item.split("=", 1)
        old = getattr(gallos, name)
        setattr(gallos, name, type(old)(Fraction(value) if isinstance(old, Fraction) else value))
    rng = random.Random(args.semilla)
    chosen = [s for s in STRATEGIES if not args.solo or s.name == args.solo]
    print(
        f"{args.dias} días, {args.jugadores} jugadores por estrategia. Cifras por día y "
        f"jugador (media); el IMV máximo es {IMV} Y$.\n"
    )
    header = (
        f"{'estrategia':<17}{'entrena':>9}{'peleas':>9}{'neto':>9}{'neto 2.ª':>10}"
        f"{'×IMV 2.ª':>9}{'pel/día':>8}{'muertes':>8}{'vida':>6}{'poder':>7}"
    )
    print(header)
    print("-" * len(header))
    for strategy in chosen:
        runs = [play(strategy, args.dias, rng) for _ in range(args.jugadores)]
        days = args.dias
        half = days - days // 2
        train = statistics.mean(r.trained for r in runs) / days
        fought = statistics.mean(r.fought for r in runs) / days
        late = statistics.mean(r.late for r in runs) / half
        print(
            f"{strategy.name:<17}{-train:>9.0f}{fought:>9.0f}{fought - train:>9.0f}"
            f"{late:>10.0f}{late / IMV:>9.2f}"
            f"{statistics.mean(r.fights for r in runs) / days:>8.1f}"
            f"{statistics.mean(r.deaths for r in runs):>8.2f}"
            f"{statistics.median([x for r in runs for x in r.lives] or [days]):>6.0f}"
            f"{statistics.mean(r.power for r in runs):>7.0f}"
        )


if __name__ == "__main__":
    main()
