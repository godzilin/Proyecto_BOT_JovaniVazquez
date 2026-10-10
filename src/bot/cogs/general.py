"""Cog con comandos generales: `/latencia` y `/ayuda`.

Cada comando tiene un único nombre corto, idéntico en las dos interfaces:
comando de aplicación (`/latencia`) y comando de texto (`.latencia`). Ambas
comparten la misma lógica a través de `CommandResponder`
(`bot.utils.responder`).
"""

from __future__ import annotations

import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import DEFAULT_COMMAND_PREFIX
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

logger = logging.getLogger(__name__)

# Color de acento de los embeds del cog, coherente con el resto del bot.
EMBED_COLOR = discord.Color.blurple()

# Orden y título de cada categoría de la ayuda, por nombre de cog. Un cog
# nuevo que no esté aquí aparece igualmente, al final, como "Otros".
HELP_CATEGORIES: dict[str, str] = {
    "General": "⚙️ General",
    "Perfil": "👤 Perfil",
    "Music": "🎵 Música",
    "MessageStats": "📊 Niveles",
    "Casino": "🎰 Casino",
    "Blackjack": "🎰 Casino",
    "Tragaperras": "🎰 Casino",
    "Botes": "🎰 Casino",
    "Crash": "🎰 Casino",
    "Minas": "🎰 Casino",
    "Pollo": "🎰 Casino",
    "Moneda": "🎰 Casino",
    "Autobús": "🎰 Casino",
    "Dados": "🎰 Casino",
    "Caballos": "🎰 Casino",
    "Pachinko": "🎰 Casino",
    "Loteria": "🎰 Casino",
    "Renta": "🎰 Casino",
    "Patrimonio": "🎰 Casino",
    "Apuestas": "🎰 Casino",
    "Porras": "🎰 Casino",
    "Donaciones": "🎰 Casino",
    "Bizum": "🎰 Casino",
    "Tienda": "🛍️ Tienda",
    "Mascotas": "🛍️ Tienda",
    "Trabajo": "🪏 Trabajo",
    "Birthdays": "🎂 Cumpleaños",
    "Lista": "📝 Lista",
    "Beernight": "🍻 Beernight",
    "Entrance": "🔔 Entradas",
    "Images": "🎨 Imagen",
    "Fun": "🗼 Diversión",
}
OTHER_CATEGORY = "📦 Otros"
# Categoría de los comandos de administración: solo se muestra a quien es
# administrador, para no enseñar a todo el mundo comandos que no puede usar.
ADMIN_COG = "Admin"
ADMIN_CATEGORY = "🛡️ Admin"

# Clave de `Command.extras` para los comandos que la ayuda lista en una
# subcategoría propia, con el título indicado (los efectos de imagen, que se
# reparten por tipo justo después de "Imagen").
COMPACT_GROUP_KEY = "help_group"
# Clave opcional de `Command.extras`: posición del grupo en la ayuda (menor, antes).
COMPACT_ORDER_KEY = "help_order"

# Máximo de caracteres de un campo de embed (límite de Discord).
FIELD_LIMIT = 1024
# Separador entre nombres de comando dentro de una categoría.
SEPARATOR = " · "


def build_help_embed(bot: commands.Bot, *, include_admin: bool = False) -> discord.Embed:
    """Construye la ayuda en un solo embed: categorías con nombres de comando.

    Todas las categorías tienen el mismo formato: un campo por categoría con
    los nombres en orden alfabético, sin descripción ni argumentos. Los
    comandos se leen de `bot.commands` en vez de una lista escrita a mano,
    para que la ayuda nunca se desincronice de lo registrado. Como `/nombre`
    y `.nombre` son idénticos, cada comando aparece una sola vez.

    Args:
        bot: Cliente con los cogs ya cargados.
        include_admin: Si se añade la categoría de administración (solo
            para quien tiene permiso de administrador).
    """
    prefix = _text_prefix(bot)
    embed = discord.Embed(
        title="📖 Comandos",
        description=(
            f"Con `/` o con `{prefix}` · los de imagen, solo con `{prefix}` · "
            f"`{prefix}memes efecto` explica un efecto"
        ),
        color=EMBED_COLOR,
    )

    visible = [c for c in bot.commands if not c.hidden]
    categories: dict[str, list[str]] = {}
    groups: dict[str, list[str]] = {}
    for command in sorted(visible, key=lambda c: c.extras.get(COMPACT_ORDER_KEY, 0)):
        if COMPACT_GROUP_KEY in command.extras:
            groups.setdefault(command.extras[COMPACT_GROUP_KEY], []).append(command.name)
        else:
            categories.setdefault(_category_title(command), []).append(command.name)

    # Varios cogs pueden compartir categoría (la ruleta y el blackjack van en
    # Casino): cada título se recorre una sola vez.
    ordered = list(dict.fromkeys(HELP_CATEGORIES.values()))
    sections: list[tuple[str, list[str]]] = []
    for title in ordered:
        sections.append((title, categories.get(title, [])))
        if title == HELP_CATEGORIES["Images"]:
            sections.extend((f"{title} · {group}", names) for group, names in groups.items())
    sections.append((OTHER_CATEGORY, categories.get(OTHER_CATEGORY, [])))
    if include_admin:
        sections.append((ADMIN_CATEGORY, categories.get(ADMIN_CATEGORY, [])))

    for title, names in sections:
        if not names:
            continue
        chunks = _chunk_names([f"`{name}`" for name in sorted(names)])
        for index, value in enumerate(chunks):
            name = f"{title} ({len(names)})" if index == 0 else f"{title} (cont.)"
            embed.add_field(name=name, value=value, inline=False)

    return embed


