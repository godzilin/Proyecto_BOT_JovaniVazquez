"""Persistencia de los ajustes del aviso de novedades (`cambios`).

Tabla `news_settings` (en el mismo archivo SQLite que el resto del bot): si
cada servidor recibe el aviso, en qué canal y con cuánto detalle. Sin fila,
se usa lo de por defecto: aviso detallado en `#chat-general`. Solo guarda
IDs; se borra cuando el bot sale del servidor.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class NewsSettings:
    """Ajustes del aviso de novedades de un servidor.

    Attributes:
        enabled: Si se publica el aviso tras cada despliegue.
        channel_id: Canal del aviso; `None` usa `#chat-general` o el del sistema.
        detailed: Con la descripción de cada PR (`True`) o solo la lista de títulos.
    """

    enabled: bool = True
    channel_id: int | None = None
    detailed: bool = True


class NewsRepository:
    """Acceso SQLite a los ajustes del aviso de novedades.

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
                CREATE TABLE IF NOT EXISTS news_settings (
                    guild_id INTEGER PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    channel_id INTEGER,
                    detailed INTEGER NOT NULL DEFAULT 1
                )
                """
            )

    async def settings(self, guild_id: int) -> NewsSettings:
        """Ajustes del servidor; los de por defecto si no hay ninguno guardado."""
        return await self._run(self._settings_sync, guild_id)

    def _settings_sync(self, guild_id: int) -> NewsSettings:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT enabled, channel_id, detailed FROM news_settings WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()
        if row is None:
            return NewsSettings()
        return NewsSettings(
            enabled=bool(row["enabled"]),
            channel_id=row["channel_id"],
            detailed=bool(row["detailed"]),
        )

    async def save_settings(self, guild_id: int, settings: NewsSettings) -> None:
        """Guarda los ajustes completos del servidor (sustituye a los anteriores)."""
        await self._run(self._save_settings_sync, guild_id, settings)

    def _save_settings_sync(self, guild_id: int, settings: NewsSettings) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO news_settings (guild_id, enabled, channel_id, detailed)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE
                SET enabled = excluded.enabled, channel_id = excluded.channel_id,
                    detailed = excluded.detailed
                """,
                (guild_id, int(settings.enabled), settings.channel_id, int(settings.detailed)),
            )

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra los ajustes del servidor (el bot ha salido de él)."""
        await self._run(self._delete_sync, guild_id)

    def _delete_sync(self, guild_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM news_settings WHERE guild_id = ?", (guild_id,))
