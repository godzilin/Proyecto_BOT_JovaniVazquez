"""El retrato de `mascota` (`assets/mascotas/escena.html` y `ropa.js`) y su clase (`pet_scene`).

Comprueba que la escena sabe pintar todas las formas de ropa y de fondo del registro, que cada
especie, cada raza, cada cara, cada prenda y cada fondo salen en un PNG del tamaño (con las
hojas de `docs/muestras_mascotas.py`), que una forma desconocida no rompe el pintor, que Node y
Chromium pintan lo mismo y que repartir entre procesos da los mismos bytes que uno solo. Sin
Node ni Chromium, `still` devuelve `None` y el panel sale en texto.
"""

from __future__ import annotations

import importlib.util
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import pytest_asyncio
from PIL import Image, ImageChops, ImageStat
from test_node_scene import LOCAL_CHROMIUM, needs_node, node_ready

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene
from bot.services.pet_scene import SCENE, H, PetScene, W, pet_entry, portrait_state
from bot.services.pet_wardrobe import HOME, OUTFIT_BY_KEY, SHAPES, Slot
from bot.services.pets_looks import LOOKS

module_loop = pytest.mark.asyncio(loop_scope="module")
NO_CHROMIUM = "/no/existe/chromium"
NO_NODE = "/no/existe/node"
META = {"w": W, "h": H}
ROPA = SCENE.parent / "ropa.js"
SCRIPT = Path(__file__).resolve().parents[2] / "docs" / "muestras_mascotas.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("muestras_mascotas", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


muestras = _load()


def png_image(png: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG" and image.size == (W, H)
    return image.convert("RGB")


def difference(a: Image.Image, b: Image.Image) -> float:
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


def few() -> list[dict]:
    """Unos retratos variados: ropa delante y detrás, fondo, visita, álbum y tarta."""
    look = {
        OUTFIT_BY_KEY[k].slot: OUTFIT_BY_KEY[k]
        for k in ("chistera", "gafas_pasta", "bufanda", "capa", "fondo_canteras")
    }
    cat = pet_entry("gato", LOOKS["gato"].breed("siames"), look)
    dog = pet_entry("perro", LOOKS["perro"].breed("beagle"), {})
    return [
        portrait_state([cat], backdrop=look[Slot.BACKDROP], mood="contenta", seed=1),
        portrait_state([dog], backdrop=OUTFIT_BY_KEY["fondo_hongkong"], mood="dormida", seed=2),
        portrait_state([cat, dog], mood="fiesta", cake=True, seed=3),
        portrait_state([dog], caption="Nivel 6 · 12/10/2026", seed=4),
        portrait_state([pet_entry("pedrusco", LOOKS["pedrusco"].default, look)], seed=5),
        portrait_state([pet_entry("pez", LOOKS["pez"].default, look)], seed=6),
    ]


@pytest.mark.skipif(shutil.which("node") is None, reason="No hay Node")
def test_la_escena_sabe_pintar_todas_las_formas_del_registro() -> None:
    script = (
        "const vm=require('vm');const c={window:{}};c.window=c;vm.createContext(c);"
        f"vm.runInContext(require('fs').readFileSync({json.dumps(str(ROPA))},'utf8'),c);"
        "process.stdout.write(JSON.stringify(c.Ropa.SHAPES));"
    )
    known = json.loads(
        subprocess.run(["node", "-e", script], capture_output=True, check=True).stdout
    )
    for slot, shapes in SHAPES.items():
        missing = shapes - set(known[slot.value])
        assert not missing, f"{slot.value}: la escena no sabe pintar {sorted(missing)}"
    assert HOME.shape in known["fondo"]


async def test_sin_node_ni_chromium_no_se_pinta() -> None:
    scene = PetScene(executable_path=NO_CHROMIUM, node=NO_NODE)
    try:
        assert await scene.paint([]) == []
        assert await scene.still(few()[0]) is None
    finally:
        await scene.close()
    assert scene.disabled


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scene() -> PetScene:
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    escena = PetScene(executable_path=NO_CHROMIUM)
    yield escena
    await escena.close()


@pytest.mark.lenta
@needs_node
@module_loop
async def test_cada_especie_raza_cara_prenda_y_fondo_sale_en_un_png(scene: PetScene) -> None:
    items = (
        muestras._breed_items(None)
        + muestras._mood_items()
        + muestras._extra_items()
        + muestras._wear_items()
        + muestras._full_items()
        + muestras._backdrop_items()
    )
    states = [state for _label, state in items]
    pngs = await scene.paint(states)
    assert pngs is not None and not scene.painter.disabled and len(pngs) == len(states)
    for (label, _state), png in zip(items, pngs, strict=True):
        image = png_image(png)
        assert len(image.getcolors(1 << 20) or []) > 500, label
    # Cada raza se ve distinta de la de serie de su especie.
    by_label = dict(zip((label for label, _ in items), pngs, strict=True))
    for species, look in LOOKS.items():
        base = png_image(by_label[f"{species} · {look.default.name}"])
        for breed in look.breeds[1:]:
            other = png_image(by_label[f"{species} · {breed.name}"])
            assert difference(base, other) > 0.05, f"{species} · {breed.name}"


@needs_node
@module_loop
async def test_una_forma_desconocida_no_rompe_el_pintor(scene: PetScene) -> None:
    rare = {"shape": "inventada", "colors": ["#123456"], "pattern": "raro"}
    state = portrait_state([pet_entry("gato", LOOKS["gato"].default, {})], seed=1)
    state["pets"][0]["outfit"] = {s: rare for s in ("cabeza", "ojos", "cuello", "cuerpo")}
    state["pets"][0]["plan"] = "inventado"
    state["backdrop"] = rare
    png = await scene.still(state)
    assert png is not None and not scene.painter.disabled
    png_image(png)


@needs_node
@module_loop
async def test_repartido_pinta_lo_mismo_que_un_solo_proceso(scene: PetScene) -> None:
    states = few()
    alone = await scene.painter.render(META, states, procs=1, gif=False)
    spread = await scene.painter.render(META, states, gif=False)
    assert alone is not None and spread is not None
    for a, b in zip(alone, spread, strict=True):
        assert a["image"].tobytes() == b["image"].tobytes()


@needs_node
@module_loop
async def test_node_y_chromium_pintan_lo_mismo(scene: PetScene) -> None:
    if not LOCAL_CHROMIUM.exists():
        pytest.skip("No hay Chromium en esta máquina")
    browser = BrowserScene(
        SCENE, name="chromium", ready="loadFonts()", executable_path=str(LOCAL_CHROMIUM)
    )
    states = few()
    try:
        from_browser = await browser.render(META, states)
    finally:
        await browser.close()
    if from_browser is None:
        pytest.skip("Chromium no arranca en esta máquina")
    from_node = await scene.painter.render(META, states, gif=False)
    assert from_node is not None
    for i, (patch_b, patch_n) in enumerate(zip(from_browser, from_node, strict=True)):
        x, y = (browser_scene.assemble([p], (W, H))[0].convert("RGB") for p in (patch_b, patch_n))
        assert difference(x, y) < 4, i
