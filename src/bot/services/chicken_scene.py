"""El Pollo con canvas, al estilo de las demás mesas del casino (Node, Chromium o Pillow).

La escena (`assets/pollo/escena.html`) pinta la mesa de tapete verde con el
título dorado, la carretera dentro de un marco (fichas de casino con el
multiplicador de cada carril, coches vistos desde arriba, vallas, la meta con
su trofeo) y el panel lateral con lo que hay en juego y los próximos carriles.

Python decide todo lo que se ve: la animación es la misma línea de tiempo que
usa el dibujo de Pillow (`chicken_render.timeline`, `resting_scene` y
`start_scene`), y aquí solo se traduce cada `Scene` a lo que lee la escena
(`frame_state`) y se añade el cartel del final (`banner_for`).

La misma escena se pinta de tres maneras, de mejor a peor:

1. **Node con Skia** (`bot.services.node_scene`): sin navegador y con los
   fotogramas en RGBA crudo, sin pasar por PNG.
2. **Chromium** (`bot.services.browser_scene`), si no hay Node o falla.
3. **Pillow** (`ChickenRenderer`, el dibujo de antes, a 640×320), si tampoco
   hay navegador.

El primer fallo de cada uno se avisa en el log y desde entonces se usa el
siguiente. El juego nunca se queda sin imagen.

Cada fotograma se pinta entero a partir de su estado (la cámara se mueve casi
siempre), así que los fotogramas se reparten entre dos procesos.
"""

from __future__ import annotations

import asyncio
import io
import random
from pathlib import Path
from typing import Any

from PIL import Image

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene
from bot.services.chicken import (
    ChickenGame,
    Difficulty,
    Status,
    format_multiplier,
    multiplier_cents,
    payout,
    short_multiplier,
)
from bot.services.chicken_render import (
    BARRIER_Y,
    CAR_COLORS,
    FINAL_FRAME_MS,
    FINISH,
    GROUND_Y,
    LANE,
    SIDEWALK,
    VEHICLES,
    ChickenRenderer,
    Media,
    Scene,
    _car_color,
    _vehicle_index,
    lane_x,
    resting_scene,
    start_scene,
    timeline,
    world_width,
)
from bot.services.economy import format_amount
from bot.services.node_scene import NodeScene, patch_png
from bot.utils.gif import local_palette_gif

SCENE = Path(__file__).resolve().parent.parent / "assets" / "pollo" / "escena.html"

#: Tamaño de la mesa (el de las demás escenas del casino).
W, H = 640, 360
#: Lo que se ve de la carretera, en unidades del mundo (marco de 426 px a escala 0,86).
VIEW_W = 426 / 0.86
#: Dónde va el pollo dentro del marco: algo a la izquierda, para ver lo que viene.
LEAD = 180.0
#: y del mundo hasta la que se pinta el asfalto (el marco llega a ~351).
BOTTOM = 352

__all__ = ["CAR_COLORS", "ChickenScene", "Media", "banner_for", "frame_state", "meta_state"]


def _chicken_x(scene: Scene) -> float:
    lanes = scene.difficulty.lanes
    if scene.chicken_at <= lanes:
        return lane_x(scene.chicken_at)
    return lane_x(lanes) + (scene.chicken_at - lanes) * LANE


def meta_state(difficulty: Difficulty, *, stake: int, seed: int) -> dict[str, Any]:
    """Lo fijo de una partida: carriles, fichas, vehículos y textos."""
    vehicles = []
    for lane in range(1, difficulty.lanes + 1):
        kind, _name, length, width = VEHICLES[_vehicle_index(seed, lane)]
        color = (250, 250, 250) if kind == "taxi" else _car_color(seed, lane)
        vehicles.append({"kind": kind, "len": length, "wid": width, "color": list(color)})
    survive = round((1 - float(difficulty.hit)) * 100)
    return {
        "seed": seed,
        "difficulty": difficulty.name,
        "road": difficulty.road,
        "lanes": difficulty.lanes,
        "sidewalk": SIDEWALK,
        "lane": LANE,
        "finish": FINISH,
        "world": world_width(difficulty),
        "ground": GROUND_Y,
        "barrier": BARRIER_Y,
        "bottom": BOTTOM,
        "cents": [
            short_multiplier(multiplier_cents(difficulty, lane))
            for lane in range(1, difficulty.lanes + 1)
        ],
        "vehicles": vehicles,
        # Los coches parados detrás de las vallas: los mismos que pinta Pillow.
        "parked": [
            lane
            for lane in range(1, difficulty.lanes + 1)
            if random.Random(seed + lane).random() < 0.55
        ],
        "stake": format_amount(stake),
        "survive": f"{survive} %",
    }


def _level(scene: Scene) -> int:
    """Carriles que cuenta el marcador: los del multiplicador que enseña."""
    difficulty = scene.difficulty
    for lane in range(difficulty.lanes, 0, -1):
        if multiplier_cents(difficulty, lane) <= scene.hud_cents:
            return lane
    return 0


