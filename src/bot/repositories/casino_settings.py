"""Persistencia de los canales del casino.

Tabla (en el mismo archivo SQLite que el resto del bot):

- `casino_settings`: canales donde se permite jugar al casino en cada servidor,
  como JSON con la lista ordenada de IDs. Una lista vacía significa «cualquier
  canal»; sin fila, el servidor usa el valor por defecto de la configuración.

Solo se guardan IDs. Las reglas viven en `bot.services.casino_channels`.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite

T = TypeVar("T")


class CasinoSettingsRepository:
    """Acceso SQLite a los canales permitidos del casino.

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
        """Crea la tabla si no existe."""
        await self._run(self._initialize_sync)

    def _initialize_sync(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS casino_settings (
                    guild_id INTEGER PRIMARY KEY,
                    channel_ids TEXT NOT NULL
                )
                """
            )

    async def all_channels(self) -> dict[int, frozenset[int]]:
        """Canales guardados de cada servidor (vacío = cualquier canal)."""
        return await self._run(self._all_channels_sync)

    def _all_channels_sync(self) -> dict[int, frozenset[int]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT guild_id, channel_ids FROM casino_settings"
            ).fetchall()
        finally:
            connection.close()
        return {
            int(row["guild_id"]): frozenset(int(c) for c in json.loads(row["channel_ids"]))
            for row in rows
        }

    async def set_channels(self, guild_id: int, channel_ids: frozenset[int]) -> None:
        """Guarda los canales permitidos de un servidor (vacío = cualquiera)."""
        await self._run(self._set_channels_sync, guild_id, channel_ids)

    def _set_channels_sync(self, guild_id: int, channel_ids: frozenset[int]) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """
                INSERT INTO casino_settings (guild_id, channel_ids) VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET channel_ids = excluded.channel_ids
                """,
                    (guild_id, json.dumps(sorted(channel_ids))),
                )
        finally:
            connection.close()

    async def forget(self, guild_id: int) -> None:
        """Borra la fila de un servidor: vuelve al valor por defecto."""
        await self._run(self._forget_sync, guild_id)

    def _forget_sync(self, guild_id: int) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute("DELETE FROM casino_settings WHERE guild_id = ?", (guild_id,))
        finally:
            connection.close()
