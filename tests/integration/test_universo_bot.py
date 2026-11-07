"""El Jovaniverso con el bot real: servidor web, login, perfil, logros y `.universo`.

El bot se carga entero (`BotClient` + `INITIAL_EXTENSIONS`), como en producción: el
cog `Universo` usa la economía y el cog de logros reales. Discord es un servidor
aiohttp local que contesta el login, y el servidor web se prueba con el cliente de
pruebas de aiohttp sobre `Universo.build_app`, sin abrir puertos.
"""

from __future__ import annotations

import asyncio
import struct
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from interaction_fakes import fake_interaction

from bot.app import ENTRY_POINT, INITIAL_EXTENSIONS, BotClient, BotTree
from bot.services.economy import STARTING_BALANCE

APP_ID = 1111
GUILD_ID = 2222
CHANNEL_ID = 3333
USER_ID = 555
CODE = "abcdefghijklmnopqrstuvwxyz0123"


def fake_discord() -> web.Application:
    """La API de OAuth2 de Discord, la justa para el login de `USER_ID`."""

    async def token(request: web.Request) -> web.Response:
        form = await request.post()
        if form.get("code") != CODE:
            return web.json_response({"error": "invalid_grant"}, status=400)
        return web.json_response({"access_token": "token-de-acceso"})

    async def me(request: web.Request) -> web.Response:
        return web.json_response({"id": str(USER_ID), "username": "yeyo"})

    app = web.Application()
    app.router.add_post("/oauth2/token", token)
    app.router.add_get("/users/@me", me)
    return app


@dataclass
class World:
    client: BotClient
    cog: object
    web: TestClient


async def load(tmp_path: Path, *, secret: str = "secreto") -> BotClient:
    client = BotClient(
        command_prefix=".",
        database_path=tmp_path / "bot.sqlite3",
        client_secret=secret,
        # Puerto 0: si se abre, el sistema elige uno libre y no choca con nada.
        universo_port=0,
    )
    await client.economy.repository.initialize()
    await client.achievements.initialize()
    await client.message_stats.initialize()
    await client.universo_mundo.initialize()
    for extension in INITIAL_EXTENSIONS:
        await client.load_extension(extension)
    client._connection.application_id = APP_ID
    return client


def member(user_id: int = USER_ID, *, bot: bool = False, admin: bool = False) -> MagicMock:
    fake = MagicMock(spec=discord.Member)
    fake.id = user_id
    fake.bot = bot
    fake.display_name = "Yeyo"
    fake.guild_permissions = discord.Permissions(administrator=admin)
    return fake


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    discord_api = TestServer(fake_discord())
    await discord_api.start_server()
    client = await load(tmp_path)
    cog = client.get_cog("Universo")
    assert cog is not None
    cog.api = str(discord_api.make_url(""))
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>Jovaniverso</title>")
    (static / "assets" / "main.js").write_text("console.log('hola')")
    cog.static_dir = static
    members = {USER_ID: member()}
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


async def login(world: World) -> str:
    response = await world.web.post("/api/token", json={"code": CODE})
    assert response.status == 200
    body = await response.json()
    assert body["access_token"] == "token-de-acceso"
    return body["session"]


async def test_la_pagina_y_sus_archivos_se_sirven(world: World) -> None:
    index = await world.web.get("/")
    assert index.status == 200
    assert "Jovaniverso" in await index.text()
    assert index.headers["Cache-Control"] == "no-cache"
    script = await world.web.get("/assets/main.js")
    assert script.status == 200


async def test_sin_compilar_la_pagina_dice_como_compilarla(world: World, tmp_path: Path) -> None:
    world.cog.static_dir = tmp_path / "vacia"
    response = await world.web.get("/")
    assert response.status == 503
    assert "npm run build" in await response.text()


async def test_la_config_da_el_id_de_la_aplicacion(world: World) -> None:
    response = await world.web.get("/api/config")
    assert await response.json() == {"client_id": str(APP_ID)}


