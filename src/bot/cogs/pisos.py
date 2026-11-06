"""`pisos`: el mercado inmobiliario de Canarias, en un panel que es solo imágenes.

`/pisos` (o `.pisos`) abre en el canal el panel de quien lo pide. Todo se ve en una
imagen (`bot.services.housing_view` decide qué sale y `bot.services.housing_scene`
lo pinta) y se navega con el desplegable de barrios y los botones de cada pantalla:
el mapa de Gran Canaria y Tenerife, cada barrio con sus diez inmuebles (nueve, y la obra
nueva o el solar donde irá, en una rejilla de 5 × 2 con un botón por tarjeta), la ficha de
cada uno, tus pisos, las VPO y la caja de cada compra o venta. Solo lo toca quien lo
abrió; a los demás se les dice que abran el suyo.

Fases 2 a 4: en la caja se elige contado o hipoteca; desde la ficha, el dueño amortiza,
se muda, alquila el piso a otro miembro (que acepta o rechaza en un mensaje aparte),
pide la licencia vacacional o vende al fondo buitre; desde la cartera monta su SOCIMI
o deja su casa, y en la pantalla 🏛️ VPO se apunta al sorteo, compra la que le toca o
la devuelve. Lo que cuesta dinero pide un segundo clic (⚠️ ¿Seguro?).

Ampliación, fase 2 (8.1.0): la ficha abre 🧰 Gestionar, donde quien decide cambia quién
elige al inquilino (la gestora o él), contrata el seguro de impago y ve lo que costaría un
desahucio; si elige él, la pantalla 👥 Candidatos enseña a los que esperan y se firma con
uno o se descartan. Desahuciar paga el abogado y el procurador (salvo con seguro). Los
lunes, las noticias de barrio salen en `#chat-general` si alguien tiene pisos.

Ampliación, fase 3 (8.2.0): desde 🧰 Gestionar se vende a otro miembro (una oferta pública
que acepta, al contado o con hipoteca; el inquilino que es miembro y los copropietarios
tienen derecho preferente unos minutos) o se saca a subasta un día. La pantalla 🔨 Subastas
enseña las abiertas y se puja con un formulario. Las ejecuciones hipotecarias salen antes a
subasta judicial. La tarea de cada 10 minutos cierra las que acaban y lo anuncia.

Plantas y habitaciones (9.12.0): desde 🧰 Gestionar, la pantalla 🏢 Dividir enseña lo que
cuesta y da dividir el inmueble por plantas (cada planta pasa a ser un inmueble, que se
vende por separado) o alquilarlo por habitaciones (cada una con su inquilino; el piso se
vende entero y se puede volver a juntar). La pantalla de plantas o habitaciones tiene un
botón por cada una. El botón verde 👥 Candidatos (del mapa, el barrio, Mis pisos y esa
pantalla) solo sale si alguien espera en algo en que decides tú, y lleva de uno a otro.

Calles (9.14.0): desde el barrio, 🗺️ Calles abre el plano de sus tres calles con sus nueve
edificios (el N es el de la tarjeta N), y cada edificio se ve en corte, con un desplegable
de sus unidades que abre la ficha de cada una.

Dinero (todo por `EconomyService.housing`, impuestos en su docstring): comprar
participaciones (ITP del 6,5 % al Estado, notaría que desaparece), venderlas al
mercado (agencia, deudas del piso e IRPF de la ganancia) y, cada noche, la renta de
cada inmueble alquilado con su IRPF, el IBI y la comunidad. Las fases 2 a 4 (hipoteca,
VPO, bono, licencia, SOCIMI) también pasan por ahí; sus impuestos, en la Biblia. Lo de
cada noche lo cierra una tarea cada 10 minutos (`HousingService.close_days`), que
recupera hasta 7 días si el bot ha estado apagado.

Rapidez: cada clic contesta antes de leer la base de datos o pintar (`ack`), una
imagen que ya se ve no se vuelve a subir, las imágenes se guardan por su contenido
(`Painter`) y, mientras miras una pantalla, se pintan por adelantado las que se abren
desde sus botones (los 20 barrios desde el mapa, los diez inmuebles desde un barrio,
la caja de comprar entero o vender todo desde una ficha, el mapa y la cartera desde
casi todas, la ficha desde la caja…). Van de una en una, la más probable primero, y
cada una comprueba antes de empezar que el panel sigue donde estaba.

Funciones puente para otros cogs: `pending` (lo pendiente de alguien en pisos, para
`saldo`). No lanza y devuelve `[]` sin el cog.

Logros: categoría 🏠 Pisos (`housing_*_stats` de `bot.services.achievements`).

Permisos: enviar mensajes y adjuntar archivos en el canal del panel.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import random
import sqlite3
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks

from bot.cogs import achievements as logros
from bot.cogs import banco, renta
from bot.cogs import banco_compras as financiar
from bot.cogs import pets as mascotas
from bot.cogs import shop as tienda
from bot.cogs import work as curro
from bot.repositories.economy import BalanceLimitError, InsufficientFundsError
from bot.repositories.housing import (
    AuctionRun,
    DayRun,
    Eviction,
    HousingError,
    HousingRepository,
    KeysDeal,
    Market,
    MemberSale,
    PriceChanged,
    Purchase,
    Sale,
    TenantDeal,
    VoteResult,
)
from bot.repositories.housing_finance import HOME_OWN, HOME_RENTED, vpo_ref
from bot.services import credit
from bot.services import housing as rules
from bot.services import housing_view as view
from bot.services.achievements import (
    StatDelta,
    advance_orphan_stats,
    guarantee_night_stats,
    housing_action_stats,
    housing_day_stats,
    housing_keys_stats,
    housing_panel_stats,
    housing_purchase_stats,
    housing_sale_stats,
    housing_streets_stats,
    housing_tax_stats,
    loan_night_stats,
    remortgage_executed_stats,
)
from bot.services.economy import EconomyService, format_amount
from bot.services.housing_catalog import (
    BARRIO_BY_KEY,
    BARRIO_NEWS_BY_KEY,
    BARRIOS,
    ISLANDS,
    KEYS_LINES,
    KINDS,
    LISTING_BY_KEY,
    REFORM_BY_KEY,
    REFORMS,
    RENT_LEVELS,
    TENANT_BY_KEY,
    Listing,
    RentLevel,
    listings_of,
)
from bot.services.housing_market import HousingService
from bot.services.housing_scene import HousingScene
from bot.services.housing_streets import (
    BUILDING_BY_KEY,
    building_of,
    buildings_of,
    map_order,
    streets_of,
)
from bot.services.levels import TIMEZONE
from bot.services.pending import Pending
from bot.services.pets import Event, Moment
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

GAME = "pisos"
PNG_NAME = "pisos.png"
#: El panel caduca tras este rato sin tocarlo.
VIEW_TIMEOUT = 15 * 60
#: Imágenes pintadas que se guardan (unos 200-400 KB cada una, ~25 MB en total). Desde el mapa se
#: pintan por adelantado los 20 barrios (más la cartera y las VPO) y desde un barrio sus 10
#: fichas, el mapa y la cartera (13 con el propio barrio): con 80 caben a la vez, y los barrios
#: ya vistos aguantan al ir y volver del mapa, sin que unas echen a otras.
CACHE_SIZE = 80
#: Botones de inmuebles por fila de la pantalla de un barrio (el máximo de Discord).
BARRIO_COLS = 5
#: Inmuebles de tu cartera que salen en su desplegable (el máximo de Discord).
MAX_OPTIONS = 25

MAPA, BARRIO, FICHA, CARTERA, CAJA, VPO = "mapa", "barrio", "ficha", "cartera", "caja", "vpo"
GESTION, CANDIDATOS, SUBASTAS = "gestion", "candidatos", "subastas"
REFORMAS, IMPREVISTOS, RESENAS, JUNTA = "reformas", "imprevistos", "resenas", "junta"
PLANTAS, DIVIDIR = "plantas", "dividir"
CALLES, EDIFICIO = "calles", "edificio"
#: 🔑 Recuperar el piso (9.23.0): se ve la ficha y cambian los botones.
LLAVES = "llaves"
#: Cómo se llama cada edificio en su botón del plano (caben cinco por fila en el móvil).
SHORT_BUILDING = {
    "bloque": "Bloque", "aparcamiento": "Parking", "guardamuebles": "Trasteros", "casa": "Casa",
    "complejo": "Bungalows", "pantalan": "Pantalán", "poligono": "Naves", "edificio": "Edificio",
}  # fmt: skip
#: Minutos que tienen el inquilino que es miembro y los copropietarios para quedarse lo que
#: se vende a otro (tanteo del art. 25 LAU y retracto de comuneros del art. 1522 CC).
PREFERENCE_SECONDS = 10 * 60
#: La oferta de alquiler a un miembro caduca tras este rato.
OFFER_TIMEOUT = 30 * 60
#: Lo que le pasa a un miembro y no al inmueble: sus logros van solo a él, no a los dueños.
PERSONAL_EVENTS = frozenset(
    {
        "ejecucion",
        "sorteo_vpo",
        "vpo_caducada",
        "partida_agotada",
        "oferta_fondo",
        "fianza_devuelta",
        "fianza_incautada",
        "licencia_renovada",
        "licencia_caduca",
    }
)
#: Canal del anuncio del BCE de los lunes (el mismo que el del Patrimonio).
ANNOUNCE_CHANNEL_NAME = "chat-general"


def _pct(value: float) -> str:
    """`0.0225` → `2,25 %`."""
    return f"{value * 100:.2f}".replace(".", ",") + " %"


def effort_text(rent: int, income: int) -> str:
    """«Destinarías el 58 % de tu sueldo al alquiler», con la vara del Banco de España."""
    rate = rules.effort(rent, income)
    if rate is None:
        return (
            "📉 Sin nómina de `pala` ni negocio en 30 días: la renta te comería el 100 % de "
            "nada. El Banco de España no tiene palabra para eso."
        )
    pct = round(rate * 100)
    if rate > 1:
        return (
            f"📉 La renta se lleva el {pct} % de tu sueldo: trabajas para pagar el piso y "
            "el piso para pagar a tu casero."
        )
    if rate > rules.OVERBURDEN:
        return (
            f"📉 Destinarías el {pct} % de tu sueldo al alquiler. El Banco de España llama "
            f"sobreesfuerzo a pasar del {round(rules.OVERBURDEN * 100)} %."
        )
    return f"📈 Destinarías el {pct} % de tu sueldo al alquiler. Eres un unicornio."


def requirements_text(rent: int, income: int) -> str:
    """Lo que pide el casero: fianza y, sin nómina de 3 veces la renta, garantía extra."""
    deposit = format_amount(rules.deposit_for(rent, guarantee=False))
    if not rules.needs_guarantee(rent, income, aval=False):
        return f"🧾 Fianza de un mes: {deposit}. Tu nómina pasa de 3 veces la renta: ni aval."
    extra = format_amount(rules.deposit_for(rent, guarantee=True))
    return (
        f"🧾 Tu nómina no llega a 3 veces la renta: fianza y garantía adicional de dos "
        f"meses ({extra} en total) salvo que te avalen tus padres (favor de `pala`), y "
        f"entonces solo la fianza ({deposit}). Vuelve cuando te vayas."
    )


def years_text(days: int) -> str:
    """«1 año» o «5 años» para una duración en días del bot (un día, un mes)."""
    years = days // 12
    return "1 año" if years == 1 else f"{years} años"


def odds_text(odds: float) -> str:
    """Una probabilidad para un botón: «60 %»."""
    return f"{round(odds * 100)} %"


def term_note(listing: Listing, level: RentLevel, term: int) -> str:
    """Lo que cambia firmar `term` días, para la descripción del desplegable."""
    find, risk = rules.term_factors(listing, level, term)
    if term == rules.TERM_DEFAULT:
        return "Lo de siempre: el máximo de candidatos y el menor impago"
    return (
        f"{round((1 - find) * 100)} % menos candidatos y {round((risk - 1) * 100)} % más "
        "impago; lo recuperas antes"
    )


def signed_text(deal: TenantDeal) -> str:
    """Lo que dejó en el depósito de fianzas al firmar."""
    if deal.aval:
        return f"👴 Sus padres le avalan: solo deja la fianza ({format_amount(deal.deposit)})."
    if deal.guarantee:
        return f"🧾 Deja fianza y garantía adicional: {format_amount(deal.deposit)}."
    return f"🧾 Deja la fianza: {format_amount(deal.deposit)}."


def promotion_embed(listing: Listing, index: float, builder: str) -> discord.Embed:
    """Anuncio de la obra nueva del lunes: qué se ha levantado, dónde y quién se lo lleva."""
    barrio = BARRIO_BY_KEY[listing.barrio]
    embed = discord.Embed(
        title=f"🏗️ {listing.title.removeprefix('Obra nueva: ')}",
        description=(
            f"**{builder}** levanta en **{barrio.name}** ({barrio.town}) un "
            f"{KINDS[listing.kind].name.lower()} de {listing.m2} m²: "
            f"**{format_amount(rules.price(listing, index))}**.\n*{listing.blurb}*\n\n"
            f"Se compra en `pisos`, en su barrio. De cada participación que se venda, el "
            f"{round(rules.PROMOTER_FEE * 100)} % va al promotor."
        ),
        color=discord.Color.orange(),
    )
    embed.set_footer(text="Obra nueva de la rama Ladrillo de pala: una por barrio, cada lunes.")
    return embed


def news_embed(items: list[tuple[str, str]]) -> discord.Embed:
    """Las noticias de barrio del lunes: una línea por barrio, con lo que cambia esta semana."""
    lines = []
    for barrio, key in items:
        news = BARRIO_NEWS_BY_KEY[key]
        lines.append(f"**{BARRIO_BY_KEY[barrio].name}:** {news.title}. {news.blurb}")
    return discord.Embed(
        title="📰 Noticias de barrio",
        description="\n".join(lines),
        color=discord.Color.dark_gold(),
    ).set_footer(text="Durante una semana cambian la demanda, el impago o las averías allí.")


def ecb_embed(before: float, after: float, week: date) -> discord.Embed:
    """Anuncio de los lunes: lo que hace el Euríbor y la frase del BCE."""
    if after > before:
        move = "sube"
    elif after < before:
        move = "baja"
    else:
        move = "se queda"
    embed = discord.Embed(
        title="🏦 Fráncfort ha hablado",
        description=(
            f"*«{rules.ecb_phrase(before, after, week)}»*\n\n"
            f"El Euríbor {move}: **{_pct(before)} → {_pct(after)}**. Las hipotecas que se "
            f"firmen esta semana van al **{_pct(after + rules.MORTGAGE_SPREAD)} fijo**; las "
            "que ya tienes no cambian."
        ),
        color=discord.Color.dark_blue(),
    )
    embed.set_footer(text="Euríbor simulado: se mueve un cuarto de punto cada lunes, del 0 al 5 %.")
    return embed


def _name(guild: discord.Guild | None, user_id: int) -> str:
    member = guild.get_member(user_id) if guild is not None else None
    return member.display_name if member is not None else "Alguien"


class Painter:
    """Pinta pantallas y las guarda por su contenido, para no pintar dos veces lo mismo.

    Los dibujos por adelantado (`prefetch`) van de uno en uno y por orden (el primero
    es el que más probablemente se pulse): antes de empezar cada uno se mira si ya
    está guardado o se está pintando y si sigue valiendo (el panel no ha cambiado de
    pantalla). Si llegan otros mientras se pinta uno, los anteriores se sueltan: ya no
    se van a ver. Si se pulsa algo que se está pintando por adelantado, se espera a
    ese dibujo en vez de pintarlo otra vez.

    Args:
        scene: Lo que pinta (Node, Chromium o Pillow).
    """

    def __init__(self, scene: HousingScene) -> None:
        self.scene = scene
        self.cache: OrderedDict[str, bytes] = OrderedDict()
        self._next: list[tuple[dict[str, Any], Callable[[], bool] | None]] = []
        self._worker: asyncio.Task[None] | None = None
        self._painting: dict[str, asyncio.Future[None]] = {}

    @staticmethod
    def key(state: dict[str, Any]) -> str:
        """Huella de una pantalla: dos pantallas iguales se pintan igual."""
        text = json.dumps(state, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha1(text.encode()).hexdigest()

    def _store(self, key: str, png: bytes) -> None:
        self.cache[key] = png
        self.cache.move_to_end(key)
        while len(self.cache) > CACHE_SIZE:
            self.cache.popitem(last=False)

    async def paint(self, states: list[dict[str, Any]]) -> list[bytes]:
        """PNG de cada pantalla, pintando solo las que no estén guardadas ni en marcha."""
        keys = [self.key(s) for s in states]
        while True:
            missing: dict[str, dict[str, Any]] = {}
            waiting: list[asyncio.Future[None]] = []
            for key, state in zip(keys, states, strict=True):
                if key in self.cache:
                    self.cache.move_to_end(key)
                elif key in self._painting:
                    waiting.append(self._painting[key])
                else:
                    missing.setdefault(key, state)
            if not missing:
                if not waiting:
                    return [self.cache[k] for k in keys]
                await asyncio.wait(waiting)  # un dibujo por adelantado: se espera a ese
                continue  # (si falló o se guardó, se mira otra vez)
            marks = {key: asyncio.get_running_loop().create_future() for key in missing}
            self._painting.update(marks)
            try:
                pngs = await self.scene.paint(list(missing.values()))
                for key, png in zip(missing, pngs, strict=True):
                    self._store(key, png)
            finally:
                for key, mark in marks.items():
                    self._painting.pop(key, None)
                    mark.set_result(None)
            if waiting:
                await asyncio.wait(waiting)
            return [self.cache[k] for k in keys]

    def prefetch(
        self, states: list[dict[str, Any]], still: Callable[[], bool] | None = None
    ) -> None:
        """Pinta en segundo plano las pantallas que pueden venir después, de una en una.

        Args:
            states: Las pantallas, de la más a la menos probable.
            still: Si ya no vale (el panel ha cambiado de pantalla), devuelve `False` y
                lo que queda de esta tanda se suelta.
        """
        self._next = [(s, still) for s in states if self.key(s) not in self.cache]
        if self._next and (self._worker is None or self._worker.done()):
            self._worker = asyncio.create_task(self._work(), name="pisos-adelanto")

    async def _work(self) -> None:
        while self._next:
            state, still = self._next.pop(0)
            if (still is not None and not still()) or self.key(state) in self.cache:
                continue
            try:
                await self.paint([state])
            except Exception:
                logger.exception("No se pudo pintar por adelantado una pantalla de pisos")

    async def close(self) -> None:
        """Para lo pendiente y cierra el pintor."""
        self._next = []
        if self._worker is not None:
            self._worker.cancel()
        await self.scene.close()


class SharesModal(ui.Modal):
    """Cuántas participaciones comprar o vender."""

    def __init__(self, panel: Panel, *, selling: bool, most: int) -> None:
        super().__init__(title="Vender participaciones" if selling else "Comprar participaciones")
        self.panel = panel
        self.selling = selling
        self.most = most
        self.amount: ui.TextInput = ui.TextInput(
            # Discord corta la etiqueta en 45 caracteres: la explicación va en el ejemplo.
            label=f"Participaciones (1 a {most})",
            placeholder="100 participaciones son el inmueble entero",
            default=str(most),
            min_length=1,
            max_length=3,
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Valida el número y enseña la caja."""
        text = self.amount.value.strip()
        if not text.isdigit() or not 1 <= int(text) <= self.most:
            await notify(interaction, f"Pon un número de 1 a {self.most}.")
            return
        await ack(interaction)
        await self.panel.open_checkout(interaction, selling=self.selling, shares=int(text))


def _number(text: str) -> int | None:
    """Un número escrito en un formulario («150.000», «150000» o «150 000»), o `None`."""
    digits = text.strip().replace(".", "").replace(" ", "").replace("Y$", "").strip()
    return int(digits) if digits.isdigit() else None


class TradeModal(ui.Modal):
    """Participaciones y precio: para vender a un miembro o para sacar a subasta."""

    def __init__(self, panel: Panel, *, auction: bool, most: int, value: int) -> None:
        super().__init__(title="Sacar a subasta" if auction else "Vender a un miembro")
        self.panel = panel
        self.auction = auction
        self.most = most
        self.shares: ui.TextInput = ui.TextInput(
            label=f"Participaciones (1 a {most})", default=str(most), min_length=1, max_length=3
        )
        self.price: ui.TextInput = ui.TextInput(
            label="Puja mínima (Y$)" if auction else "Precio de todas (Y$)",
            placeholder=f"Valen {format_amount(value)} a precio de mercado",
            default=str(value),
            min_length=1,
            max_length=15,
        )
        self.add_item(self.shares)
        self.add_item(self.price)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Valida las cifras y sigue: elegir comprador o abrir la subasta."""
        shares, price = _number(self.shares.value), _number(self.price.value)
        if shares is None or not 1 <= shares <= self.most:
            await notify(interaction, f"Pon un número de participaciones de 1 a {self.most}.")
            return
        if price is None or price < 1:
            await notify(interaction, "Pon un precio en Y$, mayor que cero.")
            return
        if self.auction:
            await self.panel.open_auction(interaction, shares, price)
        else:
            await self.panel.choose_buyer(interaction, shares, price)


class LevyModal(ui.Modal):
    """Cuánto se propone de derrama al fondo de reserva."""

    def __init__(self, panel: Panel) -> None:
        super().__init__(title="Derrama al fondo de reserva")
        self.panel = panel
        self.amount: ui.TextInput = ui.TextInput(
            label="Derrama total (Y$)",
            placeholder=(
                f"De {format_amount(rules.LEVY_MIN)} a {format_amount(rules.LEVY_MAX)}; "
                "cada dueño paga su parte"
            ),
            min_length=1,
            max_length=15,
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Valida la cifra y la propone."""
        amount = _number(self.amount.value)
        if amount is None or amount < 1:
            await notify(interaction, "Pon la derrama en Y$.")
            return
        await self.panel.propose(interaction, "derrama", "", amount)


