"""Las rutas del editor del Jovaniverso con el bot real (`BotClient` + extensiones).

Mismo montaje que `test_universo_bot.py`, pero con alguien con permiso de editor y un
administrador de Discord sin él (ser administrador ya no basta).
"""

from __future__ import annotations

import asyncio
import base64
import json
import struct
from array import array
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer
from test_universo_bot import GUILD_ID, USER_ID, World, fake_discord, load, member

from bot.cogs.universo.editor import MAX_EDITOR_BYTES
from bot.cogs.universo.mundo import MAX_LOTE, MUNDO_CACHE, ByteCache
from bot.repositories import universo_mundo as mundo_repo
from bot.services.universo_editor import CELL_TERRAIN_SIDE, FORMAT

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


async def get(world: World, path: str, user_id: int = ADMIN, **params: str) -> dict[str, object]:
    response = await world.web.get(
        path, params={"guild_id": str(GUILD_ID), **params}, headers=auth(world, user_id)
    )
    assert response.status == 200
    return await response.json()


async def whole_draft(world: World) -> dict[str, object]:
    """El borrador entero, como lo junta el editor: el índice y, por lotes, todas sus celdas."""
    index = await get(world, "/api/editor/indice")
    cells = [k for k in index["trozos"] if k.startswith("c/")]  # type: ignore[union-attr]
    docs = dict(index["docs"])  # type: ignore[call-overload]
    for start in range(0, len(cells), MAX_LOTE):
        body = await get(world, "/api/editor/trozos", t=",".join(cells[start : start + MAX_LOTE]))
        docs.update({k: v["doc"] for k, v in body["trozos"].items()})  # type: ignore[union-attr]
    trozos = {k: {"doc": docs[k], "rev": r} for k, r in index["trozos"].items()}  # type: ignore[union-attr]
    trozos.update({k: {"doc": None, "rev": r} for k, r in index["borrados"].items()})  # type: ignore[union-attr]
    return {"version": index["version"], "trozos": trozos}


async def ask(
    world: World, path: str, *, gzip: bool = False, **params: str
) -> aiohttp.ClientResponse:
    """Una petición de un miembro cualquiera, con o sin gzip."""
    headers = {**auth(world, PLAIN), "Accept-Encoding": "gzip" if gzip else "identity"}
    return await world.web.get(path, params={"guild_id": str(GUILD_ID), **params}, headers=headers)


def cell(x: float, objects: int = 1) -> dict[str, object]:
    """Una celda de la fila z = 0 con `objects` objetos del asset `a`, en la celda de `x`."""
    objeto = {"asset": "a", "pos": [x, 0, 2], "rot": [0, 0, 0], "esc": [1, 1, 1]}
    return {"objetos": [objeto] * objects}


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
        world.web.get("/api/editor/indice", params=query, headers=headers),
        world.web.get("/api/editor/trozos", params={**query, "t": "c/0/0"}, headers=headers),
        world.web.get("/api/editor/usos", params={**query, "asset": "a"}, headers=headers),
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
    assert (await world.web.get("/api/editor/indice", params=query)).status == 401


async def test_guardar_publicar_y_leer_lo_publicado(world: World) -> None:
    assert await whole_draft(world) == {"version": 0, "trozos": {}}
    # Antes de publicar, el mundo público está vacío.
    assert await get(world, "/api/mundo/indice", PLAIN) == {
        "version": 0,
        "zonas": None,
        "teleportes": None,
        "trozos": {},
    }

    status, saved = await save(world, mundo("arbol"))
    assert (status, saved) == (200, {"revs": {"a/arbol": 1, "c/0/0": 1}})
    assert await publish(world) == 1

    assert await get(world, "/api/mundo/indice", PLAIN) == {
        "version": 1,
        "zonas": None,
        "teleportes": None,
        "trozos": {"a/arbol": 1, "c/0/0": 1},
    }
    body = await get(world, "/api/mundo/trozos", PLAIN, t="c/0/0@1,a/arbol@1")
    assert list(body["trozos"]) == ["c/0/0", "a/arbol"]  # type: ignore[call-overload]
    assert body["trozos"]["c/0/0"]["objetos"][0]["pos"] == [1.0, 0.0, 2.0]  # type: ignore[index]
    draft = await whole_draft(world)
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
    draft = await whole_draft(world)
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
    draft = await whole_draft(world)
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
    draft = await whole_draft(world)
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


