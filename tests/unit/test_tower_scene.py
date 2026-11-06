"""Pruebas de bot.services.tower_scene: lo que se ve en cada jugada y cómo se pinta.

Las de la línea de tiempo no pintan nada. Las de Node se saltan si no hay `node`
o falta `@napi-rs/canvas` (`npm ci` en la raíz); la comparación con Chromium,
también si no hay navegador.
"""

from __future__ import annotations

import asyncio
import io
import random
from pathlib import Path

import pytest
import pytest_asyncio
from PIL import Image, ImageChops, ImageStat
from test_node_scene import needs_node, node_ready, open_helpers

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene
from bot.services.node_scene import NodeScene
from bot.services.tower import FLOORS, Difficulty, Status, TowerGame
from bot.services.tower_scene import (
    FINAL_FRAME_MS,
    SCENE,
    H,
    TowerScene,
    W,
    _meta,
    cashout_states,
    opening_state,
    pick_states,
    rest_state,
    short_multiplier,
    tension_frames,
)

module_loop = pytest.mark.asyncio(loop_scope="module")
LOCAL_CHROMIUM = Path("/opt/pw-browsers/chromium")
NO_CHROMIUM = "/no/existe/chromium"


def game_at(difficulty: Difficulty, floor: int) -> TowerGame:
    """Una partida con el dragón en la puerta 0 de cada piso, subida hasta `floor`."""
    game = TowerGame.new(100, difficulty, random.Random(1))
    game.dragons = tuple(frozenset(range(difficulty.dragons)) for _ in range(FLOORS))
    for _ in range(floor):
        game.climb(difficulty.width - 1)
    return game


def same_picture(a: dict, b: dict) -> bool:
    """Si dos estados enseñan lo mismo (el reloj y la duración aparte)."""
    skip = ("ms", "t")
    return {k: v for k, v in a.items() if k not in skip} == {
        k: v for k, v in b.items() if k not in skip
    }


def test_la_escena_deja_que_el_pintor_ponga_su_exportframe() -> None:
    html = SCENE.read_text(encoding="utf-8")
    assert "window.exportFrame ??=" in html
    assert "toDataURL" not in html.split("window.exportFrame ??=")[0]
    assert "Math.random(" not in html


def test_un_huevo_acaba_en_el_piso_de_arriba_quieto() -> None:
    game = game_at(Difficulty.EASY, 2)
    states = pick_states(game, 3)
    after = game_at(Difficulty.EASY, 3)
    assert same_picture(states[-1], rest_state(after))
    assert states[-1]["ms"] == FINAL_FRAME_MS
    assert any(s.get("climb") for s in states)
    assert not any(s.get("dragon") for s in states)
    assert game.floor == 2  # la partida de verdad no se toca


def test_la_tension_crece_con_el_piso() -> None:
    assert tension_frames(8) > tension_frames(4) > tension_frames(0) > 0
    low = pick_states(game_at(Difficulty.EASY, 0), 3)
    high = pick_states(game_at(Difficulty.EASY, 8), 3)
    assert max(s.get("heart", 0) for s in high) > max(s.get("heart", 0) for s in low)


def test_un_dragon_quema_y_acaba_con_la_torre_destapada() -> None:
    game = game_at(Difficulty.HARD, 3)
    states = pick_states(game, 0)
    assert any(s.get("dragon", {}).get("fire") for s in states)
    final = states[-1]
    assert final["view"] == "tower" and final["burned"] == [3, 0]
    assert final["panel"]["label"] == "QUEMADO" and final["panel"]["reveal"] == FLOORS
    # Los dragones se encienden piso a piso antes de quedarse quietos.
    tours = [s["tour"] for s in states if s.get("view") == "tower"]
    assert tours == sorted(tours) and len(set(tours)) >= FLOORS


def test_al_azar_la_ruleta_acaba_en_la_puerta_elegida() -> None:
    game = game_at(Difficulty.MASTER, 1)
    states = pick_states(game, 2, roulette=True)
    rings = [
        next(i for i, d in enumerate(s["doors"]) if d.get("ring"))
        for s in states
        if any(d.get("ring") for d in s.get("doors", []))
    ]
    assert rings[-1] == 2 and len(rings) > game.difficulty.width
    # Va frenando: cada paso dura lo mismo o más que el anterior.
    times = [s["ms"] for s in states[: len(rings)]]
    assert times == sorted(times)


def test_coronar_sube_a_la_cima() -> None:
    game = game_at(Difficulty.HARD, 8)
    states = pick_states(game, 1)
    assert any(s.get("view") == "summit" and s.get("crown") for s in states)
    final = states[-1]
    assert final["view"] == "tower" and final["crowned"]
    assert final["panel"]["label"] == "¡CORONADA!"


def test_cobrar_ensena_en_fantasma_lo_que_habia_detras() -> None:
    game = game_at(Difficulty.EXPERT, 2)
    states = cashout_states(game)
    ghosts = states[0]["doors"]
    assert [d["show"] for d in ghosts] == ["ghost-dragon", "ghost-dragon", "ghost-egg"]
    final = states[-1]
    assert final["view"] == "tower" and final["ghost"] == 2
    assert final["panel"]["label"] == "COBRAS"
    assert game.status is Status.PLAYING


