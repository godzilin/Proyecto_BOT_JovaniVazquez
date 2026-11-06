"""Pruebas del motor del juego del pico (`bot.services.picar_physics`)."""

import ast
import re
from pathlib import Path

import pytest

import bot.services.picar_physics as physics
from bot.services.picar_physics import (
    MAX_TICKS,
    PICKS,
    RADIUS,
    Pick,
    Run,
    params_hash,
    simulate,
)
from bot.services.picar_world import AIR, BLOCK_ID, BLOCKS, DRAGON_ROW, World

#: Un pico que no se gasta nunca: baja hasta el dragón.
ETERNAL = Pick("eternal", "Eterno", 10**9, 50, 0.4)


def _summary(run: Run) -> tuple:
    return (
        run.ticks,
        run.life,
        sorted(run.broken.items()),
        run.max_row,
        run.layer,
        run.dragon,
        run.hits,
        run.life_gained,
        run.explosions,
        run.longest_chain,
        run.nudges,
        run.final,
    )


@pytest.mark.parametrize("pick", list(PICKS))
def test_la_misma_semilla_y_el_mismo_pico_dan_la_misma_partida(pick: str) -> None:
    for seed in (0, 1, 99):
        a, b = simulate(seed, pick), simulate(seed, pick)
        assert a.frames == b.frames
        assert a.events == b.events
        assert _summary(a) == _summary(b)
        assert a.fingerprint() == b.fingerprint()


@pytest.mark.parametrize("pick", list(PICKS))
def test_sin_fotogramas_sale_la_misma_partida(pick: str) -> None:
    for seed in range(5):
        full, bare = simulate(seed, pick), simulate(seed, pick, record=False)
        assert not bare.frames and not bare.events
        assert _summary(full) == _summary(bare)
        assert full.fingerprint() == bare.fingerprint()
        assert full.frames[-1] == full.final


def test_semillas_distintas_dan_partidas_distintas() -> None:
    prints = {simulate(seed, "iron", record=False).fingerprint() for seed in range(40)}
    assert len(prints) == 40


def _overlap(world: World, x: float, y: float) -> float:
    """Lo que más se mete el pico en un bloque que queda."""
    worst = 0.0
    for r in range(max(0, int(y - RADIUS) - 1), int(y + RADIUS) + 2):
        for c in range(9):
            if world.block(r, c) == AIR:
                continue
            px, py = min(max(x, c), c + 1), min(max(y, r), r + 1)
            d = ((x - px) ** 2 + (y - py) ** 2) ** 0.5
            worst = max(worst, RADIUS - d)
    return worst


@pytest.mark.lenta
@pytest.mark.parametrize("pick", list(PICKS))
def test_el_pico_nunca_atraviesa_un_bloque_sin_romperlo(pick: str) -> None:
    for seed in range(25):
        run = simulate(seed, pick)
        world = World(seed)
        removals: dict[int, list[tuple[int, int]]] = {}
        for event in run.events:
            if event.kind in ("break", "blast"):
                removals.setdefault(event.tick, []).append((event.row, event.col))
        for tick, (x, y, _) in enumerate(run.frames, start=1):
            for row, col in removals.get(tick, ()):
                assert world.block(row, col) != AIR, "se rompe un bloque que ya no estaba"
                world.remove(row, col)
            assert _overlap(world, x, y) < 1e-9, (seed, tick, x, y)


@pytest.mark.parametrize("pick", list(PICKS))
def test_la_partida_siempre_termina(pick: str) -> None:
    for seed in range(150):
        run = simulate(seed, pick, record=False)
        assert not run.timeout and run.ticks < MAX_TICKS
        assert run.stalls == 0, "el pico se quedó metido en un bloque"
        assert run.life <= 0 or run.dragon


def test_el_pico_que_no_se_gasta_llega_al_dragon() -> None:
    run = simulate(4, ETERNAL)
    assert run.dragon and run.max_row == DRAGON_ROW and run.layer == "end"
    assert run.events[-1].kind == "dragon"
    layers = [event.layer for event in run.events if event.kind == "layer"]
    assert layers == ["soil", "caves", "deep", "nether", "end"]


@pytest.mark.parametrize("pick", ["stone", "diamond", "netherite"])
def test_las_cuentas_de_vida_y_bloques_cuadran(pick: str) -> None:
    for seed in range(20):
        run = simulate(seed, pick)
        struck = [e for e in run.events if e.kind in ("hit", "break")]
        assert len(struck) == run.hits
        spent = sum(BLOCKS[BLOCK_ID[e.block]].hardness for e in struck)
        gained = sum(e.amount for e in run.events if e.kind in ("chest", "craft"))
        assert gained == run.life_gained
        assert run.life == PICKS[pick].life - spent + gained
        broken: dict[str, int] = {}
        for e in run.events:
            if e.kind in ("break", "blast"):
                broken[e.block] = broken.get(e.block, 0) + 1
        assert broken == run.broken
        assert set(run.minerals) <= {key for key in broken if BLOCKS[BLOCK_ID[key]].mineral}
        assert run.explosions == sum(e.kind == "explode" for e in run.events)


def test_la_mesa_de_crafteo_devuelve_la_vida_entera() -> None:
    pick = PICKS["netherite"]
    found = 0
    for seed in range(40):
        run = simulate(seed, pick)
        life = pick.life
        for e in run.events:
            if e.kind in ("hit", "break"):
                life -= BLOCKS[BLOCK_ID[e.block]].hardness
            if e.kind == "chest":
                life += e.amount
            elif e.kind == "craft":
                assert e.block == "crafting_table"
                assert e.amount == max(0, pick.life - life)
                life += e.amount
                assert life >= pick.life
                found += 1
    assert found


def test_los_cofres_dan_la_vida_de_su_bloque() -> None:
    seen = set()
    for seed in range(60):
        for e in simulate(seed, "netherite").events:
            if e.kind == "chest":
                low, high = BLOCKS[BLOCK_ID[e.block]].life
                assert PICKS["netherite"].life * low // 100 <= e.amount
                assert e.amount <= PICKS["netherite"].life * high // 100
                seen.add(e.block)
    assert {"chest", "treasure"} <= seen


def test_la_tnt_revienta_en_cadena() -> None:
    runs = [simulate(seed, "netherite") for seed in range(60)]
    assert any(run.explosions for run in runs)
    assert max(run.longest_chain for run in runs) >= 2


def test_el_hash_cambia_si_cambia_un_parametro(monkeypatch: pytest.MonkeyPatch) -> None:
    base = params_hash()
    assert re.fullmatch(r"[0-9a-f]{64}", base) and params_hash() == base
    monkeypatch.setattr(physics, "GRAVITY", physics.GRAVITY + 1)
    assert params_hash() != base
    monkeypatch.undo()
    monkeypatch.setitem(PICKS, "wood", Pick("wood", "Madera", 41, 1, 0.45))
    assert params_hash() != base


def test_la_fisica_no_usa_funciones_de_la_libm() -> None:
    # sin, cos, atan2, exp… pueden dar otro último bit en Windows y en Linux, y la
    # calibración se hace en un sistema y el juego corre en el otro.
    forbidden = {"sin", "cos", "tan", "asin", "acos", "atan", "atan2", "exp", "log", "pow"}
    forbidden |= {"hypot", "log2", "log10", "expm1", "log1p", "numpy", "numba"}
    root = Path(physics.__file__).parent
    for name in ("picar_physics.py", "picar_world.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                used.add(node.attr)
            elif isinstance(node, ast.alias):
                used.update(node.name.split("."))
        assert not used & forbidden, (name, used & forbidden)
