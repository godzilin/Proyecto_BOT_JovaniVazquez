"""Pruebas de bot.services.slots_scene: el ritmo de la tragaperras y su dibujo.

Los estados (lo que decide qué se ve y cuánto dura) y el plan B de Pillow no necesitan
Node ni navegador. Las que pintan con Node se saltan si no hay `node` o falta
`@napi-rs/canvas` (`npm ci` en la raíz); la comparación con Chromium, si no hay navegador.
"""

from __future__ import annotations

import asyncio
import io
import itertools
import time
from pathlib import Path

import pytest
import pytest_asyncio
from PIL import Image, ImageChops, ImageStat
from test_node_scene import needs_node, node_ready, own_node

from bot.services import browser_scene, node_scene
from bot.services.browser_scene import BrowserScene
from bot.services.node_scene import NodeScene
from bot.services.slots import (
    BIG_WIN,
    EPIC_WIN,
    REEL_STRIPS,
    SEVEN,
    Kind,
    Spin,
    WinTier,
    spin_at,
)
from bot.services.slots_scene import (
    BLACK_SUITS,
    FRAME_MS,
    OUT_H,
    OUT_W,
    RED_SUITS,
    SCENE,
    SLOW_FRAME_MS,
    SMALL_ROLLUP_MS,
    SlotsScene,
    double_states,
    double_still,
    durations,
    media_key,
    meta_state,
    spin_states,
    still_state,
    tease_of,
    tracks_for,
)

module_loop = pytest.mark.asyncio(loop_scope="module")
LOCAL_CHROMIUM = Path("/opt/pw-browsers/chromium")
NO_CHROMIUM = "/no/existe/chromium"
NO_NODE = "/no/existe/node"
ALL_STOPS = list(itertools.product(*(range(len(s)) for s in REEL_STRIPS)))


def spin_where(predicate) -> Spin:  # noqa: ANN001
    for stops in ALL_STOPS:
        if predicate(spin := spin_at(stops)):
            return spin
    raise AssertionError("Ninguna tirada cumple la condición")


LOSS = spin_where(lambda s: not s.pay_halves and not s.anticipation and not s.scatters)
LINE_TEASE = spin_where(lambda s: tease_of(s).kind == "line" and not s.pay_halves and s.teaser)
JACKPOT_TEASE = spin_where(lambda s: tease_of(s).kind == "jackpot" and not s.is_jackpot)
SCATTER_TEASE = spin_where(lambda s: tease_of(s).kind == "scatter" and not s.pay_halves)
CHERRY = spin_where(lambda s: s.kind == Kind.CHERRY and not s.anticipation)
SEVENS = spin_where(lambda s: s.kind == Kind.THREE and s.symbol == SEVEN)
FREE = spin_where(lambda s: s.triggers_free_spins and not s.pay_halves)


def seconds(states: list[dict]) -> float:
    return sum(durations(states)[:-1]) / 1000


# -- El ritmo ------------------------------------------------------------------------------


def test_sin_nada_en_juego_la_tirada_es_corta() -> None:
    assert 1.2 <= seconds(spin_states(LOSS)) <= 1.8


def test_la_anticipacion_hace_esperar_y_el_camino_del_bote_aun_mas() -> None:
    loss, line, jackpot = (seconds(spin_states(s)) for s in (LOSS, LINE_TEASE, JACKPOT_TEASE))
    assert loss + 1.5 < line < jackpot
    assert seconds(spin_states(SCATTER_TEASE)) > loss + 1.5


def test_el_frenazo_lento_son_fotogramas_largos_y_no_mas_fotogramas() -> None:
    normal = durations(spin_states(LOSS))[:-1]
    slow = durations(spin_states(LINE_TEASE))[:-1]
    assert set(normal) <= {FRAME_MS, FRAME_MS + 10, FRAME_MS + 20}
    assert max(slow) >= SLOW_FRAME_MS
    # La unidad del GIF son las centésimas, y ningún fotograma es tan corto que no se vea.
    assert all(ms % 10 == 0 and ms >= 40 for ms in normal + slow)


def test_los_rodillos_acaban_en_sus_paradas_y_el_ultimo_es_el_quieto() -> None:
    for spin, won in ((LOSS, 0), (LINE_TEASE, 0), (CHERRY, 50), (SEVENS, 20_000)):
        states = spin_states(spin, won=won, stake=100)
        assert states[-1] == still_state(spin, won=won, stake=100)
        stopped = [s for s in states if s["reels"] == [float(x) for x in spin.stops]]
        assert stopped and all(s["blur"] == [0.0, 0.0, 0.0] for s in stopped)


def test_las_paradas_varian_de_una_tirada_a_otra_pero_se_repiten_para_la_misma() -> None:
    assert spin_states(LOSS) == spin_states(LOSS)
    lands = {tracks_for(spin_at(stops))[0].land for stops in ALL_STOPS[:200]}
    assert len(lands) > 3


