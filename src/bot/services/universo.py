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

También filtra lo que la página cuenta del paseo por el mundo (`walk_report`), que
solo alimenta logros, y monta las listas del menú del tabulador (`player_list` y
`backpack_list`), que solo leen.

No mueve dinero ni guarda datos.
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

from bot.services.economy import format_amount
from bot.services.levels import calculate_level_progress

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


#: Lugares del mundo (Gran Canaria) que la página puede decir que has pisado. Cada
#: uno es un logro (`universo_place_<clave>`); una clave que no está aquí no cuenta.
PLACES = frozenset({
    "nieves", "nublo", "bandama", "dunas", "faro", "plaza", "drago", "muelle", "canteras",
    "agua",
})  # fmt: skip
#: Gestos del robot: la animación del protocolo de la sala (de 4 en adelante; 0-3 son
#: quieto, andar, correr y saltar) y el artículo de la tienda que lo da. Solo se ven
#: los que se han comprado: la sala apaga los demás (`bot.services.universo_sala`).
#: Las claves de artículo no se cambian nunca; la del logro es la de después de
#: `gesto_` (`universo_gesture_saludo`).
GESTURES: dict[int, str] = {
    4: "gesto_saludo",
    5: "gesto_baile",
    6: "gesto_pulgar",
    7: "gesto_si",
    8: "gesto_no",
    9: "gesto_sentarse",
    10: "gesto_punetazo",
}
#: Cima del Pico de las Nieves, en decímetros (`PICO` de `universo/src/mundo/terreno.ts`):
#: centro x, z y radio (el de su lugar en `lugares.ts`).
SUMMIT_DM = (-50, 80, 100)


def gestures_owned(owned: frozenset[str]) -> frozenset[int]:
    """Las animaciones de gesto que puede hacer quien tiene los artículos `owned`."""
    return frozenset(anim for anim, key in GESTURES.items() if key in owned)


def on_summit(x_dm: int, z_dm: int) -> bool:
    """Si una posición (en decímetros) está en la cima del Pico de las Nieves."""
    cx, cz, radius = SUMMIT_DM
    return (x_dm - cx) ** 2 + (z_dm - cz) ** 2 < radius**2


#: Lo más rápido que se puede ir, en m/s. Correr va a 9; el margen cubre los saltos
#: y el redondeo. La distancia la dice la página: más de esto por segundo no cuenta.
MAX_SPEED = 12
#: Saltos por segundo que se aceptan: un salto dura casi un segundo en el aire.
MAX_JUMPS_PER_SECOND = 2
#: Deslizamientos por segundo que se aceptan: el impulso se recarga en un segundo.
MAX_SLIDES_PER_SECOND = 1
#: Trucos de piloto (`universo/src/mundo/jugador.ts`) que se aceptan por segundo: correr
#: por una pared o saltar de ella no baja de medio segundo, el doble salto se recarga en
#: el suelo o en una pared y el gancho tarda 3 s en volver.
TRICKS_PER_SECOND = {"wallruns": 2, "walljumps": 2, "doublejumps": 2, "grapples": 0.5}
#: Lo más rápido que se va encadenando impulsos, en m/s (`VEL_MAX` de la página).
TOP_SPEED = 20
#: Paredes seguidas sin tocar el suelo que da tiempo a encadenar por segundo.
MAX_CHAIN_PER_SECOND = 2


#: Teleportes por segundo que se aceptan en un informe: el menú tarda más en abrirse.
MAX_TELEPORTS_PER_SECOND = 0.5


def teleport_report(body: dict[str, object], elapsed: float) -> int:
    """Teleportes de un informe de paseo, recortados a lo que da tiempo a usar en `elapsed` s."""
    value = body.get("teleports")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return 0
    return min(value, int(max(elapsed, 0.0) * MAX_TELEPORTS_PER_SECOND))


@dataclass(frozen=True)
class Walk:
    """Un informe de paseo ya recortado (`walk_report`).

    Attributes:
        meters: Metros recorridos.
        jumps: Saltos desde el suelo.
        slides: Deslizamientos.
        places: Lugares pisados (claves de `PLACES`), sin repetir.
        tricks: Trucos de piloto por nombre de `TRICKS_PER_SECOND`, solo los que hay.
        speed: La mayor velocidad en horizontal, en m/s.
        chain: Lo más largo encadenando paredes sin tocar el suelo.
    """

    meters: int
    jumps: int
    slides: int
    places: list[str]
    tricks: dict[str, int]
    speed: int
    chain: int


