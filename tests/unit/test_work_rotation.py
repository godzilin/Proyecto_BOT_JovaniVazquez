"""Pruebas de la rotación de minijuegos de `pala` (P5 en adelante) y del órdago final.

Cuadrante, Negociación, Auditoría y Despacho.

La rotación está en `bot.services.work.Position.on`, los juegos en
`bot.services.work_games` (`PlanGame`, `DealGame`) y su contenido en
`bot.services.work_rotation`.
"""

from __future__ import annotations

import random

import pytest

from bot.services.achievements import (
    _audit_stats,
    _deal_stats,
    _desk_stats,
    _plan_stats,
    _work_game_stats,
)
from bot.services.work import Mechanic, Position
from bot.services.work_catalog import JOBS
from bot.services.work_games import (
    AUDIT_CELLS,
    AUDIT_HIT,
    DEAL_BREAK,
    DEAL_PROBE,
    DEAL_START,
    DEAL_STEP,
    DESK_MAX,
    DESK_PAR,
    DESK_START,
    ORDAGO_LOST,
    ORDAGO_MIN,
    ORDAGO_OFFER,
    ORDAGO_PASS,
    ORDAGO_WON,
    PLAN_CAPACITY,
    AuditGame,
    Deal,
    DealGame,
    DeskCard,
    DeskGame,
    DeskOption,
    MiniGame,
    Piece,
    PlanGame,
    deal_mood,
    new_game,
)
from bot.services.work_rotation import (
    AUDIT_PACKS,
    DEAL_PACKS,
    DEAL_TELLS,
    DESK_PACKS,
    PLAN_PACKS,
)
from bot.services.work_tools import NO_PERKS, Perks, perks_for

NOW = 1_000.0
PACKS = {
    Mechanic.PLAN: PLAN_PACKS,
    Mechanic.DEAL: DEAL_PACKS,
    Mechanic.AUDIT: AUDIT_PACKS,
    Mechanic.DESK: DESK_PACKS,
}


def rotating() -> list[Position]:
    return [p for job in JOBS for p in job.all_positions if p.alts]


def variants() -> list[Position]:
    """Cada puesto que rota, con cada uno de los minijuegos que le tocan otros días."""
    return [p.on(day) for p in rotating() for day in range(1, 1 + len(p.alts))]


def play_best(game: MiniGame) -> None:
    """Juega pulsando siempre el botón de `best()` (en el órdago, plantarse)."""
    while game.current is not None:
        game.press(game.best(), NOW + 1)  # type: ignore[attr-defined]


def plan(pieces: list[Piece], *, lanes: int = 2, retries: int = 0) -> PlanGame:
    return PlanGame(
        Mechanic.PLAN,
        "",
        [],
        60,
        NOW,
        retries=retries,
        ordago=ORDAGO_PASS,
        pack=PLAN_PACKS["quirofano"],
        lanes=tuple(range(lanes)),
        pieces=pieces,
    )


def deal(kind: str, stamina: int, *, retries: int = 0, fifty: bool = False) -> DealGame:
    tells = [DEAL_TELLS[deal_mood(kind, stamina - n)][0] for n in range(stamina + 1)]
    return DealGame(
        Mechanic.DEAL,
        "",
        [],
        60,
        NOW,
        retries=retries,
        fifty=fifty,
        ordago=ORDAGO_PASS,
        pack=DEAL_PACKS["banco"],
        deals=[Deal("la directora", "el crédito", kind, stamina, tells)],
    )


# -- Rotación -----------------------------------------------------------------------------


def test_de_p5_en_adelante_todos_los_puestos_rotan_y_antes_ninguno() -> None:
    for job in JOBS:
        for position in job.all_positions:
            assert bool(position.alts) == (position.level >= 5), position.title


def test_cada_puesto_que_rota_tiene_su_contenido() -> None:
    for position in rotating():
        for mechanic, content in position.alts:
            assert content in PACKS[mechanic], (position.title, content)


