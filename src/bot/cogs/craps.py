"""Dados: `dados`, el craps del Casino del Estado con Pase, No pase y Odds.

Cada jugador abre su propia mesa, que solo él toca. `dados 500` deja la
apuesta en 500 Y$ y espera a que se elija: ✅ **Pase** o 🚫 **No pase**
cobran la apuesta y tiran la salida. Con Pase, el 7 y el 11 ganan y el 2, el
3 y el 12 pierden; con No pase, al revés (el 12 empata). Cualquier otro total
es el **punto**: entonces 🎲 **Tirar** sigue hasta que sale el punto (gana
Pase) o un 7 (gana No pase). Con el punto puesto, ➕ **Odds** pone otra
apuesta igual encima, hasta tres veces la apuesta, que paga lo justo y no
tiene ventaja para la casa (ver `bot.services.craps`). `dados 500 pase` tira
la salida directamente.

La mesa lleva la **mano** del tirador: dura hasta el siete fuera aunque se
ganen o pierdan partidas por el camino, y sus puntos son la mano caliente de
los logros. Se anuncian en el canal las manos de 5 puntos o más.

Cada tirada es un GIF: los dados cruzan el tapete, chocan contra la pared
de pirámides, ruedan y se paran. Después el bot cambia el GIF por un PNG con
el resultado y vuelve a activar los botones. El dibujo lo hace
`bot.services.craps_scene` con canvas, con Pillow de reserva.

Para que el clic se note al instante, la mesa sortea los dados de la próxima
tirada por adelantado (`_dice`, sin enseñarlos) y pinta su GIF en segundo plano
mientras se ve el actual: uno con el punto puesto (🎲 Tirar) y dos en la salida
(✅ Pase y 🚫 No pase). Las Odds cambian las fichas que se ven, así que ponerlas
lo vuelve a pintar. Al pulsar solo queda subir el GIF.

Dinero: juego. La apuesta se cobra al tirar la salida y las Odds al
ponerlas (`place_bet`); se paga al decidirse la partida (`pay_winnings`, con
0 si se pierde), que es cuando se ajusta el IRPF del día (art. 33 LIRPF:
los premios del juego son ganancia patrimonial). Si la mesa caduca (3 min
sin tocarla) o el bot se apaga de forma ordenada con un punto puesto, el
bot tira solo hasta decidirla: la apuesta de Pase o No pase no se puede
retirar, igual que en un casino.

Si `CASINO_CHANNEL_IDS` está configurado, solo se juega en esos canales.
Permisos del bot en el canal: enviar mensajes, insertar enlaces y adjuntar
archivos.
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
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs.casino import casino_channel_error, insufficient_text
from bot.services.achievements import casino_stats, craps_stats
from bot.services.craps import (
    BAR,
    CRAPS,
    NATURALS,
    ODDS_MAX,
    TOTAL_NAMES,
    Bet,
    CrapsError,
    CrapsGame,
    Hand,
    Roll,
    Status,
    format_odds,
    milestone,
    odds_profit,
    parse_bet,
    point_chance,
    throw,
)
from bot.services.craps_render import OPENING_REST, DieRest, Media, Table
from bot.services.craps_scene import CrapsScene
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
from bot.services.pets import bet_moment
from bot.services.taxes import TAX_COLLECTOR
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

GAME = "dados"
DEFAULT_STAKE = 100
VIEW_TIMEOUT = 180
#: Desde estos puntos en una mano se anuncia en el canal.
SHOUT_POINTS = 5
#: Margen tras el GIF antes de poner el PNG: lo que tarda en llegar y empezar.
REVEAL_MARGIN_SECONDS = 0.5
GIF_NAME = "dados.gif"
PNG_NAME = "dados.png"

COLOR_IDLE = discord.Color.from_rgb(88, 101, 242)
COLOR_POINT = discord.Color.from_rgb(255, 196, 0)
COLOR_WON = discord.Color.from_rgb(60, 200, 110)
COLOR_LOST = discord.Color.from_rgb(80, 84, 92)
COLOR_PUSH = discord.Color.from_rgb(120, 150, 190)

# Textos en el tono de Jovani Vázquez.
WIN_LINES = ("¡Wepa!", "¡Eso es, mi amor!", "¡Dale!", "¡Qué suerte, bendito!", "¡A la saca!")
LOSE_LINES = ("¡Ay, bendito!", "¡Nooo!", "¡Se acabó lo que se daba!", "¡Qué mala pata!")
FLYING_LINES = (
    "🎲 ¡Los dados vuelan!",
    "🎲 Sopla los dados, que da suerte…",
    "🙏 Que salga, que salga…",
    "👀 Toda la mesa mira los dados…",
    "🎲 ¡Contra la pared!",
)
NOTHING_LINES = (
    "Nada: sigue tirando.",
    "Ni chicha ni limoná: otra.",
    "No decide nada. Los dados son tuyos.",
)


def percent(chance: float) -> str:
    """`0,4545` → `45,5 %`."""
    return f"{chance * 100:.1f}".replace(".", ",").replace(",0", "") + " %"


def roll_text(roll: Roll) -> str:
    """`**6** (3-3)`: el total y los dados."""
    return f"**{roll.total}** ({roll.dice[0]}-{roll.dice[1]})"


def outcome_line(game: CrapsGame) -> str:
    """Qué ha decidido la última tirada, en una frase."""
    roll = game.last
    assert roll is not None
    total = roll.total
    name = TOTAL_NAMES[total]
    if roll.come_out and total in NATURALS:
        return f"Sale {roll_text(roll)}: **natural** en la salida. {name}."
    if roll.come_out and total == BAR and game.bet is Bet.DONT:
        return f"Sale {roll_text(roll)}: el 12 es **la barra**, empate con No pase."
    if roll.come_out and total in CRAPS:
        return f"Sale {roll_text(roll)}: **pifia** en la salida. {name}."
    if roll.made:
        hard = " ¡Por las malas!" if roll.hard else ""
        return f"Sale {roll_text(roll)}: **¡punto hecho!**{hard}"
    if roll.seven_out:
        return f"Sale {roll_text(roll)}: **siete fuera**. Se acaba la mano."
    return f"Sale {roll_text(roll)}."


class CrapsView(ui.View):
    """Mesa de un jugador: imagen, texto y botones.

    Guarda la partida en curso (o la última), la mano del tirador, la apuesta
    y dónde quedaron los dados (para que el PNG siguiente los deje ahí). No
    guarda dinero: se cobra y se paga siempre por la economía.
    """

    def __init__(
        self, cog: Craps, *, guild_id: int, owner: discord.abc.User, stake: int, bet: Bet
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        self.stake = stake
        self.bet = bet
        self.game: CrapsGame | None = None
        self.hand = Hand()
        self.history: list[tuple[Roll, Bet]] = []
        self.rest: tuple[DieRest, DieRest] = OPENING_REST
        self.balance = 0
        self.note: str | None = None
        self.message: discord.Message | None = None
        self.channel: object = None
        self.started_at = 0.0
        self._busy = False
        self._lock = asyncio.Lock()
        self._last_interaction: discord.Interaction | None = None
        #: Dados de la próxima tirada y semilla de su vuelo, sorteados por adelantado.
        self._dice: tuple[int, int] | None = None
        self._seed: int | None = None
        #: GIF pintados por adelantado: para qué estado valen y uno por botón que tira
        #: (`None` es 🎲 Tirar con el punto puesto; Pase o No pase, en la salida).
        self._plan: (
            tuple[tuple[Any, ...], dict[Bet | None, asyncio.Future[Media | None]]] | None
        ) = None
        self._painting: asyncio.Task[None] | None = None

    def table(self) -> Table:
        """Lo que se dibuja: partida, mano, apuesta e historial de la mano."""
        return Table(self.game, self.hand, self.stake, self.bet, tuple(self.history))

    @property
    def point_on(self) -> bool:
        """Si hay una partida con el punto puesto (no se puede cambiar la apuesta)."""
        return self.game is not None and self.game.status is Status.POINT

    # -- Texto ------------------------------------------------------------------------

    def description(self) -> str:
        """Texto del embed según cómo va la partida."""
        game = self.game
        lines: list[str] = []
        if game is None:
            lines.append("# 🎲 ¿Pase o no pase?")
            lines.append(
                "✅ **Pase**: el 7 y el 11 ganan; el 2, el 3 y el 12 pierden. Lo demás es "
                "el **punto**: hay que repetirlo antes que un 7.\n"
                "🚫 **No pase**: lo contrario, y el 12 empata.\n"
                f"-# Con el punto puesto, las Odds pagan lo justo: hasta ×{ODDS_MAX} la apuesta "
                f"y sin ventaja para {TAX_COLLECTOR}."
            )
        elif game.status is Status.POINT:
            point = game.point
            assert point is not None
            lines.append(f"# 🎯 Punto: {point}")
            roll = game.last
            assert roll is not None
            if roll.come_out:
                lines.append(f"Sale {roll_text(roll)} y se convierte en el punto.")
            else:
                lines.append(f"Sale {roll_text(roll)}. {random.choice(NOTHING_LINES)}")
            chance = point_chance(point)
            if game.bet is Bet.DONT:
                chance = 1 - chance
                lines.append(f"Ahora te vale un **7** antes que el {point}.")
            else:
                lines.append(f"Ahora hay que repetir el **{point}** antes que un 7.")
            odds = (
                f" · Odds {format_amount(game.odds)} a {format_odds(game.bet, point)}"
                if game.odds
                else f" · ➕ Odds pagan {format_odds(game.bet, point)}"
            )
            lines.append(
                f"-# {percent(float(chance))} de ganar · cobras {format_amount(game.potential())}"
                f"{odds}"
            )
        elif game.status is Status.WON:
            lines.append(f"# 💰 {random.choice(WIN_LINES)} +{format_amount(game.net)}")
            lines.append(outcome_line(game))
            if game.odds and game.point is not None:
                profit = odds_profit(game.bet, game.point, game.odds)
                lines.append(
                    f"Las Odds pagan {format_odds(game.bet, game.point)}: +{format_amount(profit)}."
                )
        elif game.status is Status.PUSH:
            lines.append(f"# 🤝 Empate · te devuelven {format_amount(game.payout)}")
            lines.append(outcome_line(game))
        else:
            lines.append(f"# 💥 {random.choice(LOSE_LINES)} -{format_amount(game.wagered)}")
            lines.append(outcome_line(game))
        if game is not None and game.last is not None and game.last.made:
            if cheer := milestone(len(self.hand.points)):
                lines.append(cheer)
        if self.note:
            lines.append(self.note)
        lines.append(self.footer())
        if self.balance == 0 and not self.point_on:
            lines.append("**Estás a cero.** `imv` te recarga.")
        return "\n".join(lines)

    def footer(self) -> str:
        """Línea pequeña: apuesta, mano y saldo."""
        game = self.game
        stake = game.stake if game is not None and game.playing else self.stake
        hand = ""
        if self.hand.rolls:
            points = len(self.hand.points)
            rolls = self.hand.rolls
            hand = (
                f" · Mano: {rolls} tirada{'s' if rolls != 1 else ''}, "
                f"{points} punto{'s' if points != 1 else ''}"
            )
        return f"-# Apuesta {format_amount(stake)}{hand} · Saldo {format_amount(self.balance)}"

    def color(self) -> discord.Color:
        game = self.game
        if game is None or game.status is Status.COME_OUT:
            return COLOR_IDLE
        return {
            Status.POINT: COLOR_POINT,
            Status.WON: COLOR_WON,
            Status.LOST: COLOR_LOST,
            Status.PUSH: COLOR_PUSH,
        }[game.status]

    def embed(
        self, *, image: str = PNG_NAME, text: str | None = None, color: discord.Color | None = None
    ) -> discord.Embed:
        """Embed con la imagen adjunta (`attachment://`)."""
        embed = discord.Embed(
            title=f"🎲 Dados · {self.owner.display_name}",
            description=text if text is not None else self.description(),
            color=color or self.color(),
        )
        embed.set_image(url=f"attachment://{image}")
        return embed

    def flying_text(self) -> str:
        """Texto mientras se ve el GIF, preparado ANTES de tirar: no delata nada."""
        game = self.game
        lines = [f"# {random.choice(FLYING_LINES)}"]
        bet = game.bet if game is not None else self.bet
        if game is not None and game.status is Status.POINT:
            lines.append(f"{bet.emoji} {bet.label} · a por el **{game.point}**")
        else:
            lines.append(f"{bet.emoji} {bet.label} · tirada de salida")
        lines.append(self.footer())
        return "\n".join(lines)

    # -- Componentes ------------------------------------------------------------------

    def rebuild(self, *, busy: bool = False) -> None:
        """Vuelve a montar los botones con el estado actual."""
        self.clear_items()
        game = self.game
        if self.point_on:
            assert game is not None and game.point is not None
            self._button(
                "🎲 Tirar", "roll", self._roll, style=discord.ButtonStyle.primary, row=0,
                disabled=busy,
            )  # fmt: skip
            room = game.odds_room
            step = min(game.stake, room)
            pays = format_odds(game.bet, game.point)
            self._button(
                f"➕ Odds {format_amount(step or game.stake)} · {pays}",
                "odds",
                self._odds_one,
                style=discord.ButtonStyle.success,
                row=0,
                disabled=busy or room <= 0,
            )
            self._button(
                "⏫ Odds al máximo",
                "oddsmax",
                self._odds_max,
                row=0,
                disabled=busy or room <= 0,
            )
            return
        for bet in Bet:
            self._button(
                f"{bet.emoji} {bet.label} · {format_amount(self.stake)}",
                bet.key,
                self._pass if bet is Bet.PASS else self._dont,
                style=discord.ButtonStyle.success
                if bet is Bet.PASS
                else discord.ButtonStyle.danger,
                row=0,
                disabled=busy,
            )
        self._button("½", "half", self._halve, row=1, disabled=busy)
        self._button("×2", "x2", self._double, row=1, disabled=busy)
        self._button("💰 All-in", "allin", self._all_in, row=1, disabled=busy)

    def _button(
        self,
        label: str,
        custom_id: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        row: int,
        disabled: bool = False,
    ) -> None:
        button: ui.Button = ui.Button(
            label=label, style=style, disabled=disabled, custom_id=f"{GAME}:{custom_id}", row=row
        )
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)

    def disable_all(self) -> None:
        """Apaga todos los botones (mesa caducada)."""
        for item in self.children:
            if isinstance(item, ui.Button):
                item.disabled = True

    # -- Ciclo de vida ----------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño juega en su mesa."""
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message(
            f"Estos dados son de {self.owner.display_name}. Saca los tuyos con `dados`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Caduca: si había un punto puesto, el bot tira solo hasta decidirlo."""
        self.cog.views.discard(self)
        forced = await self.force_settle()
        self.rebuild()
        self.disable_all()
        kwargs: dict[str, Any] = {"embed": self.embed(), "view": self}
        try:
            if forced:
                # Los dados de la imagen tienen que ser los de la tirada que decidió.
                png = await self.cog.renderer.board(self.table(), self.rest)
                kwargs["attachments"] = [discord.File(io.BytesIO(png), filename=PNG_NAME)]
            if self._last_interaction is not None:
                await self._last_interaction.edit_original_response(**kwargs)
            elif self.message is not None:
                await self.message.edit(**kwargs)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar la mesa de los dados", exc_info=True)

    async def force_settle(self) -> bool:
        """Decide la partida a medias tirando sin animación, y la paga y apunta.

        Returns:
            Si había una partida a medias (y por tanto la imagen ya no vale).
        """
        async with self._lock:
            game = self.game
            if game is None or not game.playing:
                return False
            while game.playing:
                self._observe(game.play(self.cog.rng), game)
            last = game.last
            assert last is not None
            # Mismo sitio en el tapete, con los valores de la última tirada.
            self.rest = (
                replace(self.rest[0], value=last.dice[0]),
                replace(self.rest[1], value=last.dice[1]),
            )
            settlement = await self._settle(game)
        await self._after_game(None, game, settlement)
        return True

    # -- GIF por adelantado -------------------------------------------------------------

    def _plan_key(self) -> tuple[Any, ...]:
        """Lo que decide el GIF de la próxima tirada, salvo el botón que se pulse."""
        game = self.game
        if game is not None and game.playing:
            match = (id(game), len(game.rolls), game.odds)
        else:
            match = (None, self.stake, self.hand.seven_out)
        return (*match, len(self.history), self._dice, self._seed)

    def _predict(self, bet: Bet | None) -> Table:
        """La mesa tal como quedará si se pulsa `bet` (sobre copias)."""
        assert self._dice is not None
        game, hand, history = deepcopy(self.game), deepcopy(self.hand), list(self.history)
        if game is None or not game.playing:
            assert bet is not None
            game = CrapsGame.new(self.stake, bet)
            if hand.seven_out:
                hand, history = Hand(), []
        roll = game.roll(self._dice)
        hand.observe(roll)
        history.append((roll, game.bet))
        return Table(game, hand, self.stake, game.bet, tuple(history))

    def prepare(self) -> None:
        """Pinta en segundo plano los GIF de la próxima tirada, si no lo están ya.

        Se llama en cuanto sale el GIF de una tirada (los dados de la siguiente se
        sortean ya, así que se pinta mientras se ve esta), al abrir la mesa, al
        poner Odds y al cambiar la apuesta. Los GIF se pintan uno detrás de otro y
        nunca se cancelan a medias: si mientras tanto la mesa cambia, el que aún no
        ha empezado no se pinta.
        """
        if not self.cog.ahead or self.is_finished():
            return
        if self._dice is None:
            self._dice = throw(self.cog.rng)
        if self._seed is None:
            self._seed = self.cog.rng.randrange(1, 2**31)
        key = self._plan_key()
        if self._plan is not None and self._plan[0] == key:
            return
        order: list[Bet | None] = (
            [None] if self.point_on else [self.bet, Bet.DONT if self.bet is Bet.PASS else Bet.PASS]
        )
        loop = asyncio.get_running_loop()
        futures: dict[Bet | None, asyncio.Future[Media | None]] = {
            choice: loop.create_future() for choice in order
        }
        self._plan = (key, futures)
        previous = self._painting
        self._painting = asyncio.create_task(self._paint_ahead(key, order, futures, previous))

    async def _paint_ahead(
        self,
        key: tuple[Any, ...],
        order: list[Bet | None],
        futures: dict[Bet | None, asyncio.Future[Media | None]],
        previous: asyncio.Task[None] | None,
    ) -> None:
        # Un plan detrás de otro: así un plan viejo nunca hace esperar dos dibujos.
        if previous is not None:
            await asyncio.gather(previous, return_exceptions=True)
        for choice in order:
            media: Media | None = None
            if self._plan_key() == key and not self.is_finished():
                try:
                    media = await self.cog.renderer.throw(self._predict(choice), seed=key[-1])
                except Exception:
                    logger.warning("No se pudieron pintar por adelantado los dados", exc_info=True)
            if not futures[choice].done():
                futures[choice].set_result(media)

    def _take_plan(self, bet: Bet | None) -> asyncio.Future[Media | None] | None:
        """El GIF por adelantado de pulsar `bet`, si vale para la mesa tal como está."""
        plan, self._plan = self._plan, None
        if plan is None or plan[0] != self._plan_key():
            return None
        return plan[1].get(None if self.point_on else bet)

    # -- Dinero y partida -------------------------------------------------------------

    def _observe(self, roll: Roll, game: CrapsGame) -> None:
        """Apunta la tirada en la mano y en el historial de la mano."""
        self.hand.observe(roll)
        self.history.append((roll, game.bet))

    async def _start(self, bet: Bet) -> str | None:
        """Cobra la apuesta y prepara una partida nueva (y una mano, si la otra acabó).

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
        # Para las porras: una partida empezada antes del cierre no cuenta.
        self.started_at = time.time()
        self.bet = bet
        self.game = CrapsGame.new(self.stake, bet)
        if self.hand.seven_out:
            self.hand = Hand()
            self.history = []
        self.note = None
        return None

    async def _settle(self, game: CrapsGame) -> BetSettlement | None:
        """Paga la partida terminada (0 si pierde) y prepara la nota fiscal y de la mascota."""
        try:
            settlement = await self.cog.economy.pay_winnings(
                self.guild_id, self.owner.id, game=GAME, amount=game.payout
            )
        except BalanceLimitError:
            logger.warning("Premio de los dados por encima del saldo máximo; no se paga.")
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
            bet_moment(stake=game.wagered, net=game.net, balance_after=settlement.balance),
        ):
            notes.append(hint)
        self.note = "\n".join(notes) or None
        return settlement

    async def _after_game(
        self,
        interaction: discord.Interaction | None,
        game: CrapsGame,
        settlement: BetSettlement | None,
    ) -> None:
        """Lo que va después de enseñar el final: renta, logros, estadísticas y anuncio."""
        if interaction is not None:
            await renta.remind(self.cog.bot, interaction)
        balance = settlement.balance if settlement else self.balance
        tax = settlement.tax_delta if settlement else 0
        delta = craps_stats(game, self.hand, when=datetime.now(TIMEZONE))
        delta.merge(
            casino_stats(stake=game.wagered, net=game.net, balance_after=balance, tax_delta=tax)
        )
        await logros.casino_play(
            self.cog.bot, self.guild_id, self.owner, self.channel, delta, net=game.net
        )
        last = game.last
        assert last is not None
        await apuestas.record(
            self.cog.bot,
            self.guild_id,
            self.owner,
            game=GAME,
            stake=game.wagered,
            net=game.net,
            balance_after=balance,
            tax=tax,
            details=(
                ("made", int(last.made)),
                ("sevenout", int(last.seven_out)),
                ("rolls", len(game.rolls)),
                ("started", int(self.started_at)),
            ),
        )
        await self.cog.shout(game, self.hand, self.owner, self.channel)

    async def _show_throw(
        self,
        editor: Callable[..., Awaitable[Any]],
        *,
        waiting: str,
        seed: int,
        ready: asyncio.Future[Media | None] | None = None,
    ) -> None:
        """Enseña el GIF de la tirada y después el PNG con el resultado.

        Si el GIF ya estaba pintado por adelantado (`ready`), va directo. Si no,
        mientras se pinta la mesa ya enseña la jugada con los botones apagados.
        Se apagan sin reconstruirlos: los nuevos (🎲 Tirar o Pase y No pase)
        dirían si la partida sigue antes de ver los dados.
        """
        for item in self.children:
            if isinstance(item, ui.Button):
                item.disabled = True

        async def show_waiting() -> None:
            try:
                await editor(embed=self.embed(text=waiting, color=COLOR_IDLE), view=self)
            except discord.HTTPException:
                logger.warning("No se pudo apagar la mesa de los dados", exc_info=True)

        async def paint() -> Media:
            media = await ready if ready is not None else None
            if media is None:
                media = await self.cog.renderer.throw(self.table(), seed=seed)
            return media

        media: Media
        if ready is not None and ready.done() and ready.result() is not None:
            media = await paint()
        else:
            _, media = await asyncio.gather(show_waiting(), paint())
        self.rest = media.rest
        await editor(
            # Color neutro: el del final delataría el resultado antes del GIF.
            embed=self.embed(image=GIF_NAME, text=waiting, color=COLOR_IDLE),
            attachments=[discord.File(io.BytesIO(media.gif), filename=GIF_NAME)],
            view=self,
        )
        # Los dados siguientes se sortean ya y se pintan mientras se ve esta tirada.
        self.prepare()
        await asyncio.sleep(media.seconds + REVEAL_MARGIN_SECONDS)
        self.rebuild()
        await editor(
            embed=self.embed(),
            attachments=[discord.File(io.BytesIO(media.png), filename=PNG_NAME)],
            view=self,
        )

    async def play(
        self,
        bet: Bet | None,
        editor: Callable[..., Awaitable[Any]],
        interaction: discord.Interaction | None = None,
    ) -> str | None:
        """Tira: empieza partida si no la hay (con `bet`), enseña el GIF y liquida.

        Args:
            bet: Pase o No pase para empezar; `None` para seguir con el punto.
            editor: Cómo se cambia el mensaje de la mesa (la interacción ya
                aceptada o el mensaje del comando de texto).
            interaction: La del botón, para el aviso de la Renta.

        Returns:
            Un error para el usuario si no se pudo tirar, o `None`.
        """
        if self._busy:
            return None
        self._busy = True
        settlement: BetSettlement | None = None
        game: CrapsGame | None = None
        try:
            async with self._lock:
                if (self.game is None or not self.game.playing) and bet is None:
                    return None
                ready = self._take_plan(bet)
                if self.game is None or not self.game.playing:
                    assert bet is not None
                    if error := await self._start(bet):
                        return error
                game = self.game
                assert game is not None
                waiting = self.flying_text()
                dice = self._dice if self._dice is not None else throw(self.cog.rng)
                seed = self._seed if self._seed is not None else self.cog.rng.randrange(1, 2**31)
                self._dice = self._seed = None
                try:
                    roll = game.roll(dice)
                except CrapsError:
                    return None
                self._observe(roll, game)
                if not game.playing:
                    settlement = await self._settle(game)
                else:
                    self.note = None
                if interaction is not None:
                    self._last_interaction = interaction
            await self._show_throw(editor, waiting=waiting, seed=seed, ready=ready)
        except discord.HTTPException:
            logger.warning("No se pudo enseñar una tirada de los dados", exc_info=True)
        finally:
            self._busy = False
        if game is not None and not game.playing:
            await self._after_game(interaction, game, settlement)
        elif interaction is not None and game is not None and len(game.rolls) == 1:
            # Salida que pone el punto: ya se ha cobrado la apuesta.
            await renta.remind(self.cog.bot, interaction)
        return None

    async def _start_with(self, interaction: discord.Interaction, bet: Bet | None) -> None:
        """✅ Pase, 🚫 No pase o 🎲 Tirar: tira (y empieza partida si hacía falta)."""
        # Cobrar la apuesta y dibujar van antes que nada: se acepta el clic ya.
        await ack(interaction)
        if self._busy:
            return

        async def editor(**kwargs: Any) -> None:
            await edit(interaction, **kwargs)

        if error := await self.play(bet, editor, interaction):
            await notify(interaction, error)

    async def _pass(self, interaction: discord.Interaction) -> None:
        await self._start_with(interaction, Bet.PASS)

    async def _dont(self, interaction: discord.Interaction) -> None:
        await self._start_with(interaction, Bet.DONT)

    async def _roll(self, interaction: discord.Interaction) -> None:
        await self._start_with(interaction, None)

    async def _add_odds(self, interaction: discord.Interaction, *, to_max: bool) -> None:
        """➕ Odds: cobra lo que se pone encima y repinta la mesa con las fichas nuevas."""
        await ack(interaction)
        game = self.game
        if self._busy or game is None or game.status is not Status.POINT:
            return
        self._busy = True
        try:
            async with self._lock:
                room = game.odds_room
                amount = min(game.stake, room)
                if to_max:
                    # Al tope o hasta donde llegue el saldo, como quien vacía la cartera.
                    self.balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
                    amount = min(room, self.balance)
                if amount <= 0:
                    if to_max:
                        await notify(interaction, insufficient_text(self.balance))
                    return
                try:
                    settlement = await self.cog.economy.place_bet(
                        self.guild_id, self.owner.id, game=GAME, stake=amount
                    )
                except InsufficientFundsError as error:
                    await notify(interaction, insufficient_text(error.balance, amount))
                    return
                self.balance = settlement.balance
                game.add_odds(amount)
                self.note = None
                self._last_interaction = interaction
            await self._board(interaction)
        finally:
            self._busy = False
        await renta.remind(self.cog.bot, interaction)

    async def _odds_one(self, interaction: discord.Interaction) -> None:
        await self._add_odds(interaction, to_max=False)

    async def _odds_max(self, interaction: discord.Interaction) -> None:
        await self._add_odds(interaction, to_max=True)

    async def _board(self, interaction: discord.Interaction) -> None:
        """Pone el PNG de la mesa quieta (sin animación)."""
        png = await self.cog.renderer.board(self.table(), self.rest)
        self.rebuild()
        await edit(
            interaction,
            embed=self.embed(),
            attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
            view=self,
        )
        self.prepare()

    def _idle(self) -> bool:
        return not self._busy and not self.point_on

    async def _restake(self, interaction: discord.Interaction, stake: int) -> None:
        """Cambia la apuesta y repinta la mesa con la ficha nueva."""
        self.stake = max(1, stake)
        self.note = None
        self._last_interaction = interaction
        if self.game is None:
            await self._board(interaction)
            return
        self.rebuild()
        await edit(interaction, embed=self.embed(), view=self)
        self.prepare()

    async def _halve(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        if not self._idle():
            return
        self.balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
        await self._restake(interaction, self.stake // 2)

    async def _double(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        if not self._idle():
            return
        self.balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
        await self._restake(interaction, min(self.stake * 2, max(1, self.balance)))

    async def _all_in(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        if not self._idle():
            return
        self.balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
        if self.balance <= 0:
            await notify(interaction, insufficient_text(0))
            return
        await self._restake(interaction, self.balance)


# -- Cog -------------------------------------------------------------------------------


class Craps(commands.Cog, name="Dados"):
    """Craps (Pase, No pase y Odds) con los yapdollars de la economía del bot."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        casino_channel_ids: frozenset[int] = frozenset(),
        rng: random.Random | None = None,
        renderer: CrapsScene | None = None,
        ahead: bool | None = None,
    ) -> None:
        """Prepara el cog; el dibujo por defecto es `CrapsScene`.

        Args:
            ahead: Si las mesas pintan por adelantado el GIF de la próxima tirada
                (`CrapsView.prepare`). Por defecto, solo con el dibujo de verdad: con
                un `renderer` de prueba, cada dibujo de más se contaría como jugada.
        """
        self.bot = bot
        self.economy = economy
        self.casino_channel_ids = casino_channel_ids
        # `secrets` usa el azar del sistema operativo: no se puede predecir.
        self.rng = rng or secrets.SystemRandom()
        self.ahead = renderer is None if ahead is None else ahead
        self.renderer = renderer or CrapsScene()
        # Mesas abiertas: para decidir sus partidas si el bot se apaga.
        self.views: set[CrapsView] = set()

    async def cog_unload(self) -> None:
        """Al apagar, decide las partidas con el punto puesto y cierra el navegador."""
        for view in list(self.views):
            try:
                await view.force_settle()
            except Exception:
                logger.exception("No se pudo cerrar una partida de dados al apagar")
            view.stop()
        self.views.clear()
        await self.renderer.close()

    async def shout(
        self, game: CrapsGame, hand: Hand, user: discord.abc.User, channel: object
    ) -> None:
        """Anuncia en el canal las manos calientes, para que se vean."""
        if not isinstance(channel, discord.abc.Messageable):
            return
        last = game.last
        points = len(hand.points)
        if last is None or not last.made or points < SHOUT_POINTS:
            return
        text = (
            f"📣 🎲 ¡{user.mention} lleva **{points} puntos** en la misma mano a los dados! "
            "La mesa está que arde."
        )
        try:
            await channel.send(
                text, allowed_mentions=discord.AllowedMentions(users=[user], everyone=False)
            )
        except discord.HTTPException:
            logger.debug("No se pudo anunciar una mano de los dados", exc_info=True)

    # -- dados ------------------------------------------------------------------------

    async def _dados_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        bet: Bet | None,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> None:
        """Lógica compartida de `/dados` y `.dados`: abre la mesa y, con apuesta, tira."""
        if guild is None:
            await send_error("Los dados solo se juegan dentro de un servidor.")
            return
        if error := casino_channel_error(self.casino_channel_ids, channel, "Los dados"):
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

        view = CrapsView(self, guild_id=guild.id, owner=user, stake=stake, bet=bet or Bet.PASS)
        view.channel = channel
        view.balance = balance
        png = await self.renderer.board(view.table(), view.rest)
        view.rebuild(busy=bet is not None)
        view.message = await send(
            embed=view.embed(), file=discord.File(io.BytesIO(png), filename=PNG_NAME), view=view
        )
        self.views.add(view)
        if bet is None:
            view.prepare()
        else:
            message = view.message

            async def editor(**kwargs: Any) -> None:
                await message.edit(**kwargs)

            if error := await view.play(bet, editor):
                view.rebuild()
                await message.edit(view=view)
                await send_error(error)

    @app_commands.command(
        name="dados",
        description="Craps: Pase o No pase con dos dados, el punto y Odds sin comisión.",
    )
    @app_commands.describe(
        cantidad="Apuesta: 500, 2k, all… (por defecto 100)",
        apuesta="Pase o No pase para tirar ya (si no, eliges con los botones)",
    )
    @app_commands.choices(
        apuesta=[app_commands.Choice(name=f"{b.emoji} {b.label}", value=b.key) for b in Bet]
    )
    @app_commands.guild_only()
    async def dados(
        self,
        interaction: discord.Interaction,
        cantidad: str | None = None,
        apuesta: str | None = None,
    ) -> None:
        """Abre una mesa de craps y, si se elige apuesta, tira la salida.

        Solo en los canales de `CASINO_CHANNEL_IDS` si está configurado. Cobra
        la apuesta al tirar la salida y las Odds al ponerlas; paga al decidirse.
        """
        responder = InteractionResponder(interaction)
        bet = parse_bet(apuesta) if apuesta else None

        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            return await interaction.original_response()

        await self._dados_impl(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            bet=bet,
            send=send,
            send_error=responder.send_error,
        )
        if bet is not None:
            await renta.remind(self.bot, interaction)

    @commands.command(name="dados")
    @commands.guild_only()
    async def dados_text(self, ctx: commands.Context, *args: str) -> None:
        """Versión de texto: `.dados`, `.dados 500`, `.dados 500 pase`, `.dados nopase`.

        La cantidad y la apuesta van en cualquier orden.
        """
        responder = ContextResponder(ctx)
        amount_text: str | None = None
        bet: Bet | None = None
        # «no pase» en dos palabras es una sola apuesta.
        words = " ".join(args).lower().replace("no pase", "nopase").split()
        for arg in words:
            try:
                bet = parse_bet(arg)
            except ValueError:
                if amount_text is not None:
                    await responder.send_error(f"No entiendo «{arg}». Ejemplo: `.dados 500 pase`.")
                    return
                amount_text = arg

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        await self._dados_impl(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            amount_text=amount_text,
            bet=bet,
            send=send,
            send_error=responder.send_error,
        )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(Craps(bot, economy=bot.economy, casino_channel_ids=bot.casino_channel_ids))
