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

Con `?debug` y fuera de Discord, la página llama además a `POST /api/entrar` y `POST
/api/traga` de pruebas (`entrar` y `traga` aquí): las tragaperras de la plaza juegan
con saldo en memoria (sin dinero real) y las mismas piezas del bot (`SlotMachine`,
`line_payout`, `spin_states`, `still_state` y `Seats`), así que devuelven lo mismo que
el servidor de verdad (`Slots.plaza`) salvo impuestos, logros y la banca.

    python docs/sala_local.py                # servidor para probar con Vite
    python docs/sala_local.py --medir 50     # 50 clientes falsos, 20 s
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
from bot.services.universo_sala import POSE, Salas
from bot.services.universo_tragas import MACHINES, MAX_AT_ONCE, MIN_GAP_SECONDS, SeatError, Seats

GUILD = 1
USER = 1
START_BALANCE = 100_000
_ids = itertools.count(1)


async def sala(request: web.Request) -> web.WebSocketResponse:
    salas: Salas = request.app["salas"]
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=1024)
    await ws.prepare(request)
    hello = await ws.receive_json(timeout=10)
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
        self.heat = [0] * (MACHINES + 1)
        self.bonus = [BonusMeter()] * (MACHINES + 1)

    def meta(self) -> dict:
        return {
            "machines": MACHINES,
            "max_at_once": MAX_AT_ONCE,
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
        }
    )


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
    app["casino"] = Casino()
    app.router.add_get("/api/sala", sala)
    app.router.add_post("/api/entrar", entrar)
    app.router.add_post("/api/traga", traga)
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
