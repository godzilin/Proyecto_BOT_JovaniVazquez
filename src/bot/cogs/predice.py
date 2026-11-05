"""Predicciones: `predice`, cuotas fijas sobre preguntas reales de Polymarket.

`/predice` (y `.predice`) abre un panel (componentes de Discord, `PredictView`) con las
preguntas abiertas de cada sección de `bot.services.predice.SECTIONS`: las elecciones
generales de España del 29 de noviembre de 2026, el mundo, la economía y las criptos, y la
cultura y la tecnología.

- Arriba, un botón por sección y 🎟️ **Mis predicciones**. Debajo, las `LISTED` preguntas
  que más se mueven, cada una con sus dos opciones más probables y su botón, y un
  desplegable con las siguientes.
- La ficha de una pregunta enseña la probabilidad de cada opción y el botón de cada lado
  con su cuota. Si el evento tiene varias opciones (el ganador de unas elecciones), un
  desplegable lleva a la de cada una. Cada lado abre un formulario con la cantidad (500, 2k,
  mitad, all…) y apuesta a esa cuota.
- Los precios salen de Polymarket, que da la probabilidad; la casa paga la cuota fija
  `RTP / probabilidad` (`bot.services.predice`, RTP 94 %). Al pulsar un lado se pide el
  precio en vivo (`PolymarketClient.quote`): si la cuota ha empeorado se avisa y no se
  cobra; si es igual o mejor, se apuesta a la cuota que se vio. Solo se ven yapdollars:
  nunca el volumen ni la liquidez de Polymarket, que van en dólares.
- Una predicción por pregunta, y hasta `MAX_STAKE` por evento sumando sus preguntas.

Solo lo maneja quien lo abre. Con `/predice` el panel es efímero; con `.predice` se ve en el
canal. Navegar por él no toca la base de datos: las preguntas viven en memoria
(`Predice.offers`) y se rehacen cada `REFRESH_EVERY` segundos.

Preguntas y resultados. Cada 5 minutos (`tick`) el cog vuelve a pedir las preguntas a
Polymarket si hace falta (`refresh`) y resuelve las predicciones cuyo mercado ya tiene
resultado (`settle`): paga o no, y anuncia el resultado en el canal donde se apostó (solo
menciona a quien recupera dinero). Una pregunta resuelta al 50 % o sin resolver una semana
después de su fin se anula y se devuelve. Si Polymarket no contesta, las predicciones siguen
abiertas hasta la siguiente vuelta.

Traducciones. Polymarket publica en inglés: cada pregunta nueva se traduce al español
(`bot.services.babel.GoogleTranslator`) tras cada actualización, como mucho
`TRANSLATE_PER_REFRESH` a la vez, y se guarda (`bot.repositories.predice`) para no volver a
pedirla. Mientras no esté traducida, se enseña el original.

Dinero (`EconomyService.predict_bet`, `settle_predict_bet` y `void_predict_bet`): lo
apostado espera en el depósito de `predice` y, al resolverse, apuesta y premio entran a la
vez en el día del casino, con la retención del IRPF del juego. Tratamiento fiscal en
`EconomyService.predict_bet`. Lo que espera sale en `saldo` (`pending`). Apostar es gastar:
lleva `renta.remind` y la línea de `renta.hint`.

Logros: sección 🔮 Predicciones del grupo 🎰 Casino (`predict_bet_stats`,
`predict_result_stats`, `predict_void_stats` y `predict_slip_stats`), más lo común del
casino al resolverse (`casino_play` y `apuestas.record`). No admite porras
(`porras.EXCLUDED_GAMES`): se resuelve días después.

Solo se abre en los canales del casino que elige `garito` (sin elegir, los de
`CASINO_CHANNEL_IDS`) y en sus hilos. Permisos del bot en el canal: enviar mensajes e
insertar enlaces. No necesita intents especiales.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import aiohttp
import discord
from discord import app_commands, ui
from discord.ext import commands, tasks

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs.casino import casino_channel_error
from bot.repositories.economy import BetSettlement
from bot.repositories.predice import PredictRepository
from bot.services.achievements import (
    casino_stats,
    predict_bet_stats,
    predict_result_stats,
    predict_slip_stats,
    predict_void_stats,
)
from bot.services.babel import GoogleTranslator, TranslationError, Translator
from bot.services.casino_channels import CasinoChannelIds
from bot.services.casino_stats import mult
from bot.services.economy import (
    CURRENCY_EMOJI,
    BalanceLimitError,
    EconomyService,
    InsufficientFundsError,
    PredictBet,
    PredictBetTakenError,
    PredictStakeCapError,
    format_amount,
    is_all_in,
    parse_amount,
)
from bot.services.levels import TIMEZONE
from bot.services.pending import Pending
from bot.services.pending import Side as PendingSide
from bot.services.pets import Moment
from bot.services.polymarket import Event, Market, PolymarketClient, PolymarketError, Resolution
from bot.services.predice import (
    EVENTS_PER_TAG,
    GAME,
    LOOKAHEAD,
    MAX_STAKE,
    MIN_STAKE,
    SECTION_BY_KEY,
    SECTIONS,
    Offer,
    Option,
    Side,
    Verdict,
    build_sections,
    confirm,
    date_es,
    label,
    offerable_market,
    options,
    payout,
    verdict,
)
from bot.services.telemetry import TimedLock
from bot.utils.channels import send_notice
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit, notify

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

#: Segundos que se puede manejar el panel (menos que los 15 minutos del token de Discord).
VIEW_TIMEOUT = 600
#: Preguntas con su botón en la portada; las siguientes, en un desplegable.
LISTED = 6
MENU_SIZE = 25
#: Opciones con su línea en la ficha de un evento de varias opciones.
OPTION_LINES = 10
#: Predicciones que enseña 🎟️ Mis predicciones.
SLIP_SIZE = 10
#: Predicciones (abiertas y resueltas) que se miran para contar la racha de aciertos.
STREAK_WINDOW = 30
#: Apostantes por mensaje de resultado; el resto sale como «y N más».
RESULT_LINES = 20
#: Segundos que se espera a Polymarket y al traductor.
REQUEST_TIMEOUT_SECONDS = 20
#: Cada cuánto se vuelven a pedir las preguntas a Polymarket (segundos).
REFRESH_EVERY = 15 * 60
#: Cada cuánto se miran las predicciones abiertas para resolverlas (segundos).
SETTLE_EVERY = 10 * 60
#: Textos que se traducen como mucho en cada actualización.
TRANSLATE_PER_REFRESH = 40
#: Pausa entre dos traducciones (segundos): el traductor limita las consultas.
TRANSLATE_PAUSE = 0.5
#: Con qué idiomas se traduce lo de Polymarket.
TRANSLATE_FROM, TRANSLATE_TO = "en", "es"
#: Caracteres de un título en una línea del panel (el panel entero admite 4.000).
TITLE_CLIP = 100
RULES = (
    f"-# Cuota fija al apostar · una predicción por pregunta y de {format_amount(MIN_STAKE)} a "
    f"{format_amount(MAX_STAKE)} por evento, sumando preguntas."
)
VOID_REASONS = {
    "split": "se resolvió al 50 %",
    "expired": "no se resolvió a tiempo",
    "limit": "el premio pasaría el saldo máximo",
}
CLOSED_TEXT = "Esa pregunta ya no admite predicciones."


def odds_text(odds: int) -> str:
    """`151` → `×1,51`."""
    return mult(odds / 100)


def percent_text(share: float) -> str:
    """`0.884` → `88 %`; menos de un 1 % sale como `<1 %`."""
    if share < 0.01:
        return "<1 %"
    return f"{round(share * 100)} %"


def clip(text: str, size: int) -> str:
    """Recorta un texto a `size` caracteres, con «…» si se corta."""
    return text if len(text) <= size else text[: size - 1].rstrip() + "…"


def card(text: str, *, color: discord.Color | int) -> ui.LayoutView:
    """Un bloque sin botones con el texto."""
    view = ui.LayoutView(timeout=None)
    box = ui.Container(accent_colour=color)
    box.add_item(ui.TextDisplay(text))
    view.add_item(box)
    return view


def section_color(key: str) -> discord.Color:
    """El color de una sección (el de la primera si no existe)."""
    section = SECTION_BY_KEY.get(key, SECTIONS[0])
    return discord.Color(section.color)


def question_part(bet: PredictBet) -> str:
    """` — pregunta` de una predicción, o nada si es la misma que el título del evento."""
    if bet.question and bet.question != bet.event_title:
        return f" — {clip(bet.question, 80)}"
    return ""


def bet_what(bet: PredictBet) -> str:
    """`Sí en «Título» — pregunta`: lo que se apostó, como se enseñó al apostar."""
    return f"{bet.side_label} en «{clip(bet.event_title, 80)}»{question_part(bet)}"


def slip_card(bets: list[PredictBet], name: str) -> ui.LayoutView:
    """🎟️ Mis predicciones: las que esperan resultado y las últimas resueltas."""
    view = ui.LayoutView(timeout=None)
    box = ui.Container(accent_colour=section_color(bets[0].section) if bets else SECTIONS[0].color)
    box.add_item(ui.TextDisplay(f"# 🎟️ Predicciones de {clip(name, 40)}"))
    waiting, done = [], []
    for bet in bets:
        what = f"{format_amount(bet.stake)} a {bet_what(bet)}"
        if bet.status == "open":
            waiting.append(
                f"⏳ {what} a {odds_text(bet.odds)} (cobrarías "
                f"{format_amount(payout(bet.stake, bet.odds))}) · se resuelve hacia "
                f"<t:{int(bet.ends)}:R>"
            )
        elif bet.status == "won":
            done.append(
                f"✅ {what} a {odds_text(bet.odds)} · cobraste **{format_amount(bet.payout)}**"
            )
        elif bet.status == "lost":
            done.append(f"❌ {what} a {odds_text(bet.odds)}")
        else:
            done.append(f"↩️ {what} · anulada y devuelta")
    if waiting:
        box.add_item(ui.Separator())
        box.add_item(ui.TextDisplay("### Esperando resultado\n" + "\n".join(waiting)))
    if done:
        box.add_item(ui.Separator())
        box.add_item(ui.TextDisplay("### Últimas resueltas\n" + "\n".join(done)))
    if not bets:
        box.add_item(ui.TextDisplay("Aún no has hecho ninguna predicción. Elige una pregunta."))
    view.add_item(box)
    return view


@dataclass(frozen=True, slots=True)
class Resolved:
    """Una predicción recién resuelta, para el mensaje del resultado y los logros.

    Attributes:
        bet: La predicción ya cerrada (`won`, `lost` o `void`).
        outcome: Cómo salió la pregunta (`Sí`, `No`…), o `None` si no hay ganador.
        reason: Por qué se anuló (clave de `VOID_REASONS`); vacío si no se anuló.
        settlement: Lo que movió en el día del casino (`None` si se anuló).
        balance: Saldo de su dueño al terminar.
    """

    bet: PredictBet
    outcome: str | None
    reason: str
    settlement: BetSettlement | None
    balance: int


def result_message(resolved: list[Resolved]) -> str:
    """Mensaje del resultado de una pregunta para los que apostaron en un canal."""
    first = resolved[0]
    bet = first.bet
    lines = [f"🔮 **{clip(bet.event_title, TITLE_CLIP)}**{question_part(bet)}"]
    if first.outcome is not None:
        lines.append(f"Resultado: **{first.outcome}**")
    elif all(r.bet.status == "void" for r in resolved):
        lines.append("Sin resultado: se devuelve lo apostado.")
    for item in resolved[:RESULT_LINES]:
        done = item.bet
        what = f"{done.side_label} a {odds_text(done.odds)}"
        if done.status == "won":
            tax = item.settlement.tax_delta if item.settlement is not None else 0
            irpf = f" · 🐶 IRPF −{format_amount(tax)}" if tax > 0 else ""
            lines.append(
                f"✅ <@{done.user_id}> {what}: cobra **{format_amount(done.payout)}** "
                f"(+{format_amount(done.payout - done.stake)}){irpf}"
            )
        elif done.status == "lost":
            lines.append(f"❌ <@{done.user_id}> {what}: pierde {format_amount(done.stake)}")
        else:
            why = f" ({VOID_REASONS[item.reason]})" if item.reason in VOID_REASONS else ""
            lines.append(f"↩️ <@{done.user_id}> recupera {format_amount(done.payout)}{why}")
    if len(resolved) > RESULT_LINES:
        lines.append(f"-# Y {len(resolved) - RESULT_LINES} predicciones más.")
    return "\n".join(lines)


def win_streak(bets: list[PredictBet]) -> int:
    """Predicciones acertadas seguidas, de la última resuelta hacia atrás (sin anuladas)."""
    settled = sorted(
        (b for b in bets if b.status in ("won", "lost")),
        key=lambda b: (b.settled_at or 0.0, b.id),
        reverse=True,
    )
    streak = 0
    for bet in settled:
        if bet.status != "won":
            break
        streak += 1
    return streak


# -- Panel ------------------------------------------------------------------------------


class StakeModal(discord.ui.Modal):
    """Pide cuánto apostar a un lado de una pregunta."""

    amount: discord.ui.TextInput = discord.ui.TextInput(
        label="¿Cuánto?", placeholder="500, 2k, mitad, all…", max_length=20
    )

    def __init__(self, view: PredictView, offer: Offer, market: Market, option: Option) -> None:
        name = view.cog.option_name(market) if not offer.single else ""
        head = f"{option.label} {odds_text(option.odds)}"
        super().__init__(title=clip(f"{head} · {name}" if name else head, 45))
        self.panel = view
        self.event_id = offer.event.id
        self.market_id = market.id
        self.side = option.side
        self.odds = option.odds

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Hace la predicción."""
        await self.panel.cog.place(
            interaction,
            event_id=self.event_id,
            market_id=self.market_id,
            side=int(self.side),
            seen=self.odds,
            text=str(self.amount.value),
        )


