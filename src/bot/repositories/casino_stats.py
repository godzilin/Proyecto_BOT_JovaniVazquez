"""Persistencia de las estadísticas del casino (`apuestas`).

Tabla (en el mismo archivo SQLite que el resto del bot):

- `casino_plays`: una fila por jugada terminada de cualquier juego del casino,
  con la apuesta, lo devuelto, el IRPF que movió y el saldo final. Guarda
  también el día, la hora y el día de la semana en hora canaria, porque SQLite
  no sabe de zonas horarias y agrupar por hora local es lo que más se consulta.

- `casino_play_counts`: cuántas jugadas lleva cada miembro desde que existe esta tabla
  (un contador persistente, no un `COUNT(*)` de lo anterior). Solo cuentan las de
  `FREE_BOX_MIN_STAKE` o más; cada 100 el colmado le regala una caja de cartón
  (`bot.services.lootboxes.FREE_BOX_PLAYS`).

Solo se escribe desde `CasinoStatsRepository.record`, a la que llaman los
juegos al terminar cada jugada (ver `bot.cogs.apuestas.record`). No mueve
dinero: el dinero sigue pasando únicamente por `EconomyService`. Por eso,
si esta tabla fallara, el juego sigue y solo se pierde la fila.

Coste: una fila de ~80 bytes por jugada. Todas las cuentas se hacen con
agregados de SQLite (`SUM`, `GROUP BY`), nunca cargando las jugadas en
memoria, para no tocar el presupuesto de RAM del NAS.

Las reglas y los textos viven en `bot.services.casino_stats`.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite
from bot.services.casino_stats import (
    DAILY_WINDOW,
    STAKE_BUCKETS,
    TOP,
    Highlight,
    LedgerTotals,
    Play,
    PlayerRow,
    Report,
    Totals,
)
from bot.services.levels import TIMEZONE
from bot.services.lootboxes import FREE_BOX_MIN_STAKE

T = TypeVar("T")

# Condición de «se jugó todo el saldo», la misma que `Play.all_in` y que los
# logros (`casino_stats`): la apuesta cubre el saldo que había antes.
_BEFORE = "(balance_after - (payout - stake))"
_ALL_IN = f"(stake > 0 AND stake >= {_BEFORE} AND {_BEFORE} > 0)"

_TOTALS_COLUMNS = f"""
    SUM(stake > 0) AS plays,
    SUM(stake = 0) AS free_plays,
    SUM(stake) AS wagered,
    SUM(payout) AS paid,
    SUM(CASE WHEN stake = 0 THEN payout ELSE 0 END) AS free_paid,
    SUM(stake > 0 AND payout > stake) AS wins,
    SUM(stake > 0 AND payout = stake) AS pushes,
    SUM(stake > 0 AND payout < stake) AS losses,
    SUM(stake > 0 AND payout = 0) AS busts,
    SUM(CASE WHEN stake > 0 AND payout > stake THEN payout - stake ELSE 0 END) AS won_amount,
    SUM(CASE WHEN stake > 0 AND payout < stake THEN stake - payout ELSE 0 END) AS lost_amount,
    MAX(stake) AS max_stake,
    MAX(payout - stake) AS best,
    MIN(payout - stake) AS worst,
    MAX(CASE WHEN stake > 0 THEN payout * 1.0 / stake END) AS max_mult,
    SUM(MAX(tax, 0)) AS withheld,
    SUM(MAX(-tax, 0)) AS refunded,
    SUM({_ALL_IN}) AS all_ins,
    SUM({_ALL_IN} AND payout > stake) AS all_in_wins,
    SUM(balance_after = 0) AS broke
