"""Cajas de la tienda: colecciones, probabilidades y sorteo (sin Discord ni base de datos).

Hay siete cajas que se abren desde la mochila (`Box`): cuatro **por niveles**
(📦 cartón, 🎁 regalo, 💼 maletín del ministro y 👑 cofre del Rey emérito) y tres
**de temporada** (🎄 Navidad, 🎃 Halloween y 🎂 aniversario del servidor), que solo se
venden en sus meses, como las skins con `season`. La caja botín de siempre
(`caja_botin`) ya no se vende, pero quien la tenga puede seguir abriéndola.

**Qué sale.** Piezas de colección (`Piece`): coleccionables de existencias ilimitadas
que solo se consiguen en cajas. Están repartidas en colecciones (`COLLECTIONS`):
nueve normales de 8 piezas y tres de temporada de 6, con rareza Común, Rara,
Épica, Legendaria y Mítica (`Rarity`). Una pieza es un artículo `Kind.TROPHY`
oculto en el escaparate, como las skins de logro, y su precio de catálogo es su
valor: el de referencia en el Rastro y el del patrimonio. Las piezas del 👗 Armario de
mascota son además ropa que se pone a las mascotas (`bot.services.pet_wardrobe`, las
prendas con `box`): sus filas salen de allí. Además, el maletín y el
cofre dan con probabilidad muy baja un **tapete** de los que se compran en la tienda
(`tapetes_for`), y cualquier caja, una vez de cada 50.000, el 🧊 Tapete Hielo Negro,
que no se vende (`HIELO_ODDS`). Si quien abre ya tiene el tapete (uno por persona), se
vuelve a sortear entre los coleccionables.

**Retorno.** Cada caja devuelve de media `TARGET_RETURN` (~95 %) de su precio a valor
de catálogo: abrir cajas cuesta casi lo mismo que comprar lo de dentro. Lo calcula
la función pura `expected_value` con las probabilidades de `Box.odds`, que son las
de la tabla `ODDS` de este módulo; una prueba lo comprueba para cada caja, contando
los tapetes. Las cajas de temporada sacan piezas de su colección de temporada con
más probabilidad (`SEASON_SHARE`) y las normales también pueden salir.

**Dinero.** Comprar una caja paga IGIC general, como cualquier artículo
(`EconomyService.purchase`). Abrirla **no mueve dinero**: da objetos de la tienda, sin
Y$ (Biblia, sección 4, «Usar un objeto no mueve dinero»), así que no hay ni impuesto
ni gancho de la Renta al abrir. Las cajas que se ganan jugando entran en la mochila
sin cobrar, como un regalo del colmado (`ShopRepository.grant`): tampoco es dinero. Si
una pieza se vende después en el Rastro, tributa allí (ITP y plusvalía) según su valor
de referencia, que es el precio de catálogo de la pieza.
"""

from __future__ import annotations

import random
from collections.abc import Collection as AbcCollection
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum

from bot.services.pet_wardrobe import BOX_COLLECTION, BOX_OUTFITS
from bot.services.skins import HIELO, SKIN_BY_KEY, SKINS, Season, Skin


class Rarity(IntEnum):
    """Rareza de una pieza: de la más común a la más rara."""

    COMMON = 1
    RARE = 2
    EPIC = 3
    LEGENDARY = 4
    MYTHIC = 5

    @property
    def label(self) -> str:
        """`Épica`."""
        return {1: "Común", 2: "Rara", 3: "Épica", 4: "Legendaria", 5: "Mítica"}[self.value]

    @property
    def emoji(self) -> str:
        """El color con que brilla la caja antes de enseñar la pieza."""
        return {1: "⚪", 2: "🟢", 3: "🟣", 4: "🟡", 5: "🔴"}[self.value]

    @property
    def stat(self) -> str:
        """Sufijo de la estadística de logros (`lootbox_got_<sufijo>`)."""
        return {1: "common", 2: "rare", 3: "epic", 4: "legendary", 5: "mythic"}[self.value]


#: Clave de la «rareza» de un tapete en la tabla de probabilidades.
TAPETE = "tapete"
#: Clave del Tapete Hielo Negro en la tabla: sale de cualquier caja, una vez de cada 50.000.
HIELO_ODDS = "hielo"
_RARITY_BY_LETTER = {
    "C": Rarity.COMMON,
    "R": Rarity.RARE,
    "E": Rarity.EPIC,
    "L": Rarity.LEGENDARY,
    "M": Rarity.MYTHIC,
}
#: Las piezas Míticas valen más de 100.000 Y$: piden el mismo nivel que cualquier lujo.
MYTHIC_MIN_LEVEL = 10

#: Cuántas jugadas del casino dan una 📦 de cartón gratis.
FREE_BOX_PLAYS = 100
#: Apuesta mínima de una jugada para que cuente hacia la caja gratis: con menos,
#: cien tiradas de 1 Y$ darían una caja de 1.000 casi sin jugar.
FREE_BOX_MIN_STAKE = 100
#: Cada cuántos niveles se gana una 🎁 de regalo.
FREE_BOX_LEVELS = 5
#: Retorno medio de cada caja respecto a su precio, a valor de catálogo.
TARGET_RETURN = 0.95
#: En una caja de temporada, probabilidad de que la pieza salga de su colección
#: de temporada (si no, sale de las colecciones normales).
SEASON_SHARE = 0.5


