"""Pruebas de los stats de los gallos: probabilidad, cuota, entrenamiento y muerte.

Son reglas puras de `bot.services.gallos`; el cobro, la base de datos y los botones
tienen las suyas en `test_gallos_repository.py` y `test_gallos_cog.py`.
"""

from __future__ import annotations

import math
import random
from datetime import date
from fractions import Fraction

import pytest

from bot.services.gallos import (
    DEATH_CHANCE,
    EDGE_CAP_BASE,
    EDGE_CAP_PER_POWER,
    EDGE_MAX,
    FORM_DECAY_PER_DAY,
    FORM_MAX,
    FORM_PER_FIGHT,
    HOUSE,
    MAX_WIN,
    MIN_WIN,
    PAID_FORM,
    RIVAL_BY_KEY,
    RTP,
    TRAIN_COOLDOWN,
    TRAIN_PER_DAY,
    Finish,
    RoosterRecord,
    Stat,
    TrainError,
    after_fight,
    after_train,
    check_training,
    death_chance,
    default_name,
    dies,
    difficulty,
    duel_chance,
    edge_cap,
    form_now,
    house_chance,
    house_edge,
    house_odds,
    house_payout,
    house_rtp,
    reborn,
    stat_bar,
    train_gain,
    train_price,
    trains_today,
    win_probability,
)

# -- Probabilidad y cuota -----------------------------------------------------------------


def test_sin_entrenar_cada_rival_juega_como_antes_de_los_stats() -> None:
    for rival in HOUSE:
        assert house_chance(rival, 0) == rival.chance, rival.key
        assert rival.multiplier_for(0) == rival.multiplier, rival.key
        assert house_payout(1000, rival) == int(1000 * rival.multiplier), rival.key


def test_entre_dos_gallos_iguales_cada_uno_gana_la_mitad() -> None:
    assert duel_chance(0, 0) == duel_chance(70, 70) == Fraction(1, 2)


def test_la_probabilidad_sigue_la_formula_del_logit_con_k_3() -> None:
    p0 = Fraction(3, 10)
    for diff in (-80, -25, 10, 50, 100):
        expected = 1 / (1 + math.exp(-(math.log(0.3 / 0.7) + 3 * diff / 100)))
        assert float(win_probability(p0, diff, 0)) == pytest.approx(expected, abs=1e-4)


def test_entrenar_sube_la_probabilidad_y_nunca_sale_de_3_a_97() -> None:
    for rival in HOUSE:
        chances = [house_chance(rival, power) for power in range(0, 101, 10)]
        assert chances == sorted(chances), rival.key
        assert all(MIN_WIN <= c <= MAX_WIN for c in chances), rival.key
    assert win_probability(Fraction(1, 2), 100, -1000) == MAX_WIN
    assert win_probability(Fraction(1, 2), -1000, 100) == MIN_WIN


def test_en_un_duelo_los_dos_lados_suman_uno_y_gana_mas_el_mas_fuerte() -> None:
    assert duel_chance(60, 20) > Fraction(1, 2) > duel_chance(20, 60)
    assert duel_chance(60, 20) + duel_chance(20, 60) == pytest.approx(1, abs=1e-4)


def test_la_cuota_recalculada_mantiene_el_rtp_con_cualquier_poder() -> None:
    for rival in HOUSE:
        for power in (0, 17, 33.3, 50, 75, 100):
            chance = house_chance(rival, power)
            assert chance * rival.multiplier_for(power) == RTP, (rival.key, power)


def test_entrenar_cambia_el_riesgo_no_el_margen() -> None:
    falcon = next(r for r in HOUSE if r.key == "falcon")
    assert falcon.multiplier_for(100) < falcon.multiplier_for(0)
    # Lo esperado por Y$ apostado es el RTP con el gallo que sea (menos el redondeo al Y$).
    stake = 10_000
    for power in (0, 50, 100):
        expected = float(house_chance(falcon, power)) * house_payout(stake, falcon, power)
        assert expected == pytest.approx(float(RTP) * stake, rel=1e-3)


def test_el_poder_de_los_rivales_crece_con_su_dificultad() -> None:
    powers = [r.power for r in HOUSE]
    assert powers == sorted(powers) and len(set(powers)) == len(powers)
    assert all(0 < p <= 100 for p in powers)


def test_el_poder_es_la_media_de_los_tres_stats() -> None:
    record = RoosterRecord(name="Pepe", strength=60, speed=30, endurance=0)
    assert record.power == 30
    assert record.stat(Stat.STRENGTH) == 60 and record.stat(Stat.ENDURANCE) == 0
    assert RoosterRecord(name="Nuevo").power == 0


# -- Entrenamiento ------------------------------------------------------------------------


