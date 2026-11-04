"""Pruebas del dinero de las combinadas deportivas: depósito, tope, resolución y anulación.

Una combinada se cobra como una sencilla: lo apostado espera en el depósito
(`SPORTS_ACCOUNT_ID`) sin tocar el día del casino, y al resolverse apuesta y premio entran
a la vez en el día de hoy con su retención. Lo apostado cuenta entero en el tope de cada
partido que toca, y las patas que se resuelven antes que la combinada se apuntan sin mover
dinero.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bot.services.economy import (
    STARTING_BALANCE,
    InsufficientFundsError,
    SportsCombo,
    SportsLeg,
    SportsStakeCapError,
)
from bot.services.sports import GAME, MAX_STAKE, combo_odds, combo_payout
from tests.unit.test_sports_economy import (
    CHANNEL,
    GUILD,
    OTHER_GUILD,
    STAKE,
    USER,
    FakeClock,
    assert_ledger_balances,
    casino_days,
    deposit,
    make_bet,
    make_service,
    query,
    rich_service,
    state,
    today,
    wallet,
)

KICKOFF = 1_800_003_600.0


def make_leg(
    event_id: str, *, odds: int = 200, pick: str = "1", commence: float = KICKOFF
) -> SportsLeg:
    return SportsLeg(
        event_id=event_id,
        sport="soccer_spain_la_liga",
        home=f"Casa {event_id}",
        away=f"Fuera {event_id}",
        commence=commence,
        market="1x2",
        pick=pick,
        odds=odds,
    )


def make_combo(
    *events: str, stake: int = STAKE, guild: int = GUILD, odds: tuple[int, ...] = ()
) -> SportsCombo:
    names = events or ("a", "b", "c")
    prices = odds or tuple(200 for _ in names)
    legs = tuple(make_leg(e, odds=o) for e, o in zip(names, prices, strict=True))
    return SportsCombo(
        id=0,
        guild_id=guild,
        user_id=USER,
        channel_id=CHANNEL,
        stake=stake,
        odds=combo_odds(leg.odds for leg in legs),
        created_at=1_800_000_000.0,
        legs=legs,
    )


# -- Apostar ----------------------------------------------------------------------------


async def test_apostar_una_combinada_llena_el_deposito_sin_tocar_el_casino(
    tmp_path: Path,
) -> None:
    service = await make_service(tmp_path)

    saved, balance = await service.sports_combo_bet(make_combo(), game=GAME, cap=MAX_STAKE)

    assert saved.id > 0
    assert saved.status == "open"
    assert saved.odds == 800
    assert [leg.event_id for leg in saved.legs] == ["a", "b", "c"]
    assert all(leg.status == "open" for leg in saved.legs)
    assert balance == STARTING_BALANCE - STAKE
    assert deposit(tmp_path) == STAKE
    assert casino_days(tmp_path) == []
    assert state(tmp_path) == 0
    assert_ledger_balances(tmp_path)


async def test_las_patas_se_guardan_y_se_leen_en_su_orden(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    await service.sports_combo_bet(make_combo("z", "a", "m"), game=GAME)

    (combo,) = await service.open_sports_combos()

    assert [leg.event_id for leg in combo.legs] == ["z", "a", "m"]
    assert (await service.sports_combos(GUILD, USER, limit=5))[0].legs == combo.legs


async def test_una_combinada_con_dos_patas_del_mismo_partido_no_se_guarda(
    tmp_path: Path,
) -> None:
    service = await make_service(tmp_path)

    with pytest.raises(ValueError):
        await service.sports_combo_bet(make_combo("a", "a"), game=GAME)

    assert deposit(tmp_path) == 0
    assert await service.open_sports_combos() == []


async def test_una_combinada_de_una_sola_pata_no_se_guarda(tmp_path: Path) -> None:
    service = await make_service(tmp_path)

    with pytest.raises(ValueError):
        await service.sports_combo_bet(make_combo("a"), game=GAME)


async def test_una_combinada_sin_saldo_no_mueve_nada(tmp_path: Path) -> None:
    service = await make_service(tmp_path)

    with pytest.raises(InsufficientFundsError):
        await service.sports_combo_bet(make_combo(stake=STARTING_BALANCE + 1), game=GAME)

    assert await service.balance(GUILD, USER) == STARTING_BALANCE
    assert deposit(tmp_path) == 0
    assert await service.open_sports_combos() == []


# -- Tope por partido -------------------------------------------------------------------


async def test_la_combinada_cuenta_en_el_tope_de_cada_partido_que_toca(tmp_path: Path) -> None:
    service = await rich_service(tmp_path)
    await service.sports_combo_bet(make_combo("a", "b", stake=MAX_STAKE - 100), game=GAME)

    with pytest.raises(SportsStakeCapError) as error:
        await service.sports_bet(make_bet(event_id="b", stake=200), game=GAME, cap=MAX_STAKE)

    assert error.value.room == 100
    await service.sports_bet(make_bet(event_id="b", stake=100), game=GAME, cap=MAX_STAKE)
    await service.sports_bet(make_bet(event_id="otro", stake=MAX_STAKE), game=GAME, cap=MAX_STAKE)


async def test_la_combinada_mira_el_partido_mas_cargado(tmp_path: Path) -> None:
    service = await rich_service(tmp_path)
    await service.sports_bet(make_bet(event_id="b", stake=MAX_STAKE - 300), game=GAME)
    before = deposit(tmp_path)

    with pytest.raises(SportsStakeCapError) as error:
        await service.sports_combo_bet(make_combo("a", "b", stake=400), game=GAME, cap=MAX_STAKE)

    assert error.value.room == 300
    assert deposit(tmp_path) == before
    await service.sports_combo_bet(make_combo("a", "b", stake=300), game=GAME, cap=MAX_STAKE)


async def test_lo_apostado_por_partido_suma_sencillas_y_combinadas_abiertas(
    tmp_path: Path,
) -> None:
    service = await rich_service(tmp_path)
    await service.sports_bet(make_bet(event_id="a", stake=50), game=GAME)
    await service.sports_combo_bet(make_combo("a", "b", stake=70), game=GAME)
    lost, _ = await service.sports_combo_bet(make_combo("b", "c", stake=1_000), game=GAME)
    await service.settle_sports_combo(GUILD, lost.id, statuses={"c": "lost"}, payout=0, game=GAME)

    staked = await service.sports_staked(GUILD, USER, ["a", "b", "c", "d"])

    # La combinada perdida ya no arriesga nada en «b»: no cuenta.
    assert staked == {"a": 120, "b": 70, "c": 0, "d": 0}


# -- Resolver ---------------------------------------------------------------------------


async def test_ganar_una_combinada_paga_y_el_dia_ve_la_ganancia_neta(tmp_path: Path) -> None:
    clock = FakeClock()
    service = await make_service(tmp_path, clock)
    saved, _ = await service.sports_combo_bet(make_combo(), game=GAME)
    prize = combo_payout(STAKE, saved.odds)
    statuses = {"a": "won", "b": "won", "c": "won"}

    settled = await service.settle_sports_combo(
        GUILD, saved.id, statuses=statuses, payout=prize, game=GAME
    )

    assert settled is not None
    combo, result = settled
    assert (combo.status, combo.payout, combo.settled_at) == ("won", prize, clock.now)
    assert [leg.status for leg in combo.legs] == ["won", "won", "won"]
    assert result.day_net == prize - STAKE
    assert wallet(tmp_path, USER) == STARTING_BALANCE - STAKE + prize
    assert deposit(tmp_path) == 0
    assert casino_days(tmp_path) == [(today(clock), prize - STAKE, 0)]
    assert_ledger_balances(tmp_path)


async def test_perder_una_combinada_deja_el_dia_en_menos_la_apuesta(tmp_path: Path) -> None:
    clock = FakeClock()
    service = await make_service(tmp_path, clock)
    saved, _ = await service.sports_combo_bet(make_combo(), game=GAME)

    settled = await service.settle_sports_combo(
        GUILD, saved.id, statuses={"a": "lost"}, payout=0, game=GAME
    )

    assert settled is not None
    combo, result = settled
    assert combo.status == "lost"
    # Solo la pata fallada se apunta: las demás no se han jugado aún.
    assert [leg.status for leg in combo.legs] == ["lost", "open", "open"]
    assert result.day_net == -STAKE
    assert deposit(tmp_path) == 0
    assert casino_days(tmp_path) == [(today(clock), -STAKE, 0)]
    assert_ledger_balances(tmp_path)


async def test_apuntar_patas_no_mueve_dinero(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    saved, balance = await service.sports_combo_bet(make_combo(), game=GAME)

    marked = await service.mark_sports_legs(GUILD, saved.id, {"a": "won", "b": "void"})

    assert marked is not None
    assert marked.status == "open"
    assert [leg.status for leg in marked.legs] == ["won", "void", "open"]
    assert wallet(tmp_path, USER) == balance
    assert deposit(tmp_path) == STAKE
    assert casino_days(tmp_path) == []
    assert_ledger_balances(tmp_path)


async def test_resolver_dos_veces_no_mueve_nada_la_segunda(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    saved, _ = await service.sports_combo_bet(make_combo(), game=GAME)
    first = await service.settle_sports_combo(
        GUILD, saved.id, statuses={"a": "lost"}, payout=0, game=GAME
    )
    assert first is not None
    before = wallet(tmp_path, USER)

    again = await service.settle_sports_combo(
        GUILD, saved.id, statuses={}, payout=10 * STAKE, game=GAME
    )

    assert again is None
    assert await service.void_sports_combo(GUILD, saved.id, statuses={}, game=GAME) is None
    assert await service.mark_sports_legs(GUILD, saved.id, {"b": "won"}) is None
    assert wallet(tmp_path, USER) == before
    assert_ledger_balances(tmp_path)


async def test_anular_una_combinada_devuelve_lo_apostado_sin_tocar_el_casino(
    tmp_path: Path,
) -> None:
    service = await make_service(tmp_path)
    saved, _ = await service.sports_combo_bet(make_combo("a", "b"), game=GAME)

    voided = await service.void_sports_combo(
        GUILD, saved.id, statuses={"a": "void", "b": "void"}, game=GAME
    )

    assert voided is not None
    combo, balance = voided
    assert (combo.status, combo.payout) == ("void", STAKE)
    assert [leg.status for leg in combo.legs] == ["void", "void"]
    assert balance == STARTING_BALANCE
    assert deposit(tmp_path) == 0
    assert casino_days(tmp_path) == []
    assert_ledger_balances(tmp_path)


async def test_una_combinada_grande_retiene_y_el_estado_lo_recibe(tmp_path: Path) -> None:
    service = await rich_service(tmp_path)
    saved, _ = await service.sports_combo_bet(
        make_combo("a", "b", stake=MAX_STAKE, odds=(300, 300)), game=GAME
    )
    prize = combo_payout(MAX_STAKE, saved.odds)

    settled = await service.settle_sports_combo(
        GUILD, saved.id, statuses={"a": "won", "b": "won"}, payout=prize, game=GAME
    )

    assert settled is not None
    withheld = settled[1].tax_delta
    assert withheld > 0
    assert state(tmp_path) == withheld
    assert_ledger_balances(tmp_path)


async def test_las_combinadas_de_un_miembro_salen_las_abiertas_primero(tmp_path: Path) -> None:
    service = await make_service(tmp_path)
    done, _ = await service.sports_combo_bet(make_combo("a", "b"), game=GAME)
    await service.settle_sports_combo(GUILD, done.id, statuses={"a": "lost"}, payout=0, game=GAME)
    waiting, _ = await service.sports_combo_bet(make_combo("c", "d"), game=GAME)

    combos = await service.sports_combos(GUILD, USER, limit=5)

    assert [c.id for c in combos] == [waiting.id, done.id]
    assert len(await service.sports_combos(GUILD, USER, limit=1)) == 1


async def test_borrar_los_datos_del_servidor_borra_sus_combinadas_y_sus_patas(
    tmp_path: Path,
) -> None:
    service = await make_service(tmp_path)
    await service.sports_combo_bet(make_combo("a", "b"), game=GAME)
    kept, _ = await service.sports_combo_bet(make_combo("c", "d", guild=OTHER_GUILD), game=GAME)

    await service.delete_guild_data(GUILD)

    assert [c.id for c in await service.open_sports_combos()] == [kept.id]
    assert (await service.open_sports_combos())[0].legs == kept.legs
    assert await service.sports_combos(GUILD, USER, limit=5) == []
    assert query(tmp_path, "SELECT COUNT(*) FROM economy_sports_combo_legs") == [(2,)]
