"""Repositorio del mundo editable: trozos con su `rev`, publicaciones, versiones y GLB."""

from __future__ import annotations

import base64
import json
import os
import sqlite3
from array import array
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bot.repositories import universo_mundo as mundo_repo
from bot.repositories.universo_mundo import (
    KEEP_VERSIONS,
    Change,
    Conflict,
    GlbFull,
    MissingAssets,
    UniversoMundoRepository,
)
from bot.services.universo_editor import add_piso

CELL = '{"objetos":[{"asset":"pino"}]}'
PINO = '{"nombre":"Pino"}'


@pytest.fixture
async def repo(tmp_path: Path) -> UniversoMundoRepository:
    repository = UniversoMundoRepository(tmp_path / "bot.sqlite3")
    await repository.initialize()
    return repository


def put(rev: int, doc: str, *refs: str, resumen: str = "") -> Change:
    return Change(rev, doc, frozenset(refs), resumen)


async def published(
    repo: UniversoMundoRepository, guild_id: int = 1, version: int | None = None
) -> tuple[int, dict[str, str]] | None:
    """Lo publicado como `{clave: doc}` (sin borrados), leído por el índice y los lotes."""
    index = await repo.index(guild_id, version)
    if index is None:
        return None
    rows = await repo.pieces_at(guild_id, [(k, v) for k, (v, _) in index.pieces.items()])
    docs = {key: rows[key, row][0] for key, (row, _) in index.pieces.items()}
    docs.update({k: d for k, d in (("zonas", index.zonas), ("teleportes", index.teleportes)) if d})
    return index.version, docs


async def test_sin_borrador_no_hay_nada(repo: UniversoMundoRepository) -> None:
    assert await repo.draft(1) == {}
    assert await repo.index(1) is None
    assert await repo.version(1) == 0
    assert await repo.versions(1) == []
    assert await repo.revert(1, 1, 10) is False


async def test_cada_trozo_lleva_su_rev(repo: UniversoMundoRepository) -> None:
    revs = await repo.save(1, {"a/pino": put(0, PINO), "c/0/0": put(0, CELL, "pino")}, 10)
    assert revs == {"a/pino": 1, "c/0/0": 1}
    # Otro editor toca otra celda sin chocar con nadie.
    assert await repo.save(1, {"c/1/0": put(0, CELL, "pino")}, 11) == {"c/1/0": 1}
    assert await repo.save(1, {"c/0/0": put(1, '{"objetos":[]}')}, 11) == {"c/0/0": 2}
    # Quien tenía el rev 1 de esa celda llega tarde: no se guarda nada, tampoco lo demás.
    with pytest.raises(Conflict):
        await repo.save(1, {"c/1/0": put(1, "x"), "c/0/0": put(1, "viejo")}, 12)
    draft = await repo.draft(1)
    assert draft == {"a/pino": (PINO, 1), "c/0/0": ('{"objetos":[]}', 2), "c/1/0": (CELL, 1)}
    # Cada servidor lleva el suyo.
    assert await repo.save(2, {"a/pino": put(0, PINO)}, 10) == {"a/pino": 1}


async def test_un_trozo_borrado_conserva_su_rev(repo: UniversoMundoRepository) -> None:
    await repo.save(1, {"a/pino": put(0, PINO)}, 10)
    assert await repo.save(1, {"a/pino": put(1, "")}, 10) == {"a/pino": 2}
    assert await repo.draft(1) == {"a/pino": ("", 2)}
    # Quien creía que no existía no lo resucita.
    with pytest.raises(Conflict):
        await repo.save(1, {"a/pino": put(0, PINO)}, 11)


async def test_no_se_guarda_un_objeto_sin_su_asset(repo: UniversoMundoRepository) -> None:
    with pytest.raises(MissingAssets) as missing:
        await repo.save(1, {"c/0/0": put(0, CELL, "pino")}, 10)
    assert missing.value.missing == ["pino"]
    await repo.save(1, {"a/pino": put(0, PINO), "c/0/0": put(0, CELL, "pino")}, 10)
    # Borrar un asset que usa otra celda tampoco vale.
    with pytest.raises(MissingAssets):
        await repo.save(1, {"a/pino": put(1, "")}, 10)
    assert await repo.draft(1) == {"a/pino": (PINO, 1), "c/0/0": (CELL, 1)}
    # Sin tope de assets: caben tantos como se quiera.
    many = {f"a/a{n}": put(0, PINO) for n in range(2500)}
    assert len(await repo.save(1, many, 10)) == 2500


