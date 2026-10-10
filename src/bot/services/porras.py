"""Porras: apuestas entre miembros sobre las próximas jugadas de otro.

Lógica pura, sin Discord ni base de datos. El cog (`bot.cogs.porras`) pinta el
panel y mueve los botones; el dinero pasa por `EconomyService.porra_bet` y
`EconomyService.settle_porra`.

Cómo va una porra:

1. **Propuesta.** Luis le monta una porra a Ana: «¿Ana acaba ganando en sus
   próximas 3 partidas de minas de al menos 500 Y$?». Nadie puede montársela a sí
   mismo. Ana tiene que aceptarla (`ACCEPT_SECONDS`), porque se compromete a jugar.
2. **Apuestas** (`BETTING_SECONDS`). Cualquiera menos Ana apuesta a una de las
   opciones. Cada uno, a una sola opción por porra (puede subir lo que lleva).
   El bote total no pasa de `POOL_FACTOR` veces lo comprometido (apuesta mínima ×
   jugadas): `pool_cap`.
3. **En juego.** Al cerrar, cuentan las jugadas de Ana en ese juego, de al menos
   la apuesta mínima, que empiecen después del cierre (las que duran mandan
   `started` en `Play.details`). Los giros gratis y los bonus (apuesta 0) no
   cuentan. Tiene `play_deadline` segundos para jugarlas todas.
4. **Resolución.** Con las N jugadas, la propuesta dice qué opción gana
   (`Proposition.resolve`). Si Ana se queda sin saldo para la siguiente, se
   resuelve con las que lleva. Si no juega a tiempo, se anula y se devuelve todo.

El reparto es mutuo (parimutuel, como la Quiniela): no hay banca ni cuotas fijas.
De cada apuesta salen el 10 % de IAJ para el Estado (`taxes.gaming_tax`) y el
`IMAGE_SHARE` % para Ana por sus derechos de imagen; el resto se reparte entre
quienes acertaron en proporción a lo que pusieron (`split`). Si nadie acierta, o
al cerrar solo hay dinero en una opción, se devuelve todo sin comisiones.

Por qué el tope del bote. Ana decide en minas, el pollo o el blackjack, así que
podría perder a propósito para que gane un colega que apostó en contra. El tope
limita lo que se puede sacar así: como mucho `POOL_FACTOR` veces lo que Ana
arriesga. Con 5 no basta para que nunca salga a cuenta (decisión del proyecto,
para que haya porras con chicha), pero sí para que no se vaya de las manos.

Juegos. Valen todos los de `casino_stats.GAMES` menos los de `EXCLUDED_GAMES`: el
crash, que es una mesa compartida con su propio ritmo, y las propias porras. Un
juego nuevo que llame a `apuestas.record` tiene porras sin hacer nada más: las
propuestas genéricas solo usan apuesta, premio y saldo. Las específicas
(`Proposition.games`) piden claves de `Play.details` que el juego debe mandar;
una prueba lo comprueba.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

from bot.services.casino_stats import GAMES, REFERENCE_STAKE, Play
from bot.services.economy import format_amount
from bot.services.taxes import gaming_tax

#: Juegos sobre los que no se puede montar una porra.
EXCLUDED_GAMES = frozenset({"crash", "porra"})
#: Clave del juego en el libro (`porra:apuesta`) y en `GAMES`.
GAME = "porra"

#: El bote no pasa de este múltiplo de lo comprometido (apuesta mínima × jugadas).
POOL_FACTOR = 5
#: Parte del bote, en %, que cobra el protagonista por sus derechos de imagen.
IMAGE_SHARE = 2
#: Apuesta mínima en una porra.
MIN_BET = 10
#: Apuesta mínima por jugada que se puede exigir al protagonista: una tirada.
MIN_STAKE = REFERENCE_STAKE
#: Jugadas como mucho; con la libreta de la porra de la tienda, `MAX_PLAYS_NOTEBOOK`.
MAX_PLAYS = 5
MAX_PLAYS_NOTEBOOK = 10
#: Segundos que tiene el protagonista para aceptar.
ACCEPT_SECONDS = 120
#: Segundos que duran abiertas las apuestas tras aceptar.
BETTING_SECONDS = 90
#: Margen fijo y por jugada para jugarlas todas tras el cierre.
DEADLINE_BASE_SECONDS = 5 * 60
DEADLINE_PER_PLAY_SECONDS = 3 * 60

#: Claves de la tienda que cambian las porras (mismas que en `shop_catalog`).
BINOCULARS_KEY = "prismaticos_uco"
NOTEBOOK_KEY = "libreta_porra"


def allowed_games() -> dict[str, tuple[str, str]]:
    """Juegos con porra, con su emoji y nombre, en el orden de `GAMES`."""
    return {key: value for key, value in GAMES.items() if key not in EXCLUDED_GAMES}


def max_plays(owned: Iterable[str]) -> int:
    """Jugadas que puede pedir quien monta la porra, según lo que tiene de la tienda."""
    return MAX_PLAYS_NOTEBOOK if NOTEBOOK_KEY in set(owned) else MAX_PLAYS


def pool_cap(stake: int, plays: int) -> int:
    """Lo más que puede haber en el bote: `POOL_FACTOR` veces lo comprometido."""
    return POOL_FACTOR * stake * plays


def play_deadline(plays: int) -> int:
    """Segundos para jugar las `plays` jugadas tras el cierre."""
    return DEADLINE_BASE_SECONDS + DEADLINE_PER_PLAY_SECONDS * plays


# -- Propuestas --------------------------------------------------------------------------


def _wins(plays: Sequence[Play]) -> int:
    return sum(1 for play in plays if play.net > 0)


def _net(plays: Sequence[Play]) -> int:
    return sum(play.net for play in plays)


def _staked(plays: Sequence[Play]) -> int:
    return sum(play.stake for play in plays)


@dataclass(frozen=True, slots=True)
class Proposition:
    """Algo sobre lo que se apuesta.

    Attributes:
        key: Clave estable (se guarda y la usan los logros).
        emoji: Para el desplegable y el panel.
        name: Nombre corto para elegirla.
        question: Pregunta del panel, con `{who}` para el protagonista.
        options: Opciones, de la que es buena para el protagonista a la mala;
            `{n}` es el número de jugadas.
        decide: Recibe las jugadas y la apuesta mínima; devuelve el índice
            de la opción que gana.
        favourable: Índice de la opción que es buena para el protagonista, o
            `None` si ninguna lo es (las de «cuántas»).
        games: Juegos en los que tiene sentido (`None`: todos).
        details: Claves de `Play.details` que necesita.
        min_plays: Jugadas mínimas para que tenga sentido.
        counts: Si sus opciones son 0, 1… N victorias (se generan con `options_for`).
    """

    key: str
    emoji: str
    name: str
    question: str
    options: tuple[str, ...]
    decide: Callable[[Sequence[Play], int], int]
    favourable: int | None = 0
    games: frozenset[str] | None = None
    details: frozenset[str] = frozenset()
    min_plays: int = 1
    counts: bool = False

    def options_for(self, plays: int) -> tuple[str, ...]:
        """Las opciones de una porra de `plays` jugadas, ya con `{n}` puesto."""
        if self.counts:
            return tuple(f"{k} de {plays}" for k in range(plays + 1))
        return tuple(option.replace("{n}", str(plays)) for option in self.options)

    def fits(self, game: str, plays: int) -> bool:
        """Si se puede montar en ese juego y con esas jugadas."""
        return (self.games is None or game in self.games) and plays >= self.min_plays


def _decide_tieso(plays: Sequence[Play], stake: int) -> int:
    return 0 if plays and plays[-1].balance_after >= stake else 1


PROPOSITIONS: tuple[Proposition, ...] = (
    Proposition(
        "signo", "📈", "¿Gana o pierde?", "¿{who} acaba ganando dinero?",
        ("📈 Acaba ganando", "📉 No gana (pierde o se queda igual)"),
        lambda plays, _stake: 0 if _net(plays) > 0 else 1,
    ),
    Proposition(
        "pleno", "🏆", "¿Las gana todas?", "¿{who} gana las {n} jugadas?",
        ("🏆 Gana las {n}", "🙃 Falla alguna"),
        lambda plays, _stake: 0 if plays and _wins(plays) == len(plays) else 1,
        min_plays=2,
    ),
    Proposition(
        "cuantas", "🔢", "¿Cuántas gana?", "¿Cuántas de las {n} jugadas gana {who}?",
        (),
        lambda plays, _stake: _wins(plays),
        favourable=None,
        min_plays=2,
        counts=True,
    ),
    Proposition(
        "dobla", "🤑", "¿Dobla lo apostado?", "¿{who} gana más de lo que apuesta?",
        ("🤑 Dobla o más", "🫠 Se queda corto"),
        lambda plays, _stake: 0 if plays and _net(plays) >= _staked(plays) else 1,
    ),
    Proposition(
        "palo", "💸", "¿Se pega un palo?", "¿{who} pierde más de la mitad de lo que apuesta?",
        ("🛟 Salva más de la mitad", "💸 Pierde más de la mitad"),
        lambda plays, _stake: 1 if 2 * -_net(plays) > _staked(plays) else 0,
    ),
    Proposition(
        "gorda", "🎆", "¿Saca un ×5?", "¿{who} saca un premio de ×5 o más en alguna?",
        ("🎆 Saca un ×5 o más", "🥱 Nada de ×5"),
        lambda plays, _stake: (
            0 if any(p.stake and p.payout >= 5 * p.stake for p in plays) else 1
        ),
    ),
    Proposition(
        "tieso", "🪦", "¿Acaba tieso?", "¿A {who} le queda para otra ficha al acabar?",
        ("🫡 Le queda para otra", "🪦 Se queda tieso"),
        _decide_tieso,
    ),
    Proposition(
        "mina", "💣", "¿Pisa una mina?", "¿{who} sale entero de las {n} partidas de minas?",
        ("💎 Ni una mina", "💥 Pisa alguna"),
        lambda plays, _stake: 1 if any(p.detail("boom") for p in plays) else 0,
        games=frozenset({"minas"}),
        details=frozenset({"boom"}),
    ),
    Proposition(
        "atropello", "🚗", "¿Lo atropellan?", "¿{who} cruza vivo en las {n} partidas del pollo?",
        ("🐔 Cruza vivo", "🚗 Lo atropellan"),
        lambda plays, _stake: 1 if any(p.detail("splat") for p in plays) else 0,
        games=frozenset({"pollo"}),
        details=frozenset({"splat"}),
    ),
    Proposition(
        "racha", "🪙", "¿Llega a ×8?",
        "¿{who} acierta 3 seguidas en alguna de las {n} partidas a cara o cruz?",
        ("🪙 Llega a ×8", "🙃 Ni a ×8"),
        lambda plays, _stake: 0 if any(p.detail("wins") >= 3 for p in plays) else 1,
        games=frozenset({"moneda"}),
        details=frozenset({"wins"}),
    ),
    Proposition(
        "trayecto", "🚌", "¿Completa el autobús?",
        "¿{who} acierta las cuatro manos en alguna de las {n} partidas al autobús?",
        ("🚌 Llega al final", "🚏 Se baja antes"),
        lambda plays, _stake: 0 if any(p.detail("wins") >= 4 for p in plays) else 1,
        games=frozenset({"autobus"}),
        details=frozenset({"wins"}),
    ),
    Proposition(
        "punto", "🎯", "¿Hace algún punto?",
        "¿{who} repite el punto en alguna de las {n} partidas a los dados?",
        ("🎯 Hace un punto", "🎲 Ni uno"),
        lambda plays, _stake: 0 if any(p.detail("made") for p in plays) else 1,
        games=frozenset({"dados"}),
        details=frozenset({"made"}),
    ),
    Proposition(
        "pasarse", "💥", "¿Se pasa de 21?", "¿{who} juega las {n} manos sin pasarse de 21?",
        ("🧊 No se pasa nunca", "💥 Se pasa alguna vez"),
        lambda plays, _stake: 1 if any(p.detail("bust") for p in plays) else 0,
        games=frozenset({"blackjack"}),
        details=frozenset({"bust"}),
    ),
    Proposition(
        "natural", "🂡", "¿Saca blackjack?", "¿{who} saca un blackjack en alguna de las {n} manos?",
        ("🂡 Saca blackjack", "🃏 Ni uno"),
        lambda plays, _stake: 0 if any(p.detail("natural") for p in plays) else 1,
        games=frozenset({"blackjack"}),
        details=frozenset({"natural"}),
    ),
)  # fmt: skip
PROPOSITION_BY_KEY: dict[str, Proposition] = {p.key: p for p in PROPOSITIONS}


def propositions_for(game: str, plays: int) -> list[Proposition]:
    """Propuestas que se pueden montar en `game` con `plays` jugadas."""
    return [p for p in PROPOSITIONS if p.fits(game, plays)]


# -- Ciclo de vida -----------------------------------------------------------------------


class Status(StrEnum):
    """En qué punto está una porra. Los valores se guardan en la base de datos."""

    PROPOSED = "propuesta"
    OPEN = "abierta"
    LOCKED = "en_juego"
    RESOLVED = "resuelta"
    VOID = "anulada"

    @property
    def final(self) -> bool:
        """Si ya no va a cambiar."""
        return self in (Status.RESOLVED, Status.VOID)


class VoidReason(StrEnum):
    """Por qué se anula una porra. Se guarda y sale en el panel."""

    DECLINED = "rechazada"
    EXPIRED = "sin_respuesta"
    CANCELLED = "retirada"
    ONE_SIDED = "un_solo_lado"
    NO_SHOW = "espantada"
    RESTART = "reinicio"


VOID_TEXT: dict[VoidReason, str] = {
    VoidReason.DECLINED: "{who} no ha querido. Calabazas.",
    VoidReason.EXPIRED: "{who} no ha contestado a tiempo.",
    VoidReason.CANCELLED: "Quien la montó la ha retirado.",
    VoidReason.ONE_SIDED: "Todo el dinero estaba en la misma opción: así no hay porra.",
    VoidReason.NO_SHOW: "{who} no ha jugado a tiempo. Espantada de manual.",
    VoidReason.RESTART: "El bot se ha reiniciado a mitad.",
}


@dataclass(slots=True)
class Porra:
    """Una porra y lo que lleva jugado su protagonista.

    Attributes:
        id: Identificador en la base de datos (0 hasta guardarla).
        stake: Apuesta mínima por jugada que se exige al protagonista.
        plays: Jugadas que tiene que hacer.
        locked_at: Epoch del cierre de apuestas (las jugadas cuentan desde ahí).
        seen: Jugadas que ya cuentan, en orden.
    """

    id: int
    guild_id: int
    channel_id: int
    opener_id: int
    subject_id: int
    game: str
    proposition: str
    plays: int
    stake: int
    status: Status = Status.PROPOSED
    created_at: float = 0.0
    locked_at: float | None = None
    message_id: int | None = None
    outcome: int | None = None
    void_reason: VoidReason | None = None
    seen: list[Play] = field(default_factory=list)

    @property
    def prop(self) -> Proposition:
        """La propuesta."""
        return PROPOSITION_BY_KEY[self.proposition]

    @property
    def options(self) -> tuple[str, ...]:
        """Las opciones, ya con el número de jugadas."""
        return self.prop.options_for(self.plays)

    @property
    def cap(self) -> int:
        """Tope del bote."""
        return pool_cap(self.stake, self.plays)

    def counts(self, play: Play) -> bool:
        """Si una jugada terminada del protagonista cuenta para la porra.

        Cuenta si la porra está en juego, es del mismo juego, llega a la apuesta
        mínima y no empezó antes del cierre (si el juego dice cuándo empezó: una
        partida de minas abierta antes del cierre ya enseñaba casillas).
        """
        if self.status is not Status.LOCKED or self.locked_at is None or self.complete:
            return False
        if play.game != self.game or play.stake < self.stake:
            return False
        started = play.detail("started")
        return not (started and started < self.locked_at)

    @property
    def complete(self) -> bool:
        """Si ya no admite más jugadas: llegó a las pedidas o se quedó sin saldo.

        Entre la última jugada y el reparto puede colarse otra; esto la deja fuera.
        """
        if len(self.seen) >= self.plays:
            return True
        return bool(self.seen) and self.seen[-1].balance_after < self.stake

    def add(self, play: Play) -> bool:
        """Apunta una jugada que cuenta. Devuelve si la porra ya se puede resolver.

        Se resuelve al llegar a las jugadas pedidas o, antes, si el protagonista
        se queda sin saldo para otra jugada de la apuesta mínima.
        """
        self.seen.append(play)
        return self.complete

    def decide(self) -> int:
        """Índice de la opción ganadora con las jugadas vistas."""
        return self.prop.decide(self.seen, self.stake)

    def flipped_at_the_end(self) -> bool:
        """Si la última jugada le dio la vuelta al resultado (el «Tamayazo»)."""
        if len(self.seen) < 2:
            return False
        return self.prop.decide(self.seen[:-1], self.stake) != self.decide()


# -- Apuestas y reparto ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Bet:
    """Lo que lleva apostado un miembro en una porra (todas sus apuestas juntas)."""

    user_id: int
    outcome: int
    stake: int


def by_outcome(bets: Iterable[Bet], options: int) -> list[int]:
    """Dinero apostado en cada opción."""
    totals = [0] * options
    for bet in bets:
        if 0 <= bet.outcome < options:
            totals[bet.outcome] += bet.stake
    return totals


def is_one_sided(bets: Iterable[Bet], options: int) -> bool:
    """Si como mucho una opción tiene dinero (entonces la porra no tiene gracia)."""
    return sum(1 for total in by_outcome(bets, options) if total) < 2


def payout_multiplier(totals: Sequence[int], outcome: int, extra: int = 0) -> float | None:
    """Lo que devolvería ahora cada Y$ apostado a `outcome` si gana (cuota estimada).

    Args:
        totals: Dinero en cada opción.
        extra: Una apuesta que aún no está dentro, para enseñar la cuota con ella.

    Returns:
        `None` si en esa opción no hay nada (y `extra` es 0).
    """
    on_outcome = totals[outcome] + extra
    if on_outcome <= 0:
        return None
    pool = sum(totals) + extra
    keep = 1 - (gaming_tax(100) + IMAGE_SHARE) / 100
    return pool * keep / on_outcome


def image_share(stake: int) -> int:
    """Parte de una apuesta que va al protagonista (derechos de imagen), a la baja."""
    return max(0, stake) * IMAGE_SHARE // 100


@dataclass(frozen=True, slots=True)
class Split:
    """Cómo se reparte el bote de una porra.

    Attributes:
        payouts: Lo que vuelve a cada apostante (0 a quien falla). Si
            `refund`, es lo que apostó.
        taxes: IAJ de cada apostante (incluido el redondeo, que paga quien
            más se lleva).
        image: Derechos de imagen brutos para el protagonista.
        refund: Nadie acertó: se devuelve todo y no hay comisiones.
        winning: Opción ganadora (-1 en una anulada, que no tiene).
    """

    payouts: dict[int, int]
    taxes: dict[int, int]
    image: int
    refund: bool
    winning: int

    @property
    def pool(self) -> int:
        """Todo lo que había en el bote."""
        return sum(self.payouts.values()) + sum(self.taxes.values()) + self.image

    @property
    def tax(self) -> int:
        """IAJ total para el Estado."""
        return sum(self.taxes.values())


def refund_split(bets: Iterable[Bet]) -> Split:
    """Devolverle a cada uno lo suyo (anulada o sin aciertos)."""
    payouts: dict[int, int] = {}
    for bet in bets:
        payouts[bet.user_id] = payouts.get(bet.user_id, 0) + bet.stake
    return Split(payouts=payouts, taxes={}, image=0, refund=True, winning=-1)


def split(bets: Sequence[Bet], winning: int) -> Split:
    """Reparte el bote entre quienes acertaron.

    Cada apuesta deja su IAJ (`taxes.gaming_tax`) y sus derechos de imagen
    (`image_share`), los dos redondeados a la baja. Lo que queda se reparte
    entre los ganadores en proporción a lo que apostaron, a la baja; los
    Y$ sueltos del redondeo van también al Estado, a cuenta de quien más
    se lleva (en empate, el de menor id). La suma de todo es el bote exacto.

    Si nadie acertó, se devuelve todo (`refund_split`), pero con `winning` puesto.
    """
    stakes: dict[int, int] = {}
    sides: dict[int, int] = {}
    for bet in bets:
        stakes[bet.user_id] = stakes.get(bet.user_id, 0) + bet.stake
        sides[bet.user_id] = bet.outcome
    winners = {user: stake for user, stake in stakes.items() if sides[user] == winning}
    if not winners:
        return replace(refund_split(bets), winning=winning)
    taxes = {user: gaming_tax(stake) for user, stake in stakes.items()}
    image = sum(image_share(stake) for stake in stakes.values())
    pot = sum(stakes.values()) - sum(taxes.values()) - image
    on_winning = sum(winners.values())
    payouts = {user: 0 for user in stakes}
    for user, stake in winners.items():
        payouts[user] = pot * stake // on_winning
    leftover = pot - sum(payouts.values())
    if leftover:
        top = min(winners, key=lambda user: (-payouts[user], user))
        taxes[top] = taxes.get(top, 0) + leftover
    return Split(
        payouts=payouts,
        taxes={user: tax for user, tax in taxes.items() if tax},
        image=image,
        refund=False,
        winning=winning,
    )


def merge_bets(rows: Iterable[tuple[int, int, int]]) -> list[Bet]:
    """Junta las filas `(miembro, opción, apuesta)` del libro en una apuesta por miembro."""
    merged: dict[int, Bet] = {}
    for user_id, outcome, stake in rows:
        previous = merged.get(user_id)
        total = stake + (previous.stake if previous else 0)
        merged[user_id] = Bet(user_id, outcome, total)
    return list(merged.values())


def share_of_side(bets: Sequence[Bet], outcome: int) -> float:
    """Parte del bote que estaba en `outcome` (de 0 a 1)."""
    pool = sum(bet.stake for bet in bets)
    if pool <= 0:
        return 0.0
    return sum(bet.stake for bet in bets if bet.outcome == outcome) / pool


def describe_plays(plays: Sequence[Play]) -> str:
    """Resumen corto de lo jugado: `+500 · -500 · +1.200`."""
    if not plays:
        return "ninguna todavía"
    return " · ".join(("+" if play.net > 0 else "") + format_amount(play.net) for play in plays)
