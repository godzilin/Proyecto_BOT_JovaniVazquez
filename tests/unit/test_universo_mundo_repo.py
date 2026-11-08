"""Repositorio del mundo editable: trozos con su `rev`, publicaciones, versiones y GLB."""

from __future__ import annotations

import json
import os
import sqlite3
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
    WorldTooBig,
)

CELL = '{"objetos":[{"asset":"pino"}]}'
PINO = '{"nombre":"Pino"}'


@pytest.fixture
async def repo(tmp_path: Path) -> UniversoMundoRepository:
    repository = UniversoMundoRepository(tmp_path / "bot.sqlite3")
    await repository.initialize()
    return repository


def put(rev: int, doc: str, *refs: str) -> Change:
    return Change(rev, doc, frozenset(refs))


async def test_sin_borrador_no_hay_nada(repo: UniversoMundoRepository) -> None:
    assert await repo.draft(1) == {}
    assert await repo.published(1) is None
    assert await repo.versions(1) == []
    assert await repo.revert(1, 1, 10) is False


async def test_cada_trozo_lleva_su_rev(repo: UniversoMundoRepository) -> None:
    revs = await repo.save(1, {"a/pino": put(0, PINO), "c/0/0": put(0, CELL, "pino")}, 10, 99)
    assert revs == {"a/pino": 1, "c/0/0": 1}
    # Otro editor toca otra celda sin chocar con nadie.
    assert await repo.save(1, {"c/1/0": put(0, CELL, "pino")}, 11, 99) == {"c/1/0": 1}
    assert await repo.save(1, {"c/0/0": put(1, '{"objetos":[]}')}, 11, 99) == {"c/0/0": 2}
    # Quien tenía el rev 1 de esa celda llega tarde: no se guarda nada, tampoco lo demás.
    with pytest.raises(Conflict):
        await repo.save(1, {"c/1/0": put(1, "x"), "c/0/0": put(1, "viejo")}, 12, 99)
    draft = await repo.draft(1)
    assert draft == {"a/pino": (PINO, 1), "c/0/0": ('{"objetos":[]}', 2), "c/1/0": (CELL, 1)}
    # Cada servidor lleva el suyo.
    assert await repo.save(2, {"a/pino": put(0, PINO)}, 10, 99) == {"a/pino": 1}


async def test_un_trozo_borrado_conserva_su_rev(repo: UniversoMundoRepository) -> None:
    await repo.save(1, {"a/pino": put(0, PINO)}, 10, 99)
    assert await repo.save(1, {"a/pino": put(1, "")}, 10, 99) == {"a/pino": 2}
    assert await repo.draft(1) == {"a/pino": ("", 2)}
    # Quien creía que no existía no lo resucita.
    with pytest.raises(Conflict):
        await repo.save(1, {"a/pino": put(0, PINO)}, 11, 99)


async def test_no_se_guarda_un_objeto_sin_su_asset(repo: UniversoMundoRepository) -> None:
    with pytest.raises(MissingAssets) as missing:
        await repo.save(1, {"c/0/0": put(0, CELL, "pino")}, 10, 99)
    assert missing.value.missing == ["pino"]
    await repo.save(1, {"a/pino": put(0, PINO), "c/0/0": put(0, CELL, "pino")}, 10, 99)
    # Borrar un asset que usa otra celda tampoco vale.
    with pytest.raises(MissingAssets):
        await repo.save(1, {"a/pino": put(1, "")}, 10, 99)
    assert await repo.draft(1) == {"a/pino": (PINO, 1), "c/0/0": (CELL, 1)}
    # Sobran assets.
    with pytest.raises(MissingAssets) as many:
        await repo.save(1, {"a/roca": put(0, PINO)}, 10, 1)
    assert many.value.missing == []


