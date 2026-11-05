"""El armario de las mascotas: la ropa y los fondos de su retrato (sin Discord ni base de datos).

Una **prenda** (`Outfit`) cambia cómo se ve una mascota en su retrato (`mascota`) y nada
más: no da vínculo, XP ni dinero, como las skins de las mesas. Cada prenda va en un
**hueco** (`Slot`): cabeza, ojos, cuello, cuerpo o fondo. Cada mascota lleva como mucho
una por hueco y guarda su conjunto (`repositories/pets.py`, tabla `pet_outfits`); la
prenda es de su dueño, que se la puede poner a todas sus mascotas.

**Cómo se consiguen** (como las skins, `bot.services.skins`):

- *En la tienda* (`price` y `aisle`): un artículo `Kind.TROPHY` de clave `ropa_<clave>`,
  uno por persona, con su IGIC, en el pasillo de su tema. Salen también en la pestaña
  🐾 Mascotas del escaparate.
- *Por temporada* (`season`): solo se vende en esos meses.
- *Por logro* (`achievement`): artículo oculto que la tienda mete en la mochila al
  desbloquear el logro o al abrir la mochila (`rewards_for`). No se vende en el Rastro.
- *En las cajas* (`box`, la letra de su rareza): son las piezas de la colección 👗 Armario
  de `bot.services.lootboxes`, con clave `pieza_armario_<clave>`. Se ven y se venden como
  cualquier pieza, y además se ponen.

**El dibujo.** Cada prenda dice qué forma pinta la escena (`shape`, una función de
`assets/mascotas/escena.html`), con qué colores (`colors`) y, si acaso, con qué
estampado (`pattern`). La escena dibuja cada mascota y sabe dónde tiene la cabeza, los
ojos, el cuello y el lomo, así que la prenda se coloca sola. Una especie sin un hueco (un
pez no tiene cuello) no lo enseña en el armario (`bot.services.pets_looks.slots_of`).

**Añadir una prenda**: una fila en `OUTFITS` con una forma que ya exista o una forma
nueva en la escena (y en `SHAPES`), mirarla en la hoja de muestras
(`docs/muestras_mascotas.py`) sobre las 28 especies, y decidir su pasillo, precio e
IGIC como cualquier artículo (Biblia, «Tienda»). La clave no se cambia nunca.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from bot.services.skins import Season

#: Prefijo de la clave de artículo de una prenda que se vende o se gana.
CATALOG_PREFIX = "ropa_"
#: Colección de las cajas cuyas piezas se ponen (`bot.services.lootboxes`).
BOX_COLLECTION = "armario"
#: Prefijo de las piezas de esa colección.
BOX_PREFIX = f"pieza_{BOX_COLLECTION}_"


class Slot(StrEnum):
    """Dónde va una prenda. El valor es estable (se guarda en la base de datos)."""

    HEAD = "cabeza"
    EYES = "ojos"
    NECK = "cuello"
    BODY = "cuerpo"
    BACKDROP = "fondo"

    @property
    def label(self) -> str:
        """`🎩 Cabeza`."""
        return f"{SLOT_EMOJI[self]} {self.value.capitalize()}"


SLOT_EMOJI: dict[Slot, str] = {
    Slot.HEAD: "🎩",
    Slot.EYES: "🕶️",
    Slot.NECK: "🎀",
    Slot.BODY: "🦸",
    Slot.BACKDROP: "🖼️",
}
#: Huecos en el orden en que se enseñan.
SLOTS: tuple[Slot, ...] = tuple(Slot)

NAVIDAD = Season("Navidad", frozenset({12, 1}))
HALLOWEEN = Season("Halloween", frozenset({10, 11}))
CARNAVAL = Season("Carnaval", frozenset({2, 3}))


@dataclass(frozen=True, slots=True)
class Outfit:
    """Una prenda (o un fondo) del armario.

    Attributes:
        key: Clave estable; no se cambia nunca (va en la clave del artículo).
        slot: Hueco donde va.
        shape: Forma que pinta la escena (`SHAPES`).
        colors: Colores de la forma, en el orden que entiende la escena.
        pattern: Estampado, si la forma lo admite (`"rayas"`, `"cuadros"`…).
        price: Precio en la tienda; si no se vende, lo que vale para el patrimonio.
        aisle: Pasillo del colmado por su tema.
        igic: Tipo de IGIC (`taxes.IGIC_RATES`).
        min_level: Nivel mínimo para comprarla (las de 100.000 Y$ o más).
        season: Temporada en la que se vende.
        achievement: Id del logro que la regala; si lo tiene, no se vende.
        box: Letra de la rareza (C, R, E, L, M) si sale en las cajas; entonces no se vende.
    """

    key: str
    slot: Slot
    emoji: str
    name: str
    description: str
    shape: str
    colors: tuple[str, ...]
    price: int
    aisle: str
    pattern: str = ""
    igic: str = "general"
    min_level: int = 0
    season: Season | None = None
    achievement: str | None = None
    box: str | None = None

    @property
    def catalog_key(self) -> str:
        """Clave del artículo de la tienda (`ropa_chistera` o `pieza_armario_babero`)."""
        return f"{BOX_PREFIX}{self.key}" if self.box else f"{CATALOG_PREFIX}{self.key}"

    @property
    def for_sale(self) -> bool:
        """Si se vende en el escaparate (las de logro y las de caja, no)."""
        return self.achievement is None and self.box is None

    @property
    def label(self) -> str:
        """`🎩 Chistera`."""
        return f"{self.emoji} {self.name}"

    def drawing(self) -> dict[str, object]:
        """Lo que necesita la escena para pintarla."""
        return {"shape": self.shape, "colors": list(self.colors), "pattern": self.pattern}


#: Formas que sabe pintar la escena, por hueco. Una prenda nueva usa una de estas o
#: añade la suya a la escena y aquí (lo comprueba `tests/unit/test_pet_wardrobe.py`).
SHAPES: dict[Slot, frozenset[str]] = {
    Slot.HEAD: frozenset({
        "chistera", "corona", "cachorro", "sombrero_ala", "boina", "montera", "gorra", "cono",
        "casco", "gorro_chef", "papanoel", "bruja", "plumas", "tricornio", "laurel", "flores",
        "gorro_lana", "pinza",
    }),
    Slot.EYES: frozenset({
        "gafas_sol", "gafas_pasta", "monoculo", "gafas_corazon", "antifaz", "parche",
        "gafas_3d", "gafas_natacion", "gafas_estrella",
    }),
    Slot.NECK: frozenset({
        "pajarita", "collar", "bufanda", "corbata", "pinchos", "panuelo", "cono_verguenza",
        "lazo", "medalla", "babero",
    }),
    Slot.BODY: frozenset({
        "capa", "jersey", "chaleco", "alas", "manta", "chubasquero", "volantes", "camiseta",
    }),
    Slot.BACKDROP: frozenset({
        "casa", "canteras", "teide", "moncloa", "hongkong", "yate", "plaza_mayor", "patio",
        "espacio", "halloween", "navidad", "carnaval", "hacienda", "estudio", "alfombra",
    }),
}  # fmt: skip
#: Estampados que entienden las formas que los admiten (capa, manta, jersey, camiseta…).
PATTERNS: frozenset[str] = frozenset(
    {"", "rayas", "lentejuelas", "bandera_boricua", "renos", "lunares", "chapa"}
)

#: El fondo de quien no ha comprado ninguno: el salón de casa.
HOME = Outfit(
    "casa", Slot.BACKDROP, "🛋️", "Salón de casa", "El de siempre.", "casa",
    ("#f3e3c8", "#c98f5a", "#7a9e7e"), 0, "animales",
)  # fmt: skip


def _o(
    key: str,
    slot: Slot,
    emoji: str,
    name: str,
    price: int,
    aisle: str,
    description: str,
    shape: str,
    colors: tuple[str, ...],
    **extra: object,
) -> Outfit:
    return Outfit(
        key=key,
        slot=slot,
        emoji=emoji,
        name=name,
        description=description,
        shape=shape,
        colors=colors,
        price=price,
        aisle=aisle,
        **extra,  # type: ignore[arg-type]
    )


H, E, N, B, F = Slot.HEAD, Slot.EYES, Slot.NECK, Slot.BODY, Slot.BACKDROP

# fmt: off
OUTFITS: tuple[Outfit, ...] = (
    # 🎩 Cabeza ----------------------------------------------------------------------------
    _o("chistera", H, "🎩", "Chistera", 6_000, "lujo",
       "Negra, de copa alta y con cinta de raso. Para las mascotas que van a la ópera.",
       "chistera", ("#1d1d22", "#7b1e2b")),
    _o("corona", H, "👑", "Corona de oro", 150_000, "lujo",
       "Oro macizo con rubíes. La mascota no sabe lo que vale y se rasca con ella.",
       "corona", ("#f2c230", "#c0392b", "#9b6b0e"), min_level=10),
    _o("cachorro", H, "🎩", "Cachorro canario", 2_500, "canarias",
       "El sombrero de fieltro negro de los mayores de las islas, con su cinta.",
       "cachorro", ("#22201f", "#5a4632")),
    _o("sombrero_mago", H, "👒", "Sombrero de mago", 1_800, "canarias",
       "El de paja y ala ancha del campo canario, con cinta de colores. Para el sol de "
       "la cumbre.", "sombrero_ala", ("#e8c87a", "#c0392b", "#a8853c")),
    _o("pava", H, "👒", "Pava jíbara", 1_500, "boricua",
       "Sombrero de paja del campo de Puerto Rico. Jovani tiene una en el armario.",
       "sombrero_ala", ("#d9b45f", "#1b1b1b", "#9a7a35")),
    _o("boina", H, "🧢", "Boina de abuelo", 900, "espana",
       "Negra y ladeada, como la del abuelo cuando iba al casino a jugar al dominó.",
       "boina", ("#26262b",)),
    _o("montera", H, "🎩", "Montera", 4_000, "espana",
       "La del traje de luces, con sus dos borlas. Aquí solo se torean las caricias.",
       "montera", ("#1c1c1c", "#d6a630")),
    _o("tricornio", H, "🎩", "Tricornio", 3_000, "espana",
       "Charol negro, como el de la Guardia Civil. Quieto todo el mundo.",
       "tricornio", ("#141414", "#3d3d3d")),
    _o("gorra", H, "🧢", "Gorra de visera", 400, "bazar",
       "De las del chino de abajo, con la visera recta y la pegatina puesta.",
       "gorra", ("#2f6fd6", "#f5f5f5")),
    _o("casco_obra", H, "⛑️", "Casco de obra", 700, "bazar",
       "Amarillo y reglamentario. La mascota no sabe de obras, pero supervisa.",
       "casco", ("#f4c20d", "#c79a00")),
    _o("gorro_chef", H, "👨‍🍳", "Gorro de cocinero", 800, "bazar",
       "Alto y almidonado. No cocina nada, pero lo prueba todo.",
       "gorro_chef", ("#fbfbfb", "#d8d8d8")),
    _o("gorro_fiesta", H, "🥳", "Gorro de cumpleaños", 300, "cotillon",
       "Cono de cartón con lunares y goma que aprieta. Para cualquier fiesta.",
       "cono", ("#e84393", "#fdcb6e", "#00b894"), pattern="lunares"),
    _o("corona_flores", H, "💐", "Corona de flores", 700, "cotillon",
       "Margaritas y amapolas trenzadas. Le dura lo que tarda en comérsela.",
       "flores", ("#ffffff", "#e74c3c", "#f1c40f", "#27ae60")),
    _o("papa_noel", H, "🎅", "Gorro de Papá Noel", 600, "cotillon",
       "Rojo, con pompón blanco que se le cae en la cara.",
       "papanoel", ("#d63031", "#ffffff"), season=NAVIDAD),
    _o("bruja", H, "🧙", "Sombrero de bruja", 700, "cotillon",
       "Puntiagudo, con hebilla y una telaraña de regalo.",
       "bruja", ("#2d1b3d", "#8e44ad", "#f1c40f"), season=HALLOWEEN),
    _o("tocado_carnaval", H, "🪶", "Tocado de plumas", 2_500, "canarias",
       "Plumas de colores y lentejuelas, como en la cabalgata del Carnaval de Santa Cruz.",
       "plumas", ("#e84393", "#0984e3", "#fdcb6e", "#00cec9"), season=CARNAVAL),
    _o("laurel", H, "🌿", "Corona de laurel", 20_000, "lujo",
       "Laurel de oro. Se gana con el vínculo más alto, no se compra.",
       "laurel", ("#d4a017", "#8a6a07"), achievement="pet_bond_10"),
    _o("gorro_aniversario", H, "🎂", "Gorro de aniversario", 5_000, "cotillon",
       "Dorado y con una vela encima. Para la mascota que lleva un año contigo.",
       "cono", ("#f1c40f", "#ffffff", "#e67e22"), pattern="rayas",
       achievement="pet_anniversary_1"),
    # 🕶️ Ojos -------------------------------------------------------------------------------
    _o("gafas_sol", E, "🕶️", "Gafas de sol de gasolinera", 350, "bazar",
       "Negras, de plástico y con la etiqueta del precio todavía puesta.",
       "gafas_sol", ("#111111", "#2c2c2c")),
    _o("gafas_pasta", E, "👓", "Gafas de pasta", 900, "ventanilla",
       "Gruesas y negras, de opositor con el temario subrayado.",
       "gafas_pasta", ("#1b1b1b", "#d6eaf8")),
    _o("monoculo", E, "🧐", "Monóculo", 8_000, "lujo",
       "Con cadenita de oro. Para mirar por encima del hombro a las otras mascotas.",
       "monoculo", ("#d4a017", "#e8f4fa")),
    _o("gafas_corazon", E, "😍", "Gafas de corazones", 500, "cotillon",
       "Rosas, con forma de corazón, de las que se compran en la feria.",
       "gafas_corazon", ("#ff6b9d", "#c2185b")),
    _o("gafas_3d", E, "🎬", "Gafas 3D del cine", 250, "bazar",
       "Cartón, un cristal rojo y otro azul. Se las llevó del cine sin pagar.",
       "gafas_3d", ("#f5f5f5", "#e74c3c", "#3498db")),
    _o("parche", E, "🏴‍☠️", "Parche de pirata", 300, "shitpost",
       "Negro, con una calavera bordada. La mascota ve con el otro ojo igual de bien.",
       "parche", ("#111111", "#f5f5f5")),
    _o("antifaz", E, "🎭", "Antifaz de carnaval", 800, "canarias",
       "Con purpurina y plumas en las puntas. Nadie la va a reconocer, seguro.",
       "antifaz", ("#8e44ad", "#f1c40f"), season=CARNAVAL),
    _o("gafas_estrella", E, "🌟", "Gafas de estrella", 10_000, "lujo",
       "Con forma de estrella y cristales dorados. Se ganan saliendo mil veces en los "
       "mensajes del bot.", "gafas_estrella", ("#f1c40f", "#e67e22"),
       achievement="pet_cameo_1k"),
    # 🎀 Cuello -----------------------------------------------------------------------------
    _o("pajarita", N, "🎀", "Pajarita", 1_500, "lujo",
       "De terciopelo negro. Va de boda aunque no la hayan invitado.",
       "pajarita", ("#1b1b1b", "#3a3a3a")),
    _o("perlas", N, "📿", "Collar de perlas", 25_000, "lujo",
       "Dos vueltas de perlas cultivadas. Las de la abuela, pero de verdad.",
       "collar", ("#f8f4ec", "#d8cfc0")),
    _o("cadena_oro", N, "⛓️", "Cadena de oro", 9_000, "boricua",
       "Gruesa y brillante, de las de los videoclips de reguetón.",
       "collar", ("#e1b12c", "#a37b0d")),
    _o("bufanda", N, "🧣", "Bufanda de la grada", 1_200, "espana",
       "A rayas, de las que se agitan cuando marca tu equipo.",
       "bufanda", ("#f4c20d", "#1e5aa8"), pattern="rayas"),
    _o("corbata", N, "👔", "Corbata de funcionario", 1_000, "ventanilla",
       "Granate, con el nudo un poco flojo a partir de las tres.",
       "corbata", ("#7b1e2b", "#a8323e")),
    _o("panuelo", N, "🧣", "Pañuelo de San Fermín", 500, "espana",
       "Rojo, atado al cuello. Para correr delante de la aspiradora.",
       "panuelo", ("#d63031", "#ffffff")),
    _o("lazo", N, "🎀", "Lazo de regalo", 300, "cotillon",
       "Rojo y enorme. La mascota es el regalo.",
       "lazo", ("#e74c3c", "#c0392b")),
    _o("collar_chapa", N, "🏷️", "Collar con chapa", 250, "animales",
       "De cuero, con una chapa con su nombre y tu teléfono por detrás.",
       "collar", ("#8b4513", "#d4a017"), pattern="chapa"),
    _o("pinchos", N, "⛓️", "Collar de pinchos", 600, "animales",
       "Cuero negro con tachuelas. Da miedo hasta que la ves dormir.",
       "pinchos", ("#1b1b1b", "#c0c0c0")),
    _o("cono", N, "🔻", "Cono de la vergüenza", 200, "animales",
       "El del veterinario. No le pasa nada: es que le queda bien.",
       "cono_verguenza", ("#f5f5f5", "#cfd8dc")),
    _o("medalla", N, "🏅", "Medalla al cuidador", 15_000, "animales",
       "De oro, con su cinta. Se gana cuidando a tus mascotas 100 días seguidos.",
       "medalla", ("#f1c40f", "#c0392b"), achievement="pet_streak_100"),
    # 🦸 Cuerpo -----------------------------------------------------------------------------
    _o("capa", B, "🦸", "Capa de superhéroe", 1_500, "bazar",
       "Roja, de raso, con el cuello de velcro. Vuela igual que antes, o sea, nada.",
       "capa", ("#d63031", "#a71d1d")),
    _o("capa_vampiro", B, "🧛", "Capa de vampiro", 1_200, "cotillon",
       "Negra por fuera y roja por dentro, con el cuello alto.",
       "capa", ("#1b1b1b", "#b71c1c"), season=HALLOWEEN),
    _o("capa_boricua", B, "🇵🇷", "Capa con la bandera boricua", 1_800, "boricua",
       "La bandera de Puerto Rico a modo de capa, para las fiestas de la calle San Sebastián.",
       "capa", ("#ed0a3f", "#ffffff", "#0050f0"), pattern="bandera_boricua"),
    _o("jersey_renos", B, "🦌", "Jersey de renos", 1_500, "cotillon",
       "De lana que pica, con renos y copos. Lo tejió una tía.",
       "jersey", ("#c0392b", "#ffffff", "#27ae60"), pattern="renos", season=NAVIDAD),
    _o("chaleco", B, "🦺", "Chaleco salvavidas", 900, "canarias",
       "Naranja, para el barco a La Graciosa. Por si acaso.",
       "chaleco", ("#ff7f0e", "#f5f5f5")),
    _o("manta", B, "🧶", "Manta esperancera", 2_200, "canarias",
       "La manta de lana a rayas de La Esperanza, en Tenerife. Abriga hasta en agosto.",
       "manta", ("#f5f0e1", "#2c2c2c", "#b22222"), pattern="rayas"),
    _o("camiseta", B, "👕", "Camiseta amarilla de la grada", 1_500, "canarias",
       "Amarilla con el cuello azul, la de ir al estadio con el bocadillo.",
       "camiseta", ("#f9d71c", "#1e5aa8")),
    _o("chubasquero", B, "🧥", "Chubasquero amarillo", 700, "bazar",
       "Para los dos días de lluvia del año en el sur. Cruje al andar.",
       "chubasquero", ("#f4d03f", "#c9a400")),
    _o("alas", B, "😇", "Alas de ángel", 2_000, "cotillon",
       "Plumas blancas con alambre. No engañan a nadie.",
       "alas", ("#ffffff", "#dfe6e9")),
    _o("volantes", B, "💃", "Volantes de flamenca", 3_000, "espana",
       "Rojos con lunares blancos, para la feria. Se mueven solos.",
       "volantes", ("#d63031", "#ffffff"), pattern="lunares"),
    # 🖼️ Fondo ------------------------------------------------------------------------------
    _o("fondo_canteras", F, "🏖️", "Fondo: playa de Las Canteras", 3_000, "canarias",
       "La arena dorada, la barra y el mar en calma de Las Palmas de Gran Canaria.",
       "canteras", ("#7ec8e3", "#f2d49b", "#2e86c1")),
    _o("fondo_teide", F, "🌋", "Fondo: el Teide nevado", 3_000, "canarias",
       "El Teide con nieve en la punta y el mar de nubes debajo.",
       "teide", ("#9ad0f5", "#8d6e63", "#ffffff")),
    _o("fondo_carnaval", F, "🎉", "Fondo: Carnaval de Santa Cruz", 2_500, "canarias",
       "Confeti, serpentinas y luces de colores en la noche de Santa Cruz.",
       "carnaval", ("#2d1b4e", "#e84393", "#fdcb6e"), season=CARNAVAL),
    _o("fondo_moncloa", F, "🏛️", "Fondo: escalinata de la Moncloa", 5_000, "moncloa",
       "La entrada del Palacio, con la alfombra y las banderas. Ni una foto sin posado.",
       "moncloa", ("#efe6d5", "#b03a2e", "#f1c40f")),
    _o("fondo_hongkong", F, "🌃", "Fondo: neones de Mong Kok", 4_000, "hongkong",
       "Rótulos de neón, humedad y el tráfico de Nathan Road de fondo.",
       "hongkong", ("#120a2a", "#ff3fa4", "#2de2e6")),
    _o("fondo_yate", F, "🛥️", "Fondo: cubierta de yate", 50_000, "lujo",
       "Teca, mar abierto y una copa en la mesa que no es tuya.",
       "yate", ("#5dade2", "#c8a165", "#ffffff")),
    _o("fondo_plaza", F, "🏛️", "Fondo: Plaza Mayor", 2_500, "espana",
       "Soportales, el caballo de Felipe III y una paloma que mira mal.",
       "plaza_mayor", ("#c0763e", "#a0522d", "#87ceeb")),
    _o("fondo_patio", F, "🌺", "Fondo: patio boricua", 2_500, "boricua",
       "Un patio con flamboyán, una hamaca y un coquí que canta de noche.",
       "patio", ("#f6c177", "#e74c3c", "#27ae60")),
    _o("fondo_hacienda", F, "🗂️", "Fondo: oficina de Hacienda", 1_500, "ventanilla",
       "Fluorescentes, archivadores grises y el dispensador de números parado en el 47.",
       "hacienda", ("#dfe4ea", "#7f8c8d", "#2c3e50")),
    _o("fondo_espacio", F, "🚀", "Fondo: el espacio", 6_666, "shitpost",
       "Estrellas, un planeta con anillos y ningún sitio donde hacer pis.",
       "espacio", ("#0b0d2b", "#6c5ce7", "#fdcb6e")),
    _o("fondo_halloween", F, "🎃", "Fondo: noche de Halloween", 2_000, "cotillon",
       "Luna llena, calabazas y un murciélago que pasa sin saludar.",
       "halloween", ("#1e1336", "#e67e22", "#f5f6fa"), season=HALLOWEEN),
    _o("fondo_navidad", F, "🎄", "Fondo: salón navideño", 2_000, "cotillon",
       "El árbol, las luces y la chimenea con los calcetines colgados.",
       "navidad", ("#7a1f1f", "#1e8449", "#f1c40f"), season=NAVIDAD),
    _o("fondo_estudio", F, "📸", "Fondo: estudio de fotos", 8_000, "animales",
       "Fondo gris de estudio y dos focos. Se gana enseñando 25 fotos de tus mascotas.",
       "estudio", ("#9aa5b1", "#f5f6fa"), achievement="pet_shows_25"),
    # 👗 Colección Armario de las cajas (`lootboxes`) ---------------------------------------
    _o("pinza", H, "🌼", "Pinza con flor", 300, "animales",
       "Una margarita de plástico que se engancha al pelo. O a lo que haya.",
       "pinza", ("#ffffff", "#f1c40f"), box="C"),
    _o("gafas_natacion", E, "🥽", "Gafas de natación", 330, "animales",
       "Con la goma dada de sí. Preparada para la piscina municipal.",
       "gafas_natacion", ("#0fb9b1", "#1b1b1b"), box="C"),
    _o("babero", N, "🍼", "Babero", 350, "animales",
       "Con un dibujo de un hueso. Por si come con ansia, que come.",
       "babero", ("#dff9fb", "#6c5ce7"), box="C"),
    _o("gorro_lana", H, "🧶", "Gorro de lana con pompón", 1_700, "animales",
       "Calentito, de rayas y con un pompón que se mueve al andar.",
       "gorro_lana", ("#e17055", "#ffeaa7"), pattern="rayas", box="R"),
    _o("capa_lentejuelas", B, "✨", "Capa de lentejuelas", 1_900, "animales",
       "Brilla con cualquier luz. Para su gira de despedida.",
       "capa", ("#a29bfe", "#6c5ce7"), pattern="lentejuelas", box="R"),
    _o("gafas_diamante", E, "💎", "Gafas de diamantes", 8_000, "animales",
       "Montura de oro blanco con brillantes. Ve el mundo igual, pero más caro.",
       "gafas_sol", ("#dfe6e9", "#74b9ff"), pattern="lentejuelas", box="E"),
    _o("fondo_alfombra", F, "🎞️", "Fondo: alfombra roja", 8_000, "animales",
       "Photocall, flashes y una alfombra roja que no se acaba.",
       "alfombra", ("#b71c1c", "#1b1b1b", "#f1c40f"), box="E"),
    _o("corona_diamantes", H, "💠", "Diadema de diamantes", 40_000, "animales",
       "La que solo sale en las cajas. Para una mascota de la realeza.",
       "corona", ("#dfe6e9", "#74b9ff", "#b2bec3"), pattern="lentejuelas", box="L"),
)
# fmt: on

OUTFIT_BY_KEY: dict[str, Outfit] = {o.key: o for o in OUTFITS}
OUTFIT_BY_CATALOG_KEY: dict[str, Outfit] = {o.catalog_key: o for o in OUTFITS}
#: Claves de artículo de todas las prendas (las de caja incluidas).
OUTFIT_KEYS: frozenset[str] = frozenset(OUTFIT_BY_CATALOG_KEY)
#: Las prendas que salen en las cajas, de la más común a la más rara.
BOX_OUTFITS: tuple[Outfit, ...] = tuple(o for o in OUTFITS if o.box)


def outfit_of(catalog_key: str | None) -> Outfit | None:
    """La prenda de un artículo, o `None` si no es una prenda."""
    return OUTFIT_BY_CATALOG_KEY.get(catalog_key or "")


def is_outfit(catalog_key: str | None) -> bool:
    """Si un artículo es una prenda del armario (comprada, ganada o de caja)."""
    return (catalog_key or "") in OUTFIT_KEYS


def in_season(outfit: Outfit, when: datetime) -> bool:
    """Si una prenda se vende en `when` (las que no son de temporada, siempre)."""
    return outfit.season is None or outfit.season.open(when)


def rewards_for(achievement_ids: Iterable[str]) -> tuple[Outfit, ...]:
    """Las prendas que regalan estos logros."""
    unlocked = set(achievement_ids)
    return tuple(o for o in OUTFITS if o.achievement in unlocked)


def in_slot(slot: Slot) -> tuple[Outfit, ...]:
    """Las prendas de un hueco, en el orden del registro."""
    return tuple(o for o in OUTFITS if o.slot is slot)


def wearing(worn: Mapping[str, str], owned: Iterable[str]) -> dict[Slot, Outfit]:
    """Lo que se ve de un conjunto: solo las prendas que su dueño aún tiene.

    Lo puesto se guarda por mascota (`hueco → clave de artículo`). Si la prenda ya no
    está en la mochila (se vendió en el Rastro, se empeñó en el Monte), no se ve, sin
    tener que borrar nada al venderla.

    Args:
        worn: Hueco → clave de artículo, como lo guarda la base de datos.
        owned: Claves de artículo de lo que tiene el dueño.
    """
    have = set(owned)
    out: dict[Slot, Outfit] = {}
    for slot_value, key in worn.items():
        outfit = outfit_of(key)
        if outfit is not None and key in have and outfit.slot.value == slot_value:
            out[outfit.slot] = outfit
    return out


def complete(look: Mapping[Slot, Outfit]) -> bool:
    """Si el conjunto lleva algo en los cuatro huecos de ropa (el fondo no cuenta)."""
    return all(slot in look for slot in SLOTS if slot is not Slot.BACKDROP)
