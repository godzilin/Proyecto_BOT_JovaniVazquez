"""Telemetría interna: contadores en memoria, volcado por lotes e informe agregado.

Responsabilidad: contar *cuánto* se usa el bot (comandos, botones, mensajes,
voz...) y cómo va el sistema, sin guardar nunca contenido de mensajes. Los
contadores viven en memoria (`TelemetryService.count` es síncrono y barato,
porque se llama en cada mensaje y clic) y se vuelcan a SQLite por lotes con
`flush`. El informe (`build_report`) es una función pura que convierte los
agregados que devuelve el repositorio en un JSON pensado para que lo lea un
agente que decide mejoras.

Límites: no importa Discord ni toca SQLite (eso es `bot.repositories.telemetry`),
no mueve dinero y no da logros. Es interna; los comandos que la usen van aparte.

Tipos de evento (`kind`) usados. La lista es libre y no se valida, pero estos
son los acordados:

- `slash`: comando con `/`. `text`: el mismo comando escrito con `.`.
- `button`: clic en un botón o menú. `modal`: envío de un formulario.
- `message`: mensaje en un canal (el nombre es el id del canal).
- `message_type`: clase de mensaje (texto, imagen, enlace, sticker...).
- `reaction`: reacción añadida (el nombre es el id del canal).
- `voice`: actividad de voz (el nombre es el id del canal).
- `member`: entradas y salidas de miembros.
- `log`: avisos y errores registrados por el bot.
- `render`: dibujos de imágenes o GIF (con su duración en `ms`).
- `upload`: mensajes con archivos subidos a Discord (lo que tarda la subida, en
  `ms`), por nombre del primer archivo y tamaño (`moneda.gif · 250 KB-1 MB`).
- `lag`: bloqueos del bucle de eventos de más de 250 ms (con su duración en
  `ms`), por la función del bot que lo tenía parado
  (`bot/services/coin_scene.py:board`).
- `arranque`: cada vez que arranca el bot, con su versión como nombre (`7.11.0`).
- `imagen`: del clic (o del `/`) a que la primera imagen de su respuesta queda
  subida a Discord, en `ms`, por archivo (`moneda.gif`). Es la espera que ve el
  miembro; si el dibujo estaba hecho antes del clic, se acerca a la de `upload`.
- `caducada`: vistas que caducan sin que nadie las cierre, por clase (`CoinView`).
- `gateway`: conexión con Discord (`conectada`, `desconexion`, `reanudada`).
- `ratelimit`: respuestas 429 de Discord, por método y ruta sin IDs ni tokens.
- `oom`: procesos del contenedor matados por quedarse sin memoria (Chromium, Node).
- `db`: lo que tarda en escribirse cada volcado de la telemetría (`volcado`), que
  mide de paso cómo va el disco del NAS.
- `sql`: lo que tarda cada sentencia de cada repositorio (`economy · escritura`).
- `candado`: lo que espera un `TimedLock` que ya estaba cogido, por su nombre.
- `node` (procesos que se abren, se cierran, se matan y la espera hasta tener uno)
  y `plan_b`: dibujos que no pudo hacer Node o Chromium y salieron con el plan B.
- `peso`: KB de cada imagen subida (en los campos de `ms`).
- `10062`: interacciones contestadas cuando Discord ya las daba por perdidas.
- `vista`, `vista_cerrada`: vistas abiertas y paradas (con lo que duraron).
- `menu`: opción elegida en cada desplegable.
- `uso_mal` y `rechazo`: comandos mal escritos y comandos que contestan con error.
- `excepcion`: errores con traza, por tipo y función del bot.
- `musica`: búsquedas (con su duración), pistas escuchadas o saltadas (con lo que
  sonaron), fallos, cola llena y posición en la cola (en los campos de `ms`).
- `api`: cada llamada a Discord por método y plantilla de la ruta, con su duración.
- `tarea`: cada vuelta de cada tarea programada (`tasks.loop`), con su duración.
- `conexion_inicial`: del arranque del proceso a la primera conexión con Discord.
- `bot_mensaje` y `reaccion_bot`: mensajes del bot y reacciones a ellos, por canal.
- `universo_disp`: cómo va el Jovaniverso en cada dispositivo (`/api/medidas`), por
  «medida · plataforma» (`fps · mobile`), con el valor en los campos de `ms`.

Privacidad: el único dato por persona es el `user_id` en `UserKey`, que se
borra a los 90 días y nunca sale en el informe (se seudonimiza como u1, u2...).
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import statistics
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from typing import TYPE_CHECKING, Any

from bot import __version__
from bot.repositories.economy import (
    HK_ACCOUNT_ID,
    HOUSING_AUCTION_ACCOUNT_ID,
    HOUSING_DEPOSIT_ACCOUNT_ID,
    POKER_POT_ACCOUNT_ID,
    PORRA_ACCOUNT_ID,
    PREDICT_ACCOUNT_ID,
    SHOP_ACCOUNT_ID,
    SLOTS_POT_ACCOUNT_ID,
    STATE_ACCOUNT_ID,
)
from bot.services.achievements import AVAILABLE
from bot.services.levels import TIMEZONE

if TYPE_CHECKING:
    from bot.repositories.telemetry import TelemetryRepository

logger = logging.getLogger(__name__)

MAX_NAME = 100
"""Longitud máxima de un nombre de evento; lo que sobra se corta."""

PENDING_SECONDS = 600
"""Segundos que una latencia de primera respuesta espera a que la recoja `take_answer`."""

MAX_PENDING = 5000
"""Latencias pendientes como mucho; al pasarse se tiran las más antiguas."""

LISTENER_ITEM = "telemetry_listener"
"""Atributo de un botón cuyos clics atiende un listener y no su vista.

