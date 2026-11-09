"""Pruebas de los minijuegos de `pala` (`bot.services.work_games`)."""

from __future__ import annotations

import random
import re

import pytest

from bot.services.work import Mechanic
from bot.services.work_catalog import JOB_BY_KEY, JOBS, SORT_PACKS
from bot.services.work_games import (
    GRACE_SECONDS,
    MEMORIZE_SECONDS,
    MiniGame,
    _best_index,
    new_game,
)
from bot.services.work_tools import NO_PERKS, TOOLS, perks_for

NOW = 1_000.0


def play_perfect(game: MiniGame, *, step: float = 0.5) -> float:
    """Juega sin fallar; devuelve la hora al terminar."""
    now = game.started_at
    while not game.finished(now):
        if game.showing:
            game.hide(now)
            continue
        current = game.current
        assert current is not None
        option = (
            current.answer[game.step] if game.mechanic is Mechanic.MEMORY else current.answer[0]
        )
        assert game.press(option, now)
        now += step
    return now


def all_positions():  # noqa: ANN201
    return [position for job in JOBS for position in job.all_positions]


def test_jugar_perfecto_saca_100_en_todos_los_puestos() -> None:
    for position in all_positions():
        game = new_game(position, random.Random(1), now=NOW)
        play_perfect(game)
        assert game.score() == 100, position.title
        assert game.broken == 0


def test_cada_ronda_cabe_en_los_botones_de_discord() -> None:
    for position in all_positions():
        game = new_game(position, random.Random(2), now=NOW)
        for round_ in game.rounds:
            assert 2 <= len(round_.options) <= 10
            assert all(0 < len(label) <= 80 for label in round_.options)
            assert all(0 <= i < len(round_.options) for i in round_.answer)


def test_cavar_en_una_tuberia_resta() -> None:
    position = JOBS[0].position(1)
    game = new_game(position, random.Random(3), now=NOW)
    current = game.current
    assert current is not None
    # Busca una ronda con un sitio peligroso para pulsarlo.
    while not current.penalty:
        game.press(current.answer[0], NOW)
        current = game.current
        assert current is not None
    before = game.points
    assert not game.press(next(iter(current.penalty)), NOW)
    assert game.points == before - 1 and game.broken == 1


def test_en_memoria_un_fallo_acaba_la_ronda() -> None:
    position = JOBS[1].position(2)  # camarero
    game = new_game(position, random.Random(4), now=NOW)
    game.hide(NOW)
    current = game.current
    assert current is not None
    wrong = next(i for i in range(len(current.options)) if i != current.answer[0])
    assert not game.press(wrong, NOW)
    assert game.index == 1 and game.showing  # siguiente ronda, enseñando la secuencia
    assert game.perfect_rounds == 0


def test_en_memoria_el_reloj_se_para_hasta_5_segundos_al_memorizar() -> None:
    eurodiputado = JOB_BY_KEY["politica"].position(6, "bruselas")
    game = new_game(eurodiputado, random.Random(3), now=NOW)
    end = NOW + game.seconds
    # Mientras se enseña, el plazo lleva los 5 s de memorizar.
    assert game.showing and game.deadline == end + MEMORIZE_SECONDS
    # Memorizado a los 2 s: solo se paró 2 s; pulsar otra vez no cambia nada.
    game.hide(NOW + 2)
    game.hide(NOW + 3)
    assert not game.showing and game.deadline == end + 2
    # Falla y pasa de ronda: la secuencia nueva vuelve a parar el reloj.
    current = game.current
    assert current is not None
    game.press((current.answer[0] + 1) % len(current.options), NOW + 4)
    assert game.showing and game.deadline == end + 2 + MEMORIZE_SECONDS
    # Si tarda más de 5 s en memorizar, el reloj corre desde el quinto.
    game.hide(NOW + 4 + 9)
    assert game.deadline == end + 2 + MEMORIZE_SECONDS
    # Al enseñar la última ronda con el tiempo ya justo, aún quedan los 5 s.
    late = new_game(eurodiputado, random.Random(3), now=NOW)
    late.hide(NOW)
    while late.index < len(late.rounds) - 1:
        late.hide(end - 1)
        current = late.current
        assert current is not None
        late.press((current.answer[0] + 1) % len(current.options), end - 1)
    assert not late.finished(end + 3)
    late.hide(end + 3)
    assert not late.showing and late.deadline == end + 4


def test_mientras_se_ensena_la_secuencia_no_se_puede_pulsar() -> None:
    game = new_game(JOBS[1].position(1), random.Random(5), now=NOW)
    assert game.showing
    assert not game.press(game.rounds[0].answer[0], NOW)
    assert game.points == 0


