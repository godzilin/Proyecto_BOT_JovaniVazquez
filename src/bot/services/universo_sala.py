"""Salas del Jovaniverso: quién está en el mundo de cada servidor y dónde.

El multijugador sigue «Jovaniverso: rendimiento imperativo» de la Biblia: el servidor
no simula nada, solo valida y reparte. Cada página manda su posición en binario
(`POSE`, 9 bytes) y la sala la guarda. Diez veces por segundo, si algo ha cambiado,
codifica **una vez** un mensaje con todo lo cambiado (`snapshot`) y manda los mismos
bytes a todos. Una sala vacía no tiene tarea.

Protocolo (todo en little-endian):

- Página → servidor, binario: `u8 tipo=1, i16 x, i16 y, i16 z, u8 giro, u8 animación`.
  Posiciones en decímetros; el giro, de 0 a 255 por vuelta entera. La animación: 0
  quieto, 1 andar, 2 correr, 3 saltar, de 4 a 10 los gestos, que se compran en la
  tienda (`bot.services.universo.GESTURES`), y las posturas de moverse (`MOVES`: 11
  agachado, 12 deslizándose), que son de todos.
- Servidor → página, binario: `u8 tipo=1, u8 n` y `n` veces `u8 hueco` más la pose de
  arriba sin el tipo (9 bytes por jugador). Va también la pose propia; la página la
  ignora por su hueco.
- Servidor → página, JSON (lo raro): `bienvenida` (tu hueco, tu id para teñir tu robot
  del mismo color que te ven los demás, y quién hay), `entra` y `sale`. El hueco
  (0-254) es el identificador corto de cada jugador en la sala.
- Página → servidor, JSON: `{t: "salto"}` justo antes de la pose de un teleporte (el
  menú «Viajar», sentarse a una máquina, las devtools) y `{t: "trucos", on}` al poner o
  quitar una devtool (volar, noclip, velocidad).

Lo que llega de la página no es de fiar: se descarta lo que no mide 9 bytes, lo que
llega a más de `MAX_POSES_PER_SECOND` y lo que se sale del mundo (`WORLD_DM`). Un
gesto que no se ha comprado (o que no existe) se reenvía como quieto. Y nadie va más
rápido de lo que deja la física de la página (`_move`): cada jugador tiene un
presupuesto de decímetros que se rellena a `RUN_DM_PER_SECOND` (y otro para subir, a
`CLIMB_DM_PER_SECOND`; bajar, que es caer, no tiene tope) hasta `SLACK_SECONDS` de
reserva, que cubre las poses que llegan juntas tras un parón de la red. Una pose que se
pasa se recorta hasta donde llega el presupuesto: quien se teletransporta sin avisar se
ve correr hacia allí a la velocidad máxima. Tras un `salto` aceptado (uno cada
`LEAP_SECONDS`, el ritmo de teleportes del informe) la pose siguiente va sin mirar. Con
`check=False` (el extraplano de los desarrolladores, donde se vuela) no se mira nada.

Cuando alguien empieza un gesto, la sala llama a `Salas.on_gesture` con los que tiene
cerca, para los logros. Es un evento suelto (como mucho uno cada `GESTURE_SECONDS`
por jugador), no algo de cada tick.

El paseo para los logros lo cuenta la sala con las poses que acepta, no la página: los
metros en horizontal, los saltos (pasar a la animación de saltar), los deslizamientos
(pasar a la de deslizarse) y los lugares que pisa (`Places`, del mundo publicado, que el
cog pone con `set_places`). En cada pose son unas restas y comparaciones; cada
`WALK_SECONDS` (en la pose siguiente) y al salir se entrega el lote a `Salas.on_walk`,
con los saltos y deslizamientos recortados a lo que da tiempo a hacer. Lo pisado al
llegar de un teleporte no cuenta, ni nada con una devtool puesta.

No toca Discord ni la base de datos: el cog (`bot.cogs.universo`) comprueba quién es
cada uno y apunta los logros al entrar.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import struct
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from bot.services.universo import MAX_TELEPORTS_PER_SECOND, SERIE_PLACES, Places

logger = logging.getLogger(__name__)

#: Ticks por segundo de cada sala con gente.
TICK_HZ = 10
#: Poses por segundo que se aceptan de cada página (manda 10; el margen cubre el reloj).
MAX_POSES_PER_SECOND = 15
#: Jugadores por sala. Más no caben en un `u8` de hueco y no compensa dibujarlos.
MAX_PLAYERS = 50
#: Salas a la vez (una por servidor de Discord con alguien dentro).
MAX_ROOMS = 200
#: Lo más lejos del centro que puede estar alguien, en decímetros (la isla mide ~300 m). La
#: página tiene el mismo borde (`universo/src/mundo/borde.ts`, `test_universo_borde_ts.py`).
WORLD_DM = 3_000
#: Altura mínima y máxima, en decímetros.
MIN_Y_DM, MAX_Y_DM = -500, 2_000
#: Si un cliente tiene más que esto sin mandar, se salta ticks hasta que se ponga al día.
MAX_BACKLOG_BYTES = 32 * 1024

#: Lo que se dice a la conexión vieja cuando la misma persona entra desde otro sitio.
ELSEWHERE = "Has abierto el Jovaniverso en otro sitio: sigues allí."
#: Primera animación que es un gesto.
FIRST_GESTURE = 4
#: Posturas de moverse (agachado y deslizándose): no se compran ni avisan como gesto.
MOVES = frozenset({11, 12})
#: Segundos mínimos entre dos gestos que cuentan para los logros de un jugador.
GESTURE_SECONDS = 1.0
#: A cuántos decímetros alguien está «cerca» al hacer un gesto.
NEAR_DM = 80
#: A cuántos decímetros se ve la tirada de una tragaperras. La página no pinta pantallas a
#: más de 45 m (`DISTANCIA_PANTALLAS` de `casino.ts`); 15 m más cubren a quien se acerca
#: corriendo (9 m/s) mientras dura la tirada.
SPIN_SEEN_DM = 600
#: Lo más rápido que se avanza en horizontal sin devtools, en dm/s: `VEL_MAX` (20 m/s) de
#: `universo/src/mundo/jugador.ts`, que recorta la velocidad después de correr, deslizarse,
#: la pared y el gancho, más un 20 % por los empujones de las colisiones y el reloj
#: (`docs/simular_movimiento.mjs` mide lo más que se mueve en una pose).
RUN_DM_PER_SECOND = 240
#: Lo más rápido que se sube, en dm/s: el gancho tira hacia arriba a 14 m/s y deslizándose a
#: 20 m/s por la ladera más empinada que deja subir (pendiente 1,5) se sube a 30.
CLIMB_DM_PER_SECOND = 330
#: Segundos de movimiento que se pueden guardar: las poses que llegan juntas tras un parón
#: de la red (TCP las entrega de golpe) no se recortan.
SLACK_SECONDS = 2.0
#: Segundos entre dos teleportes que se dejan pasar sin mirar la velocidad.
LEAP_SECONDS = 1 / MAX_TELEPORTS_PER_SECOND
#: Lo que vale un aviso de teleporte que aún no ha llegado a usarse, en segundos.
LEAP_VALID_SECONDS = 1.0
#: Animaciones de saltar y de deslizarse: pasar a ellas es un salto o un deslizamiento.
JUMP, SLIDE = 3, 12
#: Saltos y deslizamientos por segundo que se cuentan: un salto dura casi un segundo en el
#: aire y el impulso de deslizarse se recarga en uno.
MAX_JUMPS_PER_SECOND = 2
MAX_SLIDES_PER_SECOND = 1
#: Cada cuántos segundos se entrega el paseo de alguien a los logros (como el informe).
WALK_SECONDS = 15.0
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
    #: Canal desde el que abrió el Jovaniverso (para avisar de los logros), si se sabe.
    channel_id: int | None = None
    #: Presupuesto de movimiento (`_move`): decímetros en horizontal y subiendo (al entrar,
    #: llenos) y cuándo se rellenaron por última vez (`None` hasta la primera pose).
    budget: float = RUN_DM_PER_SECOND * SLACK_SECONDS
    climb: float = CLIMB_DM_PER_SECOND * SLACK_SECONDS
    moved_at: float | None = None
    #: Cuándo se aceptó el último aviso de teleporte y si está sin usar.
    leaped: float = float("-inf")
    leap: bool = False
    #: Si tiene alguna devtool puesta: no cuenta paseo.
    tricks: bool = False
    #: Paseo desde el último lote (`on_walk`): decímetros, saltos, deslizamientos, lugares
    #: nuevos, los ya pisados en esta visita y cuándo salió el último lote.
    walked: float = 0.0
    jumps: int = 0
    slides: int = 0
    found: list[str] = field(default_factory=list)
    visited: set[str] = field(default_factory=set)
    flushed: float = 0.0


@dataclass(frozen=True, slots=True)
class Stroll:
    """Lo paseado por alguien desde el último lote, ya recortado (`Salas.on_walk`)."""

    meters: int
    jumps: int
    slides: int
    places: list[str]


#: Lo que se llama al empezar un gesto: servidor, quién, animación y quién hay cerca.
GestureHook = Callable[[int, Player, int, list[Player]], None]
#: Lo que se llama con cada lote de paseo: servidor, quién y lo paseado.
WalkHook = Callable[[int, Player, Stroll], None]


def parse_pose(data: bytes, gestures: frozenset[int] = frozenset()) -> bytes | None:
    """La pose de un mensaje binario de la página, sin el tipo, o `None` si no vale.

    Un gesto que no está en `gestures` (los comprados) sale como quieto.
    """
    if len(data) != _POSE.size:
        return None
    kind, x, y, z, _yaw, anim = _POSE.unpack(data)
    if kind != POSE or abs(x) > WORLD_DM or abs(z) > WORLD_DM or not MIN_Y_DM <= y <= MAX_Y_DM:
        return None
    if anim >= FIRST_GESTURE and anim not in gestures and anim not in MOVES:
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
    #: Dónde están los lugares de los logros en el mundo publicado de su servidor.
    places: Places = SERIE_PLACES


class SalaError(Exception):
    """No se puede entrar en la sala; el mensaje se puede enseñar tal cual."""


class Salas:
    """Todas las salas del Jovaniverso, una por servidor de Discord.

    Args:
        clock: Reloj en segundos (las pruebas ponen uno falso).
        on_gesture: Se llama al empezar un gesto (logros).
        on_walk: Se llama con cada lote de paseo (logros). Sin él, no se cuenta nada.
        check: Si se mira que nadie vaya más rápido de lo que deja la física.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        on_gesture: GestureHook | None = None,
        on_walk: WalkHook | None = None,
        check: bool = True,
    ) -> None:
        self.rooms: dict[int, Room] = {}
        self.clock = clock
        self.on_gesture = on_gesture
        self.on_walk = on_walk
        self.check = check

    async def join(
        self,
        guild_id: int,
        user_id: int,
        name: str,
        client: Client,
        backlog: Callable[[], int] = lambda: 0,
        gestures: frozenset[int] = frozenset(),
        *,
        places: Places | None = None,
        channel_id: int | None = None,
    ) -> Player:
        """Mete a alguien en la sala de su servidor y avisa a los demás.

        Si ya estaba (otra pestaña), la conexión vieja sale primero. `gestures` son
        las animaciones de gesto que ha comprado; `places`, los lugares del mundo
        publicado (como `set_places`), y `channel_id`, desde dónde abrió.

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
        if places is not None:
            room.places = places
        player = Player(
            free,
            user_id,
            name,
            client,
            backlog,
            gestures=gestures,
            channel_id=channel_id,
            flushed=self.clock(),
        )
        others = list(room.players.values())
        room.players[free] = player
        await client.send_str(
            json.dumps(
                {
                    "t": "bienvenida",
                    "slot": free,
                    "id": str(user_id),
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
        """Guarda la pose que manda la página, si vale y no se pasa de ritmo ni de velocidad."""
        room = self.rooms.get(guild_id)
        now = self.clock()
        if room is None or not _allowed(room, player, now):
            return
        pose = parse_pose(data, player.gestures)
        if pose is None or pose == player.pose:
            return
        last = player.pose
        moved: float | None = 0.0
        if last is not None:
            if self.check:
                pose, moved = _move(player, last, pose, now)
                if pose == last:
                    return
            else:
                (x0, _y0, z0), (x, _y, z) = place(last), place(pose)
                moved = math.hypot(x - x0, z - z0)
        player.moved_at = now
        player.pose = pose
        room.dirty.add(player.slot)
        if self.on_walk is not None and not player.tricks:
            self._walk(guild_id, room, player, last, pose, moved, now)
        before = last[-1] if last else 0
        anim = pose[-1]
        if (
            anim >= FIRST_GESTURE
            and anim not in MOVES
            and anim != before
            and self.on_gesture is not None
            and now - player.gestured >= GESTURE_SECONDS
        ):
            player.gestured = now
            self.on_gesture(guild_id, player, anim, near(room, player))

    def text(self, guild_id: int, player: Player, data: str) -> None:
        """Atiende un mensaje JSON de la página: un aviso de teleporte o de devtools.

        Cuenta para el mismo tope de mensajes por segundo que las poses. Un `salto` que
        llega antes de `LEAP_SECONDS` desde el último aceptado no vale: esa pose se
        recorta como cualquier otra.
        """
        room = self.rooms.get(guild_id)
        now = self.clock()
        if room is None or len(data) > 64 or not _allowed(room, player, now):
            return
        try:
            message = json.loads(data)
        except ValueError:
            return
        if not isinstance(message, dict):
            return
        if message.get("t") == "salto" and now - player.leaped >= LEAP_SECONDS:
            player.leaped, player.leap = now, True
        elif message.get("t") == "trucos":
            player.tricks = message.get("on") is True

    def set_places(self, guild_id: int, places: Places) -> None:
        """Pone los lugares del mundo recién publicado en la sala de su servidor."""
        if (room := self.rooms.get(guild_id)) is not None:
            room.places = places

    def places_at(self, guild_id: int, player: Player) -> list[str]:
        """Los lugares donde está ahora alguien (ninguno sin pose o sin sala)."""
        room = self.rooms.get(guild_id)
        if room is None or player.pose is None:
            return []
        return room.places.at(*place(player.pose))

    def _walk(
        self,
        guild_id: int,
        room: Room,
        player: Player,
        last: bytes | None,
        pose: bytes,
        moved: float | None,
        now: float,
    ) -> None:
        """Suma la pose al paseo de alguien y entrega el lote si toca. `moved` es `None` al
        llegar de un teleporte: ni metros ni lugares."""
        if moved is not None:
            player.walked += moved
            if last is not None and pose[-1] != last[-1]:
                if pose[-1] == JUMP:
                    player.jumps += 1
                elif pose[-1] == SLIDE:
                    player.slides += 1
        for key in room.places.at(*place(pose)):
            if key not in player.visited:
                player.visited.add(key)
                if moved is not None:
                    player.found.append(key)
        if now - player.flushed >= WALK_SECONDS:
            self._flush(guild_id, player, now)

    def _flush(self, guild_id: int, player: Player, now: float) -> None:
        """Entrega a `on_walk` lo paseado desde el último lote, recortado al tiempo pasado."""
        elapsed = now - player.flushed
        player.flushed = now
        meters = int(player.walked // 10)
        player.walked -= meters * 10
        stroll = Stroll(
            meters=meters,
            jumps=min(player.jumps, int(elapsed * MAX_JUMPS_PER_SECOND)),
            slides=min(player.slides, int(elapsed * MAX_SLIDES_PER_SECOND)),
            places=player.found,
        )
        player.jumps = player.slides = 0
        player.found = []
        if self.on_walk is not None and (
            stroll.meters or stroll.jumps or stroll.slides or stroll.places
        ):
            self.on_walk(guild_id, player, stroll)

    async def leave(self, guild_id: int, player: Player) -> None:
        """Saca a alguien de la sala y avisa a los demás; la sala vacía se cierra."""
        room = self.rooms.get(guild_id)
        if room is None or room.players.get(player.slot) is not player:
            return
        self._flush(guild_id, player, self.clock())
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
        self,
        guild_id: int,
        payload: dict[str, object],
        *,
        skip_user: int | None = None,
        around_dm: int | None = None,
    ) -> None:
        """Manda un aviso JSON a todos los de la sala de un servidor (sin sala, nada).

        Con `skip_user`, a todos menos a esa persona. Con `around_dm`, solo a quienes
        están a esa distancia (en horizontal) de `skip_user`; si no está en la sala o aún
        no ha mandado su pose, a nadie: no se sabe dónde ocurre.
        """
        room = self.rooms.get(guild_id)
        if room is None:
            return
        skip = next((p for p in room.players.values() if p.user_id == skip_user), None)
        if skip is not None and len(room.players) == 1:
            return
        to = None
        if around_dm is not None:
            to = near(room, skip, around_dm) if skip is not None else []
            if not to:
                return
        await self._broadcast_str(room, json.dumps(payload), skip=skip, to=to)

    async def close(self) -> None:
        """Para todas las salas (al descargar el cog), entregando antes el paseo pendiente."""
        now = self.clock()
        for guild_id, room in self.rooms.items():
            for player in room.players.values():
                self._flush(guild_id, player, now)
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

    async def _broadcast_str(
        self,
        room: Room,
        text: str,
        *,
        skip: Player | None = None,
        to: list[Player] | None = None,
    ) -> None:
        await self._send_all(room, lambda p: p.client.send_str(text), skip=skip, to=to)

    async def _send_all(
        self,
        room: Room,
        send: Callable[[Player], Awaitable[None]],
        *,
        skip: Player | None = None,
        to: list[Player] | None = None,
    ) -> None:
        # Por debajo de `MAX_BACKLOG_BYTES` el envío no espera al cliente, así que ir
        # de uno en uno no hace esperar a los demás; al que va atrasado se le salta.
        for player in list(room.players.values()) if to is None else to:
            if player is skip or player.backlog() > MAX_BACKLOG_BYTES:
                continue
            try:
                await send(player)
            except (ConnectionError, RuntimeError):
                # Conexión que se está cerrando: su manejador la saca de la sala.
                pass


def _allowed(room: Room, player: Player, now: float) -> bool:
    """Si un mensaje de `player` cabe en su tope por segundo (y lo cuenta)."""
    if room.players.get(player.slot) is not player:
        return False
    start, count = player.window
    if now - start >= 1.0:
        start, count = now, 0
    if count >= MAX_POSES_PER_SECOND:
        return False
    player.window = (start, count + 1)
    return True


def _move(player: Player, last: bytes, pose: bytes, now: float) -> tuple[bytes, float | None]:
    """La pose recortada a lo que deja la física desde `last`, y los dm que avanza en horizontal.

    Rellena el presupuesto de `player` con el tiempo pasado desde su última pose y gasta lo
    que se mueve; lo que se pasa se recorta en la misma dirección. Con un aviso de teleporte
    sin usar (y a tiempo), la pose va tal cual, el presupuesto se llena y devuelve `None`.
    """
    since = now - player.moved_at if player.moved_at is not None else SLACK_SECONDS
    player.moved_at = now
    if player.leap:
        player.leap = False
        if now - player.leaped <= LEAP_VALID_SECONDS:
            player.budget = RUN_DM_PER_SECOND * SLACK_SECONDS
            player.climb = CLIMB_DM_PER_SECOND * SLACK_SECONDS
            return pose, None
    budget = min(RUN_DM_PER_SECOND * SLACK_SECONDS, player.budget + since * RUN_DM_PER_SECOND)
    climb = min(CLIMB_DM_PER_SECOND * SLACK_SECONDS, player.climb + since * CLIMB_DM_PER_SECOND)
    x0, y0, z0 = place(last)
    x, y, z = place(pose)
    moved = math.hypot(x - x0, z - z0)
    if moved > budget or y - y0 > climb:
        if moved > budget:
            k = budget / moved
            x, z = x0 + int((x - x0) * k), z0 + int((z - z0) * k)
            moved = math.hypot(x - x0, z - z0)
        y = min(y, y0 + int(climb))
        pose = _PLACE.pack(x, y, z) + pose[6:]
    player.budget = budget - moved
    player.climb = climb - max(0, y - y0)
    return pose, moved


def near(room: Room, player: Player, radius_dm: int = NEAR_DM) -> list[Player]:
    """Los demás de la sala a menos de `radius_dm` (en horizontal) de `player`."""
    if player.pose is None:
        return []
    x, _y, z = place(player.pose)
    found = []
    for other in room.players.values():
        if other is player or other.pose is None:
            continue
        ox, _oy, oz = place(other.pose)
        if (ox - x) ** 2 + (oz - z) ** 2 <= radius_dm**2:
            found.append(other)
    return found


def _card(player: Player) -> dict[str, object]:
    return {"slot": player.slot, "id": str(player.user_id), "nombre": player.name}
