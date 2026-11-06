"""Pruebas de las reglas del juego del pico (`bot.services.picar`)."""

from dataclasses import replace
from fractions import Fraction

import pytest

import bot.services.picar_physics as physics
from bot.services import picar
from bot.services.picar import (
    OFFERS,
    SCALE,
    TARGET_RTP,
    Calibration,
    check,
    format_mult,
    mult_units,
    payout,
    play,
    settle,
)
from bot.services.picar_physics import PICKS, simulate
from bot.services.picar_world import MINERALS


@pytest.fixture(scope="module")
def cal() -> Calibration:
    return Calibration.load()


def test_la_calibracion_guardada_vale_con_este_codigo(cal: Calibration) -> None:
    # Si falla tras tocar la física, el mundo o los precios: `docs/calibrar_picar.py`.
    assert check(cal) is None


def test_cada_pico_devuelve_el_995_sin_pasarse(cal: Calibration) -> None:
    for key, pick in cal.picks.items():
        assert Fraction(99, 100) < pick.rtp <= TARGET_RTP, key
        assert set(pick.values) == set(MINERALS)
        assert all(v >= 1 for v in pick.values.values())


@pytest.mark.lenta
def test_la_calibracion_detecta_lo_que_cambia(cal: Calibration, monkeypatch) -> None:
    assert "física" in check(replace(cal, params="otro"), replay=False)
    assert "huella" in check(replace(cal, fingerprint="otra"))
    cheaper = {**OFFERS, "wood": replace(OFFERS["wood"], cost=11)}
    monkeypatch.setattr(picar, "OFFERS", cheaper)
    assert "Madera" in check(cal, replay=False)


def test_tocar_la_fisica_invalida_la_calibracion(cal: Calibration, monkeypatch) -> None:
    monkeypatch.setattr(physics, "GRAVITY", physics.GRAVITY * 1.01)
    assert check(cal, replay=False) is not None


def test_el_premio_es_la_suma_por_el_multiplicador_con_tope() -> None:
    offer = OFFERS["iron"]
    assert payout(offer, 25_000, dragon=False) == 500
    assert payout(offer, 25_049, dragon=False) == 500  # hacia abajo, a Y$ enteros
    assert payout(offer, 10**9, dragon=False) == offer.cost * offer.top
    assert payout(offer, 0, dragon=True) == offer.cost * offer.top
    assert mult_units({"coal": 100, "diamond": 900}, {"coal": 3, "diamond": 2, "dirt": 9}) == 2100
    assert mult_units({"coal": 100}, {"coal": 3}, multiplier=6) == 1800


def test_el_multiplicador_se_lee_bien() -> None:
    assert format_mult(0) == "×0,00"
    assert format_mult(125_000) == "×12,50"
    assert format_mult(25 * SCALE) == "×25,00"
    assert format_mult(12_345_678) == "×1.234,56"
    assert format_mult(42) == "×0,004"
    assert format_mult(999) == "×0,099"


def test_jugar_una_semilla_da_siempre_lo_mismo(cal: Calibration) -> None:
    for key in OFFERS:
        a, b = play(key, cal, seed=7), play(key, cal, seed=7)
        assert (a.units, a.payout, a.run.fingerprint()) == (b.units, b.payout, b.run.fingerprint())
        assert a.run.frames, "para dibujarla hace falta la partida grabada"
    with pytest.raises(ValueError):
        play("wood", cal, seed=cal.library)


def test_el_premio_coincide_con_la_cuenta_del_calibrador(cal: Calibration) -> None:
    # El calibrador cuenta los premios con numpy; aquí se rehace a mano con `settle`.
    for key in ("stone", "diamond"):
        offer, values = OFFERS[key], cal.picks[key].values
        for seed in range(30):
            run = simulate(seed, key, record=False)
            units = sum(values[m] * run.broken.get(m, 0) for m in MINERALS) * run.multiplier
            top = offer.cost * offer.top
            expected = top if run.dragon else min(top, offer.cost * units // SCALE)
            assert settle(key, run, cal).payout == expected


def test_las_ofertas_son_las_de_los_picos() -> None:
    assert OFFERS.keys() == PICKS.keys()
    costs = [OFFERS[k].cost for k in ("wood", "stone", "iron", "diamond", "netherite")]
    assert costs == sorted(costs)
