"""Los solares de `pisos` y lo que construye en ellos la rama Ladrillo de `pala` (9.56.0).

Cada calle tiene un solar (60 en el servidor). Quien ficha en la rama Ladrillo compra uno y
construye el proyecto de su puesto (o uno más pequeño): una casa, un bloque o una torre. Las
unidades salen de una regla fija (el solar y el proyecto), como las de las calles
(`housing_streets`), así que no se guardan: basta con saber qué proyecto va en qué solar.

Aquí solo hay reglas y cuentas, sin dinero ni base de datos: lo que cuesta construir
(`build_quote`), las unidades de cada proyecto (`solar_units`) y la burbuja (`bubble_odds`,
`bubble_cut`). Plan entero, cifras y normas en `docs/solares.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import cache

from bot.services.housing import (
    ICIO_RATE,
    INDEX_MIN,
    ITP_RATE,
    NOTARY_RATE,
    SERVICES_IGIC,
    SPLIT_AJD_RATE,
    base_price,
    price,
)
from bot.services.housing_catalog import BARRIO_BY_KEY, PROMOTION_BLURBS, Listing
from bot.services.housing_streets import (
    BLOQUE,
    CASA,
    HOMES,
    HOUSE_NAMES,
    SOLAR_SUFFIX,
    STREETS,
    THEMES,
    Building,
    Street,
    Theme,
    Unit,
    _bloque,
    _seed,
    _unit,
    _vary,
    buildings_of,
    is_solar_unit,
)
from bot.services.taxes import igic

#: Lo que cuesta el solar: una parte de lo que valdrán las unidades al índice de hoy. Es el
#: «valor de repercusión» del suelo: en la vida real, entre el 15 y el 30 % del precio de venta.
LAND_SHARE = 0.15
#: Lo que cuesta la obra: una parte de lo que valdrán las unidades con el índice a 1 (no sube con
#: la burbuja: el ladrillo y la mano de obra cuestan lo mismo). Calidades a la altura del barrio.
#: Con el solar y los impuestos, el promotor gana ~16 % sobre lo que pone (el margen de una
#: promotora de verdad): con el 55 % y la mitad de días, quien encadena obras en P10 llegaba al
#: Falcon el día 171 (`docs/economia_sim.py --solares`, `docs/solares.md`).
WORKS_SHARE = 0.62
#: Tasa de la licencia de obra mayor, sobre el coste de la obra (ordenanza fiscal municipal,
#: art. 20.4.h del RDL 2/2004).
LICENSE_RATE = 0.01
#: Alturas máximas por tipo de barrio (con el bajo): el campo no tiene torres.
MAX_FLOORS: dict[str, int] = {"rural": 3, "historico": 5, "marinero": 5}
#: La casa de cada tipo de barrio: `(tipo, metros)`.
HOUSES: dict[str, tuple[str, int]] = {
    "lujo": ("chalet", 260),
    "playa": ("chalet", 180),
    "historico": ("casona", 220),
}
DEFAULT_HOUSE = ("terrera", 110)

# -- La burbuja ---------------------------------------------------------------------------

#: Días hacia atrás en que se mira la obra empezada y lo que compran los miembros.
BUBBLE_WINDOW = 28
#: Por debajo de esta obra empezada no se mira nada (una casa suelta no hace burbuja).
BUBBLE_SUPPLY_MIN = 5_000_000
#: Lo menos que cuenta como demanda: sin él, con nadie comprando, la presión sería infinita.
BUBBLE_DEMAND_FLOOR = 3_000_000
#: Lo que sube la probabilidad de que estalle por cada punto de presión por encima de 1.
BUBBLE_SLOPE = 0.25
BUBBLE_MAX_ODDS = 0.5
#: Lo que baja el índice: el 10 % con la presión justo en 1, un 5 % más por cada punto.
BUBBLE_CUT_MIN = 0.10
BUBBLE_CUT_STEP = 0.05
BUBBLE_CUT_MAX = 0.30
#: Días que se retrasan las obras en marcha cuando estalla (el banco corta el crédito).
BUBBLE_DELAY = 3


@dataclass(frozen=True, slots=True)
class Solar:
    """El solar de una calle.

    Attributes:
        key: El edificio que se levantará, `<calle>_s`.
        street: Clave de la calle.
        barrio: Clave del barrio.
        number: Su número de portal: el siguiente libre de la calle.
        m2: Metros de suelo (para el cartel).
    """

    key: str
    street: str
    barrio: str
    number: int
    m2: int

    @property
    def theme(self) -> str:
        """El tipo de barrio (`centro`, `rural`…)."""
        return BARRIO_BY_KEY[self.barrio].theme


@dataclass(frozen=True, slots=True)
class Project:
    """Lo que se puede construir en un solar.

    Attributes:
        key: Clave estable (se guarda en `housing_solars.project`).
        name: Cómo se llama.
        emoji: Su icono.
        level: Puesto mínimo de la rama Ladrillo de `pala` (6 a 10).
        floors: Plantas sobre la calle, con el bajo (1 en la casa).
        layout: Cómo es el bloque (`Theme` de `housing_streets`, sin metros: los pone el barrio);
            `None` en la casa.
        days: Días de obra.
        feminine: Si se dice «una» (la casa, la torre), para los avisos.
    """

    key: str
    name: str
    emoji: str
    level: int
    floors: int
    layout: Theme | None
    days: int
    feminine: bool = False

    def called(self, *, definite: bool = False) -> str:
        """«una casa», «un bloque de 5 plantas» o, con `definite`, «la casa», «el bloque…»."""
        if definite:
            article = "la" if self.feminine else "el"
        else:
            article = "una" if self.feminine else "un"
        return f"{article} {self.name.lower()}"


PROJECTS: tuple[Project, ...] = (
    Project("casa", "Casa", "🏡", 6, 1, None, 10, feminine=True),
    Project("bloque3", "Bloque de 3 plantas", "🏘️", 7, 3, Theme(3, 2, 1, 0, 2, False, 0), 14),
    Project("bloque5", "Bloque de 5 plantas", "🏢", 8, 5, Theme(5, 2, 1, 2, 2, True, 0), 20),
    Project("bloque7", "Residencial de 7 plantas", "🏬", 9, 7, Theme(7, 2, 2, 4, 4, True, 0), 24),
    Project(
        "torre",
        "Torre de 8 plantas",
        "🗼",
        10,
        8,
        Theme(8, 2, 1, 4, 4, True, 0, True),
        28,
        feminine=True,
    ),
)
PROJECT_BY_KEY: dict[str, Project] = {p.key: p for p in PROJECTS}
#: Lo que pone en el cartel del solar mientras está libre, por tipo de barrio.
SOLAR_M2: dict[str, int] = {
    "centro": 600,
    "historico": 450,
    "obrero": 700,
    "playa": 900,
    "marinero": 450,
    "lujo": 1500,
    "rural": 2000,
}


def _solars() -> tuple[Solar, ...]:
    out = []
    for street in STREETS:
        numbers = [b.number for b in buildings_of(street.key)]
        theme = BARRIO_BY_KEY[street.barrio].theme
        out.append(
            Solar(
                key=f"{street.key}{SOLAR_SUFFIX}",
                street=street.key,
                barrio=street.barrio,
                number=max(numbers, default=0) + 10,
                m2=_vary(SOLAR_M2[theme], street.key, "solar"),
            )
        )
    return tuple(out)


SOLARS = _solars()
SOLAR_BY_KEY: dict[str, Solar] = {s.key: s for s in SOLARS}
STREET_BY_KEY: dict[str, Street] = {s.key: s for s in STREETS}


def solars_of(barrio: str) -> tuple[Solar, ...]:
    """Los tres solares de un barrio, en el orden de sus calles."""
    return tuple(s for s in SOLARS if s.barrio == barrio)


def solar_of(key: str) -> Solar | None:
    """El solar en que está una unidad construida (`triana_c2_s_1a` → `triana_c2_s`)."""
    if not is_solar_unit(key):
        return None
    head, _sep, _slot = key.partition(f"{SOLAR_SUFFIX}_")
    return SOLAR_BY_KEY.get(f"{head}{SOLAR_SUFFIX}")


def allowed(solar: Solar, project: Project) -> bool:
    """Si el barrio deja levantar ese proyecto (su límite de alturas)."""
    return project.floors <= MAX_FLOORS.get(solar.theme, 99)


def projects_for(solar: Solar) -> tuple[Project, ...]:
    """Los proyectos que caben en un solar, del más pequeño al más grande."""
    return tuple(p for p in PROJECTS if allowed(solar, p))


@cache
def solar_units(solar_key: str, project_key: str) -> tuple[Building, tuple[Listing, ...]]:
    """El edificio de un proyecto en un solar y sus unidades nuevas, de abajo arriba.

    Sale de una regla fija (las mismas claves y metros cada vez), como las calles: son obra
    nueva, con certificado A y la letra pequeña de la promotora.

    Raises:
        KeyError: Si el solar o el proyecto no existen.
    """
    solar, project = SOLAR_BY_KEY[solar_key], PROJECT_BY_KEY[project_key]
    street = STREET_BY_KEY[solar.street]
    theme = THEMES[solar.theme]
    if project.layout is None:
        house_kind, m2 = HOUSES.get(solar.theme, DEFAULT_HOUSE)
        label = HOUSE_NAMES[house_kind]
        listing = _unit(solar.key, solar.barrio, "casa", house_kind,
                        _vary(m2, solar.key, "casa"), label, street, solar.number, "A")  # fmt: skip
        kind, floors, pairs = CASA, 1, [(Unit(listing.key, 0, label), listing)]
    else:
        # Sin locales en el campo, como sus bloques; las viviendas, del tamaño del barrio.
        layout = replace(
            project.layout, m2=theme.m2, locales=project.layout.locales if theme.locales else 0
        )
        floors, pairs = _bloque(solar.key, None, street, solar.number, layout, solar.barrio)
        kind = BLOQUE
    # Las viviendas llevan la letra pequeña de la promotora; garajes, trasteros y locales, la suya.
    units = tuple(
        replace(
            listing,
            energy="A",
            blurb=PROMOTION_BLURBS[_seed(listing.key, "obra") % len(PROMOTION_BLURBS)]
            if listing.kind in HOMES or listing.kind in HOUSE_NAMES
            else listing.blurb,
        )
        for _unit_, listing in pairs
        if listing is not None
    )
    building = Building(
        key=solar.key,
        barrio=solar.barrio,
        street=solar.street,
        number=solar.number,
        kind=kind,
        featured="",
        floors=floors,
        units=tuple(unit for unit, _listing in pairs),
    )
    return building, units


@dataclass(frozen=True, slots=True)
class BuildQuote:
    """La cuenta de construir un proyecto en un solar.

    Attributes:
        land: El solar.
        itp: ITP del solar (6,5 %).
        notary: Notaría y registro del solar.
        notary_igic: Su IGIC.
        works: La obra, sin impuestos.
        works_igic: IGIC de la obra.
        icio: Impuesto de construcciones (4 % de la obra).
        license: Tasa de la licencia de obra mayor.
        ajd: AJD de la escritura de obra nueva y división horizontal.
        value: Lo que valen las unidades hoy, al índice del barrio.
        units: Cuántas unidades salen.
        days: Días de obra.
    """

    land: int
    itp: int
    notary: int
    notary_igic: int
    works: int
    works_igic: int
    icio: int
    license: int
    ajd: int
    value: int
    units: int
    days: int

    @property
    def paid(self) -> int:
        """Lo pagado sin impuestos (el solar, la notaría y la obra): lo que sube el Patrimonio."""
        return self.land + self.notary + self.works

    @property
    def taxes(self) -> int:
        """Los impuestos y tasas, que van al Estado."""
        return self.itp + self.notary_igic + self.works_igic + self.icio + self.license + self.ajd

    @property
    def total(self) -> int:
        """Todo lo que sale del bolsillo."""
        return self.paid + self.taxes

    @property
    def margin(self) -> int:
        """Lo que valen las unidades menos lo que cuesta construirlas (puede ser negativo)."""
        return self.value - self.total


def build_quote(solar: Solar, project: Project, index: float = 1.0, ipc: int = 0) -> BuildQuote:
    """Lo que cuesta construir un proyecto en un solar.

    El solar sigue al índice del barrio (la burbuja lo encarece) y la obra no: construir en un
    barrio caliente rinde más y en uno hundido apenas compensa.

    Args:
        index: Índice de hoy del barrio.
        ipc: El IPC del crédito, en % (`credit`): encarece la obra como las reformas.
    """
    _building, units = solar_units(solar.key, project.key)
    value = sum(price(unit, index) for unit in units)
    land = int(round(value * LAND_SHARE, -3))
    notary = round(land * NOTARY_RATE)
    works = int(round(sum(base_price(unit) for unit in units) * WORKS_SHARE, -3))
    works += works * ipc // 100
    return BuildQuote(
        land=land,
        itp=round(land * ITP_RATE),
        notary=notary,
        notary_igic=igic(notary, SERVICES_IGIC),
        works=works,
        works_igic=igic(works, SERVICES_IGIC),
        icio=round(works * ICIO_RATE),
        license=round(works * LICENSE_RATE),
        ajd=round(value * SPLIT_AJD_RATE),
        value=value,
        units=len(units),
        days=project.days,
    )


def split_paid(quote: BuildQuote, units: tuple[Listing, ...]) -> list[tuple[int, int]]:
    """`(pagado, coste)` de cada unidad: lo de la cuenta repartido por lo que vale cada una.

    Lo pagado (sin impuestos) va al Patrimonio y a la amortización; el coste (con ellos), a la
    ganancia al vender. La suma es exacta: el resto del redondeo va a la última.
    """
    weights = [base_price(unit) for unit in units]

    def parts(amount: int) -> list[int]:
        out = [amount * weight // sum(weights) for weight in weights]
        out[-1] += amount - sum(out)
        return out

    return list(zip(parts(quote.paid), parts(quote.total), strict=True))


def bubble_pressure(supply: int, demand: int) -> float:
    """Obra empezada frente a lo que compran los miembros (`docs/solares.md`)."""
    return supply / max(demand, BUBBLE_DEMAND_FLOOR)


def bubble_odds(supply: int, demand: int) -> float:
    """La probabilidad de que la burbuja estalle este lunes (0 con poca obra o poca presión)."""
    if supply < BUBBLE_SUPPLY_MIN:
        return 0.0
    return min(BUBBLE_MAX_ODDS, max(0.0, BUBBLE_SLOPE * (bubble_pressure(supply, demand) - 1)))


def bubble_cut(supply: int, demand: int) -> float:
    """Lo que baja el índice de los barrios con obra si estalla: del 10 al 30 %."""
    extra = max(0.0, bubble_pressure(supply, demand) - 1)
    return min(BUBBLE_CUT_MAX, BUBBLE_CUT_MIN + BUBBLE_CUT_STEP * extra)


def burst_index(index: float, cut: float) -> float:
    """El índice de un barrio tras estallar la burbuja (nunca por debajo de `INDEX_MIN`)."""
    return max(INDEX_MIN, index * (1 - cut))