def test_la_anticipacion_enciende_la_tension_y_el_bote_apaga_lo_demas() -> None:
    line = spin_states(LINE_TEASE)
    assert any(s["glow"] > 0 and s["bulbs"]["shape"] == "diamond" for s in line)
    assert all(s["dim"] == 0 for s in line)
    assert max(s["dim"] for s in spin_states(JACKPOT_TEASE)) > 0.5
    scatter = spin_states(SCATTER_TEASE)
    assert any(s["glowColor"] != line[0]["glowColor"] for s in scatter if s["glow"])


def test_en_el_regiro_solo_gira_el_tercero() -> None:
    states = spin_states(LINE_TEASE, respin=True)
    assert {tuple(s["reels"][:2]) for s in states} == {tuple(map(float, LINE_TEASE.stops[:2]))}
    assert len({s["reels"][2] for s in states}) > 10


# -- El premio -------------------------------------------------------------------------------


def test_el_cartel_sube_de_nivel_mientras_cuenta() -> None:
    won = 200 * 100
    states = spin_states(SEVENS, won=won, stake=100)
    tiers = [s["banner"]["tier"] if s["banner"] else None for s in states if s["meter"]]
    seen = list(dict.fromkeys(tiers))
    assert seen == [None, WinTier.BIG, WinTier.MEGA, WinTier.EPIC]
    amounts = [int(s["meter"]["text"].split()[0].replace(".", "")) for s in states if s["meter"]]
    assert amounts == sorted(amounts) and amounts[-1] == won
    # El marcador elige su letra con el importe final desde el primer fotograma.
    assert {s["meter"]["fit"] for s in states if s["meter"]} == {"20.000 Y$"}
    # Frena en cada umbral: hay un fotograma justo en ×5 y en ×50.
    assert {BIG_WIN * 100, EPIC_WIN * 100} <= set(amounts)
    assert any(s["coins"] for s in states) and any(s["sparkles"] for s in states)


def test_un_premio_menor_que_la_apuesta_cuenta_deprisa() -> None:
    states = spin_states(CHERRY, won=50, stake=100)
    counting = [s for s in states[:-1] if s["meter"]]
    assert sum(s["ms"] for s in counting) <= SMALL_ROLLUP_MS + 60
    assert all(s["banner"] is None for s in states)


def test_los_giros_gratis_iluminan_sus_entradas_sin_marcador() -> None:
    states = spin_states(FREE)
    final = states[-1]
    assert final["meter"] is None and final["line"] == 0
    assert final["hits"] and all(FREE.grid[row][reel] == "S" for reel, row in final["hits"])


def test_la_imagen_sin_premio_no_depende_de_la_apuesta() -> None:
    assert media_key(LOSS, won=0, stake=100, turbo=False) == media_key(
        LOSS, won=0, stake=5_000, turbo=False
    )
    assert media_key(CHERRY, won=50, stake=100, turbo=False) != media_key(
        CHERRY, won=50, stake=10, turbo=False
    )


# -- Doble o nada ----------------------------------------------------------------------------


def test_la_carta_sale_del_color_que_toca() -> None:
    for pick, won, suits in (
        ("red", True, RED_SUITS),
        ("red", False, BLACK_SUITS),
        ("black", True, BLACK_SUITS),
        ("black", False, RED_SUITS),
    ):
        final = double_still(stops=LOSS.stops, amount=400, pick=pick, won=won, history=["H"])
        card = final["gamble"]["card"]
        assert card["flip"] == 1 and card["suit"] in suits
        assert final["gamble"]["history"] == ["H", card["suit"]]


def test_el_doble_o_nada_tiembla_se_gira_y_acaba_en_la_carta_destapada() -> None:
    fresh = double_states(
        stops=LOSS.stops, amount=400, pick="red", won=True, history=[], fresh=True
    )
    again = double_states(
        stops=LOSS.stops, amount=800, pick="red", won=False, history=["D"], fresh=False
    )
    # La primera se funde sobre los rodillos; la siguiente ya empieza en la carta.
    assert fresh[0]["gamble"]["mix"] < 1 and again[0]["gamble"]["mix"] == 1
    for states, won in ((fresh, True), (again, False)):
        flips = [s["gamble"]["card"]["flip"] for s in states]
        assert flips == sorted(flips) and flips[0] == 0 and flips[-1] == 1
        assert states[-1]["gamble"]["stamp"]["kind"] == ("win" if won else "lose")
        assert 1.2 < seconds(states) < 3.5
    assert fresh[-1] == double_still(stops=LOSS.stops, amount=400, pick="red", won=True, history=[])
    assert more_than_history(again)


def more_than_history(states: list[dict]) -> bool:
    """Las últimas cartas no cambian hasta que se destapa la nueva."""
    return {tuple(s["gamble"]["history"]) for s in states[:-1]} == {("D",)}


# -- Pillow: el plan B -----------------------------------------------------------------------


