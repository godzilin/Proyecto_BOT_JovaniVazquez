"""Pruebas de bot.services.housing_scene: el panel de `pisos` con canvas (Node, Chromium o Pillow).

Las de Node se saltan si no hay `node` o falta `@napi-rs/canvas` (`npm ci` en la
raíz). La comparación con Chromium se salta también si no hay navegador.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import io
import itertools
import json
import random
import time
from datetime import date
from typing import Any

import pytest
import pytest_asyncio
from PIL import Image, ImageChops, ImageStat
from test_node_scene import LOCAL_CHROMIUM, needs_node, node_ready, own_node

from bot.repositories.housing import Decision, Holding, Market, MemberDay, News, Review, Split
from bot.repositories.housing_finance import Mortgage, Promotion, VpoOwner
from bot.repositories.housing_junta import Vote
from bot.repositories.housing_trade import Auction
from bot.services import browser_scene, node_scene
from bot.services import housing as rules
from bot.services import housing_streets as streets
from bot.services import housing_view as view
from bot.services.browser_scene import BrowserScene
from bot.services.housing_catalog import (
    BARRIO_BY_KEY,
    BARRIO_NEWS,
    BARRIOS,
    KINDS,
    LISTING_BY_KEY,
    LISTINGS,
    REVIEW_LINES,
    TENANTS,
    THEMES,
)
from bot.services.housing_furniture import FURNITURE
from bot.services.housing_scene import SCENE, SCREEN_H, H, HousingScene, W

module_loop = pytest.mark.asyncio(loop_scope="module")
NO_CHROMIUM = "/no/existe/chromium"
NO_NODE = "/no/existe/node"
META = {"w": W, "h": H}


def size_of(state: dict[str, Any]) -> tuple[int, int]:
    """Lo que mide una pantalla: 1000 × 640, salvo el barrio (1000 × 760, diez tarjetas)."""
    return W, SCREEN_H.get(state["screen"], H)


#: Un inmueble de cada tipo, para que se dibujen todas las fachadas.
BY_KIND = {
    kind: next(item.key for item in LISTINGS if item.kind == kind)
    for kind in dict.fromkeys(item.kind for item in LISTINGS)
}
LONGEST = max(LISTINGS, key=lambda item: len(item.title))


def market() -> Market:
    """Un mercado con dueños, inquilinos al día, impagos, desahucios y okupas."""
    holdings = {
        "schamann_bajo": {
            10: Holding("schamann_bajo", 10, 60, 260000, 280000, 0),
            20: Holding("schamann_bajo", 20, 25, 1, 1, 0),
        },
        "vegueta_edificio": {30: Holding("vegueta_edificio", 30, 100, 1, 1, 0)},
        "adeje_chalet": {10: Holding("adeje_chalet", 10, 100, 900000, 960000, 0)},
    }
    leases = {
        "schamann_bajo": rules.Lease(
            status="alquilado", tenant="enfermeros", rent=4300, until=date(2026, 12, 14)
        ),
        "vegueta_edificio": rules.Lease(
            status="impago", tenant="aleman", rent=90000, arrears=12000
        ),
        "adeje_chalet": rules.Lease(status="vacio"),
    }
    # El usuario 10 tiene un poco de todo: la cartera llena (10 inmuebles).
    for position, item in enumerate(LISTINGS[:9]):
        if item.key in holdings:
            continue
        holdings[item.key] = {10: Holding(item.key, 10, 5 + 10 * position, 1, 1, 0)}
        status = ("alquilado", "vacio", "impago", "desahucio", "okupa")[position % 5]
        tenant = None if status in ("vacio", "okupa") else "familia"
        leases[item.key] = rules.Lease(
            status=status,
            tenant=tenant,
            rent=1500 * (position + 1),
            arrears=3000,
            eviction_at=date(2026, 11, 20) if status == "desahucio" else None,
        )
    # Un inmueble con muchos dueños (uno de nombre larguísimo) para la barra de dueños.
    crowded = holdings[LISTINGS[0].key]
    for owner, shares in ((40, 12), (50, 9), (60, 7)):
        crowded[owner] = Holding(LISTINGS[0].key, owner, shares, 1, 1, 0)
    return Market(
        indexes={"schamann": 1.04, "adeje": 0.97, "vegueta": 1.0, "canteras": 1.0003},
        holdings=holdings,
        leases=leases,
    )


NAMES = {
    10: "Ana",
    20: "Bea",
    30: "Perro Sanxe",
    40: "Jovani el implacable de Moncloa",
    50: "Carlos",
    60: "Diego",
}
NEWS = [
    News("schamann_bajo", date(2026, 10, 14), "firma", 0, None, "enfermeros"),
    News("schamann_bajo", date(2026, 10, 13), "averia", 3200, None, "enfermeros"),
    News("schamann_bajo", date(2026, 10, 12), "compra", 0, 20, None),
    News("schamann_bajo", date(2026, 10, 11), "vecino", 0, None, "enfermeros"),
]


def money_market() -> Market:
    """Hipotecas, vivienda, licencia, zona tensionada, oferta del fondo, VPO y una ejecución."""
    key = LONGEST.key
    return Market(
        indexes={"schamann": 1.15, LONGEST.barrio: 1.2, "vegueta": 1.1},
        holdings={
            "schamann_bajo": {10: Holding("schamann_bajo", 10, 100, 434000, 470000, 0)},
            key: {10: Holding(key, 10, 40, 250000, 280000, 0), 30: Holding(key, 30, 60, 1, 1, 0)},
            "vegueta_edificio": {10: Holding("vegueta_edificio", 10, 100, 900000, 960000, 0)},
            "adeje_chalet": {10: Holding("adeje_chalet", 10, 100, 900000, 960000, 0)},
        },
        leases={
            "schamann_bajo": rules.Lease(
                status="alquilado", member=20, bono=True, rent=4300, until=date(2026, 12, 14),
                license=True,
            ),
            key: rules.Lease(
                status="impago", tenant="enfermeros", rent=3800, until=date(2027, 1, 9),
                arrears=5200, eviction_at=date(2026, 11, 20), license=True, member=40, bono=True,
            ),
            "vegueta_edificio": rules.Lease(status="habitual"),
            "adeje_chalet": rules.Lease(status="vacio"),
        },
        mortgages=(
            Mortgage(1, 10, "schamann_bajo", 347200, 340000, 0.032, 1502, 350, 2, 3004, None),
            Mortgage(2, 10, key, 200000, 190000, 0.032, 900, 300, 0, 0, None),
            Mortgage(3, 10, "vpo:jinamar", 450000, 449000, 0.032, 1947, 359, 0, 0, "ico"),
            Mortgage(5, 10, "adeje_chalet", 800000, 790000, 0.032, 3400, 340, 0, 0, "ico"),
            Mortgage(4, 10, "vegueta_edificio", 700000, 690000, 0.032, 3000, 200, 1, 3000, None),
        ),
        vpo={
            "jinamar": VpoOwner("jinamar", 10, 450000, 0, 0, 0),
            "anaza": VpoOwner("anaza", 30, 430000, 0, 0, 0),
        },
        signups=(20, 40),
        reservations={"rehoyas": (20, date(2026, 10, 26)), "cardonal": (50, date(2026, 10, 26))},
        homes={20: ("alquiler", "schamann_bajo"), 10: ("vpo", "jinamar"),
               60: ("propia", "vegueta_edificio"), 40: ("alquiler", key)},
        euribor=0.0225,
        bank_debts={10: 25000},
        offers={
            ("schamann_bajo", 10): (520000, date(2026, 10, 26)),
            (key, 10): (900000, date(2026, 10, 26)),
        },
        socimi=frozenset({10}),
    )  # fmt: skip


def promotion_market() -> Market:
    """Schamann con su obra nueva: sus nueve inmuebles y la promoción en la décima tarjeta."""
    listing = rules.new_promotion(1, ["schamann"], random.Random(7))
    return Market(
        promotions={listing.key: Promotion(listing, 10, date(2026, 10, 19))},
        holdings={listing.key: {10: Holding(listing.key, 10, 40, 1, 1, 0)}},
    )


def bubble_market() -> Market:
    """Un barrio en burbuja, otro a precio de fondo buitre, uno tensionado y un chollo."""
    return Market(
        indexes={"vegueta": 1.45, "isleta": 0.8, "schamann": 1.07, "adeje": 1.31, "tafira": 0.86},
        holdings={"vegueta_edificio": {10: Holding("vegueta_edificio", 10, 50, 1, 1, 0)}},
        leases={"vegueta_edificio": rules.Lease(status="okupa")},
    )


def mood_samples() -> dict[str, dict[str, Any]]:
    """Las pegatinas con guasa: burbuja, buitre, zona tensionada y chollo."""
    m = bubble_market()
    return {
        "mapa_mercado": view.map_state(m, 10, "Ana", 120000),
        "barrio_burbuja": view.barrio_state(m, "vegueta", 10, NAMES),
        "barrio_buitre": view.barrio_state(m, "isleta", 10, NAMES),
        "barrio_tensionada": view.barrio_state(m, "schamann", 10, NAMES),
        "ficha_burbuja": view.listing_state(m, "vegueta_edificio", 10, NAMES, NEWS),
        "ficha_chollo_buitre": view.listing_state(m, "tafira_chalet", 10, NAMES, []),
        "ficha_chollo_tensionada": view.listing_state(m, "schamann_trastero", 10, NAMES, []),
    }


def money_samples() -> dict[str, dict[str, Any]]:
    """Las pantallas con lo de las fases 2 a 4: el peor caso de la ficha, la cartera y las VPO."""
    m, week = money_market(), MemberDay(30000, 1200, 4000)
    news4 = [News("schamann_bajo", date(2026, 10, 14), "firma_miembro", 4300, 20, None)] * 4
    full = view.portfolio_state(m, 10, "Ana", 120000, week, NEWS, NAMES)
    many = {**full, "items": (full["items"] * 3)[:10]}
    mine_only = Market(vpo=m.vpo, homes={30: ("vpo", "anaza")}, euribor=0.0225)
    return {
        "ficha_miembro": view.listing_state(m, "schamann_bajo", 10, NAMES, news4),
        "ficha_peor": view.listing_state(m, LONGEST.key, 10, NAMES, NEWS),
        "ficha_vive_dueno": view.listing_state(m, "vegueta_edificio", 10, NAMES, NEWS),
        "ficha_aval": view.listing_state(m, "adeje_chalet", 10, NAMES, []),
        "ficha_de_otro": view.listing_state(m, "schamann_bajo", 40, NAMES, NEWS),
        "cartera_finanzas": full,
        "cartera_diez": many,
        "cartera_solo_vpo": view.portfolio_state(
            mine_only, 30, "Perro Sanxe", 900, MemberDay(), [], NAMES
        ),
        "cartera_ejecutada": view.portfolio_state(m, 70, "Luis", 50, MemberDay(), [], NAMES)
        | {"bank_debt": "25.000 Y$", "debt": "25.000 Y$"},
        # Más de 10: la página que toca y «Página 2 de 3» abajo, con y sin «Última hora».
        "cartera_paginada": many
        | {"page": 2, "pages": 3, "items": [i | {"mortgage": ""} for i in many["items"]]},
        "cartera_paginada_sin_noticias": many | {"page": 3, "pages": 3, "news": []},
        "cartera_paginada_corta": {**full, "page": 3, "pages": 3},
        "vpo_apuntada": view.vpo_state(
            m, 20, NAMES, eligible=True, reason="", next_draw="lunes 19/10", income=6000
        ),
        "vpo_no": view.vpo_state(
            m, 99, NAMES, eligible=False, reason="Ganas demasiado para una VPO",
            next_draw="lunes 19/10", income=9000,
        ),
        "vpo_con_casa": view.vpo_state(
            m, 10, NAMES, eligible=False, reason="Ya tienes vivienda: una VPO es para vivir",
            next_draw="lunes 19/10", income=2000,
        ),
    }  # fmt: skip


#: Un barrio de cada tema, para ver todos los tipos de inmueble con el paisaje de cada uno.
THEME_BARRIO = {
    "marinero": "isleta",
    "historico": "vegueta",
    "centro": "triana",
    "obrero": "schamann",
    "playa": "canteras",
    "lujo": "mogan",
    "rural": "tafira",
}
#: Los 20 tipos (con la habitación de 9.12.0) de inmueble y el solar de la obra nueva.
FACADE_KINDS = (*KINDS, "solar")
#: Dónde pinta la ficha la fachada (columna A): (izquierda, arriba, derecha, abajo).
FACADE_BOX = (16, 100, 16 + 296, 100 + 168)


def facade_state(kind: str, barrio: str, key: str | None = None) -> dict[str, Any]:
    """Una ficha con la fachada de `kind` en `barrio`, sin el cartel de «Se vende»."""
    base = view.listing_state(Market(), "schamann_bajo", 10, NAMES, [])
    return {
        **base,
        "kind": kind,
        "key": key or (f"solar_{barrio}" if kind == "solar" else f"{barrio}_{kind}"),
        "barrio_key": barrio,
        "theme": BARRIO_BY_KEY[barrio].theme,
        "free": 0,
        "owners": [],
    }


def facade_samples() -> dict[str, dict[str, Any]]:
    """Cada tipo en un tema distinto (dándoles la vuelta): para Node contra Chromium."""
    themes = list(THEME_BARRIO.values())
    return {
        f"fachada_{kind}": facade_state(kind, themes[n % len(themes)])
        for n, kind in enumerate(FACADE_KINDS)
    }


#: Hoy, para los candidatos y las noticias de barrio de las muestras de la fase 2.
TODAY = date(2026, 10, 15)
PISO, LOCAL = BY_KIND["piso2"], BY_KIND["local"]
LONGEST_NEWS = max(BARRIO_NEWS, key=lambda n: len(n.title) + len(n.blurb))
LONGEST_LINE = max(TENANTS, key=lambda t: len(t.line))
BUSINESSES = [t.key for t in TENANTS if "local" in t.kinds][:3]


def waiting(*tenants: str) -> tuple[tuple[str, date], ...]:
    """Candidatos que esperan: el primero hasta mañana, los demás unos días más."""
    return tuple((t, date(2026, 10, 16 + 3 * n)) for n, t in enumerate(tenants))


def phase2_market(
    leases: dict[str, rules.Lease],
    candidates: dict[str, tuple[tuple[str, date], ...]] | None = None,
    *,
    news: bool = True,
) -> Market:
    """Pisos de `user 10` con los contratos dados, candidatos y noticias de barrio (o sin ellas)."""
    holdings = {key: {10: Holding(key, 10, 100, 1, 1, 0)} for key in leases}
    if LONGEST.key in holdings:  # el que decide es otro, con un nombre larguísimo
        holdings[LONGEST.key] = {
            10: Holding(LONGEST.key, 10, 30, 1, 1, 0),
            40: Holding(LONGEST.key, 40, 70, 1, 1, 0),
        }
    span = (date(2026, 10, 12), date(2026, 10, 20))
    barrio_news = (
        {
            "isleta": (LONGEST_NEWS.key, *span),
            "schamann": (LONGEST_NEWS.key, *span),
            "telde": (BARRIO_NEWS[0].key, *span),
            "vegueta": (BARRIO_NEWS[1].key, *span),
            LONGEST.barrio: (LONGEST_NEWS.key, *span),
        }
        if news
        else {}
    )
    return Market(
        indexes={"isleta": 1.2, "schamann": 1.15, LONGEST.barrio: 1.2},
        holdings=holdings,
        leases=leases,
        candidates=candidates or {},
        barrio_news=barrio_news,
        socimi=frozenset({10}),
    )


def phase2_samples() -> dict[str, dict[str, Any]]:
    """Las pantallas nuevas (gestión y candidatos) y las claves nuevas de las de siempre."""
    vacio_yo = rules.Lease(status="vacio", pick="yo", level="mercado", insured=True)
    m = phase2_market(
        {
            PISO: vacio_yo,
            LOCAL: rules.Lease(status="vacio"),
            LONGEST.key: rules.Lease(status="vacio", pick="yo", insured=True, last_rent=5000),
            "schamann_bajo": rules.Lease(
                status="alquilado",
                tenant="abuela",
                rent=4300,
                until=date(2026, 12, 14),
                insured=True,
            ),
            "schamann_piso2": rules.Lease(
                status="impago",
                tenant="cripto",
                rent=5200,
                until=date(2026, 12, 14),
                insured=True,
                arrears=2000,
            ),
            "vegueta_edificio": rules.Lease(
                status="alquilado", tenant="policia", rent=90000, until=date(2026, 12, 14)
            ),
        },
        {
            PISO: waiting("enfermeros", "estudiantes", "abuela"),
            LONGEST.key: waiting(LONGEST_LINE.key, "policia", "britanicos"),
        },
    )
    bare = phase2_market({PISO: rules.Lease(status="vacio")}, news=False)
    empty = phase2_market({PISO: rules.Lease(status="vacio", pick="yo")}, news=False)
    one = phase2_market(
        {PISO: rules.Lease(status="vacio", pick="yo")}, {PISO: waiting(LONGEST_LINE.key)}
    )
    shop = phase2_market(
        {LOCAL: rules.Lease(status="vacio", pick="yo")}, {LOCAL: waiting(*BUSINESSES)}
    )

    def lineup(*tenants: str) -> Market:
        lease = rules.Lease(status="vacio", pick="yo")
        return phase2_market({PISO: lease}, {PISO: waiting(*tenants)}, news=False)

    week = MemberDay(30000, 1200, 4000)
    gestion = {
        "gestion_seguro_candidatos": view.gestion_state(m, PISO, 10, NAMES, TODAY),
        "gestion_sin_seguro": view.gestion_state(bare, PISO, 10, NAMES, TODAY),
        "gestion_cubierto": view.gestion_state(m, "schamann_bajo", 10, NAMES, TODAY),
        "gestion_no_cubre": view.gestion_state(m, "schamann_piso2", 10, NAMES, TODAY),
        "gestion_negocio": view.gestion_state(shop, LOCAL, 10, NAMES, TODAY),
        "gestion_larga": view.gestion_state(m, LONGEST.key, 10, NAMES, TODAY),
    }
    candidatos = {
        "candidatos_tres": view.candidates_state(m, PISO, TODAY),
        "candidatos_uno": view.candidates_state(one, PISO, TODAY),
        "candidatos_vacia": view.candidates_state(empty, PISO, TODAY),
        "candidatos_frase_larga": view.candidates_state(m, LONGEST.key, TODAY),
        "candidatos_negocios": view.candidates_state(shop, LOCAL, TODAY),
        "candidatos_uniformes": view.candidates_state(
            lineup("policia", "bombera", "piloto"), PISO, TODAY
        ),
        "candidatos_oficios": view.candidates_state(
            lineup("cajera", "guagua", "directivo"), PISO, TODAY
        ),
        "candidatos_gente": view.candidates_state(
            lineup("abuela", "estudiantes", "britanicos"), PISO, TODAY
        ),
    }
    cartera = view.portfolio_state(m, 10, "Ana", 120000, week, NEWS, NAMES, today=TODAY)
    others = {
        "ficha_candidatos_noticia": view.listing_state(m, PISO, 10, NAMES, [], TODAY),
        "ficha_inquilino_cubierto": view.listing_state(m, "schamann_bajo", 10, NAMES, NEWS, TODAY),
        "ficha_inquilino_no_cubre": view.listing_state(m, "schamann_piso2", 10, NAMES, [], TODAY),
        "ficha_inquilino_sin_seguro": view.listing_state(
            m, "vegueta_edificio", 10, NAMES, [], TODAY
        ),
        "ficha_candidatos_peor": view.listing_state(m, LONGEST.key, 10, NAMES, NEWS, TODAY),
        "barrio_noticia": view.barrio_state(m, "isleta", 10, NAMES, TODAY),
        "barrio_noticia_corta": view.barrio_state(m, "vegueta", 10, NAMES, TODAY),
        "mapa_noticias": view.map_state(m, 10, "Ana", 120000, TODAY),
        "cartera_seguro_candidatos": cartera,
        "cartera_tres_pastillas": cartera | {"bank_debt": "25.000 Y$"},
    }
    return {**gestion, **candidatos, **others}


NOW = 1_760_000_000.0
LONGEST_COMPLAINT = max(TENANTS, key=lambda t: len(t.complaint))


def auction(number: int, key: str, **kwargs: Any) -> Auction:
    """Una subasta de muestra: del vendedor 20, sin pujas y con tres horas por delante."""
    fields: dict[str, Any] = {
        "id": number, "listing": key, "seller": 20, "shares": 25, "min_bid": 40000,
        "ends_at": NOW + 3 * 3600 + 300, "judicial": False, "mortgage_id": None,
        "best_bid": 0, "best_bidder": None, "escrow": 0, "bids": 0, "created_at": NOW - 3600,
    }  # fmt: skip
    return Auction(**{**fields, **kwargs})


def phase34_market(
    leases: dict[str, rules.Lease],
    *,
    reforms: dict[str, dict[str, tuple[int, date, bool, bool]]] | None = None,
    decisions: dict[str, tuple[Decision, ...]] | None = None,
    candidates: dict[str, tuple[tuple[str, date], ...]] | None = None,
) -> Market:
    """Pisos del usuario 10 (enteros) con sus reformas e imprevistos."""
    market = phase2_market(leases, candidates)
    return dataclasses.replace(market, reforms=reforms or {}, decisions=decisions or {})


def problem(number: int, key: str, event: str, cost: int, choice: str | None = None) -> Decision:
    return Decision(number, key, event, cost, date(2026, 10, 14), date(2026, 10, 17), choice)


def phase34_samples() -> dict[str, dict[str, Any]]:
    """Subastas, reformas, imprevistos y lo nuevo de la ficha (humor, obras, reformado)."""
    keys = [item.key for item in LISTINGS]
    many = Market(
        auctions=(
            auction(1, keys[0], best_bid=41000, best_bidder=30, bids=3, seller=10),
            auction(2, LONGEST.key, judicial=True, seller=40, shares=100, min_bid=900000,
                    best_bid=1_234_000, best_bidder=40, bids=11, ends_at=NOW + 1500),
            auction(3, keys[3], best_bid=52000, best_bidder=10, bids=2, shares=40),
            auction(4, LOCAL, min_bid=15000, ends_at=NOW + 20 * 3600),
            auction(5, keys[5], judicial=True, seller=30, shares=60, best_bid=70000,
                    best_bidder=20, bids=1),
            auction(6, BY_KIND["chalet"], best_bid=99000, best_bidder=60, bids=7, seller=50),
            auction(7, keys[8]),
            auction(8, keys[9]),
        ),
    )  # fmt: skip
    pair = dataclasses.replace(many, auctions=many.auctions[1:3])
    one = dataclasses.replace(many, auctions=many.auctions[2:3])
    subastas = {
        "subastas_seis_y_mas": view.auctions_state(many, 10, NAMES, NOW, 2),
        "subastas_judicial_elegida": view.auctions_state(pair, 10, NAMES, NOW, 2),
        "subastas_voy_ganando": view.auctions_state(one, 10, NAMES, NOW, None),
        "subastas_vacia": view.auctions_state(Market(), 10, NAMES, NOW),
    }
    day = date(2026, 10, 10)
    done = {
        PISO: {
            "pintura": (7000, day, True, False),
            "cocina": (9000, day, True, False),
            "bano": (6000, date(2026, 10, 25), False, False),
        }
    }
    reformada = {PISO: {"integral": (80000, day, True, True), "energia": (40000, day, True, False)}}
    tenant = rules.Lease(status="alquilado", tenant="enfermeros", rent=4300, mood=85)
    obras = rules.Lease(status="obras", works_until=date(2026, 10, 25))
    vacio = rules.Lease(status="vacio")
    clean = phase34_market({PISO: vacio, LOCAL: vacio})
    reformas = {
        "reformas_nada": view.reforms_state(clean, PISO, 10),
        "reformas_en_obras": view.reforms_state(
            phase34_market({PISO: obras}, reforms=done), PISO, 10
        ),
        "reformas_rehabilitado": view.reforms_state(
            phase34_market({PISO: vacio}, reforms=reformada), PISO, 10
        ),
        "reformas_local": view.reforms_state(clean, LOCAL, 10),
        "reformas_no_entero": view.reforms_state(
            phase34_market({LONGEST.key: vacio}), LONGEST.key, 10
        ),
        "reformas_no_vacio": view.reforms_state(phase34_market({PISO: tenant}), PISO, 10),
    }
    problems = {
        PISO: (
            problem(1, PISO, "averia", 320_000),
            problem(2, PISO, "humedades", 80_000),
            problem(3, PISO, "vecino", 0),
        ),
        LONGEST.key: (problem(4, LONGEST.key, "cerradura", 45_000),),
        LOCAL: (problem(5, LOCAL, "averia", 15_000, "cunado"),),
    }
    angry = rules.Lease(status="alquilado", tenant=LONGEST_COMPLAINT.key, rent=5000, mood=12)
    pending = phase34_market({PISO: tenant, LONGEST.key: angry, LOCAL: vacio}, decisions=problems)
    imprevistos = {
        "imprevistos_tres": view.decisions_state(pending, PISO, 10),
        "imprevistos_una_elegida": view.decisions_state(pending, LOCAL, 10),
        "imprevistos_larga_ajeno": view.decisions_state(pending, LONGEST.key, 10),
        "imprevistos_vacia": view.decisions_state(clean, PISO, 10),
    }
    full = phase34_market(
        {PISO: tenant},
        reforms=reformada,
        decisions={PISO: problems[PISO][:2]},
        candidates={PISO: waiting("estudiantes", "abuela")},
    )
    upset = phase34_market({LONGEST.key: angry}, decisions={LONGEST.key: problems[LONGEST.key]})
    fresh = phase34_market(
        {PISO: rules.Lease(status="vacio", pick="yo")},
        reforms=reformada,
        candidates={PISO: waiting("policia", "britanicos", "abuela")},
    )
    working = phase34_market({PISO: obras}, reforms=done)
    fichas = {
        "ficha_humor_imprevistos": view.listing_state(full, PISO, 10, NAMES, NEWS, TODAY),
        "ficha_harto_un_imprevisto": view.listing_state(upset, LONGEST.key, 10, NAMES, NEWS, TODAY),
        "ficha_en_obras": view.listing_state(working, PISO, 10, NAMES, [], TODAY),
        "ficha_reformada": view.listing_state(fresh, PISO, 10, NAMES, NEWS, TODAY),
    }
    return {**subastas, **reformas, **imprevistos, **fichas}


ALL_FURNITURE = frozenset(f.key for f in FURNITURE)
TOURISTS = [t.key for t in TENANTS if "vacacional" in t.levels]
LONGEST_GUEST = max(TOURISTS, key=lambda k: len(next(t.name for t in TENANTS if t.key == k)))


def longest_line(stars: int) -> int:
    lines = REVIEW_LINES[stars]
    return max(range(len(lines)), key=lambda i: len(lines[i]))


def stay(day: int, guest: str, stars: int, line: int | None = None) -> Review:
    """Una reseña de octubre de 2026 (`line` por defecto, la más larga de esas estrellas)."""
    return Review(date(2026, 10, day), guest, stars, longest_line(stars) if line is None else line)


def phase5_market(
    leases: dict[str, rules.Lease], reviews: dict[str, tuple[Review, ...]] | None = None
) -> Market:
    """Inmuebles del usuario 10 (enteros) con sus contratos vacacionales y reseñas."""
    return dataclasses.replace(phase34_market(leases), reviews=reviews or {})


def phase5_samples() -> dict[str, dict[str, Any]]:
    """Reseñas del vacacional y lo nuevo de la ficha (nota, Superanfitrión y licencia)."""
    chalet = BY_KIND["chalet"]
    until = date(2031, 3, 2)

    def holiday(rating: float, count: int, **kwargs: Any) -> rules.Lease:
        fields: dict[str, Any] = {
            "status": "alquilado", "tenant": TOURISTS[0], "rent": 9000, "level": "vacacional",
            "license": True, "license_until": until, "rating": rating, "reviews": count,
        }  # fmt: skip
        return rules.Lease(**{**fields, **kwargs})

    stars = (5, 5, 4, 5, 5)
    top = tuple(stay(14 - i, TOURISTS[i % len(TOURISTS)], n, i % 5) for i, n in enumerate(stars))
    low = tuple(stay(14 - i, LONGEST_GUEST, n) for i, n in enumerate((1, 2, 1, 3, 2)))
    market = phase5_market(
        {
            PISO: holiday(4.8, 12),
            LONGEST.key: holiday(2.1, 6),
            chalet: holiday(0.0, 0, status="vacio", tenant=None),
            LOCAL: rules.Lease(status="vacio"),
        },
        {PISO: top, LONGEST.key: low},
    )
    some = ALL_FURNITURE - {"mueble_aire", "mueble_playa", "mueble_juegos"}
    one = Market(
        reviews={PISO: top[:1]},
        leases={PISO: holiday(4.0, 1)},
        holdings=market.holdings,
        indexes=market.indexes,
    )
    resenas = {
        "resenas_superanfitrion": view.reviews_state(market, PISO, ALL_FURNITURE),
        "resenas_nota_baja_larga": view.reviews_state(market, LONGEST.key, frozenset()),
        "resenas_algunos_muebles": view.reviews_state(market, PISO, some),
        "resenas_una": view.reviews_state(one, PISO, frozenset({"mueble_cuna"})),
        "resenas_licencia_sin_resenas": view.reviews_state(market, chalet, some),
        "resenas_sin_licencia": view.reviews_state(market, LOCAL, frozenset()),
    }
    fichas = {
        "ficha_nota_superanfitrion": view.listing_state(market, PISO, 10, NAMES, NEWS, TODAY),
        "ficha_nota_baja_larga": view.listing_state(market, LONGEST.key, 10, NAMES, NEWS, TODAY),
        "ficha_vacacional_sin_resenas": view.listing_state(market, chalet, 10, NAMES, [], TODAY),
    }
    return {**resenas, **fichas}


def phase6_samples() -> dict[str, dict[str, Any]]:
    """La junta de copropietarios, la acción de división y su subasta."""
    keys = [item.key for item in LISTINGS]
    (a, b, c, d, e) = (keys[0], BY_KIND["chalet"], LONGEST.key, PISO, keys[4])

    def holds(key: str, shares: dict[int, int]) -> dict[int, Holding]:
        return {m: Holding(key, m, s, 1, 1, 0) for m, s in shares.items()}

    def vote(
        number: int, key: str, kind: str, value: str, ballots: dict[int, bool], **kw: Any
    ) -> Vote:
        fields: dict[str, Any] = {
            "id": number, "listing": key, "proposer": 20, "kind": kind, "value": value,
            "amount": 0, "ends_at": NOW + 3 * 3600 + 300, "ballots": ballots,
        }  # fmt: skip
        return Vote(**{**fields, **kw})

    holdings = {
        a: holds(a, {10: 40, 20: 35, 30: 25}),
        b: holds(b, {10: 50, 20: 50}),
        c: holds(c, {40: 30, 10: 28, 50: 22, 60: 20}),
        d: holds(d, {10: 55, 20: 45}),
        e: holds(e, {10: 60, 20: 40}),
    }
    votes = (
        vote(1, a, "renta", "mercado", {20: True, 10: True}),
        vote(2, a, "reforma", "integral", {20: True, 30: False}, proposer=30),
        vote(3, a, "venta", "", {20: True, 10: True, 30: False}, ends_at=NOW + 25 * 60),
        vote(4, a, "derrama", "", {20: True}, amount=45000),
        vote(5, b, "seguro", "1", {}, proposer=10),
        vote(6, c, "administra", "40", {40: True, 10: False, 50: True}, proposer=40),
        vote(7, e, "licencia", "", {20: True, 10: False}),
    )
    market = Market(
        holdings=holdings,
        votes=votes,
        admins={c: 40, a: 20},
        reserves={a: 1_240_000, c: 987_654_321},
    )
    division = auction(1, e, seller=20, shares=100, min_bid=2_400_000, ends_at=NOW + 70 * 3600)
    division = dataclasses.replace(division, division=True)
    split = dataclasses.replace(
        market,
        auctions=(dataclasses.replace(division, best_bid=2_700_000, best_bidder=30, bids=2),),
    )
    mixed = dataclasses.replace(
        market,
        auctions=(
            division,
            auction(2, LONGEST.key, judicial=True, seller=40, shares=100, best_bid=1_234_000,
                    best_bidder=40, bids=11),
            auction(3, keys[3], best_bid=52000, best_bidder=10, shares=40),
        ),
    )  # fmt: skip
    return {
        "junta_cuatro_votaciones": view.junta_state(market, a, 10, NAMES, NOW),
        "junta_vacia": view.junta_state(market, d, 10, NAMES, NOW),
        "junta_duenos_largos": view.junta_state(market, c, 10, NAMES, NOW),
        "junta_division": view.junta_state(split, e, 10, NAMES, NOW),
        "junta_division_sin_pujas": view.junta_state(mixed, e, 20, NAMES, NOW),
        "junta_sin_mayoria": view.junta_state(market, b, 30, NAMES, NOW),
        "subastas_division": view.auctions_state(mixed, 10, NAMES, NOW, 1),
    }


#: Los inmuebles divididos de las muestras de 9.12.0: un edificio por plantas y dos pisos que se
#: alquilan por habitaciones (uno con seis, las que más caben).
EDIFICIO, PISO3, CASONA = "triana_edificio", "isleta_piso3", "vegueta_casona"


def split_market() -> Market:
    """Un edificio en 4 plantas (dos con dueño, una alquilada) y dos pisos por habitaciones."""
    floor = [rules.unit_key(EDIFICIO, rules.FLOORS, n) for n in (1, 2, 3)]
    room = rules.unit_key(PISO3, rules.ROOMS, 1)
    return Market(
        splits={
            EDIFICIO: Split(rules.FLOORS, 4, 1_200_000),
            PISO3: Split(rules.ROOMS, 3, 120_000),
            CASONA: Split(rules.ROOMS, 6, 300_000),
        },
        holdings={
            floor[0]: {10: Holding(floor[0], 10, 100, 1, 1, 0)},
            floor[1]: {m: Holding(floor[1], m, s, 1, 1, 0) for m, s in ((20, 60), (10, 40))},
            PISO3: {10: Holding(PISO3, 10, 100, 1, 1, 0)},
            CASONA: {10: Holding(CASONA, 10, 100, 1, 1, 0)},
        },
        leases={
            floor[0]: rules.Lease(status="alquilado", tenant="familia", rent=21000),
            floor[2]: rules.Lease(status="okupa"),
            PISO3: rules.Lease(status=rules.BY_ROOMS),
            CASONA: rules.Lease(status=rules.BY_ROOMS),
            room: rules.Lease(status="alquilado", tenant="universitaria", rent=4100),
        },
    )


def split_samples() -> dict[str, dict[str, Any]]:
    """🏢 Las plantas y 🛏️ las habitaciones, y dividir: con dos opciones, con aviso y sin ninguna."""
    market = split_market()
    whole = Market(holdings={CASONA: {10: Holding(CASONA, 10, 100, 1, 1, 0)}})
    trastero = BY_KIND["trastero"]
    return {
        "plantas_edificio": view.units_state(market, EDIFICIO, 10, NAMES),
        "plantas_habitaciones": view.units_state(market, PISO3, 10, NAMES, TODAY),
        "plantas_seis_habitaciones": view.units_state(market, CASONA, 10, NAMES),
        "barrio_dividido": view.barrio_state(market, "triana", 10, NAMES),
        "barrio_por_habitaciones": view.barrio_state(market, "isleta", 10, NAMES),
        "dividir_dos": view.split_state(whole, CASONA, 10),
        "dividir_sin_dueno": view.split_state(Market(), EDIFICIO, 10),
        "dividir_por_habitaciones": view.split_state(market, PISO3, 10),
        "dividir_no_se_puede": view.split_state(Market(), trastero, 10),
    }


def tallest() -> streets.Building:
    """El edificio con más unidades del catálogo (un bloque de 9 plantas con su sótano)."""
    return max(streets.BUILDINGS, key=lambda b: len(view.building_units(Market(), b)))


def street_market() -> Market:
    """Triana, calle 1: en el bloque del 16, una tuya alquilada, otra vacía con candidatos y una
    de otro que no paga; en el aparcamiento, una plaza tuya."""
    block = [u.key for u in view.building_units(Market(), streets.BUILDING_BY_KEY["triana_e2"])]
    plaza = view.building_units(Market(), streets.BUILDING_BY_KEY["triana_e1"])[0].key
    mine, empty, other = block[0], block[2], block[4]
    return Market(
        holdings={
            mine: {10: Holding(mine, 10, 100, 1, 1, 0)},
            empty: {10: Holding(empty, 10, 60, 1, 1, 0), 30: Holding(empty, 30, 40, 1, 1, 0)},
            other: {30: Holding(other, 30, 100, 1, 1, 0)},
            plaza: {10: Holding(plaza, 10, 100, 1, 1, 0)},
        },
        leases={
            mine: rules.Lease(status="alquilado", tenant="familia", rent=21000),
            empty: rules.Lease(status="vacio", pick="yo"),
            other: rules.Lease(status="impago", tenant="enfermeros", rent=9000, arrears=9000),
        },
        candidates={empty: waiting("enfermeros", "familia", "universitaria")},
    )


def street_samples() -> dict[str, dict[str, Any]]:
    """🗺️ El plano de las calles (centro y rural) y 🏢 el corte de cada tipo de edificio."""
    empty = Market()
    casa = next(
        b.key
        for b in streets.BUILDINGS
        if b.kind == streets.CASA and b.featured and empty.listing(b.featured).kind == "chalet"
    )
    corte = {
        "bloque": "triana_e2",
        "alto": tallest().key,
        "aparcamiento": "triana_e1",
        "pantalan": "mogan_e4",
        "casa": casa,
        "casa_terrera": "isleta_e5",
        "entero": "triana_e9",
        "complejo": "maspalomas_e4",
        "poligono": "telde_e4",
        "guardamuebles": "isleta_e7",
    }
    out = {
        "calles_triana": view.streets_state(street_market(), "triana", 10),
        "calles_rural": view.streets_state(empty, "tafira", 10),
        "calles_vegueta": view.streets_state(empty, "vegueta", 10),
        "calles_playa": view.streets_state(empty, "mogan", 10),
        # cinco por calle, la noticia más larga y la pegatina del índice disparado
        "calles_noticia": view.streets_state(
            Market(
                indexes={"schamann": 1.2},
                barrio_news={
                    "schamann": (LONGEST_NEWS.key, date(2026, 10, 12), date(2026, 10, 20))
                },
            ),
            "schamann",
            10,
            TODAY,
        ),
        "edificio_tuyas": view.building_state(street_market(), "triana_e2", 10, NAMES, TODAY),
        "edificio_plaza_tuya": view.building_state(street_market(), "triana_e1", 10, NAMES, TODAY),
    }
    out.update(
        {
            f"edificio_{name}": view.building_state(empty, key, 10, NAMES)
            for name, key in corte.items()
        }
    )
    return out


#: Un bloque de cada tema (con su sobreático `_sa` o su ático con vistas `_va` si lo tiene) y dos
#: del mismo barrio que se parecen en todo (plantas y unidades) salvo en la clave.
THEME_BLOCKS = {
    "centro": "triana_e7",
    "historico": "vegueta_e6",
    "obrero": "schamann_e2",
    "playa": "canteras_e1",
    "marinero": "isleta_e2",
    "lujo": "mogan_e2",
    "rural": "arucas_e9",
}
TWIN_BLOCKS = ("schamann_e2", "schamann_e3")
#: Las de un bloque de Schamann, una en cada uso: lo que se hace en ella se ve en su dibujo.
STATUS_BLOCK = "schamann_e4"
STATUSES = (
    "alquilado", "vacio", "obras", "okupa", "habitual", "por_habitaciones", "impago",
    "desahucio", "venta", "alquilado", "negocio", "vacio", "alquilado",
)  # fmt: skip


def status_market() -> Market:
    """El bloque de `STATUS_BLOCK` con una unidad en cada uso de `STATUSES`, por orden."""
    units = [u.key for u in view.building_units(Market(), streets.BUILDING_BY_KEY[STATUS_BLOCK])]
    holdings, leases = {}, {}
    for key, status in zip(units, STATUSES, strict=True):
        if status == "venta":
            continue
        holdings[key] = {10: Holding(key, 10, 100, 1, 1, 0)}
        if status != "vacio" or key != units[1]:  # una vacía sin contrato y otra con él
            tenant = "familia" if status in ("alquilado", "impago") else None
            leases[key] = rules.Lease(status=status, tenant=tenant, rent=9000)
    return Market(holdings=holdings, leases=leases)


def edificio_samples() -> dict[str, dict[str, Any]]:
    """🏢 Un corte de cada tema, dos gemelos del mismo barrio y uno con cada uso."""
    empty = Market()
    out = {
        f"edificio_tema_{theme}": view.building_state(empty, key, 10, NAMES)
        for theme, key in THEME_BLOCKS.items()
    }
    out["edificio_gemelo"] = view.building_state(empty, TWIN_BLOCKS[1], 10, NAMES)
    out["edificio_usos"] = view.building_state(status_market(), STATUS_BLOCK, 10, NAMES)
    out["edificio_va_laguna"] = view.building_state(empty, "laguna_e9", 10, NAMES)
    out["edificio_portal_divisible"] = view.building_state(empty, HALVES_BLOCK, 10, NAMES)
    out["edificio_mitades"] = view.building_state(halves_market(), HALVES_BLOCK, 10, NAMES)
    return out


#: Un bloque de Triana con portal en el bajo, un local de 80 m², un piso de tres dormitorios y
#: dos áticos que se pueden dividir en dos; `halves_market` parte el local, el piso y el
#: sobreático.
HALVES_BLOCK = "triana_e4"
HALVES = ("triana_local", "triana_e4_1b", "triana_e4_sa")


def halves_market() -> Market:
    """El local, el piso y el sobreático de `HALVES_BLOCK` en dos; una mitad del piso tuya."""
    mine = rules.unit_key(HALVES[1], rules.FLOORS, 1)
    return Market(
        splits={key: Split(rules.FLOORS, 2, 50_000) for key in HALVES},
        holdings={mine: {10: Holding(mine, 10, 100, 1, 1, 0)}},
        leases={mine: rules.Lease(status="alquilado", tenant="familia", rent=9000)},
    )


def checkout(key: str, **kwargs: Any) -> dict[str, Any]:
    return view.checkout_state(LISTING_BY_KEY[key], **kwargs)


def samples() -> dict[str, dict[str, Any]]:
    """Las pantallas de muestra: las quince, con casos largos, vacíos y de todos los tipos."""
    m = market()
    week = MemberDay(30000, 1200, 4000)
    out: dict[str, dict[str, Any]] = {
        "mapa": view.map_state(m, 10, "Ana", 120000),
        "barrio_schamann": view.barrio_state(m, "schamann", 10, NAMES),
        "barrio_vegueta": view.barrio_state(m, "vegueta", 10, NAMES),
        "ficha_schamann_bajo": view.listing_state(m, "schamann_bajo", 10, NAMES, NEWS),
        "ficha_adeje_chalet": view.listing_state(m, "adeje_chalet", 10, NAMES, []),
        "cartera": view.portfolio_state(m, 10, "Ana", 120000, week, NEWS, NAMES),
        "cartera_vacia": view.portfolio_state(m, 99, "Nadie", 500, MemberDay(), [], NAMES),
        "caja_compra": checkout(
            "schamann_bajo",
            selling=False,
            lines=[
                ("Precio (15 participaciones)", 65100, "normal"),
                ("ITP 6,5 %", 4232, "tax"),
                ("Notaría y registro", 977, "normal"),
                ("Total", 70309, "total"),
            ],
            balance=120000,
            after=49691,
            note="Comprar sube el índice de Schamann un 0,2 %",
        ),
        "caja_venta": checkout(
            "schamann_bajo",
            selling=True,
            lines=[
                ("Precio (60 participaciones)", 281000, "normal"),
                ("Agencia 3 %", -8430, "normal"),
                ("IRPF de la ganancia 19 %", -1200, "tax"),
                ("Cobras", 271370, "total"),
            ],
            balance=49691,
            after=321061,
            note="Vendes al mercado",
            stamp="VENDIDO",
        ),
    }
    for kind, key in BY_KIND.items():
        out[f"ficha_{kind}"] = view.listing_state(m, key, 10, NAMES, NEWS if kind == "bajo" else [])
    out["ficha_titulo_largo"] = view.listing_state(m, LONGEST.key, 10, NAMES, NEWS)
    out["cartera_con_cero"] = view.portfolio_state(m, 20, "Bea", 5, MemberDay(), [], NAMES)
    out["ficha_muchos_duenos"] = view.listing_state(m, LISTINGS[0].key, 10, NAMES, NEWS)
    out["ficha_desahucio"] = view.listing_state(m, LISTINGS[3].key, 10, NAMES, [])
    out["barrio_isleta"] = view.barrio_state(m, "isleta", 10, NAMES)
    # El peor caso de los precios: el barrio más caro con el índice por las nubes (8 dígitos).
    out["barrio_caro"] = view.barrio_state(Market(indexes={"adeje": 3.0}), "adeje", 10, NAMES)
    obra = promotion_market()
    out["barrio_obra_nueva"] = view.barrio_state(obra, "schamann", 10, NAMES)
    out["ficha_obra_nueva"] = view.listing_state(obra, "obra_1", 10, NAMES, [])
    out["caja_hipoteca"] = checkout(
        "vegueta_edificio",
        selling=False,
        lines=[
            ("Precio (el inmueble entero)", 19200000, "normal"),
            ("ITP 6,5 %", 1248000, "tax"),
            ("Notaría, registro y gestoría", 288000, "normal"),
            ("Total", 20736000, "normal"),
            ("Hipoteca del banco (80 % del precio)", -15360000, "normal"),
            ("Sale de tu bolsillo", 5376000, "total"),
        ],
        balance=9000000,
        after=3624000,
        note=(
            "Cuota: 81.000 Y$ al mes durante 30 años (360 días) al 5,25 % fijo. "
            "El banco no presta si pasa del 35 % de lo que ganas"
        ),
    )
    out["caja_sin_saldo"] = checkout(
        "adeje_chalet",
        selling=False,
        lines=[
            ("Precio (el inmueble entero)", 18032000, "normal"),
            ("Total", 18032000, "total"),
        ],
        balance=120000,
        after=-17912000,
        note="No te llega ni para la notaría",
    )
    out.update(money_samples())
    out.update(mood_samples())
    out.update(facade_samples())
    out.update(phase2_samples())
    out.update(phase34_samples())
    out.update(phase5_samples())
    out.update(phase6_samples())
    out.update(split_samples())
    out.update(street_samples())
    out.update(edificio_samples())
    out["barrio_en_dos"] = view.barrio_state(halves_market(), "triana", 10, NAMES)
    return out


CORE = [
    state
    for name, state in samples().items()
    if name
    in {
        "mapa",
        "barrio_schamann",
        "barrio_vegueta",
        "ficha_schamann_bajo",
        "ficha_adeje_chalet",
        "cartera",
        "cartera_vacia",
        "caja_compra",
        "caja_venta",
        "ficha_peor",
        "cartera_finanzas",
        "vpo_apuntada",
        "vpo_no",
        "gestion_seguro_candidatos",
        "gestion_negocio",
        "candidatos_tres",
        "candidatos_vacia",
        "ficha_candidatos_noticia",
        "ficha_inquilino_cubierto",
        "barrio_noticia",
        "mapa_noticias",
        "cartera_tres_pastillas",
        "subastas_seis_y_mas",
        "subastas_vacia",
        "reformas_en_obras",
        "reformas_local",
        "imprevistos_tres",
        "imprevistos_vacia",
        "ficha_humor_imprevistos",
        "resenas_superanfitrion",
        "resenas_nota_baja_larga",
        "resenas_sin_licencia",
        "ficha_nota_superanfitrion",
        "junta_cuatro_votaciones",
        "junta_vacia",
        "junta_duenos_largos",
        "junta_division",
        "subastas_division",
        "plantas_edificio",
        "plantas_habitaciones",
        "dividir_dos",
        "dividir_no_se_puede",
    }
]


def png_image(png: bytes, state: dict[str, Any]) -> Image.Image:
    """Abre el PNG de la pantalla `state` y comprueba que mide lo que le toca (`size_of`)."""
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG" and image.size == size_of(state)
    return image.convert("RGB")


def difference(a: Image.Image, b: Image.Image) -> float:
    """Diferencia media por canal (0-255) en el recuadro que tienen en común."""
    box = (0, 0, min(a.width, b.width), min(a.height, b.height))
    return sum(ImageStat.Stat(ImageChops.difference(a.crop(box), b.crop(box))).mean) / 3


# -- Lo que decide la vista para las pegatinas --------------------------------------------


def test_las_pegatinas_salen_del_indice_la_rentabilidad_y_la_oferta_del_fondo() -> None:
    assert view.market_badge(1.0) is None
    assert view.market_badge(rules.TENSE_INDEX) == "tensionada"
    assert view.market_badge(view.BURBUJA_INDEX) == "burbuja"
    assert view.market_badge(view.BUITRE_INDEX) == "buitre"
    assert view.listing_badges(0.05, 1.0) == []
    assert view.listing_badges(view.CHOLLO_YIELD, 1.0) == ["chollo"]
    # Una zona tensionada no es burbuja ni precio de buitre: eso va en el barrio.
    assert view.listing_badges(0.05, rules.TENSE_INDEX) == []
    assert view.listing_badges(0.09, view.BURBUJA_INDEX) == ["chollo", "burbuja"]
    assert view.listing_badges(0.05, 1.0, offer=True) == ["buitre"]
    assert view.listing_badges(0.05, view.BUITRE_INDEX, offer=True) == ["buitre"]


def test_cada_pantalla_lleva_lo_que_la_escena_dibuja() -> None:
    states = samples()
    barrio = states["barrio_burbuja"]
    assert barrio["barrio"]["badge"] == "burbuja"
    assert states["barrio_buitre"]["barrio"]["badge"] == "buitre"
    assert states["barrio_tensionada"]["barrio"]["badge"] == "tensionada"
    assert states["barrio_schamann"]["barrio"]["badge"] is None
    assert {c.get("lease_kind") for c in states["barrio_burbuja"]["cards"]} >= {"okupa", ""}
    pins = {p["key"]: p for isl in states["mapa_mercado"]["islands"] for p in isl["barrios"]}
    assert pins["schamann"]["tense"] and pins["vegueta"]["tense"] and not pins["isleta"]["tense"]
    assert states["ficha_burbuja"]["badges"] == ["burbuja"]
    assert "chollo" in states["ficha_chollo_tensionada"]["badges"]
    assert states["caja_compra"]["key"] == "schamann_bajo"
    cartera = states["cartera_solo_vpo"]
    assert cartera["items"][0]["badges"] == ["vpo"]
    assert states["ficha_obra_nueva"]["title"].startswith("Obra nueva")


def test_cada_barrio_lleva_diez_tarjetas_con_la_obra_nueva_o_el_solar_la_ultima() -> None:
    """La imagen es una rejilla de 5 × 2 y cada tarjeta tiene su botón (`pisos:ver:N`)."""
    states = samples()
    barrios = {name: state for name, state in states.items() if state["screen"] == "barrio"}
    assert len(barrios) >= 8
    for name, state in barrios.items():
        cards = state["cards"]
        assert len(cards) == view.BARRIO_SLOTS == 10, name
        assert all(card["kind"] != "solar" for card in cards[:9]), name
        assert all({"kind_name", "barrio_key", "theme", "key"} <= card.keys() for card in cards)
        assert state["barrio"]["theme_name"], name
    obra, sin_obra = states["barrio_obra_nueva"]["cards"], states["barrio_isleta"]["cards"]
    assert obra[9]["key"] == "obra_1" and obra[9]["kind"] != "solar" and obra[9]["price"]
    # El solar no se compra: sin precio, sin estado ni dueños, solo su cartel.
    assert sin_obra[9]["kind"] == "solar" and sin_obra[9]["kind_name"] == "Solar"
    assert sin_obra[9]["sign"] and not {"price", "status", "owners"} & sin_obra[9].keys()


def test_solo_el_barrio_las_plantas_dividir_y_las_calles_miden_mas_que_el_resto() -> None:
    """1000 × 760 para el barrio, las plantas (tarjetas en dos filas) y las calles (tres bandas),
    1000 × 700 para dividir (seis habitaciones y las cuentas), 1000 × 1000 para el corte del
    edificio (hasta diez plantas) y 1000 × 640 para las demás pantallas."""
    assert (W, H) == (1000, 640)
    assert SCREEN_H == {
        "barrio": 760, "plantas": 760, "dividir": 700, "calles": 760, "edificio": 1000,
    }  # fmt: skip
    assert size_of({"screen": "plantas"}) == (1000, 760)
    tall = set(SCREEN_H)
    assert {size_of(s) for s in samples().values() if s["screen"] not in tall} == {(1000, 640)}


@needs_node
@module_loop
async def test_la_escena_y_la_reserva_saben_el_alto_de_cada_pantalla(scene: HousingScene) -> None:
    assert await scene.painter.evaluate("SCREEN_H") == SCREEN_H


def test_las_muestras_cubren_las_diecisiete_pantallas_y_las_claves_de_las_fases_2_a_6() -> None:
    states = samples()
    assert {s["screen"] for s in states.values()} == {
        "mapa", "barrio", "ficha", "cartera", "caja", "vpo", "gestion", "candidatos",
        "subastas", "reformas", "imprevistos", "resenas", "junta", "plantas", "dividir",
        "calles", "edificio",
    }  # fmt: skip
    assert [len(states[k]["cards"]) for k in ("candidatos_tres", "candidatos_uno")] == [3, 1]
    assert states["candidatos_vacia"]["empty"] and not states["candidatos_vacia"]["cards"]
    assert states["gestion_negocio"]["dwelling"] is False and states["gestion_sin_seguro"]["tips"]
    assert (
        states["ficha_inquilino_no_cubre"]["insured"]
        and not states["ficha_inquilino_no_cubre"]["covered"]
    )
    assert states["ficha_candidatos_noticia"]["waiting"] == 3
    assert states["ficha_candidatos_noticia"]["barrio_news"]
    assert states["barrio_noticia"]["news"] and states["cartera_seguro_candidatos"]["waiting"] == 2
    assert any(p["news"] for i in states["mapa_noticias"]["islands"] for p in i["barrios"])
    longest = states["candidatos_frase_larga"]["cards"][0]
    assert longest["line"] == LONGEST_LINE.line
    seis = states["subastas_seis_y_mas"]
    assert (len(seis["items"]), seis["more"]) == (6, 2) and states["subastas_vacia"]["empty"]
    assert any(i["judicial"] and i["selected"] for i in seis["items"])
    assert states["subastas_voy_ganando"]["items"][0]["leading"]
    nada, obras = states["reformas_nada"], states["reformas_en_obras"]
    assert nada["whole"] and nada["vacant"]
    assert {i["status"] for i in nada["items"]} == {"available"}
    made = sorted(i["status"] for i in obras["items"] if i["status"] != "available")
    assert made == ["done", "done", "works"] and obras["works"]
    assert [i["key"] for i in states["reformas_local"]["items"]] == ["negocio"]
    assert not states["reformas_no_entero"]["whole"] and not states["reformas_no_vacio"]["vacant"]
    assert states["reformas_rehabilitado"]["rehab_until"]
    tres = states["imprevistos_tres"]
    assert [i["slot"] for i in tres["items"]] == [1, 2, 3] and tres["decides"] and tres["mood"]
    elegida = states["imprevistos_una_elegida"]["items"]
    assert any(o["chosen"] for i in elegida for o in i["options"])
    assert states["imprevistos_vacia"]["empty"] and not states["imprevistos_larga_ajeno"]["decides"]
    humor = states["ficha_humor_imprevistos"]
    assert humor["mood"] and humor["decisions"] == 2 and humor["reformed"]
    assert states["ficha_reformada"]["waiting"] == 3
    assert states["ficha_en_obras"]["works_until"] == "25/10"
    top = states["resenas_superanfitrion"]
    assert top["superhost"] and top["license_until"] == "02/03/2031" and top["occupancy"]
    assert len(top["reviews"]) == 5 and all(f["owned"] for f in top["furniture"])
    assert len(top["furniture"]) == 10 and top["furniture_stars"].endswith("★")
    low = states["resenas_nota_baja_larga"]
    assert not low["superhost"] and {r["stars"] for r in low["reviews"]} >= {1, 2}
    assert not any(f["owned"] for f in low["furniture"])
    assert states["resenas_licencia_sin_resenas"]["license"]
    assert not states["resenas_licencia_sin_resenas"]["reviews"]
    nada = states["resenas_sin_licencia"]
    assert not nada["license"] and not nada["reviews"] and nada["rating"] == ""
    fichas = ("ficha_nota_superanfitrion", "ficha_nota_baja_larga", "ficha_vacacional_sin_resenas")
    assert [states[k]["rating"] != "" for k in fichas] == [True, True, False]
    assert states[fichas[0]]["superhost"] and not states[fichas[1]]["superhost"]
    assert states[fichas[2]]["license_until"] == "02/03/2031"
    four = states["junta_cuatro_votaciones"]
    assert [v["slot"] for v in four["votes"]] == [1, 2, 3, 4] and four["more"] == 0
    assert {v["passing"] for v in four["votes"]} == {True, False} and not four["division"]
    assert [o["name"] for o in four["owners"]] == ["Ana", "Bea", "Perro Sanxe"]
    assert four["elected"] and four["admin"] == "Bea" and four["reserve"]
    assert {v["mine"] for v in four["votes"]} >= {True, None}
    assert states["junta_vacia"]["empty"] and not states["junta_vacia"]["votes"]
    long = states["junta_duenos_largos"]
    assert len(long["owners"]) == 4 and long["elected"] and any(o["admin"] for o in long["owners"])
    assert states["junta_sin_mayoria"]["admin"].startswith("Nadie")
    assert states["junta_division"]["division"]["best_bid"]
    assert not states["junta_division_sin_pujas"]["division"]["best_bid"]
    items = states["subastas_division"]["items"]
    assert [i["division"] for i in items] == [True, False, False] and items[1]["judicial"]


def test_las_muestras_de_dividir_traen_lo_que_pinta_la_escena() -> None:
    """Plantas y habitaciones (9.12.0): una tarjeta por botón, y dividir con 0, 1 y 2 opciones."""
    states = samples()
    floors, rooms = states["plantas_edificio"], states["plantas_seis_habitaciones"]
    assert floors["mode"] == rules.FLOORS and len(floors["cards"]) == 4
    assert [c["kind"] for c in floors["cards"]] == ["bajo", "piso3", "piso3", "atico"]
    assert {c["lease_kind"] for c in floors["cards"]} >= {"alquilado", "okupa", ""}
    assert rooms["mode"] == rules.ROOMS and len(rooms["cards"]) == 6  # lo más que pinta
    assert {c["kind"] for c in rooms["cards"]} == {"habitacion"}
    assert all(len(s["stats"]) == 3 for s in (floors, rooms))
    edificio = next(c for c in states["barrio_dividido"]["cards"] if c["key"] == EDIFICIO)
    assert edificio["split"] == 4 and edificio["lease_kind"] == "plantas"
    piso = next(c for c in states["barrio_por_habitaciones"]["cards"] if c["key"] == PISO3)
    assert piso["lease_kind"] == rules.BY_ROOMS
    two = states["dividir_dos"]["options"]
    assert [o["mode"] for o in two] == [rules.FLOORS, rules.ROOMS] and not any(
        o["problem"] for o in two
    )
    assert len(two[1]["units"]) == 6 and all(len(o["notes"]) == 3 for o in two)
    assert states["dividir_sin_dueno"]["options"][0]["problem"]
    assert states["dividir_por_habitaciones"]["rooms"]
    assert states["dividir_no_se_puede"]["options"] == []


def test_las_muestras_de_calles_y_edificios_traen_lo_que_pinta_la_escena() -> None:
    """El plano (tres calles de 5, 4 o 3 edificios) y cortes de cada tipo, del más bajo al más
    alto."""
    states = samples()
    plano = states["calles_triana"]
    per_street = {
        k: [len(s["buildings"]) for s in states[k]["streets"]]
        for k in ("calles_triana", "calles_vegueta", "calles_rural", "calles_noticia")
    }
    assert per_street == {
        "calles_triana": [5, 5, 5],
        "calles_vegueta": [4, 4, 4],
        "calles_rural": [3, 3, 3],
        "calles_noticia": [5, 5, 5],
    }
    assert sorted(b["slot"] for s in plano["streets"] for b in s["buildings"]) == list(range(1, 16))
    assert {b["kind"] for s in plano["streets"] for b in s["buildings"]} >= {"bloque", "casa"}
    assert all(b["face"] for s in plano["streets"] for b in s["buildings"])
    assert sum(b["mine"] for s in plano["streets"] for b in s["buildings"]) == 3
    assert not states["calles_triana"]["news"] and states["calles_noticia"]["news"]
    assert states["calles_noticia"]["barrio"]["badge"]
    assert states["calles_rural"]["barrio"]["theme"] == "rural"
    alto = states["edificio_alto"]
    assert sum(len(f["units"]) for f in alto["floors"]) >= 19 and len(alto["floors"]) >= 10
    assert [f["level"] for f in alto["floors"]][-1] < 0  # el sótano, abajo
    assert states["edificio_pantalan"]["kind"] == "pantalan"
    assert all(f["level"] < 0 for f in states["edificio_aparcamiento"]["floors"])
    for name in ("casa", "entero"):
        assert sum(len(f["units"]) for f in states[f"edificio_{name}"]["floors"]) == 1, name
    units = [u for f in states["edificio_tuyas"]["floors"] for u in f["units"]]
    assert {u["use_kind"] for u in units} >= {"alquilado", "vacio", "impago", "venta"}
    assert sum(u["mine"] for u in units) == 2 and max(u["waiting"] for u in units) == 3
    assert sum(u["featured"] for u in units) == 1


# -- Reserva con Pillow ------------------------------------------------------------------


async def test_sin_node_ni_chromium_cada_pantalla_cae_a_pillow() -> None:
    scene = HousingScene(executable_path=NO_CHROMIUM, node=NO_NODE)
    try:
        pngs = await scene.paint(list(samples().values()))
        still = await scene.still(CORE[0])
    finally:
        await scene.close()
    assert scene.disabled
    assert len(pngs) == len(samples())
    for state, png in zip([*samples().values(), CORE[0]], [*pngs, still], strict=True):
        assert len(png_image(png, state).getcolors(1 << 20) or []) > 20


async def test_sin_estados_no_se_pinta_nada() -> None:
    scene = HousingScene(executable_path=NO_CHROMIUM, node=NO_NODE)
    try:
        assert await scene.paint([]) == []
    finally:
        await scene.close()


# -- Node ---------------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scene() -> HousingScene:
    """Una escena con Node (y sin Chromium), compartida por el módulo."""
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    escena = HousingScene(executable_path=NO_CHROMIUM)
    yield escena
    await escena.close()


@needs_node
@module_loop
async def test_con_node_cada_pantalla_es_un_png_del_tamano_y_distinta(scene: HousingScene) -> None:
    states = list(samples().values())
    pngs = await scene.paint(states)
    assert not scene.painter.disabled and len(pngs) == len(states)
    images = [png_image(png, state) for png, state in zip(pngs, states, strict=True)]
    for image in images:
        assert len(image.getcolors(1 << 20) or []) > 1000
    for first, other in zip(images, images[1:], strict=False):
        assert difference(first, other) > 0.05
    assert (await scene.still(states[3])) == pngs[3]


@needs_node
@module_loop
async def test_con_node_una_pantalla_desconocida_desactiva_el_pintor() -> None:
    other = HousingScene(executable_path=NO_CHROMIUM)
    try:
        png = (await other.paint([{"screen": "nada", "fallback": ["Nada"]}]))[0]
    finally:
        await other.close()
    assert other.painter.disabled
    png_image(png, {"screen": "nada"})  # cae a Pillow, con las medidas de una pantalla corriente


@needs_node
@module_loop
async def test_las_pantallas_repartidas_pintan_lo_mismo_que_con_un_proceso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cada pantalla es entera: repartir el dibujo entre procesos no cambia ni un byte."""
    states = CORE * 4 + list(facade_samples().values())
    node = own_node()
    sharing = HousingScene(executable_path=NO_CHROMIUM, node=node)
    try:
        # Abre los procesos de refuerzo: sin ellos, el primero lo pintaría todo.
        assert await sharing.painter.render(META, states[:8], gif=False) is not None
        pool = node_scene._pool(node)
        deadline = time.monotonic() + 20
        while len([w for w in pool.workers if w.alive and not w.busy]) < 3:
            assert time.monotonic() < deadline, "No se han abierto los procesos de refuerzo"
            await asyncio.sleep(0.05)
        alone = await sharing.painter.render(META, states, procs=1, gif=False)
        assert alone is not None
        setups: list[str] = []
        call = node_scene._Process.call

        async def counted(self: node_scene._Process, request: dict, **kw: bool):  # noqa: ANN202
            setups.extend(["setup"] if request["call"] == "setup" else [])
            return await call(self, request, **kw)

        monkeypatch.setattr(node_scene._Process, "call", counted)
        split = await sharing.painter.render(META, states, gif=False)
        assert split is not None
        # Cada tramo empieza con su `setup`: con uno solo no se habría repartido.
        assert len(setups) > 1, "El dibujo no se ha repartido"
        pngs = await sharing.paint(states)
        assert len(split) == len(alone) == len(pngs) == len(states)
        for a, b, png, state in zip(split, alone, pngs, states, strict=True):
            assert a["image"].tobytes() == b["image"].tobytes()
            assert png_image(png, state).tobytes() == a["image"].tobytes()
    finally:
        await sharing.close()
        await node_scene.shutdown()