class BidModal(ui.Modal):
    """Cuánto se puja en una subasta."""

    def __init__(self, panel: Panel, auction_id: int, least: int) -> None:
        super().__init__(title="Pujar en la subasta")
        self.panel = panel
        self.auction_id = auction_id
        self.amount: ui.TextInput = ui.TextInput(
            label="Tu puja (Y$)",
            placeholder=f"Como poco {format_amount(least)}; además dejas el ITP y la notaría",
            default=str(least),
            min_length=1,
            max_length=15,
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Valida la cifra y puja."""
        amount = _number(self.amount.value)
        if amount is None or amount < 1:
            await notify(interaction, "Pon la puja en Y$.")
            return
        await self.panel.place_bid(interaction, self.auction_id, amount)


class Panel(ui.View):
    """El panel de `pisos` de un miembro: una pantalla a la vez, todas en imagen."""

    def __init__(self, cog: Pisos, *, guild: discord.Guild, owner: discord.abc.User) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild = guild
        self.owner = owner
        self.screen = MAPA
        self.barrio = BARRIOS[0].key
        self.listing = listings_of(self.barrio)[0].key
        #: El edificio de la pantalla 🏢 Edificio.
        self.building = f"{self.barrio}_e1"
        #: Caja abierta: `(vende, participaciones)`.
        self.pending: tuple[bool, int] | None = None
        #: Si la compra de la caja va con hipoteca.
        self.financed = False
        #: Botón que pide un segundo clic para hacerse (`amortizar`, `devolver`…).
        self.confirm: str | None = None
        #: Sello de la caja tras pagar (`PAGADO`, `VENDIDO`) y su resultado.
        self.done: tuple[str, Purchase | Sale] | None = None
        #: Venta a un miembro a medias: `(participaciones, precio)`, a falta del comprador.
        self.sale_draft: tuple[int, int] | None = None
        #: La subasta elegida en la pantalla 🔨 Subastas.
        self.auction_id: int | None = None
        #: La página de 🔑 Mis pisos, desde 0 (◀ ▶). Se queda al ir a una ficha y volver.
        self.page = 0
        self.market = Market()
        self.cash = 0
        self.message: discord.Message | None = None
        self._shown: str | None = None
        self._last: discord.Interaction | None = None

    # -- Pantallas ------------------------------------------------------------------

    def _at(self, key: str) -> Listing:
        """El inmueble de una clave del mercado de este servidor (la obra nueva incluida)."""
        listing = self.market.listing(key)
        if listing is None:
            raise KeyError(key)
        return listing

    def _names(self, market: Market) -> dict[int, str]:
        members = {m for holders in market.holdings.values() for m in holders}
        members |= {owner.owner_id for owner in market.vpo.values()}
        members |= {member for member, _until in market.reservations.values()}
        members |= set(market.homes)
        return {m: _name(self.guild, m) for m in members}

    async def refresh(self) -> None:
        """Lee el mercado y el saldo (una consulta cada uno)."""
        service = self.cog.service
        self.market = await service.market(self.guild.id)
        self.cog.market_cache[self.guild.id] = self.market
        self.cash = await self.cog.economy.balance(self.guild.id, self.owner.id)

    async def _state(
        self,
        screen: str,
        *,
        barrio: str | None = None,
        listing: str | None = None,
        building: str | None = None,
        page: int | None = None,
    ) -> dict[str, Any]:
        market, me = self.market, self.owner.id
        names = self._names(market)
        today = self.cog.service.today()
        if screen == LLAVES:
            screen = FICHA  # 🔑 Recuperar el piso se ve sobre la ficha
        if screen == CALLES:
            return view.streets_state(market, barrio or self.barrio, me, today)
        if screen == EDIFICIO:
            return view.building_state(market, building or self.building, me, names, today)
        if screen == BARRIO:
            return view.barrio_state(market, barrio or self.barrio, me, names, today)
        if screen == FICHA:
            key = listing or self.listing
            news = await self.cog.service.news(self.guild.id, (key,), 4)
            return view.listing_state(market, key, me, names, news, today)
        if screen == GESTION:
            return view.gestion_state(market, listing or self.listing, me, names, today)
        if screen == CANDIDATOS:
            return view.candidates_state(market, listing or self.listing, today)
        if screen == REFORMAS:
            return view.reforms_state(market, listing or self.listing, me)
        if screen == PLANTAS:
            return view.units_state(market, listing or self.listing, me, names, today)
        if screen == DIVIDIR:
            return view.split_state(market, listing or self.listing, me)
        if screen == IMPREVISTOS:
            return view.decisions_state(market, listing or self.listing, me)
        if screen == RESENAS:
            owned = await tienda.owned_keys(self.cog.bot, self.guild.id, me)
            return view.reviews_state(market, listing or self.listing, owned)
        if screen == JUNTA:
            return view.junta_state(
                market, listing or self.listing, me, names, self.cog.service.now()
            )
        if screen == SUBASTAS:
            return view.auctions_state(market, me, names, self.cog.service.now(), self.auction_id)
        if screen == CARTERA:
            mine = tuple(h.listing for h in market.of_member(me))
            news = await self.cog.service.news(self.guild.id, mine, 6)
            effort = None
            home = market.homes.get(me)
            if home is not None and home[0] == HOME_RENTED and home[1] in market.leases:
                income = await self.cog.service.work_income(self.guild.id, me)
                effort = rules.effort(market.leases[home[1]].rent, income)
            return view.portfolio_state(
                market, me, self.owner.display_name, self.cash, news, names, effort, today,
                page=self.page if page is None else page,
            )  # fmt: skip
        if screen == CAJA:
            return self._checkout_state()
        if screen == VPO:
            return await self._vpo_state()
        return view.map_state(market, me, self.owner.display_name, self.cash, today)

    async def _vpo_state(self) -> dict[str, Any]:
        market, me = self.market, self.owner.id
        income = await self.cog.service.work_income(self.guild.id, me)
        home = market.homes.get(me)
        reason = ""
        if market.vpo_of(me) is not None:
            reason = "Ya tienes tu VPO"
        elif market.owns_home(me) or (home is not None and home[0] != HOME_RENTED):
            reason = "Ya tienes casa: la VPO es para quien no tiene"
        elif income > rules.vpo_income_cap():
            reason = "Ganas más de 3,5 veces el IPREM: eres clase media"
        today = self.cog.service.today()
        # El sorteo sale al cerrar el lunes (la noche del lunes al martes).
        monday = today + timedelta(days=-today.weekday() % 7)
        return view.vpo_state(
            market, me, self._names(market), eligible=not reason, reason=reason,
            next_draw=f"lunes {monday:%d/%m}", income=income,
        )  # fmt: skip

    def _checkout_state(self, stamp: str | None = None) -> dict[str, Any]:
        listing = self._at(self.listing)
        index = self.market.index(listing.barrio)
        selling, shares = self.pending or (False, 1)
        if self.done is not None:
            stamp, deal = self.done
            index = deal.index_before if isinstance(deal, Purchase) else index
        if selling:
            if self.done is not None and isinstance(self.done[1], Sale):
                quote = self.done[1].quote
            else:
                held = self.market.holdings.get(self.listing, {}).get(self.owner.id)
                lease = self.market.leases.get(self.listing, rules.Lease())
                owned = held.shares if held else shares
                quote = rules.sale_quote(
                    listing,
                    min(shares, owned),
                    owned,
                    held.cost if held else 0,
                    index=index,
                    arrears=lease.arrears,
                    arrears_ibi=lease.arrears_ibi,
                    pool=sum(self.market.shares_of(self.listing).values()),
                )
            lines = [
                (f"Precio ({quote.shares} participaciones)", quote.price, "normal"),
                ("Agencia 3 %", -quote.fee, "normal"),
                ("IGIC de la agencia 7 %", -quote.fee_igic, "tax"),
            ]
            if quote.arrears:
                lines.append(("Deudas del piso (comunidad e IBI)", -quote.arrears, "normal"))
            if quote.gain_tax:
                lines.append(("IRPF de la ganancia 19 %", -quote.gain_tax, "tax"))
            lines.append(("Cobras", quote.received, "total"))
            after = self.cash if self.done else self.cash + quote.received
            before = self.cash - quote.received if self.done else self.cash
            if quote.gain >= 0:
                note = f"Ganas {format_amount(quote.gain)} sobre lo que te costó"
            else:
                note = f"Pierdes {format_amount(-quote.gain)}: el ladrillo nunca baja, dijeron"
            return view.checkout_state(
                listing,
                selling=True,
                lines=lines,
                balance=before,
                after=after,
                note=note,
                stamp=stamp,
            )
        rate = self.market.euribor + rules.MORTGAGE_SPREAD
        if self.done is not None and isinstance(self.done[1], Purchase):
            quote_b, loan, payment = self.done[1].quote, self.done[1].loan, self.done[1].payment
        else:
            quote_b = rules.purchase_quote(listing, shares, index)
            loan = round(quote_b.price * rules.MORTGAGE_LTV) if self.financed else 0
            payment = rules.installment(loan, rate) if loan else 0
        whole = quote_b.shares == rules.SHARES
        lines = [
            (
                "Precio (el inmueble entero)"
                if whole
                else f"Precio ({quote_b.shares} participaciones)",
                quote_b.price,
                "normal",
            ),
            ("ITP 6,5 %", quote_b.itp, "tax"),
            ("Notaría, registro y gestoría", quote_b.notary, "normal"),
            ("IGIC de la notaría 7 %", quote_b.notary_igic, "tax"),
            ("Total", quote_b.total, "normal" if loan else "total"),
        ]
        if loan:
            lines.append(("Hipoteca del banco (80 % del precio)", -loan, "normal"))
            lines.append(("Sale de tu bolsillo", quote_b.total - loan, "total"))
        pocket = quote_b.total - loan
        after = self.cash if self.done else self.cash - pocket
        before = self.cash + pocket if self.done else self.cash
        rise = rules.index_after(index, listing, quote_b.shares, bought=True) / index - 1
        barrio = BARRIO_BY_KEY[listing.barrio].name
        note = f"Comprar sube el precio de {barrio} un {rise * 100:.2f} %".replace(".", ",")
        if loan:
            note = (
                f"Cuota: {format_amount(payment)} al mes durante 30 años (360 días) al "
                f"{rate * 100:.2f} % fijo".replace(".", ",")
                + ". El banco no presta si pasa del 35 % de lo que ganas"
            )
        return view.checkout_state(
            listing,
            selling=False,
            lines=lines,
            balance=before,
            after=after,
            note=note,
            stamp=stamp,
        )

    def _neighbours(self) -> list[tuple[str, dict[str, Any]]]:
        """Pantallas que se abren desde la de ahora, de la más a la menos probable.

        Salen con los datos que ya hay en memoria (volver al mapa, la cartera, la ficha
        de un inmueble desde su barrio…) y se pintan en este orden, de una en una.
        """
        me = self.owner.id
        if self.screen == MAPA:
            mine = {self._at(h.listing).barrio for h in self.market.of_member(me)}
            # Los barrios donde ya tienes algo van primero (`sorted` es estable).
            barrios = sorted(BARRIOS, key=lambda b: b.key not in mine)
            return [(CARTERA, {}), (VPO, {})] + [(CALLES, {"barrio": b.key}) for b in barrios]
        if self.screen == BARRIO:
            fichas = [(FICHA, {"listing": i.key}) for i in self.market.listings_of(self.barrio)]
            return [*fichas, (CALLES, {}), (MAPA, {}), (CARTERA, {})]
        if self.screen == CALLES:
            buildings = [
                (EDIFICIO, {"building": b.key})
                for street in streets_of(self.barrio)
                for b in buildings_of(street.key)
            ]
            return [*buildings, (BARRIO, {}), (MAPA, {})]
        if self.screen == EDIFICIO:
            units = view.building_units(self.market, BUILDING_BY_KEY[self.building])
            return [*((FICHA, {"listing": u.key}) for u in units[:6]), (CALLES, {})]
        if self.screen == FICHA:
            barrio = self._at(self.listing).barrio
            mine = me in self.market.shares_of(self.listing)
            extra = [(GESTION, {"listing": self.listing})] if mine else []
            if (parent := rules.parent_key(self.listing)) is not None:
                extra.append((PLANTAS, {"listing": parent}))
            return [*extra, (BARRIO, {"barrio": barrio}), (CARTERA, {}), (MAPA, {})]
        if self.screen == PLANTAS:
            units = [(FICHA, {"listing": u.key}) for u in self.market.units_of(self.listing)]
            back = (FICHA if self._rooms() else BARRIO, {"listing": self.listing})
            return [*units, back, (MAPA, {})]
        if self.screen == DIVIDIR:
            return [(GESTION, {"listing": self.listing}), (FICHA, {"listing": self.listing})]
        if self.screen == GESTION:
            return [
                (CANDIDATOS, {"listing": self.listing}),
                (FICHA, {"listing": self.listing}),
                (CARTERA, {}),
                (MAPA, {}),
            ]
        if self.screen == CANDIDATOS:
            return [(GESTION, {"listing": self.listing}), (FICHA, {"listing": self.listing})]
        if self.screen in (REFORMAS, IMPREVISTOS, RESENAS, JUNTA):
            return [(FICHA, {"listing": self.listing}), (GESTION, {"listing": self.listing})]
        if self.screen == CARTERA:
            page, pages, keys = view.portfolio_page(self.market, me, self.page)
            turns = [(CARTERA, {"page": p}) for p in (page + 1, page - 1) if 0 <= p < pages]
            mine = [k for k in keys if not k.startswith(view.VPO_PREFIX)][:6]
            return [*turns, *((FICHA, {"listing": k}) for k in mine), (MAPA, {}), (VPO, {})]
        if self.screen == VPO:
            return [(MAPA, {}), (CARTERA, {})]
        if self.screen == SUBASTAS:
            return [(MAPA, {}), (CARTERA, {})]
        if self.screen == CAJA:  # cancelar o «ver la ficha», y los atajos de siempre
            return [(FICHA, {"listing": self.listing}), (CARTERA, {}), (MAPA, {})]
        return []

    async def _prefetch(self) -> None:
        """Deja pintando en segundo plano lo que se puede pulsar desde esta pantalla."""
        here = self._where()
        checkouts: list[dict[str, Any]] = []
        saved = self.pending, self.financed
        if self.screen == FICHA and self.done is None:
            # La caja de comprar todo lo libre (al contado y con hipoteca) o vender todo
            # lo tuyo: los botones que más se pulsan desde una ficha.
            for selling, shares in self._default_checkouts():
                for financed in (False, True) if not selling else (False,):
                    self.pending, self.financed = (selling, shares), financed
                    checkouts.append(self._checkout_state())
        elif self.screen == CAJA and self.done is None and self.pending and not self.pending[0]:
            # La misma caja con el otro modo de pago (el botón de al lado).
            self.financed = not self.financed
            checkouts.append(self._checkout_state())
        self.pending, self.financed = saved
        states = list(checkouts)
        for screen, where in self._neighbours():
            states.append(await self._state(screen, **where))
        if self._where() == here:  # si ya han cambiado de pantalla, lo de esta ya no vale
            self.cog.painter.prefetch(states, still=lambda: self._where() == here)

    def _where(self) -> tuple[Any, ...]:
        """Dónde está el panel: si cambia, lo que se pintaba por adelantado ya no vale."""
        return (
            self.screen,
            self.barrio,
            self.listing,
            self.building,
            self.pending,
            self.financed,
            self.done is None,
            self.page,
        )

    def _default_checkouts(self) -> list[tuple[bool, int]]:
        out = []
        if free := self.market.free_shares(self.listing):
            out.append((False, free))
        if held := self.market.holdings.get(self.listing, {}).get(self.owner.id):
            out.append((True, held.shares))
        return out

    async def show(
        self,
        interaction: discord.Interaction | None,
        *,
        content: str | None = None,
        send: Callable[..., Awaitable[discord.Message]] | None = None,
    ) -> None:
        """Pinta la pantalla de ahora y la pone en el mensaje (o lo manda, la primera vez)."""
        await self.refresh()
        if content is None and self.screen == LLAVES:
            content = self._keys_text()
        elif content is None and self.screen in (REFORMAS, DIVIDIR):
            content = self._finance_hint()
        state = self._checkout_state() if self.screen == CAJA else await self._state(self.screen)
        key = Painter.key(state)
        self.rebuild()
        if send is not None:
            png = (await self.cog.painter.paint([state]))[0]
            self.message = await send(
                content=content, file=discord.File(io.BytesIO(png), filename=PNG_NAME), view=self
            )
        elif interaction is not None and key == self._shown:
            # Ya se ve esta imagen: subirla otra vez cuesta ~1 s y no cambia nada.
            self._last = interaction
            await edit(interaction, content=content, view=self)
        elif interaction is not None:
            png = (await self.cog.painter.paint([state]))[0]
            self._last = interaction
            await edit(
                interaction,
                content=content,
                embed=None,
                attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
                view=self,
            )
        self._shown = key
        await self._prefetch()

    # -- Componentes ----------------------------------------------------------------

    def rebuild(self) -> None:
        """Monta el desplegable y los botones de la pantalla de ahora."""
        self.clear_items()
        if self.screen == CAJA:
            self._caja_buttons()
            return
        select: ui.Select = ui.Select(
            placeholder="Elige un barrio",
            custom_id=f"{GAME}:barrio",
            row=0,
            options=[
                discord.SelectOption(
                    label=b.name,
                    value=b.key,
                    description=f"{ISLANDS[b.island]} · {b.town}"[:100],
                    emoji="🏝️" if b.island == "gc" else "🌋",
                    default=self.screen in (BARRIO, FICHA) and b.key == self.barrio,
                )
                for b in BARRIOS
            ],
        )
        select.callback = self._pick_barrio  # type: ignore[method-assign]
        self.add_item(select)
        if self.screen == BARRIO:
            # Una rejilla de 5 × 2 que cuadra con las tarjetas de la imagen: el botón N abre la
            # tarjeta N (el precio ya sale en la imagen, aquí solo el número y el tipo).
            items = self.market.listings_of(self.barrio)
            for slot, item in enumerate(items):
                new = "🏗️ " if item.key in self.market.promotions else ""
                if self.market.split_mode(item.key) == rules.FLOORS:
                    new = "🏢 "
                self._button(
                    f"{new}{slot + 1} · {KINDS[item.kind].name}",
                    f"ver:{slot}",
                    self._open_listing,
                    row=1 + slot // BARRIO_COLS,
                    style=discord.ButtonStyle.primary,
                )
            if len(items) < view.BARRIO_SLOTS:  # el solar de la imagen: sin nada que abrir
                slot = len(items)
                self._button(
                    f"🚧 {slot + 1} · Solar",
                    f"ver:{slot}",
                    self._open_listing,
                    row=1 + slot // BARRIO_COLS,
                    disabled=True,
                )
            self._button("🗺️ Calles", "calles", self._go_streets, row=3,
                         style=discord.ButtonStyle.primary)  # fmt: skip
            self._button("🗺️ Mapa", "mapa", self._go_map, row=3)
            self._button("🔑 Mis pisos", "cartera", self._go_portfolio, row=3)
            self._waiting_button(row=3)
        elif self.screen == CALLES:
            self._streets_buttons()
        elif self.screen == EDIFICIO:
            self._building_buttons()
        elif self.screen == FICHA:
            self._ficha_buttons()
        elif self.screen == LLAVES:
            self._keys_buttons()
        elif self.screen == CARTERA:
            self.page, pages, keys = view.portfolio_page(self.market, self.owner.id, self.page)
            held = {h.listing: h for h in self.market.of_member(self.owner.id)}
            mine = [held[k] for k in keys if k in held]  # la VPO tiene su botón 🏛️
            if mine:
                pick: ui.Select = ui.Select(
                    placeholder="Abre uno de tus pisos",
                    custom_id=f"{GAME}:mio",
                    row=1,
                    options=[
                        discord.SelectOption(
                            label=self._at(h.listing).title[:100],
                            value=h.listing,
                            description=f"{BARRIO_BY_KEY[self._at(h.listing).barrio].name}"
                            f" · {h.shares} %{self._waiting_note(h.listing)}",
                        )
                        for h in mine
                    ],
                )
                pick.callback = self._pick_mine  # type: ignore[method-assign]
                self.add_item(pick)
            self._button("🗺️ Mapa", "mapa", self._go_map, row=2)
            self._button("🏛️ VPO", "vpo", self._go_vpo, row=2)
            self._auctions_button(row=2)
            self._waiting_button(row=2)
            if pages > 1:
                self._button("◀", "pagina:antes", self._page_back, row=3,
                             disabled=self.page == 0)  # fmt: skip
                self._button("▶", "pagina:despues", self._page_next, row=3,
                             disabled=self.page >= pages - 1)  # fmt: skip
            self._portfolio_buttons()
        elif self.screen == VPO:
            self._vpo_buttons()
        elif self.screen == GESTION:
            self._gestion_buttons()
        elif self.screen == CANDIDATOS:
            self._candidate_buttons()
        elif self.screen == SUBASTAS:
            self._auction_buttons()
        elif self.screen == REFORMAS:
            self._reform_buttons()
        elif self.screen == PLANTAS:
            self._units_buttons()
        elif self.screen == DIVIDIR:
            self._split_buttons()
        elif self.screen == IMPREVISTOS:
            self._decision_buttons()
        elif self.screen == JUNTA:
            self._junta_buttons()
        elif self.screen == RESENAS:
            self._button("◀ La ficha", "ficha", self._back_to_listing, row=1)
            self._button("🧰 Gestionar", "gestion", self._go_gestion, row=1)
            self._button("🗺️ Mapa", "mapa", self._go_map, row=1)
        else:
            self._button("🔑 Mis pisos", "cartera", self._go_portfolio, row=1)
            self._button("🏛️ VPO", "vpo", self._go_vpo, row=1)
            self._auctions_button(row=1)
            self._waiting_button(row=1)

    def _confirmable(
        self,
        label: str,
        action: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        row: int,
    ) -> None:
        """Un botón que cuesta dinero: el primer clic pide confirmar y el segundo lo hace."""
        if self.confirm == action:
            self._button(
                f"⚠️ ¿Seguro? {label}", f"{action}:si", callback, row=row,
                style=discord.ButtonStyle.danger,
            )  # fmt: skip
        else:
            self._button(label, action, self._arm, row=row)

    def _portfolio_buttons(self) -> None:
        me, market = self.owner.id, self.market
        if me not in market.socimi and market.whole_homes(me) >= rules.SOCIMI_HOMES:
            fee = format_amount(rules.SOCIMI_FEE)
            self._confirmable(f"📈 Montar SOCIMI ({fee})", "socimi", self._found_socimi, row=3)
        home = market.homes.get(me)
        if home is not None and home[0] in (HOME_OWN, HOME_RENTED):
            fine = self._early_fine()
            label = f"🚪 Dejar mi casa (indemnización {format_amount(fine)})" if fine else (
                "🚪 Dejar mi casa"
            )  # fmt: skip
            self._confirmable(label, "dejar", self._leave_home, row=3)

    def _vpo_buttons(self) -> None:
        me, market = self.owner.id, self.market
        unit = market.vpo_of(me)
        if unit is not None:
            if market.mortgages_of(me, vpo_ref(unit)):
                self._confirmable("🏦 Amortizar", "amortizar", self._amortize, row=1)
            self._confirmable("↩️ Devolverla al Estado", "devolver", self._vpo_return, row=1)
        elif market.reservation_of(me) is not None:
            self._confirmable(
                "🔑 Comprar mi VPO", "comprar_vpo", self._vpo_buy, row=1
            )  # fmt: skip
        elif me in market.signups:
            self._button("✖️ Borrarme del sorteo", "sorteo", self._vpo_signup, row=1)
        else:
            self._button(
                "✍️ Apuntarme al sorteo", "sorteo", self._vpo_signup, row=1,
                style=discord.ButtonStyle.success,
            )  # fmt: skip
        self._button("🗺️ Mapa", "mapa", self._go_map, row=2)
        self._button("🔑 Mis pisos", "cartera", self._go_portfolio, row=2)

    def _owner_buttons(self) -> None:
        """Lo que puede hacer el dueño con su inmueble (fila de abajo de la ficha)."""
        key, me, market = self.listing, self.owner.id, self.market
        listing = self._at(key)
        holders = market.shares_of(key)
        lease = market.leases.get(key)
        if not holders.get(me) or lease is None or lease.status == rules.BY_ROOMS:
            return
        if self.confirm == "miembro":
            pick: ui.UserSelect = ui.UserSelect(
                placeholder="¿A qué miembro se lo alquilas?", custom_id=f"{GAME}:inquilino", row=4
            )
            pick.callback = self._pick_tenant  # type: ignore[method-assign]
            self.add_item(pick)
            return
        is_home = listing.kind in rules.HOME_KINDS
        boss = market.manager(key) == me
        if market.mortgages_of(me, key):
            self._confirmable("🏦 Amortizar", "amortizar", self._amortize, row=4)
        if market.homes.get(me) == (HOME_OWN, key):
            if fine := self._early_fine():
                self._confirmable(
                    f"🚪 Mudarme (indemnización {format_amount(fine)})", "mudarme",
                    self._move_out, row=4,
                )  # fmt: skip
            else:
                self._button("🚪 Mudarme", "mudarme", self._move_out, row=4)
        elif (
            is_home and holders[me] == rules.SHARES and lease.status == rules.VACANT
            and me not in market.homes
        ):  # fmt: skip
            self._button("🏠 Vivir aquí", "vivir", self._set_home, row=4)
        if boss and is_home and lease.status == rules.VACANT:
            self._button("👤 Alquilar a un miembro", "miembro", self._arm, row=4)
        if (
            boss and is_home and not lease.license and lease.status != rules.HABITUAL
            and lease.member is None
        ):  # fmt: skip
            fee = format_amount(rules.LICENSE_FEE)
            self._confirmable(f"🏖️ Licencia ({fee})", "licencia", self._license, row=4)
        if (key, me) in market.offers:
            self._confirmable("🦅 Vender al fondo", "fondo", self._vulture, row=4)

    def _ficha_buttons(self) -> None:
        key = self.listing
        holders = self.market.shares_of(key)
        mine = holders.get(self.owner.id, 0)
        free = self.market.free_shares(key)
        lease = self.market.leases.get(key)
        room = rules.is_room(key)
        if free and not room:
            label = "🛒 Comprar entero" if free == rules.SHARES else f"🛒 Comprar (quedan {free})"
            self._button(label, "comprar", self._buy, row=1, style=discord.ButtonStyle.success)
        if mine and not room:
            self._button("💰 Vender", "vender", self._sell, row=1, style=discord.ButtonStyle.danger)
        if self._rooms():
            count = len(self.market.units_of(key))
            self._button(f"🛏️ Habitaciones ({count})", "plantas", self._go_units, row=1,
                         style=discord.ButtonStyle.primary)  # fmt: skip
        boss = self.market.manager(key)
        can_decide = mine and (boss is None or boss == self.owner.id)
        if can_decide and lease is not None and lease.status in (rules.DEFAULTING, rules.SQUATTED):
            self._button("⚖️ Desahuciar", "desahuciar", self._evict, row=1)
        if boss == self.owner.id and lease is not None and rules.recoverable(lease):
            self._button("🔑 Recuperar el piso", "llaves", self._go_keys, row=1)
        if not room:
            self._reforms_button(mine, lease or rules.Lease())
        if boss == self.owner.id and lease is not None and lease.status != rules.BY_ROOMS:
            listing = self._at(key)
            index = self.market.index(listing.barrio)
            levels: ui.Select = ui.Select(
                placeholder="Renta que pides",
                custom_id=f"{GAME}:renta",
                row=2,
                options=[
                    discord.SelectOption(
                        label=(
                            f"{level.name} · "
                            f"{format_amount(rules.asking_rent(listing, level, index))}"
                        ),
                        value=level.key,
                        emoji=level.emoji,
                        description="Vale para el próximo contrato (art. 18 LAU)",
                        default=level.key == lease.level,
                    )
                    for level in RENT_LEVELS.values()
                ],
            )
            levels.callback = self._pick_level  # type: ignore[method-assign]
            self.add_item(levels)
        if rules.parent_key(key) is not None:
            parent = self._at(rules.parent_key(key) or key)
            back = "◀ Plantas"
            if room or rules.halves(parent):
                back = "◀ Habitaciones" if room else "◀ Las dos mitades"
            self._button(back, "volver", self._go_parent, row=3)
        elif building_of(key) is not None:
            self._button("◀ Su edificio", "volver", self._go_own_building, row=3)
        else:
            barrio = BARRIO_BY_KEY[self._at(key).barrio].name
            self._button(f"◀ {barrio}"[:80], "volver", self._go_barrio, row=3)
        self._button("🗺️ Mapa", "mapa", self._go_map, row=3)
        self._button("🔑 Mis pisos", "cartera", self._go_portfolio, row=3)
        if mine:
            waiting = len(self.market.candidates_of(key, self.cog.service.today()))
            label = f"🧰 Gestionar ({waiting} 👥)" if waiting else "🧰 Gestionar"
            self._button(label, "gestion", self._go_gestion, row=3,
                         style=discord.ButtonStyle.primary)  # fmt: skip
            pending = len(self.market.decisions.get(key, ()))
            if pending:
                self._button(f"⚠️ Imprevistos ({pending})", "imprevistos", self._go_decisions,
                             row=3, style=discord.ButtonStyle.danger)  # fmt: skip
        self._owner_buttons()

    def _reforms_button(self, mine: int, lease: rules.Lease) -> None:
        """🏗️ Reformas en la ficha, también antes de comprar: la etiqueta dice si se puede."""
        key = self.listing
        listing = self._at(key)
        done = self.market.reforms.get(key, {})
        left = sum(1 for r in REFORMS if listing.kind in r.kinds and r.key not in done)
        if not left and not done:
            return
        style = discord.ButtonStyle.secondary
        if lease.status == rules.OBRAS:
            until = f" hasta el {lease.works_until:%d/%m}" if lease.works_until else ""
            label = f"🏗️ Reformas: en obras{until}"
        elif not left:
            label = "🏗️ Reformas: todas hechas"
        elif mine == rules.SHARES and lease.status == rules.VACANT:
            label, style = f"🏗️ Reformar: puedes ({left})", discord.ButtonStyle.success
        elif mine == rules.SHARES:
            label = f"🏗️ Reformas ({left}): tiene que estar vacío"
        elif lease.status == rules.VACANT:
            label = f"🏗️ Reformas ({left}): hay que tenerlo entero"
        else:
            label = f"🏗️ Reformas ({left}): hay que tenerlo entero y vacío"
        self._button(label, "reformas", self._go_reforms, row=1, style=style)

    def _gestion_buttons(self) -> None:
        """🧰 Gestionar: quién elige al inquilino, el seguro y los candidatos."""
        key, me, market = self.listing, self.owner.id, self.market
        listing = self._at(key)
        lease = market.leases.get(key)
        boss = market.manager(key) == me
        by_rooms = lease is not None and lease.status == rules.BY_ROOMS
        if boss and lease is not None and not by_rooms:
            if lease.pick == rules.PICK_SELF:
                self._button("🤖 Que elija la gestora", "elegir", self._toggle_pick, row=1)
            else:
                self._button(
                    "✋ Elegir yo al inquilino", "elegir", self._toggle_pick, row=1,
                    style=discord.ButtonStyle.success,
                )  # fmt: skip
            if listing.kind in rules.HOME_KINDS:
                if lease.insured:
                    self._confirmable("🛡️ Quitar el seguro", "seguro", self._toggle_insurance, row=1)
                else:
                    self._confirmable(
                        "🛡️ Contratar seguro de impago", "seguro", self._toggle_insurance, row=1
                    )
        if me in market.shares_of(key) and not rules.is_room(key) and (
            by_rooms or any(rules.split_problem(market.base(key) or listing, mode) is None
                            for mode in rules.SPLIT_MODES) and key not in market.splits
        ):  # fmt: skip
            self._button("🏢 Dividir", "dividir", self._go_split, row=1)
        if lease is not None and lease.pick == rules.PICK_SELF and not by_rooms:
            waiting = len(market.candidates_of(key, self.cog.service.today()))
            self._button(
                f"👥 Candidatos ({waiting})", "candidatos", self._go_candidates, row=1,
                style=discord.ButtonStyle.primary, disabled=not waiting,
            )  # fmt: skip
        self._button("◀ La ficha", "ficha", self._back_to_listing, row=2)
        self._button("🗺️ Mapa", "mapa", self._go_map, row=2)
        self._button("🔑 Mis pisos", "cartera", self._go_portfolio, row=2)
        if lease is not None and (lease.license or market.reviews.get(key)):
            self._button("⭐ Reseñas", "resenas", self._go_reviews, row=2)
        if len(market.shares_of(key)) > 1 and not rules.is_room(key):
            count = len(market.votes_of(key))
            self._button(
                f"🗳️ Junta ({count})" if count else "🗳️ Junta", "junta", self._go_junta, row=2,
                style=discord.ButtonStyle.primary if count else discord.ButtonStyle.secondary,
            )  # fmt: skip
        held = market.holdings.get(key, {}).get(me)
        if held is not None and market.auction_of(key, me) is None:
            if self.confirm == "comprador":
                pick: ui.UserSelect = ui.UserSelect(
                    placeholder="¿A qué miembro se lo vendes?", custom_id=f"{GAME}:comprador",
                    row=3,
                )  # fmt: skip
                pick.callback = self._pick_buyer  # type: ignore[method-assign]
                self.add_item(pick)
            else:
                self._button("🤝 Vender a un miembro", "vender_miembro", self._sell_member, row=3)
                self._button("🔨 Subastar", "subastar", self._auction_form, row=3)
                self._button("🏦 Rehipotecar", "rehipotecar", self._remortgage, row=3)
                self._button("💸 Adelantar rentas", "anticipo", self._advance, row=3)
        self._term_select(row=4)

    def _term_select(self, *, row: int) -> None:
        """Cuánto dura el próximo contrato (9.23.0): de 1 a 5 años, si con su renta se elige."""
        key, market = self.listing, self.market
        lease = market.leases.get(key)
        if market.manager(key) != self.owner.id or lease is None or lease.status == rules.BY_ROOMS:
            return
        listing = self._at(key)
        level = RENT_LEVELS.get(lease.level, RENT_LEVELS["referencia"])
        choices = rules.term_choices(listing, level)
        if not choices:
            return
        current = rules.contract_months(listing, level, lease.term)
        pick: ui.Select = ui.Select(
            placeholder="Cuánto dura el próximo contrato",
            custom_id=f"{GAME}:duracion",
            row=row,
            options=[
                discord.SelectOption(
                    label=f"{years_text(term)} ({term} días del bot)",
                    value=str(term),
                    emoji="🗓️",
                    description=term_note(listing, level, term)[:100],
                    default=term == current,
                )
                for term in choices
            ],
        )
        pick.callback = self._pick_term  # type: ignore[method-assign]
        self.add_item(pick)

    def _keys_buttons(self) -> None:
        """🔑 Recuperar el piso: comprar las llaves, no renovar o pedirlo para vivir."""
        key, market = self.listing, self.market
        lease = market.leases.get(key)
        today = self.cog.service.today()
        if lease is not None and rules.recoverable(lease) and market.manager(key) == self.owner.id:
            if lease.member is not None:
                self._button(
                    "🤝 Ofrecerle las llaves", "llaves_miembro", self._member_keys_form, row=1,
                    style=discord.ButtonStyle.success,
                )  # fmt: skip
            elif lease.keys_wait is not None and today < lease.keys_wait:
                if lease.keys_ask:
                    self._confirmable(
                        f"✅ Pagarle lo que pide ({format_amount(lease.keys_ask)})",
                        "llaves_precio", self._keys_accept_ask, row=1,
                    )  # fmt: skip
                    self._button("✖️ Dejarlo", "llaves_dejar", self._keys_drop_ask, row=1)
                else:
                    self._button(
                        f"⏳ No quiere hablar hasta el {lease.keys_wait:%d/%m}", "llaves_espera",
                        self._back_to_listing, row=1, disabled=True,
                    )  # fmt: skip
            elif (quote := market.keys_quote(key, today)) is not None:
                free = quote.odds(rules.KEYS_FREE)
                self._button(
                    f"🙏 Por las buenas · {odds_text(free)}", "llaves_buenas", self._keys_free,
                    row=1, disabled=free <= 0,
                )  # fmt: skip
                for offer, emoji, name, callback in (
                    (rules.KEYS_FAIR, "🤝", "Justa", self._keys_fair),
                    (rules.KEYS_RICH, "💰", "Generosa", self._keys_rich),
                ):
                    amount = rules.keys_offer_amount(offer, quote.fair)
                    self._confirmable(
                        f"{emoji} {name}: {format_amount(amount)} · {odds_text(quote.odds(offer))}",
                        f"llaves_{offer}", callback, row=1,
                    )  # fmt: skip
            if not lease.leaving:
                self._button("🚪 No renovarle", "no_renovar", self._toggle_no_renew, row=2)
            elif lease.leaving == rules.LEAVE_NO_RENEW:
                self._button("↩️ Que renueve", "no_renovar", self._toggle_no_renew, row=2)
            if self._need_problem() is None:
                self._confirmable("🏠 Lo necesito para vivir", "necesidad", self._claim_need, row=2)
        self._button("◀ La ficha", "ficha", self._back_to_listing, row=3)
        self._button("🗺️ Mapa", "mapa", self._go_map, row=3)
        self._button("🔑 Mis pisos", "cartera", self._go_portfolio, row=3)

    def _tenant_name(self, lease: rules.Lease) -> str:
        """Quién vive de alquiler: el inquilino del catálogo o el miembro."""
        if lease.member is not None:
            return _name(self.guild, lease.member)
        tenant = TENANT_BY_KEY.get(lease.tenant or "")
        return tenant.name if tenant is not None else "El inquilino"

    def _need_problem(self) -> str | None:
        """Por qué no se puede pedir este piso para vivir (art. 9.3 LAU), o `None`."""
        key, me, market = self.listing, self.owner.id, self.market
        lease = market.leases.get(key)
        if lease is None:
            return "No hay contrato."
        problem = rules.need_problem(self._at(key), lease, self.cog.service.today())
        if problem is not None:
            return problem
        if market.shares_of(key).get(me, 0) != rules.SHARES:
            return "Solo si es entero tuyo."
        if me in market.homes:
            return "Ya vives en otro sitio: la necesidad tiene que ser de verdad."
        if market.reclaim_of(me) is not None:
            return "Ya has pedido otro piso para vivir."
        return None

    def _early_fine(self) -> int:
        """La indemnización si se muda ya del piso que pidió para vivir (0 si no debe nada)."""
        me, market = self.owner.id, self.market
        found = market.reclaim_of(me)
        if found is None or found[1].until is None:
            return 0
        if market.homes.get(me) != (HOME_OWN, found[0]):
            return 0
        return found[1].fine if self.cog.service.today() < found[1].until else 0

    def _keys_text(self) -> str:
        """Lo que se ve encima de la ficha en 🔑 Recuperar el piso: las reglas y las cifras."""
        key, market = self.listing, self.market
        lease = market.leases.get(key)
        if lease is None or not rules.recoverable(lease) or lease.until is None:
            return "🔑 Ya no hay a quién pedirle que se vaya."
        today = self.cog.service.today()
        listing = self._at(key)
        tense = rules.tense(market.index(listing.barrio))
        left = rules.days_left(lease, today)
        lines = [
            f"🔑 **Recuperar el piso** · {self._tenant_name(lease)} · le quedan {left} días de "
            f"contrato (hasta el {lease.until:%d/%m})."
        ]
        boss = market.manager(key)
        fame = market.fame(boss, today) if boss is not None else 0
        quote = market.keys_quote(key, today)
        if quote is not None:
            lines.append(
                f"🤝 **Comprar las llaves.** Lo justo hoy: **{format_amount(quote.fair)}** "
                "(medio mes de renta por cada 12 días que le quedan, más si está a gusto o si "
                "los alquileres han subido desde que firmó). Si "
                f"acepta, se va en {rules.KEYS_MOVE_DAYS} días y paga hasta entonces. Si no, se "
                f"enfada y te dice su precio, que vale {rules.KEYS_WAIT_DAYS} días."
            )
            if quote.risk == "alto":
                lines.append(
                    "⚠️ Es de riesgo alto: si le ofreces poco, puede atrincherarse y dejar de pagar."
                )
        elif lease.member is not None:
            fair = rules.keys_fair(lease.rent, left, 1.0, lease.mood, fame)
            lines.append(
                "🤝 **Comprarle las llaves.** Es un miembro: le ofreces lo que quieras y decide "
                f"él, que puede contraofertar. Como referencia, lo justo serían "
                f"{format_amount(fair)}. Hacienda se le queda el 19 % (ganancia patrimonial)."
            )
        if fame:
            lines.append(
                f"📣 Tu fama de casero: {fame} (pisos recuperados así en {rules.FAME_DAYS} días). "
                f"Cada uno encarece el siguiente un {round(rules.FAME_STEP * 100)} %, nadie se va "
                "por las buenas y los candidatos tardan más en llegar."
            )
        if lease.leaving == rules.LEAVE_NO_RENEW:
            extra = ", salvo que pida la prórroga de un año (zona tensionada)" if tense else ""
            lines.append(
                f"🚪 Ya le has dicho que no renuevas: se va el {lease.until:%d/%m}{extra}."
            )
        elif lease.leaving == rules.LEAVE_EXTENDED:
            lines.append(
                f"🚪 Está en la prórroga de la Ley de Vivienda: se va el {lease.until:%d/%m}."
            )
        else:
            extra = (
                ". En zona tensionada puede pedir una prórroga de un año (Ley 12/2023)"
                if tense
                else ""
            )
            lines.append(f"🚪 **No renovar** es gratis: se va el {lease.until:%d/%m}{extra}.")
        problem = self._need_problem()
        if problem is None:
            fine = rules.need_fine(lease.rent, left)
            lines.append(
                f"🏠 **Lo necesito para vivir** (art. 9.3 LAU): se va en "
                f"{rules.NEED_NOTICE_DAYS} días y esa noche te mudas tú. Tienes que vivir ahí "
                f"{rules.NEED_STAY_DAYS} días; si te vas antes, le pagas {format_amount(fine)}."
            )
        else:
            lines.append(f"🏠 Lo necesito para vivir (art. 9.3 LAU): {problem}")
        return "\n".join(lines)

    def _finance_hint(self) -> str | None:
        """La línea del 💳 de obras y divisiones, con la más barata que se puede financiar."""
        key, me, market = self.listing, self.owner.id, self.market
        listing = self._at(key)
        index = market.index(listing.barrio)
        if self.screen == REFORMAS:
            lease = market.leases.get(key, rules.Lease())
            if market.shares_of(key).get(me, 0) != rules.SHARES or lease.status != rules.VACANT:
                return None
            done = market.reforms.get(key, {})
            totals = [
                rules.reform_quote(listing, r, index, market.ipc).total
                for r in REFORMS
                if listing.kind in r.kinds and r.key not in done
            ]
        else:
            base = market.base(key)
            if base is None or len(market.shares_of(key)) > 1:
                return None
            totals = [
                rules.split_quote(listing, mode, index, market.ipc).total
                for mode in rules.SPLIT_MODES
                if rules.split_problem(base, mode, promotion=key in market.promotions) is None
            ]
        hint = financiar.teaser(min(totals), "obras", works=True) if totals else None
        return f"-# {hint}." if hint else None

    def _reform_buttons(self) -> None:
        """🏗️ Reformas: una por botón, con dos clics porque cuesta dinero."""
        key, me, market = self.listing, self.owner.id, self.market
        listing = self._at(key)
        lease = market.leases.get(key, rules.Lease())
        can = market.shares_of(key).get(me, 0) == rules.SHARES and lease.status == rules.VACANT
        done = market.reforms.get(key, {})
        slot = 0
        financed: list[discord.SelectOption] = []
        for reform in REFORMS:
            if listing.kind not in reform.kinds or reform.key in done or not can:
                continue
            total = rules.reform_quote(
                listing, reform, market.index(listing.barrio), market.ipc
            ).total
            self._confirmable(
                f"{reform.emoji} {reform.name} ({format_amount(total)})",
                f"reforma:{reform.key}", self._reform, row=1 + slot // 3,
            )  # fmt: skip
            if credit.refusal(total) is None:
                financed.append(
                    discord.SelectOption(
                        label=f"{reform.name} ({format_amount(total)})"[:100],
                        value=reform.key,
                        emoji=reform.emoji,
                    )
                )
            slot += 1
        self._button("◀ La ficha", "ficha", self._back_to_listing, row=3)
        if me in market.shares_of(key):
            self._button("🧰 Gestionar", "gestion", self._go_gestion, row=3)
        self._button("🗺️ Mapa", "mapa", self._go_map, row=3)
        if financed:
            pick: ui.Select = ui.Select(
                placeholder=f"{financiar.LABEL} una obra a plazos…",
                options=financed[:25],
                custom_id=f"{GAME}:financiar_obra",
                row=4,
            )
            pick.callback = self._finance_reform  # type: ignore[method-assign]
            self.add_item(pick)

    def _decision_buttons(self) -> None:
        """⚠️ Imprevistos: una fila por imprevisto con sus formas de arreglarlo."""
        key, me, market = self.listing, self.owner.id, self.market
        holders = market.shares_of(key)
        boss = self.market.manager(key)
        decides = me in holders and (boss is None or boss == me)
        for row, decision in enumerate(market.decisions.get(key, ())[:3], start=1):
            for choice in rules.DECISIONS[decision.event]:
                chosen = decision.choice == choice.key
                self._button(
                    f"{row} · {choice.label}"[:80], f"decidir:{decision.id}:{choice.key}",
                    self._decide, row=row, disabled=not decides,
                    style=discord.ButtonStyle.success if chosen else discord.ButtonStyle.secondary,
                )  # fmt: skip
        self._button("◀ La ficha", "ficha", self._back_to_listing, row=4)
        self._button("🗺️ Mapa", "mapa", self._go_map, row=4)

    def _auctions_button(self, *, row: int) -> None:
        """El botón que abre 🔨 Subastas, con cuántas hay abiertas."""
        count = len(self.market.auctions)
        label = f"🔨 Subastas ({count})" if count else "🔨 Subastas"
        self._button(label, "subastas", self._go_auctions, row=row)

    def _auction_buttons(self) -> None:
        """🔨 Subastas: elegir una en el desplegable y pujar."""
        auctions = list(self.market.auctions)[:MAX_OPTIONS]
        if auctions:
            if self.auction_id not in {a.id for a in auctions}:
                self.auction_id = auctions[0].id
            pick: ui.Select = ui.Select(
                placeholder="Elige una subasta",
                custom_id=f"{GAME}:subasta",
                row=1,
                options=[
                    discord.SelectOption(
                        label=f"{'⚖️ ' if a.judicial else ''}{self._title(a.listing)}"[:100],
                        value=str(a.id),
                        description=f"{a.shares} % · mejor puja "
                        f"{format_amount(a.best_bid) if a.best_bid else 'ninguna'}"[:100],
                        default=a.id == self.auction_id,
                    )
                    for a in auctions
                ],
            )
            pick.callback = self._pick_auction  # type: ignore[method-assign]
            self.add_item(pick)
            chosen = next(a for a in auctions if a.id == self.auction_id)
            least = rules.next_bid(chosen.best_bid, chosen.min_bid)
            self._button(
                f"🔨 Pujar (desde {format_amount(least)})",
                "pujar", self._bid_form, row=2, style=discord.ButtonStyle.success,
                disabled=chosen.seller == self.owner.id,
            )  # fmt: skip
        self._button("🗺️ Mapa", "mapa", self._go_map, row=3)
        self._button("🔑 Mis pisos", "cartera", self._go_portfolio, row=3)

    def _title(self, key: str) -> str:
        listing = self.market.listing(key)
        return listing.title if listing is not None else key

    def _candidate_buttons(self) -> None:
        """👥 Candidatos: firmar con uno o descartarlo (lo decide quien tiene la mayoría)."""
        key, me, market = self.listing, self.owner.id, self.market
        boss = market.manager(key) == me
        waiting = market.candidates_of(key, self.cog.service.today())
        for slot, _tenant in enumerate(waiting, start=1):
            self._button(
                f"✍️ Firmar con el {slot}", f"firmar:{slot}", self._sign, row=1,
                style=discord.ButtonStyle.success, disabled=not boss,
            )  # fmt: skip
            self._button(
                f"✖️ Descartar al {slot}", f"descartar:{slot}", self._discard, row=2,
                disabled=not boss,
            )  # fmt: skip
        self._button("◀ Gestionar", "gestion", self._go_gestion, row=3)
        self._button("🏠 La ficha", "ficha", self._back_to_listing, row=3)
        self._button("🗺️ Mapa", "mapa", self._go_map, row=3)
        others = [k for k in self._waiting_keys() if k != key]
        if others:
            self._button(
                f"⏭️ Siguiente ({len(others)})", "candidatos_siguiente", self._go_waiting, row=3,
                style=discord.ButtonStyle.success,
            )  # fmt: skip

    def _streets_buttons(self) -> None:
        """🗺️ Calles: un botón por edificio, una fila por calle (de 3 a 5), numerados en el orden
        en que se leen en el plano (`map_order`), igual que en la imagen."""
        place = {b.key: n for n, b in enumerate(map_order(self.barrio), start=1)}
        for row, street in enumerate(streets_of(self.barrio), start=1):
            for building in buildings_of(street.key):
                # El número, el de su sitio en el plano; el custom_id, el del edificio (fijo).
                slot = building.key.rsplit("_e", 1)[1]
                mine = any(
                    self.owner.id in self.market.shares_of(u.key)
                    for u in view.building_units(self.market, building)
                )
                self._button(
                    f"{place[building.key]} · {SHORT_BUILDING[building.kind]}", f"edificio:{slot}",
                    self._open_building, row=row,
                    style=discord.ButtonStyle.success if mine else discord.ButtonStyle.primary,
                )  # fmt: skip
        self._button("⭐ Destacados", "destacados", self._back_to_barrio, row=4)
        self._button("🗺️ Mapa", "mapa", self._go_map, row=4)
        self._button("🔑 Mis pisos", "cartera", self._go_portfolio, row=4)
        self._waiting_button(row=4)

    def _building_buttons(self) -> None:
        """🏢 Edificio: el desplegable de sus unidades, en el orden de los números del corte."""
        building = BUILDING_BY_KEY[self.building]
        state = view.building_state(self.market, building.key, self.owner.id, {})
        units = sorted((u for f in state["floors"] for u in f["units"]), key=lambda u: u["slot"])
        pick: ui.Select = ui.Select(
            placeholder="Elige una unidad para ver su ficha",
            custom_id=f"{GAME}:unidad",
            row=1,
            options=[
                discord.SelectOption(
                    label=f"{u['slot']} · {u['label']} · {u['kind_name']}"[:100],
                    value=u["key"],
                    description=f"{u['use']} · {u['owner']} · {u['price']}"[:100],
                )
                for u in units[:MAX_OPTIONS]
            ],
        )
        pick.callback = self._pick_unit  # type: ignore[method-assign]
        self.add_item(pick)
        self._button("◀ Calles", "calles", self._go_streets, row=2)
        self._button("⭐ Destacados", "destacados", self._back_to_barrio, row=2)
        self._button("🗺️ Mapa", "mapa", self._go_map, row=2)
        self._button("🔑 Mis pisos", "cartera", self._go_portfolio, row=2)
        self._waiting_button(row=2)

    def _waiting_keys(self) -> list[str]:
        """Dónde decides tú y esperan candidatos (pisos, plantas y habitaciones). Solo memoria."""
        today, me, market = self.cog.service.today(), self.owner.id, self.market
        return [
            key
            for key in market.candidates
            if market.candidates_of(key, today) and market.manager(key) == me
        ]

    def _waiting_button(self, *, row: int) -> None:
        """👥 Candidatos (N), en verde: solo sale si alguien espera a que elijas."""
        today = self.cog.service.today()
        count = sum(len(self.market.candidates_of(k, today)) for k in self._waiting_keys())
        if count:
            self._button(
                f"👥 Candidatos ({count})", "candidatos_todos", self._go_waiting, row=row,
                style=discord.ButtonStyle.success,
            )  # fmt: skip

    def _waiting_note(self, key: str) -> str:
        """« · 👥 2» si esperan candidatos en un inmueble (o en sus habitaciones)."""
        today = self.cog.service.today()
        keys = [key, *(u.key for u in self.market.units_of(key))]
        count = sum(len(self.market.candidates_of(k, today)) for k in keys)
        return f" · 👥 {count}" if count else ""

    def _rooms(self) -> bool:
        """Si el inmueble de la pantalla se alquila por habitaciones."""
        return self.market.split_mode(self.listing) == rules.ROOMS

    def _units_buttons(self) -> None:
        """🏢 Plantas o 🛏️ habitaciones: un botón por cada una, en el orden de la imagen."""
        key, me, market = self.listing, self.owner.id, self.market
        for slot, unit in enumerate(market.units_of(key)):
            self._button(
                f"{slot + 1} · {unit.floor}", f"unidad:{slot}", self._open_unit,
                row=1 + slot // BARRIO_COLS, style=discord.ButtonStyle.primary,
            )  # fmt: skip
        if self._rooms():
            self._button("◀ La ficha", "ficha", self._back_to_listing, row=3)
            if market.manager(key) == me:
                self._confirmable("🔀 Juntar las habitaciones", "juntar", self._join_rooms, row=3)
        else:
            barrio = BARRIO_BY_KEY[self._at(key).barrio].name
            self._button(f"◀ {barrio}"[:80], "volver", self._go_barrio, row=3)
        self._button("🗺️ Mapa", "mapa", self._go_map, row=3)
        self._button("🔑 Mis pisos", "cartera", self._go_portfolio, row=3)
        self._waiting_button(row=3)

    def _split_buttons(self) -> None:
        """🏢 Dividir: por plantas o por habitaciones (dos clics, porque cuesta dinero).

        Con varios dueños se propone en la junta; si ya se alquila por habitaciones, aquí se
        juntan.
        """
        key, me, market = self.listing, self.owner.id, self.market
        holders = market.shares_of(key)
        state = view.split_state(market, key, me)
        for option in state["options"]:
            if option["problem"]:
                continue
            if len(holders) > 1:
                self._button("🗳️ Proponerlo a la junta", "junta", self._go_junta, row=1)
                break
            self._confirmable(
                f"{option['emoji']} {option['name']} ({option['total']})",
                f"dividir:{option['mode']}", self._split, row=1,
            )  # fmt: skip
            self._button(
                f"{financiar.LABEL}: {option['name']}", f"financiar_dividir:{option['mode']}",
                self._finance_split, row=3,
            )  # fmt: skip
        if state["rooms"]:
            self._button("🛏️ Habitaciones", "plantas", self._go_units, row=1)
            if market.manager(key) == me:
                self._confirmable("🔀 Juntar las habitaciones", "juntar", self._join_rooms, row=1)
        self._button("◀ Gestionar", "gestion", self._go_gestion, row=2)
        self._button("◀ La ficha", "ficha", self._back_to_listing, row=2)
        self._button("🗺️ Mapa", "mapa", self._go_map, row=2)

    def _caja_buttons(self) -> None:
        if self.done is not None:
            self._button(
                "🏠 Ver la ficha",
                "ficha",
                self._back_to_listing,
                row=0,
                style=discord.ButtonStyle.primary,
            )
            self._button("🔑 Mis pisos", "cartera", self._go_portfolio, row=0)
            self._button("🗺️ Mapa", "mapa", self._go_map, row=0)
            return
        selling = self.pending[0] if self.pending else False
        self._button(
            "🤝 Vender" if selling else "💳 Pagar",
            "pagar",
            self._pay,
            row=0,
            style=discord.ButtonStyle.success,
        )
        if not selling:
            self._button(
                "💵 Al contado" if self.financed else "🏦 Con hipoteca",
                "financiar",
                self._toggle_mortgage,
                row=0,
            )
        self._button("✖️ Cancelar", "cancelar", self._back_to_listing, row=0)

    def _button(
        self,
        label: str,
        custom_id: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        row: int,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        disabled: bool = False,
    ) -> None:
        button: ui.Button = ui.Button(
            label=label[:80],
            style=style,
            custom_id=f"{GAME}:{custom_id}",
            row=row,
            disabled=disabled,
        )
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)

    # -- Ciclo de vida --------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño mueve su panel."""
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message(
            f"Este panel es de {self.owner.display_name}. Abre el tuyo con `/pisos`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Caduca: quita los botones (la imagen se queda)."""
        self.cog.panels.discard(self)
        try:
            if self._last is not None:
                await self._last.edit_original_response(view=None)
            elif self.message is not None:
                await self.message.edit(view=None)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar el panel de pisos", exc_info=True)

    # -- Navegación -----------------------------------------------------------------

    async def _go(self, interaction: discord.Interaction, screen: str) -> None:
        await ack(interaction)
        self.screen, self.pending, self.done = screen, None, None
        self.financed, self.confirm = False, None
        await self.show(interaction)
        logros.note(
            self.cog.bot,
            self.guild.id,
            self.owner.id,
            housing_panel_stats(
                screen=screen,
                building=self.building,
                pages=self._pages() if screen == CARTERA else 0,
            ),
            getattr(interaction.channel, "id", None),
        )

    async def _go_map(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, MAPA)

    async def _go_portfolio(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, CARTERA)

    async def _page_back(self, interaction: discord.Interaction) -> None:
        await self._turn_page(interaction, -1)

    async def _page_next(self, interaction: discord.Interaction) -> None:
        await self._turn_page(interaction, 1)

    async def _turn_page(self, interaction: discord.Interaction, step: int) -> None:
        """◀ ▶ de 🔑 Mis pisos: la página de al lado (`show` la mete en las que haya)."""
        await ack(interaction)
        self.page += step
        self.confirm = None
        await self.show(interaction)
        logros.note(
            self.cog.bot,
            self.guild.id,
            self.owner.id,
            housing_panel_stats(screen="pagina", pages=self._pages()),
            getattr(interaction.channel, "id", None),
        )

    def _pages(self) -> int:
        """Cuántas páginas ocupa ahora 🔑 Mis pisos."""
        return view.portfolio_pages(len(view.portfolio_keys(self.market, self.owner.id)))

    async def _go_vpo(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, VPO)

    async def _go_gestion(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, GESTION)

    async def _go_reforms(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, REFORMAS)

    async def _go_streets(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, CALLES)

    async def _back_to_barrio(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, BARRIO)

    async def _open_building(self, interaction: discord.Interaction) -> None:
        custom_id = interaction.data.get("custom_id", "") if interaction.data else ""
        key = f"{self.barrio}_e{custom_id.rsplit(':', 1)[-1]}"
        if key not in BUILDING_BY_KEY:
            await ack(interaction)
            return
        self.building = key
        await self._go(interaction, EDIFICIO)

    async def _go_own_building(self, interaction: discord.Interaction) -> None:
        """De la ficha de una unidad de una calle a su edificio en corte."""
        building = building_of(self.listing)
        if building is None:
            await ack(interaction)
            return
        self.building, self.barrio = building.key, building.barrio
        await self._go(interaction, EDIFICIO)

    async def _pick_unit(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values", []) if interaction.data else []
        if not values or self.market.listing(values[0]) is None:
            await ack(interaction)
            return
        self.listing = values[0]
        await self._go(interaction, FICHA)

    async def _go_split(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, DIVIDIR)

    async def _go_units(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, PLANTAS)

    async def _go_parent(self, interaction: discord.Interaction) -> None:
        """De la ficha de una planta o habitación a la pantalla de todas."""
        self.listing = rules.parent_key(self.listing) or self.listing
        await self._go(interaction, PLANTAS)

    async def _open_unit(self, interaction: discord.Interaction) -> None:
        custom_id = interaction.data.get("custom_id", "") if interaction.data else ""
        slot = custom_id.rsplit(":", 1)[-1]
        units = self.market.units_of(self.listing)
        if not slot.isdigit() or int(slot) >= len(units):
            await ack(interaction)
            return
        self.listing = units[int(slot)].key
        await self._go(interaction, FICHA)

    async def _go_waiting(self, interaction: discord.Interaction) -> None:
        """👥 Candidatos: el siguiente sitio donde esperan (o el primero). Solo memoria antes."""
        keys = self._waiting_keys()
        if not keys:
            await ack(interaction)
            await notify(interaction, "Ya no espera nadie: los candidatos se han ido.")
            await self.show(interaction)
            return
        here = keys.index(self.listing) if self.listing in keys else -1
        self.listing = keys[(here + 1) % len(keys)] if self.screen == CANDIDATOS else keys[0]
        self.barrio = self._at(self.listing).barrio
        await self._go(interaction, CANDIDATOS)
        logros.note(self.cog.bot, self.guild.id, self.owner.id,
                    housing_panel_stats(screen="atajo"))  # fmt: skip

    async def _remortgage(self, interaction: discord.Interaction) -> None:
        """🏦 Rehipotecar: abre `banco` en 🔐 Garantías con este inmueble, en privado."""
        if not await banco.open_guarantees(self.cog.bot, interaction, self.listing):
            await interaction.response.send_message(
                "El banco está cerrado ahora mismo.", ephemeral=True
            )

    async def _advance(self, interaction: discord.Interaction) -> None:
        """💸 Adelantar rentas: abre `banco` en 💸 Anticipos con este inmueble, en privado."""
        if not await banco.open_advances(self.cog.bot, interaction, self.listing):
            await interaction.response.send_message(
                "El banco está cerrado ahora mismo.", ephemeral=True
            )

    async def _go_decisions(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, IMPREVISTOS)

    async def _go_reviews(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, RESENAS)

    async def _go_junta(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, JUNTA)

    def _junta_buttons(self) -> None:
        """🗳️ Junta: sí o no a cada votación (dos por fila), proponer y la acción de división."""
        key, me, market = self.listing, self.owner.id, self.market
        holders = market.shares_of(key)
        owner = me in holders
        plain = discord.ButtonStyle.secondary
        for slot, vote in enumerate(market.votes_of(key)[:4], start=1):
            mine = vote.ballots.get(me)
            row = 1 + (slot - 1) // 2
            self._button(
                f"{slot} · ✅ Sí", f"junta:si:{vote.id}", self._vote, row=row, disabled=not owner,
                style=discord.ButtonStyle.success if mine is True else plain,
            )  # fmt: skip
            self._button(
                f"{slot} · ❌ No", f"junta:no:{vote.id}", self._vote, row=row, disabled=not owner,
                style=discord.ButtonStyle.danger if mine is False else plain,
            )  # fmt: skip
        free = owner and len(holders) > 1 and market.division_of(key) is None
        options = self._motions() if free else []
        if options:
            pick: ui.Select = ui.Select(
                placeholder="Proponer a la junta…", custom_id=f"{GAME}:proponer", row=3,
                options=options,
            )  # fmt: skip
            pick.callback = self._propose  # type: ignore[method-assign]
            self.add_item(pick)
        self._button("◀ Gestionar", "gestion", self._go_gestion, row=4)
        self._button("◀ La ficha", "ficha", self._back_to_listing, row=4)
        if free:
            self._confirmable("⚖️ Acción de división", "division", self._divide, row=4)
        self._button("🗺️ Mapa", "mapa", self._go_map, row=4)

    def _motions(self) -> list[discord.SelectOption]:
        """Lo que se puede proponer ahora (sin repetir lo que ya se vota)."""
        key, me, market = self.listing, self.owner.id, self.market
        listing = self._at(key)
        lease = market.leases.get(key, rules.Lease())
        busy = {v.kind for v in market.votes_of(key)}
        options: list[discord.SelectOption] = []

        def add(value: str, label: str, emoji: str, description: str) -> None:
            options.append(
                discord.SelectOption(
                    label=label[:100], value=value, emoji=emoji, description=description[:100]
                )
            )

        rooms = lease.status == rules.BY_ROOMS
        if "renta" not in busy and not rooms:
            for level in RENT_LEVELS.values():
                if level.key == lease.level or (
                    level.key == rules.VACATION_LEVEL and not lease.license
                ):
                    continue
                add(f"renta:{level.key}", f"Renta: {level.name}", level.emoji,
                    "Mayoría de participaciones (art. 398 CC)")  # fmt: skip
        if "seguro" not in busy and listing.kind in rules.HOME_KINDS and not rooms:
            on = not lease.insured
            add(f"seguro:{int(on)}",
                "Contratar el seguro de impago" if on else "Quitar el seguro de impago", "🛡️",
                "Mayoría de participaciones")  # fmt: skip
        if "eleccion" not in busy and not rooms:
            other = rules.PICK_AGENCY if lease.pick == rules.PICK_SELF else rules.PICK_SELF
            label = (
                "Elegir nosotros al inquilino"
                if other == rules.PICK_SELF
                else "Que elija la gestora al inquilino"
            )
            add(f"eleccion:{other}", label, "✋", "Mayoría de participaciones")
        if "administra" not in busy and market.manager(key) != me:
            add(f"administra:{me}", "Administrar yo el inmueble", "🧑‍💼",
                "Mayoría: la renta, el inquilino y los imprevistos")  # fmt: skip
        if "derrama" not in busy:
            add("derrama", "Derrama al fondo de reserva…", "🏦",
                "Mayoría: cada uno paga su parte")  # fmt: skip
        if "reforma" not in busy:
            for reform in REFORMS:
                if listing.kind in reform.kinds and reform.key not in market.reforms.get(key, {}):
                    label = view.motion_text("reforma", reform.key, 0, {})
                    add(f"reforma:{reform.key}", label, reform.emoji,
                        "3/5 de participaciones (art. 17.4 LPH)")  # fmt: skip
        if (
            "licencia" not in busy and listing.kind in rules.HOME_KINDS and not lease.license
            and not rooms
        ):  # fmt: skip
            add("licencia", f"Licencia vacacional ({format_amount(rules.LICENSE_FEE)})", "🏖️",
                "3/5 de participaciones (art. 17.12 LPH)")  # fmt: skip
        if "venta" not in busy:
            add("venta", "Vender el inmueble entero al mercado", "🤝",
                "Unanimidad (art. 397 CC)")  # fmt: skip
        base = market.base(key)
        for mode in rules.SPLIT_MODES:
            motion = rules.MOTION_BY_KEY[mode]
            if mode in busy or key in market.splits or base is None:
                continue
            if rules.split_problem(base, mode, promotion=key in market.promotions) is None:
                total = rules.split_quote(
                    listing, mode, market.index(listing.barrio), market.ipc
                ).total
                add(mode, f"{motion.name} ({format_amount(total)})", motion.emoji,
                    f"{view.THRESHOLD_TEXT[motion.threshold]} ({motion.law})")  # fmt: skip
        return options[:MAX_OPTIONS]

    async def _propose(self, interaction: discord.Interaction) -> None:
        """Elige qué proponer; la derrama abre antes un formulario con la cifra."""
        values = interaction.data.get("values", []) if interaction.data else []
        choice = str(values[0]) if values else ""
        if choice == "derrama":
            await interaction.response.send_modal(LevyModal(self))
            return
        kind, _sep, value = choice.partition(":")
        await self.propose(interaction, kind, value, 0)

    async def propose(
        self, interaction: discord.Interaction, kind: str, value: str, amount: int
    ) -> None:
        """Lleva una propuesta a la junta y avisa a los demás dueños en el canal."""
        await ack(interaction)
        try:
            vote = await self.cog.service.propose(
                self.guild.id, self.owner.id, self.listing, kind, value, amount
            )
        except HousingError as error:
            await notify(interaction, str(error))
            return
        except (OSError, sqlite3.Error):
            logger.exception("No se pudo proponer a la junta de pisos")
            await notify(interaction, "Se ha caído el administrador de fincas. Prueba otra vez.")
            return
        await self.show(interaction)
        others = [m for m in self.market.shares_of(self.listing) if m != self.owner.id]
        motion = rules.MOTION_BY_KEY[vote.kind]
        await notify(
            interaction,
            f"🗳️ **Junta de {self._at(self.listing).title}**: {self.owner.mention} propone "
            f"«{view.motion_text(vote.kind, vote.value, vote.amount, {})}» "
            f"({view.THRESHOLD_TEXT[motion.threshold].lower()}, {motion.law}). "
            + " ".join(f"<@{m}>" for m in others)
            + f": votad en `/pisos` → 🧰 Gestionar → 🗳️ Junta antes de <t:{int(vote.ends_at)}:t>. "
            "Quien no vote, cuenta a favor.",
            ephemeral=False,
        )
        logros.note(self.cog.bot, self.guild.id, self.owner.id, housing_action_stats("propone"),
                    getattr(interaction.channel, "id", None))  # fmt: skip

    async def _vote(self, interaction: discord.Interaction) -> None:
        """Vota sí o no; si ya han votado todos, se cierra y se hace lo aprobado."""
        custom_id = interaction.data.get("custom_id", "") if interaction.data else ""
        parts = custom_id.split(":")
        if len(parts) != 4 or not parts[3].isdigit():
            await ack(interaction)
            return
        yes, vote_id = parts[2] == "si", int(parts[3])

        async def run() -> Any:
            return await self.cog.service.vote(self.guild.id, self.owner.id, vote_id, yes)

        result = await self._money(interaction, run, broke="No llega el dinero para lo aprobado.")
        if result is None:
            return
        await self.show(interaction)
        logros.note(self.cog.bot, self.guild.id, self.owner.id, housing_action_stats("vota"),
                    getattr(interaction.channel, "id", None))  # fmt: skip
        if result.vote.status != "abierta":
            await notify(interaction, self.cog.vote_text(result, self.guild), ephemeral=False)
            await renta.remind(self.cog.bot, interaction)
            self.cog.track_vote(self.guild.id, result)

    async def _divide(self, interaction: discord.Interaction) -> None:
        """Acción de división (segundo clic): saca el inmueble entero a subasta."""
        await ack(interaction)
        self.confirm = None
        try:
            auction = await self.cog.service.divide(self.guild.id, self.owner.id, self.listing)
        except HousingError as error:
            await notify(interaction, str(error))
            await self.show(interaction)
            return
        except (OSError, sqlite3.Error):
            logger.exception("No se pudo pedir la división de un inmueble")
            await notify(interaction, "Se ha caído el juzgado. Prueba otra vez en un momento.")
            return
        self.market = await self.cog.service.market(self.guild.id)
        self.cog.market_cache[self.guild.id] = self.market
        await self.show(interaction)
        await self.cog.announce_auction(self.guild.id, auction)
        logros.note(self.cog.bot, self.guild.id, self.owner.id, housing_action_stats("division"),
                    getattr(interaction.channel, "id", None))  # fmt: skip

    async def _reform(self, interaction: discord.Interaction) -> None:
        """Empieza una reforma (segundo clic): paga la obra y el piso entra en obras."""
        custom_id = interaction.data.get("custom_id", "") if interaction.data else ""
        parts = custom_id.split(":")
        reform = parts[2] if len(parts) > 2 else ""

        async def run() -> Any:
            return await self.cog.service.start_reform(
                self.guild.id, self.owner.id, self.listing, reform
            )

        result = await self._money(interaction, run)
        if result is None:
            return
        done, _balance = result
        quote = done.quote
        await self._done(interaction, Event.BUY, housing_action_stats(
            "reforma", reform=reform, amount=quote.total, rehab=quote.rehab,
        ))  # fmt: skip
        await notify(
            interaction,
            f"🏗️ Obras hasta el {done.until:%d/%m}: {format_amount(quote.cost)} de obra, "
            f"{format_amount(quote.igic)} de IGIC, {format_amount(quote.icio)} de ICIO y "
            f"{format_amount(quote.license)} de licencia. Es una mejora: suma a lo que te "
            "costó el piso (art. 35.1 LIRPF), no se deduce como gasto."
            + (" Cuenta como rehabilitación: los contratos de los dos años siguientes tienen el "
               "60 % de reducción." if quote.rehab else ""),
        )  # fmt: skip

    async def _finance_reform(self, interaction: discord.Interaction) -> None:
        """💳 Financiar una obra: la oferta del banco, en privado; al firmar, empieza la obra."""
        values = interaction.data.get("values", []) if interaction.data else []
        reform = REFORM_BY_KEY.get(str(values[0])) if values else None
        listing = self.market.listing(self.listing)
        if reform is None or listing is None:
            await ack(interaction)
            return
        key, ipc = self.listing, self.market.ipc
        quote = rules.reform_quote(listing, reform, self.market.index(listing.barrio), ipc)

        async def buy(_interaction: discord.Interaction) -> str | None:
            try:
                await self.cog.service.start_reform(self.guild.id, self.owner.id, key, reform.key)
            except (HousingError, InsufficientFundsError, BalanceLimitError) as error:
                return str(error) or "No se ha podido empezar la obra."
            return None

        async def after(after_interaction: discord.Interaction) -> None:
            await self.repaint()
            await logros.track(
                self.cog.bot, self.guild.id, self.owner, after_interaction.channel,
                housing_action_stats(
                    "reforma", reform=reform.key, amount=quote.total, rehab=quote.rehab
                ),
            )  # fmt: skip

        purchase = credit.Purchase(
            f"{reform.emoji} {reform.name} en {listing.title}", quote.total, "obras", works=True
        )
        if not await financiar.open_finance(
            self.cog.bot, interaction, financiar.Deal(purchase, buy, after=after)
        ):
            await interaction.response.send_message(
                "El banco está cerrado ahora mismo.", ephemeral=True
            )

    async def _finance_split(self, interaction: discord.Interaction) -> None:
        """💳 Financiar dividir: la oferta del banco, en privado; al firmar, empiezan las obras."""
        custom_id = interaction.data.get("custom_id", "") if interaction.data else ""
        mode = custom_id.rsplit(":", 1)[-1]
        listing = self.market.listing(self.listing)
        if mode not in rules.SPLIT_MODES or listing is None:
            await ack(interaction)
            return
        key = self.listing
        quote = rules.split_quote(listing, mode, self.market.index(listing.barrio), self.market.ipc)
        mortgaged = bool(self.market.mortgages_of(self.owner.id, key))
        storage = rules.storage_of(listing) if mode == rules.FLOORS else 0

        async def buy(_interaction: discord.Interaction) -> str | None:
            try:
                await self.cog.service.split(self.guild.id, self.owner.id, key, mode)
            except (HousingError, InsufficientFundsError, BalanceLimitError) as error:
                return str(error) or "No se ha podido dividir."
            return None

        async def after(after_interaction: discord.Interaction) -> None:
            self.screen = PLANTAS
            await self.repaint()
            await logros.track(
                self.cog.bot, self.guild.id, self.owner, after_interaction.channel,
                housing_action_stats(
                    mode, amount=quote.total, units=quote.units - storage, mortgaged=mortgaged
                ),
            )  # fmt: skip

        motion = rules.MOTION_BY_KEY[mode]
        purchase = credit.Purchase(
            f"{motion.emoji} {motion.name} en {listing.title}", quote.total, "obras", works=True
        )
        if not await financiar.open_finance(
            self.cog.bot, interaction, financiar.Deal(purchase, buy, after=after)
        ):
            await interaction.response.send_message(
                "El banco está cerrado ahora mismo.", ephemeral=True
            )

    async def repaint(self) -> None:
        """Repinta el panel sin un clic suyo (tras comprar desde otro mensaje, 💳 Financiar)."""
        if self._last is None:
            return
        try:
            await self.show(self._last)
        except (discord.HTTPException, OSError, sqlite3.Error):
            logger.debug("No se pudo repintar el panel de pisos", exc_info=True)

    async def _split(self, interaction: discord.Interaction) -> None:
        """Divide por plantas o por habitaciones (segundo clic): paga y entra en obras."""
        custom_id = interaction.data.get("custom_id", "") if interaction.data else ""
        parts = custom_id.split(":")
        mode = parts[2] if len(parts) > 2 else ""
        mortgaged = bool(self.market.mortgages_of(self.owner.id, self.listing))

        async def run() -> Any:
            return await self.cog.service.split(self.guild.id, self.owner.id, self.listing, mode)

        result = await self._money(interaction, run)
        if result is None:
            return
        done, _balance = result
        quote = done.quote
        self.screen = PLANTAS
        # Los logros cuentan plantas o habitaciones, no los trasteros de un edificio.
        storage = rules.storage_of(self._at(self.listing)) if mode == rules.FLOORS else 0
        await self._done(interaction, Event.BUY, housing_action_stats(
            mode, amount=quote.total, units=quote.units - storage, mortgaged=mortgaged,
        ))  # fmt: skip
        if mode == rules.FLOORS:
            extra = f" (con un local en el bajo y {storage} trasteros)" if storage else ""
            text = (
                f"🏢 Obras hasta el {done.until:%d/%m}: "
                + (
                    "en dos mitades"
                    if rules.halves(self._at(self.listing))
                    else f"{quote.units - storage} plantas{extra}"
                )
                + " "
                f"por {format_amount(quote.cost)} de obra, {format_amount(quote.igic)} de IGIC, "
                f"{format_amount(quote.icio)} de ICIO, {format_amount(quote.license)} de "
                f"licencia y {format_amount(quote.ajd)} de AJD de la escritura. Cada uno es ya "
                "un inmueble: con su inquilino, su hipoteca y su precio, y se vende por separado."
                + (" En el local puedes montar tu negocio." if storage else "")
            )
        else:
            text = (
                f"🛏️ {quote.units} habitaciones amuebladas por {format_amount(quote.cost)} "
                f"(más {format_amount(quote.igic)} de IGIC). Desde el {done.until:%d/%m} cada "
                "una busca su inquilino; cuando estén todas vacías se pueden volver a juntar."
            )
        await notify(interaction, text)

    async def _join_rooms(self, interaction: discord.Interaction) -> None:
        """Junta las habitaciones (segundo clic): el piso vuelve a alquilarse entero."""
        await ack(interaction)
        self.confirm = None
        rushed = any(
            self.market.leases.get(unit.key, rules.Lease()).status == rules.OBRAS
            for unit in self.market.units_of(self.listing)
        )
        try:
            await self.cog.service.join_rooms(self.guild.id, self.owner.id, self.listing)
        except HousingError as error:
            await notify(interaction, str(error))
            await self.show(interaction)
            return
        except (OSError, sqlite3.Error):
            logger.exception("No se pudieron juntar las habitaciones de un piso")
            await notify(interaction, "Se ha caído el cerrajero. Prueba otra vez en un momento.")
            return
        self.screen = FICHA
        await self.show(interaction)
        await notify(interaction, "🔀 Habitaciones juntadas: el piso vuelve a alquilarse entero.")
        await logros.track(self.cog.bot, self.guild.id, self.owner, interaction.channel,
                           housing_action_stats("juntar", rushed=rushed))  # fmt: skip

    async def _decide(self, interaction: discord.Interaction) -> None:
        """Elige cómo arreglar un imprevisto. No mueve dinero: se paga con la noche."""
        custom_id = interaction.data.get("custom_id", "") if interaction.data else ""
        parts = custom_id.split(":")
        await ack(interaction)
        if len(parts) != 4 or not parts[2].isdigit():
            return
        try:
            await self.cog.service.choose(self.guild.id, self.owner.id, int(parts[2]), parts[3])
        except HousingError as error:
            await notify(interaction, str(error))
            return
        await self.show(interaction)
        logros.note(
            self.cog.bot, self.guild.id, self.owner.id,
            housing_action_stats("decide", choice=parts[3]),
            getattr(interaction.channel, "id", None),
        )  # fmt: skip

    async def _go_auctions(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, SUBASTAS)

    async def _pick_auction(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values", []) if interaction.data else []
        if values and str(values[0]).isdigit():
            self.auction_id = int(values[0])
        await self._go(interaction, SUBASTAS)

    async def _sell_member(self, interaction: discord.Interaction) -> None:
        """Abre el formulario de vender a un miembro. Solo memoria (el formulario va primero)."""
        held = self.market.holdings.get(self.listing, {}).get(self.owner.id)
        if held is None:
            await notify(interaction, "No tienes nada en este inmueble.")
            return
        await interaction.response.send_modal(
            TradeModal(self, auction=False, most=held.shares, value=self.market.value(held))
        )

    async def _auction_form(self, interaction: discord.Interaction) -> None:
        """Abre el formulario de sacar a subasta. Solo memoria."""
        held = self.market.holdings.get(self.listing, {}).get(self.owner.id)
        if held is None:
            await notify(interaction, "No tienes nada en este inmueble.")
            return
        value = round(self.market.value(held) * 0.8)
        await interaction.response.send_modal(
            TradeModal(self, auction=True, most=held.shares, value=max(1, value))
        )

    async def _bid_form(self, interaction: discord.Interaction) -> None:
        """Abre el formulario de pujar en la subasta elegida. Solo memoria."""
        auction = next((a for a in self.market.auctions if a.id == self.auction_id), None)
        if auction is None:
            await notify(interaction, "Esa subasta ya ha terminado.")
            return
        least = rules.next_bid(auction.best_bid, auction.min_bid)
        await interaction.response.send_modal(BidModal(self, auction.id, least))

    async def choose_buyer(self, interaction: discord.Interaction, shares: int, price: int) -> None:
        """Tras el formulario: el desplegable para elegir a qué miembro se vende."""
        await ack(interaction)
        self.sale_draft = (shares, price)
        self.screen, self.confirm = GESTION, "comprador"
        await self.show(interaction)

    async def _pick_buyer(self, interaction: discord.Interaction) -> None:
        """Publica la oferta de venta: el comprador la acepta en un mensaje aparte."""
        values = interaction.data.get("values", []) if interaction.data else []
        await ack(interaction)
        self.confirm = None
        draft, self.sale_draft = self.sale_draft, None
        buyer = None
        if values and str(values[0]).isdigit():
            buyer = self.guild.get_member(int(values[0]))
            if buyer is None:
                try:
                    buyer = await self.guild.fetch_member(int(values[0]))
                except discord.HTTPException:
                    buyer = None
        await self.show(interaction)
        if draft is None or buyer is None:
            await notify(interaction, "No encuentro a ese miembro.")
            return
        if buyer.bot or buyer.id == self.owner.id:
            await notify(interaction, "Así no: elige a otro miembro del servidor.")
            return
        shares, price = draft
        offer = MemberSaleOffer(
            self.cog, self.guild, self.owner, buyer, self.listing, shares, price
        )
        if offer.quote is None:
            await notify(interaction, "Ya no tienes esas participaciones.")
            return
        mention = [buyer, *offer.preferred]
        offer.message = await interaction.followup.send(
            content=" ".join(m.mention for m in mention),
            embed=offer.embed(),
            view=offer,
            wait=True,
            allowed_mentions=discord.AllowedMentions(users=mention),
        )

    async def open_auction(self, interaction: discord.Interaction, shares: int, price: int) -> None:
        """Tras el formulario: abre la subasta y la anuncia. No mueve dinero."""
        await ack(interaction)
        try:
            auction = await self.cog.service.create_auction(
                self.guild.id, self.owner.id, self.listing, shares, price
            )
        except HousingError as error:
            await notify(interaction, str(error))
            return
        await self.show(interaction)
        await notify(
            interaction,
            f"🔨 Subasta abierta: {shares} participaciones de «{self._title(self.listing)}» desde "
            f"{format_amount(price)}. Acaba en 24 horas; se puja desde 🔨 Subastas.",
        )
        await self.cog.announce_auction(self.guild.id, auction)
        logros.note(
            self.cog.bot, self.guild.id, self.owner.id, housing_action_stats("subasta"),
            getattr(interaction.channel, "id", None),
        )  # fmt: skip

    async def place_bid(
        self, interaction: discord.Interaction, auction_id: int, amount: int
    ) -> None:
        """Puja: deja en el depósito la puja, su ITP y su notaría."""

        async def run() -> Any:
            return await self.cog.service.bid(self.guild.id, self.owner.id, auction_id, amount)

        bid = await self._money(interaction, run)
        if bid is None:
            return
        self.auction_id = auction_id
        self.screen = SUBASTAS
        await self._done(interaction, Event.HOME, None)
        extra = " Alguien ha pujado en la última hora: la subasta se alarga una hora." if (
            bid.extended
        ) else ""  # fmt: skip
        await notify(
            interaction,
            f"🔨 Pujas {format_amount(amount)}: dejas {format_amount(bid.escrow)} en el depósito "
            f"(con el ITP y la notaría). Si te superan, te lo devuelven entero.{extra}",
        )
        delta = housing_action_stats("puja", extended=bid.extended)
        await logros.track(self.cog.bot, self.guild.id, self.owner, interaction.channel, delta)
        if bid.outbid is not None:
            logros.note(self.cog.bot, self.guild.id, bid.outbid, housing_action_stats("superado"))

    async def _go_candidates(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, CANDIDATOS)
        logros.note(
            self.cog.bot, self.guild.id, self.owner.id, housing_panel_stats(screen="candidatos"),
            getattr(interaction.channel, "id", None),
        )  # fmt: skip

    async def _arm(self, interaction: discord.Interaction) -> None:
        """Primer clic de un botón que pide confirmar: solo cambia los botones (memoria)."""
        custom_id = interaction.data.get("custom_id", "") if interaction.data else ""
        self.confirm = custom_id.removeprefix(f"{GAME}:")
        self.rebuild()
        await interaction.response.edit_message(view=self)

    async def _go_barrio(self, interaction: discord.Interaction) -> None:
        """«◀ Barrio»: a su plano de calles, que es como se ve el barrio desde la 9.15.0."""
        self.barrio = self._at(self.listing).barrio
        await self._go(interaction, CALLES)

    async def _back_to_listing(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, FICHA)

    async def _pick_barrio(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values", []) if interaction.data else []
        if not values or values[0] not in BARRIO_BY_KEY:
            await ack(interaction)
            return
        self.barrio = values[0]
        await self._go(interaction, CALLES)  # el barrio se ve por sus calles (9.15.0)

    async def _open_listing(self, interaction: discord.Interaction) -> None:
        custom_id = interaction.data.get("custom_id", "") if interaction.data else ""
        slot = custom_id.rsplit(":", 1)[-1]
        items = self.market.listings_of(self.barrio)
        if not slot.isdigit() or int(slot) >= len(items):
            await ack(interaction)
            return
        self.listing = items[int(slot)].key
        floors = self.market.split_mode(self.listing) == rules.FLOORS
        await self._go(interaction, PLANTAS if floors else FICHA)

    async def _pick_mine(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values", []) if interaction.data else []
        if not values or self.market.listing(values[0]) is None:
            await ack(interaction)
            return
        self.listing = values[0]
        self.barrio = self._at(self.listing).barrio
        await self._go(interaction, FICHA)

    # -- Dinero ---------------------------------------------------------------------

    async def _buy(self, interaction: discord.Interaction) -> None:
        """Abre el formulario (o la caja, si solo cabe comprarlo entero). Solo memoria."""
        free = self.market.free_shares(self.listing)
        if free <= 0:
            await notify(interaction, "Ya no queda nada libre de este inmueble.")
            return
        await interaction.response.send_modal(SharesModal(self, selling=False, most=free))

    async def _sell(self, interaction: discord.Interaction) -> None:
        held = self.market.holdings.get(self.listing, {}).get(self.owner.id)
        if held is None:
            await notify(interaction, "No tienes nada en este inmueble.")
            return
        await interaction.response.send_modal(SharesModal(self, selling=True, most=held.shares))

    async def open_checkout(
        self, interaction: discord.Interaction, *, selling: bool, shares: int
    ) -> None:
        """Enseña la caja con las cuentas de hoy (aún no se mueve dinero)."""
        self.screen, self.pending, self.done = CAJA, (selling, shares), None
        self.financed = False
        await self.show(interaction)

    async def _toggle_mortgage(self, interaction: discord.Interaction) -> None:
        """Cambia la caja entre contado e hipoteca (la otra ya está pintada)."""
        await ack(interaction)
        if self.pending is None or self.done is not None:
            return
        self.financed = not self.financed
        await self.show(interaction)

    def _expected(self) -> int:
        """Lo que dice la caja que se ve ahora: el total de la compra o lo que se cobra."""
        listing = self._at(self.listing)
        index = self.market.index(listing.barrio)
        selling, shares = self.pending or (False, 1)
        if not selling:
            quote = rules.purchase_quote(listing, shares, index)
            loan = round(quote.price * rules.MORTGAGE_LTV) if self.financed else 0
            return quote.total - loan
        held = self.market.holdings.get(self.listing, {}).get(self.owner.id)
        lease = self.market.leases.get(self.listing, rules.Lease())
        owned = held.shares if held else shares
        return rules.sale_quote(
            listing, min(shares, owned), owned, held.cost if held else 0, index=index,
            arrears=lease.arrears, arrears_ibi=lease.arrears_ibi,
            pool=sum(self.market.shares_of(self.listing).values()),
        ).received  # fmt: skip

    async def _pay(self, interaction: discord.Interaction) -> None:
        # Un segundo clic en Pagar (o uno que llega tarde) no vuelve a cobrar.
        if self.pending is None or self.done is not None:
            await ack(interaction)
            return
        await ack(interaction)
        slot = (self.guild.id, self.owner.id)
        if slot in self.cog.working:
            await notify(interaction, "Espera a que termine la operación de antes.")
            return
        self.cog.working.add(slot)
        selling, shares = self.pending
        try:
            expected = self._expected()
            if selling:
                trade = await self.cog.service.sell(
                    self.guild.id, self.owner.id, self.listing, shares, expected=expected
                )
            else:
                aval = self.financed and await curro.parents_aval(
                    self.cog.bot, self.guild.id, self.owner.id
                )
                trade = await self.cog.service.buy(
                    self.guild.id, self.owner.id, self.listing, shares, expected=expected,
                    financed=self.financed, aval=aval,
                )  # fmt: skip
        except BalanceLimitError:
            await notify(interaction, "No te cabe más dinero en el monedero. Ni a Amancio.")
            return
        except InsufficientFundsError as error:
            tip = (
                ""
                if selling or self.financed
                else " Prueba 🏦 Con hipoteca: el banco pone el 80 %."
            )
            await notify(interaction, f"No te llega: tienes {format_amount(error.balance)}.{tip}")
            return
        except PriceChanged as error:
            self.cog.working.discard(slot)
            await self.show(interaction)
            await notify(
                interaction,
                "El precio ha cambiado mientras mirabas (alguien ha comprado o vendido en el "
                f"barrio): ahora son {format_amount(error.amount)}. No se ha cobrado nada; "
                "mira la caja y vuelve a pulsar.",
            )
            return
        except HousingError as error:
            await notify(interaction, str(error))
            return
        except (OSError, sqlite3.Error, ValueError):
            logger.exception("No se pudo cerrar una operación de pisos")
            await notify(interaction, "Se ha caído el notario. Prueba otra vez en un momento.")
            return
        finally:
            self.cog.working.discard(slot)
        self.done = ("VENDIDO" if selling else "PAGADO", trade.deal)
        moment = Moment(Event.TRADE if selling else Event.BUY)
        hint = await renta.hint(self.cog.bot, self.guild.id, self.owner.id, moment)
        await self.show(interaction, content=hint)
        if isinstance(trade.deal, Purchase) and trade.deal.aval:
            await curro.spend_parents_aval(self.cog.bot, self.guild.id, self.owner.id)
            await notify(
                interaction,
                "👴 El banco solo firma con el aval de tus padres: el piso del pueblo queda de "
                "garantía. Si dejas de pagar, van a por ellos (y tu familia de `pala` lo notará).",
            )
        await renta.remind(self.cog.bot, interaction)
        await self.cog.track_trade(
            self.guild.id, self.owner, interaction.channel, trade.deal, trade.balance
        )
        deal = trade.deal
        if isinstance(deal, Purchase) and deal.promoter is not None and deal.promoter_fee:
            logros.note(
                self.cog.bot,
                self.guild.id,
                deal.promoter,
                StatDelta(add={"pisos_promoter_fees": deal.promoter_fee}),
            )

    async def _money(
        self,
        interaction: discord.Interaction,
        operation: Callable[[], Awaitable[Any]],
        *,
        broke: str | None = None,
    ) -> Any | None:
        """Hace una operación con dinero, una a la vez, con los avisos de siempre.

        Args:
            broke: Aviso si falta dinero (por defecto, que no le llega).

        Returns:
            Lo que devuelve la operación, o `None` si no se ha hecho (ya avisado).
        """
        await ack(interaction)
        self.confirm = None
        slot = (self.guild.id, self.owner.id)
        if slot in self.cog.working:
            await notify(interaction, "Espera a que termine la operación de antes.")
            return None
        self.cog.working.add(slot)
        try:
            return await operation()
        except BalanceLimitError:
            text = "No te cabe más dinero en el monedero. Ni a Amancio."
        except InsufficientFundsError as error:
            text = broke or f"No te llega: tienes {format_amount(error.balance)}."
        except HousingError as error:
            text = str(error)
        except (OSError, sqlite3.Error, ValueError):
            logger.exception("No se pudo cerrar una operación de pisos")
            text = "Se ha caído el notario. Prueba otra vez en un momento."
        finally:
            self.cog.working.discard(slot)
        await notify(interaction, text)
        await self.show(interaction)
        return None

    async def _done(
        self, interaction: discord.Interaction, event: Event, delta: StatDelta | None
    ) -> None:
        """Tras una operación con dinero: la pantalla nueva, la mascota, la Renta y los logros."""
        hint = await renta.hint(self.cog.bot, self.guild.id, self.owner.id, Moment(event))
        await self.show(interaction, content=hint)
        await renta.remind(self.cog.bot, interaction)
        if delta is not None:
            await logros.track(self.cog.bot, self.guild.id, self.owner, interaction.channel, delta)

    async def _amortize(self, interaction: discord.Interaction) -> None:
        me = self.owner.id
        unit = self.market.vpo_of(me)
        ref = vpo_ref(unit) if self.screen == VPO and unit is not None else self.listing
        mortgages = self.market.mortgages_of(me, ref)

        async def run() -> int:
            total = 0
            for mortgage in mortgages:
                paid, _balance = await self.cog.service.amortize(self.guild.id, me, mortgage.id)
                total += paid
            return total

        if await self._money(interaction, run) is None:
            return
        await self._done(interaction, Event.TRADE, housing_action_stats("amortiza"))

    async def _license(self, interaction: discord.Interaction) -> None:
        async def run() -> tuple[int, int]:
            return await self.cog.service.buy_license(self.guild.id, self.owner.id, self.listing)

        if (done := await self._money(interaction, run)) is None:
            return
        if done[0] == 0:
            # La comunidad votó que no (Ley 6/2025 y art. 17.12 LPH): no se paga la tasa.
            await self.show(interaction)
            await notify(
                interaction,
                "🗳️ La comunidad de vecinos ha votado que no a la vivienda vacacional (hacen "
                "falta 3/5). No pagas la tasa; puedes volver a pedirla dentro de una semana.",
            )
            await logros.track(self.cog.bot, self.guild.id, self.owner, interaction.channel,
                               housing_action_stats("licencia_denegada"))  # fmt: skip
            return
        await self._done(interaction, Event.BUY, housing_action_stats("licencia"))

    async def _vulture(self, interaction: discord.Interaction) -> None:
        async def run() -> Any:
            return await self.cog.service.accept_offer(self.guild.id, self.owner.id, self.listing)

        trade = await self._money(interaction, run)
        if trade is None:
            return
        await self._done(interaction, Event.TRADE, None)
        await self.cog.track_trade(
            self.guild.id, self.owner, interaction.channel, trade.deal, trade.balance
        )

    async def _found_socimi(self, interaction: discord.Interaction) -> None:
        async def run() -> tuple[int, int]:
            return await self.cog.service.found_socimi(self.guild.id, self.owner.id)

        if await self._money(interaction, run) is None:
            return
        await self._done(interaction, Event.TRADE, housing_action_stats("socimi"))

    async def _vpo_buy(self, interaction: discord.Interaction) -> None:
        unit = self.market.reservation_of(self.owner.id)

        async def run() -> Any:
            if unit is None:
                raise HousingError("Ya no tienes ninguna VPO reservada.")
            return await self.cog.service.vpo_buy(self.guild.id, self.owner.id, unit)

        if await self._money(interaction, run) is None:
            return
        await self._done(interaction, Event.BUY, housing_action_stats("compra_vpo"))

    async def _vpo_return(self, interaction: discord.Interaction) -> None:
        async def run() -> Any:
            return await self.cog.service.vpo_return(self.guild.id, self.owner.id)

        broke = "El Estado no tiene partida para recomprarla. Vuelve cuando recaude."
        if await self._money(interaction, run, broke=broke) is None:
            return
        await self._done(interaction, Event.TRADE, housing_action_stats("devuelve_vpo"))

    # -- Sin dinero -----------------------------------------------------------------

    async def _vpo_signup(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        join = self.owner.id not in self.market.signups
        try:
            await self.cog.service.vpo_signup(self.guild.id, self.owner.id, join=join)
        except HousingError as error:
            await notify(interaction, str(error))
            return
        await self.show(interaction)
        if join:
            await logros.track(
                self.cog.bot, self.guild.id, self.owner, interaction.channel,
                housing_action_stats("vpo_signup"),
            )  # fmt: skip

    async def _set_home(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        try:
            await self.cog.service.set_home(self.guild.id, self.owner.id, self.listing)
        except HousingError as error:
            await notify(interaction, str(error))
            return
        await self.show(interaction)
        await logros.track(
            self.cog.bot, self.guild.id, self.owner, interaction.channel,
            housing_action_stats("se_muda"),
        )  # fmt: skip

    async def _move_out(self, interaction: discord.Interaction) -> None:
        if self._early_fine():
            await self._move_out_paying(interaction)
            return
        await ack(interaction)
        self.confirm = None
        try:
            await self.cog.service.leave_home(self.guild.id, self.owner.id)
        except HousingError as error:
            await notify(interaction, str(error))
            return
        await self.show(interaction)

    async def _move_out_paying(self, interaction: discord.Interaction) -> None:
        """Deja antes de tiempo el piso que pidió para vivir: paga la indemnización."""

        async def run() -> tuple[int, int]:
            return await self.cog.service.leave_home_paying(self.guild.id, self.owner.id)

        result = await self._money(interaction, run)
        if result is None:
            return
        fine, _balance = result
        await self._done(interaction, Event.HOME, housing_keys_stats("indemniza", amount=fine))
        await notify(
            interaction,
            f"🚪 Te vas antes de tiempo: pagas {format_amount(fine)} de indemnización al inquilino "
            "al que pediste que se fuera (art. 9.3 LAU).",
        )

    async def _leave_home(self, interaction: discord.Interaction) -> None:
        await self._move_out(interaction)

    async def _pick_tenant(self, interaction: discord.Interaction) -> None:
        """Ofrece el piso a un miembro: él acepta o rechaza en un mensaje aparte."""
        values = interaction.data.get("values", []) if interaction.data else []
        await ack(interaction)
        self.confirm = None
        member = None
        if values and str(values[0]).isdigit():
            member = self.guild.get_member(int(values[0]))
            if member is None:
                try:
                    member = await self.guild.fetch_member(int(values[0]))
                except discord.HTTPException:
                    member = None
        await self.show(interaction)
        market, key = self.market, self.listing
        if member is None:
            await notify(interaction, "No encuentro a ese miembro.")
            return
        if member.bot:
            await notify(interaction, "Los bots no alquilan: viven en la nube.")
            return
        if member.id in market.shares_of(key):
            await notify(interaction, "No puedes alquilarle el piso a uno de sus dueños.")
            return
        if member.id in market.homes:
            await notify(interaction, f"{member.display_name} ya vive en otro sitio.")
            return
        listing = self._at(key)
        lease = market.leases.get(key, rules.Lease())
        level = RENT_LEVELS["referencia" if lease.level == rules.VACATION_LEVEL else lease.level]
        rent = rules.asking_rent(listing, level, market.index(listing.barrio))
        income = await self.cog.service.work_income(self.guild.id, member.id)
        days = rules.contract_months(listing, RENT_LEVELS["referencia"], lease.term)
        offer = TenantOffer(self.cog, self.guild, self.owner, member, listing, rent, income,
                            days=days)  # fmt: skip
        offer.message = await interaction.followup.send(
            content=member.mention,
            embed=offer.embed(),
            view=offer,
            wait=True,
            allowed_mentions=discord.AllowedMentions(users=[member]),
        )

    async def _pick_level(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values", []) if interaction.data else []
        await ack(interaction)
        if not values or values[0] not in RENT_LEVELS:
            return
        try:
            await self.cog.service.set_level(self.guild.id, self.owner.id, self.listing, values[0])
        except HousingError as error:
            await notify(interaction, str(error))
            return
        await self.show(interaction)
        logros.note(
            self.cog.bot,
            self.guild.id,
            self.owner.id,
            housing_panel_stats(screen="renta", level=values[0]),
            getattr(interaction.channel, "id", None),
        )

    async def _evict(self, interaction: discord.Interaction) -> None:
        """Pide el desahucio: paga el abogado y el procurador (salvo que cubra el seguro)."""

        async def run() -> tuple[Eviction, int]:
            return await self.cog.service.evict(self.guild.id, self.owner.id, self.listing)

        fee = rules.eviction_fee(
            self.market.leases.get(self.listing, rules.Lease()).rent
            or rules.reference_rent(
                self._at(self.listing), self.market.index(self._at(self.listing).barrio)
            )
        )
        broke = (
            f"No te llega para el abogado y el procurador ({format_amount(fee)} más IGIC). Sin "
            "ellos el juzgado no admite la demanda."
        )
        result = await self._money(interaction, run, broke=broke)
        if result is None:
            return
        eviction, _balance = result
        await self._done(interaction, Event.HOME, None)
        if eviction.fee:
            await notify(
                interaction,
                f"⚖️ Demanda presentada: abogado y procurador {format_amount(eviction.fee)} y "
                f"{format_amount(eviction.fee_igic)} de IGIC. El juicio, el "
                f"{eviction.lease.eviction_at:%d/%m}.",
            )
        elif eviction.insured:
            await notify(
                interaction,
                f"⚖️ Demanda presentada: el abogado lo paga el seguro de impago. El juicio, el "
                f"{eviction.lease.eviction_at:%d/%m}.",
            )
        delta = housing_panel_stats(screen="desahucio", squatters=eviction.squatters)
        delta.merge(housing_action_stats("desahucio", fee=eviction.fee + eviction.fee_igic))
        await logros.track(self.cog.bot, self.guild.id, self.owner, interaction.channel, delta)

    async def _go_keys(self, interaction: discord.Interaction) -> None:
        """🔑 Recuperar el piso: la ficha con las ofertas, no renovar y la necesidad."""
        await self._go(interaction, LLAVES)

    async def _keys_free(self, interaction: discord.Interaction) -> None:
        await self._offer_keys(interaction, rules.KEYS_FREE)

    async def _keys_fair(self, interaction: discord.Interaction) -> None:
        await self._offer_keys(interaction, rules.KEYS_FAIR)

    async def _keys_rich(self, interaction: discord.Interaction) -> None:
        await self._offer_keys(interaction, rules.KEYS_RICH)

    async def _offer_keys(self, interaction: discord.Interaction, offer: str) -> None:
        """Le ofrece al inquilino del catálogo dinero (o nada) por irse."""

        async def run() -> tuple[KeysDeal, int]:
            return await self.cog.service.offer_keys(self.guild.id, self.owner.id, self.listing,
                                                     offer)  # fmt: skip

        result = await self._money(interaction, run)
        if result is not None:
            await self._keys_answered(interaction, result[0], offer)

    async def _keys_accept_ask(self, interaction: discord.Interaction) -> None:
        """Le paga lo que pidió al decir que no."""

        async def run() -> tuple[KeysDeal, int]:
            return await self.cog.service.accept_keys_ask(self.guild.id, self.owner.id,
                                                          self.listing)  # fmt: skip

        result = await self._money(interaction, run)
        if result is not None:
            await self._keys_answered(interaction, result[0], "")

    async def _keys_answered(
        self, interaction: discord.Interaction, deal: KeysDeal, offer: str
    ) -> None:
        """Enseña lo que contestó el inquilino, con su frase, y apunta los logros."""
        tenant = TENANT_BY_KEY.get(deal.tenant or "")
        name = tenant.name if tenant is not None else "El inquilino"
        answer = deal.answer
        if answer.accepted:
            self.screen = FICHA
            await self._done(interaction, Event.HOME, None)
            line = random.choice(KEYS_LINES["si" if answer.paid else "buenas"])
            deal_text = (
                f"acepta {format_amount(answer.paid)}" if answer.paid else "se va por las buenas"
            )
            until = f" Se va el {deal.lease.until:%d/%m}." if deal.lease.until else ""
            await notify(interaction, f"🔑 **{name}** {deal_text}.{until}\n> {line}")
            fame = self.market.fame(self.owner.id, self.cog.service.today())
            action = "llaves" if offer else "contraoferta"
            delta = housing_keys_stats(action, amount=answer.paid, offer=offer, fame=fame)
            await logros.track(self.cog.bot, self.guild.id, self.owner, interaction.channel, delta)
            return
        await self.show(interaction)
        if answer.trench:
            text = (
                f"🚧 **{name}** se atrinchera: deja de pagar desde hoy.\n"
                f"> {random.choice(KEYS_LINES['trinchera'])}\n"
                "Si no se pone al día, toca ⚖️ Desahuciar."
            )
        else:
            line = random.choice(KEYS_LINES["no"]).format(price=format_amount(answer.ask))
            wait = f" hasta el {deal.lease.keys_wait:%d/%m}" if deal.lease.keys_wait else ""
            text = f"✖️ **{name}** no acepta.\n> {line}\nSu precio vale{wait}."
        cameo = await mascotas.cameo(self.cog.bot, self.guild.id, self.owner.id, Moment(Event.HOME))
        await notify(interaction, text + (f"\n{cameo}" if cameo else ""))
        delta = housing_keys_stats("trinchera" if answer.trench else "oferta", offer=offer)
        await logros.track(self.cog.bot, self.guild.id, self.owner, interaction.channel, delta)

    async def _keys_drop_ask(self, interaction: discord.Interaction) -> None:
        """No le paga lo que pidió: su precio se olvida. No mueve dinero."""
        await ack(interaction)
        self.confirm = None
        try:
            await self.cog.service.drop_keys_ask(self.guild.id, self.owner.id, self.listing)
        except HousingError as error:
            await notify(interaction, str(error))
            return
        await self.show(interaction)
        await logros.track(self.cog.bot, self.guild.id, self.owner, interaction.channel,
                           housing_keys_stats("deja"))  # fmt: skip

    async def _toggle_no_renew(self, interaction: discord.Interaction) -> None:
        """Avisa de que no renovará el contrato (o se echa atrás). No mueve dinero."""
        await ack(interaction)
        self.confirm = None
        lease = self.market.leases.get(self.listing, rules.Lease())
        on = not lease.leaving
        try:
            lease = await self.cog.service.set_no_renew(self.guild.id, self.owner.id,
                                                        self.listing, on)  # fmt: skip
        except HousingError as error:
            await notify(interaction, str(error))
            return
        await self.show(interaction)
        if not on:
            await notify(interaction, "↩️ Hecho: si quiere, renovará al acabar el contrato.")
            return
        until = f" el {lease.until:%d/%m}" if lease.until else ""
        tense = rules.tense(self.market.index(self._at(self.listing).barrio))
        extra = " En zona tensionada puede pedirte antes una prórroga de un año." if tense else ""
        await notify(interaction, f"🚪 Avisado: no le renuevas. Se va{until}.{extra}")
        await logros.track(self.cog.bot, self.guild.id, self.owner, interaction.channel,
                           housing_keys_stats("no_renueva"))  # fmt: skip

    async def _claim_need(self, interaction: discord.Interaction) -> None:
        """Pide el piso para vivir en él (art. 9.3 LAU). No mueve dinero."""
        await ack(interaction)
        self.confirm = None
        try:
            lease, fine = await self.cog.service.claim_need(self.guild.id, self.owner.id,
                                                            self.listing)  # fmt: skip
        except HousingError as error:
            await notify(interaction, str(error))
            await self.show(interaction)
            return
        self.screen = FICHA
        await self.show(interaction)
        until = f" el {lease.until:%d/%m}" if lease.until else ""
        await notify(
            interaction,
            f"🏠 Se lo has pedido para vivir (art. 9.3 LAU): se va{until} y esa noche te mudas "
            f"tú. Tienes que vivir ahí {rules.NEED_STAY_DAYS} días; si te vas antes, le pagas "
            f"{format_amount(fine)}.",
        )
        await logros.track(self.cog.bot, self.guild.id, self.owner, interaction.channel,
                           housing_keys_stats("necesidad"))  # fmt: skip

    async def _member_keys_form(self, interaction: discord.Interaction) -> None:
        """Abre el formulario de lo que se le ofrece al miembro (sin `ack`: es un formulario)."""
        lease = self.market.leases.get(self.listing)
        if lease is None or lease.member is None:
            await interaction.response.send_message(
                "Ya no vive ahí ningún miembro.", ephemeral=True
            )
            return
        today = self.cog.service.today()
        fame = self.market.fame(self.owner.id, today)
        fair = rules.keys_fair(lease.rent, rules.days_left(lease, today), 1.0, lease.mood, fame)
        await interaction.response.send_modal(
            KeysModal("¿Cuánto le ofreces por irse?", fair, self.offer_member_keys)
        )

    async def offer_member_keys(self, interaction: discord.Interaction, amount: int) -> None:
        """Publica la oferta por las llaves para el miembro de alquiler (`MemberKeysOffer`)."""
        await ack(interaction)
        lease = self.market.leases.get(self.listing)
        if lease is None or lease.member is None or lease.until is None:
            await notify(interaction, "Ya no vive ahí ningún miembro.")
            return
        member = self.guild.get_member(lease.member)
        if member is None:
            try:
                member = await self.guild.fetch_member(lease.member)
            except discord.HTTPException:
                member = None
        if member is None:
            await notify(interaction, "No encuentro a tu inquilino en el servidor.")
            return
        left = rules.days_left(lease, self.cog.service.today())
        offer = MemberKeysOffer(self.cog, self.guild, self.owner, member, self._at(self.listing),
                                amount, left)  # fmt: skip
        offer.message = await interaction.followup.send(
            content=member.mention,
            embed=offer.embed(),
            view=offer,
            wait=True,
            allowed_mentions=discord.AllowedMentions(users=[member]),
        )

    async def _pick_term(self, interaction: discord.Interaction) -> None:
        """Cambia lo que dura el próximo contrato. No mueve dinero."""
        values = interaction.data.get("values", []) if interaction.data else []
        await ack(interaction)
        if not values or not str(values[0]).isdigit():
            return
        term = int(values[0])
        try:
            await self.cog.service.set_term(self.guild.id, self.owner.id, self.listing, term)
        except HousingError as error:
            await notify(interaction, str(error))
            return
        await self.show(interaction)
        logros.note(
            self.cog.bot, self.guild.id, self.owner.id,
            housing_keys_stats("duracion", term=term), getattr(interaction.channel, "id", None),
        )  # fmt: skip

    async def _toggle_pick(self, interaction: discord.Interaction) -> None:
        """Cambia quién elige al inquilino: la gestora o el dueño. No mueve dinero."""
        await ack(interaction)
        lease = self.market.leases.get(self.listing, rules.Lease())
        pick = rules.PICK_AGENCY if lease.pick == rules.PICK_SELF else rules.PICK_SELF
        try:
            await self.cog.service.set_pick(self.guild.id, self.owner.id, self.listing, pick)
        except HousingError as error:
            await notify(interaction, str(error))
            return
        await self.show(interaction)
        if pick == rules.PICK_SELF:
            await notify(
                interaction,
                "✋ Desde esta noche, cuando el piso esté vacío llegarán candidatos (hasta tres; "
                f"cada uno espera {rules.CANDIDATE_DAYS} días). Si no eliges, el piso sigue vacío.",
            )
            await logros.track(
                self.cog.bot, self.guild.id, self.owner, interaction.channel,
                housing_action_stats("elige_inquilino"),
            )  # fmt: skip

    async def _toggle_insurance(self, interaction: discord.Interaction) -> None:
        """Contrata o quita el seguro de impago (la prima se paga cada noche)."""
        await ack(interaction)
        self.confirm = None
        lease = self.market.leases.get(self.listing, rules.Lease())
        on = not lease.insured
        try:
            await self.cog.service.set_insurance(self.guild.id, self.owner.id, self.listing, on)
        except HousingError as error:
            await notify(interaction, str(error))
            return
        await self.show(interaction)
        if on:
            await logros.track(
                self.cog.bot, self.guild.id, self.owner, interaction.channel,
                housing_action_stats("seguro"),
            )  # fmt: skip

    def _slot_tenant(self, interaction: discord.Interaction) -> str | None:
        """El candidato del botón pulsado (`firmar:2` → el segundo que espera)."""
        custom_id = interaction.data.get("custom_id", "") if interaction.data else ""
        slot = custom_id.rsplit(":", 1)[-1]
        waiting = self.market.candidates_of(self.listing, self.cog.service.today())
        if not slot.isdigit() or not 1 <= int(slot) <= len(waiting):
            return None
        return waiting[int(slot) - 1]

    async def _sign(self, interaction: discord.Interaction) -> None:
        """Firma con un candidato. No mueve dinero: la renta llega con el cierre de la noche."""
        tenant = self._slot_tenant(interaction)
        await ack(interaction)
        if tenant is None:
            return
        try:
            signing = await self.cog.service.sign_candidate(
                self.guild.id, self.owner.id, self.listing, tenant
            )
        except HousingError as error:
            await notify(interaction, str(error))
            await self.show(interaction)
            return
        self.market = await self.cog.service.market(self.guild.id)
        self._next_waiting(FICHA)
        line = await mascotas.cameo(self.cog.bot, self.guild.id, self.owner.id, Moment(Event.HOME))
        await self.show(interaction, content=line)
        person = TENANT_BY_KEY[tenant]
        reduction = signing.lease.reduction or 0
        await notify(
            interaction,
            f"✍️ {person.emoji} **{person.name}** firma por {format_amount(signing.lease.rent)} al "
            f"mes: «{person.line}»"
            + (f" Reducción del {round(reduction * 100)} % en el IRPF." if reduction else ""),
        )
        await logros.track(
            self.cog.bot, self.guild.id, self.owner, interaction.channel,
            housing_action_stats(
                "firma_candidato", tenant=tenant, risk=signing.risk,
                young=reduction == rules.YOUNG_RENT_REDUCTION,
            ),
        )  # fmt: skip

    def _next_waiting(self, otherwise: str) -> None:
        """Tras firmar o vaciar los candidatos de un sitio: al siguiente donde esperan, o a
        `otherwise` si no queda ninguno."""
        keys = self._waiting_keys()
        if keys:
            self.listing, self.screen = keys[0], CANDIDATOS
            self.barrio = self._at(self.listing).barrio
        else:
            self.screen = otherwise

    async def _discard(self, interaction: discord.Interaction) -> None:
        """Descarta a un candidato."""
        tenant = self._slot_tenant(interaction)
        await ack(interaction)
        if tenant is None:
            return
        try:
            await self.cog.service.discard_candidate(
                self.guild.id, self.owner.id, self.listing, tenant
            )
        except HousingError as error:
            await notify(interaction, str(error))
            return
        if not self.market.candidates_of(self.listing, self.cog.service.today())[1:]:
            self.market = await self.cog.service.market(self.guild.id)
            self._next_waiting(GESTION)
        await self.show(interaction)
        logros.note(
            self.cog.bot, self.guild.id, self.owner.id, housing_action_stats("descarta"),
            getattr(interaction.channel, "id", None),
        )  # fmt: skip


class MemberSaleOffer(ui.View):
    """Oferta pública de venta de participaciones a otro miembro.

    El comprador la acepta al contado o con hipoteca, o la rechaza; el vendedor la retira.
    Si se vende entera una vivienda en la que vive de alquiler otro miembro, él tiene el
    tanteo (art. 25 LAU); si se vende a quien no es copropietario, los copropietarios tienen
    el retracto (art. 1522 CC, que va antes). Durante `PREFERENCE_SECONDS` solo ellos pueden
    comprar, al mismo precio, o renunciar; después, el comprador.
    """

    def __init__(
        self,
        cog: Pisos,
        guild: discord.Guild,
        seller: discord.abc.User,
        buyer: discord.abc.User,
        key: str,
        shares: int,
        price: int,
    ) -> None:
        super().__init__(timeout=OFFER_TIMEOUT)
        self.cog = cog
        self.guild = guild
        self.seller = seller
        self.buyer = buyer
        self.key = key
        self.shares = shares
        self.price = price
        self.message: discord.Message | None = None
        self.busy = False
        self.opened = cog.service.now()
        market = cog.market_cache.get(guild.id) or Market()
        self.listing = market.listing(key) or LISTING_BY_KEY.get(key)
        held = market.holdings.get(key, {}).get(seller.id)
        self.quote: rules.MemberSaleQuote | None = None
        if held is not None and self.listing is not None and shares <= held.shares:
            self.quote = rules.member_sale_quote(
                self.listing, shares, held.shares, held.cost, price,
                index=market.index(self.listing.barrio),
            )  # fmt: skip
        holders = market.shares_of(key)
        lease = market.leases.get(key)
        preferred: list[discord.abc.User] = []
        if buyer.id not in holders:
            preferred += [m for m in (guild.get_member(h) for h in holders if h != seller.id) if m]
        whole = shares == holders.get(seller.id, 0) == rules.SHARES
        if whole and lease is not None and lease.member not in (None, buyer.id):
            tenant = guild.get_member(lease.member)
            if tenant is not None:
                preferred.append(tenant)
        #: Quien tiene derecho preferente y aún no ha renunciado.
        self.preferred = preferred
        self.declined: set[int] = set()

    def embed(self, text: str | None = None) -> discord.Embed:
        quote = self.quote
        title = self.listing.title if self.listing else self.key
        if text is None and quote is not None:
            lines = [
                f"**{self.seller.display_name}** le vende a **{self.buyer.display_name}** "
                f"{self.shares} participaciones de «{title}» por **{format_amount(self.price)}** "
                f"(a precio de mercado valen {format_amount(quote.value)}).",
                f"El comprador paga además el ITP, {format_amount(quote.itp)} (sobre el mayor "
                "entre el precio y el valor de referencia, "
                f"{format_amount(quote.reference)}), y la notaría, "
                f"{format_amount(quote.notary + quote.notary_igic)} con IGIC.",
                f"El vendedor tributa el 19 % de su ganancia: {format_amount(quote.gain_tax)}. Las "
                "deudas y el inquilino siguen con el piso.",
            ]
            if self.preferred:
                names = ", ".join(m.display_name for m in self.preferred)
                lines.append(
                    f"Derecho preferente: {names} pueden quedárselo al mismo precio durante "
                    f"{PREFERENCE_SECONDS // 60} minutos o renunciar."
                )
            text = "\n".join(lines)
        return discord.Embed(
            title="🤝 Se vende (a un miembro)",
            description=text or "La oferta ya no vale.",
            color=discord.Color.gold(),
        )

    async def _close(self, interaction: discord.Interaction, text: str) -> None:
        self.stop()
        await interaction.response.edit_message(
            content=None, embed=self.embed(text), view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )  # fmt: skip

    def _pending(self) -> list[discord.abc.User]:
        """Quien tiene derecho preferente, no ha renunciado y aún está a tiempo."""
        if self.cog.service.now() - self.opened >= PREFERENCE_SECONDS:
            return []
        return [m for m in self.preferred if m.id not in self.declined]

    async def _buy(self, interaction: discord.Interaction, *, financed: bool) -> None:
        user = interaction.user
        preferred = {m.id for m in self.preferred}
        if user.id != self.buyer.id and user.id not in preferred:
            await interaction.response.send_message("Esa oferta no es para ti.", ephemeral=True)
            return
        if user.id == self.buyer.id and (waiting := self._pending()):
            names = ", ".join(m.display_name for m in waiting)
            await interaction.response.send_message(
                f"Espera: {names} tienen derecho preferente unos minutos más.", ephemeral=True
            )
            return
        if self.busy:
            await interaction.response.send_message("Un momento, que lo está firmando.",
                                                    ephemeral=True)  # fmt: skip
            return
        self.busy = True
        await ack(interaction)
        try:
            aval = financed and await curro.parents_aval(self.cog.bot, self.guild.id, user.id)
            sale, _balance = await self.cog.service.member_sale(
                self.guild.id, self.seller.id, user.id, self.key, self.shares, self.price,
                financed=financed, aval=aval,
            )  # fmt: skip
        except HousingError as error:
            await notify(interaction, str(error))
            return
        except InsufficientFundsError as error:
            tip = "" if financed else " Prueba con hipoteca: el banco pone el 80 %."
            await notify(interaction, f"No te llega: tienes {format_amount(error.balance)}.{tip}")
            return
        except PriceChanged as error:
            await notify(
                interaction,
                f"El barrio se ha movido y ahora te costaría {format_amount(error.amount)}. No se "
                "ha firmado nada.",
            )
            return
        except BalanceLimitError:
            await notify(interaction, "No te cabe más dinero en el monedero. Ni a Amancio.")
            return
        except (OSError, sqlite3.Error):
            logger.exception("No se pudo cerrar una venta entre miembros")
            await notify(interaction, "Se ha caído el notario. Prueba otra vez en un momento.")
            return
        finally:
            self.busy = False
        self.stop()
        preference = user.id != self.buyer.id
        how = " con el derecho preferente" if preference else ""
        await edit(
            interaction, content=None, view=None,
            embed=self.embed(
                f"🤝 **{user.display_name}** compra{how} {self.shares} participaciones a "
                f"**{self.seller.display_name}** por {format_amount(self.price)}."
                + (" Ahora vive en lo suyo." if sale.moved_in else "")
            ),
        )  # fmt: skip
        if sale.aval:
            await curro.spend_parents_aval(self.cog.bot, self.guild.id, user.id)
        await renta.remind(self.cog.bot, interaction)
        await self.cog.track_member_sale(self.guild.id, self.seller, user, interaction.channel,
                                         sale, preference=preference)  # fmt: skip

    @ui.button(label="Comprar al contado", emoji="🤝", style=discord.ButtonStyle.success,
               custom_id="pisos:venta:contado")  # fmt: skip
    async def cash(self, interaction: discord.Interaction, _button: ui.Button) -> None:
        await self._buy(interaction, financed=False)

    @ui.button(label="Con hipoteca", emoji="🏦", style=discord.ButtonStyle.primary,
               custom_id="pisos:venta:hipoteca")  # fmt: skip
    async def mortgage(self, interaction: discord.Interaction, _button: ui.Button) -> None:
        await self._buy(interaction, financed=True)

    @ui.button(label="Renuncio", emoji="🙅", style=discord.ButtonStyle.secondary,
               custom_id="pisos:venta:renuncio")  # fmt: skip
    async def decline(self, interaction: discord.Interaction, _button: ui.Button) -> None:
        if interaction.user.id == self.buyer.id:
            await self._close(interaction, f"✖️ {self.buyer.display_name} no lo quiere.")
            return
        if interaction.user.id not in {m.id for m in self.preferred}:
            await interaction.response.send_message("Esa oferta no es para ti.", ephemeral=True)
            return
        self.declined.add(interaction.user.id)
        await interaction.response.send_message("Renuncias a tu derecho preferente.",
                                                ephemeral=True)  # fmt: skip

    @ui.button(label="Retirar", emoji="↩️", style=discord.ButtonStyle.secondary,
               custom_id="pisos:venta:retirar")  # fmt: skip
    async def withdraw(self, interaction: discord.Interaction, _button: ui.Button) -> None:
        if interaction.user.id != self.seller.id:
            await interaction.response.send_message("Esa oferta no es tuya.", ephemeral=True)
            return
        await self._close(interaction, f"↩️ {self.seller.display_name} se lo ha pensado mejor.")

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(
                content=None, embed=self.embed("⌛ La oferta de venta ha caducado."), view=None,
                allowed_mentions=discord.AllowedMentions.none(),
            )  # fmt: skip
        except discord.HTTPException:
            pass


class TenantOffer(ui.View):
    """Oferta pública de alquiler de un piso a otro miembro: él acepta o rechaza.

    Al aceptar firma lo que dure el contrato que eligió el dueño (de serie, 5 años: 60 días)
    con la renta que pide el piso ese día, deja la
    fianza (y la garantía adicional si su nómina no llega a 3 veces la renta y no le
    avalan sus padres) y la paga cada noche de su monedero. Si gana poco, cobra el Bono
    Alquiler Joven. La oferta enseña cuánto de su sueldo se le iría en la renta.
    """

    def __init__(
        self,
        cog: Pisos,
        guild: discord.Guild,
        owner: discord.abc.User,
        tenant: discord.abc.User,
        listing: Listing,
        rent: int,
        income: int = 0,
        *,
        days: int = rules.TERM_DEFAULT,
    ) -> None:
        super().__init__(timeout=OFFER_TIMEOUT)
        self.cog = cog
        self.guild = guild
        self.owner = owner
        self.tenant = tenant
        self.listing = listing
        self.key = listing.key
        self.rent = rent
        #: Lo que dura el contrato que se ofrece, en días del bot.
        self.days = days
        #: Neto medio de nómina al día de quien alquila (para la tasa de esfuerzo).
        self.income = income
        self.message: discord.Message | None = None
        #: Hay una firma en marcha: un segundo clic no firma dos veces.
        self.busy = False

    def embed(self, text: str | None = None) -> discord.Embed:
        listing = self.listing
        barrio = BARRIO_BY_KEY[listing.barrio].name
        return discord.Embed(
            title="🔑 Se alquila (a un colega)",
            description=text
            or (
                f"**{self.owner.display_name}** le ofrece a **{self.tenant.display_name}** "
                f"«{listing.title}» ({barrio}) por unos **{format_amount(self.rent)} al mes**, "
                f"{years_text(self.days)} ({self.days} días del bot). La renta se paga cada noche "
                "de tu monedero; si "
                "ganas poco, el Estado te pone el Bono Alquiler Joven. Vivir en casa propia "
                "(aunque sea alquilada) recarga más rápido la batería de `pala`.\n\n"
                + effort_text(self.rent, self.income)
                + "\n"
                + requirements_text(self.rent, self.income)
            ),
            color=discord.Color.teal(),
        )

    async def _close(self, interaction: discord.Interaction, text: str) -> None:
        self.stop()
        await interaction.response.edit_message(
            content=None, embed=self.embed(text), view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )  # fmt: skip

    @ui.button(
        label="Aceptar", emoji="🤝", style=discord.ButtonStyle.success,
        custom_id="pisos:alquiler:si",
    )  # fmt: skip
    async def accept(self, interaction: discord.Interaction, _button: ui.Button) -> None:
        if interaction.user.id != self.tenant.id:
            await interaction.response.send_message("Esa oferta no es para ti.", ephemeral=True)
            return
        if self.busy:
            await interaction.response.send_message("Un momento, que lo está firmando.",
                                                    ephemeral=True)  # fmt: skip
            return
        self.busy = True
        await ack(interaction)
        try:
            aval = await curro.parents_aval(self.cog.bot, self.guild.id, self.tenant.id)
            deal = await self.cog.service.rent_to_member(
                self.guild.id, self.owner.id, self.key, self.tenant.id, aval=aval
            )
        except HousingError as error:
            await notify(interaction, str(error))
            return
        except InsufficientFundsError as error:
            await notify(
                interaction,
                f"No te llega para la fianza: tienes {format_amount(error.balance)}. Sin "
                "fianza no hay llaves, ni en Canarias.",
            )
            return
        except BalanceLimitError:
            await notify(interaction, "Se ha atascado el depósito de fianzas. Prueba otra vez.")
            return
        except (OSError, sqlite3.Error):
            logger.exception("No se pudo firmar un alquiler entre miembros")
            await notify(interaction, "Se ha caído el notario. Prueba otra vez en un momento.")
            return
        finally:
            self.busy = False
        self.stop()
        lease = deal.lease
        bono = " Cobra el Bono Alquiler Joven." if lease.bono else ""
        title = self.listing.title
        await edit(
            interaction, content=None, view=None,
            embed=self.embed(
                f"🤝 **{self.tenant.display_name}** firma con **{self.owner.display_name}**: "
                f"«{title}» por {format_amount(lease.rent)} al mes.{bono} "
                + signed_text(deal)
                + " Bienvenido a la generación inquilina."
            ),
        )  # fmt: skip
        if deal.aval:
            await curro.spend_parents_aval(self.cog.bot, self.guild.id, self.tenant.id)
        rate = rules.effort(lease.rent, self.income)
        await logros.track(
            self.cog.bot, self.guild.id, self.tenant, interaction.channel,
            housing_action_stats(
                "inquilino_miembro", effort=rate, guarantee=deal.guarantee, aval=deal.aval
            ),
        )  # fmt: skip
        await logros.track(self.cog.bot, self.guild.id, self.owner, interaction.channel,
                           housing_action_stats("casero_miembro"))  # fmt: skip

    @ui.button(
        label="Rechazar", emoji="✖️", style=discord.ButtonStyle.secondary,
        custom_id="pisos:alquiler:no",
    )  # fmt: skip
    async def reject(self, interaction: discord.Interaction, _button: ui.Button) -> None:
        if interaction.user.id != self.tenant.id:
            await interaction.response.send_message("Esa oferta no es para ti.", ephemeral=True)
            return
        await self._close(
            interaction,
            f"✖️ {self.tenant.display_name} prefiere seguir en casa de sus padres. Normal.",
        )

    @ui.button(
        label="Retirar", emoji="↩️", style=discord.ButtonStyle.secondary,
        custom_id="pisos:alquiler:retirar",
    )  # fmt: skip
    async def withdraw(self, interaction: discord.Interaction, _button: ui.Button) -> None:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message("Esa oferta no es tuya.", ephemeral=True)
            return
        await self._close(interaction, f"↩️ {self.owner.display_name} se lo ha pensado mejor.")

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(
                content=None, embed=self.embed("⌛ La oferta de alquiler ha caducado."),
                view=None, allowed_mentions=discord.AllowedMentions.none(),
            )  # fmt: skip
        except discord.HTTPException:
            pass


class KeysModal(ui.Modal):
    """Cuánto se ofrece (o se pide) por las llaves de un piso que alquila un miembro."""

    def __init__(
        self,
        title: str,
        suggested: int,
        then: Callable[[discord.Interaction, int], Awaitable[None]],
    ) -> None:
        super().__init__(title=title)
        self.then = then
        self.amount: ui.TextInput = ui.TextInput(
            label="Importe (Y$)",
            placeholder=f"Lo justo serían unos {format_amount(suggested)}",
            default=str(suggested),
            min_length=1,
            max_length=15,
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Valida la cifra (es lo que escribe un miembro) y sigue."""
        amount = _number(self.amount.value)
        if amount is None or not 0 <= amount <= rules.KEYS_MAX:
            await notify(
                interaction, f"Pon un importe en Y$, de 0 a {format_amount(rules.KEYS_MAX)}."
            )
            return
        await self.then(interaction, amount)


class MemberKeysOffer(ui.View):
    """Oferta pública por las llaves de un piso alquilado a un miembro: regatean hasta que uno
    acepta, el otro rechaza o caduca.

    Contesta a quien no hizo la última cifra: el inquilino a la del casero y el casero a la
    contraoferta del inquilino. Al aceptar, el casero paga, el miembro cobra con el 19 % de
    IRPF de su ganancia patrimonial y se va en `housing.KEYS_MOVE_DAYS` días.
    """

    def __init__(
        self,
        cog: Pisos,
        guild: discord.Guild,
        owner: discord.abc.User,
        tenant: discord.abc.User,
        listing: Listing,
        amount: int,
        left: int,
    ) -> None:
        super().__init__(timeout=OFFER_TIMEOUT)
        self.cog = cog
        self.guild = guild
        self.owner = owner
        self.tenant = tenant
        self.listing = listing
        self.amount = amount
        self.left = left
        #: Si la última cifra la ha puesto el casero (contesta el inquilino) o al revés.
        self.by_owner = True
        self.message: discord.Message | None = None
        #: Hay un pago en marcha: un segundo clic no paga dos veces.
        self.busy = False

    def embed(self, text: str | None = None) -> discord.Embed:
        title = f"«{self.listing.title}» ({BARRIO_BY_KEY[self.listing.barrio].name})"
        if self.by_owner:
            ask = (
                f"**{self.owner.display_name}** le ofrece a **{self.tenant.display_name}** "
                f"**{format_amount(self.amount)}** por dejar {title}."
            )
        else:
            ask = (
                f"**{self.tenant.display_name}** pide **{format_amount(self.amount)}** por dejar "
                f"{title}. Le toca a **{self.owner.display_name}**."
            )
        return discord.Embed(
            title="🔑 Te compran las llaves",
            description=text
            or (
                f"{ask}\n\nLe quedan {self.left} días de contrato. Si acepta, tiene "
                f"{rules.KEYS_MOVE_DAYS} días para irse, la fianza vuelve sola y Hacienda se "
                "queda el 19 % de lo que cobra (ganancia patrimonial). Si no, sigue como estaba."
            ),
            color=discord.Color.gold(),
        )

    def _turn(self) -> int:
        """Quién tiene que contestar ahora."""
        return self.tenant.id if self.by_owner else self.owner.id

    async def _close(self, interaction: discord.Interaction, text: str) -> None:
        self.stop()
        await interaction.response.edit_message(
            content=None, embed=self.embed(text), view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )  # fmt: skip

    @ui.button(
        label="Aceptar", emoji="🤝", style=discord.ButtonStyle.success,
        custom_id="pisos:llaves:si",
    )  # fmt: skip
    async def accept(self, interaction: discord.Interaction, _button: ui.Button) -> None:
        if interaction.user.id != self._turn():
            await interaction.response.send_message("Ahora no te toca contestar.", ephemeral=True)
            return
        if self.busy:
            await interaction.response.send_message("Un momento, que se está pagando.",
                                                    ephemeral=True)  # fmt: skip
            return
        self.busy = True
        await ack(interaction)
        try:
            deal = await self.cog.service.member_keys(
                self.guild.id, self.owner.id, self.listing.key, self.tenant.id, self.amount
            )
        except HousingError as error:
            await notify(interaction, str(error))
            return
        except InsufficientFundsError:
            await notify(
                interaction,
                f"A {self.owner.display_name} no le llega para pagar {format_amount(self.amount)}.",
            )
            return
        except BalanceLimitError:
            await notify(interaction, "No cabe más dinero en ese monedero. Probad con menos.")
            return
        except (OSError, sqlite3.Error):
            logger.exception("No se pudo cerrar la compra de unas llaves entre miembros")
            await notify(interaction, "Se ha caído el notario. Prueba otra vez en un momento.")
            return
        finally:
            self.busy = False
        self.stop()
        until = f" el {deal.lease.until:%d/%m}" if deal.lease.until else ""
        text = (
            f"🔑 Trato: **{self.tenant.display_name}** deja {self.listing.title}{until} por "
            f"**{format_amount(deal.amount)}**."
        )
        if deal.tax:
            text += (
                f"\n-# 🐶 IRPF del ahorro de {self.tenant.display_name}: "
                f"−{format_amount(deal.tax)} (19 % de la ganancia patrimonial)."
            )
        await edit(interaction, content=None, view=None, embed=self.embed(text))
        await renta.remind(self.cog.bot, interaction)
        today = self.cog.service.today()
        market = await self.cog.service.market(self.guild.id)
        await logros.track(
            self.cog.bot, self.guild.id, self.owner, interaction.channel,
            housing_keys_stats("miembro", amount=deal.amount,
                               fame=market.fame(self.owner.id, today)),
        )  # fmt: skip
        await logros.track(self.cog.bot, self.guild.id, self.tenant, interaction.channel,
                           housing_keys_stats("vende_llaves", amount=deal.amount))  # fmt: skip

    @ui.button(
        label="Contraoferta", emoji="💬", style=discord.ButtonStyle.primary,
        custom_id="pisos:llaves:otra",
    )  # fmt: skip
    async def counter(self, interaction: discord.Interaction, _button: ui.Button) -> None:
        if interaction.user.id != self._turn():
            await interaction.response.send_message("Ahora no te toca contestar.", ephemeral=True)
            return
        title = "¿Cuánto pides por irte?" if self.by_owner else "¿Cuánto le ofreces?"
        await interaction.response.send_modal(KeysModal(title, self.amount, self._countered))

    async def _countered(self, interaction: discord.Interaction, amount: int) -> None:
        """La nueva cifra: ahora contesta el otro."""
        self.amount = amount
        self.by_owner = interaction.user.id == self.owner.id
        await interaction.response.edit_message(
            embed=self.embed(), view=self, allowed_mentions=discord.AllowedMentions.none()
        )
        await logros.track(self.cog.bot, self.guild.id, interaction.user, interaction.channel,
                           housing_keys_stats("regatea"))  # fmt: skip

    @ui.button(
        label="No hay trato", emoji="✖️", style=discord.ButtonStyle.secondary,
        custom_id="pisos:llaves:no",
    )  # fmt: skip
    async def reject(self, interaction: discord.Interaction, _button: ui.Button) -> None:
        if interaction.user.id == self.tenant.id:
            await self._close(interaction, f"✖️ {self.tenant.display_name} se queda donde está.")
        elif interaction.user.id == self.owner.id:
            await self._close(interaction, f"↩️ {self.owner.display_name} retira la oferta.")
        else:
            await interaction.response.send_message("Esa oferta no es para ti.", ephemeral=True)

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(
                content=None, embed=self.embed("⌛ La oferta por las llaves ha caducado."),
                view=None, allowed_mentions=discord.AllowedMentions.none(),
            )  # fmt: skip
        except discord.HTTPException:
            pass


class Pisos(commands.Cog):
    """El mercado inmobiliario: el panel, la tarea de cada noche y los puentes."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        repository: HousingRepository,
        service: HousingService | None = None,
        renderer: HousingScene | None = None,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.repository = repository
        self.service = service or HousingService(repository, economy)
        self.painter = Painter(renderer or HousingScene())
        self.panels: set[Panel] = set()
        #: `(servidor, miembro)` con una compra o venta en marcha: una a la vez.
        self.working: set[tuple[int, int]] = set()
        #: El último mercado leído de cada servidor (el panel lo refresca): la oferta de venta
        #: a un miembro sale de aquí sin volver a leer la base.
        self.market_cache: dict[int, Market] = {}

    async def cog_load(self) -> None:
        self._nightly.start()

    async def cog_unload(self) -> None:
        self._nightly.cancel()
        for panel in list(self.panels):
            panel.stop()
        self.panels.clear()
        await self.painter.close()

    # -- Cada noche -----------------------------------------------------------------

    @tasks.loop(minutes=10)
    async def _nightly(self) -> None:
        for guild in self.bot.guilds:
            try:
                await self.close_guild(guild.id)
                await self.close_auctions(guild.id)
                await self.close_votes(guild.id)
                # El IPC del crédito (💳 Financiar) de la tienda y las obras, al día.
                await self.service.refresh_ipc(guild.id)
            except Exception:
                # Un servidor roto no para la tarea: si saliera la excepción, `tasks.loop`
                # se pararía para siempre y nadie cobraría más alquileres.
                logger.exception("Fallo inesperado cerrando los días de pisos en %s", guild.id)

    @_nightly.before_loop
    async def _before_nightly(self) -> None:
        await self.bot.wait_until_ready()

    async def close_guild(self, guild_id: int) -> list[DayRun]:
        """Cierra los días que falten en un servidor y apunta sus logros. Nunca lanza."""
        try:
            runs = await self.service.close_days(
                guild_id,
                lambda: curro.ladrillo_builders(self.bot, guild_id),
                lambda member: tienda.owned_keys(self.bot, guild_id, member),
            )
        except (OSError, sqlite3.Error):
            logger.exception("No se pudieron cerrar los días de pisos en %s", guild_id)
            return []
        if runs:
            for member, delta in (await self.tax_stats(guild_id)).items():
                logros.note(self.bot, guild_id, member, delta)
        for run in runs:
            if run.euribor is not None:
                await self._announce_ecb(guild_id, run.day, *run.euribor)
            if run.barrio_news:
                await self._announce_news(guild_id, run.barrio_news)
            if run.auctions:
                market = await self.service.market(guild_id)
                self.market_cache[guild_id] = market
                for auction in market.auctions:
                    if any(number == auction.id for _m, _k, number in run.auctions):
                        await self.announce_auction(guild_id, auction)
            for builder, key in run.promotions:
                await self._announce_promotion(guild_id, builder, key)
                logros.note(self.bot, guild_id, builder, StatDelta(add={"pisos_promotions": 1}))
            events: dict[int, list[str]] = {}
            signed: dict[int, list[str]] = {}
            for item in run.news:
                if item.kind in PERSONAL_EVENTS:
                    who: tuple[int, ...] = (item.user_id,) if item.user_id is not None else ()
                else:
                    who = run.owners.get(item.listing, ())
                tenant = TENANT_BY_KEY.get(item.tenant or "")
                business = item.kind == "firma" and tenant is not None and bool(tenant.kinds)
                for member in who:
                    events.setdefault(member, []).append(item.kind)
                    if business:
                        events[member].append("firma_negocio")
                    if item.kind == "firma" and tenant is not None:
                        signed.setdefault(member, []).append(tenant.key)
            for owners, _event, choice in run.resolved:
                for member in owners:
                    events.setdefault(member, []).append(f"decision_{choice}")
            for owners, _key in run.works:
                for member in owners:
                    events.setdefault(member, []).append("obra_terminada")
            stars: dict[int, list[int]] = {}
            for owners, _key, given in run.reviews:
                for member in owners:
                    stars.setdefault(member, []).append(given)
            for owners, _key in run.superhosts:
                for member in owners:
                    events.setdefault(member, []).append("superanfitrion_logrado")
            for owners, _key in run.cleanings:
                for member in owners:
                    events.setdefault(member, []).append("limpieza")
            young: dict[int, int] = {}
            for key in run.young:
                for member in run.owners.get(key, ()):
                    young[member] = young.get(member, 0) + 1
            news: dict[int, list[str]] = {}
            market = await self.service.market(guild_id) if run.barrio_news else Market()
            for barrio, key in run.barrio_news:
                owners = {
                    m for item in market.listings_of(barrio) for m in market.shares_of(item.key)
                }
                for member in owners:
                    news.setdefault(member, []).append(key)
                    events.setdefault(member, []).append("noticia")
            loan_members = (
                set(run.loan_installments)
                | set(run.loan_unpaid)
                | set(run.loan_covered)
                | set(run.loans_finished)
                | {member for member, _ in run.loan_defaults}
            )
            for member in loan_members:
                delta = loan_night_stats(
                    finished=run.loans_finished.count(member),
                    covered=run.loan_covered.count(member),
                    unpaid=run.loan_unpaid.count(member),
                    defaulted=sum(1 for who, _ in run.loan_defaults if who == member),
                )
                if delta.add:
                    logros.note(self.bot, guild_id, member, delta)
            for member in set(run.mortgage_unpaid):
                events.setdefault(member, []).append("cuota_impagada")
            for member in run.aval_executions:
                await curro.parents_lose_home(self.bot, guild_id, member)
                events.setdefault(member, []).append("ejecucion_aval")
            for member in run.remortgage_executions:
                logros.note(self.bot, guild_id, member, remortgage_executed_stats())
            self._note_guarantees(guild_id, run.guarantee_claims, run.reimbursements)
            for member in set(run.advance_orphans):
                logros.note(self.bot, guild_id, member, advance_orphan_stats())
            everyone = (
                set(run.members) | set(events) | set(run.bono) | set(run.installments) | set(stars)
            )
            for member in everyone | set(run.insurer) | set(run.bonus):
                day = run.members.get(member)
                logros.note(
                    self.bot,
                    guild_id,
                    member,
                    housing_day_stats(
                        rent=day.rent if day else 0,
                        ibi=day.ibi if day else 0,
                        events=tuple(events.get(member, ())),
                        bono=run.bono.get(member, 0),
                        installments=run.installments.get(member, 0),
                        insurer=run.insurer.get(member, 0),
                        bonus=run.bonus.get(member, 0),
                        tenants=tuple(signed.get(member, ())),
                        news=tuple(news.get(member, ())),
                        young=young.get(member, 0),
                        moods=tuple(run.moods.get(member, ())),
                        reviews=tuple(stars.get(member, ())),
                    ),
                )
        return runs

    async def _announce_ecb(self, guild_id: int, week: date, before: float, after: float) -> None:
        """Publica la frase del BCE del lunes si en el servidor alguien tiene pisos. Nunca lanza.

        Sin nadie en el mercado sería un mensaje semanal que no le importa a nadie.
        """
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        try:
            market = await self.service.market(guild_id)
        except (OSError, sqlite3.Error):
            logger.exception("No se pudo leer el mercado para el anuncio del BCE")
            return
        if not market.holdings and not market.mortgages:
            return
        channel = (
            discord.utils.get(guild.text_channels, name=ANNOUNCE_CHANNEL_NAME)
            or guild.system_channel
        )
        if channel is None:
            return
        try:
            await channel.send(
                embed=ecb_embed(before, after, week),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("No se pudo anunciar el Euríbor en %s", guild_id, exc_info=True)

    async def close_auctions(self, guild_id: int) -> AuctionRun | None:
        """Cierra las subastas que han acabado, lo anuncia y apunta sus logros. Nunca lanza."""
        try:
            run = await self.service.close_auctions(guild_id)
        except (OSError, sqlite3.Error):
            logger.exception("No se pudieron cerrar las subastas de pisos en %s", guild_id)
            return None
        for result in run.results:
            await self._announce_result(guild_id, result)
            auction = result.auction
            if result.winner is not None:
                logros.note(self.bot, guild_id, result.winner, housing_action_stats(
                    "gana_subasta", judicial=auction.judicial, amount=result.price
                ))  # fmt: skip
            if not auction.judicial and result.winner is None and not result.cancelled:
                logros.note(self.bot, guild_id, auction.seller,
                            housing_action_stats("subasta_desierta"))  # fmt: skip
        for member, _key in run.executions:
            logros.note(self.bot, guild_id, member,
                        housing_day_stats(rent=0, ibi=0, events=("ejecucion",)))  # fmt: skip
        for member in run.aval_executions:
            await curro.parents_lose_home(self.bot, guild_id, member)
            logros.note(self.bot, guild_id, member,
                        housing_day_stats(rent=0, ibi=0, events=("ejecucion_aval",)))  # fmt: skip
        for member in run.remortgage_executions:
            logros.note(self.bot, guild_id, member, remortgage_executed_stats())
        self._note_guarantees(guild_id, run.guarantee_claims, [])
        return run

    def _note_guarantees(
        self,
        guild_id: int,
        claims: list[tuple[int, int, int]],
        reimbursements: list[tuple[int, int, int]],
    ) -> None:
        """Logros de los avales de la noche: lo que el banco reclama a cada avalista (y por
        quién) y lo que cada avalado devuelve a su avalista."""
        for guarantor, debtor, amount in claims:
            logros.note(self.bot, guild_id, guarantor, guarantee_night_stats(claimed=amount))
            logros.note(self.bot, guild_id, debtor, guarantee_night_stats(defaulted=amount))
        for guarantor, debtor, amount in reimbursements:
            logros.note(self.bot, guild_id, debtor, guarantee_night_stats(repaid=amount))
            logros.note(self.bot, guild_id, guarantor, guarantee_night_stats(recovered=amount))

    async def close_votes(self, guild_id: int) -> list[VoteResult]:
        """Cierra las votaciones de la junta que han acabado, lo anuncia y apunta los logros.

        Nunca lanza.
        """
        try:
            results = await self.service.close_votes(guild_id)
        except (OSError, sqlite3.Error):
            logger.exception("No se pudieron cerrar las votaciones de pisos en %s", guild_id)
            return []
        guild = self.bot.get_guild(guild_id)
        channel = self._channel(guild_id)
        for result in results:
            self.track_vote(guild_id, result)
            if channel is None or guild is None:
                continue
            try:
                await channel.send(
                    self.vote_text(result, guild)
                    + " "
                    + " ".join(f"<@{m}>" for m in result.owners),
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except (discord.Forbidden, discord.HTTPException):
                logger.warning("No se pudo anunciar una votación en %s", guild_id, exc_info=True)
        return results

    def vote_text(self, result: VoteResult, guild: discord.Guild) -> str:
        """Cómo ha acabado una votación de la junta, en una frase para el canal."""
        vote = result.vote
        listing = (self.market_cache.get(guild.id) or Market()).listing(vote.listing)
        listing = listing or LISTING_BY_KEY.get(vote.listing)
        title = listing.title if listing else vote.listing
        names = {vote.proposer: _name(guild, vote.proposer)}
        if vote.kind == "administra" and vote.value.isdigit():
            names[int(vote.value)] = _name(guild, int(vote.value))
        what = view.motion_text(vote.kind, vote.value, vote.amount, names)
        if vote.status == "aprobada":
            return f"🗳️ **Junta de {title}**: aprobado «{what}»."
        if vote.status == "fallida":
            return f"🗳️ **Junta de {title}**: se aprobó «{what}», pero no se puede: {vote.note}"
        return f"🗳️ **Junta de {title}**: rechazado «{what}»."

    def track_vote(self, guild_id: int, result: VoteResult) -> None:
        """Logros de una votación que se cierra (de quien propuso y de los dueños)."""
        vote = result.vote
        if vote.status == "rechazada":
            logros.note(self.bot, guild_id, vote.proposer,
                        housing_action_stats("junta_rechazada"))  # fmt: skip
            return
        if vote.status != "aprobada":
            return
        silent = set(vote.ballots) == {vote.proposer}
        logros.note(self.bot, guild_id, vote.proposer,
                    housing_action_stats("junta_aprobada", silent=silent))  # fmt: skip
        if vote.kind == "administra" and vote.value.isdigit():
            logros.note(self.bot, guild_id, int(vote.value), housing_action_stats("administra"))
        if vote.kind == "derrama":
            for member in result.owners:
                logros.note(self.bot, guild_id, member, housing_action_stats("derrama_fondo"))

    def _channel(self, guild_id: int) -> discord.abc.Messageable | None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return None
        return (
            discord.utils.get(guild.text_channels, name=ANNOUNCE_CHANNEL_NAME)
            or guild.system_channel
        )

    async def announce_auction(self, guild_id: int, auction: Any) -> None:
        """Anuncia una subasta que se abre (voluntaria o judicial). Nunca lanza."""
        channel = self._channel(guild_id)
        listing = (self.market_cache.get(guild_id) or Market()).listing(auction.listing)
        listing = listing or LISTING_BY_KEY.get(auction.listing)
        if channel is None or listing is None:
            return
        kind = (
            "⚖️ Acción de división"
            if auction.division
            else "⚖️ Subasta judicial"
            if auction.judicial
            else "🔨 Subasta"
        )
        embed = discord.Embed(
            title=f"{kind}: {listing.title}",
            description=(
                ("El inmueble entero" if auction.division else f"{auction.shares} participaciones")
                + f" en {BARRIO_BY_KEY[listing.barrio].name}, "
                f"desde {format_amount(auction.min_bid)}. Acaba <t:{int(auction.ends_at)}:R>. "
                "Se puja desde `pisos` → 🔨 Subastas; si alguien puja en la última hora, se "
                "alarga una hora."
            ),
            color=discord.Color.dark_red() if auction.judicial else discord.Color.orange(),
        )
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("No se pudo anunciar una subasta en %s", guild_id, exc_info=True)

    async def _announce_result(self, guild_id: int, result: Any) -> None:
        """Anuncia cómo ha acabado una subasta. Nunca lanza."""
        channel = self._channel(guild_id)
        auction = result.auction
        listing = LISTING_BY_KEY.get(auction.listing) or (
            self.market_cache.get(guild_id) or Market()
        ).listing(auction.listing)
        guild = self.bot.get_guild(guild_id)
        if channel is None or listing is None or guild is None or result.cancelled:
            return
        if result.winner is not None:
            text = (
                f"Se la lleva **{_name(guild, result.winner)}** por {format_amount(result.price)}."
            )
        elif result.bank:
            text = "Sin puja que valga: el banco se lo queda al 70 % y vuelve al mercado."
        elif auction.division:
            text = "Nadie ha pujado: cada dueño vende lo suyo al mercado y se reparte."
        else:
            text = "Desierta: nadie ha pujado. Las participaciones siguen con su dueño."
        try:
            await channel.send(
                embed=discord.Embed(
                    title=f"🔨 Fin de la subasta: {listing.title}", description=text,
                    color=discord.Color.dark_grey(),
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )  # fmt: skip
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("No se pudo anunciar el fin de una subasta en %s", guild_id,
                           exc_info=True)  # fmt: skip

    async def track_member_sale(
        self,
        guild_id: int,
        seller: discord.abc.User,
        buyer: discord.abc.User,
        channel: object,
        sale: MemberSale,
        *,
        preference: bool,
    ) -> None:
        """Logros de una venta entre miembros (de los dos). Nunca lanza."""
        quote = sale.quote
        floor = rules.parent_key(sale.listing) is not None and not rules.is_room(sale.listing)
        await logros.track(self.bot, guild_id, seller, channel, housing_action_stats(
            "vende_miembro", amount=quote.price, gift=quote.gift > 0, floor_unit=floor,
        ))  # fmt: skip
        await logros.track(self.bot, guild_id, buyer, channel, housing_action_stats(
            "compra_miembro", amount=quote.price, preference=preference,
            reference=quote.reference > quote.price, moved_in=sale.moved_in,
            overpaid=quote.price > quote.value * 1.2,
        ))  # fmt: skip

    async def _announce_news(self, guild_id: int, items: list[tuple[str, str]]) -> None:
        """Publica las noticias de barrio del lunes si en el servidor alguien tiene pisos.

        Nunca lanza.
        """
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        try:
            market = await self.service.market(guild_id)
        except (OSError, sqlite3.Error):
            logger.exception("No se pudo leer el mercado para las noticias de barrio")
            return
        if not market.holdings:
            return
        channel = (
            discord.utils.get(guild.text_channels, name=ANNOUNCE_CHANNEL_NAME)
            or guild.system_channel
        )
        if channel is None:
            return
        try:
            await channel.send(
                embed=news_embed(items), allowed_mentions=discord.AllowedMentions.none()
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("No se pudieron anunciar las noticias de barrio en %s", guild_id,
                           exc_info=True)  # fmt: skip

    async def _announce_promotion(self, guild_id: int, builder: int, key: str) -> None:
        """Publica la obra nueva del lunes con su promotor. Nunca lanza."""
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        try:
            market = await self.service.market(guild_id)
        except (OSError, sqlite3.Error):
            logger.exception("No se pudo leer el mercado para la obra nueva")
            return
        listing = market.listing(key)
        channel = (
            discord.utils.get(guild.text_channels, name=ANNOUNCE_CHANNEL_NAME)
            or guild.system_channel
        )
        if listing is None or channel is None:
            return
        try:
            await channel.send(
                embed=promotion_embed(listing, market.index(listing.barrio), _name(guild, builder)),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("No se pudo anunciar la obra nueva en %s", guild_id, exc_info=True)

    # -- Logros de compras y ventas ------------------------------------------------

    async def tax_stats(self, guild_id: int) -> dict[int, StatDelta]:
        """Logros de Hacienda de cada casero: lo que lleva pagado por sus pisos."""
        try:
            taxes = await self.economy.housing_tax_breakdown(guild_id)
            rents = await self.economy.housing_rent_totals(guild_id)
        except (OSError, sqlite3.Error):
            logger.exception("No se pudieron leer los impuestos de los pisos")
            return {}
        return {
            member: housing_tax_stats(taxes=amounts, rent=rents.get(member, 0))
            for member, amounts in taxes.items()
        }

    async def track_trade(
        self,
        guild_id: int,
        member: discord.abc.User,
        channel: object,
        deal: Purchase | Sale,
        balance: int,
    ) -> None:
        """Logros de una compra o una venta. Nunca lanza."""
        when = datetime.now(TIMEZONE)
        market = await self.service.market(guild_id)
        mine = market.of_member(member.id)
        listing = market.listing(deal.listing)
        if listing is None:
            return
        if isinstance(deal, Purchase):
            delta = housing_purchase_stats(
                kind=listing.kind,
                barrio=listing.barrio,
                total=deal.quote.total,
                itp=deal.quote.itp,
                shares=deal.quote.shares,
                shares_now=deal.shares,
                holdings=len(mine),
                whole=sum(h.shares == rules.SHARES for h in mine),
                islands=len({BARRIO_BY_KEY[market.listing(h.listing).barrio].island for h in mine}),  # type: ignore[union-attr]
                balance_after=balance,
                when=when,
                tensed=rules.tense(deal.index_after) and not rules.tense(deal.index_before),
                barrio_whole=all(
                    market.shares_of(item.key).get(member.id) == rules.SHARES
                    for item in listings_of(listing.barrio)
                ),
            )
            delta.merge(housing_streets_stats({h.listing: h.shares for h in mine}))
        else:
            delta = housing_sale_stats(
                shares=deal.quote.shares,
                received=deal.quote.received,
                gain=deal.quote.gain,
                arrears=deal.quote.arrears,
                held_days=deal.held_days,
                holdings_left=len(mine),
                floor=rules.parent_key(deal.listing) is not None
                and not rules.is_room(deal.listing),
            )
        if isinstance(deal, Purchase) and deal.loan:
            euribor = deal.rate - rules.MORTGAGE_SPREAD
            delta.merge(
                housing_action_stats(
                    "hipoteca",
                    loan=deal.loan,
                    floor=euribor <= rules.EURIBOR_MIN + 1e-9,
                    ceiling=euribor >= rules.EURIBOR_MAX - 1e-9,
                    aval=deal.aval,
                )
            )
        if isinstance(deal, Sale) and deal.vulture:
            delta.merge(housing_action_stats("venta_fondo"))
        if isinstance(deal, Purchase) and deal.promoter is not None:
            own = deal.promoter == member.id
            delta.merge(
                StatDelta(add={"pisos_new_build": 1, **({"pisos_own_build": 1} if own else {})})
            )
        if (taxes := (await self.tax_stats(guild_id)).get(member.id)) is not None:
            delta.merge(taxes)
        await logros.track(self.bot, guild_id, member, channel, delta)

    # -- Comando --------------------------------------------------------------------

    async def _open(
        self,
        responder: CommandResponder,
        send: Callable[..., Awaitable[discord.Message]],
    ) -> None:
        guild, member = responder.guild, responder.member
        if guild is None or member is None:
            await responder.send_error("Los pisos solo se compran dentro de un servidor.")
            return
        # Un panel vivo por persona: cada uno pinta pantallas por adelantado, y abrir
        # veinte seguidos dejaría sin hilos al resto del bot. El viejo se queda quieto.
        for old in [p for p in self.panels if p.owner.id == member.id and p.guild.id == guild.id]:
            old.stop()
            self.panels.discard(old)
        panel = Panel(self, guild=guild, owner=member)
        self.panels.add(panel)
        try:
            await panel.show(None, send=send)
        except (OSError, sqlite3.Error, discord.HTTPException):
            self.panels.discard(panel)
            logger.exception("No se pudo abrir el panel de pisos en %s", guild.id)
            await responder.send_error(
                "La inmobiliaria está cerrada ahora mismo. Prueba en un rato."
            )
            return
        logros.note_for(self.bot, responder, housing_panel_stats(screen="abrir"))

    @app_commands.command(name="pisos", description="El mercado inmobiliario de Canarias.")
    @app_commands.guild_only()
    async def pisos(self, interaction: discord.Interaction) -> None:
        """Abre tu panel de pisos en el canal.

        Dibujar la primera pantalla puede pasar de 3 s (el pintor recién abierto), así
        que se acepta antes y el panel llega como seguimiento.
        """
        await interaction.response.defer(thinking=True)

        async def send(**kwargs: Any) -> discord.Message:
            return await interaction.followup.send(wait=True, **kwargs)

        await self._open(InteractionResponder(interaction), send)

    @commands.command(name="pisos")
    @commands.guild_only()
    async def pisos_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.pisos`)."""
        await self._open(ContextResponder(ctx), ctx.send)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra los pisos del servidor cuando el bot deja de pertenecer a él."""
        await self.repository.delete_guild_data(guild.id)
        logger.info("Se eliminaron los pisos del servidor %s", guild.id)


# -- Funciones puente ---------------------------------------------------------------


async def pending(bot: commands.Bot, guild_id: int, user_id: int) -> list[Pending]:
    """Lo que alguien tiene pendiente en `pisos` (para `saldo`), o `[]`.

    Lee el mercado de nuevo (la caché solo se refresca a ratos) y lo deja en la caché.
    """
    cog = find_cog(bot, Pisos)
    if cog is None:
        return []
    try:
        market = await cog.service.market(guild_id)
        deposits = await cog.repository.deposits_of(guild_id, user_id)
    except (OSError, sqlite3.Error):
        logger.exception("No se pudo leer lo pendiente en pisos de %s", user_id)
        return []
    cog.market_cache[guild_id] = market
    return view.pending_items(market, user_id, deposits, cog.service.today())


async def asnef(bot: commands.Bot, guild_id: int, user_id: int) -> bool:
    """Si alguien está en el fichero de morosos del banco (para `perfil`). Nunca lanza."""
    cog = find_cog(bot, Pisos)
    if cog is None:
        return False
    try:
        market = await cog.service.market(guild_id)
    except (OSError, sqlite3.Error):
        logger.exception("No se pudo mirar el fichero de morosos de %s", user_id)
        return False
    cog.market_cache[guild_id] = market
    return market.in_asnef(user_id)


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía y los pisos del bot."""
    await bot.add_cog(Pisos(bot, economy=bot.economy, repository=bot.housing))
