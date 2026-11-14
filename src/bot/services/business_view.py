"""Lo que enseñan las pantallas del negocio y el inicio de `pala`: un diccionario por pantalla.

La escena (`assets/negocio/escena.html`, ver `bot.services.business_scene`) pinta cada
estado y no calcula nada: aquí se decide lo que sale y se formatea cada cifra (texto plano,
sin emojis: la escena no tiene su fuente). Las claves son estables y las lee la escena tal
cual; una pantalla nueva o una clave nueva se añade aquí y en la escena a la vez. El
contrato completo de cada pantalla está en `docs/negocios.md` («Las pantallas») y en la
cabecera de la escena.

Pantallas: `dia` (el inicio de `pala`), `montar`, `negocio` (portada y vista previa),
`almacen`, `cuentas`, `cliente` (el minijuego) y `cierre` (el resumen del turno). Desde la
fase 2, también `mejoras` (las mejoras del local y las campañas) y `resenas`; el traslado
se enseña con la vista previa de `negocio` (`traslado_state`). Desde la fase 3, `sociedad`
(constituirla o su balance, `sociedad_state`). Desde la fase 4, `plantilla` (los empleados,
`plantilla_state`) y `candidatos` (los de hoy y la oferta, `candidatos_state`). Desde la
fase 5, `cadena` (todos los locales y la nave, `cadena_state`), `nave` (alquilarla, o lo que
hay dentro, lo que viene y lo que cuesta importar, `nave_state`) y `venta` (lo que paga el
comprador por la sociedad, `venta_state`).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from bot.services import business_chain as chain
from bot.services import business_sl as sl
from bot.services import business_staff as staff
from bot.services.agenda import FRANJAS_DIA, FRANJAS_NOCHE, JORNADA_NAMES, Agenda
from bot.services.business import (
    MAX_LOCALES,
    RECOMMENDED_RATING,
    RECOMMENDED_REVIEWS,
    Business,
    MoveQuote,
    book_value,
    campaign_boost,
    nightly_bill,
    opening_quote,
    rating,
    recommended,
    regular_names,
    sale_price,
    stars,
)
from bot.services.business_catalog import (
    AMORTIZATION,
    CAMPAIGN_BY_KEY,
    CAMPAIGNS,
    EMBLEMS,
    PALETTES,
    POINT_BY_KEY,
    PRICE_BY_KEY,
    REVIEW_LINES,
    SECTOR_BY_KEY,
    SECTORS,
    SIZES,
    equipment_for,
)
from bot.services.business_games import TurnGame, point_text, twist_text
from bot.services.economy import format_amount
from bot.services.housing_catalog import BARRIO_BY_KEY, BARRIOS, ISLANDS
from bot.services.levels import TIMEZONE
from bot.services.taxes import capital_gain_tax, format_rate, igic

State = dict[str, Any]

DAYS = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")
MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
    "octubre", "noviembre", "diciembre",
)  # fmt: skip

#: Partidas de las cuentas de la semana: `(clave en Business.week_totals, nombre, clase)`.
ACCOUNT_ROWS: tuple[tuple[str, str, str], ...] = (
    ("ventas", "Ventas y propinas", "in"),
    ("compras", "Compras al mayorista", "out"),
    ("local", "Local (alquiler y suministros)", "out"),
    ("marketing", "Publicidad", "out"),
    ("amortizacion", "Amortización", "out"),
    ("apertura", "Tasas de apertura y traslado", "out"),
    ("sueldo", "Sueldo del administrador", "out"),
    ("gestoria", "Gestoría", "out"),
    ("constitucion", "Notaría y registro", "out"),
    ("intereses", "Intereses y comisiones del banco", "out"),
    ("personal", "Plantilla (sueldos y Seguridad Social)", "out"),
    ("despidos", "Indemnizaciones", "out"),
    ("formacion", "Formación", "out"),
    ("negro", "Pagado en B", "out"),
    ("multas", "Multas y recargos", "out"),
    ("sociedades", "Impuesto sobre Sociedades", "tax"),
    ("cuota", "Cuota de autónomo", "tax"),
    ("irpf", "IRPF a cuenta", "tax"),
)
#: Título del tique según la nota del turno.
GRADE_TITLES: dict[str, str] = {
    "S": "Turno de leyenda",
    "A": "Buen turno",
    "B": "Turno cumplidor",
    "C": "Turno regular",
    "D": "Turno para olvidar",
}


def signed(amount: int) -> str:
    """`+1.234 Y$` o `−1.234 Y$` (el signo menos de verdad); `0 Y$` sin signo."""
    if amount == 0:
        return format_amount(0)
    if amount < 0:
        return "−" + format_amount(-amount)
    return "+" + format_amount(amount)


def plural(n: int, one: str, many: str) -> str:
    """`1 franja`, `3 franjas`."""
    return f"{n} {one if n == 1 else many}"


def date_text(now: float) -> str:
    """`Martes 20 de octubre`."""
    moment = datetime.fromtimestamp(now, TIMEZONE)
    return f"{DAYS[moment.weekday()]} {moment.day} de {MONTHS[moment.month - 1]}"


def clock_text(now: float) -> str:
    """`18:42` en hora canaria."""
    return datetime.fromtimestamp(now, TIMEZONE).strftime("%H:%M")


def _style(business: Business) -> State:
    main, accent = business.colors
    return {
        "name": business.name,
        "sector": business.sector,
        "main": main,
        "accent": accent,
        "emblem": business.emblem_key,
    }


def low_stock(business: Business) -> list[str]:
    """Productos con menos de medio lote."""
    return [
        p.name.lower()
        for p in business.sector_info.products
        if business.stock.get(p.key, 0) < max(1, p.pack // 2)
    ]


def _alert(business: Business) -> str | None:
    if business.rent_debt:
        return f"Debes {format_amount(business.rent_debt)} del local"
    low = low_stock(business)
    if len(low) == len(business.sector_info.products):
        return "Sin stock: pasa por el almacén"
    if low:
        return f"Poco stock: {low[0]}" + (f" y {len(low) - 1} más" if len(low) > 1 else "")
    return None


# -- El inicio de `pala` --------------------------------------------------------------


def dia_state(
    who: str,
    now: float,
    agenda: Agenda,
    work: Any | None,
    business: Business | None,
    battery_left: float | None = None,
    locales: list[Business] | None = None,
) -> State:
    """El inicio de `pala`: las franjas del día y las tarjetas del curro y del negocio.

    Args:
        who: Nombre del miembro.
        agenda: Sus franjas de hoy.
        work: El `pala.Status` de su contrato, o `None` sin curro.
        business: Su negocio (con los contadores de hoy al día), o `None`.
        battery_left: Su batería sin curro (la que gasta en el negocio); con curro sale
            de `work`. Sin curro no hay familia que enseñar.
        locales: Todos sus locales (fase 5): la tarjeta del negocio enseña el primero, la caja
            de hoy de todos y cuántos más tiene (`more`, 0 con uno solo).
    """
    job: State | None = None
    jornada = "Sin curro"
    family = None
    battery = None if battery_left is None else max(0, min(100, round(battery_left)))
    if work is not None:
        contract = work.contract
        jornada = "Excedencia" if contract.on_excedencia else JORNADA_NAMES[contract.jornada]
        battery = max(0, min(100, round(work.battery)))
        family = max(0, min(100, round(work.family)))
        red, green, blue = work.job.rgb
        if contract.on_excedencia:
            note = "De excedencia: no ficha"
        elif contract.reduced:
            note = "Jornada reducida: sin horas extra"
        else:
            note = f"Próximo turno: {work.next_kind.value}"
        job = {
            "icon": work.job.key,
            "name": work.job.name,
            "title": work.position.title,
            "level": f"{contract.level}/{work.job.top}",
            "pay": f"{format_amount(work.position.base_pay)} por turno",
            "today": f"{agenda.ordinary} de {contract.jornada} turnos de jornada",
            "rgb": f"#{red:02x}{green:02x}{blue:02x}",
            "note": note,
        }
    card: State | None = None
    if business is not None:
        chain_ = locales or [business]
        turns = sum(b.turns_today for b in chain_)
        alerts = [a for b in chain_ if (a := _alert(b))]
        card = {
            **_style(business),
            "sector_name": business.sector_info.name,
            "barrio": business.barrio_info.name,
            "stars": business.stars,
            "today": f"{signed(sum(b.sales_today for b in chain_))} hoy",
            "status": plural(turns, "turno hoy", "turnos hoy") if turns else "Hoy aún cerrado",
            "alert": alerts[0] if alerts else None,
            "more": len(chain_) - 1,
        }
    free = agenda.free
    if work is None and business is None:
        tip = (
            f"Busca un curro o monta tu negocio: el día tiene {FRANJAS_DIA} franjas para "
            f"repartir, y {FRANJAS_NOCHE} más si trasnochas."
        )
    elif agenda.left == 0:
        tip = "Hoy ya no te quedan franjas, ni de noche. Mañana más."
    elif free == 0:
        tip = (
            f"Se acabó el día: te quedan {agenda.night_free} de noche. Trasnochando, la "
            "Inspección pilla más el B y tu familia lo nota."
        )
    elif business is not None:
        tip = f"Te quedan {plural(free, 'franja', 'franjas')}: cada turno en tu negocio gasta una."
    else:
        tip = (
            f"La jornada reserva sus franjas aunque no fiches. Las otras {free} son para horas "
            "extra… o para un negocio propio."
        )
    return {
        "screen": "dia",
        "who": who,
        "date": date_text(now),
        "clock": clock_text(now),
        "slots": [{"kind": kind} for kind in agenda.slots()],
        "night_from": FRANJAS_DIA,
        "free": plural(free, "franja libre", "franjas libres"),
        "night_free": plural(agenda.night_free, "franja de noche", "franjas de noche"),
        "jornada": jornada,
        "battery": battery,
        "family": family,
        "job": job,
        "business": card,
        "tip": tip,
    }


# -- Montar ---------------------------------------------------------------------------


def montar_state(selected: str | None = None) -> State:
    """Elegir sector: todos los de `SECTORS`, con sus cifras (la escena los reparte)."""
    cards = []
    for sector in SECTORS:
        quotes = [opening_quote(sector, barrio) for barrio in BARRIOS]
        low, high = (format_amount(f(q.rent for q in quotes)) for f in (min, max))
        cards.append(
            {
                "key": sector.key,
                "name": sector.name,
                "blurb": sector.blurb,
                "lines": [
                    f"Local de {sector.m2} m²",
                    f"Alquiler: de {low} a {high} al día",
                    f"Montarlo: desde {format_amount(min(q.total for q in quotes))}",
                ],
                "selected": sector.key == selected,
            }
        )
    return {
        "screen": "montar",
        "title": "Elige qué negocio montas",
        "sub": f"{len(SECTORS)} locales, {len({s.game for s in SECTORS})} minijuegos",
        "sectors": cards,
        "foot": "Eres autónomo: respondes con lo tuyo. El alquiler corre aunque no abras.",
    }


def preview_state(sector: str, barrio: str, name: str, palette: int, emblem: int) -> State:
    """La fachada de un negocio antes de montarlo (con el rótulo elegido)."""
    main, accent = PALETTES[palette % len(PALETTES)][1:]
    place = BARRIO_BY_KEY[barrio]
    quote = opening_quote(SECTOR_BY_KEY[sector], place)
    return {
        "screen": "negocio",
        "mode": "preview",
        "name": name,
        "sector": sector,
        "barrio": place.name,
        "theme": place.theme,
        "main": main,
        "accent": accent,
        "emblem": EMBLEMS[emblem % len(EMBLEMS)][0],
        "open": False,
        "stars": stars(50),
        "seed": hash_seed(f"{sector}:{barrio}:{name}"),
        "stats": [],
        "tags": [place.town, f"Alquiler: {format_amount(quote.rent)} al día"],
        "regulars": [],
        "foot": f"Montarlo cuesta {format_amount(quote.total)} con la fianza.",
    }


def hash_seed(text: str) -> int:
    """Una semilla estable de un texto (sin `hash()`, que cambia en cada arranque)."""
    seed = 0
    for char in text:
        seed = (seed * 131 + ord(char)) % 2_147_483_647
    return seed


# -- El negocio -----------------------------------------------------------------------


def negocio_state(business: Business, *, foot: str, rivals: int = 0, chain_count: int = 1) -> State:
    """La portada del negocio con las cifras de hoy.

    Fase 5: `locales` (`None` con un solo local, o `{"count", "text"}`: cuántos lleva la cadena)
    y `manager` (el nombre del gerente, o `None`).

    Fase 2: `rating` (media de las reseñas, cuántas y si sale recomendado), `campaign` (la
    campaña de hoy y lo que le queda), `equipment` (las claves de las mejoras instaladas) y
    `size` (el tamaño del local y si es propio). Fase 4: `staff` (`None` sin plantilla, o
    `{"count", "line", "seeds", "roles"}`: cuántos son, sus turnos de hoy en una línea, la
    semilla de cada uno para dibujarlos y, desde la 9.41.0, su puesto para vestirlos), y «Esta
    noche» ya cuenta las nóminas.
    """
    bill = nightly_bill(business)
    tonight = bill.total
    team = None
    if business.staff:
        day = date.fromisoformat(business.day) if business.day else date.today()
        pay = staff.payroll(business, day)
        tonight += pay.cost + pay.black
        done = business.staff_today
        team = {
            "count": len(business.staff),
            "line": (
                f"{plural(done.get('turns', 0), 'turno', 'turnos')} de la plantilla hoy · "
                f"{signed(done.get('sales', 0))}"
            ),
            "seeds": [e["key"] for e in business.staff],
            "roles": [e.get("role", "") for e in business.staff],
        }
    week = sum(business.week_totals.values())
    tags = [PRICE_BY_KEY[business.price].name.split(" (")[0] + " de precio"]
    if business.is_company:
        tags.insert(0, "Sociedad limitada")
    if alert := _alert(business):
        tags.append(alert)
    if rivals:
        tags.append(plural(rivals, "competidor en el barrio", "competidores en el barrio"))
    today = date.fromisoformat(business.day) if business.day else None
    return {
        "screen": "negocio",
        "mode": "portada",
        **_style(business),
        "barrio": business.barrio_info.name,
        "theme": business.barrio_info.theme,
        "open": business.turns_today > 0,
        "stars": business.stars,
        "seed": business.seed,
        "stats": [
            {"label": "Caja de hoy", "value": signed(business.sales_today)},
            {"label": "Clientes hoy", "value": str(business.clients_today)},
            {"label": "Turnos hoy", "value": str(business.turns_today)},
            {"label": "Esta noche", "value": signed(-tonight)},
            {"label": "Esta semana", "value": signed(week)},
            {"label": "Mejor turno", "value": signed(business.best_turn)},
        ],
        "tags": tags[:4],
        "regulars": [
            {"name": name, "loyalty": business.regulars.get(name, 0)}
            for name in regular_names(business.seed)
        ],
        "rating": rating_state(business),
        "campaign": campaign_state(business, today) if today else None,
        "equipment": list(business.equipment),
        "size": size_text(business),
        "staff": team,
        "locales": (
            {"count": chain_count, "text": f"Cadena de {chain_count} locales"}
            if chain_count > 1
            else None
        ),
        "manager": boss["name"] if (boss := staff.manager(business)) else None,
        "foot": foot,
    }


def size_text(business: Business) -> str:
    """`Local mediano · propio` o `Local pequeño`."""
    name = f"Local {business.size_info.name.lower()}"
    return f"{name} · propio" if business.own_listing else name


def rating_state(business: Business) -> State | None:
    """La media de las reseñas para la escena (`None` si aún no hay ninguna)."""
    average, count = rating(business)
    if not count:
        return None
    return {
        "avg": f"{average:.1f}".replace(".", ","),
        "value": round(average, 2),
        "count": plural(count, "reseña", "reseñas"),
        "badge": recommended(business),
    }


def campaign_state(business: Business, today: date) -> State | None:
    """La campaña de hoy (`None` si no hay)."""
    if not campaign_boost(business, today):
        return None
    campaign = CAMPAIGN_BY_KEY[business.campaign]
    left = (date.fromisoformat(business.campaign_until) - today).days + 1
    return {
        "key": campaign.key,
        "name": campaign.name,
        "left": plural(left, "día", "días"),
        "boost": f"+{round(campaign.boost * 100)} % de clientes",
    }


def almacen_state(business: Business, foot: str) -> State:
    """El stock: cada producto con sus unidades, coste, precio y si caduca."""
    low = set(low_stock(business))
    products = []
    for product in business.sector_info.products:
        products.append(
            {
                "key": product.key,
                "name": product.name,
                "units": str(business.stock.get(product.key, 0)),
                "cost": format_amount(product.cost),
                "price": format_amount(sale_price(product, business.level(product.key).key)),
                # Fase 2: el nivel de precios si no es el general (`None` si va con el resto).
                "level": (
                    business.level(product.key).name.split(" (")[0]
                    if product.key in business.prices
                    else None
                ),
                "spoil": (
                    f"Caduca un {round(product.spoil * 100)} % cada noche"
                    if product.spoil
                    else None
                ),
                "low": product.name.lower() in low,
            }
        )
    return {"screen": "almacen", **_style(business), "products": products, "foot": foot}


def _week_column(title: str, totals: dict[str, int]) -> State:
    rows = [
        {"label": label, "value": signed(totals[key]), "kind": kind}
        for key, label, kind in ACCOUNT_ROWS
        if totals.get(key)
    ]
    result = sum(totals.values())
    return {
        "title": title,
        "rows": rows,
        "total": {"label": "Resultado", "value": signed(result), "good": result >= 0},
    }


def cuentas_state(business: Business) -> State:
    """La cuenta de resultados de esta semana y la anterior."""
    weeks = [_week_column("Esta semana", business.week_totals)]
    if business.last_week:
        weeks.append(_week_column("La semana pasada", business.last_week))
    return {
        "screen": "cuentas",
        **_style(business),
        "weeks": weeks,
        "foot": "Criterio de caja: cada cosa cuenta el día que se cobra o se paga, salvo las "
        "mejoras y la obra, que se amortizan cada noche. La Renta del lunes ajusta el IRPF.",
    }


# -- Fase 2: mejoras, reseñas y traslado ------------------------------------------------


def mejoras_state(business: Business, today: date, foot: str = "") -> State:
    """Las mejoras del sector (instaladas o no) y las campañas de publicidad.

    Cada mejora: `key`, `name`, `effect`, `price` (con IGIC), `class` (su amortización) y
    `owned`; si está instalada, `book` (lo que queda por amortizar). Cada campaña: `key`,
    `name`, `effect`, `price` y `days`. `active` es la campaña en marcha (`None` si no hay).
    """
    left = {a["key"]: a["left"] for a in business.assets}
    items = []
    for item in equipment_for(business.sector):
        name, rate = AMORTIZATION[item.kind]
        owned = item.key in business.equipment
        items.append(
            {
                "key": item.key,
                "name": item.name,
                "effect": item.effect(),
                "price": format_amount(item.cost + igic(item.cost)),
                "class": f"{name} · {round(rate * 100)} % al año",
                "owned": owned,
                "book": (
                    f"Por amortizar: {format_amount(left.get(item.key, 0))}" if owned else None
                ),
            }
        )
    campaigns = [
        {
            "key": c.key,
            "name": c.name,
            "effect": f"+{round(c.boost * 100)} % de clientes y de cupo",
            "price": format_amount(c.cost + igic(c.cost)),
            "days": plural(c.days, "día", "días"),
        }
        for c in CAMPAIGNS
    ]
    return {
        "screen": "mejoras",
        **_style(business),
        "items": items,
        "campaigns": campaigns,
        "active": campaign_state(business, today),
        "book": f"Por amortizar en total: {format_amount(book_value(business))}",
        "foot": foot
        or "Las mejoras se amortizan cada noche: su gasto se reparte, no se paga dos veces.",
    }


def _when(day: str, today: date) -> str:
    days = (today - date.fromisoformat(day)).days
    return "hoy" if days <= 0 else "ayer" if days == 1 else f"hace {days} días"


def resenas_state(business: Business, today: date) -> State:
    """Las reseñas: la media, cuántas, si sale recomendado y las seis últimas."""
    average, count = rating(business)
    reviews = [
        {
            "name": who,
            "stars": n,
            "line": REVIEW_LINES[n][line % len(REVIEW_LINES[n])],
            "day": _when(day, today),
        }
        for day, who, n, line in reversed(business.reviews[-6:])
    ]
    badge = recommended(business)
    need = (
        f"Con {RECOMMENDED_REVIEWS} reseñas y {str(RECOMMENDED_RATING).replace('.', ',')} de "
        "media, sales recomendado: un 10 % más de clientes."
    )
    return {
        "screen": "resenas",
        **_style(business),
        "avg": f"{average:.1f}".replace(".", ",") if count else "—",
        "value": round(average, 2),
        "count": plural(count, "reseña", "reseñas"),
        "badge": badge,
        "reviews": reviews,
        "foot": "Recomendado en la guía del barrio." if badge else need,
    }


def traslado_state(
    business: Business, barrio: str, size: int, quote: MoveQuote, *, own: bool = False
) -> State:
    """La fachada del local al que se trasladaría (vista previa de `negocio`)."""
    main, accent = business.colors
    place = BARRIO_BY_KEY[barrio]
    rent = (
        "Local propio: sin alquiler"
        if own
        else f"Alquiler: {format_amount(quote.opening.rent)} al día"
    )
    lose = "" if quote.same_barrio else " Cambias de barrio: pierdes media clientela."
    return {
        "screen": "negocio",
        "mode": "preview",
        "name": business.name,
        "sector": business.sector,
        "barrio": place.name,
        "theme": place.theme,
        "main": main,
        "accent": accent,
        "emblem": business.emblem_key,
        "open": False,
        "stars": business.stars,
        "seed": business.seed,
        "stats": [],
        "tags": [place.town, f"Local {SIZES[size].name.lower()}", rent],
        "regulars": [],
        "foot": f"Trasladarte cuesta {format_amount(quote.total)}.{lose}",
    }


# -- Un turno -------------------------------------------------------------------------


def cliente_state(business: Business, game: TurnGame) -> State:
    """El minijuego: el cliente de ahora con lo que pide y la cola.

    Solo depende del cliente, de su sitio en la cola y del paso del taller: lo que pase
    con los de antes no cambia su imagen, y por eso se pinta antes de que llegue.
    """
    client = game.current
    assert client is not None
    state: State = {
        "screen": "cliente",
        **_style(business),
        "theme": business.barrio_info.theme,
        "client": {
            "seed": client.seed,
            "name": client.regular,
            "regular": client.regular is not None,
            "line": "Lo de siempre." if client.usual else client.line,
        },
        "queue": game.queue,
        "index": f"Cliente {game.index + 1} de {len(game.clients)}",
        # La caja del turno va en el texto: así la imagen del siguiente cliente no depende de
        # cómo acabe este y se puede pintar mientras se le atiende.
        "cash": "",
        "patience": f"{round(game.patience)} s",
    }
    kind = game.sector.game
    if kind == "comandas":
        state["usual"] = client.usual
        state["order"] = (
            None if client.usual else [{"key": k, "n": n} for k, n in client.order.items()]
        )
    elif kind == "caja":
        state["basket"] = [
            {"key": k, "n": n, "price": format_amount(game.price_of(k) * n)}
            for k, n in client.order.items()
        ]
        state["total"] = format_amount(game.total())
        state["paid"] = format_amount(game.paid)
        state["thief"] = client.hidden is not None
    elif kind == "brasa":
        # Lo que se ve de la brasa no cambia con cada pulsación (eso va en el texto): el
        # plato crudo en la parrilla, la mesa y el punto que pide.
        state["order"] = [{"key": k, "n": n} for k, n in client.order.items()]
        state["dish"] = client.dish
        state["usual"] = client.usual
        state["point"] = (
            ""
            if client.usual or not client.dish
            else point_text(client.dish, client.point).capitalize()
        )
        if client.dish and not client.usual:
            _key, _ask, low, high = POINT_BY_KEY[client.point]
            state["window"] = [low, high]
    elif kind == "encargos":
        state["usual"] = client.usual
        state["ask"] = (
            "Vengo a por lo mío, lo de siempre."
            if client.usual
            else f"El encargo de {client.label}"
        )
        state["clue"] = "" if client.usual else client.clue
        state["bags"] = [
            {"label": bag.label, "items": [{"key": k, "n": n} for k, n in bag.order.items()]}
            for bag in client.bags
        ]
    elif kind == "balanza":
        state["usual"] = client.usual
        state["species"] = client.species
        state["grams"] = "" if client.usual else f"{client.grams:,} g".replace(",", ".")
        state["pieces"] = list(client.pieces)
        state["per_kg"] = format_amount(game.weight_price(client.species, 1_000))
    elif kind == "revision":
        state["phone"] = {"model": client.phone, "color": client.phone_color}
        state["faults"] = list(client.faults)
        state["protector"] = client.protector
        state["symptom"] = client.symptom
    elif kind == "foto":
        photo = client.photo
        assert photo is not None
        state["usual"] = client.usual
        state["photo"] = None if client.usual else _style_dict(photo)
        state["twist"] = "" if client.usual else twist_text(client.twist)
        state["hair"] = client.hair
        state["beard"] = client.beard
    else:
        state["car"] = {"color": client.car_color, "model": client.car_model}
        state["symptom"] = client.symptom
        state["cue"] = client.cue
        state["step"] = game.step
    return state


def _style_dict(style: Any) -> dict[str, Any]:
    return {
        "length": style.length,
        "fade": style.fade,
        "color": style.color,
        "beard": style.beard,
        "wash": style.wash,
    }


def cierre_state(business: Business, result: Any) -> State:
    """El tique del turno (`negocio.TurnResult`)."""
    summary = result.summary
    total = len(summary.outcomes)
    stats = [
        {"label": "Atendidos", "value": f"{summary.served} de {total}"},
        {"label": "Caja", "value": signed(summary.revenue)},
        {"label": "Propinas", "value": signed(summary.tips)},
        {"label": "Coste de lo vendido", "value": signed(-summary.sold_cost)},
    ]
    if summary.waste_cost:
        stats.append({"label": "Perdido", "value": signed(-summary.waste_cost)})
    if result.withheld:
        stats.append({"label": "IRPF a cuenta", "value": signed(-result.withheld)})
    return {
        "screen": "cierre",
        **_style(business),
        "grade": summary.grade,
        "title": GRADE_TITLES[summary.grade],
        "stats": stats[:6],
        "stars_before": result.stars_before,
        "stars_after": business.stars,
        "lines": turn_lines(result)[:4],
    }


#: Lo que se clava en cada minijuego de la 11.3.0, para el tique.
PERFECT_LINES: dict[str, tuple[str, str]] = {
    "brasa": ("plato en su punto exacto", "platos en su punto exacto"),
    "balanza": ("peso casi clavado", "pesos casi clavados"),
    "revision": ("móvil con tres averías, todas vistas", "móviles con tres averías, todas vistas"),
    "foto": ("foto con su cambio, acertada", "fotos con su cambio, acertadas"),
}
#: Lo que sale mal en los minijuegos de la 11.3.0, para el tique.
TURN_MISHAPS: tuple[tuple[str, str, str], ...] = (
    ("burnt", "plato quemado", "platos quemados"),
    ("raw", "plato devuelto crudo", "platos devueltos crudos"),
    ("wrong_bag", "bolsa equivocada", "bolsas equivocadas"),
    ("refused", "pescado rechazado por el peso", "pescados rechazados por el peso"),
    ("upsell", "pieza cambiada sin hacer falta", "piezas cambiadas sin hacer falta"),
    ("too_short", "corte más corto de lo pedido", "cortes más cortos de lo pedido"),
)


def turn_lines(result: Any) -> list[str]:
    """Lo que destacar de un turno (`negocio.TurnResult`), en frases cortas."""
    summary = result.summary
    lines = []
    if result.best:
        lines.append("Tu mejor turno hasta ahora")
    for name, change in summary.loyalty.items():
        if change > 0:
            loyalty = result.business.regulars.get(name, 0)
            lines.append(f"{name} sube a fidelidad {loyalty}")
        elif change < 0:
            loyalty = result.business.regulars.get(name, 0)
            lines.append(f"{name} baja a fidelidad {loyalty}")
    if caught := sum(1 for o in summary.outcomes if "caught" in o.events):
        lines.append(plural(caught, "listillo pillado", "listillos pillados"))
    game = result.business.sector_info.game
    if perfect := sum(1 for o in summary.outcomes if o.perfect):
        lines.append(plural(perfect, *PERFECT_LINES.get(game, ("clavado", "clavados"))))
    for kind, one, many in TURN_MISHAPS:
        if n := summary.count(kind):
            lines.append(plural(n, one, many))
    if missed := summary.count("left"):
        lines.append(
            plural(missed, "cliente se cansó de esperar", "clientes se cansaron de esperar")
        )
    if gone := summary.count("gone"):
        lines.append(plural(gone, "se quedó en la cola", "se quedaron en la cola"))
    return lines


# -- Fase 3: la sociedad ----------------------------------------------------------------

#: Qué cambia de autónomo a sociedad, para la pantalla de constituirla.
COMPARE: tuple[tuple[str, str, str], ...] = (
    ("Impuesto", "IRPF por tramos, con el resto de tu renta", "Sociedades: 19 % y 21 % por tramos"),
    ("Tu dinero", "Es el del negocio", "Sueldo (trabajo) o dividendos (19 %)"),
    ("Si se hunde", "Respondes con todo lo tuyo", "Solo pierdes lo que hay dentro"),
    ("Papeleo", "Ninguno", "Gestoría cada noche y notaría al empezar"),
)


def sociedad_state(view: Any, *, capital: int = sl.FULL_CAPITAL, foot: str = "") -> State:
    """La pantalla 🏢 Sociedad (`negocio.CompanyView`).

    Dos modos. `autonomo`: lo que cambia (`compare`), los capitales para escoger (`capitals`,
    con el elegido), lo que cuesta (`cost`) y si su curro no le deja (`blocked`). `sl`: el
    balance (`cash`, `capital`, `reserve`, `retained`, `loan`, `losses`, `tax_debt`,
    `equity`), lo que puede repartir (`room`), la retención del sueldo (`retention`), el
    último Impuesto sobre Sociedades (`last`, o `None`) y los avisos (`warnings`). Desde la
    9.9.0, `ric` (`share`, lo que se dota cada semana; `pending`, lo que falta por invertir;
    `due`, hasta cuándo, o `None`; `locked`, la reserva indisponible) y `zec` (`on` y `text`).
    """
    business = view.business
    blocked = f"{view.blocked.text} ({view.blocked.law})" if view.blocked else None
    if not business.is_company:
        fees = sl.constitution_fees(capital)
        options = []
        for amount in sl.CAPITALS:
            low = amount < sl.FULL_CAPITAL
            options.append(
                {
                    "amount": format_amount(amount),
                    "note": (
                        f"Reserva legal del 20 % y respondes hasta {format_amount(sl.FULL_CAPITAL)}"
                        if low
                        else "Responsabilidad limitada al capital"
                    ),
                    "selected": amount == capital,
                }
            )
        return {
            "screen": "sociedad",
            "mode": "autonomo",
            **_style(business),
            "compare": [{"label": a, "autonomo": b, "sl": c} for a, b, c in COMPARE],
            "capitals": options,
            "cost": format_amount(capital + fees + igic(fees)),
            "fees": f"Notaría y registro: {format_amount(fees)} + IGIC",
            "blocked": blocked,
            "foot": foot
            or "El negocio pasa entero a la sociedad (art. 87 LIS): sin IRPF ni IGIC al hacerlo.",
        }
    info = business.company
    last = info.get("last")
    warnings = []
    if blocked:
        warnings.append(f"No puedes cobrar de administrador: {blocked}")
    if view.dissolution:
        warnings.append("Causa de disolución: el patrimonio neto no llega a la mitad del capital")
    if info.get("tax_debt"):
        warnings.append(f"Debe {format_amount(info['tax_debt'])} a Hacienda: no hay dividendos")
    due = sl.ric_due(info)
    if due:
        warnings.append(
            f"Invierte {format_amount(sl.ric_pending(info))} de la RIC antes del "
            f"{_day_text(due)} o pagarás Sociedades por ello"
        )
    return {
        "screen": "sociedad",
        "mode": "sl",
        **_style(business),
        "cash": format_amount(view.cash),
        "capital": format_amount(info["capital"]),
        "reserve": format_amount(info["reserve"]),
        "retained": signed(info["retained"]),
        "loan": format_amount(info["loan"]),
        "losses": format_amount(info["losses"]),
        "tax_debt": format_amount(info.get("tax_debt", 0)),
        "equity": signed(view.equity),
        "room": format_amount(view.dividend_room),
        "retention": format_rate(view.retention).replace(",00", ""),
        "last": (
            {
                "base": signed(last["base"]),
                "rate": format_rate(last["rate"]).replace(",00", ""),
                "tax": format_amount(last["tax"]),
                "reserve": format_amount(last["reserve"]),
            }
            if last
            else None
        ),
        "warnings": warnings[:3],
        "ric": {
            "share": f"{round(info.get('ric_share', 0.0) * 100)} %",
            "pending": format_amount(sl.ric_pending(info)),
            "due": _day_text(due) if due else None,
            "locked": format_amount(sl.ric_locked(info)),
        },
        "zec": {"on": bool(info.get("zec")), "text": zec_text(business)},
        "foot": foot
        or "Cada domingo por la noche, el Impuesto sobre Sociedades de la semana. Lo que quede, "
        "a dividendos.",
    }


def _day_text(iso: str) -> str:
    """`lunes 9` de un día ISO."""
    day = date.fromisoformat(iso)
    return f"{DAYS[day.weekday()].lower()} {day.day}"


def zec_text(business: Business) -> str:
    """Cómo está la sociedad con la Zona Especial Canaria, en una línea."""
    info = business.company
    if info.get("zec"):
        return "Inscrita en la ZEC: Sociedades al 4 % mientras mantenga el empleo"
    jobs = sum(1 for e in business.staff if e["contract"] != "b")
    today = date.fromisoformat(business.day) if business.day else date.today()
    blocked = sl.zec_blocker(business.sector, business.barrio_info.island, jobs, today)
    return blocked or "Puede inscribirse en la ZEC: Sociedades al 4 %"


# -- Fase 4: la plantilla --------------------------------------------------------------

#: Cómo se llama cada contrato en las tarjetas.
CONTRACT_NAMES = {"indefinido": "Indefinido", "temporal": "Temporal", "b": "En B"}


def _skills(skills: dict[str, float], traits: list[str]) -> list[State]:
    probe = {"skills": skills, "traits": traits}
    return [
        {"key": k, "name": staff.SKILL_NAMES[k], "value": round(staff.skill(probe, k))}
        for k in staff.SKILLS
    ]


def _traits(keys: list[str]) -> list[State]:
    return [
        {"key": k, "name": staff.TRAIT_BY_KEY[k].name, "text": staff.TRAIT_BY_KEY[k].text}
        for k in keys
    ]


def shift_text(shift: str, jornada: int) -> str:
    """`Mañana · completa` o `Tarde · media` (los horarios de antes de la 9.41.0)."""
    whole = "completa" if jornada >= staff.FULL_DAY else "media"
    return f"{staff.SHIFT_NAMES[shift].capitalize()} · {whole}"


def horario_text(employee: dict[str, Any], *, short: bool = False) -> str:
    """Su horario: `Partida · 10:00-14:00 y 18:00-22:00` (o `Partida · 10-14 y 18-22 h`, en
    corto); el gerente, `Gerente · completa`."""
    schedule = staff.horario_of(employee)
    if schedule is None:
        return "Gerente · completa"
    return schedule.short if short else schedule.label


def _horario_state(schedule: staff.Horario | None) -> dict[str, Any]:
    """Lo que necesita la escena para el icono del horario: `horario_kind` (`completa`,
    `partida`, `seis`, `media` o `gerente`) y `night` (si empieza por la tarde)."""
    if schedule is None:
        return {"horario_kind": "gerente", "night": False}
    return {"horario_kind": schedule.kind, "night": schedule.shift == "tarde"}


def afluencia(sector: Any, schedule: staff.Horario) -> int:
    """Lo que vienen los clientes en las franjas de un horario frente a la media del día, en %
    (100 = la media de las ocho franjas): ayuda a elegir el horario de cada sector."""
    curve = sector.curve
    day = [curve[staff.demand_hour(f) % 24] for f in range(len(staff.SHIFTS["manana"]) * 2)]
    mine = [curve[staff.demand_hour(f) % 24] for f in schedule.franjas]
    return round(100 * (sum(mine) / len(mine)) / (sum(day) / len(day)))


#: Tarjetas de la plantilla por página de la escena (dos filas de tres).
STAFF_PAGE = 6


def mood_name(humor: float) -> str:
    """`contento`, `normal` o `quemado` (la escena pone la cara)."""
    return "contento" if humor >= 65 else "quemado" if humor < 35 else "normal"


def staff_card(employee: dict[str, Any], today: date, *, selected: bool = False) -> State:
    """La tarjeta de un empleado (`plantilla`).

    `seed` dibuja su cara. `contract` es la clave (`indefinido`, `temporal` o `b`) y
    `contract_name` lo que pone, con el período de prueba o el día en que se acaba. `today`
    dice lo de hoy en una línea. `alerts`, lo que hay que mirar (dos como mucho). Fase 5:
    `role` es `gerente` o `""`; el gerente no tiene turno (`shift` dice «Gerente»).

    9.41.0: `role` es la clave de su puesto (`business_staff.ROLES`) y `role_name` su nombre;
    `shift` es su horario en corto (`Partida · 10-14 y 18-22 h`), con `horario_kind` y `night`
    para el icono.
    """
    iso = today.isoformat()
    contract = CONTRACT_NAMES[employee["contract"]]
    if staff.in_trial(employee, today):
        contract += " · en prueba"
    if employee["until"]:
        end = date.fromisoformat(employee["until"])
        contract += f" · hasta el {DAYS[end.weekday()].lower()}"
    role = staff.role_of(employee)
    if employee["absent_day"] == iso:
        line = "Hoy no ha venido"
    elif not role.plays:
        line = role.duty
    elif employee["grades_day"] == iso and employee["grades"]:
        turns = plural(len(employee["grades"]), "turno", "turnos")
        line = f"Hoy: {turns} · nota " + " ".join(employee["grades"])
    else:
        line = "Hoy aún no ha trabajado"
    alerts = []
    if employee["unpaid"]:
        alerts.append(f"Se le deben {format_amount(employee['unpaid'])}")
    if planned := employee.get("next"):
        new = staff.HORARIO_BY_KEY.get(staff.horario_key(planned["franjas"]))
        if new is not None:
            alerts.append(f"Desde mañana: {new.short}")
    if employee["course"]:
        alerts.append(f"De curso: {staff.SKILL_NAMES[employee['course']].lower()}")
    worth = staff.worth(employee)
    if employee["wage"] < worth * staff.WORTH_SLACK:
        alerts.append(f"Ya vale {format_amount(worth)}")
    if employee["absences"]:
        alerts.append(plural(employee["absences"], "falta", "faltas"))
    return {
        "id": employee["id"],
        "name": employee["name"],
        "age": employee["age"],
        "seed": employee["key"],
        "contract": employee["contract"],
        "contract_name": contract,
        "shift": horario_text(employee, short=True),
        **_horario_state(staff.horario_of(employee)),
        "role": role.key,
        "role_name": role.name,
        "skills": _skills(employee["skills"], employee["traits"]),
        "humor": round(employee["humor"]),
        "mood": mood_name(employee["humor"]),
        "wage": f"{format_amount(employee['wage'])} la noche",
        "today": line,
        "traits": _traits(employee["traits"]),
        "alerts": alerts[:2],
        "selected": selected,
    }


def plantilla_state(
    business: Business, today: date, *, selected: int | None = None, foot: str = ""
) -> State:
    """La pantalla 👥 Plantilla: una tarjeta por empleado (`staff_card`) y las cuentas.

    `cap` es cuántos hay de cuántos caben; `payroll`, lo que cuesta la plantilla esta noche;
    `debt`, lo que se le debe (o `None`); `today`, sus turnos de hoy.

    9.41.0: caben más de seis (los puestos y el gerente), así que va por páginas de
    `STAFF_PAGE`: la del elegido. `page` dice cuál (`Página 2 de 2`, o `None` con una sola) y
    `room`, si queda un puesto libre (el hueco de contratar va en la última).
    """
    pay = staff.payroll(business, today)
    done = business.staff_today
    turns = plural(done.get("turns", 0), "turno", "turnos")
    clients = plural(done.get("clients", 0), "cliente", "clientes")
    counter = len(staff.seated(business))
    room = staff.cap(business.size)
    cap = f"{counter} de {room} puestos"
    if staff.manager(business) is not None:
        cap += " · con gerente"
    team = business.staff
    index = next((i for i, e in enumerate(team) if e["id"] == selected), 0)
    pages = max(1, -(-len(team) // STAFF_PAGE))
    page = min(index // STAFF_PAGE, pages - 1)
    shown = team[page * STAFF_PAGE : (page + 1) * STAFF_PAGE]
    return {
        "screen": "plantilla",
        **_style(business),
        "staff": [staff_card(e, today, selected=e["id"] == selected) for e in shown],
        "page": f"Página {page + 1} de {pages}" if pages > 1 else None,
        "room": counter < room and page == pages - 1,
        "cap": cap,
        "payroll": f"Nóminas de esta noche: {format_amount(pay.cost + pay.black)}",
        "debt": (
            f"Se debe a la plantilla: {format_amount(business.wage_debt)}"
            if business.wage_debt
            else None
        ),
        "today": f"Hoy: {turns} · {clients} · {signed(done.get('sales', 0))}",
        "foot": foot
        or "Quien atiende trabaja solo en su horario; los de apoyo dejan su efecto cada día. "
        "Cobran cada noche.",
    }


def offer_horario(offer: dict[str, Any]) -> staff.Horario:
    """El horario de una oferta: `horario` (9.41.0) o, en una de antes, `shift` y `jornada`."""
    key = offer.get("horario") or staff.horario_key(
        staff.legacy_franjas(offer.get("shift", "manana"), offer.get("jornada", staff.FULL_DAY))
    )
    return staff.HORARIO_BY_KEY.get(key, staff.HORARIOS[0])


def offer_slip(
    candidate: dict[str, Any], offer: dict[str, Any], sector: str, today: date
) -> tuple[dict[str, Any], Any]:
    """`(el empleado que saldría, su nómina de una noche)` con la oferta (`contract`, `role`
    y `horario`, o los `jornada` y `shift` de antes de la 9.41.0) y lo que pide."""
    role = offer.get("role", "")
    schedule = offer_horario(offer)
    wage = staff.role_ask(candidate["skills"], candidate["traits"], schedule.jornada, role)
    probe = staff.new_employee(
        candidate, seq=0, contract=offer["contract"], jornada=schedule.jornada,
        shift=schedule.shift, wage=wage, start=today, bonified=offer.get("bonified", True),
        role=role, franjas=schedule.franjas,
    )  # fmt: skip
    return probe, staff.payslip(probe, sector, today)


def candidatos_state(
    business: Business,
    candidates: list[dict[str, Any]],
    today: date,
    *,
    selected: int | None = None,
    offer: dict[str, Any] | None = None,
    foot: str = "",
) -> State:
    """La pantalla de los candidatos de hoy y la oferta al elegido.

    Cada candidato: `key`, `name`, `age`, `seed`, `profile` (clave) y `profile_name`, `bonus`
    (la bonificación de contratarle indefinido, o `None`), `traits`, `skills`, `ask` (a
    jornada completa y a media), `part_time` y `selected`. `offer` es la oferta al elegido
    (`None` si no hay): el contrato (`kind`, la clave, y `contract`, su nombre), el turno, el
    bruto, lo que le llega, lo que cuesta a la empresa, la bonificación y si es en B (`black`).
    Fase 5: `role` (`gerente` o `""`) en la oferta; el gerente no tiene turno.

    9.41.0: `role` es cualquier puesto (`business_staff.ROLES`), con `role_name`; `shift` es
    el horario (`Partida · 10-14 y 18-22 h`), con `horario_kind` y `night`. Con recursos
    humanos llegan cuatro candidatos.
    """
    cards = []
    for c in candidates:
        profile = staff.PROFILE_BY_KEY[c["profile"]]
        bonus = None
        if profile.bonus_eur_month:
            senior = c["age"] >= staff.SENIOR_AGE
            monthly = profile.bonus_senior if senior else profile.bonus_eur_month
            per_night = format_amount(staff.night_amount(monthly * 12))
            bonus = f"Indefinido: −{per_night} de cuota cada noche, {profile.bonus_years} semanas"
        half = format_amount(staff.ask(c["skills"], c["traits"], staff.HALF_DAY))
        cards.append(
            {
                "key": c["key"],
                "name": c["name"],
                "age": c["age"],
                "seed": c["key"],
                "profile": c["profile"],
                "profile_name": profile.name,
                "bonus": bonus,
                "traits": _traits(c["traits"]),
                "skills": _skills(c["skills"], c["traits"]),
                "ask": (
                    f"Pide {half} (media)"
                    if c["part_time"]
                    else f"Pide {format_amount(c['ask'])} (completa) · {half} (media)"
                ),
                "part_time": c["part_time"],
                "selected": c["key"] == selected,
            }
        )
    deal = None
    chosen = next((c for c in candidates if c["key"] == selected), None)
    if chosen is not None and offer is not None:
        probe, slip = offer_slip(chosen, offer, business.sector, today)
        role = staff.role_of(probe)
        deal = {
            "kind": offer["contract"],
            "contract": CONTRACT_NAMES[offer["contract"]],
            "role": role.key,
            "role_name": role.name,
            "shift": horario_text(probe, short=True),
            **_horario_state(staff.horario_of(probe)),
            "gross": f"Bruto: {format_amount(slip.gross)} la noche",
            "net": f"Le llega: {format_amount(slip.net)}",
            "cost": f"Te cuesta: {format_amount(slip.cost)}",
            "bonus": (
                f"Bonificación: −{format_amount(probe['bonus'])}" if probe["bonus"] else None
            ),
            "black": offer["contract"] == "b",
        }
    room = staff.cap(business.size) - len(staff.seated(business))
    return {
        "screen": "candidatos",
        **_style(business),
        "candidates": cards,
        "offer": deal,
        "room": plural(room, "puesto libre", "puestos libres") if room > 0 else "No queda sitio",
        "foot": foot or "Cada día llegan candidatos nuevos. El período de prueba es de dos noches.",
    }


# -- Fase 5: la cadena, la nave y vender la sociedad -----------------------------------


def _island(key: str) -> str:
    return ISLANDS.get(key, key)


def nave_name(nave: chain.Nave) -> str:
    """`Nave mediana` o `Nave pequeña · propia`."""
    name = f"Nave {nave.size_info.name.lower()}"
    return f"{name} · propia" if nave.own_listing else name


def cadena_state(
    locales: list[Business], nave: chain.Nave | None, *, selected: int | None = None, foot: str = ""
) -> State:
    """La pantalla 🗺️ Cadena: una tarjeta por local y la de la nave.

    Cada local: su rótulo (`_style`), `id`, `sector_name`, `barrio`, `island`, `stars`, `today`
    (la caja de hoy), `turns`, `staff` (cuántos atienden), `manager` (si tiene gerente),
    `alert` y `selected`. `nave` es `None` o `{"barrio", "island", "size", "used", "fill"
    (0-1), "coming", "tonight"}`. `form` dice si es de autónomo o de la sociedad, `week` lo que
    lleva la semana toda la cadena y `slots` cuántos locales de cuántos.
    """
    cards = []
    week = 0
    for business in locales:
        week += sum(business.week_totals.values())
        cards.append(
            {
                **_style(business),
                "id": business.id,
                "sector_name": business.sector_info.name,
                "barrio": business.barrio_info.name,
                "island": _island(business.barrio_info.island),
                "stars": business.stars,
                "today": f"{signed(business.sales_today)} hoy",
                "turns": plural(
                    business.turns_today + business.staff_today.get("turns", 0), "turno", "turnos"
                ),
                "staff": len(staff.seated(business)),
                "manager": staff.manager(business) is not None,
                "alert": _alert(business),
                "selected": business.id == selected,
            }
        )
    store = None
    if nave is not None:
        week += sum(nave.week_totals.values())
        used = chain.used_lots(nave)
        store = {
            "barrio": nave.barrio_info.name,
            "island": _island(nave.island),
            "size": nave_name(nave),
            "used": f"{used} de {nave.size_info.lots} lotes",
            "fill": round(min(1.0, used / nave.size_info.lots), 3),
            "coming": (
                plural(len(nave.orders), "contenedor en camino", "contenedores en camino")
                if nave.orders
                else "Nada en camino"
            ),
            "tonight": f"Esta noche: {signed(-chain.nave_bill(nave).total)}",
        }
    company = bool(locales) and locales[0].is_company
    return {
        "screen": "cadena",
        **(_style(locales[0]) if locales else {}),
        "locales": cards,
        "nave": store,
        "form": f"{locales[0].name}, S.L." if company else "Autónomo",
        "week": f"Esta semana: {signed(week)}",
        "slots": f"{len(locales)} de {MAX_LOCALES} locales",
        "foot": foot
        or "Cada local tiene su plantilla y su caja de hoy; las franjas y la cuota son tuyas.",
    }


def _arrival(iso: str, today: date) -> str:
    """`llega mañana` o `llega el jueves` (la mañana siguiente a la noche en que llega)."""
    day = date.fromisoformat(iso) + timedelta(days=1)
    if day <= today:
        return "llega esta noche"
    if day == today + timedelta(days=1):
        return "llega mañana"
    return f"llega el {DAYS[day.weekday()].lower()}"


def nave_state(
    nave: chain.Nave | None,
    today: date,
    *,
    products: list[str],
    selected: str = "",
    barrio: str = "",
    size: int = 0,
    foot: str = "",
) -> State:
    """La pantalla 🏭 Nave.

    Dos modos. `alquilar` (sin nave): el barrio que se mira (`barrio`), los tamaños (`sizes`:
    `name`, `m2`, `lots`, `rent` y `selected`) y lo que cuesta abrirla (`cost`). `nave`: el
    stock (`stock`: `key`, `name`, `units`, `lots`), lo que viene (`coming`: `key`, `name`,
    `units`, `when`), lo lleno que está (`used`, `fill`), lo de esta noche (`tonight`), la deuda
    (`debt`, o `None`) y el contenedor del producto elegido (`quote`, o `None`): `key`, `name`,
    `units`, cada parte (`goods`, `freight`, `aiem` con `aiem_rate`, `igic`, `customs`), el
    total, lo que costaría al mayorista (`wholesale`), lo que se ahorra (`saving`, `good`) y
    por qué no se puede (`blocked`, o `None`).

    Args:
        products: Las claves de lo que venden sus locales de la isla de la nave (lo que se
            puede importar).
    """
    if nave is None:
        place = BARRIO_BY_KEY.get(barrio) or BARRIOS[0]
        quote = chain.nave_quote(place, size)
        return {
            "screen": "nave",
            "mode": "alquilar",
            "barrio": place.name,
            "island": _island(place.island),
            "sizes": [
                {
                    "name": f"Nave {option.name.lower()}",
                    "m2": f"{option.m2} m²",
                    "lots": f"{option.lots} lotes",
                    "rent": f"{format_amount(chain.nave_rent(place, option))} la noche + IGIC",
                    "selected": i == size,
                }
                for i, option in enumerate(chain.NAVE_SIZES)
            ],
            "cost": f"Abrirla: {format_amount(quote.total)} (tasa y fianza)",
            "foot": foot
            or "La nave reparte a tus locales de su isla. Los contenedores llegan en dos noches.",
        }
    stock = []
    for key, units in sorted(nave.stock.items()):
        item = chain.product_by_key(key)
        if item is None or units <= 0:
            continue
        stock.append(
            {
                "key": key,
                "name": item.name,
                "units": str(units),
                "lots": plural(-(-units // item.pack), "lote", "lotes"),
            }
        )
    coming = []
    for order in nave.orders:
        item = chain.product_by_key(order["product"])
        coming.append(
            {
                "key": order["product"],
                "name": item.name if item else order["product"],
                "units": str(order["units"]),
                "when": _arrival(order["arrives"], today),
            }
        )
    deal = None
    item = chain.product_by_key(selected) if selected else None
    if item is not None:
        quote = chain.import_quote(item)
        rate = chain.AIEM.get(item.key, 0.0)
        room = nave.size_info.lots - chain.used_lots(nave)
        blocked = None
        if not chain.can_import(item):
            blocked = "Caduca: no aguanta lo que dura un contenedor"
        elif item.key not in products:
            blocked = "Ninguno de tus locales de la isla lo vende"
        elif room < chain.CONTAINER_LOTS:
            blocked = f"No cabe: quedan {max(0, room)} lotes libres"
        deal = {
            "key": item.key,
            "name": item.name,
            "units": f"{quote.units} unidades ({chain.CONTAINER_LOTS} lotes)",
            "goods": format_amount(quote.goods),
            "freight": format_amount(quote.freight),
            "aiem": format_amount(quote.aiem),
            "aiem_rate": format_rate(rate).replace(",00", "") if rate else "no lleva",
            "igic": format_amount(quote.igic),
            "customs": format_amount(quote.customs + quote.customs_igic),
            "total": format_amount(quote.total),
            "wholesale": format_amount(quote.wholesale),
            "saving": signed(quote.saving),
            "good": quote.saving > 0,
            "blocked": blocked,
        }
    used = chain.used_lots(nave)
    return {
        "screen": "nave",
        "mode": "nave",
        "barrio": nave.barrio_info.name,
        "island": _island(nave.island),
        "size": nave_name(nave),
        "used": f"{used} de {nave.size_info.lots} lotes",
        "fill": round(min(1.0, used / nave.size_info.lots), 3),
        "stock": stock,
        "coming": coming,
        "tonight": f"Esta noche: {signed(-chain.nave_bill(nave).total)}",
        "debt": f"Debes {format_amount(nave.rent_debt)} de la nave" if nave.rent_debt else None,
        "quote": deal,
        "foot": foot
        or "Un contenedor llega en dos noches. Lo de la nave se lleva a cada local desde su "
        "almacén, o lo trae su gerente.",
    }


def venta_state(view: Any, offer: chain.SaleOffer, *, foot: str = "") -> State:
    """La pantalla 💼 Vender la sociedad (`negocio.CompanyView` y `business_chain.SaleOffer`).

    `results` son los resultados de las últimas semanas; `capitalized`, su media por cinco;
    `equity`, el patrimonio neto; `price`, lo que paga (el mayor, art. 37.1.b LIRPF); `loan`, lo
    que te devuelve de tu préstamo; `gain` y `tax`, la ganancia y su IRPF; `net`, lo que te
    llevas; `locales` y `staff`, lo que se va con la sociedad; `blocker`, por qué no hay
    comprador (o `None`).
    """
    business = view.business
    tax = capital_gain_tax(offer.gain)
    return {
        "screen": "venta",
        **_style(business),
        "results": [signed(r) for r in offer.results],
        "capitalized": format_amount(offer.capitalized),
        "equity": signed(offer.equity),
        "price": format_amount(offer.price),
        "loan": format_amount(offer.loan),
        "basis": format_amount(offer.basis),
        "gain": signed(offer.gain),
        "tax": format_amount(tax),
        "net": format_amount(offer.price + offer.loan - tax),
        "locales": plural(len(view.locales), "local", "locales"),
        "staff": plural(sum(len(b.staff) for b in view.locales), "empleado", "empleados"),
        "blocker": offer.blocker,
        "foot": foot
        or "Se van la caja, los locales, la nave y la plantilla. La ganancia paga el 19 %.",
    }