# -- Personalidad: el paisaje de cada tema y los dibujos de cada tipo --------------------------


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def looks() -> HousingScene:
    """Otra escena de Node, solo para estas pruebas: no depende de si otra deja `scene` apagada."""
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    escena = HousingScene(executable_path=NO_CHROMIUM)
    yield escena
    await escena.close()


def facade_crop(patch: dict[str, Any]) -> Image.Image:
    return patch["image"].convert("RGB").crop(FACADE_BOX)


#: Lo mínimo que tiene que cambiar (diferencia media sobre 255) una fachada al cambiar de tipo, de
#: tema o de semilla. Lo medido: 8,7 entre temas, 11,6 entre tipos y 6 entre dos del mismo tipo.
MIN_KIND_DIFF, MIN_THEME_DIFF, MIN_SEED_DIFF = 5.0, 4.0, 5.0


def test_los_tipos_y_los_temas_son_los_del_catalogo() -> None:
    assert len(KINDS) == 20 and len(FACADE_KINDS) == 21 and "habitacion" in KINDS
    assert set(THEME_BARRIO) == set(THEMES) and len(THEMES) == 7
    for theme, barrio in THEME_BARRIO.items():
        assert BARRIO_BY_KEY[barrio].theme == theme
    # Las pantallas con fachada traen el barrio y el tema que la escena usa para pintarla.
    states = samples()
    assert states["ficha_schamann_bajo"]["theme"] == "obrero"
    assert states["ficha_schamann_bajo"]["barrio_key"] == "schamann"
    assert all(
        "theme" in card and "barrio_key" in card for card in states["barrio_schamann"]["cards"]
    )
    assert states["barrio_schamann"]["cards"][-1]["kind"] == "solar"
    assert states["caja_compra"]["theme"] == "obrero"
    assert all("theme" in item for item in states["cartera"]["items"] if item["kind"] != "vpo")