async def test_sin_node_ni_chromium_se_dibuja_con_el_pillow_de_antes() -> None:
    scene = SlotsScene(executable_path=NO_CHROMIUM, node=NO_NODE)
    media = await scene.spin(CHERRY, won=50, stake=100)
    assert scene.disabled
    assert Image.open(io.BytesIO(media.gif)).format == "GIF" and media.seconds > 0
    assert Image.open(io.BytesIO(media.png)).format == "PNG"
    assert Image.open(io.BytesIO(await scene.still(LOSS))).format == "PNG"
    # El doble o nada se queda sin imagen: el cog solo cambia el texto.
    assert (
        await scene.double(
            stops=LOSS.stops, amount=100, pick="red", won=True, history=[], fresh=True
        )
        is None
    )
    await scene.close()


# -- Node ------------------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scene() -> SlotsScene:
    """Una tragaperras con Node (y sin Chromium), compartida por el módulo."""
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    escena = SlotsScene(executable_path=NO_CHROMIUM)
    yield escena
    await escena.close()


def frames_of(gif: bytes) -> int:
    return Image.open(io.BytesIO(gif)).n_frames


@pytest.mark.lenta
@needs_node
@module_loop
async def test_con_node_cada_tirada_sale_con_su_gif(scene: SlotsScene) -> None:
    for spin, won in ((LOSS, 0), (SEVENS, 20_000)):
        states = spin_states(spin, won=won, stake=100)
        media = await scene.spin(spin, won=won, stake=100)
        gif = Image.open(io.BytesIO(media.gif))
        assert gif.format == "GIF" and gif.size == (OUT_W, OUT_H)
        assert frames_of(media.gif) == len(states)
        assert media.seconds == pytest.approx(seconds(states))
        png = Image.open(io.BytesIO(media.png))
        assert png.size == (OUT_W, OUT_H) and len(png.convert("RGB").getcolors(1 << 20) or []) > 200
    assert not scene.painter.disabled


@needs_node
@module_loop
async def test_el_png_final_es_el_ultimo_fotograma_y_la_maquina_quieta(scene: SlotsScene) -> None:
    media = await scene.spin(LINE_TEASE)
    gif = Image.open(io.BytesIO(media.gif))
    gif.seek(gif.n_frames - 1)
    png = Image.open(io.BytesIO(media.png)).convert("RGB")
    assert sum(ImageStat.Stat(ImageChops.difference(gif.convert("RGB"), png)).mean) / 3 < 12
    still = Image.open(io.BytesIO(await scene.still(LINE_TEASE))).convert("RGB")
    assert ImageChops.difference(still, png).getbbox() is None
    turbo = await scene.spin(LINE_TEASE, turbo=True)
    assert turbo.gif == b"" and turbo.png == media.png


@needs_node
@module_loop
async def test_el_doble_o_nada_se_pinta_con_node(scene: SlotsScene) -> None:
    media = await scene.double(
        stops=LOSS.stops, amount=400, pick="black", won=False, history=["H"], fresh=True
    )
    assert media is not None
    states = double_states(
        stops=LOSS.stops, amount=400, pick="black", won=False, history=["H"], fresh=True
    )
    assert frames_of(media.gif) == len(states)
    # La carta tapa los rodillos: la imagen final no se parece a la máquina quieta.
    card = Image.open(io.BytesIO(media.png)).convert("RGB")
    machine = Image.open(io.BytesIO(await scene.still(LOSS))).convert("RGB")
    assert sum(ImageStat.Stat(ImageChops.difference(card, machine)).mean) / 3 > 10


@needs_node
@module_loop
async def test_node_y_chromium_pintan_lo_mismo_la_tragaperras() -> None:
    """Mismo motor (Skia): solo cambia algún borde de letra, no colores ni posiciones."""
    if not LOCAL_CHROMIUM.exists():
        pytest.skip("No hay Chromium en esta máquina")
    painter = NodeScene(SCENE, name="El pintor de la tragaperras")
    browser = BrowserScene(
        SCENE, name="chromium", ready="loadFonts()", executable_path=str(LOCAL_CHROMIUM)
    )
    states = spin_states(SEVENS, won=20_000, stake=100)[::6]
    states += double_states(
        stops=LOSS.stops, amount=400, pick="red", won=True, history=["S"], fresh=True
    )[::4]
    meta = meta_state()
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
        asyncio.to_thread(browser_scene.assemble, from_browser, (OUT_W, OUT_H)),
        asyncio.to_thread(browser_scene.assemble, from_node, (OUT_W, OUT_H)),
    )
    assert len(a) == len(b) == len(states)
    for x, y in zip(a, b, strict=True):
        assert sum(ImageStat.Stat(ImageChops.difference(x, y)).mean) / 3 < 4


@pytest.mark.lenta
@needs_node
@module_loop
async def test_la_tirada_repartida_pinta_lo_mismo_que_de_un_tiron(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cada tramo empieza con su `setup` y su primer fotograma entero: sale idéntico."""
    node = own_node()
    sharing = SlotsScene(executable_path=NO_CHROMIUM, node=node)
    states = spin_states(SEVENS, won=20_000, stake=100)
    meta = meta_state()
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
