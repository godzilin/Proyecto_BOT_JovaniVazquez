"""Mundo editable del Jovaniverso: formato de los datos y su validación.

Los administradores construyen el mundo desde la propia página (`universo/src/editor/`):
montan assets con formas básicas o importan modelos GLB y los colocan. Lo que mandan
es entrada no confiable aunque venga de un administrador (otra pestaña, un script, un
GLB raro), así que todo pasa por aquí antes de guardarse en
`bot.repositories.universo_mundo`. La página dibuja lo mismo que se valida aquí; las
cifras de triángulos de cada forma tienen su par en `universo/src/editor/formato.ts`.

Formato (`FORMAT`): el mundo es un mapa de **trozos**, cada uno un JSON con su clave, que
se guardan, se publican y se descargan por separado (`docs/mundo-celdas.md`):

```
"zonas"        {"zonas": {"<lugar>": {"x": float, "z": float, "r": float}}}
"teleportes"   {"teleportes": [{"nombre": str, "x": float, "z": float}, ...]}
"a/<id>"       {"nombre": str, "tipo": "piezas", "piezas": [Pieza, ...], "malla"?: Malla,
                "colision": [[x, z, r], ...] | null, "distancia"?: float, "funcion"?: Funcion}
               {"nombre": str, "tipo": "glb", "glb": "<sha256>", "colision": ..., "distancia"?: ...,
                "funcion"?: ...}
"c/<cx>/<cz>"  {"objetos": [{"asset": "<id>", "pos": [x, y, z], "rot": [x, y, z],
                             "esc": [x, y, z], "tinte"?: float, "datos"?: Datos}],
                "terreno"?: {"alturas": "<base64>", "colores": "<base64>"}}
"i/<id>"       {"nombre": str, "ancho": m, "fondo": m, "alto": m, "suelo": 0xRRGGBB,
                "pared": 0xRRGGBB, "piso"?: true, "objetos": [Objeto, ...]}
```

- La celda `(cx, cz)` cubre `[cx·CELL, (cx+1)·CELL)` en x y en z. Cada objeto va en la
  celda de su `pos`, que sigue en coordenadas del mundo.

- Una `Pieza` es `{"forma", "pos", "rot", "esc", "color"}`: una forma de `SHAPES` de
  1 m de lado, girada (radianes, orden YXZ como `geom.poner`), escalada y pintada con
  un color `0xRRGGBB`. Sin texturas: color por vértice, como el resto del mundo. El
  `prisma` lleva además `lados`, `arriba` y `abajo` (radio de cada tapa, de 0 a 1: con 0
  es un cono o una pirámide) y `fase` (giro de sus lados, en radianes, antes de escalar:
  así un tejado de cuatro aguas sigue a los ejes aunque la escala no sea igual en x y z).
- `malla` es geometría ya hecha que no sale de formas básicas (una roca abollada, un
  roque con degradado): `{"pos": [mm, ...], "color": [0xRRGGBB, ...]}`, triángulos
  sueltos con las posiciones en milímetros enteros y un color por vértice. No se edita
  pieza a pieza; se coloca como cualquier asset.
- `colision` son los círculos (`[x, z, radio]` en metros, en las coordenadas del asset)
  contra los que se empuja el jugador (`mundo/colision.ts`); giran y escalan con cada
  objeto. `null`, se atraviesa.
- `distancia` es hasta dónde se dibuja (lo pequeño desaparece antes); sin ella, la de
  las construcciones. `tinte` multiplica los colores de un objeto (la vegetación varía
  de tono de un árbol a otro).
- `funcion` dice qué hace un asset además de verse (`FUNCTIONS`): `{"tipo": "traga"}` es una
  tragaperras jugable (la pantalla y la botonera van en su sitio del marco de la máquina,
  `casino/sitio.ts`) y `{"tipo": "letrero", "pos": [x, y, z], "ancho": m, "alto": m}` es un
  letrero con su panel en ese rectángulo (centro en `pos`, mirando a +z). Así cualquier asset,
  también un GLB, puede ser una máquina o un letrero. `{"tipo": "puerta"}` lleva a un interior
  (el de `datos.interior` del objeto) y `{"tipo": "usable", "uso": "mueble" | "reparar"}` es
  algo de dentro de un interior con un estado que cambia jugando (`USES`; las reglas, en
  `bot.services.universo_interiores`).
- `datos` es lo propio de cada objeto con función: `n`, el número de la tragaperras (de 1 a
  `MAX_SLOTS`; con él guarda el servidor el calor, la barra y los logros de cada máquina, así
  que no se repite en el mundo) o del usable dentro de su interior (de 1 a `MAX_USABLES`, sin
  repetirse en él: su estado se guarda por número), `titulo` y `botes` del letrero (el texto
  y las claves de los botes que enseña, `bot.services.universo_botes`; uno que ya no exista
  no se pinta) e `interior`, el id del interior al que lleva una puerta.
- Un interior (`i/<id>`, fase 4 de `docs/mundo-celdas.md`) es una habitación que no está en
  ninguna celda: `ancho` × `fondo` m de planta y `alto` m, con el suelo y las paredes de esos
  colores y la salida en el centro de la pared +z. Sus objetos van en coordenadas de la
  habitación (el centro del suelo es el 0) y la página la monta en el sitio de la puerta por
  la que se entra, sin el mundo de fuera. Con `piso`, cada miembro con vivienda habitual en
  `pisos` entra en el suyo (con su propio estado) y el resto, en el piso piloto, que es de
  todos.
- `zonas` mueve los lugares de los logros que son un círculo (`EDITABLE_ZONES`). No se
  crean ni se borran: cada clave es un logro (`PLACES` de `bot.services.universo`).
- `teleportes` son los sitios del panel «Viajar» del menú (Tab): un nombre y un punto (x, z)
  donde se aparece, a ras de suelo. Sin el trozo, la página pone los de serie; con la lista
  vacía, no hay ninguno (por eso una lista vacía no cuenta como trozo vacío).
- `terreno` es el suelo esculpido de la celda: una rejilla de `CELL_TERRAIN_SIDE` ×
  `CELL_TERRAIN_SIDE` vértices cada `TERRAIN_STEP` m desde su esquina (-x -z), fila a fila,
  con los bordes repetidos en la celda de al lado. `alturas` son enteros de 16 bits con
  signo en centímetros (little-endian) y `colores` un byte por vértice: 0 es el color
  natural (sale de la altura y la pendiente) y de 1 a `TERRAIN_PAINTS` uno de la paleta de
  `mundo/terreno.ts`. Sin él, la isla de serie (la fórmula de `heightAt`).
- En la página, el editor y el mundo trabajan con el documento de un solo bloque de antes
  (`MundoDoc`: `assets`, `objetos`, `zonas` y el `terreno` de toda la isla, de
  `TERRAIN_SIDE`² vértices) y lo parten o lo juntan al hablar con el servidor
  (`aTrozos` y `deTrozos` de `formato.ts`; `split_world` aquí).
- Un GLB va aparte, por su resumen (`glb_summary`), y el mundo solo lo nombra.

No guarda nada ni habla con Discord: solo dice si un trozo, un mundo o un GLB valen.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
import struct
import sys
from array import array
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Versión del formato. Una página con otra no edita (recarga al desplegar).
FORMAT = 2
#: Lo más que puede pesar un trozo en JSON.
MAX_PIECE_BYTES = 1024 * 1024
#: Topes por trozo, no por mundo: la densidad es lo que pesa al dibujar. Lo que se dibuja de
#: verdad lo limita el presupuesto de la Biblia; esto solo impide tumbar la página o el servidor.
MAX_CELL_OBJECTS = 4000
MAX_PIECES = 300
MAX_NAME = 40
#: Lo más que puede pesar y tener un GLB importado.
MAX_GLB_BYTES = 1024 * 1024
MAX_GLB_TRIANGLES = 6000
#: Triángulos de cada forma básica, con los segmentos que usa la página. El prisma
#: depende de sus lados y tapas (`piece_triangles`).
SHAPES = {
    "caja": 12, "cuna": 8, "cilindro": 48, "cono": 24, "esfera": 168,
    "icosaedro": 20, "dodecaedro": 36, "prisma": 0,
}  # fmt: skip
MIN_SIDES, MAX_SIDES = 3, 32
#: Una `malla` hecha a mano: triángulos y alcance de sus posiciones (milímetros).
MAX_MESH_TRIANGLES = 2000
MAX_MESH_MM = 60_000
MAX_CIRCLES = 16
MIN_TINT, MAX_TINT = 0.3, 2.0
MIN_DISTANCE, MAX_DISTANCE = 20.0, 600.0
#: Lugares de logros que se pueden mover: los que son un círculo en `mundo/lugares.ts`.
#: El resto depende de la altura o del agua y sigue en código.
EDITABLE_ZONES = frozenset({"nublo", "dunas", "faro", "plaza", "drago", "canteras"})
#: Lado de una celda en metros: la isla esculpible (320 m centrados en 0) son 4 × 4 celdas.
CELL = 80
#: Celdas a cada lado del 0 (±40 km, 80 km de lado). La sala acepta poses hasta ahí
#: (`universo_sala.WORLD_DM`) y la página no pasa de ahí (`LIMITE_CELDAS` de `mundo/borde.ts`).
CELL_LIMIT = 500
MIN_Y, MAX_Y = -20.0, 120.0
MIN_SCALE, MAX_SCALE = 0.02, 60.0
MAX_ROTATION = 4 * math.pi
MAX_PIECE_OFFSET = 60.0
MAX_COLLISION = 40.0
MAX_ZONE_RADIUS = 60.0
#: Sitios del panel «Viajar».
MAX_TELEPORTS = 24
#: La rejilla del terreno (`mundo/terreno.ts`: `LADO` y `CELDAS`) y los colores de su paleta.
TERRAIN_EXTENT = 320
TERRAIN_CELLS = 160
TERRAIN_SIDE = TERRAIN_CELLS + 1
TERRAIN_STEP = TERRAIN_EXTENT // TERRAIN_CELLS
TERRAIN_PAINTS = 13
#: Vértices de terreno por lado de una celda (con los dos bordes, que repite la de al lado).
CELL_TERRAIN_SIDE = CELL // TERRAIN_STEP + 1

#: Lo que puede hacer un asset además de verse (`funcion`).
FUNCTIONS = ("traga", "letrero", "puerta", "usable")
#: Para qué sirve un usable: un mueble se pone y se quita; lo que se arregla vuelve a romperse.
USES = ("mueble", "reparar")
#: Un interior: lado de su planta y altura (m), objetos y usables (con número) que lleva.
MIN_ROOM_SIDE, MAX_ROOM_SIDE = 2.0, 40.0
MIN_ROOM_HEIGHT, MAX_ROOM_HEIGHT = 2.0, 12.0
MAX_ROOM_OBJECTS = 400
MAX_USABLES = 24
#: Tragaperras con número del mundo: el servidor solo deja jugar de la 1 a esta.
MAX_SLOTS = 24
#: Botes que enseña un letrero como mucho, largo de su título y lado de su panel.
MAX_SIGN_POTS = 8
MAX_SIGN_TITLE = 40
MAX_SIGN_SIDE = 40.0

#: El casino de la plaza como datos del editor (assets y objetos, formato del `MundoDoc`): lo
#: lee el mundo de serie de la página (`mundo/serie.ts`) y `add_casino` lo añade a los mundos
#: guardados antes de que las tragaperras fueran objetos.
CASINO_FILE = Path(__file__).resolve().parent.parent / "assets" / "casino_plaza.json"

_ID = re.compile(r"[a-z0-9_-]{1,40}")
_KEY = re.compile(
    r"zonas|teleportes|a/(?P<a>[a-z0-9_-]{1,40})|i/(?P<i>[a-z0-9_-]{1,40})"
    r"|c/(?P<cx>-?\d{1,3})/(?P<cz>-?\d{1,3})"
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_GLB_MAGIC = b"glTF"
_CHUNK_JSON = 0x4E4F534A
#: `mode` de las primitivas de glTF que dibujan triángulos (lista, tira y abanico).
_TRIANGLE_MODES = {4, 5, 6}


class MundoError(ValueError):
    """Un mundo o un GLB que no valen; el texto se enseña tal cual al administrador."""


@dataclass(frozen=True, slots=True)
class GlbSummary:
    """Lo que el servidor sabe de un GLB válido.

    Attributes:
        sha256: Resumen del archivo, que es su nombre en el mundo.
        triangles: Triángulos que dibuja.
    """

    sha256: str
    triangles: int


def _number(value: object, low: float, high: float, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise MundoError(f"{what} no es un número.")
    if not low <= value <= high:
        raise MundoError(f"{what} tiene que estar entre {low:g} y {high:g}.")
    return float(value)


def _vector(value: object, low: float, high: float, what: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise MundoError(f"{what} tiene que ser una lista de tres números.")
    return [_number(v, low, high, what) for v in value]


def _object(value: object, keys: set[str], what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MundoError(f"{what} no es un objeto.")
    extra = set(value) - keys
    if extra:
        raise MundoError(f"{what} tiene campos que no se conocen: {', '.join(sorted(extra))}.")
    return value


def _name(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_NAME:
        raise MundoError(f"{what} necesita un nombre de 1 a {MAX_NAME} caracteres.")
    return value.strip()


def _color(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFF:
        raise MundoError(f"{what}: el color tiene que ser 0xRRGGBB.")
    return value


def _collision(value: object, what: str) -> list[list[float]] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_CIRCLES:
        raise MundoError(f"La colisión de {what} tiene que ser de 1 a {MAX_CIRCLES} círculos.")
    circles = []
    for c in value:
        if not isinstance(c, list) or len(c) != 3:
            raise MundoError(f"La colisión de {what}: cada círculo es [x, z, radio].")
        circles.append(
            [
                _number(c[0], -MAX_PIECE_OFFSET, MAX_PIECE_OFFSET, f"La colisión de {what}: x"),
                _number(c[1], -MAX_PIECE_OFFSET, MAX_PIECE_OFFSET, f"La colisión de {what}: z"),
                _number(c[2], 0, MAX_COLLISION, f"La colisión de {what}: el radio"),
            ]
        )
    return circles


def piece_triangles(piece: dict[str, Any]) -> int:
    """Triángulos de una pieza ya validada (el prisma, según sus lados y sus tapas)."""
    if piece["forma"] != "prisma":
        return SHAPES[piece["forma"]]
    # Como `CylinderGeometry` de three: cada tapa con radio cuenta sus lados dos veces
    # (la tapa y su mitad del costado); una tapa de radio 0 no se genera.
    return 2 * piece["lados"] * ((piece["arriba"] > 0) + (piece["abajo"] > 0))


def _piece(value: object, what: str) -> dict[str, Any]:
    keys = {"forma", "pos", "rot", "esc", "color", "lados", "arriba", "abajo", "fase"}
    p = _object(value, keys, what)
    shape = p.get("forma")
    if shape not in SHAPES:
        raise MundoError(f"{what}: la forma tiene que ser una de {', '.join(SHAPES)}.")
    piece: dict[str, Any] = {
        "forma": shape,
        "pos": _vector(p.get("pos"), -MAX_PIECE_OFFSET, MAX_PIECE_OFFSET, f"{what}: la posición"),
        "rot": _vector(p.get("rot"), -MAX_ROTATION, MAX_ROTATION, f"{what}: el giro"),
        "esc": _vector(p.get("esc"), MIN_SCALE, MAX_SCALE, f"{what}: la escala"),
        "color": _color(p.get("color"), what),
    }
    prism = {"lados", "arriba", "abajo", "fase"}
    if shape != "prisma":
        if prism & set(p):
            raise MundoError(f"{what}: lados, tapas y fase solo valen en un prisma.")
        return piece
    sides = p.get("lados")
    if isinstance(sides, bool) or not isinstance(sides, int) or not MIN_SIDES <= sides <= MAX_SIDES:
        raise MundoError(f"{what}: el prisma tiene de {MIN_SIDES} a {MAX_SIDES} lados.")
    piece["lados"] = sides
    piece["arriba"] = _number(p.get("arriba"), 0, 1, f"{what}: la tapa de arriba")
    piece["abajo"] = _number(p.get("abajo"), 0, 1, f"{what}: la tapa de abajo")
    if piece["arriba"] == 0 and piece["abajo"] == 0:
        raise MundoError(f"{what}: el prisma necesita alguna tapa con radio.")
    piece["fase"] = _number(p.get("fase", 0), -MAX_ROTATION, MAX_ROTATION, f"{what}: la fase")
    return piece


def _mesh(value: object, what: str) -> dict[str, list[int]]:
    m = _object(value, {"pos", "color"}, f"{what}: la malla")
    pos, colors = m.get("pos"), m.get("color")
    if not isinstance(pos, list) or not isinstance(colors, list) or not pos or len(pos) % 9:
        raise MundoError(f"{what}: la malla son triángulos (9 posiciones cada uno).")
    if len(pos) // 9 > MAX_MESH_TRIANGLES or len(colors) != len(pos) // 3:
        raise MundoError(
            f"{what}: la malla tiene hasta {MAX_MESH_TRIANGLES} triángulos y un color por vértice."
        )
    if not all(
        isinstance(v, int) and not isinstance(v, bool) and -MAX_MESH_MM <= v <= MAX_MESH_MM
        for v in pos
    ):
        raise MundoError(f"{what}: las posiciones de la malla son milímetros enteros.")
    return {"pos": pos, "color": [_color(c, f"{what}: la malla") for c in colors]}


def _asset(value: object, what: str, glbs: dict[str, int]) -> dict[str, Any]:
    keys = {"nombre", "tipo", "piezas", "malla", "glb", "colision", "distancia", "funcion"}
    a = _object(value, keys, what)
    name = _name(a.get("nombre"), what)
    kind = a.get("tipo")
    common: dict[str, Any] = {"colision": _collision(a.get("colision"), f"«{name}»")}
    if "funcion" in a:
        common["funcion"] = _function(a["funcion"], f"«{name}»")
    if "distancia" in a:
        common["distancia"] = _number(
            a["distancia"], MIN_DISTANCE, MAX_DISTANCE, f"«{name}»: la distancia"
        )
    if kind == "piezas":
        pieces = a.get("piezas")
        mesh = _mesh(a["malla"], f"«{name}»") if "malla" in a else None
        if not isinstance(pieces, list) or len(pieces) > MAX_PIECES or not (pieces or mesh):
            raise MundoError(f"«{name}» tiene que tener de 1 a {MAX_PIECES} piezas.")
        clean = {
            "nombre": name,
            "tipo": "piezas",
            "piezas": [_piece(p, f"«{name}», pieza {i + 1}") for i, p in enumerate(pieces)],
            **common,
        }
        if mesh is not None:
            clean["malla"] = mesh
        return clean
    if kind == "glb":
        sha = a.get("glb")
        if not isinstance(sha, str) or sha not in glbs:
            raise MundoError(f"«{name}» usa un modelo que no se ha subido.")
        return {"nombre": name, "tipo": "glb", "glb": sha, **common}
    raise MundoError(f"«{name}»: el tipo tiene que ser «piezas» o «glb».")


def _function(value: object, what: str) -> dict[str, Any]:
    f = _object(value, {"tipo", "pos", "ancho", "alto", "uso"}, f"{what}: la función")
    kind = f.get("tipo")
    if kind in ("traga", "puerta"):
        if set(f) != {"tipo"}:
            raise MundoError(f"{what}: una {kind} no lleva más datos en la función.")
        return {"tipo": kind}
    if kind == "usable":
        if set(f) != {"tipo", "uso"} or f["uso"] not in USES:
            raise MundoError(f"{what}: un usable sirve para {' o '.join(USES)}.")
        return {"tipo": "usable", "uso": f["uso"]}
    if kind == "letrero":
        return {
            "tipo": "letrero",
            "pos": _vector(
                f.get("pos"), -MAX_PIECE_OFFSET, MAX_PIECE_OFFSET, f"{what}: el centro del panel"
            ),
            "ancho": _number(f.get("ancho"), 0.1, MAX_SIGN_SIDE, f"{what}: el ancho del panel"),
            "alto": _number(f.get("alto"), 0.1, MAX_SIGN_SIDE, f"{what}: el alto del panel"),
        }
    raise MundoError(f"{what}: la función tiene que ser una de {', '.join(FUNCTIONS)}.")


def _data(value: object, what: str) -> dict[str, Any]:
    d = _object(value, {"n", "titulo", "botes", "interior"}, f"{what}: los datos")
    clean: dict[str, Any] = {}
    if "n" in d:
        n = d["n"]
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_SLOTS:
            raise MundoError(f"{what}: el número de tragaperras va de 1 a {MAX_SLOTS}.")
        clean["n"] = n
    if "titulo" in d:
        title = d["titulo"]
        if not isinstance(title, str) or len(title) > MAX_SIGN_TITLE:
            raise MundoError(f"{what}: el título tiene hasta {MAX_SIGN_TITLE} caracteres.")
        clean["titulo"] = title.strip()
    if "botes" in d:
        pots = d["botes"]
        if (
            not isinstance(pots, list)
            or len(pots) > MAX_SIGN_POTS
            or not all(isinstance(k, str) and _ID.fullmatch(k) for k in pots)
            or len(set(pots)) != len(pots)
        ):
            raise MundoError(f"{what}: un letrero enseña hasta {MAX_SIGN_POTS} botes distintos.")
        clean["botes"] = list(pots)
    if "interior" in d:
        room = d["interior"]
        if not isinstance(room, str) or not _ID.fullmatch(room):
            raise MundoError(f"{what}: la puerta lleva a un interior que no existe.")
        clean["interior"] = room
    return clean


def asset_triangles(asset: dict[str, Any], glbs: dict[str, int]) -> int:
    """Triángulos de un asset ya validado (`glbs`: resumen → triángulos)."""
    if asset["tipo"] == "glb":
        return glbs[asset["glb"]]
    mesh = len(asset["malla"]["pos"]) // 9 if "malla" in asset else 0
    return mesh + sum(piece_triangles(p) for p in asset["piezas"])


#: Cada cuántos vértices de la rejilla de una celda se muestrea su resumen (cada 20 m).
SUMMARY_STEP = 10


def cell_summary(doc: dict[str, Any]) -> str:
    """El resumen de una celda limpia: lo que la página dibuja de lejos y la cima de los logros.

    Sin terreno, `""`. Con él, `"<top_cm>,<top_index>,<b64>"`: la altura máxima de su rejilla en
    cm, el índice (fila a fila) de su primer vértice con esa altura y el base64 de las alturas
    (int16 little-endian, cm) muestreadas cada `SUMMARY_STEP` vértices, fila a fila desde -x -z.
    """
    terrain = doc.get("terreno")
    if not terrain:
        return ""
    heights = array("h", base64.b64decode(terrain["alturas"]))
    if sys.byteorder == "big":
        heights.byteswap()
    top = max(heights)
    side = CELL_TERRAIN_SIDE
    samples = array(
        "h",
        (
            heights[iz * side + ix]
            for iz in range(0, side, SUMMARY_STEP)
            for ix in range(0, side, SUMMARY_STEP)
        ),
    )
    if sys.byteorder == "big":
        samples.byteswap()
    return f"{top},{heights.index(top)},{base64.b64encode(samples.tobytes()).decode()}"


def split_summary(summary: str) -> tuple[int, int, str]:
    """Un resumen no vacío (`cell_summary`) en `(top_cm, top_index, base64 de las muestras)`."""
    top, index, samples = summary.split(",", 2)
    return int(top), int(index), samples


def _terrain(value: object, side: int) -> dict[str, str]:
    t = _object(value, {"alturas", "colores"}, "El terreno")
    n = side * side
    raw = {}
    for key in ("alturas", "colores"):
        text = t.get(key)
        try:
            if not isinstance(text, str):
                raise ValueError
            raw[key] = base64.b64decode(text, validate=True)
        except (ValueError, binascii.Error):
            raise MundoError(f"El terreno: «{key}» no es base64.") from None
    if len(raw["alturas"]) != 2 * n or len(raw["colores"]) != n:
        raise MundoError(f"El terreno tiene que ser una rejilla de {side} × {side}.")
    heights = array("h", raw["alturas"])
    if sys.byteorder == "big":
        heights.byteswap()
    if min(heights) < MIN_Y * 100 or max(heights) > MAX_Y * 100:
        raise MundoError(f"El terreno va de {MIN_Y:g} a {MAX_Y:g} m de altura.")
    if max(raw["colores"]) > TERRAIN_PAINTS:
        raise MundoError("El terreno usa un color que no está en la paleta.")
    return {"alturas": t["alturas"], "colores": t["colores"]}


def parse_key(key: object) -> tuple[str, str] | tuple[str, int, int]:
    """La clase de un trozo: `("zonas", "")`, `("teleportes", "")`, `("a", id)`, `("i", id)` o
    `("c", cx, cz)`.

    Raises:
        MundoError: Si la clave no es de ningún trozo o la celda cae fuera del mundo.
    """
    found = _KEY.fullmatch(key) if isinstance(key, str) else None
    if found is None:
        raise MundoError(f"«{str(key)[:40]}» no es la clave de ningún trozo del mundo.")
    if key in ("zonas", "teleportes"):
        return (key, "")
    if found["a"]:
        return ("a", found["a"])
    if found["i"]:
        return ("i", found["i"])
    cx, cz = int(found["cx"]), int(found["cz"])
    if not (-CELL_LIMIT <= cx < CELL_LIMIT and -CELL_LIMIT <= cz < CELL_LIMIT):
        raise MundoError(f"La celda {cx}, {cz} cae fuera del mundo.")
    return ("c", cx, cz)


def cell_key(x: float, z: float) -> str:
    """Clave de la celda donde cae el punto (x, z) del mundo."""
    return f"c/{math.floor(x / CELL)}/{math.floor(z / CELL)}"


def _zones(value: object) -> dict[str, dict[str, float]]:
    if not isinstance(value, dict):
        raise MundoError("Las zonas no son un objeto.")
    half = CELL * CELL_LIMIT
    clean = {}
    for key, zone in value.items():
        if key not in EDITABLE_ZONES:
            raise MundoError(f"La zona «{str(key)[:40]}» no se puede mover desde el editor.")
        z = _object(zone, {"x", "z", "r"}, f"La zona «{key}»")
        clean[key] = {
            "x": _number(z.get("x"), -half, half, f"La zona «{key}»: x"),
            "z": _number(z.get("z"), -half, half, f"La zona «{key}»: z"),
            "r": _number(z.get("r"), 1, MAX_ZONE_RADIUS, f"La zona «{key}»: el radio"),
        }
    return clean


def _teleports(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_TELEPORTS:
        raise MundoError(f"Puede haber {MAX_TELEPORTS} teleportes como mucho.")
    half = CELL * CELL_LIMIT
    clean = []
    for i, item in enumerate(value):
        what = f"El teleporte {i + 1}"
        t = _object(item, {"nombre", "x", "z"}, what)
        clean.append(
            {
                "nombre": _name(t.get("nombre"), what),
                "x": _number(t.get("x"), -half, half, f"{what}: x"),
                "z": _number(t.get("z"), -half, half, f"{what}: z"),
            }
        )
    return clean


def _cell(value: object, cx: int, cz: int) -> dict[str, Any]:
    d = _object(value, {"objetos", "terreno"}, f"La celda {cx}, {cz}")
    objects = d.get("objetos", [])
    if not isinstance(objects, list) or len(objects) > MAX_CELL_OBJECTS:
        raise MundoError(f"Una celda puede tener {MAX_CELL_OBJECTS} objetos como mucho.")
    x0, z0 = cx * CELL, cz * CELL
    clean_objects = []
    for i, item in enumerate(objects):
        what = f"El objeto {i + 1} de la celda {cx}, {cz}"
        obj = _placed(item, what)
        pos = obj["pos"]
        _number(pos[1], MIN_Y, MAX_Y, f"{what}: la altura")
        if not (x0 <= pos[0] < x0 + CELL and z0 <= pos[2] < z0 + CELL):
            raise MundoError(f"{what} no cae dentro de su celda.")
        clean_objects.append(obj)
    _unique_slots(clean_objects)
    clean: dict[str, Any] = {"objetos": clean_objects}
    if "terreno" in d:
        clean["terreno"] = _terrain(d["terreno"], CELL_TERRAIN_SIDE)
    return clean


def _placed(item: object, what: str) -> dict[str, Any]:
    """Un objeto colocado, limpio, sin mirar dónde cae (eso lo mira su trozo)."""
    o = _object(item, {"asset", "pos", "rot", "esc", "tinte", "datos"}, what)
    asset = o.get("asset")
    if not isinstance(asset, str) or not _ID.fullmatch(asset):
        raise MundoError(f"{what} usa un asset que no existe.")
    obj = {
        "asset": asset,
        "pos": _vector(o.get("pos"), -math.inf, math.inf, f"{what}: la posición"),
        "rot": _vector(o.get("rot"), -MAX_ROTATION, MAX_ROTATION, f"{what}: el giro"),
        "esc": _vector(o.get("esc"), MIN_SCALE, MAX_SCALE, f"{what}: la escala"),
    }
    if "tinte" in o:
        obj["tinte"] = _number(o["tinte"], MIN_TINT, MAX_TINT, f"{what}: el tinte")
    if "datos" in o and (data := _data(o["datos"], what)):
        obj["datos"] = data
    return obj


def _room(value: object, room_id: str) -> dict[str, Any]:
    """Un interior limpio: su planta, sus colores y sus objetos dentro de ella."""
    what = f"El interior «{room_id}»"
    keys = {"nombre", "ancho", "fondo", "alto", "suelo", "pared", "piso", "objetos"}
    d = _object(value, keys, what)
    width = _number(d.get("ancho"), MIN_ROOM_SIDE, MAX_ROOM_SIDE, f"{what}: el ancho")
    depth = _number(d.get("fondo"), MIN_ROOM_SIDE, MAX_ROOM_SIDE, f"{what}: el fondo")
    height = _number(d.get("alto"), MIN_ROOM_HEIGHT, MAX_ROOM_HEIGHT, f"{what}: el alto")
    objects = d.get("objetos", [])
    if not isinstance(objects, list) or len(objects) > MAX_ROOM_OBJECTS:
        raise MundoError(f"{what} puede tener {MAX_ROOM_OBJECTS} objetos como mucho.")
    clean_objects = []
    for i, item in enumerate(objects):
        where = f"El objeto {i + 1} del interior «{room_id}»"
        obj = _placed(item, where)
        x, y, z = obj["pos"]
        if abs(x) > width / 2 or abs(z) > depth / 2 or not 0 <= y <= height:
            raise MundoError(f"{where} no cae dentro de la habitación.")
        clean_objects.append(obj)
    numbers = slot_numbers(clean_objects)
    if any(n > MAX_USABLES for n in numbers):
        raise MundoError(f"{what}: los usables van numerados de 1 a {MAX_USABLES}.")
    if len(set(numbers)) != len(numbers):
        raise MundoError(f"{what}: hay dos usables con el mismo número.")
    clean: dict[str, Any] = {
        "nombre": _name(d.get("nombre"), what),
        "ancho": width,
        "fondo": depth,
        "alto": height,
        "suelo": _color(d.get("suelo"), f"{what}: el suelo"),
        "pared": _color(d.get("pared"), f"{what}: las paredes"),
    }
    if "piso" in d:
        if d["piso"] is not True:
            raise MundoError(f"{what}: «piso» solo puede ser true.")
        clean["piso"] = True
    clean["objetos"] = clean_objects
    return clean


def slot_numbers(objects: Iterable[dict[str, Any]]) -> list[int]:
    """Los números de tragaperras (`datos.n`) de unos objetos limpios, en orden."""
    return [o["datos"]["n"] for o in objects if "n" in o.get("datos", {})]


def _unique_slots(objects: Iterable[dict[str, Any]]) -> None:
    numbers = slot_numbers(objects)
    repeated = sorted({n for n in numbers if numbers.count(n) > 1})
    if repeated:
        listed = ", ".join(map(str, repeated))
        raise MundoError(f"Hay dos tragaperras con el mismo número ({listed}).")


def validate_piece(
    key: str, doc: object, glbs: dict[str, int]
) -> tuple[dict[str, Any], frozenset[str]]:
    """Comprueba un trozo y lo devuelve limpio, con los assets que usa.

    Que esos assets existan lo comprueba quien guarda (`UniversoMundoRepository.save`), en la
    misma transacción.

    Args:
        key: Su clave (`parse_key`).
        doc: El JSON del trozo, ya decodificado.
        glbs: Los GLB subidos que nombra (resumen → triángulos); uno que falte no vale.

    Raises:
        MundoError: Con el primer problema, en un texto para el administrador.
    """
    kind = parse_key(key)
    if kind[0] == "zonas":
        d = _object(doc, {"zonas"}, "Las zonas")
        return {"zonas": _zones(d.get("zonas"))}, frozenset()
    if kind[0] == "teleportes":
        d = _object(doc, {"teleportes"}, "Los teleportes")
        return {"teleportes": _teleports(d.get("teleportes"))}, frozenset()
    if kind[0] == "a":
        return _asset(doc, f"El asset «{kind[1]}»", glbs), frozenset()
    if kind[0] == "i":
        room = _room(doc, str(kind[1]))
        return room, frozenset(o["asset"] for o in room["objetos"])
    clean = _cell(doc, int(kind[1]), int(kind[2]))
    return clean, frozenset(o["asset"] for o in clean["objetos"])


def is_empty(doc: dict[str, Any]) -> bool:
    """Si un trozo limpio no lleva nada: se guarda como borrado."""
    return doc in ({"zonas": {}}, {"objetos": []})


def encode_piece(doc: dict[str, Any]) -> str:
    """Un trozo en JSON compacto, comprobando su peso.

    Raises:
        MundoError: Si pasa de `MAX_PIECE_BYTES`.
    """
    text = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    if len(text.encode()) > MAX_PIECE_BYTES:
        raise MundoError(f"Un trozo del mundo pesa más de {MAX_PIECE_BYTES // 1024} KB.")
    return text


def glb_refs(pieces: object) -> set[str]:
    """Resúmenes de los GLB que nombran unos trozos (clave → doc), sin validarlos."""
    if not isinstance(pieces, dict):
        return set()
    return {
        a["glb"]
        for a in pieces.values()
        if isinstance(a, dict) and isinstance(a.get("glb"), str) and _SHA256.fullmatch(a["glb"])
    }


def _split_terrain(terrain: dict[str, str]) -> dict[str, dict[str, str]]:
    """Parte el terreno de la isla (`TERRAIN_SIDE`² vértices desde -160, -160) en celdas."""
    heights = base64.b64decode(terrain["alturas"])
    colors = base64.b64decode(terrain["colores"])
    first = -TERRAIN_EXTENT // 2 // CELL
    per = CELL // TERRAIN_STEP
    out = {}
    for cz in range(first, -first):
        for cx in range(first, -first):
            h, c = bytearray(), bytearray()
            for k in range(CELL_TERRAIN_SIDE):
                start = ((cz - first) * per + k) * TERRAIN_SIDE + (cx - first) * per
                h += heights[2 * start : 2 * (start + CELL_TERRAIN_SIDE)]
                c += colors[start : start + CELL_TERRAIN_SIDE]
            out[f"c/{cx}/{cz}"] = {
                "alturas": base64.b64encode(bytes(h)).decode(),
                "colores": base64.b64encode(bytes(c)).decode(),
            }
    return out


def split_world(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Parte un mundo de un solo documento (formato 1, el `MundoDoc` de la página) en trozos.

    Hace lo mismo que `aTrozos` de `formato.ts`. Lo usan la migración de las tablas viejas y
    las comprobaciones del mundo de serie. Los trozos vacíos no salen. No valida nada.
    """
    pieces: dict[str, dict[str, Any]] = {}
    if doc.get("zonas"):
        pieces["zonas"] = {"zonas": doc["zonas"]}
    if "teleportes" in doc:
        pieces["teleportes"] = {"teleportes": doc["teleportes"]}
    for key, asset in doc.get("assets", {}).items():
        pieces[f"a/{key}"] = asset
    for key, room in doc.get("interiores", {}).items():
        pieces[f"i/{key}"] = room
    for obj in doc.get("objetos", []):
        cell = pieces.setdefault(cell_key(obj["pos"][0], obj["pos"][2]), {"objetos": []})
        cell["objetos"].append(obj)
    if doc.get("terreno"):
        for key, terrain in _split_terrain(doc["terreno"]).items():
            pieces.setdefault(key, {"objetos": []})["terreno"] = terrain
    return pieces


