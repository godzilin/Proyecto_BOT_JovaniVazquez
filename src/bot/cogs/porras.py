"""Porras: `porra`, apuestas entre miembros sobre las próximas jugadas de otro.

`/porra miembro juego [propuesta] [jugadas] [apuesta]` (y `.porra`) le monta una
porra a otro miembro: «¿Ana acaba ganando en sus próximas 3 partidas de minas de
al menos 500 Y$?». Sin argumentos, enseña las porras en marcha del servidor.

Todo pasa en un mismo mensaje público (el panel), en cuatro fases:

1. **Propuesta:** solo el protagonista puede pulsar ✅ Acepto o ❌ Paso (tiene
   `ACCEPT_SECONDS`). Quien la monta puede retirarla.
2. **Apuestas** (`BETTING_SECONDS`): un botón por opción. Cualquiera menos el
   protagonista apuesta con un formulario (500, 2k, mitad, all…). Cada uno, a una
   sola opción. El panel enseña el bote de cada opción y lo que pagaría.
3. **En juego:** el bot avisa al protagonista y cuenta sus jugadas del juego
   elegido (le llegan por `apuestas.record`, ver `observe`). Tiene un plazo; si no
   juega, se anula y se devuelve todo.
4. **Resultado:** el panel dice qué ha salido, quién gana cuánto y lo que se lleva
   Hacienda. Un mensaje aparte menciona a los ganadores.

Reglas (opciones, tope del bote, reparto) en `bot.services.porras`. Dinero, en
`EconomyService.porra_bet` y `EconomyService.settle_porra`, con su tratamiento
fiscal: juego para los apostantes, IAJ para el Estado y derechos de imagen con
retención del 24 % para el protagonista. Cada apuesta es un botón, así que lleva
`renta.remind`. El resultado sale en las estadísticas del casino (`apuestas`, juego
`porra`) y alimenta los logros de 🎫 Porras.

Objetos de la tienda (pasillo 🎫 Peña de la porra): con los 🔭 Prismáticos de la
UCO, el botón 🔭 enseña a cada apostante y lo que lleva; con la 📓 Libreta de la
porra se pueden montar porras de hasta `MAX_PLAYS_NOTEBOOK` jugadas.

Si el bot se apaga de forma ordenada, las porras sin terminar se anulan y se
devuelve todo; si se cae, se hace lo mismo al volver (`recover`).

Solo se montan en los canales del casino que elige `garito` (sin elegir, los de
`CASINO_CHANNEL_IDS`) y en sus hilos. Permisos del bot en el canal: enviar mensajes e insertar
enlaces.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta, shop
from bot.cogs.casino import casino_channel_error
from bot.repositories.porras import PorraRepository
from bot.services.achievements import (
    casino_stats,
    porra_answer_stats,
    porra_bet_stats,
    porra_bettor_stats,
    porra_no_show_stats,
    porra_open_stats,
    porra_snoop_stats,
    porra_subject_stats,
)
from bot.services.casino_channels import CasinoChannelIds
from bot.services.casino_stats import Play
from bot.services.economy import (
    CURRENCY_EMOJI,
    BalanceLimitError,
    EconomyService,
    InsufficientFundsError,
    PorraCapError,
    PorraClosedError,
    PorraPayment,
    PorraSideError,
    format_amount,
    parse_amount,
)
from bot.services.levels import TIMEZONE
from bot.services.pets import Moment
from bot.services.porras import (
    ACCEPT_SECONDS,
    BETTING_SECONDS,
    BINOCULARS_KEY,
    GAME,
    IMAGE_SHARE,
    MAX_PLAYS_NOTEBOOK,
    MIN_BET,
    MIN_STAKE,
    PROPOSITION_BY_KEY,
    VOID_TEXT,
    Bet,
    Porra,
    Split,
    Status,
    VoidReason,
    allowed_games,
    by_outcome,
    describe_plays,
    is_one_sided,
    max_plays,
    merge_bets,
    payout_multiplier,
    play_deadline,
    propositions_for,
    refund_split,
    share_of_side,
    split,
)
from bot.services.taxes import GAMING_TAX_RATE, IMAGE_RIGHTS_WITHHOLDING, TAX_COLLECTOR
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

#: Verde tapete de bar.
COLOR = discord.Color.from_rgb(46, 139, 87)
RESOLVED_COLOR = discord.Color.gold()
VOID_COLOR = discord.Color.dark_grey()
#: Segundos de espera antes de repintar el panel tras una apuesta o una jugada, para
#: juntar varias en una sola edición (Discord limita las ediciones por canal).
REFRESH_DELAY = 1.5
#: Ganadores que se nombran en el resultado; el resto se resume.
MAX_WINNER_LINES = 10
#: Nombres que también valen para elegir el juego en `.porra`.
GAME_ALIASES = {
    "tragas": "tragaperras",
    "bj": "blackjack",
    "volcan": "botes",
    "mina": "botes",
    "olimpo": "botes",
    "gallina": "pollo",
    "cara": "moneda",
    "cruz": "moneda",
    "craps": "dados",
}
#: Qué opciones de la propuesta caben como botones (5 filas de 5, una para el resto).
MAX_OPTION_BUTTONS = 20

_IAJ_PCT = round(GAMING_TAX_RATE * 100)
_IMAGE_TAX_PCT = round(IMAGE_RIGHTS_WITHHOLDING * 100)


def _multiplier(value: float) -> str:
    """`2.345` → `×2,35`."""
    return f"×{value:.2f}".replace(".", ",")


def _plays_word(plays: int) -> str:
    return "jugada" if plays == 1 else "jugadas"


def game_label(game: str) -> str:
    """`💣 Minas`."""
    emoji, name = allowed_games().get(game, ("🎲", game))
    return f"{emoji} {name}"


def resolve_game(text: str | None) -> str | None:
    """Clave del juego a partir de lo que escribe el usuario, o `None`."""
    if not text:
        return None
    value = text.strip().lower()
    value = GAME_ALIASES.get(value, value)
    return value if value in allowed_games() else None


def parse_text_args(args: Sequence[str]) -> tuple[str | None, int | None, str | None]:
    """Interpreta lo que va detrás del juego en `.porra @x minas …`.

    Orden: `[propuesta] [jugadas] [apuesta]`. Con un solo número, si cabe en las
    jugadas (hasta `MAX_PLAYS_NOTEBOOK`) son las jugadas; si no, la apuesta.

    Returns:
        `(propuesta, jugadas, apuesta)`, con `None` en lo que no venga.
    """
    tokens = list(args)
    prop = None
    if tokens and tokens[0].lower() in PROPOSITION_BY_KEY:
        prop = tokens.pop(0).lower()
    plays: int | None = None
    stake: str | None = None
    if len(tokens) >= 2 and tokens[0].isdigit():
        plays = int(tokens.pop(0))
        stake = tokens.pop(0)
    elif tokens:
        token = tokens.pop(0)
        if token.isdigit() and int(token) <= MAX_PLAYS_NOTEBOOK:
            plays = int(token)
        else:
            stake = token
    return prop, plays, stake


# -- Textos del panel --------------------------------------------------------------------


def question(porra: Porra, subject_name: str) -> str:
    """La pregunta de la porra con el nombre del protagonista."""
    text = porra.prop.question.replace("{who}", subject_name)
    return text.replace("{n}", str(porra.plays))


def deal_line(porra: Porra) -> str:
    """Quién se la monta a quién y qué tiene que jugar."""
    return (
        f"<@{porra.opener_id}> se la monta a <@{porra.subject_id}>: **{porra.plays}** "
        f"{_plays_word(porra.plays)} de {game_label(porra.game)} de al menos "
        f"**{format_amount(porra.stake)}**."
    )


def option_lines(porra: Porra, bets: Sequence[Bet], *, winning: int | None = None) -> list[str]:
    """Una línea por opción, con su bote, sus apostantes y lo que pagaría."""
    totals = by_outcome(bets, len(porra.options))
    lines = []
    for index, option in enumerate(porra.options):
        people = sum(1 for bet in bets if bet.outcome == index)
        parts = [f"`{index + 1}` {option}", f"**{format_amount(totals[index])}**"]
        if people:
            parts.append(f"{people} {'persona' if people == 1 else 'personas'}")
        if (mult := payout_multiplier(totals, index)) is not None:
            parts.append(f"paga {_multiplier(mult)}")
        line = " · ".join(parts)
        if winning is not None and index == winning:
            line = f"✅ {line}"
        lines.append(line)
    return lines


def fine_print(porra: Porra) -> str:
    """Letra pequeña: lo que se lleva Hacienda y el protagonista."""
    return (
        f"-# 🐶 {TAX_COLLECTOR} se queda el {_IAJ_PCT} % de cada apuesta (Impuesto sobre "
        f"Actividades de Juego) y <@{porra.subject_id}> cobra el {IMAGE_SHARE} % por derechos "
        "de imagen. Si nadie acierta, o todo el dinero va a lo mismo, se devuelve entero."
    )


def panel_embed(
    porra: Porra, bets: Sequence[Bet], subject_name: str, *, until: float
) -> discord.Embed:
    """El panel de una porra en marcha (propuesta, apuestas o en juego)."""
    pool = sum(bet.stake for bet in bets)
    lines = [deal_line(porra), ""]
    if porra.status is Status.PROPOSED:
        lines.append(
            f"⏳ <@{porra.subject_id}>, ¿aceptas? Tienes hasta <t:{int(until)}:R>. "
            "Te comprometes a jugarlas."
        )
    elif porra.status is Status.OPEN:
        lines.append(
            f"⏳ **Apuestas abiertas** hasta <t:{int(until)}:R>. Pulsa una opción para "
            f"apostar (mínimo {format_amount(MIN_BET)}; <@{porra.subject_id}> no puede)."
        )
    elif porra.status is Status.LOCKED:
        lines.append(
            f"🎲 **No va más.** <@{porra.subject_id}> tiene hasta <t:{int(until)}:R> para "
            f"jugar. Lleva {len(porra.seen)}/{porra.plays}: {describe_plays(porra.seen)}."
        )
    lines += ["", *option_lines(porra, bets), ""]
    lines.append(f"💰 Bote: **{format_amount(pool)}** de {format_amount(porra.cap)} como mucho")
    lines.append(fine_print(porra))
    return discord.Embed(
        title=f"🎫 {question(porra, subject_name)}", description="\n".join(lines), color=COLOR
    )


def result_embed(
    porra: Porra,
    bets: Sequence[Bet],
    subject_name: str,
    sp: Split,
    payment: PorraPayment,
) -> discord.Embed:
    """El panel al acabar: qué ha salido, quién gana y lo que se lleva Hacienda."""
    lines = [deal_line(porra), ""]
    lines.append(f"🎲 Ha jugado {len(porra.seen)}: {describe_plays(porra.seen)}.")
    if len(porra.seen) < porra.plays:
        lines.append(f"-# 🪦 Se ha quedado sin saldo para otra de {format_amount(porra.stake)}.")
    lines.append(f"🏁 Ha salido **{porra.options[sp.winning]}**.")
    lines += ["", *option_lines(porra, bets, winning=porra.outcome), ""]
    if sp.refund:
        lines.append("🤷 **Nadie ha acertado:** se devuelve a cada uno lo suyo.")
    else:
        winners = sorted(
            ((user, paid) for user, paid in sp.payouts.items() if paid),
            key=lambda item: -item[1],
        )
        stakes = {bet.user_id: bet.stake for bet in bets}
        for user, paid in winners[:MAX_WINNER_LINES]:
            lines.append(
                f"🤑 <@{user}> se lleva **{format_amount(paid)}** "
                f"(+{format_amount(paid - stakes.get(user, 0))})"
            )
        if len(winners) > MAX_WINNER_LINES:
            lines.append(f"… y {len(winners) - MAX_WINNER_LINES} más.")
        lines.append(
            f"-# 🐶 {TAX_COLLECTOR} se lleva {format_amount(sp.tax)} de Impuesto sobre "
            "Actividades de Juego."
        )
        if sp.image:
            lines.append(
                f"-# 📸 <@{porra.subject_id}> cobra {format_amount(sp.image)} por derechos de "
                f"imagen; {TAX_COLLECTOR} le retiene {format_amount(payment.image_tax)} "
                f"({_IMAGE_TAX_PCT} %)."
            )
    return discord.Embed(
        title=f"🎫 {question(porra, subject_name)}",
        description="\n".join(lines),
        color=RESOLVED_COLOR,
    )


def void_embed(porra: Porra, bets: Sequence[Bet], subject_name: str) -> discord.Embed:
    """El panel de una porra anulada."""
    reason = porra.void_reason or VoidReason.CANCELLED
    lines = [
        deal_line(porra),
        "",
        f"🚫 **Anulada.** {VOID_TEXT[reason]}".replace("{who}", f"<@{porra.subject_id}>"),
    ]
    if bets:
        lines.append(f"{CURRENCY_EMOJI} Se ha devuelto todo lo apostado, sin comisiones.")
    return discord.Embed(
        title=f"🎫 {question(porra, subject_name)}", description="\n".join(lines), color=VOID_COLOR
    )


# -- Botones -----------------------------------------------------------------------------


class BetModal(discord.ui.Modal, title="🎫 Apostar en la porra"):
    """Pide cuánto apostar a una opción."""

    amount: discord.ui.TextInput = discord.ui.TextInput(
        label="¿Cuánto?", placeholder="500, 2k, mitad, all…", max_length=20
    )

    def __init__(self, cog: Porras, table: Table, outcome: int) -> None:
        super().__init__()
        self.cog = cog
        self.table = table
        self.outcome = outcome
        self.title = f"🎫 {table.porra.options[outcome]}"[:45]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Hace la apuesta."""
        await self.cog.bet(interaction, self.table, self.outcome, str(self.amount.value))


