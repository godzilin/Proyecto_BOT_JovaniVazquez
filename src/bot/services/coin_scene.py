"""Cara o cruz con canvas: en Node, si no en Chromium y, si no, con Pillow.

La escena (`assets/moneda/escena.html`) pinta la moneda bimetálica con sus
relieves, el estriado del canto, los brillos que giran con ella, la estela del
vuelo y los carteles. Python decide todo lo que se ve
(`bot.services.coin_render`, `board_state` y `toss_states`): la escena solo
pinta, y el dibujo de Pillow (`CoinRenderer`) lee los mismos datos.

La misma escena se pinta de tres maneras, de mejor a peor:

1. **Node con Skia** (`bot.services.node_scene`): sin navegador y con los
   fotogramas en RGBA crudo, sin pasar por PNG.
2. **Chromium** (`bot.services.browser_scene`), si no hay Node o falla.
3. **Pillow** (`CoinRenderer`), si tampoco hay navegador.

El primer fallo de cada uno se avisa en el log y desde entonces se usa el
siguiente. El juego nunca se queda sin imagen.

Coste de un lanzamiento: ~50-80 fotogramas en ~0,5-1 s de navegador y
~0,3-0,5 s de montar el GIF en un hilo. El GIF pesa ~300-600 KB.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any

from PIL import Image

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene, Patch, png_bytes
from bot.services.coin import CoinGame, Outcome, Side
from bot.services.coin_render import (
    CoinRenderer,
    H,
    Media,
    W,
    board_state,
    encode,
    meta_state,
    toss_states,
)
from bot.services.node_scene import NodeScene

SCENE = Path(__file__).resolve().parent.parent / "assets" / "moneda" / "escena.html"


def assemble(patches: list[dict[str, Any]]) -> list[Image.Image]:
    """Fotogramas enteros a partir de los recuadros de la escena (`browser_scene.assemble`)."""
    return browser_scene.assemble(patches, (W, H))


def patch_png(patch: Patch) -> bytes:
    """El PNG de un recuadro, venga de Chromium (ya en PNG) o de Node (imagen abierta)."""
    if "image" not in patch:
        return png_bytes(patch)
    buffer = io.BytesIO()
    patch["image"].save(buffer, format="PNG")
    return buffer.getvalue()


class CoinScene:
    """Dibuja la moneda con Node, si no con Chromium y, si no, con Pillow (`fallback`).

    Args:
        fallback: El dibujo de Pillow.
        executable_path: Chromium concreto (si no, el que instaló Playwright).
        node: Node concreto (si no, el del PATH).
    """

    def __init__(
        self,
        fallback: CoinRenderer | None = None,
        *,
        executable_path: str | None = None,
        node: str | None = None,
    ) -> None:
        self.fallback = fallback or CoinRenderer()
        self.painter = NodeScene(SCENE, name="El pintor de la moneda", node=node)
        self.browser = BrowserScene(
            SCENE,
            name="El navegador de la moneda",
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
        """Recuadros de cada fotograma (ver `assemble`); `None` si no hay Node ni navegador."""
        patches = await self.painter.render(meta, states)
        if patches is None:
            patches = await self.browser.render(meta, states)
        return patches

    async def board(self, game: CoinGame | None, *, stake: int, face: Side | Outcome) -> bytes:
        """PNG de la mesa quieta: al abrir, al cobrar y al repintar."""
        patches = await self._frames(meta_state(stake), [board_state(game, face=face)])
        if patches is None:
            return await asyncio.to_thread(self.fallback.board, game, stake=stake, face=face)
        return patch_png(patches[0])

    async def toss(self, game: CoinGame, *, start: Side, seed: int) -> Media:
        """GIF del último lanzamiento de `game` y PNG del final."""
        states = toss_states(game, start=start, seed=seed)
        patches = await self._frames(meta_state(game.stake), states)
        if patches is None:
            return await asyncio.to_thread(self.fallback.toss, game, start=start, seed=seed)
        return await asyncio.to_thread(lambda: encode(assemble(patches)))
