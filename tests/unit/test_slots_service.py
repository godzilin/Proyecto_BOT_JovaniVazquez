"""Pruebas de bot.services.slots: líneas, rodillos, retorno de la máquina y calor.

Las cifras del retorno se calculan recorriendo todas las paradas posibles de
los rodillos (unas 29.000), cada una con el peso de su rodillo virtual, así
que son exactas y no dependen del azar. Lo único simulado es el efecto
conjunto de los giros gratis y la máquina caliente, con una semilla fija.
"""

from __future__ import annotations

import functools
import itertools
import random
from datetime import date

import pytest

from bot.services import slots as slots_module
from bot.services.slots import (
    BELL,
    BIG_WIN,
    BONUS_FREE_SPINS,
    BONUS_MAX,
    BONUS_NEAR_MISS,
    BONUS_SLOW_FROM,
    CHERRY,
    DAILY_STAKE,
    DAILY_STREAK_MAX,
    DIAMOND,
    EPIC_WIN,
    FREE_SPINS,
    GRAPE,
    HEAT_CELLS,
    HEAT_DECAY_SECONDS,
    HEAT_MAX,
    LEMON,
    MEGA_WIN,
    NEAR_MISS_MIN_TIMES,
    POT_SHARE_PERCENT,
    REEL_STRIPS,
    REEL_WEIGHTS,
    RESPIN_RTP,
    SCATTER,
    SEVEN,
    SYMBOLS,
    THREE_OF_A_KIND,
    TWO_CHERRIES,
    WILD,
    BonusMeter,
    Kind,
    SlotMachine,
    WinTier,
    add_bonus,
    bar_fill,
    bonus_bar,
    daily_stake,
    decayed_heat,
    evaluate_line,
    heat_bar,
    line_payout,
    next_daily_streak,
    next_heat,
    paytable_lines,
    pot_share,
    prize_table,
    respin_odds,
    respin_price,
    spin_at,
    stop_probability,
    win_tier,
)

ALL_STOPS = list(itertools.product(*(range(len(strip)) for strip in REEL_STRIPS)))


def find_stops(predicate) -> tuple[int, int, int]:  # noqa: ANN001
    """Primera parada de los rodillos cuya tirada cumple `predicate`."""
    for stops in ALL_STOPS:
        if predicate(spin_at(stops)):
            return stops
    raise AssertionError("Ninguna parada cumple la condición")


# -- Líneas ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "kind", "symbol", "halves"),
    [
        ((WILD, WILD, WILD), Kind.JACKPOT, WILD, 0),
        ((SEVEN, SEVEN, SEVEN), Kind.THREE, SEVEN, 400),
        ((SEVEN, WILD, SEVEN), Kind.THREE, SEVEN, 400),
        ((WILD, WILD, BELL), Kind.THREE, BELL, 40),
        ((DIAMOND, DIAMOND, DIAMOND), Kind.THREE, DIAMOND, 120),
        ((GRAPE, GRAPE, GRAPE), Kind.THREE, GRAPE, 20),
        ((LEMON, LEMON, LEMON), Kind.THREE, LEMON, 8),
        ((CHERRY, CHERRY, CHERRY), Kind.THREE, CHERRY, 8),
        ((CHERRY, CHERRY, BELL), Kind.TWO_CHERRIES, None, 4),
        ((WILD, CHERRY, BELL), Kind.TWO_CHERRIES, None, 4),
        ((CHERRY, BELL, BELL), Kind.CHERRY, None, 1),
        ((BELL, CHERRY, CHERRY), Kind.NONE, None, 0),
        ((SEVEN, SEVEN, BELL), Kind.NONE, None, 0),
    ],
)
def test_valor_de_cada_linea(line, kind, symbol, halves) -> None:  # noqa: ANN001
    assert evaluate_line(line) == (kind, symbol, halves)


def test_el_ticket_rompe_los_trios_aunque_haya_comodines() -> None:
    assert evaluate_line((WILD, WILD, SCATTER))[0] == Kind.TWO_CHERRIES
    assert evaluate_line((SEVEN, SEVEN, SCATTER))[0] == Kind.NONE


# -- Rodillos -------------------------------------------------------------------------


