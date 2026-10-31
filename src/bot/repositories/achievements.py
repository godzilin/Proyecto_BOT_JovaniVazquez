"""Persistencia de los logros: estadísticas por miembro y logros desbloqueados.

Dos tablas en la base de datos del bot:

- `achievement_stats`: un contador por servidor, miembro y estadística.
- `achievement_unlocks`: qué logros tiene cada miembro y desde cuándo.

No se guarda el contenido de ningún mensaje, solo números. Las reglas (qué
logros existen y cuándo se cumplen) viven en `bot.services.achievements`;
aquí se reciben como una función para evaluarlas dentro de la transacción.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite
from bot.services.achievements import StatDelta

T = TypeVar("T")

#: Recibe las estadísticas del miembro tras el cambio y los logros que ya
#: tiene; devuelve los ids de los que acaba de conseguir.
Evaluator = Callable[[dict[str, int], frozenset[str]], list[str]]


@dataclass(frozen=True, slots=True)
class Profile:
    """Estadísticas y logros de un miembro.

    Attributes:
        unlocked: Id de cada logro conseguido y el instante (epoch) en que se consiguió.
    """

    stats: dict[str, int]
    unlocked: dict[str, float]


class AchievementRepository:
    """Lee y escribe los logros en SQLite, fuera del event loop.

    Args:
        database_path: Archivo SQLite compartido con el resto del bot.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        return sqlite.connect(self.database_path)

    async def _run(self, operation: Callable[..., T], *args: object) -> T:
        return await asyncio.to_thread(operation, *args)

    async def initialize(self) -> None:
        """Crea las tablas si no existen."""
        await self._run(self._initialize_sync)

    def _initialize_sync(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS achievement_stats (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    stat TEXT NOT NULL,
                    value INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id, stat)
                );

                CREATE TABLE IF NOT EXISTS achievement_unlocks (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    achievement_id TEXT NOT NULL,
                    unlocked_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, user_id, achievement_id)
                );
                """
            )

    async def record(
        self,
        guild_id: int,
        updates: Mapping[int, StatDelta],
        evaluate: Evaluator,
        *,
        now: float,
    ) -> dict[int, list[str]]:
        """Aplica los cambios de varios miembros y desbloquea lo que toque.

        Todo va en una sola transacción: los contadores, la evaluación y los
        desbloqueos. Así dos eventos simultáneos no pueden dar el mismo logro
        dos veces, y una escritura por minuto basta para todo un servidor.

        Returns:
            Por miembro, los ids recién desbloqueados (solo miembros con alguno).
        """
        return await self._run(self._record_sync, guild_id, dict(updates), evaluate, now)

    def _record_sync(
        self,
        guild_id: int,
        updates: dict[int, StatDelta],
        evaluate: Evaluator,
        now: float,
    ) -> dict[int, list[str]]:
        unlocked_by_user: dict[int, list[str]] = {}
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for user_id, delta in updates.items():
                if not delta:
                    continue
                connection.executemany(
                    """
                    INSERT INTO achievement_stats (guild_id, user_id, stat, value)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(guild_id, user_id, stat) DO UPDATE SET
                        value = value + excluded.value
                    """,
                    [(guild_id, user_id, stat, v) for stat, v in delta.add.items() if v],
                )
                connection.executemany(
                    """
                    INSERT INTO achievement_stats (guild_id, user_id, stat, value)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(guild_id, user_id, stat) DO UPDATE SET
                        value = MAX(value, excluded.value)
                    """,
                    [(guild_id, user_id, stat, v) for stat, v in delta.peak.items()],
                )
                stats = self._stats(connection, guild_id, user_id)
                have = self._unlocked_ids(connection, guild_id, user_id)
                new = [i for i in evaluate(stats, frozenset(have)) if i not in have]
                if new:
                    connection.executemany(
                        """
                        INSERT OR IGNORE INTO achievement_unlocks
                            (guild_id, user_id, achievement_id, unlocked_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        [(guild_id, user_id, achievement_id, now) for achievement_id in new],
                    )
                    unlocked_by_user[user_id] = new
            connection.commit()
        return unlocked_by_user

    @staticmethod
    def _stats(connection: sqlite3.Connection, guild_id: int, user_id: int) -> dict[str, int]:
        rows = connection.execute(
            "SELECT stat, value FROM achievement_stats WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchall()
        return {row["stat"]: int(row["value"]) for row in rows}

    @staticmethod
    def _unlocked_ids(connection: sqlite3.Connection, guild_id: int, user_id: int) -> set[str]:
        rows = connection.execute(
            """
            SELECT achievement_id FROM achievement_unlocks
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        ).fetchall()
        return {row["achievement_id"] for row in rows}

    async def profile(self, guild_id: int, user_id: int) -> Profile:
        """Estadísticas y logros de un miembro (vacíos si nunca ha hecho nada)."""
        return await self._run(self._profile_sync, guild_id, user_id)

    def _profile_sync(self, guild_id: int, user_id: int) -> Profile:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT achievement_id, unlocked_at FROM achievement_unlocks
                WHERE guild_id = ? AND user_id = ?
                """,
                (guild_id, user_id),
            ).fetchall()
            return Profile(
                stats=self._stats(connection, guild_id, user_id),
                unlocked={row["achievement_id"]: float(row["unlocked_at"]) for row in rows},
            )

    async def guild_unlocks(self, guild_id: int) -> tuple[list[tuple[int, str]], int]:
        """Todos los desbloqueos del servidor y cuántos miembros tienen estadísticas.

        Sirve para el ranking y para el porcentaje de gente que tiene cada
        logro. Son pocas filas (miembros × logros), así que se calcula en Python.

        Returns:
            `([(user_id, achievement_id), ...], miembros_con_perfil)`.
        """
        return await self._run(self._guild_unlocks_sync, guild_id)

    def _guild_unlocks_sync(self, guild_id: int) -> tuple[list[tuple[int, str]], int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT user_id, achievement_id FROM achievement_unlocks WHERE guild_id = ?",
                (guild_id,),
            ).fetchall()
            members = connection.execute(
                "SELECT COUNT(DISTINCT user_id) FROM achievement_stats WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()[0]
            return [(int(r["user_id"]), r["achievement_id"]) for r in rows], int(members)

    async def unlock_count(self, guild_id: int) -> int:
        """Logros desbloqueados en el servidor, contando los de todos los miembros."""
        return await self._run(self._unlock_count_sync, guild_id)

    def _unlock_count_sync(self, guild_id: int) -> int:
        with self._connect() as connection:
            (count,) = connection.execute(
                "SELECT COUNT(*) FROM achievement_unlocks WHERE guild_id = ?", (guild_id,)
            ).fetchone()
        return int(count)

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra los logros del servidor (el bot ha salido de él)."""
        await self._run(self._delete_guild_data_sync, guild_id)

    def _delete_guild_data_sync(self, guild_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM achievement_stats WHERE guild_id = ?", (guild_id,))
            connection.execute("DELETE FROM achievement_unlocks WHERE guild_id = ?", (guild_id,))
