"""Negocios: tu propia empresa dentro de `pala` (cog `Negocio`, sin comando propio).

Se entra desde el inicio de `pala` (botón 🏪 **Negocio**, ver `bot.cogs.work`). El panel
es un `LayoutView` con una imagen de la escena de los negocios (`BusinessScene`) en un
`MediaGallery` (siempre PNG: nunca un GIF que luego cambie, por lo de la Torre) y el texto
debajo, que también sirve de reserva si no hay Node ni Chromium. Pantallas:

- **Montar** (sin negocio): el sector, luego el barrio, el nombre, la paleta y el emblema
  con la vista previa de la fachada, y la confirmación con lo que cuesta.
- **Portada:** la fachada con las cifras de hoy. 🔑 **Abrir** (un turno, una franja),
  📦 **Almacén**, 📊 **Cuentas**, 🎨 **Rótulo**, 🚪 **Cerrar**, el desplegable
  de precios y 🏠 **Inicio** (vuelve al inicio de `pala`).
- **Turno:** el minijuego del sector, un cliente por imagen. Sus botones no los atiende la
  vista sino `Negocio.on_interaction`, por su `custom_id` (`negocio:<panel>:<ronda>:<acción>`),
  como el minijuego de `pala`: así no se pierde ningún clic al redibujar. La imagen del
  siguiente cliente se pinta mientras se atiende al de ahora, y una tarea despide al
  cliente al que se le acaba la paciencia. Un clic cuenta a la hora en que se pulsó
  (`pressed_at`), y lo que tarda en llegar la pantalla nueva no gasta paciencia ni turno
  (`TurnGame.shown`).
- **Tique** del turno, con la nómina del negocio: caja, IRPF a cuenta y embargo.
- **Fase 3:** 🏢 **Sociedad**: constituir la sociedad limitada (con su capital) y, con ella,
  aportar, prestarle, recuperar el préstamo, cobrar sueldo de administrador y repartir
  dividendos, cada importe en un formulario.
- **Fase 2:** 🛠️ **Mejoras** (instalar mejoras del local y lanzar campañas), ⭐ **Reseñas**,
  🚚 **Traslado** (a otro barrio, a un local mayor o a uno tuyo de `pisos`), el precio de cada
  producto en el almacén y 🚪 cerrar traspasándolo o liquidando el stock y las mejoras.
- 🏦 **Banco** (9.7.0): abre en un mensaje privado el mostrador 🏪 Negocio de `banco`, con los
  préstamos a nombre del negocio (`banco.open_business`).
- **Fase 4:** 👥 **Plantilla**: los empleados, con su subida de sueldo, su curso, hacerle fijo
  y despedirle (con lo que cuesta cada forma), y los **candidatos** de hoy con el contrato,
  la jornada y el turno que se les ofrece.
- **9.41.0:** al contratar se elige el **puesto** (dependiente, encargado de turno, repartidor,
  gerente, limpieza, mozo de almacén, community manager o recursos humanos) y el **horario**
  (21: completa, partida, seis horas o media, con la afluencia de cada uno en el sector); en
  👥 Plantilla, 🕒 **cambiar el horario** desde mañana. Con más de seis, la escena va por
  páginas (la del elegido).
- **Fase 5, la cadena:** cada panel mira un local (`biz`). 🗺️ **Cadena** enseña todos, deja
  ir a otro y montar uno más (➕ Otro local, con el rótulo del de ahora), y abre la 🏭 **Nave**:
  alquilarla (o llevarla a una nave tuya de `pisos`), importar contenedores y dejarla. El
  almacén trae lotes de la nave; los candidatos se pueden contratar de gerente; y 🏢 Sociedad
  deja 💼 **Vender la sociedad** con toda la cadena.

Una tarea cada 10 minutos juega las franjas de la plantilla que ya han acabado
(`BusinessService.staff_turns`) y, cada noche, cobra el local de cada negocio y las nóminas
(`BusinessService.close_nights`) y la cuota de autónomo de la semana.

Dinero: todo por `EconomyService.business` (detalle fiscal allí) y la cuota con
`charge_self_employed_fee`. Toda acción con dinero lleva `renta.remind` y el resultado del
turno, `renta.hint` (habla la mascota). Alimenta los logros de 🏪 Negocio.

Funciones puente: para `pala`, abrir el panel (`open_panel`), pintar el inicio (`paint`) y
saber si alguien está atendiendo su negocio (`busy`); para `saldo`, lo pendiente
(`pending`); para `perfil`, una línea del negocio (`business_line`). No lanzan y no hacen
nada sin el cog.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import logging
import random
import sqlite3
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

import discord
from discord import ui
from discord.ext import commands, tasks

from bot.cogs import achievements as logros
from bot.cogs import banco, renta
from bot.repositories.business import companies_in
from bot.services import business_chain as chain
from bot.services import business_sl as sl
from bot.services import business_staff as staff
from bot.services.achievements import (
    StatDelta,
    business_loan_night_stats,
    business_stats,
    staff_course_stats,
    staff_fire_stats,
    staff_hire_stats,
    staff_night_stats,
    staff_permanent_stats,
    staff_raise_stats,
    staff_refused_stats,
    staff_reschedule_stats,
    staff_turns_stats,
)
from bot.services.business import (
    MAX_LOCALES,
    Business,
    move_quote,
    nightly_bill,
    opening_quote,
    rating,
    recommended,
    sale_price,
)
from bot.services.business_catalog import (
    CAMPAIGN_BY_KEY,
    CAMPAIGNS,
    COLOR_NAMES,
    EMBLEMS,
    EQUIPMENT_BY_KEY,
    LENGTH_NAMES,
    LENGTHS,
    PALETTES,
    POINT_BY_KEY,
    PRICE_LEVELS,
    REVIEW_LINES,
    SECTOR_BY_KEY,
    SECTORS,
    SIZES,
    equipment_for,
)
from bot.services.business_chain import Nave, SaleOffer
from bot.services.business_games import (
    COINS,
    HEAT_STEPS,
    QUOTES,
    point_text,
    twist_text,
)
from bot.services.business_scene import BusinessScene
from bot.services.business_view import (
    CONTRACT_NAMES,
    afluencia,
    almacen_state,
    cadena_state,
    campaign_state,
    candidatos_state,
    cierre_state,
    cliente_state,
    cuentas_state,
    horario_text,
    mejoras_state,
    montar_state,
    nave_state,
    negocio_state,
    offer_horario,
    plantilla_state,
    preview_state,
    resenas_state,
    signed,
    size_text,
    sociedad_state,
    traslado_state,
    turn_lines,
    venta_state,
    zec_text,
)
from bot.services.economy import format_amount, next_daily_at, parse_amount
from bot.services.housing_catalog import BARRIO_BY_KEY, BARRIOS, ISLANDS, THEMES
from bot.services.levels import local_day
from bot.services.negocio import (
    MAX_LOTS,
    BusinessError,
    BusinessService,
    BusinessStatus,
    CompanyView,
    OwnLocal,
    StaffRefusedError,
    Turn,
    TurnPlan,
    TurnResult,
)
from bot.services.pending import Pending, Side
from bot.services.pets import Event as PetEvent
from bot.services.pets import Moment, Mood
from bot.services.taxes import TAX_COLLECTOR, igic
from bot.services.telemetry import LISTENER_ITEM, TimedLock
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, claim_custom_ids, edit, notify

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

#: Segundos sin tocar el panel antes de que caduque.
PANEL_TIMEOUT = 300
#: Nombre del PNG de la escena en el mensaje.
PNG_NAME = "negocio.png"
#: Pantallas pintadas que se guardan (las del adelanto y las de vuelta).
CACHE_SIZE = 48
#: Prefijo de los `custom_id` de los botones del minijuego.
GAME_PREFIX = "negocio:"
#: Segundos de margen tras la paciencia de un cliente antes de despedirlo (lo que tarda
#: Discord en llevar un clic que salió a tiempo). Un clic cuenta a la hora en que se
#: pulsó (`pressed_at`), con este margen como mucho.
GRACE_SECONDS = 1.0
COLOR = discord.Color.from_rgb(31, 181, 173)
COLOR_GAME = discord.Color.from_rgb(88, 101, 242)
COLOR_BAD = discord.Color.from_rgb(80, 84, 92)

#: Emoji de cada producto para los botones (la escena los dibuja con trazos).
PRODUCT_EMOJI: dict[str, str] = {
    "cafe": "☕", "cortado": "🥛", "barraquito": "🍮", "zumo": "🍊", "bocadillo": "🥖",
    "dulce": "🥐", "pan": "🍞", "leche": "🧃", "fruta": "🍎", "conservas": "🥫",
    "limpieza": "🧴", "bebidas": "🥤", "pastillas": "🛑", "bateria": "🔋", "aceite": "🛢️",
    "correa": "⛓️", "bujias": "⚡", "neumatico": "🛞",
    "vino": "🍷", "papas": "🥔", "carne": "🍖", "costillas": "🍍", "queso": "🧀",
    "escaldon": "🥣", "barra": "🥖", "millo": "🌽", "quesadilla": "🥧", "truchas": "🥟",
    "bizcochon": "🍰", "tarta": "🎂", "chicharros": "🐟", "vieja": "🐠", "cherne": "🐡",
    "sama": "🎣", "pulpo": "🐙", "lapas": "🐚", "pantalla": "📱", "bateria_movil": "🔋",
    "conector": "🔌", "camara_movil": "📷", "altavoz": "🔊", "tapa": "🧩", "corte": "✂️",
    "degradado": "💈", "barba": "🧔", "tinte": "🎨", "mechas": "✨", "peinado": "🚿",
}  # fmt: skip
#: Cómo se juega cada minijuego, en una línea.
HOW_TO = {
    "comandas": "Pulsa lo que pide la mesa y luego ✅ Servir. Rápido deja propina.",
    "caja": "Da el cambio con las monedas y pulsa ✅ Cobrar. Si alguien esconde algo, 👀.",
    "diagnostico": "Elige la avería por el síntoma y luego el presupuesto: caro, más fácil "
    "que no lo acepte.",
    "brasa": f"🔥 sube {HEAT_STEPS['1'][0]}-{HEAT_STEPS['1'][1]} puntos y 🔥🔥 "
    f"{HEAT_STEPS['2'][0]}-{HEAT_STEPS['2'][1]}. Saca el plato (🍽️) dentro de su punto; a "
    "100, se quema.",
    "encargos": "Dale la bolsa que es suya: mira el nombre, la inicial y lo que lleva. Si "
    "no hay ninguna, 🚫 No está.",
    "balanza": "Pon y quita piezas hasta acercarte a lo que pide (un 10 % arriba o abajo "
    "vale) y pulsa ⚖️ Pesar.",
    "revision": "Mira el móvil por delante y por detrás, marca cada pieza rota y pulsa 🔧 "
    "Reparar. Puede haber más de una.",
    "foto": "Haz lo de la foto con el cambio que te pide y pulsa ✂️ Listo. Lo que cortas de "
    "más no vuelve.",
}

PORTADA = "portada"
ALMACEN = "almacen"
CUENTAS = "cuentas"
ROTULO = "rotulo"
CERRAR = "cerrar"
MONTAR = "montar"
PREVIA = "previa"
CONFIRMAR = "confirmar"
TURNO = "turno"
CIERRE = "cierre"
MEJORAS = "mejoras"
RESENAS = "resenas"
TRASLADO = "traslado"
SOCIEDAD = "sociedad"
PLANTILLA = "plantilla"
CANDIDATOS = "candidatos"
DESPIDO = "despido"
CADENA = "cadena"
NAVE = "nave"
VENTA = "venta"
#: Los contratos que se ofrecen: `(clave, nombre, descripción)`. Desde la 9.41.0 el gerente
#: es un puesto (`business_staff.ROLES`), no un contrato.
CONTRACTS: tuple[tuple[str, str, str], ...] = (
    ("indefinido", "Indefinido", "Con período de prueba; al parado de larga duración, bonificado"),
    ("temporal", "Temporal (6 meses)", "Se acaba solo y cotiza algo más de desempleo"),
    (
        "b",
        "En B, sin alta",
        "Ni cotiza ni contrato. Si pasa la Inspección, multa de "
        f"{format_amount(staff.FINE_PER_WORKER)}",
    ),
)
#: El horario que se ofrece de entrada: la completa de mañana; a quien solo quiere media
#: jornada, la media de las 10.
DEFAULT_HORARIO = staff.HORARIOS[0].key
DEFAULT_HALF = staff.horario_key(staff.HALF_SHIFTS["manana"])


def game_id(token: int, round_index: int, action: str) -> str:
    """`custom_id` de un botón del minijuego: panel, ronda y acción."""
    return f"{GAME_PREFIX}{token}:{round_index}:{action}"


def parse_game_id(custom_id: str) -> tuple[int, int, str] | None:
    """Lo contrario de `game_id`; `None` si no es un botón del minijuego."""
    if not custom_id.startswith(GAME_PREFIX):
        return None
    token, _, rest = custom_id[len(GAME_PREFIX) :].partition(":")
    round_text, _, action = rest.partition(":")
    if not token.isdigit() or not round_text.isdigit() or not action:
        return None
    return int(token), int(round_text), action


def pressed_at(interaction: discord.Interaction, now: float) -> float:
    """Cuándo pulsó el jugador: la hora de Discord, entre `now - GRACE_SECONDS` y `now`.

    Un clic que salió a tiempo puede llegar tarde (la red, el bucle ocupado): cuenta cuando
    se pulsó y no cuando llegó. El margen evita que un reloj del NAS desfasado regale o
    quite segundos.
    """
    created = getattr(interaction, "created_at", None)
    if not isinstance(created, datetime):
        return now
    return max(now - GRACE_SECONDS, min(now, created.timestamp()))


def state_key(state: dict[str, Any]) -> str:
    """Huella de una pantalla: dos pantallas iguales se pintan igual."""
    text = json.dumps(state, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(text.encode()).hexdigest()


class Painter:
    """Pinta pantallas, las guarda por su huella y pinta por adelantado las que pueden venir.

    Como el de `pisos`, pero `paint` devuelve `None` si no hay con qué pintar (el panel
    sale entonces en texto). Los adelantos van de uno en uno y se sueltan si el panel ya
    no está donde estaba (`still`).
    """

    def __init__(self, scene: BusinessScene) -> None:
        self.scene = scene
        self.cache: OrderedDict[str, bytes] = OrderedDict()
        self._painting: dict[str, asyncio.Future[bytes | None]] = {}
        self._next: list[tuple[dict[str, Any], Callable[[], bool] | None]] = []
        self._worker: asyncio.Task[None] | None = None

    def ready(self, state: dict[str, Any]) -> bytes | None:
        """El PNG si ya está pintado (sin esperar)."""
        return self.cache.get(state_key(state))

    async def paint(self, state: dict[str, Any]) -> bytes | None:
        """El PNG de una pantalla (`None` si no se puede pintar)."""
        key = state_key(state)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        if key in self._painting:
            return await asyncio.shield(self._painting[key])
        future: asyncio.Future[bytes | None] = asyncio.get_running_loop().create_future()
        self._painting[key] = future
        png: bytes | None = None
        try:
            pngs = await self.scene.paint([state])
            png = pngs[0] if pngs else None
            if png is not None:
                self.cache[key] = png
                while len(self.cache) > CACHE_SIZE:
                    self.cache.popitem(last=False)
        except Exception:
            logger.exception("No se pudo pintar una pantalla del negocio")
        finally:
            self._painting.pop(key, None)
            future.set_result(png)
        return png

    def prefetch(
        self, states: list[dict[str, Any]], still: Callable[[], bool] | None = None
    ) -> None:
        """Pinta en segundo plano `states` (del más al menos probable), de uno en uno."""
        self._next = [(s, still) for s in states if state_key(s) not in self.cache]
        if self._next and (self._worker is None or self._worker.done()):
            self._worker = asyncio.create_task(self._work(), name="negocio-adelanto")

    async def _work(self) -> None:
        while self._next:
            state, still = self._next.pop(0)
            if still is not None and not still():
                continue
            await self.paint(state)

    async def close(self) -> None:
        """Para el adelanto y cierra Node y el navegador."""
        if self._worker is not None:
            self._worker.cancel()
        await self.scene.close()


def _stars_text(value: float) -> str:
    return f"{value:.1f}".replace(".", ",").replace(",0", "") + " ⭐"


def plural_locales(count: int) -> str:
    """`1 local`, `3 locales`."""
    return f"{count} local" if count == 1 else f"{count} locales"


def chain_peaks(locales: list[Business]) -> dict[str, int]:
    """Los picos de la cadena para los logros: locales, sectores, islas y la marca repetida."""
    if not locales:
        return {}
    names: dict[str, int] = {}
    for business in locales:
        names[business.name] = names.get(business.name, 0) + 1
    return {
        "biz_locales_max": len(locales),
        "biz_chain_sectors_max": len({b.sector for b in locales}),
        "biz_chain_islands_max": len({b.barrio_info.island for b in locales}),
        "biz_chain_brand_max": max(names.values()),
    }


class NegocioPanel(ui.LayoutView):
    """El panel del negocio de un miembro (solo lo toca su dueño).

    Args:
        cog: El cog `Negocio`.
        guild_id: Servidor.
        owner: Dueño del panel.
        channel: Canal del mensaje (para los avisos).
    """

    def __init__(
        self, cog: Negocio, *, guild_id: int, owner: discord.abc.User, channel: object
    ) -> None:
        super().__init__(timeout=PANEL_TIMEOUT)
        self.cog = cog
        self.service = cog.service
        self.guild_id = guild_id
        self.owner = owner
        self.channel = channel
        self.message: discord.Message | None = None
        self.screen = PORTADA
        self.status: BusinessStatus | None = None
        self.notes: list[str] = []
        self.product = ""
        self.draft: dict[str, Any] = {
            "sector": "", "barrio": "", "name": "Tu negocio", "palette": 0, "emblem": 0,
        }  # fmt: skip
        self.turn: Turn | None = None
        self.result: TurnResult | None = None
        #: El azar del próximo turno, para pintar su primer cliente antes de 🔑 Abrir.
        self.plan: TurnPlan | None = None
        #: Lo elegido en 🛠️ Mejoras: la mejora y la campaña.
        self.equipment = ""
        self.campaign = ""
        #: El traslado que se mira: barrio y tamaño, o un local propio (`own`).
        self.move: dict[str, Any] = {"barrio": "", "size": 0, "own": ""}
        self.own_locals: list[OwnLocal] = []
        #: 🏢 Sociedad: el capital elegido para constituirla y la foto de la sociedad.
        self.capital = sl.FULL_CAPITAL
        self.company: CompanyView | None = None
        #: 👥 Plantilla: el empleado elegido, el candidato elegido y lo que se le ofrece.
        self.employee: int | None = None
        self.candidate: int | None = None
        self.offer: dict[str, Any] = {
            "contract": "indefinido", "role": "", "horario": DEFAULT_HORARIO,
        }  # fmt: skip
        #: Fase 5: el local que se mira (`None`: el primero), la cadena entera y su nave.
        self.biz: int | None = None
        self.chain: list[Business] = []
        self.nave: Nave | None = None
        #: 🏭 Nave: el barrio y el tamaño que se miran para alquilarla, la nave propia elegida,
        #: el producto del contenedor y si se ha pedido dejarla (el segundo clic la deja).
        self.nave_pick: dict[str, Any] = {"barrio": "", "size": 0, "own": "", "product": ""}
        self.own_naves: list[OwnLocal] = []
        self.leaving_nave = False
        #: 💼 Lo que paga el comprador por la sociedad.
        self.sale: SaleOffer | None = None
        self.token = cog.new_token()
        self._shown = ""
        #: Si la pantalla de ahora lleva imagen (la revisión del móvil se cuenta en el texto
        #: cuando no la hay).
        self._has_image = False
        self._lock = TimedLock("negocio:NegocioPanel._lock")
        self._render_lock = TimedLock("negocio:NegocioPanel._render_lock")
        self._ticket = 0
        self._timer: asyncio.Task[None] | None = None
        self._finishing = False

    # -- Lo que se pinta ------------------------------------------------------------------

    def _foot(self) -> str:
        status = self.status
        if status is None:
            return ""
        blocker = status.blocker
        if blocker:
            return blocker
        agenda = status.agenda
        if agenda.free:
            return (
                f"Abrir gasta 1 franja. Te quedan {agenda.free} (y {agenda.night_free} de noche)."
            )
        return f"Abrir gasta 1 franja: estás trasnochando. Te quedan {agenda.night_free} de noche."

    def _portada_state(self, business: Business) -> dict[str, Any]:
        """La portada del local que se mira."""
        return negocio_state(
            business,
            foot=self._foot(),
            rivals=self.status.rivals if self.status else 0,
            chain_count=len(self.chain),
        )

    def _nave_products(self) -> list[str]:
        """Lo que venden sus locales de la isla de la nave (todos, si aún no tiene nave)."""
        island = self.nave.island if self.nave is not None else None
        keys: list[str] = []
        for business in self.chain:
            if island is not None and business.barrio_info.island != island:
                continue
            keys += [p.key for p in business.sector_info.products if p.key not in keys]
        return keys

    def _nave_barrio(self) -> str:
        """El barrio que se mira para alquilar la nave (de entrada, el del local)."""
        if self.nave_pick["barrio"]:
            return str(self.nave_pick["barrio"])
        return self.chain[0].barrio if self.chain else BARRIOS[0].key

    def _nave_screen(self) -> dict[str, Any]:
        own = next((o for o in self.own_naves if o.listing.key == self.nave_pick["own"]), None)
        barrio = own.listing.barrio if own is not None else self._nave_barrio()
        size = own.size if own is not None and own.size is not None else self.nave_pick["size"]
        return nave_state(
            self.nave, self._today(), products=self._nave_products(),
            selected=self.nave_pick["product"], barrio=barrio, size=size,
        )  # fmt: skip

    def scene_state(self) -> dict[str, Any] | None:
        """El estado de la escena de la pantalla de ahora (`None` si no tiene imagen)."""
        business = self.status.business if self.status else None
        draft = self.draft
        if self.screen == MONTAR:
            return montar_state(draft["sector"] or None)
        if self.screen in (PREVIA, CONFIRMAR) or (self.screen == ROTULO and business is None):
            return preview_state(
                draft["sector"], draft["barrio"], draft["name"], draft["palette"], draft["emblem"]
            )
        if business is None:
            return None
        if self.screen == ROTULO:
            return preview_state(
                business.sector, business.barrio, draft["name"], draft["palette"], draft["emblem"]
            )
        if self.screen == ALMACEN:
            return almacen_state(business, "Cada compra paga su IGIC. Lo perecedero se estropea.")
        if self.screen == CUENTAS:
            return cuentas_state(business)
        if self.screen == MEJORAS:
            return mejoras_state(business, self._today())
        if self.screen == RESENAS:
            return resenas_state(business, self._today())
        if self.screen == SOCIEDAD and self.company is not None:
            return sociedad_state(self.company, capital=self.capital)
        if self.screen in (PLANTILLA, DESPIDO):
            return plantilla_state(business, self._today(), selected=self.employee)
        if self.screen == CANDIDATOS:
            return candidatos_state(
                business, self.service.candidates(business), self._today(),
                selected=self.candidate, offer=self._offer(business),
            )  # fmt: skip
        if self.screen == TRASLADO and (target := self._move_target()) is not None:
            barrio, size, quote, own = target
            return traslado_state(business, barrio, size, quote, own=own)
        if self.screen == TURNO and self.turn is not None and self.turn.game.current is not None:
            return cliente_state(self.turn.business, self.turn.game)
        if self.screen == CIERRE and self.result is not None:
            return cierre_state(self.result.business, self.result)
        if self.screen == CADENA:
            return cadena_state(self.chain, self.nave, selected=self.biz)
        if self.screen == NAVE:
            return self._nave_screen()
        if self.screen == VENTA and self.company is not None and self.sale is not None:
            return venta_state(self.company, self.sale)
        return self._portada_state(business)

    def _neighbours(self) -> list[dict[str, Any]]:
        """Pantallas a las que se puede ir desde esta (para pintarlas antes del clic)."""
        business = self.status.business if self.status else None
        if self.screen == TURNO and self.turn is not None:
            return self._next_clients()
        if business is None:
            return []
        out = []
        if self.screen in (PORTADA, CIERRE):
            out = self._first_client()
            out.append(
                almacen_state(business, "Cada compra paga su IGIC. Lo perecedero se estropea.")
            )
            out.append(mejoras_state(business, self._today()))
            out.append(cuentas_state(business))
            out.append(resenas_state(business, self._today()))
            out.append(plantilla_state(business, self._today(), selected=self.employee))
            out.append(cadena_state(self.chain, self.nave, selected=self.biz))
        elif self.screen == CADENA:
            out = [self._nave_screen(), self._portada_state(business)]
        elif self.screen == NAVE:
            out = [cadena_state(self.chain, self.nave, selected=self.biz)]
        elif self.screen == PLANTILLA:
            out = [
                candidatos_state(business, self.service.candidates(business), self._today()),
                self._portada_state(business),
            ]
        elif self.screen in (CANDIDATOS, DESPIDO):
            out = [plantilla_state(business, self._today(), selected=self.employee)]
        elif self.screen in (
            ALMACEN, CUENTAS, ROTULO, CERRAR, MEJORAS, RESENAS, TRASLADO, SOCIEDAD,
        ):  # fmt: skip
            out = [self._portada_state(business)]
        return out

    def _today(self) -> Any:
        return local_day(self.service.now())

    def _chosen(self, business: Business) -> dict[str, Any] | None:
        """El empleado elegido en 👥 Plantilla (el primero si no hay otro), o `None`."""
        found = next((e for e in business.staff if e["id"] == self.employee), None)
        if found is None and business.staff:
            found = business.staff[0]
        self.employee = found["id"] if found is not None else None
        return found

    def _candidate(self, business: Business) -> dict[str, Any] | None:
        """El candidato elegido, si sigue entre los de hoy."""
        return next(
            (c for c in self.service.candidates(business) if c["key"] == self.candidate), None
        )

    def _offer(self, business: Business) -> dict[str, Any] | None:
        """Lo que se ofrece al candidato elegido: el puesto, si aún se puede contratar; la media
        jornada, si solo quiere esa (y de gerente, entonces, no); el gerente, indefinido."""
        candidate = self._candidate(business)
        if candidate is None:
            return None
        roles = self._roles(business, candidate)
        if roles and self.offer["role"] not in {r.key for r in roles}:
            self.offer["role"] = roles[0].key
        if candidate["part_time"] and offer_horario(self.offer).jornada != staff.HALF_DAY:
            self.offer["horario"] = DEFAULT_HALF
        if self.offer["role"] == staff.MANAGER:
            self.offer["contract"] = "indefinido"
        return dict(self.offer)

    def _roles(self, business: Business, candidate: dict[str, Any]) -> list[staff.Role]:
        """Los puestos que se le pueden ofrecer hoy al candidato (los que caben en el local y
        no están cubiertos; el de gerente no, si solo quiere media jornada)."""
        return [
            r
            for r in staff.roles_for(business)
            if staff.hire_problem(business, r.key) is None
            and not (candidate["part_time"] and not r.schedule)
        ]

    def _move_target(self) -> tuple[str, int, Any, bool] | None:
        """El local que se mira en 🚚 Traslado: `(barrio, tamaño, presupuesto, propio)`."""
        if self.status is None:
            return None
        business = self.status.business
        own = next((o for o in self.own_locals if o.listing.key == self.move["own"]), None)
        if own is not None and own.size is not None:
            barrio = BARRIO_BY_KEY[own.listing.barrio]
            return barrio.key, own.size, move_quote(business, barrio, own.size, own=True), True
        barrio_key = self.move["barrio"] or business.barrio
        size = self.move["size"]
        return barrio_key, size, self.service.move_quote(business, barrio_key, size), False

    def _first_client(self) -> list[dict[str, Any]]:
        """El primer cliente del próximo turno, ya sorteado: 🔑 Abrir no espera al dibujo."""
        assert self.status is not None
        self.plan = self.plan or self.service.plan()
        game = self.service.preview(self.status, self.plan)
        return [] if game is None else [cliente_state(self.status.business, game)]

    def _next_clients(self) -> list[dict[str, Any]]:
        """El presupuesto del cliente de ahora (taller) y el siguiente cliente."""
        assert self.turn is not None
        game = self.turn.game
        out: list[dict[str, Any]] = []
        if game.current is None:
            return out
        if game.sector.game == "diagnostico" and game.step == "diagnostico":
            out.append({**cliente_state(self.turn.business, game), "step": "presupuesto"})
        if game.index + 1 < len(game.clients):
            ahead = game.ahead()
            out.append(cliente_state(self.turn.business, ahead))
        return out

    def text(self) -> str:
        """El texto debajo de la imagen (y la reserva si no hay imagen)."""
        status = self.status
        lines: list[str] = []
        if self.screen == MONTAR:
            lines.append("## 🏪 Monta tu negocio" if not self.chain else "## ➕ Otro local")
            for sector in SECTORS:
                lines.append(f"{sector.emoji} **{sector.name}** · {sector.blurb}")
            if self.chain and self.chain[0].is_company:
                lines.append(
                    f"-# Lo abre {self.chain[0].name}, S.L.: lo paga su caja y tributa con ella."
                )
            elif self.chain:
                lines.append(
                    f"-# Llevas {len(self.chain)} de {MAX_LOCALES} locales. Las franjas y la "
                    "cuota de autónomo son tuyas: no pagas otra."
                )
            else:
                lines.append(
                    "-# Eres autónomo: respondes con lo tuyo y el alquiler corre aunque no abras."
                )
        elif self.screen in (PREVIA, CONFIRMAR):
            draft = self.draft
            sector = SECTOR_BY_KEY[draft["sector"]]
            barrio = BARRIO_BY_KEY[draft["barrio"]]
            quote = opening_quote(sector, barrio)
            lines.append(f"## {sector.emoji} {draft['name']} · {barrio.name}")
            lines.append(
                f"Tema del barrio: {THEMES[barrio.theme]} · demanda ×"
                f"{sector.fit.get(barrio.theme, 1.0):.2f}".replace(".", ",")
            )
            lines.append(
                f"🧾 Tasa de apertura **{format_amount(quote.license)}** · Acondicionamiento "
                f"**{format_amount(quote.setup)}** + IGIC {format_amount(quote.setup_igic)} · "
                f"Fianza (2 meses) **{format_amount(quote.deposit)}**"
            )
            lines.append(
                f"🔑 Alquiler: **{format_amount(quote.rent)}** cada noche, más IGIC y suministros."
                " Se paga aunque no abras."
            )
            lines.append(
                f"Total al montarlo: **{format_amount(quote.total)}** (más la cuota de autónomo)"
            )
            if self.screen == CONFIRMAR:
                lines.append("\n**¿Lo montas?** La fianza vuelve al cerrar; lo demás, no.")
        elif status is not None and self.screen == CADENA:
            lines.extend(self._cadena_lines())
        elif status is not None and self.screen == NAVE:
            lines.extend(self._nave_lines())
        elif status is not None and self.screen == VENTA:
            lines.extend(self._venta_lines())
        elif status is not None:
            business = status.business
            sector = business.sector_info
            if self.screen == TURNO and self.turn is not None:
                lines.extend(self._turn_text())
            elif self.screen == CIERRE and self.result is not None:
                lines.extend(self._result_text())
            else:
                lines.append(
                    f"## {sector.emoji} {business.name} · {business.barrio_info.name} · "
                    f"{_stars_text(business.stars)}"
                )
                if self.screen == ALMACEN:
                    lines.extend(self._stock_lines(business))
                elif self.screen == CUENTAS:
                    totals = business.week_totals
                    lines.append(
                        "Esta semana: "
                        + (" · ".join(f"{k} {signed(v)}" for k, v in totals.items()) or "nada aún")
                    )
                elif self.screen == CERRAR:
                    lines.extend(self._close_lines(business))
                elif self.screen == MEJORAS:
                    lines.extend(self._mejoras_lines(business))
                elif self.screen == RESENAS:
                    lines.extend(self._resenas_lines(business))
                elif self.screen == TRASLADO:
                    lines.extend(self._traslado_lines(business))
                elif self.screen == SOCIEDAD and self.company is not None:
                    lines.extend(self._sociedad_lines(self.company))
                elif self.screen == PLANTILLA:
                    lines.extend(self._plantilla_lines(business))
                elif self.screen == CANDIDATOS:
                    lines.extend(self._candidatos_lines(business))
                elif self.screen == DESPIDO:
                    lines.extend(self._despido_lines(business))
                else:
                    bill = nightly_bill(business)
                    lines.append(
                        f"Caja de hoy **{signed(business.sales_today)}** · "
                        f"{business.clients_today} clientes · {business.turns_today} turnos · "
                        "franjas libres: "
                        f"{status.agenda.free} (+{status.agenda.night_free} 🌙)"
                    )
                    lines.append(
                        f"-# Esta noche: alquiler y suministros {format_amount(bill.total)}"
                        + (
                            f" · debes {format_amount(business.rent_debt)} del local"
                            if business.rent_debt
                            else ""
                        )
                    )
                    extras = [size_text(business)]
                    average, count = rating(business)
                    if count:
                        extras.append(
                            f"⭐ {average:.1f}".replace(".", ",")
                            + f" en {count} reseñas"
                            + (" · recomendado" if recommended(business) else "")
                        )
                    if campaign := campaign_state(business, self._today()):
                        extras.append(f"📣 {campaign['name']} ({campaign['left']})")
                    if business.staff:
                        done = business.staff_today
                        extras.append(
                            f"👥 {len(business.staff)} en plantilla · hoy "
                            f"{done.get('turns', 0)} turnos, {signed(done.get('sales', 0))}"
                        )
                    lines.append("-# " + " · ".join(extras))
                    if blocker := status.blocker:
                        lines.append(f"-# ⚠️ {blocker}")
        notes, self.notes = self.notes, []
        lines.extend(notes)
        return "\n".join(lines)[:3900]

    def _cadena_lines(self) -> list[str]:
        lines = [f"## 🗺️ Tu cadena · {len(self.chain)} de {MAX_LOCALES} locales"]
        for business in self.chain:
            mark = "▶ " if business.id == self.biz else ""
            boss = " · 🧑‍💼 gerente" if staff.manager(business) is not None else ""
            lines.append(
                f"{mark}{business.sector_info.emoji} **{business.name}** · "
                f"{business.barrio_info.name} ({ISLANDS[business.barrio_info.island]}) · "
                f"{_stars_text(business.stars)} · {signed(business.sales_today)} hoy{boss}"
            )
        if self.nave is not None:
            nave = self.nave
            lines.append(
                f"🏭 Nave {nave.size_info.name.lower()} en {nave.barrio_info.name}: "
                f"{chain.used_lots(nave)} de {nave.size_info.lots} lotes"
                + (f" · {len(nave.orders)} contenedores en camino" if nave.orders else "")
            )
        else:
            lines.append("-# 🏭 Sin nave: con una, importas contenedores más baratos.")
        return lines

    def _nave_lines(self) -> list[str]:
        nave = self.nave
        if nave is None:
            barrio = BARRIO_BY_KEY[self._nave_barrio()]
            size = chain.NAVE_SIZES[self.nave_pick["size"]]
            quote = chain.nave_quote(barrio, self.nave_pick["size"])
            return [
                f"## 🏭 Alquilar una nave en {barrio.name}",
                f"Nave {size.name.lower()}: {size.m2} m², caben **{size.lots} lotes**. Alquiler "
                f"**{format_amount(quote.rent)}** cada noche, más IGIC y la luz.",
                f"Abrirla: tasa {format_amount(quote.license)} y fianza (2 meses) "
                f"{format_amount(quote.deposit)}.",
                "-# Reparte a tus locales de su isla. Un contenedor son "
                f"{chain.CONTAINER_LOTS} lotes de un producto y llega en "
                f"{chain.IMPORT_NIGHTS} noches.",
            ]
        lines = [
            f"## 🏭 Nave {nave.size_info.name.lower()} · {nave.barrio_info.name} · "
            f"{chain.used_lots(nave)} de {nave.size_info.lots} lotes"
        ]
        stock = [
            f"{PRODUCT_EMOJI.get(k, '📦')} {item.name} {units}"
            for k, units in sorted(nave.stock.items())
            if (item := chain.product_by_key(k)) is not None and units > 0
        ]
        lines.append("Dentro: " + (" · ".join(stock) if stock else "nada aún"))
        for order in nave.orders:
            item = chain.product_by_key(order["product"])
            lines.append(
                f"🚢 En camino: {order['units']} de {item.name.lower() if item else '?'} "
                "(llega a la mañana siguiente a su noche)"
            )
        product = chain.product_by_key(self.nave_pick["product"])
        if product is not None:
            quote = chain.import_quote(product)
            lines.append(
                f"📦 Contenedor de {product.name.lower()} ({quote.units} unidades): fábrica "
                f"{format_amount(quote.goods)} + flete {format_amount(quote.freight)} + AIEM "
                f"{format_amount(quote.aiem)} + IGIC {format_amount(quote.igic)} + aduanas "
                f"{format_amount(quote.customs + quote.customs_igic)} = "
                f"**{format_amount(quote.total)}** (al mayorista, "
                f"{format_amount(quote.wholesale)}: {signed(quote.saving)})"
            )
        if self.leaving_nave:
            lines.append(
                "⚠️ **¿Dejas la nave?** El mayorista te recompra lo de dentro a la mitad y la "
                "fianza vuelve. Pulsa otra vez para confirmarlo."
            )
        return lines

    def _venta_lines(self) -> list[str]:
        offer = self.sale
        if offer is None:
            return ["## 💼 Vender la sociedad", "Solo se venden sociedades."]
        lines = [
            "## 💼 Vender la sociedad",
            "Resultados de las últimas semanas: "
            + (" · ".join(signed(r) for r in offer.results) or "ninguno"),
            f"Su media por cinco: {format_amount(offer.capitalized)} · Patrimonio neto: "
            f"{signed(offer.equity)}",
            f"💶 **El comprador paga {format_amount(offer.price)}** (el mayor de los dos, art. "
            f"37.1.b LIRPF) y te devuelve {format_amount(offer.loan)} de tu préstamo.",
            f"-# Ganancia: {signed(offer.gain)} sobre lo que metiste "
            f"({format_amount(offer.basis)}); paga el 19 % ({TAX_COLLECTOR}).",
        ]
        if offer.blocker:
            lines.append(f"⚠️ {offer.blocker}")
        return lines

    @staticmethod
    def _stock_lines(business: Business) -> list[str]:
        out = []
        for product in business.sector_info.products:
            units = business.stock.get(product.key, 0)
            level = business.level(product.key)
            out.append(
                f"{PRODUCT_EMOJI.get(product.key, '📦')} {product.name}: **{units}** · cuesta "
                f"{format_amount(product.cost)} · se vende a "
                f"{format_amount(sale_price(product, level.key))}"
                + (
                    f" ({level.name.split(' (')[0].lower()})"
                    if product.key in business.prices
                    else ""
                )
                + (f" · caduca un {round(product.spoil * 100)} %" if product.spoil else "")
            )
        return out

    def _close_lines(self, business: Business) -> list[str]:
        offer = self.service.offer(business)
        refund = max(0, business.deposit - business.rent_debt)
        lines = [f"🚪 **¿Cierras {business.name}?** Vuelven {format_amount(refund)} de la fianza."]
        if offer.can_traspasar:
            lines.append(
                f"🤝 **Traspaso:** te dan {format_amount(offer.traspaso)}: el stock "
                f"({format_amount(offer.stock)}), las mejoras y la obra "
                f"({format_amount(offer.book)}) y la clientela ({format_amount(offer.goodwill)})."
                " La clientela tributa al 19 % (ganancia patrimonial)."
            )
        else:
            lines.append(
                "-# 🤝 Nadie quiere traspasarlo: hace falta una semana abierto, 3 estrellas y un "
                "local alquilado."
            )
        lines.append(
            f"🧹 **Liquidar:** el mayorista te recompra el stock por "
            f"{format_amount(offer.liquidation_stock)} y vendes las mejoras de segunda mano por "
            f"{format_amount(offer.liquidation_equipment)}."
        )
        return lines

    def _mejoras_lines(self, business: Business) -> list[str]:
        lines = ["🛠️ **Mejoras del local** · se amortizan cada noche"]
        for item in equipment_for(business.sector):
            mark = "✅" if item.key in business.equipment else "▫️"
            lines.append(f"{mark} {item.name}: {item.effect()} · {format_amount(item.cost)} + IGIC")
        active = campaign_state(business, self._today())
        lines.append(
            "📣 **Publicidad** · "
            + (f"en marcha: {active['name']} ({active['left']})" if active else "sin campaña")
        )
        for campaign in CAMPAIGNS:
            lines.append(
                f"- {campaign.name}: +{round(campaign.boost * 100)} % de clientes, "
                f"{campaign.days} días · {format_amount(campaign.cost)} + IGIC"
            )
        return lines

    @staticmethod
    def _resenas_lines(business: Business) -> list[str]:
        average, count = rating(business)
        if not count:
            return ["⭐ Aún no hay reseñas: las dejan algunos clientes al acabar cada turno."]
        lines = [
            f"⭐ **{average:.1f}**".replace(".", ",")
            + f" de media en {count} reseñas"
            + (" · 🏅 Recomendado" if recommended(business) else "")
        ]
        for _day, who, n, line in reversed(business.reviews[-5:]):
            text = REVIEW_LINES[n][line % len(REVIEW_LINES[n])]
            lines.append(f"{'★' * n}{'☆' * (5 - n)} **{who}**: {text}")
        return lines

    def _sociedad_lines(self, view: CompanyView) -> list[str]:
        business = view.business
        if not business.is_company:
            fees = sl.constitution_fees(self.capital)
            lines = [
                "🏢 **Convertirlo en sociedad limitada.** Tributa en Sociedades (19 % y 21 %), "
                "no en tu IRPF; cobras con sueldo o dividendos, y si se hunde solo pierdes lo "
                "que hay dentro.",
                f"Capital: **{format_amount(self.capital)}** · notaría y registro "
                f"{format_amount(fees)} + IGIC · gestoría {format_amount(sl.BOOKKEEPING_NIGHT)} "
                "cada noche.",
            ]
            if view.blocked:
                lines.append(f"⛔ {view.blocked.text} ({view.blocked.law})")
            return lines
        info = business.company
        lines = [
            f"🏦 Caja **{format_amount(view.cash)}** · capital {format_amount(info['capital'])} "
            f"· reserva legal {format_amount(info['reserve'])} · disponible "
            f"{signed(info['retained'])}",
            f"Préstamo tuyo {format_amount(info['loan'])} · patrimonio neto "
            f"{signed(view.equity)} · se puede repartir {format_amount(view.dividend_room)} · "
            f"retención del sueldo {round(view.retention * 100)} %",
        ]
        if view.bank:
            lines.append(
                f"🏦 Debe {format_amount(view.bank)} al banco (lo avalas tú): míralo en 🏦 Banco."
            )
        if last := info.get("last"):
            lines.append(
                f"-# Último Sociedades: base {signed(last['base'])}, "
                f"{round(last['rate'] * 100, 1)} % → {format_amount(last['tax'])}".replace(
                    ".0 %", " %"
                )
            )
        if view.dissolution:
            lines.append("⚠️ Causa de disolución: aporta dinero o responderás de sus deudas.")
        if view.blocked:
            lines.append(f"⛔ No puedes cobrar de administrador: {view.blocked.text}")
        share = round(info.get("ric_share", 0.0) * 100)
        pending = sl.ric_pending(info)
        lines.append(
            f"🌴 RIC: dotas el {share} % de la base cada domingo · por invertir "
            f"{format_amount(pending)} · reserva indisponible {format_amount(sl.ric_locked(info))}"
        )
        lines.append(f"🏝️ {zec_text(business)}")
        return lines

    def _plantilla_lines(self, business: Business) -> list[str]:
        today = self._today()
        pay = staff.payroll(business, today)
        lines = [
            f"👥 **Plantilla** · {len(staff.seated(business))} de "
            f"{staff.cap(business.size)} puestos · "
            f"nóminas de esta noche {format_amount(pay.cost + pay.black)}"
        ]
        if not business.staff:
            lines.append("Aún no tienes a nadie. Mira los candidatos de hoy en 👥 Candidatos.")
        for employee in business.staff:
            skills = " · ".join(
                f"{staff.SKILL_NAMES[k][:3]} {round(staff.skill(employee, k))}"
                for k in staff.SKILLS
            )
            mark = "▶️ " if employee["id"] == self.employee else ""
            role = staff.role_of(employee)
            lines.append(
                f"{mark}**{employee['name']}** ({employee['age']}) · "
                f"{role.emoji} {role.name.lower()} · {CONTRACT_NAMES[employee['contract']]} · "
                + horario_text(employee)
                + f" · {format_amount(employee['wage'])} la noche · humor "
                f"{round(employee['humor'])} · "
                f"{skills}"
            )
            if role.effects and employee["id"] == self.employee:
                lines.append(
                    f"-# {role.emoji} {staff.effect_text(role, staff.strength(employee))}."
                )
            if (planned := employee.get("next")) and employee["id"] == self.employee:
                new = staff.HORARIO_BY_KEY.get(staff.horario_key(planned["franjas"]))
                if new is not None:
                    lines.append(
                        f"-# 🕒 Desde mañana: {new.label.lower()}, "
                        f"{format_amount(planned['wage'])} la noche."
                    )
        if business.wage_debt:
            lines.append(f"⚠️ Les debes {format_amount(business.wage_debt)}: se paga lo primero.")
        return lines

    def _candidatos_lines(self, business: Business) -> list[str]:
        lines = ["👥 **Candidatos de hoy** · mañana llegan otros"]
        candidates = self.service.candidates(business)
        if not candidates:
            lines.append("Ya has visto a todos los de hoy.")
        for c in candidates:
            traits = ", ".join(staff.TRAIT_BY_KEY[t].name.lower() for t in c["traits"])
            skills = " · ".join(
                f"{staff.SKILL_NAMES[k][:3]} {round(c['skills'][k])}" for k in staff.SKILLS
            )
            mark = "▶️ " if c["key"] == self.candidate else ""
            lines.append(
                f"{mark}**{c['name']}** ({c['age']}) · "
                f"{staff.PROFILE_BY_KEY[c['profile']].name} · {traits} · {skills} · pide "
                f"{format_amount(c['ask'])} la noche"
                + (" · solo media jornada" if c["part_time"] else "")
            )
        candidate = self._candidate(business)
        offer = self._offer(business)
        if candidate is not None and offer is not None:
            role = staff.ROLE_BY_KEY[offer["role"]]
            schedule = offer_horario(offer)
            wage = staff.role_ask(
                candidate["skills"], candidate["traits"], schedule.jornada, role.key
            )
            when = "jornada completa" if not role.schedule else schedule.label.lower()
            lines.append(
                f"✍️ Oferta: {role.emoji} {role.name.lower()}, "
                f"{CONTRACT_NAMES[offer['contract']].lower()} · {when} · "
                f"{format_amount(wage)} brutos la noche"
            )
            lines.append(f"-# {role.text}")
        return lines

    def _despido_lines(self, business: Business) -> list[str]:
        employee = self._chosen(business)
        if employee is None:
            return ["No hay nadie a quien despedir."]
        lines = [f"🚪 **¿Despides a {employee['name']}?**"]
        for option in self.service.dismissal_options(business, employee):
            mark = "✅" if option.allowed else "⛔"
            law = f" ({option.law})" if option.law else ""
            lines.append(
                f"{mark} **{option.name}** · {format_amount(option.amount)}{law}: {option.why}"
            )
        if employee["unpaid"]:
            lines.append(
                f"-# Lo que se le debe ({format_amount(employee['unpaid'])}) se le paga igual."
            )
        return lines

    def _traslado_lines(self, business: Business) -> list[str]:
        target = self._move_target()
        assert target is not None
        barrio, size, quote, own = target
        place = BARRIO_BY_KEY[barrio]
        lines = [
            f"🚚 **Traslado** a {place.name}, local {SIZES[size].name.lower()}"
            + (" (tuyo)" if own else ""),
            f"🧾 Tasa {format_amount(quote.opening.license)} · acondicionamiento "
            f"{format_amount(quote.opening.setup)} + IGIC · fianza nueva "
            f"{format_amount(quote.opening.deposit)} · vuelve la de ahora: "
            f"{format_amount(quote.refund)}",
            (
                "🔑 Sin alquiler: el local es tuyo (sus recibos siguen en `pisos`)."
                if own
                else f"🔑 Alquiler: {format_amount(quote.opening.rent)} cada noche, más IGIC."
            ),
        ]
        if not quote.same_barrio:
            lines.append("-# Cambias de barrio: la reputación y la fidelidad bajan a la mitad.")
        if not self.own_locals:
            lines.append(
                "-# Si compras un local entero en `pisos`, podrás traerte aquí el negocio."
            )
        return lines

    def _turn_text(self) -> list[str]:
        assert self.turn is not None
        game = self.turn.game
        client = game.current
        kind = game.sector.game
        lines = [f"-# 🎮 {HOW_TO[kind]}"]
        if game.last:
            lines.append(f"**{game.last}**")
        if client is None:
            return lines
        who = f"**{client.regular}** (habitual)" if client.regular else "Un cliente"
        if game.sector.game == "comandas":
            if client.usual:
                lines.append(f"{who}: «Lo de siempre». Tienes que acordarte.")
            else:
                order = ", ".join(
                    f"{n} {game.sector.product(k).name.lower()}" for k, n in client.order.items()
                )  # type: ignore[union-attr]
                lines.append(f"{who} pide: {order}")
            tray = game.tray_counts()
            lines.append(
                "🍽️ Bandeja: "
                + (
                    ", ".join(f"{n} {game.sector.product(k).name.lower()}" for k, n in tray.items())
                    or "vacía"
                )  # type: ignore[union-attr]
            )
        elif game.sector.game == "caja":
            items = ", ".join(
                f"{n} {game.sector.product(k).name.lower()}" for k, n in client.order.items()
            )  # type: ignore[union-attr]
            given = sum(game.given)
            lines.append(f"{who} lleva {items}.")
            lines.append(
                f"🧾 Total **{format_amount(game.total())}** · paga con "
                f"**{format_amount(game.paid)}** · "
                f"cambio dado: **{format_amount(given)}**"
            )
        elif kind in GAME_TEXT:
            lines.extend(GAME_TEXT[kind](self, game, client, who))
        else:
            lines.append(f"{who}: «{client.symptom}»")
            if game.step == "presupuesto":
                product = game.sector.product(game.diagnosis or "")
                lines.append(
                    f"🔧 Diagnóstico: {product.name.lower() if product else '?'}. "
                    "Elige el presupuesto."
                )
        cash = sum(o.revenue + o.tip for o in game.outcomes)
        lines.append(
            f"⏳ Se va <t:{int(game.client_deadline) + 1}:R> · caja del turno {signed(cash)} · "
            f"cierras <t:{int(game.deadline) + 1}:R>"
        )
        return lines

    def _result_text(self) -> list[str]:
        result = self.result
        assert result is not None
        summary = result.summary
        lines = [
            f"## 🧾 {summary.grade} · {summary.served} de {len(summary.outcomes)} clientes · "
            f"{signed(summary.sales)}",
        ]
        if result.withheld:
            lines.append(
                f"-# 🐶 IRPF a cuenta: −{format_amount(result.withheld)} ({TAX_COLLECTOR})"
            )
        if result.garnished:
            lines.append(
                f"-# ⚖️ Embargo por tu deuda con Hacienda: −{format_amount(result.garnished)}"
            )
        if result.bank_garnished:
            lines.append(
                "-# ⚖️ Embargo del juzgado por tu deuda con el banco (art. 607 LEC): "
                f"−{format_amount(result.bank_garnished)}"
            )
        lines.append(
            f"-# Estrellas: {_stars_text(result.stars_before)} → "
            f"{_stars_text(result.business.stars)}"
        )
        lines.extend(f"- {line}" for line in turn_lines(result))
        for who, n, line in result.reviews[:3]:
            text = REVIEW_LINES[n][line % len(REVIEW_LINES[n])]
            lines.append(f"-# {'★' * n}{'☆' * (5 - n)} {who}: «{text}»")
        return lines

    # -- Componentes ------------------------------------------------------------------

    def _button(
        self,
        label: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        name: str,
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        disabled: bool = False,
    ) -> ui.Button:
        button: ui.Button = ui.Button(
            label=label[:80], style=style, disabled=disabled, custom_id=f"negocio-panel:{name}"
        )
        button.callback = callback  # type: ignore[method-assign]
        return button

    def _game_button(
        self,
        label: str,
        action: str,
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        disabled: bool = False,
        emoji: str | None = None,
    ) -> ui.Button:
        assert self.turn is not None
        button: ui.Button = ui.Button(
            label=label[:80],
            style=style,
            disabled=disabled,
            emoji=emoji,
            custom_id=game_id(self.token, self.turn.game.round, action),
        )
        setattr(button, LISTENER_ITEM, True)
        return button

    def _select(
        self,
        placeholder: str,
        options: list[discord.SelectOption],
        name: str,
        callback: Callable[[discord.Interaction, str], Awaitable[None]],
    ) -> ui.Select:
        select: ui.Select = ui.Select(
            placeholder=placeholder[:150], options=options[:25], custom_id=f"negocio-panel:{name}"
        )

        async def run(interaction: discord.Interaction) -> None:
            await callback(interaction, select.values[0])

        select.callback = run  # type: ignore[method-assign]
        return select

    def rebuild(self, has_image: bool) -> None:
        """Monta la imagen, el texto y los controles de la pantalla de ahora."""
        self.clear_items()
        self._has_image = has_image
        color = COLOR_GAME if self.screen == TURNO else COLOR
        container = ui.Container(accent_colour=color)
        if has_image:
            container.add_item(
                ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{PNG_NAME}"))
            )
        container.add_item(ui.TextDisplay(self.text() or "…"))
        self.add_item(container)
        for row in self._rows():
            if row.children:
                self.add_item(row)

    def _rows(self) -> list[ui.ActionRow]:
        green, blue, red = (
            discord.ButtonStyle.success,
            discord.ButtonStyle.primary,
            discord.ButtonStyle.danger,
        )
        home = self._button("🏠 Inicio", self._home, "inicio")
        back = self._button("⬅️ Volver", self._portada, "volver")
        if self.screen == MONTAR:
            options = [
                discord.SelectOption(
                    label=s.name, value=s.key, emoji=s.emoji, description=s.blurb[:100]
                )
                for s in SECTORS
            ]
            bottom = ui.ActionRow(home)
            if self.chain:
                bottom = ui.ActionRow(self._button("⬅️ Cadena", self._cadena, "a-cadena"), home)
            return [
                ui.ActionRow(
                    self._select("Elige qué negocio montas", options, "sector", self._pick_sector)
                ),
                bottom,
            ]
        if self.screen == PREVIA:
            return self._draft_rows(home)
        if self.screen == CONFIRMAR:
            total = opening_quote(
                SECTOR_BY_KEY[self.draft["sector"]], BARRIO_BY_KEY[self.draft["barrio"]]
            ).total
            return [
                ui.ActionRow(
                    self._button(
                        f"✅ Montarlo · {format_amount(total)}",
                        self._open,
                        "abrir-negocio",
                        style=green,
                    ),
                    self._button("⬅️ Atrás", self._back_to_draft, "atras"),
                )
            ]
        if self.screen == TURNO:
            return self._game_rows()
        if self.status is None:
            return [ui.ActionRow(home)]
        business = self.status.business
        if self.screen == CADENA:
            return self._cadena_rows(back, home)
        if self.screen == NAVE:
            return self._nave_rows()
        if self.screen == VENTA:
            offer = self.sale
            sell = self._button(
                f"💼 Vender · {format_amount(offer.price if offer else 0)}",
                self._sell,
                "vender-si",
                style=green,
                disabled=offer is None or offer.blocker is not None,
            )
            return [ui.ActionRow(sell, self._button("⬅️ Sociedad", self._sociedad, "a-sociedad"))]
        if self.screen == ALMACEN:
            options = [
                discord.SelectOption(
                    label=f"{p.name} ({business.stock.get(p.key, 0)})",
                    value=p.key,
                    emoji=PRODUCT_EMOJI.get(p.key),
                    description=f"Lote de {p.pack}: {format_amount(p.cost * p.pack)} + IGIC",
                    default=p.key == self.product,
                )
                for p in business.sector_info.products
            ]
            rows = [
                ui.ActionRow(
                    self._select("Elige qué comprar", options, "producto", self._pick_product)
                )
            ]
            if self.product:
                current = business.prices.get(self.product, "")
                levels = [
                    discord.SelectOption(
                        label="Como el resto", value="general", default=not current
                    ),
                    *(
                        discord.SelectOption(label=p.name, value=p.key, default=p.key == current)
                        for p in PRICE_LEVELS
                    ),
                ]
                rows.append(
                    ui.ActionRow(
                        self._select(
                            "🏷️ Precio de este producto", levels, "precio-producto",
                            self._set_product_price,
                        )
                    )
                )  # fmt: skip
                buy = ui.ActionRow()
                for lots in (1, 5, MAX_LOTS // 2):
                    buy.add_item(
                        self._button(
                            f"🛒 {lots} lote{'s' if lots > 1 else ''}",
                            self._buy(lots),
                            f"comprar:{lots}",
                            style=green,
                        )
                    )
                rows.append(buy)
                if fetch := self._fetch_row(business):
                    rows.append(fetch)
            rows.append(ui.ActionRow(back, home))
            return rows
        if self.screen == CUENTAS:
            return [ui.ActionRow(back, home)]
        if self.screen == RESENAS:
            return [ui.ActionRow(back, home)]
        if self.screen == MEJORAS:
            return self._mejoras_rows(business, back, home)
        if self.screen == TRASLADO:
            return self._traslado_rows(business, back)
        if self.screen == SOCIEDAD:
            return self._sociedad_rows(back, home)
        if self.screen == PLANTILLA:
            return self._plantilla_rows(business, back, home)
        if self.screen == CANDIDATOS:
            return self._candidatos_rows(business, home)
        if self.screen == DESPIDO:
            return self._despido_rows(business)
        if self.screen == ROTULO:
            return [*self._style_rows(), ui.ActionRow(
                self._button("✏️ Nombre", self._rename, "nombre"),
                self._button("✅ Guardar el rótulo", self._save_style, "guardar", style=green),
                back,
            )]  # fmt: skip
        if self.screen == CERRAR:
            offer = self.service.offer(business)
            row = ui.ActionRow()
            if offer.can_traspasar:
                row.add_item(
                    self._button(
                        f"🤝 Traspasar · {format_amount(offer.traspaso)}",
                        self._close_traspaso,
                        "traspasar",
                        style=green,
                    )
                )
            row.add_item(
                self._button(
                    f"🚪 Cerrar y liquidar · {format_amount(offer.liquidation)}",
                    self._close,
                    "cerrar-si",
                    style=red,
                )
            )
            row.add_item(self._button("⬅️ No", self._portada, "cerrar-no"))
            return [row]
        if self.screen == CIERRE:
            return [
                ui.ActionRow(
                    self._button(
                        "🔑 Otro turno",
                        self._start,
                        "otro",
                        style=green,
                        disabled=self.status.blocker is not None,
                    ),
                    self._button("🏪 Portada", self._portada, "portada"),
                    home,
                )
            ]
        # Portada.
        main = ui.ActionRow(
            self._button(
                "🔑 Abrir · 1 franja",
                self._start,
                "turno",
                style=green,
                disabled=self.status.blocker is not None,
            ),
            self._button("📦 Almacén", self._almacen, "almacen", style=blue),
            self._button("🛠️ Mejoras", self._mejoras, "mejoras", style=blue),
            self._button("📊 Cuentas", self._cuentas, "cuentas"),
            self._button("⭐ Reseñas", self._resenas, "resenas"),
        )
        more = ui.ActionRow(
            self._button("🎨 Rótulo", self._rotulo, "rotulo"),
            self._button("🚚 Traslado", self._traslado, "traslado"),
            self._button("🏢 Sociedad", self._sociedad, "sociedad"),
            self._button("🏦 Banco", self._bank, "banco"),
            self._button("🚪 Cerrar", self._confirm_close, "cerrar"),
        )
        prices = [
            discord.SelectOption(label=p.name, value=p.key, default=p.key == business.price)
            for p in PRICE_LEVELS
        ]
        price_row = ui.ActionRow(self._select("🏷️ Precios", prices, "precio", self._set_price))
        team = self._button("👥 Plantilla", self._plantilla, "plantilla", style=blue)
        chain_ = self._button("🗺️ Cadena", self._cadena, "cadena", style=blue)
        return [main, more, price_row, ui.ActionRow(team, chain_, home)]

    def _fetch_row(self, business: Business) -> ui.ActionRow | None:
        """🏭 Traer lotes de la nave al almacén, si la nave es de su isla y tiene de eso."""
        nave = self.nave
        item = business.sector_info.product(self.product)
        if nave is None or item is None or nave.island != business.barrio_info.island:
            return None
        units = nave.stock.get(item.key, 0)
        if units <= 0:
            return None
        whole = -(-units // item.pack)
        row = ui.ActionRow()
        for lots in sorted({1, min(5, whole), whole}):
            label = (
                f"🏭 Todo de la nave ({whole})"
                if lots == whole and lots > 1
                else (f"🏭 {lots} lote{'s' if lots > 1 else ''} de la nave")
            )
            row.add_item(self._button(label, self._fetch(lots), f"nave-traer:{lots}"))
        return row

    def _cadena_rows(self, back: ui.Button, home: ui.Button) -> list[ui.ActionRow]:
        green, blue = discord.ButtonStyle.success, discord.ButtonStyle.primary
        rows = []
        if len(self.chain) > 1:
            options = [
                discord.SelectOption(
                    label=f"{b.name} · {b.barrio_info.name}"[:100],
                    value=str(b.id),
                    emoji=b.sector_info.emoji,
                    description=f"{b.sector_info.name} · {signed(b.sales_today)} hoy"[:100],
                    default=b.id == self.biz,
                )
                for b in self.chain
            ]
            rows.append(
                ui.ActionRow(self._select("🏪 Ir a un local", options, "local", self._pick_local))
            )
        rows.append(
            ui.ActionRow(
                self._button(
                    "➕ Otro local", self._new_local, "otro-local", style=green,
                    disabled=len(self.chain) >= MAX_LOCALES or self.turn is not None,
                ),
                self._button("🏭 Nave", self._nave, "nave", style=blue),
                back,
                home,
            )
        )  # fmt: skip
        return rows

    def _nave_rows(self) -> list[ui.ActionRow]:
        green, red = discord.ButtonStyle.success, discord.ButtonStyle.danger
        to_chain = self._button("⬅️ Cadena", self._cadena, "a-cadena")
        if self.nave is None:
            barrio = self._nave_barrio()
            own = self.nave_pick["own"]
            barrios = [
                discord.SelectOption(
                    label=f"{b.name} ({ISLANDS[b.island]})"[:100],
                    value=b.key,
                    description=(
                        f"{THEMES[b.theme]} · la pequeña, "
                        f"{format_amount(chain.nave_rent(b, chain.NAVE_SIZES[0]))}/día"
                    )[:100],
                    default=b.key == barrio and not own,
                )
                for b in BARRIOS
            ]
            sizes = [
                discord.SelectOption(
                    label=f"Nave {size.name.lower()} · {size.m2} m²",
                    value=str(i),
                    description=f"Caben {size.lots} lotes",
                    default=i == self.nave_pick["size"] and not own,
                )
                for i, size in enumerate(chain.NAVE_SIZES)
            ]
            rows = [
                ui.ActionRow(
                    self._select(
                        "📍 Barrio de la nave", barrios, "nave-barrio", self._pick_nave_barrio
                    )
                ),
                ui.ActionRow(self._select("📐 Tamaño", sizes, "nave-tamano", self._pick_nave_size)),
            ]
            fit = [o for o in self.own_naves if o.fits]
            if fit:
                naves = [
                    discord.SelectOption(
                        label=o.listing.title[:100],
                        value=o.listing.key,
                        description=(
                            f"{BARRIO_BY_KEY[o.listing.barrio].name} · {o.listing.m2} m² · nave "
                            f"{chain.NAVE_SIZES[o.size or 0].name.lower()}"
                        )[:100],
                        default=o.listing.key == own,
                    )
                    for o in fit
                ]
                rows.append(
                    ui.ActionRow(
                        self._select(
                            "🏠 Una nave tuya de pisos", naves, "nave-propia", self._pick_own_nave
                        )
                    )
                )
            quote = (
                chain.nave_quote(BARRIO_BY_KEY[barrio], 0, own=True)
                if own
                else chain.nave_quote(BARRIO_BY_KEY[barrio], self.nave_pick["size"])
            )
            rows.append(
                ui.ActionRow(
                    self._button(
                        f"🏭 Abrir la nave · {format_amount(quote.total)}",
                        self._open_nave,
                        "nave-abrir",
                        style=green,
                    ),
                    to_chain,
                )
            )
            return rows
        options = []
        for key in self._nave_products():
            item = chain.product_by_key(key)
            if item is None:
                continue
            quote = chain.import_quote(item)
            options.append(
                discord.SelectOption(
                    label=item.name[:100],
                    value=key,
                    emoji=PRODUCT_EMOJI.get(key),
                    description=(
                        "Caduca: se compra al mayorista"
                        if not chain.can_import(item)
                        else f"Contenedor {format_amount(quote.total)} · {signed(quote.saving)}"
                    )[:100],
                    default=key == self.nave_pick["product"],
                )
            )
        rows = []
        if options:
            rows.append(
                ui.ActionRow(
                    self._select(
                        "🚢 Elige qué importar", options, "nave-producto", self._pick_import
                    )
                )
            )
        product = chain.product_by_key(self.nave_pick["product"])
        state = self._nave_screen().get("quote") or {}
        rows.append(
            ui.ActionRow(
                self._button(
                    f"🚢 Importar · {state.get('total', '…')}",
                    self._import,
                    "nave-importar",
                    style=green,
                    disabled=product is None or bool(state.get("blocked")),
                ),
                self._button(
                    "⚠️ Sí, dejar la nave" if self.leaving_nave else "🚪 Dejar la nave",
                    self._leave_nave,
                    "nave-dejar",
                    style=red,
                    disabled=bool(self.nave.orders),
                ),
                to_chain,
            )
        )
        return rows

    def _plantilla_rows(
        self, business: Business, back: ui.Button, home: ui.Button
    ) -> list[ui.ActionRow]:
        green, blue, red = (
            discord.ButtonStyle.success,
            discord.ButtonStyle.primary,
            discord.ButtonStyle.danger,
        )
        rows = []
        employee = self._chosen(business)
        if business.staff:
            options = [
                discord.SelectOption(
                    label=f"{e['name']} · {staff.role_of(e).name}"[:100],
                    value=str(e["id"]),
                    emoji=staff.role_of(e).emoji,
                    description=(
                        f"{CONTRACT_NAMES[e['contract']]} · {horario_text(e, short=True)} · "
                        f"{format_amount(e['wage'])}"
                    )[:100],
                    default=e["id"] == self.employee,
                )
                for e in business.staff
            ]
            rows.append(
                ui.ActionRow(
                    self._select("Elige a alguien", options, "empleado", self._pick_employee)
                )
            )
        if employee is not None:
            raises = [
                discord.SelectOption(
                    label=(
                        f"+{round(share * 100)} %: "
                        f"{format_amount(int(round(employee['wage'] * (1 + share), -1)))} la noche"
                    ),
                    value=str(share),
                )
                for share in staff.RAISES
            ]
            rows.append(
                ui.ActionRow(self._select("💶 Subirle el sueldo", raises, "subida", self._raise))
            )
            if not employee["course"]:
                price = format_amount(staff.COURSE_COST + igic(staff.COURSE_COST))
                courses = [
                    discord.SelectOption(
                        label=(
                            f"Curso de {staff.SKILL_NAMES[k].lower()} "
                            f"(ahora {round(employee['skills'][k])})"
                        ),
                        value=k,
                        description=f"+{staff.COURSE_POINTS} la noche que viene · {price} con IGIC",
                    )
                    for k in staff.SKILLS
                ]
                rows.append(
                    ui.ActionRow(
                        self._select("🎓 Pagarle un curso", courses, "curso", self._course)
                    )
                )
            if (hours := self._horario_options(employee)) is not None:
                rows.append(
                    ui.ActionRow(
                        self._select(
                            "🕒 Cambiarle el horario (desde mañana)", hours, "horario",
                            self._reschedule,
                        )
                    )
                )  # fmt: skip
            actions = ui.ActionRow()
            if employee["contract"] == "temporal":
                actions.add_item(
                    self._button("📄 Hacerle fijo", self._permanent, "fijo", style=blue)
                )
            actions.add_item(self._button("🚪 Despedir", self._to_dismiss, "despedir", style=red))
            rows.append(actions)
        room = any(staff.hire_problem(business, r.key) is None for r in staff.roles_for(business))
        rows.append(
            ui.ActionRow(
                self._button(
                    "👥 Candidatos", self._candidatos, "candidatos", style=green, disabled=not room
                ),
                back,
                home,
            )
        )
        return rows

    def _horario_options(self, employee: dict[str, Any]) -> list[discord.SelectOption] | None:
        """Los horarios a los que se le puede cambiar (`None` al gerente, que no tiene), con
        su sueldo y lo que pasa: con las mismas horas le baja algo el humor; con menos, tiene
        que aceptar (art. 12.4.e ET)."""
        current = staff.horario_of(employee)
        if current is None:
            return None
        part_time = any(staff.TRAIT_BY_KEY[t].part_time for t in employee["traits"])
        today = self._today()
        options = []
        for schedule in staff.horarios_for(part_time):
            if schedule.key == current.key:
                continue
            try:
                plan = staff.reschedule(employee, schedule.key, today)
            except ValueError:
                tail = "no acepta trabajar menos horas"
            else:
                tail = {
                    "igual": "le baja algo el humor",
                    "mas": "cobra más",
                    "menos": "cobra menos",
                }[plan.kind]
                tail = f"{format_amount(plan.wage)} la noche · {tail}"
            options.append(
                discord.SelectOption(
                    label=schedule.label[:100], value=schedule.key, description=tail[:100]
                )
            )
        return options or None

    def _candidatos_rows(self, business: Business, home: ui.Button) -> list[ui.ActionRow]:
        green = discord.ButtonStyle.success
        to_staff = self._button("⬅️ Plantilla", self._plantilla, "a-plantilla")
        candidates = self.service.candidates(business)
        rows = []
        if candidates:
            options = [
                discord.SelectOption(
                    label=f"{c['name']} ({c['age']})"[:100],
                    value=str(c["key"]),
                    description=(
                        f"{staff.PROFILE_BY_KEY[c['profile']].name} · pide "
                        f"{format_amount(c['ask'])}"
                        + (" · media jornada" if c["part_time"] else "")
                    )[:100],
                    default=c["key"] == self.candidate,
                )
                for c in candidates
            ]
            rows.append(
                ui.ActionRow(
                    self._select("Elige un candidato", options, "candidato", self._pick_candidate)
                )
            )
        candidate = self._candidate(business)
        offer = self._offer(business)
        if candidate is None or offer is None:
            rows.append(ui.ActionRow(to_staff, home))
            return rows
        roles = self._roles(business, candidate)
        role = staff.ROLE_BY_KEY[offer["role"]]
        schedule = offer_horario(offer)

        def asks(role_key: str, jornada: int) -> str:
            """Lo que pide el candidato en un puesto y a una jornada."""
            amount = staff.role_ask(candidate["skills"], candidate["traits"], jornada, role_key)
            return format_amount(amount)

        if roles:
            puestos = [
                discord.SelectOption(
                    label=f"{r.name} · pide {asks(r.key, schedule.jornada)}"[:100],
                    value=r.key,
                    emoji=r.emoji,
                    description=(staff.effect_text(r) or r.text)[:100],
                    default=r.key == role.key,
                )
                for r in roles
            ]
            rows.append(ui.ActionRow(self._select("Puesto", puestos, "puesto", self._pick_role)))
        if role.key != staff.MANAGER:
            contracts = [
                discord.SelectOption(
                    label=name,
                    value=key,
                    description=text[:100],
                    default=key == offer["contract"],
                )
                for key, name, text in CONTRACTS
            ]
            rows.append(
                ui.ActionRow(self._select("Contrato", contracts, "contrato", self._pick_contract))
            )
        if role.schedule:
            sector = business.sector_info
            hours = [
                discord.SelectOption(
                    label=h.label[:100],
                    value=h.key,
                    description=(
                        f"Pide {asks(role.key, h.jornada)}"
                        + (
                            f" · clientes al {afluencia(sector, h)} % de la media"
                            if role.plays
                            else ""
                        )
                        + (" · cansa: baja el humor" if h.kind == "partida" else "")
                    )[:100],
                    default=h.key == schedule.key,
                )
                for h in staff.horarios_for(candidate["part_time"])
            ]
            rows.append(ui.ActionRow(self._select("Horario", hours, "turno", self._pick_horario)))
        rows.append(
            ui.ActionRow(
                self._button(
                    f"✍️ Contratar · {asks(role.key, schedule.jornada)} la noche",
                    self._hire,
                    "contratar",
                    style=green,
                    disabled=not roles,
                ),
                to_staff,
                home,
            )
        )
        return rows

    def _despido_rows(self, business: Business) -> list[ui.ActionRow]:
        employee = self._chosen(business)
        row = ui.ActionRow()
        if employee is not None:
            for option in self.service.dismissal_options(business, employee):
                row.add_item(
                    self._button(
                        f"{option.name} · {format_amount(option.amount)}",
                        self._fire(option.kind),
                        f"despido:{option.kind}",
                        style=discord.ButtonStyle.danger,
                        disabled=not option.allowed,
                    )
                )
        row.add_item(self._button("⬅️ No", self._plantilla, "despedir-no"))
        return [row]

    def _mejoras_rows(
        self, business: Business, back: ui.Button, home: ui.Button
    ) -> list[ui.ActionRow]:
        green = discord.ButtonStyle.success
        rows = []
        items = [e for e in equipment_for(business.sector) if e.key not in business.equipment]
        if items:
            options = [
                discord.SelectOption(
                    label=e.name, value=e.key, description=e.effect()[:100],
                    default=e.key == self.equipment,
                )
                for e in items
            ]  # fmt: skip
            rows.append(
                ui.ActionRow(
                    self._select("Elige una mejora", options, "mejora", self._pick_equipment)
                )
            )
        active = campaign_state(business, self._today())
        if active is None:
            options = [
                discord.SelectOption(
                    label=c.name, value=c.key, default=c.key == self.campaign,
                    description=f"+{round(c.boost * 100)} % de clientes durante {c.days} días",
                )
                for c in CAMPAIGNS
            ]  # fmt: skip
            rows.append(
                ui.ActionRow(
                    self._select("Elige una campaña", options, "campana", self._pick_campaign)
                )
            )
        last = ui.ActionRow()
        if (item := EQUIPMENT_BY_KEY.get(self.equipment)) and item in items:
            last.add_item(
                self._button(
                    f"🛒 Instalar · {format_amount(item.cost)} + IGIC",
                    self._install, "instalar", style=green,
                )
            )  # fmt: skip
        if active is None and (campaign := CAMPAIGN_BY_KEY.get(self.campaign)):
            last.add_item(
                self._button(
                    f"📣 Lanzar · {format_amount(campaign.cost)} + IGIC",
                    self._launch, "lanzar", style=green,
                )
            )  # fmt: skip
        last.add_item(back)
        last.add_item(home)
        rows.append(last)
        return rows

    def _sociedad_rows(self, back: ui.Button, home: ui.Button) -> list[ui.ActionRow]:
        view = self.company
        if view is None:
            return [ui.ActionRow(back, home)]
        green = discord.ButtonStyle.success
        if not view.business.is_company:
            options = [
                discord.SelectOption(
                    label=f"Capital de {format_amount(c)}",
                    value=str(c),
                    default=c == self.capital,
                    description=(
                        f"Reserva del 20 % y respondes hasta {format_amount(sl.FULL_CAPITAL)}"
                        if c < sl.FULL_CAPITAL
                        else "Responsabilidad limitada al capital"
                    ),
                )
                for c in sl.CAPITALS
            ]
            fees = sl.constitution_fees(self.capital)
            cost = self.capital + fees + igic(fees)
            found = self._button(
                f"✍️ Constituir · {format_amount(cost)}",
                self._constitute,
                "constituir",
                style=green,
                disabled=view.blocked is not None or bool(view.business.own_listing),
            )
            return [
                ui.ActionRow(
                    self._select("Elige el capital", options, "capital", self._pick_capital)
                ),
                ui.ActionRow(found, back, home),
            ]
        info = view.business.company
        actions = ui.ActionRow(
            self._button("💶 Aportar", self._ask("aportar"), "aportar", style=green),
            self._button("🏦 Prestar", self._ask("prestar"), "prestar"),
            self._button(
                "↩️ Devolverme", self._ask("devolver"), "devolver", disabled=not info["loan"]
            ),
            self._button(
                "💼 Sueldo", self._ask("sueldo"), "sueldo", disabled=view.blocked is not None
            ),
            self._button(
                "💰 Dividendos",
                self._ask("dividendo"),
                "dividendo",
                disabled=not view.dividend_room,
            ),
        )
        shares = [
            discord.SelectOption(
                label=f"RIC: dotar el {round(share * 100)} % de la base",
                value=str(share),
                description=(
                    "Sin reserva"
                    if not share
                    else "No paga Sociedades si lo inviertes en 3 semanas"
                ),
                default=share == info.get("ric_share", 0.0),
            )
            for share in sl.RIC_SHARES
        ]
        zec = self._button(
            "🏝️ Salir de la ZEC" if info.get("zec") else "🏝️ Inscribirse en la ZEC",
            self._zec,
            "zec",
        )
        sell = self._button("💼 Vender la sociedad", self._to_sale, "vender")
        return [
            actions,
            ui.ActionRow(self._select("🌴 Reserva para Inversiones", shares, "ric", self._ric)),
            ui.ActionRow(zec, sell, back, home),
        ]

    def _traslado_rows(self, business: Business, back: ui.Button) -> list[ui.ActionRow]:
        target = self._move_target()
        assert target is not None
        barrio, size, quote, own = target
        barrios = [
            discord.SelectOption(
                label=b.name, value=b.key, description=f"{b.town} · {THEMES[b.theme]}"[:100],
                default=not own and b.key == barrio,
            )
            for b in BARRIOS
        ]  # fmt: skip
        sizes = [
            discord.SelectOption(
                label=f"Local {s.name.lower()}", value=str(i), default=not own and i == size,
                description=f"{round(business.sector_info.m2 * s.m2)} m² · "
                f"×{s.clients:.1f} clientes por turno".replace(".", ","),
            )
            for i, s in enumerate(SIZES)
        ]  # fmt: skip
        pick_barrio = self._select(
            "Elige el barrio", barrios, "traslado-barrio", self._pick_move_barrio
        )
        pick_size = self._select("Elige el tamaño", sizes, "traslado-tamano", self._pick_move_size)
        rows = [ui.ActionRow(pick_barrio), ui.ActionRow(pick_size)]
        fit = [o for o in self.own_locals if o.fits]
        if fit:
            locals_ = [
                discord.SelectOption(
                    label=f"{o.listing.title}"[:100], value=o.listing.key,
                    description=f"{BARRIO_BY_KEY[o.listing.barrio].name} · {o.listing.m2} m² · "
                    f"local {SIZES[o.size or 0].name.lower()}"[:100],
                    default=own and o.listing.key == self.move["own"],
                )
                for o in fit
            ]  # fmt: skip
            title = "🏠 A un local tuyo de pisos"
            rows.append(
                ui.ActionRow(self._select(title, locals_, "traslado-propio", self._pick_own))
            )
        # Donde ya está no se traslada (el servicio también lo comprueba).
        here = (
            business.own_listing == self.move["own"]
            if own
            else quote.same_barrio and size == business.size and not business.own_listing
        )
        go = self._button(
            f"🚚 Trasladarse · {format_amount(quote.total)}",
            self._do_move,
            "trasladar",
            style=discord.ButtonStyle.success,
            disabled=here,
        )
        rows.append(ui.ActionRow(go, back))
        return rows

    def _style_rows(self) -> list[ui.ActionRow]:
        palettes = [
            discord.SelectOption(label=name, value=str(i), default=i == self.draft["palette"])
            for i, (name, _main, _accent) in enumerate(PALETTES)
        ]
        emblems = [
            discord.SelectOption(label=name, value=str(i), default=i == self.draft["emblem"])
            for i, (_key, name) in enumerate(EMBLEMS)
        ]
        return [
            ui.ActionRow(
                self._select("🎨 Colores del rótulo", palettes, "paleta", self._pick_palette)
            ),
            ui.ActionRow(self._select("✨ Emblema", emblems, "emblema", self._pick_emblem)),
        ]

    def _draft_rows(self, home: ui.Button) -> list[ui.ActionRow]:
        sector = SECTOR_BY_KEY[self.draft["sector"]]
        barrios = [
            discord.SelectOption(
                label=f"{b.name} ({ISLANDS[b.island]})"[:100],
                value=b.key,
                description=(
                    f"{THEMES[b.theme]} · demanda ×{sector.fit.get(b.theme, 1.0):.2f} · alquiler "
                    f"{format_amount(opening_quote(sector, b).rent)}/día"
                ).replace(".", ",", 1)[:100],
                default=b.key == self.draft["barrio"],
            )
            for b in BARRIOS
        ]
        return [
            ui.ActionRow(self._select("📍 Barrio", barrios, "barrio", self._pick_barrio)),
            *self._style_rows(),
            ui.ActionRow(
                self._button("✏️ Nombre", self._rename, "nombre"),
                self._button(
                    "➡️ Seguir", self._to_confirm, "seguir", style=discord.ButtonStyle.success
                ),
                self._button("⬅️ Otro sector", self._to_montar, "otro-sector"),
                home,
            ),
        ]

    def _game_rows(self) -> list[ui.ActionRow]:
        assert self.turn is not None
        game = self.turn.game
        client = game.current
        if client is None:
            return []
        rows: list[ui.ActionRow] = []
        row = ui.ActionRow()

        def push(button: ui.Button) -> None:
            nonlocal row
            if len(row.children) == 5:
                rows.append(row)
                row = ui.ActionRow()
            row.add_item(button)

        green = discord.ButtonStyle.success
        if game.sector.game == "comandas":
            for product in game.sector.products:
                left = game.available(product.key)
                push(
                    self._game_button(
                        f"{product.name} ({left})", f"p:{product.key}",
                        emoji=PRODUCT_EMOJI.get(product.key), disabled=left <= 0,
                    )
                )  # fmt: skip
            push(self._game_button("Deshacer", "undo", emoji="↩️"))
            push(self._game_button("Servir", "serve", emoji="✅", style=green))
        elif game.sector.game == "caja":
            for coin in COINS:
                push(self._game_button(format_amount(coin), f"c:{coin}"))
            push(self._game_button("Deshacer", "undo", emoji="↩️"))
            push(self._game_button("Bolsillo", "thief", emoji="👀", disabled=game.accused))
            push(self._game_button("Cobrar", "charge", emoji="✅", style=green))
        elif game.sector.game in GAME_ROWS:
            for button in GAME_ROWS[game.sector.game](self, game, client):
                push(button)
        elif game.step == "diagnostico":
            for key in client.options:
                product = game.sector.product(key)
                assert product is not None
                push(
                    self._game_button(
                        f"{product.name} ({game.stock.get(key, 0)})", f"d:{key}",
                        emoji=PRODUCT_EMOJI.get(key), disabled=game.stock.get(key, 0) <= 0,
                    )
                )  # fmt: skip
            push(self._game_button("No tengo la pieza", "nopart", emoji="🚫"))
        else:
            for index, (_key, name, _mult, _accept) in enumerate(QUOTES):
                push(
                    self._game_button(
                        f"{name} · {format_amount(game.quote_amount(index))}", f"q:{index}",
                        style=green if index == 1 else discord.ButtonStyle.secondary,
                    )
                )  # fmt: skip
        rows.append(row)
        return rows

    # -- Enseñar ----------------------------------------------------------------------

    async def refresh(self) -> None:
        """Vuelve a leer la cadena y el local que se mira (y su sociedad, su nave o su venta)."""
        self.chain = await self.service.owned(self.guild_id, self.owner.id)
        if self.biz is not None and all(b.id != self.biz for b in self.chain):
            self.biz = None
        if self.biz is None and self.chain:
            self.biz = self.chain[0].id
        self.status = (
            await self.service.status(self.guild_id, self.owner.id, self.biz)
            if self.chain
            else None
        )
        self.nave = await self.service.nave(self.guild_id, self.owner.id) if self.chain else None
        if self.status is None and self.screen not in (MONTAR, PREVIA, CONFIRMAR):
            self.screen = MONTAR
        if self.screen in (SOCIEDAD, VENTA):
            self.company = await self.service.company_view(self.guild_id, self.owner.id, self.biz)
        if self.screen == VENTA:
            self.sale = await self.service.sale_offer(self.guild_id, self.owner.id, self.biz)

    def _where(self) -> tuple[Any, ...]:
        game = self.turn.game if self.turn else None
        return (self.screen, game.index if game else -1, game.step if game else "")

    async def show(self, interaction: discord.Interaction | None, *, reload: bool = True) -> None:
        """Pinta la pantalla de ahora y la pone en el mensaje."""
        if reload:
            await self.refresh()
        state = self.scene_state()
        game = self.turn.game if self.screen == TURNO and self.turn is not None else None
        round_index = game.round if game is not None else -1
        png = await self.cog.painter.paint(state) if state is not None else None
        key = state_key(state) if (state is not None and png is not None) else ""
        self.rebuild(has_image=png is not None)
        kwargs: dict[str, Any] = {"view": self}
        if key != self._shown:
            kwargs["attachments"] = (
                [discord.File(io.BytesIO(png), filename=PNG_NAME)] if png is not None else []
            )
        if await self._edit(interaction, **kwargs) and game is not None:
            # Mientras subía, el jugador no tenía los botones: no le cuenta a la paciencia.
            game.shown(round_index, self.service.now())
        self._shown = key
        self._prefetch()

    def _prefetch(self) -> None:
        here = self._where()
        states = self._neighbours()
        if states:
            self.cog.painter.prefetch(states, still=lambda: self._where() == here)

    async def _edit(self, interaction: discord.Interaction | None, **kwargs: Any) -> bool:
        """Pone `kwargs` en el mensaje del panel. `True` si Discord lo aceptó."""
        try:
            if interaction is not None:
                await edit(interaction, **kwargs)
                if interaction.message is not None:
                    self.message = interaction.message
            elif self.message is not None:
                await self.message.edit(**kwargs)
            else:
                return False
        except discord.HTTPException:
            logger.debug("No se pudo editar el panel del negocio", exc_info=True)
            return False
        return True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño toca su negocio. Los botones del minijuego van por el listener."""
        custom_id = (interaction.data or {}).get("custom_id")
        if isinstance(custom_id, str) and parse_game_id(custom_id) is not None:
            return False
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message(
            f"Este negocio es de {self.owner.display_name}. El tuyo, en `pala`.", ephemeral=True
        )
        return False

    async def on_timeout(self) -> None:
        """Caduca: si había un turno a medias, se cierra y se cobra."""
        if self.turn is not None:
            await self._finish(None)
        self.cog.panels.discard(self)
        for item in self.walk_children():
            if isinstance(item, ui.Button | ui.Select):
                item.disabled = True
        await self._edit(None, view=self)

    # -- Navegar ----------------------------------------------------------------------

    async def _go(self, interaction: discord.Interaction, screen: str) -> None:
        await ack(interaction)
        self.screen = screen
        await self.show(interaction)

    async def _portada(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, PORTADA)

    async def _almacen(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, ALMACEN)

    async def _cuentas(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, CUENTAS)

    async def _confirm_close(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, CERRAR)

    async def _mejoras(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, MEJORAS)

    async def _resenas(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, RESENAS)

    async def _traslado(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        self.own_locals = await self.service.own_locals(self.guild_id, self.owner.id, self.biz)
        business = self.status.business if self.status else None
        if business is not None and not self.move["barrio"]:
            self.move = {
                "barrio": business.barrio,
                "size": min(len(SIZES) - 1, business.size + 1),
                "own": "",
            }
        self.screen = TRASLADO
        await self.show(interaction)

    async def _sociedad(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, SOCIEDAD)

    async def _plantilla(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, PLANTILLA)

    async def _candidatos(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, CANDIDATOS)

    async def _to_dismiss(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, DESPIDO)

    async def _pick_employee(self, interaction: discord.Interaction, value: str) -> None:
        self.employee = int(value) if value.isdigit() else None
        await self._go(interaction, PLANTILLA)

    async def _pick_candidate(self, interaction: discord.Interaction, value: str) -> None:
        self.candidate = int(value) if value.isdigit() else None
        await self._go(interaction, CANDIDATOS)

    async def _pick_role(self, interaction: discord.Interaction, value: str) -> None:
        if value in staff.ROLE_BY_KEY:
            self.offer["role"] = value
            if value == staff.MANAGER:
                self.offer["contract"] = "indefinido"
        await self._go(interaction, CANDIDATOS)

    async def _pick_contract(self, interaction: discord.Interaction, value: str) -> None:
        if any(key == value for key, _name, _text in CONTRACTS):
            self.offer["contract"] = value
        await self._go(interaction, CANDIDATOS)

    async def _pick_horario(self, interaction: discord.Interaction, value: str) -> None:
        if value in staff.HORARIO_BY_KEY:
            self.offer["horario"] = value
        await self._go(interaction, CANDIDATOS)

    async def _reschedule(self, interaction: discord.Interaction, value: str) -> None:
        await ack(interaction)
        try:
            _business, employee, plan = await self.service.reschedule(
                self.guild_id, self.owner.id, self.employee or 0, value, biz=self.biz
            )
        except StaffRefusedError as error:
            await notify(interaction, str(error))
            await self._track(staff_refused_stats())
            return
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        first = employee["name"].split()[0]
        what = {
            "igual": f"Las mismas horas en otro horario: a {first} le sienta regular.",
            "mas": f"Más horas, más sueldo: {first} encantado.",
            "menos": f"{first} acepta trabajar menos horas, y cobrar menos.",
        }[plan.kind]
        self.notes.append(
            f"🕒 **{employee['name']}** trabaja desde mañana con el horario "
            f"{plan.horario.label.lower()}, por {format_amount(plan.wage)} brutos la noche. "
            f"{what}"
        )
        await self.show(interaction)
        await self._track(staff_reschedule_stats(employee, plan))

    async def _hire(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        if self.candidate is None:
            await notify(interaction, "Elige antes a un candidato.")
            return
        offer = dict(self.offer)
        schedule = offer_horario(offer)
        try:
            business, employee = await self.service.hire(
                self.guild_id, self.owner.id, self.candidate, contract=offer["contract"],
                jornada=schedule.jornada, shift=schedule.shift, biz=self.biz,
                role=offer["role"], horario=schedule.key,
            )  # fmt: skip
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        contract = CONTRACT_NAMES[employee["contract"]].lower()
        role = staff.role_of(employee)
        wage = format_amount(employee["wage"])
        if staff.is_manager(employee):
            self.notes.append(
                f"✍️ **{employee['name']} es tu gerente**: {contract}, "
                f"{wage} brutos cada noche. Desde esta noche repone el "
                "almacén (de la nave primero) y cubre las bajas de la plantilla."
            )
        else:
            hours = horario_text(employee).lower()
            today = (
                "Hoy trabaja las franjas de su horario que quedan."
                if role.plays
                else f"Desde hoy: {staff.effect_text(role, staff.strength(employee))}."
            )
            self.notes.append(
                f"✍️ **{employee['name']} empieza a trabajar contigo** de "
                f"{role.emoji} {role.name.lower()}: {contract}, {hours}, {wage} brutos cada "
                f"noche. {today}"
            )
        if employee["bonus"]:
            self.notes.append(
                f"-# Bonificado (RDL 1/2023): la cuota de la empresa baja "
                f"{format_amount(employee['bonus'])} cada noche durante tres semanas. Si le "
                "despides sin causa antes, la devuelves con recargo."
            )
        if employee["contract"] == "b":
            self.notes.append(
                "-# 🕵️ Sin alta ni contrato: no cotiza, pero si pasa la Inspección de Trabajo "
                f"son {format_amount(staff.FINE_PER_WORKER)} de multa por cabeza y sus cuotas "
                "con recargo."
            )
        self.candidate, self.employee = None, employee["id"]
        self.offer.update(role="", contract="indefinido")
        self.screen = PLANTILLA
        await self._pet_note()
        await self.show(interaction)
        delta = staff_hire_stats(business, employee)
        if staff.is_manager(employee):
            delta.add["biz_managers_hired"] = delta.add.get("biz_managers_hired", 0) + 1
            led = sum(1 for b in self.chain if staff.manager(b) is not None)
            delta.peak["biz_managers_max"] = max(delta.peak.get("biz_managers_max", 0), led)
        await self._track(delta)

    async def _raise(self, interaction: discord.Interaction, value: str) -> None:
        await ack(interaction)
        try:
            share = float(value)
            _business, employee = await self.service.raise_wage(
                self.guild_id, self.owner.id, self.employee or 0, share, biz=self.biz
            )
        except (BusinessError, ValueError) as error:
            await notify(interaction, str(error))
            return
        self.notes.append(
            f"💶 **{employee['name']}** cobra ahora {format_amount(employee['wage'])} brutos la "
            "noche. Se le nota contento."
        )
        await self.show(interaction)
        await self._track(staff_raise_stats(employee))

    async def _course(self, interaction: discord.Interaction, value: str) -> None:
        await ack(interaction)
        try:
            _business, employee, paid, credit = await self.service.train(
                self.guild_id, self.owner.id, self.employee or 0, value, biz=self.biz
            )
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        self.notes.append(
            f"🎓 **{employee['name']}** empieza un curso de "
            f"{staff.SKILL_NAMES[value].lower()}: pagas {format_amount(paid)}"
            + (f" ({format_amount(credit)} los pone el crédito de FUNDAE)" if credit else "")
            + f". La noche que viene, +{staff.COURSE_POINTS}."
        )
        await self.show(interaction)
        await renta.remind(self.cog.bot, interaction)
        await self._track(staff_course_stats(employee, paid, credit))

    async def _permanent(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        try:
            _business, employee = await self.service.make_permanent(
                self.guild_id, self.owner.id, self.employee or 0, biz=self.biz
            )
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        self.notes.append(f"📄 **{employee['name']}** ya es fijo: su contrato no se acaba.")
        await self._pet_note(Mood.GOOD)
        await self.show(interaction)
        await self._track(staff_permanent_stats())

    def _fire(self, kind: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        """El botón de una forma de despedir al empleado elegido."""

        async def callback(interaction: discord.Interaction) -> None:
            await ack(interaction)
            try:
                _business, employee, choice, refund = await self.service.dismiss(
                    self.guild_id, self.owner.id, self.employee or 0, kind, biz=self.biz
                )
            except BusinessError as error:
                await notify(interaction, str(error))
                return
            paid = f"Le pagas {format_amount(choice.amount)}." if choice.amount else "Sin pagar."
            self.notes.append(f"🚪 **{choice.name} de {employee['name']}.** {paid}")
            if refund:
                self.notes.append(
                    f"-# Devuelves {format_amount(refund)} de bonificaciones con recargo y no "
                    "tendrás otras en una semana (art. 11.4 del RDL 1/2023)."
                )
            self.employee = None
            self.screen = PLANTILLA
            await self._pet_note(Mood.BAD if kind == "improcedente" else None)
            await self.show(interaction)
            if choice.amount or refund:
                await renta.remind(self.cog.bot, interaction)
            await self._track(staff_fire_stats(choice, refund))

        return callback

    async def _bank(self, interaction: discord.Interaction) -> None:
        # El banco contesta él (un mensaje privado nuevo): este panel no cambia.
        if not await banco.open_business(self.cog.bot, interaction, self.biz):
            await notify(interaction, "El banco está cerrado ahora mismo.")

    async def _pick_capital(self, interaction: discord.Interaction, value: str) -> None:
        if value.isdigit() and int(value) in sl.CAPITALS:
            self.capital = int(value)
        await self._go(interaction, SOCIEDAD)

    async def _constitute(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        if self.turn is not None:
            await notify(interaction, "Acaba el turno antes de ir al notario.")
            return
        try:
            business, total = await self.service.constitute(
                self.guild_id, self.owner.id, self.capital, biz=self.biz
            )
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        count = len(self.chain)
        self.notes.append(
            f"🏢 **{business.name}, S.L. ya es una sociedad limitada.** Pusiste "
            f"{format_amount(total)}: el capital y la notaría. Desde hoy, "
            + (f"tus {count} locales cobran y pagan" if count > 1 else "el negocio cobra y paga")
            + " desde su caja."
        )
        await self._pet_note(Mood.GOOD)
        await self.show(interaction)
        await renta.remind(self.cog.bot, interaction)
        await self._track(
            StatDelta(
                add={"biz_sl_founded": 1},
                peak={"biz_sl_capital_max": self.capital, "biz_sl_chain_max": count},
            )
        )

    async def _ric(self, interaction: discord.Interaction, value: str) -> None:
        await ack(interaction)
        try:
            share = float(value)
            await self.service.set_ric(self.guild_id, self.owner.id, share, biz=self.biz)
        except (BusinessError, ValueError) as error:
            await notify(interaction, str(error))
            return
        self.notes.append(
            f"🌴 Cada domingo llevas a la RIC el {round(share * 100)} % de la base de Sociedades: "
            "no tributa si lo inviertes en mejoras o en la obra de un local en 3 semanas."
            if share
            else "🌴 Ya no dotas la RIC."
        )
        await self.show(interaction)
        if share:
            await self._track(StatDelta(add={"biz_ric_set": 1}))

    async def _zec(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        view = self.company
        join = not (view is not None and view.business.company.get("zec"))
        try:
            await self.service.zec(self.guild_id, self.owner.id, join=join, biz=self.biz)
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        self.notes.append(
            "🏝️ **Tu sociedad entra en la Zona Especial Canaria**: Sociedades al 4 % cada semana "
            "que mantengas el empleo."
            if join
            else "🏝️ Tu sociedad sale de la ZEC: vuelve al tipo de siempre."
        )
        await self._pet_note(Mood.GOOD if join else None)
        await self.show(interaction)
        if join:
            await self._track(StatDelta(add={"biz_zec_joined": 1}))

    def _ask(self, action: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        """Un botón de 🏢 Sociedad que pide el importe en un formulario."""

        async def callback(interaction: discord.Interaction) -> None:
            # Un formulario es la primera respuesta: aquí solo se mira memoria.
            view = self.company
            info = view.business.company if view is not None else {}
            most = {
                "aportar": None,
                "prestar": None,
                "devolver": info.get("loan", 0),
                "sueldo": view.cash if view else 0,
                "dividendo": view.dividend_room if view else 0,
            }[action]
            await interaction.response.send_modal(CompanyAmountForm(self, action, most))

        return callback

    async def company_action(
        self, interaction: discord.Interaction, action: str, text: str
    ) -> None:
        """Lo que se pide en un formulario de 🏢 Sociedad (llega de `CompanyAmountForm`)."""
        await ack(interaction)
        view = self.company
        cash = view.cash if view is not None else 0
        try:
            amount = parse_amount(text, cash)
        except ValueError as error:
            await notify(interaction, str(error))
            return
        calls = {
            "aportar": self.service.contribute,
            "prestar": self.service.lend,
            "devolver": self.service.repay,
            "sueldo": self.service.pay_salary,
            "dividendo": self.service.pay_dividend,
        }
        try:
            _business, paid = await calls[action](
                self.guild_id, self.owner.id, amount, biz=self.biz
            )
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        money = format_amount(amount)
        notes = {
            "aportar": f"💶 Aportas {money} a la caja de la sociedad.",
            "prestar": f"🏦 Le prestas {money} a tu sociedad: te lo devolverá cuando pueda.",
            "devolver": f"↩️ Tu sociedad te devuelve {money} del préstamo.",
            "sueldo": f"💼 Cobras {money} de sueldo de administrador; te retienen "
            f"{format_amount(paid.salary_tax)} de IRPF ({TAX_COLLECTOR}).",
            "dividendo": f"💰 Te repartes {money} de dividendo; te retienen "
            f"{format_amount(paid.dividend_tax)} (19 %, {TAX_COLLECTOR}).",
        }
        self.notes.append(notes[action])
        await self._pet_note(Mood.GOOD if action in ("sueldo", "dividendo") else None)
        await self.show(interaction)
        await renta.remind(self.cog.bot, interaction)
        stats = {
            "aportar": {"biz_sl_contributed": amount},
            "prestar": {"biz_sl_lent": amount},
            "devolver": {"biz_sl_repaid": amount},
            "sueldo": {"biz_sl_salaries": 1, "biz_sl_salary_paid": amount},
            "dividendo": {"biz_sl_dividends": 1, "biz_sl_dividend_paid": amount},
        }
        await self._track(StatDelta(add=stats[action]))

    async def _pick_equipment(self, interaction: discord.Interaction, value: str) -> None:
        self.equipment = value if value in EQUIPMENT_BY_KEY else ""
        await self._go(interaction, MEJORAS)

    async def _pick_campaign(self, interaction: discord.Interaction, value: str) -> None:
        self.campaign = value if value in CAMPAIGN_BY_KEY else ""
        await self._go(interaction, MEJORAS)

    async def _pick_move_barrio(self, interaction: discord.Interaction, value: str) -> None:
        if value in BARRIO_BY_KEY:
            self.move.update(barrio=value, own="")
        await self._go(interaction, TRASLADO)

    async def _pick_move_size(self, interaction: discord.Interaction, value: str) -> None:
        if value.isdigit() and int(value) < len(SIZES):
            self.move.update(size=int(value), own="")
        await self._go(interaction, TRASLADO)

    async def _pick_own(self, interaction: discord.Interaction, value: str) -> None:
        if any(o.listing.key == value for o in self.own_locals):
            self.move["own"] = value
        await self._go(interaction, TRASLADO)

    # -- Fase 5: la cadena, la nave y vender ----------------------------------------------

    async def _cadena(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, CADENA)

    async def _pick_local(self, interaction: discord.Interaction, value: str) -> None:
        await ack(interaction)
        if self.turn is not None:
            await notify(interaction, "Acaba el turno antes de cambiar de local.")
            return
        if value.isdigit() and any(b.id == int(value) for b in self.chain):
            self.biz = int(value)
            # Lo elegido era del otro local.
            self.plan, self.product, self.employee, self.candidate = None, "", None, None
            self.equipment = self.campaign = ""
            self.move = {"barrio": "", "size": 0, "own": ""}
            self.notes.extend(await self.service.take_news(self.guild_id, self.owner.id, self.biz))
        self.screen = PORTADA
        await self.show(interaction)

    async def _new_local(self, interaction: discord.Interaction) -> None:
        # Con el rótulo del local de ahora: una cadena suele llevar la misma marca.
        if self.status is not None:
            business = self.status.business
            self.draft.update(
                sector="", barrio="", name=business.name, palette=business.palette,
                emblem=business.emblem,
            )  # fmt: skip
        await self._go(interaction, MONTAR)

    async def _nave(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        self.own_naves = await self.service.own_naves(self.guild_id, self.owner.id)
        self.leaving_nave = False
        self.screen = NAVE
        await self.show(interaction)

    async def _pick_nave_barrio(self, interaction: discord.Interaction, value: str) -> None:
        if value in BARRIO_BY_KEY:
            self.nave_pick.update(barrio=value, own="")
        await self._go(interaction, NAVE)

    async def _pick_nave_size(self, interaction: discord.Interaction, value: str) -> None:
        if value.isdigit() and int(value) < len(chain.NAVE_SIZES):
            self.nave_pick.update(size=int(value), own="")
        await self._go(interaction, NAVE)

    async def _pick_own_nave(self, interaction: discord.Interaction, value: str) -> None:
        if any(o.listing.key == value for o in self.own_naves):
            self.nave_pick["own"] = value
        await self._go(interaction, NAVE)

    async def _pick_import(self, interaction: discord.Interaction, value: str) -> None:
        self.nave_pick["product"] = value
        self.leaving_nave = False
        await self._go(interaction, NAVE)

    async def _open_nave(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        pick = self.nave_pick
        try:
            nave, quote, _balance = await self.service.open_nave(
                self.guild_id, self.owner.id, barrio=self._nave_barrio(), size=pick["size"],
                own=pick["own"],
            )  # fmt: skip
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        self.notes.append(
            f"🏭 **Nave abierta en {nave.barrio_info.name}**: caben {nave.size_info.lots} lotes. "
            f"Has pagado {format_amount(quote.total)} (tasa y fianza)"
            + (
                f"; el alquiler, {format_amount(quote.rent)} cada noche con su IGIC."
                if quote.rent
                else "; es tuya: no paga alquiler."
            )
        )
        await self._pet_note()
        await self.show(interaction)
        await renta.remind(self.cog.bot, interaction)
        await self._track(
            StatDelta(
                add={"biz_nave_opened": 1, "biz_nave_own": int(bool(pick["own"]))},
                peak={"biz_nave_size_max": nave.size + 1},
            )
        )

    async def _import(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        try:
            nave, quote, _balance = await self.service.import_container(
                self.guild_id, self.owner.id, self.nave_pick["product"]
            )
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        self.notes.append(
            f"🚢 **Contenedor de {quote.product.name.lower()} en camino**: {quote.units} unidades "
            f"por {format_amount(quote.total)} ({signed(quote.saving)} frente al mayorista). "
            f"Llega a la nave en {chain.IMPORT_NIGHTS} noches."
        )
        self.notes.append(
            f"-# 🐶 IGIC de la importación {format_amount(quote.igic + quote.customs_igic)}"
            + (f" y AIEM {format_amount(quote.aiem)}" if quote.aiem else " (sin AIEM)")
            + f" ({TAX_COLLECTOR})"
        )
        await self._pet_note()
        await self.show(interaction)
        await renta.remind(self.cog.bot, interaction)
        fill = round(100 * chain.used_lots(nave) / nave.size_info.lots)
        await self._track(
            StatDelta(
                add={
                    "biz_imports": 1,
                    "biz_import_spent": quote.total,
                    "biz_aiem_paid": quote.aiem,
                    "biz_import_saved": max(0, quote.saving),
                    "biz_import_aiem_free": int(quote.aiem == 0),
                },
                peak={"biz_nave_fill_max": min(100, fill)},
            )
        )

    async def _leave_nave(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        if not self.leaving_nave:
            self.leaving_nave = True
            await self.show(interaction, reload=False)
            return
        self.leaving_nave = False
        try:
            got, refund, _balance = await self.service.close_nave(self.guild_id, self.owner.id)
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        self.notes.append(
            f"🏭 **Has dejado la nave.** El mayorista paga {format_amount(got)} por lo que había "
            f"dentro y vuelven {format_amount(refund)} de la fianza."
        )
        self.screen = CADENA
        await self.show(interaction)
        await renta.remind(self.cog.bot, interaction)
        await self._track(StatDelta(add={"biz_nave_closed": 1}))

    def _fetch(self, lots: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await ack(interaction)
            try:
                _business, units = await self.service.from_nave(
                    self.guild_id, self.owner.id, self.product, lots, biz=self.biz
                )
            except BusinessError as error:
                await notify(interaction, str(error))
                return
            self.notes.append(f"🏭 Traes {units} unidades de la nave. Ya estaban pagadas.")
            await self.show(interaction)
            await self._track(StatDelta(add={"biz_from_nave": lots}))

        return callback

    async def _to_sale(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, VENTA)

    async def _sell(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        if self.turn is not None:
            await notify(interaction, "Acaba el turno antes de vender.")
            return
        offer = self.sale
        if offer is None:
            return
        try:
            sale = await self.service.sell_company(
                self.guild_id, self.owner.id, price=offer.price, biz=self.biz
            )
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        self.notes.append(
            f"💼 **Has vendido {sale.name}, S.L.** con {plural_locales(sale.locales)}: te pagan "
            f"{format_amount(offer.price)}"
            + (f" y {format_amount(offer.loan)} de tu préstamo" if offer.loan else "")
            + "."
        )
        if sale.gain_tax:
            self.notes.append(
                f"-# 🐶 IRPF de la ganancia (19 %): −{format_amount(sale.gain_tax)} "
                f"({TAX_COLLECTOR})"
            )
        if sale.staff:
            self.notes.append(
                f"-# 👥 Tu plantilla ({sale.staff}) sigue en la sociedad con el comprador."
            )
        self.screen = MONTAR
        await self._pet_note(Mood.GOOD if offer.gain >= 0 else Mood.BAD)
        await self.show(interaction)
        await renta.remind(self.cog.bot, interaction)
        await self._track(
            StatDelta(
                add={
                    "biz_company_sold": 1,
                    "biz_sale_gain": max(0, offer.gain),
                    "biz_sale_tax": sale.gain_tax,
                    "biz_sale_loss": int(offer.gain < 0),
                },
                peak={"biz_sale_price_max": offer.price, "biz_sale_locales_max": sale.locales},
            )
        )

    async def _rotulo(self, interaction: discord.Interaction) -> None:
        if self.status is not None:
            business = self.status.business
            self.draft.update(name=business.name, palette=business.palette, emblem=business.emblem)
        await self._go(interaction, ROTULO)

    async def _home(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        if self.turn is not None:
            return
        self.stop()
        self.cog.panels.discard(self)
        from bot.cogs import work as curro  # el inicio es de `pala`

        await curro.home(self.cog.bot, interaction, self.owner, self.channel)

    # -- Montar -----------------------------------------------------------------------

    async def _to_montar(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, MONTAR)

    async def _pick_sector(self, interaction: discord.Interaction, value: str) -> None:
        if value not in SECTOR_BY_KEY:
            await ack(interaction)
            return
        self.draft["sector"] = value
        if not self.draft["barrio"]:
            sector = SECTOR_BY_KEY[value]
            # De entrada, el barrio donde más demanda tiene el sector.
            self.draft["barrio"] = max(BARRIOS, key=lambda b: sector.fit.get(b.theme, 1.0)).key
        await self._go(interaction, PREVIA)

    async def _pick_barrio(self, interaction: discord.Interaction, value: str) -> None:
        if value in BARRIO_BY_KEY:
            self.draft["barrio"] = value
        await self._go(interaction, self.screen)

    async def _pick_palette(self, interaction: discord.Interaction, value: str) -> None:
        if value.isdigit() and int(value) < len(PALETTES):
            self.draft["palette"] = int(value)
        await self._go(interaction, self.screen)

    async def _pick_emblem(self, interaction: discord.Interaction, value: str) -> None:
        if value.isdigit() and int(value) < len(EMBLEMS):
            self.draft["emblem"] = int(value)
        await self._go(interaction, self.screen)

    async def _rename(self, interaction: discord.Interaction) -> None:
        # Un formulario tiene que ser la primera respuesta: nada de `ack` antes.
        await interaction.response.send_modal(NameModal(self))

    async def _to_confirm(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, CONFIRMAR)

    async def _back_to_draft(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, PREVIA)

    async def _open(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        draft = self.draft
        try:
            business, fee = await self.service.open(
                self.guild_id, self.owner.id, sector=draft["sector"], barrio=draft["barrio"],
                name=draft["name"], palette=draft["palette"], emblem=draft["emblem"],
            )  # fmt: skip
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        before = len(self.chain)
        self.biz, self.plan, self.product, self.employee = business.id, None, "", None
        quote = opening_quote(business.sector_info, business.barrio_info)
        self.notes.append(
            f"🎉 **¡{business.name} abre sus puertas!** Has pagado {format_amount(quote.total)} "
            f"(tasa, acondicionamiento con su IGIC y la fianza)"
            + (f" y {format_amount(fee)} de cuota de autónomo (tarifa plana)." if fee else ".")
        )
        self.notes.append("-# Ahora llena el 📦 almacén: sin stock no hay nada que vender.")
        self.screen = PORTADA
        await self.show(interaction)
        await renta.remind(self.cog.bot, interaction)
        barrio = business.barrio_info
        opened = {
            "biz_opened": 1,
            f"biz_opened_{business.sector}": 1,
            f"biz_theme_{barrio.theme}": 1,
            f"biz_island_{barrio.island}": 1,
            "biz_chain_opened": int(before > 0),
        }
        await self._track(StatDelta(add=opened, peak=chain_peaks(self.chain)))

    # -- Rótulo, precios, almacén y cerrar --------------------------------------------

    async def _save_style(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        try:
            await self.service.restyle(
                self.guild_id, self.owner.id, name=self.draft["name"],
                palette=self.draft["palette"], emblem=self.draft["emblem"], biz=self.biz,
            )  # fmt: skip
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        self.notes.append("🎨 Rótulo nuevo colgado.")
        self.screen = PORTADA
        await self.show(interaction)
        await self._track(StatDelta(add={"biz_restyled": 1}, peak=chain_peaks(self.chain)))

    async def _set_price(self, interaction: discord.Interaction, value: str) -> None:
        await ack(interaction)
        try:
            await self.service.restyle(self.guild_id, self.owner.id, price=value, biz=self.biz)
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        await self.show(interaction)
        await self._track(StatDelta(add={f"biz_price_{value}": 1}))

    async def _set_product_price(self, interaction: discord.Interaction, value: str) -> None:
        await ack(interaction)
        level = None if value == "general" else value
        try:
            await self.service.set_price(
                self.guild_id, self.owner.id, self.product, level, biz=self.biz
            )
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        await self.show(interaction)
        if level is not None:
            await self._track(StatDelta(add={"biz_price_product": 1}))

    async def _install(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        key = self.equipment
        try:
            business, base, tax, _balance = await self.service.buy_equipment(
                self.guild_id, self.owner.id, key, biz=self.biz
            )
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        item = EQUIPMENT_BY_KEY[key]
        self.equipment = ""
        self.notes.append(
            f"🛠️ **{item.name}** instalada: {format_amount(base)} + IGIC {format_amount(tax)}. "
            "Se amortiza cada noche."
        )
        await self._pet_note()
        await self.show(interaction)
        await renta.remind(self.cog.bot, interaction)
        await self._track(
            StatDelta(
                add={"biz_equipment": 1, "biz_spent_equipment": base + tax},
                peak={"biz_equipment_max": len(business.equipment)},
            )
        )

    async def _launch(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        key = self.campaign
        try:
            _business, base, tax, _balance = await self.service.start_campaign(
                self.guild_id, self.owner.id, key, biz=self.biz
            )
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        campaign = CAMPAIGN_BY_KEY[key]
        self.campaign = ""
        self.notes.append(
            f"📣 **{campaign.name}** en marcha {campaign.days} días: "
            f"{format_amount(base)} + IGIC {format_amount(tax)}."
        )
        await self._pet_note()
        await self.show(interaction)
        await renta.remind(self.cog.bot, interaction)
        await self._track(
            StatDelta(
                add={
                    "biz_campaigns": 1,
                    f"biz_campaign_{key}": 1,
                    "biz_spent_marketing": base + tax,
                }
            )
        )

    async def _do_move(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        if self.turn is not None:
            await notify(interaction, "Acaba el turno antes de trasladarte.")
            return
        target = self._move_target()
        if target is None:
            return
        barrio, size, _quote, own = target
        try:
            if own:
                business, quote, _balance = await self.service.move_to_own(
                    self.guild_id, self.owner.id, self.move["own"], biz=self.biz
                )
            else:
                business, quote, _balance = await self.service.move(
                    self.guild_id, self.owner.id, barrio, size, biz=self.biz
                )
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        self.notes.append(
            f"🚚 **{business.name}** ya está en {business.barrio_info.name}, en un "
            f"{size_text(business).lower()}. Has pagado {format_amount(quote.total)}"
            f" y te devuelven {format_amount(quote.refund)} de la fianza."
        )
        self.move = {"barrio": "", "size": 0, "own": ""}
        self.screen = PORTADA
        await self._pet_note()
        await self.show(interaction)
        await renta.remind(self.cog.bot, interaction)
        place = business.barrio_info
        await self._track(
            StatDelta(
                add={
                    "biz_moves": 1,
                    "biz_own_local": int(own),
                    f"biz_theme_{place.theme}": 1,
                    f"biz_island_{place.island}": 1,
                },
                peak={"biz_size_max": business.size + 1},
            )
        )

    async def _pick_product(self, interaction: discord.Interaction, value: str) -> None:
        self.product = value
        await self._go(interaction, ALMACEN)

    def _buy(self, lots: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await ack(interaction)
            try:
                _business, base, tax, _balance = await self.service.buy(
                    self.guild_id, self.owner.id, self.product, lots, biz=self.biz
                )
            except BusinessError as error:
                await notify(interaction, str(error))
                return
            self.notes.append(
                f"📦 Comprado: {format_amount(base)}"
                + (f" + IGIC {format_amount(tax)}" if tax else " (IGIC tipo cero)")
                + "."
            )
            await self.show(interaction)
            await renta.remind(self.cog.bot, interaction)
            await self._track(StatDelta(add={"biz_purchases": 1, "biz_spent_stock": base + tax}))

        return callback

    async def _close(self, interaction: discord.Interaction) -> None:
        await self._shut(interaction, traspaso=False)

    async def _close_traspaso(self, interaction: discord.Interaction) -> None:
        await self._shut(interaction, traspaso=True)

    async def _shut(self, interaction: discord.Interaction, *, traspaso: bool) -> None:
        await ack(interaction)
        if self.turn is not None:
            await notify(interaction, "Acaba el turno antes de cerrar.")
            return
        try:
            closing = await self.service.close(
                self.guild_id, self.owner.id, traspaso=traspaso, biz=self.biz
            )
        except BusinessError as error:
            await notify(interaction, str(error))
            return
        verb = "traspasado" if traspaso else "cerrado"
        got = closing.stock + closing.proceeds
        left = [b for b in self.chain if b.id != closing.business.id]
        branch = closing.company and bool(left)
        self.biz = None
        if branch:
            self.notes.append(
                f"🚪 **{closing.business.name} se ha {verb}.** Tu sociedad sigue con "
                f"{plural_locales(len(left))}: lo cobrado ({format_amount(got)}) y la fianza "
                f"({format_amount(closing.refund)}) entran en su caja."
            )
        elif closing.company:
            self.notes.append(
                f"🚪 **{closing.business.name} se ha {verb} y la sociedad, liquidado.** Te "
                f"llegan {format_amount(closing.liquidated)} de cuota de liquidación"
                + (
                    f" y {format_amount(closing.loan_repaid)} de tu préstamo"
                    if closing.loan_repaid
                    else ""
                )
                + "."
            )
            if closing.covered or closing.derived_debt:
                self.notes.append(
                    f"-# ⚖️ La sociedad no llegaba para sus deudas: pagas "
                    f"{format_amount(closing.covered)}"
                    + (
                        f" y te quedan {format_amount(closing.derived_debt)} de deuda con "
                        "Hacienda (art. 43.1.b LGT)"
                        if closing.derived_debt
                        else ""
                    )
                    + "."
                )
        else:
            self.notes.append(
                f"🚪 **{closing.business.name} se ha {verb}.** Cobras {format_amount(got)} por el "
                f"stock{', las mejoras y la clientela' if traspaso else ' y las mejoras'} y te "
                f"devuelven {format_amount(closing.refund)} de la fianza."
            )
        if closing.gain_tax:
            self.notes.append(
                f"-# 🐶 IRPF de la ganancia (19 %): −{format_amount(closing.gain_tax)} "
                f"({TAX_COLLECTOR})"
            )
        if closing.staff and traspaso:
            self.notes.append(
                f"-# 👥 Tu plantilla ({closing.staff}) se va con el negocio: quien lo compra se "
                "queda con sus contratos (art. 44 ET)."
            )
        elif closing.staff:
            self.notes.append(
                f"-# 👥 Despides a tu plantilla por el cierre: {format_amount(closing.severance)} "
                "de indemnizaciones (20 días por año, art. 52.c ET)"
                + (
                    f"; {format_amount(closing.fogasa)} que no llegaban los paga el FOGASA."
                    if closing.fogasa
                    else "."
                )
            )
        if closing.bank_moved:
            paid = (
                f"La caja pagó {format_amount(closing.bank_paid)} al banco y el resto lo asumes "
                "tú, que la avalabas"
                if closing.company
                else "Como autónomo ya eran tuyos"
            )
            self.notes.append(
                f"-# 🏦 {paid}: {format_amount(closing.bank_moved)} siguen como préstamo personal, "
                "con sus cuotas cada noche (`banco`)."
            )
        elif closing.bank_paid:
            self.notes.append(
                f"-# 🏦 La caja canceló sus préstamos: {format_amount(closing.bank_paid)}."
            )
        self.screen = PORTADA if left else MONTAR
        await self._pet_note(Mood.GOOD if traspaso else None)
        await self.show(interaction)
        await renta.remind(self.cog.bot, interaction)
        week = sum(closing.business.week_totals.values())
        sunk = closing.company and (closing.covered > 0 or closing.derived_debt > 0)
        await self._track(
            StatDelta(
                add={
                    "biz_closed": 1,
                    "biz_closed_red": int(week < 0),
                    "biz_traspasos": int(traspaso),
                    "biz_traspaso_money": got if traspaso else 0,
                    "biz_sl_liquidated": int(closing.company and not branch),
                    "biz_branch_closed": int(branch),
                    "biz_sl_sunk": int(sunk),
                    "biz_loan_assumed": int(closing.company and closing.bank_moved > 0),
                },
                peak={
                    "biz_traspaso_max": got if traspaso else 0,
                    "biz_sl_quota_max": closing.liquidated,
                },
            )
        )

    # -- Turno ------------------------------------------------------------------------

    async def _start(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        async with self._lock:
            if self.turn is not None:
                return
            key = (self.guild_id, self.owner.id)
            from bot.cogs import work as curro  # la pala y el negocio no se juegan a la vez

            if key in self.cog.working or curro.busy(self.cog.bot, *key):
                await notify(interaction, "Ya estás currando en otro panel. Una cosa cada vez.")
                return
            try:
                turn = await self.service.start_turn(
                    self.guild_id, self.owner.id, self.plan, biz=self.biz
                )
            except BusinessError as error:
                await notify(interaction, str(error))
                return
            self.plan = None
            self.cog.working.add(key)
            self.turn, self.result, self._finishing = turn, None, False
            self.screen = TURNO
            await self.show(interaction, reload=False)
            self._timer = asyncio.create_task(self._patience(turn))
            self.cog.timers.add(self._timer)
            self._timer.add_done_callback(self.cog.timers.discard)

    async def _patience(self, turn: Turn) -> None:
        """Despide a los clientes a los que se les acaba la paciencia aunque nadie pulse."""
        game = turn.game
        while self.turn is turn:
            delay = game.client_deadline + GRACE_SECONDS - self.service.now()
            await asyncio.sleep(max(0.2, delay))
            if self.turn is not turn or self._finishing:
                return
            now = self.service.now()
            if game.finished(now) or game.expire(now):
                if game.finished(now):
                    await self._finish(None)
                    return
                async with self._render_lock:
                    if self.turn is turn:
                        await self.show(None, reload=False)

    async def game_click(
        self, interaction: discord.Interaction, round_index: int, action: str
    ) -> None:
        """Un clic en el minijuego (llega por `Negocio.on_interaction`)."""
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message(
                f"Este negocio es de {self.owner.display_name}. El tuyo, en `pala`.", ephemeral=True
            )
            return
        turn = self.turn
        if turn is None or self._finishing or round_index != turn.game.round:
            await ack(interaction)
            return
        game = turn.game
        before = self._where()
        now = self.service.now()
        game.press(action, pressed_at(interaction, now))
        if game.finished(now):
            await self._finish(interaction)
            return
        self._ticket += 1
        ticket = self._ticket
        if self._render_lock.locked():
            # Otro clic o el reloj de la paciencia están subiendo una imagen (hasta ~4 s
            # en el informe): se acepta ya, antes de esperar el candado.
            await ack(interaction)
        async with self._render_lock:
            if ticket != self._ticket or self.turn is not turn:
                await ack(interaction)
                return
            if self._where() == before:
                # Solo cambia el texto (la bandeja, el cambio): sin subir imagen.
                self.rebuild(has_image=bool(self._shown))
                await self._edit(interaction, view=self)
                return
            state = self.scene_state()
            png = self.cog.painter.ready(state) if state is not None else None
            if png is None:
                await ack(interaction)
            await self.show(interaction, reload=False)

    async def _finish(self, interaction: discord.Interaction | None) -> None:
        """Cobra el turno y enseña el tique (una sola vez por turno)."""
        turn = self.turn
        if self._finishing or turn is None:
            if interaction is not None:
                await ack(interaction)
            return
        self._finishing = True
        if interaction is not None:
            await ack(interaction)
        if self._timer is not None and asyncio.current_task() is not self._timer:
            self._timer.cancel()
        try:
            result = await self.service.finish_turn(self.guild_id, self.owner.id, turn)
        except BusinessError as error:
            self.notes.append(str(error))
            self.turn = None
            self.cog.working.discard((self.guild_id, self.owner.id))
            self.screen = PORTADA
            async with self._render_lock:
                await self.show(interaction)
            return
        finally:
            self.cog.working.discard((self.guild_id, self.owner.id))
        self.turn, self.result = None, result
        self.screen = CIERRE
        grade = result.summary.grade
        feel = Mood.GOOD if grade in ("S", "A") else Mood.BAD if grade == "D" else None
        moment = Moment(PetEvent.BUSINESS, feel=feel)
        hint = await renta.hint(self.cog.bot, self.guild_id, self.owner.id, moment)
        if hint:
            self.notes.append(hint)
        async with self._render_lock:
            await self.show(interaction)
        if interaction is not None:
            await renta.remind(self.cog.bot, interaction)
        await self._track(business_stats(result, tired=turn.tired))

    async def _pet_note(self, feel: Mood | None = None) -> None:
        """Lo que dice la mascota de lo que acaba de pasar en el negocio (y la Renta)."""
        moment = Moment(PetEvent.BUSINESS, feel=feel)
        if hint := await renta.hint(self.cog.bot, self.guild_id, self.owner.id, moment):
            self.notes.append(hint)

    async def _track(self, delta: StatDelta) -> None:
        await logros.track(self.cog.bot, self.guild_id, self.owner, self.channel, delta)


# -- Los minijuegos de la 9.48.0: texto y botones -------------------------------------

#: Cómo se pide cada peso en la pescadería.
GRAMS_TEXT = {
    250: "un cuarto de kilo", 500: "medio kilo", 750: "tres cuartos de kilo", 1_000: "un kilo",
    1_500: "kilo y medio", 2_000: "dos kilos",
}  # fmt: skip
LETTERS = "ABCD"


def _items(game: Any, order: dict[str, int]) -> str:
    return ", ".join(f"{n} {game.sector.product(k).name.lower()}" for k, n in order.items())


def _heat_bar(heat: int) -> str:
    full = max(0, min(10, heat // 10))
    return "▰" * full + "▱" * (10 - full) + f" **{heat} %**"


def style_text(style: Any) -> str:
    """Un peinado en palabras: «corto, con degradado y mechas»."""
    parts = [LENGTH_NAMES[style.length].lower() if style.length else "sin largo elegido"]
    if style.fade:
        parts.append("con degradado")
    parts.append({"natural": "su color", "tinte": "con tinte", "mechas": "con mechas"}[style.color])
    if style.beard:
        parts.append("barba arreglada")
    if style.wash:
        parts.append("lavado y peinado")
    return ", ".join(parts[:-1]) + f" y {parts[-1]}" if len(parts) > 1 else parts[0]


def _brasa_text(panel: Any, game: Any, client: Any, who: str) -> list[str]:
    lines = []
    if client.usual:
        lines.append(f"{who}: «Lo de siempre». El plato y su punto, de memoria.")
    else:
        lines.append(f"{who} pide: {_items(game, client.order)}.")
    if client.dish:
        dish = game.sector.product(client.dish).name
        if not client.usual:
            _key, _ask, low, high = POINT_BY_KEY[client.point]
            lines.append(f"🔥 {dish}: {point_text(client.dish, client.point)} ({low}-{high} %)")
        lines.append(f"🔥 En la brasa: {_heat_bar(game.heat)}")
    else:
        lines.append("Nada de brasa en esta mesa: sírvela.")
    return lines


def _encargos_text(panel: Any, game: Any, client: Any, who: str) -> list[str]:
    if client.usual:
        lines = [f"{who}: «Vengo a por lo mío, lo de siempre.»"]
    else:
        clue = game.sector.product(client.clue).name.lower()
        lines = [f"{who}: «Vengo a por el encargo de {client.label} Lleva {clue}.»"]
    for index, bag in enumerate(client.bags):
        lines.append(f"🛍️ **{LETTERS[index]}** · {bag.label} {_items(game, bag.order)}")
    return lines


def _balanza_text(panel: Any, game: Any, client: Any, who: str) -> list[str]:
    product = game.sector.product(client.species)
    per_kg = game.weight_price(client.species, 1_000)
    if client.usual:
        lines = [
            f"{who}: «Lo de siempre.» La especie es {product.name.lower()}; el peso, de memoria."
        ]
        target = "?"
    else:
        amount = GRAMS_TEXT.get(client.grams, f"{client.grams} g").capitalize()
        lines = [f"{who}: «{amount} de {product.name.lower()}, por favor.»"]
        target = f"{client.grams} g"
    lines.append(
        f"⚖️ En la balanza: **{game.weighed()} g** de {target} · {format_amount(per_kg)} el kilo"
    )
    return lines


def _revision_text(panel: Any, game: Any, client: Any, who: str) -> list[str]:
    lines = [f"{who}: «{client.symptom}»"]
    if not panel._has_image:  # sin dibujo, lo que se vería
        seen = [game.sector.product(k).name.lower() for k in client.faults]
        if client.protector:
            seen.append("el protector de pantalla rayado (la pantalla, bien)")
        lines.append("👀 Se ve: " + ", ".join(seen) + ".")
    marked = ", ".join(game.sector.product(k).name.lower() for k in game.marked) or "nada"
    lines.append(f"🔧 Para cambiar: {marked}")
    return lines


def _foto_text(panel: Any, game: Any, client: Any, who: str) -> list[str]:
    if client.usual:
        lines = [f"{who}: «Lo de siempre.» Su corte de siempre, de memoria."]
    else:
        lines = [
            f"{who}: «{twist_text(client.twist)}»",
            f"📷 La foto: {style_text(client.photo)}.",
        ]
    beard = " y barba" if client.beard else ""
    lines.append(f"💇 Trae el pelo {LENGTH_NAMES[client.hair].lower()}{beard}.")
    lines.append(f"✂️ Llevas: {style_text(game.style)}.")
    return lines


GAME_TEXT: dict[str, Callable[[Any, Any, Any, str], list[str]]] = {
    "brasa": _brasa_text,
    "encargos": _encargos_text,
    "balanza": _balanza_text,
    "revision": _revision_text,
    "foto": _foto_text,
}


def _brasa_rows(panel: Any, game: Any, client: Any) -> list[ui.Button]:
    green = discord.ButtonStyle.success
    burn = not client.dish
    return [
        panel._game_button("Un poco", "fire:1", emoji="🔥", disabled=burn),
        panel._game_button("Avivar", "fire:2", emoji="💥", disabled=burn),
        panel._game_button("Sacar y servir", "serve", emoji="🍽️", style=green),
    ]


def _encargos_rows(panel: Any, game: Any, client: Any) -> list[ui.Button]:
    out = [
        panel._game_button(f"{LETTERS[i]} · {bag.label}", f"b:{i}", emoji="🛍️")
        for i, bag in enumerate(client.bags)
    ]
    out.append(panel._game_button("No está", "none", emoji="🚫"))
    return out


def _balanza_rows(panel: Any, game: Any, client: Any) -> list[ui.Button]:
    blue, grey = discord.ButtonStyle.primary, discord.ButtonStyle.secondary
    out = [
        panel._game_button(
            f"{grams} g", f"w:{i}", emoji="🐟", style=blue if i in game.scale else grey
        )
        for i, grams in enumerate(client.pieces)
    ]
    out.append(panel._game_button("Pesar", "weigh", emoji="⚖️", style=discord.ButtonStyle.success))
    return out


def _revision_rows(panel: Any, game: Any, client: Any) -> list[ui.Button]:
    blue, grey = discord.ButtonStyle.primary, discord.ButtonStyle.secondary
    out = []
    for product in game.sector.products:
        on = product.key in game.marked
        left = game.stock.get(product.key, 0)
        out.append(
            panel._game_button(
                f"{product.name} ({left})", f"m:{product.key}",
                emoji=PRODUCT_EMOJI.get(product.key), style=blue if on else grey,
                disabled=left <= 0 and not on,
            )
        )  # fmt: skip
    out.append(panel._game_button("Reparar", "fix", emoji="🔧", style=discord.ButtonStyle.success))
    return out


def _foto_rows(panel: Any, game: Any, client: Any) -> list[ui.Button]:
    blue, grey = discord.ButtonStyle.primary, discord.ButtonStyle.secondary
    style = game.style
    longest = LENGTHS.index(client.hair)
    out = [
        panel._game_button(
            LENGTH_NAMES[length], f"l:{length}", emoji="📏",
            style=blue if style.length == length else grey, disabled=i > longest,
        )
        for i, length in enumerate(LENGTHS)
    ]  # fmt: skip
    out.append(
        panel._game_button("Degradado", "fade", emoji="💈", style=blue if style.fade else grey)
    )
    out.extend(
        panel._game_button(
            COLOR_NAMES[color], f"col:{color}", emoji=PRODUCT_EMOJI.get(color, "🧑"),
            style=blue if style.color == color else grey,
        )
        for color in ("natural", "tinte", "mechas")
    )  # fmt: skip
    out.append(
        panel._game_button(
            "Barba", "beard", emoji="🧔", style=blue if style.beard else grey,
            disabled=not client.beard,
        )
    )  # fmt: skip
    out.append(
        panel._game_button("Lavar y peinar", "wash", emoji="🚿", style=blue if style.wash else grey)
    )
    out.append(panel._game_button("Listo", "done", emoji="✂️", style=discord.ButtonStyle.success))
    return out


GAME_ROWS: dict[str, Callable[[Any, Any, Any], list[ui.Button]]] = {
    "brasa": _brasa_rows,
    "encargos": _encargos_rows,
    "balanza": _balanza_rows,
    "revision": _revision_rows,
    "foto": _foto_rows,
}


class CompanyAmountForm(ui.Modal):
    """💶 El importe de una operación con la sociedad (aportar, prestar, sueldo…)."""

    TITLES = {
        "aportar": "💶 Aportar a la sociedad",
        "prestar": "🏦 Prestar a la sociedad",
        "devolver": "↩️ Que te devuelva el préstamo",
        "sueldo": "💼 Sueldo de administrador",
        "dividendo": "💰 Repartir dividendos",
    }

    def __init__(self, panel: NegocioPanel, action: str, most: int | None) -> None:
        super().__init__(title=self.TITLES[action][:45])
        self.panel = panel
        self.action = action
        label = "Importe" if most is None else f"Importe (hasta {format_amount(most)})"
        self.amount: ui.TextInput = ui.TextInput(
            label=label[:45], default=str(most) if most else None, max_length=20
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.panel.company_action(interaction, self.action, str(self.amount.value))


class NameModal(ui.Modal, title="Nombre del negocio"):
    """Formulario para el nombre del negocio (hasta 24 caracteres)."""

    name: ui.TextInput = ui.TextInput(label="Nombre", max_length=24, min_length=1)

    def __init__(self, panel: NegocioPanel) -> None:
        super().__init__()
        self.panel = panel
        self.name.default = panel.draft["name"]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Guarda el nombre en el borrador y repinta la vista previa."""
        await ack(interaction)
        from bot.services.business import clean_name

        try:
            self.panel.draft["name"] = clean_name(self.name.value)
        except ValueError as error:
            await notify(interaction, str(error))
            return
        await self.panel.show(interaction)


class Negocio(commands.Cog, name="Negocio"):
    """Negocios propios dentro de `pala`: montar, atender, comprar y cerrar."""

    def __init__(
        self, bot: commands.Bot, service: BusinessService, renderer: BusinessScene | None = None
    ) -> None:
        self.bot = bot
        self.service = service
        self.painter = Painter(renderer or BusinessScene())
        self.panels: set[NegocioPanel] = set()
        #: Miembros con un turno en marcha.
        self.working: set[tuple[int, int]] = set()
        self.timers: set[asyncio.Task[None]] = set()
        self._tokens = random.SystemRandom()
        claim_custom_ids(GAME_PREFIX)

    def new_token(self) -> int:
        """Número de panel para los `custom_id` del minijuego (al azar, ver `pala`)."""
        taken = {panel.token for panel in self.panels}
        while (token := self._tokens.randrange(1, 10**9)) in taken:
            pass
        return token

    async def cog_load(self) -> None:
        self._nightly.start()

    async def cog_unload(self) -> None:
        """Cierra los turnos a medias (se cobran), la tarea de las noches y el pintor."""
        self._nightly.cancel()
        for panel in list(self.panels):
            if panel.turn is not None:
                with contextlib.suppress(Exception):
                    await panel._finish(None)
            panel.stop()
        self.panels.clear()
        for timer in list(self.timers):
            timer.cancel()
        await self.painter.close()

    @tasks.loop(minutes=10)
    async def _nightly(self) -> None:
        for guild in self.bot.guilds:
            try:
                runs = await self.service.close_nights(guild.id)
            except Exception:
                # Un servidor roto no para la tarea: si saliera la excepción, `tasks.loop`
                # se pararía para siempre y nadie pagaría el local.
                logger.exception(
                    "Fallo inesperado cerrando las noches de los negocios en %s", guild.id
                )
                continue
            for run in runs:
                delta = StatDelta(add={"biz_nights": 1, "biz_nights_unpaid": int(run.unpaid > 0)})
                if run.evicted:
                    delta.add["biz_evicted"] = 1
                if (week := run.corporate) is not None:
                    delta.add.update(
                        biz_sl_weeks=1,
                        biz_sl_tax_paid=week["tax"],
                        biz_sl_loss=int(week["base"] < 0),
                        biz_ric_reserved=week.get("ric", 0),
                        biz_ric_lost=int(week.get("integrated", 0) > 0),
                        biz_zec_weeks=int(week.get("zec", False)),
                    )
                    delta.peak["biz_sl_profit_max"] = max(0, week["profit"])
                if run.refund:
                    delta.add["biz_pluri_refund"] = run.refund
                delta.add.update(business_loan_night_stats(run.loans))
                delta.add.update(
                    biz_containers=run.nave.count("contenedor"),
                    biz_nave_lost=run.nave.count("desahucio"),
                    biz_manager_restocks=int("repuso" in run.manager),
                    biz_manager_nave=int("nave" in run.manager),
                    biz_manager_hires=run.manager.count("contrato"),
                    biz_manager_absent=int("falta" in run.manager),
                )
                night = staff_night_stats(run)
                for stat, value in night.add.items():
                    delta.add[stat] = delta.add.get(stat, 0) + value
                for stat, value in night.peak.items():
                    delta.peak[stat] = max(delta.peak.get(stat, 0), value)
                logros.note(self.bot, guild.id, run.user_id, delta)
            try:
                # Los dueños que atienden un turno ahora comparten el stock: se espera.
                played = await self.service.staff_turns(
                    guild.id, skip=lambda user_id, g=guild.id: (g, user_id) in self.working
                )
            except Exception:
                logger.exception(
                    "Fallo inesperado con la plantilla de los negocios en %s", guild.id
                )
                continue
            mine: dict[int, list[Any]] = {}
            for turn in played:
                mine.setdefault(turn.user_id, []).append(turn)
            for user_id, turns in mine.items():
                logros.note(self.bot, guild.id, user_id, staff_turns_stats(turns))

    @_nightly.before_loop
    async def _before_nightly(self) -> None:
        await self.bot.wait_until_ready()

    async def open_panel(
        self, interaction: discord.Interaction, owner: discord.abc.User, channel: object
    ) -> None:
        """Pone el panel del negocio en el mensaje del botón (ya aceptado con `ack`)."""
        assert interaction.guild_id is not None
        panel = NegocioPanel(self, guild_id=interaction.guild_id, owner=owner, channel=channel)
        panel.message = interaction.message
        await panel.refresh()
        panel.screen = PORTADA if panel.status is not None else MONTAR
        news = await self.service.take_news(interaction.guild_id, owner.id)
        panel.notes.extend(news)
        self.panels.add(panel)
        await panel.show(interaction, reload=False)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Atiende los botones del minijuego (ver la docstring del módulo)."""
        if interaction.type is not discord.InteractionType.component:
            return
        custom_id = (interaction.data or {}).get("custom_id")
        if not isinstance(custom_id, str) or (parsed := parse_game_id(custom_id)) is None:
            return
        token, round_index, action = parsed
        try:
            panel = next((p for p in self.panels if p.token == token), None)
            if panel is None:
                await interaction.response.send_message(
                    "Este panel del negocio ya caducó. Ábrelo otra vez desde `pala`.",
                    ephemeral=True,
                )
                return
            await panel.game_click(interaction, round_index, action)
        finally:
            telemetry = self.bot.telemetry
            telemetry.count(
                "button",
                "NegocioPanel.game_click",
                guild_id=interaction.guild_id,
                user_id=interaction.user.id,
                ms=telemetry.take_answer(interaction.id),
                error=not interaction.response.is_done(),
            )

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra los negocios del servidor cuando el bot deja de pertenecer a él."""
        await self.service.repository.delete_guild_data(guild.id)


# -- Funciones puente ---------------------------------------------------------------


async def open_panel(
    bot: commands.Bot, interaction: discord.Interaction, owner: discord.abc.User, channel: object
) -> bool:
    """Abre el panel del negocio en el mensaje del botón. `False` sin el cog."""
    cog = find_cog(bot, Negocio)
    if cog is None:
        return False
    try:
        await cog.open_panel(interaction, owner, channel)
    except (OSError, sqlite3.Error):
        logger.exception("No se pudo abrir el negocio de %s", owner.id)
        await notify(interaction, "No se pudo abrir tu negocio. Prueba en un rato.")
    return True


async def paint(bot: commands.Bot, state: dict[str, Any]) -> bytes | None:
    """PNG de una pantalla de la escena de los negocios (el inicio de `pala`), o `None`."""
    cog = find_cog(bot, Negocio)
    if cog is None:
        return None
    return await cog.painter.paint(state)


async def business_of(bot: commands.Bot, guild_id: int, user_id: int) -> Business | None:
    """El negocio de un miembro con los contadores de hoy al día (para el inicio de `pala`)."""
    cog = find_cog(bot, Negocio)
    if cog is None:
        return None
    try:
        return await cog.service.business(guild_id, user_id)
    except (OSError, sqlite3.Error):
        logger.exception("No se pudo leer el negocio de %s", user_id)
        return None


async def chain_of(bot: commands.Bot, guild_id: int, user_id: int) -> list[Business]:
    """Todos los locales de un miembro, del más antiguo al más nuevo (vacío sin el cog)."""
    cog = find_cog(bot, Negocio)
    if cog is None:
        return []
    try:
        return await cog.service.owned(guild_id, user_id)
    except (OSError, sqlite3.Error):
        logger.exception("No se pudieron leer los negocios de %s", user_id)
        return []


def busy(bot: commands.Bot, guild_id: int, user_id: int) -> bool:
    """Si el miembro está atendiendo su negocio ahora mismo (solo memoria)."""
    cog = find_cog(bot, Negocio)
    return cog is not None and (guild_id, user_id) in cog.working


async def business_line(bot: commands.Bot, guild_id: int, user_id: int) -> str | None:
    """El negocio de alguien en un renglón (para `perfil`), o `None` si no tiene."""
    cog = find_cog(bot, Negocio)
    if cog is None:
        return None
    try:
        owned = await cog.service.owned(guild_id, user_id)
    except (OSError, sqlite3.Error):
        logger.exception("No se pudo leer el negocio de %s", user_id)
        return None
    if not owned:
        return None
    business = owned[0]
    sector = business.sector_info
    more = f" · y {plural_locales(len(owned) - 1)} más" if len(owned) > 1 else ""
    return (
        f"{sector.emoji} **{business.name}** · {sector.name} en {business.barrio_info.name} · "
        f"{_stars_text(business.stars)}{more}"
    )


async def company_values(bot: commands.Bot, guild_id: int) -> dict[int, int]:
    """El valor de la sociedad de cada dueño (para `perfil` y `fortunas`). Vacío sin el cog."""
    cog = find_cog(bot, Negocio)
    if cog is None:
        return {}
    try:
        return await cog.service.repository.read(lambda c: companies_in(c, guild_id))
    except (OSError, sqlite3.Error):
        logger.exception("No se pudieron leer las sociedades de %s", guild_id)
        return {}


async def pending(bot: commands.Bot, guild_id: int, user_id: int) -> list[Pending]:
    """Lo pendiente del negocio (para `saldo`): el local de esta noche, la deuda y la cuota."""
    cog = find_cog(bot, Negocio)
    if cog is None:
        return []
    try:
        tonight, debt, fee = await cog.service.dues(guild_id, user_id)
        payroll, owed = await cog.service.staff_dues(guild_id, user_id)
    except (OSError, sqlite3.Error):
        logger.exception("No se pudo leer lo pendiente del negocio de %s", user_id)
        return []
    items = []
    if tonight:
        items.append(
            Pending(
                Side.PAY, "business_rent", "🔑",
                f"Local de tu negocio esta noche: {format_amount(tonight)} "
                "(alquiler y suministros)",
                tonight, next_daily_at(cog.service.now()),
            )
        )  # fmt: skip
    if debt:
        items.append(
            Pending(
                Side.PAY, "business_debt", "🧾",
                f"Le debes {format_amount(debt)} al casero del local · se cobra cada noche",
                debt,
            )
        )  # fmt: skip
    if payroll:
        items.append(
            Pending(
                Side.PAY, "business_payroll", "👥",
                f"Nóminas de tu plantilla esta noche: {format_amount(payroll)} (con la cuota "
                "de la empresa)",
                payroll, next_daily_at(cog.service.now()),
            )
        )  # fmt: skip
    if owed:
        items.append(
            Pending(
                Side.PAY, "business_wages", "⚠️",
                f"Le debes {format_amount(owed)} a tu plantilla · se paga lo primero cada noche",
                owed,
            )
        )  # fmt: skip
    if fee:
        items.append(
            Pending(
                Side.PAY, "business_fee", "🧾",
                f"Cuota de autónomo de esta semana: {format_amount(fee)} · se cobra esta noche",
                fee,
            )
        )  # fmt: skip
    return items


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con el servicio de negocios del bot."""
    await bot.add_cog(Negocio(bot, bot.negocio))
