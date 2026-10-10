"""Pruebas de bot.services.bus: las reglas puras del Autobús (`autobus`).

Las cartas se fuerzan construyendo la partida con las que tocan, nunca con
estadística. El 99 % se comprueba con cuentas exactas sobre las 52 cartas.
"""

from __future__ import annotations

import random
from fractions import Fraction

import pytest

from bot.services.blackjack import Card
from bot.services.bus import (
    CARDS,
    HANDS,
    RTP,
    BusError,
    BusGame,
    Hand,
    Pick,
    Status,
    chance,
    draw_card,
    factor,
    format_chance,
    format_multiplier,
    height,
    milestone,
    paytable,
    picks_for,
    wins,
)

ALL_CARDS = [Card(rank, suit) for rank in range(1, 14) for suit in range(4)]
# Palos de blackjack.SUITS: 0 ♠, 1 ♥, 2 ♦, 3 ♣.
SPADE, HEART, DIAMOND, CLUB = range(4)


def game(*cards: Card, stake: int = 100) -> BusGame:
    """Partida con las cartas dadas; las que falten hasta cinco son 2♠."""
    padded = list(cards) + [Card(2, SPADE)] * (CARDS - len(cards))
    return BusGame(stake=stake, cards=tuple(padded))


# -- Cartas y probabilidades ------------------------------------------------------------


def test_el_as_es_la_carta_mas_alta() -> None:
    assert height(Card(1, SPADE)) == 14
    assert height(Card(13, SPADE)) == 13
    assert height(Card(2, SPADE)) == 2


def test_draw_card_saca_cualquiera_de_las_52() -> None:
    rng = random.Random(7)
    seen = {draw_card(rng) for _ in range(5_000)}
    assert seen == set(ALL_CARDS)


def test_rojo_y_negro_son_mitad_y_mitad() -> None:
    assert chance(Pick.RED, []) == Fraction(1, 2)
    assert chance(Pick.BLACK, []) == Fraction(1, 2)


@pytest.mark.parametrize(("rank", "higher", "lower"), [(7, 7, 5), (2, 12, 0), (1, 0, 12)])
def test_mayor_y_menor_cuentan_las_alturas_estrictas(rank: int, higher: int, lower: int) -> None:
    table = [Card(rank, HEART)]
    assert chance(Pick.HIGHER, table) == Fraction(higher, 13)
    assert chance(Pick.LOWER, table) == Fraction(lower, 13)
    assert chance(Pick.EQUAL, table) == Fraction(1, 13)


def test_dentro_fuera_y_poste_reparten_las_trece_alturas() -> None:
    table = [Card(4, HEART), Card(9, CLUB)]  # dentro: 5-8; poste: 4 y 9
    assert chance(Pick.INSIDE, table) == Fraction(4, 13)
    assert chance(Pick.POST, table) == Fraction(2, 13)
    assert chance(Pick.OUTSIDE, table) == Fraction(7, 13)


def test_con_dos_cartas_iguales_no_hay_dentro_y_el_poste_es_una_altura() -> None:
    table = [Card(8, HEART), Card(8, CLUB)]
    assert chance(Pick.INSIDE, table) == 0
    assert chance(Pick.POST, table) == Fraction(1, 13)
    assert chance(Pick.OUTSIDE, table) == Fraction(12, 13)


def test_cada_mano_reparte_todas_las_cartas_entre_sus_opciones() -> None:
    """Cada carta la gana exactamente una opción: no hay huecos ni solapes."""
    tables = {
        Hand.COLOR: [],
        Hand.HEIGHT: [Card(9, SPADE)],
        Hand.RANGE: [Card(3, SPADE), Card(12, HEART)],
        Hand.SUIT: [Card(3, SPADE), Card(12, HEART), Card(5, CLUB)],
        Hand.TURN: [Card(3, SPADE)] * 4,
    }
    for hand, table in tables.items():
        for card in ALL_CARDS:
            winners = [p for p in picks_for(hand) if wins(p, card, table)]
            assert len(winners) == 1, (hand, card, winners)


def test_factor_es_la_inversa_de_la_probabilidad() -> None:
    assert factor(Pick.EQUAL, [Card(5, SPADE)]) == 13
    assert factor(Pick.SPADES, []) == 4
    with pytest.raises(ValueError):
        factor(Pick.HIGHER, [Card(1, SPADE)])


# -- Partida ----------------------------------------------------------------------------


def test_acertar_el_color_paga_1_98() -> None:
    g = game(Card(5, HEART))
    guess = g.play(Pick.RED)
    assert guess.won
    assert g.multiplier == RTP * 2
    assert g.pot == 198
    assert g.hand is Hand.HEIGHT


def test_fallar_pierde_todo_y_termina() -> None:
    g = game(Card(5, HEART), Card(3, SPADE))
    g.play(Pick.RED)
    g.play(Pick.HIGHER)
    assert g.status is Status.LOST
    assert g.payout == 0
    assert g.net == -100
    assert g.hand is None
    with pytest.raises(BusError):
        g.play(Pick.HIGHER)


def test_el_multiplicador_acumula_la_inversa_de_cada_acierto() -> None:
    g = game(Card(7, HEART), Card(10, SPADE), Card(9, CLUB), Card(4, DIAMOND))
    g.play(Pick.RED)  # ×2
    g.play(Pick.HIGHER)  # con un 7: 7 de 13 → ×13/7
    g.play(Pick.INSIDE)  # entre 7 y 10: 8 y 9 → ×13/2
    g.play(Pick.DIAMONDS)  # ×4
    assert g.multiplier == RTP * 2 * Fraction(13, 7) * Fraction(13, 2) * 4
    assert g.completed
    assert not g.turned
    assert g.hand is Hand.TURN
    assert g.playing


