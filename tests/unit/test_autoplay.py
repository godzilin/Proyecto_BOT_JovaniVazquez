"""Pruebas de bot.services.autoplay: el bucle común de autoplay, sin Discord ni juego.

Cada prueba enchufa un `step` falso que devuelve resultados guionizados y
comprueba cuándo para el bucle y qué motivo da. Las esperas se sustituyen por un
registro para ver cuánto se espera sin que la prueba tarde.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from bot.services.autoplay import (
    AUTOPLAY_MAX,
    AUTOPLAY_MIN_GAP,
    LOSS_LIMIT_MULTIPLIER,
    AutoplayOutcome,
    AutoplaySession,
    AutoplayStop,
    SpinResult,
    StopReason,
    stop_text,
)

STAKE = 100


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Registra las esperas del bucle y no espera de verdad."""
    waited: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay: float, *args: object) -> None:
        waited.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return waited


def scripted(*results: SpinResult | StopReason):  # noqa: ANN201
    """`step` que devuelve los resultados en orden; un `StopReason` lanza `AutoplayStop`."""
    queue = list(results)
    calls: list[int] = []

    async def step(number: int) -> SpinResult:
        calls.append(number)
        item = queue.pop(0)
        if isinstance(item, StopReason):
            raise AutoplayStop(item)
        return item

    step.calls = calls  # type: ignore[attr-defined]
    return step


async def test_para_en_el_tope_de_rondas(sleeps: list[float]) -> None:
    session = AutoplaySession(stake=STAKE)

    outcome = await session.run(scripted(*[SpinResult(net=1)] * AUTOPLAY_MAX))

    assert (outcome.spins, outcome.reason) == (AUTOPLAY_MAX, StopReason.MAX_SPINS)
    assert outcome.net == AUTOPLAY_MAX


async def test_para_cuando_las_perdidas_netas_llegan_a_diez_veces_la_apuesta(
    sleeps: list[float],
) -> None:
    session = AutoplaySession(stake=STAKE)
    step = scripted(*[SpinResult(net=-STAKE)] * 30)

    outcome = await session.run(step)

    assert outcome.reason is StopReason.LOSS_LIMIT
    assert outcome.spins == LOSS_LIMIT_MULTIPLIER
    assert outcome.net == -STAKE * LOSS_LIMIT_MULTIPLIER


async def test_un_premio_compensa_las_perdidas_y_alarga_la_sesion(sleeps: list[float]) -> None:
    # Pierde 9 apuestas, gana 5 y vuelve a perder: el límite cuenta el neto, no las rachas.
    session = AutoplaySession(stake=STAKE)
    results = (
        [SpinResult(net=-STAKE)] * 9 + [SpinResult(net=5 * STAKE)] + [SpinResult(net=-STAKE)] * 6
    )
    outcome = await session.run(scripted(*results))

    assert outcome.reason is StopReason.LOSS_LIMIT
    assert outcome.spins == 9 + 1 + 6


async def test_un_premio_gordo_para_la_sesion(sleeps: list[float]) -> None:
    outcome = await AutoplaySession(stake=STAKE).run(
        scripted(SpinResult(net=-STAKE), SpinResult(net=-1, big_prize=True))
    )
    assert (outcome.spins, outcome.reason) == (2, StopReason.BIG_PRIZE)


async def test_si_coinciden_premio_gordo_y_limite_de_perdidas_el_motivo_es_el_premio(
    sleeps: list[float],
) -> None:
    session = AutoplaySession(stake=STAKE)
    outcome = await session.run(scripted(SpinResult(net=-STAKE * 20, big_prize=True)))
    assert outcome.reason is StopReason.BIG_PRIZE


async def test_sin_saldo_para_antes_de_jugar_y_no_cuenta_esa_ronda(sleeps: list[float]) -> None:
    session = AutoplaySession(stake=STAKE)

    outcome = await session.run(scripted(SpinResult(net=-STAKE), StopReason.NO_FUNDS))

    assert (outcome.spins, outcome.reason) == (1, StopReason.NO_FUNDS)
    assert outcome.net == -STAKE


async def test_el_limite_de_la_banca_tiene_su_motivo(sleeps: list[float]) -> None:
    outcome = await AutoplaySession(stake=STAKE).run(scripted(StopReason.BANK_LIMIT))
    assert (outcome.spins, outcome.reason) == (0, StopReason.BANK_LIMIT)


