"""Catálogo de los negocios de `pala`: sectores, productos, paletas, emblemas y habituales.

Desde la fase 2 (9.5.0), también los tamaños del local (`SIZES`), las mejoras que se
amortizan (`EQUIPMENT`, `AMORTIZATION`), las campañas de publicidad (`CAMPAIGNS`) y las
reseñas de los clientes (`REVIEW_LINES`, `REVIEWERS`).

Solo datos. Las reglas que los usan están en `bot.services.business` y los minijuegos, en
`bot.services.business_games`. Las claves (`Sector.key`, `Product.key`, los índices de
`PALETTES` y `EMBLEMS`, `REGULAR_NAMES`) se guardan en la base de datos y las lee la escena
(`assets/negocio/escena.html`): no se cambian nunca. Lo nuevo se añade al final.

Las cifras van a la escala de las nóminas (`WAGE_YAPDOLLARS_PER_EURO`, 100 Y$ = 1 €): un
café solo de 1,50 € son 150 Y$. Son cifras de juego, calibradas con `docs/economia_sim.py`
(ver `docs/negocios.md`, «Equilibrio»): un turno a la par con un P3 de la `pala`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Product:
    """Un producto que se compra al mayorista y se vende (o un recambio del taller).

    Attributes:
        key: Clave estable (la dibuja la escena).
        name: Cómo se enseña.
        cost: Base del mayorista por unidad, sin IGIC.
        price: Precio de venta con el nivel de precios normal (en el taller, el
            presupuesto justo: recambio y mano de obra).
        igic: Clave de `taxes.IGIC_BY_KEY` de la compra al mayorista, por el Decreto
            Legislativo 1/2025 de Canarias: tipo cero el pan, la leche, la fruta fresca y el
            café tostado (art. 33.Uno); el 3 % (`reducido`) los demás alimentos, los helados,
            los detergentes y la pintura (art. 34.Uno.a y e); el general del 7 % (art. 32.1)
            la comida preparada, los refrescos con azúcar y el resto. Interpretaciones del
            proyecto: el barraquito (café, leche condensada y licor) va al general, el zumo y
            el batido al 3 % y el granizado, como un refresco, al general.
        pack: Unidades de un lote del mayorista.
        spoil: Parte del stock que se pierde cada noche (0 = no caduca; 1 = se tira entero).
        weight: Lo que se pide frente a los demás productos del sector.
    """

    key: str
    name: str
    cost: int
    price: int
    igic: str = "general"
    pack: int = 10
    spoil: float = 0.0
    weight: float = 1.0


@dataclass(frozen=True, slots=True)
class Sector:
    """Un sector: su local, sus productos, su minijuego y su clientela.

    Attributes:
        key: Clave estable.
        name: Nombre corto.
        emoji: Emoji del menú.
        game: Minijuego (`comandas`, `caja` o `diagnostico`).
        blurb: Una línea para elegirlo.
        m2: Metros del local (el alquiler sale de ahí).
        kind: Tipo de inmueble de `pisos` cuyo `rent_factor` se usa (`local` o `nave`).
        products: Lo que se vende.
        clients: Clientes de un turno con todo a la media (barrio neutro, 3 estrellas,
            precios normales, hora de media).
        patience: Segundos que espera cada cliente.
        setup: Acondicionamiento del local (base, sin IGIC), al montarlo.
        license: Tasa municipal de la apertura.
        utilities: Suministros de cada noche (base, sin IGIC).
        order: `(mínimo, máximo)` de cosas que pide un cliente (en el taller, 1).
        curve: Cuánta gente viene a cada hora canaria (24 valores; 1 = la media).
        fit: Lo que multiplica la demanda el tema del barrio (`THEMES` de `pisos`).
    """

    key: str
    name: str
    emoji: str
    game: str
    blurb: str
    m2: int
    kind: str
    products: tuple[Product, ...]
    clients: int
    patience: int
    setup: int
    license: int
    utilities: int
    order: tuple[int, int]
    curve: tuple[float, ...]
    fit: dict[str, float]

    def product(self, key: str) -> Product | None:
        """El producto con esa clave, o `None`."""
        return next((p for p in self.products if p.key == key), None)


CAFETERIA = Sector(
    key="cafeteria",
    name="Cafetería",
    emoji="☕",
    game="comandas",
    blurb="Barraquitos, bocadillos y la tertulia de siempre. Cada cliente es una mesa.",
    m2=50,
    kind="local",
    products=(
        Product("cafe", "Café solo", 40, 150, igic="cero", pack=40, weight=1.4),
        Product("cortado", "Cortado", 50, 180, igic="cero", pack=40, weight=1.6),
        Product("barraquito", "Barraquito", 85, 280, pack=30, weight=1.0),
        Product("zumo", "Zumo natural", 110, 350, igic="reducido", pack=20, spoil=0.4,
                weight=0.7),
        Product("bocadillo", "Bocadillo", 160, 550, pack=20, spoil=0.5, weight=1.0),
        Product("dulce", "Dulce", 70, 250, igic="reducido", pack=20, spoil=0.5, weight=0.8),
    ),
    clients=8,
    patience=25,
    setup=120_000,
    license=15_000,
    utilities=1_200,
    order=(2, 5),
    # Desayunos y meriendas; de madrugada, los que salen de fiesta.
    curve=(
        0.25, 0.15, 0.15, 0.15, 0.15, 0.3, 0.6, 1.2, 1.4, 1.3, 1.2, 1.0,
        0.9, 1.0, 0.9, 0.8, 1.1, 1.3, 1.2, 1.0, 0.8, 0.6, 0.4, 0.3,
    ),
    fit={
        "marinero": 1.05, "historico": 1.15, "centro": 1.2, "obrero": 0.95,
        "playa": 1.15, "lujo": 1.0, "rural": 0.8,
    },
)  # fmt: skip

TIENDA = Sector(
    key="tienda",
    name="Tienda de barrio",
    emoji="🛒",
    game="caja",
    blurb="El ultramarinos de la esquina: se cobra, se da el cambio y se vigila el bolsillo.",
    m2=60,
    kind="local",
    products=(
        Product("pan", "Pan", 90, 150, igic="cero", pack=30, spoil=1.0, weight=1.5),
        Product("leche", "Leche", 120, 190, igic="cero", pack=24, spoil=0.1, weight=1.3),
        Product("fruta", "Fruta", 260, 420, igic="cero", pack=20, spoil=0.3, weight=1.0),
        Product("conservas", "Conservas", 320, 520, igic="reducido", pack=24, weight=0.9),
        Product("limpieza", "Limpieza", 500, 800, igic="reducido", pack=12, weight=0.6),
        Product("bebidas", "Bebidas", 430, 700, pack=24, weight=1.0),
    ),
    clients=7,
    patience=30,
    setup=100_000,
    license=10_000,
    utilities=1_000,
    order=(3, 6),
    # Media mañana y la vuelta del trabajo.
    curve=(
        0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.2, 0.4, 0.7, 0.9, 1.2, 1.3,
        1.2, 1.0, 0.7, 0.7, 0.9, 1.1, 1.3, 1.4, 1.2, 0.8, 0.4, 0.2,
    ),
    fit={
        "marinero": 1.05, "historico": 0.9, "centro": 0.95, "obrero": 1.2,
        "playa": 1.0, "lujo": 0.85, "rural": 1.15,
    },
)  # fmt: skip

TALLER = Sector(
    key="taller",
    name="Taller",
    emoji="🔧",
    game="diagnostico",
    blurb="Coches que hacen ruidos raros. Se diagnostica, se presupuesta y que no vuelvan.",
    m2=90,
    kind="nave",
    products=(
        Product("pastillas", "Pastillas de freno", 900, 2_400, pack=4, weight=1.3),
        Product("bateria", "Batería", 1_400, 3_200, pack=2, weight=1.0),
        Product("aceite", "Aceite y filtro", 500, 1_600, pack=6, weight=1.5),
        Product("correa", "Correa", 1_800, 4_600, pack=2, weight=0.6),
        Product("bujias", "Bujías", 400, 1_400, pack=6, weight=0.9),
        Product("neumatico", "Neumático", 1_100, 2_600, pack=4, weight=1.1),
    ),
    clients=4,
    patience=45,
    setup=150_000,
    license=12_000,
    utilities=900,
    order=(1, 1),
    # Horario de taller: mañana fuerte, tarde floja.
    curve=(
        0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.5, 1.1, 1.3, 1.3, 1.2,
        1.1, 0.9, 0.6, 0.9, 1.1, 1.0, 0.8, 0.5, 0.3, 0.15, 0.15, 0.1,
    ),
    fit={
        "marinero": 0.95, "historico": 0.8, "centro": 0.9, "obrero": 1.25,
        "playa": 0.85, "lujo": 0.95, "rural": 1.15,
    },
)  # fmt: skip

HELADERIA = Sector(
    key="heladeria",
    name="Heladería",
    emoji="🍦",
    game="comandas",
    blurb="Helado de gofio, granizados y colas de guiris al sol. Cada cliente es un pedido.",
    m2=40,
    kind="local",
    products=(
        Product("vainilla", "Helado de vainilla", 60, 280, igic="reducido", pack=30, spoil=0.1,
                weight=1.2),
        Product("chocolate", "Helado de chocolate", 60, 280, igic="reducido", pack=30,
                spoil=0.1, weight=1.3),
        Product("gofio", "Helado de gofio", 70, 320, igic="reducido", pack=20, spoil=0.1,
                weight=1.0),
        Product("mango", "Sorbete de mango", 80, 330, igic="reducido", pack=20, spoil=0.15,
                weight=0.9),
        Product("granizado", "Granizado de limón", 30, 200, pack=40, weight=1.0),
        Product("batido", "Batido", 70, 350, igic="reducido", pack=20, spoil=0.3, weight=0.7),
    ),
    clients=7,
    patience=22,
    setup=90_000,
    license=12_000,
    utilities=1_100,
    order=(1, 4),
    # Las tardes, el paseo y la playa; por la mañana, casi nadie.
    curve=(
        0.3, 0.15, 0.1, 0.1, 0.1, 0.1, 0.1, 0.2, 0.4, 0.6, 0.8, 1.0,
        1.2, 1.4, 1.4, 1.5, 1.6, 1.6, 1.5, 1.4, 1.2, 0.9, 0.6, 0.4,
    ),
    fit={
        "marinero": 1.05, "historico": 1.05, "centro": 1.1, "obrero": 0.85,
        "playa": 1.3, "lujo": 1.1, "rural": 0.75,
    },
)  # fmt: skip

FERRETERIA = Sector(
    key="ferreteria",
    name="Ferretería",
    emoji="🔩",
    game="caja",
    blurb="Tornillos sueltos, la llave de paso y el consejo gratis. Tiques caros y pocos.",
    m2=70,
    kind="local",
    products=(
        Product("tornillos", "Tornillería", 120, 270, pack=50, weight=1.4),
        Product("bombillas", "Bombillas", 250, 540, pack=20, weight=1.1),
        Product("pintura", "Pintura", 900, 1_700, igic="reducido", pack=10, weight=0.8),
        Product("herramientas", "Herramientas", 1_100, 2_150, pack=6, weight=0.6),
        Product("fontaneria", "Fontanería", 450, 900, pack=12, weight=0.9),
        Product("cerraduras", "Cerraduras", 800, 1_550, pack=8, weight=0.5),
    ),
    clients=5,
    patience=35,
    setup=110_000,
    license=10_000,
    utilities=800,
    order=(1, 4),
    # Primera hora de los profesionales y la tarde de los manitas.
    curve=(
        0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.2, 0.9, 1.4, 1.4, 1.3, 1.2,
        1.0, 0.6, 0.5, 0.8, 1.1, 1.2, 1.2, 1.0, 0.6, 0.3, 0.15, 0.1,
    ),
    fit={
        "marinero": 1.05, "historico": 0.9, "centro": 0.95, "obrero": 1.25,
        "playa": 0.8, "lujo": 0.85, "rural": 1.2,
    },
)  # fmt: skip

# -- Cinco sectores más (9.48.0), cada uno con su minijuego propio -----------------------

GUACHINCHE = Sector(
    key="guachinche",
    name="Guachinche",
    emoji="🍷",
    game="brasa",
    blurb="Vino del país, carne de cabra y queso asado. La brasa, en su punto o se quema.",
    m2=80,
    kind="local",
    products=(
        Product("vino", "Vino del país", 120, 450, pack=20, weight=1.5),
        Product("papas", "Papas con mojo", 60, 400, igic="cero", pack=30, spoil=0.3,
                weight=1.3),
        Product("carne", "Carne de cabra", 350, 1_100, igic="reducido", pack=10, spoil=0.3,
                weight=1.0),
        Product("costillas", "Costillas con piña", 300, 950, igic="reducido", pack=10,
                spoil=0.3, weight=1.0),
        Product("queso", "Queso asado", 180, 650, igic="reducido", pack=12, spoil=0.1,
                weight=0.9),
        Product("escaldon", "Escaldón de gofio", 50, 350, igic="reducido", pack=20, spoil=0.4,
                weight=0.7),
    ),
    clients=6,
    patience=32,
    setup=110_000,
    license=12_000,
    utilities=1_100,
    order=(2, 4),
    # Almuerzos largos y cenas; por la mañana, casi nadie.
    curve=(
        0.3, 0.15, 0.1, 0.1, 0.1, 0.1, 0.1, 0.15, 0.2, 0.35, 0.6, 1.0,
        1.6, 1.8, 1.7, 1.3, 0.9, 0.8, 1.0, 1.4, 1.7, 1.5, 1.0, 0.6,
    ),
    fit={
        "marinero": 1.0, "historico": 1.0, "centro": 0.8, "obrero": 1.05,
        "playa": 0.85, "lujo": 0.8, "rural": 1.35,
    },
)  # fmt: skip

PANADERIA = Sector(
    key="panaderia",
    name="Panadería",
    emoji="🥖",
    game="encargos",
    blurb="Pan recién hecho y dulces de encargo. Cada bolsa tiene dueño: no te equivoques.",
    m2=50,
    kind="local",
    products=(
        Product("barra", "Barra de pan", 40, 150, igic="cero", pack=40, spoil=1.0, weight=1.6),
        Product("millo", "Pan de millo", 70, 260, igic="cero", pack=20, spoil=0.8, weight=0.9),
        Product("quesadilla", "Quesadilla herreña", 90, 280, igic="reducido", pack=20,
                spoil=0.5, weight=1.0),
        Product("truchas", "Truchas de batata", 60, 200, igic="reducido", pack=24, spoil=0.5,
                weight=0.9),
        Product("bizcochon", "Bizcochón", 250, 850, igic="reducido", pack=8, spoil=0.3,
                weight=0.5),
        Product("tarta", "Tarta de encargo", 900, 2_600, igic="reducido", pack=4, spoil=0.3,
                weight=0.4),
    ),
    clients=8,
    patience=28,
    setup=130_000,
    license=12_000,
    utilities=1_400,
    order=(2, 5),
    # De madrugada sale el pan; la gente pasa antes del trabajo y a media tarde.
    curve=(
        0.1, 0.1, 0.1, 0.1, 0.3, 0.9, 1.6, 1.8, 1.7, 1.4, 1.1, 1.0,
        0.9, 0.7, 0.4, 0.4, 0.6, 0.9, 1.0, 0.8, 0.5, 0.3, 0.15, 0.1,
    ),
    fit={
        "marinero": 1.0, "historico": 1.15, "centro": 1.0, "obrero": 1.2,
        "playa": 0.9, "lujo": 0.9, "rural": 1.1,
    },
)  # fmt: skip

PESCADERIA = Sector(
    key="pescaderia",
    name="Pescadería",
    emoji="🐟",
    game="balanza",
    blurb="Vieja, cherne y chicharros recién llegados. Se pesa a ojo y se acierta en la balanza.",
    m2=45,
    kind="local",
    products=(
        Product("chicharros", "Chicharros", 40, 100, igic="reducido", pack=40, spoil=0.3,
                weight=1.3),
        Product("vieja", "Vieja", 380, 850, igic="reducido", pack=12, spoil=0.3, weight=1.2),
        Product("cherne", "Cherne", 500, 1_150, igic="reducido", pack=10, spoil=0.25,
                weight=0.9),
        Product("sama", "Sama", 450, 1_000, igic="reducido", pack=10, spoil=0.3, weight=0.8),
        Product("pulpo", "Pulpo", 600, 1_400, igic="reducido", pack=8, spoil=0.2, weight=0.7),
        Product("lapas", "Lapas", 220, 550, igic="reducido", pack=16, spoil=0.35, weight=0.8),
    ),
    clients=6,
    patience=35,
    setup=100_000,
    license=12_000,
    utilities=1_200,
    order=(1, 1),
    # El pescado llega de madrugada y se vende por la mañana; por la tarde, lo que quede.
    curve=(
        0.05, 0.05, 0.05, 0.05, 0.05, 0.1, 0.4, 1.0, 1.6, 1.8, 1.8, 1.6,
        1.3, 0.9, 0.5, 0.4, 0.6, 0.8, 0.8, 0.6, 0.3, 0.1, 0.05, 0.05,
    ),
    fit={
        "marinero": 1.35, "historico": 1.0, "centro": 0.9, "obrero": 1.0,
        "playa": 1.05, "lujo": 1.05, "rural": 0.9,
    },
)  # fmt: skip

MOVILES = Sector(
    key="moviles",
    name="Reparación de móviles",
    emoji="📱",
    game="revision",
    blurb="Pantallas rotas y baterías hinchadas. Se mira el móvil entero: puede tener más de una.",
    m2=25,
    kind="local",
    products=(
        Product("pantalla", "Pantalla", 1_800, 2_900, pack=4, weight=1.5),
        Product("bateria_movil", "Batería de móvil", 600, 1_150, pack=6, weight=1.3),
        Product("conector", "Conector de carga", 250, 600, pack=6, weight=1.1),
        Product("camara_movil", "Cámara", 900, 1_500, pack=4, weight=0.7),
        Product("altavoz", "Altavoz", 300, 650, pack=6, weight=0.8),
        Product("tapa", "Tapa trasera", 400, 850, pack=6, weight=0.9),
    ),
    clients=4,
    patience=50,
    setup=70_000,
    license=8_000,
    utilities=500,
    order=(1, 1),
    # Por la tarde, al salir del trabajo y del instituto.
    curve=(
        0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.2, 0.4, 0.6, 0.9, 1.0,
        1.1, 0.9, 0.7, 0.9, 1.3, 1.5, 1.6, 1.5, 1.1, 0.6, 0.3, 0.15,
    ),
    fit={
        "marinero": 0.85, "historico": 0.9, "centro": 1.3, "obrero": 1.1,
        "playa": 0.9, "lujo": 1.0, "rural": 0.75,
    },
)  # fmt: skip

PELUQUERIA = Sector(
    key="peluqueria",
    name="Peluquería",
    emoji="💈",
    game="foto",
    blurb="Traen una foto y un «pero un poco más corto». El pelo que cortas no vuelve a crecer.",
    m2=45,
    kind="local",
    products=(
        Product("corte", "Corte", 60, 420, pack=30, weight=1.0),
        Product("degradado", "Degradado", 60, 200, pack=30, weight=1.0),
        Product("barba", "Arreglo de barba", 40, 250, pack=30, weight=1.0),
        Product("tinte", "Tinte", 450, 900, pack=10, weight=1.0),
        Product("mechas", "Mechas", 650, 1_250, pack=10, weight=1.0),
        Product("peinado", "Lavar y peinar", 80, 300, pack=20, weight=1.0),
    ),
    clients=4,
    patience=45,
    setup=90_000,
    license=10_000,
    utilities=800,
    order=(1, 1),
    # Mañana y tarde; a mediodía, el bocadillo; de noche, cerrado.
    curve=(
        0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.1, 0.3, 0.9, 1.3, 1.5, 1.5,
        1.3, 0.8, 0.6, 0.9, 1.3, 1.5, 1.5, 1.3, 0.8, 0.3, 0.1, 0.05,
    ),
    fit={
        "marinero": 0.95, "historico": 1.05, "centro": 1.15, "obrero": 1.0,
        "playa": 0.95, "lujo": 1.25, "rural": 0.85,
    },
)  # fmt: skip

SECTORS: tuple[Sector, ...] = (
    CAFETERIA, TIENDA, TALLER, HELADERIA, FERRETERIA,
    GUACHINCHE, PANADERIA, PESCADERIA, MOVILES, PELUQUERIA,
)  # fmt: skip
SECTOR_BY_KEY: dict[str, Sector] = {s.key: s for s in SECTORS}


@dataclass(frozen=True, slots=True)
class PriceLevel:
    """Un nivel de precios de todo el negocio.

    Attributes:
        key: Clave estable.
        name: Cómo se enseña.
        ticket: Lo que multiplica el precio de cada cosa.
        clients: Lo que multiplica los clientes de un turno.
        luxury_clients: Lo mismo en un barrio de lujo (allí lo caro espanta menos).
        reputation: Lo que suma a la reputación cada cliente bien atendido.
    """

    key: str
    name: str
    ticket: float
    clients: float
    luxury_clients: float
    reputation: float


PRICE_LEVELS: tuple[PriceLevel, ...] = (
    PriceLevel("barato", "Barato (−15 %)", 0.85, 1.15, 1.05, 0.1),
    PriceLevel("normal", "Normal", 1.0, 1.0, 1.0, 0.0),
    PriceLevel("caro", "Caro (+15 %)", 1.15, 0.85, 0.95, 0.0),
    PriceLevel("carisimo", "Carísimo (+30 %)", 1.3, 0.68, 0.85, -0.1),
)
PRICE_BY_KEY: dict[str, PriceLevel] = {p.key: p for p in PRICE_LEVELS}

#: Paletas del rótulo y el toldo: `(nombre, color principal, color de acento)`. La escena
#: recibe los colores; el índice es lo que se guarda.
PALETTES: tuple[tuple[str, str, str], ...] = (
    ("Atlántico", "#0f4c81", "#f6bd2d"),
    ("Turquesa", "#13867f", "#fbf3e0"),
    ("Buganvilla", "#a81f5c", "#fbf3e0"),
    ("Atardecer", "#d4600c", "#1b2a35"),
    ("Laurisilva", "#1f5a37", "#f0dfb2"),
    ("Picón", "#2a242d", "#f58220"),
    ("Gofio", "#cfb676", "#1b2a35"),
    ("Mojo", "#b3261e", "#f6bd2d"),
)

#: Emblemas del rótulo (los dibuja la escena con trazos, por su clave).
EMBLEMS: tuple[tuple[str, str], ...] = (
    ("taza", "Taza"),
    ("estrella", "Estrella"),
    ("ancla", "Ancla"),
    ("sol", "Sol"),
    ("pez", "Pez"),
    ("drago", "Drago"),
    ("corona", "Corona"),
    ("rayo", "Rayo"),
)

#: Nombres de los clientes habituales. Cada negocio saca cinco por su semilla.
REGULAR_NAMES: tuple[str, ...] = (
    "Doña Carmen",
    "Don Agustín",
    "Yeray",
    "Nayra",
    "Pino",
    "Candelaria",
    "Don Fefo",
    "Chano",
    "Mari Luz",
    "Ayoze",
    "Tinerfe",
    "Doña Lola",
    "Iballa",
    "Don Paco",
    "Guaci",
    "Cristo",
    "Nauzet",
    "Doña Fela",
    "Jonay",
    "Dácil",
)
#: Habituales por negocio.
REGULARS_PER_BUSINESS = 5

#: Avería de cada recambio del taller: el síntoma que cuenta el cliente y la pista que se
#: dibuja en el coche (`cue`, la lee la escena).
FAULTS: dict[str, tuple[tuple[str, ...], str]] = {
    "pastillas": (
        ("Chirría al frenar y cada vez frena menos.", "Hace un ruido de metal cuando freno."),
        "frenos",
    ),
    "bateria": (
        (
            "Por las mañanas no arranca: hace clic y nada.",
            "Se me quedó tirado con las luces puestas.",
        ),
        "bateria",
    ),
    "aceite": (
        ("Se me encendió la lucecita roja de la aceitera.", "Echa humo azulado por el tubo."),
        "aceite",
    ),
    "correa": (
        ("Al arrancar suena un chirrido largo bajo el capó.", "Tiene un silbido raro en el motor."),
        "correa",
    ),
    "bujias": (
        ("Da tirones al acelerar y gasta más que nunca.", "En frío falla y tiembla todo."),
        "bujias",
    ),
    "neumatico": (
        ("Vibra el volante y tira hacia un lado.", "Se me baja la rueda cada dos días."),
        "rueda",
    ),
}

#: Lo que dice un cliente al llegar (`{name}` es su nombre si es habitual). Al menos la
#: mitad son frases normales, como pide la Biblia.
GREETINGS: dict[str, tuple[str, ...]] = {
    "comandas": (
        "Buenas, ¿nos pone esto?",
        "Hola, cuando pueda.",
        "Buenos días. Para la mesa del fondo.",
        "¿Me cobra luego? Primero esto.",
        "Venimos del Teide y traemos un hambre…",
        "Rapidito, que se me va la guagua.",
        "Lo de siempre no, que hoy estoy de dieta. Bueno, sí.",
    ),
    "caja": (
        "Hola, ¿me cobra?",
        "Buenas tardes.",
        "Esto es todo, gracias.",
        "¿Tiene cambio? Solo llevo este billete.",
        "Me llevo esto y me voy, que tengo el coche mal aparcado.",
        "¿A cuánto está el gofio hoy? Da igual, cóbrame.",
    ),
    "diagnostico": (
        "Buenas, el coche me hace una cosa rara.",
        "Hola, ¿me lo puede mirar hoy?",
        "Me lo dejó así mi cuñado, que entiende de motores.",
        "Necesito el coche para el lunes, ¿puede ser?",
        "No sé lo que es, pero suena a caro.",
    ),
    # 9.48.0. En la brasa, en los encargos, en la balanza y en la revisión, el saludo va
    # delante de lo que pide (que lo dice el juego); en la foto, delante de la foto.
    "brasa": (
        "Buenas, mesa para dos.",
        "¿Qué hay hoy? Bueno, lo de la pizarra.",
        "Venimos andando desde el pueblo de al lado.",
        "Hola. Sin prisa, que hoy es domingo para nosotros.",
        "Mi abuelo decía que aquí se comía como en casa. Él no cocinaba nunca.",
        "Ponnos algo y lo que sobre, para llevar.",
    ),
    "encargos": (
        "Buenos días.",
        "Hola, vengo a recoger.",
        "Buenas, que llego tarde al trabajo.",
        "Ayer llamé por teléfono.",
        "Lo encargó mi madre, que manda más que yo.",
        "Vengo a por lo de la fiesta, que si no llego me matan.",
    ),
    "balanza": (
        "Buenos días.",
        "Hola, ¿qué tal viene hoy el pescado?",
        "Buenas, ¿está fresco? No me conteste, ya lo huelo.",
        "Hola, que hoy viene la familia.",
        "Para hacerlo a la sal, como dice mi suegra.",
    ),
    "revision": (
        "Hola, ¿me puede echar un vistazo?",
        "Buenas, lo necesito para mañana.",
        "Se me cayó. Bueno, lo tiré. Bueno, se me cayó.",
        "Hola. No tengo copia de las fotos, así que cuidado.",
        "Está al 1 % desde 2024.",
    ),
    "foto": (
        "Hola, ¿tiene hueco ahora?",
        "Buenas. Le enseño una foto.",
        "Quiero un cambio, pero que no se note.",
        "Buenas. Lo de la última vez no, por favor.",
        "Hola. Mi novia dice que esto me queda bien.",
    ),
}

# -- Datos de los minijuegos de 9.48.0 (los usa `bot.services.business_games`) ---------

#: Lo que va a la brasa en el guachinche (lo demás de la mesa se sirve sin más) y la
#: terminación con que se dice su punto (`carne` poco hecha, `queso` poco hecho).
GRILLED: dict[str, str] = {"carne": "a", "costillas": "as", "queso": "o"}
#: Los puntos de la brasa: `(clave, cómo lo pide, desde, hasta)`, en tanto por ciento de lo
#: hecho. De `BURNT` en adelante, se quema.
POINTS: tuple[tuple[str, str, int, int], ...] = (
    ("poco", "poco hech{g}", 40, 50),
    ("punto", "en su punto", 60, 70),
    ("muy", "muy hech{g}", 80, 90),
)
POINT_BY_KEY: dict[str, tuple[str, str, int, int]] = {p[0]: p for p in POINTS}
BURNT = 100

#: Nombres de las bolsas de encargo que no son del cliente (los que sí son, los suyos).
BAG_NAMES: tuple[str, ...] = (
    "Mari Carmen", "Pepe", "Loli", "Juan", "Candelaria", "Paco", "Yurena", "Antonio",
    "Pino", "Domingo", "Mercedes", "Agustín", "Nieves", "Manolo", "Conchi", "Ramón",
)  # fmt: skip
#: Iniciales del apellido de las bolsas.
BAG_INITIALS = "ABCDGHLMPRST"

#: Pescadería: `(gramos mínimos, máximos y medios)` de una pieza de cada especie, y lo que
#: se pide. `Product.price` es el de una pieza media: la venta se cobra por lo que pesa
#: (`price × gramos / medios`), y la compra al mayorista es por pieza (`Product.cost`).
FISH_GRAMS: dict[str, tuple[int, int, int]] = {
    "chicharros": (60, 140, 100),
    "vieja": (250, 600, 400),
    "cherne": (350, 900, 600),
    "sama": (300, 700, 500),
    "pulpo": (500, 1_300, 900),
    "lapas": (150, 350, 250),
}
FISH_ASKS: dict[str, tuple[int, ...]] = {
    "chicharros": (250, 500, 750, 1_000),
    "vieja": (500, 1_000, 1_500),
    "cherne": (1_000, 1_500, 2_000),
    "sama": (500, 1_000, 1_500),
    "pulpo": (1_000, 1_500, 2_000),
    "lapas": (250, 500, 750),
}

#: Móviles: lo que cuenta el cliente de cada avería (dice solo la primera; las demás se ven
#: en el dibujo). La escena dibuja la marca de cada una por su clave.
PHONE_FAULTS: dict[str, tuple[str, ...]] = {
    "pantalla": ("Se me cayó y ya no se ve bien.", "La pantalla hace cosas raras."),
    "bateria_movil": ("No me dura la batería ni hasta el mediodía.", "Se calienta mucho."),
    "conector": ("Ya no carga si no pongo el cable de lado.", "Lo enchufo y no carga."),
    "camara_movil": ("Las fotos salen borrosas.", "La cámara no enfoca."),
    "altavoz": ("Cuando me llaman no oigo nada.", "Se oye todo rasposo."),
    "tapa": ("Se me partió por detrás.", "Tiene la parte de atrás rajada."),
}
#: Probabilidad de que traiga el protector de pantalla rayado sin que la pantalla esté rota
#: (la trampa de la revisión) y de que el móvil tenga 1, 2 o 3 averías.
PROTECTOR_CHANCE = 0.3
FAULT_COUNTS: tuple[float, ...] = (0.55, 0.35, 0.10)

#: Peluquería: los largos (de más corto a más largo) y los colores.
LENGTHS: tuple[str, ...] = ("rapado", "corto", "medio", "largo")
LENGTH_NAMES: dict[str, str] = {
    "rapado": "Rapado", "corto": "Corto", "medio": "Media melena", "largo": "Largo",
}  # fmt: skip
COLORS: tuple[str, ...] = ("natural", "tinte", "mechas")
COLOR_NAMES: dict[str, str] = {"natural": "Su color", "tinte": "Tinte", "mechas": "Mechas"}
#: Lo que añade el cliente a la foto: `(clave, frase)`. La regla de cada una, en
#: `business_games.restyle`.
STYLE_TWISTS: tuple[tuple[str, str], ...] = (
    ("igual", "Igual que en la foto."),
    ("corto", "Como en la foto, pero un poco más corto."),
    ("sin_degradado", "Como en la foto, pero sin degradado."),
    ("degradado", "Como en la foto, pero con degradado."),
    ("natural", "Como en la foto, pero con mi color: nada de tinte."),
    ("mechas", "Como en la foto, pero con mechas."),
    ("barba", "Como en la foto, y arréglame la barba."),
    ("peinado", "Como en la foto, y luego me lavas y me peinas."),
)


# -- Fase 2: crecer el local ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Size:
    """Un tamaño de local: lo que multiplica los metros del sector y lo que da de sí.

    Attributes:
        key: Clave estable.
        name: Cómo se enseña.
        m2: Lo que multiplica los metros del sector (y con ellos el alquiler).
        clients: Lo que multiplica los clientes de un turno (caben más).
        pool: Lo que multiplica el cupo del barrio que le toca (se le ve desde más lejos).
        costs: Lo que multiplica los suministros y el acondicionamiento.
    """

    key: str
    name: str
    m2: float
    clients: float
    pool: float
    costs: float


SIZES: tuple[Size, ...] = (
    Size("pequeno", "Pequeño", 1.0, 1.0, 1.0, 1.0),
    Size("mediano", "Mediano", 1.6, 1.3, 1.25, 1.4),
    Size("grande", "Grande", 2.4, 1.6, 1.5, 1.8),
)
#: Un local propio de `pisos` sirve si mide al menos esta parte de los metros del sector;
#: desde `SIZE_FROM[i]` veces los metros, es del tamaño `i`.
SIZE_FROM: tuple[float, ...] = (0.75, 1.3, 2.0)

#: Clases de amortización: `(nombre, coeficiente anual máximo)`. Tabla de la estimación
#: directa simplificada (Orden de 27 de marzo de 1998, art. 30 RIRPF). En el juego el año
#: fiscal es la semana: cada noche se amortiza la séptima parte del coeficiente.
AMORTIZATION: dict[str, tuple[str, float]] = {
    "instalaciones": ("Instalaciones y mobiliario", 0.10),
    "maquinaria": ("Maquinaria", 0.12),
    "informatica": ("Equipos informáticos", 0.26),
    "herramientas": ("Útiles y herramientas", 0.30),
}
#: Clase del acondicionamiento del local (las obras al montarlo o al trasladarse).
SETUP_CLASS = "instalaciones"


@dataclass(frozen=True, slots=True)
class Equipment:
    """Una mejora del local: se compra una vez, se amortiza y cambia el turno.

    Attributes:
        key: Clave estable (la dibuja la escena).
        name: Cómo se enseña.
        sectors: Sectores que la pueden instalar.
        cost: Base sin IGIC.
        kind: Clase de `AMORTIZATION`.
        clients: Lo que suma a los clientes de un turno (0,15 = un 15 % más).
        patience: Lo que suma a la paciencia de cada cliente.
        spoil: La parte de la merma de cada noche que evita.
        tips: Lo que suma a la propina de quien se atiende rápido (parte del tique).
        nightly_fee: Tasa municipal de cada noche (la terraza ocupa la vía pública).
    """

    key: str
    name: str
    sectors: tuple[str, ...]
    cost: int
    kind: str
    clients: float = 0.0
    patience: float = 0.0
    spoil: float = 0.0
    tips: float = 0.0
    nightly_fee: int = 0

    def effect(self) -> str:
        """Lo que hace, en una línea."""
        parts = []
        if self.clients:
            parts.append(f"+{round(self.clients * 100)} % de clientes por turno")
        if self.patience:
            parts.append(f"+{round(self.patience * 100)} % de paciencia")
        if self.spoil:
            parts.append(f"−{round(self.spoil * 100)} % de merma")
        if self.tips:
            parts.append(f"+{round(self.tips * 100)} puntos de propina")
        if self.nightly_fee:
            parts.append(f"tasa de {self.nightly_fee} Y$ cada noche")
        return ", ".join(parts)


EQUIPMENT: tuple[Equipment, ...] = (
    Equipment("cafetera2", "Cafetera de dos grupos", ("cafeteria",), 60_000, "maquinaria",
              clients=0.15),
    Equipment("terraza", "Terraza con cuatro mesas", ("cafeteria", "heladeria", "guachinche"),
              45_000,
              "instalaciones", clients=0.2, nightly_fee=300),
    Equipment("vitrina", "Vitrina refrigerada", ("cafeteria", "panaderia"), 30_000, "maquinaria",
              spoil=0.5, tips=0.03),
    Equipment("tpv", "Segunda caja con TPV", ("tienda", "ferreteria"), 22_000, "informatica",
              patience=0.25, clients=0.05),
    Equipment("camara", "Cámara frigorífica", ("tienda", "pescaderia"), 50_000, "instalaciones",
              spoil=0.6),
    Equipment("estanterias", "Estanterías y cartelería", ("tienda", "ferreteria"), 25_000,
              "instalaciones", clients=0.12),
    Equipment("elevador", "Elevador de dos columnas", ("taller",), 90_000, "maquinaria",
              clients=0.25),
    Equipment("diagnosis", "Máquina de diagnosis", ("taller",), 30_000, "informatica",
              patience=0.3, clients=0.05),
    Equipment("llaves", "Llaves de impacto y carro", ("taller",), 30_000, "herramientas",
              clients=0.1),
    Equipment("mantecadora", "Mantecadora artesana", ("heladeria",), 55_000, "maquinaria",
              clients=0.15, tips=0.03),
    Equipment("vitrina_helados", "Vitrina de 18 sabores", ("heladeria",), 25_000,
              "maquinaria", spoil=0.5, patience=0.1, clients=0.05),
    Equipment("copiadora", "Máquina de copiar llaves", ("ferreteria",), 45_000, "maquinaria",
              clients=0.15),
    # 9.48.0
    Equipment("parrilla", "Parrilla de leña grande", ("guachinche",), 50_000, "maquinaria",
              clients=0.15),
    Equipment("barricas", "Barricas del país", ("guachinche",), 30_000, "instalaciones",
              tips=0.03, patience=0.1),
    Equipment("horno", "Horno de pisos", ("panaderia",), 60_000, "maquinaria", clients=0.15),
    Equipment("turnos", "Dispensador de números", ("panaderia", "pescaderia"), 8_000,
              "informatica", patience=0.2),
    Equipment("balanza", "Balanza con etiquetadora", ("pescaderia",), 15_000, "informatica",
              patience=0.15, clients=0.05),
    Equipment("vivero", "Vivero de marisco", ("pescaderia",), 40_000, "instalaciones",
              clients=0.12),
    Equipment("microscopio", "Microscopio y estación de soldadura", ("moviles",), 30_000,
              "herramientas", patience=0.3),
    Equipment("fundas", "Escaparate de fundas", ("moviles",), 20_000, "instalaciones",
              clients=0.12, tips=0.02),
    Equipment("sillon", "Segundo sillón", ("peluqueria",), 35_000, "instalaciones",
              clients=0.2),
    Equipment("lavacabezas", "Lavacabezas con masaje", ("peluqueria",), 25_000, "maquinaria",
              tips=0.04, patience=0.1),
)  # fmt: skip
EQUIPMENT_BY_KEY: dict[str, Equipment] = {e.key: e for e in EQUIPMENT}


def equipment_for(sector: str) -> tuple[Equipment, ...]:
    """Las mejoras que puede instalar un sector, por orden del catálogo."""
    return tuple(e for e in EQUIPMENT if sector in e.sectors)


@dataclass(frozen=True, slots=True)
class Campaign:
    """Una campaña de publicidad: más clientes y más cupo del barrio unos días.

    Attributes:
        key: Clave estable.
        name: Cómo se enseña.
        cost: Base sin IGIC (gasto de la actividad).
        days: Días que dura, contando el de hoy.
        boost: Lo que suma a los clientes de cada turno y a tu parte del cupo del barrio.
    """

    key: str
    name: str
    cost: int
    days: int
    boost: float


CAMPAIGNS: tuple[Campaign, ...] = (
    Campaign("buzoneo", "Buzoneo y carteles", 4_000, 3, 0.10),
    Campaign("redes", "Anuncios en redes", 15_000, 5, 0.20),
    Campaign("radio", "Cuña en la radio local", 45_000, 7, 0.30),
)
CAMPAIGN_BY_KEY: dict[str, Campaign] = {c.key: c for c in CAMPAIGNS}

#: Quien deja reseña y no es habitual: nombre e inicial, como en las plataformas.
REVIEWERS: tuple[str, ...] = (
    "Ana G.", "Kevin R.", "Yurena P.", "Jonathan M.", "Saray D.", "Airam C.", "Begoña L.",
    "Fran T.", "Idaira S.", "Marcos V.", "Tamara B.", "Echedey F.", "Lucía N.", "Moisés A.",
)  # fmt: skip
#: Lo que dice una reseña según sus estrellas (la reseña guarda el índice). Al menos la
#: mitad de cada lista son frases normales.
REVIEW_LINES: dict[int, tuple[str, ...]] = {
    5: (
        "Todo perfecto. Volveremos.",
        "Rápido, amable y a buen precio.",
        "El mejor del barrio, sin discusión.",
        "Me atendieron enseguida y con una sonrisa.",
        "Cinco estrellas porque no me deja poner seis.",
        "Mi suegra salió contenta. Eso no lo consigue ni el párroco.",
    ),
    4: (
        "Muy bien, aunque había algo de cola.",
        "Buen sitio. Repetiré.",
        "Correcto y sin sorpresas.",
        "Bien atendido. El local, algo pequeño.",
        "Un cuatro: el cinco lo reservo para el bar de mi primo.",
    ),
    3: (
        "Normal. Ni fu ni fa.",
        "Bien, pero tardaron un rato.",
        "Cumple, sin más.",
        "Esperaba más por lo que dicen las reseñas.",
        "Tres estrellas, que es lo que tiene el wifi.",
    ),
    2: (
        "Me equivocaron el pedido.",
        "Tardaron muchísimo en atenderme.",
        "Me dieron mal el cambio.",
        "No creo que vuelva.",
        "El de la caja tenía cara de llevar tres turnos seguidos.",
    ),
    1: (
        "Me fui sin que me atendieran.",
        "Una espera eterna. No vuelvo.",
        "Me acusaron de robar. Inaceptable.",
        "Cero estrellas, si se pudiera.",
        "Pedí un cortado y me sirvieron un disgusto.",
    ),
}

# -- Fase 4: la plantilla (`bot.services.business_staff`) ------------------------------

#: Nombres y apellidos de los candidatos a empleado.
STAFF_FIRST_NAMES: tuple[str, ...] = (
    "Yaiza", "Ayoze", "Iballa", "Nayra", "Airam", "Acerina", "Echedey", "Guacimara", "Jonay",
    "Tanausú", "Dácil", "Idaira", "Nauzet", "Yeray", "Aythami", "Gara", "Cristo", "Desirée",
    "Marta", "Carmen", "Pedro", "José Luis", "Lucía", "Sergio", "Laura", "Javi", "Marina",
    "Andrea", "Pablo", "Rocío", "Kevin", "Jessica", "Saúl", "Loli", "Paco", "Mari Carmen",
    "Héctor", "Noemí", "Raúl", "Elena",
)  # fmt: skip
STAFF_LAST_NAMES: tuple[str, ...] = (
    "Perdomo", "Hernández", "Rodríguez", "Cabrera", "Santana", "Suárez", "Afonso", "Déniz",
    "Trujillo", "Melián", "Betancor", "Falcón", "Quintana", "Ojeda", "Mesa", "Padrón", "Armas",
    "Bethencourt", "Expósito", "Morales", "González", "Díaz", "Pérez", "Medina",
)  # fmt: skip
