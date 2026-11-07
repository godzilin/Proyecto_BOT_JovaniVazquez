"""Tienda: `tienda` y la mochila, la caja con IGIC y la caducidad de los alquileres.

- `tienda [buscar]` abre el escaparate de El Colmado de Jovani, una lista compacta
  (las vistas viven en `bot.cogs.shop_front`): secciones, pasillos con lo que ya
  tienes de cada uno, orden, filtros y buscador. Cada artículo abre su ficha privada,
  con la cantidad, el ticket y **Pagar**; varios a la vez van al carrito. Cualquiera
  puede comprar desde el escaparate de otro: la ficha y el carrito se abren solo
  para quien pulsa.
- **Carrito** (`carts`, en memoria y con caducidad `CART_TTL`): hasta
  `MAX_CART_LINES` artículos distintos y `MAX_QUANTITY` unidades de cada uno. Se paga
  todo o nada en una sola transacción (`complete_cart`); la única excepción es un rol
  que Discord no deja dar después de cobrar, que se devuelve solo. La factura desglosa
  el impuesto por tipo.
- La caja de un solo artículo (el 🎨 de las mesas, `pieles` y los regalos de `pala`)
  enseña el ticket antes de pagar y pide confirmar. Al pagar, por cualquier camino,
  sale la factura simplificada, que solo ve el comprador, y un aviso público en el
  canal para presumir. Desde `BIG_PURCHASE` (100.000 Y$) el aviso lleva un pregón
  delante.
- **Lista de deseos** (`bot.services.shop_wishlist`): pública (se ve en el escaparate
  de su dueño y en la sección ⭐ Deseos de `perfil`), hasta `MAX_WISHES` cosas.
  Comprar algo lo quita de la lista. Cada `WISH_MINUTES` se mira si algo ha cambiado
  (rebaja, temporada, reposición, que ya te llegue el dinero) y se avisa por mensaje
  privado (`whisper`); el Rastro avisa al montar un puesto con algo deseado
  (`wish_listed`). Son los únicos mensajes privados del bot y se silencian con 🔕.
- La mochila (botón 🎒 del escaparate, o sección 🎒 Objetos de `perfil`)
  enseña lo que tiene alguien. Su dueño puede ponerse y
  quitarse los roles que compró para siempre (útil para los de color) y usar
  los objetos que se usan: tirarle un huevo a alguien, mandar un burofax,
  abrir una caja botín… (reglas en `bot.services.shop_uses`). El resultado
  sale en el canal; si es contra alguien, se le menciona. También elige qué
  título luce (o ninguno).
- **Títulos** (`CatalogEntry.title`): objetos que se lucen junto al nombre en
  `perfil`, `fortunas` y `ranking`. Comprar uno te lo pone; uno a la vez. Se
  guarda en la columna `equipped` de la mochila, como los roles, y el cog lo
  tiene en memoria (`titles`) para que esas listas no lean la base de datos.
  No salen en la caja botín ni en los regalos de las mascotas.
- **Cajas** (`bot.services.lootboxes`): cuatro por niveles y tres de temporada, que se
  abren desde la mochila con una animación de tres fotogramas sobre un mismo mensaje y
  dan piezas de colección o, muy de vez en cuando, un tapete. Las de temporada solo se
  venden en sus meses (`on_sale`). La caja botín vieja ya no se vende pero se abre.
  Una cada 100 jugadas del casino y una de regalo cada 5 niveles se ganan gratis
  (`grant_box`, que llaman `apuestas` y `message_stats`).
- Surtido de serie: la primera vez que se abre la tienda en un servidor se
  llena con los artículos de `bot.services.shop_catalog`, repartidos en
  pasillos que el escaparate deja filtrar. Desde ahí, los administradores los
  tocan con `catalogo` (cog Admin; la trastienda vive en
  `bot.cogs.shop_admin`) como cualquier otro artículo.

Dinero: cada compra pasa por `EconomyService.purchase` con su IGIC (tipo por
artículo, ver `bot.services.taxes.IGIC_RATES`), que va al Estado. Pagar llama
a `renta.remind` y la factura lleva la línea de la renta pendiente. Si el rol
comprado no se puede dar, se devuelve todo con `refund_purchase`.

Logros: categoría Tienda, con `achievements.track` al pagar (`shop_stats` por
unidad y `shop_cart_stats` por compra), al usar un objeto (`shop_use_stats`), para
quien lo recibe (`shop_hit_stats`), al apuntar deseos (`shop_wish_stats`), al recibir
sus avisos (`shop_wish_alert_stats`) y al mirar fichas o buscar (`shop_browse_stats`).
Usar un objeto no mueve dinero, así que no lleva impuestos ni gancho de la
Renta: lo que tributa es la compra.

Otros cogs: `xp_multiplier(bot, …)` da el multiplicador de XP de los
potenciadores; lo usa `bot.cogs.message_stats` al dar XP por mensajes y voz.
Lo lee de una caché en memoria, sin tocar la base de datos en cada mensaje.
`owned_keys(bot, …)` da las claves de serie de lo que tiene un miembro; lo
usa `bot.cogs.work` al fichar para aplicar las herramientas de curro.
`title_of(bot, …)` da el título que luce un miembro, de memoria; lo usan
`perfil`, `fortunas` y `ranking`.

Permisos: **Gestionar roles** y que el rol del bot esté por encima de los
roles que se venden. Sin ellos, la tienda no deja ponerlos a la venta ni
cobrarlos.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import unicodedata
from collections import defaultdict
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks

from bot.cogs import achievements as logros
from bot.cogs import banco_compras as financiar
from bot.cogs import pets as mascotas
from bot.cogs import renta
from bot.repositories.shop import InventoryEntry, ShopRepository
from bot.services import credit, pet_wardrobe
from bot.services.achievements import (
    StatDelta,
    lootbox_free_stats,
    lootbox_stats,
    shop_browse_stats,
    shop_cart_stats,
    shop_hit_stats,
    shop_skin_stats,
    shop_stats,
    shop_title_stats,
    shop_use_stats,
    shop_wish_alert_stats,
    shop_wish_stats,
)
from bot.services.economy import (
    CURRENCY_EMOJI,
    EconomyService,
    InsufficientFundsError,
    format_amount,
)
from bot.services.housing_furniture import FURNITURE_KEYS
from bot.services.levels import TIMEZONE, calculate_level_progress
from bot.services.lootboxes import (
    BOX_BY_KEY,
    COLLECTION_BY_KEY,
    FRAME_SECONDS,
    Rarity,
    box_of,
    free_box_line,
    glow_frame,
    is_piece,
    reveal_frame,
    roll,
    shake_frame,
)
from bot.services.lootboxes import in_season as box_in_season
from bot.services.pet_wardrobe import Outfit, is_outfit, outfit_of
from bot.services.pets import Event, Moment
from bot.services.pets_catalog import SPECIES_BY_KEY, species_of
from bot.services.pets_looks import breed_of_item, is_breed
from bot.services.shop import (
    DANGEROUS_PERMISSIONS,
    MAX_CART_LINES,
    MAX_QUANTITY,
    Kind,
    Quote,
    Sale,
    ShopError,
    ShopItem,
    TicketLine,
    active_multiplier,
    cart_receipt,
    emblematic,
    format_multiplier,
    format_span,
    ineligibility,
    max_quantity,
    money,
    quote,
    rate_label,
    receipt,
)
from bot.services.shop_catalog import (
    CATALOG,
    RETIRED_ITEMS,
    REVISED_PRICES,
    REVISED_TAXES,
    TITLE_KEYS,
    CatalogEntry,
    aisle_of,
    food_of,
    is_skin,
    is_title,
    use_of,
)
from bot.services.shop_uses import (
    MAX_NICK,
    Target,
    Use,
    UseResult,
    clean_shout,
    megaphone_text,
    mystery_text,
    nickname_text,
    pick_mystery,
    resolve,
)
from bot.services.shop_wishlist import (
    MAX_WISHES,
    news_line,
    news_message,
    stall_message,
    stored_seen,
    wish_news,
    wish_state,
)
from bot.services.skin_images import SkinImage
from bot.services.skins import (
    GAMES,
    HIELO,
    SKIN_BY_KEY,
    Skin,
    fits,
    free_skins,
    in_season,
    rewards_for,
    skin_of_item,
    wearable_games,
)
from bot.services.skins import params as skin_params_of
from bot.services.skins import skins_of as game_skins
from bot.services.taxes import TAX_COLLECTOR
from bot.services.telemetry import TimedLock
from bot.services.work_tools import TOOL_BY_KEY
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit, notify

if TYPE_CHECKING:
    import sqlite3

    from bot.app import BotClient
    from bot.cogs.shop_front import Prefs, Storefront
    from bot.repositories.message_stats import MessageStatsRepository
    from bot.repositories.rastro import Listing

logger = logging.getLogger(__name__)

#: Dorado de colmado para el escaparate; verde y rojo para la caja.
COLOR = discord.Color.from_rgb(255, 176, 59)
COLOR_PAID = discord.Color.from_rgb(87, 242, 135)
COLOR_ERROR = discord.Color.from_rgb(237, 66, 69)
VIEW_TIMEOUT = 600
#: Cada cuánto se quitan los roles alquilados que han vencido.
EXPIRY_MINUTES = 5
#: Cada cuánto se mira si algo de las listas de deseos merece un aviso por privado.
WISH_MINUTES = 5
#: Un carrito que nadie toca en un día se olvida (vive en memoria).
CART_TTL = 86400

ANNOUNCE_LINES: dict[Kind, tuple[str, ...]] = {
    Kind.ROLE: (
        "{who} estrena {item}.",
        "{who} se ha comprado {item}. Ahora sí que brilla.",
        "{who} sale del colmado con {item} puesto. Mírala, mírala.",
        "{who} luce {item}. Yo soy yo, y así me tienes que soportar.",
        "{who} estrena {item} y ya no saluda igual.",
        "{who} se pone {item} y sale a pasearlo.",
    ),
    Kind.BOOST: (
        "{who} se mete un {item} en vena. A yapear se ha dicho.",
        "{who} pilla {item}: ojo, que viene con turbo.",
        "{who} compra {item}. El XP no se va a ganar solo.",
        "{who} se toma {item}. Más chute que un decreto ley.",
        "{who} activa {item}. Hazme café… ¡quiero café!",
    ),
    Kind.TROPHY: (
        "{who} se lleva {item} pa' la casa.",
        "{who} compra {item}. Pa' presumir, que pa' eso está.",
        "{who} añade {item} a la vitrina. Qué nivel.",
        "{who} pone {item} en la vitrina, al lado del Nobel de la Paz de Obama.",
        "{who} se lleva {item}. ¿Qué? ¿Qué? ¡Toma mangó!",
    ),
    Kind.PET: (
        "{who} sale del colmado con {item} en brazos. Familia nueva.",
        "{who} adopta {item}. Ya tiene a quien contarle sus pérdidas.",
        "{who} se lleva {item} a casa. Que alguien avise a la protectora: ha ido bien.",
        "{who} adopta {item}. Lagartijo, iguana, lagarto: la familia crece.",
        "{who} adopta {item}, el primero de la familia que no le pide un Bizum.",
    ),
}
#: Aviso de compra de un objeto que se usa (un huevo, un burofax…): suena a amenaza.
ANNOUNCE_USABLE: tuple[str, ...] = (
    "{who} sale del colmado con {item} en la mano. Alguien va a cobrar. 👀",
    "{who} se compra {item}. Yo que tú no le daba la espalda.",
    "{who} pilla {item}. Esto no va a acabar bien.",
)

#: Aviso de compra de un título: se estrena puesto.
ANNOUNCE_TITLE: tuple[str, ...] = (
    "Desde hoy, {who} luce {item}. Tratamiento de usted, por favor.",
    "{who} se compra {item} y ya no saluda igual.",
    "{who} estrena {item}. Que conste en el BOE.",
)
#: Aviso de una compra de varias cosas a la vez (un carrito).
ANNOUNCE_CART: tuple[str, ...] = (
    "{who} pasa por caja con {count} cosas: {items}.",
    "{who} sale del colmado con el carro lleno: {items}.",
    "{who} se lleva {items}. Jovani le ayuda a cargar el maletero.",
    "{who} vacía medio colmado: {items}.",
)
#: Desde cuánto (IGIC incluido) una compra se anuncia a bombo y platillo.
BIG_PURCHASE = 100_000
#: Aviso de una compra de las gordas, delante del aviso normal.
ANNOUNCE_BIG: tuple[str, ...] = (
    "📣 ¡Atención, servidor! Ha entrado dinero de verdad en el colmado.",
    "📣 Se paran las máquinas del casino: alguien acaba de tirar la casa por la ventana.",
    "📣 Jovani cierra la caja y sale a la puerta a mirar: compra gorda.",
)

#: Pestañas del escaparate: clave, etiqueta y si un artículo entra en ella.
TABS: tuple[tuple[str, str], ...] = (
    ("todo", "🛍️ Todo"),
    ("rol", "🎭 Roles"),
    ("xp", "⚡ XP"),
    ("mascota", "🐾 Mascotas"),
    ("objeto", "💎 Vitrina"),
    ("uso", "🫳 Para usar"),
    ("skin", "🎨 Skins"),
)
#: Valor del desplegable de títulos para quitárselo.
NO_TITLE = "-"


def _name(user: discord.abc.User) -> str:
    return discord.utils.escape_markdown(user.display_name)


def _button(
    label: str,
    callback: Callable[[discord.Interaction], Awaitable[None]],
    *,
    custom_id: str,
    style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    disabled: bool = False,
) -> ui.Button:
    """Botón con su callback ya enganchado y su `custom_id` fijo."""
    button: ui.Button = ui.Button(label=label, style=style, disabled=disabled, custom_id=custom_id)
    button.callback = callback  # type: ignore[method-assign]
    return button


# -- Textos ---------------------------------------------------------------------------


def local_now(epoch: float) -> datetime:
    """La hora de Canarias en `epoch`: la que manda en las temporadas."""
    return datetime.fromtimestamp(epoch, TIMEZONE)


def giftable(item: ShopItem) -> bool:
    """Si la tienda puede regalar `item` (caja botín, sorteos de las ONGs).

    Coleccionables visibles, sin uso y de existencias ilimitadas, para no gastar unidades
    numeradas de nadie. Los títulos no tocan: se lucen porque se han pagado. Los muebles
    del vacacional tampoco: suben la renta de los pisos, y eso no se regala.
    """
    return (
        item.visible
        and item.kind is Kind.TROPHY
        and item.stock is None
        and use_of(item.catalog_key) is None
        and not is_title(item.catalog_key)
        and not is_skin(item.catalog_key)
        and not is_piece(item.catalog_key)
        and not is_outfit(item.catalog_key)
        and not is_breed(item.catalog_key)
        and item.catalog_key not in FURNITURE_KEYS
    )


def season_name(item: ShopItem) -> str | None:
    """La temporada en la que se vende un artículo, o `None` si se vende todo el año."""
    skin = skin_of_item(item.catalog_key)
    if skin is not None:
        return skin.season.name if skin.season else None
    outfit = outfit_of(item.catalog_key)
    if outfit is not None:
        return outfit.season.name if outfit.season else None
    box = box_of(item.catalog_key)
    return box.season.name if box is not None and box.season else None


def on_sale(item: ShopItem, when: datetime) -> bool:
    """Si un artículo se vende ahora: skins, ropa y cajas de temporada, solo en sus meses."""
    skin = skin_of_item(item.catalog_key)
    if skin is not None:
        return in_season(skin, when)
    outfit = outfit_of(item.catalog_key)
    if outfit is not None:
        return pet_wardrobe.in_season(outfit, when)
    box = box_of(item.catalog_key)
    return box is None or box_in_season(box, when)


def kind_detail(item: ShopItem) -> str:
    """Qué es el artículo, en pocas palabras."""
    if item.kind is Kind.ROLE:
        span = "para siempre" if item.duration is None else f"durante {format_span(item.duration)}"
        return f"Rol <@&{item.role_id}> {span}"
    if item.kind is Kind.BOOST:
        return (
            f"XP {format_multiplier(item.multiplier or 100)} durante {format_span(item.duration)}"
        )
    if item.kind is Kind.PET:
        species = species_of(item.catalog_key)
        return f"🐾 Mascota {species.rarity.lower()}" if species else "🐾 Mascota"
    if (use := use_of(item.catalog_key)) is not None:
        return use.summary
    if (tool := TOOL_BY_KEY.get(item.catalog_key or "")) is not None:
        return f"🛠️ Herramienta de `pala`: {tool.effect}"
    if is_title(item.catalog_key):
        return "🎖️ Título: se luce junto a tu nombre"
    if (skin := skin_of_item(item.catalog_key)) is not None:
        game = GAMES[skin.game]
        season = f" · 🗓️ de {skin.season.name}" if skin.season else ""
        return f"🎨 Skin de {game.label}: cambia el {game.thing}{season}"
    if (outfit := outfit_of(item.catalog_key)) is not None:
        season = f" · 🗓️ de {outfit.season.name}" if outfit.season else ""
        return f"👗 Ropa de mascota ({outfit.slot.label.lower()}): se pone en `mascota`{season}"
    if (bred := breed_of_item(item.catalog_key)) is not None:
        species = SPECIES_BY_KEY.get(bred[0])
        return f"🧬 Raza de {species.name.lower() if species else 'mascota'}: se pone en `mascota`"
    return "Coleccionable"


def in_tab(item: ShopItem, tab: str) -> bool:
    """Si un artículo sale en una pestaña del escaparate."""
    usable = use_of(item.catalog_key) is not None
    skin = is_skin(item.catalog_key)
    clothes = is_outfit(item.catalog_key)
    if tab == "uso":
        return usable
    if tab == "skin":
        return skin
    if tab == "objeto":
        return item.kind is Kind.TROPHY and not usable and not skin and not clothes
    if tab == "mascota":
        # La ropa de las mascotas sale con ellas: es donde la busca quien tiene una.
        return item.kind is Kind.PET or clothes
    return tab == "todo" or item.kind.value == tab


def price_text(item: ShopItem, now: float) -> str:
    """Precio de escaparate, con la rebaja tachada si la hay."""
    price = quote(item, now)
    if not price.discount:
        return f"**{format_amount(price.base)}**"
    until = f" hasta <t:{int(item.discount_until)}:R>" if item.discount_until is not None else ""
    return (
        f"~~{format_amount(price.price)}~~ **{format_amount(price.base)}** · "
        f"🏷️ -{price.discount_pct} %{until}"
    )


def owned_tag(item: ShopItem, owned: int) -> str | None:
    """La etiqueta de lo que ya tiene quien mira el escaparate, o `None` si no tiene.

    Con el límite por persona cumplido lo dice, para no ir a la caja para nada.
    """
    if owned <= 0:
        return None
    if item.per_user is not None and owned >= item.per_user:
        return "✅ Ya lo tienes" if item.per_user == 1 else f"✅ Tienes {owned}, el máximo"
    return "✅ Lo tienes" if owned == 1 else f"✅ Tienes {owned}"


def item_card(item: ShopItem, now: float, *, best_seller: bool = False, owned: int = 0) -> str:
    """Texto de un artículo en el escaparate.

    Args:
        owned: Cuántos tiene en vigor quien mira (en su mochila), para verlo de un vistazo.
    """
    lines = [f"### {item.emoji} {item.name}", price_text(item, now)]
    if item.description:
        lines.append(item.description)
    tags = [aisle_of(item.catalog_key).label, kind_detail(item)]
    if tag := owned_tag(item, owned):
        tags.insert(0, f"**{tag}**")
    if food_of(item.catalog_key):
        tags.append("🍽️ Se come (para tu mascota)")
    if best_seller:
        tags.append("🔥 Lo más vendido")
    if item.is_new(now):
        tags.append("🆕 Nuevo")
    if item.remaining is not None:
        tags.append(
            "❌ Agotado" if item.sold_out else f"📦 Quedan {item.remaining} de {item.stock}"
        )
    if item.min_level:
        tags.append(f"🔒 Nivel {item.min_level}")
    if item.per_user is not None:
        tags.append(f"👤 Máx. {item.per_user} por persona")
    lines.append("-# " + " · ".join(tags))
    if emblematic(item):
        lines.append("-# 💳 No se financia: lo numerado y los títulos se pagan al contado.")
    elif hint := financiar.teaser(quote(item, now).total, item_category(item)):
        lines.append(f"**{hint}**")
    return "\n".join(lines)


def short_detail(item: ShopItem) -> str:
    """Qué es el artículo, para un desplegable (sin menciones, que ahí no se ven)."""
    if item.kind is Kind.ROLE:
        if item.duration is None:
            return "Rol para siempre"
        return f"Rol durante {format_span(item.duration)}"
    return kind_detail(item)


def compact_line(
    item: ShopItem,
    now: float,
    *,
    owned: int = 0,
    maxed: bool = False,
    wished: bool = False,
    in_cart: int = 0,
    best_seller: bool = False,
) -> str:
    """Una línea del escaparate: nombre, precio sin IGIC y etiquetas cortas (`LEGEND`).

    Args:
        owned: Cuántos tiene en vigor quien mira.
        maxed: Si ya compró el máximo por persona.
        wished: Si está en su lista de deseos.
        in_cart: Unidades en su carrito.
    """
    price = quote(item, now)
    cost = (
        f"~~{format_amount(price.price)}~~ **{format_amount(price.base)}** "
        f"🏷️ -{price.discount_pct} %"
        if price.discount
        else format_amount(price.base)
    )
    tags = []
    if maxed:
        tags.append("✅ máx.")
    elif owned:
        tags.append("✅" if owned == 1 else f"✅×{owned}")
    if in_cart:
        tags.append(f"🛒×{in_cart}")
    if wished:
        tags.append("⭐")
    if item.sold_out:
        tags.append("❌ agotado")
    elif item.remaining is not None:
        tags.append(f"📦 quedan {item.remaining} de {item.stock}")
    if item.min_level:
        tags.append(f"🔒 nv. {item.min_level}")
    if best_seller:
        tags.append("🔥")
    if item.is_new(now):
        tags.append("🆕")
    if not emblematic(item) and (mark := financiar.badge(price.total, item_category(item))):
        tags.append(mark)
    if not on_sale(item, local_now(now)):
        tags.append(f"🗓️ {season_name(item) or 'fuera de temporada'}")
    return f"{item.emoji} **{item.name}** · {cost}" + (f" · {' '.join(tags)}" if tags else "")


#: Qué quiere decir cada etiqueta de `compact_line`, para el pie del escaparate.
LEGEND = (
    "✅ lo tienes · ⭐ en tu lista · 🛒 en el carrito · 🔒 nivel · 🔥 lo más vendido · 🆕 nuevo"
    " · 💳 a plazos (💳 0 %: sin intereses esta semana)"
)


def fold(text: str) -> str:
    """Minúsculas y sin tildes, para buscar: `Plátano` → `platano`."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip()


