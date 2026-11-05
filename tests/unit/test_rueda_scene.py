"""Pruebas de la escena de la Rueda (`assets/rueda/escena.html`) y de `bot.services.rueda_scene`.

Las que pintan con Node se saltan si no hay `node` o falta `@napi-rs/canvas`
(`npm ci` en la raíz); la comparación con Chromium, si no hay navegador. La
reserva con Pillow no necesita ninguno de los dos (apunta a ejecutables que no
existen).
"""

from __future__ import annotations

import asyncio
import io
import random
import time
from pathlib import Path

import pytest
import pytest_asyncio
from PIL import Image, ImageChops, ImageStat
from test_node_scene import needs_node, node_ready, own_node

from bot.services import browser_scene, node_scene
from bot.services.browser_scene import BrowserScene
from bot.services.node_scene import NodeScene
from bot.services.rueda import PRIZE_BY_KEY, Draw, draw, layout
from bot.services.rueda_render import (
    OUT_H as H,
)
from bot.services.rueda_render import (
    OUT_W as W,
)
from bot.services.rueda_render import (
    banner_for,
    board_state,
    meta_state,
    result_state,
    spin_states,
)
from bot.services.rueda_scene import SCENE, RuedaScene

module_loop = pytest.mark.asyncio(loop_scope="module")
LOCAL_CHROMIUM = Path("/opt/pw-browsers/chromium")
NO_CHROMIUM = "/no/existe/chromium"
NO_NODE = "/no/existe/node"
SHOWCASE = {"key": "rueda_titulo_eje", "name": "Eje de la Rueda", "left": 7, "of": 10}
WEDGES = layout()


def draw_of(prize: str, *, seed: int = 7) -> Draw:
    """La primera vuelta sorteada con ese premio (siempre la misma)."""
    rng = random.Random(seed)
    for _ in range(100_000):
        wedge, stop, spin_seed = draw(rng, WEDGES)
        if wedge.prize.key == prize:
            return Draw(wedge.index, prize, stop, spin_seed)
    raise LookupError(prize)


def inputs(prize: str, *, pet: str | None = None, detail: str | None = None) -> tuple:
    """`(meta, estados, duraciones)` del giro de ese premio."""
    spin = draw_of(prize)
    banner = banner_for(prize, PRIZE_BY_KEY[prize].kind, detail)
    meta = meta_state(WEDGES, showcase=SHOWCASE, streak=4, pet=pet)
    states, durations = spin_states(WEDGES, spin, banner, hamster=pet is not None)
    return meta, states, durations


def mean_diff(a: Image.Image, b: Image.Image) -> float:
    return sum(ImageStat.Stat(ImageChops.difference(a.convert("RGB"), b.convert("RGB"))).mean) / 3


def test_la_escena_existe_y_trae_sus_funciones() -> None:
    html = SCENE.read_text(encoding="utf-8")
    for function in ("setup", "renderFrames", "loadFonts"):
        assert f"window.{function} =" in html
    assert "window.exportFrame ??=" in html
    assert "toDataURL" not in html.split("window.exportFrame ??=")[0]


def test_la_escena_no_usa_azar_sin_semilla_ni_emojis() -> None:
    html = SCENE.read_text(encoding="utf-8")
    assert "Math.random" not in html
    assert not [ch for ch in html if ord(ch) >= 0x1F000], "Los emojis no salen en el Docker"


async def test_sin_node_ni_navegador_dibuja_con_pillow() -> None:
    scene = RuedaScene(executable_path=NO_CHROMIUM, node=NO_NODE)
    assert not scene.disabled
    meta, states, durations = inputs("y300")

    png = await scene.board(meta, board_state(draw_of("y300")))
    assert scene.painter.disabled and scene.browser.disabled and scene.disabled
    assert Image.open(io.BytesIO(png)).size == (W, H)

    media = await scene.spin(meta, states, durations)
    assert Image.open(io.BytesIO(media.gif)).format == "GIF"
    assert Image.open(io.BytesIO(media.png)).size == (W, H)
    await scene.close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scene() -> RuedaScene:
    """La Rueda con Node (y sin Chromium), compartida por el módulo."""
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    escena = RuedaScene(executable_path=NO_CHROMIUM)
    yield escena
    await escena.close()


@needs_node
@module_loop
async def test_el_giro_acaba_en_el_png_final(scene: RuedaScene) -> None:
    meta, states, durations = inputs("regalo", detail="A la mochila")
    media = await scene.spin(meta, states, durations)
    gif = Image.open(io.BytesIO(media.gif))
    assert gif.size == (W, H)
    assert gif.n_frames > 30
    gif.seek(gif.n_frames - 1)
    png = Image.open(io.BytesIO(media.png))
    assert png.size == (W, H)
    assert mean_diff(gif, png) < 12
    # El resultado se ve: el cartel está donde dice Python y no es un fondo liso.
    assert mean_diff(png, Image.open(io.BytesIO(await scene.board(meta, board_state(None))))) > 3


