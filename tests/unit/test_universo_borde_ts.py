"""El borde y la velocidad de la página (`mundo/borde.ts`, `mundo/jugador.ts`) caben en la sala.

Si no coinciden, quien pasa el borde de la sala se queda congelado para los demás (la sala
descarta sus poses en silencio) y quien corre más de lo que acepta se ve a tirones y pierde
metros. No ejecuta Node: lee las constantes del `.ts`.
"""

from __future__ import annotations

import re
from pathlib import Path

from bot.services import universo as servicio
from bot.services import universo_sala as sala

MUNDO = Path(__file__).parents[2] / "universo" / "src" / "mundo"
TS = (MUNDO / "borde.ts").read_text(encoding="utf-8")
JUGADOR = (MUNDO / "jugador.ts").read_text(encoding="utf-8")


def number(name: str, ts: str = TS) -> float:
    found = re.search(rf"(?:export )?const {name} = (-?[\d.]+);", ts)
    assert found, f"{name} no está en el .ts"
    return float(found.group(1))


def test_el_borde_de_la_pagina_es_el_de_la_sala() -> None:
    assert number("MUNDO_DM") == sala.WORLD_DM
    assert (number("MIN_Y_DM"), number("MAX_Y_DM")) == (sala.MIN_Y_DM, sala.MAX_Y_DM)


def test_el_borde_cabe_en_el_int16_de_la_pose() -> None:
    assert max(sala.WORLD_DM, -sala.MIN_Y_DM, sala.MAX_Y_DM) <= 2**15 - 1


def test_la_sala_deja_correr_a_la_velocidad_maxima_de_la_pagina() -> None:
    top = number("VEL_MAX", JUGADOR)
    assert top == servicio.TOP_SPEED
    # Con margen para los empujones de las colisiones, pero sin dejar el doble.
    assert top * 10 < sala.RUN_DM_PER_SECOND <= top * 10 * 1.5
    # Lo más que se sube: el gancho (`GANCHO_VY`) y una ladera de pendiente 1,5 a tope.
    assert max(number("GANCHO_VY", JUGADOR), 1.5 * top) * 10 <= sala.CLIMB_DM_PER_SECOND
    # Un gancho cada `GANCHO_RECARGA` segundos como mucho.
    assert servicio.TRICKS_PER_SECOND["grapples"] == 1 / number("GANCHO_RECARGA", JUGADOR)