def test_las_tiras_solo_usan_simbolos_conocidos() -> None:
    for strip in REEL_STRIPS:
        assert set(strip) <= set(SYMBOLS)


def test_nunca_se_ven_dos_tickets_en_el_mismo_rodillo() -> None:
    """Si no, contar "un 🎟️ por rodillo" no sería lo mismo que contar 🎟️."""
    for strip in REEL_STRIPS:
        for index, symbol in enumerate(strip):
            if symbol == SCATTER:
                for offset in (1, 2):
                    assert strip[(index + offset) % len(strip)] != SCATTER


def test_la_cuadricula_sale_de_la_tira_alrededor_de_la_parada() -> None:
    spin = spin_at((0, 0, 0))
    for reel, strip in enumerate(REEL_STRIPS):
        assert spin.grid[0][reel] == strip[-1]
        assert spin.grid[1][reel] == strip[0]
        assert spin.grid[2][reel] == strip[1]


def test_la_maquina_elige_paradas_dentro_de_cada_tira() -> None:
    machine = SlotMachine(random.Random(1).randrange)
    for _ in range(200):
        stops = machine.spin().stops
        assert all(0 <= s < len(strip) for s, strip in zip(stops, REEL_STRIPS, strict=True))


def test_cada_casilla_tiene_su_peso_en_el_rodillo_virtual() -> None:
    assert [len(w) for w in REEL_WEIGHTS] == [len(s) for s in REEL_STRIPS]
    assert all(weight >= 1 for weights in REEL_WEIGHTS for weight in weights)


def test_el_azar_reparte_las_paradas_segun_los_pesos() -> None:
    """El billete n cae en la casilla cuyo tramo acumulado lo contiene."""
    weights = REEL_WEIGHTS[0]
    tickets = iter([0, weights[0] - 1, weights[0], 0, 0])
    machine = SlotMachine(lambda _n: next(tickets))
    assert machine.spin().stops[0] == 0
    tickets = iter([weights[0], 0, 0])
    assert SlotMachine(lambda _n: next(tickets)).spin().stops[0] == 1


def test_el_siete_del_tercer_rodillo_roza_la_linea_mas_que_entra() -> None:
    """El truco del rodillo virtual: las casillas vecinas del 7️⃣ pesan más que la suya."""
    strip = REEL_STRIPS[2]
    seven = strip.index(SEVEN)
    neighbours = stop_probability(2, seven - 1) + stop_probability(2, (seven + 1) % len(strip))
    assert neighbours > 5 * stop_probability(2, seven)


def test_en_el_re_giro_los_tickets_no_cuentan() -> None:
    stops = find_stops(lambda s: s.triggers_free_spins)
    assert not spin_at(stops, count_scatters=False).triggers_free_spins


def test_el_jackpot_tiene_su_parada() -> None:
    spin = spin_at(find_stops(lambda s: s.is_jackpot))
    assert spin.line == (WILD, WILD, WILD)
    assert spin.anticipation


def test_casi_premio_con_el_que_faltaba_justo_encima_o_debajo() -> None:
    spin = spin_at(
        find_stops(
            lambda s: (
                s.line[0] == SEVEN
                and s.line[1] == SEVEN
                and s.line[2] not in (SEVEN, WILD)
                and SEVEN in (s.grid[0][2], s.grid[2][2])
            )
        )
    )
    assert spin.near_miss
    assert spin.anticipation
    assert spin.teaser in (SEVEN, WILD)


def test_quedarse_a_una_cereza_no_es_casi_premio() -> None:
    """Solo cuenta quedarse a uno de un premio de ×`NEAR_MISS_MIN_TIMES` o más."""
    spin = spin_at(
        find_stops(
            lambda s: (
                s.line[:2] == (CHERRY, CHERRY)
                and s.line[2] != CHERRY
                and (s.grid[0][2], s.grid[2][2]) == (CHERRY, CHERRY)
            )
        )
    )
    assert spin.teaser is None
    assert THREE_OF_A_KIND[CHERRY] < NEAR_MISS_MIN_TIMES


def test_un_premio_gordo_en_la_linea_no_es_casi_premio() -> None:
    assert not spin_at(find_stops(lambda s: s.is_jackpot)).near_miss


