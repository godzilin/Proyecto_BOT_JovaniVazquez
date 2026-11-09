"""Mide lo que gasta el servidor del Jovaniverso al servir un mundo grande por trozos.

Crea en un directorio temporal una base con un mundo sintético (`editor_local.sintetico`,
5000 celdas por defecto), levanta la aplicación del cog de verdad con el bot falso de
`docs/editor_local.py` y hace con el cliente de pruebas de aiohttp que 30 clientes falsos pidan
el índice y, luego, lotes de 64 celdas alrededor de puntos al azar (semilla fija), con y sin
gzip. Saca la memoria del proceso (RSS si hay `psutil`; el pico de `tracemalloc` en una pasada
aparte, porque frena), los bytes de la caché, el tiempo medio y el p95 por petición y el peso
del índice.

    PYTHONPATH=src python docs/medir_mundo_servidor.py [--celdas 5000] [--clientes 30]

El directorio temporal puede pesar cientos de MB (5000 celdas × 300 objetos) y generar el
mundo tarda una o dos decenas de segundos; se borra al acabar.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import gzip
import json
import random
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parent))

from editor_local import GUILD_ID, USER_ID, make_cog, sintetico  # noqa: E402

from bot.cogs.universo.mundo import MAX_LOTE  # noqa: E402
from bot.repositories.universo_mundo import UniversoMundoRepository  # noqa: E402

try:
    import psutil
except ImportError:  # pragma: no cover - opcional
    psutil = None


def rss_mb() -> float | None:
    """Memoria residente del proceso en MB (`None` sin `psutil`)."""
    return psutil.Process().memory_info().rss / 2**20 if psutil else None


def around(rng: random.Random, cells: set[tuple[int, int]]) -> list[str]:
    """Las `MAX_LOTE` celdas del bloque de 8 × 8 alrededor de un punto al azar (las que hay)."""
    cx, cz = rng.choice(sorted(cells))
    block = [(cx + dx, cz + dz) for dz in range(-4, 4) for dx in range(-4, 4)]
    return [f"c/{x}/{z}" for x, z in block if (x, z) in cells][:MAX_LOTE]


async def client(
    web: TestClient, session: str, version: int, rng: random.Random, gz: bool, times: dict
) -> None:
    """Un cliente: el índice y tres lotes de 64 celdas (con el resumen, el último)."""
    headers = {
        "Authorization": f"Bearer {session}",
        "Accept-Encoding": "gzip" if gz else "identity",
    }
    query = {"guild_id": str(GUILD_ID), "v": str(version)}

    async def get(kind: str, path: str, params: dict) -> bytes:
        # Solo el índice se guarda; los lotes se leen y se tiran para que los clientes (que
        # viven en este mismo proceso) no engorden la memoria que se mide.
        start = time.perf_counter()
        response = await web.get(path, params=params, headers=headers)
        chunks = []
        async for chunk in response.content.iter_chunked(64 * 1024):
            if kind == "índice" or response.status != 200:
                chunks.append(chunk)
        times.setdefault(kind, []).append(time.perf_counter() - start)
        assert response.status == 200, (path, response.status, b"".join(chunks)[:200])
        return b"".join(chunks)

    index = json.loads(await get("índice", "/api/mundo/indice", query))
    rows = index["trozos"]
    cells = {tuple(map(int, k.split("/")[1:])) for k in rows if k.startswith("c/")}
    for n in range(3):
        keys = around(rng, cells)
        t = ",".join(f"{k}@{rows[k]}" for k in keys)
        extra = {"resumen": "1"} if n == 2 else {}
        await get("lote" if n < 2 else "resumen", "/api/mundo/trozos", {**query, "t": t, **extra})


async def scenario(db: Path, clients: int, gz: bool) -> dict:
    """Una pasada con una caché vacía: la memoria, la caché y los tiempos."""
    repo = UniversoMundoRepository(db)
    cog = make_cog(repo)
    web = TestClient(TestServer(cog.build_app()))
    await web.start_server()
    try:
        gc.collect()
        before = rss_mb()
        session = cog.signer.issue(USER_ID)
        version = await cog.world_version(GUILD_ID)
        rng = random.Random(42)
        times: dict[str, list[float]] = {}
        started = time.perf_counter()
        await asyncio.gather(
            *(
                client(web, session, version, random.Random(rng.random()), gz, times)
                for _ in range(clients)
            )
        )
        total = time.perf_counter() - started
        after = rss_mb()
        return {
            "times": times,
            "total": total,
            "rss_before": before,
            "rss_after": after,
            "cache_mb": cog._cache.bytes / 2**20,
            "cache_items": len(cog._cache),
        }
    finally:
        await web.close()
        await cog.salas.close()


def show(name: str, result: dict) -> None:
    print(f"\n== {name} ==")
    for kind, values in result["times"].items():
        ordered = sorted(values)
        p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
        print(
            f"  {kind:<8} {len(values):>3} peticiones"
            f" · media {statistics.mean(values) * 1000:7.1f} ms · p95 {p95 * 1000:7.1f} ms"
        )
    rss = (
        f"RSS {result['rss_before']:.0f} → {result['rss_after']:.0f} MB"
        if result["rss_before"] is not None
        else "sin psutil: sin RSS"
    )
    print(
        f"  total {result['total']:.2f} s · {rss} · caché {result['cache_mb']:.1f} MB"
        f" en {result['cache_items']} entradas"
    )


async def main(celdas: int, clients: int) -> None:
    with tempfile.TemporaryDirectory() as folder:
        db = Path(folder) / "bot.sqlite3"
        repo = UniversoMundoRepository(db)
        await repo.initialize()
        start = time.perf_counter()
        await sintetico(repo, celdas)
        print(
            f"Mundo de {celdas} celdas: {time.perf_counter() - start:.1f} s,"
            f" {db.stat().st_size / 2**20:.0f} MB"
        )
        index = await repo.index(GUILD_ID)
        assert index is not None
        start = time.perf_counter()
        await repo.index(GUILD_ID)
        print(f"Leer el índice de la base: {(time.perf_counter() - start) * 1000:.0f} ms")
        cog = make_cog(repo)
        _version, text = await cog.world_index(GUILD_ID)  # type: ignore[misc]
        print(
            f"Índice: {len(index.pieces)} trozos · {len(text) / 1024:.0f} KB de texto"
            f" · {len(gzip.compress(text.encode(), 6)) / 1024:.0f} KB con gzip"
        )
        await cog.salas.close()
        for gz in (True, False):
            show(
                f"{clients} clientes, {'con' if gz else 'sin'} gzip",
                await scenario(db, clients, gz),
            )
        for gz in (True, False):
            tracemalloc.start()
            await scenario(db, clients, gz)
            current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            print(
                f"\ntracemalloc ({'con' if gz else 'sin'} gzip): al acabar {current / 2**20:.1f} MB"
                f", pico {peak / 2**20:.1f} MB"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--celdas", type=int, default=5000)
    parser.add_argument("--clientes", type=int, default=30)
    args = parser.parse_args()
    asyncio.run(main(args.celdas, args.clientes))