async def test_entrar_da_el_perfil_con_el_saldo_real_y_apunta_los_logros(world: World) -> None:
    session = await login(world)
    response = await world.web.post(
        "/api/entrar",
        json={"guild_id": str(GUILD_ID), "channel_id": str(CHANNEL_ID), "platform": "mobile"},
        headers={"Authorization": f"Bearer {session}"},
    )
    assert response.status == 200
    body = await response.json()
    assert body["name"] == "Yeyo"
    assert body["balance"] == STARTING_BALANCE
    assert body["balance"] == await world.client.economy.balance(GUILD_ID, USER_ID)
    # La fila de tragaperras de la plaza: una máquina parada por cada una.
    assert len(body["tragas"]["stills"]) == body["tragas"]["machines"]
    assert body["tragas"]["symbols"]
    # Los logros se apuntan después de contestar: se esperan aquí.
    await asyncio.gather(*world.cog._background)
    profile = await world.client.achievements.profile(GUILD_ID, USER_ID)
    assert profile.stats["universo_mobile"] == 1
    assert any(stat.startswith("universo_day_") for stat in profile.stats)
    assert {"universo_day_1", "universo_mobile"} <= set(profile.unlocked)


async def test_entrar_sin_sesion_o_con_una_falsa_no_deja(world: World) -> None:
    body = {"guild_id": str(GUILD_ID)}
    response = await world.web.post("/api/entrar", json=body)
    assert response.status == 401
    forged = await world.web.post(
        "/api/entrar", json=body, headers={"Authorization": f"Bearer {USER_ID}.9999999999.firma"}
    )
    assert forged.status == 401
    assert "error" in await forged.json()


async def test_quien_no_es_del_servidor_no_ve_nada(world: World) -> None:
    session = world.cog.signer.issue(999)
    response = await world.web.post(
        "/api/entrar",
        json={"guild_id": str(GUILD_ID)},
        headers={"Authorization": f"Bearer {session}"},
    )
    assert response.status == 403
    other_guild = await world.web.post(
        "/api/entrar",
        json={"guild_id": "4444"},
        headers={"Authorization": f"Bearer {await login(world)}"},
    )
    assert other_guild.status == 400


async def test_un_codigo_malo_o_un_cuerpo_raro_dan_error_claro(world: World) -> None:
    bad_code = await world.web.post("/api/token", json={"code": "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"})
    assert bad_code.status == 401
    not_json = await world.web.post("/api/token", data="hola")
    assert not_json.status == 400
    a_list = await world.web.post("/api/token", json=["code"])
    assert a_list.status == 400
    too_big = await world.web.post("/api/token", json={"code": "x" * 20_000})
    assert too_big.status == 413


async def test_sin_secreto_no_se_abre_y_el_boton_lo_dice(tmp_path: Path) -> None:
    client = await load(tmp_path, secret="")
    try:
        cog = client.get_cog("Universo")
        assert cog.runner is None
        view = next(v for v in client.persistent_views if v.__class__.__name__ == "OpenView")
        interaction = fake_interaction()
        await view.open.callback(interaction)
        interaction.response.send_message.assert_awaited_once()
        assert "aún no está abierto" in interaction.response.send_message.call_args.args[0]
    finally:
        await client.close()


async def test_con_secreto_abre_el_servidor_y_el_boton_lanza_la_activity(tmp_path: Path) -> None:
    client = await load(tmp_path)
    try:
        cog = client.get_cog("Universo")
        assert cog.runner is not None
        view = next(v for v in client.persistent_views if v.__class__.__name__ == "OpenView")
        interaction = fake_interaction()
        interaction.response.launch_activity = AsyncMock()
        await view.open.callback(interaction)
        interaction.response.launch_activity.assert_awaited_once()
    finally:
        await client.close()
    assert cog.runner is None


async def test_el_texto_universo_contesta_con_el_boton(tmp_path: Path) -> None:
    client = await load(tmp_path, secret="")
    try:
        command = client.get_command("universo")
        ctx = MagicMock()
        ctx.send = AsyncMock()
        await command.callback(client.get_cog("Universo"), ctx)
        view = ctx.send.call_args.kwargs["view"]
        assert [child.custom_id for child in view.children] == ["universo:abrir"]
    finally:
        await client.close()


# -- El comando de entrada al sincronizar -----------------------------------------------


