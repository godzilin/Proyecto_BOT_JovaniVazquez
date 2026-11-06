"""Pruebas de bot.services.pachinko: tableros, bolsillos, reserva, sorteo y retorno exacto."""

from __future__ import annotations

import itertools
import random
from collections.abc import Iterable
from fractions import Fraction

import pytest

from bot.services.pachinko import (
    BALLS,
    BOARDS,
    CLASSIC,
    DEFAULT_BOARD,
    MAX_HOLD,
    MAX_JACKPOTS,
    ONI,
    SAKURA,
    Ball,
    Board,
    Draw,
    Kind,
    PachinkoMachine,
    atari_value,
    atari_volley_chance,
    build_volley,
    draw_lottery,
    expected_return,
    find_board,
    hold_bar,
    jackpots_per_atari,
    lottery_return,
    merge_volleys,
    payout,
    paytable_lines,
    pocket_probability,
    pocket_return,
)
from bot.services.pachinko_physics import TRAJECTORIES_PER_POCKET, library

ALL_BOARDS = list(BOARDS.values())


def ball_in(board: Board, pocket: int) -> Ball:
    """Bola que va a la derecha `pocket` veces y acaba en ese bolsillo."""
    return Ball((1,) * pocket + (0,) * (board.rows - pocket))


def scripted(values: Iterable[int]):  # noqa: ANN201
    """`randbelow` que devuelve los valores dados, en orden."""
    iterator = iter(values)

    def randbelow(n: int) -> int:
        value = next(iterator)
        assert 0 <= value < n
        return value

    return randbelow


MISS = Draw((1, 2, 3), Kind.MISS, False, 0)


# -- Tableros -----------------------------------------------------------------------


@pytest.mark.parametrize("board", ALL_BOARDS, ids=lambda b: b.key)
def test_cada_tablero_es_simetrico_y_start_esta_en_el_centro(board: Board) -> None:
    assert board.rows % 2 == 0
    assert len(board.pockets) == board.rows + 1
    assert board.pockets == tuple(reversed(board.pockets))
    assert board.pockets[board.start_pocket] == 0
    assert board.pocket_label(board.start_pocket) == "START"


@pytest.mark.parametrize("board", ALL_BOARDS, ids=lambda b: b.key)
def test_las_probabilidades_de_los_bolsillos_suman_uno(board: Board) -> None:
    assert sum(pocket_probability(board, k) for k in range(board.rows + 1)) == 1


@pytest.mark.parametrize("board", ALL_BOARDS, ids=lambda b: b.key)
def test_cada_tablero_devuelve_lo_de_un_casino_de_verdad(board: Board) -> None:
    """Entre el 93 y el 96 %: como la ruleta americana (94,7 %)."""
    assert Fraction(93, 100) < expected_return(board) < Fraction(96, 100)


def test_todos_los_tableros_devuelven_casi_lo_mismo() -> None:
    """Cambiar de tablero cambia el riesgo, no la ventaja de la casa."""
    returns = [expected_return(b) for b in ALL_BOARDS]
    assert max(returns) - min(returns) < Fraction(1, 100)


def test_los_tableros_van_de_menos_a_mas_riesgo() -> None:
    """Con más riesgo, menos sale de los bolsillos y el atari paga más y llega menos."""
    pockets = [pocket_return(b) for b in ALL_BOARDS]
    values = [atari_value(b) for b in ALL_BOARDS]
    chances = [atari_volley_chance(b) for b in ALL_BOARDS]
    assert pockets == sorted(pockets, reverse=True)
    assert values == sorted(values)
    assert chances == sorted(chances, reverse=True)


def test_las_cifras_de_la_documentacion_son_las_reales() -> None:
    expected = {
        # tablero: (retorno %, bolsillos %, un atari cada N tandas, atari medio ×)
        "sakura": (95.2, 66.4, 7, 1.8),
        "clasica": (94.6, 57.6, 17, 6.3),
        "dragon": (94.4, 26.4, 22, 14.4),
        "oni": (94.3, 17.2, 39, 29.6),
    }
    for key, (total, pockets, every, value) in expected.items():
        board = BOARDS[key]
        assert round(float(expected_return(board)) * 100, 1) == total
        assert round(float(pocket_return(board)) * 100, 1) == pockets
        assert round(1 / float(atari_volley_chance(board))) == every
        assert round(float(atari_value(board)), 1) == value
    assert round(float(lottery_return(CLASSIC)), 3) == 0.370
    assert round(float(jackpots_per_atari(CLASSIC)), 1) == 2.1


#: Tandas simuladas por tablero. Los tableros tranquilos llegan con menos.
SIMULATED_VOLLEYS = {"sakura": 16_000, "clasica": 16_000, "dragon": 30_000, "oni": 30_000}


