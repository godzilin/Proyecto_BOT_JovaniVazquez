"""Las calles de cada barrio de `pisos`, sus edificios y las unidades de cada edificio (9.14.0).

Cada barrio tiene 3 calles con 3 edificios, y **cada edificio es el de uno de los 9 inmuebles
destacados del barrio** (la tarjeta N vive en el edificio N): un piso del 2.º, en un bloque con
su local en el bajo y su garaje en el sótano; un chalé, en su casa; un garaje, en un
aparcamiento; un amarre, en un pantalán. Alrededor del destacado salen sus vecinos: unidades
nuevas, sin dueño, que se compran y se alquilan como cualquier inmueble. Plan entero en
`docs/pisos-calles.md`.

Nada de lo que ya existía cambia: los destacados conservan su clave, sus metros y su planta, y
las unidades nuevas tienen claves nuevas (`<edificio>_<hueco>`, p. ej. `triana_e3_2b`). El
catálogo sale de una regla fija (el destacado y el tema del barrio), sin azar del momento: una
prueba fija su huella (`fingerprint`) para que no cambie sin querer, porque cambiarlo dejaría
participaciones y contratos apuntando a unidades que ya no son las mismas.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from bot.services.housing_catalog import BARRIO_BY_KEY, BARRIOS, Listing, listings_of
from bot.services.housing_street_names import STREET_NAMES, UNIT_BLURBS

#: Los tipos de edificio, por lo que es su destacado.
BLOQUE, APARCAMIENTO, GUARDAMUEBLES, CASA = "bloque", "aparcamiento", "guardamuebles", "casa"
COMPLEJO, PANTALAN, POLIGONO, EDIFICIO = "complejo", "pantalan", "poligono", "edificio"
BUILDING_NAMES = {
    BLOQUE: "Bloque de pisos",
    APARCAMIENTO: "Aparcamiento",
    GUARDAMUEBLES: "Guardamuebles",
    CASA: "Casa",
    COMPLEJO: "Complejo de bungalows",
    PANTALAN: "Pantalán",
    POLIGONO: "Naves del polígono",
    EDIFICIO: "Edificio entero",
}
_BUILDING_OF_KIND = {
    "garaje": APARCAMIENTO,
    "trastero": GUARDAMUEBLES,
    "chalet": CASA,
    "terrera": CASA,
    "cueva": CASA,
    "finca": CASA,
    "casona": CASA,
    "bungalow": COMPLEJO,
    "amarre": PANTALAN,
    "nave": POLIGONO,
    "edificio": EDIFICIO,
}
#: Los que van en la tercera calle del barrio (la del puerto o el polígono).
_SPECIAL = frozenset({PANTALAN, POLIGONO})


@dataclass(frozen=True, slots=True)
class Theme:
    """Cómo son los bloques de un tipo de barrio.

    Attributes:
        floors: Plantas sobre la calle, con el bajo (de las que sale el ático, si lo hay).
        per_floor: Viviendas por planta.
        locales: Locales en el bajo (0: el bajo es una vivienda).
        garajes: Plazas de garaje en el sótano.
        trasteros: Trasteros en el sótano.
        attic: Si la planta de arriba es un ático.
        m2: Metros de una vivienda de las que se inventan, si el destacado no es vivienda.
        penthouse: Si la mitad de sus bloques tienen arriba un ático caro (9.16.0): un
            sobreático sobre el ático o, donde no hay ático, un ático con vistas.
    """

    floors: int
    per_floor: int
    locales: int
    garajes: int
    trasteros: int
    attic: bool
    m2: int
    penthouse: bool = False


THEMES: dict[str, Theme] = {
    "centro": Theme(4, 2, 1, 2, 1, True, 85, True),
    "historico": Theme(3, 1, 1, 0, 1, False, 95, True),
    "obrero": Theme(5, 2, 1, 1, 1, False, 70),
    "playa": Theme(4, 2, 1, 1, 1, True, 60, True),
    "marinero": Theme(3, 1, 1, 0, 1, False, 65, True),
    "lujo": Theme(3, 1, 1, 2, 0, True, 120, True),
    "rural": Theme(2, 1, 0, 0, 1, False, 90),
}
#: Edificios de más, sin destacado, por tipo de barrio (9.15.0): `(bloques, casas)`. El centro
#: y los barrios obreros son más densos que el campo: con los nueve de los destacados quedan 15
#: edificios (5 por calle), 12 (4) o 9 (3), y las casas se mezclan con los bloques.
EXTRA: dict[str, tuple[int, int]] = {
    "centro": (5, 1),
    "obrero": (4, 2),
    "historico": (1, 2),
    "playa": (2, 1),
    "marinero": (1, 2),
    "lujo": (1, 2),
    "rural": (0, 0),
}
#: Las casas de más de cada tipo de barrio: `(tipo, metros)` por orden.
EXTRA_HOUSES: dict[str, tuple[tuple[str, int], ...]] = {
    "centro": (("terrera", 95),),
    "obrero": (("terrera", 80), ("terrera", 70)),
    "historico": (("casona", 250), ("terrera", 90)),
    "playa": (("chalet", 190),),
    "marinero": (("terrera", 75), ("terrera", 65)),
    "lujo": (("chalet", 300), ("chalet", 240)),
    "rural": (),
}
HOUSE_NAMES = {"terrera": "Casa terrera", "casona": "Casa señorial", "chalet": "Chalé"}
#: Plazas por planta de un aparcamiento, trasteros de un guardamuebles, bungalows de un
#: complejo, amarres de un pantalán y naves de un polígono (con el destacado).
PLAZAS = 5
TRASTEROS = 5
BUNGALOWS = 4
AMARRES = 4
NAVES = 2

#: Los tipos que son vivienda (para los logros de las calles).
HOMES = frozenset({"estudio", "bajo", "piso2", "piso3", "atico", "loft", "duplex"})
#: Lo que piden de menos las plazas y los trasteros nuevos frente a los sueltos del catálogo:
#: los alquilan sobre todo los vecinos, que pagan menos. Sin esto, cientos de ellos a un 9-12 %
#: adelantaban el Falcon de la simulación por debajo del día 350.
BASEMENT_RENT = -0.25
#: Lo que piden y valen de más los áticos caros (9.16.0) por su terraza y sus vistas, frente a
#: un ático normal del mismo barrio y metros.
PENTHOUSE_RENT = 0.25
PENTHOUSE_VALUE = 0.40


@dataclass(frozen=True, slots=True)
class Street:
    """Una calle de un barrio.

    Attributes:
        key: `<barrio>_c<n>` (n de 1 a 3).
        barrio: Clave del barrio.
        name: Como en la placa («Calle Mayor de Triana»).
        blurb: Qué es o qué hay, en una línea.
    """

    key: str
    barrio: str
    name: str
    blurb: str


@dataclass(frozen=True, slots=True)
class Unit:
    """Un hueco de un edificio: la unidad que lo ocupa y dónde está.

    Attributes:
        key: Clave del inmueble (el destacado conserva la suya).
        level: Planta: -1 el sótano, 0 el bajo, 1 la primera… (para el corte del edificio).
        label: Cómo se dice («2.º B», «Plaza 3», «Local 1»).
    """

    key: str
    level: int
    label: str


@dataclass(frozen=True, slots=True)
class Building:
    """Un edificio de una calle, con sus unidades de abajo arriba.

    Attributes:
        key: `<barrio>_e<n>`: el edificio de la tarjeta N del barrio.
        barrio: Clave del barrio.
        street: Clave de su calle.
        number: Número de portal.
        kind: `BLOQUE`, `APARCAMIENTO`, `GUARDAMUEBLES`, `CASA`, `COMPLEJO`, `PANTALAN`,
            `POLIGONO` o `EDIFICIO`.
        featured: Clave del destacado (el inmueble del catálogo que vive aquí); vacía en los
            edificios de más (9.15.0), que no tienen.
        floors: Plantas sobre la calle, con el bajo (1 en una casa; 0 en lo que no tiene).
        units: Sus unidades, de abajo arriba y de izquierda a derecha.
    """

    key: str
    barrio: str
    street: str
    number: int
    kind: str
    featured: str
    floors: int
    units: tuple[Unit, ...]

    @property
    def name(self) -> str:
        """«Bloque de pisos», «Casa», «Pantalán»…"""
        return BUILDING_NAMES[self.kind]


def _seed(*parts: object) -> int:
    """Un número fijo para cada cosa (no `hash`: cambia entre arranques de Python)."""
    text = "|".join(str(p) for p in parts).encode()
    return int.from_bytes(hashlib.sha256(text).digest()[:4], "big")


def _vary(base: int, *parts: object, spread: float = 0.15) -> int:
    """`base` ± `spread`, fijo para `parts`."""
    step = (_seed(*parts) % 1001) / 1000 * 2 - 1
    return max(1, round(base * (1 + spread * step)))


def _home_kind(m2: int) -> str:
    return "estudio" if m2 < 40 else "piso2" if m2 < 76 else "piso3"


def _floor_of(listing: Listing) -> tuple[int, int]:
    """`(planta, plantas que ocupa)` de una vivienda o local; el ático se pone luego arriba."""
    text = listing.floor
    if text.startswith("Ático"):
        return -2, 1  # arriba del todo
    if text.startswith("Bajo y"):
        return 0, 2
    numbers = [int(n) for n in re.findall(r"(\d+)\.º", text)]
    if not numbers:
        return (0, 1) if text.startswith("Bajo") else (1, 1)  # «Casa»: un piso de una casa
    return numbers[0], len(numbers)


def _unit(
    building: str, barrio: str, slot: str, kind: str, m2: int, label: str, street: Street,
    number: int, energy: str, premium: bool = False,
) -> Listing:  # fmt: skip
    """Una unidad nueva de un edificio, con su clave `<edificio>_<hueco>`. Un ático caro
    (`premium`) pide y vale más (`PENTHOUSE_RENT`, `PENTHOUSE_VALUE`)."""
    blurbs = UNIT_BLURBS["atico_lujo" if premium else kind]
    rent_bonus = BASEMENT_RENT if kind in ("garaje", "trastero") else 0.0
    return Listing(
        key=f"{building}_{slot}",
        barrio=barrio,
        kind=kind,
        m2=m2,
        title=f"{label} · {street.name}, {number}",
        blurb=blurbs[_seed(building, slot) % len(blurbs)],
        floor=label,
        energy=energy,
        rent_bonus=PENTHOUSE_RENT if premium else rent_bonus,
        value_bonus=PENTHOUSE_VALUE if premium else 0.0,
    )


def _bloque(
    key: str, featured: Listing | None, street: Street, number: int, theme: Theme, barrio: str
) -> tuple[int, list[tuple[Unit, Listing | None]]]:
    """Las unidades de un bloque alrededor de su destacado (que conserva su sitio y su clave),
    o de un bloque de los de más, que no tiene destacado."""
    if featured is None:
        floor, span = -3, 0  # sin destacado: nada que colocar
    else:
        floor, span = _floor_of(featured) if featured.kind != "local" else (0, 1)
    attic = theme.attic or floor == -2
    floors = max(theme.floors, floor + span + (1 if attic else 0), 2 if attic else 1)
    top = floors - 1 if attic else None  # la planta del ático
    # Los vecinos se parecen al destacado si es un piso; si es un estudio, un bajo o un ático,
    # son pisos del tamaño del barrio (un bloque entero de estudios adelantaba el Falcon).
    alike = featured is not None and featured.kind in ("piso2", "piso3", "loft", "duplex")
    base_m2 = featured.m2 if featured is not None and alike else theme.m2
    energy = featured.energy if featured is not None else "CDEFG"[_seed(key, "energia") % 5]
    out: list[tuple[Unit, Listing | None]] = []

    def new(slot: str, level: int, kind: str, m2: int, label: str, premium: bool = False) -> None:
        listing = _unit(key, barrio, slot, kind, m2, label, street, number, energy, premium)
        out.append((Unit(listing.key, level, label), listing))

    for n in range(1, theme.garajes + 1):
        new(f"g{n}", -1, "garaje", 12, f"Plaza {n}")
    for n in range(1, theme.trasteros + 1):
        new(f"t{n}", -1, "trastero", _vary(5, key, "t", n, spread=0.3), f"Trastero {n}")
    # El bajo: sus locales (o una vivienda en los barrios sin comercio), con el destacado si
    # vive ahí.
    ground = max(theme.locales, 1)
    for n in range(1, ground + 1):
        if n == 1 and floor == 0 and featured is not None:
            label = "Local 1" if featured.kind == "local" else featured.floor
            out.append((Unit(featured.key, 0, label), None))
            continue
        if theme.locales:
            new(f"b{n}", 0, "local", _vary(55, key, "b", n, spread=0.3), f"Local {n}")
        else:
            new(f"b{n}", 0, "bajo", _vary(base_m2, key, "b", n), "Bajo")
    # Las plantas, con el destacado en la suya (un dúplex ocupa también la de encima).
    busy = {(floor + i, 0) for i in range(span)} if floor > 0 else set()
    for level in range(1, floors):
        if level == top:
            break
        for n in range(theme.per_floor):
            letter = "ABCD"[n]
            if (level, n) in busy and featured is not None:
                if level == floor:
                    # Como sus vecinos («2.º A»), salvo un dúplex, que dice sus dos plantas.
                    label = featured.floor if span > 1 else f"{level}.º {letter}"
                    out.append((Unit(featured.key, level, label), None))
                continue
            m2 = _vary(base_m2, key, level, n)
            new(f"{level}{letter.lower()}", level, _home_kind(m2), m2, f"{level}.º {letter}")
    if top is not None:
        if floor == -2 and featured is not None:
            out.append((Unit(featured.key, top, featured.floor), None))
        else:
            new("at", top, "atico", _vary(round(base_m2 * 1.1), key, "at"), "Ático")
    # Un ático caro en la mitad de los bloques de los barrios con vistas (9.16.0), en una planta
    # nueva arriba: sobre el ático, un sobreático; donde no hay ático, un ático con vistas. Con
    # claves nuevas (`_sa`, `_va`): lo de antes no cambia.
    if theme.penthouse and _seed(key, "atico") % 2 == 0:
        slot, label = ("sa", "Sobreático") if top is not None else ("va", "Ático")
        m2 = _vary(round(base_m2 * 1.3), key, "lujo")
        new(slot, floors, "atico", m2, label, premium=True)
        floors += 1
    return floors, out


def _group(
    key: str, featured: Listing, street: Street, number: int, kind: str
) -> tuple[int, list[tuple[Unit, Listing | None]]]:
    """Un aparcamiento, un guardamuebles, un complejo, un pantalán o un polígono.

    El destacado es el primero de su grupo («Nave 1») y los nuevos siguen la cuenta.
    """
    first = {GUARDAMUEBLES: "Trastero", COMPLEJO: "Bungalow", PANTALAN: "Amarre", POLIGONO: "Nave"}
    out: list[tuple[Unit, Listing | None]] = [
        (Unit(featured.key, 0, f"{first.get(kind, featured.floor)} 1"), None)
    ]
    barrio = featured.barrio

    def new(slot: str, level: int, unit_kind: str, m2: int, label: str) -> None:
        listing = _unit(key, barrio, slot, unit_kind, m2, label, street, number, featured.energy)
        out.append((Unit(listing.key, level, label), listing))

    if kind == APARCAMIENTO:
        levels = max(1, max((int(n) for n in re.findall(r"-(\d)", featured.floor)), default=1))
        out = [(Unit(featured.key, -levels, f"Plaza {levels}{PLAZAS:02d}"), None)]
        for level in range(1, levels + 1):
            for n in range(1, PLAZAS + (0 if level == levels else 1)):
                new(f"p{level}{n}", -level, "garaje", 12, f"Plaza {level}{n:02d}")
        return 0, sorted(out, key=lambda pair: (pair[0].level, pair[0].label))
    if kind == GUARDAMUEBLES:
        for n in range(1, TRASTEROS):
            new(f"t{n}", 0, "trastero", _vary(6, key, n, spread=0.4), f"Trastero {n + 1}")
        return 1, out
    if kind == COMPLEJO:
        for n in range(1, BUNGALOWS):
            new(f"u{n}", 0, "bungalow", _vary(featured.m2, key, n), f"Bungalow {n + 1}")
        return 1, out
    if kind == PANTALAN:
        for n in range(1, AMARRES + 1):
            new(f"a{n}", 0, "amarre", _vary(featured.m2, key, n, spread=0.5), f"Amarre {n + 1}")
        return 0, out
    for n in range(1, NAVES):
        new(f"n{n}", 0, "nave", _vary(featured.m2, key, n, spread=0.25), f"Nave {n + 1}")
    return 1, out


def _barrio(barrio: str) -> tuple[list[Street], list[Building], list[Listing]]:
    """Las calles, los edificios y las unidades nuevas de un barrio.

    Los edificios 1 a 9 son los de los destacados (el N, el de la tarjeta N) y salen igual que
    en la 9.14.0; los de más (10 en adelante) son bloques y casas sin destacado. Todos se
    reparten por las calles mezclados (con una regla fija), salvo los del puerto y el polígono,
    que van a la tercera.
    """
    names = STREET_NAMES[barrio]
    streets = [
        Street(f"{barrio}_c{n}", barrio, name, blurb)
        for n, (name, blurb) in enumerate(names, start=1)
    ]
    theme_key = BARRIO_BY_KEY[barrio].theme
    theme = THEMES[theme_key]
    featured = list(listings_of(barrio))
    plans: list[tuple[str, str, Listing | None, tuple[str, int] | None]] = [
        (f"{barrio}_e{n}", _BUILDING_OF_KIND.get(item.kind, BLOQUE), item, None)
        for n, item in enumerate(featured, start=1)
    ]
    blocks, houses = EXTRA[theme_key]
    extra = [BLOQUE] * blocks + [CASA] * houses
    for n, kind in enumerate(extra, start=len(featured) + 1):
        house = EXTRA_HOUSES[theme_key][n - len(featured) - 1 - blocks] if kind == CASA else None
        plans.append((f"{barrio}_e{n}", kind, None, house))
    per_street = len(plans) // len(streets)
    ordinary = sorted((p for p in plans if p[1] not in _SPECIAL), key=lambda p: _seed(p[0]))
    order = ordinary + [p for p in plans if p[1] in _SPECIAL]
    buildings, units = [], []
    for place, (key, kind, item, house) in enumerate(order):
        street = streets[min(place // per_street, len(streets) - 1)]
        number = 2 + (place % per_street) * 10 + _seed(key) % 7
        if kind == BLOQUE:
            floors, pairs = _bloque(key, item, street, number, theme, barrio)
        elif item is not None and kind in (CASA, EDIFICIO):
            floors, pairs = 1, [(Unit(item.key, 0, item.floor), None)]
        elif item is not None:
            floors, pairs = _group(key, item, street, number, kind)
        else:
            house_kind, m2 = house or ("terrera", 80)
            label = HOUSE_NAMES[house_kind]
            listing = _unit(key, barrio, "casa", house_kind, _vary(m2, key, "casa"), label,
                            street, number, "CDEFG"[_seed(key, "energia") % 5])  # fmt: skip
            floors, pairs = 1, [(Unit(listing.key, 0, label), listing)]
        buildings.append(
            Building(
                key,
                barrio,
                street.key,
                number,
                kind,
                item.key if item is not None else "",
                floors,
                tuple(unit for unit, _listing in pairs),
            )  # fmt: skip
        )
        units += [listing for _unit_, listing in pairs if listing is not None]
    buildings.sort(key=lambda b: int(b.key.rsplit("_e", 1)[1]))
    return streets, buildings, units


def _catalog() -> tuple[tuple[Street, ...], tuple[Building, ...], tuple[Listing, ...]]:
    streets: list[Street] = []
    buildings: list[Building] = []
    units: list[Listing] = []
    for barrio in BARRIOS:
        s, b, u = _barrio(barrio.key)
        streets += s
        buildings += b
        units += u
    return tuple(streets), tuple(buildings), tuple(units)


STREETS, BUILDINGS, STREET_UNITS = _catalog()
STREET_BY_KEY: dict[str, Street] = {s.key: s for s in STREETS}
BUILDING_BY_KEY: dict[str, Building] = {b.key: b for b in BUILDINGS}
STREET_UNIT_BY_KEY: dict[str, Listing] = {u.key: u for u in STREET_UNITS}
#: El edificio de cada unidad (los destacados incluidos).
_BUILDING_OF: dict[str, str] = {u.key: b.key for b in BUILDINGS for u in b.units}


def building_of(key: str) -> Building | None:
    """El edificio en que está un inmueble (o una planta o habitación suya), si está en uno."""
    while key:
        found = _BUILDING_OF.get(key)
        if found is not None:
            return BUILDING_BY_KEY[found]
        head, sep, _tail = key.rpartition("~")
        key = head if sep else ""
    return None


def streets_of(barrio: str) -> tuple[Street, ...]:
    """Las tres calles de un barrio."""
    return tuple(s for s in STREETS if s.barrio == barrio)


def buildings_of(street: str) -> tuple[Building, ...]:
    """Los edificios de una calle, por su número."""
    return tuple(sorted((b for b in BUILDINGS if b.street == street), key=lambda b: b.number))


#: El solar de cada calle (9.56.0, `bot.services.housing_solars`): lo que se construye en él es
#: el edificio `<calle>_s` y sus unidades, `<calle>_s_<hueco>` (`triana_c2_s_1a`).
SOLAR_SUFFIX = "_s"
_SOLAR_UNIT = re.compile(r"^[a-z]+_c\d+_s_")


def is_solar_unit(key: str) -> bool:
    """Si una clave es la de una unidad construida en un solar (o una planta o habitación suya)."""
    return bool(_SOLAR_UNIT.match(key))


def is_penthouse(key: str) -> bool:
    """Si una clave es la de un ático caro (9.16.0): un sobreático o un ático con vistas."""
    return key in STREET_UNIT_BY_KEY and key.endswith(("_sa", "_va"))


def map_order(barrio: str) -> tuple[Building, ...]:
    """Los edificios de un barrio en el orden en que se leen en su plano: calle a calle y, en
    cada una, de izquierda a derecha (por su número de portal). Su número en el plano y en los
    botones es su puesto aquí, desde 1 (9.15.2)."""
    return tuple(b for street in streets_of(barrio) for b in buildings_of(street.key))


def units_of_barrio(barrio: str) -> tuple[Listing, ...]:
    """Las unidades nuevas de las calles de un barrio (sin los destacados)."""
    return tuple(u for u in STREET_UNITS if u.barrio == barrio)


def fingerprint() -> str:
    """La huella del catálogo: cambia si cambia cualquier clave, tipo, metro o planta."""
    rows = sorted(
        (b.key, b.street, b.kind, b.number, u.key, u.level, u.label)
        for b in BUILDINGS
        for u in b.units
    )
    rows += sorted((u.key, u.kind, u.m2, u.floor) for u in STREET_UNITS)  # type: ignore[misc]
    return hashlib.sha256(repr(rows).encode()).hexdigest()[:16]
