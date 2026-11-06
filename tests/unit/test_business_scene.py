"""La escena de los negocios (`assets/negocio/escena.html`) y su clase (`BusinessScene`).

Cada pantalla de las muestras (`docs/muestras_negocio.py`) se pinta con Node en un PNG de
1000 × 640; repartir el dibujo entre procesos no cambia un byte; Node y Chromium pintan lo mismo;
y cada clave del catálogo (productos, mejoras, emblemas, temas, oficios, franjas y averías) tiene
su dibujo.
Sin Node ni Chromium, `paint` devuelve `None` y el cog enseña el panel en texto.
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import io
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import pytest_asyncio
from PIL import Image, ImageChops, ImageStat
from test_node_scene import LOCAL_CHROMIUM, needs_node, node_ready, own_node

from bot.services import browser_scene, node_scene
from bot.services.browser_scene import BrowserScene
from bot.services.business_catalog import EMBLEMS, EQUIPMENT, FAULTS, SECTOR_BY_KEY, SECTORS
from bot.services.business_scene import SCENE, BusinessScene, H, W
from bot.services.business_staff import ROLE_BY_KEY
from bot.services.housing_catalog import THEMES
from bot.services.work_catalog import JOBS

module_loop = pytest.mark.asyncio(loop_scope="module")
NO_CHROMIUM = "/no/existe/chromium"
NO_NODE = "/no/existe/node"
META = {"w": W, "h": H}
SCRIPT = Path(__file__).resolve().parents[2] / "docs" / "muestras_negocio.py"
SLOT_KINDS = ("hecha", "jornada", "extra", "guardia", "estudio", "negocio", "libre", "noche")


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("muestras_negocio", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


samples = _load().samples


def png_image(png: bytes) -> Image.Image:
    """Abre el PNG de una pantalla y comprueba que mide 1000 × 640."""
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG" and image.size == (W, H)
    return image.convert("RGB")


def difference(a: Image.Image, b: Image.Image) -> float:
    """Diferencia media por canal (0-255)."""
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


def test_las_muestras_cubren_cada_pantalla_y_las_variantes_del_contrato() -> None:
    states = samples()
    screens = {s["screen"] for s in states.values()}
    assert screens == {
        "dia", "montar", "negocio", "almacen", "cuentas", "cliente", "cierre", "mejoras", "resenas",
        "sociedad", "plantilla", "candidatos", "cadena", "nave", "venta",
    }  # fmt: skip
    sectors = {sector.key for sector in SECTORS}
    clients = [s for s in states.values() if s["screen"] == "cliente"]
    assert {s["sector"] for s in clients} == sectors
    assert {p["key"] for p in states["montar"]["sectors"]} == sectors
    for screen in ("almacen", "mejoras"):
        assert {s["sector"] for s in states.values() if s["screen"] == screen} == sectors
    assert any(p.get("level") for s in states.values() if s["screen"] == "almacen"
               for p in s["products"])  # fmt: skip
    assert any(s.get("usual") for s in clients) and any(s.get("thief") for s in clients)
    # 9.48.0: lo de cada minijuego nuevo, con lo de siempre y sus trampas.
    by_game = {
        game: [s for s in clients if SECTOR_BY_KEY[s["sector"]].game == game]
        for game in ("brasa", "encargos", "balanza", "revision", "foto")
    }
    for game, shown in by_game.items():
        if game != "revision":  # en el móvil, lo de siempre no cambia lo que se ve
            assert {bool(s.get("usual")) for s in shown} == {True, False}, game
    assert {bool(s["dish"]) for s in by_game["brasa"]} == {True, False}
    assert {s["usual"] or s["clue"] == "" for s in by_game["encargos"]} == {False, True}
    assert {len(s["faults"]) for s in by_game["revision"]} == {1, 2, 3}
    assert {f for s in by_game["revision"] for f in s["faults"]} == {
        p.key for p in SECTOR_BY_KEY["moviles"].products
    }
    assert {s["protector"] for s in by_game["revision"]} == {True, False}
    assert {s["hair"] for s in by_game["foto"]} == {"rapado", "corto", "medio", "largo"}
    assert any(s["beard"] for s in by_game["foto"]) and any(
        s["photo"] is None for s in by_game["foto"]
    )
    assert {s.get("step") for s in clients} >= {"diagnostico", "presupuesto"}
    assert any(s["cash"] == "" for s in clients) and any(s["cash"] for s in clients)
    shops = [s for s in states.values() if s["screen"] == "negocio"]
    assert {s["mode"] for s in shops} == {"portada", "preview"}
    assert {s["open"] for s in shops} == {True, False}
    covers = [s for s in shops if s["mode"] == "portada" and "rating" in s]
    assert {s["rating"] is None for s in covers} == {True, False}
    assert {s["campaign"] is None for s in covers} == {True, False}
    assert any("terraza" in s["equipment"] for s in covers)
    assert any(not s["equipment"] for s in covers) and all(s["size"] for s in covers)
    upgrades = [s for s in states.values() if s["screen"] == "mejoras"]
    assert {s["active"] is None for s in upgrades} == {True, False}
    assert {i["owned"] for s in upgrades for i in s["items"]} == {True, False}
    reviews = [s for s in states.values() if s["screen"] == "resenas"]
    assert {bool(s["reviews"]) for s in reviews} == {True, False}
    assert {s["badge"] for s in reviews} == {True, False}
    days = [s for s in states.values() if s["screen"] == "dia"]
    assert {(s["job"] is None, s["business"] is None) for s in days} == {
        (False, False),
        (False, True),
        (True, False),
        (True, True),
    }
    assert {s["grade"] for s in states.values() if s["screen"] == "cierre"} >= {"S", "D"}
    companies = [s for s in states.values() if s["screen"] == "sociedad"]
    assert {s["mode"] for s in companies} == {"autonomo", "sl"}
    forming = [s for s in companies if s["mode"] == "autonomo"]
    assert {s["blocked"] is None for s in forming} == {True, False}
    assert {c["amount"] for s in forming for c in s["capitals"] if c["selected"]} >= {"100 Y$"}
    books = [s for s in companies if s["mode"] == "sl"]
    assert {s["last"] is None for s in books} == {True, False}
    assert any(s["warnings"] for s in books) and any(not s["warnings"] for s in books)
    assert {s["ric"]["due"] is None for s in books} == {True, False}
    assert any(s["ric"]["pending"] != s["ric"]["locked"] for s in books)
    assert any(len(s["warnings"]) == 3 and not s["zec"]["on"] for s in books)  # lo más lleno
    assert {s["zec"]["on"] for s in books} == {True, False}
    assert any(w.startswith("Invierte") for s in books for w in s["warnings"])
    teams = [s for s in states.values() if s["screen"] == "plantilla"]
    assert {len(s["staff"]) for s in teams} >= {0, 1, 6}
    cards = [e for s in teams for e in s["staff"]]
    assert {e["contract"] for e in cards} == {"indefinido", "temporal", "b"}
    assert {e["mood"] for e in cards} == {"contento", "normal", "quemado"}
    assert any("en prueba" in e["contract_name"] for e in cards)
    assert any(e["alerts"] for e in cards) and any(e["selected"] for e in cards)
    assert any(e["name"] == "Mari Carmen Bethencourt" for e in cards)
    assert any(s["debt"] for s in teams) and any(s["debt"] is None for s in teams)
    hiring = [s for s in states.values() if s["screen"] == "candidatos"]
    assert {s["offer"] is None for s in hiring} == {True, False}
    offers = [s["offer"] for s in hiring if s["offer"]]
    assert {o["black"] for o in offers} == {True, False} and any(o["bonus"] for o in offers)
    people = [c for s in hiring for c in s["candidates"]]
    assert any(c["bonus"] for c in people) and any(c["part_time"] for c in people)
    assert {s["room"] == "No queda sitio" for s in hiring} == {True, False}
    assert any(s.get("staff") for s in shops) and any(s.get("staff") is None for s in covers)
    # Fase 5: la cadena, la nave y la venta, y los retoques de las pantallas de antes.
    chains = [s for s in states.values() if s["screen"] == "cadena"]
    assert {len(s["locales"]) for s in chains} >= {1, 2, 4}
    assert {s["nave"] is None for s in chains} == {True, False}
    places = [p for s in chains for p in s["locales"]]
    assert {p["manager"] for p in places} == {True, False}
    assert {p["alert"] is None for p in places} == {True, False}
    assert any(p["selected"] for p in places) and len({p["island"] for p in places}) > 1
    assert any(len(p["name"]) == 24 for p in places)
    naves = [s for s in states.values() if s["screen"] == "nave"]
    assert {s["mode"] for s in naves} == {"alquilar", "nave"}
    stores = [s for s in naves if s["mode"] == "nave"]
    assert any(not s["stock"] and not s["coming"] for s in stores)
    assert any(s["stock"] and s["coming"] for s in stores) and any(s["debt"] for s in stores)
    quotes = [s["quote"] for s in stores if s["quote"]]
    assert any(s["quote"] is None for s in stores)
    assert {q["good"] for q in quotes} == {True, False} and any(q["blocked"] for q in quotes)
    assert any(q["aiem_rate"] != "no lleva" and not q["blocked"] for q in quotes)
    sales = [s for s in states.values() if s["screen"] == "venta"]
    assert {s["blocker"] is None for s in sales} == {True, False}
    assert any(s.get("locales") and s.get("manager") for s in covers)
    assert any(e["role"] == "gerente" for e in cards)
    assert any(o.get("role") == "gerente" for o in offers)
    assert any(s["business"] and s["business"].get("more") for s in days)
    # 9.41.0: cada puesto y cada clase de horario, las páginas, el cambio de horario pendiente
    # y los cuatro candidatos de quien tiene recursos humanos.
    assert {e["role"] for e in cards} == set(ROLE_BY_KEY)
    assert {e["horario_kind"] for e in cards} == {"completa", "partida", "seis", "media", "gerente"}
    assert {e["night"] for e in cards} == {True, False}
    assert {s["page"] is None for s in teams} == {True, False}
    assert {s["room"] for s in teams} == {True, False}
    assert any(a.startswith("Desde mañana") for e in cards for a in e["alerts"])
    assert any(len(s["candidates"]) == 4 for s in hiring)
    assert {o["role"] for o in offers} >= {"", "gerente", "limpieza", "encargado"}
    assert {o["horario_kind"] for o in offers} >= {"completa", "media", "partida", "gerente"}
    assert any(set(s["staff"]["roles"]) >= {"", "encargado", "repartidor", "gerente"}
               for s in shops if s.get("staff"))  # fmt: skip


async def test_sin_node_ni_chromium_no_se_pinta_y_el_panel_va_en_texto() -> None:
    scene = BusinessScene(executable_path=NO_CHROMIUM, node=NO_NODE)
    try:
        assert await scene.paint([]) == []
        assert await scene.paint([samples()["dia_sin_nada"]]) is None
        assert await scene.still(samples()["montar"]) is None
    finally:
        await scene.close()
    assert scene.disabled


# -- Node ---------------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scene() -> BusinessScene:
    """Una escena con Node (y sin Chromium), compartida por el módulo."""
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    escena = BusinessScene(executable_path=NO_CHROMIUM)
    yield escena
    await escena.close()


@needs_node
@module_loop
async def test_con_node_cada_pantalla_es_un_png_del_tamano_y_distinta(scene: BusinessScene) -> None:
    states = list(samples().values())
    pngs = await scene.paint(states)
    assert pngs is not None and not scene.painter.disabled and len(pngs) == len(states)
    images = [png_image(png) for png in pngs]
    for image in images:
        assert len(image.getcolors(1 << 20) or []) > 1000
    for i, first in enumerate(images):
        for other in images[i + 1 :]:
            assert difference(first, other) > 0.05
    assert (await scene.still(states[3])) == pngs[3]


@needs_node
@module_loop
async def test_el_mismo_cliente_del_taller_se_distingue_en_cada_paso(scene: BusinessScene) -> None:
    states = samples()
    first, second = states["cliente_diagnostico"], states["cliente_diagnostico_paso2"]
    assert {k: v for k, v in first.items() if k != "step"} == {
        k: v for k, v in second.items() if k != "step"
    }
    pngs = await scene.paint([first, second])
    assert pngs is not None
    a, b = map(png_image, pngs)
    # El capó abierto en la escena y la libreta del presupuesto en el panel (medido: 0,53 y 12,2).
    car, panel = (16, 92, 640, 624), (664, 92, 984, 624)
    assert difference(a.crop(car), b.crop(car)) > 0.3
    assert difference(a.crop(panel), b.crop(panel)) > 6.0


@needs_node
@module_loop
async def test_con_node_una_pantalla_desconocida_desactiva_el_pintor() -> None:
    other = BusinessScene(executable_path=NO_CHROMIUM)
    try:
        assert await other.paint([{"screen": "nada"}]) is None
    finally:
        await other.close()
    assert other.painter.disabled and other.disabled


@needs_node
@module_loop
async def test_las_pantallas_repartidas_pintan_lo_mismo_que_con_un_proceso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cada pantalla es entera: repartir el dibujo entre procesos no cambia ni un byte."""
    states = list(samples().values()) * 2
    node = own_node()
    sharing = BusinessScene(executable_path=NO_CHROMIUM, node=node)
    try:
        # Abre los procesos de refuerzo: sin ellos, el primero lo pintaría todo.
        assert await sharing.painter.render(META, states[:8], gif=False) is not None
        pool = node_scene._pool(node)
        deadline = time.monotonic() + 20
        while len([w for w in pool.workers if w.alive and not w.busy]) < 3:
            assert time.monotonic() < deadline, "No se han abierto los procesos de refuerzo"
            await asyncio.sleep(0.05)
        alone = await sharing.painter.render(META, states, procs=1, gif=False)
        assert alone is not None
        setups: list[str] = []
        call = node_scene._Process.call

        async def counted(self: node_scene._Process, request: dict, **kw: bool):  # noqa: ANN202
            setups.extend(["setup"] if request["call"] == "setup" else [])
            return await call(self, request, **kw)

        monkeypatch.setattr(node_scene._Process, "call", counted)
        split = await sharing.painter.render(META, states, gif=False)
        assert split is not None
        # Cada tramo empieza con su `setup`: con uno solo no se habría repartido.
        assert len(setups) > 1, "El dibujo no se ha repartido"
        pngs = await sharing.paint(states)
        assert pngs is not None
        assert len(split) == len(alone) == len(pngs) == len(states)
        for a, b, png in zip(split, alone, pngs, strict=True):
            assert a["image"].tobytes() == b["image"].tobytes()
            assert png_image(png).tobytes() == a["image"].convert("RGB").tobytes()
    finally:
        await sharing.close()
        await node_scene.shutdown()


