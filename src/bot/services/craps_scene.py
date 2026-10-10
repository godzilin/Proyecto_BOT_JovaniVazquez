"""Los dados con canvas: en Node, si no en Chromium y, si no, con Pillow.

La escena (`assets/dados/escena.html`) pinta el tapete en perspectiva con su
pelusa y sus casillas, la pared de pirámides, los dados rojos translúcidos
con los puntos taladrados, las fichas, el disco del punto, el panel y los
carteles. Python decide todo lo que se ve (`bot.services.craps_render`,
`board_state` y `throw_states`): la escena solo pinta, y el dibujo de
Pillow (`CrapsRenderer`) lee los mismos datos.

La misma escena se pinta de tres maneras, de mejor a peor:

1. **Node con Skia** (`bot.services.node_scene`): sin navegador y con los
   fotogramas en RGBA crudo, sin pasar por PNG.
2. **Chromium** (`bot.services.browser_scene`), si no hay Node o falla.
3. **Pillow** (`CrapsRenderer`), si tampoco hay navegador.

El primer fallo de cada uno se avisa en el log y desde entonces se usa el
siguiente. El juego nunca se queda sin imagen.

Coste de una tirada: ~50 fotogramas en ~1 s de pintado y ~0,4 s de montar
el GIF en un hilo.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from PIL import Image

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene
from bot.services.craps_render import (
    CrapsRenderer,
    DieRest,
    H,
    Media,
    Table,
    W,
    board_state,
    encode,
    meta_state,
    throw_states,
)
from bot.services.node_scene import NodeScene, patch_png

SCENE = Path(__file__).resolve().parent.parent / "assets" / "dados" / "escena.html"


def assemble(patches: list[dict[str, Any]]) -> list[Image.Image]:
    """Fotogramas enteros a partir de los recuadros de la escena (`browser_scene.assemble`)."""
    return browser_scene.assemble(patches, (W, H))


class CrapsScene:
    """Dibuja los dados con Node, si no con Chromium y, si no, con Pillow (`fallback`).

    Args:
        fallback: El dibujo de Pillow.
        executable_path: Chromium concreto (si no, el que instaló Playwright).
        node: Node concreto (si no, el del PATH).
    """

    def __init__(
        self,
        fallback: CrapsRenderer | None = None,
        *,
        executable_path: str | None = None,
        node: str | None = None,
    ) -> None:
        self.fallback = fallback or CrapsRenderer()
        self.painter = NodeScene(SCENE, name="El pintor de los dados", node=node)
        self.browser = BrowserScene(
            SCENE,
            name="El navegador de los dados",
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

    async def _frames(self, states: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        """Recuadros de cada fotograma (ver `assemble`); `None` si no hay Node ni navegador."""
        patches = await self.painter.render(meta_state(), states)
        if patches is None:
            patches = await self.browser.render(meta_state(), states)
        return patches

    async def board(self, table: Table, rest: tuple[DieRest, DieRest]) -> bytes:
        """PNG de la mesa quieta: al abrir, al poner Odds y al repintar."""
        patches = await self._frames([board_state(table, rest)])
        if patches is None:
            return await asyncio.to_thread(self.fallback.board, table, rest)
        return patch_png(patches[0])

    async def throw(self, table: Table, *, seed: int) -> Media:
        """GIF de la última tirada de `table.game` y PNG del final."""
        states, rest = throw_states(table, seed=seed)
        patches = await self._frames(states)
        if patches is None:
            return await asyncio.to_thread(self.fallback.throw, table, seed=seed)
        return await asyncio.to_thread(lambda: encode(assemble(patches), rest))
