"""Pruebas de `pisos`: catálogo, reglas, compra, venta, un día de rentas y el Patrimonio."""

from __future__ import annotations

import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from bot.repositories.economy import (
    STATE_ACCOUNT_ID,
    EconomyRepository,
    InsufficientFundsError,
    LedgerEntry,
)
from bot.repositories.housing import HousingError, HousingRepository, assets_in
from bot.services import housing as rules
from bot.services.economy import STARTING_BALANCE, EconomyService, format_amount
from bot.services.housing_catalog import (
    BARRIO_BY_KEY,
    BARRIOS,
    ISLANDS,
    KINDS,
    LISTING_BY_KEY,
    LISTINGS,
    NOMAD_DEMAND,
    RENT_LEVELS,
    TENANT_BY_KEY,
    THEMES,
    listings_of,
)
from bot.services.housing_market import HousingService
from bot.services.housing_view import portfolio_state
from bot.services.levels import TIMEZONE
from bot.services.taxes import WEALTH_MINIMUM, wealth_quote, wealth_tax

GUILD = 1
ANA = 10
BEA = 20
BAJO = LISTING_BY_KEY["schamann_bajo"]


class Clock:
    def __init__(self, when: datetime) -> None:
        self.now = when.timestamp()

    def __call__(self) -> float:
        return self.now


class Scripted(random.Random):
    """Azar de guion: `random()` devuelve los valores dados y luego `fill`."""

    def __init__(self, values: list[float] | None = None, fill: float = 0.99) -> None:
        super().__init__(0)
        self.values = list(values or [])
        self.fill = fill

    def random(self) -> float:
        return self.values.pop(0) if self.values else self.fill


def at(d: int, hour: int = 12) -> datetime:
    return datetime(2026, 10, d, hour, tzinfo=TIMEZONE)


async def make(tmp_path: Path, clock: Clock, rng: random.Random | None = None):
    economy_repo = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await economy_repo.initialize()
    housing_repo = HousingRepository(tmp_path / "bot.db")
    await housing_repo.initialize()
    economy = EconomyService(economy_repo, clock=clock)
    return economy, HousingService(housing_repo, economy, clock=clock, rng=rng or Scripted())


def ledger_sum(tmp_path: Path, user_id: int) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            (GUILD, user_id),
        ).fetchone()
    return int(total)


def housing_taxes(tmp_path: Path, kind: str) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(tax), 0) FROM economy_housing_tax WHERE guild_id = ? AND kind = ?",
            (GUILD, kind),
        ).fetchone()
    return int(total)


async def give(economy: EconomyService, user_id: int, amount: int) -> None:
    await economy.repository.apply(GUILD, user_id, [LedgerEntry(amount, "prueba")])


# -- Catálogo ----------------------------------------------------------------------------


def test_hay_veinte_barrios_de_gran_canaria_y_tenerife_con_nueve_inmuebles_cada_uno() -> None:
    assert len(BARRIOS) == 20
    assert {b.island for b in BARRIOS} == set(ISLANDS)
    assert sum(b.island == "gc" for b in BARRIOS) == 10
    assert len(LISTINGS) == 180
    assert len(LISTING_BY_KEY) == 180
    assert len(BARRIO_BY_KEY) == 20
    for barrio in BARRIOS:
        assert len(listings_of(barrio.key)) == 9, barrio.key
        assert 0 <= barrio.x <= 1 and 0 <= barrio.y <= 1
        assert barrio.theme in THEMES and barrio.theme in NOMAD_DEMAND, barrio.key
    for listing in LISTINGS:
        assert listing.kind in KINDS
        assert listing.key.startswith(listing.barrio + "_")
        assert rules.base_price(listing) > 0
        assert rules.reference_rent(listing) > 0


#: Los tres primeros de cada barrio, de antes de la ampliación (7.22). Sus claves guardan
#: quién tiene qué y el botón de cada uno: ni se cambian ni se mueven de sitio.
ORIGINAL = {
    "isleta": "bajo piso2 piso3", "canteras": "estudio piso2 atico",
    "schamann": "trastero bajo piso2", "triana": "garaje piso3 atico",
    "vegueta": "trastero piso2 edificio", "tafira": "garaje piso3 chalet",
    "telde": "bajo piso2 piso3", "arucas": "trastero piso2 chalet",
    "maspalomas": "estudio piso2 chalet", "mogan": "garaje atico chalet",
    "sanandres": "bajo piso2 chalet", "laguna": "estudio piso2 piso3",
    "santacruz": "garaje piso3 edificio", "ofra": "trastero bajo piso2",
    "taco": "trastero bajo piso3", "puertocruz": "estudio piso2 atico",
    "orotava": "garaje piso3 chalet", "medano": "estudio piso2 piso3",
    "cristianos": "estudio piso2 atico", "adeje": "garaje atico chalet",
}  # fmt: skip


