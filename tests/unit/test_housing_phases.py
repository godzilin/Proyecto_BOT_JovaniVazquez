"""Pruebas de las fases 2 a 4 de `pisos`: hipoteca, VPO, vivienda habitual, miembros de
alquiler y bono, vacacional, gran tenedor, fondo buitre y SOCIMI."""

from __future__ import annotations

import random
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from bot.repositories.economy import (
    HOUSING_DEPOSIT_ACCOUNT_ID,
    STATE_ACCOUNT_ID,
    EconomyRepository,
    InsufficientFundsError,
    LedgerEntry,
)
from bot.repositories.housing import DayRun, HousingError, HousingRepository, Market, assets_in
from bot.repositories.housing_finance import HOME_OWN, HOME_RENTED, HOME_VPO, Promotion
from bot.services import housing as rules
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.housing_catalog import (
    BARRIOS,
    LISTING_BY_KEY,
    RENT_LEVELS,
    VPO_BY_KEY,
    VPO_UNITS,
)
from bot.services.housing_market import HousingService
from bot.services.levels import TIMEZONE
from bot.services.work import Contract, battery_now

GUILD = 1
ANA = 10
BEA = 20
BAJO = LISTING_BY_KEY["schamann_bajo"]


class Clock:
    def __init__(self, when: datetime) -> None:
        self.now = when.timestamp()

    def __call__(self) -> float:
        return self.now

    def to(self, d: int, hour: int = 12) -> None:
        self.now = at(d, hour).timestamp()


class Quiet(random.Random):
    """Azar sin sorpresas: ningún imprevisto, nadie encuentra piso, el primero gana."""

    def random(self) -> float:
        return 0.999

    def choice(self, seq):  # noqa: ANN001, ANN201
        return seq[0]


def at(d: int, hour: int = 12) -> datetime:
    """Octubre de 2026: los lunes son 5, 12, 19 y 26."""
    return datetime(2026, 10, d, hour, tzinfo=TIMEZONE)


async def make(tmp_path: Path, clock: Clock) -> tuple[EconomyService, HousingService]:
    economy_repo = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await economy_repo.initialize()
    housing_repo = HousingRepository(tmp_path / "bot.db")
    await housing_repo.initialize()
    economy = EconomyService(economy_repo, clock=clock)
    return economy, HousingService(housing_repo, economy, clock=clock, rng=Quiet())


async def give(economy: EconomyService, user_id: int, amount: int) -> None:
    await economy.repository.apply(GUILD, user_id, [LedgerEntry(amount, "prueba")])


def salary(tmp_path: Path, user_id: int, net_per_day: int, clock: Clock) -> None:
    """Nóminas de los últimos 30 días que suman `net_per_day` × 30 (lo que mira el banco)."""
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "INSERT INTO economy_payroll (guild_id, user_id, concept, gross, ss_worker, "
            "ss_employer, irpf, net, created_at) VALUES (?, ?, 'pala:obra', ?, 0, 0, 0, ?, ?)",
            (GUILD, user_id, net_per_day * 30, net_per_day * 30, clock.now - 3600),
        )


def ledger_sum(tmp_path: Path, user_id: int) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            (GUILD, user_id),
        ).fetchone()
    return int(total)


def sql(tmp_path: Path, query: str, *args: object) -> list[tuple]:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        return connection.execute(query, args).fetchall()


# -- Reglas ----------------------------------------------------------------------------


def test_la_cuota_francesa_devuelve_el_prestamo_en_360_meses() -> None:
    principal, rate = 347_200, 0.032
    payment = rules.installment(principal, rate)
    balance = principal
    for _ in range(rules.MORTGAGE_MONTHS):
        _interest, amortized = rules.mortgage_month(balance, rate, payment)
        balance -= amortized
    assert balance <= payment  # el redondeo hacia arriba la acaba un pelín antes
    assert rules.installment(0, rate) == 0
    assert rules.installment(36_000, 0.0) == 100


