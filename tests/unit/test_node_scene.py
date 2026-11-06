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

from bot.services import browser_scene, node_scene
from bot.services.browser_scene import BrowserScene
from bot.services.coin import CoinGame, Outcome, Side
from bot.services.coin_render import H, W, meta_state, toss_states
from bot.services.coin_scene import SCENE, CoinScene
from bot.services.craps_scene import SCENE as DICE
from bot.services.node_scene import MAX_WORKERS, MIN_WORKERS, RUNNER, NodeScene

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


async def open_helpers(painter: NodeScene, meta: object, states: list) -> None:
    """Abre los procesos de refuerzo antes de una prueba de dibujo repartido.

    Sin ellos, con la máquina cargada (toda la tanda en paralelo), el primer proceso lo
    pinta todo antes de que se abra otro y la prueba no ve el reparto.
    """
    assert await painter.render(meta, states[:8]) is not None
    pool = painter._use_pool()
    deadline = asyncio.get_running_loop().time() + 20
    while len([w for w in pool.workers if w.alive and not w.busy]) < 3:
        assert asyncio.get_running_loop().time() < deadline, "No se abren los procesos de refuerzo"
        await asyncio.sleep(0.05)


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


@needs_node
@module_loop
async def test_cortar_un_dibujo_a_medias_no_estropea_el_siguiente() -> None:
    """Cancelado con píxeles aún en la tubería, el pintor se reinicia en vez de leer basura."""
    scene = NodeScene(SCENE, name="prueba")
    game = lost_game()
    meta, states = meta_state(game.stake), toss_states(game, start=Side.CARA, seed=2)
    task = asyncio.create_task(scene.render(meta, states))
    await asyncio.sleep(0.15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not scene.disabled
    patches = await scene.render(meta, states)
    assert patches is not None and len(patches) == len(states)
    assert not scene.disabled
    await scene.close()


@needs_node
@module_loop
async def test_una_serie_de_llamadas_devuelve_las_imagenes_de_cada_una() -> None:
    """`sequence` sirve a escenas con otras funciones: aquí, setup y luego fotogramas."""
    scene = NodeScene(SCENE, name="prueba")
    game = lost_game()
    states = toss_states(game, start=Side.CARA, seed=3)[:4]
    results = await scene.sequence(
        [("setup", [meta_state(game.stake)]), ("renderFrames", [states, 0])]
    )
    assert results is not None
    setup, frames = results
    assert setup == [] and len(frames) == 4
    assert frames[0]["image"].size == (W, H)
    await scene.close()


def own_node() -> str:
    """El `node` del PATH con su ruta: un grupo de procesos aparte del de `painter`."""
    node = shutil.which("node")
    assert node is not None
    return node


@needs_node
@module_loop
async def test_dos_escenas_comparten_proceso_sin_pisarse_las_globales() -> None:
    node = own_node()
    coin = NodeScene(SCENE, name="moneda", node=node)
    dice = NodeScene(DICE, name="dados", node=node)
    await coin.evaluate("window.marca = 1")
    assert await dice.evaluate("typeof marca") == "undefined"
    assert await coin.evaluate("marca") == 1
    game = lost_game()
    states = toss_states(game, start=Side.CARA, seed=5)
    patches = await coin.render(meta_state(game.stake), states)
    assert patches is not None and len(patches) == len(states)
    pool = node_scene._pool(node)
    assert MIN_WORKERS <= len(pool.workers) <= MAX_WORKERS
    await coin.close()
    assert pool.workers, "Con los dados aún abiertos, los procesos siguen"
    await dice.close()
    assert not pool.workers and node not in node_scene._pools.get(asyncio.get_running_loop(), {})


@needs_node
@module_loop
async def test_un_dibujo_abre_los_que_faltan_y_se_reparte_con_otro() -> None:
    node = own_node()
    await node_scene.warm(node)
    pool = node_scene._pool(node)
    try:
        assert len(pool.workers) == MIN_WORKERS
        first = node_scene._Draw(MAX_WORKERS, more=lambda: True)
        assert len(await pool.start(first)) == MIN_WORKERS
        # Al empezar se abren los que le faltan, que se le unen según arrancan.
        assert len(pool.workers) + pool.starting == MAX_WORKERS
        await asyncio.gather(*pool._background)
        joined = [await pool.recruit(first) for _ in range(MAX_WORKERS - MIN_WORKERS)]
        assert all(joined) and len(first.workers) == MAX_WORKERS
        # Un segundo dibujo espera a uno libre y luego se reparten a medias.
        second = node_scene._Draw(MAX_WORKERS, more=lambda: True)
        starting = asyncio.create_task(pool.start(second))
        await asyncio.sleep(0)
        assert not starting.done()
        await pool.give_back(first, joined[0])
        assert len(await starting) == 1
        await pool.give_back(first, joined[1])
        assert await pool.recruit(second) is not None
        assert len(second.workers) == MAX_WORKERS // 2
        await pool.give_back(first, joined[2])
        assert await pool.recruit(first) is not None
        assert len(first.workers) == MAX_WORKERS // 2
        # Con su mitad, no se lleva más aunque quede uno libre.
        await pool.give_back(second, next(iter(second.workers)))
        waiting = asyncio.create_task(pool.recruit(first))
        await asyncio.sleep(0.05)
        assert not waiting.done()
        await pool.finish(first)
        assert await waiting is None
        await pool.finish(second)
        assert pool.drawing == 0 and len(pool._idle()) == MAX_WORKERS
    finally:
        await node_scene.shutdown()


@needs_node
@module_loop
async def test_los_procesos_de_mas_se_cierran_y_queda_el_minimo() -> None:
    node = own_node()
    pool = node_scene._pool(node, idle_seconds=0.2)
    await pool.warm()
    try:
        draw = node_scene._Draw(MAX_WORKERS)
        await pool.start(draw)
        await asyncio.gather(*pool._background)
        await pool.finish(draw)
        assert len(pool.workers) > MIN_WORKERS
        # Con la máquina cargada el cierre puede tardar alguna vuelta más.
        for _ in range(50):
            if len(pool.workers) == MIN_WORKERS:
                break
            await asyncio.sleep(0.1)
        assert len(pool.workers) == MIN_WORKERS
    finally:
        await node_scene.shutdown()


@needs_node
@module_loop
async def test_abrir_procesos_seguidos_no_pasa_del_tope() -> None:
    """El hueco de un proceso cuenta desde que se pide, no desde que arranca."""
    node = own_node()
    pool = node_scene._pool(node)
    try:
        pool.draws = [node_scene._Draw(MAX_WORKERS), node_scene._Draw(MAX_WORKERS)]
        for _ in range(3):
            pool._grow()
        await asyncio.gather(*pool._background)
        assert len(pool.workers) == MAX_WORKERS
        assert pool.starting == 0
        pool.draws = []
    finally:
        await node_scene.shutdown()


def test_robar_se_lleva_la_mitad_final_del_tramo_con_mas_trabajo() -> None:
    spans = [node_scene._Span(10, 12), node_scene._Span(20, 40)]
    stolen = node_scene._steal(spans)
    assert stolen is not None and (stolen.pos, stolen.end) == (30, 40)
    assert (spans[1].pos, spans[1].end) == (20, 30) and spans[-1] is stolen
    # Con menos de dos tandas mínimas pendientes no compensa otro `setup`.
    assert node_scene._steal([node_scene._Span(0, 2 * node_scene.MIN_STEAL - 1)]) is None


@needs_node
@module_loop
async def test_los_que_se_unen_a_mitad_pintan_lo_mismo_que_uno_solo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Con un solo proceso abierto, los que arrancan durante el dibujo se llevan parte.

    El resultado es el mismo, píxel a píxel, que pintarlo todo con un proceso.
    """
    node = own_node()
    await node_scene.warm(node)
    scene = NodeScene(SCENE, name="prueba", node=node)
    steals: list[int] = []
    steal = node_scene._steal

    def counted(spans: list[node_scene._Span]) -> node_scene._Span | None:
        stolen = steal(spans)
        if stolen is not None:
            steals.append(stolen.pos)
        return stolen

    monkeypatch.setattr(node_scene, "_steal", counted)
    try:
        game = lost_game()
        meta = meta_state(game.stake)
        states = toss_states(game, start=Side.CARA, seed=5) * 3
        shared = await scene.render(meta, states)
        assert shared is not None and steals, "Nadie se ha unido al dibujo"
        alone = await scene.render(meta, states, procs=1)
        assert alone is not None
        for a, b in zip(shared, alone, strict=True):
            assert a["image"].tobytes() == b["image"].tobytes()
    finally:
        await scene.close()
        await node_scene.shutdown()


async def test_cada_dibujo_sin_node_cuenta_un_plan_b() -> None:
    from bot.services import telemetry as telemetry_service

    seen: list[tuple[str, str, bool]] = []

    def observer(kind: str, name: str, ms: float | None, error: bool) -> None:
        seen.append((kind, name, error))

    scene = NodeScene(SCENE, name="prueba", node="/no/existe/node")
    telemetry_service.event_observers.add(observer)
    try:
        await scene.render({}, [{}])  # se apaga
        await scene.render({}, [{}])  # ya apagado
    finally:
        telemetry_service.event_observers.discard(observer)
        await scene.close()
    assert seen == [("plan_b", "prueba · Node", True), ("plan_b", "prueba · Node", False)]