@needs_node
@module_loop
async def test_cada_tipo_se_pinta_en_cada_tema_y_ninguno_sale_igual(looks: HousingScene) -> None:
    """Los 20 tipos y el solar en los 7 temas: 147 fachadas sin error y todas distintas."""
    combos = [(theme, kind) for theme in THEME_BARRIO for kind in FACADE_KINDS]
    states = [facade_state(kind, THEME_BARRIO[theme]) for theme, kind in combos]
    patches = await looks.painter.render(META, states, gif=False)
    assert patches is not None and not looks.painter.disabled and len(patches) == len(states)
    crops = {combo: facade_crop(patch) for combo, patch in zip(combos, patches, strict=True)}
    for theme in THEME_BARRIO:  # en un mismo barrio, cada tipo tiene su silueta
        for a, b in itertools.combinations(FACADE_KINDS, 2):
            assert difference(crops[theme, a], crops[theme, b]) > MIN_KIND_DIFF, (theme, a, b)
    for kind in FACADE_KINDS:  # y un mismo tipo se tiñe con el carácter de cada barrio
        for a, b in itertools.combinations(THEME_BARRIO, 2):
            assert difference(crops[a, kind], crops[b, kind]) > MIN_THEME_DIFF, (kind, a, b)


@needs_node
@module_loop
async def test_dos_inmuebles_del_mismo_tipo_en_un_barrio_no_salen_clavados(
    looks: HousingScene,
) -> None:
    groups: dict[tuple[str, str], list[str]] = {}
    for item in LISTINGS:
        groups.setdefault((item.barrio, item.kind), []).append(item.key)
    pairs = [keys for keys in groups.values() if len(keys) > 1]
    assert pairs, "El catálogo ya no repite ningún tipo en un barrio: la prueba no mira nada"
    keys = [key for group in pairs for key in group]
    states = [
        facade_state(LISTING_BY_KEY[key].kind, LISTING_BY_KEY[key].barrio, key) for key in keys
    ]
    patches = await looks.painter.render(META, states, gif=False)
    assert patches is not None
    crops = dict(zip(keys, map(facade_crop, patches), strict=True))
    for group in pairs:
        for a, b in itertools.combinations(group, 2):
            assert difference(crops[a], crops[b]) > MIN_SEED_DIFF, (a, b)


