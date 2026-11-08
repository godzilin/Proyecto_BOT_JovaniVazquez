"""Editor del Jovaniverso en local: para probar el editor de assets sin Discord ni el bot.

Levanta en `http://localhost:8080` la aplicación web del cog de verdad
(`Universo(...).build_app()`) con un bot falso: un servidor con un único miembro
con permiso de editor y el mundo en un SQLite (`--db`, por defecto en el directorio temporal
del sistema, así que el borrador sobrevive entre ejecuciones). Añade la ruta
`GET /api/dev/sesion`, que da una sesión firmada de ese miembro; esa ruta solo existe
aquí.

    python docs/editor_local.py                 # servidor en :8080
    python docs/editor_local.py --db mundo.db   # con otra base de datos
    python docs/editor_local.py --sintetico 400 # y, si la base no tiene nada publicado, un
                                                # mundo denso de 400 celdas para medir la página

Con el servidor en marcha, en otra terminal `npm run dev` dentro de `universo/` (Vite
reenvía `/api` a este servidor) y se abre `http://localhost:5173/?editor`: sale el
botón «Editor» y guarda y publica contra este servidor. La sala multijugador no
conecta en este modo (no hay Discord que le diga quién entra).

`sintetico(repo, celdas)` es el generador del mundo de prueba (también lo usa
`docs/medir_mundo_servidor.py`): `celdas` celdas alrededor del 0 con terreno de colinas y unos
300 objetos de 7 assets de pocas piezas cada una, más zonas y teleportes.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import math
import random
import sqlite3
import tempfile
from array import array
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web

from bot.cogs.universo import Universo
from bot.repositories.universo_mundo import Change, UniversoMundoRepository
from bot.services.universo_editor import (
    CELL,
    CELL_LIMIT,
    CELL_TERRAIN_SIDE,
    TERRAIN_STEP,
    cell_summary,
    encode_piece,
    validate_piece,
)

GUILD_ID = 1
USER_ID = 2
#: Objetos por celda del mundo sintético.
OBJECTS_PER_CELL = 300
#: Celdas por guardado: la transacción no crece sin medida.
SAVE_BATCH = 100


def _piece(shape: str, pos: list[float], esc: list[float], color: int, **extra: object) -> dict:
    return {"forma": shape, "pos": pos, "rot": [0, 0, 0], "esc": esc, "color": color, **extra}


#: Siete assets de pocas piezas: clave → (pieza(s), círculo de colisión o None).
ASSETS: dict[str, tuple[list[dict], list[list[float]] | None]] = {
    "pino": (
        [
            _piece("cilindro", [0, 1, 0], [0.4, 2, 0.4], 0x6B4423),
            _piece("cono", [0, 3.5, 0], [2.4, 3.5, 2.4], 0x1F5A2E),
        ],
        [[0, 0, 0.5]],
    ),
    "palmera": (
        [
            _piece("cilindro", [0, 3, 0], [0.35, 6, 0.35], 0x8A6A3B),
            _piece("esfera", [0, 6.4, 0], [3, 1, 3], 0x2E8B3C),
        ],
        [[0, 0, 0.4]],
    ),
    "roca": ([_piece("icosaedro", [0, 0.5, 0], [1.6, 1.1, 1.4], 0x7A7A7A)], [[0, 0, 0.9]]),
    "arbusto": ([_piece("esfera", [0, 0.5, 0], [1.2, 0.9, 1.2], 0x3F7D2B)], None),
    "casa": (
        [
            _piece("caja", [0, 1.5, 0], [4, 3, 4], 0xE8E0CC),
            _piece("cuna", [0, 3.4, 0], [4.4, 1.6, 4.4], 0xB5483A),
        ],
        [[0, 0, 2.6]],
    ),
    "farola": (
        [
            _piece("cilindro", [0, 2, 0], [0.12, 4, 0.12], 0x333333),
            _piece("esfera", [0, 4.1, 0], [0.4, 0.4, 0.4], 0xFFE9A8),
        ],
        [[0, 0, 0.2]],
    ),
    "muro": ([_piece("caja", [0, 0.6, 0], [3, 1.2, 0.5], 0xA89F91)], [[0, 0, 1.4]]),
}


def _asset_doc(key: str) -> dict:
    pieces, collision = ASSETS[key]
    return {"nombre": key.capitalize(), "tipo": "piezas", "piezas": pieces, "colision": collision}


def _cell_terrain(cx: int, cz: int) -> tuple[dict[str, str], array]:
    """El terreno de la celda: colinas con ruido determinista que casan con las vecinas."""
    side = CELL_TERRAIN_SIDE
    xs = [cx * CELL + i * TERRAIN_STEP for i in range(side)]
    zs = [cz * CELL + i * TERRAIN_STEP for i in range(side)]
    # Separable (producto de una función de x y otra de z): barato y continuo entre celdas.
    sa, sb = [math.sin(x / 37) for x in xs], [math.cos(z / 41) for z in zs]
    sc, sd = [math.sin(x / 17 + 1) for x in xs], [math.sin(z / 23) for z in zs]
    heights = array(
        "h",
        (
            int(900 + 600 * sa[ix] * sb[iz] + 300 * sc[ix] * sd[iz])
            for iz in range(side)
            for ix in range(side)
        ),
    )
    colors = bytes(((ix // 7 + iz // 7) % 3) for iz in range(side) for ix in range(side))
    return (
        {
            "alturas": base64.b64encode(heights.tobytes()).decode(),
            "colores": base64.b64encode(colors).decode(),
        },
        heights,
    )


def _cell_doc(cx: int, cz: int, per_cell: int = OBJECTS_PER_CELL) -> dict:
    rng = random.Random(cx * 100_003 + cz)
    terrain, heights = _cell_terrain(cx, cz)
    keys = list(ASSETS)
    objects = []
    for _ in range(per_cell):
        x, z = rng.uniform(0, CELL - 0.01), rng.uniform(0, CELL - 0.01)
        ground = heights[round(z / TERRAIN_STEP) * CELL_TERRAIN_SIDE + round(x / TERRAIN_STEP)]
        size = round(rng.uniform(0.7, 1.4), 2)
        objects.append(
            {
                "asset": rng.choice(keys),
                "pos": [round(cx * CELL + x, 2), round(ground / 100, 2), round(cz * CELL + z, 2)],
                "rot": [0, round(rng.uniform(0, 6.28), 2), 0],
                "esc": [size, size, size],
            }
        )
    return {"objetos": objects, "terreno": terrain}


def _square(count: int) -> list[tuple[int, int]]:
    """Las `count` celdas más cercanas al 0 (dentro de `CELL_LIMIT`), en un cuadrado."""
    side = math.ceil(math.sqrt(count))
    first = -(side // 2)
    cells = [
        (cx, cz)
        for cz in range(first, first + side)
        for cx in range(first, first + side)
        if -CELL_LIMIT <= cx < CELL_LIMIT and -CELL_LIMIT <= cz < CELL_LIMIT
    ]
    cells.sort(key=lambda c: (c[0] * c[0] + c[1] * c[1], c))
    return cells[:count]


async def sintetico(
    repo: UniversoMundoRepository,
    celdas: int,
    guild_id: int = GUILD_ID,
    per_cell: int = OBJECTS_PER_CELL,
) -> int:
    """Mete y publica un mundo sintético si el servidor no tiene nada publicado.

    Usa el repositorio, como el editor: `save` y `publish`. Devuelve las celdas que ha puesto
    (0 si ya había algo publicado). Valida los assets, las zonas y una de cada 50 celdas.
    """
    if await repo.version(guild_id):
        return 0
    cells = _square(celdas)
    changes: dict[str, Change] = {}
    for key in ASSETS:
        doc, _refs = validate_piece(f"a/{key}", _asset_doc(key), {})
        changes[f"a/{key}"] = Change(0, encode_piece(doc))
    zones = {
        "zonas": {
            "plaza": {"x": 0, "z": 0, "r": 8},
            "faro": {"x": 40, "z": -30, "r": 6},
            "nublo": {"x": -60, "z": 50, "r": 10},
        }
    }
    tele = {
        "teleportes": [{"nombre": "Plaza", "x": 0, "z": 0}, {"nombre": "Faro", "x": 40, "z": -30}]
    }
    for key, doc in (("zonas", zones), ("teleportes", tele)):
        clean, _refs = validate_piece(key, doc, {})
        changes[key] = Change(0, encode_piece(clean))
    await repo.save(guild_id, changes, USER_ID)
    for start in range(0, len(cells), SAVE_BATCH):
        batch: dict[str, Change] = {}
        for n, (cx, cz) in enumerate(cells[start : start + SAVE_BATCH], start):
            key = f"c/{cx}/{cz}"
            doc = _cell_doc(cx, cz, per_cell)
            if n % 50 == 0:
                doc, _refs = validate_piece(key, doc, {})
            batch[key] = Change(0, encode_piece(doc), frozenset(ASSETS), cell_summary(doc))
        await repo.save(guild_id, batch, USER_ID)
    await repo.publish(guild_id, USER_ID)
    # El WAL de tanto guardar pesaría cientos de MB y frenaría cada lectura: como en el bot, que
    # lo vacía solo, se deja la base al día.
    connection = sqlite3.connect(repo.database_path)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()
    return len(cells)


def make_cog(repo: UniversoMundoRepository, port: int = 0) -> Universo:
    """El cog de verdad con un bot falso: un servidor y un miembro con permiso de editor."""
    admin = SimpleNamespace(
        id=USER_ID,
        bot=False,
        display_name="Admin local",
        guild_permissions=SimpleNamespace(administrator=True),
    )
    guild = SimpleNamespace(id=GUILD_ID, name="Local", get_member=lambda _id: admin)
    bot = SimpleNamespace(
        universo_mundo=repo,
        application_id=1,
        telemetry=SimpleNamespace(count=lambda *_a, **_k: None),
        get_guild=lambda guild_id: guild if guild_id == GUILD_ID else None,
        get_cog=lambda _name: None,  # sin juegos: los letreros salen sin cifras
    )
    return Universo(bot, client_secret="local", port=port)  # type: ignore[arg-type]


async def sesion(request: web.Request) -> web.Response:
    cog: Universo = request.app["cog"]
    return web.json_response({"session": cog.signer.issue(USER_ID), "guild_id": str(GUILD_ID)})


async def main(db: Path, port: int, celdas: int = 0, per_cell: int = OBJECTS_PER_CELL) -> None:
    repo = UniversoMundoRepository(db)
    await repo.initialize()
    await repo.set_permission(GUILD_ID, USER_ID, "editor", True, USER_ID)
    if celdas:
        puestas = await sintetico(repo, celdas, per_cell=per_cell)
        print(f"Mundo sintético: {puestas} celdas publicadas" if puestas else "Ya hay mundo")
    cog = make_cog(repo, port)
    app = cog.build_app()
    app["cog"] = cog
    app.router.add_get("/api/dev/sesion", sesion)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "localhost", port).start()
    print(f"Editor local en http://localhost:{port} (base de datos: {db})")
    try:
        await asyncio.Event().wait()
    finally:
        await cog.salas.close()
        await runner.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", type=Path, default=Path(tempfile.gettempdir()) / "jv_editor.db")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--sintetico",
        type=int,
        default=0,
        metavar="N",
        help="si no hay nada publicado, publica un mundo denso de N celdas",
    )
    parser.add_argument(
        "--objetos", type=int, default=OBJECTS_PER_CELL, help="objetos por celda del sintético"
    )
    args = parser.parse_args()
    try:
        asyncio.run(main(args.db, args.port, args.sintetico, args.objetos))
    except KeyboardInterrupt:
        pass
