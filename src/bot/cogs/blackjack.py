"""Blackjack con botones: comando `blackjack` (y `.bj` como atajo de texto).

Sigue las mismas normas que la ruleta (`bot.cogs.casino`): mesa individual
que solo pulsa su dueño, dinero siempre a través de `EconomyService`,
mismo control de canales (`CASINO_CHANNEL_IDS`) y fichas con ½ / ×2 /
All-in. `.bj 500` reparte al momento.

El dinero en juego se cobra al repartir (y al doblar o separar), y el premio
se paga al terminar la mano. Para que nunca se quede dinero colgado, una
mano abierta se resuelve plantándose si la mesa caduca o si el bot se apaga
de forma ordenada. Si el proceso muere de golpe a mitad de mano, lo apostado
en esa mano se pierde (queda registrado en el libro de movimientos).

Permisos del bot en el canal: enviar mensajes, insertar enlaces y adjuntar
archivos (la imagen de la mesa).
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs.casino import casino_channel_error, insufficient_text
from bot.services.achievements import blackjack_stats, casino_stats
from bot.services.blackjack import (
    INSURANCE_PAYS,
    MAX_STAKE,
    Action,
    BlackjackGame,
    Card,
    Result,
    new_shoe,
)
from bot.services.cards_render import CardRenderer, HandView
from bot.services.economy import (
    BalanceLimitError,
    EconomyService,
    InsufficientFundsError,
    format_amount,
    gambling_tax_line,
    parse_amount,
)
from bot.services.pets import bet_moment
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

GAME = "blackjack"
DEFAULT_STAKE = 100
TABLE_TIMEOUT = 180
#: Pausa entre cartas de la banca: lo justo para que se vea robar sin aburrir.
DEALER_STEP_SECONDS = 0.6
PNG_NAME = "blackjack.png"

COLOR_PLAYING = discord.Color.from_rgb(21, 83, 64)
COLOR_WIN = discord.Color.from_rgb(255, 196, 0)
COLOR_LOSS = discord.Color.from_rgb(80, 84, 92)
COLOR_PUSH = discord.Color.from_rgb(120, 140, 160)

RULES_FOOTER = (
    f"Blackjack paga 3:2 · seguro 2:1 · la banca se planta en 17 · máx. {format_amount(MAX_STAKE)}"
)

BLACKJACK_LINES = ("🂡 ¡BLACKJACK!", "💥 ¡BLACKJACK!", "🔥 ¡BLACKJACK!")
WIN_LINES = ("¡Ganas", "¡Le ganas a la banca!", "¡Cobras", "¡Toma ya!")
LOSS_LINES = ("La banca gana.", "Casi.", "La próxima es tuya.")
BUST_LINES = ("Te pasas.", "Demasiado.", "Una carta de más.")

RESULT_BADGES = {
    Result.BLACKJACK: "BLACKJACK",
    Result.WIN: "GANA",
    Result.PUSH: "EMPATE",
    Result.LOSE: "PIERDE",
    Result.BUST: "SE PASA",
}

EditFn = Callable[..., Awaitable[Any]]


# -- Presentación -------------------------------------------------------------------


def hand_views(game: BlackjackGame) -> list[HandView]:
    """Manos del jugador listas para dibujar."""
    views = []
    several = len(game.hands) > 1
    for index, hand in enumerate(game.hands):
        name = f"MANO {index + 1}" if several else "TÚ"
        doubled = " ×2" if hand.doubled else ""
        badge = None
        good = False
        if hand.result is not None:
            net = hand.payout() - hand.stake
            badge = RESULT_BADGES[hand.result]
            if net > 0:
                badge += " +" + f"{net:,}".replace(",", ".")
            good = net > 0
        views.append(
            HandView(
                cards=hand.cards,
                title=f"{name} · {hand.total}{doubled}",
                active=game.player_turn and index == game.active,
                badge=badge,
                badge_good=good,
            )
        )
    return views


def render_table(renderer: CardRenderer, game: BlackjackGame) -> bytes:
    """PNG de la mesa en el estado actual de la partida."""
    hidden = not game.hole_revealed
    total = game.visible_dealer_total
    return renderer.render(
        dealer=game.dealer,
        hide_hole=hidden,
        dealer_title=f"BANCA · {total}{' + ?' if hidden else ''}",
        hands=hand_views(game),
    )


def result_headline(game: BlackjackGame, rng: random.Random | None = None) -> str:
    """Titular grande con lo ganado o perdido en la mano completa."""
    rng = rng or random.Random()
    return _main_headline(game, rng) + insurance_line(game)


def _main_headline(game: BlackjackGame, rng: random.Random) -> str:
    net = game.net
    results = {hand.result for hand in game.hands}
    if game.insurance_paid:
        return "# 🛡️ El seguro te cubre\nLa banca tenía blackjack."
    if net > 0:
        if results == {Result.BLACKJACK}:
            return f"# {rng.choice(BLACKJACK_LINES)}\n### +{format_amount(net)}"
        return f"# {rng.choice(WIN_LINES)} +{format_amount(net)}"
    if net == 0:
        return "# 🤝 Empate\nRecuperas tu apuesta."
    if results == {Result.BUST}:
        return f"# 💥 {rng.choice(BUST_LINES)}\n### -{format_amount(-net)}"
    return f"# -{format_amount(-net)}\n### {rng.choice(LOSS_LINES)}"


def insurance_line(game: BlackjackGame) -> str:
    """Línea con lo que hizo el seguro, si se tomó."""
    if not game.insurance:
        return ""
    if game.insurance_paid:
        return f"\n🛡️ El seguro paga +{format_amount(game.insurance * INSURANCE_PAYS)}."
    return f"\n🛡️ Seguro perdido: -{format_amount(game.insurance)}."


def table_embed(
    *,
    owner: str,
    game: BlackjackGame | None,
    balance: int,
    stake: int,
    streak: int,
    headline: str | None = None,
) -> discord.Embed:
    """Embed de la mesa. `headline` es el resultado de la última mano, si acabó."""
    if game is None:
        description = "Pulsa 🃏 **Repartir** para empezar."
        color = COLOR_PLAYING
    elif game.insurance_pending:
        cost = game.insurance_cost
        offer = (
            f"🛡️ **Seguro** por {format_amount(cost)}: paga 2:1 si tiene blackjack."
            if cost
            else "Con una ficha de 1 no hay seguro."
        )
        description = (
            f"## Tú {game.current.total} · Banca {game.visible_dealer_total}\n"
            f"La banca enseña un as. {offer}"
        )
        color = COLOR_PLAYING
    elif game.player_turn:
        description = (
            f"## Tú {game.current.total} · Banca {game.visible_dealer_total}\n"
            "🃏 Pedir · ✋ Plantarse · ⏫ Doblar · ✂️ Separar"
        )
        color = COLOR_PLAYING
    elif not game.settled:
        description = f"## Juega la banca… {game.visible_dealer_total}"
        color = COLOR_PLAYING
    else:
        description = headline or result_headline(game)
        color = COLOR_WIN if game.net > 0 else COLOR_PUSH if game.net == 0 else COLOR_LOSS
    if balance == 0 and (game is None or game.settled):
        description += "\n\n**Estás a cero.** `imv` te recarga."
    embed = discord.Embed(title="🃏 Blackjack", description=description, color=color)
    embed.add_field(name="Saldo", value=format_amount(balance))
    playing = game is not None and not game.settled
    embed.add_field(
        name="En juego" if playing else "Ficha",
        value=format_amount(game.total_stake if playing else stake),  # type: ignore[union-attr]
    )
    embed.add_field(name="Racha", value=f"🔥 {streak}" if streak >= 2 else "—")
    embed.set_image(url=f"attachment://{PNG_NAME}")
    embed.set_footer(text=f"Mesa de {owner} · {RULES_FOOTER}")
    return embed


# -- Mesa ---------------------------------------------------------------------------


class BlackjackTable(discord.ui.View):
    """Mesa de blackjack de un jugador: un mensaje con botones.

    Guarda la partida en curso, la ficha y la racha. Solo su dueño puede
    pulsarla.
    """

    def __init__(
        self, cog: Blackjack, *, guild_id: int, owner: discord.abc.User, stake: int
    ) -> None:
        super().__init__(timeout=TABLE_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        self.stake = stake
        self.game: BlackjackGame | None = None
        self.streak = 0
        self.headline: str | None = None
        self.message: discord.Message | None = None
        self._last_interaction: discord.Interaction | None = None
        self._busy = False
        #: Mano ya pagada que falta contar para los logros, con su saldo e IRPF.
        self._to_track: tuple[BlackjackGame, int, int] | None = None
        self._build_buttons()

    # -- Botones --------------------------------------------------------------------

    def _add(
        self,
        label: str,
        row: int,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        custom_id: str,
    ) -> discord.ui.Button:
        button: discord.ui.Button = discord.ui.Button(
            label=label, style=style, row=row, custom_id=f"{GAME}:{custom_id}"
        )
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)
        return button

    def _action(self, action: Action) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await self.act(interaction, action)

        return callback

    def _build_buttons(self) -> None:
        blue, gray = discord.ButtonStyle.primary, discord.ButtonStyle.secondary
        green = discord.ButtonStyle.success
        self.action_buttons = {
            Action.HIT: self._add(
                "🃏 Pedir", 0, self._action(Action.HIT), style=blue, custom_id="hit"
            ),
            Action.STAND: self._add(
                "✋ Plantarse", 0, self._action(Action.STAND), style=gray, custom_id="stand"
            ),
            Action.DOUBLE: self._add(
                "⏫ Doblar", 0, self._action(Action.DOUBLE), style=green, custom_id="double"
            ),
            Action.SPLIT: self._add(
                "✂️ Separar", 0, self._action(Action.SPLIT), style=gray, custom_id="split"
            ),
        }
        self.insurance_buttons = {
            True: self._add("🛡️ Seguro", 2, self._insure_yes, style=blue, custom_id="insure"),
            False: self._add("Sin seguro", 2, self._insure_no, style=gray, custom_id="noinsure"),
        }
        self.deal_button = self._add(
            "🃏 Repartir", 1, self._deal_again, style=green, custom_id="deal"
        )
        self.stake_buttons = [
            self._add("½", 1, self._halve, custom_id="half"),
            self._add("×2", 1, self._double_stake, custom_id="x2"),
            self._add("💰 All-in", 1, self._all_in, custom_id="allin"),
        ]
        self._update_buttons()

    def _update_buttons(self, *, locked: bool = False) -> None:
        """Activa las acciones de la mano o las de repartir, según toque.

        Los botones del seguro solo están en la mesa mientras se decide.
        """
        game = self.game
        insuring = game is not None and game.insurance_pending
        in_hand = game is not None and (game.player_turn or insuring)
        for action, button in self.action_buttons.items():
            button.disabled = locked or not (in_hand and game.can(action))  # type: ignore[union-attr]
        for button in (self.deal_button, *self.stake_buttons):
            button.disabled = locked or in_hand
        for take, button in self.insurance_buttons.items():
            if insuring and button not in self.children:
                self.add_item(button)
            elif not insuring and button in self.children:
                self.remove_item(button)
            button.disabled = locked or not insuring or (take and not game.can_insure())  # type: ignore[union-attr]

    # -- Ciclo de vida --------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño juega en su mesa."""
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message(
            f"Esta mesa es de {self.owner.display_name}. Abre la tuya con `bj`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Cierra la mesa; si quedaba una mano a medias, se planta y se paga."""
        self.cog.tables.discard(self)
        if self.game is not None and not self.game.settled:
            await self.force_settle()
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        try:
            embed = await self.current_embed()
            png = await self.cog.render(self.game) if self.game else None
            kwargs: dict[str, Any] = {"embed": embed, "view": self}
            if png is not None:
                kwargs["attachments"] = [discord.File(io.BytesIO(png), filename=PNG_NAME)]
            if self._last_interaction is not None:
                await self._last_interaction.edit_original_response(**kwargs)
            elif self.message is not None:
                await self.message.edit(**kwargs)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar la mesa de blackjack", exc_info=True)

    async def force_settle(self) -> None:
        """Termina la mano sin animación (caducidad o apagado) y paga lo que toque."""
        game = self.game
        if game is None or game.settled:
            return
        game.stand_all()
        game.reveal_hole()
        while game.dealer_should_draw():
            game.dealer_draw()
        await self._settle(game)
        await self._track()

    # -- Juego ----------------------------------------------------------------------

    async def balance(self) -> int:
        """Saldo actual del dueño."""
        return await self.cog.economy.balance(self.guild_id, self.owner.id)

    async def current_embed(self, balance: int | None = None) -> discord.Embed:
        """Embed con el estado actual de la mesa."""
        if balance is None:
            balance = await self.balance()
        return table_embed(
            owner=self.owner.display_name,
            game=self.game,
            balance=balance,
            stake=self.stake,
            streak=self.streak,
            headline=self.headline,
        )

    async def _show(self, edit: EditFn, *, locked: bool = False) -> None:
        """Dibuja la mesa y la envía con `edit` (editar o enviar, según el caso)."""
        assert self.game is not None
        self._update_buttons(locked=locked)
        png = await self.cog.render(self.game)
        await edit(
            embed=await self.current_embed(),
            attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
            view=self,
        )

    async def start(self, first_edit: EditFn, next_edit: EditFn) -> str | None:
        """Cobra la ficha y reparte una mano nueva.

        Returns:
            Un mensaje de error para el usuario si no se pudo repartir, o `None`.
        """
        try:
            await self.cog.economy.place_bet(
                self.guild_id, self.owner.id, game=GAME, stake=self.stake
            )
        except InsufficientFundsError as error:
            return insufficient_text(error.balance)
        # Para las porras: una mano repartida antes del cierre no cuenta.
        self.started_at = time.time()
        self.game = BlackjackGame(stake=self.stake, shoe=self.cog.new_shoe())
        self.headline = None
        self.game.deal()
        if self.game.player_turn or self.game.insurance_pending:
            await self._show(first_edit)
        else:
            # Blackjack de alguien en el reparto: se resuelve sin turno.
            await self._finish(first_edit, next_edit)
        return None

    async def act(self, interaction: discord.Interaction, action: Action) -> None:
        """Aplica una acción del jugador; si termina su turno, juega la banca."""
        game = self.game
        if self._busy or game is None or not game.can(action):
            await ack(interaction)
            return
        self._busy = True
        try:
            # Cobrar, pagar y dibujar la mesa tardan: se acepta el clic antes.
            await ack(interaction)
            extra = game.extra_stake(action)
            if extra:
                try:
                    await self.cog.economy.place_bet(
                        self.guild_id, self.owner.id, game=GAME, stake=extra
                    )
                except InsufficientFundsError as error:
                    verb = "doblar" if action is Action.DOUBLE else "separar"
                    await notify(
                        interaction,
                        f"Para {verb} necesitas {format_amount(extra)} más y tienes "
                        f"{format_amount(error.balance)}.",
                    )
                    return
            game.act(action)
            self._last_interaction = interaction
            if game.player_turn:
                await self._show(interaction.edit_original_response)
            else:
                await self._finish(
                    interaction.edit_original_response, interaction.edit_original_response
                )
        finally:
            self._busy = False
        if game.settled:
            await renta.remind(self.cog.bot, interaction)

    async def _insure_yes(self, interaction: discord.Interaction) -> None:
        await self.insure(interaction, take=True)

    async def _insure_no(self, interaction: discord.Interaction) -> None:
        await self.insure(interaction, take=False)

    async def insure(self, interaction: discord.Interaction, *, take: bool) -> None:
        """Toma o rechaza el seguro; luego la banca mira si tiene blackjack.

        El seguro es una apuesta más del juego (`place_bet`, y lo que paga va
        con el resto de la mano en `pay_winnings`): tributa como juego.
        """
        game = self.game
        if (
            self._busy
            or game is None
            or not game.insurance_pending
            or (take and not game.can_insure())
        ):
            await ack(interaction)
            return
        self._busy = True
        try:
            await ack(interaction)
            if take:
                cost = game.insurance_cost
                try:
                    await self.cog.economy.place_bet(
                        self.guild_id, self.owner.id, game=GAME, stake=cost
                    )
                except InsufficientFundsError as error:
                    await notify(
                        interaction,
                        f"El seguro cuesta {format_amount(cost)} y tienes "
                        f"{format_amount(error.balance)}.",
                    )
                    return
            game.decide_insurance(take)
            self._last_interaction = interaction
            if game.player_turn:
                await self._show(interaction.edit_original_response)
            else:
                await self._finish(
                    interaction.edit_original_response, interaction.edit_original_response
                )
        finally:
            self._busy = False
        if take or game.settled:
            await renta.remind(self.cog.bot, interaction)

    async def _finish(self, first_edit: EditFn, next_edit: EditFn) -> None:
        """Turno de la banca carta a carta y pago final."""
        game = self.game
        assert game is not None
        edit = first_edit
        if game.dealer_should_draw() or not game.hole_revealed:
            game.reveal_hole()
            await self._show(edit, locked=True)
            edit = next_edit
            while game.dealer_should_draw():
                await asyncio.sleep(DEALER_STEP_SECONDS)
                game.dealer_draw()
                await self._show(edit, locked=True)
            await asyncio.sleep(DEALER_STEP_SECONDS)
        balance = await self._settle(game)
        self._update_buttons()
        png = await self.cog.render(game)
        await edit(
            embed=await self.current_embed(balance),
            attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
            view=self,
        )
        await self._track()

    async def _settle(self, game: BlackjackGame) -> int:
        """Decide la mano, paga y actualiza la racha. Devuelve el saldo final.

        Si la mano ya se pagó (p. ej. el apagado la cerró mientras la banca
        robaba en pantalla) no se vuelve a pagar.
        """
        if game.settled:
            return await self.balance()
        payout = game.settle()
        tax_note: str | None = None
        tax_delta = 0
        try:
            settlement = await self.cog.economy.pay_winnings(
                self.guild_id, self.owner.id, game=GAME, amount=payout
            )
            balance = settlement.balance
            tax_note = gambling_tax_line(settlement)
            tax_delta = settlement.tax_delta
        except BalanceLimitError:
            logger.warning("Premio de blackjack por encima del saldo máximo; no se paga.")
            balance = await self.balance()
        self._to_track = (game, balance, tax_delta)
        if game.net > 0:
            self.streak += 1
        elif game.net < 0:
            self.streak = 0
        self.headline = result_headline(game)
        if tax_note:
            self.headline += f"\n{tax_note}"
        if renta_hint := await renta.hint(
            self.cog.bot,
            self.guild_id,
            self.owner.id,
            bet_moment(stake=game.total_stake, net=game.net, balance_after=balance),
        ):
            self.headline += f"\n{renta_hint}"
        return balance

    async def _track(self) -> None:
        """Cuenta la última mano pagada para los logros (una sola vez).

        Se llama después de enseñar el resultado, para que el aviso de un
        logro no se adelante a las cartas de la banca.
        """
        if self._to_track is None:
            return
        game, balance, tax_delta = self._to_track
        self._to_track = None
        delta = blackjack_stats(game)
        delta.merge(
            casino_stats(
                stake=game.total_stake, net=game.net, balance_after=balance, tax_delta=tax_delta
            )
        )
        await logros.casino_play(
            self.cog.bot,
            self.guild_id,
            self.owner,
            getattr(self.message, "channel", None),
            delta,
            net=game.net,
        )
        await apuestas.record(
            self.cog.bot,
            self.guild_id,
            self.owner,
            game=GAME,
            stake=game.total_stake,
            net=game.net,
            balance_after=balance,
            tax=tax_delta,
            details=(
                ("bust", int(any(hand.busted for hand in game.hands))),
                ("natural", int(any(hand.natural for hand in game.hands))),
                ("started", int(getattr(self, "started_at", 0))),
            ),
        )

    # -- Fichas y repartir ----------------------------------------------------------

    async def _refresh(self, interaction: discord.Interaction, balance: int | None = None) -> None:
        # El embed lee el saldo de la base de datos: se acepta el clic antes.
        await ack(interaction)
        self._update_buttons()
        await edit(interaction, embed=await self.current_embed(balance), view=self)
        self._last_interaction = interaction

    async def _halve(self, interaction: discord.Interaction) -> None:
        self.stake = max(1, self.stake // 2)
        await self._refresh(interaction)

    async def _double_stake(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        balance = await self.balance()
        self.stake = max(1, min(self.stake * 2, balance, MAX_STAKE))
        await self._refresh(interaction, balance)

    async def _all_in(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        balance = await self.balance()
        if balance == 0:
            await notify(interaction, insufficient_text(0))
            return
        self.stake = min(balance, MAX_STAKE)
        await self._refresh(interaction, balance)

    async def _deal_again(self, interaction: discord.Interaction) -> None:
        if self._busy or (self.game is not None and not self.game.settled):
            await ack(interaction)
            return
        self._busy = True
        try:
            await ack(interaction)
            self._last_interaction = interaction
            error = await self.start(
                interaction.edit_original_response, interaction.edit_original_response
            )
            if error is not None:
                await notify(interaction, error)

        finally:
            self._busy = False
        await renta.remind(self.cog.bot, interaction)


# -- Cog ----------------------------------------------------------------------------


class Blackjack(commands.Cog):
    """Blackjack con los yapdollars de la economía del bot."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        renderer: CardRenderer | None = None,
        shoe_factory: Callable[[], list[Card]] = new_shoe,
        casino_channel_ids: frozenset[int] = frozenset(),
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.renderer = renderer or CardRenderer()
        self.new_shoe = shoe_factory
        self.casino_channel_ids = casino_channel_ids
        # Mesas abiertas: para resolver sus manos si el bot se apaga. Cada
        # mesa sale de aquí al caducar, así que el tamaño está acotado.
        self.tables: set[BlackjackTable] = set()

    async def cog_unload(self) -> None:
        """Al apagar, planta y paga las manos a medias para no quedarse con el dinero."""
        for table in list(self.tables):
            try:
                await table.force_settle()
            except Exception:
                logger.exception("No se pudo cerrar una mano de blackjack al apagar")
            table.stop()
        self.tables.clear()

    async def render(self, game: BlackjackGame) -> bytes:
        """PNG de la mesa, dibujado fuera del event loop."""
        return await asyncio.to_thread(render_table, self.renderer, game)

    async def _bj_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> None:
        """Lógica compartida entre `/blackjack`, `.blackjack` y `.bj`: abre la mesa y reparte ya."""
        if guild is None:
            await send_error("El blackjack solo se juega dentro de un servidor.")
            return
        if error := casino_channel_error(self.casino_channel_ids, channel, "El blackjack"):
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
        stake = min(stake, MAX_STAKE)
        if stake > balance:
            await send_error(insufficient_text(balance))
            return

        table = BlackjackTable(self, guild_id=guild.id, owner=user, stake=stake)

        async def first_edit(**kwargs: Any) -> None:
            attachments = kwargs.pop("attachments")
            table.message = await send(files=attachments, **kwargs)

        async def next_edit(**kwargs: Any) -> None:
            assert table.message is not None
            await table.message.edit(**kwargs)

        error = await table.start(first_edit, next_edit)
        if error is not None:
            await send_error(error)
            return
        self.tables.add(table)

    @app_commands.command(
        name="blackjack", description="Blackjack con tus yapdollars: reparte al momento."
    )
    @app_commands.describe(cantidad="Apuesta: 500, 2k, all… (por defecto 100)")
    @app_commands.guild_only()
    async def blackjack(
        self, interaction: discord.Interaction, cantidad: str | None = None
    ) -> None:
        """Reparte una mano de blackjack con botones para jugarla.

        Solo en los canales de `CASINO_CHANNEL_IDS` si está configurado.
        Cobra la apuesta al repartir y paga al terminar la mano.
        """

        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            return await interaction.original_response()

        await self._bj_impl(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            send=send,
            send_error=InteractionResponder(interaction).send_error,
        )
        await renta.remind(self.bot, interaction)

    # Única excepción a la norma de un nombre por comando (ver Biblia.txt):
    # `.bj` es el atajo de siempre y `.blackjack` el nombre que se busca.
    @commands.command(name="blackjack", aliases=["bj"])
    @commands.guild_only()
    async def blackjack_text(self, ctx: commands.Context, cantidad: str | None = None) -> None:
        """Versión de texto: `.bj`, `.bj 500`, `.blackjack all`."""

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        await self._bj_impl(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            amount_text=cantidad,
            send=send,
            send_error=ContextResponder(ctx).send_error,
        )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(
        Blackjack(bot, economy=bot.economy, casino_channel_ids=bot.casino_channel_ids)
    )
