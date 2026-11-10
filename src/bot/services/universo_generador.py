"""Generador del mundo del Jovaniverso: islas enteras a partir de datos y reglas.

Herramienta de los desarrolladores (`docs/generar_mundo.py`, `docs/generador-mundo.md`): no la usa
el bot. Lee un relieve (alturas reales en una rejilla), las reglas de una isla (suelo por altura y
pendiente, pueblos, vegetación) y un kit de assets de formas básicas, y saca los trozos del mundo
(`a/<id>` y `c/<cx>/<cz>`, los mismos que guarda el editor, ver `bot.services.universo_editor`).

- **Determinista:** misma entrada y semilla, mismos bytes. El azar sale de `random.Random` con una
  semilla de texto por bloque o por celda (y de un ruido propio, `ruido`), nunca de `hash()`.
- **No pisa lo hecho a mano:** cada celda que escribe lleva `gen`, la firma de lo que la produjo
  (`firma`). Solo crea celdas que no existen o reescribe las que aún llevan `gen` (`decidir`); una
  sin él (tocada en el editor), una lápida (borrada a mano) o un asset que ya existe distinto se
  respetan.
- **Por tandas:** `generar` es un iterador (los assets primero y luego celda a celda) y `escribir`
  lee del borrador solo las claves de cada tanda y las guarda en una transacción, sin cargar el
  mundo entero.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import random
from array import array
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from bot.repositories.universo_mundo import Change, UniversoMundoRepository
from bot.services.universo_editor import (
    CELL,
    CELL_TERRAIN_SIDE,
    TERRAIN_STEP,
    cell_key,
    cell_summary,
    encode_piece,
    validate_piece,
)

#: Versión del generador: entra en la firma, así que subirla reescribe todas sus celdas.
VERSION = 1
#: Metros de un grado de latitud (y de longitud en el ecuador).
GRADO = 111_320.0
#: Lo que cabe en el `int16` de las alturas (cm); fuera de eso hace falta `base`.
INT16 = (-32768, 32767)


@dataclass(frozen=True, slots=True)
class Relieve:
    """Alturas reales (m, 0 o menos es mar) en una rejilla cuadrada, fila 0 al sur y al oeste."""

    ancho: int
    alto: int
    alturas: Sequence[float]


# --- Ruido y utilidades ---


def _azar(ix: int, iz: int, semilla: int) -> float:
    n = (ix * 374761393 + iz * 668265263 + semilla * 2246822519) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((n ^ (n >> 16)) & 0xFFFFFF) / 0x1000000


def ruido(x: float, z: float, escala: float, semilla: int) -> float:
    """Ruido de valor suave en [0, 1): el mismo en cualquier máquina."""
    gx, gz = x / escala, z / escala
    ix, iz = math.floor(gx), math.floor(gz)
    u, v = gx - ix, gz - iz
    u, v = u * u * (3 - 2 * u), v * v * (3 - 2 * v)
    a = _azar(ix, iz, semilla) * (1 - u) + _azar(ix + 1, iz, semilla) * u
    b = _azar(ix, iz + 1, semilla) * (1 - u) + _azar(ix + 1, iz + 1, semilla) * u
    return a * (1 - v) + b * v


def _suave(a: float, b: float, t: float) -> float:
    t = min(1.0, max(0.0, (t - a) / (b - a)))
    return t * t * (3 - 2 * t)


def _color(valor: Any) -> Any:
    """Los colores `#RRGGBB` del kit a entero, en cualquier sitio del documento."""
    if isinstance(valor, str) and len(valor) == 7 and valor.startswith("#"):
        return int(valor[1:], 16)
    if isinstance(valor, list):
        return [_color(v) for v in valor]
    if isinstance(valor, dict):
        return {k: _color(v) for k, v in valor.items()}
    return valor


def cargar_kit(raw: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, list[float]]]:
    """Los assets del kit (con `rot` por defecto y colores enteros) y sus huellas (ancho, fondo)."""
    assets = {}
    for clave, asset in raw["assets"].items():
        doc = _color(asset)
        doc["piezas"] = [{"rot": [0, 0, 0], **p} for p in doc["piezas"]]
        assets[clave] = doc
    return assets, raw["huellas"]


def firma(isla: dict[str, Any], kit: dict[str, Any], relieve: Relieve, semilla: int) -> str:
    """El `gen` de las celdas: la isla, la versión y un hash de toda la entrada."""
    h = hashlib.sha256()
    for parte in (isla, kit, semilla, VERSION):
        h.update(json.dumps(parte, sort_keys=True, ensure_ascii=False).encode())
    h.update(array("d", relieve.alturas).tobytes())
    return f"{isla['id']}:{VERSION}:{h.hexdigest()[:12]}"


# --- El suelo ---


def _distancias(tierra: bytearray, ancho: int, alto: int) -> list[float]:
    """Distancia (en muestras, chaflán 1/√2) de cada muestra a la tierra más cercana."""
    inf = float(ancho + alto)
    d = [0.0 if t else inf for t in tierra]
    r2 = math.sqrt(2)
    for j in range(alto):
        for i in range(ancho):
            k = j * ancho + i
            if d[k]:
                m = d[k]
                if i:
                    m = min(m, d[k - 1] + 1)
                if j:
                    m = min(m, d[k - ancho] + 1)
                    if i:
                        m = min(m, d[k - ancho - 1] + r2)
                    if i < ancho - 1:
                        m = min(m, d[k - ancho + 1] + r2)
                d[k] = m
    for j in range(alto - 1, -1, -1):
        for i in range(ancho - 1, -1, -1):
            k = j * ancho + i
            if d[k]:
                m = d[k]
                if i < ancho - 1:
                    m = min(m, d[k + 1] + 1)
                if j < alto - 1:
                    m = min(m, d[k + ancho] + 1)
                    if i < ancho - 1:
                        m = min(m, d[k + ancho + 1] + r2)
                    if i:
                        m = min(m, d[k + ancho - 1] + r2)
                d[k] = m
    return d


class Campo:
    """Las alturas del mundo (m) en la rejilla del relieve: la tierra a escala y el fondo del mar.

    El mar baja desde `costa.orilla` junto a la tierra hasta `costa.fondo` (el del mar de serie, que
    es lo que hay donde no se genera nada) a `costa.caida` metros: sin escalón con lo de alrededor.
    """

    def __init__(self, isla: dict[str, Any], relieve: Relieve) -> None:
        self.paso = isla["relieve"]["paso_m"] / isla["escala"]
        self.x0, self.z0 = isla["origen"]
        self.ancho, self.alto = relieve.ancho, relieve.alto
        costa = isla["costa"]
        self.fondo = float(costa["fondo"])
        vertical = isla["escala_vertical"]
        tierra = bytearray(1 if h > 0 else 0 for h in relieve.alturas)
        dist = _distancias(tierra, self.ancho, self.alto)
        orilla, caida = costa["orilla"], costa["caida"]
        self.h = [
            max(h / vertical, costa["tierra_min"])
            if t
            else orilla + (self.fondo - orilla) * _suave(0, caida, d * self.paso)
            for h, t, d in zip(relieve.alturas, tierra, dist, strict=True)
        ]
        borde = [
            self.h[j * self.ancho + i]
            for j in range(self.alto)
            for i in range(self.ancho)
            if i in (0, self.ancho - 1) or j in (0, self.alto - 1)
        ]
        if any(h != self.fondo for h in borde):
            raise ValueError("El relieve deja poco mar alrededor: amplía el recuadro.")

    def altura(self, x: float, z: float) -> float:
        """Altura bilineal en (x, z) del mundo; fuera del relieve, el fondo del mar."""
        gx, gz = (x - self.x0) / self.paso, (z - self.z0) / self.paso
        if not (0 <= gx < self.ancho - 1 and 0 <= gz < self.alto - 1):
            return self.fondo
        i, j = int(gx), int(gz)
        u, v = gx - i, gz - j
        k = j * self.ancho + i
        h = self.h
        a = h[k] * (1 - u) + h[k + 1] * u
        b = h[k + self.ancho] * (1 - u) + h[k + self.ancho + 1] * u
        return a * (1 - v) + b * v

    def caja(self) -> tuple[float, float, float, float]:
        """x0, z0, x1, z1 del relieve en el mundo."""
        return (
            self.x0,
            self.z0,
            self.x0 + (self.ancho - 1) * self.paso,
            self.z0 + (self.alto - 1) * self.paso,
        )


def pintura(reglas: Sequence[dict[str, Any]], alt: float, pend: float, r: float) -> int:
    """La pintura de la primera regla que se cumple (0, el color natural, si ninguna)."""
    for regla in reglas:
        if (
            alt >= regla.get("alt_min", -math.inf)
            and alt <= regla.get("alt_max", math.inf)
            and pend >= regla.get("pend_min", 0)
            and pend <= regla.get("pend_max", math.inf)
            and r >= regla.get("ruido_min", 0)
        ):
            return int(regla["pintura"])
    return 0


def terreno(alturas_cm: Sequence[int], colores: bytes) -> dict[str, Any]:
    """El terreno de una celda; con `base` (cm) si las alturas no caben en `int16`."""
    base = 0
    if min(alturas_cm) < INT16[0] or max(alturas_cm) > INT16[1]:
        base = (min(alturas_cm) + max(alturas_cm)) // 2
        if max(alturas_cm) - base > INT16[1] or min(alturas_cm) - base < INT16[0]:
            raise ValueError("Una celda tiene más de 655 m de desnivel.")
    h = array("h", (a - base for a in alturas_cm))
    out: dict[str, Any] = {
        "alturas": base64.b64encode(h.tobytes()).decode(),
        "colores": base64.b64encode(colores).decode(),
    }
    if base:
        out["base"] = base
    return out


# --- Pueblos ---


@dataclass(slots=True)
class Casa:
    asset: str
    x: float
    z: float
    y: float
    giro: float
    ancho: float
    fondo: float
    escala: float = 1.0


@dataclass(slots=True)
class Pueblo:
    """Un pueblo de manzanas en rejilla girada: casas del kit en la fachada de cada manzana."""

    datos: dict[str, Any]
    x: float
    z: float
    semilla: int
    casas: list[Casa] = field(default_factory=list)

    @property
    def periodo(self) -> tuple[float, float]:
        a, b = self.datos["manzana"]
        return a + self.datos["calle"], b + self.datos["calle"]

    def local(self, x: float, z: float) -> tuple[float, float]:
        c, s = math.cos(self.datos["giro"]), math.sin(self.datos["giro"])
        dx, dz = x - self.x, z - self.z
        return dx * c + dz * s, -dx * s + dz * c

    def mundo(self, u: float, v: float) -> tuple[float, float]:
        c, s = math.cos(self.datos["giro"]), math.sin(self.datos["giro"])
        return self.x + u * c - v * s, self.z + u * s + v * c

    def dentro(self, x: float, z: float) -> bool:
        """Dentro del pueblo: un círculo con el borde irregular."""
        r = self.datos["radio"] * (0.8 + 0.4 * ruido(x, z, 18, self.semilla + 7))
        return math.hypot(x - self.x, z - self.z) <= r

    def calle(self, x: float, z: float) -> bool:
        if not self.dentro(x, z):
            return False
        u, v = self.local(x, z)
        pu, pv = self.periodo
        return u % pu < self.datos["calle"] or v % pv < self.datos["calle"]

    def casa_en(self, x: float, z: float, margen: float) -> Casa | None:
        """La casa en la que cae (x, z), con `margen` m alrededor."""
        if math.hypot(x - self.x, z - self.z) > self.datos["radio"] * 1.3:
            return None
        for casa in self.casas:
            c, s = math.cos(casa.giro), math.sin(casa.giro)
            dx, dz = x - casa.x, z - casa.z
            lx, lz = dx * c - dz * s, dx * s + dz * c
            if (
                abs(lx) <= casa.ancho * casa.escala / 2 + margen
                and abs(lz) <= casa.fondo / 2 + margen
            ):
                return casa
        return None


def trazar_pueblo(
    datos: dict[str, Any],
    x: float,
    z: float,
    campo: Campo,
    huellas: dict[str, list[float]],
    semilla: int,
) -> Pueblo:
    """Pone las casas: en cada manzana, una fila mirando a cada calle larga, con huecos tapiados."""
    pueblo = Pueblo(datos, x, z, semilla)
    pu, pv = pueblo.periodo
    calle = datos["calle"]
    largo, hondo = datos["manzana"]
    n = math.ceil(datos["radio"] * 1.2 / min(pu, pv)) + 1
    tipos = sorted(datos["casas"])
    pesos = [datos["casas"][t] for t in tipos]
    alt_min, alt_max = datos["alt"]
    for l in range(-n, n + 1):  # noqa: E741
        for k in range(-n, n + 1):
            u0, v0 = k * pu + calle, l * pv + calle
            for lado, (v_frente, sentido) in enumerate(((v0, -1), (v0 + hondo, 1))):
                rng = random.Random(f"{semilla}:{datos['nombre']}:{k}:{l}:{lado}")
                u = u0
                while True:
                    hueco = rng.random() < datos["hueco"]
                    asset = datos["muro"] if hueco else rng.choices(tipos, pesos)[0]
                    ancho, fondo = huellas[asset]
                    if u + ancho > u0 + largo:
                        break
                    centro_v = v_frente - sentido * fondo / 2
                    cu = u + ancho / 2
                    u += ancho
                    puntos = [
                        pueblo.mundo(cu + du * ancho / 2, centro_v + dv * fondo / 2)
                        for du, dv in ((0, 0), (-1, -1), (1, -1), (-1, 1), (1, 1))
                    ]
                    if not pueblo.dentro(*puntos[0]):
                        continue
                    suelo = [campo.altura(px, pz) for px, pz in puntos]
                    if min(suelo) < alt_min or max(suelo) > alt_max:
                        continue
                    if max(suelo) - min(suelo) > datos["desnivel_max"]:
                        continue
                    # Mirando a +v, el giro del asset es -giro; a -v, media vuelta más.
                    giro = -datos["giro"] + (0 if sentido > 0 else math.pi)
                    pueblo.casas.append(
                        Casa(asset, *puntos[0], sum(suelo) / len(suelo), giro, ancho, fondo)
                    )
    return pueblo


# --- La isla entera ---


def _objeto(
    asset: str, x: float, y: float, z: float, giro: float, esc: float, tinte: float
) -> dict:
    o: dict[str, Any] = {
        "asset": asset,
        "pos": [round(x, 2), round(y, 2), round(z, 2)],
        "rot": [0, round(giro, 4), 0],
        "esc": [round(esc, 3)] * 3,
    }
    if round(tinte, 2) != 1:
        o["tinte"] = round(tinte, 2)
    return o


def mundo_de(isla: dict[str, Any], alto: int, lat: float, lon: float) -> tuple[float, float]:
    """(x, z) del mundo de un punto (lat, lon) del relieve de `alto` filas."""
    r = isla["relieve"]
    lat_media = r["sur"] + alto * r["paso_m"] / GRADO / 2
    este = (lon - r["oeste"]) * GRADO * math.cos(math.radians(lat_media))
    norte = (lat - r["sur"]) * GRADO
    return isla["origen"][0] + este / isla["escala"], isla["origen"][1] + norte / isla["escala"]


def generar(
    isla: dict[str, Any], kit_raw: dict[str, Any], relieve: Relieve, semilla: int = 1
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Los trozos de la isla: primero los assets del kit, luego cada celda con algo (fila a fila).

    Las celdas que quedan enteras en el fondo del mar de serie y sin objetos no salen.
    """
    assets, huellas = cargar_kit(kit_raw)
    gen = firma(isla, kit_raw, relieve, semilla)
    for clave, doc in assets.items():
        yield f"a/{clave}", doc
    campo = Campo(isla, relieve)
    pueblos = [
        trazar_pueblo(p, *mundo_de(isla, relieve.alto, p["lat"], p["lon"]), campo, huellas, semilla)
        for p in isla["pueblos"]
    ]
    por_celda: dict[str, list[dict[str, Any]]] = {}
    for pueblo in pueblos:
        for c in pueblo.casas:
            o = _objeto(c.asset, c.x, c.y, c.z, c.giro, c.escala, 1)
            por_celda.setdefault(cell_key(o["pos"][0], o["pos"][2]), []).append(o)
    x0, z0, x1, z1 = campo.caja()
    for cz in range(math.floor(z0 / CELL), math.floor(z1 / CELL) + 1):
        for cx in range(math.floor(x0 / CELL), math.floor(x1 / CELL) + 1):
            doc = _celda(isla, campo, pueblos, cx, cz, semilla, por_celda.get(f"c/{cx}/{cz}", []))
            if doc is not None:
                doc["gen"] = gen
                yield f"c/{cx}/{cz}", doc


