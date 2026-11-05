"""Pruebas de la Rueda: reglas (`bot.services.rueda`), objetos, ritmo del giro y repositorio."""

from __future__ import annotations

import random
from datetime import date, datetime
from pathlib import Path

import pytest

from bot.repositories.rueda import RuedaRepository
from bot.services.achievements import rueda_stats
from bot.services.levels import TIMEZONE
from bot.services.lootboxes import BOX_BY_KEY
from bot.services.rueda import (
    PRIZE_BY_KEY,
    PRIZES,
    TOTAL_WEIGHT,
    Draw,
    PrizeKind,
    chances,
    countdown,
    day_of,
    draw,
    expected_cash,
    expected_in_kind,
    landing_angle,
    layout,
    next_reset,
    showcase_for,
    streak,
    wedge_at,
    week_of,
)
from bot.services.rueda_catalog import (
    PIECE_WEIGHT,
    PIECES,
    TITLE_KEYS,
    TITLES,
    TREAT,
    pick_piece,
    piece_value,
)
from bot.services.rueda_render import (
    FINAL_FRAME_MS,
    banner_for,
    result_state,
    seconds,
    spin_motion,
    spin_states,
    tension,
)
from bot.services.shop_catalog import CATALOG_BY_KEY

IMV_MAX = 1_500


def test_las_porciones_suman_mil_y_dan_cada_premio_su_probabilidad() -> None:
    wedges = layout()
    assert sum(w.weight for w in wedges) == TOTAL_WEIGHT
    assert wedges[0].start == 0 and wedges[-1].end == pytest.approx(360)
    odds = {k: round(v * TOTAL_WEIGHT) for k, v in chances(wedges).items()}
    assert odds == {
        "y300": 190, "y600": 150, "y1000": 90, "y2500": 30, "y5000": 8, "y15000": 2,
        "xp250": 120, "xp1000": 40, "carton": 120, "regalo": 30, "maletin": 5,
        "chuche": 80, "pieza": 53, "otra": 30, "nada": 50, "expositor": 2,
    }  # fmt: skip
    assert {w.prize.key for w in wedges} == {p.key for p in PRIZES}


def test_sin_niveles_no_hay_xp_y_el_resto_ocupa_la_rueda() -> None:
    wedges = layout(levels=False)
    assert all(w.prize.kind is not PrizeKind.XP for w in wedges)
    assert sum(chances(wedges).values()) == pytest.approx(1)
    assert wedges[-1].end == pytest.approx(360)


def test_las_rayas_son_los_premios_gordos() -> None:
    slivers = {w.prize.key for w in layout() if w.sliver}
    assert slivers == {"expositor", "y15000", "y5000", "maletin"}


def test_lo_que_vale_una_vuelta_esta_fijado() -> None:
    """En efectivo, una cuarta parte del IMV; en especie, menos de la mitad del IMV."""
    cash = expected_cash()
    assert cash == pytest.approx(382)
    assert cash < IMV_MAX / 3
    prices = {
        "caja_carton": BOX_BY_KEY["caja_carton"].price,
        "caja_regalo": BOX_BY_KEY["caja_regalo"].price,
        "maletin_ministro": BOX_BY_KEY["maletin_ministro"].price,
        TREAT.key: TREAT.price,
    }
    in_kind = expected_in_kind(prices, piece_value(), TITLES[0].price)
    assert 500 < in_kind < IMV_MAX / 2


def test_el_sorteo_sigue_los_pesos() -> None:
    wedges = layout()
    rng = random.Random(3)
    counts: dict[str, int] = {}
    n = 60_000
    for _ in range(n):
        wedge, stop, seed = draw(rng, wedges)
        assert 0 < stop < 1 and seed > 0
        counts[wedge.prize.key] = counts.get(wedge.prize.key, 0) + 1
    for key, chance in chances(wedges).items():
        assert counts.get(key, 0) / n == pytest.approx(chance, abs=0.01)


@pytest.mark.parametrize("seed", range(40))
def test_la_aguja_para_en_la_porcion_sorteada(seed: int) -> None:
    wedges = layout()
    wedge, stop, spin_seed = draw(random.Random(seed), wedges)
    assert wedge_at(wedges, landing_angle(wedge, stop)).index == wedge.index
    spin = Draw(wedge.index, wedge.prize.key, stop, spin_seed)
    motion = spin_motion(wedges, spin)
    angle, speed = motion.at(motion.duration_ms)
    assert speed == pytest.approx(0, abs=1e-6)
    assert wedge_at(wedges, angle % 360).index == wedge.index


@pytest.mark.parametrize("seed", range(25))
def test_el_giro_dura_lo_justo_y_acaba_en_la_imagen_quieta(seed: int) -> None:
    wedges = layout()
    wedge, stop, spin_seed = draw(random.Random(seed), wedges)
    spin = Draw(wedge.index, wedge.prize.key, stop, spin_seed)
    banner = banner_for(spin.prize, wedge.prize.kind)
    states, durations = spin_states(wedges, spin, banner)
    assert len(states) == len(durations)
    assert 50 <= len(states) <= 120
    assert 3.5 <= seconds(durations) <= 8.5
    assert all(d % 10 == 0 for d in durations), "el GIF cuenta en centésimas"
    assert durations[-1] == FINAL_FRAME_MS
    assert states[-1] == result_state(wedges, spin, banner)


def test_hay_tension_cerca_de_una_raya() -> None:
    wedges = layout()
    sliver = next(w for w in wedges if w.prize.key == "expositor")
    beside = wedges[sliver.index + 1]
    near = Draw(beside.index, beside.prize.key, 0.05, 1)
    far = Draw(wedges[5].index, wedges[5].prize.key, 0.5, 1)
    assert tension(wedges, near) and not tension(wedges, far)
    assert spin_motion(wedges, near).decel_ms > spin_motion(wedges, far).decel_ms


