"""Login y sesiones del Jovaniverso: la página, el login con Discord y la entrada.

- `GET /`: la página (`assets/universo/`, la compila Vite desde `universo/`).
- `GET /api/config`: el ID de la aplicación, que el SDK necesita para empezar.
- `POST /api/token` `{code}`: login. Cambia el código de `authorize` por un token
  (`bot.services.universo.DiscordOAuth`) y da una sesión firmada.
- `POST /api/entrar` `{guild_id, channel_id, platform}` con la sesión en
  `Authorization: Bearer`: el perfil (nombre y saldo), los gestos comprados
  (`gestures`, sus animaciones), lo que necesitan las tragaperras del mundo (`tragas`:
  tiras, símbolos, cómo está parada cada máquina y el bote), los botes de los letreros
  (`botes`) y los logros de entrar. Las tragaperras y los letreros son objetos del mundo
  (`bot.services.universo_editor`, `funcion`), así que dónde están no lo dice el servidor.

Todas las demás rutas comprueban la sesión con `SessionMixin._member`.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

import aiohttp
import discord
from aiohttp import web

from bot import __version__
from bot.cogs import achievements as logros
from bot.cogs import shop as tienda
from bot.cogs import slots as tragas
from bot.cogs.universo.comun import _COG_KEY, _json_body
from bot.cogs.universo.permisos import DEVS
from bot.services.achievements import universo_stats
from bot.services.economy import format_amount
from bot.services.levels import TIMEZONE
from bot.services.universo import (
    DiscordOAuth,
    UniversoError,
    bearer,
    gestures_owned,
    platform_of,
    snowflake,
)

#: Cuando no está compilada la página (en local, antes de `npm run build`).
NOT_BUILT = "La página del Jovaniverso no está compilada. Ejecuta `npm run build` en `universo/`."


class SessionMixin:
    """Login, entrada y la comprobación de sesión del cog `Universo`."""

    def oauth(self) -> DiscordOAuth:
        """El login con Discord, con la sesión HTTP del cog."""
        if self.session is None:
            self.session = aiohttp.ClientSession()
        if self.bot.application_id is None:
            raise UniversoError("El bot aún está arrancando. Prueba en un momento.", 503)
        return DiscordOAuth(
            self.session,
            client_id=self.bot.application_id,
            client_secret=self.client_secret,
            api=self.api,
        )

    async def login(self, code: object) -> dict[str, str]:
        """Hace el login y devuelve el token de Discord y la sesión del universo."""
        started = time.perf_counter()
        login = await self.oauth().exchange(code)
        self.bot.telemetry.count(
            "universo",
            "login",
            guild_id=None,
            user_id=login.user_id,
            ms=(time.perf_counter() - started) * 1000,
        )
        return {"access_token": login.access_token, "session": self.signer.issue(login.user_id)}

    async def enter(self, session: str | None, body: dict[str, Any]) -> dict[str, Any]:
        """Perfil de quien entra en el universo; apunta los logros después.

        Raises:
            UniversoError: Sin sesión válida (401), sin servidor (400) o si quien
                entra no es miembro del servidor donde se abre (403).
        """
        guild, member = self._member(session, body)
        self._last_walk[guild.id, member.id] = time.monotonic()
        balance = await self.bot.economy.balance(guild.id, member.id)
        gestures = gestures_owned(await tienda.owned_keys(self.bot, guild.id, member.id))
        permisos = await self.permisos(guild.id, member.id)
        channel_id = snowflake(body.get("channel_id"))
        channel = guild.get_channel_or_thread(channel_id) if channel_id is not None else None
        delta = universo_stats(platform_of(body.get("platform")), when=datetime.now(TIMEZONE))
        self.bot.telemetry.count("universo", "entrar", guild_id=guild.id, user_id=member.id)
        # Los logros pueden avisar en el canal: no hacen esperar a la página.
        task = asyncio.create_task(logros.track(self.bot, guild.id, member, channel, delta))
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return {
            "name": member.display_name,
            "guild": guild.name,
            "balance": balance,
            "balance_text": format_amount(balance),
            "gestures": sorted(gestures),
            "version": __version__,
            "editor": "editor" in permisos,
            "dev": "devtools" in permisos,
            "permisos": member.id in DEVS,
            "mundo": await self.world_version(guild.id),
            # La fila de tragaperras de la plaza (`None` si la tragaperras no está cargada).
            "tragas": await tragas.plaza_meta(self.bot, guild.id),
            "botes": await self.pots(guild.id),
        }

    def _member(
        self, session: str | None, body: dict[str, Any]
    ) -> tuple[discord.Guild, discord.Member]:
        """Servidor y miembro de una llamada de la página, comprobando la sesión."""
        user_id = self.signer.verify(session)
        if user_id is None:
            raise UniversoError("La sesión ha caducado. Vuelve a abrir el Jovaniverso.", 401)
        guild_id = snowflake(body.get("guild_id"))
        guild = self.bot.get_guild(guild_id) if guild_id is not None else None
        if guild is None:
            raise UniversoError("El Jovaniverso solo se abre dentro de un servidor del bot.")
        member = guild.get_member(user_id)
        if member is None or member.bot:
            raise UniversoError("No eres miembro de este servidor.", 403)
        return guild, member


async def _index(request: web.Request) -> web.StreamResponse:
    cog = request.app[_COG_KEY]
    index = cog.static_dir / "index.html"
    if not index.is_file():
        return web.Response(text=NOT_BUILT, status=503)
    # Sin caché: tras desplegar, la página nueva tiene que llegar ya. Los archivos de
    # `assets/` llevan un hash en el nombre, así que esos sí se pueden guardar.
    return web.FileResponse(index, headers={"Cache-Control": "no-cache"})


async def _config(request: web.Request) -> web.Response:
    cog = request.app[_COG_KEY]
    if cog.bot.application_id is None:
        raise UniversoError("El bot aún está arrancando. Prueba en un momento.", 503)
    return web.json_response({"client_id": str(cog.bot.application_id)})


async def _token(request: web.Request) -> web.Response:
    cog = request.app[_COG_KEY]
    body = await _json_body(request)
    return web.json_response(await cog.login(body.get("code")))


async def _entrar(request: web.Request) -> web.Response:
    cog = request.app[_COG_KEY]
    body = await _json_body(request)
    return web.json_response(await cog.enter(bearer(request.headers.get("Authorization")), body))