#: Lo mínimo que se diferencian (sobre 255, en el recuadro del corte) dos bloques del mismo
#: barrio con las mismas plantas y unidades. Lo medido en Schamann: antes de darles aspecto
#: propio, desde 3,3; después, desde 13.
MIN_BLOCK_DIFF = 8.0
SECTION_BOX = (16, 166, 984, 960)


def test_las_muestras_de_edificios_traen_cada_tema_cada_uso_y_los_aticos_caros() -> None:
    states = edificio_samples()
    assert {states[f"edificio_tema_{t}"]["theme"] for t in THEME_BLOCKS} == set(THEMES)
    premium = [u["key"] for s in states.values() for f in s["floors"] for u in f["units"]]
    premium = [k for k in premium if streets.is_penthouse(k)]
    assert any(k.endswith("_sa") for k in premium) and any(k.endswith("_va") for k in premium)
    usos = [u for f in states["edificio_usos"]["floors"] for u in f["units"]]
    assert {u["use_kind"] for u in usos} == set(STATUSES)
    assert all(u["premium"] == streets.is_penthouse(u["key"]) for u in usos)


def test_las_muestras_de_dividir_en_dos_traen_portal_mitades_y_divisibles() -> None:
    states = edificio_samples()
    bajo = next(f for f in states["edificio_portal_divisible"]["floors"] if f["level"] == 0)
    assert bajo["portal"] and any(u["divisible"] and u["kind"] == "local" for u in bajo["units"])
    units = [u for f in states["edificio_mitades"]["floors"] for u in f["units"]]
    assert sorted(u["half"] for u in units if u["half"]) == ["dcha."] * 3 + ["izda."] * 3
    assert not any(u["divisible"] for u in units if u["key"].split("~")[0] in HALVES)
    assert any(c.get("lease_kind") == "dividido" for c in samples()["barrio_en_dos"]["cards"])


