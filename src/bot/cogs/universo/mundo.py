"""El mundo publicado del Jovaniverso: lo que baja la página y los GLB.

- `GET /api/mundo?guild_id=&v=` con la sesión de cualquier miembro: `{version, trozos}`, el
  mundo publicado en trozos (`docs/mundo-celdas.md`; `{version: 0, trozos: {}}` si no hay).
  El texto sale ya codificado y se guarda en memoria por servidor hasta que alguien publica.
  Con `v` igual a la versión publicada (`mundo` de `/api/entrar`) el navegador lo guarda
  para siempre (`MUNDO_CACHE`); sin `v` o con otra, contesta la de ahora sin caché. Con
  `desde=M`, la versión que ya tiene la página: `{version, desde, trozos}` con solo los
  trozos que cambiaron después de M (`null` el que se borró), con la misma caché; si M ya
  no se conserva, el mundo entero, sin `desde`.
- `GET /api/glb/{sha}`: un modelo GLB por el SHA-256 de su contenido, sin sesión (es
  contenido que no cambia: `immutable`). Sale tal cual de su archivo (`glb_path`), sin pasar
  por la base.

Lo guardado en memoria lo borra `EditorMixin.publish_world` al publicar.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import re

from aiohttp import web

from bot.cogs.universo.comun import _COG_KEY
from bot.services.universo import (
    Places,
    bearer,
    world_places,
)

#: Caché de `/api/mundo?v=` con la versión publicada: no cambia nunca, y va con sesión
#: (`private`: solo el navegador, ni el túnel ni el proxy de Discord).
MUNDO_CACHE = "private, max-age=31536000, immutable"
_SHA256 = re.compile(r"[0-9a-f]{64}")


class WorldMixin:
    """Mundo publicado del cog `Universo`, guardado en memoria por servidor."""

    async def mundo(self, guild_id: int) -> tuple[int, str]:
        """Versión publicada de un servidor y sus trozos ya codificados (0 y `{}` si no hay).

        Se guarda en memoria hasta que `publish_world` la borra.
        """
        cached = self._mundo.get(guild_id)
        if cached is None:
            epoch = self._mundo_epoch.get(guild_id, 0)
            found = await self.bot.universo_mundo.published(guild_id)
            cached = (found[0], _pieces_text(found[1])) if found else (0, "{}")
            if self._mundo_epoch.get(guild_id, 0) == epoch:
                self._mundo[guild_id] = cached
        return cached

    async def places(self, guild_id: int) -> Places:
        """Los lugares de los logros en el mundo publicado de un servidor, guardados hasta
        que se publica otro (`publish_world`)."""
        cached = self._lugares.get(guild_id)
        if cached is None:
            epoch = self._mundo_epoch.get(guild_id, 0)
            found = await self.bot.universo_mundo.published(guild_id)
            cached = await asyncio.to_thread(world_places, found[1] if found else {})
            if self._mundo_epoch.get(guild_id, 0) == epoch:
                self._lugares[guild_id] = cached
        return cached

    async def mundo_desde(self, guild_id: int, since: int) -> tuple[int, str] | None:
        """Lo publicado después de `since` (versión y trozos ya codificados, `null` los borrados).

        `None` si hace falta el mundo entero: `since` ya no se conserva o no es anterior.
        """
        version, _pieces = await self.mundo(guild_id)
        if since > version:
            return None
        if since == version:
            return version, "{}"
        cached = self._mundo_desde.get((guild_id, since))
        if cached is not None and cached[0] == version:
            return version, cached[1]
        found = await self.bot.universo_mundo.published_since(guild_id, since)
        if found is None:
            return None
        version, pieces = found
        for key in [
            k for k, c in self._mundo_desde.items() if k[0] == guild_id and c[0] != version
        ]:
            del self._mundo_desde[key]
        text = _changes_text(pieces)
        self._mundo_desde[(guild_id, since)] = (version, text, None)
        return version, text


def _pieces_text(pieces: dict[str, str]) -> str:
    """Los trozos publicados como un objeto JSON, pegando el texto de cada uno tal cual."""
    return "{" + ",".join(f"{json.dumps(k)}:{doc}" for k, doc in sorted(pieces.items())) + "}"


async def _mundo(request: web.Request) -> web.Response:
    cog = request.app[_COG_KEY]
    guild, _member = cog._member(bearer(request.headers.get("Authorization")), dict(request.query))
    since = request.query.get("desde", "")
    if since.isdigit() and int(since) > 0:
        found = await cog.mundo_desde(guild.id, int(since))
        if found is not None:
            return await _mundo_desde(request, guild.id, int(since), *found)
    version, pieces = await cog.mundo(guild.id)
    # Con `v` igual a la versión publicada (la que da `/api/entrar`), lo que hay en esa URL
    # ya no cambia (las versiones solo crecen): se guarda para siempre, solo en el navegador
    # porque va con sesión. Sin `v`, o con una vieja, lo de ahora y sin caché.
    cache = MUNDO_CACHE if request.query.get("v") == str(version) else "no-cache"
    headers = {"Cache-Control": cache, "Vary": "Accept-Encoding"}
    if "gzip" not in request.headers.get("Accept-Encoding", ""):
        return web.Response(
            text=_world_text(version, pieces), content_type="application/json", headers=headers
        )
    cached = cog._mundo_gz.get(guild.id)
    if cached is None or cached[0] != version:
        data = _world_text(version, pieces).encode()
        cached = (version, await asyncio.to_thread(gzip.compress, data, 6))
        cog._mundo_gz[guild.id] = cached
    return web.Response(
        body=cached[1],
        content_type="application/json",
        headers={**headers, "Content-Encoding": "gzip"},
    )


async def _mundo_desde(
    request: web.Request, guild_id: int, since: int, version: int, pieces: str
) -> web.Response:
    """`/api/mundo?desde=`: solo lo cambiado, con la caché de `/api/mundo` y su gzip guardado."""
    cog = request.app[_COG_KEY]
    cache = MUNDO_CACHE if request.query.get("v") == str(version) else "no-cache"
    headers = {"Cache-Control": cache, "Vary": "Accept-Encoding"}
    text = f'{{"version":{version},"desde":{since},"trozos":{pieces}}}'
    if "gzip" not in request.headers.get("Accept-Encoding", ""):
        return web.Response(text=text, content_type="application/json", headers=headers)
    cached = cog._mundo_desde.get((guild_id, since))
    data = cached[2] if cached is not None and cached[0] == version else None
    if data is None:
        data = await asyncio.to_thread(gzip.compress, text.encode(), 6)
        if cached is not None and cached[0] == version:
            cog._mundo_desde[(guild_id, since)] = (version, cached[1], data)
    return web.Response(
        body=data,
        content_type="application/json",
        headers={**headers, "Content-Encoding": "gzip"},
    )


def _changes_text(pieces: dict[str, str]) -> str:
    """Los trozos cambiados como un objeto JSON: cada uno tal cual, `null` el que se borró."""
    return (
        "{"
        + ",".join(f"{json.dumps(k)}:{doc or 'null'}" for k, doc in sorted(pieces.items()))
        + "}"
    )


def _world_text(version: int, pieces: str) -> str:
    """El cuerpo de `/api/mundo`: los trozos ya van codificados y se pegan tal cual."""
    return f'{{"version":{version},"trozos":{pieces}}}'


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
