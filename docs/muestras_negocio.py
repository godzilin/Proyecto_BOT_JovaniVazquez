"""Muestras de las pantallas de los negocios: un estado de cada pantalla y variante.

Sirve para revisar la escena (`assets/negocio/escena.html`) a ojo y a las pruebas
(`tests/unit/test_business_scene.py`), que importan `samples()` para no repetirlas.

Uso (desde la raíz del repo):

    python docs/muestras_negocio.py <carpeta>

Pinta cada muestra con `BusinessScene`, deja un PNG por muestra en `<carpeta>` y una hoja
de contactos con todas (`hoja.png`), y dice lo que tardó cada pantalla.
"""

from __future__ import annotations

import asyncio
import copy
import io
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from bot.services.business import Business, add_asset, move_quote, setup_asset
from bot.services.business_catalog import EQUIPMENT_BY_KEY, SECTOR_BY_KEY, SECTORS
from bot.services.business_view import (
    mejoras_state,
    negocio_state,
    resenas_state,
    traslado_state,
)
from bot.services.housing_catalog import BARRIO_BY_KEY

#: Lo común de cada negocio de ejemplo: nombre, sector, colores del rótulo, emblema y tema.
SHOPS: dict[str, dict[str, Any]] = {
    "cafeteria": {
        "name": "Cafetería La Tertulia",
        "sector": "cafeteria",
        "main": "#0f4c81",
        "accent": "#f6bd2d",
        "emblem": "taza",
    },
    "tienda": {
        "name": "Ultramarinos Pino",
        "sector": "tienda",
        "main": "#1f5a37",
        "accent": "#f0dfb2",
        "emblem": "sol",
    },
    "taller": {
        "name": "Talleres El Drago",
        "sector": "taller",
        "main": "#2a242d",
        "accent": "#f58220",
        "emblem": "drago",
    },
    "heladeria": {
        "name": "Heladería Dácil",
        "sector": "heladeria",
        "main": "#13867f",
        "accent": "#fbf3e0",
        "emblem": "sol",
    },
    "ferreteria": {
        "name": "Ferretería El Tornillo",
        "sector": "ferreteria",
        "main": "#b3261e",
        "accent": "#f6bd2d",
        "emblem": "rayo",
    },
}

SECTOR_NAME = {s.key: s.name for s in SECTORS}

JOB = {
    "icon": "obra",
    "name": "Obra",
    "title": "Encofrador",
    "level": "3/10",
    "pay": "4.500 Y$ por turno",
    "today": "2 de 4 turnos de jornada",
    "rgb": "#e67e22",
    "note": "Próximo turno: ordinario",
}


def _business(sector: str, **extra: Any) -> dict[str, Any]:
    """La tarjeta del negocio en la pantalla del día."""
    return {
        **SHOPS[sector],
        "sector_name": SECTOR_NAME[sector],
        "barrio": "Triana",
        "stars": 3.5,
        "today": "+12.400 Y$ hoy",
        "status": "2 turnos hoy",
        "alert": "Queda poco bocadillo",
        **extra,
    }


def _slots(*kinds: str) -> list[dict[str, str]]:
    return [{"kind": kind} for kind in kinds]


def _dia(**extra: Any) -> dict[str, Any]:
    state = {
        "screen": "dia",
        "who": "Yeyo",
        "date": "Martes 20 de octubre",
        "clock": "18:42",
        "slots": _slots(
            "hecha", "hecha", "jornada", "jornada", "negocio", "negocio", "libre", "libre"
        ),
        "free": "2 franjas libres",
        "jornada": "Jornada completa",
        "battery": 72,
        "family": 64,
        "job": JOB,
        "business": _business("cafeteria"),
        "tip": "Te quedan 2 franjas: un turno más en la cafetería o una hora extra.",
        "night_from": 8,
        "night_free": "4 franjas de noche",
    }
    state.update(extra)
    # Las 4 de noche (`agenda.FRANJAS_NOCHE`) detrás de las del día, como en el juego.
    state["slots"] = state["slots"] + _slots(*["noche"] * (12 - len(state["slots"])))
    return state


def _montar() -> dict[str, Any]:
    lines = {
        "cafeteria": ["Local de 50 m²", "Alquiler: unos 7.900 Y$ al día", "Montarlo: 150.000 Y$"],
        "tienda": ["Local de 60 m²", "Alquiler: unos 9.400 Y$ al día", "Montarlo: 125.000 Y$"],
        "taller": ["Nave de 90 m²", "Alquiler: unos 8.100 Y$ al día", "Montarlo: 180.000 Y$"],
        "heladeria": ["Local de 40 m²", "Alquiler: unos 6.300 Y$ al día", "Montarlo: 115.000 Y$"],
        "ferreteria": ["Local de 70 m²", "Alquiler: unos 11.000 Y$ al día", "Montarlo: 140.000 Y$"],
    }
    return {
        "screen": "montar",
        "title": "Elige qué negocio montas",
        "sectors": [
            {
                "key": key,
                "name": SECTOR_NAME[key],
                "blurb": SECTOR_BY_KEY[key].blurb,
                "lines": lines[key],
                "selected": key == "tienda",
            }
            for key in SECTOR_NAME
        ],
        "foot": "Elige el sector en el desplegable. Luego, el barrio, el nombre y los colores.",
    }


def _negocio(sector: str, theme: str, **extra: Any) -> dict[str, Any]:
    state = {
        "screen": "negocio",
        "mode": "portada",
        **SHOPS[sector],
        "barrio": "Triana",
        "theme": theme,
        "open": True,
        "stars": 3.5,
        "seed": 12345,
        "stats": [
            {"label": "Caja de hoy", "value": "+12.400 Y$"},
            {"label": "Clientes hoy", "value": "17"},
            {"label": "Turnos hoy", "value": "2"},
            {"label": "Esta semana", "value": "+45.000 Y$"},
            {"label": "Alquiler esta noche", "value": "7.900 Y$"},
            {"label": "Reputación", "value": "62 / 100"},
        ],
        "tags": ["Precios normales", "Poco stock: bocadillo", "Abierto hasta las 22:00"],
        "regulars": [
            {"name": "Doña Carmen", "loyalty": 7},
            {"name": "Yeray", "loyalty": 4},
            {"name": "Nayra", "loyalty": 9},
            {"name": "Don Fefo", "loyalty": 2},
            {"name": "Chano", "loyalty": 0},
        ],
        "foot": "Abrir gasta 1 franja. Te quedan 3.",
    }
    state.update(extra)
    return state


