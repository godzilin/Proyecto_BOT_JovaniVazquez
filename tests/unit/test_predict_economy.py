"""Pruebas del dinero de `predice`: depósito, resolución, anulación e IRPF.

Lo apostado sale del monedero al depósito (`PREDICT_ACCOUNT_ID`) sin tocar el día del
casino. Al resolver vuelve del depósito y apuesta y premio entran a la vez en el día del
casino de hoy, con su retención del juego. Anular devuelve lo apostado sin tocar el casino.
Cada flujo prueba que el libro cuadra con el saldo, que el Estado recibe lo retenido y que
las devoluciones de Hacienda salen de su cuenta. A diferencia de `deportes`, hay una
predicción por miembro y pregunta (`market_id`), el tope suma las preguntas del mismo evento,
solo hay dos lados (0 y 1) y no hay combinadas.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bot.repositories.economy import (
    PREDICT_ACCOUNT_ID,
    EconomyRepository,
    InsufficientFundsError,
    LedgerEntry,
)
from bot.services.economy import (
    STARTING_BALANCE,
    STATE_ACCOUNT_ID,
    EconomyService,
    PredictBet,
    PredictBetTakenError,
    PredictStakeCapError,
)
from bot.services.levels import local_day
from bot.services.predice import GAME, MAX_STAKE, MIN_STAKE, payout
from bot.services.taxes import GAMBLING_EXEMPT, gambling_day_tax

GUILD = 1
OTHER_GUILD = 2
USER = 10
OTHER = 11
CHANNEL = 99
ENDS = 1_800_003_600.0
CREATED = 1_800_000_000.0
ODDS = 250
STAKE = MIN_STAKE * 10
BIG = 10 * GAMBLING_EXEMPT


class FakeClock:
    """Reloj controlable."""

    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


async def make_service(tmp_path: Path, clock: FakeClock | None = None) -> EconomyService:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    return EconomyService(repository, clock=clock or FakeClock())


def make_bet(
    *,
    guild: int = GUILD,
    user: int = USER,
    event_id: str = "ev1",
    market_id: str = "m1",
    stake: int = STAKE,
    odds: int = ODDS,
    side: int = 0,
    ends: float = ENDS,
    created_at: float = CREATED,
) -> PredictBet:
    return PredictBet(
        id=0,
        guild_id=guild,
        user_id=user,
        channel_id=CHANNEL,
        event_id=event_id,
        event_title="¿Quién gana las elecciones?",
        market_id=market_id,
        question="¿Ganará el candidato A?",
        side=side,
        side_label="Sí" if side == 0 else "No",
        section="politica",
        odds=odds,
        stake=stake,
        ends=ends,
        created_at=created_at,
    )


async def rich_service(tmp_path: Path, clock: FakeClock | None = None) -> EconomyService:
    """Un servicio con `USER` y `OTHER` con saldo de sobra para apuestas grandes."""
    service = await make_service(tmp_path, clock)
    await service.grant(GUILD, USER, amount=100 * BIG, reason="test:saldo")
    await service.grant(GUILD, OTHER, amount=100 * BIG, reason="test:saldo")
    return service


def query(tmp_path: Path, sql: str, *params: object) -> list[tuple]:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        return connection.execute(sql, params).fetchall()


def wallet(tmp_path: Path, user_id: int, guild: int = GUILD) -> int:
    rows = query(
        tmp_path,
        "SELECT balance FROM economy_wallets WHERE guild_id = ? AND user_id = ?",
        guild,
        user_id,
    )
    return int(rows[0][0]) if rows else 0


def deposit(tmp_path: Path) -> int:
    return wallet(tmp_path, PREDICT_ACCOUNT_ID)


def state(tmp_path: Path) -> int:
    return wallet(tmp_path, STATE_ACCOUNT_ID)


def casino_days(tmp_path: Path, user_id: int = USER) -> list[tuple[str, int, int]]:
    return [
        (str(day), int(net), int(withheld))
        for day, net, withheld in query(
            tmp_path,
            "SELECT day, net, withheld FROM economy_gambling_days "
            "WHERE guild_id = ? AND user_id = ? ORDER BY day",
            GUILD,
            user_id,
        )
    ]


def assert_ledger_balances(tmp_path: Path) -> None:
    """Cada monedero (miembros, depósito y Estado) vale exactamente lo que suma su libro."""
    wallets = query(tmp_path, "SELECT guild_id, user_id, balance FROM economy_wallets")
    assert wallets
    for guild_id, user_id, balance in wallets:
        (total,) = query(
            tmp_path,
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            guild_id,
            user_id,
        )[0]
        assert total == balance, (guild_id, user_id)


def today(clock: FakeClock) -> str:
    return local_day(clock.now).isoformat()


# -- Apostar ----------------------------------------------------------------------------


async def test_apostar_cobra_y_llena_el_deposito_sin_tocar_el_casino(tmp_path: Path) -> None:
    service = await make_service(tmp_path)

    saved, balance = await service.predict_bet(make_bet(), game=GAME)

    assert saved.id > 0
    assert saved.status == "open"
    assert (saved.stake, saved.odds, saved.side, saved.market_id) == (STAKE, ODDS, 0, "m1")
    assert balance == STARTING_BALANCE - STAKE
    assert wallet(tmp_path, USER) == balance
    assert deposit(tmp_path) == STAKE
    assert casino_days(tmp_path) == []
    assert state(tmp_path) == 0
    assert_ledger_balances(tmp_path)


async def test_apostar_deja_los_motivos_deposito_en_el_libro(tmp_path: Path) -> None:
    service = await make_service(tmp_path)

    await service.predict_bet(make_bet(), game=GAME)

    reasons = query(
        tmp_path,
        "SELECT user_id, delta, reason FROM economy_ledger WHERE reason = ? ORDER BY user_id",
        "predice:deposito",
    )
    assert reasons == [
        (PREDICT_ACCOUNT_ID, STAKE, "predice:deposito"),
        (USER, -STAKE, "predice:deposito"),
    ]


async def test_la_prediccion_guardada_conserva_todos_sus_datos(tmp_path: Path) -> None:
    service = await make_service(tmp_path)

    saved, _ = await service.predict_bet(make_bet(side=1), game=GAME)

    (stored,) = await service.predict_bets(GUILD, USER, limit=5)
    assert stored == saved
    assert stored.side == 1
    assert stored.side_label == "No"
    assert (stored.event_id, stored.ends, stored.created_at) == ("ev1", ENDS, CREATED)


async def test_una_segunda_prediccion_a_la_misma_pregunta_no_cobra_nada(
    tmp_path: Path,
) -> None:
    service = await make_service(tmp_path)
    await service.predict_bet(make_bet(side=0), game=GAME)
    before = (wallet(tmp_path, USER), deposit(tmp_path))

    with pytest.raises(PredictBetTakenError):
        await service.predict_bet(make_bet(side=0, stake=STAKE * 2), game=GAME)

    assert (wallet(tmp_path, USER), deposit(tmp_path)) == before
    assert len(await service.open_predict_bets()) == 1
    assert_ledger_balances(tmp_path)


async def test_la_segunda_prediccion_con_el_otro_lado_tampoco_se_cobra(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    await service.predict_bet(make_bet(side=0), game=GAME)
    before = (wallet(tmp_path, USER), deposit(tmp_path))

    with pytest.raises(PredictBetTakenError):
        await service.predict_bet(make_bet(side=1), game=GAME)

    assert (wallet(tmp_path, USER), deposit(tmp_path)) == before
    assert len(await service.open_predict_bets()) == 1
    assert_ledger_balances(tmp_path)


async def test_la_misma_pregunta_con_otro_evento_tambien_cuenta_como_ya_apostada(
    tmp_path: Path,
) -> None:
    # Polymarket puede mover una pregunta a otro evento: la regla es una por `market_id`.
    service = await make_service(tmp_path)
    await service.predict_bet(make_bet(event_id="ev1", market_id="m1"), game=GAME)

    with pytest.raises(PredictBetTakenError):
        await service.predict_bet(make_bet(event_id="ev2", market_id="m1"), game=GAME)

    assert deposit(tmp_path) == STAKE
    assert_ledger_balances(tmp_path)


async def test_otra_pregunta_del_mismo_evento_otro_miembro_u_otro_servidor_si_pueden(
    tmp_path: Path,
) -> None:
    service = await make_service(tmp_path)
    await service.predict_bet(make_bet(), game=GAME)

    await service.predict_bet(make_bet(market_id="m2"), game=GAME)
    await service.predict_bet(make_bet(user=OTHER), game=GAME)
    await service.predict_bet(make_bet(guild=OTHER_GUILD), game=GAME)

    assert len(await service.open_predict_bets()) == 4
    assert deposit(tmp_path) == 3 * STAKE
    assert wallet(tmp_path, PREDICT_ACCOUNT_ID, OTHER_GUILD) == STAKE
    assert_ledger_balances(tmp_path)


async def test_apostar_sin_saldo_no_mueve_nada(tmp_path: Path) -> None:
    service = await make_service(tmp_path)

    with pytest.raises(InsufficientFundsError):
        await service.predict_bet(make_bet(stake=STARTING_BALANCE + 1), game=GAME)

    assert await service.balance(GUILD, USER) == STARTING_BALANCE
    assert deposit(tmp_path) == 0
    assert await service.open_predict_bets() == []
    assert casino_days(tmp_path) == []
    assert_ledger_balances(tmp_path)


async def test_se_puede_apostar_todo_el_saldo(tmp_path: Path) -> None:
    service = await make_service(tmp_path)

    _, balance = await service.predict_bet(make_bet(stake=STARTING_BALANCE), game=GAME)

    assert balance == 0
    assert deposit(tmp_path) == STARTING_BALANCE


@pytest.mark.parametrize(
    ("stake", "odds", "side"),
    [
        (0, ODDS, 0),
        (-5, ODDS, 0),
        (STAKE, 100, 0),
        (STAKE, 0, 0),
        (STAKE, 50, 0),
        (STAKE, ODDS, 2),
        (STAKE, ODDS, -1),
    ],
)
async def test_una_prediccion_invalida_se_rechaza_sin_cobrar(
    tmp_path: Path, stake: int, odds: int, side: int
) -> None:
    service = await make_service(tmp_path)

    with pytest.raises(ValueError):
        await service.predict_bet(make_bet(stake=stake, odds=odds, side=side), game=GAME)

    assert await service.balance(GUILD, USER) == STARTING_BALANCE
    assert deposit(tmp_path) == 0
    assert await service.open_predict_bets() == []
    assert_ledger_balances(tmp_path)


# -- Ganar y perder ---------------------------------------------------------------------


async def test_ganar_paga_la_cuota_y_el_dia_ve_la_ganancia_neta(tmp_path: Path) -> None:
    clock = FakeClock()
    service = await make_service(tmp_path, clock)
    saved, _ = await service.predict_bet(make_bet(), game=GAME)
    prize = payout(STAKE, ODDS)
    # La ganancia neta cabe en la exención, aunque el premio entero no: así se ve que el
    # IRPF se calcula con premio - apuesta y no con el premio.
    assert prize - STAKE < GAMBLING_EXEMPT

    settled = await service.settle_predict_bet(GUILD, saved.id, payout=prize, game=GAME)

    assert settled is not None
    bet, result = settled
    assert (bet.status, bet.payout, bet.settled_at) == ("won", prize, clock.now)
    assert result.day_net == prize - STAKE
    assert result.tax_delta == 0
    assert result.balance == STARTING_BALANCE + prize - STAKE
    assert wallet(tmp_path, USER) == result.balance
    assert deposit(tmp_path) == 0
    assert casino_days(tmp_path) == [(today(clock), prize - STAKE, 0)]
    assert state(tmp_path) == 0
    assert_ledger_balances(tmp_path)


async def test_el_dia_ve_la_ganancia_neta_y_no_el_premio_aunque_este_pase_de_la_exencion(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    service = await make_service(tmp_path, clock)
    await service.grant(GUILD, USER, amount=2 * GAMBLING_EXEMPT, reason="test:saldo")
    stake = GAMBLING_EXEMPT
    saved, _ = await service.predict_bet(make_bet(stake=stake, odds=150), game=GAME)
    prize = payout(stake, 150)
    assert prize > GAMBLING_EXEMPT >= prize - stake

    settled = await service.settle_predict_bet(GUILD, saved.id, payout=prize, game=GAME)

    assert settled is not None
    assert settled[1].day_net == prize - stake
    assert settled[1].tax_delta == 0
    assert casino_days(tmp_path) == [(today(clock), prize - stake, 0)]


async def test_perder_deja_el_dia_en_menos_la_apuesta_y_el_deposito_vacio(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    service = await make_service(tmp_path, clock)
    saved, _ = await service.predict_bet(make_bet(), game=GAME)

    settled = await service.settle_predict_bet(GUILD, saved.id, payout=0, game=GAME)

    assert settled is not None
    bet, result = settled
    assert (bet.status, bet.payout) == ("lost", 0)
    assert result.day_net == -STAKE
    assert result.tax_delta == 0
    assert result.balance == STARTING_BALANCE - STAKE
    assert deposit(tmp_path) == 0
    assert casino_days(tmp_path) == [(today(clock), -STAKE, 0)]
    assert state(tmp_path) == 0
    assert_ledger_balances(tmp_path)


async def test_el_libro_lleva_apuesta_y_premio_del_juego_al_resolver(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    saved, _ = await service.predict_bet(make_bet(), game=GAME)
    prize = payout(STAKE, ODDS)

    await service.settle_predict_bet(GUILD, saved.id, payout=prize, game=GAME)

    rows = query(
        tmp_path,
        "SELECT user_id, delta, reason FROM economy_ledger WHERE reason IN (?, ?) ORDER BY id",
        "predice:apuesta",
        "predice:premio",
    )
    assert rows == [(USER, -STAKE, "predice:apuesta"), (USER, prize, "predice:premio")]


async def test_el_dia_del_casino_es_el_de_cuando_se_resuelve_y_no_el_de_apostar(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    service = await make_service(tmp_path, clock)
    saved, _ = await service.predict_bet(make_bet(), game=GAME)
    bet_day = today(clock)
    clock.now += 3 * 86_400

    await service.settle_predict_bet(GUILD, saved.id, payout=0, game=GAME)

    assert casino_days(tmp_path) == [(today(clock), -STAKE, 0)]
    assert today(clock) != bet_day


# -- Resolver o anular dos veces --------------------------------------------------------


async def test_resolver_dos_veces_no_mueve_nada_la_segunda(tmp_path: Path) -> None:
    clock = FakeClock()
    service = await make_service(tmp_path, clock)
    saved, _ = await service.predict_bet(make_bet(), game=GAME)
    prize = payout(STAKE, ODDS)
    await service.settle_predict_bet(GUILD, saved.id, payout=prize, game=GAME)
    after = (wallet(tmp_path, USER), deposit(tmp_path), casino_days(tmp_path))
    ledger_rows = query(tmp_path, "SELECT COUNT(*) FROM economy_ledger")

    again = await service.settle_predict_bet(GUILD, saved.id, payout=prize, game=GAME)
    as_lost = await service.settle_predict_bet(GUILD, saved.id, payout=0, game=GAME)

    assert again is None
    assert as_lost is None
    assert (wallet(tmp_path, USER), deposit(tmp_path), casino_days(tmp_path)) == after
    assert query(tmp_path, "SELECT COUNT(*) FROM economy_ledger") == ledger_rows


async def test_anular_dos_veces_no_mueve_nada_la_segunda(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    saved, _ = await service.predict_bet(make_bet(), game=GAME)
    await service.void_predict_bet(GUILD, saved.id, game=GAME)
    ledger_rows = query(tmp_path, "SELECT COUNT(*) FROM economy_ledger")

    again = await service.void_predict_bet(GUILD, saved.id, game=GAME)

    assert again is None
    assert wallet(tmp_path, USER) == STARTING_BALANCE
    assert deposit(tmp_path) == 0
    assert query(tmp_path, "SELECT COUNT(*) FROM economy_ledger") == ledger_rows


async def test_una_prediccion_resuelta_no_se_puede_anular_ni_una_anulada_resolver(
    tmp_path: Path,
) -> None:
    service = await make_service(tmp_path)
    won, _ = await service.predict_bet(make_bet(market_id="a"), game=GAME)
    voided, _ = await service.predict_bet(make_bet(market_id="b"), game=GAME)
    await service.settle_predict_bet(GUILD, won.id, payout=payout(STAKE, ODDS), game=GAME)
    await service.void_predict_bet(GUILD, voided.id, game=GAME)
    before = (wallet(tmp_path, USER), deposit(tmp_path), casino_days(tmp_path))

    assert await service.void_predict_bet(GUILD, won.id, game=GAME) is None
    assert await service.settle_predict_bet(GUILD, voided.id, payout=500, game=GAME) is None

    assert (wallet(tmp_path, USER), deposit(tmp_path), casino_days(tmp_path)) == before
    assert_ledger_balances(tmp_path)


async def test_resolver_una_prediccion_de_otro_servidor_o_inexistente_no_hace_nada(
    tmp_path: Path,
) -> None:
    service = await make_service(tmp_path)
    saved, _ = await service.predict_bet(make_bet(), game=GAME)

    assert await service.settle_predict_bet(OTHER_GUILD, saved.id, payout=0, game=GAME) is None
    assert await service.void_predict_bet(OTHER_GUILD, saved.id, game=GAME) is None
    assert await service.settle_predict_bet(GUILD, saved.id + 99, payout=0, game=GAME) is None
    assert await service.void_predict_bet(GUILD, saved.id + 99, game=GAME) is None

    assert deposit(tmp_path) == STAKE
    assert len(await service.open_predict_bets()) == 1


async def test_un_premio_negativo_se_rechaza_sin_mover_nada(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    saved, _ = await service.predict_bet(make_bet(), game=GAME)

    with pytest.raises(ValueError):
        await service.settle_predict_bet(GUILD, saved.id, payout=-1, game=GAME)

    assert deposit(tmp_path) == STAKE
    assert len(await service.open_predict_bets()) == 1


# -- Anular -----------------------------------------------------------------------------


async def test_anular_devuelve_lo_apostado_y_no_toca_el_casino(tmp_path: Path) -> None:
    clock = FakeClock()
    service = await make_service(tmp_path, clock)
    saved, _ = await service.predict_bet(make_bet(), game=GAME)

    voided = await service.void_predict_bet(GUILD, saved.id, game=GAME)

    assert voided is not None
    bet, balance = voided
    assert (bet.status, bet.payout, bet.settled_at) == ("void", STAKE, clock.now)
    assert balance == STARTING_BALANCE
    assert wallet(tmp_path, USER) == STARTING_BALANCE
    assert deposit(tmp_path) == 0
    assert casino_days(tmp_path) == []
    assert state(tmp_path) == 0
    assert (
        query(tmp_path, "SELECT COUNT(*) FROM economy_gambling_days WHERE guild_id = ?", GUILD)[0][
            0
        ]
        == 0
    )
    assert_ledger_balances(tmp_path)


async def test_anular_deja_el_motivo_devolucion_en_el_libro(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    saved, _ = await service.predict_bet(make_bet(), game=GAME)

    await service.void_predict_bet(GUILD, saved.id, game=GAME)

    rows = query(
        tmp_path,
        "SELECT user_id, delta, reason FROM economy_ledger "
        "WHERE reason LIKE 'predice:%' ORDER BY id",
    )
    assert rows == [
        (USER, -STAKE, "predice:deposito"),
        (PREDICT_ACCOUNT_ID, STAKE, "predice:deposito"),
        (PREDICT_ACCOUNT_ID, -STAKE, "predice:deposito"),
        (USER, STAKE, "predice:devolucion"),
    ]


async def test_anular_no_cuenta_para_el_irpf_aunque_ese_dia_se_haya_ganado(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    service = await rich_service(tmp_path, clock)
    big, _ = await service.predict_bet(make_bet(market_id="grande", stake=BIG), game=GAME)
    await service.settle_predict_bet(GUILD, big.id, payout=payout(BIG, 400), game=GAME)
    open_bet, _ = await service.predict_bet(make_bet(market_id="aplazado"), game=GAME)
    days_before = casino_days(tmp_path)
    state_before = state(tmp_path)

    await service.void_predict_bet(GUILD, open_bet.id, game=GAME)

    assert casino_days(tmp_path) == days_before
    assert state(tmp_path) == state_before
    assert_ledger_balances(tmp_path)


# -- El libro y el Estado ---------------------------------------------------------------


async def test_el_libro_cuadra_con_cada_monedero_tras_ganar_perder_y_anular(
    tmp_path: Path,
) -> None:
    service = await rich_service(tmp_path)
    won, _ = await service.predict_bet(make_bet(market_id="a", stake=BIG), game=GAME)
    lost, _ = await service.predict_bet(make_bet(market_id="b", user=OTHER, stake=BIG), game=GAME)
    voided, _ = await service.predict_bet(make_bet(market_id="c", stake=STAKE), game=GAME)
    still_open, _ = await service.predict_bet(make_bet(market_id="d", user=OTHER), game=GAME)
    other_guild, _ = await service.predict_bet(
        make_bet(guild=OTHER_GUILD, market_id="a"), game=GAME
    )
    assert_ledger_balances(tmp_path)

    await service.settle_predict_bet(GUILD, won.id, payout=payout(BIG, 300), game=GAME)
    await service.settle_predict_bet(GUILD, lost.id, payout=0, game=GAME)
    await service.void_predict_bet(GUILD, voided.id, game=GAME)

    assert_ledger_balances(tmp_path)
    assert deposit(tmp_path) == still_open.stake
    assert wallet(tmp_path, PREDICT_ACCOUNT_ID, OTHER_GUILD) == other_guild.stake


async def test_el_estado_recibe_exactamente_lo_retenido_en_una_ganada_grande(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    service = await rich_service(tmp_path, clock)
    saved, _ = await service.predict_bet(make_bet(stake=BIG), game=GAME)
    prize = payout(BIG, 400)
    net = prize - BIG
    assert net > GAMBLING_EXEMPT
    balance_before = wallet(tmp_path, USER)  # con lo apostado ya en el depósito

    settled = await service.settle_predict_bet(GUILD, saved.id, payout=prize, game=GAME)

    assert settled is not None
    result = settled[1]
    assert result.day_net == net
    assert result.tax_delta == gambling_day_tax(net) > 0
    assert result.day_withheld == result.tax_delta
    assert state(tmp_path) == result.tax_delta
    assert wallet(tmp_path, USER) == balance_before + prize - result.tax_delta
    assert casino_days(tmp_path) == [(today(clock), net, result.tax_delta)]
    treasury = await service.treasury(GUILD, since=0)
    assert treasury.balance == treasury.collected_total == result.tax_delta
    assert_ledger_balances(tmp_path)


async def test_el_estado_solo_recibe_en_el_ajuste_y_no_al_apostar(tmp_path: Path) -> None:
    service = await rich_service(tmp_path)

    await service.predict_bet(make_bet(stake=BIG), game=GAME)

    assert state(tmp_path) == 0


async def test_ganar_grande_y_perder_otra_prediccion_el_mismo_dia_devuelve_desde_el_estado(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    service = await rich_service(tmp_path, clock)
    win, _ = await service.predict_bet(make_bet(market_id="gana", stake=BIG), game=GAME)
    lose, _ = await service.predict_bet(make_bet(market_id="pierde", stake=3 * BIG), game=GAME)
    won = await service.settle_predict_bet(GUILD, win.id, payout=payout(BIG, 400), game=GAME)
    assert won is not None
    withheld = won[1].tax_delta
    assert withheld > 0
    assert state(tmp_path) == withheld

    lost = await service.settle_predict_bet(GUILD, lose.id, payout=0, game=GAME)

    assert lost is not None
    result = lost[1]
    net = payout(BIG, 400) - BIG - 3 * BIG
    assert result.day_net == net
    assert result.tax_delta < 0
    assert (
        result.day_withheld == withheld + result.tax_delta == max(0, gambling_day_tax(max(net, 0)))
    )
    # Lo devuelto sale de la cuenta del Estado, que se queda con lo que sigue retenido.
    assert state(tmp_path) == result.day_withheld
    refunds = query(
        tmp_path,
        "SELECT user_id, delta FROM economy_ledger WHERE reason = ? ORDER BY id",
        "devolucion:irpf:juego",
    )
    assert refunds == [(STATE_ACCOUNT_ID, result.tax_delta), (USER, -result.tax_delta)]
    assert casino_days(tmp_path) == [(today(clock), net, result.day_withheld)]
    assert_ledger_balances(tmp_path)


async def test_la_devolucion_nunca_pasa_de_lo_que_hay_en_la_cuenta_del_estado(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    service = await rich_service(tmp_path, clock)
    win, _ = await service.predict_bet(make_bet(market_id="gana", stake=BIG), game=GAME)
    lose, _ = await service.predict_bet(make_bet(market_id="pierde", stake=5 * BIG), game=GAME)
    await service.settle_predict_bet(GUILD, win.id, payout=payout(BIG, 400), game=GAME)
    # El Estado gasta lo recaudado antes de la devolución.
    await service.repository.apply(
        GUILD, STATE_ACCOUNT_ID, [LedgerEntry(-state(tmp_path), "test:gasto")]
    )

    settled = await service.settle_predict_bet(GUILD, lose.id, payout=0, game=GAME)

    assert settled is not None
    assert settled[1].tax_delta == 0
    assert state(tmp_path) == 0
    assert_ledger_balances(tmp_path)


async def test_dos_predicciones_ganadas_el_mismo_dia_retienen_lo_mismo_que_una_sola(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    service = await rich_service(tmp_path, clock)
    first, _ = await service.predict_bet(make_bet(market_id="a", stake=BIG), game=GAME)
    second, _ = await service.predict_bet(make_bet(market_id="b", stake=BIG), game=GAME)
    prize = payout(BIG, 400)

    one = await service.settle_predict_bet(GUILD, first.id, payout=prize, game=GAME)
    two = await service.settle_predict_bet(GUILD, second.id, payout=prize, game=GAME)

    assert one is not None
    assert two is not None
    total_net = 2 * (prize - BIG)
    assert two[1].day_net == total_net
    assert two[1].day_withheld == gambling_day_tax(total_net)
    assert state(tmp_path) == gambling_day_tax(total_net)
    assert_ledger_balances(tmp_path)


# -- Listados ---------------------------------------------------------------------------


async def test_las_predicciones_de_un_miembro_salen_las_abiertas_primero(tmp_path: Path) -> None:
    clock = FakeClock()
    service = await make_service(tmp_path, clock)
    old, _ = await service.predict_bet(make_bet(market_id="vieja"), game=GAME)
    recent, _ = await service.predict_bet(make_bet(market_id="reciente"), game=GAME)
    clock.now += 1_000
    await service.settle_predict_bet(GUILD, old.id, payout=0, game=GAME)
    clock.now += 1_000
    await service.settle_predict_bet(GUILD, recent.id, payout=500, game=GAME)
    await service.predict_bet(make_bet(market_id="pronto", created_at=CREATED + 100), game=GAME)
    await service.predict_bet(make_bet(market_id="tarde", created_at=CREATED + 900), game=GAME)
    await service.predict_bet(make_bet(market_id="ajena", user=OTHER), game=GAME)

    bets = await service.predict_bets(GUILD, USER, limit=10)

    # Abiertas primero (la apostada más tarde arriba) y luego las resueltas, la última antes.
    assert [b.market_id for b in bets] == ["tarde", "pronto", "reciente", "vieja"]
    assert [b.status for b in bets] == ["open", "open", "won", "lost"]


async def test_una_resuelta_se_ordena_por_cuando_se_resolvio_y_no_por_cuando_se_aposto(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    service = await make_service(tmp_path, clock)
    first, _ = await service.predict_bet(make_bet(market_id="primera"), game=GAME)
    second, _ = await service.predict_bet(
        make_bet(market_id="segunda", created_at=CREATED + 50), game=GAME
    )
    clock.now += 1_000
    await service.settle_predict_bet(GUILD, second.id, payout=0, game=GAME)
    clock.now += 1_000
    await service.settle_predict_bet(GUILD, first.id, payout=0, game=GAME)

    bets = await service.predict_bets(GUILD, USER, limit=10)

    assert [b.market_id for b in bets] == ["primera", "segunda"]


async def test_las_predicciones_de_un_miembro_respetan_el_limite(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    for i in range(5):
        await service.predict_bet(
            make_bet(event_id=f"e{i}", market_id=f"m{i}", stake=MIN_STAKE), game=GAME
        )

    assert len(await service.predict_bets(GUILD, USER, limit=3)) == 3
    assert await service.predict_bets(GUILD, OTHER, limit=3) == []
    assert await service.predict_bets(OTHER_GUILD, USER, limit=3) == []


async def test_las_predicciones_abiertas_son_de_todos_los_servidores_y_por_fin_de_mercado(
    tmp_path: Path,
) -> None:
    service = await make_service(tmp_path)
    late, _ = await service.predict_bet(make_bet(market_id="tarde", ends=ENDS + 500), game=GAME)
    early, _ = await service.predict_bet(
        make_bet(guild=OTHER_GUILD, market_id="pronto", ends=ENDS - 500), game=GAME
    )
    done, _ = await service.predict_bet(make_bet(market_id="hecha"), game=GAME)
    await service.settle_predict_bet(GUILD, done.id, payout=0, game=GAME)

    open_bets = await service.open_predict_bets()

    assert [(b.guild_id, b.market_id) for b in open_bets] == [
        (OTHER_GUILD, "pronto"),
        (GUILD, "tarde"),
    ]
    assert {b.id for b in open_bets} == {early.id, late.id}


async def test_con_el_mismo_fin_las_abiertas_salen_por_id(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    first, _ = await service.predict_bet(make_bet(market_id="a"), game=GAME)
    second, _ = await service.predict_bet(make_bet(market_id="b"), game=GAME)
    third, _ = await service.predict_bet(make_bet(market_id="c", user=OTHER), game=GAME)

    open_bets = await service.open_predict_bets()

    assert [b.id for b in open_bets] == [first.id, second.id, third.id]


async def test_las_predicciones_de_un_evento_salen_por_id_y_solo_las_de_ese_miembro(
    tmp_path: Path,
) -> None:
    service = await make_service(tmp_path)
    first, _ = await service.predict_bet(make_bet(market_id="a"), game=GAME)
    await service.predict_bet(make_bet(event_id="ev2", market_id="x"), game=GAME)
    await service.predict_bet(make_bet(market_id="ajena", user=OTHER), game=GAME)
    await service.predict_bet(make_bet(guild=OTHER_GUILD, market_id="otro"), game=GAME)
    second, _ = await service.predict_bet(make_bet(market_id="b", side=1), game=GAME)
    await service.settle_predict_bet(GUILD, first.id, payout=0, game=GAME)

    bets = await service.predict_event_bets(GUILD, USER, "ev1")

    # También las ya resueltas: el tope y la regla de una por pregunta cuentan todas.
    assert [(b.id, b.market_id, b.status) for b in bets] == [
        (first.id, "a", "lost"),
        (second.id, "b", "open"),
    ]
    assert await service.predict_event_bets(GUILD, USER, "no-existe") == []


# -- Borrar el servidor -----------------------------------------------------------------


async def test_borrar_los_datos_del_servidor_borra_sus_predicciones_y_deja_las_de_otro(
    tmp_path: Path,
) -> None:
    service = await make_service(tmp_path)
    await service.predict_bet(make_bet(), game=GAME)
    kept, _ = await service.predict_bet(make_bet(guild=OTHER_GUILD), game=GAME)

    await service.delete_guild_data(GUILD)

    assert [b.id for b in await service.open_predict_bets()] == [kept.id]
    assert await service.predict_bets(GUILD, USER, limit=10) == []
    assert await service.predict_event_bets(GUILD, USER, "ev1") == []
    assert wallet(tmp_path, PREDICT_ACCOUNT_ID) == 0
    assert wallet(tmp_path, PREDICT_ACCOUNT_ID, OTHER_GUILD) == kept.stake


# -- Tope por evento --------------------------------------------------------------------


async def test_el_tope_suma_todas_las_preguntas_del_evento(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    cap = 3 * STAKE
    await service.predict_bet(make_bet(stake=2 * STAKE), game=GAME, cap=cap)
    before = (wallet(tmp_path, USER), deposit(tmp_path))

    with pytest.raises(PredictStakeCapError) as raised:
        await service.predict_bet(make_bet(market_id="m2", stake=2 * STAKE), game=GAME, cap=cap)

    assert raised.value.room == STAKE
    assert (wallet(tmp_path, USER), deposit(tmp_path)) == before
    assert len(await service.open_predict_bets()) == 1
    await service.predict_bet(make_bet(market_id="m2", stake=STAKE), game=GAME, cap=cap)
    assert deposit(tmp_path) == cap
    assert_ledger_balances(tmp_path)


async def test_el_tope_cuenta_tambien_las_predicciones_ya_resueltas_del_evento(
    tmp_path: Path,
) -> None:
    service = await make_service(tmp_path)
    cap = 3 * STAKE
    saved, _ = await service.predict_bet(make_bet(stake=2 * STAKE), game=GAME, cap=cap)
    await service.settle_predict_bet(GUILD, saved.id, payout=0, game=GAME)

    with pytest.raises(PredictStakeCapError) as raised:
        await service.predict_bet(make_bet(market_id="m2", stake=2 * STAKE), game=GAME, cap=cap)

    assert raised.value.room == STAKE


async def test_el_tope_es_de_cada_evento_y_de_cada_miembro(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    await service.predict_bet(make_bet(stake=STAKE), game=GAME, cap=STAKE)

    await service.predict_bet(
        make_bet(event_id="ev2", market_id="m2", stake=STAKE), game=GAME, cap=STAKE
    )
    await service.predict_bet(make_bet(user=OTHER, stake=STAKE), game=GAME, cap=STAKE)
    await service.predict_bet(make_bet(guild=OTHER_GUILD, stake=STAKE), game=GAME, cap=STAKE)

    assert len(await service.open_predict_bets()) == 4


async def test_una_prediccion_justo_en_el_tope_se_acepta_y_la_siguiente_no_deja_hueco(
    tmp_path: Path,
) -> None:
    service = await make_service(tmp_path)
    cap = 2 * STAKE

    await service.predict_bet(make_bet(stake=cap), game=GAME, cap=cap)

    assert deposit(tmp_path) == cap
    with pytest.raises(PredictStakeCapError) as raised:
        await service.predict_bet(make_bet(market_id="m2", stake=MIN_STAKE), game=GAME, cap=cap)
    assert raised.value.room == 0
    assert deposit(tmp_path) == cap


async def test_una_prediccion_que_pasa_el_tope_de_golpe_dice_cuanto_cabe_y_no_cobra(
    tmp_path: Path,
) -> None:
    service = await make_service(tmp_path)

    with pytest.raises(PredictStakeCapError) as raised:
        await service.predict_bet(make_bet(stake=STAKE + 1), game=GAME, cap=STAKE)

    assert raised.value.room == STAKE
    assert deposit(tmp_path) == 0
    assert await service.balance(GUILD, USER) == STARTING_BALANCE


async def test_sin_tope_no_hay_limite_de_evento(tmp_path: Path) -> None:
    service = await rich_service(tmp_path)

    await service.predict_bet(make_bet(stake=MAX_STAKE), game=GAME)
    await service.predict_bet(make_bet(market_id="m2", stake=MAX_STAKE), game=GAME)

    assert deposit(tmp_path) == 2 * MAX_STAKE
    assert_ledger_balances(tmp_path)


async def test_la_pregunta_repetida_se_rechaza_antes_que_el_tope(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    await service.predict_bet(make_bet(stake=STAKE), game=GAME, cap=STAKE)

    with pytest.raises(PredictBetTakenError):
        await service.predict_bet(make_bet(stake=STAKE), game=GAME, cap=STAKE)
