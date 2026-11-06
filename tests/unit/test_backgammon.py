"""Pruebas de bot.services.backgammon: reglas, Jovani, retos del día, ficha, grada y dinero.

Las posiciones se montan con `board`, que usa los números de punto de cada color
(las blancas, los suyos; las negras, los suyos), igual que las leería una persona. El
azar de Jovani y de las partidas simuladas sale siempre de un `random.Random(semilla)`;
cuando hace falta forzar la tirada del despiste se usa `Forced`.
"""

from __future__ import annotations

import random
import time
from datetime import date, timedelta

import pytest

from bot.services import backgammon as bg
from bot.services.backgammon import (
    BAR,
    CHALLENGE_BY_KEY,
    CHALLENGES,
    CHECKERS,
    DEFAULT_LEVEL,
    GRADA_TURNS,
    LEVEL_BY_KEY,
    LEVELS,
    MAX_CUBE,
    OFF,
    TIER_REWARDS,
    WIN_SCORE,
    Ending,
    GameEvent,
    PlayerRecord,
    Position,
    Side,
    Step,
    Turn,
    after_game,
    at_risk,
    can_double,
    daily_challenges,
    dice_of,
    duel_payouts,
    escrow,
    evaluate,
    event_for,
    format_play,
    format_step,
    game_kind,
    grada_odds,
    grada_open,
    grada_payouts,
    grada_totals,
    jovani_play,
    legal_plays,
    legal_steps,
    opening_roll,
    plan_seed,
    point_number,
    progress_for,
    remaining_dice,
    resign_kind,
    reward_for,
)
from bot.services.gallos import DUEL_CUT, POOL_CUT, Corner, PoolBet, duel_prize
from bot.services.poker_club import STREAK_MAX_PCT, streak_multiplier_pct

WHITE, BLACK = Side.WHITE, Side.BLACK
DAY = date(2026, 10, 14)


def board(
    white: dict[int, int] | None = None,
    black: dict[int, int] | None = None,
    *,
    bar: tuple[int, int] = (0, 0),
    off: tuple[int, int] = (0, 0),
) -> Position:
    """Una posición a partir de `{punto: fichas}` de cada color, con su propia numeración."""
    points = [0] * 24
    for point, n in (white or {}).items():
        points[point - 1] += n
    for point, n in (black or {}).items():
        points[24 - point] -= n
    return Position(tuple(points), bar, off)


class Forced(random.Random):
    """Azar cuyo primer `random()` es `first` (la tirada del despiste); el resto, de una semilla."""

    def __init__(self, first: float, seed: int = 1) -> None:
        super().__init__(seed)
        self.first: float | None = first

    def random(self) -> float:
        if self.first is not None:
            value, self.first = self.first, None
            return value
        return super().random()


#: Una tirada que ningún nivel considera despiste.
NO_BLUNDER = 0.999


def blocked_for_white(*white_points: int) -> dict[int, int]:
    """Dos negras en cada uno de esos puntos, dados con la numeración de las blancas."""
    return {25 - point: 2 for point in white_points}


def final_of(position: Position, side: Side, play: tuple[Step, ...]) -> Position:
    for step in play:
        position = position.apply(side, step)
    return position


def random_game_states(seed: int, limit: int = 60) -> list[tuple[Position, Side, tuple[int, int]]]:
    """Las posiciones de una partida jugada al azar: `(posición, a quién le toca, tirada)`."""
    rng = random.Random(seed)
    position = Position.initial()
    first, second = opening_roll(rng)
    side, roll = (WHITE, (first, second)) if first > second else (BLACK, (second, first))
    states = []
    for _ in range(limit):
        states.append((position, side, roll))
        position = final_of(position, side, rng.choice(legal_plays(position, side, dice_of(roll))))
        if position.winner:
            break
        side, roll = side.other, (rng.randint(1, 6), rng.randint(1, 6))
    return states


# -- La posición -------------------------------------------------------------------------


def test_la_salida_tiene_quince_fichas_de_cada_color_y_167_pips() -> None:
    start = Position.initial()
    assert sum(max(v, 0) for v in start.points) == CHECKERS
    assert sum(max(-v, 0) for v in start.points) == CHECKERS
    assert start.pip(WHITE) == start.pip(BLACK) == 167
    assert start.bar == start.off == (0, 0)
    assert start.winner is None and start.contact()


def test_la_salida_es_la_estandar_y_las_negras_son_su_espejo() -> None:
    start = Position.initial()
    assert [(i, start.count(WHITE, i)) for i in range(24) if start.count(WHITE, i)] == [
        (5, 5),
        (7, 3),
        (12, 5),
        (23, 2),
    ]
    for i in range(24):
        assert start.count(BLACK, 23 - i) == start.count(WHITE, i)
    assert start == board({24: 2, 13: 5, 8: 3, 6: 5}, {24: 2, 13: 5, 8: 3, 6: 5})


def test_la_posicion_es_hashable_e_inmutable() -> None:
    start = Position.initial()
    assert {start: 1}[Position.initial()] == 1
    moved = start.apply(WHITE, Step(12, 7, 5))
    assert moved != start and start == Position.initial()
    with pytest.raises(AttributeError):
        start.bar = (1, 0)  # type: ignore[misc]


def test_los_pips_cuentan_cada_punto_desde_su_lado_y_la_barra_vale_25() -> None:
    position = board({1: 2, 10: 1}, {1: 1, 3: 2}, bar=(1, 1))
    assert position.pip(WHITE) == 2 + 10 + 25
    assert position.pip(BLACK) == 1 + 6 + 25


def test_el_numero_de_punto_depende_de_quien_mira() -> None:
    assert point_number(0, WHITE) == 1 and point_number(23, WHITE) == 24
    assert point_number(0, BLACK) == 24 and point_number(23, BLACK) == 1


def test_los_colores_se_corresponden_con_las_esquinas_de_los_gallos() -> None:
    assert WHITE.corner is Corner.RED and BLACK.corner is Corner.BLUE
    assert WHITE.other is BLACK and BLACK.other is WHITE
    assert (WHITE.emoji, BLACK.emoji) == ("⚪", "⚫")
    assert (WHITE.label, BLACK.label) == ("blancas", "negras")


def test_gana_quien_ha_sacado_las_quince() -> None:
    assert board(off=(15, 3)).winner is WHITE
    assert board(off=(14, 15)).winner is BLACK
    assert board(off=(14, 14)).winner is None


def test_all_home_pide_todas_las_fichas_en_casa_y_ninguna_en_la_barra() -> None:
    assert board({6: 3, 1: 2}, off=(10, 0)).all_home(WHITE)
    assert not board({7: 1, 1: 2}, off=(12, 0)).all_home(WHITE)
    assert not board({1: 2}, bar=(1, 0), off=(12, 0)).all_home(WHITE)
    assert board({}, {6: 3, 1: 2}, off=(0, 10)).all_home(BLACK)
    assert not board({}, {7: 1}, off=(0, 14)).all_home(BLACK)


