"""Persistencia del juego del pico (`picar`): cada tirada con su semilla.

Tabla (en el mismo archivo SQLite que el resto del bot):

- `picar_plays`: una fila por tirada, con el pico, la semilla de la biblioteca, el hash
  de la calibración con la que se jugó, lo que costó y pagó, el multiplicador, la fila
  más honda a la que llegó y si mató al dragón.
- `picar_days`: por miembro, el último día (local) en que jugó y cuántos días seguidos
  lleva (los logros de racha).

Se guarda antes de dibujar (el dinero ya se ha movido): si el bot se cae a mitad del GIF,
la tirada está apuntada. De aquí salen el récord de profundidad de cada miembro con cada
pico y el ranking del servidor, contando solo las tiradas de la calibración que se pida
(con otra física o con otro mundo, las capas no son las mismas), y la reproducción de una
tirada para auditarla (la semilla y el hash bastan para volver a jugarla igual,
`bot.services.picar.play`). Las reglas viven en
`bot.services.picar`; el dinero, en la economía.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class PlayRecord:
    """Una tirada guardada."""

    id: int
    guild_id: int
    user_id: int
    pick: str
    seed: int
    params: str
    cost: int
    payout: int
    units: int
    multiplier: int
    max_row: int
    dragon: bool
    created_at: float


@dataclass(frozen=True, slots=True)
class Saved:
    """Lo que devuelve apuntar una tirada."""

    id: int
    #: Récord de profundidad del miembro con ese pico antes de esta tirada.
    best: int | None
    #: Récord del servidor con ese pico antes de esta tirada.
    server_best: int | None
    #: Días seguidos que lleva jugando, contando hoy.
    streak: int


class PicarRepository:
    """Acceso SQLite a las tiradas de `picar`.

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
        """Crea la tabla y sus índices si no existen."""
        await self._run(self._initialize_sync)

    def _initialize_sync(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS picar_plays (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    pick TEXT NOT NULL,
                    seed INTEGER NOT NULL,
                    params TEXT NOT NULL,
                    cost INTEGER NOT NULL,
                    payout INTEGER NOT NULL,
                    units INTEGER NOT NULL,
                    multiplier INTEGER NOT NULL,
                    max_row INTEGER NOT NULL,
                    dragon INTEGER NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS picar_plays_member
                    ON picar_plays (guild_id, user_id, pick, max_row);
                CREATE INDEX IF NOT EXISTS picar_plays_depth
                    ON picar_plays (guild_id, pick, max_row);
                CREATE TABLE IF NOT EXISTS picar_days (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    last_day TEXT NOT NULL,
                    streak INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );
                """
            )

    async def record(
        self,
        guild_id: int,
        user_id: int,
        *,
        pick: str,
        seed: int,
        params: str,
        cost: int,
        payout: int,
        units: int,
        multiplier: int,
        max_row: int,
        dragon: bool,
        day: date,
    ) -> Saved:
        """Apunta una tirada (jugada el día local `day`) y devuelve su número y récords."""
        return await self._run(
            self._record_sync,
            (guild_id, user_id, pick, seed, params, cost, payout, units, multiplier, max_row,
             int(dragon), time.time()),
            day,
        )  # fmt: skip

    def _record_sync(self, values: tuple[object, ...], day: date) -> Saved:
        guild_id, user_id, pick, _seed, params = values[:5]
        with self._connect() as connection:
            best, server_best = connection.execute(
                """
                SELECT MAX(CASE WHEN user_id = ? THEN max_row END), MAX(max_row)
                FROM picar_plays WHERE guild_id = ? AND pick = ? AND params = ?
                """,
                (user_id, guild_id, pick, params),
            ).fetchone()
            row = connection.execute(
                "SELECT last_day, streak FROM picar_days WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            streak = 1
            if row is not None:
                last = date.fromisoformat(str(row["last_day"]))
                if last == day:
                    streak = int(row["streak"])
                elif last == day - timedelta(days=1):
                    streak = int(row["streak"]) + 1
            connection.execute(
                """
                INSERT INTO picar_days (guild_id, user_id, last_day, streak) VALUES (?, ?, ?, ?)
                ON CONFLICT (guild_id, user_id)
                DO UPDATE SET last_day = excluded.last_day, streak = excluded.streak
                """,
                (guild_id, user_id, day.isoformat(), streak),
            )
            cursor = connection.execute(
                """
                INSERT INTO picar_plays
                    (guild_id, user_id, pick, seed, params, cost, payout, units, multiplier,
                     max_row, dragon, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            return Saved(
                int(cursor.lastrowid or 0),
                None if best is None else int(best),
                None if server_best is None else int(server_best),
                streak,
            )

    async def get(self, guild_id: int, play_id: int) -> PlayRecord | None:
        """Una tirada del servidor por su número; `None` si no existe."""
        return await self._run(self._get_sync, guild_id, play_id)

    def _get_sync(self, guild_id: int, play_id: int) -> PlayRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM picar_plays WHERE guild_id = ? AND id = ?", (guild_id, play_id)
            ).fetchone()
        if row is None:
            return None
        return PlayRecord(
            id=int(row["id"]),
            guild_id=int(row["guild_id"]),
            user_id=int(row["user_id"]),
            pick=str(row["pick"]),
            seed=int(row["seed"]),
            params=str(row["params"]),
            cost=int(row["cost"]),
            payout=int(row["payout"]),
            units=int(row["units"]),
            multiplier=int(row["multiplier"]),
            max_row=int(row["max_row"]),
            dragon=bool(row["dragon"]),
            created_at=float(row["created_at"]),
        )

    async def records(self, guild_id: int, user_id: int, params: str) -> dict[str, int]:
        """Fila más honda a la que ha llegado un miembro con cada pico que ha usado, con la
        calibración `params`."""
        return await self._run(self._records_sync, guild_id, user_id, params)

    def _records_sync(self, guild_id: int, user_id: int, params: str) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT pick, MAX(max_row) AS best FROM picar_plays
                WHERE guild_id = ? AND user_id = ? AND params = ? GROUP BY pick
                """,
                (guild_id, user_id, params),
            ).fetchall()
        return {str(row["pick"]): int(row["best"]) for row in rows}

    async def ranking(
        self, guild_id: int, pick: str, params: str, limit: int = 5
    ) -> list[tuple[int, int]]:
        """Los miembros que más hondo han llegado con un pico y la calibración `params`:
        (miembro, fila)."""
        return await self._run(self._ranking_sync, guild_id, pick, params, limit)

    def _ranking_sync(
        self, guild_id: int, pick: str, params: str, limit: int
    ) -> list[tuple[int, int]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT user_id, MAX(max_row) AS best FROM picar_plays
                WHERE guild_id = ? AND pick = ? AND params = ? GROUP BY user_id
                ORDER BY best DESC, MIN(id) LIMIT ?
                """,
                (guild_id, pick, params, limit),
            ).fetchall()
        return [(int(row["user_id"]), int(row["best"])) for row in rows]

    async def totals(self, guild_id: int) -> dict[str, tuple[int, int, int]]:
        """Por pico: tiradas, apostado y pagado en el servidor (el RTP real acumulado)."""
        return await self._run(self._totals_sync, guild_id)

    def _totals_sync(self, guild_id: int) -> dict[str, tuple[int, int, int]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT pick, COUNT(*) AS plays, SUM(cost) AS staked, SUM(payout) AS paid
                FROM picar_plays WHERE guild_id = ? GROUP BY pick
                """,
                (guild_id,),
            ).fetchall()
        return {
            str(row["pick"]): (int(row["plays"]), int(row["staked"]), int(row["paid"]))
            for row in rows
        }

    async def dragons(self, guild_id: int) -> int:
        """Dragones matados en el servidor."""
        return await self._run(self._dragons_sync, guild_id)

    def _dragons_sync(self, guild_id: int) -> int:
        with self._connect() as connection:
            (count,) = connection.execute(
                "SELECT COUNT(*) FROM picar_plays WHERE guild_id = ? AND dragon = 1", (guild_id,)
            ).fetchone()
        return int(count)

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra todo lo guardado de un servidor (al salir el bot de él)."""
        await self._run(self._delete_guild_sync, guild_id)

    def _delete_guild_sync(self, guild_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM picar_plays WHERE guild_id = ?", (guild_id,))
            connection.execute("DELETE FROM picar_days WHERE guild_id = ?", (guild_id,))
