"""Pruebas de lo pendiente de `saldo`: los avisos, los de pisos, el IMV, el trabajo y sus logros."""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from pathlib import Path

from bot.repositories.economy import EconomyRepository, LedgerEntry
from bot.repositories.housing import Decision, Holding, Market, Split
from bot.repositories.housing_finance import Deposit, Mortgage
from bot.repositories.housing_junta import Vote
from bot.repositories.housing_trade import Auction
from bot.repositories.work import WorkRepository
from bot.services import housing as rules
from bot.services.achievements import (
    SALDO_CLEAN_MIN,
    SALDO_DAYS_STAT,
    SALDO_KINDS_STAT,
    SALDO_RICHER_TIMES,
    SALDO_STREAK_STAT,
    saldo_peek_stats,
    saldo_stats,
    with_derived,
)
from bot.services.economy import STARTING_BALANCE, EconomyService, daily_amount
from bot.services.housing_catalog import LISTING_BY_KEY
from bot.services.housing_view import pending_items, portfolio_state
from bot.services.levels import TIMEZONE, local_day
from bot.services.pala import WorkService
from bot.services.pending import FIELD_LIMIT, SECTION_TITLES, Pending, Side, pending_fields
from bot.services.work import week_of
from bot.services.work_catalog import JOB_BY_KEY

GUILD = 1
USER = 10
OTHER = 20
TODAY = date(2026, 10, 20)
NOON = datetime(2026, 10, 20, 12, tzinfo=TIMEZONE).timestamp()
MIDNIGHT = datetime(2026, 10, 21, tzinfo=TIMEZONE).timestamp()


class Clock:
    def __init__(self, now: float = NOON) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


# -- Los avisos y sus secciones ------------------------------------------------------------


def test_las_secciones_salen_en_orden_y_lo_que_vence_antes_primero() -> None:
    items = [
        Pending(Side.COLLECT, "imv", "💶", "IMV", 100, 300.0),
        Pending(Side.PAY, "renta_debt", "⚖️", "Deuda", 50),
        Pending(Side.PAY, "mortgage", "🏦", "Hipoteca", 10, 200.0),
        Pending(Side.PAY, "renta_second", "✂️", "Segundo plazo", 10, 100.0),
    ]

    fields = pending_fields(iter(items))  # vale cualquier iterable, no solo una lista

    assert [name for name, _ in fields] == [SECTION_TITLES[Side.PAY], SECTION_TITLES[Side.COLLECT]]
    assert fields[0][1].splitlines() == [
        "✂️ Segundo plazo · <t:100:R>",
        "🏦 Hipoteca · <t:200:R>",
        "⚖️ Deuda",  # sin fecha, al final
    ]


def test_una_seccion_que_no_cabe_se_corta_con_cuantas_faltan() -> None:
    items = [Pending(Side.HELD, "deposit", "🔑", "x" * 100, when=float(i)) for i in range(30)]

    ((_name, value),) = pending_fields(items)

    assert len(value) <= FIELD_LIMIT
    shown = value.count("🔑")
    assert value.endswith(f"-# Y {30 - shown} más.")


# -- Pisos ---------------------------------------------------------------------------------


def test_los_pendientes_de_pisos_salen_con_su_importe_y_su_plazo() -> None:
    own, rented, auctioned = list(LISTING_BY_KEY)[:3]
    tonight = MIDNIGHT
    market = Market(
        holdings={
            own: {USER: Holding(own, USER, rules.SHARES, 1, 1, 0.0)},
            rented: {OTHER: Holding(rented, OTHER, rules.SHARES, 1, 1, 0.0)},
        },
        leases={
            own: rules.Lease(status=rules.RENTED, rent=1_000, arrears=300),
            rented: rules.Lease(status=rules.RENTED, rent=800, member=USER),
        },
        mortgages=(Mortgage(1, USER, own, 50_000, 40_000, 0.03, 500, 100, 2, 1_000, None),),
        bank_debts={USER: 5_000},
        decisions={own: (Decision(1, own, "averia", 700, TODAY, TODAY),)},
        votes=(Vote(1, own, OTHER, "derrama", "", 2_000, NOON + 3_600),),
        auctions=(
            Auction(
                1, auctioned, OTHER, 100, 1, NOON + 7_200, False, None, 9_000, USER, 9_500, 1, NOON
            ),
        ),  # fmt: skip
    )

    items = pending_items(market, USER, [Deposit(rented, USER, 1_600, 0)], TODAY)
    by_kind = {item.kind: item for item in items}

    assert by_kind["mortgage_unpaid"].amount == 500 + 1_000
    assert by_kind["mortgage_unpaid"].when == tonight
    assert "2 cuotas sin pagar" in by_kind["mortgage_unpaid"].text
    assert by_kind["bank_debt"].amount == 5_000
    assert by_kind["rent"].amount == 800 and by_kind["rent"].side == Side.PAY
    assert by_kind["arrears"].amount == 300
    assert by_kind["rents"].amount == 1_000 and by_kind["rents"].side == Side.COLLECT
    assert by_kind["decision"].when == tonight  # el último día para decidir es hoy
    assert by_kind["vote_derrama"].amount == 2_000 and by_kind["vote_derrama"].when == NOON + 3_600
    assert by_kind["auction_bid"].side == Side.HELD and by_kind["auction_bid"].amount == 9_500
    assert by_kind["deposit"].side == Side.HELD and by_kind["deposit"].amount == 1_600


