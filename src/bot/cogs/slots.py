"""Tragaperras: `slots`, una máquina de 3 rodillos con botones y bote común.

Cada jugador abre su propia máquina, un mensaje con botones que solo él puede
pulsar. Las reglas (rodillos, premios, giros gratis, máquina caliente) viven
en `bot.services.slots`; el dibujo, en `bot.services.slots_scene`; el dinero,
en `EconomyService.play_slots`. Este cog solo une las piezas y pinta.

Botones:

- 🎰 **Tirar**: cobra, gira y paga. Con giros gratis pendientes, juega uno.
- 🔁 **Ráfaga ×10**: diez tiradas seguidas sin animación, con un solo resumen y
  una sola imagen.
- ▶️ **Auto**: tiradas normales encadenadas, cada una con su animación y su imagen
  final, como si el dueño pulsara 🎰 Tirar una y otra vez. Mientras corre, el
  botón es ⏹️ **Parar** y es lo único que se puede pulsar. Para sola si no llega
  el saldo, si sale un premio gordo (el bote o ×50 la apuesta), si las pérdidas
  netas de la sesión llegan a 10 veces la apuesta o al llegar a 25 tiradas. El
  bucle y sus reglas están en `bot.services.autoplay`.
- ⚡ **Turbo**: sin animación, solo la imagen final (más rápido y casi sin datos).
- **½**, **×2**, 💰 **All-in**: cambian la apuesta. 📋 **Premios**: la tabla.
- 🔁 **Re-girar el 3º**: sale tras un casi-premio del tercer rodillo. Cobra
  `respin_price` (lo que vale de media el re-giro entre 0,995) y vuelve a girar
  solo ese rodillo. Se encadena mientras siga quedándose a uno.
- 🔴 **Rojo** / ⚫ **Negro**: doble o nada con lo que acaba de cobrar, a cara o
  cruz, hasta `DOUBLE_MAX` veces seguidas. Elegir color no cambia nada: es la
  ilusión de control de las máquinas de bar. Se ve como en ellas: una carta
  boca abajo que se gira y enseña un palo rojo o negro.
- 🎁 **Giro del día**: un giro gratis al día cuya apuesta sube con la racha de
  días seguidos (`daily_stake`).

El embed enseña lo que paga cada combinación a la apuesta actual, como el
cartel de una máquina de bar; el bote con su tope («cae antes de 50.000») y
las tiradas que lleva el servidor sin bote; y los premios cobrados en la
sesión, en bruto. El neto de verdad sale en el ticket al cerrar la máquina.

La animación es un GIF con su ritmo (corta si no hay nada en juego, lenta si
el tercer rodillo puede dar algo gordo; ver `bot.services.slots_scene`). Al
acabar se cambia por el PNG final, como en la ruleta.

Se pinta antes del clic (`SlotMachineView.prepare`): la próxima tirada ya está
sorteada (`_upcoming`, solo en memoria y nunca a la vista) y mientras se ve la
tirada actual se pintan las imágenes de cada botón que se puede pulsar: Tirar
(o el giro gratis), Rojo y Negro, el re-giro, el giro del día, Tirar con la
apuesta de ½, ×2 y All-in si la próxima paga algo, y la imagen final de una
Ráfaga. Al pulsar solo queda subirla. Una imagen pintada vale si coincide todo
lo que la decide (`slots_scene.media_key`); si no (el bote ha cambiado, la
máquina se ha enfriado), se pinta en el momento.

El calor de la máquina (`bot.services.slots.next_heat`) se guarda por
miembro en la base de datos (`SlotsRepository`) y se enfría un punto cada diez
minutos sin jugar (`decayed_heat`): si te vas, lo pierdes.

La máquina solo se abre en los canales del casino que elige `garito` (sin elegir, los de
`CASINO_CHANNEL_IDS`) y en sus hilos. Permisos que necesita el bot en el canal: enviar mensajes,
insertar enlaces (embeds) y adjuntar archivos.
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs import hold_win as botes
from bot.cogs.casino import casino_channel_error, insufficient_text
from bot.cogs.tragas_cambio import CLASSIC, SwitchMixin
from bot.services.achievements import (
    StatDelta,
    casino_stats,
    slots_autoplay_stats,
    slots_bonus_stats,
    slots_cooled_stats,
    slots_double_stats,
    slots_respin_stats,
    slots_stats,
    slots_ticket_stats,
    universo_slots_stats,
)
from bot.services.autoplay import (
    AUTOPLAY_MAX,
    AUTOPLAY_MIN_GAP,
    AutoplayOutcome,
    AutoplaySession,
    AutoplayStop,
    SpinResult,
    StopReason,
)
from bot.services.casino_channels import CasinoChannelIds
from bot.services.economy import (
    BalanceLimitError,
    EconomyService,
    InsufficientFundsError,
    SlotsSettlement,
    format_amount,
    gambling_tax_line,
    parse_amount,
)
from bot.services.hold_win import THEMES
from bot.services.levels import TIMEZONE
from bot.services.pets import bet_moment
from bot.services.slots import (
    BONUS_FREE_SPINS,
    BONUS_MAX,
    DOUBLE_MAX,
    FREE_SPINS,
    HEAT_DECAY_SECONDS,
    HEAT_MAX,
    HOT_MULTIPLIER,
    POT_CAP,
    POT_SEED,
    REEL_STRIPS,
    SCATTER,
    SYMBOLS,
    BonusMeter,
    Kind,
    SlotMachine,
    Spin,
    WinTier,
    add_bonus,
    bar_fill,
    bonus_bar,
    daily_stake,
    decayed_heat,
    heat_bar,
    line_payout,
    next_daily_streak,
    next_heat,
    paytable_lines,
    pot_share,
    prize_table,
    respin_price,
    win_tier,
)
from bot.services.slots_render import SYMBOL_FILES, SlotsMedia
from bot.services.slots_scene import (
    DOUBLE_HISTORY,
    SlotsScene,
    double_suit,
    media_key,
    spin_states,
    still_state,
)
from bot.services.taxes import TAX_COLLECTOR
from bot.services.telemetry import TimedLock
from bot.services.universo_tragas import MACHINES as PLAZA_MACHINES
from bot.services.universo_tragas import MAX_AT_ONCE as PLAZA_MAX_AT_ONCE
from bot.services.universo_tragas import MIN_GAP_SECONDS as PLAZA_MIN_GAP
from bot.services.universo_tragas import Auto as PlazaAuto
from bot.services.universo_tragas import SeatError, Seats, auto_stop_text
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient
    from bot.repositories.slots import SlotsRepository

logger = logging.getLogger(__name__)

GAME = "tragaperras"
#: Apuesta por defecto al abrir la máquina sin indicar cantidad.
DEFAULT_STAKE = 100
#: Segundos sin pulsar nada tras los que la máquina se cierra.
MACHINE_TIMEOUT = 180
#: Margen tras la animación: el cliente tarda un poco en empezar el GIF.
REVEAL_MARGIN_SECONDS = 0.4
#: Y un poco más cuanto más pesa (por MB), para que un móvil lento no se pierda el final
#: del frenazo: el último fotograma es igual que el PNG, así que esperar de más no se ve.
REVEAL_SECONDS_PER_MB = 0.4
#: Tiradas de Ráfaga.
BURST_SPINS = 10
#: A partir de cuántas veces la apuesta se anuncia el premio en el canal.
SHOUT_MULTIPLIER = 50

GIF_NAME = "tragaperras.gif"
PNG_NAME = "tragaperras.png"

COLOR_IDLE = discord.Color.from_rgb(43, 45, 49)
COLOR_SPIN = discord.Color.from_rgb(222, 178, 70)
COLOR_WIN = discord.Color.from_rgb(255, 196, 0)
COLOR_LOSS = discord.Color.from_rgb(80, 84, 92)
COLOR_HOT = discord.Color.from_rgb(255, 110, 40)

# Textos en el tono de Jovani Vázquez: alegre, exagerado y cariñoso.
JACKPOT_LINES = (
    "🃏🃏🃏 ¡JOVANAZO!",
    "🃏🃏🃏 ¡EL BOTE ES TUYO!",
    "🃏🃏🃏 ¡JOTA UVE! ¡PRE!",
    "🃏🃏🃏 ¿QUÉ? ¿QUÉ? ¡TOMA MANGÓ!",
    "🃏🃏🃏 ¡NI FABRA TIENE TANTA SUERTE!",
    "🃏🃏🃏 ¡LA MÁQUINA HA PERDIDO LA CABEZA!",
    "🃏🃏🃏 ¡AZUL, AZUL, COMO EL MAR AZUL!",
)
MYSTERY_LINES = (
    "💰 ¡HA CAÍDO EL BOTE!",
    "💰 ¡LE TOCABA!",
    "💰 ¡BOTE MISTERIOSO!",
    "💰 ¡BOTE SORPRESA!",
    "💰 ¡LA MÁQUINA SE HA ABIERTO DE PIERNAS!",
    "💰 ¡NI LO VISTE VENIR!",
)
TIER_LINES = {
    WinTier.EPIC: (
        "🌋 ¡ÉPICO!",
        "🌋 ¡REVIENTAS LA MÁQUINA!",
        "🌋 ¡ESTO NO SE VE NUNCA!",
        "🌋 ¡EVERYBODY WANT MANGO!",
        "🌋 ¡EL TEIDE SE PONE CELOSO!",
    ),
    WinTier.MEGA: (
        "💥 ¡MEGAPREMIO!",
        "💥 ¡QUÉ LOCURA!",
        "💥 ¡QUÉ PREMIO!",
        "💥 ¡TREMENDO, TREMENDO!",
        "💥 ¡TOMA MANGÓ!",
        "💥 ¡HACIENDA YA ESTÁ AFILANDO EL LÁPIZ!",
        "💥 ¡LA MÁQUINA ESCUPE BILLETES!",
        "💥 ¡ESTO SE CELEBRA CON BARRAQUITO!",
    ),
    WinTier.BIG: (
        "✨ ¡GRAN PREMIO!",
        "✨ ¡TOMA YA!",
        "✨ ¡ESO ES CALIDAD!",
        "✨ ¡TOMA MANGÓ!",
        "✨ ¡A LA SACA!",
    ),
}
WIN_LINES = (
    "¡Eso es!",
    "¡Cobras!",
    "¡Tilín, tilín!",
    "¿Con pulpa? ¡Sin pulpa!",
    "¡Toma mangó!",
    "¡A la saca!",
    "La máquina se ha puesto tierna.",
    "¡Más suerte que Fabra!",
    "Hoy la máquina te quiere.",
    "¡Premio y para casa!",
)
SMALL_WIN_LINES = (
    "🍒 ¡Premio!",
    "🍒 ¡Algo cae!",
    "🍒 ¡Tilín!",
    "🍒 ¡Pa'l café!",
    "🍒 ¡Para pipas!",
    "🍒 ¡Calderilla, pero calderilla tuya!",
)
NEAR_MISS_LINES = (
    "¡Por un pelo!",
    "¡Uff! Lo tenías ahí.",
    "Casi, casi… la próxima es la buena.",
    "¡Casi! Faltó uno.",
    "Por un pelo de gamba.",
    "El tercero se ha quedado en el andén.",
    "Tan cerca que casi lo hueles.",
)
LOSS_LINES = (
    "La próxima es la buena.",
    "Nada. Otra.",
    "La máquina se hace la difícil.",
    "Calentando motores…",
    "Quiero café.",
    "Quisiera ser una mosca para ver qué tiene esta máquina dentro.",
    "Nada por aquí, nada por allá.",
    "Estoy destruido, pero por fuera. Por dentro, como un coco.",
    "La máquina hoy está de morros.",
    "Ni un limón. Ni uno.",
    "Lagartijo, iguana, lagarto… y nada.",
    "La máquina se ha levantado con el pie izquierdo.",
)
DOUBLE_WIN_LINES = (
    "¡Dentro!",
    "¡Lo sabía!",
    "¡Olfato de casino!",
    "¡Tienes el don!",
    "¡Doblado!",
    "¡Ni Tezanos acierta así!",
)
DOUBLE_LOSS_LINES = (
    "La casa agradece tu aportación.",
    "Era el otro color, obviamente.",
    "Doble o nada. Ha salido nada.",
    "Sofía, cállate, que me pones nervioso.",
    "La carta te ha hecho la cobra.",
    "Lo tenías y lo soltaste.",
)
TICKET_LINES = (
    "Gracias por su visita. Vuelva pronto.",
    "Hacienda ya tiene una copia.",
    "Conserve este ticket para su psicólogo.",
    "Gracias por jugar.",
    "No canjeable por dignidad.",
    "Vuelva pronto, la máquina le echará de menos.",
    "Ticket sellado por la UCO.",
)
#: Colores del doble o nada. Da igual cuál se elija: es cara o cruz.
RED = "red"
BLACK = "black"
COLOR_NAMES = {RED: "🔴 Rojo", BLACK: "⚫ Negro"}


# -- Presentación -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SlotsPlay:
    """Una tirada ya cobrada y pagada, lista para enseñarse.

    Attributes:
        stake: Apuesta de la tirada (la que activó los giros gratis si `free`).
        payout: Lo que ha devuelto la línea.
        heat: Calor de la máquina tras la tirada.
        session_spins: Tiradas en esta máquina, contando esta.
    """

    spin: Spin
    stake: int
    free: bool
    hot: bool
    payout: int
    settlement: SlotsSettlement
    media: SlotsMedia
    heat: int
    session_spins: int
    #: Lo que ha costado si no es la apuesta: el precio de un re-giro.
    cost: int | None = None
    #: En un re-giro, cuántos van seguidos sobre la misma tirada (contando este).
    respin_chain: int = 0
    daily: bool = False
    daily_streak: int = 0
    #: Si la tirada ha llenado la barra de bonus: la apuesta de sus giros
    #: gratis (0 si no) y en cuántas tiradas se llenó.
    bonus_stake: int = 0
    bonus_fill_spins: int = 0

    @property
    def jackpot(self) -> int:
        """Lo que se ha llevado del bote."""
        return self.settlement.jackpot

    @property
    def mystery(self) -> bool:
        """Si el bote ha caído solo, por el tope oculto."""
        return bool(self.jackpot) and getattr(self.settlement, "mystery", False)

    @property
    def respin(self) -> bool:
        """Si es un re-giro del tercer rodillo."""
        return self.cost is not None

    @property
    def paid_stake(self) -> int:
        """Lo que ha salido del bolsillo: nada en un giro gratis, el precio en un re-giro."""
        if self.cost is not None:
            return self.cost
        return 0 if self.free or self.daily else self.stake

    @property
    def tier(self) -> str | None:
        """Nivel de celebración (`WinTier`) de lo cobrado frente a la apuesta."""
        return win_tier(self.won, self.stake)

    @property
    def won(self) -> int:
        """Todo lo cobrado: línea más bote."""
        return self.payout + self.jackpot

    @property
    def net(self) -> int:
        """Ganancia (positiva) o pérdida (negativa) de la tirada."""
        return self.won - self.paid_stake

    @property
    def balance(self) -> int:
        """Saldo tras la tirada, con el IRPF ya ajustado."""
        return self.settlement.bet.balance

    @property
    def big_prize(self) -> bool:
        """Premio gordo: el bote o ×`SHOUT_MULTIPLIER` la apuesta (los que se anuncian)."""
        return self.jackpot > 0 or (self.stake > 0 and self.net >= SHOUT_MULTIPLIER * self.stake)


def result_text(play: SlotsPlay, rng: random.Random | None = None) -> str:
    """Bloque grande con lo que ha pasado en la tirada.

    Cuando la línea devuelve menos de lo apostado, se celebra el premio
    ("¡Premio! Cobras 50 Y$") y no se dice que en realidad se pierde: es lo
    que hacen las máquinas de verdad. Lo cobrado es real; el saldo lo dice todo.
    """
    rng = rng or random.Random()
    spin = play.spin
    lines: list[str] = []
    if play.jackpot:
        lines.append(f"# {rng.choice(MYSTERY_LINES if play.mystery else JACKPOT_LINES)}")
        lines.append(f"## +{format_amount(play.jackpot)} del bote")
        if play.mystery and (drought := getattr(play.settlement, "drought", 0)):
            lines.append(f"Llevaba {drought:,} tiradas sin caer.".replace(",", "."))
        if play.payout:
            lines.append(f"Y además la línea paga {format_amount(play.payout)}.")
    elif (tier := play.tier) is not None:
        lines.append(f"# {rng.choice(TIER_LINES[tier])}")
        lines.append(f"## +{format_amount(play.net)}")
    elif play.net > 0:
        lines.append(f"# {rng.choice(WIN_LINES)} +{format_amount(play.net)}")
    elif play.payout > 0:
        lines.append(f"## {rng.choice(SMALL_WIN_LINES)} Cobras {format_amount(play.payout)}")
    elif spin.near_miss:
        lines.append(f"## {rng.choice(NEAR_MISS_LINES)}")
        if spin.teaser is not None:
            emoji = SYMBOLS[spin.teaser].emoji
            lines.append(f"El {emoji} se ha quedado a una casilla de la línea.")
        if play.paid_stake:
            lines.append(f"-{format_amount(play.paid_stake)}")
    elif play.paid_stake:
        lines.append(f"## -{format_amount(play.paid_stake)} · {rng.choice(LOSS_LINES)}")
    else:
        lines.append("## Nada en este giro gratis.")

    if spin.kind == Kind.THREE and spin.symbol is not None and not play.jackpot:
        emoji = SYMBOLS[spin.symbol].emoji
        lines.append(f"{emoji} {emoji} {emoji}")
    if play.hot:
        lines.append(f"🔥 Tirada caliente: la línea paga ×{HOT_MULTIPLIER}.")
    if play.respin:
        lines.append(f"🔁 Re-giro del tercer rodillo por {format_amount(play.paid_stake)}.")
    if play.daily:
        days = "1 día" if play.daily_streak == 1 else f"{play.daily_streak} días"
        lines.append(f"🎁 Giro del día · racha de {days}.")
    if play.free:
        lines.append("🎟️ Giro gratis.")
    if play.bonus_stake:
        lines.append(
            f"### 🎁 ¡BARRA DE BONUS LLENA! +{BONUS_FREE_SPINS} giros gratis "
            f"a {format_amount(play.bonus_stake)}"
        )
    if spin.triggers_free_spins:
        lines.append(f"### 🎟️🎟️🎟️ ¡GIROS GRATIS! +{FREE_SPINS}")
    elif spin.scatters == 2:
        lines.append(f"{SYMBOLS[SCATTER].emoji}{SYMBOLS[SCATTER].emoji} Te faltó una entrada…")
    return "\n".join(lines)


def auto_text(plays: list[SlotsPlay], stopped: str | None = None) -> str:
    """Resumen de una Ráfaga: cuántas, cuántas con premio, neto y la mejor."""
    net = sum(p.net for p in plays)
    paid = sum(1 for p in plays if p.won > 0)
    best = max(plays, key=lambda p: p.net)
    sign = "+" if net > 0 else "-" if net < 0 else "±"
    lines = [
        f"# 🔁 {len(plays)} tiradas · {sign}{format_amount(abs(net))}",
        f"{paid} con premio · apostado {format_amount(sum(p.paid_stake for p in plays))}",
    ]
    if best.net > 0:
        line = " ".join(SYMBOLS[s].emoji for s in best.spin.line)
        lines.append(f"Mejor: {line} +{format_amount(best.net)}")
    if any(p.jackpot for p in plays):
        lines.append(f"# 🃏🃏🃏 ¡JOVANAZO! +{format_amount(sum(p.jackpot for p in plays))}")
    if any(p.spin.triggers_free_spins for p in plays):
        lines.append("🎟️ ¡Han salido giros gratis!")
    if stopped:
        lines.append(f"-# {stopped}")
    return "\n".join(lines)


def tax_note(delta: int, plays: list[SlotsPlay]) -> str | None:
    """Línea de IRPF de una tirada o de una Ráfaga (suma de ajustes)."""
    if not plays:
        return None
    if len(plays) == 1:
        return gambling_tax_line(plays[0].settlement.bet)
    last = plays[-1].settlement.bet
    if delta > 0:
        return (
            f"-# 🐶 {TAX_COLLECTOR} se lleva {format_amount(delta)} de IRPF. "
            f"Hoy vas {format_amount(last.day_net)} arriba."
        )
    if delta < 0:
        return (
            f"-# 🐶 {TAX_COLLECTOR} te devuelve {format_amount(-delta)}: "
            "tus pérdidas de hoy compensan lo que habías ganado."
        )
    return None


def machine_embed(
    *,
    owner: str,
    balance: int,
    stake: int,
    pot: int,
    heat: int,
    free_spins: int,
    free_stake: int,
    turbo: bool,
    text: str | None = None,
    won: bool | None = None,
    last_jackpot: str | None = None,
    spins_since: int = 0,
    session_gross: int = 0,
    session_prizes: int = 0,
    bonus: int = 0,
) -> discord.Embed:
    """Embed de la máquina parada: al abrirla o tras una tirada.

    Args:
        spins_since: Tiradas pagadas del servidor desde el último bote.
        session_gross: Premios cobrados en esta máquina, en bruto: sin restar
            lo apostado. Es lo que enseña una máquina de verdad.
        session_prizes: Cuántas tiradas de la sesión han cobrado algo.
        bonus: Puntos de la barra de bonus.
    """
    if text is None:
        description = (
            "Pulsa 🎰 **Tirar**. Paga la fila del medio.\n"
            "📋 **Premios** para ver la tabla. 🃏 🃏 🃏 se lleva el **bote**."
        )
    else:
        description = text
    if balance == 0 and not free_spins:
        description += "\n\n**Estás a cero.** `imv` te recarga."
    hot = heat >= HEAT_MAX
    if won is None:
        color = COLOR_HOT if hot else COLOR_IDLE
    else:
        color = COLOR_WIN if won else COLOR_HOT if hot else COLOR_LOSS
    embed = discord.Embed(title="🎰 Tragaperras", description=description, color=color)
    embed.add_field(name="Saldo", value=format_amount(balance))
    embed.add_field(name="Apuesta", value=format_amount(stake))
    pot_lines = [f"**{format_amount(pot)}**", f"-# Cae antes de {format_amount(POT_CAP)}"]
    if spins_since:
        pot_lines.append(f"-# {spins_since:,} tiradas sin bote".replace(",", "."))
    embed.add_field(name="💰 Bote", value="\n".join(pot_lines))
    left, right = prize_columns(stake)
    embed.add_field(name=f"Premios a {format_amount(stake)}", value=left)
    embed.add_field(name="\u200b", value=right)
    if hot:
        embed.add_field(
            name="🔥 ¡Máquina caliente!",
            value=f"La siguiente tirada paga ×{HOT_MULTIPLIER}.",
            inline=False,
        )
    else:
        embed.add_field(name="Calor", value=heat_bar(heat), inline=False)
    embed.add_field(
        name=f"🎁 Bonus: {BONUS_FREE_SPINS} giros gratis al llenarse",
        value=bonus_bar(bonus),
        inline=False,
    )
    if free_spins:
        embed.add_field(
            name="🎟️ Giros gratis",
            value=f"Te quedan **{free_spins}** a {format_amount(free_stake)}.",
            inline=False,
        )
    if session_gross:
        embed.add_field(
            name="🎫 Premios cobrados",
            value=f"**{format_amount(session_gross)}** en {session_prizes} premios",
            inline=False,
        )
    embed.set_image(url=f"attachment://{PNG_NAME}")
    footer = f"Máquina de {owner}"
    if turbo:
        footer += " · ⚡ Turbo"
    if last_jackpot:
        footer += f" · Último bote: {last_jackpot}"
    embed.set_footer(text=footer)
    return embed


def prize_columns(stake: int) -> tuple[str, str]:
    """El cartel de premios a la apuesta `stake`, en dos columnas como en la máquina del bar."""
    rows = [
        f"{combo} **{'BOTE' if amount is None else format_amount(amount)}**"
        for combo, amount in prize_table(stake)
    ]
    half = (len(rows) + 1) // 2
    return "\n".join(rows[:half]), "\n".join(rows[half:])


def ticket_text(
    *, spins: int, staked: int, gross: int, tax: int = 0, rng: random.Random | None = None
) -> str:
    """El ticket de la sesión al cerrar la máquina: aquí sí sale el neto, con el IRPF.

    Args:
        tax: IRPF del juego retenido en la sesión (negativo si se devolvió).
    """
    rng = rng or random.Random()
    net = gross - staked - tax
    sign = "+" if net > 0 else "-" if net < 0 else "±"
    return "\n".join(
        (
            "## 🧾 Ticket de la sesión",
            f"Tiradas: {spins} · Apostado: {format_amount(staked)}",
            f"Premios cobrados: {format_amount(gross)}",
            *(
                [f"IRPF ({TAX_COLLECTOR}): {'-' if tax > 0 else '+'}{format_amount(abs(tax))}"]
                if tax
                else []
            ),
            f"**Neto: {sign}{format_amount(abs(net))}**",
            f"-# {rng.choice(TICKET_LINES)}",
        )
    )


def spinning_embed(*, owner: str, stake: int, free: bool, hot: bool, pot: int) -> discord.Embed:
    """Embed mientras giran los rodillos."""
    lines = ["# 🌀 ¡Girando!"]
    lines.append(f"🎟️ Giro gratis a {format_amount(stake)}" if free else format_amount(stake))
    if hot:
        lines.append(f"🔥 Tirada caliente: ×{HOT_MULTIPLIER}")
    embed = discord.Embed(title="🎰 Tragaperras", description="\n".join(lines), color=COLOR_SPIN)
    embed.add_field(name="💰 Bote", value=f"**{format_amount(pot)}**")
    # El cartel sigue a la vista mientras giran: se mira lo que podría tocar.
    left, right = prize_columns(stake)
    embed.add_field(name=f"Premios a {format_amount(stake)}", value=left)
    embed.add_field(name="\u200b", value=right)
    embed.set_image(url=f"attachment://{GIF_NAME}")
    embed.set_footer(text=f"Máquina de {owner}")
    return embed


def double_embed(*, owner: str, amount: int, color: str) -> discord.Embed:
    """Embed mientras se gira la carta del doble o nada (sin el resultado)."""
    lines = [
        "# 🃏 ¡Doble o nada!",
        f"{COLOR_NAMES[color]} · {format_amount(amount)} → {format_amount(2 * amount)}",
    ]
    embed = discord.Embed(title="🎰 Tragaperras", description="\n".join(lines), color=COLOR_SPIN)
    embed.set_image(url=f"attachment://{GIF_NAME}")
    embed.set_footer(text=f"Máquina de {owner}")
    return embed


def paytable_embed() -> discord.Embed:
    """Tabla de premios (se manda en privado)."""
    return discord.Embed(
        title="📋 Premios de la tragaperras",
        description="Solo paga la fila del medio.\n\n" + "\n".join(paytable_lines()),
        color=COLOR_WIN,
    )


def reveal_delay(media: SlotsMedia) -> float:
    """Lo que se espera tras subir el GIF antes de cambiarlo por el PNG final."""
    return media.seconds + REVEAL_MARGIN_SECONDS + len(media.gif) / 2**20 * REVEAL_SECONDS_PER_MB


def parse_stake(amount_text: str | None, balance: int) -> int:
    """Apuesta del comando: la indicada (`500`, `2k`, `all`) o la de por defecto.

    Raises:
        ValueError: Con un mensaje mostrable si no se entiende.
    """
    if not amount_text:
        return max(1, min(DEFAULT_STAKE, balance))
    return parse_amount(amount_text, balance)


EditFn = Callable[..., Awaitable[Any]]
#: Lo pintado por adelantado de una máquina, por su `media_key` (ver `SlotMachineView`).
ReadyFn = Callable[[tuple[Any, ...]], "Awaitable[SlotsMedia | None] | None"]


# -- Máquina ------------------------------------------------------------------------


#: Las máquinas de `tragas`: la clásica y cada una de `THEMES` (una nueva sale sola).
MACHINE_CHOICES = [
    app_commands.Choice(name="🎰 Clásica, con bote común", value=CLASSIC),
    *(
        app_commands.Choice(name=f"{theme.emoji} {theme.title}", value=theme.key)
        for theme in THEMES.values()
    ),
]


def parse_machine_args(args: tuple[str, ...]) -> tuple[str | None, str | None]:
    """`.tragas volcan 500` → `("volcan", "500")`: la máquina y la cantidad, en cualquier orden.

    Una palabra que es una máquina (`clasica`, `volcán`, `volcan`…) se toma como tal;
    lo primero que no lo es, como la cantidad.
    """
    names = {CLASSIC: CLASSIC, "clásica": CLASSIC, **{key: key for key in THEMES}}
    names.update({theme.title.lower(): theme.key for theme in THEMES.values()})
    machine = amount = None
    for word in args:
        key = names.get(word.lower())
        if key is not None and machine is None:
            machine = key
        elif amount is None:
            amount = word
    return machine, amount


class SlotMachineView(SwitchMixin, discord.ui.View):
    """Máquina de un jugador: un mensaje con botones.

    Guarda la apuesta, el modo turbo, los giros gratis pendientes y las
    tiradas de la sesión. No guarda dinero: el saldo y el bote se leen y se
    cambian siempre a través de la economía.

    `_busy` vale mientras hay una acción en curso (una tirada, una Ráfaga o todo
    un ▶️ Auto): un segundo clic de dos botones a la vez no puede cobrar dos
    veces. `autoplay` es la sesión de ▶️ Auto en marcha, si la hay.

    🔀 Otras tragaperras (`SwitchMixin`) la cambia por otra en el mismo mensaje.
    """

    machine_key = CLASSIC
    switch_prefix = GAME

    def __init__(self, cog: Slots, *, guild_id: int, owner: discord.abc.User, stake: int) -> None:
        super().__init__(timeout=MACHINE_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        self.stake = stake
        self.turbo = cog.turbo_default(guild_id, owner.id)
        self.free_spins = 0
        self.free_stake = 0
        self.session_spins = 0
        # Ticket de la sesión: lo apostado y lo cobrado en bruto (con re-giros y dobles).
        self.session_staked = 0
        self.session_gross = 0
        self.session_prizes = 0
        # IRPF del juego retenido (o devuelto, en negativo) en la sesión.
        self.session_tax = 0
        # Lo que se puede hacer con la última tirada: re-girar el tercer
        # rodillo (`respin_offer`: la tirada, su apuesta, el precio y los
        # re-giros encadenados) o doblar lo cobrado (`double_offer`: cuánto y
        # cuántos dobles ganados lleva).
        self.respin_offer: tuple[Spin, int, int, int] | None = None
        self.double_offer: tuple[int, int] | None = None
        # Racha con la que se cobraría hoy el giro del día; 0 si no hay.
        self.daily_streak = 0
        self.cooled = 0
        self.last_text: str | None = None
        self.last_won: bool | None = None
        self.message: discord.Message | None = None
        self._last_interaction: discord.Interaction | None = None
        self._busy = False
        self.autoplay: AutoplaySession | None = None
        # Pintar antes del clic (`prepare`): lo ya sorteado, que solo vive en memoria y
        # se gasta al jugarlo, y lo ya pintado (o pintándose) por su `media_key`.
        self._upcoming: Spin | None = None
        self._respin_next: tuple[Spin, Spin] | None = None
        self._coin_next: bool | None = None
        self._burst_next: list[Spin] | None = None
        self._ahead: dict[tuple[Any, ...], asyncio.Future[Any]] = {}
        self._painting: asyncio.Task[None] | None = None
        # Entre gastar lo sorteado y enseñar la jugada, lo que se ve aún no es lo que
        # queda: no se pinta nada por adelantado hasta el siguiente `prepare`.
        self._changing = False
        # Lo que enseña la imagen: los rodillos de la última tirada y, en el doble o
        # nada, las últimas cartas y si ya se ve el panel de la carta.
        self.last_stops: tuple[int, int, int] = (0, 0, 0)
        self.double_history: list[str] = []
        self.gamble_shown = False
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

    def _build_buttons(self) -> None:
        green, blue = discord.ButtonStyle.success, discord.ButtonStyle.primary
        self.spin_button = self._add("🎰 Tirar", 0, self._spin, style=green, custom_id="spin")
        self.burst_button = self._add(
            f"🔁 Ráfaga ×{BURST_SPINS}", 0, self._burst, style=blue, custom_id="auto"
        )
        self.autoplay_button = self._add(
            "▶️ Auto", 0, self._autoplay_click, style=blue, custom_id="autoplay"
        )
        self.turbo_button = self._add("⚡ Turbo", 0, self._toggle_turbo, custom_id="turbo")
        self._add("½", 1, self._halve, custom_id="half")
        self._add("×2", 1, self._double_stake, custom_id="x2")
        self._add("💰 All-in", 1, self._all_in, custom_id="allin")
        self._add("📋 Premios", 1, self._paytable, custom_id="paytable")
        self.add_switch_button(1)
        red = discord.ButtonStyle.danger
        self.respin_button = self._add(
            "🔁 Re-girar", 2, self._respin, style=red, custom_id="respin"
        )
        self.red_button = self._add(
            COLOR_NAMES[RED], 2, self._double_red, style=red, custom_id="red"
        )
        self.black_button = self._add(COLOR_NAMES[BLACK], 2, self._double_black, custom_id="black")
        self.daily_button = self._add(
            "🎁 Giro del día", 2, self._daily, style=green, custom_id="daily"
        )
        self._set_enabled(True)

    def _set_enabled(self, enabled: bool) -> None:
        """Activa o desactiva los botones y pone al día sus etiquetas."""
        running = self.autoplay is not None
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                # Con ▶️ Auto en marcha solo queda el botón de Parar.
                item.disabled = running or not enabled
        stopping = running and self.autoplay is not None and self.autoplay.stop_requested
        if running:
            self.autoplay_button.disabled = stopping
            self.autoplay_button.label = "⏹️ Parando…" if stopping else "⏹️ Parar"
            self.autoplay_button.style = discord.ButtonStyle.danger
        else:
            self.autoplay_button.label = "▶️ Auto"
            self.autoplay_button.style = discord.ButtonStyle.primary
        if self.free_spins:
            self.spin_button.label = f"🎟️ Giro gratis ({self.free_spins})"
            self.spin_button.style = discord.ButtonStyle.primary
        else:
            self.spin_button.label = f"🎰 Tirar · {format_amount(self.stake)}"
            self.spin_button.style = discord.ButtonStyle.success
        self.turbo_button.label = "⚡ Turbo: sí" if self.turbo else "⚡ Turbo"
        self.turbo_button.style = (
            discord.ButtonStyle.success if self.turbo else discord.ButtonStyle.secondary
        )
        # La fila de abajo solo enseña lo que se puede hacer ahora mismo.
        extras = {
            self.respin_button: self.respin_offer is not None and not running,
            self.red_button: self.double_offer is not None and not running,
            self.black_button: self.double_offer is not None and not running,
            self.daily_button: self.daily_streak > 0 and not running,
        }
        for button, visible in extras.items():
            if visible and button not in self.children:
                self.add_item(button)
            elif not visible and button in self.children:
                self.remove_item(button)
        if self.respin_offer is not None:
            price = self.respin_offer[2]
            self.respin_button.label = f"🔁 Re-girar el 3º · {format_amount(price)}"
        if self.double_offer is not None:
            amount = format_amount(self.double_offer[0])
            self.red_button.label = f"{COLOR_NAMES[RED]} · {amount}"
            self.black_button.label = f"{COLOR_NAMES[BLACK]} · {amount}"
        if self.daily_streak:
            stake = format_amount(daily_stake(self.daily_streak))
            self.daily_button.label = f"🎁 Giro del día · {stake}"

    # -- Ciclo de vida --------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño juega en su máquina; al resto se le invita a abrir la suya."""
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message(
            f"Esta máquina es de {self.owner.display_name}. Abre la tuya con `tragas`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Cierra la máquina y juega los giros gratis que se hayan quedado sin usar."""
        self.cog.machines.discard(self)
        self.leave_picker()
        await self.close_autoplay()
        plays = await self.flush_free_spins()
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        kwargs: dict[str, Any] = {"view": self}
        summary = ""
        if plays:
            summary = auto_text(plays, "Giros gratis jugados al cerrar la máquina.") + "\n\n"
        if self.session_spins:
            summary += ticket_text(
                spins=self.session_spins,
                staked=self.session_staked,
                gross=self.session_gross,
                tax=self.session_tax,
            )
        if summary:
            kwargs["embed"] = await self.current_embed(text=summary)
        try:
            if self._last_interaction is not None:
                await self._last_interaction.edit_original_response(**kwargs)
            elif self.message is not None:
                await self.message.edit(**kwargs)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar la tragaperras", exc_info=True)
        await self._track_ticket()

    async def _track_ticket(self) -> None:
        """Logros del ticket de la sesión, al cerrar la máquina o cambiar a otra."""
        if not self.session_spins:
            return
        await logros.track(
            self.cog.bot,
            self.guild_id,
            self.owner,
            getattr(self.message, "channel", None),
            slots_ticket_stats(
                spins=self.session_spins,
                gross=self.session_gross,
                staked=self.session_staked,
                net=self.session_gross - self.session_staked - self.session_tax,
            ),
        )

    def switch_blocker(self) -> str | None:
        """Con giros gratis pendientes no se cambia: se perderían."""
        if self.free_spins:
            return (
                f"Te quedan {self.free_spins} giros gratis en esta máquina. "
                "Júgalos antes de cambiar."
            )
        if self.autoplay is not None:
            return "Para el ▶️ Auto antes de cambiar de máquina."
        return None

    async def retire(self) -> str | None:
        """Cierra la máquina tras 🔀: deja de atender clics y devuelve su ticket, si hay."""
        self.cog.machines.discard(self)
        self.stop()
        if not self.session_spins:
            return None
        await self._track_ticket()
        return ticket_text(
            spins=self.session_spins,
            staked=self.session_staked,
            gross=self.session_gross,
            tax=self.session_tax,
        )

    async def close_autoplay(self) -> None:
        """Detiene un ▶️ Auto en marcha: acaba la tirada en curso y cierra la tarea."""
        session = self.autoplay
        if session is None:
            return
        await session.close()
        if self.autoplay is session:
            # La tarea se canceló antes de poder cerrarse sola: deja la vista limpia.
            self.autoplay = None
            self.timeout = MACHINE_TIMEOUT
            self._busy = False
            self._set_enabled(True)

    async def flush_free_spins(self) -> list[SlotsPlay]:
        """Juega los giros gratis pendientes sin animación; nadie pierde lo ganado.

        Lo usan el cierre por inactividad y el apagado del bot.
        """
        plays: list[SlotsPlay] = []
        while self.free_spins:
            try:
                plays.append(await self._play_one(turbo=True, render=False))
            except Exception:
                logger.exception("No se pudo jugar un giro gratis al cerrar la máquina")
                break
        for play in plays:
            await self._track(play)
        return plays

    # -- Juego ----------------------------------------------------------------------

    async def balance(self) -> int:
        """Saldo actual del dueño de la máquina."""
        return await self.cog.economy.balance(self.guild_id, self.owner.id)

    async def current_embed(
        self, *, balance: int | None = None, pot: int | None = None, text: str | None = None
    ) -> discord.Embed:
        """Embed de reposo con el saldo y el bote actuales."""
        if balance is None:
            balance = await self.balance()
        if pot is None:
            pot = await self.cog.pot(self.guild_id)
        spins_since = await self.cog.spins_since(self.guild_id)
        if text is None:
            text = self.last_text
        if self.cooled:
            note = f"🧊 La máquina se ha enfriado mientras no estabas: -{self.cooled} 🔥"
            text = f"{text}\n{note}" if text else note
        return machine_embed(
            owner=self.owner.display_name,
            balance=balance,
            stake=self.stake,
            pot=pot,
            heat=self.cog.heat(self.guild_id, self.owner.id),
            free_spins=self.free_spins,
            free_stake=self.free_stake,
            turbo=self.turbo,
            text=text,
            won=self.last_won,
            last_jackpot=await self.cog.last_jackpot_text(self.guild_id),
            spins_since=spins_since,
            session_gross=self.session_gross,
            session_prizes=self.session_prizes,
            bonus=self.cog.bonus(self.guild_id, self.owner.id).points,
        )

    async def _play_one(
        self, *, turbo: bool, render: bool = True, spin: Spin | None = None
    ) -> SlotsPlay:
        """Una tirada: gratis si quedan giros gratis, pagada si no.

        Args:
            spin: Los rodillos ya sorteados (una Ráfaga trae los suyos); por defecto,
                la próxima tirada sorteada por adelantado, si la hay.

        Raises:
            InsufficientFundsError, BalanceLimitError: Como `Slots.play`.
        """
        free = self.free_spins > 0
        stake = self.free_stake if free else self.stake
        play = await self.cog.play(
            self.guild_id,
            self.owner.id,
            stake=stake,
            free=free,
            turbo=turbo,
            render=render,
            session_spins=self.session_spins + 1,
            spin=spin if spin is not None else self._take_spin(),
            ready=self._ready,
        )
        self.session_spins += 1
        if free:
            self.free_spins -= 1
        self._grant(play)
        self._account(play)
        return play

    def _grant(self, play: SlotsPlay) -> None:
        """Apunta los giros gratis que da la tirada: 🎟️ 🎟️ 🎟️ y la barra de bonus llena.

        Todos los pendientes se juegan a una sola apuesta. Si se juntan dos
        apuestas distintas, se queda la menor: no hay forma de llenar algo
        barato y cobrarlo caro.
        """
        grants = []
        if play.spin.triggers_free_spins:
            grants.append((FREE_SPINS, play.stake))
        if play.bonus_stake:
            grants.append((BONUS_FREE_SPINS, play.bonus_stake))
        for count, stake in grants:
            self.free_stake = min(self.free_stake, stake) if self.free_spins else stake
            self.free_spins += count

    def _account(self, play: SlotsPlay) -> None:
        """Suma la tirada al ticket de la sesión y retira las ofertas de la anterior."""
        self.session_staked += play.paid_stake
        self.session_gross += play.won
        self.session_prizes += play.won > 0
        self.session_tax += play.settlement.bet.tax_delta
        self.cooled = 0
        self.respin_offer = None
        self.double_offer = None

    def _offer(self, play: SlotsPlay) -> None:
        """Lo que se puede hacer tras una tirada a mano: re-girar si roza, doblar si cobra."""
        if play.spin.teaser is not None:
            price = respin_price(play.spin, play.stake, play.settlement.pot)
            self.respin_offer = (play.spin, play.stake, price, play.respin_chain + 1)
        if play.won > 0:
            self.double_offer = (play.won, 0)

    # -- Pintar antes del clic ---------------------------------------------------------

    def _next_spin(self) -> Spin:
        """La próxima tirada, sorteada la primera vez que hace falta."""
        if self._upcoming is None:
            self._upcoming = self.cog.machine.spin()
        return self._upcoming

    def _take_spin(self) -> Spin | None:
        """La tirada sorteada por adelantado, que se gasta al jugarla (`None` si no hay)."""
        spin, self._upcoming = self._upcoming, None
        self._changing = True
        return spin

    def _next_coin(self) -> bool:
        if self._coin_next is None:
            self._coin_next = self.cog.coin()
        return self._coin_next

    def _take_coin(self) -> bool:
        """El doble o nada sorteado por adelantado (o uno nuevo), que se gasta al jugarlo."""
        won, self._coin_next = self._coin_next, None
        self._changing = True
        return self.cog.coin() if won is None else won

    def _next_respin(self, spin: Spin) -> Spin:
        if self._respin_next is None or self._respin_next[0] is not spin:
            self._respin_next = (spin, self.cog.machine.respin(spin))
        return self._respin_next[1]

    def _take_respin(self, spin: Spin) -> Spin | None:
        """El re-giro de `spin` sorteado por adelantado, si lo hay."""
        ready, self._respin_next = self._respin_next, None
        self._changing = True
        return ready[1] if ready is not None and ready[0] is spin else None

    def _next_burst(self) -> list[Spin]:
        if self._burst_next is None:
            self._burst_next = [self.cog.machine.spin() for _ in range(BURST_SPINS)]
        return self._burst_next

    def _take_burst(self) -> list[Spin] | None:
        spins, self._burst_next = self._burst_next, None
        self._changing = True
        return spins

    def _ready(self, key: tuple[Any, ...]) -> asyncio.Future[Any] | None:
        """Lo pintado (o pintándose) por adelantado para `key`; se gasta al usarlo."""
        return self._ahead.pop(key, None)

    def _double_job(
        self, amount: int, pick: str, won: bool
    ) -> tuple[tuple[Any, ...], Callable[[], Awaitable[SlotsMedia | None]]]:
        """La clave y el dibujo del doble o nada tal como está la máquina ahora."""
        stops, history, fresh = self.last_stops, list(self.double_history), not self.gamble_shown
        key = ("double", stops, amount, pick, won, tuple(history), fresh)

        def paint() -> Awaitable[SlotsMedia | None]:
            return self.cog.renderer.double(
                stops=stops, amount=amount, pick=pick, won=won, history=history, fresh=fresh
            )

        return key, paint

    async def _plans(self) -> list[tuple[tuple[Any, ...], Callable[[], Awaitable[Any]]]]:
        """Cada botón que se puede pulsar ahora y su imagen, del más probable al que menos.

        Tirar (o el giro gratis), Rojo y Negro, el re-giro, el giro del día, Tirar con la
        apuesta de ½, ×2 y All-in (solo si la próxima tirada paga: sin premio, la imagen
        es la misma con cualquier apuesta) y la imagen final de una Ráfaga.
        """
        scene = self.cog.renderer
        turbo = self.turbo
        hot = self.cog.heat(self.guild_id, self.owner.id) >= HEAT_MAX
        spin = self._next_spin()
        offer = self.respin_offer
        again = self._next_respin(offer[0]) if offer is not None else None
        pot = 0
        if spin.is_jackpot or (again is not None and again.is_jackpot):
            pot = await self.cog.pot(self.guild_id)

        def tirada(
            sp: Spin, stake: int, *, hot: bool, respin: bool = False
        ) -> tuple[tuple[Any, ...], Callable[[], Awaitable[SlotsMedia]]]:
            won = line_payout(sp, stake, hot=hot) + (pot if sp.is_jackpot else 0)
            key = media_key(sp, won=won, stake=stake, turbo=turbo, respin=respin)
            return key, lambda: scene.spin(sp, turbo=turbo, won=won, stake=stake, respin=respin)

        free = self.free_spins > 0
        stake = self.free_stake if free else self.stake
        plans = [tirada(spin, stake, hot=hot)]
        if self.double_offer is not None:
            won = self._next_coin()
            plans += [self._double_job(self.double_offer[0], pick, won) for pick in (RED, BLACK)]
        if offer is not None and again is not None:
            plans.append(tirada(again, offer[1], hot=False, respin=True))
        if self.daily_streak:
            plans.append(tirada(spin, daily_stake(self.daily_streak), hot=hot))
        if not free and (spin.pay_halves or spin.is_jackpot):
            balance = await self.balance()
            for other in (max(1, stake // 2), max(1, min(stake * 2, balance)), balance):
                if 0 < other <= balance:
                    plans.append(tirada(spin, other, hot=hot))
        burst = self._next_burst()
        # La Ráfaga para en el bote: su imagen es la de esa tirada.
        last = next((sp for sp in burst if sp.is_jackpot), burst[-1])
        lit = last.pay_halves > 0 or last.is_jackpot
        plans.append((("still", last.stops, lit), lambda: scene.still(last, highlight=lit)))
        # Sin repetir (la apuesta del giro del día puede ser la misma): el primero manda.
        unique: dict[tuple[Any, ...], Callable[[], Awaitable[Any]]] = {}
        for key, paint in plans:
            unique.setdefault(key, paint)
        return list(unique.items())

    def prepare(self) -> None:
        """Pinta en segundo plano la imagen de cada botón que se puede pulsar ahora.

        Se llama al enseñar una jugada (en cuanto la máquina queda como se va a ver), al
        abrir la máquina y al cambiar la apuesta o el turbo. Pinta de una en una, de la
        más probable a la menos (`_plans`), y antes de cada una vuelve a mirar qué hace
        falta: lo que ya no vale se suelta y nunca se corta un dibujo a medias.
        """
        self._changing = False
        if not self.cog.ahead or self.is_finished():
            return
        if self._painting is None or self._painting.done():
            self._painting = asyncio.create_task(
                self._paint_ahead(), name=f"slots-ahead-{self.guild_id}-{self.owner.id}"
            )

    async def _paint_ahead(self) -> None:
        while not self.is_finished() and not self._changing:
            plans = await self._plans()
            if self._changing:
                return
            wanted = {key for key, _ in plans}
            for key in [k for k, f in self._ahead.items() if f.done() and k not in wanted]:
                del self._ahead[key]
            todo = next(((key, paint) for key, paint in plans if key not in self._ahead), None)
            if todo is None:
                return
            key, paint = todo
            future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
            self._ahead[key] = future
            result = None
            try:
                result = await paint()
            except Exception:
                logger.warning("No se pudo pintar por adelantado la tragaperras", exc_info=True)
            finally:
                # Quien ya lo espera (un clic que llegó a mitad) no se queda colgado.
                if not future.done():
                    future.set_result(result)

    async def _guarded_play(
        self, interaction: discord.Interaction, *, turbo: bool, render: bool = True
    ) -> SlotsPlay | None:
        """`_play_one` con los errores de saldo convertidos en avisos privados."""
        try:
            return await self._play_one(turbo=turbo, render=render)
        except InsufficientFundsError as error:
            self.prepare()
            await notify(interaction, insufficient_text(error.balance, self.stake))
        except BalanceLimitError:
            self.prepare()
            await notify(interaction, "La banca no puede pagar tanto. Baja la apuesta.")
        return None

    async def play(self, interaction: discord.Interaction) -> None:
        """Respuesta a 🎰 Tirar: cobra, gira, enseña y paga."""
        if self._busy:
            # Doble clic mientras gira: se ignora sin mostrar error.
            await ack(interaction)
            return
        self._busy = True
        try:
            # Cobrar y dibujar va a la base de datos y tarda: se acepta el clic antes.
            await ack(interaction)
            play = await self._guarded_play(interaction, turbo=self.turbo)
            if play is None:
                return
            self._offer(play)
            self._last_interaction = interaction
            await self.show(
                play,
                first_edit=interaction.edit_original_response,
                final_edit=interaction.edit_original_response,
            )
        finally:
            self._busy = False
        await self._after_play(interaction, play)

    async def show(
        self,
        play: SlotsPlay,
        *,
        first_edit: EditFn,
        final_edit: EditFn,
        note: str | None = None,
    ) -> None:
        """Enseña la tirada: el GIF y después el PNG final (o solo el PNG en turbo).

        El dinero ya está cobrado y pagado: si Discord falla al editar, el
        saldo sigue siendo correcto.

        Args:
            note: Línea pequeña bajo el resultado (el contador de ▶️ Auto).
        """
        self.last_stops = play.spin.stops
        self.gamble_shown = False
        # La máquina ya está como se va a ver: se pinta lo siguiente mientras se sube esto.
        self.prepare()
        if play.media.gif:
            self._set_enabled(False)
            await first_edit(
                embed=spinning_embed(
                    owner=self.owner.display_name,
                    stake=play.stake,
                    free=play.free,
                    hot=play.hot,
                    pot=play.settlement.pot,
                ),
                attachments=[discord.File(io.BytesIO(play.media.gif), filename=GIF_NAME)],
                view=self,
            )
            await asyncio.sleep(reveal_delay(play.media))
            edit = final_edit
        else:
            edit = first_edit

        text = result_text(play)
        if tax_line := tax_note(play.settlement.bet.tax_delta, [play]):
            text += f"\n{tax_line}"
        if renta_hint := await renta.hint(
            self.cog.bot,
            self.guild_id,
            self.owner.id,
            bet_moment(stake=play.paid_stake, net=play.net, balance_after=play.balance),
        ):
            text += f"\n{renta_hint}"
        if note:
            text += f"\n{note}"
        self.last_text = text
        self.last_won = play.won > 0
        self._set_enabled(True)
        await edit(
            embed=await self.current_embed(balance=play.balance, pot=play.settlement.pot),
            attachments=[discord.File(io.BytesIO(play.media.png), filename=PNG_NAME)],
            view=self,
        )

    async def _track(self, play: SlotsPlay, *, autoplay: bool = False) -> None:
        """Logros de la tirada, después de enseñarla (antes destriparía el resultado)."""
        await self.cog.track(
            play,
            self.guild_id,
            self.owner,
            getattr(self.message, "channel", None),
            autoplay=autoplay,
        )

    async def _spin(self, interaction: discord.Interaction) -> None:
        await self.play(interaction)

    async def _after_play(self, interaction: discord.Interaction, play: SlotsPlay) -> None:
        """Lo que no hace falta para enseñar la tirada: Renta, logros y anuncio."""
        await renta.remind(self.cog.bot, interaction)
        await self._track(play)
        await self.cog.shout(play, self.owner, getattr(self.message, "channel", None))

    async def _respin(self, interaction: discord.Interaction) -> None:
        """🔁 Re-girar el 3º: cobra el precio y vuelve a girar solo el tercer rodillo.

        El precio se recalcula al pulsar con el bote de ese momento: si otro
        jugador lo ha hecho crecer y el re-giro sale más caro que lo que dice
        el botón, no se cobra y se avisa del precio nuevo.
        """
        offer = self.respin_offer
        if self._busy or offer is None:
            await ack(interaction)
            return
        self._busy = True
        play: SlotsPlay | None = None
        try:
            await ack(interaction)
            spin, stake, shown, chain = offer
            price = respin_price(spin, stake, await self.cog.pot(self.guild_id))
            if price > shown:
                self.respin_offer = (spin, stake, price, chain)
                self._set_enabled(True)
                await edit(interaction, view=self)
                await notify(
                    interaction,
                    f"El bote ha subido y el re-giro ahora cuesta {format_amount(price)}.",
                )
                return
            try:
                play = await self.cog.respin(
                    self.guild_id,
                    self.owner.id,
                    spin=spin,
                    stake=stake,
                    price=price,
                    turbo=self.turbo,
                    session_spins=self.session_spins + 1,
                    chain=chain,
                    again=self._take_respin(spin),
                    ready=self._ready,
                )
            except InsufficientFundsError as error:
                self.prepare()
                await notify(interaction, insufficient_text(error.balance, price))
                return
            except BalanceLimitError:
                self.prepare()
                await notify(interaction, "La banca no puede pagar tanto. Baja la apuesta.")
                return
            self.session_spins += 1
            self._account(play)
            self._offer(play)
            self._last_interaction = interaction
            await self.show(
                play,
                first_edit=interaction.edit_original_response,
                final_edit=interaction.edit_original_response,
            )
        finally:
            self._busy = False
        if play is not None:
            await self._after_play(interaction, play)

    async def _double_red(self, interaction: discord.Interaction) -> None:
        await self._double(interaction, RED)

    async def _double_black(self, interaction: discord.Interaction) -> None:
        await self._double(interaction, BLACK)

    async def _double(self, interaction: discord.Interaction, color: str) -> None:
        """🔴/⚫ Doble o nada con lo último cobrado: cara o cruz, el color da igual.

        Es una apuesta del casino como otra (`settle_bet`, juego en el IRPF):
        lo cobrado ya está en el saldo y se vuelve a apostar. Se ve como una carta
        que se gira (`SlotsScene.double`): primero el GIF y luego el PNG con la
        carta destapada. Sin Node ni navegador, solo cambia el texto.
        """
        offer = self.double_offer
        if self._busy or offer is None:
            await ack(interaction)
            return
        self._busy = True
        bet = None
        # La carta que se ha visto (solo si se ha pintado) y cuántas seguidas de su palo.
        card: str | None = None
        run = 0
        try:
            await ack(interaction)
            amount, chain = offer
            won = self._take_coin()
            try:
                bet = await self.cog.economy.settle_bet(
                    self.guild_id,
                    self.owner.id,
                    game=GAME,
                    stake=amount,
                    payout=2 * amount if won else 0,
                )
            except InsufficientFundsError as error:
                self.double_offer = None
                self.prepare()
                self._set_enabled(True)
                await edit(interaction, view=self)
                await notify(interaction, insufficient_text(error.balance, amount))
                return
            except BalanceLimitError:
                self.prepare()
                await notify(interaction, "La banca no puede pagar tanto.")
                return
            chain = chain + 1 if won else chain
            self.session_staked += amount
            self.session_gross += 2 * amount if won else 0
            self.session_tax += bet.tax_delta
            self.respin_offer = None
            self.double_offer = (2 * amount, chain) if won and chain < DOUBLE_MAX else None
            shown = color if won else (BLACK if color == RED else RED)
            if won:
                lines = [
                    f"# {COLOR_NAMES[shown]} · {random.choice(DOUBLE_WIN_LINES)}",
                    f"## +{format_amount(amount)} · llevas {format_amount(2 * amount)}",
                ]
                if self.double_offer is None:
                    lines.append(f"Tope de {DOUBLE_MAX} dobles seguidos: te lo quedas.")
            else:
                lines = [
                    f"# {COLOR_NAMES[shown]}",
                    f"## -{format_amount(amount)} · {random.choice(DOUBLE_LOSS_LINES)}",
                ]
            if tax_line := gambling_tax_line(bet):
                lines.append(tax_line)
            self.last_text = "\n".join(lines)
            self.last_won = won
            key, paint = self._double_job(amount, color, won)
            ready = self._ready(key)
            media = await ready if ready is not None else None
            if media is None:
                media = await paint()
            if media is not None:
                card = double_suit(amount=amount, pick=color, won=won, history=self.double_history)
                self.double_history = [*self.double_history, card][-DOUBLE_HISTORY:]
                run = len(self.double_history) - len("".join(self.double_history).rstrip(card))
                self.gamble_shown = True
            self.prepare()
            if media is not None:
                self._set_enabled(False)
                await edit(
                    interaction,
                    embed=double_embed(owner=self.owner.display_name, amount=amount, color=color),
                    attachments=[discord.File(io.BytesIO(media.gif), filename=GIF_NAME)],
                    view=self,
                )
                await asyncio.sleep(reveal_delay(media))
            self._set_enabled(True)
            final: dict[str, Any] = {}
            if media is not None:
                final["attachments"] = [discord.File(io.BytesIO(media.png), filename=PNG_NAME)]
            await edit(
                interaction,
                embed=await self.current_embed(balance=bet.balance),
                view=self,
                **final,
            )
            self._last_interaction = interaction
        finally:
            self._busy = False
        if bet is None:
            return
        net = amount if won else -amount
        await renta.remind(self.cog.bot, interaction)
        delta = slots_double_stats(amount=amount, won=won, chain=chain, suit=card, run=run)
        delta.merge(
            casino_stats(stake=amount, net=net, balance_after=bet.balance, tax_delta=bet.tax_delta)
        )
        channel = getattr(self.message, "channel", None)
        await logros.casino_play(self.cog.bot, self.guild_id, self.owner, channel, delta, net=net)
        await apuestas.record(
            self.cog.bot,
            self.guild_id,
            self.owner,
            game=GAME,
            stake=amount,
            net=net,
            balance_after=bet.balance,
            tax=bet.tax_delta,
        )

    async def _daily(self, interaction: discord.Interaction) -> None:
        """🎁 Giro del día: gratis, a una apuesta que sube con la racha de días seguidos.

        Lo cobrado es juego, como un giro gratis. Apuntarlo es atómico: dos
        clics (o dos máquinas abiertas) no dan dos giros.
        """
        if self._busy or not self.daily_streak:
            await ack(interaction)
            return
        self._busy = True
        play: SlotsPlay | None = None
        try:
            await ack(interaction)
            streak = await self.cog.claim_daily(self.guild_id, self.owner.id)
            self.daily_streak = 0
            if streak is None:
                self.prepare()
                self._set_enabled(True)
                await edit(interaction, view=self)
                await notify(interaction, "Ya has cobrado el giro de hoy. Vuelve mañana.")
                return
            try:
                play = await self.cog.play(
                    self.guild_id,
                    self.owner.id,
                    stake=daily_stake(streak),
                    free=False,
                    turbo=self.turbo,
                    session_spins=self.session_spins + 1,
                    daily_streak=streak,
                    spin=self._take_spin(),
                    ready=self._ready,
                )
            except BalanceLimitError:
                await notify(interaction, "La banca no puede pagar tanto.")
                return
            self.session_spins += 1
            self._grant(play)
            self._account(play)
            self._offer(play)
            self._last_interaction = interaction
            await self.show(
                play,
                first_edit=interaction.edit_original_response,
                final_edit=interaction.edit_original_response,
            )
        finally:
            self._busy = False
        if play is not None:
            await self._after_play(interaction, play)

    async def _burst(self, interaction: discord.Interaction) -> None:
        """🔁 Ráfaga: diez tiradas seguidas sin animación y un solo resumen.

        Para antes si se acaba el dinero o sale el bote. Los giros gratis que
        salgan por el camino se juegan dentro de la misma ronda.
        """
        if self._busy:
            await ack(interaction)
            return
        self._busy = True
        plays: list[SlotsPlay] = []
        stopped: str | None = None
        try:
            await ack(interaction)
            self._last_interaction = interaction
            spins = self._take_burst() or []
            for index in range(BURST_SPINS):
                try:
                    spin = spins[index] if index < len(spins) else None
                    plays.append(await self._play_one(turbo=True, render=False, spin=spin))
                except InsufficientFundsError:
                    stopped = "Parado: no te llega para otra tirada."
                    break
                except BalanceLimitError:
                    stopped = "Parado: la banca no puede pagar tanto."
                    break
                if plays[-1].jackpot:
                    stopped = "Parado: ¡ha salido el bote!"
                    break
            if not plays:
                self.prepare()
                await notify(interaction, insufficient_text(await self.balance(), self.stake))
                return
            last = plays[-1]
            self.last_stops = last.spin.stops
            self.gamble_shown = False
            lit = last.spin.pay_halves > 0 or last.spin.is_jackpot
            ready = self._ready(("still", last.spin.stops, lit))
            png = await ready if ready is not None else None
            if png is None:
                png = await self.cog.renderer.still(last.spin, highlight=lit)
            self.prepare()
            text = auto_text(plays, stopped)
            if note := tax_note(sum(p.settlement.bet.tax_delta for p in plays), plays):
                text += f"\n{note}"
            if renta_hint := await renta.hint(
                self.cog.bot,
                self.guild_id,
                self.owner.id,
                bet_moment(
                    stake=sum(p.paid_stake for p in plays),
                    net=sum(p.net for p in plays),
                    balance_after=plays[-1].balance,
                ),
            ):
                text += f"\n{renta_hint}"
            self.last_text = text
            self.last_won = sum(p.net for p in plays) > 0
            self._set_enabled(True)
            await interaction.edit_original_response(
                embed=await self.current_embed(balance=last.balance, pot=last.settlement.pot),
                attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
                view=self,
            )
        finally:
            self._busy = False
        await renta.remind(self.cog.bot, interaction)
        for play in plays:
            await self._track(play)
        await logros.track(
            self.cog.bot,
            self.guild_id,
            self.owner,
            getattr(self.message, "channel", None),
            StatDelta(add={"slots_auto": 1}),
        )
        for play in plays:
            await self.cog.shout(play, self.owner, getattr(self.message, "channel", None))

    # -- ▶️ Auto ---------------------------------------------------------------------

    def _message_edit(self, interaction: discord.Interaction) -> EditFn:
        """Cómo editar el mensaje de la máquina sin el token de la interacción.

        El token caduca a los 15 minutos y un ▶️ Auto largo puede pasarse. El mensaje
        de un slash command es un `InteractionMessage`, cuyo `edit` usa ese token: se
        edita entonces por el canal. Un mensaje normal (`.tragas`) ya edita con el
        token del bot. Sin mensaje guardado, se cae a la respuesta de la interacción.
        """
        message = self.message
        if message is None:
            return interaction.edit_original_response
        if isinstance(message, discord.InteractionMessage):
            get_partial = getattr(message.channel, "get_partial_message", None)
            if get_partial is not None:
                return get_partial(message.id).edit
        return message.edit

    async def _autoplay_click(self, interaction: discord.Interaction) -> None:
        """▶️ Auto empieza una sesión; con la sesión en marcha, el mismo botón es ⏹️ Parar."""
        session = self.autoplay
        if session is not None:
            await self._stop_autoplay(interaction, session)
            return
        if self._busy:
            # Doble clic o una tirada en curso: se acepta el clic y se ignora.
            await ack(interaction)
            return
        self._busy = True
        started = False
        try:
            await ack(interaction)
            self._last_interaction = interaction
            session = AutoplaySession(
                stake=self.stake, max_spins=AUTOPLAY_MAX, min_gap=AUTOPLAY_MIN_GAP
            )
            self.autoplay = session
            # Sin clics durante la sesión, la vista caducaría a los 3 minutos.
            self.timeout = None
            edit_fn = self._message_edit(interaction)

            async def step(number: int) -> SpinResult:
                return await self._autoplay_step(interaction, session, number, edit_fn)

            async def finish(outcome: AutoplayOutcome) -> None:
                await self._autoplay_finish(interaction, session, outcome, edit_fn)

            session.start(
                step,
                on_finish=finish,
                name=f"slots-autoplay-{self.guild_id}-{self.owner.id}",
            )
            started = True
        finally:
            if not started:
                self.autoplay = None
                self.timeout = MACHINE_TIMEOUT
                self._busy = False

    async def _stop_autoplay(
        self, interaction: discord.Interaction, session: AutoplaySession
    ) -> None:
        """⏹️ Parar: marca el flag y contesta ya, sin base de datos (solo memoria)."""
        if not session.armed:
            # El mensaje aún no enseña el botón de Parar: es el doble clic de ▶️ Auto.
            await ack(interaction)
            return
        session.request_stop()
        self._set_enabled(True)
        await interaction.response.edit_message(view=self)

    async def _autoplay_step(
        self,
        interaction: discord.Interaction,
        session: AutoplaySession,
        number: int,
        edit_fn: EditFn,
    ) -> SpinResult:
        """Una tirada de ▶️ Auto: igual que 🎰 Tirar, pero editando sin token.

        Raises:
            AutoplayStop: Si no llega el saldo o la banca no puede pagar (antes de cobrar).
        """
        try:
            play = await self._play_one(turbo=self.turbo)
        except InsufficientFundsError:
            raise AutoplayStop(StopReason.NO_FUNDS) from None
        except BalanceLimitError:
            raise AutoplayStop(StopReason.BANK_LIMIT) from None

        async def first_edit(**kwargs: Any) -> None:
            await edit_fn(**kwargs)
            session.armed = True

        sign = "+" if session.net + play.net > 0 else "-" if session.net + play.net < 0 else "±"
        note = (
            f"-# ▶️ Auto · tirada {number}/{session.max_spins} · "
            f"neto {sign}{format_amount(abs(session.net + play.net))}"
        )
        stop: StopReason | None = None
        try:
            await self.show(play, first_edit=first_edit, final_edit=edit_fn, note=note)
        except discord.HTTPException:
            # Mensaje borrado o sin permisos: seguir jugando a ciegas gastaría dinero.
            logger.warning("No se pudo editar la tragaperras en pleno Auto", exc_info=True)
            stop = StopReason.CLOSED
        edited_at = time.monotonic()
        channel = getattr(self.message, "channel", None)
        await self._track(play, autoplay=True)
        await self.cog.shout(play, self.owner, channel)
        if number == 1:
            await renta.remind(self.cog.bot, interaction)
        return SpinResult(net=play.net, big_prize=play.big_prize, edited_at=edited_at, stop=stop)

    async def _autoplay_finish(
        self,
        interaction: discord.Interaction,
        session: AutoplaySession,
        outcome: AutoplayOutcome,
        edit_fn: EditFn,
    ) -> None:
        """Cierra la sesión: devuelve los botones, enseña el resumen y apunta los logros."""
        try:
            self.autoplay = None
            self.timeout = MACHINE_TIMEOUT
            self.prepare()
            self._set_enabled(True)
            try:
                if outcome.spins == 0:
                    if outcome.reason is StopReason.NO_FUNDS:
                        await notify(
                            interaction, insufficient_text(await self.balance(), self.stake)
                        )
                    elif outcome.reason is not StopReason.CLOSED:
                        await notify(interaction, outcome.reason_text())
                    if session.armed:
                        await edit_fn(embed=await self.current_embed(), view=self)
                else:
                    # El resumen va encima de la última tirada para que se siga viendo.
                    text = outcome.summary()
                    if self.last_text:
                        text += f"\n\n{self.last_text}"
                    self.last_text = text
                    self.last_won = outcome.net > 0
                    await edit_fn(embed=await self.current_embed(), view=self)
            except discord.HTTPException:
                logger.debug("No se pudo cerrar el resumen del Auto", exc_info=True)
        finally:
            self._busy = False
        await logros.track(
            self.cog.bot,
            self.guild_id,
            self.owner,
            getattr(self.message, "channel", None),
            slots_autoplay_stats(spins=outcome.spins, net=outcome.net, reason=outcome.reason),
        )

    async def _refresh(self, interaction: discord.Interaction, balance: int | None = None) -> None:
        """Actualiza la máquina (apuesta, turbo) sin tocar la imagen.

        El embed lee saldo y bote de la base de datos: se acepta el clic antes.
        """
        await ack(interaction)
        self.prepare()
        self._set_enabled(True)
        await edit(interaction, embed=await self.current_embed(balance=balance), view=self)
        self._last_interaction = interaction

    async def _toggle_turbo(self, interaction: discord.Interaction) -> None:
        self.turbo = not self.turbo
        self.cog.set_turbo_default(self.guild_id, self.owner.id, self.turbo)
        await self._refresh(interaction)

    async def _halve(self, interaction: discord.Interaction) -> None:
        self.stake = max(1, self.stake // 2)
        await self._refresh(interaction)

    async def _double_stake(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        balance = await self.balance()
        # Si el doble no cabe, se queda en todo el saldo: es lo que se busca.
        self.stake = max(1, min(self.stake * 2, balance))
        await self._refresh(interaction, balance)

    async def _all_in(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        balance = await self.balance()
        if balance == 0:
            await notify(interaction, insufficient_text(0))
            return

        self.stake = balance
        await self._refresh(interaction, balance)

    async def _paytable(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=paytable_embed(), ephemeral=True)


# -- Cog ----------------------------------------------------------------------------


class Slots(commands.Cog, name="Tragaperras"):
    """La tragaperras del casino, con bote común por servidor."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        renderer: SlotsScene | None = None,
        machine: SlotMachine | None = None,
        casino_channel_ids: CasinoChannelIds = frozenset(),
        repository: SlotsRepository | None = None,
        clock: Callable[[], float] = time.time,
        coin: Callable[[], bool] | None = None,
        randbelow: Callable[[int], int] | None = None,
        ahead: bool | None = None,
    ) -> None:
        """Monta el cog.

        Args:
            renderer: El dibujo (`SlotsScene`); las pruebas pasan uno que no pinta.
            ahead: Si las máquinas pintan antes del clic (`SlotMachineView.prepare`). Por
                defecto, solo con el dibujo de verdad: con uno de prueba, cada sorteo de
                más gastaría las tiradas de guion de la prueba.
        """
        self.bot = bot
        self.economy = economy
        # Calor y giro diario guardados; `None` en pruebas que no los usan.
        self.repository = repository
        self.renderer = renderer or SlotsScene()
        self.ahead = renderer is None if ahead is None else ahead
        self.machine = machine or SlotMachine()
        self.casino_channel_ids = casino_channel_ids
        self.clock = clock
        # Cara o cruz del doble o nada; inyectable en pruebas.
        self.coin = coin or (lambda: random.SystemRandom().random() < 0.5)
        # Lo que sube la barra de bonus en cada tirada; inyectable en pruebas.
        self.randbelow = randbelow or random.SystemRandom().randrange
        # Calor (con la hora en que se tocó por última vez) y barra de bonus por
        # (servidor, miembro, máquina): la 0 es la de Discord y las demás, las de
        # la plaza del Jovaniverso. Crecen como mucho hasta los miembros que han
        # jugado por las máquinas de la plaza. Se guardan también en `repository`.
        self._heat: dict[tuple[int, int, int], int] = {}
        self._heat_at: dict[tuple[int, int, int], float] = {}
        self._bonus: dict[tuple[int, int, int], BonusMeter] = {}
        # Una tirada a la vez por máquina: entre leer el calor y guardarlo se espera
        # a la base de datos, y dos clics seguidos se pisarían.
        self._locks: dict[tuple[int, int, int], TimedLock] = {}
        self._turbo: dict[tuple[int, int], bool] = {}
        # Máquinas abiertas: para jugar sus giros gratis si el bot se apaga.
        self.machines: set[SlotMachineView] = set()
        # Las máquinas de la plaza del Jovaniverso (giros gratis y ▶️ Auto de cada una).
        self.seats = Seats()
        # Logros y anuncios de la plaza, que van después de contestar a la página.
        self._background: set[asyncio.Future[None]] = set()

    async def cog_unload(self) -> None:
        """Al apagar, juega los giros gratis pendientes y cierra el dibujo."""
        for view in list(self.machines):
            try:
                await view.close_autoplay()
                await view.flush_free_spins()
            except Exception:
                logger.exception("No se pudieron jugar los giros gratis al apagar")
            view.stop()
        self.machines.clear()
        if self._background:
            await asyncio.gather(*self._background, return_exceptions=True)
        await self.renderer.close()

    # -- Estado por miembro ----------------------------------------------------------

    def heat(self, guild_id: int, user_id: int, machine: int = 0) -> int:
        """Calor de una máquina de un miembro (sin aplicar el enfriado pendiente)."""
        return self._heat.get((guild_id, user_id, machine), 0)

    def bonus(self, guild_id: int, user_id: int, machine: int = 0) -> BonusMeter:
        """Barra de bonus de una máquina de un miembro."""
        return self._bonus.get((guild_id, user_id, machine), BonusMeter())

    async def load_heat(self, guild_id: int, user_id: int, machine: int = 0) -> None:
        """Trae calor y barra de bonus guardados la primera vez que se juega a esa máquina."""
        key = (guild_id, user_id, machine)
        if key in self._bonus or self.repository is None:
            return
        saved = await self.repository.load_heat(guild_id, user_id, machine)
        if saved is not None:
            self._heat[key], self._heat_at[key] = saved
        self._bonus[key] = await self.repository.load_bonus(guild_id, user_id, machine)

    def cool_down(self, guild_id: int, user_id: int, machine: int = 0) -> int:
        """Aplica el enfriado por el tiempo sin jugar y devuelve los puntos perdidos."""
        key = (guild_id, user_id, machine)
        heat = self._heat.get(key, 0)
        since = self._heat_at.get(key)
        if not heat or since is None:
            return 0
        cooled = decayed_heat(heat, self.clock() - since)
        if cooled == heat:
            return 0
        self._heat[key] = cooled
        # El resto del intervalo empezado sigue contando para el siguiente punto.
        self._heat_at[key] = since + (heat - cooled) * HEAT_DECAY_SECONDS
        return heat - cooled

    async def _save_heat(self, guild_id: int, user_id: int, machine: int = 0) -> None:
        key = (guild_id, user_id, machine)
        self._heat_at[key] = self.clock()
        if self.repository is not None:
            await self.repository.save_heat(
                guild_id,
                user_id,
                self._heat.get(key, 0),
                self._heat_at[key],
                self.bonus(*key),
                machine=machine,
            )

    def _lock(self, key: tuple[int, int, int]) -> TimedLock:
        """El candado de una máquina de un miembro, creado la primera vez."""
        if (lock := self._locks.get(key)) is None:
            lock = self._locks[key] = TimedLock("slots:Slots._locks")
        return lock

    async def daily_status(self, guild_id: int, user_id: int) -> tuple[bool, int]:
        """`(si puede cobrar hoy el giro del día, racha que tendría al cobrarlo)`."""
        if self.repository is None:
            return False, 0
        today = datetime.now(TIMEZONE).date()
        saved = await self.repository.daily(guild_id, user_id)
        if saved is None:
            return True, 1
        last_day, streak = date.fromisoformat(saved[0]), saved[1]
        if last_day == today:
            return False, streak
        return True, next_daily_streak(last_day, today, streak)

    async def claim_daily(self, guild_id: int, user_id: int) -> int | None:
        """Apunta el giro del día; devuelve la racha, o `None` si ya se había cobrado."""
        ready, streak = await self.daily_status(guild_id, user_id)
        if not ready or self.repository is None:
            return None
        today = datetime.now(TIMEZONE).date().isoformat()
        if not await self.repository.claim_daily(guild_id, user_id, today=today, streak=streak):
            return None
        return streak

    def turbo_default(self, guild_id: int, user_id: int) -> bool:
        """Si el miembro dejó el turbo puesto la última vez."""
        return self._turbo.get((guild_id, user_id), False)

    def set_turbo_default(self, guild_id: int, user_id: int, turbo: bool) -> None:
        """Recuerda el turbo para la próxima máquina del miembro."""
        self._turbo[(guild_id, user_id)] = turbo

    async def pot(self, guild_id: int) -> int:
        """Bote común del servidor."""
        return await self.economy.slots_pot(guild_id, seed=POT_SEED)

    async def spins_since(self, guild_id: int) -> int:
        """Tiradas pagadas del servidor desde el último bote."""
        return await self.economy.slots_spins_since(guild_id)

    async def last_jackpot_text(self, guild_id: int) -> str | None:
        """`Nombre, 85.000 Y$` del último jackpot del servidor, si lo hay."""
        record = await self.economy.last_jackpot(guild_id)
        if record is None:
            return None
        guild = self.bot.get_guild(guild_id) if hasattr(self.bot, "get_guild") else None
        member = guild.get_member(record.user_id) if isinstance(guild, discord.Guild) else None
        name = member.display_name if member is not None else "alguien"
        return f"{name}, {format_amount(record.amount)}"

    # -- Tirada ---------------------------------------------------------------------

    async def play(
        self,
        guild_id: int,
        user_id: int,
        *,
        stake: int,
        free: bool,
        turbo: bool,
        render: bool = True,
        session_spins: int = 1,
        daily_streak: int = 0,
        spin: Spin | None = None,
        ready: ReadyFn | None = None,
        machine: int = 0,
    ) -> SlotsPlay:
        """Juega una tirada: decide los rodillos y mueve el dinero de forma atómica.

        Los rodillos se deciden antes de cobrar, pero solo se enseñan si el
        cobro sale bien. El calor solo cambia si la tirada se ha pagado.
        Cada máquina tiene su calor y su barra de bonus, y juega una tirada a la
        vez (`_lock`); el dibujo va fuera del candado.

        Args:
            stake: Apuesta; en un giro gratis, la que lo activó (no se cobra).
            render: `False` para no dibujar nada (Auto y giros al cerrar).
            daily_streak: Si es el giro del día, la racha (no se cobra, pero
                los 🎟️ cuentan); 0 en el resto.
            spin: Los rodillos ya sorteados por adelantado; si no, se sortean aquí.
            ready: Lo pintado por adelantado de la máquina (`SlotMachineView._ready`).
            machine: 0 la de Discord; las demás, las de la plaza del Jovaniverso.

        Raises:
            InsufficientFundsError: Si el saldo no cubre la apuesta.
            BalanceLimitError: Si el premio superaría el saldo máximo.
        """
        key = (guild_id, user_id, machine)
        daily = daily_streak > 0
        charged = not free and not daily
        async with self._lock(key):
            await self.load_heat(*key)
            self.cool_down(*key)
            if spin is None:
                spin = self.machine.spin()
            hot = self.heat(*key) >= HEAT_MAX
            payout = line_payout(spin, stake, hot=hot)
            settlement = await self.economy.play_slots(
                guild_id,
                user_id,
                game=GAME,
                stake=stake if charged else 0,
                payout=payout,
                share=pot_share(stake) if charged else 0,
                jackpot=spin.is_jackpot,
                seed=POT_SEED,
                cap=POT_CAP,
            )
            heat = next_heat(self.heat(*key), paid=payout > 0 or spin.is_jackpot, was_hot=hot)
            self._heat[key] = heat
            bonus_stake = bonus_fill_spins = 0
            if charged:
                meter = self.bonus(*key)
                self._bonus[key], bonus_stake = add_bonus(meter, spin, stake, self.randbelow)
                bonus_fill_spins = meter.spins + 1 if bonus_stake else 0
            await self._save_heat(*key)
        media = await self._media(
            spin,
            turbo=turbo,
            render=render,
            won=payout + settlement.jackpot,
            stake=stake,
            ready=ready,
        )
        return SlotsPlay(
            spin=spin,
            stake=stake,
            free=free,
            hot=hot,
            payout=payout,
            settlement=settlement,
            media=media,
            heat=heat,
            session_spins=session_spins,
            daily=daily,
            daily_streak=daily_streak,
            bonus_stake=bonus_stake,
            bonus_fill_spins=bonus_fill_spins,
        )

    async def _media(
        self,
        spin: Spin,
        *,
        turbo: bool,
        render: bool,
        won: int,
        stake: int,
        respin: bool = False,
        ready: ReadyFn | None = None,
    ) -> SlotsMedia:
        """Animación e imagen final de una tirada (nada si `render` es falso).

        Si la máquina ya la tenía pintada (o pintándose) por adelantado, se usa esa.
        """
        if not render:
            return SlotsMedia(gif=b"", png=b"", seconds=0.0)
        if ready is not None:
            key = media_key(spin, won=won, stake=stake, turbo=turbo, respin=respin)
            planned = ready(key)
            if planned is not None and (media := await planned) is not None:
                return media
        return await self.renderer.spin(spin, turbo=turbo, won=won, stake=stake, respin=respin)

    async def respin(
        self,
        guild_id: int,
        user_id: int,
        *,
        spin: Spin,
        stake: int,
        price: int,
        turbo: bool,
        session_spins: int,
        chain: int = 1,
        again: Spin | None = None,
        ready: ReadyFn | None = None,
    ) -> SlotsPlay:
        """Re-gira el tercer rodillo de `spin` cobrando `price`.

        Es una apuesta más de la tragaperras (juego en el IRPF, como la
        tirada): paga la línea a la apuesta original y, con 🃏 🃏 🃏, el bote.
        No aporta al bote, no calienta la máquina y no da giros gratis.

        Args:
            again: El re-giro ya sorteado por adelantado; si no, se sortea aquí.
            ready: Lo pintado por adelantado de la máquina (`SlotMachineView._ready`).

        Raises:
            InsufficientFundsError: Si el saldo no cubre el precio.
            BalanceLimitError: Si el premio superaría el saldo máximo.
        """
        if again is None:
            again = self.machine.respin(spin)
        payout = line_payout(again, stake)
        settlement = await self.economy.play_slots(
            guild_id,
            user_id,
            game=GAME,
            stake=price,
            payout=payout,
            share=0,
            jackpot=again.is_jackpot,
            seed=POT_SEED,
            cap=POT_CAP,
        )
        media = await self._media(
            again,
            turbo=turbo,
            render=True,
            won=payout + settlement.jackpot,
            stake=stake,
            respin=True,
            ready=ready,
        )
        return SlotsPlay(
            spin=again,
            stake=stake,
            free=False,
            hot=False,
            payout=payout,
            settlement=settlement,
            media=media,
            heat=self.heat(guild_id, user_id),
            session_spins=session_spins,
            cost=price,
            respin_chain=chain,
        )

    async def track(
        self,
        play: SlotsPlay,
        guild_id: int,
        user: discord.abc.User,
        channel: object,
        *,
        autoplay: bool = False,
        extra: StatDelta | None = None,
    ) -> None:
        """Logros y estadísticas del casino de una tirada, después de enseñarla.

        Lo usan la máquina de Discord y las de la plaza del Jovaniverso.

        Args:
            extra: Lo que suma además la tirada (las de la plaza, `universo_slots_stats`).
                Una tirada de la plaza llega sin dibujo pero se ha visto entera en la
                página: no cuenta como ⚡ Turbo.
        """
        if play.respin:
            delta = slots_respin_stats(
                price=play.paid_stake,
                payout=play.payout,
                jackpot=play.jackpot,
                chain=play.respin_chain,
            )
        else:
            delta = slots_stats(
                play.spin,
                stake=play.stake,
                payout=play.payout,
                jackpot=play.jackpot,
                free=play.free,
                hot=play.hot,
                turbo=not play.media.gif and extra is None,
                session_spins=play.session_spins,
                when=datetime.now(TIMEZONE),
                autoplay=autoplay,
                tier=play.tier,
                mystery=play.mystery,
                drought=play.settlement.drought,
                daily=play.daily,
                daily_streak=play.daily_streak,
            )
        if play.bonus_stake:
            delta.merge(slots_bonus_stats(fill_spins=play.bonus_fill_spins))
        if extra is not None:
            delta.merge(extra)
        delta.merge(
            casino_stats(
                stake=play.paid_stake,
                net=play.net,
                balance_after=play.balance,
                tax_delta=play.settlement.bet.tax_delta,
            )
        )
        await logros.casino_play(
            self.bot,
            guild_id,
            user,
            channel,
            delta,
            net=play.net,
        )
        await apuestas.record(
            self.bot,
            guild_id,
            user,
            game=GAME,
            stake=play.paid_stake,
            net=play.net,
            balance_after=play.balance,
            tax=play.settlement.bet.tax_delta,
        )

    # -- Plaza del Jovaniverso --------------------------------------------------------

    async def plaza(
        self,
        guild_id: int,
        member: discord.Member,
        channel: object,
        *,
        machine: int,
        stake: int,
        auto: bool,
        audience: int,
    ) -> dict[str, Any]:
        """Juega una tirada en una tragaperras de la plaza y dice cómo enseñarla.

        Mismo dinero que `tragas` (`play`, con el calor y la barra de esa máquina) y
        sin dibujo: la página pinta los fotogramas que salen de `spin_states`, los
        mismos de la animación de Discord. Logros, anuncio y estadísticas van
        después de contestar.

        Args:
            channel: Canal desde el que se abrió la Activity (logros y anuncios).
            auto: Si la tirada es del ▶️ Auto (la primera lo arranca).
            audience: Cuántos más hay en la sala del Jovaniverso.

        Raises:
            SeatError: Si no se puede jugar (máquina, ritmo, saldo o banca), con el
                texto para la página.
        """
        now = time.monotonic()
        self.seats.sweep(now)
        user_id = member.id
        turn = self.seats.begin(guild_id, user_id, machine, stake=stake, auto=auto, now=now)
        try:
            play = await self.play(
                guild_id,
                user_id,
                stake=turn.stake,
                free=turn.free,
                turbo=False,
                render=False,
                session_spins=turn.session_spins,
                machine=machine,
            )
        except (InsufficientFundsError, BalanceLimitError) as error:
            ended = self.seats.stop(guild_id, user_id, machine)
            if ended is not None and ended.spins:
                reason = (
                    StopReason.NO_FUNDS
                    if isinstance(error, InsufficientFundsError)
                    else StopReason.BANK_LIMIT
                )
                self._later(self._plaza_auto_end(guild_id, member, channel, ended, reason))
            if isinstance(error, InsufficientFundsError):
                raise SeatError(insufficient_text(error.balance, turn.stake)) from error
            raise SeatError("La banca no puede pagar tanto. Baja la apuesta.") from error
        ended = self.seats.finish(
            guild_id,
            user_id,
            machine,
            turn,
            now=now,
            net=play.net,
            big_prize=play.big_prize,
            triggers_free_spins=play.spin.triggers_free_spins,
            bonus_stake=play.bonus_stake,
        )
        seat = self.seats.seat(guild_id, user_id, machine)
        text = result_text(play)
        if line := tax_note(play.settlement.bet.tax_delta, [play]):
            text += f"\n{line}"
        moment = bet_moment(stake=play.paid_stake, net=play.net, balance_after=play.balance)
        if line := await renta.hint(self.bot, guild_id, user_id, moment):
            text += f"\n{line}"
        extra = universo_slots_stats(
            machine,
            at_once=len(self.seats.active(guild_id, user_id, now)),
            auto=auto,
            big_prize=play.big_prize,
            jackpot=play.jackpot > 0,
            audience=audience,
        )
        self._later(self._plaza_after(play, guild_id, member, channel, extra, auto, ended))
        meter = self.bonus(guild_id, user_id, machine)
        return {
            "machine": machine,
            "states": spin_states(play.spin, won=play.won, stake=play.stake),
            "stops": list(play.spin.stops),
            "stake": play.stake,
            "bet": turn.bet,
            "free": play.free,
            "won": play.won,
            "net": play.net,
            "jackpot": play.jackpot,
            "big_prize": play.big_prize,
            "tier": play.tier,
            "near_miss": play.spin.near_miss,
            "free_won": play.spin.triggers_free_spins or bool(play.bonus_stake),
            "balance": play.balance,
            "balance_text": format_amount(play.balance),
            "pot": play.settlement.pot,
            "pot_text": format_amount(play.settlement.pot),
            "heat": play.heat,
            "heat_max": HEAT_MAX,
            "bonus": meter.points,
            "bonus_fill": bar_fill(meter.points, BONUS_MAX),
            "free_spins": seat.free_spins,
            "text": text,
            "auto": seat.auto is not None,
            "auto_stop": auto_stop_text(ended[0], ended[1].stake) if ended else None,
        }

    async def plaza_meta(self, guild_id: int) -> dict[str, Any]:
        """Lo que necesita la página para montar la fila de máquinas al entrar.

        Las tiras y los nombres de los símbolos de la escena (sin los PNG: la página
        los descarga de sus `assets`), cómo está parada cada máquina y el bote.
        """
        return {
            "machines": PLAZA_MACHINES,
            "max_at_once": PLAZA_MAX_AT_ONCE,
            "min_gap": PLAZA_MIN_GAP,
            "strips": list(REEL_STRIPS),
            "symbols": dict(SYMBOL_FILES),
            "stills": [
                still_state(self.machine.spin(), highlight=False) for _ in range(PLAZA_MACHINES)
            ],
            "pot_text": format_amount(await self.pot(guild_id)),
        }

    def plaza_stop(
        self, guild_id: int, member: discord.Member, channel: object, machine: int
    ) -> None:
        """⏹️ Para a mano el Auto de una máquina de la plaza (si había uno)."""
        ended = self.seats.stop(guild_id, member.id, machine)
        if ended is not None and ended.spins:
            reason = StopReason.MANUAL
            self._later(self._plaza_auto_end(guild_id, member, channel, ended, reason))

    async def _plaza_after(
        self,
        play: SlotsPlay,
        guild_id: int,
        member: discord.Member,
        channel: object,
        extra: StatDelta,
        auto: bool,
        ended: tuple[StopReason, PlazaAuto] | None,
    ) -> None:
        await self.track(play, guild_id, member, channel, autoplay=auto, extra=extra)
        await self.shout(play, member, channel)
        if ended is not None:
            await self._plaza_auto_end(guild_id, member, channel, ended[1], ended[0])

    async def _plaza_auto_end(
        self,
        guild_id: int,
        member: discord.Member,
        channel: object,
        ended: PlazaAuto,
        reason: StopReason,
    ) -> None:
        """Logros de una sesión de ▶️ Auto de la plaza que acaba de terminar."""
        delta = slots_autoplay_stats(spins=ended.spins, net=ended.net, reason=reason)
        if reason is StopReason.MANUAL and ended.spins == 1:
            delta.add["universo_slots_regret"] = 1
        await logros.track(self.bot, guild_id, member, channel, delta)

    def _later(self, job: Awaitable[None]) -> None:
        """Lanza algo que no hace falta para contestar a la página, sin perderlo de vista."""
        task = asyncio.ensure_future(job)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def shout(self, play: SlotsPlay, user: discord.abc.User, channel: object) -> None:
        """Anuncia en el canal el bote y los premios enormes, para que se vea."""
        if not isinstance(channel, discord.abc.Messageable):
            return
        if play.mystery:
            drought = getattr(play.settlement, "drought", 0)
            text = (
                f"📣 💰 ¡Ha caído el bote misterioso! {user.mention} se lleva "
                f"**{format_amount(play.jackpot)}**"
                + (f" tras {drought:,} tiradas sin bote".replace(",", ".") if drought else "")
                + f". {TAX_COLLECTOR} ya se está relamiendo."
            )
        elif play.jackpot:
            text = (
                f"📣 🃏🃏🃏 ¡{user.mention} ha sacado el **JOVANAZO**! Se lleva "
                f"**{format_amount(play.jackpot)}** del bote. {TAX_COLLECTOR} ya se está "
                "relamiendo."
            )
        elif play.stake and play.net >= SHOUT_MULTIPLIER * play.stake:
            line = " ".join(SYMBOLS[s].emoji for s in play.spin.line)
            text = f"📣 {line} ¡{user.mention} acaba de ganar **{format_amount(play.net)}**!"
        else:
            return
        try:
            await channel.send(
                text, allowed_mentions=discord.AllowedMentions(users=[user], everyone=False)
            )
        except discord.HTTPException:
            logger.debug("No se pudo anunciar un premio de la tragaperras", exc_info=True)

    # -- slots ----------------------------------------------------------------------

    async def _slots_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> None:
        """Lógica compartida entre `/tragas` y `.tragas`: abre la máquina."""
        if guild is None:
            await send_error("La tragaperras solo se juega dentro de un servidor.")
            return
        if error := casino_channel_error(self.casino_channel_ids, channel, "La tragaperras"):
            await send_error(error)
            return
        balance = await self.economy.balance(guild.id, user.id)
        try:
            stake = parse_stake(amount_text, balance)
        except ValueError as error:
            await send_error(str(error))
            return
        if stake > balance:
            await send_error(insufficient_text(balance))
            return

        view = SlotMachineView(self, guild_id=guild.id, owner=user, stake=stake)
        await self.load_heat(guild.id, user.id)
        view.cooled = self.cool_down(guild.id, user.id)
        ready, streak = await self.daily_status(guild.id, user.id)
        view.daily_streak = streak if ready else 0
        view._set_enabled(True)
        opening = self.machine.spin()
        view.last_stops = opening.stops
        png = await self.renderer.still(opening, highlight=False)
        view.message = await send(
            embed=await view.current_embed(balance=balance),
            file=discord.File(io.BytesIO(png), filename=PNG_NAME),
            view=view,
        )
        self.machines.add(view)
        view.prepare()
        if view.cooled:
            await logros.track(
                self.bot, guild.id, user, channel, slots_cooled_stats(lost=view.cooled)
            )

    async def _open_machine(
        self,
        machine: str | None,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> None:
        """Abre la clásica o una máquina de Botes (`bot.cogs.hold_win.THEMES`)."""
        if machine is not None and machine != CLASSIC:
            if await botes.open_theme(
                self.bot,
                machine,
                guild=guild,
                channel=channel,
                user=user,
                amount_text=amount_text,
                send=send,
                send_error=send_error,
            ):
                return
            await send_error("Esa máquina está en el taller. Prueba con la clásica.")
            return
        await self._slots_impl(
            guild=guild,
            channel=channel,
            user=user,
            amount_text=amount_text,
            send=send,
            send_error=send_error,
        )

    @app_commands.command(
        name="tragas",
        description="Tragaperras: la clásica con bote común, el Volcán, la Mina o el Olimpo.",
    )
    @app_commands.describe(
        cantidad="Apuesta por tirada: 500, 2k, all… (por defecto 100)",
        maquina="Qué máquina (por defecto, la clásica)",
    )
    @app_commands.choices(maquina=MACHINE_CHOICES)
    @app_commands.guild_only()
    async def slots(
        self,
        interaction: discord.Interaction,
        cantidad: str | None = None,
        maquina: app_commands.Choice[str] | None = None,
    ) -> None:
        """Abre una tragaperras con botones: la clásica o una de Botes.

        Solo en los canales del casino que elige `garito` (sin elegir, los de `CASINO_CHANNEL_IDS`)
        y en sus hilos. Las tiradas mueven yapdollars a través de la economía del bot.
        """

        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            return await interaction.original_response()

        await self._open_machine(
            maquina.value if maquina else None,
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            send=send,
            send_error=InteractionResponder(interaction).send_error,
        )

    @commands.command(name="tragas")
    @commands.guild_only()
    async def slots_text(self, ctx: commands.Context, *args: str) -> None:
        """Versión de texto: `.tragas`, `.tragas 500`, `.tragas mina` o `.tragas olimpo 500`.

        La máquina y la cantidad van en cualquier orden.
        """
        machine, amount = parse_machine_args(args)

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        await self._open_machine(
            machine,
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            amount_text=amount,
            send=send,
            send_error=ContextResponder(ctx).send_error,
        )


async def plaza(
    bot: commands.Bot,
    guild_id: int,
    member: discord.Member,
    channel: object,
    *,
    machine: int,
    stake: int,
    auto: bool,
    audience: int,
) -> dict[str, Any] | None:
    """Una tirada en una tragaperras de la plaza del Jovaniverso (`Slots.plaza`).

    Returns:
        Lo que necesita la página para enseñarla, o `None` si el cog no está cargado.

    Raises:
        SeatError: Si no se puede jugar, con el texto para la página.
    """
    cog = find_cog(bot, Slots)
    if cog is None:
        return None
    return await cog.plaza(
        guild_id, member, channel, machine=machine, stake=stake, auto=auto, audience=audience
    )


async def plaza_meta(bot: commands.Bot, guild_id: int) -> dict[str, Any] | None:
    """La fila de máquinas de la plaza para la página (`Slots.plaza_meta`), o `None`."""
    cog = find_cog(bot, Slots)
    return None if cog is None else await cog.plaza_meta(guild_id)


def plaza_stop(
    bot: commands.Bot, guild_id: int, member: discord.Member, channel: object, machine: int
) -> None:
    """⏹️ Para el Auto de una tragaperras de la plaza; no hace nada sin el cog."""
    if (cog := find_cog(bot, Slots)) is not None:
        cog.plaza_stop(guild_id, member, channel, machine)


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía y el repositorio compartidos del bot."""
    await bot.add_cog(
        Slots(
            bot,
            economy=bot.economy,
            casino_channel_ids=bot.casino_channels,
            repository=bot.slots_repository,
        )
    )
