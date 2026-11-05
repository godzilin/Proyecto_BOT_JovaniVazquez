"""Reglas del Rastro: ventas de segunda mano entre miembros, sin Discord ni base de datos.

Hay dos maneras de vender algo de la mochila (`vender`):

- **Venta directa:** a una persona y a un precio. Le llega una oferta con botones
  para aceptarla o rechazarla, que caduca a los `OFFER_SECONDS`.
- **Puesto en el Rastro:** con un precio de salida, para quien lo quiera. Mientras
  está en el puesto, el objeto lo guarda el Rastro (sale de la mochila y no se puede
  usar ni lucir). La primera puja tiene que llegar al precio de salida y abre la
  subasta: cada puja nueva sube al menos `min_raise` y vuelve a poner la cuenta
  atrás de «a la una, a las dos» (`AUCTION_SECONDS`). Si nadie puja más en ese
  tiempo, se adjudica. Un puesto sin pujas se cierra a los `LISTING_SECONDS` y el
  objeto vuelve a su dueño. Lo pujado (con su ITP) espera en el depósito del Rastro
  y se devuelve entero a quien es superado.

Qué se vende (`untradeable`): los coleccionables (`objeto`), títulos incluidos, y
los tapetes de las mesas con fieltro. No se venden las mascotas (tienen vínculo y
se adoptan), los roles ni los potenciadores, ni las skins que no son tapetes, ni
las que se ganan con un logro o son de todos (venderlas sería vender el logro), ni
las de imagen propia (llevan la foto de su dueño). La ropa de las mascotas se vende
como cualquier coleccionable, salvo la que regala un logro; las razas no, que van con
la mascota.

**Para no tener sin ganárselo lo que pide esfuerzo,** quien compra tiene que poder
comprarlo en la tienda: el nivel mínimo y el límite por persona del artículo se le
aplican igual (`ineligibility`). Regalar dinero ya se puede con el Bizum, así que lo
único que una venta podría saltarse son esos requisitos.

Fiscalidad (`bot.services.taxes`, «Rastro»):

- **ITP del 5,5 %** a cargo de quien compra, sobre el mayor entre el precio y el
  valor de referencia (`Deal.itp_base`). El valor de referencia es el precio de
  catálogo del artículo, sin IGIC ni rebaja: el equivalente de las tablas de precios
  medios con que Hacienda comprueba el valor de un coche usado. Vender barato no
  baja el impuesto.
- **La parte regalada** (lo que falta del precio al valor) está exenta de
  Donaciones, como el Bizum (decisión del proyecto).
- **Plusvalía:** si quien vende cobra más de lo que le costó (`cost`), paga el 19 %
  de la ganancia, que se le descuenta de lo que cobra. Lo que cuesta un objeto es lo
  pagado en la tienda sin IGIC (el IGIC no se recupera en una venta entre
  particulares), o, si se compró en el Rastro, el mayor entre precio y valor más el
  ITP pagado (arts. 35.1.b y 36 LIRPF: la parte regalada vale lo que valía).
  Lo que salió de la caja botín o fue un regalo de la tienda cuesta 0.

**Trueque** (`trueque`, `Swap`): dos miembros se cambian un objeto por otro, y quien
lo propone puede añadir dinero. Es una permuta, con su fiscalidad propia:

- **Cada uno paga el ITP de lo que recibe** (art. 23 del RDL 1/1993: «en las
  permutas tributará cada permutante por el valor comprobado de los que adquiera»),
  al 5,5 % y sobre su valor de referencia. Quien pone dinero, sobre el mayor entre
  ese valor y el dinero, como en una venta.
- **Cada uno tributa por la ganancia de lo que entrega** (art. 37.1.h LIRPF): el
  mayor entre el valor de lo que entrega y el de lo que recibe a cambio, menos lo que
  le costó. El dinero cuenta como parte de lo recibido para quien lo cobra y se
  descuenta de lo recibido para quien lo pone.
- Lo que cuesta lo recibido, para venderlo después, es la base de su ITP más el ITP.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from bot.services.pet_wardrobe import outfit_of
from bot.services.pets_looks import is_breed
from bot.services.shop import MAX_PRICE, Kind, parse_price
from bot.services.skins import GAMES, TAPETE, Skin, skin_of_item
from bot.services.taxes import capital_gain_tax, itp

#: Nombre del mercadillo, para los textos.
MARKET_NAME = "El Rastro"
#: Lo que dura una oferta de venta directa sin contestar.
OFFER_SECONDS = 600
#: Lo que dura un puesto sin ninguna puja.
LISTING_SECONDS = 7 * 86_400
#: Cuenta atrás desde la última puja hasta adjudicar («a la una, a las dos»).
AUCTION_SECONDS = 3_600
#: Lo mínimo que sube cada puja sobre la anterior: el mayor de los dos.
MIN_RAISE = 10
MIN_RAISE_PCT = 5
#: Puestos abiertos a la vez por persona, para que el tablón quepa.
MAX_LISTINGS = 5
#: Palabras para regalar en una venta directa (precio 0).
GIFT_WORDS = frozenset({"0", "gratis", "regalo", "nada"})


class MarketError(Exception):
    """Algo del Rastro que no se puede hacer; el mensaje se enseña tal cual."""


def is_tapete(skin: Skin) -> bool:
    """Si la skin es un tapete de las mesas con fieltro (de todas o de una sola)."""
    game = GAMES.get(skin.game)
    return skin.game == TAPETE or (game is not None and game.group == TAPETE)


def untradeable(kind: Kind, catalog_key: str | None) -> str | None:
    """Por qué no se puede vender un objeto de la mochila, o `None` si se puede."""
    if kind is not Kind.TROPHY:
        return "En el Rastro solo se venden coleccionables y tapetes."
    if is_breed(catalog_key):
        return "La raza va con la mascota: no se vende."
    outfit = outfit_of(catalog_key)
    if outfit is not None and outfit.achievement is not None:
        return "Esa prenda se gana, no se compra: venderla sería vender el logro."
    skin = skin_of_item(catalog_key)
    if skin is None:
        return None
    if not is_tapete(skin):
        return "De las skins, solo se venden los tapetes de las mesas con fieltro."
    if skin.achievement is not None or skin.free:
        return "Ese tapete se gana, no se compra: venderlo sería vender el logro."
    if skin.custom:
        return "Ese tapete lleva tu foto: no se vende, que luego sale en mesas ajenas."
    return None


@dataclass(frozen=True, slots=True)
class Deal:
    """Las cuentas de una venta del Rastro.

    Attributes:
        price: Precio pactado (o puja ganadora).
        reference: Valor de referencia del objeto (precio de catálogo).
        cost: Lo que le costó a quien vende, para la plusvalía.
    """

    price: int
    reference: int
    cost: int

    @property
    def itp_base(self) -> int:
        """Base del ITP: el mayor entre el precio y el valor de referencia."""
        return max(self.price, self.reference)

    @property
    def itp(self) -> int:
        """ITP que paga quien compra."""
        return itp(self.itp_base)

    @property
    def gain(self) -> int:
        """Ganancia de quien vende (0 si pierde o queda igual)."""
        return max(0, self.price - self.cost)

    @property
    def gain_tax(self) -> int:
        """IRPF de la plusvalía, que se le descuenta a quien vende."""
        return capital_gain_tax(self.gain)

    @property
    def gift(self) -> int:
        """Parte regalada: lo que falta del precio al valor (exenta)."""
        return max(0, self.reference - self.price)

    @property
    def buyer_pays(self) -> int:
        """Lo que sale del bolsillo de quien compra: precio más ITP."""
        return self.price + self.itp

    @property
    def seller_gets(self) -> int:
        """Lo que cobra quien vende: el precio menos su plusvalía."""
        return self.price - self.gain_tax

    @property
    def buyer_cost(self) -> int:
        """Lo que le cuesta el objeto a quien compra, para su próxima venta."""
        return self.itp_base + self.itp


@dataclass(frozen=True, slots=True)
class Swap:
    """Las cuentas de un trueque: quien propone da un objeto (y `money`) y recibe otro.

    Attributes:
        given_reference: Valor de referencia de lo que da quien propone.
        given_cost: Lo que le costó a quien propone.
        received_reference: Valor de referencia de lo que recibe a cambio.
        received_cost: Lo que le costó a la otra persona.
        money: Dinero que añade quien propone (0 si es un cambio sin más).
    """

    given_reference: int
    given_cost: int
    received_reference: int
    received_cost: int
    money: int = 0

    @property
    def proposer_itp_base(self) -> int:
        """Base del ITP de quien propone: lo que recibe, o el dinero si pone más."""
        return max(self.received_reference, self.money)

    @property
    def proposer_itp(self) -> int:
        """ITP de quien propone."""
        return itp(self.proposer_itp_base)

    @property
    def target_itp_base(self) -> int:
        """Base del ITP de quien acepta: el valor de lo que recibe."""
        return self.given_reference

    @property
    def target_itp(self) -> int:
        """ITP de quien acepta."""
        return itp(self.target_itp_base)

    @property
    def proposer_gain(self) -> int:
        """Ganancia de quien propone por lo que entrega (0 si no gana)."""
        received = max(0, self.received_reference - self.money)
        return max(0, max(self.given_reference, received) - self.given_cost)

    @property
    def target_gain(self) -> int:
        """Ganancia de quien acepta por lo que entrega, con el dinero que cobra."""
        received = self.given_reference + self.money
        return max(0, max(self.received_reference, received) - self.received_cost)

    @property
    def proposer_gain_tax(self) -> int:
        """IRPF de la ganancia de quien propone."""
        return capital_gain_tax(self.proposer_gain)

    @property
    def target_gain_tax(self) -> int:
        """IRPF de la ganancia de quien acepta."""
        return capital_gain_tax(self.target_gain)

    @property
    def proposer_pays(self) -> int:
        """Lo que sale del monedero de quien propone: dinero, ITP y su plusvalía."""
        return self.money + self.proposer_itp + self.proposer_gain_tax

    @property
    def target_pays(self) -> int:
        """Lo que sale (o, si es negativo, entra) en el monedero de quien acepta."""
        return self.target_itp + self.target_gain_tax - self.money

    @property
    def proposer_cost(self) -> int:
        """Lo que le cuesta a quien propone lo que recibe, para su próxima venta."""
        return self.proposer_itp_base + self.proposer_itp

    @property
    def target_cost(self) -> int:
        """Lo que le cuesta a quien acepta lo que recibe, para su próxima venta."""
        return self.target_itp_base + self.target_itp

    @property
    def taxes(self) -> int:
        """Todo lo que se lleva Hacienda en el trueque."""
        return self.proposer_itp + self.target_itp + self.proposer_gain_tax + self.target_gain_tax


def escrow(amount: int, reference: int) -> int:
    """Lo que retiene una puja de `amount`: la puja más el ITP que pagaría al ganar."""
    return amount + itp(max(amount, reference))


def min_raise(top: int) -> int:
    """Lo mínimo que hay que subir sobre la puja `top`."""
    return max(MIN_RAISE, math.ceil(top * MIN_RAISE_PCT / 100))


def min_bid(start: int, top: int | None) -> int:
    """Puja mínima: el precio de salida si no hay pujas; si no, la última más lo mínimo."""
    return start if top is None else top + min_raise(top)


def parse_sale_price(text: str, *, allow_gift: bool) -> int:
    """Precio de venta: `5000`, `5k`… y, si `allow_gift`, `0`/`gratis` para regalarlo.

    Raises:
        ValueError: Si no es un precio válido; el mensaje se puede enseñar.
    """
    if allow_gift and text.strip().lower() in GIFT_WORDS:
        return 0
    value = parse_price(text)
    if value > MAX_PRICE:
        raise ValueError("Ese precio no lo paga ni Florentino.")
    return value
