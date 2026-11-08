"""Persistencia del mundo editable del Jovaniverso, por trozos (`docs/mundo-celdas.md`).

Tablas (en el mismo archivo SQLite que el resto del bot):

- `universo_trozos_borrador`: el borrador de cada servidor, una fila por trozo (`clave`:
  `zonas`, `a/<id>` o `c/<cx>/<cz>`). Cada trozo tiene su `rev`, que sube en cada guardado:
  quien guarda dice qué `rev` creía que había y, si ya es otro, otro editor se le adelantó y
  no se pisa nada. Un trozo borrado se queda con `doc` vacío (lápida) y sigue subiendo su
  `rev`, para que un editor desfasado no lo resucite. `refs` son los assets que usa una celda
  (separados por comas): no se guarda nada que deje un objeto sin su asset.
- `universo_trozos_publicados`: solo los trozos que cambian en cada publicación. Lo
  publicado en la versión N es, para cada clave, su fila más nueva con versión ≤ N (vacía si
  se borró).
- `universo_mundo_publicaciones`: una fila por publicación. Solo se guardan las
  `KEEP_VERSIONS` últimas; al podar, cada clave conserva la fila en la que se apoyan.
- `universo_glb`: las fichas de los modelos GLB importados (SHA-256 de su contenido,
  triángulos, autor, fecha y tamaño), comunes a todos los servidores. Los bytes van en
  archivos, `<carpeta de la base>/universo_glb/<sha256>.glb` (en Docker, el volumen `.data`):
  así no engordan la base del dinero, sus copias ni el WAL. La columna `data` se queda vacía.
  Caben `MAX_GLB_TOTAL` bytes entre todos, y los que no usa ningún trozo del borrador ni de
  las versiones que se conservan se borran al día de subirse (`prune_glb`).
- `universo_permisos`: quién tiene cada permiso del Jovaniverso (`editor`, `devtools`)
  en cada servidor y quién se lo dio. Los desarrolladores no salen aquí: los tienen
  todos siempre (`bot.cogs.universo.DEVS`).

- `universo_migraciones`: los arreglos de una sola vez ya hechos en cada servidor. Uno
  es `casino`: los mundos guardados o publicados antes de que las tragaperras fueran objetos
  del editor reciben el casino de la plaza (`bot.services.universo_editor.add_casino`), en el
  borrador y en una publicación nueva del bot (`author_id` 0). La otra, `glb_disco` (con
  `guild_id` 0, porque los GLB son de todos), saca a archivos los GLB que se guardaban en la
  base.

Las tablas del formato 1 (`universo_mundo_borrador` y `universo_mundo_versiones`, un
documento entero por fila) se parten en trozos al arrancar, una sola vez, y se quedan como
copia con `_v1` al final del nombre.

Solo guarda: lo que vale como trozo lo decide `bot.services.universo_editor`.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

from bot.repositories import sqlite
from bot.services.universo_editor import add_casino, glb_refs

T = TypeVar("T")

#: Versiones publicadas que se conservan de cada servidor.
KEEP_VERSIONS = 50

#: Lo que pueden ocupar en disco todos los GLB juntos. Cada uno pesa hasta 1 MB y un mundo
#: bien decorado usa unas decenas: caben unos 200 modelos grandes, de sobra para los mundos de
#: todos los servidores, y en el peor caso son 200 MB más en el volumen y en cada copia.
MAX_GLB_TOTAL = 200 * 1024 * 1024

#: Un GLB sin usar se borra pasado este tiempo: da margen a quien lo sube y aún no ha guardado.
GLB_GRACE = timedelta(days=1)

_SHA256 = re.compile(r"[0-9a-f]{64}")


def _draft_bytes(connection: sqlite3.Connection, guild_id: int) -> int:
    """Bytes de todos los trozos del borrador (como `len(doc.encode())`, no en caracteres)."""
    return int(
        connection.execute(
            "SELECT COALESCE(SUM(length(CAST(doc AS BLOB))), 0) "
            "FROM universo_trozos_borrador WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()[0]
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Conflict(Exception):
    """Algún trozo ya no tiene el `rev` que creía quien guarda; no se ha guardado nada."""


class GlbFull(Exception):
    """El GLB no cabe: los guardados ya suman `MAX_GLB_TOTAL`; no se ha guardado."""


class MissingAssets(Exception):
    """El guardado dejaría objetos sin su asset (o demasiados assets); no se ha guardado nada.

    Attributes:
        missing: Los assets que faltarían (vacío si lo que sobra son assets).
    """

    def __init__(self, missing: Iterable[str]) -> None:
        self.missing = sorted(missing)
        super().__init__(", ".join(self.missing))


class WorldTooBig(Exception):
    """El guardado haría crecer el borrador por encima de su tope; no se ha guardado nada.

    Attributes:
        size: Lo que pesaría el borrador entero (bytes de sus trozos en JSON).
        limit: Lo que se permite.
    """

    def __init__(self, size: int, limit: int) -> None:
        self.size, self.limit = size, limit
        super().__init__(f"{size} > {limit}")


@dataclass(frozen=True, slots=True)
class Change:
    """Un trozo que se guarda.

    Attributes:
        rev: El `rev` que cree tener quien guarda (0 si cree que no existe).
        doc: Su JSON ya validado, o `""` para borrarlo.
        refs: Los assets que usa (solo las celdas).
    """

    rev: int
    doc: str
    refs: frozenset[str] = frozenset()


def _refs(refs: Iterable[str]) -> str:
    return ",".join(sorted(refs))


def _latest_sql(table_filter: str = "") -> str:
    # La fila más nueva de cada clave (con versión ≤ ? si se filtra).
    return (
        "SELECT p.clave, p.doc, p.refs FROM universo_trozos_publicados p JOIN ("
        "  SELECT clave, MAX(version) AS v FROM universo_trozos_publicados"
        f"  WHERE guild_id = ? {table_filter} GROUP BY clave"
        ") m ON p.clave = m.clave AND p.version = m.v WHERE p.guild_id = ?"
    )


class UniversoMundoRepository:
    """Acceso SQLite al borrador, las versiones y los GLB del mundo.

    Args:
        database_path: Ruta del archivo SQLite persistente.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.glb_dir = database_path.parent / "universo_glb"

    def _connect(self) -> sqlite3.Connection:
        return sqlite.connect(self.database_path)

    async def _run(self, operation: Callable[..., T], *args: object) -> T:
        """Ejecuta una operación SQLite fuera del event loop."""
        return await asyncio.to_thread(operation, *args)

    def _transaction(self, work: Callable[[sqlite3.Connection], T]) -> T:
        """Ejecuta `work` en una transacción de escritura (`BEGIN IMMEDIATE`)."""
        connection = sqlite.connect(self.database_path, isolation_level=None)
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                result = work(connection)
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        finally:
            connection.close()
        return result

    async def initialize(self) -> None:
        """Crea las tablas si no existen y parte en trozos las del formato 1."""
        await self._run(self._initialize_sync)

    def _initialize_sync(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS universo_trozos_borrador (
                    guild_id INTEGER NOT NULL,
                    clave TEXT NOT NULL,
                    doc TEXT NOT NULL,
                    refs TEXT NOT NULL,
                    rev INTEGER NOT NULL,
                    author_id INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, clave)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS universo_trozos_publicados (
                    guild_id INTEGER NOT NULL,
                    clave TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    doc TEXT NOT NULL,
                    refs TEXT NOT NULL,
                    PRIMARY KEY (guild_id, clave, version)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS universo_mundo_publicaciones (
                    guild_id INTEGER NOT NULL,
                    version INTEGER NOT NULL,
                    author_id INTEGER NOT NULL,
                    published_at TEXT NOT NULL,
                    bytes INTEGER NOT NULL,
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
                    created_at TEXT NOT NULL,
                    bytes INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            columns = {r[1] for r in connection.execute("PRAGMA table_info(universo_glb)")}
            if "bytes" not in columns:
                connection.execute(
                    "ALTER TABLE universo_glb ADD COLUMN bytes INTEGER NOT NULL DEFAULT 0"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS universo_migraciones (
                    guild_id INTEGER NOT NULL,
                    nombre TEXT NOT NULL,
                    PRIMARY KEY (guild_id, nombre)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS universo_permisos (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    permiso TEXT NOT NULL,
                    author_id INTEGER NOT NULL,
                    granted_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, user_id, permiso)
                )
                """
            )
        connection = self._connect()
        try:
            old = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN "
                    "('universo_mundo_borrador', 'universo_mundo_versiones')"
                )
            }
        finally:
            connection.close()
        if old:
            self._transaction(lambda c: _migrate_v1(c, old))
        self._transaction(_migrate_casino)
        self._migrate_glb_sync()
        self._prune_glb_sync()

    def _migrate_glb_sync(self) -> None:
        """Saca a archivos los GLB que se guardaban en la base y vacía su columna, una vez.

        De uno en uno: el archivo queda escrito (y en disco) antes de vaciar su fila, así que si
        se corta a medias, lo que falte se saca en el siguiente arranque y no se pierde nada.
        """
        connection = self._connect()
        try:
            if connection.execute(
                "SELECT 1 FROM universo_migraciones WHERE guild_id = 0 AND nombre = 'glb_disco'"
            ).fetchone():
                return
            pending = [
                r[0]
                for r in connection.execute(
                    "SELECT sha256 FROM universo_glb WHERE length(data) > 0"
                )
            ]
        finally:
            connection.close()
        for sha in pending:
            data = self._blob(sha)
            if data is None:
                continue
            _write_file(self.glb_path(sha), data)
            size = len(data)
            self._transaction(
                lambda c, sha=sha, size=size: c.execute(
                    "UPDATE universo_glb SET data = x'', bytes = ? WHERE sha256 = ?", (size, sha)
                )
            )
        self._transaction(
            lambda c: c.execute(
                "INSERT OR IGNORE INTO universo_migraciones (guild_id, nombre) "
                "VALUES (0, 'glb_disco')"
            )
        )

    def _blob(self, sha: str) -> bytes | None:
        """Los bytes de un GLB que aún está dentro de la base (solo para migrarlo)."""
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT data FROM universo_glb WHERE sha256 = ? AND length(data) > 0", (sha,)
            ).fetchone()
        finally:
            connection.close()
        return bytes(row[0]) if row else None

    async def permissions(self, guild_id: int, user_id: int) -> set[str]:
        """Los permisos que tiene un miembro en un servidor."""
        return await self._run(self._permissions_sync, guild_id, user_id)

    def _permissions_sync(self, guild_id: int, user_id: int) -> set[str]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT permiso FROM universo_permisos WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchall()
        finally:
            connection.close()
        return {row["permiso"] for row in rows}

    async def granted(self, guild_id: int) -> list[tuple[int, str]]:
        """Todos los permisos dados en un servidor, como `(user_id, permiso)`."""
        return await self._run(self._granted_sync, guild_id)

    def _granted_sync(self, guild_id: int) -> list[tuple[int, str]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT user_id, permiso FROM universo_permisos WHERE guild_id = ? "
                "ORDER BY granted_at, user_id, permiso",
                (guild_id,),
            ).fetchall()
        finally:
            connection.close()
        return [(int(row["user_id"]), row["permiso"]) for row in rows]

    async def set_permission(
        self, guild_id: int, user_id: int, permiso: str, on: bool, author_id: int
    ) -> None:
        """Da (`on`) o quita un permiso; darlo dos veces o quitar uno que no hay no hace nada."""
        await self._run(self._set_permission_sync, guild_id, user_id, permiso, on, author_id)

    def _set_permission_sync(
        self, guild_id: int, user_id: int, permiso: str, on: bool, author_id: int
    ) -> None:
        connection = self._connect()
        try:
            with connection:
                if on:
                    connection.execute(
                        "INSERT OR IGNORE INTO universo_permisos "
                        "(guild_id, user_id, permiso, author_id, granted_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (guild_id, user_id, permiso, author_id, _now()),
                    )
                else:
                    connection.execute(
                        "DELETE FROM universo_permisos "
                        "WHERE guild_id = ? AND user_id = ? AND permiso = ?",
                        (guild_id, user_id, permiso),
                    )
        finally:
            connection.close()

    async def draft(self, guild_id: int) -> dict[str, tuple[str, int]]:
        """El borrador: clave → `(doc, rev)`, con las lápidas (`doc` vacío) incluidas."""
        return await self._run(self._draft_sync, guild_id)

    def _draft_sync(self, guild_id: int) -> dict[str, tuple[str, int]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT clave, doc, rev FROM universo_trozos_borrador WHERE guild_id = ?",
                (guild_id,),
            ).fetchall()
        finally:
            connection.close()
        return {r["clave"]: (r["doc"], int(r["rev"])) for r in rows}

    async def save(
        self,
        guild_id: int,
        changes: dict[str, Change],
        author_id: int,
        max_assets: int,
        max_bytes: int | None = None,
    ) -> dict[str, int]:
        """Guarda unos trozos del borrador, todos o ninguno.

        Returns:
            El `rev` nuevo de cada trozo guardado.

        Raises:
            Conflict: Si algún trozo ya no tiene el `rev` que se creía.
            MissingAssets: Si quedaría algún objeto sin su asset o más de `max_assets`.
            WorldTooBig: Si el borrador pasaría de `max_bytes` y además crece: uno que ya
                pasaba (guardado antes del tope) se puede seguir editando sin engordar.
        """
        return await self._run(self._save_sync, guild_id, changes, author_id, max_assets, max_bytes)

    def _save_sync(
        self,
        guild_id: int,
        changes: dict[str, Change],
        author_id: int,
        max_assets: int,
        max_bytes: int | None,
    ) -> dict[str, int]:
        def work(connection: sqlite3.Connection) -> dict[str, int]:
            revs = {}
            before = after = _draft_bytes(connection, guild_id)
            for key, change in changes.items():
                row = connection.execute(
                    "SELECT rev, length(CAST(doc AS BLOB)) AS bytes "
                    "FROM universo_trozos_borrador WHERE guild_id = ? AND clave = ?",
                    (guild_id, key),
                ).fetchone()
                if (int(row["rev"]) if row else 0) != change.rev:
                    raise Conflict(key)
                after += len(change.doc.encode()) - (int(row["bytes"]) if row else 0)
                revs[key] = change.rev + 1
                connection.execute(
                    "INSERT INTO universo_trozos_borrador "
                    "(guild_id, clave, doc, refs, rev, author_id, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (guild_id, clave) DO UPDATE SET "
                    "doc = excluded.doc, refs = excluded.refs, rev = excluded.rev, "
                    "author_id = excluded.author_id, updated_at = excluded.updated_at",
                    (guild_id, key, change.doc, _refs(change.refs), revs[key], author_id, _now()),
                )
            _check_refs(connection, guild_id, "universo_trozos_borrador", max_assets)
            if max_bytes is not None and after > max_bytes and after > before:
                raise WorldTooBig(after, max_bytes)
            return revs

        return self._transaction(work)

    async def publish(self, guild_id: int, author_id: int) -> int:
        """Publica el borrador tal como esté como versión nueva; devuelve su número.

        Solo apunta los trozos que han cambiado desde la versión anterior y conserva las
        `KEEP_VERSIONS` últimas, en la misma transacción.
        """
        return await self._run(self._publish_sync, guild_id, author_id)

    def _publish_sync(self, guild_id: int, author_id: int) -> int:
        def work(connection: sqlite3.Connection) -> int:
            last = connection.execute(
                "SELECT MAX(version) FROM universo_mundo_publicaciones WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()[0]
            version = (last or 0) + 1
            published = {
                r["clave"]: r["doc"]
                for r in connection.execute(_latest_sql(), (guild_id, guild_id))
            }
            size = 0
            for r in connection.execute(
                "SELECT clave, doc, refs FROM universo_trozos_borrador WHERE guild_id = ?",
                (guild_id,),
            ).fetchall():
                size += len(r["doc"].encode())
                if published.get(r["clave"], "") != r["doc"]:
                    connection.execute(
                        "INSERT INTO universo_trozos_publicados "
                        "(guild_id, clave, version, doc, refs) VALUES (?, ?, ?, ?, ?)",
                        (guild_id, r["clave"], version, r["doc"], r["refs"]),
                    )
            connection.execute(
                "INSERT INTO universo_mundo_publicaciones "
                "(guild_id, version, author_id, published_at, bytes) VALUES (?, ?, ?, ?, ?)",
                (guild_id, version, author_id, _now(), size),
            )
            _prune(connection, guild_id, version - KEEP_VERSIONS + 1)
            return version

        return self._transaction(work)

    async def published(self, guild_id: int) -> tuple[int, dict[str, str]] | None:
        """La última versión publicada: `(version, {clave: doc})`, sin los trozos borrados."""
        return await self._run(self._published_sync, guild_id)

    def _published_sync(self, guild_id: int) -> tuple[int, dict[str, str]] | None:
        connection = self._connect()
        try:
            version = connection.execute(
                "SELECT MAX(version) FROM universo_mundo_publicaciones WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()[0]
            if version is None:
                return None
            rows = connection.execute(_latest_sql(), (guild_id, guild_id)).fetchall()
        finally:
            connection.close()
        return int(version), {r["clave"]: r["doc"] for r in rows if r["doc"]}

    async def published_since(self, guild_id: int, since: int) -> tuple[int, dict[str, str]] | None:
        """Lo que cambió desde la versión `since` hasta la última: `(version, {clave: doc})`.

        Un trozo borrado sale con `doc` vacío. `None` si `since` ya no se conserva (o no
        existe): entonces hace falta el mundo entero.
        """
        return await self._run(self._published_since_sync, guild_id, since)

    def _published_since_sync(self, guild_id: int, since: int) -> tuple[int, dict[str, str]] | None:
        connection = self._connect()
        try:
            known = connection.execute(
                "SELECT 1 FROM universo_mundo_publicaciones WHERE guild_id = ? AND version = ?",
                (guild_id, since),
            ).fetchone()
            if known is None:
                return None
            version = connection.execute(
                "SELECT MAX(version) FROM universo_mundo_publicaciones WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()[0]
            # La poda solo quita filas de antes de la versión más vieja que se conserva, así
            # que las de después de `since` están todas.
            rows = connection.execute(
                _latest_sql("AND version > ?"), (guild_id, since, guild_id)
            ).fetchall()
        finally:
            connection.close()
        return int(version), {r["clave"]: r["doc"] for r in rows}

    async def versions(self, guild_id: int) -> list[tuple[int, int, str, int]]:
        """Versiones publicadas, de la más nueva a la más vieja.

        Cada una es `(version, author_id, published_at, bytes)`; `bytes` es lo que pesa el
        mundo entero en esa versión.
        """
        return await self._run(self._versions_sync, guild_id)

    def _versions_sync(self, guild_id: int) -> list[tuple[int, int, str, int]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT version, author_id, published_at, bytes FROM universo_mundo_publicaciones "
                "WHERE guild_id = ? ORDER BY version DESC",
                (guild_id,),
            ).fetchall()
        finally:
            connection.close()
        return [
            (int(r["version"]), int(r["author_id"]), r["published_at"], int(r["bytes"]))
            for r in rows
        ]

    async def revert(self, guild_id: int, version: int, author_id: int) -> bool:
        """Pone en el borrador lo publicado en `version`; `False` si esa versión ya no existe.

        Sube el `rev` de cada trozo que cambia: los editores que lo tuvieran abierto chocan
        al guardarlo y recargan.
        """
        return await self._run(self._revert_sync, guild_id, version, author_id)

    def _revert_sync(self, guild_id: int, version: int, author_id: int) -> bool:
        def work(connection: sqlite3.Connection) -> bool:
            exists = connection.execute(
                "SELECT 1 FROM universo_mundo_publicaciones WHERE guild_id = ? AND version = ?",
                (guild_id, version),
            ).fetchone()
            if exists is None:
                return False
            target = {
                r["clave"]: (r["doc"], r["refs"])
                for r in connection.execute(
                    _latest_sql("AND version <= ?"), (guild_id, version, guild_id)
                )
            }
            draft = {
                r["clave"]: r["doc"]
                for r in connection.execute(
                    "SELECT clave, doc FROM universo_trozos_borrador WHERE guild_id = ?",
                    (guild_id,),
                )
            }
            for key in draft.keys() | target.keys():
                doc, refs = target.get(key, ("", ""))
                if draft.get(key, "") == doc:
                    continue
                connection.execute(
                    "INSERT INTO universo_trozos_borrador "
                    "(guild_id, clave, doc, refs, rev, author_id, updated_at) "
                    "VALUES (?, ?, ?, ?, 1, ?, ?) ON CONFLICT (guild_id, clave) DO UPDATE SET "
                    "doc = excluded.doc, refs = excluded.refs, rev = rev + 1, "
                    "author_id = excluded.author_id, updated_at = excluded.updated_at",
                    (guild_id, key, doc, refs, author_id, _now()),
                )
            return True

        return self._transaction(work)

    def glb_path(self, sha256: str) -> Path:
        """Dónde va el archivo de un GLB; no mira el disco.

        Raises:
            ValueError: Si `sha256` no son 64 cifras hexadecimales (acaba en una ruta).
        """
        if not _SHA256.fullmatch(sha256):
            raise ValueError(f"SHA-256 no válido: {sha256!r}")
        return self.glb_dir / f"{sha256}.glb"

    async def put_glb(self, sha256: str, data: bytes, triangles: int, author_id: int) -> None:
        """Guarda un GLB por su SHA-256; si ya estaba, solo le renueva la fecha.

        La fecha nueva le da otro `GLB_GRACE` antes de que `prune_glb` lo borre si nadie lo usa.

        Raises:
            GlbFull: Si es nuevo y no cabe en `MAX_GLB_TOTAL`.
        """
        await self._run(self._put_glb_sync, sha256, data, triangles, author_id)

    def _put_glb_sync(self, sha256: str, data: bytes, triangles: int, author_id: int) -> None:
        path = self.glb_path(sha256)
        # Se escribe (y se lleva al disco) fuera de la transacción; dentro solo se coloca, para
        # que `prune_glb`, que borra archivos dentro de la suya, nunca se cruce con una subida.
        temporary = _write_temporary(path.parent, data)

        def work(connection: sqlite3.Connection) -> None:
            known = connection.execute(
                "SELECT 1 FROM universo_glb WHERE sha256 = ?", (sha256,)
            ).fetchone()
            if known:
                connection.execute(
                    "UPDATE universo_glb SET created_at = ? WHERE sha256 = ?", (_now(), sha256)
                )
                if path.is_file():
                    return
            else:
                total = connection.execute("SELECT SUM(bytes) FROM universo_glb").fetchone()[0]
                if (total or 0) + len(data) > MAX_GLB_TOTAL:
                    raise GlbFull
                connection.execute(
                    "INSERT INTO universo_glb "
                    "(sha256, data, triangles, author_id, created_at, bytes) "
                    "VALUES (?, x'', ?, ?, ?, ?)",
                    (sha256, triangles, author_id, _now(), len(data)),
                )
            os.replace(temporary, path)

        try:
            self._transaction(work)
        finally:
            temporary.unlink(missing_ok=True)

    async def prune_glb(self) -> int:
        """Borra los GLB que no usa nadie y tienen más de `GLB_GRACE`; devuelve cuántos.

        Usar es salir en un asset del borrador o de lo publicado que se conserva
        (`KEEP_VERSIONS`), en cualquier servidor. También se lleva los archivos sin ficha
        (temporales de una subida cortada) igual de viejos.
        """
        return await self._run(self._prune_glb_sync)

    def _prune_glb_sync(self) -> int:
        cutoff = datetime.now(UTC) - GLB_GRACE

        def work(connection: sqlite3.Connection) -> int:
            used: set[str] = set()
            for table in ("universo_trozos_borrador", "universo_trozos_publicados"):
                for r in connection.execute(
                    f"SELECT doc FROM {table} WHERE clave LIKE 'a/%' AND doc != ''"  # noqa: S608
                ):
                    used |= glb_refs({"a": json.loads(r["doc"])})
            old = [
                r["sha256"]
                for r in connection.execute(
                    "SELECT sha256 FROM universo_glb WHERE created_at < ?",
                    (cutoff.isoformat(timespec="seconds"),),
                )
                if r["sha256"] not in used
            ]
            for sha in old:
                connection.execute("DELETE FROM universo_glb WHERE sha256 = ?", (sha,))
                self.glb_path(sha).unlink(missing_ok=True)
            known = {r[0] for r in connection.execute("SELECT sha256 FROM universo_glb")}
            if self.glb_dir.is_dir():
                for path in self.glb_dir.iterdir():
                    if (
                        path.is_file()
                        and path.stem not in known
                        and path.stat().st_mtime < cutoff.timestamp()
                    ):
                        path.unlink(missing_ok=True)
            return len(old)

        return self._transaction(work)

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


def _write_temporary(directory: Path, data: bytes) -> Path:
    """Escribe `data` en un temporal de `directory`, ya en disco (`fsync`), y lo devuelve."""
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".glb")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return Path(name)


def _write_file(path: Path, data: bytes) -> None:
    """Escribe un archivo de forma atómica: entero o nada, nunca a medias."""
    temporary = _write_temporary(path.parent, data)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _check_refs(connection: sqlite3.Connection, guild_id: int, table: str, max_assets: int) -> None:
    """Falla si alguna celda de `table` usa un asset que no está, o si sobran assets."""
    assets: set[str] = set()
    used: set[str] = set()
    for r in connection.execute(
        f"SELECT clave, refs FROM {table} WHERE guild_id = ? AND doc != ''",  # noqa: S608
        (guild_id,),
    ):
        if r["clave"].startswith("a/"):
            assets.add(r["clave"][2:])
        elif r["refs"]:
            used.update(r["refs"].split(","))
    if used - assets:
        raise MissingAssets(used - assets)
    if len(assets) > max_assets:
        raise MissingAssets(())


def _prune(connection: sqlite3.Connection, guild_id: int, oldest: int) -> None:
    """Olvida las versiones anteriores a `oldest` sin perder las filas en que se apoya."""
    connection.execute(
        "DELETE FROM universo_trozos_publicados AS p WHERE guild_id = ? AND version < ? "
        "AND EXISTS (SELECT 1 FROM universo_trozos_publicados q WHERE q.guild_id = p.guild_id "
        "AND q.clave = p.clave AND q.version > p.version AND q.version <= ?)",
        (guild_id, oldest, oldest),
    )
    connection.execute(
        "DELETE FROM universo_mundo_publicaciones WHERE guild_id = ? AND version < ?",
        (guild_id, oldest),
    )


def _pieces_v1(doc_text: str) -> dict[str, tuple[str, str]]:
    """Un mundo del formato 1 en trozos: clave → `(doc, refs)`."""
    from bot.services.universo_editor import split_world

    out = {}
    for key, doc in split_world(json.loads(doc_text)).items():
        refs = {o["asset"] for o in doc.get("objetos", [])} if key.startswith("c/") else ()
        out[key] = (json.dumps(doc, ensure_ascii=False, separators=(",", ":")), _refs(refs))
    return out


def _migrate_v1(connection: sqlite3.Connection, old: set[str]) -> None:
    """Parte en trozos el borrador y las versiones del formato 1 y deja las tablas como copia."""
    if "universo_mundo_versiones" in old:
        rows = connection.execute(
            "SELECT guild_id, version, doc, author_id, published_at FROM universo_mundo_versiones "
            "ORDER BY guild_id, version"
        ).fetchall()
        before: dict[int, dict[str, tuple[str, str]]] = {}
        for r in rows:
            guild_id, version = int(r["guild_id"]), int(r["version"])
            pieces = _pieces_v1(r["doc"])
            prev = before.get(guild_id, {})
            for key in pieces.keys() | prev.keys():
                doc, refs = pieces.get(key, ("", ""))
                if prev.get(key, ("", ""))[0] != doc:
                    connection.execute(
                        "INSERT INTO universo_trozos_publicados "
                        "(guild_id, clave, version, doc, refs) VALUES (?, ?, ?, ?, ?)",
                        (guild_id, key, version, doc, refs),
                    )
            before[guild_id] = pieces
            connection.execute(
                "INSERT INTO universo_mundo_publicaciones "
                "(guild_id, version, author_id, published_at, bytes) VALUES (?, ?, ?, ?, ?)",
                (guild_id, version, r["author_id"], r["published_at"], len(r["doc"].encode())),
            )
        connection.execute(
            "ALTER TABLE universo_mundo_versiones RENAME TO universo_mundo_versiones_v1"
        )
    if "universo_mundo_borrador" in old:
        for r in connection.execute(
            "SELECT guild_id, doc, author_id, updated_at FROM universo_mundo_borrador"
        ).fetchall():
            for key, (doc, refs) in _pieces_v1(r["doc"]).items():
                connection.execute(
                    "INSERT INTO universo_trozos_borrador "
                    "(guild_id, clave, doc, refs, rev, author_id, updated_at) "
                    "VALUES (?, ?, ?, ?, 1, ?, ?)",
                    (r["guild_id"], key, doc, refs, r["author_id"], r["updated_at"]),
                )
        connection.execute(
            "ALTER TABLE universo_mundo_borrador RENAME TO universo_mundo_borrador_v1"
        )


def _dump(doc: object) -> str:
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"))


def _cell_refs(key: str, doc: dict[str, Any]) -> str:
    return _refs({o["asset"] for o in doc.get("objetos", [])} if key.startswith("c/") else ())


def _migrate_casino(connection: sqlite3.Connection) -> None:
    """Pone el casino de la plaza en el borrador y lo publicado de cada servidor, una vez."""
    guilds = [
        int(r[0])
        for r in connection.execute(
            "SELECT guild_id FROM universo_trozos_borrador UNION "
            "SELECT guild_id FROM universo_mundo_publicaciones EXCEPT "
            "SELECT guild_id FROM universo_migraciones WHERE nombre = 'casino'"
        )
    ]
    for guild_id in guilds:
        revs: dict[str, int] = {}
        draft: dict[str, dict[str, Any]] = {}
        for r in connection.execute(
            "SELECT clave, doc, rev FROM universo_trozos_borrador WHERE guild_id = ?", (guild_id,)
        ):
            revs[r["clave"]] = int(r["rev"])
            if r["doc"]:
                draft[r["clave"]] = json.loads(r["doc"])
        for key, doc in (add_casino(draft) if draft else {}).items():
            connection.execute(
                "INSERT INTO universo_trozos_borrador "
                "(guild_id, clave, doc, refs, rev, author_id, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?) ON CONFLICT (guild_id, clave) DO UPDATE SET "
                "doc = excluded.doc, refs = excluded.refs, rev = excluded.rev, "
                "author_id = 0, updated_at = excluded.updated_at",
                (guild_id, key, _dump(doc), _cell_refs(key, doc), revs.get(key, 0) + 1, _now()),
            )
        last = connection.execute(
            "SELECT MAX(version) FROM universo_mundo_publicaciones WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()[0]
        if last is not None:
            published = {
                r["clave"]: json.loads(r["doc"])
                for r in connection.execute(_latest_sql(), (guild_id, guild_id))
                if r["doc"]
            }
            changed = add_casino(published)
            if changed:
                version = int(last) + 1
                for key, doc in changed.items():
                    connection.execute(
                        "INSERT INTO universo_trozos_publicados "
                        "(guild_id, clave, version, doc, refs) VALUES (?, ?, ?, ?, ?)",
                        (guild_id, key, version, _dump(doc), _cell_refs(key, doc)),
                    )
                size = sum(len(_dump(d).encode()) for d in {**published, **changed}.values())
                connection.execute(
                    "INSERT INTO universo_mundo_publicaciones "
                    "(guild_id, version, author_id, published_at, bytes) VALUES (?, ?, 0, ?, ?)",
                    (guild_id, version, _now(), size),
                )
        connection.execute(
            "INSERT INTO universo_migraciones (guild_id, nombre) VALUES (?, 'casino')", (guild_id,)
        )
