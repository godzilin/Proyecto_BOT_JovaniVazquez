"""Pruebas de la rotación de minijuegos de `pala` (P5 en adelante): Cuadrante y Negociación.

La rotación está en `bot.services.work.Position.on`, los juegos en
`bot.services.work_games` (`PlanGame`, `DealGame`) y su contenido en
`bot.services.work_rotation`.
"""

from __future__ import annotations

import random

from bot.services.achievements import _deal_stats, _plan_stats
from bot.services.work import Mechanic, Position
from bot.services.work_catalog import JOBS
from bot.services.work_games import (
    DEAL_BREAK,
    DEAL_PROBE,
    DEAL_START,
    DEAL_STEP,
    PLAN_CAPACITY,
    Deal,
    DealGame,
    MiniGame,
    Piece,
    PlanGame,
    deal_mood,
    new_game,
)
from bot.services.work_rotation import DEAL_PACKS, DEAL_TELLS, PLAN_PACKS
from bot.services.work_tools import NO_PERKS, Perks, perks_for

NOW = 1_000.0
ROTATED = (Mechanic.PLAN, Mechanic.DEAL)


def rotating() -> list[Position]:
    return [p for job in JOBS for p in job.all_positions if p.alts]


def play_best(game: MiniGame) -> None:
    """Juega pulsando siempre el botón de `best()`."""
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
            packs = PLAN_PACKS if mechanic is Mechanic.PLAN else DEAL_PACKS
            assert mechanic in ROTATED
            assert content in packs, (position.title, content)


def test_el_minijuego_alterna_un_dia_cada_uno() -> None:
    position = rotating()[0]
    today, tomorrow = position.on(100), position.on(101)
    assert {today.mechanic, tomorrow.mechanic} == {position.mechanic, position.alts[0][0]}
    assert position.on(102).mechanic is today.mechanic


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
    for position in rotating():
        day = next(d for d in range(2) if position.on(d).rotated)
        game = new_game(position.on(day), random.Random(3), now=NOW)
        while (current := game.current) is not None:
            assert 1 <= len(current.options) <= 10, position.title
            assert all(0 < len(label) <= 80 for label in current.options), position.title
            assert len(game.header) + len(current.prompt) < 3_000, position.title
            game.press(0, NOW + 1)


def test_jugar_lo_mejor_saca_100_en_todos_los_puestos_que_rotan() -> None:
    for position in rotating():
        day = next(d for d in range(2) if position.on(d).rotated)
        for guard in (False, True):
            game = new_game(position.on(day), random.Random(5), now=NOW, guard=guard)
            play_best(game)
            assert game.score() == 100, position.title
            assert game.completed


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