def without_halves(state: dict[str, Any]) -> dict[str, Any]:
    """El corte como lo mandaba la vista antes de la 9.16.0: sin metros, mitades ni portal."""
    old = copy.deepcopy(state)
    for floor in old["floors"]:
        floor.pop("portal", None)
        for unit in floor["units"]:
            for field in ("area", "half", "divisible"):
                unit.pop(field, None)
    return old


@needs_node
@module_loop
async def test_el_corte_pinta_portal_y_mitades_y_sin_esos_datos_sale_como_antes(
    looks: HousingScene,
) -> None:
    """Un estado viejo (sin `area`, `half`, `divisible` ni `portal`) se pinta a partes iguales,
    igual que uno con los mismos metros en cada hueco y sin nada que partir; el nuevo cambia."""
    state = edificio_samples()["edificio_portal_divisible"]
    old, flat = without_halves(state), copy.deepcopy(state)
    for floor in flat["floors"]:
        floor["portal"] = False
        for unit in floor["units"]:
            unit.update(area=50, half="", divisible=False)
    split = edificio_samples()["edificio_mitades"]
    patches = await looks.painter.render(META, [old, flat, state, split], gif=False)
    assert patches is not None
    images = [p["image"].convert("RGB") for p in patches]
    assert images[0].tobytes() == images[1].tobytes()
    assert difference(images[0], images[2]) > 0.5 and difference(images[2], images[3]) > 0.5