@needs_node
@module_loop
async def test_la_rueda_quieta_tiene_muchos_colores(scene: RuedaScene) -> None:
    meta = meta_state(WEDGES, showcase=SHOWCASE, streak=4, pet="hamster")
    raw = await scene.board(meta, board_state(draw_of("y300")))
    png = Image.open(io.BytesIO(raw)).convert("RGB")
    assert png.size == (W, H)
    colors = png.getcolors(maxcolors=W * H)
    assert colors is not None and len(colors) > 5_000


@needs_node
@module_loop
async def test_pintar_dos_veces_da_los_mismos_bytes(scene: RuedaScene) -> None:
    """Pintar antes del clic y al pulsar tiene que dar lo mismo: sin azar ni estado escondido."""
    meta, states, durations = inputs("otra", pet="hamster")
    first = await scene.spin(meta, states, durations)
    other = await scene.spin(meta_state(WEDGES, showcase=None, streak=0), states, durations)
    again = await scene.spin(meta, states, durations)
    assert first.gif == again.gif and first.png == again.png
    assert first.png != other.png


@needs_node
@module_loop
async def test_el_resultado_de_cada_cartel_se_pinta(scene: RuedaScene) -> None:
    """Todos los tipos de cartel salen y se distinguen entre sí."""
    meta = meta_state(WEDGES, showcase=SHOWCASE, streak=4)
    shots = []
    for prize in ("y300", "xp250", "chuche", "otra", "expositor", "nada"):
        spin = draw_of(prize)
        banner = banner_for(prize, PRIZE_BY_KEY[prize].kind)
        state = result_state(WEDGES, spin, banner)
        shots.append(Image.open(io.BytesIO(await scene.board(meta, state))).convert("RGB"))
    assert all(shot.size == (W, H) for shot in shots)
    for i, a in enumerate(shots):
        for b in shots[i + 1 :]:
            assert mean_diff(a, b) > 1


@needs_node
@module_loop
async def test_node_y_chromium_pintan_lo_mismo_la_rueda() -> None:
    """Mismo motor (Skia): solo cambia algún borde de letra, no colores ni posiciones."""
    if not LOCAL_CHROMIUM.exists():
        pytest.skip("No hay Chromium en esta máquina")
    painter = NodeScene(SCENE, name="El pintor de la Rueda")
    browser = BrowserScene(
        SCENE, name="chromium", ready="loadFonts()", executable_path=str(LOCAL_CHROMIUM)
    )
    meta, states, _ = inputs("expositor", pet="hamster", detail="Eje de la Rueda · n.º 4 de 10")
    states = states[::5] + states[-12:]
    try:
        from_browser = await browser.render(meta, states)
        from_node = await painter.render(meta, states)
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
        assert mean_diff(x, y) < 4


@needs_node
@module_loop
async def test_el_giro_repartido_pinta_lo_mismo_que_de_un_tiron(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cada tramo empieza con su `setup` y su primer fotograma entero: sale idéntico."""
    node = own_node()
    sharing = RuedaScene(executable_path=NO_CHROMIUM, node=node)
    meta, states, _ = inputs("y600", pet="hamster")
    try:
        # Abre los procesos de refuerzo: sin ellos, el primero lo pintaría todo.
        assert await sharing.painter.render(meta, states[:8]) is not None
        pool = node_scene._pool(node)
        deadline = time.monotonic() + 20
        while len([w for w in pool.workers if w.alive and not w.busy]) < 3:
            assert time.monotonic() < deadline, "No se han abierto los procesos de refuerzo"
            await asyncio.sleep(0.05)
        whole = await sharing.painter.render(meta, states, procs=1)
        assert whole is not None
        setups: list[str] = []
        call = node_scene._Process.call

        async def counted(self: node_scene._Process, request: dict, **kw: bool):  # noqa: ANN202
            setups.extend(["setup"] if request["call"] == "setup" else [])
            return await call(self, request, **kw)

        monkeypatch.setattr(node_scene._Process, "call", counted)
        split = await sharing.painter.render(meta, states)
        assert split is not None
        assert len(setups) > 1, "El dibujo no se ha repartido"
        assert len(split) == len(whole) == len(states)
        for a, b in zip(split, whole, strict=True):
            assert a["image"].tobytes() == b["image"].tobytes()
    finally:
        await sharing.close()
        await node_scene.shutdown()