@dataclass(frozen=True, slots=True)
class Piece:
    """Una pieza de colección.

    Attributes:
        key: Clave del artículo de la tienda (`pieza_<colección>_<pieza>`); no se
            cambia nunca.
        collection: Clave de su colección.
        rarity: Rareza.
        price: Precio de catálogo en Y$ = su valor (Rastro y patrimonio).
    """

    key: str
    collection: str
    emoji: str
    name: str
    description: str
    rarity: Rarity
    price: int

    @property
    def min_level(self) -> int:
        """Nivel mínimo para comprarla en el Rastro (las muy caras, como todo el lujo)."""
        return MYTHIC_MIN_LEVEL if self.price >= 100_000 else 0


@dataclass(frozen=True, slots=True)
class Collection:
    """Una colección de piezas.

    Attributes:
        key: Clave estable (la cuentan los logros).
        aisle: Pasillo del colmado por su tema.
        pieces: Sus piezas, de la más común a la más rara.
        season: Temporada en la que sale, si es de temporada.
    """

    key: str
    emoji: str
    name: str
    aisle: str
    pieces: tuple[Piece, ...]
    season: Season | None = None

    @property
    def size(self) -> int:
        """Cuántas piezas tiene."""
        return len(self.pieces)

    @property
    def stat(self) -> str:
        """Estadística de logros con las piezas distintas que se tienen."""
        return f"lootbox_col_{self.key}"


def _collection(
    key: str,
    emoji: str,
    name: str,
    aisle: str,
    rows: tuple[tuple[str, str, str, str, str, int], ...],
    season: Season | None = None,
) -> Collection:
    """Monta una colección con filas `(pieza, emoji, nombre, descripción, rareza, precio)`."""
    pieces = tuple(
        Piece(
            key=f"pieza_{key}_{slug}",
            collection=key,
            emoji=emoji_,
            name=name_,
            description=description,
            rarity=_RARITY_BY_LETTER[letter],
            price=price,
        )
        for slug, emoji_, name_, description, letter, price in rows
    )
    return Collection(key, emoji, name, aisle, pieces, season)


