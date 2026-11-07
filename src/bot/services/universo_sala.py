"""Salas del Jovaniverso: quién está en el mundo de cada servidor y dónde.

El multijugador sigue «Jovaniverso: rendimiento imperativo» de la Biblia: el servidor
no simula nada, solo valida y reparte. Cada página manda su posición en binario
(`POSE`, 9 bytes) y la sala la guarda. Diez veces por segundo, si algo ha cambiado,
codifica **una vez** un mensaje con todo lo cambiado (`snapshot`) y manda los mismos
bytes a todos. Una sala vacía no tiene tarea.

Protocolo (todo en little-endian):

- Página → servidor, binario: `u8 tipo=1, i16 x, i16 y, i16 z, u8 giro, u8 animación`.
  Posiciones en decímetros; el giro, de 0 a 255 por vuelta entera. La animación: 0
  quieto, 1 andar, 2 correr, 3 saltar y de 4 en adelante los gestos, que se compran en
  la tienda (`bot.services.universo.GESTURES`).
- Servidor → página, binario: `u8 tipo=1, u8 n` y `n` veces `u8 hueco` más la pose de
  arriba sin el tipo (9 bytes por jugador). Va también la pose propia; la página la
  ignora por su hueco.
- Servidor → página, JSON (lo raro): `bienvenida` (tu hueco y quién hay), `entra` y
  `sale`. El hueco (0-254) es el identificador corto de cada jugador en la sala.

Lo que llega de la página no es de fiar: se descarta lo que no mide 9 bytes, lo que
llega a más de `MAX_POSES_PER_SECOND` y lo que se sale del mundo (`WORLD_DM`). Un
gesto que no se ha comprado (o que no existe) se reenvía como quieto.

Cuando alguien empieza un gesto, la sala llama a `Salas.on_gesture` con los que tiene
cerca, para los logros. Es un evento suelto (como mucho uno cada `GESTURE_SECONDS`
por jugador), no algo de cada tick.

No toca Discord ni la base de datos: el cog (`bot.cogs.universo`) comprueba quién es
cada uno y apunta los logros al entrar.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import struct
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

logger = logging.getLogger(__name__)

#: Ticks por segundo de cada sala con gente.
TICK_HZ = 10
#: Poses por segundo que se aceptan de cada página (manda 10; el margen cubre el reloj).
MAX_POSES_PER_SECOND = 15
#: Jugadores por sala. Más no caben en un `u8` de hueco y no compensa dibujarlos.
MAX_PLAYERS = 50
#: Salas a la vez (una por servidor de Discord con alguien dentro).
MAX_ROOMS = 200
#: Lo más lejos del centro que puede estar alguien, en decímetros (la isla mide ~300 m).
WORLD_DM = 3_000
#: Altura mínima y máxima, en decímetros.
MIN_Y_DM, MAX_Y_DM = -500, 2_000
#: Si un cliente tiene más que esto sin mandar, se salta ticks hasta que se ponga al día.
MAX_BACKLOG_BYTES = 32 * 1024

#: Lo que se dice a la conexión vieja cuando la misma persona entra desde otro sitio.
ELSEWHERE = "Has abierto el Jovaniverso en otro sitio: sigues allí."
#: Primera animación que es un gesto.
FIRST_GESTURE = 4
#: Segundos mínimos entre dos gestos que cuentan para los logros de un jugador.
GESTURE_SECONDS = 1.0
#: A cuántos decímetros alguien está «cerca» al hacer un gesto.
NEAR_DM = 80
#: Tipo del mensaje binario de pose.
POSE = 1
_POSE = struct.Struct("<BhhhBB")
_ENTRY = struct.Struct("<BhhhBB")
_HEAD = struct.Struct("<BB")
_PLACE = struct.Struct("<hhh")


class Client(Protocol):
    """Lo que la sala necesita de una conexión (un `WebSocketResponse` de aiohttp)."""

    async def send_bytes(self, data: bytes) -> None: ...
    async def send_str(self, data: str) -> None: ...
    async def close(self) -> object: ...


@dataclass(slots=True)
class Player:
    """Un jugador conectado a una sala."""

    slot: int
    user_id: int
    name: str
    client: Client
    #: Bytes pendientes de mandar por el transporte (0 si no se sabe).
    backlog: Callable[[], int] = lambda: 0
    pose: bytes | None = None
    #: Ventana de un segundo para contar poses: (inicio, cuántas).
    window: tuple[float, int] = (0.0, 0)
    #: Animaciones de gesto que ha comprado.
    gestures: frozenset[int] = frozenset()
    #: Cuándo empezó el último gesto que contó para los logros.
    gestured: float = float("-inf")


#: Lo que se llama al empezar un gesto: servidor, quién, animación y quién hay cerca.
GestureHook = Callable[[int, Player, int, list[Player]], None]


def parse_pose(data: bytes, gestures: frozenset[int] = frozenset()) -> bytes | None:
    """La pose de un mensaje binario de la página, sin el tipo, o `None` si no vale.

    Un gesto que no está en `gestures` (los comprados) sale como quieto.
    """
    if len(data) != _POSE.size:
        return None
    kind, x, y, z, _yaw, anim = _POSE.unpack(data)
    if kind != POSE or abs(x) > WORLD_DM or abs(z) > WORLD_DM or not MIN_Y_DM <= y <= MAX_Y_DM:
        return None
    if anim >= FIRST_GESTURE and anim not in gestures:
        return data[1:-1] + b"\x00"
    return data[1:]


def place(pose: bytes) -> tuple[int, int, int]:
    """Posición `(x, y, z)` en decímetros de una pose guardada."""
    return _PLACE.unpack_from(pose)


def snapshot(entries: list[tuple[int, bytes]]) -> bytes:
    """El mensaje binario de un tick con las poses `(hueco, pose)` que han cambiado."""
    return _HEAD.pack(POSE, len(entries)) + b"".join(
        bytes((slot,)) + pose for slot, pose in entries
    )


@dataclass
class Room:
    """Los jugadores de un servidor y la tarea que reparte sus poses."""

    players: dict[int, Player] = field(default_factory=dict)
    dirty: set[int] = field(default_factory=set)
    task: asyncio.Task[None] | None = None


class SalaError(Exception):
    """No se puede entrar en la sala; el mensaje se puede enseñar tal cual."""


class Salas:
    """Todas las salas del Jovaniverso, una por servidor de Discord.

    Args:
        clock: Reloj en segundos (las pruebas ponen uno falso).
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        on_gesture: GestureHook | None = None,
    ) -> None:
        self.rooms: dict[int, Room] = {}
        self.clock = clock
        self.on_gesture = on_gesture

    async def join(
        self,
        guild_id: int,
        user_id: int,
        name: str,
        client: Client,
        backlog: Callable[[], int] = lambda: 0,
        gestures: frozenset[int] = frozenset(),
    ) -> Player:
        """Mete a alguien en la sala de su servidor y avisa a los demás.

        Si ya estaba (otra pestaña), la conexión vieja sale primero. `gestures` son
        las animaciones de gesto que ha comprado.

        Raises:
            SalaError: Sala llena o demasiadas salas.
        """
        room = self.rooms.get(guild_id)
        if room is None:
            if len(self.rooms) >= MAX_ROOMS:
                raise SalaError("El Jovaniverso está lleno. Prueba en un rato.")
            room = self.rooms[guild_id] = Room()
        old = next((p for p in room.players.values() if p.user_id == user_id), None)
        if old is not None:
            await self.leave(guild_id, old)
            # Con `error`, la página vieja no reintenta: si no, las dos pestañas se
            # echarían la una a la otra sin parar.
            with contextlib.suppress(Exception):
                await old.client.send_str(json.dumps({"t": "error", "error": ELSEWHERE}))
                await old.client.close()
            room = self.rooms.setdefault(guild_id, room)
        free = next((slot for slot in range(MAX_PLAYERS) if slot not in room.players), None)
        if free is None:
            raise SalaError("Esta isla está llena. Prueba en un rato.")
        player = Player(free, user_id, name, client, backlog, gestures=gestures)
        others = list(room.players.values())
        room.players[free] = player
        await client.send_str(
            json.dumps(
                {
                    "t": "bienvenida",
                    "slot": free,
                    "jugadores": [_card(p) for p in others],
                }
            )
        )
        known = [(p.slot, p.pose) for p in others if p.pose is not None]
        if known:
            await client.send_bytes(snapshot(known))  # type: ignore[arg-type]
        await self._broadcast_str(room, json.dumps({"t": "entra", **_card(player)}), skip=player)
        if room.task is None:
            room.task = asyncio.create_task(self._run(guild_id, room))
        return player

    def pose(self, guild_id: int, player: Player, data: bytes) -> None:
        """Guarda la pose que manda la página, si vale y no se pasa de ritmo."""
        room = self.rooms.get(guild_id)
        if room is None or room.players.get(player.slot) is not player:
            return
        now = self.clock()
        start, count = player.window
        if now - start >= 1.0:
            start, count = now, 0
        if count >= MAX_POSES_PER_SECOND:
            return
        player.window = (start, count + 1)
        pose = parse_pose(data, player.gestures)
        if pose is None or pose == player.pose:
            return
        before = player.pose[-1] if player.pose else 0
        player.pose = pose
        room.dirty.add(player.slot)
        anim = pose[-1]
        if (
            anim >= FIRST_GESTURE
            and anim != before
            and self.on_gesture is not None
            and now - player.gestured >= GESTURE_SECONDS
        ):
            player.gestured = now
            self.on_gesture(guild_id, player, anim, near(room, player))

    async def leave(self, guild_id: int, player: Player) -> None:
        """Saca a alguien de la sala y avisa a los demás; la sala vacía se cierra."""
        room = self.rooms.get(guild_id)
        if room is None or room.players.get(player.slot) is not player:
            return
        del room.players[player.slot]
        room.dirty.discard(player.slot)
        if not room.players:
            del self.rooms[guild_id]
            if room.task is not None and room.task is not asyncio.current_task():
                room.task.cancel()
            room.task = None
            return
        await self._broadcast_str(room, json.dumps({"t": "sale", "slot": player.slot}))

    def present(self, guild_id: int) -> list[Player]:
        """Quién hay ahora en la sala de un servidor."""
        room = self.rooms.get(guild_id)
        return list(room.players.values()) if room else []

    async def announce(
        self, guild_id: int, payload: dict[str, object], *, skip_user: int | None = None
    ) -> None:
        """Manda un aviso JSON a todos los de la sala de un servidor (sin sala, nada).

        Con `skip_user`, a todos menos a esa persona.
        """
        room = self.rooms.get(guild_id)
        if room is None:
            return
        skip = next((p for p in room.players.values() if p.user_id == skip_user), None)
        if skip is not None and len(room.players) == 1:
            return
        await self._broadcast_str(room, json.dumps(payload), skip=skip)

    async def close(self) -> None:
        """Para todas las salas (al descargar el cog)."""
        tasks = [room.task for room in self.rooms.values() if room.task is not None]
        self.rooms.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def tick(self, room: Room) -> None:
        """Manda a todos, en un solo mensaje, las poses que han cambiado."""
        if not room.dirty:
            return
        changed = [
            (slot, room.players[slot].pose)
            for slot in sorted(room.dirty)
            if slot in room.players and room.players[slot].pose is not None
        ]
        room.dirty.clear()
        if changed:
            data = snapshot(changed)  # type: ignore[arg-type]
            await self._send_all(room, lambda p: p.client.send_bytes(data))

    async def _run(self, guild_id: int, room: Room) -> None:
        period = 1 / TICK_HZ
        next_at = self.clock() + period
        while self.rooms.get(guild_id) is room:
            await asyncio.sleep(max(0.0, next_at - self.clock()))
            next_at += period
            try:
                await self.tick(room)
            except Exception:
                logger.exception("Fallo en un tick de la sala %s", guild_id)

    async def _broadcast_str(self, room: Room, text: str, *, skip: Player | None = None) -> None:
        await self._send_all(room, lambda p: p.client.send_str(text), skip=skip)

    async def _send_all(
        self,
        room: Room,
        send: Callable[[Player], Awaitable[None]],
        *,
        skip: Player | None = None,
    ) -> None:
        # Por debajo de `MAX_BACKLOG_BYTES` el envío no espera al cliente, así que ir
        # de uno en uno no hace esperar a los demás; al que va atrasado se le salta.
        for player in list(room.players.values()):
            if player is skip or player.backlog() > MAX_BACKLOG_BYTES:
                continue
            try:
                await send(player)
            except (ConnectionError, RuntimeError):
                # Conexión que se está cerrando: su manejador la saca de la sala.
                pass


def near(room: Room, player: Player) -> list[Player]:
    """Los demás de la sala a menos de `NEAR_DM` (en horizontal) de `player`."""
    if player.pose is None:
        return []
    x, _y, z = place(player.pose)
    found = []
    for other in room.players.values():
        if other is player or other.pose is None:
            continue
        ox, _oy, oz = place(other.pose)
        if (ox - x) ** 2 + (oz - z) ** 2 <= NEAR_DM**2:
            found.append(other)
    return found


def _card(player: Player) -> dict[str, object]:
    return {"slot": player.slot, "id": str(player.user_id), "nombre": player.name}
