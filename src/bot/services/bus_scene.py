"""Autobús dibujado con canvas en Chromium, y con Pillow si no hay navegador.

La escena (`assets/autobus/escena.html`) pinta el tapete, la carretera con sus
cinco paradas, el autobús, las cartas (con su giro y su sombra), el humo, las
chispas y los carteles. Python decide todo lo que se ve
(`bot.services.bus_render`, `board_state` y `reveal_states`): la escena solo
pinta, y el dibujo de Pillow (`BusRenderer`) lee los mismos datos.

**Si no hay navegador, se usa Pillow.** El primer fallo de Chromium se avisa
en el log y desde entonces cada imagen sale de `BusRenderer`
(`bot.services.browser_scene`). El juego nunca se queda sin imagen.

**Una sola llamada al navegador por mano.** `reveal` pide de una vez el tramo
común, la cola «win» y la cola «lose» (el GIF no depende de lo que elija el
jugador, así que se dibuja antes de que elija) y monta con ellos los dos GIF
en un hilo: `win` = común + cola win, `lose` = común + cola lose. El primer
fotograma de la cola «lose» va entero, porque el navegador lo dibuja justo
después de la cola «win» y su recuadro no sirve para pegarlo sobre el común.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PIL import Image

from bot.services import browser_scene
from bot.services.blackjack import Card
from bot.services.browser_scene import BrowserScene, png_bytes
from bot.services.bus_render import (
    Board,
    BusRenderer,
    H,
    Reveal,
    W,
    board_state,
    durations,
    encode,
    meta_state,
    reveal_states,
)

SCENE = Path(__file__).resolve().parent.parent / "assets" / "autobus" / "escena.html"


def assemble(patches: list[dict[str, Any]]) -> list[Image.Image]:
    """Fotogramas enteros a partir de los recuadros de la escena (`browser_scene.assemble`)."""
    return browser_scene.assemble(patches, (W, H))


def build_reveal(
    patches: list[dict[str, Any]], states: list[dict[str, Any]], common: int, win: int
) -> Reveal:
    """Los dos GIF de una mano a partir de los recuadros de la escena.

    Args:
        patches: Los recuadros de `común + win + lose`, en ese orden.
        states: Los mismos estados (de ahí salen los tiempos de cada fotograma).
        common: Cuántos fotogramas tiene el tramo común.
        win: Cuántos tiene la cola «win».
    """
    frames = assemble(patches)
    split = common + win
    win_frames = frames[:split]
    lose_frames = frames[:common] + frames[split:]
    win_ms = durations(states[:split])
    lose_ms = durations(states[:common] + states[split:])
    with ThreadPoolExecutor(max_workers=2) as pool:
        won = pool.submit(encode, win_frames, win_ms)
        lost = pool.submit(encode, lose_frames, lose_ms)
        return Reveal(win=won.result(), lose=lost.result())


class BusScene:
    """Dibuja el Autobús en Chromium y, si no puede, con Pillow (`fallback`).

    Args:
        fallback: El dibujo de Pillow.
        executable_path: Chromium concreto (si no, el que instaló Playwright).
    """

    def __init__(
        self, fallback: BusRenderer | None = None, *, executable_path: str | None = None
    ) -> None:
        self.fallback = fallback or BusRenderer()
        self.browser = BrowserScene(
            SCENE,
            name="El navegador del autobús",
            ready="loadFonts()",
            executable_path=executable_path,
        )

    @property
    def disabled(self) -> bool:
        """Si el navegador falló y ya solo se dibuja con Pillow."""
        return self.browser.disabled

    async def close(self) -> None:
        """Cierra el navegador (al apagar el bot)."""
        await self.browser.close()

    async def board(self, board: Board) -> bytes:
        """PNG de la mesa quieta: al abrir, al elegir, al cobrar y al repintar."""
        patches = await self.browser.render(meta_state(), [board_state(board)])
        if patches is None:
            return await asyncio.to_thread(self.fallback.board, board)
        return png_bytes(patches[0])

    async def reveal(self, cards: Sequence[Card], hand: int, *, seed: int) -> Reveal:
        """Los GIF de la mano `hand` (1-5) si acierta y si falla, con su PNG final.

        Args:
            cards: Las cartas de la partida (al menos `hand`).
            hand: La mano que se va a jugar.
            seed: Semilla de las chispas y el temblor (misma semilla, mismo GIF).
        """
        common, win, lose = reveal_states(cards, hand, seed=seed)
        states = common + win + lose
        patches = await self.browser.render(meta_state(), states)
        if patches is None:
            return await asyncio.to_thread(self.fallback.reveal, cards, hand, seed=seed)
        return await asyncio.to_thread(build_reveal, patches, states, len(common), len(win))