async def test_un_glb_que_no_cabe_da_un_error_claro(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mundo_repo, "MAX_GLB_TOTAL", len(glb(1)))
    query = {"guild_id": str(GUILD_ID)}
    first = await world.web.post("/api/editor/glb", params=query, data=glb(1), headers=auth(world))
    assert first.status == 200
    full = await world.web.post("/api/editor/glb", params=query, data=glb(2), headers=auth(world))
    assert full.status == 507
    assert "No caben más modelos" in (await full.json())["error"]


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


async def test_el_indice_lleva_zonas_y_teleportes_y_cada_version_su_fila(world: World) -> None:
    zonas = {"zonas": {"plaza": {"x": 1, "z": 2, "r": 3}}}
    teleportes = {"teleportes": [{"nombre": "Faro", "x": 4, "z": 5}]}
    status, _saved = await save(world, {**mundo("arbol"), "zonas": zonas, "teleportes": teleportes})
    assert status == 200
    assert await publish(world) == 1
    lejos = {"asset": "arbol", "pos": [161, 0, 2], "rot": [0, 0, 0], "esc": [1, 1, 1]}
    assert (await save(world, {"c/2/0": {"objetos": [lejos]}}))[0] == 200
    assert await publish(world) == 2

    now = await get(world, "/api/mundo/indice", PLAIN)
    assert now == {
        "version": 2,
        "zonas": {"zonas": {"plaza": {"x": 1.0, "z": 2.0, "r": 3.0}}},
        "teleportes": {"teleportes": [{"nombre": "Faro", "x": 4.0, "z": 5.0}]},
        "trozos": {"a/arbol": 1, "c/0/0": 1, "c/2/0": 2},
    }
    # La versión 1 se conserva: lo de entonces, sin la celda nueva.
    old = await get(world, "/api/mundo/indice", PLAIN, v="1")
    assert old["version"] == 1 and old["trozos"] == {"a/arbol": 1, "c/0/0": 1}
    # Quitar zonas y celdas los saca del índice.
    revs = {"zonas": 1, "c/2/0": 1}
    assert (await save(world, {"zonas": None, "c/2/0": None}, revs))[0] == 200
    assert await publish(world) == 3
    last = await get(world, "/api/mundo/indice", PLAIN)
    assert last["zonas"] is None and last["teleportes"] is not None
    assert last["trozos"] == {"a/arbol": 1, "c/0/0": 1}


async def test_un_lote_da_los_trozos_tal_cual_o_solo_el_resumen(world: World) -> None:
    heights = array("h", [100] * CELL_TERRAIN_SIDE**2)
    heights[5 * CELL_TERRAIN_SIDE + 7] = 4_000
    terreno = {
        "alturas": base64.b64encode(heights.tobytes()).decode(),
        "colores": base64.b64encode(bytes(CELL_TERRAIN_SIDE**2)).decode(),
    }
    pieces = {**mundo("arbol"), "c/1/0": {"objetos": [], "terreno": terreno}}
    assert (await save(world, pieces))[0] == 200
    await publish(world)
    stored = await world.client.universo_mundo.draft(GUILD_ID)
    t = "c/1/0@1,a/arbol@1,c/0/0@1"
    response = await ask(world, "/api/mundo/trozos", t=t)
    assert response.status == 200
    assert response.headers["Cache-Control"] == MUNDO_CACHE
    # Cada trozo sale con el texto que se guardó, sin pasar por `json`.
    assert await response.text() == (
        '{"trozos":{"c/1/0":'
        + stored["c/1/0"][0]
        + ',"a/arbol":'
        + stored["a/arbol"][0]
        + ',"c/0/0":'
        + stored["c/0/0"][0]
        + "}}"
    )
    # Con `resumen=1`, solo las alturas muestreadas de las celdas con terreno (sin leer docs).
    summary = await ask(world, "/api/mundo/trozos", t="c/1/0@1,c/0/0@1,a/arbol@1", resumen="1")
    body = await summary.json()
    assert summary.headers["Cache-Control"] == MUNDO_CACHE
    assert list(body) == ["resumenes"] and list(body["resumenes"]) == ["c/1/0", "c/0/0", "a/arbol"]
    samples = array("h")
    samples.frombytes(base64.b64decode(body["resumenes"]["c/1/0"]))
    assert len(samples) == 25 and set(samples) == {100}
    assert body["resumenes"]["c/0/0"] == body["resumenes"]["a/arbol"] == ""


