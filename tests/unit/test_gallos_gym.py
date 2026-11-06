"""Pruebas de los minijuegos de entrenamiento de los gallos (`bot.services.gallos_gym`)."""

from __future__ import annotations

import random
import re
from datetime import date

import pytest

from bot.services import gallos_gym as gym
from bot.services.achievements import gallos_drill_stats, with_derived
from bot.services.gallos import FORM_PER_TRAIN, PAID_FORM, RoosterRecord, Stat

ALL_KINDS = [k for pair in gym.KINDS.values() for k in pair]
KEY_RE = re.compile(r"^[a-z0-9_]+$")


class Script(random.Random):
    """Azar de guion: `random()` saca los valores dados (luego el último) y `choice` el primero."""

    def __init__(self, *values: float) -> None:
        super().__init__(0)
        self.values = list(values) or [0.0]

    def random(self) -> float:
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]

    def choice(self, seq):
        return seq[0]


def play(game: gym.GymGame, *keys: str, now: float = 1.0) -> None:
    for i, key in enumerate(keys):
        game.press(key, now * (i + 1))


def check_screen(game: gym.GymGame) -> None:
    assert len(game.text()) <= 1500
    buttons = game.buttons()
    if game.done:
        assert buttons == []
        return
    assert buttons
    keys = [b.key for b in buttons]
    assert len(set(keys)) == len(keys)
    rows: dict[int, int] = {}
    for b in buttons:
        assert KEY_RE.match(b.key)
        assert len(b.label) <= 40
        assert b.style in {"primary", "secondary", "success", "danger"}
        assert 0 <= b.row < 4
        rows[b.row] = rows.get(b.row, 0) + 1
    assert max(rows.values()) <= 5


def run(kind: str, policy: str, seed: int, texts: list[str] | None = None) -> float:
    game = gym.start(kind, random.Random(seed), 0.0)
    pr = random.Random(seed + 1000)
    now = 0.0
    while not game.done:
        check_screen(game)
        if texts is not None:
            texts.append(game.text())
        keys = [b.key for b in game.buttons()]
        game.press(pr.choice(keys) if policy == "random" else gym._best_key(game, keys, pr), now)
        now += 1.0
    check_screen(game)
    assert game.summary()
    return game.score


@pytest.mark.parametrize("kind", ALL_KINDS)
@pytest.mark.parametrize("policy", ["best", "random"])
def test_every_game_finishes_with_valid_screens(kind, policy):
    for seed in range(8):
        assert 0.0 <= run(kind, policy, seed) <= 1.0
        game = gym.start(kind, random.Random(seed), 0.0)
        assert game.kind == kind and game.stat in gym.KINDS and kind in gym.KINDS[game.stat]
        assert game.score == 0.0 and not game.done
        assert 0.0 <= gym.autoplay(game, policy, random.Random(seed)) <= 1.0


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_unknown_and_late_presses_are_noops(kind):
    game = gym.start(kind, random.Random(1), 0.0)
    before = game.text()
    game.press("nope", 0.0)
    assert game.text() == before and not game.done
    gym.autoplay(game, "best", random.Random(1))
    score, text = game.score, game.text()
    for b in ("nope", "pesas_up", "sauna_stay", "obs_go", "sec_ready"):
        game.press(b, 99.0)
    assert (game.score, game.text(), game.buttons()) == (score, text, [])


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_same_seed_same_game(kind):
    a: list[str] = []
    b: list[str] = []
    assert run(kind, "random", 7, a) == run(kind, "random", 7, b)
    assert a == b


def test_pesas_scores():
    game = gym.start("pesas", Script(0.99, 0.99, 0.99, 0.0), 0.0)
    play(game, *["pesas_up"] * 4)
    assert game.done and game.score == pytest.approx(3 / 14)
    game = gym.start("pesas", Script(0.99), 0.0)
    play(game, "pesas_up", "pesas_up", "pesas_up", "pesas_stop")
    assert game.score == pytest.approx(3 / 7)
    game = gym.start("pesas", Script(0.99), 0.0)
    play(game, *["pesas_up"] * 7)
    assert game.done and game.score == 1.0
    game = gym.start("pesas", Script(0.99), 0.0)
    play(game, "pesas_stop")
    assert game.score == 0.0


def test_pesas_text_shows_kg_and_risk():
    game = gym.start("pesas", Script(0.99), 0.0)
    assert "30 kg" in game.text() and "2 %" in game.text()


def test_pulso_table():
    wins = {("aguantar", "empujar"), ("empujar", "amagar"), ("amagar", "aguantar")}
    for mine in gym.PULSO_MOVES:
        game = gym.start("pulso", Script(0.0), 0.0)  # rival: empujar, pista verdadera
        assert game.hint_move == "empujar"
        game.press(f"pulso_{mine}", 0.0)
        expected = 0.5 if mine == "empujar" else 1.0 if (mine, "empujar") in wins else 0.0
        assert game.points == expected
    game = gym.start("pulso", Script(0.99), 0.0)
    assert game.hint_move == "aguantar"  # pista falsa: otra jugada


