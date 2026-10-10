"""Pruebas de bot.services.bus_scene: el Autobús dibujado con Chromium y su plan B con Pillow.

Las que necesitan Chromium se saltan si no hay navegador en la máquina; las del
plan B con Pillow no lo necesitan (apuntan a un ejecutable que no existe).
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from PIL import Image, ImageChops, ImageSequence, ImageStat

from bot.services.blackjack import Card
from bot.services.browser_scene import png_bytes
from bot.services.bus_render import Banner, Board, H, Reveal, W, meta_state, reveal_states
from bot.services.bus_scene import SCENE, BusScene, assemble

#: Las pruebas con navegador comparten un bucle de eventos: Playwright está atado al
#: bucle que lo arrancó, así que el `browser` se abre una vez y se comparte.
module_loop = pytest.mark.asyncio(loop_scope="module")
#: Chromium del entorno de desarrollo, si lo hay (en Docker lo instala Playwright).
LOCAL_CHROMIUM = Path("/opt/pw-browsers/chromium")

#: Las cinco cartas de la partida: rey de corazones, 5 de picas, 9 de diamantes, as de
#: tréboles y reina de corazones (hay rojas y negras, figuras y números).
CARDS = (Card(13, 1), Card(5, 0), Card(9, 2), Card(1, 3), Card(12, 1))
THREE = Board(cards=CARDS[:3], results=(True, True, True), active=None)


def test_la_escena_existe_y_trae_sus_funciones() -> None:
    html = SCENE.read_text(encoding="utf-8")
    for function in ("setup", "renderFrames", "loadFonts"):
        assert function in html


async def test_sin_navegador_dibuja_con_pillow_y_se_queda_desactivado() -> None:
    scene = BusScene(executable_path="/no/existe/chromium")
    assert not scene.disabled

    png = await scene.board(Board(active=0))
    assert scene.disabled
    assert Image.open(io.BytesIO(png)).size == (W, H)

    reveal = await scene.reveal(CARDS, 3, seed=3)
    assert isinstance(reveal, Reveal)
    for media in (reveal.win, reveal.lose):
        assert media.gif is None and media.seconds == 0
        assert Image.open(io.BytesIO(media.png)).size == (W, H)
    # Acertar y fallar dejan mesas finales distintas.
    win = Image.open(io.BytesIO(reveal.win.png)).convert("RGB")
    lose = Image.open(io.BytesIO(reveal.lose.png)).convert("RGB")
    assert ImageChops.difference(win, lose).getbbox() is not None
    await scene.close()


async def test_el_plan_b_dibuja_la_mesa_al_cobrar_con_la_carta_fantasma() -> None:
    scene = BusScene(executable_path="/no/existe/chromium")
    cash = Board(
        cards=CARDS[:3],
        results=(True, True, True),
        active=None,
        ghost=CARDS[3],
        banner=Banner("cash", "¡COBRADO!", "×12,87"),
    )
    with_ghost = Image.open(io.BytesIO(await scene.board(cash))).convert("RGB")
    without = Image.open(io.BytesIO(await scene.board(THREE))).convert("RGB")
    assert with_ghost.size == (W, H)
    assert ImageChops.difference(with_ghost, without).getbbox() is not None
    await scene.close()


async def test_el_plan_b_usa_el_dibujo_de_reserva_que_se_le_da() -> None:
    class Fallback:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def board(self, board: object) -> bytes:
            self.calls.append("board")
            return b"PNG-DE-RESERVA"

        def reveal(self, cards: object, hand: int, *, seed: int) -> str:
            self.calls.append("reveal")
            return "REVEAL-DE-RESERVA"

    fallback = Fallback()
    scene = BusScene(fallback, executable_path="/no/existe/chromium")  # type: ignore[arg-type]
    assert await scene.board(Board()) == b"PNG-DE-RESERVA"
    assert await scene.reveal(CARDS, 1, seed=1) == "REVEAL-DE-RESERVA"
    assert fallback.calls == ["board", "reveal"]
    await scene.close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser() -> BusScene:
    """Un solo Chromium para las pruebas con navegador del módulo."""
    path = str(LOCAL_CHROMIUM) if LOCAL_CHROMIUM.exists() else None
    scene = BusScene(executable_path=path)
    await scene.board(Board())
    if scene.disabled:
        await scene.close()
        pytest.skip("No hay Chromium en esta máquina")
    yield scene
    await scene.close()


def png(data: bytes) -> Image.Image:
    picture = Image.open(io.BytesIO(data))
    assert picture.format == "PNG"
    return picture.convert("RGB")


def gif_frames(data: bytes | None) -> list[Image.Image]:
    assert data is not None
    gif = Image.open(io.BytesIO(data))
    return [frame.convert("RGB").copy() for frame in ImageSequence.Iterator(gif)]


def difference(a: Image.Image, b: Image.Image) -> float:
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


@module_loop
async def test_con_navegador_la_mesa_es_un_png_de_640_por_360(browser: BusScene) -> None:
    picture = png(await browser.board(Board(active=0)))
    assert not browser.disabled
    assert picture.size == (W, H)
    # No es una captura en blanco: hay muchos colores distintos.
    assert len(picture.getcolors(1 << 20) or []) > 100


@module_loop
async def test_con_navegador_la_carta_fantasma_y_el_cartel_cambian_la_mesa(
    browser: BusScene,
) -> None:
    ghost = Board(cards=CARDS[:3], results=(True, True, True), active=None, ghost=CARDS[3])
    banner = Board(
        cards=CARDS[:3],
        results=(True, True, True),
        active=None,
        banner=Banner("cash", "¡COBRADO!", "×12,87"),
    )
    plain = png(await browser.board(THREE))
    with_ghost = png(await browser.board(ghost))
    with_banner = png(await browser.board(banner))
    changed = ImageChops.difference(plain, with_ghost).getbbox()
    assert changed is not None and changed[1] < 200  # el fantasma está en un hueco de carta
    sign = ImageChops.difference(plain, with_banner).getbbox()
    assert sign is not None and sign[1] > 250  # el cartel, abajo del todo


@module_loop
async def test_con_navegador_cada_palo_y_el_dorso_se_dibujan_distintos(browser: BusScene) -> None:
    """Los cuatro palos (y el dorso) no se confunden: cada mesa es distinta de las demás."""
    pictures = [
        png(await browser.board(Board(cards=(Card(7, suit),), results=(True,), active=1)))
        for suit in range(4)
    ]
    pictures.append(png(await browser.board(Board(active=0))))
    for index, first in enumerate(pictures):
        for second in pictures[index + 1 :]:
            assert ImageChops.difference(first, second).getbbox() is not None


@module_loop
@pytest.mark.parametrize("hand", [1, 3, 5])
async def test_con_navegador_reveal_da_dos_gif_cuyo_final_es_su_png(
    browser: BusScene, hand: int
) -> None:
    reveal = await browser.reveal(CARDS, hand, seed=4)
    assert not browser.disabled
    common, _, _ = reveal_states(CARDS, hand, seed=4)
    finals = []
    for media in (reveal.win, reveal.lose):
        frames = gif_frames(media.gif)
        assert frames[0].size == (W, H)
        assert 10 < len(frames) <= 70
        assert media.seconds > 0.5
        last = png(media.png)
        assert last.size == (W, H)
        # La paleta del GIF no es exacta: se compara por parecido, no píxel a píxel.
        assert difference(frames[-1], last) < 12
        finals.append(last)
    assert ImageChops.difference(*finals).getbbox() is not None
    # El tramo común es el mismo gane o pierda.
    win_frames, lose_frames = gif_frames(reveal.win.gif), gif_frames(reveal.lose.gif)
    for first, second in zip(win_frames[: len(common)], lose_frames[: len(common)], strict=True):
        assert ImageChops.difference(first, second).getbbox() is None


@module_loop
async def test_con_navegador_la_tension_dura_mas_cuanto_mas_alta_es_la_mano(
    browser: BusScene,
) -> None:
    first = await browser.reveal(CARDS, 1, seed=2)
    fifth = await browser.reveal(CARDS, 5, seed=2)
    assert fifth.win.seconds > first.win.seconds + 1
    assert fifth.lose.seconds > first.lose.seconds + 1


async def full_frames(browser: BusScene, states: list[dict[str, Any]]) -> list[Image.Image]:
    """Cada fotograma pintado entero: todos los estados con `full`."""

    async def work(page: Any) -> list[dict[str, Any]]:
        await page.evaluate("m => setup(m)", meta_state())
        return await page.evaluate(
            "([s, first]) => renderFrames(s, first)",
            [[{**state, "full": True} for state in states], 0],
        )

    patches = await browser.browser.run(work)
    assert patches is not None
    return assemble(patches)


@module_loop
async def test_con_navegador_montar_recuadros_da_lo_mismo_que_pintar_entero(
    browser: BusScene,
) -> None:
    common, win, lose = reveal_states(CARDS, 4, seed=9)
    states = common + win + lose
    patches = await browser.browser.render(meta_state(), states)
    assert patches is not None
    pasted = assemble(patches)
    whole = await full_frames(browser, states)
    assert len(pasted) == len(whole) == len(states)
    for index, (a, b) in enumerate(zip(pasted, whole, strict=True)):
        assert ImageChops.difference(a, b).getbbox() is None, f"fotograma {index}"


@module_loop
async def test_con_navegador_dos_pestanas_dan_los_mismos_fotogramas_que_una(
    browser: BusScene,
) -> None:
    common, win, lose = reveal_states(CARDS, 2, seed=1)
    states = common + win + lose
    two = await browser.browser.render(meta_state(), states, tabs=2)
    one = await browser.browser.render(meta_state(), states, tabs=1)
    assert two is not None and one is not None
    for a, b in zip(assemble(two), assemble(one), strict=True):
        assert ImageChops.difference(a, b).getbbox() is None


@module_loop
async def test_con_navegador_un_estado_full_devuelve_el_fotograma_entero(
    browser: BusScene,
) -> None:
    common, win, lose = reveal_states(CARDS, 3, seed=5)
    assert lose[0].get("full") is True  # la cola lose empieza entera
    patches = await browser.browser.render(meta_state(), common + win + lose, tabs=1)
    assert patches is not None
    start = len(common) + len(win)
    assert (patches[start]["x"], patches[start]["y"]) == (0, 0)
    assert Image.open(io.BytesIO(png_bytes(patches[start]))).size == (W, H)
    # Y los fotogramas normales de la cola solo traen el recuadro que cambia.
    assert Image.open(io.BytesIO(png_bytes(patches[start + 1]))).size != (W, H)