async def test_el_borrador_puede_pasar_de_8_mb(repo: UniversoMundoRepository) -> None:
    grande = '{"objetos":[],"x":"' + "a" * 900_000 + '"}'
    await repo.save(1, {"a/pino": put(0, PINO)}, 10)
    for tanda in range(3):  # 9 trozos de ~0,9 MB: 8,1 MB, y se sigue
        pieces = {f"c/{tanda}/{n}": put(0, grande) for n in range(3)}
        await repo.save(1, pieces, 10)
    await repo.save(1, {"c/9/9": put(0, grande), "c/9/8": put(0, grande)}, 10)
    assert sum(len(doc) for doc, _rev in (await repo.draft(1)).values()) > 8 * 1024 * 1024
    assert await repo.publish(1, 10) == 1
    index = await repo.index(1)
    assert index is not None and len(index.pieces) == 12


async def test_la_comprobacion_de_assets_solo_mira_lo_que_se_guarda(
    repo: UniversoMundoRepository,
) -> None:
    await repo.save(1, {"a/pino": put(0, PINO), "c/0/0": put(0, CELL, "pino")}, 10)
    # Un borrador que ya estaba roto (una celda con un asset que no hay) no frena otros guardados.
    connection = sqlite3.connect(repo.database_path)
    connection.execute(
        "INSERT INTO universo_trozos_borrador (guild_id, clave, doc, refs, rev, author_id, "
        "updated_at) VALUES (1, 'c/7/7', '{}', 'fantasma', 1, 0, '')"
    )
    connection.commit()
    connection.close()
    await repo.save(1, {"c/1/0": put(0, CELL, "pino")}, 10)
    # Lo que se guarda sí se comprueba: asset que falta, también entre varios, y en trozos.
    with pytest.raises(MissingAssets) as missing:
        await repo.save(1, {"c/2/0": put(0, CELL, "pino", "roca", "nube")}, 10)
    assert missing.value.missing == ["nube", "roca"]
    # Un asset que llega en el mismo guardado vale.
    await repo.save(1, {"a/roca": put(0, PINO), "c/2/0": put(0, CELL, "roca")}, 10)
    # Borrar un asset que aún usa una celda que se queda no vale; si la celda también se va, sí.
    with pytest.raises(MissingAssets) as used:
        await repo.save(1, {"a/roca": put(1, "")}, 10)
    assert used.value.missing == ["roca"]
    await repo.save(1, {"a/roca": put(1, ""), "c/2/0": put(1, "")}, 10)
    # Un asset borrado ya no cuenta como presente.
    with pytest.raises(MissingAssets):
        await repo.save(1, {"c/3/0": put(0, CELL, "roca")}, 10)
    # Muchas celdas con muchos assets a la vez (la consulta va por tandas).
    names = [f"a{n}" for n in range(1200)]
    await repo.save(1, {f"a/{n}": put(0, PINO) for n in names}, 10)
    await repo.save(1, {"c/4/0": put(0, CELL, *names)}, 10)


async def test_publicar_apunta_solo_lo_que_cambia(repo: UniversoMundoRepository) -> None:
    await repo.save(1, {"a/pino": put(0, PINO), "c/0/0": put(0, CELL, "pino")}, 10)
    assert await repo.publish(1, 10) == 1
    await repo.save(1, {"c/0/0": put(1, ""), "c/5/5": put(0, CELL, "pino")}, 11)
    assert await repo.publish(1, 11) == 2
    assert await published(repo) == (2, {"a/pino": PINO, "c/5/5": CELL})
    connection = sqlite3.connect(repo.database_path)
    rows = connection.execute(
        "SELECT clave, version FROM universo_trozos_publicados ORDER BY version, clave"
    ).fetchall()
    assert rows == [("a/pino", 1), ("c/0/0", 1), ("c/0/0", 2), ("c/5/5", 2)]
    versions = await repo.versions(1)
    assert [(v[0], v[1]) for v in versions] == [(2, 11), (1, 10)]
    assert versions[0][3] == len(PINO) + len(CELL)
    # Publicar sin cambios también es una versión (la que se ve en el historial).
    assert await repo.publish(1, 12) == 3
    assert await published(repo, 2) is None


