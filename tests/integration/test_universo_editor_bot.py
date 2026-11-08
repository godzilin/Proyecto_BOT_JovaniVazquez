"""Las rutas del editor del Jovaniverso con el bot real (`BotClient` + extensiones).

Mismo montaje que `test_universo_bot.py`, pero con alguien con permiso de editor y un
administrador de Discord sin él (ser administrador ya no basta).
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

from bot.cogs.universo import MAX_EDITOR_BYTES, MUNDO_CACHE
from bot.services.universo_editor import FORMAT

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
    members = {ADMIN: member(ADMIN), PLAIN: member(PLAIN, admin=True)}
    await client.universo_mundo.set_permission(GUILD_ID, ADMIN, "editor", True, 0)
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


def mundo(*assets: str, x: float = 1) -> dict[str, object]:
    """Un asset por nombre y un objeto de cada uno en la celda de `x` (z = 2)."""
    objetos = [{"asset": a, "pos": [x, 0, 2], "rot": [0, 0, 0], "esc": [1, 1, 1]} for a in assets]
    return {**{f"a/{a}": piezas(a) for a in assets}, f"c/{int(x // 80)}/0": {"objetos": objetos}}


async def save(
    world: World, pieces: dict[str, object], revs: dict[str, int] | None = None
) -> tuple[int, dict[str, object]]:
    trozos = {k: {"rev": (revs or {}).get(k, 0), "doc": doc} for k, doc in pieces.items()}
    response = await world.web.put(
        "/api/editor/borrador",
        json={"guild_id": str(GUILD_ID), "v": FORMAT, "trozos": trozos},
        headers=auth(world),
    )
    return response.status, await response.json()


async def get(world: World, path: str, user_id: int = ADMIN) -> dict[str, object]:
    response = await world.web.get(
        path, params={"guild_id": str(GUILD_ID)}, headers=auth(world, user_id)
    )
    assert response.status == 200
    return await response.json()


async def publish(world: World) -> int:
    response = await world.web.post(
        "/api/editor/publicar", json={"guild_id": str(GUILD_ID)}, headers=auth(world)
    )
    return (await response.json())["version"]


async def test_quien_no_es_admin_recibe_403_en_cada_ruta(world: World) -> None:
    headers = auth(world, PLAIN)
    body = {"guild_id": str(GUILD_ID), "v": FORMAT, "version": 1, "trozos": {}}
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
        assert "permiso de editor" in (await response.json())["error"]
    # Y sin sesión, 401.
    assert (await world.web.get("/api/editor/borrador", params=query)).status == 401


async def test_guardar_publicar_y_leer_lo_publicado(world: World) -> None:
    assert await get(world, "/api/editor/borrador") == {"version": 0, "trozos": {}}
    # Antes de publicar, el mundo público está vacío.
    assert await get(world, "/api/mundo", PLAIN) == {"version": 0, "trozos": {}}

    status, saved = await save(world, mundo("arbol"))
    assert (status, saved) == (200, {"revs": {"a/arbol": 1, "c/0/0": 1}})
    assert await publish(world) == 1

    body = await get(world, "/api/mundo", PLAIN)
    assert body["version"] == 1
    assert sorted(body["trozos"]) == ["a/arbol", "c/0/0"]  # type: ignore[arg-type]
    assert body["trozos"]["c/0/0"]["objetos"][0]["pos"] == [1.0, 0.0, 2.0]  # type: ignore[index]
    draft = await get(world, "/api/editor/borrador")
    assert draft["version"] == 1
    assert draft["trozos"]["a/arbol"] == {"doc": piezas("arbol"), "rev": 1}  # type: ignore[index]
    [row] = (await get(world, "/api/editor/versiones"))["versiones"]  # type: ignore[misc]
    assert (row["version"], row["autor"], row["kb"]) == (1, "Admin", 1)

    # Entrar dice si puedes editar y qué versión hay.
    headers = auth(world)
    entered = await world.web.post("/api/entrar", json={"guild_id": str(GUILD_ID)}, headers=headers)
    assert (await entered.json())["editor"] is True
    assert (await entered.json())["mundo"] == 1


async def test_dos_editores_en_celdas_distintas_no_chocan(world: World) -> None:
    assert (await save(world, mundo("a")))[0] == 200
    # El segundo pone su objeto en otra celda: guarda sin recargar.
    lejos = {"c/3/0": mundo("a", x=250)["c/3/0"]}
    assert await save(world, lejos) == (200, {"revs": {"c/3/0": 1}})
    # Pero quien no sabía que la celda 0, 0 ya existe choca, y no guarda nada.
    status, error = await save(world, {**mundo("b"), "c/5/0": {"objetos": []}})
    assert status == 409
    assert "Otra persona" in error["error"]
    draft = await get(world, "/api/editor/borrador")
    assert sorted(draft["trozos"]) == ["a/a", "c/0/0", "c/3/0"]  # type: ignore[arg-type]


async def test_borrar_un_trozo_y_los_assets_que_faltan(world: World) -> None:
    await save(world, mundo("a"))
    # Borrar el asset con su objeto todavía puesto no vale.
    status, error = await save(world, {"a/a": None}, {"a/a": 1})
    assert status == 400
    assert "no existen: a" in error["error"]
    # Borrando los dos a la vez, sí. Una celda vacía cuenta como borrada.
    assert (await save(world, {"a/a": None, "c/0/0": {"objetos": []}}, {"a/a": 1, "c/0/0": 1}))[
        0
    ] == 200
    draft = await get(world, "/api/editor/borrador")
    assert draft["trozos"] == {"a/a": {"doc": None, "rev": 2}, "c/0/0": {"doc": None, "rev": 2}}
    # Y un objeto suelto que nombra un asset que no hay, tampoco.
    status, error = await save(world, {"c/1/0": mundo("z", x=90)["c/1/0"]})
    assert status == 400


async def test_publicar_sin_borrador_da_400_y_volver_a_una_que_no_esta_404(world: World) -> None:
    headers = auth(world)
    nothing = await world.web.post(
        "/api/editor/publicar", json={"guild_id": str(GUILD_ID)}, headers=headers
    )
    assert nothing.status == 400
    assert (await nothing.json())["error"] == "No hay borrador que publicar."
    back = await world.web.post(
        "/api/editor/volver", json={"guild_id": str(GUILD_ID), "version": 3}, headers=headers
    )
    assert back.status == 404


async def test_volver_pone_una_version_como_borrador(world: World) -> None:
    await save(world, mundo("a"))
    await publish(world)
    await save(world, {**mundo("b"), "c/0/0": mundo("b")["c/0/0"]}, {"c/0/0": 1})
    back = await world.web.post(
        "/api/editor/volver", json={"guild_id": str(GUILD_ID), "version": 1}, headers=auth(world)
    )
    assert await back.json() == {}
    draft = await get(world, "/api/editor/borrador")
    trozos = draft["trozos"]
    assert trozos["c/0/0"]["rev"] == 3  # type: ignore[index]
    assert trozos["c/0/0"]["doc"]["objetos"][0]["asset"] == "a"  # type: ignore[index]
    assert trozos["a/b"] == {"doc": None, "rev": 2}  # type: ignore[index]


async def test_publicar_avisa_a_la_sala(world: World) -> None:
    ear = Ear()
    await world.cog.salas.join(GUILD_ID, PLAIN, "Ana", ear)
    await save(world, mundo("a"))
    await publish(world)
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

    # Con el GLB subido, un asset que lo nombra vale; con otro que no, no.
    modelo = {"nombre": "Modelo", "tipo": "glb", "glb": body["sha256"], "colision": None}
    assert (await save(world, {"a/modelo": modelo}))[0] == 200
    status, error = await save(world, {"a/modelo": {**modelo, "glb": "f" * 64}}, {"a/modelo": 1})
    assert status == 400
    assert "no se ha subido" in error["error"]


@pytest.mark.parametrize(
    ("body", "texto"),
    [
        ({"v": 1, "trozos": {"a/a": {"rev": 0, "doc": None}}}, "desfasado"),
        ({"v": FORMAT, "trozos": {}}, "de 1 a"),
        ({"v": FORMAT, "trozos": {"mundo": {"rev": 0, "doc": None}}}, "clave"),
        ({"v": FORMAT, "trozos": {"a/a": {"rev": 0}}}, "{rev, doc}"),
        ({"v": FORMAT, "trozos": {"a/a": {"rev": -1, "doc": None}}}, "rev"),
        ({"v": FORMAT, "trozos": {"c/0/0": {"rev": 0, "doc": {"objetos": [], "x": 1}}}}, "campos"),
        ({"v": FORMAT, "trozos": {"c/0/0": {"rev": 0, "doc": mundo("a", x=90)["c/1/0"]}}}, "celda"),
    ],
)
async def test_un_guardado_roto_da_400(world: World, body: dict[str, object], texto: str) -> None:
    response = await world.web.put(
        "/api/editor/borrador", json={"guild_id": str(GUILD_ID), **body}, headers=auth(world)
    )
    assert response.status == 400
    assert texto in (await response.json())["error"]
    broken = await world.web.put("/api/editor/borrador", data="{no", headers=auth(world))
    assert broken.status == 400


async def test_solo_el_editor_acepta_cuerpos_grandes(world: World) -> None:
    big = {"guild_id": str(GUILD_ID), "channel_id": "x" * 20_000}
    assert (await world.web.post("/api/entrar", json=big, headers=auth(world))).status == 413
    # Un guardado de 200 celdas con 100 objetos cada una (≈ 2,5 MB) cabe.
    lleno = {}
    for cx in range(-10, 10):
        for cz in range(-5, 5):
            objeto = {
                "asset": "a",
                "pos": [80 * cx + 1, 0, 80 * cz + 1],
                "rot": [0, 0, 0],
                "esc": [1, 1, 1],
            }
            lleno[f"c/{cx}/{cz}"] = {"objetos": [objeto] * 100}
    status, saved = await save(world, {"a/a": piezas("a"), **lleno})
    assert status == 200
    assert len(saved["revs"]) == 201  # type: ignore[arg-type]
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
    await publish(world)
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


async def test_tras_publicar_se_baja_solo_lo_que_cambio(world: World) -> None:
    status, saved = await save(world, mundo("arbol"))
    assert status == 200
    await publish(world)
    lejos = {"asset": "arbol", "pos": [161, 0, 2], "rot": [0, 0, 0], "esc": [1, 1, 1]}
    status, _saved = await save(world, {"c/2/0": {"objetos": [lejos]}})
    assert status == 200
    await publish(world)

    async def pedir(**params: str) -> tuple[str, dict]:
        response = await world.web.get(
            "/api/mundo",
            params={"guild_id": str(GUILD_ID), **params},
            headers={**auth(world, PLAIN), "Accept-Encoding": "gzip"},
        )
        return response.headers["Cache-Control"], await response.json()

    for _ in range(2):  # la segunda sale de la caché, ya comprimida
        cache, body = await pedir(v="2", desde="1")
        assert cache == MUNDO_CACHE
        assert body == {"version": 2, "desde": 1, "trozos": {"c/2/0": {"objetos": [lejos]}}}
    assert world.cog._mundo_desde[(GUILD_ID, 1)][2] is not None
    # Al día: nada que bajar.
    assert (await pedir(v="2", desde="2"))[1] == {"version": 2, "desde": 2, "trozos": {}}
    # Desde una versión que no hay, o sin número: el mundo entero, sin `desde`.
    for desde in ("7", "x", "0"):
        _cache, body = await pedir(v="2", desde=desde)
        assert "desde" not in body
        assert set(body["trozos"]) == {"a/arbol", "c/0/0", "c/2/0"}


async def test_el_mundo_con_su_version_se_guarda_para_siempre(world: World) -> None:
    status, _saved = await save(world, mundo("arbol"))
    assert status == 200
    await publish(world)

    async def cache(v: str | None) -> tuple[str, int]:
        params = {"guild_id": str(GUILD_ID)} | ({"v": v} if v is not None else {})
        response = await world.web.get("/api/mundo", params=params, headers=auth(world, PLAIN))
        return response.headers["Cache-Control"], (await response.json())["version"]

    assert await cache("1") == (MUNDO_CACHE, 1)
    # Sin versión o con una que ya no es la publicada: la de ahora, sin caché.
    assert await cache(None) == ("no-cache", 1)
    assert await cache("0") == ("no-cache", 1)
    assert await cache("2") == ("no-cache", 1)