PRODUCTS: dict[str, list[tuple[str, str, str, str, str, str | None, bool]]] = {
    "cafeteria": [
        ("cafe", "Café solo", "120", "40 Y$", "150 Y$", None, False),
        ("cortado", "Cortado", "86", "50 Y$", "180 Y$", None, False),
        ("barraquito", "Barraquito", "30", "85 Y$", "280 Y$", None, False),
        ("zumo", "Zumo natural", "14", "110 Y$", "350 Y$", "Caduca un 40 % cada noche", False),
        ("bocadillo", "Bocadillo", "3", "160 Y$", "550 Y$", "Caduca un 50 % cada noche", True),
        ("dulce", "Dulce", "12", "70 Y$", "250 Y$", "Caduca un 50 % cada noche", False),
    ],
    "tienda": [
        ("pan", "Pan", "24", "90 Y$", "150 Y$", "Se tira entero cada noche", False),
        ("leche", "Leche", "40", "120 Y$", "190 Y$", "Caduca un 10 % cada noche", False),
        ("fruta", "Fruta", "2", "260 Y$", "420 Y$", "Caduca un 30 % cada noche", True),
        ("conservas", "Conservas", "48", "320 Y$", "520 Y$", None, False),
        ("limpieza", "Limpieza", "12", "500 Y$", "800 Y$", None, False),
        ("bebidas", "Bebidas", "36", "430 Y$", "700 Y$", None, False),
    ],
    "taller": [
        ("pastillas", "Pastillas de freno", "4", "900 Y$", "2.400 Y$", None, False),
        ("bateria", "Batería", "1", "1.400 Y$", "3.200 Y$", None, True),
        ("aceite", "Aceite y filtro", "6", "500 Y$", "1.600 Y$", None, False),
        ("correa", "Correa", "2", "1.800 Y$", "4.600 Y$", None, False),
        ("bujias", "Bujías", "0", "400 Y$", "1.400 Y$", None, True),
        ("neumatico", "Neumático", "8", "1.100 Y$", "2.600 Y$", None, False),
    ],
    "heladeria": [
        (
            "vainilla",
            "Helado de vainilla",
            "40",
            "60 Y$",
            "250 Y$",
            "Caduca un 10 % cada noche",
            False,
        ),
        (
            "chocolate",
            "Helado de chocolate",
            "52",
            "60 Y$",
            "250 Y$",
            "Caduca un 10 % cada noche",
            False,
        ),
        ("gofio", "Helado de gofio", "9", "70 Y$", "320 Y$", "Caduca un 10 % cada noche", True),
        ("mango", "Sorbete de mango", "20", "80 Y$", "300 Y$", "Caduca un 15 % cada noche", False),
        ("granizado", "Granizado de limón", "64", "30 Y$", "180 Y$", None, False),
        ("batido", "Batido", "14", "70 Y$", "320 Y$", "Caduca un 30 % cada noche", False),
    ],
    "ferreteria": [
        ("tornillos", "Tornillería", "180", "120 Y$", "300 Y$", None, False),
        ("bombillas", "Bombillas", "35", "250 Y$", "600 Y$", None, False),
        ("pintura", "Pintura", "6", "900 Y$", "1.900 Y$", None, False),
        ("herramientas", "Herramientas", "2", "1.100 Y$", "2.400 Y$", None, True),
        ("fontaneria", "Fontanería", "20", "450 Y$", "1.000 Y$", None, False),
        ("cerraduras", "Cerraduras", "8", "800 Y$", "1.700 Y$", None, False),
    ],
}
#: Productos con su propio nivel de precios en las muestras (la chapa del almacén).
LEVELS = {("heladeria", "gofio"): "Caro", ("ferreteria", "tornillos"): "Barato"}


def _almacen(sector: str) -> dict[str, Any]:
    return {
        "screen": "almacen",
        **{k: v for k, v in SHOPS[sector].items() if k != "theme"},
        "products": [
            {
                "key": key,
                "name": name,
                "units": units,
                "cost": cost,
                "price": price,
                "spoil": spoil,
                "low": low,
                "level": LEVELS.get((sector, key)),
            }
            for key, name, units, cost, price, spoil, low in PRODUCTS[sector]
        ],
        "foot": "Compra por lotes en el desplegable. Lo perecedero se pierde en parte cada noche.",
    }


def _cuentas() -> dict[str, Any]:
    def week(title: str, rows: list[tuple[str, str, str]], total: str, good: bool) -> dict:
        return {
            "title": title,
            "rows": [{"label": a, "value": b, "kind": c} for a, b, c in rows],
            "total": {"label": "Resultado", "value": total, "good": good},
        }

    return {
        "screen": "cuentas",
        **{k: SHOPS["cafeteria"][k] for k in ("name", "main", "accent", "emblem")},
        "weeks": [
            week(
                "Esta semana",
                [
                    ("Ventas", "+120.000 Y$", "in"),
                    ("Propinas", "+6.300 Y$", "in"),
                    ("Compras al mayorista", "−38.400 Y$", "out"),
                    ("IGIC de las compras", "−2.690 Y$", "tax"),
                    ("Mermas", "−3.100 Y$", "out"),
                    ("Alquiler del local", "−27.650 Y$", "out"),
                    ("Retención del alquiler", "−5.250 Y$", "tax"),
                    ("Suministros", "−4.200 Y$", "out"),
                    ("Cuota de autónomo", "−8.000 Y$", "tax"),
                    ("IRPF a cuenta", "−9.800 Y$", "tax"),
                ],
                "+27.210 Y$",
                True,
            ),
            week(
                "La semana pasada",
                [
                    ("Ventas", "+64.000 Y$", "in"),
                    ("Propinas", "+2.100 Y$", "in"),
                    ("Compras al mayorista", "−41.000 Y$", "out"),
                    ("IGIC de las compras", "−2.870 Y$", "tax"),
                    ("Acondicionamiento", "−120.000 Y$", "out"),
                    ("Alquiler del local", "−27.650 Y$", "out"),
                    ("Cuota de autónomo", "−8.000 Y$", "tax"),
                ],
                "−133.420 Y$",
                False,
            ),
        ],
        "foot": "Criterio de caja: cada cosa cuenta el día que se cobra o se paga.",
    }


