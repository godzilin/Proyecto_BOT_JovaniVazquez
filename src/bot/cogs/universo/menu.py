"""El menú del tabulador del Jovaniverso: las pestañas «Jugadores» y «Mochila».

- `GET /api/jugadores?guild_id=[&mundo=pruebas]`, con la sesión: el menú del tabulador.
  `{jugadores: [{nombre, saldo, nivel, dentro, yo}], miembros, dentro}`: quien está en la
  sala y, después, el resto del servidor por saldo (`player_list`). `nivel` es `null` si
  el servidor no tiene niveles. Solo lee.
- `GET /api/mochila?guild_id=`, con la sesión: `{objetos: [{nombre, emoji, tipo, cantidad,
  puesto, caduca}]}`, lo que tienes de la tienda (`backpack_list`). Solo lee: los objetos se usan
  desde la mochila de `tienda`, en Discord.
"""

from __future__ import annotations

import time
from typing import Any

from aiohttp import web

from bot.cogs import achievements as logros
from bot.cogs import shop as tienda
from bot.cogs.universo.comun import _COG_KEY
from bot.services.achievements import universo_menu_stats
from bot.services.universo import (
    UniversoError,
    backpack_list,
    bearer,
    player_list,
)

#: Segundos que vale la foto de saldos y niveles de la pestaña «Jugadores»: abrir el menú
#: muchas veces seguidas no vuelve a leer la base de datos.
LIST_SECONDS = 10


class MenuMixin:
    """Pestañas del menú del cog `Universo`. Solo leen."""

    async def players(self, session: str | None, query: dict[str, Any]) -> dict[str, Any]:
        """Pestaña «Jugadores» del menú: quién está en la sala y el resto del servidor.

        Con saldo y nivel de cada uno. Solo lee; los saldos y la experiencia se leen como
        mucho una vez cada `LIST_SECONDS` por servidor.

        Raises:
            UniversoError: Igual que `enter`.
        """
        guild, member = self._member(session, query)
        salas = self.pruebas if query.get("mundo") == "pruebas" else self.salas
        inside = {p.user_id for p in salas.present(guild.id)}
        now = time.monotonic()
        cached = self._lista.get(guild.id)
        if cached is None or now - cached[0] > LIST_SECONDS:
            balances = await self.bot.economy.member_balances(guild.id)
            settings = await self.bot.message_stats.level_settings(guild.id)
            xps = None
            if settings is not None and settings.enabled:
                board = await self.bot.message_stats.level_leaderboard(guild.id, 1_000_000, 0)
                xps = dict(board)
            cached = (now, balances, xps)
            self._lista[guild.id] = cached
        members = [(m.id, m.display_name) for m in guild.members if not m.bot]
        balances = cached[1]
        if salas is self.salas:  # el extraplano no da logros
            mine = balances.get(member.id, 0)
            richest = mine > 0 and all(
                balances.get(m, 0) <= mine for m, _ in members if m != member.id
            )
            delta = universo_menu_stats(
                "jugadores", inside=len(inside), richest=richest, broke=mine == 0
            )
            logros.note(self.bot, guild.id, member.id, delta)
        return {
            "jugadores": player_list(members, inside, balances, cached[2], member.id),
            "miembros": len(members),
            "dentro": len(inside),
        }

    async def backpack(self, session: str | None, query: dict[str, Any]) -> dict[str, Any]:
        """Pestaña «Mochila» del menú: lo que tiene quien la abre. Solo lee.

        Raises:
            UniversoError: Igual que `enter`; 503 si la tienda no está cargada.
        """
        guild, member = self._member(session, query)
        entries = await tienda.inventory(self.bot, guild.id, member.id)
        if entries is None:
            raise UniversoError("La tienda no está disponible ahora.", 503)
        rows = backpack_list(entries, time.time())
        if query.get("mundo") != "pruebas":
            logros.note(
                self.bot, guild.id, member.id, universo_menu_stats("mochila", items=len(rows))
            )
        return {"objetos": rows}


async def _jugadores(request: web.Request) -> web.Response:
    cog = request.app[_COG_KEY]
    session = bearer(request.headers.get("Authorization"))
    return web.json_response(await cog.players(session, dict(request.query)))


async def _mochila(request: web.Request) -> web.Response:
    cog = request.app[_COG_KEY]
    session = bearer(request.headers.get("Authorization"))
    return web.json_response(await cog.backpack(session, dict(request.query)))