def test_hay_contacto_mientras_alguna_ficha_tiene_que_cruzarse() -> None:
    assert board({10: 1}, {20: 1}).contact()
    assert not board({2: 1}, {5: 1}).contact()
    assert not board({10: 1}, {9: 1}).contact()
    assert board({}, {5: 1}, bar=(1, 0)).contact()
    assert board({5: 1}, bar=(0, 1)).contact()
    assert not board({}, {}, bar=(0, 1), off=(15, 0)).contact()


# -- Mover ---------------------------------------------------------------------------------


def test_entrar_desde_la_barra_cae_en_el_punto_del_dado() -> None:
    white = board({}, {}, bar=(1, 0))
    assert legal_steps(white, WHITE, (3,)) == [Step(BAR, 21, 3)]
    black = board({}, {}, bar=(0, 1))
    assert legal_steps(black, BLACK, (3,)) == [Step(BAR, 2, 3)]
    assert final_of(white, WHITE, (Step(BAR, 21, 3),)).count(WHITE, 21) == 1
    assert final_of(white, WHITE, (Step(BAR, 21, 3),)).on_bar(WHITE) == 0


def test_con_ficha_en_la_barra_hay_que_entrar_primero() -> None:
    position = board({13: 3, 6: 2}, bar=(1, 0))
    assert {s.source for s in legal_steps(position, WHITE, (4, 2))} == {BAR}
    for play in legal_plays(position, WHITE, (4, 2)):
        assert play[0].source == BAR
    with pytest.raises(ValueError):
        position.apply(WHITE, Step(12, 8, 4))


def test_con_dos_en_la_barra_hay_que_entrar_las_dos_antes_de_mover() -> None:
    position = board({13: 3}, bar=(2, 0))
    plays = legal_plays(position, WHITE, (4, 2))
    assert len(plays) == 1 and {(s.source, s.target) for s in plays[0]} == {(BAR, 20), (BAR, 22)}
    assert all(s.source == BAR for p in legal_plays(position, WHITE, (6, 5)) for s in p)


def test_sin_poder_entrar_con_la_casa_cerrada_no_hay_jugada() -> None:
    closed = board({13: 2}, {p: 2 for p in range(1, 7)}, bar=(1, 0))
    assert legal_plays(closed, WHITE, dice_of((6, 3))) == [()]
    assert legal_steps(closed, WHITE, (6, 3)) == []
    assert format_play((), WHITE) == "—"


def test_con_un_solo_punto_abierto_solo_entra_con_ese_dado() -> None:
    # Las negras tienen hechos los puntos 1 a 5 de su casa: el 6 (casilla 18) está libre.
    position = board({13: 2}, {p: 2 for p in range(1, 6)}, bar=(1, 0))
    for play in legal_plays(position, WHITE, (6, 1)):
        assert play[0] == Step(BAR, 18, 6)
    assert legal_steps(position, WHITE, (5, 1)) == []


def test_comer_manda_la_ficha_rival_a_la_barra() -> None:
    position = board({8: 2}, {20: 1})
    step = Step(7, 4, 3, True)
    assert legal_steps(position, WHITE, (3,)) == [step]
    after = position.apply(WHITE, Step(7, 4, 3))  # el hit del paso se recalcula
    assert after.on_bar(BLACK) == 1 and after.count(BLACK, 4) == 0
    assert after.count(WHITE, 4) == 1 and after.count(WHITE, 7) == 1


def test_la_ficha_comida_de_las_negras_tambien_va_a_la_barra() -> None:
    position = board({21: 1}, {8: 1})
    assert legal_steps(position, BLACK, (4,)) == [Step(16, 20, 4, True)]
    after = position.apply(BLACK, Step(16, 20, 4))
    assert after.on_bar(WHITE) == 1 and after.count(BLACK, 20) == 1


def test_una_casilla_con_dos_fichas_rivales_esta_bloqueada() -> None:
    position = board({8: 1}, {20: 2})
    assert legal_steps(position, WHITE, (3,)) == []
    with pytest.raises(ValueError):
        position.apply(WHITE, Step(7, 4, 3))
    assert legal_plays(position, WHITE, (3,)) == [()]


def test_apply_rechaza_pasos_que_no_son_legales_por_si_mismos() -> None:
    position = board({8: 1, 6: 1})
    with pytest.raises(ValueError):
        position.apply(WHITE, Step(10, 7, 3))  # sin ficha en el origen
    with pytest.raises(ValueError):
        position.apply(WHITE, Step(7, 3, 3))  # el dado no lleva ahí
    with pytest.raises(ValueError):
        position.apply(BLACK, Step(7, 4, 3))  # no es su ficha
    with pytest.raises(ValueError):
        position.apply(WHITE, Step(5, OFF, 6))  # sacar con una ficha fuera de casa


def test_apply_no_toca_la_posicion_original() -> None:
    position = board({8: 1}, {20: 1})
    position.apply(WHITE, Step(7, 4, 3))
    assert position == board({8: 1}, {20: 1})


# -- Reglas de uso de los dados ----------------------------------------------------------


def test_si_solo_se_puede_jugar_un_dado_se_juega_el_mayor() -> None:
    # Una ficha en el 10; las negras tienen hecho el punto 3 de las blancas: tras un 4 o un 3
    # el otro dado cae allí.
    position = board({10: 1}, blocked_for_white(3))
    assert legal_plays(position, WHITE, (4, 3)) == [(Step(9, 5, 4),)]
    assert legal_steps(position, WHITE, (4, 3)) == [Step(9, 5, 4)]


def test_si_el_dado_mayor_no_se_puede_jugar_se_juega_el_menor() -> None:
    # Además está hecho el 6: el 4 ni siquiera sale, y con el 3 el 4 caería en el 3.
    position = board({10: 1}, blocked_for_white(3, 6))
    assert legal_plays(position, WHITE, (4, 3)) == [(Step(9, 6, 3),)]


def test_hay_que_usar_los_dos_dados_aunque_solo_se_pueda_en_un_orden() -> None:
    # El 3 primero cae en el 7 (bloqueado); el 2 y luego el 3 sí se puede.
    position = board({10: 1}, blocked_for_white(7))
    assert legal_plays(position, WHITE, (3, 2)) == [(Step(9, 7, 2), Step(7, 4, 3))]
    assert legal_steps(position, WHITE, (3, 2)) == [Step(9, 7, 2)]
    assert legal_steps(position, WHITE, (2, 3)) == [Step(9, 7, 2)]


def test_los_dobles_juegan_cuatro_pasos() -> None:
    plays = legal_plays(Position.initial(), WHITE, dice_of((3, 3)))
    assert dice_of((3, 3)) == (3, 3, 3, 3) and dice_of((5, 2)) == (5, 2)
    assert len(plays) > 5 and all(len(p) == 4 for p in plays)