La vista recibe el clic, lo rechaza sin contestar y el listener contesta después:
si la vista lo contara, saldría siempre sin respuesta. Lo cuenta el listener con
`TelemetryService.count` (como `Work.on_interaction` con el minijuego de `pala`).
"""

LATENCY_BUCKETS = (500, 1000, 3000)
"""Cortes de latencia en ms: `<500`, `<1000`, `<3000` y `>=3000`."""

REPORT_VERSION = 4

# Tipos cuyo nombre es un id de canal que se traduce a «#nombre» en el informe.
CHANNEL_KINDS = frozenset({"message", "voice", "reaction", "bot_mensaje", "reaccion_bot"})

# Nombres de las cuentas del sistema de la economía (user_id <= 0) en el informe.
SYSTEM_ACCOUNTS: dict[int, str] = {
    STATE_ACCOUNT_ID: "estado",
    SLOTS_POT_ACCOUNT_ID: "bote_tragaperras",
    SHOP_ACCOUNT_ID: "tienda",
    HK_ACCOUNT_ID: "hong_kong",
    PORRA_ACCOUNT_ID: "porras",
    POKER_POT_ACCOUNT_ID: "bote_poker",
    HOUSING_DEPOSIT_ACCOUNT_ID: "fianzas",
    HOUSING_AUCTION_ACCOUNT_ID: "subastas_pisos",
    PREDICT_ACCOUNT_ID: "predicciones",
}


@dataclass(frozen=True)
class CounterKey:
    """Clave de un contador: día canario, hora local (0-23), servidor, tipo y nombre."""

    day: str
    hour: int
    guild_id: int
    kind: str
    name: str


@dataclass
class Counter:
    """Valores acumulados de una clave: eventos, errores y distribución de latencias (ms)."""

    events: int = 0
    errors: int = 0
    ms_count: int = 0
    ms_total: int = 0
    ms_max: int = 0
    ms_lt500: int = 0
    ms_lt1000: int = 0
    ms_lt3000: int = 0
    ms_ge3000: int = 0

    def add(self, *, events: int, error: bool, ms: float | None) -> None:
        """Suma `events` eventos; cuenta un error si `error` y la latencia `ms` si no es None."""
        self.events += events
        if error:
            self.errors += 1
        if ms is None:
            return
        rounded = round(ms)
        self.ms_count += 1
        self.ms_total += rounded
        self.ms_max = max(self.ms_max, rounded)
        low, mid, high = LATENCY_BUCKETS
        if ms < low:
            self.ms_lt500 += 1
        elif ms < mid:
            self.ms_lt1000 += 1
        elif ms < high:
            self.ms_lt3000 += 1
        else:
            self.ms_ge3000 += 1

    def merge(self, other: Counter) -> None:
        """Suma otro contador al actual (el máximo se queda con el mayor)."""
        self.events += other.events
        self.errors += other.errors
        self.ms_count += other.ms_count
        self.ms_total += other.ms_total
        self.ms_max = max(self.ms_max, other.ms_max)
        self.ms_lt500 += other.ms_lt500
        self.ms_lt1000 += other.ms_lt1000
        self.ms_lt3000 += other.ms_lt3000
        self.ms_ge3000 += other.ms_ge3000


@dataclass(frozen=True)
class UserKey:
    """Un usuario distinto que hizo algo (día, servidor, tipo, nombre) ese día."""

    day: str
    guild_id: int
    kind: str
    name: str
    user_id: int


@dataclass(frozen=True)
class SystemSample:
    """Muestra del estado del proceso. `memory_mb` es la del contenedor (cgroup) y puede faltar.

    `cpu_pct` es la CPU del contenedor en el último minuto sobre todos los hilos
    (100 = todos ocupados); `db_mb`, lo que ocupa la base de datos con su WAL, y
    `disk_free_mb`, lo que queda libre en su disco. `chromium_mb` y `node_mb` son la
    memoria de esos procesos sumados; `io_read_mb` e `io_write_mb`, lo leído y
    escrito en disco por el contenedor en el último minuto, y `tasks`, las tareas
    vivas del bucle de eventos (si crecen sin parar, algo se acumula). Pueden faltar
    fuera de Linux.
    """

    at: float
    day: str
    memory_mb: float | None
    rss_mb: float
    loop_lag_ms: float
    gateway_ms: float | None
    chromium: int
    node: int
    cpu_pct: float | None = None
    db_mb: float | None = None
    disk_free_mb: float | None = None
    chromium_mb: float | None = None
    node_mb: float | None = None
    io_read_mb: float | None = None
    io_write_mb: float | None = None
    tasks: int | None = None


@dataclass
class Batch:
    """Lo acumulado entre dos volcados."""

    counters: dict[CounterKey, Counter] = field(default_factory=dict)
    users: set[UserKey] = field(default_factory=set)
    samples: list[SystemSample] = field(default_factory=list)

    def is_empty(self) -> bool:
        """Dice si no hay nada que escribir."""
        return not (self.counters or self.users or self.samples)


@dataclass
class ReportData:
    """Agregados que el repositorio entrega a `build_report`.

    - `usage`: dicts con `kind, name, events, errors, ms_count, ms_total, ms_max,
      lt500, lt1000, lt3000, ge3000`.
    - `daily`: tuplas `(day, kind, events, errors)`.
    - `daily_ms`: tuplas `(day, kind, ms_count, ms_total, ge3000)` de los tipos con latencia.
    - `starts`: tuplas `(day, version, veces)` de los arranques del bot.
    - `hourly`: tuplas `(hour, kind, events)` solo de horas 0-23.
    - `users`: tuplas `(day, kind, name, user_id)`.
    - `system`: dicts con `day, memory_max, memory_avg, rss_max, lag_max,
      gateway_avg, chromium_max, node_max, cpu_max, cpu_avg, db_max, disk_min`.
    - `casino`: dicts con `game, plays, free, players, wagered, paid`.
    - `balances`: saldos de los miembros; `system_accounts`: `{user_id <= 0: saldo}`.
    - `ledger`: dicts con `reason, moves, inflow, outflow` (solo miembros).
    - `ledger_daily`: tuplas `(day, inflow, outflow, moves)` de los miembros.
    - `state_daily`: tuplas `(day, motivo, inflow, outflow)` de la cuenta del Estado;
      el motivo es la primera parte del de la economía (`irpf` de `irpf:juego`).
    - `achievements`: tuplas `(logro, total, en la ventana)`; `achievement_members`,
      miembros con algún logro.
    - `shop`: dicts con `name, kind, sales, renewals, buyers, spent`.
    - `pets`: dicts con `species, total, active`.
    """

    usage: list[dict[str, Any]] = field(default_factory=list)
    daily: list[tuple[str, str, int, int]] = field(default_factory=list)
    daily_ms: list[tuple[str, str, int, int, int]] = field(default_factory=list)
    starts: list[tuple[str, str, int]] = field(default_factory=list)
    hourly: list[tuple[int, str, int]] = field(default_factory=list)
    users: list[tuple[str, str, str, int]] = field(default_factory=list)
    system: list[dict[str, Any]] = field(default_factory=list)
    casino: list[dict[str, Any]] = field(default_factory=list)
    balances: list[int] = field(default_factory=list)
    system_accounts: dict[int, int] = field(default_factory=dict)
    ledger: list[dict[str, Any]] = field(default_factory=list)
    ledger_daily: list[tuple[str, int, int, int]] = field(default_factory=list)
    state_daily: list[tuple[str, str, int, int]] = field(default_factory=list)
    achievements: list[tuple[str, int, int]] = field(default_factory=list)
    achievement_members: int = 0
    shop: list[dict[str, Any]] = field(default_factory=list)
    pets: list[dict[str, Any]] = field(default_factory=list)


#: Quien quiera saber lo que tarda cada dibujo se apunta aquí (el cog `Telemetry`).
#: Las escenas (`NodeScene`, `BrowserScene`) no tienen el bot a mano y avisan con
#: `report_render`; sin nadie apuntado no pasa nada.
render_observers: set[Callable[[str, float], None]] = set()


def report_render(scene: str, ms: float) -> None:
    """Avisa de que la escena `scene` ha pintado en `ms` milisegundos."""
    for observer in tuple(render_observers):
        observer(scene, ms)


#: Quien quiera saber lo que tarda cada subida de archivos a Discord se apunta aquí
#: (el cog `Telemetry`, que también engancha discord.py para avisar con `report_upload`).
upload_observers: set[Callable[[str, float, bool], None]] = set()

#: Tramos de tamaño de una subida: hasta cuántos bytes y cómo se llama.
UPLOAD_SIZES = ((250_000, "<250 KB"), (1_000_000, "250 KB-1 MB"), (3_000_000, "1-3 MB"))


def upload_name(filename: str, size: int) -> str:
    """Nombre de una subida: el archivo sin cifras (nunca un id) y su tramo de tamaño."""
    label = next((name for limit, name in UPLOAD_SIZES if size < limit), ">3 MB")
    return f"{re.sub(r'[0-9]+', '#', filename)} · {label}"


def report_upload(name: str, ms: float, *, error: bool = False) -> None:
    """Avisa de que la subida `name` (ver `upload_name`) ha tardado `ms` milisegundos."""
    for observer in tuple(upload_observers):
        observer(name, ms, error)


#: Quien quiera contar lo que pasa lejos de un cliente (vistas que caducan, esperas
#: de imagen) se apunta aquí con `(tipo, nombre, ms, error)`: el cog `Telemetry`.
event_observers: set[Callable[[str, str, float | None, bool], None]] = set()


def report_event(kind: str, name: str, ms: float | None = None, *, error: bool = False) -> None:
    """Avisa de un evento `kind`/`name` sin servidor (con su duración en `ms`, si la tiene)."""
    for observer in tuple(event_observers):
        observer(kind, name, ms, error)


class TimedLock(asyncio.Lock):
    """`asyncio.Lock` que avisa (`candado`) de lo que se espera cuando ya estaba cogido.

    Si estaba libre no cuenta nada: solo interesan los clics que esperan a otro.

    Args:
        name: Nombre estable en el informe (`CoinView._lock`).
    """

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name

    async def acquire(self) -> bool:
        if not self.locked():
            return await super().acquire()
        start = time.perf_counter()
        try:
            return await super().acquire()
        finally:
            report_event("candado", self.name, (time.perf_counter() - start) * 1000)


def local_moment(at: float) -> tuple[str, int]:
    """Devuelve el día (`YYYY-MM-DD`) y la hora (0-23) canarios de un epoch."""
    moment = datetime.fromtimestamp(at, TIMEZONE)
    return moment.date().isoformat(), moment.hour


def gini(values: Sequence[int]) -> float:
    """Índice de Gini de `values`: 0 si está vacío o todo es cero, hacia 1 cuanto más desigual."""
    total = sum(values)
    count = len(values)
    if count == 0 or total <= 0:
        return 0.0
    ordered = sorted(values)
    weighted = sum((2 * rank - count - 1) * value for rank, value in enumerate(ordered, 1))
    return weighted / (count * total)


def _round_or_none(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(float(value), digits)


def _channel_label(kind: str, name: str, channel_names: Mapping[int, str]) -> str:
    """Traduce el id de canal de `name` a «#nombre» si se conoce; si no, lo deja igual."""
    if kind in CHANNEL_KINDS and name.lstrip("-").isdigit():
        found = channel_names.get(int(name))
        if found is not None:
            return f"#{found}"
    return name


