"""Pruebas de bot.services.war: comparar cartas, empate, rendición, guerra y lateral."""

from __future__ import annotations

import random

import pytest

from bot.services.blackjack import DECKS, Card
from bot.services.war import (
    ACE_VALUE,
    BURN_CARDS,
    TIE_PAYS,
    WAR_TIE_BONUS,
    IllegalAction,
    Result,
    WarGame,
    rank_value,
)


def c(rank: int, suit: int = 0) -> Card:
    return Card(rank, suit)


def game(*cards: Card, stake: int = 100, side: int = 0) -> WarGame:
    """Mano con el zapato preparado: salen en ese orden (jugador, banca, quemadas…)."""
    shoe = [c(7)] * 10 + list(reversed(cards))
    war = WarGame(stake=stake, side=side, shoe=shoe)
    war.deal()
    return war


def test_el_as_es_la_carta_mas_alta_y_los_palos_no_cuentan() -> None:
    assert rank_value(c(1)) == ACE_VALUE > rank_value(c(13)) > rank_value(c(2))
    assert game(c(1, 0), c(13, 1)).result is Result.WIN
    assert game(c(5, 0), c(5, 3)).awaiting_choice


def test_gana_la_carta_mas_alta_y_paga_1_a_1() -> None:
    won = game(c(10), c(4))
    assert won.result is Result.WIN
    assert won.total_return == 200
    assert won.net == 100


def test_si_gana_la_banca_se_lleva_la_apuesta() -> None:
    lost = game(c(4), c(10))
    assert lost.result is Result.LOSE
    assert lost.total_return == 0
    assert lost.net == -100


def test_un_dos_pierde_contra_un_tres_y_un_rey_contra_un_as() -> None:
    assert game(c(2), c(3)).result is Result.LOSE
    assert game(c(13), c(1)).result is Result.LOSE


def test_empate_deja_la_mano_pendiente_y_sin_dinero_devuelto() -> None:
    tied = game(c(9), c(9, 2))
    assert tied.awaiting_choice
    assert not tied.settled
    assert tied.total_return == 0
    assert tied.extra_stake == 100


def test_rendirse_recupera_la_mitad_redondeando_hacia_abajo() -> None:
    even = game(c(9), c(9))
    even.surrender()
    assert even.result is Result.SURRENDER
    assert even.total_return == 50
    assert even.net == -50
    odd = game(c(9), c(9), stake=101)
    odd.surrender()
    assert odd.total_return == 50
    assert game(c(9), c(9), stake=1).total_return == 0


def test_la_guerra_ganada_devuelve_la_original_y_paga_1_a_1_la_de_la_guerra() -> None:
    war = game(c(9), c(9), c(2), c(3), c(4), c(12), c(5))
    war.go_to_war()
    assert war.burned == [c(2), c(3), c(4)] and len(war.burned) == BURN_CARDS
    assert war.player[1] == c(12) and war.dealer[1] == c(5)
    assert war.result is Result.WAR_WIN
    # 100 de la original + 100 de la guerra + 100 de premio.
    assert war.total_stake == 200
    assert war.total_return == 300
    assert war.net == 100


def test_la_guerra_perdida_pierde_las_dos_apuestas() -> None:
    war = game(c(9), c(9), c(2), c(3), c(4), c(5), c(12))
    war.go_to_war()
    assert war.result is Result.WAR_LOSE
    assert war.total_return == 0
    assert war.net == -200


def test_la_guerra_empatada_paga_ademas_un_premio_igual_a_la_apuesta_original() -> None:
    war = game(c(9), c(9), c(2), c(3), c(4), c(8), c(8, 1))
    war.go_to_war()
    assert war.result is Result.WAR_TIE
    assert war.won
    assert war.total_return == (3 + WAR_TIE_BONUS) * 100
    assert war.net == 100 + WAR_TIE_BONUS * 100


def test_el_ganador_de_la_guerra_con_ficha_impar_no_pierde_ni_un_centimo() -> None:
    war = game(c(9), c(9), c(2), c(3), c(4), c(12), c(5), stake=7)
    war.go_to_war()
    assert war.net == 7


def test_no_se_puede_decidir_sin_empate_ni_dos_veces() -> None:
    plain = game(c(10), c(4))
    with pytest.raises(IllegalAction):
        plain.go_to_war()
    with pytest.raises(IllegalAction):
        plain.surrender()
    tied = game(c(9), c(9))
    tied.surrender()
    with pytest.raises(IllegalAction):
        tied.go_to_war()


def test_la_lateral_de_empate_paga_10_a_1_si_las_dos_primeras_cartas_empatan() -> None:
    hit = game(c(9), c(9), stake=100, side=100)
    hit.surrender()
    assert hit.side_return == 100 * (1 + TIE_PAYS)
    assert hit.total_stake == 200
    # 50 de la rendición + 1.100 de la lateral, menos 200 puestos.
    assert hit.net == 50 + 1_100 - 200


def test_la_lateral_se_pierde_si_no_hay_empate() -> None:
    miss = game(c(10), c(4), stake=100, side=100)
    assert miss.side_return == 0
    assert miss.total_stake == 200
    assert miss.net == 100 - 100


def test_la_lateral_cobra_aunque_se_pierda_la_guerra_y_se_cuenta_la_segunda_apuesta() -> None:
    war = game(c(9), c(9), c(2), c(3), c(4), c(5), c(12), stake=100, side=100)
    war.go_to_war()
    assert war.total_stake == 300
    assert war.net == 1_100 - 300


def test_sin_lateral_no_hay_pago_de_empate() -> None:
    tied = game(c(9), c(9))
    tied.surrender()
    assert tied.side_return == 0


def test_la_apuesta_debe_ser_positiva() -> None:
    with pytest.raises(ValueError):
        WarGame(stake=0, shoe=[c(2), c(3)]).deal()


def test_el_zapato_real_tiene_seis_barajas_y_alcanza_para_una_guerra() -> None:
    real = WarGame(stake=10)
    assert len(real.shoe) == DECKS * 52
    real.deal()
    if real.awaiting_choice:
        real.go_to_war()
    assert real.settled


@pytest.mark.lenta
def test_frecuencia_de_empates_en_6_barajas_es_23_de_311() -> None:
    rng = random.Random(7)
    base = WarGame(stake=1).shoe
    hands = 20_000
    ties = 0
    for _ in range(hands):
        shoe = base[:]
        rng.shuffle(shoe)
        trial = WarGame(stake=1, shoe=shoe)
        trial.deal()
        ties += trial.tied
    assert ties / hands == pytest.approx(23 / 311, abs=0.006)
