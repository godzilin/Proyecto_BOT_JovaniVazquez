"""Las tragaperras de la plaza y los botes de los letreros del Jovaniverso.

- `POST /api/traga` `{guild_id, channel_id, machine, stake, auto}`, con la sesión:
  una tirada en una tragaperras de la plaza (`bot.cogs.slots.plaza`, el mismo dinero,
  calor por máquina, logros y anuncios que `tragas`). Devuelve los fotogramas de la
  animación (`spin_states`), que pinta la página. Con `{..., "parar": true}`, para el
  ▶️ Auto de esa máquina. Las reglas (máquinas a la vez, ritmo, Auto) están en
  `bot.services.universo_tragas`. La tirada sale también a los demás de la sala
  (`{t: "traga", machine, states, ...}`, sin saldo ni texto), que la ven en esa máquina.
- `GET /api/botes?guild_id=`, con la sesión: `{botes: [{clave, nombre, emoji, texto}], catalogo}`,
  los botes del servidor que pueden enseñar los letreros (`bot.services.universo_botes`) y, en
  `catalogo`, todos los que se pueden elegir en el editor aunque su juego no esté cargado. La
  página lo pide cada 30 s mientras ve algún letrero; el servidor lo guarda `POTS_SECONDS`.
  `/api/entrar` trae los mismos en `botes`.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from aiohttp import web

from bot.cogs import horses as caballos
from bot.cogs import lottery as loteria
from bot.cogs import poker
from bot.cogs import slots as tragas
from bot.cogs.universo.comun import _COG_KEY, _json_body
from bot.services.universo import (
    UniversoError,
    bearer,
    snowflake,
)
from bot.services.universo_botes import pot_catalog, pot_list
from bot.services.universo_sala import SPIN_SEEN_DM
from bot.services.universo_tragas import SeatError

logger = logging.getLogger(__name__)

#: Lo que ven los demás de una tirada (lo que pinta y suena la máquina, no el saldo ni el texto).
SEEN_SPIN = (
    "machine",
    "states",
    "won",
    "jackpot",
    "tier",
    "near_miss",
    "free_won",
    "pot_text",
    "heat",
    "heat_max",
    "bonus_fill",
    "free_spins",
)

#: Segundos que se guardan los botes de los letreros: la página los pide cada tanto.
POTS_SECONDS = 20


class CasinoMixin:
    """Tragaperras de la plaza y botes de los letreros del cog `Universo`."""

    async def pots(self, guild_id: int) -> list[dict[str, str]]:
        """Los botes del servidor para los letreros, guardados `POTS_SECONDS`.

        Cada juego se pregunta por su función puente: uno sin cargar o que falla no sale,
        y los demás sí.
        """
        now = time.monotonic()
        cached = self._botes.get(guild_id)
        if cached is not None and now - cached[0] < POTS_SECONDS:
            return cached[1]
        amounts: dict[str, int] = {}
        for key, ask in (
            ("tragas", tragas.pot),
            ("poker", poker.pot),
            ("caballos", caballos.pot),
        ):
            try:
                value = await ask(self.bot, guild_id)
            except Exception:
                logger.exception("No se ha podido leer el bote de %s", key)
                continue
            if value is not None:
                amounts[key] = value
        try:
            amounts.update(await loteria.jackpots(self.bot, guild_id))
        except Exception:
            logger.exception("No se han podido leer los botes de las loterías")
        found = pot_list(amounts)
        self._botes[guild_id] = (now, found)
        return found

    async def slot(self, session: str | None, body: dict[str, Any]) -> dict[str, Any]:
        """Una tirada (o ⏹️ parar el Auto) en una tragaperras de la plaza.

        Raises:
            UniversoError: Igual que `enter`; con 400 si los datos no valen o la tirada
                no se puede jugar (saldo, ritmo, máquinas a la vez), con su texto.
        """
        guild, member = self._member(session, body)
        machine, stake = body.get("machine"), body.get("stake", 0)
        if type(machine) is not int or type(stake) is not int:
            raise UniversoError("La tirada no es válida.")
        channel_id = snowflake(body.get("channel_id"))
        channel = guild.get_channel_or_thread(channel_id) if channel_id is not None else None
        if body.get("parar") is True:
            tragas.plaza_stop(self.bot, guild.id, member, channel, machine)
            return {"ok": True}
        audience = sum(p.user_id != member.id for p in self.salas.present(guild.id))
        started = time.perf_counter()
        try:
            result = await tragas.plaza(
                self.bot,
                guild.id,
                member,
                channel,
                machine=machine,
                stake=stake,
                auto=body.get("auto") is True,
                audience=audience,
            )
        except SeatError as error:
            raise UniversoError(str(error)) from error
        if result is None:
            raise UniversoError("La tragaperras no está disponible ahora.", 503)
        self.bot.telemetry.count(
            "universo",
            "traga",
            guild_id=guild.id,
            user_id=member.id,
            ms=(time.perf_counter() - started) * 1000,
        )
        # Los que están cerca ven la tirada en su pantalla: los fotogramas y el visor, sin
        # saldo ni texto. Son ~20 KB: a quien no ve la máquina no se le manda.
        seen = {key: result[key] for key in SEEN_SPIN if key in result}
        for salas in (self.salas, self.pruebas):
            await salas.announce(
                guild.id, {"t": "traga", **seen}, skip_user=member.id, around_dm=SPIN_SEEN_DM
            )
        return result


async def _botes(request: web.Request) -> web.Response:
    cog = request.app[_COG_KEY]
    guild, _ = cog._member(bearer(request.headers.get("Authorization")), dict(request.query))
    return web.json_response({"botes": await cog.pots(guild.id), "catalogo": pot_catalog()})


async def _traga(request: web.Request) -> web.Response:
    cog = request.app[_COG_KEY]
    body = await _json_body(request)
    response = web.json_response(await cog.slot(bearer(request.headers.get("Authorization")), body))
    # Los fotogramas de una tirada son ~20 KB de JSON muy repetido: comprimidos, ~3 KB.
    response.enable_compression()
    return response