async def test_parar_acaba_la_ronda_en_curso_y_sale_despues(sleeps: list[float]) -> None:
    session = AutoplaySession(stake=STAKE)

    async def step(number: int) -> SpinResult:
        if number == 3:
            session.request_stop()  # el clic llega a mitad de la tercera ronda
        return SpinResult(net=-1)

    outcome = await session.run(step)

    assert (outcome.spins, outcome.reason) == (3, StopReason.MANUAL)


async def test_parar_antes_de_empezar_no_juega_nada(sleeps: list[float]) -> None:
    session = AutoplaySession(stake=STAKE)
    session.request_stop()
    step = scripted()

    outcome = await session.run(step)

    assert outcome.spins == 0
    assert step.calls == []


async def test_el_primer_motivo_de_parada_es_el_que_vale() -> None:
    session = AutoplaySession(stake=STAKE)
    session.request_stop()
    session.request_stop(StopReason.CLOSED)
    assert session._stop is StopReason.MANUAL


async def test_el_premio_gordo_gana_a_parar_a_mano(sleeps: list[float]) -> None:
    session = AutoplaySession(stake=STAKE)

    async def step(number: int) -> SpinResult:
        session.request_stop()
        return SpinResult(net=STAKE * 60, big_prize=True)

    assert (await session.run(step)).reason is StopReason.BIG_PRIZE


async def test_un_error_del_juego_para_con_su_motivo_y_no_rompe_el_bucle(
    sleeps: list[float],
) -> None:
    async def step(number: int) -> SpinResult:
        raise RuntimeError("fallo del juego")

    outcome = await AutoplaySession(stake=STAKE).run(step)

    assert (outcome.spins, outcome.reason) == (0, StopReason.ERROR)


async def test_la_ronda_puede_pedir_parar_despues_de_contarse(sleeps: list[float]) -> None:
    outcome = await AutoplaySession(stake=STAKE).run(
        scripted(SpinResult(net=7, stop=StopReason.CLOSED))
    )
    assert (outcome.spins, outcome.net, outcome.reason) == (1, 7, StopReason.CLOSED)


async def test_deja_el_intervalo_minimo_entre_rondas(sleeps: list[float]) -> None:
    # Las rondas no esperan nada por su cuenta: toda la espera es el intervalo.
    session = AutoplaySession(stake=STAKE, max_spins=4)

    async def step(number: int) -> SpinResult:
        return SpinResult(net=0, edited_at=time.monotonic())

    await session.run(step)

    assert len(sleeps) == 3  # entre 4 rondas, tres huecos; tras la última no hace falta
    assert all(0 < wait <= AUTOPLAY_MIN_GAP for wait in sleeps)
    assert max(sleeps) > AUTOPLAY_MIN_GAP - 0.5


async def test_no_espera_si_la_ronda_ya_tardo_mas_que_el_intervalo(sleeps: list[float]) -> None:
    session = AutoplaySession(stake=STAKE, max_spins=3)

    async def step(number: int) -> SpinResult:
        return SpinResult(net=0, edited_at=time.monotonic() - AUTOPLAY_MIN_GAP - 1)

    await session.run(step)

    assert sleeps == []


async def test_el_intervalo_se_cuenta_desde_la_ultima_edicion(sleeps: list[float]) -> None:
    session = AutoplaySession(stake=STAKE, max_spins=2, min_gap=1.0)

    async def step(number: int) -> SpinResult:
        return SpinResult(net=0, edited_at=time.monotonic() - 0.75)

    await session.run(step)

    assert len(sleeps) == 1
    assert 0.2 < sleeps[0] <= 0.25


async def test_start_crea_una_tarea_con_nombre_y_llama_a_on_finish(sleeps: list[float]) -> None:
    session = AutoplaySession(stake=STAKE, max_spins=2)
    finished: list[AutoplayOutcome] = []

    async def finish(outcome: AutoplayOutcome) -> None:
        finished.append(outcome)

    task = session.start(
        scripted(SpinResult(net=1), SpinResult(net=1)), on_finish=finish, name="x-1"
    )

    assert task.get_name() == "x-1"
    assert session.running
    await task
    assert not session.running
    assert finished[0].spins == 2
    with pytest.raises(RuntimeError):
        session.start(scripted(), on_finish=finish, name="otra")


async def test_un_fallo_en_on_finish_no_se_escapa_de_la_tarea(sleeps: list[float]) -> None:
    async def finish(outcome: AutoplayOutcome) -> None:
        raise ValueError("boom")

    session = AutoplaySession(stake=STAKE)
    task = session.start(scripted(StopReason.NO_FUNDS), on_finish=finish, name="x")
    await task  # no lanza: se registra en el log


