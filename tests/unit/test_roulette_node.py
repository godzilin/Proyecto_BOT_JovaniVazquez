"""Pruebas del camino de Node de la ruleta: la escena pintada sin navegador.

Se saltan si no hay `node` o falta `@napi-rs/canvas` (`npm ci` en la raíz). La
comparación con Chromium se salta también si no hay navegador.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest
import pytest_asyncio
from PIL import Image, ImageChops, ImageStat
from test_node_scene import needs_node, node_ready
from test_roulette_scene_browser import HISTORY, outcome

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene
from bot.services.node_scene import NodeScene
from bot.services.roulette import Wager, parse_bet
from bot.services.roulette_scene import (
    SCENE,
    H,
    RouletteScene,
    W,
    board_state,
    meta_state,
    spin_states,
)

module_loop = pytest.mark.asyncio(loop_scope="module")
LOCAL_CHROMIUM = Path("/opt/pw-browsers/chromium")
NO_CHROMIUM = "/no/existe/chromium"
NO_NODE = "/no/existe/node"


def test_la_ruleta_deja_que_el_pintor_ponga_su_exportframe() -> None:
    html = SCENE.read_text(encoding="utf-8")
    assert "window.exportFrame ??=" in html
    assert "toDataURL" not in html.split("window.exportFrame ??=")[0]


async def test_sin_node_ni_chromium_la_ruleta_cae_a_pillow() -> None:
    scene = RouletteScene(executable_path=NO_CHROMIUM, node=NO_NODE)
    png = await scene.board(HISTORY)
    assert scene.painter.disabled and scene.browser.disabled and scene.disabled
    assert Image.open(io.BytesIO(png)).format == "PNG"
    await scene.close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scene() -> RouletteScene:
    """Una escena de la ruleta con Node (y sin Chromium), compartida por el módulo."""
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    escena = RouletteScene(executable_path=NO_CHROMIUM)
    yield escena
    await escena.close()


@needs_node
@module_loop
async def test_con_node_la_mesa_es_un_png_de_la_medida_y_no_esta_vacia(
    scene: RouletteScene,
) -> None:
    wagers = [Wager(parse_bet("17"), 100), Wager(parse_bet("rojo"), 500)]
    png = await scene.board(HISTORY, wagers)
    assert not scene.painter.disabled
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG" and image.size == (W, H)
    assert len(image.convert("RGB").getcolors(1 << 20) or []) > 100


@needs_node
@module_loop
async def test_con_node_el_giro_sale_como_gif_sin_usar_el_navegador(scene: RouletteScene) -> None:
    result = outcome(17, [("17", 100), ("rojo", 50)], {17: 200})
    states = spin_states(result, history=HISTORY, seed=3)
    media = await scene.spin(result, history=HISTORY, seed=3)
    assert not scene.painter.disabled
    # Si se hubiera probado Chromium (que no existe), estaría desactivado.
    assert not scene.browser.disabled
    gif = Image.open(io.BytesIO(media.gif))
    assert gif.format == "GIF" and gif.size == (W, H) and gif.n_frames == len(states)
    assert Image.open(io.BytesIO(media.png)).size == (W, H)


@needs_node
@module_loop
async def test_node_y_chromium_pintan_lo_mismo_la_ruleta() -> None:
    """Mismo motor (Skia): solo cambia algún borde de letra, no colores ni posiciones."""
    if not LOCAL_CHROMIUM.exists():
        pytest.skip("No hay Chromium en esta máquina")
    painter = NodeScene(SCENE, name="El pintor de la ruleta")
    browser = BrowserScene(
        SCENE,
        name="chromium",
        viewport=(W, H),
        ready="loadFonts()",
        executable_path=str(LOCAL_CHROMIUM),
    )
    result = outcome(17, [("17", 100), ("rojo", 50)], {17: 200})
    states = [board_state(HISTORY), *spin_states(result, history=HISTORY, seed=6)]
    meta = {**meta_state(), "seed": 6}
    try:
        from_browser = await browser.render(meta, states)
        from_node = await painter.render(meta, states)
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
