"""Pruebas de `bot.services.polymarket`: lectura de los datos reales capturados en
`tests/fixtures/polymarket/` (ver `docs/captura_polymarket.py`) y del cliente con una
sesión falsa, sin red."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from bot.services import polymarket
from bot.services.polymarket import (
    CLOB_URL,
    GAMMA_URL,
    MAX_PAGES,
    MIDPOINTS_CHUNK,
    PAGE_SIZE,
    REQUEST_TIMEOUT,
    Event,
    GroupOutcome,
    Market,
    PolymarketClient,
    PolymarketError,
    Resolution,
    parse_event,
    parse_market,
    parse_score,
    parse_time,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "polymarket"
KICKOFF = parse_time("2026-11-06T20:00:00Z")
BEFORE = parse_time("2026-11-05T14:00:00Z")
assert KICKOFF is not None and BEFORE is not None


def load(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def raw_events(name: str) -> list[dict[str, Any]]:
    return load(name)


def events_of(name: str) -> list[Event]:
    parsed = [parse_event(raw) for raw in raw_events(name)]
    assert None not in parsed
    return parsed  # type: ignore[return-value]


def event_with(name: str, slug: str) -> Event:
    return next(e for e in events_of(name) if e.slug == slug)


def raw_market(name: str, slug: str, index: int = 0) -> dict[str, Any]:
    raw = next(e for e in raw_events(name) if e["slug"] == slug)
    return dict(raw["markets"][index])


# -- parse_time y parse_score -----------------------------------------------------------


def test_parse_time_da_lo_mismo_con_los_dos_formatos_de_polymarket() -> None:
    assert parse_time("2026-11-06T03:00:00Z") == parse_time("2026-11-06 03:00:00+00")
    assert parse_time("2026-11-06T03:00:00Z") == 1793934000.0


def test_parse_time_entiende_zonas_con_minutos_y_desplazamientos() -> None:
    assert parse_time("2026-11-06 04:00:00+01") == parse_time("2026-11-06T03:00:00Z")
    assert parse_time("2026-11-06T04:00:00+01:00") == parse_time("2026-11-06T03:00:00Z")


@pytest.mark.parametrize("value", [None, "", "  ", "ayer", 5, "2026-13-45T00:00:00Z", []])
def test_parse_time_devuelve_none_si_falta_o_es_mala(value: object) -> None:
    assert parse_time(value) is None


def test_parse_score_lee_marcadores_con_o_sin_espacios() -> None:
    assert parse_score("3-1") == (3, 1)
    assert parse_score(" 0 - 12 ") == (0, 12)


@pytest.mark.parametrize("value", [None, "", "3", "a-b", "3-", "-1", "3-1-2", "-3-1", 31, "٣-١"])
def test_parse_score_devuelve_none_si_no_es_un_marcador(value: object) -> None:
    assert parse_score(value) is None


# -- Lectura de los datos reales --------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["partido_abierto", "partidos_cerrados", "partido_zombi", "elecciones"]
)
def test_se_leen_todos_los_eventos_y_mercados_capturados_sin_excepciones(name: str) -> None:
    for raw in raw_events(name):
        event = parse_event(raw)
        assert event is not None
        for raw_item in raw["markets"]:
            market = parse_market(raw_item)
            assert market is not None
            assert len(market.outcomes) == len(market.token_ids) == len(market.prices)


def test_el_partido_abierto_tiene_seis_eventos_y_137_mercados() -> None:
    events = events_of("partido_abierto")

    assert len(events) == 6
    assert sum(len(e.markets) for e in events) == 137


def test_el_1x2_de_sevilla_tiene_tres_mercados_que_suman_uno_y_se_pueden_apostar() -> None:
    event = event_with("partido_abierto", "lal-sev-ala-2026-11-06")

    assert event.neg_risk
    assert len(event.markets) == 3
    mids = [m.mid for m in event.markets]
    assert None not in mids
    assert sum(mids) == pytest.approx(1.0, abs=0.03)  # type: ignore[arg-type]
    assert all(m.bettable(now=BEFORE) for m in event.markets)


def test_el_1x2_ya_no_se_apuesta_al_empezar_el_partido_ni_despues() -> None:
    event = event_with("partido_abierto", "lal-sev-ala-2026-11-06")

    for moment in (KICKOFF, KICKOFF + 3_600):
        assert not any(m.bettable(now=moment) for m in event.markets)  # type: ignore[operator]
    assert all(m.bettable(now=KICKOFF - 1) for m in event.markets)  # type: ignore[operator]


def test_un_mercado_sin_compradores_no_esta_cotizado_pero_conserva_su_precio_bruto() -> None:
    event = event_with("partido_abierto", "lal-sev-ala-2026-11-06-exact-score")
    unquoted = [m for m in event.markets if m.bid is None]

    assert unquoted
    for market in unquoted:
        assert not market.quoted()
        assert market.mid is None
        assert not market.bettable(now=BEFORE)
        assert 0 < market.prices[0] < 1
    # El caso de la captura: un marcador exacto absurdo con precio bruto engañoso.
    absurd = next(m for m in unquoted if "5 - 3" in m.question)
    assert absurd.prices[0] == pytest.approx(0.105)


def test_quoted_exige_las_dos_puntas_y_respeta_el_hueco_maximo() -> None:
    market = parse_market(raw_market("partido_abierto", "lal-sev-ala-2026-11-06"))
    assert market is not None

    assert replace(market, bid=0.40, ask=0.43).quoted()  # justo en el límite
    assert not replace(market, bid=0.40, ask=0.44).quoted()
    assert replace(market, bid=0.40, ask=0.44).quoted(max_spread=0.05)
    assert not replace(market, bid=None, ask=0.5).quoted()
    assert not replace(market, bid=0.5, ask=None).quoted()
    assert replace(market, bid=0.4, ask=0.5).mid == pytest.approx(0.45)


def test_los_mercados_v1_usan_clob_token_ids_y_los_v2_usan_position_ids() -> None:
    raw = raw_market("partido_abierto", "lal-sev-ala-2026-11-06")
    v1 = parse_market(raw)
    v2 = parse_market({**raw, "version": "v2"})
    assert v1 is not None and v2 is not None

    assert v1.version == "v1"
    assert v1.token_ids == tuple(json.loads(raw["clobTokenIds"]))
    assert v2.version == "v2"
    assert v2.token_ids == tuple(raw["positionIds"])
    assert v1.token_ids != v2.token_ids


def test_un_mercado_v2_sin_position_ids_se_descarta_y_sin_version_es_v1() -> None:
    raw = raw_market("partido_abierto", "lal-sev-ala-2026-11-06")
    without_version = {k: v for k, v in raw.items() if k != "version"}

    assert parse_market({**raw, "version": "v2", "positionIds": None}) is None
    parsed = parse_market(without_version)
    assert parsed is not None and parsed.version == "v1"


def test_los_candidatos_de_relleno_de_las_elecciones_no_pasan_por_parse_event() -> None:
    raw = next(e for e in raw_events("elecciones") if e["slug"] == "spain-general-election-winner")
    event = parse_event(raw)

    assert event is not None
    assert len(event.markets) < len(raw["markets"])
    assert len(event.markets) == 5  # 33 mercados, 28 de relleno inactivos
    assert all(m.active for m in event.markets)
    assert all(t for m in event.markets for t in m.token_ids)
    assert all("Party" not in m.question for m in event.markets)


def test_parse_market_conserva_los_mercados_de_relleno_si_se_le_dan_directamente() -> None:
    raw = next(e for e in raw_events("elecciones") if e["slug"] == "spain-general-election-winner")
    filler = next(m for m in raw["markets"] if not m["active"])

    market = parse_market(filler)

    assert market is not None and not market.active
    assert not market.bettable(now=BEFORE)


def test_las_elecciones_tienen_un_si_no_suelto_y_una_escalera_de_fechas() -> None:
    single = event_with("elecciones", "spain-general-election-pp-vox-majority")
    ladder = event_with("elecciones", "pedro-snchez-out-as-pm-of-spain-by")

    assert len(single.markets) == 1
    assert single.markets[0].outcomes == ("Yes", "No")
    assert len(ladder.markets) == 4
    ends = [m.end for m in ladder.markets]
    assert None not in ends
    assert ends == sorted(ends)  # type: ignore[type-var]
    assert len(set(ends)) == 4


def test_los_campos_opcionales_que_faltan_toman_su_valor_por_defecto() -> None:
    raw = raw_market("partidos_cerrados", "lal-vil-esp-2026-10-30")
    market = parse_market(raw)
    assert market is not None

    assert "liquidityNum" not in raw
    assert market.liquidity == 0.0
    assert market.volume_24h == 0.0
    assert market.bid is None
    assert market.group_title == "Villarreal CF"
    assert market.sports_type == "moneyline"


# -- Partidos terminados, cancelados y zombi --------------------------------------------


def test_un_partido_terminado_trae_marcador_fase_y_final() -> None:
    event = event_with("partidos_cerrados", "lal-vil-esp-2026-10-30")

    assert event.score == (0, 1)
    assert event.period == "VFT"
    assert event.ended
    assert event.closed


@pytest.mark.parametrize(
    ("slug", "score", "winner"),
    [
        ("lal-vil-esp-2026-10-30", (0, 1), "RCD Espanyol de Barcelona"),
        ("lal-bet-mala-2026-10-31", (2, 1), "Real Betis Balompié"),
        ("lal-bar-ala-2026-10-31", (6, 1), "FC Barcelona"),
    ],
)
def test_el_grupo_de_un_partido_terminado_lo_gana_el_equipo_que_marco_mas(
    slug: str, score: tuple[int, int], winner: str
) -> None:
    event = event_with("partidos_cerrados", slug)

    outcome, market = event.group_outcome()

    assert event.score == score
    assert outcome is GroupOutcome.WON
    assert market is not None and market.group_title == winner
    assert market.resolution is Resolution.RESOLVED
    assert market.winner() == 0
    assert not market.split
    # Los demás mercados del grupo resuelven a No.
    assert [m.winner() for m in event.markets if m is not market] == [1, 1]


def test_el_mercado_resuelto_no_se_puede_apostar_aunque_tenga_propuestas_de_uma() -> None:
    market = event_with("partidos_cerrados", "lal-vil-esp-2026-10-30").markets[0]

    assert market.closed and market.resolution is Resolution.RESOLVED
    assert not market.bettable(now=BEFORE - 40 * 86_400)


def test_un_partido_cancelado_que_resuelve_todo_a_no_es_nulo() -> None:
    event = event_with("partidos_cerrados", "lal-vil-esp-2026-10-30")
    markets = tuple(
        replace(m, prices=(0.0, 1.0), resolution=Resolution.RESOLVED) for m in event.markets
    )

    outcome, market = replace(event, markets=markets).group_outcome()

    assert outcome is GroupOutcome.VOID
    assert market is None


def test_un_reparto_50_50_es_split_y_deja_el_grupo_nulo() -> None:
    raw_event = next(
        e for e in raw_events("partidos_cerrados") if e["slug"] == "lal-vil-esp-2026-10-30"
    )
    for raw in raw_event["markets"]:
        raw["outcomePrices"] = '["0.5", "0.5"]'
    event = parse_event(raw_event)
    assert event is not None

    outcome, market = event.group_outcome()

    assert all(m.split for m in event.markets)
    assert all(m.winner() is None for m in event.markets)
    assert outcome is GroupOutcome.VOID
    assert market is None


def test_un_mercado_cerrado_sin_resultado_limpio_queda_pendiente() -> None:
    raw = {
        **raw_market("partidos_cerrados", "lal-vil-esp-2026-10-30"),
        "outcomePrices": '["0.3", "0.7"]',
    }
    market = parse_market(raw)

    assert market is not None
    assert market.resolution is Resolution.PENDING
    assert market.winner() is None


@pytest.mark.parametrize("statuses", ['["proposed"]', '["proposed", "disputed"]'])
def test_una_resolucion_propuesta_o_disputada_deja_el_mercado_pendiente_y_no_apostable(
    statuses: str,
) -> None:
    raw = {
        **raw_market("partido_abierto", "lal-sev-ala-2026-11-06"),
        "closed": False,
        "umaResolutionStatuses": statuses,
    }
    market = parse_market(raw)

    assert market is not None
    assert market.resolution is Resolution.PENDING
    assert not market.bettable(now=BEFORE)
    assert market.winner() is None


def test_un_grupo_con_algun_mercado_sin_resolver_sigue_pendiente() -> None:
    event = event_with("partidos_cerrados", "lal-vil-esp-2026-10-30")
    open_market = replace(event.markets[2], resolution=Resolution.OPEN, prices=(0.4, 0.6))
    markets = (*event.markets[:2], open_market)

    assert replace(event, markets=markets).group_outcome() == (GroupOutcome.PENDING, None)
    assert replace(event, markets=()).group_outcome() == (GroupOutcome.PENDING, None)


def test_el_partido_zombi_esta_suspendido_y_ya_no_se_puede_apostar() -> None:
    event = event_with("partido_zombi", "lal-ray-bil-2026-10-10")
    now = parse_time("2026-11-05T14:00:00Z")
    assert now is not None

    assert event.period == "SUS"
    assert not event.closed and not event.ended
    assert event.score == (0, 0)
    assert all(not m.closed and m.accepting for m in event.markets)
    assert all(m.resolution is Resolution.OPEN for m in event.markets)
    assert not any(m.bettable(now=now) for m in event.markets)
    assert event.group_outcome() == (GroupOutcome.PENDING, None)


# -- parse_market y parse_event con datos malos -----------------------------------------


def test_parse_market_rechaza_lo_que_no_se_puede_usar_sin_lanzar() -> None:
    good = raw_market("partido_abierto", "lal-sev-ala-2026-11-06")
    bad_cases: list[dict[str, Any]] = [
        {k: v for k, v in good.items() if k != "id"},
        {**good, "question": ""},
        {k: v for k, v in good.items() if k != "outcomes"},
        {**good, "outcomes": "no es json"},
        {**good, "outcomePrices": '["0.5"]'},
        {**good, "outcomePrices": '["a", "b"]'},
        {**good, "clobTokenIds": '["1"]'},
        {**good, "clobTokenIds": None},
        {**good, "outcomes": '["Yes"]', "outcomePrices": '["1"]', "clobTokenIds": '["1"]'},
        {**good, "outcomes": '{"a": 1}'},
        {**good, "outcomePrices": '["nan", "inf"]'},
    ]

    for case in bad_cases:
        assert parse_market(case) is None


def test_parse_event_exige_id_y_titulo_y_tolera_mercados_basura() -> None:
    raw = raw_events("partido_abierto")[0]

    assert parse_event({k: v for k, v in raw.items() if k != "id"}) is None
    assert parse_event({**raw, "title": ""}) is None
    event = parse_event({**raw, "markets": [None, 3, "x", *raw["markets"]], "tags": [1, {}]})
    assert event is not None
    assert len(event.markets) == 3
    assert event.tags == ()
    bare = parse_event({"id": "1", "title": "T"})
    assert bare is not None
    assert bare.markets == () and bare.period == "" and not bare.neg_risk and bare.score is None


def test_parse_event_saca_las_etiquetas_y_el_neg_risk_nulo_es_falso() -> None:
    raw = raw_events("partido_abierto")[0]
    event = parse_event({**raw, "negRisk": None})

    assert event is not None
    assert event.tags == ("sports", "games", "soccer", "la-liga")
    assert not event.neg_risk
    assert event.start == KICKOFF


# -- Cliente ----------------------------------------------------------------------------


class FakeReply:
    """Respuesta de `session.get/post`: un context manager asíncrono como el de aiohttp."""

    def __init__(
        self,
        *,
        status: int = 200,
        payload: Any = None,
        enter_error: BaseException | None = None,
        json_error: BaseException | None = None,
    ) -> None:
        self.status = status
        self.payload = payload
        self.enter_error = enter_error
        self.json_error = json_error

    async def __aenter__(self) -> FakeReply:
        if self.enter_error is not None:
            raise self.enter_error
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def json(self) -> Any:
        if self.json_error is not None:
            raise self.json_error
        return self.payload


class FakeSession:
    """Sesión falsa: contesta con las respuestas en orden (la última se repite)."""

    def __init__(self, *replies: FakeReply) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def _next(self) -> FakeReply:
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]

    def get(self, url: str, *, params: Mapping[str, str], timeout: Any) -> FakeReply:
        self.calls.append({"method": "GET", "url": url, "params": dict(params), "timeout": timeout})
        return self._next()

    def post(self, url: str, *, json: Any, timeout: Any) -> FakeReply:
        self.calls.append({"method": "POST", "url": url, "json": json, "timeout": timeout})
        return self._next()


def client_with(*replies: FakeReply) -> tuple[PolymarketClient, FakeSession]:
    session = FakeSession(*replies)
    return PolymarketClient(session), session  # type: ignore[arg-type]


def stub_event(n: int) -> dict[str, Any]:
    return {"id": str(n), "title": f"Evento {n}", "markets": []}


async def test_events_pide_los_filtros_con_fechas_iso_y_el_flag_closed() -> None:
    client, session = client_with(FakeReply(payload=[]))

    result = await client.events(
        series_id="10200", tag_id=100639, closed=True, end_min=BEFORE, end_max=KICKOFF
    )

    assert result == []
    (call,) = session.calls
    assert call["url"] == f"{GAMMA_URL}/events"
    assert call["params"] == {
        "series_id": "10200",
        "tag_id": "100639",
        "closed": "true",
        "end_date_min": "2026-11-05T14:00:00Z",
        "end_date_max": "2026-11-06T20:00:00Z",
        "limit": str(PAGE_SIZE),
        "offset": "0",
    }
    assert call["timeout"].total == REQUEST_TIMEOUT


async def test_events_omite_los_filtros_a_none_y_por_defecto_pide_los_abiertos() -> None:
    client, session = client_with(FakeReply(payload=[]))
    await client.events()
    await client.events(closed=None)

    assert session.calls[0]["params"] == {"closed": "false", "limit": "100", "offset": "0"}
    assert session.calls[1]["params"] == {"limit": "100", "offset": "0"}


async def test_events_para_en_la_primera_pagina_corta() -> None:
    page = [stub_event(n) for n in range(PAGE_SIZE)]
    client, session = client_with(FakeReply(payload=page), FakeReply(payload=[stub_event(900)]))

    result = await client.events()

    assert len(result) == PAGE_SIZE + 1
    assert [c["params"]["offset"] for c in session.calls] == ["0", "100"]


async def test_events_se_detiene_en_max_pages_si_las_paginas_nunca_se_acortan() -> None:
    page = [stub_event(n) for n in range(PAGE_SIZE)]
    client, session = client_with(FakeReply(payload=page))

    result = await client.events()

    assert len(session.calls) == MAX_PAGES
    assert session.calls[-1]["params"]["offset"] == str((MAX_PAGES - 1) * PAGE_SIZE)
    assert len(result) == MAX_PAGES * PAGE_SIZE


async def test_events_salta_lo_que_parse_event_rechaza() -> None:
    client, _ = client_with(
        FakeReply(payload=[stub_event(1), {"id": "2"}, "basura", stub_event(3)])
    )

    result = await client.events()

    assert [e.id for e in result] == ["1", "3"]


async def test_events_devuelve_eventos_reales_de_una_captura() -> None:
    client, _ = client_with(FakeReply(payload=raw_events("partido_abierto")))

    result = await client.events(tag_id=1)

    assert len(result) == 6
    assert result[0].markets[0].outcomes == ("Yes", "No")


async def test_events_con_algo_que_no_es_una_lista_es_un_error() -> None:
    client, _ = client_with(FakeReply(payload={"error": "x"}))

    with pytest.raises(PolymarketError):
        await client.events()


async def test_event_by_slug_lee_el_evento_y_devuelve_none_en_404() -> None:
    raw = raw_events("partido_abierto")[0]
    client, session = client_with(FakeReply(payload=raw))

    event = await client.event_by_slug(raw["slug"])

    assert event is not None and event.slug == raw["slug"]
    assert session.calls[0]["url"] == f"{GAMMA_URL}/events/slug/{raw['slug']}"
    missing, _ = client_with(FakeReply(status=404))
    assert await missing.event_by_slug("no-existe") is None


async def test_event_by_slug_con_un_evento_ilegible_devuelve_none_y_con_otra_forma_falla() -> None:
    unreadable, _ = client_with(FakeReply(payload={"id": "1"}))
    assert await unreadable.event_by_slug("x") is None
    weird, _ = client_with(FakeReply(payload=[1, 2]))
    with pytest.raises(PolymarketError):
        await weird.event_by_slug("x")


async def test_market_devuelve_none_en_404_y_en_422() -> None:
    raw = raw_market("partido_abierto", "lal-sev-ala-2026-11-06")
    client, session = client_with(FakeReply(payload=raw))

    market = await client.market(raw["id"])

    assert isinstance(market, Market) and market.id == raw["id"]
    assert session.calls[0]["url"] == f"{GAMMA_URL}/markets/{raw['id']}"
    for status in (404, 422):
        client, _ = client_with(FakeReply(status=status))
        assert await client.market("abc") is None


async def test_event_by_slug_y_quote_no_toman_el_422_como_ausente() -> None:
    client, _ = client_with(FakeReply(status=422))

    with pytest.raises(PolymarketError, match="422"):
        await client.event_by_slug("x")
    with pytest.raises(PolymarketError, match="422"):
        await client.quote("1")


async def test_quote_toma_la_mejor_compra_maxima_y_la_mejor_venta_minima_del_libro() -> None:
    book = load("clob_libro")
    client, session = client_with(FakeReply(payload=book))

    quote = await client.quote(book["asset_id"])

    assert quote == (0.017, 0.018)
    (call,) = session.calls
    assert call["url"] == f"{CLOB_URL}/book"
    assert call["params"] == {"token_id": book["asset_id"]}


async def test_quote_devuelve_none_sin_libro_o_con_un_lado_vacio() -> None:
    missing, _ = client_with(FakeReply(status=404, payload={"error": "No orderbook exists"}))
    assert await missing.quote("1") is None
    book = load("clob_libro")
    for side in ("bids", "asks"):
        client, _ = client_with(FakeReply(payload={**book, side: []}))
        assert await client.quote("1") is None
    client, _ = client_with(FakeReply(payload={"bids": [{"price": "x"}], "asks": None}))
    assert await client.quote("1") is None


async def test_midpoints_pide_en_bloques_de_100_y_junta_los_resultados() -> None:
    tokens = [str(n) for n in range(MIDPOINTS_CHUNK * 2 + 1)]
    client, session = client_with(
        FakeReply(payload={"0": "0.0175", "1": "0.5"}),
        FakeReply(payload={"100": "0.25"}),
        FakeReply(payload={"200": "0.9"}),
    )

    result = await client.midpoints(tokens)

    assert result == {"0": 0.0175, "1": 0.5, "100": 0.25, "200": 0.9}
    assert [len(c["json"]) for c in session.calls] == [100, 100, 1]
    assert {c["url"] for c in session.calls} == {f"{CLOB_URL}/midpoints"}
    assert session.calls[0]["method"] == "POST"
    assert session.calls[0]["json"][0] == {"token_id": "0"}
    assert session.calls[0]["timeout"].total == REQUEST_TIMEOUT


async def test_midpoints_ignora_los_valores_que_no_se_leen_y_sin_tokens_no_pide_nada() -> None:
    client, session = client_with(FakeReply(payload={"a": "0.4", "b": "xx", "c": None, "d": 0.6}))

    assert await client.midpoints(["a", "b", "c", "d"]) == {"a": 0.4, "d": 0.6}
    assert await client.midpoints([]) == {}
    assert len(session.calls) == 1


async def test_midpoints_con_algo_que_no_es_un_objeto_es_un_error() -> None:
    client, _ = client_with(FakeReply(payload=["x"]))

    with pytest.raises(PolymarketError):
        await client.midpoints(["a"])


async def test_un_estado_500_se_convierte_en_polymarket_error() -> None:
    client, _ = client_with(FakeReply(status=500))

    with pytest.raises(PolymarketError, match="Polymarket contesta 500"):
        await client.events()
    with pytest.raises(PolymarketError, match="500"):
        await client.midpoints(["a"])


@pytest.mark.parametrize(
    ("kwargs", "name"),
    [
        (
            {"enter_error": aiohttp.ClientConnectionError("https://secreto/x")},
            "ClientConnectionError",
        ),
        ({"enter_error": TimeoutError()}, "TimeoutError"),
        ({"json_error": ValueError("json roto")}, "ValueError"),
        (
            {
                "json_error": aiohttp.ContentTypeError(
                    None,  # type: ignore[arg-type]
                    (),
                    message="no es json",
                )
            },
            "ContentTypeError",
        ),
    ],
)
async def test_los_fallos_de_red_y_de_json_se_convierten_en_polymarket_error_sin_encadenar(
    kwargs: dict[str, Any], name: str
) -> None:
    client, _ = client_with(FakeReply(**kwargs))

    with pytest.raises(PolymarketError, match=name) as info:
        await client.quote("1")

    assert info.value.__cause__ is None
    assert info.value.__suppress_context__
    assert "secreto" not in str(info.value)


def test_el_modulo_no_expone_nada_de_discord() -> None:
    assert not hasattr(polymarket, "discord")
