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

También dice dónde están los lugares de los logros en el mundo publicado
(`world_places`, que la sala mira en cada pose), filtra lo que la página cuenta del paseo
y la sala no ve (`walk_report`, solo para logros), recorta las medidas de la página para la
telemetría (`device_report`) y monta las listas del menú del
tabulador (`player_list` y `backpack_list`), que solo leen.

No mueve dinero ni guarda datos.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
import secrets
import sys
import time
from array import array
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import aiohttp

from bot.services.economy import format_amount
from bot.services.levels import calculate_level_progress
from bot.services.universo_editor import CELL, CELL_TERRAIN_SIDE, TERRAIN_STEP

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
#: Lugares que son un círculo y que el editor puede mover (`zonas` del mundo publicado):
#: centro x, z y radio, en decímetros. Son los de serie (`zonasDeSerie` de
#: `universo/src/mundo/lugares.ts`; lo vigila `test_universo_mundo_serie.py`): un mundo
#: publicado sin su trozo `zonas`, o sin alguna, deja la de aquí.
ZONES_DM: dict[str, tuple[int, int, int]] = {
    "nublo": (160, 120, 120),
    "dunas": (0, -926, 240),
    "faro": (386, -1061, 90),
    "plaza": (-80, 600, 100),
    "drago": (-150, 640, 70),
    "canteras": (493, 927, 160),
}
#: Cima del Pico de las Nieves en la isla de serie (`PICO` de `universo/src/mundo/terreno.ts`):
#: centro x, z y radio, en decímetros. Con el terreno esculpido, la cima es su punto más alto
#: (`world_places`). Esta, `BANDAMA_DM` y `PIER_DM` las vigila `test_universo_mundo_serie.py`.
SUMMIT_DM = (-50, 80, 100)
#: Altura del suelo en la cima de serie (`terrenoEn(PICO)`), en dm.
SUMMIT_TOP_DM = 497
#: Lo que pueden estar los pies por debajo de la cima para contar como en ella, en dm.
SUMMIT_DROP_DM = 60
#: Caldera de Bandama (`BAND` de `terreno.ts`): centro x, z, el borde de la caldera (radio de
#: dentro y de fuera) y la altura mínima de los pies (por encima del fondo), en dm.
BANDAMA_DM = (481, 404, 120, 240, 90)
#: Muelle de Mogán (`MUELLE` de `terreno.ts`): arranque x, z, dirección hacia el mar, largo,
#: ancho y altura mínima de los pies (sobre la cubierta, no nadando debajo), en dm.
PIER_DM = (-780.5, -780.5, -0.70711, -0.70711, 260, 32, 8)
#: Por debajo de esta altura de los pies se está en el agua (0,25 m bajo el mar), en dm.
WATER_DM = -2.5


def gestures_owned(owned: frozenset[str]) -> frozenset[int]:
    """Las animaciones de gesto que puede hacer quien tiene los artículos `owned`."""
    return frozenset(anim for anim, key in GESTURES.items() if key in owned)


@dataclass(frozen=True)
class Places:
    """Dónde están los lugares de `PLACES` en un mundo publicado (`world_places`), en dm.

    Attributes:
        circles: Los que son un círculo: clave, centro x, z y radio al cuadrado.
        summit: La cima: centro x, z, radio al cuadrado y altura mínima de los pies.
    """

    circles: tuple[tuple[str, int, int, int], ...]
    summit: tuple[int, int, int, int]

    def at(self, x: int, y: int, z: int) -> list[str]:
        """Los lugares donde están unos pies en (x, y, z) decímetros.

        Es lo que mira la sala en cada pose: unas restas y multiplicaciones por lugar.
        """
        found = [key for key, cx, cz, r2 in self.circles if (x - cx) ** 2 + (z - cz) ** 2 < r2]
        sx, sz, sr2, sy = self.summit
        if (x - sx) ** 2 + (z - sz) ** 2 < sr2 and y > sy:
            found.append("nieves")
        bx, bz, inner, outer, by = BANDAMA_DM
        if inner**2 < (x - bx) ** 2 + (z - bz) ** 2 < outer**2 and y > by:
            found.append("bandama")
        px, pz, dx, dz, length, width, py = PIER_DM
        along = (x - px) * dx + (z - pz) * dz
        if 0 <= along <= length and abs((z - pz) * dx - (x - px) * dz) <= width / 2 and y > py:
            found.append("muelle")
        if y < WATER_DM:
            found.append("agua")
        return found


