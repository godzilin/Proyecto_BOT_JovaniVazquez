"""Pruebas de bot.services.gallos_scene: el reñidero con canvas (Node, Chromium o Pillow).

Las de Node se saltan si no hay `node` o falta `@napi-rs/canvas` (`npm ci` en la
raíz).
"""

from __future__ import annotations

import io
import random
import re

import pytest
import pytest_asyncio
from PIL import Image, ImageChops, ImageStat
from test_node_scene import needs_node, node_ready

from bot.services import skins
from bot.services.gallos import HOUSE, Corner, simulate
from bot.services.gallos_render import (
    POSES,
    Fighter,
    final_state,
    intro_state,
    meta_state,
    timeline,
)
from bot.services.gallos_scene import OUT_SIZE, SCENE, GallosScene
from bot.services.node_scene import patch_png

module_loop = pytest.mark.asyncio(loop_scope="module")
NO_CHROMIUM = "/no/existe/chromium"
NO_NODE = "/no/existe/node"
SIZE = (640, 360)
NAMES = {Corner.RED: "Rojo", Corner.BLUE: "Azul"}


def meta_of(red: dict[str, object], blue: dict[str, object] | None = None) -> dict:
    return meta_state(
        Fighter("Tu gallo", "diego", red, "×1,94"),
        Fighter("Gallo de Temu", "un almacén", blue or skins.params("gallos"), "×2,10"),
        title="PELEAS DE GALLOS",
        subtitle="Reñidero de Yapland",
        seed=7,
    )


def rival_look(rival) -> dict[str, object]:
    return skins.params("gallos") | dict(rival.look)


def png_image(png: bytes, size: tuple[int, int] = SIZE) -> Image.Image:
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG" and image.size == size
    return image.convert("RGB")


def difference(a: Image.Image, b: Image.Image) -> float:
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


# -- Reserva con Pillow ------------------------------------------------------------------


async def test_sin_node_ni_chromium_los_gallos_caen_a_pillow() -> None:
    scene = GallosScene(executable_path=NO_CHROMIUM, node=NO_NODE)
    meta = meta_of(skins.params("gallos"))
    fight = simulate(Corner.RED, random.Random(1))
    try:
        intro = await scene.still(meta, intro_state())
        media = await scene.fight(meta, fight, NAMES, seed=1)
    finally:
        await scene.close()
    assert scene.disabled
    png_image(intro)
    png_image(media.png)
    gif = Image.open(io.BytesIO(media.gif))
    assert gif.format == "GIF" and gif.size == SIZE


# -- La escena ----------------------------------------------------------------------------


def test_la_escena_sabe_pintar_todos_los_complementos_y_poses() -> None:
    """Cada `accessory` de las skins y los rivales y cada pose tienen su dibujo."""
    html = SCENE.read_text(encoding="utf8")
    block = html[html.index("const ACC = {") : html.index("function drawHeadParts")]
    painted = set(re.findall(r"^  ([\wñ]+): \{", block, re.MULTILINE))
    wanted = {str(s.params.get("accessory", "")) for s in skins.skins_of("gallos")}
    wanted |= {str(r.look.get("accessory", "")) for r in HOUSE}
    wanted |= {"caja", "sombrero", "estrellas", "rosa", "patillas", "boina", "gorra", "camara"}
    wanted |= {"escudo", "bigote", "casco", "pañuelo", "corona", "calavera", "gorro"}
    wanted.discard("")
    # El pañuelo no es de la cabeza: se pinta en el cuello, junto a la esclavina.
    assert wanted - painted <= {"pañuelo"}
    assert 'K.acc === "pañuelo"' in html
    for pose in set(POSES) - {"idle"}:
        assert f'case "{pose}"' in html, pose


# -- Node ---------------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scene() -> GallosScene:
    """Una escena de los gallos con Node (y sin Chromium), compartida por el módulo."""
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    escena = GallosScene(executable_path=NO_CHROMIUM)
    yield escena
    await escena.close()


