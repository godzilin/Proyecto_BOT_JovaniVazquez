"""Mascotas: `mascota`, sus cuidados y sus apariciones en los mensajes del bot.

- `mascota [miembro]` abre el panel de mascotas de alguien. Su dueño elige
  cuál ver, la acaricia, juega con ella, le da de comer lo que tenga en la
  mochila (nunca lo de edición limitada, que no se puede volver a comprar),
  le pone nombre y elige cuál le acompaña (la **activa**). Los
  demás solo miran. Dos atajos abren la tienda en privado: 🐾 Adoptar (la
  pestaña de mascotas) y 🥣 Comida (la Tienda de animales). Reglas en
  `bot.services.pets`; especies en `bot.services.pets_catalog`.
- Las mascotas se adoptan en la `tienda` (pestaña 🐾 Mascotas) o aparecen
  solas cuando se da su momento (te quedas a cero, cobras el IMV…).

**Subsistema de cameos.** Cualquier funcionalidad puede dejar que la mascota
activa diga algo en su mensaje con `cameo(bot, guild_id, user_id, moment)`,
que devuelve una línea de subtexto o `None`. Los resultados con dinero ya lo
hacen a través de `renta.hint(..., moment=...)`, así que un juego nuevo solo
tiene que pasar su `Moment` (`bot.services.pets.bet_moment` para una jugada
del casino). Lo demás (subir de nivel, un logro) llama a `cameo` directamente.
La mascota activa de cada miembro vive en una caché en memoria: un cameo no
toca la base de datos salvo cuando aparece una mascota nueva.

Otros cogs: `xp_bonus(bot, …)` da el bonus de XP de la mascota activa (lo usa
`bot.cogs.message_stats`) y `adopted(bot, …)` lo llama la tienda al vender una.

Dinero: cuidar no mueve dinero y los regalos son objetos del colmado, nunca
Y$, así que no hay impuestos ni gancho de la Renta. Lo que tributa es la
adopción, en la caja de la tienda (IGIC). Logros: categoría 🐾 Mascotas, con
`achievements.track` al cuidar y al adoptar y `achievements.note` en los
cameos y las apariciones (que llegan desde mensajes de otros cogs).
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.repositories.pets import Owner, PetRepository
from bot.repositories.shop import InventoryEntry, ShopRepository
from bot.services.achievements import (
    pet_adopt_stats,
    pet_cameo_stats,
    pet_care_stats,
    pet_name_stats,
    pet_switch_stats,
)
from bot.services.housing_furniture import FURNITURE_KEYS
from bot.services.levels import TIMEZONE
from bot.services.lootboxes import is_piece
from bot.services.pets import (
    BOND_LEVELS,
    CARE_RULES,
    GIFT_CHANCE,
    GIFT_MAX_PRICE,
    MAX_BOND_LEVEL,
    MAX_NAME,
    Care,
    Moment,
    PetState,
    apply_care,
    bond_level,
    cameo_text,
    care_day,
    care_text,
    clean_name,
    next_level_at,
    pick_gift,
    pick_line,
    streak_after,
    unlocked_tricks,
    wants_cameo,
)
from bot.services.pets import (
    xp_bonus as species_xp_bonus,
)
from bot.services.pets_catalog import CATALOG_PREFIX, SPAWNING, SPECIES_BY_KEY, Species
from bot.services.shop import Kind, ShopItem
from bot.services.shop_catalog import CATALOG, food_of, is_skin, is_title, use_of
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit, notify

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

COLOR = discord.Color.from_rgb(240, 160, 200)
#: Pasillo de la tienda con la comida y las cosas de las mascotas (atajo 🥣 Comida).
FOOD_AISLE = "animales"
VIEW_TIMEOUT = 600
#: Opciones de un desplegable de Discord.
SELECT_LIMIT = 25
_CARE_BUTTONS: tuple[tuple[Care, str, str], ...] = (
    (Care.PET, "🫳", "Acariciar"),
    (Care.PLAY, "🎾", "Jugar"),
    (Care.FEED, "🍽️", "Dar de comer"),
)


def _name(user: discord.abc.User) -> str:
    return discord.utils.escape_markdown(user.display_name)


def pet_name(state: PetState, species: Species) -> str:
    """Nombre que se enseña: el que le puso su dueño o, si no, el de la especie."""
    return state.name or species.name


def _button(
    label: str,
    callback: Callable[[discord.Interaction], Awaitable[None]],
    *,
    custom_id: str,
    emoji: str | None = None,
    style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    disabled: bool = False,
) -> ui.Button:
    button: ui.Button = ui.Button(
        label=label, emoji=emoji, style=style, disabled=disabled, custom_id=custom_id
    )
    button.callback = callback  # type: ignore[method-assign]
    return button


def bond_bar(bond: int) -> str:
    """Barra del vínculo hasta el siguiente nivel: `▰▰▰▱▱▱▱▱▱▱`."""
    level = bond_level(bond)
    if level >= MAX_BOND_LEVEL:
        return "▰" * 10
    low, high = BOND_LEVELS[level], BOND_LEVELS[level + 1]
    filled = int(10 * (bond - low) / (high - low))
    return "▰" * filled + "▱" * (10 - filled)


def focus_text(species: Species, level: int) -> str:
    """El bonus de XP de la especie a ese nivel, en palabras."""
    message = species_xp_bonus(level, species.xp_focus, voice=False) - 1
    voice = species_xp_bonus(level, species.xp_focus, voice=True) - 1
    if message == voice:
        return f"+{message * 100:.1f} % de XP".replace(".", ",")
    if voice > message:
        return f"+{voice * 100:.1f} % de XP en voz".replace(".", ",")
    return f"+{message * 100:.1f} % de XP por mensajes".replace(".", ",")


def pet_card(
    state: PetState, species: Species, *, active: bool, now: float, note: str | None
) -> str:
    """Ficha de una mascota en el panel."""
    level = state.level
    goal = next_level_at(state.bond)
    progress = f"{state.bond}/{goal}" if goal is not None else f"{state.bond} (máximo)"
    day = care_day(now)
    today = state.today if state.day == day else {}
    done = " · ".join(
        f"{emoji} {today.get(care.value, 0)}/{CARE_RULES[care].daily}"
        for care, emoji, _label in _CARE_BUTTONS
    )
    tricks = species.tricks[: unlocked_tricks(level)]
    lines = [
        f"## {species.emoji} {discord.utils.escape_markdown(pet_name(state, species))}",
        f"-# {species.name} · {species.rarity}" + (" · ⭐ Va contigo" if active else ""),
        f"*{species.personality}*",
        f"💞 Vínculo nivel **{level}** · {bond_bar(state.bond)} {progress}",
        f"📈 {focus_text(species, level)}" + ("" if active else " (si va contigo)"),
        f"🗓️ Hoy: {done}",
    ]
    if tricks:
        lines.append("🎪 Trucos: " + " · ".join(t.split(".")[0] for t in tricks))
    else:
        lines.append("🎪 Aún no sabe trucos: el primero llega a nivel 3.")
    if species.diet == "nada":
        lines.append("-# No come: lo suyo es la compañía.")
    elif species.diet == "todo":
        lines.append(
            "-# Come cualquier objeto de la mochila, no solo comida, salvo los de edición limitada."
        )
    if note:
        lines += ["", note]
    return "\n".join(lines)


# -- Panel ----------------------------------------------------------------------------


class PetPanel(ui.LayoutView):
    """Panel de mascotas de un miembro. Solo su dueño puede tocar los botones."""

    def __init__(
        self,
        cog: Mascotas,
        guild: discord.Guild,
        member: discord.abc.User,
        viewer: discord.abc.User,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild = guild
        self.member = member
        self.viewer = viewer
        self.pets: list[PetState] = []
        self.owner = Owner()
        self.selected: int | None = None
        #: Lo último que ha pasado (la frase del cuidado), para enseñarlo en la ficha.
        self.note: str | None = None
        self.message: discord.Message | None = None
        self.interaction: discord.Interaction | None = None

    @property
    def mine(self) -> bool:
        return self.viewer.id == self.member.id

    @property
    def current(self) -> PetState | None:
        """La mascota que se está viendo."""
        return next((p for p in self.pets if p.id == self.selected), None)

    async def load(self) -> None:
        """Lee las mascotas y el dueño, y elige qué mascota enseñar.

        No rehace los botones: eso lo hace `rebuild` justo antes de enseñar el
        panel. Si un cuidado se rechaza (pide un respiro, la comida ya no está),
        el mensaje no se edita, y unos botones rehechos sin editar dejarían a
        discord.py apuntando a los viejos: tiraría todos los clics siguientes.
        """
        self.pets = [
            p
            for p in await self.cog.repository.pets(self.guild.id, self.member.id)
            if p.species in SPECIES_BY_KEY
        ]
        self.owner = await self.cog.repository.owner(self.guild.id, self.member.id)
        if self.selected not in {p.id for p in self.pets}:
            ids = {p.id for p in self.pets}
            self.selected = (
                self.owner.active_id
                if self.owner.active_id in ids
                else (self.pets[0].id if self.pets else None)
            )

    def rebuild(self) -> None:
        self.clear_items()
        now = self.cog.clock()
        container = ui.Container(accent_colour=COLOR)
        title = f"# 🐾 Mascotas de {_name(self.member)}"
        state = self.current
        if state is None:
            hints = "\n".join(f"- {s.emoji} {s.name}: {s.spawn.hint}" for s in SPAWNING if s.spawn)
            container.add_item(
                ui.TextDisplay(
                    f"{title}\nTodavía no hay nadie en casa. Adopta en la `tienda` "
                    "(pestaña 🐾 Mascotas) o espera a que alguna aparezca sola:\n" + hints
                )
            )
            self.add_item(container)
            return
        species = SPECIES_BY_KEY[state.species]
        streak = (
            f"🔥 Racha cuidando: {self.owner.streak} días (mejor: {self.owner.best_streak})"
            if self.owner.streak
            else "🔥 Sin racha: cuida a una mascota hoy para empezarla."
        )
        container.add_item(ui.TextDisplay(f"{title}\n-# {streak}"))
        container.add_item(ui.Separator())
        active = state.id == self.owner.active_id
        container.add_item(
            ui.TextDisplay(pet_card(state, species, active=active, now=now, note=self.note))
        )
        others = [p for p in self.pets if p.id != state.id]
        if others:
            container.add_item(ui.Separator())
            lines = [
                f"{SPECIES_BY_KEY[p.species].emoji} "
                f"{discord.utils.escape_markdown(pet_name(p, SPECIES_BY_KEY[p.species]))}"
                f" · nivel {p.level}" + (" · ⭐" if p.id == self.owner.active_id else "")
                for p in others[:30]
            ]
            more = f"\n-# …y {len(others) - 30} más." if len(others) > 30 else ""
            container.add_item(ui.TextDisplay("### En casa\n" + "\n".join(lines) + more))
        self.add_item(container)

        if len(self.pets) > 1:
            options = [
                discord.SelectOption(
                    label=pet_name(p, SPECIES_BY_KEY[p.species])[:100],
                    value=str(p.id),
                    description=f"{SPECIES_BY_KEY[p.species].name} · nivel {p.level}"[:100],
                    emoji=_select_emoji(SPECIES_BY_KEY[p.species].emoji),
                    default=p.id == state.id,
                )
                for p in self.pets[:SELECT_LIMIT]
            ]
            select: ui.Select = ui.Select(
                placeholder="🐾 Ver otra mascota", options=options, custom_id="mascota:elegir"
            )

            async def pick(interaction: discord.Interaction) -> None:
                self.selected, self.note = int(select.values[0]), None
                self.rebuild()
                await interaction.response.edit_message(
                    view=self, allowed_mentions=discord.AllowedMentions.none()
                )

            select.callback = pick  # type: ignore[method-assign]
            row: ui.ActionRow = ui.ActionRow()
            row.add_item(select)
            self.add_item(row)

        if self.mine:
            care_row: ui.ActionRow = ui.ActionRow()
            for care, emoji, label in _CARE_BUTTONS:
                care_row.add_item(
                    _button(
                        label, self._care(care), custom_id=f"mascota:cuidar:{care}", emoji=emoji
                    )
                )
            self.add_item(care_row)
            extra: ui.ActionRow = ui.ActionRow()
            extra.add_item(
                _button(
                    "Va contigo" if active else "Llevar contigo",
                    self._activate,
                    custom_id="mascota:activar",
                    emoji="⭐",
                    style=discord.ButtonStyle.primary,
                    disabled=active,
                )
            )
            extra.add_item(
                _button("Ponerle nombre", self._rename, custom_id="mascota:nombre", emoji="✏️")
            )
            extra.add_item(
                _button(
                    "Adoptar", self._shop(None, "mascota"), custom_id="mascota:adoptar", emoji="🐾"
                )
            )
            extra.add_item(
                _button(
                    "Comida", self._shop(FOOD_AISLE, "todo"), custom_id="mascota:comida", emoji="🥣"
                )
            )
            self.add_item(extra)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.member.id:
            return True
        await interaction.response.send_message(
            "Estas mascotas no son tuyas. Mira las tuyas con `mascota`.", ephemeral=True
        )
        return False

    def _care(self, care: Care) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            if care is Care.FEED:
                await self.cog.open_feeding(interaction, self)
            else:
                await self.cog.care(interaction, self, care)

        return callback

    def _shop(
        self, aisle: str | None, tab: str
    ) -> Callable[[discord.Interaction], Awaitable[None]]:
        """Atajo a la tienda en privado, en el pasillo y la pestaña de las mascotas."""

        async def callback(interaction: discord.Interaction) -> None:
            # Importado aquí: la tienda ya importa este módulo para sus mascotas.
            from bot.cogs import shop as tienda

            await ack(interaction, new_message=True)
            if not await tienda.open_aisle(self.cog.bot, interaction, aisle, tab=tab):
                await edit(interaction, content="La tienda está cerrada ahora mismo.")

        return callback

    async def _activate(self, interaction: discord.Interaction) -> None:
        await self.cog.activate(interaction, self)

    async def _rename(self, interaction: discord.Interaction) -> None:
        state = self.current
        if state is None:
            await ack(interaction)
            return
        await interaction.response.send_modal(RenameForm(self.cog, self, state))

    async def refresh(self, interaction: discord.Interaction | None = None) -> None:
        """Vuelve a leer y a pintar el panel (desde un botón o desde otra vista).

        Args:
            interaction: El clic en un botón del propio panel. Su token sirve 15
                minutos para editar el mensaje, así que se guarda: el desplegable
                de comida, que es otro mensaje, repinta el panel con el último.
        """
        await self.load()
        self.rebuild()
        mentions = discord.AllowedMentions.none()
        try:
            if interaction is not None:
                await edit(interaction, view=self, allowed_mentions=mentions)
                self.interaction = interaction
            elif self.interaction is not None:
                await self.interaction.edit_original_response(view=self, allowed_mentions=mentions)
            elif self.message is not None:
                await self.message.edit(view=self, allowed_mentions=mentions)
        except discord.HTTPException:
            logger.debug("No se pudo repintar el panel de mascotas", exc_info=True)

    async def on_timeout(self) -> None:
        for child in self.walk_children():
            if isinstance(child, ui.Button | ui.Select):
                child.disabled = True
        try:
            if self.interaction is not None:
                await self.interaction.edit_original_response(view=self)
            elif self.message is not None:
                await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar el panel de mascotas", exc_info=True)


def _select_emoji(emoji: str) -> discord.PartialEmoji | str | None:
    try:
        return discord.PartialEmoji.from_str(emoji)
    except (TypeError, ValueError):
        return None


class RenameForm(ui.Modal):
    """Formulario para ponerle nombre a una mascota."""

    def __init__(self, cog: Mascotas, panel: PetPanel, state: PetState) -> None:
        super().__init__(title="Ponerle nombre", timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.panel = panel
        self.state = state
        self.text: ui.TextInput = ui.TextInput(
            placeholder="Michi, Toby, Perro Sanxe…",
            default=state.name or None,
            max_length=MAX_NAME,
            required=True,
        )
        self.add_item(ui.Label(text="Nombre", component=self.text))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.rename(interaction, self.panel, self.state, self.text.value)


class FoodPicker(ui.View):
    """Desplegable con lo que se puede comer de la mochila. Solo lo ve el dueño."""

    def __init__(
        self,
        cog: Mascotas,
        panel: PetPanel,
        state: PetState,
        foods: list[InventoryEntry],
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.panel = panel
        self.state = state
        self.done = False
        species = SPECIES_BY_KEY[state.species]
        groups: dict[int, list[InventoryEntry]] = {}
        for entry in foods:
            groups.setdefault(entry.item_id, []).append(entry)
        ordered = sorted(
            groups.values(),
            key=lambda g: (g[0].catalog_key not in species.favourites, g[0].name),
        )
        self.groups = {str(g[0].item_id): g for g in ordered[:SELECT_LIMIT]}
        options = [
            discord.SelectOption(
                label=f"{g[0].name} ×{len(g)}"[:100] if len(g) > 1 else g[0].name[:100],
                value=key,
                emoji=_select_emoji(g[0].emoji),
                description="⭐ Su favorita" if g[0].catalog_key in species.favourites else None,
            )
            for key, g in self.groups.items()
        ]
        select: ui.Select = ui.Select(placeholder="🍽️ ¿Qué le das?", options=options)
        select.callback = self._picked  # type: ignore[method-assign]
        self.select = select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.panel.member.id

    async def _picked(self, interaction: discord.Interaction) -> None:
        if self.done:
            await ack(interaction)
            return
        self.done = True
        self.stop()
        group = self.groups[self.select.values[0]]
        await self.cog.care(interaction, self.panel, Care.FEED, food=min(group, key=lambda e: e.id))


# -- Cog ------------------------------------------------------------------------------


class Mascotas(commands.Cog):
    """Panel de mascotas, cuidados, apariciones y cameos."""

    def __init__(
        self,
        bot: commands.Bot,
        repository: PetRepository,
        shop: ShopRepository,
        *,
        clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
    ) -> None:
        self.bot = bot
        self.repository = repository
        self.shop = shop
        self.clock = clock
        self.rng = rng or random.Random()
        #: Mascota activa de cada `(servidor, miembro)`. Se rellena al cargar y se
        #: actualiza al cuidar, renombrar o cambiar de mascota.
        self.active: dict[tuple[int, int], PetState] = {}
        #: Último cameo de cada dueño (epoch), para no repetir en jugadas seguidas.
        self.last_cameo: dict[tuple[int, int], float] = {}

    async def cog_load(self) -> None:
        """Prepara las tablas y carga las mascotas activas en memoria."""
        # Idempotente: el bot ya lo hace en `setup_hook`, pero así el cog se
        # puede cargar solo. Las mascotas leen la mochila de la tienda.
        await self.shop.initialize()
        await self.repository.initialize()
        for state in await self.repository.active_pets():
            self.active[(state.guild_id, state.user_id)] = state

    # -- Puentes ---------------------------------------------------------------------

    def xp_bonus(self, guild_id: int, user_id: int, *, voice: bool) -> float:
        """Multiplicador de XP de la mascota activa (1,0 si no lleva ninguna)."""
        state = self.active.get((guild_id, user_id))
        species = SPECIES_BY_KEY.get(state.species) if state else None
        if state is None or species is None:
            return 1.0
        return species_xp_bonus(state.level, species.xp_focus, voice=voice)

    async def cameo(self, guild_id: int, user_id: int, moment: Moment | None) -> str | None:
        """Lo que dice la mascota activa (y si aparece una nueva) en un momento.

        Args:
            moment: Lo que ha pasado. `None` es un resultado con dinero sin más
                detalle: la mascota puede hablar, pero no aparece ninguna.

        Returns:
            Líneas de subtexto listas para añadir al mensaje, o `None`.
        """
        lines = []
        if moment is not None and (spawned := await self._maybe_spawn(guild_id, user_id, moment)):
            lines.append(spawned)
        key = (guild_id, user_id)
        state = self.active.get(key)
        species = SPECIES_BY_KEY.get(state.species) if state else None
        if state is not None and species is not None:
            now = self.clock()
            last = self.last_cameo.get(key)
            here = moment or Moment()
            hour = datetime.fromtimestamp(now, TIMEZONE).hour
            if wants_cameo(
                species.talk,
                here,
                self.rng,
                hour=hour,
                night_owl=species.night_owl,
                since_last=None if last is None else now - last,
            ):
                self.last_cameo[key] = now
                template = pick_line(species.lines, here, self.rng)
                lines.append(
                    cameo_text(
                        template,
                        emoji=species.emoji,
                        name=discord.utils.escape_markdown(pet_name(state, species)),
                        sound=species.sound,
                    )
                )
                logros.note(
                    self.bot, guild_id, user_id, pet_cameo_stats(event=here.event, when=now)
                )
        return "\n".join(lines) or None

    async def _maybe_spawn(self, guild_id: int, user_id: int, moment: Moment) -> str | None:
        """Si toca, mete en casa del miembro una mascota de las que aparecen solas."""
        now = self.clock()
        when = datetime.fromtimestamp(now, TIMEZONE)
        candidates = [s for s in SPAWNING if s.spawn and s.spawn.fires(moment, when, self.rng)]
        if not candidates:
            return None
        owned = await self.repository.owned_species(guild_id, user_id)
        species = next((s for s in candidates if s.key not in owned), None)
        if species is None:
            return None
        item = await self.shop.item_by_key(guild_id, species.catalog_key)
        if item is None:
            # El servidor aún no ha abierto la tienda: se siembra el surtido ahora.
            await self.shop.stock_catalog(guild_id, CATALOG, now)
            item = await self.shop.item_by_key(guild_id, species.catalog_key)
        if item is None:
            # Un administrador la ha retirado del catálogo: aquí no aparece.
            return None
        await self.shop.grant(guild_id, user_id, item, now)
        pets = await self.repository.pets(guild_id, user_id)
        await self._ensure_active(guild_id, user_id, pets)
        logros.note(
            self.bot,
            guild_id,
            user_id,
            pet_adopt_stats(species=species.key, spawned=True, owned=len(pets)),
        )
        return (
            f"-# {species.emoji} ¡Sorpresa! **{species.name}** se ha mudado contigo sin "
            "preguntar. Es tuya: mírala en `mascota`."
        )

    async def _ensure_active(self, guild_id: int, user_id: int, pets: list[PetState]) -> None:
        """Si el miembro no lleva ninguna mascota válida, le acompaña la más nueva."""
        owner = await self.repository.owner(guild_id, user_id)
        valid = {p.id for p in pets}
        if pets and owner.active_id not in valid:
            newest = pets[-1]
            await self.repository.set_active(guild_id, user_id, newest.id)
            self.active[(guild_id, user_id)] = newest

    async def adopted(
        self, guild_id: int, member: discord.abc.User, channel: object, item: ShopItem
    ) -> None:
        """Tras vender una mascota en la tienda: la prepara y apunta los logros."""
        species = SPECIES_BY_KEY.get((item.catalog_key or "").removeprefix(CATALOG_PREFIX))
        if species is None:
            return
        pets = await self.repository.pets(guild_id, member.id)
        await self._ensure_active(guild_id, member.id, pets)
        await logros.track(
            self.bot,
            guild_id,
            member,
            channel,
            pet_adopt_stats(
                species=species.key, spawned=False, owned=len(pets), adoption=species.adoption
            ),
        )

    # -- Panel -----------------------------------------------------------------------

    async def panel(
        self, guild: discord.Guild, member: discord.abc.User, viewer: discord.abc.User
    ) -> PetPanel:
        """Panel de mascotas de `member` visto por `viewer`, listo para enviar."""
        view = PetPanel(self, guild, member, viewer)
        await view.load()
        view.rebuild()
        return view

    async def care(
        self,
        interaction: discord.Interaction,
        panel: PetPanel,
        action: Care,
        *,
        food: InventoryEntry | None = None,
    ) -> None:
        """Acaricia, juega o da de comer a la mascota que se ve en el panel.

        Al comer, gasta de la mochila lo que se le da (salvo a las que no comen).
        """
        # Leer y guardar el vínculo van a la base de datos: se acepta el clic antes.
        await ack(interaction)
        await panel.load()
        state = panel.current
        guild, member = panel.guild, interaction.user
        if state is None:
            await notify(interaction, "Esa mascota ya no está.")
            return
        species = SPECIES_BY_KEY[state.species]
        now = self.clock()
        eats = species.diet != "nada"
        food_label = None
        if action is Care.FEED and eats:
            if food is None or not await self.shop.consume(guild.id, member.id, food.id):
                await notify(interaction, "Eso ya no está en tu mochila.")
                return
            food_label = f"{food.emoji} {food.name}"
        favourite = food is not None and food.catalog_key in species.favourites
        outcome = apply_care(state, action, now, favourite=favourite)
        if outcome.wait:
            if food is not None and eats:
                await self.shop.restore(food.id)
            minutes = max(1, -(-outcome.wait // 60))
            await notify(
                interaction,
                f"⏳ {species.emoji} Dale un respiro: podrás volver dentro de {minutes} min.",
            )
            return

        owner = await self.repository.owner(guild.id, member.id)
        today = care_day(now)
        first_today = owner.last_day != today
        streak = streak_after(owner.last_day, owner.streak, today)
        await self.repository.save_care(
            state, streak=streak, best_streak=max(streak, owner.best_streak), day=today
        )
        if (guild.id, member.id) in self.active and self.active[
            (guild.id, member.id)
        ].id == state.id:
            self.active[(guild.id, member.id)] = state

        trick = None
        known = unlocked_tricks(state.level)
        if action is Care.PLAY and known and (outcome.new_trick or self.rng.random() < 0.5):
            trick = (
                species.tricks[known - 1]
                if outcome.new_trick
                else self.rng.choice(species.tricks[:known])
            )
        name = discord.utils.escape_markdown(pet_name(state, species))
        lines = [
            f"{species.emoji} "
            + care_text(
                action,
                self.rng,
                name=name,
                sound=species.sound,
                food=food_label,
                favourite=favourite,
                trick=trick,
                eats=eats,
            )
        ]
        if outcome.points:
            lines.append(f"-# 💞 +{outcome.points} de vínculo.")
        else:
            lines.append("-# 💞 Hoy ya ha tenido bastante de esto: no suma más vínculo.")
        if outcome.level_up is not None:
            lines.append(f"🎉 **¡Vínculo a nivel {outcome.level_up}!**")
            if outcome.new_trick:
                lines.append("🎪 ¡Ha aprendido un truco nuevo!")
        gift = None
        if (
            first_today
            and species.gift_rate
            and self.rng.random() < GIFT_CHANCE * species.gift_rate
        ):
            gift = await self._gift(guild.id, member.id, now)
            if gift is not None:
                label = f"{gift.emoji} **{gift.name}**"
                lines.append("🎁 " + species.gift_line.format(pet=f"**{name}**", gift=label))
        panel.note = "\n".join(lines)
        if food is None:
            await panel.refresh(interaction)
        else:
            # Viene del desplegable privado de comida: se cierra ese y se repinta
            # el panel público, que es otro mensaje.
            await edit(interaction, content=lines[0], view=None)
            await panel.refresh()

        await logros.track(
            self.bot,
            guild.id,
            member,
            interaction.channel,
            pet_care_stats(
                action=action.value,
                points=outcome.points,
                favourite=favourite,
                level=state.level,
                tricks=unlocked_tricks(state.level),
                streak=streak,
                gift=gift is not None,
                when=now,
                food_key=food.catalog_key if food is not None else None,
                species=species.key,
                eats=eats,
            ),
        )

    async def _gift(self, guild_id: int, user_id: int, now: float) -> ShopItem | None:
        """Mete en la mochila un regalo de la mascota: algo barato, ilimitado y sin uso."""
        candidates = [
            item
            for item in await self.shop.items(guild_id)
            if item.kind is Kind.TROPHY
            and item.stock is None
            and item.price <= GIFT_MAX_PRICE
            and use_of(item.catalog_key) is None
            and not is_title(item.catalog_key)
            and not is_skin(item.catalog_key)
            and not is_piece(item.catalog_key)
            and item.catalog_key not in FURNITURE_KEYS  # suben la renta: no se regalan
        ]
        if not candidates:
            return None
        prize = candidates[pick_gift([c.price for c in candidates], self.rng)]
        await self.shop.grant(guild_id, user_id, prize, now)
        return prize

    async def open_feeding(self, interaction: discord.Interaction, panel: PetPanel) -> None:
        """Abre el desplegable de comida (o da de comer sin más a las que no comen)."""
        await ack(interaction)
        await panel.load()
        state = panel.current
        if state is None:
            await notify(interaction, "Esa mascota ya no está.")
            return
        species = SPECIES_BY_KEY[state.species]
        if species.diet == "nada":
            await self.care(interaction, panel, Care.FEED)
            return
        entries = await self.shop.inventory(panel.guild.id, panel.member.id, self.clock())
        # Lo de edición limitada no se vuelve a comprar: comérselo sin querer lo pierde
        # para siempre (así se fue una patata con forma de España). Cuenta el artículo,
        # no solo el número de serie, porque lo que da `crear` llega sin número.
        items = await self.shop.items(panel.guild.id, include_hidden=True)
        limited = {i.id for i in items if i.stock is not None}
        foods = [
            e
            for e in entries
            if e.kind is Kind.TROPHY
            and e.serial is None
            and e.item_id not in limited
            and (species.diet == "todo" or food_of(e.catalog_key))
        ]
        if not foods:
            await notify(
                interaction,
                "No tienes nada de comer en la mochila. En la `tienda` lo que se come lleva "
                "🍽️ (la 🐾 Tienda de animales tiene pienso).",
            )
            return
        await notify(
            interaction,
            f"{species.emoji} ¿Qué le das a **"
            f"{discord.utils.escape_markdown(pet_name(state, species))}**?",
            view=FoodPicker(self, panel, state, foods),
        )

    async def activate(self, interaction: discord.Interaction, panel: PetPanel) -> None:
        """La mascota que se ve pasa a ir con su dueño."""
        await ack(interaction)
        await panel.load()
        state = panel.current
        if state is None:
            await ack(interaction)
            return
        guild_id, user_id = panel.guild.id, panel.member.id
        await self.repository.set_active(guild_id, user_id, state.id)
        self.active[(guild_id, user_id)] = state
        species = SPECIES_BY_KEY[state.species]
        panel.note = (
            f"⭐ {species.emoji} **{discord.utils.escape_markdown(pet_name(state, species))}** "
            "va contigo a partir de ahora. Saldrá en tus mensajes."
        )
        await panel.refresh(interaction)
        logros.note(self.bot, guild_id, user_id, pet_switch_stats())

    async def rename(
        self, interaction: discord.Interaction, panel: PetPanel, state: PetState, text: str
    ) -> None:
        """Le pone nombre a una mascota."""
        try:
            name = clean_name(text)
        except ValueError as error:
            await interaction.response.send_message(f"❌ {error}", ephemeral=True)
            return
        await ack(interaction)
        await panel.load()
        current = next((p for p in panel.pets if p.id == state.id), None)
        if current is None:
            await notify(interaction, "Esa mascota ya no está.")
            return

        current.name = name
        await self.repository.save(current)
        key = (panel.guild.id, panel.member.id)
        if key in self.active and self.active[key].id == current.id:
            self.active[key] = current
        species = SPECIES_BY_KEY[current.species]
        panel.selected = current.id
        panel.note = (
            f"✏️ {species.emoji} Ahora se llama **{discord.utils.escape_markdown(name)}**. "
            f"Responde cuando le apetece."
        )
        await panel.refresh(interaction)
        await logros.track(
            self.bot,
            panel.guild.id,
            interaction.user,
            interaction.channel,
            pet_name_stats(name=name),
        )

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra las mascotas del servidor que el bot abandona."""
        await self.repository.delete_guild_data(guild.id)
        for key in [k for k in self.active if k[0] == guild.id]:
            del self.active[key]

    # -- Comandos --------------------------------------------------------------------

    @app_commands.command(
        name="mascota", description="Tus mascotas: cuídalas y elige cuál va contigo."
    )
    @app_commands.describe(miembro="De quién (por defecto, las tuyas).")
    @app_commands.guild_only()
    async def mascota(
        self, interaction: discord.Interaction, miembro: discord.Member | None = None
    ) -> None:
        """Enseña el panel de mascotas; solo su dueño puede cuidarlas."""
        assert interaction.guild is not None  # guild_only
        # Las mascotas se leen de la base de datos: «pensando…» mientras tanto.
        await interaction.response.defer(thinking=True)
        view = await self.panel(interaction.guild, miembro or interaction.user, interaction.user)
        await edit(interaction, view=view, allowed_mentions=discord.AllowedMentions.none())
        view.interaction = interaction

    @commands.command(name="mascota")
    @commands.guild_only()
    async def mascota_text(
        self, ctx: commands.Context, miembro: discord.Member | None = None
    ) -> None:
        """Versión de texto: `.mascota` o `.mascota @alguien`."""
        assert ctx.guild is not None  # guild_only
        view = await self.panel(ctx.guild, miembro or ctx.author, ctx.author)
        view.message = await ctx.send(view=view, allowed_mentions=discord.AllowedMentions.none())


