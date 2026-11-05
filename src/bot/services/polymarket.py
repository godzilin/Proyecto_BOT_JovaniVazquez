"""Cliente y modelos de Polymarket (mercados de predicción).

Responsabilidad: hablar con las APIs públicas de Polymarket (Gamma para eventos y
mercados, CLOB para precios en vivo) y convertir sus respuestas en objetos tipados
(`Event`, `Market`) que el resto del bot puede usar sin conocer las rarezas del
formato: números como texto, listas JSON dentro de cadenas, fechas en dos formatos y
mercados de relleno.

Límites: no usa Discord ni base de datos, no guarda caché, no reintenta y no escribe
en el log el cuerpo de las respuestas. Gamma va detrás de una CDN que cachea hasta 5
minutos, así que sus precios (`Market.bid`, `Market.ask`, `Market.prices`) valen para
enseñar, no para liquidar: el precio real sale de `PolymarketClient.quote`. Solo la
clase `PolymarketClient` toca la red; el resto son funciones puras.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import aiohttp

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
PAGE_SIZE = 100  # Gamma recorta las páginas a 100 sin avisar
MAX_PAGES = 20  # tope de seguridad de la paginación
MAX_SPREAD = 0.03  # hueco compra/venta más ancho que aceptamos como precio real
MIDPOINTS_CHUNK = 100  # identificadores por POST /midpoints
REQUEST_TIMEOUT = 10  # segundos, por petición

_EPSILON = 1e-9  # tolerancia al comparar precios en coma flotante


class PolymarketError(Exception):
    """Polymarket no ha contestado bien (estado raro, red, JSON inválido o forma inesperada)."""


class Resolution(StrEnum):
    """Estado de la resolución de un mercado."""

    OPEN = "open"  # sin propuesta y sin cerrar
    PENDING = "pending"  # propuesta o en disputa, o cerrado sin un resultado limpio
    RESOLVED = "resolved"  # cerrado con resultado limpio (un 1 y el resto 0, o todo 0,5)


class GroupOutcome(StrEnum):
    """Resultado de un grupo de mercados excluyentes (1X2, ganador de unas elecciones)."""

    PENDING = "pending"
    WON = "won"
    VOID = "void"


# -- Lectura de campos ------------------------------------------------------------------


def parse_time(value: object) -> float | None:
    """Fecha de Polymarket a segundos desde la época (UTC), o `None` si falta o es mala.

    Entiende `2026-11-06T03:00:00Z` (eventos y `endDate`) y `2026-11-06 03:00:00+00`
    (`gameStartTime`, con espacio y la zona sin minutos).
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace(" ", "T", 1)
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    elif len(text) > 3 and text[-3] in "+-" and text[-2:].isdigit() and ":" not in text[-6:]:
        text += ":00"  # `+00` -> `+00:00`
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.timestamp()


def parse_score(value: object) -> tuple[int, int] | None:
    """Marcador `"3-1"` a `(3, 1)` (se toleran espacios), o `None` si no lo es."""
    if not isinstance(value, str):
        return None
    home, sep, away = value.partition("-")
    if not sep:
        return None
    home, away = home.strip(), away.strip()
    if not (home.isascii() and home.isdigit() and away.isascii() and away.isdigit()):
        return None
    return int(home), int(away)


def _json_list(value: object) -> list[Any] | None:
    """Lista que Polymarket manda a veces como texto JSON y a veces ya hecha; `None` si no."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, list) else None


def _number(value: object) -> float | None:
    """Número de un campo que puede venir como número o como texto; `None` si no es finito."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if number == number and abs(number) != float("inf") else None


def _text(value: object) -> str:
    """Texto de un campo opcional; cadena vacía si falta."""
    return value if isinstance(value, str) else ""


def _resolution(raw: Mapping[str, Any], prices: tuple[float, ...], closed: bool) -> Resolution:
    """Estado de resolución según el cierre, los precios y las propuestas de UMA."""
    if closed:
        return Resolution.RESOLVED if _clean_result(prices) else Resolution.PENDING
    statuses = _json_list(raw.get("umaResolutionStatuses")) or []
    status = raw.get("umaResolutionStatus")
    if statuses or (isinstance(status, str) and status):
        return Resolution.PENDING
    return Resolution.OPEN


