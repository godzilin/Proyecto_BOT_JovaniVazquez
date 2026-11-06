"""Pruebas de bot.services.allatu_scene: lo que se ve en cada jugada y cómo se pinta.

Las de la línea de tiempo no pintan nada. Las de Node se saltan si no hay `node`
o falta `@napi-rs/canvas` (`npm ci` en la raíz); la comparación con Chromium,
también si no hay navegador.
"""

from __future__ import annotations

import io
import random
from copy import deepcopy
from fractions import Fraction
from pathlib import Path

import pytest
import pytest_asyncio
from PIL import Image, ImageChops, ImageStat
from test_node_scene import needs_node, node_ready, open_helpers

from bot.services import browser_scene
from bot.services.allatu import BOXES, ROUNDS, AllatuGame, Phase, Table
from bot.services.allatu_scene import (
    CONDOLENCE_TALK,
    FINAL_FRAME_MS,
    SCENE,
    AllatuScene,
    H,
    W,
    board_state,
    call_states,
    deal_states,
    final_states,
    haggle_states,
    meta_state,
    open_states,
    opening_states,
    rest_state,
)
from bot.services.browser_scene import BrowserScene
from bot.services.node_scene import NodeScene

module_loop = pytest.mark.asyncio(loop_scope="module")
LOCAL_CHROMIUM = Path("/opt/pw-browsers/chromium")
NO_CHROMIUM = "/no/existe/chromium"


def game_in(phase: Phase, *, seed: int = 4, luck: bool = True) -> AllatuGame:
    """Una partida de la tabla Infierno con el gordo de ×1.000, llevada hasta `phase`."""
    game = AllatuGame.new(1_000, Table.INFIERNO, random.Random(seed))
    values = [*Table.INFIERNO.base, Fraction(1_000)]
    game.values = tuple(values)
    game.gordo = 1_000
    game.haggle_luck = (luck,) * len(ROUNDS)
    if phase is Phase.PICK:
        return game
    game.choose(0)
    while game.phase is not phase:
        if game.phase is Phase.OPEN:
            game.open(game.openable[0])
        elif game.phase is Phase.OFFER:
            game.no_deal()
        else:  # pragma: no cover
            raise AssertionError(phase)
    return game


def test_abrir_una_caja_se_ve_igual_en_cualquier_momento_de_la_partida() -> None:
    """Por eso se pinta una vez por caja antes del primer clic."""
    early = game_in(Phase.PICK)
    late = game_in(Phase.OFFER)
    assert open_states(early, 21) == open_states(late, 21)


def test_cada_animacion_acaba_quieta_en_lo_que_queda_despues() -> None:
    offer = game_in(Phase.OFFER)
    final = game_in(Phase.FINAL)
    for states in (
        opening_states(game_in(Phase.PICK)),
        open_states(offer, 21),
        call_states(offer),
        deal_states(offer),
        haggle_states(offer),
        haggle_states(game_in(Phase.OFFER, luck=False)),
        final_states(final, swap=True),
        final_states(final, swap=False),
    ):
        assert states[-1]["ms"] == FINAL_FRAME_MS
        assert all(s["ms"] < FINAL_FRAME_MS for s in states[:-1])
    after = deepcopy(final)
    after.finish(swap=True)
    assert final_states(final, swap=True)[-1]["board"] == board_state(after)
    assert call_states(offer)[-1] == {**rest_state(offer), "t": call_states(offer)[-1]["t"]}


def test_el_plato_tacha_lo_que_ha_salido_y_marca_tu_caja_al_final() -> None:
    game = game_in(Phase.FINAL)
    board = board_state(game)
    assert sum(1 for row in board["ladder"] if row.get("out")) == len(game.opened)
    assert board["own"] == {"n": 1}
    game.finish(swap=False)
    done = board_state(game)
    assert done["own"]["v"] and sum(1 for row in done["ladder"] if row.get("mine")) == 1
    assert sum(1 for box in done["boxes"] if box["s"] == "open") == BOXES - 1
    assert done["result"]["kind"] in ("good", "bad")


def test_la_ruleta_para_en_el_gordo_sorteado() -> None:
    game = game_in(Phase.PICK)
    wheel = [s for s in opening_states(game) if s["view"] == "wheel"]
    tiers = wheel[-1]["tiers"]
    assert tiers[round(wheel[-1]["pos"]) % len(tiers)]["v"] == "×1.000"


