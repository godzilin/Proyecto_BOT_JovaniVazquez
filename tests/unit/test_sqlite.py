"""Pruebas de bot.repositories.sqlite: la conexión común en modo WAL."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from bot.repositories import sqlite
from bot.repositories.economy import EconomyRepository
from bot.services.economy import STARTING_BALANCE


def test_la_conexion_usa_wal_y_no_sincroniza_en_cada_commit(tmp_path: Path) -> None:
    connection = sqlite.connect(tmp_path / "datos" / "bot.sqlite3")
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        # 1 = NORMAL: en modo WAL, un commit no espera al disco.
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 1
        assert connection.execute("SELECT 1 AS uno").fetchone()["uno"] == 1
    finally:
        connection.close()


async def test_los_repositorios_dejan_la_base_en_wal(tmp_path: Path) -> None:
    path = tmp_path / "bot.sqlite3"
    repository = EconomyRepository(path, starting_balance=STARTING_BALANCE)
    await repository.initialize()

    # Una conexión cualquiera, sin pasar por el módulo, ve el modo guardado en el archivo.
    raw = sqlite3.connect(path)
    try:
        assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        raw.close()


def _write_many(path: Path, count: int) -> None:
    # Filas de 80 KB: pocas conexiones llenan el registro (cada una cuesta ~30 ms en Windows).
    for i in range(count):
        connection = sqlite.connect(path)
        with connection:
            connection.execute("INSERT INTO t VALUES (?, ?)", (i, "x" * 80_000))
        connection.close()


def test_el_checkpoint_vacia_el_registro_que_sin_el_crece_sin_fin(tmp_path: Path) -> None:
    path = tmp_path / "bot.sqlite3"
    wal = Path(f"{path}-wal")
    connection = sqlite.connect(path)
    with connection:
        connection.execute("CREATE TABLE t (a, b)")
    connection.close()

    # Con una conexión por operación y sin checkpoint al cerrar, SQLite no lo recicla:
    # 60 escrituras de 80 KB dejan el registro por encima de sus 1.000 páginas (4 MB).
    _write_many(path, 60)
    assert wal.stat().st_size > 4_500 * 1024

    busy, pages, done = sqlite.checkpoint(path)
    assert busy == 0 and pages == done == 0
    # Vacío, o borrado si el checkpoint fue la última conexión en cerrarse.
    assert not wal.exists() or wal.stat().st_size == 0
    # Y lo escrito sigue ahí.
    connection = sqlite.connect(path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 60
    finally:
        connection.close()


def test_el_checkpoint_de_una_base_que_no_existe_no_la_crea(tmp_path: Path) -> None:
    path = tmp_path / "nada.sqlite3"
    assert sqlite.checkpoint(path) == (0, 0, 0)
    assert not path.exists()


async def test_el_bot_vacia_el_registro_al_arrancar_y_lo_hace_cada_minuto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bot.app import BotClient

    path = tmp_path / "bot.sqlite3"
    connection = sqlite.connect(path)
    with connection:
        connection.execute("CREATE TABLE t (a, b)")
    connection.close()
    _write_many(path, 5)
    client = BotClient(command_prefix=".", database_path=path)
    try:
        await client._checkpoint()
        wal = Path(f"{path}-wal")
        assert not wal.exists() or wal.stat().st_size == 0
        calls: list[Path] = []
        monkeypatch.setattr(sqlite, "CHECKPOINT_SECONDS", 0)
        monkeypatch.setattr(sqlite, "checkpoint", lambda p: calls.append(p) or (0, 0, 0))
        task = asyncio.create_task(client._checkpoint_loop())
        # El checkpoint va en un hilo: se espera a que llegue (2 s como mucho).
        for _ in range(40):
            await asyncio.sleep(0.05)
            if calls:
                break
        task.cancel()
        assert calls[:1] == [path]
    finally:
        await client.close()


async def test_la_tabla_de_impuestos_de_pisos_de_antes_pierde_su_check(tmp_path: Path) -> None:
    """Con el `CHECK (kind IN ('itp', 'plusvalia'))` de la primera versión de `pisos`,
    el IBI, el IGIC del vacacional, la licencia y la SOCIMI hacían fallar la transacción."""
    import sqlite3 as raw_sqlite

    from bot.repositories.economy import EconomyRepository as Repo

    path = tmp_path / "viejo.sqlite3"
    old = raw_sqlite.connect(path)
    old.execute(
        """
        CREATE TABLE economy_housing_tax (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('itp', 'plusvalia')),
            base INTEGER NOT NULL CHECK (base >= 0),
            tax INTEGER NOT NULL CHECK (tax > 0),
            created_at REAL NOT NULL
        )
        """
    )
    old.execute(
        "INSERT INTO economy_housing_tax (guild_id, user_id, kind, base, tax, created_at) "
        "VALUES (1, 2, 'itp', 1000, 65, 0)"
    )
    old.commit()
    old.close()

    await Repo(path, starting_balance=0).initialize()

    connection = raw_sqlite.connect(path)
    try:
        for kind in ("ibi", "igic", "licencia", "socimi"):
            connection.execute(
                "INSERT INTO economy_housing_tax (guild_id, user_id, kind, base, tax, created_at) "
                "VALUES (1, 2, ?, 100, 1, 0)",
                (kind,),
            )
        rows = connection.execute("SELECT kind FROM economy_housing_tax ORDER BY id").fetchall()
        assert [r[0] for r in rows] == ["itp", "ibi", "igic", "licencia", "socimi"]
    finally:
        connection.close()
    # Y una segunda vez no rehace nada.
    await Repo(path, starting_balance=0).initialize()


def test_cada_sentencia_apunta_su_repositorio_operacion_y_tiempo(tmp_path: Path) -> None:
    sqlite.drain_timings()
    connection = sqlite.connect(tmp_path / "bot.sqlite3")
    try:
        with connection:
            connection.execute("CREATE TABLE t (a INTEGER)")
            connection.executemany("INSERT INTO t VALUES (?)", [(1,), (2,)])
        assert connection.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2
    finally:
        connection.close()
    timings = sqlite.drain_timings()
    # Los PRAGMA de cada conexión no cuentan; el repositorio es el módulo que conecta.
    assert [(repo, op) for repo, op, _ms, _error in timings] == [
        ("test_sqlite", "escritura"),
        ("test_sqlite", "escritura"),
        ("test_sqlite", "commit"),
        ("test_sqlite", "lectura"),
    ]
    assert all(ms >= 0 and not error for _repo, _op, ms, error in timings)
    assert sqlite.drain_timings() == []


def test_un_database_is_locked_se_apunta_como_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sqlite, "BUSY_TIMEOUT_SECONDS", 0.05)
    path = tmp_path / "bot.sqlite3"
    first, second = sqlite.connect(path, isolation_level=None), sqlite.connect(path)
    try:
        first.execute("CREATE TABLE t (a INTEGER)")
        first.execute("BEGIN IMMEDIATE")
        sqlite.drain_timings()
        with pytest.raises(sqlite3.OperationalError):
            second.execute("INSERT INTO t VALUES (1)")
        assert sqlite.drain_timings()[-1][1:][::2] == ("escritura", True)
    finally:
        first.execute("ROLLBACK")
        first.close()
        second.close()