def _usage_entries(
    data: ReportData,
    channel_names: Mapping[int, str],
    users_by_key: Mapping[tuple[str, str], set[int]],
) -> list[dict[str, Any]]:
    rows = sorted(data.usage, key=lambda r: (r["kind"], -int(r["events"]), r["name"]))
    entries = []
    for row in rows:
        count = int(row["ms_count"])
        entries.append(
            {
                "tipo": row["kind"],
                "nombre": _channel_label(row["kind"], row["name"], channel_names),
                "eventos": int(row["events"]),
                "errores": int(row["errors"]),
                "usuarios": len(users_by_key.get((row["kind"], row["name"]), ())),
                "ms_medio": round(int(row["ms_total"]) / count, 1) if count else None,
                "ms_max": int(row["ms_max"]),
                "ms": {
                    "<500": int(row["lt500"]),
                    "<1000": int(row["lt1000"]),
                    "<3000": int(row["lt3000"]),
                    ">=3000": int(row["ge3000"]),
                },
            }
        )
    return entries


def _daily_entries(data: ReportData) -> dict[str, dict[str, dict[str, int]]]:
    per_day: dict[str, dict[str, dict[str, int]]] = {}
    for day, kind, events, errors in data.daily:
        per_day.setdefault(day, {})[kind] = {
            "eventos": int(events),
            "errores": int(errors),
            "usuarios": 0,
        }
    distinct: dict[tuple[str, str], set[int]] = {}
    for day, kind, _name, user_id in data.users:
        distinct.setdefault((day, kind), set()).add(user_id)
    for (day, kind), ids in distinct.items():
        entry = per_day.setdefault(day, {}).setdefault(
            kind, {"eventos": 0, "errores": 0, "usuarios": 0}
        )
        entry["usuarios"] = len(ids)
    for day, kind, ms_count, ms_total, slow in data.daily_ms:
        if ms_count:
            entry = per_day.setdefault(day, {}).setdefault(
                kind, {"eventos": 0, "errores": 0, "usuarios": 0}
            )
            entry["ms_medio"] = round(int(ms_total) / int(ms_count), 1)
            entry["ms_n"] = int(ms_count)
            entry["lentos"] = int(slow)
    return dict(sorted(per_day.items()))