def test_la_cuadricula_en_emojis_marca_la_linea() -> None:
    text = spin_at((0, 0, 0)).emoji_grid()
    rows = text.splitlines()
    assert len(rows) == 3
    assert rows[1].startswith("▶️") and rows[1].endswith("◀️")


# -- Retorno de la máquina ------------------------------------------------------------


def probability(stops: tuple[int, int, int]) -> float:
    return (
        stop_probability(0, stops[0])
        * stop_probability(1, stops[1])
        * stop_probability(2, stops[2])
    )


@pytest.fixture(scope="module")
def exact_stats() -> dict[str, float]:
    stats = dict.fromkeys(
        ("line", "hit", "ldw", "jackpot", "free", "near", "respin", "big", "mega", "epic"), 0.0
    )
    for stops in ALL_STOPS:
        s, p = spin_at(stops), probability(stops)
        stats["line"] += p * s.pay_halves / 2
        stats["hit"] += p * (s.pay_halves > 0)
        stats["ldw"] += p * (0 < s.pay_halves < 2)
        stats["jackpot"] += p * s.is_jackpot
        stats["free"] += p * s.triggers_free_spins
        stats["near"] += p * s.near_miss
        stats["respin"] += p * (s.teaser is not None)
        stats["big"] += p * (s.pay_halves >= 2 * BIG_WIN)
        stats["mega"] += p * (s.pay_halves >= 2 * MEGA_WIN)
        stats["epic"] += p * (s.pay_halves >= 2 * EPIC_WIN or s.is_jackpot)
    return stats


def test_las_cifras_de_la_documentacion_son_las_reales(exact_stats) -> None:  # noqa: ANN001
    stats = exact_stats
    assert stats["line"] == pytest.approx(0.81, abs=0.005)
    assert stats["hit"] == pytest.approx(0.32, abs=0.01)
    # Casi dos de cada tres premios devuelven menos de lo apostado.
    assert stats["ldw"] / stats["hit"] == pytest.approx(0.635, abs=0.03)
    assert 1 / stats["jackpot"] == pytest.approx(21_000, rel=0.03)
    assert 1 / stats["free"] == pytest.approx(122, rel=0.02)
    assert stats["near"] == pytest.approx(0.21, abs=0.01)
    assert stats["respin"] == pytest.approx(0.20, abs=0.01)
    assert 1 / stats["epic"] == pytest.approx(2_200, rel=0.1)
    assert 1 / stats["mega"] == pytest.approx(450, rel=0.1)
    assert 1 / stats["big"] == pytest.approx(20, rel=0.1)


@pytest.mark.lenta
def test_el_retorno_total_es_el_de_un_casino_de_verdad(monkeypatch: pytest.MonkeyPatch) -> None:
    """Línea + giros gratis + calor + barra de bonus ≈ 96,5 %; con el bote, ≈ 99,5 %.

    Se simula con apuesta 100 para que el medio premio no pierda decimales.

    No se pueden recortar las 600.000 tiradas: con 24 semillas distintas el retorno
    queda entre 0,954 y 0,969, cerca del suelo de la horquilla (0,95 a 0,98), y con 150.000
    tiradas se salía de ella (0,945 a 0,977).

    Lo que sí se acelera es `spin_at`, que es lo más caro y devuelve siempre lo mismo
    para las mismas paradas (hay ~29.000 posibles): se guarda cada resultado (`Spin`
    es inmutable) y la simulación sale idéntica, tirada a tirada.
    """
    monkeypatch.setattr(slots_module, "spin_at", functools.cache(slots_module.spin_at))
    rng = random.Random(2026)
    machine = SlotMachine(rng.randrange)
    stake, paid, returned, heat, free = 100, 0, 0, 0, 0
    meter = BonusMeter()
    for _ in range(600_000):
        is_free = free > 0
        if is_free:
            free -= 1
        else:
            paid += stake
        spin = machine.spin()
        hot = heat >= HEAT_MAX
        payout = line_payout(spin, stake, hot=hot)
        returned += payout
        heat = next_heat(heat, paid=payout > 0, was_hot=hot)
        if spin.triggers_free_spins:
            free += FREE_SPINS
        if not is_free:
            meter, award = add_bonus(meter, spin, stake, rng.randrange)
            free += BONUS_FREE_SPINS if award else 0
    house = returned / paid
    assert 0.92 < house < 0.95
    assert 0.98 < house + POT_SHARE_PERCENT / 100 < 1.0


