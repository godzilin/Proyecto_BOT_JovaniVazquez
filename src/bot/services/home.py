"""Casa propia (`casa`): el Confort de la vivienda habitual, sus bufos y sus suministros.

Funciones puras, sin Discord ni base de datos. Quien vive en su casa de `pisos`, en su VPO
o de alquiler en el piso de otro miembro tiene un **Confort** de 0 a 100 que sale de la
vivienda (`Place`). El Confort da un tramo (`Tier`) y el tramo, los bufos de `pala`: lo que
multiplica la recarga de la batería y la de la familia y lo que rebaja el desgaste de cada
turno. Quien vive con sus padres (sin vivienda habitual) no tiene tramo: todo como siempre.

Cada noche, el cierre de `pisos` cobra los suministros del mes (`supplies`) a quien vive en
cada casa; si no le llega, le cortan la luz y al día siguiente la casa no da bufos.

El diseño entero, por fases, está en `docs/casa.md`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from bot.services import taxes

#: Dónde vive alguien: su casa, su VPO o de alquiler en el piso de otro miembro (las claves
#: de `housing_homes.kind`, `repositories.housing_finance.HOME_*`).
OWN, VPO, RENTED = "propia", "vpo", "alquiler"

# -- Confort ----------------------------------------------------------------------------

#: Puntos de cada pilar. El equipamiento (los muebles del hogar) llega en la fase 2: hasta
#: entonces vale 0 para todos y el máximo es 75, así nadie baja de tramo cuando llegue.
SPACE_POINTS = 40
QUALITY_POINTS = 25
GEAR_POINTS = 25
AREA_POINTS = 10
#: El espacio: 15 puntos por los metros de cada persona (llenos a 30 m² por cabeza) y 25 por
#: el tamaño de la casa, que crece con la raíz de los metros por encima de 20 (llenos a 300):
#: más grande es mejor, pero el salto de un estudio a un piso pesa más que el de un chalet a una
#: casona.
SPACE_PER_PERSON_POINTS = 15
M2_PER_PERSON = 30
SPACE_SIZE_POINTS = SPACE_POINTS - SPACE_PER_PERSON_POINTS
SIZE_FROM_M2 = 20
SIZE_FULL_M2 = 300
#: La calidad: el certificado energético, las reformas de vivienda acabadas y el ascensor.
ENERGY_POINTS: dict[str, int] = {"A": 16, "B": 14, "C": 12, "D": 10, "E": 8, "F": 6, "G": 4}
REFORM_POINTS = 3
REFORM_MAX = 3
NO_LIFT_PENALTY = 3
#: El entorno: el carácter del barrio (`housing_catalog.THEMES`).
AREA_BY_THEME: dict[str, int] = {
    "lujo": 10,
    "playa": 8,
    "historico": 7,
    "marinero": 7,
    "centro": 6,
    "rural": 6,
    "obrero": 4,
}
#: Las VPO no están en ningún barrio del catálogo: son promociones públicas de barrio obrero.
VPO_THEME = "obrero"
#: Ni el catálogo ni las VPO traen certificado: las VPO son de los 70 a los 2010.
VPO_ENERGY = "D"


@dataclass(frozen=True, slots=True)
class Place:
    """La vivienda en la que vive alguien, con lo que mira el Confort.

    Attributes:
        kind: `OWN`, `VPO` o `RENTED`.
        ref: Clave del inmueble (`Listing.key`) o de la VPO.
        name: Cómo se llama en pantalla («Piso de 2 dormitorios en Vegueta»).
        listing_kind: Tipo de inmueble (`housing_catalog.KINDS`); `"vpo"` en las VPO.
        m2: Metros.
        energy: Certificado energético (A a G), con sus reformas.
        reforms: Reformas de vivienda acabadas.
        theme: Carácter del barrio (`housing_catalog.THEMES`).
        floor: Planta («4.º sin ascensor»).
    """

    kind: str
    ref: str
    name: str
    listing_kind: str
    m2: int
    energy: str
    reforms: int
    theme: str
    floor: str = ""

    @property
    def no_lift(self) -> bool:
        """Si se sube andando (lo dice la planta del anuncio)."""
        return "sin ascensor" in self.floor.lower()


@dataclass(frozen=True, slots=True)
class Tier:
    """Un tramo del Confort y lo que da en `pala`.

    Attributes:
        low: Confort desde el que se está en el tramo.
        regen: Multiplica la recarga de la batería (`Contract.home_regen`).
        family: Multiplica lo que la familia se recupera sola (`Contract.home_family`).
        strain: Multiplica lo que resta cada turno a la familia (`Contract.home_strain`).
    """

    key: str
    low: int
    emoji: str
    name: str
    regen: float
    family: float
    strain: float


#: De mejor a peor. El de abajo es el ×1,2 fijo que había hasta la 9.53.0: nadie que ya
#: viviera en su casa pierde nada. Calibrado con `docs/familia_pala.py --casa`.
TIERS: tuple[Tier, ...] = (
    Tier("casoplon", 80, "🏰", "Casoplón", 1.5, 1.5, 0.8),
    Tier("hogar", 60, "🏡", "Hogar", 1.4, 1.35, 0.85),
    Tier("comoda", 45, "🏠", "Cómoda", 1.3, 1.2, 0.9),
    Tier("apanada", 30, "🏚️", "Apañada", 1.25, 1.1, 0.95),
    Tier("zulo", 0, "🛏️", "Zulo", 1.2, 1.0, 1.0),
)
TIER_BY_KEY: dict[str, Tier] = {t.key: t for t in TIERS}
#: Orden de cada tramo, del peor (1) al mejor (5): lo que guardan los logros como máximo.
TIER_RANK: dict[str, int] = {t.key: len(TIERS) - i for i, t in enumerate(TIERS)}


@dataclass(frozen=True, slots=True)
class Comfort:
    """El Confort de una casa, pilar a pilar.

    Attributes:
        space: Espacio (0 a `SPACE_POINTS`).
        quality: Calidad (0 a `QUALITY_POINTS`).
        gear: Equipamiento (0 a `GEAR_POINTS`).
        area: Entorno (0 a `AREA_POINTS`).
    """

    space: int
    quality: int
    gear: int
    area: int

    @property
    def total(self) -> int:
        """El Confort, de 0 a 100."""
        return self.space + self.quality + self.gear + self.area

    @property
    def tier(self) -> Tier:
        """Su tramo."""
        return tier_of(self.total)

    @property
    def next_tier(self) -> Tier | None:
        """El tramo siguiente, o `None` si ya está en el mejor."""
        above = [t for t in TIERS if t.low > self.total]
        return above[-1] if above else None


def tier_of(total: int) -> Tier:
    """El tramo de un Confort."""
    return next(t for t in TIERS if total >= t.low)


def space_points(m2: int, people: int = 1) -> int:
    """Puntos de espacio: por los metros de cada persona y por el tamaño de la casa."""
    each = m2 / max(1, people)
    per_person = SPACE_PER_PERSON_POINTS * min(1.0, each / M2_PER_PERSON)
    size = max(0.0, min(1.0, (m2 - SIZE_FROM_M2) / (SIZE_FULL_M2 - SIZE_FROM_M2)))
    return round(per_person + SPACE_SIZE_POINTS * math.sqrt(size))


def quality_points(place: Place) -> int:
    """Puntos de calidad: certificado, reformas acabadas y, restando, subir andando."""
    points = ENERGY_POINTS.get(place.energy, ENERGY_POINTS["G"])
    points += REFORM_POINTS * min(REFORM_MAX, place.reforms)
    if place.no_lift:
        points -= NO_LIFT_PENALTY
    return max(0, min(QUALITY_POINTS, points))


def comfort(place: Place, *, people: int = 1, gear: int = 0) -> Comfort:
    """El Confort de una casa.

    Args:
        people: Cuántas personas viven en ella (de momento, solo quien la habita).
        gear: Puntos de equipamiento (los muebles del hogar, fase 2).
    """
    return Comfort(
        space=space_points(place.m2, people),
        quality=quality_points(place),
        gear=max(0, min(GEAR_POINTS, gear)),
        area=AREA_BY_THEME.get(place.theme, 0),
    )


# -- Suministros ------------------------------------------------------------------------

#: Escala de los precios de `pisos`: 10 Y$ por euro.
YAPDOLLARS_PER_EURO = 10
#: Luz: euros por m² al mes con un certificado D (unos 63 € en un piso de 70 m², la media de
#: un hogar en España), por el factor de su certificado.
POWER_EUR_M2 = 0.9
POWER_FACTOR: dict[str, float] = {
    "A": 0.5,
    "B": 0.65,
    "C": 0.8,
    "D": 1.0,
    "E": 1.2,
    "F": 1.4,
    "G": 1.65,
}
#: Impuesto Especial sobre la Electricidad (art. 99 de la Ley 38/1992), que también se paga en
#: Canarias. El IGIC de la luz de una vivienda con hasta 10 kW contratados es el tipo cero
#: (Ley 4/2012 de Canarias), así que no lleva más.
POWER_TAX_RATE = 0.0511269632
#: Agua: euros por persona al mes. IGIC al tipo cero (el agua, Ley 4/2012).
WATER_EUR_PERSON = 15
#: Internet: la tarifa de fibra, en euros al mes. IGIC general (art. 51 de la Ley 4/2012).
INTERNET_EUR = 35


@dataclass(frozen=True, slots=True)
class Supplies:
    """Los suministros de un mes (un día del juego) de una casa.

    Lo que no es impuesto desaparece (las compañías no existen); los impuestos van al Estado.

    Attributes:
        power: La luz, sin su impuesto.
        water: El agua.
        internet: La fibra, sin su IGIC.
        power_tax: Impuesto Especial sobre la Electricidad.
        igic: El IGIC de la fibra.
    """

    power: int
    water: int
    internet: int
    power_tax: int
    igic: int

    @property
    def gone(self) -> int:
        """Lo que se llevan las compañías."""
        return self.power + self.water + self.internet

    @property
    def total(self) -> int:
        """Lo que se paga en total."""
        return self.gone + self.power_tax + self.igic


def supplies(place: Place, *, people: int = 1) -> Supplies:
    """Los suministros del mes de una casa.

    Tratamiento fiscal: la luz lleva el Impuesto Especial sobre la Electricidad (art. 99 de la
    Ley 38/1992) e IGIC al tipo cero (vivienda de hasta 10 kW, Ley 4/2012 de Canarias); el agua,
    IGIC al tipo cero; la fibra, IGIC general (art. 51 de la Ley 4/2012). Los dos impuestos van
    al Estado y lo demás desaparece. No es gasto deducible: es de la vivienda habitual, no de
    una actividad ni de un inmueble alquilado (art. 23 LIRPF).
    """
    factor = POWER_FACTOR.get(place.energy, POWER_FACTOR["G"])
    power = round(place.m2 * POWER_EUR_M2 * factor * YAPDOLLARS_PER_EURO)
    water = WATER_EUR_PERSON * max(1, people) * YAPDOLLARS_PER_EURO
    internet = INTERNET_EUR * YAPDOLLARS_PER_EURO
    return Supplies(
        power=power,
        water=water,
        internet=internet,
        power_tax=round(power * POWER_TAX_RATE),
        igic=taxes.igic(internet),
    )
