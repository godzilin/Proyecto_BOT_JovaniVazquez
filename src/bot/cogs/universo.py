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
  `Authorization: Bearer`: el perfil (nombre y saldo), los gestos comprados
  (`gestures`, sus animaciones), la fila de tragaperras de la plaza (`tragas`: tiras,
  símbolos, cómo está parada cada máquina y el bote) y los logros de entrar.
- `POST /api/paseo` `{guild_id, channel_id, meters, jumps, places}`, con la sesión:
  lo que has andado, saltado y pisado en el mundo (Gran Canaria) desde el informe
  anterior, para los logros. La página lo manda cada 15 s; el servidor lo recorta a
  lo que da tiempo a hacer (`bot.services.universo.walk_report`).
- `GET /api/mundo?guild_id=` con la sesión de cualquier miembro: `{version, doc}`, el
  mundo publicado (o `{version: 0, doc: EMPTY}`). El texto sale ya codificado y se guarda
  en memoria por servidor hasta que alguien publica.
- `GET /api/glb/{sha}`: un modelo GLB por el SHA-256 de su contenido, sin sesión (es
  contenido que no cambia: `immutable`).
- `POST /api/traga` `{guild_id, channel_id, machine, stake, auto}`, con la sesión:
  una tirada en una tragaperras de la plaza (`bot.cogs.slots.plaza`, el mismo dinero,
  calor por máquina, logros y anuncios que `tragas`). Devuelve los fotogramas de la
  animación (`spin_states`), que pinta la página. Con `{..., "parar": true}`, para el
  ▶️ Auto de esa máquina. Las reglas (máquinas a la vez, ritmo, Auto) están en
  `bot.services.universo_tragas`.
- `GET /api/sala` (WebSocket): el multijugador. El primer mensaje es JSON
  `{t: "hola", session, guild_id}`; después, poses en binario. El reparto lo hace
  `bot.services.universo_sala.Salas` (una sala por servidor, ticks fijos y binarios).
  Los gestos del robot se compran en la tienda (`gesto_*`, `bot.services.universo.GESTURES`)
  y se leen al entrar en la sala: uno comprado con el universo abierto se ve al volver
  a abrirlo.

El editor del mundo vive en una subaplicación montada en `/api/editor/`, con su propio
tope de cuerpo (`MAX_EDITOR_BYTES`: cabe un GLB; el resto de rutas sigue con
`MAX_BODY_BYTES`). Todas sus rutas llevan la sesión y `guild_id` (en el cuerpo JSON o,
en `GET` y en la subida de GLB, en la query) y el servidor comprueba siempre que quien
llama es administrador. Es una herramienta de administración: sin bromas ni logros.

- `GET /api/editor/borrador` -> `{doc, rev, version}`: el borrador (o lo publicado, o
  `EMPTY`), su `rev` y la última versión publicada.
- `PUT /api/editor/borrador` `{guild_id, rev, doc}` -> `{rev}`: valida
  (`bot.services.universo_editor`) y guarda si el `rev` es el actual; si no, 409.
- `POST /api/editor/publicar` `{guild_id, rev}` -> `{version}`: publica el borrador y
  avisa a la sala (`{t: "mundo", version}`) para que las páginas recarguen.
- `GET /api/editor/versiones` -> `{versiones: [{version, autor, fecha, kb}]}`.
- `POST /api/editor/volver` `{guild_id, rev, version}` -> `{rev}`: pone una versión
  vieja como borrador.
- `POST /api/editor/glb` (cuerpo `octet-stream`) -> `{sha256, triangles}`: sube un modelo.

La página no decide nada: todo lo que enseña sale de aquí, y el dinero (las tragaperras
de la plaza) se decide aquí con `EconomyService`, nunca en el navegador.

