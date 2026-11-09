"""Días que tarda un jugador en ascender en cada puesto de `pala`, con las partidas reales.

Uso, desde la raíz del repo:

    python docs/ritmo_pala.py                 # tabla por puesto y resumen por carrera
    python docs/ritmo_pala.py --runs 60       # más rápido (menos partidas por puesto)
    python docs/ritmo_pala.py --json docs/ritmo_pala.json   # guarda la tabla para el simulador

Modelo (lo usa `docs/economia_sim.py`):

- El jugador acierta cada pulsación con probabilidad q, ajustada para que la nota media del
  minijuego real del puesto (`work_games.new_game`) sea la `NOTA` pedida (75 un jugador
  normal, 85 uno bueno), con un bonus de tiempo de 1 a 5 puntos al completar.
- Familia «en casa feliz» (+3 a la barra cuando sube): con 4 turnos al día se queda arriba.
- Día tipo: 4 ordinarios; en sanidad con guardias, 3 ordinarios y 1 guardia; en el
  funcionariado con temario pendiente, 2 turnos de estudio más (no son jornada); un extra
  el primer día si la tarea pide extras.
- Sin deseo del día ni barra de los eventos, sin accidentes ni herramientas de la tienda.
- De P5 en adelante el minijuego rota (`Position.on`): cada día se juega el que toca, con el
  acierto ajustado a la misma nota media en cada uno. En el Cuadrante, la Negociación, la
  Auditoría y el Despacho el jugador pulsa con probabilidad q el botón de `best()` y, si no,
  otro al azar. El órdago final no se juega (es a cara o cruz).

Para cada puesto juega `RUNS` carreras día a día hasta llenar la barra y cumplir las tareas
de `work.promotion_blockers` y da la media de días. Los días que pide el puesto no cuentan
aquí: los pone el simulador (`work.required_days`, con la experiencia), que se queda con el
mayor de los dos. Las tareas de minijuego casi nunca mandan; a nota 75 suele mandar `good`
(turnos de 90 o más). El resumen por carrera sí suma los días pedidos.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, "src")
from bot.services.work import (  # noqa: E402
    EVENT_CHANCE,
    FAMILY_TIERS,
    GOOD_SCORE,
    PERFORMANCE_MAX,
    PERFORMANCE_START,
    Job,
    Mechanic,
    Position,
    ShiftKind,
    performance_delta,
    required_days,
)
from bot.services.work_catalog import JOBS, events_for  # noqa: E402
from bot.services.work_games import (  # noqa: E402
    ORDAGO_PASS,
    AuditGame,
    DeskGame,
    new_game,
)

#: Los minijuegos de la rotación: se juegan de verdad, pulsando `best()` con probabilidad q.
ROTATING = (Mechanic.PLAN, Mechanic.DEAL, Mechanic.AUDIT, Mechanic.DESK)

FELIZ = FAMILY_TIERS[0]
STAT_OF = {
    Mechanic.SPOT: "caught",
    Mechanic.MEMORY: "perfect_rounds",
    Mechanic.DIALOGUE: "smooth",
    Mechanic.SORT: "sorted",
    Mechanic.CALC: "best",
}


def play(pos: Position, q: float, rng: random.Random, guard: bool = False, retries: int = 0):
    """Juega una partida real del puesto con acierto q. Devuelve `(nota, contadores)`."""
    g = new_game(pos, rng, now=0, guard=guard)
    if g.mechanic in ROTATING:
        # Sin órdago: es a cara o cruz y no cambia la nota media.
        g.ordago = ORDAGO_PASS
        while (current := g.current) is not None:
            best = g.best()
            moves = g.moves() if isinstance(g, AuditGame) else range(len(current.options))
            others = [i for i in moves if i != best]
            g.press(best if rng.random() < q or not others else rng.choice(others), 0)
        if isinstance(g, DeskGame):
            # La nota del Despacho sale de cómo queda, no de puntos por jugada.
            g.finished_at = None
            score = round(g.score() + (0 if g.crisis else rng.uniform(1, 5)))
        else:
            score = round(100 * max(0, g.points) / g.max_points + rng.uniform(1, 5))
        # Las tareas cuentan en el contador del minijuego de siempre (como `PalaService`).
        usual = pos.task_mechanic
        if usual is Mechanic.DIG:
            return max(0, min(100, score)), {"clean": int(g.correct > 0)}
        hits = g.correct // 2 if usual is Mechanic.MEMORY else g.correct
        return max(0, min(100, score)), {STAT_OF[usual]: hits}
    points = broken = perfect = correct = 0
    left = retries
    for r in g.rounds:
        if g.mechanic is Mechanic.MEMORY:
            step = 0
            while step < len(r.answer):
                if rng.random() < q:
                    step += 1
                    points += 1
                elif left:
                    left -= 1
                else:
                    break
            if step == len(r.answer):
                perfect += 1
                correct += 1
            continue
        while True:
            if rng.random() < q:
                points += 1
                correct += 1
                break
            if left:
                left -= 1
                continue
            wrong = [i for i in range(len(r.options)) if i not in r.answer]
            if rng.choice(wrong) in r.penalty:
                broken += 1
                points -= 1
            break
    score = max(0, min(100, round(100 * max(0, points) / g.max_points + rng.uniform(1, 5))))
    stats: dict[str, int] = {}
    if g.mechanic is Mechanic.DIG:
        stats["clean"] = int(broken == 0 and correct > 0)
    else:
        stats[STAT_OF[g.mechanic]] = perfect if g.mechanic is Mechanic.MEMORY else correct
    return score, stats


def tune(pos: Position, target: float, rng: random.Random, retries: int) -> float:
    """El acierto por pulsación que da de media la nota `target` en ese puesto."""
    lo, hi = 0.0, 1.0
    for _ in range(14):
        q = (lo + hi) / 2
        m = sum(play(pos, q, rng, retries=retries)[0] for _ in range(150)) / 150
        lo, hi = (q, hi) if m < target else (lo, q)
    return (lo + hi) / 2


def study_pos(job: Job, level: int) -> Position:
    return Position(level, "Estudio", 0, Mechanic.DIALOGUE, 40, (), 0, job.study or "")


def position_days(
    job: Job, branch: str, pos: Position, target: float, rng: random.Random, runs: int
) -> float:
    """Días de calendario medios para cumplir las condiciones de ascenso de `pos`."""
    retries = sum(1 for p in job.perks(pos.level, branch) if p.retry)
    # Un acierto por cada minijuego que rota en el puesto, todos a la misma nota media.
    turns = 1 + len(pos.alts)
    qs = {pos.on(d).mechanic: tune(pos.on(d), target, rng, retries) for d in range(turns)}
    goals = {t.stat: t.goal for t in pos.tasks}
    guards_on = job.has_guards(pos.level)
    trunk = pos.level <= job.trunk_top
    has_events = bool(events_for(job.key, pos.level, 100, branch="" if trunk else branch))
    sq = tune(study_pos(job, pos.level), target, rng, 0) if "temario" in goals else 0
    total = 0
    for _ in range(runs):
        perf, prog, day = PERFORMANCE_START, {}, 0
        while True:
            day += 1
            kinds = [ShiftKind.ORDINARY] * (3 if guards_on else 4)
            if guards_on:
                kinds.append(ShiftKind.GUARD)
            if "extras" in goals and prog.get("extras", 0) < goals["extras"]:
                kinds.append(ShiftKind.EXTRA)
            for kind in kinds:
                today = pos.on(day)
                q = qs[today.mechanic]
                s, st = play(today, q, rng, guard=kind is ShiftKind.GUARD, retries=retries)
                for k, v in st.items():
                    prog[k] = prog.get(k, 0) + v
                prog["good"] = prog.get("good", 0) + (s >= GOOD_SCORE)
                if kind is ShiftKind.GUARD:
                    prog["guards"] = prog.get("guards", 0) + 1
                elif kind is not ShiftKind.ORDINARY:
                    prog["extras"] = prog.get("extras", 0) + 1
                if has_events and rng.random() < EVENT_CHANCE:
                    prog["events"] = prog.get("events", 0) + 1
                d = performance_delta(s, kind, cap=job.ordinary_cap, family=FELIZ)
                perf = max(0, min(PERFORMANCE_MAX, perf + d))
            if "temario" in goals and prog.get("temario", 0) < goals["temario"]:
                for _ in range(2):
                    smooth = play(study_pos(job, pos.level), sq, rng)[1]["smooth"]
                    prog["temario"] = prog.get("temario", 0) + smooth
            done = perf >= PERFORMANCE_MAX and all(prog.get(k, 0) >= g for k, g in goals.items())
            if done or day > 400:
                break
        total += day
    return total / runs


def table(target: float = 75, runs: int = 120, seed: int = 1) -> dict[str, float]:
    """Días por puesto: `{"oficio/rama/nivel": días}` (el tronco, con rama vacía).

    El último puesto de cada carrera no asciende y no sale.
    """
    rng = random.Random(seed)
    out: dict[str, float] = {}
    for job in JOBS:
        for branch, ladder in job.ladders:
            for pos in ladder[:-1]:
                trunk = pos.level <= job.trunk_top
                key = f"{job.key}/{'' if trunk else branch}/{pos.level}"
                if key in out:
                    continue
                out[key] = position_days(job, branch, pos, target, rng, runs)
    return out


def career_days(days: dict[str, float], job: Job, branch: str) -> tuple[float, float]:
    """`(días del tronco, días de la rama)` de una carrera con una tabla de `table`: en cada
    puesto, el mayor entre lo medido (barra y tareas) y los días que pide."""

    def need(pos: Position, key: str) -> float:
        return max(days.get(key, 0.0), float(required_days(pos, job))) if pos.tasks else 0.0

    trunk = sum(need(p, f"{job.key}//{p.level}") for p in job.positions)
    rama = sum(
        need(p, f"{job.key}/{branch}/{p.level}")
        for p in job.ladder(branch)
        if p.level > job.trunk_top
    )
    return trunk, rama


def main() -> None:
    runs = int(sys.argv[sys.argv.index("--runs") + 1]) if "--runs" in sys.argv else 120
    result = {str(target): table(target, runs) for target in (75, 85)}
    if "--json" in sys.argv:
        path = Path(sys.argv[sys.argv.index("--json") + 1])
        path.write_text(json.dumps(result, indent=1, sort_keys=True), encoding="utf-8")
        print(f"Guardado en {path}")
    for target, days in result.items():
        print(f"\n===== NOTA MEDIA {target} =====")
        for job in JOBS:
            for branch, _ladder in job.ladders:
                trunk, rama = career_days(days, job, branch)
                print(
                    f"{job.key:13} {branch or '-':11} tronco {trunk:5.1f}  rama {rama:5.1f}  "
                    f"total {trunk + rama:5.1f}"
                )


if __name__ == "__main__":
    main()