# fmt: off
COLLECTIONS: tuple[Collection, ...] = (
    _collection("transicion", "📒", "Cromos de la Transición", "espana", (
        ("tricornio", "🪖", "Cromo del tricornio en el Congreso",
         "El del tiro al techo. Se pega en el álbum con mucho cuidado y sin levantarse del "
         "suelo. «¡Quieto todo el mundo!».", "C", 250),
        ("peluca", "🥸", "Cromo de la peluca de Carrillo",
         "Volvió a España disfrazado y salió en todos los periódicos. La peluca, por "
         "cierto, está agotada.", "C", 300),
        ("pacto", "🤝", "Cromo del Pacto de la Moncloa",
         "Todos firmaron y todos juraron que era por el bien de España. Se despega solo si "
         "hay otra legislatura.", "C", 350),
        ("cafe", "☕", "Cromo del café para todos",
         "Suárez sirvió café a las autonomías y ahora cada una exige su propia cafetera.",
         "R", 1_400),
        ("cambio", "🌹", "Cromo del «Por el cambio»",
         "Cambió todo para que no cambiara casi nada. Edición con rosa y fotografía de "
         "mucho pelo.", "R", 1_800),
        ("palomares", "🏖️", "Cromo de Fraga en Palomares",
         "Se bañó en la playa para demostrar que no pasaba nada. El baño duró lo que dura "
         "un comunicado oficial.", "E", 7_000),
        ("constitucion", "📜", "Cromo de la Constitución en vigor",
         "Firmada con una tinta que solo se seca cuando conviene. Se puede reformar en "
         "cuanto nadie lo necesite.", "L", 35_000),
        ("consenso", "✨", "Cromo dorado del Consenso",
         "Nadie lo ha visto en persona. Se dice que existió en 1978, y que lo perdieron "
         "en algún pasillo del Congreso.", "M", 180_000),
    )),
    _collection("congreso", "🪆", "Muñecos del Congreso", "moncloa", (
        ("dormido", "😴", "Muñeco del diputado dormido",
         "Se despierta solo para votar lo que diga el partido. Es el único que no filtra "
         "nada a la prensa.", "C", 300),
        ("bancada", "🪑", "Muñeco de la bancada vacía",
         "Solo se llena el día de la votación importante. Viene con un hueco para la "
         "foto del pleno.", "C", 320),
        ("ujier", "🔔", "Muñeco del ujier con campanilla",
         "Pide silencio con el mismo éxito que las sirenas del Sahel piden democracia.",
         "C", 380),
        ("portavoz", "🎤", "Muñeco del portavoz que no contesta",
         "Aprietas el botón y dice «lo importante es lo que decida la gente». No dice "
         "qué, ni a quién.", "R", 1_500),
        ("tribuna", "🗣️", "Muñeco de la tribuna de oradores",
         "Habla veinte minutos sin decir nada. Es la edición de lujo de cualquier "
         "intervención de la oposición.", "R", 2_200),
        ("control", "❓", "Muñeco de la sesión de control",
         "Pregunta una cosa, le contestan otra y todo el hemiciclo aplaude. No pregunta "
         "más.", "E", 6_500),
        ("telematico", "💻", "Muñeco del voto telemático",
         "Votó desde casa. Se equivocó de botón y la ley estuvo a punto de salir al "
         "revés.", "E", 9_500),
        ("prorroga", "📅", "Muñeco del presupuesto prorrogado",
         "Lleva en trámite desde antes de que nacieras y de que lo hicieran sus "
         "señorías. Es eterno, como los mandatos de Maduro.", "L", 40_000),
    )),
    _collection("bobbleheads", "🗿", "Bobbleheads de líderes mundiales", "shitpost", (
        ("macron", "🥖", "Bobblehead de Macron",
         "Asiente con la cabeza, pero solo después de un discurso de cuarenta minutos "
         "sobre la grandeza de Europa.", "C", 350),
        ("erdogan", "🏛️", "Bobblehead de Erdoğan",
         "Viene con palacio de mil habitaciones. La cabeza no se mueve: mueve el "
         "Parlamento.", "C", 400),
        ("milei", "🪚", "Bobblehead de Milei",
         "Con motosierra de serie. Cada vez que lo agitas baja el déficit un poco. El del "
         "muñeco, quiero decir.", "C", 450),
        ("bibi", "🥂", "Bobblehead de Bibi",
         "Se mueve al ritmo del juicio: no para, no acaba. Puro y champán de regalo, "
         "no incluidos.", "R", 2_000),
        ("xi", "🐼", "Bobblehead de Xi",
         "Asiente siempre. El resto de la cabeza lo decide el Partido. Prohibido "
         "compararlo con ningún oso de peluche.", "R", 2_500),
        ("kim", "🚀", "Bobblehead de Kim Jong-un",
         "Aplaude solo, por obligación. Trae un misil de cumpleaños que no despega "
         "porque está de adorno.", "E", 9_000),
        ("putin", "🐻", "Bobblehead de Putin",
         "Se sienta al final de una mesa de seis metros. La cabeza solo asiente tras "
         "tres referéndums de rigor.", "L", 48_000),
        ("trump", "🍊", "Bobblehead dorado de Trump",
         "Cabeza de oro y flequillo imposible. Cada sacudida sube un arancel y baja la "
         "bolsa. Ya se ha firmado.", "M", 260_000),
    )),
    _collection("eurovision", "🎤", "Chapas de Eurovisión", "cotillon", (
        ("nulpoints", "0️⃣", "Chapa del «Nul points»",
         "Cero puntos, como las promesas de Bruselas sobre el tapón pegado a la botella.",
         "C", 250),
        ("jurado", "⚖️", "Chapa del jurado profesional",
         "Da diez puntos a quien quiere el jurado y cero a quien quiere la gente. Todo "
         "muy democrático.", "C", 280),
        ("humo", "💨", "Chapa del escenario con humo",
         "Más pirotecnia que ideas. Como un debate de campaña, pero con mejor "
         "iluminación.", "C", 320),
        ("televoto", "📞", "Chapa del televoto",
         "El pueblo habla y luego la organización decide si le hace caso. Las llamadas "
         "cuestan más que la entrada.", "R", 1_200),
        ("viento", "🌬️", "Chapa del ventilador en la melena",
         "Sin viento no hay actuación. El ventilador viene de serie y cobra las dietas "
         "aparte.", "R", 1_700),
        ("chikilicuatre", "🎸", "Chapa del Chikilicuatre",
         "Cantó una cosa que se pegaba más que una directiva europea. Quedó en una "
         "posición honrosa, que es la manera de decir «lejos».", "E", 6_000),
        ("veto", "🚫", "Chapa del veto de última hora",
         "Cuatro comunicados, tres pancartas y un asesor sin funciones después, "
         "sigue la polémica. La organización lo llama «neutralidad».", "E", 8_000),
        ("lalala", "🎶", "Chapa del «La, la, la»",
         "Ganó en 1968. El régimen lo celebró como un triunfo del régimen y el NO-DO tardó "
         "tres días en acordarse de la cantante.", "L", 32_000),
    )),
    _collection("hacienda", "🏷️", "Pegatinas de Hacienda", "ventanilla", (
        ("citaprevia", "🗓️", "Pegatina de «Cita previa agotada»",
         "La más repetida del país. Se pega en la puerta de cualquier oficina y vale "
         "hasta el trimestre que viene.", "C", 260),
        ("prellenado", "📝", "Pegatina del borrador prellenado",
         "Hacienda ya sabe lo que ganas, lo que gastas y con quién. Tú solo tienes que "
         "pagar.", "C", 300),
        ("desgrava", "💶", "Pegatina de «Esto desgrava»",
         "Sirve para cualquier cosa, menos para lo que tú quieres que desgrave.", "C", 340),
        ("conjunta", "👫", "Pegatina de la declaración conjunta",
         "Pagas juntos, ganas juntos y discutís juntos por quién firma el modelo.",
         "R", 1_600),
        ("clave", "🔑", "Pegatina de la Cl@ve caducada",
         "Para entrar necesitas la Cl@ve; para pedir la Cl@ve necesitas entrar. Es un "
         "bucle con sello oficial.", "R", 2_100),
        ("m720", "🌍", "Pegatina del modelo 720",
         "Cuenta lo que tienes fuera. Cuando Bruselas se enfada, también lo que tenían "
         "fuera tus abuelos.", "E", 7_500),
        ("todossomos", "📢", "Pegatina de «Hacienda somos todos»",
         "Un clásico. Siempre dicen «somos todos» hasta que llega la inspección y "
         "resulta que somos tú.", "L", 38_000),
        ("sanxe", "🐶", "Pegatina holográfica de Perro Sanxe",
         "Cambia de cara cuando la mueves: recaudador, estadista, recaudador. En el "
         "reverso, el modelo 100 en blanco.", "M", 200_000),
    )),
    _collection("figuritas", "🏛️", "Figuritas de la Moncloa", "moncloa", (
        ("ujier", "🧑‍✈️", "Figurita del chófer del coche oficial",
         "Lo sabe todo y no ha visto nada. Viene con la puerta abierta y el motor "
         "encendido.", "C", 330),
        ("jardinero", "🌿", "Figurita del jardinero de la Moncloa",
         "El único funcionario que ha visto crecer algo en el palacio sin ayuda de los "
         "fondos europeos.", "C", 380),
        ("cafe", "🍵", "Figurita de la bandeja del Consejo",
         "Un café, una infusión y un asesor que pregunta si hay quórum. Sin azúcar, que "
         "sube el déficit.", "C", 420),
        ("pizarra", "🧑‍🏫", "Figurita de la pizarra de resiliencia",
         "Tiene dibujadas tres flechas y la palabra «transformación». Nadie recuerda "
         "cuándo la escribieron.", "R", 1_800),
        ("manual", "📕", "Figurita del Manual con dedicatoria",
         "Edición de bolsillo con una nota a mano: «a mi gran amigo». El amigo cambia en "
         "cada ejemplar.", "R", 2_400),
        ("consejo", "🎞️", "Figurita del Consejo de Ministros",
         "Veintidós muñecos, veintidós carteras y una sola línea de comunicación. "
         "Se vende sin pilas.", "E", 8_000),
        ("falcon", "✈️", "Figurita del Falcon con tripulación",
         "Aterriza donde haya boda, concierto o tapas. El combustible lo paga el "
         "contribuyente, que eres tú.", "E", 10_500),
        ("laurel", "🏆", "Figurita de Perro Sanxe con laureles",
         "El único recaudador que se corona a sí mismo. Viene en urna con cristal "
         "blindado y factura con IGIC.", "L", 52_000),
    )),
    _collection("nodo", "📽️", "Cromos del NO-DO", "espana", (
        ("pantano", "💧", "Cromo del pantano inaugurado",
         "La cinta se cortó con cámaras, bandas y bendición. El agua llegó años después, "
         "si es que llegó.", "C", 270),
        ("cartilla", "📔", "Cromo de la cartilla de racionamiento",
         "Con un cupón de aceite y otro de ilusión. Se canjea por lo mismo que el "
         "programa electoral.", "C", 310),
        ("seiscientos", "🚗", "Cromo del Seiscientos",
         "Cinco en un coche pensado para cuatro y un abuelo de copiloto. Aparcar era "
         "otra cosa.", "C", 360),
        ("atado", "🪢", "Cromo del «atado y bien atado»",
         "Lo dijeron en un discurso y el nudo se soltó solo. Se vende sin lazo.", "R", 1_500),
        ("autarquia", "🌾", "Cromo de la autarquía",
         "Veinte años de «aquí no necesitamos nada de fuera», con la mesa vacía de "
         "casi todo lo de dentro.", "R", 1_900),
        ("noticiario", "📺", "Cromo del noticiario de las nueve",
         "La voz grave, la música solemne y las noticias que no podían llevar otra "
         "cosa que buenas noticias.", "E", 8_500),
        ("sigue", "🪦", "Cromo «Franco sigue muerto»",
         "Edición exhumada, con sello notarial. Se garantiza que el protagonista no "
         "ha vuelto a dar discursos desde 1975.", "L", 42_000),
        ("paz", "🕊️", "Cromo de los «25 años de paz»",
         "Eslogan del régimen. En el reverso, el índice de los libros de historia que "
         "lo desmienten, uno por uno.", "M", 220_000),
    )),
    _collection("canarias", "🌋", "Souvenirs de Canarias", "canarias", (
        ("iman", "🧲", "Imán del Teide",
         "Se pega en la nevera y recuerda que hay una montaña que da más trabajo que el "
         "Cabildo.", "C", 250),
        ("guagua", "🚌", "Llavero de la guagua que no llega",
         "Marca siempre «5 min». Lleva así desde la legislatura pasada.", "C", 300),
        ("mojo", "🌶️", "Tarrito de mojo de recuerdo",
         "Picante, rojo y cargado de ajo. Sin el pan no se come; sin el IGIC no se vende.",
         "C", 350),
        ("timple", "🎸", "Timple de adorno",
         "Suena cuando lo tocas. Cuando lo tocas tú, suena más a Clavijo explicando la "
         "ley del suelo.", "R", 1_300),
        ("camello", "🐪", "Camello de Timanfaya",
         "Da un paseo por la montaña del fuego y vuelve con la propina del turista. "
         "Cotiza más que algunos sueldos.", "R", 1_800),
        ("tren", "🚆", "Maqueta del tren de Canarias",
         "Se proyecta desde hace décadas. La maqueta es la única versión que ha llegado a "
         "su destino.", "E", 6_800),
        ("roque", "🪨", "Roque Nublo de bolsillo",
         "Una piedra de plástico con vistas. Pesa menos que el expediente de la "
         "carretera de La Aldea.", "E", 9_000),
        ("aldea", "🚧", "Primera piedra de La Aldea",
         "Veinte años de obras y todavía hay piedra para otra primera piedra. "
         "Viene con casco y cinta de inauguración.", "L", 36_000),
    )),
    # -- De temporada: seis piezas cada una.
    _collection("navidad", "🎄", "Navidad en el colmado", "cotillon", (
        ("polvoron", "🍪", "Polvorón sin deshacer",
         "Se deshace en cuanto lo miras. Si lo guardas, lo declaras como ganancia "
         "patrimonial.", "C", 300),
        ("turron", "🍫", "Turrón del año pasado",
         "Más duro que los mandatos del Sahel. Nadie lo ha conseguido partir sin golpe de "
         "Estado.", "C", 350),
        ("decimo", "🎟️", "Décimo sin premio",
         "El Gordo siempre cae en el pueblo de al lado. Este décimo te lo recuerda cada "
         "22 de diciembre.", "R", 1_800),
        ("discurso", "👑", "Discurso del Rey de bolsillo",
         "Se oye con la cena y no se recuerda nada. Todos votan que fue «muy "
         "institucional».", "E", 8_500),
        ("belen", "⛪", "Belén de la Moncloa",
         "Los tres Reyes Magos, la mula, el buey y un asesor sin funciones en el pesebre "
         "cobrando dietas.", "L", 42_000),
        ("noel", "🎅", "Papá Noel con corbata de ministro",
         "Reparte regalos con cargo al erario. El trineo sale de la cuenta del Estado, "
         "que ya es decir.", "M", 230_000),
    ), Season("Navidad", frozenset({12, 1}))),
    _collection("halloween", "🎃", "Noche de brujas", "cotillon", (
        ("calabaza", "🎃", "Calabaza de la Moncloa",
         "La luz de dentro dura lo que un ministro: hasta la próxima remodelación.",
         "C", 300),
        ("fantasma", "👻", "Fantasma de la moción de censura",
         "Aparece cuando quiere la oposición y desaparece cuando cuentan los votos. "
         "Mucho susto y poca mayoría.", "C", 340),
        ("caramelo", "🍬", "Caramelo envenenado de Bruselas",
         "Parece una subvención y trae una directiva por dentro. Se come con letra "
         "pequeña.", "R", 1_900),
        ("vampiro", "🧛", "Vampiro de Hacienda",
         "Chupa un 19 % de todo lo que tienes. La estaca no le hace nada: está "
         "aforado.", "E", 8_000),
        ("zombi", "🧟", "Zombi del Brexit",
         "Lleva muerto desde 2016 y sigue sin acabar de irse. Boris dice que está más "
         "vivo que nunca.", "L", 40_000),
        ("coco", "👹", "El Coco del déficit",
         "Asusta a gobiernos de todos los colores. Nadie lo ha visto de cerca porque "
         "nadie se atreve a mirar las cuentas.", "M", 210_000),
    ), Season("Halloween", frozenset({10}))),
    _collection("aniversario", "🎂", "Aniversario del servidor", "cotillon", (
        ("vela", "🕯️", "Vela del aniversario",
         "Se sopla el 7 de octubre. Pide un deseo, pero que no sea ni el IMV ni un "
         "cambio de rasante.", "C", 280),
        ("gorro", "🥳", "Gorro de cumpleaños del servidor",
         "Un cono de cartón con goma. La goma aguanta lo que aguanta la mayoría "
         "parlamentaria.", "C", 330),
        ("tarta", "🍰", "Tarta del 7 de octubre",
         "Mismo bizcocho, un año más. El IGIC ya viene incluido en la nata.", "R", 1_700),
        ("confeti", "🎊", "Confeti del primer mensaje",
         "Salió disparado cuando alguien escribió «hola» en un canal vacío. Aún aparece "
         "por las esquinas.", "E", 7_500),
        ("cartel", "🪧", "Cartel de «el servidor cumple años»",
         "Colgado con cinta en el canal general. Se ve desde Waterloo, aunque "
         "nadie lo mire.", "E", 9_000),
        ("ficha", "🪙", "La primera ficha del casino",
         "La primera que se jugó en el servidor. Salió roja y dejó un recuerdo que "
         "ya ni Hacienda recuerda.", "L", 44_000),
    ), Season("Aniversario", frozenset({10}))),
)
# fmt: on