def test_un_doble_juega_los_pasos_que_se_pueden_y_no_mas() -> None:
    # Una sola ficha en el 6: 6/4 4/2 2/off y se acabó.
    assert legal_plays(board({6: 1}, off=(14, 0)), WHITE, (2, 2, 2, 2)) == [
        (Step(5, 3, 2), Step(3, 1, 2), Step(1, OFF, 2))
    ]
    # Un doble bloqueado tras el primer paso.
    blocked = board({10: 1}, blocked_for_white(4))
    assert legal_plays(blocked, WHITE, (3, 3, 3, 3)) == [(Step(9, 6, 3),)]


def test_remaining_dice_quita_el_dado_gastado() -> None:
    assert remaining_dice((3, 1), Step(0, 0, 3)) == (1,)
    assert remaining_dice((3, 1), Step(0, 0, 1)) == (3,)
    assert remaining_dice((4, 4, 4, 4), Step(0, 0, 4)) == (4, 4, 4)
    with pytest.raises(ValueError):
        remaining_dice((3, 1), Step(0, 0, 6))


# -- Sacar fichas --------------------------------------------------------------------------


def test_se_saca_con_el_dado_exacto() -> None:
    position = board({3: 1, 2: 2}, off=(12, 0))
    assert Step(2, OFF, 3) in legal_steps(position, WHITE, (3,))
    assert Step(1, OFF, 2) in legal_steps(position, WHITE, (2,))
    after = position.apply(WHITE, Step(2, OFF, 3))
    assert after.borne_off(WHITE) == 13 and after.count(WHITE, 2) == 0


def test_con_un_dado_mayor_solo_saca_la_ficha_mas_atrasada() -> None:
    position = board({3: 1, 2: 1}, off=(13, 0))
    assert legal_steps(position, WHITE, (6,)) == [Step(2, OFF, 6)]
    # Quitada la del 3, la del 2 pasa a ser la más atrasada.
    assert legal_steps(board({2: 1}, off=(14, 0)), WHITE, (6,)) == [Step(1, OFF, 6)]


def test_con_fichas_mas_altas_no_se_puede_sacar_con_un_dado_mayor() -> None:
    position = board({5: 1, 3: 1}, off=(13, 0))
    steps = legal_steps(position, WHITE, (4,))
    assert Step(4, 0, 4) in steps  # 5/1
    assert not any(s.target == OFF for s in steps)
    with pytest.raises(ValueError):
        position.apply(WHITE, Step(2, OFF, 4))


def test_no_se_saca_con_una_ficha_fuera_de_casa() -> None:
    position = board({8: 1, 2: 1}, off=(13, 0))
    assert not any(s.target == OFF for s in legal_steps(position, WHITE, (2,)))
    assert not any(s.target == OFF for s in legal_steps(position, WHITE, (6,)))


def test_las_negras_sacan_por_el_otro_lado() -> None:
    position = board({}, {2: 1, 1: 1}, off=(0, 13))
    assert Step(22, OFF, 2) in legal_steps(position, BLACK, (2,))
    # La del punto 1 no puede sacarse con un 2 mientras quede otra más atrás (la del 2).
    assert Step(23, OFF, 2) not in legal_steps(position, BLACK, (2,))
    assert legal_steps(board({}, {1: 1}, off=(0, 14)), BLACK, (6,)) == [Step(23, OFF, 6)]


def test_sacar_la_ultima_ficha_no_gasta_el_otro_dado() -> None:
    position = board({1: 1}, off=(14, 0))
    plays = legal_plays(position, WHITE, (6, 5))
    assert plays == [(Step(0, OFF, 6),)]
    assert final_of(position, WHITE, plays[0]).winner is WHITE


# -- legal_plays y legal_steps en partidas de verdad ---------------------------------------


def brute_max(position: Position, side: Side, dice: tuple[int, ...]) -> int:
    """Cuántos dados se pueden gastar como máximo, probando todos los órdenes a mano."""
    best = 0
    for die in set(dice):
        rest = list(dice)
        rest.remove(die)
        for step in legal_steps(position, side, (die,)):
            best = max(best, 1 + brute_max(position.apply(side, step), side, tuple(rest)))
    return best


@pytest.mark.parametrize("seed", range(6))
def test_las_jugadas_no_repiten_posicion_y_gastan_el_maximo_de_dados(seed: int) -> None:
    for position, side, roll in random_game_states(seed, limit=40):
        dice = dice_of(roll)
        plays = legal_plays(position, side, dice)
        finals = [final_of(position, side, play) for play in plays]
        assert len(set(finals)) == len(finals)
        longest = brute_max(position, side, dice)
        if longest == 0:
            assert plays == [()]
        else:
            assert {len(p) for p in plays} == {longest}


@pytest.mark.parametrize("seed", range(6))
def test_legal_steps_lleva_siempre_a_una_jugada_de_legal_plays(seed: int) -> None:
    rng = random.Random(seed)
    for position, side, roll in random_game_states(seed, limit=40):
        dice = dice_of(roll)
        plays = legal_plays(position, side, dice)
        finals = {final_of(position, side, play) for play in plays}
        assert {p[0] for p in plays if p} <= set(legal_steps(position, side, dice))
        current, left, used = position, dice, 0
        while steps := legal_steps(current, side, left):
            step = rng.choice(steps)
            current = current.apply(side, step)
            left = remaining_dice(left, step)
            used += 1
        assert current in finals
        assert used == len(plays[0])


def test_legal_steps_deja_mover_primero_el_dado_pequeno_si_lleva_a_la_misma_jugada() -> None:
    # 13/10/5 y 13/8/5 acaban en la misma posición: la jugada se guarda una vez,
    # pero los dos primeros pasos tienen que poder elegirse.
    position = board({13: 1})
    assert len(legal_plays(position, WHITE, (3, 5))) == 1
    assert {s.die for s in legal_steps(position, WHITE, (3, 5))} == {3, 5}


def test_las_jugadas_de_la_salida_son_las_esperadas() -> None:
    start = Position.initial()
    assert len(legal_plays(start, WHITE, (3, 1))) == 16
    assert len(legal_plays(start, WHITE, (6, 5))) == 7
    assert all(len(p) == 2 for p in legal_plays(start, WHITE, (3, 1)))
    # Con el 6-5 no se puede jugar 6/1: el 1 de las blancas es el 24 de las negras, que tienen dos.
    assert Step(5, 0, 5) not in {s for p in legal_plays(start, WHITE, (6, 5)) for s in p}


def test_legal_plays_con_dobles_es_rapido() -> None:
    start = time.perf_counter()
    for roll in ((6, 6), (4, 4), (3, 3), (1, 1)):
        legal_plays(Position.initial(), WHITE, dice_of(roll))
    assert time.perf_counter() - start < 2.0


# -- Anotación -------------------------------------------------------------------------------


