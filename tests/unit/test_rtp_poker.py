"""`docs/rtp_poker.py` valora y paga cada mano igual que `bot.services.poker`.

El script calcula la ventaja de la casa con su propio evaluador en bloque (numpy),
mucho más rápido que el del bot. Si alguien cambia una regla o un pago en
`bot.services.poker` y no aquí, la cifra mentiría: estas pruebas comparan los
dos con repartos al azar y con al menos 100 manos de cada categoría (la escalera
real incluida) y avisan.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

from bot.services import poker
from bot.services.blackjack import Card

SCRIPT = Path(__file__).resolve().parents[2] / "docs" / "rtp_poker.py"


@pytest.fixture(scope="module")
def rtp() -> ModuleType:
    spec = importlib.util.spec_from_file_location("rtp_poker", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def card(c: int) -> Card:
    """Carta del script (altura 0..12 del 2 al as, por 4, más el palo) a la del bot."""
    height = c // 4
    return Card(1 if height == 12 else height + 2, c % 4)


@pytest.mark.lenta
def test_el_evaluador_y_el_pago_coinciden_con_los_del_bot(rtp: ModuleType) -> None:
    cards = rtp.deal(np.random.default_rng(7), 200_000)
    player = rtp.evaluate(np.concatenate([cards[:, :2], cards[:, 4:]], axis=1))
    dealer = rtp.evaluate(cards[:, 2:])
    gain = rtp.net(player, dealer)
    cats = rtp.category(player)
    rows = [*range(1_000)]
    for cat in range(10):
        found = np.flatnonzero(cats == cat)[:100]
        assert len(found), f"ninguna mano de categoría {cat}: sube el reparto"
        rows += found.tolist()
    for i in rows:
        hand = poker.Deal(
            player=(card(cards[i, 0]), card(cards[i, 1])),
            dealer=(card(cards[i, 2]), card(cards[i, 3])),
            board=tuple(card(c) for c in cards[i, 4:]),  # type: ignore[arg-type]
        )
        mine, theirs = hand.player_value, hand.dealer_value
        assert mine.order == cats[i], (mine.label, i)
        assert (mine > theirs) == (player[i] > dealer[i]), (mine.label, theirs.label)
        assert (mine == theirs) == (player[i] == dealer[i]), (mine.label, theirs.label)
        expected = poker.payout(hand, 1, called=True) - (1 + poker.CALL_FACTOR)
        assert gain[i] == expected, (mine.label, theirs.label)


def test_cada_reparto_y_cada_final_son_cartas_distintas(rtp: ModuleType) -> None:
    rng = np.random.default_rng(1)
    cards = rtp.deal(rng, 50_000)
    assert (np.sort(cards, axis=1)[:, 1:] != np.sort(cards, axis=1)[:, :-1]).all()
    known, rest = rtp.flops(rng, 1_000)
    # Lo que queda en el mazo son las 47 que no se ven, sin repetir.
    for row_known, row_rest in zip(known, rest, strict=True):
        assert set(row_known).isdisjoint(row_rest) and len(set(row_rest)) == 47
    assert rtp.features(known).max() < rtp.KEYS


def test_medir_con_una_estrategia_da_cifras_con_sentido(rtp: ModuleType) -> None:
    """Humo: con 'ir siempre' la aprendida y la de ir siempre coinciden y no ahorra nada."""
    always = np.ones(rtp.KEYS, dtype=bool)
    r = rtp.measure(always, 2_000, 2, seed=3)
    assert r["learned"] == r["always"]
    assert r["saving"] == 0
    assert r["calls"] == 1
    assert -1 < r["always"] < 1
