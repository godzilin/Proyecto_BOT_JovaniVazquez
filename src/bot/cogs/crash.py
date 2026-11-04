"""Crash: `crash`, un cohete compartido por canal del que hay que saltar a tiempo.

A diferencia del resto del casino, aquí la mesa es de todos: hay una ronda
por canal y cualquiera se sube. Cada ronda pasa por dos fases en el mismo
mensaje:

1. **Embarque** (7-10 s): 🚀 **Entrar** con tu ficha, o `crash 500 2x` para
   entrar con 500 Y$ y auto-retiro en 2x. ½, ×2, 💰 All-in y 🎯 Auto cambian
   tu ficha y tu auto-retiro (se recuerdan entre rondas). La cuenta atrás es
   una marca de tiempo de Discord: se mueve sola sin editar el mensaje.
2. **Vuelo**: el multiplicador sube y 💸 **Retirar** cobra apuesta ×
   multiplicador. Quien sigue dentro cuando explota, lo pierde. Si ya no
   queda nadie dentro, el cohete salta directo a su punto de explosión (para
   no esperar y para que se vea lo que podrías haber ganado).

Al explotar, el mismo mensaje enseña la gráfica de la ronda y abre el
embarque de la siguiente. Si un embarque termina sin nadie, la mesa se
cierra. Si el mensaje ha quedado enterrado bajo otros, la ronda siguiente se
manda abajo del todo y el mensaje viejo pierde los botones.

Reglas en `bot.services.crash`; gráfica en `bot.services.crash_render`.

Ritmo de ediciones: durante el vuelo el bot edita el mensaje una vez por
segundo (`TICK_SECONDS`), dentro del límite de Discord para un canal, y nunca
encadena una edición sobre otra que no haya terminado. Las pulsaciones de
💸 responden editando con la propia interacción, que no gasta ese límite.

Dinero: la apuesta se cobra al entrar (`place_bet`) y se paga al retirarse o
al explotar (`pay_winnings`, con 0 si explota), que es cuando se ajusta el
IRPF del día. Si el bot se apaga de forma ordenada, en embarque se devuelve
lo apostado y en vuelo se retira a todos en el multiplicador del momento.

Solo se juega en los canales del casino que elige `garito` (sin elegir, los de `CASINO_CHANNEL_IDS`)
y en sus hilos. Permisos del bot en el canal: enviar mensajes, insertar enlaces, adjuntar archivos y
añadir reacciones (para confirmar `.cohete` cuando ya hay mesa).
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
import random
import secrets
import time
from collections import deque
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs.casino import casino_channel_error, insufficient_text
from bot.services.achievements import casino_stats, crash_stats
from bot.services.casino_channels import CasinoChannelIds
from bot.services.crash import (
    CrashError,
    CrashRound,
    Seat,
    crash_point,
    format_multiplier,
    looks_like_multiplier,
    multiplier_at,
    parse_multiplier,
    seconds_to,
)
from bot.services.crash_render import CashoutMark, CrashRenderer
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
from bot.services.taxes import TAX_COLLECTOR
from bot.services.telemetry import TimedLock
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

GAME = "crash"
DEFAULT_STAKE = 100
#: Embarque de la primera ronda: más largo para que entren los demás.
FIRST_LOBBY_SECONDS = 10
#: Embarque de las rondas seguidas: lo justo para volver a pulsar.
LOBBY_SECONDS = 7
#: Una edición del mensaje por segundo durante el vuelo.
TICK_SECONDS = 1.0
#: Rondas que se ven en la tira de "Últimas".
HISTORY_SIZE = 10
#: Jugadores que se listan por nombre; el resto se resume.
LIST_LIMIT = 12
#: Máximo de pasos de la estela del cohete en el texto.
TRAIL_SIZE = 18
PNG_NAME = "crash.png"

TRAIL_BLOCKS = "▁▂▃▄▅▆▇█"
WORDS_OFF = {"no", "off", "0", "nada", "sin"}

COLOR_LOBBY = discord.Color.from_rgb(43, 45, 49)
COLOR_BOOM = discord.Color.from_rgb(255, 128, 48)

# Textos en el tono de Jovani Vázquez.
TAKEOFF_LINES = (
    "¡Despegamos!",
    "¡Agárrate!",
    "¡Pa' la Luna!",
    "¡Sube más que el IPC!",
    "¡Y Víctor le dijo a Joel: móntate en mi cohete!",
    "¡Azul, azul, como el mar azul… y arriba!",
    "¡Diez, nueve, ocho…!",
    "¡Cinturones abrochados!",
    "¡Rumbo a Marte!",
    "¡Sube como la factura de la luz!",
    "¡Arriba, arriba!",
)
CASH_LINES = (
    "¡Saltaste a tiempo!",
    "¡Eso es, cobras!",
    "¡Olfato de piloto!",
    "¡Toma mangó!",
    "¡Paracaídas abierto!",
    "¡Aterrizaje perfecto!",
    "¡Paracaídas dorado de consejero!",
    "¡Saltas del cohete con la cartera llena!",
    "¡Ni Fabra tiene ese olfato!",
    "Saltar a tiempo también es ganar.",
)
BOOM_LINES = (
    "¡BOOM!",
    "¡Se fue!",
    "¡Kaboom!",
    "Houston, tenemos un problema.",
    "¡Musk dice que ha sido un éxito!",
    "¡Pinchazo de la burbuja!",
    "Estoy destruido, pero por fuera… por dentro estoy nuevo.",
    "¡Lehman Brothers!",
    "El cohete no llegó a la Luna.",
    "¡Desmontaje rápido no planificado!",
    "Ni una estrella fugaz dura tan poco.",
)


class Phase(Enum):
    """Fase de la mesa."""

    LOBBY = "lobby"
    FLYING = "flying"
    CLOSED = "closed"


def flight_color(cents: int) -> discord.Color:
    """Color del vuelo según lo alto que va (no lleva significado por sí solo)."""
    if cents >= 10_000:
        return discord.Color.from_rgb(255, 255, 255)
    if cents >= 1_000:
        return discord.Color.from_rgb(155, 89, 255)
    if cents >= 500:
        return discord.Color.from_rgb(255, 150, 40)
    if cents >= 200:
        return discord.Color.from_rgb(255, 196, 0)
    return discord.Color.from_rgb(88, 101, 242)


def trail(samples: list[int]) -> str:
    """Estela del cohete: un bloque por segundo, más alto cuanto más sube.

    La altura es logarítmica respecto al multiplicador actual, así la
    estela siempre se ve subir aunque vaya por 1,2x o por 300x.
    """
    if not samples:
        return ""
    top = max(samples[-1], 101)
    blocks = []
    for cents in samples[-TRAIL_SIZE:]:
        level = math.log(max(cents, 100) / 100) / math.log(top / 100)
        blocks.append(TRAIL_BLOCKS[min(len(TRAIL_BLOCKS) - 1, int(level * len(TRAIL_BLOCKS)))])
    return "".join(blocks) + " 🚀"


def short_name(name: str) -> str:
    """Nombre recortado para las listas."""
    return name if len(name) <= 16 else name[:15] + "…"


def history_line(history: deque[int]) -> str:
    """`1,23x · **12,40x** · 1,00x`: en negrita las de 10x o más."""
    parts = []
    for cents in history:
        text = format_multiplier(cents)
        parts.append(f"**{text}**" if cents >= 1_000 else text)
    return " · ".join(parts) or "—"


def seat_line(seat: Seat, *, finished: bool) -> str:
    """Una línea de jugador: dentro, retirado o explotado."""
    name = short_name(seat.name)
    if seat.cashed_cents is not None:
        auto = " 🎯" if seat.by_auto else ""
        return (
            f"✅ {name} · {format_multiplier(seat.cashed_cents)}{auto} · +{format_amount(seat.net)}"
        )
    if finished:
        return f"💥 {name} · -{format_amount(seat.stake)}"
    auto = f" · 🎯 {format_multiplier(seat.auto_cents)}" if seat.auto_cents else ""
    return f"🧑‍🚀 {name} · {format_amount(seat.stake)}{auto}"


def seats_block(seats: list[Seat], *, finished: bool) -> str:
    """Lista de jugadores con tope de líneas."""
    lines = [seat_line(s, finished=finished) for s in seats[:LIST_LIMIT]]
    if len(seats) > LIST_LIMIT:
        lines.append(f"… y {len(seats) - LIST_LIMIT} más")
    return "\n".join(lines) or "Nadie todavía."


def tax_summary(settlements: dict[int, BetSettlement], names: dict[int, str]) -> str | None:
    """Subtexto con lo que retiene o devuelve Hacienda a cada uno en la ronda."""
    taken = [
        f"{format_amount(s.tax_delta)} a {short_name(names[u])}"
        for u, s in settlements.items()
        if s.tax_delta > 0
    ]
    given = [
        f"{format_amount(-s.tax_delta)} a {short_name(names[u])}"
        for u, s in settlements.items()
        if s.tax_delta < 0
    ]
    parts = []
    if taken:
        parts.append("se lleva " + ", ".join(taken))
    if given:
        parts.append("devuelve " + ", ".join(given))
    if not parts:
        return None
    return f"-# 🐶 {TAX_COLLECTOR} {' y '.join(parts)}."


def result_lines(round_: CrashRound, rng: random.Random | None = None) -> str:
    """Bloque del resultado: punto de explosión y quién saltó a tiempo."""
    rng = rng or random.Random()
    seats = sorted(
        round_.seats.values(),
        key=lambda s: (s.cashed_cents is None, -(s.cashed_cents or 0)),
    )
    headline = f"## 💥 {rng.choice(BOOM_LINES)} Explotó en {format_multiplier(round_.crash_cents)}"
    if round_.crash_cents == 100:
        headline += "\nNi despegó."
    return f"{headline}\n{seats_block(seats, finished=True)}"


# -- Botones --------------------------------------------------------------------------


class AutoModal(discord.ui.Modal, title="🎯 Auto-retiro"):
    """Pide el multiplicador del auto-retiro (vacío lo quita)."""

    target: discord.ui.TextInput = discord.ui.TextInput(
        label="Retirarme solo en… (vacío = sin auto)",
        placeholder="2x, 1,5, 10…",
        required=False,
        max_length=10,
    )

    def __init__(self, table: CrashTable) -> None:
        super().__init__()
        self.table = table

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Guarda el auto-retiro para este jugador."""
        text = str(self.target.value or "").strip()
        await self.table.set_auto(interaction, text)


