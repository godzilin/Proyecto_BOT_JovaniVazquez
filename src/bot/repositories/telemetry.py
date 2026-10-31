"""Persistencia de la telemetría interna (contadores, usuarios del día y muestras del sistema).

Tablas (en el mismo archivo SQLite del bot):

- `telemetry_counts`: un contador por (día, hora, servidor, tipo, nombre). La
  hora `-1` significa «día entero»: son las filas horarias viejas ya plegadas.
- `telemetry_users`: qué usuarios distintos hicieron algo cada día. Es el único
  dato por persona, por eso se borra pronto (`USER_RETENTION_DAYS`).
- `telemetry_system`: muestras de memoria, retraso del bucle y navegadores.
- `telemetry_report_channel`: en qué canal publica cada servidor el informe
  diario (lo elige `informe`). Sin fila, no se publica.

Retención (Biblia: «no guardar datos personales innecesarios»):

- horas sueltas: `HOURLY_RETENTION_DAYS` (90); después se pliegan a un día;
- contadores diarios: `COUNTS_RETENTION_DAYS` (400);
- usuarios: `USER_RETENTION_DAYS` (90);
- muestras del sistema: `SYSTEM_RETENTION_DAYS` (14).

Guarda solo cantidades, nunca contenido de mensajes. Las tablas de otros
repositorios (`casino_plays`, `economy_wallets`, `economy_ledger`,
`achievement_unlocks`, `shop_purchases`, `pets`, `pet_owners`) solo se leen,
para el informe; nunca se escriben. Todas las cuentas del informe son
agregados de SQL, salvo los usuarios, que se cargan para seudonimizarlos.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Sequence
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite
from bot.repositories.economy import STATE_ACCOUNT_ID
from bot.services.levels import TIMEZONE
from bot.services.telemetry import Batch, ReportData

T = TypeVar("T")

HOURLY_RETENTION_DAYS = 90
"""Días que se guarda el desglose por hora; antes de eso se pliega en una fila por día."""

COUNTS_RETENTION_DAYS = 400
"""Días que se guardan los contadores (ya plegados) antes de borrarlos."""

USER_RETENTION_DAYS = 90
"""Días que se guardan los usuarios distintos por día (dato personal: retención corta)."""

SYSTEM_RETENTION_DAYS = 14
"""Días que se guardan las muestras del sistema."""

WHOLE_DAY = -1
"""Valor de `hour` de las filas que resumen un día entero."""

_COUNTER_COLUMNS = (
    "events",
    "errors",
    "ms_count",
    "ms_total",
    "ms_max",
    "ms_lt500",
    "ms_lt1000",
    "ms_lt3000",
    "ms_ge3000",
)
_COUNT_KEY = "day, hour, guild_id, kind, name"

_SCHEMA = """
    CREATE TABLE IF NOT EXISTS telemetry_counts (
        day TEXT NOT NULL,
        hour INTEGER NOT NULL,
        guild_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        events INTEGER NOT NULL DEFAULT 0,
        errors INTEGER NOT NULL DEFAULT 0,
        ms_count INTEGER NOT NULL DEFAULT 0,
        ms_total INTEGER NOT NULL DEFAULT 0,
        ms_max INTEGER NOT NULL DEFAULT 0,
        ms_lt500 INTEGER NOT NULL DEFAULT 0,
        ms_lt1000 INTEGER NOT NULL DEFAULT 0,
        ms_lt3000 INTEGER NOT NULL DEFAULT 0,
        ms_ge3000 INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (day, hour, guild_id, kind, name)
    ) WITHOUT ROWID;

    CREATE TABLE IF NOT EXISTS telemetry_users (
        day TEXT NOT NULL,
        guild_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        PRIMARY KEY (day, guild_id, kind, name, user_id)
    ) WITHOUT ROWID;

    CREATE TABLE IF NOT EXISTS telemetry_system (
        at REAL NOT NULL,
        day TEXT NOT NULL,
        memory_mb REAL,
        rss_mb REAL NOT NULL,
        loop_lag_ms REAL NOT NULL,
        gateway_ms REAL,
        chromium INTEGER NOT NULL,
        node INTEGER NOT NULL,
        cpu_pct REAL,
        db_mb REAL,
        disk_free_mb REAL,
        chromium_mb REAL,
        node_mb REAL,
        io_read_mb REAL,
        io_write_mb REAL,
        tasks INTEGER
    );
    CREATE INDEX IF NOT EXISTS telemetry_system_at ON telemetry_system (at);

    CREATE TABLE IF NOT EXISTS telemetry_report_channel (
        guild_id INTEGER PRIMARY KEY,
        channel_id INTEGER NOT NULL
    );
