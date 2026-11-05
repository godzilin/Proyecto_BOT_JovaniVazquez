"""Lógica pura de `predice`: apuestas con yapdollars sobre preguntas reales de Polymarket.

Responsabilidad: decidir qué eventos y mercados se ofrecen, las cuotas de cada lado (la
casa es la contraparte a cuota fija, `RTP / probabilidad`, como en `deportes`), confirmar
el precio en el momento del clic y resolver una apuesta (ganada, perdida, anulada o
pendiente). Las cuotas son enteros en centésimas (149 es ×1,49) y se redondean hacia
abajo, igual que en `bot.services.sports`.

Límites: no usa Discord, base de datos ni red. Quien lo llama (el cog) pide los eventos
a `PolymarketClient`, guarda las apuestas y cobra o paga con `EconomyService`.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import IntEnum, StrEnum

from bot.services.polymarket import MAX_SPREAD, Event, Market, Resolution

GAME = "predice"  # clave del juego en el casino y prefijo del libro de cuentas
RTP = 0.94  # lo que recupera el jugador de media, como en `deportes`
MIN_ODDS = 101  # ×1,01, en centésimas
MAX_ODDS = 2_500  # ×25; los tiros más largos no se ofrecen
MIN_STAKE = 10
MAX_STAKE = 50_000  # por miembro y evento, sumando todos sus mercados
LOOKAHEAD = 30 * 86_400  # solo mercados que terminan dentro de 30 días
VOID_AFTER = 7 * 86_400  # sin resolver tanto tiempo tras el fin del mercado: se anula
EVENTS_PER_TAG = 25  # eventos que el cog pide a Polymarket por etiqueta (por volumen 24 h)
EVENTS_PER_SECTION = 25  # límite de un menú desplegable de Discord
OPTIONS_PER_EVENT = 25  # límite de un menú desplegable de Discord
EXCLUDED_TAGS = frozenset({"sports", "esports"})  # el deporte es de `deportes`

_EPSILON = 1e-9  # tolerancia al comparar números en coma flotante


@dataclass(frozen=True, slots=True)
class Section:
    """Una sección del menú de `predice`: las etiquetas de Polymarket que la alimentan."""

    key: str
    label: str
    tags: tuple[str, ...]
    color: int


SECTIONS: tuple[Section, ...] = (
    Section("espana", "🇪🇸 España", ("spain",), 0xC60B1E),
    Section("mundo", "🌍 Mundo", ("geopolitics", "world"), 0x1F6FEB),
    Section("economia", "💰 Economía y cripto", ("economy", "crypto", "finance"), 0xF2A900),
    Section("cultura", "🎬 Cultura y tecnología", ("pop-culture", "tech", "science"), 0x8E44AD),
)
SECTION_BY_KEY: dict[str, Section] = {section.key: section for section in SECTIONS}


class Side(IntEnum):
    """Lado de un mercado de dos resultados, por su posición en `Market.outcomes`."""

    YES = 0
    NO = 1


# -- Textos -----------------------------------------------------------------------------

# Se bloquean las apuestas sobre muertes y violencia. Palabras enteras, sin distinguir
# mayúsculas: «deadline» y «shots» no cuentan. «Killers» (el grupo) tampoco: no se bloquea.
_BLOCKED = re.compile(
    r"\b(?:die|dies|died|death|deaths|dead|killed|kill|assassinated|assassination"
    r"|murder|murdered|shot|suicide)\b",
    re.IGNORECASE,
)


def blocked(text: str) -> bool:
    """¿Apuesta sobre la muerte o la violencia contra alguien? Palabras enteras, sin
    distinguir mayúsculas (`deadline` y `shots` no cuentan)."""
    return _BLOCKED.search(text) is not None


_OUTCOMES = {"Yes": "Sí", "No": "No", "Over": "Más", "Under": "Menos", "Other": "Otro"}


def label(outcome: str) -> str:
    """Etiqueta en español de un resultado (`Yes` es `Sí`); lo desconocido queda igual."""
    return _OUTCOMES.get(outcome, outcome)


_MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)
_MONTHS_EN = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_DATE = re.compile(rf"\b({'|'.join(_MONTHS_EN)}) (\d{{1,2}})(?:, (\d{{4}}))?\b")


_THOUSANDS = re.compile(r"\b\d{1,3}(?:,\d{3})+\b")


def date_es(text: str) -> str:
    """Pasa al español las fechas y los miles en inglés de un texto corto.

    `January 15, 2027` es `15 de enero de 2027` y `December 31` es `31 de diciembre`. Los
    miles con coma pasan a punto (`76,000` es `76.000`, como en `format_amount`); los
    decimales con punto se dejan, porque no se distinguen de una versión (`6.1`). Lo
    demás (incluido un `?` final) queda como está; un texto sin fechas no cambia.
    """

    def replace(match: re.Match[str]) -> str:
        month = _MONTHS[_MONTHS_EN.index(match.group(1))]
        year = f" de {match.group(3)}" if match.group(3) else ""
        return f"{int(match.group(2))} de {month}{year}"

    # Las fechas primero: «January 15, 2027» lleva una coma que no es de miles.
    dated = _DATE.sub(replace, text)
    return _THOUSANDS.sub(lambda match: match.group(0).replace(",", "."), dated)


# -- Cuotas -----------------------------------------------------------------------------


def odds_for(probability: float) -> int | None:
    """Cuota en centésimas de un lado con esa probabilidad, o `None` si no se ofrece.

    `floor(RTP / probabilidad * 100)` con una tolerancia de 1e-9 contra el error de coma
    flotante. `None` si la probabilidad no está en (0, 1) o la cuota queda fuera de
    `MIN_ODDS`..`MAX_ODDS`.
    """
    if not 0 < probability < 1:
        return None
    cents = math.floor(100 * RTP / probability + _EPSILON)
    return cents if MIN_ODDS <= cents <= MAX_ODDS else None


def side_probability(mid: float, side: Side) -> float:
    """Probabilidad de un lado a partir del punto medio del Sí."""
    return mid if side is Side.YES else 1 - mid


def payout(stake: int, odds: int) -> int:
    """Lo que cobra una apuesta acertada, apuesta incluida (hacia abajo)."""
    return stake * odds // 100


@dataclass(frozen=True, slots=True)
class Option:
    """Un lado apostable de un mercado, con su cuota y su probabilidad."""

    market: Market
    side: Side
    label: str
    odds: int
    probability: float


def options(market: Market, mid: float) -> list[Option]:
    """Lados de un mercado de dos resultados que se pueden ofrecer a ese punto medio.

    Primero el Sí y luego el No, cada uno solo si tiene cuota (`odds_for`).
    """
    result = []
    for side in Side:
        probability = side_probability(mid, side)
        odds = odds_for(probability)
        if odds is not None:
            result.append(Option(market, side, label(market.outcomes[side]), odds, probability))
    return result


# -- Qué se ofrece ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Offer:
    """Un evento ofrecido en una sección, con los mercados que se pueden apostar."""

    event: Event
    section: str  # `Section.key`
    markets: tuple[Market, ...]  # por punto medio descendente y luego id; hasta OPTIONS_PER_EVENT

    @property
    def single(self) -> bool:
        """¿Es una pregunta Sí/No sin más?

        Un solo mercado y sin `group_title`: un evento de varias opciones al que solo le
        queda una apostable («22%+» del voto de Vox) sigue siendo de opciones, y su
        opción tiene que verse.
        """
        return len(self.markets) == 1 and not self.markets[0].group_title

    @property
    def closes(self) -> float:
        """Cuándo se cierran las apuestas: lo primero entre el fin de cada mercado y el
        comienzo del evento, si lo tiene."""
        times = [m.end for m in self.markets if m.end is not None]
        if self.event.start is not None:
            times.append(self.event.start)
        return min(times)


def offerable_market(market: Market, *, now: float) -> bool:
    """¿Se puede ofrecer este mercado? Apostable ahora, con fin dentro de `LOOKAHEAD`,
    con punto medio, sin texto bloqueado y con algún lado con cuota."""
    mid = market.mid
    return (
        market.bettable(now=now)
        and market.end is not None
        and market.end <= now + LOOKAHEAD
        and mid is not None
        and not blocked(market.question)
        and bool(options(market, mid))
    )


def offerable_event(event: Event, *, now: float) -> bool:
    """¿Se puede ofrecer este evento? Activo y abierto, sin etiquetas de deporte, sin texto
    bloqueado y sin haber empezado (las elecciones cierran en su `startTime`, como un
    partido al saque inicial)."""
    return (
        event.active
        and not event.closed
        and EXCLUDED_TAGS.isdisjoint(event.tags)
        and not blocked(event.title)
        and (event.start is None or event.start > now)
    )


def build_offer(event: Event, section: str, *, now: float) -> Offer | None:
    """Oferta de un evento en una sección, o `None` si no se puede ofrecer o ninguno de sus
    mercados es apostable."""
    if not offerable_event(event, now=now):
        return None
    markets = [m for m in event.markets if offerable_market(m, now=now)]
    if not markets:
        return None
    markets.sort(key=lambda m: (-(m.mid or 0.0), m.id))
    return Offer(event, section, tuple(markets[:OPTIONS_PER_EVENT]))


def build_sections(fetched: Mapping[str, Iterable[Event]], *, now: float) -> dict[str, list[Offer]]:
    """Ofertas de cada sección a partir de lo que Polymarket devolvió por sección.

    `fetched` va de `Section.key` a eventos (pueden repetirse y traer cualquier etiqueta).
    Por cada sección, en el orden de `SECTIONS`: quita repetidos por id, salta los eventos
    ya puestos en una sección anterior, ordena por volumen de 24 h (y título) y se queda
    con `EVENTS_PER_SECTION`. Todas las claves salen en el resultado, vacías si no hay nada.
    """
    placed: set[str] = set()
    result: dict[str, list[Offer]] = {}
    for section in SECTIONS:
        seen: set[str] = set()
        offers = []
        for event in fetched.get(section.key, ()):
            if event.id in seen or event.id in placed:
                continue
            seen.add(event.id)
            offer = build_offer(event, section.key, now=now)
            if offer is not None:
                offers.append(offer)
        offers.sort(key=lambda o: (-o.event.volume_24h, o.event.title))
        offers = offers[:EVENTS_PER_SECTION]
        placed.update(o.event.id for o in offers)
        result[section.key] = offers
    return result


# -- Apostar y liquidar -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Confirmation:
    """Resultado de confirmar el precio al pulsar: la cuota aceptada y la cuota en vivo."""

    odds: int | None  # cuota a la que se acepta la apuesta, `None` si se rechaza
    live: int | None  # cuota en vivo según la cotización, `None` si no hay precio fiable


def confirm(seen: int, side: Side, bid: float, ask: float) -> Confirmation:
    """Confirma una apuesta con la cotización en vivo del CLOB.

    Sin precio fiable (hueco mayor que `MAX_SPREAD` o compra por encima de venta) se
    rechaza sin cuota en vivo. Si no, se acepta a la cuota que vio el miembro cuando la
    cuota en vivo es igual o mejor: nunca recibe peor cuota de la que vio sin avisarle, y
    la casa nunca paga más que el precio en vivo. Si la cuota en vivo es peor, se rechaza
    y se devuelve la cuota en vivo para ofrecérsela.
    """
    if bid > ask or ask - bid > MAX_SPREAD + _EPSILON:
        return Confirmation(None, None)
    live = odds_for(side_probability((bid + ask) / 2, side))
    if live is not None and live >= seen:
        return Confirmation(seen, live)
    return Confirmation(None, live)


class Verdict(StrEnum):
    """Estado de una apuesta según el mercado."""

    PENDING = "pending"
    WON = "won"
    LOST = "lost"
    VOID = "void"


def verdict(market: Market | None, side: Side, *, ends: float, now: float) -> Verdict:
    """Cómo queda una apuesta.

    `market` es el mercado recién pedido a Polymarket (`None` si ya no existe) y `ends`
    el fin del mercado cuando se apostó. Resuelto con ganador: gana quien tenía ese lado.
    Resuelto sin ganador (reparto 50/50): anulada. En cualquier otro caso queda pendiente,
    salvo que hayan pasado `VOID_AFTER` segundos desde `ends`: entonces se anula.
    """
    if market is not None and market.resolution is Resolution.RESOLVED:
        winner = market.winner()
        if winner is None:
            return Verdict.VOID
        return Verdict.WON if winner == side else Verdict.LOST
    return Verdict.VOID if now >= ends + VOID_AFTER else Verdict.PENDING
