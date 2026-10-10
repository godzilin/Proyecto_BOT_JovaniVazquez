"""Construcción y ciclo de vida del cliente de Discord.

Este módulo define cómo se crea el bot, qué extensiones (cogs) se cargan
y cómo se arranca y se cierra de forma ordenada. No lee variables de
entorno directamente: recibe una configuración ya validada
(ver `bot.config`).
"""

from __future__ import annotations

import logging
from pathlib import Path

import discord
from discord.ext import commands

from bot.repositories.achievements import AchievementRepository
from bot.repositories.beernight import BeernightRepository
from bot.repositories.beernight_sounds import BeernightSoundStore
from bot.repositories.birthdays import BirthdayRepository
from bot.repositories.casino_stats import CasinoStatsRepository
from bot.repositories.economy import EconomyRepository
from bot.repositories.entrance_sounds import EntranceSoundStore
from bot.repositories.hold_win import HoldWinRepository
from bot.repositories.horses import HorseRepository
from bot.repositories.lottery import LotteryRepository
from bot.repositories.message_stats import MessageStatsRepository
from bot.repositories.pets import PetRepository
from bot.repositories.porras import PorraRepository
from bot.repositories.shop import ShopRepository
from bot.repositories.slots import SlotsRepository
from bot.repositories.todo import TodoRepository
from bot.repositories.welcome import WelcomeRepository
from bot.repositories.work import WorkRepository
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.pala import WorkService

logger = logging.getLogger(__name__)

# Cogs que se cargan al arrancar. Añadir aquí el nuevo módulo cuando se
# incorpore una funcionalidad nueva agrupada por dominio.
INITIAL_EXTENSIONS: tuple[str, ...] = (
    "bot.cogs.general",
    "bot.cogs.errors",
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
    "bot.cogs.chicken",
    "bot.cogs.coin",
    "bot.cogs.bus",
    "bot.cogs.craps",
    "bot.cogs.pachinko",
    "bot.cogs.horses",
    "bot.cogs.lottery",
    "bot.cogs.renta",
    "bot.cogs.apuestas",
    "bot.cogs.porras",
    "bot.cogs.patrimonio",
    "bot.cogs.intereses",
    "bot.cogs.donations",
    "bot.cogs.bizum",
    "bot.cogs.shop",
    "bot.cogs.pets",
    "bot.cogs.work",
    "bot.cogs.birthdays",
    "bot.cogs.todo",
    "bot.cogs.beernight",
    "bot.cogs.achievements",
    "bot.cogs.perfil",
    "bot.cogs.fun",
    "bot.cogs.admin",
    "bot.cogs.deploy",
)


def build_intents() -> discord.Intents:
    """Crea los intents requeridos por las funciones actuales.

    El intent privilegiado de miembros permite recibir eventos de entrada
    y salida; también debe habilitarse en el portal de desarrolladores.
    El intent privilegiado de contenido de mensajes es necesario para que
    el bot pueda leer comandos de texto con prefijo (p. ej. ".poner"); debe
    habilitarse igualmente como "Message Content Intent" en el portal.
    """
    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True
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
    ) -> None:
        self.message_stats = MessageStatsRepository(database_path)
        # Única puerta al dinero del bot: el casino y cualquier sistema futuro
        # que dé o quite yapdollars deben pasar por aquí (ver services/economy).
        self.economy = EconomyService(
            EconomyRepository(database_path, starting_balance=STARTING_BALANCE)
        )
        self.casino_channel_ids = casino_channel_ids
        # Una fila por jugada terminada del casino, para `apuestas`. No mueve dinero.
        self.casino_stats = CasinoStatsRepository(database_path)
        self.birthdays = BirthdayRepository(database_path)
        self.achievements = AchievementRepository(database_path)
        self.welcome = WelcomeRepository(database_path)
        # Catálogo e inventario de la tienda; el dinero de cada compra pasa por `economy`.
        self.shop = ShopRepository(database_path)
        # Estado de las mascotas (`mascota`); quién tiene cada una lo dice la tienda.
        self.pets = PetRepository(database_path)
        # Sorteos y boletos de `loteria`; el dinero también pasa por `economy`.
        self.lottery = LotteryRepository(database_path)
        # Lista de cosas que hacer del servidor (`lista`).
        self.todo = TodoRepository(database_path)
        # Maletines y botes de cada jugador en las máquinas de Botes
        # (de momento, `volcan`); el dinero pasa por `economy`.
        self.hold_win = HoldWinRepository(database_path)
        # Calor y giro diario de la tragaperras (`tragas`); el dinero y el
        # bote pasan por `economy`.
        self.slots_repository = SlotsRepository(database_path)
        # Historial del establo y bote del Gran Premio de `caballo`; el dinero pasa por `economy`.
        self.horses = HorseRepository(database_path)
        # Porras sobre las jugadas de otro (`porra`); el dinero pasa por `economy`.
        self.porras = PorraRepository(database_path)
        # Oficios, turnos y ascensos de `pala`; el dinero de las nóminas pasa por `economy`.
        self.work = WorkService(WorkRepository(database_path), self.economy)
        # Los sonidos de entrada viven junto a la base de datos, en el mismo
        # volumen persistente (`.data/entradas/`).
        self.entrance_sounds = EntranceSoundStore(database_path.parent / "entradas")
        # Histórico de la beernight (`beernight`) y sus audios, en el mismo volumen.
        self.beernight = BeernightRepository(database_path)
        self.beernight_sounds = BeernightSoundStore(database_path.parent / "beernight")
        super().__init__(
            command_prefix=command_prefix,
            intents=build_intents(),
            # El texto de ayuda por defecto de discord.py no está en
            # español ni pensado para slash commands; se desactiva.
            help_command=None,
        )

    async def setup_hook(self) -> None:
        """Carga las extensiones y sincroniza los comandos de aplicación.

        `setup_hook` se ejecuta una vez, tras conectar pero antes de
        recibir eventos, que es el punto recomendado por discord.py para
        preparar el estado del bot.
        """
        await self.message_stats.initialize()
        await self.message_stats.recover_interrupted_imports()
        await self.economy.repository.initialize()
        await self.casino_stats.initialize()
        await self.birthdays.initialize()
        await self.achievements.initialize()
        await self.welcome.initialize()
        await self.shop.initialize()
        await self.pets.initialize()
        await self.lottery.initialize()
        await self.todo.initialize()
        await self.hold_win.initialize()
        await self.slots_repository.initialize()
        await self.horses.initialize()
        await self.porras.initialize()
        await self.work.repository.initialize()
        await self.beernight.initialize()

        for extension in INITIAL_EXTENSIONS:
            await self.load_extension(extension)
            logger.info("Extensión cargada: %s", extension)

        synced = await self.tree.sync()
        logger.info("Comandos de aplicación sincronizados: %d", len(synced))

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
) -> None:
    """Crea el cliente y lo ejecuta hasta que se detenga o falle.

    Args:
        token: Token de autenticación del bot. Nunca se registra en logs.
        command_prefix: Prefijo de los comandos de texto (por defecto `.`).
        database_path: Ubicación de la base de datos persistente del bot.
        casino_channel_ids: Canales donde se permiten los juegos del casino
            (vacío = cualquiera).

    Raises:
        discord.LoginFailure: Si el token es inválido.
    """
    client = BotClient(
        command_prefix=command_prefix,
        database_path=database_path,
        casino_channel_ids=casino_channel_ids,
    )
    async with client:
        await client.start(token)
