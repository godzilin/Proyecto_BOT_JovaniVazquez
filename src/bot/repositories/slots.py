"""Persistencia de la tragaperras (`tragas`) que no es dinero.

Tablas (en el mismo archivo SQLite que el resto del bot):

- `slots_heat`: el calor de la máquina de cada miembro por servidor y cuándo
  se tocó por última vez. Antes vivía en memoria y se perdía al reiniciar el
  bot; guardando la hora, el enfriamiento se puede calcular al volver.
  También la barra de bonus (`bonus`, `bonus_stake`, `bonus_spins`): los
  puntos, la suma de las apuestas que la han llenado y cuántas tiradas. Se
  guarda en la misma fila y en la misma escritura que el calor. Cada máquina
  tiene los suyos (`machine`): la 0 es la de Discord (`tragas`) y las demás,
  las de la plaza del Jovaniverso (`bot.services.universo_tragas`).
- `slots_daily`: el giro diario gratis. Último día reclamado (`YYYY-MM-DD`,
  día local del servidor) y la racha de días seguidos.

El dinero (apuestas, premios y el bote común) no pasa por aquí: va por
`bot.services.economy`. Las reglas (cuánto calor, cuánto vale la racha)
viven en `bot.services.slots`; aquí solo se guarda y se lee.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite
from bot.services.slots import BonusMeter

T = TypeVar("T")


class SlotsRepository:
    """Acceso SQLite al calor y al giro diario de la tragaperras.

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
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS slots_heat (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    machine INTEGER NOT NULL DEFAULT 0,
                    heat INTEGER NOT NULL,
                    updated_at REAL NOT NULL,
                    bonus INTEGER NOT NULL DEFAULT 0,
                    bonus_stake INTEGER NOT NULL DEFAULT 0,
                    bonus_spins INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id, machine)
                );

                CREATE TABLE IF NOT EXISTS slots_daily (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    last_day TEXT NOT NULL,
                    streak INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );
                """
            )
            # Columnas de la barra de bonus, añadidas después de crear la tabla.
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(slots_heat)")}
            for column in ("bonus", "bonus_stake", "bonus_spins"):
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE slots_heat ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
                    )
            if "machine" not in columns:
                # La clave primaria no cambia con ALTER: se rehace la tabla y lo de
                # antes queda como la máquina 0 (la de Discord).
                connection.executescript(
                    """
                    ALTER TABLE slots_heat RENAME TO slots_heat_old;
                    CREATE TABLE slots_heat (
                        guild_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        machine INTEGER NOT NULL DEFAULT 0,
                        heat INTEGER NOT NULL,
                        updated_at REAL NOT NULL,
                        bonus INTEGER NOT NULL DEFAULT 0,
                        bonus_stake INTEGER NOT NULL DEFAULT 0,
                        bonus_spins INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (guild_id, user_id, machine)
                    );
                    INSERT INTO slots_heat
                        (guild_id, user_id, machine, heat, updated_at,
                         bonus, bonus_stake, bonus_spins)
                    SELECT guild_id, user_id, 0, heat, updated_at, bonus, bonus_stake, bonus_spins
                    FROM slots_heat_old;
                    DROP TABLE slots_heat_old;
                    """
                )

    # -- Calor -----------------------------------------------------------------------

    async def load_heat(
        self, guild_id: int, user_id: int, machine: int = 0
    ) -> tuple[int, float] | None:
        """Calor guardado de una máquina del miembro y cuándo se guardó (`None` si nunca)."""
        return await self._run(self._load_heat_sync, guild_id, user_id, machine)

    def _load_heat_sync(
        self, guild_id: int, user_id: int, machine: int
    ) -> tuple[int, float] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT heat, updated_at FROM slots_heat
                WHERE guild_id = ? AND user_id = ? AND machine = ?
                """,
                (guild_id, user_id, machine),
            ).fetchone()
        if row is None:
            return None
        return int(row["heat"]), float(row["updated_at"])

    async def save_heat(
        self,
        guild_id: int,
        user_id: int,
        heat: int,
        now: float,
        bonus: BonusMeter | None = None,
        machine: int = 0,
    ) -> None:
        """Guarda el calor de una máquina del miembro con la hora `now` (epoch).

        Args:
            bonus: Si se pasa, guarda también la barra de bonus en la misma
                escritura; si no, la deja como estaba.
            machine: 0 la de Discord; las demás, las de la plaza del Jovaniverso.
        """
        await self._run(self._save_heat_sync, guild_id, user_id, heat, now, bonus, machine)

    def _save_heat_sync(
        self,
        guild_id: int,
        user_id: int,
        heat: int,
        now: float,
        bonus: BonusMeter | None,
        machine: int,
    ) -> None:
        with closing(self._connect()) as connection, connection:
            if bonus is None:
                connection.execute(
                    """
                    INSERT INTO slots_heat (guild_id, user_id, machine, heat, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (guild_id, user_id, machine) DO UPDATE SET
                        heat = excluded.heat, updated_at = excluded.updated_at
                    """,
                    (guild_id, user_id, machine, heat, now),
                )
                return
            connection.execute(
                """
                INSERT INTO slots_heat
                    (guild_id, user_id, machine, heat, updated_at,
                     bonus, bonus_stake, bonus_spins)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (guild_id, user_id, machine) DO UPDATE SET
                    heat = excluded.heat, updated_at = excluded.updated_at,
                    bonus = excluded.bonus, bonus_stake = excluded.bonus_stake,
                    bonus_spins = excluded.bonus_spins
                """,
                (
                    guild_id,
                    user_id,
                    machine,
                    heat,
                    now,
                    bonus.points,
                    bonus.stake_sum,
                    bonus.spins,
                ),
            )

    async def load_bonus(self, guild_id: int, user_id: int, machine: int = 0) -> BonusMeter:
        """Barra de bonus guardada de una máquina del miembro (vacía si nunca ha jugado)."""
        return await self._run(self._load_bonus_sync, guild_id, user_id, machine)

    def _load_bonus_sync(self, guild_id: int, user_id: int, machine: int) -> BonusMeter:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT bonus, bonus_stake, bonus_spins FROM slots_heat
                WHERE guild_id = ? AND user_id = ? AND machine = ?
                """,
                (guild_id, user_id, machine),
            ).fetchone()
        if row is None:
            return BonusMeter()
        return BonusMeter(int(row["bonus"]), int(row["bonus_stake"]), int(row["bonus_spins"]))

    # -- Giro diario -----------------------------------------------------------------

    async def daily(self, guild_id: int, user_id: int) -> tuple[str, int] | None:
        """Último día reclamado y racha del miembro (`None` si nunca ha reclamado)."""
        return await self._run(self._daily_sync, guild_id, user_id)

    def _daily_sync(self, guild_id: int, user_id: int) -> tuple[str, int] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT last_day, streak FROM slots_daily WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
        if row is None:
            return None
        return str(row["last_day"]), int(row["streak"])

    async def claim_daily(self, guild_id: int, user_id: int, *, today: str, streak: int) -> bool:
        """Apunta el giro diario de `today` con su racha, una sola vez por día.

        Es atómico: la comprobación y la escritura son la misma sentencia, así
        que dos clics a la vez no cobran dos giros. La racha la calcula quien
        llama (`bot.services.slots`) a partir de `daily`.

        Args:
            today: Día local del servidor, `YYYY-MM-DD`.
            streak: Racha que queda tras reclamar hoy.

        Returns:
            True si se ha apuntado; False si ya estaba reclamado hoy (no se
            toca nada).
        """
        return await self._run(self._claim_daily_sync, guild_id, user_id, today, streak)

    def _claim_daily_sync(self, guild_id: int, user_id: int, today: str, streak: int) -> bool:
        with closing(self._connect()) as connection, connection:
            # El `WHERE` del upsert deja la fila como está si ya es de hoy y
            # entonces no cuenta como cambio: `rowcount` dice si se ha cobrado.
            cursor = connection.execute(
                """
                INSERT INTO slots_daily (guild_id, user_id, last_day, streak)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (guild_id, user_id) DO UPDATE SET
                    last_day = excluded.last_day, streak = excluded.streak
                WHERE slots_daily.last_day <> excluded.last_day
                """,
                (guild_id, user_id, today, streak),
            )
            return cursor.rowcount == 1

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra todo lo guardado de un servidor (al salir el bot de él)."""
        await self._run(self._delete_guild_sync, guild_id)

    def _delete_guild_sync(self, guild_id: int) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM slots_heat WHERE guild_id = ?", (guild_id,))
            connection.execute("DELETE FROM slots_daily WHERE guild_id = ?", (guild_id,))