def _category_title(command: commands.Command) -> str:
    """Título de la categoría a la que pertenece el comando."""
    if command.cog_name == ADMIN_COG:
        return ADMIN_CATEGORY
    return HELP_CATEGORIES.get(command.cog_name or "", OTHER_CATEGORY)


def _chunk_names(names: list[str]) -> list[str]:
    """Une los nombres con ` · ` en trozos que caben en un campo de embed."""
    chunks: list[str] = []
    current = ""
    for name in names:
        candidate = f"{current}{SEPARATOR}{name}" if current else name
        if len(candidate) > FIELD_LIMIT:
            chunks.append(current)
            candidate = name
        current = candidate
    if current:
        chunks.append(current)
    return chunks


def _text_prefix(bot: commands.Bot) -> str:
    """Prefijo de texto con el que se mostrarán los ejemplos de la ayuda.

    Se lee del propio bot para que la ayuda no se desincronice si cambia
    `COMMAND_PREFIX`. Si hubiera varios prefijos, se muestra el primero.
    """
    prefix = bot.command_prefix
    if isinstance(prefix, str):
        return prefix
    if isinstance(prefix, (list, tuple)) and prefix:
        return str(prefix[0])
    return DEFAULT_COMMAND_PREFIX


def is_admin(member: discord.Member | None) -> bool:
    """Indica si el miembro tiene el permiso de administrador en su servidor."""
    return member is not None and member.guild_permissions.administrator


class General(commands.Cog):
    """Comandos generales que no pertenecen a un dominio más específico."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _ping_impl(self, responder: CommandResponder) -> None:
        """Lógica compartida entre `/latencia` y `.latencia`."""
        start = time.perf_counter()
        latency_ms = round(self.bot.latency * 1000)
        elapsed_ms = round((time.perf_counter() - start) * 1000)

        await responder.send(
            f"🏓 Pong! Latencia de la conexión: {latency_ms} ms "
            f"(tiempo de respuesta: {elapsed_ms} ms).",
            ephemeral=True,
        )

    @app_commands.command(name="latencia", description="Comprueba que el bot responde.")
    async def ping(self, interaction: discord.Interaction) -> None:
        """Responde con la latencia actual de la conexión con Discord.

        No requiere permisos especiales. Responde de forma efímera porque
        el resultado solo es relevante para quien ejecuta el comando.
        """
        await self._ping_impl(InteractionResponder(interaction))

    @commands.command(name="latencia")
    async def ping_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.latencia`) de `/latencia`."""
        await self._ping_impl(ContextResponder(ctx))

    async def _help_impl(self, responder: CommandResponder) -> None:
        """Lógica compartida entre `/ayuda` y `.ayuda`."""
        embed = build_help_embed(self.bot, include_admin=is_admin(responder.member))
        await responder.send(embed=embed, ephemeral=True)

    @app_commands.command(name="ayuda", description="Muestra todos los comandos.")
    async def help_command(self, interaction: discord.Interaction) -> None:
        """Muestra un embed con todos los comandos."""
        await self._help_impl(InteractionResponder(interaction))

    @commands.command(name="ayuda")
    async def help_command_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.ayuda`) de `/ayuda`."""
        await self._help_impl(ContextResponder(ctx))


async def setup(bot: commands.Bot) -> None:
    """Punto de entrada usado por `bot.load_extension` para registrar el cog."""
    await bot.add_cog(General(bot))
