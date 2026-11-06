"""Pruebas del camino de Node del bacará: la escena pintada sin navegador.

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

from bot.services import browser_scene
from bot.services.bacara import Coup, Result, Side
from bot.services.bacara_render import (
    H,
    Header,
    W,
    board_state,
    coup_states,
    meta_state,
)
from bot.services.bacara_scene import SCENE, BacaraScene
from bot.services.blackjack import Card
from bot.services.browser_scene import BrowserScene
from bot.services.node_scene import NodeScene

module_loop = pytest.mark.asyncio(loop_scope="module")
LOCAL_CHROMIUM = Path("/opt/pw-browsers/chromium")
NO_CHROMIUM = "/no/existe/chromium"


def c(rank: int, suit: int = 0) -> Card:
    return Card(rank, suit)


#: Un natural (rey y 8 contra as y 7: los dos con 8).
NATURAL = Coup((c(13, 1), c(8, 0)), (c(1, 2), c(7, 3)), number=3, left=380)
#: Las dos terceras cartas (5-10 del Jugador piden; 3-2 de la Banca pide).
THIRDS = Coup((c(5, 0), c(10, 1), c(12, 2)), (c(3, 3), c(2, 0), c(9, 1)), number=5, left=360)
#: La primera mano del zapato: lleva el cartel de ZAPATO NUEVO.
FIRST = Coup((c(11, 3), c(7, 2)), (c(10, 0), c(1, 1)), number=1, left=410)

HEADER = Header(shoe=2, hand=5, left=360)
BEFORE = [
    Result(winner, 5, 4, natural=k % 7 == 0)
    for k, winner in enumerate([Side.PLAYER, Side.BANKER, Side.BANKER, Side.TIE, Side.PLAYER] * 5)
]


def test_el_bacara_deja_que_el_pintor_ponga_su_exportframe() -> None:
    html = SCENE.read_text(encoding="utf-8")
    assert "window.exportFrame ??=" in html
    assert "toDataURL" not in html.split("window.exportFrame ??=")[0]


async def test_sin_node_ni_chromium_el_bacara_cae_a_pillow() -> None:
    scene = BacaraScene(executable_path=NO_CHROMIUM, node="/no/existe/node")
    png = await scene.board(BEFORE, HEADER, THIRDS)
    assert scene.painter.disabled and scene.browser.disabled and scene.disabled
    assert Image.open(io.BytesIO(png)).size == (W, H)
    media = await scene.coup(THIRDS, BEFORE, HEADER)
    assert media.gif is None
    assert Image.open(io.BytesIO(media.png)).size == (W, H)
    await scene.close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scene() -> BacaraScene:
    """Una escena del bacará con Node (y sin Chromium), compartida por el módulo."""
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    escena = BacaraScene(executable_path=NO_CHROMIUM)
    yield escena
    await escena.close()


def colors(png: bytes) -> int:
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG" and image.size == (W, H)
    return len(image.convert("RGB").getcolors(1 << 20) or [])


@needs_node
@module_loop
async def test_con_node_la_mesa_vacia_y_con_mano_son_un_png_de_la_medida(
    scene: BacaraScene,
) -> None:
    empty = await scene.board([], Header(shoe=1))
    assert not scene.painter.disabled
    assert colors(empty) > 100
    for coup in (NATURAL, THIRDS):
        assert colors(await scene.board(BEFORE, HEADER, coup)) > 100


@pytest.mark.lenta
@needs_node
@module_loop
async def test_con_node_la_mano_sale_con_su_gif_sin_usar_el_navegador(
    scene: BacaraScene,
) -> None:
    for coup, burned in ((NATURAL, 0), (THIRDS, 0), (FIRST, 7)):
        media = await scene.coup(coup, BEFORE, HEADER, burned=burned, seed=4)
        gif = Image.open(io.BytesIO(media.gif or b""))
        assert gif.format == "GIF" and gif.size == (W, H) and gif.n_frames > 30
        assert Image.open(io.BytesIO(media.png)).size == (W, H)
        assert media.seconds > 2
    assert not scene.painter.disabled
    # Si se hubiera probado Chromium (que no existe), estaría desactivado.
    assert not scene.browser.disabled


@needs_node
@module_loop
async def test_con_node_el_apreton_enseña_la_cara_sin_cambiar_el_dorso_quieto(
    scene: BacaraScene,
) -> None:
    """Con `peel` 0 la carta es el dorso entero; al despegarse cambia solo su sitio."""
    states = [
        s
        for s in coup_states(THIRDS, BEFORE, HEADER, seed=1)
        if any(k["peel"] is not None for k in s["cards"])
    ]

    def peel(state: dict) -> float:
        return max(k["peel"] or 0 for k in state["cards"])

    flat = next(s for s in states if peel(s) == 0)
    peeled = max(states, key=peel)
    assert peel(peeled) > 0.5
    patches = await scene._frames(meta_state(), [{**flat, "full": True}, {**peeled, "full": True}])
    assert patches is not None
    a, b = browser_scene.assemble(patches, (W, H))
    box = ImageChops.difference(a, b).getbbox()
    assert box is not None
    # El cambio queda dentro de la mitad de abajo (las terceras van tumbadas).
    assert box[1] > 150


@needs_node
@module_loop
async def test_node_y_chromium_pintan_lo_mismo_el_bacara() -> None:
    """Mismo motor (Skia): solo cambia algún borde de letra, no colores ni posiciones."""
    if not LOCAL_CHROMIUM.exists():
        pytest.skip("No hay Chromium en esta máquina")
    painter = NodeScene(SCENE, name="El pintor del bacará")
    browser = BrowserScene(
        SCENE, name="chromium", ready="loadFonts()", executable_path=str(LOCAL_CHROMIUM)
    )
    after = [*BEFORE, Result.of(THIRDS)]
    states = [
        board_state([], Header(shoe=1)),
        *coup_states(THIRDS, BEFORE, HEADER, seed=6),
        board_state(after, HEADER, THIRDS),
        *coup_states(FIRST, BEFORE, Header(shoe=2, hand=1, left=410), burned=7, seed=2),
    ]
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