def test_la_vuelta_doblada_cierra_el_trayecto() -> None:
    g = game(Card(7, HEART), Card(10, SPADE), Card(9, CLUB), Card(4, DIAMOND), Card(1, CLUB))
    for pick in (Pick.RED, Pick.HIGHER, Pick.INSIDE, Pick.DIAMONDS, Pick.TURN_BLACK):
        g.play(pick)
    assert g.turned
    assert g.hand is None
    assert g.cash_out() == g.pot
    assert g.status is Status.CASHED


def test_no_se_puede_pedir_una_opcion_de_otra_mano_ni_una_imposible() -> None:
    g = game(Card(1, HEART))
    with pytest.raises(BusError):
        g.play(Pick.HIGHER)
    g.play(Pick.RED)
    with pytest.raises(BusError):
        g.play(Pick.HIGHER)  # nada es mayor que un as
    assert g.playing


def test_cobrar_pide_un_acierto_y_paga_redondeando_hacia_abajo() -> None:
    g = game(Card(7, HEART), Card(10, SPADE), stake=7)
    with pytest.raises(BusError):
        g.cash_out()
    g.play(Pick.RED)
    g.play(Pick.HIGHER)
    # 7 × 0,99 × 2 × 13/7 = 25,74 → 25
    assert g.cash_out() == 25
    assert g.net == 18


def test_options_ensenan_probabilidad_y_multiplicador_total() -> None:
    g = game(Card(1, HEART))
    first = {o.pick: o for o in g.options()}
    assert first[Pick.RED].multiplier == RTP * 2
    g.play(Pick.RED)
    second = {o.pick: o for o in g.options()}
    assert second[Pick.HIGHER].chance == 0
    assert second[Pick.HIGHER].multiplier is None
    assert second[Pick.LOWER].multiplier == RTP * 2 * Fraction(13, 12)


def test_missed_dice_que_opciones_ganaban_la_siguiente_carta() -> None:
    g = game(Card(7, HEART), Card(7, SPADE))
    g.play(Pick.RED)
    g.cash_out()
    assert g.missed() == [Pick.EQUAL]


# -- El 99 % ----------------------------------------------------------------------------


def _expected_multiplier(table: list[Card], hands_left: int, choose) -> Fraction:
    """Multiplicador esperado jugando `hands_left` manos más con la elección `choose`."""
    hand = Hand(len(table) + 1)
    pick = choose(hand, table)
    total = Fraction(0)
    for card in ALL_CARDS:
        if not wins(pick, card, table):
            continue
        rest = (
            _expected_multiplier([*table, card], hands_left - 1, choose)
            if hands_left > 1
            else Fraction(1)
        )
        total += Fraction(1, 52) * factor(pick, table) * rest
    return total


def _best(hand: Hand, table: list[Card]) -> Pick:
    return max(picks_for(hand), key=lambda p: chance(p, table))


def _longshot(hand: Hand, table: list[Card]) -> Pick:
    possible = [p for p in picks_for(hand) if chance(p, table)]
    return min(possible, key=lambda p: chance(p, table))


@pytest.mark.parametrize("choose", [_best, _longshot])
@pytest.mark.parametrize("hands", [1, 2, 3])
def test_cobrar_en_cualquier_parada_devuelve_el_99_por_ciento(choose, hands: int) -> None:
    """Sea cual sea la elección y la parada, el multiplicador medio es 0,99 exacto."""
    assert RTP * _expected_multiplier([], hands, choose) == RTP


def test_el_palo_y_la_vuelta_son_apuestas_justas() -> None:
    table = [Card(3, SPADE), Card(12, HEART), Card(5, CLUB)]
    assert _expected_multiplier(table, 1, _best) == 1
    assert _expected_multiplier([*table, Card(2, HEART)], 1, _best) == 1


# -- Textos -----------------------------------------------------------------------------


def test_format_multiplier_y_format_chance() -> None:
    assert format_multiplier(Fraction(99, 50)) == "×1,98"
    assert format_multiplier(RTP * 2 * 13 * 13 * 4 * 2) == "×2.676,96"
    assert format_chance(Fraction(6, 13)) == "46 %"
    assert format_chance(Fraction(1, 13)) == "7,7 %"
    assert format_chance(Fraction(1, 2)) == "50 %"


def test_paytable_tiene_una_fila_por_altura_y_por_distancia() -> None:
    rows = paytable()
    assert any(label == "Con un 7" for label, _ in rows)
    assert any(label == "Separadas por 0" for label, _ in rows)
    assert any("×13" in cells for _, cells in rows)


def test_milestone_celebra_el_autobus_completo_y_la_vuelta() -> None:
    g = game(Card(7, HEART), Card(10, SPADE), Card(9, CLUB), Card(4, DIAMOND), Card(1, CLUB))
    for pick in (Pick.RED, Pick.HIGHER, Pick.INSIDE, Pick.DIAMONDS):
        g.play(pick)
    assert "COMPLETO" in (milestone(g) or "")
    g.play(Pick.TURN_BLACK)
    assert "VUELTA" in (milestone(g) or "")
    assert HANDS == 4