@needs_node
@module_loop
async def test_dos_bloques_iguales_de_un_barrio_no_salen_clavados_y_uno_sale_igual_dos_veces(
    looks: HousingScene,
) -> None:
    """Cada bloque tiene su fachada, su cielo, su azotea y sus pisos amueblados a su manera: dos
    con las mismas plantas y unidades se distinguen a simple vista, y el mismo sale igual."""
    blocks = [b for b in streets.BUILDINGS if b.barrio == "schamann" and b.kind == "bloque"]
    states = [view.building_state(Market(), b.key, 10, NAMES) for b in blocks]
    shapes = [tuple(len(f["units"]) for f in s["floors"]) for s in states]
    patches = await looks.painter.render(META, [*states, states[0]], gif=False)
    assert patches is not None
    crops = [p["image"].convert("RGB").crop(SECTION_BOX) for p in patches]
    assert crops[0].tobytes() == crops[-1].tobytes()
    pairs = [
        (i, j) for i, j in itertools.combinations(range(len(states)), 2) if shapes[i] == shapes[j]
    ]
    assert len(pairs) > 10
    assert any({blocks[i].key, blocks[j].key} == set(TWIN_BLOCKS) for i, j in pairs)
    for i, j in pairs:
        assert difference(crops[i], crops[j]) > MIN_BLOCK_DIFF, (blocks[i].key, blocks[j].key)