class PredictView(ui.LayoutView):
    """Panel de `predice`: sección, pregunta, opción y lado.

    Todo sale de memoria (`Predice.offers`), así que cada clic se contesta directamente con
    `edit_message`. Los botones llevan `custom_id` fijo.

    Args:
        cog: El cog, que tiene las preguntas en memoria y apuesta.
        owner_id: Quien abrió el panel (el único que lo maneja).
        section: Sección que se enseña.
    """

    def __init__(self, cog: Predice, *, owner_id: int, section: str) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.owner_id = owner_id
        self.section = section
        self.event_id: str | None = None
        self.market_id: str | None = None
        self.message: discord.Message | discord.InteractionMessage | None = None
        self.rebuild()

    # -- Qué se enseña -----------------------------------------------------------------

    def selected(self) -> Offer | None:
        """La pregunta elegida, si sigue abierta."""
        if self.event_id is None:
            return None
        return next(
            (o for o in self.cog.open_offers(self.section) if o.event.id == self.event_id), None
        )

    def chosen(self, offer: Offer) -> Market | None:
        """El mercado que se mira: el único del evento o la opción elegida."""
        if offer.single:
            return offer.markets[0]
        return next((m for m in offer.markets if m.id == self.market_id), None)

    def rebuild(self) -> None:
        """Rehace el panel con la sección, la pregunta y la opción elegidas."""
        self.clear_items()
        offer = self.selected()
        if offer is None:
            self.event_id = self.market_id = None
        market = self.chosen(offer) if offer is not None else None
        if offer is not None and market is None:
            self.market_id = None
        key = offer.section if offer is not None else self.section
        box = ui.Container(accent_colour=section_color(key))
        if offer is None:
            self._home(box)
        elif offer.single or market is None:
            self._event_page(box, offer)
        else:
            self._market_page(box, offer, market)
        self.add_item(box)

    def _button(
        self,
        label_text: str,
        custom_id: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        disabled: bool = False,
    ) -> ui.Button:
        button: ui.Button = ui.Button(
            label=label_text[:80], style=style, disabled=disabled, custom_id=custom_id
        )
        button.callback = callback  # type: ignore[method-assign]
        return button

    # -- Portada -----------------------------------------------------------------------

    def _home(self, box: ui.Container) -> None:
        """La portada de una sección: sus preguntas con su botón y el resto en un desplegable."""
        offers = self.cog.open_offers(self.section)
        section = SECTION_BY_KEY[self.section]
        box.add_item(
            ui.TextDisplay(
                f"🔮 **Predicciones** · {section.label}\n"
                "Los precios vienen de Polymarket y la casa paga la cuota que ves. Tu "
                "predicción se resuelve sola cuando Polymarket resuelve la pregunta.\n"
                f"-# {self.cog.age_text()}"
            )
        )
        row: ui.ActionRow = ui.ActionRow()
        for other in SECTIONS:
            current = other.key == self.section
            row.add_item(
                self._button(
                    other.label,
                    f"predice:seccion:{other.key}",
                    self._section_callback(other.key),
                    style=(
                        discord.ButtonStyle.primary if current else discord.ButtonStyle.secondary
                    ),
                    disabled=current,
                )
            )
        row.add_item(self._button("🎟️ Mis predicciones", "predice:mis", self._slip))
        box.add_item(row)
        box.add_item(ui.Separator())
        if not offers:
            text = (
                "Cargando preguntas…"
                if self.cog.offers_at is None
                else "Ahora mismo no hay preguntas abiertas en esta sección. Mira otra o "
                "vuelve más tarde."
            )
            box.add_item(ui.TextDisplay(text))
        for offer in offers[:LISTED]:
            text = (
                f"**{clip(self.cog.title(offer.event), TITLE_CLIP)}**\n"
                f"{self._top_line(offer)}\n"
                f"-# cierra <t:{int(offer.closes)}:R>"
            )
            box.add_item(
                ui.Section(
                    text,
                    accessory=self._button(
                        "Ver",
                        f"predice:evento:{offer.event.id}",
                        self._open_callback(offer.event.id),
                        style=discord.ButtonStyle.success,
                    ),
                )
            )
        rest = offers[LISTED : LISTED + MENU_SIZE]
        if rest:
            select: ui.Select = ui.Select(
                placeholder="Más preguntas…",
                options=[
                    discord.SelectOption(
                        label=clip(self.cog.title(o.event), 100),
                        value=o.event.id,
                        description=clip(self._top_line(o), 100),
                    )
                    for o in rest
                ],
                custom_id="predice:mas",
            )
            select.callback = self._pick_event(select)  # type: ignore[method-assign]
            box.add_item(ui.ActionRow(select))
        box.add_item(ui.Separator())
        box.add_item(ui.TextDisplay(RULES))

    def _top_line(self, offer: Offer) -> str:
        """`PP 88 % · PSOE 9 %` (las dos opciones más probables) o `Sí 62 %`."""
        if offer.single:
            market = offer.markets[0]
            return f"{label(market.outcomes[0])} {percent_text(market.mid or 0.0)}"
        return " · ".join(
            f"{self.cog.option_name(m)} {percent_text(m.mid or 0.0)}" for m in offer.markets[:2]
        )

    # -- Ficha del evento --------------------------------------------------------------

    def _event_page(self, box: ui.Container, offer: Offer) -> None:
        """La ficha de una pregunta: su probabilidad y sus lados (o sus opciones)."""
        event = offer.event
        title = clip(self.cog.title(event), 200)
        lines = [f"## {title}", f"cierra <t:{int(offer.closes)}:R>"]
        if offer.single:
            market = offer.markets[0]
            question = self.cog.question_text(market)
            if question != title:
                lines.append(question)
            lines.append(self._sides_line(market))
        head = "\n".join(lines)
        if event.image:
            box.add_item(ui.Section(head, accessory=ui.Thumbnail(event.image)))
        else:
            box.add_item(ui.TextDisplay(head))
        if offer.single:
            box.add_item(self._side_row(offer.markets[0]))
        else:
            shown = offer.markets[:OPTION_LINES]
            text = "\n".join(
                f"**{clip(self.cog.option_name(m), 80)}** — {percent_text(m.mid or 0.0)}"
                for m in shown
            )
            if len(offer.markets) > len(shown):
                text += f"\n-# y {len(offer.markets) - len(shown)} más en el desplegable"
            box.add_item(ui.TextDisplay(text))
            select: ui.Select = ui.Select(
                placeholder="Elige una opción…",
                options=[
                    discord.SelectOption(
                        label=clip(
                            f"{self.cog.option_name(m)} — {percent_text(m.mid or 0.0)}", 100
                        ),
                        value=m.id,
                    )
                    for m in offer.markets[:MENU_SIZE]
                ],
                custom_id="predice:opcion",
            )
            select.callback = self._pick_option(select)  # type: ignore[method-assign]
            box.add_item(ui.ActionRow(select))
        self._footer(box, offer)

    def _market_page(self, box: ui.Container, offer: Offer, market: Market) -> None:
        """La ficha de una opción de un evento de varias: su probabilidad y sus lados."""
        title = clip(self.cog.title(offer.event), 200)
        name = clip(self.cog.option_name(market), 150)
        box.add_item(
            ui.TextDisplay(
                f"## {title}\n**{name}**\ncierra <t:{int(offer.closes)}:R>\n"
                f"{self._sides_line(market)}"
            )
        )
        box.add_item(self._side_row(market))
        self._footer(box, offer)

    def _footer(self, box: ui.Container, offer: Offer) -> None:
        """Volver, las reglas en Polymarket y la letra pequeña."""
        row: ui.ActionRow = ui.ActionRow()
        row.add_item(self._button("◀ Volver", "predice:volver", self._back))
        row.add_item(
            ui.Button(
                label="Reglas en Polymarket",
                style=discord.ButtonStyle.link,
                url=f"https://polymarket.com/event/{offer.event.slug}",
            )
        )
        box.add_item(row)
        box.add_item(ui.TextDisplay(RULES))

    @staticmethod
    def _sides_line(market: Market) -> str:
        """`**Sí 62 %** · No 38 %`."""
        mid = market.mid or 0.0
        return (
            f"**{label(market.outcomes[0])} {percent_text(mid)}** · "
            f"{label(market.outcomes[1])} {percent_text(1 - mid)}"
        )

    def _side_row(self, market: Market) -> ui.ActionRow:
        """Los botones `Sí ×1,51` y `No ×2,47` de un mercado (solo los lados con cuota)."""
        row: ui.ActionRow = ui.ActionRow()
        for option in options(market, market.mid or 0.0):
            row.add_item(
                self._button(
                    f"{option.label} {odds_text(option.odds)}",
                    "predice:si" if option.side is Side.YES else "predice:no",
                    self._side_callback(option.side),
                    style=(
                        discord.ButtonStyle.success
                        if option.side is Side.YES
                        else discord.ButtonStyle.danger
                    ),
                )
            )
        return row

    # -- Callbacks ---------------------------------------------------------------------

    def _section_callback(self, key: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            self.section = key
            self.event_id = self.market_id = None
            await self.show(interaction)

        return callback

    def _open_callback(self, event_id: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            self.event_id = event_id
            self.market_id = None
            await self.show(interaction)

        return callback

    def _pick_event(self, select: ui.Select) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            self.event_id = select.values[0]
            self.market_id = None
            await self.show(interaction)

        return callback

    def _pick_option(self, select: ui.Select) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            self.market_id = select.values[0]
            await self.show(interaction)

        return callback

    async def _back(self, interaction: discord.Interaction) -> None:
        # Una opción vuelve a su evento; un evento, a la portada.
        if self.market_id is not None:
            self.market_id = None
        else:
            self.event_id = None
        await self.show(interaction)

    def _side_callback(self, side: Side) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await self._stake(interaction, side)

        return callback

    async def _stake(self, interaction: discord.Interaction, side: Side) -> None:
        # Un formulario tiene que ser la primera respuesta: aquí solo se mira memoria.
        offer = self.selected()
        market = self.chosen(offer) if offer is not None else None
        option = None
        if offer is not None and market is not None and market.mid is not None:
            option = next((o for o in options(market, market.mid) if o.side is side), None)
        if (
            offer is None
            or market is None
            or option is None
            or offer.closes <= self.cog.clock()
            or not offerable_market(market, now=self.cog.clock())
        ):
            await interaction.response.send_message(CLOSED_TEXT, ephemeral=True)
            return
        await interaction.response.send_modal(StakeModal(self, offer, market, option))

    async def _slip(self, interaction: discord.Interaction) -> None:
        await self.cog.slip(interaction)

    async def show(self, interaction: discord.Interaction) -> None:
        """Repinta el panel. Todo sale de memoria: se contesta directamente."""
        self.rebuild()
        await interaction.response.edit_message(view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Este panel es de otra persona. Abre el tuyo con `/predice`.", ephemeral=True
        )
        return False

    async def on_timeout(self) -> None:
        """Apaga los botones al caducar (sin la vista, el panel se quedaría vacío)."""
        if self.message is None:
            return
        for child in self.walk_children():
            if isinstance(child, ui.Button | ui.Select) and getattr(child, "url", None) is None:
                child.disabled = True
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug("No se pudo apagar el panel de predice al caducar")


# -- Cog ----------------------------------------------------------------------------------


class Predice(commands.Cog):
    """Predicciones con cuotas fijas sobre preguntas de Polymarket.

    Args:
        bot: Cliente.
        economy: Economía (el dinero de las predicciones).
        repository: Traducciones de las preguntas.
        casino_channel_ids: Canales del casino (vacío = cualquiera).
        clock: Fuente de tiempo (epoch); inyectable en pruebas.
        session: Sesión HTTP; inyectable en pruebas. Si no se da, se crea al cargar.
        client: Cliente de Polymarket; si no se da, se crea al cargar.
        translator: Traductor (`texto, origen, destino`); si no se da, `GoogleTranslator`.
    """

    def __init__(
        self,
        bot: commands.Bot,
        economy: EconomyService,
        repository: PredictRepository,
        *,
        casino_channel_ids: CasinoChannelIds = frozenset(),
        clock: Callable[[], float] = time.time,
        session: aiohttp.ClientSession | None = None,
        client: PolymarketClient | None = None,
        translator: Translator | None = None,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.repository = repository
        self.casino_channel_ids = casino_channel_ids
        self.clock = clock
        self._session = session
        self._own_session = session is None
        self.client = client
        self.translator = translator
        #: Preguntas abiertas de cada sección (clave de sección) y cuándo se pidieron.
        self.offers: dict[str, list[Offer]] = {}
        self.offers_at: float | None = None
        #: Texto de Polymarket (en inglés) → su traducción al español.
        self.translations: dict[str, str] = {}
        self._settled_at: float | None = None
        self._settling = TimedLock("predice:Predice._settling")

    async def cog_load(self) -> None:
        """Abre la conexión, carga las traducciones guardadas y arranca el bucle."""
        if self._session is None:
            timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
            self._session = aiohttp.ClientSession(timeout=timeout)
        if self.client is None:
            self.client = PolymarketClient(self._session)
        if self.translator is None:
            self.translator = GoogleTranslator(self._session)
        try:
            self.translations = await self.repository.translations()
        except sqlite3.Error:
            # Sin traducciones guardadas se enseña el original hasta la siguiente vuelta.
            logger.exception("No se pudieron leer las traducciones guardadas de predice")
        self._feed.start()

    async def cog_unload(self) -> None:
        """Para el bucle y cierra la conexión si la abrió el cog."""
        self._feed.cancel()
        if self._own_session and self._session is not None:
            await self._session.close()

    # -- Textos y preguntas en memoria -------------------------------------------------

    def title(self, event: Event) -> str:
        """El título del evento en español si está traducido; si no, el original."""
        return self.translations.get(event.title, event.title)

    def question_text(self, market: Market) -> str:
        """La pregunta de un mercado en español si está traducida; si no, la original."""
        return self.translations.get(market.question, market.question)

    def option_name(self, market: Market) -> str:
        """La opción de un mercado: el candidato o la fecha (`date_es`) o su pregunta."""
        if market.group_title:
            return date_es(market.group_title)
        return self.question_text(market)

    def open_offers(self, section: str) -> list[Offer]:
        """Las preguntas de una sección que aún admiten predicciones (de memoria)."""
        now = self.clock()
        return [o for o in self.offers.get(section, []) if o.closes > now]

    def find(self, event_id: str, market_id: str) -> tuple[Offer, Market] | None:
        """Una pregunta y su mercado por sus ids (de memoria), o `None` si ya no está."""
        for offers in self.offers.values():
            for offer in offers:
                if offer.event.id != event_id:
                    continue
                for market in offer.markets:
                    if market.id == market_id:
                        return offer, market
        return None

    def age_text(self) -> str:
        """`Precios de hace 3 min`, o que aún están cargando."""
        if self.offers_at is None:
            return "Cargando preguntas…"
        minutes = max(0, int((self.clock() - self.offers_at) // 60))
        return f"Precios de hace {minutes} min" if minutes else "Precios de hace menos de 1 min"

    # -- Apostar ---------------------------------------------------------------------

    async def place(
        self,
        interaction: discord.Interaction,
        *,
        event_id: str,
        market_id: str,
        side: int,
        seen: int,
        text: str,
    ) -> None:
        """Cobra la predicción del formulario a la cuota que se vio, si el precio sigue valiendo."""
        # Cobrar va a la base de datos y confirmar el precio, a Polymarket: se acepta antes.
        await ack(interaction)
        user = interaction.user
        guild_id = interaction.guild_id
        if guild_id is None or user.bot:
            await notify(interaction, "Solo se hacen predicciones dentro de un servidor.")
            return
        now = self.clock()
        found = self.find(event_id, market_id)
        if found is None or self.client is None:
            await notify(interaction, CLOSED_TEXT)
            return
        offer, market = found
        if offer.closes <= now or not offerable_market(market, now=now):
            await notify(interaction, CLOSED_TEXT)
            return
        try:
            quote = await self.client.quote(market.token_ids[0])
        except PolymarketError:
            quote = None
        if quote is None:
            await notify(
                interaction,
                "No he podido confirmar el precio con Polymarket. No se ha cobrado nada: "
                "inténtalo de nuevo en un momento.",
            )
            return
        side_enum = Side(side)
        confirmation = confirm(seen, side_enum, quote[0], quote[1])
        name = self._bet_name(offer, market, side_enum)
        if confirmation.odds is None:
            if confirmation.live is None:
                await notify(
                    interaction,
                    "Ahora mismo no hay un precio fiable para esa pregunta. No se ha cobrado "
                    "nada: prueba más tarde.",
                )
            else:
                await notify(
                    interaction,
                    f"La cuota ha cambiado: «{name}» paga ahora {odds_text(confirmation.live)} "
                    f"(antes, {odds_text(seen)}). Vuelve a pulsarlo si te sigue valiendo.",
                )
            return
        odds = confirmation.odds
        placed = await self.economy.predict_event_bets(guild_id, user.id, offer.event.id)
        if any(bet.market_id == market.id for bet in placed):
            await notify(interaction, "Ya tienes una predicción a esta pregunta: una por pregunta.")
            return
        room = MAX_STAKE - sum(bet.stake for bet in placed)
        balance = await self.economy.balance(guild_id, user.id)
        try:
            stake = parse_amount(text, balance)
        except ValueError as error:
            await notify(interaction, str(error))
            return
        if is_all_in(text):
            stake = min(stake, room)
        if not MIN_STAKE <= stake <= room:
            await notify(interaction, self._cap_text(room))
            return
        title = self.title(offer.event)
        ticket = PredictBet(
            id=0,
            guild_id=guild_id,
            user_id=user.id,
            channel_id=interaction.channel_id or 0,
            event_id=offer.event.id,
            event_title=title,
            market_id=market.id,
            question=title if offer.single else self.option_name(market),
            side=int(side_enum),
            side_label=label(market.outcomes[side_enum]),
            section=offer.section,
            odds=odds,
            stake=stake,
            ends=market.end or offer.closes,
            created_at=now,
        )
        try:
            bet, balance = await self.economy.predict_bet(ticket, game=GAME, cap=MAX_STAKE)
        except PredictBetTakenError:
            await notify(interaction, "Ya tienes una predicción a esta pregunta: una por pregunta.")
            return
        except PredictStakeCapError as error:
            await notify(interaction, self._cap_text(error.room))
            return
        except InsufficientFundsError as error:
            await notify(interaction, f"No te llega: tienes {format_amount(error.balance)}.")
            return
        lines = [
            f"🔮 Predices **{format_amount(stake)}** a **{name}** a {odds_text(odds)}.",
            f"Si aciertas, cobras **{format_amount(payout(stake, odds))}**. Se resuelve cuando "
            f"lo resuelva Polymarket (hacia <t:{int(bet.ends)}:R>) y el resultado sale en este "
            "canal.",
            f"{CURRENCY_EMOJI} Te quedan **{format_amount(balance)}**",
            "-# 🐶 Lo que ganes cuenta en el casino del día en que se resuelve, con la "
            "retención del IRPF del juego.",
        ]
        # Apostar es un resultado con dinero: la mascota activa puede opinar.
        if hint := await renta.hint(self.bot, guild_id, user.id, Moment()):
            lines.append(hint)
        await notify(interaction, view=card("\n".join(lines), color=section_color(offer.section)))
        # Apostar es gastar: gancho de la Renta (ver Biblia.txt, sección 4).
        await renta.remind(self.bot, interaction)
        await logros.track(
            self.bot,
            guild_id,
            user,
            interaction.channel,
            predict_bet_stats(
                stake=stake,
                odds=odds,
                side=int(side_enum),
                section=offer.section,
                balance_before=balance + stake,
                lead=offer.closes - now,
                when=datetime.fromtimestamp(now, TIMEZONE),
                event_markets=len({b.market_id for b in placed} | {market.id}),
            ),
        )

    def _bet_name(self, offer: Offer, market: Market, side: Side) -> str:
        """`Sí · PSOE` (varias opciones) o `Sí` (una pregunta sola): lo que se apuesta."""
        side_label = label(market.outcomes[side])
        return side_label if offer.single else f"{side_label} · {self.option_name(market)}"

    @staticmethod
    def _cap_text(room: int) -> str:
        """El aviso del tope por evento, con lo que aún cabe."""
        if room < MIN_STAKE:
            return (
                f"Ya llevas {format_amount(MAX_STAKE)} en este evento, el máximo sumando sus "
                "preguntas."
            )
        return (
            f"La predicción va de {format_amount(MIN_STAKE)} a {format_amount(MAX_STAKE)} por "
            f"evento, sumando preguntas: en este te caben {format_amount(room)}."
        )

    async def slip(self, interaction: discord.Interaction) -> None:
        """🎟️ Mis predicciones, en un mensaje privado aparte."""
        await ack(interaction, new_message=True)
        guild_id = interaction.guild_id or 0
        bets = await self.economy.predict_bets(guild_id, interaction.user.id, limit=SLIP_SIZE)
        name = discord.utils.escape_markdown(interaction.user.display_name)
        await edit(interaction, view=slip_card(bets, name))
        await logros.track(
            self.bot, guild_id, interaction.user, interaction.channel, predict_slip_stats()
        )

    # -- Preguntas, traducciones y resultados ------------------------------------------

    @tasks.loop(minutes=5)
    async def _feed(self) -> None:
        try:
            await self.tick()
        except Exception:
            # Una excepción sin capturar pararía la tarea para siempre.
            logger.exception("Error con las predicciones")

    @_feed.before_loop
    async def _before_feed(self) -> None:
        await self.bot.wait_until_ready()

    async def tick(self) -> None:
        """Vuelve a pedir las preguntas si toca y resuelve lo que toque."""
        now = self.clock()
        if self.offers_at is None or now - self.offers_at >= REFRESH_EVERY:
            await self.refresh(now)
        if self._settled_at is None or now - self._settled_at >= SETTLE_EVERY:
            self._settled_at = now
            await self.settle(now)

    async def refresh(self, now: float) -> None:
        """Pide a Polymarket las preguntas de cada sección y traduce las nuevas.

        Si una etiqueta falla, su sección conserva lo que tenía (sin las preguntas que ya
        no admiten predicciones). Si fallan todas, no se cambia la hora de los precios.
        """
        if self.client is None:
            return
        fetched: dict[str, list[Event]] = {}
        failed: set[str] = set()
        for section in SECTIONS:
            events: list[Event] = []
            try:
                for tag in section.tags:
                    events += await self.client.events(
                        tag_slug=tag,
                        closed=False,
                        end_min=now,
                        end_max=now + LOOKAHEAD,
                        order="volume24hr",
                        limit=EVENTS_PER_TAG,
                    )
            except PolymarketError as error:
                logger.warning("Polymarket no da las preguntas de %s: %s", section.key, error)
                failed.add(section.key)
                events = [offer.event for offer in self.offers.get(section.key, [])]
            fetched[section.key] = events
        self.offers = build_sections(fetched, now=now)
        if len(failed) < len(SECTIONS):
            self.offers_at = now
        await self._translate()

    def _untranslated(self) -> list[str]:
        """Los textos de las preguntas que faltan por traducir, sin repetir y en orden."""
        found: dict[str, None] = {}
        for section in SECTIONS:
            for offer in self.offers.get(section.key, []):
                texts = [offer.event.title]
                if offer.single and offer.markets[0].question != offer.event.title:
                    texts.append(offer.markets[0].question)
                for text in texts:
                    if text and text not in self.translations:
                        found[text] = None
        return list(found)

    async def _translate(self) -> None:
        """Traduce y guarda hasta `TRANSLATE_PER_REFRESH` textos; para al primer error."""
        if self.translator is None:
            return
        for index, source in enumerate(self._untranslated()[:TRANSLATE_PER_REFRESH]):
            if index:
                await asyncio.sleep(TRANSLATE_PAUSE)
            try:
                text = await self.translator(source, TRANSLATE_FROM, TRANSLATE_TO)
            except TranslationError as error:
                logger.info("El traductor ha parado la ronda de predice: %s", error)
                return
            self.translations[source] = text
            try:
                await self.repository.save_translation(source, text, now=self.clock())
            except sqlite3.Error:
                logger.exception("No se pudo guardar una traducción de predice")

    async def settle(self, now: float) -> list[Resolved]:
        """Resuelve las predicciones cuyo mercado ya tiene resultado o ha caducado.

        Returns:
            Lo resuelto en esta vuelta (también se anuncia y se cuenta).
        """
        if self.client is None:
            return []
        async with self._settling:
            bets = await self.economy.open_predict_bets()
            if not bets:
                return []
            markets: dict[str, Market | None] = {}
            for market_id in sorted({b.market_id for b in bets}):
                try:
                    markets[market_id] = await self.client.market(market_id)
                except PolymarketError as error:
                    # Esa pregunta espera a la siguiente vuelta; las demás siguen.
                    logger.warning("Polymarket no da el mercado %s: %s", market_id, error)
            resolved = []
            for bet in bets:
                if bet.market_id not in markets:
                    continue
                market = markets[bet.market_id]
                result = verdict(market, Side(bet.side), ends=bet.ends, now=now)
                if result is Verdict.PENDING:
                    continue
                if (done := await self._close(bet, result, market)) is not None:
                    resolved.append(done)
        groups: dict[tuple[int, int, str], list[Resolved]] = {}
        for item in resolved:
            key = (item.bet.guild_id, item.bet.channel_id, item.bet.market_id)
            groups.setdefault(key, []).append(item)
        for group in groups.values():
            await self._announce(group)
            await self._count(group)
        return resolved

    async def _close(
        self, bet: PredictBet, result: Verdict, market: Market | None
    ) -> Resolved | None:
        """Paga o anula una predicción; `None` si otro ya la había cerrado."""
        winner = market.winner() if market is not None else None
        outcome = (
            label(market.outcomes[winner]) if market is not None and winner is not None else None
        )
        reason = ""
        if result is not Verdict.VOID:
            prize = payout(bet.stake, bet.odds) if result is Verdict.WON else 0
            try:
                done = await self.economy.settle_predict_bet(
                    bet.guild_id, bet.id, payout=prize, game=GAME
                )
            except BalanceLimitError:
                logger.warning("La predicción %s pasaría el saldo máximo; se anula.", bet.id)
                reason = "limit"
            else:
                if done is None:
                    return None
                closed, settlement = done
                return Resolved(closed, outcome, "", settlement, settlement.balance)
        elif market is not None and market.resolution is Resolution.RESOLVED:
            reason = "split"
        else:
            reason = "expired"
        voided = await self.economy.void_predict_bet(bet.guild_id, bet.id, game=GAME)
        if voided is None:
            return None
        closed, balance = voided
        return Resolved(closed, outcome, reason, None, balance)

    async def _announce(self, group: list[Resolved]) -> None:
        """El resultado en el canal donde se apostó; menciona a quien cobra o recupera."""
        channel = self.bot.get_channel(group[0].bet.channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            return
        pinged = [r.bet.user_id for r in group if r.bet.payout > 0][:RESULT_LINES]
        mentions = discord.AllowedMentions(
            everyone=False, roles=False, users=[discord.Object(id=u) for u in pinged]
        )
        view = card(result_message(group), color=section_color(group[0].bet.section))
        try:
            await channel.send(view=view, allowed_mentions=mentions)
        except discord.HTTPException:
            logger.warning("No se pudo anunciar el resultado de %s", group[0].bet.market_id)

    async def _count(self, group: list[Resolved]) -> None:
        """Logros y estadísticas del casino de cada predicción resuelta."""
        for item in group:
            bet = item.bet
            guild = self.bot.get_guild(bet.guild_id)
            member = guild.get_member(bet.user_id) if guild is not None else None
            if member is None:
                continue
            channel = self.bot.get_channel(bet.channel_id)
            if item.settlement is None:
                await logros.track(self.bot, bet.guild_id, member, channel, predict_void_stats())
                continue
            net = bet.payout - bet.stake
            streak = 0
            if bet.status == "won":
                recent = await self.economy.predict_bets(
                    bet.guild_id, bet.user_id, limit=STREAK_WINDOW
                )
                streak = win_streak(recent)
            delta = predict_result_stats(
                stake=bet.stake,
                odds=bet.odds,
                side=bet.side,
                payout=bet.payout,
                streak=streak,
                waited=(bet.settled_at or bet.created_at) - bet.created_at,
                tax=item.settlement.tax_delta,
            )
            delta.merge(
                casino_stats(
                    stake=bet.stake,
                    net=net,
                    balance_after=item.balance,
                    tax_delta=item.settlement.tax_delta,
                )
            )
            await logros.casino_play(self.bot, bet.guild_id, member, channel, delta, net=net)
            await apuestas.record(
                self.bot,
                bet.guild_id,
                member,
                game=GAME,
                stake=bet.stake,
                net=net,
                balance_after=item.balance,
                tax=item.settlement.tax_delta,
            )

    # -- Comandos --------------------------------------------------------------------

    def _view(self, owner_id: int) -> PredictView:
        """Un panel nuevo, en la primera sección con preguntas."""
        section = next((s.key for s in SECTIONS if self.open_offers(s.key)), SECTIONS[0].key)
        return PredictView(self, owner_id=owner_id, section=section)

    @app_commands.command(
        name="predice",
        description="Apuesta a preguntas reales: elecciones, política, economía y cultura.",
    )
    @app_commands.guild_only()
    async def predice(self, interaction: discord.Interaction) -> None:
        """Abre el panel de predicciones (efímero). Sin permisos especiales."""
        if error := casino_channel_error(
            self.casino_channel_ids, interaction.channel, "Las predicciones"
        ):
            await interaction.response.send_message(error, ephemeral=True)
            return
        view = self._view(interaction.user.id)
        await interaction.response.send_message(view=view, ephemeral=True)
        view.message = await interaction.original_response()

    @commands.command(name="predice")
    @commands.guild_only()
    async def predice_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.predice`): el panel se ve en el canal."""
        if error := casino_channel_error(self.casino_channel_ids, ctx.channel, "Las predicciones"):
            await send_notice(ctx, error)
            return
        view = self._view(ctx.author.id)
        view.message = await ctx.send(view=view)


async def pending(bot: commands.Bot, guild_id: int, user_id: int) -> list[Pending]:
    """Las predicciones de alguien que esperan resultado (para `saldo`).

    Nunca lanza: si algo falla se registra y `saldo` sale sin estas líneas.
    """
    cog = find_cog(bot, Predice)
    if cog is None:
        return []
    try:
        bets = await cog.economy.predict_bets(guild_id, user_id, limit=SLIP_SIZE)
    except (OSError, sqlite3.Error):
        logger.exception("No se pudieron leer las predicciones de %s", user_id)
        return []
    return [
        Pending(
            PendingSide.HELD,
            "predict_bet",
            "🔮",
            f"{format_amount(bet.stake)} a {bet_what(bet)} (cobrarías "
            f"{format_amount(payout(bet.stake, bet.odds))})",
            bet.stake,
            bet.ends,
        )
        for bet in bets
        if bet.status == "open"
    ]


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía y el repositorio de traducciones del bot."""
    await bot.add_cog(
        Predice(bot, bot.economy, bot.predice, casino_channel_ids=bot.casino_channels)
    )
