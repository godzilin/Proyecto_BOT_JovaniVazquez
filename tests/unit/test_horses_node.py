"""Pruebas del camino de Node de los caballos: la carrera pintada sin navegador.

La parrilla y el boleto siguen en Chromium. Se saltan si no hay `node` o falta
`@napi-rs/canvas` (`npm ci` en la raíz). La comparación con Chromium se salta
también si no hay navegador.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest
import pytest_asyncio
from PIL import Image, ImageChops, ImageStat
from test_horses_cog import fixed_result
from test_horses_scene import card_of, photo_result
from test_node_scene import needs_node, node_ready

from bot.services.horses_render import GATE_FRAMES, H, W
from bot.services.horses_scene import SCENE, SceneRenderer
from bot.services.node_scene import NodeScene

module_loop = pytest.mark.asyncio(loop_scope="module")
LOCAL_CHROMIUM = Path("/opt/pw-browsers/chromium")
NO_CHROMIUM = "/no/existe/chromium"
NO_NODE = "/no/existe/node"


def test_los_caballos_dejan_que_el_pintor_ponga_su_exportframe() -> None:
    html = SCENE.read_text(encoding="utf-8")
    assert "window.exportFrame ??=" in html
    assert "toDataURL" not in html.split("window.exportFrame ??=")[0]


async def test_sin_node_ni_chromium_la_carrera_cae_a_pillow() -> None:
    renderer = SceneRenderer(executable_path=NO_CHROMIUM, node=NO_NODE)
    media = await renderer.race(card_of(), fixed_result((0, 1, 2, 3, 4, 5)))
    assert renderer.painter.disabled and renderer.disabled
    assert Image.open(io.BytesIO(media.gif)).format == "GIF"
    assert Image.open(io.BytesIO(media.png)).size == (W, H)
    await renderer.close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def renderer() -> SceneRenderer:
    """Un `SceneRenderer` con Node (y sin Chromium), compartido por el módulo."""
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    pintor = SceneRenderer(executable_path=NO_CHROMIUM)
    yield pintor
    await pintor.close()


@needs_node
@module_loop
async def test_con_node_la_carrera_sale_como_gif_sin_abrir_el_navegador(
    renderer: SceneRenderer,
) -> None:
    media = await renderer.race(card_of(rain_chance=0.5), photo_result())
    assert not renderer.painter.disabled
    assert not renderer.disabled
    assert renderer._browser is None
    gif = Image.open(io.BytesIO(media.gif))
    assert gif.format == "GIF" and gif.size == (W, H) and gif.n_frames > GATE_FRAMES
    assert len(media.gif) < 8 * 1024 * 1024
    png = Image.open(io.BytesIO(media.png))
    assert png.format == "PNG" and png.size == (640, 360)
    assert len(png.convert("RGB").getcolors(1 << 20) or []) > 100


@needs_node
@module_loop
async def test_node_y_chromium_pintan_lo_mismo_la_carrera() -> None:
    """Mismo motor (Skia): solo cambia algún borde de letra, no colores ni posiciones."""
    if not LOCAL_CHROMIUM.exists():
        pytest.skip("No hay Chromium en esta máquina")
    card, result = card_of(rain_chance=0.5), photo_result()
    with_node = SceneRenderer(executable_path=NO_CHROMIUM)
    with_chromium = SceneRenderer(executable_path=str(LOCAL_CHROMIUM), node=NO_NODE)
    try:
        from_node = await with_node.race(card, result)
        from_browser = await with_chromium.race(card, result)
    finally:
        await with_node.close()
        await with_chromium.close()
    if with_chromium.disabled:
        pytest.skip("Chromium no arranca en esta máquina")
    gif_a = Image.open(io.BytesIO(from_node.gif))
    gif_b = Image.open(io.BytesIO(from_browser.gif))
    assert gif_a.n_frames == gif_b.n_frames

    def frames(gif: Image.Image) -> list[Image.Image]:
        out = []
        for n in range(gif.n_frames):
            gif.seek(n)
            out.append(gif.convert("RGB"))
        return out

    a, b = await asyncio.gather(asyncio.to_thread(frames, gif_a), asyncio.to_thread(frames, gif_b))
    for x, y in zip(a, b, strict=True):
        assert sum(ImageStat.Stat(ImageChops.difference(x, y)).mean) / 3 < 4
    llegada_a = Image.open(io.BytesIO(from_node.png)).convert("RGB")
    llegada_b = Image.open(io.BytesIO(from_browser.png)).convert("RGB")
    assert sum(ImageStat.Stat(ImageChops.difference(llegada_a, llegada_b)).mean) / 3 < 4


def test_el_pintor_de_las_carreras_es_un_nodescene() -> None:
    assert isinstance(SceneRenderer(node=NO_NODE).painter, NodeScene)
