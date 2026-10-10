"""Una pestaña de Chromium sin ventana con una escena HTML cargada, para dibujar con JavaScript.

Discord no ejecuta JavaScript en los mensajes, así que el JavaScript corre en
el bot: la escena es un HTML de `assets/` con funciones que Python llama con
`page.evaluate` y que devuelven imágenes (data URL). Este módulo solo se
encarga del navegador: arrancarlo cuando hace falta, tener una pestaña con la
escena, cerrarlo tras un rato sin uso y, si falla, decirlo una vez en el log
y apagarse para que quien dibuja use su versión de Pillow.

Lo usan Cara o cruz (`bot.services.coin_scene`), la ruleta
(`bot.services.roulette_scene`) y los dados (`bot.services.craps_scene`), cada
uno con su navegador. Las carreras de caballos
tienen su propia copia de esta lógica en `bot.services.horses_scene`, anterior
a este módulo.

También monta los fotogramas que devuelven las escenas (`render` y
`assemble`): cada escena tiene `setup(meta)` y `renderFrames(states, first)`,
que devuelve de cada fotograma su PNG entero o solo el recuadro que cambia.

Coste: Chromium arranca en ~1-2 s la primera vez y ocupa ~150-250 MB mientras
está abierto. Los dibujos se hacen uno detrás de otro; dentro de un dibujo,
`render` reparte los fotogramas entre `TABS` pestañas que pintan a la vez, cada
una en su proceso. El NAS tiene 4 hilos: con 2 pestañas, el navegador tarda la
mitad y quedan hilos para el bot y para montar el GIF.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar

from PIL import Image

from bot.utils.gif import QUANTIZE_THREADS

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Tras este rato sin dibujar nada, el navegador se cierra para liberar memoria.
IDLE_SECONDS = 10 * 60
#: Pestañas que pintan un mismo dibujo a la vez (ver la cabecera).
TABS = 2
#: Fotogramas que se piden a una pestaña de una vez (cada uno vuelve como PNG en base64).
BATCH = 20

Patch = dict[str, Any]


def png_bytes(patch: Patch) -> bytes:
    """Los bytes del PNG de un recuadro (viene como data URL)."""
    return base64.b64decode(patch["u"].split(",", 1)[1])


def assemble(patches: list[Patch], size: tuple[int, int]) -> list[Image.Image]:
    """Monta los fotogramas pegando cada recuadro sobre el fotograma anterior.

    La escena devuelve el fotograma entero (`size`) cuando hace falta y, si no,
    solo el recuadro que ha cambiado (`x`, `y` y su PNG en `u`). Los PNG se
    descomprimen en varios hilos (no dependen del orden y Pillow suelta el GIL);
    pegar sí va en orden. Los recuadros de `bot.services.node_scene` ya traen la
    imagen abierta (`image`) y no hay nada que descomprimir.

    Raises:
        ValueError: Si el primer fotograma no viene entero.
    """

    def image_of(patch: Patch) -> Image.Image:
        if "image" in patch:
            return patch["image"]
        return Image.open(io.BytesIO(png_bytes(patch))).convert("RGB")

    if all("image" in patch for patch in patches):
        images = [patch["image"] for patch in patches]
    else:
        with ThreadPoolExecutor(max_workers=QUANTIZE_THREADS) as pool:
            images = list(pool.map(image_of, patches))
    frames: list[Image.Image] = []
    for patch, image in zip(patches, images, strict=True):
        if image.size != size:
            if not frames:
                raise ValueError("El primer fotograma de la escena tiene que ir entero.")
            frame = frames[-1].copy()
            frame.paste(image, (patch["x"], patch["y"]))
            image = frame
        frames.append(image)
    return frames


class BrowserScene:
    """Pestaña de Chromium con `scene` cargada; `None` en `run` si no hay navegador.

    Args:
        scene: El HTML de la escena.
        name: Para los mensajes del log («el navegador de la moneda»).
        viewport: Tamaño de la pestaña.
        ready: Expresión JavaScript que se espera tras cargar (fuentes, etc.).
        executable_path: Chromium concreto (si no, el que instaló Playwright).
        idle_seconds: Cuánto se deja abierto el navegador sin usarlo.
    """

    def __init__(
        self,
        scene: Path,
        *,
        name: str,
        viewport: tuple[int, int] = (640, 360),
        ready: str = "document.fonts.ready.then(() => true)",
        executable_path: str | None = None,
        idle_seconds: float = IDLE_SECONDS,
    ) -> None:
        self.scene = scene
        self.name = name
        self.viewport = viewport
        self.ready = ready
        self.executable_path = executable_path
        self.idle_seconds = idle_seconds
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None
        #: Pestañas de más para `run_tabs`, con la escena ya cargada.
        self._extra: list[Any] = []
        self._lock = asyncio.Lock()
        self._idle: asyncio.TimerHandle | None = None
        #: Tras un fallo no se reintenta en cada dibujo: se usa Pillow.
        self.disabled = False

    async def _open(self) -> Any:
        """La pestaña con la escena cargada (arranca el navegador si hace falta)."""
        if self._page is not None and not self._page.is_closed():
            return self._page
        from playwright.async_api import async_playwright  # import perezoso: es pesado

        if self._playwright is None:
            self._playwright = await async_playwright().start()
        if self._browser is None or not self._browser.is_connected():
            self._browser = await self._playwright.chromium.launch(
                executable_path=self.executable_path,
                args=["--disable-gpu", "--disable-dev-shm-usage"],
            )
        width, height = self.viewport
        self._page = await self._browser.new_page(
            viewport={"width": width, "height": height}, device_scale_factor=1
        )
        await self._page.goto(self.scene.as_uri())
        await self._page.evaluate(self.ready)
        self._extra = []
        return self._page

    async def _open_tabs(self, count: int) -> list[Any]:
        """`count` pestañas con la escena cargada: la principal y las de más."""
        first = await self._open()
        self._extra = [page for page in self._extra if not page.is_closed()]
        width, height = self.viewport
        while len(self._extra) < count - 1:
            page = await self._browser.new_page(
                viewport={"width": width, "height": height}, device_scale_factor=1
            )
            await page.goto(self.scene.as_uri())
            await page.evaluate(self.ready)
            self._extra.append(page)
        return [first, *self._extra[: count - 1]]

    def _touch(self) -> None:
        """Programa el cierre del navegador tras `idle_seconds` sin uso."""
        if self._idle is not None:
            self._idle.cancel()
        loop = asyncio.get_running_loop()
        self._idle = loop.call_later(self.idle_seconds, lambda: asyncio.ensure_future(self.close()))

    async def close(self) -> None:
        """Cierra el navegador (al apagar el bot o tras un rato sin uso)."""
        async with self._lock:
            if self._idle is not None:
                self._idle.cancel()
                self._idle = None
            browser, playwright = self._browser, self._playwright
            self._page = self._browser = self._playwright = None
            self._extra = []
            try:
                if browser is not None:
                    await browser.close()
                if playwright is not None:
                    await playwright.stop()
            except Exception:
                logger.debug("No se pudo cerrar %s", self.name, exc_info=True)

    async def run(self, work: Callable[[Any], Awaitable[T]]) -> T | None:
        """Ejecuta `work(page)` con la pestaña; `None` si no hay navegador.

        El primer fallo apaga el navegador para siempre (`disabled`) y se
        avisa en el log: quien llama dibuja entonces con Pillow.
        """
        return await self._run(self._open, work)

    async def run_tabs(self, count: int, work: Callable[[list[Any]], Awaitable[T]]) -> T | None:
        """Como `run`, pero `work` recibe `count` pestañas para dibujar a la vez."""
        return await self._run(lambda: self._open_tabs(count), work)

    async def render(
        self, meta: dict[str, Any], states: list[dict[str, Any]], *, tabs: int = TABS
    ) -> list[Patch] | None:
        """Recuadros de cada fotograma (ver `assemble`); `None` si no hay navegador.

        Los estados se reparten en tramos seguidos, uno por pestaña. Cada pestaña
        hace `setup(meta)` y su primer fotograma sale entero, así que los tramos
        se pegan uno detrás de otro. `renderFrames` recibe el índice absoluto del
        primer estado de cada lote, por si la escena lo usa (los destellos de la
        moneda).
        """
        tabs = max(1, min(tabs, len(states)))
        size = -(-len(states) // tabs)
        chunks = [(i, states[i : i + size]) for i in range(0, len(states), size)]

        async def one(page: Any, offset: int, chunk: list[dict[str, Any]]) -> list[Patch]:
            await page.evaluate("m => setup(m)", meta)
            patches: list[Patch] = []
            for start in range(0, len(chunk), BATCH):
                patches += await page.evaluate(
                    "([s, first]) => renderFrames(s, first)",
                    [chunk[start : start + BATCH], offset + start],
                )
            return patches

        async def work(pages: list[Any]) -> list[Patch]:
            parts = await asyncio.gather(
                *(
                    one(page, offset, chunk)
                    for page, (offset, chunk) in zip(pages, chunks, strict=True)
                )
            )
            return [patch for part in parts for patch in part]

        return await self.run_tabs(len(chunks), work)

    async def _run(
        self, open_: Callable[[], Awaitable[Any]], work: Callable[[Any], Awaitable[T]]
    ) -> T | None:
        if self.disabled:
            return None
        async with self._lock:
            try:
                page = await open_()
                result = await work(page)
            except Exception:
                logger.warning(
                    "%s no está disponible; se dibuja con Pillow", self.name, exc_info=True
                )
                self.disabled = True
                self._page = None
                return None
            self._touch()
            return result
