"""Pruebas del formato y la validación del mundo editable (`bot.services.universo_editor`)."""

from __future__ import annotations

import base64
import json
import struct

import pytest

from bot.services.universo import PLACES
from bot.services.universo_editor import (
    CELL_TERRAIN_SIDE,
    EDITABLE_ZONES,
    MAX_CELL_OBJECTS,
    MAX_GLB_TRIANGLES,
    MAX_TELEPORTS,
    TERRAIN_PAINTS,
    TERRAIN_SIDE,
    MundoError,
    asset_triangles,
    encode_piece,
    glb_refs,
    glb_summary,
    is_empty,
    parse_key,
    split_world,
    validate_piece,
)
from bot.services.universo_editor import validate_world as validate_pieces

#: La celda de la farola de `world()` (x = -8, z = 60).
FAROLA = "c/-1/0"


def validate_world(doc: dict, glbs: dict[str, int]) -> dict:
    """Valida un mundo de un solo documento como lo guarda la página: partido en trozos."""
    return validate_pieces(split_world(doc), glbs)


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
    assert set(clean) == {"a/farola", FAROLA, "zonas"}
    assert clean[FAROLA]["objetos"][0]["pos"] == [-8.0, 8.0, 60.0]
    assert clean["a/farola"]["colision"] == [[0.0, 0.0, 0.3]]
    assert asset_triangles(clean["a/farola"], {}) == 48 + 168


def test_el_mundo_vacio_vale() -> None:
    assert validate_world({"v": 1, "assets": {}, "objetos": [], "zonas": {}}, {}) == {}