def validate_world(pieces: dict[str, Any], glbs: dict[str, int]) -> dict[str, dict[str, Any]]:
    """Comprueba un mundo entero en trozos, incluido que cada objeto tenga su asset.

    Raises:
        MundoError: Con el primer problema.
    """
    clean: dict[str, dict[str, Any]] = {}
    used: set[str] = set()
    for key, doc in pieces.items():
        clean[key], refs = validate_piece(key, doc, glbs)
        used |= refs
    assets = {key[2:] for key in clean if key.startswith("a/")}
    if used - assets:
        missing = ", ".join(sorted(used - assets))
        raise MundoError(f"Hay objetos que usan assets que no existen: {missing}.")
    _unique_slots(o for k, t in clean.items() if k.startswith("c/") for o in t["objetos"])
    return clean


def glb_summary(data: bytes) -> GlbSummary:
    """Comprueba un GLB (glTF 2.0 binario) y cuenta sus triángulos.

    Se rechaza lo que la página no podría cargar con el `GLTFLoader` de serie
    (extensiones obligatorias como Draco) y cualquier `uri`: un modelo que pidiera
    archivos o URLs de fuera haría que cada página los descargase.

    Raises:
        MundoError: Si no es un GLB válido, pesa o dibuja demasiado.
    """
    if len(data) > MAX_GLB_BYTES:
        raise MundoError(f"El modelo pesa más de {MAX_GLB_BYTES // 1024} KB.")
    if len(data) < 20 or data[:4] != _GLB_MAGIC:
        raise MundoError("El archivo no es un modelo .glb.")
    version, length = struct.unpack_from("<II", data, 4)
    if version != 2 or length != len(data):
        raise MundoError("El modelo no es glTF 2.0 o está cortado.")
    chunk_length, chunk_type = struct.unpack_from("<II", data, 12)
    if chunk_type != _CHUNK_JSON or 20 + chunk_length > len(data):
        raise MundoError("El modelo no tiene su descripción JSON.")
    try:
        gltf = json.loads(data[20 : 20 + chunk_length])
    except ValueError as error:
        raise MundoError("La descripción del modelo no es JSON.") from error
    if not isinstance(gltf, dict):
        raise MundoError("La descripción del modelo no es un objeto.")
    if gltf.get("extensionsRequired"):
        raise MundoError("El modelo usa extensiones (como Draco): expórtalo sin comprimir.")
    for kind in ("buffers", "images"):
        for item in gltf.get(kind) or []:
            if isinstance(item, dict) and "uri" in item:
                raise MundoError("El modelo enlaza archivos de fuera: expórtalo todo en el .glb.")
    triangles = _glb_triangles(gltf)
    if triangles == 0:
        raise MundoError("El modelo no tiene ningún triángulo.")
    if triangles > MAX_GLB_TRIANGLES:
        raise MundoError(
            f"El modelo tiene {triangles} triángulos y el tope es {MAX_GLB_TRIANGLES}."
        )
    return GlbSummary(hashlib.sha256(data).hexdigest(), triangles)