def test_el_minijuego_alterna_un_dia_cada_uno() -> None:
    for position in rotating():
        cycle = 1 + len(position.alts)
        days = [position.on(day).mechanic for day in range(100, 100 + cycle)]
        assert sorted(days) == sorted([position.mechanic, *(m for m, _ in position.alts)])
        assert position.on(100 + cycle).mechanic is days[0]


def test_los_de_detectar_y_los_jefes_rotan_entre_tres_minijuegos() -> None:
    for position in rotating():
        extra = [mechanic for mechanic, _content in position.alts[1:]]
        if position.mechanic is Mechanic.SPOT:
            assert extra == [Mechanic.AUDIT], position.title
        elif position.mechanic is Mechanic.DIALOGUE and position.level >= 9:
            assert extra == [Mechanic.DESK], position.title
        else:
            assert extra == [], position.title


def test_el_puesto_del_dia_conserva_sueldo_tareas_y_el_juego_de_siempre() -> None:
    position = rotating()[0]
    day = next(d for d in range(2) if position.on(d).rotated)
    rotated = position.on(day)
    assert (rotated.title, rotated.base_pay, rotated.tasks) == (
        position.title,
        position.base_pay,
        position.tasks,
    )
    assert rotated.task_mechanic is position.mechanic
    assert rotated.on(day + 1) is rotated
    assert position.task_mechanic is position.mechanic


def test_un_puesto_sin_rotacion_es_siempre_el_mismo() -> None:
    position = JOBS[0].positions[0]
    assert all(position.on(day) is position for day in range(4))


def test_los_botones_de_los_juegos_que_rotan_caben_en_discord() -> None:
    for position in variants():
        game = new_game(position, random.Random(3), now=NOW)
        while (current := game.current) is not None:
            assert 1 <= len(current.options) <= 20, position.title
            assert all(0 < len(label) <= 80 for label in current.options), position.title
            assert len(game.header) + len(current.prompt) < 3_000, position.title
            moves = game.moves() if isinstance(game, AuditGame) and not game.ordago else [0]
            game.press(moves[0], NOW + 1)


def test_jugar_lo_mejor_saca_100_en_todos_los_puestos_que_rotan() -> None:
    for position in variants():
        for guard in (False, True):
            game = new_game(position, random.Random(5), now=NOW, guard=guard)
            play_best(game)
            assert game.completed, position.title
            # En el Despacho no hay jugada perfecta: basta con que no se hunda.
            if isinstance(game, DeskGame):
                assert not game.crisis and game.score() >= 85, position.title
            else:
                assert game.score() == 100, position.title


# -- Cuadrante ----------------------------------------------------------------------------


def test_el_cuadrante_solo_ofrece_los_carriles_donde_cabe_la_pieza() -> None:
    game = plan([Piece("a", 4), Piece("b", 3), Piece("c", 2)])
    game.press(0, NOW)
    current = game.current
    assert current is not None
    # La de 3 no cabe con la de 4 (caben 6): solo el segundo carril y dejarla fuera.
    assert current.options == ["Q2", PLAN_PACKS["quirofano"].skip]


def test_una_pieza_con_condicion_no_va_donde_no_puede() -> None:
    game = plan([Piece("a", 1, ban=0), Piece("b", 1, only=0)])
    assert game.current is not None and game.current.options[0] == "Q2"
    game.press(0, NOW)
    assert game.current is not None and game.current.options[:-1] == ["Q1"]


def test_dejar_una_pieza_fuera_resta_y_encajarlo_todo_es_un_100() -> None:
    pieces = [Piece("a", 4), Piece("b", 2), Piece("c", 3), Piece("d", 3)]
    full = plan(list(pieces))
    play_best(full)
    assert (full.score(), full.perfect_rounds, full.correct) == (100, 1, 4)

    lazy = plan(list(pieces))
    assert lazy.current is not None
    lazy.press(len(lazy.current.options) - 1, NOW)  # la de 4, fuera
    play_best(lazy)
    # 8 de 12 encajadas y 4 fuera que restan: (8 - 4) / 12.
    assert lazy.points == 4
    assert lazy.perfect_rounds == 0
    assert lazy.misses == 1


