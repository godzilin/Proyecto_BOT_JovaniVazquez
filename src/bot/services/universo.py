"""Login y sesiones del Jovaniverso (`universo`), la Activity del bot dentro de Discord.

Una Activity es una página web que Discord abre en un iframe. La página no es de
fiar (cualquiera puede tocar su JavaScript), así que no decide nada: pide cosas al
servidor web del bot (`bot.cogs.universo`) y el servidor contesta con lo que dicen
los servicios de siempre. Este módulo tiene las dos piezas que dicen quién es quién:

- `DiscordOAuth` cambia el código que Discord da a la página (`authorize` del SDK)
  por un token de acceso y pregunta a Discord de quién es. Es el flujo OAuth2 de
  las Activities: el secreto de la aplicación solo lo tiene el servidor.
- `SessionSigner` firma una sesión corta con el ID del miembro. La página la manda
  en cada llamada (`Authorization: Bearer …`) y el servidor no tiene que guardar
  nada: la firma dice que la sesión la dio él y que no ha caducado.

La clave de firma se sortea al arrancar el bot, así que un reinicio cierra todas las
sesiones. La página vuelve a hacer login sola al abrirse, así que da igual.

No mueve dinero ni guarda datos: solo identifica.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import aiohttp

#: Base de la API de Discord para el login.
DISCORD_API = "https://discord.com/api/v10"
#: Lo que dura una sesión. Una tarde larga en el universo; luego, login otra vez.
SESSION_SECONDS = 12 * 3600
#: Lo que se espera a Discord en cada paso del login.
OAUTH_TIMEOUT_SECONDS = 10
#: Forma de un código de autorización de Discord (30 caracteres hoy). Se mira antes
#: de mandarlo para no reenviar a Discord lo que llegue de la página sin filtrar.
_CODE_PATTERN = re.compile(r"[A-Za-z0-9]{10,100}")


class UniversoError(Exception):
    """Un fallo que se puede contar a quien usa la Activity.

    Attributes:
        message: Texto para la página, en español y sin datos internos.
        status: Código HTTP con que lo contesta el servidor web.
    """

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True, slots=True)
class DiscordLogin:
    """Resultado de un login con Discord.

    Attributes:
        access_token: Token de acceso del miembro. La página lo necesita para
            `authenticate` del SDK; el servidor no lo guarda.
        user_id: ID de Discord de quien ha hecho login.
    """

    access_token: str
    user_id: int


class DiscordOAuth:
    """Cambia el código de `authorize` por un token y averigua de quién es.

    Args:
        session: Sesión HTTP de quien la crea (el cog), que la cierra al descargarse.
        client_id: ID de la aplicación (el mismo que el del bot).
        client_secret: Secreto de OAuth2 de la aplicación. Nunca sale de aquí ni
            se registra en logs.
        api: Base de la API; las pruebas ponen la de un Discord falso.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        client_id: int,
        client_secret: str,
        api: str = DISCORD_API,
    ) -> None:
        self._session = session
        self._client_id = client_id
        self._client_secret = client_secret
        self._api = api.rstrip("/")

    async def exchange(self, code: object) -> DiscordLogin:
        """Hace el login con el código que la página recibió de Discord.

        Raises:
            UniversoError: Si el código no tiene buena forma (400), si Discord lo
                rechaza (401) o si Discord no contesta bien (502).
        """
        if not isinstance(code, str) or not _CODE_PATTERN.fullmatch(code):
            raise UniversoError("Ese código de Discord no tiene buena pinta.")
        token_data = await self._call(
            "POST",
            "/oauth2/token",
            data={
                "client_id": str(self._client_id),
                "client_secret": self._client_secret,
                "grant_type": "authorization_code",
                "code": code,
            },
        )
        access_token = token_data.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise UniversoError("Discord no ha dado el token de acceso.", 502)
        user = await self._call(
            "GET", "/users/@me", headers={"Authorization": f"Bearer {access_token}"}
        )
        user_id = user.get("id")
        if not isinstance(user_id, str) or not user_id.isdigit():
            raise UniversoError("Discord no ha dicho quién eres.", 502)
        return DiscordLogin(access_token=access_token, user_id=int(user_id))

    async def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """Llama a Discord y devuelve el JSON, o lanza un `UniversoError`."""
        timeout = aiohttp.ClientTimeout(total=OAUTH_TIMEOUT_SECONDS)
        try:
            async with self._session.request(
                method, f"{self._api}{path}", timeout=timeout, **kwargs
            ) as response:
                if response.status in (400, 401):
                    # Código caducado, ya usado o de otra aplicación: hay que repetir el login.
                    raise UniversoError("Discord no ha aceptado el login. Prueba otra vez.", 401)
                if response.status != 200:
                    raise UniversoError("Discord no contesta bien ahora mismo.", 502)
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as error:
            raise UniversoError("No se ha podido hablar con Discord.", 502) from error
        if not isinstance(data, dict):
            raise UniversoError("Discord ha contestado algo raro.", 502)
        return data


class SessionSigner:
    """Firma y comprueba sesiones sin guardarlas.

    Una sesión es `<id>.<caduca>.<firma>`: el ID del miembro, el momento (UNIX)
    en que caduca y un HMAC-SHA256 de los dos con la clave del proceso.

    Args:
        key: Clave de firma. Por defecto, 32 bytes al azar: las sesiones mueren
            al reiniciar el bot.
        clock: Reloj en segundos; las pruebas ponen uno fijo.
        lifetime: Segundos que vale cada sesión.
    """

    def __init__(
        self,
        key: bytes | None = None,
        *,
        clock: Callable[[], float] = time.time,
        lifetime: int = SESSION_SECONDS,
    ) -> None:
        self._key = key if key is not None else secrets.token_bytes(32)
        self._clock = clock
        self._lifetime = lifetime

    def issue(self, user_id: int) -> str:
        """Da una sesión nueva para `user_id`."""
        expires = int(self._clock()) + self._lifetime
        body = f"{user_id}.{expires}"
        return f"{body}.{self._sign(body)}"

    def verify(self, token: object) -> int | None:
        """Devuelve el ID del miembro si la sesión es buena, o `None` si no.

        Es mala si no tiene la forma, si la firma no cuadra (la ha tocado alguien
        o es de antes de un reinicio) o si ha caducado.
        """
        if not isinstance(token, str):
            return None
        parts = token.split(".")
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
            return None
        user_id, expires, signature = parts
        if not hmac.compare_digest(signature, self._sign(f"{user_id}.{expires}")):
            return None
        if int(expires) <= self._clock():
            return None
        return int(user_id)

    def _sign(self, body: str) -> str:
        digest = hmac.new(self._key, body.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def bearer(header: str | None) -> str | None:
    """Saca la sesión de una cabecera `Authorization: Bearer <sesión>`."""
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


#: Plataformas que dice el SDK (`discordSdk.platform`). Lo dice la página, así que
#: solo se usa para logros Comunes que no dan nada que se pueda forzar con valor.
PLATFORMS = frozenset({"desktop", "mobile"})


def platform_of(value: object) -> str | None:
    """La plataforma desde la que se abre la Activity, si es una conocida."""
    return value if isinstance(value, str) and value in PLATFORMS else None


def snowflake(value: object) -> int | None:
    """Un ID de Discord que llega de la página (texto de cifras), o `None`."""
    if isinstance(value, str) and value.isdigit() and len(value) <= 20:
        return int(value)
    return None
