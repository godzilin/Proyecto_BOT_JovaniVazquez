"""Pruebas de `gallos_stats` y `gallos_bet_stats`: lo que cuenta cada pelea para los logros."""

from __future__ import annotations

from datetime import date, datetime

from bot.services.achievements import (
    GALLOS_BEAT_PREFIX,
    GALLOS_SKIN_PREFIX,
    GALLOS_TRAINED_BEAT_PREFIX,
    GALLOS_TRAINED_POWER,
    gallos_bet_stats,
    gallos_stats,
    gallos_train_stats,
)
from bot.services.gallos import Corner, Exchange, Fight, Finish, RoosterRecord
from bot.services.levels import TIMEZONE

RED, BLUE = Corner.RED, Corner.BLUE
WHEN = datetime(2026, 10, 12, 3, 0, tzinfo=TIMEZONE)  # Hispanidad, de madrugada
RECORD = RoosterRecord(name="Pepe", fights=3, wins=3, streak=3, best_streak=3, day_streak=2,
                       last_day=date(2026, 10, 12))  # fmt: skip


def stats(fight: Fight, **kwargs: object) -> dict[str, int]:
    args: dict[str, object] = {
        "fight": fight,
        "corner": RED,
        "rival": "temu",
        "chance": 0.85,
        "stake": 100,
        "net": 12,
        "record": RECORD,
        "skin": "serie",
        "paid_skin": False,
        "crowd_pool": 0,
        "rake": 0,
        "when": WHEN,
    }
    args.update(kwargs)
    delta = gallos_stats(**args)  # type: ignore[arg-type]
    return {**delta.add, **delta.peak}


def test_ko_relampago_sin_un_rasguno_contra_la_casa() -> None:
    fight = Fight(RED, Finish.KO, (
        Exchange(1, BLUE, "picotazo", 0, False, True, (100, 100)),
        Exchange(1, RED, "patada", 64, True, False, (100, 36)),
        Exchange(1, RED, "espolonazo", 36, False, False, (100, 0)),
    ))  # fmt: skip
    got = stats(fight)
    assert got["gallos_fights"] == got["gallos_wins"] == got["gallos_kos"] == 1
    assert got["gallos_flawless"] == got["gallos_blitz"] == 1
    assert got["gallos_crits"] == 1 and got["gallos_dodges"] == 1
    assert got["gallos_damage_max"] == 64
    assert got[f"{GALLOS_BEAT_PREFIX}temu"] == 1
    assert got[f"{GALLOS_SKIN_PREFIX}serie"] == 1
    assert got["gallos_streak_max"] == 3 and got["gallos_days_max"] == 2
    assert got["gallos_hispanidad"] == got["gallos_night"] == 1
    assert got["gallos_win_max"] == 12
    assert "gallos_duels" not in got and "gallos_losses" not in got


def test_perder_un_duelo_huyendo_siendo_favorito() -> None:
    fight = Fight(BLUE, Finish.FLED, (Exchange(2, BLUE, "picotazo", 80, False, False, (20, 100)),))
    got = stats(fight, rival=None, chance=0.85, net=-100, stake=10_000, crowd_pool=5_000, rake=25)
    assert got["gallos_losses"] == got["gallos_fled"] == got["gallos_favourite_loss"] == 1
    assert got["gallos_duels"] == got["gallos_duel_big"] == 1
    assert got["gallos_crowd_max"] == 5_000 and got["gallos_rake"] == 25
    assert "gallos_duel_wins" not in got and "gallos_temu_loss" not in got


def test_perder_contra_temu_es_secreto_y_cuenta_aparte() -> None:
    fight = Fight(BLUE, Finish.POINTS, (Exchange(3, BLUE, "aletazo", 10, False, False, (90, 100)),))
    got = stats(fight, net=-100)
    assert got["gallos_temu_loss"] == 1 and f"{GALLOS_BEAT_PREFIX}temu" not in got


def win_fight() -> Fight:
    return Fight(RED, Finish.POINTS, (Exchange(3, RED, "aletazo", 10, False, False, (100, 90)),))


def test_ganar_con_un_gallo_entrenado_cuenta_para_la_gira_entrenada() -> None:
    trained = stats(win_fight(), power=GALLOS_TRAINED_POWER)
    assert trained[f"{GALLOS_TRAINED_BEAT_PREFIX}temu"] == 1
    assert trained[f"{GALLOS_BEAT_PREFIX}temu"] == 1
    flat = stats(win_fight(), power=GALLOS_TRAINED_POWER - 1)
    assert f"{GALLOS_TRAINED_BEAT_PREFIX}temu" not in flat
    assert f"{GALLOS_TRAINED_BEAT_PREFIX}temu" not in stats(win_fight(), rival=None, power=90)


