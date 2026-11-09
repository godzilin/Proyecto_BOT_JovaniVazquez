"""Sala del Jovaniverso en local: para probar el multijugador y para medirlo.

Levanta en `http://localhost:8080/api/sala` la misma sala que el bot
(`bot.services.universo_sala.Salas`), sin Discord ni login: el saludo es
`{"t": "hola", "v": 2, "dev": "<nombre>"}` y cada conexión es una persona nueva. Con Vite en
marcha (`npm run dev` en `universo/`, que reenvía `/api` aquí) se abren dos pestañas
y cada una ve a la otra.

Con `--medir N` además conecta N clientes falsos que se mueven a 10 poses por segundo
durante `--segundos` (en la plaza o, con `--repartir`, cada uno en un sitio al azar de un
cuadrado de ese lado en metros) y dice cuánta CPU ha gastado el servidor, lo que tarda
cada tick y cuánto pesa lo que manda. Así se comprueba la regla de «Jovaniverso: rendimiento imperativo» de la
Biblia: el tick tiene que ser barato con decenas de jugadores. El proceso se ata a 4
hilos (`--hilos`) para parecerse al NAS; el N100 va ~1,5 veces más lento por hilo que
un PC de sobremesa. Referencia (08/11/2026, PC): 50 jugadores en la plaza, ~3 % de un
hilo, ticks de ~1 ms y 4,2 KB/s por cliente; repartidos en 20 km, 2 %, 0,3 ms y casi
nada (más cifras en `docs/mundo-celdas.md`, «La red»).

Con `?debug` y fuera de Discord, la página llama además a `POST /api/entrar` y `POST
/api/traga` de pruebas (`entrar` y `traga` aquí): las tragaperras de la plaza juegan
con saldo en memoria (sin dinero real) y las mismas piezas del bot (`SlotMachine`,
`line_payout`, `spin_states`, `still_state` y `Seats`), así que devuelven lo mismo que
el servidor de verdad (`Slots.plaza`) salvo impuestos, logros y la banca.

    python docs/sala_local.py                # servidor para probar con Vite
    python docs/sala_local.py --medir 50     # 50 clientes falsos en la plaza, 20 s
    python docs/sala_local.py --medir 50 --repartir 20000  # repartidos en 20 × 20 km
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import math
import os
import random
import struct
import sys
import time

import aiohttp
from aiohttp import WSMsgType, web

from bot.services import universo_sala
from bot.services.economy import format_amount
from bot.services.slots import (
    BONUS_MAX,
    HEAT_MAX,
    POT_SEED,
    REEL_STRIPS,
    BonusMeter,
    SlotMachine,
    add_bonus,
    bar_fill,
    line_payout,
    next_heat,
    pot_share,
    win_tier,
)
from bot.services.slots_render import SYMBOL_FILES
from bot.services.slots_scene import spin_states, still_state
from bot.services.universo import GESTURES
from bot.services.universo_botes import pot_catalog, pot_list
from bot.services.universo_editor import MAX_SLOTS
from bot.services.universo_sala import Salas
from bot.services.universo_tragas import MACHINES, MIN_GAP_SECONDS, SeatError, Seats

GUILD = 1
USER = 1
START_BALANCE = 100_000
_ids = itertools.count(1)
#: Lo que añade el saludo y cómo va una pose (las de antes de la 10.11 no tenían versión).
HELLO = {"v": universo_sala.PROTOCOL} if hasattr(universo_sala, "PROTOCOL") else {}
pose_bytes = getattr(
    universo_sala,
    "pack_pose",
    lambda x, y, z, yaw, anim: struct.pack("<BhhhBB", 1, x, y, z, yaw, anim),
)


def pin(threads: int) -> None:
    """Ata este proceso a `threads` procesadores (como `docs/medir_dibujos.py`)."""
    count = os.cpu_count() or threads
    step = 2 if count >= 2 * threads else 1
    cpus = [i * step for i in range(min(threads, count))]
    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, cpus)
    elif sys.platform == "win32":
        import ctypes

        kernel = ctypes.windll.kernel32
        kernel.SetProcessAffinityMask(kernel.GetCurrentProcess(), sum(1 << c for c in cpus))


class TimedSalas(Salas):
    """La sala de siempre, apuntando lo que tarda cada tick."""

    ticks: list[float]

    async def tick(self, room: universo_sala.Room) -> None:
        start = time.perf_counter()
        await super().tick(room)
        self.ticks.append(time.perf_counter() - start)


async def sala(request: web.Request) -> web.WebSocketResponse:
    salas: Salas = request.app["salas"]
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=1024)
    await ws.prepare(request)
    hello = await ws.receive_json(timeout=10)
    if hello.get("v") != universo_sala.PROTOCOL:
        await ws.send_json({"t": "error", "error": universo_sala.OLD_PAGE})
        await ws.close()
        return ws
    user_id = next(_ids)
    name = str(hello.get("dev") or f"Prueba {user_id}")[:32]
    transport = request.transport
    # En local todos tienen todos los gestos, como si los hubieran comprado.
    player = await salas.join(
        GUILD,
        user_id,
        name,
        ws,
        lambda: transport.get_write_buffer_size() if transport else 0,
        frozenset(GESTURES),
    )
    try:
        async for message in ws:
            if message.type == WSMsgType.BINARY:
                salas.pose(GUILD, player, message.data)
            elif message.type == WSMsgType.TEXT:
                salas.text(GUILD, player, message.data)
    finally:
        await salas.leave(GUILD, player)
    return ws


class Casino:
    """La plaza de pruebas: un jugador con saldo en memoria y sus máquinas."""

    def __init__(self) -> None:
        self.seats = Seats()
        self.machine = SlotMachine()
        self.balance = START_BALANCE
        self.pot = POT_SEED
        self.heat = [0] * (MAX_SLOTS + 1)
        self.bonus = [BonusMeter()] * (MAX_SLOTS + 1)

    def pots(self) -> list[dict[str, str]]:
        """Los botes de los letreros: el de verdad de esta plaza y cifras fijas para el resto."""
        fixed = {"poker": 12_345, "caballos": 250_000, "primitiva": 1_000_000}
        return pot_list({"tragas": self.pot, **fixed})

    def meta(self) -> dict:
        return {
            "machines": MACHINES,
            "min_gap": MIN_GAP_SECONDS,
            "strips": list(REEL_STRIPS),
            "symbols": dict(SYMBOL_FILES),
            "stills": [still_state(self.machine.spin(), highlight=False) for _ in range(MACHINES)],
            "pot_text": format_amount(self.pot),
        }

    def play(self, machine: int, stake: int, auto: bool) -> dict:
        """Una tirada como `Slots.plaza`, sin impuestos ni logros. Lanza `SeatError`."""
        now = time.monotonic()
        self.seats.sweep(now)
        turn = self.seats.begin(GUILD, USER, machine, stake=stake, auto=auto, now=now)
        paid = 0 if turn.free else turn.stake
        if paid > self.balance:
            self.seats.stop(GUILD, USER, machine)
            raise SeatError(f"No tienes bastante saldo: {format_amount(self.balance)}.")
        spin = self.machine.spin()
        jackpot = self.pot if spin.is_jackpot else 0
        won = line_payout(spin, turn.stake) + jackpot
        self.pot = POT_SEED if jackpot else self.pot + (0 if turn.free else pot_share(paid))
        self.balance += won - paid
        tier = win_tier(won, turn.stake)
        self.heat[machine] = next_heat(self.heat[machine], paid=won > paid, was_hot=False)
        bonus_stake = 0
        if not turn.free:
            self.bonus[machine], bonus_stake = add_bonus(
                self.bonus[machine], spin, paid, random.randrange
            )
        big = tier is not None or jackpot > 0
        ended = self.seats.finish(
            GUILD, USER, machine, turn, now=now, net=won - paid, big_prize=big,
            triggers_free_spins=spin.triggers_free_spins, bonus_stake=bonus_stake,
        )  # fmt: skip
        seat = self.seats.seat(GUILD, USER, machine)
        text = f"### {'Premio' if won else 'Sin premio'}\n**{format_amount(won)}** cobrados"
        text += f"\n-# Apuesta {format_amount(paid)}" + (" (giro gratis)" if turn.free else "")
        return {
            "machine": machine,
            "states": spin_states(spin, won=won, stake=turn.stake),
            "stops": list(spin.stops),
            "stake": turn.stake,
            "bet": turn.bet,
            "free": turn.free,
            "won": won,
            "net": won - paid,
            "jackpot": jackpot,
            "big_prize": big,
            "tier": tier,
            "near_miss": spin.near_miss,
            "free_won": spin.triggers_free_spins or bool(bonus_stake),
            "balance": self.balance,
            "balance_text": format_amount(self.balance),
            "pot": self.pot,
            "pot_text": format_amount(self.pot),
            "heat": self.heat[machine],
            "heat_max": HEAT_MAX,
            "bonus": self.bonus[machine].points,
            "bonus_fill": bar_fill(self.bonus[machine].points, BONUS_MAX),
            "free_spins": seat.free_spins,
            "text": text,
            "auto": seat.auto is not None,
            "auto_stop": f"El Auto se ha parado: {ended[0].name}." if ended else None,
        }


async def entrar(request: web.Request) -> web.Response:
    """`/api/entrar` de pruebas: el perfil y la fila de máquinas, sin login."""
    casino: Casino = request.app["casino"]
    return web.json_response(
        {
            "name": "Prueba",
            "guild": "Local",
            "balance": casino.balance,
            "balance_text": format_amount(casino.balance),
            "version": "local",
            "gestures": sorted(GESTURES),
            "tragas": casino.meta(),
            "botes": casino.pots(),
        }
    )


async def botes(request: web.Request) -> web.Response:
    """`/api/botes` de pruebas: el bote de la tragaperras y unos de mentira para los letreros."""
    return web.json_response({"botes": request.app["casino"].pots(), "catalogo": pot_catalog()})


async def traga(request: web.Request) -> web.Response:
    """`/api/traga` de pruebas: una tirada, o parar el Auto de una máquina."""
    casino: Casino = request.app["casino"]
    body = await request.json()
    machine, stake = body.get("machine"), body.get("stake", 0)
    if type(machine) is not int or type(stake) is not int:
        return web.json_response({"error": "La tirada no es válida."}, status=400)
    if body.get("parar") is True:
        casino.seats.stop(GUILD, USER, machine)
        return web.json_response({"ok": True})
    try:
        return web.json_response(casino.play(machine, stake, body.get("auto") is True))
    except SeatError as error:
        return web.json_response({"error": str(error)}, status=400)


def centre(index: int, spread: float) -> tuple[int, int, int]:
    """Centro y radio (dm) de las vueltas de un cliente: la plaza o un sitio al azar."""
    if not spread:
        # La plaza (`PLAZA` de universo/src/mundo/terreno.ts: x -8 m, z 60 m).
        return -80, 600, 60 + 15 * (index % 8)
    rng = random.Random(index)
    half = spread * 5  # metros de lado → dm de medio lado
    return int(rng.uniform(-half, half)), int(rng.uniform(-half, half)), 60 + 15 * (index % 8)


async def fake_player(
    url: str, index: int, until: float, received: list[int], spread: float
) -> None:
    """Un cliente que da vueltas (por la plaza o por su sitio) y cuenta los bytes que recibe."""
    async with aiohttp.ClientSession() as session, session.ws_connect(url) as ws:
        await ws.send_json({"t": "hola", "dev": f"Bot {index}", **HELLO})

        async def read() -> None:
            async for message in ws:
                if message.type == WSMsgType.BINARY:
                    received[0] += len(message.data)

        reader = asyncio.create_task(read())
        step = 0
        cx, cz, radius = centre(index, spread)
        while time.monotonic() < until:
            # Vueltas a 8 m de altura, en decímetros.
            angle = step / 40 + index
            x = cx + int(math.cos(angle) * radius)
            z = cz + int(math.sin(angle) * radius)
            yaw = int((-angle % math.tau) / math.tau * 256) & 255
            await ws.send_bytes(pose_bytes(x, 80, z, yaw, 1))
            step += 1
            await asyncio.sleep(0.1)
        reader.cancel()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--medir", type=int, default=0, help="clientes falsos")
    parser.add_argument("--segundos", type=float, default=20)
    parser.add_argument(
        "--repartir", type=float, default=0, help="lado en metros del mundo (0: todos en la plaza)"
    )
    parser.add_argument("--hilos", type=int, default=4, help="procesadores (0: sin atar)")
    parser.add_argument("--puerto", type=int, default=8080)
    args = parser.parse_args()

    app = web.Application()
    # Como en el bot: la sala comprueba la velocidad y cuenta el paseo (aquí, sin logros).
    app["salas"] = salas = TimedSalas(on_walk=lambda _guild, _player, _stroll: None)
    salas.ticks = []
    app["casino"] = Casino()
    app.router.add_get("/api/sala", sala)
    app.router.add_post("/api/entrar", entrar)
    app.router.add_post("/api/traga", traga)
    app.router.add_get("/api/botes", botes)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "localhost", args.puerto).start()
    url = f"http://localhost:{args.puerto}/api/sala"
    if not args.medir:
        print(f"Sala local en {url}. Ctrl+C para salir.")
        await asyncio.Event().wait()

    # Los clientes falsos corren en otro proceso: lo que se mide es solo el servidor.
    if args.hilos:
        pin(args.hilos)
    started = time.monotonic()
    cpu0 = time.process_time()
    proc = await asyncio.create_subprocess_exec(
        sys.executable, __file__, "--clientes", str(args.medir), "--segundos",
        str(args.segundos), "--url", url, "--repartir", str(args.repartir),
    )  # fmt: skip
    await proc.wait()
    cpu = time.process_time() - cpu0
    wall = time.monotonic() - started
    print(
        f"{args.medir} jugadores, {wall:.1f} s: el servidor gastó {cpu:.2f} s de CPU "
        f"({100 * cpu / wall:.1f} % de un hilo)."
    )
    ticks = sorted(salas.ticks) or [0.0]
    print(
        f"{len(salas.ticks)} ticks: {1000 * sum(ticks) / len(ticks):.2f} ms de media, "
        f"{1000 * ticks[int(0.99 * (len(ticks) - 1))]:.2f} ms el percentil 99."
    )
    await runner.cleanup()


async def clients(count: int, seconds: float, url: str, spread: float) -> None:
    received = [0]
    until = time.monotonic() + seconds
    await asyncio.gather(*(fake_player(url, i, until, received, spread) for i in range(count)))
    per_client = received[0] / count / seconds / 1024
    print(f"Cada cliente recibió {per_client:.1f} KB/s.")


if __name__ == "__main__":
    if "--clientes" in sys.argv:
        cli = argparse.ArgumentParser()
        cli.add_argument("--clientes", type=int)
        cli.add_argument("--segundos", type=float)
        cli.add_argument("--url")
        cli.add_argument("--repartir", type=float)
        a = cli.parse_args()
        asyncio.run(clients(a.clientes, a.segundos, a.url, a.repartir))
    else:
        asyncio.run(main())