def test_el_fallo_gratis_deshace_la_ultima_jugada_una_vez() -> None:
    game = plan([Piece("a", 4), Piece("b", 2)], retries=1)
    assert game.current is not None and "↩️" not in game.current.options[-1]
    game.press(0, NOW)
    current = game.current
    assert current is not None and current.options[-1].startswith("↩️")
    game.press(len(current.options) - 1, NOW)
    assert (game.placed, game.points, game.correct, game.undone) == ([], 0, 0, 1)
    game.press(0, NOW)
    assert game.current is not None and "↩️" not in game.current.options[-1]


def test_cada_jugada_del_cuadrante_cambia_el_indice_para_descartar_clics_viejos() -> None:
    game = plan([Piece("a", 1), Piece("b", 1), Piece("c", 1)], retries=1)
    seen = [game.index]
    game.press(0, NOW)
    seen.append(game.index)
    assert game.current is not None
    game.press(len(game.current.options) - 1, NOW)  # deshacer
    seen.append(game.index)
    assert len(set(seen)) == 3


def test_la_guardia_dobla_el_sitio_y_las_piezas_del_cuadrante() -> None:
    position = Position(6, "x", 0, Mechanic.PLAN, 60, (), 0, "quirofano")
    normal = new_game(position, random.Random(1), now=NOW)
    guard = new_game(position, random.Random(1), now=NOW, guard=True)
    assert isinstance(normal, PlanGame) and isinstance(guard, PlanGame)
    assert guard.capacity == 2 * normal.capacity == 2 * PLAN_CAPACITY
    assert guard.max_points == 2 * normal.max_points
    assert guard.seconds == 2 * normal.seconds


def test_el_cuadrante_fuera_de_tiempo_no_cuenta() -> None:
    game = plan([Piece("a", 1), Piece("b", 1)])
    assert not game.press(0, NOW + 1_000)
    assert game.finished(NOW + 1_000) and game.placed == []


# -- Negociación --------------------------------------------------------------------------


def test_cada_caracter_ensena_su_aguante_a_su_manera() -> None:
    assert [deal_mood("libro", n) for n in (3, 2, 1, 0)] == ["calm", "calm", "tense", "limit"]
    # El farolero aparenta un apretón menos de los que aguanta.
    assert [deal_mood("farol", n) for n in (3, 2, 1, 0)] == ["calm", "tense", "limit", "limit"]
    assert {deal_mood("poker", n) for n in range(5)} == {"poker"}


def test_apretar_hasta_el_limite_y_cerrar_saca_el_maximo() -> None:
    game = deal("libro", 2)
    assert game.press(0, NOW) and game.press(0, NOW)
    assert game.offer == DEAL_START + 2 * DEAL_STEP
    assert game.press(1, NOW)
    assert (game.got, game.correct, game.perfect_rounds) == ([game.deals[0].best], 2, 1)
    assert game.completed and game.score() == 100


def test_apretar_de_mas_lo_levanta_de_la_mesa() -> None:
    game = deal("libro", 1)
    game.press(0, NOW)
    assert not game.press(0, NOW)
    assert (game.got, game.walked, game.misses) == ([DEAL_BREAK], 1, 1)
    assert game.completed


def test_el_fallo_gratis_lo_sienta_otra_vez() -> None:
    game = deal("libro", 1, retries=1)
    game.press(0, NOW)
    assert not game.press(0, NOW)
    assert (game.walked, game.saved, game.turn) == (0, 1, 0)
    game.press(1, NOW)
    assert game.got == [game.deals[0].best]


def test_cerrar_pronto_cobra_menos_y_dice_cuanto_aguantaba() -> None:
    game = deal("libro", 3)
    game.press(0, NOW)
    assert not game.press(1, NOW)
    assert game.got == [DEAL_START + DEAL_STEP]
    assert "Aguantaba 2 más" in game.last
    assert game.correct == 0


