"""Las rutas del editor del Jovaniverso con el bot real (`BotClient` + extensiones).

Mismo montaje que `test_universo_bot.py`, pero con un administrador y alguien que no lo es.
"""

from __future__ import annotations

import json
import struct
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer
from test_universo_bot import GUILD_ID, USER_ID, World, fake_discord, load, member

from bot.cogs.universo import MAX_EDITOR_BYTES
from bot.services.universo_editor import EMPTY

ADMIN = USER_ID
PLAIN = 777


class Ear:
    """Una conexión de sala que apunta lo que recibe."""

    def __init__(self) -> None:
        self.texts: list[dict[str, object]] = []

    async def send_str(self, data: str) -> None:
        self.texts.append(json.loads(data))

    async def send_bytes(self, data: bytes) -> None:
        pass

    async def close(self) -> None:
        pass


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    discord_api = TestServer(fake_discord())
    await discord_api.start_server()
    client = await load(tmp_path)
    cog = client.get_cog("Universo")
    assert cog is not None
    members = {ADMIN: member(ADMIN, admin=True), PLAIN: member(PLAIN)}
    members[ADMIN].display_name = "Admin"
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


def auth(world: World, user_id: int = ADMIN) -> dict[str, str]:
    return {"Authorization": f"Bearer {world.cog.signer.issue(user_id)}"}