async def test_sin_node_ni_chromium_no_hay_imagen() -> None:
    scene = AllatuScene(executable_path=NO_CHROMIUM, node="/no/existe/node")
    assert await scene.still(game_in(Phase.PICK)) is None
    assert await scene.open_box(game_in(Phase.PICK), 3) is None
    assert scene.disabled
    await scene.close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scene() -> AllatuScene:
    """Un plató con Node (y sin Chromium), compartido por el módulo."""
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    escena = AllatuScene(executable_path=NO_CHROMIUM)
    yield escena
    await escena.close()


@needs_node
@module_loop
async def test_con_node_cada_jugada_es_un_gif_que_acaba_en_su_png(scene: AllatuScene) -> None:
    offer = game_in(Phase.OFFER)
    final = game_in(Phase.FINAL)
    for media in (
        await scene.opening(game_in(Phase.PICK)),
        await scene.open_box(offer, 21),
        await scene.call(offer),
        await scene.deal(offer),
        await scene.haggle(offer),
        await scene.final(final, swap=True),
    ):
        assert media is not None and media.seconds > 0.8
        gif = Image.open(io.BytesIO(media.gif))
        assert gif.format == "GIF" and gif.size == (W, H) and gif.n_frames > 10
        gif.seek(gif.n_frames - 1)
        last = gif.convert("RGB")
        png = Image.open(io.BytesIO(media.png)).convert("RGB")
        # El último fotograma del GIF es el PNG que queda (salvo la paleta del GIF).
        assert sum(ImageStat.Stat(ImageChops.difference(last, png)).mean) / 3 < 4
    assert not scene.painter.disabled


@needs_node
@module_loop
async def test_con_node_las_22_cajas_elegidas_son_22_imagenes_distintas(
    scene: AllatuScene,
) -> None:
    pngs = await scene.picks(game_in(Phase.PICK))
    assert pngs is not None and len(pngs) == BOXES
    assert len(set(pngs)) == BOXES
    assert Image.open(io.BytesIO(pngs[0])).size == (W, H)


@needs_node
@module_loop
async def test_repartida_pinta_lo_mismo_que_de_un_tiron(scene: AllatuScene) -> None:
    game = game_in(Phase.OFFER)
    states = deal_states(game)
    meta = meta_state(game)
    painter = scene.painter
    calls: list[str] = []
    original = painter._request

    def spy(call: str, args: list | None = None, **extra: object) -> dict:
        calls.append(call)
        return original(call, args, **extra)

    await open_helpers(painter, meta, states)
    painter._request = spy  # type: ignore[method-assign]
    try:
        split = await painter.render(meta, states)
        setups = calls.count("setup")
        whole = await painter.render(meta, states, procs=1)
    finally:
        painter._request = original  # type: ignore[method-assign]
    assert split is not None and whole is not None
    assert setups > 1  # de verdad se repartió
    a = browser_scene.assemble(split, (W, H))
    b = browser_scene.assemble(whole, (W, H))
    assert [x.tobytes() for x in a] == [y.tobytes() for y in b]


@needs_node
@module_loop
async def test_node_y_chromium_pintan_lo_mismo_el_plato() -> None:
    """Mismo motor (Skia): solo cambia algún borde de letra, no colores ni posiciones."""
    if not LOCAL_CHROMIUM.exists():
        pytest.skip("No hay Chromium en esta máquina")
    painter = NodeScene(SCENE, name="El pintor de Allá tú")
    browser = BrowserScene(
        SCENE, name="chromium", ready="loadFonts()", executable_path=str(LOCAL_CHROMIUM)
    )
    game = game_in(Phase.OFFER)
    states = call_states(game)[::4]
    try:
        node = await painter.render(meta_state(game), states)
        chrome = await browser.render(meta_state(game), states)
    finally:
        await painter.close()
        await browser.close()
    assert node is not None and chrome is not None
    for a, b in zip(
        browser_scene.assemble(node, (W, H)), browser_scene.assemble(chrome, (W, H)), strict=True
    ):
        diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
        assert sum(ImageStat.Stat(diff).mean) / 3 < 4


def test_el_pesame_tiene_su_cartel_y_su_frase() -> None:
    game = game_in(Phase.PICK)
    game.values = (Fraction(0), Fraction(1_000), *[Fraction(1)] * 20)
    game.choose(0)
    game.open(1)
    last = call_states(game)[-1]
    assert last["label"] == "LLAMADA DE PÉSAME"
    assert last["talk"] in CONDOLENCE_TALK
    assert rest_state(game)["label"] == "LLAMADA DE PÉSAME"
