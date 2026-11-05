"""Reglas de las cadenas de negocios de `pala` (fase 5, `docs/negocios.md`).

Lógica pura, sin Discord ni base de datos. Un miembro puede llevar varios locales
(`business.MAX_LOCALES`), y con ellos llega lo de este módulo:

- **La nave** (`Nave`): un almacén alquilado en un barrio de `pisos`, o una nave entera suya,
  donde llegan las importaciones y de donde se reparte a los locales de su isla. Paga cada
  noche su alquiler y sus suministros como un local (`nave_bill`).
- **Importar** (`import_quote`): un contenedor de `CONTAINER_LOTS` lotes de un producto,
  directo del fabricante de la Península, más barato que el mayorista pero con su flete, el
  AIEM, el IGIC de la importación y la tasa del agente de aduanas. Llega a las
  `IMPORT_NIGHTS` noches. Solo lo que no caduca: un contenedor dura semanas.
- **El gerente** (`restock`): un empleado con el puesto de gerente que no atiende, pero cada
  noche repone el stock de su local (de la nave primero y luego del mayorista) y cubre las
  bajas de la plantilla (`business_staff`).
- **Vender la sociedad** (`sale_price`): el comprador paga por las participaciones lo que
  marca el art. 37.1.b LIRPF.

Las cifras van a la escala de los negocios (`taxes.YAPDOLLARS_PER_EURO`, 10 Y$ el euro, como
el alquiler de los locales).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from bot.services.business_catalog import SECTOR_BY_KEY, Product
from bot.services.housing_catalog import BARRIO_BY_KEY, KINDS, Barrio
from bot.services.taxes import IGIC_BY_KEY, IGIC_GENERAL_RATE, YAPDOLLARS_PER_EURO, igic

# -- La nave ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NaveSize:
    """Un tamaño de nave.

    Attributes:
        key: Clave estable.
        name: Cómo se enseña.
        m2: Metros (dan el alquiler, como en `pisos`).
        lots: Lotes que caben.
        utilities: Suministros de cada noche (la luz y el agua de un almacén), base.
    """

    key: str
    name: str
    m2: int
    lots: int
    utilities: int


NAVE_SIZES: tuple[NaveSize, ...] = (
    NaveSize("pequena", "Pequeña", 100, 50, 300),
    NaveSize("mediana", "Mediana", 200, 110, 500),
    NaveSize("grande", "Grande", 400, 240, 800),
)
#: Tasa municipal de la declaración responsable del almacén (art. 20.4.i TRLRHL). Un almacén
#: sin mercancía peligrosa es actividad inocua y la Ley 7/2011 de Canarias no le pide
#: comunicación previa (art. 2.3.c), pero las ordenanzas suelen pedir declaración responsable.
NAVE_LICENSE = 5_000
#: Fianza de la nave, en mensualidades (art. 36.1 LAU, uso distinto de vivienda).
NAVE_DEPOSIT_MONTHS = 2
#: Noches sin pagar tras las que el casero de la nave rescinde (como el de un local).
NAVE_EVICTION_NIGHTS = 3
#: Lo que vale el stock de la nave si se cierra (el mayorista lo recompra a la mitad).
NAVE_LIQUIDATION = 0.5
#: Desde qué parte de los metros de un tamaño cabe en una nave propia de `pisos`.
NAVE_FIT = 1.0

# -- Importar ---------------------------------------------------------------------------

#: Lotes de un contenedor (todos del mismo producto).
CONTAINER_LOTS = 20
#: Lo que cobra el fabricante frente al mayorista (sin su margen).
IMPORT_SHARE = 0.7
#: Flete marítimo de un contenedor desde la Península (entra en el valor en aduana).
FREIGHT = 4_000
#: Lo que cobra el agente de aduanas por el despacho (DUA), base sin IGIC.
CUSTOMS_FEE = 1_000
#: Noches que tarda en llegar: sale del puerto el día que se pide y está en la nave a la
#: mañana siguiente de la segunda noche.
IMPORT_NIGHTS = 2
#: AIEM (Arbitrio sobre Importaciones y Entregas de Mercancías en las Islas Canarias, art. 92
#: y anexo 2.º del Decreto Legislativo 1/2025 de Canarias, vigente en 2026): grava la entrada
#: de lo que también se produce en las islas. Por producto, de su partida del anexo: el café
#: tostado (0901.21) al 15 %; los refrescos y bebidas con azúcar (22.02) al 5 %; los
#: detergentes (34.02) al 5 %; las pinturas al agua (32.09) al 10 %. Las conservas de pescado,
#: los recambios, la tornillería, las bombillas y las herramientas no están en el anexo; el
#: aceite de motor salió en 2026 (Ley 9/2025). Interpretaciones del proyecto: el cortado y el
#: barraquito, como el café; el granizado, como un refresco; la pintura, al agua; la
#: fontanería y las cerraduras, fuera del anexo. Solo se importa lo que no caduca: los helados
#: (21.05, al 15 %) y la leche no llegan a importarse.
AIEM: dict[str, float] = {
    "cafe": 0.15, "cortado": 0.15, "barraquito": 0.15, "bebidas": 0.05, "limpieza": 0.05,
    "granizado": 0.05, "pintura": 0.10,
}  # fmt: skip


@dataclass(slots=True)
class Nave:
    """La nave de un miembro (una fila de `business_nave`; todo va en JSON).

    Attributes:
        user_id: Su dueño.
        barrio: Clave del barrio de `pisos` (la isla dice a qué locales reparte).
        size: Índice de `NAVE_SIZES`.
        own_listing: Clave de la nave propia de `pisos` (`""` = alquilada).
        opened_at: Cuándo se abrió (epoch).
        deposit: Fianza que espera en el depósito de fianzas.
        rent_debt: Lo que se debe al casero.
        unpaid_nights: Noches seguidas sin pagar entero.
        billed_day: Último día (ISO) cuya noche ya se cobró.
        stock: Unidades por producto.
        orders: Contenedores en camino: `{"id", "product", "units", "arrives"}` (`arrives`,
            el día ISO de la noche en que llegan).
        seq: El número del último contenedor pedido.
        week: Lunes ISO de `week_totals`.
        week_totals: Sus cuentas de la semana (`compras`, `local`, `apertura`, `ventas`).
        last_week: Las de la semana anterior.
        containers: Contenedores que han llegado en total.
        news: Lo que ha pasado (se cuenta en el panel de su primer local).
    """

    user_id: int
    barrio: str
    size: int = 0
    own_listing: str = ""
    opened_at: float = 0.0
    deposit: int = 0
    rent_debt: int = 0
    unpaid_nights: int = 0
    billed_day: str = ""
    stock: dict[str, int] = field(default_factory=dict)
    orders: list[dict[str, Any]] = field(default_factory=list)
    seq: int = 0
    week: str = ""
    week_totals: dict[str, int] = field(default_factory=dict)
    last_week: dict[str, int] = field(default_factory=dict)
    containers: int = 0
    news: list[str] = field(default_factory=list)

    @property
    def barrio_info(self) -> Barrio:
        """Su barrio de `pisos`."""
        return BARRIO_BY_KEY[self.barrio]

    @property
    def island(self) -> str:
        """Su isla: solo reparte a los locales de la misma."""
        return self.barrio_info.island

    @property
    def size_info(self) -> NaveSize:
        """Su tamaño."""
        return NAVE_SIZES[max(0, min(len(NAVE_SIZES) - 1, self.size))]

    def roll_over(self, week: str) -> None:
        """Pasa la semana de sus cuentas si ha cambiado (idempotente)."""
        if self.week != week:
            if self.week:
                self.last_week = dict(self.week_totals)
            self.week = week
            self.week_totals = {}

    def add(self, item: str, amount: int) -> None:
        """Suma `amount` a una partida de las cuentas de la semana."""
        if amount:
            self.week_totals[item] = self.week_totals.get(item, 0) + amount

    def tell(self, line: str) -> None:
        """Apunta un aviso (los cinco últimos)."""
        self.news = [*self.news, line][-5:]


def product_by_key(key: str) -> Product | None:
    """Un producto de cualquier sector."""
    for sector in SECTOR_BY_KEY.values():
        if (item := sector.product(key)) is not None:
            return item
    return None


def lots_in(units: dict[str, int]) -> int:
    """Lotes que ocupan unas unidades (un lote empezado ocupa entero)."""
    total = 0
    for key, count in units.items():
        item = product_by_key(key)
        if item is not None and count > 0:
            total += math.ceil(count / item.pack)
    return total


def used_lots(nave: Nave) -> int:
    """Lo que ocupa lo que hay y lo que viene en camino."""
    coming: dict[str, int] = {}
    for order in nave.orders:
        coming[order["product"]] = coming.get(order["product"], 0) + order["units"]
    return lots_in(nave.stock) + lots_in(coming)


def nave_rent(barrio: Barrio, size: NaveSize) -> int:
    """Alquiler de cada noche de una nave (un mes del contrato), base sin IGIC, como una nave
    industrial de `pisos` con esos metros."""
    euros = size.m2 * barrio.rent_m2 * KINDS["nave"].rent_factor
    return int(round(euros * YAPDOLLARS_PER_EURO, -1))


def nave_size_for(m2: int) -> int | None:
    """El tamaño (índice de `NAVE_SIZES`) de una nave propia de `m2` metros, o `None`."""
    fits = [i for i, size in enumerate(NAVE_SIZES) if m2 >= size.m2 * NAVE_FIT]
    return fits[-1] if fits else None


@dataclass(frozen=True, slots=True)
class NaveQuote:
    """Lo que cuesta abrir una nave.

    Attributes:
        license: Tasa de la declaración responsable (al Estado).
        deposit: Fianza (al depósito de fianzas; vuelve al dejarla).
        rent: Alquiler de cada noche, base.
    """

    license: int
    deposit: int
    rent: int

    @property
    def total(self) -> int:
        """Lo que sale al abrirla."""
        return self.license + self.deposit


def nave_quote(barrio: Barrio, size: int, *, own: bool = False) -> NaveQuote:
    """Lo que cuesta abrir una nave de tamaño `size` en `barrio` (propia: sin alquiler ni
    fianza)."""
    rent = 0 if own else nave_rent(barrio, NAVE_SIZES[size])
    return NaveQuote(NAVE_LICENSE, NAVE_DEPOSIT_MONTHS * rent, rent)


@dataclass(frozen=True, slots=True)
class NaveBill:
    """El recibo de una noche de la nave.

    Attributes:
        rent: Alquiler, base.
        rent_igic: Su IGIC.
        withholding: Retención del IRPF del arrendador (art. 100 RIRPF), que sale de la base.
        utilities: Suministros, base.
        utilities_igic: Su IGIC.
    """

    rent: int
    rent_igic: int
    withholding: int
    utilities: int
    utilities_igic: int

    @property
    def total(self) -> int:
        """Lo que cuesta la noche entera."""
        return self.rent + self.rent_igic + self.utilities + self.utilities_igic


def nave_bill(nave: Nave) -> NaveBill:
    """Lo que cuesta una noche de la nave (sin alquiler si es suya)."""
    base = 0 if nave.own_listing else nave_rent(nave.barrio_info, nave.size_info)
    utilities = nave.size_info.utilities
    return NaveBill(base, igic(base), round(base * 0.19), utilities, igic(utilities))


def stock_value(nave: Nave) -> int:
    """Lo que costó al mayorista el stock de la nave (sin IGIC)."""
    return sum(
        item.cost * units
        for key, units in nave.stock.items()
        if (item := product_by_key(key)) is not None
    )


@dataclass(frozen=True, slots=True)
class ImportQuote:
    """Lo que cuesta un contenedor de un producto, frente a comprarlo al mayorista.

    Attributes:
        product: El producto.
        units: Unidades del contenedor.
        goods: Lo que cobra el fabricante, base.
        freight: El flete.
        aiem: El AIEM, sobre el valor en aduana (mercancía y flete).
        igic: El IGIC de la importación, sobre el valor en aduana más el AIEM (al tipo de la
            entrega del mismo bien).
        customs: La tasa del agente de aduanas, base.
        customs_igic: Su IGIC (general).
        wholesale: Lo que costarían esas unidades al mayorista, con su IGIC.
    """

    product: Product
    units: int
    goods: int
    freight: int
    aiem: int
    igic: int
    customs: int
    customs_igic: int
    wholesale: int

    @property
    def total(self) -> int:
        """Lo que se paga por el contenedor."""
        return self.goods + self.freight + self.aiem + self.igic + self.customs + self.customs_igic

    @property
    def saving(self) -> int:
        """Lo que se ahorra frente al mayorista (negativo si sale más caro)."""
        return self.wholesale - self.total


def can_import(product: Product) -> bool:
    """Si se puede importar: lo que caduca no aguanta las semanas que dura un contenedor."""
    return not product.spoil


def import_quote(product: Product) -> ImportQuote:
    """Lo que cuesta un contenedor de `product`.

    El valor en aduana es la mercancía con su flete; el AIEM va sobre él (art. 94 del DL
    1/2025: el mismo tipo en la importación que en la entrega) y el IGIC de la importación,
    al tipo de la entrega del bien (art. 32.4), sobre el valor en aduana más el AIEM. El
    pequeño empresario no repercute IGIC en sus ventas pero sí paga el de sus importaciones
    (art. 91.b), y no lo deduce: es coste.
    """
    units = product.pack * CONTAINER_LOTS
    goods = int(round(product.cost * units * IMPORT_SHARE, -1))
    value = goods + FREIGHT
    aiem = round(value * AIEM.get(product.key, 0.0))
    rate = IGIC_BY_KEY.get(product.igic, IGIC_BY_KEY["general"]).rate
    tax = igic(value + aiem, rate)
    base = product.cost * units
    return ImportQuote(
        product, units, goods, FREIGHT, aiem, tax, CUSTOMS_FEE, igic(CUSTOMS_FEE),
        base + igic(base, rate),
    )  # fmt: skip


def import_base(quote: ImportQuote) -> int:
    """Lo que desaparece de un contenedor (fabricante, naviera y agente): el resto son
    impuestos."""
    return quote.goods + quote.freight


#: El IGIC general, para los textos.
IGIC_RATE = IGIC_GENERAL_RATE


# -- El gerente: reponer ----------------------------------------------------------------

#: Días de lo vendido que el gerente deja en el almacén del local.
RESTOCK_DAYS = 1.5
#: Lotes como poco de cada producto (para que haya algo que vender aunque ayer no se vendiera).
RESTOCK_MIN_LOTS = 1


def restock_target(product: Product, sold: int, days: float = RESTOCK_DAYS) -> int:
    """Unidades que el gerente quiere tener de `product` si ayer se vendieron `sold` (para
    `days` días; el mozo de almacén repone lo fresco para uno)."""
    return max(product.pack * RESTOCK_MIN_LOTS, math.ceil(sold * days))


@dataclass(frozen=True, slots=True)
class Restock:
    """Lo que repone el gerente de un producto una noche.

    Attributes:
        product: El producto.
        from_nave: Unidades que trae de la nave.
        lots: Lotes que compra al mayorista.
    """

    product: Product
    from_nave: int
    lots: int


def restock(
    stock: dict[str, int],
    products: tuple[Product, ...],
    sold: dict[str, int],
    nave_stock: dict[str, int] | None,
    budget: int,
    *,
    price: Any,
    days: float = RESTOCK_DAYS,
) -> list[Restock]:
    """Lo que repone el gerente: hasta el objetivo de cada producto (`restock_target`, para
    `days` días), de la nave primero (gratis) y lo que falte del mayorista, mientras llegue
    `budget`.

    Va de lo que más se vende a lo que menos. `price(product, units)` da lo que cuestan esas
    unidades al mayorista con su IGIC. No cambia nada: devuelve el plan.
    """
    plan: list[Restock] = []
    left = dict(nave_stock or {})
    money = budget
    for item in sorted(products, key=lambda p: -sold.get(p.key, 0)):
        have = stock.get(item.key, 0)
        need = restock_target(item, sold.get(item.key, 0), days) - have
        if need <= 0:
            continue
        take = min(need, left.get(item.key, 0))
        left[item.key] = left.get(item.key, 0) - take
        need -= take
        lots = math.ceil(need / item.pack) if need > 0 else 0
        while lots and price(item, lots * item.pack) > money:
            lots -= 1
        if lots:
            money -= price(item, lots * item.pack)
        if take or lots:
            plan.append(Restock(item, take, lots))
    return plan


# -- Vender la sociedad -----------------------------------------------------------------

#: Capitalizar al 20 % un resultado del año es multiplicarlo por 5 (art. 37.1.b LIRPF). En el
#: juego el ejercicio es la semana.
CAPITALIZE = 5


@dataclass(frozen=True, slots=True)
class SaleOffer:
    """Lo que paga un comprador por las participaciones de la sociedad.

    Attributes:
        equity: El patrimonio neto.
        results: Los resultados de los últimos ejercicios cerrados (semanas de Sociedades).
        capitalized: Su media capitalizada al 20 %.
        price: Lo que paga: el mayor de los dos (art. 37.1.b LIRPF).
        loan: El préstamo del socio, que el comprador le paga por su valor.
        basis: Lo que el socio metió (capital y aportaciones): su valor de adquisición.
        blocker: Por qué no hay comprador, o `None`.
    """

    equity: int
    results: tuple[int, ...]
    capitalized: int
    price: int
    loan: int
    basis: int
    blocker: str | None

    @property
    def gain(self) -> int:
        """La ganancia patrimonial (negativa si pierde)."""
        return self.price - self.basis


def sale_offer(company: dict[str, Any], equity: int, *, weeks: int, debts: int) -> SaleOffer:
    """Lo que daría vender ahora la sociedad.

    El valor de las participaciones que no cotizan no puede bajar del mayor entre el
    patrimonio neto y la media de los resultados de los tres últimos ejercicios cerrados
    capitalizada al 20 % (art. 37.1.b LIRPF; se cuentan como beneficio los dividendos y lo
    que fue a reservas). El comprador del juego paga justo eso.

    Args:
        weeks: Cuántos ejercicios cerrados hacen falta (`business_sl.SALE_WEEKS`).
        debts: Lo que la sociedad debe a Hacienda, al casero y a la plantilla: con deudas
            pendientes, nadie la compra sin rebaja (el juego no negocia).
    """
    results = tuple(int(r) for r in company.get("results", []))[-weeks:]
    average = sum(results) / len(results) if results else 0.0
    capitalized = max(0, round(average * CAPITALIZE))
    price = int(round(max(equity, capitalized), -2))
    blocker = None
    if len(results) < weeks:
        blocker = f"Un comprador quiere ver {weeks} semanas de cuentas cerradas en Sociedades."
    elif debts > 0:
        blocker = "Tiene deudas pendientes (Hacienda, el casero o la plantilla): págalas antes."
    elif price <= 0:
        blocker = "Nadie paga por una sociedad que pierde dinero y no tiene patrimonio."
    return SaleOffer(
        equity, results, capitalized, max(0, price), int(company.get("loan", 0)),
        int(company.get("contributed", 0)), blocker,
    )  # fmt: skip
