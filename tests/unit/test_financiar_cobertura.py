"""Vigilante de 💳 Financiar: cada compra del bot tiene decidido si se financia o por qué no.

Recorre el código buscando lo que mueve dinero de una compra (`economy.purchase`,
`purchase_lines`, `housing` y `business`) y exige que cada función que lo hace esté en una de
las dos listas: `FINANCIADAS` (con dónde sale el botón) o `FUERA` (con el motivo, que ha de
ser una norma de la Biblia: lo único, el juego, impuestos y tasas, fianzas y depósitos, entre
miembros, crédito, ventas o cierres que no son compras). Una compra nueva sin decidir, o una
decisión que ya no tiene código, hace fallar la prueba.

Es texto, no AST: leer `src/` entero con `ast` cuesta ~3 s y esto, unas décimas.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
#: Las llamadas que mueven el dinero de una compra.
_CALL = re.compile(r"economy\.(purchase|purchase_lines|housing|business)\(")
#: Las funciones de módulo o de clase (las anidadas, como `prepare`, cuentan para la de fuera).
_DEF = re.compile(r"(?:    )?(?:async )?def (\w+)\(")

FINANCIADAS = {
    "bot/cogs/shop.py::complete_cart": "tienda: caja, ficha y carrito (lo único, al contado)",
    "bot/cogs/entrance.py::buy": "`entrada`: 🛒 Mejoras",
    "bot/cogs/gallos.py::train": "gallos: entrenamiento",
    "bot/services/pala.py::buy_training": "`pala`: formación",
    "bot/services/pala.py::_fly": "`pala`: vuelo a Hong Kong",
    "bot/services/pala.py::buy_coffee": "`pala`: café",
    "bot/services/housing_market.py::start_reform": "`pisos`: obras",
    "bot/services/housing_market.py::split": "`pisos`: divisiones",
    "bot/services/negocio.py::buy": "negocio de autónomo: mercancía",
    "bot/services/negocio.py::buy_equipment": "negocio de autónomo: mejoras del local",
    "bot/services/negocio.py::start_campaign": "negocio de autónomo: campañas",
    "bot/services/negocio.py::train": "negocio de autónomo: cursos de la plantilla",
    "bot/services/negocio.py::open": "negocio de autónomo: montarlo (solo el acondicionamiento)",
    "bot/services/negocio.py::_move": "negocio de autónomo: traslado (solo el acondicionamiento)",
}

_CREDITO = "crédito: no se financia un préstamo con otro"
_CIERRE = "cierre o turno: sueldos, ventas y cuotas, no una compra"
_ENTRE = "entre miembros: dos podrían inventarse una venta con dinero recién creado"
_SOCIEDAD = "lo paga la caja de una sociedad: tiene el préstamo de 🏪 Negocio"

FUERA = {
    # pisos
    "bot/services/housing_market.py::buy": "un inmueble: va con hipoteca",
    "bot/services/housing_market.py::vpo_buy": "un inmueble: va con hipoteca",
    "bot/services/housing_market.py::vpo_return": "devolver la VPO: se cobra",
    "bot/services/housing_market.py::sell": "una venta: se cobra",
    "bot/services/housing_market.py::accept_offer": "una venta al fondo: se cobra",
    "bot/services/housing_market.py::member_sale": _ENTRE,
    "bot/services/housing_market.py::rent_to_member": _ENTRE,
    "bot/services/housing_market.py::member_keys": _ENTRE,
    "bot/services/housing_market.py::offer_keys": "pagar a un inquilino por irse: no es compra",
    "bot/services/housing_market.py::accept_keys_ask": "pagar a un inquilino por irse",
    "bot/services/housing_market.py::bid": "una puja: es un depósito",
    "bot/services/housing_market.py::close_auctions": "cierre de subastas: depósitos",
    "bot/services/housing_market.py::buy_license": "la licencia vacacional: es una tasa",
    "bot/services/housing_market.py::found_socimi": "montar la SOCIMI: capital y trámites",
    "bot/services/housing_market.py::evict": "el desahucio: costas y tasas",
    "bot/services/housing_market.py::leave_home_paying": "penalización por irse antes",
    "bot/services/housing_market.py::vote": "junta de vecinos: derramas, no una compra",
    "bot/services/housing_market.py::close_votes": "junta de vecinos: derramas",
    "bot/services/housing_market.py::close_days": _CIERRE,
    "bot/services/housing_market.py::advance": _CREDITO,
    "bot/services/housing_market.py::amortize": _CREDITO,
    "bot/services/housing_market.py::desist_loan": _CREDITO,
    "bot/services/housing_market.py::finance": _CREDITO,
    "bot/services/housing_market.py::remortgage": _CREDITO,
    "bot/services/housing_market.py::repay_loan": _CREDITO,
    "bot/services/housing_market.py::take_loan": _CREDITO,
    # negocios
    "bot/services/negocio.py::constitute": "constituir la sociedad: capital y notaría",
    "bot/services/negocio.py::import_container": _SOCIEDAD,
    "bot/services/negocio.py::open_nave": _SOCIEDAD,
    "bot/services/negocio.py::from_nave": _SOCIEDAD,
    "bot/services/negocio.py::close_nave": "dejar la nave: se cobra",
    "bot/services/negocio.py::_evict_nave": "desahucio de la nave",
    "bot/services/negocio.py::_company_move": "dinero entre el dueño y su sociedad",
    "bot/services/negocio.py::sell_company": "una venta: se cobra",
    "bot/services/negocio.py::dismiss": "indemnización de un despido: no es compra",
    "bot/services/negocio.py::_close": _CIERRE,
    "bot/services/negocio.py::_close_night": _CIERRE,
    "bot/services/negocio.py::_close_nave_night": _CIERRE,
    "bot/services/negocio.py::_manage": _CIERRE,
    "bot/services/negocio.py::_staff_franja": _CIERRE,
    "bot/services/negocio.py::finish_turn": _CIERRE,
    "bot/services/negocio.py::repay_loan": _CREDITO,
    "bot/services/negocio.py::take_loan": _CREDITO,
}


@cache
def _sites() -> frozenset[str]:
    """`archivo::función` de cada compra del código (una sola pasada por `src/`)."""
    found = set()
    for path in (SRC / "bot").rglob("*.py"):
        relative = path.relative_to(SRC).as_posix()
        text = path.read_text(encoding="utf-8")
        if relative == "bot/services/economy.py" or not _CALL.search(text):
            continue
        function = None
        for line in text.splitlines():
            if (match := _DEF.match(line)) is not None:
                function = match.group(1)
            elif _CALL.search(line):
                found.add(f"{relative}::{function}")
    return frozenset(found)


def test_cada_compra_tiene_decidido_si_se_financia() -> None:
    undecided = sorted(_sites() - FINANCIADAS.keys() - FUERA.keys())
    assert not undecided, (
        "Compras sin decidir si ofrecen 💳 Financiar (Biblia, «Préstamos personales»): "
        f"añádelas a FINANCIADAS o a FUERA con su motivo: {undecided}"
    )


def test_no_quedan_decisiones_sin_codigo() -> None:
    assert not (FINANCIADAS.keys() & FUERA.keys())
    stale = sorted((FINANCIADAS.keys() | FUERA.keys()) - _sites())
    assert not stale, f"Ya no mueven dinero de una compra (quítalas de la lista): {stale}"
