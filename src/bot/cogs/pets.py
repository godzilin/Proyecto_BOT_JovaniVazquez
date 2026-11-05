"""Mascotas: `mascota`, su retrato, sus cuidados, su armario y sus apariciones.

- `mascota [miembro]` abre el panel de mascotas de alguien, con el **retrato** de la
  que se ve (`bot.services.pet_scene`): su especie y su raza dibujadas, lo que lleva
  puesto, el fondo y su cara del momento (duerme de madrugada, sale contenta si la has
  cuidado hoy, de fiesta en su aniversario). Su dueño elige cuál ver, la acaricia,
  juega con ella, le da de comer lo que tenga en la mochila (nunca lo de edición
  limitada), la saca de paseo, la lleva de visita a casa de la mascota de otro, le pone
  nombre y elige cuál le acompaña (la **activa**). Los demás solo miran.
- 👗 **Vestir**: un desplegable por hueco (cabeza, ojos, cuello, cuerpo y fondo) con lo
  que tienes y lo que se vende. Lo que no tienes se ve puesto antes de pagarlo y se
  compra ahí mismo (`tienda.prepare_checkout`). Prendas en `bot.services.pet_wardrobe`.
- 🧬 **Raza**: igual, con las razas de su especie (`bot.services.pets_looks`).
- 📔 **Álbum**: las fotos que se hacen solas (al subir a nivel 3, 6, 9 y 10, en su
  aniversario, al estrenar ropa, de visita) y las que enseñas.
- 📸 **Enseñar**: publica el retrato en el canal.
- Dos atajos abren la tienda en privado: 🐾 Adoptar y 🥣 Comida.

Las mascotas se adoptan en la `tienda` (pestaña 🐾 Mascotas, que también tiene la
ropa) o aparecen solas cuando se da su momento (te quedas a cero, cobras el IMV…).

**El retrato no se sube si no cambia.** Cada mensaje con archivo cuesta ~1 s en
Discord: el panel guarda el resumen de lo que enseña (`portrait_key`) y solo adjunta
la imagen cuando cambia (como la lotería). Acariciar otra vez no la cambia. Si ni Node
ni Chromium pueden pintar, el panel sale en texto, como antes del retrato.

**Subsistema de cameos.** Cualquier funcionalidad puede dejar que la mascota activa
diga algo en su mensaje con `cameo(bot, guild_id, user_id, moment)`, que devuelve una
línea de subtexto o `None`. Los resultados con dinero ya lo hacen a través de
`renta.hint(..., moment=...)`. La mascota activa de cada miembro vive en una caché en
memoria: un cameo no toca la base de datos salvo cuando aparece una mascota nueva o es
su aniversario (una vez al año).

Otros cogs: `xp_bonus(bot, …)` da el bonus de XP de la mascota activa (lo usa
`bot.cogs.message_stats`) y `adopted(bot, …)` lo llama la tienda al vender una.

Dinero: cuidar, pasear, visitar y vestir no mueven dinero, y los regalos son objetos
del colmado. Lo que tributa son las compras: la adopción, la ropa y las razas pagan
IGIC en la caja de la tienda, que ya avisa de la Renta (`tienda.after_checkout`).
Logros: categoría 🐾 Mascotas, con `achievements.track` al cuidar, vestir, enseñar y
comprar, y `achievements.note` en los cameos, las apariciones y lo que le pasa a la
mascota de otro (una visita que recibe).
"""

from __future__ import annotations

import io
import logging
import random
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import shop as tienda
from bot.repositories.pets import Owner, PetRepository, PhotoRow
from bot.repositories.shop import InventoryEntry, ShopRepository
from bot.services import pet_scene
from bot.services.achievements import (
    pet_adopt_stats,
    pet_anniversary_stats,
    pet_breed_stats,
    pet_cameo_stats,
    pet_care_stats,
    pet_dress_stats,
    pet_hosted_stats,
    pet_name_stats,
    pet_photo_stats,
    pet_show_stats,
    pet_switch_stats,
)
from bot.services.economy import CURRENCY_EMOJI, format_amount
from bot.services.housing_furniture import FURNITURE_KEYS
from bot.services.levels import TIMEZONE
from bot.services.lootboxes import is_piece
from bot.services.pet_scene import card, pet_entry, portrait_key, portrait_state, visit_card
from bot.services.pet_wardrobe import (
    OUTFITS,
    SLOTS,
    Outfit,
    Slot,
    complete,
    in_season,
    in_slot,
    is_outfit,
    outfit_of,
    wearing,
)
from bot.services.pets import (
    ALBUM_LEVELS,
    BOND_LEVELS,
    CARE_RULES,
    GENERIC_LINES,
    GIFT_CHANCE,
    GIFT_MAX_PRICE,
    MAX_BOND_LEVEL,
    MAX_NAME,
    Care,
    Event,
    Moment,
    PetState,
    Photo,
    anniversary_years,
    apply_care,
    bond_level,
    cameo_text,
    care_day,
    care_text,
    clean_name,
    next_level_at,
    photo_caption,
    pick_gift,
    pick_line,
    portrait_mood,
    show_text,
    streak_after,
    unlocked_tricks,
    visit_text,
    wants_cameo,
)
from bot.services.pets import (
    xp_bonus as species_xp_bonus,
)
from bot.services.pets_catalog import CATALOG_PREFIX, SPAWNING, SPECIES_BY_KEY, Species
from bot.services.pets_looks import Breed, breed_catalog_key, is_breed, look_of, slots_of
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
#: Nombre del adjunto del retrato.
PORTRAIT = "mascota.png"
#: Segundos entre dos fotos enseñadas del mismo miembro (para no llenar el canal).
SHOW_GAP = 60
#: Valor del desplegable para quitarse la prenda de un hueco.
NOTHING = "-"
#: Prefijo del valor de una opción que se compra (aún no se tiene).
BUY = "comprar:"
_CARE_BUTTONS: tuple[tuple[Care, str, str], ...] = (
    (Care.PET, "🫳", "Acariciar"),
    (Care.PLAY, "🎾", "Jugar"),
    (Care.FEED, "🍽️", "Dar de comer"),
    (Care.WALK, "🦮", "Pasear"),
)
#: Cuidados que salen en «Hoy» de la ficha (con la visita, que no tiene botón propio).
_TODAY: tuple[tuple[Care, str], ...] = (
    *((care, emoji) for care, emoji, _label in _CARE_BUTTONS),
    (Care.VISIT, "🏠"),
)


class Mode(StrEnum):
    """Lo que enseña el panel."""

    HOME = "ficha"
    WARDROBE = "armario"
    BREED = "raza"
    ALBUM = "album"


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


