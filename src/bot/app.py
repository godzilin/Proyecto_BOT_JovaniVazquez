"""Construcción y ciclo de vida del cliente de Discord.

Este módulo define cómo se crea el bot, qué extensiones (cogs) se cargan
y cómo se arranca y se cierra de forma ordenada. No lee variables de
entorno directamente: recibe una configuración ya validada
(ver `bot.config`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
import time
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands

from bot.cogs.deploy import Deploy
from bot.repositories import sqlite
from bot.repositories.achievements import AchievementRepository
from bot.repositories.activity import ActivityRepository
from bot.repositories.aspecto import AspectoRepository, LookImageStore
from bot.repositories.backgammon import BackgammonRepository
from bot.repositories.beernight import BeernightRepository
from bot.repositories.beernight_sounds import BeernightSoundStore
from bot.repositories.birthdays import BirthdayRepository
from bot.repositories.business import BusinessRepository
from bot.repositories.casino_stats import CasinoStatsRepository
from bot.repositories.economy import EconomyRepository
from bot.repositories.enraya import EnrayaRepository
from bot.repositories.entrance_sounds import EntranceSoundStore
from bot.repositories.gallos import GallosRepository
from bot.repositories.hold_win import HoldWinRepository
from bot.repositories.horses import HorseRepository
from bot.repositories.housing import HousingRepository
from bot.repositories.lottery import LotteryRepository
from bot.repositories.message_stats import MessageStatsRepository
from bot.repositories.moderation import ModerationRepository
from bot.repositories.monte import MonteRepository
from bot.repositories.news import NewsRepository
from bot.repositories.pets import PetRepository
from bot.repositories.picar import PicarRepository
from bot.repositories.poker import PokerRepository
from bot.repositories.porras import PorraRepository
from bot.repositories.rastro import MarketRepository
from bot.repositories.shop import ShopRepository
from bot.repositories.slots import SlotsRepository
from bot.repositories.sports import SportsRepository
from bot.repositories.telemetry import TelemetryRepository
from bot.repositories.todo import TodoRepository
from bot.repositories.welcome import WelcomeRepository
from bot.repositories.work import WorkRepository
from bot.services import node_scene
from bot.services.economy import IRPF_START, STARTING_BALANCE, EconomyService
from bot.services.negocio import BusinessService
from bot.services.pala import WorkService
from bot.services.telemetry import TelemetryService
from bot.utils.cogs import find_cog

logger = logging.getLogger(__name__)

#: Segundos que una conexión HTTP con Discord se queda abierta sin uso. aiohttp la
#: cierra a los 15 s y el clic siguiente paga otra vez el saludo TCP y TLS (dos
#: viajes más, ~240 ms desde el NAS). Discord la aguanta al menos 250 s parada.
KEEPALIVE_SECONDS = 90
#: Cada cuánto se toca la API para que la conexión no llegue nunca a cerrarse.
KEEP_WARM_SECONDS = 60

# Cogs que se cargan al arrancar. Añadir aquí el nuevo módulo cuando se
# incorpore una funcionalidad nueva agrupada por dominio.
INITIAL_EXTENSIONS: tuple[str, ...] = (
    "bot.cogs.general",
    "bot.cogs.errors",
    "bot.cogs.telemetry",
    "bot.cogs.message_stats",
    "bot.cogs.welcome",
    "bot.cogs.music",
    "bot.cogs.entrance",
    "bot.cogs.images",
    "bot.cogs.casino",
    "bot.cogs.blackjack",
    "bot.cogs.slots",
    "bot.cogs.hold_win",
    "bot.cogs.crash",
    "bot.cogs.mines",
    "bot.cogs.tower",
    "bot.cogs.war",
    "bot.cogs.chicken",
    "bot.cogs.coin",
    "bot.cogs.bus",
    "bot.cogs.allatu",
    "bot.cogs.poker",
    "bot.cogs.timba",
    "bot.cogs.craps",
    "bot.cogs.pachinko",
    "bot.cogs.picar",
    "bot.cogs.horses",
    "bot.cogs.bacara",
    "bot.cogs.gallos",
    "bot.cogs.enraya",
    "bot.cogs.backgammon",
    "bot.cogs.lottery",
    "bot.cogs.renta",
    "bot.cogs.apuestas",
    "bot.cogs.porras",
    "bot.cogs.sports",
    "bot.cogs.patrimonio",
    "bot.cogs.intereses",
    "bot.cogs.donations",
    "bot.cogs.bizum",
    "bot.cogs.shop",
    "bot.cogs.rastro",
    "bot.cogs.pisos",
    "bot.cogs.banco",
    "bot.cogs.pieles",
    "bot.cogs.pets",
    "bot.cogs.work",
    "bot.cogs.business",
    "bot.cogs.birthdays",
    "bot.cogs.todo",
    "bot.cogs.beernight",
    "bot.cogs.achievements",
    "bot.cogs.robuso",
    "bot.cogs.perfil",
    "bot.cogs.fun",
    "bot.cogs.aspecto",
    "bot.cogs.admin",
    "bot.cogs.moderation",
    "bot.cogs.deploy",
)


def build_intents(*, presences: bool = True) -> discord.Intents:
    """Crea los intents requeridos por las funciones actuales.

    El intent privilegiado de miembros permite recibir eventos de entrada
    y salida; también debe habilitarse en el portal de desarrolladores.
    El intent privilegiado de contenido de mensajes es necesario para que
    el bot pueda leer comandos de texto con prefijo (p. ej. ".poner"); debe
    habilitarse igualmente como "Message Content Intent" en el portal.
    El de presencias (`presences`) dice cuándo se conecta cada miembro, para
    la «última vez conectado» del panel `admin`; es el «Presence Intent» del
    portal. Si no está activado allí, `start_bot` arranca sin él.
    """
    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True
    intents.presences = presences
    return intents


class BotClient(commands.Bot):
    """Cliente principal del bot.

    Encapsula la carga de extensiones y expone un punto único de
    arranque (`start_bot`) para mantener `__main__.py` mínimo.
    """

    def __init__(
        self,
        *,
        command_prefix: str,
        database_path: Path,
        casino_channel_ids: frozenset[int] = frozenset(),
        presences: bool = True,
        odds_api_key: str = "",
    ) -> None:
        self.database_path = database_path
        self.message_stats = MessageStatsRepository(database_path)
        # Única puerta al dinero del bot: el casino y cualquier sistema futuro
        # que dé o quite yapdollars deben pasar por aquí (ver services/economy).
        self.economy = EconomyService(
            EconomyRepository(
                database_path,
                starting_balance=STARTING_BALANCE,
                irpf_start=IRPF_START.isoformat(),
            )
        )
        self.casino_channel_ids = casino_channel_ids
        # Una fila por jugada terminada del casino, para `apuestas`. No mueve dinero.
        self.casino_stats = CasinoStatsRepository(database_path)
        self.birthdays = BirthdayRepository(database_path)
        self.achievements = AchievementRepository(database_path)
        self.welcome = WelcomeRepository(database_path)
        # Dónde y cómo se publica el aviso de novedades de cada despliegue (`cambios`).
        self.news = NewsRepository(database_path)
        # Catálogo e inventario de la tienda; el dinero de cada compra pasa por `economy`.
        self.shop = ShopRepository(database_path)
        # Puestos, pujas y ventas de segunda mano del Rastro (`vender`, `rastro`); el
        # dinero pasa por `economy` y el objeto, por la mochila de la tienda.
        self.rastro = MarketRepository(database_path)
        # Papeletas y ventas del Monte de Piedad (panel de `vender`); el dinero pasa por `economy`
        # (con la caja de la tienda) y el objeto, por la mochila de la tienda.
        self.monte = MonteRepository(database_path)
        # Inmuebles, participaciones e inquilinos de `pisos`; el dinero pasa por `economy`.
        self.housing = HousingRepository(database_path)
        # Estado de las mascotas (`mascota`); quién tiene cada una lo dice la tienda.
        self.pets = PetRepository(database_path)
        # Sorteos y boletos de `loteria`; el dinero también pasa por `economy`.
        self.lottery = LotteryRepository(database_path)
        # Lista de cosas que hacer del servidor (`lista`).
        self.todo = TodoRepository(database_path)
        # Maletines y botes de cada jugador en las máquinas de Botes
        # (el Volcán, la Mina y el Olimpo, que se abren con `tragas`); el dinero pasa por `economy`.
        self.hold_win = HoldWinRepository(database_path)
        # Tiradas de `picar` con su semilla (récords, ranking y auditoría); el dinero
        # pasa por `economy`.
        self.picar = PicarRepository(database_path)
        # Calor y giro diario de la tragaperras (`tragas`); el dinero y el
        # bote pasan por `economy`.
        self.slots_repository = SlotsRepository(database_path)
        # Historial del establo y bote del Gran Premio de `caballo`; el dinero pasa por `economy`.
        self.horses = HorseRepository(database_path)
        # Ficha del gallo de cada miembro para `gallos`; el dinero pasa por `economy`.
        self.gallos = GallosRepository(database_path)
        # Ficha y retos del día de `4enraya`; el dinero de duelos y retos pasa por `economy`.
        self.enraya = EnrayaRepository(database_path)
        # Ficha y retos del día de `backgammon`; el dinero de duelos y retos pasa por `economy`.
        self.backgammon = BackgammonRepository(database_path)
        # Rango, racha y retos del club del `poker`; el dinero pasa por `economy`.
        self.poker = PokerRepository(database_path)
        # Porras sobre las jugadas de otro (`porra`); el dinero pasa por `economy`.
        self.porras = PorraRepository(database_path)
        # Partidos y cuotas de las apuestas deportivas (`deportes`); el dinero pasa por
        # `economy`. Sin clave de la API de cuotas, `deportes` dice que no está configurado.
        self.sports = SportsRepository(database_path)
        self.odds_api_key = odds_api_key
        # Oficios, turnos y ascensos de `pala`; el dinero de las nóminas pasa por `economy`.
        self.work = WorkService(WorkRepository(database_path), self.economy, housing=self.housing)
        # Negocios propios dentro de `pala`: comparten las franjas y la batería del curro;
        # el dinero pasa por `economy`.
        self.negocio = BusinessService(
            BusinessRepository(database_path), self.economy, self.work, housing=self.housing
        )
        self.work.business = self.negocio
        # Los sonidos de entrada viven junto a la base de datos, en el mismo
        # volumen persistente (`.data/entradas/`).
        self.entrance_sounds = EntranceSoundStore(database_path.parent / "entradas")
        # Histórico de la beernight (`beernight`) y sus audios, en el mismo volumen.
        self.beernight = BeernightRepository(database_path)
        self.beernight_sounds = BeernightSoundStore(database_path.parent / "beernight")
        # Telemetría interna (`informe`): cuánto se usa cada cosa, sin contenido de
        # mensajes. Cuenta en memoria y la vuelca cada minuto el cog `Telemetry`.
        self.telemetry_repository = TelemetryRepository(database_path)
        self.telemetry = TelemetryService(self.telemetry_repository)
        # Panel `admin`: última actividad de cada miembro, sanciones y notas de moderador.
        self.last_seen = ActivityRepository(database_path)
        self.moderation = ModerationRepository(database_path)
        # Looks de Jovani (`aspecto`, `jovani`) y sus imágenes, en el mismo volumen.
        self.aspecto = AspectoRepository(database_path)
        self.aspecto_images = LookImageStore(database_path.parent / "aspecto")
        self._keep_warm_task: asyncio.Task[None] | None = None
        self._checkpoint_task: asyncio.Task[None] | None = None
        super().__init__(
            command_prefix=command_prefix,
            intents=build_intents(presences=presences),
            # El texto de ayuda por defecto de discord.py no está en
            # español ni pensado para slash commands; se desactiva.
            help_command=None,
        )

    async def login(self, token: str) -> None:
        """Inicia sesión con una conexión HTTP que no se cierra a los 15 s parada."""
        self.http.connector = aiohttp.TCPConnector(limit=0, keepalive_timeout=KEEPALIVE_SECONDS)
        await super().login(token)

    async def _keep_warm(self) -> None:
        """Toca la API cada minuto: el primer clic tras un rato sin uso no abre conexión."""
        route = discord.http.Route("GET", "/gateway")
        while True:
            await asyncio.sleep(KEEP_WARM_SECONDS)
            with contextlib.suppress(discord.HTTPException, OSError):
                await self.http.request(route)

    async def _checkpoint(self) -> None:
        """Vacía el registro WAL de la base (ver `bot.repositories.sqlite.checkpoint`)."""
        started = time.perf_counter()
        try:
            busy, pages, _done = await asyncio.to_thread(sqlite.checkpoint, self.database_path)
        except sqlite3.Error:
            logger.warning("No se pudo volcar el registro de la base", exc_info=True)
            return
        ms = (time.perf_counter() - started) * 1000
        if busy:
            logger.info("Checkpoint de la base ocupado (%d páginas); se reintenta luego", pages)
        elif ms > 1000:
            logger.warning("Checkpoint lento de la base: %d páginas en %.0f ms", pages, ms)

    async def _checkpoint_loop(self) -> None:
        """Vacía el registro WAL cada `CHECKPOINT_SECONDS`: si no, crece sin fin."""
        while True:
            await asyncio.sleep(sqlite.CHECKPOINT_SECONDS)
            await self._checkpoint()

    async def setup_hook(self) -> None:
        """Carga las extensiones y sincroniza los comandos de aplicación.

        `setup_hook` se ejecuta una vez, tras conectar pero antes de
        recibir eventos, que es el punto recomendado por discord.py para
        preparar el estado del bot.
        """
        # Antes de nada: un registro WAL heredado de versiones anteriores puede ser
        # enorme (no se vaciaba nunca) y haría lenta cada conexión del arranque.
        await self._checkpoint()
        await self.message_stats.initialize()
        await self.message_stats.recover_interrupted_imports()
        await self.economy.repository.initialize()
        await self.casino_stats.initialize()
        await self.birthdays.initialize()
        await self.achievements.initialize()
        await self.welcome.initialize()
        await self.news.initialize()
        await self.shop.initialize()
        await self.rastro.initialize()
        await self.monte.initialize()
        await self.housing.initialize()
        await self.pets.initialize()
        await self.lottery.initialize()
        await self.todo.initialize()
        await self.hold_win.initialize()
        await self.picar.initialize()
        await self.slots_repository.initialize()
        await self.horses.initialize()
        await self.gallos.initialize()
        await self.enraya.initialize()
        await self.backgammon.initialize()
        await self.poker.initialize()
        await self.porras.initialize()
        await self.sports.initialize()
        await self.work.repository.initialize()
        await self.negocio.repository.initialize()
        await self.beernight.initialize()
        await self.telemetry_repository.initialize()
        await self.last_seen.initialize()
        await self.moderation.initialize()
        await self.aspecto.initialize()

        for extension in INITIAL_EXTENSIONS:
            await self.load_extension(extension)
            logger.info("Extensión cargada: %s", extension)

        # El pintor de Node que siempre queda abierto: el primer dibujo no arranca en frío.
        await node_scene.warm()

        synced = await self.tree.sync()
        logger.info("Comandos de aplicación sincronizados: %d", len(synced))
        self._keep_warm_task = asyncio.create_task(self._keep_warm(), name="http-caliente")
        self._checkpoint_task = asyncio.create_task(self._checkpoint_loop(), name="sqlite-wal")

    async def close(self) -> None:
        """Cierra la conexión, descarga los cogs y luego los procesos de Node."""
        # Antes de descargar los juegos, que cierran sus partidas: sin comandos ni
        # botones mientras tanto (modo reinicio, `bot.cogs.deploy`).
        if (deploy := find_cog(self, Deploy)) is not None:
            deploy.closing = True
        if self._keep_warm_task is not None:
            self._keep_warm_task.cancel()
        if self._checkpoint_task is not None:
            self._checkpoint_task.cancel()
        await super().close()
        await node_scene.shutdown()
        # Con los cogs ya cerrados, nada más escribe: el registro queda vacío.
        await self._checkpoint()

    async def on_ready(self) -> None:
        """Registra en el log que el bot está conectado y operativo."""
        assert self.user is not None  # on_ready implica sesión iniciada
        logger.info("Sesión iniciada como %s (ID: %s)", self.user, self.user.id)


async def start_bot(
    token: str,
    *,
    command_prefix: str,
    database_path: Path,
    casino_channel_ids: frozenset[int] = frozenset(),
    odds_api_key: str = "",
) -> None:
    """Crea el cliente y lo ejecuta hasta que se detenga o falle.

    Args:
        token: Token de autenticación del bot. Nunca se registra en logs.
        command_prefix: Prefijo de los comandos de texto (por defecto `.`).
        database_path: Ubicación de la base de datos persistente del bot.
        casino_channel_ids: Canales donde se permiten los juegos del casino
            (vacío = cualquiera).
        odds_api_key: Clave de The Odds API para `deportes` (vacía = sin apuestas
            deportivas). Nunca se registra en logs.

    Si el «Presence Intent» no está activado en el portal, vuelve a arrancar
    sin él en vez de caerse: solo se pierde la «última vez conectado» de `admin`.

    Raises:
        discord.LoginFailure: Si el token es inválido.
    """
    for presences in (True, False):
        client = BotClient(
            command_prefix=command_prefix,
            database_path=database_path,
            casino_channel_ids=casino_channel_ids,
            presences=presences,
            odds_api_key=odds_api_key,
        )
        try:
            async with client:
                await client.start(token)
            return
        except discord.PrivilegedIntentsRequired:
            if not presences:
                raise
            logger.warning(
                "El «Presence Intent» no está activado en el portal de Discord: "
                "se arranca sin él y `admin` no sabrá cuándo se conectó cada miembro."
            )
