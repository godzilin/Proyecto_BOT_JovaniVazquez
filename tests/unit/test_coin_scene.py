"""Pruebas de bot.services.coin_scene: la moneda dibujada con Chromium y su plan B con Pillow.

Las que necesitan Chromium se saltan si no hay navegador en la máquina; la de
la reserva con Pillow no lo necesita (apunta a un ejecutable que no existe).
Todas apuntan a un Node que no existe para probar Chromium y Pillow; el
camino de Node está en `test_node_scene.py`.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
import pytest_asyncio
from coin_fakes import Scripted
from PIL import Image, ImageChops, ImageStat

from bot.services.coin import CoinGame, Outcome, Side
from bot.services.coin_render import H, W
from bot.services.coin_scene import SCENE, CoinScene

#: Las pruebas con navegador comparten un bucle de eventos: Playwright está atado al
#: bucle que lo arrancó, así que el `browser` se abre una vez y se comparte.
module_loop = pytest.mark.asyncio(loop_scope="module")
#: Chromium del entorno de desarrollo, si lo hay (en Docker lo instala Playwright).
LOCAL_CHROMIUM = Path("/opt/pw-browsers/chromium")
#: Sin Node, la escena va a Chromium y luego a Pillow.
NO_NODE = "/no/existe/node"


def lost_game() -> CoinGame:
    """Un acierto y luego fallo (pidió cara, salió cruz)."""
    rng = Scripted(Outcome.CARA, Outcome.CRUZ, Outcome.CARA)
    game = CoinGame.new(100, rng)
    game.flip(Side.CARA, rng)
    game.flip(Side.CARA, rng)
    return game


def test_la_escena_existe_y_trae_sus_funciones() -> None:
    html = SCENE.read_text(encoding="utf-8")
    for function in ("setup", "renderFrames", "loadFonts"):
        assert function in html


async def test_sin_navegador_dibuja_con_pillow_y_se_queda_desactivado() -> None:
    scene = CoinScene(executable_path="/no/existe/chromium", node=NO_NODE)
    assert not scene.disabled
    game = lost_game()

    png = await scene.board(None, stake=100, face=Side.CARA)
    assert scene.disabled
    assert Image.open(io.BytesIO(png)).size == (W, H)

    media = await scene.toss(game, start=Side.CARA, seed=3)
    assert Image.open(io.BytesIO(media.gif)).format == "GIF"
    assert Image.open(io.BytesIO(media.png)).size == (W, H)
    await scene.close()


async def test_el_plan_b_usa_el_dibujo_de_reserva_que_se_le_da() -> None:
    class Fallback:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def board(self, game: object, *, stake: int, face: object) -> bytes:
            self.calls.append("board")
            return b"PNG-DE-RESERVA"

        def toss(self, game: object, *, start: object, seed: int) -> str:
            self.calls.append("toss")
            return "MEDIA-DE-RESERVA"

    fallback = Fallback()
    scene = CoinScene(fallback, executable_path="/no/existe/chromium", node=NO_NODE)  # type: ignore[arg-type]
    assert await scene.board(None, stake=1, face=Side.CARA) == b"PNG-DE-RESERVA"
    assert await scene.toss(lost_game(), start=Side.CARA, seed=1) == "MEDIA-DE-RESERVA"
    assert fallback.calls == ["board", "toss"]
    await scene.close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser() -> CoinScene:
    """Un solo Chromium para las pruebas con navegador del módulo."""
    path = str(LOCAL_CHROMIUM) if LOCAL_CHROMIUM.exists() else None
    scene = CoinScene(executable_path=path, node=NO_NODE)
    await scene.board(None, stake=100, face=Side.CARA)
    if scene.disabled:
        await scene.close()
        pytest.skip("No hay Chromium en esta máquina")
    yield scene
    await scene.close()


@module_loop
async def test_con_navegador_la_mesa_es_un_png_de_640_por_360(browser: CoinScene) -> None:
    png = await browser.board(None, stake=100, face=Side.CARA)
    assert not browser.disabled
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG" and image.size == (W, H)
    # No es una captura en blanco: hay colores distintos.
    assert len(image.convert("RGB").getcolors(1 << 20) or []) > 100


@module_loop
async def test_con_navegador_el_lanzamiento_es_un_gif_cuyo_final_es_el_png(
    browser: CoinScene,
) -> None:
    game = lost_game()
    media = await browser.toss(game, start=Side.CARA, seed=4)
    assert not browser.disabled
    gif = Image.open(io.BytesIO(media.gif))
    assert gif.format == "GIF" and gif.size == (W, H)
    assert gif.n_frames > 10
    assert media.seconds > 1
    gif.seek(gif.n_frames - 1)
    last = gif.convert("RGB")
    png = Image.open(io.BytesIO(media.png))
    assert png.format == "PNG" and png.size == (W, H)
    # La paleta del GIF no es exacta: se compara por parecido, no píxel a píxel.
    diff = sum(ImageStat.Stat(ImageChops.difference(last, png.convert("RGB"))).mean) / 3
    assert diff < 12


@module_loop
async def test_con_navegador_el_canto_tambien_se_dibuja(browser: CoinScene) -> None:
    rng = Scripted(Outcome.EDGE, Outcome.CARA)
    game = CoinGame.new(100, rng)
    game.flip(Side.CRUZ, rng)
    media = await browser.toss(game, start=Side.CARA, seed=9)
    assert not browser.disabled
    assert Image.open(io.BytesIO(media.png)).size == (W, H)