def test_tantear_dice_el_aguante_y_cuesta_un_poco_del_trato() -> None:
    game = deal("poker", 2)
    assert game.current is not None and len(game.current.options) == 3
    game.press(2, NOW)
    current = game.current
    assert current is not None and len(current.options) == 2
    assert "aguanta **2** más" in current.prompt
    game.press(0, NOW)
    game.press(0, NOW)
    game.press(1, NOW)
    assert game.got == [game.deals[0].best - DEAL_PROBE]
    # Aun pagando el tanteo, cerrar en el límite cuenta como bien cerrado.
    assert (game.correct, game.perfect_rounds, game.peeked) == (2, 0, [True])


def test_con_la_chuleta_tantear_es_gratis() -> None:
    game = deal("poker", 2, fifty=True)
    game.press(2, NOW)
    game.press(0, NOW)
    game.press(0, NOW)
    game.press(1, NOW)
    assert game.got == [game.deals[0].best]


def test_la_chuleta_y_el_fallo_gratis_sirven_en_la_negociacion() -> None:
    owned = {"argumentario", "pinganillo"}
    assert perks_for("politica", Mechanic.DEAL, owned) == Perks(
        tools=perks_for("politica", Mechanic.DEAL, owned).tools, retries=1, fifty=True
    )
    assert perks_for("politica", Mechanic.PLAN, {"argumentario"}) == NO_PERKS


def test_los_tratos_del_turno_dependen_del_puesto() -> None:
    low = new_game(
        Position(5, "x", 0, Mechanic.DEAL, 60, (), 0, "banco"), random.Random(1), now=NOW
    )
    top = new_game(
        Position(10, "x", 0, Mechanic.DEAL, 60, (), 0, "banco"), random.Random(1), now=NOW
    )
    assert isinstance(low, DealGame) and isinstance(top, DealGame)
    assert sorted(d.kind for d in low.deals) == ["farol", "libro", "libro"]
    assert sorted(d.kind for d in top.deals) == ["farol", "poker", "poker"]
    assert all(d.stamina >= 2 for d in top.deals if d.kind == "poker")


# -- Logros -------------------------------------------------------------------------------


def test_un_cuadrante_completo_cuenta_para_sus_logros() -> None:
    game = plan([Piece("a", 4), Piece("b", 2), Piece("c", 3), Piece("d", 3)])
    play_best(game)
    add: dict[str, int] = {}
    _plan_stats(game, add)
    assert add == {
        "work_plan_shifts": 1,
        "work_plan_pieces": 4,
        "work_plan_full": 1,
        "work_plan_fast": 1,
    }


def test_dejar_fuera_la_ultima_o_todas_las_piezas_tiene_su_logro() -> None:
    last = plan([Piece("a", 1), Piece("b", 1)])
    last.press(0, NOW)
    last.press(2, NOW)  # los dos carriles y, el tercero, dejarla fuera
    add: dict[str, int] = {}
    _plan_stats(last, add)
    assert add["work_plan_last_out"] == 1 and "work_plan_full" not in add

    none = plan([Piece("a", 1), Piece("b", 1), Piece("c", 1)])
    while (current := none.current) is not None:
        none.press(len(current.options) - 1, NOW)
    add = {}
    _plan_stats(none, add)
    assert (add["work_plan_none"], add["work_plan_out3"]) == (1, 1)
    assert "work_plan_pieces" not in add and "work_plan_last_out" not in add


def test_los_tratos_cuentan_para_sus_logros() -> None:
    poker = deal("poker", 4)
    for _ in range(4):
        poker.press(0, NOW)
    poker.press(1, NOW)
    add: dict[str, int] = {}
    _deal_stats(poker, add)
    assert add == {
        "work_deal_shifts": 1,
        "work_deal_best": 1,
        "work_deal_hundred": 1,
        "work_deal_poker_blind": 1,
        "work_deal_clean": 1,
    }

    timid = deal("libro", 2)
    timid.press(1, NOW)
    add = {}
    _deal_stats(timid, add)
    assert add == {"work_deal_shifts": 1, "work_deal_timid": 1}

    greedy = deal("libro", 1)
    greedy.press(2, NOW)
    greedy.press(0, NOW)
    greedy.press(0, NOW)
    add = {}
    _deal_stats(greedy, add)
    assert add == {
        "work_deal_shifts": 1,
        "work_deal_probes": 1,
        "work_deal_walked": 1,
        "work_deal_all_walked": 1,
    }