def test_precio_del_entrenamiento_150_mas_4_por_nivel_a_la_decena() -> None:
    assert train_price(0) == 150
    assert train_price(40) == 310
    assert train_price(100) == 550
    for level in range(0, 101):
        assert train_price(level) % 10 == 0
        assert abs(train_price(level) - (150 + 4 * level)) <= 5
    prices = [train_price(level) for level in range(0, 101)]
    assert prices == sorted(prices)


def test_cada_entrenamiento_sube_3_2_o_1_segun_el_nivel() -> None:
    assert [train_gain(level) for level in (0, 39, 40, 69, 70, 99)] == [3, 3, 2, 2, 1, 1]
    day = date(2026, 10, 15)
    record = after_train(RoosterRecord(name="Pepe", strength=38), Stat.STRENGTH, now=1, day=day)
    assert record.strength == 41  # 38 + 3: el salto se decide por el nivel de partida
    record = after_train(record, Stat.STRENGTH, now=2, day=day)
    assert record.strength == 43


def test_un_stat_nunca_pasa_de_100_pero_al_maximo_se_entrena_por_la_forma() -> None:
    day = date(2026, 10, 15)
    assert (
        after_train(RoosterRecord(name="Pepe", speed=99), Stat.SPEED, now=0, day=day).speed == 100
    )
    maxed = RoosterRecord(name="Pepe", speed=100)
    check_training(maxed, Stat.SPEED, now=TRAIN_COOLDOWN, day=day)
    trained = after_train(maxed, Stat.SPEED, now=TRAIN_COOLDOWN, day=day)
    assert trained.speed == 100 and trained.form == PAID_FORM


def test_hay_una_hora_de_espera_entre_entrenamientos() -> None:
    day = date(2026, 10, 15)
    record = after_train(RoosterRecord(name="Pepe"), Stat.STRENGTH, now=100_000, day=day)
    with pytest.raises(TrainError, match="descansa"):
        check_training(record, Stat.SPEED, now=100_000 + TRAIN_COOLDOWN - 1, day=day)
    check_training(record, Stat.SPEED, now=100_000 + TRAIN_COOLDOWN, day=day)


def test_como_mucho_seis_entrenamientos_al_dia_y_al_dia_siguiente_se_reinicia() -> None:
    day = date(2026, 10, 15)
    record = RoosterRecord(name="Pepe")
    now = 1_000_000.0
    for _ in range(TRAIN_PER_DAY):
        check_training(record, Stat.STRENGTH, now=now, day=day)
        record = after_train(record, Stat.STRENGTH, now=now, day=day)
        now += TRAIN_COOLDOWN
    assert trains_today(record, day) == TRAIN_PER_DAY
    with pytest.raises(TrainError, match="entrenamientos"):
        check_training(record, Stat.STRENGTH, now=now, day=day)
    tomorrow = date(2026, 10, 16)
    assert trains_today(record, tomorrow) == 0
    check_training(record, Stat.STRENGTH, now=now, day=tomorrow)


# -- Muerte -------------------------------------------------------------------------------


def test_la_probabilidad_de_morir_baja_con_el_aguante_y_con_la_forma() -> None:
    assert DEATH_CHANCE == pytest.approx(0.10)
    # Agotado multiplica por 1,5 y en plena forma por 0,25; el aguante la parte por la mitad.
    assert death_chance(0, 0) == pytest.approx(0.15)
    assert death_chance(100, 0) == pytest.approx(0.075)
    assert death_chance(0, 100) == pytest.approx(0.025)
    assert death_chance(100, 100) == pytest.approx(0.0125)
    assert death_chance(0, 40) == pytest.approx(DEATH_CHANCE)
    chances = [death_chance(50, form) for form in range(0, 101, 10)]
    assert chances == sorted(chances, reverse=True)


class Roll(random.Random):
    """Azar cuyo único `random()` vale `value`."""

    def __init__(self, value: float) -> None:
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


def test_se_muere_si_el_sorteo_cae_por_debajo_de_la_probabilidad() -> None:
    tough = RoosterRecord(name="Roca", endurance=100)
    weak = RoosterRecord(name="Pluma")
    # Con forma 40 el multiplicador de la forma es 1: del 10 % al 5 % por el aguante.
    assert dies(weak, Roll(0.09), 40) and not dies(tough, Roll(0.09), 40)
    assert dies(tough, Roll(0.049), 40) and not dies(tough, Roll(0.05), 40)
    assert dies(tough, Roll(0.07), 0) and not dies(tough, Roll(0.02), 100)


