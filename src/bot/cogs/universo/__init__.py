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
URL Mapping del portal apunta al túnel del NAS). Las rutas y su lógica van por temas,
cada una documentada en su módulo:

- `sesion`: la página, el login y la entrada (`/`, `/api/config`, `/api/token`,
  `/api/entrar`).
- `permisos`: los permisos del Jovaniverso (`/api/permisos`).
- `editor`: el editor del mundo (`/api/editor/*`).
- `mundo`: el mundo publicado por trozos y los modelos (`/api/mundo/indice`,
  `/api/mundo/trozos`, `/api/glb/{sha}`).
- `sala`: el multijugador y el paseo (`/api/sala`, `/api/paseo`).
- `casino`: las tragaperras de la plaza y los botes (`/api/traga`, `/api/botes`).
- `menu`: el menú del tabulador (`/api/jugadores`, `/api/mochila`).
- `telemetria`: cómo va la página en cada dispositivo (`/api/medidas`).
- `promo`: el botón 🌌 Jovaniverso de los paneles.

Cada módulo de rutas trae un mixin con los métodos del cog de su tema; el cog (aquí) guarda el
estado, el comando, el botón y el arranque del servidor.

La página no decide nada: todo lo que enseña sale de aquí, y el dinero (las tragaperras
de la plaza) se decide aquí con `EconomyService`, nunca en el navegador.

Logros: entrar (`universo_stats`, días distintos, móvil, ordenador, madrugada y
Día de Canarias), pasear (`universo_walk_stats`: metros, saltos, deslizamientos y
lugares de la sala; trucos, velocidad y teleportes del informe),
coincidir con otros (`universo_meet_stats`, al entrar en la sala), y gestos
(`universo_gesture_stats`, al empezar cada uno) y las tragaperras de la plaza
(`universo_slots_stats`), categoría 🎤 Jovani. La mascota solo habla en el resultado de
las tragaperras (`renta.hint`, que pide el cog de la tragaperras).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Hashable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp
import discord
from aiohttp import web
from discord import ui
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs.universo.casino import CasinoMixin, _botes, _traga
from bot.cogs.universo.comun import _COG_KEY, _errors
from bot.cogs.universo.editor import EditorMixin
from bot.cogs.universo.menu import MenuMixin, _jugadores, _mochila
from bot.cogs.universo.mundo import CACHE_BYTES, ByteCache, WorldMixin, _glb, _indice, _trozos
from bot.cogs.universo.permisos import PermissionsMixin, _permisos
from bot.cogs.universo.promo import PROMO_ID, install_promo, promo_embed, uninstall_promo
from bot.cogs.universo.sala import SalaMixin, _paseo, _sala
from bot.cogs.universo.sesion import SessionMixin, _config, _entrar, _index, _token
from bot.cogs.universo.telemetria import TelemetryMixin, _medidas
from bot.services.achievements import universo_teaser_stats
from bot.services.universo import DISCORD_API, Places, SessionSigner
from bot.services.universo_sala import Salas

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

#: Carpeta de la página compilada (`npm run build` en `universo/`).
STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "universo"
#: `custom_id` del botón de `.universo`. Fijo: la vista es persistente y el botón
#: sigue valiendo tras un reinicio.
OPEN_ID = "universo:abrir"
#: Lo más que acepta el servidor en el cuerpo de una petición. El login y la entrada
#: son unos pocos campos; nada justifica más.
MAX_BODY_BYTES = 16 * 1024
#: Dirección en que escucha el servidor. En Docker hay que escuchar en todas para
#: que el túnel, en otro contenedor, llegue; el puerto no se publica fuera.
LISTEN_HOST = "0.0.0.0"  # noqa: S104

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