def _cliente(sector: str, **extra: Any) -> dict[str, Any]:
    state = {
        "screen": "cliente",
        **SHOPS[sector],
        "theme": "centro",
        "client": {"seed": 7, "name": None, "regular": False, "line": "Buenas, ¿nos pone esto?"},
        "queue": 3,
        "index": "Cliente 2 de 8",
        # Python manda la caja vacía para poder pintar al siguiente cliente por adelantado.
        "cash": "",
        "patience": "25 s",
    }
    state.update(extra)
    return state


def _cierre(grade: str, **extra: Any) -> dict[str, Any]:
    state = {
        "screen": "cierre",
        **SHOPS["cafeteria"],
        "grade": grade,
        "title": "Buen turno",
        "stats": [
            {"label": "Atendidos", "value": "7 de 8"},
            {"label": "Perdidos", "value": "1"},
            {"label": "Caja", "value": "+6.240 Y$"},
            {"label": "Propinas", "value": "+480 Y$"},
            {"label": "Coste de lo vendido", "value": "−1.870 Y$"},
            {"label": "IRPF a cuenta", "value": "−830 Y$"},
        ],
        "stars_before": 3.0,
        "stars_after": 3.5,
        "lines": [
            "Doña Carmen sube a fidelidad 4",
            "Acertaste «lo de siempre» de Yeray",
            "Se acabó el bocadillo a media tarde",
        ],
    }
    state.update(extra)
    return state


def samples() -> dict[str, dict[str, Any]]:
    """Un estado de ejemplo de cada pantalla y variante, por nombre."""
    s: dict[str, dict[str, Any]] = {}
    s["dia_curro_y_negocio"] = _dia()
    s["dia_solo_curro"] = _dia(
        business=None,
        slots=_slots("hecha", "jornada", "jornada", "jornada", "extra", "libre", "libre", "libre"),
        free="3 franjas libres",
        tip="Con 3 franjas libres podrías montar un negocio por las tardes.",
    )
    s["dia_solo_negocio"] = _dia(
        job=None,
        jornada="Sin curro",
        battery=64,
        family=None,
        slots=_slots("negocio", "negocio", "negocio", "libre", "libre", "libre", "libre", "libre"),
        free="5 franjas libres",
        business=_business("taller", barrio="Schamann", stars=4.0, alert=None),
        tip="Te quedan 5 franjas: el taller abre bien por la mañana.",
    )
    s["dia_sin_nada"] = _dia(
        job=None,
        business=None,
        jornada="Sin curro",
        battery=None,
        family=None,
        slots=_slots(*["libre"] * 8),
        free="8 franjas libres",
        tip="El día entero es tuyo: busca un curro o monta un negocio.",
    )
    s["dia_guardia_excedencia"] = _dia(
        jornada="Excedencia",
        slots=_slots(
            "guardia", "guardia", "estudio", "negocio", "negocio", "negocio", "libre", "libre"
        ),
        free="2 franjas libres",
        job={**JOB, "icon": "sanidad", "name": "Sanidad", "title": "Residente", "level": "5/10",
             "today": "De excedencia hasta que vuelvas", "rgb": "#2e86c1",
             "note": "Puedes volver dentro de 1 día"},
        business=_business("tienda", barrio="Vegueta", stars=2.5, today="−1.200 Y$ hoy",
                           status="3 turnos hoy", alert="Debes 1 noche de alquiler"),
        battery=18,
        family=91,
        tip="Excedencia: la jornada no reserva franjas. Aprovecha para el negocio.",
    )  # fmt: skip
    s["montar"] = _montar()
    s["negocio_cafeteria_centro"] = _negocio("cafeteria", "centro")
    s["negocio_tienda_obrero_cerrado"] = _negocio(
        "tienda", "obrero", open=False, barrio="Schamann", stars=2.0, seed=99
    )
    s["negocio_taller_rural"] = _negocio(
        "taller", "rural", barrio="Tafira", stars=4.5, seed=4242, tags=["Precios caros"]
    )
    s["negocio_cafeteria_marinero_cerrada"] = _negocio(
        "cafeteria", "marinero", open=False, main="#a81f5c", accent="#fbf3e0", emblem="ancla",
        name="Bar La Caleta", barrio="La Isleta", stars=1.0, seed=31,
    )  # fmt: skip
    s["preview_cafeteria_playa"] = _negocio(
        "cafeteria", "playa", mode="preview", main="#13867f", accent="#fbf3e0", emblem="pez",
        name="Chiringuito Dácil", barrio="Las Canteras", stats=[], tags=[], regulars=[],
        stars=2.5, foot="Así quedará tu cafetería en Las Canteras.",
    )  # fmt: skip
    s["preview_tienda_lujo"] = _negocio(
        "tienda", "lujo", mode="preview", main="#cfb676", accent="#1b2a35", emblem="corona",
        name="Gourmet Adeje", barrio="Adeje", stats=[], tags=[], regulars=[], stars=2.5,
        foot="Así quedará tu tienda en Adeje.",
    )  # fmt: skip
    s["preview_taller_historico"] = _negocio(
        "taller", "historico", mode="preview", main="#b3261e", accent="#f6bd2d", emblem="rayo",
        name="Mecánica Rápida Vegueta", barrio="Vegueta", stats=[], tags=[], regulars=[],
        stars=2.5, foot="Así quedará tu taller en Vegueta.",
    )  # fmt: skip
    s["preview_cafeteria_playa_abierta"] = _negocio(
        "cafeteria", "playa", mode="preview", main="#d4600c", accent="#1b2a35", emblem="estrella",
        name="Café Atardecer", barrio="El Médano", stats=[], tags=[], regulars=[], stars=2.5,
        foot="Así quedará tu cafetería en El Médano.",
    )  # fmt: skip
    for sector in SHOPS:
        s[f"almacen_{sector}"] = _almacen(sector)
    s["cuentas"] = _cuentas()
    s["cliente_comandas"] = _cliente(
        "cafeteria",
        order=[
            {"key": "cortado", "n": 2},
            {"key": "bocadillo", "n": 1},
            {"key": "barraquito", "n": 1},
        ],
    )
    s["cliente_comandas_con_caja"] = {
        **s["cliente_comandas"],
        "cash": "+3.400 Y$",
        "client": {"seed": 77, "name": "Yeray", "regular": False,
                   "line": "Rapidito, que se me va la guagua."},
        "order": [{"key": "cafe", "n": 1}, {"key": "zumo", "n": 2}, {"key": "dulce", "n": 3},
                  {"key": "bocadillo", "n": 1}, {"key": "cortado", "n": 1}],
    }  # fmt: skip
    s["cliente_comandas_siempre"] = _cliente(
        "cafeteria",
        theme="marinero",
        client={
            "seed": 3,
            "name": "Doña Carmen",
            "regular": True,
            "line": "Lo de siempre, mi niño.",
        },
        order=None,
        usual=True,
        queue=1,
        index="Cliente 5 de 8",
    )
    s["cliente_caja"] = _cliente(
        "tienda",
        theme="obrero",
        client={"seed": 21, "name": None, "regular": False, "line": "Hola, ¿me cobra?"},
        basket=[
            {"key": "pan", "n": 2, "price": "300 Y$"},
            {"key": "leche", "n": 3, "price": "570 Y$"},
            {"key": "fruta", "n": 1, "price": "420 Y$"},
            {"key": "bebidas", "n": 1, "price": "700 Y$"},
            {"key": "conservas", "n": 1, "price": "520 Y$"},
        ],
        total="2.510 Y$",
        paid="3.000 Y$",
        thief=False,
        queue=4,
        index="Cliente 3 de 7",
        patience="30 s",
    )
    s["cliente_caja_ladron"] = copy.deepcopy(s["cliente_caja"])
    s["cliente_caja_ladron"].update(
        thief=True,
        client={"seed": 58, "name": None, "regular": False, "line": "Esto es todo, gracias."},
        basket=[
            {"key": "pan", "n": 1, "price": "150 Y$"},
            {"key": "limpieza", "n": 1, "price": "800 Y$"},
        ],
        total="950 Y$",
        paid="1.000 Y$",
        queue=0,
    )  # fmt: skip
    s["cliente_diagnostico"] = _cliente(
        "taller",
        theme="obrero",
        client={"seed": 11, "name": None, "regular": False,
                "line": "Buenas, el coche me hace una cosa rara."},
        car={"color": "#c0392b", "model": 0},
        symptom="Chirría al frenar y cada vez frena menos.",
        cue="frenos",
        step="diagnostico",
        queue=2,
        index="Cliente 1 de 4",
        patience="45 s",
    )  # fmt: skip
    s["cliente_presupuesto"] = _cliente(
        "taller",
        theme="rural",
        client={"seed": 40, "name": "Don Agustín", "regular": True,
                "line": "Necesito el coche para el lunes, ¿puede ser?"},
        car={"color": "#2e86c1", "model": 2},
        symptom="Echa humo azulado por el tubo.",
        cue="aceite",
        step="presupuesto",
        queue=0,
        index="Cliente 4 de 4",
        patience="45 s",
    )  # fmt: skip
    # El mismo cliente en el segundo paso: tiene que verse distinto a simple vista.
    s["cliente_diagnostico_paso2"] = {**s["cliente_diagnostico"], "step": "presupuesto"}
    for n, cue in enumerate(("bateria", "correa", "bujias", "rueda")):
        s[f"cliente_diagnostico_{cue}"] = {
            **s["cliente_diagnostico"],
            "car": {"color": ["#f1c40f", "#7f8c8d", "#27ae60", "#ecf0f1"][n], "model": (n + 1) % 4},
            "cue": cue,
            "client": {**s["cliente_diagnostico"]["client"], "seed": 100 + n},
        }
    s["cierre_s"] = _cierre("S", title="Turno de récord", stars_before=3.5, stars_after=4.0)
    s["cierre_d"] = _cierre(
        "D",
        sector="tienda",
        name="Ultramarinos Pino",
        main="#1f5a37",
        accent="#f0dfb2",
        emblem="sol",
        title="Mal turno",
        stars_before=3.0,
        stars_after=2.5,
        stats=[
            {"label": "Atendidos", "value": "3 de 7"},
            {"label": "Perdidos", "value": "4"},
            {"label": "Caja", "value": "+1.150 Y$"},
            {"label": "Cambio de más", "value": "−600 Y$"},
        ],
        lines=["Acusaste a quien no era: −8 de reputación", "Nayra baja a fidelidad 2"],
    )
    s.update(_phase2())
    return s