async def test_el_borrador_no_crece_por_encima_de_su_tope(repo: UniversoMundoRepository) -> None:
    tope = len(PINO) + len(CELL)
    # Justo en el tope, entra.
    await repo.save(1, {"a/pino": put(0, PINO), "c/0/0": put(0, CELL, "pino")}, 10, 99, tope)
    # Un byte más, no; y no se guarda nada de la tanda, tampoco lo que adelgaza.
    vacia = '{"objetos":[]}'
    with pytest.raises(WorldTooBig) as big:
        await repo.save(1, {"c/0/0": put(1, vacia), "c/1/0": put(0, CELL + " ")}, 10, 99, tope)
    assert (big.value.size, big.value.limit) == (len(PINO) + len(vacia) + len(CELL) + 1, tope)
    assert await repo.draft(1) == {"a/pino": (PINO, 1), "c/0/0": (CELL, 1)}
    # Cada servidor cuenta lo suyo.
    await repo.save(2, {"a/pino": put(0, PINO), "c/0/0": put(0, CELL, "pino")}, 10, 99, tope)


async def test_un_mundo_que_ya_pasaba_del_tope_se_edita_sin_crecer(
    repo: UniversoMundoRepository,
) -> None:
    await repo.save(1, {"a/pino": put(0, PINO), "c/0/0": put(0, CELL, "pino")}, 10, 99)
    tope = len(PINO)  # el tope llegó después: el borrador ya pasa
    # Lo mismo de largo, o más corto, vale.
    await repo.save(1, {"c/0/0": put(1, CELL, "pino")}, 10, 99, tope)
    await repo.save(1, {"a/pino": put(1, '{"nombre":"P"}')}, 10, 99, tope)
    # Crecer, aunque sea un byte, no.
    with pytest.raises(WorldTooBig):
        await repo.save(1, {"a/pino": put(2, '{"nombre":"Pi"}')}, 10, 99, tope)
    # Y se puede seguir publicando lo que hay.
    assert await repo.publish(1, 10) == 1


async def test_publicar_apunta_solo_lo_que_cambia(repo: UniversoMundoRepository) -> None:
    await repo.save(1, {"a/pino": put(0, PINO), "c/0/0": put(0, CELL, "pino")}, 10, 99)
    assert await repo.publish(1, 10) == 1
    await repo.save(1, {"c/0/0": put(1, ""), "c/5/5": put(0, CELL, "pino")}, 11, 99)
    assert await repo.publish(1, 11) == 2
    assert await repo.published(1) == (2, {"a/pino": PINO, "c/5/5": CELL})
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
    assert await repo.published(2) is None


async def test_lo_publicado_desde_una_version_solo_trae_lo_que_cambia(
    repo: UniversoMundoRepository,
) -> None:
    await repo.save(1, {"a/pino": put(0, PINO), "c/0/0": put(0, CELL, "pino")}, 10, 99)
    await repo.publish(1, 10)
    await repo.save(1, {"c/0/0": put(1, ""), "c/5/5": put(0, CELL, "pino")}, 11, 99)
    await repo.publish(1, 11)
    await repo.save(1, {"c/5/5": put(1, '{"objetos":[]}')}, 11, 99)
    await repo.publish(1, 11)
    # Desde la 1: lo borrado sale vacío y de cada clave, lo último.
    assert await repo.published_since(1, 1) == (3, {"c/0/0": "", "c/5/5": '{"objetos":[]}'})
    assert await repo.published_since(1, 2) == (3, {"c/5/5": '{"objetos":[]}'})
    assert await repo.published_since(1, 3) == (3, {})
    # Una versión que no hay (o de otro servidor): hace falta el mundo entero.
    assert await repo.published_since(1, 9) is None
    assert await repo.published_since(2, 1) is None


