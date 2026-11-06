"""Pruebas de bot.services.blackjack_scene: el blackjack dibujado con Node, Chromium y Pillow.

Las que pintan con Node se saltan si no hay `node` o falta `@napi-rs/canvas` (`npm ci` en
la raíz); la comparación con Chromium, si no hay navegador. Los estados (lo que decide qué
se ve) y el plan B de Pillow no necesitan ninguno de los dos.
"""

from __future__ import annotations

import asyncio
import io
import time
from copy import deepcopy
from pathlib import Path

import pytest
import pytest_asyncio
from PIL import Image, ImageChops, ImageStat
from test_node_scene import needs_node, node_ready, own_node

from bot.cogs.blackjack import resolve
from bot.services import browser_scene, node_scene
from bot.services.blackjack import Action, BlackjackGame, Card
from bot.services.blackjack_scene import (
    SCENE,
    SHOE_CARDS,
    BlackjackScene,
    H,
    Shoe,
    W,
    meta_state,
    step_states,
    still_state,
)
from bot.services.browser_scene import BrowserScene
from bot.services.node_scene import NodeScene

module_loop = pytest.mark.asyncio(loop_scope="module")
LOCAL_CHROMIUM = Path("/opt/pw-browsers/chromium")
NO_CHROMIUM = "/no/existe/chromium"
NO_NODE = "/no/existe/node"

#: Un tapete con dibujo, como los de la tienda (`skins.params`).
BANDERA = {
    "key": "tapete_prueba",
    "felt": ["#7a1020", "#4d0a15", "#25040a"],
    "pattern": "rojigualda",
}


def c(rank: int, suit: int = 0) -> Card:
    return Card(rank, suit)


def shoe(*cards: Card) -> list[Card]:
    """Un zapato de 6 barajas que reparte `cards` en ese orden (debajo, sietes)."""
    return [c(7, 3)] * (SHOE_CARDS - len(cards)) + list(reversed(cards))


def dealt(*cards: Card, stake: int = 100) -> BlackjackGame:
    game = BlackjackGame(stake=stake, shoe=shoe(*cards))
    game.deal()
    return game


def play(game: BlackjackGame, action: Action) -> tuple[BlackjackGame, BlackjackGame]:
    """La mano antes y después de `action`, con la banca jugada y pagada si acaba."""
    before = deepcopy(game)
    after = deepcopy(game)
    after.act(action)
    resolve(after)
    if not after.player_turn and not after.insurance_pending:
        after.settle()
    return before, after


def plain(state: dict) -> dict:
    return {k: v for k, v in state.items() if k not in ("ms", "full")}


def seconds(states: list[dict]) -> float:
    return sum(s["ms"] for s in states[:-1]) / 1000


def standing() -> tuple[BlackjackGame, BlackjackGame]:
    """Tú 10+8 = 18; la banca 10+6 = 16 roba un 10 y se pasa."""
    return play(dealt(c(10), c(10, 1), c(8, 2), c(6, 3), c(10, 2)), Action.STAND)


# -- Estados: lo que lee la escena ---------------------------------------------------------------


def test_la_escena_existe_y_trae_sus_funciones() -> None:
    html = SCENE.read_text(encoding="utf-8")
    for function in ("setup", "renderFrames", "loadFonts"):
        assert function in html
    # Node pone su `exportFrame`; Chromium, el de PNG. Y nada de azar suelto.
    assert "window.exportFrame ??=" in html
    assert "toDataURL" not in html.split("window.exportFrame ??=")[0]
    assert "Math.random" not in html
    assert '<script src="../tapete.js">' in html and "Tapete.felt(" in html


def test_repartir_saca_tu_carta_la_de_la_banca_la_tuya_y_la_tapada() -> None:
    frames = step_states(None, dealt(c(10), c(9, 1), c(6, 2), c(8, 3)))
    last = frames[-1]
    faces = [card["card"]["label"] if card["card"] else None for card in last["cards"]]
    assert faces == ["9", None, "10", "6"]  # banca (una tapada) y tus dos
    assert {lab["kind"] for lab in last["labels"]} == {"hidden", "hand"}
    assert last["banner"] is None and last["ms"] >= 60_000
    assert 0.6 < seconds(frames) <= 2.0 and len(frames) < 60
    # Las cartas salen del zapato de una en una.
    lefts = [f["shoe"]["left"] for f in frames]
    assert lefts[0] == SHOE_CARDS and lefts[-1] == SHOE_CARDS - 4
    assert lefts == sorted(lefts, reverse=True)


