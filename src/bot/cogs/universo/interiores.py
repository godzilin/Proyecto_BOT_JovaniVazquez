"""Los interiores del Jovaniverso: entrar por una puerta, salir y usar lo de dentro.

`POST /api/interior` `{guild_id, channel_id, mundo?, accion, ...}`, con la sesión:

- `accion: "entrar"`, `interior` (su id) y `t` (`c/<cx>/<cz>@v,i/<id>@v,a/<id>@v,...`: la
  celda de la puerta, el interior y los assets de sus usables, cada uno con la versión de su
  fila del índice, como en `/api/mundo/trozos`) → `{lugar, propio, piloto, puedes, estados}`:
  el lugar en que entra, si es su piso o el piso piloto, si puede usar lo de dentro y el estado
  de cada usable (`{"<n>": 0 | 1}`). El servidor busca la puerta en esa celda publicada (sin
  nada publicado, en el piso de serie, `SERIE_FILE`) y mira con la pose de la sala que esté a
  su alcance; luego pasa al jugador a la capa de su lugar (`Salas.set_layer`). 403 si está
  lejos, 409 si aún no está en la sala, 410 si esa versión ya no está.
- `accion: "usar"`, `n` → `{n, estado}`: cambia el estado de un usable del lugar en que está
  (cerca, al ritmo de `USE_SECONDS` y si el lugar es suyo o de todos), lo guarda
  (`bot.repositories.universo_interiores`) y avisa a los demás del lugar
  (`{t: "uso", n, estado}` por la sala).
- `accion: "salir"` → `{ok}`: vuelve a la capa de fuera.

Con `mundo: "pruebas"` (el extraplano, solo con `devtools`), el mundo es de la página y no se
puede comprobar la puerta: la página manda `piso` y `usables` (`{"<n>": uso}`), y el
lugar va aparte (`pruebas/`), sin logros. Las reglas están en `bot.services.universo_interiores`.

Quien está dentro se apunta en memoria (`Universo._dentro`, uno por miembro): si la sala se
corta y la página vuelve a entrar diciendo que sigue dentro (`dentro: true` en el saludo), la
sala le devuelve a su capa; si dice que no (ha recargado), se olvida.

Logros (`universo_interior_stats` al entrar y `universo_use_stats` al usar), categoría 🎤 Jovani.
No mueve dinero.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from functools import cache
from typing import Any

from aiohttp import web

from bot.cogs import achievements as logros
from bot.cogs.universo.comun import _COG_KEY, _json_body
from bot.cogs.universo.mundo import _parse_batch
from bot.cogs.universo.permisos import NOT_DEV
from bot.services.achievements import StatDelta, universo_interior_stats, universo_use_stats
from bot.services.levels import TIMEZONE
from bot.services.universo import UniversoError, bearer, snowflake
from bot.services.universo_editor import (
    MAX_USABLES,
    PISO_FILE,
    USES,
    MundoError,
    parse_key,
    split_world,
)
from bot.services.universo_interiores import (
    USE_SECONDS,
    Inside,
    Usable,
    find_door,
    near,
    next_state,
    place_key,
    usables_of,
    view,
)
from bot.services.universo_sala import Salas, place

#: El piso de serie de la plaza (edificio, puerta e interior), el mismo que dibuja la página
#: cuando no hay nada publicado (`mundo/serie.ts`).
SERIE_FILE = PISO_FILE

#: Lo que se puede pedir (`accion`).
ACTIONS = ("entrar", "usar", "salir")
FAR = "Acércate más a la puerta."
NOT_INSIDE = "No estás dentro de ningún interior."
NO_ROOM = "Aún no estás en la sala del Jovaniverso: espera un momento."


@cache
def serie_pieces() -> dict[str, dict[str, Any]]:
    """Los trozos del piso de serie (clave → doc), leídos una vez."""
    data = json.loads(SERIE_FILE.read_text(encoding="utf-8"))
    return split_world({**data, "zonas": {}})


class InteriorsMixin:
    """Interiores del cog `Universo`: entrar, usar y salir."""

    async def interior(self, session: str | None, body: dict[str, Any]) -> dict[str, Any]:
        """Atiende `/api/interior` según `accion`.

        Raises:
            UniversoError: Igual que `enter`; 400 si los datos no valen, y los de cada acción.
        """
        guild, member = self._member(session, body)
        pruebas = body.get("mundo") == "pruebas"
        if pruebas and "devtools" not in await self.permisos(guild.id, member.id):
            raise UniversoError(NOT_DEV, 403)
        salas = self.pruebas if pruebas else self.salas
        action = body.get("accion")
        if action not in ACTIONS:
            raise UniversoError("No se sabe qué hacer con ese interior.")
        self.bot.telemetry.count("universo", f"interior_{action}", guild_id=guild.id)
        if action == "salir":
            self._dentro.pop((guild.id, member.id), None)
            salas.set_layer(guild.id, member.id, "")
            return {"ok": True}
        player = salas.find(guild.id, member.id)
        if player is None or player.pose is None:
            raise UniversoError(NO_ROOM, 409)
        x, _y, z = place(player.pose)
        # Entrar y usar leen la base: como mucho una cosa cada `USE_SECONDS` por persona.
        now = time.monotonic()
        if now - self._usado.get(member.id, float("-inf")) < USE_SECONDS:
            raise UniversoError("Más despacio.", 429)
        self._usado[member.id] = now
        if action == "entrar":
            return await self._enter(guild.id, member.id, body, salas, pruebas, (x, z))
        channel_id = snowflake(body.get("channel_id"))
        return await self._use(guild.id, member.id, body, salas, (x, z), channel_id)

    async def _enter(
        self,
        guild_id: int,
        user_id: int,
        body: dict[str, Any],
        salas: Salas,
        pruebas: bool,
        at: tuple[int, int],
    ) -> dict[str, Any]:
        room_id = body.get("interior")
        try:
            parse_key(f"i/{room_id}")
        except MundoError as error:
            raise UniversoError("Ese interior no existe.") from error
        if pruebas:
            flat, usables = _test_room(body)
        else:
            docs = await self._room_pieces(guild_id, room_id, body.get("t"))
            cell = next((d for k, d in docs.items() if k.startswith("c/")), None)
            room = docs.get(f"i/{room_id}")
            door = find_door(cell, room_id, *at) if cell and room else None
            if room is None or door is None:
                raise UniversoError(FAR, 403)
            assets = {k[2:]: d for k, d in docs.items() if k.startswith("a/")}
            flat, usables = room.get("piso") is True, usables_of(room, assets, door)
        owner = None
        if flat and await self.bot.housing.home_of(guild_id, user_id) is not None:
            owner = user_id
        lugar = place_key(room_id, owner=owner, pruebas=pruebas)
        salas.set_layer(guild_id, user_id, lugar)
        self._dentro[guild_id, user_id] = Inside(lugar, room_id, owner, usables, pruebas)
        saved = await self.bot.universo_interiores.states(guild_id, lugar)
        now = time.time()
        states = {str(n): view(u.uso, saved.get(n), now) for n, u in usables.items()}
        if not pruebas:
            together = salas.in_layer(guild_id, lugar)
            delta = universo_interior_stats(
                room_id,
                own=owner is not None,
                pilot=flat and owner is None,
                crowd=len(together),
                when=datetime.now(TIMEZONE),
            )
            channel_id = snowflake(body.get("channel_id"))
            logros.note(self.bot, guild_id, user_id, delta, channel_id)
            if delta.peak:
                for other in together:
                    if other.user_id != user_id:
                        logros.note(self.bot, guild_id, other.user_id, StatDelta(peak=delta.peak))
        return {
            "lugar": lugar,
            "propio": owner is not None,
            "piloto": flat and owner is None,
            "puedes": owner in (None, user_id),
            "estados": states,
        }

    async def _room_pieces(
        self, guild_id: int, room_id: str, t: object
    ) -> dict[str, dict[str, Any]]:
        """Los trozos publicados que dice `t` (sin nada publicado, los del piso de serie)."""
        if await self.world_version(guild_id) == 0:
            return serie_pieces()
        wanted = _parse_batch(t if isinstance(t, str) else "")
        if sum(k.startswith("c/") for k in wanted) != 1 or f"i/{room_id}" not in wanted:
            raise UniversoError("Falta la celda de la puerta o el interior.")
        text = await self.world_batch(guild_id, wanted, False)
        if text is None:
            raise UniversoError("Esa parte del mundo ya no está: vuelve a pedir el índice.", 410)
        found: dict[str, dict[str, Any]] = (await asyncio.to_thread(json.loads, text))["trozos"]
        return found

    async def _use(
        self,
        guild_id: int,
        user_id: int,
        body: dict[str, Any],
        salas: Salas,
        at: tuple[int, int],
        channel_id: int | None,
    ) -> dict[str, Any]:
        inside = self._dentro.get((guild_id, user_id))
        n = body.get("n")
        if inside is None or inside.pruebas != (salas is self.pruebas):
            raise UniversoError(NOT_INSIDE, 409)
        usable = inside.usables.get(n) if type(n) is int else None
        if usable is None:
            raise UniversoError("Eso no se puede usar.")
        if inside.owner not in (None, user_id):
            raise UniversoError("Este piso no es tuyo: aquí solo se mira.", 403)
        if not inside.pruebas and not near(*at, usable):
            raise UniversoError("Acércate más.", 403)
        now = time.time()
        repo = self.bot.universo_interiores
        # shortcut: leer y escribir sin candado; dos personas a la vez en el mismo mueble
        # pueden dejarlo como estaba. Si llega a pasar, un `TimedLock` por lugar.
        current = view(usable.uso, (await repo.states(guild_id, inside.lugar)).get(n), now)
        state = next_state(usable.uso, current)
        if state is None:
            raise UniversoError("Ya está arreglado.", 409)
        await repo.set_state(guild_id, inside.lugar, n, state, user_id, now)
        await salas.announce(
            guild_id, {"t": "uso", "n": n, "estado": state}, skip_user=user_id, layer=inside.lugar
        )
        if not inside.pruebas:
            delta = universo_use_stats(usable.uso, state, shared=inside.owner is None)
            logros.note(self.bot, guild_id, user_id, delta, channel_id)
        return {"n": n, "estado": state}

    def layer_for(self, guild_id: int, user_id: int, pruebas: bool, inside: bool) -> str:
        """La capa con que vuelve a la sala alguien que se conecta: la de su interior si la
        página dice que sigue dentro; si no, se olvida que estaba."""
        found = self._dentro.get((guild_id, user_id))
        if found is None or found.pruebas != pruebas:
            return ""
        if not inside:
            del self._dentro[guild_id, user_id]
            return ""
        return found.lugar


def _test_room(body: dict[str, Any]) -> tuple[bool, dict[int, Usable]]:
    """El piso y los usables que dice la página en el extraplano (no hay mundo en el servidor)."""
    raw = body.get("usables")
    if not isinstance(raw, dict) or len(raw) > MAX_USABLES:
        raise UniversoError("Los usables no valen.")
    usables = {}
    for key, uso in raw.items():
        if not (isinstance(key, str) and key.isdigit() and 1 <= int(key) <= MAX_USABLES):
            raise UniversoError("Los usables no valen.")
        if uso not in USES:
            raise UniversoError("Los usables no valen.")
        usables[int(key)] = Usable(uso, 0, 0)
    return body.get("piso") is True, usables


async def _interior(request: web.Request) -> web.Response:
    cog = request.app[_COG_KEY]
    body = await _json_body(request)
    return web.json_response(await cog.interior(bearer(request.headers.get("Authorization")), body))
