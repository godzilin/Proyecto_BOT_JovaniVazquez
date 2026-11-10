"""Estado de los interiores del Jovaniverso: lo que cambia jugando dentro, por lugar.

Nunca va en el documento del mundo (Biblia, «Jovaniverso: rendimiento imperativo»): el mundo
lo publican los editores y esto lo cambian los miembros. Una sola tabla:

- `universo_interior_estado (guild_id, lugar, n, estado, user_id, updated_at)`: el estado de
  cada usable (`n`, su número en el interior) de cada lugar (`<interior>` o
  `<interior>@<dueño>`, con `pruebas/` delante en el extraplano; ver
  `bot.services.universo_interiores.place_key`), quién lo cambió por última vez y cuándo (en
  segundos Unix: lo de reparar vuelve a romperse con el tiempo). Sin fila, el estado de serie.

Un lugar tiene como mucho `MAX_USABLES` filas (los números de sus usables), así que la tabla
crece con los pisos de los miembros y no con el uso.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from bot.repositories import sqlite


class UniversoInterioresRepository:
    """Acceso SQLite al estado de los interiores.

    Args:
        database_path: Ruta del archivo SQLite persistente.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    async def initialize(self) -> None:
        """Crea la tabla si no existe."""
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        connection = sqlite.connect(self.database_path)
        try:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS universo_interior_estado (
                        guild_id INTEGER NOT NULL,
                        lugar TEXT NOT NULL,
                        n INTEGER NOT NULL,
                        estado INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        updated_at REAL NOT NULL,
                        PRIMARY KEY (guild_id, lugar, n)
                    )
                    """
                )
        finally:
            connection.close()

    async def states(self, guild_id: int, lugar: str) -> dict[int, tuple[int, float]]:
        """El estado guardado de cada usable de un lugar: número → `(estado, cuándo)`."""
        return await asyncio.to_thread(self._states_sync, guild_id, lugar)

    def _states_sync(self, guild_id: int, lugar: str) -> dict[int, tuple[int, float]]:
        connection = sqlite.connect(self.database_path)
        try:
            rows = connection.execute(
                "SELECT n, estado, updated_at FROM universo_interior_estado "
                "WHERE guild_id = ? AND lugar = ?",
                (guild_id, lugar),
            ).fetchall()
        finally:
            connection.close()
        return {int(r[0]): (int(r[1]), float(r[2])) for r in rows}

    async def set_state(
        self, guild_id: int, lugar: str, n: int, estado: int, user_id: int, at: float
    ) -> None:
        """Guarda el estado de un usable de un lugar."""
        await asyncio.to_thread(self._set_sync, guild_id, lugar, n, estado, user_id, at)

    def _set_sync(
        self, guild_id: int, lugar: str, n: int, estado: int, user_id: int, at: float
    ) -> None:
        connection = sqlite.connect(self.database_path)
        try:
            with connection:
                connection.execute(
                    "INSERT INTO universo_interior_estado "
                    "(guild_id, lugar, n, estado, user_id, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (guild_id, lugar, n) DO UPDATE SET estado = excluded.estado, "
                    "user_id = excluded.user_id, updated_at = excluded.updated_at",
                    (guild_id, lugar, n, estado, user_id, at),
                )
        finally:
            connection.close()
