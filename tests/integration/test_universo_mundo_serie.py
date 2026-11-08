"""El mundo de serie del Jovaniverso (`mundoDeSerie`, en TypeScript) lo acepta el servidor.

Compila el módulo con Vite y lo ejecuta con Node (`docs/volcar_mundo_serie.py`), así que
necesita `universo/node_modules` (`npm ci`); sin él, se salta.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from bot.services import universo_editor as editor
from bot.services.universo import ZONES_DM

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
    limpio = editor.validate_world(editor.split_world(mundo), {})
    # Con holgura frente a los topes: el mundo de serie es solo el punto de partida del editor.
    assert max(len(editor.encode_piece(t).encode()) for t in limpio.values()) < (
        editor.MAX_PIECE_BYTES // 4
    )
    assert sum(k.startswith("a/") for k in limpio) < editor.MAX_ASSETS // 2
    densa = max(len(t["objetos"]) for k, t in limpio.items() if k.startswith("c/"))
    assert densa < editor.MAX_CELL_OBJECTS // 4
    assert sum(len(t.get("objetos", [])) for t in limpio.values()) == len(mundo["objetos"])


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
    # La sala cuenta los lugares con las mismas zonas de serie (en decímetros).
    serie = {
        k: (round(z["x"] * 10), round(z["z"] * 10), round(z["r"] * 10))
        for k, z in mundo["zonas"].items()
    }
    assert serie == ZONES_DM


def test_solo_chocan_las_rocas_grandes(mundo: dict) -> None:
    assert mundo["assets"]["roca"]["colision"] is None
    assert mundo["assets"]["roca-grande"]["colision"]
    assert mundo["assets"]["roca"]["malla"] == mundo["assets"]["roca-grande"]["malla"]


def test_la_pagina_parte_el_mundo_igual_que_el_servidor(mundo: dict) -> None:
    # `aTrozos` y `deTrozos` de la página contra `split_world` (`docs/comparar_trozos.py`).
    spec = importlib.util.spec_from_file_location(
        "comparar_trozos", RAIZ / "docs" / "comparar_trozos.py"
    )
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    modulo.main()