def test_acabar_antes_da_un_extra_pero_la_nota_no_pasa_de_100() -> None:
    game = new_game(JOBS[2].position(3), random.Random(6), now=NOW)  # rueda de prensa
    # Falla la primera y acierta el resto deprisa.
    first = game.current
    assert first is not None
    game.press((first.answer[0] + 1) % len(first.options), NOW)
    play_perfect(game, step=0.1)
    base = round(100 * game.points / game.max_points)
    assert base < game.score() <= 100


def test_fuera_de_tiempo_no_cuenta() -> None:
    game = new_game(JOBS[0].position(1), random.Random(7), now=NOW)
    late = game.deadline + GRACE_SECONDS + 1
    current = game.current
    assert current is not None
    assert not game.press(current.answer[0], late)
    assert game.finished(late) and game.points == 0


def test_reventado_tienes_menos_tiempo() -> None:
    position = JOBS[0].position(1)
    fresh = new_game(position, random.Random(8), now=NOW)
    tired = new_game(position, random.Random(8), now=NOW, tired=True)
    assert tired.seconds < fresh.seconds


def test_el_plano_de_cavar_cambia_cada_turno() -> None:
    position = JOBS[0].position(1)
    headers = {new_game(position, random.Random(seed), now=NOW).header for seed in range(10)}
    assert len(headers) > 1


def test_la_guardia_dura_el_doble_y_tiene_el_doble_de_rondas() -> None:
    position = JOBS[3].position(3)  # enfermero
    normal = new_game(position, random.Random(9), now=NOW)
    guard = new_game(position, random.Random(9), now=NOW, guard=True)
    assert guard.seconds == normal.seconds * 2
    assert len(guard.rounds) == len(normal.rounds) * 2


def test_el_mir_tiene_cuatro_respuestas() -> None:
    game = new_game(JOBS[3].position(4), random.Random(10), now=NOW)
    assert all(len(r.options) == 4 for r in game.rounds)
    assert game.rounds[0].options == ["A", "B", "C", "D"]


def test_los_bugs_se_ensenan_como_codigo_con_lineas() -> None:
    game = new_game(JOBS[4].position(2), random.Random(11), now=NOW)
    first = game.rounds[0]
    assert "```py" in first.prompt
    assert first.options[0] == "Línea 1"


# -- Herramientas de curro y mensajes de cada jugada --------------------------------------


def wrong_option(game: MiniGame) -> int:
    current = game.current
    assert current is not None
    expected = current.answer[game.step] if game.mechanic is Mechanic.MEMORY else None
    return next(
        i
        for i in range(len(current.options))
        if (i != expected if expected is not None else i not in current.answer)
    )


def test_un_fallo_dice_cual_era_la_buena() -> None:
    game = new_game(JOBS[2].position(3), random.Random(12), now=NOW)  # rueda de prensa
    current = game.current
    assert current is not None
    game.press(wrong_option(game), NOW)
    assert game.last == f"❌ Fallo. Era {current.options[current.answer[0]]}."
    assert game.first_miss and game.misses == 1


def test_el_fallo_gratis_repite_la_jugada_una_sola_vez() -> None:
    perks = perks_for("obra", Mechanic.DIG, {"chaleco_reflectante"})
    game = new_game(JOBS[0].position(1), random.Random(13), now=NOW, perks=perks)
    assert game.retries == 1
    assert not game.press(wrong_option(game), NOW)
    assert game.index == 0 and game.saved == 1 and game.misses == 0
    assert "inténtalo otra vez" in game.last
    game.press(wrong_option(game), NOW)
    assert game.index == 1 and game.misses == 1


def test_en_memoria_el_fallo_gratis_no_pierde_lo_que_llevabas() -> None:
    perks = perks_for("politica", Mechanic.MEMORY, {"pinganillo"})
    game = new_game(JOBS[2].position(1), random.Random(14), now=NOW, perks=perks)
    game.hide(NOW)
    current = game.current
    assert current is not None
    assert game.press(current.answer[0], NOW)
    assert not game.press(wrong_option(game), NOW)
    assert game.step == 1 and game.index == 0 and not game.showing
    for option in current.answer[1:]:
        assert game.press(option, NOW)
    assert game.perfect_rounds == 1


def test_con_el_seguro_romper_algo_no_resta() -> None:
    perks = perks_for("obra", Mechanic.DIG, {"seguro_rc"})
    game = new_game(JOBS[0].position(1), random.Random(3), now=NOW, perks=perks)
    current = game.current
    assert current is not None
    while not current.penalty:
        game.press(current.answer[0], NOW)
        current = game.current
        assert current is not None
    before = game.points
    game.press(next(iter(current.penalty)), NOW)
    assert game.points == before
    assert game.broken == 1 and game.insured_breaks == 1
    assert "paga el seguro" in game.last


