"""Los minijuegos de un turno en tu negocio: Comandas, La caja y Diagnóstico.

Lógica pura, sin Discord. Un turno es una cola de clientes (`make_clients`, sorteada con
la semilla del turno: así se sabe quién viene después y su imagen se pinta mientras
atiendes al de ahora). A cada cliente se le atiende con botones (`TurnGame.press`) y tiene
su paciencia: si se agota, se va (`TurnGame.expire`). El turno acaba al vaciarse la cola o
a los `TURN_SECONDS`. `TurnGame.summary` junta lo que ha pasado; el dinero lo mueve
`bot.services.negocio`.

Los relojes se paran mientras el jugador espera la pantalla nueva (`TurnGame.shown`): el
cliente y el cierre del turno no cuentan lo que tarda Discord en enseñarle a quién atiende.

Los tres juegos, que piden cosas distintas de los de la `pala` (allí la nota mueve un
sueldo; aquí la nota es el negocio):

- **Comandas** (cafetería): el cliente pide su mesa; se pulsan los productos (`p:<clave>`)
  y luego Servir (`serve`). El habitual con fidelidad pide «lo de siempre» sin decir qué.
- **La caja** (tienda): la cesta, el total y el billete; se da el cambio con monedas y
  billetes (`c:<importe>`) y se cobra (`charge`). A veces alguien esconde algo: `thief`
  lo pilla (y se cobra), pero acusar a quien no es lo echa del local.
- **Diagnóstico** (taller): el síntoma y cuatro averías posibles (`d:<clave>`, o `nopart`
  si no tienes el recambio); luego el presupuesto (`q:<0-2>`). Si el diagnóstico estaba
  mal, el coche vuelve y el recambio se pierde.

Desde la 9.48.0, uno por cada sector nuevo, y ninguno se parece a los demás:

- **La brasa** (guachinche): arriesgar. El plato se hace a la brasa con `fire:1` (un poco)
  o `fire:2` (avivar, más rápido y menos fino) hasta el punto que pide, y se saca con
  `serve`. Lo que sube cada vez no se sabe hasta pulsar; de `BURNT` en adelante, se quema.
- **Los encargos** (panadería): leer con cuidado. En la balda hay cuatro bolsas con nombres
  y contenidos parecidos; se da la del cliente (`b:<0-3>`) o se le dice que no hay ninguna
  a su nombre (`none`).
- **La balanza** (pescadería): sumar. Piden unos gramos de una especie y se ponen y quitan
  piezas de distinto peso (`w:<índice>`) hasta acercarse; luego se pesa (`weigh`).
- **La revisión** (móviles): mirar. El móvil puede tener hasta tres averías y el cliente
  solo cuenta una; se marcan las piezas que hay que cambiar (`m:<clave>`) y se repara
  (`fix`). Un protector rayado no es una pantalla rota.
- **La foto** (peluquería): razonar. El cliente trae una foto y un cambio («pero un poco más
  corto»); se elige el largo (`l:<largo>`), el degradado (`fade`), el color (`col:<color>`),
  la barba (`beard`) y el lavado (`wash`), y se termina (`done`). Cortar de más no tiene
  arreglo.

Todo el azar sale de semillas: el del turno sortea la cola y el de cada cliente decide si
acepta el presupuesto. Pintar por adelantado no cambia nada.
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field, replace

from bot.services.business import (
    LOYALTY_MAX,
    REGULAR_CHANCE,
    REPUTATION,
    USUAL_LOYALTY,
    Business,
    regular_names,
    sale_price,
    stars,
    usual_order,
)
from bot.services.business_catalog import (
    BAG_INITIALS,
    BAG_NAMES,
    BURNT,
    COLORS,
    FAULT_COUNTS,
    FAULTS,
    FISH_ASKS,
    FISH_GRAMS,
    GREETINGS,
    GRILLED,
    LENGTH_NAMES,
    LENGTHS,
    PHONE_FAULTS,
    POINT_BY_KEY,
    POINTS,
    PROTECTOR_CHANCE,
    STYLE_TWISTS,
    Product,
    Sector,
)

#: Lo que dura un turno como mucho (sin las esperas a la pantalla, `TurnGame.shown`).
TURN_SECONDS = 150
#: Lo más que para los relojes una sola espera a la pantalla (un mensaje que se atasca en
#: Discord no alarga el turno sin fin).
PAUSE_MAX = 5.0
#: Atender en menos de esta parte de la paciencia es rápido (propina); en más de
#: `SLOW_SHARE`, lento.
FAST_SHARE = 0.4
SLOW_SHARE = 0.75
#: Propina de una mesa o una compra atendida rápido, y lo que suma cada punto de fidelidad.
TIP_SHARE = {
    "comandas": 0.10, "caja": 0.05, "diagnostico": 0.0, "brasa": 0.10, "encargos": 0.03,
    "balanza": 0.03, "revision": 0.0, "foto": 0.08,
}  # fmt: skip
LOYALTY_TIP = 0.01
#: Lo que espera un cliente si vas reventado (`work.TIRED`): te ven arrastrarte.
TIRED_PATIENCE = 0.7
#: Monedas y billetes de la caja, de mayor a menor.
COINS: tuple[int, ...] = (1_000, 500, 200, 100, 50, 20, 10)
#: Billetes con los que se paga una compra.
BILLS: tuple[int, ...] = (500, 1_000, 5_000)
#: Probabilidad de que un cliente de la tienda (que no sea habitual) esconda algo.
THIEF_CHANCE = 0.08
#: Presupuestos del taller: `(clave, nombre, multiplicador del precio, aceptación)`.
QUOTES: tuple[tuple[str, str, float, float], ...] = (
    ("ajustado", "Ajustado", 0.85, 1.0),
    ("justo", "Justo", 1.0, 0.9),
    ("caro", "Caro", 1.3, 0.55),
)
#: Lo que sube la aceptación del presupuesto caro en un barrio de lujo.
LUXURY_QUOTE_BONUS = 0.15
#: Averías entre las que elegir en el diagnóstico.
OPTIONS = 4
#: Colores de los coches del taller.
CAR_COLORS: tuple[str, ...] = ("#c0392b", "#2c6fbb", "#f1c40f", "#ecf0f1", "#2d3436", "#27ae60")

#: La brasa: lo que sube cada pulsación de `fire:1` (un poco) y de `fire:2` (avivar).
HEAT_STEPS: dict[str, tuple[int, int]] = {"1": (6, 14), "2": (15, 30)}
#: Lo lejos del centro del punto (en puntos) que cuenta como clavarlo.
PERFECT_HEAT = 0
#: Los encargos: bolsas en la balda y probabilidad de que no haya ninguna a su nombre.
BAGS = 4
NO_BAG_CHANCE = 0.1
#: La balanza: piezas en el mostrador, lo que se aleja de lo pedido sin que proteste
#: (`WEIGHT_OK`), lo que cuenta como clavarlo (`WEIGHT_PERFECT`), lo que acepta de más
#: rezongando (`WEIGHT_HEAVY`) y lo que se tiene que poder hacer con las piezas (`WEIGHT_FAIR`).
PIECES = 7
WEIGHT_OK = 0.10
WEIGHT_PERFECT = 0.02
WEIGHT_HEAVY = 0.40
WEIGHT_FAIR = 0.05
#: Colores de los móviles (los dibuja la escena con su modelo, `phone`).
PHONE_COLORS: tuple[str, ...] = ("#1b1b22", "#e8e4dc", "#2c6fbb", "#c0392b", "#7a5aa8", "#9fd6a8")
#: Probabilidad de que un cliente de la peluquería tenga barba.
BEARD_CHANCE = 0.45

#: Nota de cada resultado (y, si se atendió bien, según la rapidez).
SCORES: dict[str, int] = {
    "short": 60,
    "over": 60,
    "missed_thief": 50,
    "rejected": 50,
    "no_part": 30,
    "wrong": 0,
    "bad_diagnosis": 0,
    # 9.48.0
    "raw": 30,
    "overdone": 60,
    "burnt": 0,
    "wrong_bag": 0,
    "missed_bag": 0,
    "honest": 100,
    "heavy": 60,
    "light": 50,
    "refused": 30,
    "missed": 50,
    "upsell": 20,
    "unhappy": 60,
    "mess": 30,
    "too_short": 0,
    "left": 0,
    "gone": 0,
    "false_accuse": 0,
}
SPEED_SCORES: dict[str, int] = {"fast": 100, "ok": 85, "slow": 70}
#: Notas del turno: `(mínimo de la media, letra)`.
GRADES: tuple[tuple[int, str], ...] = ((95, "S"), (80, "A"), (60, "B"), (40, "C"), (0, "D"))


@dataclass(slots=True)
class Client:
    """Un cliente de la cola.

    Attributes:
        seed: Su semilla (aspecto en la escena y si acepta el presupuesto).
        line: Lo que dice al llegar.
        regular: Su nombre, si es uno de tus habituales.
        usual: Si pide «lo de siempre» sin decir qué (habitual con fidelidad).
        order: Lo que pide (comandas) o lo que trae en la cesta a la vista (caja).
        hidden: Lo que esconde el listillo de la tienda (`None` si no esconde nada).
        paid: Billete con que paga en la tienda.
        fault: La avería del coche (clave del recambio).
        options: Las averías entre las que elegir.
        symptom: Lo que cuenta del coche.
        cue: La pista que se dibuja en el coche.
        car_color: Color del coche.
        car_model: Modelo del coche (0-3, lo dibuja la escena).
        dish: Lo que va a la brasa en el guachinche (`""` si la mesa no pide nada de brasa).
        point: Su punto (clave de `POINTS`).
        bags: Las bolsas de la balda de la panadería, en orden.
        bag: Cuál es la suya (`-1` si no hay ninguna a su nombre).
        label: A nombre de quién dice que está su encargo.
        clue: Lo que dice que lleva (para distinguir dos bolsas con el mismo nombre).
        species: Lo que pide en la pescadería.
        grams: Los gramos que pide.
        pieces: Lo que pesa cada pieza que hay en el mostrador.
        faults: Las averías del móvil (la primera es la que cuenta).
        protector: Si trae el protector de pantalla rayado (la pantalla no tiene nada).
        phone: Modelo del móvil (0-3) y su color.
        photo: El estilo de la foto que trae a la peluquería (`Style`).
        twist: Lo que le cambia a la foto (clave de `STYLE_TWISTS`; `""` si es habitual).
        target: Cómo lo quiere de verdad.
        hair: El largo que trae.
        beard: Si tiene barba.
    """

    seed: int
    line: str
    regular: str | None = None
    usual: bool = False
    order: dict[str, int] = field(default_factory=dict)
    hidden: str | None = None
    paid: int = 0
    fault: str = ""
    options: tuple[str, ...] = ()
    symptom: str = ""
    cue: str = ""
    car_color: str = ""
    car_model: int = 0
    dish: str = ""
    point: str = ""
    bags: tuple[Bag, ...] = ()
    bag: int = -1
    label: str = ""
    clue: str = ""
    species: str = ""
    grams: int = 0
    pieces: tuple[int, ...] = ()
    faults: tuple[str, ...] = ()
    protector: bool = False
    phone: int = 0
    phone_color: str = ""
    photo: Style | None = None
    twist: str = ""
    target: Style | None = None
    hair: str = ""
    beard: bool = False


@dataclass(frozen=True, slots=True)
class Bag:
    """Una bolsa de encargo de la balda: a nombre de quién y lo que lleva."""

    label: str
    order: dict[str, int]


@dataclass(frozen=True, slots=True)
class Style:
    """Un peinado: largo (`LENGTHS`), degradado, color (`COLORS`), barba y lavado."""

    length: str
    fade: bool = False
    color: str = "natural"
    beard: bool = False
    wash: bool = False

    def services(self) -> dict[str, int]:
        """Lo que se cobra por hacerlo (productos de la peluquería)."""
        out = {"corte": 1}
        if self.fade:
            out["degradado"] = 1
        if self.color != "natural":
            out[self.color] = 1
        if self.beard:
            out["barba"] = 1
        if self.wash:
            out["peinado"] = 1
        return out

    def differences(self, other: Style) -> int:
        """En cuántas cosas no coincide con `other`."""
        return sum(
            getattr(self, name) != getattr(other, name)
            for name in ("length", "fade", "color", "beard", "wash")
        )


@dataclass(frozen=True, slots=True)
class Outcome:
    """Lo que pasó con un cliente.

    Attributes:
        result: `ok`, `short` o `over` (cambio de menos o de más), `wrong`, `left` (se
            cansó de esperar), `gone` (se acabó el turno con él en la cola),
            `false_accuse`, `missed_thief`, `bad_diagnosis`, `rejected` o `no_part`.
        speed: `fast`, `ok` o `slow` si se le atendió; `""` si no.
        events: Lo que mueve la reputación (claves de `business.REPUTATION`).
        revenue: Lo cobrado, sin propina (con el cambio de más ya restado).
        tip: Propina.
        sold_cost: Coste al mayorista de lo vendido.
        waste_cost: Coste de lo que se perdió (lo servido mal, lo robado).
        units: Unidades que salieron del stock, por producto (vendidas o perdidas).
        quote: Presupuesto elegido en el taller.
        exact: Si se dio el cambio exacto.
        usual_hit: Si acertaste «lo de siempre» de un habitual.
        perfect: Si lo clavaste (9.48.0): el punto exacto de la brasa, el peso justo en la
            balanza, las tres averías de un móvil o la foto con su cambio.
        trap: Si esquivó la trampa del juego: el protector rayado sin cambiar la pantalla.
    """

    client: Client
    result: str
    speed: str = ""
    events: tuple[str, ...] = ()
    revenue: int = 0
    tip: int = 0
    sold_cost: int = 0
    waste_cost: int = 0
    units: dict[str, int] = field(default_factory=dict)
    quote: str = ""
    exact: bool = False
    usual_hit: bool = False
    perfect: bool = False
    trap: bool = False

    @property
    def served(self) -> bool:
        """Si se le cobró algo."""
        return self.revenue > 0

    @property
    def score(self) -> int:
        """Nota del cliente (0-100)."""
        if self.result == "ok":
            return SPEED_SCORES.get(self.speed, 85)
        return SCORES.get(self.result, 0)

    @property
    def reputation(self) -> float:
        """Lo que mueve la reputación."""
        return sum(REPUTATION.get(event, 0.0) for event in self.events)


def _pick(sector: Sector, stock: dict[str, int], rng: random.Random) -> Product | None:
    """Un producto al azar (por su peso) de los que quedan en `stock`."""
    left = [p for p in sector.products if stock.get(p.key, 0) > 0]
    if not left:
        return None
    return rng.choices(left, weights=[p.weight for p in left])[0]


def _basket(sector: Sector, stock: dict[str, int], rng: random.Random) -> dict[str, int]:
    """Lo que pide un cliente nuevo de lo que queda (y lo quita de `stock`)."""
    low, high = sector.order
    order: dict[str, int] = {}
    for _ in range(rng.randint(low, high)):
        product = _pick(sector, stock, rng)
        if product is None:
            break
        order[product.key] = order.get(product.key, 0) + 1
        stock[product.key] -= 1
    return order


def _fits(order: dict[str, int], stock: dict[str, int]) -> bool:
    return all(stock.get(key, 0) >= n for key, n in order.items())


def _paid(total: int, rng: random.Random) -> int:
    """Con qué billete paga una compra de `total`."""
    roll = rng.random()
    bill = BILLS[0] if roll < 0.5 else BILLS[1] if roll < 0.85 else BILLS[2]
    if total >= BILLS[2]:
        bill = BILLS[1]
    return -(-total // bill) * bill if total % bill else total + bill


def usual_point(seed: int, regular: str) -> str:
    """El punto de siempre de un habitual del guachinche."""
    return random.Random(f"{seed}:{regular}:punto").choice(POINTS)[0]


def usual_style(seed: int, regular: str) -> Style:
    """El corte de siempre de un habitual de la peluquería."""
    rng = random.Random(f"{seed}:{regular}:corte")
    return Style(
        length=rng.choice(LENGTHS),
        fade=rng.random() < 0.5,
        color=rng.choices(COLORS, weights=(6, 2, 2))[0],
        beard=rng.random() < BEARD_CHANCE,
        wash=rng.random() < 0.3,
    )


def point_text(dish: str, point: str) -> str:
    """Cómo se pide el punto de `dish` («poco hecha», «en su punto»…)."""
    return POINT_BY_KEY[point][1].format(g=GRILLED.get(dish, "o"))


def twist_text(twist: str) -> str:
    """Lo que dice el cliente de la peluquería de su foto (`STYLE_TWISTS`)."""
    return next((text for key, text in STYLE_TWISTS if key == twist), "")


def restyle(photo: Style, twist: str) -> Style:
    """Cómo lo quiere el cliente: la foto con su cambio (`STYLE_TWISTS`)."""
    if twist == "corto":
        return replace(photo, length=LENGTHS[max(0, LENGTHS.index(photo.length) - 1)])
    if twist in ("sin_degradado", "degradado"):
        return replace(photo, fade=twist == "degradado")
    if twist in ("natural", "mechas"):
        return replace(photo, color=twist)
    if twist == "barba":
        return replace(photo, beard=True)
    if twist == "peinado":
        return replace(photo, wash=True)
    return photo


def twists_for(photo: Style, beard: bool) -> list[str]:
    """Los cambios que tienen sentido con esa foto (y esa barba)."""
    out = ["igual", "peinado"]
    if photo.length != LENGTHS[0]:
        out.append("corto")
    out.append("sin_degradado" if photo.fade else "degradado")
    if photo.color != "natural":
        out.append("natural")
    if photo.color != "mechas":
        out.append("mechas")
    if beard:
        out.append("barba")
    return out


def best_weight(pieces: tuple[int, ...], grams: int) -> tuple[int, ...]:
    """Las piezas (índices) que más se acercan a `grams`, con las menos posibles."""
    best: tuple[int, ...] = ()
    best_key = (grams, 0)
    for mask in range(1, 1 << len(pieces)):
        chosen = tuple(i for i in range(len(pieces)) if mask >> i & 1)
        key = (abs(sum(pieces[i] for i in chosen) - grams), len(chosen))
        if key < best_key:
            best, best_key = chosen, key
    return best


def _weighted(products: list[Product], rng: random.Random) -> Product:
    return rng.choices(products, weights=[p.weight for p in products])[0]


def _in_stock(sector: Sector, stock: dict[str, int], keys: object = None) -> list[Product]:
    return [
        p for p in sector.products if stock.get(p.key, 0) > 0 and (keys is None or p.key in keys)
    ]  # type: ignore[operator]


def _take_local(stock: dict[str, int], order: dict[str, int]) -> None:
    for key, n in order.items():
        stock[key] = stock.get(key, 0) - n


def _grill_order(
    sector: Sector, stock: dict[str, int], rng: random.Random
) -> tuple[dict[str, int], str]:
    """La mesa del guachinche: un plato de brasa (si queda) y lo demás, de lo que queda."""
    low, high = sector.order
    order: dict[str, int] = {}
    dish = ""
    grill = _in_stock(sector, stock, GRILLED)
    if grill:
        dish = _weighted(grill, rng).key
        order[dish] = 1
        stock[dish] -= 1
    for _ in range(rng.randint(low, high) - len(order)):
        rest = [p for p in _in_stock(sector, stock) if p.key not in GRILLED]
        if not rest:
            break
        key = _weighted(rest, rng).key
        order[key] = order.get(key, 0) + 1
        stock[key] -= 1
    return order, dish


def _bags(
    sector: Sector,
    order: dict[str, int],
    name: str,
    initial: str,
    rng: random.Random,
    *,
    here: bool,
    usual: bool,
) -> tuple[tuple[Bag, ...], int, str]:
    """La balda de la panadería: la bolsa del cliente (si está) y las que se le parecen.

    Una lleva el mismo nombre y otra inicial; otra, el mismo nombre y la misma inicial pero
    sin lo que dice que lleva (`clue`), y la última es de otra persona. Si es «lo de siempre»
    no dice ni su inicial ni lo que lleva: hay que acordarse. Devuelve las bolsas, cuál es la
    suya (`-1` si no está) y lo que dice que lleva.
    """
    keys = [p.key for p in sector.products]
    clue = rng.choice(sorted(order))
    others = [k for k in keys if k not in order]
    swapped = dict(order)
    del swapped[clue]
    swapped[rng.choice(others) if others else rng.choice(keys)] = rng.randint(1, 2)
    if swapped == order:
        swapped[clue] = order[clue] + 1
    label = f"{name} {initial}."
    other_initial = rng.choice([c for c in BAG_INITIALS if c != initial])
    other_name = rng.choice([n for n in BAG_NAMES if n != name])

    def random_order() -> dict[str, int]:
        picks = rng.sample(keys, rng.randint(2, 3))
        return {k: rng.randint(1, 3) for k in picks}

    bags = [Bag(label, dict(order)), Bag(label, swapped)]
    if usual:
        bags.append(Bag(f"{other_name} {other_initial}.", dict(order)))
    else:
        bags.append(Bag(f"{name} {other_initial}.", dict(order)))
    bags.append(Bag(f"{other_name} {initial}.", random_order()))
    if not here:
        bags[0] = Bag(f"{rng.choice(BAG_NAMES)} {rng.choice(BAG_INITIALS)}.", random_order())
    rng.shuffle(bags)
    mine = next((i for i, b in enumerate(bags) if b.label == label and b.order == order), -1)
    return tuple(bags), mine if here else -1, clue


def _fish(
    sector: Sector, stock: dict[str, int], rng: random.Random, usual: dict[str, int]
) -> tuple[str, int, tuple[int, ...]] | None:
    """El pedido de la pescadería: la especie, los gramos y las piezas del mostrador.

    Siempre se puede servir: alguna combinación de piezas queda a menos de `WEIGHT_FAIR` de
    lo pedido. Lo de siempre de un habitual va si queda y se puede; si no, pide otra cosa.
    """
    left = _in_stock(sector, stock)
    if not left:
        return None
    want = next(iter(usual.items()), None)
    for attempt in range(40):
        if want and attempt < 10 and stock.get(want[0], 0) > 0:
            species, asks = want[0], (want[1] * 250,)
        else:
            species = _weighted(left, rng).key
            asks = FISH_ASKS[species]
        low, high, _avg = FISH_GRAMS[species]
        count = min(PIECES, stock[species])
        pieces = tuple(int(round(rng.uniform(low, high), -1)) for _ in range(count))
        fair = [
            a for a in asks
            if abs(sum(pieces[i] for i in best_weight(pieces, a)) - a) <= a * WEIGHT_FAIR
        ]  # fmt: skip
        if fair:
            grams = rng.choice(fair)
            stock[species] -= len(best_weight(pieces, grams))
            return species, grams, pieces
    return None


def _phone_faults(
    sector: Sector, stock: dict[str, int], rng: random.Random, usual: dict[str, int]
) -> tuple[str, ...]:
    """Las averías de un móvil, de las piezas que quedan (la de siempre, la primera)."""
    left = _in_stock(sector, stock)
    if not left:
        return ()
    count = min(len(left), rng.choices((1, 2, 3), weights=FAULT_COUNTS)[0])
    faults: list[str] = []
    mine = next(iter(usual), None)
    if mine is not None and stock.get(mine, 0) > 0:
        faults.append(mine)
    while len(faults) < count:
        key = _weighted([p for p in left if p.key not in faults], rng).key
        faults.append(key)
    for key in faults:
        stock[key] -= 1
    return tuple(faults)


def _style_client(
    sector: Sector,
    stock: dict[str, int],
    rng: random.Random,
    usual: Style | None,
) -> tuple[Style, str, Style, str, bool] | None:
    """La foto, su cambio, lo que quiere de verdad, el largo que trae y si tiene barba.

    Lo que no queda en el stock no se pide (sin tinte no hay foto con tinte).
    """
    if stock.get("corte", 0) <= 0:
        return None

    def can(style: Style) -> bool:
        return all(stock.get(k, 0) >= n for k, n in style.services().items())

    if usual is not None and can(usual):
        target, beard, photo, twist = usual, usual.beard, usual, ""
    else:
        beard = rng.random() < BEARD_CHANCE
        for _ in range(30):
            photo = Style(
                length=rng.choice(LENGTHS),
                fade=rng.random() < 0.5,
                color=rng.choices(COLORS, weights=(5, 3, 2))[0],
            )
            twist = rng.choice(twists_for(photo, beard))
            target = restyle(photo, twist)
            if can(target):
                break
        else:
            photo = target = Style(length=rng.choice(LENGTHS))
            twist = "igual"
    longest = max(LENGTHS.index(photo.length), LENGTHS.index(target.length))
    hair = LENGTHS[min(len(LENGTHS) - 1, longest + rng.randint(0, 1))]
    _take_local(stock, target.services())
    return photo, twist, target, hair, beard


def make_clients(
    business: Business, count: int, seed: int, *, thieves: bool = True
) -> list[Client]:
    """La cola de un turno: `count` clientes como mucho, con lo que queda en el stock.

    En la cafetería y la tienda cada uno pide de lo que queda (si ya no queda nada, no
    viene nadie más). En el taller la avería no mira tu stock: si no tienes el recambio,
    lo pierdes. Los habituales piden lo de siempre si está. Los pedidos a domicilio del
    repartidor (`thieves=False`) no esconden nada: no hay bolsillo que mirar.

    En los sectores de la 9.48.0 todo sale de lo que queda: el plato de brasa, la bolsa del
    encargo, las piezas del mostrador, las piezas del móvil y lo que lleva el peinado.
    """
    sector = business.sector_info
    game = sector.game
    rng = random.Random(seed)
    stock = dict(business.stock)
    regulars = list(regular_names(business.seed))
    rng.shuffle(regulars)
    clients: list[Client] = []
    for _ in range(count):
        client = Client(seed=rng.randrange(2**31), line=rng.choice(GREETINGS[game]))
        regular = regulars.pop() if regulars and rng.random() < REGULAR_CHANCE else None
        loyalty = business.regulars.get(regular, 0) if regular else 0
        usual = usual_order(business.seed, regular, sector) if regular else {}
        knows = bool(regular) and loyalty >= USUAL_LOYALTY
        client.regular = regular
        if game == "diagnostico":
            fault = (
                next(iter(usual))
                if usual
                else rng.choices(sector.products, weights=[p.weight for p in sector.products])[
                    0
                ].key
            )
            others = [p.key for p in sector.products if p.key != fault]
            options = [fault, *rng.sample(others, OPTIONS - 1)]
            rng.shuffle(options)
            texts, cue = FAULTS[fault]
            client.fault, client.options = fault, tuple(options)
            client.symptom, client.cue = rng.choice(texts), cue
            client.car_color, client.car_model = rng.choice(CAR_COLORS), rng.randrange(4)
            client.usual = knows
            clients.append(client)
            continue
        if game == "balanza":
            fish = _fish(sector, stock, rng, usual)
            if fish is None:
                break
            client.species, client.grams, client.pieces = fish
            client.usual = knows and bool(usual) and next(iter(usual)) == client.species
            clients.append(client)
            continue
        if game == "revision":
            faults = _phone_faults(sector, stock, rng, usual)
            if not faults:
                break
            client.faults = faults
            client.order = {k: 1 for k in faults}
            client.protector = "pantalla" not in faults and rng.random() < PROTECTOR_CHANCE
            client.symptom = rng.choice(PHONE_FAULTS[faults[0]])
            client.phone, client.phone_color = rng.randrange(4), rng.choice(PHONE_COLORS)
            client.usual = knows and bool(usual) and next(iter(usual)) in faults
            clients.append(client)
            continue
        if game == "foto":
            mine = usual_style(business.seed, regular) if knows and regular else None
            styled = _style_client(sector, stock, rng, mine)
            if styled is None:
                break
            client.photo, client.twist, client.target, client.hair, client.beard = styled
            client.order = client.target.services()
            client.usual = mine is not None and client.twist == ""
            clients.append(client)
            continue
        if game == "brasa":
            if usual and _fits(usual, stock):
                order, dish = dict(usual), next((k for k in usual if k in GRILLED), "")
                _take_local(stock, usual)
                client.usual = knows
            else:
                order, dish = _grill_order(sector, stock, rng)
            if not order:
                break
            client.order, client.dish = order, dish
            if dish:
                client.point = (
                    usual_point(business.seed, regular)
                    if client.usual and regular
                    else rng.choice(POINTS)[0]
                )
            clients.append(client)
            continue
        if usual and _fits(usual, stock):
            order = usual
            for key, n in usual.items():
                stock[key] -= n
            client.usual = knows
        else:
            order = _basket(sector, stock, rng)
        if not order:
            break
        client.order = order
        if game == "encargos":
            name = regular or rng.choice(BAG_NAMES)
            initial = (
                random.Random(f"{business.seed}:{regular}").choice(BAG_INITIALS)
                if regular
                else rng.choice(BAG_INITIALS)
            )
            here = rng.random() >= NO_BAG_CHANCE
            client.bags, client.bag, client.clue = _bags(
                sector, order, name, initial, rng, here=here, usual=client.usual
            )
            client.label = f"{name} {initial}."
            if not here:  # lo que iba a llevarse vuelve al stock: no hay bolsa
                _take_local(stock, {k: -n for k, n in order.items()})
        if game == "caja":
            if thieves and regular is None and rng.random() < THIEF_CHANCE:
                hidden = _pick(sector, stock, rng)
                if hidden is not None:
                    client.hidden = hidden.key
                    stock[hidden.key] -= 1
            total = sum(
                sale_price(sector.product(k), business.level(k).key) * n  # type: ignore[arg-type]
                for k, n in order.items()
            )
            client.paid = _paid(total, rng)
        clients.append(client)
    return clients


class TurnGame:
    """Un turno en marcha.

    Args:
        business: El negocio (se lee su sector, sus precios, su stock y sus habituales;
            no se cambia: el stock que se gasta va en `stock`).
        clients: La cola (`make_clients`).
        now: Cuándo empieza.
        tired: Si el dueño va reventado (los clientes esperan menos).
    """

    def __init__(
        self, business: Business, clients: list[Client], *, now: float, tired: bool = False
    ) -> None:
        self.sector = business.sector_info
        #: Nivel de precios de cada producto (el general o el suyo, `Business.prices`).
        self.levels = {p.key: business.level(p.key).key for p in business.sector_info.products}
        self.luxury = business.barrio_info.theme == "lujo"
        self.stars = stars(business.reputation)
        self.loyalty = dict(business.regulars)
        self.clients = clients
        self.stock = dict(business.stock)
        self.started = now
        self.deadline = now + TURN_SECONDS
        installed = business.installed
        # Las mejoras y el local que dejó limpio anoche la limpieza (`Business.support`).
        self.patience = (
            self.sector.patience
            * (TIRED_PATIENCE if tired else 1.0)
            * (1 + sum(e.patience for e in installed) + business.support.get("patience", 0.0))
        )
        #: Lo que suman las mejoras a la propina de quien se atiende rápido.
        self.tip_bonus = sum(e.tips for e in installed)
        self.index = 0
        #: Cambia con cada cliente y cada paso del taller: los clics de antes no cuentan.
        self.round = 0
        #: Cuándo empezó la ronda de ahora y la última ronda que ya paró los relojes.
        self.round_since = now
        self.paused_round = -1
        self.since = now
        self.outcomes: list[Outcome] = []
        #: Lo que ha pasado con la última pulsación, para enseñarlo.
        self.last = ""
        self._fresh()

    def _fresh(self) -> None:
        """Deja la mesa limpia para el cliente de ahora (lo de cada juego, a cero)."""
        self.tray: list[str] = []
        self.given: list[int] = []
        self.accused = False
        self.step = "diagnostico"
        self.diagnosis: str | None = None
        client = self.current
        self.paid = client.paid if client else 0
        self.extra = 0
        #: La brasa: lo hecho que va (0-100) y cuántas veces se ha avivado.
        self.heat = 0
        self.flips = 0
        #: La balanza: las piezas (índices) que hay en el plato.
        self.scale: list[int] = []
        #: La revisión: las piezas marcadas para cambiar.
        self.marked: list[str] = []
        #: La foto: lo elegido hasta ahora (`length` vacío hasta que se elige).
        self.style = Style(length="")

    def ahead(self) -> TurnGame:
        """Una copia ligera con el siguiente cliente delante (para pintarlo antes)."""
        nxt = copy.copy(self)
        nxt.index = self.index + 1
        nxt._fresh()
        return nxt

    # -- Lo que se ve -------------------------------------------------------------------

    @property
    def current(self) -> Client | None:
        """El cliente al que se atiende, o `None` si no queda nadie."""
        return self.clients[self.index] if self.index < len(self.clients) else None

    @property
    def client_deadline(self) -> float:
        """Cuándo se cansa el cliente de ahora."""
        return min(self.deadline, self.since + self.patience)

    @property
    def queue(self) -> int:
        """Clientes esperando detrás del de ahora."""
        return max(0, len(self.clients) - self.index - 1)

    def price_of(self, key: str) -> int:
        """Precio de venta de una unidad."""
        product = self.sector.product(key)
        assert product is not None
        return sale_price(product, self.levels[key])

    def total(self) -> int:
        """Lo que cuesta la cesta del cliente de ahora (con lo pillado al listillo)."""
        client = self.current
        if client is None:
            return 0
        return sum(self.price_of(k) * n for k, n in client.order.items()) + self.extra

    def due(self) -> int:
        """El cambio que hay que darle al cliente de ahora."""
        return max(0, self.paid - self.total())

    def tray_counts(self) -> dict[str, int]:
        """Lo que hay en la bandeja, por producto."""
        counts: dict[str, int] = {}
        for key in self.tray:
            counts[key] = counts.get(key, 0) + 1
        return counts

    def quote_amount(self, index: int) -> int:
        """El presupuesto `index` del cliente de ahora (con la avería que diagnosticaste)."""
        key = self.diagnosis or (self.current.fault if self.current else "")
        return int(round(self.price_of(key) * QUOTES[index][2], -1))

    def weighed(self) -> int:
        """Los gramos que hay en la balanza."""
        client = self.current
        return sum(client.pieces[i] for i in self.scale) if client else 0

    def weight_price(self, species: str, grams: int) -> int:
        """Lo que cuestan `grams` de `species` (el precio es de una pieza media)."""
        return int(round(self.price_of(species) * grams / FISH_GRAMS[species][2], -1))

    def available(self, key: str) -> int:
        """Unidades de `key` que quedan, sin lo que ya hay en la bandeja."""
        return self.stock.get(key, 0) - self.tray.count(key)

    # -- Jugar --------------------------------------------------------------------------

    def finished(self, now: float) -> bool:
        """Si el turno ha acabado (sin cola o sin tiempo)."""
        return self.current is None or now >= self.deadline

    def shown(self, round_index: int, at: float) -> float:
        """La pantalla de la ronda `round_index` llegó al jugador en `at`: para los relojes.

        Desde que empieza una ronda (otro cliente o el presupuesto del taller) hasta que el
        jugador la ve, sus botones aún no están: ese rato, hasta `PAUSE_MAX`, se suma a la
        paciencia del cliente y al cierre del turno. Cada ronda para los relojes una vez;
        si ya hay otra ronda, no hace nada. Devuelve los segundos que los ha parado.
        """
        if self.current is None or round_index != self.round or self.paused_round == self.round:
            return 0.0
        self.paused_round = self.round
        lost = max(0.0, min(PAUSE_MAX, at - self.round_since))
        self.since += lost
        self.deadline += lost
        return lost

    def expire(self, now: float) -> bool:
        """Despide al cliente de ahora si se le acabó la paciencia. `True` si se fue."""
        client = self.current
        if client is None or now < self.client_deadline:
            return False
        if now >= self.deadline:
            self.finish(now)
            return True
        self.last = f"⌛ {self._who(client)} se cansó de esperar y se fue."
        self._close(Outcome(client, "left", events=("left",)), now)
        return True

    def finish(self, now: float) -> None:
        """Cierra el turno: el de ahora y los de la cola se van sin que se les atienda."""
        client = self.current
        if client is not None:
            self._close(Outcome(client, "left", events=("left",)), now)
        while (client := self.current) is not None:
            self._close(Outcome(client, "gone", events=()), now)

    def press(self, action: str, now: float) -> None:
        """Una pulsación. Lo que pasa queda en `last`; si cierra al cliente, pasa al siguiente."""
        if self.expire(now) or self.current is None:
            return
        game = self.sector.game
        verb, _, arg = action.partition(":")
        handler = {
            "comandas": self._comandas,
            "caja": self._caja,
            "diagnostico": self._diagnostico,
            "brasa": self._brasa,
            "encargos": self._encargos,
            "balanza": self._balanza,
            "revision": self._revision,
            "foto": self._foto,
        }[game]
        handler(verb, arg, now)

    def _who(self, client: Client) -> str:
        return client.regular or "El cliente"

    def _speed(self, now: float) -> str:
        share = (now - self.since) / self.patience if self.patience else 1.0
        return "fast" if share < FAST_SHARE else "slow" if share > SLOW_SHARE else "ok"

    def _cost(self, units: dict[str, int]) -> int:
        return sum(self.sector.product(k).cost * n for k, n in units.items())  # type: ignore[union-attr]

    def _take(self, units: dict[str, int]) -> None:
        for key, n in units.items():
            self.stock[key] = max(0, self.stock.get(key, 0) - n)

    def _tip(self, client: Client, revenue: int, speed: str) -> int:
        if speed != "fast":
            return 0
        loyalty = self.loyalty.get(client.regular, 0) if client.regular else 0
        share = TIP_SHARE[self.sector.game] + self.tip_bonus + LOYALTY_TIP * loyalty
        return round(revenue * share)

    def _comandas(self, verb: str, arg: str, now: float) -> None:
        client = self.current
        assert client is not None
        if verb == "p":
            product = self.sector.product(arg)
            if product is None:
                return
            if self.available(arg) <= 0:
                self.last = f"🚫 No te queda {product.name.lower()}."
                return
            self.tray.append(arg)
            self.last = ""
        elif verb == "undo":
            if self.tray:
                self.tray.pop()
        elif verb == "serve":
            tray = self.tray_counts()
            if not tray:
                self.last = "La bandeja está vacía: primero pon lo que pide."
                return
            self._take(tray)
            if tray == client.order:
                speed = self._speed(now)
                revenue = sum(self.price_of(k) * n for k, n in tray.items())
                tip = self._tip(client, revenue, speed)
                self.last = f"✅ Mesa servida: +{revenue} Y$" + (
                    f" y {tip} de propina" if tip else ""
                )
                self._close(
                    Outcome(
                        client, "ok", speed, (speed,), revenue, tip,
                        sold_cost=self._cost(tray), units=tray, usual_hit=client.usual,
                    ),
                    now,
                )  # fmt: skip
            else:
                self.last = (
                    f"❌ {self._who(client)}: «¡Esto no es lo que he pedido!» Se va sin pagar."
                )
                self._close(
                    Outcome(
                        client, "wrong", events=("wrong",), waste_cost=self._cost(tray), units=tray
                    ),
                    now,
                )

    def _caja(self, verb: str, arg: str, now: float) -> None:
        client = self.current
        assert client is not None
        if verb == "c" and arg.isdigit() and int(arg) in COINS:
            if sum(self.given) + int(arg) > self.paid:
                self.last = "No puedes devolver más de lo que te han dado."
                return
            self.given.append(int(arg))
            self.last = ""
        elif verb == "undo":
            if self.given:
                self.given.pop()
        elif verb == "thief":
            if self.accused:
                return
            self.accused = True
            if client.hidden is None:
                self.last = (
                    f"😡 {self._who(client)}: «¿Me está llamando ladrón?» Se va y no vuelve."
                )
                self._close(Outcome(client, "false_accuse", events=("false_accuse",)), now)
                return
            price = self.price_of(client.hidden)
            self.extra += price
            while self.paid < self.total():
                self.paid += BILLS[1]
            product = self.sector.product(client.hidden)
            assert product is not None
            self.last = f"👀 ¡Pillado! Llevaba {product.name.lower()} escondido: lo paga."
        elif verb == "charge":
            due, change = self.due(), sum(self.given)
            units = dict(client.order)
            caught = self.accused and client.hidden is not None
            if caught:
                units[client.hidden] = units.get(client.hidden, 0) + 1  # type: ignore[index]
            self._take(units)
            revenue = self.total() - max(0, change - due)
            speed = self._speed(now)
            events: list[str] = []
            if change == due:
                result = "ok"
                events.append(speed)
                self.last = f"✅ Cobrado: +{revenue} Y$, cambio exacto."
            elif change < due:
                result = "short"
                events.append("short")
                self.last = f"😤 «¡Me falta cambio!» Le das los {due - change} Y$ que faltaban."
            else:
                result = "over"
                events.append("over")
                self.last = f"💸 Le has dado {change - due} Y$ de más. Se va encantado."
            waste = 0
            if caught:
                events.append("caught")
            elif client.hidden is not None:
                result = "missed_thief" if result == "ok" else result
                events.append("missed_thief")
                self._take({client.hidden: 1})
                waste = self._cost({client.hidden: 1})
                self.last += " Al irse ves que llevaba algo en el bolsillo…"
            tip = self._tip(client, revenue, speed) if result == "ok" else 0
            if tip:
                self.last += f" Te deja {tip} Y$ de propina."
            self._close(
                Outcome(
                    client, result, speed, tuple(events), revenue, tip,
                    sold_cost=self._cost(units), waste_cost=waste, units=units,
                    exact=change == due, usual_hit=client.usual,
                ),
                now,
            )  # fmt: skip

    def _diagnostico(self, verb: str, arg: str, now: float) -> None:
        client = self.current
        assert client is not None
        if self.step == "diagnostico":
            if verb == "nopart":
                self.last = f"🚫 «Pues me lo llevo a otro taller.» {self._who(client)} se va."
                self._close(Outcome(client, "no_part", events=("no_part",)), now)
            elif verb == "d" and arg in client.options:
                if self.stock.get(arg, 0) <= 0:
                    product = self.sector.product(arg)
                    name = product.name.lower() if product else arg
                    self.last = f"🚫 No te queda {name}: pide más o di que no tienes la pieza."
                    return
                self.diagnosis, self.step = arg, "presupuesto"
                self.round += 1
                self.round_since = now
                self.last = ""
            return
        if verb != "q" or not arg.isdigit() or not 0 <= int(arg) < len(QUOTES):
            return
        key, name, _mult, accept = QUOTES[int(arg)]
        if key == "caro" and self.luxury:
            accept += LUXURY_QUOTE_BONUS
        accept = max(0.0, min(1.0, accept + 0.05 * (self.stars - 3)))
        amount = self.quote_amount(int(arg))
        if random.Random(client.seed).random() >= accept:
            self.last = f"🙅 «¿{amount} Y$? Ni loco.» {self._who(client)} se lleva el coche."
            self._close(Outcome(client, "rejected", events=("rejected",), quote=key), now)
            return
        assert self.diagnosis is not None
        part = {self.diagnosis: 1}
        self._take(part)
        if self.diagnosis != client.fault:
            self.last = (
                f"🔁 Presupuesto {name.lower()} aceptado… y a los dos días vuelve con lo mismo."
            )
            self._close(
                Outcome(
                    client, "bad_diagnosis", events=("bad_diagnosis",),
                    waste_cost=self._cost(part), units=part, quote=key,
                ),
                now,
            )  # fmt: skip
            return
        speed = self._speed(now)
        events = (speed, "cheap_quote") if key == "ajustado" else (speed,)
        self.last = f"🔧 Arreglado: +{amount} Y$ (presupuesto {name.lower()})."
        self._close(
            Outcome(
                client, "ok", speed, events, amount, sold_cost=self._cost(part),
                units=part, quote=key, usual_hit=client.usual,
            ),
            now,
        )  # fmt: skip

    # -- 9.48.0 ------------------------------------------------------------------------

    def _ok(
        self, client: Client, now: float, units: dict[str, int], revenue: int, **kw: object
    ) -> None:
        """Cierra al cliente bien atendido: cobra, propina por la rapidez y fuera stock."""
        self._take(units)
        speed = self._speed(now)
        tip = self._tip(client, revenue, speed)
        extra = tuple(kw.pop("events", ()))  # type: ignore[arg-type]
        self._close(
            Outcome(
                client, "ok", speed, (speed, *extra), revenue, tip,
                sold_cost=self._cost(units), units=units, usual_hit=client.usual,
                **kw,  # type: ignore[arg-type]
            ),
            now,
        )  # fmt: skip
        if tip:
            self.last += f" Te deja {tip} Y$ de propina."

    def _sum(self, units: dict[str, int]) -> int:
        return sum(self.price_of(k) * n for k, n in units.items())

    def _brasa(self, verb: str, arg: str, now: float) -> None:
        client = self.current
        assert client is not None
        if verb == "fire" and arg in HEAT_STEPS:
            if not client.dish:
                self.last = "Esta mesa no pide nada de brasa: sírvela."
                return
            low, high = HEAT_STEPS[arg]
            self.heat += random.Random(f"{client.seed}:{self.flips}").randint(low, high)
            self.flips += 1
            self.last = ""
            if self.heat >= BURNT:
                rest = {k: n for k, n in client.order.items() if k != client.dish}
                lost = {client.dish: 1}
                self._take({**rest, **lost})
                self.last = "🔥 Se te ha quemado. La mesa se come lo demás y se va con hambre."
                self._close(
                    Outcome(
                        client, "burnt", events=("burnt",), revenue=self._sum(rest),
                        sold_cost=self._cost(rest), waste_cost=self._cost(lost),
                        units={**rest, **lost},
                    ),
                    now,
                )  # fmt: skip
            return
        if verb != "serve":
            return
        units = dict(client.order)
        if not client.dish:
            self.last = f"✅ Mesa servida: +{self._sum(units)} Y$."
            self._ok(client, now, units, self._sum(units))
            return
        _key, _ask, low, high = POINT_BY_KEY[client.point]
        name = (client.dish and self.sector.product(client.dish).name.lower()) or ""  # type: ignore[union-attr]
        if self.heat < low:
            rest = {k: n for k, n in units.items() if k != client.dish}
            lost = {client.dish: 1}
            self._take(units)
            self.last = f"🩸 «¡Esto está crudo!» Te devuelven {name} y pagan solo lo demás."
            self._close(
                Outcome(
                    client, "raw", events=("raw",), revenue=self._sum(rest),
                    sold_cost=self._cost(rest), waste_cost=self._cost(lost), units=units,
                ),
                now,
            )  # fmt: skip
        elif self.heat > high:
            self._take(units)
            revenue = self._sum(units)
            self.last = f"😬 «Un poco pasado, ¿no?» Se lo comen y pagan {revenue} Y$, sin propina."
            self._close(
                Outcome(
                    client, "overdone", self._speed(now), ("overdone",), revenue,
                    sold_cost=self._cost(units), units=units,
                ),
                now,
            )  # fmt: skip
        else:
            middle = (low + high) / 2
            perfect = abs(self.heat - middle) <= PERFECT_HEAT
            revenue = self._sum(units)
            self.last = f"✅ {name.capitalize()} en su punto: +{revenue} Y$."
            if perfect:
                self.last = f"🎯 ¡Clavado! {name.capitalize()} justo como la pedían: +{revenue} Y$."
            self._ok(client, now, units, revenue, perfect=perfect)

    def _encargos(self, verb: str, arg: str, now: float) -> None:
        client = self.current
        assert client is not None
        if verb == "none":
            if client.bag < 0:
                self.last = (
                    "🤷 «Pues lo encargaría en otro sitio.» Al menos no te llevas un disgusto."
                )
                self._close(Outcome(client, "honest", events=("honest",)), now)
            else:
                self.last = f"😠 Su bolsa estaba en la balda. {self._who(client)} se va sin nada."
                self._close(Outcome(client, "missed_bag", events=("wrong_bag",)), now)
            return
        if verb != "b" or not arg.isdigit() or not 0 <= int(arg) < len(client.bags):
            return
        index = int(arg)
        if index == client.bag:
            units = dict(client.order)
            revenue = self._sum(units)
            self.last = f"✅ Encargo entregado: +{revenue} Y$."
            self._ok(client, now, units, revenue)
            return
        given = client.bags[index].order
        lost = {k: min(n, max(0, self.stock.get(k, 0))) for k, n in given.items()}
        lost = {k: n for k, n in lost.items() if n}
        self._take(lost)
        self.last = (
            f"❌ Esa bolsa era de otra persona. {self._who(client)} se la lleva y hay que "
            "rehacer el encargo."
        )
        self._close(
            Outcome(
                client, "wrong_bag", events=("wrong_bag",), waste_cost=self._cost(lost),
                units=lost,
            ),
            now,
        )  # fmt: skip

    def _balanza(self, verb: str, arg: str, now: float) -> None:
        client = self.current
        assert client is not None
        if verb == "w" and arg.isdigit() and 0 <= int(arg) < len(client.pieces):
            index = int(arg)
            if index in self.scale:
                self.scale.remove(index)
            else:
                self.scale.append(index)
            self.last = ""
            return
        if verb != "weigh":
            return
        grams = self.weighed()
        if not grams:
            self.last = "La balanza está vacía: pon alguna pieza."
            return
        units = {client.species: len(self.scale)}
        revenue = self.weight_price(client.species, grams)
        ratio = grams / client.grams
        if ratio > 1 + WEIGHT_HEAVY:
            self.last = f"🙅 «¿{grams} g? Le pedí {client.grams}, no el barco entero.» Se va."
            self._close(Outcome(client, "refused", events=("refused",)), now)
        elif ratio > 1 + WEIGHT_OK:
            self._take(units)
            self.last = f"🤨 «Bueno, me lo llevo, pero es mucho.» +{revenue} Y$, sin propina."
            self._close(
                Outcome(
                    client, "heavy", self._speed(now), ("heavy",), revenue,
                    sold_cost=self._cost(units), units=units,
                ),
                now,
            )  # fmt: skip
        elif ratio < 1 - WEIGHT_OK:
            self._take(units)
            self.last = f"😒 «Esto es poco.» Paga sus {grams} g: +{revenue} Y$."
            self._close(
                Outcome(
                    client, "light", events=("light",), revenue=revenue,
                    sold_cost=self._cost(units), units=units,
                ),
                now,
            )  # fmt: skip
        else:
            perfect = abs(grams - client.grams) <= client.grams * WEIGHT_PERFECT
            self.last = f"✅ {grams} g: +{revenue} Y$."
            if perfect:
                self.last = f"⚖️ ¡{grams} g, casi clavados! +{revenue} Y$."
            self._ok(
                client, now, units, revenue, perfect=perfect,
                events=("exact_weight",) if perfect else (),
            )  # fmt: skip

    def _revision(self, verb: str, arg: str, now: float) -> None:
        client = self.current
        assert client is not None
        if verb == "m" and self.sector.product(arg) is not None:
            if arg in self.marked:
                self.marked.remove(arg)
            elif self.available(arg) - self.marked.count(arg) <= 0:
                product = self.sector.product(arg)
                self.last = f"🚫 No te queda {product.name.lower()}."  # type: ignore[union-attr]
                return
            else:
                self.marked.append(arg)
            self.last = ""
            return
        if verb != "fix":
            return
        if not self.marked:
            self.last = "Marca primero lo que hay que cambiar."
            return
        marked = set(self.marked)
        faults = set(client.faults)
        found, extra, missed = marked & faults, marked - faults, faults - marked
        units = {k: 1 for k in marked}
        paid = {k: 1 for k in found}
        revenue = self._sum(paid)
        trap = client.protector and "pantalla" not in marked
        if not extra and not missed:
            self.last = f"✅ Como nuevo: +{revenue} Y$."
            self._ok(client, now, units, revenue, perfect=len(faults) >= 3, trap=trap)
            return
        self._take(units)
        events: list[str] = []
        if missed:
            events.append("missed_fault")
            self.last = f"🔁 A los dos días vuelve: aún tenía {len(missed)} cosa(s) mal."
        if extra:
            events.append("upsell")
            self.last = (
                "🧾 «¿Me has cambiado piezas que estaban bien?» No paga lo que no hacía falta."
            )
        self._close(
            Outcome(
                client, "upsell" if extra else "missed", events=tuple(events),
                revenue=revenue, sold_cost=self._cost(paid),
                waste_cost=self._cost({k: 1 for k in extra}), units=units, trap=trap,
            ),
            now,
        )  # fmt: skip

    def _foto(self, verb: str, arg: str, now: float) -> None:
        client = self.current
        assert client is not None and client.target is not None
        style = self.style
        if verb == "l" and arg in LENGTHS:
            style = replace(style, length=arg)
        elif verb == "fade":
            style = replace(style, fade=not style.fade)
        elif verb == "col" and arg in COLORS:
            style = replace(style, color=arg)
        elif verb == "beard":
            style = replace(style, beard=not style.beard)
        elif verb == "wash":
            style = replace(style, wash=not style.wash)
        elif verb == "done":
            self._foto_done(client, now)
            return
        else:
            return
        missing = [
            k for k, n in style.services().items()
            if k != "corte" and self.available(k) < n
        ]  # fmt: skip
        if missing:
            product = self.sector.product(missing[0])
            self.last = f"🚫 No te queda {product.name.lower()}."  # type: ignore[union-attr]
            return
        self.style = style
        self.last = ""

    def _foto_done(self, client: Client, now: float) -> None:
        target = client.target
        assert target is not None
        style = self.style
        if not style.length:
            self.last = "Elige primero el largo."
            return
        done = style.services()
        wanted = target.services()
        paid = {k: n for k, n in done.items() if k in wanted}
        waste = {k: n for k, n in done.items() if k not in wanted}
        if LENGTHS.index(style.length) < LENGTHS.index(target.length):
            self._take(done)
            self.last = (
                f"😱 «¡Me lo has dejado {LENGTH_NAMES[style.length].lower()}!» Se va sin pagar."
            )
            self._close(
                Outcome(
                    client, "too_short", events=("too_short",), waste_cost=self._cost(done),
                    units=done,
                ),
                now,
            )  # fmt: skip
            return
        misses = style.differences(target)
        revenue = self._sum(paid)
        if not misses:
            self.last = f"✅ Se mira al espejo y sonríe: +{revenue} Y$."
            self._ok(client, now, done, revenue, perfect=client.twist not in ("", "igual"))
            return
        self._take(done)
        self.last = (
            f"😕 «No es lo que te pedí.» Paga lo que sí quería: +{revenue} Y$."
            if revenue
            else "😕 «No es lo que te pedí.» No paga."
        )
        self._close(
            Outcome(
                client, "unhappy" if misses == 1 else "mess", events=("unhappy",),
                revenue=revenue, sold_cost=self._cost(paid), waste_cost=self._cost(waste),
                units=done,
            ),
            now,
        )  # fmt: skip

    def _close(self, outcome: Outcome, now: float) -> None:
        self.outcomes.append(outcome)
        self.index += 1
        self.round += 1
        self.round_since = self.since = now
        self._fresh()

    # -- El resultado -------------------------------------------------------------------

    def summary(self) -> TurnSummary:
        """Lo que ha dado el turno (llámalo cuando haya acabado)."""
        return summarize(self.outcomes)


#: Lo que baja la fidelidad de un habitual (dos puntos); `honest` la sube como un acierto.
UPSET = frozenset({
    "wrong", "left", "false_accuse", "bad_diagnosis", "burnt", "wrong_bag", "missed_bag",
    "refused", "upsell", "too_short", "mess",
})  # fmt: skip


@dataclass(frozen=True, slots=True)
class TurnSummary:
    """Lo que ha dado un turno.

    Attributes:
        outcomes: Lo que pasó con cada cliente, en orden.
        revenue: Lo cobrado, sin propinas.
        tips: Propinas.
        sold_cost: Coste al mayorista de lo vendido.
        waste_cost: Coste de lo perdido.
        units: Unidades que salieron del stock, por producto.
        reputation: Lo que se mueve la reputación.
        loyalty: Lo que se mueve la fidelidad de cada habitual que vino.
        score: Nota media (0-100).
        grade: Letra (`GRADES`).
    """

    outcomes: tuple[Outcome, ...]
    revenue: int
    tips: int
    sold_cost: int
    waste_cost: int
    units: dict[str, int]
    reputation: float
    loyalty: dict[str, int]
    score: int
    grade: str

    @property
    def sales(self) -> int:
        """Lo que entra en caja (con propinas)."""
        return self.revenue + self.tips

    @property
    def margin(self) -> int:
        """Ventas menos el coste de lo vendido: sobre esto retiene el pago a cuenta."""
        return self.sales - self.sold_cost

    @property
    def served(self) -> int:
        """Clientes a los que se cobró algo."""
        return sum(1 for o in self.outcomes if o.served)

    def count(self, result: str) -> int:
        """Clientes con ese resultado."""
        return sum(1 for o in self.outcomes if o.result == result)


def summarize(outcomes: list[Outcome]) -> TurnSummary:
    """Junta los resultados de los clientes de un turno."""
    units: dict[str, int] = {}
    loyalty: dict[str, int] = {}
    for outcome in outcomes:
        for key, n in outcome.units.items():
            units[key] = units.get(key, 0) + n
        if (name := outcome.client.regular) is not None:
            if outcome.result in ("ok", "honest"):
                change = 1
            elif outcome.result in UPSET:
                change = -2
            else:
                change = 0
            loyalty[name] = loyalty.get(name, 0) + change
    score = round(sum(o.score for o in outcomes) / len(outcomes)) if outcomes else 0
    grade = next(letter for floor, letter in GRADES if score >= floor)
    return TurnSummary(
        outcomes=tuple(outcomes),
        revenue=sum(o.revenue for o in outcomes),
        tips=sum(o.tip for o in outcomes),
        sold_cost=sum(o.sold_cost for o in outcomes),
        waste_cost=sum(o.waste_cost for o in outcomes),
        units=units,
        reputation=sum(o.reputation for o in outcomes),
        loyalty=loyalty,
        score=score,
        grade=grade,
    )


def apply_loyalty(current: dict[str, int], changes: dict[str, int]) -> dict[str, int]:
    """La fidelidad de los habituales tras un turno (de 0 a `LOYALTY_MAX`)."""
    out = dict(current)
    for name, change in changes.items():
        out[name] = max(0, min(LOYALTY_MAX, out.get(name, 0) + change))
    return out


# -- Jugar solo (la plantilla y `docs/negocio_sim.py`) ----------------------------------

#: Los minijuegos que sabe jugar `autoplay` (los de antes los juegan la plantilla y el
#: calibrador a su manera, que ya estaba medida).
AUTOPLAY_GAMES = frozenset({"brasa", "encargos", "balanza", "revision", "foto"})


def autoplay(game: TurnGame, now: float, *, click: float, wrong: bool, rng: random.Random) -> float:
    """Atiende al cliente de ahora en un minijuego de la 9.48.0, bien o con un fallo típico.

    Lo usan la plantilla (`business_staff.play_turn`) y el calibrador. Cada pulsación suma
    `click` segundos y leer lo que hay que hacer, unas cuantas más. Los fallos son los que
    comete una persona: sacar la carne antes de tiempo, dar la bolsa con el mismo nombre,
    poner una pieza de más, no ver una avería o pasar del cambio de la foto. Devuelve la hora
    al acabar.
    """
    client = game.current
    assert client is not None
    kind = game.sector.game

    def press(action: str, think: float = 1.0) -> None:
        nonlocal now
        now += click * think
        game.press(action, now)

    if kind == "brasa":
        if client.dish:
            _key, _ask, low, high = POINT_BY_KEY[client.point]
            stop = low - (8 if wrong else 0)
            while game.current is client and game.heat < stop:
                press("fire:2" if game.heat + HEAT_STEPS["2"][1] <= high else "fire:1")
        if game.current is client:
            press("serve")
    elif kind == "encargos":
        if wrong:
            label = client.label
            twin = next(
                (i for i, b in enumerate(client.bags) if b.label == label and i != client.bag), 0
            )
            press(f"b:{twin}", 3)
        else:
            press("none" if client.bag < 0 else f"b:{client.bag}", 3)
    elif kind == "balanza":
        chosen = list(best_weight(client.pieces, client.grams))
        if wrong:
            rest = [i for i in range(len(client.pieces)) if i not in chosen]
            if rest and (len(chosen) == 1 or rng.random() < 0.5):
                chosen.append(rng.choice(rest))
            else:
                chosen.pop()
        for index in chosen:
            press(f"w:{index}", 1.5)
        press("weigh")
    elif kind == "revision":
        marks = list(client.faults)
        if wrong:
            if len(marks) > 1 and rng.random() < 0.6:
                marks.pop()
            elif client.protector:
                marks.append("pantalla")
            else:
                others = [p.key for p in game.sector.products if p.key not in marks]
                marks.append(rng.choice(others))
        now += click * 2  # mirar el móvil por delante y por detrás
        for key in marks:
            press(f"m:{key}")
        press("fix")
    elif kind == "foto":
        assert client.target is not None and client.photo is not None
        want = client.photo if wrong and client.twist not in ("", "igual") else client.target
        now += click * 2  # mirar la foto
        press(f"l:{want.length}")
        if want.fade:
            press("fade")
        if want.color != "natural":
            press(f"col:{want.color}")
        if want.beard:
            press("beard")
        if want.wash:
            press("wash")
        press("done")
    if game.current is client and not game.finished(now):
        # No le quedaba algo y no pudo ni empezar: el cliente se cansa y se va.
        game._close(Outcome(client, "left", events=("left",)), now)
    return now + click