def breed_for(state: PetState, owned: frozenset[str]) -> Breed:
    """La raza que se ve: la elegida si su dueño la tiene; si no, la de serie."""
    look = look_of(state.species)
    breed = look.breed(state.breed)
    if breed is not look.default and breed_catalog_key(state.species, breed.key) not in owned:
        return look.default
    return breed


def pet_card(
    state: PetState,
    species: Species,
    *,
    active: bool,
    now: float,
    note: str | None,
    breed: Breed | None = None,
    look: dict[Slot, Outfit] | None = None,
    compact: bool = False,
) -> str:
    """Ficha de una mascota en el panel.

    Args:
        compact: Si hay retrato: el nombre, la raza, la ropa y los trucos ya salen en la imagen,
            así que el texto se queda con lo que cambia en cada cuidado.
    """
    level = state.level
    goal = next_level_at(state.bond)
    progress = f"{state.bond}/{goal}" if goal is not None else f"{state.bond} (máximo)"
    day = care_day(now)
    today = state.today if state.day == day else {}
    done = " · ".join(
        f"{emoji} {today.get(care.value, 0)}/{CARE_RULES[care].daily}" for care, emoji in _TODAY
    )
    tricks = species.tricks[: unlocked_tricks(level)]
    diet = {
        "nada": "-# No come: lo suyo es la compañía.",
        "todo": "-# Come cualquier objeto de la mochila, no solo comida, salvo los de edición "
        "limitada.",
    }.get(species.diet)
    if compact:
        # Quien no ve la imagen (un lector de pantalla) sabe igual de quién es la ficha.
        lines = [
            f"-# {species.emoji} {discord.utils.escape_markdown(pet_name(state, species))} · "
            f"{species.name}" + (" · ⭐ Va contigo" if active else ""),
            f"💞 Vínculo nivel **{level}** · {bond_bar(state.bond)} {progress}",
            f"🗓️ Hoy: {done}",
        ]
        if diet:
            lines.append(diet)
        if note:
            lines += ["", note]
        return "\n".join(lines)
    lines = [
        f"## {species.emoji} {discord.utils.escape_markdown(pet_name(state, species))}",
        f"-# {species.name} · {species.rarity}" + (" · ⭐ Va contigo" if active else ""),
        f"*{species.personality}*",
    ]
    if years := anniversary_years(state.adopted_at, now):
        lines.append(f"🎂 Hoy hace {years} año{'s' if years > 1 else ''} que llegó a casa.")
    lines += [
        f"💞 Vínculo nivel **{level}** · {bond_bar(state.bond)} {progress}",
        f"📈 {focus_text(species, level)}" + ("" if active else " (si va contigo)"),
        f"🗓️ Hoy: {done}",
    ]
    if breed is not None and len(look_of(state.species).breeds) > 1:
        lines.append(f"🧬 Raza: {breed.name}")
    worn = [o.label for slot, o in (look or {}).items() if slot is not Slot.BACKDROP]
    if worn:
        lines.append("👗 Lleva: " + ", ".join(worn))
    if tricks:
        lines.append("🎪 Trucos: " + " · ".join(t.split(".")[0] for t in tricks))
    else:
        lines.append("🎪 Aún no sabe trucos: el primero llega a nivel 3.")
    if diet:
        lines.append(diet)
    if note:
        lines += ["", note]
    return "\n".join(lines)


