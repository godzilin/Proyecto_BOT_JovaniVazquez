"""Lo que comparten las rutas del Jovaniverso: dónde vive el cog y cómo se contestan los fallos.

`_COG_KEY` vive aquí y no en el paquete: `load_extension` vuelve a ejecutar
`bot.cogs.universo` y una clave nueva no casaría con la de los módulos ya cargados.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web

from bot.services.universo import UniversoError

if TYPE_CHECKING:
    from bot.cogs.universo import Universo

logger = logging.getLogger(__name__)

#: Clave de la aplicación de aiohttp donde vive el cog.
_COG_KEY: web.AppKey[Universo] = web.AppKey("universo")


@web.middleware
async def _errors(request: web.Request, handler: Any) -> web.StreamResponse:
    """Convierte los fallos en JSON `{error}` sin trazas ni datos internos."""
    try:
        return await handler(request)
    except UniversoError as error:
        return web.json_response({"error": error.message}, status=error.status)
    except web.HTTPException:
        raise
    except Exception:
        logger.exception("Fallo en el servidor del Jovaniverso (%s)", request.path)
        return web.json_response({"error": "Algo ha fallado en el Jovaniverso."}, status=500)


async def _json_body(request: web.Request) -> dict[str, Any]:
    """El cuerpo JSON de la petición, que tiene que ser un objeto."""
    try:
        body = await request.json()
    except ValueError as error:
        raise UniversoError("La petición no es JSON.") from error
    if not isinstance(body, dict):
        raise UniversoError("La petición no es un objeto JSON.")
    return body
