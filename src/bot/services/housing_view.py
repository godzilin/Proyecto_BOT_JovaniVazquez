"""Lo que enseña cada pantalla del panel de `pisos`, listo para pintar.

El panel es solo imágenes: cada pantalla es un diccionario que pinta la escena
(`assets/pisos/escena.html`, ver `bot.services.housing_scene`). Aquí se decide todo
lo que sale y se formatea cada cifra; la escena no calcula nada. Cada pantalla lleva
también `fallback`, unas líneas de texto para el dibujo de reserva con Pillow.

Sin emojis en nada de lo que se pinta: ni Node ni el Chromium del Docker traen su
fuente. Lo que lleva icono (el tipo de inmueble, el inquilino, el nivel de renta)
manda su clave y la escena dibuja el icono.

Pantallas (`screen`):

- `mapa`: Gran Canaria y Tenerife con una chincheta por barrio.
- `barrio`: los nueve inmuebles de un barrio y su obra nueva (o el solar donde irá).
- `ficha`: un inmueble con todas sus cuentas.
- `cartera`: lo que tiene un miembro, de `PORTFOLIO_PAGE` en `PORTFOLIO_PAGE` (◀ ▶).
- `caja`: la cuenta de una compra o una venta, antes o después de pagar.
- `vpo`: las viviendas de protección oficial, el sorteo de los lunes y lo tuyo.

Las claves son estables: la escena las lee tal cual. Una pantalla nueva o una clave
nueva se añade aquí y en la escena a la vez.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Any

from bot.repositories.housing import Holding, Market, News
from bot.repositories.housing_finance import LOAN_BNPL, Deposit
from bot.services import housing as rules
from bot.services import housing_solars as solar_rules
from bot.services import loans as lending
from bot.services.economy import format_amount
from bot.services.housing_catalog import (
    BARRIO_BY_KEY,
    BARRIOS,
    ISLANDS,
    KINDS,
    LISTING_BY_KEY,
    REFORMS,
    RENT_LEVELS,
    REVIEW_LINES,
    TENANT_BY_KEY,
    THEMES,
    VPO_BY_KEY,
    VPO_UNITS,
    BarrioNews,
    Listing,
)
from bot.services.housing_furniture import FURNITURE, furniture_stars
from bot.services.housing_solars import (
    PROJECT_BY_KEY,
    PROJECTS,
    SOLAR_BY_KEY,
    solar_units,
    solars_of,
)
from bot.services.housing_streets import (
    PANTALAN,
    SOLAR_SUFFIX,
    Building,
    buildings_of,
    is_penthouse,
    map_order,
    streets_of,
)
from bot.services.levels import TIMEZONE
from bot.services.pending import Pending, Side
from bot.services.taxes import vacant_rental_deduction

State = dict[str, Any]

#: Tarjetas de la pantalla de un barrio: sus nueve inmuebles y la obra nueva (o su solar),
#: en dos filas de cinco, una por botón.
BARRIO_SLOTS = 10
#: Clave de la tarjeta del solar de un barrio sin obra nueva (`solar_<barrio>`).
SOLAR_PREFIX = "solar_"
#: Inmuebles por página de 🔑 Mis pisos: los que caben en la imagen aun con hipoteca
#: (dos columnas de cinco) y en su desplegable. El resto, con ◀ ▶.
PORTFOLIO_PAGE = 10

#: Cómo se dice cada estado de un inmueble con dueño.
STATUS_TEXT = {
    rules.VACANT: "Vacío, buscando inquilino",
    rules.RENTED: "Alquilado",
    rules.DEFAULTING: "El inquilino no paga",
    rules.EVICTING: "Desahucio en marcha",
    rules.SQUATTED: "Okupado",
    rules.HABITUAL: "Vive su dueño",
    rules.OWN_BUSINESS: "Su dueño tiene aquí su negocio",
    rules.OBRAS: "En obras",
    rules.BY_ROOMS: "Se alquila por habitaciones",
}

#: Qué pasa en una noticia, en una línea. `{tenant}` y `{amount}` se rellenan.
NEWS_TEXT = {
    "compra": "{who} compra participaciones",
    "venta": "{who} vende participaciones",
    "firma": "{tenant} firma el contrato",
    "renueva": "{tenant} renueva el contrato",
    "se_va": "{tenant} se va del piso",
    "impago": "{tenant} deja de pagar",
    "paga": "{tenant} se pone al día (milagro)",
    "okupa": "Entran okupas en el piso vacío",
    "juicio": "Desahucio pedido: el juzgado da fecha (abogado: {amount})",
    "desahucio": "Desahucio hecho: el piso vuelve a estar libre",
    "averia": "Se rompe el termo: {amount}",
    "humedades": "Humedades en el baño: {amount}",
    "derrama": "Derrama de la comunidad: {amount}",
    "ite": "Inspección Técnica de Edificios: {amount}",
    "vecino": "El vecino del 3.º pone una queja por el ruido",
    "cerradura": "Cambio de cerradura: {amount}",
    "hipoteca": "{who} firma una hipoteca de {amount}",
    "amortiza": "{who} cancela la hipoteca: {amount}",
    "rehipoteca": "{who} rehipoteca su parte por {amount}",
    "anticipo": "{who} cede sus rentas al banco por un anticipo de {amount}",
    "ejecucion": "El banco ejecuta la hipoteca de {who}: subasta por {amount}",
    "se_muda": "{who} se muda aquí",
    "abre_negocio": "{who} trae aquí su negocio",
    "cierra_negocio": "{who} saca de aquí su negocio",
    "firma_miembro": "{who} firma el alquiler por {amount} al mes",
    "obra_nueva": "{who} levanta la promoción: sale a {amount}",
    "obra_solar": "{who} empieza a construir en el solar: {amount} entre solar, obra e impuestos",
    "burbuja": "Estalla la burbuja: el barrio baja y las obras se retrasan",
    "fianza_devuelta": "{who} recupera la fianza: {amount}",
    "fianza_incautada": "El casero se queda {amount} de la fianza de {who}",
    "partida_agotada": "Partida agotada: {who} se queda sin el Bono Alquiler Joven",
    "licencia": "Licencia vacacional concedida (tasa de {amount})",
    "tope_ley": "La Ley de Vivienda topa la renta: zona tensionada",
    "recargo_ibi": "IBI recargado por vivienda vacía: {amount}",
    "oferta_fondo": "Buitre Capital ofrece {amount} por el inmueble entero",
    "venta_fondo": "{who} se lo vende a Buitre Capital",
    "sorteo_vpo": "A {who} le toca la VPO en el sorteo",
    "compra_vpo": "{who} compra la VPO por {amount}",
    "devuelve_vpo": "{who} devuelve la VPO a la Administración",
    "vpo_caducada": "A {who} se le pasa el plazo de la VPO",
    "candidato": "Llega un candidato: {tenant}",
    "seguro": "El seguro de impago paga la renta: {amount}",
    "venta_miembro": "{who} vende participaciones a otro miembro por {amount}",
    "compra_miembro": "{who} compra participaciones a otro miembro por {amount}",
    "subasta": "{who} saca participaciones a subasta desde {amount}",
    "subasta_judicial": "Subasta judicial por la hipoteca de {who} (vale {amount})",
    "subasta_adjudicada": "{who} se lleva la subasta por {amount}",
    "subasta_desierta": "Subasta desierta: nadie puja (mínima {amount})",
    "ejecucion_subasta": "Remate de la subasta judicial de {who}: {amount}",
    "obras": "{who} empieza una reforma de {amount}",
    "obra_terminada": "Se acaban las obras: el piso está como nuevo",
    "licencia_renovada": "Se renueva la licencia vacacional: 5 años más",
    "licencia_caduca": "Caduca la licencia vacacional: en zona tensionada no se renueva",
    "licencia_denegada": "La comunidad vota que no a la licencia vacacional",
    "superanfitrion": "Superanfitrión: 4,8 de media, cobra un 10 % más",
    "junta": "{who} propone algo a la junta",
    "junta_aprueba": "La junta aprueba la propuesta de {who}",
    "junta_rechaza": "La junta rechaza la propuesta de {who}",
    "junta_falla": "La junta aprueba lo de {who}, pero no se puede hacer",
    "fondo_reserva": "El fondo de reserva paga {amount}",
    "division": "{who} pide la división: subasta desde {amount}",
    "division_adjudicada": "{who} se queda el inmueble entero por {amount}",
    "division_mercado": "Nadie puja en la división: se vende al mercado",
    "plantas": "{who} lo divide por plantas: obra de {amount}",
    "habitaciones": "{who} lo prepara para alquilar por habitaciones: {amount} en muebles",
    "juntadas": "{who} junta las habitaciones: vuelve a alquilarse entero",
    # 9.23.0: la duración del contrato y recuperar el piso.
    "no_renueva": "{who} avisa: no renovará el contrato",
    "prorroga": "{tenant} pide la prórroga de la Ley de Vivienda: un año más",
    "no_renovado": "Se acaba el contrato y no se renueva: {tenant} se va",
    "llaves": "{who} paga {amount} por las llaves",
    "llaves_gratis": "{who} recupera el piso por las buenas",
    "rechaza_llaves": "{tenant} no se va: pide {amount}",
    "atrinchera": "{tenant} se atrinchera y deja de pagar",
    "sale_llaves": "{tenant} se va con las llaves pagadas",
    "sale_necesidad": "{tenant} deja el piso: su dueño lo necesita para vivir",
    "necesidad": "{who} lo pide para vivir (art. 9.3 LAU)",
    "necesidad_multa": "{who} se va antes de tiempo: {amount} de indemnización",
}

#: La etiqueta de la ficha cuando el inquilino se va al acabar el contrato (9.23.0).
LEAVING_TAG = {
    rules.LEAVE_NO_RENEW: "No renueva: se va el {day}",
    rules.LEAVE_EXTENDED: "En prórroga hasta el {day}",
    rules.LEAVE_KEYS: "Llaves pagadas: se va el {day}",
    rules.LEAVE_NEED: "Lo pides para vivir: se va el {day}",
}

#: Cómo se llama cada noticia de las VPO (su `listing` es `vpo:<clave>`).
VPO_PREFIX = "vpo:"

#: Pegatinas con guasa (solo presentación: ninguna regla las mira). Rentabilidad bruta anual a
#: partir de la cual un inmueble es un «chollo»; índice del barrio desde el que hay «burbuja» y
#: por debajo del cual el precio es «de fondo buitre».
CHOLLO_YIELD = 0.08
BURBUJA_INDEX = 1.08
BUITRE_INDEX = 0.88


def _pct(value: float, digits: int = 1) -> str:
    return f"{value * 100:.{digits}f}".replace(".", ",") + " %"


def _change(index: float) -> str:
    delta = index - 1.0
    if abs(delta) < 0.0005:
        return "="
    return ("+" if delta > 0 else "−") + _pct(abs(delta))


def _name(names: dict[int, str], member: int) -> str:
    return names.get(member) or "Alguien"


def market_badge(index: float) -> str | None:
    """La pegatina del barrio según su índice: `burbuja`, `tensionada`, `buitre` o `None`."""
    if index >= BURBUJA_INDEX:
        return "burbuja"
    if rules.tense(index):
        return "tensionada"
    return "buitre" if index <= BUITRE_INDEX else None


def listing_badges(yearly_yield: float, index: float, *, offer: bool = False) -> list[str]:
    """Las pegatinas de un inmueble, de más a menos importante.

    Args:
        yearly_yield: Renta de un año entre el precio (0,08 = 8 %).
        index: Índice de su barrio.
        offer: Si el fondo buitre ha hecho una oferta por él (la ficha).
    """
    out = ["chollo"] if yearly_yield >= CHOLLO_YIELD else []
    badge = market_badge(index)
    if badge in ("burbuja", "buitre"):
        out.append(badge)
    if offer and "buitre" not in out:
        out.append("buitre")
    return out


def listing_status(market: Market, key: str, user_id: int) -> tuple[str, str]:
    """`(etiqueta, tipo)` de un inmueble: `venta`, `parcial`, `tuyo`, `compartido` o `ajeno`."""
    holders = market.shares_of(key)
    free = market.free_shares(key)
    mine = holders.get(user_id, 0)
    if not holders:
        return "En venta", "venta"
    if mine == rules.SHARES:
        return "Tuyo entero", "tuyo"
    if mine:
        return f"Tienes el {mine} %", "compartido"
    if free:
        return f"Queda el {free} % libre", "parcial"
    return "Vendido", "ajeno"


def news_line(
    item: News, names: dict[int, str], *, titled: bool = True, market: Market | None = None
) -> str:
    """Una noticia en una línea.

    Args:
        titled: Si empieza por el inmueble (en la cartera sí; en su propia ficha, no).
        market: El mercado del servidor, para el título de su obra nueva.
    """
    tenant = TENANT_BY_KEY.get(item.tenant or "")
    text = NEWS_TEXT.get(item.kind, item.kind).format(
        who=_name(names, item.user_id) if item.user_id else "Alguien",
        tenant=tenant.name if tenant else "El inquilino",
        amount=format_amount(item.amount),
    )
    listing = (market or Market()).listing(item.listing)
    vpo = VPO_BY_KEY.get(item.listing.removeprefix(VPO_PREFIX))
    title = listing.title if listing is not None else vpo.name if vpo is not None else ""
    where = f"{title}: " if titled and title else ""
    return f"{item.day.strftime('%d/%m')} · {where}{text}"


#: Cómo se dice lo que cambia un inquilino (`housing.TenantFactors`), de mejor a peor.
RISK_TEXT = {"bajo": "Riesgo de impago bajo", "medio": "Riesgo de impago medio",
             "alto": "Riesgo de impago alto"}  # fmt: skip


def _scale(value: float, low: str, mid: str, high: str) -> str:
    """Un multiplicador relativo (1 es la media) en tres palabras."""
    if value < rules.RISK_LOW:
        return low
    if value > rules.RISK_HIGH:
        return high
    return mid


def news_box(news: BarrioNews | None) -> State | None:
    """La noticia de un barrio para las pantallas, o `None`."""
    if news is None:
        return None
    return {"key": news.key, "title": news.title, "blurb": news.blurb}


def tenant_card(
    tenant_key: str,
    listing: Listing,
    level: str,
    index: float,
    *,
    last_rent: int = 0,
    until: date | None = None,
    slot: int = 0,
) -> State:
    """La tarjeta de un inquilino (un candidato o el que vive en el piso).

    Args:
        last_rent: Renta del contrato anterior del inmueble (para la reducción).
        until: Hasta cuándo espera, si es un candidato.
        slot: Su número en la pantalla de candidatos (1 a 3).
    """
    tenant = TENANT_BY_KEY[tenant_key]
    factors = rules.tenant_factors(tenant_key, level, listing.kind)
    rent_level = RENT_LEVELS.get(level, RENT_LEVELS["referencia"])
    rent = rules.asking_rent(listing, rent_level, index)
    reduction = rules.contract_reduction(
        listing, level, index=index, rent=rent, last_rent=last_rent,
        young=rules.is_young(tenant_key),
    )  # fmt: skip
    lease = rules.Lease(level=level, tenant=tenant_key, rent=rent, status=rules.RENTED)
    risk = rules.risk_tier(factors)
    return {
        "slot": slot,
        "key": tenant.key,
        "name": tenant.name,
        "emoji": tenant.emoji,
        "age": f"{tenant.age} años" if tenant.age else "",
        "young": rules.is_young(tenant_key),
        "traits": list(tenant.traits),
        "risk": risk,
        "risk_text": RISK_TEXT[risk],
        "stay": _scale(factors.stay, "Se queda mucho", "Estabilidad normal", "Se va pronto"),
        "wear": _scale(factors.wear, "Cuida la casa", "Desgaste normal", "Desgasta mucho"),
        "insurable": rules.insurable(listing, lease),
        "line": tenant.line,
        "rent": format_amount(rent) + "/mes",
        "reduction": _pct(reduction, 0) if reduction else "",
        "until": until.strftime("%d/%m") if until else "",
    }


def map_state(
    market: Market, user_id: int, name: str, cash: int, today: date | None = None
) -> State:
    """El mapa: cada barrio con su índice, lo que queda libre, si tienes algo y si hay noticia."""
    islands = []
    for island, island_name in ISLANDS.items():
        pins = []
        for barrio in BARRIOS:
            if barrio.island != island:
                continue
            items = market.listings_of(barrio.key)
            index = market.index(barrio.key)
            pins.append(
                {
                    "key": barrio.key,
                    "name": barrio.name,
                    "theme": barrio.theme,
                    "x": barrio.x,
                    "y": barrio.y,
                    "change": _change(index),
                    "up": index > 1.0005,
                    "down": index < 0.9995,
                    "tense": rules.tense(index),
                    "owned": any(user_id in market.holdings.get(i.key, {}) for i in items),
                    "free": sum(market.free_shares(i.key) for i in items),
                    "from": format_amount(min(rules.price(i, index) for i in items)),
                    "news": bool(today and market.news_of(barrio.key, today)),
                }
            )
        islands.append({"key": island, "name": island_name, "barrios": pins})
    worth = sum(market.value(h) for h in market.of_member(user_id))
    cheapest = [f"{pin['name']}: desde {pin['from']}" for i in islands for pin in i["barrios"]]
    headlines = [
        f"📰 {b.name}: {item.title}"
        for b in BARRIOS
        if today and (item := market.news_of(b.key, today)) is not None
    ]
    return {
        "screen": "mapa",
        "title": "PISOS DE JOVANI",
        "subtitle": "El mercado inmobiliario de Canarias",
        "user": {"name": name, "cash": format_amount(cash), "worth": format_amount(worth)},
        "islands": islands,
        "headlines": headlines,
        "footer": "Elige un barrio en el desplegable · precios reales × 10",
        "fallback": [
            "Pisos de Jovani: el mercado de Canarias",
            f"{name}: {format_amount(cash)} en efectivo, {format_amount(worth)} en ladrillo",
            *headlines,
            *cheapest,
        ],
    }


def _card(market: Market, listing: Listing, user_id: int, names: dict[int, str]) -> State:
    if market.split_mode(listing.key) == rules.FLOORS:
        return _floors_card(market, listing, user_id, names)
    index = market.index(listing.barrio)
    status, status_kind = listing_status(market, listing.key, user_id)
    lease = market.leases.get(listing.key)
    holders = market.shares_of(listing.key)
    value = rules.price(listing, index)
    rent = rules.reference_rent(listing, index)
    return {
        "key": listing.key,
        "barrio_key": listing.barrio,
        "theme": BARRIO_BY_KEY[listing.barrio].theme,
        "kind": listing.kind,
        "kind_name": KINDS[listing.kind].name,
        "title": listing.title,
        "m2": f"{listing.m2} m²",
        "floor": listing.floor,
        "energy": listing.energy,
        "price": format_amount(value),
        "share_price": format_amount(rules.share_price(listing, index)),
        "rent": format_amount(rent) + "/mes",
        "yield": _pct(rent * 12 / value),
        "badges": listing_badges(rent * 12 / value, index),
        "status": status,
        "status_kind": status_kind,
        "free": market.free_shares(listing.key),
        "owners": [
            {"name": _name(names, m), "shares": s, "mine": m == user_id}
            for m, s in sorted(holders.items(), key=lambda kv: -kv[1])
        ],
        "lease": STATUS_TEXT.get(lease.status, "") if lease else "",
        "lease_kind": lease.status if lease else "",
    }


def _parts(units: list[Listing] | tuple[Listing, ...]) -> str:
    """Lo que sale de dividir un inmueble, en palabras: «un local, 3 viviendas y 3 trasteros»."""
    locales = sum(1 for u in units if u.kind in rules.BUSINESS_SPACE_KINDS)
    storage = sum(1 for u in units if u.kind == "trastero")
    homes = len(units) - locales - storage
    words = [
        f"{n} {one if n == 1 else many}"
        for n, one, many in (
            (locales, "local", "locales"), (homes, "vivienda", "viviendas"),
            (storage, "trastero", "trasteros"),
        )
        if n
    ]  # fmt: skip
    text = words[0] if len(words) == 1 else ", ".join(words[:-1]) + " y " + words[-1]
    return text.replace("1 local", "un local", 1)


def _each(units: list[Listing] | tuple[Listing, ...]) -> str:
    """«cada una» si solo salen viviendas; si hay locales o trasteros, «cada uno»."""
    mixed = any(u.kind in rules.BUSINESS_SPACE_KINDS or u.kind == "trastero" for u in units)
    return "cada uno" if mixed else "cada una"


def _floors_card(market: Market, listing: Listing, user_id: int, names: dict[int, str]) -> State:
    """La tarjeta de un inmueble dividido por plantas: lo que valen y piden todas juntas, y de
    quién son (las participaciones de cada dueño, en tanto por ciento del total)."""
    units = [market.listing(u.key) or u for u in market.units_of(listing.key)]
    index = market.index(listing.barrio)
    value = sum(rules.price(u, index) for u in units)
    rent = sum(rules.reference_rent(u, index) for u in units)
    owned: dict[int, int] = {}
    for unit in units:
        for member, shares in market.shares_of(unit.key).items():
            owned[member] = owned.get(member, 0) + shares
    free_units = sum(1 for u in units if not market.shares_of(u.key))
    mine = sum(1 for u in units if user_id in market.shares_of(u.key))
    if mine:
        status = f"Tienes {mine} de {len(units)}"
        kind = "compartido" if mine < len(units) else "tuyo"
    elif free_units:
        status, kind = f"{free_units} de {len(units)} en venta", "venta"
    else:
        status, kind = "Vendidas", "ajeno"
    total = rules.SHARES * len(units)
    return {
        "key": listing.key,
        "barrio_key": listing.barrio,
        "theme": BARRIO_BY_KEY[listing.barrio].theme,
        "kind": listing.kind,
        "kind_name": KINDS[listing.kind].name,
        "title": listing.title,
        "m2": f"{listing.m2} m²",
        "floor": listing.floor,
        "energy": listing.energy,
        "price": format_amount(value),
        "share_price": format_amount(value // total),
        "rent": format_amount(rent) + "/mes",
        "yield": _pct(rent * 12 / value),
        "badges": [],
        "status": status,
        "status_kind": kind,
        "free": sum(market.free_shares(u.key) for u in units) * rules.SHARES // total,
        "owners": [
            {"name": _name(names, m), "shares": s * rules.SHARES // total, "mine": m == user_id}
            for m, s in sorted(owned.items(), key=lambda kv: -kv[1])
        ],
        "lease": "En dos" if rules.halves(listing) else f"{rules.floors_of(listing)} plantas",
        # «dividido» (en dos) o «plantas» (por plantas): la escena pone su cinta.
        "lease_kind": "dividido" if rules.halves(listing) else rules.FLOORS,
        "split": 0 if rules.halves(listing) else rules.floors_of(listing),
    }


def solar_card(barrio_key: str) -> State:
    """El hueco de la obra nueva de un barrio que aún no la tiene: un solar con su cartel.

    Ocupa la última tarjeta para que el barrio salga siempre con `BARRIO_SLOTS`; no se
    compra ni tiene botón.
    """
    return {
        "key": f"{SOLAR_PREFIX}{barrio_key}",
        "barrio_key": barrio_key,
        "theme": BARRIO_BY_KEY[barrio_key].theme,
        "kind": "solar",
        "kind_name": "Solar",
        "title": "Solar: próximamente obra nueva",
        "sign": "Aquí levantará su promoción la rama Ladrillo de pala",
    }


def barrio_state(
    market: Market,
    barrio_key: str,
    user_id: int,
    names: dict[int, str],
    today: date | None = None,
) -> State:
    """Un barrio con sus nueve inmuebles y su obra nueva (o el solar donde irá).

    Siempre `BARRIO_SLOTS` tarjetas: la décima es la promoción o, si aún no la hay, el
    solar (`solar_card`). Cada tarjeta corresponde a un botón, en el mismo orden.
    """
    barrio = BARRIO_BY_KEY[barrio_key]
    index = market.index(barrio_key)
    news = news_box(market.news_of(barrio_key, today)) if today else None
    cards = [_card(market, item, user_id, names) for item in market.listings_of(barrio_key)]
    if len(cards) < BARRIO_SLOTS:
        cards.append(solar_card(barrio_key))
    return {
        "screen": "barrio",
        "barrio": {
            "key": barrio.key,
            "name": barrio.name,
            "town": barrio.town,
            "island": ISLANDS[barrio.island],
            "blurb": barrio.blurb,
            "theme": barrio.theme,
            "theme_name": THEMES[barrio.theme],
            "index": f"{index:.3f}".replace(".", ","),
            "change": _change(index),
            "up": index > 1.0005,
            "down": index < 0.9995,
            "badge": market_badge(index),
            "price_m2": format_amount(round(barrio.price_m2 * index * 10)) + "/m²",
        },
        "cards": cards,
        "news": news,
        "footer": "Toca un inmueble para ver su ficha",
        "fallback": [
            f"{barrio.name} ({barrio.town})",
            barrio.blurb,
            *([f"📰 {news['title']}"] if news else []),
            *(
                f"{c['title']}: {c['price']} · {c['rent']} · {c['status']}"
                for c in cards
                if c["kind"] != "solar"
            ),
        ],
    }


def listing_state(
    market: Market,
    key: str,
    user_id: int,
    names: dict[int, str],
    news: list[News],
    today: date | None = None,
) -> State:
    """La ficha de un inmueble: precio, renta, gastos, dueños, inquilino y noticias."""
    listing = market.listing(key)
    if listing is None:
        raise KeyError(key)
    index = market.index(listing.barrio)
    card = _card(market, listing, user_id, names)
    lease = market.leases.get(key)
    level = RENT_LEVELS[lease.level if lease else "referencia"]
    costs = rules.monthly_costs(listing)
    rent = rules.reference_rent(listing, index)
    asked = rules.asking_rent(listing, level, index)
    tenant = TENANT_BY_KEY.get(lease.tenant or "") if lease else None
    holding: Holding | None = market.holdings.get(key, {}).get(user_id)
    net = rent - costs.total
    full = rules.purchase_quote(listing, rules.SHARES, index).total
    state: State = {
        "screen": "ficha",
        **card,
        "barrio": BARRIO_BY_KEY[listing.barrio].name,
        "town": BARRIO_BY_KEY[listing.barrio].town,
        "blurb": listing.blurb,
        "rent_ref": format_amount(rent) + "/mes",
        "rent_asked": format_amount(asked) + "/mes",
        "level": level.name,
        "level_key": level.key,
        "costs": [
            {"label": "Comunidad", "amount": format_amount(costs.community)},
            {"label": "Seguro del hogar", "amount": format_amount(costs.insurance)},
            {"label": "IBI", "amount": format_amount(costs.ibi)},
        ],
        "payback": f"{full / max(net, 1) / 30:.1f}".replace(".", ",") + " meses",
        "lease_status": lease.status if lease else "",
        # Un edificio entero no se alquila a una sola persona: son ocho viviendas.
        "tenant": (
            f"Ocho inquilinos (uno, {tenant.name[0].lower()}{tenant.name[1:]})"
            if tenant and listing.kind == "edificio"
            else tenant.name
            if tenant
            else ""
        ),
        "tenant_key": tenant.key if tenant else "",
        "tenant_line": tenant.line if tenant else "",
        "contract_rent": format_amount(lease.rent) + "/mes" if lease and lease.rent else "",
        "until": lease.until.strftime("%d/%m/%Y") if lease and lease.until else "",
        "leaving": LEAVING_TAG[lease.leaving].format(day=f"{lease.until:%d/%m}")
        if lease and lease.until and lease.leaving in LEAVING_TAG
        else "",
        "eviction_at": lease.eviction_at.strftime("%d/%m/%Y")
        if lease and lease.eviction_at
        else "",
        "arrears": format_amount(lease.arrears) if lease and lease.arrears else "",
        "mine": None,
        "news": [news_line(n, names, titled=False, market=market) for n in news[:4]],
        "footer": "1 día del bot = 1 mes del contrato · ITP 6,5 % al comprar",
    }
    if holding is not None:
        value = market.value(holding)
        state["mine"] = {
            "shares": holding.shares,
            "value": format_amount(value),
            "paid": format_amount(holding.cost),
            "gain": ("+" if value >= holding.cost else "−")
            + format_amount(abs(value - holding.cost)),
            "up": value >= holding.cost,
        }
    state["mortgage"] = mortgage_box(market.mortgages_of(user_id, key))
    home = None
    for member, (kind, ref) in market.homes.items():
        if ref == key and kind in ("propia", "alquiler"):
            home = (member, kind)
    state["home"] = (
        ("Vives aquí" if home[0] == user_id else f"Vive {_name(names, home[0])}")
        if home is not None and home[1] == "propia"
        else (
            ("Vives aquí de alquiler" if home[0] == user_id
             else f"De alquiler: {_name(names, home[0])}")
            if home is not None
            else ""
        )
    )  # fmt: skip
    state["bono"] = bool(lease and lease.member is not None and lease.bono)
    state["license"] = bool(lease and lease.license)
    state["tense"] = rules.tense(index)
    offer = market.offers.get((key, user_id))
    state["offer"] = (
        {"price": format_amount(offer[0]), "until": offer[1].strftime("%d/%m")} if offer else None
    )
    state["badges"] = listing_badges(
        rent * 12 / rules.price(listing, index), index, offer=bool(offer)
    )
    state["socimi"] = user_id in market.socimi
    # Fase 2: quién es el inquilino, el seguro de impago y la noticia del barrio.
    state["tenant_card"] = (
        tenant_card(tenant.key, listing, lease.level, index, last_rent=lease.last_rent)
        if tenant is not None and lease is not None
        else None
    )
    if (
        tenant is not None
        and lease is not None
        and lease.status in (rules.DEFAULTING, rules.EVICTING)
    ):
        state["tenant_line"] = tenant.unpaid or tenant.line
    state["insured"] = bool(lease and lease.insured)
    state["covered"] = bool(lease and listing and rules.covered(listing, lease))
    state["pick"] = lease.pick if lease else rules.PICK_AGENCY
    state["waiting"] = len(market.candidates_of(key, today)) if today else 0
    state["barrio_news"] = news_box(market.news_of(listing.barrio, today)) if today else None
    # Fase 4: el humor del inquilino, los imprevistos que esperan y las obras.
    state["mood"] = mood_text(lease.mood) if tenant is not None and lease is not None else ""
    state["mood_value"] = lease.mood if lease is not None else rules.MOOD_BASE
    state["decisions"] = len(market.decisions.get(key, ()))
    state["works_until"] = (
        lease.works_until.strftime("%d/%m") if lease is not None and lease.works_until else ""
    )
    state["reformed"] = bool(market.reforms.get(key))
    # Fase 5: la nota del vacacional y hasta cuándo vale la licencia.
    state["rating"] = rating_text(lease) if lease is not None else ""
    state["superhost"] = bool(lease and rules.superhost(lease.rating, lease.reviews))
    state["license_until"] = (
        lease.license_until.strftime("%d/%m/%Y")
        if lease is not None and lease.license and lease.license_until
        else ""
    )
    state["fallback"] = [
        f"{listing.title} ({card['m2']}, {listing.floor})",
        f"{state['barrio']}: {card['price']} · {card['share_price']} la participación",
        f"Renta de referencia {state['rent_ref']} · pide {state['rent_asked']}",
        f"{card['status']} · {card['lease'] or 'en venta'}",
        listing.blurb,
    ]
    return state


def mortgage_box(mortgages: list) -> State | None:  # list[Mortgage]
    """Lo que se enseña de las hipotecas de un inmueble (o `None` si no tiene)."""
    if not mortgages:
        return None
    owed = sum(m.owed for m in mortgages)
    payment = sum(m.payment for m in mortgages)
    unpaid = sum(m.unpaid_amount for m in mortgages)
    return {
        "owed": format_amount(owed),
        "payment": format_amount(payment) + "/mes",
        "months": f"{max(m.months_left for m in mortgages)} meses",
        "rate": _pct(mortgages[0].rate, 2),
        "unpaid": format_amount(unpaid) if unpaid else "",
        "unpaid_count": max(m.unpaid_count for m in mortgages),
        "aval": any(m.aval for m in mortgages),
    }


def portfolio_keys(market: Market, user_id: int) -> list[str]:
    """Los inmuebles de 🔑 Mis pisos, en el orden en que salen: su VPO (si tiene) y luego
    cada participación. La imagen y el desplegable de cada página salen de esta lista."""
    keys = [f"{VPO_PREFIX}{unit}"] if (unit := market.vpo_of(user_id)) is not None else []
    keys += [h.listing for h in market.of_member(user_id) if market.listing(h.listing)]
    return keys


def portfolio_pages(count: int) -> int:
    """Cuántas páginas ocupan `count` inmuebles en 🔑 Mis pisos (al menos una)."""
    return max(1, -(-count // PORTFOLIO_PAGE))


def portfolio_page(market: Market, user_id: int, page: int) -> tuple[int, int, list[str]]:
    """La página `page` de 🔑 Mis pisos, contando desde 0 y metida en las que hay.

    Returns:
        `(página, páginas, claves de esa página)`: la página ya corregida (si vendió y
        sobran páginas, la última), cuántas hay y los inmuebles que enseña.
    """
    keys = portfolio_keys(market, user_id)
    pages = portfolio_pages(len(keys))
    page = min(max(page, 0), pages - 1)
    return page, pages, keys[page * PORTFOLIO_PAGE : (page + 1) * PORTFOLIO_PAGE]


def portfolio_state(
    market: Market,
    user_id: int,
    name: str,
    cash: int,
    news: list[News],
    names: dict[int, str],
    effort: float | None = None,
    today: date | None = None,
    page: int = 0,
) -> State:
    """Lo que tiene un miembro: cada inmueble, lo que cobra esta noche y las noticias.

    Lo de esta noche es la misma cifra que da `saldo` (`tonight_rent`): lo ya cobrado no
    sale aquí, que con dos cifras de rentas no se sabía cuál era cuál.

    Las cifras de arriba (efectivo, ladrillo, deuda, candidatos) son de todo lo que tiene;
    la lista, solo de una página (`PORTFOLIO_PAGE` inmuebles).

    Args:
        effort: Si vive de alquiler en el piso de otro miembro, la parte de su sueldo que
            se le va en la renta (`housing.effort`), para la línea de dónde vive.
        today: Hoy, para los candidatos que esperan.
        page: La página de la lista, desde 0 (`portfolio_page` la corrige si sobra).
    """
    items = []
    for holding in market.of_member(user_id):
        listing = market.listing(holding.listing)
        if listing is None:
            continue
        lease = market.leases.get(holding.listing)
        rent = tonight_rent(market, holding)
        items.append(
            {
                "key": listing.key,
                "barrio_key": listing.barrio,
                "theme": BARRIO_BY_KEY[listing.barrio].theme,
                "kind": listing.kind,
                "title": listing.title,
                "barrio": BARRIO_BY_KEY[listing.barrio].name,
                "shares": holding.shares,
                "value": format_amount(market.value(holding)),
                "status": STATUS_TEXT.get(lease.status, "") if lease else "",
                "status_kind": lease.status if lease else "",
                "rent": format_amount(rent) + "/mes" if rent else "—",
                "mortgage": _mortgage_line(market.mortgages_of(user_id, listing.key)),
                "home": market.homes.get(user_id) == ("propia", listing.key),
                "insured": bool(lease and lease.insured),
                "waiting": len(market.candidates_of(listing.key, today)) if today else 0,
            }
        )
    unit = market.vpo_of(user_id)
    if unit is not None:
        spec = VPO_BY_KEY[unit]
        items.insert(
            0,
            {
                "key": f"{VPO_PREFIX}{unit}",
                "kind": "vpo",
                "title": spec.name,
                "barrio": spec.town,
                "shares": rules.SHARES,
                "value": format_amount(spec.price),
                "badges": ["vpo"],
                "status": "Tu vivienda habitual (VPO)",
                "status_kind": rules.HABITUAL,
                "rent": "—",
                "mortgage": _mortgage_line(market.mortgages_of(user_id, f"{VPO_PREFIX}{unit}")),
                "home": True,
            },
        )
    worth = sum(market.value(h) for h in market.of_member(user_id))
    worth += VPO_BY_KEY[unit].price if unit is not None else 0
    # Lo que entra esta noche (la misma renta que da `saldo`) y lo que sale de ella.
    tonight = _Night()
    for holding in market.of_member(user_id):
        tonight.add(market, holding, today)
    if unit is not None:
        tonight.add_vpo(market, unit)
    debt = sum(m.owed for m in market.mortgages_of(user_id)) + market.bank_debts.get(user_id, 0)
    home = market.homes.get(user_id)
    waiting = sum(1 for i in items if i.get("waiting"))
    page, pages, shown = portfolio_page(market, user_id, page)
    visible = set(shown)
    items = [i for i in items if i["key"] in visible]
    state: State = {
        "screen": "cartera",
        "user": {"name": name, "cash": format_amount(cash), "worth": format_amount(worth)},
        "items": items,
        "page": page + 1,
        "pages": pages,
        "news": [news_line(n, names, market=market) for n in news[:6]],
        "empty": not items,
        "debt": format_amount(debt) if debt else "",
        "bank_debt": format_amount(market.bank_debts[user_id])
        if market.bank_debts.get(user_id)
        else "",
        "euribor": "Euríbor " + _pct(market.euribor, 2),
        "living": _living(market, home, names, effort),
        "socimi": user_id in market.socimi,
        "waiting": waiting,
        "tonight": {
            "rent": format_amount(tonight.rent),
            "ibi": format_amount(tonight.ibi),
            "costs": format_amount(tonight.costs),
            "net": format_amount(tonight.net),
            "loss": tonight.net < 0,
        },
        "footer": "Antes del IRPF, que se retiene al cobrar · Euríbor " + _pct(market.euribor, 2),
    }
    state["fallback"] = [
        f"Los pisos de {name}: {format_amount(worth)}",
        f"Esta noche: {state['tonight']['rent']} de renta, {state['tonight']['net']} netos",
        *(f"{i['title']} ({i['shares']} %): {i['value']} · {i['status']}" for i in items),
        *([f"Página {page + 1} de {pages}"] if pages > 1 else []),
    ] if items else [f"{name} no tiene ningún piso. Generación inquilina."]  # fmt: skip
    return state


def rent_keys(market: Market, key: str) -> tuple[str, ...]:
    """Las claves que pagan renta a los dueños de `key`: él o, si va por habitaciones, cada una."""
    if market.split_mode(key) == rules.ROOMS:
        return tuple(unit.key for unit in market.units_of(key))
    return (key,)


def tonight_rent(market: Market, holding: Holding) -> int:
    """La renta bruta que le toca a unas participaciones esta noche: solo la de quien está al
    día (un impago no paga) y, en un piso por habitaciones, la de cada habitación alquilada."""
    rent = sum(
        lease.rent
        for key in rent_keys(market, holding.listing)
        if (lease := market.leases.get(key)) is not None and lease.status == rules.RENTED
    )
    return rent * holding.shares // rules.SHARES


@dataclass(slots=True)
class _Night:
    """Lo que un miembro cobra y paga esta noche por sus inmuebles, antes del IRPF.

    Las mismas cuentas que el cierre (`HousingRepository._close_listing_in`): la renta de los
    inquilinos al día (`tonight_rent`), el IBI del mes (con el recargo del piso vacío de un
    gran tenedor), la comunidad, los seguros con su impuesto, el IGIC del vacacional y los
    atrasos, que también salen de la renta. Los imprevistos de esta noche no se saben.
    """

    rent: int = 0
    ibi: int = 0
    costs: int = 0

    @property
    def net(self) -> int:
        return self.rent - self.ibi - self.costs

    def add(self, market: Market, holding: Holding, today: date | None) -> None:
        self.rent += tonight_rent(market, holding)
        ibi = costs = 0
        for key in rent_keys(market, holding.listing):
            listing, lease = market.listing(key), market.leases.get(key)
            if listing is None or lease is None:
                continue
            month = rules.monthly_costs(listing)
            month_ibi, home = month.ibi, month.insurance
            boss = market.manager(key)
            if (
                today is not None and lease.status == rules.VACANT and lease.since is not None
                and (today - lease.since).days >= rules.EMPTY_DAYS
                and boss is not None and market.whole_homes(boss) >= rules.EMPTY_OWNER_HOMES
            ):  # fmt: skip
                month_ibi = round(month_ibi * rules.EMPTY_IBI_FACTOR)
            if (
                today is not None
                and lease.home_claim_until is not None
                and (lease.home_claim_until >= today)
            ):
                home = round(home * rules.HOME_CLAIM_SURCHARGE)
            cover = rules.premium(listing, lease)
            ips = round(home * rules.IPS_RATE) + round(cover * rules.IPS_RATE)
            vacation = (
                rules.vacation_igic(lease.rent)
                if lease.level == rules.VACATION_LEVEL and lease.status == rules.RENTED
                else 0
            )
            ibi += month_ibi
            costs += month.community + home + cover + ips + vacation
            # Los atrasos ya son solo de los dueños: se reparten entre ellos, como en `saldo`.
            owners = market.shares_of(key)
            self.ibi += rules.split(lease.arrears_ibi, owners).get(holding.user_id, 0)
            self.costs += rules.split(lease.arrears - lease.arrears_ibi, owners).get(
                holding.user_id, 0
            )
        self.ibi += ibi * holding.shares // rules.SHARES
        self.costs += costs * holding.shares // rules.SHARES

    def add_vpo(self, market: Market, unit: str) -> None:
        spec, owner = VPO_BY_KEY[unit], market.vpo[unit]
        month = rules.vpo_costs(spec.price, spec.community)
        self.ibi += month.ibi + owner.arrears_ibi
        self.costs += month.community + owner.arrears - owner.arrears_ibi


def _mortgage_line(mortgages: list) -> str:
    box = mortgage_box(mortgages)
    if box is None:
        return ""
    late = f" · debes {box['unpaid']}" if box["unpaid"] else ""
    return f"Hipoteca {box['owed']} · {box['payment']}{late}"


def _living(
    market: Market,
    home: tuple[str, str] | None,
    names: dict[int, str],
    effort: float | None = None,
) -> str:
    if home is None:
        return "Vives con tus padres"
    kind, ref = home
    if kind == "vpo":
        return f"Vives en tu {VPO_BY_KEY[ref].name}"
    item = market.listing(ref)
    title = item.title if item is not None else "un piso"
    if kind == "alquiler":
        owners = market.shares_of(ref)
        boss = max(owners, key=owners.get) if owners else None
        landlord = _name(names, boss) if boss is not None else "el mercado"
        burden = ""
        if effort is not None:
            # La tasa de esfuerzo, como la mide el Banco de España (sobreesfuerzo del 30 %).
            alarm = " (sobreesfuerzo)" if effort > rules.OVERBURDEN else ""
            burden = f" · el {round(effort * 100)} % de tu sueldo{alarm}"
        return f"De alquiler en «{title}» (casero: {landlord}){burden}"
    return f"Vives en «{title}»"


def vpo_state(
    market: Market,
    user_id: int,
    names: dict[int, str],
    *,
    eligible: bool,
    reason: str,
    next_draw: str,
    income: int,
) -> State:
    """Las VPO: de quién es cada una, el sorteo de los lunes y lo tuyo.

    Args:
        eligible: Si puede apuntarse (sin casa y sin pasar del límite de ingresos).
        reason: Por qué no, si no puede.
        next_draw: Cuándo es el próximo sorteo («lunes 19/10»).
        income: Su neto medio de nómina al día (lo que mira el límite).
    """
    rate = market.euribor + rules.MORTGAGE_SPREAD
    units = []
    for unit in VPO_UNITS:
        owner = market.vpo.get(unit.key)
        reserved = market.reservations.get(unit.key)
        if owner is not None:
            mine = owner.owner_id == user_id
            status = "Tuya" if mine else f"De {_name(names, owner.owner_id)}"
            kind = "tuyo" if mine else "ajeno"
        elif reserved is not None:
            mine = reserved[0] == user_id
            who = "ti" if mine else _name(names, reserved[0])
            status = f"Reservada para {who} hasta el {reserved[1].strftime('%d/%m')}"
            kind = "compartido" if mine else "parcial"
        else:
            status, kind = "Libre: entra en el próximo sorteo", "venta"
        units.append(
            {
                "key": unit.key,
                "name": unit.name,
                "town": unit.town,
                "island": ISLANDS[unit.island],
                "m2": f"{unit.m2} m²",
                "price": format_amount(unit.price),
                "payment": format_amount(rules.installment(unit.price, rate)) + "/mes",
                "status": status,
                "status_kind": kind,
                "blurb": unit.blurb,
            }
        )
    signed = user_id in market.signups
    reservation = market.reservation_of(user_id)
    state: State = {
        "screen": "vpo",
        "title": "VIVIENDA PROTEGIDA",
        "subtitle": "Sorteo cada lunes · hipoteca al 100 % con aval del Estado",
        "units": units,
        "user": {
            "signed_up": signed,
            "eligible": eligible,
            "reason": reason,
            "reservation": VPO_BY_KEY[reservation].name if reservation else "",
            "owns": market.vpo_of(user_id) is not None,
            "income": format_amount(income) + "/día",
            "income_ok": income <= rules.vpo_income_cap(),
            "home_ok": not market.owns_home(user_id)
            and market.homes.get(user_id, ("alquiler", ""))[0] == "alquiler",
            "cap": format_amount(rules.vpo_income_cap()) + "/día",
            "signups": len(market.signups),
        },
        "next_draw": next_draw,
        "euribor": "Euríbor " + _pct(market.euribor, 2),
        "footer": "Precio tasado · IGIC al tipo cero · no se alquila · se devuelve al Estado",
    }
    state["fallback"] = [
        "Vivienda protegida: sorteo cada lunes",
        f"Próximo sorteo: {next_draw} · {len(market.signups)} apuntados",
        *(f"{u['name']}: {u['price']} · {u['status']}" for u in units),
    ]
    return state


def checkout_state(
    listing: Listing,
    *,
    selling: bool,
    lines: list[tuple[str, int, str]],
    balance: int,
    after: int,
    note: str,
    stamp: str | None = None,
) -> State:
    """La caja de una compra o una venta.

    Args:
        lines: `(concepto, importe, tipo)` en orden; `tipo` es `normal`, `tax`
            (impuesto: lleva a Perro Sanxe) o `total`.
        balance: Saldo antes.
        after: Saldo después.
        stamp: Sello encima (`PAGADO`, `VENDIDO`) cuando ya está hecho.
    """
    return {
        "screen": "caja",
        "selling": selling,
        "key": listing.key,
        "barrio_key": listing.barrio,
        "theme": BARRIO_BY_KEY[listing.barrio].theme,
        "kind": listing.kind,
        "title": listing.title,
        "barrio": BARRIO_BY_KEY[listing.barrio].name,
        "lines": [
            {"label": label, "amount": format_amount(amount), "type": kind}
            for label, amount, kind in lines
        ],
        "balance": format_amount(balance),
        "after": format_amount(after),
        "short": after < 0,
        "note": note,
        "stamp": stamp,
        "footer": "Escritura ante notario · el dinero de verdad no existe",
        "fallback": [
            f"{'Venta' if selling else 'Compra'}: {listing.title}",
            *(f"{label}: {format_amount(amount)}" for label, amount, _kind in lines),
            f"Saldo: {format_amount(balance)} → {format_amount(after)}",
        ],
    }


def gestion_state(
    market: Market, key: str, user_id: int, names: dict[int, str], today: date
) -> State:
    """🧰 Gestionar: quién elige al inquilino, el seguro de impago, el desahucio y la noticia.

    Lo ve cualquier dueño; los botones que cambian algo son de quien decide (la mayoría).
    """
    listing = market.listing(key)
    if listing is None:
        raise KeyError(key)
    index = market.index(listing.barrio)
    lease = market.leases.get(key, rules.Lease())
    boss = market.manager(key)
    level = RENT_LEVELS.get(lease.level, RENT_LEVELS["referencia"])
    rent = lease.rent or rules.asking_rent(listing, level, index)
    premium = round(rent * rules.premium_rate(lease.level))
    contract = lease.status in (rules.RENTED, rules.DEFAULTING, rules.EVICTING)
    cover = rules.covered(listing, lease)
    if not lease.insured:
        insurance = "Sin seguro de impago"
    elif not contract:
        insurance = "Contratado: empieza a pagar con el próximo contrato"
    elif cover:
        insurance = f"Contratado: cubre a este inquilino ({lease.claims} meses pagados)"
    else:
        insurance = "Contratado, pero no cubre a este inquilino (no pasa el estudio de solvencia)"
    fee = rules.eviction_fee(rent)
    tense = rules.tense(index)
    tips = []
    if listing.kind in rules.HOME_KINDS:
        if tense and lease.last_rent == 0:
            tips.append("Zona tensionada y primer alquiler: con alguien de 18 a 35 años, el 70 %")
        elif tense:
            tips.append(
                "Zona tensionada: bajar la renta más de un 5 % en el próximo contrato da el 90 %"
            )
        if lease.status == rules.VACANT:
            tips.append(
                "Si se alquila tras una semana vacía, deduce "
                f"{format_amount(vacant_rental_deduction(100))} en la Renta (art. 16)"
            )
    waiting = market.candidates_of(key, today)
    state: State = {
        "screen": "gestion",
        "key": listing.key,
        "barrio_key": listing.barrio,
        "theme": BARRIO_BY_KEY[listing.barrio].theme,
        "kind": listing.kind,
        "title": listing.title,
        "barrio": BARRIO_BY_KEY[listing.barrio].name,
        "boss": _name(names, boss) if boss is not None else "",
        "decides": boss == user_id,
        "pick": lease.pick,
        "pick_text": (
            "Eliges tú entre los candidatos que llegan"
            if lease.pick == rules.PICK_SELF
            else "La gestora elige al inquilino"
        ),
        "waiting": len(waiting),
        "status": STATUS_TEXT.get(lease.status, ""),
        "dwelling": listing.kind in rules.HOME_KINDS,
        "insured": lease.insured,
        "covered": cover,
        "insurance": insurance,
        "premium": format_amount(premium) + "/mes",
        "premium_rate": _pct(rules.premium_rate(lease.level), 1),
        "fee": format_amount(fee),
        "fee_covered": cover,
        "tips": tips,
        "news": news_box(market.news_of(listing.barrio, today)),
        "footer": "El seguro paga desde el 2.º mes sin cobrar, hasta 12, y el abogado",
    }
    state["fallback"] = [
        f"Gestionar: {listing.title} ({state['barrio']})",
        state["pick_text"] + (f" · {len(waiting)} candidatos esperando" if waiting else ""),
        f"Seguro de impago: {insurance} · prima {state['premium']} + 8 % de primas",
        f"Desahucio: abogado y procurador {state['fee']} + IGIC"
        + (" (lo paga el seguro)" if cover else ""),
        *tips,
    ]
    return state


def candidates_state(market: Market, key: str, today: date) -> State:
    """👥 Candidatos: los que esperan en un piso vacío cuyo dueño elige al inquilino."""
    listing = market.listing(key)
    if listing is None:
        raise KeyError(key)
    index = market.index(listing.barrio)
    lease = market.leases.get(key, rules.Lease())
    until = {tenant: day for tenant, day in market.candidates.get(key, ())}
    cards = [
        tenant_card(
            tenant, listing, lease.level, index, last_rent=lease.last_rent,
            until=until.get(tenant), slot=slot,
        )
        for slot, tenant in enumerate(market.candidates_of(key, today), start=1)
    ]  # fmt: skip
    state: State = {
        "screen": "candidatos",
        "key": listing.key,
        "barrio_key": listing.barrio,
        "theme": BARRIO_BY_KEY[listing.barrio].theme,
        "kind": listing.kind,
        "title": listing.title,
        "barrio": BARRIO_BY_KEY[listing.barrio].name,
        "cards": cards,
        "empty": not cards,
        "level": RENT_LEVELS.get(lease.level, RENT_LEVELS["referencia"]).name,
        "footer": f"Cada candidato espera {rules.CANDIDATE_DAYS} días · llegan cada noche",
    }
    state["fallback"] = [
        f"Candidatos para {listing.title}",
        *(
            f"{c['slot']}. {c['name']}{', ' + c['age'] if c['age'] else ''}: {c['risk_text']}"
            f" · {c['rent']}"
            for c in cards
        ),
    ] if cards else [f"Nadie espera en {listing.title}: llegan cada noche."]  # fmt: skip
    return state


def _remaining(seconds: float) -> str:
    """Lo que falta para que acabe una subasta, en horas y minutos («3 h 05 min»)."""
    seconds = max(0, int(seconds))
    hours, minutes = seconds // 3600, seconds % 3600 // 60
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"


def auctions_state(
    market: Market, user_id: int, names: dict[int, str], now: float, selected: int | None = None
) -> State:
    """🔨 Subastas: las abiertas del servidor, con la mejor puja y lo que falta.

    Args:
        now: Ahora (epoch), para lo que le queda a cada una.
        selected: La subasta elegida en el desplegable (la de los botones).
    """
    items = []
    for auction in market.auctions:
        listing = market.listing(auction.listing)
        if listing is None:
            continue
        index = market.index(listing.barrio)
        value = rules.share_price(listing, index) * auction.shares
        least = rules.next_bid(auction.best_bid, auction.min_bid)
        items.append(
            {
                "id": auction.id,
                "key": listing.key,
                "barrio_key": listing.barrio,
                "theme": BARRIO_BY_KEY[listing.barrio].theme,
                "kind": listing.kind,
                "title": listing.title,
                "barrio": BARRIO_BY_KEY[listing.barrio].name,
                "shares": auction.shares,
                "judicial": auction.judicial,
                "seller": _name(names, auction.seller),
                "mine": auction.seller == user_id and not auction.division,
                "division": auction.division,
                "value": format_amount(value),
                "min_bid": format_amount(auction.min_bid),
                "next_bid": format_amount(least),
                "best_bid": format_amount(auction.best_bid) if auction.best_bid else "",
                "best_bidder": _name(names, auction.best_bidder) if auction.best_bidder else "",
                "leading": auction.best_bidder == user_id,
                "bids": auction.bids,
                "left": _remaining(auction.ends_at - now),
                "selected": auction.id == selected,
                "pct": _pct(auction.best_bid / value, 0) if auction.best_bid and value else "",
            }
        )
    state: State = {
        "screen": "subastas",
        "title": "SUBASTAS",
        "subtitle": "Entre miembros y judiciales · un día · la última hora alarga una hora",
        "items": items[:6],
        "more": max(0, len(items) - 6),
        "empty": not items,
        "footer": "Lo pujado espera en el depósito con su ITP y se devuelve a quien superan",
    }
    state["fallback"] = [
        "Subastas de pisos",
        *(
            f"{'⚖️ ' if i['judicial'] else ''}{i['title']} ({i['shares']} %): "
            f"{i['best_bid'] or 'sin pujas'} · mínima {i['next_bid']} · quedan {i['left']}"
            for i in items
        ),
    ] if items else ["Ninguna subasta abierta ahora mismo."]  # fmt: skip
    return state


def mood_text(mood: int) -> str:
    """El humor del inquilino (`Lease.mood`) en una palabra."""
    if mood >= 85:
        return "Encantado"
    if mood >= 60:
        return "Contento"
    if mood >= 40:
        return "Regular"
    if mood >= 20:
        return "Molesto"
    return "Harto"


def reforms_state(market: Market, key: str, user_id: int) -> State:
    """🏗️ Reformas: las que se le pueden hacer a un inmueble, lo que cuestan y lo que dan."""
    listing = market.listing(key)
    if listing is None:
        raise KeyError(key)
    index = market.index(listing.barrio)
    lease = market.leases.get(key, rules.Lease())
    done = market.reforms.get(key, {})
    items = []
    for reform in REFORMS:
        if listing.kind not in reform.kinds:
            continue
        quote = rules.reform_quote(listing, reform, index, market.ipc)
        made = done.get(reform.key)
        status = "available" if made is None else "done" if made[2] else "works"
        items.append(
            {
                "key": reform.key,
                "name": reform.name,
                "emoji": reform.emoji,
                "blurb": reform.blurb,
                "cost": format_amount(quote.cost),
                "total": format_amount(quote.total),
                "taxes": format_amount(quote.igic + quote.icio + quote.license),
                "days": f"{reform.days} días",
                "rent": "+" + _pct(reform.rent_bonus, 0) + " de renta",
                "breakdowns": reform.breakdowns < 1 or reform.damp < 1,
                "energy": reform.energy,
                "rehab": quote.rehab,
                "status": status,
                "until": made[1].strftime("%d/%m") if made else "",
            }
        )
    state: State = {
        "screen": "reformas",
        "key": listing.key,
        "barrio_key": listing.barrio,
        "theme": BARRIO_BY_KEY[listing.barrio].theme,
        "kind": listing.kind,
        "title": listing.title,
        "barrio": BARRIO_BY_KEY[listing.barrio].name,
        "items": items,
        "whole": market.shares_of(key).get(user_id, 0) == rules.SHARES,
        "vacant": lease.status == rules.VACANT,
        "works": lease.status == rules.OBRAS,
        "works_until": lease.works_until.strftime("%d/%m") if lease.works_until else "",
        "rent_bonus": "+" + _pct(listing.rent_bonus, 0),
        "value_bonus": "+" + _pct(listing.value_bonus, 0),
        "energy": listing.energy,
        "rehab_until": listing.rehab_until.strftime("%d/%m") if listing.rehab_until else "",
        "footer": "IGIC 7 % · ICIO 4 % · licencia de obra · el valor sube el 60 % del coste",
    }
    state["fallback"] = [
        f"Reformas: {listing.title} ({state['barrio']})",
        f"Renta {state['rent_bonus']} · valor {state['value_bonus']} · certificado "
        f"{listing.energy}",
        *(f"{i['name']}: {i['total']} con impuestos · {i['days']} · {i['rent']}" for i in items),
    ]
    return state


def units_state(
    market: Market, key: str, user_id: int, names: dict[int, str], today: date | None = None
) -> State:
    """🏢 Las plantas (o 🛏️ las habitaciones) de un inmueble dividido, como las tarjetas de un
    barrio: cada tarjeta corresponde a un botón, en el mismo orden."""
    listing = market.listing(key)
    mode = market.split_mode(key)
    if listing is None or mode is None:
        raise KeyError(key)
    units = [market.listing(u.key) or u for u in market.units_of(key)]
    cards = [_card(market, unit, user_id, names) for unit in units]
    leases = [market.leases.get(u.key, rules.Lease()) for u in units]
    rented = sum(1 for lease in leases if lease.status in (rules.RENTED, rules.DEFAULTING))
    asking = sum(rules.reference_rent(u, market.index(listing.barrio)) for u in units)
    waiting = sum(len(market.candidates_of(u.key, today)) for u in units) if today else 0
    if mode == rules.FLOORS:
        word = "plantas"
        summary = (
            f"{_parts(units).capitalize()}, {_each(units)} con sus dueños y su inquilino: se "
            "venden por separado."
        )
        free = sum(1 for u in units if not market.shares_of(u.key))
        third = {"label": "En venta", "value": str(free)}
    else:
        word = "habitaciones"
        summary = (
            f"{len(units)} habitaciones, cada una con su inquilino. El piso se compra y se "
            "vende entero."
        )
        third = {"label": "Candidatos", "value": str(waiting)}
    state: State = {
        "screen": "plantas",
        "key": listing.key,
        "barrio_key": listing.barrio,
        "theme": BARRIO_BY_KEY[listing.barrio].theme,
        "kind": listing.kind,
        "title": listing.title,
        "barrio": BARRIO_BY_KEY[listing.barrio].name,
        "mode": mode,
        "mode_name": (
            "Por habitaciones"
            if mode == rules.ROOMS
            else "Dividido en dos"
            if rules.halves(listing)
            else "Dividido por plantas"
        ),
        "summary": summary,
        "stats": [
            {"label": "Alquiladas", "value": f"{rented} de {len(units)}"},
            {"label": "Renta de referencia", "value": format_amount(asking) + "/mes"},
            third,
        ],
        "cards": cards,
        "footer": f"Toca una de las {word} para ver su ficha",
    }
    state["fallback"] = [
        f"{listing.title} ({state['barrio']}): {state['mode_name'].lower()}",
        summary,
        *(f"{c['title']}: {c['rent']} · {c['lease'] or c['status']}" for c in cards),
    ]
    return state


def _unit_rows(units: tuple[Listing, ...], index: float) -> list[State]:
    """Las filas de lo que sale al dividir, con los trasteros juntos en una (son iguales)."""
    rows: list[State] = [
        {
            "label": u.floor,
            "kind": u.kind,
            "kind_name": KINDS[u.kind].name,
            "m2": f"{u.m2} m²",
            "rent": format_amount(rules.reference_rent(u, index)) + "/mes",
        }
        for u in units
        if u.kind != "trastero"
    ]
    storage = [u for u in units if u.kind == "trastero"]
    if storage:
        rows.append(
            {
                "label": f"{len(storage)} trasteros" if len(storage) > 1 else "Trastero",
                "kind": "trastero",
                "kind_name": KINDS["trastero"].name,
                "m2": f"{storage[0].m2} m² cada uno" if len(storage) > 1 else f"{storage[0].m2} m²",
                "rent": format_amount(rules.reference_rent(storage[0], index)) + "/mes",
            }
        )
    return rows


def split_state(market: Market, key: str, user_id: int) -> State:
    """🏢 Dividir: lo que cuesta y lo que da dividir un inmueble por plantas o alquilarlo por
    habitaciones, y por qué no se puede ahora si no se puede."""
    listing = market.listing(key)
    base = market.base(key)
    if listing is None or base is None:
        raise KeyError(key)
    index = market.index(listing.barrio)
    lease = market.leases.get(key, rules.Lease())
    holders = market.shares_of(key)
    rent = rules.reference_rent(listing, index)
    value = rules.price(listing, index)
    options = []
    for mode in rules.SPLIT_MODES:
        if rules.split_problem(base, mode, promotion=key in market.promotions) is not None:
            continue
        quote = rules.split_quote(listing, mode, index, market.ipc)
        parent = base if mode == rules.FLOORS else listing
        units = rules.split_units(parent, mode, quote.units, quote.cost)
        if mode == rules.FLOORS:
            # Las reformas hechas se copian a cada planta y le suman lo mismo (`improved`).
            units = tuple(
                replace(
                    u,
                    rent_bonus=u.rent_bonus + listing.rent_bonus,
                    value_bonus=u.value_bonus + listing.value_bonus,
                )
                for u in units
            )
        after = sum(rules.reference_rent(u, index) for u in units)
        worth = sum(rules.price(u, index) for u in units) if mode == rules.FLOORS else value
        if lease.status == rules.BY_ROOMS:
            problem = "Ya se alquila por habitaciones."
        elif key in market.splits or rules.is_room(key):
            problem = "Ya está dividido."
        elif sum(holders.values()) != rules.SHARES:
            problem = "Hay participaciones sin dueño: tiene que ser todo de miembros."
        elif user_id not in holders:
            problem = "No es tuyo."
        elif lease.status != rules.VACANT or lease.member is not None:
            problem = "Tiene que estar vacío."
        elif market.decisions.get(key):
            problem = "Antes decide los imprevistos que tiene pendientes."
        else:
            problem = ""
        floors = mode == rules.FLOORS
        options.append(
            {
                "mode": mode,
                "emoji": "🏢" if floors else "🛏️",
                "name": (
                    "Alquilar por habitaciones"
                    if not floors
                    else "Dividir en dos"
                    if rules.halves(base)
                    else "Dividir por plantas"
                ),
                "blurb": (
                    f"{_parts(units).capitalize()} independientes: {_each(units)} con su "
                    "inquilino, sus dueños y su hipoteca, y se venden por separado."
                    if floors
                    else f"{quote.units} habitaciones amuebladas con cerradura, cada una con su "
                    "inquilino. El piso se sigue vendiendo entero."
                ),
                "units": _unit_rows(units, index),
                "cost": format_amount(quote.cost),
                "taxes": format_amount(quote.total - quote.cost),
                "total": format_amount(quote.total),
                "days": f"{quote.days} días sin alquilar",
                "rent_after": format_amount(after) + "/mes",
                "rent_change": f"{after / rent - 1:+.0%}".replace("%", " %") if rent else "",
                "value_after": format_amount(worth),
                "notes": (
                    [
                        "Para siempre: no se puede deshacer",
                        "Cada vivienda cuenta para gran tenedor (desde 5); locales y trasteros, no",
                        "Con varios dueños, la junta lo aprueba por unanimidad",
                    ]
                    if floors
                    else [
                        "Se deshace cuando estén todas vacías",
                        "Cada habitación paga "
                        f"{format_amount(rules.monthly_room_community())} al mes de comunidad "
                        "y suministros",
                        "Contratos de un año, sin seguro de impago ni vacacional",
                    ]
                ),
                "problem": problem,
            }
        )
    state: State = {
        "screen": "dividir",
        "key": listing.key,
        "barrio_key": listing.barrio,
        "theme": BARRIO_BY_KEY[listing.barrio].theme,
        "kind": listing.kind,
        "title": listing.title,
        "barrio": BARRIO_BY_KEY[listing.barrio].name,
        "rent": format_amount(rent) + "/mes",
        "value": format_amount(value),
        "rooms": lease.status == rules.BY_ROOMS,
        "options": options,
        "footer": (
            "IGIC 7 % · ICIO 4 % · licencia de obra · AJD 0,75 % de la escritura"
            if any(o["mode"] == rules.FLOORS for o in options)
            else "Los muebles llevan IGIC del 7 %: no es obra, ni ICIO ni licencia"
            if options
            else ""
        ),
    }
    state["fallback"] = [
        f"Dividir: {listing.title} ({state['barrio']})",
        f"Entero pide {state['rent']} y vale {state['value']}",
        *(
            f"{o['name']}: {o['total']} · {o['days']} · {o['rent_after']} ({o['rent_change']})"
            + (f" · {o['problem']}" if o["problem"] else "")
            for o in options
        ),
    ]
    return state


#: Lo que se hace en una unidad, según su contrato (para el corte del edificio).
USE_TEXT = {
    rules.VACANT: "Vacío",
    rules.RENTED: "Alquilado",
    rules.DEFAULTING: "No paga",
    rules.EVICTING: "Desahucio",
    rules.SQUATTED: "Okupado",
    rules.HABITUAL: "Vive su dueño",
    rules.OWN_BUSINESS: "Negocio de su dueño",
    rules.OBRAS: "En obras",
    rules.BY_ROOMS: "Por habitaciones",
}


def building_units(market: Market, building: Building) -> list[Listing]:
    """Las unidades de un edificio que se ven en su corte: las suyas y, si el edificio entero
    está dividido por plantas, sus plantas en su lugar."""
    out = []
    for unit in building.units:
        if market.split_mode(unit.key) == rules.FLOORS:
            out += [market.listing(u.key) or u for u in market.units_of(unit.key)]
        elif (listing := market.listing(unit.key)) is not None:
            out.append(listing)
    return out


def _use(market: Market, key: str) -> tuple[str, str]:
    """`(qué se hace en una unidad, su tipo)`: «En venta», «Alquilado: Médica residente»…"""
    lease = market.leases.get(key)
    if not market.shares_of(key):
        return "En venta", "venta"
    if lease is None:
        return "Vacío", rules.VACANT
    text = USE_TEXT.get(lease.status, "")
    if lease.status in (rules.RENTED, rules.DEFAULTING) and lease.member is not None:
        return f"{text}: un miembro", lease.status
    tenant = TENANT_BY_KEY.get(lease.tenant or "")
    if tenant is not None and lease.status in (rules.RENTED, rules.DEFAULTING):
        return f"{text}: {tenant.name}", lease.status
    if lease.status == rules.BY_ROOMS:
        rooms = [market.leases.get(u.key) for u in market.units_of(key)]
        busy = sum(1 for r in rooms if r is not None and r.status == rules.RENTED)
        return f"{text} ({busy} de {len(rooms)})", lease.status
    return text, lease.status


def _owner(market: Market, key: str, user_id: int, names: dict[int, str]) -> str:
    """De quién es una unidad: «Tuya», «De Ana», «De Ana y 2 más» o «En venta»."""
    holders = market.shares_of(key)
    if not holders:
        return "En venta"
    if user_id in holders and len(holders) == 1:
        return "Tuya"
    first = max(holders, key=lambda m: (m == user_id, holders[m]))
    who = "ti" if first == user_id else _name(names, first)
    return f"De {who}" + (f" y {len(holders) - 1} más" if len(holders) > 1 else "")


def _unit_place(
    market: Market, building: Building, unit: Listing, units: list[Listing]
) -> tuple[int, str]:
    """`(planta, mitad)` de una unidad en el corte.

    La mitad de algo dividido en dos se queda en la planta de su padre (`izda.`/`dcha.`); una
    planta de un edificio entero dividido va a su número de planta (y sus trasteros, al sótano).
    """
    levels = {u.key: u.level for u in building.units}
    if unit.key in levels:
        return levels[unit.key], ""
    parent = rules.parent_key(unit.key)
    if parent in levels and building.kind != "edificio":
        return levels[parent], "izda." if unit.key.endswith("~1") else "dcha."
    if unit.kind == "trastero":
        return -1, ""
    return [u for u in units if u.kind != "trastero"].index(unit), ""


def _divisible(market: Market, unit: Listing) -> bool:
    """Si una unidad se podría dividir (por plantas o en dos) y aún no lo está."""
    if unit.key in market.splits:
        return False
    base = market.base(unit.key) or unit
    return rules.split_problem(base, rules.FLOORS) is None


def _level_name(level: int, building: Building) -> str:
    """Cómo se llama una planta en el corte: «Sótano» (o «2.º sótano» si hay más de uno),
    «Bajo», «2.ª planta», «Pantalán»…"""
    if level < 0:
        deep = any(u.level < -1 for u in building.units)
        return f"{-level}.º sótano" if deep else "Sótano"
    if building.kind == PANTALAN:
        return "Pantalán"
    if level == 0:
        return "Bajo" if building.floors > 1 else "Planta"
    return f"{level}.ª planta"


def _building_summary(building: Building, units: int) -> str:
    """La línea de arriba del corte: «4 plantas y sótano · 9 unidades», «5 amarres»…"""
    things = {
        PANTALAN: ("amarre", "amarres"),
        "aparcamiento": ("plaza", "plazas"),
        "guardamuebles": ("trastero", "trasteros"),
        "complejo": ("bungalow", "bungalows"),
        "poligono": ("nave", "naves"),
    }
    if building.kind in things:
        one, many = things[building.kind]
        return f"{units} {one if units == 1 else many}"
    if building.kind == "edificio":
        listing = LISTING_BY_KEY.get(building.featured)
        return f"Edificio entero ({listing.floor if listing else ''})" + (
            f" · dividido en {units}" if units > 1 else " · sin dividir"
        )
    if building.kind == "casa":
        return "Casa de una vivienda"
    floors = f"{building.floors} {'planta' if building.floors == 1 else 'plantas'}"
    basement = " y sótano" if any(u.level < 0 for u in building.units) else ""
    return f"{floors}{basement} · {units} unidades"


def streets_state(
    market: Market, barrio_key: str, user_id: int, today: date | None = None
) -> State:
    """🗺️ Calles: el plano del barrio con sus tres calles y los edificios de cada una (de 3 a
    5 por calle: el centro, más denso que el campo). Es lo que se ve al elegir un barrio.

    Cada edificio lleva su número y un botón con el mismo, en el orden en que se lee el plano:
    calle a calle y de izquierda a derecha (`map_order`). Arriba va lo del barrio: su índice,
    el precio medio y la noticia, si hay. Al final de cada calle, su solar (`street["solar"]`):
    vacío, en obras o con lo construido.
    """
    place = {b.key: n for n, b in enumerate(map_order(barrio_key), start=1)}
    streets = []
    for street in streets_of(barrio_key):
        buildings = []
        for building in buildings_of(street.key):
            units = building_units(market, building)
            # La fachada del plano: la del destacado o, si no tiene, la de su primera vivienda.
            featured = market.listing(building.featured) if building.featured else None
            face = featured or next(
                (u for u in units if u.kind not in ("garaje", "trastero", "local")), units[0]
            )
            buildings.append(
                {
                    "key": building.key,
                    "featured": building.featured,
                    "slot": place[building.key],
                    "number": building.number,
                    "kind": building.kind,
                    "kind_name": building.name,
                    "featured_kind": face.kind,
                    "face": face.key,
                    "floors": building.floors,
                    "units": len(units),
                    "free": sum(1 for u in units if not market.shares_of(u.key)),
                    "mine": sum(1 for u in units if user_id in market.shares_of(u.key)),
                    "rented": sum(
                        1
                        for u in units
                        if (lease := market.leases.get(u.key)) is not None
                        and lease.status in (rules.RENTED, rules.DEFAULTING, rules.BY_ROOMS)
                    ),
                }
            )
        streets.append(
            {
                "key": street.key,
                "name": street.name,
                "blurb": street.blurb,
                "buildings": buildings,
                "solar": _solar_tag(market, f"{street.key}{SOLAR_SUFFIX}", user_id, today),
            }
        )
    state: State = {
        "screen": "calles",
        "barrio": _barrio_head(market, barrio_key),
        "news": news_box(market.news_of(barrio_key, today)) if today else None,
        "streets": streets,
        "footer": "Toca un edificio para verlo por dentro · 🏗️ Solares: obra nueva de la rama "
        "Ladrillo",
    }
    state["fallback"] = [
        f"{state['barrio']['name']}: sus calles",
        *(
            f"{st['name']}: "
            + ", ".join(f"{b['slot']} · {b['kind_name']} ({b['units']})" for b in st["buildings"])
            for st in streets
        ),
    ]
    return state


def building_state(
    market: Market, key: str, user_id: int, names: dict[int, str], today: date | None = None
) -> State:
    """🏢 Un edificio en corte, de arriba abajo: cada planta con sus unidades, qué se hace en
    cada una y de quién es. Las unidades van numeradas como en el desplegable. Vale también
    para lo construido en un solar (`Market.building`)."""
    building = market.building(key)
    if building is None:
        raise KeyError(key)
    street = next(s for s in streets_of(building.barrio) if s.key == building.street)
    units = building_units(market, building)
    index = market.index(building.barrio)
    levels: dict[int, list[State]] = {}
    for slot, unit in enumerate(units, start=1):
        level, half = _unit_place(market, building, unit, units)
        use, use_kind = _use(market, unit.key)
        levels.setdefault(level, []).append(
            {
                "slot": slot,
                "key": unit.key,
                "label": next((u.label for u in building.units if u.key == unit.key), unit.floor),
                "kind": unit.kind,
                "kind_name": KINDS[unit.kind].name,
                "m2": f"{unit.m2} m²",
                "use": use,
                "use_kind": use_kind,
                "owner": _owner(market, unit.key, user_id, names),
                "mine": user_id in market.shares_of(unit.key),
                "price": format_amount(rules.price(unit, index)),
                "rent": format_amount(rules.reference_rent(unit, index)) + "/mes",
                "featured": unit.key == building.featured,
                # Un ático caro (sobreático o con vistas, 9.16.0), o una de sus mitades: la escena
                # le pinta su terraza.
                "premium": is_penthouse(rules.parent_key(unit.key) or unit.key),
                # Para que el dibujo diga la verdad: el ancho de cada hueco, por sus metros; si es
                # la mitad de algo dividido en dos («izda.»/«dcha.»), y si se podría dividir.
                "area": unit.m2,
                "half": half,
                "divisible": _divisible(market, unit),
                "waiting": len(market.candidates_of(unit.key, today)) if today else 0,
            }
        )
    floors = [
        {
            "level": level,
            "label": _level_name(level, building),
            "units": levels[level],
            # El bajo de un bloque tiene su portal y la escalera: lo que no es de ninguna unidad.
            "portal": level == 0 and building.kind == "bloque",
        }
        for level in sorted(levels, reverse=True)
    ]
    mine = sum(1 for u in units if user_id in market.shares_of(u.key))
    state: State = {
        "screen": "edificio",
        "key": building.key,
        "barrio_key": building.barrio,
        "theme": BARRIO_BY_KEY[building.barrio].theme,
        "kind": building.kind,
        "kind_name": building.name,
        "title": f"{street.name}, {building.number}",
        "barrio": BARRIO_BY_KEY[building.barrio].name,
        "featured": building.featured,
        "summary": _building_summary(building, len(units)),
        "floors": floors,
        "stats": [
            {"label": "Unidades", "value": str(len(units))},
            {"label": "Tuyas", "value": str(mine)},
            {
                "label": "En venta",
                "value": str(sum(1 for u in units if not market.shares_of(u.key))),
            },
        ],
        "footer": "Elige una unidad en el desplegable para ver su ficha",
    }
    state["fallback"] = [
        f"{building.name} · {state['title']} ({state['barrio']})",
        *(
            f"{f['label']}: "
            + ", ".join(
                f"{u['slot']} {u['label']} ({u['kind_name']}, {u['use']})" for u in f["units"]
            )
            for f in floors
        ),
    ]
    return state


def _barrio_head(market: Market, barrio_key: str) -> State:
    """La cabecera de un barrio en 🗺️ Calles y 🏗️ Solares: su índice y el precio medio."""
    barrio = BARRIO_BY_KEY[barrio_key]
    index = market.index(barrio_key)
    return {
        "key": barrio.key,
        "name": barrio.name,
        "town": barrio.town,
        "island": ISLANDS[barrio.island],
        "theme": barrio.theme,
        "theme_name": THEMES[barrio.theme],
        "blurb": barrio.blurb,
        "index": f"{index:.3f}".replace(".", ","),
        "change": _change(index),
        "up": index > 1.0005,
        "down": index < 0.9995,
        "badge": market_badge(index),
        "price_m2": format_amount(round(barrio.price_m2 * index * 10)) + "/m²",
    }


#: Lo que se ve de un solar según cómo está.
SOLAR_FREE, SOLAR_WORKS, SOLAR_BUILT = "libre", "obras", "hecho"


def _solar_tag(market: Market, key: str, user_id: int, today: date | None) -> State:
    """Lo mínimo de un solar para el plano de calles y las pestañas de 🏗️ Solares."""
    solar = SOLAR_BY_KEY[key]
    street = next(s for s in streets_of(solar.barrio) if s.key == solar.street)
    work = market.solars.get(key)
    if work is None:
        return {"key": key, "street": street.name, "number": solar.number, "status": SOLAR_FREE,
                "emoji": "🚧", "mine": False, "text": "Solar libre"}  # fmt: skip
    project = PROJECT_BY_KEY[work.project]
    building = today is None or work.until <= today
    return {
        "key": key,
        "street": street.name,
        "number": solar.number,
        "status": SOLAR_BUILT if building else SOLAR_WORKS,
        "emoji": project.emoji,
        "mine": work.user_id == user_id,
        "text": project.name if building else f"En obras hasta el {work.until:%d/%m}",
    }


def bubble_box(supply: int, demand: int) -> State:
    """El termómetro de la burbuja: la obra empezada frente a lo que compran los miembros."""
    pressure = solar_rules.bubble_pressure(supply, demand)
    odds = solar_rules.bubble_odds(supply, demand)
    level = "calma" if odds == 0 else "tension" if odds < 0.25 else "burbuja"
    names = {"calma": "Calma", "tension": "Tensión", "burbuja": "Burbuja"}
    return {
        "level": level,
        "name": names[level],
        "pressure": f"{pressure:.2f}".replace(".", ","),
        "odds": f"{odds * 100:.0f} %",
        "text": f"Obra empezada en 4 semanas: {format_amount(supply)} · lo que han comprado los "
        f"miembros: {format_amount(demand)}",
    }


def solar_state(
    market: Market,
    key: str,
    user_id: int,
    level: int,
    names: dict[int, str],
    today: date,
    gauge: tuple[int, int] = (0, 0),
) -> State:
    """🏗️ Solares: un solar del barrio, con pestañas para los otros dos.

    Libre: los proyectos que caben, con lo que cuestan, lo que valdrán y lo que pide cada uno.
    En obras o hecho: quién lo construye, qué, hasta cuándo y cuántas unidades le quedan.

    Args:
        level: El puesto de quien mira en la rama Ladrillo (0 si no está en ella).
        gauge: `(obra empezada, lo comprado)` de las últimas semanas, para el termómetro de la
            burbuja (`HousingService.bubble_gauge`).
    """
    solar = SOLAR_BY_KEY[key]
    index = market.index(solar.barrio)
    work = market.solars.get(key)
    tabs = [
        {**_solar_tag(market, s.key, user_id, today), "slot": n, "selected": s.key == key}
        for n, s in enumerate(solars_of(solar.barrio), start=1)
    ]
    tag = next(t for t in tabs if t["selected"])
    busy = next(
        (w for w in market.solars.values() if w.user_id == user_id and w.until > today), None
    )
    projects: list[State] = []
    built: State | None = None
    if work is None:
        for project in PROJECTS:
            quote = solar_rules.build_quote(solar, project, index, market.ipc)
            reason = ""
            if not solar_rules.allowed(solar, project):
                reason = f"El barrio no deja {project.floors} plantas"
            elif level < project.level:
                reason = f"Pide P{project.level} en Ladrillo"
            elif busy is not None:
                reason = f"Tu obra acaba el {busy.until:%d/%m}"
            projects.append(
                {
                    "key": project.key,
                    "emoji": project.emoji,
                    "name": project.name,
                    "units": quote.units,
                    "floors": project.floors,
                    "days": f"{project.days} días",
                    "cost": format_amount(quote.total),
                    "value": format_amount(quote.value),
                    "margin": f"{quote.margin / quote.total * 100:+.0f} %",
                    "level": f"P{project.level}",
                    "can": not reason,
                    "reason": reason,
                }
            )
    else:
        project = PROJECT_BY_KEY[work.project]
        _building, units = solar_units(work.solar, work.project)
        days = max(1, (work.until - work.started).days)
        done = min(1.0, max(0.0, (today - work.started).days / days))
        built = {
            "project": project.name,
            "emoji": project.emoji,
            "owner": "Tuyo" if work.user_id == user_id else _name(names, work.user_id),
            "units": len(units),
            "mine": sum(1 for u in units if user_id in market.shares_of(u.key)),
            "for_sale": sum(1 for u in units if not market.shares_of(u.key)),
            "value": format_amount(sum(rules.price(u, index) for u in units)),
            "cost": format_amount(work.cost),
            "started": f"{work.started:%d/%m}",
            "until": f"{work.until:%d/%m}",
            "progress": round(done, 2),
        }
    state: State = {
        "screen": "solar",
        "barrio": _barrio_head(market, solar.barrio),
        "solar": {**tag, "m2": f"{solar.m2} m² de suelo"},
        "tabs": tabs,
        "projects": projects,
        "built": built,
        "bubble": bubble_box(*gauge),
        "level": f"P{level} en Ladrillo" if level else "No estás en la rama Ladrillo",
        "footer": "Construye quien ficha en la rama Ladrillo de la obra en `pala` · una obra a "
        "la vez · 60 solares en todo el servidor",
    }
    state["fallback"] = [
        f"🏗️ {tag['street']}, {solar.number} ({state['barrio']['name']}): {tag['text']}",
        *(
            f"{p['emoji']} {p['name']}: {p['units']} unidades, {p['cost']} → {p['value']} "
            f"({p['margin']}, {p['days']}){' · ' + p['reason'] if p['reason'] else ''}"
            for p in projects
        ),
        *(
            [
                f"{built['emoji']} {built['project']} de {built['owner']}: {built['units']} "
                f"unidades, hasta el {built['until']}"
            ]
            if built
            else []
        ),
        f"Burbuja: {state['bubble']['name']} ({state['bubble']['odds']} este lunes)",
    ]
    return state


def decisions_state(market: Market, key: str, user_id: int) -> State:
    """⚠️ Imprevistos: los que esperan decisión en un inmueble, con sus formas de arreglarlo."""
    listing = market.listing(key)
    if listing is None:
        raise KeyError(key)
    lease = market.leases.get(key, rules.Lease())
    tenant = TENANT_BY_KEY.get(lease.tenant or "")
    holders = market.shares_of(key)
    boss = market.manager(key)
    items = []
    for slot, decision in enumerate(market.decisions.get(key, ())[:3], start=1):
        options = []
        for choice in rules.DECISIONS[decision.event]:
            cost = round(decision.cost * choice.share) + choice.flat
            if choice.claim:
                cost = min(decision.cost, rules.HOME_CLAIM_EXCESS)
            options.append(
                {
                    "key": choice.key,
                    "label": choice.label,
                    "cost": format_amount(cost),
                    "mood": choice.mood,
                    "relapse": _pct(choice.relapse, 0) if choice.relapse else "",
                    "chosen": decision.choice == choice.key,
                }
            )
        items.append(
            {
                "slot": slot,
                "id": decision.id,
                "event": decision.event,
                "title": NEWS_TEXT.get(decision.event, decision.event).format(
                    amount=format_amount(decision.cost), who="", tenant=""
                ),
                "complaint": tenant.complaint if tenant else "",
                "until": decision.until.strftime("%d/%m"),
                "options": options,
            }
        )
    state: State = {
        "screen": "imprevistos",
        "key": listing.key,
        "barrio_key": listing.barrio,
        "theme": BARRIO_BY_KEY[listing.barrio].theme,
        "kind": listing.kind,
        "title": listing.title,
        "barrio": BARRIO_BY_KEY[listing.barrio].name,
        "items": items,
        "empty": not items,
        "decides": user_id in holders and (boss is None or boss == user_id),
        "tenant": tenant.name if tenant else "",
        "mood": mood_text(lease.mood) if tenant else "",
        "mood_value": lease.mood,
        "footer": "Si no decides en dos días, la gestora lo arregla bien · se paga por la noche",
    }
    state["fallback"] = [
        f"Imprevistos: {listing.title}",
        *(
            f"{i['title']} · " + " / ".join(f"{o['label']} {o['cost']}" for o in i["options"])
            for i in items
        ),
    ] if items else ["No hay nada pendiente en este piso."]  # fmt: skip
    return state


# -- Fase 5: reseñas del vacacional --------------------------------------------------------


def rating_text(lease: rules.Lease) -> str:
    """La nota media del vacacional («4,6 ★ · 12 reseñas»), o vacío si no hay reseñas."""
    if not lease.reviews:
        return ""
    count = "1 reseña" if lease.reviews == 1 else f"{lease.reviews} reseñas"
    return f"{lease.rating:.1f}".replace(".", ",") + f" ★ · {count}"


def reviews_state(market: Market, key: str, furniture: frozenset[str]) -> State:
    """⭐ Reseñas: la nota del vacacional, lo que mueve la ocupación y las últimas reseñas.

    Args:
        furniture: Los muebles del vacacional de quien mira (valen si es quien decide).
    """
    listing = market.listing(key)
    if listing is None:
        raise KeyError(key)
    lease = market.leases.get(key, rules.Lease())
    theme = BARRIO_BY_KEY[listing.barrio].theme
    reviews = []
    for review in market.reviews.get(key, ())[:5]:
        guest = TENANT_BY_KEY.get(review.guest)
        lines = REVIEW_LINES[review.stars]
        reviews.append(
            {
                "stars": review.stars,
                "guest": guest.name if guest else review.guest,
                "emoji": guest.emoji if guest else "🧳",
                "line": lines[review.line % len(lines)],
                "day": review.day.strftime("%d/%m"),
            }
        )
    factor = rules.rating_factor(lease.rating, lease.reviews)
    state: State = {
        "screen": "resenas",
        "key": listing.key,
        "barrio_key": listing.barrio,
        "theme": theme,
        "kind": listing.kind,
        "title": listing.title,
        "barrio": BARRIO_BY_KEY[listing.barrio].name,
        "rating": rating_text(lease),
        "rating_value": lease.rating,
        "count": lease.reviews,
        "superhost": rules.superhost(lease.rating, lease.reviews),
        "occupancy": "×" + f"{factor:.2f}".replace(".", ","),
        "license": lease.license,
        "license_until": lease.license_until.strftime("%d/%m/%Y")
        if lease.license and lease.license_until
        else "",
        "reviews": reviews,
        "furniture": [
            {"emoji": item.emoji, "name": item.name, "owned": item.key in furniture}
            for item in FURNITURE
        ],
        "furniture_stars": "+"
        + f"{furniture_stars(furniture, theme, None):.2f}".replace(".", ",")
        + " ★",
        "footer": (
            f"Superanfitrión: {rules.SUPERHOST_RATING:.1f}".replace(".", ",")
            + f" de media con {rules.SUPERHOST_REVIEWS} reseñas, +10 % de precio · "
            "limpieza y 15 % de plataforma por estancia"
        ),
    }
    state["fallback"] = [
        f"Reseñas: {listing.title}",
        state["rating"] or "Aún sin reseñas.",
        *(f"{'★' * r['stars']} {r['guest']}: {r['line']}" for r in reviews),
    ]
    return state


# -- Fase 6: junta de copropietarios -------------------------------------------------------

#: Cómo se dice cada mayoría.
THRESHOLD_TEXT = {
    rules.MAJORITY: "Mayoría",
    rules.THREE_FIFTHS: "3/5",
    rules.UNANIMITY: "Unanimidad",
}


def motion_text(kind: str, value: str, amount: int, names: dict[int, str]) -> str:
    """Lo que propone una votación, en una frase («Pedir la renta de mercado»)."""
    if kind == "renta":
        level = RENT_LEVELS.get(value)
        return f"Pedir la renta {level.name.lower()}" if level else "Cambiar la renta"
    if kind == "seguro":
        return "Contratar el seguro de impago" if value == "1" else "Quitar el seguro de impago"
    if kind == "eleccion":
        return (
            "Elegir nosotros al inquilino" if value == rules.PICK_SELF else "Que elija la gestora"
        )
    if kind == "administra":
        return f"Que administre {_name(names, int(value))}" if value.isdigit() else "Administrar"
    if kind == "derrama":
        return f"Derrama de {format_amount(amount)} al fondo de reserva"
    if kind == "reforma":
        reform = next((r for r in REFORMS if r.key == value), None)
        if reform is None:
            return "Una reforma"
        return (
            reform.name if reform.name.lower().startswith("reforma") else f"Reforma: {reform.name}"
        )
    if kind == "licencia":
        return f"Pedir la licencia vacacional ({format_amount(rules.LICENSE_FEE)})"
    if kind in rules.SPLIT_MODES:
        return rules.MOTION_BY_KEY[kind].name
    return "Vender el inmueble entero al mercado"


def junta_state(market: Market, key: str, user_id: int, names: dict[int, str], now: float) -> State:
    """🗳️ Junta: los dueños, quién administra, el fondo de reserva y lo que se vota."""
    listing = market.listing(key)
    if listing is None:
        raise KeyError(key)
    holders = market.shares_of(key)
    total = sum(holders.values())
    boss = market.manager(key)
    votes = []
    for slot, vote in enumerate(market.votes_of(key)[:4], start=1):
        motion = rules.MOTION_BY_KEY[vote.kind]
        against = sum(s for m, s in holders.items() if vote.ballots.get(m) is False)
        yes = sum(s for m, s in holders.items() if vote.ballots.get(m) is True)
        votes.append(
            {
                "slot": slot,
                "id": vote.id,
                "kind": vote.kind,
                "name": motion.name,
                "what": motion_text(vote.kind, vote.value, vote.amount, names),
                "proposer": _name(names, vote.proposer),
                "threshold": THRESHOLD_TEXT[motion.threshold],
                "law": motion.law,
                "yes": yes,
                "no": against,
                "silent": max(0, total - yes - against),
                "total": total,
                "passing": rules.passes(motion.threshold, total, against),
                "mine": vote.ballots.get(user_id),
                "left": _remaining(vote.ends_at - now),
            }
        )
    division = market.division_of(key)
    state: State = {
        "screen": "junta",
        "key": listing.key,
        "barrio_key": listing.barrio,
        "theme": BARRIO_BY_KEY[listing.barrio].theme,
        "kind": listing.kind,
        "title": listing.title,
        "barrio": BARRIO_BY_KEY[listing.barrio].name,
        "owners": [
            {"name": _name(names, m), "shares": s, "admin": m == boss, "me": m == user_id}
            for m, s in sorted(holders.items(), key=lambda item: (-item[1], item[0]))
        ],
        "admin": _name(names, boss) if boss is not None else "Nadie: no hay mayoría",
        "elected": market.admins.get(key) == boss and boss is not None,
        "reserve": format_amount(market.reserves.get(key, 0)),
        "votes": votes,
        "more": max(0, len(market.votes_of(key)) - 4),
        "empty": not votes,
        "division": {
            "min_bid": format_amount(division.min_bid),
            "best_bid": format_amount(division.best_bid) if division.best_bid else "",
            "left": _remaining(division.ends_at - now),
        }
        if division is not None
        else None,
        "footer": "24 horas por votación · quien no vota cuenta a favor (art. 17.8 LPH)",
    }
    state["fallback"] = [
        f"Junta: {listing.title} · administra {state['admin']} · fondo {state['reserve']}",
        *(
            f"{v['slot']}. {v['what']} ({v['threshold']}): sí {v['yes']} · no {v['no']} · "
            f"sin votar {v['silent']} · quedan {v['left']}"
            for v in votes
        ),
    ] if votes else ["No hay nada que votar. Propón algo en el desplegable."]  # fmt: skip
    return state


# -- Lo pendiente, para `saldo` -------------------------------------------------------


def _midnight(day: date) -> float:
    """El comienzo de `day` en hora canaria (epoch): cuando se cierra el día anterior."""
    return datetime.combine(day, time.min, TIMEZONE).timestamp()


def _place(market: Market, key: str) -> str:
    """El nombre de un inmueble o de una VPO (`vpo:<unidad>`) para una línea."""
    if key.startswith(VPO_PREFIX):
        vpo = VPO_BY_KEY.get(key.removeprefix(VPO_PREFIX))
        return f"tu VPO de {vpo.town}" if vpo is not None else "tu VPO"
    listing = market.listing(key)
    return listing.title if listing is not None else "un inmueble"


def _arrears_and_decisions(
    market: Market, user_id: int, key: str, items: list[Pending], tonight: float
) -> None:
    """Los gastos atrasados de un inmueble (o habitación) y sus imprevistos sin decidir."""
    lease = market.leases.get(key)
    if lease is not None and lease.arrears:
        part = rules.split(lease.arrears, market.shares_of(key)).get(user_id, 0)
        if part:
            items.append(
                Pending(
                    Side.PAY, "arrears", "🧾",
                    f"Gastos atrasados de {_place(market, key)}: {format_amount(part)} · "
                    "salen de la renta o del monedero",
                    part, tonight,
                )
            )  # fmt: skip
    if market.manager(key) == user_id:
        for decision in market.decisions.get(key, ()):
            if decision.choice is None:
                items.append(
                    Pending(
                        Side.PAY, "decision", "🔧",
                        f"Imprevisto en {_place(market, key)} sin decidir: si no eliges en "
                        "`pisos`, lo arregla la gestora",
                        decision.cost, _midnight(decision.until + timedelta(days=1)),
                    )
                )  # fmt: skip


def pending_items(
    market: Market, user_id: int, deposits: list[Deposit], today: date
) -> list[Pending]:
    """Lo que un miembro tiene pendiente en `pisos`, para `saldo`.

    Un día del juego es un mes de contrato y se cierra pasada la medianoche: cuotas,
    recibos, alquiler y rentas salen «esta noche». Las rentas van brutas, antes de
    gastos e IRPF, como en la cartera.

    Args:
        deposits: Sus fianzas en el depósito (`HousingRepository.deposits_of`).
        today: Hoy, en hora canaria.
    """
    tonight = _midnight(today + timedelta(days=1))
    items: list[Pending] = []
    for mortgage in market.mortgages_of(user_id):
        due = (mortgage.payment if mortgage.months_left > 0 else 0) + mortgage.unpaid_amount
        if not due:
            continue
        what = "Rehipoteca" if mortgage.remortgage else "Hipoteca"
        text = f"{what} de {_place(market, mortgage.listing)}: {format_amount(due)}"
        kind = "mortgage"
        if mortgage.unpaid_count:
            kind = "mortgage_unpaid"
            text += (
                f" · ⚠️ {mortgage.unpaid_count} cuotas sin pagar: con "
                f"{rules.EXECUTION_INSTALLMENTS}, el banco la ejecuta"
            )
        items.append(Pending(Side.PAY, kind, "🏦", text, due, tonight))
    for loan in market.loans_of(user_id):
        due = loan.monthly + loan.unpaid_amount
        if not due:
            continue
        # La primera cuota se cobra la noche siguiente a firmar (`Loan.first_day`).
        first = date.fromisoformat(loan.first_day)
        when = tonight if today >= first else _midnight(first + timedelta(days=1))
        if loan.corporate:
            # Lo paga la caja de su sociedad: él solo lo avala.
            items.append(
                Pending(
                    Side.HELD, "business_loan_guarantee", "🏢",
                    f"Avalas el préstamo #{loan.id} de tu sociedad: su caja paga "
                    f"{format_amount(due)} cada noche; si no paga {lending.DEFAULT_INSTALLMENTS} "
                    f"cuotas, el banco te reclama lo que quede ({format_amount(loan.owed)})",
                    loan.owed, None,
                )
            )  # fmt: skip
            continue
        kind = "loan"
        if loan.advance and loan.listing:
            what = f"Anticipo de rentas de {_place(market, loan.listing)}"
        elif loan.business_id is not None:
            what = f"Préstamo #{loan.id} de tu negocio"
        elif loan.purchase:
            kind = "financing"
            plan = "Paga en 3" if loan.kind == LOAN_BNPL else "Financiación"
            what = f"{plan} de {loan.listing or 'una compra'} (quedan {loan.months_left} cuotas)"
        else:
            what = f"Préstamo #{loan.id} del banco"
        text = f"{what}: {format_amount(due)}"
        if loan.unpaid_count:
            kind += "_unpaid"
            text += (
                f" · ⚠️ {loan.unpaid_count} cuotas sin pagar: con "
                f"{lending.DEFAULT_INSTALLMENTS}, vence entero"
            )
        items.append(Pending(Side.PAY, kind, "💳", text, due, when))
    for debt in market.guaranteed_by(user_id):
        # Lo que avala: no se paga, pero si el avalado deja de pagar, se lo reclaman.
        items.append(
            Pending(
                Side.HELD, "guarantee", "🤝",
                f"Avalas a <@{debt.user_id}>: si deja de pagar, el banco te reclama lo que "
                f"quede ({format_amount(debt.owed)}). Su cuota cuenta en tu 35 %",
                debt.owed, None,
            )
        )  # fmt: skip
    for (creditor, debtor), amount in market.member_debts.items():
        if debtor == user_id:
            items.append(
                Pending(
                    Side.PAY, "guarantee_debt", "🤝",
                    f"Debes {format_amount(amount)} a <@{creditor}>, que pagó por tu aval: se "
                    "cobra cada noche de lo que tengas y se embarga de tus nóminas",
                    amount, tonight,
                )
            )  # fmt: skip
        elif creditor == user_id:
            items.append(
                Pending(
                    Side.COLLECT, "guarantee_credit", "🤝",
                    f"<@{debtor}> te debe {format_amount(amount)} de lo que pagaste por su aval: "
                    "te lo devuelve cada noche de lo que tenga",
                    amount, tonight,
                )
            )  # fmt: skip
    if debt := market.bank_debts.get(user_id, 0):
        items.append(
            Pending(
                Side.PAY, "bank_debt", "🏦",
                f"Deuda con el banco: {format_amount(debt)} · se cobra cada noche de lo que "
                "tengas y se embarga de tus nóminas",
                debt, tonight,
            )
        )  # fmt: skip
    if (unit := market.vpo_of(user_id)) is not None:
        spec = VPO_BY_KEY[unit]
        costs = rules.vpo_costs(spec.price, spec.community)
        owner = market.vpo[unit]
        due = costs.community + costs.ibi + owner.arrears
        late = " y lo atrasado" if owner.arrears else ""
        items.append(
            Pending(
                Side.PAY, "vpo_bills", "🏢",
                f"Recibos de tu VPO: {format_amount(due)} (comunidad e IBI{late})", due, tonight,
            )
        )  # fmt: skip
    for key, lease in market.leases.items():
        if lease.member != user_id or lease.status not in (rules.RENTED, rules.DEFAULTING):
            continue
        text = (
            f"Alquiler de {_place(market, key)}: {format_amount(lease.rent)} · "
            "si no lo tienes entero, impago"
        )
        if lease.unpaid:
            text += f" · ⚠️ debes {lease.unpaid} meses"
        items.append(Pending(Side.PAY, "rent", "🔑", text, lease.rent, tonight))
    rents = 0
    for holding in market.of_member(user_id):
        rents += tonight_rent(market, holding)
        # Las habitaciones de un piso tienen sus propios atrasos e imprevistos.
        for key in dict.fromkeys((holding.listing, *rent_keys(market, holding.listing))):
            _arrears_and_decisions(market, user_id, key, items, tonight)
    for vote in market.votes:
        holders = market.shares_of(vote.listing)
        if user_id not in holders or vote.kind not in ("derrama", "reforma"):
            continue
        if vote.kind == "derrama":
            part = rules.split(vote.amount, holders).get(user_id, 0)
            text = f"Derrama en {_place(market, vote.listing)}: tu parte, {format_amount(part)}"
        else:
            part = 0
            text = f"Reforma en {_place(market, vote.listing)}: pagas tu parte si sale"
        items.append(
            Pending(
                Side.PAY, f"vote_{vote.kind}", "🗳️",
                text + " · quien no vota, cuenta a favor", part, vote.ends_at,
            )
        )  # fmt: skip
    if (unit := market.reservation_of(user_id)) is not None:
        spec = VPO_BY_KEY[unit]
        _member, until = market.reservations[unit]
        items.append(
            Pending(
                Side.PAY, "vpo_reserved", "🏢",
                f"Te tocó una VPO en {spec.town}: cómprala por {format_amount(spec.price)} en "
                "`pisos` o se la queda otro",
                0, _midnight(until + timedelta(days=1)),  # comprarla es opcional: no se debe
            )
        )  # fmt: skip
    if rents:
        items.append(
            Pending(
                Side.COLLECT, "rents", "🏘️",
                f"Rentas de esta noche: {format_amount(rents)} brutas, de los inquilinos que "
                "tienes al día (antes de gastos e IRPF)",
                rents, tonight,
            )
        )  # fmt: skip
    for (key, member), (price, until) in market.offers.items():
        if member == user_id:
            items.append(
                Pending(
                    Side.COLLECT, "vulture_offer", "💼",
                    f"Buitre Capital ofrece {format_amount(price)} por {_place(market, key)}",
                    price, _midnight(until + timedelta(days=1)),
                )
            )  # fmt: skip
    for auction in market.auctions:
        place = _place(market, auction.listing)
        if auction.best_bidder == user_id:
            items.append(
                Pending(
                    Side.HELD, "auction_bid", "🔨",
                    f"Tu puja por {place}: {format_amount(auction.escrow)} con ITP y notaría",
                    auction.escrow, auction.ends_at,
                )
            )  # fmt: skip
        elif auction.seller == user_id and auction.judicial:
            items.append(
                Pending(
                    Side.PAY, "auction_judicial", "⚖️",
                    f"{place} está en subasta judicial por tu hipoteca", 0, auction.ends_at,
                )
            )  # fmt: skip
        elif auction.seller == user_id and auction.best_bid:
            items.append(
                Pending(
                    Side.COLLECT, "auction_sale", "🔨",
                    f"Tu subasta de {place} va por {format_amount(auction.best_bid)}",
                    auction.best_bid, auction.ends_at,
                )
            )  # fmt: skip
    for deposit in deposits:
        held = deposit.amount - deposit.forfeit
        if held <= 0:
            continue
        lease = market.leases.get(deposit.listing)
        if lease is not None and lease.member == user_id:
            items.append(
                Pending(
                    Side.HELD, "deposit", "🔑",
                    f"Fianza de {_place(market, deposit.listing)}: {format_amount(held)} · "
                    "vuelve cuando dejes el piso",
                    held,
                )
            )  # fmt: skip
        else:
            items.append(
                Pending(
                    Side.COLLECT, "deposit", "🔑",
                    f"Te devuelven la fianza de {_place(market, deposit.listing)}: "
                    f"{format_amount(held)}",
                    held, tonight,
                )
            )  # fmt: skip
    return items
