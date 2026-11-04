"""Pruebas de la lógica de las combinadas: cuota, tope del premio y cómo se resuelven."""

from __future__ import annotations

import itertools
import math

from bot.services.achievements import CATALOG
from bot.services.sports import (
    COMBO_MARKETS,
    MAX_COMBO_PAYOUT,
    RTP,
    VOID_AFTER,
    LegStatus,
    Market,
    Score,
    combo_odds,
    combo_outcome,
    combo_payout,
    leg_status,
)

WON, LOST, OPEN, VOID = LegStatus.WON, LegStatus.LOST, LegStatus.OPEN, LegStatus.VOID


def test_la_cuota_es_el_producto_de_las_patas() -> None:
    assert combo_odds([200, 300]) == 600
    assert combo_odds([150, 150, 150]) == 337  # 3,375 hacia abajo
    assert combo_odds([250]) == 250
    assert combo_odds([]) == 100


def test_la_cuota_nunca_pasa_del_producto_exacto_ni_se_aleja_de_el() -> None:
    for legs in itertools.product((101, 133, 187, 245, 999, 2_500), repeat=3):
        exact = math.prod(legs) / 100 ** (len(legs) - 1)
        assert exact * 0.99 < combo_odds(legs) <= exact


def test_el_margen_se_multiplica_con_las_patas() -> None:
    """Con todas las patas a su cuota justa menos el margen, el retorno es RTP^n."""
    for n in range(2, 6):
        # Patas a 50 % cada una: cuota justa 2,00, la del bot 1,88.
        house = math.floor(100 * RTP / 0.5)
        odds = combo_odds([house] * n)
        assert odds / 100 * 0.5**n <= RTP**n + 1e-9


def test_el_premio_tiene_tope() -> None:
    assert combo_payout(100, 500) == 500
    assert combo_payout(50_000, 2_500 * 25) == MAX_COMBO_PAYOUT


def test_el_exacto_no_entra_en_las_combinadas() -> None:
    assert Market.EXACT not in COMBO_MARKETS
    assert {Market.RESULT, Market.GOALS, Market.BOTH} == COMBO_MARKETS


def test_una_pata_con_marcador_gana_o_pierde() -> None:
    assert leg_status("1", Score(2, 0), commence=0, now=1) is WON
    assert leg_status("+2.5", Score(1, 1), commence=0, now=1) is LOST
    assert leg_status("si", Score(1, 1), commence=0, now=1) is WON


def test_una_pata_sin_marcador_espera_y_luego_se_anula() -> None:
    assert leg_status("1", None, commence=0, now=VOID_AFTER - 1) is OPEN
    assert leg_status("1", None, commence=0, now=VOID_AFTER) is VOID


def test_una_pata_fallada_pierde_la_combinada_aunque_falten_otras() -> None:
    assert combo_outcome([(WON, 200), (LOST, 300), (OPEN, 150)]) == (LOST, 0)


def test_con_patas_abiertas_y_ninguna_fallada_sigue_abierta() -> None:
    assert combo_outcome([(WON, 200), (OPEN, 150)]) == (OPEN, 0)


def test_todas_acertadas_cobra_el_producto() -> None:
    assert combo_outcome([(WON, 200), (WON, 150)]) == (WON, 300)


def test_una_pata_anulada_cuenta_como_uno() -> None:
    assert combo_outcome([(WON, 200), (VOID, 900), (WON, 150)]) == (WON, 300)


def test_todas_anuladas_se_devuelve() -> None:
    assert combo_outcome([(VOID, 200), (VOID, 150)]) == (VOID, 0)


COMBO_STATS = (
    "sports_combos", "sports_combo_staked", "sports_combo_full", "sports_combo_mixed",
    "sports_combo_all_markets", "sports_combo_huge", "sports_combo_all_in", "sports_combo_min",
    "sports_combo_666", "sports_combo_69", "sports_combos_settled", "sports_combo_wins",
    "sports_combo_losses", "sports_combo_capped", "sports_combo_saved_by_void",
    "sports_combo_full_win", "sports_combo_last_leg", "sports_combo_first_leg",
    "sports_combo_refunds", "sports_combo_odds_max", "sports_combo_legs_max",
    "sports_combo_win_max", "sports_combo_odds_won_max", "sports_combo_legs_won_max",
)  # fmt: skip


def test_cada_estadistica_de_combinadas_tiene_algun_logro() -> None:
    used = {stat for a in CATALOG for stat, _goal in a.conditions}
    assert [stat for stat in COMBO_STATS if stat not in used] == []
