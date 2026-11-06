"""Lo que se ve en cada fotograma del backgammon y su dibujo de reserva con Pillow.

Python decide todo lo que se ve: dónde está cada ficha en su vuelo (levantarse,
arco, posarse), cómo ruedan y se paran los dados, cómo gira el cubo y el cartel
del final. La escena de canvas (`assets/backgammon/escena.html`, pintada por
`bot.services.backgammon_scene`) solo pinta estos estados; si no hay Node ni
navegador, `BackgammonRenderer` los pinta con Pillow, más sencillos.

**La mesa.** Vista desde las blancas, que juegan abajo. La fila de abajo, de
derecha a izquierda, es 0..5 (la mitad derecha, su casa), la barra y 6..11; la
de arriba, de izquierda a derecha, 12..17, la barra y 18..23. La bandeja está a
la derecha: las blancas sacan abajo y las negras, arriba. Los dados de las
blancas caen en la mitad derecha y los de las negras, en la izquierda (la que
tiene cada jugador a su derecha, como en una mesa de verdad). Hasta 5 fichas en
un punto van a paso completo; con más se comprimen y la de arriba lleva el número.

**Lo fijo** (`meta_state`): los dos jugadores (nombre y una línea debajo), el
título, el subtítulo, la semilla (del confeti: la madera y el fieltro son los
mismos en todas las partidas), la skin del fieltro y la geometría (`GEOMETRY`),
la misma para la escena y para Pillow.

**Cada fotograma** (`board_state` y los de las animaciones):

- `points`, `bar`, `off`: la posición (`points` positivo son blancas; `bar` y
  `off` son `[blancas, negras]`).
- `pips`: la cuenta de pips de `[blancas, negras]` (se ve en las tarjetas).
- `turn`: a quién le toca (1 blancas, 2 negras, 0 nadie): se ilumina su tarjeta.
- `labels`: desde qué color se numeran los puntos (1 blancas, 2 negras).
- `marks`: casillas resaltadas (la última jugada); 24 es la barra y 25, la bandeja.
  Solo las lleva el estado final de una jugada, no los fotogramas del camino.
- `dice`: `{"x", "y", "rot", "z", "v", "sx", "m", "side", "used"}` por dado. `z` es lo
  alto que va (0 en la mesa), `sx` el aplastamiento al dar vueltas (lo usa Pillow),
  `m` su orientación en 3D (la matriz de `craps_render`, por filas: la escena pinta
  sus caras como las del craps) y `used` lo apaga.
- `cube`: `{"v", "owner", "x", "y", "scale", "glow", "flip", "lift"}`.
- `flying`: fichas en el aire, `{"x", "y", "h", "side"}`: `(x, y)` es su sombra en
  la mesa y `h` lo alto que vuela, en píxeles.
- `banner` (`{"title", "sub", "kind", "a", "y"}`, como en el 4 en raya) y
  `confetti` (`{"t", "side"}`).
"""

from __future__ import annotations

import io
import math
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw, ImageFont

# Reutilizados del 4 en raya, no copiados: el cog los trata igual en los dos.
from bot.services.backgammon import (
    BAR,
    OFF,
    POINTS,
    Position,
    Side,
    Step,
    dice_of,
    point_number,
)
from bot.services.craps_render import Mat, face_up, rotation, value_up
from bot.services.craps_render import _mul as mat_mul
from bot.services.enraya_render import (
    TEXT_FONT,
    TITLE_FONT,
    Media,
    Player,
    banner,
    canvas_text,
    encode,
)

__all__ = [
    "GEOMETRY",
    "BackgammonRenderer",
    "Cube",
    "H",
    "Media",
    "Player",
    "W",
    "banner",
    "board_state",
    "canvas_text",
    "chain",
    "cube_steps",
    "encode",
    "end_steps",
    "meta_state",
    "move_steps",
    "opening_steps",
    "rest_dice",
    "roll_steps",
]

W, H = 800, 572

#: Todo en píxeles de la imagen. Lo lee la escena (`meta["geom"]`) y Pillow.
GEOMETRY: dict[str, Any] = {
    "w": W,
    "h": H,
    "frame": [10, 72, 790, 562],  # marco de madera: x0, y0, x1, y1
    "felt": [24, 94, 706, 540],  # el tapete donde están los puntos
    "wall": [706, 718],  # el listón entre el tapete y la bandeja
    "tray": [718, 94, 776, 540],  # la bandeja forrada
    "pw": 52,  # ancho de un punto
    "left": 30,  # donde empieza la mitad izquierda
    "right": 388,  # donde empieza la mitad derecha
    "bar": [342, 388],  # la barra
    "bar_x": 365,
    "mid": 317,  # la línea entre las dos filas
    "top": 96,  # base de los puntos de arriba
    "bottom": 538,  # base de los puntos de abajo
    "length": 200,  # largo de un punto
    "r": 21,  # radio de una ficha
    "step": 40,  # paso entre fichas de un montón de hasta 5
    "bar_inner": 43,  # de la línea del medio a la primera ficha de la barra
    "bar_span": 120,  # lo que ocupan, como mucho, las fichas de la barra desde la primera
    "tray_x": 747,  # centro de la bandeja
    "slab": [42, 9, 10],  # ficha de canto en la bandeja: ancho, alto y paso
    "die": 34,  # lado de un dado
    "cube": 36,  # lado del cubo
    "cards": [12, 8, 244, 58],  # tarjeta de las blancas (x, y, ancho, alto); la otra, a la derecha
    "banner_y": 317,
}

