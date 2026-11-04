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

Todo el azar sale de semillas: el del turno sortea la cola y el de cada cliente decide si
acepta el presupuesto. Pintar por adelantado no cambia nada.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

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
from bot.services.business_catalog import FAULTS, GREETINGS, Product, Sector

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
TIP_SHARE = {"comandas": 0.10, "caja": 0.05, "diagnostico": 0.0}
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

#: Nota de cada resultado (y, si se atendió bien, según la rapidez).
SCORES: dict[str, int] = {
    "short": 60,
    "over": 60,
    "missed_thief": 50,
    "rejected": 50,
    "no_part": 30,
    "wrong": 0,
    "bad_diagnosis": 0,
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


def make_clients(business: Business, count: int, seed: int) -> list[Client]:
    """La cola de un turno: `count` clientes como mucho, con lo que queda en el stock.

    En la cafetería y la tienda cada uno pide de lo que queda (si ya no queda nada, no
    viene nadie más). En el taller la avería no mira tu stock: si no tienes el recambio,
    lo pierdes. Los habituales piden lo de siempre si está.
    """
    sector = business.sector_info
    rng = random.Random(seed)
    stock = dict(business.stock)
    regulars = list(regular_names(business.seed))
    rng.shuffle(regulars)
    clients: list[Client] = []
    for _ in range(count):
        client = Client(seed=rng.randrange(2**31), line=rng.choice(GREETINGS[sector.game]))
        regular = regulars.pop() if regulars and rng.random() < REGULAR_CHANCE else None
        loyalty = business.regulars.get(regular, 0) if regular else 0
        usual = usual_order(business.seed, regular, sector) if regular else {}
        if sector.game == "diagnostico":
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
            client.regular, client.fault, client.options = regular, fault, tuple(options)
            client.symptom, client.cue = rng.choice(texts), cue
            client.car_color, client.car_model = rng.choice(CAR_COLORS), rng.randrange(4)
            client.usual = bool(regular) and loyalty >= USUAL_LOYALTY
            clients.append(client)
            continue
        if usual and _fits(usual, stock):
            order = usual
            for key, n in usual.items():
                stock[key] -= n
            client.usual = loyalty >= USUAL_LOYALTY
        else:
            order = _basket(sector, stock, rng)
        if not order:
            break
        client.regular, client.order = regular, order
        if sector.game == "caja":
            if regular is None and rng.random() < THIEF_CHANCE:
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
        self.patience = (
            self.sector.patience
            * (TIRED_PATIENCE if tired else 1.0)
            * (1 + sum(e.patience for e in installed))
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
        self.tray: list[str] = []
        self.given: list[int] = []
        self.accused = False
        self.step = "diagnostico"
        self.diagnosis: str | None = None
        self.paid = clients[0].paid if clients else 0
        self.extra = 0

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
        if game == "comandas":
            self._comandas(verb, arg, now)
        elif game == "caja":
            self._caja(verb, arg, now)
        else:
            self._diagnostico(verb, arg, now)

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

    def _close(self, outcome: Outcome, now: float) -> None:
        self.outcomes.append(outcome)
        self.index += 1
        self.round += 1
        self.round_since = self.since = now
        self.tray, self.given, self.accused, self.extra = [], [], False, 0
        self.step, self.diagnosis = "diagnostico", None
        if (client := self.current) is not None:
            self.paid = client.paid

    # -- El resultado -------------------------------------------------------------------

    def summary(self) -> TurnSummary:
        """Lo que ha dado el turno (llámalo cuando haya acabado)."""
        return summarize(self.outcomes)


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
            if outcome.result == "ok":
                change = 1
            elif outcome.result in ("wrong", "left", "false_accuse", "bad_diagnosis"):
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