@pytest.mark.lenta
@pytest.mark.parametrize("board", ALL_BOARDS, ids=lambda b: b.key)
def test_la_simulacion_se_acerca_al_retorno_exacto(board: Board) -> None:
    """La máquina de verdad sigue las mismas reglas que las cuentas.

    Los tableros arriesgados varían mucho, así que la tolerancia crece con el
    valor de un atari. Para que corra rápido, el azar sale de `random()` en vez
    de `randrange` (unas tres veces más barato; el sesgo es de 2^-53) y las
    tandas dependen del tablero: con 40 semillas distintas, el error más grande
    llega al 77 % de la tolerancia en sakura y clasica (16.000 tandas) y al 89 %
    en dragon y oni (30.000).
    """
    rng = random.Random(1234)
    unit = rng.random

    def randbelow(n: int) -> int:
        return int(unit() * n)

    machine = PachinkoMachine(randbelow, randbelow)
    volleys = SIMULATED_VOLLEYS[board.key]
    total = sum(machine.launch(board).total_balls for _ in range(volleys))
    tolerance = 0.02 + float(atari_value(board)) / 250
    assert total / (volleys * BALLS) == pytest.approx(float(expected_return(board)), abs=tolerance)


def test_buscar_tablero_sin_tildes_ni_mayusculas() -> None:
    assert find_board("Dragón") is BOARDS["dragon"]
    assert find_board("ONI") is ONI
    assert find_board("clasico") is CLASSIC
    assert find_board("500") is None
    assert DEFAULT_BOARD == CLASSIC.key


def test_el_tope_de_racha_es_el_del_super_rush_de_oni() -> None:
    assert MAX_JACKPOTS == ONI.super_rush[2] + 1 == 25


def test_la_maquina_puede_elegir_cualquier_tablero() -> None:
    picks = iter(range(len(BOARDS)))
    machine = PachinkoMachine(lambda n: next(picks))
    assert [machine.random_board() for _ in BOARDS] == ALL_BOARDS


# -- Bolas y tanda ------------------------------------------------------------------


@pytest.mark.parametrize("board", ALL_BOARDS, ids=lambda b: b.key)
def test_la_maquina_lanza_diez_bolas_con_un_rebote_por_fila(board: Board) -> None:
    volley = PachinkoMachine().launch(board)
    assert volley.board is board
    assert len(volley.balls) == BALLS
    assert all(len(ball.path) == board.rows for ball in volley.balls)
    assert len(volley.draws) == min(volley.starts, MAX_HOLD)


@pytest.mark.parametrize("board", ALL_BOARDS, ids=lambda b: b.key)
def test_la_maquina_elige_la_caida_con_su_propio_azar(board: Board) -> None:
    """La caída que se ve sale de `pick_trajectory`; el azar del juego no la toca."""
    asked: list[int] = []

    def pick(n: int) -> int:
        asked.append(n)
        return 7

    game = scripted([1] * board.rows)  # solo alcanza para el camino de una bola
    ball = PachinkoMachine(game, pick).ball(board)
    assert ball.path == (1,) * board.rows
    assert ball.trajectory == 7
    assert asked == [TRAJECTORIES_PER_POCKET]
    with pytest.raises(StopIteration):
        game(2)  # el sorteo del juego no gastó ni una tirada de más


@pytest.mark.parametrize("board", ALL_BOARDS, ids=lambda b: b.key)
def test_la_caida_de_cada_bola_existe_en_la_biblioteca_de_su_bolsillo(board: Board) -> None:
    volley = PachinkoMachine().launch(board)
    for ball in volley.balls:
        assert 0 <= ball.trajectory < TRAJECTORIES_PER_POCKET
        assert library(board)[ball.pocket][ball.trajectory].pocket == ball.pocket


def test_la_caida_elegida_no_cambia_lo_que_paga_la_tanda() -> None:
    rng = random.Random(8)
    paths = [tuple(rng.randrange(2) for _ in range(CLASSIC.rows)) for _ in range(BALLS)]
    totals = set()
    for index in (0, 5, TRAJECTORIES_PER_POCKET - 1):
        balls = [Ball(path, index) for path in paths]
        volley = build_volley(CLASSIC, balls, lambda: MISS)
        totals.add((volley.total_balls, volley.starts, tuple(volley.pocket_counts())))
    assert len(totals) == 1


def test_una_bola_de_otro_tablero_se_rechaza() -> None:
    with pytest.raises(ValueError):
        build_volley(ONI, [ball_in(SAKURA, 0)] * BALLS, lambda: MISS)


def test_el_bolsillo_es_el_numero_de_rebotes_a_la_derecha() -> None:
    assert ball_in(CLASSIC, 0).pocket == 0
    assert Ball((0, 1) * 5).pocket == 5