def test_los_inmuebles_de_antes_siguen_con_su_clave_y_en_su_sitio() -> None:
    for barrio, slugs in ORIGINAL.items():
        first = [item.key for item in listings_of(barrio)[:3]]
        assert first == [f"{barrio}_{slug}" for slug in slugs.split()], barrio


def test_cada_tipo_y_cada_nivel_de_renta_tienen_quien_los_alquile() -> None:
    for kind in KINDS:
        for level in RENT_LEVELS:
            if level == rules.VACATION_LEVEL and kind not in rules.HOME_KINDS:
                continue  # sin licencia vacacional: solo las viviendas la piden
            assert any(t.rents(level, kind) for t in TENANT_BY_KEY.values()), (kind, level)


def test_los_negocios_los_alquilan_negocios_y_las_casas_personas() -> None:
    rng = random.Random(3)
    for _ in range(200):
        tenant = TENANT_BY_KEY[rules._tenant_for("referencia", "nave", rng)]
        assert "nave" in tenant.kinds
        tenant = TENANT_BY_KEY[rules._tenant_for("referencia", "piso2", rng)]
        assert not tenant.kinds


def test_los_nomadas_buscan_playa_y_no_poligono() -> None:
    level = RENT_LEVELS["nomadas"]
    day = date(2026, 7, 1)
    playa = rules.find_chance(LISTING_BY_KEY["canteras_estudio"], level, day)
    obrero = rules.find_chance(LISTING_BY_KEY["taco_bajo"], level, day)
    assert playa == level.find and obrero < playa


def test_lo_barato_renta_mas_que_lo_caro() -> None:
    def gross_yield(key: str) -> float:
        listing = LISTING_BY_KEY[key]
        return rules.reference_rent(listing) / rules.base_price(listing)

    assert gross_yield("schamann_bajo") > gross_yield("adeje_chalet")


def test_un_piso_se_paga_en_meses_y_no_en_dias_ni_en_anios() -> None:
    """Con un día = un mes, la renta neta paga el bajo de Schamann en unos meses de verdad."""
    costs = rules.monthly_costs(BAJO).total
    net = rules.reference_rent(BAJO) - costs
    days = rules.purchase_quote(BAJO, rules.SHARES).total / net
    assert 90 < days < 365


# -- Reglas ------------------------------------------------------------------------------


def test_la_compra_paga_el_itp_de_inmuebles_y_la_notaria() -> None:
    quote = rules.purchase_quote(BAJO, 10)
    assert quote.price == rules.share_price(BAJO) * 10
    assert quote.itp == round(quote.price * rules.ITP_RATE)
    assert quote.notary == round(quote.price * rules.NOTARY_RATE)
    # La notaría es un servicio: paga el IGIC general, que va al Estado.
    assert quote.notary_igic == round(quote.notary * rules.SERVICES_IGIC) > 0
    assert quote.total == quote.price + quote.itp + quote.notary + quote.notary_igic
    with pytest.raises(ValueError):
        rules.purchase_quote(BAJO, 0)
    with pytest.raises(ValueError):
        rules.purchase_quote(BAJO, rules.SHARES + 1)


def test_vender_con_ganancia_paga_el_19_por_ciento_y_la_deuda_va_con_el_piso() -> None:
    cost = rules.purchase_quote(BAJO, 50).total
    quote = rules.sale_quote(
        BAJO, 50, 50, cost, index=1.5, arrears=10_000, arrears_ibi=2_000, pool=100
    )
    assert quote.arrears == 5_000 and quote.arrears_ibi == 1_000
    # La comisión de la agencia paga el IGIC general y es gasto de la venta.
    assert quote.fee_igic == round(quote.fee * rules.SERVICES_IGIC) > 0
    selling = quote.fee + quote.fee_igic
    assert quote.gain == quote.price - selling - quote.arrears - cost
    assert quote.gain > 0
    assert quote.gain_tax == rules.capital_gain_tax(quote.gain)
    assert quote.received == quote.price - selling - quote.arrears - quote.gain_tax


def test_vender_con_perdidas_no_paga_irpf() -> None:
    cost = rules.purchase_quote(BAJO, 100).total
    quote = rules.sale_quote(BAJO, 100, 100, cost)
    assert quote.gain < 0
    assert quote.gain_tax == 0


def test_el_reparto_es_entre_los_duenios_y_lo_que_no_es_de_nadie_no_cuenta() -> None:
    parts = rules.split(9_001, {ANA: 60, BEA: 30})
    assert parts[BEA] == 3_000 and parts[ANA] + parts[BEA] == 9_001
    assert rules.owned_share(10_000, 90) == 9_000
    assert rules.split(0, {ANA: 100}) == {ANA: 0}