def test_format_step_usa_los_numeros_de_quien_mira() -> None:
    assert format_step(Step(12, 7, 5), WHITE) == "13/8"
    assert format_step(Step(12, 7, 5, True), WHITE) == "13/8*"
    assert format_step(Step(BAR, 21, 3), WHITE) == "bar/22"
    assert format_step(Step(5, OFF, 6), WHITE) == "6/off"
    # Las negras ven su propio tablero: la casilla 11 es su punto 13.
    assert format_step(Step(11, 16, 5), BLACK) == "13/8"
    assert format_step(Step(BAR, 2, 3), BLACK) == "bar/22"
    assert format_step(Step(23, OFF, 1), BLACK) == "1/off"


def test_format_play_separa_los_pasos_con_espacios() -> None:
    assert format_play((Step(7, 4, 3), Step(5, 4, 1)), WHITE) == "8/5 6/5"
    assert format_play((), BLACK) == "—"


def test_opening_roll_nunca_empata_y_repite_los_empates() -> None:
    class Dice(random.Random):
        def __init__(self, values: list[int]) -> None:
            super().__init__(0)
            self.values = values

        def randint(self, a: int, b: int) -> int:
            return self.values.pop(0)

    assert opening_roll(Dice([3, 3, 6, 6, 2, 5])) == (2, 5)
    for seed in range(100):
        white, black = opening_roll(random.Random(seed))
        assert white != black and 1 <= white <= 6 and 1 <= black <= 6


# -- Lo que vale la partida ------------------------------------------------------------------


def test_game_kind_sencilla_si_el_perdedor_ha_sacado_alguna() -> None:
    assert game_kind(board({}, {10: 14}, off=(15, 1)), WHITE) == 1
    assert game_kind(board({10: 14}, {}, off=(1, 15)), BLACK) == 1


def test_game_kind_gammon_si_el_perdedor_no_ha_sacado_ninguna() -> None:
    assert game_kind(board({}, {10: 15}, off=(15, 0)), WHITE) == 2
    assert game_kind(board({10: 15}, {}, off=(0, 15)), BLACK) == 2


def test_game_kind_backgammon_con_ficha_en_la_barra_o_en_la_casa_del_ganador() -> None:
    assert game_kind(board({}, {10: 14}, bar=(0, 1), off=(15, 0)), WHITE) == 3
    # La casa de las blancas son las casillas 0 a 5 (puntos 1 a 6 de las blancas).
    assert game_kind(board({}, {10: 14, 20: 1}, off=(15, 0)), WHITE) == 3
    assert game_kind(board({}, {10: 14, 19: 1}, off=(15, 0)), WHITE) == 3
    assert game_kind(board({}, {10: 14, 18: 1}, off=(15, 0)), WHITE) == 2
    # Y la de las negras, las casillas 18 a 23.
    assert game_kind(board({10: 14, 24: 1}, {}, off=(0, 15)), BLACK) == 3
    assert game_kind(board({10: 14}, {}, bar=(1, 0), off=(0, 15)), BLACK) == 3
    assert game_kind(board({10: 14, 18: 1}, {}, off=(0, 15)), BLACK) == 2


def test_resign_kind_es_uno_salvo_que_el_rival_ya_saque_y_yo_no() -> None:
    assert resign_kind(Position.initial(), WHITE) == 1
    assert resign_kind(board({10: 15}, {5: 13}, off=(0, 2)), WHITE) == 2
    assert resign_kind(board({10: 14}, {5: 13}, bar=(1, 0), off=(0, 2)), WHITE) == 3
    assert resign_kind(board({10: 14}, {5: 13}, off=(1, 2)), WHITE) == 1
    assert resign_kind(board({10: 13}, {5: 14}, off=(2, 1)), BLACK) == 1
    assert resign_kind(board({5: 13}, {10: 15}, off=(2, 0)), BLACK) == 2


# -- Jovani ----------------------------------------------------------------------------------


def test_los_niveles_son_cuatro_y_piensan_cada_vez_mas() -> None:
    assert [lv.key for lv in LEVELS] == ["facil", "normal", "dificil", "imposible"]
    assert [lv.order for lv in LEVELS] == [0, 1, 2, 3]
    assert [lv.ply for lv in LEVELS] == sorted(lv.ply for lv in LEVELS)
    assert LEVEL_BY_KEY["imposible"].ply == 2 and LEVEL_BY_KEY["imposible"].blunder == 0
    assert [lv.blunder for lv in LEVELS] == sorted((lv.blunder for lv in LEVELS), reverse=True)
    assert DEFAULT_LEVEL == "normal" and DEFAULT_LEVEL in LEVEL_BY_KEY
    assert all(len(lv.blurb) < 100 and lv.name and lv.emoji for lv in LEVELS)
    assert len({lv.name for lv in LEVELS}) == 4


@pytest.mark.lenta
@pytest.mark.parametrize("level", LEVELS, ids=lambda level: level.key)
def test_jovani_siempre_juega_una_jugada_legal(level: bg.Level) -> None:
    rng = random.Random(7)
    for position, side, roll in random_game_states(3, limit=25):
        play = jovani_play(position, side, roll, level, rng)
        assert play in legal_plays(position, side, dice_of(roll))


@pytest.mark.lenta
def test_jovani_con_la_misma_semilla_juega_lo_mismo() -> None:
    states = random_game_states(5, limit=20)
    for level in LEVELS:
        first = [jovani_play(p, s, r, level, random.Random(42)) for p, s, r in states]
        again = [jovani_play(p, s, r, level, random.Random(42)) for p, s, r in states]
        assert first == again


def test_jovani_sin_despiste_elige_la_mejor_por_evaluate() -> None:
    normal = LEVEL_BY_KEY["normal"]
    for position, side, roll in random_game_states(2, limit=20):
        plays = legal_plays(position, side, dice_of(roll))
        best = max(evaluate(final_of(position, side, p), side) for p in plays)
        chosen = jovani_play(position, side, roll, normal, Forced(NO_BLUNDER))
        assert evaluate(final_of(position, side, chosen), side) == pytest.approx(best)


def test_un_despiste_juega_cualquier_jugada_legal_aunque_no_sea_la_mejor() -> None:
    start, roll = Position.initial(), (3, 1)
    plays = legal_plays(start, WHITE, dice_of(roll))
    facil = LEVEL_BY_KEY["facil"]
    chosen = {jovani_play(start, WHITE, roll, facil, Forced(0.0, seed)) for seed in range(80)}
    assert chosen <= set(plays) and len(chosen) > 3
    sane = {jovani_play(start, WHITE, roll, facil, Forced(NO_BLUNDER, seed)) for seed in range(40)}
    assert len(sane) <= 2  # solo se rompen empates entre jugadas igual de buenas


def test_jovani_devuelve_la_jugada_vacia_si_no_puede_mover() -> None:
    closed = board({13: 2}, {p: 2 for p in range(1, 7)}, bar=(1, 0))
    for level in LEVELS:
        assert jovani_play(closed, WHITE, (6, 3), level, random.Random(1)) == ()


