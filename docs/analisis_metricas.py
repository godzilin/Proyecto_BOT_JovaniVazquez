"""Resume en texto el informe JSON de uso del bot, pensado para que un agente decida mejoras.

Uso, desde la raíz del repo:

    python docs/analisis_metricas.py docs/metricas/informe-2026-10-11.json
    python docs/analisis_metricas.py informe.json --top 5 --limite-mb 2048
    python docs/analisis_metricas.py docs/metricas/

El informe lo genera el comando `informe` del bot (`build_report` en
`bot/services/telemetry.py`). Entradas: la ruta del JSON, `--top N` (filas de cada
ranking, 10 por defecto) y `--limite-mb` (memoria del contenedor, 2048 por defecto,
para marcar los días cerca del límite).

Visión global: con varios JSON o una carpeta, junta todos los informes
(`merge_reports`). Lo que va por día (`por_dia`, `sistema`, `arranques`) se une y,
si dos informes tienen el mismo día, manda el más nuevo; lo que es de la ventana
entera (uso, actividad, casino, economía) sale del más nuevo. Así «Sistema» y
«Versiones y reinicios» cubren toda la historia bajada y comparan versiones.

Salida: Markdown sencillo por pantalla, con una sección `## ...` por tema. No escribe
archivos y solo usa la biblioteca estándar: no importa nada de `bot.*`, así que
funciona con el JSON solo.

Cualquier sección cuyo dato falte o esté vacío se omite o dice «sin datos»; el
script no debe reventar con un informe casi vacío.

El signo ⚠️ marca lo que merece mirarse: latencias de 3 s o más, memoria por
encima del 85 % del límite, bucle de eventos con más de 250 ms de retraso, RTP del
casino por encima de 1, juegos sin jugadas, CPU por encima del 85 %, procesos
matados por memoria, cortes con Discord, respuestas 429, días con más de media hora
sin datos (bot caído), esperas de base de datos o candados de 1 s o más, dibujos con
plan B, interacciones perdidas (10062) y logros cuya rareza no cuadra con cuántos
miembros lo tienen.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

Report = dict[str, Any]

DEFAULT_TOP = 10
DEFAULT_LIMIT_MB = 2048.0
MEMORY_WARN = 0.85
LAG_WARN_MS = 250.0
NEW_USER_DAYS = 7
RETURN_FROM_DAY = 14
MIN_RETENTION_WINDOW = 21
MIN_RETENTION_USERS = 3
TREND_KINDS = ("message", "slash", "button", "voice")
FAIL_KINDS = ("button", "modal", "slash", "text")
WARN = "⚠️"
CPU_WARN = 85.0
# Minutos de un día y los que pueden faltar sin aviso (la muestra es cada minuto,
# y el bucle se retrasa algún segundo).
DAY_MINUTES = 1440
DOWNTIME_WARN_MIN = 30
SLOW_MS = 1000
# Rareza que no cuadra: un logro legendario o mítico que tiene mucha gente, o uno
# común que casi nadie consigue (proporción de miembros con algún logro).
RARITY_TOO_COMMON = {"Legendario": 0.10, "Mítico": 0.03}
RARITY_TOO_RARE = {"Común": 0.02}
RARITY_ORDER = ("Común", "Raro", "Épico", "Legendario", "Mítico")
# Lo que va por día en el informe: al juntar varios, se une día a día (y dentro de
# cada día, campo a campo: la masa monetaria solo viene en el informe de ese día).
DAILY_KEYS = ("por_dia", "sistema", "arranques", "economia_dia")
# Presupuesto por fotograma del Jovaniverso (Biblia, «Jovaniverso: rendimiento imperativo»).
UNIVERSO_CALLS = 120
UNIVERSO_TRIANGLES = 250_000
UNIVERSO_FPS_WARN = 30
UNIVERSO_ENTRY = (
    ("login", "Login"),
    ("api", "Perfil (/api/entrar)"),
    ("mundo", "Mundo montado"),
    ("frame", "Primer fotograma"),
)


# --------------------------------------------------------------------------- utilidades


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _num(value: Any, default: float = 0) -> float:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _entries(report: Report, *kinds: str) -> list[dict[str, Any]]:
    """Entradas de `uso` de los tipos dados (todas si no se indica ninguno)."""
    return [
        e
        for e in _list(report.get("uso"))
        if isinstance(e, dict) and (not kinds or e.get("tipo") in kinds)
    ]


def _pct(part: float, whole: float) -> str:
    return f"{100 * part / whole:.1f} %" if whole else "-"


def _table(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return lines


def _window(report: Report) -> tuple[date, date] | None:
    try:
        return date.fromisoformat(report["desde"]), date.fromisoformat(report["hasta"])
    except (KeyError, TypeError, ValueError):
        return None


def _window_days(report: Report) -> int:
    """Días de la ventana: por las fechas si son válidas, si no por `dias`."""
    window = _window(report)
    if window:
        return (window[1] - window[0]).days + 1
    return int(_num(report.get("dias")))


def _feature(label: str) -> str | None:
    """Funcionalidad de una etiqueta «tipo:nombre»: el comando (slash+text) o la vista del botón."""
    kind, _, name = label.partition(":")
    if kind in ("slash", "text"):
        return f"comando {name}"
    if kind == "button":
        return f"vista {name.split('.', 1)[0]}"
    return None


def _feature_users(report: Report) -> dict[str, set[str]]:
    """Usuarios distintos por funcionalidad, a partir de `actividad`."""
    found: dict[str, set[str]] = {}
    for who, labels in _dict(report.get("actividad")).items():
        for label in _dict(labels):
            feature = _feature(label)
            if feature:
                found.setdefault(feature, set()).add(who)
    return found


def _section(title: str, body: list[str]) -> list[str]:
    return [f"## {title}", "", *(body or ["Sin datos."]), ""]


# --------------------------------------------------------------------------- secciones


def section_summary(report: Report, top: int) -> list[str]:
    """Ventana del informe, eventos por tipo y usuarios distintos totales."""
    lines = [
        f"- Ventana: {report.get('desde', '?')} a {report.get('hasta', '?')} "
        f"({report.get('dias', '?')} días)",
    ]
    if report.get("generado"):
        lines.append(f"- Generado: {report['generado']}")
    per_kind: dict[str, int] = {}
    for entry in _entries(report):
        kind = str(entry.get("tipo"))
        per_kind[kind] = per_kind.get(kind, 0) + int(_num(entry.get("eventos")))
    if per_kind:
        lines.append(
            "- Eventos por tipo: " + ", ".join(f"{k} {v}" for k, v in sorted(per_kind.items()))
        )
    else:
        lines.append("- Eventos por tipo: sin datos")
    lines.append(f"- Usuarios distintos con actividad: {len(_dict(report.get('actividad')))}")
    return _section("Resumen", lines)


def section_slow(report: Report, top: int) -> list[str]:
    """Entradas con latencia medida, de más a menos lentas por proporción de ≥3 s y luego ≥1 s."""
    rows = []
    for entry in _entries(report):
        if entry.get("ms_medio") is None:
            continue
        ms = _dict(entry.get("ms"))
        slow = int(_num(ms.get("<3000")))
        very_slow = int(_num(ms.get(">=3000")))
        measured = sum(int(_num(ms.get(k))) for k in ("<500", "<1000", "<3000", ">=3000"))
        if not measured:
            continue
        rows.append((very_slow / measured, slow / measured, measured, very_slow, slow, entry))
    rows.sort(key=lambda r: (-r[0], -r[1], -r[2], str(r[5].get("nombre"))))
    body: list[str] = []
    if rows:
        table = [
            (
                WARN if very_slow else "",
                f"{e.get('tipo')}:{e.get('nombre')}",
                measured,
                _pct(slow + very_slow, measured),
                _pct(very_slow, measured),
                f"{_num(e.get('ms_medio')):.0f}",
                f"{_num(e.get('ms_max')):.0f}",
            )
            for _a, _b, measured, very_slow, slow, e in rows[:top]
        ]
        body = ["Latencia hasta la primera respuesta o duración medida.", ""]
        body += _table(["", "Nombre", "Medidos", "≥1 s", "≥3 s", "ms medio", "ms máx"], table)
        if len(rows) > top:
            body += ["", f"(+{len(rows) - top} más con medición; usa --top para ver más)"]
    return _section("Lentos", body)


def section_failures(report: Report, top: int) -> list[str]:
    """Botones, formularios y comandos con errores: una interacción sin respuesta o fallida."""
    rows = sorted(
        (e for e in _entries(report, *FAIL_KINDS) if _num(e.get("errores")) > 0),
        key=lambda e: (-_num(e.get("errores")), str(e.get("nombre"))),
    )
    if not rows:
        return _section("Fallos", ["Sin errores registrados."])
    body = [
        "En `button`, `modal` y `slash` un error es una interacción sin responder a Discord"
        " o fallida.",
        "",
    ]
    body += _table(
        ["Nombre", "Errores", "Eventos", "Tasa"],
        [
            (
                f"{e.get('tipo')}:{e.get('nombre')}",
                int(_num(e.get("errores"))),
                int(_num(e.get("eventos"))),
                _pct(_num(e.get("errores")), _num(e.get("eventos"))),
            )
            for e in rows[:top]
        ],
    )
    return _section("Fallos", body)


def _usage_groups(report: Report) -> dict[str, tuple[int, int]]:
    """Eventos y usuarios por funcionalidad (comando o vista de botones)."""
    distinct = _feature_users(report)
    groups: dict[str, list[int]] = {}
    for entry in _entries(report, "slash", "text", "button"):
        feature = _feature(f"{entry.get('tipo')}:{entry.get('nombre')}")
        if feature is None:
            continue
        slot = groups.setdefault(feature, [0, 0])
        slot[0] += int(_num(entry.get("eventos")))
        slot[1] += int(_num(entry.get("usuarios")))  # solo se usa si no hay `actividad`
    return {
        f: (events, len(distinct[f]) if f in distinct else users)
        for f, (events, users) in groups.items()
    }


def section_usage(report: Report, top: int) -> list[str]:
    """Comandos (slash y text sumados) y vistas de botones más y menos usados."""
    groups = _usage_groups(report)
    body: list[str] = []
    for prefix, title in (("comando ", "Comandos"), ("vista ", "Botones por vista")):
        items = sorted(
            ((f[len(prefix) :], ev, us) for f, (ev, us) in groups.items() if f.startswith(prefix)),
            key=lambda i: (-i[1], i[0]),
        )
        if not items:
            continue
        head = items[:top]
        tail = [i for i in sorted(items, key=lambda i: (i[1], i[0]))[:top] if i not in head]
        if body:
            body.append("")
        body += [f"### {title}: más usados", ""]
        body += _table(["Nombre", "Eventos", "Usuarios"], head)
        if tail:
            body += ["", f"### {title}: menos usados", ""]
            body += _table(["Nombre", "Eventos", "Usuarios"], tail)
    return _section("Lo más y lo menos usado", body)


def section_unused(report: Report, top: int) -> list[str]:
    """Comandos del bot que nadie usó en la ventana."""
    names = [str(n) for n in _list(report.get("comandos_sin_uso"))]
    if not names:
        return _section("Comandos sin uso", ["Ninguno: todos se han usado (o no hay datos)."])
    return _section("Comandos sin uso", [f"{len(names)}: " + ", ".join(names)])


def _split_halves(report: Report) -> tuple[list[str], list[str]] | None:
    window = _window(report)
    if window is None:
        return None
    start, end = window
    total = (end - start).days + 1
    if total < 2:
        return None
    days = [(start + timedelta(days=i)).isoformat() for i in range(total)]
    half = total // 2
    return days[:half], days[total - half :]


def _change(before: float, after: float) -> str:
    if before == 0:
        return "nuevo" if after else "-"
    return f"{100 * (after - before) / before:+.0f} %"


def section_trend(report: Report, top: int) -> list[str]:
    """Eventos y usuarios medios por día: primera mitad de la ventana frente a la segunda."""
    halves = _split_halves(report)
    per_day = _dict(report.get("por_dia"))
    if halves is None or not per_day:
        return _section("Tendencia", [])
    first, second = halves
    rows = []
    for kind in TREND_KINDS:
        if not any(kind in _dict(per_day.get(d)) for d in per_day):
            continue
        cells: list[float] = []
        for days in (first, second):
            for field in ("eventos", "usuarios"):
                total = sum(_num(_dict(_dict(per_day.get(d)).get(kind)).get(field)) for d in days)
                cells.append(total / len(days))
        rows.append(
            (
                kind,
                f"{cells[0]:.1f}",
                f"{cells[2]:.1f}",
                _change(cells[0], cells[2]),
                f"{cells[1]:.1f}",
                f"{cells[3]:.1f}",
                _change(cells[1], cells[3]),
            )
        )
    if not rows:
        return _section("Tendencia", [])
    body = [
        f"Primera mitad: {first[0]} a {first[-1]}; segunda: {second[0]} a {second[-1]}."
        " Medias por día.",
        "",
    ]
    body += _table(
        ["Tipo", "Eventos 1ª", "Eventos 2ª", "Cambio", "Usuarios 1ª", "Usuarios 2ª", "Cambio"],
        rows,
    )
    return _section("Tendencia", body)


def section_peak_hours(report: Report, top: int) -> list[str]:
    """Las 3 horas con más mensajes y las 3 con más comandos y clics."""
    hourly = _dict(report.get("por_hora"))
    if not hourly:
        return _section("Horas punta", [])

    def series(kind: str) -> list[float]:
        raw = _list(hourly.get(kind))
        return [_num(raw[h]) if h < len(raw) else 0 for h in range(24)]

    def best(values: list[float]) -> str:
        ranked = [
            (h, v) for h, v in sorted(enumerate(values), key=lambda i: (-i[1], i[0])) if v > 0
        ]
        return ", ".join(f"{h:02d}h ({int(v)})" for h, v in ranked[:3]) or "sin datos"

    interactions = [
        a + b + c for a, b, c in zip(series("slash"), series("button"), series("text"), strict=True)
    ]
    return _section(
        "Horas punta",
        [
            f"- Mensajes: {best(series('message'))}",
            f"- Comandos y botones: {best(interactions)}",
            "- Horas locales de Canarias.",
        ],
    )


def section_retention(report: Report, top: int) -> list[str]:
    """Retención por funcionalidad y usuarios activos por semana."""
    title = "Retención por funcionalidad"
    activity = _dict(report.get("actividad"))
    days = _window_days(report)
    if not activity:
        return _section(title, [])
    if days < MIN_RETENTION_WINDOW:
        return _section(
            title,
            [
                f"Ventana de {days} días: hacen falta al menos {MIN_RETENTION_WINDOW}"
                " para medir la retención."
            ],
        )
    offsets: dict[str, dict[str, set[int]]] = {}
    for who, labels in activity.items():
        for label, shifts in _dict(labels).items():
            feature = _feature(label)
            if feature:
                bucket = offsets.setdefault(feature, {}).setdefault(who, set())
                bucket.update(int(s) for s in _list(shifts) if isinstance(s, int | float))
    rows = []
    for feature, users in offsets.items():
        if len(users) < MIN_RETENTION_USERS:
            continue
        new = [u for u, s in users.items() if s and min(s) < NEW_USER_DAYS]
        back = [u for u in new if any(d >= RETURN_FROM_DAY for d in users[u])]
        rows.append((feature, len(users), len(new), len(back), _pct(len(back), len(new))))
    rows.sort(key=lambda r: (-r[1], r[0]))
    body = [
        f"Nuevo: primer uso en los primeros {NEW_USER_DAYS} días. Vuelve: lo usa de nuevo a"
        f" partir del día {RETURN_FROM_DAY}.",
        "",
    ]
    if rows:
        body += _table(["Funcionalidad", "Usuarios", "Nuevos", "Vuelven", "Retención"], rows[:top])
    else:
        body.append(f"Ninguna funcionalidad llega a {MIN_RETENTION_USERS} usuarios.")
    weeks = math.ceil(days / 7)
    active = [0] * weeks
    for labels in activity.values():
        seen = {
            int(s) // 7
            for shifts in _dict(labels).values()
            for s in _list(shifts)
            if isinstance(s, int | float) and 0 <= s < weeks * 7
        }
        for week in seen:
            active[week] += 1
    body += ["", "Usuarios activos (cualquier cosa) por semana:", ""]
    body += _table(["Semana", "Usuarios"], [(f"semana {i + 1}", n) for i, n in enumerate(active)])
    return _section(title, body)


def section_channels(report: Report, top: int) -> list[str]:
    """Canales con más mensajes y con más minutos de voz."""
    body: list[str] = []
    for kind, title, unit in (("message", "Mensajes", "Mensajes"), ("voice", "Voz", "Minutos")):
        rows = sorted(
            _entries(report, kind), key=lambda e: (-_num(e.get("eventos")), str(e.get("nombre")))
        )
        if not rows:
            continue
        if body:
            body.append("")
        body += [f"### {title}", ""]
        body += _table(
            ["Canal", unit, "Usuarios"],
            [
                (e.get("nombre"), int(_num(e.get("eventos"))), int(_num(e.get("usuarios"))))
                for e in rows[:top]
            ],
        )
    return _section("Canales", body)


def section_system(report: Report, top: int, limit_mb: float = DEFAULT_LIMIT_MB) -> list[str]:
    """Memoria, retraso del bucle, procesos de dibujo y latencia con Discord por día."""
    rows = []
    today = str(report.get("hasta"))
    for day, raw in sorted(_dict(report.get("sistema")).items()):
        row = _dict(raw)
        samples = row.get("muestras")
        # El día del informe aún no ha acabado: sus minutos sin datos no dicen nada.
        missing = (
            max(0, DAY_MINUTES - int(samples))
            if isinstance(samples, int | float) and day != today
            else None
        )
        memory = row.get("memoria_mb_max")
        lag = row.get("lag_ms_max")
        gateway = row.get("gateway_ms_media")
        cpu_max, cpu_avg = row.get("cpu_pct_max"), row.get("cpu_pct_media")
        database, free = row.get("db_mb_max"), row.get("disco_libre_mb_min")
        flags = []
        if isinstance(memory, int | float) and limit_mb > 0 and memory > MEMORY_WARN * limit_mb:
            flags.append(f"{WARN} memoria")
        if isinstance(lag, int | float) and lag > LAG_WARN_MS:
            flags.append(f"{WARN} bucle")
        if isinstance(cpu_max, int | float) and cpu_max > CPU_WARN:
            flags.append(f"{WARN} CPU")
        if missing is not None and missing > DOWNTIME_WARN_MIN:
            flags.append(f"{WARN} caído")
        rows.append(
            (
                day,
                "-" if memory is None else f"{memory:.0f} ({_pct(memory, limit_mb)})",
                "-" if cpu_avg is None else f"{cpu_avg:.0f} / {_num(cpu_max):.0f}",
                "-" if lag is None else f"{lag:.0f}",
                row.get("chromium_max", "-"),
                row.get("node_max", "-"),
                "-" if gateway is None else f"{gateway:.0f}",
                "-" if database is None else f"{database:.0f}",
                "-" if free is None else f"{free / 1024:.1f}",
                "-" if missing is None else missing,
                " ".join(flags),
            )
        )
    body: list[str] = []
    if rows:
        body = [
            f"Límite de memoria: {limit_mb:.0f} MB; aviso por encima del {MEMORY_WARN:.0%}.",
            "",
        ]
        extra = _system_extra(report)
        body += _table(
            [
                "Día",
                "Memoria máx MB",
                "CPU % media / máx",
                "Retraso bucle ms",
                "Chromium",
                "Node",
                "Discord ms",
                "Base MB",
                "Disco libre GB",
                "Min sin datos",
                "Avisos",
            ],
            rows,
        )
        if extra:
            body += ["", "Procesos de dibujo, disco y tareas del bucle:", ""]
            body += _table(
                [
                    "Día",
                    "Chromium MB",
                    "Node MB",
                    "Disco leído MB",
                    "Disco escrito MB",
                    "Tareas máx",
                ],
                extra,
            )
    return _section("Sistema", body)


def _system_extra(report: Report) -> list[tuple]:
    """Filas de memoria de Chromium y Node, disco leído y escrito y tareas vivas por día."""
    keys = ("chromium_mb_max", "node_mb_max", "disco_leido_mb", "disco_escrito_mb", "tareas_max")
    rows = []
    for day, raw in sorted(_dict(report.get("sistema")).items()):
        row = _dict(raw)
        if all(row.get(key) is None for key in keys):
            continue
        rows.append(
            (day, *("-" if row.get(key) is None else f"{_num(row.get(key)):.0f}" for key in keys))
        )
    return rows


def section_render(report: Report, top: int) -> list[str]:
    """Tiempo medio y máximo de cada dibujo (escena)."""
    rows = sorted(
        _entries(report, "render"),
        key=lambda e: (-_num(e.get("ms_medio")), str(e.get("nombre"))),
    )
    body: list[str] = []
    if rows:
        body = _table(
            ["Escena", "Dibujos", "ms medio", "ms máx"],
            [
                (
                    e.get("nombre"),
                    int(_num(e.get("eventos"))),
                    "-" if e.get("ms_medio") is None else f"{e['ms_medio']:.0f}",
                    f"{_num(e.get('ms_max')):.0f}",
                )
                for e in rows[:top]
            ],
        )
    return _section("Dibujos", body)


def section_upload(report: Report, top: int) -> list[str]:
    """Tiempo medio y máximo de subir cada archivo a Discord, por nombre y tamaño."""
    rows = sorted(
        _entries(report, "upload"),
        key=lambda e: (-_num(e.get("ms_medio")), str(e.get("nombre"))),
    )
    body: list[str] = []
    if rows:
        body = _table(
            ["Archivo", "Subidas", "Fallos", "ms medio", "ms máx"],
            [
                (
                    e.get("nombre"),
                    int(_num(e.get("eventos"))),
                    int(_num(e.get("errores"))),
                    "-" if e.get("ms_medio") is None else f"{e['ms_medio']:.0f}",
                    f"{_num(e.get('ms_max')):.0f}",
                )
                for e in rows[:top]
            ],
        )
    return _section("Subidas a Discord", body)


def section_lag(report: Report, top: int) -> list[str]:
    """Dónde se para el bucle de eventos más de 250 ms: lo que retrasa todos los botones."""
    rows = sorted(
        _entries(report, "lag"),
        key=lambda e: (-_num(e.get("eventos")) * _num(e.get("ms_medio")), str(e.get("nombre"))),
    )
    body: list[str] = []
    if rows:
        body = _table(
            ["Dónde", "Veces", "ms medio", "ms máx"],
            [
                (
                    e.get("nombre"),
                    int(_num(e.get("eventos"))),
                    "-" if e.get("ms_medio") is None else f"{e['ms_medio']:.0f}",
                    f"{_num(e.get('ms_max')):.0f}",
                )
                for e in rows[:top]
            ],
        )
    return _section("Bloqueos del bucle", body)


def _upload_base(name: Any) -> str:
    """Archivo de una subida sin su tramo de tamaño (`moneda.gif · <250 KB` → `moneda.gif`)."""
    return str(name).split(" · ", 1)[0]


def section_image(report: Report, top: int) -> list[str]:
    """Del clic a que la imagen está en Discord, frente a lo que tarda solo la subida.

    Lo que sobra sobre la subida es lo que el miembro espera al dibujo: cerca de 0
    si se pintó antes del clic.
    """
    uploads: dict[str, tuple[float, float]] = {}
    for e in _entries(report, "upload"):
        if e.get("ms_medio") is None:
            continue
        base = _upload_base(e.get("nombre"))
        count, total = uploads.get(base, (0.0, 0.0))
        weight = _num(e.get("eventos"))
        uploads[base] = (count + weight, total + weight * _num(e.get("ms_medio")))
    rows = sorted(
        (e for e in _entries(report, "imagen") if e.get("ms_medio") is not None),
        key=lambda e: (-_num(e.get("ms_medio")), str(e.get("nombre"))),
    )
    body: list[str] = []
    if rows:
        table = []
        for e in rows[:top]:
            count, total = uploads.get(str(e.get("nombre")), (0.0, 0.0))
            upload = total / count if count else None
            wait = _num(e.get("ms_medio"))
            very_slow = int(_num(_dict(e.get("ms")).get(">=3000")))
            table.append(
                (
                    WARN if very_slow else "",
                    e.get("nombre"),
                    int(_num(e.get("eventos"))),
                    f"{wait:.0f}",
                    "-" if upload is None else f"{upload:.0f}",
                    "-" if upload is None else f"{max(0.0, wait - upload):.0f}",
                    _pct(very_slow, _num(e.get("eventos"))),
                    f"{_num(e.get('ms_max')):.0f}",
                )
            )
        body = [
            "Espera del miembro desde el clic hasta la primera imagen. «Sin la subida» es"
            " lo que esperó al dibujo: alto quiere decir que no estaba pintado antes.",
            "",
        ]
        body += _table(
            ["", "Imagen", "Veces", "ms medio", "Subida ms", "Sin la subida", "≥3 s", "ms máx"],
            table,
        )
    return _section("Del clic a la imagen", body)


def section_discord(report: Report, top: int) -> list[str]:
    """Cortes con Discord, respuestas 429, procesos matados por memoria y volcados a disco."""

    def events(kind: str) -> list[dict[str, Any]]:
        return sorted(
            _entries(report, kind), key=lambda e: (-_num(e.get("eventos")), str(e.get("nombre")))
        )

    body: list[str] = []
    gateway = {str(e.get("nombre")): int(_num(e.get("eventos"))) for e in events("gateway")}
    if gateway:
        cuts = gateway.get("desconexion", 0)
        body.append(
            f"- Conexión: {gateway.get('conectada', 0)} conectada, {cuts} cortes, "
            f"{gateway.get('reanudada', 0)} reanudadas" + (f" {WARN}" if cuts else "")
        )
    for e in events("conexion_inicial"):
        if e.get("ms_medio") is not None:
            body.append(
                f"- Del arranque a conectar: {_num(e.get('ms_medio')) / 1000:.1f} s de media,"
                f" {_num(e.get('ms_max')) / 1000:.1f} de máximo, en {int(_num(e.get('eventos')))}"
                " arranques"
            )
    kills = sum(int(_num(e.get("eventos"))) for e in events("oom"))
    if kills:
        body.append(f"- {WARN} Procesos matados por falta de memoria: {kills}")
    for e in events("db"):
        if e.get("ms_medio") is not None:
            body.append(
                f"- Volcados a la base de datos: {int(_num(e.get('eventos')))}, "
                f"{_num(e.get('ms_medio')):.0f} ms de media, {_num(e.get('ms_max')):.0f} de máximo"
            )
    limits = events("ratelimit")
    if limits:
        body += ["", f"{WARN} Respuestas 429 (Discord frena al bot) por ruta:", ""]
        body += _table(
            ["Ruta", "Veces"],
            [(e.get("nombre"), int(_num(e.get("eventos")))) for e in limits[:top]],
        )
    return _section("Conexión con Discord", body)


def _timed_rows(report: Report, kind: str, top: int, *, by_total: bool = True) -> list[tuple]:
    """Filas `(aviso, nombre, veces, ms medio, ms máx, ≥1 s, errores)` de un tipo medido."""
    entries = [e for e in _entries(report, kind) if e.get("ms_medio") is not None]
    entries.sort(
        key=lambda e: (
            -(
                _num(e.get("eventos")) * _num(e.get("ms_medio"))
                if by_total
                else _num(e.get("ms_medio"))
            ),
            str(e.get("nombre")),
        )
    )
    rows = []
    for e in entries[:top]:
        ms = _dict(e.get("ms"))
        measured = sum(int(_num(ms.get(k))) for k in ("<500", "<1000", "<3000", ">=3000"))
        slow = int(_num(ms.get("<3000"))) + int(_num(ms.get(">=3000")))
        rows.append(
            (
                WARN if slow or _num(e.get("errores")) else "",
                e.get("nombre"),
                int(_num(e.get("eventos"))),
                f"{_num(e.get('ms_medio')):.0f}",
                f"{_num(e.get('ms_max')):.0f}",
                _pct(slow, measured),
                int(_num(e.get("errores"))),
            )
        )
    return rows


def section_database(report: Report, top: int) -> list[str]:
    """Lo que tarda cada repositorio en la base de datos, de más a menos tiempo total."""
    rows = _timed_rows(report, "sql", top)
    body: list[str] = []
    if rows:
        body = [
            "Por repositorio y operación, ordenado por tiempo total. Un error es un «database"
            " is locked» o parecido. En las lecturas solo se mide hasta la primera fila.",
            "",
        ]
        body += _table(
            ["", "Repositorio · operación", "Veces", "ms medio", "ms máx", "≥1 s", "Errores"], rows
        )
    return _section("Base de datos", body)


def section_api(report: Report, top: int) -> list[str]:
    """Lo que tarda cada llamada a Discord por ruta, de más a menos tiempo total."""
    rows = _timed_rows(report, "api", top)
    body: list[str] = []
    if rows:
        body = [
            "Por método y plantilla de la ruta (sin IDs). Incluye lo que discord.py espera"
            " cuando Discord le frena (429). Un error es una llamada que falló.",
            "",
        ]
        body += _table(["", "Llamada", "Veces", "ms medio", "ms máx", "≥1 s", "Errores"], rows)
    return _section("Llamadas a Discord", body)


def section_tasks(report: Report, top: int) -> list[str]:
    """Cada tarea programada: vueltas, lo que tarda y si falla."""
    rows = _timed_rows(report, "tarea", max(top, 50))
    body: list[str] = []
    if rows:
        body = [
            "El nombre lleva cuántas vueltas le tocan al día; cada arranque del bot suma una.",
            "",
        ]
        body += _table(["", "Tarea", "Vueltas", "ms medio", "ms máx", "≥1 s", "Fallos"], rows)
    return _section("Tareas programadas", body)


def section_newcomers(report: Report, top: int) -> list[str]:
    """Primeros pasos de los miembros que entraron en la ventana."""
    data = _dict(report.get("nuevos"))
    joined = int(_num(data.get("altas")))
    if not joined:
        return _section("Nuevos miembros", [])

    def days(key: str) -> str:
        value = data.get(key)
        return "-" if value is None else f"{_num(value):.1f}"

    def step(label: str, count_key: str, days_key: str) -> tuple:
        count = _num(data.get(count_key))
        return (label, int(count), _pct(count, joined), days(days_key))

    body = [f"- Altas en la ventana: {joined}", ""]
    body += _table(
        ["Paso", "Miembros", "De las altas", "Días hasta (mediana)"],
        [
            step("Escribió un mensaje", "escribieron", "dias_hasta_escribir_mediana"),
            step("Usó un comando", "usaron_comandos", "dias_hasta_comando_mediana"),
            step("Pulsó un botón (jugó)", "usaron_botones", "dias_hasta_boton_mediana"),
            (
                "Volvió tras 7 días",
                int(_num(data.get("volvieron_tras_7_dias"))),
                _pct(_num(data.get("volvieron_tras_7_dias")), joined),
                "-",
            ),
        ],
    )
    return _section("Nuevos miembros", body)


def section_bot_messages(report: Report, top: int) -> list[str]:
    """Mensajes del bot por canal y reacciones que reciben: qué avisos se leen."""
    sent = {str(e.get("nombre")): _num(e.get("eventos")) for e in _entries(report, "bot_mensaje")}
    reacted = {
        str(e.get("nombre")): (_num(e.get("eventos")), int(_num(e.get("usuarios"))))
        for e in _entries(report, "reaccion_bot")
    }
    names = sorted(set(sent) | set(reacted), key=lambda n: (-sent.get(n, 0), n))
    body: list[str] = []
    if names:
        body = _table(
            ["Canal", "Mensajes del bot", "Reacciones", "Personas", "Reacciones por mensaje"],
            [
                (
                    name,
                    int(sent.get(name, 0)),
                    int(reacted.get(name, (0, 0))[0]),
                    reacted.get(name, (0, 0))[1],
                    f"{reacted.get(name, (0, 0))[0] / sent[name]:.2f}" if sent.get(name) else "-",
                )
                for name in names[:top]
            ],
        )
    return _section("Avisos del bot", body)


def section_locks(report: Report, top: int) -> list[str]:
    """Candados que hacen esperar a un clic porque otro ya los tenía."""
    rows = _timed_rows(report, "candado", top)
    body: list[str] = []
    if rows:
        body = ["Solo cuenta las veces que el candado ya estaba cogido.", ""]
        body += _table(["", "Candado", "Esperas", "ms medio", "ms máx", "≥1 s", "-"], rows)
    return _section("Esperas en candados", body)


def section_painters(report: Report, top: int) -> list[str]:
    """Procesos de Node (abiertos, cerrados, matados, espera) y dibujos con plan B."""
    node = {str(e.get("nombre")): e for e in _entries(report, "node")}
    body: list[str] = []
    if node:
        waiting = _dict(node.get("espera de proceso"))
        opening = _dict(node.get("proceso abierto"))
        closed = int(_num(_dict(node.get("proceso cerrado")).get("eventos")))
        killed = int(_num(_dict(node.get("proceso matado")).get("eventos")))
        body.append(
            f"- Node: {int(_num(opening.get('eventos')))} procesos abiertos"
            f" ({_num(opening.get('ms_medio')):.0f} ms de media en arrancar),"
            f" {closed} cerrados por no usarse, {killed} matados a medias"
        )
        if waiting:
            wait_mean, wait_max = _num(waiting.get("ms_medio")), _num(waiting.get("ms_max"))
            body.append(
                f"- Espera hasta tener un proceso: {wait_mean:.0f} ms de media,"
                f" {wait_max:.0f} de máximo, en {int(_num(waiting.get('eventos')))} dibujos"
            )
    fallbacks = sorted(
        _entries(report, "plan_b"), key=lambda e: (-_num(e.get("eventos")), str(e.get("nombre")))
    )
    if fallbacks:
        body += ["", f"{WARN} Dibujos con plan B (el motor no estaba disponible):", ""]
        body += _table(
            ["Pintor · motor", "Dibujos", "Veces que se apagó"],
            [
                (e.get("nombre"), int(_num(e.get("eventos"))), int(_num(e.get("errores"))))
                for e in fallbacks[:top]
            ],
        )
    return _section("Procesos de dibujo", body)


def section_weight(report: Report, top: int) -> list[str]:
    """Lo que pesa cada imagen subida a Discord (KB medio y máximo)."""
    rows = sorted(
        (e for e in _entries(report, "peso") if e.get("ms_medio") is not None),
        key=lambda e: (-_num(e.get("ms_medio")), str(e.get("nombre"))),
    )
    body: list[str] = []
    if rows:
        body = _table(
            ["Imagen", "Subidas", "KB medio", "KB máx"],
            [
                (
                    e.get("nombre"),
                    int(_num(e.get("eventos"))),
                    f"{_num(e.get('ms_medio')):.0f}",
                    f"{_num(e.get('ms_max')):.0f}",
                )
                for e in rows[:top]
            ],
        )
    return _section("Peso de las imágenes", body)


def section_lost(report: Report, top: int) -> list[str]:
    """Interacciones que Discord ya daba por perdidas al contestar (error 10062)."""
    rows = sorted(
        _entries(report, "10062"), key=lambda e: (-_num(e.get("eventos")), str(e.get("nombre")))
    )
    body: list[str] = []
    if rows:
        body = [
            f"{WARN} El miembro vio «Esta interacción ha fallado». Retraso desde el clic.",
            "",
        ]
        body += _table(
            ["Botón o comando", "Veces", "ms medio", "ms máx"],
            [
                (
                    e.get("nombre"),
                    int(_num(e.get("eventos"))),
                    f"{_num(e.get('ms_medio')):.0f}",
                    f"{_num(e.get('ms_max')):.0f}",
                )
                for e in rows[:top]
            ],
        )
    return _section("Interacciones perdidas (10062)", body)


def section_views(report: Report, top: int) -> list[str]:
    """Partidas, paneles y menús: abiertos, cerrados, abandonados y lo que duran."""

    def by_name(kind: str) -> dict[str, dict[str, Any]]:
        return {str(e.get("nombre")): e for e in _entries(report, kind)}

    opened, closed, expired = by_name("vista"), by_name("vista_cerrada"), by_name("caducada")
    names = sorted(
        set(opened) | set(closed) | set(expired),
        key=lambda n: (-_num(_dict(opened.get(n)).get("eventos")), n),
    )
    body: list[str] = []
    if names:
        rows = []
        for name in names[:top]:
            started = _num(_dict(opened.get(name)).get("eventos"))
            ended = _dict(closed.get(name))
            lost = _dict(expired.get(name))
            lost_count = _num(lost.get("eventos"))
            rows.append(
                (
                    name,
                    int(started),
                    int(_num(ended.get("eventos"))),
                    int(lost_count),
                    _pct(lost_count, started),
                    "-"
                    if ended.get("ms_medio") is None
                    else f"{_num(ended.get('ms_medio')) / 60000:.1f}",
                    "-"
                    if lost.get("ms_medio") is None
                    else f"{_num(lost.get('ms_medio')) / 60000:.1f}",
                )
            )
        body = [
            "Abiertas cuenta cada vista creada (una partida que rehace su vista cuenta varias)."
            " Caducada: nadie la cerró antes de su tiempo, es decir, abandonada o sin cierre.",
            "",
        ]
        body += _table(
            [
                "Vista",
                "Abiertas",
                "Cerradas",
                "Caducadas",
                "Abandono",
                "Min hasta cerrar",
                "Min hasta caducar",
            ],
            rows,
        )
    return _section("Partidas y paneles", body)


def section_menus(report: Report, top: int) -> list[str]:
    """Opciones más elegidas en los desplegables; la ayuda aparte, entera."""
    entries = sorted(
        _entries(report, "menu"), key=lambda e: (-_num(e.get("eventos")), str(e.get("nombre")))
    )
    help_pages = [e for e in entries if str(e.get("nombre")).startswith("HelpView.")]
    others = [e for e in entries if e not in help_pages]
    body: list[str] = []
    if help_pages:
        body += ["Páginas de `ayuda` que se abren:", ""]
        body += _table(
            ["Página", "Veces"],
            [
                (str(e.get("nombre")).split(" → ", 1)[-1], int(_num(e.get("eventos"))))
                for e in help_pages
            ],
        )
    if others:
        body += ["", f"Otros desplegables (top {top}):", ""] if body else []
        body += _table(
            ["Desplegable → opción", "Veces"],
            [(e.get("nombre"), int(_num(e.get("eventos")))) for e in others[:top]],
        )
    return _section("Menús y ayuda", body)


def section_misuse(report: Report, top: int) -> list[str]:
    """Comandos mal escritos y comandos que contestan con error, frente a sus usos."""
    uses: dict[str, float] = {}
    for e in _entries(report, "slash", "text"):
        uses[str(e.get("nombre"))] = uses.get(str(e.get("nombre")), 0) + _num(e.get("eventos"))
    wrong = {str(e.get("nombre")): _num(e.get("eventos")) for e in _entries(report, "uso_mal")}
    rejected = {str(e.get("nombre")): _num(e.get("eventos")) for e in _entries(report, "rechazo")}
    names = sorted(
        set(wrong) | set(rejected), key=lambda n: (-(wrong.get(n, 0) + rejected.get(n, 0)), n)
    )
    body: list[str] = []
    if names:
        body = [
            "Mal escritos: falta o sobra algo, o el bot contestó «Uso: …». Rechazos: el"
            " comando contestó con un error (sin saldo, sin permiso, no disponible…).",
            "",
        ]
        body += _table(
            ["Comando", "Usos", "Mal escritos", "Rechazos", "Rechazo %"],
            [
                (
                    n,
                    int(uses.get(n, 0)),
                    int(wrong.get(n, 0)),
                    int(rejected.get(n, 0)),
                    _pct(rejected.get(n, 0), uses.get(n, 0)),
                )
                for n in names[:top]
            ],
        )
    return _section("Usos incorrectos y rechazos", body)


def section_exceptions(report: Report, top: int) -> list[str]:
    """Errores con traza por tipo y función del bot donde saltaron."""
    rows = sorted(
        _entries(report, "excepcion"), key=lambda e: (-_num(e.get("eventos")), str(e.get("nombre")))
    )
    body: list[str] = []
    if rows:
        body = _table(
            ["", "Error · dónde", "Veces"],
            [(WARN, e.get("nombre"), int(_num(e.get("eventos")))) for e in rows[:top]],
        )
    return _section("Excepciones", body)


def section_music(report: Report, top: int) -> list[str]:
    """Búsquedas, pistas escuchadas o saltadas, fallos y cola."""
    music = {str(e.get("nombre")): e for e in _entries(report, "musica")}
    body: list[str] = []
    if music:

        def count(name: str) -> int:
            return int(_num(_dict(music.get(name)).get("eventos")))

        def mean(name: str, scale: float = 1.0) -> str:
            value = _dict(music.get(name)).get("ms_medio")
            return "-" if value is None else f"{_num(value) / scale:.1f}"

        searches = count("búsqueda") + count("búsqueda sin resultado") + count("búsqueda sin audio")
        body = [
            f"- Búsquedas: {searches} ({mean('búsqueda', 1000)} s de media),"
            f" {count('búsqueda sin resultado')} sin resultado, {count('búsqueda sin audio')} sin"
            f" audio, {count('pista demasiado larga')} demasiado largas",
            f"- Pistas: {count('escuchada')} escuchadas ({mean('escuchada', 60000)} min de media),"
            f" {count('saltada')} saltadas ({mean('saltada', 60000)} min antes de saltar),"
            f" {count('fallo al sonar')} fallos al sonar"
            + (f" {WARN}" if count("fallo al sonar") else ""),
            f"- Cola: posición media {mean('pistas en cola')}, máxima"
            f" {_num(_dict(music.get('pistas en cola')).get('ms_max')):.0f};"
            f" {count('cola llena')} veces llena",
        ]
    return _section("Música", body)


def section_achievements(report: Report, top: int) -> list[str]:
    """Logros por rareza: cuánta gente tiene cada uno y los que no cuadran con su rareza."""
    data = _dict(report.get("logros"))
    rows = [r for r in _list(data.get("por_logro")) if isinstance(r, dict)]
    members = int(_num(data.get("miembros_con_logros")))
    missing = _list(data.get("sin_conseguir"))
    if not rows and not missing:
        return _section("Logros", [])
    body = [
        f"- Miembros con algún logro: {members}; logros que nadie tiene: {len(missing)}",
        "",
    ]
    per_rarity: dict[str, list[float]] = {}
    for r in rows:
        per_rarity.setdefault(str(r.get("rareza")), []).append(_num(r.get("pct_miembros")))
    body += _table(
        ["Rareza", "Logros conseguidos", "% medio de miembros", "% máx"],
        [
            (rarity, len(values), _pct(sum(values) / len(values), 1), _pct(max(values), 1))
            for rarity in sorted(
                per_rarity, key=lambda r: (RARITY_ORDER.index(r) if r in RARITY_ORDER else 99, r)
            )
            for values in [per_rarity[rarity]]
        ],
    )
    odd = [
        r
        for r in rows
        if _num(r.get("pct_miembros")) > RARITY_TOO_COMMON.get(str(r.get("rareza")), 2)
        or (
            str(r.get("rareza")) in RARITY_TOO_RARE
            and members >= 10
            and _num(r.get("pct_miembros")) < RARITY_TOO_RARE[str(r.get("rareza"))]
        )
    ]
    if odd:
        body += ["", f"{WARN} Rareza a revisar (demasiada o muy poca gente lo tiene):", ""]
        body += _table(
            ["Logro", "Rareza", "Miembros", "% de miembros"],
            [
                (
                    r.get("logro"),
                    r.get("rareza"),
                    int(_num(r.get("miembros"))),
                    _pct(_num(r.get("pct_miembros")), 1),
                )
                for r in odd[:top]
            ],
        )
    recent = sorted(rows, key=lambda r: (-_num(r.get("en_la_ventana")), str(r.get("logro"))))
    recent = [r for r in recent if _num(r.get("en_la_ventana"))]
    if recent:
        body += ["", f"Más conseguidos en la ventana (top {top}):", ""]
        body += _table(
            ["Logro", "Rareza", "En la ventana"],
            [
                (r.get("logro"), r.get("rareza"), int(_num(r.get("en_la_ventana"))))
                for r in recent[:top]
            ],
        )
    return _section("Logros", body)


def section_economy_days(report: Report, top: int) -> list[str]:
    """Entradas y salidas de dinero de los miembros por día y masa monetaria."""
    days = _dict(report.get("economia_dia"))
    body: list[str] = []
    if days:
        rows = []
        for day, raw in sorted(days.items()):
            row = _dict(raw)
            inflow, outflow = _num(row.get("entradas")), _num(row.get("salidas"))
            mass, inequality, state = row.get("masa"), row.get("gini"), row.get("estado")
            taxes = _dict(row.get("impuestos"))
            rows.append(
                (
                    day,
                    int(inflow),
                    int(outflow),
                    f"{int(inflow - outflow):+d}",
                    int(_num(row.get("movimientos"))),
                    ", ".join(f"{k} {int(_num(v))}" for k, v in sorted(taxes.items())) or "-",
                    int(_num(row.get("estado_gasto"))),
                    "-" if mass is None else int(_num(mass)),
                    "-" if inequality is None else f"{_num(inequality):.3f}",
                    "-" if state is None else int(_num(state)),
                )
            )
        body = [
            "Dinero que entra y sale de los monederos de los miembros, lo que recauda el"
            " Estado por impuesto y lo que gasta. La masa monetaria, el Gini y el saldo del"
            " Estado salen los días en que se sacó un informe.",
            "",
        ]
        body += _table(
            [
                "Día",
                "Entradas",
                "Salidas",
                "Neto",
                "Movimientos",
                "Impuestos",
                "Gasto Estado",
                "Masa",
                "Gini",
                "Estado",
            ],
            rows,
        )
    return _section("Economía por día", body)


def section_shop(report: Report, top: int) -> list[str]:
    """Ventas de la tienda por artículo y mascotas por especie."""
    sales = [r for r in _list(report.get("tienda")) if isinstance(r, dict)]
    pets = _dict(report.get("mascotas"))
    body: list[str] = []
    if sales:
        body += _table(
            ["Artículo", "Tipo", "Ventas", "Renovaciones", "Compradores", "Gastado"],
            [
                (
                    r.get("articulo"),
                    r.get("tipo"),
                    int(_num(r.get("ventas"))),
                    int(_num(r.get("renovaciones"))),
                    int(_num(r.get("compradores"))),
                    int(_num(r.get("gastado"))),
                )
                for r in sales[:top]
            ],
        )
    if pets:
        body += ["", "Mascotas por especie:", ""] if body else ["Mascotas por especie:", ""]
        body += _table(
            ["Especie", "Adoptadas", "Activas"],
            [
                (species, int(_num(_dict(v).get("total"))), int(_num(_dict(v).get("activas"))))
                for species, v in sorted(
                    pets.items(), key=lambda kv: -_num(_dict(kv[1]).get("total"))
                )
            ],
        )
    return _section("Tienda y mascotas", body)


def _day_versions(report: Report) -> dict[str, str]:
    """Versión del bot de cada día con datos: la última que arrancó ese día o antes."""
    starts = _dict(report.get("arranques"))
    days = sorted(
        set(_dict(report.get("por_dia"))) | set(_dict(report.get("sistema"))) | set(starts)
    )
    current: str | None = None
    found: dict[str, str] = {}
    for day in days:
        versions = _dict(starts.get(day))
        if versions:
            # shortcut: con dos versiones el mismo día, el día entero va a la mayor.
            current = max(versions, key=_version_key)
        if current is not None:
            found[day] = current
    return found


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) if part.isdigit() else 0 for part in str(version).split("."))


def section_versions(report: Report, top: int) -> list[str]:
    """Cada versión del bot por días: reinicios, clics, latencia, fallos, CPU y memoria.

    Con varios informes juntos es la comparación global de antes y después de cada
    despliegue. Un reinicio es un arranque de una versión que ya estaba corriendo:
    caída, contenedor matado por memoria o reinicio a mano.
    """
    per_version = _day_versions(report)
    if not per_version:
        return _section("Versiones y reinicios", [])
    starts = _dict(report.get("arranques"))
    per_day = _dict(report.get("por_dia"))
    system = _dict(report.get("sistema"))
    rows = []
    for version in sorted(set(per_version.values()), key=_version_key):
        days = [d for d, v in per_version.items() if v == version]
        launched = sum(int(_num(_dict(starts.get(d)).get(version))) for d in days)
        clicks = errors = measured = total_ms = slow = 0.0
        for day in days:
            button = _dict(_dict(per_day.get(day)).get("button"))
            clicks += _num(button.get("eventos"))
            errors += _num(button.get("errores"))
            count = _num(button.get("ms_n"))
            measured += count
            total_ms += count * _num(button.get("ms_medio"))
            slow += _num(button.get("lentos"))
        cpu = [
            _num(_dict(system.get(d)).get("cpu_pct_media"))
            for d in days
            if _dict(system.get(d)).get("cpu_pct_media") is not None
        ]
        memory = [
            _num(_dict(system.get(d)).get("memoria_mb_max"))
            for d in days
            if _dict(system.get(d)).get("memoria_mb_max") is not None
        ]
        restarts = max(0, launched - 1)
        rows.append(
            (
                version,
                f"{min(days)} a {max(days)}",
                len(days),
                f"{restarts}{' ' + WARN if restarts else ''}",
                f"{clicks / len(days):.0f}",
                f"{total_ms / measured:.0f}" if measured else "-",
                _pct(slow, measured),
                _pct(errors, clicks),
                f"{sum(cpu) / len(cpu):.0f}" if cpu else "-",
                f"{max(memory):.0f}" if memory else "-",
            )
        )
    body = [
        "Clics de botones por día, su latencia media y su proporción ≥3 s y sin respuesta.",
        "",
    ]
    body += _table(
        [
            "Versión",
            "Días",
            "N",
            "Reinicios",
            "Clics/día",
            "ms medio",
            "≥3 s",
            "Fallos",
            "CPU %",
            "Memoria máx MB",
        ],
        rows,
    )
    return _section("Versiones y reinicios", body)


def section_log(report: Report, top: int) -> list[str]:
    """Avisos y errores registrados por el bot, de más a menos frecuentes."""
    rows = sorted(
        _entries(report, "log"), key=lambda e: (-_num(e.get("eventos")), str(e.get("nombre")))
    )
    body: list[str] = []
    if rows:
        body = _table(
            ["Registro", "Eventos"],
            [(e.get("nombre"), int(_num(e.get("eventos")))) for e in rows[:top]],
        )
    return _section("Log", body)


def section_casino(report: Report, top: int) -> list[str]:
    """Jugadas, jugadores, apostado, pagado y RTP por juego."""
    rows = []
    for raw in _list(report.get("casino")):
        game = _dict(raw)
        plays = int(_num(game.get("jugadas")))
        rtp = game.get("rtp")
        flags = []
        if plays == 0:
            flags.append(f"{WARN} sin jugadas")
        if isinstance(rtp, int | float) and rtp > 1:
            flags.append(f"{WARN} la casa pierde")
        rows.append(
            (
                game.get("juego"),
                plays,
                int(_num(game.get("jugadores"))),
                int(_num(game.get("apostado"))),
                int(_num(game.get("pagado"))),
                "-" if rtp is None else f"{rtp:.1%}",
                " ".join(flags),
            )
        )
    body: list[str] = []
    if rows:
        body = _table(
            ["Juego", "Jugadas", "Jugadores", "Apostado", "Pagado", "RTP", "Avisos"], rows
        )
    return _section("Casino", body)


def section_universo_devices(report: Report, top: int) -> list[str]:
    """Cómo va el Jovaniverso en los dispositivos (`universo_disp`, de `/api/medidas`).

    Cada nombre es «medida · plataforma» y el valor va en los campos de `ms`: la media de
    los tramos (cada 5 min de juego) y el máximo de todos.
    """
    by: dict[str, dict[str, dict[str, Any]]] = {}
    for e in _entries(report, "universo_disp"):
        name, _, platform = str(e.get("nombre")).rpartition(" · ")
        by.setdefault(platform, {})[name] = e
    body: list[str] = []
    if not by:
        return _section("Jovaniverso en los dispositivos", body)

    def get(m: dict[str, dict[str, Any]], name: str, field: str = "eventos") -> float:
        return _num(_dict(m.get(name)).get(field))

    rows, budget, entry = [], [], []
    for platform, m in sorted(by.items()):
        tramos = get(m, "tramo")
        fps = get(m, "fps", "ms_medio")
        rows.append(
            (
                platform,
                int(tramos),
                int(get(m, "tramo", "usuarios")),
                f"{fps:.0f}" + (f" {WARN}" if tramos and fps < UNIVERSO_FPS_WARN else ""),
                f"{get(m, 'fps_p5', 'ms_medio'):.0f}",
                f"{_pct(get(m, 'sin_sombras'), tramos)} ({int(get(m, 'sin_sombras', 'usuarios'))})",
                f"{_pct(get(m, 'resolucion_baja'), tramos)}"
                f" ({int(get(m, 'resolucion_baja', 'usuarios'))})",
                f"{int(get(m, 'bajadas'))} / {int(get(m, 'subidas'))}",
                int(get(m, "reconexiones")),
            )
        )
        if tramos:
            calls, tris = get(m, "llamadas_max", "ms_max"), get(m, "triangulos_max", "ms_max")
            budget.append(
                (
                    platform,
                    f"{get(m, 'llamadas', 'ms_medio'):.0f}",
                    f"{calls:.0f}" + (f" {WARN}" if calls > UNIVERSO_CALLS else ""),
                    f"{get(m, 'triangulos', 'ms_medio'):,.0f}".replace(",", "."),
                    f"{tris:,.0f}".replace(",", ".")
                    + (f" {WARN}" if tris > UNIVERSO_TRIANGLES else ""),
                )
            )
        for step, label in UNIVERSO_ENTRY:
            if get(m, f"entrada_{step}"):
                entry.append(
                    (
                        platform,
                        label,
                        int(get(m, f"entrada_{step}")),
                        f"{get(m, f'entrada_{step}', 'ms_medio') / 1000:.1f}",
                        f"{get(m, f'entrada_{step}', 'ms_max') / 1000:.1f}",
                    )
                )
    body = [
        "Tramos de ~5 min de juego. Sin sombras y a resolución baja, al acabar cada tramo",
        "(entre paréntesis, miembros distintos). Bajadas / subidas de la calidad adaptativa.",
        "",
    ]
    body += _table(
        [
            "Plataforma",
            "Tramos",
            "Miembros",
            "fps medio",
            "fps p5",
            "Sin sombras",
            "Resolución baja",
            "Bajadas / subidas",
            "Reconexiones",
        ],
        rows,
    )
    if budget:
        body += [
            "",
            f"Presupuesto por fotograma: {UNIVERSO_CALLS} llamadas de dibujo y"
            f" {UNIVERSO_TRIANGLES:,} triángulos, sombras incluidas.".replace(",", "."),
            "",
        ]
        body += _table(
            [
                "Plataforma",
                "Llamadas medias",
                "Llamadas máx",
                "Triángulos medios",
                "Triángulos máx",
            ],
            budget,
        )
    if entry:
        body += ["", "Entrada: segundos desde que se abre la página hasta acabar cada tramo.", ""]
        body += _table(["Plataforma", "Tramo", "Entradas", "s medio", "s máx"], entry)
    for prefix, title in (("gpu:", "GPU"), ("pantalla:", "Pantallas")):
        seen = sorted(
            (
                (-get(m, name), f"{name.removeprefix(prefix)} ({platform})")
                for platform, m in by.items()
                for name in m
                if name.startswith(prefix)
            ),
        )[:top]
        if seen:
            body += ["", f"{title}: " + ", ".join(f"{label} {int(-n)}" for n, label in seen)]
    return _section("Jovaniverso en los dispositivos", body)


def section_economy(report: Report, top: int) -> list[str]:
    """Masa monetaria, desigualdad, cuentas del sistema y motivos con más movimiento."""
    eco = _dict(report.get("economia"))
    if not eco:
        return _section("Economía", [])
    body = [
        f"- Masa monetaria: {int(_num(eco.get('masa_monetaria')))}",
        f"- Miembros con saldo: {int(_num(eco.get('miembros_con_saldo')))}",
        f"- Gini: {_num(eco.get('gini')):.3f}",
        f"- Parte del 10 % más rico: {100 * _num(eco.get('top10_pct')):.1f} %",
    ]
    accounts = _dict(eco.get("cuentas_sistema"))
    if accounts:
        body.append("- Cuentas del sistema: " + ", ".join(f"{k} {v}" for k, v in accounts.items()))
    reasons = [r for r in _list(eco.get("motivos")) if isinstance(r, dict)]
    if reasons:
        body += ["", f"Motivos con más movimiento (top {top}):", ""]
        body += _table(
            ["Motivo", "Movimientos", "Entradas", "Salidas"],
            [
                (
                    r.get("motivo"),
                    int(_num(r.get("movimientos"))),
                    int(_num(r.get("entradas"))),
                    int(_num(r.get("salidas"))),
                )
                for r in reasons[:top]
            ],
        )
    return _section("Economía", body)


# --------------------------------------------------------------------------- principal


def build_text(
    report: Report, top: int = DEFAULT_TOP, limit_mb: float = DEFAULT_LIMIT_MB
) -> list[str]:
    """Une todas las secciones, en orden, en las líneas del resumen completo."""
    lines = ["# Análisis de métricas del bot", ""]
    for section in (
        section_summary,
        section_slow,
        section_failures,
        section_usage,
        section_unused,
        section_trend,
        section_peak_hours,
        section_retention,
        section_newcomers,
        section_channels,
        section_bot_messages,
    ):
        lines += section(report, top)
    lines += section_system(report, top, limit_mb)
    for section in (
        section_versions,
        section_database,
        section_api,
        section_locks,
        section_tasks,
        section_render,
        section_painters,
        section_upload,
        section_weight,
        section_image,
        section_lost,
        section_lag,
        section_discord,
        section_exceptions,
        section_log,
        section_views,
        section_misuse,
        section_menus,
        section_universo_devices,
        section_casino,
        section_economy,
        section_economy_days,
        section_achievements,
        section_shop,
        section_music,
    ):
        lines += section(report, top)
    return lines


def merge_reports(reports: Sequence[Report]) -> Report:
    """Junta varios informes en uno global (ver la cabecera del módulo)."""
    ordered = sorted(reports, key=lambda r: (str(r.get("hasta")), str(r.get("generado"))))
    merged = dict(ordered[-1])
    for key in DAILY_KEYS:
        days: dict[str, Any] = {}
        for report in ordered:
            for day, value in _dict(report.get(key)).items():
                # Campo a campo: un informe más nuevo no borra lo que solo traía otro.
                days[day] = {**_dict(days.get(day)), **_dict(value)}
        merged[key] = dict(sorted(days.items()))
    starts = [str(r.get("desde")) for r in ordered if r.get("desde")]
    merged["informes"] = len(ordered)
    if starts:
        merged["desde_global"] = min(starts)
    return merged


def _report_paths(paths: Sequence[str]) -> list[Path]:
    found: list[Path] = []
    for raw in paths:
        path = Path(raw)
        found += sorted(path.glob("informe*.json")) if path.is_dir() else [path]
    return found


def main(argv: Sequence[str] | None = None) -> int:
    """Lee el informe (o los informes, juntos) y escribe el resumen por la salida estándar."""
    parser = argparse.ArgumentParser(description="Resume el informe JSON de uso del bot.")
    parser.add_argument(
        "informe",
        nargs="+",
        help="JSON que genera el comando `informe`; con varios o una carpeta, los junta",
    )
    parser.add_argument("--top", type=int, default=DEFAULT_TOP, help="filas por ranking")
    parser.add_argument(
        "--limite-mb", type=float, default=DEFAULT_LIMIT_MB, help="límite de memoria en MB"
    )
    args = parser.parse_args(argv)
    reports: list[Report] = []
    for path in _report_paths(args.informe):
        try:
            with open(path, encoding="utf-8") as handle:
                report = json.load(handle)
        except (OSError, json.JSONDecodeError) as error:
            print(f"No se pudo leer el informe: {error}", file=sys.stderr)
            return 1
        if not isinstance(report, dict):
            print("El informe no es un objeto JSON.", file=sys.stderr)
            return 1
        reports.append(report)
    if not reports:
        print("No se pudo leer el informe: no hay ningún informe*.json.", file=sys.stderr)
        return 1
    report = reports[0] if len(reports) == 1 else merge_reports(reports)
    lines = build_text(report, max(args.top, 1), args.limite_mb)
    if len(reports) > 1:
        lines[1:1] = [
            f"Visión global de {len(reports)} informes, desde {report.get('desde_global')}:"
            " lo que va por día cubre todos; lo demás es del más nuevo.",
            "",
        ]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
