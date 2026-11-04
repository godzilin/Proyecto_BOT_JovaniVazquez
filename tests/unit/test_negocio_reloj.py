"""El reloj de un turno del negocio: lo que tarda la pantalla no gasta paciencia ni turno."""

from __future__ import annotations

import pytest

from bot.services.business import Business
from bot.services.business_catalog import SECTOR_BY_KEY
from bot.services.business_games import PAUSE_MAX, TURN_SECONDS, TurnGame, make_clients


def game_of(sector: str = "cafeteria", count: int = 4, *, now: float = 1_000.0) -> TurnGame:
    business = Business(user_id=1, sector=sector, barrio="triana", name="Bar", seed=7)
    business.stock = {p.key: 50 for p in SECTOR_BY_KEY[sector].products}
    return TurnGame(business, make_clients(business, count, seed=3), now=now)


def serve(game: TurnGame, now: float) -> None:
    client = game.current
    assert client is not None
    for key, n in client.order.items():
        for _ in range(n):
            game.press(f"p:{key}", now)
    game.press("serve", now)


def test_la_espera_a_la_pantalla_del_cliente_no_gasta_su_paciencia_ni_el_turno() -> None:
    game = game_of()
    patience = game.client_deadline - game.since
    # La imagen del primer cliente tarda 2 s en llegar: su paciencia y el cierre empiezan ahí.
    assert game.shown(game.round, 1_002.0) == pytest.approx(2.0)
    assert game.client_deadline == pytest.approx(1_002.0 + patience)
    assert game.deadline == pytest.approx(1_000.0 + TURN_SECONDS + 2.0)
    # Una segunda edición de la misma ronda (el reloj, el mismo dibujo) no para otra vez.
    assert game.shown(game.round, 1_005.0) == 0.0
    assert game.client_deadline == pytest.approx(1_002.0 + patience)


def test_lo_que_tarda_el_siguiente_cliente_se_cuenta_desde_que_se_sirvio_al_anterior() -> None:
    game = game_of()
    game.shown(game.round, 1_000.0)
    serve(game, 1_006.0)
    assert len(game.outcomes) == 1 and game.outcomes[0].result == "ok"
    old = game.round - 1
    assert game.shown(old, 1_009.0) == 0.0  # una ronda que ya pasó no para nada
    assert game.shown(game.round, 1_007.5) == pytest.approx(1.5)
    assert game.since == pytest.approx(1_007.5)


def test_una_espera_atascada_no_alarga_el_turno_sin_fin() -> None:
    game = game_of()
    assert game.shown(game.round, 1_000.0 + 60) == PAUSE_MAX
    assert game.deadline == pytest.approx(1_000.0 + TURN_SECONDS + PAUSE_MAX)


def test_el_presupuesto_del_taller_tambien_para_el_reloj() -> None:
    game = game_of("taller")
    game.shown(game.round, 1_000.0)
    client = game.current
    assert client is not None
    game.press(f"d:{client.fault}", 1_010.0)
    assert game.step == "presupuesto"
    before = game.client_deadline
    assert game.shown(game.round, 1_011.0) == pytest.approx(1.0)
    assert game.client_deadline == pytest.approx(before + 1.0)


def test_con_la_pausa_un_cliente_atendido_a_tiempo_no_se_va() -> None:
    game = game_of()
    patience = game.patience
    # Sin la pausa, servir a los `patience + 1` s de abrir lo echaría; con 2 s de espera a la
    # imagen, todavía está.
    game.shown(game.round, 1_002.0)
    serve(game, 1_000.0 + patience + 1)
    assert game.outcomes[0].result == "ok"
