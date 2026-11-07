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
    assert number("MAX_ASSETS") == py.MAX_ASSETS
    assert number("MAX_TRIANGULOS_GLB") == py.MAX_GLB_TRIANGLES
    assert number("MAX_NOMBRE") == py.MAX_NAME


def test_el_id_es_la_misma_expresion() -> None:
    found = re.search(r"export const ID = /\^(.*)\$/;", TS)
    assert found
    assert found.group(1) == py._ID.pattern
