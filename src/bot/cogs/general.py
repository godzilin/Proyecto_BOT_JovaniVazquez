"""Cog con comandos generales: `/latencia` y `/ayuda`.

`/ayuda` abre un menú desplegable, como el de `perfil`: una portada con las
categorías y una página por categoría con cada comando y lo que hace.

Cada comando tiene un único nombre corto, idéntico en las dos interfaces:
comando de aplicación (`/latencia`) y comando de texto (`.latencia`). Ambas
comparten la misma lógica a través de `CommandResponder`
(`bot.utils.responder`).
"""

from __future__ import annotations

import logging
import time
from typing import NamedTuple

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import DEFAULT_COMMAND_PREFIX
from bot.utils.interactions import notify
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

logger = logging.getLogger(__name__)

# Color de acento de los embeds del cog, coherente con el resto del bot.
EMBED_COLOR = discord.Color.blurple()

# Categorías de la ayuda por nombre de cog, en el orden del menú. Varios cogs
# pueden compartir categoría; un cog nuevo que no esté aquí aparece igualmente,
# al final, como "Otros".
HELP_CATEGORIES: dict[str, str] = {
    "Perfil": "👤 Perfil",
    "MessageStats": "👤 Perfil",
    "Trabajo": "👤 Perfil",
    "Casino": "🎰 Casino",
    "Blackjack": "🎰 Casino",
    "Tragaperras": "🎰 Casino",
    "Botes": "🎰 Casino",
    "Crash": "🎰 Casino",
    "Minas": "🎰 Casino",
    "Torre": "🎰 Casino",
    "Guerra": "🎰 Casino",
    "Pollo": "🎰 Casino",
    "Moneda": "🎰 Casino",
    "Autobús": "🎰 Casino",
    "Allá tú": "🎰 Casino",
    "Póker": "🎰 Casino",
    "Dados": "🎰 Casino",
    "Caballos": "🎰 Casino",
    "Bacará": "🎰 Casino",
    "Gallos": "🎰 Casino",
    "Enraya": "🎰 Casino",
    "Backgammon": "🎰 Casino",
    "Pachinko": "🎰 Casino",
    "Pico": "🎰 Casino",
    "Loteria": "🎰 Casino",
    "Apuestas": "🎰 Casino",
    "Porras": "🎰 Casino",
    "Deportes": "🎰 Casino",
    "Renta": "🏦 Dinero",
    "Patrimonio": "🏦 Dinero",
    "Donaciones": "🏦 Dinero",
    "Bizum": "🏦 Dinero",
    "Pisos": "🏦 Dinero",
    "Banco": "🏦 Dinero",
    "Tienda": "🛍️ Tienda",
    "Pieles": "🛍️ Tienda",
    "Rastro": "🛍️ Tienda",
    "Mascotas": "🛍️ Tienda",
    "Music": "🎵 Música",
    "Birthdays": "🍻 Comunidad",
    "Lista": "🍻 Comunidad",
    "Beernight": "🍻 Comunidad",
    "Entrance": "🍻 Comunidad",
    "Fun": "🍻 Comunidad",
    "Aspecto": "🍻 Comunidad",
    "Images": "🎨 Imagen",
    "General": "⚙️ General",
}
# Comandos que van en otra categoría que la de su cog: el cog `Casino` lleva
# también el saldo, el IMV y Hacienda, que son dinero y no juegos.
COMMAND_CATEGORIES: dict[str, str] = {
    "saldo": "🏦 Dinero",
    "imv": "🏦 Dinero",
    "hacienda": "🏦 Dinero",
}
OTHER_CATEGORY = "📦 Otros"
# Categoría de los comandos de administración (cogs `Admin` y `Moderation`, el
# panel `admin`): solo se muestra a quien es administrador, para no enseñar a
# todo el mundo comandos que no puede usar.
ADMIN_COGS = frozenset({"Admin", "Moderation"})
ADMIN_CATEGORY = "🛡️ Admin"
# Página de portada del menú.
HOME = "📋 Inicio"

# Clave de `Command.extras` para los comandos que la ayuda lista en bloque,
# solo por nombre, dentro de la página de Imagen (los efectos, por tipo).
COMPACT_GROUP_KEY = "help_group"
# Clave opcional de `Command.extras`: posición del grupo en la ayuda (menor, antes).
COMPACT_ORDER_KEY = "help_order"

# Máximo de caracteres de un campo de embed (límite de Discord).
FIELD_LIMIT = 1024
# Separador entre nombres de comando dentro de un grupo.
SEPARATOR = " · "
# Segundos que el menú de la ayuda sigue respondiendo.
VIEW_TIMEOUT = 600


class HelpPage(NamedTuple):
    """Una página del menú de la ayuda: título con emoji, resumen y embed."""

    title: str
    summary: str
    embed: discord.Embed


