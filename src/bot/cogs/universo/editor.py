"""El editor del mundo del Jovaniverso: borrador, publicar, versiones y GLB.

El editor del mundo vive en una subaplicación montada en `/api/editor/`, con su propio
tope de cuerpo (`MAX_EDITOR_BYTES`: cabe un GLB; el resto de rutas sigue con
`MAX_BODY_BYTES`). Todas sus rutas llevan la sesión y `guild_id` (en el cuerpo JSON o,
en `GET` y en la subida de GLB, en la query) y el servidor comprueba siempre que quien
llama tiene el permiso `editor`. Es una herramienta de administración: sin bromas ni logros.
Los permisos están en `bot.cogs.universo.permisos`.

- `GET /api/editor/indice` -> `{version, trozos: {clave: rev}, borrados: {clave: rev},
  docs: {clave: doc}}`: qué trozos tiene el borrador y su `rev` (las lápidas aparte, que
  conservan el suyo), los documentos de lo que no es celda (assets, zonas, teleportes) y la
  última versión publicada. Las celdas no van: el editor las pide alrededor de su cámara.
- `GET /api/editor/trozos?t=c/0/0,c/1/0` -> `{trozos: {clave: {doc, rev}}}`: hasta `MAX_LOTE`
  trozos del borrador (`doc` es `null` en una lápida; una clave que no existe sale con `rev` 0).
- `GET /api/editor/usos?asset=id` -> `{celdas: [clave]}`: las celdas del borrador que usan un
  asset (para no borrar uno que usa una celda que el editor no ha bajado).
- `PUT /api/editor/borrador` `{guild_id, v, trozos: {clave: {rev, doc}}}` -> `{revs}`:
  valida cada trozo (`bot.services.universo_editor`) y los guarda todos o ninguno: 409 si
  alguno ya no tiene ese `rev`. `doc: null` (o vacío) lo borra.
- `POST /api/editor/publicar` `{guild_id}` -> `{version}`: publica el borrador tal como esté
  y avisa a la sala (`{t: "mundo", version}`) para que las páginas recarguen.
- `GET /api/editor/versiones` -> `{versiones: [{version, autor, fecha, kb}]}`.
- `POST /api/editor/volver` `{guild_id, version}` -> `{}`: pone una versión vieja como
  borrador (la página recarga el borrador después).
- `POST /api/editor/glb` (cuerpo `octet-stream`) -> `{sha256, triangles}`: sube un modelo
  (507 si ya no cabe: `MAX_GLB_TOTAL`).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

import discord
from aiohttp import web

from bot.cogs.universo.comun import _COG_KEY, _errors
from bot.cogs.universo.mundo import MAX_LOTE
from bot.repositories.universo_mundo import (
    MAX_GLB_TOTAL,
    Change,
    Conflict,
    DraftIndex,
    GlbFull,
    MissingAssets,
)
from bot.services.universo import (
    UniversoError,
    bearer,
)
from bot.services.universo_editor import (
    FORMAT,
    MAX_GLB_BYTES,
    MundoError,
    cell_summary,
    encode_piece,
    glb_refs,
    glb_summary,
    is_empty,
    parse_key,
    validate_piece,
)

if TYPE_CHECKING:
    from bot.cogs.universo import Universo

logger = logging.getLogger(__name__)

#: Cuerpo máximo de las rutas del editor: un GLB o un guardado de trozos. La página parte
#: los guardados grandes en tandas de `MAX_TANDA` en `editor/borrador.ts`; el tope de cada
#: trozo lo pone `encode_piece`, y el de un GLB, `glb_summary`.
MAX_EDITOR_BYTES = max(MAX_GLB_BYTES, 8 * 1024 * 1024)
#: Trozos que caben en un guardado.
MAX_SAVE_PIECES = 5000

#: Aviso del 409 cuando el `rev` del editor ya no es el del borrador.
REV_CONFLICT = "Otra persona ha guardado el borrador mientras tanto. Recarga para ver sus cambios."


class EditorMixin:
    """Editor del mundo del cog `Universo`: borrador, publicar, versiones y GLB."""

    def build_editor(self) -> web.Application:
        """La subaplicación del editor, con un tope de cuerpo mayor que el del resto."""
        app = web.Application(client_max_size=MAX_EDITOR_BYTES, middlewares=[_errors])
        app[_COG_KEY] = self
        app.router.add_get("/indice", _editor_indice)
        app.router.add_get("/trozos", _editor_trozos)
        app.router.add_get("/usos", _editor_usos)
        app.router.add_put("/borrador", _editor_guardar)
        app.router.add_post("/publicar", _editor_publicar)
        app.router.add_get("/versiones", _editor_versiones)
        app.router.add_post("/volver", _editor_volver)
        app.router.add_post("/glb", _editor_glb)
        return app

    async def draft_index(self, guild_id: int) -> str:
        """Lo que baja el editor al abrir (`/indice`), ya codificado."""
        index = await self.bot.universo_mundo.draft_index(guild_id)
        version = await self.world_version(guild_id)
        return await asyncio.to_thread(_index_text, index, version)

    async def draft_pieces(self, guild_id: int, keys: list[str]) -> str:
        """Unos trozos del borrador (`/trozos`), ya codificados; los que no hay, con `rev` 0."""
        found = await self.bot.universo_mundo.draft_pieces(guild_id, keys)
        return _draft_text({k: found.get(k, ("", 0)) for k in keys})

    async def save_world(
        self, guild: discord.Guild, member: discord.Member, body: dict[str, Any]
    ) -> dict[str, int]:
        """Valida y guarda unos trozos del borrador, todos o ninguno; devuelve sus `rev` nuevos.

        Raises:
            UniversoError: 400 si algún trozo no vale o dejaría objetos sin asset, 409 si
                alguno ya no tiene el `rev` que cree la página.
        """
        if body.get("v") != FORMAT:
            raise UniversoError("El editor está desfasado: recarga la página.")
        pieces = body.get("trozos")
        if not isinstance(pieces, dict) or not 0 < len(pieces) <= MAX_SAVE_PIECES:
            raise UniversoError(f"Un guardado lleva de 1 a {MAX_SAVE_PIECES} trozos.")
        repo = self.bot.universo_mundo
        docs = {k: v.get("doc") for k, v in pieces.items() if isinstance(v, dict)}
        glbs = await repo.glb_triangles(glb_refs(docs))

        def check() -> dict[str, Change]:
            changes = {}
            for key, value in pieces.items():
                if not isinstance(value, dict) or set(value) != {"rev", "doc"}:
                    raise MundoError("Cada trozo que se guarda es {rev, doc}.")
                rev = _int(value["rev"], "rev", 0)
                parse_key(key)
                if value["doc"] is None:
                    changes[key] = Change(rev, "")
                    continue
                clean, refs = validate_piece(key, value["doc"], glbs)
                text = "" if is_empty(clean) else encode_piece(clean)
                summary = cell_summary(clean) if text else ""
                changes[key] = Change(rev, text, refs if text else frozenset(), summary)
            return changes

        try:
            changes = await asyncio.to_thread(check)
            return await repo.save(guild.id, changes, member.id)
        except MundoError as error:
            raise UniversoError(str(error)) from error
        except Conflict as error:
            raise UniversoError(REV_CONFLICT, 409) from error
        except MissingAssets as error:
            raise UniversoError(
                f"Hay objetos que usan assets que no existen: {', '.join(error.missing)}."
            ) from error

    async def publish_world(self, guild: discord.Guild, member: discord.Member) -> int:
        """Publica el borrador, limpia la caché y avisa a la sala; devuelve la versión.

        Raises:
            UniversoError: 400 si no hay borrador.
        """
        repo = self.bot.universo_mundo
        index = await repo.draft_index(guild.id)
        if not index.alive and not index.gone:
            raise UniversoError("No hay borrador que publicar.")
        version = await repo.publish(guild.id, member.id)
        # La caché de trozos va por versión de fila: no hace falta vaciarla.
        self._version.pop(guild.id, None)
        self._lugares.pop(guild.id, None)
        self._mundo_epoch[guild.id] = self._mundo_epoch.get(guild.id, 0) + 1
        self.bot.telemetry.count("universo", "publicar", guild_id=guild.id, user_id=member.id)
        self.salas.set_places(guild.id, await self.places(guild.id))
        await self.salas.announce(guild.id, {"t": "mundo", "version": version})
        # Publicar poda versiones viejas: los GLB que solo usaban ellas ya sobran.
        try:
            await repo.prune_glb()
        except Exception:
            logger.exception("No se han podido borrar los GLB sin usar")
        return version

    async def world_versions(self, guild: discord.Guild) -> list[dict[str, Any]]:
        """Las versiones publicadas para el editor: número, autor, fecha y tamaño en KB."""
        rows = await self.bot.universo_mundo.versions(guild.id)
        out = []
        for version, author_id, when, size in rows:
            author = guild.get_member(author_id)
            out.append(
                {
                    "version": version,
                    "autor": author.display_name if author else str(author_id),
                    "fecha": when,
                    "kb": max(1, (size + 1023) // 1024),
                }
            )
        return out

    async def revert_world(
        self, guild: discord.Guild, member: discord.Member, body: dict[str, Any]
    ) -> None:
        """Pone una versión publicada como borrador (la página lo recarga después).

        Raises:
            UniversoError: 404 si la versión no existe.
        """
        version = _int(body.get("version"), "version", 1)
        if not await self.bot.universo_mundo.revert(guild.id, version, member.id):
            raise UniversoError("Esa versión ya no existe.", 404)

    async def upload_glb(self, member: discord.Member, data: bytes) -> dict[str, Any]:
        """Comprueba y guarda un GLB; devuelve su resumen (`sha256`) y triángulos.

        Raises:
            UniversoError: 400 si no es un GLB válido, 507 si ya no caben más.
        """
        try:
            summary = await asyncio.to_thread(glb_summary, data)
        except MundoError as error:
            raise UniversoError(str(error)) from error
        try:
            await self.bot.universo_mundo.put_glb(
                summary.sha256, data, summary.triangles, member.id
            )
        except GlbFull as error:
            raise UniversoError(
                f"No caben más modelos: entre todos ya ocupan {MAX_GLB_TOTAL // 2**20} MB. "
                "Quita del mundo los que no uses y publica; se liberan al día siguiente.",
                507,
            ) from error
        return {"sha256": summary.sha256, "triangles": summary.triangles}


def _int(value: object, what: str, low: int) -> int:
    """Un entero del cuerpo (no un booleano) de `low` para arriba."""
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        raise UniversoError(f"«{what}» no es un número válido.")
    return value


def _decode_object(raw: bytes) -> dict[str, Any]:
    """Decodifica un cuerpo JSON que tiene que ser un objeto (para `asyncio.to_thread`)."""
    try:
        body = json.loads(raw)
    except ValueError as error:
        raise UniversoError("La petición no es JSON.") from error
    if not isinstance(body, dict):
        raise UniversoError("La petición no es un objeto JSON.")
    return body


async def _read_body(request: web.Request) -> bytes:
    """El cuerpo crudo de una petición del editor, hasta `MAX_EDITOR_BYTES`.

    aiohttp aplica a una subaplicación el `client_max_size` de la aplicación principal
    (16 KB), así que el tope del editor se pone leyendo el flujo a trozos: las demás
    rutas siguen con el suyo.

    Raises:
        UniversoError: 413 si el cuerpo es mayor.
    """
    too_big = UniversoError("La petición es demasiado grande.", 413)
    if (request.content_length or 0) > MAX_EDITOR_BYTES:
        raise too_big
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.content.iter_chunked(64 * 1024):
        size += len(chunk)
        if size > MAX_EDITOR_BYTES:
            raise too_big
        chunks.append(chunk)
    return b"".join(chunks)


async def _editor_call(
    request: web.Request, *, raw: bool = False
) -> tuple[Universo, discord.Guild, discord.Member, dict[str, Any]]:
    """Autoriza una petición del editor y da su cuerpo.

    La sesión se mira antes de leer el cuerpo, y el JSON grande se decodifica fuera
    del bucle. En `GET` y en la subida de GLB (`raw`), `guild_id` viene en la query y
    el cuerpo se deja sin leer.
    """
    cog = request.app[_COG_KEY]
    session = bearer(request.headers.get("Authorization"))
    if cog.signer.verify(session) is None:
        raise UniversoError("La sesión ha caducado. Vuelve a abrir el Jovaniverso.", 401)
    if raw or request.method == "GET":
        body: dict[str, Any] = dict(request.query)
    else:
        body = await asyncio.to_thread(_decode_object, await _read_body(request))
    guild, member = await cog.editor(session, body)
    return cog, guild, member, body


def _draft_text(pieces: dict[str, tuple[str, int]]) -> str:
    """Los trozos del borrador (`{clave: {doc, rev}}`), sin decodificar cada `doc`."""
    return (
        "{"
        + ",".join(
            f'{json.dumps(k)}:{{"doc":{doc or "null"},"rev":{rev}}}'
            for k, (doc, rev) in sorted(pieces.items())
        )
        + "}"
    )


def _index_text(index: DraftIndex, version: int) -> str:
    """El índice del borrador como JSON, con los documentos pegados tal cual."""
    dumps = json.dumps
    docs = ",".join(f"{dumps(k)}:{doc}" for k, doc in sorted(index.small.items()))
    return (
        f'{{"version":{version},"trozos":{dumps(index.alive, separators=(",", ":"))},'
        f'"borrados":{dumps(index.gone, separators=(",", ":"))},"docs":{{{docs}}}}}'
    )


def _keys(text: str) -> list[str]:
    """El parámetro `t` (`clave,clave,...`) de `/trozos`.

    Raises:
        UniversoError: 400 si no lleva de 1 a `MAX_LOTE` claves, repite una o alguna no vale.
    """
    keys = text.split(",") if text else []
    if not 1 <= len(keys) <= MAX_LOTE or len(set(keys)) < len(keys):
        raise UniversoError(f"Un lote lleva de 1 a {MAX_LOTE} trozos distintos.")
    try:
        for key in keys:
            parse_key(key)
    except MundoError as error:
        raise UniversoError(str(error)) from error
    return keys


def _json(text: str) -> web.Response:
    # Lo del borrador cambia con cada guardado: nunca a la caché.
    return web.Response(
        text=text, content_type="application/json", headers={"Cache-Control": "no-store"}
    )


async def _editor_indice(request: web.Request) -> web.Response:
    cog, guild, _member, _body = await _editor_call(request)
    return _json(await cog.draft_index(guild.id))


async def _editor_trozos(request: web.Request) -> web.Response:
    cog, guild, _member, body = await _editor_call(request)
    keys = _keys(str(body.get("t", "")))
    return _json(f'{{"trozos":{await cog.draft_pieces(guild.id, keys)}}}')


async def _editor_usos(request: web.Request) -> web.Response:
    cog, guild, _member, body = await _editor_call(request)
    asset = str(body.get("asset", ""))
    try:
        parse_key(f"a/{asset}")
    except MundoError as error:
        raise UniversoError(str(error)) from error
    cells = await cog.bot.universo_mundo.draft_users(guild.id, asset)
    return web.json_response({"celdas": cells})


async def _editor_guardar(request: web.Request) -> web.Response:
    cog, guild, member, body = await _editor_call(request)
    return web.json_response({"revs": await cog.save_world(guild, member, body)})


async def _editor_publicar(request: web.Request) -> web.Response:
    cog, guild, member, _body = await _editor_call(request)
    return web.json_response({"version": await cog.publish_world(guild, member)})


async def _editor_versiones(request: web.Request) -> web.Response:
    cog, guild, _member, _body = await _editor_call(request)
    return web.json_response({"versiones": await cog.world_versions(guild)})


async def _editor_volver(request: web.Request) -> web.Response:
    cog, guild, member, body = await _editor_call(request)
    await cog.revert_world(guild, member, body)
    return web.json_response({})


async def _editor_glb(request: web.Request) -> web.Response:
    cog, _guild, member, _body = await _editor_call(request, raw=True)
    return web.json_response(await cog.upload_glb(member, await _read_body(request)))