def glb(triangles: int = 1) -> bytes:
    """Un GLB mínimo válido (solo el trozo JSON) con `triangles` triángulos."""
    gltf = {
        "asset": {"version": "2.0"},
        "accessors": [{"count": 3 * triangles}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
    }
    body = json.dumps(gltf).encode()
    body += b" " * (-len(body) % 4)
    return (
        b"glTF"
        + struct.pack("<II", 2, 20 + len(body))
        + struct.pack("<II", len(body), 0x4E4F534A)
        + body
    )


def piezas(name: str = "Caja") -> dict[str, object]:
    pieza = {"forma": "caja", "pos": [0, 0, 0], "rot": [0, 0, 0], "esc": [1, 1, 1], "color": 255}
    return {"nombre": name, "tipo": "piezas", "piezas": [pieza], "colision": None}


def mundo(*assets: str) -> dict[str, object]:
    return {
        "v": 1,
        "assets": {a: piezas(a) for a in assets},
        "objetos": [
            {"asset": a, "pos": [1, 0, 2], "rot": [0, 0, 0], "esc": [1, 1, 1]} for a in assets
        ],
        "zonas": {},
    }


async def save(world: World, doc: dict[str, object], rev: int = 0) -> tuple[int, dict[str, object]]:
    response = await world.web.put(
        "/api/editor/borrador",
        json={"guild_id": str(GUILD_ID), "rev": rev, "doc": doc},
        headers=auth(world),
    )
    return response.status, await response.json()


async def test_quien_no_es_admin_recibe_403_en_cada_ruta(world: World) -> None:
    headers = auth(world, PLAIN)
    body = {"guild_id": str(GUILD_ID), "rev": 0, "version": 1, "doc": EMPTY}
    query = {"guild_id": str(GUILD_ID)}
    calls = [
        world.web.get("/api/editor/borrador", params=query, headers=headers),
        world.web.put("/api/editor/borrador", json=body, headers=headers),
        world.web.post("/api/editor/publicar", json=body, headers=headers),
        world.web.get("/api/editor/versiones", params=query, headers=headers),
        world.web.post("/api/editor/volver", json=body, headers=headers),
        world.web.post("/api/editor/glb", params=query, data=glb(), headers=headers),
    ]
    for call in calls:
        response = await call
        assert response.status == 403
        assert "administradores" in (await response.json())["error"]
    # Y sin sesión, 401.
    assert (await world.web.get("/api/editor/borrador", params=query)).status == 401


async def test_guardar_publicar_y_leer_lo_publicado(world: World) -> None:
    headers = auth(world)
    first = await world.web.get(
        "/api/editor/borrador", params={"guild_id": str(GUILD_ID)}, headers=headers
    )
    assert await first.json() == {"doc": EMPTY, "rev": 0, "version": 0}
    # Antes de publicar, el mundo público está vacío.
    public = await world.web.get(
        "/api/mundo", params={"guild_id": str(GUILD_ID)}, headers=auth(world, PLAIN)
    )
    assert await public.json() == {"version": 0, "doc": EMPTY}

    status, saved = await save(world, mundo("arbol"))
    assert (status, saved) == (200, {"rev": 1})
    published = await world.web.post(
        "/api/editor/publicar", json={"guild_id": str(GUILD_ID), "rev": 1}, headers=headers
    )
    assert await published.json() == {"version": 1}

    public = await world.web.get(
        "/api/mundo", params={"guild_id": str(GUILD_ID)}, headers=auth(world, PLAIN)
    )
    body = await public.json()
    assert body["version"] == 1
    assert list(body["doc"]["assets"]) == ["arbol"]
    draft = await world.web.get(
        "/api/editor/borrador", params={"guild_id": str(GUILD_ID)}, headers=headers
    )
    assert (await draft.json())["version"] == 1
    versions = await world.web.get(
        "/api/editor/versiones", params={"guild_id": str(GUILD_ID)}, headers=headers
    )
    [row] = (await versions.json())["versiones"]
    assert (row["version"], row["autor"], row["kb"]) == (1, "Admin", 1)

    # Entrar dice si eres admin y qué versión hay.
    entered = await world.web.post("/api/entrar", json={"guild_id": str(GUILD_ID)}, headers=headers)
    assert (await entered.json())["admin"] is True
    assert (await entered.json())["mundo"] == 1


async def test_un_rev_viejo_da_409_y_publicar_sin_borrador_400(world: World) -> None:
    headers = auth(world)
    nothing = await world.web.post(
        "/api/editor/publicar", json={"guild_id": str(GUILD_ID), "rev": 0}, headers=headers
    )
    assert nothing.status == 400
    assert (await nothing.json())["error"] == "No hay borrador que publicar."
    assert (await save(world, mundo("a")))[0] == 200
    status, error = await save(world, mundo("b"), rev=0)
    assert status == 409
    assert "Otro administrador" in error["error"]
    stale = await world.web.post(
        "/api/editor/publicar", json={"guild_id": str(GUILD_ID), "rev": 5}, headers=headers
    )
    assert stale.status == 409
    back = await world.web.post(
        "/api/editor/volver",
        json={"guild_id": str(GUILD_ID), "rev": 1, "version": 3},
        headers=headers,
    )
    assert back.status == 404


async def test_volver_pone_una_version_como_borrador(world: World) -> None:
    headers = auth(world)
    await save(world, mundo("a"))
    await world.web.post(
        "/api/editor/publicar", json={"guild_id": str(GUILD_ID), "rev": 1}, headers=headers
    )
    await save(world, mundo("b"), rev=1)
    back = await world.web.post(
        "/api/editor/volver",
        json={"guild_id": str(GUILD_ID), "rev": 2, "version": 1},
        headers=headers,
    )
    assert await back.json() == {"rev": 3}
    draft = await world.web.get(
        "/api/editor/borrador", params={"guild_id": str(GUILD_ID)}, headers=headers
    )
    assert list((await draft.json())["doc"]["assets"]) == ["a"]
    stale = await world.web.post(
        "/api/editor/volver",
        json={"guild_id": str(GUILD_ID), "rev": 2, "version": 1},
        headers=headers,
    )
    assert stale.status == 409


async def test_publicar_avisa_a_la_sala(world: World) -> None:
    ear = Ear()
    await world.cog.salas.join(GUILD_ID, PLAIN, "Ana", ear)
    await save(world, mundo("a"))
    await world.web.post(
        "/api/editor/publicar", json={"guild_id": str(GUILD_ID), "rev": 1}, headers=auth(world)
    )
    assert {"t": "mundo", "version": 1} in ear.texts


async def test_un_glb_se_sube_y_se_baja_por_su_contenido(world: World) -> None:
    data = glb(4)
    up = await world.web.post(
        "/api/editor/glb", params={"guild_id": str(GUILD_ID)}, data=data, headers=auth(world)
    )
    body = await up.json()
    assert up.status == 200
    assert body["triangles"] == 4
    down = await world.web.get(f"/api/glb/{body['sha256']}")
    assert down.status == 200
    assert await down.read() == data
    assert down.headers["Content-Type"] == "model/gltf-binary"
    assert down.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert (await world.web.get("/api/glb/" + "0" * 64)).status == 404
    assert (await world.web.get("/api/glb/NO-ES-UN-SHA")).status == 404
    bad = await world.web.post(
        "/api/editor/glb",
        params={"guild_id": str(GUILD_ID)},
        data=b"no soy un glb",
        headers=auth(world),
    )
    assert bad.status == 400

    # Con el GLB subido, un mundo que lo nombra vale; con otro que no, no.
    doc = mundo()
    doc["assets"] = {
        "modelo": {"nombre": "Modelo", "tipo": "glb", "glb": body["sha256"], "colision": None}
    }
    assert (await save(world, doc))[0] == 200
    doc["assets"]["modelo"]["glb"] = "f" * 64  # type: ignore[index]
    status, error = await save(world, doc, rev=1)
    assert status == 400
    assert "no se ha subido" in error["error"]


async def test_un_mundo_roto_da_400(world: World) -> None:
    status, error = await save(world, {"v": 1, "assets": [], "objetos": [], "zonas": {}})
    assert status == 400
    assert error["error"]
    broken = await world.web.put("/api/editor/borrador", data="{no", headers=auth(world))
    assert broken.status == 400


async def test_solo_el_editor_acepta_cuerpos_grandes(world: World) -> None:
    big = {"guild_id": str(GUILD_ID), "channel_id": "x" * 20_000}
    assert (await world.web.post("/api/entrar", json=big, headers=auth(world))).status == 413
    status, _ = await save(world, mundo("a"))
    assert status == 200
    padded = mundo("a")
    padded["nota"] = "x" * 20_000  # un campo que no se conoce: lo rechaza la validación, no el tope
    status, _ = await save(world, padded, rev=1)
    assert status == 400
    too_big = await world.web.post(
        "/api/editor/glb",
        params={"guild_id": str(GUILD_ID)},
        data=b"0" * (MAX_EDITOR_BYTES + 1),
        headers=auth(world),
    )
    assert too_big.status == 413


async def test_el_mundo_publicado_va_comprimido_y_se_comprime_una_vez(world: World) -> None:
    status, _saved = await save(world, mundo("arbol"))
    assert status == 200
    await world.web.post(
        "/api/editor/publicar", json={"guild_id": str(GUILD_ID), "rev": 1}, headers=auth(world)
    )
    for _ in range(2):
        response = await world.web.get(
            "/api/mundo",
            params={"guild_id": str(GUILD_ID)},
            headers={**auth(world, PLAIN), "Accept-Encoding": "gzip"},
        )
        assert response.headers["Content-Encoding"] == "gzip"
        assert (await response.json())["version"] == 1
    assert world.cog._mundo_gz[GUILD_ID][0] == 1
    plain = await world.web.get(
        "/api/mundo",
        params={"guild_id": str(GUILD_ID)},
        headers={**auth(world, PLAIN), "Accept-Encoding": "identity"},
    )
    assert "Content-Encoding" not in plain.headers
    assert (await plain.json())["version"] == 1
