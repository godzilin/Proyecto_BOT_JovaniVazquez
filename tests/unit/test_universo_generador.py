"""Generador del mundo: determinista, válido para el servidor y sin pisar lo hecho a mano."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import sys
from array import array
from pathlib import Path

import pytest

from bot.repositories.universo_mundo import Change, UniversoMundoRepository
from bot.services.universo_editor import (
    CELL,
    CELL_TERRAIN_SIDE,
    TERRAIN_STEP,
    MundoError,
    encode_piece,
    validate_piece,
    validate_world,
)
from bot.services.universo_generador import GRADO, Relieve, escribir, generar, terreno

ISLAS = Path(__file__).resolve().parents[2] / "docs" / "islas"
KIT = json.loads((ISLAS / "kit_canario.json").read_text(encoding="utf-8"))
LADO = 70  # muestras de 30 m reales: 3 m del mundo


def _isla() -> dict:
    """Las reglas de La Graciosa sobre un islote redondo y bajo de prueba."""
    isla = json.loads((ISLAS / "la_graciosa.json").read_text(encoding="utf-8"))
    isla.update(
        id="prueba",
        relieve={"archivo": "", "paso_m": 30, "sur": 0.0, "oeste": 0.0},
        origen=[800, 800],
        costa={"fondo": -10, "caida": 40, "orilla": -0.5, "tierra_min": 0.3},
    )
    # El pueblo, 30 m al este del centro.
    isla["pueblos"][0].update(lat=1050 / GRADO, lon=1350 / GRADO, radio=22)
    return isla


def _relieve() -> Relieve:
    alturas = []
    for j in range(LADO):
        for i in range(LADO):
            r = math.hypot(i - 35, j - 35)
            alturas.append(max(0.0, 30 * (1 - r / 18)))
    return Relieve(LADO, LADO, tuple(alturas))


def _trozos(semilla: int = 1, isla: dict | None = None) -> dict[str, dict]:
    return dict(generar(isla or _isla(), KIT, _relieve(), semilla))


def _alturas(doc: dict) -> array:
    h = array("h", base64.b64decode(doc["terreno"]["alturas"]))
    if sys.byteorder == "big":
        h.byteswap()
    return h


def test_misma_entrada_y_semilla_dan_los_mismos_bytes() -> None:
    uno = [(k, encode_piece(d)) for k, d in generar(_isla(), KIT, _relieve(), 7)]
    dos = [(k, encode_piece(d)) for k, d in generar(_isla(), KIT, _relieve(), 7)]
    assert uno == dos
    otra = [(k, encode_piece(d)) for k, d in generar(_isla(), KIT, _relieve(), 8)]
    assert otra != uno


def test_lo_generado_lo_acepta_el_servidor() -> None:
    trozos = _trozos()
    limpio = validate_world(trozos, {})
    celdas = [d for k, d in limpio.items() if k.startswith("c/")]
    assert celdas and all(d["gen"].startswith("prueba:") for d in celdas)
    assets = {o["asset"] for d in celdas for o in d["objetos"]}
    assert {"kit-casa-1", "kit-tabaiba"} <= assets


def test_la_firma_del_generador_se_valida() -> None:
    doc = {"objetos": [], "gen": "la-graciosa:1:abc123"}
    assert validate_piece("c/0/0", doc, {})[0]["gen"] == "la-graciosa:1:abc123"
    for malo in ("", "con espacios", 3, "x" * 81):
        with pytest.raises(MundoError):
            validate_piece("c/0/0", {"objetos": [], "gen": malo}, {})


def test_las_casas_se_apoyan_en_su_explanada() -> None:
    casas = 0
    for clave, doc in _trozos().items():
        if not clave.startswith("c/"):
            continue
        cx, cz = (int(v) for v in clave.split("/")[1:])
        h = _alturas(doc)
        for o in doc["objetos"]:
            if not o["asset"].startswith("kit-casa"):
                continue
            casas += 1
            x, y, z = o["pos"]
            i = round((x - cx * CELL) / TERRAIN_STEP)
            j = round((z - cz * CELL) / TERRAIN_STEP)
            assert abs(h[j * CELL_TERRAIN_SIDE + i] - y * 100) <= 1, o
    assert casas >= 3


def test_la_costa_casa_con_el_mar_de_serie() -> None:
    """Donde no hay celda generada al lado, el borde está en el fondo del mar de serie (-10 m)."""
    trozos = _trozos()
    celdas = {tuple(int(v) for v in k.split("/")[1:]): d for k, d in trozos.items() if k[0] == "c"}
    n = CELL_TERRAIN_SIDE
    lados = {
        (-1, 0): [j * n for j in range(n)],
        (1, 0): [j * n + n - 1 for j in range(n)],
        (0, -1): list(range(n)),
        (0, 1): [(n - 1) * n + i for i in range(n)],
    }
    for (cx, cz), doc in celdas.items():
        h = _alturas(doc)
        for (dx, dz), indices in lados.items():
            if (cx + dx, cz + dz) not in celdas:
                assert {h[k] for k in indices} == {-1000}


def test_terreno_alto_lleva_base() -> None:
    llano = terreno([100, 200], bytes(2))
    assert "base" not in llano
    alto = terreno([60_000, 61_000], bytes(2))
    assert alto["base"] == 60_500
    assert list(array("h", base64.b64decode(alto["alturas"]))) == [-500, 500]
    with pytest.raises(ValueError):
        terreno([0, 70_000], bytes(2))
    # Una celda entera en lo alto (a 600 m) la acepta el servidor.
    n = CELL_TERRAIN_SIDE * CELL_TERRAIN_SIDE
    cumbre = terreno([60_000 + k % 900 for k in range(n)], bytes(n))
    assert "base" in cumbre
    validate_piece("c/0/0", {"objetos": [], "terreno": cumbre}, {})


async def test_no_pisa_lo_hecho_a_mano(tmp_path: Path) -> None:
    repo = UniversoMundoRepository(tmp_path / "mundo.sqlite3")
    await repo.initialize()
    plan = await escribir(repo, 1, generar(_isla(), KIT, _relieve(), 1), autor=5, tanda=7)
    assert plan.crear and not plan.respetar
    borrador = await repo.draft(1)
    celdas = sorted(k for k in borrador if k.startswith("c/"))
    a_mano, borrada, tocada = celdas[0], celdas[1], "a/kit-muro"
    # Alguien retoca una celda en el editor (sin `gen`), borra otra y cambia un asset del kit.
    doc = json.loads(borrador[a_mano][0])
    del doc["gen"]
    doc["objetos"] = []
    texto_a_mano = encode_piece(doc)
    muro = json.loads(borrador[tocada][0])
    muro["nombre"] = "Muro de mi abuela"
    await repo.save(
        1,
        {
            a_mano: Change(borrador[a_mano][1], texto_a_mano, frozenset(), ""),
            borrada: Change(borrador[borrada][1], ""),
            tocada: Change(borrador[tocada][1], encode_piece(muro)),
        },
        2,
    )
    # En seco, con otra semilla: lo generado se reescribiría y lo de a mano se respeta.
    trozos = list(generar(_isla(), KIT, _relieve(), 2))
    seco = await escribir(repo, 1, trozos, autor=5, simular=True)
    assert sorted(seco.respetar) == sorted([a_mano, borrada, tocada])
    assert set(seco.reescribir) == set(celdas) - {a_mano, borrada}
    antes = await repo.draft(1)
    assert antes[a_mano][0] == texto_a_mano  # el ensayo no ha guardado nada
    de_verdad = await escribir(repo, 1, trozos, autor=5)
    assert de_verdad.respetar == seco.respetar
    despues = await repo.draft(1)
    assert despues[a_mano] == antes[a_mano] and despues[borrada] == antes[borrada]
    assert despues[tocada] == antes[tocada]
    assert all(despues[k] != antes[k] for k in de_verdad.reescribir)
    # Y otra vez lo mismo: nada que hacer.
    igual = await escribir(repo, 1, trozos, autor=5)
    assert not igual.crear and not igual.reescribir


@pytest.mark.lenta
def test_la_graciosa_entera_vale_y_es_repetible() -> None:
    sys.path.insert(0, str(ISLAS.parent))
    from generar_mundo import cargar_relieve  # noqa: PLC0415

    isla = json.loads((ISLAS / "la_graciosa.json").read_text(encoding="utf-8"))
    relieve = cargar_relieve(ISLAS / isla["relieve"]["archivo"])
    huellas = []
    for _ in range(2):
        trozos = dict(generar(copy.deepcopy(isla), KIT, relieve, 1))
        huellas.append(hashlib.sha256(json.dumps(trozos).encode()).hexdigest())
    assert huellas[0] == huellas[1]
    limpio = validate_world(trozos, {})
    alturas = [v for k, d in limpio.items() if k[0] == "c" for v in _alturas(d)]
    assert max(alturas) < 12_000  # sin `base`: el piloto no la necesita
    casas = sum(
        o["asset"].startswith("kit-casa") for d in limpio.values() for o in d.get("objetos", ())
    )
    assert casas >= 50