def _clean_result(prices: tuple[float, ...]) -> bool:
    """¿Un precio a 1 y el resto a 0, o todos a 0,5?"""
    return _is_split(prices) or (
        sum(abs(p - 1) <= _EPSILON for p in prices) == 1
        and sum(abs(p) <= _EPSILON for p in prices) == len(prices) - 1
    )


def _is_split(prices: tuple[float, ...]) -> bool:
    return bool(prices) and all(abs(p - 0.5) <= _EPSILON for p in prices)


# -- Modelos ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Market:
    """Un mercado de Polymarket (normalmente un Sí/No).

    Attributes:
        id, slug, question, description: Identidad y texto del mercado.
        group_title: `groupItemTitle` (el candidato o equipo), vacío si falta.
        outcomes: Etiquetas de los resultados, p. ej. `("Yes", "No")`.
        token_ids: Identificador de cada resultado en el CLOB, mismo largo que `outcomes`.
            En los mercados `v2` salen de `positionIds`; en el resto, de `clobTokenIds`.
        prices: `outcomePrices` leídos, mismo largo que `outcomes`. Sin compradores no
            significan nada (ver `quoted`); en un mercado resuelto son el resultado.
        bid, ask: Mejor compra y venta del primer resultado (`bestBid`, `bestAsk`).
        liquidity, volume_24h: `liquidityNum` y `volume24hr`, 0.0 si faltan.
        end, game_start: `endDate` y `gameStartTime` en segundos UTC.
        sports_type: `sportsMarketType` (`moneyline`, `totals`...).
        active, closed, accepting: Banderas del mercado (`accepting` es `acceptingOrders`).
        resolution: Estado de la resolución.
        version: `"v1"` o `"v2"` (`"v1"` si falta).
        image: URL de la imagen, vacía si falta.
    """

    id: str
    slug: str
    question: str
    description: str
    group_title: str
    outcomes: tuple[str, ...]
    token_ids: tuple[str, ...]
    prices: tuple[float, ...]
    bid: float | None
    ask: float | None
    liquidity: float
    volume_24h: float
    end: float | None
    game_start: float | None
    sports_type: str | None
    active: bool
    closed: bool
    accepting: bool
    resolution: Resolution
    version: str
    image: str

    @property
    def mid(self) -> float | None:
        """Punto medio entre compra y venta, o `None` si falta alguna."""
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2

    def quoted(self, max_spread: float = MAX_SPREAD) -> bool:
        """¿Hay compra y venta y la distancia entre ellas es de `max_spread` o menos?

        Sin compradores `outcomePrices` no significa nada (un 5-3 exacto sale a 0,105),
        por eso se exigen las dos puntas.
        """
        if self.bid is None or self.ask is None:
            return False
        return self.ask - self.bid <= max_spread + _EPSILON

    def bettable(self, *, now: float, max_spread: float = MAX_SPREAD) -> bool:
        """¿Se puede apostar ahora? Activo, abierto, aceptando órdenes, sin resolución en
        marcha, con precio real, de dos resultados, con fin futuro y sin haber empezado."""
        return (
            self.active
            and not self.closed
            and self.accepting
            and self.resolution is Resolution.OPEN
            and self.quoted(max_spread)
            and len(self.outcomes) == 2
            and self.end is not None
            and self.end > now
            and (self.game_start is None or self.game_start > now)
        )

    def winner(self) -> int | None:
        """Índice del resultado que cobra (el de precio 1), o `None` si no hay (sin
        resolver o un reparto 50/50)."""
        if self.resolution is not Resolution.RESOLVED or self.split:
            return None
        for index, price in enumerate(self.prices):
            if abs(price - 1) <= _EPSILON:
                return index
        return None

    @property
    def split(self) -> bool:
        """¿Resuelto a 50/50 (todos los precios a 0,5)?"""
        return self.resolution is Resolution.RESOLVED and _is_split(self.prices)


