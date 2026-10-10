"""Pruebas de bot.services.chicken_scene: el Pollo con canvas (Node, Chromium o Pillow).

Las de Node se saltan si no hay `node` o falta `@napi-rs/canvas` (`npm ci` en la
raíz); la comparación con Chromium, si no hay navegador en la máquina.
"""

from __future__ import annotations

import asyncio
import io
import random
from pathlib import Path

import pytest
import pytest_asyncio
from PIL import Image, ImageChops, ImageStat
from test_node_scene import needs_node, node_ready

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene
from bot.services.chicken import DIFFICULTY_BY_KEY, ChickenGame, format_multiplier
from bot.services.chicken_render import (
    FINISH,
    LANE,
    SIDEWALK,
    resting_scene,
    start_scene,
    timeline,
    world_width,
)
from bot.services.chicken_scene import (
    SCENE,
    VIEW_W,
    ChickenScene,
    H,
    W,
    banner_for,
    frame_state,
    meta_state,
)
from bot.services.economy import format_amount
from bot.services.node_scene import NodeScene

module_loop = pytest.mark.asyncio(loop_scope="module")
LOCAL_CHROMIUM = Path("/opt/pw-browsers/chromium")
NO_CHROMIUM = "/no/existe/chromium"
NO_NODE = "/no/existe/node"
PILLOW_SIZE = (640, 320)
SEED = 7


def new_game(key: str = "media", *, hit_lane: int | None = None, stake: int = 100) -> ChickenGame:
    return ChickenGame(stake=stake, difficulty=DIFFICULTY_BY_KEY[key], hit_lane=hit_lane)


def run_over_game() -> ChickenGame:
    """Dos carriles cruzados y atropello en el tercero."""
    game = new_game(hit_lane=3)
    assert game.cross() and game.cross()
    assert not game.cross()
    return game


def cashed_game() -> ChickenGame:
    """Tres carriles cruzados y cobrado, con el coche esperando en el quinto."""
    game = new_game(hit_lane=5)
    for _ in range(3):
        assert game.cross()
    game.cash_out()
    return game


def finished_game() -> ChickenGame:
    """Cruza toda la carretera (sin coche) y cobra en la meta."""
    game = new_game("hardcore", hit_lane=None)
    game.cross_until(10**12)
    game.cash_out()
    assert game.finished_road
    return game


def mean_difference(a: Image.Image, b: Image.Image) -> float:
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    return sum(ImageStat.Stat(diff).mean) / 3


# -- Reserva con Pillow ------------------------------------------------------------------


async def test_sin_node_ni_chromium_el_pollo_cae_a_pillow() -> None:
    scene = ChickenScene(executable_path=NO_CHROMIUM, node=NO_NODE)
    difficulty = DIFFICULTY_BY_KEY["media"]
    game = run_over_game()
    try:
        start = await scene.start(difficulty, stake=100, seed=SEED)
        board = await scene.board(game, seed=SEED)
        media = await scene.hops(game, start=0, seed=SEED)
    finally:
        await scene.close()
    assert scene.disabled
    for png in (start, board, media.png):
        image = Image.open(io.BytesIO(png))
        assert image.format == "PNG" and image.size == PILLOW_SIZE
    gif = Image.open(io.BytesIO(media.gif))
    assert gif.format == "GIF" and gif.size == PILLOW_SIZE and gif.n_frames > 1
    gif.seek(gif.n_frames - 1)
    gif.load()


# -- Node ---------------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scene() -> ChickenScene:
    """Una escena del Pollo con Node (y sin Chromium), compartida por el módulo."""
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    escena = ChickenScene(executable_path=NO_CHROMIUM)
    yield escena
    await escena.close()


def assert_table_png(png: bytes) -> None:
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG" and image.size == (W, H) == (640, 360)
    assert len(image.convert("RGB").getcolors(1 << 20) or []) > 100