def build_help_pages(bot: commands.Bot, *, include_admin: bool = False) -> list[HelpPage]:
    """Construye las páginas del menú de la ayuda: portada y una por categoría.

    Los comandos se leen de `bot.commands` en vez de una lista escrita a mano,
    para que la ayuda nunca se desincronice de lo registrado. Como `/nombre`
    y `.nombre` son idénticos, cada comando aparece una sola vez, con la
    descripción de su slash command (o la primera línea de su docstring si
    solo es de texto). Los efectos de imagen van en bloque, solo por nombre.

    Args:
        bot: Cliente con los cogs ya cargados.
        include_admin: Si se añade la categoría de administración (solo
            para quien tiene permiso de administrador).
    """
    prefix = _text_prefix(bot)
    visible = [c for c in bot.commands if not c.hidden]
    categories: dict[str, list[commands.Command]] = {}
    groups: dict[str, list[str]] = {}
    for command in sorted(visible, key=lambda c: c.extras.get(COMPACT_ORDER_KEY, 0)):
        if COMPACT_GROUP_KEY in command.extras:
            groups.setdefault(command.extras[COMPACT_GROUP_KEY], []).append(command.name)
        else:
            categories.setdefault(_category_title(command), []).append(command)

    titles = [*dict.fromkeys(HELP_CATEGORIES.values()), OTHER_CATEGORY]
    if include_admin:
        titles.append(ADMIN_CATEGORY)

    pages: list[HelpPage] = []
    for title in titles:
        listed = sorted(categories.get(title, []), key=lambda c: c.name)
        extra = groups if title == HELP_CATEGORIES["Images"] else {}
        count = len(listed) + sum(len(names) for names in extra.values())
        if not count:
            continue
        embed = discord.Embed(
            title=title,
            description="\n".join(f"`{c.name}` — {_describe(bot, c)}" for c in listed),
            color=EMBED_COLOR,
        )
        for group, names in extra.items():
            chunks = _chunk_names([f"`{name}`" for name in sorted(names)])
            for index, value in enumerate(chunks):
                name = f"{group} ({len(names)})" if index == 0 else f"{group} (cont.)"
                embed.add_field(name=name, value=value, inline=False)
        if extra:
            embed.set_footer(
                text=f"Los efectos van solo con {prefix} · {prefix}memes efecto explica uno"
            )
        pages.append(HelpPage(title, f"{count} {'comando' if count == 1 else 'comandos'}", embed))

    home = discord.Embed(
        title="📖 Comandos",
        description=(
            f"Todos funcionan con `/` y con `{prefix}` (los efectos de imagen, solo con "
            f"`{prefix}`). Elige una categoría en el menú para ver qué hace cada uno.\n\n"
            + "\n".join(f"{page.title} · {page.summary}" for page in pages)
        ),
        color=EMBED_COLOR,
    )
    return [HelpPage(HOME, "Cómo se usa y qué hay", home), *pages]


def _category_title(command: commands.Command) -> str:
    """Título de la categoría a la que pertenece el comando."""
    if command.cog_name in ADMIN_COGS:
        return ADMIN_CATEGORY
    if command.name in COMMAND_CATEGORIES:
        return COMMAND_CATEGORIES[command.name]
    return HELP_CATEGORIES.get(command.cog_name or "", OTHER_CATEGORY)


def _describe(bot: commands.Bot, command: commands.Command) -> str:
    """Descripción del comando: la de su slash command o, si no tiene, su docstring."""
    slash = bot.tree.get_command(command.name)
    if isinstance(slash, app_commands.Command) and slash.description != "…":
        return slash.description
    return command.short_doc or "Sin descripción."


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


class HelpSelect(discord.ui.Select):
    """Desplegable de categorías de la ayuda."""

    def __init__(self, view: HelpView) -> None:
        options = [
            discord.SelectOption(
                label=page.title.partition(" ")[2],
                value=page.title,
                emoji=page.title.partition(" ")[0],
                description=page.summary,
                default=page.title == view.key,
            )
            for page in view.pages.values()
        ]
        super().__init__(
            placeholder="Elige una categoría",
            options=options,
            row=0,
            custom_id="ayuda:categoria",
        )
        self.help_view = view

    async def callback(self, interaction: discord.Interaction) -> None:
        # Las páginas ya están hechas: solo cambia memoria, se contesta directamente.
        await self.help_view.show(interaction, self.values[0])


class HelpView(discord.ui.View):
    """Menú de `ayuda`: una página a la vista y un desplegable para cambiar.

    Solo quien la abrió la maneja (con `.ayuda` el mensaje es público).
    """

    def __init__(self, pages: list[HelpPage], owner_id: int) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.pages = {page.title: page for page in pages}
        self.owner_id = owner_id
        self.key = pages[0].title
        self.message: discord.Message | None = None
        self.add_item(HelpSelect(self))

    @property
    def embed(self) -> discord.Embed:
        """Embed de la página a la vista."""
        return self.pages[self.key].embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Cada uno maneja su propia ayuda."""
        if interaction.user.id == self.owner_id:
            return True
        await notify(interaction, "Esta ayuda la maneja quien la abrió. Abre la tuya con `ayuda`.")
        return False

    async def show(self, interaction: discord.Interaction, key: str) -> None:
        """Cambia a la página `key` en el mismo mensaje."""
        self.key = key
        self.clear_items()
        self.add_item(HelpSelect(self))
        await interaction.response.edit_message(embed=self.embed, view=self)

    async def on_timeout(self) -> None:
        """Al caducar, el menú se queda gris para que no parezca que funciona."""
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                logger.debug("No se pudo cerrar una ayuda caducada", exc_info=True)


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

    def _help_view(self, user: discord.abc.User) -> HelpView:
        """Menú de la ayuda para `user`, con Admin si es administrador."""
        member = user if isinstance(user, discord.Member) else None
        return HelpView(build_help_pages(self.bot, include_admin=is_admin(member)), user.id)

    @app_commands.command(name="ayuda", description="Todos los comandos, por categorías.")
    async def help_command(self, interaction: discord.Interaction) -> None:
        """Abre el menú de la ayuda, solo para quien la pide."""
        view = self._help_view(interaction.user)
        await interaction.response.send_message(embed=view.embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()

    @commands.command(name="ayuda")
    async def help_command_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.ayuda`) de `/ayuda`."""
        view = self._help_view(ctx.author)
        view.message = await ctx.send(embed=view.embed, view=view)


async def setup(bot: commands.Bot) -> None:
    """Punto de entrada usado por `bot.load_extension` para registrar el cog."""
    await bot.add_cog(General(bot))