@needs_node
@module_loop
async def test_con_node_la_presentacion_es_un_png_de_la_mesa_y_no_esta_vacia(
    scene: GallosScene,
) -> None:
    banner = {"kind": "intro", "title": "¡A PELEAR!", "sub": ""}
    png = await scene.still(meta_of(skins.params("gallos")), intro_state(banner=banner))
    assert not scene.painter.disabled
    image = png_image(png)
    assert len(image.getcolors(1 << 20) or []) > 1000


@pytest.mark.lenta
@needs_node
@module_loop
async def test_con_node_la_pelea_es_un_gif_de_muchos_fotogramas(scene: GallosScene) -> None:
    fight = simulate(Corner.RED, random.Random(3))
    meta = meta_of(skins.params("gallos", skins.SKIN_BY_KEY["gallo_moncloa"]))
    media = await scene.fight(meta, fight, NAMES, seed=5)
    assert not scene.painter.disabled
    assert not scene.browser.disabled  # Chromium ni se probó
    gif = Image.open(io.BytesIO(media.gif))
    # La pelea sale a 2/3 de la escena (`OUT_SIZE`): pesa ~40 % menos.
    assert gif.format == "GIF" and gif.size == OUT_SIZE and gif.n_frames > 50
    gif.seek(gif.n_frames - 1)
    last = gif.convert("RGB")
    assert difference(last, png_image(media.png, OUT_SIZE)) < 12
    steps = timeline(fight, NAMES, seed=5)
    assert media.seconds == sum(ms for _state, ms in steps) / 1000


@needs_node
@module_loop
async def test_con_node_cada_final_y_cada_pose_se_pinta(scene: GallosScene) -> None:
    """KO, huida y puntos; y todas las poses de los gallos, sin error y distintas entre sí."""
    meta = meta_of(skins.params("gallos"))
    base = final_state(simulate(Corner.RED, random.Random(3)), NAMES)
    states = []
    for pose in POSES:
        state = {**base, "banner": None, "fx": []}
        state["red"] = {**base["red"], "pose": pose, "t": 0.5, "x": 214.0, "y": 292.0}
        state["blue"] = {**base["blue"], "pose": "idle", "x": 426.0, "y": 292.0}
        states.append(state)
    for kind in ("ko", "fled", "points", "round", "intro"):
        states.append({**base, "banner": {"kind": kind, "title": "TÍTULO", "sub": "GANA ROJO"}})
    patches = await scene.painter.render(meta, states)
    assert patches is not None and len(patches) == len(states)
    images = [png_image(patch_png(p)) for p in patches]
    poses = images[: len(POSES)]
    for i, first in enumerate(poses):
        for other in poses[i + 1 :]:
            assert difference(first, other) > 0.05


@pytest.mark.lenta
@needs_node
@module_loop
async def test_con_node_cada_skin_y_cada_rival_se_pinta(scene: GallosScene) -> None:
    """Todos los gallos de la tienda y de la casa pintan sin error, y cada uno distinto."""
    looks = [skins.params("gallos", s) for s in skins.skins_of("gallos")]
    looks += [rival_look(r) for r in HOUSE]
    images = []
    for i, look in enumerate(looks):
        # Cada uno sale en la esquina roja contra el siguiente.
        meta = meta_of(look, looks[(i + 1) % len(looks)])
        images.append(png_image(await scene.still(meta, intro_state())))
    assert not scene.painter.disabled
    assert len(images) == len(looks) > 20
    for first, second in zip(images, images[1:], strict=False):
        assert difference(first, second) > 0.1


@needs_node
@module_loop
async def test_con_node_el_gallo_de_serie_se_distingue_de_una_skin(scene: GallosScene) -> None:
    default = png_image(await scene.still(meta_of(skins.params("gallos")), intro_state()))
    white = skins.params("gallos", skins.SKIN_BY_KEY["gallo_blanco"])
    other = png_image(await scene.still(meta_of(white), intro_state()))
    assert difference(default, other) > 0.3
