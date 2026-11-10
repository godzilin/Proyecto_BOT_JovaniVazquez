"""El editor carga y guarda solo las celdas que se ven o se tocan (`editor/borrador.ts`).

Compila los guiones de `borrador_celdas_prueba.ts` con Vite (como `test_universo_cercania.py`)
y los ejecuta con Node contra un servidor falso del borrador: abrir no baja las celdas, la
cámara baja las de alrededor y suelta las lejanas sin cambios, y ningún cambio pisa una celda
que no se ha bajado. Las rutas de verdad, en `test_universo_editor_bot.py`.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from typing import Any

import pytest

RAIZ = Path(__file__).parents[2]
pytestmark = [
    pytest.mark.lenta,
    pytest.mark.skipif(shutil.which("node") is None, reason="hace falta node"),
    pytest.mark.skipif(
        not (RAIZ / "universo" / "node_modules" / "vite").is_dir(), reason="hace falta npm ci"
    ),
]


def _guion(nombre: str) -> Any:
    spec = importlib.util.spec_from_file_location("volcar", RAIZ / "docs" / "volcar_mundo_serie.py")
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo.evaluar("../tests/integration/borrador_celdas_prueba.ts", f"await m.{nombre}()")


def test_abrir_no_crece_con_el_mundo() -> None:
    r = _guion("abrirSegunTamano")
    chico, grande = r["10"], r["60"]
    # Solo las 16 celdas de la isla, en un mundo de 100 celdas y en uno de 3600.
    assert chico["celdasPedidas"] == grande["celdasPedidas"] == 16
    assert chico["objetos"] == grande["objetos"] == 32
    # Lo que crece es el índice: unos 20 bytes por celda.
    assert grande["bytes"] - chico["bytes"] < 25 * (3600 - 100)


@pytest.fixture(scope="module")
def paseo() -> dict:
    return _guion("paseo")


def test_la_camara_baja_las_de_alrededor_y_suelta_las_lejanas(paseo: dict) -> None:
    centro = paseo["centro"]
    assert 100 < centro["celdas"]["bajadas"] < 200
    assert centro["celdas"]["bajadas"] + centro["celdas"]["sinBajar"] == 3600
    assert centro["objetos"] == 2 * centro["celdas"]["bajadas"]
    tras = paseo["tras"]
    # Quedan la isla y las celdas del historial (la del objeto movido y la lejana).
    assert tras["celdas"]["bajadas"] <= 18
    assert tras["sigueLejos"]
    # El objeto movido sale de una celda y entra en la lejana: la cuenta sigue cuadrando.
    assert paseo["vuelta"]["objetos"] == 2 * paseo["vuelta"]["celdas"]["bajadas"]


def test_un_cambio_no_pisa_una_celda_sin_bajar(paseo: dict) -> None:
    assert paseo["rechazado"]
    assert paseo["vuelto"]
    assert paseo["avisado"]
    # Ya bajada, el mismo cambio vale y guarda solo las dos celdas, con lo que tenía la lejana.
    assert paseo["aceptado"]
    assert paseo["guardado"] == ["c/-1/-2", "c/20/0"]  # la celda de origen y la lejana
    assert paseo["enLejos"] == 3
    assert paseo["deshecho"]
    assert paseo["descuadres"] == 0


def test_el_servidor_dice_quien_usa_un_asset(paseo: dict) -> None:
    # a1 lo usa una de cada tres celdas; casi todas siguen sin bajar.
    assert paseo["usos"] > 1000


def test_conserva_lo_que_no_conoce_y_quita_gen_al_tocarla() -> None:
    r = _guion("camposAjenos")
    assert r["claves"] == ["c/3/0"]
    celda = r["celda"]["doc"]
    assert "gen" not in celda
    assert celda["otro"] == {"x": 1}
    assert celda["terreno"] == {"alturas": "AAAA", "colores": "AA"}
    assert r["celda"]["rev"] == 4
