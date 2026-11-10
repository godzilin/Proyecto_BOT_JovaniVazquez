"""El terreno por celdas con `base` (`docs/mundo-celdas.md`): la página y el servidor leen igual.

Compila los guiones de `terreno_prueba.ts` con Vite (como `docs/volcar_mundo_serie.py`) y los
ejecuta con Node, sin navegador: el Teide a escala cabe en una celda gracias a la `base`, el
borde entre dos celdas con bases distintas casa sin escalón, lo de antes (sin `base`) se lee
igual y el servidor acepta lo que guarda el editor y resume la celda como la página.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import shutil
import struct
from pathlib import Path

import pytest

from bot.services.universo_editor import cell_summary, split_summary, validate_piece

RAIZ = Path(__file__).parents[2]
pytestmark = [
    pytest.mark.skipif(shutil.which("node") is None, reason="hace falta node"),
    pytest.mark.skipif(
        not (RAIZ / "universo" / "node_modules" / "vite").is_dir(), reason="hace falta npm ci"
    ),
]


@pytest.fixture(scope="module")
def r() -> dict:
    spec = importlib.util.spec_from_file_location("volcar", RAIZ / "docs" / "volcar_mundo_serie.py")
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo.evaluar(
        "../tests/integration/terreno_prueba.ts",
        "{teide: m.teide(), desnivel: m.desnivel(), sinBase: m.sinBase(),"
        " vecina: m.vecinaNueva(), trozos: m.trozos()}",
    )


def test_el_teide_cabe_en_una_celda_con_su_base(r: dict) -> None:
    t = r["teide"]
    assert t["fondo"] == -10  # mar sin terreno: se esculpe desde el fondo que se ve
    assert not t["recortado"]
    assert t["cima"] == pytest.approx(371, abs=0.01)
    # 371 m no caben en 16 bits de cm: las dos celdas llevan base, y distinta.
    bases = {k: v.get("base", 0) for k, v in t["terrenos"].items()}
    assert all(bases.values()) and len(set(bases.values())) == 2
    assert t["difMax"] <= 0.005  # al centímetro
    assert t["escalon"] == 0  # el borde casa aunque las bases sean distintas
    assert t["horizonte"] <= 0.005


def test_el_servidor_guarda_y_resume_la_celda_como_la_pagina(r: dict) -> None:
    for key, terreno in r["teide"]["terrenos"].items():
        _, cx, cz = key.split("/")
        clean, _ = validate_piece(key, {"terreno": terreno}, {})
        assert clean["terreno"] == terreno
        top, _, samples = split_summary(cell_summary(clean))
        assert samples == r["teide"]["resumenes"][key]
        if key == "c/11/3":  # la de la cima
            assert top == 37_100
    raw = base64.b64decode(r["teide"]["resumenes"]["c/11/3"])
    assert len(raw) == 54  # 25 muestras y la base
    assert struct.unpack_from("<i", raw, 50)[0] == r["teide"]["terrenos"]["c/11/3"]["base"]


def test_mas_de_655_m_en_una_celda_se_recorta_por_abajo(r: dict) -> None:
    d = r["desnivel"]
    assert d["recortado"]
    assert d["alto"] == 900
    assert d["bajo"] == pytest.approx(900 - 655.35)


def test_lo_de_antes_sigue_sin_base(r: dict) -> None:
    s = r["sinBase"]
    assert s["base"] is False
    assert -20 <= s["min"] and s["max"] <= 120


def test_una_celda_nueva_empieza_con_el_borde_de_su_vecina(r: dict) -> None:
    v = r["vecina"]
    assert v["escalon"] == 0
    assert v["borde"] == pytest.approx(250.5)
    assert v["dentro"] == -10


def test_el_terreno_por_celdas_pasa_a_trozos_y_vuelve(r: dict) -> None:
    t = r["trozos"]
    assert sorted(t["trozos"]) == ["c/-1/0", "c/40/-7"]
    assert "base" not in t["junto"]["c/-1/0"]
    assert t["junto"]["c/40/-7"].get("base")  # 3000 m: con base
    assert t["junto"] == {k: json.loads(v)["terreno"] for k, v in t["trozos"].items()}
    assert list(t["parcheado"]) == ["c/40/-7"]
    assert t["parcheado"]["c/40/-7"]["base"] == t["junto"]["c/40/-7"]["base"] + 100
