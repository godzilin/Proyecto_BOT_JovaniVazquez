"""Telemetría interna: cuenta cuánto se usa cada cosa del bot y cómo va el NAS.

Es para decidir mejoras, no para los miembros: no tiene comandos propios (el
informe lo saca `informe`, en el cog `Admin`), no da logros y no mueve dinero.
Nunca guarda el contenido de un mensaje: solo cuántos hay, en qué canal y de qué
clase (con adjunto, enlace, respuesta, corto o largo).

Qué cuenta y dónde lo engancha (tipos de `bot.services.telemetry`):

- `slash` y `text`: comandos terminados (`on_app_command_completion`,
  `on_command_completion`) y fallidos (`on_command_error` aquí; los de `/`, en
  el cog `Errors`).
- `button` y `modal`: cada clic o formulario, con el nombre `Vista.método`
  (`CoinView.heads`). discord.py no avisa de los clics con un evento que diga qué
  botón fue, así que se envuelve el método que los despacha (ver `install_hooks`).
  Un clic que termina sin respuesta a Discord cuenta como error: es el «Esta
  interacción ha fallado» que ve el miembro. Los botones que atiende un listener
  (marcados con `LISTENER_ITEM`) no los cuenta la vista sino el listener.
- Latencia de la primera respuesta (`defer`, `send_message`, `edit_message`,
  `send_modal`), desde que Discord creó la interacción. Es lo que tiene que
  quedar por debajo de 3 segundos. Además, tipo `respuesta`, en tres tramos: lo
  que tarda la interacción en llegar al bot (incluye el desfase de reloj del NAS),
  lo que tarda el bot en empezar a contestar y el viaje de la respuesta a Discord.
- `message`, `message_type`, `reaction`, `voice` (minutos de persona en cada
  canal, leídos de la caché cada minuto) y `member` (altas y bajas).
- `log`: avisos y errores del log, por plantilla del mensaje (sin sus datos).
- `render`: lo que tarda cada dibujo de `NodeScene` y `BrowserScene`.
- `upload`: lo que tarda cada mensaje con archivos en subir a Discord, por nombre
  del archivo y tamaño. Se envuelve donde discord.py hace las peticiones con
  archivos (`HTTPClient.request` y, para las interacciones, el adaptador de
  webhooks).
- `lag`: cada vez que el bucle de eventos se para más de `LAG_CULPRIT_MS`, dónde
  estaba parado (la función del bot, `LoopWatchdog`) y cuánto.
- Al arrancar y una vez al día, el informe de `informe` se deja en `REPORTS_DIR`
  (`informes/`, montada desde el NAS) y se publica en el canal que haya elegido
  un administrador con `informe` (`publish_daily`), para que un agente lo lea
  sin pedirlo.
- Cada minuto, una muestra del sistema: memoria del contenedor (lo que vigila el
  `mem_limit` de `docker-compose.yml`), memoria del proceso, retraso del bucle
  de eventos, latencia con Discord, procesos de Chromium y Node abiertos, CPU
  del contenedor, tamaño de la base de datos y disco libre. Si el contenedor ha
  matado algún proceso por memoria desde la anterior, cuenta un `oom`.
- `arranque` (con la versión), `gateway` (conexión con Discord), `ratelimit` (los
  429 que avisa discord.py en el log, por ruta sin IDs ni tokens).
- `imagen`: del clic a que la primera imagen de la respuesta queda subida. Se
  saca del token de la interacción, que va en la ruta de la subida.
- `vista`, `vista_cerrada` y `caducada`: cada vista (partida, panel, menú) que se
  abre, la que se para (con lo que duró) y la que llega a su `timeout` sin que
  nadie la pare (abandonada, con lo que duró).
- `menu`: la opción elegida en cada desplegable (`HelpSelect → 🎰 Casino`), con
  las cifras cambiadas por `#` para que nunca salga un id.
- `peso`: lo que pesa cada imagen subida, en KB (va en los campos de `ms`).
- `universo_disp`: lo cuenta el cog `Universo` con lo que manda la página del
  Jovaniverso (fps, calidad, llamadas de dibujo, triángulos, entrada, GPU).
- `10062`: interacciones que Discord ya había dado por perdidas al contestar.
- `uso_mal`: comandos de texto mal escritos (falta o sobra un argumento, o el
  bot contesta «Uso: …»). `rechazo` (en `bot.utils.responder`): comandos que
  contestan con un error.
- `excepcion`: errores del log con traza, por tipo y por la función del bot
  donde saltaron (`KeyError · bot/cogs/coin.py:_settle`).
- `sql`: lo que tarda cada sentencia de cada repositorio (`economy · escritura`),
  recogido cada minuto de `bot.repositories.sqlite`.
- `api`: lo que tarda cada llamada a Discord por ruta (`PUT /guilds/{guild_id}/members/
  {user_id}/roles/{role_id}`: la plantilla de la ruta, sin IDs), con sus fallos.
- `tarea`: cada vuelta de cada tarea programada (`tasks.loop`), con lo que tardó, si
  falló y cuántas veces al día le toca (`Intereses.pay · 1/día`).
- `conexion_inicial`: lo que tarda el bot desde que arranca el proceso hasta que
  Discord le da paso (el primer `on_ready`).
- `bot_mensaje` y `reaccion_bot`: mensajes del bot y reacciones a ellos, por canal:
  qué avisos se leen.
- Cada alta (`member`) guarda quién entra, para ver sus primeros pasos en el
  informe (`nuevos`), con seudónimos como el resto.
- Al publicar el informe diario, un resumen con lo que hay que vigilar del día
  anterior (`telemetry.alerts`).

Todo se suma en memoria y se escribe una vez por minuto (`TelemetryService.flush`),
así que contar un clic nunca abre la base de datos antes de contestar a Discord.
Al descargar el cog (también al cerrar el bot) se vuelca lo pendiente.

Necesita los intents de miembros y de contenido de mensajes que ya pide el bot
(`bot.app.build_intents`); solo llama a la API de Discord para publicar el
informe diario.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import io
import json
import logging
import math
import os
import re
import shutil
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import discord
import discord.webhook.async_
from discord import app_commands
from discord.ext import commands, tasks
from discord.state import ConnectionState
from discord.ui.view import BaseView, ViewStore

from bot import __version__
from bot.cogs.deploy import NewsTarget, resolve_target
from bot.repositories import sqlite
from bot.services import telemetry as telemetry_service
from bot.services.telemetry import LISTENER_ITEM, SystemSample, TelemetryService, local_moment

logger = logging.getLogger(__name__)

#: Cada cuánto se vuelcan los contadores a SQLite y se toma la muestra del sistema.
FLUSH_SECONDS = 60
#: Cada cuánto se mira si el bucle de eventos va con retraso.
LAG_PROBE_SECONDS = 0.5
#: Retraso del bucle a partir del cual se apunta qué lo tenía parado (tipo `lag`).
LAG_CULPRIT_MS = 250
#: Cada cuánto mira el vigía (`LoopWatchdog`) si el bucle sigue parado.
LAG_WATCH_SECONDS = 0.05
#: Carpeta donde se deja cada día el informe, montada desde el NAS
#: (`docker-compose.yml`) para leerlo por la red; si no existe, no se guarda.
REPORTS_DIR = Path("informes")
#: Días que abarca el informe diario y cuántos informes se guardan.
DAILY_REPORT_DAYS = 30
KEPT_REPORTS = 14
#: Mensajes de este tamaño o menos son «cortos»; de más de `LONG_MESSAGE`, «largos».
SHORT_MESSAGE = 20
LONG_MESSAGE = 200
#: Caracteres de la plantilla de un aviso del log que entran en su nombre.
LOG_TEMPLATE_LENGTH = 80

# Procesos que cuentan como Chromium o Node en la muestra del sistema (`/proc/*/comm`).
CHROMIUM_NAMES = frozenset({"chrome", "chromium", "headless_shell", "chrome_crashpad"})
NODE_NAMES = frozenset({"node"})
# Memoria del contenedor: cgroup v2 y, si no, v1.
CGROUP_MEMORY_FILES = (
    Path("/sys/fs/cgroup/memory.current"),
    Path("/sys/fs/cgroup/memory/memory.usage_in_bytes"),
)
# CPU usada por el contenedor (cgroup v2, en µs) y matanzas por memoria (`oom_kill`).
CGROUP_CPU_FILE = Path("/sys/fs/cgroup/cpu.stat")
CGROUP_EVENTS_FILE = Path("/sys/fs/cgroup/memory.events")
# Bytes leídos y escritos en disco por el contenedor y su límite de memoria.
CGROUP_IO_FILE = Path("/sys/fs/cgroup/io.stat")
CGROUP_MEMORY_MAX_FILE = Path("/sys/fs/cgroup/memory.max")
# Archivos que se suben como imagen: los que cuentan para `imagen`.
IMAGE_SUFFIXES = (".gif", ".png", ".webp", ".jpg", ".jpeg")
# Error de Discord al contestar una interacción que ya ha caducado (3 s sin respuesta).
UNKNOWN_INTERACTION = 10062
# Respuesta del bot a un comando de texto mal escrito: «Uso: `.informe [días]`…».
USAGE_PREFIX = "Uso:"
USAGE_COMMAND = re.compile(r"`[./](\w+)")
# Aviso de discord.py al recibir un 429 (`discord.http`): se cuenta por ruta.
RATE_LIMITED = "We are being rate limited. %s %s responded with 429."
# Trozos de una ruta que son IDs o tokens: no salen en el nombre.
ROUTE_SECRET = re.compile(r"/(\d+|[\w-]{20,})(?=/|$)")
LINK = re.compile(r"https?://")

# Atributo que marca un método ya envuelto, para no envolverlo dos veces: cada
# `load_extension` vuelve a ejecutar este módulo, pero las clases de discord.py
# son las mismas.
_WRAPPED = "_telemetry_wrapped"
# Respuestas que cuentan como «primera respuesta» de una interacción.
FIRST_RESPONSES = ("defer", "send_message", "edit_message", "send_modal")
#: Tramos de la primera respuesta (tipo `respuesta`), en el orden en que pasan.
ARRIVAL = "1 llegada al bot"
IN_BOT = "2 dentro del bot"
TRIP = "3 viaje de la respuesta"
# Cuándo llegó al bot cada interacción por id: (`time.time()`, `perf_counter()`).
_arrivals: dict[int, tuple[float, float]] = {}
MAX_ARRIVALS = 500
# Nombre del botón, formulario o comando de cada interacción en curso, para
# ponérselo a un `10062` (la respuesta no sabe qué botón la pidió).
_item_names: dict[int, str] = {}
# Atributo con el `perf_counter()` de cuando se creó cada vista.
_BORN = "_telemetry_born"
# Momento en que se importó este módulo: si no se puede leer la edad del proceso
# (fuera de Linux), el arranque se cuenta desde aquí.
_IMPORTED = time.monotonic()
# Cuándo llegó cada interacción por su token, hasta que sube su primera imagen
# (`imagen`). El token va en la ruta de las respuestas; el id, no siempre.
_image_waits: dict[str, float] = {}


def _telemetry_of(client: object) -> TelemetryService | None:
    """La telemetría del bot, o `None` si ese cliente no tiene (pruebas, dobles)."""
    found = getattr(client, "telemetry", None)
    return found if isinstance(found, TelemetryService) else None


def _user_id(interaction: discord.Interaction) -> int | None:
    user = getattr(interaction, "user", None)
    return getattr(user, "id", None)


def _answered(interaction: discord.Interaction, sent: float, done: float) -> None:
    """Apunta lo que tardó la primera respuesta, desde que Discord creó la interacción.

    `sent` y `done` son el `perf_counter()` de cuando salió la respuesta y de
    cuando Discord la confirmó.
    """
    arrival = _arrivals.pop(interaction.id, None)
    telemetry = _telemetry_of(interaction.client)
    if telemetry is None:
        return
    created = interaction.created_at.timestamp()
    elapsed = (discord.utils.utcnow().timestamp() - created) * 1000
    # Con el reloj del NAS un poco adelantado podría salir negativo.
    telemetry.answered(interaction.id, max(0.0, elapsed))
    if arrival is None:
        return
    wall, perf = arrival
    for name, ms in (
        (ARRIVAL, (wall - created) * 1000),
        (IN_BOT, (sent - perf) * 1000),
        (TRIP, (done - sent) * 1000),
    ):
        telemetry.count("respuesta", name, guild_id=interaction.guild_id, ms=max(0.0, ms))


def _count_interaction(interaction: discord.Interaction, kind: str, name: str) -> None:
    """Cuenta un clic o formulario terminado, con su latencia y si se quedó sin respuesta."""
    telemetry = _telemetry_of(interaction.client)
    if telemetry is None:
        return
    telemetry.count(
        kind,
        name,
        guild_id=interaction.guild_id,
        user_id=_user_id(interaction),
        ms=telemetry.take_answer(interaction.id),
        error=not interaction.response.is_done(),
    )


def _count_choices(interaction: Any, name: str, values: list[str]) -> None:
    """Cuenta cada opción elegida en un desplegable de texto, sin cifras (nunca un id)."""
    telemetry = _telemetry_of(getattr(interaction, "client", None))
    if telemetry is None:
        return
    for value in values:
        choice = re.sub(r"[0-9]+", "#", str(value))[:60]
        telemetry.count("menu", f"{name} → {choice}", guild_id=interaction.guild_id)


def item_name(view: object, item: discord.ui.Item[Any]) -> str:
    """Nombre estable de un botón o menú: `Vista.método` (`CoinView.heads`).

    El `custom_id` no sirve: si no se fija, discord.py pone uno al azar. Se usa el
    nombre de la función del callback (`@discord.ui.button`), el de la clase del
    botón si es una subclase con su `callback`, o el de la función asignada.
    """
    callback = item.callback
    inner = getattr(callback, "callback", None)
    if inner is not None:
        part = getattr(inner, "__name__", type(item).__name__)
    elif getattr(callback, "__self__", None) is item:
        part = type(item).__name__
    else:
        part = getattr(callback, "__name__", type(item).__name__)
    return f"{type(view).__name__}.{part}"


def _wrap(owner: type, attribute: str, wrapper: Callable[[Any], Any]) -> None:
    """Sustituye `owner.attribute` por `wrapper(original)` una sola vez por proceso."""
    original = getattr(owner, attribute)
    if getattr(original, _WRAPPED, False):
        return
    wrapped = wrapper(original)
    setattr(wrapped, _WRAPPED, True)
    setattr(owner, attribute, wrapped)


def _timed_response(original: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(original)
    async def respond(self: discord.InteractionResponse, *args: Any, **kwargs: Any) -> Any:
        first = not self.is_done()
        sent = time.perf_counter()
        try:
            result = await original(self, *args, **kwargs)
        except discord.NotFound as error:
            if first and error.code == UNKNOWN_INTERACTION:
                _lost(self._parent)
            raise
        if first:
            _answered(self._parent, sent, time.perf_counter())
        return result

    return respond


def _lost(interaction: discord.Interaction) -> None:
    """Cuenta un `10062`: la interacción llegó a contestarse tarde, con su nombre y retraso."""
    command = getattr(interaction, "command", None)
    name = _item_names.get(interaction.id) or (
        command.qualified_name if command is not None else "?"
    )
    elapsed = (discord.utils.utcnow() - interaction.created_at).total_seconds() * 1000
    telemetry_service.report_event("10062", name, max(0.0, elapsed))


def _remember(interaction: Any, name: str) -> None:
    """Apunta qué botón o formulario atiende `interaction` mientras corre su callback."""
    interaction_id = getattr(interaction, "id", None)
    if isinstance(interaction_id, int):
        _item_names[interaction_id] = name
        while len(_item_names) > MAX_ARRIVALS:
            del _item_names[next(iter(_item_names))]


def _arrival(original: Callable[..., None]) -> Callable[..., None]:
    @functools.wraps(original)
    def parse(self: ConnectionState, data: Any) -> None:
        now = time.perf_counter()
        _arrivals[int(data["id"])] = (time.time(), now)
        while len(_arrivals) > MAX_ARRIVALS:
            del _arrivals[next(iter(_arrivals))]
        if token := data.get("token"):
            _image_waits[token] = now
            while len(_image_waits) > MAX_ARRIVALS:
                del _image_waits[next(iter(_image_waits))]
        original(self, data)

    return parse


def _counted_view_task(original: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(original)
    async def run(self: BaseView, item: discord.ui.Item[Any], interaction: Any) -> Any:
        name = item_name(self, item)
        _remember(interaction, name)
        try:
            return await original(self, item, interaction)
        finally:
            _item_names.pop(getattr(interaction, "id", None), None)
            if not getattr(item, LISTENER_ITEM, False):
                _count_interaction(interaction, "button", name)
            if isinstance(item, discord.ui.Select):
                _count_choices(interaction, name, item.values)

    return run


def _counted_modal_task(original: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(original)
    async def run(self: discord.ui.Modal, interaction: Any, *args: Any, **kwargs: Any) -> Any:
        _remember(interaction, type(self).__name__)
        try:
            return await original(self, interaction, *args, **kwargs)
        finally:
            _item_names.pop(getattr(interaction, "id", None), None)
            _count_interaction(interaction, "modal", type(self).__name__)

    return run


def _counted_dynamic_task(
    original: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(original)
    async def run(
        self: ViewStore, component_type: int, factory: type, interaction: Any, *args: Any
    ) -> Any:
        _remember(interaction, factory.__name__)
        try:
            return await original(self, component_type, factory, interaction, *args)
        finally:
            _item_names.pop(getattr(interaction, "id", None), None)
            _count_interaction(interaction, "button", factory.__name__)

    return run


def _upload_size(file: discord.File) -> int:
    """Bytes de un archivo por subir, sin mover el punto de lectura."""
    try:
        position = file.fp.tell()
        size = file.fp.seek(0, io.SEEK_END)
        file.fp.seek(position)
    except (OSError, ValueError):
        return 0
    return size


def _timed_upload(original: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(original)
    async def request(self: Any, *args: Any, **kwargs: Any) -> Any:
        route = args[0] if args else kwargs.get("route")
        files = kwargs.get("files") or ()
        sizes = [_upload_size(file) for file in files]
        start = time.perf_counter()
        failed = True
        try:
            result = await original(self, *args, **kwargs)
            failed = False
            return result
        finally:
            done = time.perf_counter()
            telemetry_service.report_event(
                "api", api_name(route), (done - start) * 1000, error=failed
            )
            if files:
                name = telemetry_service.upload_name(files[0].filename or "archivo", sum(sizes))
                telemetry_service.report_upload(name, (done - start) * 1000, error=failed)
            if files and not failed:
                _image_uploaded(route, files, done)
                for file, size in zip(files, sizes, strict=True):
                    if (file.filename or "").lower().endswith(IMAGE_SUFFIXES):
                        # Los KB van en el campo de los ms: así salen la media y el máximo.
                        image = re.sub(r"[0-9]+", "#", file.filename or "")
                        telemetry_service.report_event("peso", image, size / 1024)

    return request


def api_name(route: Any) -> str:
    """`PATCH /channels/{channel_id}/messages/{message_id}`: método y plantilla de la ruta.

    La plantilla nunca lleva IDs ni tokens: discord.py los pone aparte.
    """
    method = getattr(route, "method", None)
    path = getattr(route, "path", None)
    if not isinstance(method, str) or not isinstance(path, str):
        return "?"
    return f"{method} {path}"


def _image_uploaded(route: Any, files: Any, done: float) -> None:
    """Si es la primera imagen de una respuesta a una interacción, cuenta su espera."""
    token = getattr(route, "webhook_token", None)
    image = next(
        (f.filename for f in files if (f.filename or "").lower().endswith(IMAGE_SUFFIXES)), None
    )
    if token is None or image is None:
        return
    arrived = _image_waits.pop(token, None)
    if arrived is not None:
        name = re.sub(r"[0-9]+", "#", image)
        telemetry_service.report_event("imagen", name, (done - arrived) * 1000)


def _per_day(loop: tasks.Loop[Any]) -> str:
    """Cuántas vueltas al día le tocan a `loop` (`24/día`), según su intervalo u horas."""
    times = getattr(loop, "_time", None)
    if isinstance(times, list) and times:
        return f"{len(times)}/día"
    seconds = (loop.seconds or 0) + (loop.minutes or 0) * 60 + (loop.hours or 0) * 3600
    return f"{86400 / seconds:g}/día" if seconds else "?"


def _counted_start(original: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(original)
    def start(self: tasks.Loop[Any], *args: Any, **kwargs: Any) -> Any:
        if not getattr(self.coro, _WRAPPED, False):
            self.coro = _timed_task(self.coro, f"{self.coro.__qualname__} · {_per_day(self)}")
        return original(self, *args, **kwargs)

    return start


def _timed_task(coro: Callable[..., Awaitable[Any]], name: str) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(coro)
    async def run(*args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        failed = True
        try:
            result = await coro(*args, **kwargs)
            failed = False
            return result
        finally:
            telemetry_service.report_event(
                "tarea", name, (time.perf_counter() - start) * 1000, error=failed
            )

    setattr(run, _WRAPPED, True)
    return run


def _lifetime(view: BaseView) -> float | None:
    born = getattr(view, _BORN, None)
    return None if born is None else (time.perf_counter() - born) * 1000


def _counted_timeout(original: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(original)
    def dispatch(self: BaseView) -> Any:
        if not self.is_finished():
            telemetry_service.report_event("caducada", type(self).__name__, _lifetime(self))
        return original(self)

    return dispatch


def _counted_init(original: Callable[..., None]) -> Callable[..., None]:
    @functools.wraps(original)
    def init(self: BaseView, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        setattr(self, _BORN, time.perf_counter())
        telemetry_service.report_event("vista", type(self).__name__)

    return init


def _counted_stop(original: Callable[..., None]) -> Callable[..., None]:
    @functools.wraps(original)
    def stop(self: BaseView) -> None:
        if not self.is_finished():
            telemetry_service.report_event("vista_cerrada", type(self).__name__, _lifetime(self))
        original(self)

    return stop


def route_name(method: str, url: str) -> str:
    """`PATCH /channels/#/messages/#`: la ruta de un 429 sin IDs ni tokens."""
    path = url.split("?", 1)[0].split("/api/v", 1)[-1].split("/", 1)[-1]
    return f"{method} " + ROUTE_SECRET.sub("/#", "/" + path)


def install_hooks() -> None:
    """Envuelve los puntos de discord.py por donde pasan todas las respuestas y clics.

    discord.py no tiene un evento «se ha contestado a una interacción» ni dice qué
    botón se pulsó (solo un `custom_id`, que muchas vistas dejan al azar), así que
    se envuelven:

    - `InteractionResponse.defer`, `send_message`, `edit_message` y `send_modal`:
      la primera de ellas marca la latencia de la interacción;
    - `ConnectionState.parse_interaction_create`: cuándo llega cada interacción,
      para partir esa latencia en tramos;
    - `BaseView._scheduled_task`, `Modal._scheduled_task` y
      `ViewStore.schedule_dynamic_item_call`: corren el callback de cada botón,
      formulario o botón dinámico y saben cuál es;
    - `HTTPClient.request` y `AsyncWebhookAdapter.request`: por ahí salen todos
      los mensajes con archivos (los de las interacciones, por el segundo) y se
      mide lo que tarda la subida y, si es la primera imagen de una interacción,
      lo que ha esperado el miembro desde el clic (`imagen`);
    - `tasks.Loop.start`: envuelve la función de cada tarea programada para medir
      cada vuelta (`tarea`). Las que arrancan antes que este cog no se miden.
    - `BaseView.__init__`, `stop` y `_dispatch_timeout`: cada vista que se abre
      (`vista`), se para (`vista_cerrada`) o caduca sin pararse (`caducada`), con
      lo que duró.

    Salvo los de `InteractionResponse`, son métodos internos: si discord.py los cambia, la prueba de
    integración de la telemetría falla. Los envoltorios no cambian lo que hace el
    método; si el cliente no tiene telemetría, no hacen nada. Es idempotente y no
    se deshace al descargar el cog (sin telemetría no cuentan nada).
    """
    for name in FIRST_RESPONSES:
        _wrap(discord.InteractionResponse, name, _timed_response)
    _wrap(ConnectionState, "parse_interaction_create", _arrival)
    _wrap(BaseView, "_scheduled_task", _counted_view_task)
    _wrap(discord.ui.Modal, "_scheduled_task", _counted_modal_task)
    _wrap(ViewStore, "schedule_dynamic_item_call", _counted_dynamic_task)
    _wrap(discord.http.HTTPClient, "request", _timed_upload)
    _wrap(discord.webhook.async_.AsyncWebhookAdapter, "request", _timed_upload)
    _wrap(BaseView, "_dispatch_timeout", _counted_timeout)
    _wrap(BaseView, "__init__", _counted_init)
    _wrap(BaseView, "stop", _counted_stop)
    _wrap(tasks.Loop, "start", _counted_start)


def message_types(message: discord.Message) -> list[str]:
    """Clases de un mensaje para `message_type`; mira el texto sin guardarlo."""
    content = message.content or ""
    kinds = []
    if message.attachments:
        kinds.append("adjunto")
    if message.stickers:
        kinds.append("sticker")
    if LINK.search(content):
        kinds.append("enlace")
    if message.reference is not None:
        kinds.append("respuesta")
    if content:
        if len(content) <= SHORT_MESSAGE:
            kinds.append("corto")
        elif len(content) <= LONG_MESSAGE:
            kinds.append("medio")
        else:
            kinds.append("largo")
    return kinds


def read_system_usage() -> tuple[float | None, float, int, int, float, float]:
    """Memoria del contenedor y del proceso (MB), procesos de Chromium y Node y su memoria.

    Devuelve `(contenedor, proceso, chromium, node, chromium_mb, node_mb)`.

    Lee `/sys` y `/proc`, así que es bloqueante: se llama con `asyncio.to_thread`.
    Fuera de Linux devuelve ceros y `None`.
    """
    memory_mb = None
    for path in CGROUP_MEMORY_FILES:
        try:
            memory_mb = int(path.read_text().strip()) / 2**20
            break
        except (OSError, ValueError):
            continue
    rss_mb = 0.0
    # Sin `/proc` (fuera de Linux) la memoria del proceso se queda en 0.
    with contextlib.suppress(OSError):
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                rss_mb = int(line.split()[1]) / 1024
                break
    chromium = node = 0
    chromium_mb = node_mb = 0.0
    try:
        pids = [entry for entry in os.listdir("/proc") if entry.isdigit()]
    except OSError:
        pids = []
    for pid in pids:
        try:
            command = Path(f"/proc/{pid}/comm").read_text().strip()
            if command not in CHROMIUM_NAMES and command not in NODE_NAMES:
                continue
            resident = _rss_mb(Path(f"/proc/{pid}/status").read_text())
        except OSError:
            # El proceso ha terminado entre listar y leer.
            continue
        if command in CHROMIUM_NAMES:
            chromium += 1
            chromium_mb += resident
        else:
            node += 1
            node_mb += resident
    return memory_mb, rss_mb, chromium, node, chromium_mb, node_mb


def _rss_mb(status: str) -> float:
    """`VmRSS` de un `/proc/<pid>/status`, en MB (0 si no viene)."""
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    return 0.0


def read_container_usage(
    database: Path | None,
) -> tuple[float | None, int | None, float | None, float | None]:
    """CPU acumulada del contenedor (µs), procesos matados por memoria, base de datos y disco.

    Devuelve `(cpu_us, oom_kills, db_mb, disk_free_mb)`; lo que no se pueda leer
    (fuera de Linux, sin base de datos) sale `None`. Bloqueante: va en
    `asyncio.to_thread`. Lo leído y escrito en disco va aparte (`read_io_bytes`).
    """
    cpu_us: float | None = None
    oom_kills: int | None = None
    db_mb: float | None = None
    disk_free_mb: float | None = None
    with contextlib.suppress(OSError, ValueError):
        for line in CGROUP_CPU_FILE.read_text().splitlines():
            if line.startswith("usage_usec "):
                cpu_us = float(line.split()[1])
    with contextlib.suppress(OSError, ValueError):
        for line in CGROUP_EVENTS_FILE.read_text().splitlines():
            if line.startswith("oom_kill "):
                oom_kills = int(line.split()[1])
    if database is not None:
        with contextlib.suppress(OSError):
            files = (database, database.with_name(database.name + "-wal"))
            db_mb = sum(path.stat().st_size for path in files if path.exists()) / 2**20
            disk_free_mb = shutil.disk_usage(database.parent).free / 2**20
    return cpu_us, oom_kills, db_mb, disk_free_mb


def read_io_bytes() -> tuple[int, int] | None:
    """Bytes leídos y escritos en disco por el contenedor desde que arrancó (`io.stat`).

    Suma todos los discos. `None` fuera de Linux o sin cgroup v2. Bloqueante.
    """
    try:
        text = CGROUP_IO_FILE.read_text()
    except OSError:
        return None
    read = written = 0
    for line in text.splitlines():
        for field in line.split()[1:]:
            key, _, value = field.partition("=")
            if key == "rbytes" and value.isdigit():
                read += int(value)
            elif key == "wbytes" and value.isdigit():
                written += int(value)
    return read, written


def read_memory_limit_mb() -> float | None:
    """El `mem_limit` del contenedor en MB (`memory.max`); `None` si no hay o no se lee."""
    try:
        value = CGROUP_MEMORY_MAX_FILE.read_text().strip()
    except OSError:
        return None
    return int(value) / 2**20 if value.isdigit() else None


def process_age_seconds() -> float:
    """Segundos desde que arrancó el proceso (`/proc/self/stat`), o desde que se importó esto."""
    try:
        start_ticks = int(Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19])
        uptime = float(Path("/proc/uptime").read_text().split()[0])
        return max(0.0, uptime - start_ticks / os.sysconf("SC_CLK_TCK"))
    except (OSError, ValueError, IndexError, AttributeError):
        return time.monotonic() - _IMPORTED


def cpu_count() -> int:
    """Hilos que puede usar el proceso (los del NAS, salvo que Docker lo limite)."""
    # shortcut: no lee la cuota de `cpu.max`; el `docker-compose.yml` no la pone.
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0)) or 1
    return os.cpu_count() or 1


def exception_site(trace: Any) -> str:
    """La función del bot más interna de una traza (`bot/cogs/coin.py:_settle`).

    Si la traza no pasa por el bot (un error de discord.py o de la red), la última
    función de la traza.
    """
    last = None
    found = None
    while trace is not None:
        code = trace.tb_frame.f_code
        last = code
        if _bot_path(code.co_filename) is not None:
            found = code
        trace = trace.tb_next
    code = found or last
    if code is None:
        return "?"
    where = _bot_path(code.co_filename) or Path(code.co_filename).name
    return f"{where}:{code.co_name}"


def _bot_path(filename: str) -> str | None:
    """`bot/...` de un archivo del bot (con `/`), o `None` si es de fuera.

    En el NAS el bot está instalado en `site-packages/bot`: vale igual.
    """
    parts = Path(filename).parts
    if "bot" not in parts:
        return None
    index = len(parts) - 1 - parts[::-1].index("bot")
    return "/".join(parts[index:])


def blocking_site(frame: Any) -> str:
    """Dónde está parado un hilo: la función del bot más interna de su pila.

    `bot/services/coin_scene.py:board`. Si no hay ninguna del bot, la más
    interna sin más (`selectors.py:select` quiere decir que el bucle esperaba
    y lo que faltaba era CPU).
    """
    innermost = frame
    while frame is not None:
        parts = Path(frame.f_code.co_filename).parts
        if "bot" in parts:
            start = len(parts) - 1 - parts[::-1].index("bot")
            return f"{'/'.join(parts[start:])}:{frame.f_code.co_name}"
        frame = frame.f_back
    return f"{Path(innermost.f_code.co_filename).name}:{innermost.f_code.co_name}"


class LoopWatchdog:
    """Vigila desde otro hilo si el bucle de eventos se queda parado y apunta dónde.

    `_probe_lag` le dice cuándo tendría que despertar (`arm`). Si pasan
    `LAG_CULPRIT_MS` de esa hora y no ha despertado, el bucle está ocupado en
    otra cosa: se lee la pila de su hilo (`blocking_site`). Si el bloqueo es
    código en C que no suelta el GIL, el vigía no despierta hasta que acaba y
    puede apuntar lo que vino justo después.

    Args:
        loop_thread: El `threading.get_ident()` del hilo del bucle.
    """

    def __init__(self, loop_thread: int) -> None:
        self.loop_thread = loop_thread
        self.due = math.inf
        self.culprit: str | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="telemetria-vigia", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def arm(self, due: float) -> None:
        """El bucle tendría que despertar a la hora `due` (`time.perf_counter`)."""
        self.due = math.inf
        self.culprit = None
        self.due = due

    def disarm(self) -> str | None:
        """Ha despertado: dónde estaba parado, si ha llegado a pasar de `LAG_CULPRIT_MS`."""
        self.due = math.inf
        return self.culprit

    def _run(self) -> None:
        while not self._stop.wait(LAG_WATCH_SECONDS):
            late = time.perf_counter() - self.due
            if self.culprit is None and late * 1000 > LAG_CULPRIT_MS:
                frame = sys._current_frames().get(self.loop_thread)
                if frame is not None:
                    self.culprit = blocking_site(frame)


def report_inputs(bot: commands.Bot, guild: discord.Guild) -> tuple[list[str], dict[int, str]]:
    """Los comandos y los nombres de canal con los que se arma el informe de `guild`."""
    channel_names = {channel.id: channel.name for channel in guild.channels}
    channel_names.update({thread.id: thread.name for thread in guild.threads})
    # Los efectos de imagen son más de cien comandos de texto: si se listaran,
    # `comandos_sin_uso` sería casi entero de efectos.
    names = [c.name for c in bot.commands if "help_group" not in c.extras]
    return names, channel_names


def save_report(folder: Path, name: str, data: str, *, keep: int = KEPT_REPORTS) -> None:
    """Escribe el informe `name` en `folder` y borra los más viejos, dejando `keep`.

    Se escribe aparte y se renombra: quien lo lea por la red nunca ve uno a medias.
    """
    path = folder / name
    temporary = path.with_suffix(".tmp")
    temporary.write_text(data, encoding="utf-8")
    temporary.replace(path)
    reports = sorted(folder.glob("informe*.json"), key=lambda p: p.stat().st_mtime)
    for old in reports[:-keep]:
        old.unlink(missing_ok=True)


async def publish_report(
    channel: NewsTarget, name: str, data: str, summary: str | None = None
) -> bool:
    """Sube el informe `name` a `channel` con su resumen, salvo que el último mensaje ya sea ese.

    Así un reinicio el mismo día no lo repite. Devuelve si quedó publicado.
    """
    try:
        async for last in channel.history(limit=1):
            if any(attachment.filename == name for attachment in last.attachments):
                return True
        await channel.send(
            summary,
            file=discord.File(io.BytesIO(data.encode()), filename=name),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.HTTPException:
        logger.warning("No se pudo publicar el informe diario", exc_info=True)
        return False
    return True


class LogCounter(logging.Handler):
    """Cuenta los avisos y errores del log por plantilla, sin sus argumentos.

    Se cuenta `record.msg` («%s no está disponible; …») y no el mensaje ya
    formateado, que podría llevar nombres o IDs. Los registros que llegan desde
    otros hilos (`asyncio.to_thread`) se pasan al bucle de eventos, porque los
    contadores no son seguros entre hilos.
    """

    def __init__(self, telemetry: TelemetryService, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__(level=logging.WARNING)
        self.telemetry = telemetry
        self.loop = loop
        self.loop_thread = threading.get_ident()

    def emit(self, record: logging.LogRecord) -> None:
        name = f"{record.levelname} {record.name}: {str(record.msg)[:LOG_TEMPLATE_LENGTH]}"
        self._count("log", name)
        if record.exc_info and record.exc_info[0] is not None:
            error_type, _error, trace = record.exc_info
            self._count("excepcion", f"{error_type.__name__} · {exception_site(trace)}")
        args = record.args if isinstance(record.args, tuple) else ()
        if str(record.msg).startswith(RATE_LIMITED) and len(args) >= 2:
            self._count("ratelimit", route_name(str(args[0]), str(args[1])))

    def _count(self, kind: str, name: str) -> None:
        if threading.get_ident() == self.loop_thread:
            self.telemetry.count(kind, name, guild_id=None)
        elif not self.loop.is_closed():
            self.loop.call_soon_threadsafe(
                functools.partial(self.telemetry.count, kind, name, guild_id=None)
            )


class Telemetry(commands.Cog):
    """Escucha eventos y cuenta en `bot.telemetry`; vuelca cada minuto."""

    def __init__(self, bot: commands.Bot, telemetry: TelemetryService) -> None:
        self.bot = bot
        self.telemetry = telemetry
        self._log_counter: LogCounter | None = None
        self._lag_task: asyncio.Task[None] | None = None
        self._max_lag_ms = 0.0
        self._watchdog: LoopWatchdog | None = None
        # Última lectura de CPU del contenedor (µs, `perf_counter`) y de `oom_kill`.
        self._last_cpu: tuple[float, float] | None = None
        self._last_oom: int | None = None
        # Última lectura de bytes de disco del contenedor, para sacar lo de cada minuto.
        self._last_io: tuple[int, int] | None = None
        self._connected_once = False

    async def cog_load(self) -> None:
        """Engancha discord.py, el log y las escenas, y arranca los volcados."""
        install_hooks()
        self._log_counter = LogCounter(self.telemetry, asyncio.get_running_loop())
        logging.getLogger().addHandler(self._log_counter)
        telemetry_service.render_observers.add(self._on_render)
        telemetry_service.upload_observers.add(self._on_upload)
        telemetry_service.event_observers.add(self._on_event)
        # Recargar el cog no es un arranque del bot: se apunta una vez por cliente.
        if not getattr(self.bot, "_telemetry_started", False):
            self.bot._telemetry_started = True  # type: ignore[attr-defined]
            self.telemetry.count("arranque", __version__, guild_id=None)
        self.flush_loop.start()
        self.prune_loop.start()
        self.export_loop.start()
        self._watchdog = LoopWatchdog(threading.get_ident())
        self._watchdog.start()
        self._lag_task = asyncio.create_task(self._probe_lag(), name="telemetria-retraso")

    async def cog_unload(self) -> None:
        """Para los volcados, suelta los enganches y guarda lo pendiente."""
        self.flush_loop.cancel()
        self.prune_loop.cancel()
        self.export_loop.cancel()
        if self._lag_task is not None:
            self._lag_task.cancel()
        if self._watchdog is not None:
            self._watchdog.stop()
        telemetry_service.render_observers.discard(self._on_render)
        telemetry_service.upload_observers.discard(self._on_upload)
        telemetry_service.event_observers.discard(self._on_event)
        if self._log_counter is not None:
            logging.getLogger().removeHandler(self._log_counter)
        await self.telemetry.flush()

    def _on_render(self, scene: str, ms: float) -> None:
        self.telemetry.count("render", scene, guild_id=None, ms=ms)

    def _on_upload(self, name: str, ms: float, error: bool) -> None:
        self.telemetry.count("upload", name, guild_id=None, ms=ms, error=error)

    def _on_event(self, kind: str, name: str, ms: float | None, error: bool) -> None:
        self.telemetry.count(kind, name, guild_id=None, ms=ms, error=error)

    # --- Bucles -------------------------------------------------------------

    async def _probe_lag(self) -> None:
        """Mide cuánto tarda en despertar un `sleep` corto: el retraso del bucle.

        Si algo bloquea el bucle de eventos (un cálculo o una lectura de disco sin
        `to_thread`), todos los botones contestan tarde a la vez. Se guarda el peor
        retraso de cada minuto y, si pasa de `LAG_CULPRIT_MS`, qué lo tenía parado
        (tipo `lag`, con el nombre de `blocking_site`).
        """
        await self.bot.wait_until_ready()
        while True:
            start = time.perf_counter()
            if self._watchdog is not None:
                self._watchdog.arm(start + LAG_PROBE_SECONDS)
            await asyncio.sleep(LAG_PROBE_SECONDS)
            lag = (time.perf_counter() - start - LAG_PROBE_SECONDS) * 1000
            self._max_lag_ms = max(self._max_lag_ms, lag)
            culprit = self._watchdog.disarm() if self._watchdog is not None else None
            if culprit is not None and lag >= LAG_CULPRIT_MS:
                self.telemetry.count("lag", culprit, guild_id=None, ms=lag)

    @tasks.loop(seconds=FLUSH_SECONDS)
    async def flush_loop(self) -> None:
        """Cada minuto: minutos de voz, muestra del sistema y volcado a SQLite."""
        self._count_voice()
        await self._sample_system()
        for repository, operation, ms, error in sqlite.drain_timings():
            self.telemetry.count(
                "sql", f"{repository} · {operation}", guild_id=None, ms=ms, error=error
            )
        await self.telemetry.flush()

    @flush_loop.before_loop
    async def _before_flush(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(hours=24)
    async def prune_loop(self) -> None:
        """Una vez al día: pliega las horas viejas y borra lo que pasa de plazo."""
        await self.telemetry.prune()

    @prune_loop.before_loop
    async def _before_prune(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(hours=24)
    async def export_loop(self) -> None:
        """Al arrancar y una vez al día: deja el informe en `REPORTS_DIR` y lo publica.

        Es el mismo de `informe` (`DAILY_REPORT_DAYS` días), para que un agente lo
        lea sin pedirlo: desde la carpeta compartida del NAS o desde el canal que
        eligió un administrador con `informe`. Sin carpeta (fuera de Docker o sin
        montarla) no lo guarda; sin canal, no lo publica.
        """
        has_folder = await asyncio.to_thread(REPORTS_DIR.is_dir)
        for guild in self.bot.guilds:
            await self.publish_daily(guild, save=has_folder)

    async def publish_daily(self, guild: discord.Guild, *, save: bool = False) -> bool:
        """Saca el informe diario de `guild`, lo guarda si `save` y lo publica en su canal.

        Devuelve si lo publicó (o ya estaba publicado).
        """
        channel_id = await self.telemetry.report_channel(guild.id)
        channel = None if channel_id is None else await resolve_target(guild, channel_id)
        if not save and channel is None:
            return False
        names, channel_names = report_inputs(self.bot, guild)
        report = await self.telemetry.report(
            guild_id=guild.id,
            days=DAILY_REPORT_DAYS,
            commands=names,
            channel_names=channel_names,
        )
        prefix = "informe" if len(self.bot.guilds) == 1 else f"informe-{guild.id}"
        name = f"{prefix}-{report['hasta']}.json"
        data = json.dumps(report, ensure_ascii=False, separators=(",", ":"))
        if save:
            try:
                await asyncio.to_thread(save_report, REPORTS_DIR, name, data)
            except OSError:
                logger.warning("No se pudo guardar el informe diario", exc_info=True)
        if channel is None:
            return False
        limit = await asyncio.to_thread(read_memory_limit_mb)
        summary = telemetry_service.alerts_text(report, memory_limit_mb=limit)
        return await publish_report(channel, name, data, summary)

    @export_loop.before_loop
    async def _before_export(self) -> None:
        await self.bot.wait_until_ready()

    def _count_voice(self) -> None:
        """Un minuto de voz por persona conectada (sin bots), leído de la caché."""
        for guild in self.bot.guilds:
            for channel in (*guild.voice_channels, *guild.stage_channels):
                for member in channel.members:
                    if not member.bot:
                        self.telemetry.count(
                            "voice", str(channel.id), guild_id=guild.id, user_id=member.id
                        )

    async def _sample_system(self) -> None:
        memory_mb, rss_mb, chromium, node, chromium_mb, node_mb = await asyncio.to_thread(
            read_system_usage
        )
        io_read_mb, io_write_mb = self._io_mb(await asyncio.to_thread(read_io_bytes))
        repository = self.telemetry.repository
        database = repository.database_path if repository is not None else None
        cpu_us, oom_kills, db_mb, disk_free_mb = await asyncio.to_thread(
            read_container_usage, database
        )
        if oom_kills is not None:
            if self._last_oom is not None and oom_kills > self._last_oom:
                self.telemetry.count(
                    "oom", "contenedor", guild_id=None, events=oom_kills - self._last_oom
                )
            self._last_oom = oom_kills
        now = time.time()
        latency = self.bot.latency
        self.telemetry.sample(
            SystemSample(
                at=now,
                day=local_moment(now)[0],
                memory_mb=memory_mb,
                rss_mb=rss_mb,
                loop_lag_ms=self._max_lag_ms,
                gateway_ms=latency * 1000 if math.isfinite(latency) else None,
                chromium=chromium,
                node=node,
                cpu_pct=self._cpu_pct(cpu_us),
                db_mb=db_mb,
                disk_free_mb=disk_free_mb,
                chromium_mb=chromium_mb if chromium else None,
                node_mb=node_mb if node else None,
                io_read_mb=io_read_mb,
                io_write_mb=io_write_mb,
                tasks=len(asyncio.all_tasks()),
            )
        )
        self._max_lag_ms = 0.0

    def _io_mb(self, totals: tuple[int, int] | None) -> tuple[float | None, float | None]:
        """MB leídos y escritos en disco desde la lectura anterior."""
        if totals is None:
            return None, None
        last, self._last_io = self._last_io, totals
        if last is None or totals[0] < last[0] or totals[1] < last[1]:
            return None, None
        return (totals[0] - last[0]) / 2**20, (totals[1] - last[1]) / 2**20

    def _cpu_pct(self, cpu_us: float | None) -> float | None:
        """CPU del contenedor desde la lectura anterior, sobre todos los hilos (0-100)."""
        if cpu_us is None:
            return None
        now, last = time.perf_counter(), self._last_cpu
        self._last_cpu = (cpu_us, now)
        if last is None or now <= last[1] or cpu_us < last[0]:
            return None
        return min(100.0, 100 * (cpu_us - last[0]) / 1e6 / (now - last[1]) / cpu_count())

    # --- Eventos ------------------------------------------------------------

    @commands.Cog.listener()
    async def on_app_command_completion(
        self,
        interaction: discord.Interaction,
        command: app_commands.Command[Any, ..., Any] | app_commands.ContextMenu,
    ) -> None:
        """Cuenta un comando `/` terminado, con lo que tardó en contestar."""
        self.telemetry.count(
            "slash",
            command.qualified_name,
            guild_id=interaction.guild_id,
            user_id=_user_id(interaction),
            ms=self.telemetry.take_answer(interaction.id),
            error=not interaction.response.is_done(),
        )

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context) -> None:
        """Cuenta un comando de texto (`.`) terminado (salvo los secretos, como `enchufe`)."""
        if ctx.command is None or ctx.command.extras.get("secret"):
            return
        self.telemetry.count(
            "text",
            ctx.command.qualified_name,
            guild_id=ctx.guild.id if ctx.guild else None,
            user_id=ctx.author.id,
        )

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        """Cuenta un comando de texto fallido (no los que no existen ni los secretos)."""
        if (
            ctx.command is None
            or isinstance(error, commands.CommandNotFound)
            or ctx.command.extras.get("secret")
        ):
            return
        if isinstance(error, commands.UserInputError):
            self.telemetry.count(
                "uso_mal", ctx.command.qualified_name, guild_id=ctx.guild.id if ctx.guild else None
            )
        self.telemetry.count(
            "text",
            ctx.command.qualified_name,
            guild_id=ctx.guild.id if ctx.guild else None,
            user_id=ctx.author.id,
            error=True,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Cuenta el mensaje por canal y sus clases; nunca guarda el texto."""
        if message.guild is None:
            return
        if message.author.bot:
            if message.author == self.bot.user:
                self.telemetry.count(
                    "bot_mensaje", str(message.channel.id), guild_id=message.guild.id
                )
            # «Uso: `.comando …`» del propio bot: alguien escribió mal ese comando.
            if message.author == self.bot.user and message.content.startswith(USAGE_PREFIX):
                found = USAGE_COMMAND.search(message.content)
                name = found.group(1) if found else "?"
                self.telemetry.count("uso_mal", name, guild_id=message.guild.id)
            return
        guild_id = message.guild.id
        self.telemetry.count(
            "message", str(message.channel.id), guild_id=guild_id, user_id=message.author.id
        )
        for kind in message_types(message):
            self.telemetry.count("message_type", kind, guild_id=guild_id)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """Cuenta una reacción por canal (sin bots)."""
        if payload.guild_id is None or (payload.member is not None and payload.member.bot):
            return
        self.telemetry.count(
            "reaction", str(payload.channel_id), guild_id=payload.guild_id, user_id=payload.user_id
        )
        me = self.bot.user
        if me is not None and payload.message_author_id == me.id:
            self.telemetry.count(
                "reaccion_bot",
                str(payload.channel_id),
                guild_id=payload.guild_id,
                user_id=payload.user_id,
            )

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Cuenta una conexión con Discord; la primera, con lo que tardó el arranque."""
        self.telemetry.count("gateway", "conectada", guild_id=None)
        if not self._connected_once:
            self._connected_once = True
            age = await asyncio.to_thread(process_age_seconds)
            self.telemetry.count("conexion_inicial", "hasta conectar", guild_id=None, ms=age * 1000)

    @commands.Cog.listener()
    async def on_disconnect(self) -> None:
        """Cuenta un corte de la conexión con Discord (como error: así sale por día)."""
        self.telemetry.count("gateway", "desconexion", guild_id=None, error=True)

    @commands.Cog.listener()
    async def on_resumed(self) -> None:
        """Cuenta una conexión recuperada sin perder la sesión."""
        self.telemetry.count("gateway", "reanudada", guild_id=None)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Cuenta un alta en el servidor, con quién: sus primeros pasos salen en `nuevos`."""
        self.telemetry.count("member", "alta", guild_id=member.guild.id, user_id=member.id)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Cuenta una baja del servidor."""
        self.telemetry.count("member", "baja", guild_id=member.guild.id)


async def setup(bot: commands.Bot) -> None:
    """Registra la telemetría si el cliente la tiene (`BotClient.telemetry`)."""
    telemetry = _telemetry_of(bot)
    if telemetry is None:
        logger.warning("El cliente no tiene telemetría; no se cuenta nada")
        return
    await bot.add_cog(Telemetry(bot, telemetry))