Logros: entrar (`universo_stats`, días distintos, móvil, ordenador, madrugada y
Día de Canarias), pasear (`universo_walk_stats`, metros, saltos y lugares),
coincidir con otros (`universo_meet_stats`, al entrar en la sala), y gestos
(`universo_gesture_stats`, al empezar cada uno) y las tragaperras de la plaza
(`universo_slots_stats`), categoría 🎤 Jovani. La mascota solo habla en el resultado de
las tragaperras (`renta.hint`, que pide el cog de la tragaperras).
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import re
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
from bot.cogs import shop as tienda
from bot.cogs import slots as tragas
from bot.services.achievements import (
    StatDelta,
    universo_gesture_stats,
    universo_meet_stats,
    universo_stats,
    universo_walk_stats,
)
from bot.services.economy import format_amount
from bot.services.levels import TIMEZONE
from bot.services.universo import (
    DISCORD_API,
    GESTURES,
    DiscordOAuth,
    SessionSigner,
    UniversoError,
    bearer,
    gestures_owned,
    on_summit,
    platform_of,
    snowflake,
    walk_report,
)
from bot.services.universo_editor import (
    EMPTY,
    MAX_DOC_BYTES,
    MAX_GLB_BYTES,
    MundoError,
    encode_world,
    glb_refs,
    glb_summary,
    validate_world,
)
from bot.services.universo_sala import Player, SalaError, Salas, place
from bot.services.universo_tragas import SeatError

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
#: Cuerpo máximo de las rutas del editor: lo más grande que acepta (un mundo o un GLB) con
#: margen para el resto del JSON. El tope real de cada cosa lo ponen `encode_world` y
#: `glb_summary`.
MAX_EDITOR_BYTES = max(MAX_GLB_BYTES, MAX_DOC_BYTES) + 64 * 1024

#: Segundos que tiene la página para decir quién es al abrir el multijugador.
HELLO_SECONDS = 10
#: Lo más que acepta el multijugador en un mensaje: el saludo es lo más grande.
MAX_WS_BYTES = 1024
#: Latido del multijugador: una conexión que no contesta en este tiempo se cierra.
HEARTBEAT_SECONDS = 20

#: Clave de la aplicación de aiohttp donde vive el cog.
_COG_KEY: web.AppKey[Universo] = web.AppKey("universo")

