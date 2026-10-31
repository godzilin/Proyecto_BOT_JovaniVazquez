"""Persistencia del aspecto de Jovani (`aspecto`).

Tablas (en el mismo archivo SQLite que el resto del bot):

- `aspecto_state`: una fila por servidor con el estado de `AspectoState`.
- `aspecto_looks`: cambios de los administradores sobre los looks (nombre, bio y
  estado). Una fila con `name` es un look propio; sin `name`, un ajuste de uno de serie.
- `aspecto_totals`: totales acumulados por servidor y tipo, para las frases de datos.
- `aspecto_totals_day`: último día ya sumado en `aspecto_totals` de cada servidor.

Las imágenes de los looks no van en la base: `LookImageStore` las guarda en disco, en
`<base>/<guild_id>/<key>.<kind>.img`. Las reglas viven en `bot.services.aspecto`.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import fields
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite
from bot.services.aspecto import (
    MAX_CUSTOM_LOOKS,
    AspectoError,
    AspectoState,
    LookOverride,
    valid_key,
)

T = TypeVar("T")

_STATE_FIELDS = tuple(field.name for field in fields(AspectoState))
_GUILD_TABLES = ("aspecto_state", "aspecto_looks", "aspecto_totals", "aspecto_totals_day")


class AspectoRepository:
    """Acceso SQLite al aspecto de Jovani.

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
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS aspecto_state (
                    guild_id INTEGER PRIMARY KEY,
                    enabled INTEGER NOT NULL,
                    pinned_key TEXT,
                    pinned_until REAL,
                    dragon_until REAL NOT NULL,
                    updated_until REAL NOT NULL,
                    last_version TEXT,
                    applied_key TEXT,
                    applied_avatar TEXT,
                    profile_at REAL NOT NULL,
                    refreshed_day TEXT,
                    blocked_until REAL NOT NULL,
                    last_error TEXT
                );

                CREATE TABLE IF NOT EXISTS aspecto_looks (
                    guild_id INTEGER NOT NULL,
                    key TEXT NOT NULL,
                    name TEXT,
                    bio TEXT,
                    status TEXT,
                    PRIMARY KEY (guild_id, key)
                );

                CREATE TABLE IF NOT EXISTS aspecto_totals (
                    guild_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    total INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, kind)
                );

                CREATE TABLE IF NOT EXISTS aspecto_totals_day (
                    guild_id INTEGER PRIMARY KEY,
                    through_day TEXT NOT NULL
                );
                """
            )

    async def state(self, guild_id: int) -> AspectoState:
        """Estado del servidor, o el de serie si nunca se guardó."""
        return await self._run(self._state_sync, guild_id)

    def _state_sync(self, guild_id: int) -> AspectoState:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM aspecto_state WHERE guild_id = ?", (guild_id,)
            ).fetchone()
        if row is None:
            return AspectoState()
        values = {name: row[name] for name in _STATE_FIELDS}
        values["enabled"] = bool(values["enabled"])
        return AspectoState(**values)

    async def save_state(self, guild_id: int, state: AspectoState) -> None:
        """Guarda el estado completo del servidor."""
        await self._run(self._save_state_sync, guild_id, state)

    def _save_state_sync(self, guild_id: int, state: AspectoState) -> None:
        columns = ", ".join(_STATE_FIELDS)
        placeholders = ", ".join("?" for _ in _STATE_FIELDS)
        updates = ", ".join(f"{name} = excluded.{name}" for name in _STATE_FIELDS)
        values = [
            int(value) if isinstance(value, bool) else value
            for value in (getattr(state, name) for name in _STATE_FIELDS)
        ]
        with self._connect() as connection:
            connection.execute(
                f"INSERT INTO aspecto_state (guild_id, {columns}) VALUES (?, {placeholders})"
                f" ON CONFLICT (guild_id) DO UPDATE SET {updates}",
                (guild_id, *values),
            )

    async def overrides(self, guild_id: int) -> dict[str, LookOverride]:
        """Cambios de los administradores, por clave de look."""
        return await self._run(self._overrides_sync, guild_id)

    def _overrides_sync(self, guild_id: int) -> dict[str, LookOverride]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT key, name, bio, status FROM aspecto_looks WHERE guild_id = ?",
                (guild_id,),
            ).fetchall()
        return {
            str(row["key"]): LookOverride(str(row["key"]), row["name"], row["bio"], row["status"])
            for row in rows
        }

    async def create_look(self, guild_id: int, override: LookOverride) -> None:
        """Crea un look propio.

        Raises:
            AspectoError: Si la clave ya existe o el servidor tiene `MAX_CUSTOM_LOOKS`.
        """
        await self._run(self._create_look_sync, guild_id, override)

    def _create_look_sync(self, guild_id: int, override: LookOverride) -> None:
        with self._connect() as connection:
            # Cuenta e inserta en la misma transacción para que dos creaciones a la
            # vez no pasen las dos del tope.
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM aspecto_looks WHERE guild_id = ? AND key = ?",
                (guild_id, override.key),
            ).fetchone()
            if exists is not None:
                raise AspectoError("Ya existe un look con ese nombre.")
            (count,) = connection.execute(
                "SELECT COUNT(*) FROM aspecto_looks WHERE guild_id = ? AND name IS NOT NULL",
                (guild_id,),
            ).fetchone()
            if count >= MAX_CUSTOM_LOOKS:
                raise AspectoError(
                    f"Ya hay {MAX_CUSTOM_LOOKS} looks propios. Borra alguno antes de crear otro."
                )
            connection.execute(
                "INSERT INTO aspecto_looks (guild_id, key, name, bio, status)"
                " VALUES (?, ?, ?, ?, ?)",
                (guild_id, override.key, override.name, override.bio, override.status),
            )

    async def save_override(self, guild_id: int, override: LookOverride) -> None:
        """Guarda los cambios de un look; si no queda nada que guardar, borra la fila."""
        await self._run(self._save_override_sync, guild_id, override)

    def _save_override_sync(self, guild_id: int, override: LookOverride) -> None:
        with self._connect() as connection:
            if override.name is None and override.bio is None and override.status is None:
                connection.execute(
                    "DELETE FROM aspecto_looks WHERE guild_id = ? AND key = ?",
                    (guild_id, override.key),
                )
                return
            connection.execute(
                "INSERT INTO aspecto_looks (guild_id, key, name, bio, status)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT (guild_id, key) DO UPDATE SET"
                " name = excluded.name, bio = excluded.bio, status = excluded.status",
                (guild_id, override.key, override.name, override.bio, override.status),
            )

    async def delete_look(self, guild_id: int, key: str) -> None:
        """Borra los cambios o el look propio con esa clave."""
        await self._run(self._delete_look_sync, guild_id, key)

    def _delete_look_sync(self, guild_id: int, key: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM aspecto_looks WHERE guild_id = ? AND key = ?", (guild_id, key)
            )

    async def totals(self, guild_id: int) -> tuple[dict[str, int], str | None]:
        """Totales por tipo y último día sumado (`None` si nunca se acumuló)."""
        return await self._run(self._totals_sync, guild_id)

    def _totals_sync(self, guild_id: int) -> tuple[dict[str, int], str | None]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT kind, total FROM aspecto_totals WHERE guild_id = ?", (guild_id,)
            ).fetchall()
            day = connection.execute(
                "SELECT through_day FROM aspecto_totals_day WHERE guild_id = ?", (guild_id,)
            ).fetchone()
        totals = {str(row["kind"]): int(row["total"]) for row in rows}
        return totals, (str(day["through_day"]) if day is not None else None)

    async def add_totals(self, guild_id: int, through_day: str, deltas: Mapping[str, int]) -> None:
        """Suma `deltas` a los totales y fija `through_day`.

        Si `through_day` no es posterior al guardado no hace nada, para que un día no
        se sume dos veces.
        """
        await self._run(self._add_totals_sync, guild_id, through_day, dict(deltas))

    def _add_totals_sync(self, guild_id: int, through_day: str, deltas: dict[str, int]) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            saved = connection.execute(
                "SELECT through_day FROM aspecto_totals_day WHERE guild_id = ?", (guild_id,)
            ).fetchone()
            if saved is not None and through_day <= str(saved["through_day"]):
                return
            for kind, amount in deltas.items():
                connection.execute(
                    "INSERT INTO aspecto_totals (guild_id, kind, total) VALUES (?, ?, ?)"
                    " ON CONFLICT (guild_id, kind) DO UPDATE SET total = total + excluded.total",
                    (guild_id, kind, amount),
                )
            connection.execute(
                "INSERT INTO aspecto_totals_day (guild_id, through_day) VALUES (?, ?)"
                " ON CONFLICT (guild_id) DO UPDATE SET through_day = excluded.through_day",
                (guild_id, through_day),
            )

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra todo lo del servidor en las tablas de aspecto."""
        await self._run(self._delete_guild_data_sync, guild_id)

    def _delete_guild_data_sync(self, guild_id: int) -> None:
        with self._connect() as connection:
            for table in _GUILD_TABLES:
                connection.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))