#: El Armario de mascota: sus piezas son las prendas de caja del armario de las mascotas,
#: con los precios de las demás colecciones por rareza (el retorno de las cajas no cambia).
COLLECTIONS += (
    _collection(
        BOX_COLLECTION,
        "👗",
        "Armario de mascota",
        "animales",
        tuple((o.key, o.emoji, o.name, o.description, o.box or "C", o.price) for o in BOX_OUTFITS),
    ),
)

PIECES: tuple[Piece, ...] = tuple(p for c in COLLECTIONS for p in c.pieces)
PIECE_BY_KEY: dict[str, Piece] = {p.key: p for p in PIECES}
COLLECTION_BY_KEY: dict[str, Collection] = {c.key: c for c in COLLECTIONS}
#: Colecciones normales (siempre disponibles) y de temporada.
NORMAL_COLLECTIONS: tuple[Collection, ...] = tuple(c for c in COLLECTIONS if c.season is None)
SEASONAL_COLLECTIONS: tuple[Collection, ...] = tuple(c for c in COLLECTIONS if c.season)
#: Claves de los artículos que son piezas de caja.
PIECE_KEYS: frozenset[str] = frozenset(PIECE_BY_KEY)


def is_piece(catalog_key: str | None) -> bool:
    """Si un artículo es una pieza de colección que solo sale en cajas."""
    return (catalog_key or "") in PIECE_KEYS


