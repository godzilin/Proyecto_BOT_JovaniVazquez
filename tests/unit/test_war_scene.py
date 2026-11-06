"""Pruebas de bot.services.war_scene: la Guerra dibujada con Node, Chromium y Pillow.

Las que pintan con Node se saltan si no hay `node` o falta `@napi-rs/canvas` (`npm ci` en
la raíz); la comparación con Chromium, si no hay navegador. Los estados (lo que decide qué
se ve) y el plan B de Pillow no necesitan ninguno de los dos.
"""

from __future__ import annotations

import asyncio
import io
import time
from pathlib import Path

import pytest
import pytest_asyncio
from PIL import Image, ImageChops, ImageStat
from test_node_scene import needs_node, node_ready, own_node

from bot.services import browser_scene, node_scene
from bot.services.blackjack import Card
from bot.services.browser_scene import BrowserScene
from bot.services.node_scene import NodeScene
from bot.services.war import WarGame
from bot.services.war_scene import (
    SCENE,
    H,
    W,
    WarScene,
    deal_states,
    meta_state,
    surrender_states,
    war_states,
)

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


def hand(*cards: Card, stake: int = 500, side: int = 0) -> WarGame:
    """Mano con ese zapato (la última carta se reparte primero), ya repartida."""
    game = WarGame(stake=stake, side=side, shoe=[Card(7, 0)] * 6 + list(reversed(cards)))
    game.deal()
    return game


def won() -> WarGame:
    return hand(Card(13, 1), Card(4, 0))


def lost() -> WarGame:
    return hand(Card(4, 0), Card(1, 3), side=500)


def tied() -> WarGame:
    return hand(Card(9, 1), Card(9, 3), Card(2, 0), Card(3, 0), Card(4, 0), Card(12, 2), Card(5, 1))


def war_of(*, side: int = 500, second: tuple[Card, Card] = (Card(12, 2), Card(5, 1))) -> WarGame:
    game = hand(
        Card(9, 1), Card(9, 3), Card(2, 0), Card(3, 0), Card(4, 0), *second, stake=500, side=side
    )
    game.go_to_war()
    return game


def surrendered() -> WarGame:
    game = hand(Card(9, 1), Card(9, 3), side=500)
    game.surrender()
    return game


def without_ms(state: dict) -> dict:
    return {k: v for k, v in state.items() if k != "ms"}


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


@pytest.mark.parametrize(
    "game, states",
    [(won(), deal_states), (tied(), deal_states), (war_of(), war_states)]
    + [(surrendered(), surrender_states)],
)
def test_cada_gif_dura_unos_dos_segundos_y_acaba_quieto(game: WarGame, states: object) -> None:
    frames = states(game)  # type: ignore[operator]
    seconds = sum(f["ms"] for f in frames[:-1]) / 1000
    assert 0.3 < seconds <= 2.3, seconds
    assert len(frames) < 70
    # El último fotograma se queda quieto: es el PNG que se deja en el mensaje.
    assert frames[-1]["ms"] >= 60_000
    # Un cartel con el resultado y el importe en el panel.
    assert frames[-1]["result"]["title"] and frames[-1]["banner"]["a"] == 1


def test_primero_sale_la_tuya_y_despues_la_de_la_banca() -> None:
    frames = deal_states(won())
    shown = [
        (i, [k for k, c in enumerate(f["cards"]) if c["card"] and c["flip"] > 0.5])
        for i, f in enumerate(frames)
    ]
    first_mine = next(i for i, faces in shown if faces)
    first_bank = next(i for i, faces in shown if len(faces) == 2)
    assert first_mine < first_bank
    # Con suspense: un fotograma largo entre las dos.
    assert max(f["ms"] for f in frames[first_mine:first_bank]) >= 300
    # Y en el último, el resultado: ganas, con el importe.
    assert frames[-1]["result"] == {
        "kind": "win",
        "title": "GANAS",
        "amount": "+500",
        "sub": "COBRAS 1:1",
    }


