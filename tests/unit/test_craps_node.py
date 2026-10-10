"""Pruebas del camino de Node de los dados: la escena pintada sin navegador.

Se saltan si no hay `node` o falta `@napi-rs/canvas` (`npm ci` en la raíz). La
comparación con Chromium se salta también si no hay navegador.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest
import pytest_asyncio
from craps_fakes import game_after, table_for
from PIL import Image, ImageChops, ImageStat
from test_node_scene import needs_node, node_ready

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene
from bot.services.craps_render import OPENING_REST, H, W, board_state, meta_state, throw_states
from bot.services.craps_scene import SCENE, CrapsScene
from bot.services.node_scene import NodeScene

module_loop = pytest.mark.asyncio(loop_scope="module")
LOCAL_CHROMIUM = Path("/opt/pw-browsers/chromium")
NO_CHROMIUM = "/no/existe/chromium"


def test_los_dados_dejan_que_el_pintor_ponga_su_exportframe() -> None:
    html = SCENE.read_text(encoding="utf-8")
    assert "window.exportFrame ??=" in html
    assert "toDataURL" not in html.split("window.exportFrame ??=")[0]


async def test_sin_node_ni_chromium_los_dados_caen_a_pillow() -> None:
    scene = CrapsScene(executable_path=NO_CHROMIUM, node="/no/existe/node")
    png = await scene.board(table_for(None), OPENING_REST)
    assert scene.painter.disabled and scene.browser.disabled and scene.disabled
    assert Image.open(io.BytesIO(png)).size == (W, H)
    await scene.close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scene() -> CrapsScene:
    """Una escena de los dados con Node (y sin Chromium), compartida por el módulo."""
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    escena = CrapsScene(executable_path=NO_CHROMIUM)
    yield escena
    await escena.close()


@needs_node
@module_loop
async def test_con_node_la_mesa_es_un_png_de_la_medida_y_no_esta_vacia(scene: CrapsScene) -> None:
    png = await scene.board(table_for(None), OPENING_REST)
    assert not scene.painter.disabled
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG" and image.size == (W, H)
    assert len(image.convert("RGB").getcolors(1 << 20) or []) > 100


@needs_node
@module_loop
async def test_con_node_la_tirada_sale_como_gif_sin_usar_el_navegador(scene: CrapsScene) -> None:
    media = await scene.throw(table_for(game_after((1, 5))), seed=4)
    assert not scene.painter.disabled
    # Si se hubiera probado Chromium (que no existe), estaría desactivado.
    assert not scene.browser.disabled
    gif = Image.open(io.BytesIO(media.gif))
    assert gif.format == "GIF" and gif.size == (W, H) and gif.n_frames > 10
    assert Image.open(io.BytesIO(media.png)).size == (W, H)


@needs_node
@module_loop
async def test_node_y_chromium_pintan_lo_mismo_los_dados() -> None:
    """Mismo motor (Skia): solo cambia algún borde de letra, no colores ni posiciones."""
    if not LOCAL_CHROMIUM.exists():
        pytest.skip("No hay Chromium en esta máquina")
    painter = NodeScene(SCENE, name="El pintor de los dados")
    browser = BrowserScene(
        SCENE, name="chromium", ready="loadFonts()", executable_path=str(LOCAL_CHROMIUM)
    )
    table = table_for(game_after((1, 5)))
    states, rest = throw_states(table, seed=6)
    states = [board_state(table_for(None), OPENING_REST), *states]
    try:
        from_browser = await browser.render(meta_state(), states)
        from_node = await painter.render(meta_state(), states)
    finally:
        await browser.close()
        await painter.close()
    if from_browser is None:
        pytest.skip("Chromium no arranca en esta máquina")
    assert from_node is not None and rest is not None
    a, b = await asyncio.gather(
        asyncio.to_thread(browser_scene.assemble, from_browser, (W, H)),
        asyncio.to_thread(browser_scene.assemble, from_node, (W, H)),
    )
    assert len(a) == len(b) == len(states)
    for x, y in zip(a, b, strict=True):
        assert sum(ImageStat.Stat(ImageChops.difference(x, y)).mean) / 3 < 4