def piece_of(catalog_key: str | None) -> Piece | None:
    """La pieza de un artículo de la tienda, o `None`."""
    return PIECE_BY_KEY.get(catalog_key or "")


# -- Cajas ---------------------------------------------------------------------------------

#: Probabilidad de cada resultado en cada caja: las cinco rarezas y, en el maletín y el
#: cofre, el tapete, y en todas, el Tapete Hielo Negro (`HIELO_ODDS`, 1 de cada 50.000,
#: restado de los Común). Suman 1 (`_normalised` lo ajusta por si sobra una milésima). Se
#: calibraron con `expected_value` para que cada caja devuelva ~95 % de su precio. Los
#: Común salen al restar; si se toca una cifra hay que volver a calibrar (la prueba exige
#: entre 0,93 y 0,97 en cada caja).
# fmt: off
ODDS: dict[str, dict[str, float]] = {
    # Cartón (1.000): casi todo Común y Raro; la Mítica sale una de cada 10.000.
    "caja_carton": {
        "C": 0.76788, "R": 0.2000, "E": 0.0300, "L": 0.0020, "M": 0.0001,
        "hielo": 0.00002,
    },
    # Regalo (5.000): Común, Rara y alguna Épica; una Legendaria de cada 27.
    "caja_regalo": {
        "C": 0.52598, "R": 0.3000, "E": 0.1300, "L": 0.0370, "M": 0.0070,
        "hielo": 0.00002,
    },
    # Maletín (25.000): lo normal es Épica o Legendaria; tapete, 6 de cada 10.000.
    "maletin_ministro": {
        "C": 0.19008, "R": 0.2500, "E": 0.3000, "L": 0.2000, "M": 0.0593, "tapete": 0.0006,
        "hielo": 0.00002,
    },
    # Cofre (100.000): la mayoría Legendaria o Mítica; tapete, 1 de cada 100.
    "cofre_rey_emerito": {
        "C": 0.14178, "R": 0.0500, "E": 0.1200, "L": 0.3000, "M": 0.3782, "tapete": 0.0100,
        "hielo": 0.00002,
    },
    # Las de temporada (5.000) tiran como el regalo; su colección sale con más peso
    # (`SEASON_SHARE`) y por eso hay que afinar cada una por separado.
    "caja_navidad": {
        "C": 0.52898, "R": 0.3000, "E": 0.1300, "L": 0.0340, "M": 0.0070,
        "hielo": 0.00002,
    },
    "caja_halloween": {
        "C": 0.52598, "R": 0.3000, "E": 0.1300, "L": 0.0370, "M": 0.0070,
        "hielo": 0.00002,
    },
    "caja_aniversario": {
        "C": 0.52768, "R": 0.3000, "E": 0.1300, "L": 0.0353, "M": 0.0070,
        "hielo": 0.00002,
    },
}
# fmt: on