async def test_close_espera_a_la_ronda_en_curso_y_la_deja_acabar(sleeps: list[float]) -> None:
    session = AutoplaySession(stake=STAKE)
    in_round = asyncio.Event()
    release = asyncio.Event()
    finished: list[AutoplayOutcome] = []

    async def step(number: int) -> SpinResult:
        in_round.set()
        await release.wait()
        return SpinResult(net=-1)

    async def finish(outcome: AutoplayOutcome) -> None:
        finished.append(outcome)

    session.start(step, on_finish=finish, name="x")
    await in_round.wait()
    closing = asyncio.create_task(session.close(grace=5))
    await asyncio.sleep(0)
    assert not closing.done()  # espera a que acabe la ronda
    release.set()
    await closing

    assert (finished[0].spins, finished[0].reason) == (1, StopReason.CLOSED)


async def test_close_cancela_la_tarea_si_la_ronda_no_acaba(sleeps: list[float]) -> None:
    session = AutoplaySession(stake=STAKE)
    in_round = asyncio.Event()

    async def step(number: int) -> SpinResult:
        in_round.set()
        await asyncio.Event().wait()  # no acaba nunca
        return SpinResult(net=0)

    async def finish(outcome: AutoplayOutcome) -> None:
        raise AssertionError("una tarea cancelada no llega a on_finish")

    task = session.start(step, on_finish=finish, name="x")
    await in_round.wait()

    await session.close(grace=0.01)

    assert task.cancelled()


async def test_close_sin_tarea_o_ya_acabada_no_hace_nada(sleeps: list[float]) -> None:
    session = AutoplaySession(stake=STAKE)
    await session.close()
    task = session.start(
        scripted(StopReason.NO_FUNDS), on_finish=lambda o: asyncio.sleep(0), name="x"
    )
    await task
    await session.close()


def test_el_limite_de_perdidas_es_diez_veces_la_apuesta() -> None:
    assert AutoplaySession(stake=250).loss_limit == 250 * LOSS_LIMIT_MULTIPLIER == 2_500


def test_resumen_con_neto_y_motivo() -> None:
    outcome = AutoplayOutcome(spins=17, net=-1_230, reason=StopReason.LOSS_LIMIT, loss_limit=1_000)
    text = outcome.summary()
    assert "17 tiradas" in text
    assert "-1.230 Y$" in text
    assert "1.000 Y$" in text and "Techo de gasto" in text


def test_resumen_en_positivo_y_en_tablas() -> None:
    assert "+500 Y$" in AutoplayOutcome(3, 500, StopReason.BIG_PRIZE).summary()
    assert "±0 Y$" in AutoplayOutcome(25, 0, StopReason.MAX_SPINS).summary(unit="bolas")


@pytest.mark.parametrize("reason", list(StopReason))
def test_todos_los_motivos_tienen_frase(reason: StopReason) -> None:
    assert stop_text(reason, loss_limit=1_000).strip()


def test_los_motivos_de_parada_usan_la_unidad_del_juego() -> None:
    """La tragaperras dice «tiradas»; el pachinko, «tandas». Lo que no es de ronda no cambia."""
    for reason in (StopReason.MAX_SPINS, StopReason.NO_FUNDS):
        default = stop_text(reason)
        assert "tirada" in default and "tanda" not in default
        tandas = stop_text(reason, unit="tandas")
        assert "tanda" in tandas and "tirada" not in tandas
    assert stop_text(StopReason.MAX_SPINS, max_spins=25, unit="tandas") == (
        "Parado: tope de 25 tandas por sesión."
    )
    assert stop_text(StopReason.NO_FUNDS, unit="tandas") == "Parado: no te llega para más tandas."
    assert stop_text(StopReason.NO_FUNDS) == "Parado: no te llega para más tiradas."
    assert stop_text(StopReason.MANUAL, unit="tandas") == stop_text(StopReason.MANUAL)


def test_el_resumen_lleva_la_unidad_en_el_titular_y_en_el_motivo() -> None:
    outcome = AutoplayOutcome(spins=25, net=-40, reason=StopReason.MAX_SPINS)
    assert "25 tiradas" in outcome.summary() and "tope de 25 tiradas" in outcome.summary()
    text = outcome.summary(unit="tandas")
    assert "25 tandas" in text and "tope de 25 tandas" in text and "tirada" not in text
    assert outcome.reason_text(unit="tandas") == "Parado: tope de 25 tandas por sesión."
    assert outcome.reason_text() == "Parado: tope de 25 tiradas por sesión."