def walk_report(body: dict[str, object], elapsed: float) -> Walk:
    """Lo que cuenta de un informe de paseo: metros, saltos, deslizamientos, lugares y trucos.

    La página no es de fiar: los contadores se recortan a lo que da tiempo a hacer en
    `elapsed` segundos (desde el informe anterior), la velocidad a la más alta posible y
    los lugares se filtran.
    """

    def count(value: object, limit: float) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return 0
        return min(value, int(limit))

    seconds = max(elapsed, 0.0)
    places = body.get("places")
    seen = []
    if isinstance(places, list):
        seen = sorted({place for place in places if isinstance(place, str) and place in PLACES})
    tricks = {
        name: n
        for name, per_second in TRICKS_PER_SECOND.items()
        if (n := count(body.get(name), seconds * per_second)) > 0
    }
    return Walk(
        meters=count(body.get("meters"), seconds * MAX_SPEED),
        jumps=count(body.get("jumps"), seconds * MAX_JUMPS_PER_SECOND),
        slides=count(body.get("slides"), seconds * MAX_SLIDES_PER_SECOND),
        places=seen,
        tricks=tricks,
        speed=count(body.get("speed"), TOP_SPEED),
        chain=count(body.get("chain"), seconds * MAX_CHAIN_PER_SECOND),
    )


#: Filas de la pestaña «Jugadores» del menú: los que están dentro van siempre; del resto,
#: los de más saldo hasta llenar.
MAX_PLAYER_ROWS = 100
_CUSTOM_EMOJI = re.compile(r"<a?:\w+:\d+>")


def player_list(
    members: list[tuple[int, str]],
    inside: set[int],
    balances: dict[int, int],
    xps: dict[int, int] | None,
    me: int,
    *,
    limit: int = MAX_PLAYER_ROWS,
) -> list[dict[str, Any]]:
    """Filas de la pestaña «Jugadores»: primero quien está en la sala y luego el resto, por saldo.

    Args:
        members: `(id, nombre)` de los miembros del servidor, sin bots.
        inside: Quién está ahora en la sala.
        balances: Saldo de cada uno (quien no tiene monedero, 0).
        xps: Experiencia de cada uno; `None` si el servidor no tiene niveles.
        me: Quien pide la lista (va siempre, marcado con `yo`).
    """
    rows = sorted(
        members,
        key=lambda m: (m[0] not in inside, m[0] != me, -balances.get(m[0], 0), m[1].casefold()),
    )
    out = []
    for user_id, name in rows:
        if len(out) >= limit and user_id not in inside and user_id != me:
            break
        level = None if xps is None else calculate_level_progress(xps.get(user_id, 0)).level
        out.append(
            {
                "nombre": name,
                "saldo": format_amount(balances.get(user_id, 0)),
                "nivel": level,
                "dentro": user_id in inside,
                "yo": user_id == me,
            }
        )
    return out


def backpack_list(entries: list[Any], now: float) -> list[dict[str, Any]]:
    """Lo que hay en una mochila, agrupado, para la pestaña «Mochila».

    `entries` son `InventoryEntry` de la tienda. Las unidades iguales (mismo nombre, emoji y
    tipo) van en una fila con su cantidad y la caducidad más cercana, en segundos desde ahora.
    Un emoji propio de Discord (`<:x:id>`) no se puede pintar en la página: va el del tipo.
    """
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for e in entries:
        emoji = e.emoji if e.emoji and not _CUSTOM_EMOJI.search(e.emoji) else e.kind.icon
        key = (e.name, emoji, e.kind.value)
        row = groups.setdefault(
            key,
            {
                "nombre": e.name,
                "emoji": emoji,
                "tipo": e.kind.title,
                "cantidad": 0,
                "puesto": False,
                "caduca": None,
            },
        )
        row["cantidad"] += 1
        row["puesto"] = row["puesto"] or bool(e.equipped)
        if e.expires_at is not None:
            left = max(0, int(e.expires_at - now))
            row["caduca"] = left if row["caduca"] is None else min(row["caduca"], left)
    return sorted(groups.values(), key=lambda r: (r["tipo"], r["nombre"].casefold()))