"""

# Columnas añadidas a `telemetry_system` después de crearla: las bases de datos de
# antes las reciben con `ALTER TABLE` al arrancar.
_SYSTEM_ADDED = {
    "cpu_pct": "REAL",
    "db_mb": "REAL",
    "disk_free_mb": "REAL",
    "chromium_mb": "REAL",
    "node_mb": "REAL",
    "io_read_mb": "REAL",
    "io_write_mb": "REAL",
    "tasks": "INTEGER",
}

# Suma de todas las columnas y MAX para ms_max: sirve para el upsert y para el pliegue.
_SUM_SET = ", ".join(
    f"{c} = MAX({c}, excluded.{c})" if c == "ms_max" else f"{c} = {c} + excluded.{c}"
    for c in _COUNTER_COLUMNS
)


class TelemetryRepository:
    """Acceso SQLite a la telemetría.

    Args:
        database_path: Ruta del archivo SQLite persistente.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        # Si nadie llamó a `initialize` (pruebas que cierran el bot sin arrancarlo,
        # con flush al cerrar), `write` crea las tablas la primera vez.
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        return sqlite.connect(self.database_path)

    async def _run(self, operation: Callable[..., T], *args: object) -> T:
        """Ejecuta una operación SQLite fuera del event loop."""
        return await asyncio.to_thread(operation, *args)

    async def initialize(self) -> None:
        """Crea las tablas si no existen (idempotente)."""
        await self._run(self._initialize_sync)

    def _initialize_sync(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executescript(_SCHEMA)
            present = {
                row["name"] for row in connection.execute("PRAGMA table_info(telemetry_system)")
            }
            for column, sql_type in _SYSTEM_ADDED.items():
                if column not in present:
                    connection.execute(
                        f"ALTER TABLE telemetry_system ADD COLUMN {column} {sql_type}"
                    )
        self._ready = True

    async def write(self, batch: Batch) -> None:
        """Escribe un lote en una sola transacción.

        Los contadores se suman a los que ya hubiera (upsert), los usuarios se
        ignoran si ya estaban y las muestras se añaden. Crea las tablas si hace falta.
        """
        await self._run(self._write_sync, batch)

    def _write_sync(self, batch: Batch) -> None:
        if not self._ready:
            self._initialize_sync()
        columns = ", ".join(_COUNTER_COLUMNS)
        marks = ", ".join("?" for _ in range(5 + len(_COUNTER_COLUMNS)))
        upsert = (
            f"INSERT INTO telemetry_counts ({_COUNT_KEY}, {columns}) VALUES ({marks}) "
            f"ON CONFLICT({_COUNT_KEY}) DO UPDATE SET {_SUM_SET}"
        )
        counters = [
            (
                key.day,
                key.hour,
                key.guild_id,
                key.kind,
                key.name,
                *(getattr(value, column) for column in _COUNTER_COLUMNS),
            )
            for key, value in batch.counters.items()
        ]
        users = [(u.day, u.guild_id, u.kind, u.name, u.user_id) for u in batch.users]
        samples = [
            (
                s.at,
                s.day,
                s.memory_mb,
                s.rss_mb,
                s.loop_lag_ms,
                s.gateway_ms,
                s.chromium,
                s.node,
                s.cpu_pct,
                s.db_mb,
                s.disk_free_mb,
                s.chromium_mb,
                s.node_mb,
                s.io_read_mb,
                s.io_write_mb,
                s.tasks,
            )
            for s in batch.samples
        ]
        with closing(self._connect()) as connection, connection:
            connection.executemany(upsert, counters)
            connection.executemany(
                "INSERT OR IGNORE INTO telemetry_users (day, guild_id, kind, name, user_id) "
                "VALUES (?, ?, ?, ?, ?)",
                users,
            )
            connection.executemany(
                "INSERT INTO telemetry_system "
                "(at, day, memory_mb, rss_mb, loop_lag_ms, gateway_ms, chromium, node, "
                "cpu_pct, db_mb, disk_free_mb, chromium_mb, node_mb, io_read_mb, io_write_mb, "
                "tasks) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                samples,
            )

    async def prune(self, *, now: float) -> None:
        """Aplica la retención: pliega horas viejas y borra lo que pasa de plazo."""
        await self._run(self._prune_sync, now)

    def _prune_sync(self, now: float) -> None:
        if not self._ready:
            self._initialize_sync()
        today = datetime.fromtimestamp(now, TIMEZONE).date()
        fold_before = (today - timedelta(days=HOURLY_RETENTION_DAYS)).isoformat()
        counts_before = (today - timedelta(days=COUNTS_RETENTION_DAYS)).isoformat()
        users_before = (today - timedelta(days=USER_RETENTION_DAYS)).isoformat()
        system_before = now - SYSTEM_RETENTION_DAYS * 86400
        columns = ", ".join(_COUNTER_COLUMNS)
        folded = ", ".join(f"MAX({c})" if c == "ms_max" else f"SUM({c})" for c in _COUNTER_COLUMNS)
        with closing(self._connect()) as connection, connection:
            # Pliegue: cada hora vieja se suma a su fila de día entero (`hour = -1`),
            # que puede existir ya si el pliegue se hizo a medias otro día.
            connection.execute(
                f"INSERT INTO telemetry_counts (day, hour, guild_id, kind, name, {columns}) "
                f"SELECT day, {WHOLE_DAY}, guild_id, kind, name, {folded} "
                "FROM telemetry_counts WHERE hour >= 0 AND day < ? "
                "GROUP BY day, guild_id, kind, name "
                f"ON CONFLICT({_COUNT_KEY}) DO UPDATE SET {_SUM_SET}",
                (fold_before,),
            )
            connection.execute(
                "DELETE FROM telemetry_counts WHERE hour >= 0 AND day < ?", (fold_before,)
            )
            connection.execute("DELETE FROM telemetry_counts WHERE day < ?", (counts_before,))
            connection.execute("DELETE FROM telemetry_users WHERE day < ?", (users_before,))
            connection.execute("DELETE FROM telemetry_system WHERE at < ?", (system_before,))

    async def report_channel(self, guild_id: int) -> int | None:
        """Canal donde el servidor publica el informe diario; `None` si no publica."""
        return await self._run(self._report_channel_sync, guild_id)

    def _report_channel_sync(self, guild_id: int) -> int | None:
        if not self._ready:
            self._initialize_sync()
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT channel_id FROM telemetry_report_channel WHERE guild_id = ?", (guild_id,)
            ).fetchone()
        return None if row is None else int(row["channel_id"])

    async def set_report_channel(self, guild_id: int, channel_id: int | None) -> None:
        """Guarda el canal del informe diario; `None` deja de publicarlo."""
        await self._run(self._set_report_channel_sync, guild_id, channel_id)

    def _set_report_channel_sync(self, guild_id: int, channel_id: int | None) -> None:
        if not self._ready:
            self._initialize_sync()
        with closing(self._connect()) as connection, connection:
            if channel_id is None:
                connection.execute(
                    "DELETE FROM telemetry_report_channel WHERE guild_id = ?", (guild_id,)
                )
            else:
                connection.execute(
                    "INSERT INTO telemetry_report_channel (guild_id, channel_id) VALUES (?, ?) "
                    "ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id",
                    (guild_id, channel_id),
                )

    async def usage_counts(
        self,
        guild_id: int,
        kinds: Sequence[str],
        *,
        since_day: str | None = None,
        until_day: str | None = None,
    ) -> dict[str, dict[str, int]]:
        """Eventos por tipo y nombre entre dos días, ambos incluidos.

        Como en el informe, suma también los contadores del servidor 0 (dibujos,
        música y arranques, que no son de ningún servidor).

        Args:
            kinds: Tipos de evento que se quieren.
            since_day: Primer día (`AAAA-MM-DD`), o `None` para todo lo guardado.
            until_day: Último día, o `None` para llegar hasta hoy.

        Returns:
            `{tipo: {nombre: eventos}}`, sin los tipos que no tengan nada.
        """
        return await self._run(
            self._usage_counts_sync, guild_id, tuple(kinds), since_day, until_day
        )

    def _usage_counts_sync(
        self,
        guild_id: int,
        kinds: tuple[str, ...],
        since_day: str | None,
        until_day: str | None,
    ) -> dict[str, dict[str, int]]:
        if not self._ready:
            self._initialize_sync()
        marks = ", ".join("?" for _ in kinds)
        counts: dict[str, dict[str, int]] = {}
        with closing(self._connect()) as connection:
            for row in connection.execute(
                "SELECT kind, name, SUM(events) AS events FROM telemetry_counts "
                f"WHERE guild_id IN (?, 0) AND kind IN ({marks}) AND day >= ? AND day <= ? "
                "GROUP BY kind, name",
                (guild_id, *kinds, since_day or "", until_day or "9999-12-31"),
            ):
                counts.setdefault(row["kind"], {})[row["name"]] = int(row["events"])
        return counts

    async def report_data(self, *, guild_id: int, since_day: str, since_ts: float) -> ReportData:
        """Agrega en SQL lo que necesita `build_report` para un servidor desde `since_day`."""
        return await self._run(self._report_data_sync, guild_id, since_day, since_ts)

    @staticmethod
    def _has_table(connection: sqlite3.Connection, name: str) -> bool:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        return row is not None

    def _report_data_sync(self, guild_id: int, since_day: str, since_ts: float) -> ReportData:
        # Los contadores del servidor 0 (avisos del log, dibujos) no son de ningún
        # servidor y salen en el informe de todos.
        if not self._ready:
            self._initialize_sync()
        data = ReportData()
        with closing(self._connect()) as connection:
            data.usage = [
                dict(row)
                for row in connection.execute(
                    "SELECT kind, name, SUM(events) AS events, SUM(errors) AS errors, "
                    "SUM(ms_count) AS ms_count, SUM(ms_total) AS ms_total, "
                    "MAX(ms_max) AS ms_max, SUM(ms_lt500) AS lt500, SUM(ms_lt1000) AS lt1000, "
                    "SUM(ms_lt3000) AS lt3000, SUM(ms_ge3000) AS ge3000 "
                    "FROM telemetry_counts WHERE guild_id IN (?, 0) AND day >= ? "
                    "GROUP BY kind, name",
                    (guild_id, since_day),
                )
            ]
            data.daily = [
                (r["day"], r["kind"], r["events"], r["errors"])
                for r in connection.execute(
                    "SELECT day, kind, SUM(events) AS events, SUM(errors) AS errors "
                    "FROM telemetry_counts WHERE guild_id IN (?, 0) AND day >= ? "
                    "GROUP BY day, kind",
                    (guild_id, since_day),
                )
            ]
            data.daily_ms = [
                (r["day"], r["kind"], r["ms_count"], r["ms_total"], r["slow"])
                for r in connection.execute(
                    "SELECT day, kind, SUM(ms_count) AS ms_count, SUM(ms_total) AS ms_total, "
                    "SUM(ms_ge3000) AS slow FROM telemetry_counts "
                    "WHERE guild_id IN (?, 0) AND day >= ? AND ms_count > 0 GROUP BY day, kind",
                    (guild_id, since_day),
                )
            ]
            data.starts = [
                (r["day"], r["name"], r["times"])
                for r in connection.execute(
                    "SELECT day, name, SUM(events) AS times FROM telemetry_counts "
                    "WHERE guild_id = 0 AND kind = 'arranque' AND day >= ? GROUP BY day, name",
                    (since_day,),
                )
            ]
            data.hourly = [
                (r["hour"], r["kind"], r["events"])
                for r in connection.execute(
                    "SELECT hour, kind, SUM(events) AS events FROM telemetry_counts "
                    "WHERE guild_id IN (?, 0) AND day >= ? AND hour >= 0 GROUP BY hour, kind",
                    (guild_id, since_day),
                )
            ]
            data.users = [
                (r["day"], r["kind"], r["name"], r["user_id"])
                for r in connection.execute(
                    "SELECT day, kind, name, user_id FROM telemetry_users "
                    "WHERE guild_id = ? AND day >= ?",
                    (guild_id, since_day),
                )
            ]
            data.system = [
                dict(row)
                for row in connection.execute(
                    "SELECT day, MAX(memory_mb) AS memory_max, AVG(memory_mb) AS memory_avg, "
                    "MAX(rss_mb) AS rss_max, MAX(loop_lag_ms) AS lag_max, "
                    "AVG(gateway_ms) AS gateway_avg, MAX(chromium) AS chromium_max, "
                    "MAX(node) AS node_max, MAX(cpu_pct) AS cpu_max, AVG(cpu_pct) AS cpu_avg, "
                    "MAX(db_mb) AS db_max, MIN(disk_free_mb) AS disk_min, "
                    "COUNT(*) AS samples, MAX(chromium_mb) AS chromium_mb_max, "
                    "MAX(node_mb) AS node_mb_max, SUM(io_read_mb) AS io_read, "
                    "SUM(io_write_mb) AS io_write, MAX(tasks) AS tasks_max "
                    "FROM telemetry_system WHERE day >= ? GROUP BY day",
                    (since_day,),
                )
            ]
            if self._has_table(connection, "casino_plays"):
                data.casino = [
                    dict(row)
                    for row in connection.execute(
                        "SELECT game, COALESCE(SUM(stake > 0), 0) AS plays, "
                        "COALESCE(SUM(stake = 0), 0) AS free, "
                        "COUNT(DISTINCT user_id) AS players, "
                        "COALESCE(SUM(stake), 0) AS wagered, COALESCE(SUM(payout), 0) AS paid "
                        "FROM casino_plays WHERE guild_id = ? AND created_at >= ? "
                        "GROUP BY game",
                        (guild_id, since_ts),
                    )
                ]
            if self._has_table(connection, "economy_wallets"):
                for row in connection.execute(
                    "SELECT user_id, balance FROM economy_wallets WHERE guild_id = ?",
                    (guild_id,),
                ):
                    if row["user_id"] > 0:
                        data.balances.append(int(row["balance"]))
                    else:
                        data.system_accounts[int(row["user_id"])] = int(row["balance"])
            if self._has_table(connection, "economy_ledger"):
                # Por día canario en Python: con el cambio de hora, un desfase fijo
                # en SQL pasaría una hora de movimientos al día de al lado.
                per_day: dict[str, list[int]] = {}
                for row in connection.execute(
                    "SELECT created_at, delta FROM economy_ledger "
                    "WHERE guild_id = ? AND created_at >= ? AND user_id > 0",
                    (guild_id, since_ts),
                ):
                    day = datetime.fromtimestamp(row["created_at"], TIMEZONE).date().isoformat()
                    totals = per_day.setdefault(day, [0, 0, 0])
                    delta = int(row["delta"])
                    totals[0 if delta > 0 else 1] += abs(delta)
                    totals[2] += 1
                data.ledger_daily = [(day, *totals) for day, totals in sorted(per_day.items())]
                # La cuenta del Estado: lo que recauda por impuesto y lo que gasta.
                state: dict[tuple[str, str], list[int]] = {}
                for row in connection.execute(
                    "SELECT created_at, delta, reason FROM economy_ledger "
                    "WHERE guild_id = ? AND created_at >= ? AND user_id = ?",
                    (guild_id, since_ts, STATE_ACCOUNT_ID),
                ):
                    day = datetime.fromtimestamp(row["created_at"], TIMEZONE).date().isoformat()
                    reason = str(row["reason"]).split(":", 1)[0]
                    totals = state.setdefault((day, reason), [0, 0])
                    delta = int(row["delta"])
                    totals[0 if delta > 0 else 1] += abs(delta)
                data.state_daily = [
                    (day, reason, inflow, outflow)
                    for (day, reason), (inflow, outflow) in sorted(state.items())
                ]
                data.ledger = [
                    dict(row)
                    for row in connection.execute(
                        "SELECT reason, COUNT(*) AS moves, "
                        "COALESCE(SUM(CASE WHEN delta > 0 THEN delta ELSE 0 END), 0) AS inflow, "
                        "COALESCE(SUM(CASE WHEN delta < 0 THEN -delta ELSE 0 END), 0) AS outflow "
                        "FROM economy_ledger WHERE guild_id = ? AND created_at >= ? "
                        "AND user_id > 0 GROUP BY reason",
                        (guild_id, since_ts),
                    )
                ]
            if self._has_table(connection, "achievement_unlocks"):
                data.achievements = [
                    (row["achievement_id"], int(row["total"]), int(row["recent"] or 0))
                    for row in connection.execute(
                        "SELECT achievement_id, COUNT(*) AS total, "
                        "SUM(unlocked_at >= ?) AS recent FROM achievement_unlocks "
                        "WHERE guild_id = ? GROUP BY achievement_id",
                        (since_ts, guild_id),
                    )
                ]
                row = connection.execute(
                    "SELECT COUNT(DISTINCT user_id) AS members FROM achievement_unlocks "
                    "WHERE guild_id = ?",
                    (guild_id,),
                ).fetchone()
                data.achievement_members = int(row["members"])
            if self._has_table(connection, "shop_purchases"):
                data.shop = [
                    dict(row)
                    for row in connection.execute(
                        "SELECT item_name AS name, kind, COUNT(*) AS sales, "
                        "SUM(renewal) AS renewals, COUNT(DISTINCT user_id) AS buyers, "
                        "SUM(base + tax) AS spent FROM shop_purchases "
                        "WHERE guild_id = ? AND created_at >= ? AND refunded = 0 "
                        "GROUP BY item_name, kind",
                        (guild_id, since_ts),
                    )
                ]
            if self._has_table(connection, "pets") and self._has_table(connection, "pet_owners"):
                data.pets = [
                    dict(row)
                    for row in connection.execute(
                        "SELECT pets.species AS species, COUNT(*) AS total, "
                        "SUM(pet_owners.active_id = pets.id) AS active FROM pets "
                        "LEFT JOIN pet_owners ON pet_owners.guild_id = pets.guild_id "
                        "AND pet_owners.user_id = pets.user_id "
                        "WHERE pets.guild_id = ? GROUP BY pets.species",
                        (guild_id,),
                    )
                ]
        return data
