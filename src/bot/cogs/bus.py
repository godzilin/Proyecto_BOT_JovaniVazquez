"""Autobús (Ride the Bus): `guagua`, cuatro cartas, cuatro preguntas y cobrar a tiempo.

Cada jugador abre su propia mesa, que solo él toca. `guagua 500` deja la
apuesta en 500 Y$ y espera a la primera elección: 🔴 **Rojo** o ⚫ **Negro**
cobran la apuesta y descubren la primera carta. Luego vienen ⬆️ mayor /
⬇️ menor / 🟰 igual, ↔️ dentro / 🔀 fuera / 🎯 poste y el palo. Tras cada
acierto se puede 💰 **cobrar** o seguir; si se falla, se pierde todo. Quien
acierta las cuatro puede jugarse lo ganado a 🔄 **la vuelta** (doble o nada).

**Las probabilidades se ven siempre.** Cada botón lleva su multiplicador y
su probabilidad, y el texto enseña qué se cobra con cada opción. La tabla de
pagos es fija (📋 Tabla) y cobrar en cualquier parada devuelve de media el
99 % (reglas en `bot.services.bus`).

**Precarga.** Las cinco cartas se sortean antes de jugar, así que la
animación de cada carta no depende de lo que se elija: solo de si se acierta.
La escena (`bot.services.bus_scene`) dibuja de una vez el GIF si aciertas y
el GIF si fallas. La primera mano se dibuja al abrir la mesa y la siguiente,
en cuanto se acierta la anterior, mientras se ve su GIF y el jugador piensa.
Al pulsar, el GIF ya está hecho y sale al momento. Se tira como mucho un
dibujo por partida (el de la mano a la que no se llega), y los pendientes se
cancelan al cerrar la mesa.

Dinero: la apuesta se cobra al pedir la primera carta (`place_bet`) y se
paga al cobrar o al fallar (`pay_winnings`, con 0 si se pierde), que es
cuando se ajusta el IRPF del día. Si la mesa caduca (3 min sin tocarla) o el
bot se apaga de forma ordenada con una partida a medias, se cobra sola:
nunca hay partida empezada sin un acierto.

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
from datetime import datetime
from fractions import Fraction
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs.casino import casino_channel_error, insufficient_text
from bot.services.achievements import bus_stats, casino_stats
from bot.services.blackjack import Card
from bot.services.bus import (
    HANDS,
    BusError,
    BusGame,
    Hand,
    Pick,
    Status,
    chance,
    deal,
    format_chance,
    format_multiplier,
    milestone,
    paytable,
)
from bot.services.bus_render import Banner, Board, Media, Reveal
from bot.services.bus_scene import BusScene
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
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

GAME = "autobus"
DEFAULT_STAKE = 100
VIEW_TIMEOUT = 180
#: Desde este multiplicador se anuncia el cobro en el canal.
SHOUT_MULTIPLIER = 50
#: Margen tras el GIF antes de poner el PNG: lo que tarda en llegar y empezar.
REVEAL_MARGIN_SECONDS = 0.5
GIF_NAME = "autobus.gif"
PNG_NAME = "autobus.png"

COLOR_IDLE = discord.Color.from_rgb(88, 101, 242)
COLOR_HOT = discord.Color.from_rgb(255, 196, 0)
COLOR_LOST = discord.Color.from_rgb(80, 84, 92)

# Textos en el tono de Jovani Vázquez.
WIN_LINES = ("¡Wepa!", "¡Eso es, mi amor!", "¡Acertaste!", "¡Dale!", "¡Siguiente parada!")
CASH_LINES = ("¡Wepa!", "¡Te bajas con la plata!", "¡Parada solicitada!", "¡A la saca!")
LOSE_LINES = ("¡Ay, bendito!", "¡Fin del trayecto!", "¡Se averió la guagua!", "¡Nooo!")
FLIPPING_LINES = (
    "🃏 La carta tiembla…",
    "🚌 El autobús frena…",
    "🙏 Que salga, que salga…",
    "😬 Se levanta la carta…",
    "👀 Todos miran la carta…",
)


def card_text(card: Card) -> str:
    """`7♥`, `A♠`: la carta en negrita para el texto."""
    return f"**{card}**"


class BusView(ui.View):
    """Mesa de un jugador: imagen, texto, botones y la precarga de los GIF.

    Guarda la partida en curso (o la última), la apuesta y las cartas de la
    partida siguiente (`deck`), que se sortean antes de jugar para dibujar su
    animación por adelantado. No guarda dinero: se cobra y se paga siempre
    por la economía.
    """

    def __init__(self, cog: Bus, *, guild_id: int, owner: discord.abc.User, stake: int) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        self.stake = stake
        self.game: BusGame | None = None
        self.balance = 0
        self.note: str | None = None
        self.message: discord.Message | None = None
        self.channel: object = None
        self.started_at = 0.0
        self._busy = False
        self._lock = asyncio.Lock()
        self._last_interaction: discord.Interaction | None = None
        #: Cartas de la partida que se está jugando o de la siguiente.
        self.deck: tuple[Card, ...] = ()
        self.seed = 0
        #: Animaciones que se dibujan por adelantado, por mano (1-5) de `deck`.
        self._reveals: dict[int, asyncio.Task[Reveal]] = {}
        self.new_deck()

    # -- Precarga ---------------------------------------------------------------------

    def new_deck(self) -> None:
        """Sortea las cartas de la próxima partida y empieza a dibujar su primera mano."""
        self.cancel_reveals()
        self.deck = deal(self.cog.rng)
        self.seed = self.cog.rng.randrange(1, 2**31)
        self.prepare(1)

    def prepare(self, hand: int) -> None:
        """Empieza a dibujar en segundo plano la mano `hand` de `deck`, si no se está haciendo."""
        if hand in self._reveals or not 1 <= hand <= len(self.deck):
            return
        self._reveals[hand] = asyncio.create_task(
            self.cog.renderer.reveal(self.deck, hand, seed=self.seed + hand)
        )

    async def reveal(self, hand: int) -> Reveal:
        """La animación de la mano `hand`: la ya dibujada o, si no se pidió, se dibuja ahora."""
        self.prepare(hand)
        return await self._reveals[hand]

    def cancel_reveals(self) -> None:
        """Cancela los dibujos pendientes (partida terminada o mesa cerrada)."""
        for task in self._reveals.values():
            task.cancel()
        self._reveals.clear()

    # -- Texto ------------------------------------------------------------------------

    def options_text(self, game: BusGame | None) -> list[str]:
        """Cada opción de la mano en juego con su probabilidad y lo que se cobraría."""
        lines = []
        stake = game.stake if game is not None else self.stake
        for option in self.options(game):
            pick = option.pick
            if option.multiplier is None:
                lines.append(f"-# {pick.emoji} {pick.label}: imposible con estas cartas")
                continue
            amount = format_amount(int(stake * option.multiplier))
            lines.append(
                f"{pick.emoji} **{pick.label}** · {format_chance(option.chance)} · "
                f"{format_multiplier(option.multiplier)} → {amount}"
            )
        return lines

    def options(self, game: BusGame | None) -> list:
        """Las opciones que se enseñan: las de la partida o, sin partida, las del color."""
        if game is not None and game.playing:
            return game.options()
        return BusGame(stake=max(1, self.stake), cards=self.deck).options()

    def description(self) -> str:
        """Texto del embed según cómo va la partida."""
        game = self.game
        lines: list[str] = []
        if game is None or not game.playing:
            lines.extend(self.result_lines(game))
            lines.append(f"## 🚌 {Hand.COLOR.title}")
            lines.extend(self.options_text(None))
        else:
            hand = game.hand
            assert hand is not None
            last = game.last
            assert last is not None
            lines.append(f"# {format_multiplier(game.multiplier)} · {format_amount(game.pot)}")
            stop = f"{card_text(last.card)}: **{random.choice(WIN_LINES)}**"
            stop += f" · 🚏 Parada {min(game.wins, HANDS)}/{HANDS}"
            lines.append(stop)
            if cheer := milestone(game):
                lines.append(cheer)
            lines.append(f"## {hand.title}")
            lines.extend(self.options_text(game))
        if self.note:
            lines.append(self.note)
        lines.append(self.footer())
        if self.balance == 0 and (game is None or not game.playing):
            lines.append("**Estás a cero.** `imv` te recarga.")
        return "\n".join(lines)

    def result_lines(self, game: BusGame | None) -> list[str]:
        """Cómo acabó la última partida (nada si no hubo)."""
        if game is None:
            return [
                "# 🚌 ¿Subes al autobús?",
                "Cuatro cartas, cuatro preguntas. Cada acierto multiplica; cobra cuando quieras "
                "o juégatelo todo.\n-# Cada opción enseña su probabilidad y lo que paga. "
                "Cobres donde cobres, la casa se queda el 1 %.",
            ]
        last = game.last
        assert last is not None
        if game.status is Status.CASHED:
            lines = [
                f"# 💰 {random.choice(CASH_LINES)} +{format_amount(game.net)}",
                f"Cobras **{format_amount(game.payout)}** en {format_multiplier(game.multiplier)} "
                f"tras {game.wins} parada{'s' if game.wins != 1 else ''}.",
            ]
            if cheer := milestone(game):
                lines.append(cheer)
            if near := self.near_miss(game):
                lines.append(near)
            return lines
        lines = [
            f"# 💥 {random.choice(LOSE_LINES)} -{format_amount(game.stake)}",
            f"Ha salido {card_text(last.card)} y pediste **{last.pick.label.lower()}** "
            f"({format_chance(last.chance)}).",
        ]
        if game.wins:
            lines.append(
                f"Llevabas {format_multiplier(game.multiplier)}: "
                f"te ibas a llevar {format_amount(game.pot)}."
            )
        return lines

    def near_miss(self, game: BusGame) -> str | None:
        """Al cobrar: qué carta venía y lo que habría pagado acertarla."""
        missed = game.missed()
        card = game.next_card
        if not missed or card is None:
            return None
        pick = missed[0]
        mult = game.multiplier / chance(pick, game.table)
        return (
            f"🔮 La siguiente era {card_text(card)}: con **{pick.label.lower()}** te ibas a "
            f"{format_multiplier(mult)} ({format_amount(int(game.stake * mult))})."
        )

    def footer(self) -> str:
        """Línea pequeña: apuesta y saldo."""
        game = self.game
        stake = game.stake if game is not None and game.playing else self.stake
        return f"-# Apuesta {format_amount(stake)} · Saldo {format_amount(self.balance)}"

    def color(self) -> discord.Color:
        game = self.game
        if game is None:
            return COLOR_IDLE
        return COLOR_LOST if game.status is Status.LOST else COLOR_HOT

    def embed(
        self, *, image: str = PNG_NAME, text: str | None = None, color: discord.Color | None = None
    ) -> discord.Embed:
        """Embed con la imagen adjunta (`attachment://`)."""
        embed = discord.Embed(
            title=f"🚌 Autobús · {self.owner.display_name}",
            description=text if text is not None else self.description(),
            color=color or self.color(),
        )
        embed.set_image(url=f"attachment://{image}")
        return embed

    def waiting_text(self, pick: Pick, target: Fraction | None) -> str:
        """Texto mientras se ve el GIF, preparado ANTES de jugar: no delata nada."""
        lines = [f"# {random.choice(FLIPPING_LINES)}", f"Pides **{pick.emoji} {pick.label}**"]
        if target is not None:
            lines.append(f"A por {format_multiplier(target)}…")
        lines.append(self.footer())
        return "\n".join(lines)

    # -- Componentes ------------------------------------------------------------------

    def rebuild(self, *, busy: bool = False) -> None:
        """Vuelve a montar los botones con el estado actual."""
        self.clear_items()
        game = self.game
        playing = game is not None and game.playing
        for option in self.options(game):
            pick = option.pick
            if option.multiplier is None:
                label = f"{pick.emoji} {pick.label} · imposible"
            else:
                label = (
                    f"{pick.emoji} {pick.label} · {format_multiplier(option.multiplier)} · "
                    f"{format_chance(option.chance)}"
                )
            self._button(
                label,
                pick.key,
                self._pick_callback(pick),
                style=discord.ButtonStyle.primary,
                row=0,
                disabled=busy or option.multiplier is None,
            )
        if playing:
            assert game is not None
            self._button(
                f"💰 Cobrar {format_amount(game.pot)}",
                "cashout",
                self._cash_out,
                style=discord.ButtonStyle.success,
                row=1,
                disabled=busy,
            )
        else:
            self._button("½", "half", self._halve, row=1, disabled=busy)
            self._button("×2", "x2", self._double, row=1, disabled=busy)
            self._button("💰 All-in", "allin", self._all_in, row=1, disabled=busy)
        self._button("📋 Tabla", "table", self._paytable, row=1)

    def _pick_callback(self, pick: Pick) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await self._pick(interaction, pick)

        return callback

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
        """Solo el dueño juega en su mesa (la tabla de pagos la puede ver cualquiera)."""
        if interaction.user.id == self.owner.id:
            return True
        data: Any = interaction.data or {}
        if data.get("custom_id") == f"{GAME}:table":
            return True
        await interaction.response.send_message(
            f"Este autobús es de {self.owner.display_name}. Sube a la tuya con `guagua`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Caduca: si había una partida a medias, se cobra sola."""
        self.cog.views.discard(self)
        self.cancel_reveals()
        await self.force_settle()
        self.rebuild()
        self.disable_all()
        try:
            if self._last_interaction is not None:
                await self._last_interaction.edit_original_response(embed=self.embed(), view=self)
            elif self.message is not None:
                await self.message.edit(embed=self.embed(), view=self)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar la mesa del autobús", exc_info=True)

    async def force_settle(self) -> None:
        """Cobra la partida a medias (siempre lleva al menos un acierto)."""
        async with self._lock:
            game = self.game
            if game is None or not game.playing:
                return
            game.cash_out()
            await self._settle(game)

    # -- Dinero y partida -------------------------------------------------------------

    async def _start(self) -> str | None:
        """Cobra la apuesta y empieza la partida con las cartas ya sorteadas.

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
        self.game = BusGame.new(self.stake, self.cog.rng, cards=self.deck)
        self.note = None
        return None

    async def _settle(self, game: BusGame) -> BetSettlement | None:
        """Paga la partida terminada (0 si falló) y prepara la nota fiscal."""
        try:
            settlement = await self.cog.economy.pay_winnings(
                self.guild_id, self.owner.id, game=GAME, amount=game.payout
            )
        except BalanceLimitError:
            logger.warning("Premio del autobús por encima del saldo máximo; no se paga.")
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
        game: BusGame,
        settlement: BetSettlement | None,
    ) -> None:
        """Lo que va después de enseñar el final: renta, logros, estadísticas y anuncio."""
        if interaction is not None:
            await renta.remind(self.cog.bot, interaction)
        balance = settlement.balance if settlement else self.balance
        tax = settlement.tax_delta if settlement else 0
        delta = bus_stats(game, when=datetime.now(TIMEZONE))
        delta.merge(
            casino_stats(stake=game.stake, net=game.net, balance_after=balance, tax_delta=tax)
        )
        await logros.casino_play(
            self.cog.bot, self.guild_id, self.owner, self.channel, delta, net=game.net
        )
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
                ("wins", game.wins),
                ("started", int(self.started_at)),
            ),
        )
        await self.cog.shout(game, self.owner, self.channel)

    async def _show(
        self, editor: Callable[..., Awaitable[Any]], media: Media, *, waiting: str
    ) -> None:
        """Enseña el GIF de la carta y después el PNG con el estado nuevo."""
        if media.gif is not None:
            self.rebuild(busy=True)
            await editor(
                # Color neutro: el del final delataría el resultado antes del GIF.
                embed=self.embed(image=GIF_NAME, text=waiting, color=COLOR_IDLE),
                attachments=[discord.File(io.BytesIO(media.gif), filename=GIF_NAME)],
                view=self,
            )
            await asyncio.sleep(media.seconds + REVEAL_MARGIN_SECONDS)
        self.rebuild()
        await editor(
            embed=self.embed(),
            attachments=[discord.File(io.BytesIO(media.png), filename=PNG_NAME)],
            view=self,
        )

    async def play(
        self,
        pick: Pick,
        editor: Callable[..., Awaitable[Any]],
        interaction: discord.Interaction | None = None,
    ) -> str | None:
        """Juega la mano en curso pidiendo `pick` (y empieza partida si no la hay).

        Args:
            editor: Cómo se cambia el mensaje de la mesa (la interacción ya
                aceptada o el mensaje del comando de texto).
            interaction: La del botón, para el aviso de la Renta.

        Returns:
            Un error para el usuario si no se pudo jugar, o `None`.
        """
        if self._busy:
            return None
        self._busy = True
        settlement: BetSettlement | None = None
        game: BusGame | None = None
        started = False
        try:
            async with self._lock:
                if self.game is None or not self.game.playing:
                    if pick.hand is not Hand.COLOR:
                        return None
                    if error := await self._start():
                        return error
                    started = True
                game = self.game
                assert game is not None
                hand = game.hand
                assert hand is not None
                target = next((o.multiplier for o in game.options() if o.pick is pick), None)
                waiting = self.waiting_text(pick, target)
                try:
                    guess = game.play(pick)
                except BusError:
                    return None
                if guess.won and game.playing and game.hand is not None:
                    # La siguiente carta se dibuja mientras se ve esta y el jugador piensa.
                    self.prepare(game.hand.value)
                if game.playing and game.turned:
                    game.cash_out()
                if not game.playing:
                    settlement = await self._settle(game)
                else:
                    self.note = None
                if interaction is not None:
                    self._last_interaction = interaction
            reveal = await self.reveal(hand.value)
            if not game.playing:
                self.new_deck()
            await self._show(editor, reveal.win if guess.won else reveal.lose, waiting=waiting)
        except discord.HTTPException:
            logger.warning("No se pudo enseñar una mano del autobús", exc_info=True)
        finally:
            self._busy = False
        if game is not None and not game.playing:
            await self._after_game(interaction, game, settlement)
        elif interaction is not None and started:
            # Primera carta: ya se ha cobrado la apuesta.
            await renta.remind(self.cog.bot, interaction)
        return None

    async def _pick(self, interaction: discord.Interaction, pick: Pick) -> None:
        """Una opción de la mano en curso: descubre la carta (y empieza partida si hace falta)."""
        # Cobrar la apuesta y enseñar el GIF van antes que nada: se acepta el clic ya.
        await ack(interaction)
        if self._busy:
            return

        async def editor(**kwargs: Any) -> None:
            await edit(interaction, **kwargs)

        if error := await self.play(pick, editor, interaction):
            await notify(interaction, error)

    async def _cash_out(self, interaction: discord.Interaction) -> None:
        """💰 Cobrar: se baja del autobús con lo que hay en juego."""
        await ack(interaction)
        game = self.game
        if self._busy or game is None or not game.playing:
            return
        self._busy = True
        try:
            async with self._lock:
                game.cash_out()
                settlement = await self._settle(game)
                self._last_interaction = interaction
            board = self.cash_board(game)
            self.new_deck()
            png = await self.cog.renderer.board(board)
            self.rebuild()
            await edit(
                interaction,
                embed=self.embed(),
                attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
                view=self,
            )
        finally:
            self._busy = False
        await self._after_game(interaction, game, settlement)

    def cash_board(self, game: BusGame) -> Board:
        """La mesa al cobrar: lo acertado, la carta que venía y el cartel."""
        return Board(
            cards=tuple(game.table),
            results=tuple(g.won for g in game.guesses),
            active=None,
            ghost=game.next_card,
            banner=Banner(
                kind="gold" if game.completed else "cash",
                title="¡COBRADO!",
                sub=format_multiplier(game.multiplier),
            ),
        )

    async def _paytable(self, interaction: discord.Interaction) -> None:
        """📋 La tabla de pagos, en privado (solo memoria: se contesta directamente)."""
        rows = "\n".join(f"**{label}** · {cells}" for label, cells in paytable())
        embed = discord.Embed(
            title="📋 Tabla de pagos del autobús",
            description=(
                "Cada acierto multiplica lo que llevas por la inversa de su probabilidad. "
                "La primera mano lleva el 1 % de la casa y las demás son justas, así que "
                "cobres donde cobres te devuelve de media el 99 %. El as es la carta más alta "
                "y cada carta sale de una baraja entera.\n\n" + rows
            ),
            color=COLOR_IDLE,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    def _idle(self) -> bool:
        return not self._busy and (self.game is None or not self.game.playing)

    async def _restake(self, interaction: discord.Interaction, stake: int) -> None:
        """Cambia la apuesta y repinta el texto y los botones (la imagen se queda)."""
        self.stake = max(1, stake)
        self.note = None
        self._last_interaction = interaction
        self.rebuild()
        await edit(interaction, embed=self.embed(), view=self)

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


class Bus(commands.Cog, name="Autobús"):
    """El Autobús (Ride the Bus) con los yapdollars de la economía del bot."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        casino_channel_ids: frozenset[int] = frozenset(),
        rng: random.Random | None = None,
        renderer: BusScene | None = None,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.casino_channel_ids = casino_channel_ids
        # `secrets` usa el azar del sistema operativo: no se puede predecir.
        self.rng = rng or secrets.SystemRandom()
        self.renderer = renderer or BusScene()
        # Mesas abiertas: para cobrar sus partidas si el bot se apaga.
        self.views: set[BusView] = set()

    async def cog_unload(self) -> None:
        """Al apagar, cobra las partidas a medias, cancela los dibujos y cierra el navegador."""
        for view in list(self.views):
            view.cancel_reveals()
            try:
                await view.force_settle()
            except Exception:
                logger.exception("No se pudo cerrar una partida del autobús al apagar")
            view.stop()
        self.views.clear()
        await self.renderer.close()

    async def shout(self, game: BusGame, user: discord.abc.User, channel: object) -> None:
        """Anuncia en el canal los cobros gordos y los autobuses completos."""
        if game.status is not Status.CASHED or not isinstance(channel, discord.abc.Messageable):
            return
        if game.multiplier < SHOUT_MULTIPLIER and not game.completed:
            return
        if game.turned:
            head = "¡ha completado el autobús **y la vuelta**"
        elif game.completed:
            head = "¡ha completado el autobús"
        else:
            head = "ha cobrado"
        text = (
            f"📣 🚌 ¡{user.mention} {head} con **{format_multiplier(game.multiplier)}**! "
            f"**+{format_amount(game.net)}**"
        )
        try:
            await channel.send(
                text, allowed_mentions=discord.AllowedMentions(users=[user], everyone=False)
            )
        except discord.HTTPException:
            logger.debug("No se pudo anunciar una partida del autobús", exc_info=True)

    # -- guagua -----------------------------------------------------------------------

    async def _guagua_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> None:
        """Lógica compartida de `/guagua` y `.guagua`: abre la mesa."""
        if guild is None:
            await send_error("El autobús solo se juega dentro de un servidor.")
            return
        if error := casino_channel_error(self.casino_channel_ids, channel, "El autobús"):
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

        # La mesa vacía va antes que la mesa: al crearla empieza a dibujarse la
        # primera mano, y el navegador dibuja de uno en uno.
        png = await self.renderer.board(Board())
        view = BusView(self, guild_id=guild.id, owner=user, stake=stake)
        view.channel = channel
        view.balance = balance
        view.rebuild()
        view.message = await send(
            embed=view.embed(), file=discord.File(io.BytesIO(png), filename=PNG_NAME), view=view
        )
        self.views.add(view)

    @app_commands.command(
        name="guagua",
        description="Autobús (Ride the Bus): cuatro cartas, cuatro preguntas. Cobra a tiempo.",
    )
    @app_commands.describe(cantidad="Apuesta: 500, 2k, all… (por defecto 100)")
    @app_commands.guild_only()
    async def guagua(self, interaction: discord.Interaction, cantidad: str | None = None) -> None:
        """Abre una mesa del Autobús.

        Solo en los canales de `CASINO_CHANNEL_IDS` si está configurado. La
        apuesta se cobra al pedir la primera carta y se paga al cobrar.
        """
        responder = InteractionResponder(interaction)

        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            return await interaction.original_response()

        await self._guagua_impl(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            send=send,
            send_error=responder.send_error,
        )

    @commands.command(name="guagua")
    @commands.guild_only()
    async def guagua_text(self, ctx: commands.Context, cantidad: str | None = None) -> None:
        """Versión de texto: `.guagua` o `.guagua 500`."""
        responder = ContextResponder(ctx)

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        await self._guagua_impl(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            amount_text=cantidad,
            send=send,
            send_error=responder.send_error,
        )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(Bus(bot, economy=bot.economy, casino_channel_ids=bot.casino_channel_ids))