def _phase2() -> dict[str, dict[str, Any]]:
    """Las muestras de la fase 2, con las funciones de verdad de `business_view`."""
    today = date(2026, 10, 20)
    s: dict[str, dict[str, Any]] = {}
    s["dia_heladeria"] = _dia(
        business=_business("heladeria", barrio="Las Canteras", alert=None, stars=4.5),
        tip="Te quedan 2 franjas: la heladería vende más por la tarde.",
    )
    s["dia_ferreteria"] = _dia(
        job=None, jornada="Sin curro", battery=46, family=None, free="6 franjas libres",
        slots=_slots("negocio", "negocio", *["libre"] * 6),
        business=_business("ferreteria", barrio="Schamann", alert="Poco stock: herramientas"),
        tip="Sin curro también te cansas: cada turno en la ferretería gasta batería.",
    )  # fmt: skip
    s["cliente_heladeria"] = _cliente(
        "heladeria",
        theme="playa",
        order=[{"key": "gofio", "n": 2}, {"key": "granizado", "n": 1}, {"key": "batido", "n": 1}],
        patience="22 s",
    )
    s["cliente_ferreteria"] = _cliente(
        "ferreteria",
        theme="obrero",
        client={"seed": 64, "name": "Don Fefo", "regular": True, "line": "Buenas tardes."},
        basket=[
            {"key": "tornillos", "n": 3, "price": "900 Y$"},
            {"key": "pintura", "n": 1, "price": "1.900 Y$"},
            {"key": "cerraduras", "n": 1, "price": "1.700 Y$"},
            {"key": "bombillas", "n": 2, "price": "1.200 Y$"},
            {"key": "fontaneria", "n": 1, "price": "1.000 Y$"},
            {"key": "herramientas", "n": 1, "price": "2.400 Y$"},
        ],
        total="9.100 Y$",
        paid="10.000 Y$",
        thief=True,
        queue=2,
        index="Cliente 2 de 5",
        patience="35 s",
    )
    for sector in ("heladeria", "ferreteria"):
        s[f"almacen_{sector}"] = _almacen(sector)
    # Una cafetería crecida: local grande y propio, tres mejoras, campaña y buenas reseñas.
    cafe = Business(
        user_id=1, sector="cafeteria", barrio="triana", name="Cafetería La Tertulia",
        seed=12345, reputation=88.0, day=today.isoformat(), turns_today=3,
        sales_today=31_400, clients_today=29, best_turn=14_800, size=2,
        own_listing="triana_local", equipment=["cafetera2", "terraza", "vitrina"],
        campaign="radio", campaign_until=(today + timedelta(days=3)).isoformat(),
        regulars={"Doña Carmen": 9, "Yeray": 6}, week_totals={"ventas": 180_000},
    )  # fmt: skip
    setup_asset(cafe, 230_000)
    for key in cafe.equipment:
        item = EQUIPMENT_BY_KEY[key]
        add_asset(cafe, key, round(item.cost * 1.07), item.kind)
    cafe.assets[1]["left"] -= 9_000
    lines = [(5, 0), (5, 3), (4, 1), (5, 4), (5, 2), (5, 1), (4, 0), (5, 2), (5, 5),
             (4, 3), (5, 0), (5, 1), (5, 3), (2, 0)]  # fmt: skip
    names = ["Doña Carmen", "Ana G.", "Yeray", "Kevin R.", "Saray D.", "Pino", "Airam C.",
             "Lucía N.", "Fran T.", "Nayra", "Moisés A.", "Echedey F.", "Begoña L.",
             "Tamara B."]  # fmt: skip
    cafe.reviews = [
        [(today - timedelta(days=13 - i)).isoformat(), who, n, line]
        for i, (who, (n, line)) in enumerate(zip(names, lines, strict=True))
    ]
    s["negocio_cafeteria_crecida"] = negocio_state(
        cafe, foot="Abrir gasta 1 franja. Te quedan 4.", rivals=2
    )
    s["mejoras_cafeteria"] = mejoras_state(cafe, today)
    s["resenas"] = resenas_state(cafe, today)
    fresh = {
        sector: Business(
            user_id=2,
            sector=sector,
            barrio="schamann",
            name=SHOPS[sector]["name"],
            seed=7,
            day=today.isoformat(),
        )  # fmt: skip
        for sector in SHOPS
    }
    for sector, business in fresh.items():
        if sector != "cafeteria":
            s[f"mejoras_{sector}"] = mejoras_state(business, today)
    s["resenas_vacia"] = resenas_state(fresh["tienda"], today)
    s["negocio_heladeria_playa"] = negocio_state(
        Business(
            user_id=3, sector="heladeria", barrio="canteras", name="Heladería Dácil",
            palette=1, emblem=3, seed=55, reputation=70.0, day=today.isoformat(),
            turns_today=1, sales_today=6_800, clients_today=9,
        ),
        foot="Abrir gasta 1 franja. Te quedan 5.",
    )  # fmt: skip
    s["negocio_ferreteria_obrero"] = negocio_state(
        Business(
            user_id=4, sector="ferreteria", barrio="schamann", name="Ferretería El Tornillo",
            palette=7, emblem=7, seed=77, reputation=40.0, day=today.isoformat(),
            equipment=["tpv"],
        ),
        foot="Abrir gasta 1 franja. Te quedan 6.",
    )  # fmt: skip
    small = fresh["cafeteria"]
    quote = move_quote(small, BARRIO_BY_KEY["vegueta"], 1)
    s["traslado_vegueta"] = traslado_state(small, "vegueta", 1, quote)
    own = move_quote(small, BARRIO_BY_KEY["schamann"], 0, own=True)
    s["traslado_propio"] = traslado_state(small, "schamann", 0, own, own=True)
    s.update(_phase3(today))
    return s


