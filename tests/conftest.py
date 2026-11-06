"""Lo común a todas las pruebas."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator

import pytest

from bot.services import credit


@pytest.fixture(autouse=True)
def _ipc_a_cero() -> Iterator[None]:
    """El IPC del crédito vive en memoria del proceso: cada prueba empieza sin él, para que lo
    financiado en una no encarezca la tienda de la siguiente."""
    credit._IPC.clear()
    yield
    credit._IPC.clear()


# --- Vigilancia del tiempo (Biblia, «Pruebas rápidas») ---------------------------------------
#: Una prueba sin `@pytest.mark.lenta` que gaste más CPU que esto sale en el resumen de la tanda.
SLOW_SECONDS = 3.0
#: Y más que esto hace fallar la tanda: o se acorta, o se marca `lenta`.
TOO_SLOW_SECONDS = 10.0
#: Tope de reloj, para lo que espera sin gastar CPU (un `sleep`, un proceso de Node).
TOO_LONG_SECONDS = 30.0
_slow: list[tuple[float, float, str]] = []


def pytest_xdist_auto_num_workers(config: pytest.Config) -> int:
    # Un proceso por núcleo físico: con uno por hilo la tanda no acaba antes (lo medido, 12 o 24
    # en un Ryzen de 12 núcleos: 118 s las dos) y cada prueba tarda el doble de reloj.
    return max(1, (os.cpu_count() or 2) // 2)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Iterator[None]:
    # La CPU del proceso, no el reloj: con la tanda en paralelo el reloj de una prueba
    # normal se triplica, y la CPU casi no cambia.
    start = time.process_time()
    try:
        return (yield)
    finally:
        item.user_properties.append(("cpu", time.process_time() - start))


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    # Con xdist llega al proceso principal el informe de cada prueba, con su CPU y sus marcas.
    if report.when != "call" or "lenta" in report.keywords:
        return
    cpu = dict(report.user_properties).get("cpu", 0.0)
    if cpu > SLOW_SECONDS or report.duration > TOO_LONG_SECONDS:
        _slow.append((cpu, report.duration, report.nodeid))


def _fails(cpu: float, wall: float) -> bool:
    return cpu > TOO_SLOW_SECONDS or wall > TOO_LONG_SECONDS


def pytest_sessionfinish(session: pytest.Session) -> None:
    if any(_fails(cpu, wall) for cpu, wall, _ in _slow) and session.exitstatus == 0:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    if not _slow:
        return
    terminalreporter.section("Pruebas lentas sin @pytest.mark.lenta")
    for cpu, wall, nodeid in sorted(_slow, reverse=True):
        flag = "  <- FALLA" if _fails(cpu, wall) else ""
        terminalreporter.write_line(f"{cpu:5.1f} s CPU {wall:5.1f} s reloj  {nodeid}{flag}")
    terminalreporter.write_line(
        f"Más de {SLOW_SECONDS:g} s de CPU avisa; más de {TOO_SLOW_SECONDS:g} s de CPU o "
        f"{TOO_LONG_SECONDS:g} s de reloj falla. Se acorta la prueba (dobles, menos datos, una "
        "sola pasada, sin esperas de reloj) o, si comprueba algo que casi nunca cambia (dibujo "
        "repartido, Node contra Chromium, estadística), se marca `lenta`."
    )