def test_con_un_as_de_la_banca_la_mesa_espera_el_seguro() -> None:
    last = step_states(None, dealt(c(10), c(1, 1), c(9), c(5)))[-1]
    assert last["banner"]["title"] == "¿SEGURO?" and last["banner"]["a"] == 1


def test_plantarse_destapa_roba_y_paga_con_su_cartel() -> None:
    before, after = standing()
    frames = step_states(before, after, streak=(2, 3))
    last = frames[-1]
    assert last["banner"]["title"] == "¡LA BANCA SE PASA!" and last["banner"]["sub"] == "+100"
    assert [lab["text"] for lab in last["labels"]] == ["26 · SE PASA", "18 · GANA +100"]
    assert last["streak"] == {"n": 3, "a": 1.0}
    # El premio llega de la banca: dos montones de fichas (la apuesta y lo ganado).
    assert [chip["stake"] for chip in last["chips"]] == [100, 100]
    # Con suspense antes de la carta de la banca, y chispas al ganar.
    assert max(f["ms"] for f in frames[:-1]) >= 260
    assert any(f["sparks"] for f in frames)
    assert 0.8 < seconds(frames) <= 2.5


def test_el_importe_del_cartel_sube_hasta_lo_ganado() -> None:
    before, after = standing()
    subs = [f["banner"]["sub"] for f in step_states(before, after) if f["banner"]]
    amounts = [int(s.replace("+", "").replace(".", "")) for s in subs]
    assert amounts == sorted(amounts) and amounts[-1] == 100 and amounts[0] < 100


def test_perder_se_lleva_la_ficha_a_la_banca() -> None:
    # Tú 10+7 = 17; la banca 10+9 = 19.
    before, after = play(dealt(c(10), c(10, 1), c(7, 2), c(9, 3)), Action.STAND)
    last = step_states(before, after)[-1]
    assert last["chips"] == []
    assert last["banner"]["kind"] == "lose" and last["banner"]["sub"] == "-100"
    assert not any(f["sparks"] for f in step_states(before, after))


def test_doblar_pone_la_segunda_ficha_y_la_carta_de_lado() -> None:
    before, after = play(dealt(c(6), c(10, 1), c(5, 2), c(7, 3), c(10, 2), c(9)), Action.DOUBLE)
    last = step_states(before, after)[-1]
    mine = [card for card in last["cards"] if card["y"] >= 290]
    assert len(mine) == 3 and mine[-1]["rot"] > 1.5  # la tercera, de lado
    assert [chip["stake"] for chip in last["chips"]] == [100, 100, 200]


def test_separar_reparte_una_carta_a_cada_mano() -> None:
    before, after = play(dealt(c(8), c(10, 1), c(8, 2), c(9, 3), c(3), c(2, 1)), Action.SPLIT)
    last = step_states(before, after)[-1]
    labels = [lab for lab in last["labels"] if lab["y"] > 200]
    assert [lab["kind"] for lab in labels] == ["active", "hand"]
    assert [lab["text"] for lab in labels] == ["11", "10"]
    assert len([chip for chip in last["chips"]]) == 2


def test_cada_jugada_empieza_donde_acabo_la_anterior() -> None:
    game = dealt(c(5), c(9, 1), c(6, 2), c(8, 3), c(3), c(2, 1), c(9), c(10, 1), c(4, 2), c(6))
    deal = step_states(None, game)
    before, hit = play(game, Action.HIT)
    hit_states = step_states(before, hit)
    assert plain(deal[-1]) == plain(hit_states[0])
    before, stood = play(hit, Action.STAND)
    stand_states = step_states(before, stood, streak=(0, 1))
    assert plain(hit_states[-1]) == plain(stand_states[0])
    assert plain(stand_states[-1]) == plain(still_state(stood, streak=1))
    # La mano siguiente arranca con la mesa pagada y la recoge a la bandeja.
    left = len(stood.shoe)
    following = BlackjackGame(stake=100, shoe=stood.shoe)
    following.deal()
    resolve(following)
    again = step_states(stood, following, shoe=Shoe(left=left), streak=(1, 1))
    assert plain(stand_states[-1]) == plain(again[0])