def test_la_renta_paga_primero_el_ibi_y_lo_que_falta_queda_a_deber() -> None:
    costs = rules.MonthlyCosts(community=300, insurance=100, ibi=200)
    paid = rules.settle_day(rent=1_000, costs=costs, extra=0, arrears=0, arrears_ibi=0)
    assert (paid.ibi, paid.costs, paid.net, paid.arrears) == (200, 400, 400, 0)
    empty = rules.settle_day(rent=0, costs=costs, extra=50, arrears=100, arrears_ibi=30)
    assert (empty.ibi, empty.costs, empty.net) == (0, 0, 0)
    assert empty.arrears == 100 + 600 + 50
    assert empty.arrears_ibi == 30 + 200
    short = rules.settle_day(rent=250, costs=costs, extra=0, arrears=0, arrears_ibi=0)
    assert (short.ibi, short.costs, short.net, short.arrears_ibi) == (200, 50, 0, 0)


def test_solo_el_alquiler_de_vivienda_tiene_la_reduccion_del_23_2() -> None:
    local = next(item for item in LISTINGS if item.kind not in rules.HOME_KINDS)
    assert rules.is_dwelling(BAJO, "referencia")
    assert not rules.is_dwelling(BAJO, rules.NOMAD_LEVEL)  # temporada (art. 3 LAU)
    assert not rules.is_dwelling(BAJO, rules.VACATION_LEVEL)  # turístico
    assert not rules.is_dwelling(local, "referencia")
    calm = rules.contract_reduction(BAJO, "referencia", index=1.0, rent=900, last_rent=1_000)
    assert calm == rules.RENT_REDUCTION
    assert rules.contract_reduction(local, "referencia", index=1.0, rent=900, last_rent=0) == 0


def test_el_90_es_para_quien_rebaja_mas_de_un_5_en_zona_tensionada() -> None:
    tense = rules.TENSE_INDEX
    cut = rules.contract_reduction(BAJO, "referencia", index=tense, rent=940, last_rent=1_000)
    small = rules.contract_reduction(BAJO, "referencia", index=tense, rent=950, last_rent=1_000)
    first = rules.contract_reduction(BAJO, "referencia", index=tense, rent=500, last_rent=0)
    assert cut == rules.TENSE_RENT_REDUCTION
    assert small == first == rules.RENT_REDUCTION


def test_los_contratos_de_antes_de_la_8_tienen_la_reduccion_general() -> None:
    old = rules.Lease(status=rules.RENTED, level="referencia", rent=1_000)
    assert rules.lease_reduction(BAJO, old) == rules.RENT_REDUCTION
    signed = rules.Lease(status=rules.RENTED, level="referencia", rent=1_000, reduction=0.9)
    assert rules.lease_reduction(BAJO, signed) == 0.9


