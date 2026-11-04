"""Almacén de los sonidos de entrada: audios en disco y fichas en SQLite.

Por cada servidor y miembro, en `<base>/<guild_id>/<user_id>/`:

- `<ranura>.source.ogg`: hasta 30 s del audio subido, sin retocar, para
  recortar otra vez, cambiar el efecto o el volumen sin pedir el archivo.
- `<ranura>.ogg`: el clip final, el que se reproduce tal cual.

La ranura 0 es la de salida (`bot.services.entrance.EXIT_SLOT`). No se guarda
el adjunto original ni su nombre.

En la base de datos van la ficha (`entrance_profiles`: mejoras compradas, modo
y ranura activa) y una fila por ranura con sonido (`entrance_slots`: nombre,
volumen, efecto, recorte, onda y veces que ha sonado). Las mejoras se cobran
en la misma transacción que las apunta (`buy`, el `reserve` de
`EconomyService.purchase`).

Antes de las ranuras (9.39.0) cada miembro tenía un solo sonido en
`<base>/<guild_id>/<user_id>.ogg`, con su fuente y un `.json` con el volumen.
`initialize` los pasa a la ranura 1 al arrancar.

Toda la E/S sale del event loop con `asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Callable
from pathlib import Path

from bot.repositories import sqlite
from bot.services.entrance import (
    BASE_TENTHS,
    EXIT_SLOT,
    Mode,
    Offer,
    Profile,
    Slot,
    apply_offer,
    wave_text,
)
from bot.services.entrance_sound import (
    DEFAULT_VOLUME_PERCENT,
    PACKET_SECONDS,
    EntranceSoundError,
    read_opus_packets,
)

logger = logging.getLogger(__name__)

#: Nombre de la ranura 1 de quien ya tenía sonido antes de las ranuras.
LEGACY_NAME = "Mi entrada"

_SLOT_COLUMNS = (
    "slot, name, volume, effect, fade, start_ms, length_ms, source_ms, clip_ms, wave, plays"
)


def _slot_from(row: sqlite3.Row) -> Slot:
    return Slot(
        slot=row["slot"],
        name=row["name"],
        volume=row["volume"],
        effect=row["effect"],
        fade=bool(row["fade"]),
        start_ms=row["start_ms"],
        length_ms=row["length_ms"],
        source_ms=row["source_ms"],
        clip_ms=row["clip_ms"],
        wave=row["wave"],
        plays=row["plays"],
    )


def _mode(value: str) -> Mode:
    try:
        return Mode(value)
    except ValueError:
        return Mode.FIXED


class EntranceSoundStore:
    """Lee, escribe y borra los sonidos de entrada y las fichas de cada miembro.

    Args:
        base_dir: Carpeta de los audios (en Docker, dentro del volumen `.data`).
        database_path: Archivo SQLite del bot.
    """

    def __init__(self, base_dir: Path, database_path: Path) -> None:
        self.base_dir = base_dir
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        return sqlite.connect(self.database_path)

    # -- Rutas -------------------------------------------------------------------------

    def user_dir(self, guild_id: int, user_id: int) -> Path:
        """Carpeta del miembro.

        Los IDs son enteros de Discord, así que no pueden contener `..` ni
        separadores: la ruta no se construye con texto del usuario.
        """
        return self.base_dir / str(int(guild_id)) / str(int(user_id))

    def slot_paths(self, guild_id: int, user_id: int, slot: int) -> tuple[Path, Path]:
        """`(fuente, clip)` de una ranura."""
        directory = self.user_dir(guild_id, user_id)
        stem = str(int(slot))
        return directory / f"{stem}.source.ogg", directory / f"{stem}.ogg"

    def make_work_dir(self) -> tempfile.TemporaryDirectory[str]:
        """Carpeta temporal en el mismo disco, para mover archivos de forma atómica."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(dir=self.base_dir, prefix=".tmp-")

    # -- Arranque ----------------------------------------------------------------------

    async def initialize(self) -> None:
        """Crea las tablas y pasa los sonidos de antes de las ranuras a la ranura 1."""
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        with self._connect() as connection:
            self._create_tables(connection)
        self._migrate_legacy()

    @staticmethod
    def _create_tables(connection: sqlite3.Connection) -> None:
        """Crea las tablas si no existen (no cuesta nada repetirlo)."""
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS entrance_profiles (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                slots INTEGER NOT NULL DEFAULT 1,
                max_tenths INTEGER NOT NULL DEFAULT {BASE_TENTHS},
                exit_owned INTEGER NOT NULL DEFAULT 0,
                mode TEXT NOT NULL DEFAULT '{Mode.FIXED.value}',
                active INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS entrance_slots (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                slot INTEGER NOT NULL,
                name TEXT NOT NULL,
                volume INTEGER NOT NULL,
                effect TEXT NOT NULL,
                fade INTEGER NOT NULL DEFAULT 0,
                start_ms INTEGER NOT NULL DEFAULT 0,
                length_ms INTEGER NOT NULL,
                source_ms INTEGER NOT NULL,
                clip_ms INTEGER NOT NULL,
                wave TEXT NOT NULL DEFAULT '',
                plays INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, slot)
            )
            """
        )

    def _migrate_legacy(self) -> None:
        """Pasa cada `<guild>/<user>.ogg` de antes a `<guild>/<user>/1.ogg`.

        Primero se apunta la fila y luego se mueven los archivos: si el bot se
        corta a mitad, el siguiente arranque encuentra el `.ogg` viejo y termina
        de moverlo (la fila ya está y no se duplica).
        """
        if not self.base_dir.is_dir():
            return
        for guild_dir in self.base_dir.iterdir():
            if not (guild_dir.is_dir() and guild_dir.name.isdigit()):
                continue
            for clip in guild_dir.glob("*.ogg"):
                stem = clip.name.removesuffix(".ogg")
                if stem.isdigit():
                    self._migrate_one(int(guild_dir.name), int(stem), guild_dir)

    def _migrate_one(self, guild_id: int, user_id: int, guild_dir: Path) -> None:
        old_clip = guild_dir / f"{user_id}.ogg"
        old_source = guild_dir / f"{user_id}.source.ogg"
        old_settings = guild_dir / f"{user_id}.json"
        try:
            packets = read_opus_packets(old_clip.read_bytes())
        except (OSError, EntranceSoundError):
            logger.warning("Sonido de entrada antiguo ilegible: %s", old_clip)
            return
        try:
            volume = int(json.loads(old_settings.read_text(encoding="utf-8"))["volume_percent"])
        except (OSError, ValueError, KeyError, TypeError):
            volume = DEFAULT_VOLUME_PERCENT
        clip_ms = round(len(packets) * PACKET_SECONDS * 1000)
        with self._connect() as connection:
            connection.execute(
                f"""
                INSERT OR IGNORE INTO entrance_slots (guild_id, user_id, {_SLOT_COLUMNS})
                VALUES (?, ?, 1, ?, ?, 'normal', 0, 0, ?, ?, ?, ?, 0)
                """,
                (
                    guild_id,
                    user_id,
                    LEGACY_NAME,
                    volume,
                    BASE_TENTHS * 100,
                    clip_ms,
                    clip_ms,
                    wave_text(packets),
                ),
            )
        source, target = self.slot_paths(guild_id, user_id, 1)
        source.parent.mkdir(parents=True, exist_ok=True)
        if old_source.is_file():
            os.replace(old_source, source)
        elif not source.is_file():
            shutil.copyfile(old_clip, source)
        os.replace(old_clip, target)
        old_settings.unlink(missing_ok=True)
        logger.info("Sonido de entrada de %s en %s pasado a la ranura 1", user_id, guild_id)

    # -- Lectura -----------------------------------------------------------------------

    async def load_all(self) -> dict[tuple[int, int], Profile]:
        """Todas las fichas con sus ranuras, para tenerlas en memoria.

        El cog las lee una vez al cargar: así entrar a voz no consulta la base
        ni el disco salvo para leer el clip que suena.
        """
        return await asyncio.to_thread(self._load_all_sync)

    def _load_all_sync(self) -> dict[tuple[int, int], Profile]:
        profiles: dict[tuple[int, int], Profile] = {}
        with self._connect() as connection:
            # Las pruebas cargan el cog sin `initialize`: las tablas, por si acaso.
            self._create_tables(connection)
            for row in connection.execute("SELECT * FROM entrance_profiles"):
                profiles[(row["guild_id"], row["user_id"])] = Profile(
                    slots=row["slots"],
                    max_tenths=row["max_tenths"],
                    exit_owned=bool(row["exit_owned"]),
                    mode=_mode(row["mode"]),
                    active=row["active"],
                )
            for row in connection.execute(
                f"SELECT guild_id, user_id, {_SLOT_COLUMNS} FROM entrance_slots"
            ):
                key = (row["guild_id"], row["user_id"])
                profiles[key] = profiles.get(key, Profile()).with_sound(_slot_from(row))
        return profiles

    async def read_clip(self, guild_id: int, user_id: int, slot: int) -> bytes | None:
        """El clip final de una ranura, o `None` si no está."""
        _source, clip = self.slot_paths(guild_id, user_id, slot)

        def _read() -> bytes | None:
            try:
                return clip.read_bytes()
            except FileNotFoundError:
                return None

        return await asyncio.to_thread(_read)

    # -- Escritura ---------------------------------------------------------------------

    async def save_sound(
        self,
        guild_id: int,
        user_id: int,
        sound: Slot,
        *,
        clip: Path,
        source: Path | None = None,
    ) -> None:
        """Guarda el clip (y la fuente, si cambia) de una ranura y su fila.

        `os.replace` es atómico dentro del mismo disco: si el bot se reinicia a
        mitad, queda el sonido viejo o el nuevo, nunca uno a medio escribir.
        """
        source_path, clip_path = self.slot_paths(guild_id, user_id, sound.slot)

        def _save() -> None:
            clip_path.parent.mkdir(parents=True, exist_ok=True)
            if source is not None:
                os.replace(source, source_path)
            os.replace(clip, clip_path)
            with self._connect() as connection:
                connection.execute(
                    f"""
                    INSERT INTO entrance_slots (guild_id, user_id, {_SLOT_COLUMNS})
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (guild_id, user_id, slot) DO UPDATE SET
                        name = excluded.name, volume = excluded.volume,
                        effect = excluded.effect, fade = excluded.fade,
                        start_ms = excluded.start_ms, length_ms = excluded.length_ms,
                        source_ms = excluded.source_ms, clip_ms = excluded.clip_ms,
                        wave = excluded.wave, plays = excluded.plays
                    """,
                    (
                        guild_id,
                        user_id,
                        sound.slot,
                        sound.name,
                        sound.volume,
                        sound.effect,
                        int(sound.fade),
                        sound.start_ms,
                        sound.length_ms,
                        sound.source_ms,
                        sound.clip_ms,
                        sound.wave,
                        sound.plays,
                    ),
                )

        await asyncio.to_thread(_save)

    async def rename(self, guild_id: int, user_id: int, slot: int, name: str) -> None:
        """Cambia solo el nombre de una ranura (no toca el audio)."""

        def _rename() -> None:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE entrance_slots SET name = ? "
                    "WHERE guild_id = ? AND user_id = ? AND slot = ?",
                    (name, guild_id, user_id, slot),
                )

        await asyncio.to_thread(_rename)

    async def delete_sound(self, guild_id: int, user_id: int, slot: int) -> bool:
        """Borra el audio y la fila de una ranura. Devuelve si había algo."""
        paths = self.slot_paths(guild_id, user_id, slot)

        def _delete() -> bool:
            with self._connect() as connection:
                removed = connection.execute(
                    "DELETE FROM entrance_slots WHERE guild_id = ? AND user_id = ? AND slot = ?",
                    (guild_id, user_id, slot),
                ).rowcount
            for path in paths:
                path.unlink(missing_ok=True)
            return bool(removed)

        return await asyncio.to_thread(_delete)

    async def save_settings(self, guild_id: int, user_id: int, profile: Profile) -> None:
        """Guarda el modo y la ranura activa (lo que no se compra)."""

        def _save() -> None:
            with self._connect() as connection:
                self._upsert_profile(connection, guild_id, user_id, profile)

        await asyncio.to_thread(_save)

    async def add_play(self, guild_id: int, user_id: int, slot: int) -> None:
        """Suma una reproducción a la ranura (para el ranking)."""

        def _add() -> None:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE entrance_slots SET plays = plays + 1 "
                    "WHERE guild_id = ? AND user_id = ? AND slot = ?",
                    (guild_id, user_id, slot),
                )

        await asyncio.to_thread(_add)

    def buy(
        self, guild_id: int, user_id: int, offer: Offer
    ) -> Callable[[sqlite3.Connection], Profile]:
        """Prepara una mejora para `EconomyService.purchase` (su `reserve`).

        Dentro de la transacción del cobro vuelve a leer la ficha y comprueba
        que la oferta sigue siendo la siguiente: si el miembro ha comprado lo
        mismo desde otro panel, `apply_offer` lanza y no se cobra nada. La
        mejora se apunta en la misma transacción que el cobro.

        Returns:
            Una función que recibe la conexión y devuelve la ficha nueva, sin
            los sonidos (el cog los tiene en memoria).
        """

        def run(connection: sqlite3.Connection) -> Profile:
            row = connection.execute(
                "SELECT * FROM entrance_profiles WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            before = (
                Profile(
                    slots=row["slots"],
                    max_tenths=row["max_tenths"],
                    exit_owned=bool(row["exit_owned"]),
                    mode=_mode(row["mode"]),
                    active=row["active"],
                )
                if row is not None
                else Profile()
            )
            after = apply_offer(before, offer)
            self._upsert_profile(connection, guild_id, user_id, after)
            return after

        return run

    @staticmethod
    def _upsert_profile(
        connection: sqlite3.Connection, guild_id: int, user_id: int, profile: Profile
    ) -> None:
        connection.execute(
            """
            INSERT INTO entrance_profiles
                (guild_id, user_id, slots, max_tenths, exit_owned, mode, active)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (guild_id, user_id) DO UPDATE SET
                slots = excluded.slots, max_tenths = excluded.max_tenths,
                exit_owned = excluded.exit_owned, mode = excluded.mode,
                active = excluded.active
            """,
            (
                guild_id,
                user_id,
                profile.slots,
                profile.max_tenths,
                int(profile.exit_owned),
                profile.mode.value,
                profile.active,
            ),
        )


__all__ = ["EXIT_SLOT", "LEGACY_NAME", "EntranceSoundStore"]