# -- Auditoría ----------------------------------------------------------------------------


def audit(*, bad: set[int] | None = None, retries: int = 0, boards: int = 1) -> AuditGame:
    game = AuditGame(
        Mechanic.AUDIT,
        "",
        [],
        60,
        NOW,
        retries=retries,
        ordago=ORDAGO_PASS,
        pack=AUDIT_PACKS["sobrecostes"],
        boards=boards,
        bad_count=len(bad) if bad else 4,
        seed=7,
    )
    if bad:
        game.bad = set(bad)
    return game


def test_la_primera_casilla_de_una_auditoria_nunca_falla() -> None:
    for first in range(AUDIT_CELLS):
        game = audit()
        assert game.press(first, NOW)
        # Sin malas al lado: abre también a sus vecinas.
        assert game.blown == 0 and game.points > 1


def test_una_casilla_abierta_dice_cuantas_malas_tiene_al_lado() -> None:
    # Malas en las esquinas de arriba: la casilla 1 toca una y la 2, ninguna.
    game = audit(bad={0, 4, 19})
    game.press(1, NOW)
    current = game.current
    assert current is not None and current.options[1] == "1️⃣"
    assert game.opened == {1}
    game.press(12, NOW)
    current = game.current
    assert current is not None and current.options[12] == "▫️"
    # Las de la izquierda no tienen malas cerca: se abren solas en cadena.
    assert {10, 11, 12, 15, 16, 17} <= game.opened
    assert current.options[13] == "1️⃣" and 14 not in game.opened


def test_abrir_una_mala_resta_y_se_queda_a_la_vista() -> None:
    game = audit(bad={0, 4})
    assert not game.press(0, NOW)
    assert (game.points, game.blown, game.misses) == (-AUDIT_HIT, 1, 1)
    current = game.current
    assert current is not None and current.options[0] == AUDIT_PACKS["sobrecostes"].hit
    assert 0 not in game.moves()


def test_el_fallo_gratis_marca_la_mala_sin_abrirla() -> None:
    game = audit(bad={0, 4}, retries=1)
    assert not game.press(0, NOW)
    assert (game.points, game.blown, game.saved, game.flags) == (0, 0, 1, {0})
    assert not game.press(4, NOW) and game.blown == 1


def test_abrir_todas_las_buenas_acaba_el_tablero_y_cuenta_las_malas_sin_tocar() -> None:
    game = audit(bad={0, 4})
    play_best(game)
    assert game.completed and game.score() == 100
    assert (game.clean_boards, game.correct, game.perfect_rounds) == (1, 4, 1)


def test_pulsar_una_casilla_ya_abierta_no_hace_nada() -> None:
    game = audit(bad={0, 4})
    game.press(1, NOW)
    index = game.index
    assert not game.press(1, NOW) and game.index == index


def test_la_guardia_tiene_dos_tableros_de_auditoria() -> None:
    game = audit(boards=2)
    play_best(game)
    assert (game.board, game.clean_boards, game.score()) == (2, 2, 100)


# -- Despacho -----------------------------------------------------------------------------


def desk(cards: list[DeskCard], *, retries: int = 0, fifty: bool = False) -> DeskGame:
    return DeskGame(
        Mechanic.DESK,
        "",
        [],
        60,
        NOW,
        retries=retries,
        fifty=fifty,
        ordago=ORDAGO_PASS,
        pack=DESK_PACKS["control"],
        cards=cards,
    )


def card(*options: DeskOption, wear: int | None = None) -> DeskCard:
    return DeskCard("Algo pasa.", list(options), wear)


def test_cada_opcion_del_despacho_sube_una_barra_y_baja_otra() -> None:
    game = desk([card(DeskOption("a", 0, 2, 1, 1), DeskOption("b", 2, 1, 3, 0), wear=3)] * 2)
    assert game.press(0, NOW)
    # Sube Socios, baja Prensa y, después, se desgasta sola la Caja.
    assert game.bars == [DESK_START + 2, DESK_START - 1, DESK_START, DESK_START - 1]
    current = game.current
    assert current is not None and "▲▲" in current.prompt and "se desgasta sola" in current.prompt