def test_la_chuleta_tacha_una_respuesta_mala_en_cada_pregunta() -> None:
    position = JOBS[2].position(3)  # rueda de prensa: tres respuestas
    perks = perks_for("politica", Mechanic.DIALOGUE, {"argumentario"})
    plain = new_game(position, random.Random(15), now=NOW)
    helped = new_game(position, random.Random(15), now=NOW, perks=perks)
    assert helped.fifty
    for before, after in zip(plain.rounds, helped.rounds, strict=True):
        assert len(after.options) == len(before.options) - 1
    play_perfect(helped)
    assert helped.score() == 100


def test_las_herramientas_de_tiempo_se_suman() -> None:
    position = JOBS[0].position(1)
    perks = perks_for("obra", Mechanic.DIG, {"reloj_fichar", "casco_linterna"})
    assert perks.extra_time == pytest.approx(0.25)
    base = new_game(position, random.Random(16), now=NOW)
    longer = new_game(position, random.Random(16), now=NOW, perks=perks)
    assert longer.seconds == pytest.approx(base.seconds * 1.25)


def test_cada_herramienta_solo_sirve_en_su_oficio_y_su_minijuego() -> None:
    owned = {tool.key for tool in TOOLS}
    assert perks_for("obra", Mechanic.DIG, set()) is NO_PERKS
    dig = perks_for("obra", Mechanic.DIG, owned)
    assert {t.key for t in dig.tools} == {
        "reloj_fichar",
        "casco_linterna",
        "chaleco_reflectante",
        "seguro_rc",
    }
    assert dig.insured and not dig.fifty and dig.retries == 1
    office_talk = perks_for("oficina", Mechanic.DIALOGUE, owned)
    assert office_talk.fifty and not office_talk.insured
    assert all(t.job in (None, "oficina") for t in office_talk.tools)
    # Cada oficio tiene sus herramientas y todas tienen un efecto.
    for job in JOBS:
        assert any(t.job == job.key for t in TOOLS), job.key
    assert all(tool.effect for tool in TOOLS)


def test_el_tiempo_sobrante_solo_cuenta_si_se_acaban_las_rondas() -> None:
    game = new_game(JOBS[0].position(1), random.Random(17), now=NOW)
    end = play_perfect(game, step=0.5)
    assert game.completed
    # La última pulsación fue plantarse en el órdago: el extra se midió una antes.
    assert game.time_left == pytest.approx(game.deadline - (end - 1.0))
    late = new_game(JOBS[0].position(1), random.Random(17), now=NOW)
    late.finish(late.deadline)
    assert not late.completed and late.time_left == 0


def test_calcular_la_mejor_oferta_gana_por_margen_claro() -> None:
    assert _best_index([1.0, 1.3, 0.5]) == 1
    assert _best_index([1.0, 1.1, 0.5]) is None  # demasiado justo: se vuelve a sortear


def test_en_los_pedidos_la_respuesta_es_la_que_mas_paga_por_kilometro() -> None:
    position = JOB_BY_KEY["reparto"].position(2)
    for seed in range(20):
        game = new_game(position, random.Random(seed), now=NOW)
        for round_ in game.rounds:
            offers = re.findall(r"(\d+,\d) km · \*\*([\d.]+) Y\$", round_.prompt)
            ratios = [int(pay.replace(".", "")) / float(km.replace(",", ".")) for km, pay in offers]
            assert len(ratios) == len(round_.options) == 4
            assert round_.answer == [ratios.index(max(ratios))]


def test_en_las_metricas_la_respuesta_es_la_de_mas_engagement() -> None:
    position = JOB_BY_KEY["influencer"].position(2)
    for seed in range(20):
        game = new_game(position, random.Random(seed), now=NOW)
        for round_ in game.rounds:
            pairs = re.findall(r"([\d.]+) visitas · ([\d.]+) ❤️", round_.prompt)
            rates = [int(lk.replace(".", "")) / int(v.replace(".", "")) for v, lk in pairs]
            assert round_.answer == [rates.index(max(rates))]


def test_clasificar_manda_cada_papel_a_su_sitio() -> None:
    for key, pack in SORT_PACKS.items():
        targets = dict(pack.items)
        position = next(p for job in JOBS for p in job.all_positions if p.content == key)
        game = new_game(position, random.Random(5), now=NOW)
        assert game.header == pack.rule
        for round_ in game.rounds:
            assert round_.options == list(pack.bins)
            text = round_.prompt.split("**")[1]
            assert round_.answer == [targets[text]]