@needs_node
@module_loop
async def test_la_obra_nueva_usa_el_estilo_de_su_barrio(looks: HousingScene) -> None:
    """`obra_N` no dice su barrio: lo da el estado (`barrio_key`), no un color al azar."""
    obra = view.listing_state(promotion_market(), "obra_1", 10, NAMES, [])
    assert obra["barrio_key"] == "schamann" and obra["theme"] == "obrero"

    async def style(key: str, barrio: str | None = None) -> dict[str, Any]:
        found = await looks.painter.evaluate(f"styleOf({json.dumps(key)}, {json.dumps(barrio)})")
        assert isinstance(found, dict)
        return found

    schamann = await style("schamann_bajo")
    assert await style(obra["key"], obra["barrio_key"]) == schamann
    assert (await style(obra["key"]))["wall"] != schamann["wall"]  # sin el dato, sale otro color
    assert (await style(obra["key"], "mogan"))["wall"] != schamann["wall"]
    # Las que sí dicen su barrio siguen valiendo: el solar, las VPO y lo que trae `barrio_key`.
    assert (await style("solar_vegueta"))["base"] == "vegueta"
    assert (await style("vpo:jinamar"))["t"] == "vpo"
    assert (await style("vegueta_piso2", "orotava"))["base"] == "orotava"
    # Y se ve: la misma ficha en otro barrio cambia la fachada.
    states = [
        facade_state("edificio", "schamann", obra["key"]),
        facade_state("edificio", "mogan", obra["key"]),
    ]
    patches = await looks.painter.render(META, states, gif=False)
    assert patches is not None
    assert difference(*map(facade_crop, patches)) > MIN_THEME_DIFF


