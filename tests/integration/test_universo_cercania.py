"""La página carga el mundo por cercanía (`universo/src/mundo/cercania.ts`, fase 2 de
`docs/mundo-celdas.md`).

Compila los guiones de `cercania_prueba.ts` con Vite (como `docs/volcar_mundo_serie.py`) y los
ejecuta con Node, sin navegador: con un mundo sintético de miles de celdas, la página solo tiene
las de alrededor, suelta las que deja atrás y, al cambiar de versión, solo vuelve a bajar lo que
ha cambiado.
"""

from __future__ import annotations

import importlib.util
import math
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

#: Lo que carga la página alrededor de la cámara (`DIST_CARGA` de `mundo.ts`) y su margen al soltar.
RADIO = 480
HISTERESIS = 60
CELDA = 80


def _guion(nombre: str) -> Any:
    spec = importlib.util.spec_from_file_location("volcar", RAIZ / "docs" / "volcar_mundo_serie.py")
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo.evaluar("../tests/integration/cercania_prueba.ts", f"await m.{nombre}()")


@pytest.fixture(scope="module")
def paseo() -> dict:
    return _guion("paseo")


def test_solo_baja_las_celdas_de_alrededor(paseo: dict) -> None:
    p = paseo["alPrincipio"]
    # Las celdas que tocan el círculo de 480 m: unas 130 de las 3600 del mundo.
    esperadas = math.pi * (RADIO + CELDA / 2) ** 2 / CELDA**2
    assert 0.8 * esperadas < p["celdas"] < 1.3 * esperadas
    assert p["pedidas"] == p["celdas"]  # cada una, una vez
    assert p["lejos"] <= RADIO
    assert p["objetos"] == 2 * p["celdas"]
    assert p["assets"] == 3
    # De las de más allá, hasta 900 m, solo el resumen del terreno.
    assert p["resumenes"] > p["celdas"]


def test_suelta_lo_que_deja_atras(paseo: dict) -> None:
    m = paseo["alMoverse"]
    assert m["lejos"] <= RADIO + HISTERESIS
    assert not m["cerca0"]
    assert abs(m["celdas"] - paseo["alPrincipio"]["celdas"]) < 20


def test_otra_version_solo_baja_lo_cambiado(paseo: dict) -> None:
    c = paseo["alCambiar"]
    assert c["pedidas"] == ["c/18/0"]
    assert c["viejas"] == 0


def test_si_el_servidor_ya_no_lo_tiene_pide_otro_indice(paseo: dict) -> None:
    assert paseo["avisos"] >= 1


def test_las_obras_se_sueltan_al_irse() -> None:
    o = _guion("obras")
    assert o["cargado"]["mallas"] > 0
    assert o["cargado"]["circulos"] == o["cargado"]["objetos"]
    assert o["vacio"] == {"mallas": 0, "hijos": 0, "circulos": 0}


def test_el_suelo_monta_la_isla_y_la_suelta() -> None:
    s = _guion("suelo")
    # Las 16 celdas de la isla esculpible (sin terreno en el documento: la fórmula), con su malla.
    assert s["isla"]["rejillas"] == 16
    assert s["isla"]["mallas"] >= 16
    assert s["fuera"] == {"rejillas": 0, "mallas": 0, "hijos": 0}


def test_rehacer_las_celdas_cambiadas_deja_lo_mismo_que_montar_entero() -> None:
    r = _guion("mismaEscena")
    assert r["mallas"] > 30
    assert r["iguales"], r["distintas"]
