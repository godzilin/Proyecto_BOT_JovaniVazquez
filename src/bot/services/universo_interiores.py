"""Interiores del Jovaniverso: entrar por una puerta, usar lo de dentro y su estado.

Un interior es un trozo del mundo (`i/<id>`, `bot.services.universo_editor`) al que lleva un
objeto con la función `puerta`. La página lo monta en el sitio de la puerta sin el mundo de
fuera; aquí solo se decide (el servidor no simula, Biblia «Jovaniverso: rendimiento
imperativo»):

- **Entrar.** Quien pide entrar tiene que estar en la sala a menos de `DOOR_REACH_DM` de una
  puerta de ese interior (`find_door`, con su pose de la sala, no con lo que diga la página).
  Dentro, la sala le pone en la capa de su **lugar** (`place_key`): solo ve y le ven los que
  están en el mismo lugar, y los de fuera no ven a los de dentro. En un interior con `piso`,
  quien tiene vivienda habitual en `pisos` entra en el suyo (`<id>@<miembro>`); el resto, en
  el piso piloto (`<id>`), que es de todos. En el extraplano de pruebas el lugar lleva delante
  `pruebas/`, para que su estado no se mezcle con el de la isla.
- **Usar.** Los usables (objetos del interior con la función `usable` y su número en
  `datos.n`) tienen un estado binario que se guarda por lugar y número
  (`bot.repositories.universo_interiores`). Un `mueble` se pone (1) y se quita (0); de serie
  está puesto. Lo de `reparar` empieza roto (0), se arregla (1) y vuelve a estar roto
  `REPAIR_SECONDS` después de arreglarse: se calcula al leerlo (`view`), sin ninguna tarea que
  lo rompa. Solo el dueño usa lo de su piso; en un lugar sin dueño, cualquiera. Hay que estar
  a `USE_REACH_DM` del usable y como mucho un uso cada `USE_SECONDS` por persona.

No toca Discord ni la base de datos: el cog (`bot.cogs.universo.interiores`) lee, guarda y
avisa a la sala.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

#: Lo más lejos de una puerta (en horizontal, dm) desde donde se puede entrar. La página
#: ofrece entrar a menos de 2,2 m; el resto cubre el retraso de la pose en la sala.
DOOR_REACH_DM = 40
#: Lo más lejos de un usable (en horizontal, dm) desde donde se puede usar (la página, 1,8 m).
USE_REACH_DM = 30
#: Segundos mínimos entre dos peticiones de la misma persona (entrar o usar).
USE_SECONDS = 0.4
#: Lo que dura arreglado algo de `reparar` antes de volver a romperse.
REPAIR_SECONDS = 24 * 3600
#: Prefijo del lugar en el extraplano de pruebas.
PRUEBAS = "pruebas/"
MUEBLE, REPARAR = "mueble", "reparar"


@dataclass(frozen=True, slots=True)
class Usable:
    """Un usable de un interior ya montado: su uso y dónde está (dm, en el mundo)."""

    uso: str
    x: int
    z: int


@dataclass(frozen=True, slots=True)
class Inside:
    """Dónde está alguien que ha entrado en un interior.

    Attributes:
        lugar: La capa de la sala y la clave de su estado (`place_key`).
        interior: El id del interior.
        owner: El dueño del lugar (su piso) o `None` si es de todos.
        usables: Sus usables por número.
        pruebas: Si es del extraplano de pruebas.
    """

    lugar: str
    interior: str
    owner: int | None
    usables: dict[int, Usable]
    pruebas: bool


def place_key(interior: str, *, owner: int | None, pruebas: bool) -> str:
    """El lugar de un interior: el piso de `owner` o el de todos, y aparte en el extraplano."""
    key = f"{interior}@{owner}" if owner is not None else interior
    return f"{PRUEBAS}{key}" if pruebas else key


def find_door(cell: dict[str, Any], interior: str, x: int, z: int) -> list[float] | None:
    """La posición (m) de la puerta de `interior` de una celda más cercana a (x, z) en dm.

    `None` si no hay ninguna a menos de `DOOR_REACH_DM`. Basta con que el objeto lleve
    `datos.interior`: solo una puerta lo lleva (el editor no lo pone en otra cosa) y lo peor
    que haría un objeto mal puesto es dejar entrar desde él.
    """
    best: tuple[float, list[float]] | None = None
    for obj in cell.get("objetos", []):
        if (obj.get("datos") or {}).get("interior") != interior:
            continue
        d = math.hypot(obj["pos"][0] * 10 - x, obj["pos"][2] * 10 - z)
        if d <= DOOR_REACH_DM and (best is None or d < best[0]):
            best = (d, obj["pos"])
    return best[1] if best else None


def usables_of(
    room: dict[str, Any], assets: dict[str, dict[str, Any]], origin: list[float]
) -> dict[int, Usable]:
    """Los usables de un interior montado en `origin` (m): los objetos con número cuyo asset
    (`assets`, id → doc) es `usable`. Uno cuyo asset no se tiene no cuenta."""
    found = {}
    for obj in room.get("objetos", []):
        n = (obj.get("datos") or {}).get("n")
        f = (assets.get(obj["asset"]) or {}).get("funcion") or {}
        if n is None or f.get("tipo") != "usable":
            continue
        x, _y, z = obj["pos"]
        found[n] = Usable(f["uso"], round((origin[0] + x) * 10), round((origin[2] + z) * 10))
    return found


def view(uso: str, saved: tuple[int, float] | None, now: float) -> int:
    """El estado que se ve de un usable: el guardado `(estado, cuándo)` o el de serie.

    Un mueble está puesto de serie; lo de reparar, roto, y vuelve a romperse
    `REPAIR_SECONDS` después de arreglarlo.
    """
    if saved is None:
        return 1 if uso == MUEBLE else 0
    state, at = saved
    if uso == REPARAR and state == 1 and now - at >= REPAIR_SECONDS:
        return 0
    return state


def next_state(uso: str, current: int) -> int | None:
    """El estado tras usarlo, o `None` si no hay nada que hacer (ya está arreglado)."""
    if uso == MUEBLE:
        return 1 - current
    return 1 if current == 0 else None


def near(x: int, z: int, usable: Usable) -> bool:
    """Si (x, z) en dm está al alcance del usable."""
    return math.hypot(usable.x - x, usable.z - z) <= USE_REACH_DM