#: Tipos de imagen que tiene un look.
IMAGE_KINDS = ("avatar", "banner")


class LookImageStore:
    """Almacén en disco de los avatares y banners de los looks.

    Los archivos están en `<base>/<guild_id>/<key>.<kind>.img` (PNG o GIF según el
    contenido, por eso la extensión no dice el formato). Toda la E/S sale del
    event loop con `asyncio.to_thread`. La clave acaba en un nombre de archivo, así
    que se valida con `valid_key` antes de construir ninguna ruta.

    Args:
        base: Carpeta persistente (en Docker, dentro del volumen `.data`).
    """

    def __init__(self, base: Path) -> None:
        self.base = base

    def _directory(self, guild_id: int) -> Path:
        return self.base / str(int(guild_id))

    @staticmethod
    def _check(key: str, kind: str | None = None) -> None:
        if not valid_key(key):
            raise ValueError(f"Clave de look no válida: {key!r}")
        if kind is not None and kind not in IMAGE_KINDS:
            raise ValueError(f"Tipo de imagen no válido: {kind!r}")

    def _path(self, guild_id: int, key: str, kind: str) -> Path:
        self._check(key, kind)
        return self._directory(guild_id) / f"{key}.{kind}.img"

    async def save(self, guild_id: int, key: str, kind: str, data: bytes) -> None:
        """Guarda la imagen de forma atómica (archivo temporal y `os.replace`)."""
        path = self._path(guild_id, key, kind)
        await asyncio.to_thread(self._save_sync, path, data)

    @staticmethod
    def _save_sync(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".img")
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    async def load(self, guild_id: int, key: str, kind: str) -> bytes | None:
        """Devuelve la imagen, o `None` si no existe."""
        path = self._path(guild_id, key, kind)

        def _read() -> bytes | None:
            try:
                return path.read_bytes()
            except FileNotFoundError:
                return None

        return await asyncio.to_thread(_read)

    async def delete(self, guild_id: int, key: str, kind: str) -> None:
        """Borra la imagen; no falla si no existe."""
        path = self._path(guild_id, key, kind)
        await asyncio.to_thread(path.unlink, True)

    async def kinds(self, guild_id: int, key: str) -> set[str]:
        """Tipos de imagen que tiene guardados el look."""
        paths = {kind: self._path(guild_id, key, kind) for kind in IMAGE_KINDS}
        return await asyncio.to_thread(
            lambda: {kind for kind, path in paths.items() if path.is_file()}
        )

    async def delete_look(self, guild_id: int, key: str) -> None:
        """Borra todas las imágenes del look."""
        paths = [self._path(guild_id, key, kind) for kind in IMAGE_KINDS]

        def _delete() -> None:
            for path in paths:
                path.unlink(missing_ok=True)

        await asyncio.to_thread(_delete)

    async def delete_guild(self, guild_id: int) -> None:
        """Borra todas las imágenes del servidor."""
        directory = self._directory(guild_id)
        await asyncio.to_thread(shutil.rmtree, directory, True)
