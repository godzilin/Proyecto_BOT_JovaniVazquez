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
TWEETS_TAG = "tweets-markets"  # los tuits solo salen en su sección

_EPSILON = 1e-9  # tolerancia al comparar números en coma flotante


@dataclass(frozen=True, slots=True)
class Topic:
    """Un tema de una sección (un país, una guerra, un activo): lo que se elige en el
    segundo desplegable del panel.

    Un evento es del tema si lleva alguna de sus etiquetas o si su título original (en
    inglés) casa con `title`, una expresión regular sin distinguir mayúsculas. La `key` no
    se repite entre temas ni con las secciones, porque cuenta para los logros
    (`predict_section_<key>`): no se cambia nunca.
    """

    key: str
    label: str
    tags: tuple[str, ...] = ()
    title: str = ""

    def has(self, event: Event) -> bool:
        """¿Es este evento del tema?"""
        return not set(self.tags).isdisjoint(event.tags) or bool(
            self.title and re.search(self.title, event.title, re.IGNORECASE)
        )


@dataclass(frozen=True, slots=True)
class Section:
    """Una sección del menú de `predice`: las etiquetas de Polymarket que la alimentan.

    Se piden a Polymarket sus `tags` y las de sus `topics`, `limit` eventos por etiqueta.
    Si `gate` no está vacío, un evento solo entra si lleva alguna de esas etiquetas (el
    `taiwan` de Polymarket son sobre todo sus elecciones municipales, que no son una
    guerra), y nunca entra uno con etiquetas de `exclude`. Un mismo evento puede salir en
    varias secciones (el alto el fuego con Irán es política y es guerra). La `key` se
    guarda con cada predicción y cuenta para los logros: no se cambia nunca (`mundo` es la
    de ⚔️ Guerras, que heredó las predicciones y los logros de 🌍 Mundo).
    """

    key: str
    label: str
    tags: tuple[str, ...]
    color: int
    topics: tuple[Topic, ...] = ()
    gate: frozenset[str] = frozenset()
    exclude: frozenset[str] = frozenset({TWEETS_TAG})
    limit: int = EVENTS_PER_TAG

    @property
    def fetch_tags(self) -> tuple[str, ...]:
        """Etiquetas que se piden a Polymarket para esta sección, sin repetir."""
        return tuple(dict.fromkeys([*self.tags, *(t for topic in self.topics for t in topic.tags)]))

    def admits(self, event: Event) -> bool:
        """¿Entra este evento en la sección? Pasa `gate` y no lleva nada de `exclude`."""
        gated = not self.gate or not self.gate.isdisjoint(event.tags)
        return gated and self.exclude.isdisjoint(event.tags)

    def topics_of(self, event: Event) -> tuple[str, ...]:
        """Claves de los temas de la sección a los que pertenece el evento."""
        return tuple(topic.key for topic in self.topics if topic.has(event))


# Ciudades de las preguntas del tiempo («Highest temperature in Paris on…»).
_EUROPE = (
    r"\b(?:Madrid|Barcelona|Seville|Valencia|Paris|London|Munich|Berlin|Milan|Rome|Warsaw"
    r"|Amsterdam|Helsinki|Moscow|Istanbul|Ankara|Lisbon|Vienna|Dublin|Athens|Stockholm|Oslo"
    r"|Copenhagen|Prague|Brussels|Zurich|Kyiv)\b"
)
_AMERICA = (
    r"\b(?:NYC|New York|Toronto|Montreal|Vancouver|Dallas|Miami|Houston|Atlanta|Austin"
    r"|Seattle|Chicago|Los Angeles|San Francisco|Denver|Boston|Phoenix|Las Vegas|Panama"
    r"|Mexico City|Buenos Aires|Sao Paulo|Lima|Bogota|Santiago|Mt\. Washington|Mississippi)\b"
)
_ASIA = (
    r"\b(?:Hong Kong|Tokyo|Seoul|Busan|Beijing|Shanghai|Shenzhen|Guangzhou|Chongqing|Wuhan"
    r"|Chengdu|Taipei|Singapore|Kuala Lumpur|Manila|Bangkok|Jakarta|Delhi|Mumbai|Lucknow"
    r"|Karachi|Dubai|Jeddah|Tel Aviv|Sydney|Melbourne|Wellington)\b"
)
_WEATHER_EVENTS = r"\b(?:earthquakes?|hurricanes?|tornado(?:es)?|typhoons?|snow|storms?)\b"