# -- Cada clave del catálogo tiene su dibujo -------------------------------------------------


def catalog_states() -> list[dict[str, Any]]:
    """Una pantalla por cada producto, mejora, emblema, tema, oficio, franja y avería."""
    s = samples()
    states: list[dict[str, Any]] = []
    for sector in SECTORS:  # cada producto, en el almacén de su sector
        state = copy.deepcopy(s[f"almacen_{sector.key}"])
        state["products"] = [
            {**row, "key": p.key, "name": p.name}
            for row, p in zip(state["products"], sector.products, strict=True)
        ]
        states.append(state)
    base = s["negocio_cafeteria_centro"]
    for key, _name in EMBLEMS:
        states.append({**base, "emblem": key})
    for theme in THEMES:  # cada tema con cada sector, abierto y cerrado
        for n, sector in enumerate(SECTORS):
            states.append({**base, "theme": theme, "sector": sector.key, "open": n % 2 == 0})
        states.append({**s["cliente_caja"], "theme": theme})
    cover = s["negocio_cafeteria_crecida"]  # cada mejora, instalada en la portada y en su pantalla
    for sector in SECTORS:
        kit = [e.key for e in EQUIPMENT if sector.key in e.sectors]
        states.append({**cover, "sector": sector.key, "equipment": kit})
        upgrades = copy.deepcopy(s[f"mejoras_{sector.key}"])
        for item in upgrades["items"]:
            item.update(owned=True, book="Por amortizar: 1.000 Y$")
        states.append(upgrades)
    for job in JOBS:
        day = copy.deepcopy(s["dia_curro_y_negocio"])
        day["job"]["icon"] = job.key
        states.append(day)
    day = copy.deepcopy(s["dia_curro_y_negocio"])
    day["slots"] = [{"kind": kind} for kind in (*SLOT_KINDS, "guardia")]
    states.append(day)
    for model, (_symptoms, cue) in enumerate(FAULTS.values()):
        for step in ("diagnostico", "presupuesto"):
            car = {"color": "#2e86c1", "model": model % 4}
            states.append({**s["cliente_diagnostico"], "cue": cue, "step": step, "car": car})
    for sector in SECTORS:  # cada producto en una comanda
        states.append(
            {**s["cliente_comandas"], "order": [{"key": p.key, "n": 2} for p in sector.products]}
        )
    return states