def test_jovani_come_una_ficha_suelta_que_se_puede_comer_sin_riesgo() -> None:
    position = board({6: 3, 13: 5, 8: 3, 24: 2}, {20: 1, 11: 14})
    play = jovani_play(position, WHITE, (3, 2), LEVEL_BY_KEY["dificil"], Forced(NO_BLUNDER))
    assert any(step.hit for step in play)


def test_la_semilla_del_turno_depende_de_la_semilla_y_de_la_sal() -> None:
    assert plan_seed(7, 2).random() == plan_seed(7, 2).random()
    assert plan_seed(7, 2).random() != plan_seed(7, 3).random()
    assert plan_seed(7, 2).random() != plan_seed(8, 2).random()


# -- La evaluación ---------------------------------------------------------------------------


def test_la_salida_es_igual_para_los_dos() -> None:
    start = Position.initial()
    assert evaluate(start, WHITE) == pytest.approx(0)
    assert evaluate(start, BLACK) == pytest.approx(0)


def test_una_partida_acabada_vale_mas_menos_win_score() -> None:
    won = board({}, {10: 15}, off=(15, 0))
    assert evaluate(won, WHITE) == WIN_SCORE and evaluate(won, BLACK) == -WIN_SCORE


def test_ir_por_delante_en_pips_es_mejor() -> None:
    ahead = board({3: 2}, {3: 2, 8: 3})
    behind = board({3: 2, 8: 3}, {3: 2})
    assert evaluate(ahead, WHITE) > 0 > evaluate(behind, WHITE)


def test_una_ficha_suelta_a_tiro_vale_menos_que_la_misma_ficha_a_salvo() -> None:
    attackers = {24: 2, 8: 3, 6: 5, 13: 5}
    # Mismos pips: una suelta en el 10 y otra en el 4, frente a dos juntas en el 7.
    exposed = board({10: 1, 4: 1, 8: 3, 6: 5, 13: 5}, attackers)
    safe = board({7: 2, 8: 3, 6: 5, 13: 5}, attackers)
    assert exposed.pip(WHITE) == safe.pip(WHITE)
    assert evaluate(safe, WHITE) > evaluate(exposed, WHITE)


def test_dejar_una_ficha_rival_en_la_barra_y_una_casa_fuerte_es_mejor() -> None:
    base = {6: 2, 5: 2, 4: 2, 13: 5, 8: 3}
    quiet = board(base, {20: 2, 12: 13})
    hit = board(base, {12: 13}, bar=(0, 2))
    assert evaluate(hit, WHITE) > evaluate(quiet, WHITE)
    # Y a las negras les va peor con la barra llena.
    assert evaluate(hit, BLACK) < evaluate(quiet, BLACK)


def test_una_carrera_pura_solo_mira_pips_y_huecos_al_sacar() -> None:
    race = board({6: 3, 5: 3, 4: 3}, {6: 3, 5: 3, 4: 3, 3: 1, 2: 2, 1: 1})
    assert not race.contact()
    assert evaluate(race, WHITE) > 0 and evaluate(race, WHITE) == pytest.approx(
        -evaluate(race, BLACK)
    )
    gaps = board({6: 2, 1: 2, 2: 1}, {6: 2, 2: 2, 3: 1}, off=(10, 10))
    solid = board({4: 2, 3: 2, 2: 1}, {6: 2, 2: 2, 3: 1}, off=(10, 10))
    assert gaps.pip(WHITE) == solid.pip(WHITE)
    assert evaluate(solid, WHITE) > evaluate(gaps, WHITE)


# -- Partidas completas entre máquinas -------------------------------------------------------


def play_game(
    white: bg.Level, black: bg.Level, seed: int
) -> tuple[Position, int, dict[Side, list[float]]]:
    """Una partida entera entre dos niveles; falla si no acaba o si pierde fichas por el camino."""
    rng = random.Random(seed)
    levels = {WHITE: white, BLACK: black}
    position = Position.initial()
    first, second = opening_roll(rng)
    side, roll = (WHITE, (first, second)) if first > second else (BLACK, (second, first))
    times: dict[Side, list[float]] = {WHITE: [], BLACK: []}
    for turn in range(1, 1001):
        start = time.perf_counter()
        play = jovani_play(position, side, roll, levels[side], rng)
        times[side].append(time.perf_counter() - start)
        position = final_of(position, side, play)
        for color in Side:
            on_board = sum(position.count(color, i) for i in range(24))
            assert on_board + position.on_bar(color) + position.borne_off(color) == CHECKERS
        if position.winner is not None:
            return position, turn, times
        side, roll = side.other, (rng.randint(1, 6), rng.randint(1, 6))
    raise AssertionError("La partida no ha acabado en mil turnos.")


@pytest.mark.lenta
@pytest.mark.parametrize("batch", range(4))
def test_imposible_gana_a_facil_en_la_mayoria_de_las_partidas(batch: int) -> None:
    """20 partidas en 4 tandas de 5 (se reparten entre procesos): gana la mayoría de cada una."""
    imposible, facil = LEVEL_BY_KEY["imposible"], LEVEL_BY_KEY["facil"]
    wins = 0
    for seed in range(batch * 5, batch * 5 + 5):
        imposible_white = seed % 2 == 0
        white, black = (imposible, facil) if imposible_white else (facil, imposible)
        position, _, _ = play_game(white, black, seed)
        winner = position.winner
        assert winner is not None and 1 <= game_kind(position, winner) <= 3
        wins += winner is (WHITE if imposible_white else BLACK)
    assert wins >= 3


def test_dificil_gana_a_facil_en_la_mayoria_de_20_partidas() -> None:
    dificil, facil = LEVEL_BY_KEY["dificil"], LEVEL_BY_KEY["facil"]
    wins = sum(
        play_game(*((dificil, facil) if s % 2 == 0 else (facil, dificil)), 100 + s)[0].winner
        is (WHITE if s % 2 == 0 else BLACK)
        for s in range(20)
    )
    assert wins >= 15


@pytest.mark.lenta
def test_jovani_imposible_tarda_menos_de_un_segundo_y_medio_por_jugada() -> None:
    imposible = LEVEL_BY_KEY["imposible"]
    _, _, times = play_game(imposible, imposible, 11)
    every = times[WHITE] + times[BLACK]
    assert len(every) > 40
    assert sum(every) / len(every) < 1.5
    assert max(every) < 4.0  # tope holgado para un PC cargado; en este suele ser < 0,5 s


# -- El registro de la partida ---------------------------------------------------------------


def build_log(rolls: list[tuple[Side, tuple[int, int]]]) -> list[Turn]:
    """Un registro jugando siempre la primera jugada legal."""
    position, log = Position.initial(), []
    for side, roll in rolls:
        play = legal_plays(position, side, dice_of(roll))[0]
        position = final_of(position, side, play)
        log.append(Turn(side, roll, play))
    return log


