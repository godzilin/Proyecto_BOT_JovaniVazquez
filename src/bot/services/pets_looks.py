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
            lee tal cual. Todos son opcionales: lo que falta lo pone la escena (un gato
            pardo, un pájaro amarillo...). Los colores son `#rrggbb`. Vocabulario por plan:

            - ``gato``: fur, belly, muzzle, nose, eye (iris), stripes ("atigrado") y stripe,
              points (extremidades oscuras: siamés), patches y bodyPatches (manchas), flat
              (cara aplastada), fluffy (pelo largo), face ("tuerto": un ojo cerrado y cicatriz;
              si no, la cara de siempre, chulo con los párpados a media asta).
            - ``perro``: coat, muzzle, ears ("caidas", "largas", "pie", "rosa", "rizos"),
              earColor, saddle, chest, blaze, mask, brindle, legs, tip, ruff, jowls, snout,
              patches, head ("ancha"), body ("largo": teckel), curly (caniche).
            - ``hamster``: coat, belly, feet, cap, stripe, ring, inner, nose.
            - ``conejo``: coat, belly, muzzle, inner, earColor, ears ("largas", "caidas",
              "cortas"), mane y maneColor (cabeza de león), blaze, patches, bodyPatches, tail,
              eyeInk.
            - ``huron``: coat, belly, mask, points, muzzle, crown, inner, nose, eye (iris).
            - ``oso``: light, dark, eyePatch, muzzle, bamboo (``False`` lo quita); su cara de
              siempre son las ojeras de dormilón.
            - ``cabra``: coat, belly, muzzle, horn, ear, beard, hoof, blaze, patches,
              bodyPatches, sclera; su cara de siempre, pupilas rectangulares y mirada loca.
            - ``caballo``: coat, mane, muzzle, blaze, socks, dapples, sheen.
            - ``pajaro``: body, head, belly, wing, wingTip, tail, feet, beak, beakKind
              ("corto", "ganchudo", "grande"), crest y crestColor, facePatch, faceLines,
              headTop, neckPatch (dos colores), bars, eye (iris), cheek, face ("parlanchin":
              pico abierto).
            - ``gallo``: body, hackle, wing, tail (3 colores), comb, beak, legs.
            - ``pavo``: body, neck, tail, rim, ring, spot (el ojo de la cola), crest.
            - ``pez``: body, fin, belly, patches, stripes (x de las franjas) y stripeColor,
              scales, tailKind ("largo"), whiskers, sheen.
            - ``tortuga``: shell, scute, skin, rim, ear, line, blotches.
            - ``lagarto``: skin, belly, spots, spotAt ("flancos", "cabeza", "ambos"),
              blotches, eye (iris).
            - ``rana``: skin, belly, stripe, pads, eye (iris), spots.
            - ``pulpo``: body, belly, spots, rings y ringColor.
            - ``piedra``: base, speckle (colores), pores, facets y sheen, glitter (colores),
              moss.
            - ``cucaracha``: shell, edge, ring.
            - ``mosquito``: body, head, belly, stripes, eye (iris).

            Las manchas (`patches`) son tuplas `(color, x, y, rx, ry, giro)` relativas al
            centro de la cabeza; las del cuerpo (`bodyPatches`), en el espacio del plan.
            `face` nombra la cara de siempre. Solo cambia el dibujo en el gato ("tuerto") y
            en el pájaro ("parlanchin"); en los demás (ojeras del oso, mirada loca de la cabra,
            cejas del gallo, listo del pulpo, ojos saltones de la piedra, sonrisa de la
            cucaracha) viene fija en el plan y la clave solo lo documenta. Las otras caras del
            momento (contenta, dormida, fiesta) son iguales para todos.
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
        _b("europeo", "Común europeo atigrado", fur="#9a8470", belly="#e6d8c4",
           stripes="atigrado", stripe="#5a4636", eye="#d9b030"),
        _b("naranja", "Naranja", 1_500, fur="#f09a46", belly="#fde3c0",
           stripes="atigrado", stripe="#c9702a", eye="#8cc850"),
        _b("siames", "Siamés", 4_000, fur="#f3e6d0", belly="#fbf1e0", points="#5a4034",
           eye="#4aa3e8"),
        _b("carey", "Carey", 2_500, fur="#2e2830", belly="#463c44", eye="#d9b030",
           patches=(("#e8873a", -44, -8, 34, 40, 0.3), ("#f3e6d0", 10, 24, 15, 17, 0.0)),
           bodyPatches=(("#e8873a", -34, -76, 26, 22, 0.4), ("#e8873a", 30, -40, 22, 26, 0.0))),
        _b("persa", "Persa blanco", 6_000, fur="#fbf8f2", belly="#ffffff", flat=True,
           fluffy=True, nose="#f0a0b0", eye="#e8923a"),
    )),
    "gato_callejero": Look("gato", (
        _b("negro", "Negro y tuerto", fur="#2e2a34", belly="#3e3844", eye="#c8d838",
           face="tuerto"),
        _b("pardo", "Pardo y tuerto", 1_000, fur="#8a7058", belly="#b8a088",
           stripes="atigrado", stripe="#4a382a", eye="#d9b030", face="tuerto"),
    )),
    "perro": Look("perro", (
        _b("mestizo", "Mestizo canela", coat="#c8935a", muzzle="#ecd2a8", ears="caidas",
           earColor="#a8703c"),
        _b("beagle", "Beagle", 3_000, coat="#c98a4a", saddle="#3d2e2a", chest="#fbf3e6",
           blaze="#fbf3e6", muzzle="#fbf3e6", ears="largas", earColor="#8a5230", tip="#fbf3e6",
           legs="#f6ecdc"),
        _b("golden", "Golden retriever", 5_000, coat="#e3a845", muzzle="#f4d89a",
           ears="caidas", earColor="#c98a2c", ruff="#ecb85a"),
        _b("salchicha", "Teckel", 4_000, coat="#9a5a30", muzzle="#c48a5a", ears="largas",
           earColor="#7a4222", body="largo"),
        _b("pastor", "Pastor alemán", 5_000, coat="#d09a52", saddle="#2f2628", mask="#3a2c2c",
           ears="pie", earColor="#2f2628", snout=True, legs="#d09a52"),
    )),
    "bardino": Look("perro", (
        _b("atigrado", "Atigrado", coat="#b4875a", brindle="#4a3426", mask="#3d3030",
           ears="rosa", earColor="#8a6a48", head="ancha"),
        _b("leonado", "Leonado", 4_000, coat="#d6a465", mask="#3d3030", ears="rosa",
           earColor="#b0803c", head="ancha"),
    )),
    "presa": Look("perro", (
        _b("atigrado", "Atigrado", coat="#a07a52", brindle="#3a2a20", mask="#2e2428",
           ears="rosa", earColor="#4a3a30", head="ancha", jowls=True),
        _b("leonado", "Leonado", 30_000, coat="#c9965a", mask="#2e2428", ears="rosa",
           earColor="#5a4030", head="ancha", jowls=True),
        _b("negro", "Negro", 30_000, coat="#2e2a34", mask="#3a3440", chest="#e8e0e8",
           ears="rosa", earColor="#26222e", head="ancha", jowls=True),
    )),
    "perro_sanxe": Look("perro", (
        _b("unico", "El de verdad", coat="#bb8b5e", muzzle="#ead5b0", chest="#f4ead8",
           ears="caidas", earColor="#9a6a40"),
    )),
    "caniche": Look("perro", (
        _b("blanco", "Blanco", coat="#fbf8f2", curly=True, ears="rizos"),
        _b("albaricoque", "Albaricoque", 20_000, coat="#e6b27a", curly=True, ears="rizos"),
        _b("negro", "Negro", 20_000, coat="#34303c", curly=True, ears="rizos"),
        _b("rosa", "Teñido de rosa", 35_000, coat="#f6a9c4", curly=True, ears="rizos"),
    )),
    "hamster": Look("hamster", (
        _b("dorado", "Dorado", coat="#d9a35e", belly="#fff0d8", stripe="#b07a3a"),
        _b("ruso", "Ruso blanco", 800, coat="#e2dfdc", belly="#ffffff", cap="#c9c4ce",
           stripe="#9a94a6"),
        _b("panda", "Panda", 1_200, coat="#fbf8f2", belly="#ffffff", cap="#34303c",
           ring="#34303c"),
    )),
    "conejo": Look("conejo", (
        _b("belier", "Belier gris", coat="#8e8c96", belly="#c8c6cf", ears="caidas",
           earColor="#76747e", inner="#d9a0b0"),
        _b("blanco", "Blanco", 1_500, coat="#fbf8fc", belly="#ffffff", ears="largas",
           eyeInk="#c0344f"),
        _b("holandes", "Holandés", 2_000, coat="#fbf8fc", belly="#ffffff", ears="largas",
           earColor="#3e3a46", inner="#d9a0b0",
           patches=(("#3e3a46", -54, 6, 32, 44, 0.2), ("#3e3a46", 54, 6, 32, 44, -0.2)),
           bodyPatches=(("#3e3a46", 0, -22, 70, 30, 0.0),)),
        _b("leon", "Cabeza de león", 3_000, coat="#e8c08a", belly="#fbe9cc", ears="cortas",
           mane=True, maneColor="#b0763e"),
    )),
    "huron": Look("huron", (
        _b("turon", "Turón", coat="#cdb48e", mask="#5a4234", points="#5a4234",
           muzzle="#f0e4cc"),
        _b("albino", "Albino", 4_000, coat="#fbf6f0", belly="#ffffff", eye="#d63a52"),
        _b("canela", "Canela", 4_000, coat="#c07a46", mask="#8a4e2c", points="#8a4e2c",
           muzzle="#f0d8b8"),
    )),
    "panda": Look("oso", (
        _b("gigante", "Gigante", light="#fbfaf6", dark="#34303c", face="ojeras"),
        _b("qinling", "De Qinling, marrón", 300_000, light="#f0dfbc", dark="#7a4a2a",
           face="ojeras"),
    )),
    "cabra": Look("cabra", (
        _b("majorera", "Majorera canela", coat="#b8825a", belly="#e8cfae", horn="#e9dcc0",
           ear="#9a6a44", beard="#6a4a34", blaze="#f1e4cc", face="loca"),
        _b("negra", "Negra", 5_000, coat="#34303c", belly="#4a4450", horn="#c8bca0",
           ear="#26222e", beard="#26222e", face="loca"),
        _b("pintada", "Pintada", 6_000, coat="#fbf6ee", belly="#ffffff", ear="#b0723c",
           beard="#e8d8c0", face="loca",
           patches=(("#b0723c", -44, -16, 26, 32, 0.0), ("#2e2a34", 42, 6, 20, 24, 0.0)),
           bodyPatches=(("#b0723c", -30, -70, 26, 22, 0.0), ("#2e2a34", 34, -44, 24, 26, 0.0))),
    )),
    "caballo": Look("caballo", (
        _b("castano", "Castaño", coat="#a05e34", mane="#3a2418", blaze="#fff2e0"),
        _b("tordo", "Tordo", 150_000, coat="#aab0bc", mane="#6a7080", muzzle="#8a8f9c",
           dapples=True),
        _b("azabache", "Negro azabache", 200_000, coat="#34303c", mane="#201c28",
           muzzle="#4a4452", sheen="#6a6a96"),
        _b("palomino", "Palomino", 180_000, coat="#e4b25a", mane="#fff1cf", blaze="#ffffff",
           muzzle="#f0cc88"),
    )),
    "canario": Look("pajaro", (
        _b("amarillo", "Amarillo", body="#f6d23a", belly="#fbe888", wing="#e8b422",
           beak="#f0902e"),
        _b("rojo", "Rojo de factor", 1_500, body="#e2452f", belly="#ee7a5a", wing="#b82f20",
           beak="#f0b070"),
        _b("blanco", "Blanco", 1_500, body="#fbf8f6", belly="#ffffff", wing="#e6e0ea",
           beak="#f0a050"),
    )),
    "loro": Look("pajaro", (
        _b("gris", "Gris africano", body="#8e949c", belly="#a8adb5", wing="#6f7680",
           tail="#d6473a", beak="#2e2c34", beakKind="ganchudo", facePatch="#f2f2f4",
           face="parlanchin"),
        _b("guacamayo", "Guacamayo azul", 12_000, body="#2e6fd0", belly="#f2c02e",
           wing="#2358b0", tail="#2358b0", beak="#2e2c34", beakKind="grande",
           facePatch="#fbfaf4", faceLines="#3a2a3a", headTop="#58b068", face="parlanchin"),
        _b("cacatua", "Cacatúa blanca", 9_000, body="#fbfbfd", belly="#ffffff", wing="#e8e8f0",
           tail="#f4e48a", beak="#6a6a74", beakKind="ganchudo", crest=True,
           crestColor="#f5d24a", face="parlanchin"),
    )),
    "cotorra": Look("pajaro", (
        _b("verde", "Argentina verde", body="#59b84a", belly="#b0dc8a", wing="#3f9a3a",
           tail="#3a86d0", beak="#e8a089", beakKind="ganchudo"),
        _b("azul", "Azul", 300, body="#4aa8e8", belly="#cdeaff", wing="#2f86c8",
           tail="#2a70b0", beak="#e8a089", beakKind="ganchudo"),
    )),
    "paloma": Look("pajaro", (
        _b("gris", "Gris de plaza", body="#9aa2ae", belly="#b9c0ca", wing="#838b98",
           bars="#3a3a46", neckPatch=("#5fae9a", "#b070c0"), beak="#d9b3a8", eye="#e8803a",
           tail="#6a7080"),
        _b("blanca", "Blanca", 120, body="#fbf8f6", belly="#ffffff", wing="#e6e0ea",
           beak="#f0b0a8", eye="#c8344f"),
    )),
    "gallo": Look("gallo", (
        _b("giro", "Giro", body="#c8742f", hackle="#e8a83a", wing="#8a4a22",
           tail=("#2f6b4a", "#1f4f3a", "#c8742f"), comb="#d92f3a"),
        _b("colorado", "Colorado", 3_500, body="#b5321f", hackle="#e06a2a", wing="#7a2014",
           tail=("#3a2418", "#7a2014", "#2f5a3a"), comb="#e03040"),
        _b("blanco", "Blanco", 3_500, body="#fbfaf5", hackle="#f1ecdf", wing="#e2dcd0",
           tail=("#e8e4dc", "#fbfaf5", "#d8d2c6"), comb="#e03040"),
        _b("negro", "Negro", 3_500, body="#34303c", hackle="#44506a", wing="#26222e",
           tail=("#2f6b5a", "#26222e", "#3a4a7a"), comb="#d92f3a"),
    )),
    "pavo_real": Look("pavo", (
        _b("azul", "Azul", body="#2a5fb0", tail="#2d8f86", rim="#d9b44a", ring="#2a6fb0",
           spot="#1a2a60"),
        _b("verde", "Verde", 60_000, body="#2f9a5a", tail="#4a9a3a", rim="#d4b44a",
           ring="#2a8a7a", spot="#1e4a3a"),
        _b("blanco", "Blanco", 80_000, body="#fbfaff", tail="#f3f2fb", rim="#e6e2f0",
           ring="#d8d4e8", spot="#b8b2d0", crest="#b8b2d0"),
    )),
    "pez": Look("pez", (
        _b("rojo", "Pez rojo", body="#ff6a3d", fin="#ff9a5a", belly="#ffb08a", scales=True,
           tailKind="largo"),
        _b("betta", "Betta azul", 300, body="#2c6fd8", fin="#6aa8f8", belly="#5a96ec",
           sheen="#8ac0ff", tailKind="largo"),
        _b("payaso", "Pez payaso", 600, body="#ff8a1f", fin="#ff9a3a", belly="#ffa850",
           stripes=(-50, 40), stripeColor="#ffffff"),
    )),
    "koi": Look("pez", (
        _b("kohaku", "Kohaku", body="#fbf5ec", fin="#fffaf0", belly="#ffffff", whiskers=True,
           patches=(("#e8452c", -34, -44, 34, 24, 0.3), ("#e8452c", 40, -10, 28, 32, 0.0),
                    ("#e8452c", -50, 30, 20, 22, 0.0))),
        _b("showa", "Showa", 20_000, body="#fbf5ec", fin="#fffaf0", belly="#ffffff",
           whiskers=True,
           patches=(("#2e2a34", -40, -40, 34, 26, 0.0), ("#e8452c", 30, -32, 32, 26, 0.0),
                    ("#2e2a34", 44, 24, 24, 26, 0.0), ("#e8452c", -48, 30, 22, 22, 0.0))),
        _b("ogon", "Ogon dorada", 30_000, body="#f0b92e", fin="#f6cd5a", belly="#fbe08a",
           sheen="#fff0b0", whiskers=True, scales=True),
    )),
    "tortuga": Look("tortuga", (
        _b("florida", "De Florida", shell="#5e9a4a", scute="#2e5a2e", skin="#7fb24a",
           ear="#e0443a", line="#f2e070", rim="#d9c98a"),
        _b("mora", "Mora", 3_000, shell="#c8a44a", scute="#4a3a22", skin="#a8986a",
           rim="#e8d8a0",
           blotches=(("#4a3a22", 0, -108, 26, 22, 0.0), ("#4a3a22", -72, -76, 18, 14, 0.0),
                     ("#4a3a22", 72, -76, 18, 14, 0.0))),
    )),
    "lagarto": Look("lagarto", (
        _b("hierro", "Gigante de El Hierro", skin="#7a8a4a", belly="#c8c88a", spots="#4a9ae0",
           blotches="#566236", spotAt="ambos"),
        _b("tizon", "Tizón", 20_000, skin="#2e2a36", belly="#4a4658", spots="#4aa8ff",
           spotAt="ambos", eye="#e8c040"),
    )),
    "coqui": Look("rana", (
        _b("pardo", "Pardo", skin="#8a6a44", belly="#d9c39a", stripe="#c9a86a",
           pads="#f0d27a", eye="#e8b840", spots="#5a4028"),
        _b("verde", "Verde", 2_500, skin="#6cb84a", belly="#d6efa8", stripe="#a8e07a",
           pads="#f6e47a", eye="#f0c040"),
    )),
    "pulpo": Look("pulpo", (
        _b("rojo", "Rojo", body="#e0523a", belly="#ffd0c0", spots="#c03a2a", face="listo"),
        _b("anillado", "De anillos azules", 6_666, body="#f2c23a", belly="#fde9a0",
           rings=True, ringColor="#2f7ae8", face="listo"),
    )),
    "pedrusco": Look("piedra", (
        _b("granito", "Granito", base="#9a9aa4", speckle=("#e8e0d8", "#3a3a46", "#c8a8a0"),
           moss="#6aa05a", face="saltones"),
        _b("basalto", "Basalto canario", 50, base="#4e4c54", pores=True, face="saltones"),
        _b("obsidiana", "Obsidiana", 300, base="#2c2636", facets=True, sheen="#7a6ac0",
           face="saltones"),
        _b("purpurina", "Con purpurina", 1_000, base="#8a86a8",
           glitter=("#ff7ac8", "#ffd23f", "#5ad8e8", "#ffffff"), face="saltones"),
    )),
    "cucaracha": Look("cucaracha", (
        _b("comun", "Común", shell="#6b3e26", edge="#b98a52", face="chulita"),
        _b("americana", "Americana", 66, shell="#8c3a22", edge="#e6c46a", ring=True,
           face="chulita"),
    )),
    "mosquito": Look("mosquito", (
        _b("tigre", "Tigre", body="#2a2832", belly="#f2f0f4", stripes="#f6f6f8",
           eye="#c9d4e8"),
        _b("comun", "Común", 10, body="#8a7a70", belly="#d9c8b0"),
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
