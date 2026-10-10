"""Pruebas del camino de Node del autobús: la escena pintada sin navegador.

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
from test_bus_scene import CARDS, THREE
from test_node_scene import needs_node, node_ready

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene
from bot.services.bus_render import Board, H, W, board_state, meta_state, reveal_states
from bot.services.bus_scene import SCENE, BusScene
from bot.services.node_scene import NodeScene

module_loop = pytest.mark.asyncio(loop_scope="module")
LOCAL_CHROMIUM = Path("/opt/pw-browsers/chromium")
NO_CHROMIUM = "/no/existe/chromium"


def test_el_autobus_deja_que_el_pintor_ponga_su_exportframe() -> None:
    html = SCENE.read_text(encoding="utf-8")
    assert "window.exportFrame ??=" in html
    assert "toDataURL" not in html.split("window.exportFrame ??=")[0]


async def test_sin_node_ni_chromium_el_autobus_cae_a_pillow() -> None:
    scene = BusScene(executable_path=NO_CHROMIUM, node="/no/existe/node")
    png = await scene.board(Board(active=0))
    assert scene.painter.disabled and scene.browser.disabled and scene.disabled
    assert Image.open(io.BytesIO(png)).size == (W, H)
    await scene.close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scene() -> BusScene:
    """Una escena del autobús con Node (y sin Chromium), compartida por el módulo."""
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    escena = BusScene(executable_path=NO_CHROMIUM)
    yield escena
    await escena.close()


@needs_node
@module_loop
async def test_con_node_la_mesa_es_un_png_de_la_medida_y_no_esta_vacia(scene: BusScene) -> None:
    png = await scene.board(THREE)
    assert not scene.painter.disabled
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG" and image.size == (W, H)
    assert len(image.convert("RGB").getcolors(1 << 20) or []) > 100


@needs_node
@module_loop
async def test_con_node_la_mano_sale_con_sus_gif_sin_usar_el_navegador(scene: BusScene) -> None:
    reveal = await scene.reveal(CARDS, 3, seed=4)
    assert not scene.painter.disabled
    # Si se hubiera probado Chromium (que no existe), estaría desactivado.
    assert not scene.browser.disabled
    for media in (reveal.win, reveal.lose):
        gif = Image.open(io.BytesIO(media.gif or b""))
        assert gif.format == "GIF" and gif.size == (W, H) and gif.n_frames > 10
        assert Image.open(io.BytesIO(media.png)).size == (W, H)


@needs_node
@module_loop
async def test_node_y_chromium_pintan_lo_mismo_el_autobus() -> None:
    """Mismo motor (Skia): solo cambia algún borde de letra, no colores ni posiciones."""
    if not LOCAL_CHROMIUM.exists():
        pytest.skip("No hay Chromium en esta máquina")
    painter = NodeScene(SCENE, name="El pintor del autobús")
    browser = BrowserScene(
        SCENE, name="chromium", ready="loadFonts()", executable_path=str(LOCAL_CHROMIUM)
    )
    common, win, lose = reveal_states(CARDS, 3, seed=6)
    states = [board_state(THREE), *common, *win, *lose]
    try:
        from_browser = await browser.render(meta_state(), states)
        from_node = await painter.render(meta_state(), states)
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
