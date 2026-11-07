"""Reglas de las tragaperras de la plaza del Jovaniverso (`bot.services.universo_tragas`)."""

from __future__ import annotations

import pytest

from bot.services.autoplay import AUTOPLAY_MAX, LOSS_LIMIT_MULTIPLIER, StopReason
from bot.services.slots import BONUS_FREE_SPINS, FREE_SPINS
from bot.services.universo_editor import MAX_SLOTS
from bot.services.universo_tragas import (
    IDLE_SECONDS,
    MIN_GAP_SECONDS,
    SeatError,
    Seats,
)

G, U = 1, 2


def play(seats: Seats, machine: int = 1, *, now: float, stake: int = 100, auto: bool = False,
         net: int = -100, big: bool = False, scatter: bool = False, bonus: int = 0):  # fmt: skip
    turn = seats.begin(G, U, machine, stake=stake, auto=auto, now=now)
    ended = seats.finish(
        G, U, machine, turn, now=now, net=net, big_prize=big,
        triggers_free_spins=scatter, bonus_stake=bonus,
    )  # fmt: skip
    return turn, ended


def test_maquina_que_no_existe_o_apuesta_cero_no_se_juega() -> None:
    seats = Seats()
    for machine in (0, MAX_SLOTS + 1):
        with pytest.raises(SeatError):
            seats.begin(G, U, machine, stake=100, auto=False, now=0)
    with pytest.raises(SeatError):
        seats.begin(G, U, 1, stake=0, auto=False, now=0)


def test_no_se_tira_mas_rapido_que_la_tirada_mas_corta() -> None:
    seats = Seats()
    seats.begin(G, U, 1, stake=100, auto=False, now=10)
    # Reservada: una segunda petición a la vez tampoco pasa.
    with pytest.raises(SeatError):
        seats.begin(G, U, 1, stake=100, auto=False, now=10)
    with pytest.raises(SeatError):
        seats.begin(G, U, 1, stake=100, auto=False, now=10 + MIN_GAP_SECONDS / 2)
    seats.begin(G, U, 1, stake=100, auto=False, now=10 + MIN_GAP_SECONDS)


def test_se_juega_a_todas_las_maquinas_a_la_vez() -> None:
    seats = Seats()
    for machine in range(1, MAX_SLOTS + 1):
        play(seats, machine, now=0)
    assert seats.active(G, U, now=1) == set(range(1, MAX_SLOTS + 1))
    # Otro miembro tiene las suyas.
    seats.begin(G, U + 1, 1, stake=100, auto=False, now=1)


def test_los_giros_gratis_son_de_su_maquina_y_van_a_la_menor_apuesta() -> None:
    seats = Seats()
    play(seats, 1, now=0, stake=500, scatter=True)
    assert seats.seat(G, U, 1).free_spins == FREE_SPINS
    assert seats.seat(G, U, 2).free_spins == 0
    turn, _ = play(seats, 1, now=2, stake=50, bonus=20)
    assert turn.free and turn.stake == 500
    assert seats.seat(G, U, 1).free_spins == FREE_SPINS - 1 + BONUS_FREE_SPINS
    assert seats.seat(G, U, 1).free_stake == 20


def test_el_auto_para_al_tope_de_perdidas() -> None:
    seats = Seats()
    ended = None
    for k in range(LOSS_LIMIT_MULTIPLIER):
        _, ended = play(seats, now=k * 2, auto=True, net=-100)
    assert ended is not None
    reason, auto = ended
    assert reason is StopReason.LOSS_LIMIT and auto.spins == LOSS_LIMIT_MULTIPLIER
    assert seats.seat(G, U, 1).auto is None


def test_el_auto_para_al_tope_de_tiradas_y_con_un_premio_gordo() -> None:
    seats = Seats()
    ended = None
    for k in range(AUTOPLAY_MAX):
        _, ended = play(seats, now=k * 2, auto=True, net=0)
    assert ended is not None and ended[0] is StopReason.MAX_SPINS
    _, ended = play(seats, now=999, auto=True, net=5_000, big=True)
    assert ended is not None and ended[0] is StopReason.BIG_PRIZE


def test_el_auto_mantiene_su_apuesta_y_parar_lo_quita() -> None:
    seats = Seats()
    play(seats, now=0, stake=100, auto=True)
    turn, _ = play(seats, now=2, stake=9_999, auto=True)
    assert turn.stake == 100
    stopped = seats.stop(G, U, 1)
    assert stopped is not None and stopped.spins == 2
    assert seats.stop(G, U, 1) is None


def test_una_tirada_a_mano_quita_el_auto() -> None:
    seats = Seats()
    play(seats, now=0, auto=True)
    play(seats, now=2, auto=False)
    assert seats.seat(G, U, 1).auto is None


def test_los_asientos_sin_usar_se_olvidan() -> None:
    seats = Seats()
    play(seats, 1, now=0)
    play(seats, 2, now=IDLE_SECONDS)
    seats.sweep(IDLE_SECONDS + 1)
    assert len(seats) == 1


def test_los_botes_de_loteria_son_los_de_la_loteria() -> None:
    from bot.cogs.lottery import POOL_GAMES
    from bot.services.universo_botes import LOTTERY_POTS, POTS

    assert LOTTERY_POTS == POOL_GAMES
    assert len({p.key for p in POTS}) == len(POTS)