def test_un_piso_vacio_imputa_el_1_1_por_ciento_del_catastral_al_ano() -> None:
    monthly = rules.imputed_rent(BAJO, rules.SHARES)
    assert monthly == round(rules.cadastral_value(BAJO) * 0.011 / 12)
    assert rules.imputed_rent(BAJO, rules.SHARES // 2) == pytest.approx(monthly / 2, abs=1)


def test_al_irse_el_inquilino_se_guarda_su_renta_para_el_siguiente_contrato() -> None:
    lease = rules.Lease(
        status=rules.RENTED, level="referencia", tenant="familia", rent=1_234,
        until=date(2026, 10, 1), since=date(2026, 8, 1),
    )  # fmt: skip

    class NoRenew:
        def random(self) -> float:
            return 0.99

        def choice(self, items):  # type: ignore[no-untyped-def]
            return items[0]

        def randint(self, low: int, high: int) -> int:
            return low

    outcome = rules.advance_day(BAJO, lease, date(2026, 10, 2), 1.0, NoRenew())  # type: ignore[arg-type]
    assert outcome.lease.status == rules.VACANT
    assert outcome.lease.last_rent == 1_234


def test_comprar_sube_el_indice_segun_lo_que_pesa_y_vender_lo_deja_igual() -> None:
    weight = rules.barrio_weight(BAJO)
    up = rules.index_after(1.0, BAJO, 100, bought=True)
    assert up == pytest.approx(1 + rules.INDEX_STEP * weight)
    assert rules.index_after(up, BAJO, 100, bought=False) == pytest.approx(1.0, abs=1e-12)
    assert sum(rules.barrio_weight(item) for item in listings_of("schamann")) == pytest.approx(1)
    trastero = LISTING_BY_KEY["schamann_trastero"]
    assert rules.index_after(1.0, trastero, 100, bought=True) < up  # lo barato apenas lo mueve
    assert rules.index_next_day(1.5) < 1.5
    assert rules.index_after(rules.INDEX_MAX, BAJO, 100, bought=True) == rules.INDEX_MAX


def test_el_indice_tiende_a_lo_que_tienen_comprado_los_miembros_y_no_a_uno() -> None:
    """Revisión 7.22: antes volvía a 1 y no pasaba de un ±1,5 %; la zona tensionada no saltaba."""
    barrio = listings_of("schamann")
    whole = rules.ownership_level((item, 100) for item in barrio)
    assert 1 + rules.INDEX_STEP <= whole < 1 + rules.INDEX_STEP * 1.1
    assert rules.ownership_level((item, 0) for item in barrio) == 1.0
    half = rules.ownership_level((item, 60) for item in barrio)
    assert rules.tense(half) and not rules.tense(rules.ownership_level((i, 40) for i in barrio))
    assert 1.0 < rules.index_next_day(1.0, whole) < whole
    assert rules.index_next_day(whole, whole) == whole
    assert rules.index_next_day(1.2) < 1.2  # sin nada comprado, vuelve a 1


def test_inflar_el_barrio_comprando_y_vendiendo_nunca_sale_a_cuenta() -> None:
    """Revisión: con el índice viejo, mil idas y vueltas de una participación lo subían un 30 %.

    Ahora una ida y vuelta lo deja igual, y comprar de verdad para inflar los demás pisos
    cuesta más (ITP, notaría y agencia) de lo que suben.
    """
    index = 1.0
    for _ in range(3_000):
        index = rules.index_after(index, BAJO, 1, bought=True)
        index = rules.index_after(index, BAJO, 1, bought=False)
    assert index == pytest.approx(1.0, abs=1e-9)
    for listing in LISTINGS:
        others = sum(rules.base_price(i) for i in listings_of(listing.barrio)) - rules.base_price(
            listing
        )
        cost = (
            rules.purchase_quote(listing, 100).total
            - rules.sale_quote(
                listing, 100, 100, 0, index=rules.index_after(1.0, listing, 100, bought=True)
            ).received
        )
        gain = (rules.index_after(1.0, listing, 100, bought=True) - 1) * others
        assert gain < cost, listing.key


def test_un_piso_vacio_encuentra_inquilino_con_la_renta_que_pide() -> None:
    day = date(2026, 10, 14)
    events_off = [0.99] * len(rules.EVENTS)
    lease = rules.Lease(level="mercado", since=day)
    out = rules.advance_day(BAJO, lease, day, 1.0, Scripted([*events_off, 0.0]))
    assert out.lease.status == rules.RENTED
    assert out.lease.rent == rules.asking_rent(BAJO, RENT_LEVELS["mercado"])
    assert out.lease.until == day + timedelta(days=RENT_LEVELS["mercado"].months)
    assert out.rent == 0  # el primer mes se cobra mañana
    assert TENANT_BY_KEY[out.lease.tenant].levels.count("mercado") == 1


def test_un_inquilino_que_deja_de_pagar_acaba_desahuciado() -> None:
    day = date(2026, 10, 14)
    events_off = [0.99] * len(rules.EVENTS)
    rented = rules.Lease(status=rules.RENTED, tenant="familia", rent=5_000, until=day + timedelta(
        days=30), since=day)  # fmt: skip
    out = rules.advance_day(BAJO, rented, day, 1.0, Scripted([*events_off, 0.0]))
    assert out.lease.status == rules.DEFAULTING and out.rent == 0
    evicting = rules.start_eviction(out.lease, day, random.Random(1))
    assert evicting.status == rules.EVICTING
    low, high = rules.EVICTION_DAYS
    assert day + timedelta(days=low) <= evicting.eviction_at <= day + timedelta(days=high)
    done = rules.advance_day(BAJO, evicting, evicting.eviction_at, 1.0, Scripted())
    assert done.lease.status == rules.VACANT and done.lease.tenant is None
    with pytest.raises(ValueError):
        rules.start_eviction(done.lease, day, random.Random(1))


def test_vender_de_una_en_una_paga_la_misma_deuda_que_de_golpe() -> None:
    """Revisión: antes, vender participación a participación se saltaba un 37 % de la deuda."""
    cost = rules.purchase_quote(BAJO, 100).total
    whole = rules.sale_quote(BAJO, 100, 100, cost, arrears=20_000, arrears_ibi=5_000)
    arrears, arrears_ibi, owned, paid, paid_ibi = 20_000, 5_000, 100, 0, 0
    while owned:
        quote = rules.sale_quote(BAJO, 1, owned, cost * owned // 100, arrears=arrears,
                                 arrears_ibi=arrears_ibi, pool=owned)  # fmt: skip
        paid, paid_ibi = paid + quote.arrears, paid_ibi + quote.arrears_ibi
        arrears, arrears_ibi, owned = (
            arrears - quote.arrears,
            arrears_ibi - quote.arrears_ibi,
            owned - 1,
        )
    assert paid == whole.arrears == 20_000
    assert paid_ibi == whole.arrears_ibi == 5_000


def test_el_patrimonio_cuenta_lo_pagado_o_el_catastral() -> None:
    assert rules.fiscal_value(BAJO, 100, 0) == rules.cadastral_value(BAJO)
    assert rules.fiscal_value(BAJO, 100, 10**9) == 10**9


# -- Dinero en la base de datos ------------------------------------------------------------


async def test_comprar_cobra_precio_itp_y_notaria_y_el_libro_cuadra(tmp_path: Path) -> None:
    economy, service = await make(tmp_path, Clock(at(14)))
    await give(economy, ANA, 1_000_000)
    trade = await service.buy(GUILD, ANA, BAJO.key, 40)
    quote = trade.deal.quote

    assert trade.balance == STARTING_BALANCE + 1_000_000 - quote.total
    assert ledger_sum(tmp_path, ANA) == trade.balance
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) == quote.itp + quote.notary_igic
    assert housing_taxes(tmp_path, "itp") == quote.itp
    assert housing_taxes(tmp_path, "igic_gastos") == quote.notary_igic
    market = await service.market(GUILD)
    assert market.shares_of(BAJO.key) == {ANA: 40}
    assert market.free_shares(BAJO.key) == 60
    assert market.index("schamann") > 1.0
    assert market.leases[BAJO.key].status == rules.VACANT
    assert trade.deal.first