# -- Puntos de entrada para otros cogs ------------------------------------------------


def xp_bonus(bot: commands.Bot, guild_id: int, user_id: int, *, voice: bool) -> float:
    """Bonus de XP de la mascota activa; 1,0 si no hay o si el cog no está cargado."""
    if (cog := find_cog(bot, Mascotas)) is not None:
        return cog.xp_bonus(guild_id, user_id, voice=voice)
    return 1.0


def active_species(bot: commands.Bot, guild_id: int, user_id: int) -> str | None:
    """Clave de la especie de la mascota activa (`hamster`), o `None`. Sale de memoria.

    La Rueda la mira para el cameo del hámster, que corre dentro de la rueda.
    """
    if (cog := find_cog(bot, Mascotas)) is None:
        return None
    state = cog.active.get((guild_id, user_id))
    return state.species if state is not None else None


async def cameo(
    bot: commands.Bot, guild_id: int, user_id: int, moment: Moment | None = None
) -> str | None:
    """Línea de la mascota activa para el mensaje de un resultado, o `None`.

    Nunca lanza: si algo falla se registra y el mensaje sale sin mascota.
    """
    if (cog := find_cog(bot, Mascotas)) is None:
        return None
    try:
        return await cog.cameo(guild_id, user_id, moment)
    except Exception:
        logger.exception("No se pudo sacar a la mascota de %s", user_id)
        return None


async def adopted(
    bot: commands.Bot, guild_id: int, member: discord.abc.User, channel: object, item: ShopItem
) -> None:
    """Avisa al cog de que se ha vendido una mascota. Nunca lanza."""
    if (cog := find_cog(bot, Mascotas)) is None:
        return
    try:
        await cog.adopted(guild_id, member, channel, item)
    except Exception:
        logger.exception("No se pudo preparar la mascota adoptada por %s", member.id)


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con las mascotas y la tienda del bot."""
    await bot.add_cog(Mascotas(bot, bot.pets, bot.shop))