def test_pulso_full_score():
    game = gym.start("pulso", Script(0.0), 0.0)
    play(
        game, "pulso_aguantar", "pulso_empujar", "pulso_amagar", "pulso_aguantar", "pulso_aguantar"
    )
    assert game.done and game.score == pytest.approx(0.7)
    assert "3.5" in game.summary()


def test_secuencia_partial_credit():
    game = gym.start("secuencia", Script(), 0.0)  # todo maíz
    assert "🌽 🌽 🌽 🌽" in game.text()
    play(game, "sec_ready", "sec_maiz", "sec_maiz", "sec_gusano")  # 2 de 4
    assert not game.done and "🌽" in game.text()
    play(game, "sec_ready", *["sec_maiz"] * 5)  # ronda perfecta
    assert not game.done
    play(game, "sec_ready", "sec_agua")  # 0 de 6
    assert game.done and game.score == pytest.approx((0.5 + 1 + 0) / 3)


def test_secuencia_hides_sequence_while_answering():
    game = gym.start("secuencia", random.Random(3), 0.0)
    play(game, "sec_ready")
    assert not any(s in game.text() for s in gym.SEC_SYMBOLS.values())


def test_obstaculos_late_press_ends_run():
    game = gym.start("obstaculos", Script(), 0.0)  # todo piedra
    play(game, "obs_go")
    game.press("obs_saltar", 1.0)
    game.press("obs_agachar", 2.0)
    assert "Tiempo restante: 19 s" in game.text()  # la salida fue en now=1.0
    game.press("obs_saltar", 1.0 + gym.OBS_TIME + 0.1)
    assert game.done and game.score == pytest.approx(1 / 8)


def test_obstaculos_clock_starts_at_go_and_full_run():
    game = gym.start("obstaculos", Script(), 0.0)
    game.press("obs_go", 100.0)
    for _ in range(gym.OBS_COUNT):
        game.press("obs_saltar", 100.0 + gym.OBS_TIME)  # justo en el límite todavía vale
    assert game.done and game.score == 1.0


def test_fondo_best_collapse_and_full():
    game = gym.start("fondo", Script(), 0.0)  # todo llano
    assert game.best == 10
    play(game, "fondo_sprint", "fondo_sprint", "fondo_sprint")  # la tercera no se puede pagar
    assert game.done and game.score == pytest.approx(6 / 10 * 0.5)
    game = gym.start("fondo", Script(), 0.0)
    play(game, *["fondo_normal"] * 5)
    assert game.done and game.score == 1.0


def test_fondo_terrain_cost_and_best_distance():
    assert gym._lap_cost("cuesta", "fondo_suave") == 2
    assert gym._lap_cost("bajada", "fondo_suave") == 0
    assert gym._best_distance(["bajada"] * 5) == 12  # 3 sprints (3 de coste), 1 normal, 1 suave
    assert gym._best_distance(["cuesta"] * 5) == 5  # suave cuesta 2: solo da para 5 vueltas


def test_sauna_scores():
    game = gym.start("sauna", Script(0.99), 0.0)
    play(game, "sauna_stay", "sauna_stay", "sauna_out")
    assert game.score == pytest.approx(2 / 7)
    game = gym.start("sauna", Script(0.0), 0.0)  # ronda 1 sin riesgo, ronda 2 se marea
    play(game, "sauna_stay", "sauna_stay")
    assert game.done and game.score == pytest.approx(1 / 14)
    game = gym.start("sauna", Script(0.99), 0.0)
    play(game, *["sauna_stay"] * 7)
    assert game.done and game.score == 1.0
    game = gym.start("sauna", Script(0.99), 0.0)
    assert "0 %" in game.text() and "68 °C" in game.text()


def test_balance_best_beats_random():
    for kind in ALL_KINDS:
        best = sum(run(kind, "best", s) for s in range(300)) / 300
        rand = sum(run(kind, "random", s) for s in range(300)) / 300
        assert 0.55 <= best <= 0.9, (kind, best)
        assert best > rand, (kind, best, rand)


def test_pick_kind_and_start():
    for stat, pair in gym.KINDS.items():
        rng = random.Random(1)
        assert {gym.pick_kind(stat, rng) for _ in range(40)} == set(pair)
    assert set(gym.KINDS) == set(Stat)
    assert set(gym.TITLES) == set(ALL_KINDS)
    with pytest.raises(ValueError):
        gym.start("nope", random.Random(0), 0.0)


# -- La sesión: calidad, combo, personalidad y eventos ---------------------------------------


def _session(record: RoosterRecord, score: float, trait: gym.Trait = gym.Trait.BALANCED):
    return gym.after_session(
        record, Stat.STRENGTH, score, trait=trait, now=0.0, day=date(2026, 11, 6)
    )