def test_ganar_con_menos_del_20_por_ciento_es_una_sorpresa_solo_contra_la_casa() -> None:
    assert stats(win_fight(), rival="marte", chance=0.19)["gallos_upset"] == 1
    assert "gallos_upset" not in stats(win_fight(), rival="marte", chance=0.20)
    assert "gallos_upset" not in stats(win_fight(), rival=None, chance=0.1)
    loss = Fight(BLUE, Finish.POINTS, (Exchange(3, BLUE, "aletazo", 10, False, False, (90, 100)),))
    assert "gallos_upset" not in stats(loss, rival="marte", chance=0.1)


def test_la_muerte_cuenta_veterano_y_debut_y_el_duelo_cuenta_la_muerte_ajena() -> None:
    loss = Fight(BLUE, Finish.KO, (Exchange(1, BLUE, "patada", 100, True, False, (0, 100)),))
    veteran = RoosterRecord(name="Roca", fights=80, wins=50)
    got = stats(loss, died=veteran, record=veteran)
    assert got["gallos_deaths"] == got["gallos_death_veteran"] == 1
    assert "gallos_death_debut" not in got
    debut = RoosterRecord(name="Novato", fights=1)
    got = stats(loss, died=debut, record=debut)
    assert got["gallos_deaths"] == got["gallos_death_debut"] == 1
    assert "gallos_death_veteran" not in got
    assert "gallos_deaths" not in stats(loss)
    won = stats(Fight(RED, Finish.KO, loss.exchanges), rival=None, killed=True)
    assert won["gallos_kills"] == 1 and "gallos_deaths" not in won


def test_el_ave_fenix_es_ganar_la_primera_pelea_del_gallo_nuevo_tras_una_muerte() -> None:
    fresh = RoosterRecord(name="Nuevo", fights=1, wins=1, deaths=1)
    assert stats(win_fight(), record=fresh)["gallos_phoenix"] == 1
    first_ever = RoosterRecord(name="Nuevo", fights=1, wins=1)
    assert "gallos_phoenix" not in stats(win_fight(), record=first_ever)
    second = RoosterRecord(name="Nuevo", fights=2, wins=2, deaths=1)
    assert "gallos_phoenix" not in stats(win_fight(), record=second)


def test_entrenar_cuenta_gasto_maximos_y_cupo_del_dia() -> None:
    record = RoosterRecord(name="Roca", strength=60, speed=30, endurance=0)
    delta = gallos_train_stats(record=record, paid=1_284, today=6, when=WHEN, form=48)
    assert delta.add == {"gallos_trains": 1, "gallos_train_spent": 1_284, "gallos_train_dawn": 1}
    assert delta.peak == {
        "gallos_strength_max": 60,
        "gallos_speed_max": 30,
        "gallos_endurance_max": 0,
        "gallos_power_max": 30,
        "gallos_trains_day_max": 6,
        "gallos_form_max": 48,
    }
    noon = WHEN.replace(hour=12)
    assert (
        "gallos_train_dawn" not in gallos_train_stats(record=record, paid=1, today=1, when=noon).add
    )


def test_apuesta_de_grada() -> None:
    won = gallos_bet_stats(won=True, refunded=False, odds_cents=320, rake=5, when=WHEN)
    assert won.add == {"gallos_bets": 1, "gallos_bets_won": 1, "gallos_rake": 5, "gallos_night": 1}
    assert won.peak == {"gallos_bet_odds_max": 320}
    refund = gallos_bet_stats(won=True, refunded=True, odds_cents=0, rake=0, when=WHEN)
    assert refund.add["gallos_bet_refund"] == 1 and "gallos_bets_won" not in refund.add


def lose_fight() -> Fight:
    return Fight(BLUE, Finish.KO, (Exchange(3, BLUE, "patada", 100, True, False, (0, 90)),))


def test_contra_la_casa_cuenta_la_forma_y_lo_que_saca_la_ventaja() -> None:
    delta = stats(win_fight(), rival="marte", form=85, edge_gain=320, full_cap=True)
    for stat, value in (
        ("gallos_form_max", 85),
        ("gallos_edge_wins", 1),
        ("gallos_edge_gain", 320),
        ("gallos_fit_marte", 1),
        ("gallos_full_cap", 1),
    ):
        assert delta[stat] == value, stat
    assert "gallos_tired_fights" not in delta
    tired = stats(win_fight(), rival="falcon", form=0)
    assert tired["gallos_tired_fights"] == 1 and "gallos_edge_wins" not in tired
    assert "gallos_form_max" not in stats(win_fight(), rival=None)


def test_morir_agotado_o_en_lo_mejor_se_cuentan_aparte() -> None:
    dead = RoosterRecord(name="Roca", fights=5)
    tired = stats(lose_fight(), rival="marte", form=5, died=dead)
    assert tired["gallos_tired_death"] == 1 and "gallos_fit_death" not in tired
    fit = stats(lose_fight(), rival="marte", form=95, died=dead)
    assert fit["gallos_fit_death"] == 1 and "gallos_tired_death" not in fit
