"""Mundo editable del Jovaniverso: formato de los datos y su validación.

Los administradores construyen el mundo desde la propia página (`universo/src/editor/`):
montan assets con formas básicas o importan modelos GLB y los colocan. Lo que mandan
es entrada no confiable aunque venga de un administrador (otra pestaña, un script, un
GLB raro), así que todo pasa por aquí antes de guardarse en
`bot.repositories.universo_mundo`. La página dibuja lo mismo que se valida aquí; las
cifras de triángulos de cada forma tienen su par en `universo/src/editor/formato.ts`.

Formato (`FORMAT`), un objeto JSON:

```
{
  "v": 1,
  "assets": {
    "<id>": {"nombre": str, "tipo": "piezas", "piezas": [Pieza, ...], "malla"?: Malla,
             "colision": [[x, z, r], ...] | null, "distancia"?: float}
    "<id>": {"nombre": str, "tipo": "glb", "glb": "<sha256>", "colision": ..., "distancia"?: ...}
  },
  "objetos": [{"asset": "<id>", "pos": [x, y, z], "rot": [x, y, z], "esc": [x, y, z],
               "tinte"?: float}],
  "zonas": {"<lugar>": {"x": float, "z": float, "r": float}}
}
```

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
- `zonas` mueve los lugares de los logros que son un círculo (`EDITABLE_ZONES`). No se
  crean ni se borran: cada clave es un logro (`PLACES` de `bot.services.universo`).
- Un GLB va aparte, por su resumen (`glb_summary`), y el mundo solo lo nombra.

No guarda nada ni habla con Discord: solo dice si un mundo o un GLB valen.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from dataclasses import dataclass
from typing import Any

#: Versión del formato. Una página con otra no edita (recarga al desplegar).
FORMAT = 1
#: Lo más que puede pesar un mundo en JSON. El de serie migrado ronda los cientos de KB.
MAX_DOC_BYTES = 2 * 1024 * 1024
#: Topes de un mundo. Lo que se dibuja de verdad lo limita el presupuesto de la Biblia;
#: esto solo impide que un mundo tumbe la página o el servidor.
MAX_ASSETS = 400
MAX_OBJECTS = 20_000
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
#: El mundo mide `MAPA_LADO` (400 m) de lado, centrado en 0; un poco de margen para el mar.
WORLD_HALF = 260.0
MIN_Y, MAX_Y = -20.0, 120.0
MIN_SCALE, MAX_SCALE = 0.02, 60.0
MAX_ROTATION = 4 * math.pi
MAX_PIECE_OFFSET = 60.0
MAX_COLLISION = 40.0
MAX_ZONE_RADIUS = 60.0

_ID = re.compile(r"[a-z0-9_-]{1,40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_GLB_MAGIC = b"glTF"
_CHUNK_JSON = 0x4E4F534A
#: `mode` de las primitivas de glTF que dibujan triángulos (lista, tira y abanico).
_TRIANGLE_MODES = {4, 5, 6}

#: Un mundo vacío: lo que hay antes de que nadie publique nada.
EMPTY: dict[str, Any] = {"v": FORMAT, "assets": {}, "objetos": [], "zonas": {}}


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
    keys = {"nombre", "tipo", "piezas", "malla", "glb", "colision", "distancia"}
    a = _object(value, keys, what)
    name = _name(a.get("nombre"), what)
    kind = a.get("tipo")
    common: dict[str, Any] = {"colision": _collision(a.get("colision"), f"«{name}»")}
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


def asset_triangles(asset: dict[str, Any], glbs: dict[str, int]) -> int:
    """Triángulos de un asset ya validado (`glbs`: resumen → triángulos)."""
    if asset["tipo"] == "glb":
        return glbs[asset["glb"]]
    mesh = len(asset["malla"]["pos"]) // 9 if "malla" in asset else 0
    return mesh + sum(piece_triangles(p) for p in asset["piezas"])


def glb_refs(doc: object) -> set[str]:
    """Resúmenes de los GLB que nombra un mundo, sin validarlo (para buscarlos antes)."""
    assets = doc.get("assets") if isinstance(doc, dict) else None
    if not isinstance(assets, dict):
        return set()
    return {
        a["glb"]
        for a in assets.values()
        if isinstance(a, dict) and isinstance(a.get("glb"), str) and _SHA256.fullmatch(a["glb"])
    }


def validate_world(doc: object, glbs: dict[str, int]) -> dict[str, Any]:
    """Comprueba un mundo y lo devuelve limpio (solo los campos conocidos, en float).

    Args:
        doc: El JSON que manda la página, ya decodificado.
        glbs: Los GLB subidos que nombra (resumen → triángulos); uno que falte no vale.

    Raises:
        MundoError: Con el primer problema, en un texto para el administrador.
    """
    d = _object(doc, {"v", "assets", "objetos", "zonas"}, "El mundo")
    if d.get("v") != FORMAT:
        raise MundoError("El editor está desfasado: recarga la página.")
    assets = d.get("assets")
    if not isinstance(assets, dict) or len(assets) > MAX_ASSETS:
        raise MundoError(f"El mundo puede tener {MAX_ASSETS} assets como mucho.")
    clean_assets: dict[str, dict[str, Any]] = {}
    for key, value in assets.items():
        if not _ID.fullmatch(key):
            raise MundoError(f"«{key[:40]}» no vale como identificador de asset.")
        clean_assets[key] = _asset(value, f"El asset «{key}»", glbs)
    objects = d.get("objetos")
    if not isinstance(objects, list) or len(objects) > MAX_OBJECTS:
        raise MundoError(f"El mundo puede tener {MAX_OBJECTS} objetos como mucho.")
    clean_objects = []
    for i, value in enumerate(objects):
        what = f"El objeto {i + 1}"
        o = _object(value, {"asset", "pos", "rot", "esc", "tinte"}, what)
        if o.get("asset") not in clean_assets:
            raise MundoError(f"{what} usa un asset que no existe.")
        pos = _vector(o.get("pos"), -WORLD_HALF, WORLD_HALF, f"{what}: la posición")
        _number(pos[1], MIN_Y, MAX_Y, f"{what}: la altura")
        obj = {
            "asset": o["asset"],
            "pos": pos,
            "rot": _vector(o.get("rot"), -MAX_ROTATION, MAX_ROTATION, f"{what}: el giro"),
            "esc": _vector(o.get("esc"), MIN_SCALE, MAX_SCALE, f"{what}: la escala"),
        }
        if "tinte" in o:
            obj["tinte"] = _number(o["tinte"], MIN_TINT, MAX_TINT, f"{what}: el tinte")
        clean_objects.append(obj)
    zones = d.get("zonas")
    if not isinstance(zones, dict):
        raise MundoError("Las zonas no son un objeto.")
    clean_zones = {}
    for key, value in zones.items():
        if key not in EDITABLE_ZONES:
            raise MundoError(f"La zona «{str(key)[:40]}» no se puede mover desde el editor.")
        z = _object(value, {"x", "z", "r"}, f"La zona «{key}»")
        clean_zones[key] = {
            "x": _number(z.get("x"), -WORLD_HALF, WORLD_HALF, f"La zona «{key}»: x"),
            "z": _number(z.get("z"), -WORLD_HALF, WORLD_HALF, f"La zona «{key}»: z"),
            "r": _number(z.get("r"), 1, MAX_ZONE_RADIUS, f"La zona «{key}»: el radio"),
        }
    return {"v": FORMAT, "assets": clean_assets, "objetos": clean_objects, "zonas": clean_zones}


def encode_world(doc: dict[str, Any]) -> str:
    """El mundo en JSON compacto, comprobando su peso.

    Raises:
        MundoError: Si pasa de `MAX_DOC_BYTES`.
    """
    text = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    if len(text.encode()) > MAX_DOC_BYTES:
        raise MundoError(f"El mundo pesa más de {MAX_DOC_BYTES // 1024 // 1024} MB.")
    return text


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