def test_la_cuota_de_un_bajo_o_una_vpo_cabe_en_el_sueldo_de_un_celador() -> None:
    celador = 6_000  # neto de cuatro turnos del puesto 1, a la escala de las nóminas
    payment = rules.installment(round(rules.price(BAJO) * rules.MORTGAGE_LTV), 0.032)
    assert rules.affordable(celador, payment)
    vpo = max(VPO_UNITS, key=lambda unit: unit.price)
    assert rules.affordable(celador, rules.installment(vpo.price, 0.032))
    assert celador <= rules.vpo_income_cap() and celador <= rules.bono_income_cap()


def test_el_banco_ejecuta_con_doce_cuotas_o_el_tres_por_ciento() -> None:
    assert not rules.must_execute(11, 100, 1_000_000)
    assert rules.must_execute(12, 100, 1_000_000)
    assert rules.must_execute(1, 30_000, 1_000_000)


def test_el_bono_es_250_euros_como_mucho_y_media_renta() -> None:
    assert rules.bono_amount(10_000) == rules.BONO_MAX == 2_500
    assert rules.bono_amount(3_000) == 1_500


def test_la_temporada_alta_canaria_es_el_invierno_y_el_igic_va_dentro() -> None:
    assert rules.season_factor(at(10).date()) < rules.season_factor(at(10).date().replace(month=12))
    assert rules.vacation_igic(10_700) == 700


def test_el_gran_tenedor_tiene_tope_en_zona_tensionada() -> None:
    assert rules.level_allowed("mercado", index=1.2, whole_homes=4, license=False)
    assert not rules.level_allowed("mercado", index=1.2, whole_homes=5, license=False)
    assert rules.level_allowed("mercado", index=1.05, whole_homes=9, license=False)
    assert not rules.level_allowed("vacacional", index=1.0, whole_homes=0, license=False)
    assert rules.level_allowed("vacacional", index=1.0, whole_homes=0, license=True)


def test_vivir_en_tu_casa_recarga_la_bateria_mas_rapido() -> None:
    contract = Contract(job="obra", level=1, battery=0.0, battery_at=0.0)
    at_home = Contract(job="obra", level=1, battery=0.0, battery_at=0.0, home_regen=1.2)
    assert battery_now(at_home, 3_600) == pytest.approx(battery_now(contract, 3_600) * 1.2)


# -- Hipoteca --------------------------------------------------------------------------


async def test_comprar_con_hipoteca_paga_la_entrada_y_firma_el_prestamo(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, ANA, 6_000, clock)
    await give(economy, ANA, 200_000)

    trade = await service.buy(GUILD, ANA, BAJO.key, 100, financed=True)

    deal = trade.deal
    assert deal.loan == round(deal.quote.price * rules.MORTGAGE_LTV)
    assert trade.balance == STARTING_BALANCE + 200_000 - (deal.quote.total - deal.loan)
    (mortgage,) = (await service.market(GUILD)).mortgages_of(ANA, BAJO.key)
    assert mortgage.balance == deal.loan and mortgage.payment == deal.payment
    assert mortgage.months_left == rules.MORTGAGE_MONTHS
    assert ledger_sum(tmp_path, ANA) == trade.balance


async def test_sin_nomina_el_banco_no_presta(tmp_path: Path) -> None:
    economy, service = await make(tmp_path, Clock(at(14)))
    await give(economy, ANA, 10_000_000)
    with pytest.raises(HousingError, match="banco"):
        await service.buy(GUILD, ANA, BAJO.key, 100, financed=True)
    assert (await service.market(GUILD)).holdings == {}


async def test_las_cuotas_se_cobran_y_sin_pagar_doce_llega_la_ejecucion(tmp_path: Path) -> None:
    clock = Clock(at(1))
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, ANA, 6_000, clock)
    await give(economy, ANA, 200_000)
    trade = await service.buy(GUILD, ANA, BAJO.key, 100, financed=True)
    clock.to(2)
    await service.close_days(GUILD)
    (mortgage,) = (await service.market(GUILD)).mortgages_of(ANA)
    assert mortgage.balance < trade.deal.loan and mortgage.unpaid_count == 0
    # Se queda sin un duro: las cuotas siguientes no se pagan.
    balance = await economy.balance(GUILD, ANA)
    await economy.repository.apply(GUILD, ANA, [LedgerEntry(-balance, "prueba")])
    # A las 12 cuotas sin pagar sale a subasta judicial; sin pujas, el banco se lo queda al 70 %.
    closed = []
    for d in range(3, 18):
        clock.to(d)
        await service.close_days(GUILD)
        closed += (await service.close_auctions(GUILD)).results

    assert [r.bank for r in closed] == [True]
    market = await service.market(GUILD)
    assert market.mortgages_of(ANA) == []
    assert BAJO.key not in market.holdings
    assert market.bank_debts.get(ANA, 0) > 0
    assert sql(tmp_path, "SELECT status FROM housing_mortgages")[0][0] == "ejecutada"
    assert ledger_sum(tmp_path, ANA) == await economy.balance(GUILD, ANA)
    # La deuda con el banco se cobra de lo que vaya entrando.
    debt = market.bank_debts[ANA]
    await give(economy, ANA, debt + 1_000)
    clock.to(18)
    await service.close_days(GUILD)
    assert (await service.market(GUILD)).bank_debts.get(ANA, 0) == 0