#: El último fotograma se queda quieto (el GIF no se repite): lo pone `encode`.
FRAME_MS = 40
#: Lo que se queda quieta la ficha al posarse, antes del siguiente paso.
LAND_MS = 70
#: Fotogramas de la tirada de dados (el último, el reposo, es el estado final).
ROLL_FRAMES = 19
#: Lo que se levanta una ficha antes de volar, y su altura máxima de arco, en píxeles.
LIFT_PX = 12
MAX_ARC_PX = 44
#: Fotogramas del cartel al aparecer y del brillo y el confeti al ganar.
BANNER_FRAMES = 8
WIN_FRAMES = 26
WIN_FRAME_MS = 45
#: Cuánto dura la pausa entre los dos dados de la salida y juntarse.
OPENING_PAUSE_MS = 520

_MID = GEOMETRY["mid"]
_PW = GEOMETRY["pw"]
_STEP = GEOMETRY["step"]
_R = GEOMETRY["r"]
_DIE = GEOMETRY["die"]
#: Altura a la que se inclinan los dados quietos (radianes) y su desvío vertical.
_TILTS = (-0.07, 0.05, 0.09, -0.04)
_DROPS = (-3, 3, -2, 2)


@dataclass(frozen=True, slots=True)
class Cube:
    """El cubo de doblar: su valor, quién lo tiene (`None` si nadie) y si está ofrecido."""

    value: int = 1
    owner: Side | None = None
    offered: bool = False


#: El cubo sin doblar (valor 1, de nadie), el de serie de las animaciones.
NO_CUBE = Cube()


def canvas_name(player: Player) -> dict[str, str]:
    """Nombre y línea de un jugador sin emojis, como los pinta la escena."""
    return {"name": canvas_text(player.name), "tag": canvas_text(player.tag)}