@dataclass(frozen=True, slots=True)
class Box:
    """Una caja que se abre desde la mochila.

    Attributes:
        key: Clave del artículo de la tienda; no se cambia nunca.
        aisle: Pasillo del colmado por su tema.
        price: Precio de catálogo (IGIC general aparte).
        min_level: Nivel mínimo para comprarla y, por tanto, para los tapetes que da.
        season: Temporada en la que se vende, si es de temporada.
        collection: Colección de temporada que sale con más probabilidad.
    """

    key: str
    emoji: str
    name: str
    description: str
    aisle: str
    price: int
    min_level: int = 0
    season: Season | None = None
    collection: str | None = None

    @property
    def odds(self) -> Mapping[str, float]:
        """Probabilidad de cada resultado (`C`, `R`, `E`, `L`, `M`, `tapete` y `hielo`)."""
        return ODDS[self.key]

    @property
    def uses_tapetes(self) -> bool:
        """Si puede dar un tapete."""
        return self.odds.get(TAPETE, 0) > 0


# fmt: off
BOXES: tuple[Box, ...] = (
    Box("caja_carton", "📦", "Caja de cartón",
        "La caja de una mudanza de ministerio, con algo dentro. Se abre desde la mochila: "
        "piezas de colección, casi todas comunes. Devuelve de media un 95 % de lo que cuesta.",
        "bazar", 1_000),
    Box("caja_regalo", "🎁", "Caja de regalo",
        "Con lazo y todo, como los obsequios de Bibi. Piezas de colección de todas las "
        "rarezas; las buenas salen de vez en cuando. Se abre desde la mochila.",
        "cotillon", 5_000),
    Box("maletin_ministro", "💼", "Maletín del ministro",
        "Pesa mucho y no se declara. Dentro, piezas de colección bastante buenas y, con "
        "muchísima suerte, un tapete del casino. Se abre desde la mochila.",
        "moncloa", 25_000),
    Box("cofre_rey_emerito", "👑", "Cofre del Rey emérito",
        "Viene de Abu Dabi con la llave puesta. Piezas de alto copete, casi siempre "
        "Legendarias o Míticas, y un tapete rarísimo. Nivel 10.",
        "lujo", 100_000, min_level=10),
    Box("caja_navidad", "🎄", "Caja de Navidad",
        "Solo en diciembre y enero. Piezas de colección, con más probabilidad las de "
        "Navidad. Se abre desde la mochila.",
        "cotillon", 5_000, season=Season("Navidad", frozenset({12, 1})),
        collection="navidad"),
    Box("caja_halloween", "🎃", "Caja de Halloween",
        "Solo en octubre. Piezas de colección, con más probabilidad las de la noche de "
        "brujas. Truco o trato, tú decides.",
        "cotillon", 5_000, season=Season("Halloween", frozenset({10})),
        collection="halloween"),
    Box("caja_aniversario", "🎂", "Caja del aniversario",
        "Solo en octubre, el mes en que nació el servidor. Piezas de colección, con más "
        "probabilidad las del aniversario. Se abre desde la mochila.",
        "cotillon", 5_000, season=Season("Aniversario", frozenset({10})),
        collection="aniversario"),
)
# fmt: on
BOX_BY_KEY: dict[str, Box] = {b.key: b for b in BOXES}
BOX_KEYS: frozenset[str] = frozenset(BOX_BY_KEY)


def box_of(catalog_key: str | None) -> Box | None:
    """La caja de un artículo de la tienda, o `None`."""
    return BOX_BY_KEY.get(catalog_key or "")


def in_season(box: Box, when: datetime) -> bool:
    """Si la caja se vende en `when` (hora local; las que no son de temporada, siempre)."""
    return box.season is None or box.season.open(when)


