"""Persistencia de las mascotas: su estado y cuál lleva cada dueño.

Quién tiene qué mascota no se guarda aquí: una mascota es una fila de la
mochila de la tienda (`shop_inventory`, tipo `mascota`), igual que cualquier
compra, así que la venta, el IGIC y el patrimonio siguen funcionando sin
cambios. Este repositorio guarda lo que la tienda no sabe:

- `pets`: el estado de cada mascota, con el mismo `id` que su fila de la
  mochila: nombre, vínculo y cuidados (`bot.services.pets.PetState`). La fila
  se crea la primera vez que se mira la mascota.
- `pet_owners`: la mascota activa de cada miembro y su racha de días
  cuidando.
- `pet_outfits`: lo que lleva puesto cada mascota, una prenda por hueco
  (`bot.services.pet_wardrobe`). Guarda la clave del artículo; si su dueño ya
  no lo tiene (lo vendió), no se ve, sin tener que borrarlo al venderlo.
- `pet_photos`: el álbum de cada mascota. Cada foto guarda el estado del
  retrato tal como era (`bot.services.pet_scene`), no la imagen: se vuelve a
  pintar al mirarla y ocupa ~1 KB.

Mismo archivo SQLite que el resto del bot. Solo se guardan IDs, nombres de
mascota y marcas de tiempo; todo se borra cuando el bot sale del servidor.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from bot.repositories import sqlite
from bot.services.pets import ALBUM_SIZE, PetState
from bot.services.pets_catalog import CATALOG_PREFIX, species_of

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Owner:
    """Lo que se guarda de cada dueño.

    Attributes:
        active_id: Mascota que lleva consigo (`None` si ninguna).
        streak: Días seguidos cuidando alguna mascota, hasta `last_day`.
        best_streak: Racha más larga.
        last_day: Último día con algún cuidado (`bot.services.pets.care_day`).
    """

    active_id: int | None = None
    streak: int = 0
    best_streak: int = 0
    last_day: str = ""


def _state(row: sqlite3.Row) -> PetState:
    return PetState(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        user_id=int(row["user_id"]),
        species=str(row["species"]),
        name=str(row["name"]),
        bond=int(row["bond"]),
        adopted_at=float(row["adopted_at"]),
        day=str(row["day"]),
        today={k: int(v) for k, v in json.loads(row["today"]).items()},
        last={k: float(v) for k, v in json.loads(row["last"]).items()},
        totals={k: int(v) for k, v in json.loads(row["totals"]).items()},
        breed=str(row["breed"] or ""),
        celebrated=int(row["celebrated"] or 0),
    )


@dataclass(frozen=True, slots=True)
class PhotoRow:
    """Una foto del álbum.

    Attributes:
        reason: `bot.services.pets.Photo`.
        caption: Pie de foto ya escrito.
        state: Estado del retrato tal como se pintó (`bot.services.pet_scene`).
    """

    id: int
    pet_id: int
    taken_at: float
    reason: str
    caption: str
    state: dict[str, Any]


class PetRepository:
    """Acceso SQLite al estado de las mascotas.

    Args:
        database_path: Ruta del archivo SQLite persistente (el de la tienda).
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
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS pets (
                    id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    species TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    bond INTEGER NOT NULL DEFAULT 0 CHECK (bond >= 0),
                    adopted_at REAL NOT NULL,
                    day TEXT NOT NULL DEFAULT '',
                    today TEXT NOT NULL DEFAULT '{}',
                    last TEXT NOT NULL DEFAULT '{}',
                    totals TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS pets_owner ON pets (guild_id, user_id);

                CREATE TABLE IF NOT EXISTS pet_outfits (
                    pet_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    slot TEXT NOT NULL,
                    catalog_key TEXT NOT NULL,
                    PRIMARY KEY (pet_id, slot)
                );

                CREATE TABLE IF NOT EXISTS pet_photos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pet_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    taken_at REAL NOT NULL,
                    reason TEXT NOT NULL,
                    caption TEXT NOT NULL,
                    state TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS pet_photos_pet ON pet_photos (pet_id, id);

                CREATE TABLE IF NOT EXISTS pet_owners (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    active_id INTEGER,
                    streak INTEGER NOT NULL DEFAULT 0,
                    best_streak INTEGER NOT NULL DEFAULT 0,
                    last_day TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (guild_id, user_id)
                );
                """
            )
            # Columnas de la 9.43 (raza y aniversario) en las bases de datos de antes.
            have = {row[1] for row in connection.execute("PRAGMA table_info(pets)")}
            for name, ddl in (
                ("breed", "TEXT NOT NULL DEFAULT ''"),
                ("celebrated", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in have:
                    # `name` y `ddl` salen de una tupla fija, nunca de entrada del usuario.
                    connection.execute(f"ALTER TABLE pets ADD COLUMN {name} {ddl}")
            connection.commit()
        finally:
            connection.close()

    # -- Mascotas --------------------------------------------------------------------

    async def pets(self, guild_id: int, user_id: int) -> list[PetState]:
        """Mascotas de un miembro, de la más antigua a la más nueva.

        Crea el estado de las que acaba de conseguir (adoptadas o aparecidas).
        Las filas de la mochila de una especie que ya no existe se ignoran.
        """
        return await self._run(self._pets_sync, guild_id, user_id)

    def _pets_sync(self, guild_id: int, user_id: int) -> list[PetState]:
        connection = self._connect()
        try:
            with connection:
                owned = connection.execute(
                    """
                    SELECT id, catalog_key, starts_at FROM shop_inventory
                    WHERE guild_id = ? AND user_id = ? AND kind = 'mascota'
                      AND status = 'active'
                    ORDER BY id
                    """,
                    (guild_id, user_id),
                ).fetchall()
                for row in owned:
                    species = species_of(row["catalog_key"])
                    if species is None:
                        continue
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO pets (id, guild_id, user_id, species, adopted_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (row["id"], guild_id, user_id, species.key, row["starts_at"]),
                    )
                rows = connection.execute(
                    """
                    SELECT p.* FROM pets p
                    JOIN shop_inventory i ON i.id = p.id AND i.status = 'active'
                    WHERE p.guild_id = ? AND p.user_id = ?
                    ORDER BY p.id
                    """,
                    (guild_id, user_id),
                ).fetchall()
        finally:
            connection.close()
        return [_state(row) for row in rows]

    async def owned_species(self, guild_id: int, user_id: int) -> set[str]:
        """Especies que tiene un miembro (aunque aún no se haya mirado su estado)."""
        return await self._run(self._owned_sync, guild_id, user_id)

    def _owned_sync(self, guild_id: int, user_id: int) -> set[str]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT catalog_key FROM shop_inventory
                WHERE guild_id = ? AND user_id = ? AND kind = 'mascota' AND status = 'active'
                """,
                (guild_id, user_id),
            ).fetchall()
        finally:
            connection.close()
        return {
            str(row[0]).removeprefix(CATALOG_PREFIX)
            for row in rows
            if species_of(row[0]) is not None
        }

    async def save(self, state: PetState) -> None:
        """Guarda el nombre, el vínculo y los cuidados de una mascota."""
        await self._run(self._save_sync, state)

    def _save_sync(self, state: PetState) -> None:
        connection = self._connect()
        try:
            with connection:
                self._save_in(connection, state)
        finally:
            connection.close()

    @staticmethod
    def _save_in(connection: sqlite3.Connection, state: PetState) -> None:
        connection.execute(
            """
            UPDATE pets SET name = ?, bond = ?, day = ?, today = ?, last = ?, totals = ?,
                breed = ?, celebrated = ?
            WHERE id = ? AND guild_id = ? AND user_id = ?
            """,
            (
                state.name,
                state.bond,
                state.day,
                json.dumps(state.today),
                json.dumps(state.last),
                json.dumps(state.totals),
                state.breed,
                state.celebrated,
                state.id,
                state.guild_id,
                state.user_id,
            ),
        )

    # -- Dueños ----------------------------------------------------------------------

    async def owner(self, guild_id: int, user_id: int) -> Owner:
        """La mascota activa y la racha de un miembro (vacío si nunca ha tenido)."""
        return await self._run(self._owner_sync, guild_id, user_id)

    def _owner_sync(self, guild_id: int, user_id: int) -> Owner:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM pet_owners WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return Owner()
        return Owner(
            active_id=row["active_id"],
            streak=int(row["streak"]),
            best_streak=int(row["best_streak"]),
            last_day=str(row["last_day"]),
        )

    async def set_active(self, guild_id: int, user_id: int, pet_id: int | None) -> None:
        """Elige la mascota que lleva consigo un miembro (`None`: ninguna)."""
        await self._run(self._set_active_sync, guild_id, user_id, pet_id)

    def _set_active_sync(self, guild_id: int, user_id: int, pet_id: int | None) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """
                    INSERT INTO pet_owners (guild_id, user_id, active_id) VALUES (?, ?, ?)
                    ON CONFLICT (guild_id, user_id) DO UPDATE SET active_id = excluded.active_id
                    """,
                    (guild_id, user_id, pet_id),
                )
        finally:
            connection.close()

    async def save_care(self, state: PetState, *, streak: int, best_streak: int, day: str) -> None:
        """Guarda un cuidado: la mascota y la racha del dueño, juntos o nada."""
        await self._run(self._save_care_sync, state, streak, best_streak, day)

    def _save_care_sync(self, state: PetState, streak: int, best_streak: int, day: str) -> None:
        connection = self._connect()
        try:
            with connection:
                self._save_in(connection, state)
                connection.execute(
                    """
                    INSERT INTO pet_owners (guild_id, user_id, streak, best_streak, last_day)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (guild_id, user_id) DO UPDATE SET
                        streak = excluded.streak,
                        best_streak = MAX(pet_owners.best_streak, excluded.best_streak),
                        last_day = excluded.last_day
                    """,
                    (state.guild_id, state.user_id, streak, best_streak, day),
                )
        finally:
            connection.close()

    async def active_pets(self) -> list[PetState]:
        """La mascota activa de cada dueño, de todos los servidores (para la caché)."""
        return await self._run(self._active_sync)

    def _active_sync(self) -> list[PetState]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT p.* FROM pet_owners o
                JOIN pets p ON p.id = o.active_id AND p.guild_id = o.guild_id
                    AND p.user_id = o.user_id
                JOIN shop_inventory i ON i.id = p.id AND i.status = 'active'
                """
            ).fetchall()
        finally:
            connection.close()
        return [_state(row) for row in rows]

    # -- Armario ---------------------------------------------------------------------

    async def outfits(self, pet_ids: list[int]) -> dict[int, dict[str, str]]:
        """Lo que lleva puesto cada mascota: id → hueco → clave del artículo."""
        if not pet_ids:
            return {}
        return await self._run(self._outfits_sync, list(pet_ids))

    def _outfits_sync(self, pet_ids: list[int]) -> dict[int, dict[str, str]]:
        connection = self._connect()
        try:
            marks = ",".join("?" * len(pet_ids))
            rows = connection.execute(
                f"SELECT pet_id, slot, catalog_key FROM pet_outfits WHERE pet_id IN ({marks})",
                pet_ids,
            ).fetchall()
        finally:
            connection.close()
        out: dict[int, dict[str, str]] = {pet_id: {} for pet_id in pet_ids}
        for row in rows:
            out[int(row["pet_id"])][str(row["slot"])] = str(row["catalog_key"])
        return out

    async def wear(self, state: PetState, slot: str, catalog_key: str | None) -> None:
        """Le pone a una mascota la prenda `catalog_key` en `slot` (`None`: se la quita)."""
        await self._run(self._wear_sync, state, slot, catalog_key)

    def _wear_sync(self, state: PetState, slot: str, catalog_key: str | None) -> None:
        connection = self._connect()
        try:
            with connection:
                if catalog_key is None:
                    connection.execute(
                        "DELETE FROM pet_outfits WHERE pet_id = ? AND slot = ?", (state.id, slot)
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO pet_outfits (pet_id, guild_id, user_id, slot, catalog_key)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT (pet_id, slot) DO UPDATE SET catalog_key = excluded.catalog_key
                        """,
                        (state.id, state.guild_id, state.user_id, slot, catalog_key),
                    )
        finally:
            connection.close()

    # -- Álbum -------------------------------------------------------------------------

    async def add_photo(
        self, state: PetState, *, taken_at: float, reason: str, caption: str, portrait: dict
    ) -> None:
        """Guarda una foto en el álbum de una mascota (y borra las que pasen de `ALBUM_SIZE`)."""
        await self._run(self._add_photo_sync, state, taken_at, reason, caption, portrait)

    def _add_photo_sync(
        self, state: PetState, taken_at: float, reason: str, caption: str, portrait: dict
    ) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """
                    INSERT INTO pet_photos (pet_id, guild_id, user_id, taken_at, reason, caption,
                        state)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.id,
                        state.guild_id,
                        state.user_id,
                        taken_at,
                        reason,
                        caption,
                        json.dumps(portrait, sort_keys=True),
                    ),
                )
                connection.execute(
                    """
                    DELETE FROM pet_photos WHERE pet_id = ? AND id NOT IN (
                        SELECT id FROM pet_photos WHERE pet_id = ? ORDER BY id DESC LIMIT ?
                    )
                    """,
                    (state.id, state.id, ALBUM_SIZE),
                )
        finally:
            connection.close()

    async def photos(self, pet_id: int) -> list[PhotoRow]:
        """El álbum de una mascota, de la foto más vieja a la más nueva."""
        return await self._run(self._photos_sync, pet_id)

    def _photos_sync(self, pet_id: int) -> list[PhotoRow]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM pet_photos WHERE pet_id = ? ORDER BY id", (pet_id,)
            ).fetchall()
        finally:
            connection.close()
        return [
            PhotoRow(
                id=int(row["id"]),
                pet_id=int(row["pet_id"]),
                taken_at=float(row["taken_at"]),
                reason=str(row["reason"]),
                caption=str(row["caption"]),
                state=json.loads(row["state"]),
            )
            for row in rows
        ]

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra las mascotas y los dueños de un servidor."""
        await self._run(self._delete_guild_sync, guild_id)

    def _delete_guild_sync(self, guild_id: int) -> None:
        connection = self._connect()
        try:
            with connection:
                for table in ("pets", "pet_owners", "pet_outfits", "pet_photos"):
                    # `table` sale de una tupla fija, nunca de entrada del usuario.
                    connection.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
        finally:
            connection.close()