@needs_node
@module_loop
async def test_con_node_el_inicio_es_un_png_de_la_mesa_y_no_esta_vacio(
    scene: ChickenScene,
) -> None:
    png = await scene.start(DIFFICULTY_BY_KEY["facil"], stake=100, seed=SEED)
    assert not scene.painter.disabled
    assert_table_png(png)


@needs_node
@module_loop
async def test_con_node_el_tablero_es_un_png_de_la_mesa_y_no_esta_vacio(
    scene: ChickenScene,
) -> None:
    png = await scene.board(cashed_game(), seed=SEED, note="Cobrado")
    assert not scene.painter.disabled
    assert_table_png(png)


@needs_node
@module_loop
async def test_con_node_el_paso_es_un_gif_cuyo_final_se_parece_al_png(
    scene: ChickenScene,
) -> None:
    game = new_game(hit_lane=6)
    assert game.cross() and game.cross()
    media = await scene.hops(game, start=1, seed=SEED)
    assert not scene.painter.disabled
    # Si se hubiera probado Chromium (que no existe), estaría desactivado.
    assert not scene.browser.disabled
    gif = Image.open(io.BytesIO(media.gif))
    assert gif.format == "GIF" and gif.size == (W, H) and gif.n_frames > 5
    assert gif.getpalette() is not None
    gif.seek(gif.n_frames - 1)
    last = gif.convert("RGB")
    png = Image.open(io.BytesIO(media.png))
    assert png.size == (W, H)
    assert mean_difference(last, png) < 12
    steps = timeline(game, start=1, seed=SEED)
    assert media.seconds == sum(ms for _scene, ms in steps) / 1000


@needs_node
@module_loop
async def test_node_y_chromium_pintan_lo_mismo_el_pollo() -> None:
    """Mismo motor (Skia): solo cambia algún borde de letra, no colores ni posiciones."""
    if not LOCAL_CHROMIUM.exists():
        pytest.skip("No hay Chromium en esta máquina")
    painter = NodeScene(SCENE, name="El pintor del pollo")
    browser = BrowserScene(
        SCENE, name="chromium", ready="loadFonts()", executable_path=str(LOCAL_CHROMIUM)
    )
    run_over = run_over_game()
    cashed = cashed_game()
    steps = timeline(run_over, start=1, seed=SEED)
    hit_states = [frame_state(scene) for scene, _ms in steps]
    hit_states.append(frame_state(resting_scene(run_over, seed=SEED), banner_for(run_over)))
    cases = [
        (meta_state(run_over.difficulty, stake=100, seed=SEED), hit_states),
        (
            meta_state(cashed.difficulty, stake=100, seed=SEED),
            [frame_state(resting_scene(cashed, seed=SEED), banner_for(cashed))],
        ),
    ]
    try:
        for meta, states in cases:
            from_browser = await browser.render(meta, states)
            from_node = await painter.render(meta, states)
            if from_browser is None:
                pytest.skip("Chromium no arranca en esta máquina")
            assert from_node is not None
            a, b = await asyncio.gather(
                asyncio.to_thread(browser_scene.assemble, from_browser, (W, H)),
                asyncio.to_thread(browser_scene.assemble, from_node, (W, H)),
            )
            assert len(a) == len(b) == len(states)
            for x, y in zip(a, b, strict=True):
                assert mean_difference(x, y) < 4
    finally:
        await browser.close()
        await painter.close()


# -- Cartel del final -----------------------------------------------------------------------


def test_no_hay_cartel_mientras_se_juega() -> None:
    game = new_game(hit_lane=4)
    assert banner_for(game) is None
    game.cross()
    assert banner_for(game) is None


def test_el_cartel_del_atropello_dice_el_carril_y_lo_perdido() -> None:
    game = run_over_game()
    banner = banner_for(game)
    assert banner is not None and banner["kind"] == "splat"
    assert banner["title"] == "¡ATROPELLADO!"
    assert f"Carril {game.crossed + 1}" in banner["sub"]
    assert format_amount(game.stake) in banner["sub"]