async def test_volver_deja_el_borrador_como_aquella_version(repo: UniversoMundoRepository) -> None:
    await repo.save(1, {"a/pino": put(0, PINO), "c/0/0": put(0, CELL, "pino")}, 10, 99)
    await repo.publish(1, 10)
    await repo.save(1, {"c/0/0": put(1, ""), "c/5/5": put(0, CELL, "pino")}, 10, 99)
    assert await repo.revert(1, 1, 12) is True
    assert await repo.draft(1) == {"a/pino": (PINO, 1), "c/0/0": (CELL, 3), "c/5/5": ("", 2)}
    assert await repo.revert(1, 7, 12) is False


async def test_podar_conserva_las_filas_de_las_versiones_que_quedan(
    repo: UniversoMundoRepository,
) -> None:
    await repo.save(1, {"a/pino": put(0, PINO)}, 10, 99)  # no cambia nunca más
    revs = {"c/0/0": 0}
    for n in range(1, KEEP_VERSIONS + 11):
        revs = await repo.save(1, {"c/0/0": put(revs["c/0/0"], f'{{"n":{n}}}')}, 10, 99)
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
    # Desde la más vieja que queda se sigue sabiendo lo cambiado; desde una podada, no.
    assert await repo.published_since(1, 11) == (
        KEEP_VERSIONS + 10,
        {"c/0/0": f'{{"n":{KEEP_VERSIONS + 10}}}'},
    )
    assert await repo.published_since(1, 10) is None


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
    version, published = await repo.published(1)  # type: ignore[misc]
    # La 5 la publica el bot con el casino de la plaza (`add_casino`), que el mundo no tenía.
    assert version == 5
    assert published["a/pino"] == pino and published["c/1/-1"] == moved_cell
    assert "a/traga-roja" in published
    assert [v[0] for v in await repo.versions(1)] == [5, 4, 3]
    draft = await repo.draft(1)
    assert draft["a/pino"] == (pino, 1) and draft["c/1/-1"] == (moved_cell, 1)
    assert "a/letrero-botes" in draft
    assert await repo.revert(1, 3, 10) is True
    assert (await repo.draft(1))["c/0/0"][0] == '{"objetos":[{"asset":"pino","pos":[1.0,0.0,2.0]}]}'
    # El borrado se ve: en la 4 ya no hay nada en la celda 0, 0.
    with pytest.raises(MissingAssets):
        await repo.save(1, {"a/pino": put(1, "")}, 10, 99)
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
    await repo.save(1, {"a/c": put(0, modelo(c))}, 10, 99)
    await repo.publish(1, 10)
    await repo.save(1, {"a/c": put(1, ""), "a/b": put(0, modelo(b))}, 10, 99)
    await repo.publish(1, 10)
    await repo.save(1, {"a/b": put(1, ""), "a/a": put(0, modelo(a))}, 10, 99)
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
    await repo.save(1, {"a/pino": put(0, PINO), "c/-1/0": put(0, cell, "pino")}, 10, 99)
    await repo.publish(1, 10)
    # Otro que ya tiene su tragaperras: no se toca.
    traga = json.dumps(
        {"nombre": "T", "tipo": "piezas", "piezas": [], "funcion": {"tipo": "traga"}}
    )
    await repo.save(2, {"a/mia": put(0, traga)}, 10, 99)
    connection = sqlite3.connect(repo.database_path)
    connection.execute("DELETE FROM universo_migraciones")
    connection.commit()
    connection.close()

    await repo.initialize()
    version, published = await repo.published(1)  # type: ignore[misc]
    assert version == 2
    objetos = json.loads(published["c/-1/0"])["objetos"]
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
    assert draft["c/-1/0"][1] == 2  # sube el rev: un editor abierto de antes no lo pisa
    assert set(draft) == set(published)
    assert set(await repo.draft(2)) == {"a/mia"}
    # Quitar el casino después es cosa de los editores: no vuelve al reiniciar.
    await repo.save(1, {"c/-1/0": put(2, cell, "pino")}, 10, 99)
    await repo.initialize()
    assert (await repo.draft(1))["c/-1/0"][0] == cell