async def test_vender_con_hipoteca_la_cancela_con_lo_cobrado(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, ANA, 6_000, clock)
    await give(economy, ANA, 200_000)
    await service.buy(GUILD, ANA, BAJO.key, 100, financed=True)
    trade = await service.sell(GUILD, ANA, BAJO.key, 100)
    assert trade.deal.payoff > 0
    assert (await service.market(GUILD)).mortgages_of(ANA) == []
    assert ledger_sum(tmp_path, ANA) == trade.balance


async def test_amortizar_cancela_la_hipoteca_con_el_monedero(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, ANA, 6_000, clock)
    await give(economy, ANA, 2_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100, financed=True)
    (mortgage,) = (await service.market(GUILD)).mortgages_of(ANA)
    paid, balance = await service.amortize(GUILD, ANA, mortgage.id)
    assert paid == mortgage.owed
    assert (await service.market(GUILD)).mortgages_of(ANA) == []
    assert ledger_sum(tmp_path, ANA) == balance


async def test_el_euribor_se_mueve_los_lunes(tmp_path: Path) -> None:
    clock = Clock(at(11))
    _economy, service = await make(tmp_path, clock)
    service.rng = random.Random(4)
    clock.to(13)  # cierra el lunes 12
    await service.close_days(GUILD)
    assert sql(tmp_path, "SELECT week FROM housing_euribor") == [("2026-10-12",)]


async def test_la_hipoteca_resta_en_el_patrimonio(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, ANA, 6_000, clock)
    await give(economy, ANA, 200_000)
    trade = await service.buy(GUILD, ANA, BAJO.key, 100, financed=True)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.row_factory = sqlite3.Row
        net = assets_in(connection, GUILD)[ANA]
    fiscal = rules.fiscal_value(BAJO, 100, trade.deal.quote.price)
    assert net == fiscal - trade.deal.loan


# -- VPO y vivienda habitual -------------------------------------------------------------


async def test_la_vpo_se_sortea_se_compra_con_aval_y_se_devuelve_al_estado(tmp_path: Path) -> None:
    clock = Clock(at(11))
    economy, service = await make(tmp_path, clock)
    await give(economy, STATE_ACCOUNT_ID, 0)
    await service.vpo_signup(GUILD, ANA)
    clock.to(13)
    runs = await service.close_days(GUILD)  # el lunes 12 se sortea
    assert any(run.draws for run in runs)
    unit = (await service.market(GUILD)).reservation_of(ANA)
    assert unit == VPO_UNITS[0].key
    salary(tmp_path, ANA, 6_000, clock)
    await give(economy, ANA, 50_000)
    state = ledger_sum(tmp_path, STATE_ACCOUNT_ID)

    deal, balance = await service.vpo_buy(GUILD, ANA, unit)

    price = VPO_BY_KEY[unit].price
    notary_igic = round(deal.notary * rules.SERVICES_IGIC)
    assert deal.loan == price
    assert balance == STARTING_BALANCE + 50_000 - deal.notary - notary_igic
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) - state == price + notary_igic
    market = await service.market(GUILD)
    assert market.vpo_of(ANA) == unit and market.homes[ANA] == (HOME_VPO, unit)
    assert market.mortgages_of(ANA)[0].aval == "ico"
    with pytest.raises(HousingError):
        await service.vpo_signup(GUILD, ANA)  # ya tiene casa
    # La cuota de la hipoteca de la VPO cuenta para la deducción por vivienda habitual.
    await give(economy, ANA, 100_000)
    clock.to(14)
    await service.close_days(GUILD)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (home_paid,) = connection.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM economy_irpf_extras "
            "WHERE user_id = ? AND kind = 'vivienda'",
            (ANA,),
        ).fetchone()
    assert home_paid == deal.payment
    (mortgage,) = (await service.market(GUILD)).mortgages_of(ANA)
    assert mortgage.owed < deal.loan  # ya ha pagado una cuota

    back, balance = await service.vpo_return(GUILD, ANA)
    assert back.payoff == mortgage.owed
    market = await service.market(GUILD)
    assert market.vpo_of(ANA) is None and ANA not in market.homes
    assert ledger_sum(tmp_path, ANA) == balance


