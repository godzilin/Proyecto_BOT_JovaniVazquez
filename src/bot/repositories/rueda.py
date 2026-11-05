"""Persistencia de la Rueda: la vuelta de cada día de cada miembro, sorteada antes de girar.

Tabla (en el mismo archivo SQLite que el resto del bot):

- `rueda_spins`: una fila por vuelta, con la clave `(servidor, miembro, día, vuelta)`.
  `spin` es 0 para la vuelta del día y 1, 2… para las que da «otra vuelta». La fila
  se crea al abrir el panel con lo que ha tocado (`wedge`, `prize`, `stop`, `seed` e
  `item`), sin `claimed_at`; girar la marca (`claim`). La clave primaria hace que no
  pueda haber dos vueltas iguales aunque dos paneles se abran a la vez: `create`
  inserta solo si no existe y devuelve la que quedó guardada.

El dinero y los objetos no están aquí: los paga la economía y los guarda la tienda
(ver `bot.cogs.rueda`). Las reglas, en `bot.services.rueda`.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite
from bot.services.rueda import Draw

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class SpinRow:
    """Una vuelta guardada: lo que tocó y si ya se ha girado."""

    day: date
    spin: int
    draw: Draw
    created_at: float
    claimed_at: float | None

    @property
    def claimed(self) -> bool:
        return self.claimed_at is not None


def _row(row: sqlite3.Row) -> SpinRow:
    return SpinRow(
        day=date.fromisoformat(row["day"]),
        spin=int(row["spin"]),
        draw=Draw(
            wedge=int(row["wedge"]),
            prize=row["prize"],
            stop=float(row["stop"]),
            seed=int(row["seed"]),
            item=row["item"],
        ),
        created_at=float(row["created_at"]),
        claimed_at=None if row["claimed_at"] is None else float(row["claimed_at"]),
    )


class RuedaRepository:
    """Acceso SQLite a las vueltas de la Rueda.

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
                CREATE TABLE IF NOT EXISTS rueda_spins (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    spin INTEGER NOT NULL,
                    wedge INTEGER NOT NULL,
                    prize TEXT NOT NULL,
                    stop REAL NOT NULL,
                    seed INTEGER NOT NULL,
                    item TEXT,
                    created_at REAL NOT NULL,
                    claimed_at REAL,
                    PRIMARY KEY (guild_id, user_id, day, spin)
                )
                """
            )

    async def day(self, guild_id: int, user_id: int, day: date) -> list[SpinRow]:
        """Las vueltas de un miembro en un día, de la primera a la última."""
        return await self._run(self._day_sync, guild_id, user_id, day.isoformat())

    def _day_sync(self, guild_id: int, user_id: int, day: str) -> list[SpinRow]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM rueda_spins
                WHERE guild_id = ? AND user_id = ? AND day = ?
                ORDER BY spin
                """,
                (guild_id, user_id, day),
            ).fetchall()
        return [_row(r) for r in rows]

    async def create(
        self, guild_id: int, user_id: int, day: date, spin: int, draw: Draw, now: float
    ) -> SpinRow:
        """Guarda la vuelta `spin` del día con lo sorteado, si no estaba ya.

        Returns:
            La vuelta que queda guardada: la nueva o, si otro panel se adelantó, la suya.
        """
        return await self._run(
            self._create_sync, guild_id, user_id, day.isoformat(), spin, draw, now
        )

    def _create_sync(
        self, guild_id: int, user_id: int, day: str, spin: int, draw: Draw, now: float
    ) -> SpinRow:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO rueda_spins
                    (guild_id, user_id, day, spin, wedge, prize, stop, seed, item, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id, user_id, day, spin, draw.wedge, draw.prize, draw.stop, draw.seed,
                    draw.item, now,
                ),
            )  # fmt: skip
            row = connection.execute(
                """
                SELECT * FROM rueda_spins
                WHERE guild_id = ? AND user_id = ? AND day = ? AND spin = ?
                """,
                (guild_id, user_id, day, spin),
            ).fetchone()
        return _row(row)

    async def claim(self, guild_id: int, user_id: int, day: date, spin: int, now: float) -> bool:
        """Marca la vuelta como girada; `False` si ya lo estaba (otro clic se adelantó)."""
        return await self._run(self._claim_sync, guild_id, user_id, day.isoformat(), spin, now)

    def _claim_sync(self, guild_id: int, user_id: int, day: str, spin: int, now: float) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE rueda_spins SET claimed_at = ?
                WHERE guild_id = ? AND user_id = ? AND day = ? AND spin = ?
                  AND claimed_at IS NULL
                """,
                (now, guild_id, user_id, day, spin),
            )
        return cursor.rowcount == 1

    async def unclaim(self, guild_id: int, user_id: int, day: date, spin: int) -> None:
        """Deshace `claim` si el premio no se pudo dar: la vuelta queda por girar."""
        await self._run(self._unclaim_sync, guild_id, user_id, day.isoformat(), spin)

    def _unclaim_sync(self, guild_id: int, user_id: int, day: str, spin: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE rueda_spins SET claimed_at = NULL
                WHERE guild_id = ? AND user_id = ? AND day = ? AND spin = ?
                """,
                (guild_id, user_id, day, spin),
            )

    async def set_item(
        self, guild_id: int, user_id: int, day: date, spin: int, prize: str, item: str | None
    ) -> None:
        """Cambia lo que da una vuelta ya girada (el expositor agotado pasa a dinero)."""
        await self._run(self._set_item_sync, guild_id, user_id, day.isoformat(), spin, prize, item)

    def _set_item_sync(
        self, guild_id: int, user_id: int, day: str, spin: int, prize: str, item: str | None
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE rueda_spins SET prize = ?, item = ?
                WHERE guild_id = ? AND user_id = ? AND day = ? AND spin = ?
                """,
                (prize, item, guild_id, user_id, day, spin),
            )

    async def spin_days(self, guild_id: int, user_id: int) -> list[date]:
        """Días distintos en los que el miembro ha girado (para la racha)."""
        return await self._run(self._spin_days_sync, guild_id, user_id)

    def _spin_days_sync(self, guild_id: int, user_id: int) -> list[date]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT day FROM rueda_spins
                WHERE guild_id = ? AND user_id = ? AND claimed_at IS NOT NULL
                """,
                (guild_id, user_id),
            ).fetchall()
        return [date.fromisoformat(r["day"]) for r in rows]

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra las vueltas de un servidor (cuando el bot sale de él)."""
        await self._run(self._delete_guild_data_sync, guild_id)

    def _delete_guild_data_sync(self, guild_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM rueda_spins WHERE guild_id = ?", (guild_id,))