def test_la_bandeja_crece_con_lo_que_sale_y_el_zapato_mengua() -> None:
    before, after = standing()
    first = step_states(before, after)
    on_table = 5
    end = first[-1]["shoe"]
    assert end["left"] == len(after.shoe)
    assert end["tray"] == SHOE_CARDS - end["left"] - on_table
    # Al repartir otra, las cinco van a la bandeja.
    nxt = BlackjackGame(stake=100, shoe=after.shoe)
    left = len(after.shoe)
    nxt.deal()
    states = step_states(after, nxt, shoe=Shoe(left=left))
    trays = [f["shoe"]["tray"] for f in states]
    assert max(trays) == end["tray"] + on_table and trays == sorted(trays)


def test_la_carta_de_corte_baraja_con_su_cartel_y_vacia_la_bandeja() -> None:
    before, after = standing()
    nxt = dealt(c(10), c(9, 1), c(6, 2), c(8, 3))
    states = step_states(after, nxt, shoe=Shoe(left=60, shuffle="cut"), streak=(1, 1))
    titles = {f["banner"]["title"] for f in states if f["banner"]}
    assert "ZAPATO NUEVO" in titles
    assert states[-1]["shoe"] == {"left": SHOE_CARDS - 4, "tray": 0}
    boss = step_states(after, nxt, shoe=Shoe(left=60, shuffle="boss"))
    assert "EL JEFE DE SALA BARAJA" in {f["banner"]["title"] for f in boss if f["banner"]}


def test_la_mesa_no_da_la_cuenta_ni_las_barajas_que_quedan() -> None:
    """Solo lo que se ve en un casino: cartas, el zapato y la bandeja. Las cuentas, tuyas."""
    before, after = standing()
    for state in step_states(before, after):
        assert set(state) == {
            "cards",
            "labels",
            "chips",
            "shoe",
            "streak",
            "banner",
            "sparks",
            "ms",
        }
        assert set(state["shoe"]) == {"left", "tray"}
    assert set(meta_state(None)) == {"skin", "cards", "cut", "decks"}


def test_un_blackjack_al_repartir_se_paga_en_el_mismo_gif() -> None:
    game = dealt(c(1), c(9, 1), c(13, 2), c(8, 3))
    resolve(game)
    game.settle()
    frames = step_states(None, game, streak=(0, 1))
    assert frames[-1]["banner"]["title"] == "¡BLACKJACK!"
    assert frames[-1]["chips"][-1]["gold"] is True
    assert seconds(frames) <= 2.6


def test_los_estados_son_siempre_los_mismos() -> None:
    before, after = standing()
    assert step_states(before, after) == step_states(before, after)


# -- Pillow: el plan B ------------------------------------------------------------------------


async def test_sin_node_ni_chromium_se_dibuja_con_pillow_y_sin_gif() -> None:
    scene = BlackjackScene(executable_path=NO_CHROMIUM, node=NO_NODE)
    before, after = standing()
    media = await scene.step(before, after)
    assert scene.painter.disabled and scene.browser.disabled and scene.disabled
    assert media.gif is None and media.seconds == 0
    assert Image.open(io.BytesIO(media.png)).format == "PNG"
    assert Image.open(io.BytesIO(await scene.still(after))).format == "PNG"
    await scene.close()


# -- Node ------------------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scene() -> BlackjackScene:
    """Una escena del blackjack con Node (y sin Chromium), compartida por el módulo."""
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    escena = BlackjackScene(executable_path=NO_CHROMIUM)
    yield escena
    await escena.close()


def colors(png: bytes) -> int:
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG" and image.size == (W, H)
    return len(image.convert("RGB").getcolors(1 << 20) or [])


def frames_of(gif: bytes) -> int:
    return Image.open(io.BytesIO(gif)).n_frames


