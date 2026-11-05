"""Reglas de los negocios de `pala`: el local, la demanda, la reputación y las cuentas.

Lógica pura, sin Discord ni base de datos. El catálogo está en
`bot.services.business_catalog`, los minijuegos en `bot.services.business_games`, la
persistencia en `bot.repositories.business` y los casos de uso (y el dinero) en
`bot.services.negocio`. El diseño entero, con sus fases, en `docs/negocios.md`.

En resumen:

- **Un negocio por miembro** (fase 1), de autónomo: no tiene caja propia, todo sale y
  entra del monedero de su dueño («respondes con lo tuyo»). Desde la fase 5, hasta
  `MAX_LOCALES` (una cadena), cada uno en su local.
- **El local** se alquila en un barrio de `pisos`: su alquiler de cada noche sale de los
  metros del sector, el `rent_m2` del barrio y el `rent_factor` del tipo de inmueble, a la
  misma escala que la renta de un local en `pisos` (`rent`). La fianza son dos
  mensualidades (art. 36.1 LAU; un día del bot es un mes del contrato, como en `pisos`).
- **La demanda es artificial y limitada** (`turn_clients`, `daily_pool`): los clientes de
  un turno dependen del sector, el tema del barrio, la hora canaria, las estrellas y el
  precio; el barrio tiene un cupo diario por sector que comparten todos los negocios de
  ese sector en él.
- **Reputación** de 0 a 100 (`stars`, de 1 a 5): la mueven los clientes de cada turno
  (`REPUTATION`) y se olvida un poco cada noche sin abrir (`REPUTATION_DRIFT`).
- **Stock** por producto: lo perecedero se pierde en parte cada noche (`spoil`).
- **Cada noche** se pagan el alquiler y los suministros (`nightly_bill`); lo que no llega
  queda como deuda del local y, a las `EVICTION_NIGHTS` noches sin pagar, el casero
  rescinde el contrato.

Fase 2 (9.5.0, «crecer el local»):

- **Tamaño** (`SIZES`): un local mayor cuesta más de alquiler y suministros, pero caben más
  clientes por turno y le toca más cupo del barrio. Un local propio de `pisos`
  (`own_listing`) no paga alquiler ni fianza; su tamaño sale de sus metros (`size_for`).
- **Mejoras** (`EQUIPMENT`): suben los clientes, la paciencia o la propina, o bajan la
  merma. Se amortizan cada noche (`amortize`), como el acondicionamiento del local.
- **Campañas** (`CAMPAIGNS`): unos días con más clientes y más cupo.
- **Precio por producto** (`prices`): cada producto puede tener su nivel de precios.
- **Reseñas** (`review_of`): las dejan algunos clientes; con buena media y bastantes, el
  negocio sale «recomendado» (`RECOMMENDED_BOOST`).
- **Cerrar** con traspaso o liquidando lo que queda (`closing_offer`), y **trasladarse**
  (`move_quote`).
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from bot.services.business_catalog import (
    AMORTIZATION,
    CAMPAIGN_BY_KEY,
    EMBLEMS,
    EQUIPMENT_BY_KEY,
    PALETTES,
    PRICE_BY_KEY,
    REGULAR_NAMES,
    REGULARS_PER_BUSINESS,
    REVIEW_LINES,
    REVIEWERS,
    SECTOR_BY_KEY,
    SETUP_CLASS,
    SIZE_FROM,
    SIZES,
    Equipment,
    PriceLevel,
    Product,
    Sector,
    Size,
)
from bot.services.housing_catalog import BARRIO_BY_KEY, KINDS, Barrio
from bot.services.taxes import IGIC_BY_KEY, IGIC_GENERAL_RATE, YAPDOLLARS_PER_EURO, igic

# -- Constantes ----------------------------------------------------------------------

#: Largo máximo del nombre de un negocio.
NAME_MAX = 24
#: Avisos que se guardan para enseñar en la portada.
NEWS_MAX = 5
#: Reputación al abrir y sus límites.
REPUTATION_START = 50.0
REPUTATION_MAX = 100.0
#: Lo que se acerca la reputación a `REPUTATION_START` cada noche sin abrir (si está por
#: encima): la clientela olvida.
REPUTATION_DRIFT = 1.0
#: Turnos de clientes base que da de sí un barrio al día por sector (el cupo de la
#: demanda): ni con el mejor negocio se saca más de un barrio.
DAILY_POOL_TURNS = 8
#: Lo que suman como mucho la campaña y el community manager a los clientes y al cupo.
BOOST_CAP = 0.5
#: Noches sin pagar el alquiler tras las que el casero rescinde el contrato.
EVICTION_NIGHTS = 3
#: Fianza del local en mensualidades (art. 36.1 de la Ley 29/1994, uso distinto de vivienda).
DEPOSIT_MONTHS = 2
#: Retención del IRPF del arrendador de un local (art. 100 RIRPF): la ingresa el inquilino.
RENT_WITHHOLDING = 0.19
#: Días desde el alta con la tarifa plana de autónomos (art. 38 ter de la Ley 20/2007, «12
#: meses»: la semana es el año fiscal).
FLAT_RATE_DAYS = 7
#: Probabilidad de que un cliente sea uno de tus habituales.
REGULAR_CHANCE = 0.3
#: Fidelidad máxima de un habitual y desde la que pide «lo de siempre» sin decir qué.
LOYALTY_MAX = 10
USUAL_LOYALTY = 3

#: Lo que mueve la reputación cada cosa que pasa con un cliente (`business_games`). Con
#: `docs/negocio_sim.py`, un jugador bueno (falla 1 de cada 8) a 4 turnos al día llega a las
#: 5 estrellas en unas tres semanas; uno flojo (falla 1 de cada 3) se hunde.
REPUTATION: dict[str, float] = {
    "fast": 0.15,
    "ok": 0.1,
    "slow": 0.03,
    "wrong": -0.5,
    "left": -0.3,
    "short": -0.6,
    "over": 0.0,
    "caught": 0.3,
    "missed_thief": 0.0,
    "false_accuse": -2.5,
    "bad_diagnosis": -0.6,
    "rejected": -0.05,
    "no_part": -0.2,
    "cheap_quote": 0.1,
}
#: Clientes de un turno de referencia: la reputación de un turno se escala a ellos, así un
#: sector con pocos clientes por turno (el taller) sube y baja al mismo ritmo por turno.
REPUTATION_CLIENTS = 8

#: Fase 2. Reseñas: probabilidad de que deje una quien se atendió y quien se fue sin
#: atender (el enfadado escribe más), y cuántas cuentan en la media.
REVIEW_CHANCE = 0.25
REVIEW_ANGRY_CHANCE = 0.5
REVIEW_WINDOW = 20
#: «Recomendado»: con esta media y estas reseñas, un 10 % más de clientes.
RECOMMENDED_RATING = 4.5
RECOMMENDED_REVIEWS = 10
RECOMMENDED_BOOST = 0.10
#: Días que tiene que llevar abierto un negocio para que alguien quiera traspasarlo, y las
#: estrellas mínimas: nadie compra un negocio hundido.
TRASPASO_MIN_DAYS = 7
TRASPASO_MIN_STARS = 3.0
#: El fondo de comercio de un traspaso: estos días de lo que dejó de media la semana pasada,
#: por las estrellas sobre cinco.
GOODWILL_DAYS = 3
#: Sin traspaso, el mayorista recompra el stock a esta parte de lo que costó y las mejoras
#: se venden de segunda mano a esta parte de lo que queda por amortizar.
LIQUIDATION_STOCK = 0.5
LIQUIDATION_EQUIPMENT = 0.4
#: Al cambiar de barrio, la reputación se acerca a la de partida a la mitad y los habituales
#: pierden la mitad de su fidelidad: te conocen, pero les pilla lejos.
MOVE_KEEP = 0.5
#: Fase 5, las cadenas: locales que puede llevar un miembro a la vez (cada uno es un
#: `Business`). Si es una sociedad, la sociedad los lleva todos.
MAX_LOCALES = 4
#: Días de lo vendido por producto que se guardan (`Business.sold_days`).
SOLD_DAYS = 3


def stars(reputation: float) -> float:
    """Estrellas de 1 a 5, de media en media, de una reputación de 0 a 100."""
    return round((1 + 4 * max(0.0, min(REPUTATION_MAX, reputation)) / REPUTATION_MAX) * 2) / 2


def turn_reputation(sector: Sector, raw: float, served: int, per_client: float) -> float:
    """Lo que mueve la reputación un turno: lo de sus clientes escalado a `REPUTATION_CLIENTS`,
    más lo de los precios por cada cliente atendido (`price_reputation`)."""
    return (raw + per_client * served) * REPUTATION_CLIENTS / sector.clients


def reputation_factor(reputation: float) -> float:
    """Lo que multiplica la clientela la reputación: 1 con 3 estrellas, 4/3 con 5."""
    return 0.5 + stars(reputation) / 6


# -- El negocio ------------------------------------------------------------------------


@dataclass(slots=True)
class Business:
    """Un negocio (una fila de `business`; casi todo va en JSON).

    Attributes:
        id: Número del negocio (lo pone la base de datos; 0 si aún no está guardado).
        user_id: Su dueño.
        sector: Clave de `SECTORS`.
        barrio: Clave de los barrios de `pisos`.
        name: Nombre (ya limpio, `clean_name`).
        palette: Índice de `PALETTES`.
        emblem: Índice de `EMBLEMS`.
        form: Forma jurídica: `autonomo` (la fase 3 traerá `sl`).
        opened_at: Cuándo se montó (epoch).
        seed: Semilla de sus habituales y de lo que se dibuja.
        price: Clave de `PRICE_LEVELS`.
        reputation: De 0 a 100.
        stock: Unidades por producto.
        regulars: Fidelidad de cada habitual, por su nombre.
        day: Día (ISO) de los contadores de hoy.
        turns_today: Turnos abiertos hoy (las franjas del negocio).
        sales_today: Ventas de hoy (con propinas).
        clients_today: Clientes atendidos hoy.
        last_turn_at: Cuándo abrió por última vez (epoch; 0 si nunca).
        open_streak: Días seguidos abriendo, hasta `last_open_day`.
        last_open_day: Último día (ISO) en que abrió.
        week: Lunes ISO de `week_totals`.
        week_totals: Cuentas de esta semana por partida (`ventas`, `compras`, ...).
        last_week: Las de la semana anterior.
        rent_debt: Alquiler y suministros que se deben al casero.
        unpaid_nights: Noches seguidas sin pagar entero.
        billed_day: Último día (ISO) cuya noche ya se cobró.
        deposit: Fianza que espera en el depósito de fianzas.
        news: Lo que ha pasado desde la última vez que se abrió el panel (las noches).
        best_turn: Las mejores ventas de un turno.
        size: Índice de `SIZES` (fase 2).
        own_listing: Clave del local propio de `pisos` en el que está (`""` = alquilado).
        equipment: Claves de `EQUIPMENT` instaladas.
        assets: Lo que se amortiza: `{"key", "cost", "rate", "left"}` (`left`, lo que queda;
            `key` es la mejora o `obra` para el acondicionamiento).
        campaign: Clave de la campaña de publicidad en marcha (`""` si no hay).
        campaign_until: Último día (ISO) de la campaña.
        prices: Nivel de precios de cada producto que no va con el general (`price`).
        reviews: Las últimas reseñas: `[día ISO, quién, estrellas, índice de la frase]`.
        company: La sociedad limitada, si `form` es `sl` (fase 3): ver
            `bot.services.business_sl.new_company`.
        pluri_week: Última semana (lunes ISO) en que se miró el reintegro de pluriactividad.
        staff: La plantilla (fase 4): un diccionario por empleado, ver
            `bot.services.business_staff.new_employee`.
        staff_seq: El número del último empleado contratado.
        seen: Las claves de los candidatos ya contratados o descartados (las últimas).
        staff_day: Día (ISO) de las franjas de la plantilla ya jugadas (`staff_done`).
        staff_done: Franjas de `staff_day` ya jugadas por la plantilla (la siguiente a jugar).
        staff_today: Lo de la plantilla hoy: `turns`, `clients` y `sales`.
        wage_debt: Lo que se debe a la plantilla y a quien se fue (sueldos e indemnizaciones).
        bonus_ban: Último día (ISO) sin bonificaciones por un despido improcedente de alguien
            bonificado (art. 11.4 del RDL 1/2023).
        tipoff: Día (ISO) en que alguien en B se fue enfadado: esa noche, la Inspección es
            más probable.
        fundae_week: Semana (lunes ISO) de `fundae_used`.
        fundae_used: Lo gastado esa semana del crédito de formación de FUNDAE.
        company_id: La sociedad del local (fase 5: una sociedad lleva todos los locales de su
            dueño): el `id` del local con el que se constituyó, que da su caja
            (`economy.company_account`) aunque ese local cierre. 0 si es de autónomo.
        sold_days: Unidades vendidas de cada producto por día (ISO), los últimos días: el
            gerente repone según lo que se vendió (fase 5).
        vacancies: Las bajas que tiene que cubrir el gerente: `{"jornada", "shift"}` y, desde
            la 9.41.0, `role` y `franjas`.
        support: Lo que dejó la plantilla de apoyo la última noche cerrada para el día
            siguiente (9.41.0, `business_staff.support`): `clients` (el community manager) y
            `patience` (la limpieza).
    """

    user_id: int
    sector: str
    barrio: str
    name: str
    palette: int = 0
    emblem: int = 0
    form: str = "autonomo"
    opened_at: float = 0.0
    seed: int = 0
    price: str = "normal"
    reputation: float = REPUTATION_START
    stock: dict[str, int] = field(default_factory=dict)
    regulars: dict[str, int] = field(default_factory=dict)
    day: str = ""
    turns_today: int = 0
    sales_today: int = 0
    clients_today: int = 0
    last_turn_at: float = 0.0
    open_streak: int = 0
    last_open_day: str = ""
    week: str = ""
    week_totals: dict[str, int] = field(default_factory=dict)
    last_week: dict[str, int] = field(default_factory=dict)
    rent_debt: int = 0
    unpaid_nights: int = 0
    billed_day: str = ""
    deposit: int = 0
    news: list[str] = field(default_factory=list)
    best_turn: int = 0
    size: int = 0
    own_listing: str = ""
    equipment: list[str] = field(default_factory=list)
    assets: list[dict[str, Any]] = field(default_factory=list)
    campaign: str = ""
    campaign_until: str = ""
    prices: dict[str, str] = field(default_factory=dict)
    reviews: list[list[Any]] = field(default_factory=list)
    company: dict[str, Any] = field(default_factory=dict)
    pluri_week: str = ""
    staff: list[dict[str, Any]] = field(default_factory=list)
    staff_seq: int = 0
    seen: list[int] = field(default_factory=list)
    staff_day: str = ""
    staff_done: int = 0
    staff_today: dict[str, int] = field(default_factory=dict)
    wage_debt: int = 0
    bonus_ban: str = ""
    tipoff: str = ""
    fundae_week: str = ""
    fundae_used: int = 0
    company_id: int = 0
    sold_days: dict[str, dict[str, int]] = field(default_factory=dict)
    vacancies: list[dict[str, Any]] = field(default_factory=list)
    support: dict[str, float] = field(default_factory=dict)
    id: int = 0

    @property
    def sector_info(self) -> Sector:
        """Su sector del catálogo."""
        return SECTOR_BY_KEY[self.sector]

    @property
    def barrio_info(self) -> Barrio:
        """Su barrio de `pisos`."""
        return BARRIO_BY_KEY[self.barrio]

    @property
    def stars(self) -> float:
        """Sus estrellas (1-5)."""
        return stars(self.reputation)

    @property
    def colors(self) -> tuple[str, str]:
        """`(principal, acento)` de su rótulo."""
        _name, main, accent = PALETTES[self.palette % len(PALETTES)]
        return main, accent

    @property
    def emblem_key(self) -> str:
        """Clave del emblema del rótulo (la dibuja la escena)."""
        return EMBLEMS[self.emblem % len(EMBLEMS)][0]

    @property
    def is_company(self) -> bool:
        """Si es de una sociedad limitada (fase 3) y no de un autónomo."""
        return self.form == "sl" and bool(self.company)

    @property
    def size_info(self) -> Size:
        """Su tamaño de local."""
        return SIZES[max(0, min(len(SIZES) - 1, self.size))]

    @property
    def installed(self) -> tuple[Equipment, ...]:
        """Sus mejoras, del catálogo."""
        return tuple(EQUIPMENT_BY_KEY[k] for k in self.equipment if k in EQUIPMENT_BY_KEY)

    def level(self, product: str) -> PriceLevel:
        """El nivel de precios de un producto (el suyo o el general)."""
        return PRICE_BY_KEY[self.prices.get(product, self.price)]

    def roll_over(self, today: date, week: str) -> None:
        """Pone a cero los contadores del día y pasa la semana si han cambiado.

        Idempotente: se puede llamar varias veces el mismo día.
        """
        if self.day != today.isoformat():
            self.day = today.isoformat()
            self.turns_today = self.sales_today = self.clients_today = 0
            self.staff_today = {}
        if self.week != week:
            if self.week:
                self.last_week = dict(self.week_totals)
            self.week = week
            self.week_totals = {}

    def tell(self, line: str) -> None:
        """Apunta un aviso para la próxima vez que abra el panel (los cinco últimos)."""
        self.news = [*self.news, line][-NEWS_MAX:]

    def add(self, item: str, amount: int) -> None:
        """Suma `amount` a una partida de las cuentas de la semana (positivo o negativo)."""
        if amount:
            self.week_totals[item] = self.week_totals.get(item, 0) + amount

    def note_sold(self, day: date, units: dict[str, int]) -> None:
        """Apunta lo vendido `day` (se guardan los `SOLD_DAYS` últimos días)."""
        iso = day.isoformat()
        today = dict(self.sold_days.get(iso, {}))
        for key, count in units.items():
            if count:
                today[key] = today.get(key, 0) + count
        days = {**self.sold_days, iso: today}
        self.sold_days = {k: days[k] for k in sorted(days)[-SOLD_DAYS:]}

    def sold_on(self, day: date) -> dict[str, int]:
        """Lo vendido `day`, o el día anterior si `day` no abrió (lo que mira el gerente)."""
        iso = day.isoformat()
        if self.sold_days.get(iso):
            return dict(self.sold_days[iso])
        earlier = [k for k in sorted(self.sold_days) if k < iso and self.sold_days[k]]
        return dict(self.sold_days[earlier[-1]]) if earlier else {}


def clean_name(text: str) -> str:
    """El nombre de un negocio, limpio: una línea, sin menciones, enlaces ni marcas.

    Raises:
        ValueError: Si queda vacío.
    """
    # Lo que viene de un formulario es de quien lo escribe: fuera saltos, @, <#…> y
    # caracteres de formato de Markdown que romperían el panel o mencionarían a alguien.
    text = re.sub(r"<[@#][!&]?\d+>|<a?:\w+:\d+>", "", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[\r\n\t@<>*_`~|#\\]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()[:NAME_MAX].strip()
    if not text:
        raise ValueError("El negocio necesita un nombre.")
    return text


def regular_names(seed: int) -> tuple[str, ...]:
    """Los cinco habituales de un negocio, siempre los mismos para su semilla."""
    rng = random.Random(seed)
    return tuple(rng.sample(REGULAR_NAMES, REGULARS_PER_BUSINESS))


def usual_order(seed: int, regular: str, sector: Sector) -> dict[str, int]:
    """Lo que pide siempre un habitual (en el taller, la avería que le repite el coche)."""
    rng = random.Random(f"{seed}:{regular}")
    if sector.game == "diagnostico":
        return {rng.choice(sector.products).key: 1}
    low, high = sector.order
    count = rng.randint(low, max(low, (low + high) // 2))
    order: dict[str, int] = {}
    for _ in range(count):
        key = rng.choices(sector.products, weights=[p.weight for p in sector.products])[0].key
        order[key] = order.get(key, 0) + 1
    return order


# -- El local y lo que cuesta ----------------------------------------------------------


def rent(sector: Sector, barrio: Barrio, size: Size = SIZES[0]) -> int:
    """Alquiler del local de cada noche (un mes del contrato), base sin IGIC, en Y$.

    Como `housing.reference_rent` de un local o una nave de `pisos` con los metros del
    sector (por su tamaño): alquilar el local o tenerlo alquilado sale a lo mismo.
    """
    euros = sector.m2 * size.m2 * barrio.rent_m2 * KINDS[sector.kind].rent_factor
    return int(round(euros * YAPDOLLARS_PER_EURO, -1))


def deposit(sector: Sector, barrio: Barrio, size: Size = SIZES[0]) -> int:
    """Fianza del local: `DEPOSIT_MONTHS` mensualidades."""
    return DEPOSIT_MONTHS * rent(sector, barrio, size)


def size_for(sector: Sector, m2: int) -> int | None:
    """El tamaño (índice de `SIZES`) de un local propio de `m2` metros, o `None` si no cabe."""
    ratio = m2 / sector.m2
    fits = [i for i, low in enumerate(SIZE_FROM) if ratio >= low]
    return fits[-1] if fits else None


@dataclass(frozen=True, slots=True)
class OpeningQuote:
    """Lo que cuesta montar un negocio.

    Attributes:
        license: Tasa de la apertura (al Estado: no hay ayuntamientos).
        deposit: Fianza del local (al depósito de fianzas; vuelve al cerrar).
        setup: Acondicionamiento del local, base.
        setup_igic: Su IGIC.
        rent: Alquiler de cada noche, base.
    """

    license: int
    deposit: int
    setup: int
    setup_igic: int
    rent: int

    @property
    def total(self) -> int:
        """Lo que sale del monedero al montarlo."""
        return self.license + self.deposit + self.setup + self.setup_igic


def opening_quote(
    sector: Sector, barrio: Barrio, size: Size = SIZES[0], *, own: bool = False
) -> OpeningQuote:
    """Lo que cuesta montar `sector` en `barrio` (o trasladarse allí).

    Args:
        own: En un local propio de `pisos`: sin fianza ni alquiler.
    """
    setup = int(round(sector.setup * size.costs, -2))
    return OpeningQuote(
        license=sector.license,
        deposit=0 if own else deposit(sector, barrio, size),
        setup=setup,
        setup_igic=igic(setup),
        rent=0 if own else rent(sector, barrio, size),
    )


@dataclass(frozen=True, slots=True)
class NightlyBill:
    """El recibo de una noche: alquiler y suministros.

    Attributes:
        rent: Alquiler, base.
        rent_igic: IGIC del alquiler (arrendamiento de local, tipo general).
        withholding: Retención del IRPF del arrendador (art. 100 RIRPF), sale de la base:
            el inquilino la ingresa en el Estado y el resto se lo lleva el casero.
        utilities: Suministros, base.
        utilities_igic: Su IGIC.
        fees: Tasas municipales de cada noche (la terraza, art. 20.3.l TRLRHL): al Estado.
    """

    rent: int
    rent_igic: int
    withholding: int
    utilities: int
    utilities_igic: int
    fees: int = 0

    @property
    def total(self) -> int:
        """Lo que cuesta la noche entera."""
        return self.rent + self.rent_igic + self.utilities + self.utilities_igic + self.fees

    @property
    def taxes(self) -> int:
        """Lo que va al Estado (IGIC, retención y tasas)."""
        return self.rent_igic + self.withholding + self.utilities_igic + self.fees


def nightly_bill(business: Business) -> NightlyBill:
    """Lo que cuesta una noche del local de `business` (sin alquiler si el local es suyo)."""
    sector, barrio, size = business.sector_info, business.barrio_info, business.size_info
    base = 0 if business.own_listing else rent(sector, barrio, size)
    utilities = int(round(sector.utilities * size.costs, -1))
    return NightlyBill(
        rent=base,
        rent_igic=igic(base),
        withholding=round(base * RENT_WITHHOLDING),
        utilities=utilities,
        utilities_igic=igic(utilities),
        fees=sum(e.nightly_fee for e in business.installed),
    )


def wholesale(product: Product, units: int) -> tuple[int, int]:
    """`(base, IGIC)` de comprar `units` unidades al mayorista."""
    base = product.cost * units
    return base, igic(base, IGIC_BY_KEY.get(product.igic, IGIC_BY_KEY["general"]).rate)


def sale_price(product: Product, price_level: str) -> int:
    """Precio de venta de una unidad con el nivel de precios del negocio (a decenas)."""
    return int(round(product.price * PRICE_BY_KEY[price_level].ticket, -1))


def spoil(business: Business, keep: float = 0.0) -> dict[str, int]:
    """Quita del stock lo que se pierde esta noche y devuelve las unidades perdidas.

    Las mejoras que enfrían (`Equipment.spoil`) evitan una parte, y el mozo de almacén otra
    (`keep`, `business_staff.Support.keep`), con el tope de toda la merma.
    """
    lost = spoil_units(business, keep)
    for key, gone in lost.items():
        business.stock[key] = business.stock.get(key, 0) - gone
    return lost


def spoil_units(business: Business, keep: float = 0.0) -> dict[str, int]:
    """Lo que se perdería esta noche (sin quitarlo del stock), con las mejoras y `keep`."""
    lost: dict[str, int] = {}
    keep = min(1.0, sum(e.spoil for e in business.installed) + keep)
    for product in business.sector_info.products:
        units = business.stock.get(product.key, 0)
        share = product.spoil * (1 - keep)
        gone = math.ceil(units * share) if share and units else 0
        if gone:
            lost[product.key] = gone
    return lost


def drift(business: Business) -> None:
    """La clientela olvida un poco si no abriste (solo por encima de la reputación inicial)."""
    if business.reputation > REPUTATION_START:
        business.reputation = max(REPUTATION_START, business.reputation - REPUTATION_DRIFT)


# -- La demanda ----------------------------------------------------------------------


def daily_pool(sector: Sector, barrio: Barrio) -> float:
    """Clientes que da de sí un barrio al día para un sector (entre todos sus negocios)."""
    return DAILY_POOL_TURNS * sector.clients * sector.fit.get(barrio.theme, 1.0)


def campaign_boost(business: Business, today: date) -> float:
    """Lo que suma su campaña de publicidad hoy (0 si no tiene o ya acabó)."""
    campaign = CAMPAIGN_BY_KEY.get(business.campaign)
    if campaign is None or business.campaign_until < today.isoformat():
        return 0.0
    return campaign.boost


def boost(business: Business, today: date | None) -> float:
    """Lo que suman hoy a los clientes y al cupo la campaña y el community manager (lo que
    publicó anoche, `Business.support`), con el tope de `BOOST_CAP`."""
    campaign = campaign_boost(business, today) if today else 0.0
    return min(BOOST_CAP, campaign + business.support.get("clients", 0.0))


def pool_factor(business: Business, today: date) -> float:
    """Lo que multiplica su parte del cupo del barrio: el tamaño del local, la campaña y el
    community manager."""
    return business.size_info.pool * (1 + boost(business, today))


def _weighted(business: Business, value: Callable[[PriceLevel], float]) -> float:
    """La media de algo del nivel de precios de cada producto, por lo que se pide."""
    products = business.sector_info.products
    total = sum(p.weight for p in products)
    return sum(value(business.level(p.key)) * p.weight for p in products) / total


def price_clients(business: Business) -> float:
    """Lo que multiplican los precios los clientes de un turno (media por producto)."""
    luxury = business.barrio_info.theme == "lujo"
    return _weighted(business, lambda lv: lv.luxury_clients if luxury else lv.clients)


def price_reputation(business: Business) -> float:
    """Lo que suman los precios a la reputación por cada cliente bien atendido."""
    return _weighted(business, lambda lv: lv.reputation)


def turn_clients(business: Business, hour: int, luck: float, today: date | None = None) -> int:
    """Clientes que vendrían a un turno a esta hora (sin mirar el cupo del barrio).

    `base × tema del barrio × hora × reputación × precio × local`, redondeado con un poco de
    azar (`luck`, de 0 a 1) para que dos turnos iguales no salgan clavados. El local suma
    su tamaño, sus mejoras, la campaña de `today` (y el community manager, `boost`) y el
    «recomendado». Al menos uno.
    """
    sector, barrio = business.sector_info, business.barrio_info
    local = (
        business.size_info.clients
        * (1 + sum(e.clients for e in business.installed))
        * (1 + boost(business, today))
        * (1 + (RECOMMENDED_BOOST if recommended(business) else 0.0))
    )
    expected = (
        sector.clients
        * sector.fit.get(barrio.theme, 1.0)
        * sector.curve[hour % 24]
        * reputation_factor(business.reputation)
        * price_clients(business)
        * local
    )
    return max(1, math.floor(expected + luck))


def stock_value(business: Business) -> int:
    """Lo que costó al mayorista el stock que hay (sin IGIC)."""
    return sum(p.cost * business.stock.get(p.key, 0) for p in business.sector_info.products)


# -- Fase 2: amortizar, reseñas, cerrar y trasladarse --------------------------------


def add_asset(business: Business, key: str, cost: int, kind: str) -> None:
    """Apunta algo que se amortiza (`cost` con su IGIC, que el pequeño empresario no deduce)."""
    rate = AMORTIZATION[kind][1]
    business.assets = [*business.assets, {"key": key, "cost": cost, "rate": rate, "left": cost}]


def amortize(business: Business) -> int:
    """La amortización de una noche: la séptima parte del coeficiente anual de cada cosa.

    El año fiscal es la semana. Baja lo que queda por amortizar y devuelve el total.
    """
    total = 0
    assets = []
    for asset in business.assets:
        share = min(asset["left"], max(1, round(asset["cost"] * asset["rate"] / 7)))
        total += share
        if asset["left"] - share > 0:
            assets.append({**asset, "left": asset["left"] - share})
    business.assets = assets
    return total


def book_value(business: Business, *, equipment_only: bool = False) -> int:
    """Lo que queda por amortizar (el valor neto contable); sin la obra, si se pide."""
    return sum(a["left"] for a in business.assets if not (equipment_only and a["key"] == "obra"))


def setup_asset(business: Business, cost: int) -> None:
    """Apunta el acondicionamiento de un local nuevo (base más IGIC) como instalación."""
    add_asset(business, "obra", cost, SETUP_CLASS)


def review_of(outcome: Any, rng: random.Random) -> tuple[str, int, int] | None:
    """La reseña que deja un cliente (`(quién, estrellas, frase)`), o `None` si no deja.

    Las estrellas salen de su nota (`Outcome.score`, de 0 a 100) con un poco de azar; un
    habitual es algo más generoso. Quien seguía en la cola al cerrar (`gone`) no llegó a
    que le atendieran: no escribe, igual que no mueve la reputación.
    """
    if outcome.result == "gone":
        return None
    angry = not outcome.served
    if rng.random() >= (REVIEW_ANGRY_CHANCE if angry else REVIEW_CHANCE):
        return None
    regular = outcome.client.regular
    stars = 1 + outcome.score / 25 + rng.uniform(-0.5, 0.5) + (0.4 if regular else 0.0)
    count = max(1, min(5, round(stars)))
    who = regular or rng.choice(REVIEWERS)
    return who, count, rng.randrange(len(REVIEW_LINES[count]))


def add_reviews(business: Business, day: date, reviews: list[tuple[str, int, int]]) -> None:
    """Apunta las reseñas de un turno (se guardan las `REVIEW_WINDOW` últimas)."""
    rows = [[day.isoformat(), who, n, line] for who, n, line in reviews]
    business.reviews = [*business.reviews, *rows][-REVIEW_WINDOW:]


def rating(business: Business) -> tuple[float, int]:
    """`(media, cuántas)` de sus reseñas (media 0 si no hay)."""
    count = len(business.reviews)
    return (sum(r[2] for r in business.reviews) / count if count else 0.0), count


def recommended(business: Business) -> bool:
    """Si sale «recomendado»: buena media con bastantes reseñas."""
    average, count = rating(business)
    return count >= RECOMMENDED_REVIEWS and average >= RECOMMENDED_RATING


@dataclass(frozen=True, slots=True)
class ClosingOffer:
    """Lo que se saca al cerrar: traspasándolo o liquidando.

    Attributes:
        stock: Coste al mayorista del stock que queda.
        book: Lo que queda por amortizar de mejoras y obra (valor neto contable).
        equipment_book: Lo mismo, solo de las mejoras (la obra no se puede vender suelta).
        goodwill: El fondo de comercio de un traspaso (0 si no hay quien lo compre).
        can_traspasar: Si hay quien lo compre (local alquilado, días y estrellas).
    """

    stock: int
    book: int
    equipment_book: int
    goodwill: int
    can_traspasar: bool

    @property
    def traspaso(self) -> int:
        """Lo que paga quien se queda el negocio: stock, mejoras y obra, y la clientela."""
        return self.stock + self.book + self.goodwill

    @property
    def liquidation_stock(self) -> int:
        """Lo que paga el mayorista por el stock."""
        return int(self.stock * LIQUIDATION_STOCK)

    @property
    def liquidation_equipment(self) -> int:
        """Lo que se saca vendiendo las mejoras de segunda mano."""
        return int(self.equipment_book * LIQUIDATION_EQUIPMENT)

    @property
    def liquidation(self) -> int:
        """Lo que se saca cerrando sin traspaso."""
        return self.liquidation_stock + self.liquidation_equipment


def closing_offer(business: Business, now: float) -> ClosingOffer:
    """Lo que daría cerrar ahora `business` (la semana pasada fija el fondo de comercio)."""
    days = (now - business.opened_at) / 86_400
    can = (
        not business.own_listing
        and days >= TRASPASO_MIN_DAYS
        and business.stars >= TRASPASO_MIN_STARS
    )
    # Quien se lo queda no se queda con los préstamos: los intereses no bajan el fondo.
    daily = max(0, sum(v for k, v in business.last_week.items() if k != "intereses")) / 7
    goodwill = int(round(daily * GOODWILL_DAYS * business.stars / 5, -2)) if can else 0
    return ClosingOffer(
        stock=stock_value(business),
        book=book_value(business),
        equipment_book=book_value(business, equipment_only=True),
        goodwill=goodwill,
        can_traspasar=can,
    )


@dataclass(frozen=True, slots=True)
class MoveQuote:
    """Lo que cuesta trasladar el negocio.

    Attributes:
        opening: La apertura del local nuevo (tasa, acondicionamiento y fianza).
        refund: La fianza del local de ahora que vuelve (menos lo que se debe al casero).
        size: Tamaño del local nuevo (índice de `SIZES`).
        same_barrio: Si se queda en el mismo barrio (no pierde clientela).
    """

    opening: OpeningQuote
    refund: int
    size: int
    same_barrio: bool

    @property
    def total(self) -> int:
        """Lo que sale del monedero (sin contar la fianza que vuelve)."""
        return self.opening.total


def move_quote(business: Business, barrio: Barrio, size: int, *, own: bool = False) -> MoveQuote:
    """Lo que cuesta trasladar `business` a `barrio` con un local de tamaño `size`."""
    opening = opening_quote(business.sector_info, barrio, SIZES[size], own=own)
    refund = max(0, business.deposit - business.rent_debt)
    return MoveQuote(opening, refund, size, barrio.key == business.barrio)


def forget_on_move(business: Business) -> None:
    """Lo que pierde un negocio al cambiar de barrio: media reputación y media fidelidad."""
    business.reputation = REPUTATION_START + (business.reputation - REPUTATION_START) * MOVE_KEEP
    business.regulars = {k: int(v * MOVE_KEEP) for k, v in business.regulars.items()}


#: IGIC general, para los textos.
IGIC_RATE = IGIC_GENERAL_RATE
