"""La sala (multijugador por WebSocket) y el paseo del Jovaniverso.

- `GET /api/sala` (WebSocket): el multijugador. El primer mensaje es JSON
  `{t: "hola", v, session, guild_id}` (`v`, la versión del protocolo: a una página vieja se
  le pide que recargue); después, poses en binario y, en JSON, los avisos de
  teleporte y de devtools. El reparto lo hace `bot.services.universo_sala.Salas` (una sala
  por servidor, ticks fijos y binarios), que no deja ir más rápido de lo que deja la física
  y cuenta el paseo con lo que ve (`_walked`). Con `mundo: "pruebas"` en el saludo, entra en
  la sala del extraplano, que es otra (`pruebas`), sin logros de coincidir, de gestos ni de
  paseo, sin mirar la velocidad (allí se vuela) y solo para los desarrolladores. En la isla
  se mira a todos, también con devtools: quien vuela o corre con ellas se ve recortado a la
  velocidad de los demás. Con `dentro: true` en el saludo (la página sigue dentro de un
  interior tras cortarse la sala), vuelve a la capa de su interior (`InteriorsMixin.layer_for`).
  Los gestos del robot se compran en la tienda (`gesto_*`, `bot.services.universo.GESTURES`)
  y se leen al entrar en la sala: uno comprado con el universo abierto se ve al volver
  a abrirlo.
- `POST /api/paseo` `{guild_id, channel_id, teleports, wallruns, walljumps, doublejumps,
  grapples, speed, chain}`, con la sesión: lo del paseo que la sala no ve en las poses
  (teleportes, trucos de piloto, la mayor velocidad y la cadena de paredes más larga),
  para los logros. La página lo manda cada 15 s; el servidor lo recorta a lo que da tiempo
  a hacer (`bot.services.universo.walk_report`). Los metros, saltos, deslizamientos y
  lugares los cuenta la sala (`bot.services.universo_sala`) y aquí se ignoran.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from aiohttp import WSMsgType, web

from bot.cogs import achievements as logros
from bot.cogs import shop as tienda
from bot.cogs.universo.comun import _COG_KEY, _json_body
from bot.cogs.universo.permisos import NOT_DEV
from bot.services.achievements import (
    StatDelta,
    universo_gesture_stats,
    universo_meet_stats,
    universo_teleport_stats,
    universo_walk_stats,
)
from bot.services.levels import TIMEZONE
from bot.services.universo import (
    GESTURES,
    UniversoError,
    bearer,
    gestures_owned,
    snowflake,
    teleport_report,
    walk_report,
)
from bot.services.universo_sala import OLD_PAGE, PROTOCOL, Player, SalaError, Stroll

#: Segundos que tiene la página para decir quién es al abrir el multijugador.
HELLO_SECONDS = 10
#: Lo más que acepta el multijugador en un mensaje: el saludo es lo más grande.
MAX_WS_BYTES = 1024
#: Latido del multijugador: una conexión que no contesta en este tiempo se cierra.
HEARTBEAT_SECONDS = 20


class SalaMixin:
    """Sala y paseo del cog `Universo`: atiende el multijugador y apunta sus logros."""

    def walk(self, session: str | None, body: dict[str, Any]) -> None:
        """Apunta para los logros lo que la página dice del paseo y la sala no ve.

        Trucos de piloto, velocidad, cadena y teleportes. Solo cuenta lo que da tiempo a
        hacer desde el informe anterior (o desde que entraste); si el bot se ha reiniciado
        entre medias, nada hasta el siguiente.

        Raises:
            UniversoError: Igual que `enter`.
        """
        guild, member = self._member(session, body)
        now = time.monotonic()
        last = self._last_walk.get((guild.id, member.id), now)
        self._last_walk[guild.id, member.id] = now
        walk = walk_report(body, now - last)
        delta = universo_walk_stats(
            0, 0, (), tricks=walk.tricks, speed=walk.speed, chain=walk.chain
        )
        delta.merge(
            universo_teleport_stats(teleport_report(body, now - last), when=datetime.now(TIMEZONE))
        )
        if delta.add or delta.peak:
            channel_id = snowflake(body.get("channel_id"))
            logros.note(self.bot, guild.id, member.id, delta, channel_id)

    async def play(self, ws: web.WebSocketResponse, backlog: Callable[[], int]) -> None:
        """Atiende el multijugador de una página hasta que se va.

        El primer mensaje dice quién es (la misma sesión que `enter`); si no vale, se
        contesta `{t: "error"}` y se cierra. Luego cada mensaje binario es una pose.
        """
        try:
            first = await ws.receive(timeout=HELLO_SECONDS)
            hello = json.loads(first.data) if first.type == WSMsgType.TEXT else None
        except (TimeoutError, ValueError):
            hello = None
        if not isinstance(hello, dict) or hello.get("t") != "hola":
            await ws.close()
            return
        if hello.get("v") != PROTOCOL:
            # Una página de antes de un despliegue: que recargue (con `error` no reintenta).
            await ws.send_json({"t": "error", "error": OLD_PAGE})
            await ws.close()
            return
        try:
            guild, member = self._member(hello.get("session"), hello)
            pruebas = hello.get("mundo") == "pruebas"
            if pruebas and "devtools" not in await self.permisos(guild.id, member.id):
                raise UniversoError(NOT_DEV, 403)
            salas = self.pruebas if pruebas else self.salas
            gestures = gestures_owned(await tienda.owned_keys(self.bot, guild.id, member.id))
            player = await salas.join(
                guild.id,
                member.id,
                member.display_name,
                ws,
                backlog,
                gestures,
                places=None if pruebas else await self.places(guild.id),
                channel_id=snowflake(hello.get("channel_id")),
                layer=self.layer_for(guild.id, member.id, pruebas, hello.get("dentro") is True),
            )
        except (UniversoError, SalaError) as error:
            text = error.message if isinstance(error, UniversoError) else str(error)
            await ws.send_json({"t": "error", "error": text})
            await ws.close()
            return
        self.bot.telemetry.count("universo", "sala", guild_id=guild.id, user_id=member.id)
        if not pruebas:
            self._meet(guild.id, player)
        try:
            async for message in ws:
                if message.type == WSMsgType.BINARY:
                    salas.pose(guild.id, player, message.data)
                elif message.type == WSMsgType.TEXT:
                    salas.text(guild.id, player, message.data)
        finally:
            await salas.leave(guild.id, player)

    def _meet(self, guild_id: int, player: Player) -> None:
        """Logros de coincidir: quien entra y cada uno de los que ya estaban."""
        present = self.salas.present(guild_id)
        others = [p for p in present if p is not player]
        joined = universo_meet_stats([p.user_id for p in others], len(present))
        logros.note(self.bot, guild_id, player.user_id, joined)
        for other in others:
            seen = universo_meet_stats([player.user_id], len(present))
            logros.note(self.bot, guild_id, other.user_id, seen)

    def _walked(self, guild_id: int, player: Player, stroll: Stroll) -> None:
        """Logros del paseo que ha visto la sala (un lote cada 15 s o al salir)."""
        delta = universo_walk_stats(stroll.meters, stroll.jumps, stroll.places, stroll.slides)
        logros.note(self.bot, guild_id, player.user_id, delta, player.channel_id)

    def _gesture(self, guild_id: int, player: Player, anim: int, near: list[Player]) -> None:
        """Logros de empezar un gesto. Quien baila cerca sube también su récord de bailar juntos."""
        same = [p for p in near if p.pose is not None and p.pose[-1] == anim]
        delta = universo_gesture_stats(
            GESTURES[anim].removeprefix("gesto_"),
            near=len(near),
            together=len(same) + 1,
            summit="nieves" in self.salas.places_at(guild_id, player),
            when=datetime.now(TIMEZONE),
        )
        logros.note(self.bot, guild_id, player.user_id, delta)
        if delta.peak and same:
            peak = StatDelta(peak=delta.peak)
            for other in same:
                logros.note(self.bot, guild_id, other.user_id, peak)


async def _paseo(request: web.Request) -> web.Response:
    cog = request.app[_COG_KEY]
    body = await _json_body(request)
    cog.walk(bearer(request.headers.get("Authorization")), body)
    return web.json_response({"ok": True})


async def _sala(request: web.Request) -> web.WebSocketResponse:
    cog = request.app[_COG_KEY]
    # Sin permessage-deflate: comprimiría cada mensaje una vez por conexión, en el bucle,
    # y la sala codifica una vez y manda los mismos bytes a todos.
    ws = web.WebSocketResponse(
        heartbeat=HEARTBEAT_SECONDS, max_msg_size=MAX_WS_BYTES, compress=False
    )
    await ws.prepare(request)
    transport = request.transport
    await cog.play(ws, lambda: transport.get_write_buffer_size() if transport else 0)
    return ws
