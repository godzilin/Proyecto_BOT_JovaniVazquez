"""Pruebas de bot.services.craps_scene: los dados con Chromium y su plan B con Pillow.

Siempre con `node=NO_NODE`, para no pasar por Node. Las que necesitan Chromium
se saltan si no hay navegador en la máquina; la de la reserva con Pillow no lo
necesita (apunta a ejecutables que no existen).
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
import pytest_asyncio
from craps_fakes import game_after, table_for
from PIL import Image, ImageChops, ImageStat

from bot.services.craps import CrapsGame
from bot.services.craps_render import OPENING_REST, H, W
from bot.services.craps_scene import SCENE, CrapsScene, assemble

#: Las pruebas con navegador comparten un bucle de eventos: Playwright está atado al
#: bucle que lo arrancó, así que el `browser` se abre una vez y se comparte.
module_loop = pytest.mark.asyncio(loop_scope="module")
#: Chromium del entorno de desarrollo, si lo hay (en Docker lo instala Playwright).
LOCAL_CHROMIUM = Path("/opt/pw-browsers/chromium")
#: Un Node que no existe: fuerza el camino de Chromium o de Pillow.
NO_NODE = "/no/existe/node"


def point_game() -> CrapsGame:
    """Una salida que pone el punto en 6."""
    return game_after((1, 5))


def test_la_escena_existe_y_trae_sus_funciones() -> None:
    html = SCENE.read_text(encoding="utf-8")
    for function in ("setup", "renderFrames", "loadFonts"):
        assert function in html


async def test_sin_navegador_dibuja_con_pillow_y_se_queda_desactivado() -> None:
    scene = CrapsScene(executable_path="/no/existe/chromium", node=NO_NODE)
    assert not scene.disabled

    png = await scene.board(table_for(None), OPENING_REST)
    assert scene.disabled
    assert Image.open(io.BytesIO(png)).size == (W, H)

    media = await scene.throw(table_for(point_game()), seed=3)
    assert Image.open(io.BytesIO(media.gif)).format == "GIF"
    assert Image.open(io.BytesIO(media.png)).size == (W, H)
    await scene.close()


async def test_el_plan_b_usa_el_dibujo_de_reserva_que_se_le_da() -> None:
    class Fallback:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def board(self, table: object, rest: object) -> bytes:
            self.calls.append("board")
            return b"PNG-DE-RESERVA"

        def throw(self, table: object, *, seed: int) -> str:
            self.calls.append("throw")
            return "MEDIA-DE-RESERVA"

    fallback = Fallback()
    scene = CrapsScene(fallback, executable_path="/no/existe/chromium", node=NO_NODE)  # type: ignore[arg-type]
    assert await scene.board(table_for(None), OPENING_REST) == b"PNG-DE-RESERVA"
    assert await scene.throw(table_for(point_game()), seed=1) == "MEDIA-DE-RESERVA"
    assert fallback.calls == ["board", "throw"]
    await scene.close()


# -- assemble ---------------------------------------------------------------------------


def data_url(image: Image.Image) -> str:
    out = io.BytesIO()
    image.save(out, format="PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()


def test_assemble_pega_cada_recuadro_sobre_el_fotograma_anterior() -> None:
    red = Image.new("RGB", (W, H), (255, 0, 0))
    blue = Image.new("RGB", (10, 6), (0, 0, 255))
    green = Image.new("RGB", (4, 4), (0, 255, 0))
    frames = assemble(
        [
            {"u": data_url(red)},
            {"u": data_url(blue), "x": 5, "y": 7},
            {"u": data_url(green), "x": 100, "y": 200},
        ]
    )
    assert len(frames) == 3 and all(f.size == (W, H) for f in frames)
    assert frames[0].getpixel((5, 7)) == (255, 0, 0)  # el primero no se toca
    assert frames[1].getpixel((5, 7)) == (0, 0, 255)
    assert frames[1].getpixel((14, 12)) == (0, 0, 255)
    assert frames[1].getpixel((15, 13)) == (255, 0, 0)
    # El tercero conserva el azul del segundo y añade su recuadro verde.
    assert frames[2].getpixel((5, 7)) == (0, 0, 255)
    assert frames[2].getpixel((101, 201)) == (0, 255, 0)
    assert frames[1].getpixel((101, 201)) == (255, 0, 0)


def test_assemble_acepta_un_fotograma_entero_en_mitad() -> None:
    red = Image.new("RGB", (W, H), (255, 0, 0))
    white = Image.new("RGB", (W, H), (255, 255, 255))
    frames = assemble([{"u": data_url(red)}, {"u": data_url(white)}])
    assert frames[1].getpixel((0, 0)) == (255, 255, 255)


def test_assemble_falla_si_el_primer_fotograma_no_viene_entero() -> None:
    patch = Image.new("RGB", (10, 10), (0, 0, 255))
    with pytest.raises(ValueError, match="primer fotograma"):
        assemble([{"u": data_url(patch), "x": 0, "y": 0}])


# -- Con Chromium -----------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser() -> CrapsScene:
    """Un solo Chromium para las pruebas con navegador del módulo."""
    path = str(LOCAL_CHROMIUM) if LOCAL_CHROMIUM.exists() else None
    scene = CrapsScene(executable_path=path, node=NO_NODE)
    await scene.board(table_for(None), OPENING_REST)
    if scene.disabled:
        await scene.close()
        pytest.skip("No hay Chromium en esta máquina")
    yield scene
    await scene.close()


@module_loop
async def test_con_navegador_la_mesa_es_un_png_de_640_por_360(browser: CrapsScene) -> None:
    png = await browser.board(table_for(None), OPENING_REST)
    assert not browser.disabled
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG" and image.size == (W, H)
    # No es una captura en blanco: hay colores distintos.
    assert len(image.convert("RGB").getcolors(1 << 20) or []) > 100


@module_loop
async def test_con_navegador_la_tirada_es_un_gif_cuyo_final_es_el_png(
    browser: CrapsScene,
) -> None:
    media = await browser.throw(table_for(point_game()), seed=4)
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
async def test_con_navegador_una_partida_ganada_con_odds_tambien_se_dibuja(
    browser: CrapsScene,
) -> None:
    game = game_after((2, 2), (2, 2), odds=100)
    assert game.odds == 100 and not game.playing
    media = await browser.throw(table_for(game), seed=9)
    assert not browser.disabled
    assert Image.open(io.BytesIO(media.png)).size == (W, H)