def test_el_gallo_que_sustituye_al_muerto_empieza_a_cero_pero_el_dueno_guarda_su_cupo() -> None:
    day = date(2026, 10, 15)
    dead = RoosterRecord(
        name="Veterano", fights=40, wins=30, kos=9, strength=60, speed=40, endurance=20,
        last_train=5.0, train_day=day, trains_today=3, deaths=1,
    )  # fmt: skip
    new = reborn(dead, 42)
    assert new.name == default_name(42)
    assert (new.fights, new.wins, new.kos, new.streak, new.best_streak) == (0, 0, 0, 0, 0)
    assert (new.strength, new.speed, new.endurance) == (0, 0, 0)
    assert new.deaths == 2
    assert (new.last_train, new.train_day, new.trains_today) == (5.0, day, 3)


def test_barras_de_stat() -> None:
    assert stat_bar(0) == "░" * 10 and stat_bar(100) == "█" * 10
    assert stat_bar(55) == "█" * 6 + "░" * 4


def test_contra_la_casa_la_cuota_nunca_baja_de_1_01() -> None:
    from bot.services.gallos import HOUSE, MAX_HOUSE_WIN, RTP, house_chance

    for rival in HOUSE:
        for power in (0, 50, 100):
            p = house_chance(rival, power)
            assert p <= MAX_HOUSE_WIN
            assert RTP / p >= Fraction(101, 100) - Fraction(1, 1000)


# -- Forma y ventaja ----------------------------------------------------------------------


def test_entrenar_da_forma_y_la_forma_baja_con_el_tiempo_sin_pasar_de_cero() -> None:
    day = date(2026, 10, 15)
    record = after_train(RoosterRecord(name="Pepe"), Stat.STRENGTH, now=0, day=day)
    assert form_now(record, 0) == PAID_FORM
    assert form_now(record, 86_400 / 2) == pytest.approx(PAID_FORM - FORM_DECAY_PER_DAY / 2)
    assert form_now(record, 86_400 * 30) == 0
    # La siguiente sesión suma a lo que queda, no a lo guardado.
    later = after_train(record, Stat.SPEED, now=86_400 / 2, day=day)
    assert later.form == pytest.approx(2 * PAID_FORM - FORM_DECAY_PER_DAY / 2)


def test_la_forma_no_pasa_del_maximo() -> None:
    day = date(2026, 10, 15)
    record = RoosterRecord(name="Pepe", form=FORM_MAX - 1, form_at=0)
    assert after_train(record, Stat.STRENGTH, now=0, day=day).form == FORM_MAX


def test_pelear_contra_la_casa_gasta_forma_y_el_duelo_no() -> None:
    day = date(2026, 10, 15)
    record = RoosterRecord(name="Pepe", form=50, form_at=0)
    house = after_fight(record, won=True, finish=Finish.KO, day=day, now=0)
    assert house.form == 50 - FORM_PER_FIGHT and house.fights == 1
    duel = after_fight(record, won=True, finish=Finish.KO, day=day)
    assert duel.form == 50 and duel.fights == 1
    tired = RoosterRecord(name="Pepe", form=3, form_at=0)
    assert after_fight(tired, won=False, finish=Finish.POINTS, day=day, now=0).form == 0


def test_sin_forma_la_casa_paga_su_rtp_de_siempre() -> None:
    for rival in HOUSE:
        for power in (0, 50, 100):
            assert house_rtp(rival, power, 0, 1_000) == RTP
            assert house_odds(rival, power, 0, 1_000) == (
                house_chance(rival, power),
                rival.multiplier_for(power),
            )


def test_la_ventaja_crece_con_la_forma_y_con_lo_duro_del_rival() -> None:
    marte, temu = RIVAL_BY_KEY["marte"], RIVAL_BY_KEY["temu"]
    assert difficulty(marte) == 1
    assert house_edge(marte, FORM_MAX) == EDGE_MAX
    assert house_edge(marte, 50) == EDGE_MAX / 2
    edges = [house_edge(rival, 80) for rival in HOUSE]
    assert edges == sorted(edges)
    assert house_edge(temu, FORM_MAX) < house_edge(marte, FORM_MAX) / 5


def test_la_ventaja_solo_cubre_la_apuesta_hasta_el_tope_del_poder() -> None:
    marte = RIVAL_BY_KEY["marte"]
    assert edge_cap(0) == EDGE_CAP_BASE
    assert edge_cap(100) == EDGE_CAP_BASE + 100 * EDGE_CAP_PER_POWER
    cap = edge_cap(40)
    edge = house_edge(marte, FORM_MAX)
    assert house_rtp(marte, 40, FORM_MAX, cap) == RTP + edge
    # El doble del tope: la ventaja en Y$ es la misma, así que el RTP de la apuesta baja.
    assert house_rtp(marte, 40, FORM_MAX, 2 * cap) == RTP + edge / 2
    extra = [(house_rtp(marte, 40, FORM_MAX, s) - RTP) * s for s in (cap, 2 * cap, 10 * cap)]
    assert extra[0] == extra[1] == extra[2]