def _phase3(today: date) -> dict[str, dict[str, Any]]:
    """Las muestras de la pantalla 🏢 Sociedad, con las funciones de verdad."""
    from bot.services import business_sl as sl
    from bot.services import business_staff as staff
    from bot.services.business_view import sociedad_state
    from bot.services.negocio import CompanyView

    s: dict[str, dict[str, Any]] = {}
    shop = Business(
        user_id=5, sector="tienda", barrio="schamann", name="Ultramarinos Pino", palette=4,
        emblem=3, seed=21, day=today.isoformat(),
    )  # fmt: skip
    s["sociedad_autonomo"] = sociedad_state(CompanyView(shop, 0, 0, 0, 0.35, None, False))
    s["sociedad_autonomo_1euro"] = sociedad_state(
        CompanyView(shop, 0, 0, 0, 0.35, None, False), capital=100
    )
    blocked = sl.incompatibility("politica", "moncloa", "tienda")
    s["sociedad_incompatible"] = sociedad_state(CompanyView(shop, 0, 0, 0, 0.35, blocked, False))
    company = Business(
        user_id=6, sector="cafeteria", barrio="triana", name="Cafetería La Tertulia",
        seed=12345, day=today.isoformat(), form="sl", reputation=80.0,
    )  # fmt: skip
    company.company = sl.new_company(300_000, 325_000, 0)
    company.company.update(reserve=42_000, retained=168_000, loan=50_000)
    # La RIC al 90 %: dos domingos dotados, parte invertida y el resto por invertir (con aviso).
    company.company["ric_share"] = 0.9
    sunday = today - timedelta(days=(today.weekday() + 1) % 7)
    sl.settle_week(company.company, 150_000, 400_000, day=sunday - timedelta(days=7))
    sl.ric_invest(company.company, 90_000, sunday - timedelta(days=3))
    last = sl.settle_week(company.company, 210_000, 450_000, day=sunday)
    company.company["last"] = {**last, "week": (sunday - timedelta(days=6)).isoformat()}
    s["sociedad_sl"] = sociedad_state(
        CompanyView(company, 412_000, 560_000, 168_000, 0.19, None, False)
    )
    broke = Business(
        user_id=7, sector="taller", barrio="arucas", name="Talleres El Drago", seed=4242,
        day=today.isoformat(), form="sl", palette=5, emblem=5,
    )  # fmt: skip
    broke.company = sl.new_company(1_000_000, 1_000_000, 0)
    broke.company.update(retained=-640_000, losses=640_000, tax_debt=12_000)
    s["sociedad_en_perdidas"] = sociedad_state(
        CompanyView(broke, 18_000, 360_000, 0, 0.19, blocked, True)
    )
    # Un taller con seis empleos con alta: puede entrar en la ZEC y, en otra muestra, ya está.
    garage = Business(
        user_id=11, sector="taller", barrio="arucas", name="Talleres El Drago", seed=4242,
        day=today.isoformat(), form="sl", palette=5, emblem=5, size=2,
    )  # fmt: skip
    garage.company = sl.new_company(300_000, 300_000, 0)
    pool = [c for n in range(30) for c in staff.candidates(garage, today - timedelta(days=n))]
    for candidate in [c for c in pool if not c["part_time"]][:6]:
        _hire(garage, candidate, "indefinido", start=today - timedelta(days=10))
    s["sociedad_zec_puede"] = sociedad_state(
        CompanyView(garage, 240_000, 300_000, 0, 0.19, None, False)
    )
    zec = copy.deepcopy(garage)
    zec.company.update(zec=True, zec_misses=0, zec_since=today.isoformat(), ric_share=0.5)
    island = zec.barrio_info.island
    last = sl.settle_week(zec.company, 180_000, 420_000, day=sunday, zec=(6, island))
    zec.company["last"] = {**last, "week": (sunday - timedelta(days=6)).isoformat()}
    s["sociedad_zec"] = sociedad_state(
        CompanyView(zec, 330_000, 470_000, 90_000, 0.19, None, False)
    )
    s.update(_phase4(today))
    return s