def _glb_triangles(gltf: dict[str, Any]) -> int:
    """Triángulos de todas las primitivas, contando cada malla tantas veces como se usa."""
    accessors = gltf.get("accessors") or []
    meshes = gltf.get("meshes") or []
    if not isinstance(accessors, list) or not isinstance(meshes, list):
        raise MundoError("La descripción del modelo está mal formada.")

    def count(index: object) -> int:
        if not isinstance(index, int) or not 0 <= index < len(accessors):
            raise MundoError("El modelo nombra datos que no tiene.")
        value = accessors[index].get("count") if isinstance(accessors[index], dict) else None
        if not isinstance(value, int) or value < 0:
            raise MundoError("La descripción del modelo está mal formada.")
        return value

    per_mesh = []
    for mesh in meshes:
        total = 0
        for prim in (mesh.get("primitives") or []) if isinstance(mesh, dict) else []:
            if not isinstance(prim, dict):
                continue
            mode = prim.get("mode", 4)
            if mode not in _TRIANGLE_MODES:
                continue
            attributes = prim.get("attributes") or {}
            n = count(prim["indices"]) if "indices" in prim else count(attributes.get("POSITION"))
            total += n // 3 if mode == 4 else max(n - 2, 0)
        per_mesh.append(total)
    nodes = gltf.get("nodes") or []
    uses = [n.get("mesh") for n in nodes if isinstance(n, dict) and isinstance(n.get("mesh"), int)]
    if not uses:
        return sum(per_mesh)
    return sum(per_mesh[m] for m in uses if 0 <= m < len(per_mesh))