def summarize(log: list[Turn], side: Side, **kw: object) -> GameEvent:
    base: dict[str, object] = {"ending": Ending.WIN, "level": None, "kind": 1}
    return event_for(log, side, **{**base, **kw})  # type: ignore[arg-type]


def test_event_for_cuenta_turnos_dobles_y_la_racha_de_dobles() -> None:
    log = build_log(
        [
            (WHITE, (3, 3)),
            (BLACK, (2, 1)),
            (WHITE, (4, 4)),
            (BLACK, (6, 6)),
            (WHITE, (5, 5)),
            (BLACK, (1, 2)),
            (WHITE, (6, 3)),
            (BLACK, (4, 4)),
            (WHITE, (2, 2)),
        ]
    )
    white, black = summarize(log, WHITE), summarize(log, BLACK)
    assert (white.turns, white.doubles, white.double_run) == (5, 4, 3)
    assert (black.turns, black.doubles, black.double_run) == (4, 2, 1)
    assert white.opening and not black.opening


def test_event_for_cuenta_las_comidas_de_cada_lado() -> None:
    log = build_log([(WHITE, (3, 1)), (BLACK, (6, 5)), (WHITE, (4, 2)), (BLACK, (5, 3))] * 6)
    white, black = summarize(log, WHITE), summarize(log, BLACK)
    assert (
        white.hits == black.hit_taken == sum(s.hit for t in log if t.side is WHITE for s in t.steps)
    )
    assert (
        black.hits == white.hit_taken == sum(s.hit for t in log if t.side is BLACK for s in t.steps)
    )
    assert white.hits + black.hits > 0


def test_event_for_mide_la_mayor_desventaja_de_pips() -> None:
    # Las blancas juegan 24/13 (11 pips) y las negras van 11 por detrás.
    log = [Turn(WHITE, (6, 5), (Step(23, 17, 6), Step(17, 12, 5)))]
    assert summarize(log, BLACK).max_deficit == 11
    assert summarize(log, WHITE).max_deficit == 0
    assert summarize([], WHITE) == GameEvent(Ending.WIN, WHITE, None, 1)


def test_event_for_detecta_el_cierre_del_tablero_con_el_rival_en_la_barra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = board({p: 2 for p in range(1, 7)} | {8: 3}, {18: 1, 13: 14})
    monkeypatch.setattr(Position, "initial", classmethod(lambda cls: start))
    play = next(p for p in legal_plays(start, WHITE, (1, 2)) if p[0].hit)
    log = [Turn(WHITE, (1, 2), play), Turn(BLACK, (3, 4), ())]
    white, black = summarize(log, WHITE), summarize(log, BLACK)
    assert white.closed_out and not black.closed_out
    assert (white.hits, white.hit_taken, black.hits, black.hit_taken) == (1, 0, 0, 1)
    assert (white.turns, black.turns) == (1, 1)


def test_event_for_cuenta_las_fichas_sacadas(monkeypatch: pytest.MonkeyPatch) -> None:
    start = board({1: 1, 2: 1}, {13: 15}, off=(13, 0))
    monkeypatch.setattr(Position, "initial", classmethod(lambda cls: start))
    play = legal_plays(start, WHITE, (2, 1))[0]
    log = [Turn(WHITE, (2, 1), play)]
    assert summarize(log, WHITE).my_off == 15 and summarize(log, BLACK).my_off == 0


def test_event_for_guarda_el_resto_de_datos_tal_cual() -> None:
    log = build_log([(BLACK, (2, 1)), (WHITE, (3, 1))])
    event = event_for(
        log, BLACK, ending=Ending.LOSS, level=LEVEL_BY_KEY["dificil"], kind=2, cube=4, stake=50
    )
    assert event.opening and event.lost and not event.duel
    assert (event.ending, event.side, event.kind, event.cube, event.stake) == (
        Ending.LOSS,
        BLACK,
        2,
        4,
        50,
    )


def test_las_partidas_ganadas_y_perdidas_se_distinguen() -> None:
    def ev(ending: Ending, kind: int = 1, level: str | None = "normal") -> GameEvent:
        return GameEvent(ending, WHITE, LEVEL_BY_KEY[level] if level else None, kind)

    assert ev(Ending.WIN).won and not ev(Ending.WIN).lost
    for ending in (Ending.LOSS, Ending.RESIGN, Ending.TIMEOUT, Ending.DROP):
        assert ev(ending).lost and not ev(ending).won
    assert ev(Ending.WIN, 2).gammon and not ev(Ending.WIN, 2).backgammon
    assert ev(Ending.WIN, 3).gammon and ev(Ending.WIN, 3).backgammon
    assert not ev(Ending.WIN, 1).gammon
    assert not ev(Ending.LOSS, 3).gammon and not ev(Ending.LOSS, 3).backgammon
    assert ev(Ending.WIN, level=None).duel and not ev(Ending.WIN).duel
    assert [Ending.WIN.value, Ending.DROP.value] == ["win", "drop"]
    assert ev(Ending.WIN, level="dificil").beat_at_least(2)
    assert not ev(Ending.WIN, level="normal").beat_at_least(2)
    assert not ev(Ending.LOSS, level="imposible").beat_at_least(0)
    assert not ev(Ending.WIN, level=None).beat_at_least(0)


# -- Retos del día ---------------------------------------------------------------------------


def test_cada_dia_hay_tres_retos_uno_de_cada_dificultad() -> None:
    challenges = daily_challenges(1, 10, DAY)
    assert [c.tier for c in challenges] == [0, 1, 2]
    assert all(CHALLENGE_BY_KEY[c.key] is c for c in challenges)


def test_los_retos_son_los_mismos_todo_el_dia() -> None:
    assert daily_challenges(1, 10, DAY) == daily_challenges(1, 10, DAY)


def test_los_retos_cambian_de_un_dia_a_otro_para_casi_todos() -> None:
    users = range(100, 140)
    different = sum(
        daily_challenges(1, user, DAY) != daily_challenges(1, user, DAY + timedelta(days=1))
        for user in users
    )
    assert different >= len(users) * 0.9


def test_los_retos_de_dos_miembros_o_servidores_no_son_siempre_iguales() -> None:
    by_user = {daily_challenges(1, user, DAY) for user in range(100, 140)}
    by_guild = {daily_challenges(guild, 10, DAY) for guild in range(1, 41)}
    assert len(by_user) > 1 and len(by_guild) > 1


def test_el_catalogo_de_retos_esta_bien_formado() -> None:
    assert len({c.key for c in CHALLENGES}) == len(CHALLENGES) == len(CHALLENGE_BY_KEY)
    assert [sum(c.tier == t for c in CHALLENGES) for t in range(3)] == [5, 6, 5]
    for challenge in CHALLENGES:
        assert challenge.goal >= 1
        assert challenge.reward == TIER_REWARDS[challenge.tier]
        assert "{goal}" not in challenge.label


