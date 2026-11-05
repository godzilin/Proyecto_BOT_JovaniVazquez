"""Persistencia de `predice`: las traducciones de las preguntas de Polymarket.

Tabla (en el mismo archivo SQLite que el resto del bot):

- `predict_translations`: cada texto de Polymarket (en inglés) con su traducción al español
  (`source` es la clave) y cuándo se tradujo. Traducir es lento y tiene límite de
  consultas: así un reinicio no vuelve a pedir lo que ya se tradujo.

El dinero no está aquí: las predicciones viven en la economía (`economy_predict_bets`),
que es la que manda. Las preguntas y sus cuotas viven en memoria (`Predice.offers`) y se
rehacen cada vez que se piden a Polymarket. Las reglas, en `bot.services.predice`.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite

T = TypeVar("T")


class PredictRepository:
    """Acceso SQLite a las traducciones de `predice`.

    Args:
        database_path: Ruta del archivo SQLite persistente.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Conexión con transacción que se confirma al salir y se cierra siempre."""
        connection = sqlite.connect(self.database_path)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

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
                CREATE TABLE IF NOT EXISTS predict_translations (
                    source TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )

    async def translations(self) -> dict[str, str]:
        """Todas las traducciones guardadas, del texto original a su traducción."""
        return await self._run(self._translations_sync)

    def _translations_sync(self) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute("SELECT source, text FROM predict_translations").fetchall()
        return {row["source"]: row["text"] for row in rows}

    async def save_translation(self, source: str, text: str, *, now: float) -> None:
        """Guarda la traducción de un texto; si ya había una, la sustituye."""
        await self._run(self._save_translation_sync, source, text, now)

    def _save_translation_sync(self, source: str, text: str, now: float) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO predict_translations (source, text, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET text = excluded.text,
                    created_at = excluded.created_at
                """,
                (source, text, now),
            )
