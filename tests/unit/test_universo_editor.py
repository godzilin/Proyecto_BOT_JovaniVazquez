"""Pruebas del formato y la validación del mundo editable (`bot.services.universo_editor`)."""

from __future__ import annotations

import base64
import json
import struct

import pytest

from bot.services.universo import PLACES
from bot.services.universo_editor import (
    EDITABLE_ZONES,
    EMPTY,
    MAX_GLB_TRIANGLES,
    TERRAIN_PAINTS,
    TERRAIN_SIDE,
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
                "colision": [[0, 0, 0.3]],
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
    assert clean["assets"]["farola"]["colision"] == [[0.0, 0.0, 0.3]]
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


def test_un_prisma_cuenta_sus_lados_y_sus_tapas() -> None:
    tejado = {"forma": "prisma", "pos": [0, 4, 0], "rot": [0, 0, 0], "esc": [6, 1.8, 5],
              "color": 0xB5523A, "lados": 4, "arriba": 0, "abajo": 1, "fase": 0.785}  # fmt: skip
    tronco = {**tejado, "lados": 6, "arriba": 0.6, "abajo": 1, "fase": 0}
    doc = world()
    doc["assets"]["farola"]["piezas"] = [tejado, tronco]
    clean = validate_world(doc, {})
    assert clean["assets"]["farola"]["piezas"][0]["fase"] == 0.785
    assert asset_triangles(clean["assets"]["farola"], {}) == 8 + 24


@pytest.mark.parametrize(
    ("cambio", "texto"),
    [
        ({"lados": 2}, "lados"),
        ({"arriba": 0, "abajo": 0}, "alguna tapa"),
        ({"arriba": 1.5}, "entre"),
    ],
)
def test_un_prisma_malo_no_vale(cambio: dict, texto: str) -> None:
    doc = world()
    pieza = {"forma": "prisma", "pos": [0, 0, 0], "rot": [0, 0, 0], "esc": [1, 1, 1],
             "color": 0, "lados": 6, "arriba": 1, "abajo": 1, **cambio}  # fmt: skip
    doc["assets"]["farola"]["piezas"] = [pieza]
    with pytest.raises(MundoError, match=texto):
        validate_world(doc, {})


def test_los_campos_del_prisma_no_valen_en_otra_forma() -> None:
    doc = world()
    doc["assets"]["farola"]["piezas"][0]["lados"] = 6
    with pytest.raises(MundoError, match="solo valen en un prisma"):
        validate_world(doc, {})


def test_una_malla_hecha_cuenta_sus_triangulos_y_puede_ir_sin_piezas() -> None:
    malla = {"pos": [0, 0, 0, 1000, 0, 0, 0, 1000, 0], "color": [0x8A7F78] * 3}
    doc = world()
    doc["assets"]["farola"].update(piezas=[], malla=malla, distancia=110)
    clean = validate_world(doc, {})
    assert clean["assets"]["farola"]["malla"] == malla
    assert clean["assets"]["farola"]["distancia"] == 110.0
    assert asset_triangles(clean["assets"]["farola"], {}) == 1


@pytest.mark.parametrize(
    ("malla", "texto"),
    [
        ({"pos": [0] * 8, "color": [0] * 3}, "9 posiciones"),
        ({"pos": [0] * 9, "color": [0] * 2}, "un color por vértice"),
        ({"pos": [0.5] + [0] * 8, "color": [0] * 3}, "milímetros enteros"),
        ({"pos": [10**6] + [0] * 8, "color": [0] * 3}, "milímetros enteros"),
    ],
)
def test_una_malla_mala_no_vale(malla: dict, texto: str) -> None:
    doc = world()
    doc["assets"]["farola"]["malla"] = malla
    with pytest.raises(MundoError, match=texto):
        validate_world(doc, {})


def test_un_asset_sin_piezas_ni_malla_no_vale() -> None:
    doc = world()
    doc["assets"]["farola"]["piezas"] = []
    with pytest.raises(MundoError, match="piezas"):
        validate_world(doc, {})


def test_la_colision_son_circulos_con_tope() -> None:
    doc = world()
    doc["assets"]["farola"]["colision"] = [[0, 0, 1]] * 17
    with pytest.raises(MundoError, match="círculos"):
        validate_world(doc, {})
    doc["assets"]["farola"]["colision"] = [[0, 1]]
    with pytest.raises(MundoError, match=r"\[x, z, radio\]"):
        validate_world(doc, {})


def test_el_tinte_de_un_objeto_tiene_tope() -> None:
    doc = world()
    doc["objetos"][0]["tinte"] = 1.1
    assert validate_world(doc, {})["objetos"][0]["tinte"] == 1.1
    doc["objetos"][0]["tinte"] = 5
    with pytest.raises(MundoError, match="tinte"):
        validate_world(doc, {})


def terreno(alturas: list[int] | None = None, colores: bytes | None = None) -> dict:
    """Un terreno de la rejilla entera: todo a 0 m y con el color natural, salvo lo que se pase."""
    n = TERRAIN_SIDE * TERRAIN_SIDE
    h = (alturas or []) + [0] * (n - len(alturas or []))
    return {
        "alturas": base64.b64encode(struct.pack(f"<{len(h)}h", *h)).decode(),
        "colores": base64.b64encode(colores if colores is not None else bytes(n)).decode(),
    }


def test_un_terreno_esculpido_vale_y_se_guarda_tal_cual() -> None:
    t = terreno([-1000, 4200], bytes([TERRAIN_PAINTS]) + bytes(TERRAIN_SIDE**2 - 1))
    assert validate_world(world(terreno=t), {})["terreno"] == t
    assert "terreno" not in validate_world(world(), {})


@pytest.mark.parametrize(
    ("t", "texto"),
    [
        ({"alturas": "@@", "colores": ""}, "base64"),
        ({"alturas": base64.b64encode(b"\0\0").decode(), "colores": ""}, "rejilla"),
        (terreno([12001]), "altura"),
        (terreno([-2001]), "altura"),
        (terreno(colores=bytes([TERRAIN_PAINTS + 1]) + bytes(TERRAIN_SIDE**2 - 1)), "paleta"),
        ({**terreno(), "extra": 1}, "campos que no se conocen"),
    ],
)  # fmt: skip
def test_un_terreno_malo_dice_por_que(t: dict, texto: str) -> None:
    with pytest.raises(MundoError, match=texto):
        validate_world(world(terreno=t), {})
