"""Las constantes del protocolo y de los lugares que están a la vez en Python y en la página.

La pose (`universo/src/sala.ts`), las animaciones (`mundo/jugador.ts`, `mundo/gestos.ts`,
`mundo/remotos.ts`) y los lugares de los logros (`mundo/lugares.ts`) se escriben a mano en los
dos lados. Si no coinciden, los demás ven a alguien congelado (la sala descarta su pose), con el
gesto equivocado o sin logro de un lugar que sí ha pisado. Sin Node: lee las constantes del
`.ts`, exportadas con un nombre fijo. Los centros de los lugares y la cima, que salen de
fórmulas, los compara con Node `tests/integration/test_universo_mundo_serie.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

from bot.services import universo as servicio
from bot.services import universo_editor as editor
from bot.services import universo_sala as sala
from bot.services.achievements import UNIVERSO_PLACES

SRC = Path(__file__).parents[2] / "universo" / "src"


def ts(path: str) -> str:
    return (SRC / path).read_text(encoding="utf-8")


def const(name: str, code: str) -> str:
    found = re.search(rf"export const {name}(?:: [^=]+)? = (.+?);", code)
    assert found, f"{name} no está exportada en el .ts"
    return found.group(1)


def pairs(text: str) -> dict[str, int]:
    """`{ Idle: 0, 11: "Agachado" }` → `{"Idle": 0}` y al revés: clave → número."""
    out = {k: int(v) for k, v in re.findall(r"(\w+): (\d+)\b", text)}
    out |= {v: int(k) for k, v in re.findall(r"(\d+): \"(\w+)\"", text)}
    return out


def test_la_pose_mide_y_se_llama_igual_en_los_dos_lados() -> None:
    code = ts("sala.ts")
    assert int(const("POSE", code)) == sala.POSE
    size = int(const("POSE_BYTES", code))
    # Lo que manda la página (con su tipo) y lo que manda la sala por jugador (con su hueco).
    assert size == sala._POSE.size == sala._ENTRY.size
    assert sala._HEAD.size == 2  # «2 + i * POSE_BYTES» en sala.ts


def test_los_gestos_son_los_de_la_tienda() -> None:
    code = ts("mundo/gestos.ts")
    first = int(const("PRIMER_GESTO", code))
    assert first == sala.FIRST_GESTURE
    ids = [int(n) for n in re.findall(r"\{ id: (\d+),", code)]
    assert ids == sorted(servicio.GESTURES) == list(range(first, first + len(ids)))


def test_las_animaciones_de_moverse_son_las_de_la_sala() -> None:
    code = ts("mundo/jugador.ts")
    anims = pairs(const("ANIMS", code))
    posturas = pairs(const("POSTURAS", code))
    assert (anims["Idle"], anims["Walking"], anims["Running"]) == (0, 1, 2)
    assert (anims["Jump"], anims["Deslizar"]) == (sala.JUMP, sala.SLIDE)
    # Las posturas son de todos (`MOVES`), van después de los gestos y cada una tiene su id.
    assert set(posturas.values()) == sala.MOVES
    assert all(anims[name] == anim for name, anim in posturas.items())
    assert max(anims["Jump"], *servicio.GESTURES) < min(sala.MOVES)
    # Los remotos cargan sus cuatro clips de base en el orden de sus ids.
    nombres = re.search(r"const NOMBRES = \[(.+?)\]", ts("mundo/remotos.ts"))
    assert nombres
    assert [anims[n] for n in re.findall(r'"(\w+)"', nombres.group(1))] == [0, 1, 2, 3]


def test_los_lugares_son_los_de_los_logros_y_los_del_editor() -> None:
    # Cada lugar tiene su logro y su forma de pisarse en la sala (`Places.at`).
    assert {place for place, *_ in UNIVERSO_PLACES} == servicio.PLACES
    assert servicio.PLACES == set(servicio.ZONES_DM) | {"nieves", "bandama", "muelle", "agua"}
    # Los círculos que mueve el editor tienen el mismo radio de serie en la página.
    base = re.search(r"const BASE[^=]*= \{(.+?)\n\};", ts("mundo/lugares.ts"), re.S)
    assert base
    radios = dict(re.findall(r"(\w+): \{[^}]*r: (\d+) \}", base.group(1)))
    assert set(radios) == set(servicio.ZONES_DM) == editor.EDITABLE_ZONES
    assert {k: int(r) * 10 for k, r in radios.items()} == {
        k: r for k, (_x, _z, r) in servicio.ZONES_DM.items()
    }