#: Aviso del 409 cuando el `rev` del editor ya no es el del borrador.
REV_CONFLICT = (
    "Otro administrador ha guardado el borrador mientras tanto. Recarga para ver sus cambios."
)
#: Quien no es administrador no toca el mundo.
NOT_ADMIN = "Solo los administradores pueden editar el mundo."
_SHA256 = re.compile(r"[0-9a-f]{64}")
#: El mundo vacío ya codificado, para servirlo sin pasar por el JSON.
_EMPTY_TEXT = json.dumps(EMPTY, separators=(",", ":"))

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
        self.salas = Salas(on_gesture=self._gesture)
        # Mundo publicado de cada servidor (versión y texto ya codificado), para no ir
        # a la base en cada entrada. `_mundo_epoch` cuenta las publicaciones: una lectura
        # que empezó antes de publicar no guarda lo viejo.
        self._mundo: dict[int, tuple[int, str]] = {}
        self._mundo_epoch: dict[int, int] = {}
        # El mismo mundo comprimido con gzip, por servidor y versión: se comprime una vez
        # por publicación y fuera del bucle, no en cada entrada.
        self._mundo_gz: dict[int, tuple[int, bytes]] = {}

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
        app.router.add_get("/api/mundo", _mundo)
        app.router.add_get("/api/glb/{sha}", _glb)
        app.router.add_post("/api/traga", _traga)
        app.router.add_get("/api/sala", _sala)
        app.add_subapp("/api/editor/", self.build_editor())
        assets = self.static_dir / "assets"
        if assets.is_dir():
            app.router.add_static("/assets", assets)
        return app

    def build_editor(self) -> web.Application:
        """La subaplicación del editor, con un tope de cuerpo mayor que el del resto."""
        app = web.Application(client_max_size=MAX_EDITOR_BYTES, middlewares=[_errors])
        app[_COG_KEY] = self
        app.router.add_get("/borrador", _editor_borrador)
        app.router.add_put("/borrador", _editor_guardar)
        app.router.add_post("/publicar", _editor_publicar)
        app.router.add_get("/versiones", _editor_versiones)
        app.router.add_post("/volver", _editor_volver)
        app.router.add_post("/glb", _editor_glb)
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
        gestures = gestures_owned(await tienda.owned_keys(self.bot, guild.id, member.id))
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
            "admin": member.guild_permissions.administrator,
            "mundo": (await self.mundo(guild.id))[0],
            # La fila de tragaperras de la plaza (`None` si la tragaperras no está cargada).
            "tragas": await tragas.plaza_meta(self.bot, guild.id),
        }

    async def mundo(self, guild_id: int) -> tuple[int, str]:
        """Versión publicada de un servidor y su documento ya codificado (0 y vacío si no hay).

        Se guarda en memoria hasta que `publish_world` la borra.
        """
        cached = self._mundo.get(guild_id)
        if cached is None:
            epoch = self._mundo_epoch.get(guild_id, 0)
            cached = await self.bot.universo_mundo.published(guild_id) or (0, _EMPTY_TEXT)
            if self._mundo_epoch.get(guild_id, 0) == epoch:
                self._mundo[guild_id] = cached
        return cached

    def admin(
        self, session: str | None, body: dict[str, Any]
    ) -> tuple[discord.Guild, discord.Member]:
        """Servidor y miembro de una llamada del editor; solo valen los administradores.

        Raises:
            UniversoError: Como `_member`, y 403 si no es administrador.
        """
        guild, member = self._member(session, body)
        if not member.guild_permissions.administrator:
            raise UniversoError(NOT_ADMIN, 403)
        return guild, member

    async def draft_world(self, guild_id: int) -> tuple[str, int, int]:
        """Lo que carga el editor: documento (borrador, publicado o vacío), `rev` y versión."""
        text, rev = await self.bot.universo_mundo.draft(guild_id)
        version, published = await self.mundo(guild_id)
        return text if text is not None else published, rev, version

    async def save_world(
        self, guild: discord.Guild, member: discord.Member, body: dict[str, Any]
    ) -> int:
        """Valida y guarda el borrador; devuelve el `rev` nuevo.

        Raises:
            UniversoError: 400 si el mundo no vale, 409 si el `rev` ya no es el actual.
        """
        repo = self.bot.universo_mundo
        doc = body.get("doc")
        rev = _int(body.get("rev"), "rev", 0)
        glbs = await repo.glb_triangles(glb_refs(doc))
        try:
            text = await asyncio.to_thread(lambda: encode_world(validate_world(doc, glbs)))
        except MundoError as error:
            raise UniversoError(str(error)) from error
        saved = await repo.save_draft(guild.id, text, rev, member.id)
        if saved is None:
            raise UniversoError(REV_CONFLICT, 409)
        return saved

    async def publish_world(
        self, guild: discord.Guild, member: discord.Member, body: dict[str, Any]
    ) -> int:
        """Publica el borrador, limpia la caché y avisa a la sala; devuelve la versión.

        Raises:
            UniversoError: 400 si no hay borrador, 409 si el `rev` ya no es el actual.
        """
        repo = self.bot.universo_mundo
        version = await repo.publish(guild.id, _int(body.get("rev"), "rev", 0), member.id)
        if version is None:
            text, _rev = await repo.draft(guild.id)
            if text is None:
                raise UniversoError("No hay borrador que publicar.")
            raise UniversoError(REV_CONFLICT, 409)
        self._mundo.pop(guild.id, None)
        self._mundo_epoch[guild.id] = self._mundo_epoch.get(guild.id, 0) + 1
        self.bot.telemetry.count("universo", "publicar", guild_id=guild.id, user_id=member.id)
        await self.salas.announce(guild.id, {"t": "mundo", "version": version})
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
    ) -> int:
        """Pone una versión publicada como borrador; devuelve el `rev` nuevo.

        Raises:
            UniversoError: 404 si la versión no existe, 409 si el `rev` ya no es el actual.
        """
        repo = self.bot.universo_mundo
        doc = await repo.version_doc(guild.id, _int(body.get("version"), "version", 1))
        if doc is None:
            raise UniversoError("Esa versión ya no existe.", 404)
        saved = await repo.save_draft(guild.id, doc, _int(body.get("rev"), "rev", 0), member.id)
        if saved is None:
            raise UniversoError(REV_CONFLICT, 409)
        return saved

    async def upload_glb(self, member: discord.Member, data: bytes) -> dict[str, Any]:
        """Comprueba y guarda un GLB; devuelve su resumen (`sha256`) y triángulos.

        Raises:
            UniversoError: 400 si no es un GLB válido.
        """
        try:
            summary = await asyncio.to_thread(glb_summary, data)
        except MundoError as error:
            raise UniversoError(str(error)) from error
        await self.bot.universo_mundo.put_glb(summary.sha256, data, summary.triangles, member.id)
        return {"sha256": summary.sha256, "triangles": summary.triangles}

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

    async def slot(self, session: str | None, body: dict[str, Any]) -> dict[str, Any]:
        """Una tirada (o ⏹️ parar el Auto) en una tragaperras de la plaza.

        Raises:
            UniversoError: Igual que `enter`; con 400 si los datos no valen o la tirada
                no se puede jugar (saldo, ritmo, máquinas a la vez), con su texto.
        """
        guild, member = self._member(session, body)
        machine, stake = body.get("machine"), body.get("stake", 0)
        if type(machine) is not int or type(stake) is not int:
            raise UniversoError("La tirada no es válida.")
        channel_id = snowflake(body.get("channel_id"))
        channel = guild.get_channel_or_thread(channel_id) if channel_id is not None else None
        if body.get("parar") is True:
            tragas.plaza_stop(self.bot, guild.id, member, channel, machine)
            return {"ok": True}
        audience = sum(p.user_id != member.id for p in self.salas.present(guild.id))
        started = time.perf_counter()
        try:
            result = await tragas.plaza(
                self.bot,
                guild.id,
                member,
                channel,
                machine=machine,
                stake=stake,
                auto=body.get("auto") is True,
                audience=audience,
            )
        except SeatError as error:
            raise UniversoError(str(error)) from error
        if result is None:
            raise UniversoError("La tragaperras no está disponible ahora.", 503)
        self.bot.telemetry.count(
            "universo",
            "traga",
            guild_id=guild.id,
            user_id=member.id,
            ms=(time.perf_counter() - started) * 1000,
        )
        return result

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
            gestures = gestures_owned(await tienda.owned_keys(self.bot, guild.id, member.id))
            player = await self.salas.join(
                guild.id, member.id, member.display_name, ws, backlog, gestures
            )
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

    def _gesture(self, guild_id: int, player: Player, anim: int, near: list[Player]) -> None:
        """Logros de empezar un gesto. Quien baila cerca sube también su récord de bailar juntos."""
        x, _y, z = place(player.pose or bytes(8))
        same = [p for p in near if p.pose is not None and p.pose[-1] == anim]
        delta = universo_gesture_stats(
            GESTURES[anim].removeprefix("gesto_"),
            near=len(near),
            together=len(same) + 1,
            summit=on_summit(x, z),
            when=datetime.now(TIMEZONE),
        )
        logros.note(self.bot, guild_id, player.user_id, delta)
        if delta.peak and same:
            peak = StatDelta(peak=delta.peak)
            for other in same:
                logros.note(self.bot, guild_id, other.user_id, peak)

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
    guild, member = cog.admin(session, body)
    return cog, guild, member, body