async def test_sin_dinero_no_se_compra_nada(tmp_path: Path) -> None:
    economy, service = await make(tmp_path, Clock(at(14)))
    with pytest.raises(InsufficientFundsError):
        await service.buy(GUILD, ANA, BAJO.key, 100)
    market = await service.market(GUILD)
    assert market.holdings == {} and market.leases == {} and market.indexes == {}
    assert await economy.balance(GUILD, ANA) == STARTING_BALANCE


async def test_no_se_compran_mas_participaciones_de_las_que_quedan(tmp_path: Path) -> None:
    economy, service = await make(tmp_path, Clock(at(14)))
    await give(economy, ANA, 10_000_000)
    await give(economy, BEA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 70)
    with pytest.raises(HousingError, match="30"):
        await service.buy(GUILD, BEA, BAJO.key, 31)
    await service.buy(GUILD, BEA, BAJO.key, 30)
    with pytest.raises(HousingError):
        await service.buy(GUILD, BEA, BAJO.key, 1)


async def test_un_dia_alquilado_reparte_la_renta_y_hacienda_cobra_lo_suyo(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await give(economy, BEA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 75)
    await service.buy(GUILD, BEA, BAJO.key, 25)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "UPDATE housing_leases SET status = 'alquilado', tenant = 'familia', rent = 8000, "
            "until = '2027-01-01' WHERE listing = ?",
            (BAJO.key,),
        )
    # Ana cobra además un buen premio esta semana: su renta del alquiler ya retiene.
    await economy.pay_income(GUILD, ANA, gross=300_000, concept="nivel:30")
    before = {m: await economy.balance(GUILD, m) for m in (ANA, BEA)}
    state_before = ledger_sum(tmp_path, STATE_ACCOUNT_ID)
    clock.now = at(15).timestamp()

    runs = await service.close_days(GUILD)
    again = await service.close_days(GUILD)

    assert [r.day for r in runs] == [date(2026, 10, 14)] and again == []
    costs = rules.monthly_costs(BAJO)
    got = {m: await economy.balance(GUILD, m) - before[m] for m in (ANA, BEA)}
    net = 8_000 - costs.total
    assert 0 < got[BEA] < got[ANA] <= net * 75 // 100 + 1  # +1: el redondeo va al que más tiene
    assert sum(got.values()) <= net
    for member in (ANA, BEA):
        assert ledger_sum(tmp_path, member) == await economy.balance(GUILD, member)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (irpf,) = connection.execute(
            "SELECT COALESCE(SUM(-delta), 0) FROM economy_ledger "
            "WHERE user_id > 0 AND reason = 'irpf:pisos:renta'"
        ).fetchone()
    ibi = housing_taxes(tmp_path, "ibi")
    ips = housing_taxes(tmp_path, "ips")
    assert irpf > 0
    assert ibi == costs.ibi
    assert ips == round(costs.insurance * rules.IPS_RATE)  # impuesto de primas del seguro
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) - state_before == irpf + ibi + ips
    breakdown = await economy.tax_breakdown(GUILD)
    assert breakdown[ANA]["ibi"] > 0 and "itp_pisos" in breakdown[ANA]
    assert breakdown[ANA]["irpf_alquiler"] > 0


async def test_un_barrio_comprado_no_se_abarata_solo_al_pasar_los_dias(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    bought = (await service.market(GUILD)).index("schamann")
    clock.now = at(15).timestamp()

    await service.close_days(GUILD)

    market = await service.market(GUILD)
    assert bought > 1.0
    assert market.index("schamann") == pytest.approx(bought)
    assert market.index_level("schamann") == pytest.approx(bought)


async def test_un_piso_vacio_paga_sus_recibos_del_monedero(tmp_path: Path) -> None:
    """Revisión: un piso vacío también paga IBI y comunidad, del bolsillo de su dueño."""
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 100)
    balance = await economy.balance(GUILD, ANA)
    state = ledger_sum(tmp_path, STATE_ACCOUNT_ID)
    clock.now = at(15).timestamp()

    await service.close_days(GUILD)

    costs = rules.monthly_costs(BAJO)
    lease = (await service.market(GUILD)).leases[BAJO.key]
    assert lease.status == rules.VACANT and lease.arrears == 0
    ips = round(costs.insurance * rules.IPS_RATE)
    assert await economy.balance(GUILD, ANA) <= balance - costs.total - ips
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) - state == costs.ibi + ips
    # Cada impuesto con su nombre: el de primas del seguro no se apunta como IBI.
    assert housing_taxes(tmp_path, "ibi") == costs.ibi
    assert housing_taxes(tmp_path, "ips") == ips
    assert ledger_sum(tmp_path, ANA) == await economy.balance(GUILD, ANA)