def test_la_forma_de_la_sesion_va_de_la_mitad_a_vez_y_media_segun_la_nota() -> None:
    fresh = RoosterRecord(name="Pepe")
    zero, _ = _session(fresh, 0.0)
    full, session = _session(fresh, 1.0)
    # El pago ya dio la mitad: con nota 0 el minijuego no suma nada, con nota 1 suma 1×.
    assert zero.form == 0
    assert full.form == pytest.approx(FORM_PER_TRAIN) == pytest.approx(session.form)
    assert PAID_FORM + session.form == pytest.approx(FORM_PER_TRAIN * 1.5)


def test_una_nota_perfecta_sube_el_stat_uno_mas_sin_pasar_del_maximo() -> None:
    record, session = _session(RoosterRecord(name="Pepe", strength=40), gym.PERFECT_SCORE)
    assert record.strength == 41 and session.bonus == 1
    record, session = _session(RoosterRecord(name="Pepe", strength=40), gym.PERFECT_SCORE - 0.01)
    assert record.strength == 40 and session.bonus == 0
    maxed, session = _session(RoosterRecord(name="Pepe", strength=100), 1.0)
    assert maxed.strength == 100 and session.bonus == 0


def test_el_combo_sube_con_las_sesiones_buenas_se_rompe_y_cambia_de_dia() -> None:
    record = RoosterRecord(name="Pepe")
    forms = []
    for _ in range(5):
        record, session = _session(record, gym.GOOD_SCORE)
        forms.append(session.form)
    assert record.combo == 5
    # Cada sesión buena seguida da más forma, hasta el tope del combo.
    assert forms[0] < forms[1] < forms[2] < forms[3] == forms[4]
    record, session = _session(record, 0.1)
    assert record.combo == 0 and session.combo == 0
    assert gym.combo_now(RoosterRecord(name="P", combo=3, combo_day=date(2026, 11, 5)),
                         date(2026, 11, 6)) == 0  # fmt: skip


def test_la_personalidad_sube_un_stat_y_baja_otro() -> None:
    assert gym.trait_factor(gym.Trait.BALANCED, Stat.SPEED) == 1
    for trait, (good, bad) in gym.TRAIT_STATS.items():
        assert gym.trait_factor(trait, good) == gym.TRAIT_GOOD
        assert gym.trait_factor(trait, bad) == gym.TRAIT_BAD
    cocky, _ = _session(RoosterRecord(name="Pepe"), 1.0, gym.Trait.COCKY)
    plain, _ = _session(RoosterRecord(name="Pepe"), 1.0)
    assert cocky.form > plain.form
    # Fija para un gallo y cambia con el siguiente.
    assert gym.trait_of(7, 0) == gym.trait_of(7, 0) != gym.trait_of(7, 1)
    assert {gym.trait_of(user, 0) for user in range(8)} == set(gym.Trait)


class _Fixed(random.Random):
    def __init__(self, value: float) -> None:
        super().__init__(3)
        self.value = value

    def random(self) -> float:
        return self.value


def test_los_eventos_salen_a_veces_y_el_suplemento_puede_lesionar() -> None:
    assert gym.roll_event(_Fixed(gym.EVENT_CHANCE)) is None
    assert gym.roll_event(_Fixed(0.0)) in gym.EVENTS
    gofio = gym.EVENT_BY_KEY["gofio"]
    after = gym.apply_event(RoosterRecord(name="Pepe"), gofio, 0.0)
    assert after.form == gofio.form and "Pepe" in gym.event_text(gofio, "Pepe")
    raised, hurt = gym.take_supplement(RoosterRecord(name="Pepe"), Stat.SPEED, _Fixed(0.0), 0.0)
    assert raised.speed == gym.SUPPLEMENT_GAIN and hurt
    assert raised.injured_until == gym.SUPPLEMENT_HOURS * 3600
    safe, hurt = gym.take_supplement(RoosterRecord(name="Pepe"), Stat.SPEED, _Fixed(0.99), 0.0)
    assert not hurt and safe.injured_until == 0


def test_los_contadores_de_un_minijuego() -> None:
    delta = gallos_drill_stats(kind="sauna", score=1.0, bonus=1, combo=3, event="gofio", form=40)
    assert delta.add == {
        "gallos_drills": 1,
        "gallos_drill_sauna": 1,
        "gallos_drill_perfect": 1,
        "gallos_drill_full": 1,
        "gallos_evt_gofio": 1,
    }
    assert delta.peak == {"gallos_combo_max": 3, "gallos_form_max": 40}
    zero = gallos_drill_stats(kind="pulso", score=0.0, bonus=0, combo=0, event=None, form=0)
    assert zero.add["gallos_drill_zero"] == 1
    derived = with_derived(
        {"gallos_drill_pesas": 2, "gallos_drill_sauna": 1, "gallos_evt_gofio": 1}
    )
    assert derived["gallos_drill_kinds"] == 2 and derived["gallos_event_kinds"] == 1