@pytest.mark.parametrize(
    "t",
    [
        "",
        "c/0/0",  # sin versión
        "c/0/0@",
        "c/0/0@0",
        "c/0/0@-1",
        "c/0/0@01",
        "c/0/0@uno",
        "c/0/0@1,c/0/0@1",  # repetida
        "c/x/0@1",
        "c/999/0@1",  # fuera del mundo
        "mundo@1",
        "a/NO VALE@1",
        ",c/0/0@1",
        ",".join(f"c/{n - 30}/0@1" for n in range(MAX_LOTE + 1)),
    ],
)
async def test_un_lote_mal_pedido_da_400(world: World, t: str) -> None:
    for extra in ({}, {"resumen": "1"}):
        response = await ask(world, "/api/mundo/trozos", t=t, **extra)
        assert response.status == 400
        assert "error" in await response.json()
    nothing = await world.web.get("/api/mundo/trozos", params={"guild_id": str(GUILD_ID)})
    assert nothing.status == 401  # y sin sesión no se mira ni el lote


async def test_un_lote_de_64_claves_bien_formadas_se_acepta(world: World) -> None:
    t = ",".join(f"c/{n - 30}/0@1" for n in range(MAX_LOTE))
    assert (await ask(world, "/api/mundo/trozos", t=t)).status == 410  # bien formado, pero no está


async def test_una_fila_podada_da_410_y_el_indice_viejo_vuelve_al_de_ahora(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mundo_repo, "KEEP_VERSIONS", 2)
    revs: dict[str, int] = {}
    for x in (1, 2, 3):
        _status, saved = await save(world, {**mundo("a", x=float(x))}, revs)
        revs.update(saved["revs"])  # type: ignore[arg-type]
        await publish(world)
    # La fila 1 de la celda se cambió en la 2 y la 3, y con KEEP_VERSIONS = 2 se podó.
    gone = await ask(world, "/api/mundo/trozos", t="c/0/0@1")
    assert gone.status == 410
    assert "índice" in (await gone.json())["error"]
    alive = await ask(world, "/api/mundo/trozos", t="c/0/0@3,a/a@1")
    assert alive.status == 200
    # Y el índice de la versión podada ya no existe: sale el de ahora, sin caché.
    index = await ask(world, "/api/mundo/indice", v="1")
    assert index.headers["Cache-Control"] == "no-cache"
    assert (await index.json())["version"] == 3
    kept = await ask(world, "/api/mundo/indice", v="2")
    assert kept.headers["Cache-Control"] == MUNDO_CACHE and (await kept.json())["version"] == 2


async def test_el_indice_y_los_lotes_van_comprimidos_y_se_comprimen_una_vez(world: World) -> None:
    assert (await save(world, {"a/a": piezas("a"), "c/0/0": cell(1, 30)}))[0] == 200
    await publish(world)
    for _ in range(2):
        response = await ask(world, "/api/mundo/indice", gzip=True)
        assert response.headers["Content-Encoding"] == "gzip"
        assert (await response.json())["version"] == 1
    assert world.cog._cache.get(("indice.gz", GUILD_ID, 1)) is not None
    plain = await ask(world, "/api/mundo/indice")
    assert "Content-Encoding" not in plain.headers
    assert (await plain.json())["version"] == 1

    t = "c/0/0@1"
    for _ in range(2):
        batch = await ask(world, "/api/mundo/trozos", gzip=True, t=t)
        assert batch.headers["Content-Encoding"] == "gzip"
        assert len((await batch.json())["trozos"]["c/0/0"]["objetos"]) == 30
    assert world.cog._cache.get(("lote.gz", GUILD_ID, t, False)) is not None
    # Un lote que no llega a 1 KB sale tal cual aunque el cliente acepte gzip.
    small = await ask(world, "/api/mundo/trozos", gzip=True, t="a/a@1")
    assert "Content-Encoding" not in small.headers
    assert world.cog._cache.get(("lote.gz", GUILD_ID, "a/a@1", False)) is None