def test_una_barra_no_pasa_del_tope() -> None:
    game = desk([card(DeskOption("a", 0, 3, 1, 0))] * 3)
    game.press(0, NOW)
    game.press(0, NOW)
    assert game.bars[0] == DESK_MAX


def test_lo_que_se_paga_mas_tarde_llega_dos_decisiones_despues() -> None:
    later = DeskOption("a", 0, 3, 1, 2, later=True)
    quiet = DeskOption("b", 2, 1, 3, 0)
    game = desk([card(later, quiet), card(quiet, quiet), card(quiet, quiet), card(quiet, quiet)])
    game.press(0, NOW)
    assert game.bars[1] == DESK_START and game.later_used == 1
    current = game.current
    assert current is not None and "Por pagar" in current.prompt
    game.press(0, NOW)
    assert game.bars[1] == DESK_START
    game.press(0, NOW)
    assert game.bars[1] == DESK_START - 2


def test_una_barra_a_cero_es_crisis_y_la_nota_se_queda_en_lo_hecho() -> None:
    sink = DeskOption("a", 0, 1, 1, 2)
    game = desk([card(sink, sink)] * 8)
    assert game.press(0, NOW)
    assert not game.press(0, NOW)
    assert game.crisis and game.finished(NOW) and game.current is None
    assert game.bars[1] == 0
    # Una decisión hecha de ocho: como mucho, un octavo de la nota.
    assert game.score() <= 100 / 8


def test_el_fallo_gratis_salva_una_crisis() -> None:
    sink = DeskOption("a", 0, 1, 1, 2)
    game = desk([card(sink, sink)] * 3, retries=1)
    game.press(0, NOW)
    assert game.press(0, NOW)
    assert (game.crisis, game.bars[1], game.saved) == (False, 1, 1)


def test_acabar_el_despacho_bien_alto_es_un_100() -> None:
    up = [DeskOption("a", bar, 2, (bar + 1) % 4, 0) for bar in range(4)]
    game = desk([card(up[i % 4], up[(i + 1) % 4]) for i in range(8)])
    while game.current is not None:
        game.press(0, NOW)
    assert game.bars == [DESK_MAX] * 4
    assert game.score() == 100 and game.perfect_rounds == 1 and game.correct == 8
    assert 4 * DESK_START + 2 * DESK_START < DESK_PAR  # quedarse como al empezar no llega


def test_la_chuleta_tacha_la_opcion_que_peor_deja_el_despacho() -> None:
    good, bad = DeskOption("buena", 0, 2, 1, 0), DeskOption("mala", 0, 1, 1, 3)
    game = desk([card(good, bad)] * 2, fifty=True)
    current = game.current
    assert current is not None
    struck = [line for line in current.prompt.splitlines() if line.startswith("✂️")]
    assert len(struck) == 1 and "mala" in struck[0]
    assert game.best() == 0


# -- Órdago final -------------------------------------------------------------------------


def quick(position: Position | None = None) -> MiniGame:
    """Una partida real acabada en un momento: queda casi todo el extra de tiempo."""
    game = new_game(position or JOBS[0].position(4), random.Random(2), now=NOW)
    while game.ordago != ORDAGO_OFFER:
        current = game.current
        assert current is not None
        game.press(current.answer[0], NOW + 1)
    return game


def test_acabar_con_tiempo_de_sobra_ofrece_el_ordago() -> None:
    game = quick()
    current = game.current
    assert current is not None and "Órdago" in current.prompt and len(current.options) == 2
    assert game.stake >= ORDAGO_MIN
    assert not game.completed and not game.finished(NOW + 2)


