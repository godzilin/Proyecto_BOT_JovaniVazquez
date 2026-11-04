"""Pachinko: `pachinko`, una máquina japonesa de bolas con botones.

Cada jugador abre su propia máquina, un mensaje con botones que solo él puede
pulsar. Las reglas (clavos, bolsillos, sorteo, reach y rush) viven en
`bot.services.pachinko`; el dibujo, en `bot.services.pachinko_render`; el
dinero, en `EconomyService.settle_bet`. Este cog solo une las piezas y pinta.

Botones:

- 🎯 **Lanzar**: cobra la tanda, lanza las 10 bolas y paga lo que devuelvan.
- 🔁 **Ráfaga ×5**: cinco tandas seguidas con un solo resumen. Sin turbo, las 50
  bolas caen juntas en una sola animación, en un mismo tablero; en turbo, solo
  la imagen final.
- ▶️ **Auto**: tandas normales encadenadas, cada una con su animación y su imagen
  final, como si el dueño pulsara 🎯 Lanzar una y otra vez. Mientras corre, el
  botón es ⏹️ **Parar** y es lo único que se puede pulsar (Parar solo marca un
  flag en memoria y contesta al instante; la tanda en curso termina entera). Para
  sola si no llega el saldo, si sale un atari (cualquier tanda con premio gordo,
  como en la Ráfaga), si las pérdidas netas de la sesión llegan a 10 veces la
  apuesta o al llegar a 25 tandas. Con 🎲 Al azar, cada tanda cae en un tablero
  distinto. El bucle y sus reglas están en `bot.services.autoplay`; el cog solo
  pone la tanda (`_autoplay_step`), el cierre (`_autoplay_finish`) y los logros.
- ⚡ **Turbo**: sin animación, solo la imagen final (más rápido y casi sin datos).
- **½**, **×2**, 💰 **All-in**: cambian la apuesta. 📋 **Premios**: la tabla.
- Menú de **tablero**: 🌸 Sakura, 🏮 Clásica, 🐉 Dragón, 👹 Oni o 🎲 Al azar
  (cada tanda en uno distinto). Todos devuelven lo mismo de media; cambia el
  riesgo. También se elige al abrir: `.pachinko 500 oni`.

Las bolas chocan entre sí y aun así cada una acaba donde dijo el sorteo
(`bot.services.pachinko_motion`, ~0,1 s de CPU por tanda, también en turbo y en
la Ráfaga porque los logros cuentan los choques). La animación es un GIF que se
monta en cada tanda (~0,8 s de CPU fuera del event loop y 130-370 KB). Al acabar
se cambia por el PNG final, como en la tragaperras. El turbo y el tablero se
recuerdan por miembro en memoria hasta reiniciar.

**Precarga de la tanda siguiente.** Cobrar, sortear (`machine.launch`), calcular el
movimiento (`motion_for`) y dibujar el GIF eran todo trabajo después del clic.
Ahora, mientras el jugador mira el resultado (y durante la animación de cada
tanda del ▶️ Auto), la máquina prepara en segundo plano la siguiente
(`Pachinko.prepare`, en un hilo, en una tarea con nombre
`pachinko-preload-<servidor>-<miembro>`): el tablero (con 🎲 se sortea ya), el
`Volley`, el movimiento y la imagen (GIF o solo PNG, según el turbo de ese
momento). Solo hay una por máquina. Al pulsar 🎯 Lanzar o en el siguiente paso
del Auto, si la precarga sigue valiendo (mismo tablero elegido, o 🎲, y mismo
turbo) se cobra con `settle_bet` con su `volley` y se enseña sin dibujar; si aún
no ha acabado, se espera a ella en vez de sortear otra; si ya no vale (o
falló, y entonces se registra), se tira y se juega como antes. La Ráfaga
dibuja aparte (sus tandas juntas) y no usa ni gasta la precarga. Cambiar la
apuesta no la invalida: ni el sorteo ni ninguna imagen dependen de ella.
Cambiar de tablero o de turbo la cancela; cerrar la máquina (`on_timeout`) o
descargar el cog también.

**El dinero no se mueve hasta el clic**, y por eso es justo: sortear antes o en
el clic da la misma probabilidad (el sorteo no mira la apuesta ni al jugador),
nadie ve el resultado antes de pagar y, si el jugador cierra la máquina, cambia
de tablero o el bot se reinicia, la tanda se tira sin más (no hay cobro, ni
logro, ni rastro en la economía). Detalle en `Pachinko.prepare`.

Lo que gana (script de usar y tirar con la economía en SQLite, el dibujo real,
tablero Clásica y dobles de Discord; mediana de 12 clics desde `ack` hasta la
primera edición del mensaje, sin contar la espera de la animación): con el GIF,
~290 ms sin precarga y ~2,5 ms con ella; en turbo, ~125 ms y ~2,6 ms. Con 🎲 Al
azar sale parecido (~350 ms y ~2,3 ms con GIF). Si el clic llega justo después
del anterior y la precarga aún no está lista, se espera lo que le falte: en el
peor caso, lo mismo que sin ella. El coste en CPU es el mismo; solo cambia
cuándo se paga. Los números son de un portátil y varían con el equipo.

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
from datetime import datetime
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs.casino import casino_channel_error, insufficient_text
from bot.services.achievements import (
    StatDelta,
    casino_stats,
    pachinko_autoplay_stats,
    pachinko_stats,
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
    BetSettlement,
    EconomyService,
    InsufficientFundsError,
    format_amount,
    gambling_tax_line,
    parse_amount,
)
from bot.services.levels import TIMEZONE
from bot.services.pachinko import (
    BALLS,
    BOARDS,
    DEFAULT_BOARD,
    MIN_STAKE,
    Board,
    Kind,
    PachinkoMachine,
    Volley,
    atari_value,
    atari_volley_chance,
    decimal,
    expected_return,
    find_board,
    hold_bar,
    merge_volleys,
    payout,
    paytable_lines,
)
from bot.services.pachinko_motion import VolleyMotion, motion_for
from bot.services.pachinko_render import PachinkoMedia, PachinkoRenderer
from bot.services.pets import bet_moment
from bot.services.taxes import TAX_COLLECTOR
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

GAME = "pachinko"
#: Apuesta por defecto al abrir la máquina sin indicar cantidad.
DEFAULT_STAKE = 100
#: Segundos sin pulsar nada tras los que la máquina se cierra.
MACHINE_TIMEOUT = 180
#: Margen tras la animación: el cliente tarda un poco en empezar el GIF.
REVEAL_MARGIN_SECONDS = 0.4
#: Tandas de la Ráfaga.
BURST_VOLLEYS = 5
#: A partir de cuántas veces la apuesta se anuncia el premio en el canal.
SHOUT_MULTIPLIER = 20
#: Premios gordos encadenados que se anuncian en el canal aunque paguen poco.
SHOUT_RUSH = 5
#: Cómo llama el pachinko a las rondas de ▶️ Auto en los textos de `bot.services.autoplay`.
AUTOPLAY_UNIT = "tandas"

#: Valor del menú para jugar cada tanda en un tablero al azar.
RANDOM_BOARD = "azar"
RANDOM_WORDS = frozenset({"azar", "random", "aleatorio", "sorpresa"})

GIF_NAME = "pachinko.gif"
PNG_NAME = "pachinko.png"

COLOR_IDLE = discord.Color.from_rgb(34, 16, 64)
COLOR_SPIN = discord.Color.from_rgb(255, 60, 200)
COLOR_WIN = discord.Color.from_rgb(255, 200, 60)
COLOR_LOSS = discord.Color.from_rgb(80, 84, 92)

# Textos en el tono de Jovani Vázquez: alegre, exagerado y cariñoso.
SUPER_LINES = (
    "7️⃣7️⃣7️⃣ ¡SUPER RUSH!",
    "7️⃣7️⃣7️⃣ ¡SE ROMPIÓ LA MÁQUINA!",
    "7️⃣7️⃣7️⃣ ¡JOVANAZO JAPONÉS!",
    "7️⃣7️⃣7️⃣ ¡NI FABRA CON LA LOTERÍA!",
    "7️⃣7️⃣7️⃣ ¡TOMA MANGÓ!",
)
RUSH_LINES = (
    "🔥 ¡RUSH! ¡Esto no para!",
    "🔥 ¡RUSH!",
    "🔥 ¡Encadenando!",
    "🔥 ¡RUSH! Más rápido que un decreto ley",
    "🔥 ¡RUSH! ¡Jota Uve! ¡Pre!",
)
ATARI_LINES = (
    "🎉 ¡ATARI!",
    "🎉 ¡Se abrió la compuerta!",
    "🎉 ¡ATARI, toma mangó!",
    "🎉 ¡ATARI! Lluvia de bolas",
    "🎉 ¡ATARI! ¡Azul, azul, como el mar azul!",
)
WIN_LINES = (
    "¡Eso es!",
    "¡Cobras!",
    "¡Lluvia de bolas!",
    "¡Bolas para dar y regalar!",
    "¡Toma mangó!",
    "¡Crecimiento a la japonesa!",
    "¡Bandeja llena!",
    "¡Las bolas te quieren!",
)
SMALL_WIN_LINES = (
    "🎯 ¡Premio!",
    "🎯 ¡Algo cae!",
    "🎯 ¡Tilín, tilín!",
    "🎯 ¡Para pipas!",
    "🎯 ¡Pa'l café!",
)
REACH_LINES = (
    "👀 ¡REACH!… y nada. Por un pelo.",
    "👀 El del centro se pasó.",
    "👀 Casi, casi… la próxima es la buena.",
    "👀 ¡REACH!… y se cae, como una investidura fallida.",
    "👀 Tan cerca que casi lo tocas.",
    "👀 ¡REACH! La bola duda más que tú.",
)
LOSS_LINES = (
    "Las bolas se fueron por el desagüe.",
    "Ni una. Otra tanda.",
    "Calentando la compuerta…",
    "Las bolas se fueron a Andorra.",
    "La máquina se ha puesto en modo ventanilla.",
    "Estoy destruido, pero por fuera… por dentro estoy nuevo.",
    "Ni una bola dentro.",
    "Dolor de bolas.",
    "Lagartijo, iguana, lagarto… y bolas al desagüe.",
)


# -- Presentación -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PachinkoPlay:
    """Una tanda ya cobrada y pagada, lista para enseñarse.

    Attributes:
        won: Lo que ha devuelto la tanda (apuesta incluida).
        motion: Cómo se mueven las bolas (con sus choques); lo usan el dibujo
            y los logros, también en turbo y en la Ráfaga.
        session_volleys: Tandas en esta máquina, contando esta.
    """

    volley: Volley
    stake: int
    won: int
    settlement: BetSettlement
    media: PachinkoMedia
    session_volleys: int
    motion: VolleyMotion

    @property
    def net(self) -> int:
        """Ganancia (positiva) o pérdida (negativa) de la tanda."""
        return self.won - self.stake

    @property
    def balance(self) -> int:
        """Saldo tras la tanda, con el IRPF ya ajustado."""
        return self.settlement.balance


@dataclass(frozen=True, slots=True)
class PreparedVolley:
    """Una tanda sorteada y dibujada de antemano, todavía sin cobrar.

    No lleva apuesta: ni el sorteo, ni el movimiento ni ninguna imagen
    (`bot.services.pachinko_render` solo dibuja bolas, nunca la cifra apostada)
    dependen de ella, así que sirve para cualquier apuesta. Lo único que decide
    si sigue valiendo es lo que se ve: el tablero elegido y el turbo.

    Attributes:
        board_key: Lo que había elegido el jugador al prepararla (la clave de
            un tablero o `RANDOM_BOARD`); con 🎲 el tablero ya está sorteado en
            `volley.board`.
        turbo: Si `media` se dibujó sin animación.
    """

    board_key: str
    turbo: bool
    volley: Volley
    motion: VolleyMotion
    media: PachinkoMedia


@dataclass(slots=True)
class _Preload:
    """La precarga en marcha de una máquina: qué se pidió y la tarea que la prepara."""

    board_key: str
    turbo: bool
    task: asyncio.Task[PreparedVolley]


def _log_preload_failure(task: asyncio.Task[PreparedVolley]) -> None:
    """Registra una precarga que falló (y la da por leída); jugar sigue sin ella."""
    if not task.cancelled() and (error := task.exception()) is not None:
        logger.warning("Falló la precarga del pachinko: se jugará sin ella", exc_info=error)


def _digits(volley: Volley) -> str | None:
    best = volley.best
    if best is None:
        return None
    return " ".join(str(d) for d in best.digits)


def result_text(
    play: PachinkoPlay, rng: random.Random | None = None, *, random_board: bool = False
) -> str:
    """Bloque grande con lo que ha pasado en la tanda.

    Como en la tragaperras, una tanda que devuelve algo se celebra aunque
    devuelva menos de lo apostado: lo cobrado es real y el saldo lo dice todo.

    Args:
        random_board: Si el jugador eligió 🎲 Al azar: se dice qué tablero salió.
    """
    rng = rng or random.Random()
    volley = play.volley
    best = volley.best
    lines: list[str] = []
    if best is not None:
        if best.kind == Kind.SUPER:
            lines.append(f"# {rng.choice(SUPER_LINES)}")
        elif best.kind == Kind.RUSH:
            lines.append(f"# {rng.choice(RUSH_LINES)}")
        else:
            lines.append(f"# {rng.choice(ATARI_LINES)}")
        lines.append(f"## {_digits(volley)} · +{format_amount(play.net)}")
        if volley.jackpots > 1:
            lines.append(f"**×{volley.jackpots}** premios gordos encadenados.")
    elif play.net > 0:
        lines.append(f"# {rng.choice(WIN_LINES)} +{format_amount(play.net)}")
    elif play.won > 0:
        lines.append(f"## {rng.choice(SMALL_WIN_LINES)} Cobras {format_amount(play.won)}")
    elif any(d.reach for d in volley.draws):
        lines.append(f"## {rng.choice(REACH_LINES)}")
        lines.append(f"-{format_amount(play.stake)}")
    else:
        lines.append(f"## -{format_amount(play.stake)} · {rng.choice(LOSS_LINES)}")

    if best is None and any(d.reach for d in volley.draws) and play.won > 0:
        lines.append(rng.choice(REACH_LINES))
    detail = f"🎯 {volley.total_balls} bolas de vuelta · 🌀 {volley.starts} en START"
    if volley.draws:
        detail += f" · {len(volley.draws)} sorteo{'s' if len(volley.draws) > 1 else ''}"
    lines.append(detail)
    if volley.wasted:
        lines.append(
            f"Reserva llena: {volley.wasted} bola{'s' if volley.wasted > 1 else ''} al limbo."
        )
    if volley.corners:
        lines.append("⭐ ¡Bola en la esquina!")
    if random_board:
        lines.append(f"🎲 Ha tocado {volley.board.title} ({volley.board.risk}).")
    return "\n".join(lines)


def burst_text(plays: list[PachinkoPlay], stopped: str | None = None) -> str:
    """Resumen de una Ráfaga: cuántas tandas, neto, ataris y la mejor."""
    net = sum(p.net for p in plays)
    sign = "+" if net > 0 else "-" if net < 0 else "±"
    jackpots = sum(p.volley.jackpots for p in plays)
    lines = [
        f"# 🔁 {len(plays)} tandas · {sign}{format_amount(abs(net))}",
        f"Apostado {format_amount(sum(p.stake for p in plays))} · "
        f"{sum(p.volley.total_balls for p in plays)} bolas de vuelta",
    ]
    if jackpots:
        best = max(plays, key=lambda p: p.volley.jackpots)
        lines.append(f"## 🎉 {_digits(best.volley)} · ×{jackpots} premios gordos")
    if stopped:
        lines.append(f"-# {stopped}")
    return "\n".join(lines)


def tax_note(plays: list[PachinkoPlay]) -> str | None:
    """Línea de IRPF de una tanda o de una Ráfaga (suma de ajustes)."""
    if not plays:
        return None
    if len(plays) == 1:
        return gambling_tax_line(plays[0].settlement)
    delta = sum(p.settlement.tax_delta for p in plays)
    if delta > 0:
        return (
            f"-# 🐶 {TAX_COLLECTOR} se lleva {format_amount(delta)} de IRPF. "
            f"Hoy vas {format_amount(plays[-1].settlement.day_net)} arriba."
        )
    if delta < 0:
        return (
            f"-# 🐶 {TAX_COLLECTOR} te devuelve {format_amount(-delta)}: "
            "tus pérdidas de hoy compensan lo que habías ganado."
        )
    return None


def board_label(board: Board | None) -> str:
    """Nombre del tablero elegido para títulos y menús; `None` es 🎲 Al azar."""
    return "🎲 Al azar" if board is None else board.title


def machine_embed(
    *,
    owner: str,
    balance: int,
    stake: int,
    turbo: bool,
    board: Board | None,
    text: str | None = None,
    won: bool | None = None,
) -> discord.Embed:
    """Embed de la máquina parada: al abrirla o tras una tanda.

    Args:
        board: Tablero elegido; `None` si cada tanda sale en uno al azar.
    """
    if text is None:
        if board is None:
            intro = "Cada tanda cae en un tablero distinto: puede tocar Sakura o puede tocar Oni."
        else:
            intro = f"**{board.title}** · {board.risk}. {board.blurb}"
        description = (
            f"{intro}\n\nPulsa 🎯 **Lanzar**: {BALLS} bolas bajan por los clavos. "
            "Las que entran en **START** juegan en la pantalla: tres iguales es "
            "**ATARI** y los impares traen **RUSH**. Cambia de tablero en el menú."
        )
    else:
        description = text
    if balance < MIN_STAKE:
        description += "\n\n**No te llega para una tanda.** `imv` te recarga."
    color = COLOR_IDLE if won is None else COLOR_WIN if won else COLOR_LOSS
    embed = discord.Embed(
        title=f"🌸 Pachinko · {board_label(board)}", description=description, color=color
    )
    embed.add_field(name="Saldo", value=format_amount(balance))
    embed.add_field(name="Apuesta", value=f"{format_amount(stake)} ({BALLS} bolas)")
    embed.add_field(name="Riesgo", value="sorpresa" if board is None else board.risk)
    embed.set_image(url=f"attachment://{PNG_NAME}")
    footer = f"Máquina de {owner}"
    if turbo:
        footer += " · ⚡ Turbo"
    embed.set_footer(text=footer)
    return embed


def launching_embed(*, owner: str, stake: int, held: int, board: Board) -> discord.Embed:
    """Embed mientras caen las bolas."""
    embed = discord.Embed(
        title=f"🌸 Pachinko · {board.title}",
        description=f"# 🎯 ¡Bolas fuera!\n{format_amount(stake)} · reserva {hold_bar(held)}",
        color=COLOR_SPIN,
    )
    embed.set_image(url=f"attachment://{GIF_NAME}")
    embed.set_footer(text=f"Máquina de {owner}")
    return embed


def boards_summary() -> list[str]:
    """Una línea por tablero: riesgo, cada cuánto hay atari y cuánto paga."""
    lines = []
    for board in BOARDS.values():
        every = round(1 / float(atari_volley_chance(board)))
        value = float(atari_value(board))
        back = float(expected_return(board)) * 100
        lines.append(
            f"**{board.title}** · {board.risk} · atari cada ~{every} tandas, "
            f"×{decimal(value)} de media · devuelve {decimal(back)} %"
        )
    return lines


def paytable_embed(board: Board | None) -> discord.Embed:
    """Tabla de premios del tablero elegido y resumen de todos (se manda en privado)."""
    if board is None:
        description = "🎲 Cada tanda cae en uno de estos tableros:\n\n"
    else:
        description = "\n".join(paytable_lines(board)) + "\n\n**Todos los tableros**\n"
    description += "\n".join(boards_summary())
    return discord.Embed(title="📋 Premios del pachinko", description=description, color=COLOR_WIN)


def parse_args(first: str | None, second: str | None) -> tuple[str | None, str | None]:
    """Separa la cantidad y el tablero de `.pachinko`, en cualquier orden.

    Returns:
        `(texto de la cantidad, clave del tablero o RANDOM_BOARD)`; cada uno
        puede ser `None` si no se ha escrito.

    Raises:
        ValueError: Si hay dos tableros o dos cantidades.
    """
    amount: str | None = None
    board: str | None = None
    for word in (first, second):
        if not word:
            continue
        if word.lower() in RANDOM_WORDS:
            found = RANDOM_BOARD
        else:
            match = find_board(word)
            found = match.key if match else None
        if found is not None:
            if board is not None:
                raise ValueError("Elige un solo tablero: sakura, clasica, dragon, oni o azar.")
            board = found
        else:
            if amount is not None:
                raise ValueError(
                    "No entiendo eso. Ejemplos: `.pachinko 500`, `.pachinko 500 oni`, "
                    "`.pachinko sakura`."
                )
            amount = word
    return amount, board


def parse_stake(amount_text: str | None, balance: int) -> int:
    """Apuesta del comando: la indicada (`500`, `2k`, `all`) o la de por defecto.

    Raises:
        ValueError: Con un mensaje mostrable si no se entiende o no llega al
            mínimo (una bola tiene que valer al menos 1 Y$).
    """
    if not amount_text:
        return max(MIN_STAKE, min(DEFAULT_STAKE, balance))
    stake = parse_amount(amount_text, balance)
    if stake < MIN_STAKE:
        raise ValueError(f"La tanda mínima es {format_amount(MIN_STAKE)}: una bola por yapdollar.")
    return stake


EditFn = Callable[..., Awaitable[Any]]


# -- Máquina ------------------------------------------------------------------------


class PachinkoView(discord.ui.View):
    """Máquina de un jugador: un mensaje con botones.

    Guarda la apuesta, el modo turbo y las tandas de la sesión. No guarda
    dinero: el saldo se lee y se cambia siempre a través de la economía.

    `_busy` vale mientras hay una acción en curso (una tanda, una Ráfaga o todo
    un ▶️ Auto): dos botones a la vez no pueden cobrar dos veces. `autoplay` es la
    sesión de ▶️ Auto en marcha, si la hay. `_preload` es la tanda de después,
    que se prepara mientras el jugador mira la anterior (ver el cog).
    """

    def __init__(
        self,
        cog: Pachinko,
        *,
        guild_id: int,
        owner: discord.abc.User,
        stake: int,
        board_key: str | None = None,
    ) -> None:
        super().__init__(timeout=MACHINE_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        self.stake = stake
        self.turbo = cog.turbo_default(guild_id, owner.id)
        #: Clave del tablero elegido o `RANDOM_BOARD`.
        self.board_key = board_key or cog.board_default(guild_id, owner.id)
        self.session_volleys = 0
        self.last_text: str | None = None
        self.last_won: bool | None = None
        self.message: discord.Message | None = None
        self._last_interaction: discord.Interaction | None = None
        self._busy = False
        self.autoplay: AutoplaySession | None = None
        self._preload: _Preload | None = None
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

    def _build_buttons(self) -> None:
        green, blue = discord.ButtonStyle.success, discord.ButtonStyle.primary
        self.launch_button = self._add("🎯 Lanzar", 0, self._launch, style=green, custom_id="go")
        self._add(f"🔁 Ráfaga ×{BURST_VOLLEYS}", 0, self._burst, style=blue, custom_id="burst")
        self.autoplay_button = self._add(
            "▶️ Auto", 0, self._autoplay_click, style=blue, custom_id="autoplay"
        )
        self.turbo_button = self._add("⚡ Turbo", 0, self._toggle_turbo, custom_id="turbo")
        self._add("½", 1, self._halve, custom_id="half")
        self._add("×2", 1, self._double_stake, custom_id="x2")
        self._add("💰 All-in", 1, self._all_in, custom_id="allin")
        self._add("📋 Premios", 1, self._paytable, custom_id="paytable")
        self.board_select: discord.ui.Select = discord.ui.Select(
            placeholder="Elige tablero", row=2, custom_id=f"{GAME}:board", options=[]
        )
        self.board_select.callback = self._choose_board  # type: ignore[method-assign]
        self.add_item(self.board_select)
        self._set_enabled(True)

    @property
    def board(self) -> Board | None:
        """Tablero elegido; `None` con 🎲 Al azar."""
        return BOARDS.get(self.board_key)

    def _board_options(self) -> list[discord.SelectOption]:
        options = [
            discord.SelectOption(
                label=f"{board.name} · {board.risk}",
                value=board.key,
                emoji=board.emoji,
                description=board.blurb[:100],
                default=board.key == self.board_key,
            )
            for board in BOARDS.values()
        ]
        options.append(
            discord.SelectOption(
                label="Al azar · sorpresa",
                value=RANDOM_BOARD,
                emoji="🎲",
                description="Cada tanda cae en un tablero distinto.",
                default=self.board_key == RANDOM_BOARD,
            )
        )
        return options

    def _set_enabled(self, enabled: bool) -> None:
        """Activa o desactiva los botones y pone al día sus etiquetas."""
        running = self.autoplay is not None
        for item in self.children:
            if isinstance(item, discord.ui.Button | discord.ui.Select):
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
        self.board_select.options = self._board_options()
        self.launch_button.label = f"🎯 Lanzar · {format_amount(self.stake)}"
        self.turbo_button.label = "⚡ Turbo: sí" if self.turbo else "⚡ Turbo"
        self.turbo_button.style = (
            discord.ButtonStyle.success if self.turbo else discord.ButtonStyle.secondary
        )

    # -- Ciclo de vida --------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño juega en su máquina; al resto se le invita a abrir la suya."""
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message(
            f"Esta máquina es de {self.owner.display_name}. Abre la tuya con `pachinko`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Cierra la máquina por inactividad: para el Auto, suelta la precarga y apaga todo."""
        self.cog.machines.discard(self)
        await self.close_autoplay()
        self.close_preload()
        for item in self.children:
            if isinstance(item, discord.ui.Button | discord.ui.Select):
                item.disabled = True
        try:
            if self._last_interaction is not None:
                await self._last_interaction.edit_original_response(view=self)
            elif self.message is not None:
                # Tras un ▶️ Auto largo el token de la interacción ya habrá caducado.
                await self._message_edit(None)(view=self)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar el pachinko", exc_info=True)

    async def close_autoplay(self) -> None:
        """Detiene un ▶️ Auto en marcha: acaba la tanda en curso y cierra la tarea."""
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

    # -- Precarga ---------------------------------------------------------------------

    def _start_preload(self) -> None:
        """Pone a preparar la tanda siguiente, si no hay ya una valiendo."""
        if self._closed or not self.cog.preload_enabled:
            return
        current = self._preload
        if current is not None:
            if (current.board_key, current.turbo) == (self.board_key, self.turbo):
                return
            current.task.cancel()
        task = asyncio.create_task(
            self.cog.prepare(self.board_key, turbo=self.turbo),
            name=f"pachinko-preload-{self.guild_id}-{self.owner.id}",
        )
        task.add_done_callback(_log_preload_failure)
        self._preload = _Preload(self.board_key, self.turbo, task)

    def _drop_stale_preload(self) -> None:
        """Tira la precarga si el tablero o el turbo ya no son los suyos."""
        current = self._preload
        if current is not None and (current.board_key, current.turbo) != (
            self.board_key,
            self.turbo,
        ):
            current.task.cancel()
            self._preload = None

    def close_preload(self) -> None:
        """Cancela la precarga y no deja que se prepare otra (la máquina se cierra)."""
        self._closed = True
        preload, self._preload = self._preload, None
        if preload is not None:
            preload.task.cancel()

    async def _take_preload(self, *, turbo: bool) -> PreparedVolley | None:
        """Entrega la tanda precargada si sigue valiendo; si no, `None` y se tira de nuevo.

        Si todavía se está preparando, espera a que acabe en vez de lanzar otra.
        Una precarga que falló o que ya no vale (otro tablero, otro turbo) se
        descarta: el dinero no se ha movido, así que no se pierde nada.
        """
        preload, self._preload = self._preload, None
        if preload is None:
            return None
        if (preload.board_key, preload.turbo) != (self.board_key, turbo):
            preload.task.cancel()
            return None
        try:
            await asyncio.wait({preload.task})
        except asyncio.CancelledError:
            preload.task.cancel()
            raise
        if preload.task.cancelled() or preload.task.exception() is not None:
            return None
        return preload.task.result()

    # -- Juego ----------------------------------------------------------------------

    async def balance(self) -> int:
        """Saldo actual del dueño de la máquina."""
        return await self.cog.economy.balance(self.guild_id, self.owner.id)

    async def current_embed(
        self, *, balance: int | None = None, text: str | None = None
    ) -> discord.Embed:
        """Embed de reposo con el saldo actual."""
        if balance is None:
            balance = await self.balance()
        return machine_embed(
            owner=self.owner.display_name,
            balance=balance,
            stake=self.stake,
            turbo=self.turbo,
            board=self.board,
            text=text if text is not None else self.last_text,
            won=self.last_won,
        )

    async def _play_one(
        self, *, turbo: bool, render: bool = True, board: Board | None = None
    ) -> PachinkoPlay:
        """Una tanda con la apuesta actual.

        Con animación (`render`) usa la tanda precargada si la hay y vale; la
        Ráfaga, que dibuja aparte, siempre sortea la suya y no gasta la precarga.

        Args:
            board: Tablero obligado (la Ráfaga con animación juega las cinco en
                el mismo); si no, el elegido o uno al azar con 🎲.

        Raises:
            InsufficientFundsError, BalanceLimitError: Como `Pachinko.play`.
        """
        prepared = await self._take_preload(turbo=turbo) if render else None
        if prepared is None:
            board = board or self.board or self.cog.machine.random_board()
        play = await self.cog.play(
            self.guild_id,
            self.owner.id,
            stake=self.stake,
            board=None if prepared else board,
            turbo=turbo,
            render=render,
            session_volleys=self.session_volleys + 1,
            prepared=prepared,
        )
        self.session_volleys += 1
        return play

    async def _launch(self, interaction: discord.Interaction) -> None:
        """Respuesta a 🎯 Lanzar: cobra, lanza, enseña y paga."""
        if self._busy:
            # Doble clic mientras caen las bolas: se ignora sin mostrar error.
            await ack(interaction)
            return
        self._busy = True
        try:
            # Cobrar y dibujar la caída tardan: se acepta el clic antes.
            await ack(interaction)
            try:
                play = await self._play_one(turbo=self.turbo)
            except InsufficientFundsError as error:
                await notify(interaction, insufficient_text(error.balance, self.stake))
                return
            except BalanceLimitError:
                await notify(interaction, "La banca no puede pagar tanto. Baja la apuesta.")
                return
            self._last_interaction = interaction
            await self.show(
                play,
                first_edit=interaction.edit_original_response,
                final_edit=interaction.edit_original_response,
            )
        finally:
            self._busy = False
        await renta.remind(self.cog.bot, interaction)
        await self._track(play)
        await self.cog.shout(play, self.owner, getattr(self.message, "channel", None))

    async def show(
        self,
        play: PachinkoPlay,
        *,
        first_edit: EditFn,
        final_edit: EditFn,
        note: str | None = None,
    ) -> None:
        """Enseña la tanda: el GIF y después el PNG final (o solo el PNG en turbo).

        El dinero ya está cobrado y pagado: si Discord falla al editar, el
        saldo sigue siendo correcto. En cuanto el jugador tiene algo que mirar
        (el GIF, o el PNG en turbo) se pone a preparar la tanda siguiente.

        Args:
            note: Línea pequeña bajo el resultado (el contador de ▶️ Auto). No
                se guarda en `last_text`: es solo de esta tanda.
        """
        if play.media.gif:
            self._set_enabled(False)
            await first_edit(
                embed=launching_embed(
                    owner=self.owner.display_name,
                    stake=play.stake,
                    held=len(play.volley.draws),
                    board=play.volley.board,
                ),
                attachments=[discord.File(io.BytesIO(play.media.gif), filename=GIF_NAME)],
                view=self,
            )
            self._start_preload()
            await asyncio.sleep(play.media.seconds + REVEAL_MARGIN_SECONDS)
            edit = final_edit
        else:
            edit = first_edit

        text = result_text(play, random_board=self.board is None)
        if tax := tax_note([play]):
            text += f"\n{tax}"
        if renta_hint := await renta.hint(
            self.cog.bot,
            self.guild_id,
            self.owner.id,
            bet_moment(stake=play.stake, net=play.net, balance_after=play.balance),
        ):
            text += f"\n{renta_hint}"
        self.last_text = text
        self.last_won = play.won > 0
        self._set_enabled(True)
        await edit(
            embed=await self.current_embed(
                balance=play.balance, text=f"{text}\n{note}" if note else text
            ),
            attachments=[discord.File(io.BytesIO(play.media.png), filename=PNG_NAME)],
            view=self,
        )
        self._start_preload()

    async def _track(self, play: PachinkoPlay, *, autoplay: bool = False) -> None:
        """Logros de la tanda, después de enseñarla (antes destriparía el resultado)."""
        delta = pachinko_stats(
            play.volley,
            motion=play.motion,
            stake=play.stake,
            won=play.won,
            turbo=not play.media.gif,
            session_volleys=play.session_volleys,
            when=datetime.now(TIMEZONE),
            autoplay=autoplay,
        )
        delta.merge(
            casino_stats(
                stake=play.stake,
                net=play.net,
                balance_after=play.balance,
                tax_delta=play.settlement.tax_delta,
            )
        )
        await logros.casino_play(
            self.cog.bot,
            self.guild_id,
            self.owner,
            getattr(self.message, "channel", None),
            delta,
            net=play.net,
        )
        await apuestas.record(
            self.cog.bot,
            self.guild_id,
            self.owner,
            game=GAME,
            stake=play.stake,
            net=play.net,
            balance_after=play.balance,
            tax=play.settlement.tax_delta,
        )

    async def _burst(self, interaction: discord.Interaction) -> None:
        """Cinco tandas seguidas y un solo resumen.

        Sin turbo, las bolas de todas las tandas caen juntas en una sola
        animación (cinco veces más bolas que 🎯 Lanzar) y la pantalla juega las
        tiradas de todas; por eso las cinco van al mismo tablero (con 🎲, uno
        sorteado para toda la Ráfaga). Cada tanda se cobra y cuenta para los
        logros suelta, como siempre: juntarlas solo cambia el dibujo
        (`merge_volleys`). En turbo, solo la imagen final de la última, y con 🎲
        cada tanda en un tablero.

        Para antes si se acaba el dinero o sale un atari (para que se vea).
        """
        if self._busy:
            await ack(interaction)
            return
        self._busy = True
        plays: list[PachinkoPlay] = []
        stopped: str | None = None
        try:
            await ack(interaction)
            self._last_interaction = interaction
            animated = not self.turbo
            board = (self.board or self.cog.machine.random_board()) if animated else None
            for _ in range(BURST_VOLLEYS):
                try:
                    plays.append(await self._play_one(turbo=True, render=False, board=board))
                except InsufficientFundsError:
                    stopped = "Parado: no te llega para otra tanda."
                    break
                except BalanceLimitError:
                    stopped = "Parado: la banca no puede pagar tanto."
                    break
                if plays[-1].volley.jackpots:
                    stopped = "Parado: ¡ATARI!"
                    break
            if not plays:
                await interaction.followup.send(
                    insufficient_text(await self.balance(), self.stake), ephemeral=True
                )
                return
            last = plays[-1]
            if animated:
                together = merge_volleys([p.volley for p in plays])
                # El movimiento de las tandas juntas es otro (chocan entre todas): se
                # calcula al dibujar, en el mismo hilo. ~1,3 s de CPU con 50 bolas.
                media = await asyncio.to_thread(self.cog.renderer.render, together)
                self._set_enabled(False)
                await interaction.edit_original_response(
                    embed=launching_embed(
                        owner=self.owner.display_name,
                        stake=sum(p.stake for p in plays),
                        held=len(together.draws),
                        board=together.board,
                    ),
                    attachments=[discord.File(io.BytesIO(media.gif), filename=GIF_NAME)],
                    view=self,
                )
                self._start_preload()
                await asyncio.sleep(media.seconds + REVEAL_MARGIN_SECONDS)
                png = media.png
            else:
                png = await asyncio.to_thread(self.cog.renderer.still_png, last.volley, last.motion)
            text = burst_text(plays, stopped)
            if note := tax_note(plays):
                text += f"\n{note}"
            if renta_hint := await renta.hint(
                self.cog.bot,
                self.guild_id,
                self.owner.id,
                bet_moment(
                    stake=sum(p.stake for p in plays),
                    net=sum(p.net for p in plays),
                    balance_after=plays[-1].balance,
                ),
            ):
                text += f"\n{renta_hint}"
            self.last_text = text
            self.last_won = sum(p.net for p in plays) > 0
            self._set_enabled(True)
            await interaction.edit_original_response(
                embed=await self.current_embed(balance=last.balance),
                attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
                view=self,
            )
            self._start_preload()
        finally:
            self._busy = False
        await renta.remind(self.cog.bot, interaction)
        channel = getattr(self.message, "channel", None)
        for play in plays:
            await self._track(play)
        await logros.track(
            self.cog.bot, self.guild_id, self.owner, channel, StatDelta(add={"pachinko_burst": 1})
        )
        for play in plays:
            await self.cog.shout(play, self.owner, channel)

    # -- ▶️ Auto ---------------------------------------------------------------------

    def _message_edit(self, interaction: discord.Interaction | None) -> EditFn:
        """Cómo editar el mensaje de la máquina sin el token de la interacción.

        El token caduca a los 15 minutos y un ▶️ Auto largo puede pasarse. El mensaje
        de un slash command es un `InteractionMessage`, cuyo `edit` usa ese token: se
        edita entonces por el canal. Un mensaje normal (`.pachinko`) ya edita con el
        token del bot. Sin mensaje guardado, se cae a la respuesta de la interacción.
        """
        message = self.message
        if message is None:
            if interaction is None:
                raise RuntimeError("La máquina no tiene mensaje ni interacción que editar")
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
            # Doble clic o una tanda en curso: se acepta el clic y se ignora.
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
                name=f"pachinko-autoplay-{self.guild_id}-{self.owner.id}",
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
        """Una tanda de ▶️ Auto: igual que 🎯 Lanzar, pero editando sin token.

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

        net = session.net + play.net
        sign = "+" if net > 0 else "-" if net < 0 else "±"
        note = (
            f"-# ▶️ Auto · tanda {number}/{session.max_spins} · neto {sign}{format_amount(abs(net))}"
        )
        stop: StopReason | None = None
        try:
            await self.show(play, first_edit=first_edit, final_edit=edit_fn, note=note)
        except discord.HTTPException:
            # Mensaje borrado o sin permisos: seguir jugando a ciegas gastaría dinero.
            logger.warning("No se pudo editar el pachinko en pleno Auto", exc_info=True)
            stop = StopReason.CLOSED
        edited_at = time.monotonic()
        await self._track(play, autoplay=True)
        await self.cog.shout(play, self.owner, getattr(self.message, "channel", None))
        if number == 1:
            await renta.remind(self.cog.bot, interaction)
        return SpinResult(
            net=play.net, big_prize=play.volley.jackpots > 0, edited_at=edited_at, stop=stop
        )

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
            # El token de esta interacción puede caducar antes de que la máquina se cierre.
            self._last_interaction = None
            self._set_enabled(True)
            try:
                if outcome.spins == 0:
                    if outcome.reason is StopReason.NO_FUNDS:
                        await notify(
                            interaction, insufficient_text(await self.balance(), self.stake)
                        )
                    elif outcome.reason is not StopReason.CLOSED:
                        await notify(interaction, outcome.reason_text(unit=AUTOPLAY_UNIT))
                    if session.armed:
                        await edit_fn(embed=await self.current_embed(), view=self)
                else:
                    # El resumen va encima de la última tanda para que se siga viendo.
                    text = outcome.summary(unit=AUTOPLAY_UNIT)
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
            pachinko_autoplay_stats(volleys=outcome.spins, net=outcome.net, reason=outcome.reason),
        )

    async def _refresh(self, interaction: discord.Interaction, balance: int | None = None) -> None:
        """Actualiza la máquina (apuesta, turbo) sin tocar la imagen.

        El embed lee el saldo de la base de datos: se acepta el clic antes.
        """
        await ack(interaction)
        self._set_enabled(True)
        await edit(interaction, embed=await self.current_embed(balance=balance), view=self)
        self._last_interaction = interaction

    async def _toggle_turbo(self, interaction: discord.Interaction) -> None:
        self.turbo = not self.turbo
        self.cog.set_turbo_default(self.guild_id, self.owner.id, self.turbo)
        self._drop_stale_preload()
        await self._refresh(interaction)

    async def _halve(self, interaction: discord.Interaction) -> None:
        self.stake = max(MIN_STAKE, self.stake // 2)
        await self._refresh(interaction)

    async def _double_stake(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        balance = await self.balance()
        # Si el doble no cabe, se queda en todo el saldo: es lo que se busca.
        self.stake = max(MIN_STAKE, min(self.stake * 2, balance))
        await self._refresh(interaction, balance)

    async def _all_in(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        balance = await self.balance()
        if balance < MIN_STAKE:
            await notify(interaction, insufficient_text(balance, MIN_STAKE))
            return
        self.stake = balance
        await self._refresh(interaction, balance)

    async def _paytable(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=paytable_embed(self.board), ephemeral=True)

    async def _choose_board(self, interaction: discord.Interaction) -> None:
        """Cambia de tablero y enseña la máquina nueva parada."""
        if self._busy:
            await ack(interaction)
            return
        values = self.board_select.values
        if values and (values[0] in BOARDS or values[0] == RANDOM_BOARD):
            self.board_key = values[0]
        self.cog.set_board_default(self.guild_id, self.owner.id, self.board_key)
        self._drop_stale_preload()
        self.last_text = None
        self.last_won = None
        self._set_enabled(True)
        # Dibujar el tablero y leer el saldo tardan: se acepta el clic antes.
        await ack(interaction)
        board = self.board or self.cog.machine.random_board()
        png = await asyncio.to_thread(self.cog.renderer.idle_png, board)
        await edit(
            interaction,
            embed=await self.current_embed(),
            attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
            view=self,
        )
        self._last_interaction = interaction


# -- Cog ----------------------------------------------------------------------------


class Pachinko(commands.Cog, name="Pachinko"):
    """El pachinko del casino: una máquina propia por jugador."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        renderer: PachinkoRenderer | None = None,
        machine: PachinkoMachine | None = None,
        casino_channel_ids: CasinoChannelIds = frozenset(),
        preload: bool = True,
    ) -> None:
        self.bot = bot
        self.economy = economy
        #: Si las máquinas preparan la tanda siguiente mientras se mira la anterior.
        #: Solo se apaga en pruebas que cuentan los sorteos uno a uno.
        self.preload_enabled = preload
        self.renderer = renderer or PachinkoRenderer()
        self.machine = machine or PachinkoMachine()
        self.casino_channel_ids = casino_channel_ids
        # Turbo por (servidor, miembro). Crece como mucho hasta el número de
        # miembros que han jugado; se pierde al reiniciar.
        self._turbo: dict[tuple[int, int], bool] = {}
        self._board: dict[tuple[int, int], str] = {}
        # Máquinas abiertas: para cerrar su ▶️ Auto y su precarga si el bot se apaga.
        self.machines: set[PachinkoView] = set()
        self._warm_task: asyncio.Task[None] | None = None

    async def cog_load(self) -> None:
        """Prepara las piezas de todos los tableros en segundo plano (~0,3 s de CPU)."""
        self._warm_task = asyncio.create_task(
            asyncio.to_thread(self.renderer.warm_up), name="pachinko-warm-up"
        )

    async def cog_unload(self) -> None:
        """Cancela la preparación si el bot se apaga antes de acabarla y cierra las máquinas.

        Un ▶️ Auto en marcha acaba la tanda en curso (ya cobrada) y para; las
        precargas se tiran sin más, porque no habían movido dinero.
        """
        if self._warm_task is not None:
            self._warm_task.cancel()
        for view in list(self.machines):
            try:
                await view.close_autoplay()
            except Exception:
                logger.exception("No se pudo cerrar el Auto del pachinko al apagar")
            view.close_preload()
            view.stop()
        self.machines.clear()

    def turbo_default(self, guild_id: int, user_id: int) -> bool:
        """Si el miembro dejó el turbo puesto la última vez."""
        return self._turbo.get((guild_id, user_id), False)

    def set_turbo_default(self, guild_id: int, user_id: int, turbo: bool) -> None:
        """Recuerda el turbo para la próxima máquina del miembro."""
        self._turbo[(guild_id, user_id)] = turbo

    def board_default(self, guild_id: int, user_id: int) -> str:
        """Tablero (o `RANDOM_BOARD`) que eligió el miembro la última vez."""
        return self._board.get((guild_id, user_id), DEFAULT_BOARD)

    def set_board_default(self, guild_id: int, user_id: int, board_key: str) -> None:
        """Recuerda el tablero para la próxima máquina del miembro."""
        self._board[(guild_id, user_id)] = board_key

    def _build_prepared(self, board_key: str, board: Board, turbo: bool) -> PreparedVolley:
        """Sorteo, movimiento y dibujo de una tanda (CPU: se llama fuera del event loop)."""
        volley = self.machine.launch(board)
        motion = motion_for(volley)
        media = self.renderer.render(volley, turbo=turbo, motion=motion)
        return PreparedVolley(
            board_key=board_key, turbo=turbo, volley=volley, motion=motion, media=media
        )

    async def prepare(self, board_key: str, *, turbo: bool) -> PreparedVolley:
        """Sortea y dibuja una tanda de antemano, **sin mover dinero**.

        Es lo que la máquina hace mientras el jugador mira el resultado de la
        anterior (y mientras corre la animación de cada tanda del ▶️ Auto), para
        que el siguiente clic solo tenga que cobrar y enseñar. Se pasa a
        `play(prepared=...)` y solo entonces se cobra.

        Es justo porque el azar es el mismo: el sorteo no mira la apuesta ni al
        jugador, así que sortear antes del clic o en el clic da la misma
        probabilidad; nadie ve el resultado antes de pagar (no se enseña nada
        hasta que `play` ha cobrado), y la apuesta no sale en ninguna imagen (el
        GIF y el PNG solo pintan bolas), de modo que cambiarla no invalida la
        tanda. Solo la invalidan los cambios de lo que sí se ve: el tablero
        elegido y el turbo. Si el jugador cierra la máquina, cambia de tablero o
        el bot se reinicia, la tanda se tira sin más: no había movido un yapdollar
        ni dejado rastro en la economía ni en los logros.

        Args:
            board_key: Clave del tablero elegido o `RANDOM_BOARD`; con 🎲 se
                sortea ya cuál toca.
            turbo: Si hay que dibujar solo el PNG (sin GIF).
        """
        board = BOARDS.get(board_key) or self.machine.random_board()
        return await asyncio.to_thread(self._build_prepared, board_key, board, turbo)

    async def play(
        self,
        guild_id: int,
        user_id: int,
        *,
        stake: int,
        board: Board | None = None,
        turbo: bool,
        render: bool = True,
        session_volleys: int = 1,
        prepared: PreparedVolley | None = None,
    ) -> PachinkoPlay:
        """Juega una tanda: decide las bolas y el sorteo y mueve el dinero de una vez.

        Fiscalmente es juego, como la ruleta y la tragaperras: `settle_bet`
        cobra la apuesta, paga el premio y ajusta la retención diaria del
        IRPF, porque la ganancia es una ganancia patrimonial de la base general
        (art. 33.1 LIRPF) y las pérdidas del mismo día compensan (art. 33.5.d
        LIRPF). Lo retenido va a la cuenta del Estado en la misma transacción.

        Args:
            board: Tablero de la tanda; no hace falta si se da `prepared`.
            prepared: Tanda ya sorteada y dibujada por `prepare` (con el mismo
                turbo): se cobra tal cual, sin volver a sortear ni a dibujar.

        Raises:
            ValueError: Si la apuesta no llega a `MIN_STAKE` o no hay tablero ni tanda.
            InsufficientFundsError: Si el saldo no cubre la apuesta.
            BalanceLimitError: Si el premio superaría el saldo máximo.
        """
        if stake < MIN_STAKE:
            raise ValueError(f"La tanda mínima es {MIN_STAKE}.")
        if prepared is not None:
            if render and prepared.turbo != turbo:
                raise ValueError("La tanda precargada se dibujó con otro turbo.")
            volley, motion = prepared.volley, prepared.motion
        elif board is not None:
            volley = self.machine.launch(board)
            # El movimiento (choques incluidos) hace falta para los logros aunque no se
            # dibuje (turbo, Ráfaga) y se calcula antes de cobrar: si fallara, no se ha
            # movido dinero. Es CPU (decenas de ms), así que fuera del event loop.
            motion = await asyncio.to_thread(motion_for, volley)
        else:
            raise ValueError("Falta el tablero de la tanda.")
        won = payout(volley, stake)
        settlement = await self.economy.settle_bet(
            guild_id, user_id, game=GAME, stake=stake, payout=won
        )
        if prepared is not None:
            media = prepared.media if render else PachinkoMedia(gif=b"", png=b"", seconds=0.0)
        elif render:
            media = await asyncio.to_thread(
                self.renderer.render, volley, turbo=turbo, motion=motion
            )
        else:
            media = PachinkoMedia(gif=b"", png=b"", seconds=0.0)
        return PachinkoPlay(
            volley=volley,
            stake=stake,
            won=won,
            settlement=settlement,
            media=media,
            session_volleys=session_volleys,
            motion=motion,
        )

    async def shout(self, play: PachinkoPlay, user: discord.abc.User, channel: object) -> None:
        """Anuncia en el canal los rush largos y los premios enormes, para que se vea."""
        if not isinstance(channel, discord.abc.Messageable):
            return
        volley = play.volley
        best = volley.best
        big = play.stake and play.net >= SHOUT_MULTIPLIER * play.stake
        if best is not None and (best.kind == Kind.SUPER or volley.jackpots >= SHOUT_RUSH or big):
            name = (
                "SUPER RUSH"
                if best.kind == Kind.SUPER
                else "RUSH"
                if volley.jackpots > 1
                else "ATARI"
            )
            text = (
                f"📣 {volley.board.emoji} {_digits(volley)} ¡{user.mention} ha sacado "
                f"**{name} ×{volley.jackpots}** en el pachinko {volley.board.name}! "
                f"Se lleva **{format_amount(play.net)}**. {TAX_COLLECTOR} ya está contando bolas."
            )
        elif big:
            amount = format_amount(play.net)
            text = (
                f"📣 {volley.board.emoji} ¡{user.mention} acaba de ganar **{amount}** en el "
                f"pachinko {volley.board.name}!"
            )
        else:
            return
        try:
            await channel.send(
                text, allowed_mentions=discord.AllowedMentions(users=[user], everyone=False)
            )
        except discord.HTTPException:
            logger.debug("No se pudo anunciar un premio del pachinko", exc_info=True)

    # -- pachinko -------------------------------------------------------------------

    async def _pachinko_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
        board_key: str | None = None,
    ) -> None:
        """Lógica compartida entre `/pachinko` y `.pachinko`: abre la máquina.

        Args:
            board_key: Tablero pedido (o `RANDOM_BOARD`); sin él, el último que
                eligió el miembro.
        """
        if guild is None:
            await send_error("El pachinko solo se juega dentro de un servidor.")
            return
        if error := casino_channel_error(self.casino_channel_ids, channel, "El pachinko"):
            await send_error(error)
            return
        balance = await self.economy.balance(guild.id, user.id)
        try:
            stake = parse_stake(amount_text, balance)
        except ValueError as error:
            await send_error(str(error))
            return
        if stake > balance:
            await send_error(insufficient_text(balance, stake))
            return

        if board_key is not None:
            self.set_board_default(guild.id, user.id, board_key)
        view = PachinkoView(self, guild_id=guild.id, owner=user, stake=stake, board_key=board_key)
        board = view.board or self.machine.random_board()
        png = await asyncio.to_thread(self.renderer.idle_png, board)
        view.message = await send(
            embed=await view.current_embed(balance=balance),
            file=discord.File(io.BytesIO(png), filename=PNG_NAME),
            view=view,
        )
        self.machines.add(view)

    @app_commands.command(name="pachinko", description="Pachinko japonés con reach y rush.")
    @app_commands.describe(
        cantidad="Apuesta por tanda de 10 bolas: 500, 2k, all… (100)",
        mapa="Tablero: de Sakura (riesgo bajo) a Oni (extremo); por defecto, el último",
    )
    @app_commands.choices(
        mapa=[
            *(
                app_commands.Choice(name=f"{b.title} · {b.risk}", value=b.key)
                for b in BOARDS.values()
            ),
            app_commands.Choice(name="🎲 Al azar", value=RANDOM_BOARD),
        ]
    )
    @app_commands.guild_only()
    async def pachinko(
        self,
        interaction: discord.Interaction,
        cantidad: str | None = None,
        mapa: str | None = None,
    ) -> None:
        """Abre una máquina de pachinko con botones.

        Solo en los canales del casino que elige `garito` (sin elegir, los de `CASINO_CHANNEL_IDS`)
        y en sus hilos. Las tandas mueven yapdollars a través de la economía del bot.
        """

        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            return await interaction.original_response()

        await self._pachinko_impl(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            send=send,
            send_error=InteractionResponder(interaction).send_error,
            board_key=mapa,
        )

    @commands.command(name="pachinko")
    @commands.guild_only()
    async def pachinko_text(
        self, ctx: commands.Context, primero: str | None = None, segundo: str | None = None
    ) -> None:
        """Versión de texto: `.pachinko`, `.pachinko 500`, `.pachinko 500 oni`, `.pachinko azar`.

        La cantidad y el tablero van en cualquier orden.
        """

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        responder = ContextResponder(ctx)
        try:
            amount_text, board_key = parse_args(primero, segundo)
        except ValueError as error:
            await responder.send_error(str(error))
            return
        await self._pachinko_impl(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            amount_text=amount_text,
            send=send,
            send_error=responder.send_error,
            board_key=board_key,
        )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(Pachinko(bot, economy=bot.economy, casino_channel_ids=bot.casino_channels))