# -- Re-giro ------------------------------------------------------------------------------


def test_el_re_giro_cambia_solo_el_tercer_rodillo() -> None:
    spin = spin_at(find_stops(lambda s: s.teaser is not None))
    again = SlotMachine(random.Random(3).randrange).respin(spin)
    assert again.stops[:2] == spin.stops[:2]
    assert not again.triggers_free_spins


def test_el_re_giro_nunca_sale_a_cuenta(exact_stats) -> None:  # noqa: ANN001
    """Precio ≥ valor esperado / `RESPIN_RTP` en cada casi-premio, también con el bote lleno."""
    for stops in ALL_STOPS:
        spin = spin_at(stops)
        if spin.teaser is None:
            continue
        halves, jackpot = respin_odds(spin)
        for pot in (0, 5_000, 50_000):
            expected = 100 * halves / 2 + jackpot * pot
            assert respin_price(spin, 100, pot) * RESPIN_RTP >= expected - 1e-9


def test_con_dos_comodines_el_re_giro_paga_el_bote_en_el_precio() -> None:
    spin = spin_at(find_stops(lambda s: s.line[:2] == (WILD, WILD) and s.teaser == WILD))
    assert respin_price(spin, 100, 50_000) > respin_price(spin, 100, 0)


# -- Celebraciones, calor y giro diario --------------------------------------------------


@pytest.mark.parametrize(
    ("won", "tier"),
    [
        (0, None),
        (BIG_WIN * 100 - 1, None),
        (BIG_WIN * 100, WinTier.BIG),
        (MEGA_WIN * 100, WinTier.MEGA),
        (EPIC_WIN * 100, WinTier.EPIC),
    ],
)
def test_nivel_de_celebracion(won: int, tier: str | None) -> None:
    assert win_tier(won, 100) == tier
    assert win_tier(won, 0) is None


def test_el_calor_se_enfria_un_punto_cada_rato_sin_jugar() -> None:
    assert decayed_heat(4, HEAT_DECAY_SECONDS - 1) == 4
    assert decayed_heat(4, HEAT_DECAY_SECONDS) == 3
    assert decayed_heat(4, 10 * HEAT_DECAY_SECONDS) == 0
    assert decayed_heat(4, -50) == 4


def test_la_racha_del_giro_diario_sigue_solo_si_fue_ayer() -> None:
    today = date(2026, 10, 9)
    assert next_daily_streak(None, today, 0) == 1
    assert next_daily_streak(date(2026, 10, 8), today, 3) == 4
    assert next_daily_streak(date(2026, 10, 7), today, 3) == 1


def test_el_giro_diario_sube_con_la_racha_hasta_el_tope() -> None:
    assert daily_stake(1) == DAILY_STAKE
    assert daily_stake(3) == 3 * DAILY_STAKE
    assert daily_stake(99) == DAILY_STREAK_MAX * DAILY_STAKE


def test_el_cartel_de_premios_a_la_apuesta_actual() -> None:
    rows = dict(prize_table(200))
    assert rows[SYMBOLS[WILD].emoji * 3] is None
    assert rows[SYMBOLS[SEVEN].emoji * 3] == 200 * THREE_OF_A_KIND[SEVEN]
    assert rows[f"{SYMBOLS[CHERRY].emoji}{SYMBOLS[CHERRY].emoji}❔"] == 200 * TWO_CHERRIES
    assert rows[f"{SYMBOLS[CHERRY].emoji}❔❔"] == 100


# -- Dinero y calor -------------------------------------------------------------------


def test_el_medio_premio_y_la_maquina_caliente() -> None:
    cherry = spin_at(find_stops(lambda s: s.kind == Kind.CHERRY))
    assert line_payout(cherry, 100) == 50
    assert line_payout(cherry, 100, hot=True) == 100
    assert line_payout(cherry, 1) == 0


