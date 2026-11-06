"""Escaparate de la tienda: la lista compacta, la ficha, el carrito y la lista de deseos.

Son las vistas de `tienda`; las reglas de compra, los datos y los avisos están en
`bot.cogs.shop` (`Tienda`). Discord admite 40 componentes y 4.000 caracteres por
mensaje: con un botón **Comprar** por artículo cabían 5 por página. Aquí cada artículo
es una línea de texto (`compact_line`) y un desplegable abre su ficha, así que caben
`PAGE_SIZE`.

- `Storefront`: el escaparate de quien lo abre, en el canal. Secciones (los tipos de
  `TABS` y ⭐ Mi lista de deseos), pasillo (con cuántos tienes de cada uno), orden,
  filtros (🏷️ ofertas, 🆕 novedades, 💰 lo que te llega, ✅/❌ lo que tienes o no),
  buscador y páginas. Todo eso es memoria y contesta en el acto. Sección, pasillo y
  orden se recuerdan por persona (`Tienda.shop_prefs`). Si otro lo toca, se le abre el
  suyo en privado. El desplegable de artículos y el carrito valen para cualquiera:
  la ficha y el carrito son de quien pulsa.
- `ItemSheet`: la ficha privada de un artículo. Cantidad, ticket con su impuesto,
  **Pagar**, al carrito y ⭐ a la lista de deseos (que es pública).
- `CartPanel`: el carrito privado. Cada línea dice lo que le impide comprarse; el
  ticket va desglosado por tipo y se paga todo o nada (`Tienda.complete_cart`).
- `SearchForm`: el formulario del 🔎.

Los clics siguen «Botones rápidos» de CLAUDE.md: lo que lee la base de datos hace
`ack` antes; lo que solo cambia memoria contesta con `edit_message`. Los `custom_id`
son fijos para el rescate de clics.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import discord
from discord import ui

from bot.cogs import achievements as logros
from bot.cogs import banco_compras as financiar
from bot.cogs import rastro
from bot.cogs.shop import (
    COLOR,
    COLOR_ERROR,
    COLOR_PAID,
    LEGEND,
    TABS,
    VIEW_TIMEOUT,
    Bought,
    _button,
    _clip,
    _close_view,
    _name,
    _select_emoji,
    compact_line,
    financed_purchase,
    fold,
    igic_line,
    in_tab,
    item_card,
    local_now,
    on_sale,
    short_detail,
)
from bot.services.achievements import shop_browse_stats
from bot.services.economy import CURRENCY_EMOJI, InsufficientFundsError, format_amount
from bot.services.shop import (
    BEST_SELLER_MIN,
    MAX_CART_LINES,
    SHOP_NAME,
    Quote,
    ShopError,
    ShopItem,
    TicketLine,
    cart_preview,
    emblematic,
    max_quantity,
    quote,
    rate_label,
)
from bot.services.shop_catalog import AISLE_BY_KEY, AISLES, HOUSE_AISLE, aisle_of
from bot.services.shop_wishlist import MAX_WISHES
from bot.services.taxes import TAX_COLLECTOR
from bot.services.telemetry import TimedLock
from bot.utils.interactions import ack, edit, notify

if TYPE_CHECKING:
    from bot.cogs.shop import Tienda
    from bot.repositories.shop import InventoryEntry

logger = logging.getLogger(__name__)

#: Artículos por página. Cada uno es una línea (~150 caracteres con sus etiquetas) y
#: una opción del desplegable (caben 25): 15 se leen bien también en el móvil.
PAGE_SIZE = 15
#: Sección de la lista de deseos, además de las de `TABS`.
WISH_TAB = "deseos"
#: Órdenes del escaparate: clave y etiqueta. «Recomendado» es el del catálogo (por
#: tipo y de barato a caro) y, en la lista de deseos, el orden en que se apuntó.
SORTS: tuple[tuple[str, str], ...] = (
    ("recomendado", "⭐ Recomendado"),
    ("barato", "💸 Más baratos primero"),
    ("caro", "💰 Más caros primero"),
    ("nuevo", "🆕 Lo último en llegar"),
    ("vendido", "🔥 Lo más vendido"),
    ("nombre", "🔤 Por nombre"),
)
#: El filtro de lo que tienes da la vuelta: sin filtro, lo que tienes, lo que te falta.
OWNED_CYCLE: tuple[str | None, ...] = (None, "tengo", "falta")
_NO_MENTIONS = discord.AllowedMentions.none()


@dataclass
class Prefs:
    """Lo que se recuerda del escaparate de cada miembro: sección, pasillo y orden.

    Los filtros y la búsqueda no: abrir la tienda con un filtro olvidado puesto
    parecería que no hay nada.
    """

    tab: str = "todo"
    aisle: str | None = None
    sort: str = "recomendado"


def sort_items(items: list[ShopItem], key: str, now: float) -> list[ShopItem]:
    """`items` en el orden `key` de `SORTS` (los empates, por antigüedad)."""
    if key == "barato":
        return sorted(items, key=lambda i: (quote(i, now).base, i.id))
    if key == "caro":
        return sorted(items, key=lambda i: (-quote(i, now).base, i.id))
    if key == "nuevo":
        return sorted(items, key=lambda i: (-i.created_at, -i.id))
    if key == "vendido":
        return sorted(items, key=lambda i: (-i.sold, i.id))
    if key == "nombre":
        return sorted(items, key=lambda i: (fold(i.name), i.id))
    return list(items)


def _finance_note(lines: list[TicketLine]) -> list[str]:
    """Qué parte de un carrito mezclado va a plazos y cuál al contado, si se financia."""
    purchase = financed_purchase(lines)
    if purchase is None:
        return []
    hint = financiar.teaser(purchase.amount, purchase.category)
    if not purchase.cash:
        return [f"-# {hint}."] if hint else []
    return [
        f"-# {financiar.LABEL}: {format_amount(purchase.amount)} a plazos y "
        f"{format_amount(purchase.cash)} al contado (lo numerado y los títulos no se financian)."
        + (f" {hint}." if hint else "")
    ]


def _money_line(total: int, balance: int) -> str:
    """Lo que te queda al pagar, o lo que te falta."""
    missing = total - balance
    if missing > 0:
        return (
            f"😬 Tienes {format_amount(balance)}: te faltan **{format_amount(missing)}**. "
            "Pásate por el casino o cobra el `imv`."
        )
    return (
        f"{CURRENCY_EMOJI} Tienes {format_amount(balance)} → te quedan "
        f"**{format_amount(balance - total)}**"
    )


def _stock_note(item: ShopItem) -> str:
    """Lo que queda de una edición limitada, para el desplegable; vacío si no lo es."""
    if item.remaining is None:
        return ""
    return " · agotado" if item.sold_out else f" · quedan {item.remaining} de {item.stock}"


def _tax_note(lines: Sequence[TicketLine]) -> str:
    """La mordida del impuesto: la de siempre con una unidad; si no, el total."""
    if len(lines) == 1 and lines[0].qty == 1:
        return igic_line(lines[0].price)
    tax = sum(line.tax for line in lines)
    rates = {line.price.rate.key: line.price.rate for line in lines}
    if len(rates) == 1:
        (rate,) = rates.values()
        kind = f"de {rate.tax} ({rate_label(rate.rate)})"
    else:
        kind = "en impuestos, desglosados por tipo en el ticket"
    return f"-# 🐶 {TAX_COLLECTOR} se lleva {format_amount(tax)} {kind}."


# -- Escaparate -----------------------------------------------------------------------


class Storefront(ui.LayoutView):
    """El escaparate (ver el módulo). Lo mueve su dueño; el desplegable de artículos y
    los botones del carrito y la mochila valen para cualquiera."""

    def __init__(
        self,
        cog: Tienda,
        guild: discord.Guild,
        owner: discord.abc.User,
        *,
        search: str | None = None,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild = guild
        self.owner = owner
        saved = cog.shop_prefs.get((guild.id, owner.id))
        self.prefs = replace(saved) if saved else Prefs()
        self.search = (search or "").strip()[:40] or None
        self.offers = False
        self.new = False
        self.affordable = False
        self.owned_filter: str | None = None
        self.page = 0
        #: Todo el catálogo del servidor por id, ocultos incluidos (la lista de deseos
        #: puede tener algo que ya no se vende).
        self.catalog: dict[int, ShopItem] = {}
        self.items_all: list[ShopItem] = []
        self.best_seller: int | None = None
        self.balance = 0
        #: Cuántos tiene el dueño de cada artículo en vigor, y cuántos compró.
        self.owned: Counter[int] = Counter()
        self.bought: Counter[int] = Counter()
        self.wishes: list[int] = []
        self.muted = False
        self.message: discord.Message | None = None
        self.interaction: discord.Interaction | None = None

    async def load(self) -> None:
        """Lee el catálogo, el saldo, la mochila, las compras y la lista del dueño."""
        repository = self.cog.repository
        everything = await repository.items(self.guild.id, include_hidden=True)
        self.catalog = {item.id: item for item in everything}
        self.items_all = [item for item in everything if item.visible]
        sellers = [item for item in self.items_all if item.sold >= BEST_SELLER_MIN]
        self.best_seller = min(sellers, key=lambda i: (-i.sold, i.id)).id if sellers else None
        self.balance = await self.cog.economy.balance(self.guild.id, self.owner.id)
        entries = await repository.inventory(self.guild.id, self.owner.id, self.cog.clock())
        self.owned = Counter(entry.item_id for entry in entries)
        self.bought = await repository.purchase_counts(self.guild.id, self.owner.id)
        self.wishes = [w.item_id for w in await repository.wishlist(self.guild.id, self.owner.id)]
        self.muted = (self.guild.id, self.owner.id) in await repository.muted()
        self.rebuild()

    # -- Qué se ve -------------------------------------------------------------------

    def _base(self) -> list[ShopItem]:
        """Lo de la sección elegida, antes del pasillo y los filtros."""
        if self.prefs.tab == WISH_TAB:
            return [self.catalog[i] for i in self.wishes if i in self.catalog]
        when = local_now(self.cog.clock())
        return [i for i in self.items_all if in_tab(i, self.prefs.tab) and on_sale(i, when)]

    @property
    def shown(self) -> list[ShopItem]:
        """Artículos de la sección, el pasillo, la búsqueda y los filtros, en su orden."""
        now = self.cog.clock()
        items = self._base()
        if self.prefs.aisle:
            items = [i for i in items if aisle_of(i.catalog_key).key == self.prefs.aisle]
        if self.search:
            needle = fold(self.search)
            items = [i for i in items if needle in fold(i.name) or needle in fold(i.description)]
        if self.offers:
            items = [i for i in items if i.on_sale(now)]
        if self.new:
            items = [i for i in items if i.is_new(now)]
        if self.affordable:
            items = [i for i in items if not i.sold_out and quote(i, now).total <= self.balance]
        if self.owned_filter == "tengo":
            items = [i for i in items if self.owned[i.id]]
        elif self.owned_filter == "falta":
            items = [i for i in items if not self.owned[i.id]]
        return sort_items(items, self.prefs.sort, now)

    @property
    def pages(self) -> int:
        return max(1, -(-len(self.shown) // PAGE_SIZE))

    def _maxed(self, item: ShopItem) -> bool:
        return item.per_user is not None and self.bought[item.id] >= item.per_user

    def rebuild(self) -> None:
        """Monta los componentes con el estado actual."""
        self.clear_items()
        now = self.cog.clock()
        shown = self.shown
        pages = max(1, -(-len(shown) // PAGE_SIZE))
        self.page = max(0, min(self.page, pages - 1))
        page_items = shown[self.page * PAGE_SIZE : (self.page + 1) * PAGE_SIZE]
        cart = self.cog.cart(self.guild.id, self.owner.id)
        container = ui.Container(accent_colour=COLOR)
        container.add_item(ui.TextDisplay(self._header(shown, pages, cart)))
        container.add_item(ui.Separator())
        if page_items:
            lines = [
                compact_line(
                    item,
                    now,
                    owned=self.owned[item.id],
                    maxed=self._maxed(item),
                    wished=item.id in self.wishes,
                    in_cart=cart.get(item.id, 0),
                    best_seller=item.id == self.best_seller,
                )
                for item in page_items
            ]
            container.add_item(ui.TextDisplay(_clip("\n".join(lines), 3000) + f"\n-# {LEGEND}"))
            row: ui.ActionRow = ui.ActionRow()
            row.add_item(self._item_select(page_items, now))
            container.add_item(row)
        else:
            container.add_item(ui.TextDisplay(self._empty_text()))
        container.add_item(self._nav_row(pages, cart))
        container.add_item(self._filter_row())
        if self.prefs.tab == WISH_TAB:
            container.add_item(self._wish_row())
        container.add_item(ui.Separator())
        for select in (self._tab_select(), self._aisle_select(), self._sort_select()):
            if select is not None:
                select_row: ui.ActionRow = ui.ActionRow()
                select_row.add_item(select)
                container.add_item(select_row)
        self.add_item(container)

    def _header(self, shown: list[ShopItem], pages: int, cart: dict[int, int]) -> str:
        where = ""
        if self.prefs.tab == WISH_TAB:
            where = " · ⭐ Lista de deseos"
        elif self.prefs.aisle and (aisle := AISLE_BY_KEY.get(self.prefs.aisle)) is not None:
            where = f" · {aisle.label}"
        lines = [f"# 🛒 {SHOP_NAME}{where}"]
        if self.prefs.tab == WISH_TAB:
            lines.append(self._wish_summary())
        else:
            lines.append(
                f"¡Bienvenido, {_name(self.owner)}! Aquí se gasta lo que te dan el casino y "
                "el IMV. Elige algo en el desplegable para ver su ficha, o varias cosas para "
                f"echarlas al carrito. Los precios van sin IGIC: {TAX_COLLECTOR} cobra en caja."
            )
            lines.append(
                "💳 Lo que lleva 💳 se paga a plazos desde la caja o el carrito. "
                f"Esta semana, **0 % TIN** en {financiar.zero_pitch()}."
            )
        info = [f"{CURRENCY_EMOJI} Saldo de {_name(self.owner)}: {format_amount(self.balance)}"]
        if cart:
            info.append(f"🛒 {len(cart)} en el carrito")
        info.append(f"{len(shown)} artículos · página {self.page + 1}/{pages}")
        lines.append("-# " + " · ".join(info))
        if active := self._active_filters():
            lines.append("-# Filtros: " + " · ".join(active))
        return "\n".join(lines)

    def _wish_summary(self) -> str:
        now = self.cog.clock()
        items = [self.catalog[i] for i in self.wishes if i in self.catalog]
        total = sum(quote(item, now).total for item in items)
        bell = (
            "🔕 Avisos por privado silenciados."
            if self.muted
            else "🔔 Aviso por privado de rebajas, temporadas, reposiciones, puestos del "
            "Rastro y de cuando ya te llegue el dinero."
        )
        return (
            f"Lo que quiere {_name(self.owner)}: {len(items)} de {MAX_WISHES} · todo junto, "
            f"{format_amount(total)} con IGIC. La lista es pública.\n-# {bell}"
        )

    def _active_filters(self) -> list[str]:
        active = []
        if self.search:
            active.append(f"🔎 «{discord.utils.escape_markdown(self.search)}»")
        if self.offers:
            active.append("🏷️ ofertas")
        if self.new:
            active.append("🆕 novedades")
        if self.affordable:
            active.append("💰 lo que te llega")
        if self.owned_filter == "tengo":
            active.append("✅ lo que tienes")
        elif self.owned_filter == "falta":
            active.append("❌ lo que te falta")
        return active

    def _empty_text(self) -> str:
        if not self.items_all:
            return "El colmado está vacío. Un admin tiene que llenarlo con `catalogo`."
        if self.prefs.tab == WISH_TAB and not self.wishes:
            return "La lista de deseos está vacía. Abre la ficha de algo y pulsa ⭐ para apuntarlo."
        return "No hay nada por aquí. Prueba otro pasillo u otra sección, o quita algún filtro."

    # -- Componentes -----------------------------------------------------------------

    def _item_select(self, items: list[ShopItem], now: float) -> ui.Select:
        options = [
            discord.SelectOption(
                label=item.name[:100],
                value=str(item.id),
                emoji=_select_emoji(item.emoji),
                # En un desplegable no hay formato: las comillas de `pala` saldrían tal cual.
                description=(
                    f"{format_amount(quote(item, now).base)}{_stock_note(item)} · "
                    f"{short_detail(item)}".replace("`", "")[:100]
                ),
            )
            for item in items
        ]
        select: ui.Select = ui.Select(
            placeholder="🔍 Elige algo para ver su ficha, o varias cosas para el carrito",
            min_values=1,
            max_values=len(options),
            options=options,
            custom_id="tienda:elegir",
        )

        async def pick(interaction: discord.Interaction) -> None:
            await self._pick(interaction, [int(value) for value in select.values])

        select.callback = pick  # type: ignore[method-assign]
        return select

    def _nav_row(self, pages: int, cart: dict[int, int]) -> ui.ActionRow:
        row: ui.ActionRow = ui.ActionRow()
        row.add_item(
            _button(
                "◀", self.prev_page, custom_id="tienda:pagina:anterior", disabled=self.page == 0
            )
        )
        row.add_item(
            _button(
                "▶",
                self.next_page,
                custom_id="tienda:pagina:siguiente",
                disabled=self.page >= pages - 1,
            )
        )
        if self.search:
            row.add_item(
                _button(f"✖️ «{self.search[:20]}»", self.clear_search, custom_id="tienda:buscar")
            )
        else:
            row.add_item(_button("🔎 Buscar", self.search_form, custom_id="tienda:buscar"))
        row.add_item(
            _button(
                f"🛒 Carrito · {len(cart)}" if cart else "🛒 Carrito",
                self.cart_button,
                custom_id="tienda:carrito",
                style=discord.ButtonStyle.primary,
            )
        )
        row.add_item(_button("🎒 Mochila", self.backpack, custom_id="tienda:mochila"))
        return row

    def _filter_row(self) -> ui.ActionRow:
        def style(on: bool) -> discord.ButtonStyle:
            return discord.ButtonStyle.primary if on else discord.ButtonStyle.secondary

        row: ui.ActionRow = ui.ActionRow()
        row.add_item(
            _button(
                "🏷️ Ofertas",
                self.toggle_offers,
                custom_id="tienda:filtro:ofertas",
                style=style(self.offers),
            )
        )
        row.add_item(
            _button(
                "🆕 Novedades",
                self.toggle_new,
                custom_id="tienda:filtro:nuevo",
                style=style(self.new),
            )
        )
        row.add_item(
            _button(
                "💰 Me llega",
                self.toggle_affordable,
                custom_id="tienda:filtro:llega",
                style=style(self.affordable),
            )
        )
        label = "❌ Lo que me falta" if self.owned_filter == "falta" else "✅ Lo que tengo"
        row.add_item(
            _button(
                label,
                self.cycle_owned,
                custom_id="tienda:filtro:tengo",
                style=style(self.owned_filter is not None),
            )
        )
        return row

    def _wish_row(self) -> ui.ActionRow:
        row: ui.ActionRow = ui.ActionRow()
        row.add_item(
            _button(
                "🛒 Todo al carrito",
                self.wishes_to_cart,
                custom_id="tienda:deseos:carrito",
                style=discord.ButtonStyle.success,
                disabled=not self.wishes,
            )
        )
        row.add_item(
            _button(
                "🔕 Avisos: no" if self.muted else "🔔 Avisos: sí",
                self.toggle_mute,
                custom_id="tienda:deseos:avisos",
            )
        )
        return row

    def _tab_select(self) -> ui.Select:
        when = local_now(self.cog.clock())
        options = []
        for key, label in TABS:
            count = sum(1 for i in self.items_all if in_tab(i, key) and on_sale(i, when))
            if count or key in ("todo", self.prefs.tab):
                emoji, _sep, text = label.partition(" ")
                options.append(
                    discord.SelectOption(
                        label=f"{text} · {count}",
                        value=key,
                        emoji=emoji,
                        default=key == self.prefs.tab,
                    )
                )
        options.append(
            discord.SelectOption(
                label=f"Mi lista de deseos · {len(self.wishes)}",
                value=WISH_TAB,
                emoji="⭐",
                default=self.prefs.tab == WISH_TAB,
            )
        )
        select: ui.Select = ui.Select(
            placeholder="🧭 Sección", options=options, custom_id="tienda:seccion"
        )

        async def section(interaction: discord.Interaction) -> None:
            await self._change(interaction, tab=select.values[0])

        select.callback = section  # type: ignore[method-assign]
        return select

    def _aisle_select(self) -> ui.Select | None:
        """Pasillos con algo en la sección, con cuántos tiene el dueño de cada uno."""
        base = self._base()
        counts = Counter(aisle_of(item.catalog_key).key for item in base)
        mine = Counter(aisle_of(item.catalog_key).key for item in base if self.owned[item.id])
        present = [
            a for a in (*AISLES, HOUSE_AISLE) if counts.get(a.key) or a.key == self.prefs.aisle
        ]
        if len(present) < 2 and self.prefs.aisle is None:
            return None
        options = [
            discord.SelectOption(
                label="Todos los pasillos",
                value="*",
                emoji="🧭",
                default=self.prefs.aisle is None,
            )
        ]
        options += [
            discord.SelectOption(
                label=f"{a.name} · {counts[a.key]}"[:100],
                value=a.key,
                emoji=a.emoji,
                description=f"Tienes {mine[a.key]} de {counts[a.key]}",
                default=a.key == self.prefs.aisle,
            )
            for a in present[:24]
        ]
        select: ui.Select = ui.Select(
            placeholder="🧭 Elige pasillo", options=options, custom_id="tienda:pasillo"
        )

        async def aisle(interaction: discord.Interaction) -> None:
            value = select.values[0]
            await self._change(interaction, aisle=None if value == "*" else value)

        select.callback = aisle  # type: ignore[method-assign]
        return select

    def _sort_select(self) -> ui.Select:
        options = []
        for key, label in SORTS:
            emoji, _sep, text = label.partition(" ")
            options.append(
                discord.SelectOption(
                    label=text, value=key, emoji=emoji, default=key == self.prefs.sort
                )
            )
        select: ui.Select = ui.Select(
            placeholder="↕️ Orden", options=options, custom_id="tienda:orden"
        )

        async def sort(interaction: discord.Interaction) -> None:
            await self._change(interaction, sort=select.values[0])

        select.callback = sort  # type: ignore[method-assign]
        return select

    # -- Clics -----------------------------------------------------------------------

    async def _redirect(
        self, interaction: discord.Interaction, apply: Callable[[Storefront], None]
    ) -> bool:
        """Si no es el dueño, le abre su propio escaparate en privado con el cambio que
        ha pulsado sobre lo que estaba viendo. Devuelve si lo hizo."""
        if interaction.user.id == self.owner.id:
            return False
        own = Storefront(self.cog, self.guild, interaction.user)
        own.prefs, own.search = replace(self.prefs), self.search
        own.offers, own.new, own.affordable = self.offers, self.new, self.affordable
        own.owned_filter = self.owned_filter
        apply(own)
        # Cargar el escaparate va a la base de datos: se acepta el clic antes.
        await ack(interaction, new_message=True)
        await own.load()
        await edit(interaction, view=own, allowed_mentions=_NO_MENTIONS)
        own.interaction = interaction
        return True

    async def _apply(
        self, interaction: discord.Interaction, apply: Callable[[Storefront], None]
    ) -> None:
        """Un cambio que solo toca memoria: al dueño se le repinta en el acto."""
        if await self._redirect(interaction, apply):
            return
        apply(self)
        self.rebuild()
        await interaction.response.edit_message(view=self, allowed_mentions=_NO_MENTIONS)

    async def _change(self, interaction: discord.Interaction, **changes: object) -> None:
        """Cambia sección, pasillo u orden; al dueño se le recuerda para la próxima vez."""

        def apply(view: Storefront) -> None:
            view.prefs = replace(view.prefs, **changes)  # type: ignore[arg-type]
            view.page = 0

        if not await self._redirect(interaction, apply):
            apply(self)
            self.cog.shop_prefs[(self.guild.id, self.owner.id)] = replace(self.prefs)
            self.rebuild()
            await interaction.response.edit_message(view=self, allowed_mentions=_NO_MENTIONS)

    async def _pick(self, interaction: discord.Interaction, item_ids: list[int]) -> None:
        mine = interaction.user.id == self.owner.id
        if len(item_ids) == 1:
            await open_sheet(self.cog, interaction, item_ids[0], storefront=self if mine else None)
        else:
            await add_to_cart(
                self.cog,
                interaction,
                item_ids,
                names=self.catalog,
                storefront=self if mine else None,
            )

    async def prev_page(self, interaction: discord.Interaction) -> None:
        await self._move(interaction, -1)

    async def next_page(self, interaction: discord.Interaction) -> None:
        await self._move(interaction, 1)

    async def _move(self, interaction: discord.Interaction, step: int) -> None:
        target = self.page + step

        def apply(view: Storefront) -> None:
            view.page = max(0, target)

        await self._apply(interaction, apply)

    def _flip(self, attr: str, value: object) -> Callable[[Storefront], None]:
        def apply(view: Storefront) -> None:
            setattr(view, attr, value)
            view.page = 0

        return apply

    async def toggle_offers(self, interaction: discord.Interaction) -> None:
        await self._apply(interaction, self._flip("offers", not self.offers))

    async def toggle_new(self, interaction: discord.Interaction) -> None:
        await self._apply(interaction, self._flip("new", not self.new))

    async def toggle_affordable(self, interaction: discord.Interaction) -> None:
        await self._apply(interaction, self._flip("affordable", not self.affordable))

    async def cycle_owned(self, interaction: discord.Interaction) -> None:
        following = OWNED_CYCLE[(OWNED_CYCLE.index(self.owned_filter) + 1) % len(OWNED_CYCLE)]
        await self._apply(interaction, self._flip("owned_filter", following))

    async def search_form(self, interaction: discord.Interaction) -> None:
        # Abrir un formulario tiene que ser la primera respuesta: nada de `ack`.
        await interaction.response.send_modal(SearchForm(self))

    async def clear_search(self, interaction: discord.Interaction) -> None:
        await self._apply(interaction, self._flip("search", None))

    async def cart_button(self, interaction: discord.Interaction) -> None:
        mine = interaction.user.id == self.owner.id
        await open_cart(self.cog, interaction, storefront=self if mine else None)

    async def backpack(self, interaction: discord.Interaction) -> None:
        await ack(interaction, new_message=True)
        view = await self.cog.backpack_view(self.guild, interaction.user, interaction.user)
        await edit(interaction, view=view, allowed_mentions=_NO_MENTIONS)
        view.interaction = interaction

    async def wishes_to_cart(self, interaction: discord.Interaction) -> None:
        if await self._redirect(
            interaction, self._flip("prefs", replace(self.prefs, tab=WISH_TAB))
        ):
            return
        when = local_now(self.cog.clock())
        ready = [
            item_id
            for item_id in self.wishes
            if (item := self.catalog.get(item_id)) is not None
            and item.visible
            and not item.sold_out
            and on_sale(item, when)
        ]
        await add_to_cart(
            self.cog, interaction, ready, names=self.catalog, storefront=self, open_panel=True
        )

    async def toggle_mute(self, interaction: discord.Interaction) -> None:
        if await self._redirect(
            interaction, self._flip("prefs", replace(self.prefs, tab=WISH_TAB))
        ):
            return
        await ack(interaction)
        self.muted = not self.muted
        await self.cog.repository.set_muted(self.guild.id, self.owner.id, self.muted)
        self.rebuild()
        await edit(interaction, view=self, allowed_mentions=_NO_MENTIONS)

    # -- Repintar sin clic -----------------------------------------------------------

    async def repaint(self, *, reload: bool = False) -> None:
        """Vuelve a pintar el mensaje del escaparate tras un cambio hecho en otro sitio
        (el carrito desde una ficha, una compra). Con `reload`, relee los datos."""
        if self.is_finished():
            return
        try:
            if reload:
                await self.load()
            else:
                self.rebuild()
            if self.interaction is not None:
                await self.interaction.edit_original_response(
                    view=self, allowed_mentions=_NO_MENTIONS
                )
            elif self.message is not None:
                await self.message.edit(view=self, allowed_mentions=_NO_MENTIONS)
        except discord.HTTPException:
            logger.debug("No se pudo repintar el escaparate", exc_info=True)

    async def on_timeout(self) -> None:
        """Apaga los botones al caducar."""
        await _close_view(self, self.message, self.interaction)


class SearchForm(ui.Modal):
    """El 🔎 del escaparate: qué buscar en el nombre o la descripción."""

    def __init__(self, storefront: Storefront) -> None:
        super().__init__(title="Buscar en el colmado", timeout=VIEW_TIMEOUT)
        self.storefront = storefront
        self.text: ui.TextInput = ui.TextInput(
            placeholder="plátano, Canarias, tapete…", max_length=40, required=True
        )
        self.add_item(ui.Label(text="¿Qué buscas?", component=self.text))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        view = self.storefront
        text = self.text.value.strip() or None
        if interaction.guild is not None:
            logros.note(
                view.cog.bot,
                interaction.guild.id,
                interaction.user.id,
                shop_browse_stats(searches=1),
            )
        await view._apply(interaction, view._flip("search", text))


# -- Comprar (ficha y carrito) ----------------------------------------------------------


@dataclass
class Outcome:
    """Cómo ha ido un pago: lo comprado y el texto, o el error (no se ha cobrado nada)."""

    bought: list[Bought]
    balance: int
    text: str
    error: str | None = None


async def buy(
    cog: Tienda, guild: discord.Guild, buyer: discord.Member, lines: Sequence[TicketLine]
) -> Outcome:
    """Paga `lines` todo o nada (`Tienda.complete_cart`) y prepara la factura."""
    try:
        bought, balance = await cog.complete_cart(guild, buyer, lines)
    except ShopError as error:
        logros.note(cog.bot, guild.id, buyer.id, shop_browse_stats(failed=True))
        return Outcome([], 0, "", str(error))
    except InsufficientFundsError as error:
        total = sum(line.total for line in lines)
        logros.note(cog.bot, guild.id, buyer.id, shop_browse_stats(failed=True))
        return Outcome(
            [],
            0,
            "",
            f"No te llega: tienes {format_amount(error.balance)} y son {format_amount(total)}.",
        )
    return Outcome(bought, balance, await cog.cart_paid_text(bought, balance, buyer, cog.clock()))


def _member(interaction: discord.Interaction) -> tuple[discord.Guild, discord.Member] | None:
    guild, user = interaction.guild, interaction.user
    if guild is None or not isinstance(user, discord.Member):
        return None
    return guild, user


async def open_sheet(
    cog: Tienda,
    interaction: discord.Interaction,
    item_id: int,
    *,
    storefront: Storefront | None = None,
    back: CartPanel | None = None,
) -> None:
    """Abre la ficha de un artículo para quien pulsa: en privado, o en el mismo mensaje
    del carrito si se abre desde él (`back`)."""
    if (who := _member(interaction)) is None:
        await notify(interaction, "La tienda solo abre en un servidor.")
        return
    guild, viewer = who
    # La ficha lee el artículo, la mochila, el saldo y la lista: se acepta el clic antes.
    await ack(interaction, new_message=back is None)
    sheet = await ItemSheet.build(cog, guild, viewer, item_id, storefront=storefront, back=back)
    if sheet is None:
        if back is None:
            await edit(interaction, content="Ese artículo ya no está en la tienda.")
        else:
            await notify(interaction, "Ese artículo ya no está en la tienda.")
        return
    await edit(interaction, view=sheet, allowed_mentions=_NO_MENTIONS)
    sheet.interaction = interaction
    logros.note(cog.bot, guild.id, viewer.id, shop_browse_stats(sheets=1))


async def open_cart(
    cog: Tienda, interaction: discord.Interaction, *, storefront: Storefront | None = None
) -> None:
    """Abre el carrito de quien pulsa, en privado."""
    if (who := _member(interaction)) is None:
        await notify(interaction, "La tienda solo abre en un servidor.")
        return
    guild, buyer = who
    await ack(interaction, new_message=True)
    panel = CartPanel(cog, guild, buyer, storefront=storefront)
    await panel.load()
    await edit(interaction, view=panel, allowed_mentions=_NO_MENTIONS)
    panel.interaction = interaction


async def add_to_cart(
    cog: Tienda,
    interaction: discord.Interaction,
    item_ids: Sequence[int],
    *,
    names: dict[int, ShopItem],
    storefront: Storefront | None = None,
    open_panel: bool = False,
) -> None:
    """Echa al carrito de quien pulsa una unidad de cada artículo que no llevara ya.

    Lo que se pueda comprar o no se mira en el carrito, línea a línea. Sin `open_panel`
    solo cambia memoria: contesta en el acto (al dueño le repinta el escaparate).

    Args:
        names: Artículos por id, para nombrarlos en el aviso.
    """
    if (who := _member(interaction)) is None:
        await notify(interaction, "La tienda solo abre en un servidor.")
        return
    guild, buyer = who
    added: list[int] = []
    refused = None
    for item_id in item_ids:
        if item_id in cog.cart(guild.id, buyer.id):
            continue
        if (refused := cog.put_in_cart(guild.id, buyer.id, item_id, 1)) is not None:
            break
        added.append(item_id)
    if open_panel:
        await ack(interaction, new_message=True)
        panel = CartPanel(cog, guild, buyer, storefront=storefront)
        panel.note = refused
        await panel.load()
        await edit(interaction, view=panel, allowed_mentions=_NO_MENTIONS)
        panel.interaction = interaction
        if storefront is not None:
            await storefront.repaint()
        return
    count = len(cog.cart(guild.id, buyer.id))
    what = ", ".join(f"{names[i].emoji} {names[i].name}" for i in added if i in names)
    text = f"🛒 Al carrito: {what}." if added else "🛒 Eso ya estaba en el carrito."
    text += f" Llevas {count} {'artículo' if count == 1 else 'artículos'}."
    if refused:
        text += f"\n⚠️ {refused}"
    link = CartLink(cog)
    if storefront is not None:
        storefront.rebuild()
        await interaction.response.edit_message(view=storefront, allowed_mentions=_NO_MENTIONS)
        await interaction.followup.send(text, ephemeral=True, view=link)
    else:
        await interaction.response.send_message(text, ephemeral=True, view=link)


class CartLink(ui.View):
    """El botón «Ver carrito» del aviso de que algo se ha echado al carrito."""

    def __init__(self, cog: Tienda) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        button: ui.Button = ui.Button(
            label="🛒 Ver carrito",
            style=discord.ButtonStyle.primary,
            custom_id="tienda:ver-carrito",
        )
        button.callback = self.show_cart  # type: ignore[method-assign]
        self.add_item(button)

    async def show_cart(self, interaction: discord.Interaction) -> None:
        await open_cart(self.cog, interaction)


# -- Ficha ------------------------------------------------------------------------------


class ItemSheet(ui.LayoutView):
    """La ficha de un artículo para quien la abre (ver el módulo)."""

    def __init__(
        self,
        cog: Tienda,
        guild: discord.Guild,
        viewer: discord.Member,
        item: ShopItem,
        *,
        entries: list[InventoryEntry],
        bought: int,
        level: int,
        balance: int,
        wished: bool,
        wishers: int,
        owners: int,
        stall: tuple[int, float] | None,
        storefront: Storefront | None = None,
        back: CartPanel | None = None,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild = guild
        self.viewer = viewer
        self.item = item
        self.entries = entries
        self.bought = bought
        self.level = level
        self.balance = balance
        self.wished = wished
        self.wishers = wishers
        self.owners = owners
        self.stall = stall
        self.storefront = storefront
        self.back = back
        in_cart = cog.cart(guild.id, viewer.id).get(item.id, 0)
        self.qty = max(1, min(in_cart or 1, max_quantity(item, bought)))
        #: El precio enseñado: es el que se cobra (si cambia, la compra se rechaza).
        self.price: Quote = quote(item, cog.clock())
        #: Aviso del último intento (algo cambió al pagar) o del carrito.
        self.note: str | None = None
        self.done = False
        self.result: str | None = None
        self.interaction: discord.Interaction | None = None
        self._lock = TimedLock("shop_front:ItemSheet._lock")
        self.rebuild()

    @classmethod
    async def build(
        cls,
        cog: Tienda,
        guild: discord.Guild,
        viewer: discord.Member,
        item_id: int,
        *,
        storefront: Storefront | None = None,
        back: CartPanel | None = None,
    ) -> ItemSheet | None:
        """Lee lo que hace falta y monta la ficha; `None` si el artículo ya no existe."""
        repository = cog.repository
        item = await repository.item(guild.id, item_id)
        if item is None:
            return None
        now = cog.clock()
        wishers = await repository.wishers(guild.id, item.id)
        return cls(
            cog,
            guild,
            viewer,
            item,
            entries=await repository.inventory(guild.id, viewer.id, now),
            bought=(await repository.purchase_counts(guild.id, viewer.id))[item.id],
            level=await cog.member_level(guild.id, viewer.id) if item.min_level else 0,
            balance=await cog.economy.balance(guild.id, viewer.id),
            wished=viewer.id in wishers,
            wishers=len(wishers),
            owners=await repository.owners(guild.id, item.id),
            stall=rastro.floor(cog.bot, guild.id, item.id),
            storefront=storefront,
            back=back,
        )

    @property
    def in_cart(self) -> int:
        return self.cog.cart(self.guild.id, self.viewer.id).get(self.item.id, 0)

    def rebuild(self) -> None:
        """Pinta la ficha, o el resultado del pago."""
        self.clear_items()
        if self.done:
            container = ui.Container(accent_colour=COLOR_PAID)
            container.add_item(ui.TextDisplay(self.result or ""))
            self.add_item(container)
            row: ui.ActionRow = ui.ActionRow()
            row.add_item(_button("🔁 Otra vez", self.again, custom_id="ficha:otra"))
            if self.back is not None:
                row.add_item(_button("◀ Carrito", self.show_cart, custom_id="ficha:volver"))
            self.add_item(row)
            return
        now = self.cog.clock()
        item = self.item
        self.price = quote(item, now)
        cap = max_quantity(item, self.bought)
        problem = self.cog.check_line(
            self.guild,
            self.viewer,
            item,
            self.qty,
            bought=self.bought,
            entries=self.entries,
            level=self.level,
            now=now,
        )
        owned = sum(1 for entry in self.entries if entry.item_id == item.id)
        line = TicketLine(item, self.qty, self.price)
        container = ui.Container(accent_colour=COLOR_ERROR if problem else COLOR)
        container.add_item(ui.TextDisplay(item_card(item, now, owned=owned) + self._extra()))
        container.add_item(ui.Separator())
        body = []
        if self.note:
            body.append(f"⚠️ {self.note}")
        if problem:
            body.append(f"❌ {problem}")
        else:
            body += [cart_preview([line]), _tax_note([line]), _money_line(line.total, self.balance)]
        container.add_item(ui.TextDisplay("\n".join(body)))
        self.add_item(container)
        if cap > 1:
            qty_row: ui.ActionRow = ui.ActionRow()
            qty_row.add_item(
                _button("➖", self.less, custom_id="ficha:menos", disabled=self.qty <= 1)
            )
            qty_row.add_item(
                _button(f"× {self.qty}", self._noop, custom_id="ficha:cantidad", disabled=True)
            )
            qty_row.add_item(
                _button("➕", self.more, custom_id="ficha:mas", disabled=self.qty >= cap)
            )
            self.add_item(qty_row)
        actions: ui.ActionRow = ui.ActionRow()
        actions.add_item(
            _button(
                f"💳 Pagar {format_amount(line.total)}",
                self.pay,
                custom_id="ficha:pagar",
                style=discord.ButtonStyle.success,
                disabled=problem is not None or line.total > self.balance,
            )
        )
        if problem is None and financed_purchase([line]) is not None:
            actions.add_item(_button(financiar.LABEL, self.finance, custom_id="ficha:financiar"))
        in_cart = self.in_cart
        actions.add_item(
            _button(
                f"🛒 Dejar × {self.qty} en el carrito" if in_cart else "🛒 Al carrito",
                self.to_cart,
                custom_id="ficha:carrito",
                style=discord.ButtonStyle.primary,
                disabled=problem is not None and cap == 0,
            )
        )
        if in_cart:
            actions.add_item(_button("🗑️ Quitar del carrito", self.drop, custom_id="ficha:quitar"))
        actions.add_item(
            _button(
                "⭐ Quitar de mi lista" if self.wished else "⭐ A mi lista",
                self.wish,
                custom_id="ficha:deseo",
            )
        )
        self.add_item(actions)
        cart = self.cog.cart(self.guild.id, self.viewer.id)
        if self.back is not None or cart:
            back_row: ui.ActionRow = ui.ActionRow()
            back_row.add_item(
                _button(
                    "◀ Volver al carrito"
                    if self.back is not None
                    else f"🛒 Ver carrito · {len(cart)}",
                    self.show_cart,
                    custom_id="ficha:volver",
                )
            )
            self.add_item(back_row)

    def _extra(self) -> str:
        """Lo que la ficha cuenta además de la tarjeta del escaparate."""
        bits = []
        if self.in_cart:
            bits.append(f"🛒 Llevas {self.in_cart} en el carrito")
        if self.wishers:
            mine = " (la tuya también)" if self.wished else ""
            bits.append(f"⭐ En {self.wishers} {'lista' if self.wishers == 1 else 'listas'}{mine}")
        if self.owners:
            bits.append(f"👥 Lo {'tiene' if self.owners == 1 else 'tienen'} {self.owners}")
        if self.stall is not None:
            price, closes = self.stall
            bits.append(
                f"🧺 En el Rastro desde {format_amount(price)} (cierra <t:{int(closes)}:R>)"
            )
        if self.item.per_user is not None and self.bought:
            bits.append(f"Has comprado {self.bought} de {self.item.per_user}")
        return ("\n-# " + " · ".join(bits)) if bits else ""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """La ficha es de quien la abrió (en la práctica ya es privada)."""
        return interaction.user.id == self.viewer.id

    async def _noop(self, interaction: discord.Interaction) -> None:
        await ack(interaction)

    async def less(self, interaction: discord.Interaction) -> None:
        self.qty = max(1, self.qty - 1)
        self.note = None
        self.rebuild()
        await interaction.response.edit_message(view=self, allowed_mentions=_NO_MENTIONS)

    async def more(self, interaction: discord.Interaction) -> None:
        self.qty = min(max_quantity(self.item, self.bought), self.qty + 1)
        self.note = None
        self.rebuild()
        await interaction.response.edit_message(view=self, allowed_mentions=_NO_MENTIONS)

    async def to_cart(self, interaction: discord.Interaction) -> None:
        refused = self.cog.put_in_cart(self.guild.id, self.viewer.id, self.item.id, self.qty)
        if refused is not None:
            await notify(interaction, f"⚠️ {refused}")
            return
        if self.back is not None:
            await self.show_cart(interaction)
            return
        self.note = None
        self.rebuild()
        await interaction.response.edit_message(view=self, allowed_mentions=_NO_MENTIONS)
        if self.storefront is not None:
            await self.storefront.repaint()

    async def drop(self, interaction: discord.Interaction) -> None:
        cart = self.cog.cart(self.guild.id, self.viewer.id)
        cart.pop(self.item.id, None)
        self.cog.set_cart(self.guild.id, self.viewer.id, cart)
        if self.back is not None:
            await self.show_cart(interaction)
            return
        self.rebuild()
        await interaction.response.edit_message(view=self, allowed_mentions=_NO_MENTIONS)
        if self.storefront is not None:
            await self.storefront.repaint()

    async def wish(self, interaction: discord.Interaction) -> None:
        # Apuntarlo va a la base de datos: se acepta el clic antes.
        await ack(interaction)
        wished, refused = await self.cog.toggle_wish(
            self.guild.id, self.viewer, self.item, interaction.channel
        )
        if refused is not None:
            await notify(interaction, f"⚠️ {refused}")
            return
        self.wishers += 1 if wished else -1
        self.wished = wished
        self.rebuild()
        await edit(interaction, view=self, allowed_mentions=_NO_MENTIONS)
        if self.storefront is not None:
            await self.storefront.repaint(reload=True)

    async def show_cart(self, interaction: discord.Interaction) -> None:
        """Pasa este mismo mensaje al carrito."""
        await ack(interaction)
        panel = self.back or CartPanel(
            self.cog, self.guild, self.viewer, storefront=self.storefront
        )
        panel.done = False
        await panel.load()
        await edit(interaction, view=panel, allowed_mentions=_NO_MENTIONS)
        panel.interaction = self.interaction or interaction

    async def again(self, interaction: discord.Interaction) -> None:
        """Tras pagar: la misma ficha con los datos de ahora, para repetir."""
        await ack(interaction)
        fresh = await ItemSheet.build(
            self.cog,
            self.guild,
            self.viewer,
            self.item.id,
            storefront=self.storefront,
            back=self.back,
        )
        if fresh is None:
            await notify(interaction, "Ese artículo ya no está en la tienda.")
            return
        fresh.qty = max(1, min(self.qty, max_quantity(fresh.item, fresh.bought)))
        fresh.rebuild()
        fresh.interaction = self.interaction
        await edit(interaction, view=fresh, allowed_mentions=_NO_MENTIONS)

    async def pay(self, interaction: discord.Interaction) -> None:
        # El ack va antes del candado: un doble clic no espera a la compra del primero.
        await ack(interaction)
        async with self._lock:
            if self.done:
                return
            outcome = await self._purchase()
            await edit(interaction, view=self, allowed_mentions=_NO_MENTIONS)
        if outcome.error is None:
            await self._after_paid(interaction, outcome)

    async def _purchase(self) -> Outcome:
        """Paga lo que enseña la ficha (dentro del candado) y la repinta."""
        outcome = await buy(
            self.cog, self.guild, self.viewer, [TicketLine(self.item, self.qty, self.price)]
        )
        if outcome.error is None:
            self.done, self.result = True, outcome.text
        else:
            await self._reload(note=outcome.error)
        self.rebuild()
        return outcome

    async def _after_paid(self, interaction: discord.Interaction, outcome: Outcome) -> None:
        await self.cog.after_cart(
            interaction, self.guild, self.viewer, outcome.bought, outcome.balance
        )
        if self.storefront is not None:
            await self.storefront.repaint(reload=True)

    async def finance(self, interaction: discord.Interaction) -> None:
        """💳 Financiar: la oferta del banco sustituye a la ficha (↩️ Volver la devuelve)."""
        purchase = financed_purchase([TicketLine(self.item, self.qty, self.price)])
        if purchase is None or self.done:
            await interaction.response.edit_message(view=self)
            return
        paid: list[Outcome] = []

        async def buy_it(_interaction: discord.Interaction) -> str | None:
            async with self._lock:
                if self.done:
                    return "Ya lo has comprado."
                paid.append(await self._purchase())
            return paid[-1].error

        async def after(after_interaction: discord.Interaction) -> None:
            await self._after_paid(after_interaction, paid[-1])

        deal = financiar.Deal(purchase, buy_it, host=self, after=after)
        if not await financiar.open_finance(self.cog.bot, interaction, deal):
            await interaction.response.send_message(
                "El banco está cerrado ahora mismo.", ephemeral=True
            )

    async def _reload(self, *, note: str | None) -> None:
        """Vuelve a leer el artículo y lo del comprador (algo cambió al pagar)."""
        fresh = await ItemSheet.build(self.cog, self.guild, self.viewer, self.item.id)
        if fresh is not None:
            self.item, self.entries, self.bought = fresh.item, fresh.entries, fresh.bought
            self.level, self.balance = fresh.level, fresh.balance
            self.qty = max(1, min(self.qty, max_quantity(self.item, self.bought)))
        self.note = note


# -- Carrito ----------------------------------------------------------------------------


class CartPanel(ui.LayoutView):
    """El carrito de quien lo abre (ver el módulo)."""

    def __init__(
        self,
        cog: Tienda,
        guild: discord.Guild,
        buyer: discord.Member,
        *,
        storefront: Storefront | None = None,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild = guild
        self.buyer = buyer
        self.storefront = storefront
        self.lines: list[tuple[ShopItem, int]] = []
        self.prices: dict[int, Quote] = {}
        self.problems: dict[int, str] = {}
        self.balance = 0
        #: Aviso del último intento de pago (algo cambió) o de lo que no cupo.
        self.note: str | None = None
        self.done = False
        self.result: str | None = None
        #: Lo que se pagó, para «Repetir compra».
        self.last: dict[int, int] = {}
        self.interaction: discord.Interaction | None = None
        self._lock = TimedLock("shop_front:CartPanel._lock")

    async def load(self) -> None:
        """Lee los artículos del carrito y lo que hace falta para mirar cada línea."""
        repository = self.cog.repository
        cart = self.cog.cart(self.guild.id, self.buyer.id)
        now = self.cog.clock()
        catalog = (
            {i.id: i for i in await repository.items(self.guild.id, include_hidden=True)}
            if cart
            else {}
        )
        if any(item_id not in catalog for item_id in cart):
            # Un admin ha borrado algo que estaba en el carrito: fuera.
            cart = {item_id: qty for item_id, qty in cart.items() if item_id in catalog}
            self.cog.set_cart(self.guild.id, self.buyer.id, cart)
        self.lines = [(catalog[item_id], qty) for item_id, qty in cart.items()]
        items = [item for item, _qty in self.lines]
        entries = (
            await repository.inventory(self.guild.id, self.buyer.id, now)
            if any(item.role_id for item in items)
            else []
        )
        bought = (
            await repository.purchase_counts(self.guild.id, self.buyer.id) if items else Counter()
        )
        needs_level = any(item.min_level for item in items)
        level = await self.cog.member_level(self.guild.id, self.buyer.id) if needs_level else 0
        self.balance = await self.cog.economy.balance(self.guild.id, self.buyer.id)
        self.prices = {item.id: quote(item, now) for item in items}
        self.problems = {}
        for item, qty in self.lines:
            problem = self.cog.check_line(
                self.guild,
                self.buyer,
                item,
                qty,
                bought=bought[item.id],
                entries=entries,
                level=level,
                now=now,
            )
            if problem is not None:
                self.problems[item.id] = problem
        self.rebuild()

    @property
    def ticket(self) -> list[TicketLine]:
        return [TicketLine(item, qty, self.prices[item.id]) for item, qty in self.lines]

    def rebuild(self) -> None:
        """Pinta el carrito, o la factura tras pagar."""
        self.clear_items()
        if self.done:
            container = ui.Container(accent_colour=COLOR_PAID)
            container.add_item(ui.TextDisplay(self.result or ""))
            self.add_item(container)
            row: ui.ActionRow = ui.ActionRow()
            row.add_item(_button("🔁 Repetir compra", self.again, custom_id="carrito:otra"))
            self.add_item(row)
            return
        units = sum(qty for _item, qty in self.lines)
        container = ui.Container(accent_colour=COLOR_ERROR if self.problems else COLOR)
        container.add_item(
            ui.TextDisplay(
                f"### 🛒 Carrito de {_name(self.buyer)}\n-# {len(self.lines)} de "
                f"{MAX_CART_LINES} artículos · {units} {'unidad' if units == 1 else 'unidades'}"
                " · se paga todo junto o nada"
            )
        )
        if self.note:
            container.add_item(ui.TextDisplay(f"⚠️ {self.note}"))
        if not self.lines:
            container.add_item(
                ui.TextDisplay(
                    "Vacío. En la `tienda`, elige varias cosas a la vez en el desplegable o "
                    "pulsa 🛒 en la ficha de algo."
                )
            )
            self.add_item(container)
            return
        rows = []
        for line in self.ticket:
            # Sin IGIC, como en el escaparate: el impuesto se suma en el ticket de abajo.
            text = (
                f"{line.qty} × {line.item.emoji} **{line.item.name}** · {format_amount(line.base)}"
                + (" · al contado" if emblematic(line.item) else " · 💳")
            )
            if (problem := self.problems.get(line.item.id)) is not None:
                text += f"\n-# ⚠️ {problem}"
            rows.append(text)
        container.add_item(ui.TextDisplay(_clip("\n".join(rows), 1500)))
        container.add_item(ui.Separator())
        total = sum(line.total for line in self.ticket)
        container.add_item(
            ui.TextDisplay(
                "\n".join(
                    [
                        cart_preview(self.ticket),
                        _tax_note(self.ticket),
                        _money_line(total, self.balance),
                        *_finance_note(self.ticket),
                    ]
                )
            )
        )
        self.add_item(container)
        pick_row: ui.ActionRow = ui.ActionRow()
        pick_row.add_item(self._line_select())
        self.add_item(pick_row)
        buttons: ui.ActionRow = ui.ActionRow()
        buttons.add_item(
            _button(
                f"💳 Pagar {format_amount(total)}",
                self.pay,
                custom_id="carrito:pagar",
                style=discord.ButtonStyle.success,
                disabled=bool(self.problems) or total > self.balance,
            )
        )
        if not self.problems and financed_purchase(self.ticket) is not None:
            buttons.add_item(_button(financiar.LABEL, self.finance, custom_id="carrito:financiar"))
        buttons.add_item(_button("🗑️ Vaciar", self.empty, custom_id="carrito:vaciar"))
        self.add_item(buttons)

    def _line_select(self) -> ui.Select:
        options = [
            discord.SelectOption(
                label=f"{qty} × {item.name}"[:100],
                value=str(item.id),
                emoji=_select_emoji(item.emoji),
                description=(self.problems.get(item.id) or "Cambiar la cantidad o quitarlo")[:100],
            )
            for item, qty in self.lines
        ]
        select: ui.Select = ui.Select(
            placeholder="✏️ Cambiar o quitar algo", options=options, custom_id="carrito:linea"
        )

        async def change_line(interaction: discord.Interaction) -> None:
            await open_sheet(
                self.cog, interaction, int(select.values[0]), storefront=self.storefront, back=self
            )

        select.callback = change_line  # type: ignore[method-assign]
        return select

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """El carrito es de quien lo abrió (en la práctica ya es privado)."""
        return interaction.user.id == self.buyer.id

    async def empty(self, interaction: discord.Interaction) -> None:
        lines = len(self.lines)
        self.cog.set_cart(self.guild.id, self.buyer.id, {})
        self.lines, self.problems, self.note = [], {}, None
        self.rebuild()
        await interaction.response.edit_message(view=self, allowed_mentions=_NO_MENTIONS)
        logros.note(self.cog.bot, self.guild.id, self.buyer.id, shop_browse_stats(emptied=lines))
        if self.storefront is not None:
            await self.storefront.repaint()

    async def again(self, interaction: discord.Interaction) -> None:
        """Tras pagar: vuelve a llenar el carrito con lo mismo, para revisarlo y pagar."""
        await ack(interaction)
        self.cog.set_cart(self.guild.id, self.buyer.id, self.last)
        self.done, self.note = False, None
        await self.load()
        await edit(interaction, view=self, allowed_mentions=_NO_MENTIONS)

    async def pay(self, interaction: discord.Interaction) -> None:
        # El ack va antes del candado: un doble clic no espera a la compra del primero.
        await ack(interaction)
        async with self._lock:
            if self.done or not self.lines:
                return
            outcome = await self._purchase()
            await edit(interaction, view=self, allowed_mentions=_NO_MENTIONS)
        if outcome.error is None:
            await self._after_paid(interaction, outcome)

    async def _purchase(self) -> Outcome:
        """Paga el carrito entero (dentro del candado) y lo repinta."""
        outcome = await buy(self.cog, self.guild, self.buyer, self.ticket)
        if outcome.error is None:
            self.last = {item.id: qty for item, qty in self.lines}
            self.cog.set_cart(self.guild.id, self.buyer.id, {})
            self.done, self.result, self.note = True, outcome.text, None
            self.rebuild()
        else:
            self.note = outcome.error
            await self.load()
        return outcome

    async def _after_paid(self, interaction: discord.Interaction, outcome: Outcome) -> None:
        await self.cog.after_cart(
            interaction, self.guild, self.buyer, outcome.bought, outcome.balance
        )
        if self.storefront is not None:
            await self.storefront.repaint(reload=True)

    async def finance(self, interaction: discord.Interaction) -> None:
        """💳 Financiar el carrito (lo emblemático, al contado): la oferta lo sustituye (↩️ Volver
        lo devuelve)."""
        purchase = financed_purchase(self.ticket)
        if purchase is None or self.done or not self.lines:
            await interaction.response.edit_message(view=self)
            return
        paid: list[Outcome] = []

        async def buy_it(_interaction: discord.Interaction) -> str | None:
            async with self._lock:
                if self.done or not self.lines:
                    return "El carrito ya está pagado o vacío."
                paid.append(await self._purchase())
            return paid[-1].error

        async def after(after_interaction: discord.Interaction) -> None:
            await self._after_paid(after_interaction, paid[-1])

        deal = financiar.Deal(purchase, buy_it, host=self, after=after)
        if not await financiar.open_finance(self.cog.bot, interaction, deal):
            await interaction.response.send_message(
                "El banco está cerrado ahora mismo.", ephemeral=True
            )
