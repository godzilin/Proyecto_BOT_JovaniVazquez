"""Pruebas de `casa`, fase 1: el Confort de la vivienda habitual, sus tramos, los bufos que
da en `pala` y los suministros que cobra cada noche el cierre de `pisos` (con su corte)."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from bot.repositories.economy import STATE_ACCOUNT_ID
from bot.repositories.housing import HomeStatus, Market, place_of
from bot.repositories.housing_finance import HOME_OWN, HOME_RENTED, HOME_VPO
from bot.services import home
from bot.services import housing as rules
from bot.services.housing_catalog import LISTING_BY_KEY, VPO_UNITS
from bot.services.pala import WorkService
from bot.services.work import FAMILY_REGEN_PER_HOUR, Contract, family_now, home_relief
from tests.unit.test_housing_phases import (
    ANA,
    BAJO,
    GUILD,
    Clock,
    at,
    give,
    ledger_sum,
    make,
    sql,
)


def place(m2: int = 60, energy: str = "D", reforms: int = 0, theme: str = "centro",
          floor: str = "2.º") -> home.Place:  # fmt: skip
    return home.Place(home.OWN, "x", "Piso", "piso2", m2, energy, reforms, theme, floor)


# -- Confort ----------------------------------------------------------------------------


def test_el_espacio_premia_los_metros_por_persona_y_el_tamano_de_la_casa() -> None:
    assert home.space_points(30) < home.space_points(60) < home.space_points(250)
    assert home.space_points(home.SIZE_FULL_M2) == home.SPACE_POINTS
    assert home.space_points(2 * home.SIZE_FULL_M2) == home.SPACE_POINTS
    # Con más gente en la misma casa, menos espacio por cabeza.
    assert home.space_points(60, people=3) < home.space_points(60, people=1)
    assert home.space_points(10) >= 0


def test_la_calidad_sale_del_certificado_las_reformas_y_el_ascensor() -> None:
    assert home.quality_points(place(energy="A")) > home.quality_points(place(energy="G"))
    assert home.quality_points(place(reforms=2)) == home.quality_points(place()) + 2 * (
        home.REFORM_POINTS
    )
    assert home.quality_points(place(reforms=9)) <= home.QUALITY_POINTS
    assert home.quality_points(place(floor="4.º sin ascensor")) < home.quality_points(place())
    worst = place(energy="G", floor="5.º sin ascensor")
    assert home.quality_points(worst) == home.ENERGY_POINTS["G"] - home.NO_LIFT_PENALTY
    assert home.quality_points(place(energy="A", reforms=3)) == home.QUALITY_POINTS


def test_sin_muebles_ninguna_casa_llega_al_tramo_de_arriba() -> None:
    best = home.comfort(place(m2=home.SIZE_FULL_M2, energy="A", reforms=3, theme="lujo"))
    assert best.gear == 0
    assert best.total == home.SPACE_POINTS + home.QUALITY_POINTS + home.AREA_POINTS
    assert best.tier is not home.TIERS[0]
    assert home.comfort(place(m2=home.SIZE_FULL_M2, energy="A", reforms=3, theme="lujo"),
                        gear=home.GEAR_POINTS).total == 100  # fmt: skip


def test_los_tramos_van_de_mejor_a_peor_y_el_peor_es_el_bufo_de_antes() -> None:
    lows = [t.low for t in home.TIERS]
    assert lows == sorted(lows, reverse=True) and lows[-1] == 0
    for better, worse in zip(home.TIERS, home.TIERS[1:], strict=False):
        assert better.regen >= worse.regen
        assert better.family >= worse.family
        assert better.strain <= worse.strain
    assert home.TIERS[-1].regen == 1.2  # el ×1,2 fijo de la 9.53.0: nadie pierde
    assert home.tier_of(100) is home.TIERS[0]
    assert home.tier_of(0) is home.TIERS[-1]
    for t in home.TIERS:
        assert home.tier_of(t.low) is t


def test_el_siguiente_tramo_es_el_de_justo_encima() -> None:
    zulo = home.Comfort(10, 5, 0, 4)
    assert zulo.tier.key == "zulo"
    assert zulo.next_tier is not None and zulo.next_tier.key == "apanada"
    assert home.Comfort(40, 25, 25, 10).next_tier is None


# -- Suministros ------------------------------------------------------------------------


def test_los_suministros_crecen_con_los_metros_y_un_certificado_malo() -> None:
    small, big = home.supplies(place(m2=40)), home.supplies(place(m2=120))
    assert big.total > small.total
    assert home.supplies(place(energy="G")).power > home.supplies(place(energy="A")).power
    # El agua va por persona; la fibra es fija.
    assert home.supplies(place(), people=3).water == 3 * home.supplies(place()).water
    assert small.internet == big.internet


def test_la_luz_paga_el_impuesto_de_la_electricidad_y_la_fibra_su_igic() -> None:
    bill = home.supplies(place())
    assert bill.power_tax == round(bill.power * home.POWER_TAX_RATE)
    assert bill.igic == round(bill.internet * 0.07)
    assert bill.total == bill.power + bill.water + bill.internet + bill.power_tax + bill.igic
    assert bill.gone == bill.total - bill.power_tax - bill.igic


# -- Dónde vive -------------------------------------------------------------------------


def test_cada_vivienda_habitual_da_su_sitio() -> None:
    market = Market()
    own = place_of(market, HOME_OWN, BAJO.key)
    assert own is not None and own.kind == home.OWN and own.m2 == BAJO.m2
    assert own.energy == BAJO.energy and own.theme
    rented = place_of(market, HOME_RENTED, BAJO.key)
    assert rented is not None and rented.kind == home.RENTED
    vpo = place_of(market, HOME_VPO, VPO_UNITS[0].key)
    assert vpo is not None and vpo.kind == home.VPO and vpo.theme == home.VPO_THEME
    assert place_of(market, HOME_OWN, "no_existe") is None
    assert place_of(market, HOME_VPO, "no_existe") is None


def test_las_reformas_acabadas_suben_la_calidad_de_la_casa() -> None:
    finished = {"bano": (1, date(2026, 10, 1), True, False)}
    pending = {"cocina": (1, date(2026, 12, 1), False, False)}
    market = Market(reforms={BAJO.key: {**finished, **pending}})
    reformed = place_of(market, HOME_OWN, BAJO.key)
    assert reformed is not None and reformed.reforms == 1


# -- Cada noche -------------------------------------------------------------------------


async def test_quien_vive_en_su_casa_paga_los_suministros_y_el_estado_cobra_sus_impuestos(
    tmp_path,  # noqa: ANN001
) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    await service.set_home(GUILD, ANA, BAJO.key)
    bill = (await service.repository.home_status(GUILD, ANA)).bill
    clock.to(15)
    (run,) = await service.close_days(GUILD)
    night = run.homes[ANA]
    assert night.bill == bill and night.unpaid == 0 and night.paid == bill.total
    taxes = dict(sql(tmp_path, "SELECT kind, SUM(tax) FROM economy_housing_tax "
                     "WHERE user_id = ? GROUP BY kind", ANA))  # fmt: skip
    assert taxes["electricidad"] == bill.power_tax
    assert taxes["igic_suministros"] == bill.igic
    (gone,) = sql(tmp_path, "SELECT SUM(delta) FROM economy_ledger WHERE user_id = ? "
                  "AND reason = 'casa:suministros'", ANA)[0]  # fmt: skip
    assert gone == -bill.gone
    state = dict(sql(tmp_path, "SELECT reason, SUM(delta) FROM economy_ledger WHERE user_id = ? "
                     "GROUP BY reason", STATE_ACCOUNT_ID))  # fmt: skip
    assert state["pisos:electricidad"] == bill.power_tax
    assert state["pisos:igic_suministros"] == bill.igic
    assert ledger_sum(tmp_path, ANA) == await economy.balance(GUILD, ANA)
    status = await service.repository.home_status(GUILD, ANA)
    assert status is not None and not status.cut(at(15).date())
    # `hacienda`: los dos con los impuestos de las compras, nunca en el IRPF del ahorro, y en
    # el bloque 🏠 en una línea.
    paid = (await economy.tax_breakdown(GUILD))[ANA]
    assert "irpf_ahorro" not in paid
    homes = (await economy.repository.housing_tax_breakdown(GUILD))[ANA]
    assert homes["suministros"] == bill.power_tax + bill.igic


async def test_sin_dinero_le_cortan_la_luz_un_dia_y_no_se_queda_debiendo(tmp_path) -> None:  # noqa: ANN001
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    await service.set_home(GUILD, ANA, BAJO.key)
    await give(economy, ANA, -(await economy.balance(GUILD, ANA)))
    clock.to(15)
    (run,) = await service.close_days(GUILD)
    assert run.homes[ANA].unpaid == run.homes[ANA].bill.total
    status = await service.repository.home_status(GUILD, ANA)
    assert status is not None
    assert status.cut(at(15).date()) and not status.cut(at(16).date())
    # Lo que no pagó de suministros no es deuda del piso (solo sus recibos de siempre).
    market = await service.market(GUILD)
    costs = rules.monthly_costs(BAJO)
    assert market.leases[BAJO.key].arrears <= costs.total + round(costs.insurance * rules.IPS_RATE)
    assert await economy.balance(GUILD, ANA) == 0


async def test_con_tus_padres_no_hay_casa_ni_suministros(tmp_path) -> None:  # noqa: ANN001
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000)
    assert await service.repository.home_status(GUILD, ANA) is None
    clock.to(15)
    (run,) = await service.close_days(GUILD)
    assert ANA not in run.homes
    assert not sql(tmp_path, "SELECT 1 FROM economy_ledger WHERE reason = 'casa:suministros'")


# -- Los bufos en `pala` ----------------------------------------------------------------


def test_la_casa_hace_que_la_familia_se_recupere_antes() -> None:
    plain = Contract(job="obra", level=1, family=0.0, family_at=0.0)
    at_home = Contract(job="obra", level=1, family=0.0, family_at=0.0, home_family=1.5)
    assert family_now(plain, 3_600) == pytest.approx(FAMILY_REGEN_PER_HOUR)
    assert family_now(at_home, 3_600) == pytest.approx(FAMILY_REGEN_PER_HOUR * 1.5)


def test_la_casa_rebaja_lo_que_desgasta_cada_turno_en_su_propia_linea() -> None:
    parts = [("extra", -12), ("madrugada", -5), ("en casa", 4)]
    assert home_relief(parts, 1.0) == parts
    relieved = home_relief(parts, 0.8)
    assert relieved[-1] == ("casa", round(17 * 0.2))
    assert sum(v for _, v in relieved) > sum(v for _, v in parts)


class _Housing:
    def __init__(self, status: HomeStatus | None) -> None:
        self.status = status

    async def home_status(self, guild_id: int, user_id: int) -> HomeStatus | None:
        return self.status


def _work(status: HomeStatus | None, when: float) -> WorkService:
    return WorkService(MagicMock(), MagicMock(), clock=lambda: when, housing=_Housing(status))


async def test_pala_usa_el_tramo_de_la_casa_salvo_con_la_luz_cortada() -> None:
    where = place(m2=110, energy="B", reforms=2, theme="lujo")
    comfort = home.comfort(where)
    now = at(15).timestamp()
    assert await _work(HomeStatus(where, comfort), now).home_tier(GUILD, ANA) == comfort.tier
    cut = HomeStatus(where, comfort, cut_until=at(15).date())
    assert await _work(cut, now).home_tier(GUILD, ANA) is None
    assert await _work(cut, at(16).timestamp()).home_tier(GUILD, ANA) == comfort.tier
    assert await _work(None, now).home_tier(GUILD, ANA) is None


def test_ningun_inmueble_del_catalogo_rompe_el_confort() -> None:
    market = Market()
    for key, listing in LISTING_BY_KEY.items():
        if listing.kind not in rules.HOME_KINDS:
            continue
        found = place_of(market, HOME_OWN, key)
        assert found is not None
        total = home.comfort(found).total
        assert 0 <= total <= 100 - home.GEAR_POINTS
        assert home.supplies(found).total > 0