def test_los_premios_suben_con_la_dificultad() -> None:
    assert list(TIER_REWARDS) == sorted(TIER_REWARDS)


def result(
    ending: Ending = Ending.WIN,
    *,
    level: str | None = "normal",
    kind: int = 1,
    **kw: object,
) -> GameEvent:
    return GameEvent(
        ending=ending,
        side=WHITE,
        level=LEVEL_BY_KEY[level] if level else None,
        kind=kind,
        **kw,  # type: ignore[arg-type]
    )


# Casos de partida y lo que suman a los retos (todos los retos del catálogo).
PROGRESS_CASES = {
    "victoria sencilla contra normal comiendo y sacando dobles": (
        result(hits=6, hit_taken=3, doubles=2),
        {
            "jugar_3": 1,
            "ganar_2": 1,
            "normal_1": 1,
            "comer_5": 6,
            "dobles_3": 2,
            "serio_4": 1,
            "comer_15": 6,
        },
    ),
    "backgammon perfecto contra imposible": (
        result(level="imposible", kind=3, closed_out=True),
        {
            "jugar_3": 1,
            "ganar_2": 1,
            "normal_1": 1,
            "serio_4": 1,
            "dificil_1": 1,
            "intacto_1": 1,
            "gammon_1": 1,
            "imposible_1": 1,
            "dificil_3": 1,
            "cierre_1": 1,
            "backgammon_1": 1,
        },
    ),
    "gammon ganado en un duelo": (
        result(level=None, kind=2, hits=1, hit_taken=1),
        {
            "jugar_3": 1,
            "ganar_2": 1,
            "comer_5": 1,
            "serio_4": 1,
            "comer_15": 1,
            "duelo_1": 1,
            "gammon_1": 1,
            "duelo_gana_1": 1,
        },
    ),
    "gammon a Jovani en fácil no cuenta como serio": (
        result(level="facil", kind=2),
        {"jugar_3": 1, "ganar_2": 1},
    ),
    "derrota contra fácil habiendo cerrado el tablero": (
        result(Ending.LOSS, level="facil", closed_out=True),
        {"jugar_3": 1, "cierre_1": 1},
    ),
    "derrota en un duelo": (result(Ending.LOSS, level=None), {"jugar_3": 1, "duelo_1": 1}),
    "rendirse cuenta como partida jugada": (result(Ending.RESIGN), {"jugar_3": 1}),
}


@pytest.mark.parametrize(("game", "expected"), PROGRESS_CASES.values(), ids=list(PROGRESS_CASES))
def test_cada_partida_suma_lo_que_toca_a_cada_reto(
    game: GameEvent, expected: dict[str, int]
) -> None:
    assert progress_for(CHALLENGES, game) == expected


def test_los_casos_de_prueba_cubren_todos_los_retos() -> None:
    covered = {key for _, expected in PROGRESS_CASES.values() for key in expected}
    assert covered == set(CHALLENGE_BY_KEY)


def test_progress_for_solo_mira_los_retos_que_le_pasan() -> None:
    mine = (CHALLENGE_BY_KEY["jugar_3"], CHALLENGE_BY_KEY["imposible_1"])
    assert progress_for(mine, result()) == {"jugar_3": 1}


@pytest.mark.parametrize("streak", range(1, 15))
def test_el_premio_de_un_reto_sube_con_la_racha(streak: int) -> None:
    for challenge in CHALLENGES:
        expected = challenge.reward * (100 + streak_multiplier_pct(streak)) // 100
        assert reward_for(challenge, streak) == expected


def test_la_racha_empieza_en_el_premio_base_y_tiene_tope() -> None:
    challenge = CHALLENGE_BY_KEY["dificil_3"]
    assert reward_for(challenge, 1) == challenge.reward
    assert reward_for(challenge, 2) > challenge.reward
    assert reward_for(challenge, 1000) == challenge.reward * (100 + STREAK_MAX_PCT) // 100


# -- La ficha ----------------------------------------------------------------------------


def test_ganar_suma_racha_y_la_mejor_racha() -> None:
    record = PlayerRecord()
    for expected in (1, 2, 3):
        record = after_game(record, result(), DAY)
        assert record.streak == expected and record.best_streak == expected
    assert record.games == record.wins == 3 and record.losses == 0


@pytest.mark.parametrize("ending", [Ending.LOSS, Ending.RESIGN, Ending.TIMEOUT, Ending.DROP])
def test_perder_rendirse_dormirse_o_no_aceptar_corta_la_racha_y_cuenta_derrota(
    ending: Ending,
) -> None:
    record = PlayerRecord(games=3, wins=3, streak=3, best_streak=3)
    after = after_game(record, result(ending), DAY)
    assert (after.streak, after.best_streak) == (0, 3)
    assert (after.games, after.wins, after.losses) == (4, 3, 1)


def test_los_gammons_cuentan_tambien_los_backgammons_y_solo_si_se_gana() -> None:
    record = after_game(PlayerRecord(), result(kind=2), DAY)
    assert (record.gammons, record.backgammons) == (1, 0)
    record = after_game(record, result(kind=3), DAY)
    assert (record.gammons, record.backgammons) == (2, 1)
    record = after_game(record, result(kind=1), DAY)
    record = after_game(record, result(Ending.LOSS, kind=3), DAY)
    assert (record.gammons, record.backgammons, record.games) == (2, 1, 4)


def test_los_dias_seguidos_jugando_suben_se_quedan_o_vuelven_a_uno() -> None:
    game = result()
    first = after_game(PlayerRecord(), game, DAY)
    assert (first.day_streak, first.last_day) == (1, DAY)
    same_day = after_game(first, game, DAY)
    assert same_day.day_streak == 1
    next_day = after_game(same_day, game, DAY + timedelta(days=1))
    assert next_day.day_streak == 2
    assert after_game(next_day, game, DAY + timedelta(days=2)).day_streak == 3
    skipped = after_game(next_day, game, DAY + timedelta(days=5))
    assert skipped.day_streak == 1 and skipped.last_day == DAY + timedelta(days=5)


def test_el_mejor_nivel_solo_sube_ganando_a_jovani() -> None:
    record = PlayerRecord()
    assert record.best_level == -1
    record = after_game(record, result(Ending.LOSS, level="imposible"), DAY)
    assert record.best_level == -1
    record = after_game(record, result(level=None), DAY)
    assert record.best_level == -1
    record = after_game(record, result(level="dificil"), DAY)
    assert record.best_level == LEVEL_BY_KEY["dificil"].order
    record = after_game(record, result(level="facil"), DAY)
    assert record.best_level == LEVEL_BY_KEY["dificil"].order


# -- La grada ----------------------------------------------------------------------------


def test_la_grada_esta_abierta_hasta_que_se_han_jugado_los_turnos_de_cierre() -> None:
    assert grada_open(0)
    assert grada_open(GRADA_TURNS - 1)
    assert not grada_open(GRADA_TURNS)
    assert not grada_open(GRADA_TURNS + 10)


