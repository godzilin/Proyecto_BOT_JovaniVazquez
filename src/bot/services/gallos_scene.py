"""Las peleas de gallos con canvas: en Node, si no en Chromium y, si no, con Pillow.

La escena (`assets/gallos/escena.html`) pinta el reñidero (arena, valla, el
público, focos y el marcador con las barras de vida) y los dos gallos con los
colores de su skin, en la pose que toque, con sus plumas, onomatopeyas y
estrellitas. Python decide todo lo que se ve (`bot.services.gallos_render`):
la escena solo pinta.

La misma escena se pinta de tres maneras, de mejor a peor:

1. **Node con Skia** (`bot.services.node_scene`): sin navegador y con los
   fotogramas en RGBA crudo, sin pasar por PNG.
2. **Chromium** (`bot.services.browser_scene`), si no hay Node o falla.
3. **Pillow** (`GallosRenderer`), si tampoco hay navegador: una mesa quieta.

El primer fallo de cada uno se avisa en el log y desde entonces se usa el
siguiente. El juego nunca se queda sin imagen.

Cada fotograma se pinta entero a partir de su estado (los gallos se mueven por
todo el reñidero y el público salta), así que se reparten entre los procesos de
Node libres (`bot.services.node_scene`).
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PIL import Image

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene
from bot.services.gallos import Corner, Fight
from bot.services.gallos_render import (
    GallosRenderer,
    H,
    Media,
    W,
    encode,
    final_state,
    timeline,
)
from bot.services.node_scene import NodeScene, patch_png
from bot.utils.gif import QUANTIZE_THREADS

#: Tamaño del GIF de la pelea: 3/4 de la escena. Pesa menos y en Discord no se nota (el
#: embed lo enseña más pequeño de todos modos).
OUT_SIZE = (W * 2 // 3, H * 2 // 3)
SCENE = Path(__file__).resolve().parent.parent / "assets" / "gallos" / "escena.html"


def _shrink(frame: Image.Image) -> Image.Image:
    return frame.resize(OUT_SIZE, Image.Resampling.BILINEAR)


class GallosScene:
    """Dibuja las peleas con Node, si no con Chromium y, si no, con Pillow (`fallback`).

    Args:
        fallback: El dibujo de Pillow.
        executable_path: Chromium concreto (si no, el que instaló Playwright).
        node: Node concreto (si no, el del PATH).
    """

    def __init__(
        self,
        fallback: GallosRenderer | None = None,
        *,
        executable_path: str | None = None,
        node: str | None = None,
    ) -> None:
        self.fallback = fallback or GallosRenderer()
        self.painter = NodeScene(SCENE, name="El pintor de los gallos", node=node)
        self.browser = BrowserScene(
            SCENE,
            name="El navegador de los gallos",
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

    async def _patches(
        self, meta: dict[str, Any], states: list[dict[str, Any]]
    ) -> list[dict[str, Any]] | None:
        patches = await self.painter.render(meta, states)
        if patches is None:
            patches = await self.browser.render(meta, states)
        return patches

    async def still(self, meta: dict[str, Any], state: dict[str, Any]) -> bytes:
        """PNG de un estado quieto (la presentación o el final)."""
        patches = await self._patches(meta, [state])
        if patches is None:
            return await asyncio.to_thread(self.fallback.still, meta, state)
        return await asyncio.to_thread(patch_png, patches[0])

    async def fight(
        self, meta: dict[str, Any], fight: Fight, names: dict[Corner, str], *, seed: int
    ) -> Media:
        """GIF de la pelea entera y PNG del final."""
        steps = timeline(fight, names, seed=seed)
        states = [state for state, _ms in steps]
        states.append(final_state(fight, names))
        patches = await self._patches(meta, states)
        if patches is None:
            return await asyncio.to_thread(self.fallback.fight, meta, fight, names)

        def build() -> Media:
            frames: list[Image.Image] = browser_scene.assemble(patches, (W, H))
            # Pillow suelta el GIL al reducir: en varios hilos, como la codificación del GIF.
            with ThreadPoolExecutor(QUANTIZE_THREADS) as pool:
                small = list(pool.map(_shrink, frames))
            return encode(small, [ms for _state, ms in steps])

        return await asyncio.to_thread(build)