def has_slots(pieces: dict[str, dict[str, Any]]) -> bool:
    """Si algún asset de unos trozos es una tragaperras."""
    return any(
        k.startswith("a/") and (t.get("funcion") or {}).get("tipo") == "traga"
        for k, t in pieces.items()
    )


#: Lo que vale el casino del Jovaniverso en `fortunas` (`casino_value`), en Y$: cada
#: tragaperras, cada letrero, cada forma básica de un asset de piezas y cada modelo (un GLB o
#: una malla, que no se cuentan por piezas).
CASINO_SLOT_VALUE = 100_000
CASINO_SIGN_VALUE = 25_000
CASINO_PIECE_VALUE = 1_500
CASINO_MODEL_VALUE = 15_000
#: Qué es del casino: lo que está a menos de estos metros (en planta) de una tragaperras.
CASINO_RADIUS = 15.0


def _build_worth(asset: dict[str, Any] | None) -> int:
    """Lo que vale un objeto del casino según su asset (0 si el asset no existe)."""
    if asset is None:
        return 0
    kind = (asset.get("funcion") or {}).get("tipo")
    if kind == "traga":
        return CASINO_SLOT_VALUE
    if kind == "letrero":
        return CASINO_SIGN_VALUE
    if asset.get("tipo") == "glb":
        return CASINO_MODEL_VALUE
    return CASINO_PIECE_VALUE * len(asset.get("piezas") or ()) + (
        CASINO_MODEL_VALUE if asset.get("malla") else 0
    )