def test_el_cartel_de_cobrar_lleva_multiplicador_e_importe() -> None:
    game = cashed_game()
    banner = banner_for(game)
    assert banner is not None and banner["kind"] == "cash"
    assert banner["title"] == "¡COBRADO!"
    assert format_multiplier(game.cents) in banner["sub"]
    assert format_amount(game.payout) in banner["sub"]


def test_el_cartel_de_meta_sale_al_llegar_al_final() -> None:
    game = finished_game()
    banner = banner_for(game)
    assert banner is not None and banner["kind"] == "meta"
    assert banner["title"] == "¡META!"
    assert format_multiplier(game.cents) in banner["sub"]
    assert format_amount(game.payout) in banner["sub"]


# -- Estado de cada fotograma -----------------------------------------------------------------


def test_la_camara_nunca_se_sale_del_mundo() -> None:
    for difficulty in DIFFICULTY_BY_KEY.values():
        limit = world_width(difficulty) - VIEW_W
        game = ChickenGame(stake=100, difficulty=difficulty, hit_lane=None)
        game.cross_until(10**12)
        game.cash_out()
        sidewalk = start_scene(difficulty, stake=100, seed=SEED)
        goal = resting_scene(game, seed=SEED)
        scenes = [sidewalk, goal]
        scenes.extend(scene for scene, _ms in timeline(game, start=0, seed=SEED))
        for scene in scenes:
            cam = frame_state(scene)["cam"]
            assert 0 <= cam <= limit, (difficulty.key, scene.chicken_at, cam)
        assert frame_state(sidewalk)["cam"] == 0
        assert frame_state(goal)["cam"] == round(limit, 2)


def test_el_marcador_ensena_el_valor_de_cobrar_durante_el_gif() -> None:
    game = new_game(hit_lane=6)
    for _ in range(3):
        game.cross()
    states = [frame_state(scene) for scene, _ms in timeline(game, start=2, seed=SEED)]
    assert states
    for state in states:
        assert state["hud"]["level"] >= 1
        assert state["hud"]["value"] is not None
        assert state["hud"]["value"] != "Perdido"


def test_sin_carriles_cruzados_el_marcador_aun_no_tiene_valor() -> None:
    state = frame_state(start_scene(DIFFICULTY_BY_KEY["media"], stake=100, seed=SEED))
    assert state["hud"]["level"] == 0 and state["hud"]["value"] is None


def test_en_el_atropello_el_marcador_marca_perdido() -> None:
    game = run_over_game()
    states = [frame_state(scene) for scene, _ms in timeline(game, start=0, seed=SEED)]
    dead = [s for s in states if s["splat"]]
    assert dead and all(s["hud"]["value"] == "Perdido" and s["hud"]["dead"] for s in dead)
    final = frame_state(resting_scene(game, seed=SEED), banner_for(game))
    assert final["hud"]["value"] == "Perdido" and final["banner"] is not None


# -- Lo fijo de la partida --------------------------------------------------------------------


def test_los_coches_aparcados_son_los_mismos_que_pinta_pillow() -> None:
    for key, seed in (("facil", 3), ("media", 11), ("hardcore", 12345)):
        difficulty = DIFFICULTY_BY_KEY[key]
        meta = meta_state(difficulty, stake=100, seed=seed)
        expected = [
            lane
            for lane in range(1, difficulty.lanes + 1)
            if random.Random(seed + lane).random() < 0.55
        ]
        assert meta["parked"] == expected
        assert meta["world"] == SIDEWALK + difficulty.lanes * LANE + FINISH
        assert len(meta["vehicles"]) == len(meta["cents"]) == difficulty.lanes


def test_un_taxi_es_siempre_blanco() -> None:
    taxis = 0
    for seed in range(40):
        for vehicle in meta_state(DIFFICULTY_BY_KEY["facil"], stake=100, seed=seed)["vehicles"]:
            if vehicle["kind"] == "taxi":
                taxis += 1
                assert vehicle["color"] == [250, 250, 250]
    assert taxis > 0