class CrashView(discord.ui.View):
    """Botones de la mesa. Los pulsa cualquiera: la mesa es de todos."""

    def __init__(self, table: CrashTable, phase: Phase) -> None:
        super().__init__(timeout=None)
        self.table = table
        if phase is Phase.LOBBY:
            self._add("🚀 Entrar", discord.ButtonStyle.success, "join", table.join_button)
            self._add("½", discord.ButtonStyle.secondary, "half", table.halve)
            self._add("×2", discord.ButtonStyle.secondary, "x2", table.double)
            self._add("💰 All-in", discord.ButtonStyle.secondary, "allin", table.all_in)
            self._add("🎯 Auto", discord.ButtonStyle.primary, "auto", table.open_auto)
        elif phase is Phase.FLYING:
            self._add("💸 Retirar", discord.ButtonStyle.success, "cashout", table.cash_out)

    def _add(
        self,
        label: str,
        style: discord.ButtonStyle,
        custom_id: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
    ) -> None:
        button: discord.ui.Button = discord.ui.Button(
            label=label, style=style, custom_id=f"{GAME}:{custom_id}"
        )
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)


# -- Mesa ------------------------------------------------------------------------------


class CrashTable:
    """La mesa de Crash de un canal: la ronda en curso, su mensaje y su bucle.

    Guarda quién está sentado y el historial de puntos de explosión. El
    dinero nunca vive aquí: se cobra y se paga siempre por la economía.
    """

    def __init__(self, cog: Crash, *, guild_id: int, channel: discord.abc.Messageable) -> None:
        self.cog = cog
        self.guild_id = guild_id
        self.channel = channel
        self.message: discord.Message | None = None
        self.phase = Phase.LOBBY
        self.round_no = 1
        self.round = CrashRound(cog.new_crash_point())
        self.history: deque[int] = deque(maxlen=HISTORY_SIZE)
        self.lobby_ends = cog.wall_clock() + FIRST_LOBBY_SECONDS
        self.lobby_seconds: float = FIRST_LOBBY_SECONDS
        self.launched_at = 0.0
        self.samples: list[int] = []
        self.users: dict[int, discord.abc.User] = {}
        self.settlements: dict[int, BetSettlement] = {}
        #: Quien escribió `crash` durante un vuelo: entra en la ronda siguiente.
        self.queued: dict[int, tuple[discord.abc.User, int, int | None]] = {}
        self.last_result: str | None = None
        self.last_png: bytes | None = None
        self.task: asyncio.Task[None] | None = None
        self._edit_task: asyncio.Task[None] | None = None
        self._lock = TimedLock("crash:CrashTable._lock")
        self._views: dict[Phase, CrashView] = {}
        #: El apagado pide parar; el bucle solo se corta mientras duerme,
        #: nunca a mitad de un pago.
        self.stopping = False
        self.sleeping = False

    def view(self, phase: Phase) -> CrashView:
        """Botones de una fase; se reutilizan en vez de crear unos nuevos por edición."""
        view = self._views.get(phase)
        if view is None:
            view = self._views[phase] = CrashView(self, phase)
        return view

    async def nap(self, seconds: float) -> None:
        """Espera del bucle; el único sitio donde el apagado lo puede cortar."""
        if self.stopping:
            raise asyncio.CancelledError
        self.sleeping = True
        try:
            await self.cog.sleep(seconds)
        finally:
            self.sleeping = False

    # -- Vista ------------------------------------------------------------------------

    @property
    def crash_seconds(self) -> float:
        """Segundos de vuelo hasta la explosión."""
        return seconds_to(self.round.crash_cents)

    def elapsed(self) -> float:
        """Segundos desde el despegue."""
        return self.cog.clock() - self.launched_at

    def current_cents(self) -> int:
        """Multiplicador ahora mismo (sin pasar del punto de explosión)."""
        return min(multiplier_at(self.elapsed()), self.round.crash_cents)

    def lobby_embed(self) -> discord.Embed:
        """Embarque, con el resultado de la ronda anterior si la hay."""
        lines = []
        if self.last_result:
            lines.append(self.last_result)
            lines.append("")
        lines.append(f"### 🚀 Despega <t:{math.ceil(self.lobby_ends)}:R>")
        lines.append("Pulsa 🚀 **Entrar** o escribe `crash 500 2x`.")
        embed = discord.Embed(title="🚀 Crash", description="\n".join(lines), color=COLOR_LOBBY)
        seats = list(self.round.seats.values())
        embed.add_field(
            name=f"A bordo ({len(seats)}) · {format_amount(self.round.total_staked)}",
            value=seats_block(seats, finished=False),
            inline=False,
        )
        embed.add_field(name="Últimas", value=history_line(self.history), inline=False)
        if self.last_png is not None:
            embed.set_image(url=f"attachment://{PNG_NAME}")
        embed.set_footer(text=f"Ronda #{self.round_no} · devuelve el 99 % · tope 1.000x")
        return embed

    def flight_embed(self) -> discord.Embed:
        """El cohete en el aire: multiplicador grande, estela y jugadores."""
        cents = self.current_cents()
        riding = self.round.riding
        lines = [f"# 🚀 {format_multiplier(cents)}", trail([*self.samples, cents])]
        # Una frase por ronda, no por fotograma: el embed se redibuja varias veces por segundo.
        lines.append(f"-# {random.Random(self.round_no).choice(TAKEOFF_LINES)}")
        if riding:
            lines.append(f"-# {len(riding)} dentro · pulsa 💸 **Retirar** para cobrar")
        embed = discord.Embed(
            title="🚀 Crash", description="\n".join(lines), color=flight_color(cents)
        )
        seats = sorted(
            self.round.seats.values(), key=lambda s: (s.cashed_cents is not None, s.name)
        )
        embed.add_field(name="Pasajeros", value=seats_block(seats, finished=False), inline=False)
        embed.set_footer(text=f"Ronda #{self.round_no}")
        return embed

    def closed_embed(self) -> discord.Embed:
        """Mesa cerrada: el último resultado y cómo abrir otra."""
        description = (self.last_result + "\n\n") if self.last_result else ""
        description += "Mesa cerrada. `crash` para despegar otra."
        embed = discord.Embed(title="🚀 Crash", description=description, color=COLOR_LOBBY)
        embed.add_field(name="Últimas", value=history_line(self.history), inline=False)
        if self.last_png is not None:
            embed.set_image(url=f"attachment://{PNG_NAME}")
        return embed

    async def edit(self, **kwargs: Any) -> None:
        """Edita el mensaje de la mesa; un fallo de Discord no rompe la ronda."""
        if self.message is None:
            return
        try:
            await self.message.edit(**kwargs)
        except discord.HTTPException:
            logger.debug("No se pudo editar la mesa de Crash", exc_info=True)

    def edit_soon(self) -> None:
        """Edición del vuelo sin esperar; si la anterior no ha terminado, se salta."""
        if self._edit_task is not None and not self._edit_task.done():
            return
        self._edit_task = asyncio.create_task(
            self.edit(embed=self.flight_embed(), view=self.view(Phase.FLYING))
        )

    # -- Jugadores --------------------------------------------------------------------

    async def sit(self, user: discord.abc.User, stake: int, auto_cents: int | None) -> None:
        """Cobra la apuesta y sienta al jugador en el embarque.

        Raises:
            InsufficientFundsError: Si no le llega.
            CrashError: Si ya estaba dentro o la ronda ya despegó.
        """
        async with self._lock:
            if self.phase is not Phase.LOBBY:
                raise CrashError("La ronda ya ha despegado.")
            if user.id in self.round.seats:
                raise CrashError("Ya estás dentro de esta ronda.")
            await self.cog.economy.place_bet(self.guild_id, user.id, game=GAME, stake=stake)
            self.round.sit(Seat(user.id, user.display_name, stake, auto_cents))
            self.users[user.id] = user

    async def pay(self, seat: Seat) -> None:
        """Paga a un jugador (0 si explotó) y ajusta su IRPF del día."""
        try:
            settlement = await self.cog.economy.pay_winnings(
                self.guild_id, seat.user_id, game=GAME, amount=seat.payout
            )
        except BalanceLimitError:
            logger.warning("Premio de Crash por encima del saldo máximo; no se paga.")
            return
        self.settlements[seat.user_id] = settlement

    async def _ficha_or_error(self, interaction: discord.Interaction) -> int | None:
        """Ficha del jugador si le llega; si no, avisa en privado y devuelve `None`."""
        stake = self.cog.ficha(self.guild_id, interaction.user.id)
        balance = await self.cog.economy.balance(self.guild_id, interaction.user.id)
        if balance <= 0 or stake > balance:
            await notify(interaction, insufficient_text(balance, stake))
            return None
        return stake

    async def join_button(self, interaction: discord.Interaction) -> None:
        """🚀 Entrar: se sienta con su ficha y su auto-retiro."""
        user = interaction.user
        if self.phase is not Phase.LOBBY:
            await ack(interaction)
            return
        if user.id in self.round.seats:
            seat = self.round.seats[user.id]
            await interaction.response.send_message(
                f"Ya estás dentro con {format_amount(seat.stake)}.", ephemeral=True
            )
            return
        # Leer el saldo y cobrar van a la base de datos: se acepta el clic antes.
        await ack(interaction)
        stake = await self._ficha_or_error(interaction)
        if stake is None:
            return
        try:
            await self.sit(user, stake, self.cog.auto(self.guild_id, user.id))
        except InsufficientFundsError as error:
            await notify(interaction, insufficient_text(error.balance, stake))
            return
        except CrashError as error:
            await notify(interaction, str(error))
            return
        await edit(interaction, embed=self.lobby_embed(), view=self.view(Phase.LOBBY))
        await renta.remind(self.cog.bot, interaction)

    async def _set_ficha(self, interaction: discord.Interaction, stake: int) -> None:
        self.cog.set_ficha(self.guild_id, interaction.user.id, stake)
        await notify(interaction, self.ficha_text(interaction.user.id))

    def ficha_text(self, user_id: int) -> str:
        """Ficha y auto-retiro de un jugador, para sus avisos privados."""
        stake = self.cog.ficha(self.guild_id, user_id)
        auto = self.cog.auto(self.guild_id, user_id)
        text = f"🎟️ Tu ficha: **{format_amount(stake)}**"
        text += f" · 🎯 auto en **{format_multiplier(auto)}**" if auto else " · 🎯 sin auto"
        if user_id in self.round.seats:
            text += "\n-# Ya estás dentro: la ficha nueva vale para la próxima ronda."
        return text

    async def halve(self, interaction: discord.Interaction) -> None:
        """½: divide la ficha."""
        await self._set_ficha(
            interaction, max(1, self.cog.ficha(self.guild_id, interaction.user.id) // 2)
        )

    async def double(self, interaction: discord.Interaction) -> None:
        """×2: dobla la ficha (como mucho, todo el saldo)."""
        await ack(interaction)
        balance = await self.cog.economy.balance(self.guild_id, interaction.user.id)
        stake = self.cog.ficha(self.guild_id, interaction.user.id) * 2
        await self._set_ficha(interaction, max(1, min(stake, balance)))

    async def all_in(self, interaction: discord.Interaction) -> None:
        """💰 All-in: ficha = todo el saldo."""
        await ack(interaction)
        balance = await self.cog.economy.balance(self.guild_id, interaction.user.id)
        if balance <= 0:
            await notify(interaction, insufficient_text(0))
            return
        await self._set_ficha(interaction, balance)

    async def open_auto(self, interaction: discord.Interaction) -> None:
        """🎯 Auto: abre el formulario del auto-retiro."""
        await interaction.response.send_modal(AutoModal(self))

    async def set_auto(self, interaction: discord.Interaction, text: str) -> None:
        """Guarda el auto-retiro; en embarque también cambia el de su asiento."""
        if not text or text.lower() in WORDS_OFF:
            auto = None
        else:
            try:
                auto = parse_multiplier(text)
            except ValueError as error:
                await interaction.response.send_message(str(error), ephemeral=True)
                return
        self.cog.set_auto(self.guild_id, interaction.user.id, auto)
        seat = self.round.seats.get(interaction.user.id)
        if seat is not None and self.phase is Phase.LOBBY:
            seat.auto_cents = auto
            await interaction.response.edit_message(
                embed=self.lobby_embed(), view=self.view(Phase.LOBBY)
            )
            await interaction.followup.send(self.ficha_text(interaction.user.id), ephemeral=True)
            return
        await interaction.response.send_message(
            self.ficha_text(interaction.user.id), ephemeral=True
        )

    async def cash_out(self, interaction: discord.Interaction) -> None:
        """💸 Retirar: cobra apuesta × multiplicador de este instante."""
        if self.phase is not Phase.FLYING:
            await ack(interaction)
            return
        user_id = interaction.user.id
        elapsed = self.elapsed()
        if elapsed >= self.crash_seconds:
            await interaction.response.send_message(
                f"¡Tarde! Explotó en {format_multiplier(self.round.crash_cents)}.", ephemeral=True
            )
            return
        try:
            seat = self.round.cash_out(user_id, multiplier_at(elapsed))
        except CrashError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        # El multiplicador ya está fijado; pagar va a la base de datos y tarda.
        await ack(interaction)
        await self.pay(seat)
        await edit(interaction, embed=self.flight_embed(), view=self.view(Phase.FLYING))

        text = (
            f"## ✅ {random.choice(CASH_LINES)} {format_multiplier(seat.cashed_cents or 0)}\n"
            f"Cobras **{format_amount(seat.payout)}** (+{format_amount(seat.net)})."
        )
        settlement = self.settlements.get(user_id)
        if settlement is not None and (note := gambling_tax_line(settlement)):
            text += f"\n{note}"
        if hint := await renta.hint(
            self.cog.bot,
            self.guild_id,
            user_id,
            bet_moment(
                stake=seat.stake,
                net=seat.net,
                balance_after=settlement.balance if settlement is not None else 0,
            ),
        ):
            text += f"\n{hint}"
        try:
            await interaction.followup.send(text, ephemeral=True)
        except discord.HTTPException:
            logger.debug("No se pudo enviar el resultado privado de Crash", exc_info=True)
        await renta.remind(self.cog.bot, interaction)

    # -- Ciclo de una ronda -----------------------------------------------------------

    async def run(self) -> None:
        """Bucle de la mesa: embarque, vuelo y explosión hasta que nadie entre."""
        try:
            while True:
                await self.nap(max(0.0, self.lobby_ends - self.cog.wall_clock()))
                if not self.round.seats:
                    await self.close()
                    return
                await self.fly()
                await self.finish()
        except asyncio.CancelledError:
            if not self.stopping:
                raise
        except Exception:
            logger.exception("La mesa de Crash ha fallado; se devuelve lo apostado")
            self.cog.tables.pop(getattr(self.channel, "id", None), None)
            await self.shutdown()

    async def fly(self) -> None:
        """El vuelo: auto-retiros exactos, una edición por segundo y la explosión."""
        async with self._lock:
            self.phase = Phase.FLYING
            self.launched_at = self.cog.clock()
            self.samples = []
        await self.edit(embed=self.flight_embed(), view=self.view(Phase.FLYING), attachments=[])
        last_tick = 0.0
        while True:
            elapsed = self.elapsed()
            done = elapsed >= self.crash_seconds
            for seat in self.round.due_autos(
                self.round.crash_cents if done else multiplier_at(elapsed)
            ):
                await self.pay(seat)
            if done:
                break
            if not self.round.riding:
                # Nadie dentro: sin esperar al resto del vuelo, a explotar.
                break
            if elapsed - last_tick >= TICK_SECONDS:
                last_tick = elapsed
                self.samples.append(multiplier_at(elapsed))
                self.edit_soon()
            wake = [last_tick + TICK_SECONDS, self.crash_seconds]
            if (target := self.round.next_auto()) is not None:
                wake.append(seconds_to(target))
            await self.nap(max(0.02, min(wake) - elapsed))

    async def finish(self) -> None:
        """Explosión: cobra a los que siguen dentro, enseña la gráfica y abre otra ronda."""
        if self._edit_task is not None:
            await asyncio.gather(self._edit_task, return_exceptions=True)
        async with self._lock:
            finished = self.round
            for seat in finished.riding:
                await self.pay(seat)
            self.history.appendleft(finished.crash_cents)
            names = {s.user_id: s.name for s in finished.seats.values()}
            result = result_lines(finished)
            if note := tax_summary(self.settlements, names):
                result += f"\n{note}"
            self.last_result = result
            settlements = self.settlements
            users = dict(self.users)

            cashed = [s for s in finished.seats.values() if s.cashed_cents is not None]
            players = len(finished.seats)
            subtitle = f"Ronda {self.round_no} · {players} jugador{'es' if players != 1 else ''}"
            if cashed:
                subtitle += f" · {len(cashed)} cobra{'n' if len(cashed) != 1 else ''}"
            marks = [CashoutMark(short_name(s.name), s.cashed_cents or 0) for s in cashed]
            self.last_png = await asyncio.to_thread(
                self.cog.renderer.render,
                crash_cents=finished.crash_cents,
                cashouts=marks,
                subtitle=subtitle,
            )

            # Ronda siguiente.
            self.round_no += 1
            self.round = CrashRound(self.cog.new_crash_point())
            self.settlements = {}
            self.users = {}
            self.phase = Phase.LOBBY
            self.lobby_seconds = LOBBY_SECONDS
            self.lobby_ends = self.cog.wall_clock() + LOBBY_SECONDS
            queued, self.queued = self.queued, {}
        for user, stake, auto in queued.values():
            try:
                await self.sit(user, stake, auto)
            except (InsufficientFundsError, CrashError):
                logger.debug("Un jugador en cola no pudo entrar en la ronda de Crash")

        await self.show_lobby()
        await self.track(finished, settlements, users)

    async def show_lobby(self) -> None:
        """Enseña el embarque: en el mismo mensaje, o abajo del todo si quedó enterrado."""
        kwargs: dict[str, Any] = {
            "embed": self.lobby_embed(),
            "view": self.view(Phase.LOBBY),
        }
        buried = (
            self.message is not None
            and getattr(self.channel, "last_message_id", self.message.id) != self.message.id
        )
        if buried:
            old = self.message
            if self.last_png is not None:
                kwargs["file"] = self._png_file()
            try:
                self.message = await self.channel.send(**kwargs)
            except discord.HTTPException:
                logger.debug("No se pudo mandar la mesa de Crash abajo", exc_info=True)
                kwargs.pop("file", None)
            else:
                try:
                    await old.edit(view=None)  # type: ignore[union-attr]
                except discord.HTTPException:
                    logger.debug("No se pudo apagar la mesa vieja de Crash", exc_info=True)
                return
        if self.last_png is not None:
            kwargs["attachments"] = [self._png_file()]
        await self.edit(**kwargs)

    def _png_file(self) -> discord.File:
        assert self.last_png is not None
        return discord.File(io.BytesIO(self.last_png), filename=PNG_NAME)

    async def track(
        self,
        finished: CrashRound,
        settlements: dict[int, BetSettlement],
        users: dict[int, discord.abc.User],
    ) -> None:
        """Logros de cada jugador de la ronda, después de enseñar el resultado."""
        cashed = [s for s in finished.seats.values() if s.cashed_cents is not None]
        last_out = max(cashed, key=lambda s: s.cashed_cents or 0) if len(cashed) >= 2 else None
        for seat in finished.seats.values():
            user = users.get(seat.user_id)
            settlement = settlements.get(seat.user_id)
            if user is None:
                continue
            delta = crash_stats(
                seat,
                crash_cents=finished.crash_cents,
                players=len(finished.seats),
                last_out=last_out is seat and bool(finished.riding),
            )
            delta.merge(
                casino_stats(
                    stake=seat.stake,
                    net=seat.net,
                    balance_after=settlement.balance if settlement else 0,
                    tax_delta=settlement.tax_delta if settlement else 0,
                )
            )
            await logros.casino_play(
                self.cog.bot, self.guild_id, user, self.channel, delta, net=seat.net
            )
            await apuestas.record(
                self.cog.bot,
                self.guild_id,
                user,
                game=GAME,
                stake=seat.stake,
                net=seat.net,
                balance_after=settlement.balance if settlement else 0,
                tax=settlement.tax_delta if settlement else 0,
            )

    async def close(self) -> None:
        """Embarque vacío: la mesa se cierra y deja de tener botones."""
        self.phase = Phase.CLOSED
        self.cog.tables.pop(getattr(self.channel, "id", None), None)
        await self.edit(embed=self.closed_embed(), view=None)
        self.stop_views()

    def stop_views(self) -> None:
        """Suelta los botones para que discord.py deje de escucharlos."""
        for view in self._views.values():
            view.stop()
        self._views.clear()

    async def shutdown(self) -> None:
        """Cierre ordenado: nadie pierde dinero por apagar el bot.

        En embarque se devuelve lo apostado (premio = apuesta, neto 0); en
        vuelo se retira a todos los que siguen dentro en el multiplicador
        del momento, o pierden si ya había explotado.
        """
        async with self._lock:
            if self.phase is Phase.LOBBY:
                for seat in self.round.seats.values():
                    try:
                        await self.cog.economy.pay_winnings(
                            self.guild_id, seat.user_id, game=GAME, amount=seat.stake
                        )
                    except BalanceLimitError:
                        logger.warning("No se pudo devolver una apuesta de Crash.")
            elif self.phase is Phase.FLYING:
                cents = multiplier_at(self.elapsed())
                for seat in self.round.riding:
                    if cents < self.round.crash_cents:
                        self.round.cash_out(seat.user_id, cents)
                    await self.pay(seat)
            self.round = CrashRound(self.round.crash_cents)
            self.phase = Phase.CLOSED
        await self.edit(embed=self.closed_embed(), view=None)
        self.stop_views()


# -- Cog -------------------------------------------------------------------------------


class Crash(commands.Cog):
    """El Crash del casino: una ronda compartida por canal."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        renderer: CrashRenderer | None = None,
        casino_channel_ids: CasinoChannelIds = frozenset(),
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.renderer = renderer or CrashRenderer()
        self.casino_channel_ids = casino_channel_ids
        # `secrets` usa el azar del sistema operativo: no se puede predecir.
        self.rng = rng or secrets.SystemRandom()
        self.clock = clock
        self.wall_clock = wall_clock
        self.sleep = sleep
        #: Mesa abierta de cada canal.
        self.tables: dict[int, CrashTable] = {}
        # Ficha y auto-retiro por (servidor, miembro). Crecen como mucho hasta
        # el número de miembros que han jugado; se pierden al reiniciar.
        self._fichas: dict[tuple[int, int], int] = {}
        self._autos: dict[tuple[int, int], int | None] = {}

    def new_crash_point(self) -> int:
        """Sortea el punto de explosión de una ronda."""
        return crash_point(self.rng.random())

    def ficha(self, guild_id: int, user_id: int) -> int:
        """Ficha recordada de un miembro."""
        return self._fichas.get((guild_id, user_id), DEFAULT_STAKE)

    def set_ficha(self, guild_id: int, user_id: int, stake: int) -> None:
        """Recuerda la ficha de un miembro."""
        self._fichas[(guild_id, user_id)] = max(1, stake)

    def auto(self, guild_id: int, user_id: int) -> int | None:
        """Auto-retiro recordado de un miembro."""
        return self._autos.get((guild_id, user_id))

    def set_auto(self, guild_id: int, user_id: int, cents: int | None) -> None:
        """Recuerda el auto-retiro de un miembro (`None` lo quita)."""
        self._autos[(guild_id, user_id)] = cents

    async def cog_unload(self) -> None:
        """Al apagar, devuelve o paga todo lo que esté en juego."""
        for table in list(self.tables.values()):
            table.stopping = True
            if table.task is not None:
                # Solo se cancela si está esperando: a mitad de un pago se
                # deja terminar y el bucle se corta en su siguiente espera.
                if table.sleeping:
                    table.task.cancel()
                await asyncio.gather(table.task, return_exceptions=True)
            try:
                await table.shutdown()
            except Exception:
                logger.exception("No se pudo cerrar una mesa de Crash al apagar")
        self.tables.clear()

    # -- crash -----------------------------------------------------------------------

    @staticmethod
    def split_args(first: str | None, second: str | None) -> tuple[str | None, str | None]:
        """Separa cantidad y auto-retiro en cualquier orden (`500 2x` o `2x 500`)."""
        amount, auto = None, None
        for arg in (first, second):
            if not arg:
                continue
            if looks_like_multiplier(arg) or arg.lower() in WORDS_OFF:
                auto = arg
            else:
                amount = arg
        return amount, auto

    async def _crash_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        auto_text: str | None,
        send: Callable[..., Awaitable[discord.Message]],
        confirm: Callable[[str], Awaitable[None]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> None:
        """Lógica compartida de `/cohete` y `.cohete`: abre la mesa o entra en ella."""
        if guild is None or not isinstance(channel, discord.abc.Messageable):
            await send_error("El Crash solo se juega dentro de un servidor.")
            return
        if error := casino_channel_error(self.casino_channel_ids, channel, "El Crash"):
            await send_error(error)
            return
        balance = await self.economy.balance(guild.id, user.id)
        try:
            stake = (
                parse_amount(amount_text, balance)
                if amount_text
                else max(1, min(self.ficha(guild.id, user.id), balance))
            )
            if auto_text is not None:
                off = auto_text.lower() in WORDS_OFF
                self.set_auto(guild.id, user.id, None if off else parse_multiplier(auto_text))
        except ValueError as error:
            await send_error(str(error))
            return
        if balance <= 0 or stake > balance:
            await send_error(insufficient_text(balance, stake))
            return
        self.set_ficha(guild.id, user.id, stake)
        auto = self.auto(guild.id, user.id)
        channel_id = getattr(channel, "id", 0)
        table = self.tables.get(channel_id)

        if table is not None and table.phase is Phase.FLYING:
            table.queued[user.id] = (user, stake, auto)
            await confirm(
                f"🚀 Hay un cohete en el aire. Entras en la siguiente con {format_amount(stake)}."
            )
            return

        if table is not None and table.phase is Phase.LOBBY:
            try:
                await table.sit(user, stake, auto)
            except InsufficientFundsError as error:
                await send_error(insufficient_text(error.balance, stake))
                return
            except CrashError as error:
                await send_error(str(error))
                return
            await table.edit(embed=table.lobby_embed(), view=table.view(Phase.LOBBY))
            auto_text_out = f" · 🎯 {format_multiplier(auto)}" if auto else ""
            await confirm(f"🚀 Dentro con {format_amount(stake)}{auto_text_out}.")
            return

        table = CrashTable(self, guild_id=guild.id, channel=channel)
        try:
            await table.sit(user, stake, auto)
        except InsufficientFundsError as error:
            await send_error(insufficient_text(error.balance, stake))
            return
        self.tables[channel_id] = table
        table.message = await send(embed=table.lobby_embed(), view=table.view(Phase.LOBBY))
        self.start(table)

    def start(self, table: CrashTable) -> None:
        """Arranca el bucle de rondas de una mesa recién abierta."""
        channel_id = getattr(table.channel, "id", 0)
        table.task = asyncio.create_task(table.run(), name=f"crash-{channel_id}")

    @app_commands.command(
        name="cohete", description="Crash: sube al cohete y salta antes de que explote."
    )
    @app_commands.describe(
        cantidad="Apuesta: 500, 2k, all… (por defecto tu última ficha o 100)",
        auto="Auto-retiro: 2x, 1,5… (`no` para quitarlo)",
    )
    @app_commands.guild_only()
    async def crash(
        self,
        interaction: discord.Interaction,
        cantidad: str | None = None,
        auto: str | None = None,
    ) -> None:
        """Abre la mesa de Crash del canal o entra en la ronda que esté embarcando.

        Solo en los canales del casino que elige `garito` (sin elegir, los de `CASINO_CHANNEL_IDS`)
        y en sus hilos. Cobra la apuesta al entrar y paga al retirarse.
        """

        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            return await interaction.original_response()

        async def confirm(text: str) -> None:
            await interaction.response.send_message(text, ephemeral=True)

        await self._crash_impl(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            auto_text=auto,
            send=send,
            confirm=confirm,
            send_error=InteractionResponder(interaction).send_error,
        )
        await renta.remind(self.bot, interaction)

    @commands.command(name="cohete")
    @commands.guild_only()
    async def crash_text(
        self, ctx: commands.Context, primero: str | None = None, segundo: str | None = None
    ) -> None:
        """Versión de texto: `.cohete`, `.cohete 500`, `.cohete 500 2x` o `.cohete 2x`."""
        amount_text, auto_text = self.split_args(primero, segundo)

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        async def confirm(text: str) -> None:
            # Sin mensajes privados en comandos de texto: una reacción basta.
            try:
                await ctx.message.add_reaction("🚀")
            except discord.HTTPException:
                await ctx.send(text)

        await self._crash_impl(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            amount_text=amount_text,
            auto_text=auto_text,
            send=send,
            confirm=confirm,
            send_error=ContextResponder(ctx).send_error,
        )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(Crash(bot, economy=bot.economy, casino_channel_ids=bot.casino_channels))