async def test_quien_compra_despues_del_dia_no_cobra_ese_dia(tmp_path: Path) -> None:
    """Revisión: comprar entre la medianoche y el cierre no da la renta del día anterior."""
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await give(economy, BEA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 50)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "UPDATE housing_leases SET status = 'alquilado', tenant = 'familia', rent = 8000, "
            "until = '2027-01-01'"
        )
    clock.now = at(15, hour=0).timestamp() + 60  # las 00:01 del día siguiente
    await service.buy(GUILD, BEA, BAJO.key, 50)
    bea = await economy.balance(GUILD, BEA)

    await service.close_days(GUILD)

    assert await economy.balance(GUILD, BEA) == bea
    day = await service.repository.income(GUILD, ANA, date(2026, 10, 14))
    assert day.rent > 0


async def test_un_dia_vacio_sin_dinero_deja_la_deuda_en_el_piso_y_se_descuenta_al_vender(
    tmp_path: Path,
) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    total = rules.purchase_quote(BAJO, 100).total
    await give(economy, ANA, total - STARTING_BALANCE)  # se queda a cero
    await service.buy(GUILD, ANA, BAJO.key, 100)
    clock.now = at(15).timestamp()
    await service.close_days(GUILD)
    lease = (await service.market(GUILD)).leases[BAJO.key]
    assert lease.status == rules.VACANT
    assert lease.arrears >= rules.monthly_costs(BAJO).total
    balance = await economy.balance(GUILD, ANA)
    state = ledger_sum(tmp_path, STATE_ACCOUNT_ID)

    trade = await service.sell(GUILD, ANA, BAJO.key, 100)

    quote = trade.deal.quote
    assert quote.arrears == lease.arrears and quote.arrears_ibi == lease.arrears_ibi
    assert trade.balance == balance + quote.received
    assert ledger_sum(tmp_path, ANA) == trade.balance
    state_gets = quote.arrears_ibi + quote.gain_tax + quote.fee_igic
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) - state == state_gets
    market = await service.market(GUILD)
    assert BAJO.key not in market.leases and market.holdings == {}


async def test_vender_con_ganancia_paga_irpf_del_ahorro(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 10)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute("UPDATE housing_index SET value = 2.0")

    trade = await service.sell(GUILD, ANA, BAJO.key, 10)

    assert trade.deal.quote.gain_tax > 0
    assert housing_taxes(tmp_path, "plusvalia") == trade.deal.quote.gain_tax
    breakdown = await economy.tax_breakdown(GUILD)
    assert breakdown[ANA]["irpf_ahorro"] == trade.deal.quote.gain_tax


async def test_cambiar_la_renta_y_desahuciar_lo_decide_quien_tiene_la_mayoria(
    tmp_path: Path,
) -> None:
    economy, service = await make(tmp_path, Clock(at(14)))
    await give(economy, ANA, 10_000_000)
    await give(economy, BEA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 60)
    await service.buy(GUILD, BEA, BAJO.key, 10)
    with pytest.raises(HousingError, match="mitad"):
        await service.set_level(GUILD, BEA, BAJO.key, "mercado")
    lease = await service.set_level(GUILD, ANA, BAJO.key, "mercado")
    assert lease.level == "mercado"
    with pytest.raises(HousingError):
        await service.evict(GUILD, ANA, BAJO.key)  # nadie debe nada


async def test_el_patrimonio_cuenta_los_inmuebles(tmp_path: Path) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    edificio = LISTING_BY_KEY["vegueta_edificio"]
    price = rules.purchase_quote(edificio, 100).total
    await give(economy, ANA, price + 1_000_000)
    await service.buy(GUILD, ANA, edificio.key, 100)
    cash = await economy.balance(GUILD, ANA)
    await economy.charge_wealth_tax(GUILD)  # la primera semana solo activa
    clock.now = at(19, hour=1).timestamp()

    _, run = await economy.charge_wealth_tax(
        GUILD, assets=lambda connection: assets_in(connection, GUILD)
    )

    worth = cash + rules.fiscal_value(edificio, 100, rules.share_price(edificio) * 100)
    assert worth > WEALTH_MINIMUM
    assert [(c.user_id, c.balance, c.tax) for c in run.charges] == [
        (ANA, worth, min(cash, wealth_quote(worth).due))
    ]
    assert wealth_quote(worth).due < wealth_tax(worth)  # sin renta: límite conjunto


def test_todas_las_cifras_son_enteras() -> None:
    """Los Y$ son enteros: un `round(x, -3)` de un float devuelve un float."""
    for listing in LISTINGS:
        for index in (1.0, 1.137):
            assert type(rules.price(listing, index)) is int
            assert type(rules.share_price(listing, index)) is int
            assert type(rules.reference_rent(listing, index)) is int
            for level in RENT_LEVELS.values():
                assert type(rules.asking_rent(listing, level, index)) is int