def test_plantarse_deja_el_extra_como_estaba() -> None:
    game = quick()
    stake = game.stake
    assert game.press(0, NOW + 30)
    assert game.ordago == ORDAGO_PASS and game.completed
    # El extra se mide al acabar las rondas, no al contestar al órdago.
    assert game.time_left == pytest.approx(game.deadline - (NOW + 1))
    assert round(game._time_bonus()) == stake


@pytest.mark.parametrize(
    ("coin", "state", "factor"), [(True, ORDAGO_WON, 2), (False, ORDAGO_LOST, 0)]
)
def test_el_ordago_dobla_el_extra_o_lo_pierde(coin: bool, state: str, factor: int) -> None:
    game = quick()
    game.coin = coin
    plain = 10 * (game.deadline - (NOW + 1)) / game.seconds
    assert game.press(1, NOW + 2) is coin
    assert game.ordago == state and game.completed
    assert game._time_bonus() == pytest.approx(factor * plain)


def test_si_se_acaba_el_tiempo_sin_contestar_al_ordago_se_planta() -> None:
    game = quick()
    game.finish(game.deadline + 5)
    assert game.ordago == ORDAGO_PASS
    assert game.time_left == pytest.approx(game.deadline - (NOW + 1))


def test_sin_tiempo_de_sobra_no_hay_ordago() -> None:
    game = new_game(JOBS[0].position(4), random.Random(2), now=NOW)
    late = game.deadline - 1
    while (current := game.current) is not None:
        game.press(current.answer[0], late)
    assert game.ordago == "" and game.completed


def test_el_ordago_se_ofrece_tambien_en_los_juegos_que_rotan() -> None:
    for position in variants()[:12]:
        game = new_game(position, random.Random(8), now=NOW)
        while game.current is not None and game.ordago != ORDAGO_OFFER:
            game.press(game.best(), NOW + 1)  # type: ignore[attr-defined]
        if isinstance(game, DeskGame) and game.crisis:
            continue
        assert game.ordago == ORDAGO_OFFER, position.title
        game.coin = True
        game.press(1, NOW + 2)
        assert game.ordago == ORDAGO_WON and game.completed


def test_el_ordago_cuenta_para_sus_logros() -> None:
    from types import SimpleNamespace

    for option, coin, want in (
        (1, True, "work_ordago_won"),
        (1, False, "work_ordago_lost"),
        (0, None, "work_ordago_pass"),
    ):
        game = quick()
        game.coin = coin
        game.press(option, NOW + 2)
        add: dict[str, int] = {}
        _work_game_stats(SimpleNamespace(game=game, score=game.score(), coffees=0), add)
        assert add[want] == 1
        assert ("work_ordago" in add) == (option == 1)


# -- Logros de Auditoría y Despacho -------------------------------------------------------


def test_una_auditoria_cuenta_para_sus_logros() -> None:
    clean = audit(bad={0, 4})
    play_best(clean)
    add: dict[str, int] = {}
    _audit_stats(clean, add)
    assert add == {"work_audit_shifts": 1, "work_audit_clean": 1, "work_audit_fast": 1}

    messy = audit(bad={0, 4})
    messy.press(0, NOW)
    messy.press(4, NOW)
    play_best(messy)
    add = {}
    _audit_stats(messy, add)
    assert add == {"work_audit_shifts": 1, "work_audit_blown": 2, "work_audit_all_blown": 1}


def test_un_despacho_cuenta_para_sus_logros() -> None:
    up = [DeskOption("a", bar, 2, (bar + 1) % 4, 0) for bar in range(4)]
    good = desk([card(up[i % 4], up[(i + 1) % 4]) for i in range(8)])
    while good.current is not None:
        good.press(0, NOW)
    add: dict[str, int] = {}
    _desk_stats(good, add)
    assert add == {"work_desk_shifts": 1, "work_desk_full": 1, "work_desk_calm": 1}

    sink = DeskOption("a", 0, 1, 1, 2, later=False)
    bad = desk([card(sink, sink)] * 8)
    bad.press(0, NOW)
    bad.press(0, NOW)
    add = {}
    _desk_stats(bad, add)
    assert add == {"work_desk_shifts": 1, "work_desk_crisis": 1, "work_desk_crisis_fast": 1}
