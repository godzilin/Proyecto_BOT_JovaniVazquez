"""Pruebas de bot.services.backgammon_scene: el backgammon con canvas (Node, Chromium o Pillow).

Las de Node se saltan si no hay `node` o falta `@napi-rs/canvas` (`npm ci` en la
raíz). La comparación con Chromium se salta también si no hay navegador.
"""

from __future__ import annotations

import asyncio
import io
import json
import time

import pytest
import pytest_asyncio
from PIL import Image, ImageChops, ImageStat
from test_node_scene import LOCAL_CHROMIUM, needs_node, node_ready, own_node

from bot.services import browser_scene, node_scene
from bot.services.backgammon import (
    BAR,
    OFF,
    Position,
    Side,
    Step,
    dice_of,
    legal_plays,
)
from bot.services.backgammon_render import (
    GEOMETRY,
    Cube,
    H,
    Player,
    W,
    banner,
    bar_xy,
    board_state,
    chain,
    cube_steps,
    meta_state,
    move_steps,
    off_xy,
    opening_steps,
    point_x,
    rest_dice,
    roll_steps,
    stack_xy,
)
from bot.services.backgammon_scene import SCENE, BackgammonScene
from bot.services.browser_scene import BrowserScene
from bot.services.node_scene import patch_png

module_loop = pytest.mark.asyncio(loop_scope="module")
NO_CHROMIUM = "/no/existe/chromium"
NO_NODE = "/no/existe/node"
SIZE = (W, H)
META = meta_state(
    Player("Diego", "Fichas blancas"),
    Player("Jovani el implacable de Moncloa", "Nivel difícil"),
    title="BACKGAMMON",
    subtitle="Mesa de Yapland",
    seed=7,
)
INITIAL = Position.initial()
#: Una ficha negra sola en el punto 9 (índice): las blancas la comen con un 3.
BLOT = Position(
    tuple({12: 5, 5: 3, 23: 2, 9: -1, 16: -3, 0: -2, 18: -4}.get(i, 0) for i in range(24))
)
#: Las blancas lo tienen todo en casa y las negras han sacado ya ocho.
HOME = Position(
    tuple({0: 3, 1: 3, 2: 3, 3: 3, 4: 3, 21: -3, 22: -2, 23: -2}.get(i, 0) for i in range(24)),
    (0, 0),
    (0, 8),
)
RED = {"key": "rojo", "felt": ["#d23a3a", "#a01818", "#4a0808"], "pattern": ""}
GOLD = {"key": "oro", "felt": ["#ffe9a3", "#b07d22", "#5c3b07"], "pattern": "oro"}


