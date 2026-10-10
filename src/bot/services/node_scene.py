"""Escenas de canvas pintadas con Node y Skia, sin navegador.

Las escenas (`assets/*/escena.html`) solo usan canvas 2D, así que no necesitan
un Chromium entero: `assets/escena_node.cjs` carga su `<script>` en Node con
`@napi-rs/canvas` (Skia, el motor que pinta el canvas en Chrome) y devuelve
los recuadros en RGBA crudo por una tubería. Frente a `BrowserScene` se ahorra
comprimir cada fotograma a PNG en un hilo, pasarlo en base64 y descomprimirlo
en Python, y cada proceso ocupa bastante menos memoria que un Chromium.

Mismo contrato que `BrowserScene.render`: `setup(meta)` y
`renderFrames(states, first)` en la escena, varios procesos que pintan tramos
seguidos a la vez (`PROCS`) y `None` si no hay Node, para que quien dibuja
pase a su plan B. Los recuadros que devuelve llevan la imagen ya abierta
(`image`) en vez del PNG (`u`); `assemble` los monta igual.

Hace falta `node` en el PATH y el paquete `@napi-rs/canvas` (`package.json` de
la raíz; en Docker, en `NODE_PATH`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from PIL import Image

from bot.services.browser_scene import BATCH, IDLE_SECONDS, TABS, Patch

logger = logging.getLogger(__name__)

RUNNER = Path(__file__).resolve().parent.parent / "assets" / "escena_node.cjs"
#: Procesos que pintan un mismo dibujo a la vez; como las pestañas de Chromium.
PROCS = TABS
#: Lo que puede tardar Node en arrancar y cargar la escena.
START_TIMEOUT = 15.0


class NodeSceneError(RuntimeError):
    """La escena falló dentro de Node (el mensaje trae su traza)."""


class _Process:
    """Un `node escena_node.cjs escena.html` vivo, con la escena cargada."""

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self.proc = proc

    @classmethod
    async def start(cls, node: str, scene: Path) -> _Process:
        proc = await asyncio.create_subprocess_exec(
            node,
            str(RUNNER),
            str(scene),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self = cls(proc)
        try:
            header = await asyncio.wait_for(self._header(), START_TIMEOUT)
        except BaseException:
            await self.close()
            raise
        if not header.get("ready"):
            raise NodeSceneError(f"Node no ha cargado la escena: {header}")
        return self

    @property
    def alive(self) -> bool:
        return self.proc.returncode is None

    async def _header(self) -> dict[str, Any]:
        assert self.proc.stdout is not None
        line = await self.proc.stdout.readline()
        if not line:
            assert self.proc.stderr is not None
            error = (await self.proc.stderr.read()).decode(errors="replace").strip()
            raise NodeSceneError(f"Node se ha cerrado: {error or 'sin mensaje'}")
        return json.loads(line)

    async def call(self, request: dict[str, Any]) -> tuple[dict[str, Any], list[Patch]]:
        """Manda una petición y lee su respuesta (y los píxeles, si los hay)."""
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write(json.dumps(request).encode() + b"\n")
        await self.proc.stdin.drain()
        header = await self._header()
        if not header.get("ok"):
            raise NodeSceneError(header.get("error", "error sin mensaje"))
        patches: list[Patch] = []
        for x, y, w, h in header.get("frames", []):
            raw = await self.proc.stdout.readexactly(w * h * 4)
            # El fondo de las escenas es opaco: el alfa sobra.
            image = Image.frombuffer("RGBA", (w, h), raw, "raw", "RGBA", 0, 1).convert("RGB")
            patches.append({"x": x, "y": y, "image": image})
        return header, patches

    async def close(self) -> None:
        if self.alive:
            self.proc.kill()
        await self.proc.wait()


class NodeScene:
    """Procesos de Node con `scene` cargada; `None` en `render` si no hay Node.

    Args:
        scene: El HTML de la escena.
        name: Para los mensajes del log («el pintor de la moneda»).
        node: Ejecutable de Node (si no, el `node` del PATH).
        idle_seconds: Cuánto se dejan abiertos los procesos sin usarlos.
    """

    def __init__(
        self,
        scene: Path,
        *,
        name: str,
        node: str | None = None,
        idle_seconds: float = IDLE_SECONDS,
    ) -> None:
        self.scene = scene
        self.name = name
        self.node = node
        self.idle_seconds = idle_seconds
        self._procs: list[_Process] = []
        self._lock = asyncio.Lock()
        self._idle: asyncio.TimerHandle | None = None
        #: Tras un fallo no se reintenta en cada dibujo: se usa el plan B.
        self.disabled = False

    async def _open(self, count: int) -> list[_Process]:
        """`count` procesos con la escena cargada (arranca los que falten)."""
        self._procs = [proc for proc in self._procs if proc.alive]
        missing = count - len(self._procs)
        if missing > 0:
            node = self.node or shutil.which("node")
            if node is None:
                raise NodeSceneError("No hay `node` en el PATH")
            self._procs += await asyncio.gather(
                *(_Process.start(node, self.scene) for _ in range(missing))
            )
        return self._procs[:count]

    def _touch(self) -> None:
        """Programa el cierre de los procesos tras `idle_seconds` sin uso."""
        if self._idle is not None:
            self._idle.cancel()
        loop = asyncio.get_running_loop()
        self._idle = loop.call_later(self.idle_seconds, lambda: asyncio.ensure_future(self.close()))

    async def close(self) -> None:
        """Cierra los procesos (al apagar el bot o tras un rato sin uso)."""
        async with self._lock:
            if self._idle is not None:
                self._idle.cancel()
                self._idle = None
            procs, self._procs = self._procs, []
            for proc in procs:
                await proc.close()

    async def evaluate(self, expression: str) -> Any:
        """Evalúa `expression` en la escena (para las pruebas); `None` si no hay Node."""

        async def work(procs: list[_Process]) -> Any:
            header, _ = await procs[0].call({"call": expression, "eval": True})
            return header.get("value")

        return await self._run(1, work)

    async def render(
        self, meta: dict[str, Any], states: list[dict[str, Any]], *, procs: int = PROCS
    ) -> list[Patch] | None:
        """Recuadros de cada fotograma (ver `browser_scene.assemble`); `None` si no hay Node.

        Igual que `BrowserScene.render`: cada proceso pinta un tramo seguido de
        estados tras su propio `setup(meta)`, y su primer fotograma sale entero.
        """
        count = max(1, min(procs, len(states)))
        size = -(-len(states) // count)
        chunks = [(i, states[i : i + size]) for i in range(0, len(states), size)]

        async def one(proc: _Process, offset: int, chunk: list[dict[str, Any]]) -> list[Patch]:
            await proc.call({"call": "setup", "args": [meta]})
            patches: list[Patch] = []
            for start in range(0, len(chunk), BATCH):
                _, part = await proc.call(
                    {"call": "renderFrames", "args": [chunk[start : start + BATCH], offset + start]}
                )
                patches += part
            return patches

        async def work(running: list[_Process]) -> list[Patch]:
            parts = await asyncio.gather(
                *(
                    one(proc, offset, chunk)
                    for proc, (offset, chunk) in zip(running, chunks, strict=True)
                )
            )
            return [patch for part in parts for patch in part]

        return await self._run(len(chunks), work)

    async def _run(self, count: int, work: Any) -> Any:
        if self.disabled:
            return None
        async with self._lock:
            try:
                result = await work(await self._open(count))
            except Exception:
                logger.warning("%s no está disponible; se usa el plan B", self.name, exc_info=True)
                self.disabled = True
                procs, self._procs = self._procs, []
                for proc in procs:
                    await proc.close()
                return None
            self._touch()
            return result
