"""Pruebas de las reglas del blackjack (`bot.services.blackjack`).

Los zapatos se preparan a mano: `shoe("A♠", "7♥", …)` reparte en ese orden
(jugador, banca, jugador, banca y luego las cartas que se pidan).
"""

from __future__ import annotations

import pytest

from bot.services.blackjack import (
    SUITS,
    Action,
    BlackjackGame,
    Card,
    IllegalAction,
    Result,
    hand_total,
    new_shoe,
)

RANKS = {"A": 1, "J": 11, "Q": 12, "K": 13}


def card(text: str) -> Card:
    label, suit = text[:-1], text[-1]
    return Card(RANKS.get(label) or int(label), SUITS.index(suit))


def shoe(*cards: str) -> list[Card]:
    """Zapato que reparte las cartas en el orden dado."""
    return [card(c) for c in reversed(cards)]


def game(*cards: str, stake: int = 100) -> BlackjackGame:
    g = BlackjackGame(stake=stake, shoe=shoe(*cards))
    g.deal()
    return g


def finish(g: BlackjackGame) -> int:
    g.reveal_hole()
    while g.dealer_should_draw():
        g.dealer_draw()
    return g.settle()


def test_zapato_de_seis_barajas() -> None:
    cards = new_shoe()
    assert len(cards) == 312
    assert len(set(cards)) == 52


def test_total_blando_y_duro() -> None:
    assert hand_total([card("A♠"), card("6♥")]) == (17, True)
    assert hand_total([card("A♠"), card("6♥"), card("9♣")]) == (16, False)
    assert hand_total([card("A♠"), card("A♥"), card("9♣")]) == (21, True)


def test_blackjack_del_jugador_paga_3_a_2_sin_turno() -> None:
    g = game("A♠", "7♥", "K♦", "9♣")

    assert not g.player_turn
    assert finish(g) == 250
    assert g.hands[0].result is Result.BLACKJACK


def test_blackjack_con_apuesta_impar_redondea_hacia_abajo() -> None:
    g = game("A♠", "7♥", "K♦", "9♣", stake=5)

    assert finish(g) == 5 + 7


def test_banca_con_blackjack_y_un_10_a_la_vista_gana_sin_turno() -> None:
    g = game("10♠", "K♥", "Q♦", "A♣")

    assert not g.insurance_pending
    assert not g.player_turn
    assert finish(g) == 0
    assert g.hands[0].result is Result.LOSE
    assert g.net == -100


def test_banca_con_as_ofrece_seguro_antes_de_mirar() -> None:
    g = game("10♠", "A♥", "Q♦", "K♣")

    assert g.insurance_pending
    assert not g.player_turn
    assert not g.hole_revealed
    assert not g.can(Action.HIT)
    assert g.insurance_cost == 50


def test_sin_seguro_y_banca_con_blackjack_se_pierde() -> None:
    g = game("10♠", "A♥", "Q♦", "K♣")

    g.decide_insurance(False)

    assert not g.player_turn
    assert finish(g) == 0
    assert g.net == -100


def test_seguro_paga_2_a_1_si_la_banca_tiene_blackjack() -> None:
    g = game("10♠", "A♥", "Q♦", "K♣")

    g.decide_insurance(True)

    assert g.total_stake == 150
    assert finish(g) == 150
    assert g.insurance_paid
    assert g.net == 0


def test_seguro_se_pierde_si_la_banca_no_tiene_blackjack_y_se_juega() -> None:
    g = game("10♠", "A♥", "Q♦", "7♣")

    g.decide_insurance(True)
    assert g.player_turn
    g.act(Action.STAND)

    assert finish(g) == 200  # 20 contra 18: gana la mano, el seguro se va
    assert not g.insurance_paid
    assert g.net == 50


def test_con_blackjack_propio_no_hay_seguro() -> None:
    g = game("A♠", "A♥", "K♦", "7♣")

    assert not g.insurance_pending
    assert finish(g) == 250


def test_seguro_imposible_con_ficha_de_1() -> None:
    g = game("10♠", "A♥", "Q♦", "7♣", stake=1)

    assert g.insurance_pending
    assert not g.can_insure()
    with pytest.raises(IllegalAction):
        g.decide_insurance(True)
    g.decide_insurance(False)
    assert g.player_turn


def test_no_se_liquida_con_el_seguro_sin_decidir() -> None:
    g = game("10♠", "A♥", "Q♦", "K♣")
    g.reveal_hole()

    with pytest.raises(RuntimeError):
        g.settle()


def test_stand_all_con_seguro_pendiente_lo_rechaza() -> None:
    g = game("10♠", "A♥", "Q♦", "K♣")

    g.stand_all()

    assert finish(g) == 0
    assert g.insurance == 0