@dataclass(frozen=True, slots=True)
class Event:
    """Un evento de Polymarket: un partido o una pregunta con sus mercados.

    Attributes:
        id, slug, title, description: Identidad y texto del evento.
        start, end: `startTime` y `endDate` en segundos UTC.
        neg_risk: Los mercados son excluyentes entre sí (solo uno gana).
        tags: Los `slug` de sus etiquetas.
        image: URL de la imagen, vacía si falta.
        volume_24h: Lo negociado en las últimas 24 horas (`volume24hr`), 0.0 si falta.
        active, closed: Banderas del evento.
        score: Marcador `(local, visitante)` si es un partido y lo hay.
        period: Fase del partido: `VFT` terminado, `NS` sin empezar, `SUS` suspendido...
            Vacío si no es un partido.
        ended: El partido ha terminado.
        markets: Solo los mercados que `parse_market` aceptó, en el orden de la API.
    """

    id: str
    slug: str
    title: str
    description: str
    start: float | None
    end: float | None
    neg_risk: bool
    tags: tuple[str, ...]
    image: str
    volume_24h: float
    active: bool
    closed: bool
    score: tuple[int, int] | None
    period: str
    ended: bool
    markets: tuple[Market, ...]

    def group_outcome(self) -> tuple[GroupOutcome, Market | None]:
        """Resultado del grupo de mercados excluyentes (1X2, ganador de unas elecciones).

        Returns:
            `(WON, mercado)` con el mercado resuelto a Sí si lo hay; `(VOID, None)` si
            todos están resueltos y ninguno ganó (un partido cancelado resuelve todo a
            No, o todo 50/50); `(PENDING, None)` en cualquier otro caso.
        """
        for market in self.markets:
            if market.winner() == 0:
                return GroupOutcome.WON, market
        if self.markets and all(m.resolution is Resolution.RESOLVED for m in self.markets):
            return GroupOutcome.VOID, None
        return GroupOutcome.PENDING, None


# -- Construcción -----------------------------------------------------------------------


def parse_market(raw: Mapping[str, Any]) -> Market | None:
    """Mercado a partir del JSON de Gamma, o `None` si no se puede usar.

    Se descarta si falta el id o la pregunta, si faltan o no se leen los resultados, los
    precios o los identificadores, si sus largos difieren o si hay menos de 2 resultados
    (los candidatos de relleno de las elecciones caen aquí). Nunca lanza por datos malos.
    """
    try:
        return _build_market(raw)
    except (TypeError, ValueError, AttributeError, KeyError):  # datos con forma imposible
        return None


def _build_market(raw: Mapping[str, Any]) -> Market | None:
    market_id, question = raw.get("id"), raw.get("question")
    if not market_id or not isinstance(question, str) or not question:
        return None
    version = _text(raw.get("version")) or "v1"
    labels = _json_list(raw.get("outcomes"))
    price_list = _json_list(raw.get("outcomePrices"))
    ids = _json_list(raw.get("positionIds" if version == "v2" else "clobTokenIds"))
    if labels is None or price_list is None or ids is None:
        return None
    if not (len(labels) == len(price_list) == len(ids)) or len(labels) < 2:
        return None
    prices = [_number(p) for p in price_list]
    if (
        any(p is None for p in prices)
        or not all(isinstance(label, str) for label in labels)
        or not all(isinstance(i, str | int) and str(i) for i in ids)
    ):
        return None
    closed = raw.get("closed") is True
    price_tuple = tuple(p for p in prices if p is not None)
    sports = raw.get("sportsMarketType")
    return Market(
        id=str(market_id),
        slug=_text(raw.get("slug")),
        question=question,
        description=_text(raw.get("description")),
        group_title=_text(raw.get("groupItemTitle")),
        outcomes=tuple(labels),
        token_ids=tuple(str(i) for i in ids),
        prices=price_tuple,
        bid=_number(raw.get("bestBid")),
        ask=_number(raw.get("bestAsk")),
        liquidity=_number(raw.get("liquidityNum")) or 0.0,
        volume_24h=_number(raw.get("volume24hr")) or 0.0,
        end=parse_time(raw.get("endDate")),
        game_start=parse_time(raw.get("gameStartTime")),
        sports_type=sports if isinstance(sports, str) and sports else None,
        active=raw.get("active") is True,
        closed=closed,
        accepting=raw.get("acceptingOrders") is True,
        resolution=_resolution(raw, price_tuple, closed),
        version=version,
        image=_text(raw.get("image")),
    )