def test_la_reserva_guarda_cuatro_y_las_demas_se_pierden() -> None:
    start = CLASSIC.start_pocket
    balls = [ball_in(CLASSIC, start)] * 6 + [ball_in(CLASSIC, 0)] * 4
    volley = build_volley(CLASSIC, balls, lambda: MISS)
    assert len(volley.draws) == MAX_HOLD
    assert volley.wasted == 2
    assert volley.starts == 6
    assert volley.corners == 4
    assert volley.pocket_balls == 4 * CLASSIC.pockets[0]


def test_el_mismo_bolsillo_paga_distinto_en_cada_tablero() -> None:
    sakura = build_volley(SAKURA, [ball_in(SAKURA, 0)] * BALLS, lambda: MISS)
    oni = build_volley(ONI, [ball_in(ONI, 0)] * BALLS, lambda: MISS)
    assert sakura.pocket_balls == BALLS * SAKURA.pockets[0]
    assert oni.pocket_balls == BALLS * ONI.pockets[0]


def test_el_pago_se_redondea_una_sola_vez() -> None:
    balls = [ball_in(CLASSIC, 1)] + [ball_in(CLASSIC, 3)] * 9
    volley = build_volley(CLASSIC, balls, lambda: MISS)
    assert volley.total_balls == 3
    assert payout(volley, 105) == 31  # 105 × 3 / 10 = 31,5


def test_los_premios_gordos_pagan_las_bolas_de_su_tablero() -> None:
    rush = Draw((5, 5, 5), Kind.RUSH, True, 3)
    for board in ALL_BOARDS:
        balls = [ball_in(board, board.start_pocket)] + [ball_in(board, 3)] * 9
        volley = build_volley(board, balls, lambda: rush)
        assert volley.fever_balls == 3 * board.fever_balls
        assert volley.best is rush


# -- Sorteo -------------------------------------------------------------------------


def test_atari_par_paga_un_premio() -> None:
    # Toca (0 < 1), número 4 (3 + 1).
    assert draw_lottery(CLASSIC, scripted([0, 3])) == Draw((4, 4, 4), Kind.ATARI, True, 1)


def test_rush_encadena_mientras_sale_cara_y_respeta_el_tope() -> None:
    # Toca, número 5, sigue dos veces y para.
    draw = draw_lottery(CLASSIC, scripted([0, 4, 0, 0, 4]))
    assert (draw.kind, draw.jackpots) == (Kind.RUSH, 3)
    for board in ALL_BOARDS:
        capped = draw_lottery(board, scripted([0, 4] + [0] * 50))
        assert capped.jackpots == board.rush[2] + 1


def test_el_siete_es_el_super_rush() -> None:
    for board in ALL_BOARDS:
        draw = draw_lottery(board, scripted([0, 6] + [0] * 50))
        assert draw.kind == Kind.SUPER
        assert draw.digits == (7, 7, 7)
        assert draw.jackpots == board.super_rush[2] + 1


def test_reach_falso_para_justo_al_lado() -> None:
    # No toca (39), lados 9, reach (0 < 1), centro hacia arriba.
    draw = draw_lottery(CLASSIC, scripted([39, 8, 0, 1]))
    assert draw.reach and not draw.atari
    assert draw.digits == (9, 1, 9)


def test_un_fallo_sin_reach_nunca_parece_reach() -> None:
    for left, offset in itertools.product(range(9), range(8)):
        draw = draw_lottery(CLASSIC, scripted([39, left, 5, offset, 0]))
        assert draw.digits[0] != draw.digits[2]
        assert not draw.reach


# -- Presentación -------------------------------------------------------------------


def test_la_reserva_usa_formas() -> None:
    assert hold_bar(3) == "●●●○"
    assert hold_bar(9) == "●●●●"


@pytest.mark.parametrize("board", ALL_BOARDS, ids=lambda b: b.key)
def test_la_tabla_de_premios_explica_su_tablero(board: Board) -> None:
    text = "\n".join(paytable_lines(board))
    assert board.name in text and board.risk in text
    assert "ATARI" in text and "RUSH" in text and "REACH" in text and "START" in text
    assert f"+{board.fever_balls} bolas" in text


def test_juntar_tandas_suma_bolas_tiradas_y_perdidas() -> None:
    machine = PachinkoMachine(random.Random(5).randrange, random.Random(6).randrange)
    volleys = [machine.launch(CLASSIC) for _ in range(5)]
    together = merge_volleys(volleys)
    assert len(together.balls) == 5 * BALLS
    assert together.draws == tuple(d for v in volleys for d in v.draws)
    assert together.wasted == sum(v.wasted for v in volleys)
    assert together.total_balls == sum(v.total_balls for v in volleys)


def test_no_se_juntan_tandas_de_tableros_distintos_ni_ninguna() -> None:
    machine = PachinkoMachine(random.Random(5).randrange, random.Random(6).randrange)
    with pytest.raises(ValueError):
        merge_volleys([machine.launch(CLASSIC), machine.launch(ONI)])
    with pytest.raises(ValueError):
        merge_volleys([])
