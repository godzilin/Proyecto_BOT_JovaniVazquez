"""Pruebas de la escena de la ruleta con Chromium de verdad (`assets/ruleta/escena.html`).

Se saltan si no hay navegador en la máquina. Lo que no necesita navegador (los
estados que Python calcula) está en `test_roulette_service.py`.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from PIL import Image, ImageChops

from bot.services.roulette import POCKETS, Wager, Wheel, parse_bet, play_round
from bot.services.roulette_scene import (
    H,
    RouletteScene,
    W,
    assemble,
    meta_state,
    spin_states,
)

#: Las pruebas con navegador comparten un bucle de eventos: Playwright está atado al
#: bucle que lo arrancó, así que el `browser` se abre una vez y se comparte.
module_loop = pytest.mark.asyncio(loop_scope="module")
#: Chromium del entorno de desarrollo, si lo hay (en Docker lo instala Playwright).
LOCAL_CHROMIUM = Path("/opt/pw-browsers/chromium")
#: Sin Node la escena cae a Chromium, que es lo que prueba este módulo.
NO_NODE = "/no/existe/node"

HISTORY = [17, 0, 37, 5, 22, 8, 31, 14, 2, 20, 9, 11, 25]


class FixedWheel(Wheel):
    """Una rueda que saca siempre la misma casilla y los mismos rayos."""

    def __init__(self, pocket: int, lucky: dict[int, int] | None = None) -> None:
        super().__init__(lambda n: POCKETS.index(pocket), lightning=False)
        self._lucky = dict(lucky or {})

    def strike(self) -> dict[int, int]:
        return dict(self._lucky)


def outcome(pocket: int, bets: list[tuple[str, int]], lucky: dict[int, int] | None = None):
    wagers = [Wager(parse_bet(text), stake) for text, stake in bets]
    return play_round(FixedWheel(pocket, lucky), wagers)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser() -> RouletteScene:
    """Un solo Chromium para las pruebas con navegador del módulo."""
    path = str(LOCAL_CHROMIUM) if LOCAL_CHROMIUM.exists() else None
    scene = RouletteScene(executable_path=path, node=NO_NODE)
    await scene.board(HISTORY)
    if scene.disabled:
        await scene.close()
        pytest.skip("No hay Chromium en esta máquina")
    yield scene
    await scene.close()


def png(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    assert image.format == "PNG"
    return image.convert("RGB")


def from_url(url: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("RGB")


@module_loop
async def test_la_mesa_es_un_png_de_640_por_360(browser: RouletteScene) -> None:
    wagers = [Wager(parse_bet("17"), 100), Wager(parse_bet("rojo"), 500)]
    image = png(await browser.board(HISTORY, wagers))
    assert not browser.disabled
    assert image.size == (W, H)
    # No es una captura en blanco: hay muchos colores distintos.
    assert len(image.getcolors(1 << 20) or []) > 500


@module_loop
async def test_la_mesa_sin_historial_ni_apuestas_tambien_se_dibuja(browser: RouletteScene) -> None:
    assert png(await browser.board([])).size == (W, H)


@module_loop
async def test_la_tirada_es_un_gif_con_un_fotograma_por_estado(browser: RouletteScene) -> None:
    result = outcome(17, [("17", 100), ("rojo", 50)], {17: 200, 5: 50})
    states = spin_states(result, history=HISTORY, seed=3)
    media = await browser.spin(result, history=HISTORY, seed=3)
    assert not browser.disabled
    gif = Image.open(io.BytesIO(media.gif))
    assert gif.format == "GIF" and gif.size == (W, H)
    assert gif.n_frames == len(states)
    assert media.seconds > 2
    last = png(media.png)
    assert last.size == (W, H)
    # La paleta del GIF no es exacta: se compara con un margen.
    gif.seek(gif.n_frames - 1)
    diff = ImageChops.difference(gif.convert("RGB"), last).convert("L")
    assert sum(diff.histogram()[40:]) < W * H * 0.02


@module_loop
async def test_cada_cartel_se_pinta_distinto(browser: RouletteScene) -> None:
    """Un cartel por estilo (premio, pérdida, casi, rayo): cada uno cambia la imagen."""
    cases = {
        "lucky": outcome(17, [("17", 100)], {17: 200}),
        "big": outcome(17, [("17", 100)]),
        "win": outcome(1, [("rojo", 100)]),
        "lose": outcome(2, [("rojo", 100)]),
        "near": outcome(18, [("19", 100)]),
        "ldw": outcome(1, [("rojo", 100), ("negro", 100), ("par", 100)]),
    }
    finals: dict[str, Image.Image] = {}
    for kind, result in cases.items():
        states = spin_states(result, history=HISTORY, seed=5)
        patches = await browser._frames(states[-1:], 5)
        assert patches is not None
        finals[kind] = assemble(patches)[0]
        assert finals[kind].size == (W, H)
    kinds = list(finals)
    for index, first in enumerate(kinds):
        for second in kinds[index + 1 :]:
            assert ImageChops.difference(finals[first], finals[second]).getbbox() is not None


async def full_render(browser: RouletteScene, state: dict[str, Any], seed: int) -> Image.Image:
    """El fotograma pintado entero: setup, un solo estado y `full`."""

    async def work(page: Any) -> list[dict[str, Any]]:
        await page.evaluate("m => setup(m)", {**meta_state(), "seed": seed})
        return await page.evaluate(
            "([s, first]) => renderFrames(s, first)", [[{**state, "full": True}], 0]
        )

    patches = await browser.browser.run(work)
    assert patches is not None and (patches[0]["x"], patches[0]["y"]) == (0, 0)
    image = from_url(patches[0]["u"])
    assert image.size == (W, H)
    return image


@module_loop
@pytest.mark.parametrize("lucky", [{}, {17: 200, 5: 50, 30: 500}], ids=["sin_rayos", "con_rayos"])
async def test_montar_los_recuadros_da_el_mismo_fotograma_que_pintarlo_entero(
    browser: RouletteScene, lucky: dict[int, int]
) -> None:
    result = outcome(17, [("17", 100), ("rojo", 50), ("par", 20)], lucky)
    states = spin_states(result, history=HISTORY, seed=9)
    patches = await browser._frames(states, 9)
    assert patches is not None and len(patches) == len(states)
    frames = assemble(patches)
    # Los fotogramas con rayos, con el cartel, con el marcador cambiado y el último.
    checks = {len(states) - 1, len(states) - 4, 8, 20, 33, 55}
    for index in sorted(checks):
        whole = await full_render(browser, states[index], 9)
        assert ImageChops.difference(frames[index], whole).getbbox() is None, index


@module_loop
async def test_los_recuadros_son_mas_pequenos_que_el_fotograma_entero(
    browser: RouletteScene,
) -> None:
    result = outcome(17, [("17", 100)])
    states = spin_states(result, history=HISTORY, seed=2)
    patches = await browser._frames(states, 2)
    assert patches is not None
    # El primero va entero y la mayoría de los siguientes son un recuadro.
    first = from_url(patches[0]["u"])
    assert first.size == (W, H)
    small = [p for p in patches[1:] if from_url(p["u"]).size != (W, H)]
    assert len(small) > len(patches) // 2