def test_sin_nada_en_pisos_no_sale_nada() -> None:
    assert pending_items(Market(), USER, [], TODAY) == []


def test_las_rentas_de_saldo_suman_lo_mismo_que_la_cartera_de_pisos() -> None:
    # Un piso por habitaciones cobra de cada habitación y uno en impago no paga nada.
    piso = LISTING_BY_KEY["isleta_piso3"]
    normal, impago = [k for k in LISTING_BY_KEY if k != piso.key][:2]
    rooms = [f"{piso.key}~h{n}" for n in (1, 2, 3)]
    market = Market(
        holdings={
            key: {USER: Holding(key, USER, shares, 1, 1, 0.0)}
            for key, shares in ((piso.key, rules.SHARES), (normal, 50), (impago, rules.SHARES))
        },
        splits={piso.key: Split(rules.ROOMS, 3, 0)},
        leases={
            piso.key: rules.Lease(status=rules.BY_ROOMS),
            rooms[0]: rules.Lease(status=rules.RENTED, rent=300, arrears=90),
            rooms[1]: rules.Lease(status=rules.RENTED, rent=300),
            normal: rules.Lease(status=rules.RENTED, rent=1_000),
            impago: rules.Lease(status=rules.DEFAULTING, rent=700, unpaid=2),
        },
    )

    by_kind = {i.kind: i for i in pending_items(market, USER, [], TODAY)}
    cartera = portfolio_state(market, USER, "x", 0, [], {}, today=TODAY)

    assert by_kind["rents"].amount == 600 + 500
    assert by_kind["arrears"].amount == 90  # los atrasos de una habitación también salen
    rents = {i["key"]: i["rent"] for i in cartera["items"]}
    assert rents == {piso.key: "600 Y$/mes", normal: "500 Y$/mes", impago: "—"}
    # La cartera da la misma previsión que saldo, y saldo dice de quién es.
    assert cartera["tonight"]["rent"] == "1.100 Y$"
    assert "de los inquilinos que tienes al día" in by_kind["rents"].text


def test_la_fianza_de_un_piso_que_ya_dejaste_vuelve_esta_noche() -> None:
    key = next(iter(LISTING_BY_KEY))

    (item,) = pending_items(Market(), USER, [Deposit(key, USER, 1_600, 400)], TODAY)

    assert item.side == Side.COLLECT and item.amount == 1_200 and item.when == MIDNIGHT


def test_la_derrama_de_un_piso_compartido_se_reparte_por_participaciones() -> None:
    key = next(iter(LISTING_BY_KEY))
    market = Market(
        holdings={key: {USER: Holding(key, USER, 30, 1, 1, 0.0),
                        OTHER: Holding(key, OTHER, 70, 1, 1, 0.0)}},
        votes=(Vote(1, key, OTHER, "derrama", "", 1_000, NOON),),
        decisions={key: (Decision(1, key, "averia", 700, TODAY, TODAY),)},
    )  # fmt: skip

    items = pending_items(market, USER, [], TODAY)

    # Paga su parte y el imprevisto lo decide quien tiene la mayoría, no él.
    assert [(i.kind, i.amount) for i in items] == [("vote_derrama", 300)]


# -- IMV y trabajo -------------------------------------------------------------------------


async def economy(tmp_path: Path, clock: Clock) -> EconomyService:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    return EconomyService(repository, clock=clock)