def _celda(
    isla: dict[str, Any],
    campo: Campo,
    pueblos: list[Pueblo],
    cx: int,
    cz: int,
    semilla: int,
    casas: list[dict[str, Any]],
) -> dict[str, Any] | None:
    n = CELL_TERRAIN_SIDE
    e = n + 2  # con un vértice más alrededor, para la pendiente
    bx, bz = cx * CELL - TERRAIN_STEP, cz * CELL - TERRAIN_STEP
    h = []
    for j in range(e):
        for i in range(e):
            x, z = bx + i * TERRAIN_STEP, bz + j * TERRAIN_STEP
            # La explanada de cada casa, a su altura media (mitad desmonte, mitad relleno).
            casa = next((c for p in pueblos if (c := p.casa_en(x, z, 0.8))), None)
            h.append(casa.y if casa else campo.altura(x, z))
    dentro = [h[(j + 1) * e + i + 1] for j in range(n) for i in range(n)]
    objetos = list(casas)
    veg = _vegetacion(isla, campo, pueblos, cx, cz, semilla)
    objetos += veg
    if not objetos and all(a <= campo.fondo + 0.005 for a in dentro):
        return None
    colores = bytearray(n * n)
    escala_ruido = isla["ruido"]
    for j in range(n):
        for i in range(n):
            k = (j + 1) * e + i + 1
            gx = (h[k + 1] - h[k - 1]) / (2 * TERRAIN_STEP)
            gz = (h[k + e] - h[k - e]) / (2 * TERRAIN_STEP)
            x, z = cx * CELL + i * TERRAIN_STEP, cz * CELL + j * TERRAIN_STEP
            calle = next((p for p in pueblos if p.calle(x, z)), None)
            if calle is not None and h[k] > 0:
                colores[j * n + i] = calle.datos["pintura_calle"]
                continue
            r = ruido(x, z, escala_ruido, semilla)
            colores[j * n + i] = pintura(isla["suelo"], h[k], math.hypot(gx, gz), r)
    cm = [round(a * 100) for a in dentro]
    return {"objetos": objetos, "terreno": terreno(cm, bytes(colores))}


