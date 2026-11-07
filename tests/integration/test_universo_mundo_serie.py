"""El mundo de serie del Jovaniverso (`mundoDeSerie`, en TypeScript) lo acepta el servidor.

Compila el módulo con Vite y lo ejecuta con Node (`docs/volcar_mundo_serie.py`), así que
necesita `universo/node_modules` (`npm ci`); sin él, se salta.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from bot.services import universo_editor as editor

RAIZ = Path(__file__).resolve().parents[2]
SCRIPT = RAIZ / "docs" / "volcar_mundo_serie.py"

pytestmark = pytest.mark.lenta


def _cargar_script():
    spec = importlib.util.spec_from_file_location("volcar_mundo_serie", SCRIPT)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture(scope="module")
def mundo() -> dict:
    if not (RAIZ / "universo" / "node_modules").is_dir():
        pytest.skip("falta universo/node_modules (npm ci)")
    return _cargar_script().volcar()


def test_el_servidor_acepta_el_mundo_de_serie(mundo: dict) -> None:
    limpio = editor.validate_world(mundo, {})
    # Con holgura frente a los topes: el mundo de serie es solo el punto de partida del editor.
    assert len(editor.encode_world(limpio).encode()) < editor.MAX_DOC_BYTES // 4
    assert len(limpio["assets"]) < editor.MAX_ASSETS // 2
    assert len(limpio["objetos"]) < editor.MAX_OBJECTS // 4


def test_lleva_lo_de_siempre(mundo: dict) -> None:
    assets = mundo["assets"]
    for clave in (
        "iglesia",
        "quiosco",
        "faro",
        "muelle",
        "roque",
        "pino",
        "palmera",
        "drago-plaza",
    ):
        assert clave in assets
    usados = {o["asset"] for o in mundo["objetos"]}
    assert usados == set(assets), "cada asset se usa y cada objeto tiene el suyo"
    assert set(mundo["zonas"]) == editor.EDITABLE_ZONES


def test_solo_chocan_las_rocas_grandes(mundo: dict) -> None:
    assert mundo["assets"]["roca"]["colision"] is None
    assert mundo["assets"]["roca-grande"]["colision"]
    assert mundo["assets"]["roca"]["malla"] == mundo["assets"]["roca-grande"]["malla"]
