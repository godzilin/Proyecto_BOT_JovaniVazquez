"""Pruebas del formato y la validación del mundo editable (`bot.services.universo_editor`)."""

from __future__ import annotations

import json
import struct

import pytest

from bot.services.universo import PLACES
from bot.services.universo_editor import (
    EDITABLE_ZONES,
    EMPTY,
    MAX_GLB_TRIANGLES,
    MundoError,
    asset_triangles,
    encode_world,
    glb_refs,
    glb_summary,
    validate_world,
)


def glb(gltf: dict, *, version: int = 2) -> bytes:
    """Un GLB mínimo: cabecera y el trozo JSON (sin binario, basta para validar)."""
    body = json.dumps(gltf).encode()
    body += b" " * (-len(body) % 4)
    chunk = struct.pack("<II", len(body), 0x4E4F534A) + body
    return b"glTF" + struct.pack("<II", version, 12 + len(chunk)) + chunk


def tris(n_indices: int) -> dict:
    return {
        "accessors": [{"count": 3}, {"count": n_indices}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "nodes": [{"mesh": 0}],
    }


def world(**cambios: object) -> dict:
    doc = {
        "v": 1,
        "assets": {
            "farola": {
                "nombre": "Farola",
                "tipo": "piezas",
                "piezas": [
                    {"forma": "cilindro", "pos": [0, 1.5, 0], "rot": [0, 0, 0],
                     "esc": [0.2, 3, 0.2], "color": 0x333333},
                    {"forma": "esfera", "pos": [0, 3.1, 0], "rot": [0, 0, 0],
                     "esc": [0.5, 0.5, 0.5], "color": 0xFFE9A0},
                ],
                "colision": 0.3,
            }
        },
        "objetos": [{"asset": "farola", "pos": [-8, 8, 60], "rot": [0, 1.2, 0], "esc": [1, 1, 1]}],
        "zonas": {"faro": {"x": 100, "z": -40, "r": 9}},
    }  # fmt: skip
    doc.update(cambios)
    return doc


def test_las_zonas_movibles_son_logros_que_existen() -> None:
    assert EDITABLE_ZONES <= PLACES


def test_un_mundo_bueno_sale_igual_y_en_float() -> None:
    clean = validate_world(world(), {})
    assert clean["objetos"][0]["pos"] == [-8.0, 8.0, 60.0]
    assert clean["assets"]["farola"]["colision"] == 0.3
    assert asset_triangles(clean["assets"]["farola"], {}) == 48 + 168


def test_el_mundo_vacio_vale() -> None:
    assert validate_world(EMPTY, {}) == EMPTY


@pytest.mark.parametrize(
    ("cambio", "texto"),
    [
        ({"v": 2}, "desfasado"),
        ({"extra": 1}, "campos que no se conocen"),
        ({"objetos": [{"asset": "nada", "pos": [0, 0, 0], "rot": [0, 0, 0], "esc": [1, 1, 1]}]},
         "no existe"),
        ({"objetos": [{"asset": "farola", "pos": [9999, 0, 0], "rot": [0, 0, 0],
                       "esc": [1, 1, 1]}]}, "entre"),
        ({"objetos": [{"asset": "farola", "pos": [0, 0, 0], "rot": [0, 0, 0],
                       "esc": [0, 1, 1]}]}, "escala"),
        ({"objetos": [{"asset": "farola", "pos": [0, float("nan"), 0], "rot": [0, 0, 0],
                       "esc": [1, 1, 1]}]}, "no es un número"),
        ({"zonas": {"nieves": {"x": 0, "z": 0, "r": 5}}}, "no se puede mover"),
        ({"assets": {"Mal Id": {"nombre": "x", "tipo": "piezas", "piezas": []}}},
         "identificador"),
        ({"assets": {"a": {"nombre": "x", "tipo": "glb", "glb": "0" * 64, "colision": None}}},
         "no se ha subido"),
    ],
)  # fmt: skip
def test_un_mundo_malo_dice_por_que(cambio: dict, texto: str) -> None:
    with pytest.raises(MundoError, match=texto):
        validate_world(world(**cambio), {})


def test_una_pieza_con_forma_o_color_raro_no_vale() -> None:
    doc = world()
    doc["assets"]["farola"]["piezas"][0]["forma"] = "toro"
    with pytest.raises(MundoError, match="forma"):
        validate_world(doc, {})
    doc = world()
    doc["assets"]["farola"]["piezas"][0]["color"] = True
    with pytest.raises(MundoError, match="color"):
        validate_world(doc, {})


def test_un_asset_glb_cuenta_los_triangulos_del_modelo() -> None:
    sha = "a" * 64
    doc = world(assets={"roca": {"nombre": "Roca", "tipo": "glb", "glb": sha, "colision": None}})
    doc["objetos"][0]["asset"] = "roca"
    clean = validate_world(doc, {sha: 500})
    assert glb_refs(doc) == {sha}
    assert asset_triangles(clean["assets"]["roca"], {sha: 500}) == 500


def test_encode_world_es_compacto_y_con_tope() -> None:
    assert encode_world(EMPTY) == '{"v":1,"assets":{},"objetos":[],"zonas":{}}'
    with pytest.raises(MundoError, match="pesa"):
        encode_world({"x": "a" * (3 * 1024 * 1024)})


def test_glb_cuenta_triangulos_y_da_su_resumen() -> None:
    data = glb(tris(30))
    summary = glb_summary(data)
    assert summary.triangles == 10
    assert len(summary.sha256) == 64


def test_glb_cuenta_cada_uso_de_la_malla() -> None:
    gltf = tris(30)
    gltf["nodes"] = [{"mesh": 0}, {"mesh": 0}]
    assert glb_summary(glb(gltf)).triangles == 20


@pytest.mark.parametrize(
    ("data", "texto"),
    [
        (b"PK\x03\x04" + b"0" * 40, "no es un modelo"),
        (glb(tris(30), version=1), "glTF 2.0"),
        (glb(tris(30))[:-4], "cortado"),
        (glb({**tris(30), "extensionsRequired": ["KHR_draco_mesh_compression"]}), "Draco"),
        (glb({**tris(30), "images": [{"uri": "https://ejemplo.org/a.png"}]}), "de fuera"),
        (glb({**tris(30), "buffers": [{"uri": "a.bin", "byteLength": 4}]}), "de fuera"),
        (glb(tris(3 * (MAX_GLB_TRIANGLES + 1))), "triángulos"),
        (glb({"meshes": []}), "ningún triángulo"),
        (
            glb({"accessors": [], "meshes": [{"primitives": [{"attributes": {"POSITION": 7}}]}]}),
            "no tiene",
        ),
    ],
)
def test_glb_malo_dice_por_que(data: bytes, texto: str) -> None:
    with pytest.raises(MundoError, match=texto):
        glb_summary(data)
