"""Los permisos del Jovaniverso: quién edita el mundo y quién usa las devtools.

Permisos (`PERMISOS`), por servidor: `editor` (el editor del mundo) y
`devtools` (noclip, volar, velocidad y el extraplano de pruebas). Los desarrolladores del
bot (`DEVS`, por ID de Discord) los tienen todos siempre y son los únicos que los dan y
los quitan; ser administrador de Discord no da ninguno. `/api/entrar` devuelve `editor`,
`dev` (devtools) y `permisos` (si puede cambiarlos), y la sala del extraplano rechaza a
quien no tenga `devtools`. Lo que hacen las devtools pasa en el navegador; el servidor solo
guarda las puertas. Sin bromas ni logros.

- `GET /api/permisos?guild_id=[&q=]` -> `{permisos, usuarios, candidatos}`: los nombres de
  cada permiso, quién tiene alguno y, con `q`, hasta 10 miembros cuyo nombre lo contiene.
- `POST /api/permisos` `{guild_id, user_id, permiso, dar}` -> lo mismo que el `GET`.
  Las dos, solo para los desarrolladores.

Las comprobaciones las usan también el editor (`editor`) y la sala del extraplano
(`NOT_DEV`).
"""

from __future__ import annotations

from typing import Any

import discord
from aiohttp import web

from bot.cogs.universo.comun import _COG_KEY, _json_body
from bot.services.universo import (
    UniversoError,
    bearer,
    snowflake,
)

#: Los desarrolladores del bot (Yeyo y godzilin): los únicos con devtools y extraplano.
DEVS = frozenset({403646452414545921, 498473711687434241})
#: Los permisos que se dan en el Jovaniverso, con su nombre para el panel.
PERMISOS = {"editor": "Editar el mundo", "devtools": "Devtools y extraplano"}
#: Candidatos que devuelve la búsqueda de miembros del panel de permisos.
MAX_CANDIDATOS = 10
#: Quien no tiene `devtools` no entra en el extraplano.
NOT_DEV = "El mundo de pruebas es solo para quien tenga permiso de devtools."
#: Quien no tiene `editor` no toca el mundo.
NOT_ADMIN = "Solo quien tenga permiso de editor puede editar el mundo."
#: Solo los desarrolladores cambian los permisos.
NOT_OWNER = "Solo los desarrolladores del bot cambian los permisos."


class PermissionsMixin:
    """Permisos del cog `Universo`: quién tiene cuál y darlos o quitarlos."""

    async def permisos(self, guild_id: int, user_id: int) -> set[str]:
        """Los permisos de un miembro: todos si es desarrollador, si no los que le dieron."""
        if user_id in DEVS:
            return set(PERMISOS)
        return await self.bot.universo_mundo.permissions(guild_id, user_id)

    async def editor(
        self, session: str | None, body: dict[str, Any]
    ) -> tuple[discord.Guild, discord.Member]:
        """Servidor y miembro de una llamada del editor; solo vale quien tiene `editor`.

        Raises:
            UniversoError: Como `_member`, y 403 sin el permiso.
        """
        guild, member = self._member(session, body)
        if "editor" not in await self.permisos(guild.id, member.id):
            raise UniversoError(NOT_ADMIN, 403)
        return guild, member

    async def permission_list(self, guild: discord.Guild, query: str = "") -> dict[str, Any]:
        """Lo que enseña el panel de permisos: nombres, quién tiene alguno y la búsqueda."""
        usuarios: dict[int, list[str]] = {}
        for user_id, permiso in await self.bot.universo_mundo.granted(guild.id):
            usuarios.setdefault(user_id, []).append(permiso)

        def card(member: discord.Member | None, user_id: int) -> dict[str, Any]:
            nombre = member.display_name if member is not None else f"Ya no está ({user_id})"
            return {"id": str(user_id), "nombre": nombre}

        found: list[dict[str, Any]] = []
        query = query.strip().casefold()[:40]
        if query:
            for member in getattr(guild, "members", []):
                names = (member.display_name.casefold(), member.name.casefold())
                if not member.bot and member.id not in DEVS and any(query in n for n in names):
                    found.append(card(member, member.id))
                    if len(found) >= MAX_CANDIDATOS:
                        break
        return {
            "permisos": PERMISOS,
            "usuarios": [
                {**card(guild.get_member(uid), uid), "permisos": perms}
                for uid, perms in usuarios.items()
            ],
            "candidatos": found,
        }

    async def set_permission(
        self, guild: discord.Guild, author: discord.Member, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Da o quita un permiso a un miembro (solo los desarrolladores llegan aquí).

        Raises:
            UniversoError: Permiso que no existe, alguien que no es del servidor o un
                desarrollador (los tiene todos y no se le quitan).
        """
        permiso = body.get("permiso")
        if permiso not in PERMISOS:
            raise UniversoError("Ese permiso no existe.")
        user_id = snowflake(body.get("user_id"))
        member = guild.get_member(user_id) if user_id is not None else None
        dar = body.get("dar") is True
        # Quitar se deja aunque ya no sea del servidor, para poder limpiar la lista.
        if user_id is None or (dar and (member is None or member.bot)):
            raise UniversoError("Esa persona no es miembro de este servidor.")
        if user_id in DEVS:
            raise UniversoError("Los desarrolladores tienen todos los permisos siempre.")
        await self.bot.universo_mundo.set_permission(guild.id, user_id, permiso, dar, author.id)
        self.bot.telemetry.count("universo", "permiso", guild_id=guild.id, user_id=author.id)
        return await self.permission_list(guild)


async def _permisos(request: web.Request) -> web.Response:
    cog = request.app[_COG_KEY]
    session = bearer(request.headers.get("Authorization"))
    body = dict(request.query) if request.method == "GET" else await _json_body(request)
    guild, member = cog._member(session, body)
    if member.id not in DEVS:
        raise UniversoError(NOT_OWNER, 403)
    if request.method == "GET":
        return web.json_response(await cog.permission_list(guild, str(body.get("q") or "")))
    return web.json_response(await cog.set_permission(guild, member, body))
