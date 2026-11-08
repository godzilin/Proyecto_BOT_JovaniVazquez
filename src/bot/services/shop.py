"""Reglas de la tienda (El Colmado de Jovani), sin Discord ni base de datos.

Qué se vende (`Kind`):

- **Roles** (`rol`): un rol del servidor, para siempre o alquilado unos días.
  Los permanentes se pueden poner y quitar desde la mochila (útil para los
  roles de color). Volver a comprar uno alquilado alarga el alquiler.
- **Potenciadores** (`xp`): multiplican el XP de mensajes y voz durante un
  tiempo. Si ya tienes uno, el nuevo se pone a la cola y empieza cuando
  acaba el anterior, para que no se pisen.
- **Coleccionables** (`objeto`): no hacen nada, solo se tienen. Si tienen
  existencias limitadas, cada unidad lleva su número de serie ("nº 3 de 10").
  Algunos se usan desde la mochila (`bot.services.shop_uses`).
- **Mascotas** (`mascota`): solo del surtido de serie
  (`bot.services.pets_catalog`); se cuidan con `mascota` y la activa sale en
  los mensajes del bot (`bot.services.pets`).

Todo el catálogo lo montan los administradores con `catalogo`: precio, tipo
de IGIC, rebajas con fecha de fin, existencias, límite por persona y nivel
mínimo. Los precios del catálogo van sin IGIC, como en un escaparate de
empresa a empresa; el IGIC se suma en la caja y se enseña aparte, para que se
vea lo que se lleva Perro Sanxe. Tratamiento fiscal en
`bot.services.economy.EconomyService.purchase`.

Carrito: se pagan varias líneas de una vez (`TicketLine`, hasta `MAX_CART_LINES`
artículos y `MAX_QUANTITY` unidades de cada uno, `max_quantity`) y la factura
desglosa el impuesto por tipo (`cart_receipt`).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from bot.services import credit
from bot.services.levels import TIMEZONE
from bot.services.pets_catalog import species_of
from bot.services.pets_looks import is_breed
from bot.services.taxes import IGIC_BY_KEY, IGIC_DEFAULT, IgicRate, igic

SHOP_NAME = "El Colmado de Jovani"


class Kind(StrEnum):
    """Tipo de artículo. El valor es lo que se guarda en la base de datos."""

    ROLE = "rol"
    BOOST = "xp"
    TROPHY = "objeto"
    PET = "mascota"

    @property
    def title(self) -> str:
        """Nombre de la sección del escaparate."""
        return {
            "rol": "Roles",
            "xp": "Potenciadores",
            "objeto": "Coleccionables",
            "mascota": "Mascotas",
        }[self.value]

    @property
    def icon(self) -> str:
        """Emoji de la sección y emoji por defecto de sus artículos."""
        return {"rol": "🎭", "xp": "⚡", "objeto": "💎", "mascota": "🐾"}[self.value]


# -- Límites ---------------------------------------------------------------------------

MAX_NAME = 40
MAX_DESCRIPTION = 200
MAX_EMOJI = 64
#: Precio máximo de catálogo; muy por encima de cualquier saldo realista.
MAX_PRICE = 1_000_000_000
MAX_STOCK = 100_000
MAX_LEVEL = 500
#: Rebaja máxima: regalar las cosas no es una rebaja.
MAX_DISCOUNT = 90
#: Multiplicador de XP, en tanto por cien (150 = ×1,5).
MIN_BOOST = 110
MAX_BOOST = 300
MIN_SPAN = 3600
MAX_SPAN = 365 * 86400
#: Un artículo es "nuevo" durante sus primeros días en el catálogo.
NEW_ITEM_SECONDS = 3 * 86400
#: Ventas mínimas para que el más vendido lleve la etiqueta 🔥.
BEST_SELLER_MIN = 3

#: Permisos que un rol de la tienda no puede tener: si se vendiera, cualquiera
#: con dinero podría moderar o romper el servidor. Nombres de
#: `discord.Permissions`.
DANGEROUS_PERMISSIONS: tuple[str, ...] = (
    "administrator",
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "manage_webhooks",
    "manage_messages",
    "manage_nicknames",
    "manage_expressions",
    "manage_events",
    "manage_threads",
    "kick_members",
    "ban_members",
    "moderate_members",
    "mention_everyone",
    "view_audit_log",
)


class ShopError(Exception):
    """Algo impide una compra o un cambio del catálogo.

    El mensaje está pensado para enseñárselo al usuario.
    """


# -- Artículos -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ShopItem:
    """Un artículo del catálogo de un servidor.

    Attributes:
        id: Identificador estable del artículo.
        price: Precio de catálogo en Y$, sin IGIC ni rebaja.
        igic_key: Tipo de IGIC (`bot.services.taxes.IGIC_RATES`).
        role_id: Rol que se da (solo `Kind.ROLE`).
        duration: Segundos que dura (alquiler de rol o potenciador);
            `None` es para siempre.
        multiplier: Multiplicador de XP en tanto por cien (solo `Kind.BOOST`).
        stock: Unidades totales a la venta; `None` es ilimitado.
        sold: Unidades vendidas (sin contar devoluciones).
        per_user: Compras máximas por persona; `None` sin límite.
        min_level: Nivel mínimo para comprarlo (0 = cualquiera).
        discount: Rebaja en % (0 = sin rebaja).
        discount_until: Fin de la rebaja (epoch); `None` hasta que se quite.
        visible: Si se enseña y se vende. Los ocultos solo los ve `catalogo`.
        created_at: Cuándo entró en el catálogo (epoch).
        catalog_key: Clave del surtido de serie (`bot.services.shop_catalog`)
            si vino de ahí; `None` si lo creó un administrador.
    """

    id: int
    guild_id: int
    kind: Kind
    name: str
    emoji: str
    description: str
    price: int
    igic_key: str = IGIC_DEFAULT.key
    role_id: int | None = None
    duration: int | None = None
    multiplier: int | None = None
    stock: int | None = None
    sold: int = 0
    per_user: int | None = None
    min_level: int = 0
    discount: int = 0
    discount_until: float | None = None
    visible: bool = True
    created_at: float = 0.0
    catalog_key: str | None = None

    @property
    def igic_rate(self) -> IgicRate:
        """Tipo de IGIC; el general si la clave guardada ya no existe."""
        return IGIC_BY_KEY.get(self.igic_key, IGIC_DEFAULT)

    @property
    def remaining(self) -> int | None:
        """Unidades que quedan; `None` si es ilimitado."""
        return None if self.stock is None else max(0, self.stock - self.sold)

    @property
    def sold_out(self) -> bool:
        """Si se ha agotado."""
        return self.remaining == 0

    def on_sale(self, now: float) -> bool:
        """Si tiene una rebaja en vigor."""
        return self.discount > 0 and (self.discount_until is None or now < self.discount_until)

    def is_new(self, now: float) -> bool:
        """Si acaba de llegar al catálogo."""
        return now - self.created_at < NEW_ITEM_SECONDS

    @property
    def permanent_role(self) -> bool:
        """Rol que se compra una vez y es para siempre."""
        return self.kind is Kind.ROLE and self.duration is None


# -- Precio en caja --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Quote:
    """Lo que cuesta un artículo ahora mismo, desglosado como en un ticket.

    Attributes:
        price: Precio de catálogo, con el IPC del crédito encima.
        discount_pct: Rebaja aplicada en % (0 si no hay).
        discount: Lo que se descuenta, en Y$.
        base: Base imponible: precio menos rebaja.
        rate: Tipo de IGIC.
        tax: IGIC sobre la base.
        ipc: Lo que el IPC del crédito ha subido el precio, en % (`credit`).
    """

    price: int
    discount_pct: int
    discount: int
    base: int
    rate: IgicRate
    tax: int
    ipc: int = 0

    @property
    def total(self) -> int:
        """Lo que se paga."""
        return self.base + self.tax


def quote(item: ShopItem, now: float) -> Quote:
    """Desglose de la compra de `item` en el momento `now`.

    La rebaja se resta antes del IGIC: los descuentos de la propia venta no
    forman parte de la base imponible (art. 22 de la Ley 20/1991). El IPC del crédito del
    servidor (`credit.ipc`) sube el precio de catálogo antes de todo.
    """
    ipc = credit.ipc(item.guild_id)
    price = credit.inflate(item.price, ipc)
    pct = item.discount if item.on_sale(now) else 0
    discount = price * pct // 100
    base = price - discount
    rate = item.igic_rate
    return Quote(
        price=price,
        discount_pct=pct,
        discount=discount,
        base=base,
        rate=rate,
        tax=igic(base, rate.rate),
        ipc=ipc,
    )


def emblematic(item: ShopItem) -> bool:
    """Si un artículo es emblemático y no se financia: lo numerado y los títulos."""
    from bot.services.shop_catalog import is_title  # shop_catalog importa este módulo

    return item.stock is not None or is_title(item.catalog_key)


def ineligibility(
    item: ShopItem,
    *,
    now: float,
    level: int,
    bought: int,
    owns_role: bool,
) -> str | None:
    """Por qué un miembro no puede comprar `item`, o `None` si puede.

    Args:
        level: Nivel actual del miembro.
        bought: Compras suyas de este artículo, sin devoluciones.
        owns_role: Si ya tiene, comprado en la tienda, este rol para siempre.
    """
    # Las razas van ocultas del escaparate pero se compran desde el panel de `mascota`.
    if not item.visible and not is_breed(item.catalog_key):
        return "Ese artículo ya no está a la venta."
    if item.kind is Kind.PET and (species := species_of(item.catalog_key)) is not None:
        if not species.adoptable:
            return "Esa mascota no se vende: aparece sola cuando le da la gana."
    if item.sold_out:
        return "Agotado. Llegaste tarde."
    if item.min_level and level < item.min_level:
        return f"Hace falta nivel {item.min_level} y vas por el {level}. ¡A yapear!"
    if item.permanent_role and owns_role:
        return "Ese rol ya es tuyo. Póntelo o quítatelo desde la mochila (`perfil`, 🎒 Objetos)."
    if item.per_user is not None and bought >= item.per_user:
        veces = "vez" if item.per_user == 1 else "veces"
        return f"Solo se puede comprar {item.per_user} {veces} por persona y ya lo hiciste."
    return None


# -- Venta -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Sale:
    """Lo que apunta la tienda al vender, dentro de la transacción del cobro.

    Attributes:
        purchase_id: Fila de la venta (para deshacerla si hace falta).
        invoice: Número de factura del servidor (1, 2, 3…).
        serial: Número de serie de la unidad, si el artículo es limitado.
        edition: Unidades totales de la edición, si es limitado.
        starts_at: Desde cuándo vale (potenciadores en cola).
        expires_at: Hasta cuándo vale; `None` para siempre.
        renewed: Si alarga un alquiler que ya tenía.
        last_unit: Si se ha llevado la última unidad.
        collection: Coleccionables distintos que tiene tras comprar.
    """

    purchase_id: int
    invoice: int
    serial: int | None
    edition: int | None
    starts_at: float
    expires_at: float | None
    renewed: bool
    last_unit: bool
    collection: int


def boost_window(now: float, queue_end: float | None, duration: int) -> tuple[float, float]:
    """Cuándo empieza y acaba un potenciador nuevo.

    Se pone a la cola: si ya hay uno en marcha o esperando, empieza cuando
    acaba el último.

    Args:
        queue_end: Fin del último potenciador del miembro, si aún no ha acabado.
    """
    start = max(now, queue_end or now)
    return start, start + duration


def rental_end(now: float, current_end: float | None, duration: int) -> float:
    """Nuevo fin de un alquiler de rol: se suma a lo que le quede."""
    return max(now, current_end or now) + duration


# -- Carrito ---------------------------------------------------------------------------

#: Unidades de un mismo artículo por compra: diez bombones sí, cien no (un ticket
#: kilométrico no cabe en un mensaje).
MAX_QUANTITY = 10
#: Artículos distintos en un carrito: caben en un desplegable (25) con sitio de sobra.
MAX_CART_LINES = 20


def max_quantity(item: ShopItem, bought: int) -> int:
    """Cuántas unidades de `item` puede llevarse ahora quien ya compró `bought`.

    Un rol para siempre y una mascota solo se tienen una vez; el resto, hasta
    `MAX_QUANTITY`, su límite por persona y lo que quede. `0` si no puede ninguna.
    """
    cap = 1 if item.permanent_role or item.kind is Kind.PET else MAX_QUANTITY
    if item.per_user is not None:
        cap = min(cap, item.per_user - bought)
    if item.remaining is not None:
        cap = min(cap, item.remaining)
    return max(0, cap)


@dataclass(frozen=True, slots=True)
class TicketLine:
    """Una línea del ticket: `qty` unidades de un artículo al precio de `price`.

    El IGIC se calcula por unidad, como se cobra y se apunta cada venta
    (`shop_purchases`), así que la línea es la suma exacta de sus unidades.
    """

    item: ShopItem
    qty: int
    price: Quote

    @property
    def base(self) -> int:
        """Base imponible de la línea."""
        return self.price.base * self.qty

    @property
    def tax(self) -> int:
        """Impuesto de la línea."""
        return self.price.tax * self.qty

    @property
    def total(self) -> int:
        """Lo que se paga por la línea."""
        return self.base + self.tax


def tax_breakdown(lines: Sequence[TicketLine]) -> list[tuple[IgicRate, int, int]]:
    """`(tipo, base, impuesto)` por tipo, de menor a mayor: el desglose de una factura."""
    groups: dict[str, list[int]] = {}
    rates: dict[str, IgicRate] = {}
    for line in lines:
        rate = line.price.rate
        rates[rate.key] = rate
        group = groups.setdefault(rate.key, [0, 0])
        group[0] += line.base
        group[1] += line.tax
    return sorted(
        ((rates[key], base, tax) for key, (base, tax) in groups.items()),
        key=lambda row: (row[0].rate, row[0].key),
    )


def _quantity_lines(
    line: TicketLine, width: int, serials: Sequence[int] = (), edition: int | None = None
) -> list[str]:
    """Lo que ocupa una línea en el ticket: artículo, unidades numeradas y rebaja."""
    price = line.price
    rows = [_ticket_line(f"{line.qty} × {line.item.name}", money(price.price * line.qty), width)]
    if serials and edition is not None:
        numbers = ", ".join(str(serial) for serial in serials)
        noun = "Unidad" if len(serials) == 1 else "Unidades"
        rows.append(f"    {noun} nº {numbers} de {edition}"[:width])
    if price.discount:
        rows.append(
            _ticket_line(
                f"    Rebaja -{price.discount_pct} %", f"-{money(price.discount * line.qty)}", width
            )
        )
    return rows


def _tax_rows(lines: Sequence[TicketLine], width: int) -> list[str]:
    """Base e impuesto; con varios tipos, uno por tipo (`IGIC 7 % s/ 2.000`)."""
    breakdown = tax_breakdown(lines)
    rows = [_ticket_line("Base imponible", money(sum(line.base for line in lines)), width)]
    if len(breakdown) == 1:
        rate, _base, tax = breakdown[0]
        return [*rows, _ticket_line(f"{rate.tax} {rate_label(rate.rate)}", money(tax), width)]
    return rows + [
        _ticket_line(f"{rate.tax} {rate_label(rate.rate)} s/ {money(base)}", money(tax), width)
        for rate, base, tax in breakdown
    ]


def cart_preview(lines: Sequence[TicketLine]) -> str:
    """El ticket antes de pagar: cada línea, el desglose de impuestos y el total."""
    width = 26
    rows: list[str] = []
    for line in lines:
        rows += _quantity_lines(line, width)
    rows += ["─" * width, *_tax_rows(lines, width), "─" * width]
    rows.append(_ticket_line("TOTAL", f"{money(sum(line.total for line in lines))} Y$", width))
    return "```\n" + "\n".join(rows) + "\n```"


def active_multiplier(windows: list[tuple[float, float, int]], now: float) -> float:
    """Multiplicador de XP en `now` según los potenciadores `(inicio, fin, %)`.

    Si por lo que sea se solapan dos, vale el mayor; no se multiplican entre sí.
    """
    best = 100
    for start, end, pct in windows:
        if start <= now < end:
            best = max(best, pct)
    return best / 100


# -- Entrada de los administradores ----------------------------------------------------

_SPAN_UNITS = {
    "m": 60,
    "min": 60,
    "h": 3600,
    "d": 86400,
    "dia": 86400,
    "dias": 86400,
    "s": 7 * 86400,
    "sem": 7 * 86400,
    "semanas": 7 * 86400,
    "semana": 7 * 86400,
}
_SPAN_PART = re.compile(r"(\d+)([a-z]*)")
_FOREVER = {"", "0", "permanente", "siempre", "parasiempre", "nunca", "no", "-"}


def _plain(text: str) -> str:
    """Minúsculas, sin tildes ni espacios: `Días` → `dias`."""
    decomposed = unicodedata.normalize("NFKD", text.strip().lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).replace(" ", "")


def parse_span(text: str | None, default_unit: str = "d") -> int | None:
    """Convierte `7d`, `12h`, `1d12h`, `2 semanas` o `3` en segundos.

    Un número sin unidad usa `default_unit` (días para roles, horas para
    potenciadores). Vacío o `permanente` es para siempre.

    Returns:
        Segundos, o `None` si es para siempre.

    Raises:
        ValueError: Formato no válido o fuera de 1 hora a 1 año.
    """
    cleaned = _plain(text or "")
    if cleaned in _FOREVER:
        return None
    parts = _SPAN_PART.findall(cleaned)
    if not parts or _SPAN_PART.sub("", cleaned):
        raise ValueError(f"No entiendo `{text}` como duración. Prueba `7d`, `12h` o `permanente`.")
    seconds = 0
    for amount, unit in parts:
        size = _SPAN_UNITS.get(unit or default_unit)
        if size is None:
            raise ValueError(f"No conozco la unidad `{unit}`. Usa `h`, `d` o `sem`.")
        seconds += int(amount) * size
    if not MIN_SPAN <= seconds <= MAX_SPAN:
        raise ValueError("La duración tiene que ir de 1 hora a 1 año.")
    return seconds


def format_span(seconds: int | None) -> str:
    """`604800` → `7 días`; `129600` → `1 día y 12 h`; `None` → `para siempre`."""
    if seconds is None:
        return "para siempre"
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    parts = []
    if days:
        parts.append(f"{days} día" + ("s" if days != 1 else ""))
    if hours:
        parts.append(f"{hours} h")
    if minutes and not days:
        parts.append(f"{minutes} min")
    if not parts:
        return "menos de 1 min"
    return " y ".join([", ".join(parts[:-1]), parts[-1]]) if len(parts) > 1 else parts[0]


def parse_multiplier(text: str) -> int:
    """`x2`, `×1,5`, `2`, `150%` → tanto por cien (200, 150, 200, 150).

    Raises:
        ValueError: Si no se entiende o está fuera de ×1,1 a ×3.
    """
    cleaned = _plain(text).lstrip("x×*").replace(",", ".")
    try:
        if cleaned.endswith("%"):
            pct = round(float(cleaned[:-1]))
        else:
            pct = round(float(cleaned) * 100)
    except ValueError:
        raise ValueError(f"No entiendo `{text}` como multiplicador. Prueba `x2` o `1,5`.") from None
    if not MIN_BOOST <= pct <= MAX_BOOST:
        raise ValueError(
            f"El multiplicador va de {format_multiplier(MIN_BOOST)} "
            f"a {format_multiplier(MAX_BOOST)}."
        )
    return pct


def format_multiplier(pct: int) -> str:
    """`200` → `×2`; `150` → `×1,5`."""
    text = f"{pct / 100:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"×{text}"


def parse_price(text: str) -> int:
    """Precio de catálogo: `5000`, `5.000`, `5k`, `1,5m`.

    Raises:
        ValueError: Si no es un precio válido.
    """
    cleaned = text.strip().lower().replace(" ", "")
    match = re.fullmatch(r"(\d+(?:[.,]\d+)?)([km]?)", cleaned)
    if match is None:
        raise ValueError(f"No entiendo `{text}` como precio. Prueba `5000` o `5k`.")
    number, suffix = match.groups()
    if suffix:
        value = round(float(number.replace(",", ".")) * {"k": 1_000, "m": 1_000_000}[suffix])
    elif re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", number):
        value = int(re.sub(r"[.,]", "", number))
    elif number.isdigit():
        value = int(number)
    else:
        raise ValueError(f"No entiendo `{text}` como precio. Prueba `5000` o `5k`.")
    if not 1 <= value <= MAX_PRICE:
        raise ValueError("El precio tiene que ser de al menos 1 Y$.")
    return value


def parse_limit(text: str | None, what: str, maximum: int) -> int | None:
    """Número opcional (existencias, límite por persona): vacío es sin límite.

    Raises:
        ValueError: Si no es un número de 1 a `maximum`.
    """
    cleaned = _plain(text or "")
    if cleaned in _FOREVER | {"ilimitado", "sinlimite", "infinito"}:
        return None
    if not cleaned.isdigit() or not 1 <= int(cleaned) <= maximum:
        raise ValueError(f"{what} tiene que ser un número de 1 a {maximum:,} o quedar vacío.")
    return int(cleaned)


def parse_level(text: str | None) -> int:
    """Nivel mínimo: vacío o 0 es cualquiera.

    Raises:
        ValueError: Si no es un número de 0 a `MAX_LEVEL`.
    """
    cleaned = _plain(text or "") or "0"
    if not cleaned.isdigit() or int(cleaned) > MAX_LEVEL:
        raise ValueError(f"El nivel mínimo tiene que ser un número de 0 a {MAX_LEVEL}.")
    return int(cleaned)


def parse_discount(text: str | None) -> int:
    """Rebaja en %: `20`, `20%`, vacío o `0` para quitarla.

    Raises:
        ValueError: Si no es un número de 0 a `MAX_DISCOUNT`.
    """
    cleaned = _plain(text or "").rstrip("%") or "0"
    if not cleaned.isdigit() or int(cleaned) > MAX_DISCOUNT:
        raise ValueError(f"La rebaja va de 0 a {MAX_DISCOUNT} %.")
    return int(cleaned)


def clean_text(text: str | None, limit: int, *, what: str, required: bool = True) -> str:
    """Recorta espacios y comprueba el largo de un nombre o una descripción.

    Raises:
        ValueError: Si es obligatorio y está vacío, o si se pasa de `limit`.
    """
    cleaned = " ".join((text or "").split())
    if required and not cleaned:
        raise ValueError(f"{what} no puede quedar vacío.")
    if len(cleaned) > limit:
        raise ValueError(f"{what} puede tener como mucho {limit} caracteres.")
    return cleaned


_CUSTOM_EMOJI = re.compile(r"<a?:\w{2,32}:\d{15,20}>")


def split_emoji(text: str | None, kind: Kind) -> tuple[str, str]:
    """Separa el emoji del principio de un nombre: `🛥️ Yate` → `("🛥️", "Yate")`.

    Así los administradores escriben el emoji y el nombre en un solo campo.
    Si no empieza por un emoji, se usa el de la sección.

    Returns:
        `(emoji, nombre)`. El nombre puede quedar vacío; lo valida `clean_text`.
    """
    cleaned = (text or "").strip()
    first, _, rest = cleaned.partition(" ")
    is_emoji = bool(first) and (
        _CUSTOM_EMOJI.fullmatch(first) is not None or not any(c.isalnum() for c in first)
    )
    if is_emoji and len(first) <= MAX_EMOJI:
        return first, rest.strip()
    return kind.icon, cleaned


# -- Texto -----------------------------------------------------------------------------


def rate_label(rate: float) -> str:
    """`0.07` → `7 %`; `0.095` → `9,5 %`."""
    return f"{rate * 100:.2f}".rstrip("0").rstrip(".").replace(".", ",") + " %"


def money(amount: int) -> str:
    """`4280` → `4.280`, sin símbolo (para alinear columnas en el ticket)."""
    return f"{amount:,}".replace(",", ".")


def invoice_code(number: int, when: float) -> str:
    """Número de factura legible: `T2026-000042`."""
    year = datetime.fromtimestamp(when, TIMEZONE).year
    return f"T{year}-{number:06d}"


_TICKET_WIDTH = 30


def _ticket_line(left: str, right: str, width: int = _TICKET_WIDTH) -> str:
    room = width - len(right) - 1
    if len(left) > room:
        left = left[: max(1, room - 1)] + "…"
    return f"{left:<{room}} {right}"


def receipt(
    item: ShopItem,
    price: Quote,
    *,
    invoice: int,
    when: float,
    buyer: str,
    serial: int | None = None,
    edition: int | None = None,
) -> str:
    """Factura simplificada de una sola unidad (ver `cart_receipt`)."""
    return cart_receipt(
        [TicketLine(item, 1, price)],
        invoices=[invoice],
        when=when,
        buyer=buyer,
        serials={item.id: [serial]} if serial is not None else None,
        editions={item.id: edition} if edition is not None else None,
    )


def cart_receipt(
    lines: Sequence[TicketLine],
    *,
    invoices: Sequence[int],
    when: float,
    buyer: str,
    serials: Mapping[int, Sequence[int]] | None = None,
    editions: Mapping[int, int] | None = None,
) -> str:
    """Factura simplificada en un bloque de código, como un ticket de caja.

    Cada unidad es una venta con su número (`shop_purchases`), así que una compra
    de varias lleva el primero y el último, que son correlativos. Con varios tipos
    de impuesto, el impuesto va desglosado por tipo, como manda una factura.

    El artículo es contenido digital que se entrega al momento, así que no
    hay derecho de desistimiento (art. 103.m del Real Decreto Legislativo
    1/2007, de consumidores).

    Args:
        serials: Números de serie por artículo (`id`), si es de edición limitada.
        editions: Unidades de la edición de esos artículos.
    """
    rule = "─" * _TICKET_WIDTH
    moment = datetime.fromtimestamp(when, TIMEZONE)
    number = invoice_code(min(invoices), when)
    if len(invoices) > 1:
        number += f" a {max(invoices):06d}"
    rows = [
        "FACTURA SIMPLIFICADA",
        f"Nº {number}",
        SHOP_NAME,
        f"{moment:%d/%m/%Y %H:%M} · {buyer}"[:_TICKET_WIDTH],
        rule,
    ]
    for line in lines:
        rows += _quantity_lines(
            line,
            _TICKET_WIDTH,
            (serials or {}).get(line.item.id, ()),
            (editions or {}).get(line.item.id),
        )
    rows += [
        *_tax_rows(lines, _TICKET_WIDTH),
        rule,
        _ticket_line("TOTAL", f"{money(sum(line.total for line in lines))} Y$"),
        rule,
        "Contenido digital entregado:",
        "sin desistimiento (art. 103.m",
        "TRLGDCU). Gracias.",
    ]
    return "```\n" + "\n".join(rows) + "\n```"
