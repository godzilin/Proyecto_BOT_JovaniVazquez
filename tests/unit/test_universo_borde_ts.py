"""El borde del mundo de la página (`mundo/borde.ts`) es el mismo que acepta la sala.

Si no coinciden, quien pasa el borde de la sala se queda congelado para los demás (la sala
descarta sus poses en silencio). No ejecuta Node: lee las constantes del `.ts`.
"""

from __future__ import annotations

import re
from pathlib import Path

from bot.services import universo_sala as sala

TS = (Path(__file__).parents[2] / "universo" / "src" / "mundo" / "borde.ts").read_text(
    encoding="utf-8"
)


def number(name: str) -> int:
    found = re.search(rf"export const {name} = (-?\d+);", TS)
    assert found, f"{name} no está en borde.ts"
    return int(found.group(1))


def test_el_borde_de_la_pagina_es_el_de_la_sala() -> None:
    assert number("MUNDO_DM") == sala.WORLD_DM
    assert (number("MIN_Y_DM"), number("MAX_Y_DM")) == (sala.MIN_Y_DM, sala.MAX_Y_DM)


def test_el_borde_cabe_en_el_int16_de_la_pose() -> None:
    assert max(sala.WORLD_DM, -sala.MIN_Y_DM, sala.MAX_Y_DM) <= 2**15 - 1
