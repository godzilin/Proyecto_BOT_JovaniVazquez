"""El mundo publicado del Jovaniverso: lo que baja la página y los GLB.

El servidor no guarda el mundo en memoria: sirve un índice y los trozos que le piden, leídos
de SQLite, con una caché acotada por bytes (`ByteCache`, `CACHE_BYTES`). Las dos rutas llevan
la sesión de cualquier miembro y `guild_id`:

- `GET /api/mundo/indice?guild_id=&v=N` → `{"version": N, "zonas": doc | null,
  "teleportes": doc | null, "trozos": {"a/pino": 3, "c/0/0": 7, ...}}`: cada celda y asset
  publicados con la versión de su fila (`docs/mundo-celdas.md`), ordenados. Sin nada publicado,
  `{"version": 0, "zonas": null, "teleportes": null, "trozos": {}}`. Con `v` igual a una versión
  que se conserva (la de `/api/entrar` u otra), esa versión y `MUNDO_CACHE` (no cambia nunca);
  sin `v` o con una que ya no está, la de ahora y `no-cache`.
- `GET /api/mundo/trozos?guild_id=&t=c/0/0@7,a/pino@3[&resumen=1]` → `{"trozos": {"c/0/0": doc,
  "a/pino": doc}}`, hasta `MAX_LOTE` claves, cada una con la versión de su fila. Con `resumen=1`,
  `{"resumenes": {"c/0/0": "<base64 de las alturas muestreadas>"}}` (`""` si la celda no tiene
  terreno) sin leer los docs. 400 si una clave o versión no vale, 410 si alguna fila ya no
  existe (se podó: la página vuelve a pedir el índice). Siempre `MUNDO_CACHE`: la URL nombra
  filas que no cambian.
- `GET /api/glb/{sha}`: un modelo GLB por el SHA-256 de su contenido, sin sesión (es
  contenido que no cambia: `immutable`). Sale tal cual de su archivo (`glb_path`), sin pasar
  por la base.

Los dos textos salen con gzip si el cliente lo acepta: el índice siempre, el lote si pasa de
1 KB, comprimidos una vez (en un hilo) y guardados en la caché.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from typing import TYPE_CHECKING, TypeVar

from aiohttp import web

from bot.cogs.universo.comun import _COG_KEY
from bot.repositories import universo_mundo as repository
from bot.repositories.universo_mundo import WorldIndex
from bot.services.universo import (
    SERIE_PLACES,
    Places,
    UniversoError,
    bearer,
    world_places,
)
from bot.services.universo_editor import MundoError, parse_key, split_summary

if TYPE_CHECKING:
    from bot.cogs.universo import Universo

#: Caché del índice y de los lotes con la versión por delante: no cambia nunca, y va con
#: sesión (`private`: solo el navegador, ni el túnel ni el proxy de Discord).
MUNDO_CACHE = "private, max-age=31536000, immutable"
#: Lo que pueden ocupar entre todos los servidores el índice, los trozos y los lotes ya
#: comprimidos que se guardan en memoria (el resto está en SQLite).
CACHE_BYTES = 48 * 2**20
#: Trozos que caben en un lote de `/api/mundo/trozos`.
MAX_LOTE = 64
#: El lote más pequeño que se comprime.
MIN_GZIP = 1024
#: El índice de un servidor sin nada publicado.
EMPTY_INDEX = '{"version":0,"zonas":null,"teleportes":null,"trozos":{}}'
_SHA256 = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"[1-9][0-9]{0,9}")
T = TypeVar("T")


class ByteCache:
    """Caché LRU acotada por bytes: lo que se pasa del tope, lo menos usado, se suelta.

    El tamaño de un valor es su `len` (los textos son JSON, casi siempre ASCII). Un valor que
    por sí solo no cabe no se guarda.

    Args:
        max_bytes: Lo más que pueden sumar los valores guardados.

    Attributes:
        bytes: Lo que suman ahora.
    """

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self.bytes = 0
        self._items: OrderedDict[Hashable, str | bytes] = OrderedDict()

    def __len__(self) -> int:
        return len(self._items)

    def get(self, key: Hashable) -> str | bytes | None:
        """El valor guardado (y pasa a ser el más reciente), o `None`."""
        value = self._items.get(key)
        if value is not None:
            self._items.move_to_end(key)
        return value

    def put(self, key: Hashable, value: str | bytes) -> None:
        """Guarda un valor y suelta los menos usados hasta que todo cabe."""
        old = self._items.pop(key, None)
        if old is not None:
            self.bytes -= len(old)
        if len(value) > self.max_bytes:
            return
        self._items[key] = value
        self.bytes += len(value)
        while self.bytes > self.max_bytes:
            _key, dropped = self._items.popitem(last=False)
            self.bytes -= len(dropped)


class WorldMixin:
    """Mundo publicado del cog `Universo`: versión, índice y trozos por servidor."""

    async def _once(self, key: Hashable, make: Callable[[], Awaitable[T]]) -> T:
        """Hace `make()` una sola vez a la vez para `key`: los que llegan mientras tanto esperan
        su resultado. Tras publicar, todas las páginas piden lo mismo a la vez."""
        task = self._pending.get(key)
        if task is None:
            task = asyncio.ensure_future(make())
            self._pending[key] = task
            task.add_done_callback(lambda _task: self._pending.pop(key, None))
        return await asyncio.shield(task)

    async def world_version(self, guild_id: int) -> int:
        """La versión publicada de un servidor (0 si no hay), guardada hasta que se publica."""
        cached = self._version.get(guild_id)
        if cached is None:
            epoch = self._mundo_epoch.get(guild_id, 0)
            cached = await self.bot.universo_mundo.version(guild_id)
            if self._mundo_epoch.get(guild_id, 0) == epoch:
                self._version[guild_id] = cached
        return cached

    async def world_index(
        self, guild_id: int, version: int | None = None
    ) -> tuple[int, str] | None:
        """El índice (`version`, texto JSON) de una versión, o de la de ahora si es `None`.

        Con `version` dada, `None` si ya no se conserva (o aún no existe).
        """
        exact = version is not None
        current = await self.world_version(guild_id)
        if version is None:
            version = current
            if version == 0:
                return 0, EMPTY_INDEX
        elif not current - repository.KEEP_VERSIONS < version <= current:
            return None  # podada (publicar conserva las últimas `KEEP_VERSIONS`) o futura
        text = self._cache.get(("indice", guild_id, version))
        if text is None:
            built = await self._build_index(guild_id, version if exact else None)
            if built is None:
                return None if exact else (0, EMPTY_INDEX)
            version, text = built[0], built[1]
        return version, str(text)

    async def _build_index(
        self, guild_id: int, version: int | None
    ) -> tuple[int, str, Places] | None:
        """Lee el índice de la base, lo guarda en la caché y saca de él los lugares."""
        return await self._once(
            ("indice", guild_id, version), lambda: self._read_index(guild_id, version)
        )

    async def _read_index(
        self, guild_id: int, version: int | None
    ) -> tuple[int, str, Places] | None:
        index = await self.bot.universo_mundo.index(guild_id, version)
        if index is None:
            return None
        text, places = await asyncio.to_thread(_digest, index)
        self._cache.put(("indice", guild_id, index.version), text)
        return index.version, text, places

    async def places(self, guild_id: int) -> Places:
        """Los lugares de los logros en el mundo publicado de un servidor, guardados hasta
        que se publica otro (`publish_world`). Salen del índice, sin leer ningún trozo."""
        cached = self._lugares.get(guild_id)
        if cached is None:
            epoch = self._mundo_epoch.get(guild_id, 0)
            built = await self._build_index(guild_id, None)
            cached = built[2] if built else SERIE_PLACES
            if self._mundo_epoch.get(guild_id, 0) == epoch:
                self._lugares[guild_id] = cached
        return cached

    async def world_batch(self, guild_id: int, wanted: dict[str, int], summary: bool) -> str | None:
        """Los trozos de un lote (clave → versión de su fila) como texto JSON de la respuesta.

        `None` si alguna fila ya no existe. Los documentos se pegan tal cual, sin decodificar.
        """
        key = ("lote", guild_id, tuple(wanted.items()), summary)
        return await self._once(key, lambda: self._read_batch(guild_id, wanted, summary))

    async def _read_batch(self, guild_id: int, wanted: dict[str, int], summary: bool) -> str | None:
        repo = self.bot.universo_mundo
        if summary:
            found = await repo.pieces_at(guild_id, list(wanted.items()), summary_only=True)
            if len(found) < len(wanted):
                return None
            samples = {
                key: split_summary(found[key, version][1])[2] if found[key, version][1] else ""
                for key, version in wanted.items()
            }
            return json.dumps({"resumenes": samples}, separators=(",", ":"))
        texts: dict[str, str] = {}
        missing = []
        for key, version in wanted.items():
            text = self._cache.get(("trozo", guild_id, key, version))
            if text is None:
                missing.append((key, version))
            else:
                texts[key] = str(text)
        if missing:
            found = await repo.pieces_at(guild_id, missing)
            for key, version in missing:
                if (key, version) not in found:
                    return None
                texts[key] = found[key, version][0]
                self._cache.put(("trozo", guild_id, key, version), texts[key])
        return '{"trozos":{' + ",".join(f"{json.dumps(k)}:{texts[k]}" for k in wanted) + "}}"


def _digest(index: WorldIndex) -> tuple[str, Places]:
    """El texto del índice y los lugares que salen de él (para `asyncio.to_thread`)."""
    pieces = {key: version for key, (version, _summary) in sorted(index.pieces.items())}
    text = (
        f'{{"version":{index.version},"zonas":{index.zonas or "null"},'
        f'"teleportes":{index.teleportes or "null"},'
        f'"trozos":{json.dumps(pieces, separators=(",", ":"))}}}'
    )
    summaries = {key: summary for key, (_v, summary) in sorted(index.pieces.items()) if summary}
    return text, world_places(index.zonas, summaries)


def _parse_batch(text: str) -> dict[str, int]:
    """El parámetro `t` (`clave@versión,...`) como clave → versión de su fila.

    Raises:
        UniversoError: 400 si no lleva de 1 a `MAX_LOTE` trozos, repite uno o alguno no vale.
    """
    items = text.split(",") if text else []
    if not 1 <= len(items) <= MAX_LOTE:
        raise UniversoError(f"Un lote lleva de 1 a {MAX_LOTE} trozos.")
    wanted: dict[str, int] = {}
    for item in items:
        key, at, version = item.partition("@")
        try:
            parse_key(key)
        except MundoError as error:
            raise UniversoError(str(error)) from error
        if not at or not _VERSION.fullmatch(version) or key in wanted:
            raise UniversoError(f"«{item[:50]}» no es un trozo con la versión de su fila.")
        wanted[key] = int(version)
    return wanted


def _reply(text: str | None, data: bytes | None, headers: dict[str, str]) -> web.Response:
    """La respuesta JSON: el texto tal cual o, si hay, su gzip."""
    headers = {**headers, "Vary": "Accept-Encoding"}
    if data is None:
        return web.Response(text=text, content_type="application/json", headers=headers)
    return web.Response(
        body=data,
        content_type="application/json",
        headers={**headers, "Content-Encoding": "gzip"},
    )


async def _gzipped(cog: Universo, key: Hashable, text: str) -> bytes:
    """El gzip de un texto, de la caché o comprimido en un hilo (y guardado)."""
    data = cog._cache.get(key)
    if data is None:

        async def pack() -> bytes:
            packed = await asyncio.to_thread(gzip.compress, text.encode(), 6)
            cog._cache.put(key, packed)
            return packed

        return await cog._once(("gz", key), pack)
    return bytes(data)


async def _indice(request: web.Request) -> web.Response:
    cog = request.app[_COG_KEY]
    guild, _member = cog._member(bearer(request.headers.get("Authorization")), dict(request.query))
    asked = request.query.get("v", "")
    want = int(asked) if _VERSION.fullmatch(asked) else None
    found = await cog.world_index(guild.id, want) if want else None
    if found is None:
        found = await cog.world_index(guild.id)
    assert found is not None
    version, text = found
    # Con `v` igual a una versión que se conserva, lo que hay en esa URL ya no cambia: se
    # guarda para siempre, solo en el navegador porque va con sesión. Si no, lo de ahora.
    headers = {"Cache-Control": MUNDO_CACHE if want == version else "no-cache"}
    if "gzip" not in request.headers.get("Accept-Encoding", ""):
        return _reply(text, None, headers)
    return _reply(None, await _gzipped(cog, ("indice.gz", guild.id, version), text), headers)


async def _trozos(request: web.Request) -> web.Response:
    cog = request.app[_COG_KEY]
    guild, _member = cog._member(bearer(request.headers.get("Authorization")), dict(request.query))
    t = request.query.get("t", "")
    summary = request.query.get("resumen") == "1"
    wanted = _parse_batch(t)
    headers = {"Cache-Control": MUNDO_CACHE}
    key = ("lote.gz", guild.id, t, summary)
    packed = "gzip" in request.headers.get("Accept-Encoding", "")
    if packed and (data := cog._cache.get(key)) is not None:
        return _reply(None, bytes(data), headers)
    text = await cog.world_batch(guild.id, wanted, summary)
    if text is None:
        raise UniversoError("Esa parte del mundo ya no está: vuelve a pedir el índice.", 410)
    if packed and len(text) > MIN_GZIP:
        return _reply(None, await _gzipped(cog, key, text), headers)
    return _reply(text, None, headers)


async def _glb(request: web.Request) -> web.Response:
    cog = request.app[_COG_KEY]
    sha = request.match_info["sha"]
    if not _SHA256.fullmatch(sha):
        raise web.HTTPNotFound
    path = cog.bot.universo_mundo.glb_path(sha)
    # Se mira antes, fuera del bucle: el 404 de `FileResponse` saldría con la caché `immutable`.
    if not await asyncio.to_thread(path.is_file):
        raise web.HTTPNotFound
    return web.FileResponse(
        path,
        headers={
            "Content-Type": "model/gltf-binary",
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )
