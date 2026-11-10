"""El formato del editor en TypeScript (`formato.ts`) dice lo mismo que el de Python.

No ejecuta Node: lee las constantes del `.ts` con expresiones regulares.
"""

from __future__ import annotations

import re
from pathlib import Path

from bot.services import universo_editor as py

TS = (Path(__file__).parents[2] / "universo" / "src" / "editor" / "formato.ts").read_text(
    encoding="utf-8"
)


def number(name: str) -> int:
    found = re.search(rf"export const {name} = (\d+);", TS)
    assert found, f"{name} no está en formato.ts"
    return int(found.group(1))


def test_triangulos_de_cada_forma_coinciden() -> None:
    body = re.search(r"export const TRIANGULOS[^=]*= \{([^}]*)\}", TS)
    assert body
    ts = {k: int(v) for k, v in re.findall(r"(\w+): (\d+)", body.group(1))}
    assert ts == py.SHAPES


def test_topes_y_version_coinciden() -> None:
    assert number("FORMATO") == py.FORMAT
    assert number("MAX_PIEZAS") == py.MAX_PIECES
    assert number("MAX_OBJETOS_CELDA") == py.MAX_CELL_OBJECTS
    assert number("MAX_TRIANGULOS_GLB") == py.MAX_GLB_TRIANGLES
    assert number("MAX_NOMBRE") == py.MAX_NAME
    assert number("MIN_LADOS") == py.MIN_SIDES
    assert number("MAX_LADOS") == py.MAX_SIDES
    assert number("MAX_TRIANGULOS_MALLA") == py.MAX_MESH_TRIANGLES
    assert number("MAX_CIRCULOS") == py.MAX_CIRCLES
    assert number("MAX_TELEPORTES") == py.MAX_TELEPORTS
    assert number("TERRENO_LADO") == py.TERRAIN_EXTENT
    assert number("TERRENO_CELDAS") == py.TERRAIN_CELLS
    assert number("TERRENO_PINTURAS") == py.TERRAIN_PAINTS
    assert number("CELDA") == py.CELL
    assert number("MAX_TRAGAS") == py.MAX_SLOTS
    assert number("MAX_BOTES_LETRERO") == py.MAX_SIGN_POTS
    assert number("MAX_TITULO") == py.MAX_SIGN_TITLE
    assert number("MAX_LADO_LETRERO") == py.MAX_SIGN_SIDE
    assert number("MAX_USABLES") == py.MAX_USABLES
    assert number("MAX_OBJETOS_INTERIOR") == py.MAX_ROOM_OBJECTS
    assert (number("MIN_LADO_INTERIOR"), number("MAX_LADO_INTERIOR")) == (
        py.MIN_ROOM_SIDE,
        py.MAX_ROOM_SIDE,
    )
    assert (number("MIN_ALTO_INTERIOR"), number("MAX_ALTO_INTERIOR")) == (
        py.MIN_ROOM_HEIGHT,
        py.MAX_ROOM_HEIGHT,
    )


def test_tinte_y_distancia_coinciden() -> None:
    def real(name: str) -> float:
        found = re.search(rf"export const {name} = (\d+(?:\.\d+)?);", TS)
        assert found, f"{name} no está en formato.ts"
        return float(found.group(1))

    assert (real("MIN_TINTE"), real("MAX_TINTE")) == (py.MIN_TINT, py.MAX_TINT)
    assert (real("MIN_DISTANCIA"), real("MAX_DISTANCIA")) == (py.MIN_DISTANCE, py.MAX_DISTANCE)


def test_triangulos_del_prisma_salen_de_la_misma_cuenta() -> None:
    # `triangulosPieza` de TS y `piece_triangles` de Python: 2 * lados * tapas con radio.
    assert re.search(r"2 \* \(p\.lados \?\? 0\) \* \(Number\(\(p\.arriba", TS)
    prism = {"forma": "prisma", "lados": 6, "arriba": 1, "abajo": 0}
    assert py.piece_triangles(prism) == 12


def test_el_id_es_la_misma_expresion() -> None:
    found = re.search(r"export const ID = /\^(.*)\$/;", TS)
    assert found
    assert found.group(1) == py._ID.pattern