def _activity(
    data: ReportData, since: date, channel_names: Mapping[int, str]
) -> dict[str, dict[str, list[int]]]:
    """Días de actividad por usuario seudonimizado (u1, u2... sin el user_id)."""
    first_seen: dict[int, str] = {}
    for day, _kind, _name, user_id in data.users:
        if user_id not in first_seen or day < first_seen[user_id]:
            first_seen[user_id] = day
    order = sorted(first_seen, key=lambda uid: (first_seen[uid], uid))
    alias = {uid: f"u{index}" for index, uid in enumerate(order, 1)}
    offsets: dict[str, dict[str, set[int]]] = {}
    for day, kind, name, user_id in data.users:
        label = f"{kind}:{_channel_label(kind, name, channel_names)}"
        shift = (date.fromisoformat(day) - since).days
        offsets.setdefault(alias[user_id], {}).setdefault(label, set()).add(shift)
    return {
        who: {label: sorted(days) for label, days in sorted(labels.items())}
        for who, labels in sorted(offsets.items(), key=lambda item: int(item[0][1:]))
    }


def _economy(data: ReportData) -> dict[str, Any]:
    balances = data.balances
    total = sum(balances)
    holders = sorted((b for b in balances if b > 0), reverse=True)
    top = math.ceil(len(holders) * 0.1)
    top_share = sum(holders[:top]) / total if total > 0 else 0.0
    accounts = {
        SYSTEM_ACCOUNTS.get(account, f"cuenta_{account}"): int(balance)
        for account, balance in sorted(data.system_accounts.items(), reverse=True)
    }
    reasons = sorted(
        (
            {
                "motivo": row["reason"],
                "movimientos": int(row["moves"]),
                "entradas": int(row["inflow"]),
                "salidas": int(row["outflow"]),
            }
            for row in data.ledger
        ),
        key=lambda r: (-(r["entradas"] + r["salidas"]), r["motivo"]),
    )
    return {
        "masa_monetaria": int(total),
        "miembros_con_saldo": len(holders),
        "gini": round(gini(balances), 4),
        "top10_pct": round(top_share, 4),
        "cuentas_sistema": accounts,
        "motivos": reasons,
    }