async def test_el_indice_dice_la_version_de_la_fila_de_cada_trozo(
    repo: UniversoMundoRepository,
) -> None:
    zonas = '{"zonas":{}}'
    tele = '{"teleportes":[]}'
    await repo.save(
        1,
        {
            "a/pino": put(0, PINO),
            "c/0/0": put(0, CELL, "pino", resumen="5,0,AAA="),
            "zonas": put(0, zonas),
            "teleportes": put(0, tele),
        },
        10,
    )
    await repo.publish(1, 10)
    await repo.save(1, {"c/0/0": put(1, ""), "c/5/5": put(0, CELL, "pino")}, 11)
    await repo.publish(1, 11)
    await repo.save(1, {"c/5/5": put(1, '{"objetos":[]}'), "zonas": put(1, "")}, 11)
    await repo.publish(1, 11)
    assert await repo.version(1) == 3
    # La última: lo borrado no sale y cada trozo trae la versión de su fila.
    now = await repo.index(1)
    assert now is not None
    assert (now.version, now.pieces) == (3, {"a/pino": (1, ""), "c/5/5": (3, "")})
    assert (now.zonas, now.teleportes) == ("", tele)
    # Una vieja: lo de entonces, con el resumen que tenía.
    first = await repo.index(1, 1)
    assert first is not None
    assert first.pieces == {"a/pino": (1, ""), "c/0/0": (1, "5,0,AAA=")}
    assert (first.zonas, first.teleportes) == (zonas, tele)
    second = await repo.index(1, 2)
    assert second is not None and set(second.pieces) == {"a/pino", "c/5/5"}
    assert second.pieces["c/5/5"] == (2, "")
    # Una versión que no hay (o de otro servidor): no hay índice.
    assert await repo.index(1, 9) is None
    assert await repo.index(1, 0) is None
    assert await repo.index(2, 1) is None
    # Los trozos se piden por (clave, versión de la fila): los que no hay no salen.
    rows = await repo.pieces_at(1, [("c/0/0", 1), ("c/5/5", 2), ("c/5/5", 3), ("c/5/5", 9)])
    assert rows == {
        ("c/0/0", 1): (CELL, "5,0,AAA="),
        ("c/5/5", 2): (CELL, ""),
        ("c/5/5", 3): ('{"objetos":[]}', ""),
    }
    # Una lápida no es un trozo; solo con el resumen se devuelve (vacío) sin leer docs.
    assert await repo.pieces_at(1, [("c/0/0", 2)]) == {}
    assert await repo.pieces_at(1, [("c/0/0", 2), ("c/0/0", 1)], summary_only=True) == {
        ("c/0/0", 2): ("", ""),
        ("c/0/0", 1): ("", "5,0,AAA="),
    }
    assert await repo.pieces_at(1, []) == {}
    # Muchos a la vez (la consulta va por tandas).
    many = [("c/0/0", 1)] * 3 + [(f"c/{n}/0", 1) for n in range(1, 450)]
    assert set(await repo.pieces_at(1, many)) == {("c/0/0", 1)}


async def test_volver_deja_el_borrador_como_aquella_version(repo: UniversoMundoRepository) -> None:
    await repo.save(1, {"a/pino": put(0, PINO), "c/0/0": put(0, CELL, "pino")}, 10)
    await repo.publish(1, 10)
    await repo.save(1, {"c/0/0": put(1, ""), "c/5/5": put(0, CELL, "pino")}, 10)
    assert await repo.revert(1, 1, 12) is True
    assert await repo.draft(1) == {"a/pino": (PINO, 1), "c/0/0": (CELL, 3), "c/5/5": ("", 2)}
    assert await repo.revert(1, 7, 12) is False


