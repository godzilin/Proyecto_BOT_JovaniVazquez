"""Casino y economía: `ruleta`, `saldo`, `imv` (la recompensa diaria) y `hacienda`.

Todo el dinero se mueve con `EconomyService` (`bot.economy`), que es la
misma economía que usará cualquier juego o sistema futuro. Este cog solo
traduce botones y comandos a llamadas al servicio y pinta el resultado.

La ruleta es americana (0 y 00) e individual: cada jugador abre su propia
mesa, un mensaje con botones que solo él puede pulsar. Tiene dos modos:

- **Rápido** (por defecto): cada clic en una apuesta cobra, gira y paga.
- **Varias apuestas**: cada clic pone una ficha en la mesa y 🎰 Girar las
  juega todas en la misma tirada.

**Precarga de la tirada siguiente.** Dibujar un giro cuesta ~2 s. Mientras el
jugador mira el resultado, la mesa sortea y dibuja en segundo plano la jugada
más probable: repetir la última apuesta con la ficha actual (`_preload_wagers`,
que es lo que hacen 🔁 Repetir y volver a pulsar el mismo botón). Si el clic
pide justo eso y el historial del servidor no ha cambiado, se cobra y el GIF
sale al momento; si aún se está dibujando, se espera lo que falte. Si pide otra
cosa, la precarga se tira y se juega como siempre (con el «no va más» mientras
se dibuja). Igual que en el pachinko, el dinero no se mueve hasta el clic: el
número y los rayos no miran la apuesta (`roulette.draw`), así que sortearlos
antes da la misma probabilidad, y nadie ve el resultado sin haber pagado.

Cada tirada trae los trucos de las ruletas de casino (`bot.services.roulette`
y `bot.services.roulette_scene`): rayos con multiplicador sobre los plenos,
la bola que pasa por tu número antes de caer al lado, lo recuperado celebrado
como premio y el marcador de números calientes y fríos, con sus botones
🔥 Caliente y ❄️ Frío para apostar a pleno a lo que «toca».

**Skins.** La mesa se pinta con el tapete que el jugador lleva puesto en la
tienda (`shop.skin_params`, juego `tapete` de `bot.services.skins`), y el pie
de la mesa lo nombra. La precarga apunta con qué tapete se pintó: si el
jugador lo cambia entre medias, se tira. La mesa no lleva el botón 🎨: sus 25
botones ya llenan las cinco filas de Discord; el tapete se cambia en la mochila.

**Saldo.** `saldo` junta lo pendiente de cada cog (por pagar, por cobrar y retenido,
`bot.services.pending`) con sus funciones puente: `renta.pending`, `pisos.pending`,
`work.pending`, `lottery.pending`, `sports.pending`, `rastro.pending` y
`patrimonio.wealth_due`. El
saldo de otro enseña lo mismo que si lo pidiera él (sin su mascota).

La ruleta solo se abre en los canales del casino que elige `garito` (sin elegir, los de
`CASINO_CHANNEL_IDS`) y en sus hilos. Permisos que necesita el bot en el canal: enviar mensajes,
insertar enlaces (embeds) y adjuntar archivos (el GIF de la rueda).
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
import secrets
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, patrimonio, pisos, rastro, renta
from bot.cogs import business as negocio
from bot.cogs import pets as mascotas
from bot.cogs import shop as tienda
from bot.cogs import work as curro
from bot.cogs.hacienda_panel import HaciendaPanel, bill_card
from bot.services import skins
from bot.services.achievements import (
    StatDelta,
    casino_stats,
    hacienda_stats,
    roulette_stats,
    saldo_peek_stats,
    saldo_stats,
)
from bot.services.casino_channels import CasinoChannelIds, CasinoChannels, allowed_ids
from bot.services.economy import (
    CURRENCY_EMOJI,
    CURRENCY_NAME,
    IMV_FLOOR_SHARE,
    IMV_WORK_EXEMPT,
    BalanceLimitError,
    EconomyService,
    InsufficientFundsError,
    daily_amount,
    format_amount,
    gambling_tax_line,
    is_all_in,
    monday_epoch,
    next_daily_at,
    parse_amount,
)
from bot.services.levels import TIMEZONE, local_day
from bot.services.pending import Pending, Side, pending_fields
from bot.services.pets import Event, Moment, bet_moment
from bot.services.roulette import (
    COLOR_EMOJI,
    OUTSIDE_BETS,
    Bet,
    Draw,
    RoundOutcome,
    Wager,
    Wheel,
    add_wager,
    color,
    draw,
    hot_cold,
    label,
    parse_bet,
    parse_bets,
    pretty,
    resolve,
)
from bot.services.roulette_scene import GAME as GAME_SKIN
from bot.services.roulette_scene import Media, RouletteScene
from bot.services.tax_report import Snapshot, TaxBill, member_page, server_bill
from bot.services.tax_report import bills as tax_bills
from bot.services.taxes import TAX_COLLECTOR, WEALTH_MINIMUM
from bot.utils.channels import ChannelNotice, wrong_channel
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

GAME = "ruleta"
#: Ficha por defecto al abrir una mesa sin indicar cantidad.
DEFAULT_STAKE = 100
#: Segundos sin pulsar nada tras los que la mesa se cierra.
TABLE_TIMEOUT = 180
#: Margen tras la animación: el cliente tarda un poco en descargar el GIF y
#: empezar a reproducirlo; sin margen se cortaría el final del frenazo.
REVEAL_MARGIN_SECONDS = 0.4
#: Últimos números que se muestran en el pie de la mesa.
HISTORY_SIZE = 12
#: Números que se guardan por servidor para los calientes y los fríos.
HOT_WINDOW = 100
#: Espera antes de precargar tras cambiar la ficha: si llega otro cambio (½ ½ ½),
#: la precarga anterior se cancela antes de tocar el navegador.
PRELOAD_DEBOUNCE_SECONDS = 0.6
#: Cobrar el IMV en estos primeros minutos del día cuenta para `imv_midnight`.
IMV_MIDNIGHT_MINUTES = 5

GIF_NAME = "ruleta.gif"


PNG_NAME = "ruleta.png"

COLOR_IDLE = discord.Color.from_rgb(43, 45, 49)
COLOR_SPIN = discord.Color.from_rgb(222, 178, 70)
COLOR_WIN = discord.Color.from_rgb(255, 196, 0)
COLOR_LOSS = discord.Color.from_rgb(80, 84, 92)

WIN_LINES = (
    "¡Ganas",
    "¡Toma ya!",
    "¡Cobras",
    "¡Dentro!",
    "¡Bingo!",
    "¡Eso es!",
    "La bola te quiere.",
    "Desayuna con huevo y jugo de china del bueno.",
    "La bola se ha portado.",
    "¿Qué? ¿Qué? ¡Toma mangó!",
    "Rien ne va plus… y va todo para ti.",
    "La bola ha elegido bando, y es el tuyo.",
    "Ni el crupier se lo cree.",
    "Le has dado al número. Y al crupier, un disgusto.",
)
BIG_WIN_LINES = (
    "💥 ¡PLENAZO!",
    "💥 ¡REVIENTAS LA BANCA!",
    "💥 ¡LOCURA!",
    "💥 ¡TOMA MANGÓ!",
    "💥 ¡PLENO Y A LA SACA!",
    "💥 ¡JOTA UVE! ¡PRE!",
    "💥 ¡EL CRUPIER PIDE LA BAJA!",
    "💥 ¡QUE LO CANTE EL NIÑO DE SAN ILDEFONSO!",
    "💥 ¡ESTO ES MÁS GORDO QUE EL GORDO!",
    "💥 ¡HASTA LA BOLA ESTÁ SUDANDO!",
)
LOSS_LINES = (
    "Casi.",
    "A la siguiente vuelta.",
    "La bola no quiso.",
    "Uf, por poco.",
    "Otra vez será.",
    "Quisiera ser una mosca para ver adónde va la bola.",
    "Quiero café. Y otra ficha.",
    "Sofía, cállate… que la bola no quiso.",
    "Gracias por tu donativo a la casa.",
    "La bola tenía otros planes.",
    "Hacienda apostó al cero y ganó.",
    "La bola se ha ido a por tabaco.",
    "La bola dimite. Ah, no, que aquí no dimite nadie.",
    "La bola ha hecho ghosting.",
    "Estoy destruido, pero por dentro estoy nuevo.",
)


# -- Presentación -------------------------------------------------------------------


def history_line(history: Iterable[int]) -> str:
    """Últimos resultados con su color, el más reciente primero."""
    items = [f"{COLOR_EMOJI[color(p)]}{label(p)}" for p in list(history)[:HISTORY_SIZE]]
    return "Últimos: " + " · ".join(items) if items else "Aún no ha salido ningún número."


def wagers_total(wagers: Sequence[Wager]) -> int:
    """Suma de las fichas de varias apuestas."""
    return sum(w.stake for w in wagers)


def wagers_lines(wagers: Sequence[Wager]) -> str:
    """Una línea por apuesta: `🔴 Rojo · 100 Y$`."""
    return "\n".join(f"{w.bet.name} · {format_amount(w.stake)}" for w in wagers)


def result_text(outcome: RoundOutcome, rng: random.Random | None = None) -> str:
    """Bloque grande con el número y lo ganado o perdido.

    Con una sola apuesta es una frase; con varias, además, una línea por
    apuesta marcando cuál ha entrado.
    """
    rng = rng or random.Random()
    lines = [f"# {pretty(outcome.pocket)}"]
    if outcome.lucky_hit:
        lines.append(f"## ⚡ ¡RAYO x{outcome.lucky_hit}! +{format_amount(outcome.net)}")
    elif outcome.won:
        if outcome.max_payout >= 8:
            lines.append(f"## {rng.choice(BIG_WIN_LINES)} +{format_amount(outcome.net)}")
        else:
            lines.append(f"### {rng.choice(WIN_LINES)} +{format_amount(outcome.net)}")
    elif outcome.total_return and outcome.net < 0:
        # Ha acertado algo, pero menos de lo apostado en total. Se celebra lo
        # recuperado y la pérdida va debajo, pequeña: el truco de las tragaperras.
        lines.append(f"### ✅ ¡Recuperas {format_amount(outcome.total_return)}!")
        lines.append(
            f"-# Ponías {format_amount(outcome.stake)} en la mesa: "
            f"-{format_amount(-outcome.net)} en total."
        )
    elif outcome.total_return:
        lines.append("### Te quedas igual.")
    elif outcome.near_miss is not None:
        lines.append(
            f"### -{format_amount(outcome.stake)} · ¡Por una casilla! "
            f"Tu {label(outcome.near_miss)} estaba al lado."
        )
    else:
        lines.append(f"### -{format_amount(outcome.stake)} · {rng.choice(LOSS_LINES)}")
    if outcome.lucky:
        rays = " · ".join(f"{pretty(p)} ×{m}" for p, m in outcome.lucky.items())
        lines.append(f"-# ⚡ Rayos: {rays}")

    if len(outcome.wagers) == 1:
        (wager,) = outcome.wagers
        if outcome.won:
            lines.append(
                f"{wager.bet.name} paga {wager.bet.payout_for(outcome.pocket, outcome.lucky)}:1"
            )
        else:
            lines.append(f"Ibas a {wager.bet.name}")
        return "\n".join(lines)

    for wager, returned in zip(outcome.wagers, outcome.returns, strict=True):
        if returned:
            gain = returned - wager.stake
            lines.append(
                f"✅ {wager.bet.name} · {format_amount(wager.stake)} → +{format_amount(gain)}"
            )
        else:
            lines.append(f"❌ {wager.bet.name} · {format_amount(wager.stake)}")
    return "\n".join(lines)


def table_embed(
    *,
    owner: str,
    balance: int,
    stake: int,
    history: Iterable[int],
    outcome: RoundOutcome | None = None,
    streak: int = 0,
    text: str | None = None,
    multi: bool = False,
    slip: Sequence[Wager] = (),
    skin: str | None = None,
) -> discord.Embed:
    """Embed de la mesa en reposo: tras abrirla, tras una tirada o al poner fichas.

    Args:
        text: Texto ya calculado del resultado. Se pasa aparte para que no
            cambie la frase aleatoria al tocar la ficha después de una tirada.
        multi: Si la mesa está en modo varias apuestas.
        slip: Fichas puestas y aún no jugadas (modo varias apuestas).
        skin: El tapete del dueño (`🟥 Tapete granate`), si no es el de serie.
    """
    if outcome is not None:
        description = text or result_text(outcome)
        embed_color = COLOR_WIN if outcome.won else COLOR_LOSS
    elif multi:
        description = "Cada apuesta que pulses pone una ficha. Luego, 🎰 **Girar**."
        embed_color = COLOR_IDLE
    else:
        description = (
            "Pulsa una apuesta y la rueda gira al momento.\n"
            "🎯 **Números** para plenos, caballos, cuadros…\n"
            "⚡ En cada tirada caen rayos: un pleno con rayo cobra de ×50 a ×500.\n"
            "🔥 **Caliente** y ❄️ **Frío**: pleno al número que más sale o al que más tarda.\n"
            "🧩 **Varias** para jugar varias apuestas en la misma tirada."
        )
        embed_color = COLOR_IDLE
    if multi and slip:
        description += (
            f"\n\n**🧩 En la mesa** ({format_amount(wagers_total(slip))})\n{wagers_lines(slip)}"
        )
    elif multi and outcome is not None:
        description += "\n\n🧩 Pon fichas para la siguiente tirada o pulsa 🔁 Repetir."
    if balance == 0:
        description += "\n\n**Estás a cero.** `imv` te recarga."
    embed = discord.Embed(title="🎰 Ruleta americana", description=description, color=embed_color)
    embed.add_field(name="Saldo", value=format_amount(balance))
    embed.add_field(name="Ficha", value=format_amount(stake))
    embed.add_field(name="Racha", value=f"🔥 {streak}" if streak >= 2 else "—")
    embed.set_image(url=f"attachment://{PNG_NAME}")
    table = f"Mesa de {owner}" + (f" · {skin}" if skin else "")
    embed.set_footer(text=f"{table} · {history_line(history)}")
    return embed


def spinning_embed(
    *,
    owner: str,
    wagers: Sequence[Wager],
    history: Iterable[int],
    closed: bool = False,
) -> discord.Embed:
    """Embed mientras la bola gira: solo dice a qué se ha apostado.

    Args:
        closed: El «no va más» de justo después de cobrar, mientras se dibuja el
            giro: la mesa enseña aún su imagen quieta (el PNG) en vez del GIF.
    """
    if len(wagers) == 1:
        bets = f"**{wagers[0].bet.name}** · {format_amount(wagers[0].stake)}"
    else:
        bets = f"{wagers_lines(wagers)}\n**Total** · {format_amount(wagers_total(wagers))}"
    embed = discord.Embed(
        title="🎰 Ruleta americana",
        description=f"# {'🎲 No va más…' if closed else '🌀 Girando…'}\n{bets}",
        color=COLOR_SPIN,
    )
    embed.set_image(url=f"attachment://{PNG_NAME if closed else GIF_NAME}")
    embed.set_footer(text=f"Mesa de {owner} · {history_line(history)}")
    return embed


def insufficient_text(balance: int, needed: int | None = None) -> str:
    """Aviso cuando lo apostado supera el saldo."""
    if balance == 0:
        return "Estás a cero. Usa `imv` para recargar."
    if needed is not None:
        return f"Necesitas {format_amount(needed)} y tienes {format_amount(balance)}."
    return f"No te llega: tienes {format_amount(balance)}. Baja la ficha o pulsa 💰 All-in."


def casino_channel_error(
    channel_ids: CasinoChannelIds, channel: object, game: str = "El casino"
) -> ChannelNotice | None:
    """Aviso si `channel` no es un canal de casino («🎰 Los dados están en #casino»).

    Lo comparten todos los juegos. `channel_ids` es el servicio del bot
    (`bot.casino_channels`: lo que eligió `garito` en el servidor del canal o, si
    no eligió nada, `CASINO_CHANNEL_IDS`) o un conjunto fijo en las pruebas. Sin
    canales se juega en cualquiera; con canales, también en sus hilos.
    """
    guild_id = getattr(getattr(channel, "guild", None), "id", None)
    allowed = allowed_ids(channel_ids, guild_id if isinstance(guild_id, int) else None)
    return wrong_channel(allowed, channel, game, "🎰")


def parse_command_args(
    amount_text: str | None, bet_text: str | None, balance: int
) -> tuple[int, list[Bet]]:
    """Interpreta `ruleta [cantidad] [apuesta + apuesta + …]`.

    La cantidad es la ficha de cada apuesta. Con `all` y varias apuestas, el
    saldo se reparte a partes iguales. Si el primer argumento no es una
    cantidad pero sí una apuesta (`.ruleta rojo + 17`), se juega con la
    ficha por defecto.

    Returns:
        `(ficha, apuestas)`; la lista está vacía si solo se abre la mesa.

    Raises:
        ValueError: Con un mensaje mostrable si algo no se entiende.
    """
    default_stake = max(1, min(DEFAULT_STAKE, balance))
    if not amount_text:
        return default_stake, parse_bets(bet_text) if bet_text else []
    try:
        stake = parse_amount(amount_text, balance)
    except ValueError:
        whole = f"{amount_text} {bet_text or ''}".strip()
        try:
            return default_stake, parse_bets(whole)
        except ValueError:
            if bet_text:
                raise
            raise ValueError(
                f"No entiendo `{amount_text}`. Ejemplos: `ruleta 500`, `ruleta all rojo`, "
                "`ruleta 50 17 + rojo`."
            ) from None
    bets = parse_bets(bet_text) if bet_text else []
    if len(bets) > 1 and is_all_in(amount_text):
        stake = balance // len(bets)
        if stake == 0:
            raise ValueError(insufficient_text(balance, len(bets)))
    return stake, bets


# -- Mesa ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SpinResult:
    """Tirada ya cobrada y pagada, lista para mostrarse."""

    outcome: RoundOutcome
    balance: int
    media: Media
    #: Línea de IRPF de esta tirada (retención o devolución), si la hay.
    tax_note: str | None = None
    #: IRPF retenido (positivo) o devuelto (negativo) en esta tirada.
    tax_delta: int = 0


@dataclass(frozen=True, slots=True)
class PreparedSpin:
    """Una tirada sorteada y dibujada antes del clic, sin cobrar (ver la cabecera).

    Vale solo para las mismas fichas (`wagers`), el mismo historial del
    servidor (`history`, que sale en el marcador del dibujo) y el mismo tapete
    (`skin`).
    """

    wagers: tuple[Wager, ...]
    history: tuple[int, ...]
    outcome: RoundOutcome
    media: Media
    #: Clave de la skin con la que se pintó: si el jugador la cambia, no vale.
    skin: str = "serie"


def _log_preload_failure(task: asyncio.Task[PreparedSpin]) -> None:
    """Registra una precarga que falló (y la da por leída); jugar sigue sin ella."""
    if not task.cancelled() and (error := task.exception()) is not None:
        logger.warning("Falló la precarga de la ruleta: se jugará sin ella", exc_info=error)


EditFn = Callable[..., Awaitable[Any]]


class NumberBetModal(discord.ui.Modal, title="🎯 Apuesta a números"):
    """Formulario para las apuestas interiores (plenos, caballos, cuadros…) y las columnas.

    Las columnas no tienen botón (dejan sitio al 🎨 del tapete): se escriben
    `c1`, `c2` o `c3`. Acepta cualquier apuesta que entienda `parse_bet`.
    """

    numbers: discord.ui.TextInput = discord.ui.TextInput(
        label="Números o columna (c1, c2, c3)",
        placeholder="17 · 0-00 · 17-20 · 13-14-15 · 17-18-20-21 · c1 (1ª columna)",
        max_length=40,
    )

    def __init__(self, table: RouletteTable) -> None:
        super().__init__()
        self.table = table

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Valida la apuesta y la juega (o la pone en la mesa en modo varias)."""
        try:
            bet = parse_bet(self.numbers.value)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await self.table.choose(interaction, bet)


class RouletteTable(discord.ui.View):
    """Mesa de ruleta de un jugador: un mensaje con botones.

    Solo su dueño puede pulsarla. Guarda la ficha actual, el modo (rápido o
    varias apuestas), las fichas puestas sin jugar, la última tirada (para
    repetir/doblar) y la racha. No guarda dinero: el saldo se lee y se
    cambia siempre a través de la economía, y las fichas puestas no se
    cobran hasta pulsar Girar.
    """

    def __init__(self, cog: Casino, *, guild_id: int, owner: discord.abc.User, stake: int) -> None:
        super().__init__(timeout=TABLE_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        self.stake = stake
        self.multi = False
        self.slip: tuple[Wager, ...] = ()
        self.last_wagers: tuple[Wager, ...] = ()
        self.last_outcome: RoundOutcome | None = None
        self.last_text: str | None = None
        self.streak = 0
        #: Apuestas puestas con 🔥 o ❄️ en esta tirada (clave → "hot"/"cold"), para los logros.
        self.hunches: dict[str, str] = {}
        self.message: discord.Message | None = None
        self._last_interaction: discord.Interaction | None = None
        self._busy = False
        #: La tirada siguiente preparándose en segundo plano, si la hay.
        self._preload: asyncio.Task[PreparedSpin] | None = None
        #: Las fichas para las que es esa precarga.
        self._preload_for: tuple[Wager, ...] = ()
        #: Si esa precarga ya ha pasado su espera y está en el navegador.
        self._preload_drawing = False
        self._closed = False
        self._build_buttons()

    # -- Construcción ---------------------------------------------------------------

    def _add(
        self,
        label_text: str,
        row: int,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        custom_id: str,
    ) -> discord.ui.Button:
        button: discord.ui.Button = discord.ui.Button(
            label=label_text, style=style, row=row, custom_id=f"{GAME}:{custom_id}"
        )
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)
        return button

    def _bet_button(self, key: str, text: str, row: int, style: discord.ButtonStyle) -> None:
        bet = OUTSIDE_BETS[key] if key in OUTSIDE_BETS else parse_bet(key)

        async def callback(interaction: discord.Interaction) -> None:
            await self.choose(interaction, bet)

        self._add(text, row, callback, style=style, custom_id=f"bet:{bet.key}")

    def _build_buttons(self) -> None:
        gray, red = discord.ButtonStyle.secondary, discord.ButtonStyle.danger
        green, blue = discord.ButtonStyle.success, discord.ButtonStyle.primary
        self._bet_button("red", "🔴 Rojo", 0, red)
        self._bet_button("black", "⚫ Negro", 0, gray)
        self._bet_button("even", "Par", 0, gray)
        self._bet_button("odd", "Impar", 0, gray)
        self._add("🎯 Números", 0, self._open_numbers, style=blue, custom_id="numbers")
        self._bet_button("low", "1-18", 1, gray)
        self._bet_button("high", "19-36", 1, gray)
        self._bet_button("dozen1", "1-12", 1, gray)
        self._bet_button("dozen2", "13-24", 1, gray)
        self._bet_button("dozen3", "25-36", 1, gray)
        # Las columnas van en 🎯 Números (`c1`…`c3`): su sitio es para el 🎨.
        self._bet_button("0", "🟢 0", 2, green)
        self._bet_button("00", "🟢 00", 2, green)
        if (paint := tienda.skin_button(self.cog.bot, GAME, row=2)) is not None:
            self.add_item(paint)
        self._add("½", 3, self._halve, custom_id="half")
        self._add("×2", 3, self._double_stake, custom_id="x2")
        self._add("💰 All-in", 3, self._all_in, custom_id="allin")
        self.repeat_button = self._add(
            "🔁 Repetir", 3, self._repeat, style=blue, custom_id="repeat"
        )
        self.double_button = self._add(
            "⏫ Doblar", 3, self._double_and_repeat, style=blue, custom_id="double"
        )
        self.mode_button = self._add("🧩 Varias", 4, self._toggle_mode, custom_id="mode")
        self.spin_button = self._add("🎰 Girar", 4, self._spin_slip, style=green, custom_id="spin")
        self.clear_button = self._add("🗑️ Quitar", 4, self._clear_slip, custom_id="clear")
        self._add("🔥 Caliente", 4, self._hot, style=red, custom_id="hot")
        self._add("❄️ Frío", 4, self._cold, style=blue, custom_id="cold")
        self._set_enabled(True)

    def _set_enabled(self, enabled: bool) -> None:
        """Activa o desactiva los botones según el estado de la mesa."""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = not enabled
        self.mode_button.label = "🧩 Varias: sí" if self.multi else "🧩 Varias"
        self.mode_button.style = (
            discord.ButtonStyle.success if self.multi else discord.ButtonStyle.secondary
        )
        if not enabled:
            return
        if not self.last_wagers:
            self.repeat_button.disabled = True
            self.double_button.disabled = True
        self.spin_button.disabled = not (self.multi and self.slip)
        self.clear_button.disabled = not self.slip

    # -- Ciclo de vida --------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño juega en su mesa; al resto se le invita a abrir la suya."""
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message(
            f"Esta mesa es de {self.owner.display_name}. Abre la tuya con `ruleta`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Desactiva los botones cuando la mesa lleva un rato sin usarse."""
        self._closed = True
        if self._preload is not None and not self._preload_drawing:
            self._preload.cancel()
        self._preload = None
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        # El token de la última interacción que editó la mesa vale 15 min y la
        # mesa caduca a los 3 sin uso: sirve aunque se abriera con `/` hace rato.
        # Si no hubo ninguna, la mesa es reciente y vale su propio mensaje.
        try:
            if self._last_interaction is not None:
                await self._last_interaction.edit_original_response(view=self)
            elif self.message is not None:
                await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar la mesa de ruleta", exc_info=True)

    # -- Precarga ---------------------------------------------------------------------

    def _preload_wagers(self) -> tuple[Wager, ...]:
        """La jugada que se precarga: la última, con la ficha actual si era una sola."""
        if len(self.last_wagers) == 1 and not self.multi:
            return (Wager(self.last_wagers[0].bet, self.stake),)
        return self.last_wagers

    def _start_preload(self, history: Sequence[int] | None = None, *, delay: float = 0) -> None:
        """Pone a preparar la tirada siguiente, si hay jugada que repetir.

        Args:
            history: El historial del servidor que tendrá el marcador al jugarla.
                Al empezar el GIF aún no incluye el número que está saliendo.
            delay: Espera antes de dibujar (ver `PRELOAD_DEBOUNCE_SECONDS`).

        La anterior se cancela si aún no ha empezado a dibujar; si ya dibuja, se
        abandona y acaba sola: cortar a Chromium a mitad de un dibujo deja la
        pestaña a medias, y acabar cuesta poco.
        """
        wagers = self._preload_wagers()
        if self._closed or not self.cog.preload_enabled or not wagers:
            return
        if history is None:
            history = self.cog.history(self.guild_id)
        if self._preload is not None and not self._preload_drawing:
            self._preload.cancel()
        self._preload_drawing = False

        async def prepare() -> PreparedSpin:
            if delay:
                await asyncio.sleep(delay)
            self._preload_drawing = True
            return await self.cog.prepare(
                self.guild_id, wagers, history=history, user_id=self.owner.id
            )

        task = asyncio.create_task(
            prepare(), name=f"ruleta-preload-{self.guild_id}-{self.owner.id}"
        )
        task.add_done_callback(_log_preload_failure)
        self._preload = task
        self._preload_for = wagers

    async def _take_preload(self, wagers: Sequence[Wager]) -> PreparedSpin | None:
        """La tirada precargada si es para estas fichas y este historial; si no, `None`.

        Si aún se está dibujando, espera a que acabe. Una que falló o que ya no
        vale se tira: el dinero no se ha movido, así que no se pierde nada.
        """
        task, self._preload = self._preload, None
        target, self._preload_for = self._preload_for, ()
        if task is None:
            return None
        if target != tuple(wagers):
            # Otra jugada: ni se espera. Si aún dibuja, acaba sola y se tira.
            return None
        if not task.done():
            await asyncio.wait({task})
        if task.cancelled() or task.exception() is not None:
            return None
        prepared = task.result()
        if prepared.wagers != tuple(wagers):
            return None
        if prepared.history != tuple(self.cog.history(self.guild_id)):
            return None
        if prepared.skin != self.cog.skin(self.guild_id, self.owner.id)["key"]:
            return None
        return prepared

    # -- Juego ----------------------------------------------------------------------

    async def balance(self) -> int:
        """Saldo actual del dueño de la mesa."""
        return await self.cog.economy.balance(self.guild_id, self.owner.id)

    async def current_embed(self, balance: int | None = None) -> discord.Embed:
        """Embed de reposo con el saldo actual."""
        if balance is None:
            balance = await self.balance()
        return table_embed(
            owner=self.owner.display_name,
            balance=balance,
            stake=self.stake,
            history=self.cog.history(self.guild_id),
            outcome=self.last_outcome,
            streak=self.streak,
            text=self.last_text,
            multi=self.multi,
            slip=self.slip,
            skin=skin.label if (skin := self.cog.worn_skin(self.guild_id, self.owner.id)) else None,
        )

    async def choose(self, interaction: discord.Interaction, bet: Bet) -> None:
        """Respuesta a pulsar una apuesta: girar ya o poner la ficha, según el modo."""
        if not self.multi:
            await self.play(interaction, (Wager(bet, self.stake),))
            return
        if self._busy:
            await ack(interaction)
            return
        await ack(interaction)
        balance = await self.balance()
        needed = wagers_total(self.slip) + self.stake
        if needed > balance:
            await notify(interaction, insufficient_text(balance, needed))
            return
        try:
            self.slip = add_wager(self.slip, bet, self.stake)
        except ValueError as error:
            await notify(interaction, str(error))
            return
        await self._refresh(interaction, balance)

    async def play(self, interaction: discord.Interaction, wagers: Sequence[Wager]) -> None:
        """Cobra, gira y paga las apuestas, editando la mesa."""
        if self._busy:
            # Doble clic mientras gira: se ignora sin mostrar error.
            await ack(interaction)
            return
        self._busy = True
        try:
            # Cobrar y dibujar el giro tardan: se acepta el clic antes.
            await ack(interaction)
            closing: asyncio.Task[None] | None = None

            async def close_bets() -> None:
                # Cobrado: «no va más» mientras se dibuja el giro (~2 s), que el
                # clic se note al momento. A la vez que el dibujo, no antes.
                nonlocal closing
                self._set_enabled(False)
                closing = asyncio.create_task(self._close_bets(interaction, wagers))

            prepared = await self._take_preload(wagers)
            try:
                result = await self.cog.spin(
                    self.guild_id, self.owner.id, wagers, on_paid=close_bets, prepared=prepared
                )
            except InsufficientFundsError as error:
                await notify(interaction, insufficient_text(error.balance, wagers_total(wagers)))
                return
            except BalanceLimitError:
                await notify(interaction, "La banca no puede pagar tanto. Baja la ficha.")
                return
            self._last_interaction = interaction
            if closing is not None:
                # Que el «no va más» no llegue a Discord después del giro.
                await asyncio.gather(closing, return_exceptions=True)
            await self.show_spin(
                result,
                first_edit=interaction.edit_original_response,
                final_edit=interaction.edit_original_response,
            )
        finally:
            self._busy = False
        await renta.remind(self.cog.bot, interaction)

    async def _close_bets(self, interaction: discord.Interaction, wagers: Sequence[Wager]) -> None:
        """Pone la mesa en «no va más», sin tocar la imagen ni esperar al dibujo."""
        try:
            await edit(
                interaction,
                embed=spinning_embed(
                    owner=self.owner.display_name,
                    wagers=wagers,
                    history=self.cog.history(self.guild_id),
                    closed=True,
                ),
                view=self,
            )
        except discord.HTTPException:
            logger.debug("No se pudo poner la mesa en «no va más»", exc_info=True)

    async def show_spin(
        self, result: SpinResult, *, first_edit: EditFn, final_edit: EditFn
    ) -> None:
        """Muestra el GIF del giro y, al acabar, el resultado con su PNG final.

        El dinero ya está cobrado y pagado cuando se llama: si Discord falla
        al editar, el saldo sigue siendo correcto.
        """
        outcome = result.outcome
        self.last_wagers = outcome.wagers
        self.slip = ()
        self._set_enabled(False)
        await first_edit(
            embed=spinning_embed(
                owner=self.owner.display_name,
                wagers=outcome.wagers,
                history=self.cog.history(self.guild_id),
            ),
            attachments=[discord.File(io.BytesIO(result.media.gif), filename=GIF_NAME)],
            view=self,
        )
        # La siguiente se dibuja mientras esta gira: ~4 s de GIF dan para los ~2 s
        # del dibujo. Su marcador ya lleva el número que está saliendo.
        self._start_preload(history=[outcome.pocket, *self.cog.history(self.guild_id)])
        await asyncio.sleep(result.media.seconds + REVEAL_MARGIN_SECONDS)

        self.cog.record(self.guild_id, outcome.pocket)
        previous_pocket = self.last_outcome.pocket if self.last_outcome is not None else None
        self.streak = self.streak + 1 if outcome.won else 0
        self.last_outcome = outcome
        self.last_text = result_text(outcome)
        if result.tax_note:
            self.last_text += f"\n{result.tax_note}"
        if renta_hint := await renta.hint(
            self.cog.bot,
            self.guild_id,
            self.owner.id,
            bet_moment(stake=outcome.stake, net=outcome.net, balance_after=result.balance),
        ):
            self.last_text += f"\n{renta_hint}"
        self._set_enabled(True)
        await final_edit(
            embed=await self.current_embed(result.balance),
            attachments=[discord.File(io.BytesIO(result.media.png), filename=PNG_NAME)],
            view=self,
        )
        # Después de enseñar el número: un aviso de logro antes destriparía la tirada.
        hunches, self.hunches = self.hunches, {}
        delta = roulette_stats(
            outcome, table_streak=self.streak, previous_pocket=previous_pocket, hunches=hunches
        )
        delta.merge(
            casino_stats(
                stake=outcome.stake,
                net=outcome.net,
                balance_after=result.balance,
                tax_delta=result.tax_delta,
            )
        )
        await logros.casino_play(
            self.cog.bot,
            self.guild_id,
            self.owner,
            getattr(self.message, "channel", None),
            delta,
            net=outcome.net,
        )
        await apuestas.record(
            self.cog.bot,
            self.guild_id,
            self.owner,
            game=GAME,
            stake=outcome.stake,
            net=outcome.net,
            balance_after=result.balance,
            tax=result.tax_delta,
        )

    async def _refresh(self, interaction: discord.Interaction, balance: int | None = None) -> None:
        """Actualiza la mesa (ficha, modo, fichas puestas) sin tocar la imagen.

        El embed lee el saldo de la base de datos: se acepta el clic antes.
        Si cambia la jugada que se repetiría (otra ficha, otro modo), se vuelve
        a precargar.
        """
        await ack(interaction)
        self._set_enabled(True)
        await edit(interaction, embed=await self.current_embed(balance), view=self)
        self._last_interaction = interaction
        if self._preload is None or self._preload_for != self._preload_wagers():
            self._start_preload(delay=PRELOAD_DEBOUNCE_SECONDS)

    async def _open_numbers(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(NumberBetModal(self))

    async def _hunch(self, interaction: discord.Interaction, kind: str) -> None:
        """Pleno al número más caliente (`hot`) o al más frío (`cold`) del servidor.

        El historial está en memoria: si no hay, se contesta al momento.
        """
        hot, cold = hot_cold(self.cog.history(self.guild_id))
        picks = hot if kind == "hot" else cold
        if not picks:
            await interaction.response.send_message(
                "Aún no ha salido ningún número en este servidor. Gira una vez y vuelve.",
                ephemeral=True,
            )
            return
        bet = parse_bet(label(picks[0][0]))
        self.hunches[bet.key] = kind
        await self.choose(interaction, bet)

    async def _hot(self, interaction: discord.Interaction) -> None:
        await self._hunch(interaction, "hot")

    async def _cold(self, interaction: discord.Interaction) -> None:
        await self._hunch(interaction, "cold")

    def _free_balance(self, balance: int) -> int:
        """Saldo que queda sin comprometer por las fichas ya puestas."""
        return max(0, balance - wagers_total(self.slip))

    async def _halve(self, interaction: discord.Interaction) -> None:
        self.stake = max(1, self.stake // 2)
        await self._refresh(interaction)

    async def _double_stake(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        balance = await self.balance()
        # Si el doble no cabe, se queda en lo que queda libre: es lo que se busca.
        self.stake = max(1, min(self.stake * 2, self._free_balance(balance)))
        await self._refresh(interaction, balance)

    async def _all_in(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        balance = await self.balance()
        free = self._free_balance(balance)
        if free == 0:
            await notify(interaction, insufficient_text(free))
            return
        # En modo varias, all-in es "todo lo que queda" para la siguiente ficha.
        self.stake = free
        await self._refresh(interaction, balance)

    async def _repeat(self, interaction: discord.Interaction) -> None:
        if self.last_wagers:
            await self.play(interaction, self.last_wagers)
        else:
            # Clic en un botón que ya debía estar apagado: se acepta sin más.
            await ack(interaction)

    async def _double_and_repeat(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        if not self.last_wagers:
            return
        doubled = tuple(Wager(w.bet, w.stake * 2) for w in self.last_wagers)
        balance = await self.balance()
        if wagers_total(doubled) > balance:
            await notify(
                interaction,
                "No te llega para doblar. " + insufficient_text(balance, wagers_total(doubled)),
            )
            return
        self.stake *= 2
        await self.play(interaction, doubled)

    async def _toggle_mode(self, interaction: discord.Interaction) -> None:
        self.multi = not self.multi
        self.slip = ()
        self.hunches = {}
        await self._refresh(interaction)

    async def _spin_slip(self, interaction: discord.Interaction) -> None:
        if self.slip:
            await self.play(interaction, self.slip)
        else:
            await ack(interaction)

    async def _clear_slip(self, interaction: discord.Interaction) -> None:
        self.slip = ()
        self.hunches = {}
        await self._refresh(interaction)


# -- Cog ----------------------------------------------------------------------------


class Casino(commands.Cog):
    """Comandos del casino y de la economía de yapdollars."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        renderer: RouletteScene | None = None,
        wheel: Wheel | None = None,
        casino_channel_ids: CasinoChannelIds = frozenset(),
        preload: bool = True,
    ) -> None:
        self.bot = bot
        self.economy = economy
        #: Si las mesas preparan la tirada siguiente mientras se mira la anterior.
        #: Solo se apaga en pruebas que cuentan los sorteos o los dibujos uno a uno.
        self.preload_enabled = preload
        self.renderer = renderer or RouletteScene()
        self.wheel = wheel or Wheel()
        self.casino_channel_ids = casino_channel_ids
        # Una cola corta por servidor: el tamaño total está acotado por el
        # número de servidores del bot.
        self._history: dict[int, deque[int]] = {}

    async def cog_unload(self) -> None:
        """Cierra el navegador de la ruleta."""
        await self.renderer.close()

    # -- Estado compartido ----------------------------------------------------------

    def history(self, guild_id: int) -> list[int]:
        """Últimos números del servidor, el más reciente primero."""
        return list(self._history.get(guild_id, ()))

    def record(self, guild_id: int, pocket: int) -> None:
        """Añade un número al historial del servidor."""
        self._history.setdefault(guild_id, deque(maxlen=HOT_WINDOW)).appendleft(pocket)

    async def spin(
        self,
        guild_id: int,
        user_id: int,
        wagers: Sequence[Wager],
        *,
        on_paid: Callable[[], Awaitable[None]] | None = None,
        prepared: PreparedSpin | None = None,
    ) -> SpinResult:
        """Juega una tirada: decide el número y mueve el dinero de forma atómica.

        Todas las apuestas se cobran y se pagan en una sola operación. El
        número se decide antes de cobrar, pero solo se muestra si el cobro
        sale bien; así no se puede "ver" el resultado sin pagarlo.

        Args:
            on_paid: Se llama tras cobrar y antes de dibujar el giro (no si
                la tirada ya venía dibujada: no hay espera que tapar).
            prepared: La tirada precargada para estas mismas fichas; se cobra
                y se enseña sin sortear ni dibujar.

        Raises:
            InsufficientFundsError: Si el saldo no cubre el total apostado.
            BalanceLimitError: Si el premio superaría el saldo máximo.
        """
        outcome = prepared.outcome if prepared else resolve(draw(self.wheel), wagers)
        settlement = await self.economy.settle_bet(
            guild_id, user_id, game=GAME, stake=outcome.stake, payout=outcome.total_return
        )
        if prepared is not None:
            media = prepared.media
        else:
            if on_paid is not None:
                await on_paid()
            media = await self.renderer.spin(
                outcome,
                history=self.history(guild_id),
                seed=secrets.randbits(32),
                skin=self.skin(guild_id, user_id),
            )
        return SpinResult(
            outcome=outcome,
            balance=settlement.balance,
            media=media,
            tax_note=gambling_tax_line(settlement),
            tax_delta=settlement.tax_delta,
        )

    async def prepare(
        self,
        guild_id: int,
        wagers: Sequence[Wager],
        *,
        history: Sequence[int],
        user_id: int | None = None,
    ) -> PreparedSpin:
        """Sortea y dibuja una tirada para `wagers` sin cobrar nada (la precarga).

        El sorteo no mira las fichas ni al jugador: hacerlo antes del clic da la
        misma probabilidad. Si la tirada no se juega, se tira sin dejar rastro.

        Args:
            history: El historial del servidor con el que se jugará (sale en el
                marcador); si al jugar es otro, la precarga no vale.
            user_id: Quien jugará: se pinta con su tapete.
        """
        history = tuple(list(history)[:HOT_WINDOW])
        result: Draw = draw(self.wheel)
        outcome = resolve(result, wagers)
        skin = self.skin(guild_id, user_id) if user_id is not None else skins.params(GAME_SKIN)
        media = await self.renderer.spin(
            outcome, history=history, seed=secrets.randbits(32), skin=skin
        )
        return PreparedSpin(tuple(wagers), history, outcome, media, skin=str(skin["key"]))

    def worn_skin(self, guild_id: int, user_id: int) -> skins.Skin | None:
        """La skin de ruleta que lleva este jugador, o `None` (el tapete de serie)."""
        return tienda.skin_of(self.bot, guild_id, user_id, GAME_SKIN)

    def skin(self, guild_id: int, user_id: int) -> dict[str, object]:
        """Los colores del tapete de este jugador (la tienda dice qué skin lleva)."""
        return tienda.skin_params(self.bot, guild_id, user_id, GAME_SKIN)

    def _casino_channel_error(self, channel: object) -> str | None:
        return casino_channel_error(self.casino_channel_ids, channel, "La ruleta")

    # -- ruleta ---------------------------------------------------------------------

    async def _ruleta_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        bet_text: str | None,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> None:
        """Lógica compartida entre `/ruleta` y `.ruleta`.

        Args:
            send: Envía el mensaje de la mesa y devuelve el mensaje creado.
            send_error: Responde con un error (efímero cuando es posible).
        """
        if guild is None:
            await send_error("La ruleta solo se juega dentro de un servidor.")
            return
        if (error := self._casino_channel_error(channel)) is not None:
            await send_error(error)
            return

        balance = await self.economy.balance(guild.id, user.id)
        try:
            stake, bets = parse_command_args(amount_text, bet_text, balance)
        except ValueError as error:
            await send_error(str(error))
            return
        needed = stake * max(1, len(bets))
        if needed > balance:
            await send_error(insufficient_text(balance, needed if len(bets) > 1 else None))
            return

        table = RouletteTable(self, guild_id=guild.id, owner=user, stake=stake)
        if not bets:
            png = await self.renderer.board(
                self.history(guild.id), skin=self.skin(guild.id, user.id)
            )
            table.message = await send(
                embed=await table.current_embed(balance),
                file=discord.File(io.BytesIO(png), filename=PNG_NAME),
                view=table,
            )
            return

        # Apuestas escritas en el propio comando: la mesa nace ya girando.
        # Con varias, la mesa queda en modo varias para seguir igual.
        table.multi = len(bets) > 1
        wagers = tuple(Wager(bet, stake) for bet in bets)
        try:
            result = await self.spin(guild.id, user.id, wagers)
        except InsufficientFundsError as error:
            await send_error(insufficient_text(error.balance, wagers_total(wagers)))
            return
        except BalanceLimitError:
            await send_error("La banca no puede pagar tanto. Baja la ficha.")
            return

        async def first_edit(**kwargs: Any) -> None:
            attachments = kwargs.pop("attachments")
            table.message = await send(files=attachments, **kwargs)

        async def final_edit(**kwargs: Any) -> None:
            assert table.message is not None
            await table.message.edit(**kwargs)

        await table.show_spin(result, first_edit=first_edit, final_edit=final_edit)

    @app_commands.command(name="ruleta", description="Ruleta americana con tus yapdollars.")
    @app_commands.describe(
        cantidad="Ficha por apuesta: 500, 2k, all… (por defecto 100)",
        apuesta="Opcional, gira ya: rojo, 17, d2, 17-20… Varias con +: rojo + 17",
    )
    @app_commands.guild_only()
    async def ruleta(
        self,
        interaction: discord.Interaction,
        cantidad: str | None = None,
        apuesta: str | None = None,
    ) -> None:
        """Abre una mesa de ruleta con botones (o gira directamente con `apuesta`).

        Solo en los canales del casino que elige `garito` (sin elegir, los de `CASINO_CHANNEL_IDS`)
        y en sus hilos. Mueve yapdollars del usuario a través de la economía del bot.
        """

        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            return await interaction.original_response()

        await self._ruleta_impl(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            bet_text=apuesta,
            send=send,
            send_error=InteractionResponder(interaction).send_error,
        )
        await renta.remind(self.bot, interaction)

    @commands.command(name="ruleta")
    @commands.guild_only()
    async def ruleta_text(
        self, ctx: commands.Context, cantidad: str | None = None, *, apuesta: str | None = None
    ) -> None:
        """Versión de texto: `.ruleta`, `.ruleta 500`, `.ruleta all rojo`, `.ruleta 50 17 + rojo`.

        Varias apuestas se separan con `+`; la cantidad es la ficha de cada una.
        """

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        await self._ruleta_impl(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            amount_text=cantidad,
            bet_text=apuesta,
            send=send,
            send_error=ContextResponder(ctx).send_error,
        )

    # -- saldo ----------------------------------------------------------------------

    async def _saldo_impl(
        self, responder: CommandResponder, user: discord.abc.User, target: discord.abc.User | None
    ) -> None:
        if responder.guild is None:
            await responder.send_error("La economía solo funciona dentro de un servidor.")
            return
        who = target or user
        if who.bot:
            await responder.send_error("Los bots no tienen monedero.")
            return
        guild_id = responder.guild.id
        own = who.id == user.id
        balance = await self.economy.balance(guild_id, who.id)
        description = f"{CURRENCY_EMOJI} **{who.display_name}** tiene **{format_amount(balance)}**"
        # La cuenta remunerada: enseñar lo de ayer y lo que va saliendo hoy es lo que
        # hace que se note y que dé pena fundírselo todo.
        preview = await self.economy.interest_preview(guild_id, who.id)
        if preview.yesterday is not None:
            gross, tax = preview.yesterday
            description += (
                f"\n-# 🏦 Ayer cobró {format_amount(gross - tax)} de intereses "
                f"({TAX_COLLECTOR} se llevó {format_amount(tax)})."
            )
        if preview.gross:
            top = (
                f" Le faltan {format_amount(preview.to_top)} de media para el máximo."
                if preview.to_top
                else " Ya cobra el máximo: lo que pase de ahí, ni un Y$."
            )
            description += (
                f"\n-# 🏦 Saldo medio de hoy: {format_amount(preview.average)}. Si no lo toca, "
                f"mañana cobra {format_amount(preview.net)} netos.{top}"
            )
        # De otro se ve igual que si lo pidiera él: todo lo pendiente.
        items = await self._pending(guild_id, who.id, balance)
        if own and (pet := await mascotas.cameo(self.bot, guild_id, who.id, Moment(Event.MONEY))):
            description += f"\n{pet}"
        embed = discord.Embed(description=description, color=COLOR_WIN)
        for name, value in pending_fields(items):
            embed.add_field(name=name, value=value, inline=False)
        await responder.send(embed=embed)
        if own:
            delta = saldo_stats(balance=balance, items=items, when=self.economy.now())
        else:
            mine = await self.economy.balance(guild_id, user.id)
            delta = saldo_peek_stats(own_balance=mine, their_balance=balance)
        logros.note_for(self.bot, responder, delta)

    async def _pending(self, guild_id: int, user_id: int, balance: int) -> list[Pending]:
        """Lo pendiente que sale en `saldo`, el propio o el de otro.

        Cada cog da lo suyo con su función puente; se leen a la vez para que `saldo`
        tarde lo que la lectura más lenta y no la suma de todas.
        """
        # Imports tardíos: `lottery` y `sports` importan este módulo.
        from bot.cogs import lottery as loterias
        from bot.cogs import sports as deportes

        reads = [
            self._wealth_pending(guild_id, user_id, balance),
            renta.pending(self.bot, guild_id, user_id),
            self._imv_pending(guild_id, user_id),
            pisos.pending(self.bot, guild_id, user_id),
            curro.pending(self.bot, guild_id, user_id),
            negocio.pending(self.bot, guild_id, user_id),
            loterias.pending(self.bot, guild_id, user_id),
            deportes.pending(self.bot, guild_id, user_id),
        ]
        items = [item for found in await asyncio.gather(*reads) for item in found]
        return items + rastro.pending(self.bot, guild_id, user_id)

    async def _wealth_pending(self, guild_id: int, user_id: int, balance: int) -> list[Pending]:
        """El Patrimonio que se cobraría el lunes, la misma cuenta que en `perfil`."""
        quote = await patrimonio.wealth_due(self.bot, guild_id, user_id, balance)
        if quote is None or not quote.due:
            return []
        # Se cobra sobre lo que haya el lunes: avisarlo es lo que empuja a gastar antes.
        monday = monday_epoch(local_day(self.economy.now()) + timedelta(days=7))
        return [
            Pending(
                Side.PAY, "wealth", "🐶",
                f"Patrimonio del lunes: {format_amount(quote.due)}, si sigue habiendo más de "
                f"{format_amount(WEALTH_MINIMUM)} entre efectivo, pisos y objetos (lo que no "
                "esté en efectivo, de los alquileres)",
                quote.due, monday,
            )
        ]  # fmt: skip

    async def _imv_pending(self, guild_id: int, user_id: int) -> list[Pending]:
        """El IMV de hoy, si aún no lo ha cobrado, con la racha que se juega."""
        preview = await self.economy.daily_preview(guild_id, user_id)
        if preview is None:
            return []
        amount, streak = preview
        text = f"IMV de hoy sin cobrar: {format_amount(amount)} · `imv`"
        if streak >= 2:
            text += f"; si no lo cobras, pierdes la racha de {streak} días"
        return [Pending(Side.COLLECT, "imv", "💶", text, amount, next_daily_at(self.economy.now()))]

    @app_commands.command(
        name="saldo",
        description=f"Tus {CURRENCY_NAME} y lo que tienes pendiente, o el saldo de otro.",
    )
    @app_commands.describe(miembro="De quién ver el saldo (por defecto, tú)")
    @app_commands.guild_only()
    async def saldo(
        self, interaction: discord.Interaction, miembro: discord.Member | None = None
    ) -> None:
        """Muestra el saldo de yapdollars y lo pendiente; abre el monedero si es la primera vez.

        Con `miembro`, su saldo entero, igual que si lo pidiera él.
        """
        await self._saldo_impl(InteractionResponder(interaction), interaction.user, miembro)

    @commands.command(name="saldo")
    @commands.guild_only()
    async def saldo_text(
        self, ctx: commands.Context, miembro: discord.Member | None = None
    ) -> None:
        """Versión de texto (`.saldo [@miembro]`) de `/saldo`."""
        await self._saldo_impl(ContextResponder(ctx), ctx.author, miembro)

    # -- IMV (recompensa diaria) ----------------------------------------------------

    async def _daily_impl(self, responder: CommandResponder, user: discord.abc.User) -> None:
        """Cobra el IMV, lo enseña y lo cuenta para los logros de Economía."""
        if responder.guild is None:
            await responder.send_error("La economía solo funciona dentro de un servidor.")
            return
        result = await self.economy.claim_daily(responder.guild.id, user.id)
        next_at = f"<t:{int(result.next_claim_at)}:R>"
        if result.abroad:
            await responder.send_error(
                "🇭🇰 Vives en Hong Kong: el IMV es para quien reside en España "
                "(arts. 10 y 36.e de la Ley 19/2021). Vuelve con `pala` y hablamos."
            )
            await logros.track(
                self.bot,
                responder.guild.id,
                user,
                responder.channel,
                StatDelta(add={"imv_abroad": 1}),
            )
            return
        if result.suspended_until:
            await responder.send_error(
                f"🕵️ La Inspección de Trabajo te pilló cobrando en negro y te ha suspendido "
                f"el IMV. Vuelve {next_at} y esta vez declara."
            )
            return
        if not result.claimed:
            await responder.send_error(f"Ya cobraste el IMV hoy. Vuelve {next_at}.")
            return
        streak = (
            f"🔥 Racha de {result.streak} días" if result.streak >= 2 else "Primer día de racha"
        )
        work = ""
        if result.reduction:
            work = (
                f"\n🪏 Esta semana has cobrado {format_amount(result.work_net)} netos "
                f"trabajando: te quitan {format_amount(result.reduction)} de los "
                f"{format_amount(result.full_amount)} que tocaban. Los primeros "
                f"{format_amount(IMV_WORK_EXEMPT)} a la semana no cuentan y del resto, "
                "solo la mitad (RD 789/2022)."
            )
        elif result.work_net:
            work = "\n🪏 Trabajas y cobras el IMV entero: aún no pasas del mínimo exento."
        pet = await mascotas.cameo(self.bot, responder.guild.id, user.id, Moment(Event.IMV))
        embed = discord.Embed(
            description=(
                f"# {CURRENCY_EMOJI} +{format_amount(result.amount)}\n"
                f"{streak} · Saldo: **{format_amount(result.balance)}**{work}\n"
                f"Vuelve {next_at} y cobras {format_amount(daily_amount(result.streak + 1))}"
                f"{' (menos lo que trabajes)' if result.work_net else ''}. "
                "Toca uno por día (se cuenta a medianoche, hora canaria) y si te saltas "
                "un día, la racha se pierde.\n"
                f"-# 🐶 {TAX_COLLECTOR} no puede tocarlo: el IMV está exento de IRPF "
                "(art. 7.y LIRPF)." + (f"\n{pet}" if pet else "")
            ),
            color=COLOR_WIN,
        )
        embed.set_author(name=f"IMV de {user.display_name}", icon_url=user.display_avatar.url)
        await responder.send(embed=embed)
        delta = StatDelta(
            add={"imv_claims": 1},
            peak={"imv_streak_max": result.streak, "balance_max": result.balance},
        )
        if result.work_net:
            delta.add["imv_with_salary"] = 1
        clock = datetime.now(TIMEZONE)
        if clock.hour == 0 and clock.minute < IMV_MIDNIGHT_MINUTES:
            delta.add["imv_midnight"] = 1
        if result.reduction and result.amount <= round(result.full_amount * IMV_FLOOR_SHARE):
            delta.add["imv_floor"] = 1
        await logros.track(self.bot, responder.guild.id, user, responder.channel, delta)

    @app_commands.command(
        name="imv", description=f"Cobra tu Ingreso Mínimo Vital diario en {CURRENCY_NAME}."
    )
    @app_commands.guild_only()
    async def imv(self, interaction: discord.Interaction) -> None:
        """Cobra el IMV; cada día seguido paga más (hasta un tope). Exento de IRPF."""
        await self._daily_impl(InteractionResponder(interaction), interaction.user)

    @commands.command(name="imv")
    @commands.guild_only()
    async def imv_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.imv`) de `/imv`."""
        await self._daily_impl(ContextResponder(ctx), ctx.author)

    # -- Hacienda ------------------------------------------------------------------

    async def _hacienda_view(
        self, guild: discord.Guild, author: discord.abc.User, member: discord.abc.User | None
    ) -> tuple[ui.LayoutView, list[TaxBill]]:
        """Sin miembro: el panel de la cuenta del Estado. Con miembro: su factura.

        Lee todas las cifras de una vez (las pestañas no vuelven a la base de datos).

        Returns:
            `(la vista, las facturas de todos)`, estas para los logros.
        """
        now = datetime.now(TIMEZONE)
        year_start = datetime(now.year, 1, 1, tzinfo=TIMEZONE).timestamp()
        if member is not None:
            everything, this_year, housing, business = await asyncio.gather(
                self.economy.tax_breakdown(guild.id),
                self.economy.tax_breakdown(guild.id, since=year_start),
                self.economy.housing_tax_breakdown(guild.id),
                self.economy.business_tax_breakdown(guild.id),
            )
            all_bills = tax_bills(everything)
            text = member_page(
                member_name=discord.utils.escape_markdown(member.display_name),
                all_bills=all_bills,
                year_bills=tax_bills(this_year),
                user_id=member.id,
                year=now.year,
                homes=housing.get(member.id, {}),
                business=business.get(member.id, {}),
            )
            return bill_card(text), all_bills
        everything, treasury, housing, business, solidarity = await asyncio.gather(
            self.economy.tax_breakdown(guild.id),
            self.economy.treasury(guild.id, since=year_start),
            self.economy.housing_tax_breakdown(guild.id),
            self.economy.business_tax_breakdown(guild.id),
            self.economy.solidarity_summary(guild.id),
        )
        all_bills = tax_bills(everything)
        people = {bill.user_id for bill in all_bills} | set(housing) | set(business)
        snapshot = Snapshot(
            guild_id=guild.id,
            year=now.year,
            treasury=treasury,
            bills=all_bills,
            housing=housing,
            business=business,
            solidarity=solidarity,
            names={user_id: self._display_name(guild, user_id) for user_id in people},
        )
        return HaciendaPanel(self.bot, snapshot, author), all_bills

    async def _hacienda_logros(
        self,
        guild: discord.Guild,
        author: discord.abc.User,
        channel: object,
        all_bills: list[TaxBill],
        member: discord.abc.User | None,
    ) -> None:
        if author.bot:
            return
        server = server_bill(all_bills)
        mine = next((b for b in all_bills if b.user_id == author.id), None)
        share = (mine.total / server.total) if mine and server.total > 0 else 0.0
        await logros.track(
            self.bot,
            guild.id,
            author,
            channel,
            hacienda_stats(
                own=member is not None and member.id == author.id,
                snooping=member is not None and member.id != author.id,
                share=share,
                indirect_over_direct=bool(mine and mine.indirect > mine.direct > 0),
            ),
        )

    @staticmethod
    def _display_name(guild: discord.Guild, user_id: int) -> str:
        found = guild.get_member(user_id)
        if found is None:
            return f"<@{user_id}>"
        return discord.utils.escape_markdown(found.display_name)

    @app_commands.command(
        name="hacienda",
        description="Cuánto ha recaudado el Estado y todo lo que paga cada uno (o tu factura).",
    )
    @app_commands.describe(miembro="De quién ver la factura fiscal completa (opcional).")
    @app_commands.guild_only()
    async def hacienda(
        self, interaction: discord.Interaction, miembro: discord.Member | None = None
    ) -> None:
        """La cuenta del Estado con lo que paga cada miembro, o la factura de `miembro`."""
        assert interaction.guild is not None  # guild_only
        # Las cifras salen de varias consultas grandes: se acepta el comando antes.
        await ack(interaction)
        view, all_bills = await self._hacienda_view(interaction.guild, interaction.user, miembro)
        await edit(interaction, view=view, allowed_mentions=discord.AllowedMentions.none())
        if isinstance(view, HaciendaPanel):
            view.interaction = interaction
        await self._hacienda_logros(
            interaction.guild, interaction.user, interaction.channel, all_bills, miembro
        )

    @commands.command(name="hacienda")
    @commands.guild_only()
    async def hacienda_text(
        self, ctx: commands.Context, miembro: discord.Member | None = None
    ) -> None:
        """Versión de texto (`.hacienda [miembro]`) de `/hacienda`."""
        assert ctx.guild is not None  # guild_only
        view, all_bills = await self._hacienda_view(ctx.guild, ctx.author, miembro)
        message = await ctx.send(view=view, allowed_mentions=discord.AllowedMentions.none())
        if isinstance(view, HaciendaPanel):
            view.message = message
        await self._hacienda_logros(ctx.guild, ctx.author, ctx.channel, all_bills, miembro)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra la economía del servidor cuando el bot deja de pertenecer a él."""
        await self.economy.delete_guild_data(guild.id)
        self._history.pop(guild.id, None)
        if isinstance(rules := getattr(self.bot, "casino_channels", None), CasinoChannels):
            await rules.forget(guild.id)
        logger.info("Se eliminó la economía del servidor %s", guild.id)


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(Casino(bot, economy=bot.economy, casino_channel_ids=bot.casino_channels))
