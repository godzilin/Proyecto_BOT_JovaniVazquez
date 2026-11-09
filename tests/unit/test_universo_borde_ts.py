"""El borde y la velocidad de la página (`mundo/borde.ts`, `mundo/jugador.ts`) caben en la sala.

Si no coinciden, quien pasa el borde de la sala se queda congelado para los demás (la sala
descarta sus poses en silencio) y quien corre más de lo que acepta se ve a tirones y pierde
metros. Las constantes se leen del `.ts`; `fijarBorde` se ejecuta con Node, si lo hay.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from bot.services import universo as servicio
from bot.services import universo_editor as editor
from bot.services import universo_sala as sala

MUNDO = Path(__file__).parents[2] / "universo" / "src" / "mundo"
TS = (MUNDO / "borde.ts").read_text(encoding="utf-8")
JUGADOR = (MUNDO / "jugador.ts").read_text(encoding="utf-8")


def number(name: str, ts: str = TS) -> float:
    found = re.search(rf"(?:export )?const {name} = (-?[\d.]+);", ts)
    assert found, f"{name} no está en el .ts"
    return float(found.group(1))


def test_el_borde_de_la_pagina_es_el_de_la_sala_y_el_del_editor() -> None:
    # Las celdas que acepta el editor son las que puede pisar la página y las que acepta la sala.
    assert (number("CELDA_M"), number("LIMITE_CELDAS")) == (editor.CELL, editor.CELL_LIMIT)
    assert "MUNDO_DM = CELDA_M * LIMITE_CELDAS * 10;" in TS
    assert sala.WORLD_DM == editor.CELL * editor.CELL_LIMIT * 10
    assert (number("MIN_Y_DM"), number("MAX_Y_DM")) == (sala.MIN_Y_DM, sala.MAX_Y_DM)
    # La isla de serie son 4 × 4 celdas centradas en el 0: con su mar, el borde de siempre.
    assert 2 * editor.CELL + number("MAR") == number("SERIE")
    assert editor.parse_key(f"c/{-editor.CELL_LIMIT}/{editor.CELL_LIMIT - 1}")
    assert sala.MAX_Y_DM >= 37_180  # el Teide


def test_el_mundo_cabe_en_la_pose_y_en_los_grupos_de_la_sala() -> None:
    assert sala.WORLD_DM <= 2**31 - 1  # i32 de la página
    assert sala.MAX_Y_DM - sala.MIN_Y_DM <= 2**16 - 1  # u16 de la altura
    # Lo más lejos que se puede estar de la esquina de la región (i16) y las regiones (i16).
    assert sala.REGION_DM + sala.HYSTERESIS_DM < 2**15
    assert sala.WORLD_DM // sala.REGION_DM + 1 < 2**15
    assert sala.MAX_PLAYERS < 200  # el `u8` del hueco y los fantasmas de las devtools


def test_el_borde_sale_de_las_celdas_publicadas() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("no hay Node en el PATH")
    script = (
        "const b = await import(process.argv[1]); const out = [];"
        "b.fijarBorde([]); out.push({...b.borde});"
        "b.fijarBorde(['zonas', 'a/pino', 'c/-2/-2', 'c/1/1']); out.push({...b.borde});"
        "b.fijarBorde(['c/10/-3'], true); out.push({...b.borde});"
        "b.fijarBorde(['c/10/-3']); out.push({...b.borde});"
        "b.fijarBorde(['c/499/-500']); out.push({...b.borde});"
        "const p = {x: 9e9, y: 9e9, z: -9e9}; out.push([b.encerrar(p), p]);"
        "console.log(JSON.stringify(out));"
    )
    done = subprocess.run(
        [node, "--input-type=module", "-e", script, (MUNDO / "borde.ts").as_uri()],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    if done.returncode and "ERR_UNKNOWN_FILE_EXTENSION" in done.stderr:
        pytest.skip("este Node no ejecuta TypeScript a secas")
    assert done.returncode == 0, done.stderr
    serie = {"x0": -300, "x1": 300, "z0": -300, "z1": 300}
    limit = editor.CELL * editor.CELL_LIMIT
    first, island, grown, alone, far, (out, where) = json.loads(done.stdout)
    assert first == island == serie  # la isla de serie ya cabe en su borde
    assert grown == {"x0": -300, "x1": 11 * 80 + 140, "z0": -3 * 80 - 140, "z1": 300}
    assert alone == grown  # nunca menos que la isla de serie
    assert far == {"x0": -300, "x1": limit, "z0": -limit, "z1": 300}  # sin pasar del editor
    assert out and where == {"x": limit, "y": sala.MAX_Y_DM / 10, "z": -limit}


def test_la_sala_deja_correr_a_la_velocidad_maxima_de_la_pagina() -> None:
    top = number("VEL_MAX", JUGADOR)
    assert top == servicio.TOP_SPEED
    # Con margen para los empujones de las colisiones, pero sin dejar el doble.
    assert top * 10 < sala.RUN_DM_PER_SECOND <= top * 10 * 1.5
    # Lo más que se sube: el gancho (`GANCHO_VY`) y una ladera de pendiente 1,5 a tope.
    assert max(number("GANCHO_VY", JUGADOR), 1.5 * top) * 10 <= sala.CLIMB_DM_PER_SECOND
    # Un gancho cada `GANCHO_RECARGA` segundos como mucho.
    assert servicio.TRICKS_PER_SECOND["grapples"] == 1 / number("GANCHO_RECARGA", JUGADOR)