def test_blackjack_contra_blackjack_es_empate() -> None:
    g = game("A♠", "A♥", "Q♦", "K♣")

    assert finish(g) == 100
    assert g.hands[0].result is Result.PUSH


def test_pasarse_pierde_y_la_banca_no_roba() -> None:
    g = game("10♠", "6♥", "5♦", "10♣", "9♠")

    g.act(Action.HIT)

    assert not g.player_turn
    g.reveal_hole()
    assert not g.dealer_should_draw()
    assert g.settle() == 0
    assert g.hands[0].result is Result.BUST


def test_plantarse_y_ganar_paga_1_a_1() -> None:
    g = game("10♠", "10♥", "8♦", "7♣")

    g.act(Action.STAND)

    assert finish(g) == 200


def test_la_banca_se_planta_en_17_blando() -> None:
    g = game("10♠", "A♥", "8♦", "6♣", "5♠")

    g.decide_insurance(False)
    g.act(Action.STAND)
    g.reveal_hole()

    assert not g.dealer_should_draw()
    assert g.settle() == 200  # 18 contra 17


def test_la_banca_pide_con_16() -> None:
    g = game("10♠", "10♥", "8♦", "6♣", "K♠")

    g.act(Action.STAND)

    assert finish(g) == 200  # la banca se pasa con 26
    assert len(g.dealer) == 3


def test_empate_devuelve_la_apuesta() -> None:
    g = game("10♠", "10♥", "8♦", "8♣")
    g.act(Action.STAND)
    assert finish(g) == 100


def test_doblar_dobla_la_apuesta_y_da_una_sola_carta() -> None:
    g = game("6♠", "10♥", "5♦", "7♣", "10♠", "5♥")

    assert g.extra_stake(Action.DOUBLE) == 100
    g.act(Action.DOUBLE)

    assert g.hands[0].stake == 200
    assert len(g.hands[0].cards) == 3
    assert not g.player_turn
    assert finish(g) == 400  # 21 contra 17 + 5 = 22


def test_no_se_dobla_con_tres_cartas() -> None:
    g = game("2♠", "10♥", "3♦", "7♣", "4♠")
    g.act(Action.HIT)

    assert not g.can(Action.DOUBLE)
    with pytest.raises(IllegalAction):
        g.act(Action.DOUBLE)


def test_separar_crea_dos_manos_con_su_apuesta() -> None:
    g = game("8♠", "10♥", "8♦", "7♣", "3♠", "K♥")

    assert g.can(Action.SPLIT)
    assert g.extra_stake(Action.SPLIT) == 100
    g.act(Action.SPLIT)

    assert [len(h.cards) for h in g.hands] == [2, 2]
    assert g.total_stake == 200
    assert g.active == 0
    g.act(Action.STAND)  # 8+3 = 11
    assert g.active == 1
    g.act(Action.STAND)  # 8+K = 18
    assert finish(g) == 200  # pierde 11 contra 17, gana 18 contra 17
    assert [h.result for h in g.hands] == [Result.LOSE, Result.WIN]


def test_separar_figuras_de_distinto_rango_vale() -> None:
    g = game("K♠", "10♥", "Q♦", "7♣")
    assert g.can(Action.SPLIT)


def test_solo_se_separa_una_vez() -> None:
    g = game("8♠", "10♥", "8♦", "7♣", "8♥", "K♥")
    g.act(Action.SPLIT)
    assert not g.can(Action.SPLIT)


def test_ases_separados_reciben_una_carta_y_21_no_es_blackjack() -> None:
    g = game("A♠", "10♥", "A♦", "7♣", "K♠", "9♥")

    g.act(Action.SPLIT)

    assert not g.player_turn
    assert finish(g) == 400  # 21 y 20 contra 17, pagados 1:1
    assert g.hands[0].result is Result.WIN


def test_doblar_tras_separar() -> None:
    g = game("8♠", "10♥", "8♦", "7♣", "3♠", "K♥", "10♣")
    g.act(Action.SPLIT)

    assert g.can(Action.DOUBLE)
    g.act(Action.DOUBLE)  # 8+3+10 = 21

    assert g.hands[0].stake == 200
    assert g.active == 1


def test_stand_all_cierra_las_manos_abiertas() -> None:
    g = game("10♠", "10♥", "6♦", "7♣")
    g.stand_all()
    assert not g.player_turn
    assert finish(g) == 0  # 16 contra 17


def test_no_se_puede_liquidar_a_medias() -> None:
    g = game("10♠", "10♥", "6♦", "7♣")
    with pytest.raises(RuntimeError):
        g.settle()