def _hire(business: Business, candidate: dict[str, Any], contract: str, **kw: Any) -> dict:
    """Contrata a `candidate` con `new_employee`, como el cog (lo que pide, a su jornada).

    Con `role` y `horario` (9.41.0), en ese puesto y con ese horario.
    """
    from bot.services import business_staff as staff

    role = kw.pop("role", "")
    horario = staff.HORARIO_BY_KEY.get(kw.pop("horario", ""))
    jornada = staff.HALF_DAY if candidate["part_time"] else kw.pop("jornada", staff.FULL_DAY)
    if horario is not None:
        jornada = horario.jornada
    business.staff_seq += 1
    employee = staff.new_employee(
        candidate, seq=business.staff_seq, contract=contract, jornada=jornada,
        shift=kw.pop("shift", "manana"),
        wage=staff.role_ask(candidate["skills"], candidate["traits"], jornada, role),
        start=kw.pop("start"), role=role,
        franjas=horario.franjas if horario is not None else None,
    )  # fmt: skip
    business.staff.append(employee)
    return employee


def _puestos(today: date) -> dict[str, dict[str, Any]]:
    """Las muestras de los puestos y los horarios (9.41.0): todos los puestos, las dos páginas
    de una plantilla de siete, un cambio de horario pendiente, cuatro candidatos (con recursos
    humanos) y las ofertas de un puesto de apoyo y de un encargado con jornada partida."""
    from bot.services import business_staff as staff
    from bot.services.business_view import candidatos_state, plantilla_state

    s: dict[str, dict[str, Any]] = {}
    iso = today.isoformat()
    week = today - timedelta(days=6)
    shop = Business(
        user_id=30, sector="tienda", barrio="schamann", name="Ultramarinos Pino", seed=8080,
        reputation=74.0, day=iso, turns_today=1, size=2, palette=4, emblem=3,
        sales_today=12_900, clients_today=21,
    )  # fmt: skip
    pool = [c for n in range(120) for c in staff.candidates(shop, today - timedelta(days=n))]
    full = [c for c in pool if not c["part_time"]]
    team = [
        _hire(shop, full[0], "indefinido", start=week, horario="0123"),
        _hire(shop, full[1], "indefinido", start=week, role="encargado", horario="1256"),
        _hire(shop, full[2], "temporal", start=week, role="repartidor", horario="45"),
        _hire(shop, full[3], "indefinido", start=week, role="limpieza", horario="67"),
        _hire(shop, full[4], "indefinido", start=week, role="reponedor", horario="012"),
        _hire(shop, full[5], "indefinido", start=week, role="redes", horario="23"),
        _hire(shop, full[6], "indefinido", start=week, role=staff.MANAGER),
    ]
    team[0].update(grades=["A", "S"], grades_day=iso, led_day=iso)
    team[1].update(grades=["S"], grades_day=iso, humor=81.0)
    team[2].update(grades=["B"], grades_day=iso)
    team[3]["absent_day"] = iso
    shop.staff_today = {"turns": 4, "clients": 23, "sales": 15_800}
    s["plantilla_puestos"] = plantilla_state(shop, today, selected=team[1]["id"])
    s["plantilla_puestos_pagina_2"] = plantilla_state(shop, today, selected=team[6]["id"])
    s["negocio_con_puestos"] = negocio_state(shop, foot="Abrir gasta 1 franja. Te quedan 3.")

    cafe = Business(
        user_id=31, sector="cafeteria", barrio="vegueta", name="Cafetería La Tertulia",
        seed=9191, reputation=66.0, day=iso, size=1, palette=0, emblem=0,
    )  # fmt: skip
    pool = [c for n in range(120) for c in staff.candidates(cafe, today - timedelta(days=n))]
    full = [c for c in pool if not c["part_time"]]
    hr = _hire(cafe, full[0], "indefinido", start=week, role="rrhh", horario="0123")
    moved = _hire(cafe, full[1], "temporal", start=week, horario="012")
    staff.plan_reschedule(moved, staff.reschedule(moved, "0145", today))
    _hire(cafe, full[2], "indefinido", start=today, horario="4567")
    s["plantilla_rrhh_y_horario"] = plantilla_state(cafe, today, selected=moved["id"])
    hoy = staff.candidates(cafe, today)
    assert len(hoy) == staff.CANDIDATES_PER_DAY + staff.HR_CANDIDATES, hr
    pick = next(c for c in hoy if not c["part_time"])
    s["candidatos_cuatro_limpieza"] = candidatos_state(
        cafe, hoy, today, selected=pick["key"],
        offer={"contract": "indefinido", "role": "limpieza", "horario": "67"},
    )  # fmt: skip
    s["candidatos_encargado_partida"] = candidatos_state(
        shop, staff.candidates(shop, today), today,
        selected=staff.candidates(shop, today)[0]["key"],
        offer={"contract": "temporal", "role": "encargado", "horario": "1256"},
    )  # fmt: skip
    return s