def world_places(pieces: Mapping[str, str]) -> Places:
    """Los lugares de un mundo publicado (`{clave: doc}`, ya validado; `{}`, la isla de serie).

    Las zonas salen del trozo `zonas` y la cima, del punto más alto del terreno esculpido; lo
    que el mundo no trae, de serie. Decodifica el terreno de cada celda: se llama en un hilo.
    """
    zones = dict(ZONES_DM)
    if raw := pieces.get("zonas"):
        for key, zone in json.loads(raw).get("zonas", {}).items():
            if key in zones:
                zones[key] = (round(zone["x"] * 10), round(zone["z"] * 10), round(zone["r"] * 10))
    sx, sz, radius = SUMMIT_DM
    top_cm = SUMMIT_TOP_DM * 10
    sculpted: tuple[int, int, int] | None = None
    for key, raw in pieces.items():
        if not key.startswith("c/"):
            continue
        terrain = json.loads(raw).get("terreno")
        if not terrain:
            continue
        heights = array("h", base64.b64decode(terrain["alturas"]))
        if sys.byteorder == "big":
            heights.byteswap()
        high = max(heights)
        if sculpted is None or high > sculpted[0]:
            i = heights.index(high)
            _, cx, cz = key.split("/")
            sculpted = (
                high,
                (int(cx) * CELL + i % CELL_TERRAIN_SIDE * TERRAIN_STEP) * 10,
                (int(cz) * CELL + i // CELL_TERRAIN_SIDE * TERRAIN_STEP) * 10,
            )
    if sculpted is not None:
        top_cm, sx, sz = sculpted
    return Places(
        circles=tuple((key, x, z, r * r) for key, (x, z, r) in sorted(zones.items())),
        summit=(sx, sz, radius * radius, top_cm // 10 - SUMMIT_DROP_DM),
    )


#: Los lugares de la isla de serie, para la sala de un servidor sin mundo publicado.
SERIE_PLACES = world_places({})


#: Trucos de piloto (`universo/src/mundo/jugador.ts`) que se aceptan por segundo: correr
#: por una pared o saltar de ella no baja de medio segundo, el doble salto se recarga en
#: el suelo o en una pared y el gancho tarda 3 s en volver desde que se suelta
#: (`GANCHO_RECARGA`).
TRICKS_PER_SECOND = {"wallruns": 2, "walljumps": 2, "doublejumps": 2, "grapples": 1 / 3}
#: Lo más rápido que se va encadenando impulsos, en m/s (`VEL_MAX` de la página).
TOP_SPEED = 20
#: Paredes seguidas sin tocar el suelo que da tiempo a encadenar por segundo.
MAX_CHAIN_PER_SECOND = 2


#: Teleportes por segundo que se aceptan en un informe: el menú tarda más en abrirse. La
#: sala deja saltar sin mirar la velocidad igual de a menudo (`universo_sala.LEAP_SECONDS`).
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
        tricks: Trucos de piloto por nombre de `TRICKS_PER_SECOND`, solo los que hay.
        speed: La mayor velocidad en horizontal, en m/s.
        chain: Lo más largo encadenando paredes sin tocar el suelo.
    """

    tricks: dict[str, int]
    speed: int
    chain: int


def walk_report(body: dict[str, object], elapsed: float) -> Walk:
    """Lo que cuenta de un informe de paseo: los trucos de piloto, la velocidad y la cadena.

    Es lo que la sala no ve en las poses: los metros, saltos, deslizamientos y lugares los
    cuenta ella (`bot.services.universo_sala`), y lo que la página mande de eso se ignora.
    La página no es de fiar: los contadores se recortan a lo que da tiempo a hacer en
    `elapsed` segundos (desde el informe anterior) y la velocidad, a la más alta posible.
    """

    def count(value: object, limit: float) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return 0
        return min(value, int(limit))

    seconds = max(elapsed, 0.0)
    tricks = {
        name: n
        for name, per_second in TRICKS_PER_SECOND.items()
        if (n := count(body.get(name), seconds * per_second)) > 0
    }
    return Walk(
        tricks=tricks,
        speed=count(body.get("speed"), TOP_SPEED),
        chain=count(body.get("chain"), seconds * MAX_CHAIN_PER_SECOND),
    )


#: Medidas de la página (`/api/medidas`, `universo/src/medidas.ts`): topes de cada campo.
#: Un tramo son los fotogramas desde el informe anterior (cada 5 min o al ocultarse).
MAX_TRAMO_SECONDS = 900
MAX_FPS = 240
MAX_CALLS = 5000
MAX_TRIANGLES = 20_000_000
MAX_ENTRY_MS = 300_000
MAX_CHANGES = 50
#: Lo que se acepta de `devicePixelRatio` por la calidad adaptativa (`mundo/calidad.ts`).
RATIO_RANGE = (0.25, 4.0)
#: Tramos de la entrada: momento (ms desde que se abrió la página) en que acabó cada uno.
ENTRY_STEPS = ("login", "api", "mundo", "frame")
#: Familias de GPU que la página puede decir (`familiaGpu` en `medidas.ts`); lo demás, «otra».
GPU_FAMILIES = frozenset(
    {"adreno", "mali", "powervr", "apple", "intel", "nvidia", "amd", "xclipse", "software"}
)
#: Pantallas, redondeadas a 100 px y con tope: así no identifican a nadie.
SCREEN_STEP = 100
MAX_SCREEN = 4000


def _measure(value: object, high: float, low: float = 0.0) -> float | None:
    """Un número de la página recortado a `[low, high]`, o `None` si no es un número."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return min(max(float(value), low), high)


def device_report(body: dict[str, object]) -> list[tuple[str, float | None, int]]:
    """Lo que cuenta la telemetría de un tramo de medidas de la página.

    Devuelve `(medida, valor, eventos)` para `TelemetryService.count` (el valor va en los
    campos de `ms`). Solo cifras: la GPU, de una lista cerrada, y la pantalla, redondeada.
    La página no es de fiar: cada campo se recorta a su tope y lo que no es un número se
    ignora. Sin fotogramas, no hay nada del dibujo (la pestaña estuvo oculta).
    """
    rows: list[tuple[str, float | None, int]] = []

    def add(name: str, value: float | None) -> None:
        if value is not None:
            rows.append((name, value, 1))

    seconds = _measure(body.get("segundos"), MAX_TRAMO_SECONDS)
    frames = _measure(body.get("fotogramas"), MAX_FPS * MAX_TRAMO_SECONDS)
    if seconds and frames and seconds >= 1:
        fps = min(frames / seconds, MAX_FPS)
        rows.append(("tramo", None, 1))
        add("fps", fps)
        low = _measure(body.get("fps_p5"), fps)
        add("fps_p5", low)
        calls_max = _measure(body.get("llamadas_max"), MAX_CALLS)
        tris_max = _measure(body.get("triangulos_max"), MAX_TRIANGLES)
        add("llamadas_max", calls_max)
        add("triangulos_max", tris_max)
        if calls_max is not None:
            add("llamadas", _measure(body.get("llamadas"), calls_max))
        if tris_max is not None:
            add("triangulos", _measure(body.get("triangulos"), tris_max))
        ratio = _measure(body.get("ratio"), RATIO_RANGE[1], RATIO_RANGE[0])
        top = _measure(body.get("ratio_max"), RATIO_RANGE[1], RATIO_RANGE[0])
        if ratio is not None:
            add("ratio", ratio * 100)
            if top is not None and ratio < top - 0.01:
                rows.append(("resolucion_baja", None, 1))
        if body.get("sombras") is False:
            rows.append(("sin_sombras", None, 1))
    for name in ("bajadas", "subidas", "reconexiones"):
        n = int(_measure(body.get(name), MAX_CHANGES) or 0)
        if n:
            rows.append((name, None, n))
    entry = body.get("entrada")
    if isinstance(entry, dict):
        for step in ENTRY_STEPS:
            add(f"entrada_{step}", _measure(entry.get(step), MAX_ENTRY_MS))
    gpu = body.get("gpu")
    if isinstance(gpu, str):
        rows.append((f"gpu:{gpu if gpu in GPU_FAMILIES else 'otra'}", None, 1))
    width = _measure(body.get("ancho"), MAX_SCREEN, SCREEN_STEP)
    height = _measure(body.get("alto"), MAX_SCREEN, SCREEN_STEP)
    if width is not None and height is not None:
        size = [int(round(v / SCREEN_STEP) * SCREEN_STEP) for v in (width, height)]
        rows.append((f"pantalla:{size[0]}x{size[1]}", None, 1))
    return rows


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