def item_category(item: ShopItem) -> str:
    """La categoría de un artículo para financiarlo (su pasillo, `credit.categories`)."""
    return f"tienda:{aisle_of(item.catalog_key).key}"


def financed_purchase(lines: Sequence[TicketLine]) -> credit.Purchase | None:
    """Lo que se financia de unas líneas del ticket (💳 Financiar), o `None` si no se puede.

    Solo entra lo que no es emblemático (numerado o un título): en un carrito mezclado, eso se
    paga al contado en la misma compra (`cash_part`). `None` si no queda nada que financiar o
    el banco no financia su importe. Con artículos de varios pasillos, la categoría es «la
    tienda», que nunca tiene el 0 % TIN de la semana.
    """
    financeable = [line for line in lines if not emblematic(line.item)]
    total = sum(line.total for line in financeable)
    if not financeable or credit.refusal(total) is not None:
        return None
    top = max(financeable, key=lambda line: line.total)
    label = f"{top.item.emoji} {top.item.name}" + (f" × {top.qty}" if top.qty > 1 else "")
    if len(financeable) > 1:
        label += f" y {len(financeable) - 1} más"
    categories = {item_category(line.item) for line in financeable}
    category = categories.pop() if len(categories) == 1 else "tienda"
    return credit.Purchase(label, total, category, cash=cash_part(lines))


def cash_part(lines: Sequence[TicketLine]) -> int:
    """Lo que se paga al contado al financiar unas líneas: lo emblemático."""
    return sum(line.total for line in lines if emblematic(line.item))


def ticket_preview(price: Quote) -> str:
    """El desglose de la caja, antes de pagar."""
    width = 26

    def row(left: str, right: str) -> str:
        return f"{left:<{width - len(right) - 1}} {right}"

    lines = [row("Precio", money(price.price))]
    if price.ipc:
        lines[0] = row(f"Precio (IPC +{price.ipc} %)", money(price.price))
    if price.discount:
        lines.append(row(f"Rebaja -{price.discount_pct} %", f"-{money(price.discount)}"))
    lines += [
        row("Base imponible", money(price.base)),
        row(f"{price.rate.tax} {rate_label(price.rate.rate)}", money(price.tax)),
        "─" * width,
        row("TOTAL", f"{money(price.total)} Y$"),
    ]
    return "```\n" + "\n".join(lines) + "\n```"


def igic_line(price: Quote) -> str:
    """La mordida del IGIC, en subtexto (y lo que ha subido el IPC del crédito, si algo)."""
    if not price.tax:
        line = (
            f"-# 🐶 {TAX_COLLECTOR} no cobra nada: este artículo va al tipo cero "
            f"({price.rate.law})."
        )
    else:
        line = (
            f"-# 🐶 {TAX_COLLECTOR} se lleva {format_amount(price.tax)} de {price.rate.tax} "
            f"({price.rate.label.lower()}, {rate_label(price.rate.rate)}, {price.rate.law})."
        )
    return f"{line}\n{ipc_line(price.ipc)}" if price.ipc else line


def ipc_line(pct: int) -> str:
    """Lo que ha subido los precios el IPC del crédito, en subtexto."""
    return (
        f"-# 📈 IPC del crédito: los precios suben un {pct} % por lo que hay financiado en el "
        "servidor sin devolver. Baja cuando se devuelve."
    )


def entry_line(entry: InventoryEntry, now: float) -> str:
    """Una línea de la mochila."""
    if entry.kind is Kind.ROLE:
        if entry.expires_at is not None:
            return f"{entry.emoji} <@&{entry.role_id}> · vence <t:{int(entry.expires_at)}:R>"
        state = "✅ puesto" if entry.equipped else "💤 guardado"
        return f"{entry.emoji} <@&{entry.role_id}> · {state}"
    if entry.kind is Kind.BOOST:
        boost = format_multiplier(entry.multiplier or 100)
        end = int(entry.expires_at or now)
        if entry.starts_at > now:
            start = int(entry.starts_at)
            return f"{entry.emoji} **{entry.name}** {boost} · en cola, empieza <t:{start}:R>"
        return f"{entry.emoji} **{entry.name}** {boost} · activo hasta <t:{end}:t> (<t:{end}:R>)"
    return f"{entry.emoji} **{entry.name}**"


def group_entries(entries: list[InventoryEntry]) -> list[list[InventoryEntry]]:
    """Entradas de la mochila agrupadas por artículo, en el orden en que llegan."""
    groups: dict[int, list[InventoryEntry]] = defaultdict(list)
    for entry in entries:
        groups[entry.item_id].append(entry)
    return list(groups.values())


def trophy_lines(entries: list[InventoryEntry]) -> list[str]:
    """Objetos agrupados por artículo, con sus números de serie y cómo se usan."""
    lines = []
    for group in group_entries(entries):
        first = group[0]
        text = f"{first.emoji} **{first.name}**"
        if len(group) > 1:
            text += f" ×{len(group)}"
        serials = sorted(e.serial for e in group if e.serial is not None)
        if serials and first.edition:
            numbers = ", ".join(str(s) for s in serials)
            text += f" · nº {numbers} de {first.edition}"
        if (use := use_of(first.catalog_key)) is not None:
            text += f" · *{use.verb.lower()}*"
        if is_title(first.catalog_key):
            text += " · 🎖️ *lo luces*" if any(e.equipped for e in group) else " · 🎖️ título"
        lines.append(text)
    return lines


def skin_lines(entries: list[InventoryEntry], worn: Mapping[str, str] | None = None) -> list[str]:
    """Las skins de la mochila por grupo y, con `worn` (mesa → clave), lo que lleva cada mesa.

    Un tapete vale en todas las mesas con fieltro y cada una lleva el suyo.
    """
    owned = [skin for e in entries if (skin := skin_of_item(e.catalog_key)) is not None]
    lines = []
    for group in GAMES.values():
        mine = [s for s in owned if s.game == group.key]
        if mine:
            lines.append(f"{group.label}: " + " · ".join(f"**{s.label}**" for s in mine))
    if owned and worn is not None:
        on = [
            f"{g.emoji} {SKIN_BY_KEY[worn[g.key]].name if g.key in worn else 'de serie'}"
            for g in GAMES.values()
            if g.wearable and any(fits(s, g.key) for s in owned)
        ]
        lines.append("-# Puestas: " + " · ".join(on))
    return lines


def _usable(entry: InventoryEntry) -> bool:
    return entry.kind is Kind.TROPHY and use_of(entry.catalog_key) is not None


def _clip(text: str, limit: int) -> str:
    """Recorta por líneas enteras para no partir un emoji o un formato a la mitad."""
    if len(text) <= limit:
        return text
    kept: list[str] = []
    size = 0
    for line in text.split("\n"):
        if size + len(line) + 1 > limit - 20:
            break
        kept.append(line)
        size += len(line) + 1
    return "\n".join(kept) + "\n-# …y más cosas."


def _select_emoji(emoji: str) -> discord.PartialEmoji | str | None:
    """Emoji válido para una opción de desplegable (los de bandera y los raros fallan)."""
    try:
        return discord.PartialEmoji.from_str(emoji)
    except (TypeError, ValueError):
        return None


async def _close_view(
    view: ui.LayoutView, message: discord.Message | None, interaction: discord.Interaction | None
) -> None:
    """Desactiva los componentes de una vista caducada y la vuelve a pintar."""
    for child in view.walk_children():
        if isinstance(child, ui.Button | ui.Select):
            child.disabled = True
    try:
        if interaction is not None:
            await interaction.edit_original_response(view=view)
        elif message is not None:
            await message.edit(view=view)
    except discord.HTTPException:
        logger.debug("No se pudo cerrar una vista de la tienda", exc_info=True)


# -- Caja -----------------------------------------------------------------------------


@dataclass
class Bought:
    """Una línea pagada: lo comprado (con el precio cobrado), sus ventas y si se devolvió.

    Attributes:
        refund: Por qué se devolvió (un rol que Discord no dejó dar), o `None`.
        refund_failed: Si además no se pudo devolver el dinero.
    """

    line: TicketLine
    sales: list[Sale]
    refund: str | None = None
    refund_failed: bool = False

    @property
    def item(self) -> ShopItem:
        """El artículo, tal y como quedó al venderlo."""
        return self.line.item