def test_la_cuota_con_forma_devuelve_lo_esperado() -> None:
    for rival in HOUSE:
        for power in (0, 60, 100):
            for form in (30, 100):
                stake = 5_000
                chance, multiplier = house_odds(rival, power, form, stake)
                rtp = house_rtp(rival, power, form, stake)
                assert chance * multiplier == rtp, (rival.key, power, form)
                assert multiplier >= Fraction(101, 100) - Fraction(1, 1000)
                expected = float(chance) * house_payout(stake, rival, power, form)
                assert expected == pytest.approx(float(rtp) * stake, rel=1e-3)


# -- A muerte y el pique ------------------------------------------------------------------


def test_a_muerte_paga_mas_y_perder_por_ko_mata_seguro() -> None:
    from bot.services.gallos import DEADLY_EDGE

    marte = RIVAL_BY_KEY["marte"]
    stake = edge_cap(50)
    plain = house_rtp(marte, 50, 60, stake)
    deadly = house_rtp(marte, 50, 60, stake, deadly=True)
    assert deadly - plain == DEADLY_EDGE
    assert dies(RoosterRecord(name="Roca", endurance=100), Roll(0.99), 100, deadly=True)


def test_ganar_tres_seguidas_al_mismo_rival_lo_pica_y_perder_lo_calma() -> None:
    from bot.services.gallos import GRUDGE_EDGE, GRUDGE_WINS, grudge_against

    day = date(2026, 11, 6)
    record = RoosterRecord(name="Pepe")
    for _ in range(GRUDGE_WINS):
        assert not grudge_against(record, "falcon")
        record = after_fight(record, won=True, finish=Finish.KO, day=day, rival="falcon")
    assert grudge_against(record, "falcon") and not grudge_against(record, "marte")
    # Ganarle a otro empieza otra cuenta; perder la corta.
    other = after_fight(record, won=True, finish=Finish.KO, day=day, rival="marte")
    assert (other.grudge_rival, other.grudge_wins) == ("marte", 1)
    lost = after_fight(record, won=False, finish=Finish.KO, day=day, rival="falcon")
    assert not grudge_against(lost, "falcon")
    falcon = RIVAL_BY_KEY["falcon"]
    stake = edge_cap(0)
    gain = house_rtp(falcon, 0, 80, stake, grudge=True) - RTP
    assert gain == house_edge(falcon, 80) * GRUDGE_EDGE
    # Contra un rival picado, morir es más probable.
    tough = RoosterRecord(name="Roca")
    assert dies(tough, Roll(death_chance(0, 40) * 1.2), 40, grudge=True)
    assert not dies(tough, Roll(death_chance(0, 40) * 1.2), 40)


# -- Edad, retiro y cría ------------------------------------------------------------------


def test_desde_las_150_peleas_pierde_un_punto_de_cada_stat_cada_10() -> None:
    from bot.services.gallos import AGE_FIGHTS, AGE_STEP, aged

    day = date(2026, 11, 6)
    record = RoosterRecord(
        name="Viejo", fights=AGE_FIGHTS + AGE_STEP - 1, strength=50, speed=0, endurance=30
    )
    assert aged(record)
    older = after_fight(record, won=True, finish=Finish.KO, day=day)
    assert (older.strength, older.speed, older.endurance) == (49, 0, 29)
    same = after_fight(older, won=True, finish=Finish.KO, day=day)
    assert same.strength == 49
    young = RoosterRecord(name="Joven", fights=AGE_STEP - 1, strength=50)
    assert after_fight(young, won=True, finish=Finish.KO, day=day).strength == 50


def test_la_cria_de_un_retirado_hereda_parte_de_sus_stats_y_una_generacion() -> None:
    from bot.services.gallos import INHERIT_SHARE, RETIRE_MIN_FIGHTS, heir, retire_error

    assert retire_error(RoosterRecord(name="Pollo", fights=RETIRE_MIN_FIGHTS - 1))
    old = RoosterRecord(
        name="Campeón", fights=RETIRE_MIN_FIGHTS, wins=15, strength=100, speed=60,
        endurance=33, deaths=2, generation=1, form=80,
    )  # fmt: skip
    assert retire_error(old) is None
    chick = heir(old, 42)
    assert chick.name == default_name(42) and chick.fights == 0 and chick.form == 0
    assert chick.strength == int(100 * INHERIT_SHARE) and chick.speed == int(60 * INHERIT_SHARE)
    assert (chick.deaths, chick.generation) == (2, 2)
    # Morir corta el linaje.
    assert reborn(old, 42).generation == 0