def _economy_days(data: ReportData, until: date) -> dict[str, dict[str, Any]]:
    """Dinero de los miembros y del Estado por día; el último día, la foto entera.

    Por día: entradas, salidas y movimientos de los miembros, lo que recauda el
    Estado por impuesto (`impuestos`) y lo que gasta (`estado_gasto`). La masa
    monetaria, el Gini y el saldo del Estado solo se saben el día del informe: al
    juntar informes de días distintos sale la serie entera.
    """
    empty: dict[str, Any] = {"entradas": 0, "salidas": 0, "movimientos": 0}
    days: dict[str, dict[str, Any]] = {
        day: {"entradas": int(inflow), "salidas": int(outflow), "movimientos": int(moves)}
        for day, inflow, outflow, moves in data.ledger_daily
    }
    for day, reason, inflow, outflow in data.state_daily:
        entry = days.setdefault(day, dict(empty))
        if inflow:
            taxes = entry.setdefault("impuestos", {})
            taxes[reason] = taxes.get(reason, 0) + int(inflow)
        if outflow:
            entry["estado_gasto"] = entry.get("estado_gasto", 0) + int(outflow)
    if data.balances or data.system_accounts:
        today = days.setdefault(until.isoformat(), dict(empty))
        today["masa"] = int(sum(data.balances) + sum(data.system_accounts.values()))
        today["gini"] = round(gini(data.balances), 4)
        today["estado"] = int(data.system_accounts.get(STATE_ACCOUNT_ID, 0))
    return dict(sorted(days.items()))


#: Días que tiene que pasar un nuevo para contar como «vuelve» (`nuevos`).
RETURN_AFTER_DAYS = 7


def _newcomers(data: ReportData) -> dict[str, Any]:
    """Primeros pasos de quien entró en la ventana: si escribe, usa comandos o botones.

    Sale de `users`: el alta (`member:alta`) y el primer día de cada cosa. Solo
    cuentas, sin seudónimos.
    """
    joined: dict[int, str] = {}
    first: dict[tuple[int, str], str] = {}
    last: dict[int, str] = {}
    step_of = {
        "message": "mensaje",
        "slash": "comando",
        "text": "comando",
        "button": "boton",
        "modal": "boton",
    }
    for day, kind, name, user_id in data.users:
        if kind == "member" and name == "alta":
            joined[user_id] = min(day, joined.get(user_id, day))
            continue
        step = step_of.get(kind)
        if step is not None:
            key = (user_id, step)
            first[key] = min(day, first.get(key, day))
        last[user_id] = max(day, last.get(user_id, day))

    def days_to(step: str) -> list[int]:
        return sorted(
            (date.fromisoformat(first[(uid, step)]) - date.fromisoformat(day)).days
            for uid, day in joined.items()
            if (uid, step) in first and first[(uid, step)] >= day
        )

    def median(values: list[int]) -> float | None:
        return float(statistics.median(values)) if values else None

    returned = sum(
        1
        for uid, day in joined.items()
        if uid in last
        and (date.fromisoformat(last[uid]) - date.fromisoformat(day)).days >= RETURN_AFTER_DAYS
    )
    message, command, button = days_to("mensaje"), days_to("comando"), days_to("boton")
    return {
        "altas": len(joined),
        "escribieron": len(message),
        "usaron_comandos": len(command),
        "usaron_botones": len(button),
        "volvieron_tras_7_dias": returned,
        "dias_hasta_escribir_mediana": median(message),
        "dias_hasta_comando_mediana": median(command),
        "dias_hasta_boton_mediana": median(button),
    }


def _achievements(data: ReportData, catalog: Mapping[str, str]) -> dict[str, Any]:
    """Cuántos miembros tienen cada logro, frente a su rareza; y los que nadie tiene."""
    members = data.achievement_members
    rows = [
        {
            "logro": achievement,
            "rareza": catalog.get(achievement),
            "miembros": total,
            "en_la_ventana": recent,
            "pct_miembros": round(total / members, 4) if members else None,
        }
        for achievement, total, recent in sorted(data.achievements, key=lambda r: (-r[1], r[0]))
    ]
    owned = {achievement for achievement, _total, _recent in data.achievements}
    return {
        "miembros_con_logros": members,
        "por_logro": rows,
        "sin_conseguir": sorted(set(catalog) - owned),
    }


