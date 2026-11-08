"""La telemetría de la página del Jovaniverso: cómo va en cada dispositivo.

- `POST /api/medidas` `{guild_id, platform, segundos, fotogramas, fps_p5, llamadas, ...}`,
  con la sesión: cómo va la página en el dispositivo (fps, calidad adaptativa, llamadas de
  dibujo, triángulos, tiempos de entrada, GPU por familia y pantalla redondeada), solo para
  la telemetría («Jovaniverso en los dispositivos» de `docs/analisis_metricas.py`). La página
  lo manda cada 5 min y al ocultarse; el servidor lo recorta
  (`bot.services.universo.device_report`), deja uno por miembro cada `MEDIDAS_SECONDS` (`429`
  si llega antes) y lo suma con `telemetry.count` (tipo `universo_disp`, nombre «medida ·
  plataforma»). Ni base de datos ni logros.
"""

from __future__ import annotations

import time
from typing import Any

from aiohttp import web

from bot.cogs.universo.comun import _COG_KEY, _json_body
from bot.services.universo import (
    UniversoError,
    bearer,
    device_report,
    platform_of,
)

#: Un informe de medidas por miembro cada tanto como mucho (la página manda uno cada 5 min).
MEDIDAS_SECONDS = 60
#: Miembros que recuerda el tope de medidas; al pasarse se olvidan los que ya pueden mandar.
MAX_MEDIDAS_USERS = 2000


class TelemetryMixin:
    """Medidas de la página del cog `Universo` (sin logros: es telemetría)."""

    def measure(self, session: str | None, body: dict[str, Any]) -> None:
        """Suma a la telemetría un tramo de medidas de la página (sin logros: es telemetría).

        Raises:
            UniversoError: Igual que `enter`, o 429 si el miembro ya mandó uno hace menos
                de `MEDIDAS_SECONDS`.
        """
        guild, member = self._member(session, body)
        now = time.monotonic()
        if now - self._last_medidas.get(member.id, -MEDIDAS_SECONDS) < MEDIDAS_SECONDS:
            raise UniversoError("Demasiadas medidas seguidas.", 429)
        if len(self._last_medidas) >= MAX_MEDIDAS_USERS:
            self._last_medidas = {
                k: t for k, t in self._last_medidas.items() if now - t < MEDIDAS_SECONDS
            }
        self._last_medidas[member.id] = now
        platform = platform_of(body.get("platform")) or "otra"
        for name, value, events in device_report(body):
            self.bot.telemetry.count(
                "universo_disp",
                f"{name} · {platform}",
                guild_id=guild.id,
                user_id=member.id,
                ms=value,
                events=events,
            )


async def _medidas(request: web.Request) -> web.Response:
    cog = request.app[_COG_KEY]
    body = await _json_body(request)
    cog.measure(bearer(request.headers.get("Authorization")), body)
    return web.json_response({"ok": True})