def test_el_empate_espera_la_decision_y_la_lateral_acierta() -> None:
    game = hand(Card(9, 1), Card(9, 3), side=500)
    last = deal_states(game)[-1]
    assert last["result"]["kind"] == "tie" and last["result"]["amount"] is None
    assert last["banner"]["title"] == "EMPATE"
    assert last["side"] == {"text": "+5.000", "chip": "500", "stake": 500, "state": "win"}


def test_perder_con_lateral_apaga_la_ficha_de_la_lateral() -> None:
    last = deal_states(lost())[-1]
    assert last["result"]["amount"] == "-500" and last["result"]["kind"] == "lose"
    assert last["side"]["state"] == "lose" and last["side"]["text"] == "-500"


def test_la_guerra_parte_del_empate_tal_cual_y_quema_tres_cartas() -> None:
    game = war_of()
    deal, war = deal_states(game)[-1], war_states(game)
    # Mismo fotograma: sin salto entre un GIF y el siguiente.
    assert without_ms(war[0]) == without_ms(deal)
    # Ficha de la guerra, tres quemadas boca abajo y las cuatro cartas al final.
    assert war[-1]["bet"]["war"] == 1 and war[-1]["bet"]["text"] == "1.000"
    assert any(f["burn"] for f in war)
    most_burned = max(sum(1 for c in f["cards"] if c["card"] is None) for f in war)
    assert most_burned == 3
    assert sum(1 for c in war[-1]["cards"] if c["card"]) == 4
    assert war[-1]["result"]["title"] == "GUERRA GANADA"


@pytest.mark.parametrize(
    "second, title, amount",
    [
        ((Card(12, 2), Card(5, 1)), "GUERRA GANADA", "+500"),
        ((Card(5, 1), Card(12, 2)), "GUERRA PERDIDA", "-1.000"),
        ((Card(8, 1), Card(8, 2)), "GUERRA EMPATADA", "+1.000"),
    ],
)
def test_cada_desenlace_de_la_guerra_dice_lo_suyo(
    second: tuple[Card, Card], title: str, amount: str
) -> None:
    last = war_states(war_of(second=second))[-1]
    assert last["result"]["title"] == title and last["result"]["amount"] == amount
    assert last["side"]["state"] == "win"


def test_rendirse_enseña_la_bandera_y_lo_que_se_recupera() -> None:
    last = surrender_states(surrendered())[-1]
    assert last["banner"]["icon"] == "flag"
    assert last["result"]["title"] == "RENDICIÓN" and last["result"]["amount"] == "-250"
    assert all(c["dim"] > 0 for c in last["cards"])


def test_los_estados_son_siempre_los_mismos() -> None:
    assert war_states(war_of()) == war_states(war_of())
    assert deal_states(won()) == deal_states(won())


# -- Pillow: el plan B ------------------------------------------------------------------------


async def test_sin_node_ni_chromium_se_dibuja_con_pillow_y_sin_gif() -> None:
    scene = WarScene(executable_path=NO_CHROMIUM, node=NO_NODE)
    media = await scene.deal(won())
    assert scene.painter.disabled and scene.browser.disabled and scene.disabled
    assert media.gif is None and media.seconds == 0
    assert Image.open(io.BytesIO(media.png)).format == "PNG"
    war = await scene.war(war_of())
    assert war.gif is None and Image.open(io.BytesIO(war.png)).format == "PNG"
    assert Image.open(io.BytesIO(await scene.still(war_of()))).format == "PNG"
    await scene.close()


async def test_el_plan_b_usa_el_dibujo_de_reserva_que_se_le_da() -> None:
    class Fallback:
        def __init__(self) -> None:
            self.calls = 0

        def render(self, **kwargs: object) -> bytes:
            self.calls += 1
            return b"PNG-DE-RESERVA"

    fallback = Fallback()
    scene = WarScene(fallback, executable_path=NO_CHROMIUM, node=NO_NODE)  # type: ignore[arg-type]
    assert (await scene.surrender(surrendered())).png == b"PNG-DE-RESERVA"
    assert fallback.calls == 1
    await scene.close()