@pytest.mark.lenta
@needs_node
@module_loop
async def test_con_node_cada_jugada_sale_con_su_gif(scene: BlackjackScene) -> None:
    game = dealt(c(5), c(9, 1), c(6, 2), c(8, 3), c(3))
    deal = await scene.step(None, game)
    before, hit = play(game, Action.HIT)
    hit_media = await scene.step(before, hit)
    before, stood = standing()
    stand = await scene.step(before, stood, streak=(0, 1))
    for media in (deal, hit_media, stand):
        gif = Image.open(io.BytesIO(media.gif or b""))
        assert gif.format == "GIF" and gif.size == (W, H) and gif.n_frames > 3
        assert colors(media.png) > 100
    # Ningún GIF se repite: si el PNG tarda, se queda quieto en el último fotograma.
    assert not any(b"NETSCAPE2.0" in (m.gif or b"") for m in (deal, hit_media, stand))
    assert not scene.painter.disabled and not scene.browser.disabled


@needs_node
@module_loop
async def test_el_png_final_es_el_ultimo_fotograma_y_el_de_la_mesa_quieta(
    scene: BlackjackScene,
) -> None:
    before, after = standing()
    media = await scene.step(before, after, streak=(0, 1))
    gif = Image.open(io.BytesIO(media.gif or b""))
    gif.seek(gif.n_frames - 1)
    png = Image.open(io.BytesIO(media.png)).convert("RGB")
    assert sum(ImageStat.Stat(ImageChops.difference(gif.convert("RGB"), png)).mean) / 3 < 12
    still = Image.open(io.BytesIO(await scene.still(after, streak=1))).convert("RGB")
    assert ImageChops.difference(still, png).getbbox() is None


@needs_node
@module_loop
async def test_la_skin_cambia_el_tapete_y_la_de_serie_es_igual_a_si_misma(
    scene: BlackjackScene,
) -> None:
    serie = {"key": "serie", "felt": ["#2f9b62", "#1b7345", "#0b3f26"], "pattern": ""}
    _, after = standing()
    plain_png = await scene.still(after, skin=None)
    again = await scene.still(after, skin=serie)
    dressed = await scene.still(after, skin=BANDERA)
    assert plain_png == again
    a, b = (Image.open(io.BytesIO(x)).convert("RGB") for x in (plain_png, dressed))
    assert sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3 > 5


@needs_node
@module_loop
async def test_la_bandeja_y_el_zapato_se_ven_distintos_segun_lo_jugado(
    scene: BlackjackScene,
) -> None:
    """Quien cuenta tiene que poder ver cuánto se ha jugado: cambia el dibujo, no un número."""
    _, after = standing()
    fresh = Image.open(io.BytesIO(await scene.still(after))).convert("RGB")
    worn = deepcopy(after)
    worn.shoe = worn.shoe[: SHOE_CARDS // 3]
    used = Image.open(io.BytesIO(await scene.still(worn))).convert("RGB")
    # Solo cambian las dos esquinas de arriba: el zapato y la bandeja.
    box = ImageChops.difference(fresh, used).getbbox()
    assert box is not None and box[1] < 70 and box[3] < 110


@needs_node
@module_loop
async def test_node_y_chromium_pintan_lo_mismo_el_blackjack() -> None:
    """Mismo motor (Skia): solo cambia algún borde de letra, no colores ni posiciones."""
    if not LOCAL_CHROMIUM.exists():
        pytest.skip("No hay Chromium en esta máquina")
    painter = NodeScene(SCENE, name="El pintor del blackjack")
    browser = BrowserScene(
        SCENE, name="chromium", ready="loadFonts()", executable_path=str(LOCAL_CHROMIUM)
    )
    before, after = standing()
    states = step_states(before, after)
    meta = meta_state(BANDERA)
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
        assert sum(ImageStat.Stat(ImageChops.difference(x, y)).mean) / 3 < 4


@pytest.mark.lenta
@needs_node
@module_loop
async def test_el_reparto_repartido_pinta_lo_mismo_que_de_un_tiron(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cada tramo empieza con su `setup` y su primer fotograma entero: sale idéntico."""
    node = own_node()
    sharing = BlackjackScene(executable_path=NO_CHROMIUM, node=node)
    before, after = standing()
    nxt = BlackjackGame(stake=100, shoe=after.shoe)
    left = len(after.shoe)
    nxt.deal()
    states = [
        *step_states(before, after, streak=(0, 1)),
        *step_states(after, nxt, shoe=Shoe(left=left, shuffle="cut"), streak=(1, 1)),
    ]
    meta = meta_state(BANDERA)
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
