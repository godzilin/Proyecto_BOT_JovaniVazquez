"""Pruebas de `docs/analisis_metricas.py` con informes sintéticos pequeños.

El script vive en `docs/` (no es paquete), así que se carga por ruta. Las secciones
son funciones puras, de modo que se prueban sin tocar el disco salvo en `main`.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "docs" / "analisis_metricas.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("analisis_metricas", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


am = _load()


def _entry(kind, name, events, *, errors=0, users=1, ms=None, buckets=(0, 0, 0, 0), ms_max=0):
    return {
        "tipo": kind,
        "nombre": name,
        "eventos": events,
        "errores": errors,
        "usuarios": users,
        "ms_medio": ms,
        "ms_max": ms_max,
        "ms": dict(zip(["<500", "<1000", "<3000", ">=3000"], buckets, strict=True)),
    }


def _report() -> dict:
    per_day = {}
    for i in range(30):
        day = f"2026-09-{i + 1:02d}"
        late = i >= 15
        per_day[day] = {
            "message": {"eventos": 20 if late else 10, "errores": 0, "usuarios": 4},
            "slash": {"eventos": 5, "errores": 0, "usuarios": 2},
        }
    activity = {
        # u1..u4 estrenan `moneda` en la primera semana; u1 y u2 vuelven tras el día 14.
        "u1": {"slash:moneda": [0, 20], "button:CoinView.heads": [1]},
        "u2": {"text:moneda": [2, 15], "button:CoinView.tails": [3]},
        "u3": {"slash:moneda": [4], "message:#chat": [25]},
        "u4": {"slash:moneda": [5]},
        "u5": {"slash:ruleta": [10]},
    }
    return {
        "version": 1,
        "generado": "2026-09-30T12:00:00",
        "desde": "2026-09-01",
        "hasta": "2026-09-30",
        "dias": 30,
        "uso": [
            _entry(
                "slash", "moneda", 40, errors=2, users=3, ms=900, buckets=(3, 2, 1, 1), ms_max=4000
            ),
            _entry("text", "moneda", 10, users=2),
            _entry("slash", "ruleta", 3, users=1, ms=100, buckets=(3, 0, 0, 0), ms_max=150),
            _entry("button", "CoinView.heads", 25, errors=1, users=3, ms=300, buckets=(5, 0, 0, 0)),
            _entry("button", "CoinView.tails", 15, users=2),
            _entry("message", "#chat", 500, users=5),
            _entry("voice", "#sala", 90, users=2),
            _entry("render", "coin", 12, ms=1200.0, ms_max=2500),
            _entry("upload", "moneda.gif · 250 KB-1 MB", 9, ms=800.0, ms_max=2100),
            _entry("lag", "bot/services/coin_scene.py:board", 2, ms=400.0, ms_max=600),
            _entry("log", "WARNING:bot.cogs.x", 7),
        ],
        "por_dia": per_day,
        "por_hora": {"message": [0] * 20 + [5, 9, 1, 0], "slash": [0, 4] + [0] * 22},
        "comandos_sin_uso": ["pala", "caballo"],
        "actividad": activity,
        "sistema": {
            "2026-09-29": {
                "memoria_mb_max": 1900.0,
                "lag_ms_max": 300.0,
                "chromium_max": 2,
                "node_max": 1,
                "gateway_ms_media": 80.0,
            },
            "2026-09-30": {"memoria_mb_max": 800.0, "lag_ms_max": 10.0, "gateway_ms_media": None},
        },
        "casino": [
            {
                "juego": "moneda",
                "jugadas": 10,
                "jugadores": 3,
                "apostado": 100,
                "pagado": 120,
                "rtp": 1.2,
            },
            {
                "juego": "dados",
                "jugadas": 0,
                "jugadores": 0,
                "apostado": 0,
                "pagado": 0,
                "rtp": None,
            },
            {
                "juego": "ruleta",
                "jugadas": 5,
                "jugadores": 2,
                "apostado": 100,
                "pagado": 90,
                "rtp": 0.9,
            },
        ],
        "economia": {
            "masa_monetaria": 5000,
            "miembros_con_saldo": 4,
            "gini": 0.4321,
            "top10_pct": 0.35,
            "cuentas_sistema": {"estado": 777},
            "motivos": [
                {"motivo": "casino", "movimientos": 10, "entradas": 50, "salidas": 70},
                {"motivo": "sueldo", "movimientos": 2, "entradas": 500, "salidas": 0},
            ],
        },
    }


def _empty() -> dict:
    return {
        "version": 1,
        "desde": "2026-09-01",
        "hasta": "2026-09-07",
        "dias": 7,
        "uso": [],
        "por_dia": {},
        "por_hora": {},
        "comandos_sin_uso": [],
        "actividad": {},
        "sistema": {},
        "casino": [],
        "economia": {},
    }


def _text(lines: list[str]) -> str:
    return "\n".join(lines)


def test_informe_casi_vacio_no_revienta_y_dice_sin_datos():
    text = _text(
        am.build_text({"version": 1, "desde": "2026-09-01", "hasta": "2026-09-07", "dias": 7})
    )
    assert "## Resumen" in text
    assert "Sin datos." in text


def test_informe_vacio_con_todas_las_claves_no_revienta():
    text = _text(am.build_text(_empty()))
    assert "## Casino" in text and "Sin datos." in text


def test_informe_sin_fechas_validas_no_revienta():
    text = _text(am.build_text({"desde": None, "hasta": "x", "uso": None, "actividad": []}))
    assert "## Tendencia" in text


def test_secciones_salen_en_el_orden_pedido():
    text = _text(am.build_text(_report()))
    titles = [line for line in text.splitlines() if line.startswith("## ")]
    assert titles == [
        "## Resumen",
        "## Lentos",
        "## Fallos",
        "## Lo más y lo menos usado",
        "## Comandos sin uso",
        "## Tendencia",
        "## Horas punta",
        "## Retención por funcionalidad",
        "## Nuevos miembros",
        "## Canales",
        "## Avisos del bot",
        "## Sistema",
        "## Versiones y reinicios",
        "## Base de datos",
        "## Llamadas a Discord",
        "## Esperas en candados",
        "## Tareas programadas",
        "## Dibujos",
        "## Procesos de dibujo",
        "## Subidas a Discord",
        "## Peso de las imágenes",
        "## Del clic a la imagen",
        "## Interacciones perdidas (10062)",
        "## Bloqueos del bucle",
        "## Conexión con Discord",
        "## Excepciones",
        "## Log",
        "## Partidas y paneles",
        "## Usos incorrectos y rechazos",
        "## Menús y ayuda",
        "## Jovaniverso en los dispositivos",
        "## Casino",
        "## Economía",
        "## Economía por día",
        "## Logros",
        "## Tienda y mascotas",
        "## Música",
    ]


def test_resumen_cuenta_eventos_por_tipo_y_usuarios_distintos():
    text = _text(am.section_summary(_report(), 10))
    assert "2026-09-01 a 2026-09-30 (30 días)" in text
    assert "slash 43" in text and "button 40" in text
    assert "Usuarios distintos con actividad: 5" in text


def test_lentos_ordena_por_proporcion_y_marca_los_de_tres_segundos():
    lines = am.section_slow(_report(), 10)
    rows = [
        line for line in lines if line.startswith("| ") and ("slash:" in line or "button:" in line)
    ]
    assert "slash:moneda" in rows[0] and "⚠️" in rows[0]
    # 7 medidos: 2 son >=1 s (2/7) y 1 es >=3 s (1/7).
    assert "28.6 %" in rows[0] and "14.3 %" in rows[0]
    assert all("⚠️" not in row for row in rows[1:])


def test_lentos_ignora_entradas_sin_latencia():
    text = _text(am.section_slow(_report(), 10))
    assert "text:moneda" not in text and "message:" not in text


def test_fallos_calcula_la_tasa_y_solo_incluye_interacciones():
    text = _text(am.section_failures(_report(), 10))
    assert "slash:moneda | 2 | 40 | 5.0 %" in text
    assert "button:CoinView.heads | 1 | 25 | 4.0 %" in text
    assert "ruleta" not in text


def test_fallos_sin_errores_lo_dice():
    assert "Sin errores" in _text(am.section_failures(_empty(), 10))


def test_uso_suma_slash_y_text_y_agrupa_botones_por_vista():
    text = _text(am.section_usage(_report(), 10))
    # slash 40 + text 10; usuarios distintos por actividad (u1, u2, u3, u4).
    assert "| moneda | 50 | 4 |" in text
    assert "| CoinView | 40 | 2 |" in text


def test_uso_menos_usados_no_repite_los_del_top():
    text = _text(am.section_usage(_report(), 1))
    assert "Comandos: más usados" in text and "Comandos: menos usados" in text
    assert "| ruleta | 3 | 1 |" in text


def test_uso_sin_actividad_suma_usuarios_de_las_entradas():
    report = _report()
    report["actividad"] = {}
    assert "| moneda | 50 | 5 |" in _text(am.section_usage(report, 10))


def test_sin_uso_lista_los_comandos():
    assert "2: pala, caballo" in _text(am.section_unused(_report(), 10))


def test_tendencia_compara_mitades_con_porcentaje():
    text = _text(am.section_trend(_report(), 10))
    # message: 10/día la primera mitad, 20/día la segunda; slash no cambia.
    assert "| message | 10.0 | 20.0 | +100 % |" in text
    assert "| slash | 5.0 | 5.0 | +0 % |" in text
    assert "voice" not in text


def test_tendencia_con_ventana_de_un_dia_se_omite():
    report = _report()
    report["desde"] = report["hasta"] = "2026-09-30"
    assert "Sin datos." in _text(am.section_trend(report, 10))


def test_horas_punta_ordena_y_separa_mensajes_de_interacciones():
    text = _text(am.section_peak_hours(_report(), 10))
    assert "Mensajes: 21h (9), 20h (5), 22h (1)" in text
    assert "Comandos y botones: 01h (4)" in text


def test_retencion_cuenta_nuevos_y_los_que_vuelven():
    text = _text(am.section_retention(_report(), 10))
    # moneda: u1..u4 (4 usuarios), todos nuevos; vuelven u1 (20) y u2 (15).
    assert "| comando moneda | 4 | 4 | 2 | 50.0 % |" in text
    # CoinView solo tiene 2 usuarios: queda fuera por debajo del mínimo.
    assert "vista CoinView" not in text
    assert "semana 1 | 4" in text


def test_retencion_en_ventana_corta_lo_dice_y_salta_la_tabla():
    report = _report()
    report["desde"], report["hasta"] = "2026-09-01", "2026-09-14"
    text = _text(am.section_retention(report, 10))
    assert "hacen falta al menos 21" in text
    assert "Usuarios activos" not in text


def test_canales_ordena_mensajes_y_voz():
    text = _text(am.section_channels(_report(), 10))
    assert "| #chat | 500 | 5 |" in text and "| #sala | 90 | 2 |" in text


def test_sistema_marca_memoria_alta_y_bucle_lento():
    text = _text(am.section_system(_report(), 10, 2048))
    day29 = next(line for line in text.splitlines() if line.startswith("| 2026-09-29"))
    day30 = next(line for line in text.splitlines() if line.startswith("| 2026-09-30"))
    assert "⚠️ memoria" in day29 and "⚠️ bucle" in day29
    assert "⚠️" not in day30


def test_sistema_respeta_el_limite_indicado():
    text = _text(am.section_system(_report(), 10, 8000))
    assert "⚠️ memoria" not in text


def test_dibujos_y_log_salen_ordenados():
    assert "| coin | 12 | 1200 | 2500 |" in _text(am.section_render(_report(), 10))
    assert "WARNING:bot.cogs.x | 7" in _text(am.section_log(_report(), 10))
    assert "| moneda.gif · 250 KB-1 MB | 9 | 0 | 800 | 2100 |" in _text(
        am.section_upload(_report(), 10)
    )
    assert "| bot/services/coin_scene.py:board | 2 | 400 | 600 |" in _text(
        am.section_lag(_report(), 10)
    )


def test_casino_avisa_si_la_casa_pierde_o_no_hay_jugadas():
    lines = am.section_casino(_report(), 10)
    by_game = {
        line.split("|")[1].strip(): line
        for line in lines
        if line.startswith("| ") and "---" not in line
    }
    assert "la casa pierde" in by_game["moneda"]
    assert "sin jugadas" in by_game["dados"]
    assert "⚠️" not in by_game["ruleta"]


def test_economia_muestra_cifras_cuentas_y_motivos_por_movimiento():
    text = _text(am.section_economy(_report(), 1))
    assert "Masa monetaria: 5000" in text and "Gini: 0.432" in text
    assert "35.0 %" in text and "estado 777" in text
    assert "| casino | 10 | 50 | 70 |" in text
    assert "sueldo" not in text  # con --top 1 solo entra el de más movimiento


def test_main_lee_el_archivo_y_respeta_top(tmp_path, capsys):
    path = tmp_path / "informe.json"
    path.write_text(json.dumps(_report()), encoding="utf-8")
    assert am.main([str(path), "--top", "1", "--limite-mb", "1000"]) == 0
    out = capsys.readouterr().out
    assert "## Economía" in out and "Límite de memoria: 1000 MB" in out


def test_main_con_archivo_inexistente_o_roto_devuelve_error(tmp_path, capsys):
    assert am.main([str(tmp_path / "no-existe.json")]) == 1
    broken = tmp_path / "roto.json"
    broken.write_text("{", encoding="utf-8")
    assert am.main([str(broken)]) == 1
    listing = tmp_path / "lista.json"
    listing.write_text("[]", encoding="utf-8")
    assert am.main([str(listing)]) == 1
    assert "No se pudo leer" in capsys.readouterr().err


@pytest.mark.parametrize("section", [name for name in dir(am) if name.startswith("section_")])
def test_cada_seccion_acepta_un_informe_vacio(section):
    func = getattr(am, section)
    lines = func({}, 5)
    assert lines[0].startswith("## ")


def _days(start: int, end: int, ms: float, slow: int) -> dict:
    return {
        f"2026-10-{day:02d}": {
            "button": {
                "eventos": 100,
                "errores": 2,
                "usuarios": 5,
                "ms_medio": ms,
                "ms_n": 100,
                "lentos": slow,
            }
        }
        for day in range(start, end + 1)
    }


def test_versiones_compara_latencia_y_cuenta_reinicios():
    report = {
        "por_dia": {**_days(1, 3, 900.0, 10), **_days(4, 5, 300.0, 0)},
        "sistema": {"2026-10-02": {"cpu_pct_media": 40.0, "memoria_mb_max": 1500.0}},
        "arranques": {
            "2026-10-01": {"7.10.0": 1},
            "2026-10-02": {"7.10.0": 2},
            "2026-10-04": {"7.11.0": 1},
        },
    }
    rows = {
        line.split("|")[1].strip(): line
        for line in am.section_versions(report, 10)
        if line.startswith("| 7.")
    }
    assert (
        "| 2026-10-01 a 2026-10-03 | 3 | 2 ⚠️ | 100 | 900 | 10.0 % | 2.0 % | 40 | 1500 |"
        in rows["7.10.0"]
    )
    assert "| 2026-10-04 a 2026-10-05 | 2 | 0 | 100 | 300 | 0.0 % |" in rows["7.11.0"]


def test_clic_a_imagen_resta_la_subida_y_marca_las_esperas_largas():
    report = {
        "uso": [
            _entry("upload", "moneda.gif · <250 KB", 10, ms=400.0),
            _entry("upload", "moneda.gif · 250 KB-1 MB", 10, ms=600.0),
            _entry("imagen", "moneda.gif", 20, ms=700.0, buckets=(10, 9, 0, 1), ms_max=3200),
        ]
    }
    text = _text(am.section_image(report, 10))
    assert "| ⚠️ | moneda.gif | 20 | 700 | 500 | 200 | 5.0 % | 3200 |" in text


def test_conexion_y_caducadas_resumen_lo_que_frena_al_bot():
    report = {
        "uso": [
            _entry("gateway", "conectada", 2),
            _entry("gateway", "desconexion", 1),
            _entry("oom", "contenedor", 3),
            _entry("db", "volcado", 1440, ms=12.0, ms_max=900),
            _entry("ratelimit", "PATCH /channels/#/messages/#", 4),
            _entry("caducada", "CoinView", 7),
        ]
    }
    text = _text(am.section_discord(report, 10))
    assert "2 conectada, 1 cortes, 0 reanudadas ⚠️" in text
    assert "matados por falta de memoria: 3" in text
    assert "1440, 12 ms de media, 900 de máximo" in text
    assert "| PATCH /channels/#/messages/# | 4 |" in text


def test_sistema_enseña_cpu_base_de_datos_y_disco():
    report = {
        "sistema": {
            "2026-10-01": {
                "cpu_pct_media": 40.0,
                "cpu_pct_max": 90.0,
                "db_mb_max": 120.0,
                "disco_libre_mb_min": 2048.0,
            }
        }
    }
    text = _text(am.section_system(report, 10))
    assert "| 40 / 90 |" in text and "| 120 | 2.0 | - | ⚠️ CPU |" in text


def test_varios_informes_se_juntan_por_dia_y_manda_el_mas_nuevo(tmp_path, capsys):
    old = {
        **_empty(),
        "hasta": "2026-10-02",
        "desde": "2026-09-03",
        "por_dia": _days(1, 2, 900.0, 0),
        "arranques": {"2026-10-01": {"7.10.0": 1}},
        "uso": [_entry("slash", "viejo", 1)],
    }
    new = {
        **_empty(),
        "hasta": "2026-10-04",
        "desde": "2026-09-05",
        "por_dia": _days(2, 4, 300.0, 0),
        "arranques": {"2026-10-03": {"7.11.0": 1}},
        "uso": [_entry("slash", "nuevo", 1)],
    }
    merged = am.merge_reports([new, old])
    assert list(merged["por_dia"]) == ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]
    assert merged["por_dia"]["2026-10-02"]["button"]["ms_medio"] == 300.0
    assert merged["uso"][0]["nombre"] == "nuevo"
    assert (merged["informes"], merged["desde_global"]) == (2, "2026-09-03")

    folder = tmp_path / "metricas"
    folder.mkdir()
    (folder / "informe-2026-10-02.json").write_text(json.dumps(old), encoding="utf-8")
    (folder / "informe-2026-10-04.json").write_text(json.dumps(new), encoding="utf-8")
    (folder / "README.md").write_text("no es un informe", encoding="utf-8")
    assert am.main([str(folder)]) == 0
    out = capsys.readouterr().out
    assert "Visión global de 2 informes, desde 2026-09-03" in out
    assert "| 7.10.0 |" in out and "| 7.11.0 |" in out
    assert am.main([str(tmp_path / "vacia")]) == 1


def test_sistema_cuenta_los_minutos_sin_datos_salvo_el_dia_del_informe():
    report = {
        "hasta": "2026-10-03",
        "sistema": {
            "2026-10-01": {"muestras": 1435},
            "2026-10-02": {"muestras": 1300},
            "2026-10-03": {"muestras": 600},
        },
    }
    lines = {
        line.split("|")[1].strip(): line
        for line in am.section_system(report, 10)
        if line.startswith("| 2026")
    }
    assert "| 5 |" in lines["2026-10-01"] and "caído" not in lines["2026-10-01"]
    assert "| 140 | ⚠️ caído |" in lines["2026-10-02"]
    assert "| - |" in lines["2026-10-03"]


def test_base_de_datos_y_candados_ordenan_por_tiempo_total_y_marcan_lo_lento():
    report = {
        "uso": [
            _entry("sql", "economy · escritura", 100, ms=50.0, buckets=(95, 3, 1, 1), ms_max=4000),
            _entry("sql", "levels · lectura", 1000, ms=1.0, buckets=(1000, 0, 0, 0), ms_max=3),
            _entry("sql", "shop · commit", 2, errors=2, ms=10.0, buckets=(2, 0, 0, 0)),
            _entry("candado", "coin:CoinView._lock", 4, ms=300.0, buckets=(3, 1, 0, 0)),
        ]
    }
    lines = [line for line in am.section_database(report, 10) if line.startswith("| ")]
    # lines[0] es la cabecera.
    assert "economy · escritura" in lines[1] and "| 2.0 % | 0 |" in lines[1]
    assert "levels · lectura" in lines[2] and lines[2].startswith("|  |")
    assert "shop · commit" in lines[3] and lines[3].endswith("| 2 |")
    assert "| coin:CoinView._lock | 4 | 300 |" in _text(am.section_locks(report, 10))


def test_procesos_de_dibujo_peso_y_10062():
    report = {
        "uso": [
            _entry("node", "proceso abierto", 3, ms=350.0),
            _entry("node", "proceso cerrado", 2),
            _entry("node", "proceso matado", 1),
            _entry("node", "espera de proceso", 50, ms=40.0, ms_max=900),
            _entry("plan_b", "El pintor de la moneda · Node", 12, errors=1),
            _entry("peso", "moneda.gif", 10, ms=420.0, ms_max=900),
            _entry("10062", "CoinView.heads", 3, ms=3500.0, ms_max=5000),
        ]
    }
    text = _text(am.section_painters(report, 10))
    assert "3 procesos abiertos (350 ms de media en arrancar), 2 cerrados por no usarse," in text
    assert "1 matados a medias" in text and "40 ms de media, 900 de máximo, en 50" in text
    assert "| El pintor de la moneda · Node | 12 | 1 |" in text
    assert "| moneda.gif | 10 | 420 | 900 |" in _text(am.section_weight(report, 10))
    assert "| CoinView.heads | 3 | 3500 | 5000 |" in _text(am.section_lost(report, 10))


def test_partidas_y_paneles_calcula_abandono_y_duracion():
    report = {
        "uso": [
            _entry("vista", "CoinView", 10),
            _entry("vista_cerrada", "CoinView", 6, ms=120000.0),
            _entry("caducada", "CoinView", 4, ms=600000.0),
            _entry("caducada", "Huerfana", 1),
        ]
    }
    text = _text(am.section_views(report, 10))
    assert "| CoinView | 10 | 6 | 4 | 40.0 % | 2.0 | 10.0 |" in text
    assert "| Huerfana | 0 | 0 | 1 | - | - | - |" in text


def test_usos_incorrectos_menus_y_excepciones():
    report = {
        "uso": [
            _entry("slash", "bizum", 18),
            _entry("text", "bizum", 2),
            _entry("rechazo", "bizum", 5),
            _entry("uso_mal", "tajo", 3),
            _entry("menu", "HelpView.callback → 🎰 Casino", 7),
            _entry("menu", "ShopView.pick → artículo #", 4),
            _entry("excepcion", "KeyError · bot/cogs/coin.py:_settle", 2),
        ]
    }
    misuse = _text(am.section_misuse(report, 10))
    assert "| bizum | 20 | 0 | 5 | 25.0 % |" in misuse
    assert "| tajo | 0 | 3 | 0 | - |" in misuse
    menus = _text(am.section_menus(report, 10))
    assert "| 🎰 Casino | 7 |" in menus and "| ShopView.pick → artículo # | 4 |" in menus
    assert "KeyError · bot/cogs/coin.py:_settle | 2 |" in _text(am.section_exceptions(report, 10))


def test_logros_marcan_la_rareza_que_no_cuadra():
    report = {
        "logros": {
            "miembros_con_logros": 20,
            "por_logro": [
                {
                    "logro": "fácil",
                    "rareza": "Común",
                    "miembros": 18,
                    "en_la_ventana": 5,
                    "pct_miembros": 0.9,
                },
                {
                    "logro": "imposible",
                    "rareza": "Común",
                    "miembros": 0,
                    "en_la_ventana": 0,
                    "pct_miembros": 0.0,
                },
                {
                    "logro": "regalado",
                    "rareza": "Mítico",
                    "miembros": 4,
                    "en_la_ventana": 1,
                    "pct_miembros": 0.2,
                },
            ],
            "sin_conseguir": ["x", "y"],
        }
    }
    text = _text(am.section_achievements(report, 10))
    assert "Miembros con algún logro: 20; logros que nadie tiene: 2" in text
    assert "| Común | 2 | 45.0 % | 90.0 % |" in text
    assert "| regalado | Mítico | 4 | 20.0 % |" in text
    assert "| imposible | Común | 0 | 0.0 % |" in text
    assert "| fácil | Común | 5 |" in text


def test_economia_por_dia_tienda_mascotas_y_musica():
    report = {
        "economia_dia": {
            "2026-10-01": {"entradas": 100, "salidas": 130, "movimientos": 4},
            "2026-10-02": {
                "entradas": 50,
                "salidas": 0,
                "movimientos": 1,
                "masa": 9000,
                "gini": 0.42,
                "estado": 5000,
                "impuestos": {"irpf": 40, "igic": 7},
                "estado_gasto": 15,
            },
        },
        "tienda": [
            {
                "articulo": "Rol VIP",
                "tipo": "rol",
                "ventas": 2,
                "renovaciones": 1,
                "compradores": 2,
                "gastado": 203,
            }
        ],
        "mascotas": {"gato": {"total": 3, "activas": 2}},
        "uso": [
            _entry("musica", "búsqueda", 4, ms=1500.0),
            _entry("musica", "búsqueda sin resultado", 1, ms=900.0),
            _entry("musica", "escuchada", 3, ms=180000.0),
            _entry("musica", "saltada", 1, ms=30000.0),
            _entry("musica", "fallo al sonar", 1, errors=1),
            _entry("musica", "pistas en cola", 4, ms=1.5, ms_max=3),
        ],
    }
    economy = _text(am.section_economy_days(report, 10))
    assert "| 2026-10-01 | 100 | 130 | -30 | 4 | - | 0 | - | - | - |" in economy
    assert (
        "| 2026-10-02 | 50 | 0 | +50 | 1 | igic 7, irpf 40 | 15 | 9000 | 0.420 | 5000 |" in economy
    )
    shop = _text(am.section_shop(report, 10))
    assert "| Rol VIP | rol | 2 | 1 | 2 | 203 |" in shop and "| gato | 3 | 2 |" in shop
    music = _text(am.section_music(report, 10))
    assert "Búsquedas: 5 (1.5 s de media), 1 sin resultado" in music
    assert "3 escuchadas (3.0 min de media), 1 saltadas (0.5 min antes de saltar)" in music
    assert "1 fallos al sonar ⚠️" in music and "posición media 1.5, máxima 3" in music


def test_al_juntar_informes_la_masa_de_cada_dia_no_se_pierde():
    old = {"hasta": "2026-10-01", "economia_dia": {"2026-10-01": {"entradas": 5, "masa": 900}}}
    new = {
        "hasta": "2026-10-02",
        "economia_dia": {"2026-10-01": {"entradas": 7}, "2026-10-02": {"entradas": 1, "masa": 950}},
    }
    merged = am.merge_reports([old, new])
    assert merged["economia_dia"] == {
        "2026-10-01": {"entradas": 7, "masa": 900},
        "2026-10-02": {"entradas": 1, "masa": 950},
    }


def test_llamadas_a_discord_y_tareas_programadas():
    report = {
        "uso": [
            _entry(
                "api",
                "PUT /guilds/{guild_id}/members/{user_id}/roles/{role_id}",
                20,
                ms=800.0,
                buckets=(5, 10, 4, 1),
                ms_max=3500,
            ),
            _entry("api", "GET /users/@me", 5, ms=50.0, buckets=(5, 0, 0, 0)),
            _entry(
                "tarea",
                "Intereses.pay · 1/día",
                3,
                errors=1,
                ms=2000.0,
                buckets=(0, 0, 3, 0),
                ms_max=2000,
            ),
        ]
    }
    api = _text(am.section_api(report, 10))
    assert (
        "| ⚠️ | PUT /guilds/{guild_id}/members/{user_id}/roles/{role_id} | 20 | 800 | 3500 "
        "| 25.0 % | 0 |" in api
    )
    tasks = _text(am.section_tasks(report, 10))
    assert "| ⚠️ | Intereses.pay · 1/día | 3 | 2000 | 2000 | 100.0 % | 1 |" in tasks


def test_nuevos_miembros_y_avisos_del_bot():
    report = {
        "nuevos": {
            "altas": 4,
            "escribieron": 3,
            "usaron_comandos": 2,
            "usaron_botones": 1,
            "volvieron_tras_7_dias": 2,
            "dias_hasta_escribir_mediana": 0.5,
            "dias_hasta_comando_mediana": 2.0,
            "dias_hasta_boton_mediana": None,
        },
        "uso": [
            _entry("bot_mensaje", "#novedades", 4),
            _entry("reaccion_bot", "#novedades", 10, users=6),
            _entry("bot_mensaje", "#casino", 300),
        ],
    }
    new = _text(am.section_newcomers(report, 10))
    assert "Altas en la ventana: 4" in new
    assert "| Escribió un mensaje | 3 | 75.0 % | 0.5 |" in new
    assert "| Pulsó un botón (jugó) | 1 | 25.0 % | - |" in new
    assert "| Volvió tras 7 días | 2 | 50.0 % | - |" in new
    bot = _text(am.section_bot_messages(report, 10))
    assert "| #casino | 300 | 0 | 0 | 0.00 |" in bot
    assert "| #novedades | 4 | 10 | 6 | 2.50 |" in bot


def test_sistema_enseña_procesos_disco_y_tareas_y_el_arranque():
    report = {
        "sistema": {
            "2026-10-01": {
                "chromium_mb_max": 170.4,
                "node_mb_max": 320.0,
                "disco_leido_mb": 12.0,
                "disco_escrito_mb": 340.0,
                "tareas_max": 55,
            }
        },
        "uso": [_entry("conexion_inicial", "hasta conectar", 2, ms=8500.0, ms_max=12000)],
    }
    assert "| 2026-10-01 | 170 | 320 | 12 | 340 | 55 |" in _text(am.section_system(report, 10))
    assert "Del arranque a conectar: 8.5 s de media, 12.0 de máximo, en 2 arranques" in _text(
        am.section_discord(report, 10)
    )


def test_jovaniverso_en_los_dispositivos_por_plataforma_y_frente_al_presupuesto():
    def disp(name, events=1, *, users=1, ms=None, ms_max=0):
        return _entry("universo_disp", name, events, users=users, ms=ms, ms_max=ms_max)

    report = {
        "uso": [
            disp("tramo · mobile", 10, users=4),
            disp("fps · mobile", 10, ms=24, ms_max=60),
            disp("fps_p5 · mobile", 10, ms=12, ms_max=40),
            disp("sin_sombras · mobile", 3, users=2),
            disp("resolucion_baja · mobile", 6, users=3),
            disp("bajadas · mobile", 7),
            disp("subidas · mobile", 2),
            disp("llamadas · mobile", 10, ms=90, ms_max=110),
            disp("llamadas_max · mobile", 10, ms=110, ms_max=150),
            disp("triangulos · mobile", 10, ms=120_000, ms_max=180_000),
            disp("triangulos_max · mobile", 10, ms=180_000, ms_max=240_000),
            disp("entrada_frame · mobile", 4, ms=6500, ms_max=12_000),
            disp("gpu:adreno · mobile", 3),
            disp("pantalla:400x800 · mobile", 4),
            disp("tramo · desktop", 5, users=2),
            disp("fps · desktop", 5, ms=60, ms_max=60),
        ]
    }
    text = _text(am.section_universo_devices(report, 10))
    assert "| mobile | 10 | 4 | 24 ⚠️ | 12 | 30.0 % (2) | 60.0 % (3) | 7 / 2 | 0 |" in text
    assert "| desktop | 5 | 2 | 60 | 0 |" in text
    assert "| mobile | 90 | 150 ⚠️ | 120.000 | 240.000 |" in text
    assert "| mobile | Primer fotograma | 4 | 6.5 | 12.0 |" in text
    assert "GPU: adreno (mobile) 3" in text
    assert "Pantallas: 400x800 (mobile) 4" in text
    assert "Sin datos." in _text(am.section_universo_devices({"uso": []}, 10))
