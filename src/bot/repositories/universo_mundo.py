"""Persistencia del mundo editable del Jovaniverso.

Tablas (en el mismo archivo SQLite que el resto del bot):

- `universo_mundo_borrador`: el borrador de cada servidor, con un `rev` que sube en
  cada guardado. Quien guarda dice qué `rev` creía que había; si ya es otro, otro
  administrador se le adelantó y no se pisa nada.
- `universo_mundo_versiones`: lo publicado, numerado desde 1 en cada servidor. Solo se
  guardan las `KEEP_VERSIONS` últimas.
- `universo_glb`: los modelos GLB importados, por el SHA-256 de su contenido y comunes
  a todos los servidores.

Solo guarda: lo que vale como mundo lo decide `bot.services.universo_editor`.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite

T = TypeVar("T")

#: Versiones publicadas que se conservan de cada servidor.
KEEP_VERSIONS = 50


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class UniversoMundoRepository:
    """Acceso SQLite al borrador, las versiones y los GLB del mundo.

    Args:
        database_path: Ruta del archivo SQLite persistente.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        return sqlite.connect(self.database_path)

    async def _run(self, operation: Callable[..., T], *args: object) -> T:
        """Ejecuta una operación SQLite fuera del event loop."""
        return await asyncio.to_thread(operation, *args)

    async def initialize(self) -> None:
        """Crea las tablas si no existen."""
        await self._run(self._initialize_sync)

    def _initialize_sync(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS universo_mundo_borrador (
                    guild_id INTEGER PRIMARY KEY,
                    doc TEXT NOT NULL,
                    rev INTEGER NOT NULL,
                    author_id INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS universo_mundo_versiones (
                    guild_id INTEGER NOT NULL,
                    version INTEGER NOT NULL,
                    doc TEXT NOT NULL,
                    author_id INTEGER NOT NULL,
                    published_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, version)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS universo_glb (
                    sha256 TEXT PRIMARY KEY,
                    data BLOB NOT NULL,
                    triangles INTEGER NOT NULL,
                    author_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    async def draft(self, guild_id: int) -> tuple[str | None, int]:
        """El texto del borrador y su `rev`; sin borrador, `(None, 0)`."""
        return await self._run(self._draft_sync, guild_id)

    def _draft_sync(self, guild_id: int) -> tuple[str | None, int]:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT doc, rev FROM universo_mundo_borrador WHERE guild_id = ?", (guild_id,)
            ).fetchone()
        finally:
            connection.close()
        return (row["doc"], int(row["rev"])) if row else (None, 0)

    async def save_draft(
        self, guild_id: int, doc_text: str, expected_rev: int, author_id: int
    ) -> int | None:
        """Guarda el borrador si su `rev` actual es `expected_rev` (0 = no hay).

        Returns:
            El `rev` nuevo, o `None` si no coincidía y no se ha guardado nada.
        """
        return await self._run(self._save_draft_sync, guild_id, doc_text, expected_rev, author_id)

    def _save_draft_sync(
        self, guild_id: int, doc_text: str, expected_rev: int, author_id: int
    ) -> int | None:
        connection = self._connect()
        try:
            with connection:
                if expected_rev == 0:
                    # Si ya hay borrador, el conflicto deja la fila como estaba.
                    cursor = connection.execute(
                        "INSERT OR IGNORE INTO universo_mundo_borrador "
                        "(guild_id, doc, rev, author_id, updated_at) VALUES (?, ?, 1, ?, ?)",
                        (guild_id, doc_text, author_id, _now()),
                    )
                else:
                    cursor = connection.execute(
                        "UPDATE universo_mundo_borrador SET doc = ?, rev = rev + 1, "
                        "author_id = ?, updated_at = ? WHERE guild_id = ? AND rev = ?",
                        (doc_text, author_id, _now(), guild_id, expected_rev),
                    )
                saved = cursor.rowcount == 1
        finally:
            connection.close()
        return expected_rev + 1 if saved else None

    async def publish(self, guild_id: int, expected_rev: int, author_id: int) -> int | None:
        """Publica el borrador como versión nueva si su `rev` es `expected_rev`.

        Conserva solo las `KEEP_VERSIONS` últimas en la misma transacción.

        Returns:
            El número de versión, o `None` si no hay borrador o el `rev` no coincide.
        """
        return await self._run(self._publish_sync, guild_id, expected_rev, author_id)

    def _publish_sync(self, guild_id: int, expected_rev: int, author_id: int) -> int | None:
        connection = sqlite.connect(self.database_path, isolation_level=None)
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT doc, rev FROM universo_mundo_borrador WHERE guild_id = ?",
                    (guild_id,),
                ).fetchone()
                if row is None or int(row["rev"]) != expected_rev:
                    connection.execute("ROLLBACK")
                    return None
                last = connection.execute(
                    "SELECT MAX(version) FROM universo_mundo_versiones WHERE guild_id = ?",
                    (guild_id,),
                ).fetchone()[0]
                version = (last or 0) + 1
                connection.execute(
                    "INSERT INTO universo_mundo_versiones "
                    "(guild_id, version, doc, author_id, published_at) VALUES (?, ?, ?, ?, ?)",
                    (guild_id, version, row["doc"], author_id, _now()),
                )
                connection.execute(
                    "DELETE FROM universo_mundo_versiones WHERE guild_id = ? AND version <= ?",
                    (guild_id, version - KEEP_VERSIONS),
                )
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        finally:
            connection.close()
        return version

    async def published(self, guild_id: int) -> tuple[int, str] | None:
        """La última versión publicada: `(version, doc)`."""
        return await self._run(self._published_sync, guild_id)

    def _published_sync(self, guild_id: int) -> tuple[int, str] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT version, doc FROM universo_mundo_versiones WHERE guild_id = ? "
                "ORDER BY version DESC LIMIT 1",
                (guild_id,),
            ).fetchone()
        finally:
            connection.close()
        return (int(row["version"]), row["doc"]) if row else None

    async def versions(self, guild_id: int) -> list[tuple[int, int, str, int]]:
        """Versiones publicadas, de la más nueva a la más vieja.

        Cada una es `(version, author_id, published_at, bytes)`.
        """
        return await self._run(self._versions_sync, guild_id)

    def _versions_sync(self, guild_id: int) -> list[tuple[int, int, str, int]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT version, author_id, published_at, LENGTH(CAST(doc AS BLOB)) AS size "
                "FROM universo_mundo_versiones WHERE guild_id = ? ORDER BY version DESC",
                (guild_id,),
            ).fetchall()
        finally:
            connection.close()
        return [
            (int(r["version"]), int(r["author_id"]), r["published_at"], int(r["size"]))
            for r in rows
        ]

    async def version_doc(self, guild_id: int, version: int) -> str | None:
        """El documento de una versión publicada, si existe."""
        return await self._run(self._version_doc_sync, guild_id, version)

    def _version_doc_sync(self, guild_id: int, version: int) -> str | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT doc FROM universo_mundo_versiones WHERE guild_id = ? AND version = ?",
                (guild_id, version),
            ).fetchone()
        finally:
            connection.close()
        return row["doc"] if row else None

    async def put_glb(self, sha256: str, data: bytes, triangles: int, author_id: int) -> None:
        """Guarda un GLB por su SHA-256; si ya estaba, no hace nada."""
        await self._run(self._put_glb_sync, sha256, data, triangles, author_id)

    def _put_glb_sync(self, sha256: str, data: bytes, triangles: int, author_id: int) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    "INSERT OR IGNORE INTO universo_glb "
                    "(sha256, data, triangles, author_id, created_at) VALUES (?, ?, ?, ?, ?)",
                    (sha256, data, triangles, author_id, _now()),
                )
        finally:
            connection.close()

    async def glb(self, sha256: str) -> bytes | None:
        """Los bytes de un GLB, si está guardado."""
        return await self._run(self._glb_sync, sha256)

    def _glb_sync(self, sha256: str) -> bytes | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT data FROM universo_glb WHERE sha256 = ?", (sha256,)
            ).fetchone()
        finally:
            connection.close()
        return bytes(row["data"]) if row else None

    async def glb_triangles(self, shas: set[str]) -> dict[str, int]:
        """Triángulos de los GLB guardados entre `shas` (los que no están no salen)."""
        return await self._run(self._glb_triangles_sync, tuple(shas))

    def _glb_triangles_sync(self, shas: tuple[str, ...]) -> dict[str, int]:
        if not shas:
            return {}
        connection = self._connect()
        try:
            marks = ",".join("?" * len(shas))
            rows = connection.execute(
                f"SELECT sha256, triangles FROM universo_glb WHERE sha256 IN ({marks})",  # noqa: S608
                shas,
            ).fetchall()
        finally:
            connection.close()
        return {r["sha256"]: int(r["triangles"]) for r in rows}
