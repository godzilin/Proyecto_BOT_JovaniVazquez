"""Pruebas de bot.services.node_scene: las escenas de canvas pintadas con Node, sin navegador.

Las que pintan se saltan si no hay `node` o falta `@napi-rs/canvas` (`npm ci` en
la raíz). La comparación con Chromium se salta también si no hay navegador.
"""

from __future__ import annotations

import asyncio
import io
import shutil
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio
from coin_fakes import Scripted
from PIL import Image, ImageChops, ImageStat

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene
from bot.services.coin import CoinGame, Outcome, Side
from bot.services.coin_render import H, W, meta_state, toss_states
from bot.services.coin_scene import SCENE, CoinScene
from bot.services.node_scene import RUNNER, NodeScene

module_loop = pytest.mark.asyncio(loop_scope="module")
LOCAL_CHROMIUM = Path("/opt/pw-browsers/chromium")


def node_ready() -> bool:
    """Si hay `node` y encuentra `@napi-rs/canvas` desde la carpeta del pintor."""
    node = shutil.which("node")
    if node is None:
        return False
    check = subprocess.run(
        [node, "-e", "require('@napi-rs/canvas')"],
        cwd=RUNNER.parent,
        capture_output=True,
        check=False,
    )
    return check.returncode == 0


needs_node = pytest.mark.skipif(not node_ready(), reason="No hay Node con @napi-rs/canvas")


def lost_game() -> CoinGame:
    """Dos aciertos y luego fallo (pidió cara, salió cruz)."""
    rng = Scripted(Outcome.CARA, Outcome.CARA, Outcome.CRUZ, Outcome.CARA)
    game = CoinGame.new(100, rng)
    for _ in range(3):
        game.flip(Side.CARA, rng)
    return game


def test_la_moneda_deja_que_el_pintor_ponga_su_exportframe() -> None:
    html = SCENE.read_text(encoding="utf-8")
    assert "window.exportFrame ??=" in html
    assert "toDataURL" not in html.split("window.exportFrame ??=")[0]


async def test_sin_node_se_desactiva_y_devuelve_none() -> None:
    scene = NodeScene(SCENE, name="prueba", node="/no/existe/node")
    assert await scene.render({}, [{}]) is None
    assert scene.disabled
    # Ya no lo reintenta.
    assert await scene.evaluate("1 + 1") is None
    await scene.close()


async def test_sin_node_la_moneda_pasa_a_chromium_y_luego_a_pillow() -> None:
    scene = CoinScene(executable_path="/no/existe/chromium", node="/no/existe/node")
    png = await scene.board(None, stake=100, face=Side.CARA)
    assert scene.painter.disabled and scene.browser.disabled and scene.disabled
    assert Image.open(io.BytesIO(png)).size == (W, H)
    await scene.close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def painter() -> NodeScene:
    """Un pintor de Node para la moneda, compartido por el módulo."""
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    scene = NodeScene(SCENE, name="El pintor de la moneda")
    yield scene
    await scene.close()


@needs_node
@module_loop
async def test_node_carga_la_escena_y_sus_fuentes(painter: NodeScene) -> None:
    assert await painter.evaluate("typeof renderFrames") == "function"
    # Con la fuente cargada, «CARA» mide distinto que con la de reserva.
    width = (
        "(() => { const c = document.createElement('canvas').getContext('2d');"
        " c.font = '30px Luckiest'; const a = c.measureText('CARA').width;"
        " c.font = '30px NoExiste'; return a !== c.measureText('CARA').width; })()"
    )
    assert await painter.evaluate(width) is True


@needs_node
@module_loop
async def test_node_pinta_el_lanzamiento_entero_con_el_primer_fotograma_completo(
    painter: NodeScene,
) -> None:
    game = lost_game()
    states = toss_states(game, start=Side.CARA, seed=5)
    patches = await painter.render(meta_state(game.stake), states)
    assert patches is not None and not painter.disabled
    assert len(patches) == len(states)
    first = patches[0]
    assert (first["x"], first["y"], first["image"].size) == (0, 0, (W, H))
    frames = browser_scene.assemble(patches, (W, H))
    assert all(frame.size == (W, H) for frame in frames)
    # No es un lienzo vacío.
    assert len(frames[-1].getcolors(1 << 20) or []) > 100


@needs_node
@module_loop
async def test_un_error_en_la_escena_desactiva_el_pintor() -> None:
    scene = NodeScene(SCENE, name="prueba")
    # Pinta sin `setup`: la escena no tiene META y lanza dentro de Node.
    assert await scene.render(None, [{"coin": {}}]) is None  # type: ignore[arg-type]
    assert scene.disabled
    await scene.close()


@needs_node
@module_loop
async def test_la_moneda_con_node_da_mesa_y_lanzamiento() -> None:
    scene = CoinScene(executable_path="/no/existe/chromium")
    png = await scene.board(None, stake=100, face=Side.CARA)
    assert Image.open(io.BytesIO(png)).size == (W, H)
    media = await scene.toss(lost_game(), start=Side.CARA, seed=4)
    assert not scene.painter.disabled
    # No ha hecho falta el navegador: si se hubiera probado, estaría desactivado.
    assert not scene.browser.disabled
    gif = Image.open(io.BytesIO(media.gif))
    assert gif.format == "GIF" and gif.n_frames > 10
    await scene.close()


@needs_node
@module_loop
async def test_node_y_chromium_pintan_lo_mismo(painter: NodeScene) -> None:
    """Mismo motor (Skia): solo cambia algún borde de letra, no colores ni posiciones."""
    if not LOCAL_CHROMIUM.exists():
        pytest.skip("No hay Chromium en esta máquina")
    browser = BrowserScene(
        SCENE, name="chromium", ready="loadFonts()", executable_path=str(LOCAL_CHROMIUM)
    )
    game = lost_game()
    meta, states = meta_state(game.stake), toss_states(game, start=Side.CARA, seed=6)
    try:
        from_browser = await browser.render(meta, states)
    finally:
        await browser.close()
    if from_browser is None:
        pytest.skip("Chromium no arranca en esta máquina")
    from_node = await painter.render(meta, states)
    assert from_node is not None
    a, b = await asyncio.gather(
        asyncio.to_thread(browser_scene.assemble, from_browser, (W, H)),
        asyncio.to_thread(browser_scene.assemble, from_node, (W, H)),
    )
    for x, y in zip(a, b, strict=True):
        assert sum(ImageStat.Stat(ImageChops.difference(x, y)).mean) / 3 < 4
