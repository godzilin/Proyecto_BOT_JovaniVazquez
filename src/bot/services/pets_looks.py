"""Cómo se ve cada especie en su retrato: su dibujo, sus razas y qué huecos se puede vestir.

El retrato de `mascota` (`assets/mascotas/escena.html`) no pega un emoji: dibuja cada
mascota en canvas, sentada de frente, cabezona y con ojos grandes, para que tenga cara y
gestos (se duerme, se ríe, pone cara de aburrida). Cada especie sigue un **plan**
(`Look.plan`): la forma general del cuerpo, que comparten las especies parecidas (los
perros, los pájaros). Lo que distingue a una de otra dentro de su plan (colores, orejas,
manchas, cara de siempre) va en los parámetros de su **raza** (`Breed.look`), que la
escena lee tal cual.

**Razas.** Cada especie tiene la suya de serie (la primera de `breeds`, gratis) y, la
mayoría, otras que se compran: un artículo oculto de la tienda (`raza_<especie>_<raza>`),
uno por persona, con IGIC general, que se paga desde el panel de la mascota (🧬 Raza) y
se queda en la mochila. Quien tiene la raza la puede poner y quitar las veces que quiera
a su mascota de esa especie (como mucho hay una de cada especie por persona). No se
vende en el Rastro: va con la mascota. No da vínculo, XP ni dinero.

**Huecos.** No todas las especies se pueden vestir entera: un pez no tiene cuello y el
hámster es casi todo cabeza. `PLAN_SLOTS` dice qué huecos de ropa tiene cada plan; el
fondo vale siempre.

**Añadir una especie** (además de lo de `pets_catalog`): su plan (uno que ya exista o uno
nuevo en la escena), sus razas con su `look`, y mirarla en la hoja de muestras
(`docs/muestras_mascotas.py`) con todas las prendas puestas.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

#: Prefijo de la clave de artículo de una raza que se compra.
CATALOG_PREFIX = "raza_"


def _frozen(values: Mapping[str, object] | None = None) -> Mapping[str, object]:
    return MappingProxyType(dict(values or {}))


@dataclass(frozen=True, slots=True)
class Breed:
    """Una raza (o variedad) de una especie.

    Attributes:
        key: Clave estable dentro de su especie; no se cambia nunca.
        name: Cómo se llama («Siamés»).
        price: Lo que cuesta sin IGIC; 0 en la de serie.
        look: Parámetros de dibujo de su plan (colores, orejas, manchas…), que la escena
            lee tal cual.
    """

    key: str
    name: str
    price: int
    look: Mapping[str, object] = field(default_factory=_frozen)


@dataclass(frozen=True, slots=True)
class Look:
    """El dibujo de una especie: su plan y sus razas (la primera, la de serie)."""

    plan: str
    breeds: tuple[Breed, ...]

    @property
    def default(self) -> Breed:
        """La raza de serie: la que tiene toda mascota recién llegada."""
        return self.breeds[0]

    def breed(self, key: str | None) -> Breed:
        """La raza `key`, o la de serie si no existe (o es `None`)."""
        return next((b for b in self.breeds if b.key == key), self.default)


#: Huecos de ropa (sin el fondo, que vale siempre) que tiene cada plan.
ALL_SLOTS = ("cabeza", "ojos", "cuello", "cuerpo")
PLAN_SLOTS: dict[str, tuple[str, ...]] = {
    "pez": ("cabeza", "ojos"),
    "mosquito": ("cabeza", "ojos"),
    "pulpo": ("cabeza", "ojos", "cuello"),
    "cucaracha": ("cabeza", "ojos", "cuerpo"),
}


def _b(key: str, name: str, price: int = 0, **look: object) -> Breed:
    return Breed(key, name, price, _frozen(look))


# fmt: off
LOOKS: dict[str, Look] = {
    "gato": Look("gato", (
        _b("europeo", "Común europeo atigrado"),
        _b("naranja", "Naranja", 1_500),
        _b("siames", "Siamés", 4_000),
        _b("carey", "Carey", 2_500),
        _b("persa", "Persa blanco", 6_000),
    )),
    "gato_callejero": Look("gato", (
        _b("negro", "Negro y tuerto"),
        _b("pardo", "Pardo y tuerto", 1_000),
    )),
    "perro": Look("perro", (
        _b("mestizo", "Mestizo canela"),
        _b("beagle", "Beagle", 3_000),
        _b("golden", "Golden retriever", 5_000),
        _b("salchicha", "Teckel", 4_000),
        _b("pastor", "Pastor alemán", 5_000),
    )),
    "bardino": Look("perro", (
        _b("atigrado", "Atigrado"),
        _b("leonado", "Leonado", 4_000),
    )),
    "presa": Look("perro", (
        _b("atigrado", "Atigrado"),
        _b("leonado", "Leonado", 30_000),
        _b("negro", "Negro", 30_000),
    )),
    "perro_sanxe": Look("perro", (
        _b("unico", "El de verdad"),
    )),
    "caniche": Look("perro", (
        _b("blanco", "Blanco"),
        _b("albaricoque", "Albaricoque", 20_000),
        _b("negro", "Negro", 20_000),
        _b("rosa", "Teñido de rosa", 35_000),
    )),
    "hamster": Look("hamster", (
        _b("dorado", "Dorado"),
        _b("ruso", "Ruso blanco", 800),
        _b("panda", "Panda", 1_200),
    )),
    "conejo": Look("conejo", (
        _b("belier", "Belier gris"),
        _b("blanco", "Blanco", 1_500),
        _b("holandes", "Holandés", 2_000),
        _b("leon", "Cabeza de león", 3_000),
    )),
    "huron": Look("huron", (
        _b("turon", "Turón"),
        _b("albino", "Albino", 4_000),
        _b("canela", "Canela", 4_000),
    )),
    "panda": Look("oso", (
        _b("gigante", "Gigante"),
        _b("qinling", "De Qinling, marrón", 300_000),
    )),
    "cabra": Look("cabra", (
        _b("majorera", "Majorera canela"),
        _b("negra", "Negra", 5_000),
        _b("pintada", "Pintada", 6_000),
    )),
    "caballo": Look("caballo", (
        _b("castano", "Castaño"),
        _b("tordo", "Tordo", 150_000),
        _b("azabache", "Negro azabache", 200_000),
        _b("palomino", "Palomino", 180_000),
    )),
    "canario": Look("pajaro", (
        _b("amarillo", "Amarillo"),
        _b("rojo", "Rojo de factor", 1_500),
        _b("blanco", "Blanco", 1_500),
    )),
    "loro": Look("pajaro", (
        _b("gris", "Gris africano"),
        _b("guacamayo", "Guacamayo azul", 12_000),
        _b("cacatua", "Cacatúa blanca", 9_000),
    )),
    "cotorra": Look("pajaro", (
        _b("verde", "Argentina verde"),
        _b("azul", "Azul", 300),
    )),
    "paloma": Look("pajaro", (
        _b("gris", "Gris de plaza"),
        _b("blanca", "Blanca", 120),
    )),
    "gallo": Look("gallo", (
        _b("giro", "Giro"),
        _b("colorado", "Colorado", 3_500),
        _b("blanco", "Blanco", 3_500),
        _b("negro", "Negro", 3_500),
    )),
    "pavo_real": Look("pavo", (
        _b("azul", "Azul"),
        _b("verde", "Verde", 60_000),
        _b("blanco", "Blanco", 80_000),
    )),
    "pez": Look("pez", (
        _b("rojo", "Pez rojo"),
        _b("betta", "Betta azul", 300),
        _b("payaso", "Pez payaso", 600),
    )),
    "koi": Look("pez", (
        _b("kohaku", "Kohaku"),
        _b("showa", "Showa", 20_000),
        _b("ogon", "Ogon dorada", 30_000),
    )),
    "tortuga": Look("tortuga", (
        _b("florida", "De Florida"),
        _b("mora", "Mora", 3_000),
    )),
    "lagarto": Look("lagarto", (
        _b("hierro", "Gigante de El Hierro"),
        _b("tizon", "Tizón", 20_000),
    )),
    "coqui": Look("rana", (
        _b("pardo", "Pardo"),
        _b("verde", "Verde", 2_500),
    )),
    "pulpo": Look("pulpo", (
        _b("rojo", "Rojo"),
        _b("anillado", "De anillos azules", 6_666),
    )),
    "pedrusco": Look("piedra", (
        _b("granito", "Granito"),
        _b("basalto", "Basalto canario", 50),
        _b("obsidiana", "Obsidiana", 300),
        _b("purpurina", "Con purpurina", 1_000),
    )),
    "cucaracha": Look("cucaracha", (
        _b("comun", "Común"),
        _b("americana", "Americana", 66),
    )),
    "mosquito": Look("mosquito", (
        _b("tigre", "Tigre"),
        _b("comun", "Común", 10),
    )),
}
# fmt: on


def look_of(species: str) -> Look:
    """El dibujo de una especie (las que aún no tengan, con el plan de los gatos)."""
    return LOOKS.get(species) or Look("gato", (Breed("serie", "De serie", 0),))


def slots_of(species: str) -> tuple[str, ...]:
    """Huecos de ropa que se pueden vestir en una especie (sin el fondo)."""
    return PLAN_SLOTS.get(look_of(species).plan, ALL_SLOTS)


def breed_catalog_key(species: str, breed: str) -> str:
    """Clave del artículo de una raza que se compra (`raza_gato_siames`)."""
    return f"{CATALOG_PREFIX}{species}_{breed}"


#: Artículo de cada raza que se compra → `(especie, raza)`.
BREED_ITEMS: dict[str, tuple[str, Breed]] = {
    breed_catalog_key(species, breed.key): (species, breed)
    for species, look in LOOKS.items()
    for breed in look.breeds[1:]
}


def breed_of_item(catalog_key: str | None) -> tuple[str, Breed] | None:
    """La especie y la raza de un artículo, o `None` si no es una raza."""
    return BREED_ITEMS.get(catalog_key or "")


def is_breed(catalog_key: str | None) -> bool:
    """Si un artículo es una raza de mascota."""
    return (catalog_key or "") in BREED_ITEMS