async def test_el_indice_con_su_version_se_guarda_para_siempre(world: World) -> None:
    assert (await save(world, mundo("arbol")))[0] == 200
    await publish(world)

    async def cache(v: str | None) -> tuple[str, int]:
        response = await ask(world, "/api/mundo/indice", **({"v": v} if v is not None else {}))
        return response.headers["Cache-Control"], (await response.json())["version"]

    assert await cache("1") == (MUNDO_CACHE, 1)
    # Sin versión o con una que no hay (futura, cero o rota): la de ahora, sin caché.
    assert await cache(None) == ("no-cache", 1)
    for raro in ("0", "2", "-1", "x", "1.5"):
        assert await cache(raro) == ("no-cache", 1)
    # Tras publicar otra, la vieja sigue valiendo y la de ahora cambia.
    assert (await save(world, {"c/0/0": mundo("arbol", x=5)["c/0/0"]}, {"c/0/0": 1}))[0] == 200
    await publish(world)
    assert await cache("1") == (MUNDO_CACHE, 1)
    assert await cache("2") == (MUNDO_CACHE, 2)
    assert await cache(None) == ("no-cache", 2)
    entered = await world.web.post(
        "/api/entrar", json={"guild_id": str(GUILD_ID)}, headers=auth(world)
    )
    assert (await entered.json())["mundo"] == 2


async def test_la_cache_del_mundo_no_pasa_de_su_tope(world: World) -> None:
    world.cog._cache = ByteCache(4000)  # cada celda de 25 objetos pesa ~2 KB
    pieces = {"a/a": piezas("a"), **{f"c/{n}/0": cell(80 * n + 1, 25) for n in range(5)}}
    assert (await save(world, pieces))[0] == 200
    await publish(world)
    for n in range(5):
        response = await ask(world, "/api/mundo/trozos", gzip=True, t=f"c/{n}/0@1")
        assert response.status == 200
        assert len((await response.json())["trozos"][f"c/{n}/0"]["objetos"]) == 25
        assert world.cog._cache.bytes <= 4000
    # Lo más antiguo se soltó y lo último sigue; lo soltado se vuelve a leer sin problema.
    assert world.cog._cache.get(("trozo", GUILD_ID, "c/0/0", 1)) is None
    assert world.cog._cache.get(("trozo", GUILD_ID, "c/4/0", 1)) is not None
    again = await ask(world, "/api/mundo/trozos", t="c/0/0@1")
    assert len((await again.json())["trozos"]["c/0/0"]["objetos"]) == 25


async def test_lo_que_piden_todos_a_la_vez_se_lee_una_sola_vez(world: World) -> None:
    pieces = {"a/a": piezas("a"), **{f"c/{n}/0": cell(80 * n + 1, 25) for n in range(3)}}
    assert (await save(world, pieces))[0] == 200
    await publish(world)
    world.cog._cache = ByteCache(10_000_000)  # lo que se leyó al publicar no cuenta
    repo = world.client.universo_mundo
    calls = {"index": 0, "pieces": 0}
    real_index, real_pieces = repo.index, repo.pieces_at

    async def index(*args: object, **kwargs: object) -> object:
        calls["index"] += 1
        await asyncio.sleep(0.05)
        return await real_index(*args, **kwargs)

    async def pieces_at(*args: object, **kwargs: object) -> object:
        calls["pieces"] += 1
        await asyncio.sleep(0.05)
        return await real_pieces(*args, **kwargs)

    repo.index, repo.pieces_at = index, pieces_at  # type: ignore[method-assign]
    t = "c/0/0@1,c/1/0@1,c/2/0@1"
    answers = await asyncio.gather(
        *(ask(world, "/api/mundo/indice", gzip=True) for _ in range(8)),
        *(ask(world, "/api/mundo/trozos", gzip=True, t=t) for _ in range(8)),
    )
    assert {r.status for r in answers} == {200}
    assert calls == {"index": 1, "pieces": 1}