@needs_node
@module_loop
async def test_cada_clave_del_catalogo_se_pinta_sin_error(scene: BusinessScene) -> None:
    keys = await scene.painter.evaluate(
        "({product: Object.keys(PRODUCT), emblem: Object.keys(EMBLEM), job: Object.keys(JOB_ICON),"
        " theme: Object.keys(SKY), slot: Object.keys(SLOT), wall: Object.keys(WALLS),"
        " equip: Object.keys(EQUIP), sector: Object.keys(GAME)})"
    )
    assert isinstance(keys, dict)
    assert set(keys["product"]) >= {p.key for sector in SECTORS for p in sector.products}
    assert set(keys["emblem"]) >= {key for key, _name in EMBLEMS}
    assert set(keys["equip"]) >= {e.key for e in EQUIPMENT}
    assert set(keys["sector"]) >= {sector.key for sector in SECTORS}
    assert set(keys["job"]) >= {job.key for job in JOBS}
    assert set(keys["theme"]) >= set(THEMES) and set(keys["wall"]) >= set(THEMES)
    assert set(keys["slot"]) == set(SLOT_KINDS)
    states = catalog_states()
    pngs = await scene.paint(states)
    assert pngs is not None and not scene.painter.disabled and len(pngs) == len(states)
    for png in pngs:
        png_image(png)


# -- Node contra Chromium --------------------------------------------------------------------


@needs_node
@module_loop
async def test_node_y_chromium_pintan_lo_mismo(scene: BusinessScene) -> None:
    """Mismo motor (Skia): solo cambia algún borde de letra, no colores ni posiciones."""
    if not LOCAL_CHROMIUM.exists():
        pytest.skip("No hay Chromium en esta máquina")
    browser = BrowserScene(
        SCENE, name="chromium", ready="loadFonts()", executable_path=str(LOCAL_CHROMIUM)
    )
    states = list(samples().values())
    try:
        from_browser = await browser.render(META, states)
    finally:
        await browser.close()
    if from_browser is None:
        pytest.skip("Chromium no arranca en esta máquina")
    from_node = await scene.painter.render(META, states, gif=False)
    assert from_node is not None
    for name, patch_b, patch_n in zip(samples(), from_browser, from_node, strict=True):
        x, y = (browser_scene.assemble([p], (W, H))[0].convert("RGB") for p in (patch_b, patch_n))
        assert difference(x, y) < 4, name