def test_el_primer_piso_no_depende_de_los_dragones() -> None:
    state = opening_state(Difficulty.NORMAL, 250)
    assert state["view"] == "floor" and state["floor"] == 0
    assert state["panel"]["current"] == 0 and state["panel"]["picks"] == []


def test_los_multiplicadores_enormes_van_sin_decimales() -> None:
    assert short_multiplier(132) == "×1,32"
    assert short_multiplier(25_952_256) == "×259.522"


async def test_sin_node_ni_chromium_no_hay_imagen() -> None:
    scene = TowerScene(executable_path=NO_CHROMIUM, node="/no/existe/node")
    assert await scene.opening(Difficulty.EASY, 100) is None
    assert await scene.pick(game_at(Difficulty.EASY, 0), 3) is None
    assert scene.disabled
    await scene.close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scene() -> TowerScene:
    """Una torre con Node (y sin Chromium), compartida por el módulo."""
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    escena = TowerScene(executable_path=NO_CHROMIUM)
    yield escena
    await escena.close()


@needs_node
@module_loop
async def test_con_node_cada_tema_es_un_png_distinto(scene: TowerScene) -> None:
    pngs = [await scene.opening(d, 100) for d in Difficulty]
    assert not scene.painter.disabled
    images = [Image.open(io.BytesIO(png or b"")) for png in pngs]
    assert all(i.format == "PNG" and i.size == (W, H) for i in images)
    assert len({png for png in pngs}) == len(pngs)


@pytest.mark.lenta
@needs_node
@module_loop
async def test_con_node_cada_jugada_es_un_gif_que_acaba_en_su_png(scene: TowerScene) -> None:
    game = game_at(Difficulty.NORMAL, 4)
    for media in (
        await scene.pick(game, 2),
        await scene.pick(game, 0, roulette=True),
        await scene.cashout(game),
    ):
        assert media is not None and media.seconds > 0.5
        gif = Image.open(io.BytesIO(media.gif))
        assert gif.format == "GIF" and gif.size == (W, H) and gif.n_frames > 10
        gif.seek(gif.n_frames - 1)
        last = gif.convert("RGB")
        png = Image.open(io.BytesIO(media.png)).convert("RGB")
        # El último fotograma del GIF es el PNG que queda (salvo la paleta del GIF).
        assert sum(ImageStat.Stat(ImageChops.difference(last, png)).mean) / 3 < 4


@pytest.mark.lenta
@needs_node
@module_loop
async def test_repartida_pinta_lo_mismo_que_de_un_tiron(scene: TowerScene) -> None:
    game = game_at(Difficulty.MASTER, 8)
    states = pick_states(game, 3)
    meta = _meta(game)
    painter = scene.painter
    calls: list[str] = []
    original = painter._request

    def spy(call: str, args: list | None = None, **extra: object) -> dict:
        calls.append(call)
        return original(call, args, **extra)

    await open_helpers(painter, meta, states)
    painter._request = spy  # type: ignore[method-assign]
    try:
        split = await painter.render(meta, states)
        setups = calls.count("setup")
        whole = await painter.render(meta, states, procs=1)
    finally:
        painter._request = original  # type: ignore[method-assign]
    assert split is not None and whole is not None
    assert setups > 1  # de verdad se repartió
    a = browser_scene.assemble(split, (W, H))
    b = browser_scene.assemble(whole, (W, H))
    assert [x.tobytes() for x in a] == [y.tobytes() for y in b]


@needs_node
@module_loop
async def test_node_y_chromium_pintan_lo_mismo_la_torre() -> None:
    """Mismo motor (Skia): solo cambia algún borde de letra, no colores ni posiciones."""
    if not LOCAL_CHROMIUM.exists():
        pytest.skip("No hay Chromium en esta máquina")
    painter = NodeScene(SCENE, name="El pintor de la torre")
    browser = BrowserScene(
        SCENE, name="chromium", ready="loadFonts()", executable_path=str(LOCAL_CHROMIUM)
    )
    game = game_at(Difficulty.EXPERT, 3)
    states = pick_states(game, 0)[::4]
    try:
        from_browser = await browser.render(_meta(game), states)
        from_node = await painter.render(_meta(game), states)
    finally:
        await browser.close()
        await painter.close()
    if from_browser is None:
        pytest.skip("Chromium no arranca en esta máquina")
    assert from_node is not None
    a, b = await asyncio.gather(
        asyncio.to_thread(browser_scene.assemble, from_browser, (W, H)),
        asyncio.to_thread(browser_scene.assemble, from_node, (W, H)),
    )
    assert len(a) == len(b) == len(states)
    for x, y in zip(a, b, strict=True):
        assert sum(ImageStat.Stat(ImageChops.difference(x, y)).mean) / 3 < 4