async def test_podar_conserva_las_filas_de_las_versiones_que_quedan(
    repo: UniversoMundoRepository,
) -> None:
    await repo.save(1, {"a/pino": put(0, PINO)}, 10)  # no cambia nunca más
    revs = {"c/0/0": 0}
    for n in range(1, KEEP_VERSIONS + 11):
        revs = await repo.save(1, {"c/0/0": put(revs["c/0/0"], f'{{"n":{n}}}')}, 10)
        assert await repo.publish(1, 10) == n
    versions = await repo.versions(1)
    assert [v[0] for v in versions] == list(range(KEEP_VERSIONS + 10, 10, -1))
    assert await repo.revert(1, 10, 10) is False
    assert await repo.revert(1, 11, 10) is True
    # El asset se publicó en la 1, que ya no está, pero la 11 se apoya en él.
    assert await repo.draft(1) == {"a/pino": (PINO, 1), "c/0/0": ('{"n":11}', KEEP_VERSIONS + 11)}
    connection = sqlite3.connect(repo.database_path)
    (count,) = connection.execute("SELECT COUNT(*) FROM universo_trozos_publicados").fetchone()
    assert count == KEEP_VERSIONS + 1
    # La más vieja que queda se apoya en las filas conservadas; una podada ya no tiene índice.
    oldest = await repo.index(1, 11)
    assert oldest is not None
    assert oldest.pieces["a/pino"] == (1, "") and oldest.pieces["c/0/0"][0] == 11
    assert await published(repo, 1, 11) == (11, {"a/pino": PINO, "c/0/0": '{"n":11}'})
    assert await repo.index(1, 10) is None


async def test_las_tablas_del_formato_1_se_parten_en_trozos(tmp_path: Path) -> None:
    path = tmp_path / "bot.sqlite3"
    world = {
        "v": 1,
        "assets": {"pino": {"nombre": "Pino"}},
        "objetos": [{"asset": "pino", "pos": [1.0, 0.0, 2.0]}],
        "zonas": {},
    }
    moved = {**world, "objetos": [{"asset": "pino", "pos": [100.0, 0.0, -5.0]}]}
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE universo_mundo_borrador (guild_id INTEGER PRIMARY KEY, doc TEXT NOT NULL, "
        "rev INTEGER NOT NULL, author_id INTEGER NOT NULL, updated_at TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE universo_mundo_versiones (guild_id INTEGER NOT NULL, version INTEGER "
        "NOT NULL, doc TEXT NOT NULL, author_id INTEGER NOT NULL, published_at TEXT NOT NULL, "
        "PRIMARY KEY (guild_id, version))"
    )
    connection.execute(
        "INSERT INTO universo_mundo_borrador VALUES (1, ?, 7, 10, 'ayer')", (json.dumps(moved),)
    )
    for version, doc in ((3, world), (4, moved)):
        connection.execute(
            "INSERT INTO universo_mundo_versiones VALUES (1, ?, ?, 10, 'ayer')",
            (version, json.dumps(doc)),
        )
    connection.commit()
    connection.close()

    repo = UniversoMundoRepository(path)
    await repo.initialize()
    await repo.initialize()  # la segunda vez no hace nada
    pino = '{"nombre":"Pino"}'
    moved_cell = '{"objetos":[{"asset":"pino","pos":[100.0,0.0,-5.0]}]}'
    version, world = await published(repo) or (0, {})
    # La 5 la publica el bot con el casino de la plaza (`add_casino`), que el mundo no tenía.
    assert version == 5
    assert world["a/pino"] == pino and world["c/1/-1"] == moved_cell
    assert "a/traga-roja" in world
    assert [v[0] for v in await repo.versions(1)] == [5, 4, 3]
    draft = await repo.draft(1)
    assert draft["a/pino"] == (pino, 1) and draft["c/1/-1"] == (moved_cell, 1)
    assert "a/letrero-botes" in draft
    assert await repo.revert(1, 3, 10) is True
    assert (await repo.draft(1))["c/0/0"][0] == '{"objetos":[{"asset":"pino","pos":[1.0,0.0,2.0]}]}'
    # El borrado se ve: en la 4 ya no hay nada en la celda 0, 0.
    with pytest.raises(MissingAssets):
        await repo.save(1, {"a/pino": put(1, "")}, 10)
    tables = {
        row[0]
        for row in sqlite3.connect(path).execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"universo_mundo_borrador_v1", "universo_mundo_versiones_v1"} <= tables
    assert "universo_mundo_borrador" not in tables


