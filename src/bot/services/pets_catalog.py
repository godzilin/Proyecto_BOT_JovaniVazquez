"""Las especies de mascota: quiénes son, cuánto cuestan y cómo hablan.

Cada especie es una fila de `SPECIES`. Las adoptables entran en la tienda
como artículos de tipo `Kind.PET` (`bot.services.shop_catalog` las convierte
en artículos de serie con la clave `mascota_<especie>`); las que tienen
`spawn` no se venden: aparecen solas cuando se da su momento.

La gracia de tenerlas es su **personalidad**: sus frases en los mensajes del
bot (`lines`), sus trucos (`tricks`) y, como mucho, un detalle de juego
pequeño:

- `talk`: lo habladora que es (el loro sale el doble; el gato, menos).
- `night_owl`: se anima de madrugada (sale el doble de 0:00 a 6:00).
- `xp_focus`: canal favorito del bonus de XP (el canario canta en voz).
- `diet`: `"comida"` (lo comestible de la mochila), `"todo"` (la cabra se
  come cualquier objeto, Modelo 100 incluido) o `"nada"` (la piedra no come).
- `gift_rate`: cuánto trae regalos (el perro, más; la piedra, nunca).
- `favourites`: comidas del colmado que le dan vínculo extra.

**Frases.** `lines` usa como claves los valores de `bot.services.pets.Event`
(`"bust"`, `"tax"`…) y los ánimos `"good"`, `"bad"` y `"neutral"`. Las
plantillas llevan `{pet}` (el nombre, en negrita), `{sound}` y `{collector}`
(Perro Sanxe). Sin adjetivos con género: el nombre lo pone el dueño.

**Ley 7/2023.** Su art. 56 prohíbe vender perros, gatos y hurones en tiendas
de animales: el colmado solo los da en adopción, con su «tasa de adopción».
Los demás animales sí se venden. Todos pagan IGIC al tipo general (7 %): la
Ley 4/2012 de Canarias no les da un tipo propio.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.services.pets import Event, Spawn

#: Rarezas de las especies, de más a menos común (solo para enseñarlas).
RARITIES: tuple[str, ...] = ("Común", "Rara", "Épica", "Legendaria", "Mítica")


@dataclass(frozen=True, slots=True)
class Species:
    """Una especie de mascota.

    Attributes:
        key: Clave estable; se guarda en cada mascota y en los logros.
        aisle: Pasillo del colmado (por tema, como cualquier artículo).
        rarity: Una de `RARITIES`.
        price: Tasa de adopción o precio, sin IGIC. Las que aparecen solas
            lo usan como valor para el patrimonio.
        personality: Una línea que la describe en `mascota`.
        description: Ficha del escaparate (como mucho 200 caracteres).
        sound: El ruido que hace (`{sound}` en las frases).
        lines: Frases por momento o ánimo (ver el docstring del módulo).
        tricks: Tres trucos, que aprende a nivel 3, 6 y 9 de vínculo.
        spawn: Cuándo aparece sola; `None` si se adopta en la tienda.
        stock: Unidades en todo el servidor; `None` sin límite.
        level: Nivel mínimo para adoptarla.
    """

    key: str
    emoji: str
    name: str
    aisle: str
    rarity: str
    price: int
    personality: str
    description: str
    sound: str
    lines: dict[str, tuple[str, ...]]
    tricks: tuple[str, str, str]
    favourites: tuple[str, ...] = ()
    diet: str = "comida"
    talk: float = 1.0
    night_owl: bool = False
    xp_focus: str = "todo"
    gift_rate: float = 1.0
    gift_line: str = "{pet} te trae {gift} en la boca. No preguntes de dónde lo ha sacado."
    spawn: Spawn | None = None
    stock: int | None = None
    level: int = 0
    adoption: bool = False

    @property
    def catalog_key(self) -> str:
        """Clave del artículo de la tienda (`mascota_gato`)."""
        return f"{CATALOG_PREFIX}{self.key}"

    @property
    def adoptable(self) -> bool:
        """Si se consigue en la tienda (las demás aparecen solas)."""
        return self.spawn is None


#: Prefijo de la clave de serie de las mascotas en la tienda.
CATALOG_PREFIX = "mascota_"

G, B, N = "good", "bad", "neutral"
W = Event


# fmt: off
SPECIES: tuple[Species, ...] = (
    # 🐾 Tienda de animales ---------------------------------------------------------------
    Species(
        "gato", "🐈", "Gato común europeo", "animales", "Común", 3_000,
        "Te quiere a su manera, que es desde lejos y con condiciones.",
        "Tasa de adopción: la Ley 7/2023 no deja vender gatos en tiendas. Viene "
        "vacunado, con chip y con desprecio de serie.",
        "miau",
        {
            G: ("{pet} parpadea despacio. En gato, eso es un «te quiero». No te acostumbres.",
                "{pet} se digna a sentarse a tu lado. Celebración discreta.",
                "{pet} ronronea encima de las fichas ganadas. Ahora son suyas."),
            B: ("{pet} tira tu ficha de la mesa con la pata. Mirándote a los ojos.",
                "{pet} se lava una pata mientras pierdes. No es su problema.",
                "{pet} se va de la habitación. Ha visto suficiente."),
            N: ("{pet} se sienta justo encima del teclado.",
                "{pet} mira fijamente a una esquina vacía. Algo sabe.",
                "{pet} pide comida aunque acaba de comer."),
            W.BUST: ("{pet} se mete en la caja vacía de tu cartera. Por fin le sirve de algo.",),
            W.TAX: ("{pet} le bufa a {collector}. Primera vez que alguien le planta cara.",),
        },
        ("Te trae un ratón de juguete y lo deja a tus pies. Tienes que fingir sorpresa.",
         "Se hace un ovillo perfecto dentro de una caja de zapatos de la talla 36.",
         "Abre la puerta del armario sola. Ya no hay secretos en esta casa."),
        favourites=("lata_atun",), talk=0.7, night_owl=True, gift_rate=1.3,
        gift_line="{pet} te deja {gift} en la puerta. Es un regalo. Tienes que darle las gracias.",
        adoption=True,
    ),
    Species(
        "perro", "🐕", "Perro mestizo de la protectora", "animales", "Común", 3_500,
        "Lo más leal del servidor. Cree que eres la mejor persona del mundo.",
        "Tasa de adopción de la protectora (la Ley 7/2023 no deja vender perros en "
        "tiendas). Mezcla de todo y de nada. Te quiere ya.",
        "guau",
        {
            G: ("{pet} da saltos como si hubieras vuelto de la guerra.",
                "{pet} mueve tanto la cola que tira el vaso. Merece la pena.",
                "{pet} te lame la cara. Ganes lo que ganes, para {pet} eres lo mejor."),
            B: ("{pet} te pone la cabeza en las rodillas. No pasa nada, de verdad.",
                "{pet} trae su pelota para que se te pase. Funciona un poco.",
                "{pet} gime bajito. Le duele más a {pet} que a ti."),
            N: ("{pet} te mira con ojos de «¿salimos?».",
                "{pet} ladra al cartero de Hacienda. Por si acaso.",
                "{pet} da tres vueltas y se tumba. Feliz por nada."),
            W.BUST: ("{pet} quiere currar para pagarte el alquiler. No puede: no tiene NIE.",),
            W.WORK: ("{pet} te acompaña hasta la puerta del curro y te espera ahí ocho horas.",),
        },
        ("Da la pata. La otra también, si insistes.",
         "Se hace el muerto cuando oye la palabra «Hacienda».",
         "Te trae las zapatillas. Las de otra persona, pero te las trae."),
        favourites=("hueso_jamon", "tortilla"), talk=1.2, gift_rate=1.5,
        adoption=True,
    ),
    Species(
        "hamster", "🐹", "Hámster de rueda", "animales", "Común", 1_200,
        "Corre mucho y no llega a ningún sitio. Como la economía.",
        "Corre 9 km cada noche en su rueda sin moverse de la jaula. Si apuestas, lo hace "
        "más rápido por los nervios.",
        "ñic",
        {
            G: ("{pet} corre en su rueda de la emoción. Se ha subido el PIB.",
                "{pet} se mete tres pipas en cada moflete para celebrarlo."),
            B: ("{pet} se para en seco en la rueda. Hasta {pet} lo ha visto venir.",
                "{pet} esconde comida para el invierno. Hace bien, viendo tu saldo."),
            N: ("{pet} corre en la rueda. Sin parar. Sin destino.",
                "{pet} se queda dormido con una pipa en la boca."),
            W.BUST: ("{pet} te ofrece las pipas de su moflete. Es todo lo que tiene.",),
        },
        ("Se mete en un rollo de papel higiénico y sale por el otro lado. Ovación.",
         "Hace equilibrio sobre la rueda parada. Nadie se lo esperaba.",
         "Vacía sus mofletes en tu mano. Señal de confianza absoluta."),
        favourites=("pipas",), talk=0.9, night_owl=True, gift_rate=0.6,
        gift_line="{pet} saca {gift} de un moflete. Lo llevaba ahí desde hace días.",
    ),
    Species(
        "pez", "🐟", "Pez de feria", "animales", "Común", 400,
        "Memoria de tres segundos. Olvida tus pérdidas antes que tú.",
        "Ganado en la tómbola de unas fiestas de pueblo y criado en una bolsa. Su memoria "
        "dura menos que una promesa electoral.",
        "blub",
        {
            G: ("{pet} da una vuelta a la pecera. Y otra. Y otra. Ha olvidado por qué.",
                "{pet} hace burbujas de alegría. Duran tres segundos."),
            B: ("{pet} ya ha olvidado lo que acabas de perder. Te recomienda lo mismo.",
                "{pet} te mira a través del cristal sin juzgarte. No le da tiempo."),
            N: ("{pet} descubre el castillo de plástico por primera vez. Por milésima vez.",
                "{pet} abre y cierra la boca. Es lo que hay."),
        },
        ("Sigue tu dedo por el cristal. Durante tres segundos.",
         "Salta por un aro de plástico. Luego no se acuerda de haberlo hecho.",
         "Reconoce tu cara. O eso parece. Puede que sea el reflejo."),
        favourites=("barra_pan",), talk=0.8, gift_rate=0.3,
        gift_line="{pet} hace una burbuja y dentro aparece {gift}. Nadie sabe cómo.",
    ),
    Species(
        "tortuga", "🐢", "Tortuga de Florida jubilada", "animales", "Rara", 6_000,
        "Tiene 30 años, no tiene prisa y ha visto pasar a cinco presidentes.",
        "Especie invasora: venderla está prohibido desde 2011 (RD 630/2013). Esta es de "
        "antes y viene en adopción. Lenta, pero llega.",
        "…",
        {
            G: ("{pet} saca la cabeza del caparazón para mirarte. Gran honor.",
                "{pet} avanza un centímetro hacia ti. Euforia de tortuga."),
            B: ("{pet} se mete en el caparazón. Volverá cuando pase la crisis.",
                "{pet} ha visto crisis peores. Lo de 2008, por ejemplo."),
            N: ("{pet} toma el sol en su piedra. Lleva así desde el martes.",
                "{pet} se come una hoja de lechuga en veinte minutos."),
            W.BUST: ("{pet} te recuerda que la tortuga ganó a la liebre. A la ruleta, no.",),
        },
        ("Llega al final del pasillo. Tarda una tarde, pero llega.",
         "Saca la cabeza cuando dices su nombre. A veces.",
         "Se pone panza arriba y se da la vuelta sola. Nivel olímpico."),
        favourites=("zanahoria", "tomate"), talk=0.5, gift_rate=0.5,
    ),
    Species(
        "loro", "🦜", "Loro gris parlanchín", "animales", "Rara", 12_000,
        "Repite todo lo que oye. Todo. Cuidado con lo que dices delante de Hacienda.",
        "Con papeles CITES en regla. Habla más que un tertuliano y entiende lo mismo. "
        "Bonus doble de XP por mensajes; en voz se calla.",
        "¡rrrah!",
        {
            G: ("{pet} grita «¡TOMA MANGÓ!» y despierta a todo el edificio.",
                "{pet} imita el sonido de la tragaperras pagando. Le sale clavado.",
                "{pet} repite «millonario, millonario» con tu voz."),
            B: ("{pet} repite la palabrota que acabas de soltar. Delante de tu madre.",
                "{pet} dice «¡a cero, a cero!». Lo ha aprendido de ti.",
                "{pet} imita la risa de {collector}. Escalofriante."),
            N: ("{pet} dice «Perro Sanxe» y se ríe solo.",
                "{pet} recita tu número de cuenta en voz alta. Gracias, {pet}.",
                "{pet} silba el himno de Canarias desafinando."),
            W.TAX: ("{pet} repite «retención, retención» hasta que le tapas la jaula.",),
        },
        ("Dice tu nombre. Y el de tu ex, que no se lo has enseñado tú.",
         "Imita el tono de llamada del móvil. Llevas una semana contestando a nadie.",
         "Recita el art. 7.y de la Ley del IRPF de memoria. Le ha enseñado el IMV."),
        favourites=("pipas", "platano"), talk=2.0, xp_focus="mensajes",
    ),
    Species(
        "conejo", "🐇", "Conejo enano belier", "animales", "Común", 2_500,
        "Orejas caídas, nariz que no para y una vida tranquila.",
        "Enano de orejas caídas. Se come los cables del cargador. Que lo sepas.",
        "fff",
        {
            G: ("{pet} da un saltito en el aire de pura alegría. Se llama «binky».",
                "{pet} mueve la nariz a toda velocidad. Emoción máxima de conejo."),
            B: ("{pet} da un golpe con la pata trasera en el suelo. Aviso de peligro.",
                "{pet} se esconde detrás del sofá hasta que pase."),
            N: ("{pet} se come el cable del cargador. Otro más.",
                "{pet} se tumba estirado como una alfombra."),
        },
        ("Viene cuando le llamas. Solo si llevas una zanahoria.",
         "Salta un obstáculo de dos libros de alto. Récord del barrio.",
         "Da vueltas alrededor de tus pies. Te está cortejando."),
        favourites=("zanahoria",), gift_rate=0.8,
    ),
    Species(
        "huron", "🦦", "Hurón juguetón", "animales", "Rara", 8_000,
        "Ladrón profesional de calcetines. Duerme 18 horas y las otras 6 lía.",
        "Tasa de adopción: como perros y gatos, los hurones no se venden en tiendas desde "
        "la Ley 7/2023. Te robará los calcetines.",
        "dook dook",
        {
            G: ("{pet} hace la danza de guerra del hurón: saltos de lado y la boca abierta.",
                "{pet} roba una ficha ganada y la esconde debajo del sofá."),
            B: ("{pet} roba lo poco que te queda y lo esconde. Por tu bien, dice.",
                "{pet} se duerme de golpe en mitad de la jugada. De pura vergüenza ajena."),
            N: ("{pet} se mete por la manga de tu sudadera.",
                "{pet} roba un calcetín. El otro ya lo tenía."),
        },
        ("Se mete por un tubo y sale con un calcetín. Siempre.",
         "Hace la croqueta por el suelo cuando le llamas.",
         "Esconde el mando de la tele en un sitio que ni la UCO encontraría."),
        favourites=("huevo",), night_owl=True, gift_rate=1.6,
        gift_line="{pet} aparece con {gift}. Robado, seguro. A alguien le falta.",
        adoption=True,
    ),

    # 🌴 Canarias -------------------------------------------------------------------------
    Species(
        "canario", "🐤", "Canario de canto timbrado", "canarias", "Común", 2_000,
        "El que dio nombre a las islas. O al revés. Canta mejor que Jovani.",
        "Canto timbrado de concurso. Bonus doble de XP en los canales de voz, que es "
        "donde se le oye; por escrito, ni pío.",
        "pío pío",
        {
            G: ("{pet} se arranca con una isa de las de Teror.",
                "{pet} canta tan alto que los vecinos aplauden desde el balcón.",
                "{pet} trina de alegría. Es lo suyo."),
            B: ("{pet} se calla. Un canario callado es mala señal.",
                "{pet} canta una folía triste. Muy apropiado."),
            N: ("{pet} se baña en su bebedero y lo pone todo perdido.",
                "{pet} pía bajito, como quien no quiere molestar."),
            W.BUST: ("{pet} canta «Mírame» de Jovani para animarte. No ayuda.",),
        },
        ("Silba los primeros compases del himno de Canarias.",
         "Se posa en tu dedo sin miedo. Confianza de las islas.",
         "Imita el pito de la guagua. Media calle sale corriendo a la parada."),
        favourites=("alpiste", "pella_gofio"), xp_focus="voz", talk=1.2,
    ),
    Species(
        "bardino", "🐕‍🦺", "Perro bardino majorero", "canarias", "Rara", 9_000,
        "Perro pastor de Fuerteventura. Serio, noble y desconfiado con los de fuera.",
        "Raza canaria de pastoreo, de la tierra de las cabras. En adopción, que la Ley "
        "7/2023 no deja venderlo. Vigila tu saldo como un rebaño.",
        "wof",
        {
            G: ("{pet} asiente con seriedad. Bien hecho, pastor.",
                "{pet} junta las fichas ganadas como si fueran cabras. Ni una se escapa."),
            B: ("{pet} mira el saldo como quien cuenta las cabras y le faltan.",
                "{pet} te gruñe por apostar sin cabeza. Tiene razón."),
            N: ("{pet} vigila la puerta. Nadie de fuera pasa sin permiso.",
                "{pet} huele el viento de Fuerteventura. Calima a la vista."),
            W.TAX: ("{pet} no deja pasar a {collector} de la puerta. Hoy no cobra.",),
        },
        ("Junta a todos los del canal en una esquina, como un rebaño.",
         "Te avisa de la calima un día antes.",
         "Distingue a los majoreros de los turistas por el olor."),
        favourites=("queso_guia",), talk=0.9, adoption=True,
    ),
    Species(
        "cabra", "🐐", "Cabra majorera", "canarias", "Rara", 15_000,
        "Se come todo lo que encuentra. Todo. Incluido el Modelo 100.",
        "Majorera de pura cepa, de las que dan el queso. Come cualquier objeto de tu "
        "mochila, no solo comida. Su favorito: el Modelo 100.",
        "beee",
        {
            G: ("{pet} se come el boleto ganador. Lo has cobrado justo antes.",
                "{pet} bala de alegría y se sube a la mesa."),
            B: ("{pet} se come tu declaración de la renta. Igual es lo mejor.",
                "{pet} mastica el recibo de lo que has perdido. Prueba destruida."),
            N: ("{pet} se está comiendo la cortina.",
                "{pet} se sube al tejado. Nadie sabe cómo."),
            W.TAX: ("{pet} se come la carta de Hacienda. Técnicamente, no te ha llegado.",),
            W.BUY: ("{pet} se come el ticket de la compra. Y la bolsa.",),
        },
        ("Se sube a lo más alto de la nevera y bala desde ahí.",
         "Abre la puerta del corral con los dientes.",
         "Da leche para un queso de flor. Bueno, para medio."),
        favourites=("modelo_100",), diet="todo", talk=1.1, gift_rate=0.4,
    ),
    Species(
        "presa", "🐶", "Presa canario", "canarias", "Épica", 60_000,
        "Imponente por fuera, un trozo de pan por dentro. Perro Sanxe le tiene miedo.",
        "Dogo canario, el perro de la isla. En adopción y con el seguro de responsabilidad "
        "civil que pide la Ley 7/2023 (cómpralo en la tienda).",
        "¡GUAU!",
        {
            G: ("{pet} apoya la cabezota en tu regazo. Pesa 50 kilos. Vale la pena.",
                "{pet} suelta un ladrido que hace temblar los cristales. Celebración."),
            B: ("{pet} mira a la mesa del casino y la mesa se disculpa.",
                "{pet} se sienta a tu lado. Nadie se va a reír de ti hoy."),
            N: ("{pet} babea encima de tu móvil.",
                "{pet} duerme en el sofá. Entero. No te queda sitio."),
            W.TAX: ("{pet} se planta delante de {collector}. {collector} decide volver otro día.",),
        },
        ("Se sienta con solo mirarle.",
         "Pasea a tu lado sin correa y sin tirar. Mejor educado que muchos.",
         "Se tumba panza arriba para que le rasques. 50 kilos de cariño."),
        favourites=("hueso_jamon",), talk=0.8, level=10, adoption=True,
    ),

    # 🌹 La Moncloa -----------------------------------------------------------------------
    Species(
        "perro_sanxe", "🐶", "Perro Sanxe", "moncloa", "Mítica", 25_000_000,
        "El recaudador en persona. Ahora vive en tu casa y te cobra el alquiler.",
        "El único, el original, el de Hacienda. Uno en todo el servidor. Se come lo que "
        "le eches y sale siempre que pagas impuestos.",
        "guau (19 % retenido)",
        {
            G: ("{pet} aplaude tu premio y se queda el 19 %. Por costumbre.",
                "{pet} sonríe. Cuando {pet} sonríe, alguien paga.",
                "{pet} se apunta tu premio en una libreta. Para la declaración."),
            B: ("{pet} te consuela: «perder no tributa». Qué detalle.",
                "{pet} mira tus pérdidas con cara de no poder desgravarlas."),
            N: ("{pet} revisa tus extractos del banco mientras duermes.",
                "{pet} se sube al Falcon de juguete y hace «brrrm».",
                "{pet} lee el BOE en voz alta. Es viernes por la tarde."),
            W.TAX: ("{pet} cobra el impuesto en persona, sin intermediarios. Eficiencia.",
                    "{pet} te mira con orgullo. Contribuyente ejemplar."),
            W.IMV: ("{pet} te da la paguita con una mano y te retiene con la otra.",),
        },
        ("Firma un decreto ley con la pata un viernes por la tarde.",
         "Aguanta cinco días de reflexión sin ladrar y luego decide seguir.",
         "Encuentra dinero en cualquier sitio: debajo del sofá, en tu cartera, en tu renta."),
        diet="todo", talk=1.3, stock=1, level=50,
    ),

    # 🇵🇷 Rincón boricua ------------------------------------------------------------------
    Species(
        "coqui", "🐸", "Coquí", "boricua", "Rara", 5_000,
        "La ranita de Puerto Rico. Canta toda la noche. Toda.",
        "Diminuto, ruidoso y orgullosamente boricua. Canta de noche y en voz: bonus doble "
        "de XP en los canales de voz.",
        "¡co-quí!",
        {
            G: ("{pet} canta «¡co-quí!» tan fuerte que se oye en San Juan.",
                "{pet} perrea un poquito. Dentro de lo que una rana puede."),
            B: ("{pet} canta más bajito. Respeto por tu saldo.",
                "{pet} se esconde debajo de una hoja hasta que pase el chubasco."),
            N: ("{pet} canta a las 3 de la mañana. Como cada noche.",
                "{pet} se pega al cristal de la ventana y te mira."),
        },
        ("Canta a dúo con el canario. El canario no está de acuerdo.",
         "Salta de tu hombro a tu cabeza sin avisar.",
         "Se aprende el coro de una canción de Jovani. Ahora la canta toda la noche."),
        favourites=("chuche",), talk=1.1, night_owl=True, xp_focus="voz",
    ),
    Species(
        "gallo", "🐓", "Gallo de patio boricua", "boricua", "Rara", 7_000,
        "Se cree el dueño del barrio. Canta a las cinco, llueva o truene.",
        "Gallo de patio, de los que despiertan a toda la calle. Come gofio de millo: el "
        "maíz es el maíz, en Canarias o en Puerto Rico.",
        "¡kikirikí!",
        {
            G: ("{pet} se sube a la mesa y canta victoria. Literalmente.",
                "{pet} infla el pecho como si el premio fuera suyo."),
            B: ("{pet} picotea el suelo con rabia. El mundo le debe algo.",
                "{pet} canta a destiempo. Hasta el gallo se ha liado."),
            N: ("{pet} canta. Son las cinco de la mañana. Siempre son las cinco.",
                "{pet} persigue al perro del vecino. Y gana."),
            W.WORK: ("{pet} te ha despertado para ir a currar. Dos horas antes, pero bueno.",),
        },
        ("Canta exactamente a la hora que le pides. Más o menos.",
         "Se sube a tu hombro como un pirata.",
         "Pone en fila a todas las gallinas del barrio. Liderazgo."),
        favourites=("saco_gofio",), talk=1.3,
    ),

    # 🇭🇰 Importación de Hong Kong ----------------------------------------------------------
    Species(
        "koi", "🎏", "Carpa koi de Mong Kok", "hongkong", "Épica", 40_000,
        "Del mercado de peces de colores de Mong Kok. Trae buena suerte, dicen allí.",
        "Comprada en Goldfish Street y traída por Robuso en el avión, en una bolsa con agua. "
        "En Hong Kong, una koi es prosperidad.",
        "blop",
        {
            G: ("{pet} salta fuera del agua. En Hong Kong eso es buena suerte. Aquí, un charco.",
                "{pet} nada en círculos de prosperidad. Robuso lo aprobaría."),
            B: ("{pet} se queda quieta en el fondo del estanque. Mal feng shui.",
                "{pet} abre la boca. Ha visto tu saldo."),
            N: ("{pet} nada despacio entre las piedras. Zen absoluto.",
                "{pet} sube a pedir comida. Siempre sube a pedir comida."),
        },
        ("Come de tu mano sin miedo.",
         "Nada siguiendo tu dedo por el borde del estanque.",
         "Se coloca justo en el centro cuando hay visita. Sabe posar."),
        favourites=("barra_pan", "dimsum"), talk=0.8, level=10,
    ),
    Species(
        "panda", "🐼", "Panda en préstamo", "hongkong", "Legendaria", 900_000,
        "No es tuyo: es un préstamo diplomático de China. Te lo dejan diez años.",
        "Diplomacia del panda: China no los vende, los presta. Este viene de Ocean Park con "
        "papeles, cuidadores y mucha agenda. Dos en todo el servidor.",
        "mmmh",
        {
            G: ("{pet} rueda por el suelo. Los cuidadores lo graban para la tele china.",
                "{pet} aplaude con las patas. Momento viral asegurado."),
            B: ("{pet} se tapa los ojos con las patas. No quiere verlo.",
                "{pet} se sienta a comer bambú. Ya habrá otra tirada."),
            N: ("{pet} come bambú. Catorce horas al día. Es su trabajo.",
                "{pet} se cae de un tronco. Nadie se ríe: es diplomacia."),
        },
        ("Hace la voltereta sin caerse. Casi.",
         "Saluda a la cámara con la pata. Entrevista en la CCTV.",
         "Se queda dormido abrazado a ti. Incidente diplomático evitado."),
        favourites=("dimsum", "zanahoria"), talk=0.9, stock=2, level=35,
    ),

    # 💎 Vida de rico ---------------------------------------------------------------------
    Species(
        "caniche", "🐩", "Caniche de la calle Serrano", "lujo", "Épica", 50_000,
        "Peluquería semanal, abrigo de cuadros y más seguidores que tú.",
        "Criado en el barrio de Salamanca, con pedigrí y cuenta de Instagram. En adopción, "
        "como manda la Ley 7/2023, pero con tasa de pijo.",
        "guau, querida",
        {
            G: ("{pet} posa con el premio para sus 40.000 seguidores.",
                "{pet} levanta el hocico. Ya lo sabía: nació para esto."),
            B: ("{pet} te mira por encima del hombro. Tú no eres de su clase.",
                "{pet} se va al spa a superar el disgusto. A tu cuenta."),
            N: ("{pet} se mira en el espejo. Le gusta lo que ve.",
                "{pet} pide merluza. No pescadilla: merluza."),
        },
        ("Desfila como en una pasarela de Cibeles.",
         "Sabe distinguir el champán bueno del cava. Solo bebe el bueno.",
         "Hace una reverencia a las visitas. A las que le caen bien."),
        favourites=("tarta_nata",), talk=1.1, level=15, adoption=True,
    ),
    Species(
        "pavo_real", "🦚", "Pavo real", "lujo", "Épica", 120_000,
        "Abre la cola cada vez que alguien le mira. Y cuando no, también.",
        "Para el jardín del chalet. Grita como un niño perdido a las seis de la mañana. "
        "Lo bonito se paga.",
        "¡léon!",
        {
            G: ("{pet} abre la cola entera. Tus éxitos son sus éxitos.",
                "{pet} grita de alegría. Los vecinos llaman a la policía."),
            B: ("{pet} cierra la cola despacio. Luto.",
                "{pet} se da la vuelta para no verte perder. Con elegancia."),
            N: ("{pet} pasea por el jardín como si fuera suyo. Lo es.",
                "{pet} se mira en el agua de la piscina."),
        },
        ("Abre la cola cuando dices «foto».",
         "Camina a tu lado como un guardaespaldas con plumas.",
         "Se sube a la verja y vigila. Nadie entra sin ser visto."),
        favourites=("uvas",), talk=1.2, level=20,
    ),
    Species(
        "caballo", "🐎", "Caballo de pura raza española", "lujo", "Legendaria", 400_000,
        "Pura raza española, de la Feria de Abril. Tiene más clase que tú y lo sabe.",
        "PRE con papeles, para lucirlo en la Feria de Abril o en la romería del Pino. "
        "Cuadra, herrador y veterinario aparte.",
        "hiiii",
        {
            G: ("{pet} relincha y hace una cabriola. Feria de Abril en tu salón.",
                "{pet} levanta las patas delanteras. Eres el rey del cortijo."),
            B: ("{pet} resopla. Ha visto apuestas mejores en el hipódromo.",
                "{pet} se aparta de la mesa. No quiere que lo vendan para pagar deudas."),
            N: ("{pet} come paja con mucha dignidad.",
                "{pet} sacude las crines al sol. Parece un anuncio."),
        },
        ("Baila al son de una sevillana.",
         "Hace el paso español. Te pones a llorar de la emoción.",
         "Te deja montar sin silla. Confianza absoluta."),
        favourites=("zanahoria",), talk=0.9, level=30,
    ),

    # 💀 Lo que no debería venderse -------------------------------------------------------
    Species(
        "pulpo", "🐙", "Pulpo adivino", "shitpost", "Rara", 6_666,
        "Sobrino nieto del pulpo Paul. Adivina el resultado justo después de que pase.",
        "Pariente del pulpo Paul del Mundial de 2010. Predice todo con total seguridad. "
        "Acierta como el CIS.",
        "plop",
        {
            G: ("{pet} dice que lo sabía. Lo dice después, claro.",
                "{pet} levanta un tentáculo: «os lo dije». Nadie le había oído decir nada."),
            B: ("{pet} lo vio venir y no dijo nada. Mal pulpo.",
                "{pet} se pone de color gris. Así se pone cuando pierdes."),
            N: ("{pet} cambia de color. Ahora es naranja. Ahora es tu sofá.",
                "{pet} abre un tarro de mermelada. Lo cierra. Lo vuelve a abrir."),
            W.BIG_WIN: ("{pet} reclama el premio: dice que lo predijo con tinta.",),
        },
        ("Elige entre dos cajas y siempre acierta la que tú no elegirías.",
         "Se esconde en una lata de fabada. Nadie lo encuentra.",
         "Predice el resultado de la próxima encuesta del CIS. Con margen de error."),
        favourites=("paella",), talk=1.2,
    ),
    Species(
        "pedrusco", "🪨", "Piedra mascota", "shitpost", "Común", 1,
        "Es una piedra. No come, no habla, no se mueve. La mascota perfecta.",
        "La mascota de moda de 1975, ahora en el colmado. No come, no sale a pasear y "
        "tampoco trae regalos. Pero te quiere. Probablemente.",
        "…",
        {
            G: ("{pet} no reacciona. Es una piedra. Pero se le nota el orgullo.",),
            B: ("{pet} sigue ahí. Siempre sigue ahí. Es lo que tienen las piedras.",),
            N: ("{pet} está. Ser, es mucho decir.",
                "{pet} ha cambiado de posición. No, era la luz."),
            W.BUST: ("{pet} es lo único que te queda. Al menos no se va.",),
        },
        ("Se queda quieta cuando le dices «quieta». Obediencia total.",
         "Se hace la muerta. Le sale muy natural.",
         "No hace nada, pero lo hace con estilo."),
        diet="nada", talk=0.4, gift_rate=0.0,
    ),

    # Las que no se venden: aparecen solas ----------------------------------------------
    Species(
        "cucaracha", "🪳", "Cucaracha superviviente", "shitpost", "Rara", 66,
        "Sobrevivió a una bomba nuclear. Sobrevivirá a tu saldo.",
        "No se vende: se te mete en casa cuando te quedas a cero. Come de todo.",
        "tic tic",
        {
            G: ("{pet} sale de debajo de la nevera a celebrarlo. Ahora hay dos.",),
            B: ("{pet} se siente como en casa. Las ruinas son su hábitat.",
                "{pet} te mira y piensa: «ya somos iguales»."),
            N: ("{pet} cruza la cocina a toda velocidad. Nadie la ha visto. Todos la han visto.",
                "{pet} se esconde detrás del microondas."),
            W.BUST: ("{pet} se muda a tu cartera vacía. Hay sitio de sobra.",),
        },
        ("Echa a volar en dirección a tu cara. Truco favorito del público.",
         "Se queda patas arriba y se levanta cuando te vas. Teatro.",
         "Sobrevive a una chancla de madre. Récord mundial."),
        diet="todo", night_owl=True, talk=1.0,
        spawn=Spawn(frozenset({Event.BUST}), chance=0.35,
                    hint="Aparece cuando te quedas a cero."),
    ),
    Species(
        "gato_callejero", "🐈‍⬛", "Gato callejero tuerto", "espana", "Épica", 1_500,
        "Lleva diez vidas en la calle. Te ha elegido para la undécima.",
        "No se adopta: viene solo, el día que cobras el IMV, y se queda. Tiene más "
        "calle que el Metro de Madrid.",
        "mrrrau",
        {
            G: ("{pet} te mira con su único ojo. Aprobado.",
                "{pet} acepta una caricia. Una sola. No abuses."),
            B: ("{pet} ha estado peor. Bastante peor. Tú también saldrás.",
                "{pet} se va a dar una vuelta por los tejados. Vuelve cuando se te pase."),
            N: ("{pet} se lame una cicatriz de una pelea de 2019. Ganó.",
                "{pet} duerme en el capó del coche del vecino."),
            W.IMV: ("{pet} te acompaña a la ventanilla. Sabe cómo funciona esto.",),
        },
        ("Abre la basura con una sola pata.",
         "Se cuela en el bar de abajo y le dan una sardina.",
         "Asusta al perro más grande del barrio con un solo bufido."),
        favourites=("lata_atun",), night_owl=True, talk=0.8, gift_rate=1.4,
        spawn=Spawn(frozenset({Event.IMV}), chance=0.06,
                    hint="Aparece, a veces, al cobrar el IMV."),
    ),
    Species(
        "paloma", "🕊️", "Paloma de la Plaza Mayor", "espana", "Rara", 120,
        "Rata con alas, según algunos. Compañera de curro, según tú.",
        "No se vende: se te posa en el hombro, a veces, al salir de currar. Come migas "
        "y no tiene modales.",
        "currucucú",
        {
            G: ("{pet} sacude las alas y te deja un recuerdo en el hombro. Da suerte.",),
            B: ("{pet} picotea las migas de tu desgracia.",
                "{pet} se va volando a la Plaza Mayor. Volverá cuando haya pan."),
            N: ("{pet} anda moviendo la cabeza adelante y atrás. Concentración total.",
                "{pet} se pelea con otra paloma por una patata frita."),
            W.WORK: ("{pet} te espera en la cornisa del curro. Para el bocadillo.",),
        },
        ("Se posa en tu cabeza como en una estatua.",
         "Te sigue por la calle hasta tu casa. Ya no hay vuelta atrás.",
         "Lleva un mensaje a otra persona del canal. Llega con migas."),
        favourites=("barra_pan", "bocata_calamares"), talk=1.0,
        spawn=Spawn(frozenset({Event.WORK, Event.BUSINESS}), chance=0.04,
                    hint="Se te posa, a veces, al salir de un turno de trabajo."),
    ),
    Species(
        "cotorra", "🦜", "Cotorra argentina", "animales", "Rara", 300,
        "Especie invasora de los parques de Madrid. Ahora invade tu casa.",
        "No se vende (es invasora, RD 630/2013): llega volando cuando subes de nivel. "
        "Habla aún más que el loro. Bonus doble de XP por mensajes.",
        "¡cra cra!",
        {
            G: ("{pet} grita de alegría con toda su bandada. Treinta cotorras en tu balcón.",
                "{pet} celebra subiendo y bajando de la lámpara."),
            B: ("{pet} grita de indignación. Con la bandada entera.",
                "{pet} se come la planta del balcón de rabia."),
            N: ("{pet} construye un nido de 40 kilos en tu terraza.",
                "{pet} discute con las palomas por el territorio."),
            W.LEVEL: ("{pet} trae a toda la familia a celebrar tu nivel.",),
        },
        ("Dice «hola» con acento de Buenos Aires.",
         "Abre el pestillo de la jaula. Era decorativa.",
         "Convence a otras cinco cotorras de mudarse contigo."),
        favourites=("pipas",), talk=2.0, xp_focus="mensajes",
        spawn=Spawn(frozenset({Event.LEVEL}), chance=0.2,
                    hint="Llega volando, a veces, cuando subes de nivel."),
    ),
    Species(
        "urraca", "🐦‍⬛", "Urraca de la feria", "amuletos", "Épica", 5_000,
        "Le gusta todo lo que brilla. Por eso vino a la Rueda.",
        "No se vende: se posa, a veces, en la Rueda cuando la giras. Colecciona "
        "todo lo que brilla, incluidos tus premios.",
        "¡chac-chac!",
        {
            G: ("{pet} se lleva el premio al nido para verlo brillar.",
                "{pet} da saltitos alrededor de lo que te ha tocado."),
            B: ("{pet} te mira como diciendo que eso no brillaba nada.",
                "{pet} se va al tejado a buscar algo más bonito."),
            N: ("{pet} esconde una chapa de cerveza debajo del sofá.",
                "{pet} se mira en la cuchara del café un buen rato."),
            W.WHEEL: ("{pet} se posa encima de la aguja y la rueda no se para hasta que "
                      "se va.",
                      "{pet} picotea las bombillas de la rueda una a una."),
        },
        ("Te trae un anillo que no es de nadie. O eso dice.",
         "Imita el ruido de la rueda al girar.",
         "Se aprende tu cara y no se le olvida nunca."),
        favourites=("rueda_piruleta",), talk=1.2, gift_rate=1.6,
        spawn=Spawn(frozenset({Event.WHEEL}), chance=0.03,
                    hint="Se posa, a veces, en la Rueda cuando la giras."),
    ),
    Species(
        "lagarto", "🦎", "Lagarto gigante de El Hierro", "canarias", "Legendaria", 50_000,
        "En peligro crítico de extinción. Solo se deja ver el Día de Canarias.",
        "No se vende: está protegido. Solo aparece el 30 de mayo, Día de Canarias, a "
        "quien hace algo ese día. Cuídalo, que quedan pocos.",
        "…ssss",
        {
            G: ("{pet} toma el sol en tu ventana. La ley le protege y presume de ello.",
                "{pet} levanta la cabeza. Un gigante de El Hierro no celebra más."),
            B: ("{pet} se esconde entre las piedras de la Fuga de Gorreta.",
                "{pet} ha sobrevivido a gatos asilvestrados. Tu saldo no le asusta."),
            N: ("{pet} se queda inmóvil al sol. Lleva así desde el Pleistoceno.",
                "{pet} come una hoja de tabaiba con mucha calma."),
        },
        ("Sale de su escondite cuando le llamas. Cosa rarísima.",
         "Se deja medir por los biólogos del Cabildo sin protestar.",
         "Posa para una foto de National Geographic."),
        favourites=("tomate", "platano"), talk=0.7,
        spawn=Spawn(chance=0.5, day=(5, 30),
                    hint="Solo aparece el 30 de mayo, Día de Canarias."),
    ),
    Species(
        "mosquito", "🦟", "Mosquito tigre", "shitpost", "Rara", 10,
        "Especie invasora del verano español. No come: se alimenta de ti.",
        "No se vende: se te pega en julio o agosto. No come comida; con tu sangre le "
        "basta. Zumba de madrugada.",
        "zzzzz",
        {
            G: ("{pet} te pica para celebrarlo. Es su forma de querer.",),
            B: ("{pet} te pica. Lo que faltaba.",
                "{pet} zumba en tu oreja justo cuando ibas a dormir del disgusto."),
            N: ("{pet} zumba. No sabes dónde. Nunca sabes dónde.",
                "{pet} se posa en la pared. Lo ves. Lo pierdes."),
        },
        ("Zumba una melodía reconocible. Parece el himno.",
         "Esquiva tres zapatillazos seguidos.",
         "Pica justo donde no llegas a rascarte."),
        diet="nada", night_owl=True, talk=1.1, gift_rate=0.0,
        spawn=Spawn(months=frozenset({7, 8}), chance=0.02,
                    hint="Se te pega en julio o agosto."),
    ),
)
# fmt: on

SPECIES_BY_KEY: dict[str, Species] = {s.key: s for s in SPECIES}
SPECIES_BY_CATALOG_KEY: dict[str, Species] = {s.catalog_key: s for s in SPECIES}
#: Las que aparecen solas, en el orden del catálogo.
SPAWNING: tuple[Species, ...] = tuple(s for s in SPECIES if s.spawn is not None)


def species_of(catalog_key: str | None) -> Species | None:
    """Especie de un artículo de la tienda por su clave de serie, o `None`."""
    return SPECIES_BY_CATALOG_KEY.get(catalog_key or "")
