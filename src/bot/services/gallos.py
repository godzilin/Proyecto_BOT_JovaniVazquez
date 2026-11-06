"""Reglas de las peleas de gallos (`gallos`): rivales, cuotas, la pelea y los repartos.

Lógica pura, sin Discord ni dinero. El cog (`bot.cogs.gallos`) cobra, paga y
pinta (`bot.services.gallos_scene`); la ficha de cada gallo se guarda en
`bot.repositories.gallos`.

**Stats, entrenamiento y muerte.** Cada gallo tiene tres stats de 0 a 100
(`Stat`: 💪 fuerza, 🪶 velocidad y ❤️ aguante) y su *poder* es la media. Se
entrenan pagando (`train_price`, `train_gain`, con una hora de espera y un tope
diario) y deciden quién gana (`win_probability`). Si un gallo pierde por KO
puede morir (`death_chance`, que baja con el aguante y con la forma): su ficha
pasa al cementerio y el dueño empieza con un gallo nuevo, a cero.

**La forma, lo que hace rentable entrenar.** Cada entrenamiento da forma
(`FORM_PER_TRAIN`, de 0 a `FORM_MAX`), cada pelea contra la casa gasta
`FORM_PER_FIGHT` y baja sola con el tiempo (`FORM_DECAY_PER_DAY`, contada por
horas en `form_now`, sin tareas nocturnas). Con forma, la casa paga por encima
de su `RTP`: `house_rtp` le suma `house_edge` (hasta `EDGE_MAX` con la forma al
máximo contra el rival más duro, en proporción a su `difficulty`) sobre la parte
de la apuesta que cubre `edge_cap`, que crece con el poder. Lo que pasa del tope
juega al 95 %: apostar más no multiplica lo que se saca, y lo que se puede sacar
al día lo limita la forma que entra, es decir, los entrenamientos (su tope
diario). Las cifras salen de `docs/gallos_sim.py`: quien entrena cada día y
pelea en forma contra rivales duros gana entre 1,5 y 3 veces el IMV al día, el
que pelea agotado o contra rivales fáciles pierde, y el que no entrena juega al
95 % de siempre.

**Dos maneras de pelear:**

- *Contra la casa*: tu gallo contra uno de los rivales de `HOUSE`, cada uno con
  la probabilidad de que le gane un gallo sin entrenar (`Rival.chance`). La
  cuota se recalcula con la probabilidad real de cada pelea (`house_odds`) y
  devuelve de media el `RTP` (95 %) de lo apostado más la ventaja de la forma:
  los stats cambian el riesgo y la forma, el margen.
- *Contra otra persona*: los dos ponen lo mismo y el ganador se lleva el bote
  menos la comisión de la casa (`DUEL_CUT`). Entrenar sí da ventaja aquí
  (`win_probability` partiendo de 0,5): es dinero entre jugadores, de suma cero
  menos la comisión. Mientras se calientan los gallos,
  el público apuesta por uno u otro a la porra: todo lo apostado se reparte
  entre los que acierten, menos la misma comisión (`pool_payouts`).

**La skin no pelea; los stats sí.** El aspecto del gallo (`bot.services.skins`,
juego `gallos`), su nombre y su historial no tocan ninguna probabilidad. Lo que
pelea son los stats: `p = sigmoid(logit(p0) + K * (poder_tuyo - poder_rival) /
100)` con `K = 3`, acotada a [3 %, 97 %]. Contra la casa, `p0` es la `chance` de
serie del rival, anclada a un gallo sin entrenar (poder 0): ese juega
exactamente como antes de que existieran los stats. Entre dos personas `p0` es
0,5 y cuentan los dos poderes. El aspecto de los rivales de la casa y su golpe
estrella (`Rival.signature`) son solo la gracia; su `Rival.power` es el poder
que se le supone al cartel y sirve para ordenarlos y enseñarlo, no para el
sorteo.

**Cómo se decide y cómo se cuenta.** Primero se sortea quién gana (con la
probabilidad de arriba) y luego `simulate` cuenta una pelea que acabe así. La
pelea se simula simétrica (cada intercambio lo empieza cualquiera de los dos al
50 %) y, si el ganador que sale no es el sorteado, se le da la vuelta entera:
como las dos esquinas son intercambiables, el resultado tiene exactamente la
probabilidad sorteada y la pelea parece igual de natural en los dos sentidos.
Los stats solo deciden quién gana: no cambian la animación.

**El dinero que sale de la nada.** Los premios de la casa salen de la nada,
como en cualquier juego del casino; los compensa el margen de las cuotas. En
el duelo y en la porra del público no se crea dinero: la comisión se destruye
(es el margen de la casa, no un impuesto, así que no va al Estado). La única
excepción es la cuota mínima de la porra (`MIN_POOL_PAYOUT`): quien acierta
nunca cobra menos de lo que apostó, aunque casi todos apostaran lo mismo.
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from enum import Enum
from fractions import Fraction
from types import MappingProxyType

#: Clave del juego: motivo del libro, `casino_stats.GAMES` y juego de las skins.
GAME = "gallos"
#: Lo que devuelve de media una pelea contra la casa (el resto es su margen).
RTP = Fraction(95, 100)
#: Comisión de la casa en el bote de un duelo y en la porra del público.
DUEL_CUT = Fraction(5, 100)
POOL_CUT = DUEL_CUT
#: Quien acierta en la porra cobra al menos esto por cada Y$ apostado.
MIN_POOL_PAYOUT = Fraction(1)

#: Vida de cada gallo al empezar.
MAX_HP = 100
#: Asaltos de una pelea y cuántos intercambios lleva cada uno (como mucho).
ROUNDS = 3
EXCHANGES_PER_ROUND = (3, 4)
#: Probabilidad de que el que recibe esquive el golpe.
DODGE_CHANCE = 0.18
#: Probabilidad de golpe crítico (daño doble).
CRIT_CHANCE = 0.10
#: Por debajo de esta vida, un gallo puede salir corriendo tras un golpe.
FLEE_HP = 25
FLEE_CHANCE = 0.15

#: Tope de cada stat.
STAT_MAX = 100
#: Cuánto pesa una diferencia de poder (de 0 a 100) en el logit de ganar.
POWER_K = 3.0
#: La probabilidad de ganar nunca sale de aquí, por mucho que se entrene.
MIN_WIN = Fraction(3, 100)
MAX_WIN = Fraction(97, 100)
#: Un gallo con este poder o más cuenta como «entrenado» para los logros.
TRAINED_POWER = 25

#: Segundos de espera entre dos entrenamientos y entrenamientos al día (día canario).
TRAIN_COOLDOWN = 3600
TRAIN_PER_DAY = 6
#: Motivo del libro de un entrenamiento (la tienda lo antepone: `tienda:gallos:entreno`).
TRAIN_CONCEPT = "gallos:entreno"

#: Probabilidad base de morir al perder por KO; el aguante la rebaja hasta la mitad.
DEATH_CHANCE = 0.10
#: La forma la multiplica por esto: agotado (forma 0) y en plena forma (100).
TIRED_DEATH = 1.5
FIT_DEATH = 0.25

#: Largo máximo del nombre de un gallo.
NAME_MAX = 24
#: Nombres de serie hasta que el dueño le pone uno.
DEFAULT_NAMES = (
    "Kikirikí",
    "Espolón",
    "Pechuga de Acero",
    "Cresta Roja",
    "Gallito Inglés",
    "El Madrugador",
    "Plumas",
    "Caldo de Gallina",
    "Sin Miedo",
    "Pollo Pera",
)


class Corner(Enum):
    """Las dos esquinas del reñidero. En un duelo, el que reta es el rojo."""

    RED = "rojo"
    BLUE = "azul"

    @property
    def other(self) -> Corner:
        """La otra esquina."""
        return Corner.BLUE if self is Corner.RED else Corner.RED

    @property
    def emoji(self) -> str:
        """🔴 o 🔵."""
        return "🔴" if self is Corner.RED else "🔵"


class Finish(Enum):
    """Cómo acaba la pelea."""

    KO = "ko"
    FLED = "huida"
    POINTS = "puntos"

    @property
    def label(self) -> str:
        """Cómo se dice en el resultado."""
        return {
            Finish.KO: "por KO",
            Finish.FLED: "porque el otro salió por patas",
            Finish.POINTS: "a los puntos",
        }[self]


# -- Golpes ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Move:
    """Un golpe.

    Attributes:
        key: Clave estable (la escena la usa para elegir la animación).
        name: Nombre para la narración.
        damage: Daño mínimo y máximo, sin crítico.
        weight: Lo a menudo que sale.
        sound: Onomatopeya que pinta la escena al impactar.
    """

    key: str
    name: str
    damage: tuple[int, int]
    weight: int
    sound: str


MOVES: tuple[Move, ...] = (
    Move("picotazo", "Picotazo", (11, 22), 5, "¡TOC!"),
    Move("espolonazo", "Espolonazo", (18, 32), 3, "¡ZAS!"),
    Move("aletazo", "Aletazo", (7, 16), 4, "¡FLAP!"),
    Move("crestazo", "Crestazo", (14, 25), 3, "¡BONK!"),
    Move("patada", "Patada voladora", (22, 36), 2, "¡PAF!"),
)
MOVE_BY_KEY: dict[str, Move] = {m.key: m for m in MOVES}
#: El golpe que cambia de nombre por el golpe estrella de cada rival.
SIGNATURE_MOVE = "patada"


# -- Rivales de la casa ------------------------------------------------------------------


def _look(**params: object) -> Mapping[str, object]:
    return MappingProxyType(dict(params))


@dataclass(frozen=True, slots=True)
class Rival:
    """Un gallo de la casa.

    Attributes:
        key: Clave estable (la cuentan los logros: `gallos_beat_<clave>`).
        name: Nombre del gallo.
        emoji: Bandera o icono de su sitio.
        origin: De dónde viene, para el texto.
        blurb: Su ficha en una frase.
        chance: Probabilidad de que le gane un gallo sin entrenar (poder 0).
        signature: Nombre de su golpe estrella (su patada voladora).
        look: Parámetros de la escena que cambia respecto al gallo de serie
            (los mismos que una skin: `skins.GAMES["gallos"].defaults`).
        power: Poder que se le supone (0 a 100): crece con su dificultad y es lo
            que se enseña para compararlo con el tuyo. No entra en el sorteo
            (ver `house_chance`).
    """

    key: str
    name: str
    emoji: str
    origin: str
    blurb: str
    chance: Fraction
    signature: str
    look: Mapping[str, object] = field(default_factory=dict)
    power: int = 0

    @property
    def label(self) -> str:
        """`🇺🇸 Arancel`."""
        return f"{self.emoji} {self.name}"

    @property
    def multiplier(self) -> Fraction:
        """Cuota a la que paga ganarle con un gallo sin entrenar (apuesta incluida)."""
        return house_multiplier(self.chance)

    def chance_for(self, power: float) -> Fraction:
        """Probabilidad de que le gane un gallo de ese poder."""
        return house_chance(self, power)

    def multiplier_for(self, power: float) -> Fraction:
        """Cuota a la que paga ganarle con un gallo de ese poder (devuelve el `RTP`)."""
        return house_multiplier(house_chance(self, power))


# fmt: off
HOUSE: tuple[Rival, ...] = (
    Rival("temu", "Gallo de Temu", "📦", "un almacén de Shenzhen",
          "Lo pediste por 2,99 y llegó en tres cajas. Falta una pata.",
          Fraction(85, 100), "Pieza de recambio",
          _look(body="#d9c25a", hackle="#f0dc78", wing="#b49a3a",
                tail=("#7a6a2a", "#a08a3a", "#c9b45a"), comb="#e86a6a", accessory="caja"),
          power=10),
    Rival("teror", "El Pollo de Teror", "🌋", "Gran Canaria",
          "Gallo viejo de la Fiesta del Pino. Ya no pica, pero huele a gofio.",
          Fraction(80, 100), "Gofiazo",
          _look(body="#e8d27a", hackle="#fff0a8", wing="#c9ad4a",
                tail=("#3a6b3a", "#2f5a8a", "#e8d27a"), accessory="sombrero"), power=15),
    Rival("directiva", "Directiva 2019/904", "🇪🇺", "Bruselas",
          "Lleva el tapón pegado a la cresta y pide tres formularios antes de cada picotazo.",
          Fraction(65, 100), "Tapón solidario",
          _look(body="#2a4aa8", hackle="#3c64d0", wing="#1f3a86",
                tail=("#f2c42a", "#2a4aa8", "#f2c42a"), comb="#f2c42a", accessory="estrellas"),
          power=30),
    Rival("falcon", "El Gallo del Falcon", "🇪🇸", "la Moncloa",
          "Vuela a todas partes en avión oficial y no dimite ni con el pico roto.",
          Fraction(50, 100), "Manual de resistencia",
          _look(body="#c8102e", hackle="#e8344a", wing="#9a0c22",
                tail=("#1a1a1a", "#c8102e", "#f2f2f2"), accessory="rosa"), power=50),
    Rival("motosierra", "La Motosierra", "🇦🇷", "la Casa Rosada",
          "Le grita «¡afuera!» a cada pluma que le sobra. Pelado a lo león.",
          Fraction(45, 100), "¡Afuera!",
          _look(body="#6ab0e8", hackle="#f2f2f2", wing="#4a90c8",
                tail=("#6ab0e8", "#f2f2f2", "#6ab0e8"), accessory="patillas"), power=55),
    Rival("sahel", "El Coronel del Sahel", "🌍", "un cuartel del Sahel",
          "Llegó al reñidero con un golpe de Estado y promete elecciones «pronto».",
          Fraction(40, 100), "Golpe de Estado",
          _look(body="#5a6a3a", hackle="#7a8a4a", wing="#4a5a2a",
                tail=("#3a4a2a", "#5a6a3a", "#8a7a4a"), accessory="boina"), power=60),
    Rival("arancel", "Arancel", "🇺🇸", "el Despacho Oval",
          "Tupé naranja, gorra roja y un 200 % de recargo a cada pluma que entra.",
          Fraction(35, 100), "Arancel del 200 %",
          _look(body="#f2a03a", hackle="#ffc85a", wing="#d0802a",
                tail=("#c8102e", "#f2f2f2", "#1a3a8a"), accessory="gorra"), power=65),
    Rival("credito", "Crédito Social", "🇨🇳", "Zhongnanhai",
          "Te quita puntos por cada picotazo que le das. Y por cada uno que no le das.",
          Fraction(30, 100), "Bajada de crédito social",
          _look(body="#c8102e", hackle="#ffd200", wing="#a00c24",
                tail=("#ffd200", "#c8102e", "#ffd200"), comb="#ffd200", accessory="camara"),
          power=70),
    Rival("cupula", "Cúpula de Hierro", "🇮🇱", "la Knesset",
          "Intercepta casi todo y lleva treinta años con el juicio aplazado.",
          Fraction(25, 100), "Intercepción",
          _look(body="#e8eef8", hackle="#ffffff", wing="#b8c8e8",
                tail=("#1a4ab8", "#e8eef8", "#1a4ab8"), accessory="escudo"), power=75),
    Rival("actas", "El Gallo de las Actas", "🇻🇪", "Miraflores",
          "Gana siempre según sus actas. Las actas no aparecen.",
          Fraction(15, 100), "Recuento propio",
          _look(body="#ffcc00", hackle="#1a3a8a", wing="#cf142b",
                tail=("#ffcc00", "#1a3a8a", "#cf142b"), accessory="bigote"), power=85),
    Rival("marte", "Gallo de Marte", "🚀", "una nave que explotó en el despegue",
          "Despide a medio gallinero a las 3 de la mañana y lo anuncia en X.",
          Fraction(10, 100), "Despido masivo",
          _look(body="#2a2a2a", hackle="#4a4a4a", wing="#1a1a1a",
                tail=("#e8e8e8", "#8a8a8a", "#e8e8e8"), comb="#e8e8e8", accessory="casco"),
          power=90),
)
# fmt: on
RIVAL_BY_KEY: dict[str, Rival] = {r.key: r for r in HOUSE}


def house_multiplier(chance: Fraction, rtp: Fraction = RTP) -> Fraction:
    """Cuota de ganar con probabilidad `chance`: devuelve de media `rtp` (el `RTP` de serie).

    Raises:
        ValueError: Si la probabilidad no está entre 0 y 1 (sin incluirlos).
    """
    if not 0 < chance < 1:
        raise ValueError("La probabilidad tiene que estar entre 0 y 1.")
    return rtp / chance


def win_probability(p0: Fraction | float, mine: float, theirs: float = 0.0) -> Fraction:
    """Probabilidad de ganar con poder `mine` contra poder `theirs`, partiendo de `p0`.

    `p = sigmoid(logit(p0) + POWER_K * (mine - theirs) / 100)`, acotada a
    [`MIN_WIN`, `MAX_WIN`] y redondeada a la diezmilésima para que la cuota
    (`RTP / p`) siga siendo una fracción limpia. Con poderes iguales devuelve
    `p0` tal cual.
    """
    base = min(MAX_WIN, max(MIN_WIN, Fraction(p0).limit_denominator(10_000)))
    diff = mine - theirs
    if diff == 0:
        return base
    x = math.log(base / (1 - base)) + POWER_K * diff / 100
    p = Fraction(round(10_000 / (1 + math.exp(-x))), 10_000)
    return min(MAX_WIN, max(MIN_WIN, p))


#: Contra la casa, la probabilidad no pasa de esto: así la cuota (`RTP / p`) nunca
#: baja de ×1,01 y ganar siempre devuelve algo más de lo apostado.
MAX_HOUSE_WIN = (RTP / Fraction(101, 100)).limit_denominator(10_000)


def house_chance(rival: Rival, power: float, rtp: Fraction = RTP) -> Fraction:
    """Probabilidad de que un gallo de `power` le gane a `rival`.

    Con poder 0 es `rival.chance`, tal como era antes de los stats. Acotada para
    que la cuota (`rtp / p`) no baje de ×1,01: con el `RTP` de serie, a
    `MAX_HOUSE_WIN`; con la ventaja de la forma (`house_rtp`), algo más arriba.
    """
    top = MAX_HOUSE_WIN if rtp == RTP else (rtp / Fraction(101, 100)).limit_denominator(10_000)
    return min(top, win_probability(rival.chance, power, 0.0))


def duel_chance(red_power: float, blue_power: float) -> Fraction:
    """Probabilidad de que gane el rojo en un duelo (0,5 con los mismos poderes)."""
    return win_probability(Fraction(1, 2), red_power, blue_power)


def house_payout(stake: int, rival: Rival, power: float = 0.0, form: float = 0.0) -> int:
    """Lo que cobra quien le gana a `rival` con `stake` (apuesta incluida, redondeado abajo).

    `power` es el poder de su gallo y `form`, su forma: la cuota se recalcula con
    la probabilidad de esa pelea y la ventaja de la forma (`house_odds`).
    """
    return int(stake * house_odds(rival, power, form, stake)[1])


# -- Forma y ventaja ---------------------------------------------------------------------

#: Tope de la forma.
FORM_MAX = 100
#: Lo que baja la forma cada día sin entrenar (se descuenta por horas, sin tareas nocturnas).
FORM_DECAY_PER_DAY = 12
#: Forma que da un entrenamiento de calidad 1 (minijuego a media nota, sin combo).
FORM_PER_TRAIN = 12
#: Parte de esa forma que se gana al pagar; el resto depende del minijuego
#: (`gallos_gym.after_session`): quien lo abandona se queda con esta.
FORM_ON_PAYMENT = Fraction(1, 2)
#: Forma que da pagar un entrenamiento.
PAID_FORM = float(FORM_PER_TRAIN * FORM_ON_PAYMENT)
#: Forma que gasta cada pelea contra la casa.
FORM_PER_FIGHT = 10
#: Lo que la forma al máximo suma al RTP contra el rival más duro (en lo cubierto).
EDGE_MAX = Fraction(20, 100)
#: Apuesta cubierta por la ventaja: `EDGE_CAP_BASE + EDGE_CAP_PER_POWER · poder`.
EDGE_CAP_BASE = 2_000
EDGE_CAP_PER_POWER = 150
#: El rival que menos se deja ganar: su dificultad es 1 (la de los demás, proporcional).
_HARDEST = Fraction(9, 10)


def form_now(record: RoosterRecord, now: float) -> float:
    """La forma del gallo en `now`: la guardada menos lo que ha bajado desde entonces."""
    lost = max(0.0, now - record.form_at) / 86_400 * FORM_DECAY_PER_DAY
    return max(0.0, record.form - lost)


def with_form(record: RoosterRecord, form: float, now: float) -> RoosterRecord:
    """La ficha con la forma fijada a `form` (entre 0 y `FORM_MAX`) en el instante `now`."""
    return replace(record, form=min(float(FORM_MAX), max(0.0, form)), form_at=now)


def difficulty(rival: Rival) -> Fraction:
    """Lo duro que es `rival`, de 0 a 1: la parte de la ventaja que da ganarle."""
    return (1 - rival.chance) / _HARDEST


def house_edge(rival: Rival, form: float) -> Fraction:
    """Lo que la forma suma al RTP contra `rival`, en la parte de la apuesta cubierta."""
    level = Fraction(round(min(FORM_MAX, max(0.0, form))), FORM_MAX)
    return EDGE_MAX * level * difficulty(rival)


def edge_cap(power: float) -> int:
    """Apuesta máxima a la que llega la ventaja de la forma con un gallo de `power`."""
    return EDGE_CAP_BASE + round(EDGE_CAP_PER_POWER * power)


#: ☠️ A muerte: lo que suma al RTP contra el rival más duro (en lo cubierto), a cambio
#: de que perder por KO mate seguro.
DEADLY_EDGE = Fraction(10, 100)
#: El pique: victorias seguidas contra un mismo rival para que se pique, y lo que
#: multiplica entonces la ventaja de la forma y la probabilidad de morir.
GRUDGE_WINS = 3
GRUDGE_EDGE = Fraction(3, 2)
GRUDGE_DEATH = 1.5


def total_edge(
    rival: Rival, form: float, *, deadly: bool = False, grudge: bool = False
) -> Fraction:
    """Lo que se suma al RTP en lo cubierto: la forma (más si el rival está picado) y ☠️."""
    edge = house_edge(rival, form) * (GRUDGE_EDGE if grudge else 1)
    if deadly:
        edge += DEADLY_EDGE * difficulty(rival)
    return edge


def house_rtp(
    rival: Rival,
    power: float,
    form: float,
    stake: int,
    *,
    deadly: bool = False,
    grudge: bool = False,
) -> Fraction:
    """Lo que devuelve de media una pelea contra `rival` con esa apuesta, poder y forma.

    El `RTP` de serie más la ventaja (`total_edge`: la forma, el pique y ☠️ a
    muerte) sobre lo cubierto (`edge_cap`): lo que pase del tope juega al 95 %,
    así que apostar más no multiplica lo que se saca.
    """
    if stake <= 0:
        return RTP
    covered = min(stake, edge_cap(power))
    return RTP + total_edge(rival, form, deadly=deadly, grudge=grudge) * covered / stake


def house_odds(
    rival: Rival,
    power: float,
    form: float,
    stake: int,
    *,
    deadly: bool = False,
    grudge: bool = False,
) -> tuple[Fraction, Fraction]:
    """La probabilidad de ganar y la cuota (apuesta incluida) de una pelea contra la casa."""
    rtp = house_rtp(rival, power, form, stake, deadly=deadly, grudge=grudge)
    chance = house_chance(rival, power, rtp)
    return chance, house_multiplier(chance, rtp)


def grudge_against(record: RoosterRecord, rival: str) -> bool:
    """Si `rival` está picado con este gallo (le ha ganado `GRUDGE_WINS` seguidas o más)."""
    return record.grudge_rival == rival and record.grudge_wins >= GRUDGE_WINS


def format_odds(multiplier: Fraction | float) -> str:
    """`Fraction(19, 10)` → `×1,90`."""
    return "×" + f"{float(multiplier):,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")


def duel_prize(stake: int) -> int:
    """Lo que se lleva el ganador de un duelo en el que cada uno puso `stake`."""
    return int(2 * stake * (1 - DUEL_CUT))


# -- La pelea ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Exchange:
    """Un intercambio: uno ataca y el otro recibe (o esquiva).

    Attributes:
        round: Asalto (desde 1).
        attacker: Quién ataca.
        move: Clave del golpe (`MOVES`).
        damage: Daño hecho (0 si esquiva).
        crit: Si fue crítico.
        dodged: Si el que recibe lo esquivó.
        hp: Vida de rojo y azul después del intercambio.
    """

    round: int
    attacker: Corner
    move: str
    damage: int
    crit: bool
    dodged: bool
    hp: tuple[int, int]

    def mirrored(self) -> Exchange:
        """El mismo intercambio con las esquinas cambiadas."""
        return replace(self, attacker=self.attacker.other, hp=(self.hp[1], self.hp[0]))


@dataclass(frozen=True, slots=True)
class Fight:
    """Una pelea ya decidida y contada.

    Attributes:
        winner: Quién gana.
        finish: Cómo acaba.
        exchanges: Los intercambios, en orden.
    """

    winner: Corner
    finish: Finish
    exchanges: tuple[Exchange, ...]

    @property
    def loser(self) -> Corner:
        """Quién pierde."""
        return self.winner.other

    @property
    def rounds(self) -> int:
        """Asaltos que duró."""
        return self.exchanges[-1].round if self.exchanges else 1

    def hp(self, corner: Corner) -> int:
        """Vida con la que acaba `corner`."""
        last = self.exchanges[-1].hp if self.exchanges else (MAX_HP, MAX_HP)
        return last[0] if corner is Corner.RED else last[1]

    def hits(self, corner: Corner) -> list[Exchange]:
        """Golpes que `corner` acertó."""
        return [e for e in self.exchanges if e.attacker is corner and not e.dodged]

    def crits(self, corner: Corner) -> int:
        """Críticos que dio `corner`."""
        return sum(1 for e in self.hits(corner) if e.crit)

    def damage_taken(self, corner: Corner) -> int:
        """Vida que perdió `corner`."""
        return MAX_HP - self.hp(corner)

    def dodges(self, corner: Corner) -> int:
        """Golpes que esquivó `corner`."""
        return sum(1 for e in self.exchanges if e.attacker is corner.other and e.dodged)

    def mirrored(self) -> Fight:
        """La misma pelea con las esquinas cambiadas."""
        return Fight(
            winner=self.winner.other,
            finish=self.finish,
            exchanges=tuple(e.mirrored() for e in self.exchanges),
        )


def _pick_move(rng: random.Random) -> Move:
    return rng.choices(MOVES, weights=[m.weight for m in MOVES])[0]


def _natural(rng: random.Random) -> Fight:
    """Una pelea simétrica: cada intercambio lo empieza cualquiera de los dos al 50 %."""
    hp = {Corner.RED: MAX_HP, Corner.BLUE: MAX_HP}
    exchanges: list[Exchange] = []
    for round_number in range(1, ROUNDS + 1):
        for _ in range(rng.randint(*EXCHANGES_PER_ROUND)):
            attacker = Corner.RED if rng.random() < 0.5 else Corner.BLUE
            target = attacker.other
            move = _pick_move(rng)
            dodged = rng.random() < DODGE_CHANCE
            crit = not dodged and rng.random() < CRIT_CHANCE
            damage = 0 if dodged else rng.randint(*move.damage) * (2 if crit else 1)
            hp[target] = max(0, hp[target] - damage)
            exchanges.append(
                Exchange(
                    round_number,
                    attacker,
                    move.key,
                    damage,
                    crit,
                    dodged,
                    (hp[Corner.RED], hp[Corner.BLUE]),
                )
            )
            if hp[target] == 0:
                return Fight(attacker, Finish.KO, tuple(exchanges))
            if not dodged and hp[target] <= FLEE_HP and rng.random() < FLEE_CHANCE:
                return Fight(attacker, Finish.FLED, tuple(exchanges))
    if hp[Corner.RED] != hp[Corner.BLUE]:
        winner = Corner.RED if hp[Corner.RED] > hp[Corner.BLUE] else Corner.BLUE
    else:
        # Empate a vida: los jueces se lo dan al último que acertó.
        landed = [e for e in exchanges if not e.dodged]
        winner = landed[-1].attacker if landed else Corner.RED
    return Fight(winner, Finish.POINTS, tuple(exchanges))


def simulate(winner: Corner, rng: random.Random) -> Fight:
    """Cuenta una pelea que gana `winner` (ver la docstring del módulo)."""
    fight = _natural(rng)
    return fight if fight.winner is winner else fight.mirrored()


def draw_winner(chance: Fraction | float, rng: random.Random) -> Corner:
    """Sortea el ganador: el rojo gana con probabilidad `chance`."""
    return Corner.RED if rng.random() < chance else Corner.BLUE


# -- Porra del público -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PoolBet:
    """Una apuesta del público: por qué esquina y cuánto."""

    corner: Corner
    stake: int


def pool_totals(bets: Mapping[int, PoolBet]) -> dict[Corner, int]:
    """Lo apostado a cada esquina."""
    totals = {Corner.RED: 0, Corner.BLUE: 0}
    for bet in bets.values():
        totals[bet.corner] += bet.stake
    return totals


def pool_odds(bets: Mapping[int, PoolBet]) -> dict[Corner, Fraction | None]:
    """Cuota de cada esquina si ganara ahora mismo; `None` si nadie le ha apostado.

    Es la cuota «a la porra»: lo que hay en total, menos la comisión, entre lo
    que hay a esa esquina, y nunca menos de `MIN_POOL_PAYOUT`.
    """
    totals = pool_totals(bets)
    pot = sum(totals.values()) * (1 - POOL_CUT)
    return {
        corner: max(MIN_POOL_PAYOUT, pot / total) if total else None
        for corner, total in totals.items()
    }


def pool_payouts(bets: Mapping[int, PoolBet], winner: Corner) -> dict[int, int]:
    """Lo que cobra cada apostante del público (apuesta incluida; 0 si falla).

    Si una de las dos esquinas no tiene ninguna apuesta, no hay porra: se
    devuelve a cada uno lo suyo.
    """
    totals = pool_totals(bets)
    if not totals[Corner.RED] or not totals[Corner.BLUE]:
        return {user: bet.stake for user, bet in bets.items()}
    odds = pool_odds(bets)[winner]
    assert odds is not None  # las dos esquinas tienen apuestas
    return {
        user: int(bet.stake * odds) if bet.corner is winner else 0 for user, bet in bets.items()
    }


# -- La ficha del gallo ------------------------------------------------------------------


class Stat(Enum):
    """Los tres stats de un gallo; el valor es la clave estable (botones y columnas)."""

    STRENGTH = "fuerza"
    SPEED = "velocidad"
    ENDURANCE = "aguante"

    @property
    def emoji(self) -> str:
        """💪, 🪶 o ❤️."""
        return {Stat.STRENGTH: "💪", Stat.SPEED: "🪶", Stat.ENDURANCE: "❤️"}[self]

    @property
    def label(self) -> str:
        """`Fuerza`, `Velocidad` o `Aguante`."""
        return self.value.capitalize()

    @property
    def column(self) -> str:
        """Columna de la ficha y campo de `RoosterRecord`."""
        return {
            Stat.STRENGTH: "strength",
            Stat.SPEED: "speed",
            Stat.ENDURANCE: "endurance",
        }[self]


STAT_BY_KEY: dict[str, Stat] = {s.value: s for s in Stat}


@dataclass(frozen=True, slots=True)
class RoosterRecord:
    """La ficha de un gallo: su historial, sus stats y su entrenamiento.

    El historial y el aspecto no tocan ninguna probabilidad; los stats sí (ver
    la docstring del módulo).

    Attributes:
        name: Su nombre.
        fights: Peleas.
        wins: Victorias.
        kos: Victorias por KO.
        streak: Victorias seguidas ahora mismo.
        best_streak: La racha más larga.
        day_streak: Días seguidos peleando.
        last_day: Último día que peleó (`None` si nunca).
        strength: 💪 Fuerza (0 a 100).
        speed: 🪶 Velocidad (0 a 100).
        endurance: ❤️ Aguante (0 a 100); rebaja la probabilidad de morir.
        deaths: Gallos que ha perdido el dueño (no se reinicia al morir uno).
        last_train: Instante (epoch) del último entrenamiento del dueño, 0 si ninguno.
        train_day: Día canario del último entrenamiento.
        trains_today: Entrenamientos de ese día. La espera y el tope diario son del
            dueño, no del gallo: un gallo nuevo no se salta el cupo.
        form: Forma guardada en `form_at` (0 a `FORM_MAX`); la de ahora sale de
            `form_now`, que descuenta lo que baja con el tiempo.
        form_at: Instante (epoch) en que se guardó `form`.
        combo: Sesiones buenas seguidas del día `combo_day` (`gallos_gym.after_session`).
        combo_day: Día canario del combo.
        injured_until: Instante (epoch) hasta el que el gallo está lesionado y no pelea
            (0 si no lo está).
        grudge_rival: Rival de la casa al que le lleva `grudge_wins` victorias seguidas.
        grudge_wins: Victorias seguidas contra él; desde `GRUDGE_WINS`, está picado.
    """

    name: str
    fights: int = 0
    wins: int = 0
    kos: int = 0
    streak: int = 0
    best_streak: int = 0
    day_streak: int = 0
    last_day: date | None = None
    strength: int = 0
    speed: int = 0
    endurance: int = 0
    deaths: int = 0
    last_train: float = 0.0
    train_day: date | None = None
    trains_today: int = 0
    form: float = 0.0
    form_at: float = 0.0
    combo: int = 0
    combo_day: date | None = None
    injured_until: float = 0.0
    grudge_rival: str = ""
    grudge_wins: int = 0

    @property
    def losses(self) -> int:
        """Derrotas."""
        return self.fights - self.wins

    def stat(self, stat: Stat) -> int:
        """Valor de un stat."""
        return int(getattr(self, stat.column))

    @property
    def power(self) -> float:
        """Poder: la media de los tres stats (0 a 100)."""
        return (self.strength + self.speed + self.endurance) / 3


class TrainError(ValueError):
    """No se puede entrenar ahora; el mensaje se enseña tal cual."""


def train_price(level: int) -> int:
    """Base imponible de entrenar un stat que está en `level`: 150 + 4 · nivel, a la decena.

    150 Y$ a 0, 310 a 40 y 550 a 100 (con el stat al máximo se sigue entrenando
    por la forma, a ese precio). Plano a propósito: cada sesión también da forma,
    que es lo que hace rentable al gallo, y el mantenimiento diario no puede
    comerse lo que deja (`docs/gallos_sim.py`).
    """
    return (150 + 4 * level + 5) // 10 * 10


def train_gain(level: int) -> int:
    """Lo que sube un stat en `level`: +3 por debajo de 40, +2 por debajo de 70, +1 hasta 100."""
    if level >= STAT_MAX:
        return 0
    return 3 if level < 40 else 2 if level < 70 else 1


def trains_today(record: RoosterRecord, day: date) -> int:
    """Entrenamientos que ya lleva el dueño hoy (día canario)."""
    return record.trains_today if record.train_day == day else 0


def check_training(record: RoosterRecord, stat: Stat, *, now: float, day: date) -> None:
    """Comprueba que se puede entrenar `stat` ahora.

    Un stat al máximo se puede entrenar: no sube, pero da forma.

    Raises:
        TrainError: Si hace falta esperar a la hora de descanso o se ha llegado
            al tope del día.
    """
    if trains_today(record, day) >= TRAIN_PER_DAY:
        raise TrainError(
            f"Hoy ya van {TRAIN_PER_DAY} entrenamientos. Hasta Rocky descansaba el domingo."
        )
    wait = record.last_train + TRAIN_COOLDOWN - now
    if wait > 0:
        raise TrainError(
            f"Tu gallo está agotado: descansa {math.ceil(wait / 60)} min más antes de volver "
            "al gimnasio."
        )


def after_train(record: RoosterRecord, stat: Stat, *, now: float, day: date) -> RoosterRecord:
    """La ficha tras pagar un entrenamiento: sube el stat (sin pasar de `STAT_MAX`) y la forma.

    Solo la forma que da el pago (`FORM_ON_PAYMENT`); la del minijuego la suma
    `gallos_gym.after_session` al acabarlo.
    """
    level = record.stat(stat)
    trained = replace(
        record,
        **{stat.column: min(STAT_MAX, level + train_gain(level))},
        last_train=now,
        train_day=day,
        trains_today=trains_today(record, day) + 1,
    )
    return with_form(trained, form_now(record, now) + PAID_FORM, now)


def death_chance(endurance: int, form: float) -> float:
    """Probabilidad de morir al perder por KO.

    `DEATH_CHANCE` (10 %), rebajada por el aguante hasta la mitad (aguante 100) y
    multiplicada por la forma, de `TIRED_DEATH` (×1,5, agotado) a `FIT_DEATH`
    (×0,25, forma al máximo): pelear cansado es lo arriesgado. Sin aguante va
    del 15 % al 2,5 %; con aguante 100, del 7,5 % al 1,25 %.
    """
    tired = TIRED_DEATH - (TIRED_DEATH - FIT_DEATH) * min(FORM_MAX, max(0.0, form)) / FORM_MAX
    return DEATH_CHANCE * (1 - endurance / 200) * tired


def dies(
    record: RoosterRecord,
    rng: random.Random,
    form: float,
    *,
    deadly: bool = False,
    grudge: bool = False,
) -> bool:
    """Sortea si el gallo `record`, con forma `form`, muere tras perder por KO.

    ☠️ A muerte, muere seguro (sin sorteo); contra un rival picado, la
    probabilidad se multiplica por `GRUDGE_DEATH`.
    """
    if deadly:
        return True
    return rng.random() < death_chance(record.endurance, form) * (GRUDGE_DEATH if grudge else 1)


#: Perder por KO agotado (forma por debajo de esto) y sobrevivir puede lesionar.
INJURY_FORM = 20
#: Probabilidad de lesionarse así y horas que dura la lesión.
FIGHT_INJURY_CHANCE = 0.25
INJURY_HOURS = 6


def injured(record: RoosterRecord, now: float) -> bool:
    """Si el gallo está lesionado en `now` (no puede pelear, sí entrenar)."""
    return record.injured_until > now


def injury_text(record: RoosterRecord, now: float) -> str:
    """El aviso de que no puede pelear, con lo que le queda (en horas o minutos)."""
    left = max(0.0, record.injured_until - now)
    time = f"{math.ceil(left / 3600)} h" if left >= 3600 else f"{math.ceil(left / 60)} min"
    return f"🤕 **{record.name}** está lesionado: no puede pelear hasta dentro de {time}."


def injure(record: RoosterRecord, now: float, hours: float = INJURY_HOURS) -> RoosterRecord:
    """La ficha con una lesión de `hours` horas desde `now` (o la que ya tenía, si dura más)."""
    return replace(record, injured_until=max(record.injured_until, now + hours * 3600))


def fight_injury(form: float, rng: random.Random) -> bool:
    """Tras perder por KO sin morir: si se lesiona por haber peleado agotado."""
    return form < INJURY_FORM and rng.random() < FIGHT_INJURY_CHANCE


def stat_bar(value: int, width: int = 10) -> str:
    """Barra de un stat: `██████░░░░` para 60 de 100."""
    filled = round(width * value / STAT_MAX)
    return "█" * filled + "░" * (width - filled)


@dataclass(frozen=True, slots=True)
class Fallen:
    """Un gallo caído, tal como queda en el cementerio.

    Attributes:
        name: Su nombre.
        fights: Peleas que llegó a dar.
        wins: Victorias.
        kos: Victorias por KO.
        strength: Fuerza con la que murió.
        speed: Velocidad con la que murió.
        endurance: Aguante con el que murió.
        died_at: Instante (epoch) de la muerte.
        killer_kind: `casa` si lo mató un rival de la casa o `miembro` si fue el
            gallo de otro miembro en un duelo.
        killer: Clave del rival (`Rival.key`) o ID del miembro que lo mató.
    """

    name: str
    fights: int
    wins: int
    kos: int
    strength: int
    speed: int
    endurance: int
    died_at: float
    killer_kind: str
    killer: str

    @property
    def power(self) -> float:
        """Su poder cuando murió."""
        return (self.strength + self.speed + self.endurance) / 3


def after_fight(
    record: RoosterRecord,
    *,
    won: bool,
    finish: Finish,
    day: date,
    now: float | None = None,
    rival: str | None = None,
) -> RoosterRecord:
    """La ficha tras una pelea que acaba `finish` el día `day`.

    Con `now` (las peleas contra la casa) gasta `FORM_PER_FIGHT` de forma. Con
    `rival` (la clave del de la casa) lleva el pique: ganarle suma una victoria
    seguida contra él (o empieza la cuenta, si era otro) y perder la corta.
    """
    streak = record.streak + 1 if won else 0
    if record.last_day == day:
        day_streak = record.day_streak
    elif record.last_day is not None and (day - record.last_day).days == 1:
        day_streak = record.day_streak + 1
    else:
        day_streak = 1
    fought = (
        record if now is None else with_form(record, form_now(record, now) - FORM_PER_FIGHT, now)
    )
    if rival is not None:
        if not won:
            fought = replace(fought, grudge_rival="", grudge_wins=0)
        elif record.grudge_rival == rival:
            fought = replace(fought, grudge_wins=record.grudge_wins + 1)
        else:
            fought = replace(fought, grudge_rival=rival, grudge_wins=1)
    return replace(
        fought,
        fights=record.fights + 1,
        wins=record.wins + int(won),
        kos=record.kos + int(won and finish is Finish.KO),
        streak=streak,
        best_streak=max(record.best_streak, streak),
        day_streak=day_streak,
        last_day=day,
    )


def default_name(user_id: int) -> str:
    """Nombre de serie de un gallo: siempre el mismo para el mismo dueño."""
    return DEFAULT_NAMES[user_id % len(DEFAULT_NAMES)]


def fallen_from(record: RoosterRecord, *, died_at: float, killer_kind: str, killer: str) -> Fallen:
    """La entrada del cementerio para un gallo que muere con la ficha `record`."""
    return Fallen(
        name=record.name,
        fights=record.fights,
        wins=record.wins,
        kos=record.kos,
        strength=record.strength,
        speed=record.speed,
        endurance=record.endurance,
        died_at=died_at,
        killer_kind=killer_kind,
        killer=killer,
    )


def reborn(record: RoosterRecord, user_id: int) -> RoosterRecord:
    """La ficha del gallo que sustituye al que muere: nombre de serie, stats e historial a cero.

    Se queda con lo que es del dueño y no del gallo: cuántos ha perdido (uno
    más) y su cupo de entrenamientos. La skin vive en la tienda y no se pierde.
    """
    return RoosterRecord(
        name=default_name(user_id),
        deaths=record.deaths + 1,
        combo=record.combo,
        combo_day=record.combo_day,
        last_train=record.last_train,
        train_day=record.train_day,
        trains_today=record.trains_today,
    )


_MARKUP = re.compile(r"[`*_~|<>@#\\]")


def clean_name(text: str) -> str:
    """Valida el nombre que pone el dueño: sin menciones ni formato, como mucho `NAME_MAX`.

    Raises:
        ValueError: Con un mensaje mostrable si queda vacío o es demasiado largo.
    """
    name = " ".join(_MARKUP.sub("", text).split())
    if not name:
        raise ValueError("Ponle un nombre con letras de verdad.")
    if len(name) > NAME_MAX:
        raise ValueError(f"Como mucho {NAME_MAX} caracteres: es un gallo, no un decreto.")
    return name


# -- Narración ---------------------------------------------------------------------------


def move_name(exchange: Exchange, signature: Mapping[Corner, str]) -> str:
    """Nombre del golpe de un intercambio (el estrella si es la patada de un rival)."""
    if exchange.move == SIGNATURE_MOVE and exchange.attacker in signature:
        return signature[exchange.attacker]
    return MOVE_BY_KEY[exchange.move].name


def narrate(
    fight: Fight, names: Mapping[Corner, str], signature: Mapping[Corner, str] | None = None
) -> list[str]:
    """Las frases de la crónica: el golpe más gordo, el final y cómo acabó."""
    signature = signature or {}
    lines: list[str] = []
    landed = [e for e in fight.exchanges if not e.dodged]
    if landed:
        best = max(landed, key=lambda e: e.damage)
        crit = " **crítico**" if best.crit else ""
        lines.append(
            f"💥 El golpe de la noche: {move_name(best, signature)}{crit} de "
            f"**{names[best.attacker]}** (−{best.damage})."
        )
    dodges = {c: fight.dodges(c) for c in Corner}
    agile = max(Corner, key=lambda c: dodges[c])
    if dodges[agile] >= 3:
        lines.append(f"🌀 **{names[agile]}** esquivó {dodges[agile]} golpes. Ni el Falcon.")
    last = fight.exchanges[-1] if fight.exchanges else None
    winner, loser = names[fight.winner], names[fight.loser]
    if fight.finish is Finish.KO and last is not None:
        lines.append(
            f"🥊 {move_name(last, signature)} de **{winner}** y **{loser}** ve estrellitas. "
            f"KO en el asalto {fight.rounds}."
        )
    elif fight.finish is Finish.FLED:
        lines.append(f"🏃 **{loser}** sale por patas del reñidero. **{winner}** canta victoria.")
    else:
        lines.append(
            f"⚖️ Tres asaltos y a los jueces: **{winner}** gana a los puntos "
            f"({fight.hp(fight.winner)} a {fight.hp(fight.loser)} de vida)."
        )
    return lines