def _vegetacion(
    isla: dict[str, Any], campo: Campo, pueblos: list[Pueblo], cx: int, cz: int, semilla: int
) -> list[dict[str, Any]]:
    reglas = isla["vegetacion"]
    paso = reglas["paso"]
    rng = random.Random(f"{semilla}:veg:{cx}:{cz}")
    out = []
    lado = int(CELL // paso)
    for j in range(lado):
        for i in range(lado):
            x = cx * CELL + (i + rng.random()) * paso
            z = cz * CELL + (j + rng.random()) * paso
            tirada, giro, esc, tinte = rng.random(), rng.random(), rng.random(), rng.random()
            alt = campo.altura(x, z)
            if alt <= 0.5:
                continue
            pend = (
                math.hypot(
                    campo.altura(x + 1, z) - campo.altura(x - 1, z),
                    campo.altura(x, z + 1) - campo.altura(x, z - 1),
                )
                / 2
            )
            pueblo = next((p for p in pueblos if p.dentro(x, z)), None)
            acumulado = 0.0
            for especie in reglas["especies"]:
                en_pueblo = especie.get("zona", "campo") == "pueblo"
                if en_pueblo != (pueblo is not None):
                    continue
                lo, hi = especie.get("alt", (-math.inf, math.inf))
                if not lo <= alt <= hi or pend > especie.get("pend_max", math.inf):
                    continue
                if pueblo is not None and (pueblo.calle(x, z) or pueblo.casa_en(x, z, 1.5)):
                    continue
                acumulado += especie["prob"]
                if tirada < acumulado:
                    e0, e1 = especie.get("escala", (1, 1))
                    out.append(
                        _objeto(
                            especie["asset"],
                            x,
                            alt - 0.05,
                            z,
                            giro * 2 * math.pi,
                            e0 + (e1 - e0) * esc,
                            0.88 + 0.24 * tinte,
                        )
                    )
                    break
    return out


# --- Escribir sin pisar lo hecho a mano ---


@dataclass(slots=True)
class Plan:
    """Qué ha hecho (o haría, con `simular`) el generador con cada trozo."""

    crear: list[str] = field(default_factory=list)
    reescribir: list[str] = field(default_factory=list)
    igual: list[str] = field(default_factory=list)
    respetar: list[str] = field(default_factory=list)
    objetos: int = 0
    bytes: int = 0


def decidir(clave: str, texto: str, actual: tuple[str, int] | None) -> str:
    """`crear`, `reescribir`, `igual` o `respetar` un trozo, según lo que haya en el borrador.

    Se crea lo que no existe y se reescribe una celda que aún lleva `gen` (de cualquier versión).
    Se respeta una lápida (alguien la borró), una celda sin `gen` (hecha o tocada a mano) y un asset
    que ya existe distinto (alguien retocó el kit en el editor).
    """
    if actual is None:
        return "crear"
    viejo = actual[0]
    if viejo == texto:
        return "igual"
    if not viejo or clave.startswith("a/"):
        return "respetar"
    return "reescribir" if "gen" in json.loads(viejo) else "respetar"


async def escribir(
    repo: UniversoMundoRepository,
    guild_id: int,
    trozos: Iterable[tuple[str, dict[str, Any]]],
    autor: int,
    tanda: int = 100,
    simular: bool = False,
) -> Plan:
    """Valida cada trozo como el servidor y guarda en el borrador, por tandas, lo que toque.

    Raises:
        MundoError: Si un trozo no vale (no se guarda esa tanda ni las siguientes).
        Conflict: Si alguien guarda a la vez un trozo de la tanda.
    """
    plan = Plan()
    lote: list[tuple[str, dict[str, Any]]] = []

    async def vaciar() -> None:
        limpios = {}
        for clave, doc in lote:
            limpio, refs = validate_piece(clave, doc, {})
            limpios[clave] = (limpio, refs, encode_piece(limpio))
        actuales = await repo.draft_pieces(guild_id, limpios)
        cambios = {}
        for clave, (limpio, refs, texto) in limpios.items():
            que = decidir(clave, texto, actuales.get(clave))
            getattr(plan, que).append(clave)
            plan.objetos += len(limpio.get("objetos", ()))
            plan.bytes += len(texto.encode())
            if que in ("crear", "reescribir"):
                rev = actuales[clave][1] if clave in actuales else 0
                resumen = cell_summary(limpio) if clave.startswith("c/") else ""
                cambios[clave] = Change(rev, texto, refs, resumen)
        if cambios and not simular:
            await repo.save(guild_id, cambios, autor)
        lote.clear()

    for trozo in trozos:
        lote.append(trozo)
        if len(lote) >= tanda:
            await vaciar()
    if lote:
        await vaciar()
    return plan
