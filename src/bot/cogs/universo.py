"""El Jovaniverso (`universo`): el bot dentro de una Activity de Discord.

Una Activity es una página web que Discord abre en un iframe, por encima del chat
o de la llamada. Esta es la prueba de concepto: la página hace login con Discord
y enseña tu nombre y tu saldo, leídos de la base de datos de verdad. Lo que venga
después (el mundo, el personaje, el casino) se monta sobre estas mismas piezas.

Cómo se abre:

- `/universo` es el comando de entrada de la Activity. No es un slash command de
  discord.py: Discord lo crea al activar las Activities en el portal y
  `bot.app.BotTree` lo renombra y lo mantiene al sincronizar. Discord abre la
  Activity sin pasar por el bot.
- `.universo` contesta con un botón que la abre (`launch_activity`), porque un
  mensaje de texto no puede abrirla por sí solo. Sin `DISCORD_CLIENT_SECRET`, el
  botón avisa de que el universo aún no está abierto.

El servidor web (aiohttp, dentro del mismo proceso que el bot, para usar la misma
base de datos y los mismos servicios) solo se abre con `DISCORD_CLIENT_SECRET`.
Escucha en `UNIVERSO_PORT` y Discord le llega por el proxy de las Activities (la
URL Mapping del portal apunta al túnel del NAS). Rutas:

- `GET /`: la página (`assets/universo/`, la compila Vite desde `universo/`).
- `GET /api/config`: el ID de la aplicación, que el SDK necesita para empezar.
- `POST /api/token` `{code}`: login. Cambia el código de `authorize` por un token
  (`bot.services.universo.DiscordOAuth`) y da una sesión firmada.
- `POST /api/entrar` `{guild_id, channel_id, platform}` con la sesión en
  `Authorization: Bearer`: el perfil (nombre y saldo) y los logros de entrar.
- `POST /api/paseo` `{guild_id, channel_id, meters, jumps, places}`, con la sesión:
  lo que has andado, saltado y pisado en el mundo (Gran Canaria) desde el informe
  anterior, para los logros. La página lo manda cada 15 s; el servidor lo recorta a
  lo que da tiempo a hacer (`bot.services.universo.walk_report`).
- `GET /api/sala` (WebSocket): el multijugador. El primer mensaje es JSON
  `{t: "hola", session, guild_id}`; después, poses en binario. El reparto lo hace
  `bot.services.universo_sala.Salas` (una sala por servidor, ticks fijos y binarios).

La página no decide nada: todo lo que enseña sale de aquí, y lo que algún día mueva
dinero se decidirá aquí con `EconomyService`, nunca en el navegador.

Logros: entrar (`universo_stats`, días distintos, móvil, ordenador, madrugada y
Día de Canarias), pasear (`universo_walk_stats`, metros, saltos y lugares) y
coincidir con otros (`universo_meet_stats`, al entrar en la sala), categoría
🎤 Jovani. La mascota no sale: la página no es un mensaje de Discord y, de momento,
no enseña ningún resultado en el que pueda hablar.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp
import discord
from aiohttp import WSMsgType, web
from discord import ui
from discord.ext import commands

from bot import __version__
from bot.cogs import achievements as logros
from bot.services.achievements import (
    universo_meet_stats,
    universo_stats,
    universo_walk_stats,
)
from bot.services.economy import format_amount
from bot.services.levels import TIMEZONE
from bot.services.universo import (
    DISCORD_API,
    DiscordOAuth,
    SessionSigner,
    UniversoError,
    bearer,
    platform_of,
    snowflake,
    walk_report,
)
from bot.services.universo_sala import Player, SalaError, Salas

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

#: Carpeta de la página compilada (`npm run build` en `universo/`).
STATIC_DIR = Path(__file__).resolve().parent.parent / "assets" / "universo"
#: `custom_id` del botón de `.universo`. Fijo: la vista es persistente y el botón
#: sigue valiendo tras un reinicio.
OPEN_ID = "universo:abrir"
#: Lo más que acepta el servidor en el cuerpo de una petición. El login y la entrada
#: son unos pocos campos; nada justifica más.
MAX_BODY_BYTES = 16 * 1024
#: Dirección en que escucha el servidor. En Docker hay que escuchar en todas para
#: que el túnel, en otro contenedor, llegue; el puerto no se publica fuera.
LISTEN_HOST = "0.0.0.0"  # noqa: S104

#: Segundos que tiene la página para decir quién es al abrir el multijugador.
HELLO_SECONDS = 10
#: Lo más que acepta el multijugador en un mensaje: el saludo es lo más grande.
MAX_WS_BYTES = 1024
#: Latido del multijugador: una conexión que no contesta en este tiempo se cierra.
HEARTBEAT_SECONDS = 20

#: Clave de la aplicación de aiohttp donde vive el cog.
_COG_KEY: web.AppKey[Universo] = web.AppKey("universo")

#: Texto de `.universo`.
INTRO = (
    "🌌 **Jovaniverso**\n"
    "El bot en una Activity, dentro de Discord. De momento es una prueba: entras, te "
    "reconoce y te enseña tu saldo. Lo demás irá llegando."
)
CLOSED = "🌌 El Jovaniverso aún no está abierto en este servidor."
NO_ACTIVITY = (
    "🌌 Discord no ha dejado abrir el Jovaniverso aquí. Prueba desde un canal del servidor."
)
#: Cuando no está compilada la página (en local, antes de `npm run build`).
NOT_BUILT = "La página del Jovaniverso no está compilada. Ejecuta `npm run build` en `universo/`."


class OpenView(ui.View):
    """Botón persistente que abre la Activity desde `.universo`."""

    def __init__(self, cog: Universo) -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @ui.button(
        label="Abrir el Jovaniverso",
        emoji="🌌",
        style=discord.ButtonStyle.primary,
        custom_id=OPEN_ID,
    )
    async def open(self, interaction: discord.Interaction, _button: ui.Button[OpenView]) -> None:
        """Abre la Activity. Solo mira memoria, así que contesta directamente."""
        if not self.cog.is_open:
            await interaction.response.send_message(CLOSED, ephemeral=True)
            return
        try:
            await interaction.response.launch_activity()
        except discord.HTTPException as error:
            # Sin Activities en el portal o en un sitio donde no se pueden abrir (un MD).
            logger.info("No se pudo abrir la Activity: %s", error)
            if not interaction.response.is_done():
                await interaction.response.send_message(NO_ACTIVITY, ephemeral=True)


class Universo(commands.Cog, name="Universo"):
    """Comando `universo` y servidor web de la Activity.

    Args:
        bot: Cliente del bot.
        client_secret: Secreto de OAuth2. Vacío, no se abre el servidor.
        port: Puerto del servidor web.
        static_dir: Carpeta de la página compilada.
        api: Base de la API de Discord para el login (las pruebas ponen una falsa).
    """

    def __init__(
        self,
        bot: BotClient,
        *,
        client_secret: str,
        port: int,
        static_dir: Path = STATIC_DIR,
        api: str = DISCORD_API,
    ) -> None:
        self.bot = bot
        self.client_secret = client_secret
        self.port = port
        self.static_dir = static_dir
        self.api = api
        self.signer = SessionSigner()
        self.session: aiohttp.ClientSession | None = None
        self.runner: web.AppRunner | None = None
        # Logros que se apuntan después de contestar a la página: se guardan para que
        # el recolector no se los lleve a medias y para esperarlos al descargar.
        self._background: set[asyncio.Task[None]] = set()
        # Último informe de paseo (o entrada) de cada miembro, en segundos de
        # `time.monotonic`: dice cuánto ha podido andar desde entonces.
        self._last_walk: dict[tuple[int, int], float] = {}
        self.salas = Salas()

    @property
    def is_open(self) -> bool:
        """Si el universo está configurado (hay secreto de OAuth2)."""
        return bool(self.client_secret)

    async def cog_load(self) -> None:
        """Registra el botón persistente y abre el servidor web si hay secreto."""
        self.bot.add_view(OpenView(self))
        if not self.is_open:
            logger.info("Sin DISCORD_CLIENT_SECRET: el Jovaniverso no abre su servidor web")
            return
        self.session = aiohttp.ClientSession()
        runner = web.AppRunner(self.build_app(), access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner, LISTEN_HOST, self.port).start()
        except OSError:
            # Un puerto ocupado no debe tumbar el bot entero: el resto sigue igual.
            logger.exception(
                "No se pudo abrir el servidor del Jovaniverso en el puerto %d", self.port
            )
            await runner.cleanup()
            return
        self.runner = runner
        logger.info("Servidor del Jovaniverso escuchando en el puerto %d", self.port)

    async def cog_unload(self) -> None:
        """Cierra el servidor web, la sesión HTTP y espera los logros pendientes."""
        await self.salas.close()
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None
        if self._background:
            await asyncio.gather(*self._background, return_exceptions=True)
        if self.session is not None:
            await self.session.close()
            self.session = None

    def build_app(self) -> web.Application:
        """Crea la aplicación web con sus rutas (las pruebas la usan sin abrir puerto)."""
        app = web.Application(client_max_size=MAX_BODY_BYTES, middlewares=[_errors])
        app[_COG_KEY] = self
        app.router.add_get("/", _index)
        app.router.add_get("/api/config", _config)
        app.router.add_post("/api/token", _token)
        app.router.add_post("/api/entrar", _entrar)
        app.router.add_post("/api/paseo", _paseo)
        app.router.add_get("/api/sala", _sala)
        assets = self.static_dir / "assets"
        if assets.is_dir():
            app.router.add_static("/assets", assets)
        return app

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
            "version": __version__,
        }

    def walk(self, session: str | None, body: dict[str, Any]) -> None:
        """Apunta para los logros lo que la página dice que has paseado.

        Solo cuenta lo que da tiempo a hacer desde el informe anterior (o desde que
        entraste); si el bot se ha reiniciado entre medias, nada hasta el siguiente.

        Raises:
            UniversoError: Igual que `enter`.
        """
        guild, member = self._member(session, body)
        now = time.monotonic()
        last = self._last_walk.get((guild.id, member.id), now)
        self._last_walk[guild.id, member.id] = now
        meters, jumps, places = walk_report(body, now - last)
        delta = universo_walk_stats(meters, jumps, places)
        if delta.add:
            channel_id = snowflake(body.get("channel_id"))
            logros.note(self.bot, guild.id, member.id, delta, channel_id)

    async def play(self, ws: web.WebSocketResponse, backlog: Callable[[], int]) -> None:
        """Atiende el multijugador de una página hasta que se va.

        El primer mensaje dice quién es (la misma sesión que `enter`); si no vale, se
        contesta `{t: "error"}` y se cierra. Luego cada mensaje binario es una pose.
        """
        try:
            first = await ws.receive(timeout=HELLO_SECONDS)
            hello = json.loads(first.data) if first.type == WSMsgType.TEXT else None
        except (TimeoutError, ValueError):
            hello = None
        if not isinstance(hello, dict) or hello.get("t") != "hola":
            await ws.close()
            return
        try:
            guild, member = self._member(hello.get("session"), hello)
            player = await self.salas.join(guild.id, member.id, member.display_name, ws, backlog)
        except (UniversoError, SalaError) as error:
            text = error.message if isinstance(error, UniversoError) else str(error)
            await ws.send_json({"t": "error", "error": text})
            await ws.close()
            return
        self.bot.telemetry.count("universo", "sala", guild_id=guild.id, user_id=member.id)
        self._meet(guild.id, player)
        try:
            async for message in ws:
                if message.type == WSMsgType.BINARY:
                    self.salas.pose(guild.id, player, message.data)
        finally:
            await self.salas.leave(guild.id, player)

    def _meet(self, guild_id: int, player: Player) -> None:
        """Logros de coincidir: quien entra y cada uno de los que ya estaban."""
        present = self.salas.present(guild_id)
        others = [p for p in present if p is not player]
        joined = universo_meet_stats([p.user_id for p in others], len(present))
        logros.note(self.bot, guild_id, player.user_id, joined)
        for other in others:
            seen = universo_meet_stats([player.user_id], len(present))
            logros.note(self.bot, guild_id, other.user_id, seen)

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

    @commands.command(name="universo")
    @commands.guild_only()
    async def universo_text(self, ctx: commands.Context) -> None:
        """Abre el Jovaniverso, el bot en una Activity dentro de Discord.

        Con `/` es el comando de entrada de la Activity (lo abre Discord); con `.`,
        un botón que la abre.
        """
        await ctx.send(INTRO, view=OpenView(self))


@web.middleware
async def _errors(request: web.Request, handler: Any) -> web.StreamResponse:
    """Convierte los fallos en JSON `{error}` sin trazas ni datos internos."""
    try:
        return await handler(request)
    except UniversoError as error:
        return web.json_response({"error": error.message}, status=error.status)
    except web.HTTPException:
        raise
    except Exception:
        logger.exception("Fallo en el servidor del Jovaniverso (%s)", request.path)
        return web.json_response({"error": "Algo ha fallado en el Jovaniverso."}, status=500)


async def _json_body(request: web.Request) -> dict[str, Any]:
    """El cuerpo JSON de la petición, que tiene que ser un objeto."""
    try:
        body = await request.json()
    except ValueError as error:
        raise UniversoError("La petición no es JSON.") from error
    if not isinstance(body, dict):
        raise UniversoError("La petición no es un objeto JSON.")
    return body


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


async def _paseo(request: web.Request) -> web.Response:
    cog = request.app[_COG_KEY]
    body = await _json_body(request)
    cog.walk(bearer(request.headers.get("Authorization")), body)
    return web.json_response({"ok": True})


async def _sala(request: web.Request) -> web.WebSocketResponse:
    cog = request.app[_COG_KEY]
    ws = web.WebSocketResponse(heartbeat=HEARTBEAT_SECONDS, max_msg_size=MAX_WS_BYTES)
    await ws.prepare(request)
    transport = request.transport
    await cog.play(ws, lambda: transport.get_write_buffer_size() if transport else 0)
    return ws


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con el secreto de OAuth2 y el puerto de la configuración."""
    await bot.add_cog(Universo(bot, client_secret=bot.client_secret, port=bot.universo_port))