def tapetes_for(box: Box) -> tuple[Skin, ...]:
    """Los tapetes que puede dar una caja.

    Los que se compran (`for_sale`), sin existencias limitadas, sin temporada ni imagen
    propia, y que no piden más nivel que la propia caja: así un cofre de nivel 10 no
    salta el nivel 20 del tapete de diamantes.
    """
    if not box.uses_tapetes:
        return ()
    return tuple(
        s
        for s in SKINS
        if s.game == TAPETE
        and s.for_sale
        and s.stock is None
        and s.season is None
        and not s.custom
        and s.min_level <= box.min_level
    )


def _normalised(odds: Mapping[str, float]) -> dict[str, float]:
    """Las probabilidades como pesos que suman exactamente 1."""
    total = sum(odds.values())
    return {k: v / total for k, v in odds.items()}


def _pool(box: Box, rarity: Rarity, *, seasonal: bool) -> tuple[Piece, ...]:
    """Las piezas de una rareza: de la colección de temporada de la caja o de las normales."""
    if seasonal and box.collection is not None:
        collections: tuple[Collection, ...] = (COLLECTION_BY_KEY[box.collection],)
    else:
        collections = NORMAL_COLLECTIONS
    return tuple(p for c in collections for p in c.pieces if p.rarity is rarity)


def expected_value(box: Box) -> float:
    """Lo que vale de media abrir `box`, a precio de catálogo.

    Suma, para cada resultado, su probabilidad por el valor medio de lo que da: la pieza
    media de esa rareza (en una caja de temporada, mezclando su colección y las
    normales según `SEASON_SHARE`) o el tapete medio. Se calcula como si quien abre no
    tuviera ningún tapete: es el retorno de la caja nueva, el que se promete.
    """
    total = 0.0
    for outcome, chance in _normalised(box.odds).items():
        if outcome == HIELO_ODDS:
            total += chance * SKIN_BY_KEY[HIELO].price
            continue
        if outcome == TAPETE:
            tapetes = tapetes_for(box)
            total += chance * sum(s.price for s in tapetes) / len(tapetes)
            continue
        rarity = _RARITY_BY_LETTER[outcome]
        normal = _pool(box, rarity, seasonal=False)
        mean = sum(p.price for p in normal) / len(normal)
        if box.collection is not None:
            special = _pool(box, rarity, seasonal=True)
            if special:
                mean = (
                    SEASON_SHARE * sum(p.price for p in special) / len(special)
                    + (1 - SEASON_SHARE) * mean
                )
        total += chance * mean
    return total


def return_rate(box: Box) -> float:
    """`expected_value` entre el precio: ~0,95 en todas las cajas."""
    return expected_value(box) / box.price


@dataclass(frozen=True, slots=True)
class Drop:
    """Lo que ha salido de una caja.

    Attributes:
        rarity: Rareza (los tapetes cuentan como Míticos al enseñarlos).
        piece: La pieza, si ha salido una.
        skin: El tapete, si ha salido uno.
        rerolled: Si antes tocó un tapete que ya tenía y se volvió a sortear.
    """

    rarity: Rarity
    piece: Piece | None = None
    skin: Skin | None = None
    rerolled: bool = False

    @property
    def catalog_key(self) -> str:
        """Clave del artículo de la tienda que se entrega."""
        return self.skin.catalog_key if self.skin is not None else self.piece.key  # type: ignore[union-attr]

    @property
    def price(self) -> int:
        """Su valor de catálogo."""
        return self.skin.price if self.skin is not None else self.piece.price  # type: ignore[union-attr]

    @property
    def emoji(self) -> str:
        return self.skin.emoji if self.skin is not None else self.piece.emoji  # type: ignore[union-attr]

    @property
    def name(self) -> str:
        return self.skin.name if self.skin is not None else self.piece.name  # type: ignore[union-attr]


def roll(
    box: Box,
    rng: random.Random,
    *,
    owned: AbcCollection[str] = (),
    have: AbcCollection[str] | None = None,
) -> Drop | None:
    """Sortea lo que da una caja.

    Primero se sortea el resultado con las probabilidades de la caja. Si es un tapete
    (o el Hielo Negro) que quien abre ya tiene (uno por persona) o que el servidor no
    vende, se vuelve a sortear entre los coleccionables (`rerolled`). Con la rareza
    elegida, la pieza sale de la colección de temporada con probabilidad `SEASON_SHARE`
    (solo cajas de temporada) y, si no, de las colecciones normales.

    Args:
        owned: Claves de los tapetes que ya tiene.
        have: Claves de los artículos que existen en el servidor y se pueden dar (un
            administrador puede haber retirado alguno). `None` es que están todos.

    Returns:
        Lo que ha tocado, o `None` si no hay ni una pieza que dar.
    """
    weights = _normalised(box.odds)

    def exists(key: str) -> bool:
        return have is None or key in have

    outcome = _choose(weights, rng)
    rerolled = False
    if outcome in (TAPETE, HIELO_ODDS):
        candidates = tapetes_for(box) if outcome == TAPETE else (SKIN_BY_KEY[HIELO],)
        options = [s for s in candidates if s.catalog_key not in owned and exists(s.catalog_key)]
        if options:
            return Drop(Rarity.MYTHIC, skin=rng.choice(options))
        rerolled = True
        weights = _normalised({k: v for k, v in weights.items() if k not in (TAPETE, HIELO_ODDS)})
        outcome = _choose(weights, rng)
    rarity = _RARITY_BY_LETTER[outcome]
    seasonal = box.collection is not None and rng.random() < SEASON_SHARE
    for pool in (
        _pool(box, rarity, seasonal=seasonal),
        _pool(box, rarity, seasonal=False),
        tuple(p for p in PIECES if p.rarity is rarity),
        PIECES,
    ):
        pieces = [p for p in pool if exists(p.key)]
        if pieces:
            piece = rng.choice(pieces)
            return Drop(piece.rarity, piece=piece, rerolled=rerolled)
    return None