def _phase4(today: date) -> dict[str, dict[str, Any]]:
    """Las muestras de la plantilla y los candidatos, con candidatos de verdad del día."""
    from bot.services import business_staff as staff
    from bot.services.business_view import candidatos_state, plantilla_state

    s: dict[str, dict[str, Any]] = {}
    cafe = Business(
        user_id=8, sector="cafeteria", barrio="triana", name="Cafetería La Tertulia",
        seed=12345, reputation=82.0, day=today.isoformat(), turns_today=2, size=2,
        sales_today=18_200, clients_today=16,
    )  # fmt: skip
    # Los candidatos de días pasados, para tener a quien contratar (el primero con un nombre
    # largo de verdad, para ver que cabe).
    pool = [c for n in range(400) for c in staff.candidates(cafe, today - timedelta(days=n))]
    long = next(c for c in pool if c["name"] == "Mari Carmen Bethencourt")
    full = [c for c in pool if not c["part_time"] and c is not long]
    student = next(c for c in pool if c["part_time"])
    week = today - timedelta(days=6)
    hires = [
        _hire(cafe, long, "indefinido", start=week),
        _hire(cafe, full[0], "temporal", start=today - timedelta(days=1), shift="tarde"),
        _hire(cafe, full[1], "b", start=week, shift="tarde"),
        _hire(cafe, student, "indefinido", start=today, shift="tarde"),
        _hire(cafe, full[2], "indefinido", start=week),
        _hire(cafe, full[3], "temporal", start=today - timedelta(days=2), shift="manana"),
    ]
    iso = today.isoformat()
    grades_today = (["A", "B"], ["S"], [], [], ["C", "D"], ["B"])
    for employee, grades in zip(hires, grades_today, strict=True):
        employee.update(grades=grades, grades_day=iso if grades else "")
    hires[2].update(humor=22.0, unpaid=3_400, absences=2)  # quemado, con avisos
    hires[3]["absent_day"] = iso
    hires[4].update(course="rapidez", humor=88.0)
    cafe.staff_today = {"turns": 6, "clients": 41, "sales": 24_600}
    cafe.wage_debt = 3_400
    s["plantilla_llena"] = plantilla_state(cafe, today, selected=hires[1]["id"])
    alone = Business(
        user_id=9, sector="taller", barrio="arucas", name="Talleres El Drago", seed=4242,
        day=today.isoformat(), palette=5, emblem=5,
    )  # fmt: skip
    _hire(alone, full[4], "temporal", start=today - timedelta(days=1))
    s["plantilla_uno"] = plantilla_state(alone, today)
    empty = Business(
        user_id=10, sector="heladeria", barrio="canteras", name="Heladería Dácil", seed=55,
        day=today.isoformat(), palette=1, emblem=3,
    )  # fmt: skip
    s["plantilla_vacia"] = plantilla_state(empty, today)
    # Los candidatos de un día con un parado de larga duración (bonificado) y un estudiante
    # (solo media jornada).
    day = next(
        today - timedelta(days=n)
        for n in range(400)
        if {c["profile"] for c in staff.candidates(empty, today - timedelta(days=n))} >= {"parado"}
        and any(c["part_time"] for c in staff.candidates(empty, today - timedelta(days=n)))
    )
    hoy = staff.candidates(empty, day)
    bonified = next(c for c in hoy if c["profile"] == "parado")
    s["candidatos_sin_oferta"] = candidatos_state(empty, hoy, day)
    s["candidatos_oferta"] = candidatos_state(
        empty, hoy, day, selected=bonified["key"],
        offer={"contract": "indefinido", "jornada": staff.FULL_DAY, "shift": "tarde"},
    )  # fmt: skip
    other = next(c for c in hoy if c is not bonified)
    s["candidatos_oferta_en_b"] = candidatos_state(
        cafe, hoy, day, selected=other["key"],
        offer={"contract": "b", "jornada": staff.HALF_DAY, "shift": "manana"},
    )  # fmt: skip
    s["negocio_con_plantilla"] = negocio_state(cafe, foot="Abrir gasta 1 franja. Te quedan 4.")
    s.update(_phase5(today))
    s.update(_puestos(today))
    return s


