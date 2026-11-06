"""Persistencia de las peleas de gallos: la ficha del gallo de cada miembro y su cementerio.

Tabla `gallos` (en el mismo archivo SQLite que el resto del bot): por servidor
y miembro, el nombre de su gallo, su historial (`RoosterRecord`: peleas,
victorias, KO, rachas y el último día que peleó), sus tres stats (fuerza,
velocidad y aguante, que sí tocan las probabilidades) y lo que necesita el
entrenamiento (la hora del último y cuántos lleva hoy) la forma (`form`, guardada
con su instante `form_at`: la de ahora la calcula `gallos.form_now`), el combo del
gimnasio (`combo`, `combo_day`), hasta cuándo está lesionado (`injured_until`) , el
pique con un rival de la casa (`grudge_rival`, `grudge_wins`) y cuántos le han echado de
comer hoy (`fed_day`, `fed_count`). Las columnas de los
stats y del entrenamiento se añaden a las bases antiguas con `ALTER TABLE ...
DEFAULT 0`: los gallos que ya existían empiezan a cero.

Tabla `gallos_cementerio`: un gallo por fila, con su ficha al morir y quién lo
mató (un rival de la casa o un miembro). Al morir, la fila de `gallos` vuelve a
un gallo nuevo (`bury`) en la misma transacción.

El aspecto del gallo no se guarda aquí, es una skin de la tienda
(`bot.services.skins`). El dinero tampoco: pasa por la economía. Solo se
guardan IDs; todo se borra cuando el bot sale del servidor.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import TypeVar

from bot.repositories import sqlite
from bot.services.gallos import (
    Fallen,
    Finish,
    RoosterRecord,
    Stat,
    TrainError,
    after_fight,
    after_train,
    check_training,
    default_name,
    fallen_from,
    reborn,
)

T = TypeVar("T")


def _record(row: sqlite3.Row) -> RoosterRecord:
    last_day = str(row["last_day"] or "")
    train_day = str(row["train_day"] or "")
    combo_day = str(row["combo_day"] or "")
    fed_day = str(row["fed_day"] or "")
    return RoosterRecord(
        name=str(row["name"]),
        fights=int(row["fights"]),
        wins=int(row["wins"]),
        kos=int(row["kos"]),
        streak=int(row["streak"]),
        best_streak=int(row["best_streak"]),
        day_streak=int(row["day_streak"]),
        last_day=date.fromisoformat(last_day) if last_day else None,
        strength=int(row["strength"]),
        speed=int(row["speed"]),
        endurance=int(row["endurance"]),
        deaths=int(row["deaths"]),
        last_train=float(row["last_train"]),
        train_day=date.fromisoformat(train_day) if train_day else None,
        trains_today=int(row["trains_today"]),
        form=float(row["form"]),
        form_at=float(row["form_at"]),
        combo=int(row["combo"]),
        combo_day=date.fromisoformat(combo_day) if combo_day else None,
        injured_until=float(row["injured_until"]),
        grudge_rival=str(row["grudge_rival"]),
        grudge_wins=int(row["grudge_wins"]),
        fed_day=date.fromisoformat(fed_day) if fed_day else None,
        fed_count=int(row["fed_count"]),
    )


#: Columnas añadidas a `gallos` después de la primera versión: (nombre, definición).
_NEW_COLUMNS = (
    ("strength", "INTEGER NOT NULL DEFAULT 0"),
    ("speed", "INTEGER NOT NULL DEFAULT 0"),
    ("endurance", "INTEGER NOT NULL DEFAULT 0"),
    ("deaths", "INTEGER NOT NULL DEFAULT 0"),
    ("last_train", "REAL NOT NULL DEFAULT 0"),
    ("train_day", "TEXT"),
    ("trains_today", "INTEGER NOT NULL DEFAULT 0"),
    ("form", "REAL NOT NULL DEFAULT 0"),
    ("form_at", "REAL NOT NULL DEFAULT 0"),
    ("combo", "INTEGER NOT NULL DEFAULT 0"),
    ("combo_day", "TEXT"),
    ("injured_until", "REAL NOT NULL DEFAULT 0"),
    ("grudge_rival", "TEXT NOT NULL DEFAULT ''"),
    ("grudge_wins", "INTEGER NOT NULL DEFAULT 0"),
    ("fed_day", "TEXT"),
    ("fed_count", "INTEGER NOT NULL DEFAULT 0"),
)


class GallosRepository:
    """Acceso SQLite a la ficha de los gallos.

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
        """Crea las tablas si no existen y añade las columnas que faltan a las antiguas."""
        await self._run(self._initialize_sync)

    def _initialize_sync(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS gallos (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    fights INTEGER NOT NULL DEFAULT 0,
                    wins INTEGER NOT NULL DEFAULT 0,
                    kos INTEGER NOT NULL DEFAULT 0,
                    streak INTEGER NOT NULL DEFAULT 0,
                    best_streak INTEGER NOT NULL DEFAULT 0,
                    day_streak INTEGER NOT NULL DEFAULT 0,
                    last_day TEXT,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )
            # Stats y entrenamiento, añadidos después de crear la tabla.
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(gallos)")}
            for column, definition in _NEW_COLUMNS:
                if column not in columns:
                    connection.execute(f"ALTER TABLE gallos ADD COLUMN {column} {definition}")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS gallos_cementerio (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    fights INTEGER NOT NULL,
                    wins INTEGER NOT NULL,
                    kos INTEGER NOT NULL,
                    strength INTEGER NOT NULL,
                    speed INTEGER NOT NULL,
                    endurance INTEGER NOT NULL,
                    died_at REAL NOT NULL,
                    killer_kind TEXT NOT NULL,
                    killer TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS gallos_cementerio_owner "
                "ON gallos_cementerio (guild_id, user_id, id)"
            )

    async def get(self, guild_id: int, user_id: int) -> RoosterRecord:
        """La ficha del gallo de un miembro (la de un gallo nuevo si nunca ha peleado)."""
        return await self._run(self._get_sync, guild_id, user_id)

    def _get_sync(self, guild_id: int, user_id: int) -> RoosterRecord:
        with self._connect() as connection:
            return self._get_in(connection, guild_id, user_id)

    @staticmethod
    def _get_in(connection: sqlite3.Connection, guild_id: int, user_id: int) -> RoosterRecord:
        row = connection.execute(
            "SELECT * FROM gallos WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        ).fetchone()
        return _record(row) if row is not None else RoosterRecord(name=default_name(user_id))

    async def record_fight(
        self,
        guild_id: int,
        user_id: int,
        *,
        won: bool,
        finish: Finish,
        day: date,
        now: float | None = None,
        rival: str | None = None,
    ) -> tuple[RoosterRecord, RoosterRecord]:
        """Apunta una pelea en la ficha del gallo.

        Args:
            now: Instante de la pelea si gasta forma (contra la casa); `None` si no.
            rival: Clave del rival de la casa (lleva el pique); `None` en un duelo.

        Returns:
            La ficha de antes y la de después.
        """
        return await self._run(
            self._record_fight_sync, guild_id, user_id, won, finish, day, now, rival
        )

    def _record_fight_sync(
        self,
        guild_id: int,
        user_id: int,
        won: bool,
        finish: Finish,
        day: date,
        now: float | None,
        rival: str | None,
    ) -> tuple[RoosterRecord, RoosterRecord]:
        with self._connect() as connection:
            before = self._get_in(connection, guild_id, user_id)
            after = after_fight(before, won=won, finish=finish, day=day, now=now, rival=rival)
            self._save_in(connection, guild_id, user_id, after)
        return before, after

    async def change(
        self, guild_id: int, user_id: int, change: Callable[[RoosterRecord], RoosterRecord]
    ) -> tuple[RoosterRecord, RoosterRecord]:
        """Lee la ficha, le aplica `change` (una función pura) y la guarda, en una transacción.

        Para lo que cambia la ficha sin mover dinero: el final del minijuego, los
        eventos del gimnasio y las lesiones.

        Returns:
            La ficha de antes y la de después.
        """
        return await self._run(self._change_sync, guild_id, user_id, change)

    def _change_sync(
        self, guild_id: int, user_id: int, change: Callable[[RoosterRecord], RoosterRecord]
    ) -> tuple[RoosterRecord, RoosterRecord]:
        with self._connect() as connection:
            before = self._get_in(connection, guild_id, user_id)
            after = change(before)
            self._save_in(connection, guild_id, user_id, after)
        return before, after

    async def rename(self, guild_id: int, user_id: int, name: str) -> RoosterRecord:
        """Le cambia el nombre al gallo (ya validado con `gallos.clean_name`)."""
        return await self._run(self._rename_sync, guild_id, user_id, name)

    def _rename_sync(self, guild_id: int, user_id: int, name: str) -> RoosterRecord:
        with self._connect() as connection:
            record = self._get_in(connection, guild_id, user_id)
            renamed = replace(record, name=name)
            self._save_in(connection, guild_id, user_id, renamed)
        return renamed

    @staticmethod
    def _save_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, record: RoosterRecord
    ) -> None:
        connection.execute(
            """
            INSERT INTO gallos
                (guild_id, user_id, name, fights, wins, kos, streak, best_streak,
                 day_streak, last_day, strength, speed, endurance, deaths, last_train,
                 train_day, trains_today, form, form_at, combo, combo_day, injured_until,
                 grudge_rival, grudge_wins, fed_day, fed_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?)
            ON CONFLICT (guild_id, user_id) DO UPDATE SET
                name = excluded.name, fights = excluded.fights, wins = excluded.wins,
                kos = excluded.kos, streak = excluded.streak,
                best_streak = excluded.best_streak, day_streak = excluded.day_streak,
                last_day = excluded.last_day, strength = excluded.strength,
                speed = excluded.speed, endurance = excluded.endurance,
                deaths = excluded.deaths, last_train = excluded.last_train,
                train_day = excluded.train_day, trains_today = excluded.trains_today,
                form = excluded.form, form_at = excluded.form_at,
                combo = excluded.combo, combo_day = excluded.combo_day,
                injured_until = excluded.injured_until,
                grudge_rival = excluded.grudge_rival, grudge_wins = excluded.grudge_wins,
                fed_day = excluded.fed_day, fed_count = excluded.fed_count
            """,
            (
                guild_id,
                user_id,
                record.name,
                record.fights,
                record.wins,
                record.kos,
                record.streak,
                record.best_streak,
                record.day_streak,
                record.last_day.isoformat() if record.last_day else None,
                record.strength,
                record.speed,
                record.endurance,
                record.deaths,
                record.last_train,
                record.train_day.isoformat() if record.train_day else None,
                record.trains_today,
                record.form,
                record.form_at,
                record.combo,
                record.combo_day.isoformat() if record.combo_day else None,
                record.injured_until,
                record.grudge_rival,
                record.grudge_wins,
                record.fed_day.isoformat() if record.fed_day else None,
                record.fed_count,
            ),
        )

    # -- Entrenamiento -----------------------------------------------------------------

    def train(
        self, guild_id: int, user_id: int, stat: Stat, *, level: int, now: float, day: date
    ) -> Callable[[sqlite3.Connection], tuple[RoosterRecord, RoosterRecord]]:
        """Prepara el entrenamiento para `EconomyService.purchase` (el parámetro `reserve`).

        Dentro de la transacción del cobro vuelve a leer la ficha y lo comprueba
        todo otra vez: entre que se enseña el precio y se paga, el gallo puede
        haber subido desde otro panel, o haber llegado al tope del día. Sube el
        stat en la misma transacción, así que no se cobra sin subir ni se sube
        sin cobrar.

        Args:
            level: El valor del stat con el que se calculó el precio.
            now: Instante (epoch) del entrenamiento.
            day: Día canario de `now`.

        Returns:
            Una función que recibe la conexión de la transacción y devuelve
            `(ficha de antes, ficha de después)`, o lanza `TrainError` y se
            deshace todo.
        """

        def run(connection: sqlite3.Connection) -> tuple[RoosterRecord, RoosterRecord]:
            before = self._get_in(connection, guild_id, user_id)
            if before.stat(stat) != level:
                raise TrainError(
                    "Tu gallo ha cambiado mientras mirabas el gimnasio. Ábrelo otra vez."
                )
            check_training(before, stat, now=now, day=day)
            after = after_train(before, stat, now=now, day=day)
            self._save_in(connection, guild_id, user_id, after)
            return before, after

        return run

    def restore(
        self, guild_id: int, user_id: int, record: RoosterRecord
    ) -> Callable[[sqlite3.Connection], None]:
        """Prepara volver la ficha a `record` dentro de otra transacción.

        Para `EconomyService.refund_purchase` (el parámetro `release`): si se
        devuelve un entrenamiento, el stat y la forma vuelven a como estaban.
        """

        def run(connection: sqlite3.Connection) -> None:
            self._save_in(connection, guild_id, user_id, record)

        return run

    # -- Cementerio --------------------------------------------------------------------

    async def bury(
        self, guild_id: int, user_id: int, *, killer_kind: str, killer: str, now: float
    ) -> RoosterRecord:
        """Entierra al gallo del miembro y le da uno nuevo, a cero.

        Su ficha pasa al cementerio (con quién lo mató: `casa` y la clave del
        rival, o `miembro` y su ID) y la fila de `gallos` vuelve a un gallo con
        el nombre de serie, sin historial ni stats (`gallos.reborn`). Todo en una
        transacción.

        Returns:
            La ficha del gallo nuevo.
        """
        return await self._run(self._bury_sync, guild_id, user_id, killer_kind, killer, now)

    def _bury_sync(
        self, guild_id: int, user_id: int, killer_kind: str, killer: str, now: float
    ) -> RoosterRecord:
        with self._connect() as connection:
            dead = self._get_in(connection, guild_id, user_id)
            grave = fallen_from(dead, died_at=now, killer_kind=killer_kind, killer=killer)
            connection.execute(
                """
                INSERT INTO gallos_cementerio
                    (guild_id, user_id, name, fights, wins, kos, strength, speed, endurance,
                     died_at, killer_kind, killer)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    user_id,
                    grave.name,
                    grave.fights,
                    grave.wins,
                    grave.kos,
                    grave.strength,
                    grave.speed,
                    grave.endurance,
                    grave.died_at,
                    grave.killer_kind,
                    grave.killer,
                ),
            )
            fresh = reborn(dead, user_id)
            self._save_in(connection, guild_id, user_id, fresh)
        return fresh

    async def graveyard(self, guild_id: int, user_id: int, limit: int = 3) -> list[Fallen]:
        """Los últimos gallos caídos del miembro, el más reciente primero."""
        return await self._run(self._graveyard_sync, guild_id, user_id, limit)

    def _graveyard_sync(self, guild_id: int, user_id: int, limit: int) -> list[Fallen]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM gallos_cementerio WHERE guild_id = ? AND user_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (guild_id, user_id, limit),
            ).fetchall()
        return [
            Fallen(
                name=str(row["name"]),
                fights=int(row["fights"]),
                wins=int(row["wins"]),
                kos=int(row["kos"]),
                strength=int(row["strength"]),
                speed=int(row["speed"]),
                endurance=int(row["endurance"]),
                died_at=float(row["died_at"]),
                killer_kind=str(row["killer_kind"]),
                killer=str(row["killer"]),
            )
            for row in rows
        ]

    async def ranking(self, guild_id: int, limit: int = 10) -> list[tuple[int, RoosterRecord]]:
        """Los gallos con más victorias del servidor: `(user_id, ficha)`."""
        return await self._run(self._ranking_sync, guild_id, limit)

    def _ranking_sync(self, guild_id: int, limit: int) -> list[tuple[int, RoosterRecord]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM gallos WHERE guild_id = ? AND fights > 0
                ORDER BY wins DESC, best_streak DESC, fights ASC LIMIT ?
                """,
                (guild_id, limit),
            ).fetchall()
        return [(int(row["user_id"]), _record(row)) for row in rows]

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra todo lo guardado de un servidor (al salir el bot de él)."""
        await self._run(self._delete_guild_sync, guild_id)

    def _delete_guild_sync(self, guild_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM gallos WHERE guild_id = ?", (guild_id,))
            connection.execute("DELETE FROM gallos_cementerio WHERE guild_id = ?", (guild_id,))
