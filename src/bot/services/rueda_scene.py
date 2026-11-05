"""La Rueda con canvas: en Node, si no en Chromium y, si no, con Pillow.

La escena (`assets/rueda/escena.html`) pinta la rueda de feria con sus porciones,
el aro de bombillas, la lengüeta de la aguja, el expositor de la semana y el cartel
del premio. Python decide todo lo que se ve (`bot.services.rueda_render`,
`spin_states`, `board_state` y `result_state`): la escena solo pinta, y el dibujo
de Pillow (`RuedaRenderer`) lee los mismos estados.

La misma escena se pinta de tres maneras, de mejor a peor:

1. **Node con Skia** (`bot.services.node_scene`): sin navegador y con los
   fotogramas en RGBA crudo, sin pasar por PNG. Lo normal.
2. **Chromium** (`bot.services.browser_scene`), si no hay Node o falla.
3. **Pillow** (`RuedaRenderer`), si tampoco hay navegador.

El primer fallo de cada uno se avisa en el log y desde entonces se usa el
siguiente. La rueda nunca se queda sin imagen.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from PIL import Image

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene
from bot.services.node_scene import NodeScene, patch_png
from bot.services.rueda_render import H, W, Media, RuedaRenderer, encode

SCENE = Path(__file__).resolve().parent.parent / "assets" / "rueda" / "escena.html"


def assemble(patches: list[dict[str, Any]]) -> list[Image.Image]:
    """Fotogramas enteros a partir de los recuadros de la escena (`browser_scene.assemble`)."""
    return browser_scene.assemble(patches, (W, H))


class RuedaScene:
    """Dibuja la Rueda con Node, si no con Chromium y, si no, con Pillow (`fallback`).

    Args:
        fallback: El dibujo de Pillow.
        executable_path: Chromium concreto (si no, el que instaló Playwright).
        node: Node concreto (si no, el del PATH).
    """

    def __init__(
        self,
        fallback: RuedaRenderer | None = None,
        *,
        executable_path: str | None = None,
        node: str | None = None,
    ) -> None:
        self.fallback = fallback or RuedaRenderer()
        self.painter = NodeScene(SCENE, name="El pintor de la Rueda", node=node)
        self.browser = BrowserScene(
            SCENE,
            name="El navegador de la Rueda",
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
    ) -> list[dict[str, Any]] | None:
        """Recuadros de cada fotograma; `None` si no hay Node ni navegador."""
        patches = await self.painter.render(meta, states)
        if patches is None:
            patches = await self.browser.render(meta, states)
        return patches

    async def board(self, meta: dict[str, Any], state: dict[str, Any]) -> bytes:
        """PNG de la rueda quieta: antes de girar o con el premio ya cantado."""
        patches = await self._frames(meta, [state])
        if patches is None:
            return await asyncio.to_thread(self.fallback.board, meta, state)
        return await asyncio.to_thread(patch_png, patches[0])

    async def spin(
        self, meta: dict[str, Any], states: list[dict[str, Any]], durations: list[int]
    ) -> Media:
        """GIF del giro y PNG del final.

        Los fotogramas de Node llegan ya enteros y codificándose para el GIF: se pasan
        tal cual a `encode`, en el mismo orden, para no perder lo adelantado.
        """
        patches = await self._frames(meta, states)
        if patches is None:
            return await asyncio.to_thread(self.fallback.spin, meta, states, durations)
        return await asyncio.to_thread(lambda: encode(assemble(patches), durations))