def test_la_grada_suma_lo_apostado_a_cada_color() -> None:
    bets = {
        1: PoolBet(WHITE.corner, 100),
        2: PoolBet(BLACK.corner, 300),
        3: PoolBet(WHITE.corner, 50),
    }
    assert grada_totals(bets) == {WHITE: 150, BLACK: 300}
    assert grada_totals({}) == {WHITE: 0, BLACK: 0}


def test_la_cuota_de_la_grada_es_none_si_nadie_apuesta_a_ese_color() -> None:
    odds = grada_odds({1: PoolBet(WHITE.corner, 100)})
    assert odds[BLACK] is None and odds[WHITE] is not None
    both = grada_odds({1: PoolBet(WHITE.corner, 100), 2: PoolBet(BLACK.corner, 100)})
    assert both[WHITE] == both[BLACK] == 2 * (1 - POOL_CUT)


def test_la_grada_no_reparte_mas_de_lo_apostado() -> None:
    bets = {
        1: PoolBet(WHITE.corner, 100),
        2: PoolBet(BLACK.corner, 333),
        3: PoolBet(WHITE.corner, 77),
        4: PoolBet(BLACK.corner, 1),
    }
    pot = sum(bet.stake for bet in bets.values())
    for winner in Side:
        payouts = grada_payouts(bets, winner)
        assert set(payouts) == set(bets)
        assert sum(payouts.values()) <= pot
        for user, bet in bets.items():
            assert (payouts[user] > 0) == (bet.corner is winner.corner)


def test_si_todos_apuestan_al_mismo_color_se_devuelve_lo_apostado() -> None:
    bets = {1: PoolBet(WHITE.corner, 100), 2: PoolBet(WHITE.corner, 40)}
    for winner in Side:
        assert grada_payouts(bets, winner) == {1: 100, 2: 40}


# -- El dinero de un duelo -------------------------------------------------------------------


def test_escrow_cubre_un_backgammon_al_valor_del_cubo() -> None:
    assert escrow(100, 1) == 300 and escrow(100, 4) == 1200 and escrow(0, 2) == 0
    for cube in (1, 2, 4, 8):
        assert escrow(50, cube) == at_risk(50, cube, 3)


def test_at_risk_es_apuesta_por_cubo_por_valor_de_la_partida() -> None:
    assert at_risk(100, 1, 1) == 100
    assert at_risk(100, 2, 2) == 400
    assert at_risk(100, 4, 3) == 1200


@pytest.mark.parametrize("cube", [1, 2, 4, 8])
@pytest.mark.parametrize("kind", [1, 2, 3])
@pytest.mark.parametrize("stake", [0, 1, 7, 100, 333])
def test_lo_devuelto_mas_la_comision_es_lo_retenido_por_los_dos(
    stake: int, kind: int, cube: int
) -> None:
    for winner in Side:
        payouts = duel_payouts(stake, cube, kind, winner)
        risked = at_risk(stake, cube, kind)
        fee = 2 * risked - duel_prize(risked)
        assert set(payouts) == set(Side)
        assert sum(payouts.values()) + fee == 2 * escrow(stake, cube)
        assert fee == pytest.approx(2 * risked * DUEL_CUT, abs=1)
        assert payouts[winner.other] == escrow(stake, cube) - risked >= 0
        assert payouts[winner] >= payouts[winner.other]


def test_el_ganador_gana_lo_del_perdedor_menos_la_comision() -> None:
    held = escrow(100, 2)
    for kind in (1, 2, 3):
        payouts = duel_payouts(100, 2, kind, WHITE)
        risked = at_risk(100, 2, kind)
        assert payouts[BLACK] - held == -risked
        assert payouts[WHITE] - held == duel_prize(risked) - risked
        assert 0 < payouts[WHITE] - held < risked


def test_can_double_pide_apuesta_cubo_por_debajo_del_tope_y_que_no_sea_del_rival() -> None:
    assert can_double(1, None, WHITE, 100)
    assert can_double(2, WHITE, WHITE, 100) and not can_double(2, BLACK, WHITE, 100)
    assert can_double(MAX_CUBE // 2, BLACK, BLACK, 50)
    assert not can_double(MAX_CUBE, WHITE, WHITE, 100)
    assert not can_double(1, None, WHITE, 0)
    assert MAX_CUBE == 8 and bg.GAME == "backgammon"


def test_doblar_retiene_de_mas_lo_que_cubre_el_cubo_nuevo() -> None:
    assert bg.double_extra(100, 1) == bg.escrow(100, 2) - bg.escrow(100, 1)
    assert bg.escrow(100, 1) + bg.double_extra(100, 1) == bg.escrow(100, 2)


@pytest.mark.parametrize("kind", [1, 2, 3])
def test_las_devoluciones_con_lo_retenido_de_cada_uno_cuadran(kind: int) -> None:
    """Quien dobló y no le aceptaron tiene más retenido: se le devuelve entero lo de más."""
    stake, cube = 100, 1
    held = {
        Side.WHITE: bg.escrow(stake, cube) + bg.double_extra(stake, cube),
        Side.BLACK: bg.escrow(stake, cube),
    }
    returns = bg.duel_returns(held, stake, cube, kind, Side.WHITE)
    risked = bg.at_risk(stake, cube, kind)
    rake = 2 * risked - duel_prize(risked)
    assert returns[Side.BLACK] == held[Side.BLACK] - risked
    assert returns[Side.WHITE] - held[Side.WHITE] == duel_prize(risked) - risked
    assert sum(returns.values()) + rake == sum(held.values())


def test_con_lo_mismo_retenido_las_devoluciones_son_las_del_duelo() -> None:
    held = dict.fromkeys(Side, bg.escrow(100, 4))
    assert bg.duel_returns(held, 100, 4, 2, Side.BLACK) == bg.duel_payouts(100, 4, 2, Side.BLACK)


def test_si_el_perdedor_no_tiene_retenido_lo_que_se_juega_falla() -> None:
    held = dict.fromkeys(Side, 100)
    with pytest.raises(ValueError):
        bg.duel_returns(held, 100, 1, 3, Side.WHITE)


def test_event_for_rehace_la_partida_desde_la_posicion_que_se_le_da() -> None:
    points = [0] * bg.POINTS
    points[0], points[20] = 1, -14
    start = Position(tuple(points), (0, 0), (14, 1))
    log = [bg.Turn(Side.WHITE, (1, 2), (bg.Step(0, bg.OFF, 2),))]
    event = bg.event_for(log, Side.WHITE, ending=bg.Ending.WIN, level=None, kind=1, start=start)
    assert event.my_off == 15 and event.turns == 1
    with pytest.raises(ValueError):
        bg.event_for(log, Side.WHITE, ending=bg.Ending.WIN, level=None, kind=1)
