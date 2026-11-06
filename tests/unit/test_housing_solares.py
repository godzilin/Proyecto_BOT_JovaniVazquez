"""Pruebas de los solares de `pisos` (9.56.0): lo que construye la rama Ladrillo de `pala`.

Reglas (los 60 solares, los proyectos, la cuenta y la burbuja) y, con la base de datos,
construir (el dinero y sus impuestos, las unidades del promotor en obras y su venta sobre
plano), acabar la obra y la burbuja de los lunes.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from bot.repositories.economy import STATE_ACCOUNT_ID, InsufficientFundsError
from bot.repositories.housing import HousingError, Market, assets_in
from bot.repositories.housing_finance import SolarWork
from bot.services import housing as rules
from bot.services import housing_solars as solar_rules
from bot.services.housing_catalog import BARRIOS
from bot.services.housing_solars import (
    PROJECT_BY_KEY,
    PROJECTS,
    SOLAR_BY_KEY,
    SOLARS,
    build_quote,
    solar_of,
    solar_units,
)
from bot.services.housing_streets import STREETS, buildings_of, is_solar_unit
from tests.unit.test_housing import (
    ANA,
    BEA,
    GUILD,
    Clock,
    Scripted,
    at,
    give,
    housing_taxes,
    ledger_sum,
    make,
)

TRIANA = SOLAR_BY_KEY["triana_c1_s"]  # centro: caben todos los proyectos
ARUCAS = SOLAR_BY_KEY["arucas_c1_s"]  # rural: hasta 3 plantas


# -- Reglas --------------------------------------------------------------------------------


def test_un_solar_por_calle_tres_por_barrio() -> None:
    assert len(SOLARS) == len(STREETS) == 60
    assert all(len(solar_rules.solars_of(b.key)) == 3 for b in BARRIOS)
    for solar in SOLARS:
        numbers = [b.number for b in buildings_of(solar.street)]
        assert solar.number > max(numbers)  # el siguiente portal libre de la calle


def test_las_unidades_salen_siempre_iguales_y_son_obra_nueva() -> None:
    for project in PROJECTS:
        building, units = solar_units(TRIANA.key, project.key)
        assert building.key == TRIANA.key and building.street == TRIANA.street
        assert len({u.key for u in units}) == len(units) <= 25  # caben en un desplegable
        assert all(is_solar_unit(u.key) and solar_of(u.key) == TRIANA for u in units)
        assert all(u.energy == "A" and u.barrio == TRIANA.barrio for u in units)
        assert {u.key for u in units} == {unit.key for unit in building.units}  # todas en el corte
    sizes = [len(solar_units(TRIANA.key, p.key)[1]) for p in PROJECTS]
    assert sizes == sorted(sizes) and sizes[0] == 1  # de la casa a la torre
    assert not is_solar_unit("triana_e3_2b") and solar_of("triana_e3_sa") is None


def test_el_barrio_pone_el_limite_de_alturas() -> None:
    assert [p.key for p in solar_rules.projects_for(ARUCAS)] == ["casa", "bloque3"]
    assert solar_rules.projects_for(TRIANA) == PROJECTS


def test_construir_deja_margen_y_el_solar_sigue_al_indice() -> None:
    for solar in (TRIANA, ARUCAS):
        for project in solar_rules.projects_for(solar):
            quote = build_quote(solar, project)
            # Con el índice a 1, el promotor gana ~16 % de lo que pone (`docs/solares.md`).
            assert 0.12 < quote.margin / quote.total < 0.2
            assert quote.total == quote.paid + quote.taxes
            assert quote.itp == round(quote.land * rules.ITP_RATE)
            assert quote.icio == round(quote.works * rules.ICIO_RATE)
    torre = PROJECT_BY_KEY["torre"]
    hot, cold = build_quote(TRIANA, torre, 1.2), build_quote(TRIANA, torre, 0.8)
    assert hot.works == cold.works and hot.land > cold.land  # la obra no sigue al índice
    assert hot.margin / hot.total > cold.margin / cold.total
    assert build_quote(TRIANA, torre, ipc=10).works > build_quote(TRIANA, torre).works


def test_lo_pagado_se_reparte_entero_entre_las_unidades() -> None:
    project = PROJECT_BY_KEY["bloque7"]
    quote = build_quote(TRIANA, project)
    parts = solar_rules.split_paid(quote, solar_units(TRIANA.key, project.key)[1])
    assert sum(p for p, _c in parts) == quote.paid and sum(c for _p, c in parts) == quote.total


def test_la_burbuja_depende_de_la_obra_frente_a_lo_que_se_compra() -> None:
    floor = solar_rules.BUBBLE_DEMAND_FLOOR
    assert solar_rules.bubble_odds(solar_rules.BUBBLE_SUPPLY_MIN - 1, 0) == 0  # poca obra
    assert solar_rules.bubble_odds(10 * floor, 10 * floor) == 0  # se vende lo que se hace
    low = solar_rules.bubble_odds(15 * floor, 10 * floor)
    high = solar_rules.bubble_odds(30 * floor, 10 * floor)
    assert 0 < low < high <= solar_rules.BUBBLE_MAX_ODDS
    assert solar_rules.bubble_odds(10**12, 0) == solar_rules.BUBBLE_MAX_ODDS
    cuts = [solar_rules.bubble_cut(s * floor, floor) for s in (1, 2, 4, 100)]
    assert cuts == sorted(cuts) and cuts[0] == solar_rules.BUBBLE_CUT_MIN
    assert cuts[-1] == solar_rules.BUBBLE_CUT_MAX
    assert solar_rules.burst_index(0.72, 0.3) == rules.INDEX_MIN


def test_el_mercado_ve_lo_construido_y_pesa_como_las_calles() -> None:
    work = SolarWork(TRIANA.key, ANA, "bloque5", at(14).date(), at(24).date(), 1, 1)
    market = Market(solars={TRIANA.key: work})
    _building, units = solar_units(TRIANA.key, "bloque5")
    assert all(market.listing(u.key) == u for u in units)
    assert set(market.keys()) >= {u.key for u in units}
    assert market.building(TRIANA.key) is not None
    assert market.buildings_on(TRIANA.street)[-1].key == TRIANA.key
    assert market.building_of(units[0].key).key == TRIANA.key
    assert Market().building(TRIANA.key) is None
    # Cada unidad pesa como una de las calles: un bloque entero apenas mueve el barrio.
    assert sum(rules.barrio_weight(u) for u in units) < 0.2


# -- Con la base de datos ------------------------------------------------------------------


async def test_construir_cobra_con_sus_impuestos_y_deja_las_unidades_en_obras(
    tmp_path: Path,
) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock, Scripted(fill=0.99))
    project = PROJECT_BY_KEY["bloque5"]
    quote = build_quote(TRIANA, project)
    await give(economy, ANA, quote.total + 1_000_000)
    with pytest.raises(HousingError):
        await service.build(GUILD, ANA, TRIANA.key, project.key, 7)  # P7 no llega
    with pytest.raises(HousingError):
        await service.build(GUILD, ANA, ARUCAS.key, project.key, 10)  # 5 plantas en el campo
    with pytest.raises(HousingError):
        await service.build(GUILD, ANA, TRIANA.key, project.key, 8, expected=quote.total - 1)
    before, state = await economy.balance(GUILD, ANA), ledger_sum(tmp_path, STATE_ACCOUNT_ID)

    done, balance = await service.build(GUILD, ANA, TRIANA.key, project.key, 8)

    assert done.quote == quote and balance == before - quote.total
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) - state == quote.taxes
    assert housing_taxes(tmp_path, "icio") == quote.icio
    assert housing_taxes(tmp_path, "licencia_obra") == quote.license
    assert housing_taxes(tmp_path, "itp") == quote.itp
    assert housing_taxes(tmp_path, "ajd") == quote.ajd
    assert ledger_sum(tmp_path, ANA) == balance
    market = await service.market(GUILD)
    assert done.until == at(14).date() + timedelta(days=project.days)
    assert len(done.units) == quote.units
    for key in done.units:
        held = market.holdings[key][ANA]
        assert held.shares == rules.SHARES and held.locked_until == done.until
        assert market.leases[key].status == rules.OBRAS
    holdings = [market.holdings[k][ANA] for k in done.units]
    assert sum(h.paid for h in holdings) == quote.paid
    assert sum(h.cost for h in holdings) == quote.total
    # El Patrimonio ya las cuenta.
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.row_factory = sqlite3.Row
        assert assets_in(connection, GUILD).get(ANA, 0) > 0
    # Ya tiene dueño, y una obra a la vez.
    await give(economy, BEA, quote.total)
    with pytest.raises(HousingError):
        await service.build(GUILD, BEA, TRIANA.key, project.key, 10)
    with pytest.raises(HousingError):
        await service.build(GUILD, ANA, ARUCAS.key, "casa", 8)
    # Sobre plano: al mercado no, a otro miembro sí.
    with pytest.raises(HousingError):
        await service.sell(GUILD, ANA, done.units[-1], 100)
    price = rules.price(market.listing(done.units[-1]))
    await service.member_sale(GUILD, ANA, BEA, done.units[-1], 100, price)
    assert (await service.market(GUILD)).holdings[done.units[-1]][BEA].shares == 100


async def test_sin_dinero_no_se_construye_nada(tmp_path: Path) -> None:
    economy, service = await make(tmp_path, Clock(at(14)))
    with pytest.raises(InsufficientFundsError):
        await service.build(GUILD, ANA, ARUCAS.key, "casa", 6)
    assert ARUCAS.key not in (await service.market(GUILD)).solars


async def test_al_acabar_la_obra_se_alquila_y_se_vende_como_cualquiera(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock, Scripted(fill=0.99))
    quote = build_quote(ARUCAS, PROJECT_BY_KEY["casa"])
    await give(economy, ANA, quote.total + 500_000)
    done, _balance = await service.build(GUILD, ANA, ARUCAS.key, "casa", 6)
    finished = []
    for d in range(15, 15 + quote.days + 1):
        clock.now = at(d).timestamp()
        for run in await service.close_days(GUILD):
            finished += run.solars_done
    assert finished == [(ANA, ARUCAS.key)]
    market = await service.market(GUILD)
    assert market.leases[done.units[0]].status == rules.VACANT
    await service.sell(GUILD, ANA, done.units[0], 100)
    assert ledger_sum(tmp_path, ANA) == await economy.balance(GUILD, ANA)
    # El solar ya está construido: no se vuelve a construir.
    with pytest.raises(HousingError):
        await service.build(GUILD, ANA, ARUCAS.key, "casa", 6)


async def test_la_burbuja_hunde_el_barrio_y_retrasa_las_obras(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock(at(14))  # miércoles
    economy, service = await make(tmp_path, clock, Scripted(fill=0.99))
    project = PROJECT_BY_KEY["torre"]
    quote = build_quote(TRIANA, project)
    assert quote.value >= solar_rules.BUBBLE_SUPPLY_MIN
    await give(economy, ANA, quote.total)
    done, _balance = await service.build(GUILD, ANA, TRIANA.key, project.key, 10)
    monkeypatch.setattr(solar_rules, "bubble_odds", lambda supply, demand: 1.0)
    bubbles = []
    for d in range(15, 21):  # hasta el lunes 19, cerrado el martes 20
        clock.now = at(d).timestamp()
        bubbles += [run.bubble for run in await service.close_days(GUILD) if run.bubble]
    assert len(bubbles) == 1
    cut, barrios, promoters = bubbles[0]
    assert barrios == (TRIANA.barrio,) and promoters == (ANA,)
    market = await service.market(GUILD)
    assert market.index(TRIANA.barrio) < 1 - cut / 2
    until = done.until + timedelta(days=solar_rules.BUBBLE_DELAY)
    assert market.solars[TRIANA.key].until == until
    assert all(market.leases[k].works_until == until for k in done.units)
    assert all(market.holdings[k][ANA].locked_until == until for k in done.units)