def png_image(png: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG" and image.size == SIZE
    return image.convert("RGB")


def difference(a: Image.Image, b: Image.Image) -> float:
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


def play_of(position: Position, side: Side, roll: tuple[int, int], wanted) -> tuple[Step, ...]:
    for play in legal_plays(position, side, dice_of(roll)):
        if wanted(play):
            return play
    raise AssertionError("No hay jugada con esa condición")


def typical_turn() -> tuple[list, dict]:
    """Un turno de verdad: cuatro pasos con una comida, los dados rivales, dos pasos y los míos."""
    play = play_of(BLOT, Side.WHITE, (3, 3), lambda p: len(p) == 4 and any(s.hit for s in p))
    moved = BLOT
    for step in play:
        moved = moved.apply(Side.WHITE, step)
    reply = play_of(moved, Side.BLACK, (6, 2), lambda p: len(p) == 2)
    answered = moved
    for step in reply:
        answered = answered.apply(Side.BLACK, step)
    return chain(
        move_steps(BLOT, Side.WHITE, play, (3, 3)),
        roll_steps(moved, Side.BLACK, (6, 2), seed=21),
        move_steps(moved, Side.BLACK, reply, (6, 2)),
        roll_steps(answered, Side.WHITE, (5, 4), seed=22),
        pause_ms=250,
    )


def winning_move() -> tuple[list, dict]:
    end = banner("¡GANAS!", "Has sacado las 15 fichas", kind="win")
    last = Position(tuple({0: 1}.get(i, 0) for i in range(24)), (0, 0), (14, 4))
    return move_steps(last, Side.WHITE, [Step(0, OFF, 1)], (1, 3), end=end, winner=Side.WHITE)


def estados(steps: list, final: dict) -> list[dict]:
    return [state for state, _ms in steps] + [final]


def test_la_escena_deja_que_el_pintor_ponga_su_exportframe_y_no_usa_azar_suelto() -> None:
    html = SCENE.read_text(encoding="utf-8")
    assert "window.exportFrame ??=" in html
    assert "toDataURL" not in html.split("window.exportFrame ??=")[0]
    assert "Math.random" not in html
    assert '<script src="../tapete.js">' in html and "Tapete.ready(meta?.skin)" in html


# -- Reserva con Pillow ------------------------------------------------------------------


@pytest.mark.lenta
async def test_sin_node_ni_chromium_el_backgammon_cae_a_pillow() -> None:
    scene = BackgammonScene(executable_path=NO_CHROMIUM, node=NO_NODE)
    steps, final = winning_move()
    try:
        still = await scene.still(META, board_state(INITIAL, turn=Side.WHITE))
        media = await scene.animate(META, steps, final)
    finally:
        await scene.close()
    assert scene.disabled
    png_image(still)
    png_image(media.png)
    gif = Image.open(io.BytesIO(media.gif))
    assert gif.format == "GIF" and gif.size == SIZE


# -- Node ---------------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scene() -> BackgammonScene:
    """Una escena con Node (y sin Chromium), compartida por el módulo."""
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    escena = BackgammonScene(executable_path=NO_CHROMIUM)
    yield escena
    await escena.close()


async def raw_patches(scene: BackgammonScene, states: list[dict], meta: dict = META) -> list:
    """Lo que devuelve la escena (recuadros), sin montar: `render` ya da los fotogramas enteros."""
    results = await scene.painter.sequence([("setup", [meta]), ("renderFrames", [states, 0])])
    assert results is not None
    return results[1]


async def assert_pegados_igual_que_enteros(
    scene: BackgammonScene, states: list[dict], every: int = 1
) -> None:
    """Cada fotograma (uno de cada `every`), pegado sobre el anterior, sale igual que entero."""
    patches = await raw_patches(scene, states)
    frames = await asyncio.to_thread(browser_scene.assemble, patches, SIZE)
    for index, state in enumerate(states):
        if index % every and index != len(states) - 1:
            continue
        whole = await raw_patches(scene, [state])
        assert whole[0]["image"].size == SIZE
        assert frames[index].tobytes() == whole[0]["image"].tobytes(), index


@needs_node
@module_loop
async def test_con_node_la_mesa_de_salida_es_un_png_y_no_esta_vacia(scene: BackgammonScene) -> None:
    state = board_state(INITIAL, turn=Side.WHITE, dice=rest_dice(Side.WHITE, (3, 1)))
    png = await scene.still(META, state)
    assert not scene.painter.disabled
    assert len(png_image(png).getcolors(1 << 20) or []) > 5000


@needs_node
@module_loop
async def test_con_node_una_jugada_ganadora_es_un_gif_con_su_png_final(
    scene: BackgammonScene,
) -> None:
    steps, final = winning_move()
    media = await scene.animate(META, steps, final)
    assert not scene.painter.disabled
    gif = Image.open(io.BytesIO(media.gif))
    assert gif.format == "GIF" and gif.size == SIZE and gif.n_frames > 30
    gif.seek(gif.n_frames - 1)
    assert difference(gif.convert("RGB"), png_image(media.png)) < 12
    assert media.seconds == sum(ms for _state, ms in steps) / 1000
    assert len(media.gif) < 8 * 1024 * 1024


@needs_node
@module_loop
async def test_con_node_un_turno_entero_encadenado_sale_como_gif(scene: BackgammonScene) -> None:
    steps, final = typical_turn()
    media = await scene.animate(META, steps, final)
    gif = Image.open(io.BytesIO(media.gif))
    assert gif.format == "GIF" and gif.size == SIZE and gif.n_frames > 80
    assert png_image(media.png).size == SIZE
    assert len(media.gif) < 8 * 1024 * 1024


@needs_node
@module_loop
async def test_con_node_mientras_vuela_una_ficha_solo_se_devuelve_su_recuadro(
    scene: BackgammonScene,
) -> None:
    """Los fotogramas del vuelo son recuadros pequeños y, pegados, iguales que enteros."""
    steps, final = move_steps(INITIAL, Side.WHITE, [Step(12, 7, 5)], (5, 3))
    states = estados(steps, final)
    patches = await raw_patches(scene, states)
    assert len(patches) == len(states)
    sizes = [patch["image"].size for patch in patches]
    assert sizes[0] == SIZE and sizes[-1] == SIZE  # el primero y el que cambia las fichas
    flying = [size for size, state in zip(sizes, states, strict=True) if state["flying"]]
    assert len(flying) > 10
    assert sum(w * h < W * H / 8 for w, h in flying) >= len(flying) - 2
    frames = await asyncio.to_thread(browser_scene.assemble, patches, SIZE)
    rendered = await scene.painter.render(META, states)
    assert rendered is not None
    for mine, theirs in zip(frames, rendered, strict=True):
        assert mine.tobytes() == theirs["image"].tobytes()
    # Pegado sobre el anterior sale igual que pintando ese fotograma entero.
    await assert_pegados_igual_que_enteros(scene, states)


@needs_node
@module_loop
async def test_con_node_si_solo_ruedan_los_dados_o_gira_el_cubo_los_recuadros_son_pequenos(
    scene: BackgammonScene,
) -> None:
    rolling, rest = roll_steps(INITIAL, Side.WHITE, (6, 1), seed=2)
    turning, turned = cube_steps(INITIAL, Cube(), Cube(2, Side.BLACK), turn=Side.WHITE)
    for steps, final in ((rolling, rest), (turning, turned)):
        states = estados(steps, final)
        patches = await raw_patches(scene, states)
        sizes = [patch["image"].size for patch in patches]
        assert sizes[0] == SIZE
        assert all(w * h < W * H * 0.4 for w, h in sizes[1:]), sizes
        await assert_pegados_igual_que_enteros(scene, states)


@pytest.mark.lenta
@needs_node
@module_loop
async def test_con_node_cada_fotograma_de_los_turnos_pegado_sale_igual_que_entero(
    scene: BackgammonScene,
) -> None:
    """Comer, sacar, dados, cubo, salida y victoria: ningún recuadro deja rastro."""
    play = play_of(BLOT, Side.WHITE, (3, 1), lambda p: any(s.hit for s in p))
    out = play_of(HOME, Side.WHITE, (5, 2), lambda p: any(s.target == OFF for s in p))
    drawings = [
        typical_turn(),
        move_steps(BLOT, Side.WHITE, play, (3, 1)),
        move_steps(HOME, Side.WHITE, out, (5, 2)),
        chain(
            opening_steps(INITIAL, (2, 5), seed=3),
            cube_steps(INITIAL, Cube(), Cube(2, None, True), turn=Side.BLACK),
            cube_steps(INITIAL, Cube(2, None, True), Cube(2, Side.WHITE)),
        ),
        winning_move(),
    ]
    for steps, final in drawings:
        await assert_pegados_igual_que_enteros(scene, estados(steps, final), every=3)


@needs_node
@module_loop
async def test_con_node_un_recuadro_que_toca_las_tarjetas_o_el_marco_sale_igual_que_entero(
    scene: BackgammonScene,
) -> None:
    """Una ficha que sube hasta las tarjetas (con su resplandor) y los números del marco."""
    base = board_state(INITIAL, turn=Side.WHITE, dice=rest_dice(Side.WHITE, (3, 1)))
    fly = [
        {"x": 40.0 + 60 * i, "y": 118.0 - 10 * i, "h": 44.0 - 3 * i, "side": 1} for i in range(5)
    ]
    low = [{"x": 300.0 + 30 * i, "y": 520.0, "h": 20.0 + 5 * i, "side": 2} for i in range(3)]
    states = [base, *[{**base, "flying": [f]} for f in fly + low], base]
    states.append({**base, "turn": 2})
    await assert_pegados_igual_que_enteros(scene, states)


@needs_node
@module_loop
async def test_con_node_la_mesa_pintada_vale_para_otra_partida_con_otros_nombres_y_titulo(
    scene: BackgammonScene, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nombres, título y subtítulo van con el fotograma: no repintan la mesa de madera y fieltro."""
    other = meta_state(
        Player("Ana", "Fichas negras"),
        Player("Pedro", "Contra Ana"),
        title="OTRA MESA",
        subtitle="Otra partida",
        seed=99,
    )
    state = board_state(INITIAL, turn=Side.WHITE)
    first = await raw_patches(scene, [state])
    started = time.perf_counter()
    again = await raw_patches(scene, [state], other)
    assert time.perf_counter() - started < 1
    # Cambian el título y los nombres, pero la mesa es la misma: las zonas sin texto, iguales.
    a, b = first[0]["image"].convert("RGB"), again[0]["image"].convert("RGB")
    assert ImageChops.difference(a, b).getbbox() is not None
    board = (30, 100, 700, 530)
    assert ImageChops.difference(a.crop(board), b.crop(board)).getbbox() is None


@pytest.mark.lenta
@needs_node
@module_loop
async def test_con_node_cada_estado_distinto_se_pinta_distinto(scene: BackgammonScene) -> None:
    """Turnos, dados, cubo, marcas, números, bandeja, vuelo y carteles: cada uno se ve."""
    base = board_state(INITIAL, turn=Side.WHITE, dice=rest_dice(Side.WHITE, (3, 1)))
    crowded = Position(tuple({12: 9, 5: 5, 23: -4}.get(i, 0) for i in range(24)), (2, 1), (3, 2))
    variants = {
        "turno": board_state(INITIAL, turn=Side.BLACK, dice=rest_dice(Side.WHITE, (3, 1))),
        "sin turno": board_state(INITIAL, dice=rest_dice(Side.WHITE, (3, 1))),
        "dados": board_state(INITIAL, turn=Side.WHITE, dice=rest_dice(Side.WHITE, (6, 5))),
        "dado gastado": board_state(
            INITIAL, turn=Side.WHITE, dice=rest_dice(Side.WHITE, (3, 1), 1)
        ),
        "dados negros": board_state(INITIAL, turn=Side.WHITE, dice=rest_dice(Side.BLACK, (3, 1))),
        "doble": board_state(INITIAL, turn=Side.WHITE, dice=rest_dice(Side.WHITE, (3, 3))),
        "cubo 2": board_state(INITIAL, turn=Side.WHITE, cube=Cube(2, Side.WHITE)),
        "cubo 4": board_state(INITIAL, turn=Side.WHITE, cube=Cube(4, Side.WHITE)),
        "cubo de las negras": board_state(INITIAL, turn=Side.WHITE, cube=Cube(2, Side.BLACK)),
        "cubo ofrecido": board_state(INITIAL, turn=Side.WHITE, cube=Cube(2, None, True)),
        "marca de un punto": board_state(INITIAL, turn=Side.WHITE, marks=[5]),
        "marca de la barra": board_state(INITIAL, turn=Side.WHITE, marks=[BAR]),
        "marca de la bandeja": board_state(INITIAL, turn=Side.WHITE, marks=[OFF]),
        "números de las negras": board_state(INITIAL, turn=Side.WHITE, labels=Side.BLACK),
        "montones, barra y bandeja": board_state(crowded, turn=Side.WHITE),
        "ficha en el aire": board_state(
            INITIAL, turn=Side.WHITE, flying=[{"x": 300.0, "y": 200.0, "h": 30.0, "side": 1}]
        ),
        "cubo girando": board_state(INITIAL, turn=Side.WHITE)
        | {"cube": {**board_state(INITIAL)["cube"], "flip": 1.0, "lift": 1.0}},
    }
    for kind in ("win", "lose", "draw", "time"):
        variants[f"cartel {kind}"] = board_state(
            INITIAL, end={**banner("TÍTULO", "Sub", kind=kind), "a": 1.0}
        )
    variants["confeti"] = board_state(
        INITIAL,
        end={**banner("¡GANAS!", "x", kind="win"), "a": 1.0},
        confetti_t=0.5,
        winner=Side.WHITE,
    )
    patches = await scene.painter.render(META, [base, *variants.values()])
    assert patches is not None
    images = [png_image(patch_png(patch)) for patch in patches]
    for name, image in zip(variants, images[1:], strict=True):
        assert ImageChops.difference(images[0], image).getbbox() is not None, name
    # Y los que son grandes (carteles, bandeja llena, montones) se ven a ojo.
    for name in ("cartel win", "cartel lose", "cartel time", "montones, barra y bandeja"):
        assert difference(images[0], images[1 + list(variants).index(name)]) > 0.5, name
    # Todos distintos entre sí.
    seen: list[tuple[str, Image.Image]] = []
    for name, image in zip(variants, images[1:], strict=True):
        for other_name, other in seen:
            assert ImageChops.difference(image, other).getbbox() is not None, (name, other_name)
        seen.append((name, image))


@needs_node
@module_loop
async def test_con_node_cada_color_de_ficha_y_de_dado_se_pinta_de_su_color(
    scene: BackgammonScene,
) -> None:
    state = board_state(INITIAL, turn=Side.WHITE)
    image = png_image(await scene.still(META, state))
    # Las blancas del punto 13 (arriba a la izquierda) son claras y las negras del 6... oscuras.
    x, y = stack_xy(12, 2, 5)
    white = image.getpixel((round(x), round(y)))
    x, y = stack_xy(11, 2, 5)
    black = image.getpixel((round(x), round(y)))
    assert sum(white) / 3 > 150 and sum(black) / 3 < 110


@pytest.mark.lenta
@needs_node
@module_loop
async def test_con_node_la_skin_cambia_el_fieltro_y_al_volver_a_la_de_serie_sale_igual(
    scene: BackgammonScene,
) -> None:
    state = board_state(INITIAL, turn=Side.WHITE)
    plain = await scene.painter.render(META, [state])
    red_meta = {**META, "skin": RED}
    gold_meta = {**META, "skin": GOLD}
    red = await scene.painter.render(red_meta, [state])
    gold = await scene.painter.render(gold_meta, [state])
    again = await scene.painter.render(META, [state])
    assert plain and red and gold and again
    felt = (
        GEOMETRY["bar"][1] + 20,
        GEOMETRY["mid"] - 4,
    )  # tapete entre dos fichas, junto a la barra
    pixels = [patches[0]["image"].convert("RGB").getpixel(felt) for patches in (plain, red, gold)]
    assert pixels[0][1] > pixels[0][0] and pixels[1][0] > pixels[1][1]  # verde y rojo
    assert difference(plain[0]["image"].convert("RGB"), red[0]["image"].convert("RGB")) > 2
    assert difference(red[0]["image"].convert("RGB"), gold[0]["image"].convert("RGB")) > 2
    assert plain[0]["image"].tobytes() == again[0]["image"].tobytes()


@pytest.mark.lenta
@needs_node
@module_loop
async def test_con_node_el_mismo_dibujo_dos_veces_da_los_mismos_bytes(
    scene: BackgammonScene,
) -> None:
    steps, final = typical_turn()
    states = estados(steps, final)[:70]
    first = await scene.painter.render(META, states, procs=1)
    second = await scene.painter.render(META, states, procs=1)
    assert first is not None and second is not None
    for a, b in zip(first, second, strict=True):
        assert a["image"].tobytes() == b["image"].tobytes()


@needs_node
@module_loop
async def test_la_escena_pone_cada_ficha_donde_la_pone_python(scene: BackgammonScene) -> None:
    """Los montones, la barra y la bandeja salen de la misma cuenta en Python y en la escena."""
    geom = json.dumps(GEOMETRY)
    for index in (0, 5, 11, 12, 17, 23):
        assert await scene.painter.evaluate(f"slotAt({geom}, 'point', [{index}])") == pytest.approx(
            point_x(index)
        )
        for count in (1, 4, 5, 6, 9, 15):
            for k in {0, count - 1}:
                got = await scene.painter.evaluate(
                    f"slotAt({geom}, 'stack', [{index}, {k}, {count}])"
                )
                assert got == pytest.approx(list(stack_xy(index, k, count)))
    for side in (Side.WHITE, Side.BLACK):
        for count in (1, 3, 4, 5, 9):
            got = await scene.painter.evaluate(
                f"slotAt({geom}, 'bar', [{side.value}, {count - 1}, {count}])"
            )
            assert got == pytest.approx(list(bar_xy(side, count - 1, count)))
        for k in (0, 7, 14):
            got = await scene.painter.evaluate(f"slotAt({geom}, 'off', [{side.value}, {k}])")
            assert got == pytest.approx(list(off_xy(side, k)))


@pytest.mark.lenta
@needs_node
@module_loop
async def test_el_turno_encadenado_repartido_entre_procesos_pinta_lo_mismo_que_de_un_tiron(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cada proceso empieza con su `setup` y su primer fotograma entero, y sale idéntico.

    Un dibujo largo (varios turnos seguidos, con cubo y cartel) con los procesos por
    defecto: se cuentan los `setup` para comprobar que de verdad se ha repartido.
    """
    steps, final = typical_turn()
    cube, end = cube_steps(INITIAL, Cube(), Cube(2, Side.WHITE), turn=Side.BLACK), winning_move()
    opening = opening_steps(INITIAL, (2, 5), seed=3)
    long_steps, long_final = chain((steps, final), cube, opening, end, pause_ms=300)
    states = estados(long_steps, long_final)
    assert len(states) > 200
    node = own_node()
    sharing = BackgammonScene(executable_path=NO_CHROMIUM, node=node)
    try:
        # Abre los procesos de refuerzo: sin ellos, el primero lo pintaría todo.
        assert await sharing.painter.render(META, states[:8]) is not None
        pool = node_scene._pool(node)
        deadline = time.monotonic() + 20
        while len([w for w in pool.workers if w.alive and not w.busy]) < 3:
            assert time.monotonic() < deadline, "No se han abierto los procesos de refuerzo"
            await asyncio.sleep(0.05)
        whole = await sharing.painter.sequence([("setup", [META]), ("renderFrames", [states, 0])])
        assert whole is not None
        setups: list[str] = []
        call = node_scene._Process.call

        async def counted(self: node_scene._Process, request: dict, **kw: bool):  # noqa: ANN202
            setups.extend(["setup"] if request["call"] == "setup" else [])
            return await call(self, request, **kw)

        monkeypatch.setattr(node_scene._Process, "call", counted)
        split = await sharing.painter.render(META, states)
        assert split is not None
        # Cada tramo empieza con su `setup`: con uno solo no se habría repartido.
        assert len(setups) > 1, "El dibujo no se ha repartido"
        whole_frames = browser_scene.assemble(whole[1], SIZE)
        assert len(split) == len(whole_frames) == len(states)
        for a, b in zip(split, whole_frames, strict=True):
            assert a["image"].tobytes() == b.tobytes()
    finally:
        await sharing.close()
        await node_scene.shutdown()


@needs_node
@module_loop
async def test_node_y_chromium_pintan_lo_mismo(scene: BackgammonScene) -> None:
    """Mismo motor (Skia): solo cambia algún borde de letra, no colores ni posiciones."""
    if not LOCAL_CHROMIUM.exists():
        pytest.skip("No hay Chromium en esta máquina")
    browser = BrowserScene(
        SCENE, name="chromium", ready="loadFonts()", executable_path=str(LOCAL_CHROMIUM)
    )
    steps, final = winning_move()
    cube = cube_steps(INITIAL, Cube(), Cube(2, Side.BLACK), turn=Side.WHITE)
    roll = roll_steps(INITIAL, Side.WHITE, (5, 3), seed=9)
    states = estados(*chain((steps, final), cube, roll))
    meta = {**META, "skin": GOLD}
    try:
        from_browser = await browser.render(meta, states)
    finally:
        await browser.close()
    if from_browser is None:
        pytest.skip("Chromium no arranca en esta máquina")
    from_node = await scene.painter.render(meta, states)
    assert from_node is not None
    a, b = await asyncio.gather(
        asyncio.to_thread(browser_scene.assemble, from_browser, SIZE),
        asyncio.to_thread(browser_scene.assemble, from_node, SIZE),
    )
    for x, y in zip(a, b, strict=True):
        assert difference(x.convert("RGB"), y.convert("RGB")) < 4