async def test_hacienda_separa_todo_lo_que_pagan_los_pisos(tmp_path: Path) -> None:
    from bot.repositories.economy import Treasury
    from bot.services.achievements import housing_tax_stats
    from bot.services.tax_report import Snapshot, housing_lines, landlords_page

    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await economy.pay_income(GUILD, ANA, gross=300_000, concept="nivel:30")
    buy = await service.buy(GUILD, ANA, BAJO.key, 100)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "UPDATE housing_leases SET status = 'alquilado', tenant = 'familia', rent = 8000, "
            "until = '2027-01-01'"
        )
    clock.now = at(15).timestamp()
    await service.close_days(GUILD)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute("UPDATE housing_index SET value = 2.0")
    sale = await service.sell(GUILD, ANA, BAJO.key, 100)

    taxes = (await economy.housing_tax_breakdown(GUILD))[ANA]

    assert taxes["itp"] == buy.deal.quote.itp
    assert taxes["ibi"] == rules.monthly_costs(BAJO).ibi
    assert taxes["alquiler"] > 0
    assert taxes["ganancia"] == sale.deal.quote.gain_tax
    assert taxes["igic_gastos"] == buy.deal.quote.notary_igic + sale.deal.quote.fee_igic
    assert taxes["ips"] == round(rules.monthly_costs(BAJO).insurance * rules.IPS_RATE)
    assert len(housing_lines(taxes)) == 6
    snapshot = Snapshot(
        guild_id=GUILD, year=2026, treasury=Treasury(0, 0, 0, ()), bills=[], housing={ANA: taxes},
        business={}, solidarity=(0, 0, 0), names={ANA: "Ana"},
    )  # fmt: skip
    head, ranking = landlords_page(snapshot).split("### 🔑 Los caseros que más pagan")
    assert format_total(sum(taxes.values())) in head.splitlines()[0]
    assert "Ana" in ranking and "ITP" in ranking
    rent = (await economy.housing_rent_totals(GUILD))[ANA]
    delta = housing_tax_stats(taxes=taxes, rent=rent)
    assert delta.peak["pisos_tax_total"] == sum(taxes.values())
    assert delta.peak["pisos_tax_ganancia"] == taxes["ganancia"]


def format_total(amount: int) -> str:
    from bot.services.economy import format_amount

    return format_amount(amount)


async def test_quien_lo_tiene_todo_en_ladrillo_paga_el_patrimonio_con_un_embargo(
    tmp_path: Path,
) -> None:
    """Sin efectivo el lunes, el Patrimonio se embarga de lo que cobre por sus pisos."""
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    edificio = LISTING_BY_KEY["vegueta_edificio"]
    total = rules.purchase_quote(edificio, 100).total
    await give(economy, ANA, total - STARTING_BALANCE)  # se queda a cero tras comprar
    await service.buy(GUILD, ANA, edificio.key, 100)
    assert await economy.balance(GUILD, ANA) == 0
    await economy.charge_wealth_tax(GUILD)  # activación
    clock.now = at(19, hour=1).timestamp()

    _, run = await economy.charge_wealth_tax(
        GUILD, assets=lambda connection: assets_in(connection, GUILD)
    )

    (charge,) = run.charges
    assert charge.tax == 0 and charge.debt == wealth_quote(charge.balance).due > 0
    assert await economy.tax_debt(GUILD, ANA) == charge.debt
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "UPDATE housing_leases SET status = 'alquilado', tenant = 'familia', "
            "rent = 90000, until = '2027-01-01'"
        )
    state = ledger_sum(tmp_path, STATE_ACCOUNT_ID)
    clock.now = at(20).timestamp()
    await service.close_days(GUILD)

    owed = await economy.tax_debt(GUILD, ANA)
    embargoed = charge.debt - owed
    assert embargoed > 0
    assert ledger_sum(tmp_path, ANA) == await economy.balance(GUILD, ANA)
    breakdown = await economy.tax_breakdown(GUILD)
    assert breakdown[ANA]["deudas"] == embargoed
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) - state >= embargoed


async def test_si_el_precio_cambia_mientras_miras_no_se_cobra(tmp_path: Path) -> None:
    from bot.repositories.housing import PriceChanged

    economy, service = await make(tmp_path, Clock(at(14)))
    await give(economy, ANA, 10_000_000)
    await give(economy, BEA, 10_000_000)
    seen = rules.purchase_quote(BAJO, 10).total
    await service.buy(GUILD, BEA, "schamann_piso2", 100)  # sube el índice del barrio
    with pytest.raises(PriceChanged) as error:
        await service.buy(GUILD, ANA, BAJO.key, 10, expected=seen)
    assert error.value.amount > seen
    assert (await service.market(GUILD)).shares_of(BAJO.key) == {}
    trade = await service.buy(GUILD, ANA, BAJO.key, 10, expected=error.value.amount)
    assert trade.deal.quote.total == error.value.amount
    with pytest.raises(PriceChanged):
        await service.sell(GUILD, ANA, BAJO.key, 10, expected=10**9)
    assert (await service.market(GUILD)).shares_of(BAJO.key) == {ANA: 10}


