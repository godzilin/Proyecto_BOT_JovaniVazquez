"""Salas del Jovaniverso: quién está en el mundo de cada servidor y dónde.

El multijugador sigue «Jovaniverso: rendimiento imperativo» de la Biblia: el servidor
no simula nada, solo valida y reparte. Cada página manda su posición en binario
(`POSE`, 13 bytes) y la sala la guarda. Diez veces por segundo, si algo ha cambiado,
codifica **una vez** cada región con lo cambiado en ella y manda a cada uno las de su
alrededor (interés por cercanía, abajo). Una sala vacía no tiene tarea.

Protocolo `PROTOCOL` = 2 (todo en little-endian; las posiciones, en decímetros):

- El saludo (`{t: "hola", v: 2, ...}`, `bot.cogs.universo.sala`) lleva la versión. A una
  página de otra (una vieja abierta durante un despliegue) se le contesta `{t: "error"}`
  con `OLD_PAGE`, que pide recargar, y se cierra; con `error`, la página no reintenta.
- Página → servidor, binario: `u8 tipo=2, i32 x, u16 y, i32 z, u8 giro, u8 animación`.
  La altura se cuenta desde `MIN_Y_DM` (de −50 a 4000 m: cabe el Teide). El giro, de 0 a
  255 por vuelta entera. La animación: 0 quieto, 1 andar, 2 correr, 3 saltar, de 4 a 10
  los gestos, que se compran en la tienda (`bot.services.universo.GESTURES`), y las
  posturas de moverse (`MOVES`: 11 agachado, 12 deslizándose), que son de todos.
- Servidor → página, binario: `u8 tipo` (2, lo cambiado; 3, todo lo que ves) y, hasta el
  final, grupos de una región: `i16 rx, i16 rz, u8 n` y `n` veces `u8 hueco, i16 dx,
  u16 y, i16 dz, u8 giro, u8 animación` (9 bytes), con `dx` y `dz` desde la esquina de la
  región (`rx · REGION_DM`, `rz · REGION_DM`). Va también la pose propia; la página la
  ignora por su hueco. Con el tipo 3, la página deja de dibujar a quien no venga en él.
- Servidor → página, JSON (lo raro): `bienvenida` (tu hueco, tu id para teñir tu robot
  del mismo color que te ven los demás, y quién hay: todos, aunque estén lejos), `entra`,
  `sale` y `lejos` (`slots`: los que acaban de salir de lo que ves; se dejan de dibujar,
  pero siguen en la sala). El hueco (de 0 a `MAX_PLAYERS` − 1) es el identificador
  corto de cada jugador en la sala.
- Página → servidor, JSON: `{t: "salto"}` justo antes de la pose de un teleporte (el
  menú «Viajar», sentarse a una máquina, las devtools) y `{t: "trucos", on}` al poner o
  quitar una devtool (volar, noclip, velocidad).

Interés por cercanía: el mundo se parte en regiones de `REGION_DM` de lado y cada
jugador está en una, que solo cambia cuando se sale de ella más de `HYSTERESIS_DM` (quien
pasea por la raya no cambia de región a cada paso). Cada uno recibe a los de las 3 × 3
regiones de alrededor de la suya: ve seguro a quien tiene a menos de `REGION_DM` − 2 ·
`HYSTERESIS_DM` (140 m) y, como mucho, a unos 330 m. En cada tick se codifica un grupo
por región con algo cambiado, y los que están en la misma región reciben los mismos bytes
(sus 9 grupos, juntados una vez). Al cambiar de región, al entrar y tras saltarse ticks
por ir atrasado, uno recibe un tipo 3 con todos los de sus 9 regiones (`resync`), y a
quien ve a alguien salirse de su vista se le manda `lejos`: cuestan una vez por cada 160 m
que se recorren, no en cada tick. El menú de jugadores no depende de esto (`/api/jugadores`).

Capas (los interiores, fase 4 de `docs/mundo-celdas.md`): cada jugador está en una capa
(`Player.layer`, `""` fuera) y las regiones son de cada capa, así que solo se ve a quien está
en la misma. Quien entra en un interior pasa a la capa de su lugar (`set_layer`, que decide el
cog con `bot.services.universo_interiores`): la página monta la habitación en el sitio de la
puerta y los de fuera no le ven atravesar la pared, ni él a ellos. Cambiar de capa es como
cambiar de región: los de antes reciben `lejos` y él, un tipo 3 con los de la nueva. Lo que
mira la cercanía (`near`: gestos, tiradas de las tragaperras) tampoco cruza de capa, y
`announce(..., layer=)` avisa solo a una (lo que se usa dentro de un interior).

Lo que llega de la página no es de fiar: se descarta lo que no mide 13 bytes, lo que
llega a más de `MAX_POSES_PER_SECOND` y lo que se sale del mundo (`WORLD_DM`, hasta donde
llegan las celdas del editor). Un gesto que no se ha comprado (o que no existe) se
reenvía como quieto. Y nadie va más rápido de lo que deja la física de la página
(`_move`): cada jugador tiene un presupuesto de decímetros que se rellena a
`RUN_DM_PER_SECOND` (y otro para subir, a `CLIMB_DM_PER_SECOND`; bajar, que es caer, no
tiene tope) hasta `SLACK_SECONDS` de reserva, que cubre las poses que llegan juntas tras
un parón de la red. Una pose que se pasa se recorta hasta donde llega el presupuesto:
quien se teletransporta sin avisar se ve correr hacia allí a la velocidad máxima. Tras un
`salto` aceptado (uno cada `LEAP_SECONDS`, el ritmo de teleportes del informe) la pose
siguiente va sin mirar. Con `check=False` (el extraplano de los desarrolladores, donde se
vuela) no se mira nada.

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
from bot.services.universo_editor import CELL, CELL_LIMIT

logger = logging.getLogger(__name__)

#: Versión del protocolo (el `v` del saludo).
PROTOCOL = 2
#: Lo que se dice a una página de otra versión del protocolo.
OLD_PAGE = "El Jovaniverso se ha actualizado: recarga la página para seguir."
#: Ticks por segundo de cada sala con gente.
TICK_HZ = 10
#: Poses por segundo que se aceptan de cada página (manda 10; el margen cubre el reloj).
MAX_POSES_PER_SECOND = 15
#: Jugadores por sala. Caben en el `u8` del hueco y por debajo de los fantasmas de las
#: devtools (huecos desde el 200, `universo/src/dev/devtools.ts`).
MAX_PLAYERS = 50
#: Salas a la vez (una por servidor de Discord con alguien dentro).
MAX_ROOMS = 200
#: Lo más lejos del centro que puede estar alguien, en decímetros: hasta donde llegan las
#: celdas del editor (±40 km). La página no pasa del borde de su mundo publicado, que cae
#: dentro (`universo/src/mundo/borde.ts`, `test_universo_borde_ts.py`).
WORLD_DM = CELL * CELL_LIMIT * 10
#: Altura mínima y máxima, en decímetros (el Teide mide 3715 m).
MIN_Y_DM, MAX_Y_DM = -500, 40_000
#: Lado de una región del interés por cercanía, en decímetros (dos celdas del mundo).
REGION_DM = 1_600
#: Lo que hay que salirse de la región para cambiar de región, en decímetros.
HYSTERESIS_DM = 100
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
#: Tipos del mensaje binario de pose: la de la página o lo cambiado del tick, y todo lo que ves.
POSE, FULL = 2, 3
_POSE = struct.Struct("<BiHiBB")
#: Una pose guardada: la de la página sin el tipo.
_STORED = struct.Struct("<iHiBB")
_PLACE = struct.Struct("<iHi")
_GROUP = struct.Struct("<hhB")
_ENTRY = struct.Struct("<BhHhBB")
#: Una región del interés por cercanía: `(rx, rz)`.
Region = tuple[int, int]
#: Una región de una capa: `(capa, región)`. Es por lo que se agrupa en cada tick.
Zone = tuple[str, Region]


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
    #: Su región (`None` hasta el primer tick con pose) y si le toca un tipo 3 con todo lo
    #: que ve (al entrar, al cambiar de región y tras saltarse ticks).
    region: Region | None = None
    resync: bool = True
    #: Su capa (`""` fuera; el lugar del interior en el que está) y la capa con la que se
    #: colocó en su región (cambia en el tick siguiente a `set_layer`).
    layer: str = ""
    placed: str = ""


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
    if kind != POSE or abs(x) > WORLD_DM or abs(z) > WORLD_DM or y > MAX_Y_DM - MIN_Y_DM:
        return None
    if anim >= FIRST_GESTURE and anim not in gestures and anim not in MOVES:
        return data[1:-1] + b"\x00"
    return data[1:]


def pack_pose(x: int, y: int, z: int, yaw: int, anim: int) -> bytes:
    """La pose que manda la página, en decímetros (`y` desde el nivel del mar)."""
    return _POSE.pack(POSE, x, y - MIN_Y_DM, z, yaw, anim)


def place(pose: bytes) -> tuple[int, int, int]:
    """Posición `(x, y, z)` en decímetros de una pose guardada."""
    x, y, z = _PLACE.unpack_from(pose)
    return x, y + MIN_Y_DM, z


def region_of(x: int, z: int, current: Region | None = None) -> Region:
    """La región de (x, z); si no se ha salido de `current` más de `HYSTERESIS_DM`, esa."""
    if current is not None:
        x0, z0, h = current[0] * REGION_DM, current[1] * REGION_DM, HYSTERESIS_DM
        if x0 - h <= x < x0 + REGION_DM + h and z0 - h <= z < z0 + REGION_DM + h:
            return current
    return x // REGION_DM, z // REGION_DM


def around(region: Region) -> list[Region]:
    """Las 3 × 3 regiones que ve quien está en `region`."""
    rx, rz = region
    return [(rx + i, rz + j) for i in (-1, 0, 1) for j in (-1, 0, 1)]


def _sees(a: Zone, b: Zone) -> bool:
    """Si quien está en la región de una capa `a` ve la `b`: misma capa y región al lado."""
    return a[0] == b[0] and abs(a[1][0] - b[1][0]) <= 1 and abs(a[1][1] - b[1][1]) <= 1


def _around(zone: Zone) -> list[Zone]:
    """Las 3 × 3 regiones de su capa que ve quien está en `zone`."""
    return [(zone[0], r) for r in around(zone[1])]


def group(region: Region, poses: list[tuple[int, bytes]]) -> bytes:
    """El grupo binario de una región con las poses `(hueco, pose guardada)`."""
    x0, z0 = region[0] * REGION_DM, region[1] * REGION_DM
    out = [_GROUP.pack(region[0], region[1], len(poses))]
    for slot, pose in poses:
        x, y, z, yaw, anim = _STORED.unpack(pose)
        out.append(_ENTRY.pack(slot, x - x0, y, z - z0, yaw, anim))
    return b"".join(out)


@dataclass
class Room:
    """Los jugadores de un servidor y la tarea que reparte sus poses."""

    players: dict[int, Player] = field(default_factory=dict)
    dirty: set[int] = field(default_factory=set)
    #: Quién hay en cada región de cada capa (huecos), para el tipo 3.
    cells: dict[Zone, set[int]] = field(default_factory=dict)
    #: Si alguien se ha saltado un tick (le toca su tipo 3 aunque nadie se mueva).
    behind: bool = False
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
        layer: str = "",
    ) -> Player:
        """Mete a alguien en la sala de su servidor y avisa a los demás.

        Si ya estaba (otra pestaña), la conexión vieja sale primero. `gestures` son
        las animaciones de gesto que ha comprado; `places`, los lugares del mundo
        publicado (como `set_places`); `channel_id`, desde dónde abrió, y `layer`, su capa
        (la del interior en el que sigue al volver a conectarse). Las poses de
        los demás le llegan en el primer tick tras su primera pose (antes no se sabe qué
        tiene cerca).

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
            layer=layer,
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
        _unplace(room, player)
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

    def find(self, guild_id: int, user_id: int) -> Player | None:
        """El jugador de un miembro en la sala de su servidor, si está."""
        room = self.rooms.get(guild_id)
        if room is None:
            return None
        return next((p for p in room.players.values() if p.user_id == user_id), None)

    def set_layer(self, guild_id: int, user_id: int, layer: str) -> bool:
        """Pasa a alguien a otra capa (un interior, o `""` para salir); dice si está en la sala.

        En el tick siguiente cambia de región: los de su capa de antes reciben `lejos` y él,
        todo lo que ve en la nueva.
        """
        player = self.find(guild_id, user_id)
        if player is None:
            return False
        if player.layer != layer:
            player.layer = layer
            self.rooms[guild_id].dirty.add(player.slot)
        return True

    def in_layer(self, guild_id: int, layer: str) -> list[Player]:
        """Quién está en una capa de la sala de un servidor."""
        return [p for p in self.present(guild_id) if p.layer == layer]

    async def announce(
        self,
        guild_id: int,
        payload: dict[str, object],
        *,
        skip_user: int | None = None,
        around_dm: int | None = None,
        layer: str | None = None,
    ) -> None:
        """Manda un aviso JSON a todos los de la sala de un servidor (sin sala, nada).

        Con `skip_user`, a todos menos a esa persona. Con `around_dm`, solo a quienes
        están a esa distancia (en horizontal) de `skip_user` y en su capa; si no está en la
        sala o aún no ha mandado su pose, a nadie: no se sabe dónde ocurre. Con `layer`, solo
        a los de esa capa.
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
        if layer is not None:
            to = [p for p in (room.players.values() if to is None else to) if p.layer == layer]
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
        """Manda a cada uno, en un solo mensaje, lo cambiado en las regiones que ve.

        Cada región con cambios se codifica una vez y quienes están en la misma región
        reciben los mismos bytes. Quien ha cambiado de región (o entra, o iba atrasado)
        recibe todo lo que ve (`FULL`); quien ve a alguien irse de su vista, `lejos`.
        """
        if not room.dirty and not room.behind:
            return
        room.behind = False
        moved = [
            p
            for slot in sorted(room.dirty)
            if (p := room.players.get(slot)) is not None and p.pose is not None
        ]
        room.dirty.clear()
        # Quién ha cambiado de región o de capa: desde cuál (`None` en su primera pose) y a cuál.
        crossed: dict[int, tuple[Zone | None, Zone]] = {}
        changed: dict[Zone, list[tuple[int, bytes]]] = {}
        for p in moved:
            x, _y, z = place(p.pose)  # type: ignore[arg-type]
            region = region_of(x, z, p.region if p.placed == p.layer else None)
            if region != p.region or p.placed != p.layer:
                before = (p.placed, p.region) if p.region is not None else None
                crossed[p.slot] = (before, (p.layer, region))
                _unplace(room, p)
                room.cells.setdefault((p.layer, region), set()).add(p.slot)
                p.region, p.placed = region, p.layer
                p.resync = True
            changed.setdefault((p.layer, region), []).append((p.slot, p.pose))  # type: ignore[arg-type]
        # Grupos codificados (cuando alguien los recibe) y, por región, lo que ve quien está en
        # ella: vacío si lo único cambiado es su propia pose, que la página ignora.
        groups: dict[Zone, bytes] = {}
        shared: dict[Zone, tuple[bytes, int]] = {}
        full: dict[Zone, bytes] = {}
        for q in list(room.players.values()):
            if q.region is None:
                continue
            zone = (q.placed, q.region)
            if q.backlog() > MAX_BACKLOG_BYTES:
                q.resync = room.behind = True  # se pierde ticks: luego, todo lo que ve
                continue
            gone: list[int] = []
            if q.resync:
                data = bytes((FULL,)) + b"".join(
                    _full(room, r, full) for r in _around(zone) if r in room.cells
                )
            else:
                if zone not in shared:
                    seen = [r for r in _around(zone) if r in changed]
                    only = changed[seen[0]][0][0] if len(seen) == 1 == len(changed[seen[0]]) else -1
                    shared[zone] = (_seen(seen, changed, groups), only)
                data, only = shared[zone]
                if only == q.slot:
                    data = b""
                gone = [
                    slot
                    for slot, (before, after) in crossed.items()
                    if before is not None and _sees(zone, before) and not _sees(zone, after)
                ]
            try:
                if gone:
                    await q.client.send_str(json.dumps({"t": "lejos", "slots": gone}))
                if len(data) > 1 or q.resync:
                    await q.client.send_bytes(data)
                q.resync = False
            except (ConnectionError, RuntimeError):
                # Conexión que se está cerrando: su manejador la saca de la sala.
                pass

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


def _unplace(room: Room, player: Player) -> None:
    """Quita a alguien del índice de regiones de la sala."""
    if player.region is None:
        return
    zone = (player.placed, player.region)
    slots = room.cells.get(zone)
    if slots is not None:
        slots.discard(player.slot)
        if not slots:
            del room.cells[zone]


def _seen(
    seen: list[Zone], changed: dict[Zone, list[tuple[int, bytes]]], cache: dict[Zone, bytes]
) -> bytes:
    """El mensaje con lo cambiado en las regiones `seen` (cada grupo, una vez por tick)."""
    parts = [bytes((POSE,))]
    for zone in seen:
        if zone not in cache:
            cache[zone] = group(zone[1], changed[zone])
        parts.append(cache[zone])
    return b"".join(parts)


def _full(room: Room, zone: Zone, cache: dict[Zone, bytes]) -> bytes:
    """El grupo de una región con todos los que están en ella (una vez por tick)."""
    data = cache.get(zone)
    if data is None:
        poses = [(s, room.players[s].pose) for s in sorted(room.cells[zone])]
        data = cache[zone] = group(zone[1], poses)  # type: ignore[arg-type]
    return data


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
        pose = _PLACE.pack(x, y - MIN_Y_DM, z) + pose[_PLACE.size :]
    player.budget = budget - moved
    player.climb = climb - max(0, y - y0)
    return pose, moved


def near(room: Room, player: Player, radius_dm: int = NEAR_DM) -> list[Player]:
    """Los demás de la sala de su capa a menos de `radius_dm` (en horizontal) de `player`."""
    if player.pose is None:
        return []
    x, _y, z = place(player.pose)
    found = []
    for other in room.players.values():
        if other is player or other.pose is None or other.layer != player.layer:
            continue
        ox, _oy, oz = place(other.pose)
        if (ox - x) ** 2 + (oz - z) ** 2 <= radius_dm**2:
            found.append(other)
    return found


def _card(player: Player) -> dict[str, object]:
    return {"slot": player.slot, "id": str(player.user_id), "nombre": player.name}