async def _editor_borrador(request: web.Request) -> web.Response:
    cog, guild, _member, _body = await _editor_call(request)
    doc, rev, version = await cog.draft_world(guild.id)
    # El documento ya está codificado: se pega tal cual, sin pasar por `json`.
    return web.Response(
        text=f'{{"doc":{doc},"rev":{rev},"version":{version}}}', content_type="application/json"
    )


async def _editor_guardar(request: web.Request) -> web.Response:
    cog, guild, member, body = await _editor_call(request)
    return web.json_response({"rev": await cog.save_world(guild, member, body)})


async def _editor_publicar(request: web.Request) -> web.Response:
    cog, guild, member, body = await _editor_call(request)
    return web.json_response({"version": await cog.publish_world(guild, member, body)})


async def _editor_versiones(request: web.Request) -> web.Response:
    cog, guild, _member, _body = await _editor_call(request)
    return web.json_response({"versiones": await cog.world_versions(guild)})


async def _editor_volver(request: web.Request) -> web.Response:
    cog, guild, member, body = await _editor_call(request)
    return web.json_response({"rev": await cog.revert_world(guild, member, body)})


async def _editor_glb(request: web.Request) -> web.Response:
    cog, _guild, member, _body = await _editor_call(request, raw=True)
    return web.json_response(await cog.upload_glb(member, await _read_body(request)))


async def _mundo(request: web.Request) -> web.Response:
    cog = request.app[_COG_KEY]
    guild, _member = cog._member(bearer(request.headers.get("Authorization")), dict(request.query))
    version, doc = await cog.mundo(guild.id)
    text = f'{{"version":{version},"doc":{doc}}}'
    if "gzip" not in request.headers.get("Accept-Encoding", ""):
        return web.Response(text=text, content_type="application/json")
    cached = cog._mundo_gz.get(guild.id)
    if cached is None or cached[0] != version:
        cached = (version, await asyncio.to_thread(gzip.compress, text.encode(), 6))
        cog._mundo_gz[guild.id] = cached
    return web.Response(
        body=cached[1],
        content_type="application/json",
        headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"},
    )


async def _glb(request: web.Request) -> web.Response:
    cog = request.app[_COG_KEY]
    sha = request.match_info["sha"]
    data = await cog.bot.universo_mundo.glb(sha) if _SHA256.fullmatch(sha) else None
    if data is None:
        raise web.HTTPNotFound
    return web.Response(
        body=data,
        content_type="model/gltf-binary",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


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


async def _traga(request: web.Request) -> web.Response:
    cog = request.app[_COG_KEY]
    body = await _json_body(request)
    response = web.json_response(await cog.slot(bearer(request.headers.get("Authorization")), body))
    # Los fotogramas de una tirada son ~20 KB de JSON muy repetido: comprimidos, ~3 KB.
    response.enable_compression()
    return response


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
