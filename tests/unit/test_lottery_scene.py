"""La escena de la lotería (`assets/loteria/escena.html`) y su clase (`bot.services.lottery_scene`).

Pinta el rasca y los sorteos (GIF y PNG final) y las imágenes quietas: décimo, boleto y cartel.
Las de Node se saltan si no hay `node` o falta `@napi-rs/canvas` (`npm ci` en la raíz); la
comparación con Chromium, también si no hay navegador. Sin ninguno de los dos, la clase devuelve
`None` y el cog sigue en texto.
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import io
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import pytest_asyncio
from PIL import Image, ImageChops, ImageStat
from test_node_scene import LOCAL_CHROMIUM, needs_node, node_ready, open_helpers

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene
from bot.services.lottery_render import DRAW_SIZE, SCRATCH_SIZE
from bot.services.lottery_scene import SCENE, LotteryScene
from bot.services.node_scene import NodeScene

module_loop = pytest.mark.asyncio(loop_scope="module")
NO_CHROMIUM = "/no/existe/chromium"
NO_NODE = "/no/existe/node"
SCRIPT = Path(__file__).resolve().parents[2] / "docs" / "muestras_loteria.py"
#: Lo que más puede pesar un rasca y un sorteo (el GIF sube a Discord con cada uno).
MAX_SCRATCH_BYTES = 600 * 1024
MAX_DRAW_BYTES = 1536 * 1024


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("muestras_loteria", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


muestras = _load()
ANIMATIONS = muestras.animations()
SCRATCHES = {name: a for name, a in ANIMATIONS.items() if name.startswith("rasca_")}
DRAWS = {name: a for name, a in ANIMATIONS.items() if name.startswith("sorteo_")}
STILLS = muestras.stills()


def size_of(meta: dict[str, Any]) -> tuple[int, int]:
    return meta["w"], meta["h"]


def still_meta(state: dict[str, Any]) -> dict[str, Any]:
    """Lo que `LotteryScene.still` pasa como `meta`: el modo y el tamaño."""
    return {"mode": state["mode"], "w": state["w"], "h": state["h"]}


def max_bytes(name: str) -> int:
    return MAX_SCRATCH_BYTES if name.startswith("rasca_") else MAX_DRAW_BYTES


def difference(a: Image.Image, b: Image.Image) -> float:
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


def last_frame(gif: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(gif))
    image.seek(image.n_frames - 1)
    return image.convert("RGB")


def test_la_escena_deja_que_el_pintor_ponga_su_exportframe() -> None:
    html = SCENE.read_text(encoding="utf-8")
    assert "window.exportFrame ??=" in html
    assert "toDataURL" not in html.split("window.exportFrame ??=")[0]
    assert "Math.random(" not in html


def test_las_muestras_cubren_cada_rasca_y_cada_sorteo() -> None:
    assert len(SCRATCHES) == 6
    assert {meta["game"] for meta, _, _ in SCRATCHES.values()} == {"x10", "7ymedia"}
    assert {states[-1]["banner"]["tone"] for _, states, _ in SCRATCHES.values()} == {"win", "lose"}
    for meta, states, _ in SCRATCHES.values():
        assert size_of(meta) == SCRATCH_SIZE
        assert len(meta["cells"]) == 9 and len(meta["top"]) == 4
        assert all(len(state["p"]) == 9 for state in states)
    # El rasca sin premio con dos casillas del premio más alto brilla en otro tono.
    tones = {name: states[-1]["glowTone"] for name, (_, states, _) in SCRATCHES.items()}
    assert tones["rasca_x10_casi"] == "near" and tones["rasca_x10_premio"] == "win"
    assert len(DRAWS) == 7
    assert {meta["kind"] for meta, _, _ in DRAWS.values()} == {"lotto", "gordo", "euro", "nacional"}
    assert {states[-1]["banner"]["tone"] for _, states, _ in DRAWS.values()} == {"win", "carry"}
    for meta, _, _ in DRAWS.values():
        assert size_of(meta) == DRAW_SIZE
    assert {len(meta["rows"]) for meta, _, _ in DRAWS.values() if meta["kind"] == "nacional"} == {
        2,
        3,
    }


def test_las_muestras_cubren_cada_imagen_quieta() -> None:
    assert {state["mode"] for state in STILLS.values()} == {"decimo", "boleto", "cartel"}
    tickets = [s for s in STILLS.values() if s["mode"] == "decimo"]
    slips = [s for s in STILLS.values() if s["mode"] == "boleto"]
    assert {s["game"] for s in tickets} == {"jueves", "sabado", "navidad", "nino"}
    assert {s["quantity"] for s in tickets} == {1, 10}
    assert {(s["stamp"] or {}).get("tone") for s in tickets} == {None, "win", "lose"}
    assert {s["game"] for s in slips} == {"primitiva", "bonoloto", "gordo", "euromillones"}
    assert any(s["result"] for s in slips) and any(not s["result"] for s in slips)
    assert any(len(s["rows"]) > 5 for s in slips)  # dos columnas
    assert len({s["h"] for s in slips}) > 3


async def test_sin_node_ni_chromium_no_se_pinta() -> None:
    scene = LotteryScene(executable_path=NO_CHROMIUM, node=NO_NODE)
    try:
        for name in ("rasca_x10_premio", "sorteo_primitiva_bote", "sorteo_navidad"):
            meta, states, durations = ANIMATIONS[name]
            assert await scene.animate(meta, states, durations) is None
        for state in STILLS.values():
            assert await scene.still(state) is None
    finally:
        await scene.close()
    assert scene.disabled


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scene() -> LotteryScene:
    """La lotería con Node (y sin Chromium), compartida por el módulo."""
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    escena = LotteryScene(executable_path=NO_CHROMIUM)
    yield escena
    await escena.close()


async def final_picture(scene: LotteryScene, meta: dict, state: dict) -> Image.Image:
    """El último fotograma de una muestra, pintado solo, como una imagen entera."""
    patches = await scene.painter.render(meta, [state], gif=False)
    assert patches is not None
    return patches[0]["image"].convert("RGB")


@needs_node
@module_loop
@pytest.mark.parametrize("name", sorted(ANIMATIONS))
async def test_con_node_cada_animacion_es_un_gif_que_acaba_en_su_png(
    scene: LotteryScene, name: str
) -> None:
    meta, states, durations = ANIMATIONS[name]
    media = await scene.animate(meta, states, durations)
    assert media is not None and not scene.painter.disabled
    assert media.seconds > 1.5
    gif = Image.open(io.BytesIO(media.gif))
    assert gif.format == "GIF" and gif.size == size_of(meta) and gif.n_frames > 10
    assert len(media.gif) < max_bytes(name)
    png = Image.open(io.BytesIO(media.png)).convert("RGB")
    assert png.size == size_of(meta)
    # El último fotograma del GIF es el PNG que queda (salvo la paleta del GIF).
    assert difference(last_frame(media.gif), png) < 4


@needs_node
@module_loop
async def test_con_node_cada_imagen_quieta_es_un_png_de_su_tamano_y_siempre_el_mismo(
    scene: LotteryScene,
) -> None:
    pngs = {}
    for name, state in STILLS.items():
        png = await scene.still(state)
        assert png is not None and not scene.painter.disabled, name
        image = Image.open(io.BytesIO(png))
        assert image.format == "PNG" and image.size == (state["w"], state["h"]), name
        assert len(image.convert("RGB").getcolors(1 << 20) or []) > 1000, name
        assert len(png) < 512 * 1024, name
        # Pintar antes del clic y al pulsar tienen que dar los mismos bytes.
        assert await scene.still(copy.deepcopy(state)) == png, name
        pngs[name] = png
    assert len(set(pngs.values())) == len(pngs)


@needs_node
@module_loop
@pytest.mark.parametrize("name", ["decimo_premiado", "decimo_sin_premio", "decimo_sabado_billete"])
async def test_el_sello_y_la_chapa_no_tapan_el_numero(scene: LotteryScene, name: str) -> None:
    state = STILLS[name]
    meta = still_meta(state)
    bare = {**state, "stamp": None, "quantity": 1}
    with_stamp = await final_picture(scene, meta, state)
    without = await final_picture(scene, meta, bare)
    assert with_stamp.tobytes() != without.tobytes()
    # El recuadro del número (a la derecha de la ilustración) queda igual.
    box = (284, 92, 704, 198)
    assert with_stamp.crop(box).tobytes() == without.crop(box).tobytes()


@needs_node
@module_loop
async def test_el_boleto_sorteado_enseña_los_aciertos_llenos_y_el_resto_apagado(
    scene: LotteryScene,
) -> None:
    state = STILLS["boleto_primitiva_sorteado"]
    meta = still_meta(state)
    drawn = await final_picture(scene, meta, state)
    plain = await final_picture(
        scene,
        meta,
        {
            **state,
            "result": None,
            "rows": [{**row, "hit": None, "xhit": None, "tag": ""} for row in state["rows"]],
        },
    )
    assert drawn.tobytes() != plain.tobytes()
    # Primera apuesta (A): el 07 acierta (bola llena, oscura) y el 10 no (bola apagada, clara).
    row_y = 118 + 23

    def brightness(x: int) -> float:
        return sum(ImageStat.Stat(drawn.crop((x - 10, row_y - 13, x + 10, row_y - 7))).mean) / 3

    assert brightness(82) < 120 and brightness(120) > 200
    # Lo que paga la fila sale a la derecha, en verde.
    tag = drawn.crop((480, row_y - 10, 620, row_y + 10)).getcolors(10000) or []
    assert any(g > r + 40 and g > b + 20 for _, (r, g, b) in tag)


@needs_node
@module_loop
async def test_con_node_cada_resultado_se_ve_distinto_y_pinta_lo_mismo_dos_veces(
    scene: LotteryScene,
) -> None:
    names = (
        "rasca_x10_premio",
        "rasca_x10_nada",
        "rasca_7m_premio",
        "sorteo_gordo",
        "sorteo_jueves",
    )
    results = {}
    for name in names:
        meta, states, durations = ANIMATIONS[name]
        media = await scene.animate(meta, states, durations)
        assert media is not None
        results[name] = media
    pngs = [media.png for media in results.values()]
    assert len(set(pngs)) == len(pngs)
    for name in ("rasca_x10_premio", "sorteo_primitiva_bote"):
        meta, states, durations = ANIMATIONS[name]
        first = results.get(name) or await scene.animate(meta, states, durations)
        again = await scene.animate(meta, states, durations)
        assert first is not None and again is not None
        # Pintar antes del clic y al pulsar tienen que dar los mismos bytes.
        assert again.gif == first.gif and again.png == first.png


@needs_node
@module_loop
@pytest.mark.parametrize("name", ["rasca_x10_premio", "sorteo_primitiva_bote", "sorteo_navidad"])
async def test_con_node_solo_viaja_lo_que_cambia(scene: LotteryScene, name: str) -> None:
    meta, states, _ = ANIMATIONS[name]
    result = await scene.painter.sequence([("setup", [meta]), ("renderFrames", [states, 0])])
    assert result is not None
    patches = result[1]
    assert len(patches) == len(states)
    w, h = size_of(meta)
    sizes = [patch["image"].size for patch in patches]
    assert sizes[0] == (w, h) and (patches[0]["x"], patches[0]["y"]) == (0, 0)
    assert sum(pw * ph for pw, ph in sizes) < len(states) * w * h * 0.5


@needs_node
@module_loop
@pytest.mark.parametrize(
    "name", ["rasca_x10_premio", "rasca_7m_nada", "sorteo_primitiva_bote", "sorteo_navidad"]
)
async def test_repartido_pinta_lo_mismo_que_de_un_tiron(scene: LotteryScene, name: str) -> None:
    painter = scene.painter
    original = painter._request
    calls: list[str] = []

    def spy(call: str, args: list | None = None, **extra: object) -> dict:
        calls.append(call)
        return original(call, args, **extra)

    meta, states, _ = ANIMATIONS[name]
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
    a = browser_scene.assemble(split, size_of(meta))
    b = browser_scene.assemble(whole, size_of(meta))
    assert [x.tobytes() for x in a] == [y.tobytes() for y in b]


@needs_node
@module_loop
async def test_el_casi_y_el_premio_pintan_halos_distintos(scene: LotteryScene) -> None:
    meta, states, _ = SCRATCHES["rasca_x10_casi"]
    near = states[-1]
    assert near["glowTone"] == "near" and len(near["glow"]) == 2
    win = {**near, "glowTone": "win"}
    near_image = await final_picture(scene, meta, near)
    win_image = await final_picture(scene, meta, win)
    assert near_image.tobytes() != win_image.tobytes()
    # El aro de la casilla que brilla (en su borde de arriba): rojo en el casi, dorado en el premio.
    cell = near["glow"][0]
    x, y = 266 + cell % 3 * 120 + 12, 25 + cell // 3 * 120 - 1
    r, g, _ = near_image.getpixel((x, y))
    assert r > 200 and g < 100
    r, g, _ = win_image.getpixel((x, y))
    assert r > 200 and g > 170


@needs_node
@module_loop
@pytest.mark.parametrize(
    ("name", "from_y", "x0"),
    [
        ("rasca_x10_casi", 0, 266),
        ("rasca_7m_nada", 0, 266),
        ("sorteo_primitiva_bote", 299, 0),
        ("sorteo_bonoloto", 299, 0),
        ("sorteo_jueves", 155, 0),
        ("sorteo_navidad", 155, 0),
    ],
)
async def test_el_cartel_no_tapa_la_combinacion(
    scene: LotteryScene, name: str, from_y: int, x0: int
) -> None:
    """Con y sin cartel, la rejilla (rasca) o la combinación (sorteo) salen igual.

    Se empieza unos píxeles más allá del recuadro del cartel: el filtro al reducir se asoma uno.
    """
    meta, states, _ = ANIMATIONS[name]
    final = states[-1]
    assert final["banner"] is not None
    bare = {**final, "banner": None, "bannerA": 0.0}
    with_banner = await final_picture(scene, meta, final)
    without = await final_picture(scene, meta, bare)
    w, h = size_of(meta)
    area = (x0, from_y, w, h)
    assert with_banner.crop(area).tobytes() == without.crop(area).tobytes()
    assert with_banner.tobytes() != without.tobytes()


#: Datos raros (textos vacíos, `null` donde se espera texto, índices fuera de rango, una fila de
#: más): la escena tiene que pintarlos, porque una excepción apaga el pintor hasta reiniciar.
POOL = {
    "mode": "sorteo",
    "kind": "lotto",
    "w": 720,
    "h": 405,
    "game": "primitiva",
    "title": "LA PRIMITIVA",
    "date": "hoy",
    "seed": 5,
    "slots": [{"kind": "main"}] * 6 + [{"kind": "comp", "label": "C"}],
}
NACIONAL = {
    "mode": "sorteo",
    "kind": "nacional",
    "w": 720,
    "h": 405,
    "game": "navidad",
    "title": "NAVIDAD",
    "date": "hoy",
    "seed": 5,
    "rows": [{"label": "A", "amount": "1 Y$"}] * 3,
}
RASCA = {
    "mode": "rasca",
    "w": 640,
    "h": 400,
    "game": "x10",
    "title": "T",
    "price": "2 Y$",
    "top": ["1 Y$"] * 4,
    "cells": ["1 Y$"] * 9,
    "serial": "1",
    "seed": 3,
}
BIG_BANNER = {
    "tone": "win",
    "title": "🎉 ¡BOTE! 🎉" * 6,
    "amount": "9" * 40 + " Y$",
    "sub": "a · " * 30,
}
WEIRD: dict[str, tuple[dict, list]] = {
    "bote vacío": (POOL, [{}, None, 5, "x", []]),
    "bote sin datos": (
        {
            "mode": "sorteo",
            "game": "nuevo",
            "title": None,
            "date": None,
            "seed": None,
            "slots": None,
        },
        [{"shown": [None, "1", 2]}],
    ),
    "bote con huecos raros": (
        {**POOL, "slots": [None, {"kind": "raro"}, {"kind": "star", "label": None}, {}]},
        [{"shown": ["01", 2, None, "", {"a": 1}, "99", "x", "y", "z"], "sorted": True}],
    ),
    "bote con bola rara": (
        POOL,
        [
            {"fly": {"slot": 99, "v": "1", "t": 0.5}},
            {"fly": {"slot": "x", "v": None, "t": "a"}},
            {"fly": {"slot": 3, "v": None, "t": 7}},
            {"fly": 3},
            {"fly": {"slot": -1, "v": "2", "t": -2}},
        ],
    ),
    "bote con cartel raro": (
        POOL,
        [
            {
                "banner": {"tone": "otro", "title": None, "amount": None, "sub": None},
                "bannerA": 0.5,
            },
            {"banner": {}, "bannerA": "x"},
            {"banner": "texto", "bannerA": 1},
            {"banner": BIG_BANNER, "bannerA": 3},
            {"banner": {**BIG_BANNER, "tone": "carry"}, "bannerA": 0.2},
            {"banner": {"tone": "lose", "title": None}, "bannerA": 0.7, "spin": None},
        ],
    ),
    "nacional vacío": (NACIONAL, [{}, None, {"digits": None, "reintegros": None, "fly": None}]),
    "nacional sin filas": ({**NACIONAL, "rows": None}, [{"digits": [["1"]]}]),
    "nacional con una fila": (
        {**NACIONAL, "rows": [{"label": None, "amount": None}]},
        [
            {
                "digits": [[1, 2, None, "", "5", "6", "7"], "raro", None],
                "reintegros": [1, None, "2", "3", "4", "5"],
            }
        ],
    ),
    "nacional con bola rara": (
        NACIONAL,
        [
            {"fly": {"row": 5, "col": 0, "v": "1", "t": 0.5}},
            {"fly": {"row": 0, "col": 9, "v": "1", "t": 0.5}},
            {"fly": {"row": 1.5, "col": "x", "v": None, "t": None}},
            {"fly": {"row": 2, "col": 4, "v": "8", "t": 9}},
        ],
    ),
    "nacional con cartel": (
        NACIONAL,
        [
            {"banner": BIG_BANNER, "bannerA": 0.4},
            {
                "banner": {
                    "tone": "lose",
                    "title": "SIN PREMIOS",
                    "amount": "",
                    "sub": "En el servidor",
                },
                "bannerA": 1,
            },
        ],
    ),
    "tamaño raro": ({**POOL, "w": None, "h": "x"}, [{}]),
    "rasca vacío": (RASCA, [{}, None, {"p": None}, {"p": [2, -1, "x", None, 0.5]}]),
    "rasca con brillo raro": (
        RASCA,
        [
            {
                "p": [1] * 9,
                "active": 99,
                "glow": [0, 99, -1, "a", None, 2.5],
                "glowA": 5,
                "glowTone": "rara",
                "banner": {"tone": "win"},
                "bannerA": 2,
            },
            {
                "p": [0.5] * 9,
                "active": "3",
                "banner": {"tone": "lose", "title": None, "note": None},
                "bannerA": 1,
            },
        ],
    ),
    "rasca sin textos": (
        {"mode": "rasca", "game": "otro", "cells": None, "top": None, "title": None, "price": None},
        [{}],
    ),
    "rasca con pocas casillas": (
        {**RASCA, "cells": ["1 Y$", None, 7]},
        [{"p": [0.3] * 9, "active": 1}],
    ),
}


DECIMO = {"mode": "decimo", "w": 720, "h": 330}
BOLETO = {"mode": "boleto", "w": 640, "h": 406}
CARTEL = {"mode": "cartel", "w": 720, "h": 405}
LONG_OWNER = "Ana 🍀 " * 40
WEIRD_ROWS = [
    None,
    {},
    {"n": None, "x": "7"},
    {"n": ["1"] * 30, "x": [1, 2, 3, 4, 5], "hit": "x", "xhit": 5, "tag": "★" * 40},
]
WEIRD.update(
    {
        "décimo vacío": (DECIMO, [{}, None, "x", {"game": "raro", "number": None, "stamp": None}]),
        "décimo con datos raros": (
            DECIMO,
            [
                {
                    "game": "navidad",
                    "title": None,
                    "number": "1234567890123",
                    "price": 5,
                    "owner": LONG_OWNER,
                    "quantity": "x",
                    "barcode": "abc",
                    "stamp": {"tone": "win", "title": None, "amount": None, "detail": "x " * 100},
                },
                {"game": "nino", "number": "7", "quantity": 1000, "stamp": "texto", "owner": None},
                {"game": "sabado", "number": 48288, "quantity": None, "barcode": None, "stamp": {}},
            ],
        ),
        "boleto vacío": (
            BOLETO,
            [{}, None, {"rows": None, "result": "x"}, {"game": "raro", "xlabel": None}],
        ),
        "boleto con una fila": (
            {**BOLETO, "h": 222},
            [
                {
                    "game": "bonoloto",
                    "rows": [{"n": ["01", "02"], "x": ["3"], "hit": None, "xhit": None, "tag": ""}],
                }
            ],
        ),
        "boleto con diez filas": (
            BOLETO,
            [
                {
                    "game": "euromillones",
                    "xlabel": "★",
                    "owner": LONG_OWNER,
                    "title": None,
                    "jackpot": None,
                    "rows": [
                        {
                            "n": ["01", "02", "03", "04", "05"],
                            "x": ["01", "02"],
                            "hit": ["hit", "comp", "", None, "hit"],
                            "xhit": [True, False],
                            "tag": "5 + 2 ★ · 1.000.000 Y$",
                        }
                    ]
                    * 10,
                    "result": {
                        "n": ["01", "02", "03", "04", "05"],
                        "x": ["01", "02"],
                        "comp": None,
                    },
                }
            ],
        ),
        "boleto con cuarenta filas": (
            BOLETO,
            [{"game": "gordo", "rows": [{"n": ["01"], "x": ["1"], "tag": "x" * 90}] * 40}],
        ),
        "boleto diminuto": (
            {**BOLETO, "h": 100},
            [{"game": "primitiva", "rows": [{"n": ["01"]}] * 7}],
        ),
        "boleto con filas raras": (
            BOLETO,
            [
                {"rows": WEIRD_ROWS, "result": {"n": None, "x": "x", "comp": 5}},
                {"rows": WEIRD_ROWS * 3, "result": {"n": ["1"] * 20}},
            ],
        ),
        "cartel vacío": (
            CARTEL,
            [
                {},
                None,
                {"botes": None, "nacional": None},
                {"botes": [None, 5, "x"], "nacional": [{}]},
            ],
        ),
        "cartel con diez": (
            CARTEL,
            [
                {
                    "botes": [
                        {
                            "game": "otro",
                            "name": LONG_OWNER,
                            "amount": "9" * 60,
                            "when": None,
                            "grow": "★ " * 30,
                            "hot": 1,
                        }
                    ]
                    * 10,
                    "nacional": [
                        {
                            "game": "navidad",
                            "name": None,
                            "amount": None,
                            "when": None,
                            "days": "x",
                        },
                        {"game": "nino", "days": -5},
                        {"game": "jueves", "days": 1e9},
                        {},
                        {},
                        {},
                        {},
                        {},
                        {},
                        {},
                    ],
                }
            ],
        ),
    }
)


@needs_node
@module_loop
@pytest.mark.parametrize("name", sorted(WEIRD))
async def test_con_node_los_datos_raros_se_pintan_sin_apagar_el_pintor(name: str) -> None:
    meta, states = WEIRD[name]
    other = LotteryScene(executable_path=NO_CHROMIUM)
    try:
        patches = await other.painter.render(copy.deepcopy(meta), copy.deepcopy(states), procs=1)
    finally:
        await other.close()
    assert patches is not None and len(patches) == len(states)
    assert not other.painter.disabled


@needs_node
@module_loop
async def test_un_modo_que_no_existe_desactiva_el_pintor() -> None:
    other = LotteryScene(executable_path=NO_CHROMIUM)
    meta, states, durations = ANIMATIONS["rasca_x10_premio"]
    try:
        assert await other.animate({**meta, "mode": "no_existe"}, states, durations) is None
    finally:
        await other.close()
    assert other.painter.disabled and other.disabled


@pytest.fixture
def chromium() -> BrowserScene:
    if not LOCAL_CHROMIUM.exists():
        pytest.skip("No hay Chromium en esta máquina")
    return BrowserScene(
        SCENE, name="chromium", ready="loadFonts()", executable_path=str(LOCAL_CHROMIUM)
    )


@needs_node
@module_loop
@pytest.mark.parametrize(
    "name", ["rasca_x10_premio", "rasca_7m_nada", "sorteo_euromillones", "sorteo_nino"]
)
async def test_node_y_chromium_pintan_lo_mismo(chromium: BrowserScene, name: str) -> None:
    """Mismo motor (Skia): solo cambia algún borde de letra, no colores ni posiciones."""
    painter = NodeScene(SCENE, name="El pintor de la lotería")
    meta, all_states, _ = ANIMATIONS[name]
    states = [*all_states[::6], all_states[-1]]
    size = size_of(meta)
    try:
        from_browser = await chromium.render(meta, states)
        if from_browser is None:
            pytest.skip("Chromium no arranca en esta máquina")
        from_node = await painter.render(meta, states)
        assert from_node is not None
        a, b = await asyncio.gather(
            asyncio.to_thread(browser_scene.assemble, from_browser, size),
            asyncio.to_thread(browser_scene.assemble, from_node, size),
        )
    finally:
        await chromium.close()
        await painter.close()
    assert len(a) == len(b) == len(states)
    for x, y in zip(a, b, strict=True):
        assert difference(x, y) < 4


@needs_node
@module_loop
@pytest.mark.parametrize(
    "name", ["decimo_premiado", "decimo_sabado_billete", "boleto_euromillones_sorteado", "cartel"]
)
async def test_node_y_chromium_pintan_lo_mismo_las_imagenes_quietas(
    chromium: BrowserScene, name: str
) -> None:
    painter = NodeScene(SCENE, name="El pintor de la lotería")
    state = STILLS[name]
    meta, size = still_meta(state), (state["w"], state["h"])
    try:
        from_browser = await chromium.render(meta, [state])
        if from_browser is None:
            pytest.skip("Chromium no arranca en esta máquina")
        from_node = await painter.render(meta, [state], gif=False)
        assert from_node is not None
        (a,), (b,) = (
            browser_scene.assemble(from_browser, size),
            browser_scene.assemble(from_node, size),
        )
    finally:
        await chromium.close()
        await painter.close()
    assert difference(a, b) < 4


@needs_node
@module_loop
@pytest.mark.parametrize("name", sorted(WEIRD))
async def test_con_chromium_los_datos_raros_tambien_se_pintan(
    chromium: BrowserScene, name: str
) -> None:
    meta, states = WEIRD[name]
    try:
        patches = await chromium.render(copy.deepcopy(meta), copy.deepcopy(states))
    finally:
        await chromium.close()
    if patches is None:
        pytest.fail("Chromium no pinta datos raros")
    assert len(patches) == len(states) and not chromium.disabled
