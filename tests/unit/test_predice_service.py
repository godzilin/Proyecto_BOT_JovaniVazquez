"""Pruebas de `bot.services.predice`: cuotas, qué se ofrece, confirmación y liquidación,
sobre los datos reales de `tests/fixtures/polymarket/` y sin red."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from bot.services import predice
from bot.services.polymarket import Event, Market, Resolution, parse_event, parse_time
from bot.services.predice import (
    EVENTS_PER_SECTION,
    LOOKAHEAD,
    MAX_ODDS,
    MIN_ODDS,
    SECTION_BY_KEY,
    SECTIONS,
    VOID_AFTER,
    Confirmation,
    Side,
    Verdict,
    blocked,
    build_offer,
    build_sections,
    confirm,
    date_es,
    label,
    odds_for,
    offerable_event,
    offerable_market,
    options,
    payout,
    side_probability,
    verdict,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "polymarket"
NOW = parse_time("2026-11-05T14:15:00Z")
assert NOW is not None


def load(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def events_of(name: str) -> list[Event]:
    parsed = [parse_event(raw) for raw in load(name)]
    assert None not in parsed
    return parsed  # type: ignore[return-value]


def event_titled(name: str, start: str) -> Event:
    return next(e for e in events_of(name) if e.title.startswith(start))


def market(**changes: Any) -> Market:
    """Un mercado Sí/No apostable a las 14:15 del 5 de noviembre, con cambios a gusto."""
    base = event_titled("elecciones", "Spain General Election: PP + Vox").markets[0]
    return replace(base, **changes)


def event(*markets: Market, **changes: Any) -> Event:
    base = event_titled("elecciones", "Spain General Election: PP + Vox")
    return replace(base, markets=markets, **changes)


# -- Cuotas -----------------------------------------------------------------------------


def test_la_cuota_se_redondea_hacia_abajo() -> None:
    assert odds_for(0.5) == 188
    assert odds_for(0.0376) == MAX_ODDS  # 0,94 / 0,0376 = 25 exacto pese al error de coma flotante
    assert odds_for(0.4) == 235


def test_un_favorito_demasiado_claro_no_tiene_cuota() -> None:
    assert odds_for(0.94) is None  # ×1,00 no llega a ×1,01
    assert odds_for(0.93) == 101


def test_un_tiro_demasiado_largo_no_tiene_cuota() -> None:
    assert odds_for(0.03) is None
    assert odds_for(0.0376) == MAX_ODDS


def test_cero_y_uno_no_tienen_cuota() -> None:
    assert odds_for(0.0) is None
    assert odds_for(1.0) is None
    assert odds_for(-0.2) is None
    assert odds_for(1.5) is None


def test_la_cuota_aguanta_el_error_de_coma_flotante() -> None:
    # 0,94 / 0,47 es 2 exacto, pero en coma flotante puede salir 1,9999999...
    assert odds_for(0.47) == 200
    assert odds_for(0.188) == 500
    assert MIN_ODDS == 101


def test_la_probabilidad_de_cada_lado() -> None:
    assert side_probability(0.3, Side.YES) == 0.3
    assert side_probability(0.3, Side.NO) == pytest.approx(0.7)


def test_el_cobro_se_redondea_hacia_abajo() -> None:
    assert payout(100, 188) == 188
    assert payout(55, 105) == 57  # 57,75
    assert payout(10, 101) == 10


# -- Textos -----------------------------------------------------------------------------


def test_etiquetas_en_espanol() -> None:
    assert label("Yes") == "Sí"
    assert label("No") == "No"
    assert label("Over") == "Más"
    assert label("Under") == "Menos"
    assert label("Other") == "Otro"
    assert label("PP") == "PP"


def test_fecha_con_y_sin_anio() -> None:
    assert date_es("January 15, 2027") == "15 de enero de 2027"
    assert date_es("December 31") == "31 de diciembre"
    assert date_es("March 5") == "5 de marzo"


def test_la_fecha_conserva_la_interrogacion_final() -> None:
    assert date_es("November 30, 2026?") == "30 de noviembre de 2026?"


def test_un_texto_sin_fechas_no_cambia() -> None:
    assert date_es("PP") == "PP"
    assert date_es("Will Spain win?") == "Will Spain win?"
    assert date_es("May the best win") == "May the best win"


def test_los_doce_meses() -> None:
    english = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]  # fmt: skip
    spanish = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
    ]  # fmt: skip
    for en, es in zip(english, spanish, strict=True):
        assert date_es(f"{en} 9, 2027") == f"9 de {es} de 2027"


@pytest.mark.parametrize(
    "text",
    [
        "Will he die this year?",
        "Will the leader be KILLED?",
        "Assassination attempt before May?",
        "Death toll above 100?",
        "Will he be shot?",
        "Suicide rate up?",
        "Murder trial verdict",
        "Is she dead by June?",
    ],
)
def test_bloquea_muertes_y_violencia(text: str) -> None:
    assert blocked(text)


@pytest.mark.parametrize(
    "text",
    ["Budget deadline", "Deadly Premonition 3", "Three shots on target", "Killers tour", "Spain"],
)
def test_no_bloquea_palabras_parecidas(text: str) -> None:
    assert not blocked(text)


# -- Opciones ---------------------------------------------------------------------------


def test_opciones_de_un_favorito() -> None:
    winner = event_titled("elecciones", "Spain General Election Winner")
    pp = next(m for m in winner.markets if m.group_title == "PP")
    assert pp.group_title == "PP"
    found = options(pp, pp.mid or 0)
    assert [o.side for o in found] == [Side.YES, Side.NO]
    assert [o.label for o in found] == ["Sí", "No"]
    assert found[0].odds == 106  # 0,94 / 0,885
    assert found[1].odds == 817  # 0,94 / 0,115
    assert found[0].probability == pytest.approx(0.885)


def test_un_lado_fuera_de_rango_no_se_ofrece() -> None:
    pp_vox = event_titled("elecciones", "Spain General Election: PP + Vox").markets[0]
    found = options(pp_vox, 0.95)  # el Sí rinde menos de ×1,01
    assert [o.side for o in found] == [Side.NO]
    assert found[0].odds == 1_880
    assert options(pp_vox, 0.99) == []  # el No supera el tope


# -- Qué se ofrece ----------------------------------------------------------------------


def test_las_elecciones_se_ofrecen_en_espana_con_el_pp_primero() -> None:
    winner = event_titled("elecciones", "Spain General Election Winner")
    offer = build_offer(winner, "espana", now=NOW)
    assert offer is not None
    assert offer.section == "espana"
    assert [m.group_title for m in offer.markets] == ["PP", "PSOE"]
    assert not offer.single


def test_las_elecciones_cierran_el_dia_del_voto_a_mediodia() -> None:
    winner = event_titled("elecciones", "Spain General Election Winner")
    offer = build_offer(winner, "espana", now=NOW)
    assert offer is not None
    assert offer.closes == parse_time("2026-11-29T12:00:00Z")


def test_a_la_hora_del_voto_ya_no_se_ofrece_nada_de_las_elecciones() -> None:
    now = parse_time("2026-11-29T12:00:00Z")
    assert now is not None
    winner = event_titled("elecciones", "Spain General Election Winner")
    assert not offerable_event(winner, now=now)
    assert build_offer(winner, "espana", now=now) is None


def test_una_pregunta_de_si_o_no_es_una_oferta_unica() -> None:
    pregunta = event_titled("elecciones", "Spain General Election: PP + Vox")
    offer = build_offer(pregunta, "espana", now=NOW)
    assert offer is not None
    assert offer.single
    assert offer.closes == pregunta.markets[0].end


def test_los_mercados_con_hueco_ancho_no_se_ofrecen() -> None:
    palmas = event_titled("elecciones", "Spain General Election: Las Palmas")
    assert build_offer(palmas, "espana", now=NOW) is None


def test_un_partido_con_etiqueta_de_deporte_no_se_ofrece() -> None:
    for partido in events_of("partido_abierto"):
        assert "sports" in partido.tags
        assert not offerable_event(partido, now=NOW)
        assert build_offer(partido, "mundo", now=NOW) is None


def test_la_escalera_solo_ofrece_los_mercados_de_los_proximos_30_dias() -> None:
    sanchez = event_titled("elecciones", "Pedro Sánchez out")
    # A 5 de noviembre ninguno termina en 30 días (el primero abierto cierra el 31 de diciembre).
    assert build_offer(sanchez, "espana", now=NOW) is None
    now = parse_time("2026-12-20T10:00:00Z")
    assert now is not None
    offer = build_offer(sanchez, "espana", now=now)
    assert offer is not None
    assert all(m.end is not None and m.end <= now + LOOKAHEAD for m in offer.markets)
    assert [date_es(m.group_title) for m in offer.markets] == [
        "15 de enero de 2027",
        "31 de diciembre de 2026",
    ]  # por probabilidad descendente


def test_un_mercado_con_pregunta_bloqueada_se_salta() -> None:
    sano = market(id="a")
    malo = market(id="b", question="Will the president die before 2027?")
    assert not offerable_market(malo, now=NOW)
    offer = build_offer(event(sano, malo), "espana", now=NOW)
    assert offer is not None
    assert [m.id for m in offer.markets] == ["a"]


def test_un_evento_con_titulo_bloqueado_no_se_ofrece() -> None:
    assert build_offer(event(market(), title="Will X be assassinated?"), "mundo", now=NOW) is None


def test_un_evento_cerrado_o_inactivo_no_se_ofrece() -> None:
    assert build_offer(event(market(), closed=True), "mundo", now=NOW) is None
    assert build_offer(event(market(), active=False), "mundo", now=NOW) is None


def test_un_mercado_sin_cuota_en_ningun_lado_no_se_ofrece() -> None:
    assert not offerable_market(market(bid=0.985, ask=0.995), now=NOW)


def test_como_mucho_25_mercados_por_evento() -> None:
    muchos = [market(id=f"m{i:02d}") for i in range(40)]
    offer = build_offer(event(*muchos), "mundo", now=NOW)
    assert offer is not None
    assert len(offer.markets) == predice.OPTIONS_PER_EVENT


def test_el_empate_de_probabilidad_se_ordena_por_id() -> None:
    offer = build_offer(event(market(id="b"), market(id="a")), "mundo", now=NOW)
    assert offer is not None
    assert [m.id for m in offer.markets] == ["a", "b"]


# -- Secciones --------------------------------------------------------------------------


def numbered(n: int, volume: float = 0.0) -> Event:
    return event(market(id=f"mk{n}"), id=f"ev{n}", title=f"Pregunta {n:03d}", volume_24h=volume)


def test_las_secciones_existen_y_se_buscan_por_clave() -> None:
    assert [s.key for s in SECTIONS] == ["espana", "mundo", "economia", "cultura"]
    assert SECTION_BY_KEY["mundo"].tags == ("geopolitics", "world")


def test_un_evento_en_dos_secciones_solo_sale_en_la_primera() -> None:
    comun = numbered(1, 5)
    sections = build_sections({"espana": [comun, comun], "mundo": [comun, numbered(2)]}, now=NOW)
    assert [o.event.id for o in sections["espana"]] == ["ev1"]
    assert [o.event.id for o in sections["mundo"]] == ["ev2"]


def test_las_secciones_se_ordenan_por_volumen_y_luego_por_titulo() -> None:
    eventos = [numbered(1, 10), numbered(2, 99), numbered(4, 10), numbered(3, 10)]
    sections = build_sections({"mundo": eventos}, now=NOW)
    assert [o.event.id for o in sections["mundo"]] == ["ev2", "ev1", "ev3", "ev4"]


def test_cada_seccion_tiene_un_tope_de_eventos() -> None:
    eventos = [numbered(n, float(n)) for n in range(EVENTS_PER_SECTION + 10)]
    sections = build_sections({"economia": eventos}, now=NOW)
    assert len(sections["economia"]) == EVENTS_PER_SECTION
    assert sections["economia"][0].event.id == f"ev{EVENTS_PER_SECTION + 9}"


def test_las_cuatro_secciones_salen_aunque_esten_vacias() -> None:
    sections = build_sections({}, now=NOW)
    assert sections == {"espana": [], "mundo": [], "economia": [], "cultura": []}
    parcial = build_sections({"cultura": [numbered(1)]}, now=NOW)
    assert list(parcial) == ["espana", "mundo", "economia", "cultura"]
    assert len(parcial["cultura"]) == 1


def test_las_secciones_con_los_datos_reales() -> None:
    sections = build_sections({"espana": events_of("elecciones")}, now=NOW)
    titles = [o.event.title for o in sections["espana"]]
    assert titles[0] in {
        "Spain General Election Winner",
        "Spain General Election: PP + Vox Majority?",
    }
    assert "Spain General Election Winner" in titles
    assert "Pedro Sánchez out as PM of Spain by...?" not in titles


# -- Confirmar --------------------------------------------------------------------------


def test_el_mismo_precio_se_acepta_a_la_cuota_vista() -> None:
    seen = odds_for(0.5)
    assert seen == 188
    assert confirm(188, Side.YES, 0.49, 0.51) == Confirmation(188, 188)


def test_un_precio_mejor_se_acepta_a_la_cuota_vista() -> None:
    result = confirm(188, Side.YES, 0.39, 0.41)  # el Sí ha bajado: paga más
    assert result == Confirmation(188, 235)


def test_un_precio_peor_se_rechaza_con_la_cuota_en_vivo() -> None:
    result = confirm(188, Side.YES, 0.59, 0.61)
    assert result == Confirmation(None, 156)


def test_el_lado_no_usa_el_precio_complementario() -> None:
    assert confirm(188, Side.NO, 0.49, 0.51) == Confirmation(188, 188)
    assert confirm(188, Side.NO, 0.59, 0.61) == Confirmation(188, 235)


def test_un_hueco_ancho_se_rechaza_sin_cuota() -> None:
    assert confirm(188, Side.YES, 0.40, 0.60) == Confirmation(None, None)
    assert confirm(188, Side.YES, 0.52, 0.50) == Confirmation(None, None)


def test_un_hueco_justo_en_el_limite_vale() -> None:
    assert confirm(188, Side.YES, 0.485, 0.515).odds == 188


def test_si_la_cuota_en_vivo_sale_de_rango_se_rechaza() -> None:
    result = confirm(188, Side.YES, 0.965, 0.975)
    assert result == Confirmation(None, None)


# -- Liquidar ---------------------------------------------------------------------------


def closed_market() -> Market:
    """Un mercado resuelto con ganador en el índice 0 (Sí)."""
    for e in events_of("partidos_cerrados"):
        for m in e.markets:
            if m.winner() == 0:
                return m
    raise AssertionError("sin mercado ganador en el captura")


def test_gana_quien_tenia_el_lado_ganador() -> None:
    si = closed_market()
    assert verdict(si, Side.YES, ends=NOW, now=NOW) is Verdict.WON
    assert verdict(si, Side.NO, ends=NOW, now=NOW) is Verdict.LOST


def test_el_no_gana_cuando_el_mercado_resuelve_a_no() -> None:
    no = replace(closed_market(), prices=(0.0, 1.0))
    assert no.winner() == 1
    assert verdict(no, Side.NO, ends=NOW, now=NOW) is Verdict.WON
    assert verdict(no, Side.YES, ends=NOW, now=NOW) is Verdict.LOST


def test_un_reparto_50_50_se_anula() -> None:
    mitad = replace(closed_market(), prices=(0.5, 0.5))
    assert mitad.split
    assert verdict(mitad, Side.YES, ends=NOW, now=NOW) is Verdict.VOID
    assert verdict(mitad, Side.NO, ends=NOW, now=NOW) is Verdict.VOID


def test_sin_resolver_queda_pendiente_hasta_la_fecha_limite() -> None:
    abierto = replace(closed_market(), closed=False, resolution=Resolution.PENDING)
    assert verdict(abierto, Side.YES, ends=NOW, now=NOW + VOID_AFTER - 1) is Verdict.PENDING
    assert verdict(abierto, Side.YES, ends=NOW, now=NOW + VOID_AFTER) is Verdict.VOID


def test_un_mercado_que_ya_no_existe_se_anula_pasado_el_plazo() -> None:
    assert verdict(None, Side.YES, ends=NOW, now=NOW + VOID_AFTER - 1) is Verdict.PENDING
    assert verdict(None, Side.NO, ends=NOW, now=NOW + VOID_AFTER) is Verdict.VOID


def test_los_limites_del_modulo_son_coherentes() -> None:
    assert predice.GAME == "predice"
    assert predice.MIN_STAKE < predice.MAX_STAKE
    assert MIN_ODDS < MAX_ODDS