def _choose(weights: Mapping[str, float], rng: random.Random) -> str:
    """Un resultado al azar según sus probabilidades."""
    return rng.choices(list(weights), weights=list(weights.values()), k=1)[0]


# -- Textos --------------------------------------------------------------------------------

#: Segundos entre un fotograma de la animación de apertura y el siguiente (tres fotogramas).
FRAME_SECONDS = 0.7

SHAKE_LINES: tuple[str, ...] = (
    "{who} agita {box}… *clac, clac*",
    "{who} sacude {box}. Dentro suena a algo duro y, con suerte, valioso.",
    "{who} abre {box} como quien abre un sobre de la Renta: con miedo.",
    "{who} le pega un meneo a {box}. Se oye un «tintineo» sospechoso.",
    "{who} levanta {box} al oído: parece que hay algo.",
)

#: Qué se dice al enseñar la pieza, según su rareza.
REVEAL_LINES: dict[Rarity, tuple[str, ...]] = {
    Rarity.COMMON: (
        "Bueno, es lo que hay: mucho cartón y poca sorpresa.",
        "Una pieza de las de siempre. Para hacer bulto en la vitrina.",
        "Ni fu ni fa.",
        "Sin pena ni gloria, pero ya la tienes.",
    ),
    Rarity.RARE: (
        "Esta ya se deja ver en la vitrina.",
        "Con esta ya hay algo que enseñar a las visitas.",
        "Una pieza con clase. Se nota que cuesta algo más que una tapa.",
    ),
    Rarity.EPIC: (
        "¡Épica! Aplauso con la mano abierta.",
        "Esto ya es de las que se comentan en el canal.",
        "Una pieza épica. Hasta el Cabildo lo notaría.",
    ),
    Rarity.LEGENDARY: (
        "¡LEGENDARIA! ¡Qué suerte!",
        "Legendaria. Las visitas se quitan el sombrero.",
        "Una pieza legendaria: solo un puñado de servidores la ha visto.",
    ),
    Rarity.MYTHIC: (
        "¡MÍTICA! ¡Toma mangó! Esto casi nunca pasa.",
        "¡Mítica! Hay que llamar a un notario para dar fe.",
        "¡Mítica! Ni en sueños.",
    ),
}


def shake_frame(who: str, box: Box, rng: random.Random) -> str:
    """Primer fotograma: se agita la caja."""
    return rng.choice(SHAKE_LINES).format(who=who, box=f"{box.emoji} **{box.name}**")


def glow_frame(who: str, box: Box, drop: Drop) -> str:
    """Segundo fotograma: el interior brilla del color de la rareza."""
    color = "🌈" if drop.skin is not None else drop.rarity.emoji
    return f"{box.emoji} {who} abre {box.name}… dentro brilla algo {color}"


def progress_text(collection: Collection, owned: int) -> str:
    """`Cromos de la Transición: 3/8`."""
    done = " ✅" if owned >= collection.size else ""
    return f"{collection.emoji} {collection.name}: **{owned}/{collection.size}**{done}"


def reveal_frame(
    who: str,
    box: Box,
    drop: Drop,
    *,
    dupe: bool,
    owned: int,
    rng: random.Random,
    price_text: str,
) -> str:
    """Último fotograma: lo que ha salido, con su rareza, su valor y la colección.

    Args:
        owned: Piezas distintas que tiene de su colección tras abrir.
        price_text: El valor de catálogo ya formateado (`1.500 Y$`).
    """
    head = f"{box.emoji} {who} abre {box.name}…"
    if drop.skin is not None and drop.skin.key == HIELO:
        body = (
            f"🧊 **¡HIELO NEGRO!** {drop.emoji} **{drop.name}** ({price_text}). Sale una vez "
            "de cada 50.000 cajas. Ponlo en cualquier mesa con fieltro desde la mochila."
        )
        return f"{head}\n{body}"
    if drop.skin is not None:
        body = (
            f"🌈 **¡UN TAPETE!** {drop.emoji} **{drop.name}** ({price_text}). Esto no "
            "pasa casi nunca. Ponlo en cualquier mesa con fieltro desde la mochila."
        )
        return f"{head}\n{body}"
    assert drop.piece is not None
    body = (
        f"{drop.rarity.emoji} **{drop.rarity.label}**: {drop.emoji} **{drop.name}** "
        f"({price_text}). {rng.choice(REVEAL_LINES[drop.rarity])}"
    )
    lines = [head, body]
    if drop.rerolled:
        lines.append("-# Te tocaba un tapete que ya tienes, así que he vuelto a sortear.")
    if dupe:
        lines.append("Repetida: te la puedes cambiar o vender en el Rastro.")
    lines.append(progress_text(COLLECTION_BY_KEY[drop.piece.collection], owned))
    return "\n".join(lines)


def free_box_line(box: Box, *, because: str, count: int = 1) -> str:
    """Línea de aviso de una caja ganada jugando (`because`: «tus 100 jugadas»)."""
    what = f"una **{box.name}**" if count == 1 else f"{count} **{box.name}**"
    look = "Mírala" if count == 1 else "Míralas"
    return f"-# {box.emoji} ¡Te llevas {what} gratis por {because}! {look} en tu mochila."