async def test_los_lugares_salen_del_indice_sin_leer_los_trozos(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    heights = array("h", [100] * CELL_TERRAIN_SIDE**2)
    heights[5 * CELL_TERRAIN_SIDE + 7] = 6_000  # un pico de 60 m en (-66, -150), celda -1/-2
    terreno = {
        "alturas": base64.b64encode(heights.tobytes()).decode(),
        "colores": base64.b64encode(bytes(CELL_TERRAIN_SIDE**2)).decode(),
    }
    zonas = {"zonas": {"plaza": {"x": 30, "z": -20, "r": 4.5}}}
    pieces = {"zonas": zonas, "c/-1/-2": {"objetos": [], "terreno": terreno}}
    assert (await save(world, pieces))[0] == 200
    await publish(world)

    async def boom(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("los lugares no leen los trozos")

    monkeypatch.setattr(world.client.universo_mundo, "pieces_at", boom)
    world.cog._lugares.clear()
    places = await world.cog.places(GUILD_ID)
    assert places.at(300, 80, -200) == ["plaza"]
    assert places.at(-660, 600, -1500) == ["nieves"]
    assert places.at(-50, 497, 80) == []  # la cima de serie ya no lo es
    # Sin nada publicado (otro servidor), los de serie.
    assert (await world.cog.places(GUILD_ID + 1)).at(-80, 80, 600) == ["plaza"]


async def test_el_editor_baja_el_indice_y_las_celdas_por_lotes(world: World) -> None:
    """El índice no trae las celdas; se piden por lotes y lo que no existe sale con `rev` 0."""
    lejos = mundo("b", x=250)
    await save(world, {**mundo("a"), **lejos, "zonas": {"zonas": {}}})
    await save(world, {"c/0/0": None}, {"c/0/0": 1})
    response = await world.web.get(
        "/api/editor/indice", params={"guild_id": str(GUILD_ID)}, headers=auth(world)
    )
    assert response.headers["Cache-Control"] == "no-store"
    index = await response.json()
    assert index["trozos"] == {"a/a": 1, "a/b": 1, "c/3/0": 1}
    assert index["borrados"] == {"c/0/0": 2, "zonas": 1}  # unas zonas vacías no guardan nada
    assert index["docs"] == {"a/a": piezas("a"), "a/b": piezas("b")}  # sin celdas
    body = await get(world, "/api/editor/trozos", t="c/3/0,c/0/0,c/9/9")
    cells = body["trozos"]
    assert cells["c/3/0"]["rev"] == 1  # type: ignore[index]
    assert cells["c/3/0"]["doc"]["objetos"][0]["asset"] == "b"  # type: ignore[index]
    assert cells["c/0/0"] == {"doc": None, "rev": 2}  # type: ignore[index]
    assert cells["c/9/9"] == {"doc": None, "rev": 0}  # type: ignore[index]
    # Quién usa un asset, bajado o no.
    assert await get(world, "/api/editor/usos", asset="b") == {"celdas": ["c/3/0"]}
    assert await get(world, "/api/editor/usos", asset="a") == {"celdas": []}


async def test_los_lotes_del_editor_tienen_tope(world: World) -> None:
    query = {"guild_id": str(GUILD_ID)}
    for t in ("", ",".join(f"c/{i}/0" for i in range(MAX_LOTE + 1)), "c/0/0,c/0/0", "x/1"):
        response = await world.web.get(
            "/api/editor/trozos", params={**query, "t": t}, headers=auth(world)
        )
        assert response.status == 400, t
    bad = await world.web.get(
        "/api/editor/usos", params={**query, "asset": "../x"}, headers=auth(world)
    )
    assert bad.status == 400
