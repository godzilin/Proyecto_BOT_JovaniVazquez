"""Allá tú: `allatu`, 22 cajas, un gordo sorpresa y Perro Sanxe al teléfono.

Cada jugador juega su propio plató, que solo él puede pulsar. `allatu 500
picante` cobra 500 Y$ y juega con la tabla Picante. Al empezar gira la ruleta
del gordo (casi siempre ×10, a veces ×250, ×500 o ×1.000 según la tabla). Luego
se elige una caja, que no se abre hasta el final, y se abren las demás por
rondas de 6, 5, 4, 3 y 2. Tras cada ronda llama la banca con una oferta: ✅
Trato (cobras y acaba), ❌ No trato (sigues) o, una vez por partida, 🤝 Regatear
(la mitad de las veces sube la oferta; la otra mitad cuelga y esa ronda no hay
oferta). Si se abre el gordo a mitad de ronda, llama en el acto con la oferta de
esa ronda: el pésame, para irse sin abrir más cajas. Al final quedan dos cajas:
🔒 te quedas la tuya o 🔄 la cambias.

**Cada jugada es un GIF** (`bot.services.allatu_scene`): la ruleta del gordo,
cada caja que se abre de cerca, la llamada de Perro Sanxe con su marcador, el
sello del trato, el regateo y el final a dos cajas. Después del GIF queda un PNG
con el plató (o la llamada) y los botones vuelven. Los botones de las cajas
abiertas enseñan su premio, y el texto lleva lo que no se ve: la ronda, si el
gordo sigue vivo, la media de lo que queda y el historial de ofertas.

**Todo se pinta antes del clic** (`AllatuBoard.prepare`). Lo de dentro de cada
caja se sortea al empezar, y la animación de abrir una caja no depende de cuándo
se abra, así que en cuanto empieza la partida se pintan las 22 (una por caja),
de una en una y por detrás de lo urgente. Además, según el momento: al elegir
caja, el plató de cada caja posible (las 22 imágenes en un solo dibujo) y la
ruleta de la tabla siguiente; con una oferta, el GIF del trato, el del regateo
(su suerte ya está sorteada) y el plató del no trato; en el final, los de
quedarse y cambiar; al acabar, la ruleta de la partida siguiente (🔁). Lo que
depende de la caja que se abre (el plató después y la llamada de la banca) se
pinta mientras se ve el GIF de la caja. Si al pulsar aún no está, el plató apaga
los botones al momento y espera.

El mensaje es un embed con el texto y la imagen, como los demás juegos del
casino, con las cajas en cuatro filas de cinco y una quinta con las dos últimas
y los controles (Discord admite 25 botones). No usa `MediaGallery`: al cambiar
del GIF al PNG, Discord de PC repetía el GIF viejo (ver la Torre del dragón). Si
no hay Node ni navegador para pintar, el plató se queda en texto y cada clic es
una edición instantánea.

Reglas en `bot.services.allatu`. Dinero: la apuesta se cobra al empezar
(`place_bet`) y se paga al acabar (`pay_winnings`), que es cuando se ajusta el
IRPF del día (juego, art. 33.1 y 33.5.d LIRPF). Si el plató caduca (3 min sin
tocarlo) o el bot se apaga de forma ordenada con una partida a medias, se acepta
la oferta en juego (o la que tocaría en ese momento); si aún no se había elegido
caja, se devuelve la apuesta.

Solo se juega en los canales del casino que elige `garito` (sin elegir, los de `CASINO_CHANNEL_IDS`)
y en sus hilos. Permisos del bot en el canal: enviar mensajes y adjuntar archivos.
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
import secrets
import time
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs.casino import casino_channel_error, insufficient_text
from bot.services.achievements import allatu_stats, casino_stats
from bot.services.allatu import (
    BOXES,
    DEFAULT_TABLE,
    OFFER_FACTORS,
    ROUNDS,
    TABLE_BY_KEY,
    TABLES,
    AllatuError,
    AllatuGame,
    Ending,
    Phase,
    Table,
    format_chance,
    format_multiplier,
    offer_mood,
    parse_table,
    tier,
)
from bot.services.allatu_scene import AllatuScene, Media
from bot.services.casino_channels import CasinoChannelIds
from bot.services.economy import (
    BalanceLimitError,
    BetSettlement,
    EconomyService,
    InsufficientFundsError,
    format_amount,
    gambling_tax_line,
    parse_amount,
)
from bot.services.pets import bet_moment
from bot.services.telemetry import TimedLock
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

#: Clave del juego: motivo del libro de la economía y de `casino_stats.GAMES`.
GAME = "allatu"
DEFAULT_STAKE = 100
BOARD_TIMEOUT = 180
GIF_NAME = "allatu.gif"
PNG_NAME = "allatu.png"
#: Margen tras el GIF antes de poner el PNG: lo que tarda el cliente en bajarlo.
REVEAL_MARGIN_SECONDS = 0.5
#: Desde este multiplicador cobrado (veces la apuesta) se anuncia en el canal.
SHOUT_MULT = 20
#: Rechazar una oferta de este múltiplo de la apuesta y acabar perdiendo se anuncia.
SHAME_MULT = 5
#: Botones de caja por fila (cuatro filas de cinco y la quinta con las dos últimas).
PER_ROW = 5

COLOR_PLAYING = discord.Color.from_rgb(88, 101, 242)
COLOR_OFFER = discord.Color.from_rgb(255, 196, 0)
COLOR_WON = discord.Color.from_rgb(67, 181, 129)
COLOR_LOST = discord.Color.from_rgb(180, 60, 40)

# Textos en el tono de Jovani Vázquez y de Perro Sanxe, el de la banca.
RATA_LINES = (
    "Perro Sanxe te ofrece las sobras del Falcon.",
    "Oferta rata.",
    "Esto es lo que hay. Tómalo o déjalo.",
    "Una oferta de risa.",
    "Ni para pipas.",
    "Perro Sanxe lo llama «escudo social». Tú, limosna.",
    "Te está tomando el pelo.",
    "Oferta de chiste: ¿qué? ¿qué? ¡toma mangó!",
)
NORMAL_LINES = (
    "Una oferta normalita.",
    "Perro Sanxe te mira por encima de las gafas. Es su oferta seria.",
    "Ni regalo ni timo.",
    "La banca juega a la contra. ¿Te fías?",
    "Una oferta razonable.",
    "Piénsalo bien.",
    "Oferta de consenso: no le gusta a nadie, pero se firma.",
    "Hazme café… que esto hay que pensarlo. ¡Quiero café!",
)
GENEROSA_LINES = (
    "¡Oferta generosa! A Perro Sanxe se le ha caído la cartera.",
    "La banca está asustada. ¡Toma mangó!",
    "¡Esto ya es una oferta de verdad!",
    "Perro Sanxe suda la gota gorda.",
    "La banca tiene miedo del gordo.",
    "Oferta con aroma a indulto: tómala antes de que la recurran.",
    "¡No te lo vuelve a dar!",
    "Cógelo y corre.",
)
DEAL_LINES = (
    "¡Trato hecho!",
    "¡Trato! Firmado más rápido que un decreto ley.",
    "¡Trato! Perro Sanxe guarda el teléfono y suspira.",
    "¡Trato! Hacienda ya te está mirando.",
    "¡Trato! ¿Con pulpa? ¡Sin pulpa!",
    "¡Trato! Y la caja, sin abrir.",
    "¡Trato! Más vale pájaro en mano.",
    "¡Trato! Te bajas del plató con la maleta.",
)
WIN_LINES = (
    "¡Toma mangó!",
    "¡Tu caja valía oro!",
    "¡Ni el Gordo de Navidad reparte así!",
    "¡Hasta Jesús Vázquez aplaude!",
    "¡Lo tenías tú!",
    "¡La caja era la buena!",
    "¡Perro Sanxe tiene que pedir un rescate!",
)
LOSE_LINES = (
    "Allá tú.",
    "Te lo dijo Perro Sanxe y no le hiciste caso.",
    "¡Caja vacía, como las promesas electorales!",
    "Estás destruido… pero por dentro estás nuevo.",
    "La caja no era.",
    "Te la jugaste y salió mal.",
    "Sofía, cállate, que no quiero oírlo.",
)
GORDO_ALIVE = (
    "🔥 El gordo sigue vivo: **{gordo}**.",
    "🔥 El {gordo} sigue en alguna caja. Perro Sanxe suda.",
    "🔥 Aún queda el {gordo}.",
)
GORDO_DEAD = (
    "💀 El gordo ya salió. Ahora se juega por la dignidad.",
    "💀 El {gordo} se fue a otra caja.",
    "💀 Sin gordo: queda lo que queda.",
)
#: El pésame: la llamada fuera de ronda al abrir el gordo.
CONDOLENCE_LINES = (
    "🕯️ Ha salido el gordo y Perro Sanxe llama antes de tiempo.",
    "🕯️ Llamada fuera de ronda: la banca ha visto salir el gordo.",
    "🕯️ Perro Sanxe te acompaña en el sentimiento. Y en la cartera.",
    "🕯️ Perro Sanxe llama para darte el pésame y una salida.",
    "🕯️ Sin gordo, la banca se adelanta con su oferta.",
    "🕯️ El gordo se ha ido, pero la partida sigue.",
)
HANG_LINES = (
    "📵 Perro Sanxe ha colgado. Esta ronda, sin oferta.",
    "📵 Ha colgado: dice que le llama Bruselas.",
    "📵 ¡Tut, tut, tut! Te has pasado regateando.",
)
UP_LINES = (
    "🤝 ¡Sube la oferta! Perro Sanxe la saca del Falcon.",
    "🤝 ¡Regateo ganado! Ni en el Rastro te lo dejan así.",
    "🤝 La banca cede.",
)
SHOUT_DEAL = "📣 📦 ¡{who} ha cerrado un trato de **{amount}** ({mult}) en Allá tú!"
SHOUT_BOX = "📣 📦 ¡{who} se lleva **{amount}** ({mult}) con su caja en Allá tú!"
SHAME = "📣 🤡 {who} rechazó **{offer}** de Perro Sanxe y se ha llevado **{amount}**. Allá tú."
SHOUT_GORDO = "📣 🌟 ¡Ha salido un gordo de **{gordo}** en el plató de {who}! ¿Dónde estará?"


def _pick(lines: tuple[str, ...], **kwargs: str) -> str:
    return random.choice(lines).format(**kwargs)


def percent(value: float) -> str:
    """`0,75` → `75 %`."""
    return f"{round(value * 100)} %"


@dataclass(slots=True)
class _Job:
    """Un dibujo por adelantado: su resultado futuro y cómo se pinta."""

    paint: Callable[[], Awaitable[Any]]
    future: asyncio.Future[Any]
    started: bool = False


@dataclass(slots=True)
class _Plan:
    """Lo pintado por adelantado para un momento de la partida (`_plan_key`)."""

    key: tuple[Any, ...]
    jobs: dict[str, _Job] = field(default_factory=dict)


class AllatuBoard(ui.View):
    """El plató de un jugador: el texto, las cajas y los controles.

    Guarda la partida en curso (o la última), la apuesta y la tabla. No guarda
    dinero: se cobra y se paga siempre por la economía.
    """

    def __init__(
        self,
        cog: Allatu,
        *,
        guild_id: int,
        owner: discord.abc.User,
        stake: int,
        table: Table,
    ) -> None:
        super().__init__(timeout=BOARD_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        self.stake = stake
        self.table = table
        self.game: AllatuGame | None = None
        self.balance = 0
        self.note: str | None = None
        self.message: discord.Message | None = None
        self.channel: object = None
        #: Cuándo empezó la partida en curso (para las porras).
        self.started_at = 0
        self._lock = TimedLock("allatu:AllatuBoard._lock")
        self._last_interaction: discord.Interaction | None = None
        #: El adjunto que se ve (`allatu.png` o `allatu.gif`); `None`, el plató de texto.
        self.image: str | None = None
        #: Texto mientras se ve un GIF (no delata el resultado).
        self.waiting: str | None = None
        #: Mientras se enseña una jugada, los clics se aceptan sin hacer nada.
        self._busy = False
        #: La última frase de la banca o del regateo, para el texto.
        self.quip: str | None = None
        self.embed = discord.Embed()
        #: Dibujos por adelantado: los del momento (`_plan`) y el de cada caja.
        self._plan: _Plan | None = None
        self._boxes: tuple[int, dict[int, _Job]] | None = None
        self._wake = asyncio.Event()
        self._painter: asyncio.Task[None] | None = None
        #: Partidas sorteadas por adelantado: la de 🔁 y la de cambiar de tabla.
        self._upcoming: dict[tuple[Any, ...], AllatuGame] = {}

    # -- Texto ------------------------------------------------------------------------

    def _footer(self) -> str:
        game = self.game
        playing = game is not None and game.playing
        table = game.table if game is not None and playing else self.table
        stake = game.stake if game is not None and playing else self.stake
        return (
            f"-# Apuesta {format_amount(stake)} · {table.emoji} {table.label} · "
            f"Saldo {format_amount(self.balance)}"
        )

    def remaining_text(self, game: AllatuGame) -> str:
        """Los premios que siguen en juego, de menor a mayor (el gordo con 🌟)."""
        cells = []
        for value in game.remaining_values:
            text = format_multiplier(value)
            cells.append(f"🌟**{text}**" if value == game.gordo else text)
        return "Quedan: " + " · ".join(cells)

    def _gordo_line(self, game: AllatuGame) -> str:
        lines = GORDO_ALIVE if game.gordo_alive else GORDO_DEAD
        return lines[(len(game.opened) + game.gordo) % len(lines)].format(
            gordo=format_multiplier(game.gordo)
        )

    def _history(self, game: AllatuGame) -> str | None:
        parts = []
        for offer in game.offers:
            if offer.hung_up:
                parts.append("📵")
            elif offer is game.offer:
                parts.append(f"**{format_amount(offer.amount)}**")
            else:
                parts.append(format_amount(offer.amount))
        return "📞 Ofertas: " + " → ".join(parts) if parts else None

    def header(self) -> str:
        """Texto de arriba según la fase de la partida."""
        game = self.game
        lines = [f"### 📦 Allá tú · {self.owner.display_name}"]
        if self.waiting:
            lines += [self.waiting, self._footer()]
            return "\n".join(lines)
        if game is None:
            lines.append("Pulsa 🔁 para jugar.")
        elif game.phase is Phase.PICK:
            gordo = format_multiplier(game.gordo)
            chance = format_chance(game.table.gordo_chance(game.gordo))
            lines.append("# Elige tu caja")
            lines.append(f"🌟 El gordo de hoy: **{gordo}** (sale en el {chance} de las partidas)")
            lines.append(
                "-# Tu caja no se abre hasta el final. Las demás, por rondas, "
                "y tras cada ronda llama Perro Sanxe."
            )
        elif game.phase is Phase.OPEN:
            left = game.left_in_round
            factor = OFFER_FACTORS[game.round]
            lines.append(
                f"# Ronda {game.round + 1}/{len(ROUNDS)} · abre {left} "
                f"{'caja' if left == 1 else 'cajas'}"
            )
            lines.append(self._gordo_line(game))
            lines.append(
                f"-# Media de lo que queda: {format_multiplier(game.mean)} "
                f"({format_amount(game.amount_of(game.mean))}) · La banca ofrecerá el "
                f"{percent(float(factor))} de la media"
            )
            if self.quip:
                lines.append(self.quip)
        elif game.phase is Phase.OFFER:
            offer = game.offer
            assert offer is not None
            mult = format_multiplier(offer.amount / game.stake)
            lines.append(f"# 📞 Perro Sanxe ofrece {format_amount(offer.amount)} ({mult})")
            mood = offer_mood(game)
            pool = {"rata": RATA_LINES, "generosa": GENEROSA_LINES}.get(mood, NORMAL_LINES)
            if offer.condolence:
                pool = CONDOLENCE_LINES
            lines.append(self.quip or pool[(len(game.opened) + offer.amount) % len(pool)])
            lines.append(self._gordo_line(game))
            if history := self._history(game):
                lines.append(history)
            extra = " · 🤝 Puedes regatear una vez" if game.can_haggle else ""
            if offer.condolence:
                extra += " · Con ❌ sigue la ronda"
            lines.append(
                f"-# Media de las cajas cerradas: {format_multiplier(offer.mean)} · "
                f"Oferta al {percent(float(offer.factor))} de la media{extra}"
            )
        elif game.phase is Phase.FINAL:
            assert game.own is not None and game.other is not None
            low, high = sorted((game.values[game.own], game.values[game.other]))
            lines.append(f"# ¿Te quedas la caja {game.own + 1} o cambias por la {game.other + 1}?")
            lines.append(
                f"Quedan dos premios: **{format_multiplier(low)}** y "
                f"**{format_multiplier(high)}**. Uno es tuyo. Allá tú."
            )
            if history := self._history(game):
                lines.append(history)
        else:
            lines += self._result_lines(game)
        if game is not None and game.playing and self.image is None:
            lines.append(self.remaining_text(game))
        if self.note:
            lines.append(self.note)
        lines.append(self._footer())
        if self.balance == 0 and (game is None or not game.playing):
            lines.append("**Estás a cero.** `imv` te recarga.")
        return "\n".join(lines)

    def _result_lines(self, game: AllatuGame) -> list[str]:
        sign = "+" if game.net >= 0 else "-"
        net = f"{sign}{format_amount(abs(game.net))}"
        assert game.own is not None
        own = game.values[game.own]
        own_amount = game.amount_of(own)
        lines = []
        if game.ending is Ending.DEAL:
            lines.append(f"# 🤝 {random.choice(DEAL_LINES)} {net}")
            verdict = "buen trato 👌" if game.payout >= own_amount else "te han timado 🤡"
            lines.append(
                f"Cobras **{format_amount(game.payout)}**. En tu caja había "
                f"**{format_multiplier(own)}** ({format_amount(own_amount)}): {verdict}."
            )
        else:
            emoji = "🌟" if own == game.gordo else ("🎉" if game.net >= 0 else "💸")
            phrase = random.choice(WIN_LINES if game.net >= 0 else LOSE_LINES)
            lines.append(f"# {emoji} {phrase} {net}")
            how = "Cambiaste y en tu caja nueva" if game.ending is Ending.SWAPPED else "En tu caja"
            other = [i for i in game.opened if i != game.own][-1]
            lines.append(
                f"{how} había **{format_multiplier(own)}**: cobras "
                f"**{format_amount(game.payout)}**. La otra tenía "
                f"{format_multiplier(game.values[other])}."
            )
            best = game.best_offer()
            if best is not None and best.amount > game.payout:
                lines.append(f"-# La mejor oferta de Perro Sanxe fue {format_amount(best.amount)}.")
        return lines

    def color(self) -> discord.Color:
        """Color del borde según la fase (al acabar, verde si gana y rojo si pierde)."""
        game = self.game
        if game is None:
            return COLOR_PLAYING
        if game.phase is Phase.OFFER:
            return COLOR_OFFER
        if game.playing:
            return COLOR_PLAYING
        return COLOR_WON if game.net >= 0 else COLOR_LOST

    # -- Botones ----------------------------------------------------------------------

    def _button(
        self,
        label: str,
        custom_id: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        row: int,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        emoji: str | None = None,
        disabled: bool = False,
    ) -> ui.Button:
        button: ui.Button = ui.Button(
            label=label,
            style=style,
            emoji=emoji,
            disabled=disabled,
            custom_id=f"{GAME}:{custom_id}",
            row=row,
        )
        button.callback = callback  # type: ignore[method-assign]
        return button

    def box_button(self, game: AllatuGame, box: int) -> ui.Button:
        """La caja `box`: cerrada, la tuya (🔒) o abierta con su premio."""
        row = box // PER_ROW
        callback = self._box_callback(box)
        if box == game.own:
            return self._button(
                str(box + 1),
                f"caja:{box}",
                callback,
                row=row,
                style=discord.ButtonStyle.primary,
                emoji="🔒",
                disabled=True,
            )
        if box in game.opened:
            value = game.values[box]
            kind = tier(value, game.gordo)
            style = (
                discord.ButtonStyle.danger if kind in ("rojo", "gordo") else
                discord.ButtonStyle.secondary
            )  # fmt: skip
            return self._button(
                format_multiplier(value),
                f"caja:{box}",
                callback,
                row=row,
                style=style,
                emoji="💥" if kind == "gordo" else None,
                disabled=True,
            )
        can = game.phase in (Phase.PICK, Phase.OPEN)
        return self._button(
            str(box + 1), f"caja:{box}", callback, row=row, emoji="📦", disabled=not can
        )

    def rebuild(self) -> None:
        """Vuelve a montar el texto, la imagen y los componentes con el estado actual."""
        self.clear_items()
        game = self.game
        self.embed = discord.Embed(description=self.header(), color=self.color())
        if self.image is not None:
            self.embed.set_image(url=f"attachment://{self.image}")
        green, red, blue = (
            discord.ButtonStyle.success,
            discord.ButtonStyle.danger,
            discord.ButtonStyle.primary,
        )
        if game is None or not game.playing:
            self.add_item(
                self._button(
                    f"🔁 Otra · {format_amount(self.stake)}",
                    "again",
                    self._again,
                    row=0,
                    style=green,
                )
            )
            self.add_item(self._button("½", "half", self._halve, row=0))
            self.add_item(self._button("×2", "x2", self._double, row=0))
            self.add_item(self._button("💰 All-in", "allin", self._all_in, row=0))
            self.add_item(self.table_select())
            return
        for box in range(BOXES):
            self.add_item(self.box_button(game, box))
        last = BOXES // PER_ROW
        if game.phase is Phase.PICK:
            nxt = self._next_table(game.table)
            self.add_item(self._button("🎲 Al azar", "random", self._random, row=last, style=blue))
            self.add_item(self._button("📋 Premios", "premios", self._prizes, row=last))
            self.add_item(
                self._button(f"{nxt.emoji} {nxt.label}", "tabla", self._cycle_table, row=last)
            )
        elif game.phase is Phase.OPEN:
            self.add_item(self._button("🎲 Al azar", "random", self._random, row=last, style=blue))
            self.add_item(self._button("📋 Premios", "premios", self._prizes, row=last))
        elif game.phase is Phase.OFFER:
            offer = game.offer
            assert offer is not None
            self.add_item(
                self._button(
                    f"✅ Trato · {format_amount(offer.amount)}", "trato", self._deal, row=last,
                    style=green,
                )
            )  # fmt: skip
            self.add_item(
                self._button("❌ No trato", "notrato", self._no_deal, row=last, style=red)
            )
            self.add_item(
                self._button(
                    "🤝 Regatear", "regatear", self._haggle, row=last, style=blue,
                    disabled=not game.can_haggle,
                )
            )  # fmt: skip
        elif game.phase is Phase.FINAL:
            assert game.own is not None and game.other is not None
            self.add_item(
                self._button(
                    f"🔒 Me quedo la {game.own + 1}", "quedo", self._keep, row=last, style=green
                )
            )
            self.add_item(
                self._button(
                    f"🔄 Cambio por la {game.other + 1}", "cambio", self._swap, row=last, style=blue
                )
            )

    def table_select(self) -> ui.Select:
        """Menú de tabla para la partida siguiente, con el gordo máximo de cada una."""
        options = [
            discord.SelectOption(
                label=f"{t.emoji} {t.label}",
                value=t.key,
                description=f"Gordo de ×10 hasta {format_multiplier(t.max_gordo)}",
                default=t is self.table,
            )
            for t in TABLES
        ]
        select: ui.Select = ui.Select(
            custom_id=f"{GAME}:tablas", options=options, placeholder="Elige la tabla", row=1
        )

        async def callback(interaction: discord.Interaction) -> None:
            await self._choose_table(interaction, select.values[0])

        select.callback = callback  # type: ignore[method-assign]
        return select

    def disable_all(self) -> None:
        """Apaga todos los botones y el menú (plató caducado o GIF en marcha)."""
        for item in self.walk_children():
            if isinstance(item, ui.Button | ui.Select):
                item.disabled = True

    @staticmethod
    def _next_table(table: Table) -> Table:
        return TABLES[(TABLES.index(table) + 1) % len(TABLES)]

    # -- Ciclo de vida ----------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño juega en su plató."""
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message(
            f"Este plató es de {self.owner.display_name}. Abre el tuyo con `allatu`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Caduca: si había partida a medias, se cierra sola y se enseña cómo acabó."""
        self.cog.boards.discard(self)
        game = self.game
        was_playing = game is not None and game.playing
        await self.force_settle()
        self._wake.set()
        files: dict[str, Any] = {}
        if self.image is not None and was_playing and self.game is not None:
            png = await self.cog.renderer.still(self.game)
            if png is not None:
                self.image = PNG_NAME
                files["attachments"] = [discord.File(io.BytesIO(png), filename=PNG_NAME)]
        self.waiting = None
        self.rebuild()
        self.disable_all()
        try:
            if self._last_interaction is not None:
                await self._last_interaction.edit_original_response(
                    embed=self.embed, view=self, **files
                )
            elif self.message is not None:
                await self.message.edit(embed=self.embed, view=self, **files)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar el plató de Allá tú", exc_info=True)

    async def force_settle(self) -> None:
        """Cierra la partida a medias sin que nadie pierda: acepta la oferta o devuelve.

        Lo cobrado así cuenta igual que un trato a mano: logros, estadísticas y
        porras (sin el aviso de la Renta, que necesita un clic).
        """
        settled: tuple[AllatuGame, BetSettlement | None] | None = None
        async with self._lock:
            game = self.game
            if game is None or not game.playing:
                return
            if game.phase is Phase.PICK:
                # Ni caja elegida: se devuelve la apuesta (neto 0).
                try:
                    await self.cog.economy.pay_winnings(
                        self.guild_id, self.owner.id, game=GAME, amount=game.stake
                    )
                except BalanceLimitError:
                    logger.warning("No se pudo devolver una apuesta de Allá tú.")
                self.game = None
                return
            game.settle_now()
            settled = game, await self._settle(game)
        await self._after_game(None, *settled)

    # -- Juego ------------------------------------------------------------------------

    async def start(self, game: AllatuGame | None = None) -> str | None:
        """Cobra la apuesta y empieza una partida (la sorteada por adelantado, si se pasa).

        Returns:
            Un mensaje de error para el usuario si no se pudo, o `None`.
        """
        try:
            settlement = await self.cog.economy.place_bet(
                self.guild_id, self.owner.id, game=GAME, stake=self.stake
            )
        except InsufficientFundsError as error:
            return insufficient_text(error.balance, self.stake)
        self.balance = settlement.balance
        # Para las porras: una partida abierta antes del cierre no cuenta.
        self.started_at = int(time.time())
        self.game = game or AllatuGame.new(self.stake, self.table, self.cog.rng)
        self.note = None
        self.quip = None
        self.rebuild()
        return None

    async def _settle(self, game: AllatuGame) -> BetSettlement | None:
        """Paga la partida terminada y deja preparado el resultado."""
        try:
            settlement = await self.cog.economy.pay_winnings(
                self.guild_id, self.owner.id, game=GAME, amount=game.payout
            )
        except BalanceLimitError:
            logger.warning("Premio de Allá tú por encima del saldo máximo; no se paga.")
            self.balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
            return None
        self.balance = settlement.balance
        notes = []
        if tax := gambling_tax_line(settlement):
            notes.append(tax)
        if hint := await renta.hint(
            self.cog.bot,
            self.guild_id,
            self.owner.id,
            bet_moment(stake=game.stake, net=game.net, balance_after=settlement.balance),
        ):
            notes.append(hint)
        self.note = "\n".join(notes) or None
        return settlement

    async def _after_game(
        self,
        interaction: discord.Interaction | None,
        game: AllatuGame,
        settlement: BetSettlement | None,
    ) -> None:
        """Lo que va después de enseñar el final: renta, logros, porras y anuncio.

        Sin `interaction` (se cerró sola al caducar o al apagar) no hay aviso de
        la Renta: lo dará el siguiente clic con dinero.
        """
        if interaction is not None:
            await renta.remind(self.cog.bot, interaction)
        now = datetime.now()
        start, last = await self.cog.play_day(self.guild_id, self.owner.id, now.date())
        delta = allatu_stats(game, when=now, day_start=start, day_last=last)
        balance = settlement.balance if settlement else self.balance
        tax = settlement.tax_delta if settlement else 0
        delta.merge(
            casino_stats(stake=game.stake, net=game.net, balance_after=balance, tax_delta=tax)
        )
        await logros.casino_play(
            self.cog.bot, self.guild_id, self.owner, self.channel, delta, net=game.net
        )
        own_gordo = game.own is not None and game.values[game.own] == game.gordo
        await apuestas.record(
            self.cog.bot,
            self.guild_id,
            self.owner,
            game=GAME,
            stake=game.stake,
            net=game.net,
            balance_after=balance,
            tax=tax,
            details=(
                ("deal", int(game.ending is Ending.DEAL)),
                ("gordo", int(own_gordo)),
                ("started", self.started_at),
            ),
        )
        await self.cog.shout(game, self.owner, self.channel)

    def _box_callback(self, box: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await self._press_box(interaction, box)

        return callback

    async def _press_box(
        self, interaction: discord.Interaction, box: int | None, *, random_pick: bool = False
    ) -> None:
        """Elige tu caja (al empezar) o abre otra; `None` es 🎲."""
        if self._busy or self._lock.locked():
            # Doble clic mientras se enseña el anterior: se ignora.
            await ack(interaction)
            return
        game = self.game
        if game is None or game.phase not in (Phase.PICK, Phase.OPEN):
            await ack(interaction)
            return
        target = game.dice if box is None else box
        if game.phase is Phase.PICK:
            await self._choose(interaction, game, target)
        else:
            await self._open(interaction, game, target, random_pick=random_pick)

    async def _choose(self, interaction: discord.Interaction, game: AllatuGame, box: int) -> None:
        if self.image is None:
            # Sin dibujo solo cambia la memoria: se contesta directamente.
            try:
                game.choose(box)
            except AllatuError:
                await ack(interaction)
                return
            game.reroll_dice(self.cog.rng)
            self._last_interaction = interaction
            self.rebuild()
            await edit(interaction, embed=self.embed, view=self)
            return
        self._busy = True
        try:
            await ack(interaction)
            async with self._lock:
                job = self._take("picks")
                before = deepcopy(game)
                try:
                    game.choose(box)
                except AllatuError:
                    return
                game.reroll_dice(self.cog.rng)
                self._last_interaction = interaction

            async def paint() -> bytes | None:
                pngs = await self._obtain(job, lambda: self.cog.renderer.picks(before))
                return pngs[box] if pngs else None

            await self._present(
                self.editor_for(interaction),
                self._ready(job, lambda pngs: pngs[box]),
                paint,
                None,
                f"# 📦 Te quedas la caja {box + 1}…",
            )
        except discord.HTTPException:
            logger.warning("No se pudo enseñar la caja elegida de Allá tú", exc_info=True)
        finally:
            self._busy = False

    async def _open(
        self, interaction: discord.Interaction, game: AllatuGame, box: int, *, random_pick: bool
    ) -> None:
        if self.image is None:
            await self._open_text(interaction, game, box, random_pick=random_pick)
            return
        self._busy = True
        try:
            await ack(interaction)
            async with self._lock:
                job = self._take_box(game, box)
                snapshot = deepcopy(game)
                try:
                    game.open(box, random_pick=random_pick)
                except AllatuError:
                    return
                game.reroll_dice(self.cog.rng)
                self.quip = None
                self._last_interaction = interaction
                after = deepcopy(game)
            renderer = self.cog.renderer
            then: Callable[[], Awaitable[Media | bytes | None]]
            if after.phase is Phase.OFFER:
                condolence = after.offer is not None and after.offer.condolence
                then, waiting_then = (
                    (lambda: renderer.call(after)),
                    "# 📞 Ring, ring… Perro Sanxe ha visto el gordo"
                    if condolence
                    else "# 📞 Ring, ring… llama Perro Sanxe",
                )
            else:
                then, waiting_then = (lambda: renderer.still(after)), None
            await self._present(
                self.editor_for(interaction),
                self._ready(job),
                lambda: self._obtain(job, lambda: renderer.open_box(snapshot, box)),
                then,
                f"# 📦 Abriendo la caja {box + 1}…",
                waiting_then,
            )
        except discord.HTTPException:
            logger.warning("No se pudo enseñar una caja de Allá tú", exc_info=True)
        finally:
            self._busy = False

    async def _open_text(
        self, interaction: discord.Interaction, game: AllatuGame, box: int, *, random_pick: bool
    ) -> None:
        """`_open` con el plató de texto: sin imagen, la edición es instantánea."""
        async with self._lock:
            try:
                game.open(box, random_pick=random_pick)
            except AllatuError:
                await ack(interaction)
                return
            game.reroll_dice(self.cog.rng)
            self.quip = None
            self._last_interaction = interaction
            self.rebuild()
            await edit(interaction, embed=self.embed, view=self)

    async def _random(self, interaction: discord.Interaction) -> None:
        await self._press_box(interaction, None, random_pick=True)

    async def _deal(self, interaction: discord.Interaction) -> None:
        """✅ Trato: cobra la oferta y acaba."""
        await self._decide(interaction, "deal")

    async def _no_deal(self, interaction: discord.Interaction) -> None:
        """❌ No trato: sigue abriendo cajas (o pasa al final)."""
        await self._decide(interaction, "nodeal")

    async def _haggle(self, interaction: discord.Interaction) -> None:
        """🤝 Regatear: sube la oferta o cuelga (ya sorteado)."""
        await self._decide(interaction, "haggle")

    async def _keep(self, interaction: discord.Interaction) -> None:
        """🔒 Me quedo mi caja."""
        await self._decide(interaction, "keep")

    async def _swap(self, interaction: discord.Interaction) -> None:
        """🔄 Cambio mi caja por la otra."""
        await self._decide(interaction, "swap")

    async def _decide(self, interaction: discord.Interaction, kind: str) -> None:
        """Responde a la banca o al final: trato, no trato, regatear, quedarse o cambiar."""
        if self._busy or self._lock.locked():
            await ack(interaction)
            return
        game = self.game
        need = Phase.FINAL if kind in ("keep", "swap") else Phase.OFFER
        if game is None or game.phase is not need:
            await ack(interaction)
            return
        if kind == "haggle" and not game.can_haggle:
            await ack(interaction)
            return
        self._busy = True
        settlement: BetSettlement | None = None
        try:
            await ack(interaction)
            async with self._lock:
                job = self._take(kind)
                before = deepcopy(game)
                renderer = self.cog.renderer
                paint: Callable[[], Awaitable[Any]]
                if kind == "deal":
                    game.deal()
                    paint, waiting = (lambda: renderer.deal(before)), "# ✅ ¡Trato!…"
                elif kind == "nodeal":
                    game.no_deal()
                    paint, waiting = (lambda: renderer.no_deal(before)), "# ❌ ¡No trato!"
                elif kind == "haggle":
                    up = game.haggle()
                    self.quip = random.choice(UP_LINES if up else HANG_LINES)
                    paint, waiting = (lambda: renderer.haggle(before)), "# 🤝 Regateando…"
                else:
                    swap = kind == "swap"
                    game.finish(swap=swap)
                    paint = lambda: renderer.final(before, swap=swap)  # noqa: E731
                    waiting = "# 🔄 Cambias de caja…" if swap else "# 🔒 Te quedas tu caja…"
                if kind == "nodeal":
                    self.quip = None
                if not game.playing:
                    settlement = await self._settle(game)
                self._last_interaction = interaction
            await self._present(
                self.editor_for(interaction),
                self._ready(job),
                lambda: self._obtain(job, paint),
                None,
                waiting,
            )
        except discord.HTTPException:
            logger.warning("No se pudo enseñar la jugada de Allá tú", exc_info=True)
        finally:
            self._busy = False
        if not game.playing:
            await self._after_game(interaction, game, settlement)

    async def _prizes(self, interaction: discord.Interaction) -> None:
        """📋 Premios: la tabla y las probabilidades del gordo, en privado."""
        game = self.game
        table = game.table if game is not None else self.table
        base = " · ".join(format_multiplier(v) for v in table.base)
        gordo = "\n".join(
            f"- 🌟 **{format_multiplier(m)}**: {format_chance(table.gordo_chance(m))}"
            for m, _ in table.gordo
        )
        factors = " → ".join(percent(float(f)) for f in OFFER_FACTORS)
        text = (
            f"## {table.emoji} Tabla {table.label}\n"
            f"**Las 21 cajas de siempre:** {base}\n"
            f"**La caja 22, el gordo,** se sortea al empezar:\n{gordo}\n\n"
            f"Una caja vale de media el 99 % de lo apostado. La banca ofrece la media "
            f"de lo que queda por {factors} según la ronda: quien llega al final "
            f"cobra el 99 % y quien acepta antes, menos. Regatear (una vez) sube un 10 % "
            f"la mitad de las veces; la otra mitad, cuelga. Si abres el gordo a mitad "
            f"de ronda, Perro Sanxe llama en el acto con la oferta de esa ronda."
        )
        await interaction.response.send_message(text, ephemeral=True)

    async def _again(self, interaction: discord.Interaction) -> None:
        """🔁 Otra: cobra otra vez y empieza con la partida sorteada por adelantado."""
        if self._busy or self._lock.locked():
            await ack(interaction)
            return
        async with self._lock:
            if self.game is not None and self.game.playing:
                await ack(interaction)
                return
            await ack(interaction)
            job = self._take("again")
            game = self._upcoming.pop(("again", self.stake, self.table.key), None)
            error = await self.start(game)
            if error is not None:
                await notify(interaction, error)
                return
            self._last_interaction = interaction
        await self._open_game(interaction, job)
        await renta.remind(self.cog.bot, interaction)

    async def _cycle_table(self, interaction: discord.Interaction) -> None:
        """Cambia la tabla de la partida recién empezada (aún sin caja): otro gordo.

        La apuesta ya está cobrada y no se mueve dinero: se rehace la partida.
        """
        if self._busy or self._lock.locked():
            await ack(interaction)
            return
        game = self.game
        if game is None or game.phase is not Phase.PICK:
            await ack(interaction)
            return
        async with self._lock:
            await ack(interaction)
            table = self._next_table(game.table)
            job = self._take(f"tabla:{table.key}")
            fresh = self._upcoming.pop(("tabla", id(game), table.key), None)
            self.table = table
            self.cog.set_table(self.guild_id, self.owner.id, table)
            self.game = fresh or AllatuGame.new(game.stake, table, self.cog.rng)
            self._last_interaction = interaction
        await self._open_game(interaction, job)

    async def _open_game(self, interaction: discord.Interaction, job: _Job | None) -> None:
        """Enseña la ruleta del gordo de la partida nueva y deja elegir caja."""
        game = self.game
        assert game is not None
        if self.image is None:
            self.rebuild()
            await edit(interaction, embed=self.embed, view=self)
            return
        await self.show_opening(self.editor_for(interaction), job)

    async def show_opening(
        self, editor: Callable[..., Awaitable[Any]], job: _Job | None = None
    ) -> None:
        """La ruleta del gordo de la partida en curso (la pintada por adelantado, si está).

        La usan 🔁, el cambio de tabla y el comando (con `message.edit`).
        """
        game = self.game
        assert game is not None
        snapshot = deepcopy(game)
        self._busy = True
        try:
            await self._present(
                editor,
                self._ready(job),
                lambda: self._obtain(job, lambda: self.cog.renderer.opening(snapshot)),
                None,
                "# 🎡 Sorteando el gordo…",
            )
        except discord.HTTPException:
            logger.warning("No se pudo enseñar la ruleta de Allá tú", exc_info=True)
        finally:
            self._busy = False
        await self.cog.shout_gordo(game, self.owner, self.channel)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        """Repinta el texto tras cambiar apuesta o tabla (sin dinero de por medio)."""
        await ack(interaction)
        self.balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
        self.note = None
        self._last_interaction = interaction
        self.rebuild()
        await edit(interaction, embed=self.embed, view=self)
        # La apuesta o la tabla de 🔁 han cambiado: se pinta la partida nueva.
        self.prepare()

    def _idle(self) -> bool:
        return (self.game is None or not self.game.playing) and not self._busy

    async def _halve(self, interaction: discord.Interaction) -> None:
        if not self._idle():
            await ack(interaction)
            return
        self.stake = max(1, self.stake // 2)
        await self._refresh(interaction)

    async def _double(self, interaction: discord.Interaction) -> None:
        if not self._idle():
            await ack(interaction)
            return
        await ack(interaction)
        balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
        # Si el doble no cabe, se queda en todo el saldo.
        self.stake = max(1, min(self.stake * 2, balance))
        await self._refresh(interaction)

    async def _all_in(self, interaction: discord.Interaction) -> None:
        if not self._idle():
            await ack(interaction)
            return
        await ack(interaction)
        balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
        if balance <= 0:
            await notify(interaction, insufficient_text(0))
            return
        self.stake = balance
        await self._refresh(interaction)

    async def _choose_table(self, interaction: discord.Interaction, key: str) -> None:
        """Menú de tabla (entre partidas): cambia la de la partida siguiente."""
        if not self._idle() or key not in TABLE_BY_KEY:
            await ack(interaction)
            return
        self.table = TABLE_BY_KEY[key]
        self.cog.set_table(self.guild_id, self.owner.id, self.table)
        await self._refresh(interaction)

    # -- Imagen -----------------------------------------------------------------------

    def _hold(self, waiting: str, *, image: str | None = None) -> None:
        """Apaga los controles sin rehacerlos y pone el texto de espera (y `image`).

        Rehacerlos delataría el resultado: las etiquetas nuevas (el premio en el
        botón de la caja, ✅ Trato · 840 Y$) saldrían antes que el GIF. Por lo
        mismo, el embed conserva el color de antes.
        """
        self.waiting = waiting
        self.embed.description = self.header()
        if image is not None:
            self.embed.set_image(url=f"attachment://{image}")
        self.disable_all()

    @staticmethod
    def _ready(job: _Job | None, extract: Callable[[Any], Any] = lambda r: r) -> Any:
        """Lo pintado por adelantado, si ya está; `None` si hay que esperarlo."""
        if job is None or not job.future.done():
            return None
        result = job.future.result()
        return extract(result) if result is not None else None

    def editor_for(self, interaction: discord.Interaction) -> Callable[..., Awaitable[Any]]:
        """Cómo se cambia el mensaje desde un clic (desde el comando, `message.edit`)."""

        async def editor(**kwargs: Any) -> None:
            await edit(interaction, **kwargs)

        return editor

    async def _present(
        self,
        editor: Callable[..., Awaitable[Any]],
        ready: Media | bytes | None,
        first: Callable[[], Awaitable[Media | bytes | None]],
        then: Callable[[], Awaitable[Media | bytes | None]] | None,
        waiting: str,
        waiting_then: str | None = None,
    ) -> None:
        """Enseña una jugada: el GIF (o el PNG) pintado por adelantado o `first`, y luego `then`.

        `then` se empieza a pintar ya, mientras se ve lo primero (el plató tras
        abrir una caja o la llamada de la banca). Si lo primero aún no está
        pintado (`ready` es `None`), el plató apaga los botones al momento (si
        no, tras el clic no cambia nada y parece que no responde) y espera. Si no
        hay con qué pintar, sigue con texto.
        """
        if self.image is None:
            # El plató de texto: nada que pintar, una sola edición.
            self.waiting = None
            self.rebuild()
            await editor(embed=self.embed, view=self)
            return
        then_task = asyncio.ensure_future(then()) if then is not None else None
        self._hold(waiting)
        media = ready
        if media is None:

            async def show_waiting() -> None:
                try:
                    await editor(embed=self.embed, view=self)
                except discord.HTTPException:
                    logger.warning("No se pudo apagar el plató de Allá tú", exc_info=True)

            _, media = await asyncio.gather(show_waiting(), first())
        if media is None:
            await self._to_text(editor, then_task)
            return

        async def put(data: bytes, name: str) -> None:
            await editor(
                embed=self.embed,
                view=self,
                attachments=[discord.File(io.BytesIO(data), filename=name)],
            )

        final: bytes | None
        if isinstance(media, Media):
            self._hold(waiting, image=GIF_NAME)
            await put(media.gif, GIF_NAME)
            self.image = GIF_NAME
            # Lo siguiente ya se sabe: se pinta mientras se ve este GIF.
            self.prepare()
            await asyncio.sleep(media.seconds + REVEAL_MARGIN_SECONDS)
            final = media.png
        else:
            final = media
        if then_task is not None:
            nxt = await then_task
            if isinstance(nxt, Media):
                self._hold(waiting_then or waiting, image=GIF_NAME)
                await put(nxt.gif, GIF_NAME)
                await asyncio.sleep(nxt.seconds + REVEAL_MARGIN_SECONDS)
                final = nxt.png
            elif nxt is not None:
                final = nxt
        if final is None:
            await self._to_text(editor, None)
            return
        self.waiting = None
        self.image = PNG_NAME
        self.rebuild()
        await put(final, PNG_NAME)
        self.prepare()

    async def _to_text(
        self, editor: Callable[..., Awaitable[Any]], pending: asyncio.Future[Any] | None
    ) -> None:
        """Ni Node ni navegador: el plató sigue con texto."""
        if pending is not None:
            pending.cancel()
        self.waiting = None
        self.image = None
        self.rebuild()
        await editor(embed=self.embed, view=self, attachments=[])

    # -- Pintar por adelantado ----------------------------------------------------------

    def _plan_key(self) -> tuple[Any, ...]:
        """De qué depende lo pintado por adelantado (salvo el botón que se pulse)."""
        game = self.game
        if game is not None and game.playing:
            return ("play", id(game), game.phase.value, len(game.opened), len(game.offers))
        return ("idle", self.stake, self.table.key)

    def _plan_jobs(self) -> dict[str, Callable[[], Awaitable[Any]]]:
        """Lo que puede pedir el próximo clic, sin las cajas (van aparte)."""
        renderer = self.cog.renderer
        game = self.game
        if game is None or not game.playing:
            key = ("again", self.stake, self.table.key)
            nxt = self._upcoming.get(key) or AllatuGame.new(self.stake, self.table, self.cog.rng)
            # Solo se guarda la partida que puede pedir el próximo clic.
            self._upcoming = {key: nxt}
            return {"again": lambda: renderer.opening(nxt)}
        snap = deepcopy(game)
        if game.phase is Phase.PICK:
            table = self._next_table(game.table)
            key = ("tabla", id(game), table.key)
            alt = self._upcoming.get(key) or AllatuGame.new(game.stake, table, self.cog.rng)
            self._upcoming = {key: alt}
            return {
                "picks": lambda: renderer.picks(snap),
                f"tabla:{table.key}": lambda: renderer.opening(alt),
            }
        self._upcoming = {}
        if game.phase is Phase.OFFER:
            jobs: dict[str, Callable[[], Awaitable[Any]]] = {
                "deal": lambda: renderer.deal(snap),
                "nodeal": lambda: renderer.no_deal(snap),
            }
            if game.can_haggle:
                jobs["haggle"] = lambda: renderer.haggle(snap)
            return jobs
        if game.phase is Phase.FINAL:
            return {
                "keep": lambda: renderer.final(snap, swap=False),
                "swap": lambda: renderer.final(snap, swap=True),
            }
        return {}

    def prepare(self) -> None:
        """Pinta en segundo plano lo que puede pedir el próximo clic, si no lo está ya.

        Lo del momento (ver `_plan_jobs`) va antes; detrás, la animación de cada
        caja de la partida, que vale hasta que acaba. Se pinta de uno en uno y
        nunca se corta a medias: lo que deja de valer y no ha empezado no se pinta.
        """
        if not self.cog.ahead or self.image is None or self.is_finished():
            return
        game = self.game
        key = self._plan_key()
        if self._plan is None or self._plan.key != key:
            old, self._plan = self._plan, _Plan(key)
            if old is not None:
                self._drop(old.jobs.values())
            loop = asyncio.get_running_loop()
            for kind, paint in self._plan_jobs().items():
                self._plan.jobs[kind] = _Job(paint, loop.create_future())
        if (
            game is not None
            and game.playing
            and (self._boxes is None or self._boxes[0] != id(game))
        ):
            if self._boxes is not None:
                self._drop(self._boxes[1].values())
            loop = asyncio.get_running_loop()
            snap = deepcopy(game)
            jobs = {}
            for box in range(BOXES):
                jobs[box] = _Job(self._box_painter(snap, box), loop.create_future())
            self._boxes = (id(game), jobs)
        if self._painter is None or self._painter.done():
            self._painter = asyncio.create_task(self._paint_loop())
        self._wake.set()

    def _box_painter(self, game: AllatuGame, box: int) -> Callable[[], Awaitable[Any]]:
        return lambda: self.cog.renderer.open_box(game, box)

    @staticmethod
    def _drop(jobs: Any) -> None:
        """Lo que ya no vale y no ha empezado: nadie lo va a esperar."""
        for job in jobs:
            if not job.started and not job.future.done():
                job.future.set_result(None)

    def _next_job(self) -> _Job | None:
        """El siguiente dibujo pendiente: primero lo del momento, luego las cajas."""
        if self._plan is not None and self._plan.key == self._plan_key():
            for job in self._plan.jobs.values():
                if not job.started:
                    return job
        game = self.game
        if self._boxes is not None and game is not None and self._boxes[0] == id(game):
            closed = set(game.openable) if game.phase is not Phase.PICK else set(range(BOXES))
            for box, job in self._boxes[1].items():
                if not job.started and box in closed:
                    return job
        return None

    async def _paint_loop(self) -> None:
        while not self.is_finished():
            job = self._next_job()
            if job is None:
                self._wake.clear()
                await self._wake.wait()
                continue
            job.started = True
            result: Any = None
            try:
                result = await job.paint()
            except Exception:
                logger.warning("No se pudo pintar por adelantado Allá tú", exc_info=True)
            if not job.future.done():
                job.future.set_result(result)

    def _take(self, kind: str) -> _Job | None:
        """Lo pintado por adelantado para `kind`, si vale para la partida tal como está."""
        plan = self._plan
        if plan is None or plan.key != self._plan_key():
            return None
        return plan.jobs.get(kind)

    def _take_box(self, game: AllatuGame, box: int) -> _Job | None:
        if self._boxes is None or self._boxes[0] != id(game):
            return None
        return self._boxes[1].get(box)

    async def _obtain(self, job: _Job | None, paint: Callable[[], Awaitable[Any]]) -> Any:
        """El dibujo de `job`: ya hecho, el que está en marcha o pintado ahora mismo."""
        if job is None:
            return await paint()
        if not job.started:
            # Aún no había empezado: se pinta aquí y el pintor de fondo lo salta.
            job.started = True
            result = None
            try:
                result = await paint()
            finally:
                if not job.future.done():
                    job.future.set_result(result)
            return result
        result = await job.future
        return result if result is not None else await paint()


# -- Cog -------------------------------------------------------------------------------


class Allatu(commands.Cog, name="Allá tú"):
    """Allá tú con los yapdollars de la economía del bot."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        casino_channel_ids: CasinoChannelIds = frozenset(),
        rng: random.Random | None = None,
        load_stats: Callable[[int, int], Awaitable[dict[str, int]]] | None = None,
        renderer: AllatuScene | None = None,
        ahead: bool | None = None,
    ) -> None:
        """Prepara el cog; el dibujo por defecto es `AllatuScene`.

        Args:
            load_stats: Lee las estadísticas de logros de un miembro (para la
                racha de días seguidos jugando, que sobrevive a reinicios).
            ahead: Si los platós pintan por adelantado (`AllatuBoard.prepare`).
                Por defecto, solo con el dibujo de verdad.
        """
        self.bot = bot
        self.economy = economy
        self.casino_channel_ids = casino_channel_ids
        # `secrets` usa el azar del sistema operativo: no se puede predecir.
        self.rng = rng or secrets.SystemRandom()
        self.ahead = renderer is None if ahead is None else ahead
        self.renderer = renderer or AllatuScene()
        # Platós abiertos: para cerrar sus partidas si el bot se apaga. Cada
        # plató sale de aquí al caducar, así que el tamaño está acotado.
        self.boards: set[AllatuBoard] = set()
        # Tabla elegida por (servidor, miembro); se pierde al reiniciar.
        self._tables: dict[tuple[int, int], Table] = {}
        # Racha de días por (servidor, miembro): (primer día, último día), en
        # ordinales de fecha. Se lee una vez de los logros y luego va en memoria.
        self._days: dict[tuple[int, int], tuple[int, int]] = {}
        self._load_stats = load_stats

    def table_for(self, guild_id: int, user_id: int) -> Table:
        """Tabla que eligió un miembro la última vez (Normal si no eligió)."""
        return self._tables.get((guild_id, user_id), DEFAULT_TABLE)

    def set_table(self, guild_id: int, user_id: int, table: Table) -> None:
        """Recuerda la tabla de un miembro."""
        self._tables[(guild_id, user_id)] = table

    async def play_day(self, guild_id: int, user_id: int, today: Any) -> tuple[int, int]:
        """Apunta que un miembro ha jugado hoy y devuelve su racha (primer y último día).

        Los dos son ordinales de fecha. Jugar el día siguiente al último alarga la
        racha; saltarse un día la reinicia desde hoy. Como los dos números solo
        crecen, los logros los guardan como máximos (`peak`) sin perder nada.
        """
        key = (guild_id, user_id)
        if key not in self._days:
            start = last = 0
            if self._load_stats is not None:
                try:
                    stats = await self._load_stats(guild_id, user_id)
                    start = stats.get("allatu_day_start", 0)
                    last = stats.get("allatu_day_last", 0)
                except Exception:
                    logger.exception("No se pudo leer la racha de Allá tú de %s", user_id)
            self._days[key] = (start, last)
        start, last = self._days[key]
        day = today.toordinal()
        if last != day:
            start = start if last == day - 1 and start else day
            last = day
        self._days[key] = (start, last)
        return start, last

    async def cog_unload(self) -> None:
        """Al apagar, cierra las partidas a medias (trato o devolución)."""
        for board in list(self.boards):
            try:
                await board.force_settle()
            except Exception:
                logger.exception("No se pudo cerrar una partida de Allá tú al apagar")
            board.stop()
            board._wake.set()
        self.boards.clear()
        await self.renderer.close()

    async def _say(self, channel: object, text: str, user: discord.abc.User) -> None:
        if not isinstance(channel, discord.abc.Messageable):
            return
        try:
            await channel.send(
                text, allowed_mentions=discord.AllowedMentions(users=[user], everyone=False)
            )
        except discord.HTTPException:
            logger.debug("No se pudo anunciar algo de Allá tú", exc_info=True)

    async def shout(self, game: AllatuGame, user: discord.abc.User, channel: object) -> None:
        """Anuncia en el canal los premios enormes y las vergüenzas sonadas."""
        mult = format_multiplier(game.payout / game.stake)
        if game.payout >= SHOUT_MULT * game.stake:
            line = SHOUT_DEAL if game.ending is Ending.DEAL else SHOUT_BOX
            text = line.format(who=user.mention, amount=format_amount(game.payout), mult=mult)
            await self._say(channel, text, user)
            return
        rejected = [o for o in game.offers if not o.accepted and not o.hung_up]
        worst = max((o.amount for o in rejected), default=0)
        if game.ending is not Ending.DEAL and worst >= SHAME_MULT * game.stake and game.net < 0:
            text = SHAME.format(
                who=user.mention, offer=format_amount(worst), amount=format_amount(game.payout)
            )
            await self._say(channel, text, user)

    async def shout_gordo(self, game: AllatuGame, user: discord.abc.User, channel: object) -> None:
        """Anuncia los gordos máximos (los de una partida entre 500) al empezar."""
        if game.gordo == game.table.max_gordo:
            text = SHOUT_GORDO.format(gordo=format_multiplier(game.gordo), who=user.mention)
            await self._say(channel, text, user)

    # -- allatu ----------------------------------------------------------------------

    async def _allatu_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        table: Table | None,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> None:
        """Lógica compartida de `/allatu` y `.allatu`: abre el plató y empieza ya."""
        if guild is None:
            await send_error("Allá tú solo se juega dentro de un servidor.")
            return
        if error := casino_channel_error(self.casino_channel_ids, channel, "Allá tú"):
            await send_error(error)
            return
        balance = await self.economy.balance(guild.id, user.id)
        try:
            stake = (
                parse_amount(amount_text, balance)
                if amount_text
                else max(1, min(DEFAULT_STAKE, balance))
            )
        except ValueError as error:
            await send_error(str(error))
            return
        if balance <= 0 or stake > balance:
            await send_error(insufficient_text(balance, stake))
            return
        if table is not None:
            self.set_table(guild.id, user.id, table)

        board = AllatuBoard(
            self,
            guild_id=guild.id,
            owner=user,
            stake=stake,
            table=self.table_for(guild.id, user.id),
        )
        board.channel = channel
        error = await board.start()
        if error is not None:
            await send_error(error)
            return
        game = board.game
        assert game is not None
        # La apuesta ya está cobrada: si Discord no deja enseñar el plató (la
        # respuesta llega tarde), se devuelve en el acto.
        self.boards.add(board)
        board.image = None if self.renderer.disabled else GIF_NAME
        if board.image is not None:
            board._hold("# 🎡 Sorteando el gordo…")
        else:
            board.rebuild()
        try:
            board.message = await send(embed=board.embed, view=board)
        except discord.HTTPException:
            self.boards.discard(board)
            await board.force_settle()
            raise
        if board.image is not None:
            await board.show_opening(board.message.edit)

    @app_commands.command(
        name="allatu",
        description="Allá tú: 22 cajas, un gordo sorpresa y Perro Sanxe al teléfono.",
    )
    @app_commands.describe(
        cantidad="Apuesta: 500, 2k, all… (por defecto 100)",
        tabla="Tabla de premios (por defecto, la última vez o Normal)",
    )
    @app_commands.choices(
        tabla=[
            app_commands.Choice(
                name=f"{t.emoji} {t.label} · gordo hasta {format_multiplier(t.max_gordo)}",
                value=t.key,
            )
            for t in TABLES
        ]
    )
    @app_commands.guild_only()
    async def allatu(
        self,
        interaction: discord.Interaction,
        cantidad: str | None = None,
        tabla: str | None = None,
    ) -> None:
        """Abre un plató de Allá tú y empieza la partida.

        Solo en los canales del casino que elige `garito` (sin elegir, los de `CASINO_CHANNEL_IDS`)
        y en sus hilos. Cobra la apuesta al empezar y paga al acabar.
        """
        responder = InteractionResponder(interaction)
        try:
            table = parse_table(tabla) if tabla else None
        except ValueError as error:
            await responder.send_error(str(error))
            return

        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            return await interaction.original_response()

        await self._allatu_impl(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            table=table,
            send=send,
            send_error=responder.send_error,
        )
        await renta.remind(self.bot, interaction)

    @commands.command(name="allatu")
    @commands.guild_only()
    async def allatu_text(
        self, ctx: commands.Context, cantidad: str | None = None, tabla: str | None = None
    ) -> None:
        """Versión de texto: `.allatu`, `.allatu 500` o `.allatu 500 picante`."""
        responder = ContextResponder(ctx)
        if cantidad and tabla is None and cantidad.lower() in TABLE_BY_KEY:
            # `.allatu picante` sin cantidad.
            cantidad, tabla = None, cantidad
        try:
            table = parse_table(tabla) if tabla else None
        except ValueError as error:
            await responder.send_error(str(error))
            return

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        await self._allatu_impl(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            amount_text=cantidad,
            table=table,
            send=send,
            send_error=responder.send_error,
        )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""

    async def load_stats(guild_id: int, user_id: int) -> dict[str, int]:
        profile = await bot.achievements.profile(guild_id, user_id)
        return dict(profile.stats)

    await bot.add_cog(
        Allatu(
            bot,
            economy=bot.economy,
            casino_channel_ids=bot.casino_channels,
            load_stats=load_stats,
        )
    )