async def test_el_imv_de_hoy_sale_hasta_que_se_cobra_con_la_racha_que_se_juega(
    tmp_path: Path,
) -> None:
    clock = Clock()
    service = await economy(tmp_path, clock)

    assert await service.daily_preview(GUILD, USER) == (daily_amount(1), 0)
    await service.claim_daily(GUILD, USER)
    assert await service.daily_preview(GUILD, USER) is None
    clock.now += 86_400
    assert await service.daily_preview(GUILD, USER) == (daily_amount(2), 1)
    clock.now += 2 * 86_400  # se salta un día: la racha vuelve a empezar
    assert await service.daily_preview(GUILD, USER) == (daily_amount(1), 0)


async def test_mirar_el_imv_no_cobra_nada(tmp_path: Path) -> None:
    service = await economy(tmp_path, Clock())
    before = await service.balance(GUILD, USER)

    await service.daily_preview(GUILD, USER)

    assert await service.balance(GUILD, USER) == before
    assert (await service.claim_daily(GUILD, USER)).claimed


async def test_la_cuota_de_autonomo_sale_hasta_el_primer_turno_de_la_semana(
    tmp_path: Path,
) -> None:
    clock = Clock()
    service = await economy(tmp_path, clock)
    repository = WorkRepository(tmp_path / "bot.db")
    await repository.initialize()
    work = WorkService(repository, service, clock=clock, rng=random.Random(1))
    await service.repository.apply(GUILD, USER, [LedgerEntry(1_000_000, "prueba")])

    assert await work.dues(GUILD, USER) == (0, 0)  # sin contrato
    await work.hire(GUILD, USER, "obra")
    contract = await repository.contract(GUILD, USER)
    assert contract is not None
    job = JOB_BY_KEY["obra"]
    level = next(p.level for p in job.positions if p.self_employed)
    contract.level, contract.mom_debt = level, 1_234
    await repository.save_contract(GUILD, USER, contract)

    fee = job.position(level).fee
    assert await work.dues(GUILD, USER) == (fee, 1_234)
    contract.fee_week = week_of(local_day(NOON))
    await repository.save_contract(GUILD, USER, contract)
    assert await work.dues(GUILD, USER) == (0, 1_234)


# -- Logros --------------------------------------------------------------------------------


def test_los_logros_del_saldo_cuentan_lo_que_se_ve() -> None:
    items = [
        Pending(Side.PAY, "renta_debt", "⚖️", "Deuda", 5_000),
        Pending(Side.COLLECT, "imv", "💶", "IMV", 500),
        Pending(Side.HELD, "auction_bid", "🔨", "Puja", 9_000),
    ]
    late = datetime(2026, 10, 20, 23, 30, tzinfo=TIMEZONE).timestamp()

    delta = saldo_stats(balance=666, items=items, when=late)

    assert delta.add["saldo_checks"] == 1
    assert delta.add["saldo_day_20261020"] == 1
    assert delta.peak["saldo_pending_max"] == 3
    for stat in ("saldo_all_sides", "saldo_red", "saldo_debt", "saldo_imv_late",
                 "saldo_exact_666", "saldo_kind_imv"):  # fmt: skip
        assert delta.add[stat] == 1, stat
    assert "saldo_clean" not in delta.add and "saldo_night" not in delta.add


def test_mirar_el_saldo_rico_y_sin_nada_por_pagar_es_libre_de_cargas() -> None:
    monday = datetime(2026, 10, 19, 0, 5, tzinfo=TIMEZONE).timestamp()

    delta = saldo_stats(balance=SALDO_CLEAN_MIN + 1, items=[], when=monday)

    assert delta.add["saldo_clean"] == 1 and delta.add["saldo_monday"] == 1
    assert "saldo_red" not in delta.add and "saldo_all_sides" not in delta.add


def test_mirar_el_saldo_de_alguien_mucho_mas_rico() -> None:
    assert saldo_peek_stats(own_balance=100, their_balance=100 * SALDO_RICHER_TIMES).add == {
        "saldo_peeks": 1,
        "saldo_peek_richer": 1,
    }
    assert saldo_peek_stats(own_balance=0, their_balance=5).add == {"saldo_peeks": 1}


def test_los_dias_la_racha_y_los_tipos_del_saldo_se_calculan() -> None:
    start = date(2026, 10, 1)
    days = [start + timedelta(days=n) for n in (0, 1, 2, 5, 6)]
    stats = {f"saldo_day_{d:%Y%m%d}": 1 for d in days}
    stats |= {"saldo_kind_imv": 3, "saldo_kind_rent": 1, "saldo_kind_wealth": 0}

    full = with_derived(stats)

    assert full[SALDO_DAYS_STAT] == 5
    assert full[SALDO_STREAK_STAT] == 3
    assert full[SALDO_KINDS_STAT] == 2