async def test_al_salir_del_servidor_se_borran_sus_pisos(tmp_path: Path) -> None:
    economy, service = await make(tmp_path, Clock(at(14)))
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 10)
    await service.repository.delete_guild_data(GUILD)
    market = await service.market(GUILD)
    assert market.holdings == {} and market.leases == {} and market.indexes == {}


async def test_los_pisos_declaran_por_inmueble_imputan_los_vacios_y_la_casa_desgrava(
    tmp_path: Path,
) -> None:
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await economy.close_irpf_weeks(GUILD)  # el servidor entra en la 8.0.0 esta semana
    await give(economy, ANA, 10_000_000)
    await economy.pay_salary(GUILD, ANA, gross=400_000, concept="pala:obra")  # para el banco
    homes = sorted(
        (x for x in listings_of(BAJO.barrio) if x.key != BAJO.key and x.kind in rules.HOME_KINDS),
        key=rules.price,
    )
    empty, home = homes[-1], homes[0]
    await service.buy(GUILD, ANA, BAJO.key, 100)
    await service.buy(GUILD, ANA, empty.key, 100)
    trade = await service.buy(GUILD, ANA, home.key, 100, financed=True)
    assert trade.deal.loan > 0
    await service.set_home(GUILD, ANA, home.key)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "UPDATE housing_leases SET status = 'alquilado', tenant = 'familia', rent = 8000, "
            "until = '2027-01-01' WHERE listing = ?",
            (BAJO.key,),
        )
    clock.now = at(15).timestamp()
    await service.close_days(GUILD)

    with sqlite3.connect(tmp_path / "bot.db") as connection:
        rows = {
            ref: (kind, gross, costs, reduction)
            for ref, kind, gross, costs, reduction in connection.execute(
                "SELECT ref, kind, gross, costs, reduction FROM economy_tax_records "
                "WHERE user_id = ? AND kind IN ('inmueble', 'imputacion')",
                (ANA,),
            )
        }
        home_paid = connection.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM economy_irpf_extras WHERE user_id = ? "
            "AND kind = 'vivienda'",
            (ANA,),
        ).fetchone()[0]
    # El alquilado, por inmueble: la renta entera, sus gastos y la reducción de vivienda.
    kind, gross, costs, reduction = rows[BAJO.key]
    month = rules.monthly_costs(BAJO)
    assert (kind, gross, reduction) == ("inmueble", 8000, rules.RENT_REDUCTION)
    assert costs >= month.ibi + month.community + month.insurance
    # El vacío imputa el 1,1 % del catastral; la casa en la que vive, nada.
    assert rows[empty.key][:2] == ("imputacion", rules.imputed_rent(empty, 100))
    assert home.key not in rows
    # La cuota de la hipoteca de la vivienda habitual queda para la deducción canaria.
    assert home_paid == trade.deal.payment

    clock.now = at(19, hour=0).timestamp()
    await economy.close_irpf_weeks(GUILD)
    statement = await economy.irpf_statement(GUILD, ANA)

    assert statement is not None and statement.detail is not None
    (item,) = statement.income.properties
    assert (item.ref, item.income, item.reduction) == (BAJO.key, 8000, rules.RENT_REDUCTION)
    assert statement.income.imputed == rules.imputed_rent(empty, 100)
    assert statement.detail.tax.base == statement.filed.base
    assert statement.detail.result == statement.filed.result


async def test_lo_de_esta_noche_de_la_cartera_es_lo_que_apunta_el_cierre(tmp_path: Path) -> None:
    """Renta, IBI y gastos de «Esta noche» en `pisos` son los del cierre de esa noche."""
    clock = Clock(at(14))
    economy, service = await make(tmp_path, clock)
    await give(economy, ANA, 10_000_000)
    await service.buy(GUILD, ANA, BAJO.key, 50)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "UPDATE housing_leases SET status = 'alquilado', tenant = 'familia', rent = 8000, "
            "until = '2027-01-01', arrears = 300, arrears_ibi = 100"
        )
    market = await service.market(GUILD)
    night = portfolio_state(market, ANA, "Ana", 0, [], {}, today=date(2026, 10, 14))["tonight"]
    clock.now = at(15).timestamp()

    await service.close_days(GUILD)

    day = await service.repository.income(GUILD, ANA, date(2026, 10, 14))
    assert night["rent"] == format_amount(day.rent) == format_amount(4000)
    assert night["ibi"] == format_amount(day.ibi)
    # El impuesto de primas sale de la renta, pero el cierre lo apunta aparte de los gastos.
    assert night["costs"] == format_amount(day.costs + housing_taxes(tmp_path, "ips"))