def frame_state(scene: Scene, banner: dict[str, str] | None = None) -> dict[str, Any]:
    """Un fotograma tal como lo lee la escena."""
    difficulty = scene.difficulty
    x = _chicken_x(scene)
    cam = max(0.0, min(x - LEAD, world_width(difficulty) - VIEW_W))
    dead = scene.splat
    level = _level(scene)
    value = None
    if dead:
        value = "Perdido"
    elif scene.hud_value is not None:
        value = format_amount(scene.hud_value)
    elif level:
        # Durante el GIF, lo que valdría cobrar en el carril que enseña el marcador.
        value = format_amount(payout(scene.stake, difficulty, level))
    return {
        "cam": round(cam, 2),
        "at": scene.chicken_at,
        "x": round(x + scene.shake, 2),
        "y": round(GROUND_Y + 6 - scene.hop * 46, 2),
        "hop": scene.hop,
        "pose": scene.pose,
        "fenced": scene.fenced,
        "glowLane": scene.glow_lane,
        "glow": scene.glow,
        "headlights": scene.headlights,
        "cars": [{"lane": c.lane, "y": c.y_front, "braking": c.braking} for c in scene.cars],
        "fall": list(scene.falling_fence) if scene.falling_fence else None,
        "skid": scene.skid,
        "impact": scene.impact,
        "splat": scene.splat,
        "ghost": scene.ghost_lane,
        "celebrate": scene.celebrate,
        "plaf": scene.plaf,
        "hud": {
            "level": level,
            "mult": format_multiplier(scene.hud_cents),
            "value": value,
            "note": scene.hud_note,
            "dead": dead,
            "playing": not dead and not scene.celebrate,
        },
        "banner": banner,
    }


def banner_for(game: ChickenGame) -> dict[str, str] | None:
    """El cartel del final, o `None` si la partida sigue."""
    if game.playing:
        return None
    if game.status is Status.SPLAT:
        return {
            "kind": "splat",
            "title": "¡ATROPELLADO!",
            "sub": f"Carril {game.crossed + 1} · pierdes {format_amount(game.stake)}",
        }
    sub = f"{format_multiplier(game.cents)} · {format_amount(game.payout)}"
    if game.finished_road:
        return {"kind": "meta", "title": "¡META!", "sub": sub}
    return {"kind": "cash", "title": "¡COBRADO!", "sub": sub}


def encode(frames: list[Image.Image], durations: list[int]) -> Media:
    """GIF (el último fotograma, quieto) y PNG del último fotograma."""
    seconds = sum(durations) / 1000
    gif = local_palette_gif(frames, [*durations, FINAL_FRAME_MS])
    png = io.BytesIO()
    frames[-1].save(png, format="PNG", compress_level=6)
    return Media(gif=gif, png=png.getvalue(), seconds=seconds)


class ChickenScene:
    """Dibuja el Pollo con Node, si no con Chromium y, si no, con Pillow (`fallback`).

    Mismas funciones que `ChickenRenderer`, pero asíncronas: el dibujo de
    Pillow va en un hilo.

    Args:
        fallback: El dibujo de Pillow.
        executable_path: Chromium concreto (si no, el que instaló Playwright).
        node: Node concreto (si no, el del PATH).
    """

    def __init__(
        self,
        fallback: ChickenRenderer | None = None,
        *,
        executable_path: str | None = None,
        node: str | None = None,
    ) -> None:
        self.fallback = fallback or ChickenRenderer()
        self.painter = NodeScene(SCENE, name="El pintor del pollo", node=node)
        self.browser = BrowserScene(
            SCENE,
            name="El navegador del pollo",
            ready="loadFonts()",
            executable_path=executable_path,
        )

    @property
    def disabled(self) -> bool:
        """Si Node y el navegador fallaron y ya solo se dibuja con Pillow."""
        return self.painter.disabled and self.browser.disabled

    async def close(self) -> None:
        """Cierra Node y el navegador (al apagar el bot)."""
        await self.painter.close()
        await self.browser.close()

    async def _frames(
        self, meta: dict[str, Any], states: list[dict[str, Any]]
    ) -> list[Image.Image] | None:
        """Los fotogramas enteros; `None` si no hay Node ni navegador."""
        patches = await self.painter.render(meta, states)
        if patches is None:
            patches = await self.browser.render(meta, states)
        if patches is None:
            return None
        return await asyncio.to_thread(browser_scene.assemble, patches, (W, H))

    async def _still(self, meta: dict[str, Any], state: dict[str, Any]) -> bytes | None:
        patches = await self.painter.render(meta, [state])
        if patches is None:
            patches = await self.browser.render(meta, [state])
        if patches is None:
            return None
        return await asyncio.to_thread(patch_png, patches[0])

    async def start(self, difficulty: Difficulty, *, stake: int, seed: int) -> bytes:
        """PNG antes de jugar: el pollo en la acera."""
        meta = meta_state(difficulty, stake=stake, seed=seed)
        png = await self._still(meta, frame_state(start_scene(difficulty, stake=stake, seed=seed)))
        if png is None:
            return await asyncio.to_thread(self.fallback.start, difficulty, stake=stake, seed=seed)
        return png

    async def board(self, game: ChickenGame, *, seed: int, note: str = "") -> bytes:
        """PNG del estado actual de la partida (o de cómo acabó)."""
        meta = meta_state(game.difficulty, stake=game.stake, seed=seed)
        state = frame_state(resting_scene(game, seed=seed, note=note), banner_for(game))
        png = await self._still(meta, state)
        if png is None:
            return await asyncio.to_thread(self.fallback.board, game, seed=seed, note=note)
        return png

    async def hops(self, game: ChickenGame, *, start: int, seed: int, note: str = "") -> Media:
        """GIF de los carriles cruzados desde `start` y PNG del final (ver `timeline`).

        Raises:
            ValueError: Si no hay ningún carril que enseñar.
        """
        steps = timeline(game, start=start, seed=seed)
        meta = meta_state(game.difficulty, stake=game.stake, seed=seed)
        states = [frame_state(scene) for scene, _ms in steps]
        states.append(frame_state(resting_scene(game, seed=seed, note=note), banner_for(game)))
        frames = await self._frames(meta, states)
        if frames is None:
            return await asyncio.to_thread(
                self.fallback.hops, game, start=start, seed=seed, note=note
            )
        durations = [ms for _scene, ms in steps]
        return await asyncio.to_thread(encode, frames, durations)