def test_parte_del_bote() -> None:
    assert pot_share(100) == POT_SHARE_PERCENT
    assert pot_share(100 // POT_SHARE_PERCENT - 1) == 0


def test_el_calor_sube_con_cada_premio_y_la_tirada_caliente_lo_gasta() -> None:
    heat = 0
    for _ in range(HEAT_MAX + 2):
        heat = next_heat(heat, paid=True, was_hot=False)
    assert heat == HEAT_MAX
    assert next_heat(heat, paid=False, was_hot=False) == HEAT_MAX
    assert next_heat(heat, paid=True, was_hot=True) == 0


def test_la_barra_de_calor_usa_formas() -> None:
    assert heat_bar(0) == "▫️" * HEAT_CELLS
    assert heat_bar(2) == "🔥" * 6 + "▫️" * 4
    assert heat_bar(99) == "🔥" * HEAT_CELLS


def test_la_barra_de_calor_corre_al_principio_y_se_frena_al_final() -> None:
    lit = [heat_bar(h).count("🔥") for h in range(HEAT_MAX + 1)]
    steps = [b - a for a, b in itertools.pairwise(lit)]
    assert lit[0] == 0 and lit[-1] == HEAT_CELLS
    assert all(step > 0 for step in steps), "cada tirada con premio se tiene que ver"
    assert steps == sorted(steps, reverse=True), "los primeros pasos llenan más"
    assert lit[HEAT_MAX - 1] < HEAT_CELLS, "solo se ve llena cuando está caliente"


def test_la_tabla_de_premios_menciona_el_bote_y_los_giros() -> None:
    text = "\n".join(paytable_lines())
    assert "BOTE" in text
    assert f"{FREE_SPINS} giros gratis" in text


# -- Barra de bonus ------------------------------------------------------------------------


def test_la_barra_de_bonus_sube_mas_con_un_casi_premio() -> None:
    near = spin_at(find_stops(lambda s: s.near_miss))
    loss = spin_at(find_stops(lambda s: not s.near_miss and not s.pay_halves))
    zero = lambda _n: 0  # noqa: E731
    assert add_bonus(BonusMeter(), loss, 100, zero)[0].points == 0
    assert add_bonus(BonusMeter(), near, 100, zero)[0].points == BONUS_NEAR_MISS


def test_cerca_del_final_la_barra_sube_a_cuentagotas() -> None:
    loss = spin_at(find_stops(lambda s: not s.near_miss and not s.pay_halves))
    two = lambda _n: 2  # noqa: E731
    meter = BonusMeter(points=BONUS_SLOW_FROM)
    assert add_bonus(meter, loss, 100, two)[0].points == BONUS_SLOW_FROM
    assert add_bonus(BonusMeter(points=10), loss, 100, two)[0].points == 12


def test_la_barra_llena_da_giros_a_la_apuesta_media_y_vuelve_a_cero() -> None:
    near = spin_at(find_stops(lambda s: s.near_miss))
    meter = BonusMeter(points=BONUS_MAX - 1, stake_sum=900, spins=9)
    meter, award = add_bonus(meter, near, 10_000, lambda _n: 0)
    assert meter == BonusMeter()
    assert award == (900 + 10_000) // 10


def test_la_barra_de_bonus_dice_el_porcentaje() -> None:
    assert bonus_bar(0) == "▱▱▱▱▱▱▱▱▱▱ 0 %"
    assert bonus_bar(50) == "▰▰▰▰▰▰▰▱▱▱ 74 %"
    assert bonus_bar(500).endswith("100 %")


def test_la_barra_de_bonus_se_ve_mas_llena_de_lo_que_esta_pero_no_llena() -> None:
    shown = [bar_fill(points, BONUS_MAX) for points in range(BONUS_MAX + 1)]
    assert all(b > a for a, b in itertools.pairwise(shown)), "cada punto se nota"
    assert all(f >= p / BONUS_MAX for p, f in enumerate(shown)), "nunca menos que lo real"
    gains = [b - a for a, b in itertools.pairwise(shown)]
    assert gains == sorted(gains, reverse=True), "los primeros puntos llenan más"
    assert bonus_bar(BONUS_MAX - 1) == "▰" * 9 + "▱ 99 %"
    assert bonus_bar(BONUS_MAX) == "▰" * 10 + " 100 %"
