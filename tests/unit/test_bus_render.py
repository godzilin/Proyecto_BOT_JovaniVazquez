"""Pruebas de bot.services.bus_render: los estados de la mesa y de la animación (sin navegador)."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from bot.services.blackjack import Card
from bot.services.bus_render import (
    FINAL_FRAME_MS,
    QUESTIONS,
    Banner,
    Board,
    BusRenderer,
    board_state,
    bus_stop_for,
    durations,
    meta_state,
    reveal_states,
    tension_frames,
)

CARDS = (Card(13, 1), Card(5, 0), Card(9, 2), Card(1, 3), Card(12, 1))


def test_las_paradas_son_las_cinco_manos_y_la_ultima_es_la_vuelta() -> None:
    assert meta_state()["stops"] == ["COLOR", "ALTURA", "RANGO", "PALO", "LA VUELTA"]
    assert len(QUESTIONS) == 5


def test_la_mesa_al_empezar_tiene_el_primer_hueco_boca_abajo_y_activo() -> None:
    state = board_state(Board(active=0))
    assert state["active"] == 0 and state["question"] == QUESTIONS[0]
    assert state["slots"][0]["down"] and state["slots"][0]["card"] is None
    assert not any(slot["down"] for slot in state["slots"][1:])
    assert state["bus"]["x"] == 0


def test_la_mesa_al_cobrar_ensena_la_carta_siguiente_como_fantasma() -> None:
    board = Board(
        cards=CARDS[:2],
        results=(True, True),
        active=None,
        ghost=CARDS[2],
        banner=Banner("cash", "¡COBRADO!", "×4,00"),
    )
    state = board_state(board)
    assert [slot["mark"] for slot in state["slots"][:2]] == ["win", "win"]
    assert state["slots"][2]["ghost"] and state["slots"][2]["card"]["rank"] == 9
    assert state["question"] is None and state["banner"]["title"] == "¡COBRADO!"
    assert state["bus"]["x"] == 2  # espera en la parada de la carta que venía


@pytest.mark.parametrize(
    ("board", "stop", "broken"),
    [
        (Board(active=3), 3.0, False),
        (Board(cards=CARDS[:4], results=(True,) * 4, active=None), 4.0, False),
        (Board(cards=CARDS, results=(True,) * 5, active=None), 4.0, False),
        (Board(cards=CARDS[:3], results=(True, True, False), active=None), 2.0, True),
    ],
)
def test_la_parada_del_autobus_segun_la_mesa(board: Board, stop: float, broken: bool) -> None:
    assert bus_stop_for(board) == (stop, broken)


def test_el_autobus_averiado_echa_humo_en_la_mesa() -> None:
    board = Board(cards=CARDS[:1], results=(False,), active=None)
    bus = board_state(board)["bus"]
    assert bus["smoke"] and bus["soot"] > 0


@pytest.mark.parametrize("hand", [0, 6])
def test_una_mano_fuera_de_1_a_5_no_se_anima(hand: int) -> None:
    with pytest.raises(ValueError, match="mano"):
        reveal_states(CARDS, hand, seed=1)


def test_faltan_cartas_para_la_mano() -> None:
    with pytest.raises(ValueError, match="mano"):
        reveal_states(CARDS[:2], 3, seed=1)


def test_la_tension_crece_con_la_mano_y_la_animacion_cabe_en_70_fotogramas() -> None:
    lengths = [tension_frames(h) for h in range(1, 6)]
    assert lengths == sorted(lengths) and lengths[-1] >= 2 * lengths[0]
    for hand in range(1, 6):
        common, win, lose = reveal_states(CARDS, hand, seed=1)
        assert len(common) + max(len(win), len(lose)) <= 70
    # Las esperas son fotogramas más largos: la mano alta dura mucho más que la baja.
    total = [sum(s["ms"] for s in reveal_states(CARDS, h, seed=1)[0]) for h in (1, 5)]
    assert total[1] > 2 * total[0]


def test_la_animacion_solo_depende_de_las_cartas_la_mano_y_la_semilla() -> None:
    assert reveal_states(CARDS, 3, seed=7) == reveal_states(CARDS, 3, seed=7)
    assert reveal_states(CARDS, 3, seed=7) != reveal_states(CARDS, 3, seed=8)


def test_antes_de_girar_la_carta_esta_boca_abajo_y_las_anteriores_acertadas() -> None:
    common, _, _ = reveal_states(CARDS, 3, seed=2)
    first = common[0]["slots"]
    assert [slot["mark"] for slot in first[:2]] == ["win", "win"]
    assert first[2]["down"] and first[2]["flip"] == 0
    assert all(slot["card"] is None for slot in first[3:])
    # Al final del tramo común la carta está descubierta y aún sin marca.
    last = common[-1]["slots"][2]
    assert last["flip"] == 1 and last["mark"] is None


def test_cada_cola_trae_su_cartel_y_la_lose_empieza_entera() -> None:
    expected = {
        1: "¡SIGUIENTE PARADA!",
        3: "¡SIGUIENTE PARADA!",
        4: "¡AUTOBÚS COMPLETO!",
        5: "¡LA VUELTA!",
    }
    for hand, title in expected.items():
        _, win, lose = reveal_states(CARDS, hand, seed=1)
        assert win[-1]["banner"]["title"] == title
        assert win[-1]["banner"]["kind"] == ("gold" if hand == 5 else "win")
        assert lose[-1]["banner"]["title"] == "¡FIN DEL TRAYECTO!"
        assert lose[0]["full"] is True and not any(s.get("full") for s in lose[1:])
        assert not any(s.get("full") for s in win)


def test_al_acertar_el_autobus_avanza_y_al_fallar_se_averia() -> None:
    _, win, lose = reveal_states(CARDS, 2, seed=1)
    assert win[-1]["bus"]["x"] == 2 and win[-1]["active"] == 2
    assert win[-1]["slots"][1]["mark"] == "win"
    assert lose[-1]["bus"]["x"] == 1 and lose[-1]["bus"]["smoke"]
    assert lose[-1]["slots"][1]["mark"] == "lose" and lose[-1]["active"] is None


def test_la_vuelta_acertada_se_celebra_sin_mover_el_autobus() -> None:
    _, win, _ = reveal_states(CARDS, 5, seed=1)
    assert win[-1]["bus"]["x"] == 4 and win[-1]["active"] is None
    assert any(s["sparks"] for s in win)


def test_las_duraciones_dejan_el_ultimo_fotograma_un_minuto() -> None:
    common, win, _ = reveal_states(CARDS, 2, seed=1)
    ms = durations(common + win)
    assert ms[-1] == FINAL_FRAME_MS and len(ms) == len(common) + len(win)
    assert all(m % 10 == 0 and m >= 20 for m in ms)  # el GIF guarda centésimas


def test_el_plan_b_de_pillow_dibuja_la_mesa_y_las_dos_mesas_finales() -> None:
    renderer = BusRenderer()
    assert Image.open(io.BytesIO(renderer.board(Board(active=0)))).size == (640, 360)
    reveal = renderer.reveal(CARDS, 5, seed=1)
    assert reveal.win.gif is None and reveal.lose.gif is None
    assert reveal.win.png != reveal.lose.png