# -- Node ------------------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scene() -> WarScene:
    """Una escena de la Guerra con Node (y sin Chromium), compartida por el módulo."""
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    escena = WarScene(executable_path=NO_CHROMIUM)
    yield escena
    await escena.close()


def colors(png: bytes) -> int:
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG" and image.size == (W, H)
    return len(image.convert("RGB").getcolors(1 << 20) or [])


@pytest.mark.lenta
@needs_node
@module_loop
async def test_con_node_cada_jugada_sale_con_su_gif_sin_usar_el_navegador(
    scene: WarScene,
) -> None:
    for game, method in (
        (won(), scene.deal),
        (lost(), scene.deal),
        (tied(), scene.deal),
        (war_of(), scene.war),
        (surrendered(), scene.surrender),
    ):
        media = await method(game, skin=None)
        gif = Image.open(io.BytesIO(media.gif or b""))
        assert gif.format == "GIF" and gif.size == (W, H) and gif.n_frames > 3
        assert colors(media.png) > 100
        assert 0.3 < media.seconds <= 2.3
    assert not scene.painter.disabled
    # Si se hubiera probado Chromium (que no existe), estaría desactivado.
    assert not scene.browser.disabled


@needs_node
@module_loop
async def test_el_png_final_es_el_ultimo_fotograma_del_gif(scene: WarScene) -> None:
    media = await scene.war(war_of(), skin=None)
    gif = Image.open(io.BytesIO(media.gif or b""))
    gif.seek(gif.n_frames - 1)
    png = Image.open(io.BytesIO(media.png)).convert("RGB")
    # La paleta del GIF no es exacta: se compara por parecido.
    assert sum(ImageStat.Stat(ImageChops.difference(gif.convert("RGB"), png)).mean) / 3 < 12
    # `still` (al caducar la mesa) da el mismo fotograma sin pintar el GIF.
    still = Image.open(io.BytesIO(await scene.still(war_of(), skin=None))).convert("RGB")
    assert ImageChops.difference(still, png).getbbox() is None


@needs_node
@module_loop
async def test_la_skin_cambia_el_tapete_y_la_de_serie_es_igual_a_si_misma(
    scene: WarScene,
) -> None:
    serie = {"key": "serie", "felt": ["#2f9b62", "#1b7345", "#0b3f26"], "pattern": ""}
    plain = (await scene.deal(won(), skin=None)).png
    again = (await scene.deal(won(), skin=serie)).png
    dressed = (await scene.deal(won(), skin=BANDERA)).png
    # Sin skin y con la de serie sale la misma mesa, y con otra, otro fieltro.
    assert plain == again
    assert plain != dressed
    a, b = (Image.open(io.BytesIO(x)).convert("RGB") for x in (plain, dressed))
    assert sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3 > 5


@needs_node
@module_loop
async def test_node_y_chromium_pintan_lo_mismo_la_guerra() -> None:
    """Mismo motor (Skia): solo cambia algún borde de letra, no colores ni posiciones."""
    if not LOCAL_CHROMIUM.exists():
        pytest.skip("No hay Chromium en esta máquina")
    painter = NodeScene(SCENE, name="El pintor de la guerra")
    browser = BrowserScene(
        SCENE, name="chromium", ready="loadFonts()", executable_path=str(LOCAL_CHROMIUM)
    )
    states = [*deal_states(tied()), *war_states(war_of()), *surrender_states(surrendered())]
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
async def test_la_guerra_repartida_pinta_lo_mismo_que_de_un_tiron(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cada tramo empieza con su `setup` y su primer fotograma entero: sale idéntico."""
    node = own_node()
    sharing = WarScene(executable_path=NO_CHROMIUM, node=node)
    states = [*deal_states(tied()), *war_states(war_of()), *surrender_states(surrendered())]
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
        # Con un solo `setup` no se habría repartido.
        assert len(setups) > 1, "El dibujo no se ha repartido"
        assert len(split) == len(whole) == len(states)
        for a, b in zip(split, whole, strict=True):
            assert a["image"].tobytes() == b["image"].tobytes()
    finally:
        await sharing.close()
        await node_scene.shutdown()