def tree_with(
    current: list[dict[str, object]], *, fail_first: bool = False
) -> tuple[BotTree, MagicMock]:
    client = BotClient(command_prefix=".", database_path=Path("/nonexistent/bot.sqlite3"))
    client._connection.application_id = APP_ID
    http = MagicMock()
    http.get_global_commands = AsyncMock(return_value=current)
    calls: list[list[dict[str, object]]] = []

    async def upsert(
        application_id: int, payload: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        calls.append(payload)
        if fail_first and len(calls) == 1:
            raise discord.HTTPException(MagicMock(status=400), "no")
        return []

    http.bulk_upsert_global_commands = AsyncMock(side_effect=upsert)
    http.calls = calls
    tree = client.tree
    tree._http = http
    return tree, http


async def test_sin_comando_de_entrada_el_bot_crea_universo() -> None:
    tree, http = tree_with([{"id": "1", "type": 1, "name": "ayuda"}])
    await tree.sync()
    (payload,) = http.calls
    assert [c for c in payload if c.get("type") == 4] == [ENTRY_POINT]


async def test_sin_activities_se_sincroniza_como_siempre() -> None:
    tree, http = tree_with([{"id": "1", "type": 1, "name": "ayuda"}], fail_first=True)
    await tree.sync()
    assert len(http.calls) == 2
    assert all(c.get("type") != 4 for c in http.calls[1])


async def test_con_activities_el_comando_de_entrada_va_y_se_llama_universo() -> None:
    tree, http = tree_with([{"id": "77", "type": 4, "name": "launch", "handler": 2}])
    await tree.sync()
    (payload,) = http.calls
    entries = [c for c in payload if c.get("type") == 4]
    assert entries == [{**ENTRY_POINT, "id": "77"}]
    assert entries[0]["name"] == "universo"


async def test_si_discord_no_acepta_el_nombre_se_deja_su_comando_de_entrada() -> None:
    entry = {
        "id": "77",
        "type": 4,
        "name": "launch",
        "description": "",
        "handler": 2,
        "version": "9",
    }
    tree, http = tree_with([entry], fail_first=True)
    await tree.sync()
    assert len(http.calls) == 2
    kept = [c for c in http.calls[1] if c.get("type") == 4]
    assert kept == [{"id": "77", "type": 4, "name": "launch", "description": "", "handler": 2}]


async def test_pasear_apunta_lo_recorrido_recortado_a_lo_que_da_tiempo(world: World) -> None:
    session = await login(world)
    headers = {"Authorization": f"Bearer {session}"}
    body = {"guild_id": str(GUILD_ID), "channel_id": str(CHANNEL_ID)}
    await world.web.post("/api/entrar", json=body, headers=headers)
    await asyncio.gather(*world.cog._background)
    # Como si hubieran pasado 10 s desde que entró: caben 120 m y 20 saltos.
    key = (GUILD_ID, USER_ID)
    world.cog._last_walk[key] -= 10
    response = await world.web.post(
        "/api/paseo",
        json={**body, "meters": 90, "jumps": 500, "places": ["faro", "faro", "luna", 7]},
        headers=headers,
    )
    assert response.status == 200
    cheat = await world.web.post(
        "/api/paseo", json={**body, "meters": 10_000, "jumps": -3}, headers=headers
    )
    assert cheat.status == 200
    await world.client.get_cog("Achievements").flush()
    profile = await world.client.achievements.profile(GUILD_ID, USER_ID)
    assert 90 <= profile.stats["universo_meters"] < 92
    assert profile.stats["universo_jumps"] == 20
    assert profile.stats["universo_place_faro"] == 1
    assert "universo_place_luna" not in profile.stats
    assert {"universo_jumps_1", "universo_place_faro"} <= set(profile.unlocked)


async def test_pasear_sin_sesion_no_deja(world: World) -> None:
    response = await world.web.post("/api/paseo", json={"guild_id": str(GUILD_ID), "meters": 5})
    assert response.status == 401


# -- Multijugador (`/api/sala`) ------------------------------------------------------------


async def test_dos_jugadores_se_ven_y_coinciden_para_los_logros(world: World) -> None:
    other = member(777)
    other.display_name = "Bea"
    guild = world.client.get_guild(GUILD_ID)
    members = {USER_ID: guild.get_member(USER_ID), 777: other}
    guild.get_member = members.get
    hello = {"t": "hola", "guild_id": str(GUILD_ID)}
    ana = await world.web.ws_connect("/api/sala")
    await ana.send_json({**hello, "session": await login(world)})
    welcome = await ana.receive_json()
    assert welcome["t"] == "bienvenida" and welcome["jugadores"] == []
    bea = await world.web.ws_connect("/api/sala")
    await bea.send_json({**hello, "session": world.cog.signer.issue(777)})
    assert (await bea.receive_json())["jugadores"][0]["nombre"] == "Yeyo"
    assert (await ana.receive_json())["nombre"] == "Bea"
    await bea.send_bytes(struct.pack("<BhhhBB", 1, 15, 3, -40, 128, 2))
    tick = await ana.receive_bytes(timeout=2)
    assert tick[:2] == bytes((1, 1)) and struct.unpack_from("<hhh", tick, 3) == (15, 3, -40)
    await bea.close()
    assert (await ana.receive_json(timeout=2))["t"] == "sale"
    await ana.close()
    await world.client.get_cog("Achievements").flush()
    profile = await world.client.achievements.profile(GUILD_ID, USER_ID)
    assert profile.stats["universo_met_777"] == 1
    assert "universo_people_1" in profile.unlocked
    assert world.cog.salas.rooms == {}


async def test_solo_los_desarrolladores_tienen_devtools_y_entran_al_extraplano(
    world: World,
) -> None:
    from bot.cogs.universo import DEVS

    dev_id = min(DEVS)
    guild = world.client.get_guild(GUILD_ID)
    members = {USER_ID: guild.get_member(USER_ID), dev_id: member(dev_id)}
    guild.get_member = members.get
    body = {"guild_id": str(GUILD_ID), "channel_id": str(CHANNEL_ID)}

    async def entrar(session: str) -> dict:
        headers = {"Authorization": f"Bearer {session}"}
        return await (await world.web.post("/api/entrar", json=body, headers=headers)).json()

    assert (await entrar(await login(world)))["dev"] is False
    assert (await entrar(world.cog.signer.issue(dev_id)))["dev"] is True

    hello = {"t": "hola", "guild_id": str(GUILD_ID), "mundo": "pruebas"}
    nadie = await world.web.ws_connect("/api/sala")
    await nadie.send_json({**hello, "session": await login(world)})
    assert (await nadie.receive_json())["error"].startswith("El mundo de pruebas")
    isla = await world.web.ws_connect("/api/sala")
    await isla.send_json({**hello, "mundo": None, "session": await login(world)})
    assert (await isla.receive_json())["jugadores"] == []
    dev = await world.web.ws_connect("/api/sala")
    await dev.send_json({**hello, "session": world.cog.signer.issue(dev_id)})
    assert (await dev.receive_json())["jugadores"] == []  # no ve a quien está en la isla
    await dev.send_bytes(struct.pack("<BhhhBB", 1, 15, 1900, -40, 128, 0))
    assert list(world.cog.pruebas.rooms) == [GUILD_ID]
    assert [p.user_id for p in world.cog.salas.present(GUILD_ID)] == [USER_ID]
    await dev.close()
    await isla.close()
    await world.client.get_cog("Achievements").flush()
    profile = await world.client.achievements.profile(GUILD_ID, dev_id)
    assert not any(stat.startswith("universo_met") for stat in profile.stats)


async def test_los_desarrolladores_dan_y_quitan_el_editor_y_las_devtools(world: World) -> None:
    from bot.cogs.universo import DEVS

    dev_id = min(DEVS)
    guild = world.client.get_guild(GUILD_ID)
    plain = guild.get_member(USER_ID)
    plain.name = "yeyo"
    admin = member(888, admin=True)  # administrador de Discord: eso ya no da nada
    admin.display_name = admin.name = "Bea"
    members = {USER_ID: plain, dev_id: member(dev_id), 888: admin}
    guild.get_member = members.get
    guild.members = list(members.values())
    dev = {"Authorization": f"Bearer {world.cog.signer.issue(dev_id)}"}
    yeyo = {"Authorization": f"Bearer {await login(world)}"}
    query = {"guild_id": str(GUILD_ID)}

    async def entrar(headers: dict[str, str]) -> dict:
        return await (await world.web.post("/api/entrar", json=query, headers=headers)).json()

    async def dar(user_id: int, permiso: str, si: bool = True) -> tuple[int, dict]:
        body = {**query, "user_id": str(user_id), "permiso": permiso, "dar": si}
        response = await world.web.post("/api/permisos", json=body, headers=dev)
        return response.status, await response.json()

    # Solo los desarrolladores ven y cambian los permisos.
    for headers in (yeyo, {"Authorization": f"Bearer {world.cog.signer.issue(888)}"}):
        assert (await world.web.get("/api/permisos", params=query, headers=headers)).status == 403
        body = {**query, "user_id": str(USER_ID), "permiso": "editor", "dar": True}
        assert (await world.web.post("/api/permisos", json=body, headers=headers)).status == 403
    found = await world.web.get("/api/permisos", params={**query, "q": "YE"}, headers=dev)
    listing = await found.json()
    assert listing["usuarios"] == [] and set(listing["permisos"]) == {"editor", "devtools"}
    assert [c["id"] for c in listing["candidatos"]] == [str(USER_ID)]  # ni el dev ni bots
    assert (await entrar(dev))["permisos"] is True
    profile = await entrar(yeyo)
    assert (profile["editor"], profile["dev"], profile["permisos"]) == (False, False, False)
    borrador = {"params": query, "headers": yeyo}
    assert (await world.web.get("/api/editor/borrador", **borrador)).status == 403

    status, listing = await dar(USER_ID, "editor")
    assert status == 200
    assert listing["usuarios"] == [{"id": str(USER_ID), "nombre": "Yeyo", "permisos": ["editor"]}]
    assert (await dar(USER_ID, "devtools"))[0] == 200
    profile = await entrar(yeyo)
    assert (profile["editor"], profile["dev"]) == (True, True)
    assert (await world.web.get("/api/editor/borrador", **borrador)).status == 200
    sala = await world.web.ws_connect("/api/sala")
    await sala.send_json({**query, "t": "hola", "mundo": "pruebas", "session": await login(world)})
    assert (await sala.receive_json())["t"] == "bienvenida"
    await sala.close()

    assert (await dar(USER_ID, "editor", False))[0] == 200
    assert (await world.web.get("/api/editor/borrador", **borrador)).status == 403
    assert (await entrar(yeyo))["dev"] is True
    # Lo que no se puede: un permiso que no existe, quitárselo a un dev o dárselo a un bot.
    assert (await dar(USER_ID, "dios"))[0] == 400
    assert "todos los permisos" in (await dar(dev_id, "editor", False))[1]["error"]
    members[999] = member(999, bot=True)
    assert (await dar(999, "editor"))[0] == 400


async def test_la_sala_sin_sesion_contesta_error_y_cierra(world: World) -> None:
    ws = await world.web.ws_connect("/api/sala")
    await ws.send_json({"t": "hola", "guild_id": str(GUILD_ID), "session": "falsa"})
    assert (await ws.receive_json())["t"] == "error"
    assert (await ws.receive()).type.name in {"CLOSE", "CLOSED", "CLOSING"}
    garbage = await world.web.ws_connect("/api/sala")
    await garbage.send_bytes(b"\x01")
    assert (await garbage.receive()).type.name in {"CLOSE", "CLOSED", "CLOSING"}


async def test_solo_se_ven_los_gestos_comprados_y_cuentan_para_los_logros(world: World) -> None:
    tienda = world.client.get_cog("Tienda")
    assert await tienda.give_key(GUILD_ID, USER_ID, "gesto_baile") is not None
    session = await login(world)
    entry = await world.web.post(
        "/api/entrar",
        json={"guild_id": str(GUILD_ID)},
        headers={"Authorization": f"Bearer {session}"},
    )
    assert (await entry.json())["gestures"] == [5]
    hello = {"t": "hola", "guild_id": str(GUILD_ID), "session": session}
    ana = await world.web.ws_connect("/api/sala")
    await ana.send_json(hello)
    assert (await ana.receive_json())["t"] == "bienvenida"
    player = world.cog.salas.present(GUILD_ID)[0]
    await ana.send_bytes(struct.pack("<BhhhBB", 1, 0, 0, 0, 0, 4))  # saludo sin comprar
    await ana.send_bytes(struct.pack("<BhhhBB", 1, 0, 0, 1, 0, 5))  # baile comprado
    tick = await ana.receive_bytes(timeout=2)
    assert tick[-1] == 5
    assert player.gestures == frozenset({5})
    await ana.close()
    await world.client.get_cog("Achievements").flush()
    profile = await world.client.achievements.profile(GUILD_ID, USER_ID)
    assert profile.stats["universo_gesture_baile"] == 1
    assert "universo_gesture_saludo" not in profile.stats
    assert "universo_gestures_1" in profile.unlocked


# -- Tragaperras de la plaza ----------------------------------------------------------


async def spin(world: World, session: str, **body: object) -> tuple[int, dict]:
    payload = {"guild_id": str(GUILD_ID), "channel_id": str(CHANNEL_ID), "stake": 100, **body}
    response = await world.web.post(
        "/api/traga", json=payload, headers={"Authorization": f"Bearer {session}"}
    )
    return response.status, await response.json()


async def settle(world: World) -> None:
    """Espera a los logros que van después de contestar y los guarda."""
    slots = world.client.get_cog("Tragaperras")
    await asyncio.gather(*slots._background)
    await world.client.get_cog("Achievements").flush()


async def test_una_tirada_en_la_plaza_cobra_de_verdad_y_trae_la_animacion(
    world: World,
) -> None:
    from bot.services.slots import HEAT_MAX

    session = await login(world)
    status, body = await spin(world, session, machine=2)
    assert status == 200, body
    assert body["balance"] == await world.client.economy.balance(GUILD_ID, USER_ID)
    assert body["balance"] == STARTING_BALANCE - 100 + body["won"]
    # La página pinta la misma animación que Discord: el último fotograma es el quieto.
    assert body["states"] and "reels" in body["states"][-1]
    assert body["heat_max"] == HEAT_MAX
    await settle(world)
    profile = await world.client.achievements.profile(GUILD_ID, USER_ID)
    assert profile.stats["universo_slots_spins"] == 1
    assert profile.stats["universo_slots_machine_2"] == 1
    assert "universo_slots_1" in profile.unlocked
    # Una tirada de la plaza se ve entera: no cuenta como ⚡ Turbo.
    assert "slots_turbo" not in profile.stats


async def test_cada_maquina_de_la_plaza_tiene_su_calor_y_su_barra(world: World) -> None:
    from bot.services.slots import SlotMachine, line_payout, spin_at

    slots = world.client.get_cog("Tragaperras")
    # Siempre la misma tirada con premio de línea: sube el calor de su máquina.
    winning = next(
        s
        for s in (spin_at((a, b, c)) for a in range(30) for b in range(30) for c in range(30))
        if line_payout(s, 100) > 0 and not s.is_jackpot and not s.triggers_free_spins
    )
    machine = MagicMock(spec=SlotMachine)
    machine.spin.return_value = winning
    slots.machine = machine
    session = await login(world)
    status, body = await spin(world, session, machine=1)
    assert status == 200, body
    assert slots.heat(GUILD_ID, USER_ID, 1) == 1
    assert slots.heat(GUILD_ID, USER_ID, 3) == 0
    assert slots.heat(GUILD_ID, USER_ID) == 0  # la de Discord, intacta
    assert slots.bonus(GUILD_ID, USER_ID, 1).spins == 1
    assert slots.bonus(GUILD_ID, USER_ID, 3).spins == 0


async def test_la_plaza_pone_ritmo_y_tope_de_maquinas(world: World) -> None:
    from bot.services.universo_tragas import MAX_AT_ONCE

    session = await login(world)
    assert (await spin(world, session, machine=1))[0] == 200
    status, body = await spin(world, session, machine=1)
    assert status == 400 and "girando" in body["error"]
    for machine in range(2, MAX_AT_ONCE + 1):
        assert (await spin(world, session, machine=machine))[0] == 200
    status, body = await spin(world, session, machine=MAX_AT_ONCE + 1)
    assert status == 400 and str(MAX_AT_ONCE) in body["error"]
    assert (await spin(world, session, machine=99))[0] == 400
    assert (await spin(world, session, machine="1"))[0] == 400
    await settle(world)
    profile = await world.client.achievements.profile(GUILD_ID, USER_ID)
    assert profile.stats["universo_slots_at_once"] == MAX_AT_ONCE


async def test_sin_saldo_la_plaza_avisa_y_no_cobra(world: World) -> None:
    session = await login(world)
    status, body = await spin(world, session, machine=4, stake=STARTING_BALANCE * 10)
    assert status == 400
    assert body["error"]
    assert await world.client.economy.balance(GUILD_ID, USER_ID) == STARTING_BALANCE


async def test_parar_el_auto_tras_la_primera_cuenta_para_los_logros(world: World) -> None:
    slots = world.client.get_cog("Tragaperras")
    session = await login(world)
    status, body = await spin(world, session, machine=5, auto=True)
    assert status == 200, body
    assert body["auto"] is True or body["auto_stop"]
    assert (await spin(world, session, machine=5, parar=True))[0] == 200
    assert slots.seats.seat(GUILD_ID, USER_ID, 5).auto is None
    await settle(world)
    profile = await world.client.achievements.profile(GUILD_ID, USER_ID)
    assert profile.stats["universo_slots_auto"] == 1
    if not body["auto_stop"]:
        assert profile.stats["universo_slots_regret"] == 1