def parse_event(raw: Mapping[str, Any]) -> Event | None:
    """Evento a partir del JSON de Gamma, o `None` si falta el id o el título.

    Los mercados que `parse_market` rechaza se quitan, y también los inactivos y sin
    cerrar: son los candidatos de relleno de las elecciones («Party A»), que en los datos
    reales traen precios 0,5 e identificadores válidos pero no se pueden operar ni
    resolverán nunca, y dejarían el grupo en pendiente para siempre. Nunca lanza por
    datos malos.
    """
    try:
        return _build_event(raw)
    except (TypeError, ValueError, AttributeError, KeyError):  # datos con forma imposible
        return None


def _build_event(raw: Mapping[str, Any]) -> Event | None:
    event_id, title = raw.get("id"), raw.get("title")
    if not event_id or not isinstance(title, str) or not title:
        return None
    raw_markets = raw.get("markets")
    markets = [
        market
        for item in (raw_markets if isinstance(raw_markets, list) else [])
        if isinstance(item, Mapping)
        and (market := parse_market(item)) is not None
        and (market.active or market.closed)
    ]
    raw_tags = raw.get("tags")
    tags = tuple(
        tag["slug"]
        for tag in (raw_tags if isinstance(raw_tags, list) else [])
        if isinstance(tag, Mapping) and isinstance(tag.get("slug"), str)
    )
    return Event(
        id=str(event_id),
        slug=_text(raw.get("slug")),
        title=title,
        description=_text(raw.get("description")),
        start=parse_time(raw.get("startTime")),
        end=parse_time(raw.get("endDate")),
        neg_risk=raw.get("negRisk") is True,
        tags=tags,
        image=_text(raw.get("image")),
        volume_24h=_number(raw.get("volume24hr")) or 0.0,
        active=raw.get("active") is True,
        closed=raw.get("closed") is True,
        score=parse_score(raw.get("score")),
        period=_text(raw.get("period")),
        ended=raw.get("ended") is True,
        markets=tuple(markets),
    )


# -- Cliente ----------------------------------------------------------------------------