def casino_value(pieces: dict[str, dict[str, Any]]) -> int:
    """Lo que vale lo construido del casino en un mundo, para su fortuna en `fortunas`.

    Es del casino todo objeto a menos de `CASINO_RADIUS` de una tragaperras (ellas
    incluidas): las paredes, el toldo y la decoración que se le ponga alrededor con el
    editor. Cuanto más se construya, más vale. Sin tragaperras, 0.

    Args:
        pieces: Los trozos del mundo (clave → documento ya leído), como en `add_casino`.
    """
    assets = {key[2:]: doc for key, doc in pieces.items() if key.startswith("a/")}
    objects = [
        obj for key, doc in pieces.items() if key.startswith("c/") for obj in doc.get("objetos", [])
    ]
    slots = [
        (obj["pos"][0], obj["pos"][2])
        for obj in objects
        if ((assets.get(obj["asset"]) or {}).get("funcion") or {}).get("tipo") == "traga"
    ]
    reach = CASINO_RADIUS**2
    return sum(
        _build_worth(assets.get(obj["asset"]))
        for obj in objects
        if any((obj["pos"][0] - x) ** 2 + (obj["pos"][2] - z) ** 2 <= reach for x, z in slots)
    )


def add_casino(pieces: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Los trozos que cambian al poner el casino de la plaza (`CASINO_FILE`) en un mundo.

    Para los mundos de antes de que las tragaperras fueran objetos: sin esto, el casino
    desaparecería al desplegar. No toca un mundo que ya tenga alguna tragaperras ni un asset
    que ya exista con la misma clave. Los objetos se suman a lo que ya tenga su celda.

    Args:
        pieces: Los trozos del mundo (clave → documento), sin los borrados.

    Returns:
        Clave → documento nuevo de cada trozo que cambia (vacío si no hace falta nada).
    """
    if has_slots(pieces):
        return {}
    casino = json.loads(CASINO_FILE.read_text(encoding="utf-8"))
    changed: dict[str, dict[str, Any]] = {}
    for key, asset in casino["assets"].items():
        if f"a/{key}" not in pieces:
            changed[f"a/{key}"] = asset
    for obj in casino["objetos"]:
        key = cell_key(obj["pos"][0], obj["pos"][2])
        if key not in changed:
            old = pieces.get(key, {"objetos": []})
            changed[key] = {**old, "objetos": list(old.get("objetos", []))}
        changed[key]["objetos"].append(obj)
    return changed