SECTIONS: tuple[Section, ...] = (
    Section(
        "politica",
        "🏛️ Política",
        ("politics", "elections"),
        0xC60B1E,
        topics=(
            Topic("espana", "🇪🇸 España", ("spain",)),
            Topic("eeuu", "🇺🇸 EE. UU.", ("united-states", "midterms", "trump")),
            Topic("francia", "🇫🇷 Francia", ("france",)),
            Topic("alemania", "🇩🇪 Alemania", ("germany",)),
            Topic("reino_unido", "🇬🇧 Reino Unido", ("uk",)),
            Topic("israel", "🇮🇱 Israel", ("israel",)),
            Topic("mexico", "🇲🇽 México", ("mexico",)),
            Topic("canada", "🇨🇦 Canadá", ("canada",)),
            Topic("brasil", "🇧🇷 Brasil", ("brazil",)),
            Topic("taiwan", "🇹🇼 Taiwán", ("taiwan",)),
            Topic("japon", "🇯🇵 Japón", ("japan",)),
            Topic("australia", "🇦🇺 Australia", ("australia",)),
        ),
        gate=frozenset({"politics", "elections"}),
    ),
    Section(
        "mundo",
        "⚔️ Guerras",
        ("geopolitics", "military"),
        0x5D6D7E,
        topics=(
            Topic("ucrania", "🇺🇦 Ucrania y Rusia", ("ukraine", "russia")),
            Topic(
                "oriente", "🕌 Oriente Medio", ("middle-east", "iran", "israel", "lebanon", "yemen")
            ),
            Topic("china", "🇨🇳 China y Taiwán", ("china", "taiwan")),
            Topic("corea", "🇰🇵 Corea del Norte", ("north-korea",)),
            Topic("venezuela", "🇻🇪 Venezuela", ("venezuela",)),
        ),
        gate=frozenset({"geopolitics", "military"}),
    ),
    Section(
        "economia",
        "💰 Economía y cripto",
        ("economy", "finance"),
        0xF2A900,
        topics=(
            Topic("cripto", "🪙 Cripto", ("crypto",)),
            Topic("bolsa", "📈 Bolsa", ("stocks", "earnings", "ipos")),
            Topic("materias", "🛢️ Materias primas", ("commodities", "oil", "gold", "silver")),
            Topic("macro", "🏦 Tipos e inflación", ("fed-rates", "macro-indicators")),
        ),
    ),
    Section("ia", "🤖 IA y ciencia", ("ai", "tech", "science"), 0x10A37F),
    Section(
        "cultura",
        "🎬 Cultura",
        ("pop-culture",),
        0x8E44AD,
        topics=(
            Topic("cine", "🍿 Cine y taquilla", ("movies", "box-office")),
            Topic("series", "📺 Series", ("netflix", "tv")),
            Topic("musica", "🎵 Música", ("music", "spotify")),
            Topic("famosos", "⭐ Famosos", ("celebrities",)),
            Topic("videojuegos", "🎮 Videojuegos", ("gaming", "video-games")),
        ),
    ),
    Section(
        "tuits",
        "📱 Tuits",
        (TWEETS_TAG,),
        0x1DA1F2,
        topics=(
            Topic("musk", "🚀 Elon Musk", title=r"\b(?:Elon|Musk)\b"),
            Topic("trump_posts", "🇺🇸 Trump y la Casa Blanca", title=r"\bTrump\b|White House"),
        ),
        exclude=frozenset(),
    ),
    Section(
        "tiempo",
        "🌦️ El tiempo",
        ("weather", "climate"),
        0x48C9B0,
        topics=(
            Topic("europa", "🇪🇺 Europa", title=_EUROPE),
            Topic("america", "🌎 América", title=_AMERICA),
            Topic("asia", "🌏 Asia y Oceanía", title=_ASIA),
            Topic("fenomenos", "🌋 Terremotos y tormentas", title=_WEATHER_EVENTS),
        ),
        limit=100,  # son muchas y pequeñas: con 25 no llegan preguntas de todas las zonas
    ),
)
SECTION_BY_KEY: dict[str, Section] = {section.key: section for section in SECTIONS}
TOPIC_BY_KEY: dict[str, Topic] = {t.key: t for section in SECTIONS for t in section.topics}


def fetch_plan() -> dict[str, int]:
    """Cada etiqueta que hay que pedir a Polymarket y cuántos eventos. Si dos secciones
    piden la misma, se pide una vez, con el límite mayor."""
    plan: dict[str, int] = {}
    for section in SECTIONS:
        for tag in section.fetch_tags:
            plan[tag] = max(plan.get(tag, 0), section.limit)
    return plan


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
    """Ofertas de cada sección a partir de lo que Polymarket devolvió por etiqueta.

    `fetched` va de etiqueta a eventos (pueden repetirse entre etiquetas). Por cada
    sección: junta los de sus `fetch_tags` sin repetir, se queda con los que admite
    (`Section.admits`) y se pueden ofrecer, y los ordena por volumen de 24 h (y título).
    Aquí no se recorta: cada tema enseña sus `EVENTS_PER_SECTION` (`in_topic`). Todas las
    claves salen en el resultado, vacías si no hay nada.
    """
    result: dict[str, list[Offer]] = {}
    for section in SECTIONS:
        seen: set[str] = set()
        offers = []
        for tag in section.fetch_tags:
            for event in fetched.get(tag, ()):
                if event.id in seen or not section.admits(event):
                    continue
                seen.add(event.id)
                offer = build_offer(event, section.key, now=now)
                if offer is not None:
                    offers.append(offer)
        offers.sort(key=lambda o: (-o.event.volume_24h, o.event.title))
        result[section.key] = offers
    return result


def in_topic(offers: Iterable[Offer], topic: str | None) -> list[Offer]:
    """Las `EVENTS_PER_SECTION` primeras ofertas de una sección que son del tema, o de
    todas si `topic` es `None` (🔥 Tendencias)."""
    chosen = TOPIC_BY_KEY.get(topic) if topic is not None else None
    picked = [o for o in offers if chosen is None or chosen.has(o.event)]
    return picked[:EVENTS_PER_SECTION]


def offer_topics(offer: Offer) -> tuple[str, ...]:
    """Claves de los temas de su sección a los que pertenece una oferta."""
    section = SECTION_BY_KEY.get(offer.section)
    return section.topics_of(offer.event) if section is not None else ()


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