def _iso(epoch: float) -> str:
    """Fecha en el formato que piden los filtros de Gamma: `2026-11-05T14:00:00Z`."""
    return datetime.fromtimestamp(int(epoch), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class PolymarketClient:
    """Cliente mínimo de las APIs Gamma y CLOB de Polymarket (sin clave).

    Cada método lanza `PolymarketError` si Polymarket no contesta bien. No reintenta ni
    guarda caché.

    Args:
        session: Sesión HTTP del que la usa; quien la crea la cierra.
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session

    async def events(
        self,
        *,
        series_id: str | None = None,
        tag_id: int | None = None,
        tag_slug: str | None = None,
        closed: bool | None = False,
        end_min: float | None = None,
        end_max: float | None = None,
        order: str | None = None,
        limit: int | None = None,
    ) -> list[Event]:
        """Eventos de Gamma que cumplen los filtros, todas las páginas.

        Pagina de `PAGE_SIZE` en `PAGE_SIZE` hasta una página corta, `MAX_PAGES` o
        `limit` eventos. Los filtros a `None` no se mandan. `end_min` y `end_max` son
        segundos UTC. Con `order` (un campo de Gamma, como `volume24hr`) los pide de mayor
        a menor. Los elementos que `parse_event` rechaza se saltan.

        `limit` importa porque cada evento llega con todos sus mercados: los 92 eventos
        de la etiqueta `spain` pesaban 13 MB en noviembre de 2026.
        """
        params: dict[str, str] = {}
        if series_id is not None:
            params["series_id"] = series_id
        if tag_id is not None:
            params["tag_id"] = str(tag_id)
        if tag_slug is not None:
            params["tag_slug"] = tag_slug
        if closed is not None:
            params["closed"] = "true" if closed else "false"
        if end_min is not None:
            params["end_date_min"] = _iso(end_min)
        if end_max is not None:
            params["end_date_max"] = _iso(end_max)
        if order is not None:
            params["order"] = order
            params["ascending"] = "false"
        wanted = PAGE_SIZE * MAX_PAGES if limit is None else max(0, limit)
        found: list[Event] = []
        page = 0
        while page < MAX_PAGES and page * PAGE_SIZE < wanted:
            size = min(PAGE_SIZE, wanted - page * PAGE_SIZE)
            payload = await self._request(
                "GET",
                f"{GAMMA_URL}/events",
                params={**params, "limit": str(size), "offset": str(page * PAGE_SIZE)},
            )
            if not isinstance(payload, list):
                raise PolymarketError("Polymarket devuelve algo que no es una lista de eventos")
            found.extend(
                event
                for item in payload
                if isinstance(item, Mapping) and (event := parse_event(item)) is not None
            )
            if len(payload) < size:
                break
            page += 1
        return found

    async def event_by_slug(self, slug: str) -> Event | None:
        """Un evento por su `slug`, o `None` si no existe (404) o no se puede leer."""
        payload = await self._request("GET", f"{GAMMA_URL}/events/slug/{slug}", missing=(404,))
        if payload is None:
            return None
        if not isinstance(payload, Mapping):
            raise PolymarketError("Polymarket devuelve algo que no es un evento")
        return parse_event(payload)

    async def market(self, market_id: str) -> Market | None:
        """Un mercado por su id, o `None` si no existe (404) o el id no vale (422)."""
        payload = await self._request("GET", f"{GAMMA_URL}/markets/{market_id}", missing=(404, 422))
        if payload is None:
            return None
        if not isinstance(payload, Mapping):
            raise PolymarketError("Polymarket devuelve algo que no es un mercado")
        return parse_market(payload)

    async def quote(self, token_id: str) -> tuple[float, float] | None:
        """`(mejor compra, mejor venta)` en vivo de un resultado, leídos del libro del CLOB.

        Devuelve `None` si no hay libro (404) o le falta una de las dos puntas. Las
        listas del libro no vienen ordenadas por precio, por eso se toma el máximo de las
        compras y el mínimo de las ventas.
        """
        payload = await self._request(
            "GET", f"{CLOB_URL}/book", params={"token_id": token_id}, missing=(404,)
        )
        if payload is None:
            return None
        if not isinstance(payload, Mapping):
            raise PolymarketError("Polymarket devuelve algo que no es un libro de órdenes")
        bids, asks = _levels(payload.get("bids")), _levels(payload.get("asks"))
        if not bids or not asks:
            return None
        return max(bids), min(asks)

    async def midpoints(self, token_ids: Sequence[str]) -> dict[str, float]:
        """Punto medio en vivo de cada resultado, pedidos de `MIDPOINTS_CHUNK` en
        `MIDPOINTS_CHUNK`. Los valores que no se leen y los resultados sin dato se omiten."""
        found: dict[str, float] = {}
        for start in range(0, len(token_ids), MIDPOINTS_CHUNK):
            chunk = token_ids[start : start + MIDPOINTS_CHUNK]
            payload = await self._request(
                "POST", f"{CLOB_URL}/midpoints", body=[{"token_id": token} for token in chunk]
            )
            if not isinstance(payload, Mapping):
                raise PolymarketError("Polymarket devuelve algo que no son puntos medios")
            for token, value in payload.items():
                number = _number(value)
                if isinstance(token, str) and number is not None:
                    found[token] = number
        return found

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        body: Any = None,
        missing: tuple[int, ...] = (),
    ) -> Any:
        """Una petición con su plazo. Devuelve el JSON, o `None` si el estado está en `missing`."""
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        try:
            if method == "POST":
                request = self.session.post(url, json=body, timeout=timeout)
            else:
                request = self.session.get(url, params=params or {}, timeout=timeout)
            async with request as reply:
                if reply.status in missing:
                    return None
                if reply.status != 200:
                    raise PolymarketError(f"Polymarket contesta {reply.status}")
                return await reply.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as error:
            # Sin encadenar: el error de aiohttp lleva la URL completa.
            raise PolymarketError(f"Polymarket no responde ({type(error).__name__})") from None


def _levels(value: object) -> list[float]:
    """Precios de un lado del libro; los niveles que no se leen se ignoran."""
    if not isinstance(value, list):
        return []
    prices = (_number(level.get("price")) for level in value if isinstance(level, Mapping))
    return [price for price in prices if price is not None]