@needs_node
@module_loop
async def test_la_escena_sabe_el_tema_de_cada_barrio_y_tiene_bromas_de_cada_tipo(
    looks: HousingScene,
) -> None:
    themes = await looks.painter.evaluate(
        "Object.fromEntries(Object.entries(STYLE).map(([k, a]) => [k, a[5]]))"
    )
    assert themes is not None
    for barrio in BARRIOS:
        assert themes[barrio.key] == barrio.theme, barrio.key
    assert await looks.painter.evaluate("THEME_NAME") == THEMES
    tags = await looks.painter.evaluate("KIND_TAGS")
    assert isinstance(tags, dict)
    assert set(tags) >= {*KINDS, "solar"}
    jokes = [joke for pool in tags.values() for joke in pool]
    assert all(len(pool) >= 2 for pool in tags.values())
    assert len(jokes) == len(set(jokes)), "Hay una broma repetida"
    assert all(5 < len(joke) <= 80 for joke in jokes)


@needs_node
@module_loop
async def test_cada_chincheta_del_mapa_enseña_el_tema_de_su_barrio(looks: HousingScene) -> None:
    state = view.map_state(Market(), 10, "Ana", 1000)
    pins = [pin for island in state["islands"] for pin in island["barrios"]]
    assert {pin["theme"] for pin in pins} == set(THEMES)
    other = copy.deepcopy(state)
    for island in other["islands"]:
        for pin in island["barrios"]:
            pin["theme"] = "playa"
    patches = await looks.painter.render(META, [state, other], gif=False)
    assert patches is not None
    a, b = (patch["image"].convert("RGB") for patch in patches)
    sq, size = 354, 476
    for n, island in enumerate(state["islands"]):
        left = 16 + n * (size + 16) + (size - sq) / 2
        for pin in island["barrios"]:
            x, y = left + pin["x"] * sq, 98 + pin["y"] * sq
            box = (
                int(x + 4),
                int(y + 4),
                int(x + 17),
                int(y + 17),
            )  # el disco de abajo a la derecha
            if pin["theme"] != "playa":
                assert difference(a.crop(box), b.crop(box)) > 3, pin["key"]
    # La clave de abajo trae los siete discos, cada uno de su color.
    colors = await looks.painter.evaluate("THEME_PIN")
    assert isinstance(colors, dict) and set(colors) == set(THEMES)
    strip = list(a.crop((16, 586, 984, 614)).getdata())
    for theme, (color, _icon) in colors.items():
        want = tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))
        near = sum(1 for px in strip if max(abs(p - w) for p, w in zip(px, want, strict=True)) < 12)
        assert near > 20, theme


def changed(state: dict[str, Any], edit: Any) -> dict[str, Any]:
    """Una copia de `state` con `edit` aplicado (para ver qué parte de la imagen depende de qué)."""
    other = copy.deepcopy(state)
    edit(other)
    return other


def box_difference(a: Image.Image, b: Image.Image, box: tuple[int, int, int, int]) -> float:
    return difference(a.crop(box), b.crop(box))


@needs_node
@module_loop
async def test_las_habitaciones_enseñan_lo_que_piden_y_no_el_precio(looks: HousingScene) -> None:
    """El piso se vende entero: en sus habitaciones el precio no dice nada y no se pinta."""
    rooms, floors = samples()["plantas_habitaciones"], samples()["plantas_edificio"]

    def price(state: dict[str, Any]) -> None:
        for c in state["cards"]:
            c["price"] = "99.999.999 Y$"

    def rent(state: dict[str, Any]) -> None:
        for c in state["cards"]:
            c["rent"] = "99.999 Y$/mes"

    states = [rooms, changed(rooms, price), changed(rooms, rent), floors, changed(floors, price)]
    patches = await looks.painter.render(META, states, gif=False)
    assert patches is not None
    room, room_price, room_rent, floor, floor_price = (p["image"].convert("RGB") for p in patches)
    assert room.tobytes() == room_price.tobytes()
    assert difference(room, room_rent) > 0.05
    assert difference(floor, floor_price) > 0.05  # las plantas sí se venden por separado


@needs_node
@module_loop
async def test_dividir_pinta_cada_opcion_en_su_tarjeta(looks: HousingScene) -> None:
    """Las dos opciones, una al lado de otra: cambiar una solo cambia su mitad, y el aviso sale."""
    two, rooms = samples()["dividir_dos"], samples()["dividir_por_habitaciones"]

    def total(n: int) -> Any:
        return lambda s: s["options"][n].update(total="99.999.999 Y$")

    def problem(s: dict[str, Any]) -> None:
        s["options"][1]["problem"] = "Tiene que estar vacío."

    def whole(s: dict[str, Any]) -> None:
        s["rooms"] = False

    states = [two, changed(two, total(0)), changed(two, total(1)), changed(two, problem)]
    states += [rooms, changed(rooms, whole)]
    patches = await looks.painter.render(META, states, gif=False)
    assert patches is not None
    base, first, second, warned, by_rooms, by_whole = (p["image"].convert("RGB") for p in patches)
    left, right, bottom = (0, 170, 500, 660), (500, 170, 1000, 660), (500, 600, 1000, 660)
    assert box_difference(base, first, left) > 0.05 and box_difference(base, first, right) == 0
    assert box_difference(base, second, right) > 0.05 and box_difference(base, second, left) == 0
    assert box_difference(base, warned, bottom) > 1  # el aviso ámbar al pie de la tarjeta
    assert box_difference(by_rooms, by_whole, (480, 100, 860, 164)) > 1  # la nota de juntarlas


@needs_node
@module_loop
async def test_node_y_chromium_pintan_lo_mismo(scene: HousingScene) -> None:
    """Mismo motor (Skia): solo cambia algún borde de letra, no colores ni posiciones."""
    if not LOCAL_CHROMIUM.exists():
        pytest.skip("No hay Chromium en esta máquina")
    browser = BrowserScene(
        SCENE, name="chromium", ready="loadFonts()", executable_path=str(LOCAL_CHROMIUM)
    )
    states = list(samples().values())
    try:
        from_browser = await browser.render(META, states)
    finally:
        await browser.close()
    if from_browser is None:
        pytest.skip("Chromium no arranca en esta máquina")
    from_node = await scene.painter.render(META, states, gif=False)
    assert from_node is not None
    # Cada pantalla entera y de su tamaño (`assemble` falla si no mide lo que toca).
    for (name, state), patch_b, patch_n in zip(
        samples().items(), from_browser, from_node, strict=True
    ):
        x, y = (browser_scene.assemble([patch], size_of(state))[0] for patch in (patch_b, patch_n))
        assert difference(x.convert("RGB"), y.convert("RGB")) < 4, name


def paged_market(count: int, vpo: bool = False) -> Market:
    """Un miembro (el 10) con participaciones en `count` inmuebles y, si se pide, su VPO."""
    holdings = {item.key: {10: Holding(item.key, 10, 1, 1, 1, 0)} for item in LISTINGS[:count]}
    owners = {"anaza": VpoOwner("anaza", 10, 430000, 0, 0, 0)} if vpo else {}
    return Market(holdings=holdings, vpo=owners)


def test_mis_pisos_va_de_diez_en_diez_y_corrige_la_pagina_que_sobra() -> None:
    m = paged_market(23)
    keys = view.portfolio_keys(m, 10)
    assert len(keys) == 23
    first = view.portfolio_state(m, 10, "Ana", 1, MemberDay(), [], NAMES)
    assert (first["page"], first["pages"]) == (1, 3)
    assert [i["key"] for i in first["items"]] == keys[:10]
    last = view.portfolio_state(m, 10, "Ana", 1, MemberDay(), [], NAMES, page=2)
    assert [i["key"] for i in last["items"]] == keys[20:]
    assert last["fallback"][-1] == "Página 3 de 3"
    # Si vende y sobra la página en la que estaba, se ve la última que queda.
    assert view.portfolio_page(m, 10, 99)[:2] == (2, 3)
    assert view.portfolio_page(m, 10, -1)[0] == 0
    # Lo de arriba (el ladrillo) es de todo, no de la página.
    assert first["user"]["worth"] == last["user"]["worth"]


def test_la_vpo_abre_la_primera_pagina_de_mis_pisos() -> None:
    m = paged_market(10, vpo=True)
    assert view.portfolio_pages(len(view.portfolio_keys(m, 10))) == 2
    first = view.portfolio_state(m, 10, "Ana", 1, MemberDay(), [], NAMES)
    assert first["items"][0]["kind"] == "vpo" and len(first["items"]) == 10
    second = view.portfolio_state(m, 10, "Ana", 1, MemberDay(), [], NAMES, page=1)
    assert [i["key"] for i in second["items"]] == [LISTINGS[9].key]
    alone = view.portfolio_state(paged_market(10), 10, "Ana", 1, MemberDay(), [], NAMES)
    assert alone["pages"] == 1 and len(alone["items"]) == 10