def meta_state(
    white: Player,
    black: Player,
    *,
    title: str,
    subtitle: str,
    seed: int,
    skin: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Lo fijo de la partida, tal como lo lee la escena (nombres sin emojis).

    Args:
        white: Quien juega con blancas.
        black: Quien juega con negras.
        title: El título de arriba.
        subtitle: La línea de debajo del título.
        seed: Semilla del confeti (y de cualquier azar que añada la escena).
        skin: La skin del tapete (`bot.services.skins`), tal cual; `None` es la de serie.
    """
    return {
        "white": canvas_name(white),
        "black": canvas_name(black),
        "title": title,
        "subtitle": subtitle,
        "seed": seed,
        "geom": dict(GEOMETRY),
        "skin": dict(skin) if skin is not None else None,
    }


# -- Dónde va cada cosa -----------------------------------------------------------------------


def point_x(index: int) -> float:
    """El centro horizontal del punto `index` (0..23)."""
    column = 11 - index if index < 12 else index - 12
    base = GEOMETRY["left"] if column < 6 else GEOMETRY["right"]
    return base + (column % 6 + 0.5) * _PW


def stack_xy(index: int, k: int, n: int) -> tuple[float, float]:
    """El centro de la ficha `k` (0 la de abajo) de un montón de `n` en el punto `index`."""
    step = _STEP if n <= 5 else 4 * _STEP / (n - 1)
    if index < 12:
        return point_x(index), GEOMETRY["bottom"] - _R - k * step
    return point_x(index), GEOMETRY["top"] + _R + k * step


def bar_xy(side: Side, k: int, n: int) -> tuple[float, float]:
    """El centro de la ficha `k` de las `n` que `side` tiene en la barra.

    Se apilan desde el centro hacia su lado: las blancas hacia abajo y las negras
    hacia arriba.
    """
    step = _STEP if n <= 4 else GEOMETRY["bar_span"] / (n - 1)
    offset = GEOMETRY["bar_inner"] + k * step
    return GEOMETRY["bar_x"], _MID + offset if side is Side.WHITE else _MID - offset


def off_xy(side: Side, k: int) -> tuple[float, float]:
    """El centro de la ficha `k` que `side` lleva sacada, de canto en la bandeja."""
    _width, height, pace = GEOMETRY["slab"]
    y0, y1 = GEOMETRY["tray"][1], GEOMETRY["tray"][3]
    if side is Side.WHITE:
        return GEOMETRY["tray_x"], y1 - 5 - height / 2 - k * pace
    return GEOMETRY["tray_x"], y0 + 5 + height / 2 + k * pace


def half_center(side: Side) -> float:
    """El centro horizontal de la mitad donde cae la tirada de `side`."""
    if side is Side.WHITE:
        return GEOMETRY["right"] + 3 * _PW
    return GEOMETRY["left"] + 3 * _PW


def cube_xy(cube: Cube) -> tuple[float, float]:
    """Dónde está el cubo: en medio de la barra, en la del que lo tiene o ofrecido."""
    if cube.offered or cube.owner is None:
        return GEOMETRY["bar_x"], float(_MID)
    edge = GEOMETRY["felt"][2] - 28 if cube.owner is Side.WHITE else GEOMETRY["left"] + 28
    return float(edge), float(_MID)


def cube_state(
    cube: Cube,
    *,
    x: float | None = None,
    y: float | None = None,
    value: int | None = None,
    lift: float = 0.0,
    flip: float = 0.0,
    glow: float | None = None,
) -> dict[str, Any]:
    """El cubo tal como lo pinta la escena (con 1 enseña 64, como uno de verdad)."""
    at = cube_xy(cube)
    shown = cube.value if value is None else value
    return {
        "v": 64 if shown == 1 else shown,
        "owner": cube.owner.value if cube.owner is not None else 0,
        "x": round(at[0] if x is None else x, 2),
        "y": round(at[1] if y is None else y, 2),
        "scale": 1.22 if cube.offered else 1.0,
        "glow": (1.0 if cube.offered else 0.0) if glow is None else round(glow, 3),
        "flip": round(flip, 3),
        "lift": round(lift, 3),
    }


# -- Dados ------------------------------------------------------------------------------------


def _flat(m: Mat) -> list[float]:
    return [round(c, 3) for row in m for c in row]


def _die(side: Side, value: int, x: float, y: float, i: int, *, used: bool = False) -> dict:
    return {
        "x": round(x, 2),
        "y": round(y, 2),
        "rot": _TILTS[i % 4],
        "z": 0.0,
        "v": value,
        "sx": 1.0,
        # En pantalla `y` baja y en el mundo del craps va al fondo: el giro cambia de signo.
        "m": _flat(face_up(value, -_TILTS[i % 4])),
        "side": side.value,
        "used": used,
    }


def _rest_slots(side: Side, count: int) -> list[tuple[float, float]]:
    """Dónde se paran `count` dados en la mitad de `side`."""
    centre = half_center(side)
    span = 46
    xs = [centre + (i - (count - 1) / 2) * span for i in range(count)]
    return [(x, _MID + _DROPS[i % 4]) for i, x in enumerate(xs)]


def _dim_dice(dice: list[dict], spent: Iterable[int]) -> list[dict]:
    """Copia de `dice` con apagados los de los índices `spent`."""
    off = set(spent)
    return [{**die, "used": die["used"] or i in off} for i, die in enumerate(dice)]


def rest_dice(side: Side, roll: tuple[int, int], used: int = 0) -> list[dict]:
    """Los dados quietos en la mitad de `side`; los `used` primeros, apagados.

    Un doble son cuatro dados (cada paso gasta uno, como en `dice_of`).
    """
    values = dice_of(roll)
    slots = _rest_slots(side, len(values))
    dice = [_die(side, v, x, y, i) for i, (v, (x, y)) in enumerate(zip(values, slots, strict=True))]
    return _dim_dice(dice, range(used))


# -- Estados ----------------------------------------------------------------------------------


def board_state(
    position: Position,
    *,
    turn: Side | None = None,
    dice: list[dict] | None = None,
    cube: Cube = NO_CUBE,
    labels: Side = Side.WHITE,
    marks: Iterable[int] = (),
    end: dict[str, Any] | None = None,
    confetti_t: float | None = None,
    flying: list[dict] | None = None,
    winner: Side | None = None,
) -> dict[str, Any]:
    """Un fotograma quieto: la posición con lo que haya encima.

    Args:
        position: Las fichas (con sus pips, que se ven en las tarjetas).
        turn: A quién le toca (se ilumina su tarjeta); `None` si nadie.
        dice: Los dados que se ven (`rest_dice`, o los de una tirada).
        cube: El cubo de doblar.
        labels: Desde qué color se numeran los puntos 1 a 24 de alrededor.
        marks: Casillas (0..23, `BAR`, `OFF`) resaltadas, la última jugada.
        end: Cartel del final (`banner`).
        confetti_t: Avance del confeti (0 a 1), si lo hay.
        flying: Fichas en el aire (ver la cabecera).
        winner: Quién gana, para el color del confeti (si no, el de la posición).
    """
    confetti = None
    if confetti_t is not None:
        winning = winner or position.winner
        confetti = {"t": round(confetti_t, 3), "side": winning.value if winning else 0}
    return {
        "points": list(position.points),
        "bar": list(position.bar),
        "off": list(position.off),
        "pips": _pips(position),
        "turn": turn.value if turn is not None else 0,
        "labels": labels.value,
        "marks": sorted({m for m in marks if 0 <= m <= OFF}),
        "dice": dice if dice is not None else [],
        "cube": cube_state(cube),
        "flying": flying,
        "banner": {**end, "y": GEOMETRY["banner_y"]} if end is not None else None,
        "confetti": confetti,
    }


def _with(state: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return {**state, **changes}


def _remove(position: Position, side: Side, source: int) -> Position:
    """La posición con la ficha de `source` en el aire (aún no está en ningún sitio)."""
    sign = 1 if side is Side.WHITE else -1
    me = 0 if side is Side.WHITE else 1
    points, bar = list(position.points), list(position.bar)
    if source == BAR:
        bar[me] -= 1
    else:
        points[source] -= sign
    return Position(tuple(points), (bar[0], bar[1]), position.off)


def _unhit(position: Position, side: Side) -> Position:
    """La posición de después de comer, con la ficha rival aún en el aire camino de la barra.

    `position` es la que sale de aplicar el paso, con la rival ya en la barra.
    """
    other = 0 if side.other is Side.WHITE else 1
    bar = list(position.bar)
    bar[other] -= 1
    return Position(position.points, (bar[0], bar[1]), position.off)


def _pips(position: Position) -> list[int]:
    return [position.pip(Side.WHITE), position.pip(Side.BLACK)]


def _smooth(t: float) -> float:
    return t * t * (3 - 2 * t)


def _flight(
    side: Side,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    lift_frames: int = 2,
    land_frames: int = 2,
) -> list[dict]:
    """Las fichas en el aire de un vuelo: se levanta, cruza en arco y se posa.

    Cada elemento es el `flying` de un fotograma (una lista con una ficha).
    """
    distance = math.hypot(end[0] - start[0], end[1] - start[1])
    travel = max(5, min(9, 4 + round(distance / 70)))
    arc = min(MAX_ARC_PX, 14 + 0.12 * distance)
    flights: list[dict] = []

    def at(x: float, y: float, h: float) -> dict:
        return {"x": round(x, 2), "y": round(y, 2), "h": round(h, 2), "side": side.value}

    for i in range(1, lift_frames + 1):
        flights.append(at(*start, LIFT_PX * i / lift_frames))
    for i in range(1, travel + 1):
        t = i / (travel + 1)
        e = _smooth(t)
        x = start[0] + (end[0] - start[0]) * e
        y = start[1] + (end[1] - start[1]) * e
        flights.append(at(x, y, LIFT_PX + arc * math.sin(math.pi * t)))
    for i in range(1, land_frames + 1):
        flights.append(at(*end, LIFT_PX * (1 - i / (land_frames + 1))))
    return flights


def _steps_of(
    base: dict[str, Any], flights: list[dict], ms: int = FRAME_MS
) -> list[tuple[dict, int]]:
    return [(_with(base, flying=[flight]), ms) for flight in flights]


def _end_frames(
    still: dict[str, Any], end: dict[str, Any], winner: Side | None
) -> list[tuple[dict, int]]:
    """Los fotogramas del cartel al aparecer (y, si hay ganador, el confeti)."""
    frames: list[tuple[dict, int]] = []
    count = WIN_FRAMES if winner is not None else BANNER_FRAMES
    for frame in range(count):
        t = (frame + 1) / count
        if winner is not None:
            a = min(1.0, max(0.0, (t - 0.18) / 0.4))
        else:
            a = t
        shown = {**end, "a": round(a, 3), "y": GEOMETRY["banner_y"]}
        changes: dict[str, Any] = {"banner": shown if a > 0 else None}
        if winner is not None:
            changes["confetti"] = {"t": round(t, 3), "side": winner.value}
        frames.append((_with(still, **changes), WIN_FRAME_MS))
    return frames


def _final_end(still: dict[str, Any], end: dict[str, Any]) -> dict[str, Any]:
    return _with(still, banner={**end, "a": 1.0, "y": GEOMETRY["banner_y"]}, confetti=None)


def move_steps(
    position: Position,
    side: Side,
    steps: Sequence[Step],
    roll: tuple[int, int],
    *,
    used_before: int = 0,
    cube: Cube = NO_CUBE,
    labels: Side = Side.WHITE,
    end: dict[str, Any] | None = None,
    winner: Side | None = None,
) -> tuple[list[tuple[dict, int]], dict[str, Any]]:
    """Todos los fotogramas de una jugada: ficha a ficha, con la comida y la bandeja.

    Cada paso levanta la ficha de arriba del montón de origen, la lleva en arco y
    la posa arriba del de destino; si come, la rival vuela a la barra justo
    después, y si saca, entra en la bandeja. El dado gastado se apaga al acabar
    el paso (el que vale lo que `step.die`; los `used_before` primeros ya estaban
    apagados). Con `end`, al final aparece el cartel y, con `winner`, el confeti.

    Returns:
        Los fotogramas `(estado, ms)` y el estado final, quieto. Los pasos se
        aplican con `Position.apply`: tienen que venir legales y en orden.
    """
    common: dict[str, Any] = {"cube": cube, "labels": labels}
    dice = rest_dice(side, roll, used_before)
    frames: list[tuple[dict, int]] = []
    current = position
    marks: set[int] = set()
    for step in steps:
        after = current.apply(side, step)
        hit = step.target != OFF and current.count(side.other, step.target) == 1
        if step.source == BAR:
            n = current.on_bar(side)
            start = bar_xy(side, n - 1, n)
        else:
            n = current.count(side, step.source)
            start = stack_xy(step.source, n - 1, n)
        if step.target == OFF:
            end_at = off_xy(side, current.borne_off(side))
        else:
            landing = after.count(side, step.target)
            end_at = stack_xy(step.target, landing - 1, landing)
        shown = _dim_dice(dice, [])
        # Los pips no cambian hasta que la ficha se posa.
        lifted = board_state(_remove(current, side, step.source), turn=side, dice=shown, **common)
        lifted["pips"] = _pips(current)
        frames += _steps_of(lifted, _flight(side, start, end_at))
        marks |= {step.source, step.target}
        if hit:
            # La rival sigue en el punto, bajo la ficha que acaba de caer.
            mid = _unhit(after, side)
            other = side.other
            before_bar = current.on_bar(other)
            spot = stack_xy(step.target, 0, 1)
            blot = board_state(mid, turn=side, dice=shown, **common)
            blot["pips"] = _pips(after)
            bar_at = bar_xy(other, before_bar, before_bar + 1)
            flights = _flight(other, spot, bar_at, lift_frames=1, land_frames=2)
            frames += _steps_of(blot, flights)
            marks.add(BAR)
        i = next((j for j, d in enumerate(dice) if not d["used"] and d["v"] == step.die), None)
        if i is None:
            i = next((j for j, d in enumerate(dice) if not d["used"]), None)
        if i is not None:
            dice = _dim_dice(dice, [i])
        landed = board_state(after, turn=side, dice=dice, **common)
        frames.append((landed, LAND_MS))
        current = after
    final_turn = None if end is not None or current.winner is not None else side.other
    final = board_state(current, turn=final_turn, dice=dice, marks=marks, **common)
    if end is not None:
        frames += _end_frames(_with(final, turn=0), end, winner)
        final = _final_end(_with(final, turn=0), end)
    return frames, final


def end_steps(
    position: Position,
    *,
    end: dict[str, Any],
    winner: Side | None = None,
    cube: Cube = NO_CUBE,
    labels: Side = Side.WHITE,
) -> tuple[list[tuple[dict, int]], dict[str, Any]]:
    """El cartel que aparece (rendirse, dormirse, rechazar un doblado) y, con `winner`, confeti."""
    still = board_state(position, cube=cube, labels=labels)
    return _end_frames(still, end, winner), _final_end(still, end)


def chain(
    *parts: tuple[list[tuple[dict, int]], dict[str, Any]], pause_ms: int = 0
) -> tuple[list[tuple[dict, int]], dict[str, Any]]:
    """Junta varias animaciones en una, con una pausa quieta opcional entre ellas.

    La pausa enseña el estado final de la anterior durante `pause_ms`; sin ella, la
    siguiente empieza justo donde acaba la otra. El final es el de la última.
    """
    if not parts:
        raise ValueError("chain necesita al menos una animación.")
    steps: list[tuple[dict, int]] = []
    for index, (part_steps, final) in enumerate(parts):
        steps += part_steps
        if pause_ms > 0 and index < len(parts) - 1:
            steps.append((final, pause_ms))
    return steps, parts[-1][1]


# -- Los dados ruedan --------------------------------------------------------------------------


def _roll_track(
    rng: random.Random,
    start: tuple[float, float],
    end: tuple[float, float],
    end_rot: float,
    value: int,
    frames: int,
) -> list[dict[str, float]]:
    """La trayectoria de un dado: entra, bota tres veces, rueda y se para en `end`.

    Devuelve por fotograma `x`, `y`, `rot`, `z`, `sx`, `m` y `v`; el fotograma `frames`
    (que no se incluye) es el reposo.
    """
    flips = rng.randint(4, 6)
    # Vuelca hacia donde va: gira sobre el eje del suelo perpendicular al camino. En el
    # mundo del craps `y` va hacia el fondo, al revés que en pantalla.
    axis = (end[1] - start[1], end[0] - start[0], 0.0)
    spin = rng.choice((-1, 1)) * rng.uniform(2.4, 3.6) * math.pi
    swerve = rng.uniform(-34, 34)
    track: list[dict[str, float]] = []
    for i in range(1, frames):
        u = i / frames
        run = min(1.0, u / 0.92)
        e = 1 - (1 - run) ** 2.3
        x = start[0] + (end[0] - start[0]) * e
        y = start[1] + (end[1] - start[1]) * e + swerve * math.sin(math.pi * run) * (1 - run)
        # Tres botes cada vez más bajos y el resto, rodando por el tapete.
        z = 0.0
        for lo, hi, amp in ((0.0, 0.42, 1.0), (0.42, 0.68, 0.42), (0.68, 0.84, 0.15)):
            if lo <= u < hi:
                z = amp * math.sin(math.pi * (u - lo) / (hi - lo))
        air = min(1.0, u / 0.84)
        theta = flips * math.pi * _smooth(air)
        sx = 0.3 + 0.7 * abs(math.cos(theta)) if u < 0.84 else 1.0
        # El giro se frena y acaba justo en el del dado quieto: no salta al pararse.
        rot = end_rot + spin * (1 - u) ** 2
        # Le faltan por volcar `flips` cuartos de vuelta y acaba con `value` arriba.
        left = flips * math.pi / 2 * (1 - _smooth(air))
        m = mat_mul(rotation(axis, -left), face_up(value, -rot))
        track.append(
            {
                "x": x,
                "y": y,
                "rot": rot,
                "z": z,
                "sx": sx,
                "m": m,
                "v": value_up(m),
            }
        )
    return track


def _rolling(
    side_of: Sequence[Side],
    values: Sequence[int],
    starts: Sequence[tuple[float, float]],
    ends: Sequence[tuple[float, float]],
    seed: int,
) -> list[list[dict]]:
    """Los dados de cada fotograma de una tirada (uno por elemento de `values`)."""
    tracks = []
    for i, value in enumerate(values):
        rng = random.Random(f"{seed}:{i}")
        track = _roll_track(rng, starts[i], ends[i], _TILTS[i % 4], value, ROLL_FRAMES)
        tracks.append(track)
    frames = []
    for f in range(ROLL_FRAMES - 1):
        frames.append(
            [
                {
                    "x": round(t[f]["x"], 2),
                    "y": round(t[f]["y"], 2),
                    "rot": round(t[f]["rot"], 3),
                    "z": round(t[f]["z"], 3),
                    "v": int(t[f]["v"]),
                    "sx": round(t[f]["sx"], 3),
                    "m": _flat(t[f]["m"]),
                    "side": side_of[i].value,
                    "used": False,
                }
                for i, t in enumerate(tracks)
            ]
        )
    return frames


def _entry(side: Side, rng: random.Random, i: int) -> tuple[float, float]:
    """Por dónde entra un dado: por el borde de su lado, desde fuera del tablero."""
    x = W + 24 if side is Side.WHITE else -24
    return x, _MID + rng.uniform(-90, 90) + (i - 1) * 10


def roll_steps(
    position: Position,
    side: Side,
    roll: tuple[int, int],
    *,
    seed: int,
    cube: Cube = NO_CUBE,
    labels: Side = Side.WHITE,
) -> tuple[list[tuple[dict, int]], dict[str, Any]]:
    """Los dados entran rodando, dan vueltas, botan y se paran en la mitad de `side`.

    El azar (por dónde entran, cuánto giran) sale de `seed`: la misma semilla
    pinta lo mismo.

    Returns:
        Los fotogramas `(estado, ms)` y el estado final, quieto, con `roll` a la vista.
    """
    values = dice_of(roll)
    ends = _rest_slots(side, len(values))
    entry_rng = random.Random(f"{seed}:entry")
    starts = [_entry(side, entry_rng, i) for i in range(len(values))]
    sides = [side] * len(values)
    states = _rolling(sides, values, starts, ends, seed)
    frames = [
        (board_state(position, turn=side, dice=dice, cube=cube, labels=labels), FRAME_MS)
        for dice in states
    ]
    final = board_state(position, turn=side, dice=rest_dice(side, roll), cube=cube, labels=labels)
    return frames, final


def opening_steps(
    position: Position,
    roll: tuple[int, int],
    *,
    seed: int,
    labels: Side = Side.WHITE,
) -> tuple[list[tuple[dict, int]], dict[str, Any]]:
    """La tirada de salida: un dado por color, una pausa y los dos juntos en la mitad de quien sale.

    `roll[0]` es el dado de las blancas (cae en su mitad) y `roll[1]` el de las negras;
    sale quien saca más y juega con los dos.
    """
    sides = (Side.WHITE, Side.BLACK)
    ends = [(half_center(s), float(_MID)) for s in sides]
    entry_rng = random.Random(f"{seed}:entry")
    starts = [_entry(s, entry_rng, 1) for s in sides]
    states = _rolling(sides, roll, starts, ends, seed)
    common: dict[str, Any] = {"labels": labels}
    frames = [(board_state(position, dice=dice, **common), FRAME_MS) for dice in states]

    def resting(places: Sequence[tuple[float, float]]) -> list[dict]:
        return [
            {**_die(s, v, x, y, i), "rot": _TILTS[i % 4]}
            for i, (s, v, (x, y)) in enumerate(zip(sides, roll, places, strict=True))
        ]

    apart = resting(ends)
    frames.append((board_state(position, dice=apart, **common), OPENING_PAUSE_MS))
    starter = Side.WHITE if roll[0] > roll[1] else Side.BLACK
    together = _rest_slots(starter, 2)
    join = 9
    for i in range(1, join + 1):
        t = _smooth(i / (join + 1))
        dice = []
        for j, (s, v) in enumerate(zip(sides, roll, strict=True)):
            x = ends[j][0] + (together[j][0] - ends[j][0]) * t
            y = ends[j][1] + (together[j][1] - ends[j][1]) * t
            lifted = 0.0 if s is starter else math.sin(math.pi * i / (join + 1))
            dice.append({**_die(s, v, x, y, j), "z": round(lifted * 0.6, 3)})
        frames.append((board_state(position, dice=dice, **common), FRAME_MS))
    final = board_state(position, turn=starter, dice=resting(together), **common)
    return frames, final


# -- El cubo ----------------------------------------------------------------------------------


def cube_steps(
    position: Position,
    before: Cube,
    after: Cube,
    *,
    turn: Side | None = None,
    labels: Side = Side.WHITE,
) -> tuple[list[tuple[dict, int]], dict[str, Any]]:
    """Doblar: el cubo se levanta, gira mostrando el valor nuevo y va a su sitio.

    Va al lado de quien lo acepta (`after.owner`) o, con `after.offered`, se queda
    en el centro iluminado.
    """

    def still(**cube: Any) -> dict[str, Any]:
        return board_state(position, turn=turn, cube=after, labels=labels) | {
            "cube": cube_state(after, **cube)
        }

    frames: list[tuple[dict, int]] = []
    start, goal = cube_xy(before), cube_xy(after)
    old_glow = 1.0 if before.offered else 0.0
    new_glow = 1.0 if after.offered else 0.0
    lift_frames, turn_frames, land_frames = 3, 9, 3
    for i in range(1, lift_frames + 1):
        frames.append(
            (
                _with(
                    still(x=start[0], y=start[1], value=before.value, glow=old_glow),
                    cube=cube_state(before, lift=i / lift_frames),
                ),
                FRAME_MS,
            )
        )
    for i in range(1, turn_frames + 1):
        t = i / (turn_frames + 1)
        e = _smooth(t)
        flip = math.pi * t if before.value != after.value else 0.0
        shown = before.value if flip < math.pi / 2 else after.value
        arc = 1.0 + 0.1 * math.sin(math.pi * t)
        cube = cube_state(
            after,
            x=start[0] + (goal[0] - start[0]) * e,
            y=start[1] + (goal[1] - start[1]) * e,
            value=shown,
            lift=1.0,
            flip=flip,
            glow=old_glow + (new_glow - old_glow) * e,
        )
        cube["scale"] = round(cube["scale"] * arc, 3)
        frames.append((_with(still(), cube=cube), FRAME_MS))
    for i in range(1, land_frames + 1):
        frames.append((still(lift=1 - i / (land_frames + 1)), FRAME_MS))
    return frames, still()


# -- Pillow (reserva) ----------------------------------------------------------------------------

_WOOD = ((122, 76, 42), (74, 42, 22))
_FELT = (24, 98, 62)
_IVORY = (233, 215, 170)
_BURGUNDY = (140, 36, 52)
_TRAY = (60, 16, 28)
_BG = (28, 20, 16)
_GOLD = (255, 214, 90)
_CHECKER = {
    Side.WHITE: ((255, 252, 240), (196, 178, 140)),
    Side.BLACK: ((70, 70, 80), (14, 14, 18)),
}


class BackgammonRenderer:
    """El tablero con Pillow: sin brillos ni sombras finas, para cuando no hay Node ni navegador."""

    def _font(self, path: Any, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            return ImageFont.load_default()

    @staticmethod
    def _triangle(index: int, scale: float = 0.45) -> list[tuple[float, float]]:
        """Los tres vértices del punto `index` (ancho `2 * scale` puntos)."""
        cx, half = point_x(index), _PW * scale
        if index < 12:
            base, tip = GEOMETRY["bottom"], GEOMETRY["bottom"] - GEOMETRY["length"]
        else:
            base, tip = GEOMETRY["top"], GEOMETRY["top"] + GEOMETRY["length"]
        return [(cx - half, base), (cx + half, base), (cx, tip)]

    def _board(self, draw: ImageDraw.ImageDraw) -> None:
        """La mesa de serie: Pillow no conoce las skins de tapete."""
        g = GEOMETRY
        fx0, fy0, fx1, fy1 = g["frame"]
        draw.rounded_rectangle((fx0, fy0, fx1, fy1), 14, fill=_WOOD[0], outline=_WOOD[1], width=3)
        x0, y0, x1, y1 = g["felt"]
        draw.rectangle((x0, y0, x1, y1), fill=_FELT)
        for index in range(POINTS):
            column = 11 - index if index < 12 else index - 12
            dark = (column % 2 == 0) == (index < 12)
            draw.polygon(self._triangle(index), fill=_BURGUNDY if dark else _IVORY)
        bx0, bx1 = g["bar"]
        draw.rectangle((bx0, y0, bx1, y1), fill=_WOOD[0], outline=_WOOD[1])
        wx0, wx1 = g["wall"]
        draw.rectangle((wx0, y0, wx1, y1), fill=_WOOD[0])
        tx0, ty0, tx1, ty1 = g["tray"]
        draw.rectangle((tx0, ty0, tx1, ty1), fill=_TRAY, outline=_WOOD[1])
        draw.line((tx0, g["mid"], tx1, g["mid"]), fill=_WOOD[0], width=5)

    def _numbers(self, draw: ImageDraw.ImageDraw, state: dict[str, Any]) -> None:
        font = self._font(TEXT_FONT, 11)
        labels = Side(state["labels"])
        fy0, fy1 = GEOMETRY["frame"][1], GEOMETRY["frame"][3]
        for index in range(POINTS):
            y = fy1 - 11 if index < 12 else fy0 + 11
            draw.text(
                (point_x(index), y),
                str(point_number(index, labels)),
                font=font,
                fill=(235, 205, 150),
                anchor="mm",
            )

    def _checker(self, draw: ImageDraw.ImageDraw, x: float, y: float, side: Side) -> None:
        light, dark = _CHECKER[side]
        r = _R
        draw.ellipse((x - r, y - r + 2, x + r, y + r + 2), fill=dark)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=light, outline=dark, width=2)
        draw.ellipse((x - r * 0.6, y - r * 0.6, x + r * 0.6, y + r * 0.6), outline=dark, width=1)

    def _die(self, draw: ImageDraw.ImageDraw, die: dict[str, Any]) -> None:
        size = _DIE * (1 + 0.35 * die["z"]) / 2
        x, y = die["x"], die["y"]
        dark = die["side"] == Side.BLACK.value
        body = (34, 34, 40) if dark else (250, 244, 226)
        pip = (240, 226, 190) if dark else (50, 36, 26)
        if die["used"]:
            body = tuple(int(c * 0.55 + 70) for c in body)
        draw.rounded_rectangle((x - size, y - size, x + size, y + size), 7, fill=body, outline=pip)
        step = size * 0.52
        layout = {
            1: [(0, 0)],
            2: [(-1, -1), (1, 1)],
            3: [(-1, -1), (0, 0), (1, 1)],
            4: [(-1, -1), (1, -1), (-1, 1), (1, 1)],
            5: [(-1, -1), (1, -1), (0, 0), (-1, 1), (1, 1)],
            6: [(-1, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (1, 1)],
        }[die["v"]]
        for dx, dy in layout:
            px, py = x + dx * step, y + dy * step
            draw.ellipse((px - 3, py - 3, px + 3, py + 3), fill=pip)

    def frame(self, meta: dict[str, Any], state: dict[str, Any]) -> Image.Image:
        """Un fotograma a partir de su estado."""
        image = Image.new("RGB", (W, H), _BG)
        draw = ImageDraw.Draw(image)
        title = self._font(TITLE_FONT, 26)
        text = self._font(TEXT_FONT, 13)
        draw.text((W / 2, 24), meta["title"], font=title, fill=_GOLD, anchor="mm")
        draw.text((W / 2, 50), meta["subtitle"], font=text, fill=(230, 220, 200), anchor="mm")
        self._board(draw)
        self._numbers(draw, state)
        cx, cy, cw, ch = GEOMETRY["cards"]
        for side, key, x in ((Side.WHITE, "white", cx), (Side.BLACK, "black", W - cx - cw)):
            active = state["turn"] == side.value
            draw.rounded_rectangle(
                (x, cy, x + cw, cy + ch),
                12,
                fill=(46, 34, 26),
                outline=_GOLD if active else (110, 90, 70),
                width=3 if active else 1,
            )
            self._checker(draw, x + 30, cy + ch / 2, side)
            draw.text(
                (x + 56, cy + 20),
                meta[key]["name"][:14],
                font=text,
                fill=(255, 255, 255),
                anchor="lm",
            )
            draw.text(
                (x + 56, cy + 40),
                meta[key]["tag"][:16],
                font=text,
                fill=(190, 175, 150),
                anchor="lm",
            )
            pips = str(state["pips"][side.value - 1])
            draw.text((x + cw - 30, cy + ch / 2), pips, font=title, fill=_GOLD, anchor="mm")
            if active:
                draw.text((x + cw / 2, cy), "TURNO", font=text, fill=_GOLD, anchor="mm")
        self._pieces(draw, state)
        banner_state = state["banner"]
        if banner_state is not None and banner_state["a"] >= 0.5:
            y = banner_state.get("y", GEOMETRY["banner_y"])
            draw.rounded_rectangle(
                (W / 2 - 230, y - 48, W / 2 + 230, y + 48), 16, fill=(10, 12, 30)
            )
            big = self._font(TITLE_FONT, 34)
            draw.text(
                (W / 2, y - 12), banner_state["title"], font=big, fill=(255, 226, 120), anchor="mm"
            )
            draw.text(
                (W / 2, y + 22), banner_state["sub"], font=text, fill=(230, 230, 240), anchor="mm"
            )
        return image

    def _pieces(self, draw: ImageDraw.ImageDraw, state: dict[str, Any]) -> None:
        for index in state["marks"]:
            if index < POINTS:
                draw.polygon(self._triangle(index, 0.5), outline=_GOLD, width=3)
            else:
                box = GEOMETRY["bar"] if index == BAR else GEOMETRY["tray"][0::2]
                top, bottom = GEOMETRY["felt"][1], GEOMETRY["felt"][3]
                draw.rectangle((box[0], top, box[1], bottom), outline=_GOLD, width=3)
        for index, value in enumerate(state["points"]):
            side = Side.WHITE if value > 0 else Side.BLACK
            n = abs(value)
            for k in range(n):
                x, y = stack_xy(index, k, n)
                self._checker(draw, x, y, side)
            if n > 5:
                x, y = stack_xy(index, n - 1, n)
                draw.text((x, y), str(n), fill=(255, 0, 0), anchor="mm")
        for side in (Side.WHITE, Side.BLACK):
            count = state["bar"][side.value - 1]
            for k in range(count):
                self._checker(draw, *bar_xy(side, k, count), side)
            for k in range(state["off"][side.value - 1]):
                x, y = off_xy(side, k)
                light, dark = _CHECKER[side]
                width, height, _pace = GEOMETRY["slab"]
                draw.rounded_rectangle(
                    (x - width / 2, y - height / 2, x + width / 2, y + height / 2),
                    3,
                    fill=light,
                    outline=dark,
                )
        cube = state["cube"]
        half = GEOMETRY["cube"] / 2 * cube["scale"]
        draw.rounded_rectangle(
            (cube["x"] - half, cube["y"] - half, cube["x"] + half, cube["y"] + half),
            6,
            fill=(245, 236, 210),
            outline=(120, 40, 50) if cube["glow"] else (90, 70, 40),
            width=3 if cube["glow"] else 1,
        )
        draw.text((cube["x"], cube["y"]), str(cube["v"]), fill=(120, 20, 38), anchor="mm")
        for die in state["dice"]:
            self._die(draw, die)
        for fly in state["flying"] or []:
            self._checker(draw, fly["x"], fly["y"] - fly["h"], Side(fly["side"]))

    def still(self, meta: dict[str, Any], state: dict[str, Any]) -> bytes:
        """PNG de un estado quieto."""
        png = io.BytesIO()
        self.frame(meta, state).save(png, format="PNG", compress_level=6)
        return png.getvalue()

    def animate(
        self, meta: dict[str, Any], steps: list[tuple[dict, int]], final: dict[str, Any]
    ) -> Media:
        """GIF de unos fotogramas y PNG del final."""
        frames = [self.frame(meta, state) for state, _ms in steps]
        frames.append(self.frame(meta, final))
        return encode(frames, [ms for _state, ms in steps])