def _shop(data: ReportData) -> list[dict[str, Any]]:
    return [
        {
            "articulo": row["name"],
            "tipo": row["kind"],
            "ventas": int(row["sales"]),
            "renovaciones": int(row["renewals"] or 0),
            "compradores": int(row["buyers"]),
            "gastado": int(row["spent"] or 0),
        }
        for row in sorted(data.shop, key=lambda r: (-int(r["sales"]), str(r["name"])))
    ]


def _pets(data: ReportData) -> dict[str, dict[str, int]]:
    return {
        str(row["species"]): {"total": int(row["total"]), "activas": int(row["active"] or 0)}
        for row in sorted(data.pets, key=lambda r: str(r["species"]))
    }


def build_report(
    data: ReportData,
    *,
    generated_at: float,
    days: int,
    commands: Iterable[str],
    channel_names: Mapping[int, str],
    guild_id: int = 0,
    achievements: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Convierte los agregados en el informe JSON (claves en español, serializable tal cual).

    Args:
        data: Agregados del repositorio.
        generated_at: Epoch del momento del informe; fija el «hasta» canario.
        days: Ventana en días (incluye hoy).
        commands: Nombres de todos los comandos del bot, para `comandos_sin_uso`.
        channel_names: Nombres de canal por id, para traducir `message`, `voice`, `reaction`.
        guild_id: Servidor del informe (solo se copia a `servidor`).
        achievements: Rareza de cada logro del catálogo por id, para `logros`.

    El `user_id` nunca aparece: la actividad va por seudónimos u1, u2...
    """
    until = datetime.fromtimestamp(generated_at, TIMEZONE).date()
    since = until - timedelta(days=max(days, 1) - 1)
    users_by_key: dict[tuple[str, str], set[int]] = {}
    for _day, kind, name, user_id in data.users:
        users_by_key.setdefault((kind, name), set()).add(user_id)
    hourly: dict[str, list[int]] = {}
    for hour, kind, events in data.hourly:
        if 0 <= hour <= 23:
            hourly.setdefault(kind, [0] * 24)[hour] += int(events)
    used = {row["name"] for row in data.usage if row["kind"] in ("slash", "text")}
    system = {
        row["day"]: {
            "memoria_mb_max": _round_or_none(row["memory_max"]),
            "memoria_mb_media": _round_or_none(row["memory_avg"]),
            "rss_mb_max": _round_or_none(row["rss_max"]),
            "lag_ms_max": _round_or_none(row["lag_max"]),
            "gateway_ms_media": _round_or_none(row["gateway_avg"]),
            "chromium_max": row["chromium_max"],
            "node_max": row["node_max"],
            "cpu_pct_max": _round_or_none(row.get("cpu_max")),
            "cpu_pct_media": _round_or_none(row.get("cpu_avg")),
            "db_mb_max": _round_or_none(row.get("db_max")),
            "disco_libre_mb_min": _round_or_none(row.get("disk_min")),
            "muestras": row.get("samples"),
            "chromium_mb_max": _round_or_none(row.get("chromium_mb_max")),
            "node_mb_max": _round_or_none(row.get("node_mb_max")),
            "disco_leido_mb": _round_or_none(row.get("io_read")),
            "disco_escrito_mb": _round_or_none(row.get("io_write")),
            "tareas_max": row.get("tasks_max"),
        }
        for row in sorted(data.system, key=lambda r: r["day"])
    }
    casino = [
        {
            "juego": row["game"],
            "jugadas": int(row["plays"]),
            "gratis": int(row["free"]),
            "jugadores": int(row["players"]),
            "apostado": int(row["wagered"]),
            "pagado": int(row["paid"]),
            "rtp": round(int(row["paid"]) / int(row["wagered"]), 4)
            if int(row["wagered"])
            else None,
        }
        for row in sorted(data.casino, key=lambda r: r["game"])
    ]
    starts: dict[str, dict[str, int]] = {}
    for day, version, times in sorted(data.starts):
        starts.setdefault(day, {})[version] = int(times)
    return {
        "version": REPORT_VERSION,
        "version_bot": __version__,
        "generado": datetime.fromtimestamp(generated_at, TIMEZONE).isoformat(timespec="seconds"),
        "servidor": guild_id,
        "desde": since.isoformat(),
        "hasta": until.isoformat(),
        "dias": days,
        "uso": _usage_entries(data, channel_names, users_by_key),
        "por_dia": _daily_entries(data),
        "por_hora": dict(sorted(hourly.items())),
        "comandos_sin_uso": sorted(set(commands) - used),
        "actividad": _activity(data, since, channel_names),
        "sistema": system,
        "arranques": starts,
        "casino": casino,
        "economia": _economy(data),
        "economia_dia": _economy_days(data, until),
        "nuevos": _newcomers(data),
        "logros": _achievements(data, achievements or {}),
        "tienda": _shop(data),
        "mascotas": _pets(data),
    }


class TelemetryService:
    """Contadores en memoria con volcado periódico a SQLite.

    Args:
        repository: Destino de los volcados; con `None` los contadores se acumulan y
            `flush` no hace nada (útil en pruebas o si la telemetría está apagada).
        clock: Reloj en epoch, sustituible en pruebas.
    """

    def __init__(
        self,
        repository: TelemetryRepository | None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.repository = repository
        self.clock = clock
        self._counters: dict[CounterKey, Counter] = {}
        self._users: set[UserKey] = set()
        self._samples: list[SystemSample] = []
        # id de interacción -> (ms, momento en que se guardó); el orden es la antigüedad.
        self._answers: OrderedDict[int, tuple[float, float]] = OrderedDict()
        self._flush_lock = asyncio.Lock()
        # Lo que tardó el último volcado, para contarlo en el siguiente (`db`).
        self._write_ms: float | None = None

    def count(
        self,
        kind: str,
        name: str,
        *,
        guild_id: int | None,
        user_id: int | None = None,
        ms: float | None = None,
        error: bool = False,
        events: int = 1,
        at: float | None = None,
    ) -> None:
        """Cuenta un evento en memoria (síncrono y barato).

        `guild_id=None` se guarda como 0 (sin servidor). `name` se corta a `MAX_NAME`.
        Si hay `user_id`, también se apunta que ese usuario hizo esto ese día.
        """
        name = name[:MAX_NAME]
        day, hour = local_moment(self.clock() if at is None else at)
        guild = guild_id or 0
        key = CounterKey(day, hour, guild, kind, name)
        counter = self._counters.get(key)
        if counter is None:
            counter = self._counters[key] = Counter()
        counter.add(events=events, error=error, ms=ms)
        if user_id is not None:
            self._users.add(UserKey(day, guild, kind, name, user_id))

    def answered(self, interaction_id: int, ms: float) -> None:
        """Guarda la latencia de primera respuesta hasta que `take_answer` la recoja.

        Si ya había una para ese id no se pisa (vale la primera). Pasado `MAX_PENDING`
        se tiran las más antiguas.
        """
        if interaction_id in self._answers:
            return
        self._answers[interaction_id] = (ms, self.clock())
        while len(self._answers) > MAX_PENDING:
            self._answers.popitem(last=False)

    def take_answer(self, interaction_id: int) -> float | None:
        """Saca y borra la latencia guardada de una interacción (None si no hay)."""
        found = self._answers.pop(interaction_id, None)
        return None if found is None else found[0]

    def sample(self, sample: SystemSample) -> None:
        """Apunta una muestra del estado del sistema."""
        self._samples.append(sample)

    def drain(self) -> Batch:
        """Se lleva todo lo acumulado, deja los buffers vacíos y tira las latencias caducadas."""
        batch = Batch(self._counters, self._users, self._samples)
        self._counters = {}
        self._users = set()
        self._samples = []
        limit = self.clock() - PENDING_SECONDS
        for interaction_id in [i for i, (_ms, stored) in self._answers.items() if stored < limit]:
            del self._answers[interaction_id]
        return batch

    async def flush(self) -> None:
        """Vuelca lo acumulado al repositorio.

        Si `write` falla se registra cuántos contadores se pierden y se sigue: perder
        un minuto de métricas es aceptable y no se reintenta.
        """
        async with self._flush_lock:
            batch = self.drain()
            if batch.is_empty() or self.repository is None:
                return
            if self._write_ms is not None:
                # Lo que tardó el volcado anterior va en este: mide el disco del NAS
                # sin dejar nada pendiente al cerrar.
                day, hour = local_moment(self.clock())
                key = CounterKey(day, hour, 0, "db", "volcado")
                batch.counters.setdefault(key, Counter()).add(
                    events=1, error=False, ms=self._write_ms
                )
                self._write_ms = None
            start = time.perf_counter()
            try:
                await self.repository.write(batch)
                self._write_ms = (time.perf_counter() - start) * 1000
            except Exception:
                logger.exception(
                    "No se pudo volcar la telemetría: se pierden %d contadores",
                    len(batch.counters),
                )

    async def prune(self) -> None:
        """Aplica la retención del repositorio (pliegue y borrado de datos viejos)."""
        if self.repository is not None:
            await self.repository.prune(now=self.clock())

    async def report_channel(self, guild_id: int) -> int | None:
        """Canal del informe diario del servidor; `None` si no publica o no hay repositorio."""
        if self.repository is None:
            return None
        return await self.repository.report_channel(guild_id)

    async def set_report_channel(self, guild_id: int, channel_id: int | None) -> bool:
        """Guarda el canal del informe diario (`None` lo quita); `False` sin repositorio."""
        if self.repository is None:
            return False
        await self.repository.set_report_channel(guild_id, channel_id)
        return True

    async def report(
        self,
        *,
        guild_id: int,
        days: int,
        commands: Iterable[str],
        channel_names: Mapping[int, str],
    ) -> dict[str, Any]:
        """Vuelca lo pendiente y devuelve el informe de los últimos `days` días de un servidor."""
        await self.flush()
        now = self.clock()
        today = datetime.fromtimestamp(now, TIMEZONE).date()
        since = today - timedelta(days=max(days, 1) - 1)
        since_ts = datetime.combine(since, dt_time.min, TIMEZONE).timestamp()
        if self.repository is None:
            data = ReportData()
        else:
            data = await self.repository.report_data(
                guild_id=guild_id, since_day=since.isoformat(), since_ts=since_ts
            )
        return build_report(
            data,
            generated_at=now,
            days=days,
            commands=commands,
            channel_names=channel_names,
            guild_id=guild_id,
            achievements={a.id: a.rarity.value[0] for a in AVAILABLE},
        )


# --------------------------------------------------------------------------- avisos

#: Lo que se avisa en el resumen diario: por tipo, campo del día (`eventos`,
#: `errores` o `lentos`, que son los de 3 s o más) y el texto con `{n}`.
DAY_ALERTS: tuple[tuple[str, str, str], ...] = (
    ("button", "errores", "🖱️ {n} clics sin respuesta («Esta interacción ha fallado»)"),
    ("10062", "eventos", "💥 {n} interacciones perdidas (10062)"),
    ("button", "lentos", "🐢 {n} clics de 3 s o más"),
    ("imagen", "lentos", "🖼️ {n} esperas de 3 s o más hasta ver la imagen"),
    ("sql", "errores", "🗄️ {n} fallos de la base de datos («database is locked»)"),
    ("sql", "lentos", "🗄️ {n} consultas de 3 s o más"),
    ("candado", "lentos", "🔒 {n} esperas de 3 s o más en candados"),
    ("plan_b", "eventos", "🎨 {n} dibujos con plan B (Node o Chromium no disponible)"),
    ("oom", "eventos", "🧠 {n} procesos matados por falta de memoria"),
    ("gateway", "errores", "📡 {n} cortes con Discord"),
    ("ratelimit", "eventos", "🚦 {n} respuestas 429 (Discord frena al bot)"),
    ("api", "errores", "🌐 {n} llamadas a Discord fallidas"),
    ("tarea", "errores", "⏰ {n} vueltas de tareas programadas fallidas"),
    ("excepcion", "eventos", "🐞 {n} errores con traza"),
)
#: Umbrales del sistema para avisar.
ALERT_CPU_PCT = 85.0
ALERT_MEMORY_SHARE = 0.85
ALERT_DISK_FREE_MB = 2048.0
ALERT_LAG_MS = 1000.0
ALERT_MISSING_MIN = 30
#: Minutos de un día: una muestra por minuto si el bot no se para.
DAY_MINUTES = 1440
#: Tope de caracteres de un mensaje de Discord.
MESSAGE_LIMIT = 2000


def alerts(report: Mapping[str, Any], *, memory_limit_mb: float | None = None) -> list[str]:
    """Lo que hay que vigilar del último día entero del informe (el de antes de `hasta`).

    Va por día (`por_dia`, `sistema`, `arranques`) para no repetir cada mañana lo
    que pasó hace semanas. Vacío si no hay nada.
    """
    try:
        day = (date.fromisoformat(str(report.get("hasta"))) - timedelta(days=1)).isoformat()
    except ValueError:
        return []
    found: list[str] = []
    per_kind = report.get("por_dia", {}).get(day, {})
    for kind, field_name, text in DAY_ALERTS:
        count = int(per_kind.get(kind, {}).get(field_name, 0) or 0)
        if count:
            found.append(text.format(n=count))
    system = report.get("sistema", {}).get(day, {})
    samples = system.get("muestras")
    if isinstance(samples, int) and DAY_MINUTES - samples > ALERT_MISSING_MIN:
        found.append(f"🔌 {DAY_MINUTES - samples} min sin datos (bot caído o parado)")
    cpu = system.get("cpu_pct_max")
    if isinstance(cpu, int | float) and cpu > ALERT_CPU_PCT:
        found.append(f"🔥 CPU al {cpu:.0f} %")
    memory = system.get("memoria_mb_max")
    if (
        isinstance(memory, int | float)
        and memory_limit_mb
        and memory > ALERT_MEMORY_SHARE * memory_limit_mb
    ):
        found.append(f"📈 Memoria al {100 * memory / memory_limit_mb:.0f} % del límite")
    free = system.get("disco_libre_mb_min")
    if isinstance(free, int | float) and free < ALERT_DISK_FREE_MB:
        found.append(f"💾 Quedan {free / 1024:.1f} GB de disco")
    lag = system.get("lag_ms_max")
    if isinstance(lag, int | float) and lag > ALERT_LAG_MS:
        found.append(f"⏳ El bucle de eventos se paró {lag / 1000:.1f} s")
    starts = report.get("arranques", {}).get(day, {})
    restarts = sum(int(times) for times in starts.values()) - len(starts)
    if restarts > 0:
        found.append(f"🔁 {restarts} reinicios sin despliegue (caída, memoria o a mano)")
    return found


def alerts_text(report: Mapping[str, Any], *, memory_limit_mb: float | None = None) -> str:
    """El mensaje que acompaña al informe diario: versión, novedades y avisos del día."""
    try:
        day = (date.fromisoformat(str(report.get("hasta"))) - timedelta(days=1)).isoformat()
    except ValueError:
        day = "?"
    lines = [f"📊 Informe hasta el {report.get('hasta')} · bot {report.get('version_bot')}"]
    for version in sorted(report.get("arranques", {}).get(day, {})):
        lines.append(f"🚀 Arrancó la versión {version}")
    found = alerts(report, memory_limit_mb=memory_limit_mb)
    if found:
        lines.append(f"⚠️ Ayer ({day}):")
        lines += [f"- {line}" for line in found]
    else:
        lines.append(f"✅ Ayer ({day}) no hubo nada que vigilar.")
    text = "\n".join(lines)
    return text if len(text) <= MESSAGE_LIMIT else text[: MESSAGE_LIMIT - 1] + "…"