async def test_glb_por_contenido(repo: UniversoMundoRepository) -> None:
    assert not repo.glb_path("a" * 64).exists()
    assert await repo.glb_triangles(set()) == {}
    await repo.put_glb("a" * 64, b"datos", 12, 10)
    await repo.put_glb("a" * 64, b"otros", 99, 11)  # ya estaba: no cambia
    await repo.put_glb("b" * 64, b"mas", 7, 10)
    assert repo.glb_path("a" * 64).read_bytes() == b"datos"
    assert repo.glb_path("a" * 64) == repo.database_path.parent / "universo_glb" / f"{'a' * 64}.glb"
    assert await repo.glb_triangles({"a" * 64, "b" * 64, "c" * 64}) == {"a" * 64: 12, "b" * 64: 7}
    # En la base solo queda la ficha; ni temporales en la carpeta.
    rows = sqlite3.connect(repo.database_path).execute(
        "SELECT sha256, length(data), bytes FROM universo_glb ORDER BY sha256"
    )
    assert rows.fetchall() == [("a" * 64, 0, 5), ("b" * 64, 0, 3)]
    assert sorted(p.name for p in repo.glb_dir.iterdir()) == [f"{c * 64}.glb" for c in "ab"]
    with pytest.raises(ValueError, match="SHA-256"):
        repo.glb_path("../bot.sqlite3")


async def test_los_glb_tienen_un_tope_entre_todos(
    repo: UniversoMundoRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mundo_repo, "MAX_GLB_TOTAL", 10)
    await repo.put_glb("a" * 64, b"123456", 1, 10)
    with pytest.raises(GlbFull):
        await repo.put_glb("b" * 64, b"12345", 1, 10)
    assert not repo.glb_path("b" * 64).exists()
    assert await repo.glb_triangles({"b" * 64}) == {}
    await repo.put_glb("a" * 64, b"123456", 1, 10)  # el que ya está sí se puede volver a subir
    await repo.put_glb("c" * 64, b"1234", 1, 10)  # lo justo cabe
    assert [p.name for p in repo.glb_dir.iterdir() if p.name.startswith(".")] == []


def _age(repo: UniversoMundoRepository, sha: str, days: int = 2) -> None:
    """Hace que un GLB parezca subido hace `days` días."""
    when = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    with sqlite3.connect(repo.database_path) as connection:
        connection.execute("UPDATE universo_glb SET created_at = ? WHERE sha256 = ?", (when, sha))


async def test_los_glb_sin_usar_se_borran_al_dia(repo: UniversoMundoRepository) -> None:
    def modelo(sha: str) -> str:
        return json.dumps({"nombre": "M", "tipo": "glb", "glb": sha, "colision": None})

    a, b, c, d, e = (x * 64 for x in "abcde")
    for sha in (a, b, c, d, e):
        await repo.put_glb(sha, sha.encode(), 1, 10)
    # a: en el borrador. b y c: solo en versiones publicadas que se conservan. d: no lo usa
    # nadie. e: tampoco, pero se subió hace nada.
    await repo.save(1, {"a/c": put(0, modelo(c))}, 10)
    await repo.publish(1, 10)
    await repo.save(1, {"a/c": put(1, ""), "a/b": put(0, modelo(b))}, 10)
    await repo.publish(1, 10)
    await repo.save(1, {"a/b": put(1, ""), "a/a": put(0, modelo(a))}, 10)
    for _ in range(KEEP_VERSIONS):
        await repo.publish(2, 10)  # otro servidor: no poda las de 1
    for sha in (a, b, c, d):
        _age(repo, sha)
    stray = repo.glb_dir / ".tmp-cortado.glb"
    stray.write_bytes(b"x")
    os.utime(stray, (0, 0))

    assert await repo.prune_glb() == 1
    assert {s for s in (a, b, c, d, e) if repo.glb_path(s).exists()} == {a, b, c, e}
    assert set(await repo.glb_triangles({a, b, c, d, e})) == {a, b, c, e}
    assert not stray.exists()
    # Cuando se podan las versiones 1 y 2, b y c ya no los usa nadie.
    for _ in range(KEEP_VERSIONS):
        await repo.publish(1, 10)
    assert await repo.prune_glb() == 2
    assert {s for s in (a, b, c, e) if repo.glb_path(s).exists()} == {a, e}
    # Volver a subir uno viejo le da otro día.
    _age(repo, e)
    await repo.put_glb(e, e.encode(), 1, 10)
    assert await repo.prune_glb() == 0


