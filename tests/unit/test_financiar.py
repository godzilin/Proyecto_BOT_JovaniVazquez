"""Pruebas de 💳 Financiar: las ofertas, el préstamo de compra, Paga en 3 y el IPC del crédito."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from bot.cogs.shop import cash_part, financed_purchase
from bot.repositories.housing import HousingError
from bot.repositories.housing_finance import LOAN_BNPL, LOAN_PURCHASE
from bot.services import credit
from bot.services import housing as rules
from bot.services import loans as lending
from bot.services.economy import STARTING_BALANCE
from bot.services.housing_catalog import LISTING_BY_KEY, REFORMS
from bot.services.shop import TicketLine, emblematic, quote
from bot.services.shop_catalog import CATALOG, HOUSE_AISLE, TITLE_KEYS
from tests.unit.test_loans import ANA, GUILD, Clock, empty, ledger_sum, make, salary, sql
from tests.unit.test_shop import NOW
from tests.unit.test_shop import item as shop_item

COCHE = credit.Purchase("Seat Ibiza de segunda mano", 20_000, "tienda:lujo")
CAFE = credit.Purchase("Barraquito", 200, "pala")


# -- Reglas ----------------------------------------------------------------------------------


def test_la_comision_se_financia_y_llega_justo_lo_que_cuesta() -> None:
    offer = credit.offer(10_000, credit.Plan.NORMAL, 12, 0.095)
    assert offer.quote.received == 10_000
    assert offer.quote.fee == offer.quote.principal - 10_000 > 0
    assert offer.extra == offer.total - 10_000 > offer.quote.fee


def test_la_carencia_suma_los_intereses_y_se_paga_mas() -> None:
    plain = credit.offer(10_000, credit.Plan.NORMAL, 12, 0.095)
    grace = credit.offer(10_000, credit.Plan.NORMAL, 12, 0.095, grace=True)
    assert grace.balance > plain.balance == plain.quote.principal
    assert grace.total > plain.total
    # Los intereses de la carencia van al mismo tipo y la comisión se reparte en más tiempo:
    # la TAE hasta baja, pero se paga más (la letra pequeña).
    assert grace.tae < plain.tae


def test_el_cero_por_ciento_no_es_gratis_y_a_seis_cuotas_es_una_trampa() -> None:
    normal = credit.offer(10_000, credit.Plan.NORMAL, 12, 0.095)
    zero12 = credit.offer(10_000, credit.Plan.ZERO, 12, rate=0.2, grace=True)  # se ignoran
    zero6 = credit.offer(10_000, credit.Plan.ZERO, 6)
    assert zero12.quote.rate == 0 and not zero12.grace and zero12.quote.premium > 0
    assert 0 < zero12.tae < normal.tae < zero6.tae


def test_paga_en_3_sale_por_lo_que_cuesta() -> None:
    offer = credit.offer(300, credit.Plan.BNPL, 3)
    assert offer.total == 300 and offer.quote.fee == 0 and offer.tae == 0
    assert credit.late_fee(100) == credit.BNPL_LATE_MIN
    assert credit.late_fee(1_000) == 250


def test_que_planes_tiene_cada_compra() -> None:
    day = date(2026, 10, 25)
    picked, *_ = credit.zero_picks(day)
    other = next(c for c in credit.categories() if c not in credit.zero_picks(day))
    assert credit.plans_for(credit.Purchase("x", 50, other), day) == ()
    assert credit.plans_for(credit.Purchase("x", 999, picked), day) == (credit.Plan.BNPL,)
    assert credit.plans_for(credit.Purchase("x", 1_000, other), day) == (credit.Plan.NORMAL,)
    assert credit.Plan.ZERO in credit.plans_for(credit.Purchase("x", 1_000, picked), day)
    assert credit.plans_for(credit.Purchase("x", 800_000, picked), day) == ()
    assert credit.terms(credit.Plan.NORMAL, works=True)[-1] == 48
    # El 0 % cambia cada semana y es el mismo toda la semana.
    assert credit.zero_picks(date(2026, 10, 19)) == credit.zero_picks(day)
    assert len({credit.zero_picks(date(2026, 10, d)) for d in range(1, 29, 7)}) > 1


def test_el_ipc_sube_con_lo_financiado_hasta_el_tope() -> None:
    assert credit.ipc_pct(0, 1_000_000) == 0
    assert credit.ipc_pct(50_000, 1_000_000) == 5
    assert credit.ipc_pct(900_000, 1_000_000) == credit.IPC_CAP
    assert credit.ipc_pct(10, 0) == credit.IPC_CAP
    assert credit.inflate(1_000, 7) == 1_070


def test_el_ipc_encarece_la_tienda_y_las_obras() -> None:
    article = shop_item(price=1_000)
    before = quote(article, NOW)
    credit.set_ipc(article.guild_id, 10)
    after = quote(article, NOW)
    assert after.price == 1_100 and after.ipc == 10 and after.total > before.total
    piso = LISTING_BY_KEY["isleta_piso3"]
    reform = next(r for r in REFORMS if piso.kind in r.kinds)
    cost = rules.reform_quote(piso, reform).cost
    assert rules.reform_quote(piso, reform, ipc=10).cost == cost + cost // 10
    split = rules.split_quote(piso, rules.ROOMS).cost
    assert rules.split_quote(piso, rules.ROOMS, ipc=10).cost == split + split // 10


def test_lo_numerado_y_los_titulos_son_emblematicos() -> None:
    assert emblematic(shop_item(stock=1))
    assert emblematic(shop_item(catalog_key=next(iter(TITLE_KEYS))))
    assert not emblematic(shop_item())


def _line(**fields: object) -> TicketLine:
    piece = shop_item(**fields)
    return TicketLine(piece, 1, quote(piece, NOW))


def test_en_un_carrito_mezclado_lo_unico_va_al_contado() -> None:
    normal, numbered = _line(id=1, price=20_000), _line(id=2, price=5_000, stock=5)
    purchase = financed_purchase([normal, numbered])
    assert purchase is not None
    assert purchase.amount == normal.total
    assert cash_part([normal, numbered]) == numbered.total
    assert financed_purchase([numbered]) is None


def test_el_cero_por_ciento_del_carrito_solo_vale_con_un_pasillo() -> None:
    other = next(e.key for e in CATALOG if e.aisle != HOUSE_AISLE.key and e.key not in TITLE_KEYS)
    house = _line(id=1, price=20_000)
    same = financed_purchase([house, _line(id=2, price=3_000)])
    mixed = financed_purchase([house, _line(id=3, price=3_000, catalog_key=other)])
    assert same is not None and same.category == f"tienda:{HOUSE_AISLE.key}"
    assert mixed is not None and mixed.category == "tienda"
    assert "tienda" not in credit.categories()


# -- Con la base de datos --------------------------------------------------------------------


async def _finance(service, purchase=COCHE, plan=credit.Plan.NORMAL, months=12, **kw):  # noqa: ANN001, ANN201
    rate = 0.0
    if plan is credit.Plan.NORMAL:
        desk, _income = await service.loan_desk(GUILD, ANA)
        factors = lending.profile_factors(desk.profile, insured=kw.get("insured", False))
        rate = credit.purchase_rate(desk.euribor, factors)
    return await service.finance(GUILD, ANA, purchase, plan=plan, months=months, rate=rate, **kw)


async def test_financiar_trae_lo_que_cuesta_y_el_ipc_sube_al_recalcularlo(tmp_path: Path) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, 6_000, clock)

    signed, balance = await _finance(service)

    assert balance == STARTING_BALANCE + COCHE.amount
    assert signed.loan.kind == LOAN_PURCHASE and signed.loan.listing == COCHE.label
    assert signed.loan.principal == COCHE.amount + signed.quote.fee
    assert ledger_sum(tmp_path) == balance
    (loan,) = (await service.market(GUILD)).loans_of(ANA)
    assert loan.purchase and loan.balance == loan.principal
    # El IPC no cambia hasta que se recalcula: la compra cuesta lo que se ha financiado.
    assert credit.ipc(GUILD) == 0
    money = balance  # solo hay un miembro
    assert await service.refresh_ipc(GUILD) == credit.ipc_pct(loan.owed, money) > 0
    assert credit.ipc(GUILD) > 0 and (await service.market(GUILD)).ipc == credit.ipc(GUILD)


async def test_si_la_compra_falla_la_financiacion_se_deshace(tmp_path: Path) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, 6_000, clock)
    signed, _ = await _finance(service)

    await service.unfinance(GUILD, ANA, signed.loan.id)

    assert await economy.balance(GUILD, ANA) == STARTING_BALANCE
    assert (await service.market(GUILD)).loans_of(ANA) == []
    assert credit.ipc(GUILD) == 0


async def test_deshacer_sin_el_dinero_deja_la_financiacion_viva(tmp_path: Path) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, 6_000, clock)
    signed, _ = await _finance(service)
    await empty(economy)

    await service.unfinance(GUILD, ANA, signed.loan.id)  # no lanza

    assert len((await service.market(GUILD)).loans_of(ANA)) == 1


async def test_la_carencia_retrasa_la_primera_cuota(tmp_path: Path) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, 6_000, clock)
    signed, _ = await _finance(service, grace=True)
    for d in range(2, 3 + credit.GRACE_DAYS):
        clock.to(d)
        await service.close_days(GUILD)
    (loan,) = (await service.market(GUILD)).loans_of(ANA)
    assert loan.months_left == loan.months and loan.paid == 0
    clock.to(3 + credit.GRACE_DAYS)
    await service.close_days(GUILD)
    (loan,) = (await service.market(GUILD)).loans_of(ANA)
    assert loan.months_left == loan.months - 1 and loan.paid == signed.quote.monthly


async def test_el_35_por_ciento_frena_salvo_en_paga_en_3(tmp_path: Path) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)  # sin nómina: no le cabe nada
    with pytest.raises(HousingError, match="35 %"):
        await _finance(service)
    signed, balance = await _finance(service, CAFE, credit.Plan.BNPL, 3)
    assert signed.loan.kind == LOAN_BNPL and balance == STARTING_BALANCE + CAFE.amount


async def test_hay_tope_de_compras_en_paga_en_3(tmp_path: Path) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)
    for _ in range(credit.MAX_BNPL):
        await _finance(service, CAFE, credit.Plan.BNPL, 3)
    with pytest.raises(HousingError, match="Paga en 3"):
        await _finance(service, CAFE, credit.Plan.BNPL, 3)


async def test_un_plan_o_un_plazo_que_no_vale_no_se_firma(tmp_path: Path) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, 6_000, clock)
    with pytest.raises(HousingError, match="plazo"):
        await _finance(service, months=48)  # solo las obras llegan a 48
    with pytest.raises(HousingError):
        await _finance(service, CAFE)  # 200 Y$ solo van en Paga en 3


async def test_paga_en_3_sin_pagar_lleva_recargo_y_vence(tmp_path: Path) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)
    signed, _ = await _finance(service, CAFE, credit.Plan.BNPL, 3)
    await empty(economy)
    for d in (2, 3):  # el día 1 no tiene cuota; la del día 2 no llega
        clock.to(d)
        await service.close_days(GUILD)
    (loan,) = (await service.market(GUILD)).loans_of(ANA)
    payment = signed.quote.payment
    assert loan.unpaid_count == 1
    assert loan.unpaid_amount == payment + credit.late_fee(payment)
    for d in range(4, 3 + lending.DEFAULT_INSTALLMENTS):
        clock.to(d)
        await service.close_days(GUILD)
    market = await service.market(GUILD)
    assert market.loans_of(ANA) == [] and market.bank_debts[ANA] > CAFE.amount
    assert sql(tmp_path, "SELECT status FROM housing_loans")[0][0] == "vencido"