def test_el_mismo_sorteo_da_los_mismos_fotogramas() -> None:
    wedges = layout()
    spin = Draw(3, wedges[3].prize.key, 0.4, 99)
    banner = banner_for(spin.prize, wedges[3].prize.kind)
    assert spin_states(wedges, spin, banner) == spin_states(wedges, spin, banner)


def test_dias_semanas_y_cuenta_atras_en_hora_canaria() -> None:
    noon = datetime(2026, 11, 5, 12, 0, tzinfo=TIMEZONE).timestamp()
    assert day_of(noon) == date(2026, 11, 5)
    assert week_of(date(2026, 11, 5)) == date(2026, 11, 2)
    assert next_reset(noon) - noon == pytest.approx(12 * 3600)
    assert countdown(12 * 3600) == "12 h 0 min"
    assert countdown(30) == "1 min"


def test_la_racha_cuenta_dias_seguidos_hasta_hoy_o_ayer() -> None:
    today = date(2026, 11, 5)
    days = [date(2026, 11, d) for d in (1, 3, 4, 5)]
    assert streak(days, today) == 3
    assert streak(days[:-1], today) == 2, "hoy aún no ha girado: cuenta hasta ayer"
    assert streak([date(2026, 10, 1)], today) == 0


def test_el_expositor_rota_cada_semana_y_salta_los_agotados() -> None:
    weeks = [week_of(date(2026, 11, 2)).toordinal() + 7 * k for k in range(len(TITLE_KEYS))]
    shown = [showcase_for(date.fromordinal(w), TITLE_KEYS, frozenset()) for w in weeks]
    assert sorted(shown) == sorted(TITLE_KEYS), "cada semana, uno distinto"
    first = shown[0]
    assert showcase_for(date.fromordinal(weeks[0]), TITLE_KEYS, frozenset({first})) != first
    assert showcase_for(date(2026, 11, 2), TITLE_KEYS, frozenset(TITLE_KEYS)) is None


def test_las_piezas_salen_por_su_peso_y_estan_en_la_tienda_ocultas() -> None:
    assert [pick_piece(i).key for i in (0, PIECE_WEIGHT - 1)] == [PIECES[0].key, PIECES[-1].key]
    assert len(PIECES) == 8 and len({p.rarity for p in PIECES}) == 5
    for thing in (*PIECES, *TITLES, TREAT):
        entry = CATALOG_BY_KEY[thing.key]
        assert not entry.visible and entry.price == thing.price
    assert all(CATALOG_BY_KEY[t.key].title and CATALOG_BY_KEY[t.key].stock for t in TITLES)
    assert CATALOG_BY_KEY[TREAT.key].food
    for prize in PRIZES:
        if prize.item is not None:
            assert prize.item in CATALOG_BY_KEY, prize.key


def test_los_contadores_de_logros_de_una_vuelta() -> None:
    when = datetime(2026, 12, 31, 0, 3, tzinfo=TIMEZONE)
    delta = rueda_stats(
        "y2500", when=when, money=2_500, streak=7, again_today=1, pieces_owned=3, hamster=True
    )
    assert delta.add["rueda_spins"] == 1 and delta.add["rueda_prize_y2500"] == 1
    assert delta.add["rueda_money_total"] == 2_500 and delta.peak["rueda_money_max"] == 2_500
    assert delta.peak["rueda_streak_max"] == 7 and delta.peak["rueda_col"] == 3
    for stat in ("rueda_midnight", "rueda_nochevieja", "rueda_hamster"):
        assert delta.add[stat] == 1
    later = rueda_stats("nada", when=datetime(2026, 11, 13, 23, 57, tzinfo=TIMEZONE),
                        after_again=True)  # fmt: skip
    assert later.add["rueda_again_nothing"] == 1
    assert later.add["rueda_lastminute"] == 1 and later.add["rueda_viernes13"] == 1


def test_cada_premio_tiene_su_cartel() -> None:
    for prize in PRIZES:
        banner = banner_for(prize.key, prize.kind)
        assert banner["title"] and banner["sub"] and banner["kind"]
    assert PRIZE_BY_KEY["nada"].kind is PrizeKind.NOTHING


async def test_el_repositorio_guarda_una_vuelta_por_dia_y_solo_se_gira_una_vez(
    tmp_path: Path,
) -> None:
    repo = RuedaRepository(tmp_path / "bot.sqlite3")
    await repo.initialize()
    today = date(2026, 11, 5)
    first = await repo.create(1, 10, today, 0, Draw(2, "y600", 0.3, 5), 100.0)
    again = await repo.create(1, 10, today, 0, Draw(9, "nada", 0.5, 6), 101.0)
    assert again.draw == first.draw, "otro panel no vuelve a sortear"
    assert await repo.claim(1, 10, today, 0, 102.0)
    assert not await repo.claim(1, 10, today, 0, 103.0), "el segundo clic pierde"
    await repo.unclaim(1, 10, today, 0)
    assert await repo.claim(1, 10, today, 0, 104.0)
    await repo.set_item(1, 10, today, 0, "y15000", None)
    rows = await repo.day(1, 10, today)
    assert [(r.spin, r.draw.prize, r.claimed) for r in rows] == [(0, "y15000", True)]
    assert await repo.spin_days(1, 10) == [today]
    await repo.delete_guild_data(1)
    assert await repo.day(1, 10, today) == []