async def test_los_glb_de_la_base_pasan_a_archivos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "bot.sqlite3"
    with sqlite3.connect(path) as connection:  # la tabla de antes, con los bytes dentro
        connection.execute(
            "CREATE TABLE universo_glb (sha256 TEXT PRIMARY KEY, data BLOB NOT NULL, "
            "triangles INTEGER NOT NULL, author_id INTEGER NOT NULL, created_at TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO universo_glb VALUES (?, ?, 3, 10, ?)",
            [(c * 64, b"modelo " + c.encode(), datetime.now(UTC).isoformat()) for c in "ab"],
        )
    connection.close()
    repo = UniversoMundoRepository(path)
    # Se corta tras sacar el primero: el segundo sigue en la base y nada se pierde.
    real = mundo_repo._write_file
    calls = 0

    def cut(target: Path, data: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("corte de luz")
        real(target, data)

    monkeypatch.setattr(mundo_repo, "_write_file", cut)
    with pytest.raises(OSError, match="corte"):
        await repo.initialize()
    monkeypatch.undo()
    with sqlite3.connect(path) as connection:
        left = connection.execute(
            "SELECT sha256 FROM universo_glb WHERE length(data) > 0"
        ).fetchall()
    connection.close()
    assert len(left) == 1

    await repo.initialize()
    await repo.initialize()  # la segunda vez no hace nada
    for sha, data in (("a" * 64, b"modelo a"), ("b" * 64, b"modelo b")):
        assert repo.glb_path(sha).read_bytes() == data
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT length(data), bytes FROM universo_glb").fetchall()
        done = connection.execute(
            "SELECT guild_id FROM universo_migraciones WHERE nombre = 'glb_disco'"
        ).fetchall()
    connection.close()
    assert rows == [(0, 8), (0, 8)] and done == [(0,)]
    assert await repo.glb_triangles({"a" * 64, "b" * 64}) == {"a" * 64: 3, "b" * 64: 3}


async def test_los_mundos_sin_tragaperras_reciben_el_casino_una_sola_vez(tmp_path: Path) -> None:
    repo = UniversoMundoRepository(tmp_path / "bot.sqlite3")
    await repo.initialize()
    # Un servidor con algo guardado y publicado de antes, en la misma celda que el casino.
    cell = '{"objetos":[{"asset":"pino","pos":[-10.0,8.0,70.0]}]}'
    await repo.save(1, {"a/pino": put(0, PINO), "c/-1/0": put(0, cell, "pino")}, 10)
    await repo.publish(1, 10)
    # Otro que ya tiene su tragaperras: no se toca.
    traga = json.dumps(
        {"nombre": "T", "tipo": "piezas", "piezas": [], "funcion": {"tipo": "traga"}}
    )
    await repo.save(2, {"a/mia": put(0, traga)}, 10)
    connection = sqlite3.connect(repo.database_path)
    connection.execute("DELETE FROM universo_migraciones")
    connection.commit()
    connection.close()

    await repo.initialize()
    version, world = await published(repo) or (0, {})
    assert version == 2
    objetos = json.loads(world["c/-1/0"])["objetos"]
    assert objetos[0]["asset"] == "pino"
    assert sorted(o["datos"]["n"] for o in objetos if "n" in o.get("datos", {})) == [
        1,
        2,
        3,
        4,
        5,
        6,
    ]
    draft = await repo.draft(1)
    # Sube el rev (con el casino y con el piso de serie, que va en la misma celda): un editor
    # abierto de antes no lo pisa.
    assert draft["c/-1/0"][1] == 3
    assert set(world) <= set(draft)  # el borrador lleva además el piso de serie (`add_piso`)
    assert set(await repo.draft(2)) - set(add_piso({})) == {"a/mia"}
    # Quitar el casino después es cosa de los editores: no vuelve al reiniciar.
    await repo.save(1, {"c/-1/0": put(3, cell, "pino")}, 10)
    await repo.initialize()
    assert (await repo.draft(1))["c/-1/0"][0] == cell


async def test_el_borrador_de_antes_recibe_el_piso_de_serie_sin_publicarlo(tmp_path: Path) -> None:
    repo = UniversoMundoRepository(tmp_path / "bot.sqlite3")
    await repo.initialize()
    await repo.save(1, {"a/pino": put(0, PINO)}, 10)
    await repo.publish(1, 10)
    connection = sqlite3.connect(repo.database_path)
    connection.execute("DELETE FROM universo_migraciones WHERE nombre = 'piso'")
    connection.commit()
    connection.close()
    await repo.initialize()
    draft = await repo.draft(1)
    assert {"i/piso-plaza", "a/sofa", "a/puerta-pisos", "a/pino"} <= set(draft)
    _version, world = await published(repo) or (0, {})
    assert "i/piso-plaza" not in world
    # Lo usa el interior: el sofá no se puede borrar mientras esté.
    with pytest.raises(mundo_repo.MissingAssets):
        await repo.save(1, {"a/sofa": put(draft["a/sofa"][1], "")}, 10)
    # Quitarlo es cosa de los editores: no vuelve al reiniciar.
    await repo.save(1, {"i/piso-plaza": put(draft["i/piso-plaza"][1], "")}, 10)
    await repo.initialize()
    assert (await repo.draft(1))["i/piso-plaza"][0] == ""


def terrain_cell(peak: int) -> tuple[str, str]:
    """Una celda con terreno (su texto) y el resumen que le toca."""
    from bot.services.universo_editor import CELL_TERRAIN_SIDE, cell_summary

    heights = array("h", [100] * CELL_TERRAIN_SIDE**2)
    heights[3] = peak
    doc = {
        "objetos": [],
        "terreno": {"alturas": base64.b64encode(heights.tobytes()).decode(), "colores": ""},
    }
    return json.dumps(doc, separators=(",", ":")), cell_summary(doc)


async def test_el_resumen_viaja_del_borrador_a_lo_publicado_y_vuelve(
    repo: UniversoMundoRepository,
) -> None:
    await repo.save(
        1, {"a/pino": put(0, PINO), "c/0/0": put(0, CELL, "pino", resumen="9,3,AAA=")}, 10
    )
    await repo.publish(1, 10)
    # Se cambia en el borrador (y se publica); volver a la 1 recupera el resumen de entonces.
    await repo.save(1, {"c/0/0": put(1, CELL + " ", "pino", resumen="7,1,BBB=")}, 10)
    await repo.publish(1, 10)
    assert (await repo.index(1, 1)).pieces["c/0/0"] == (1, "9,3,AAA=")  # type: ignore[union-attr]
    assert (await repo.index(1, 2)).pieces["c/0/0"] == (2, "7,1,BBB=")  # type: ignore[union-attr]
    assert await repo.revert(1, 1, 10) is True
    rows = sqlite3.connect(repo.database_path).execute(
        "SELECT resumen FROM universo_trozos_borrador WHERE clave = 'c/0/0'"
    )
    assert rows.fetchall() == [("9,3,AAA=",)]


async def test_las_bases_de_antes_reciben_la_columna_y_el_resumen_una_sola_vez(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bot.sqlite3"
    cell, summary = terrain_cell(4_000)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE universo_trozos_borrador (
            guild_id INTEGER NOT NULL, clave TEXT NOT NULL, doc TEXT NOT NULL,
            refs TEXT NOT NULL, rev INTEGER NOT NULL, author_id INTEGER NOT NULL,
            updated_at TEXT NOT NULL, PRIMARY KEY (guild_id, clave));
        CREATE TABLE universo_trozos_publicados (
            guild_id INTEGER NOT NULL, clave TEXT NOT NULL, version INTEGER NOT NULL,
            doc TEXT NOT NULL, refs TEXT NOT NULL, PRIMARY KEY (guild_id, clave, version));
        CREATE TABLE universo_migraciones (
            guild_id INTEGER NOT NULL, nombre TEXT NOT NULL, PRIMARY KEY (guild_id, nombre));
        """
    )
    connection.execute("INSERT INTO universo_migraciones VALUES (1, 'casino')")
    connection.execute(
        "INSERT INTO universo_trozos_borrador VALUES (1, 'c/0/0', ?, '', 1, 10, 'ayer')", (cell,)
    )
    connection.execute(
        "INSERT INTO universo_trozos_borrador VALUES (1, 'c/1/0', ?, '', 1, 10, 'ayer')", (CELL,)
    )
    connection.execute(
        "INSERT INTO universo_trozos_publicados VALUES (1, 'c/0/0', 3, ?, '')", (cell,)
    )
    connection.commit()
    connection.close()

    repo = UniversoMundoRepository(path)
    await repo.initialize()

    def summaries() -> dict[str, list[str]]:
        db = sqlite3.connect(path)
        try:
            return {
                table: [
                    r[0]
                    for r in db.execute(
                        f"SELECT resumen FROM universo_trozos_{table} "
                        # Las celdas de la prueba (el borrador recibe además el piso de serie).
                        "WHERE clave IN ('c/0/0', 'c/1/0') ORDER BY clave"
                    )
                ]
                for table in ("borrador", "publicados")
            }
        finally:
            db.close()

    # La celda con terreno tiene su resumen, la que no, vacío; y lo publicado también.
    assert summaries() == {"borrador": [summary, ""], "publicados": [summary]}
    # Una sola vez: lo que se toque después no se recalcula al arrancar otra vez.
    db = sqlite3.connect(path)
    db.execute("UPDATE universo_trozos_borrador SET resumen = 'a mano' WHERE clave = 'c/0/0'")
    db.commit()
    db.close()
    await repo.initialize()
    assert summaries()["borrador"] == ["a mano", ""]


async def test_para_valorar_el_casino_solo_lee_las_celdas_con_tragaperras(
    repo: UniversoMundoRepository,
) -> None:
    assert await repo.casino_pieces(1) == (0, {})
    traga = '{"nombre":"Traga","funcion":{"tipo":"traga"}}'
    near = '{"objetos":[{"asset":"traga","pos":[1,0,1]}]}'
    border = '{"objetos":[{"asset":"pino","pos":[85,0,1]}]}'
    far = '{"objetos":[{"asset":"pino","pos":[900,0,900]}]}'
    changes = {
        "a/traga": put(0, traga),
        "a/pino": put(0, PINO),
        "c/0/0": put(0, near, "traga"),
        "c/1/0": put(0, border, "pino"),
        "c/11/11": put(0, far, "pino"),
    }
    await repo.save(1, changes, 10)
    version = await repo.publish(1, 10)
    found, pieces = await repo.casino_pieces(1)
    assert found == version
    assert set(pieces) == {"a/traga", "a/pino", "c/0/0", "c/1/0"}


async def test_el_indice_del_borrador_no_lee_las_celdas(repo: UniversoMundoRepository) -> None:
    zonas = '{"zonas":{}}'
    await repo.save(
        1,
        {
            "a/pino": put(0, PINO),
            "zonas": put(0, zonas),
            "c/0/0": put(0, CELL, "pino"),
            "c/-1/3": put(0, CELL, "pino"),
            "c/5/5": put(0, '{"objetos":[]}'),
        },
        10,
    )
    await repo.save(1, {"c/5/5": put(1, "")}, 10)
    index = await repo.draft_index(1)
    assert index.alive == {"a/pino": 1, "zonas": 1, "c/0/0": 1, "c/-1/3": 1}
    assert index.gone == {"c/5/5": 2}
    assert index.small == {"a/pino": PINO, "zonas": zonas}
    assert await repo.draft_pieces(1, ["c/0/0", "c/5/5", "c/9/9"]) == {
        "c/0/0": (CELL, 1),
        "c/5/5": ("", 2),
    }
    assert await repo.draft_users(1, "pino") == ["c/-1/3", "c/0/0"]
    assert await repo.draft_users(1, "pin") == []
    # La lista de trozos sale del índice, sin leer la tabla.
    connection = sqlite3.connect(repo.database_path)
    plan = connection.execute(
        "EXPLAIN QUERY PLAN SELECT clave, rev, doc = '' FROM universo_trozos_borrador "
        "INDEXED BY universo_trozos_borrador_indice WHERE guild_id = 1"
    ).fetchall()
    connection.close()
    assert "COVERING INDEX" in str(plan)
