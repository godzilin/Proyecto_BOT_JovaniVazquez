"""Los interiores del Jovaniverso con el bot real: entrar, usar, salir y quién ve a quién.

Mismo montaje que `test_universo_bot.py` (`BotClient` + `INITIAL_EXTENSIONS`, la sala por
WebSocket y las rutas con el cliente de pruebas de aiohttp). Sin nada publicado, la puerta y el
interior son los del piso de serie (`universo_piso.json`); con un mundo publicado, los de sus
trozos.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer
from test_universo_bot import GUILD_ID, USER_ID, World, fake_discord, load, member

from bot.cogs.universo.interiores import SERIE_FILE
from bot.repositories.universo_mundo import Change
from bot.services.universo_editor import encode_piece, split_world, validate_piece
from bot.services.universo_interiores import REPAIR_SECONDS
from bot.services.universo_sala import PROTOCOL, pack_pose, place

BEA = 777
SERIE = json.loads(SERIE_FILE.read_text(encoding="utf-8"))
DOOR = SERIE["objetos"][1]["pos"]
#: Delante de la puerta, a 1 m, y dentro, junto al sofá (usable 1) y la lámpara (usable 3).
OUTSIDE = (round((DOOR[0] + 1) * 10), round(DOOR[2] * 10))
SOFA = (round(DOOR[0] * 10), round((DOOR[2] - 2.2) * 10))
LAMP = (round((DOOR[0] - 1.7) * 10), round((DOOR[2] - 2.6) * 10))


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    discord_api = TestServer(fake_discord())
    await discord_api.start_server()
    client = await load(tmp_path)
    await client.universo_interiores.initialize()
    await client.housing.initialize()
    cog = client.get_cog("Universo")
    assert cog is not None
    members = {USER_ID: member(), BEA: member(BEA)}
    guild = SimpleNamespace(
        id=GUILD_ID,
        name="Servidor de pruebas",
        get_member=members.get,
        get_channel_or_thread=lambda _id: None,
        system_channel=None,
    )
    client.get_guild = lambda guild_id: guild if guild_id == GUILD_ID else None  # type: ignore[method-assign]
    web_client = TestClient(TestServer(cog.build_app()))
    await web_client.start_server()
    try:
        yield World(client, cog, web_client)
    finally:
        await web_client.close()
        await client.close()
        await discord_api.close()


async def connect(world: World, user_id: int, *, inside: bool = False) -> Any:
    ws = await world.web.ws_connect("/api/sala")
    session = world.cog.signer.issue(user_id)
    await ws.send_json(
        {
            "t": "hola",
            "v": PROTOCOL,
            "guild_id": str(GUILD_ID),
            "session": session,
            "dentro": inside,
        }
    )
    assert (await ws.receive_json())["t"] == "bienvenida"
    return ws


async def move(world: World, ws: Any, user_id: int, at: tuple[int, int]) -> None:
    """Manda una pose y espera a que la sala la tenga."""
    await ws.send_bytes(pack_pose(at[0], 80, at[1], 0, 0))
    player = world.cog.salas.find(GUILD_ID, user_id)
    for _ in range(100):
        if player.pose is not None and place(player.pose)[::2] == at:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("La sala no ha recibido la pose")


async def call(world: World, user_id: int, **body: object) -> tuple[int, dict[str, Any]]:
    headers = {"Authorization": f"Bearer {world.cog.signer.issue(user_id)}"}
    response = await world.web.post(
        "/api/interior", json={"guild_id": str(GUILD_ID), **body}, headers=headers
    )
    return response.status, await response.json()


async def until(ws: Any, wanted: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    """El primer mensaje JSON de la sala que cumple `wanted` (los demás se saltan)."""
    async with asyncio.timeout(3):
        while True:
            message = await ws.receive()
            if message.type == WSMsgType.TEXT and wanted(found := json.loads(message.data)):
                return found


async def stats(world: World, user_id: int = USER_ID) -> dict[str, int]:
    await world.client.get_cog("Achievements").flush()
    return (await world.client.achievements.profile(GUILD_ID, user_id)).stats


async def test_sin_piso_se_entra_en_el_piloto_se_usa_y_se_sale(world: World) -> None:
    ana = await connect(world, USER_ID)
    await move(world, ana, USER_ID, OUTSIDE)
    status, body = await call(world, USER_ID, accion="entrar", interior="piso-plaza")
    assert status == 200
    assert body == {
        "lugar": "piso-plaza",
        "propio": False,
        "piloto": True,
        "puedes": True,
        "estados": {"1": 1, "2": 1, "3": 0, "4": 0},  # muebles puestos y cosas rotas de serie
    }
    assert world.cog.salas.find(GUILD_ID, USER_ID).layer == "piso-plaza"
    # El sofá se quita y la lámpara se arregla, cada uno de cerca; lejos, no.
    await move(world, ana, USER_ID, (SOFA[0] + 40, SOFA[1] + 30))
    status, _ = await call(world, USER_ID, accion="usar", n=1)
    assert status == 403
    await move(world, ana, USER_ID, SOFA)
    assert await call(world, USER_ID, accion="usar", n=1) == (200, {"n": 1, "estado": 0})
    world.cog._usado.clear()  # sin esperar `USE_SECONDS`
    await move(world, ana, USER_ID, LAMP)
    assert await call(world, USER_ID, accion="usar", n=3) == (200, {"n": 3, "estado": 1})
    world.cog._usado.clear()
    assert (await call(world, USER_ID, accion="usar", n=3))[0] == 409  # ya está arreglada
    saved = await world.client.universo_interiores.states(GUILD_ID, "piso-plaza")
    assert {n: state for n, (state, _at) in saved.items()} == {1: 0, 3: 1}
    assert await call(world, USER_ID, accion="salir") == (200, {"ok": True})
    assert world.cog.salas.find(GUILD_ID, USER_ID).layer == ""
    assert (await call(world, USER_ID, accion="usar", n=1))[0] == 409
    await ana.close()
    got = await stats(world)
    assert got["universo_interiors"] == 1
    assert got["universo_pilot"] == 1
    assert got["universo_furniture_removed"] == 1
    assert got["universo_repairs"] == 1 and got["universo_repair_shared"] == 1
    profile = await world.client.achievements.profile(GUILD_ID, USER_ID)
    assert {
        "universo_interior_1",
        "universo_pilot",
        "universo_repairs_1",
    } <= profile.unlocked.keys()


async def test_lejos_de_la_puerta_no_se_entra(world: World) -> None:
    status, _ = await call(world, USER_ID, accion="entrar", interior="piso-plaza")
    assert status == 409  # aún no está en la sala
    ana = await connect(world, USER_ID)
    await move(world, ana, USER_ID, (OUTSIDE[0] + 100, OUTSIDE[1]))
    status, body = await call(world, USER_ID, accion="entrar", interior="piso-plaza")
    assert (status, body["error"]) == (403, "Acércate más a la puerta.")
    status, _ = await call(world, USER_ID, accion="entrar", interior="no-existe")
    assert status == 403
    assert world.cog.salas.find(GUILD_ID, USER_ID).layer == ""
    await ana.close()


async def test_el_dueno_de_un_piso_entra_en_el_suyo_y_los_demas_solo_miran(world: World) -> None:
    world.client.housing.home_of = AsyncMock(return_value=("propio", "calle-1"))
    ana = await connect(world, USER_ID)
    await move(world, ana, USER_ID, OUTSIDE)
    status, body = await call(world, USER_ID, accion="entrar", interior="piso-plaza")
    assert (status, body["lugar"], body["propio"]) == (200, f"piso-plaza@{USER_ID}", True)
    await move(world, ana, USER_ID, SOFA)
    assert (await call(world, USER_ID, accion="usar", n=1))[0] == 200
    # Su sofá quitado no toca el del piso piloto.
    assert await world.client.universo_interiores.states(GUILD_ID, "piso-plaza") == {}
    await ana.close()
    got = await stats(world)
    assert got["universo_home"] == 1 and got["universo_furniture_removed"] == 1
    assert "universo_pilot" not in got


async def test_los_de_fuera_no_ven_a_los_de_dentro_y_los_de_dentro_se_ven(world: World) -> None:
    ana = await connect(world, USER_ID)
    bea = await connect(world, BEA)
    await move(world, ana, USER_ID, OUTSIDE)
    await move(world, bea, BEA, (OUTSIDE[0] + 10, OUTSIDE[1]))
    ana_slot = world.cog.salas.find(GUILD_ID, USER_ID).slot
    async with asyncio.timeout(3):  # Bea ve a Ana (un tick con las dos)
        while len(await bea.receive_bytes()) < 1 + 5 + 2 * 9:
            pass
    assert (await call(world, USER_ID, accion="entrar", interior="piso-plaza"))[0] == 200
    # Bea, que la tenía al lado, deja de verla en cuanto entra.
    assert (await until(bea, lambda m: m.get("t") == "lejos"))["slots"] == [ana_slot]
    # Bea entra en el mismo sitio: ahora coinciden y lo que usa una le llega a la otra.
    assert (await call(world, BEA, accion="entrar", interior="piso-plaza"))[0] == 200
    await move(world, bea, BEA, SOFA)
    assert (await call(world, BEA, accion="usar", n=1))[0] == 200
    assert await until(ana, lambda m: m.get("t") == "uso") == {"t": "uso", "n": 1, "estado": 0}
    await ana.close()
    await bea.close()
    assert (await stats(world, BEA))["universo_interior_crowd_max"] == 2
    assert (await stats(world))["universo_interior_crowd_max"] == 2


async def test_al_volver_a_conectarse_sigue_dentro_solo_si_la_pagina_lo_dice(world: World) -> None:
    ana = await connect(world, USER_ID)
    await move(world, ana, USER_ID, OUTSIDE)
    assert (await call(world, USER_ID, accion="entrar", interior="piso-plaza"))[0] == 200
    await ana.close()
    ana = await connect(world, USER_ID, inside=True)  # la sala se cortó: sigue dentro
    assert world.cog.salas.find(GUILD_ID, USER_ID).layer == "piso-plaza"
    await ana.close()
    ana = await connect(world, USER_ID)  # ha recargado la página: está fuera
    assert world.cog.salas.find(GUILD_ID, USER_ID).layer == ""
    assert (GUILD_ID, USER_ID) not in world.cog._dentro
    await ana.close()


async def test_con_un_mundo_publicado_la_puerta_sale_de_sus_trozos(world: World) -> None:
    repo = world.client.universo_mundo
    pieces = split_world({**SERIE, "zonas": {}})
    # La misma puerta, 20 m más al este.
    cell = pieces["c/-1/0"]
    for obj in cell["objetos"]:
        obj["pos"][0] += 20
    changes = {}
    for key, doc in pieces.items():
        clean, refs = validate_piece(key, doc, {})
        changes[key] = Change(0, encode_piece(clean), refs)
    await repo.save(GUILD_ID, changes, 0)
    version = await repo.publish(GUILD_ID, 0)
    index = await repo.index(GUILD_ID, version)
    t = ",".join(f"{k}@{index.pieces[k][0]}" for k in ("c/-1/0", "i/piso-plaza", "a/sofa"))
    ana = await connect(world, USER_ID)
    await move(world, ana, USER_ID, OUTSIDE)  # donde estaba la de serie: ya no hay puerta
    status, _ = await call(world, USER_ID, accion="entrar", interior="piso-plaza", t=t)
    assert status == 403
    await move(world, ana, USER_ID, (OUTSIDE[0] + 200, OUTSIDE[1]))
    status, body = await call(world, USER_ID, accion="entrar", interior="piso-plaza", t=t)
    assert status == 200
    # Solo se ha mandado el asset del sofá: es el único usable que se puede usar.
    assert body["estados"] == {"1": 1}
    assert (await call(world, USER_ID, accion="entrar", interior="piso-plaza", t="c/0/0@1"))[
        0
    ] == 400
    await ana.close()


async def test_lo_arreglado_se_vuelve_a_romper_con_el_tiempo(world: World) -> None:
    lugar = "piso-plaza"
    old = 1_000.0
    await world.client.universo_interiores.set_state(GUILD_ID, lugar, 3, 1, USER_ID, old)
    await world.client.universo_interiores.set_state(GUILD_ID, lugar, 4, 1, USER_ID, 2e10)
    ana = await connect(world, USER_ID)
    await move(world, ana, USER_ID, OUTSIDE)
    _status, body = await call(world, USER_ID, accion="entrar", interior=lugar)
    assert REPAIR_SECONDS < 2e10 - old
    assert (body["estados"]["3"], body["estados"]["4"]) == (0, 1)
    await ana.close()


async def test_el_extraplano_va_aparte_y_solo_para_desarrolladores(world: World) -> None:
    body = {"accion": "entrar", "interior": "piso-plaza", "mundo": "pruebas"}
    status, _ = await call(world, USER_ID, **body, origen=[0, 0, 0], usables={"1": "mueble"})
    assert status == 403
    await world.client.universo_mundo.set_permission(GUILD_ID, USER_ID, "devtools", True, 0)
    ws = await world.web.ws_connect("/api/sala")
    session = world.cog.signer.issue(USER_ID)
    hello = {"t": "hola", "v": PROTOCOL, "guild_id": str(GUILD_ID), "session": session}
    await ws.send_json({**hello, "mundo": "pruebas"})
    assert (await ws.receive_json())["t"] == "bienvenida"
    await ws.send_bytes(pack_pose(0, 0, 0, 0, 0))
    player = world.cog.pruebas.find(GUILD_ID, USER_ID)
    for _ in range(100):
        if player.pose is not None:
            break
        await asyncio.sleep(0.01)
    status, got = await call(world, USER_ID, **body, origen=[0, 0, 0], usables={"1": "mueble"})
    assert (status, got["lugar"]) == (200, "pruebas/piso-plaza")
    assert await call(world, USER_ID, accion="usar", n=1, mundo="pruebas") == (
        200,
        {"n": 1, "estado": 0},
    )
    await ws.close()
    assert "universo_interiors" not in await stats(world)