class Universo(
    SessionMixin,
    PermissionsMixin,
    WorldMixin,
    EditorMixin,
    SalaMixin,
    CasinoMixin,
    MenuMixin,
    TelemetryMixin,
    commands.Cog,
    name="Universo",
):
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
        # Último informe de medidas de cada miembro (`time.monotonic`), para su tope.
        self._last_medidas: dict[int, float] = {}
        # Último informe de paseo (o entrada) de cada miembro, en segundos de
        # `time.monotonic`: dice cuánto ha podido andar desde entonces.
        self._last_walk: dict[tuple[int, int], float] = {}
        self.salas = Salas(on_gesture=self._gesture, on_walk=self._walked)
        # El extraplano de los desarrolladores: sala aparte, para que nadie vea volar a
        # nadie por la plaza, sin gestos ni paseo que cuenten para logros y sin mirar la
        # velocidad (allí se vuela y se atraviesa todo).
        self.pruebas = Salas(check=False)
        # Dónde están los lugares de los logros en el mundo publicado de cada servidor
        # (`world_places`), hasta que alguien publica.
        self._lugares: dict[int, Places] = {}
        # Versión publicada de cada servidor, para no ir a la base en cada entrada.
        # `_mundo_epoch` cuenta las publicaciones: una lectura que empezó antes de publicar no
        # guarda lo viejo. El mundo no está en memoria: el índice y los trozos que piden las
        # páginas viven en una caché con tope de bytes (`ByteCache`, por versión de fila).
        self._version: dict[int, int] = {}
        self._mundo_epoch: dict[int, int] = {}
        self._cache = ByteCache(CACHE_BYTES)
        # Lo que se está leyendo ahora (`_once`): quien pide lo mismo espera, no lo repite.
        self._pending: dict[Hashable, asyncio.Future[Any]] = {}
        # Botes de cada servidor para los letreros (`pots`), con su hora.
        self._botes: dict[int, tuple[float, list[dict[str, str]]]] = {}
        # Saldos y experiencia de cada servidor para el menú (`players`), con su hora.
        self._lista: dict[int, tuple[float, dict[int, int], dict[int, int] | None]] = {}

    @property
    def is_open(self) -> bool:
        """Si el universo está configurado (hay secreto de OAuth2)."""
        return bool(self.client_secret)

    async def cog_load(self) -> None:
        """Registra el botón persistente y abre el servidor web si hay secreto."""
        self.bot.add_view(OpenView(self))
        install_promo()
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
        uninstall_promo()
        await self.salas.close()
        await self.pruebas.close()
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
        app.router.add_post("/api/medidas", _medidas)
        app.router.add_get("/api/mundo/indice", _indice)
        app.router.add_get("/api/mundo/trozos", _trozos)
        app.router.add_get("/api/glb/{sha}", _glb)
        app.router.add_post("/api/traga", _traga)
        app.router.add_get("/api/sala", _sala)
        app.router.add_get("/api/permisos", _permisos)
        app.router.add_get("/api/jugadores", _jugadores)
        app.router.add_get("/api/mochila", _mochila)
        app.router.add_get("/api/botes", _botes)
        app.router.add_post("/api/permisos", _permisos)
        app.add_subapp("/api/editor/", self.build_editor())
        assets = self.static_dir / "assets"
        if assets.is_dir():
            app.router.add_static("/assets", assets)
        return app

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Contesta el botón 🌌 Jovaniverso de cualquier panel. Solo mira memoria."""
        custom_id = str((interaction.data or {}).get("custom_id", ""))
        if interaction.type is not discord.InteractionType.component or not custom_id.startswith(
            PROMO_ID
        ):
            return
        panel = custom_id.removeprefix(PROMO_ID)
        embed = promo_embed(panel, is_open=self.is_open)
        if self.is_open:
            await interaction.response.send_message(
                embed=embed, view=OpenView(self), ephemeral=True
            )
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        if interaction.guild_id is not None and not interaction.user.bot:
            logros.note(
                self.bot,
                interaction.guild_id,
                interaction.user.id,
                universo_teaser_stats(panel),
                interaction.channel_id,
            )

    @commands.command(name="universo")
    @commands.guild_only()
    async def universo_text(self, ctx: commands.Context) -> None:
        """Abre el Jovaniverso, el bot en una Activity dentro de Discord.

        Con `/` es el comando de entrada de la Activity (lo abre Discord); con `.`,
        un botón que la abre.
        """
        await ctx.send(INTRO, view=OpenView(self))


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con el secreto de OAuth2 y el puerto de la configuración."""
    await bot.add_cog(Universo(bot, client_secret=bot.client_secret, port=bot.universo_port))