async def test_sin_partida_el_estado_no_recompra_la_vpo(tmp_path: Path) -> None:
    clock = Clock(at(11))
    economy, service = await make(tmp_path, clock)
    await service.vpo_signup(GUILD, ANA)
    clock.to(13)
    await service.close_days(GUILD)
    salary(tmp_path, ANA, 6_000, clock)
    await give(economy, ANA, 50_000)
    unit = (await service.market(GUILD)).reservation_of(ANA)
    await service.vpo_buy(GUILD, ANA, unit)
    estado = await economy.state_balance(GUILD)
    await economy.repository.apply(GUILD, STATE_ACCOUNT_ID, [LedgerEntry(-estado, "prueba")])
    with pytest.raises(InsufficientFundsError):
        await service.vpo_return(GUILD, ANA)
    assert (await service.market(GUILD)).vpo_of(ANA) == unit


async def test_quien_gana_mucho_no_entra_en_el_sorteo(tmp_path: Path) -> None:
    clock = Clock(at(11))
    _economy, service = await make(tmp_path, clock)
    salary(tmp_path, ANA, rules.vpo_income_cap() + 1, clock)
    with pytest.raises(HousingError, match="IPREM"):
        await service.vpo_signup(GUILD, ANA)


async def test_vivir_en_tu_piso_no_lo_alquila_y_paga_sus_recibos(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    await service.set_home(GUILD, ANA, BAJO.key)
    market = await service.market(GUILD)
    assert market.leases[BAJO.key].status == rules.HABITUAL
    assert market.homes[ANA] == (HOME_OWN, BAJO.key)
    assert await service.repository.home_of(GUILD, ANA) == (HOME_OWN, BAJO.key)
    balance = await economy.balance(GUILD, ANA)
    clock.to(15)
    await service.close_days(GUILD)
    costs = rules.monthly_costs(BAJO)
    ips = round(costs.insurance * rules.IPS_RATE)  # el seguro del hogar lleva el de primas
    # Y quien vive en ella paga los suministros (`casa`).
    bill = (await service.repository.home_status(GUILD, ANA)).bill
    assert await economy.balance(GUILD, ANA) == balance - costs.total - ips - bill.total
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.row_factory = sqlite3.Row
        assert assets_in(connection, GUILD).get(ANA, 0) == 0  # exenta hasta 3 millones
    assert await service.leave_home(GUILD, ANA) == HOME_OWN
    assert (await service.market(GUILD)).leases[BAJO.key].status == rules.VACANT


# -- Miembros de alquiler y bono --------------------------------------------------------


async def test_un_miembro_de_alquiler_paga_al_casero_y_cobra_el_bono(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await give(economy, BEA, 1_000_000)
    await give(economy, STATE_ACCOUNT_ID, 1_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    deal = await service.rent_to_member(GUILD, ANA, BAJO.key, BEA)
    lease = deal.lease
    assert lease.member == BEA and lease.bono
    assert (await service.market(GUILD)).homes[BEA] == (HOME_RENTED, BAJO.key)
    ana = await economy.balance(GUILD, ANA)
    bea = await economy.balance(GUILD, BEA)
    state = await economy.balance(GUILD, STATE_ACCOUNT_ID)
    clock.to(15)

    (run,) = await service.close_days(GUILD)

    bono = rules.bono_amount(lease.rent)
    assert run.bono == {BEA: bono}
    assert await economy.balance(GUILD, ANA) > ana
    assert await economy.balance(GUILD, BEA) <= bea - lease.rent + bono
    assert await economy.balance(GUILD, STATE_ACCOUNT_ID) < state + 1  # pagó el bono
    for member in (ANA, BEA, STATE_ACCOUNT_ID):
        assert ledger_sum(tmp_path, member) == await economy.balance(GUILD, member)
    # Lo que paga el inquilino queda para su deducción canaria (art. 15 del DL 1/2009).
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (paid,) = connection.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM economy_irpf_extras "
            "WHERE user_id = ? AND kind = 'alquiler'",
            (BEA,),
        ).fetchone()
    assert paid == lease.rent


async def test_sin_dinero_el_miembro_deja_de_pagar_y_sin_partida_no_hay_bono(
    tmp_path: Path,
) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await give(economy, BEA, 100_000)  # para la fianza
    await service.buy(GUILD, ANA, BAJO.key, 100)
    await service.rent_to_member(GUILD, ANA, BAJO.key, BEA)
    balance = await economy.balance(GUILD, BEA)
    await economy.repository.apply(GUILD, BEA, [LedgerEntry(-balance, "prueba")])
    clock.to(15)
    (run,) = await service.close_days(GUILD)
    assert (await service.market(GUILD)).leases[BAJO.key].status == rules.DEFAULTING
    assert run.bono == {}
    # Con dinero y sin un duro en el Estado: paga, pero el bono no llega.
    await give(economy, BEA, 1_000_000)
    estado = await economy.state_balance(GUILD)
    await economy.repository.apply(GUILD, STATE_ACCOUNT_ID, [LedgerEntry(-estado, "prueba")])
    clock.to(16)
    (run,) = await service.close_days(GUILD)
    assert (await service.market(GUILD)).leases[BAJO.key].status == rules.RENTED
    assert run.bono_unpaid == [BEA]


def test_tasa_de_esfuerzo_fianza_y_garantia() -> None:
    assert rules.effort(4_290, 6_000) == pytest.approx(0.715)
    assert rules.effort(4_290, 0) is None
    assert rules.needs_guarantee(4_290, 12_869, aval=False)
    assert not rules.needs_guarantee(4_290, 12_870, aval=False)
    assert not rules.needs_guarantee(4_290, 0, aval=True)
    assert rules.deposit_for(4_290, guarantee=False) == 4_290
    assert rules.deposit_for(4_290, guarantee=True) == 4_290 * 3


async def test_la_fianza_va_al_deposito_y_vuelve_la_noche_que_se_va(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await give(economy, BEA, 1_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    index = (await service.market(GUILD)).index(BAJO.barrio)  # comprar lo ha subido
    rent = rules.asking_rent(BAJO, RENT_LEVELS["referencia"], index)
    salary(tmp_path, BEA, rent * rules.INCOME_RENT_MULTIPLE, clock)
    deal = await service.rent_to_member(GUILD, ANA, BAJO.key, BEA)
    assert not deal.guarantee and deal.deposit == rent
    assert await economy.balance(GUILD, HOUSING_DEPOSIT_ACCOUNT_ID) == rent
    assert await service.leave_home(GUILD, BEA) == HOME_RENTED
    before = await economy.balance(GUILD, BEA)
    clock.to(15)
    (run,) = await service.close_days(GUILD)
    assert await economy.balance(GUILD, BEA) == before + rent
    assert await economy.balance(GUILD, HOUSING_DEPOSIT_ACCOUNT_ID) == 0
    assert [n.kind for n in run.news if n.user_id == BEA] == ["fianza_devuelta"]
    assert sql(tmp_path, "SELECT * FROM housing_deposits") == []
    for account in (ANA, BEA, HOUSING_DEPOSIT_ACCOUNT_ID):
        assert ledger_sum(tmp_path, account) == await economy.balance(GUILD, account)


async def test_sin_nomina_de_tres_rentas_se_pide_garantia_salvo_que_avalen_los_padres(
    tmp_path: Path,
) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await give(economy, BEA, 1_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    await service.buy(GUILD, ANA, "schamann_piso2", 100)
    deal = await service.rent_to_member(GUILD, ANA, BAJO.key, BEA)
    assert deal.guarantee and not deal.aval
    assert deal.deposit == rules.deposit_for(deal.lease.rent, guarantee=True)
    await service.leave_home(GUILD, BEA)
    deal = await service.rent_to_member(GUILD, ANA, "schamann_piso2", BEA, aval=True)
    assert not deal.guarantee and deal.aval
    assert deal.deposit == deal.lease.rent


async def test_sin_dinero_para_la_fianza_no_se_firma(tmp_path: Path) -> None:
    economy, service = await make(tmp_path, Clock(at(14)))
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    balance = await economy.balance(GUILD, BEA)
    await economy.repository.apply(GUILD, BEA, [LedgerEntry(-balance, "prueba")])
    with pytest.raises(InsufficientFundsError):
        await service.rent_to_member(GUILD, ANA, BAJO.key, BEA)
    market = await service.market(GUILD)
    assert BEA not in market.homes and market.leases[BAJO.key].member is None


async def test_al_desahuciar_al_miembro_el_casero_se_queda_lo_que_debia(tmp_path: Path) -> None:
    clock = Clock(at(1))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await give(economy, BEA, 100_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    deal = await service.rent_to_member(GUILD, ANA, BAJO.key, BEA)
    balance = await economy.balance(GUILD, BEA)
    await economy.repository.apply(GUILD, BEA, [LedgerEntry(-balance, "prueba")])
    clock.to(2)
    await service.close_days(GUILD)  # no paga: impago
    await service.evict(GUILD, ANA, BAJO.key)
    unpaid = (await service.market(GUILD)).leases[BAJO.key].unpaid
    day, news = 3, []
    while BEA in (await service.market(GUILD)).homes:
        clock.to(day)
        for run in await service.close_days(GUILD):
            news += run.news
        day += 1
        assert day < 30, "el juzgado no da nunca fecha"
    assert (await service.market(GUILD)).leases[BAJO.key].member is None
    ana = await economy.balance(GUILD, ANA)
    clock.to(day)
    (run,) = await service.close_days(GUILD)
    kept = min(deal.deposit, unpaid * deal.lease.rent)
    kinds = {n.kind: n.amount for n in run.news if n.user_id == BEA}
    assert kinds.get("fianza_incautada") == kept
    assert kinds.get("fianza_devuelta", 0) == deal.deposit - kept
    assert await economy.balance(GUILD, ANA) > ana  # cobra la fianza como renta
    assert await economy.balance(GUILD, HOUSING_DEPOSIT_ACCOUNT_ID) == 0
    for account in (ANA, BEA, HOUSING_DEPOSIT_ACCOUNT_ID):
        assert ledger_sum(tmp_path, account) == await economy.balance(GUILD, account)


# -- El aval de tus padres y el BCE ----------------------------------------------------


async def test_con_el_aval_de_tus_padres_el_banco_presta_lo_que_no_te_daba(
    tmp_path: Path,
) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, ANA, 2_500, clock)
    await give(economy, ANA, 200_000)
    with pytest.raises(HousingError, match="padres"):
        await service.buy(GUILD, ANA, BAJO.key, 100, financed=True)
    trade = await service.buy(GUILD, ANA, BAJO.key, 100, financed=True, aval=True)
    assert trade.deal.aval
    assert sql(tmp_path, "SELECT aval FROM housing_mortgages") == [(rules.PARENTS_AVAL,)]
    assert rules.parents_income() == 2_500  # media pensión de 1.500 € a la escala de nóminas


async def test_si_tu_nomina_basta_no_se_gasta_el_aval(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, ANA, 6_000, clock)
    await give(economy, ANA, 200_000)
    trade = await service.buy(GUILD, ANA, BAJO.key, 100, financed=True, aval=True)
    assert not trade.deal.aval
    assert sql(tmp_path, "SELECT aval FROM housing_mortgages") == [(None,)]


async def test_ejecutar_una_hipoteca_avalada_lo_paga_la_familia(tmp_path: Path) -> None:
    clock = Clock(at(1))
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, ANA, 2_500, clock)
    await give(economy, ANA, 200_000)
    await service.buy(GUILD, ANA, BAJO.key, 100, financed=True, aval=True)
    balance = await economy.balance(GUILD, ANA)
    await economy.repository.apply(GUILD, ANA, [LedgerEntry(-balance, "prueba")])
    executed = []
    for d in range(2, 18):
        clock.to(d)
        for run in await service.close_days(GUILD):
            executed += run.aval_executions
        executed += (await service.close_auctions(GUILD)).aval_executions
    assert executed == [ANA]


def test_la_frase_del_bce_sigue_al_euribor_y_no_cambia_en_la_semana() -> None:
    monday = datetime(2026, 10, 12).date()
    assert rules.ecb_phrase(0.02, 0.0225, monday) in rules.ECB_UP
    assert rules.ecb_phrase(0.02, 0.0175, monday) in rules.ECB_DOWN
    assert rules.ecb_phrase(0.02, 0.02, monday) in rules.ECB_HOLD
    assert rules.ecb_phrase(0.02, 0.0225, monday) == rules.ecb_phrase(0.0, 0.01, monday)


async def test_el_lunes_el_cierre_trae_el_euribor_de_antes_y_el_nuevo(tmp_path: Path) -> None:
    clock = Clock(at(11))
    _economy, service = await make(tmp_path, clock)
    clock.to(13)  # cierra el domingo 11 (no) y el lunes 12 (sí)
    runs = await service.close_days(GUILD)
    (monday,) = [run for run in runs if run.euribor is not None]
    assert monday.day.weekday() == 0
    before, after = monday.euribor
    assert before == rules.EURIBOR_START and abs(after - before) <= rules.EURIBOR_STEP


# -- Vacacional, gran tenedor, fondo buitre y SOCIMI ------------------------------------


async def test_la_licencia_vacacional_es_una_por_barrio_y_su_tasa_va_al_estado(
    tmp_path: Path,
) -> None:
    economy, service = await make(tmp_path, Clock(at(14)))
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    await service.buy(GUILD, ANA, "schamann_piso2", 100)
    with pytest.raises(HousingError, match="licencia"):
        await service.set_level(GUILD, ANA, BAJO.key, "vacacional")
    state = ledger_sum(tmp_path, STATE_ACCOUNT_ID)
    service.rng = random.Random(1)  # su primer random() es 0,13: la comunidad dice que sí
    fee, _balance = await service.buy_license(GUILD, ANA, BAJO.key)
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) - state == fee == rules.LICENSE_FEE
    await service.set_level(GUILD, ANA, BAJO.key, "vacacional")
    with pytest.raises(HousingError, match="saturado"):
        await service.buy_license(GUILD, ANA, "schamann_piso2")
    breakdown = await economy.tax_breakdown(GUILD)
    assert breakdown[ANA]["tasas"] == fee


async def test_el_gran_tenedor_en_zona_tensionada_no_puede_pedir_de_mercado(
    tmp_path: Path,
) -> None:
    economy, service = await make(tmp_path, Clock(at(14)))
    await give(economy, ANA, 100_000_000)
    for key in ("taco_bajo", "taco_piso3", "ofra_bajo", "ofra_piso2", "telde_bajo"):
        await service.buy(GUILD, ANA, key, 100)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute("UPDATE housing_index SET value = 1.2 WHERE barrio = 'taco'")
    with pytest.raises(HousingError, match="tensionada"):
        await service.set_level(GUILD, ANA, "taco_bajo", "mercado")
    await service.set_level(GUILD, ANA, "ofra_bajo", "mercado")  # Ofra no está tensionado


async def test_el_fondo_buitre_ofrece_un_15_por_ciento_mas_y_se_puede_aceptar(
    tmp_path: Path,
) -> None:
    clock = Clock(at(11))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 100_000_000)
    for key in ("taco_bajo", "ofra_bajo", "telde_bajo"):
        await service.buy(GUILD, ANA, key, 100)
    clock.to(13)
    (run, *_rest) = await service.close_days(GUILD)
    assert run.offers and run.offers[0][0] == ANA
    _member, key, price = run.offers[0]
    trade = await service.accept_offer(GUILD, ANA, key)
    assert trade.deal.vulture and trade.deal.quote.price == price
    assert key not in (await service.market(GUILD)).holdings


async def test_la_socimi_paga_el_19_del_dividendo_y_no_irpf_del_alquiler(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 100_000_000)
    for key in ("taco_bajo", "ofra_bajo", "telde_bajo"):
        await service.buy(GUILD, ANA, key, 100)
    with pytest.raises(HousingError):
        await service.found_socimi(GUILD, BEA)
    await service.found_socimi(GUILD, ANA)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "UPDATE housing_leases SET status = 'alquilado', tenant = 'familia', rent = 8000, "
            "until = '2027-01-01'"
        )
    clock.to(15)
    await service.close_days(GUILD)
    taxes = await economy.housing_tax_breakdown(GUILD)
    assert taxes[ANA]["ganancia"] > 0  # el 19 % del dividendo
    assert "alquiler" not in taxes[ANA]


# -- Obra nueva de la rama Ladrillo -----------------------------------------------------


def test_la_obra_nueva_sale_en_un_barrio_libre_y_nunca_pesa_mas_que_su_barrio() -> None:
    rng = random.Random(1)
    listing = rules.new_promotion(3, ["taco", "vegueta"], rng)
    assert listing.key == "obra_3" and rules.is_promotion(listing.key)
    assert listing.barrio in ("taco", "vegueta") and listing.energy == "A"
    assert listing.title.startswith("Obra nueva: ")
    assert 0 < rules.barrio_weight(listing) < 1
    assert rules.price(listing) > 0 and rules.reference_rent(listing) > 0
    with pytest.raises(ValueError):
        rules.new_promotion(4, [], rng)
    assert rules.promoter_fee(1_000_000) == 50_000


async def test_el_lunes_quien_esta_en_ladrillo_levanta_una_promocion(tmp_path: Path) -> None:
    clock = Clock(at(11))
    economy, service = await make(tmp_path, clock)
    clock.to(13)  # cierra el domingo 11 y el lunes 12

    async def builders() -> tuple[int, ...]:
        return (ANA,)

    runs = await service.close_days(GUILD, builders)
    (monday,) = [run for run in runs if run.promotions]
    ((builder, key),) = monday.promotions
    assert builder == ANA and rules.is_promotion(key)
    assert any(n.kind == "obra_nueva" and n.user_id == ANA for n in monday.news)
    market = await service.market(GUILD)
    listing = market.listing(key)
    assert listing is not None and listing in market.listings_of(listing.barrio)
    assert market.promotions[key].promoter == ANA

    # Bea compra: el 5 % del precio va a Ana, con su IRPF retenido; Ana compra en la suya
    # y no se paga comisión a sí misma.
    await give(economy, BEA, 20_000_000)
    before = await economy.balance(GUILD, ANA)
    trade = await service.buy(GUILD, BEA, key, 10)
    fee = rules.promoter_fee(trade.deal.quote.price)
    assert trade.deal.promoter == ANA and trade.deal.promoter_fee == fee > 0
    gained = await economy.balance(GUILD, ANA) - before
    assert 0 < gained <= fee
    await give(economy, ANA, 20_000_000)
    own = await service.buy(GUILD, ANA, key, 10)
    assert own.deal.promoter == ANA and own.deal.promoter_fee == 0
    for member in (ANA, BEA, STATE_ACCOUNT_ID):
        assert ledger_sum(tmp_path, member) == await economy.balance(GUILD, member)

    # La obra nueva vive como cualquier otro inmueble: se cierra cada noche.
    clock.to(14)
    await service.close_days(GUILD)
    assert key in (await service.market(GUILD)).leases


async def test_sin_nadie_en_ladrillo_no_hay_obra_nueva_y_con_todo_el_mapa_lleno_tampoco(
    tmp_path: Path,
) -> None:
    clock = Clock(at(11))
    _economy, service = await make(tmp_path, clock)
    clock.to(13)
    assert not any(run.promotions for run in await service.close_days(GUILD))
    full = Market(
        promotions={
            f"obra_{i}": Promotion(
                rules.new_promotion(i, [b.key], random.Random(i)), ANA, at(12).date()
            )
            for i, b in enumerate(BARRIOS, 1)
        }
    )
    result = DayRun(at(19).date())
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        service.repository._promotion_in(
            connection, GUILD, at(19).date(), 0.0, random.Random(1), full, ANA, result
        )
    assert result.promotions == []  # una por barrio: el tope