class PorraView(discord.ui.View):
    """Botones del panel según la fase de la porra."""

    def __init__(self, cog: Porras, table: Table) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.table = table
        porra = table.porra
        if porra.status is Status.PROPOSED:
            self._add("✅ Acepto", discord.ButtonStyle.success, self._accept, "aceptar")
            self._add("❌ Paso", discord.ButtonStyle.danger, self._decline, "pasar")
            self._add("🗑️ Retirar", discord.ButtonStyle.secondary, self._cancel, "retirar")
        elif porra.status is Status.OPEN:
            for index, option in enumerate(porra.options[:MAX_OPTION_BUTTONS]):
                self._add(
                    option[:80],
                    discord.ButtonStyle.primary,
                    self._bet_callback(index),
                    f"apostar:{index}",
                )
            self._add("🔭", discord.ButtonStyle.secondary, self._snoop, "espiar")
        elif porra.status is Status.LOCKED:
            self._add("🔭", discord.ButtonStyle.secondary, self._snoop, "espiar")

    def _add(
        self,
        label: str,
        style: discord.ButtonStyle,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        custom_id: str,
    ) -> None:
        button: discord.ui.Button = discord.ui.Button(
            label=label, style=style, custom_id=f"porra:{custom_id}"
        )
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)

    def _bet_callback(self, outcome: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await self.cog.ask_bet(interaction, self.table, outcome)

        return callback

    async def _accept(self, interaction: discord.Interaction) -> None:
        await self.cog.answer(interaction, self.table, accepted=True)

    async def _decline(self, interaction: discord.Interaction) -> None:
        await self.cog.answer(interaction, self.table, accepted=False)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await self.cog.cancel(interaction, self.table)

    async def _snoop(self, interaction: discord.Interaction) -> None:
        await self.cog.snoop(interaction, self.table)


# -- Estado en marcha --------------------------------------------------------------------


@dataclass(slots=True)
class Table:
    """Una porra en marcha y lo que el cog necesita para llevarla.

    Attributes:
        until: Epoch en que acaba la fase actual (aceptar, apostar o jugar).
        timer: Tarea que actúa al acabar la fase.
        refresh: Tarea pendiente de repintar el panel.
    """

    porra: Porra
    message: discord.Message | None = None
    channel: discord.abc.Messageable | None = None
    until: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    timer: asyncio.Task[None] | None = None
    refresh: asyncio.Task[None] | None = None


class Porras(commands.Cog):
    """Monta las porras, cobra las apuestas, cuenta las jugadas y reparte."""

    def __init__(
        self,
        bot: commands.Bot,
        economy: EconomyService,
        repository: PorraRepository,
        *,
        casino_channel_ids: CasinoChannelIds = frozenset(),
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.repository = repository
        self.casino_channel_ids = casino_channel_ids
        self.clock = clock
        self.sleep = sleep
        #: Porras en marcha, por id.
        self.tables: dict[int, Table] = {}
        #: Tareas sueltas (resoluciones, avisos tras reiniciar) para cancelarlas al cerrar.
        self._tasks: set[asyncio.Task[Any]] = set()

    async def cog_load(self) -> None:
        """Anula lo que quedó a medias si el bot se cayó (devuelve el dinero)."""
        await self.recover()

    async def cog_unload(self) -> None:
        """Anula las porras en marcha y devuelve lo apostado (apagado ordenado)."""
        for table in list(self.tables.values()):
            if table.timer is not None:
                table.timer.cancel()
            if table.refresh is not None:
                table.refresh.cancel()
            try:
                async with table.lock:
                    await self._void_locked(table, VoidReason.RESTART)
            except Exception:
                logger.exception("No se pudo anular la porra %s al cerrar", table.porra.id)
        for task in list(self._tasks):
            task.cancel()

    # -- Utilidades ------------------------------------------------------------------

    def _spawn(self, coro: Awaitable[Any]) -> asyncio.Task[Any]:
        """Lanza una tarea suelta que se cancela al cerrar y registra si falla."""
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)

        def done(finished: asyncio.Task[Any]) -> None:
            self._tasks.discard(finished)
            if not finished.cancelled() and (error := finished.exception()) is not None:
                logger.error("Fallo en una tarea de las porras", exc_info=error)

        task.add_done_callback(done)
        return task

    def _schedule(
        self, table: Table, delay: float, action: Callable[[Table], Awaitable[None]]
    ) -> None:
        """Programa lo que pasa al acabar la fase actual y cancela lo anterior."""
        current = asyncio.current_task()
        if table.timer is not None and table.timer is not current:
            table.timer.cancel()
        table.until = self.clock() + delay

        async def run() -> None:
            await self.sleep(max(0.0, delay))
            try:
                await action(table)
            except Exception:
                logger.exception("Fallo en la porra %s", table.porra.id)

        table.timer = asyncio.ensure_future(run())

    def _member(self, guild_id: int, user_id: int) -> discord.abc.User | None:
        guild = self.bot.get_guild(guild_id)
        member = guild.get_member(user_id) if guild is not None else None
        return member or self.bot.get_user(user_id)

    def _name(self, guild_id: int, user_id: int) -> str:
        user = self._member(guild_id, user_id)
        name = getattr(user, "display_name", None) or f"<@{user_id}>"
        return discord.utils.escape_markdown(name)

    async def _bets(self, porra: Porra) -> list[Bet]:
        return merge_bets(await self.economy.porra_bets(porra.guild_id, porra.id))

    def subject_table(self, guild_id: int, user_id: int) -> Table | None:
        """La porra en marcha de la que `user_id` es protagonista, si hay."""
        for table in self.tables.values():
            porra = table.porra
            if porra.guild_id == guild_id and porra.subject_id == user_id:
                return table
        return None

    async def _repaint(self, table: Table, bets: list[Bet] | None = None) -> None:
        """Edita el panel con el estado actual."""
        if table.message is None:
            return
        porra = table.porra
        bets = await self._bets(porra) if bets is None else bets
        embed = panel_embed(
            porra, bets, self._name(porra.guild_id, porra.subject_id), until=table.until
        )
        try:
            await table.message.edit(embed=embed, view=PorraView(self, table))
        except discord.HTTPException:
            logger.warning("No se pudo repintar la porra %s", porra.id)

    def refresh_soon(self, table: Table) -> None:
        """Repinta el panel en `REFRESH_DELAY` segundos, juntando lo que llegue mientras."""
        if table.refresh is not None and not table.refresh.done():
            return

        async def run() -> None:
            await self.sleep(REFRESH_DELAY)
            async with table.lock:
                # Mientras esperaba el candado ha podido resolverse o anularse.
                if not table.porra.status.final:
                    await self._repaint(table)

        table.refresh = self._spawn(run())

    async def _send(self, table: Table, content: str, users: Sequence[int]) -> None:
        """Mensaje suelto en el canal de la porra, mencionando solo a `users`."""
        channel = table.channel or getattr(table.message, "channel", None)
        if channel is None:
            return
        mentions = discord.AllowedMentions(
            everyone=False, roles=False, users=[discord.Object(id=u) for u in users]
        )
        try:
            await channel.send(content, allowed_mentions=mentions)
        except discord.HTTPException:
            logger.warning("No se pudo avisar en la porra %s", table.porra.id)

    # -- Montar --------------------------------------------------------------------------

    async def open(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        opener: discord.abc.User,
        subject: discord.abc.User | None,
        game_text: str | None,
        prop_key: str | None,
        plays: int | None,
        stake_text: str | None,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> Porra | None:
        """Lógica compartida de `/porra` y `.porra`: valida, guarda y publica el panel.

        Returns:
            La porra montada, o `None` si no se pudo (ya se ha dicho por qué).
        """
        if guild is None:
            await send_error("Las porras solo van dentro de un servidor.")
            return None
        if error := casino_channel_error(self.casino_channel_ids, channel, "Las porras"):
            await send_error(error)
            return None
        if subject is None:
            # Sin avisar a nadie: el listado nombra a los protagonistas con menciones.
            await send(
                content=self.listing(guild.id),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return None
        if subject.bot:
            await send_error("A los bots no se les monta porras. Siempre ganan.")
            return None
        if subject.id == opener.id:
            await send_error(
                "No puedes montarte una porra a ti mismo: sería amañarla. Móntasela a otro."
            )
            return None
        who = discord.utils.escape_markdown(subject.display_name)
        game = resolve_game(game_text)
        if game is None:
            names = ", ".join(f"`{key}`" for key in allowed_games())
            await send_error(f"¿A qué juego? Vale cualquiera de estos: {names}.")
            return None
        owned = await shop.owned_keys(self.bot, guild.id, opener.id)
        limit = max_plays(owned)
        plays = 1 if plays is None else plays
        if not 1 <= plays <= limit:
            extra = "" if limit > 5 else " (con la 📓 Libreta de la porra, hasta 10)"
            await send_error(f"Las jugadas van de 1 a {limit}{extra}.")
            return None
        prop_key = (prop_key or "signo").lower()
        prop = PROPOSITION_BY_KEY.get(prop_key)
        if prop is None or not prop.fits(game, plays):
            options = ", ".join(f"`{p.key}`" for p in propositions_for(game, plays))
            await send_error(f"Esa propuesta no vale aquí. Con {game_label(game)}: {options}.")
            return None
        if self.subject_table(guild.id, subject.id) is not None:
            await send_error(f"**{who}** ya tiene una porra en marcha. Una cada vez.")
            return None
        balance = await self.economy.balance(guild.id, subject.id)
        try:
            stake = parse_amount(stake_text, balance) if stake_text else MIN_STAKE
        except ValueError as error:
            await send_error(str(error))
            return None
        if stake < MIN_STAKE:
            await send_error(
                f"La apuesta por jugada tiene que ser de {format_amount(MIN_STAKE)} o más."
            )
            return None
        if balance < stake:
            await send_error(
                f"A **{who}** no le llega para una jugada de {format_amount(stake)}: "
                f"tiene {format_amount(balance)}."
            )
            return None

        porra = Porra(
            id=0,
            guild_id=guild.id,
            channel_id=getattr(channel, "id", 0) or 0,
            opener_id=opener.id,
            subject_id=subject.id,
            game=game,
            proposition=prop.key,
            plays=plays,
            stake=stake,
            created_at=self.clock(),
        )
        await self.repository.create(porra)
        table = Table(
            porra=porra, channel=channel if isinstance(channel, discord.abc.Messageable) else None
        )
        self.tables[porra.id] = table
        self._schedule(table, ACCEPT_SECONDS, self._expire)
        embed = panel_embed(porra, [], who, until=table.until)
        try:
            table.message = await send(
                content=subject.mention,
                embed=embed,
                view=PorraView(self, table),
                allowed_mentions=discord.AllowedMentions(
                    everyone=False, roles=False, users=[subject]
                ),
            )
        except discord.HTTPException:
            logger.exception("No se pudo publicar la porra %s", porra.id)
            async with table.lock:
                await self._void_locked(table, VoidReason.CANCELLED)
            return None
        porra.message_id = getattr(table.message, "id", None)
        await self.repository.save(porra)
        await logros.track(
            self.bot,
            guild.id,
            opener,
            channel,
            porra_open_stats(game=game, proposition=prop.key, plays=plays),
        )
        return porra

    def listing(self, guild_id: int) -> str:
        """Las porras en marcha del servidor, para `porra` sin argumentos."""
        tables = [t for t in self.tables.values() if t.porra.guild_id == guild_id]
        lines = ["🎫 **Porras en marcha**"]
        if not tables:
            lines.append("Ninguna ahora mismo.")
        for table in tables:
            porra = table.porra
            phase = {
                Status.PROPOSED: "esperando respuesta",
                Status.OPEN: "apuestas abiertas",
                Status.LOCKED: f"en juego ({len(porra.seen)}/{porra.plays})",
            }.get(porra.status, porra.status.value)
            link = f" · [ver]({table.message.jump_url})" if table.message is not None else ""
            lines.append(
                f"• <@{porra.subject_id}> en {game_label(porra.game)}: {porra.prop.name} "
                f"· {phase}{link}"
            )
        lines.append(
            "-# Monta una con `/porra miembro juego [propuesta] [jugadas] [apuesta]`. "
            "Propuestas: " + ", ".join(f"`{key}`" for key in PROPOSITION_BY_KEY) + "."
        )
        return "\n".join(lines)

    # -- Propuesta -------------------------------------------------------------------

    async def answer(
        self, interaction: discord.Interaction, table: Table, *, accepted: bool
    ) -> None:
        """✅ o ❌ del protagonista."""
        porra = table.porra
        if interaction.user.id != porra.subject_id:
            await notify(
                interaction,
                "Solo el protagonista puede aceptar o rechazar su porra.",
            )
            return
        # Guardar y repintar van a la base de datos (y esperan al candado): se acepta antes.
        await ack(interaction)
        async with table.lock:
            if porra.status is not Status.PROPOSED:
                await notify(interaction, "Esa porra ya no espera respuesta.")
                return
            if accepted:
                balance = await self.economy.balance(porra.guild_id, porra.subject_id)
                if balance < porra.stake:
                    await notify(
                        interaction,
                        f"No te llega para jugar a {format_amount(porra.stake)}: tienes "
                        f"{format_amount(balance)}.",
                    )
                    return
                porra.status = Status.OPEN
                await self.repository.save(porra)
                self._schedule(table, BETTING_SECONDS, self.lock)
                embed = panel_embed(
                    porra, [], self._name(porra.guild_id, porra.subject_id), until=table.until
                )
                await edit(interaction, embed=embed, view=PorraView(self, table))
            else:
                await self._void_locked(table, VoidReason.DECLINED)
        own, opener_delta = porra_answer_stats(accepted=accepted)
        await logros.track(self.bot, porra.guild_id, interaction.user, interaction.channel, own)
        if opener_delta and (opener := self._member(porra.guild_id, porra.opener_id)):
            await logros.track(self.bot, porra.guild_id, opener, interaction.channel, opener_delta)

    async def cancel(self, interaction: discord.Interaction, table: Table) -> None:
        """🗑️ de quien la montó, antes de que el protagonista conteste."""
        porra = table.porra
        if interaction.user.id != porra.opener_id:
            await notify(interaction, "Solo quien la montó puede retirarla.")
            return
        await ack(interaction)
        async with table.lock:
            if porra.status is not Status.PROPOSED:
                await notify(
                    interaction,
                    "Ya no se puede retirar: el protagonista ha contestado.",
                )
                return
            await self._void_locked(table, VoidReason.CANCELLED)

    async def _expire(self, table: Table) -> None:
        """Se acabó el tiempo para aceptar."""
        async with table.lock:
            if table.porra.status is Status.PROPOSED:
                await self._void_locked(table, VoidReason.EXPIRED)

    # -- Apuestas --------------------------------------------------------------------

    async def ask_bet(self, interaction: discord.Interaction, table: Table, outcome: int) -> None:
        """Botón de una opción: comprueba que puede y le abre el formulario."""
        porra = table.porra
        if interaction.user.id == porra.subject_id:
            await interaction.response.send_message(
                "Tú juegas, no apuestas: así nadie se amaña su propia porra.", ephemeral=True
            )
            return
        if porra.status is not Status.OPEN:
            await interaction.response.send_message(
                "Las apuestas ya están cerradas.", ephemeral=True
            )
            return
        await interaction.response.send_modal(BetModal(self, table, outcome))

    async def bet(
        self, interaction: discord.Interaction, table: Table, outcome: int, text: str
    ) -> None:
        """Cobra la apuesta del formulario y la deja en el depósito de la porra."""
        porra = table.porra
        user = interaction.user
        # Cobrar va a la base de datos (y espera al candado de la porra): se acepta antes.
        await ack(interaction)
        async with table.lock:
            if porra.status is not Status.OPEN:
                await notify(interaction, "Las apuestas ya están cerradas.")
                return
            if user.id == porra.subject_id or user.bot:
                await notify(interaction, "Tú no puedes apostar aquí.")
                return
            balance = await self.economy.balance(porra.guild_id, user.id)
            try:
                stake = parse_amount(text, balance)
                if stake < MIN_BET:
                    raise ValueError(f"La apuesta mínima es de {format_amount(MIN_BET)}.")
                receipt = await self.economy.porra_bet(
                    porra.guild_id, porra.id, user.id, outcome=outcome, stake=stake, cap=porra.cap
                )
            except ValueError as error:
                await notify(interaction, str(error))
                return
            except InsufficientFundsError as error:
                await notify(
                    interaction,
                    f"No te llega: tienes {format_amount(error.balance)}.",
                )
                return
            except PorraSideError as error:
                await notify(
                    interaction,
                    f"Ya vas a **{porra.options[error.outcome]}**. Una opción por porra: puedes "
                    "subir lo que llevas ahí, pero no cubrirte.",
                )
                return
            except PorraCapError as error:
                await notify(
                    interaction,
                    f"No cabe: el bote no pasa de {format_amount(porra.cap)} (5 veces lo que se "
                    f"juega <@{porra.subject_id}>). Quedan {format_amount(error.room)}.",
                )
                return
            except PorraClosedError:
                await notify(interaction, "Esa porra ya está cerrada.")
                return
            bets = await self._bets(porra)
            totals = by_outcome(bets, len(porra.options))
            mult = payout_multiplier(totals, outcome)
            lines = [
                f"🎫 Apuestas **{format_amount(stake)}** a **{porra.options[outcome]}**"
                + (f" (llevas {format_amount(receipt.stake)})." if receipt.stake != stake else "."),
                f"Si acierta, ahora mismo pagaría {_multiplier(mult)}: unos "
                f"**{format_amount(int(receipt.stake * mult))}**. Cambia con cada apuesta."
                if mult
                else "",
                f"{CURRENCY_EMOJI} Te quedan **{format_amount(receipt.balance)}**",
                f"-# 🐶 Si la porra sale adelante, {TAX_COLLECTOR} se queda el {_IAJ_PCT} % de lo "
                "que apuestas, aciertes o no.",
            ]
            # Apostar es un resultado con dinero: la mascota activa puede opinar.
            if hint := await renta.hint(self.bot, porra.guild_id, user.id, Moment()):
                lines.append(hint)
            text = "\n".join(line for line in lines if line)
            await notify(interaction, text)
        self.refresh_soon(table)
        # Apostar es gastar: gancho de la Renta (ver Biblia.txt, sección 4).
        await renta.remind(self.bot, interaction)
        favourable = porra.prop.favourable
        await logros.track(
            self.bot,
            porra.guild_id,
            user,
            interaction.channel,
            porra_bet_stats(
                stake=stake,
                balance_before=receipt.balance + stake,
                opener_against=user.id == porra.opener_id
                and favourable is not None
                and outcome != favourable,
                when=datetime.fromtimestamp(self.clock(), TIMEZONE),
            ),
        )

    async def snoop(self, interaction: discord.Interaction, table: Table) -> None:
        """🔭: quién apuesta qué, para quien tenga los prismáticos."""
        porra = table.porra
        await ack(interaction, new_message=True)
        owned = await shop.owned_keys(self.bot, porra.guild_id, interaction.user.id)
        if BINOCULARS_KEY not in owned:
            await edit(
                interaction,
                content="🔭 Para ver quién apuesta qué necesitas los **Prismáticos de la UCO** "
                "(`tienda`, pasillo 🎫 Peña de la porra).",
            )
            return
        bets = await self._bets(porra)
        lines = [f"🔭 **Quién va a qué** en la porra de <@{porra.subject_id}>:"]
        for bet in sorted(bets, key=lambda b: (b.outcome, -b.stake)):
            lines.append(
                f"• <@{bet.user_id}>: {format_amount(bet.stake)} a {porra.options[bet.outcome]}"
            )
        if not bets:
            lines.append("Nadie todavía. Mucho mirar y poco apostar.")
        await edit(interaction, content="\n".join(lines))
        await logros.track(
            self.bot, porra.guild_id, interaction.user, interaction.channel, porra_snoop_stats()
        )

    # -- En juego --------------------------------------------------------------------

    async def lock(self, table: Table) -> None:
        """Cierra las apuestas. Sin dinero en dos opciones, se anula."""
        async with table.lock:
            porra = table.porra
            if porra.status is not Status.OPEN:
                return
            bets = await self._bets(porra)
            if is_one_sided(bets, len(porra.options)):
                await self._void_locked(table, VoidReason.ONE_SIDED, bets)
                return
            porra.status = Status.LOCKED
            porra.locked_at = self.clock()
            await self.repository.save(porra)
            self._schedule(table, play_deadline(porra.plays), self._no_show)
            await self._repaint(table, bets)
        await self._send(
            table,
            f"🎲 ¡No va más! <@{porra.subject_id}>, te toca: **{porra.plays}** "
            f"{_plays_word(porra.plays)} de {game_label(porra.game)} de al menos "
            f"**{format_amount(porra.stake)}** antes de <t:{int(table.until)}:R>. Hay "
            f"**{format_amount(sum(b.stake for b in bets))}** en juego.",
            [porra.subject_id],
        )

    async def observe(self, guild_id: int, user_id: int, play: Play) -> None:
        """Una jugada terminada de alguien: si es protagonista de una porra, cuenta."""
        table = self.subject_table(guild_id, user_id)
        if table is None or not table.porra.counts(play):
            return
        async with table.lock:
            if not table.porra.counts(play):
                return
            ready = table.porra.add(play)
        if ready:
            # En otra tarea: el juego que ha llamado no tiene que esperar al reparto.
            self._spawn(self.resolve(table))
        else:
            self.refresh_soon(table)

    async def _no_show(self, table: Table) -> None:
        """Se acabó el plazo para jugar: se anula y se devuelve todo."""
        async with table.lock:
            if table.porra.status is Status.LOCKED:
                await self._void_locked(table, VoidReason.NO_SHOW)

    # -- Final -----------------------------------------------------------------------

    async def resolve(self, table: Table) -> None:
        """Reparte el bote según lo que ha salido y lo cuenta."""
        porra = table.porra
        async with table.lock:
            if porra.status is not Status.LOCKED:
                return
            winning = porra.decide()
            bets = await self._bets(porra)
            sp = split(bets, winning)
            try:
                payment = await self.economy.settle_porra(
                    porra.guild_id,
                    porra.id,
                    payouts=sp.payouts,
                    taxes=sp.taxes,
                    refund=sp.refund,
                    image=sp.image,
                    subject_id=porra.subject_id,
                )
            except PorraClosedError:
                payment = PorraPayment(bets={})
            except BalanceLimitError:
                logger.warning(
                    "La porra %s pasaría el saldo máximo de alguien; se devuelve.", porra.id
                )
                await self._void_locked(table, VoidReason.RESTART, bets)
                return
            porra.status = Status.RESOLVED
            porra.outcome = winning
            if table.timer is not None and table.timer is not asyncio.current_task():
                table.timer.cancel()
            self.tables.pop(porra.id, None)
            await self.repository.save(porra, finished_at=self.clock())
            subject_name = self._name(porra.guild_id, porra.subject_id)
            if table.message is not None:
                try:
                    await table.message.edit(
                        embed=result_embed(porra, bets, subject_name, sp, payment), view=None
                    )
                except discord.HTTPException:
                    logger.warning("No se pudo enseñar el resultado de la porra %s", porra.id)
        winners = [user for user, paid in sp.payouts.items() if paid and not sp.refund]
        if winners:
            await self._send(
                table,
                f"🏁 Porra de <@{porra.subject_id}>: ha salido **{porra.options[winning]}**. "
                + " ".join(f"<@{u}>" for u in winners[:MAX_WINNER_LINES])
                + " ¡a cobrar!",
                winners[:MAX_WINNER_LINES],
            )
        elif sp.refund:
            await self._send(
                table,
                f"🏁 Porra de <@{porra.subject_id}>: ha salido **{porra.options[winning]}** y no "
                "lo había visto venir nadie. Se devuelve todo.",
                [],
            )
        await self._count(table, bets, sp, payment)

    async def _count(self, table: Table, bets: list[Bet], sp: Split, payment: PorraPayment) -> None:
        """Logros y estadísticas del casino de todos los que han participado."""
        porra = table.porra
        channel = table.channel or getattr(table.message, "channel", None)
        winners = [bet for bet in bets if bet.outcome == sp.winning]
        losers = len(bets) - len(winners)
        flipped = porra.flipped_at_the_end()
        for bet in bets:
            user = self._member(porra.guild_id, bet.user_id)
            if user is None:
                continue
            paid = sp.payouts.get(bet.user_id, 0)
            won = not sp.refund and paid > 0
            streak = 0
            if not sp.refund:
                try:
                    streak = await self.repository.bump_streak(porra.guild_id, bet.user_id, won=won)
                except Exception:
                    logger.exception("No se pudo guardar la racha de porras de %s", bet.user_id)
            delta = porra_bettor_stats(
                stake=bet.stake,
                payout=paid,
                refund=sp.refund,
                nobody=sp.refund,
                tax=sp.taxes.get(bet.user_id, 0),
                streak=streak,
                lone_wolf=won and len(winners) == 1 and losers >= 3,
                favourite_flop=not won and share_of_side(bets, bet.outcome) >= 0.75,
                loyal_loss=not won and bet.outcome == porra.prop.favourable,
                flipped=flipped,
                crowd=len(bets),
            )
            settlement = payment.bets.get(bet.user_id)
            if sp.refund or settlement is None:
                await logros.track(self.bot, porra.guild_id, user, channel, delta)
                continue
            net = paid - bet.stake
            delta.merge(
                casino_stats(
                    stake=bet.stake,
                    net=net,
                    balance_after=settlement.balance,
                    tax_delta=settlement.tax_delta,
                )
            )
            await logros.casino_play(self.bot, porra.guild_id, user, channel, delta, net=net)
            await apuestas.record(
                self.bot,
                porra.guild_id,
                user,
                game=GAME,
                stake=bet.stake,
                net=net,
                balance_after=settlement.balance,
                tax=settlement.tax_delta,
            )
        subject = self._member(porra.guild_id, porra.subject_id)
        if subject is not None:
            favourable = porra.prop.favourable
            good = None if favourable is None else sp.winning == favourable
            await logros.track(
                self.bot,
                porra.guild_id,
                subject,
                channel,
                porra_subject_stats(
                    image=sp.image,
                    pool=sum(bet.stake for bet in bets),
                    favourable=good,
                    heroic=bool(good) and len(bets) >= 3 and not winners,
                    broke=len(porra.seen) < porra.plays,
                    crowd=len(bets),
                ),
            )

    async def _void_locked(
        self, table: Table, reason: VoidReason, bets: list[Bet] | None = None
    ) -> None:
        """Anula la porra y devuelve lo apostado. Hay que tener `table.lock`."""
        porra = table.porra
        if porra.status.final:
            return
        bets = await self._bets(porra) if bets is None else bets
        await self._refund(porra, bets)
        porra.status = Status.VOID
        porra.void_reason = reason
        if table.timer is not None and table.timer is not asyncio.current_task():
            table.timer.cancel()
        self.tables.pop(porra.id, None)
        await self.repository.save(porra, finished_at=self.clock())
        if table.message is not None:
            try:
                await table.message.edit(
                    embed=void_embed(porra, bets, self._name(porra.guild_id, porra.subject_id)),
                    view=None,
                )
            except discord.HTTPException:
                logger.warning("No se pudo enseñar la anulación de la porra %s", porra.id)
        channel = table.channel or getattr(table.message, "channel", None)
        for bet in bets:
            if user := self._member(porra.guild_id, bet.user_id):
                await logros.track(
                    self.bot,
                    porra.guild_id,
                    user,
                    channel,
                    porra_bettor_stats(
                        stake=bet.stake,
                        payout=bet.stake,
                        refund=True,
                        nobody=False,
                        tax=0,
                        streak=0,
                        lone_wolf=False,
                        favourite_flop=False,
                        loyal_loss=False,
                        flipped=False,
                        crowd=len(bets),
                    ),
                )
        if reason is VoidReason.NO_SHOW and (
            subject := self._member(porra.guild_id, porra.subject_id)
        ):
            await logros.track(self.bot, porra.guild_id, subject, channel, porra_no_show_stats())

    async def _refund(self, porra: Porra, bets: list[Bet]) -> None:
        """Devuelve el depósito de una porra (si había algo y no estaba liquidada)."""
        if not bets:
            return
        sp = refund_split(bets)
        try:
            await self.economy.settle_porra(
                porra.guild_id,
                porra.id,
                payouts=sp.payouts,
                taxes={},
                refund=True,
                image=0,
                subject_id=porra.subject_id,
            )
        except PorraClosedError:
            pass

    async def recover(self) -> None:
        """Al arrancar: las porras que quedaron a medias se anulan y se devuelven."""
        try:
            pending = await self.repository.unfinished()
        except Exception:
            logger.exception("No se pudieron leer las porras pendientes")
            return
        for porra in pending:
            try:
                bets = merge_bets(await self.economy.porra_bets(porra.guild_id, porra.id))
                await self._refund(porra, bets)
                porra.status = Status.VOID
                porra.void_reason = VoidReason.RESTART
                await self.repository.save(porra, finished_at=self.clock())
            except Exception:
                logger.exception("No se pudo anular la porra %s tras el reinicio", porra.id)
                continue
            if porra.message_id:
                self._spawn(self._mark_void_message(porra, bets))

    async def _mark_void_message(self, porra: Porra, bets: list[Bet]) -> None:
        """Cuando el bot esté listo, quita los botones del panel de una porra anulada."""
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(porra.channel_id)
        if not isinstance(channel, discord.abc.Messageable) or porra.message_id is None:
            return
        try:
            message = await channel.fetch_message(porra.message_id)
            await message.edit(
                embed=void_embed(porra, bets, self._name(porra.guild_id, porra.subject_id)),
                view=None,
            )
        except discord.HTTPException:
            logger.info("No se pudo limpiar el panel de la porra %s", porra.id)

    # -- Comandos --------------------------------------------------------------------

    @app_commands.command(
        name="porra", description="Monta una porra sobre las próximas jugadas de otro miembro."
    )
    @app_commands.describe(
        miembro="Sobre quién (no puedes ser tú). Sin nadie, ves las porras en marcha.",
        juego="A qué juego tiene que jugar.",
        propuesta="Qué se apuesta (por defecto, si acaba ganando).",
        jugadas="Cuántas jugadas cuentan (por defecto 1; hasta 5, o 10 con la libreta).",
        apuesta="Lo mínimo que tiene que apostar en cada una (por defecto 100).",
    )
    @app_commands.choices(
        juego=[
            app_commands.Choice(name=f"{emoji} {name}", value=key)
            for key, (emoji, name) in allowed_games().items()
        ]
    )
    @app_commands.guild_only()
    async def porra(
        self,
        interaction: discord.Interaction,
        miembro: discord.Member | None = None,
        juego: str | None = None,
        propuesta: str | None = None,
        jugadas: app_commands.Range[int, 1, MAX_PLAYS_NOTEBOOK] | None = None,
        apuesta: str | None = None,
    ) -> None:
        """Monta una porra pública. Quien la protagoniza tiene que aceptarla."""

        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            sent = await interaction.original_response()
            # El mensaje de una interacción solo se puede editar con su token durante
            # 15 minutos, y una porra dura más: se edita como mensaje normal del canal.
            channel = interaction.channel
            if isinstance(channel, discord.abc.Messageable):
                try:
                    return await channel.fetch_message(sent.id)
                except discord.HTTPException:
                    pass
            return sent

        await self.open(
            guild=interaction.guild,
            channel=interaction.channel,
            opener=interaction.user,
            subject=miembro,
            game_text=juego,
            prop_key=propuesta,
            plays=jugadas,
            stake_text=apuesta,
            send=send,
            send_error=InteractionResponder(interaction).send_error,
        )

    @porra.autocomplete("propuesta")
    async def _propositions(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocompletado de `propuesta`: las que valen para el juego elegido."""
        game = resolve_game(getattr(interaction.namespace, "juego", None))
        plays = getattr(interaction.namespace, "jugadas", None) or 1
        props = propositions_for(game, plays) if game else list(PROPOSITION_BY_KEY.values())
        text = current.lower()
        return [
            app_commands.Choice(name=f"{p.emoji} {p.name}", value=p.key)
            for p in props
            if text in p.key or text in p.name.lower()
        ][:25]

    @commands.command(name="porra")
    @commands.guild_only()
    async def porra_text(
        self,
        ctx: commands.Context,
        miembro: discord.Member | None = None,
        juego: str | None = None,
        *resto: str,
    ) -> None:
        """Versión de texto: `.porra @ana minas`, `.porra @ana minas mina 3 500`."""
        prop, plays, stake = parse_text_args(resto)

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        await self.open(
            guild=ctx.guild,
            channel=ctx.channel,
            opener=ctx.author,
            subject=miembro,
            game_text=juego,
            prop_key=prop,
            plays=plays,
            stake_text=stake,
            send=send,
            send_error=ContextResponder(ctx).send_error,
        )


async def observe(bot: commands.Bot, guild_id: int, user_id: int, play: Play) -> None:
    """Puente para `apuestas.record`: avisa de una jugada terminada. Nunca lanza."""
    if (cog := find_cog(bot, Porras)) is None:
        return
    try:
        await cog.observe(guild_id, user_id, play)
    except Exception:
        logger.exception("No se pudo contar la jugada de %s para su porra", user_id)


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía y el repositorio de porras del bot."""
    await bot.add_cog(Porras(bot, bot.economy, bot.porras, casino_channel_ids=bot.casino_channels))