def _phase5(today: date) -> dict[str, dict[str, Any]]:
    """Las muestras de la cadena, la nave y vender la sociedad, con las funciones de verdad."""
    from bot.services import business_chain as chain
    from bot.services import business_sl as sl
    from bot.services import business_staff as staff
    from bot.services.business_view import (
        cadena_state,
        candidatos_state,
        nave_state,
        plantilla_state,
        venta_state,
    )
    from bot.services.negocio import CompanyView

    iso = today.isoformat()
    s: dict[str, dict[str, Any]] = {}

    def shop(n: int, sector: str, barrio: str, name: str, **kw: Any) -> Business:
        stock = {p.key: p.pack * 2 for p in SECTOR_BY_KEY[sector].products}
        return Business(
            user_id=20, sector=sector, barrio=barrio, name=name, seed=700 + n, day=iso,
            palette=n % 6, emblem=n % 5, id=n + 1, stock=stock, **kw,
        )  # fmt: skip

    cafe = shop(0, "cafeteria", "triana", "Cafetería La Tertulia", reputation=82.0, turns_today=2,
                sales_today=18_200, size=2)  # fmt: skip
    cafe.week_totals = {"ventas": 96_000, "compras": -38_000}
    pool = [c for n in range(60) for c in staff.candidates(cafe, today - timedelta(days=n))]
    full = [c for c in pool if not c["part_time"]]
    boss = _hire(cafe, full[0], "indefinido", start=today - timedelta(days=9))
    boss["role"] = staff.MANAGER
    for c in full[1:4]:
        _hire(cafe, c, "indefinido", start=today - timedelta(days=5))
    cafe.staff_today = {"turns": 4, "sales": 21_000}
    ice = shop(1, "heladeria", "canteras", "Heladería Dácil", sales_today=-1_200)
    ice.stock = {}  # sin nada: el aviso del local
    _hire(ice, full[4], "temporal", start=today - timedelta(days=2))
    # El nombre más largo que admite el bot (24), para ver que se recorta.
    garage = shop(2, "taller", "laguna", "Talleres Drago de Laguna", turns_today=1,
                  sales_today=9_600, rent_debt=7_800)  # fmt: skip
    hardware = shop(3, "ferreteria", "adeje", "Ferretería El Tornillo", sales_today=31_400,
                    turns_today=3, reputation=95.0)  # fmt: skip
    for c in full[5:7]:
        _hire(hardware, c, "indefinido", start=today - timedelta(days=4))

    nave = chain.Nave(user_id=20, barrio="schamann", size=1)
    nave.stock = {"cafe": 600, "cortado": 260, "conservas": 300, "limpieza": 40}
    nave.orders = [
        {"id": 1, "product": "bebidas", "units": 480, "arrives": iso},
        {"id": 2, "product": "cafe", "units": 800, "arrives": (today + timedelta(1)).isoformat()},
    ]
    nave.week_totals = {"compras": -54_000, "local": -6_200}
    nave.rent_debt = 0

    s["cadena_uno"] = cadena_state([cafe], None)
    s["cadena_dos_con_nave"] = cadena_state([cafe, ice], nave, selected=ice.id)
    s["cadena_cuatro_sin_nave"] = cadena_state([cafe, ice, garage, hardware], None)
    s["cadena_cuatro_con_nave"] = cadena_state(
        [hardware, garage, ice, cafe], nave, selected=garage.id
    )

    products = ["cafe", "cortado", "barraquito", "conservas", "limpieza", "bebidas"]
    s["nave_alquilar"] = nave_state(None, today, products=[], barrio="schamann", size=1)
    s["nave_alquilar_tenerife"] = nave_state(None, today, products=[], barrio="ofra", size=2)
    empty = chain.Nave(user_id=21, barrio="taco", size=0)
    s["nave_vacia"] = nave_state(empty, today, products=["tornillos"])
    s["nave_con_stock"] = nave_state(nave, today, products=products)
    s["nave_cotiza_bien"] = nave_state(nave, today, products=products, selected="conservas")
    # El café tostado lleva un AIEM del 15 %: de lo que menos ahorra.
    s["nave_cotiza_mal_aiem"] = nave_state(nave, today, products=products, selected="cafe")
    # Ningún producto sale hoy más caro que al mayorista: el rojo, a mano.
    worse = copy.deepcopy(s["nave_cotiza_mal_aiem"])
    worse["quote"].update(saving="−1.840 Y$", good=False, wholesale="23.868 Y$")
    s["nave_cotiza_pierde"] = worse
    debt = copy.deepcopy(nave)
    debt.rent_debt = 4_300
    s["nave_bloqueada"] = nave_state(debt, today, products=products, selected="zumo")
    full_nave = chain.Nave(user_id=22, barrio="laguna", size=0)
    full_nave.stock = {"pastillas": 100, "aceite": 120}
    s["nave_no_cabe"] = nave_state(full_nave, today, products=["aceite"], selected="aceite")

    company = copy.deepcopy(cafe)
    company.form = "sl"
    company.company = sl.new_company(300_000, 325_000, 0)
    company.company.update(results=[142_000, 168_000, 151_000], loan=50_000, retained=320_000)
    view = CompanyView(company, 412_000, 620_000, 168_000, 0.19, None, False,
                       locales=(company, ice))  # fmt: skip
    offer = chain.sale_offer(company.company, view.equity, weeks=sl.SALE_WEEKS, debts=0)
    s["venta_con_comprador"] = venta_state(view, offer)
    young = copy.deepcopy(company)
    young.company.update(results=[-12_000], loan=0)
    view = CompanyView(young, 80_000, 290_000, 0, 0.19, None, False, locales=(young,))
    offer = chain.sale_offer(young.company, view.equity, weeks=sl.SALE_WEEKS, debts=0)
    s["venta_sin_comprador"] = venta_state(view, offer)

    s["negocio_cadena_gerente"] = negocio_state(
        cafe, foot="Abrir gasta 1 franja. Te quedan 4.", chain_count=4
    )
    s["plantilla_con_gerente"] = plantilla_state(cafe, today, selected=boss["id"])
    hoy = staff.candidates(ice, today)
    s["candidatos_gerente"] = candidatos_state(
        ice, hoy, today, selected=hoy[0]["key"],
        offer={"contract": "indefinido", "jornada": staff.FULL_DAY, "shift": "manana",
               "role": staff.MANAGER},
    )  # fmt: skip
    s["dia_cadena"] = _dia(business=_business("cafeteria", more=3))
    s["dia_trasnochando"] = _dia(
        slots=_slots(*["hecha"] * 4, *["extra"] * 4, "extra", "negocio"),
        free="0 franjas libres",
        night_free="2 franjas de noche",
        battery=18,
        family=22,
        tip="Se acabó el día: te quedan 2 de noche. Trasnochando, la Inspección pilla más el B "
        "y tu familia lo nota.",
    )
    s["dia_guardia_de_noche"] = _dia(
        business=None,
        slots=_slots(*["hecha"] * 4, *["extra"] * 3, "guardia", "guardia"),
        free="0 franjas libres",
        night_free="3 franjas de noche",
        job={**JOB, "icon": "sanidad", "name": "Sanidad", "title": "Residente", "level": "5/10"},
        tip="La guardia empezó en la última franja del día y acaba trasnochando.",
    )
    return s


def contact_sheet(pngs: list[bytes], names: list[str]) -> bytes:
    """Una hoja con todas las muestras en miniatura y su nombre debajo."""
    from PIL import Image, ImageDraw

    cols, tw, th, label = 4, 500, 320, 26
    rows = (len(pngs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * (th + label)), "#222")
    draw = ImageDraw.Draw(sheet)
    for i, (png, name) in enumerate(zip(pngs, names, strict=True)):
        image = Image.open(io.BytesIO(png)).convert("RGB").resize((tw, th), Image.LANCZOS)
        x, y = (i % cols) * tw, (i // cols) * (th + label)
        sheet.paste(image, (x, y))
        draw.text((x + 6, y + th + 6), name, fill="#fff")
    buffer = io.BytesIO()
    sheet.save(buffer, format="PNG")
    return buffer.getvalue()


async def main(folder: Path) -> None:
    from bot.services.business_scene import BusinessScene

    folder.mkdir(parents=True, exist_ok=True)
    states = samples()
    scene = BusinessScene()
    try:
        await scene.paint([states["dia_curro_y_negocio"]])  # en caliente
        start = time.perf_counter()
        pngs = await scene.paint(list(states.values()))
        total = time.perf_counter() - start
        if pngs is None:
            raise SystemExit("Ni Node ni Chromium pueden pintar la escena")
        for name, state in states.items():  # una a una, para ver lo que tarda cada pantalla
            t0 = time.perf_counter()
            await scene.paint([state])
            print(f"{name:40s} {(time.perf_counter() - t0) * 1000:6.0f} ms")
    finally:
        await scene.close()
    for name, png in zip(states, pngs, strict=True):
        (folder / f"{name}.png").write_bytes(png)
    (folder / "hoja.png").write_bytes(contact_sheet(pngs, list(states)))
    print(f"{len(pngs)} pantallas en {total * 1000:.0f} ms (repartidas); en {folder}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    asyncio.run(main(Path(sys.argv[1])))
