"""Lo común a todas las pruebas."""

from __future__ import annotations

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
#: Una prueba sin `@pytest.mark.lenta` que tarde más que esto sale en el resumen de la tanda.
SLOW_SECONDS = 2.0
#: Y más que esto hace fallar la tanda: o se acorta, o se marca `lenta`.
TOO_SLOW_SECONDS = 10.0
_slow: list[tuple[float, str]] = []


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    # Con xdist llega al proceso principal el informe de cada prueba, con su tiempo y sus marcas.
    if report.when == "call" and "lenta" not in report.keywords and report.duration > SLOW_SECONDS:
        _slow.append((report.duration, report.nodeid))


def pytest_sessionfinish(session: pytest.Session) -> None:
    if any(seconds > TOO_SLOW_SECONDS for seconds, _ in _slow) and session.exitstatus == 0:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    if not _slow:
        return
    terminalreporter.section("Pruebas lentas sin @pytest.mark.lenta")
    for seconds, nodeid in sorted(_slow, reverse=True):
        flag = "  <- FALLA" if seconds > TOO_SLOW_SECONDS else ""
        terminalreporter.write_line(f"{seconds:6.1f} s  {nodeid}{flag}")
    terminalreporter.write_line(
        f"Más de {SLOW_SECONDS:g} s avisa y más de {TOO_SLOW_SECONDS:g} s falla: se acorta la "
        "prueba (dobles, menos datos, una sola pasada) o, si comprueba algo que casi nunca cambia "
        "(dibujo repartido, Node contra Chromium, estadística), se marca `lenta`."
    )