"""


def _bucket_case() -> str:
    """`CASE` de SQL que pone cada apuesta en su tramo de `STAKE_BUCKETS`."""
    parts = []
    for index, (top, _label) in enumerate(STAKE_BUCKETS):
        if top is None:
            parts.append(f"ELSE {index}")
        else:
            parts.append(f"WHEN stake <= {int(top)} THEN {index}")
    return "CASE " + " ".join(parts) + " END"


def _totals(row: sqlite3.Row) -> Totals:
    return Totals(
        plays=int(row["plays"] or 0),
        free_plays=int(row["free_plays"] or 0),
        wagered=int(row["wagered"] or 0),
        paid=int(row["paid"] or 0),
        free_paid=int(row["free_paid"] or 0),
        wins=int(row["wins"] or 0),
        pushes=int(row["pushes"] or 0),
        losses=int(row["losses"] or 0),
        busts=int(row["busts"] or 0),
        won_amount=int(row["won_amount"] or 0),
        lost_amount=int(row["lost_amount"] or 0),
        max_stake=int(row["max_stake"] or 0),
        best=max(0, int(row["best"] or 0)),
        worst=min(0, int(row["worst"] or 0)),
        max_mult=float(row["max_mult"] or 0.0),
        withheld=int(row["withheld"] or 0),
        refunded=int(row["refunded"] or 0),
        all_ins=int(row["all_ins"] or 0),
        all_in_wins=int(row["all_in_wins"] or 0),
        broke=int(row["broke"] or 0),
    )


def _highlight(row: sqlite3.Row) -> Highlight:
    return Highlight(
        user_id=int(row["user_id"]),
        game=str(row["game"]),
        stake=int(row["stake"]),
        payout=int(row["payout"]),
        created_at=float(row["created_at"]),
    )


class CasinoStatsRepository:
    """Acceso SQLite a las jugadas del casino.

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
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS casino_plays (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    game TEXT NOT NULL,
                    stake INTEGER NOT NULL CHECK (stake >= 0),
                    payout INTEGER NOT NULL CHECK (payout >= 0),
                    -- IRPF de la jugada: positivo retenido, negativo devuelto.
                    tax INTEGER NOT NULL DEFAULT 0,
                    balance_after INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    -- Hora canaria, calculada al apuntar (SQLite no sabe de zonas).
                    day TEXT NOT NULL,
                    hour INTEGER NOT NULL,
                    weekday INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS casino_play_counts (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    plays INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id)
                );
                CREATE INDEX IF NOT EXISTS casino_plays_guild_time
                    ON casino_plays (guild_id, created_at);
                CREATE INDEX IF NOT EXISTS casino_plays_member_time
                    ON casino_plays (guild_id, user_id, created_at);
                """
            )

    async def record(self, guild_id: int, user_id: int, play: Play, *, now: float) -> int | None:
        """Apunta una jugada terminada y devuelve las que lleva el miembro (contador persistente).

        Devuelve `None` si la jugada no llega a `FREE_BOX_MIN_STAKE` y no suma al
        contador (sí queda en `casino_plays`).

        Raises:
            ValueError: Si la apuesta o el premio son negativos.
        """
        if play.stake < 0 or play.payout < 0:
            raise ValueError("Jugada inválida: apuesta y premio no pueden ser negativos.")
        return await self._run(self._record_sync, guild_id, user_id, play, now)

    def _record_sync(self, guild_id: int, user_id: int, play: Play, now: float) -> int | None:
        local = datetime.fromtimestamp(now, TIMEZONE)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO casino_plays (
                    guild_id, user_id, game, stake, payout, tax, balance_after,
                    created_at, day, hour, weekday
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    user_id,
                    play.game,
                    play.stake,
                    play.payout,
                    play.tax,
                    play.balance_after,
                    now,
                    local.date().isoformat(),
                    local.hour,
                    local.weekday(),
                ),
            )
            if play.stake < FREE_BOX_MIN_STAKE:
                return None
            connection.execute(
                """
                INSERT INTO casino_play_counts (guild_id, user_id, plays) VALUES (?, ?, 1)
                ON CONFLICT (guild_id, user_id) DO UPDATE SET plays = plays + 1
                """,
                (guild_id, user_id),
            )
            (plays,) = connection.execute(
                "SELECT plays FROM casino_play_counts WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
        return int(plays)

    async def report(
        self, guild_id: int, user_id: int | None, *, since: float | None, today: date
    ) -> Report:
        """Todas las cifras de `apuestas` de un golpe, en una sola conexión.

        Args:
            user_id: Miembro del que sacar las cifras, o `None` para el servidor
                entero (y entonces también sale el ranking).
            since: Epoch desde el que contar, o `None` para todo.
            today: Día de hoy en hora canaria, para la gráfica de los últimos días.

        El libro (`Report.ledger`) sale vacío: lo rellena el servicio de la
        economía, que es el único que lee `economy_ledger`.
        """
        return await self._run(self._report_sync, guild_id, user_id, since, today)

    def _report_sync(
        self, guild_id: int, user_id: int | None, since: float | None, today: date
    ) -> Report:
        where = ["guild_id = ?"]
        args: list[object] = [guild_id]
        if user_id is not None:
            where.append("user_id = ?")
            args.append(user_id)
        if since is not None:
            where.append("created_at >= ?")
            args.append(since)
        clause = " AND ".join(where)
        params = tuple(args)
        with self._connect() as connection:
            by_game = {
                str(row["game"]): _totals(row)
                for row in connection.execute(
                    f"SELECT game, {_TOTALS_COLUMNS} FROM casino_plays "
                    f"WHERE {clause} GROUP BY game",
                    params,
                )
            }
            players, first_at = connection.execute(
                f"SELECT COUNT(DISTINCT user_id), MIN(created_at) FROM casino_plays WHERE {clause}",
                params,
            ).fetchone()
            days = tuple(
                str(day)
                for (day,) in connection.execute(
                    f"SELECT DISTINCT day FROM casino_plays WHERE {clause} ORDER BY day", params
                )
            )
            median = self._median_stake(connection, clause, params)
            counts = dict(
                connection.execute(
                    f"SELECT {_bucket_case()} AS bucket, COUNT(*) FROM casino_plays "
                    f"WHERE {clause} AND stake > 0 GROUP BY bucket",
                    params,
                ).fetchall()
            )
            buckets = tuple(int(counts.get(i, 0)) for i in range(len(STAKE_BUCKETS)))
            by_hour = self._grouped(connection, "hour", 24, clause, params)
            by_weekday = self._grouped(connection, "weekday", 7, clause, params)
            daily = self._daily(connection, guild_id, user_id, today)
            tops = self._tops(connection, clause, params)
            ranking: tuple[PlayerRow, ...] = ()
            if user_id is None:
                ranking = self._ranking(connection, clause, params)
            (recorded_since,) = connection.execute(
                "SELECT MIN(created_at) FROM casino_plays WHERE guild_id = ?", (guild_id,)
            ).fetchone()
        return Report(
            by_game=by_game,
            players=int(players or 0),
            days=days,
            first_at=float(first_at) if first_at is not None else None,
            median_stake=median,
            buckets=buckets,
            by_hour=by_hour,
            by_weekday=by_weekday,
            daily=daily,
            top_wins=tops[0],
            top_losses=tops[1],
            top_mults=tops[2],
            top_stakes=tops[3],
            ranking=ranking,
            ledger=LedgerTotals(),
            recorded_since=float(recorded_since) if recorded_since is not None else None,
        )

    @staticmethod
    def _median_stake(connection: sqlite3.Connection, clause: str, params: tuple) -> float:
        # Con OFFSET en vez de cargar todas las apuestas: la tabla puede tener
        # cientos de miles de filas y el NAS tiene 1 GB para todo el bot.
        (count,) = connection.execute(
            f"SELECT COUNT(*) FROM casino_plays WHERE {clause} AND stake > 0", params
        ).fetchone()
        if not count:
            return 0.0
        middle = connection.execute(
            f"SELECT stake FROM casino_plays WHERE {clause} AND stake > 0 "
            "ORDER BY stake LIMIT ? OFFSET ?",
            (*params, 2 - count % 2, (count - 1) // 2),
        ).fetchall()
        return sum(int(row[0]) for row in middle) / len(middle)

    @staticmethod
    def _grouped(
        connection: sqlite3.Connection, column: str, size: int, clause: str, params: tuple
    ) -> tuple[tuple[int, int], ...]:
        """`(jugadas, neto)` por cada valor de `column` (hora o día de la semana)."""
        found = {
            int(row[0]): (int(row[1]), int(row[2] or 0))
            for row in connection.execute(
                f"SELECT {column}, COUNT(*), SUM(payout - stake) FROM casino_plays "
                f"WHERE {clause} GROUP BY {column}",
                params,
            )
        }
        return tuple(found.get(i, (0, 0)) for i in range(size))

    @staticmethod
    def _daily(
        connection: sqlite3.Connection, guild_id: int, user_id: int | None, today: date
    ) -> tuple[tuple[str, int, int], ...]:
        """Los últimos `DAILY_WINDOW` días, con ceros en los días sin jugadas."""
        first = today - timedelta(days=DAILY_WINDOW - 1)
        sql = (
            "SELECT day, COUNT(*), SUM(payout - stake) FROM casino_plays "
            "WHERE guild_id = ? AND day >= ?"
        )
        args: list[object] = [guild_id, first.isoformat()]
        if user_id is not None:
            sql += " AND user_id = ?"
            args.append(user_id)
        found = {
            str(row[0]): (int(row[1]), int(row[2] or 0))
            for row in connection.execute(sql + " GROUP BY day", args)
        }
        out = []
        for offset in range(DAILY_WINDOW):
            day = (first + timedelta(days=offset)).isoformat()
            plays, net = found.get(day, (0, 0))
            out.append((day, plays, net))
        return tuple(out)

    @staticmethod
    def _tops(
        connection: sqlite3.Connection, clause: str, params: tuple
    ) -> tuple[tuple[Highlight, ...], ...]:
        """Mayores premios, mayores palos, mejores multiplicadores y mayores apuestas."""
        base = f"SELECT user_id, game, stake, payout, created_at FROM casino_plays WHERE {clause}"
        queries = (
            f"{base} AND payout > stake ORDER BY payout - stake DESC, id LIMIT {TOP}",
            f"{base} AND payout < stake ORDER BY payout - stake ASC, id LIMIT {TOP}",
            f"{base} AND stake > 0 AND payout > stake "
            f"ORDER BY payout * 1.0 / stake DESC, id LIMIT {TOP}",
            f"{base} AND stake > 0 ORDER BY stake DESC, id LIMIT {TOP}",
        )
        return tuple(
            tuple(_highlight(row) for row in connection.execute(sql, params)) for sql in queries
        )

    @staticmethod
    def _ranking(
        connection: sqlite3.Connection, clause: str, params: tuple
    ) -> tuple[PlayerRow, ...]:
        rows = connection.execute(
            f"""
            SELECT user_id,
                SUM(stake > 0) AS plays,
                SUM(stake) AS wagered,
                SUM(payout) AS paid,
                MAX(payout - stake) AS best,
                SUM({_ALL_IN}) AS all_ins,
                SUM(balance_after = 0) AS broke,
                SUM(MAX(tax, 0)) AS withheld,
                SUM(MAX(-tax, 0)) AS refunded
            FROM casino_plays WHERE {clause} GROUP BY user_id
            """,
            params,
        ).fetchall()
        return tuple(
            PlayerRow(
                user_id=int(row["user_id"]),
                plays=int(row["plays"] or 0),
                wagered=int(row["wagered"] or 0),
                paid=int(row["paid"] or 0),
                best=max(0, int(row["best"] or 0)),
                all_ins=int(row["all_ins"] or 0),
                broke=int(row["broke"] or 0),
                withheld=int(row["withheld"] or 0),
                refunded=int(row["refunded"] or 0),
            )
            for row in rows
        )

    async def house_net(self, guild_id: int, since_day: str) -> int:
        """Lo que ha ganado la casa desde un día (negativo si ha perdido).

        Args:
            since_day: Primer día que cuenta, en hora canaria (`AAAA-MM-DD`).
        """
        return await self._run(self._house_net_sync, guild_id, since_day)

    def _house_net_sync(self, guild_id: int, since_day: str) -> int:
        with self._connect() as connection:
            (net,) = connection.execute(
                "SELECT COALESCE(SUM(stake - payout), 0) FROM casino_plays "
                "WHERE guild_id = ? AND day >= ?",
                (guild_id, since_day),
            ).fetchone()
        return int(net)

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra las jugadas de un servidor cuando el bot sale de él."""
        await self._run(self._delete_guild_data_sync, guild_id)

    def _delete_guild_data_sync(self, guild_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM casino_plays WHERE guild_id = ?", (guild_id,))
            connection.execute("DELETE FROM casino_play_counts WHERE guild_id = ?", (guild_id,))