def _select_emoji(emoji: str) -> discord.PartialEmoji | str | None:
    try:
        return discord.PartialEmoji.from_str(emoji)
    except (TypeError, ValueError):
        return None


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
        self.mode = Mode.HOME
        #: Lo que lleva puesto cada mascota (solo lo que su dueño aún tiene).
        self.looks: dict[int, dict[Slot, Outfit]] = {}
        #: Claves de serie de la mochila del dueño (ropa y razas).
        self.owned: frozenset[str] = frozenset()
        #: Lo último que ha pasado (la frase del cuidado), para enseñarlo en la ficha.
        self.note: str | None = None
        #: Si se acaba de cuidar: el retrato sale contento.
        self.cared = False
        #: Prenda o raza que se está mirando antes de comprarla, y su caja.
        self.preview: Outfit | Breed | None = None
        self.checkout: Any = None
        #: Fotos del álbum de la mascota que se ve y cuál se mira.
        self.album: list[PhotoRow] = []
        self.page = 0
        #: Retrato que enseña el panel y el que ya tiene el mensaje (para no resubirlo).
        self.image: bytes | None = None
        self.image_key: str | None = None
        self.sent_key: str | None = None
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
        """Lee las mascotas, el dueño, lo que llevan puesto y elige qué mascota enseñar.

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
        self.owned = await self.cog.owned_keys(self.guild.id, self.member.id)
        worn = await self.cog.repository.outfits([p.id for p in self.pets])
        self.looks = {p.id: wearing(worn.get(p.id, {}), self.owned) for p in self.pets}
        if self.mode is Mode.ALBUM and self.current is not None:
            self.album = await self.cog.repository.photos(self.current.id)
            self.page = min(self.page, max(0, len(self.album) - 1))

    # -- Retrato ----------------------------------------------------------------------

    def look_now(self, state: PetState) -> dict[Slot, Outfit]:
        """Lo que lleva, con la prenda que se está mirando puesta (vista previa)."""
        look = dict(self.looks.get(state.id, {}))
        if isinstance(self.preview, Outfit):
            look[self.preview.slot] = self.preview
        return look

    def breed_now(self, state: PetState) -> Breed:
        """La raza que lleva, o la que se está mirando antes de comprarla."""
        if isinstance(self.preview, Breed):
            return self.preview
        return breed_for(state, self.owned)

    def portrait(self) -> dict[str, Any] | None:
        """El retrato que toca enseñar en el modo actual, o `None` si ninguno."""
        state = self.current
        if state is None:
            return None
        if self.mode is Mode.ALBUM:
            if not self.album:
                return None
            photo = self.album[self.page]
            return {**photo.state, "caption": photo.caption}
        return self.cog.portrait(
            state,
            self.look_now(state),
            self.breed_now(state),
            cared=self.cared,
            owner=self.owner,
        )

    async def paint(self) -> None:
        """Pinta el retrato si ha cambiado (si no, se queda el que había)."""
        state = self.portrait()
        if state is None:
            self.image, self.image_key = None, None
            return
        key = portrait_key(state)
        if key == self.image_key:
            return
        png = await self.cog.paint(state)
        self.image, self.image_key = png, (key if png is not None else None)

    def edit_kwargs(self) -> dict[str, Any]:
        """Para cambiar el mensaje del panel: el retrato solo se sube si ha cambiado."""
        kwargs: dict[str, Any] = {
            "view": self,
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if self.image_key != self.sent_key:
            kwargs["attachments"] = self.files()
            self.sent_key = self.image_key
        return kwargs

    def send_kwargs(self) -> dict[str, Any]:
        """Para mandar el panel en un mensaje nuevo."""
        self.sent_key = self.image_key
        kwargs: dict[str, Any] = {
            "view": self,
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if files := self.files():
            kwargs["files"] = files
        return kwargs

    def files(self) -> list[discord.File]:
        """El retrato como adjunto (vacío si no hay)."""
        if self.image is None:
            return []
        return [discord.File(io.BytesIO(self.image), filename=PORTRAIT)]

    # -- Montaje ----------------------------------------------------------------------

    def rebuild(self) -> None:
        self.clear_items()
        state = self.current
        if state is None:
            self._empty()
            return
        if self.mode is Mode.WARDROBE:
            self._wardrobe(state)
        elif self.mode is Mode.BREED:
            self._breeds(state)
        elif self.mode is Mode.ALBUM:
            self._album(state)
        else:
            self._home(state)

    def _gallery(self, container: ui.Container) -> None:
        if self.image is not None:
            container.add_item(
                ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{PORTRAIT}"))
            )

    def _empty(self) -> None:
        container = ui.Container(accent_colour=COLOR)
        hints = "\n".join(f"- {s.emoji} {s.name}: {s.spawn.hint}" for s in SPAWNING if s.spawn)
        container.add_item(
            ui.TextDisplay(
                f"# 🐾 Mascotas de {_name(self.member)}\nTodavía no hay nadie en casa. Adopta "
                "en la `tienda` (pestaña 🐾 Mascotas) o espera a que alguna aparezca sola:\n"
                + hints
            )
        )
        self.add_item(container)

    def _home(self, state: PetState) -> None:
        now = self.cog.clock()
        container = ui.Container(accent_colour=COLOR)
        title = f"# 🐾 Mascotas de {_name(self.member)}"
        species = SPECIES_BY_KEY[state.species]
        streak = (
            f"🔥 Racha cuidando: {self.owner.streak} días (mejor: {self.owner.best_streak})"
            if self.owner.streak
            else "🔥 Sin racha: cuida a una mascota hoy para empezarla."
        )
        container.add_item(ui.TextDisplay(f"{title}\n-# {streak}"))
        self._gallery(container)
        active = state.id == self.owner.active_id
        container.add_item(
            ui.TextDisplay(
                pet_card(
                    state,
                    species,
                    active=active,
                    now=now,
                    note=self.note,
                    breed=breed_for(state, self.owned),
                    look=self.looks.get(state.id),
                    compact=self.image is not None,
                )
            )
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
                # Otra mascota es otro retrato: se pinta, así que se acepta antes.
                await ack(interaction)
                self.selected, self.note, self.cared = int(select.values[0]), None, False
                await self.refresh(interaction)

            select.callback = pick  # type: ignore[method-assign]
            row: ui.ActionRow = ui.ActionRow()
            row.add_item(select)
            self.add_item(row)

        if not self.mine:
            return
        care_row: ui.ActionRow = ui.ActionRow()
        for care, emoji, label in _CARE_BUTTONS:
            care_row.add_item(
                _button(label, self._care(care), custom_id=f"mascota:cuidar:{care}", emoji=emoji)
            )
        care_row.add_item(_button("Visitar", self._visit, custom_id="mascota:visitar", emoji="🏠"))
        self.add_item(care_row)
        style_row: ui.ActionRow = ui.ActionRow()
        style_row.add_item(
            _button("Vestir", self._goto(Mode.WARDROBE), custom_id="mascota:vestir", emoji="👗")
        )
        style_row.add_item(
            _button(
                "Raza",
                self._goto(Mode.BREED),
                custom_id="mascota:raza",
                emoji="🧬",
                disabled=len(look_of(state.species).breeds) < 2,
            )
        )
        style_row.add_item(
            _button("Álbum", self._goto(Mode.ALBUM), custom_id="mascota:album", emoji="📔")
        )
        style_row.add_item(
            _button(
                "Enseñar",
                self._show,
                custom_id="mascota:ensenar",
                emoji="📸",
                disabled=self.image is None,
            )
        )
        style_row.add_item(
            _button("Ponerle nombre", self._rename, custom_id="mascota:nombre", emoji="✏️")
        )
        self.add_item(style_row)
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
            _button("Adoptar", self._shop(None, "mascota"), custom_id="mascota:adoptar", emoji="🐾")
        )
        extra.add_item(
            _button(
                "Comida", self._shop(FOOD_AISLE, "todo"), custom_id="mascota:comida", emoji="🥣"
            )
        )
        self.add_item(extra)

    def _header(self, state: PetState, what: str) -> str:
        species = SPECIES_BY_KEY[state.species]
        name = discord.utils.escape_markdown(pet_name(state, species))
        return f"# {what} de {species.emoji} {name}"

    def _checkout_rows(self, label: str) -> list[ui.ActionRow]:
        """La caja de lo que se está mirando: el desglose y comprar o cancelar."""
        row: ui.ActionRow = ui.ActionRow()
        checkout = self.checkout
        price = checkout.price
        missing = price.total - checkout.balance
        row.add_item(
            _button(
                f"💳 {label} · {format_amount(price.total)}",
                self._pay,
                custom_id="mascota:pagar",
                style=discord.ButtonStyle.success,
                disabled=checkout.failed or missing > 0,
            )
        )
        row.add_item(_button("Cancelar", self._cancel, custom_id="mascota:cancelar"))
        return [row]

    def _checkout_text(self) -> str:
        checkout = self.checkout
        price = checkout.price
        lines = [tienda.ticket_preview(price), tienda.igic_line(price)]
        missing = price.total - checkout.balance
        if checkout.failed:
            lines.append(checkout.result or "")
        elif missing > 0:
            lines.append(
                f"😬 Tienes {format_amount(checkout.balance)}: te faltan "
                f"**{format_amount(missing)}**."
            )
        else:
            lines.append(
                f"{CURRENCY_EMOJI} Tienes {format_amount(checkout.balance)} → te quedan "
                f"**{format_amount(checkout.balance - price.total)}**"
            )
        return "\n".join(lines)

    def _back_row(self) -> ui.ActionRow:
        row: ui.ActionRow = ui.ActionRow()
        row.add_item(
            _button("Volver", self._goto(Mode.HOME), custom_id="mascota:volver", emoji="⬅️")
        )
        return row

    def _wardrobe(self, state: PetState) -> None:
        container = ui.Container(accent_colour=COLOR)
        container.add_item(ui.TextDisplay(self._header(state, "👗 Armario")))
        self._gallery(container)
        look = self.looks.get(state.id, {})
        slots = [s for s in SLOTS if s is Slot.BACKDROP or s.value in slots_of(state.species)]
        if isinstance(self.preview, Outfit) and self.checkout is not None:
            outfit = self.preview
            season = f" · 🗓️ de {outfit.season.name}" if outfit.season else ""
            container.add_item(
                ui.TextDisplay(
                    f"### {outfit.label}{season}\n{outfit.description}\n" + self._checkout_text()
                )
            )
            self.add_item(container)
            for row in self._checkout_rows("Comprar y poner"):
                self.add_item(row)
            return
        lines = [f"{slot.label}: {look[slot].label if slot in look else 'nada'}" for slot in slots]
        missing = [s.label for s in SLOTS if s not in slots]
        hint = (
            f"\n-# {', '.join(missing)}: esta especie no lo tiene." if missing else ""
        ) + "\n-# Lo que no tienes sale con 🛒: elígelo para verlo puesto antes de comprarlo."
        if self.note:
            hint += f"\n\n{self.note}"
        container.add_item(ui.TextDisplay("\n".join(lines) + hint))
        self.add_item(container)
        when = datetime.fromtimestamp(self.cog.clock(), TIMEZONE)
        for slot in slots:
            options = [discord.SelectOption(label="Nada", value=NOTHING, emoji="🚫")]
            mine = [o for o in in_slot(slot) if o.catalog_key in self.owned]
            shop = [
                o
                for o in in_slot(slot)
                if o.for_sale and o.catalog_key not in self.owned and in_season(o, when)
            ]
            for outfit in mine:
                options.append(
                    discord.SelectOption(
                        label=outfit.name[:100],
                        value=outfit.key,
                        emoji=_select_emoji(outfit.emoji),
                        description="✅ Es tuya",
                        default=look.get(slot) is outfit,
                    )
                )
            for outfit in shop[: SELECT_LIMIT - len(options)]:
                options.append(
                    discord.SelectOption(
                        label=outfit.name[:100],
                        value=BUY + outfit.key,
                        emoji=_select_emoji(outfit.emoji),
                        description=f"🛒 {format_amount(outfit.price)} + IGIC"[:100],
                    )
                )
            select: ui.Select = ui.Select(
                placeholder=f"{slot.label}: elige",
                options=options[:SELECT_LIMIT],
                custom_id=f"mascota:ropa:{slot.value}",
            )
            select.callback = self._dress(slot, select)  # type: ignore[method-assign]
            row: ui.ActionRow = ui.ActionRow()
            row.add_item(select)
            self.add_item(row)
        back = self._back_row()
        back.add_item(
            _button(
                "Toda la ropa",
                self._shop(None, "mascota"),
                custom_id="mascota:tienda_ropa",
                emoji="🛒",
            )
        )
        self.add_item(back)

    def _breeds(self, state: PetState) -> None:
        container = ui.Container(accent_colour=COLOR)
        container.add_item(ui.TextDisplay(self._header(state, "🧬 Raza")))
        self._gallery(container)
        look = look_of(state.species)
        current = breed_for(state, self.owned)
        if isinstance(self.preview, Breed) and self.checkout is not None:
            container.add_item(ui.TextDisplay(f"### {self.preview.name}\n" + self._checkout_text()))
            self.add_item(container)
            for row in self._checkout_rows("Comprar y poner"):
                self.add_item(row)
            return
        text = (
            f"Ahora: **{current.name}**.\n-# Cada raza se compra una vez y se pone y se quita "
            "cuando quieras. Elige una 🛒 para verla antes de comprarla."
        )
        if self.note:
            text += f"\n\n{self.note}"
        container.add_item(ui.TextDisplay(text))
        self.add_item(container)
        options = []
        for breed in look.breeds[:SELECT_LIMIT]:
            mine = breed is look.default or (
                breed_catalog_key(state.species, breed.key) in self.owned
            )
            options.append(
                discord.SelectOption(
                    label=breed.name[:100],
                    value=breed.key if mine else BUY + breed.key,
                    description=(
                        ("✅ De serie" if breed is look.default else "✅ Es tuya")
                        if mine
                        else f"🛒 {format_amount(breed.price)} + IGIC"
                    ),
                    default=breed is current,
                )
            )
        select: ui.Select = ui.Select(
            placeholder="🧬 Elige raza", options=options, custom_id="mascota:razas"
        )

        async def picked(interaction: discord.Interaction) -> None:
            await self.cog.choose_breed(interaction, self, select.values[0])

        select.callback = picked  # type: ignore[method-assign]
        row: ui.ActionRow = ui.ActionRow()
        row.add_item(select)
        self.add_item(row)
        self.add_item(self._back_row())

    def _album(self, state: PetState) -> None:
        container = ui.Container(accent_colour=COLOR)
        count = len(self.album)
        title = self._header(state, "📔 Álbum")
        if count:
            title += f"\n-# Foto {self.page + 1} de {count}"
        container.add_item(ui.TextDisplay(title))
        self._gallery(container)
        if not count:
            container.add_item(
                ui.TextDisplay(
                    "Aún no hay fotos. Se hacen solas al subir el vínculo a nivel 3, 6, 9 y "
                    "10, en su aniversario en casa, al estrenar ropa y de visita; y cada vez "
                    "que la enseñas con 📸."
                )
            )
        elif self.image is None:
            container.add_item(ui.TextDisplay(f"*{self.album[self.page].caption}*"))
        self.add_item(container)
        row: ui.ActionRow = ui.ActionRow()
        row.add_item(
            _button("Volver", self._goto(Mode.HOME), custom_id="mascota:volver", emoji="⬅️")
        )
        row.add_item(
            _button(
                "Anterior",
                self._turn(-1),
                custom_id="mascota:album:antes",
                emoji="◀️",
                disabled=self.page <= 0,
            )
        )
        row.add_item(
            _button(
                "Siguiente",
                self._turn(1),
                custom_id="mascota:album:despues",
                emoji="▶️",
                disabled=self.page >= count - 1,
            )
        )
        self.add_item(row)

    # -- Botones ----------------------------------------------------------------------

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

    def _goto(self, mode: Mode) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            # Cambiar de modo lee la mochila o el álbum y pinta: se acepta antes.
            await ack(interaction)
            self.mode, self.page, self.note = mode, 0, None
            self.preview, self.checkout = None, None
            if mode is Mode.ALBUM:
                # Se abre en la foto más nueva.
                self.page = 10**6
            if mode is not Mode.HOME:
                await tienda_claim(self.cog.bot, self.guild.id, self.member.id)
            await self.refresh(interaction)

        return callback

    def _turn(self, step: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await ack(interaction)
            self.page = max(0, self.page + step)
            await self.refresh(interaction)

        return callback

    def _dress(
        self, slot: Slot, select: ui.Select
    ) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await self.cog.dress(interaction, self, slot, select.values[0])

        return callback

    def _shop(
        self, aisle: str | None, tab: str
    ) -> Callable[[discord.Interaction], Awaitable[None]]:
        """Atajo a la tienda en privado, en el pasillo y la pestaña de las mascotas."""

        async def callback(interaction: discord.Interaction) -> None:
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

    async def _show(self, interaction: discord.Interaction) -> None:
        await self.cog.show(interaction, self)

    async def _visit(self, interaction: discord.Interaction) -> None:
        # Solo memoria: se contesta con el desplegable de miembros al momento.
        if self.current is None:
            await ack(interaction)
            return
        await interaction.response.send_message(
            "🏠 ¿A casa de quién la llevas? Tiene que llevar una mascota consigo.",
            view=VisitPicker(self.cog, self),
            ephemeral=True,
        )

    async def _pay(self, interaction: discord.Interaction) -> None:
        await self.cog.pay(interaction, self)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        self.preview, self.checkout = None, None
        await self.refresh(interaction)

    async def refresh(self, interaction: discord.Interaction | None = None) -> None:
        """Vuelve a leer y a pintar el panel (desde un botón o desde otra vista).

        Args:
            interaction: El clic en un botón del propio panel. Su token sirve 15
                minutos para editar el mensaje, así que se guarda: el desplegable
                de comida, que es otro mensaje, repinta el panel con el último.
        """
        await self.load()
        await self.paint()
        self.rebuild()
        try:
            if interaction is not None:
                await edit(interaction, **self.edit_kwargs())
                self.interaction = interaction
            elif self.interaction is not None:
                await self.interaction.edit_original_response(**self.edit_kwargs())
            elif self.message is not None:
                await self.message.edit(**self.edit_kwargs())
        except discord.HTTPException:
            # Si no llegó, la próxima edición vuelve a subir la imagen.
            self.sent_key = None
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


async def tienda_claim(bot: commands.Bot, guild_id: int, user_id: int) -> None:
    """Da la ropa de logros que ya tenía y le falta antes de abrir el armario. Nunca lanza."""
    if (cog := find_cog(bot, tienda.Tienda)) is None:
        return
    try:
        await cog.claim_reward_skins(guild_id, user_id)
    except Exception:
        logger.exception("No se pudo dar la ropa de logros a %s", user_id)


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


class VisitPicker(ui.View):
    """Desplegable privado para elegir a casa de quién va la mascota de visita."""

    def __init__(self, cog: Mascotas, panel: PetPanel) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.panel = panel
        self.done = False
        select: ui.UserSelect = ui.UserSelect(placeholder="🏠 Elige a quién visitas")
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
        await self.cog.visit(interaction, self.panel, self.select.values[0])


# -- Cog ------------------------------------------------------------------------------


class Mascotas(commands.Cog):
    """Panel de mascotas, retrato, armario, cuidados, apariciones y cameos."""

    def __init__(
        self,
        bot: commands.Bot,
        repository: PetRepository,
        shop: ShopRepository,
        *,
        clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
        scene: pet_scene.PetScene | None = None,
    ) -> None:
        self.bot = bot
        self.repository = repository
        self.shop = shop
        self.clock = clock
        self.rng = rng or random.Random()
        #: Dibuja los retratos (Node y, si no, Chromium). Las pruebas pasan uno de mentira.
        self.scene = scene or pet_scene.PetScene()
        #: Mascota activa de cada `(servidor, miembro)`. Se rellena al cargar y se
        #: actualiza al cuidar, renombrar o cambiar de mascota.
        self.active: dict[tuple[int, int], PetState] = {}
        #: Último cameo de cada dueño (epoch), para no repetir en jugadas seguidas.
        self.last_cameo: dict[tuple[int, int], float] = {}
        #: Última foto enseñada de cada dueño (epoch), para no llenar el canal.
        self.last_show: dict[tuple[int, int], float] = {}

    async def cog_load(self) -> None:
        """Prepara las tablas y carga las mascotas activas en memoria."""
        # Idempotente: el bot ya lo hace en `setup_hook`, pero así el cog se
        # puede cargar solo. Las mascotas leen la mochila de la tienda.
        await self.shop.initialize()
        await self.repository.initialize()
        for state in await self.repository.active_pets():
            self.active[(state.guild_id, state.user_id)] = state

    async def cog_unload(self) -> None:
        """Cierra el pintor de los retratos."""
        await self.scene.close()

    # -- Retratos --------------------------------------------------------------------

    def portrait(
        self,
        state: PetState,
        look: dict[Slot, Outfit],
        breed: Breed,
        *,
        cared: bool = False,
        caption: str | None = None,
        guest: tuple[PetState, dict[Slot, Outfit], Breed] | None = None,
        owner: Owner | None = None,
    ) -> dict[str, Any]:
        """El estado del retrato de una mascota (y, de visita, el de la que visita).

        Args:
            look: Lo que lleva puesto (el fondo incluido).
            breed: La raza que se ve.
            cared: Si se acaba de cuidar: sale contenta.
            caption: Pie de foto del álbum.
            guest: La mascota que viene de visita: sale al lado, con su ropa y su raza.
            owner: Su dueño (la racha y la que lleva consigo); sin él, la de la caché.
        """
        now = self.clock()
        species = SPECIES_BY_KEY[state.species]
        party = anniversary_years(state.adopted_at, now) > 0
        pets = [pet_entry(state.species, breed, look)]
        name = pet_name(state, species)
        if guest is not None:
            other, other_look, other_breed = guest
            other_species = SPECIES_BY_KEY[other.species]
            pets.insert(0, pet_entry(other.species, other_breed, other_look))
            text = visit_card(
                (other, other_species, pet_name(other, other_species)), (state, species, name)
            )
        else:
            active = self.active.get((state.guild_id, state.user_id))
            text = card(
                state,
                species,
                breed,
                look,
                name=name,
                xp=focus_text(species, state.level),
                active=(owner.active_id if owner else (active.id if active else None)) == state.id,
                streak=owner.streak if owner else 0,
                best_streak=owner.best_streak if owner else 0,
            )
        return portrait_state(
            pets,
            backdrop=look.get(Slot.BACKDROP),
            mood=portrait_mood(state, now, party=party, cared=cared or guest is not None),
            card=text,
            caption=caption,
            cake=party,
            seed=state.id,
        )

    async def owned_keys(self, guild_id: int, user_id: int) -> frozenset[str]:
        """Claves de serie de la mochila de un miembro (su ropa y sus razas)."""
        entries = await self.shop.inventory(guild_id, user_id, self.clock())
        return frozenset(e.catalog_key for e in entries if e.catalog_key)

    async def paint(self, state: dict[str, Any]) -> bytes | None:
        """PNG de un retrato; `None` si no se puede (y el panel sale en texto). Nunca lanza."""
        try:
            return await self.scene.still(state)
        except Exception:
            logger.exception("No se pudo pintar el retrato de una mascota")
            return None

    async def dressing(
        self, guild_id: int, user_id: int, state: PetState
    ) -> tuple[dict[Slot, Outfit], Breed, frozenset[str]]:
        """Lo que lleva puesto, su raza y la mochila de su dueño (lee la base de datos)."""
        owned = await self.owned_keys(guild_id, user_id)
        worn = await self.repository.outfits([state.id])
        return wearing(worn.get(state.id, {}), owned), breed_for(state, owned), owned

    async def photo(
        self,
        state: PetState,
        reason: Photo,
        *,
        level: int = 0,
        years: int = 0,
        guest: tuple[PetState, dict[Slot, Outfit], Breed] | None = None,
    ) -> None:
        """Hace una foto para el álbum de una mascota. Nunca lanza."""
        try:
            look, breed, _owned = await self.dressing(state.guild_id, state.user_id, state)
            now = self.clock()
            owner = await self.repository.owner(state.guild_id, state.user_id)
            portrait = self.portrait(state, look, breed, cared=True, guest=guest, owner=owner)
            caption = photo_caption(reason, now, level=level, years=years)
            await self.repository.add_photo(
                state, taken_at=now, reason=reason.value, caption=caption, portrait=portrait
            )
        except Exception:
            logger.exception("No se pudo guardar una foto del álbum de %s", state.user_id)

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

        En el aniversario de la mascota activa en casa, la primera vez que sale ese
        año lo celebra (sale siempre) y se hace una foto para su álbum.

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
            here = moment or Moment()
            if party := await self._anniversary(state, now):
                here = Moment(Event.PET_BIRTHDAY)
            last = self.last_cameo.get(key)
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
                template = (
                    # El aniversario se dice siempre con su frase, no con una de buen humor.
                    self.rng.choice(
                        species.lines.get(Event.PET_BIRTHDAY.value)
                        or GENERIC_LINES[Event.PET_BIRTHDAY]
                    )
                    if party
                    else pick_line(species.lines, here, self.rng)
                )
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
            if party:
                logros.note(self.bot, guild_id, user_id, pet_anniversary_stats(years=party))
        return "\n".join(lines) or None

    async def _anniversary(self, state: PetState, now: float) -> int:
        """Si hoy es su aniversario y aún no se ha celebrado este año, lo celebra.

        Devuelve los años que cumple (0 si nada). Solo toca la base de datos ese día.
        """
        years = anniversary_years(state.adopted_at, now)
        year = datetime.fromtimestamp(now, TIMEZONE).year
        if not years or state.celebrated >= year:
            return 0
        state.celebrated = year
        await self.repository.save(state)
        await self.photo(state, Photo.ANNIVERSARY, years=years)
        return years

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

    def _remember(self, state: PetState) -> None:
        """Si es la mascota activa, la caché de cameos pasa a tener este estado."""
        key = (state.guild_id, state.user_id)
        if key in self.active and self.active[key].id == state.id:
            self.active[key] = state

    # -- Panel -----------------------------------------------------------------------

    async def panel(
        self, guild: discord.Guild, member: discord.abc.User, viewer: discord.abc.User
    ) -> PetPanel:
        """Panel de mascotas de `member` visto por `viewer`, listo para enviar."""
        view = PetPanel(self, guild, member, viewer)
        await view.load()
        await view.paint()
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
        """Acaricia, juega, da de comer o saca de paseo a la mascota del panel.

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
            wait = f"<t:{int(now + outcome.wait)}:R>" if outcome.wait > 3600 else f"{minutes} min"
            await notify(
                interaction,
                f"⏳ {species.emoji} Dale un respiro: podrás volver dentro de {wait}.",
            )
            return

        streak, first_today = await self._save_care(state, now)
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
        lines += self._outcome_lines(outcome)
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
        panel.cared = True
        if food is None:
            await panel.refresh(interaction)
        else:
            # Viene del desplegable privado de comida: se cierra ese y se repinta
            # el panel público, que es otro mensaje.
            await edit(interaction, content=lines[0], view=None)
            await panel.refresh()

        if outcome.level_up in ALBUM_LEVELS:
            await self.photo(state, Photo.LEVEL, level=outcome.level_up or 0)
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

    async def _save_care(self, state: PetState, now: float) -> tuple[int, bool]:
        """Guarda un cuidado y la racha del dueño: `(racha, si es el primero del día)`."""
        owner = await self.repository.owner(state.guild_id, state.user_id)
        today = care_day(now)
        first_today = owner.last_day != today
        streak = streak_after(owner.last_day, owner.streak, today)
        await self.repository.save_care(
            state, streak=streak, best_streak=max(streak, owner.best_streak), day=today
        )
        self._remember(state)
        return streak, first_today

    @staticmethod
    def _outcome_lines(outcome: Any) -> list[str]:
        lines = []
        if outcome.points:
            lines.append(f"-# 💞 +{outcome.points} de vínculo.")
        else:
            lines.append("-# 💞 Hoy ya ha tenido bastante de esto: no suma más vínculo.")
        if outcome.level_up is not None:
            lines.append(f"🎉 **¡Vínculo a nivel {outcome.level_up}!**")
            if outcome.new_trick:
                lines.append("🎪 ¡Ha aprendido un truco nuevo!")
            if outcome.level_up in ALBUM_LEVELS:
                lines.append("📔 Foto nueva en su álbum.")
        return lines

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
            and not is_outfit(item.catalog_key)  # la ropa se compra o se gana
            and not is_breed(item.catalog_key)
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
            and not is_outfit(e.catalog_key)
            and not is_breed(e.catalog_key)
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
        self._remember(current)
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

    # -- Armario y razas -------------------------------------------------------------

    async def dress(
        self, interaction: discord.Interaction, panel: PetPanel, slot: Slot, value: str
    ) -> None:
        """Pone, quita o enseña antes de comprar una prenda en un hueco."""
        await ack(interaction)
        await panel.load()
        state = panel.current
        if state is None:
            await notify(interaction, "Esa mascota ya no está.")
            return
        if value.startswith(BUY):
            outfit = next((o for o in OUTFITS if o.key == value.removeprefix(BUY)), None)
            if outfit is None or outfit.slot is not slot:
                return
            await self._open_checkout(interaction, panel, outfit, outfit.catalog_key)
            return
        outfit = None if value == NOTHING else next((o for o in OUTFITS if o.key == value), None)
        if outfit is not None and (
            outfit.catalog_key not in panel.owned or outfit.slot is not slot
        ):
            await notify(interaction, "Esa prenda ya no está en tu mochila.")
            await panel.refresh(interaction)
            return
        await self._wear(interaction, panel, state, slot, outfit)

    async def _wear(
        self,
        interaction: discord.Interaction,
        panel: PetPanel,
        state: PetState,
        slot: Slot,
        outfit: Outfit | None,
        *,
        prefix: str = "",
    ) -> None:
        before = dict(panel.looks.get(state.id, {}))
        await self.repository.wear(state, slot.value, outfit.catalog_key if outfit else None)
        species = SPECIES_BY_KEY[state.species]
        name = discord.utils.escape_markdown(pet_name(state, species))
        if outfit is None:
            panel.note = f"{slot.label}: {name} ya no lleva nada."
        else:
            panel.note = f"{outfit.label}: {name} lo luce como si fuera suyo."
        panel.note = f"{prefix}{panel.note}"
        await panel.refresh(interaction)
        look = panel.looks.get(state.id, {})
        clothes = [s for s in before if s is not Slot.BACKDROP]
        if outfit is not None and outfit.slot is not Slot.BACKDROP and not clothes:
            photos = await self.repository.photos(state.id)
            if not any(p.reason == Photo.DRESSED.value for p in photos):
                await self.photo(state, Photo.DRESSED)
        await logros.track(
            self.bot,
            panel.guild.id,
            interaction.user,
            interaction.channel,
            pet_dress_stats(
                species=state.species,
                outfit=outfit.key if outfit else None,
                owned=sum(1 for key in panel.owned if outfit_of(key) is not None),
                complete=complete(look),
                look={s.value: o.key for s, o in look.items()},
                aisles={s.value: o.aisle for s, o in look.items()},
            ),
        )

    async def choose_breed(
        self, interaction: discord.Interaction, panel: PetPanel, value: str
    ) -> None:
        """Pone una raza que ya tiene o la enseña antes de comprarla."""
        await ack(interaction)
        await panel.load()
        state = panel.current
        if state is None:
            await notify(interaction, "Esa mascota ya no está.")
            return
        look = look_of(state.species)
        breed = look.breed(value.removeprefix(BUY))
        if value.startswith(BUY) and breed is not look.default:
            await self._open_checkout(
                interaction, panel, breed, breed_catalog_key(state.species, breed.key)
            )
            return
        if breed is not look.default and (
            breed_catalog_key(state.species, breed.key) not in panel.owned
        ):
            await notify(interaction, "Esa raza ya no es tuya.")
            await panel.refresh(interaction)
            return
        await self._set_breed(interaction, panel, state, breed, bought=False)

    async def _set_breed(
        self,
        interaction: discord.Interaction,
        panel: PetPanel,
        state: PetState,
        breed: Breed,
        *,
        bought: bool,
        prefix: str = "",
    ) -> None:
        look = look_of(state.species)
        state.breed = "" if breed is look.default else breed.key
        await self.repository.save(state)
        self._remember(state)
        species = SPECIES_BY_KEY[state.species]
        panel.note = (
            f"🧬 {discord.utils.escape_markdown(pet_name(state, species))} pasa a ser "
            f"{breed.name.lower()}. Sigue con su carácter de siempre."
        )
        panel.note = f"{prefix}{panel.note}"
        await panel.refresh(interaction)
        owned = sum(1 for key in panel.owned if is_breed(key))
        await logros.track(
            self.bot,
            panel.guild.id,
            interaction.user,
            interaction.channel,
            pet_breed_stats(bought=bought, owned=owned, species=state.species),
        )

    async def _open_checkout(
        self,
        interaction: discord.Interaction,
        panel: PetPanel,
        thing: Outfit | Breed,
        catalog_key: str,
    ) -> None:
        """Enseña puesto lo que no tiene y su caja, para comprarlo sin salir del panel."""
        checkout = await tienda.prepare_checkout(
            self.bot, interaction, catalog_key, hidden=isinstance(thing, Breed)
        )
        if checkout is None:
            await notify(interaction, "Eso no se vende ahora mismo.")
            return
        panel.preview, panel.checkout = thing, checkout
        panel.note = None
        await panel.refresh(interaction)

    async def pay(self, interaction: discord.Interaction, panel: PetPanel) -> None:
        """Cobra lo que se está mirando y se lo pone a la mascota."""
        # Se suelta antes de esperar a nada: un segundo clic no cobra dos veces.
        checkout, thing = panel.checkout, panel.preview
        panel.checkout, panel.preview = None, None
        await ack(interaction)
        if checkout is None or thing is None:
            return
        paid = await tienda.pay_checkout(checkout, interaction)
        if paid is None:
            panel.note = checkout.result or "No se ha podido comprar."
            await panel.refresh(interaction)
            return
        await panel.load()
        state = panel.current
        if state is not None:
            charged = (
                f"💳 Pagas {format_amount(checkout.price.total)} "
                f"({format_amount(checkout.price.tax)} de IGIC)."
            )
            if isinstance(thing, Outfit):
                await self._wear(
                    interaction, panel, state, thing.slot, thing, prefix=charged + "\n"
                )
            else:
                await self._set_breed(
                    interaction, panel, state, thing, bought=True, prefix=charged + "\n"
                )
        await tienda.after_checkout(checkout, interaction, paid)

    # -- Enseñar y visitas -----------------------------------------------------------

    async def show(self, interaction: discord.Interaction, panel: PetPanel) -> None:
        """Publica en el canal el retrato de la mascota que se ve (y lo guarda en el álbum)."""
        now = self.clock()
        key = (panel.guild.id, panel.member.id)
        last = self.last_show.get(key)
        if last is not None and now - last < SHOW_GAP:
            # Solo memoria: se contesta al momento.
            await interaction.response.send_message(
                f"📸 Espera un poco: la siguiente foto, <t:{int(last + SHOW_GAP)}:R>.",
                ephemeral=True,
            )
            return
        await ack(interaction)
        await panel.load()
        state = panel.current
        if state is None:
            await notify(interaction, "Esa mascota ya no está.")
            return
        self.last_show[key] = now
        look = panel.looks.get(state.id, {})
        portrait = self.portrait(
            state, look, breed_for(state, panel.owned), cared=True, owner=panel.owner
        )
        png = await self.paint(portrait)
        if png is None:
            await notify(interaction, "La cámara no funciona ahora mismo. Prueba más tarde.")
            return
        species = SPECIES_BY_KEY[state.species]
        name = discord.utils.escape_markdown(pet_name(state, species))
        text = f"📸 {interaction.user.mention}: " + show_text(self.rng, name=name)
        await notify(
            interaction,
            text,
            ephemeral=False,
            file=discord.File(io.BytesIO(png), filename=PORTRAIT),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        caption = photo_caption(Photo.SHOW, now)
        await self.repository.add_photo(
            state, taken_at=now, reason=Photo.SHOW.value, caption=caption, portrait=portrait
        )
        await logros.track(
            self.bot,
            panel.guild.id,
            interaction.user,
            interaction.channel,
            pet_show_stats(dressed=sum(1 for s in look if s is not Slot.BACKDROP), when=now),
        )

    async def visit(
        self,
        interaction: discord.Interaction,
        panel: PetPanel,
        host: discord.abc.User,
    ) -> None:
        """Lleva la mascota del panel de visita a casa de la mascota activa de `host`."""
        await ack(interaction)
        guild = panel.guild
        if host.id == panel.member.id:
            await edit(interaction, content="Esa es tu casa. Elige a otra persona.", view=None)
            return
        if host.bot:
            await edit(interaction, content="Los bots no tienen mascota.", view=None)
            return
        host_state = self.active.get((guild.id, host.id))
        if host_state is None or host_state.species not in SPECIES_BY_KEY:
            await edit(
                interaction,
                content=f"{_name(host)} no lleva ninguna mascota consigo.",
                view=None,
            )
            return
        await panel.load()
        state = panel.current
        if state is None:
            await edit(interaction, content="Esa mascota ya no está.", view=None)
            return
        now = self.clock()
        outcome = apply_care(state, Care.VISIT, now)
        species = SPECIES_BY_KEY[state.species]
        if outcome.wait:
            await edit(
                interaction,
                content=(
                    f"⏳ {species.emoji} Acaba de volver de una visita: otra "
                    f"<t:{int(now + outcome.wait)}:R>."
                ),
                view=None,
            )
            return
        streak, _first = await self._save_care(state, now)
        # La de la casa también suma, si hoy aún no ha recibido o hecho una visita.
        hosts = await self.repository.pets(guild.id, host.id)
        host_state = next((p for p in hosts if p.id == host_state.id), host_state)
        hosted = apply_care(host_state, Care.VISIT, now)
        if not hosted.wait:
            await self.repository.save(host_state)
            self._remember(host_state)
        look, breed, _owned = await self.dressing(guild.id, panel.member.id, state)
        host_look, host_breed, _ = await self.dressing(guild.id, host.id, host_state)
        guest = (state, look, breed)
        portrait = self.portrait(host_state, host_look, host_breed, guest=guest)
        png = await self.paint(portrait)
        host_species = SPECIES_BY_KEY[host_state.species]
        name = discord.utils.escape_markdown(pet_name(state, species))
        host_name = discord.utils.escape_markdown(pet_name(host_state, host_species))
        lines = [
            f"🏠 {interaction.user.mention} lleva a {species.emoji} **{name}** a casa de "
            f"{host.mention}.",
            visit_text(self.rng, name=name, host=f"{host_species.emoji} {host_name}"),
        ]
        lines += self._outcome_lines(outcome)
        if hosted.points:
            lines.append(f"-# 💞 {host_name} también suma +{hosted.points} de vínculo.")
        await edit(interaction, content="🏠 ¡De visita!", view=None)
        kwargs: dict[str, Any] = {
            "ephemeral": False,
            "allowed_mentions": discord.AllowedMentions(users=[host]),
        }
        if png is not None:
            kwargs["file"] = discord.File(io.BytesIO(png), filename=PORTRAIT)
        await notify(interaction, "\n".join(lines), **kwargs)
        panel.note, panel.cared = lines[1], True
        await panel.refresh()
        caption = photo_caption(Photo.VISIT, now)
        await self.repository.add_photo(
            state, taken_at=now, reason=Photo.VISIT.value, caption=caption, portrait=portrait
        )
        await self.repository.add_photo(
            host_state,
            taken_at=now,
            reason=Photo.VISIT.value,
            caption=caption,
            portrait=portrait,
        )
        if outcome.level_up in ALBUM_LEVELS:
            await self.photo(state, Photo.LEVEL, level=outcome.level_up or 0)
        await logros.track(
            self.bot,
            guild.id,
            interaction.user,
            interaction.channel,
            pet_care_stats(
                action=Care.VISIT.value,
                points=outcome.points,
                favourite=False,
                level=state.level,
                tricks=unlocked_tricks(state.level),
                streak=streak,
                gift=False,
                when=now,
                species=species.key,
            ),
        )
        logros.note(self.bot, guild.id, host.id, pet_hosted_stats())
        logros.note(self.bot, guild.id, panel.member.id, pet_photo_stats())

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra las mascotas del servidor que el bot abandona."""
        await self.repository.delete_guild_data(guild.id)
        for key in [k for k in self.active if k[0] == guild.id]:
            del self.active[key]

    # -- Comandos --------------------------------------------------------------------

    @app_commands.command(
        name="mascota", description="Tus mascotas: cuídalas, vístelas y elige cuál va contigo."
    )
    @app_commands.describe(miembro="De quién (por defecto, las tuyas).")
    @app_commands.guild_only()
    async def mascota(
        self, interaction: discord.Interaction, miembro: discord.Member | None = None
    ) -> None:
        """Enseña el panel de mascotas; solo su dueño puede cuidarlas."""
        assert interaction.guild is not None  # guild_only
        # Las mascotas se leen de la base de datos y el retrato se pinta: «pensando…».
        await interaction.response.defer(thinking=True)
        view = await self.panel(interaction.guild, miembro or interaction.user, interaction.user)
        kwargs = view.send_kwargs()
        if "files" in kwargs:
            kwargs["attachments"] = kwargs.pop("files")
        await edit(interaction, **kwargs)
        view.interaction = interaction

    @commands.command(name="mascota")
    @commands.guild_only()
    async def mascota_text(
        self, ctx: commands.Context, miembro: discord.Member | None = None
    ) -> None:
        """Versión de texto: `.mascota` o `.mascota @alguien`."""
        assert ctx.guild is not None  # guild_only
        view = await self.panel(ctx.guild, miembro or ctx.author, ctx.author)
        view.message = await ctx.send(**view.send_kwargs())


# -- Puntos de entrada para otros cogs ------------------------------------------------


def xp_bonus(bot: commands.Bot, guild_id: int, user_id: int, *, voice: bool) -> float:
    """Bonus de XP de la mascota activa; 1,0 si no hay o si el cog no está cargado."""
    if (cog := find_cog(bot, Mascotas)) is not None:
        return cog.xp_bonus(guild_id, user_id, voice=voice)
    return 1.0


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
