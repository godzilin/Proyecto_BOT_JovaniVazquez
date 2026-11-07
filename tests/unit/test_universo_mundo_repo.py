"""Repositorio del mundo editable: trozos con su `rev`, publicaciones, versiones y GLB."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from bot.repositories.universo_mundo import (
    KEEP_VERSIONS,
    Change,
    Conflict,
    MissingAssets,
    UniversoMundoRepository,
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
    assert await repo.glb("a" * 64) is None
    assert await repo.glb_triangles(set()) == {}
    await repo.put_glb("a" * 64, b"datos", 12, 10)
    await repo.put_glb("a" * 64, b"otros", 99, 11)  # ya estaba: no cambia
    await repo.put_glb("b" * 64, b"mas", 7, 10)
    assert await repo.glb("a" * 64) == b"datos"
    assert await repo.glb_triangles({"a" * 64, "b" * 64, "c" * 64}) == {"a" * 64: 12, "b" * 64: 7}


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
