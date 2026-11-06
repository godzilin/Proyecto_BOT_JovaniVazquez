"""Sala del Jovaniverso en local: para probar el multijugador y para medirlo.

Levanta en `http://localhost:8080/api/sala` la misma sala que el bot
(`bot.services.universo_sala.Salas`), sin Discord ni login: el saludo es
`{"t": "hola", "dev": "<nombre>"}` y cada conexión es una persona nueva. Con Vite en
marcha (`npm run dev` en `universo/`, que reenvía `/api` aquí) se abren dos pestañas
y cada una ve a la otra.

Con `--medir N` además conecta N clientes falsos que se mueven a 10 poses por segundo
durante `--segundos` y dice cuánta CPU ha gastado el servidor y cuánto pesa lo que
manda. Así se comprueba la regla de «Jovaniverso: rendimiento imperativo» de la
Biblia: el tick tiene que ser barato con decenas de jugadores. En Linux el proceso se
ata a 4 hilos para parecerse al NAS; el N100 va ~1,5 veces más lento por hilo que un
PC de sobremesa. Referencia (06/11/2026, PC): 50 jugadores, 3,4 % de un hilo y
4,1 KB/s por cliente.

    python docs/sala_local.py                # servidor para probar con Vite
    python docs/sala_local.py --medir 50     # 50 clientes falsos, 20 s
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import math
import os
import struct
import sys
import time

import aiohttp
from aiohttp import WSMsgType, web

from bot.services.universo_sala import POSE, Salas

GUILD = 1
_ids = itertools.count(1)


async def sala(request: web.Request) -> web.WebSocketResponse:
    salas: Salas = request.app["salas"]
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=1024)
    await ws.prepare(request)
    hello = await ws.receive_json(timeout=10)
    user_id = next(_ids)
    name = str(hello.get("dev") or f"Prueba {user_id}")[:32]
    transport = request.transport
    player = await salas.join(
        GUILD, user_id, name, ws, lambda: transport.get_write_buffer_size() if transport else 0
    )
    try:
        async for message in ws:
            if message.type == WSMsgType.BINARY:
                salas.pose(GUILD, player, message.data)
    finally:
        await salas.leave(GUILD, player)
    return ws


async def fake_player(url: str, index: int, until: float, received: list[int]) -> None:
    """Un cliente que da vueltas por la plaza y cuenta los bytes que recibe."""
    async with aiohttp.ClientSession() as session, session.ws_connect(url) as ws:
        await ws.send_json({"t": "hola", "dev": f"Bot {index}"})

        async def read() -> None:
            async for message in ws:
                if message.type == WSMsgType.BINARY:
                    received[0] += len(message.data)

        reader = asyncio.create_task(read())
        step = 0
        while time.monotonic() < until:
            # Vueltas por la plaza (`PLAZA` de universo/src/mundo/terreno.ts: x -8 m,
            # z 60 m, suelo a 8 m), en decímetros.
            angle = step / 40 + index
            radius = 60 + 15 * (index % 8)
            x = -80 + int(math.cos(angle) * radius)
            z = 600 + int(math.sin(angle) * radius)
            yaw = int((-angle % math.tau) / math.tau * 256) & 255
            await ws.send_bytes(struct.pack("<BhhhBB", POSE, x, 80, z, yaw, 1))
            step += 1
            await asyncio.sleep(0.1)
        reader.cancel()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--medir", type=int, default=0, help="clientes falsos")
    parser.add_argument("--segundos", type=float, default=20)
    parser.add_argument("--puerto", type=int, default=8080)
    args = parser.parse_args()

    app = web.Application()
    app["salas"] = Salas()
    app.router.add_get("/api/sala", sala)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "localhost", args.puerto).start()
    url = f"http://localhost:{args.puerto}/api/sala"
    if not args.medir:
        print(f"Sala local en {url}. Ctrl+C para salir.")
        await asyncio.Event().wait()

    # Los clientes falsos corren en otro proceso: lo que se mide es solo el servidor.
    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, set(range(4)))
    started = time.monotonic()
    cpu0 = time.process_time()
    proc = await asyncio.create_subprocess_exec(
        sys.executable, __file__, "--clientes", str(args.medir), "--segundos",
        str(args.segundos), "--url", url,
    )  # fmt: skip
    await proc.wait()
    cpu = time.process_time() - cpu0
    wall = time.monotonic() - started
    print(
        f"{args.medir} jugadores, {wall:.1f} s: el servidor gastó {cpu:.2f} s de CPU "
        f"({100 * cpu / wall:.1f} % de un hilo)."
    )
    await runner.cleanup()


async def clients(count: int, seconds: float, url: str) -> None:
    received = [0]
    until = time.monotonic() + seconds
    await asyncio.gather(*(fake_player(url, i, until, received) for i in range(count)))
    per_client = received[0] / count / seconds / 1024
    print(f"Cada cliente recibió {per_client:.1f} KB/s.")


if __name__ == "__main__":
    if "--clientes" in sys.argv:
        cli = argparse.ArgumentParser()
        cli.add_argument("--clientes", type=int)
        cli.add_argument("--segundos", type=float)
        cli.add_argument("--url")
        a = cli.parse_args()
        asyncio.run(clients(a.clientes, a.segundos, a.url))
    else:
        asyncio.run(main())
