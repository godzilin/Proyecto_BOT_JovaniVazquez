"""Reglas de las mascotas, sin Discord ni base de datos.

Las mascotas se adoptan en la tienda (pestaña 🐾 Mascotas) o aparecen solas
(`Species.spawn`). Se pueden tener todas las que se quiera, pero solo una va
contigo: la **activa**, que es la que sale en los mensajes del bot y la que
da el bonus de XP. El catálogo de especies vive en
`bot.services.pets_catalog`.

**Cuidarlas suma, descuidarlas no resta.** Acariciar, jugar y dar de comer
suben el **vínculo** (`bond`), que nunca baja. Cada cuidado da vínculo solo
las primeras veces de cada día (`CARE_RULES`) y tiene una espera corta entre
uso y uso. Con el vínculo sube el nivel (`BOND_LEVELS`), y con el nivel:

- **Bonus de XP pequeño** (`xp_bonus`): +0,5 % por nivel, hasta +5 % a nivel
  10. Las que tienen un canal favorito (`xp_focus`: el canario canta en voz,
  el loro habla por escrito) dan el doble ahí y nada en el otro.
- **Trucos** (`Species.tricks`): frases nuevas al jugar, a nivel 3, 6 y 9.

**Regalos.** El primer cuidado del día de cada miembro puede traer un objeto
del colmado (`GIFT_CHANCE`): un coleccionable barato, de existencias
ilimitadas y sin uso, como el premio de la caja botín. Nunca dinero, así que
no hay que decidir tratamiento fiscal (Biblia, sección 4).

**Cameos.** Cada funcionalidad avisa de lo que acaba de pasar con un `Moment`
(ganar, quedarse a cero, cobrar el IMV…) y la mascota activa decide si dice
algo. En los momentos sonados (`NOTABLE`) sale siempre; en el resto, de vez
en cuando (`CAMEO_CHANCE`, más o menos según lo habladora que sea la especie)
y nunca dos veces seguidas en menos de `CAMEO_GAP` segundos.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from bot.services.levels import TIMEZONE
from bot.services.taxes import TAX_COLLECTOR

# -- Momentos -------------------------------------------------------------------------


class Event(StrEnum):
    """Lo que acaba de pasarle al dueño. El valor es estable (frases y logros)."""

    WIN = "win"
    BIG_WIN = "big_win"
    LOSE = "lose"
    BUST = "bust"
    MONEY = "money"
    WORK = "work"
    IMV = "imv"
    TAX = "tax"
    BUY = "buy"
    USE = "use"
    LEVEL = "level"
    ACHIEVEMENT = "achievement"
    LOTTERY_WIN = "lottery_win"
    LOTTERY_LOSE = "lottery_lose"
    BIZUM = "bizum"
    BIRTHDAY = "birthday"
    BEER = "beer"
    PROFILE = "profile"


class Mood(StrEnum):
    """Cómo le sienta a la mascota: cada especie tiene frases para los tres."""

    GOOD = "good"
    BAD = "bad"
    NEUTRAL = "neutral"


MOOD: dict[Event, Mood] = {
    Event.WIN: Mood.GOOD,
    Event.BIG_WIN: Mood.GOOD,
    Event.LEVEL: Mood.GOOD,
    Event.ACHIEVEMENT: Mood.GOOD,
    Event.LOTTERY_WIN: Mood.GOOD,
    Event.BIRTHDAY: Mood.GOOD,
    Event.LOSE: Mood.BAD,
    Event.BUST: Mood.BAD,
    Event.TAX: Mood.BAD,
    Event.LOTTERY_LOSE: Mood.BAD,
}
#: Momentos en los que la mascota activa sale siempre. Subir de nivel y los
#: logros pasan a menudo: ahí sale de vez en cuando, como en una jugada normal.
NOTABLE: frozenset[Event] = frozenset({Event.BIG_WIN, Event.BUST, Event.LOTTERY_WIN})
#: Premio a partir del cual ganar es «pelotazo», aunque la apuesta fuera grande.
BIG_WIN_MIN = 1_000
#: Veces la apuesta que hay que ganar para que sea pelotazo.
BIG_WIN_TIMES = 10
#: Premio que es pelotazo siempre, apueste lo que apueste.
BIG_WIN_ALWAYS = 100_000


@dataclass(frozen=True, slots=True)
class Moment:
    """Lo que ha pasado, para que la mascota activa reaccione.

    Attributes:
        event: Qué ha pasado.
        notable: Si la mascota debe salir sí o sí (un all-in, un pelotazo).
    """

    event: Event = Event.MONEY
    notable: bool = False

    @property
    def mood(self) -> Mood:
        """Cómo le sienta a la mascota."""
        return MOOD.get(self.event, Mood.NEUTRAL)

    @property
    def loud(self) -> bool:
        """Si la mascota sale siempre."""
        return self.notable or self.event in NOTABLE


def bet_moment(*, stake: int, net: int, balance_after: int, all_in: bool = False) -> Moment:
    """El momento de una jugada del casino, a partir de sus cifras.

    Quedarse a cero tras perder es `BUST`; ganar diez veces la apuesta (y al
    menos `BIG_WIN_MIN`), o más de `BIG_WIN_ALWAYS`, es `BIG_WIN`. Un all-in
    siempre es sonado.

    Args:
        stake: Lo apostado.
        net: Lo ganado menos lo apostado (negativo si pierde).
        balance_after: Saldo tras la jugada.
        all_in: Si se jugó todo el saldo.
    """
    if net < 0:
        if balance_after <= 0:
            return Moment(Event.BUST, notable=True)
        return Moment(Event.LOSE, notable=all_in)
    if net >= BIG_WIN_ALWAYS or (net >= BIG_WIN_MIN and net >= BIG_WIN_TIMES * stake):
        return Moment(Event.BIG_WIN, notable=True)
    if net > 0:
        return Moment(Event.WIN, notable=all_in)
    return Moment(Event.MONEY, notable=all_in)


# -- Vínculo --------------------------------------------------------------------------

#: Vínculo necesario para cada nivel (el índice es el nivel). Nunca baja.
BOND_LEVELS: tuple[int, ...] = (0, 10, 30, 60, 100, 150, 220, 300, 400, 520, 666)
MAX_BOND_LEVEL = len(BOND_LEVELS) - 1
#: Niveles en los que la mascota aprende cada uno de sus tres trucos.
TRICK_LEVELS: tuple[int, ...] = (3, 6, 9)
#: Bonus de XP por nivel de vínculo, en tanto por uno.
XP_BONUS_PER_LEVEL = 0.005


def bond_level(bond: int) -> int:
    """Nivel de vínculo (0 a `MAX_BOND_LEVEL`) para unos puntos."""
    return sum(1 for need in BOND_LEVELS[1:] if bond >= need)


def next_level_at(bond: int) -> int | None:
    """Vínculo que hace falta para el siguiente nivel, o `None` si ya es el máximo."""
    level = bond_level(bond)
    return None if level >= MAX_BOND_LEVEL else BOND_LEVELS[level + 1]


def xp_bonus(level: int, focus: str, *, voice: bool) -> float:
    """Multiplicador de XP de la mascota activa (1,0 sin bonus).

    Args:
        level: Nivel de vínculo.
        focus: Canal favorito de la especie: `"todo"`, `"mensajes"` o `"voz"`.
        voice: Si el XP es de voz (si no, de mensajes).
    """
    base = level * XP_BONUS_PER_LEVEL
    if focus == "todo":
        return 1.0 + base
    favourite = (focus == "voz") == voice
    return 1.0 + (2 * base if favourite else 0.0)


def unlocked_tricks(level: int) -> int:
    """Cuántos trucos sabe la mascota a ese nivel de vínculo."""
    return sum(1 for need in TRICK_LEVELS if level >= need)


# -- Cuidados -------------------------------------------------------------------------


class Care(StrEnum):
    """Lo que se le puede hacer a una mascota. El valor es estable (logros)."""

    PET = "acariciar"
    PLAY = "jugar"
    FEED = "comer"


@dataclass(frozen=True, slots=True)
class CareRule:
    """Lo que da un cuidado.

    Attributes:
        points: Vínculo que suma.
        daily: Veces al día que suma vínculo; las siguientes solo dan la frase.
        cooldown: Segundos entre un cuidado de este tipo y el siguiente.
    """

    points: int
    daily: int
    cooldown: int


CARE_RULES: dict[Care, CareRule] = {
    Care.PET: CareRule(points=1, daily=3, cooldown=10 * 60),
    Care.PLAY: CareRule(points=2, daily=2, cooldown=60 * 60),
    Care.FEED: CareRule(points=3, daily=2, cooldown=3 * 60 * 60),
}
#: Vínculo extra al darle su comida favorita.
FAVOURITE_BONUS = 3
#: Probabilidad de que el primer cuidado del día traiga un regalo.
GIFT_CHANCE = 0.25
#: Precio máximo de lo que trae una mascota: cosas pequeñas, nunca un yate.
GIFT_MAX_PRICE = 1_000


def care_day(when: float) -> str:
    """Día natural en hora canaria (`2026-10-07`): los cuidados se cuentan por día."""
    return datetime.fromtimestamp(when, TIMEZONE).date().isoformat()


@dataclass(slots=True)
class PetState:
    """Lo que se guarda de una mascota.

    Attributes:
        id: Fila del inventario de la tienda (`shop_inventory.id`).
        species: Clave de la especie (`bot.services.pets_catalog.SPECIES`).
        name: Nombre que le ha puesto su dueño (vacío: el de la especie).
        bond: Vínculo acumulado; nunca baja.
        day: Último día con algún cuidado (`care_day`).
        today: Cuidados de ese día que han sumado vínculo, por tipo.
        last: Último cuidado de cada tipo (epoch), para las esperas.
        totals: Cuidados de cada tipo desde siempre.
    """

    id: int
    guild_id: int
    user_id: int
    species: str
    name: str = ""
    bond: int = 0
    adopted_at: float = 0.0
    day: str = ""
    today: dict[str, int] = field(default_factory=dict)
    last: dict[str, float] = field(default_factory=dict)
    totals: dict[str, int] = field(default_factory=dict)

    @property
    def level(self) -> int:
        """Nivel de vínculo."""
        return bond_level(self.bond)


@dataclass(frozen=True, slots=True)
class CareOutcome:
    """Lo que ha dado un cuidado.

    Attributes:
        wait: Segundos que faltan si aún no se puede (y entonces nada más cuenta).
        points: Vínculo sumado (0 si ya se ha cuidado lo bastante hoy).
        level_up: Nivel nuevo si ha subido; `None` si no.
        new_trick: Si con este nivel aprende un truco.
    """

    wait: int = 0
    points: int = 0
    level_up: int | None = None
    new_trick: bool = False


def cooldown_left(state: PetState, action: Care, now: float) -> int:
    """Segundos que faltan para poder repetir un cuidado (0 si ya se puede)."""
    last = state.last.get(action.value)
    if last is None:
        return 0
    return max(0, int(last + CARE_RULES[action].cooldown - now + 0.999))


def apply_care(
    state: PetState, action: Care, now: float, *, favourite: bool = False
) -> CareOutcome:
    """Aplica un cuidado a `state` (lo cambia) y dice lo que ha dado.

    No resta nunca: si se ha pasado del cupo del día, el cuidado sale igual
    pero no suma vínculo.

    Args:
        favourite: Si le ha dado su comida favorita (solo `Care.FEED`).
    """
    wait = cooldown_left(state, action, now)
    if wait:
        return CareOutcome(wait=wait)
    day = care_day(now)
    if state.day != day:
        state.day, state.today = day, {}
    rule = CARE_RULES[action]
    done = state.today.get(action.value, 0)
    points = 0
    if done < rule.daily:
        points = rule.points + (FAVOURITE_BONUS if favourite and action is Care.FEED else 0)
        state.today[action.value] = done + 1
    before = state.level
    state.bond += points
    state.last[action.value] = now
    state.totals[action.value] = state.totals.get(action.value, 0) + 1
    after = state.level
    level_up = after if after > before else None
    new_trick = level_up is not None and unlocked_tricks(after) > unlocked_tricks(before)
    return CareOutcome(points=points, level_up=level_up, new_trick=new_trick)


#: Frases de cada cuidado. `{pet}`, `{sound}` y, al comer, `{food}`.
CARE_LINES: dict[Care, tuple[str, ...]] = {
    Care.PET: (
        "{pet} se deja acariciar. «{sound}», dice.",
        "{pet} cierra los ojos mientras le rascas detrás de las orejas. O de donde toque.",
        "{pet} se acurruca en tu mano. Ni {collector} podría estropear este momento.",
    ),
    Care.PLAY: (
        "{pet} corre, salta y lo tira todo. Ha sido un éxito.",
        "{pet} juega contigo hasta que no puede más. «{sound}».",
        "{pet} persigue algo que solo ve {pet}. Tú haces como que también.",
    ),
    Care.FEED: (
        "{pet} se come {food} en tres segundos. Ni las gracias.",
        "{pet} huele {food}, te mira y se lo come. Aprobado.",
        "{pet} se come {food} y se queda mirando por si hay más.",
    ),
}
FAVOURITE_LINE = "{pet} pierde la cabeza con {food}: ¡su comida favorita!"
NO_FOOD_LINE = "{pet} no come. Le dejas la comida al lado y no pasa nada. Te lo agradece igual."
TRICK_LINE = "🎪 {pet} hace un truco: {trick}"


def care_text(
    action: Care,
    rng: random.Random,
    *,
    name: str,
    sound: str,
    food: str | None = None,
    favourite: bool = False,
    trick: str | None = None,
    eats: bool = True,
) -> str:
    """La frase de un cuidado, con el nombre en negrita.

    Args:
        food: Lo que se ha comido, ya con su emoji (solo `Care.FEED`).
        favourite: Si era su comida favorita.
        trick: Truco que hace al jugar, si ya sabe alguno y le toca.
        eats: Si la especie come (la piedra y el mosquito no).
    """
    if action is Care.FEED and not eats:
        template = NO_FOOD_LINE
    elif action is Care.FEED and favourite:
        template = FAVOURITE_LINE
    else:
        template = rng.choice(CARE_LINES[action])
    text = template.format(pet=f"**{name}**", sound=sound, food=food or "", collector=TAX_COLLECTOR)
    if trick:
        text += "\n" + TRICK_LINE.format(pet=f"**{name}**", trick=trick[0].lower() + trick[1:])
    return text


def streak_after(last_day: str, streak: int, today: str) -> int:
    """Racha de días seguidos cuidando alguna mascota tras cuidar hoy.

    Args:
        last_day: Último día con algún cuidado (`care_day`), o vacío.
        streak: Racha guardada hasta ese día.
        today: Día de hoy (`care_day`).
    """
    if last_day == today:
        return max(streak, 1)
    if last_day:
        gap = (datetime.fromisoformat(today) - datetime.fromisoformat(last_day)).days
        if gap == 1:
            return streak + 1
    return 1


def gift_weight(price: int) -> float:
    """Peso de un regalo: lo barato sale mucho más (calcetines, pilas, piedras)."""
    return 1 / max(1, price) ** 0.5


def pick_gift(prices: list[int], rng: random.Random) -> int:
    """Índice del regalo que trae la mascota."""
    weights = [gift_weight(p) for p in prices]
    return rng.choices(range(len(prices)), weights=weights, k=1)[0]


# -- Nombres --------------------------------------------------------------------------

MAX_NAME = 24
_FORBIDDEN = set("@<>`*_~|#\\")


def clean_name(text: str | None) -> str:
    """Nombre de una mascota, limpio.

    Sin menciones ni formato de Discord: el nombre sale en mensajes del canal.

    Raises:
        ValueError: Si queda vacío, es demasiado largo o lleva caracteres
            que Discord interpretaría.
    """
    name = " ".join((text or "").split())
    if not name:
        raise ValueError("El nombre no puede quedar vacío, mi amor.")
    if len(name) > MAX_NAME:
        raise ValueError(f"Como mucho {MAX_NAME} caracteres. No es un DNI.")
    if any(c in _FORBIDDEN for c in name):
        raise ValueError("Sin @, <, >, asteriscos ni cosas raras: solo un nombre.")
    return name


# -- Cameos ---------------------------------------------------------------------------

#: Probabilidad de que la mascota diga algo en un momento normal.
CAMEO_CHANCE = 0.15
#: Segundos mínimos entre dos cameos normales del mismo dueño (los sonados no esperan).
CAMEO_GAP = 45

#: Frases de cualquier especie para cada momento. `{pet}` es el nombre y
#: `{sound}`, el ruido que hace la especie.
GENERIC_LINES: dict[Event, tuple[str, ...]] = {
    Event.BUST: (
        "{pet} mira tu saldo, te mira a ti y se va a buscar un dueño con IMV.",
        "{pet} se tumba encima de tu cartera vacía. Al menos está calentita.",
        "{pet} te ofrece su comida para que no pases hambre. Qué vergüenza, mi amor.",
    ),
    Event.BIG_WIN: (
        "{pet} se pone a dar vueltas de la emoción. {collector} también.",
        "{pet} exige su parte del premio. En chuches, que no tributan.",
        "{pet} ya está mirando yates en la tienda.",
    ),
    Event.WIN: (
        "{pet} aprueba la jugada con un {sound}.",
        "{pet} se acerca a oler las fichas ganadas.",
    ),
    Event.LOSE: (
        "{pet} hace como que no ha visto nada.",
        "{pet} se acurruca a tu lado. Se nota que lo necesitas.",
    ),
    Event.TAX: (
        "{pet} le gruñe a {collector}. {collector} le retiene el 19 % del gruñido.",
        "{pet} se esconde debajo del sofá cuando llega la carta de Hacienda.",
    ),
    Event.IMV: (
        "{pet} te acompaña a cobrar la paguita. Su parte, en pienso.",
        "{pet} hace cola contigo en la ventanilla del IMV.",
    ),
    Event.WORK: (
        "{pet} te espera en la puerta del curro.",
        "{pet} se ha dormido encima de tu nómina.",
    ),
    Event.LEVEL: (
        "{pet} celebra tu nivel nuevo como si fuera suyo. En parte lo es.",
        "{pet} te felicita por subir de nivel con un {sound} de los buenos.",
    ),
    Event.ACHIEVEMENT: (
        "{pet} pone cara de «ya era hora».",
        "{pet} se apunta el logro también. Trabajo en equipo.",
    ),
    Event.LOTTERY_WIN: (
        "{pet} intenta comerse el décimo premiado. Lo has salvado por los pelos.",
        "{pet} ya sabe en qué se va a gastar tu premio.",
    ),
    Event.LOTTERY_LOSE: (
        "{pet} rompe el décimo a mordiscos. Total, ya no servía.",
        "{pet} te mira como diciendo «la próxima, al cupón».",
    ),
    Event.BUY: (
        "{pet} olfatea la bolsa de la compra a ver si hay algo de comer.",
        "{pet} se mete en la caja de lo que acabas de comprar. La caja le gusta más.",
    ),
    Event.USE: ("{pet} mira lo que acabas de hacer y no quiere saber nada.",),
    Event.BIZUM: ("{pet} pregunta si el Bizum era para comprarle algo.",),
    Event.BIRTHDAY: ("{pet} te canta el cumpleaños a su manera: {sound}.",),
    Event.MONEY: ("{pet} cuenta tus yapdólares con la pata.",),
    Event.BEER: (
        "{pet} te mira beber y apunta otro sorbo en su libreta.",
        "{pet} brinda contigo a su manera: {sound}.",
        "{pet} no bebe, pero te juzga en silencio.",
        "{pet} se acerca a olisquear el vaso y vuelve con cara de «¿otra vez?».",
    ),
    Event.PROFILE: (
        "{pet} se asoma a tu perfil y busca su foto. No sale. Se ofende.",
        "{pet} lee tu vida laboral y suspira como un funcionario de la Seguridad Social.",
        "{pet} cree que tu perfil necesita más logros y menos casino.",
        "{pet} pide salir en el resumen. Lo ve justo: sin {pet} no eres nadie.",
    ),
}


def wants_cameo(
    talk: float,
    moment: Moment,
    rng: random.Random,
    *,
    hour: int,
    night_owl: bool = False,
    since_last: float | None = None,
) -> bool:
    """Si la mascota activa dice algo en este momento.

    Args:
        talk: Lo habladora que es la especie (1,0 normal; el loro, 2,0).
        hour: Hora local del dueño (0-23).
        night_owl: Si la especie se anima de madrugada (0:00 a 6:00).
        since_last: Segundos desde su último cameo, si lo hubo.
    """
    if moment.loud:
        return True
    if since_last is not None and since_last < CAMEO_GAP:
        return False
    chance = CAMEO_CHANCE * talk * (2 if night_owl and hour < 6 else 1)
    return rng.random() < chance


def cameo_text(template: str, *, emoji: str, name: str, sound: str) -> str:
    """Una frase de mascota lista para el pie de un mensaje (`-# 🐈 **Michi** …`)."""
    text = template.format(pet=f"**{name}**", sound=sound, collector=TAX_COLLECTOR)
    return f"-# {emoji} {text}"


def pick_line(lines: dict[str, tuple[str, ...]], moment: Moment, rng: random.Random) -> str:
    """Frase para un momento: la propia de la especie si la tiene, o una de su ánimo.

    Mezcla las frases de la especie para ese momento (si tiene), las de su
    ánimo (`good`, `bad`, `neutral`) y las genéricas del momento, con más peso
    para lo propio, que es lo que le da personalidad.
    """
    own = lines.get(moment.event.value, ())
    mood = lines.get(moment.mood.value, ())
    generic = GENERIC_LINES.get(moment.event, ())
    pool = [(line, 4) for line in own] + [(line, 2) for line in mood]
    pool += [(line, 1) for line in generic]
    if not pool:
        pool = [(line, 1) for line in GENERIC_LINES[Event.MONEY]]
    texts, weights = zip(*pool, strict=True)
    return rng.choices(texts, weights=weights, k=1)[0]


# -- Apariciones ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Spawn:
    """Cuándo aparece sola una especie que no se vende.

    Attributes:
        events: Momentos en los que puede aparecer (vacío: cualquiera).
        chance: Probabilidad cada vez que se da el momento.
        months: Meses en los que puede aparecer (vacío: todo el año).
        day: `(mes, día)` concreto, si solo aparece ese día.
        hint: Pista de cómo se consigue, para `mascota`.
    """

    events: frozenset[Event] = frozenset()
    chance: float = 0.05
    months: frozenset[int] = frozenset()
    day: tuple[int, int] | None = None
    hint: str = ""

    def fires(self, moment: Moment, when: datetime, rng: random.Random) -> bool:
        """Si aparece en este momento (antes de mirar si ya la tiene)."""
        if self.events and moment.event not in self.events:
            return False
        if self.months and when.month not in self.months:
            return False
        if self.day is not None and (when.month, when.day) != self.day:
            return False
        return rng.random() < self.chance