class Checkout(ui.LayoutView):
    """La caja de un comprador: ticket, Pagar y Cancelar. Solo la ve él."""

    def __init__(
        self,
        cog: Tienda,
        guild: discord.Guild,
        buyer: discord.Member,
        item: ShopItem,
        price: Quote,
        *,
        balance: int,
        note: str | None,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild = guild
        self.buyer = buyer
        self.item = item
        self.price = price
        self.balance = balance
        self.note = note
        #: Si se abrió desde el 🎨 de una mesa, la mesa donde se pone la skin al pagar.
        self.skin_game: str | None = None
        self.result: str | None = None
        self.failed = False
        self.done = False
        #: Lo pagado al financiarlo (💳 Financiar), para lo de después de enseñarlo.
        self.paid: tuple[ShopItem, Sale, int] | None = None
        self._lock = TimedLock("shop:Checkout._lock")
        self.rebuild()

    def rebuild(self) -> None:
        """Pinta la caja, el resultado del pago o el error."""
        self.clear_items()
        item, price = self.item, self.price
        if self.done or self.failed:
            colour = COLOR_ERROR if self.failed else COLOR_PAID
            container = ui.Container(accent_colour=colour)
            container.add_item(ui.TextDisplay(self.result or ""))
            self.add_item(container)
            return
        container = ui.Container(accent_colour=COLOR)
        header = f"### 🧾 Caja · {item.emoji} {item.name}\n-# {kind_detail(item)}"
        container.add_item(ui.TextDisplay(header))
        container.add_item(ui.Separator())
        container.add_item(ui.TextDisplay(ticket_preview(price)))
        lines = [igic_line(price)]
        if self.note:
            lines.append(self.note)
        missing = price.total - self.balance
        if missing > 0:
            lines.append(
                f"😬 Tienes {format_amount(self.balance)}: te faltan **{format_amount(missing)}**. "
                "Pásate por el casino o cobra el `imv`."
            )
        else:
            lines.append(
                f"{CURRENCY_EMOJI} Tienes {format_amount(self.balance)} → te quedan "
                f"**{format_amount(self.balance - price.total)}**"
            )
        if not emblematic(item) and (hint := financiar.teaser(price.total, item_category(item))):
            lines.append(f"-# ¿No te llega? {hint}." if missing > 0 else f"-# {hint}.")
        container.add_item(ui.TextDisplay("\n".join(lines)))
        self.add_item(container)
        row: ui.ActionRow = ui.ActionRow()
        row.add_item(
            _button(
                f"💳 Pagar {format_amount(price.total)}",
                self._pay,
                custom_id="caja:pagar",
                style=discord.ButtonStyle.success,
                disabled=missing > 0,
            )
        )
        if financed_purchase([TicketLine(item, 1, price)]) is not None:
            row.add_item(_button(financiar.LABEL, self._finance, custom_id="caja:financiar"))
        row.add_item(_button("Cancelar", self._cancel, custom_id="caja:cancelar"))
        self.add_item(row)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """La caja es solo de quien la abrió (en la práctica ya es efímera)."""
        return interaction.user.id == self.buyer.id

    async def _finance(self, interaction: discord.Interaction) -> None:
        """💳 Financiar: la oferta del banco sustituye a la caja (↩️ Volver la devuelve)."""
        purchase = financed_purchase([TicketLine(self.item, 1, self.price)])
        if purchase is None or self.done or self.failed:
            await interaction.response.edit_message(view=self)
            return
        deal = financiar.Deal(purchase, self._buy_financed, host=self, after=self._after_financed)
        if not await financiar.open_finance(self.cog.bot, interaction, deal):
            await interaction.response.send_message(
                "El banco está cerrado ahora mismo.", ephemeral=True
            )

    async def _buy_financed(self, interaction: discord.Interaction) -> str | None:
        async with self._lock:
            if self.done or self.failed:
                return "Esta caja ya está cerrada."
            self.paid = await self.cog.complete_purchase(self, interaction)
            self.rebuild()
            self.stop()
        return None if self.paid is not None else self.result or "La compra no ha salido."

    async def _after_financed(self, interaction: discord.Interaction) -> None:
        if self.paid is not None:
            await self.cog.after_purchase(interaction, self, self.paid)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.failed, self.result = True, "Compra cancelada. Aquí no ha pasado nada."
        self.rebuild()
        self.stop()
        await interaction.response.edit_message(view=self)

    async def _pay(self, interaction: discord.Interaction) -> None:
        # El ack va antes del candado: un doble clic no espera a la compra del primero.
        await ack(interaction)
        async with self._lock:
            if self.done or self.failed:
                return
            paid = await self.cog.complete_purchase(self, interaction)
            self.rebuild()
            self.stop()
            await interaction.edit_original_response(
                view=self, allowed_mentions=discord.AllowedMentions.none()
            )
        if paid is not None:
            await self.cog.after_purchase(interaction, self, paid)


# -- Mochila --------------------------------------------------------------------------


def backpack_text(
    entries: list[InventoryEntry], now: float, worn: Mapping[str, str] | None = None
) -> str:
    """Lo que hay en una mochila, por tipos, en texto (mochila y `perfil`).

    `worn`: la skin que lleva cada mesa (`Tienda.worn_skins`).
    """
    sections = []
    for kind in Kind:
        mine = [
            e for e in entries if e.kind is kind and not _usable(e) and not is_skin(e.catalog_key)
        ]
        if not mine:
            continue
        lines = (
            trophy_lines(mine)
            if kind in (Kind.TROPHY, Kind.PET)
            else [entry_line(e, now) for e in sorted(mine, key=lambda e: e.starts_at)]
        )
        if kind is Kind.PET:
            lines.append("-# Cuídalas y elige cuál va contigo con `mascota`.")
        sections.append(f"### {kind.icon} {kind.title}\n" + "\n".join(lines))
    usable = [e for e in entries if _usable(e)]
    if usable:
        sections.append("### 🫳 Para usar\n" + "\n".join(trophy_lines(usable)))
    if skins := skin_lines(entries, worn):
        sections.append("### 🎨 Skins\n" + "\n".join(skins))
    if not sections:
        sections.append("Vacía. Date una vuelta por la `tienda`, que hay cositas.")
    return _clip("\n\n".join(sections), 3800)


def skin_options(
    entries: list[InventoryEntry], worn: Mapping[str, str], game: str
) -> list[discord.SelectOption]:
    """Opciones del selector 🎨 de una mesa: la de serie y las suyas que valen aquí.

    El valor es `juego:clave` (`ruleta:` para la de serie); `worn` es lo que
    lleva cada mesa (`Tienda.worn_skins`).
    """
    g = GAMES[game]
    mine = [s for e in entries if (s := skin_of_item(e.catalog_key)) and fits(s, game)]
    options = [
        discord.SelectOption(
            label=f"{g.thing.capitalize()} de serie"[:100],
            value=f"{game}:",
            emoji=_select_emoji(g.emoji),
            default=game not in worn,
        )
    ]
    options += [
        discord.SelectOption(
            label=s.name[:100],
            value=f"{game}:{s.key}",
            emoji=_select_emoji(s.emoji),
            default=worn.get(game) == s.key,
        )
        for s in {s.key: s for s in mine}.values()
    ]
    return options[:25]


def skin_game_options(
    entries: list[InventoryEntry], worn: Mapping[str, str]
) -> list[discord.SelectOption]:
    """Opciones de la mochila: una por mesa donde tiene alguna skin (abre su selector)."""
    owned = [s for e in entries if (s := skin_of_item(e.catalog_key)) is not None]
    options = []
    for g in GAMES.values():
        if not g.wearable or not any(fits(s, g.key) for s in owned):
            continue
        now = SKIN_BY_KEY[worn[g.key]].name if g.key in worn else f"{g.thing} de serie"
        options.append(
            discord.SelectOption(
                label=f"{g.name}: {now}"[:100], value=g.key, emoji=_select_emoji(g.emoji)
            )
        )
    return options[:25]


class Backpack(ui.LayoutView):
    """La mochila de un miembro; su dueño puede ponerse y quitarse roles."""

    def __init__(
        self,
        cog: Tienda,
        guild: discord.Guild,
        member: discord.abc.User,
        viewer: discord.abc.User,
        entries: list[InventoryEntry],
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild = guild
        self.member = member
        self.viewer = viewer
        self.entries = entries
        self.message: discord.Message | None = None
        self.interaction: discord.Interaction | None = None
        self.rebuild()

    def rebuild(self) -> None:
        self.clear_items()
        now = self.cog.clock()
        container = ui.Container(accent_colour=COLOR)
        container.add_item(ui.TextDisplay(f"# 🎒 Mochila de {_name(self.member)}"))
        container.add_item(ui.Separator())
        worn = self.cog.worn_skins(self.guild.id, self.member.id)
        container.add_item(ui.TextDisplay(backpack_text(self.entries, now, worn)))
        self.add_item(container)
        usable = [e for e in self.entries if _usable(e)]

        if usable and self.viewer.id == self.member.id:
            groups = group_entries(usable)
            use_options = []
            for group in groups[:25]:
                first = group[0]
                use = use_of(first.catalog_key)
                assert use is not None  # _usable lo garantiza
                count = f" ×{len(group)}" if len(group) > 1 else ""
                use_options.append(
                    discord.SelectOption(
                        label=f"{first.name}{count}"[:100],
                        value=str(first.item_id),
                        emoji=_select_emoji(first.emoji),
                        description=use.summary.removeprefix("🫳 ")[:100],
                    )
                )
            use_select: ui.Select = ui.Select(
                placeholder="🫳 Usar algo", options=use_options, custom_id="mochila:usar"
            )

            async def use_callback(interaction: discord.Interaction) -> None:
                await self.cog.start_use(interaction, self, int(use_select.values[0]))

            use_select.callback = use_callback  # type: ignore[method-assign]
            use_row: ui.ActionRow = ui.ActionRow()
            use_row.add_item(use_select)
            self.add_item(use_row)

        wardrobe = [e for e in self.entries if e.kind is Kind.ROLE and e.expires_at is None]
        if wardrobe and self.viewer.id == self.member.id:
            options = [
                discord.SelectOption(
                    label=(self._role_name(e) or e.name)[:100],
                    value=str(e.id),
                    description="Puesto: elígelo para quitártelo"
                    if e.equipped
                    else "Guardado: elígelo para ponértelo",
                )
                for e in wardrobe[:25]
            ]
            select: ui.Select = ui.Select(
                placeholder="👔 Ponerte o quitarte un rol", options=options, custom_id="mochila:rol"
            )

            async def callback(interaction: discord.Interaction) -> None:
                await self.cog.toggle_role(interaction, self, int(select.values[0]))

            select.callback = callback  # type: ignore[method-assign]
            row: ui.ActionRow = ui.ActionRow()
            row.add_item(select)
            self.add_item(row)

        titles = [e for e in self.entries if is_title(e.catalog_key)]
        if titles and self.viewer.id == self.member.id:
            # Uno a la vez; si hubiera dos marcados, vale el más reciente.
            shown = max((e.id for e in titles if e.equipped), default=None)
            title_options = [
                discord.SelectOption(
                    label=e.name[:100],
                    value=e.catalog_key or "",
                    emoji=_select_emoji(e.emoji),
                    description="Lo luces ahora" if e.id == shown else "Elígelo para lucirlo",
                    default=e.id == shown,
                )
                for e in titles[:24]
            ]
            title_options.append(
                discord.SelectOption(label="Sin título", value=NO_TITLE, emoji="🚫")
            )
            title_select: ui.Select = ui.Select(
                placeholder="🎖️ Qué título luces",
                options=title_options,
                custom_id="mochila:titulo",
            )

            async def title_callback(interaction: discord.Interaction) -> None:
                await self.cog.choose_title(interaction, self, title_select.values[0])

            title_select.callback = title_callback  # type: ignore[method-assign]
            title_row: ui.ActionRow = ui.ActionRow()
            title_row.add_item(title_select)
            self.add_item(title_row)

        if self.viewer.id == self.member.id and (options := skin_game_options(self.entries, worn)):
            skin_select: ui.Select = ui.Select(
                placeholder="🎨 Cambiar la skin de una mesa",
                options=options,
                custom_id="mochila:skin",
            )

            async def skin_callback(interaction: discord.Interaction) -> None:
                await self.cog.open_skin_picker(interaction, skin_select.values[0])

            skin_select.callback = skin_callback  # type: ignore[method-assign]
            skin_row: ui.ActionRow = ui.ActionRow()
            skin_row.add_item(skin_select)
            self.add_item(skin_row)

    def _role_name(self, entry: InventoryEntry) -> str | None:
        role = self.guild.get_role(entry.role_id or 0)
        return role.name if role else None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.member.id:
            return True
        await interaction.response.send_message(
            "Esa mochila no es tuya. Mira la tuya en `perfil`, sección 🎒 Objetos.", ephemeral=True
        )
        return False

    async def on_timeout(self) -> None:
        await _close_view(self, self.message, self.interaction)


# -- Usar objetos ---------------------------------------------------------------------


class TargetPicker(ui.View):
    """Desplegable de miembros para usar un objeto contra alguien. Solo lo ve quien lo usa."""

    def __init__(self, cog: Tienda, backpack: Backpack, entry: InventoryEntry, use: Use) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.backpack = backpack
        self.entry = entry
        self.use = use
        self.done = False
        select: ui.UserSelect = ui.UserSelect(
            placeholder=f"{entry.emoji} {use.verb}: ¿a quién?", min_values=1, max_values=1
        )
        select.callback = self._picked  # type: ignore[method-assign]
        self.select = select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.backpack.member.id

    async def _picked(self, interaction: discord.Interaction) -> None:
        if self.done:
            await ack(interaction)
            return
        self.done = True
        self.stop()
        target = self.select.values[0]
        await self.cog.perform_use(interaction, self.backpack, self.entry, self.use, target=target)


class UseTextForm(ui.Modal):
    """Formulario del megáfono y del DNI falso."""

    def __init__(self, cog: Tienda, backpack: Backpack, entry: InventoryEntry, use: Use) -> None:
        nickname = use.special == "nickname"
        super().__init__(title=f"{use.verb}"[:45], timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.backpack = backpack
        self.entry = entry
        self.use = use
        self.text: ui.TextInput = ui.TextInput(
            placeholder="Tu nuevo apodo" if nickname else "Lo que quieres gritar",
            max_length=MAX_NICK if nickname else 150,
            required=True,
        )
        label = "Nombre del DNI" if nickname else "Qué gritas por el megáfono"
        self.add_item(ui.Label(text=label, component=self.text))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.perform_use(
            interaction, self.backpack, self.entry, self.use, text=self.text.value
        )


class SkinPicker(ui.View):
    """Selector privado de skin de una mesa (botón 🎨 o mochila).

    Las opciones `buy:<artículo>` son las que se venden y aún no tiene: abren la
    caja, y al pagar se la pone en esta mesa.
    """

    def __init__(self, cog: Tienda, options: list[discord.SelectOption], game: str) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        select: ui.Select = ui.Select(placeholder="🎨 Elige", options=options)

        async def callback(interaction: discord.Interaction) -> None:
            self.stop()
            value = select.values[0]
            if value.startswith("buy:"):
                await self.cog.open_checkout(
                    interaction, int(value.removeprefix("buy:")), skin_game=game
                )
                return
            await self.cog.choose_skin(interaction, value)

        select.callback = callback  # type: ignore[method-assign]
        self.select = select
        self.add_item(select)


def skin_button(bot: commands.Bot, game: str, *, row: int | None = None) -> ui.Button | None:
    """El botón 🎨 para poner en una mesa de `game`, o `None` si no tiene skins.

    El juego lo añade a su vista; el clic abre el selector privado
    (`Tienda.open_skin_picker`). Sin la tienda cargada, no hay botón.
    """
    if game not in GAMES or not game_skins(game) or find_cog(bot, Tienda) is None:
        return None
    button: ui.Button = ui.Button(
        emoji="🎨", style=discord.ButtonStyle.secondary, row=row, custom_id=f"{game}:skin"
    )

    async def callback(interaction: discord.Interaction) -> None:
        if (cog := find_cog(bot, Tienda)) is None:
            await notify(interaction, "La tienda está cerrada ahora mismo.")
            return
        await cog.open_skin_picker(interaction, game)

    button.callback = callback  # type: ignore[method-assign]
    return button


# -- Cog ------------------------------------------------------------------------------


class Tienda(commands.Cog):
    """El Colmado de Jovani: escaparate, caja, mochila y alquileres."""

    def __init__(
        self,
        bot: commands.Bot,
        economy: EconomyService,
        repository: ShopRepository,
        *,
        levels: MessageStatsRepository | None = None,
        clock: Callable[[], float] = time.time,
        catalog: Sequence[CatalogEntry] = CATALOG,
        rng: random.Random | None = None,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.repository = repository
        self.levels = levels
        self.clock = clock
        #: Surtido de serie que se mete en cada servidor (las pruebas pasan `()`).
        self.catalog = tuple(catalog)
        self.rng = rng or random.Random()
        #: Potenciadores sin acabar por `(servidor, miembro)`: `(inicio, fin, %)`.
        self.boosts: dict[tuple[int, int], list[tuple[float, float, int]]] = defaultdict(list)
        #: Servidores cuyo surtido ya se ha mirado desde que arrancó el bot.
        self._stocked: set[int] = set()
        #: Último uso de cada objeto que no se gasta: `(servidor, miembro, uso)` → epoch.
        self.last_used: dict[tuple[int, int, str], float] = {}
        #: Título que luce cada `(servidor, miembro)`, ya con su emoji. En memoria
        #: para que `fortunas` y `ranking` no lean la base de datos por cada nombre.
        self.titles: dict[tuple[int, int], str] = {}
        #: Skin que lleva cada `(servidor, miembro, juego)`: su clave de `skins`.
        #: Sin entrada, la de serie. En memoria para no leer la base de datos al
        #: pintar cada tirada.
        self.skins: dict[tuple[int, int, str], str] = {}
        #: Imagen de cada skin propia por `(servidor, miembro, skin)` (`Skin.custom`).
        #: En memoria por lo mismo; solo la tienen quienes compraron una y la
        #: subieron, y cada una pesa decenas de KB (`skin_images.MAX_SIDE`).
        self.skin_images: dict[tuple[int, int, str], SkinImage] = {}
        #: Segundos entre fotogramas al abrir una caja (las pruebas lo ponen a 0).
        self.lootbox_pause = FRAME_SECONDS
        #: Avisos de cajas ganadas jugando que aún no ha visto su dueño: salen en el
        #: siguiente resultado con dinero (`notices_for`, que llama `renta.hint`).
        self.notices: dict[tuple[int, int], list[str]] = defaultdict(list)
        #: Carrito de cada `(servidor, miembro)`: artículo → unidades, en el orden en
        #: que se añadieron. En memoria: se pierde al reiniciar y caduca en `CART_TTL`.
        self.carts: dict[tuple[int, int], dict[int, int]] = {}
        self.cart_touched: dict[tuple[int, int], float] = {}
        #: Sección, pasillo y orden que eligió cada miembro la última vez en el
        #: escaparate. Uno por miembro: no crece más que el servidor.
        self.shop_prefs: dict[tuple[int, int], Prefs] = {}
        #: Avisos por privado que van aparte de quien los provoca (un puesto del
        #: Rastro): se guardan para cancelarlos al descargar el cog.
        self._tasks: set[asyncio.Task[None]] = set()

    async def cog_load(self) -> None:
        """Carga potenciadores, títulos y skins puestos, y arranca la caducidad."""
        # Idempotente: el bot ya lo hace en `setup_hook`, pero así el cog se
        # puede cargar solo (pruebas, recarga en caliente).
        await self.repository.initialize()
        for guild_id, user_id, start, end, pct in await self.repository.active_boosts(self.clock()):
            self.boosts[(guild_id, user_id)].append((start, end, pct))
        for guild_id, user_id, _key, emoji, name in await self.repository.worn_among(TITLE_KEYS):
            self.titles[(guild_id, user_id)] = f"{emoji} {name}"
        for guild_id, user_id, game, key in await self.repository.worn_skins():
            if (skin := skin_of_item(key)) is not None and fits(skin, game):
                self.skins[(guild_id, user_id, game)] = skin.key
        for guild_id, user_id, skin_key, data in await self.repository.skin_images():
            self.skin_images[(guild_id, user_id, skin_key)] = SkinImage.of(data)
        self._expire.start()
        self._watch_wishes.start()

    async def cog_unload(self) -> None:
        """Para la caducidad, la vigilancia de las listas de deseos y los avisos en marcha."""
        self._expire.cancel()
        self._watch_wishes.cancel()
        for task in self._tasks:
            task.cancel()

    # -- Potenciadores ---------------------------------------------------------------

    def xp_multiplier(self, guild_id: int, user_id: int, now: float) -> float:
        """Multiplicador de XP de un miembro ahora mismo (1,0 sin potenciador)."""
        key = (guild_id, user_id)
        windows = self.boosts.get(key)
        if not windows:
            return 1.0
        windows[:] = [w for w in windows if w[1] > now]
        if not windows:
            del self.boosts[key]
            return 1.0
        return active_multiplier(windows, now)

    async def owned_keys(self, guild_id: int, user_id: int) -> frozenset[str]:
        """Claves de serie de lo que tiene el miembro en vigor (para `pala`)."""
        entries = await self.repository.inventory(guild_id, user_id, self.clock())
        return frozenset(e.catalog_key for e in entries if e.catalog_key)

    async def give_key(self, guild_id: int, user_id: int, catalog_key: str) -> ShopItem | None:
        """Mete en la mochila una unidad del objeto de serie `catalog_key`, sin cobrar.

        Es un premio (la Rueda), como `grant_free_box`: no mueve dinero ni lleva IGIC.
        Solo para artículos de existencias ilimitadas; los numerados van por
        `give_numbered`.

        Returns:
            El artículo dado, o `None` si el servidor no lo tiene.
        """
        await self.stock_up(guild_id)
        item = await self.repository.item_by_key(guild_id, catalog_key)
        if item is None:
            return None
        await self.repository.grant(guild_id, user_id, item, self.clock())
        return item

    async def give_numbered(
        self, guild_id: int, user_id: int, catalog_key: str
    ) -> tuple[ShopItem, int, int] | None:
        """Da la siguiente unidad numerada de `catalog_key` (el expositor de la Rueda).

        Returns:
            `(artículo, número, edición)`, o `None` si no quedan o ya tiene una.
        """
        await self.stock_up(guild_id)
        item = await self.repository.item_by_key(guild_id, catalog_key)
        if item is None:
            return None
        given = await self.repository.grant_numbered(guild_id, user_id, catalog_key, self.clock())
        if given is None:
            return None
        _, serial, edition = given
        return item, serial, edition

    async def units_left(self, guild_id: int, keys: Collection[str]) -> dict[str, int]:
        """Unidades que quedan de cada artículo numerado de `keys` (0 si no existe)."""
        await self.stock_up(guild_id)
        left: dict[str, int] = {}
        for key in keys:
            item = await self.repository.item_by_key(guild_id, key)
            left[key] = 0 if item is None or item.stock is None else max(0, item.stock - item.sold)
        return left

    async def consume_key(self, guild_id: int, user_id: int, catalog_key: str) -> bool:
        """Gasta una unidad del objeto de serie `catalog_key` (los regalos de `pala`).

        `False` si no le queda ninguna.
        """
        entries = await self.repository.inventory(guild_id, user_id, self.clock())
        ids = sorted(e.id for e in entries if e.catalog_key == catalog_key)
        return bool(ids) and await self.repository.consume(guild_id, user_id, ids[0])

    def queued_boosts(
        self, guild_id: int, user_id: int, now: float
    ) -> list[tuple[float, float, int]]:
        """Potenciadores del miembro que no han acabado, en orden."""
        return sorted(w for w in self.boosts.get((guild_id, user_id), []) if w[1] > now)

    # -- Títulos ---------------------------------------------------------------------

    def title(self, guild_id: int, user_id: int) -> str | None:
        """Título que luce un miembro (`🏰 Marquesado de Mogán`), o `None`."""
        return self.titles.get((guild_id, user_id))

    async def wear_title(self, guild_id: int, user_id: int, catalog_key: str | None) -> None:
        """Le pone a un miembro el título `catalog_key` (o se lo quita con `None`)."""
        await self.repository.wear_one(guild_id, user_id, catalog_key, TITLE_KEYS)
        entries = await self.repository.inventory(guild_id, user_id, self.clock())
        worn = next((e for e in entries if e.catalog_key == catalog_key), None)
        if catalog_key is None or worn is None:
            self.titles.pop((guild_id, user_id), None)
        else:
            self.titles[(guild_id, user_id)] = f"{worn.emoji} {worn.name}"

    async def choose_title(
        self, interaction: discord.Interaction, view: Backpack, value: str
    ) -> None:
        """Cambia el título que luce el dueño de la mochila y la repinta."""
        member = interaction.user
        key = None if value == NO_TITLE else value
        before = self.title(view.guild.id, member.id)
        # Guardarlo va a la base de datos: se acepta el clic antes.
        await ack(interaction)
        await self.wear_title(view.guild.id, member.id, key)
        view.entries = await self.repository.inventory(view.guild.id, member.id, self.clock())
        view.rebuild()
        await edit(interaction, view=view, allowed_mentions=discord.AllowedMentions.none())
        after = self.title(view.guild.id, member.id)
        if after != before:
            await logros.track(
                self.bot,
                view.guild.id,
                member,
                interaction.channel,
                shop_title_stats(worn=after is not None),
            )

    async def refresh_worn(self, guild_id: int, user_id: int) -> None:
        """Vuelve a leer el título y las skins de un miembro tras perder un objeto.

        Lo usa el Rastro (`bot.cogs.rastro`) cuando algo sale de la mochila (a un
        puesto o a otra persona): si lo llevaba puesto, deja de lucirlo.
        """
        entries = await self.repository.inventory(guild_id, user_id, self.clock())
        owned = {e.catalog_key for e in entries if e.catalog_key}
        worn = next((e for e in entries if e.catalog_key in TITLE_KEYS and e.equipped), None)
        if worn is None:
            self.titles.pop((guild_id, user_id), None)
        else:
            self.titles[(guild_id, user_id)] = f"{worn.emoji} {worn.name}"
        for key, skin_key in list(self.skins.items()):
            skin = SKIN_BY_KEY.get(skin_key)
            if key[:2] == (guild_id, user_id) and (skin is None or skin.catalog_key not in owned):
                del self.skins[key]

    # -- Skins -----------------------------------------------------------------------

    def skin(self, guild_id: int, user_id: int, game: str) -> Skin | None:
        """La skin que lleva un miembro en `game`, o `None` (la de serie)."""
        key = self.skins.get((guild_id, user_id, game))
        return SKIN_BY_KEY.get(key) if key else None

    def scene_params(self, guild_id: int, user_id: int, game: str) -> dict[str, object]:
        """Lo que pinta la escena de `game` para este miembro (con su imagen, si es propia)."""
        skin = self.skin(guild_id, user_id, game)
        image = self.skin_image(guild_id, user_id, skin.key) if skin and skin.custom else None
        return skin_params_of(game, skin, image)

    # -- Imágenes propias (`Skin.custom`; las sube `pieles`) -------------------------

    def skin_image(self, guild_id: int, user_id: int, skin_key: str) -> SkinImage | None:
        """La imagen que ha subido un miembro para su skin propia `skin_key`, o `None`."""
        return self.skin_images.get((guild_id, user_id, skin_key))

    async def owns_skin(self, guild_id: int, user_id: int, skin: Skin) -> bool:
        """Si la skin está en la mochila del miembro."""
        entries = await self.repository.inventory(guild_id, user_id, self.clock())
        return any(e.catalog_key == skin.catalog_key for e in entries)

    async def set_skin_image(
        self, guild_id: int, user_id: int, skin: Skin, image: SkinImage, *, set_by: int
    ) -> None:
        """Guarda la imagen de la skin propia de un miembro; se ve desde la próxima jugada."""
        await self.repository.set_skin_image(
            guild_id, user_id, skin.key, image.data, set_by=set_by, now=self.clock()
        )
        self.skin_images[(guild_id, user_id, skin.key)] = image

    async def remove_skin_image(self, guild_id: int, user_id: int, skin: Skin) -> bool:
        """Borra la imagen de la skin propia de un miembro; `False` si no tenía."""
        removed = await self.repository.delete_skin_image(guild_id, user_id, skin.key)
        self.skin_images.pop((guild_id, user_id, skin.key), None)
        return removed

    def wear_on_purchase(
        self, guild_id: int, user_id: int, skin: Skin, game: str | None
    ) -> list[str]:
        """Dónde se pone una skin al comprarla.

        En la mesa desde la que se compra (`game`); si no, en su juego, o, si es
        un tapete, en las mesas con fieltro que llevaban el de serie.
        """
        if game is not None and fits(skin, game):
            return [game]
        if GAMES[skin.game].wearable:
            return [skin.game]
        worn = self.worn_skins(guild_id, user_id)
        return [g.key for g in wearable_games(skin) if g.key not in worn]

    def worn_skins(self, guild_id: int, user_id: int) -> dict[str, str]:
        """Lo que lleva un miembro en cada mesa: juego → clave de la skin (sin las de serie)."""
        return {
            g: k for (gid, uid, g), k in self.skins.items() if (gid, uid) == (guild_id, user_id)
        }

    async def wear_skin(self, guild_id: int, user_id: int, game: str, skin: Skin | None) -> None:
        """Le pone a un miembro la skin `skin` en la mesa `game` (la de serie con `None`)."""
        await self.repository.wear_skin(guild_id, user_id, game, skin.catalog_key if skin else None)
        if skin is None:
            self.skins.pop((guild_id, user_id, game), None)
        else:
            self.skins[(guild_id, user_id, game)] = skin.key

    async def sync_reward_skins(
        self, guild_id: int, user_id: int, achievement_ids: list[str] | set[str]
    ) -> list[Skin]:
        """Mete en la mochila las skins que regalan estos logros y le faltan.

        Se llama al desbloquear un logro y al abrir la mochila (así llegan
        también las skins nuevas de logros que ya se tenían). Devuelve las
        que ha dado.
        """
        return await self._grant_skins(guild_id, user_id, rewards_for(set(achievement_ids)))

    async def sync_reward_outfits(
        self, guild_id: int, user_id: int, achievement_ids: list[str] | set[str]
    ) -> list[Outfit]:
        """Mete en la mochila la ropa de mascota que regalan estos logros y le falta."""
        wanted = pet_wardrobe.rewards_for(achievement_ids)
        if not wanted:
            return []
        now = self.clock()
        owned = {e.catalog_key for e in await self.repository.inventory(guild_id, user_id, now)}
        given = []
        for outfit in wanted:
            if outfit.catalog_key in owned:
                continue
            await self.stock_up(guild_id)
            item = await self.repository.item_by_key(guild_id, outfit.catalog_key)
            if item is None:
                continue
            await self.repository.grant(guild_id, user_id, item, now)
            given.append(outfit)
        return given

    async def grant_free_skins(self, guild_id: int, user_id: int, game: str) -> list[Skin]:
        """Mete en la mochila las skins gratis de `game` que le falten (`Skin.free`).

        Se llama al abrir el 🎨 de ese juego y desde el propio juego, no al abrir
        la mochila: así no se llena la de quien nunca ha jugado.
        """
        return await self._grant_skins(guild_id, user_id, free_skins(game))

    async def _grant_skins(
        self, guild_id: int, user_id: int, wanted: tuple[Skin, ...]
    ) -> list[Skin]:
        """Da las skins de `wanted` que no tenga; devuelve las que ha dado."""
        if not wanted:
            return []
        now = self.clock()
        owned = {e.catalog_key for e in await self.repository.inventory(guild_id, user_id, now)}
        given = []
        for skin in wanted:
            if skin.catalog_key in owned:
                continue
            await self.stock_up(guild_id)
            item = await self.repository.item_by_key(guild_id, skin.catalog_key)
            if item is None:
                continue
            await self.repository.grant(guild_id, user_id, item, now)
            given.append(skin)
        return given

    async def claim_reward_skins(self, guild_id: int, user_id: int) -> None:
        """Da las skins y la ropa de logros que ya tenía y le faltan.

        Se llama al abrir la mochila, el 🎨 de una mesa o el armario de `mascota`:
        así llegan también los regalos nuevos de logros que ya se tenían.
        """
        unlocked = await self._unlocked(guild_id, user_id)
        await self.sync_reward_skins(guild_id, user_id, unlocked)
        await self.sync_reward_outfits(guild_id, user_id, unlocked)

    async def _unlocked(self, guild_id: int, user_id: int) -> set[str]:
        """Logros que tiene un miembro, para las skins que se ganan con ellos."""
        from bot.cogs.achievements import Achievements  # evita el import circular

        cog = find_cog(self.bot, Achievements)
        if cog is None:
            return set()
        return set((await cog.fresh_profile(guild_id, user_id)).unlocked)

    async def choose_skin(
        self,
        interaction: discord.Interaction,
        value: str,
        *,
        backpack: Backpack | None = None,
    ) -> None:
        """Cambia la skin de un juego (`juego:clave`; `juego:` para la de serie).

        Desde la mochila la repinta; desde el botón 🎨 de una mesa contesta en
        el mensaje privado del selector.
        """
        member = interaction.user
        guild = interaction.guild
        game, _sep, key = value.partition(":")
        skin = SKIN_BY_KEY.get(key) if key else None
        if guild is None or game not in GAMES or (skin is not None and not fits(skin, game)):
            await notify(interaction, "Esa skin ya no existe.")
            return
        # Comprobar la mochila y guardarlo van a la base de datos: se acepta antes.
        await ack(interaction)
        if skin is not None:
            owned = await self.repository.inventory(guild.id, member.id, self.clock())
            if not any(e.catalog_key == skin.catalog_key for e in owned):
                await notify(interaction, "Esa skin no está en tu mochila.")
                return
        before = self.skin(guild.id, member.id, game)
        await self.wear_skin(guild.id, member.id, game, skin)
        label = skin.label if skin else f"{GAMES[game].thing} de serie"
        if backpack is not None:
            backpack.entries = await self.repository.inventory(guild.id, member.id, self.clock())
            backpack.rebuild()
            await edit(interaction, view=backpack, allowed_mentions=discord.AllowedMentions.none())
        else:
            await edit(
                interaction,
                content=f"🎨 Listo: en {GAMES[game].label} verás **{label}** en la próxima jugada.",
                view=None,
            )
        if before != skin:
            await logros.track(
                self.bot, guild.id, member, interaction.channel, shop_skin_stats(swapped=True)
            )

    async def open_skin_picker(self, interaction: discord.Interaction, game: str) -> None:
        """El botón 🎨 de una mesa: elige tu skin de `game` en un mensaje privado."""
        guild = interaction.guild
        if guild is None or game not in GAMES:
            await notify(interaction, "Aquí no hay skins que elegir.")
            return
        # Leer la mochila va a la base de datos: «pensando…» privado mientras tanto.
        await ack(interaction, new_message=True)
        member = interaction.user
        await self.claim_reward_skins(guild.id, member.id)
        await self.grant_free_skins(guild.id, member.id, game)
        now = self.clock()
        entries = await self.repository.inventory(guild.id, member.id, now)
        options = skin_options(entries, self.worn_skins(guild.id, member.id), game)
        options += await self._skin_offers(guild.id, entries, game, now, 25 - len(options))
        g = GAMES[game]
        if len(options) == 1:
            await edit(
                interaction,
                content=(
                    f"🎨 No hay ningún {g.thing} para {g.label} ahora mismo. Mira la pestaña "
                    "🎨 Skins de la `tienda`."
                ),
            )
            return
        picker = SkinPicker(self, options, game)
        await edit(
            interaction,
            content=f"🎨 ¿Qué {g.thing} quieres en {g.label}? Los 🛒 se compran aquí mismo.",
            view=picker,
        )

    async def _skin_offers(
        self, guild_id: int, entries: list[InventoryEntry], game: str, now: float, room: int
    ) -> list[discord.SelectOption]:
        """Las skins de `game` que se venden ahora y no tiene, como opciones `buy:<artículo>`."""
        if room <= 0:
            return []
        await self.stock_up(guild_id)
        owned = {e.catalog_key for e in entries}
        offers = []
        for skin in game_skins(game):
            if (
                not skin.for_sale
                or skin.catalog_key in owned
                or not in_season(skin, local_now(now))
            ):
                continue
            item = await self.repository.item_by_key(guild_id, skin.catalog_key)
            if item is None or not item.visible:
                continue
            level = f" · nivel {skin.min_level}" if skin.min_level else ""
            offers.append(
                discord.SelectOption(
                    label=f"🛒 {skin.name} · {format_amount(quote(item, now).total)}"[:100],
                    value=f"buy:{item.id}",
                    emoji=_select_emoji(skin.emoji),
                    description=f"Comprar y ponerlo en {GAMES[game].name}{level}"[:100],
                )
            )
        return offers[:room]

    # -- Comprobaciones --------------------------------------------------------------

    async def member_level(self, guild_id: int, user_id: int) -> int:
        """Nivel del miembro según su XP (0 si los niveles no están disponibles)."""
        if self.levels is None:
            return 0
        xp = await self.levels.member_xp(guild_id, user_id)
        return calculate_level_progress(xp).level

    @staticmethod
    def role_problem(guild: discord.Guild, role: discord.Role | None) -> str | None:
        """Por qué no se puede vender o dar `role`, o `None` si se puede."""
        if role is None:
            return "Ese rol ya no existe en el servidor."
        if role.is_default():
            return "@everyone no se vende."
        if role.managed:
            return "Ese rol lo gestiona una integración (un bot o los boosts); no lo puedo dar."
        me = guild.me
        if not me.guild_permissions.manage_roles:
            return "Me falta el permiso **Gestionar roles**."
        if role >= me.top_role:
            return "Ese rol está por encima del mío: súbeme en la lista de roles del servidor."
        dangerous = [p for p in DANGEROUS_PERMISSIONS if getattr(role.permissions, p, False)]
        if dangerous:
            return (
                "Ese rol tiene permisos de moderación o administración "
                f"({', '.join(dangerous)}): no lo vendo, que se lía."
            )
        return None

    # -- Escaparate y mochila --------------------------------------------------------

    async def stock_up(self, guild_id: int) -> int:
        """Mete en el servidor el surtido de serie que le falte (una vez por arranque).

        Returns:
            Cuántos artículos se han añadido.
        """
        if guild_id in self._stocked or not self.catalog:
            return 0
        added = await self.repository.stock_catalog(
            guild_id,
            self.catalog,
            self.clock(),
            revised=REVISED_PRICES,
            retired=RETIRED_ITEMS,
            retaxed=REVISED_TAXES,
        )
        self._stocked.add(guild_id)
        if added:
            logger.info("Surtido de serie: %s artículos nuevos en %s", added, guild_id)
        return added

    async def restock(self, guild_id: int) -> int:
        """Vuelve a meter los artículos de serie que se hayan retirado."""
        added = await self.repository.stock_catalog(
            guild_id,
            self.catalog,
            self.clock(),
            restock=True,
            revised=REVISED_PRICES,
            retired=RETIRED_ITEMS,
            retaxed=REVISED_TAXES,
        )
        self._stocked.add(guild_id)
        return added

    async def storefront(
        self, guild: discord.Guild, owner: discord.abc.User, *, search: str | None = None
    ) -> Storefront:
        """Escaparate listo para enviar (`bot.cogs.shop_front.Storefront`).

        Args:
            search: Lo que se busca, si se abre buscando.
        """
        from bot.cogs.shop_front import Storefront  # evita el import circular

        await self.stock_up(guild.id)
        view = Storefront(self, guild, owner, search=search)
        await view.load()
        return view

    async def backpack_embed(self, guild: discord.Guild, member: discord.abc.User) -> discord.Embed:
        """Sección 🎒 Objetos de `perfil`: la mochila en texto, sin botones."""
        entries = await self.repository.inventory(guild.id, member.id, self.clock())
        embed = discord.Embed(
            title=f"🎒 Mochila de {_name(member)}",
            description=backpack_text(entries, self.clock(), self.worn_skins(guild.id, member.id)),
            color=COLOR,
        )
        embed.set_footer(text="Para usar objetos o ponerte roles, abre la mochila.")
        return embed

    async def item_count(self, guild_id: int, user_id: int) -> int:
        """Cuántas cosas en vigor tiene alguien en la mochila."""
        return len(await self.repository.inventory(guild_id, user_id, self.clock()))

    async def backpack_view(
        self, guild: discord.Guild, member: discord.abc.User, viewer: discord.abc.User
    ) -> Backpack:
        """Mochila de `member` vista por `viewer`.

        Antes de leerla, le da las skins de logros que le falten (las de logros
        que ya tenía cuando la skin llegó al bot).
        """
        if viewer.id == member.id:
            await self.claim_reward_skins(guild.id, member.id)
        entries = await self.repository.inventory(guild.id, member.id, self.clock())
        return Backpack(self, guild, member, viewer, entries)

    async def toggle_role(
        self, interaction: discord.Interaction, view: Backpack, entry_id: int
    ) -> None:
        """Pone o quita un rol comprado para siempre y repinta la mochila."""
        entry = next((e for e in view.entries if e.id == entry_id), None)
        member = interaction.user
        if entry is None or not isinstance(member, discord.Member):
            await notify(interaction, "Ese rol ya no está en tu mochila.")
            return
        role = view.guild.get_role(entry.role_id or 0)
        if (problem := self.role_problem(view.guild, role)) is not None:
            await notify(interaction, problem)
            return
        assert role is not None  # role_problem lo comprueba
        # Cambiar el rol en Discord y guardarlo tardan: se acepta el clic antes.
        await ack(interaction)
        equip = not entry.equipped
        try:
            if equip:
                await member.add_roles(role, reason="Se lo pone desde la mochila")
            else:
                await member.remove_roles(role, reason="Se lo quita desde la mochila")
        except discord.HTTPException:
            await notify(interaction, "No he podido cambiarte el rol. Revisa mis permisos.")
            return
        await self.repository.set_equipped(view.guild.id, member.id, entry.id, equip)
        view.entries = await self.repository.inventory(view.guild.id, member.id, self.clock())
        view.rebuild()
        await edit(interaction, view=view, allowed_mentions=discord.AllowedMentions.none())

    # -- Usar objetos ----------------------------------------------------------------

    def cooldown_left(self, guild_id: int, user_id: int, use: Use, now: float) -> int:
        """Segundos que faltan para poder volver a usar un objeto que no se gasta."""
        if use.consumes or not use.cooldown:
            return 0
        last = self.last_used.get((guild_id, user_id, use.key))
        if last is None:
            return 0
        return max(0, int(last + use.cooldown - now + 0.999))

    async def start_use(
        self, interaction: discord.Interaction, backpack: Backpack, item_id: int
    ) -> None:
        """Primer paso de usar un objeto desde la mochila.

        Si el uso pide a alguien, abre el desplegable de miembros; si pide
        texto, el formulario; si no, lo usa ya.
        """
        entries = [e for e in backpack.entries if e.item_id == item_id and _usable(e)]
        if not entries:
            await interaction.response.send_message("Eso ya no está en tu mochila.", ephemeral=True)
            return
        entry = min(entries, key=lambda e: e.id)
        use = use_of(entry.catalog_key)
        assert use is not None  # _usable lo garantiza
        wait = self.cooldown_left(backpack.guild.id, backpack.member.id, use, self.clock())
        if wait:
            await interaction.response.send_message(
                f"⏳ Espera {wait} s antes de volver a usar {entry.emoji} {entry.name}. "
                "Que los vecinos también descansan.",
                ephemeral=True,
            )
            return
        if use.target is Target.MEMBER:
            picker = TargetPicker(self, backpack, entry, use)
            await interaction.response.send_message(
                f"{entry.emoji} **{entry.name}**: elige a quién.", view=picker, ephemeral=True
            )
            return
        if use.target is Target.TEXT:
            await interaction.response.send_modal(UseTextForm(self, backpack, entry, use))
            return
        await self.perform_use(interaction, backpack, entry, use)

    async def perform_use(
        self,
        interaction: discord.Interaction,
        backpack: Backpack,
        entry: InventoryEntry,
        use: Use,
        *,
        target: discord.abc.User | None = None,
        text: str | None = None,
    ) -> None:
        """Usa un objeto: lo gasta si toca, publica el resultado y apunta los logros.

        Ningún uso mueve dinero, así que no hay impuestos ni gancho de la Renta
        (Biblia, sección 4): lo que tributó fue la compra.
        """
        guild, member = backpack.guild, interaction.user
        # Gastar el objeto y sortear premios van a la base de datos: se acepta antes.
        await ack(interaction)
        now = self.clock()
        shout = ""
        if self.cooldown_left(guild.id, member.id, use, now):
            await notify(interaction, "⏳ Todavía no. Respira.")
            return

        if use.special == "megaphone":
            try:
                shout = discord.utils.escape_mentions(
                    discord.utils.escape_markdown(clean_shout(text or ""))
                )
            except ValueError as error:
                await notify(interaction, f"❌ {error}")
                return
        if use.special == "nickname":
            problem = self._nickname_problem(guild, member, text)
            if problem is not None:
                await notify(interaction, f"❌ {problem}")
                return

        if use.consumes and not await self.repository.consume(guild.id, member.id, entry.id):
            await notify(
                interaction,
                "Eso ya lo has gastado. Mira tu mochila en `perfil`.",
                ephemeral=True,
            )
            return

        who = f"**{_name(member)}**"
        item_label = f"{entry.emoji} {entry.name}"
        prize: ShopItem | None = None
        if use.special == "lootbox":
            await self._perform_lootbox(interaction, backpack, entry, who, now)
            return
        if use.special == "mystery":
            outcome = await self._open_mystery(guild.id, member.id, who, entry, now)
            if outcome is None:
                await self.repository.restore(entry.id)
                await notify(
                    interaction,
                    "La caja está vacía: no hay coleccionables que sortear. Te la devuelvo.",
                )
                return
            result, prize = outcome
        elif use.special == "megaphone":
            result = megaphone_text(who, shout)
        elif use.special == "nickname":
            assert isinstance(member, discord.Member)
            new = " ".join((text or "").split())[:MAX_NICK]
            old = member.display_name
            try:
                await member.edit(nick=new, reason="DNI falso de la tienda")
            except discord.HTTPException:
                await self.repository.restore(entry.id)
                await notify(
                    interaction,
                    "❌ Discord no me deja cambiarte el apodo. Te devuelvo el DNI.",
                )
                return
            result = nickname_text(who, discord.utils.escape_markdown(old), new)
        else:
            mention = target.mention if target is not None else None
            result = resolve(
                use,
                who=who,
                item=item_label,
                rng=self.rng,
                now=now,
                target=mention,
                target_is_self=target is not None and target.id == member.id,
                target_is_bot=target is not None and target.bot,
            )

        victim = (
            target if target is not None and not target.bot and target.id != member.id else None
        )
        mentions = (
            discord.AllowedMentions(users=[victim], everyone=False, roles=False)
            if victim is not None
            else discord.AllowedMentions.none()
        )
        text = result.text
        if pet := await mascotas.cameo(self.bot, guild.id, member.id, Moment(Event.USE)):
            text += f"\n{pet}"
        await notify(interaction, text, ephemeral=False, allowed_mentions=mentions)
        if not use.consumes:
            self.last_used[(guild.id, member.id, use.key)] = now
        await self._refresh_backpack(backpack)

        collection = await self.repository.collection_size(guild.id, member.id)
        await logros.track(
            self.bot,
            guild.id,
            member,
            interaction.channel,
            shop_use_stats(
                use=use.key,
                flags=result.flags,
                consumed=use.consumes,
                targeted=victim is not None,
                at_self=target is not None and target.id == member.id,
                at_bot=target is not None and target.bot,
                prize_price=prize.price if prize is not None else None,
                collection=collection,
                when=now,
            ),
        )
        if victim is not None:
            await logros.track(
                self.bot, guild.id, victim, interaction.channel, shop_hit_stats(use=use.key)
            )

    async def _perform_lootbox(
        self,
        interaction: discord.Interaction,
        backpack: Backpack,
        entry: InventoryEntry,
        who: str,
        now: float,
    ) -> None:
        """Abre una caja de `lootboxes` (la unidad ya está gastada y el clic aceptado).

        Sortea (`lootboxes.roll`), mete la pieza o el tapete en la mochila sin cobrar
        (`ShopRepository.grant`) y lo enseña con una animación de tres fotogramas sobre un
        mismo mensaje (agitar la caja, el color de la rareza, la pieza), de ~1,5 s en total.
        Todo lo que toca la base de datos va antes del primer fotograma. Abrir no mueve
        dinero (Biblia, «Usar un objeto no mueve dinero»): lo que tributó fue la compra, con
        IGIC general. Si no hay nada que dar (el servidor ha retirado todas las piezas), la
        caja vuelve a la mochila.
        """
        guild, member = backpack.guild, interaction.user
        box = box_of(entry.catalog_key)
        if box is None:
            await self.repository.restore(entry.id)
            await notify(interaction, "Esa caja no sé abrirla. Te la devuelvo.")
            return
        # La caja ya está gastada: si la base de datos falla antes de dar la pieza, se
        # devuelve entera en vez de perderse.
        try:
            await self.stock_up(guild.id)
            mine = await self.repository.inventory(guild.id, member.id, now)
            owned = {e.catalog_key for e in mine if e.catalog_key}
            items = {
                i.catalog_key: i
                for i in await self.repository.items(guild.id, include_hidden=True)
                if i.catalog_key
            }
            # Una pieza se da aunque esté oculta (siempre lo está); un tapete solo si se
            # sigue vendiendo y sin existencias limitadas.
            have = {k for k, i in items.items() if is_piece(k) or (i.visible and i.stock is None)}
            drop = roll(box, self.rng, owned=owned, have=have)
            if drop is None:
                await self.repository.restore(entry.id)
                await notify(
                    interaction,
                    "La caja está vacía: no hay piezas que sortear. Te la devuelvo.",
                )
                return
            _given, dupe = await self.repository.grant(
                guild.id, member.id, items[drop.catalog_key], now
            )
        except Exception:
            logger.exception("No se pudo abrir una caja de %s; se devuelve", member.id)
            await self.repository.restore(entry.id)
            await notify(interaction, "La caja se ha atascado. Te la devuelvo entera.")
            return
        held = owned | {drop.catalog_key}
        collection = COLLECTION_BY_KEY[drop.piece.collection] if drop.piece else None
        have_in_collection = (
            sum(1 for p in collection.pieces if p.key in held) if collection is not None else 0
        )
        last = reveal_frame(
            who,
            box,
            drop,
            dupe=dupe,
            owned=have_in_collection,
            rng=self.rng,
            price_text=format_amount(drop.price),
        )
        # Las piezas buenas y los tapetes son un momento sonado: la mascota sale siempre.
        notable = drop.skin is not None or drop.rarity >= Rarity.LEGENDARY
        moment = Moment(Event.LOOTBOX, notable)
        if pet := await mascotas.cameo(self.bot, guild.id, member.id, moment):
            last += f"\n{pet}"
        await self._animate(
            interaction, [shake_frame(who, box, self.rng), glow_frame(who, box, drop), last]
        )
        await self._refresh_backpack(backpack)

        collection_size = await self.repository.collection_size(guild.id, member.id)
        await logros.track(
            self.bot,
            guild.id,
            member,
            interaction.channel,
            shop_use_stats(
                use="lootbox",
                flags=(),
                consumed=True,
                targeted=False,
                at_self=False,
                at_bot=False,
                prize_price=None,
                collection=collection_size,
                when=now,
            ),
        )
        await logros.track(
            self.bot,
            guild.id,
            member,
            interaction.channel,
            lootbox_stats(
                box=box.key,
                rarity=drop.rarity.stat,
                tapete=drop.skin is not None,
                hielo=drop.skin is not None and drop.skin.key == HIELO,
                dupe=dupe,
                seasonal=box.season is not None,
                collection=collection.key if collection is not None else None,
                owned=have_in_collection,
                when=now,
            ),
        )

    async def _animate(self, interaction: discord.Interaction, frames: list[str]) -> None:
        """Manda el primer fotograma al canal y lo va editando con los demás.

        Si una edición falla (mensaje borrado, límite de Discord) se salta el resto y el
        último fotograma, que es el resultado, sale como mensaje nuevo: nunca se pierde.
        """
        mentions = discord.AllowedMentions.none()
        try:
            message = await interaction.followup.send(
                frames[0], ephemeral=False, wait=True, allowed_mentions=mentions
            )
        except discord.HTTPException:
            # Sin el primer fotograma no hay animación: el resultado sale solo, en privado
            # si tampoco deja en público. La pieza ya está en la mochila.
            logger.warning("No se pudo empezar la apertura de una caja", exc_info=True)
            try:
                await notify(interaction, frames[-1], allowed_mentions=mentions)
            except discord.HTTPException:
                logger.warning("Ni siquiera se pudo enseñar la caja abierta", exc_info=True)
            return
        try:
            for frame in frames[1:]:
                await asyncio.sleep(self.lootbox_pause)
                await message.edit(content=frame, allowed_mentions=mentions)
        except (discord.HTTPException, AttributeError):
            logger.warning("No se pudo animar la apertura de una caja", exc_info=True)
            await notify(interaction, frames[-1], ephemeral=False, allowed_mentions=mentions)

    async def grant_free_box(
        self, guild_id: int, user_id: int, box_key: str, *, count: int = 1, because: str
    ) -> str | None:
        """Mete en la mochila `count` cajas ganadas jugando, sin cobrar, y devuelve el aviso.

        Es un regalo del colmado, como `ShopRepository.grant`: no mueve dinero (Biblia,
        sección 4), así que no hay ni IGIC ni gancho de la Renta. Cuenta para los logros
        de cajas gratis.

        Args:
            because: Por qué se gana, para el aviso («tus 100 jugadas»).

        Returns:
            La línea del aviso, o `None` si el servidor no tiene la caja.
        """
        box = BOX_BY_KEY[box_key]
        await self.stock_up(guild_id)
        item = await self.repository.item_by_key(guild_id, box.key)
        if item is None or count < 1:
            return None
        now = self.clock()
        for _ in range(count):
            await self.repository.grant(guild_id, user_id, item, now)
        logros.note(self.bot, guild_id, user_id, lootbox_free_stats(count))
        return free_box_line(box, because=because, count=count)

    async def grant_ong_gift(
        self,
        guild_id: int,
        user_id: int,
        keys: Collection[str],
        *,
        cheap_max: int,
        exclusive: float,
    ) -> ShopItem | None:
        """Mete en la mochila un regalo de una ONG: uno de los suyos o algo barato de la tienda.

        Con probabilidad `exclusive`, uno de sus regalos exclusivos (`keys`, mejor uno que
        aún no tenga); si no, un objeto que la tienda puede regalar (`giftable`) de
        `cheap_max` Y$ o menos. Es un regalo, como `grant_free_box`: no mueve dinero ni
        lleva IGIC.

        Returns:
            El artículo regalado, o `None` si el servidor ha retirado todos.
        """
        await self.stock_up(guild_id)
        now = self.clock()
        owned = {e.catalog_key for e in await self.repository.inventory(guild_id, user_id, now)}
        items = await self.repository.items(guild_id, include_hidden=True)
        own = [i for i in items if i.catalog_key in keys]
        own = [i for i in own if i.catalog_key not in owned] or own
        cheap = [i for i in items if giftable(i) and i.price <= cheap_max]
        pool = own if own and (not cheap or self.rng.random() < exclusive) else cheap
        if not pool:
            return None
        item = self.rng.choice(pool)
        await self.repository.grant(guild_id, user_id, item, now)
        return item

    def notices_for(self, guild_id: int, user_id: int) -> str | None:
        """Los avisos de cajas ganadas que aún no ha visto (se sirven una sola vez)."""
        lines = self.notices.pop((guild_id, user_id), None)
        return "\n".join(lines) if lines else None

    async def _open_mystery(
        self, guild_id: int, user_id: int, who: str, box: InventoryEntry, now: float
    ) -> tuple[UseResult, ShopItem] | None:
        """Sortea y entrega el premio de una caja botín, o `None` si no hay premios.

        Premios: coleccionables visibles, sin uso y de existencias ilimitadas,
        para no gastar unidades numeradas de nadie. Los títulos no tocan: se
        lucen porque se han pagado. Los muebles del vacacional tampoco: suben la renta
        de los pisos, y eso no se regala.
        """
        candidates = [
            item
            for item in await self.repository.items(guild_id)
            if giftable(item) and item.id != box.item_id
        ]
        if not candidates:
            return None
        prize = candidates[pick_mystery([c.price for c in candidates], self.rng)]
        _entry, dupe = await self.repository.grant(guild_id, user_id, prize, now)
        label = f"{prize.emoji} **{prize.name}** ({format_amount(prize.price)})"
        return mystery_text(who, label, price=prize.price, dupe=dupe), prize

    @staticmethod
    def _nickname_problem(
        guild: discord.Guild, member: discord.abc.User, text: str | None
    ) -> str | None:
        """Por qué no se puede cambiar el apodo con el DNI falso, o `None`."""
        new = " ".join((text or "").split())
        if not new:
            return "El DNI necesita un nombre."
        if len(new) > MAX_NICK:
            return f"Discord no deja apodos de más de {MAX_NICK} caracteres."
        if not isinstance(member, discord.Member):
            return "Esto solo funciona en un servidor."
        me = guild.me
        if not me.guild_permissions.manage_nicknames:
            return "Me falta el permiso **Gestionar apodos**. El DNI sigue en tu mochila."
        if member.id == guild.owner_id or member.top_role >= me.top_role:
            return (
                "Tu rol está por encima del mío y no te puedo cambiar el apodo. "
                "El DNI sigue en tu mochila."
            )
        return None

    async def _refresh_backpack(self, backpack: Backpack) -> None:
        """Vuelve a pintar la mochila tras usar algo (si se puede; si no, da igual)."""
        backpack.entries = await self.repository.inventory(
            backpack.guild.id, backpack.member.id, self.clock()
        )
        backpack.rebuild()
        try:
            if backpack.interaction is not None:
                await backpack.interaction.edit_original_response(
                    view=backpack, allowed_mentions=discord.AllowedMentions.none()
                )
            elif backpack.message is not None:
                await backpack.message.edit(
                    view=backpack, allowed_mentions=discord.AllowedMentions.none()
                )
        except discord.HTTPException:
            logger.debug("No se pudo repintar la mochila", exc_info=True)

    # -- Caja ------------------------------------------------------------------------

    async def open_checkout(
        self, interaction: discord.Interaction, item_id: int, *, skin_game: str | None = None
    ) -> None:
        """Abre la caja de un artículo para quien ha pulsado Comprar.

        `skin_game`: la mesa desde cuyo 🎨 se compra una skin, para ponérsela ahí.
        """
        guild = interaction.guild
        buyer = interaction.user
        if guild is None or not isinstance(buyer, discord.Member):
            await notify(interaction, "La tienda solo abre en un servidor.")
            return
        # La caja lee artículo, mochila, nivel y saldo: se acepta el clic antes.
        # Los errores rellenan el «pensando…» privado en vez de dejarlo colgado.
        await ack(interaction, new_message=True)
        item = await self.repository.item(guild.id, item_id)
        if item is None or not item.visible:
            await edit(
                interaction,
                content="Ese artículo ya no está a la venta. Abre la `tienda` otra vez.",
            )
            return
        now = self.clock()
        error, note = await self._precheck(guild, buyer, item, now)
        if error is not None:
            await edit(interaction, content=f"❌ {error}")
            return
        balance = await self.economy.balance(guild.id, buyer.id)
        view = Checkout(self, guild, buyer, item, quote(item, now), balance=balance, note=note)
        view.skin_game = skin_game
        await edit(interaction, view=view, allowed_mentions=discord.AllowedMentions.none())

    def check_line(
        self,
        guild: discord.Guild,
        buyer: discord.Member,
        item: ShopItem,
        qty: int,
        *,
        bought: int,
        entries: Sequence[InventoryEntry],
        level: int,
        now: float,
    ) -> str | None:
        """Por qué `buyer` no puede llevarse `qty` unidades de `item`, o `None` si puede.

        Lo mismo que se vuelve a mirar al pagar (`ineligibility`, dentro de la
        transacción), más la temporada, el rol y la cantidad. Sin base de datos: los
        datos los trae quien llama.

        Args:
            bought: Compras suyas de este artículo (`ShopRepository.purchase_counts`).
            entries: Su mochila (para los roles).
            level: Su nivel (0 si el artículo no pide nivel).
        """
        if not on_sale(item, local_now(now)):
            season = season_name(item) or "su temporada"
            return f"Eso solo se vende en {season}. Vuelve entonces."
        if item.kind is Kind.ROLE:
            role = guild.get_role(item.role_id or 0)
            if (problem := self.role_problem(guild, role)) is not None:
                return problem
            mine = [e for e in entries if e.role_id == item.role_id]
            if role in buyer.roles and not mine:
                return "Ya llevas ese rol, y no te lo vendió el colmado."
        owns_role = item.permanent_role and any(
            e.role_id == item.role_id and e.expires_at is None for e in entries
        )
        reason = ineligibility(item, now=now, level=level, bought=bought, owns_role=owns_role)
        if reason is not None:
            return reason
        cap = max_quantity(item, bought)
        if qty > cap:
            return f"De este puedes llevarte {cap} como mucho."
        return None

    async def _precheck(
        self, guild: discord.Guild, buyer: discord.Member, item: ShopItem, now: float
    ) -> tuple[str | None, str | None]:
        """Lo que se puede saber antes de cobrar: `(error, nota para la caja)`.

        Las reglas de existencias y límites se vuelven a mirar al pagar,
        dentro de la transacción (`ShopRepository.reserve`).
        """
        entries = (
            await self.repository.inventory(guild.id, buyer.id, now)
            if item.kind is Kind.ROLE
            else []
        )
        bought = (
            (await self.repository.purchase_counts(guild.id, buyer.id))[item.id]
            if item.per_user is not None
            else 0
        )
        level = await self.member_level(guild.id, buyer.id) if item.min_level else 0
        error = self.check_line(
            guild, buyer, item, 1, bought=bought, entries=entries, level=level, now=now
        )
        if error is not None:
            return error, None
        note = None
        if item.kind is Kind.ROLE:
            rental = next(
                (e for e in entries if e.role_id == item.role_id and e.expires_at is not None),
                None,
            )
            if item.duration is not None and rental is not None:
                note = (
                    f"🔁 Ya lo tienes alquilado hasta <t:{int(rental.expires_at or now)}:f>: "
                    f"se suman {format_span(item.duration)}."
                )
        elif item.kind is Kind.BOOST:
            queue = self.queued_boosts(guild.id, buyer.id, now)
            if queue:
                note = (
                    f"⏳ Ya tienes {len(queue)} potenciador{'es' if len(queue) > 1 else ''}: "
                    f"este se pone a la cola y empieza <t:{int(queue[-1][1])}:R>."
                )
        return None, note

    async def complete_cart(
        self, guild: discord.Guild, buyer: discord.Member, lines: Sequence[TicketLine]
    ) -> tuple[list[Bought], int]:
        """Cobra varias líneas de una vez y entrega lo comprado. Todo o nada.

        Existencias, límites, nivel y precios se vuelven a mirar dentro de la
        transacción del cobro (`ShopRepository.reserve_cart`), unidad a unidad. Después
        se dan los roles: si Discord no deja dar uno, esa línea se devuelve entera
        (`Bought.refund`) y el resto se queda, porque ya se ha cobrado y entregado.

        Returns:
            `(líneas pagadas, saldo final)`.

        Raises:
            ShopError: Si alguna línea no se puede comprar. No se cobra nada.
            InsufficientFundsError: Si no le llega para el total. No se cobra nada.
        """
        now = self.clock()
        when = local_now(now)
        for line in lines:
            if not on_sale(line.item, when):
                raise ShopError(f"{line.item.emoji} {line.item.name}: se acabó su temporada.")
        needs_level = any(line.item.min_level for line in lines)
        level = await self.member_level(guild.id, buyer.id) if needs_level else 0
        reserved, balance = await self.economy.purchase_lines(
            guild.id,
            buyer.id,
            lines=[(line.base, line.tax, line.item.kind.value) for line in lines],
            reserve=self.repository.reserve_cart(
                guild.id,
                buyer.id,
                [(line.item.id, line.qty, line.price) for line in lines],
                level=level,
                now=now,
            ),
        )
        bought = [
            Bought(TicketLine(item, len(sales), line.price), sales)
            for line, (item, sales) in zip(lines, reserved, strict=True)
        ]
        for entry in bought:
            item = entry.item
            if item.kind is Kind.BOOST:
                for sale in entry.sales:
                    if sale.expires_at is not None:
                        self.boosts[(guild.id, buyer.id)].append(
                            (sale.starts_at, sale.expires_at, item.multiplier or 100)
                        )
            if item.kind is not Kind.ROLE:
                continue
            problem = await self._give_role(guild, buyer, item, entry.sales[0].invoice)
            if problem is None:
                continue
            entry.refund = problem
            try:
                balance = await self.economy.refund_purchase(
                    guild.id,
                    buyer.id,
                    base=entry.line.base,
                    tax=entry.line.tax,
                    concept=item.kind.value,
                    release=self._release(entry.sales),
                )
            except (InsufficientFundsError, ShopError):
                # Solo pasa si el Estado ya se ha gastado ese IGIC en devoluciones.
                logger.exception("No se pudo devolver la compra %s", entry.sales[0].purchase_id)
                entry.refund_failed = True
        return bought, balance

    async def _give_role(
        self, guild: discord.Guild, buyer: discord.Member, item: ShopItem, invoice: int
    ) -> str | None:
        """Le pone al comprador el rol que ha pagado; el motivo si no se puede."""
        role = guild.get_role(item.role_id or 0)
        problem = self.role_problem(guild, role)
        if problem is None and role is not None:
            try:
                await buyer.add_roles(role, reason=f"Compra en la tienda (ticket {invoice})")
            except discord.HTTPException:
                logger.warning("No se pudo dar el rol %s comprado por %s", item.role_id, buyer.id)
                problem = "Discord no me dejó darte el rol."
        return problem

    def _release(self, sales: Sequence[Sale]) -> Callable[[sqlite3.Connection], None]:
        """Anula varias ventas en una transacción, de la última a la primera.

        Al revés para que un alquiler renovado unidad a unidad vuelva a como estaba.
        """
        releases = [self.repository.release(sale.purchase_id) for sale in reversed(sales)]

        def run(connection: sqlite3.Connection) -> None:
            for release in releases:
                release(connection)

        return run

    async def complete_purchase(
        self, view: Checkout, interaction: discord.Interaction
    ) -> tuple[ShopItem, Sale, int] | None:
        """Cobra la compra de la caja y entrega el artículo.

        Deja en `view.result` el texto que verá el comprador. Si no se pudo
        dar el rol, devuelve el dinero.

        Returns:
            `(artículo, venta, saldo)` si se ha pagado, `None` si no.
        """
        guild, buyer = view.guild, view.buyer
        now = self.clock()
        if not on_sale(view.item, local_now(now)):
            # La caja se abrió en temporada y se ha pagado fuera: no se cobra.
            view.failed, view.result = True, "❌ Se acabó la temporada de ese artículo."
            return None
        try:
            (entry,), balance = await self.complete_cart(
                guild, buyer, [TicketLine(view.item, 1, view.price)]
            )
        except ShopError as error:
            view.failed, view.result = True, f"❌ {error}"
            return None
        except InsufficientFundsError as error:
            view.failed = True
            view.result = (
                f"❌ No te llega: tienes {format_amount(error.balance)} "
                f"y son {format_amount(view.price.total)}."
            )
            return None
        if entry.refund is not None:
            view.failed = True
            if entry.refund_failed:
                view.result = (
                    f"❌ {entry.refund} Y encima no he podido devolverte el dinero: avisa a un "
                    f"admin con el ticket nº {entry.sales[0].invoice}."
                )
            else:
                view.result = (
                    f"❌ {entry.refund} Te he devuelto los {format_amount(view.price.total)} "
                    f"(IGIC incluido). Saldo: {format_amount(balance)}"
                )
            return None
        item, sale = entry.item, entry.sales[0]
        view.done = True
        view.result = await self._paid_text(item, view.price, sale, buyer, balance, now)
        return item, sale, balance

    @staticmethod
    def _got_text(item: ShopItem, sales: Sequence[Sale], now: float) -> str:
        """Lo que ha conseguido el comprador con `sales`, las unidades de un artículo."""
        sale = sales[-1]
        if item.kind is Kind.ROLE:
            return (
                f"Ya llevas <@&{item.role_id}>"
                + ("" if sale.expires_at is None else f" hasta <t:{int(sale.expires_at)}:f>")
                + ("; se ha sumado a tu alquiler." if sale.renewed else ".")
            )
        if item.kind is Kind.BOOST:
            boost = format_multiplier(item.multiplier or 100)
            if len(sales) > 1:
                end = int(sale.expires_at or now)
                return f"{len(sales)} × XP {boost} en cola: el último acaba <t:{end}:R>."
            return (
                f"XP {boost} activo hasta <t:{int(sale.expires_at or now)}:t>."
                if sale.starts_at <= now
                else f"XP {boost} en cola: empieza <t:{int(sale.starts_at)}:R>."
            )
        if item.kind is Kind.PET:
            return (
                f"{item.emoji} {item.name} ya vive contigo. Ponle nombre, cuídala y elige "
                "si te acompaña con `mascota`."
            )
        many = f" ×{len(sales)}" if len(sales) > 1 else ""
        got = f"{item.emoji} {item.name}{many} ya está en tu mochila."
        serials = [str(s.serial) for s in sales if s.serial is not None]
        if serials:
            noun = "Unidad" if len(serials) == 1 else "Unidades"
            got += f" {noun} nº {', '.join(serials)} de {sale.edition}."
        if (use := use_of(item.catalog_key)) is not None:
            got += f" Úsalo desde la mochila ({use.verb.lower()})."
        if is_title(item.catalog_key):
            got += " Ya lo luces junto a tu nombre; lo cambias en la mochila."
        if (skin := skin_of_item(item.catalog_key)) is not None:
            game = GAMES[skin.game]
            got += (
                f" Ya la llevas puesta en {game.label}; la cambias en la mochila o con "
                "el botón 🎨 de la mesa."
                if game.wearable
                else f" Vale en todas las {game.name.lower()}: te la pongo en la mesa desde "
                "la que la compras o en las que llevaban el de serie, y cada mesa la "
                "cambias con su botón 🎨 o en la mochila."
            )
        return got

    async def _paid_text(
        self,
        item: ShopItem,
        price: Quote,
        sale: Sale,
        buyer: discord.Member,
        balance: int,
        now: float,
    ) -> str:
        """Lo que ve el comprador tras pagar: la factura y qué ha conseguido."""
        lines = [
            "### ✅ ¡Es tuyo!",
            self._got_text(item, [sale], now),
            receipt(
                item,
                price,
                invoice=sale.invoice,
                when=now,
                buyer=buyer.display_name,
                serial=sale.serial,
                edition=sale.edition,
            ),
            igic_line(price),
            f"{CURRENCY_EMOJI} Saldo: **{format_amount(balance)}**",
        ]
        if hint := await renta.hint(self.bot, buyer.guild.id, buyer.id, Moment(Event.BUY)):
            lines.append(hint)
        return "\n".join(lines)

    async def cart_paid_text(
        self, bought: Sequence[Bought], balance: int, buyer: discord.Member, now: float
    ) -> str:
        """Lo que ve el comprador tras pagar una ficha o un carrito: lo conseguido, la
        factura con el impuesto desglosado y el saldo."""
        delivered = [b for b in bought if b.refund is None]
        units = sum(len(b.sales) for b in delivered)
        title = "### ✅ ¡Es tuyo!" if units == 1 else "### ✅ ¡Todo tuyo!"
        lines = [title if delivered else "### ❌ No se ha podido entregar"]
        notes = [f"- {self._got_text(b.item, b.sales, now)}" for b in delivered]
        for b in bought:
            if b.refund is None:
                continue
            what = f"{b.item.emoji} {b.item.name}"
            if b.refund_failed:
                notes.append(
                    f"- ⚠️ {what}: {b.refund} Y encima no he podido devolverte el dinero: "
                    f"avisa a un admin con el ticket nº {b.sales[0].invoice}."
                )
            else:
                notes.append(
                    f"- ⚠️ {what}: {b.refund} Te he devuelto los {format_amount(b.line.total)} "
                    "(IGIC incluido)."
                )
        # Con muchas líneas no cabría con la factura en un mensaje (4.000 caracteres).
        lines.append(_clip("\n".join(notes), 1200))
        if delivered:
            lines.append(
                cart_receipt(
                    [b.line for b in delivered],
                    invoices=[s.invoice for b in delivered for s in b.sales],
                    when=now,
                    buyer=buyer.display_name,
                    serials={
                        b.item.id: [s.serial for s in b.sales if s.serial is not None]
                        for b in delivered
                    },
                    editions={
                        b.item.id: edition
                        for b in delivered
                        if (edition := b.sales[0].edition) is not None
                    },
                )
            )
            if units == 1:
                lines.append(igic_line(delivered[0].line.price))
            else:
                tax = sum(b.line.tax for b in delivered)
                lines.append(
                    f"-# 🐶 {TAX_COLLECTOR} se lleva {format_amount(tax)} en impuestos: el "
                    "desglose por tipo va en la factura."
                )
        lines.append(f"{CURRENCY_EMOJI} Saldo: **{format_amount(balance)}**")
        if hint := await renta.hint(self.bot, buyer.guild.id, buyer.id, Moment(Event.BUY)):
            lines.append(hint)
        return "\n".join(lines)

    async def after_purchase(
        self,
        interaction: discord.Interaction,
        view: Checkout,
        paid: tuple[ShopItem, Sale, int],
        *,
        announce: bool = True,
    ) -> None:
        """Lo de `after_cart` para la compra de una caja de un solo artículo."""
        item, sale, balance = paid
        await self.after_cart(
            interaction,
            view.guild,
            view.buyer,
            [Bought(TicketLine(item, 1, view.price), [sale])],
            balance,
            announce=announce,
            skin_game=view.skin_game,
        )

    async def after_cart(
        self,
        interaction: discord.Interaction,
        guild: discord.Guild,
        buyer: discord.Member,
        bought: Sequence[Bought],
        balance: int,
        *,
        announce: bool = True,
        skin_game: str | None = None,
    ) -> None:
        """Después de enseñar la factura: skins y título puestos, aviso público, Renta,
        mascotas, lista de deseos y logros.

        Args:
            announce: `False` se salta el aviso público (compras desde otro panel).
            skin_game: La mesa desde cuyo 🎨 se compró una skin, para ponérsela ahí.
        """
        delivered = [b for b in bought if b.refund is None]
        if not delivered:
            return
        for entry in delivered:
            if (skin := skin_of_item(entry.item.catalog_key)) is not None:
                for game in self.wear_on_purchase(guild.id, buyer.id, skin, skin_game):
                    await self.wear_skin(guild.id, buyer.id, game, skin)
        titles = [b.item for b in delivered if is_title(b.item.catalog_key)]
        if titles:
            # Comprar un título te lo pone, antes de anunciarlo; con varios, el último.
            await self.wear_title(guild.id, buyer.id, titles[-1].catalog_key)
        if announce:
            try:
                await interaction.followup.send(
                    self._announcement(buyer, delivered),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                logger.debug("No se pudo anunciar una compra", exc_info=True)
        # Comprar gasta dinero: gancho de la Renta (ver Biblia.txt, sección 4).
        await renta.remind(self.bot, interaction)
        for entry in delivered:
            if entry.item.kind is Kind.PET:
                await mascotas.adopted(self.bot, guild.id, buyer, interaction.channel, entry.item)
        wished = {w.item_id for w in await self.repository.wishlist(guild.id, buyer.id)}
        got_wished = [b for b in delivered if b.item.id in wished]
        await self.repository.remove_wishes(guild.id, buyer.id, {b.item.id for b in got_wished})
        await logros.track(
            self.bot,
            guild.id,
            buyer,
            interaction.channel,
            self._purchase_delta(guild.id, buyer.id, delivered, balance, got_wished),
        )

    def _announcement(self, buyer: discord.abc.User, delivered: Sequence[Bought]) -> str:
        """El aviso público de una compra; el de siempre si es una sola unidad."""
        total = sum(b.line.total for b in delivered)
        tax = sum(b.line.tax for b in delivered)
        who = f"**{_name(buyer)}**"
        if len(delivered) == 1 and len(delivered[0].sales) == 1:
            item, sale = delivered[0].item, delivered[0].sales[0]
            if is_title(item.catalog_key):
                pool = ANNOUNCE_TITLE
            elif use_of(item.catalog_key):
                pool = ANNOUNCE_USABLE
            else:
                pool = ANNOUNCE_LINES[item.kind]
            line = self.rng.choice(pool).format(who=who, item=f"{item.emoji} **{item.name}**")
            extra = f" (nº {sale.serial} de {sale.edition})" if sale.serial is not None else ""
            header = f"{self.rng.choice(ANNOUNCE_BIG)}\n" if total >= BIG_PURCHASE else ""
            return (
                f"{header}🛍️ {line}{extra}\n-# Pagó {format_amount(total)}, "
                f"de los que {format_amount(tax)} son IGIC para {TAX_COLLECTOR}."
            )
        names = [
            f"{b.item.emoji} **{b.item.name}**" + (f" ×{len(b.sales)}" if len(b.sales) > 1 else "")
            for b in delivered
        ]
        shown = ", ".join(names[:8]) + (f" y {len(names) - 8} más" if len(names) > 8 else "")
        count = sum(len(b.sales) for b in delivered)
        line = self.rng.choice(ANNOUNCE_CART).format(who=who, items=shown, count=count)
        header = f"{self.rng.choice(ANNOUNCE_BIG)}\n" if total >= BIG_PURCHASE else ""
        return (
            f"{header}🛒 {line}\n-# Pagó {format_amount(total)}, de los que "
            f"{format_amount(tax)} son impuestos para {TAX_COLLECTOR}."
        )

    def _purchase_delta(
        self,
        guild_id: int,
        user_id: int,
        delivered: Sequence[Bought],
        balance: int,
        got_wished: Sequence[Bought],
    ) -> StatDelta:
        """Los logros de una compra: los de siempre por unidad y los del carrito entero."""
        now = self.clock()
        queued = len(self.queued_boosts(guild_id, user_id, now))
        delta = StatDelta()
        for entry in delivered:
            item, price = entry.item, entry.line.price
            for sale in entry.sales:
                delta.merge(
                    shop_stats(
                        kind=item.kind.value,
                        total=price.total,
                        tax=price.tax,
                        discount_pct=price.discount_pct,
                        luxury=price.rate.key == "lujo",
                        serial=sale.serial,
                        last_unit=sale.last_unit,
                        renewed=sale.renewed,
                        collection=sale.collection,
                        queued_boosts=queued,
                        balance_after=balance,
                        catalog_key=item.catalog_key,
                        aisle=aisle_of(item.catalog_key).key,
                        title=is_title(item.catalog_key),
                        skin=skin_of_item(item.catalog_key) is not None,
                    )
                )
        if "shop_broke_buy" in delta.add:
            # Quedarse a cero cuenta una vez por compra, no una por unidad.
            delta.add["shop_broke_buy"] = 1
        delta.merge(
            shop_cart_stats(
                lines=len(delivered),
                units=sum(len(b.sales) for b in delivered),
                rates=len({b.line.price.rate.key for b in delivered}),
                same_max=max(len(b.sales) for b in delivered),
                total=sum(b.line.total for b in delivered),
                wished=len(got_wished),
                wished_on_sale=sum(1 for b in got_wished if b.line.price.discount_pct),
                when=local_now(now),
            )
        )
        return delta

    # -- Carrito ---------------------------------------------------------------------

    def cart(self, guild_id: int, user_id: int) -> dict[int, int]:
        """Lo que lleva un miembro en el carrito: artículo → unidades (una copia)."""
        return dict(self.carts.get((guild_id, user_id), {}))

    def set_cart(self, guild_id: int, user_id: int, lines: Mapping[int, int]) -> None:
        """Cambia el carrito entero (sin líneas, lo vacía)."""
        key = (guild_id, user_id)
        kept = {item_id: qty for item_id, qty in lines.items() if qty > 0}
        if kept:
            self.carts[key] = kept
            self.cart_touched[key] = self.clock()
        else:
            self.carts.pop(key, None)
            self.cart_touched.pop(key, None)

    def put_in_cart(
        self, guild_id: int, user_id: int, item_id: int, qty: int, *, add: bool = False
    ) -> str | None:
        """Deja `qty` unidades de un artículo en el carrito (o las suma con `add`).

        Returns:
            Por qué no cabe, o `None`.
        """
        cart = self.cart(guild_id, user_id)
        if item_id not in cart and len(cart) >= MAX_CART_LINES:
            return (
                f"En el carrito caben {MAX_CART_LINES} artículos distintos. "
                "Paga o quita alguno antes."
            )
        cart[item_id] = min(MAX_QUANTITY, (cart.get(item_id, 0) if add else 0) + qty)
        self.set_cart(guild_id, user_id, cart)
        return None

    def prune_carts(self, now: float) -> None:
        """Olvida los carritos que nadie toca desde hace `CART_TTL`."""
        for key, touched in list(self.cart_touched.items()):
            if now - touched > CART_TTL:
                self.carts.pop(key, None)
                self.cart_touched.pop(key, None)

    # -- Lista de deseos -------------------------------------------------------------

    def wish_marks(self, item: ShopItem, now: float, balance: int) -> frozenset[str]:
        """Lo que se sabe ahora de un artículo deseado (`shop_wishlist.wish_state`)."""
        return wish_state(
            item,
            now=now,
            balance=balance,
            seasonal=season_name(item) is not None,
            selling=on_sale(item, local_now(now)),
        )

    async def toggle_wish(
        self, guild_id: int, member: discord.abc.User, item: ShopItem, channel: object
    ) -> tuple[bool, str | None]:
        """Apunta un artículo en la lista de deseos, o lo quita si ya estaba.

        Returns:
            `(si está ahora en la lista, por qué no se pudo apuntar)`.
        """
        wishes = await self.repository.wishlist(guild_id, member.id)
        if any(w.item_id == item.id for w in wishes):
            await self.repository.remove_wishes(guild_id, member.id, [item.id])
            return False, None
        if len(wishes) >= MAX_WISHES:
            return False, f"Tu lista ya tiene {MAX_WISHES} cosas. Quita alguna antes."
        now = self.clock()
        balance = await self.economy.balance(guild_id, member.id)
        await self.repository.add_wish(
            guild_id, member.id, item.id, now, self.wish_marks(item, now, balance)
        )
        await logros.track(
            self.bot, guild_id, member, channel, shop_wish_stats(added=1, size=len(wishes) + 1)
        )
        return True, None

    async def wishlist_embed(self, guild: discord.Guild, member: discord.abc.User) -> discord.Embed:
        """Sección ⭐ Deseos de `perfil`: la lista de alguien, que es pública."""
        now = self.clock()
        wishes = await self.repository.wishlist(guild.id, member.id)
        catalog = {i.id: i for i in await self.repository.items(guild.id, include_hidden=True)}
        items = [catalog[w.item_id] for w in wishes if w.item_id in catalog]
        if items:
            total = sum(quote(item, now).total for item in items)
            text = (
                _clip("\n".join(compact_line(item, now) for item in items), 3600)
                + f"\n\n**Todo junto:** {format_amount(total)} con IGIC."
            )
        else:
            text = "Nada por ahora. Se apunta en la `tienda`: abre la ficha de algo y pulsa ⭐."
        embed = discord.Embed(
            title=f"⭐ Lista de deseos de {_name(member)}", description=text, color=COLOR
        )
        embed.set_footer(text=f"Caben {MAX_WISHES} cosas. Comprar algo lo quita de la lista.")
        return embed

    async def check_wishes(self) -> int:
        """Mira todas las listas de deseos y avisa por privado de lo que ha cambiado.

        Guarda lo visto aunque el aviso no llegue (privados cerrados o silenciados):
        así no se repite al volver a activarlos.

        Returns:
            A cuántos miembros se ha avisado.
        """
        wishes = await self.repository.all_wishes()
        if not wishes:
            return 0
        muted = await self.repository.muted()
        now = self.clock()
        catalogs: dict[int, dict[int, ShopItem]] = {}
        balances: dict[tuple[int, int], int] = {}
        changes = []
        news: dict[tuple[int, int], list[str]] = defaultdict(list)
        for wish in wishes:
            if wish.guild_id not in catalogs:
                items = await self.repository.items(wish.guild_id, include_hidden=True)
                catalogs[wish.guild_id] = {item.id: item for item in items}
            item = catalogs[wish.guild_id].get(wish.item_id)
            if item is None:
                continue
            key = (wish.guild_id, wish.user_id)
            if key not in balances:
                balances[key] = await self.economy.balance(*key)
            marks = self.wish_marks(item, now, balances[key])
            stored = stored_seen(wish.seen, marks)
            if stored != wish.seen:
                changes.append((wish, stored))
            if key in muted or not item.visible:
                continue
            news[key] += [
                news_line(kind, item, now, season=season_name(item))
                for kind in wish_news(wish.seen, marks)
            ]
        await self.repository.set_wish_seen(changes)
        told = 0
        for (guild_id, user_id), lines in news.items():
            guild = self.bot.get_guild(guild_id)
            if not lines or guild is None:
                continue
            if await self.whisper(guild_id, user_id, news_message(guild.name, lines)):
                told += 1
                logros.note(self.bot, guild_id, user_id, shop_wish_alert_stats(alerts=len(lines)))
        return told

    async def alert_stall(self, guild_id: int, listing: Listing) -> int:
        """Avisa por privado a quien desea lo que se acaba de poner en el Rastro.

        Returns:
            A cuántos miembros se ha avisado.
        """
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return 0
        muted = await self.repository.muted()
        seller = guild.get_member(listing.seller_id)
        text = stall_message(
            guild.name,
            f"**{listing.label}**",
            seller=f"**{_name(seller)}**" if seller else "alguien",
            price=listing.start_price,
            closes_at=listing.expires_at,
        )
        told = 0
        for user_id in await self.repository.wishers(guild_id, listing.item_id):
            if user_id == listing.seller_id or (guild_id, user_id) in muted:
                continue
            if await self.whisper(guild_id, user_id, text):
                told += 1
                logros.note(self.bot, guild_id, user_id, shop_wish_alert_stats(stalls=1))
        return told

    async def whisper(self, guild_id: int, user_id: int, text: str) -> bool:
        """Manda un mensaje privado a un miembro. `False` si no se puede (privados cerrados).

        Es el único sitio del bot que escribe por privado: solo para los avisos de la
        lista de deseos, que cada uno pide al apuntar algo y puede silenciar.
        """
        guild = self.bot.get_guild(guild_id)
        member = guild.get_member(user_id) if guild else None
        if member is None or member.bot:
            return False
        try:
            await member.send(text, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            logger.info("No se pudo mandar el aviso de la lista de deseos a %s", user_id)
            return False
        return True

    def spawn(self, coroutine: Awaitable[object]) -> None:
        """Lanza un aviso aparte, sin hacer esperar a quien lo provoca."""

        async def run() -> None:
            try:
                await coroutine
            except Exception:
                logger.exception("Error mandando un aviso de la lista de deseos")

        task = asyncio.get_running_loop().create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # -- Caducidad -------------------------------------------------------------------

    @tasks.loop(minutes=EXPIRY_MINUTES)
    async def _expire(self) -> None:
        """Quita los roles alquilados que han vencido y olvida los carritos abandonados."""
        self.prune_carts(self.clock())
        try:
            await self.expire_rentals()
        except Exception:
            # Una excepción sin capturar pararía la tarea para siempre.
            logger.exception("Error quitando roles alquilados vencidos")

    @_expire.before_loop
    async def _before_expire(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=WISH_MINUTES)
    async def _watch_wishes(self) -> None:
        """Mira si algo de las listas de deseos merece un aviso por privado."""
        try:
            await self.check_wishes()
        except Exception:
            # Una excepción sin capturar pararía la tarea para siempre.
            logger.exception("Error mirando las listas de deseos")

    @_watch_wishes.before_loop
    async def _before_watch_wishes(self) -> None:
        await self.bot.wait_until_ready()

    async def expire_rentals(self) -> int:
        """Quita los alquileres vencidos y los marca. Devuelve cuántos ha cerrado.

        Si el miembro sigue teniendo el mismo rol por otra compra en vigor (uno
        para siempre puesto, u otro alquiler), no se le quita.
        """
        now = self.clock()
        due = await self.repository.due_rentals(now)
        for _entry_id, guild_id, user_id, role_id in due:
            guild = self.bot.get_guild(guild_id)
            member = guild.get_member(user_id) if guild else None
            role = guild.get_role(role_id or 0) if guild else None
            if guild is None or member is None or role is None or role not in member.roles:
                continue
            others = await self.repository.inventory(guild_id, user_id, now)
            if any(
                e.role_id == role_id and (e.expires_at is not None or e.equipped) for e in others
            ):
                continue
            try:
                await member.remove_roles(role, reason="Alquiler de la tienda vencido")
            except discord.HTTPException:
                logger.info("No se pudo quitar el rol alquilado %s a %s", role_id, user_id)
        await self.repository.mark_expired([entry_id for entry_id, *_ in due])
        return len(due)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra la tienda del servidor que el bot abandona."""
        await self.repository.delete_guild_data(guild.id)
        for key in [k for k in self.boosts if k[0] == guild.id]:
            del self.boosts[key]
        for key in [k for k in self.titles if k[0] == guild.id]:
            del self.titles[key]
        for skin_key in [k for k in self.skins if k[0] == guild.id]:
            del self.skins[skin_key]
        for image_key in [k for k in self.skin_images if k[0] == guild.id]:
            del self.skin_images[image_key]
        for cart_key in [k for k in self.carts if k[0] == guild.id]:
            self.set_cart(*cart_key, {})
        for prefs_key in [k for k in self.shop_prefs if k[0] == guild.id]:
            del self.shop_prefs[prefs_key]

    # -- Comandos --------------------------------------------------------------------

    @app_commands.command(
        name="tienda",
        description="Abre El Colmado de Jovani: busca, llena el carrito y apunta deseos.",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        buscar="Un artículo o parte de su nombre (busca también en la descripción)."
    )
    async def tienda(self, interaction: discord.Interaction, buscar: str | None = None) -> None:
        """Enseña el escaparate en el canal; la ficha y el carrito se abren en privado."""
        assert interaction.guild is not None  # guild_only
        # El escaparate se lee de la base de datos: «pensando…» mientras tanto.
        await interaction.response.defer(thinking=True)
        view = await self.storefront(interaction.guild, interaction.user, search=buscar)
        await edit(interaction, view=view, allowed_mentions=discord.AllowedMentions.none())
        view.interaction = interaction
        if buscar:
            logros.note(
                self.bot, interaction.guild.id, interaction.user.id, shop_browse_stats(searches=1)
            )

    @tienda.autocomplete("buscar")
    async def _buscar_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild is None:
            return []
        needle = fold(current)
        when = local_now(self.clock())
        return [
            app_commands.Choice(name=item.name[:100], value=item.name[:100])
            for item in await self.repository.items(interaction.guild.id)
            if needle in fold(item.name) and on_sale(item, when)
        ][:25]

    @commands.command(name="tienda")
    @commands.guild_only()
    async def tienda_text(self, ctx: commands.Context, *, buscar: str | None = None) -> None:
        """Versión de texto de `tienda`: `.tienda` o `.tienda plátano`."""
        assert ctx.guild is not None  # guild_only
        view = await self.storefront(ctx.guild, ctx.author, search=buscar)
        view.message = await ctx.send(view=view, allowed_mentions=discord.AllowedMentions.none())
        if buscar:
            logros.note(self.bot, ctx.guild.id, ctx.author.id, shop_browse_stats(searches=1))

    # -- Trastienda (la abre el comando `catalogo` del cog Admin) ---------------------

    async def open_admin_panel(
        self,
        *,
        interaction: discord.Interaction | None = None,
        ctx: commands.Context | None = None,
    ) -> None:
        """Abre la trastienda para un administrador (efímera con `/`)."""
        from bot.cogs.shop_admin import AdminPanel  # evita el import circular

        guild = interaction.guild if interaction else ctx.guild if ctx else None
        owner = interaction.user if interaction else ctx.author if ctx else None
        if guild is None or owner is None:
            return
        if interaction is not None:
            # Reponer y cargar el catálogo tardan: «pensando…» privado mientras tanto.
            await ack(interaction, new_message=True)
        await self.stock_up(guild.id)
        panel = AdminPanel(self, guild, owner)
        await panel.load()
        if interaction is not None:
            await edit(interaction, view=panel, allowed_mentions=discord.AllowedMentions.none())
            panel.interaction = interaction

        elif ctx is not None:
            panel.message = await ctx.send(
                view=panel, allowed_mentions=discord.AllowedMentions.none()
            )


# -- Puntos de entrada para otros cogs ------------------------------------------------


def xp_multiplier(bot: commands.Bot, guild_id: int, user_id: int, now: float) -> float:
    """Multiplicador de XP por potenciadores; 1,0 si la tienda no está cargada."""
    if (cog := find_cog(bot, Tienda)) is not None:
        return cog.xp_multiplier(guild_id, user_id, now)
    return 1.0


def title_of(bot: commands.Bot, guild_id: int, user_id: int) -> str | None:
    """Título que luce un miembro (`🏰 Marquesado de Mogán`), o `None`.

    Lo usan `perfil`, `fortunas` y `ranking`. Sale de memoria: no toca la base
    de datos, así que se puede llamar por cada nombre de una lista.
    """
    if (cog := find_cog(bot, Tienda)) is not None:
        return cog.title(guild_id, user_id)
    return None


def skin_of(bot: commands.Bot, guild_id: int, user_id: int, game: str) -> Skin | None:
    """La skin que lleva un miembro en `game`, o `None` (la de serie). De memoria."""
    if (cog := find_cog(bot, Tienda)) is not None:
        return cog.skin(guild_id, user_id, game)
    return None


def skin_params(bot: commands.Bot, guild_id: int, user_id: int, game: str) -> dict[str, object]:
    """Lo que la escena de `game` tiene que pintar para este miembro.

    Los parámetros de serie con los de su skin encima (`skins.params`), y su
    imagen si la skin es propia. Si la tienda no está cargada, los de serie: el
    juego nunca se queda sin dibujo.
    """
    if (cog := find_cog(bot, Tienda)) is not None:
        return cog.scene_params(guild_id, user_id, game)
    return skin_params_of(game, None)


async def refresh_worn(bot: commands.Bot, guild_id: int, user_id: int) -> None:
    """Puente de `Tienda.refresh_worn`; no hace nada si la tienda no está cargada."""
    if (cog := find_cog(bot, Tienda)) is not None:
        await cog.refresh_worn(guild_id, user_id)


async def grant_reward_skins(
    bot: commands.Bot, guild_id: int, user_id: int, achievement_ids: list[str]
) -> list[Skin | Outfit]:
    """Da las skins y la ropa de mascota que regalan estos logros recién desbloqueados."""
    if (cog := find_cog(bot, Tienda)) is None:
        return []
    try:
        given: list[Skin | Outfit] = []
        given += await cog.sync_reward_skins(guild_id, user_id, achievement_ids)
        given += await cog.sync_reward_outfits(guild_id, user_id, achievement_ids)
        return given
    except Exception:
        logger.exception("No se pudieron dar las skins de logros a %s", user_id)
        return []


async def grant_free_skins(bot: commands.Bot, guild_id: int, user_id: int, game: str) -> None:
    """Da las skins gratis de `game` que le falten (`Tienda.grant_free_skins`).

    No lanza: si la tienda no está o falla, el juego sigue con el gallo de serie.
    """
    if (cog := find_cog(bot, Tienda)) is None:
        return
    try:
        await cog.grant_free_skins(guild_id, user_id, game)
    except Exception:
        logger.exception("No se pudieron dar las skins gratis de %s a %s", game, user_id)


async def grant_box(
    bot: commands.Bot,
    guild_id: int,
    user_id: int,
    box_key: str,
    *,
    because: str,
    count: int = 1,
    pending: bool = False,
) -> str | None:
    """Regala una caja ganada jugando (`Tienda.grant_free_box`) y devuelve la línea de aviso.

    `apuestas` da una de cartón cada 100 jugadas y `message_stats`, una de regalo cada 5
    niveles. Con `pending`, el aviso espera a `notice` (el siguiente resultado con dinero
    de ese miembro) en vez de devolverse. No lanza: si la tienda no está o falla, solo se
    pierde la caja, y queda en el log.
    """
    if (cog := find_cog(bot, Tienda)) is None:
        return None
    try:
        line = await cog.grant_free_box(guild_id, user_id, box_key, count=count, because=because)
    except Exception:
        logger.exception("No se pudo dar la caja %s a %s", box_key, user_id)
        return None
    if line is not None and pending:
        cog.notices[(guild_id, user_id)].append(line)
        return None
    return line


def notice(bot: commands.Bot, guild_id: int, user_id: int) -> str | None:
    """El aviso pendiente de cajas ganadas, una sola vez; lo añade `renta.hint`."""
    if (cog := find_cog(bot, Tienda)) is None:
        return None
    return cog.notices_for(guild_id, user_id)


async def consume_key(bot: commands.Bot, guild_id: int, user_id: int, catalog_key: str) -> bool:
    """Gasta una unidad de un objeto de serie; `False` si no lo tiene o la tienda no está.

    `pala` lo usa para dar los regalos para la familia.
    """
    if (cog := find_cog(bot, Tienda)) is None:
        return False
    return await cog.consume_key(guild_id, user_id, catalog_key)


async def open_aisle(
    bot: commands.Bot, interaction: discord.Interaction, aisle: str | None, *, tab: str = "todo"
) -> bool:
    """Rellena una respuesta ya aceptada (`ack`) con el escaparate abierto en `aisle`.

    Es el atajo 🎁 de `pala` al pasillo de la familia y el de 🐾 Adoptar y 🥣 Comida
    del panel de `mascota`. `aisle` `None` son todos los pasillos; `tab`, la
    pestaña (`TABS`). `False` si la tienda no está cargada (y no se toca la respuesta).
    """
    if (cog := find_cog(bot, Tienda)) is None or interaction.guild is None:
        return False
    view = await cog.storefront(interaction.guild, interaction.user)
    view.prefs.aisle, view.prefs.tab, view.page = aisle, tab, 0
    view.rebuild()
    await edit(interaction, view=view, allowed_mentions=discord.AllowedMentions.none())
    view.interaction = interaction
    return True


async def prepare_checkout(
    bot: commands.Bot,
    interaction: discord.Interaction,
    catalog_key: str,
    *,
    hidden: bool = False,
) -> Checkout | None:
    """Caja del artículo de serie `catalog_key`, sin enseñarla, para pagarla desde otro panel.

    `pala` la usa para comprar y dar un regalo sin salir del panel, y `mascota` para la
    ropa y las razas. Si no se puede comprar (nivel, existencias, temporada), la caja
    viene con `failed` y el motivo en `result`. `None` si la tienda no está cargada o
    no lo vende.

    Args:
        hidden: Si vale un artículo oculto del escaparate: las razas de las mascotas
            solo se compran desde su panel.
    """
    cog = find_cog(bot, Tienda)
    guild, buyer = interaction.guild, interaction.user
    if cog is None or guild is None or not isinstance(buyer, discord.Member):
        return None
    await cog.stock_up(guild.id)
    item = await cog.repository.item_by_key(guild.id, catalog_key)
    if item is None or not (item.visible or (hidden and is_breed(item.catalog_key))):
        return None
    now = cog.clock()
    error, note = await cog._precheck(guild, buyer, item, now)
    balance = await cog.economy.balance(guild.id, buyer.id)
    view = Checkout(cog, guild, buyer, item, quote(item, now), balance=balance, note=note)
    view.stop()  # no se enseña: solo guarda el precio y el comprador
    if error is not None:
        view.failed, view.result = True, f"❌ {error}"
    return view


async def pay_checkout(
    view: Checkout, interaction: discord.Interaction
) -> tuple[ShopItem, Sale, int] | None:
    """Cobra una caja de `prepare_checkout`; `None` y el motivo en `view.result` si no."""
    return await view.cog.complete_purchase(view, interaction)


async def after_checkout(
    view: Checkout, interaction: discord.Interaction, paid: tuple[ShopItem, Sale, int]
) -> None:
    """Renta y logros de una compra de `pay_checkout`, sin anunciarla en el canal."""
    await view.cog.after_purchase(interaction, view, paid, announce=False)


def wish_listed(bot: commands.Bot, guild_id: int, listing: Listing) -> None:
    """El Rastro acaba de montar un puesto: avisa por privado a quien lo desea.

    Va aparte (`Tienda.spawn`) para no hacer esperar a quien vende. No hace nada si la
    tienda no está cargada.
    """
    if (cog := find_cog(bot, Tienda)) is not None:
        cog.spawn(cog.alert_stall(guild_id, listing))


async def owned_keys(bot: commands.Bot, guild_id: int, user_id: int) -> frozenset[str]:
    """Claves de serie de lo que tiene un miembro; vacío si la tienda no está cargada.

    `pala` lo usa para las herramientas de curro. Si la base de datos falla, el
    turno sigue sin herramientas: mejor currar sin casco que no poder fichar.
    """
    if (cog := find_cog(bot, Tienda)) is None:
        return frozenset()
    try:
        return await cog.owned_keys(guild_id, user_id)
    except Exception:
        logger.exception("No se pudo leer la mochila de %s para la pala", user_id)
        return frozenset()


async def inventory(bot: commands.Bot, guild_id: int, user_id: int) -> list[InventoryEntry] | None:
    """Lo que tiene un miembro en vigor (la mochila del Jovaniverso); `None` sin tienda."""
    if (cog := find_cog(bot, Tienda)) is None:
        return None
    return await cog.repository.inventory(guild_id, user_id, cog.clock())


async def give_key(
    bot: commands.Bot, guild_id: int, user_id: int, catalog_key: str
) -> ShopItem | None:
    """Regala un objeto de serie (`Tienda.give_key`); `None` si no se pudo.

    No lanza: si la tienda no está o falla, queda en el log y quien llama lo dice.
    """
    if (cog := find_cog(bot, Tienda)) is None:
        return None
    try:
        return await cog.give_key(guild_id, user_id, catalog_key)
    except Exception:
        logger.exception("No se pudo dar %s a %s", catalog_key, user_id)
        return None


async def give_numbered(
    bot: commands.Bot, guild_id: int, user_id: int, catalog_key: str
) -> tuple[ShopItem, int, int] | None:
    """Regala la siguiente unidad numerada (`Tienda.give_numbered`); `None` si no se pudo."""
    if (cog := find_cog(bot, Tienda)) is None:
        return None
    try:
        return await cog.give_numbered(guild_id, user_id, catalog_key)
    except Exception:
        logger.exception("No se pudo dar el numerado %s a %s", catalog_key, user_id)
        return None


async def units_left(bot: commands.Bot, guild_id: int, keys: Collection[str]) -> dict[str, int]:
    """Unidades que quedan de cada numerado (`Tienda.units_left`); vacío si no se pudo."""
    if (cog := find_cog(bot, Tienda)) is None:
        return {}
    try:
        return await cog.units_left(guild_id, keys)
    except Exception:
        logger.exception("No se pudo leer cuántos quedan de %s", ", ".join(keys))
        return {}


async def grant_ong_gift(
    bot: commands.Bot,
    guild_id: int,
    user_id: int,
    keys: Collection[str],
    *,
    cheap_max: int,
    exclusive: float,
) -> ShopItem | None:
    """Regala un objeto de una ONG o de la tienda (`Tienda.grant_ong_gift`).

    No lanza: si la tienda no está o falla, no hay regalo y queda en el log.
    """
    if (cog := find_cog(bot, Tienda)) is None:
        return None
    try:
        return await cog.grant_ong_gift(
            guild_id, user_id, keys, cheap_max=cheap_max, exclusive=exclusive
        )
    except Exception:
        logger.exception("No se pudo dar un regalo de ONG a %s", user_id)
        return None


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía, la tienda y los niveles del bot."""
    await bot.add_cog(Tienda(bot, bot.economy, bot.shop, levels=bot.message_stats))