@pytest.mark.parametrize(
    ("cambio", "texto"),
    [
        ({"objetos": [{"asset": "nada", "pos": [0, 0, 0], "rot": [0, 0, 0], "esc": [1, 1, 1]}]},
         "no existe"),
        ({"objetos": [{"asset": "farola", "pos": [9999, 0, 0], "rot": [0, 0, 0],
                       "esc": [1, 1, 1]}]}, "fuera del mundo"),
        ({"objetos": [{"asset": "farola", "pos": [0, 0, 0], "rot": [0, 0, 0],
                       "esc": [0, 1, 1]}]}, "escala"),
        ({"objetos": [{"asset": "farola", "pos": [0, float("nan"), 0], "rot": [0, 0, 0],
                       "esc": [1, 1, 1]}]}, "no es un número"),
        ({"zonas": {"nieves": {"x": 0, "z": 0, "r": 5}}}, "no se puede mover"),
        ({"assets": {"Mal Id": {"nombre": "x", "tipo": "piezas", "piezas": []}}},
         "clave"),
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
    assert glb_refs(split_world(doc)) == {sha}
    assert asset_triangles(clean["a/roca"], {sha: 500}) == 500


def test_encode_piece_es_compacto_y_con_tope() -> None:
    assert encode_piece({"objetos": []}) == '{"objetos":[]}'
    with pytest.raises(MundoError, match="pesa"):
        encode_piece({"x": "a" * (2 * 1024 * 1024)})


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
    assert clean["a/farola"]["piezas"][0]["fase"] == 0.785
    assert asset_triangles(clean["a/farola"], {}) == 8 + 24


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
    assert clean["a/farola"]["malla"] == malla
    assert clean["a/farola"]["distancia"] == 110.0
    assert asset_triangles(clean["a/farola"], {}) == 1


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
    assert validate_world(doc, {})[FAROLA]["objetos"][0]["tinte"] == 1.1
    doc["objetos"][0]["tinte"] = 5
    with pytest.raises(MundoError, match="tinte"):
        validate_world(doc, {})


def terreno(alturas: list[int] | None = None, colores: bytes | None = None, side: int = 0) -> dict:
    """Un terreno de `side`² vértices (la isla, por defecto): todo a 0 m y con el color
    natural, salvo lo que se pase."""
    n = (side or TERRAIN_SIDE) ** 2
    h = (alturas or []) + [0] * (n - len(alturas or []))
    return {
        "alturas": base64.b64encode(struct.pack(f"<{len(h)}h", *h)).decode(),
        "colores": base64.b64encode(colores if colores is not None else bytes(n)).decode(),
    }


def heights(t: dict) -> tuple[int, ...]:
    raw = base64.b64decode(t["alturas"])
    return struct.unpack(f"<{len(raw) // 2}h", raw)


def test_el_terreno_de_la_isla_se_parte_en_sus_16_celdas() -> None:
    # Cada vértice con su propio número: así se ve de dónde sale cada uno.
    n = TERRAIN_SIDE * TERRAIN_SIDE
    isla = terreno([i % 12000 for i in range(n)], bytes(i % 14 for i in range(n)))
    pieces = split_world(world(terreno=isla))
    cells = {k: v["terreno"] for k, v in pieces.items() if "terreno" in v}
    assert sorted(cells) == sorted(f"c/{x}/{z}" for x in range(-2, 2) for z in range(-2, 2))
    side = CELL_TERRAIN_SIDE
    # La esquina -160, -160 es el primer vértice de la isla; la 160, 160, el último.
    assert heights(cells["c/-2/-2"])[0] == 0
    assert heights(cells["c/1/1"])[-1] == (n - 1) % 12000
    # La fila 3 de la celda 0, -1 empieza en la columna 80 y la fila 40 + 3 de la isla.
    assert heights(cells["c/0/-1"])[3 * side] == (43 * TERRAIN_SIDE + 80) % 12000
    # Los bordes se repiten en la celda de al lado.
    left, right = heights(cells["c/-1/0"]), heights(cells["c/0/0"])
    assert [left[k * side + side - 1] for k in range(side)] == [
        right[k * side] for k in range(side)
    ]
    clean = validate_pieces(pieces, {})
    assert clean[FAROLA]["terreno"] == cells[FAROLA]
    assert clean[FAROLA]["objetos"][0]["asset"] == "farola"
    assert "terreno" not in validate_world(world(), {})[FAROLA]


@pytest.mark.parametrize(
    ("t", "texto"),
    [
        ({"alturas": "@@", "colores": ""}, "base64"),
        ({"alturas": base64.b64encode(b"\0\0").decode(), "colores": ""}, "rejilla"),
        (terreno(side=TERRAIN_SIDE), "rejilla"),
        (terreno([12001], side=CELL_TERRAIN_SIDE), "altura"),
        (terreno([-2001], side=CELL_TERRAIN_SIDE), "altura"),
        (terreno(colores=bytes([TERRAIN_PAINTS + 1]) + bytes(CELL_TERRAIN_SIDE**2 - 1),
                 side=CELL_TERRAIN_SIDE), "paleta"),
        ({**terreno(side=CELL_TERRAIN_SIDE), "extra": 1}, "campos que no se conocen"),
    ],
)  # fmt: skip
def test_un_terreno_malo_dice_por_que(t: dict, texto: str) -> None:
    with pytest.raises(MundoError, match=texto):
        validate_piece("c/0/0", {"objetos": [], "terreno": t}, {})


def test_un_terreno_de_celda_bueno_se_guarda_tal_cual() -> None:
    t = terreno([-1000, 4200], bytes([TERRAIN_PAINTS]) + bytes(CELL_TERRAIN_SIDE**2 - 1), side=41)
    clean, refs = validate_piece("c/5/-3", {"terreno": t}, {})
    assert clean == {"objetos": [], "terreno": t}
    assert refs == frozenset()
    assert not is_empty(clean)


@pytest.mark.parametrize(
    ("key", "esperado"),
    [
        ("zonas", ("zonas", "")),
        ("a/farola", ("a", "farola")),
        ("c/-40/39", ("c", -40, 39)),
    ],
)
def test_las_claves_de_los_trozos(key: str, esperado: tuple) -> None:
    assert parse_key(key) == esperado


@pytest.mark.parametrize(
    ("key", "texto"),
    [
        ("c/40/0", "fuera del mundo"),
        ("c/0/-41", "fuera del mundo"),
        ("c/1.5/0", "clave"),
        ("a/Farola", "clave"),
        ("assets", "clave"),
        (7, "clave"),
    ],
)
def test_una_clave_mala_no_vale(key: object, texto: str) -> None:
    with pytest.raises(MundoError, match=texto):
        parse_key(key)


def objeto(x: float, z: float, asset: str = "farola") -> dict:
    return {"asset": asset, "pos": [x, 0, z], "rot": [0, 0, 0], "esc": [1, 1, 1]}


def test_cada_objeto_va_en_la_celda_de_su_posicion() -> None:
    doc = world(objetos=[objeto(0, 0), objeto(79.99, -0.01), objeto(-80, 80), objeto(0.5, 1)])
    pieces = split_world(doc)
    assert len(pieces["c/0/0"]["objetos"]) == 2
    assert set(pieces) == {"a/farola", "zonas", "c/0/0", "c/0/-1", "c/-1/1"}
    clean, refs = validate_piece("c/-1/1", pieces["c/-1/1"], {})
    assert refs == frozenset({"farola"})
    with pytest.raises(MundoError, match="dentro de su celda"):
        validate_piece("c/1/1", pieces["c/-1/1"], {})


def test_una_celda_tiene_tope_de_objetos_y_el_mundo_no() -> None:
    llena = {"objetos": [objeto(1, 1)] * MAX_CELL_OBJECTS}
    validate_piece("c/0/0", llena, {})
    with pytest.raises(MundoError, match="como mucho"):
        validate_piece("c/0/0", {"objetos": [objeto(1, 1)] * (MAX_CELL_OBJECTS + 1)}, {})
    # Muchas celdas llenas: el mundo entero no tiene tope de objetos.
    cells = {f"c/{x}/1": {"objetos": [objeto(80 * x + 1, 81)] * 1000} for x in range(-20, 20)}
    clean = validate_pieces({**split_world(world()), **cells}, {})
    assert sum(len(c.get("objetos", [])) for c in clean.values()) == 40_001


def test_un_mundo_con_objetos_de_un_asset_que_no_esta_no_vale() -> None:
    with pytest.raises(MundoError, match="no existen: pino"):
        validate_pieces({"c/0/0": {"objetos": [objeto(1, 1, "pino")]}}, {})


def test_un_trozo_vacio_se_guarda_como_borrado() -> None:
    assert is_empty(validate_piece("c/0/0", {"objetos": []}, {})[0])
    assert is_empty(validate_piece("zonas", {"zonas": {}}, {})[0])
    assert not is_empty(validate_piece("zonas", {"zonas": world()["zonas"]}, {})[0])
    with pytest.raises(MundoError, match="campos que no se conocen"):
        validate_piece("zonas", {"zonas": {}, "x": 1}, {})


def test_los_teleportes_son_un_trozo_con_nombre_y_punto() -> None:
    tps = [{"nombre": " Faro ", "x": 10, "z": -20.5}]
    clean, refs = validate_piece("teleportes", {"teleportes": tps}, {})
    assert clean == {"teleportes": [{"nombre": "Faro", "x": 10.0, "z": -20.5}]}
    assert refs == frozenset()
    assert parse_key("teleportes") == ("teleportes", "")
    # La lista vacía quiere decir «ninguno», no «los de serie»: no es un trozo vacío.
    assert not is_empty(validate_piece("teleportes", {"teleportes": []}, {})[0])
    assert split_world({**world(), "teleportes": []})["teleportes"] == {"teleportes": []}
    assert "teleportes" not in split_world(world())


@pytest.mark.parametrize(
    ("doc", "error"),
    [
        ({"teleportes": [{"nombre": "", "x": 0, "z": 0}]}, "nombre"),
        ({"teleportes": [{"nombre": "a", "x": 1e9, "z": 0}]}, "x"),
        ({"teleportes": [{"nombre": "a", "x": 0, "z": 0, "y": 3}]}, "campos que no se conocen"),
        ({"teleportes": [{"nombre": "a", "x": 0, "z": 0}] * (MAX_TELEPORTS + 1)}, "como mucho"),
        ({"teleportes": {}}, "como mucho"),
    ],
)
def test_un_teleporte_raro_no_vale(doc: dict, error: str) -> None:
    with pytest.raises(MundoError, match=error):
        validate_piece("teleportes", doc, {})
