"""Blackjack con botones: comando `blackjack` (y `.bj` como atajo de texto).

Sigue las mismas normas que la ruleta (`bot.cogs.casino`): mesa individual
que solo pulsa su dueño, dinero siempre a través de `EconomyService`,
mismo control de canales (`CASINO_CHANNEL_IDS`) y fichas con ½ / ×2 /
All-in. `.bj 500` reparte al momento.

El zapato (6 barajas) es de cada jugador: dura entre manos, y entre mesas, y se
baraja al llegar a la carta de corte, así que se pueden contar cartas; la mesa enseña
cuántas quedan. Cada jugador tiene una mesa abierta: abrir otra cierra la anterior y
sigue con el mismo zapato (si no, bastaría levantarse con la cuenta en contra para
estrenar uno). Si la apuesta pasa de `PIT_BOSS_SPREAD` veces la más baja del zapato
con la cuenta alta, el jefe de sala baraja antes de repartir (`bot.services.blackjack`).

El dinero en juego se cobra al repartir (y al doblar o separar), y el premio
se paga al terminar la mano. Para que nunca se quede dinero colgado, una
mano abierta se resuelve plantándose si la mesa caduca o si el bot se apaga
de forma ordenada. Si el proceso muere de golpe a mitad de mano, lo apostado
en esa mano se pierde (queda registrado en el libro de movimientos).

La mesa se dibuja con canvas (`bot.services.blackjack_scene`, escena
`assets/blackjack/escena.html`): tapete con skins (botón 🎨), el zapato y la bandeja de
descartes que dejan ver cuánto se ha jugado del zapato, las cartas que vuelan y se
voltean, las fichas, los totales y la racha. Cada jugada es un GIF que suena con los
botones apagados y sin el resultado en el texto, y al acabar se cambia por el PNG de cómo
queda la mesa, con los botones encendidos (y el resultado, si la mano acabó), como en el
resto del casino.

Mientras se ve una jugada, la mesa pinta en segundo plano el GIF de cada botón que
puede pulsar (`BlackjackTable.prepare`): el zapato es una lista y la mano siguiente
se conoce, así que se juega sobre una copia sin enseñarla. Entre manos, «Repartir»
con la ficha de ahora y con la de ½, ×2 y All-in, y con el zapato que toque (el
barajado de antes, si sale la carta de corte o el jefe de sala baraja). Al pulsar
solo queda subirlo.

Permisos del bot en el canal: enviar mensajes, insertar enlaces y adjuntar
archivos (el GIF y la imagen de la mesa).
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
import time
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs import shop as tienda
from bot.cogs.casino import casino_channel_error, insufficient_text
from bot.services.achievements import blackjack_shoe_stats, blackjack_stats, casino_stats
from bot.services.blackjack import (
    CUT_CARD,
    INSURANCE_PAYS,
    Action,
    BlackjackGame,
    Card,
    Result,
    blackjack_prize,
    hand_total,
    needs_shuffle,
    new_shoe,
    pit_boss_shuffles,
    true_count,
)
from bot.services.blackjack_scene import BlackjackScene, Media, Shoe
from bot.services.casino_channels import CasinoChannelIds
from bot.services.economy import (
    BalanceLimitError,
    EconomyService,
    InsufficientFundsError,
    format_amount,
    gambling_tax_line,
    parse_amount,
)
from bot.services.pets import bet_moment
from bot.utils.ahead import Ahead
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

GAME = "blackjack"
DEFAULT_STAKE = 100
TABLE_TIMEOUT = 180
PNG_NAME = "blackjack.png"
GIF_NAME = "blackjack.gif"
#: Lo que se deja pasar tras el GIF antes de enseñar el resultado: lo que tarda el cliente
#: en descargarlo (si se queda corto, el resultado sale antes de acabar la animación).
REVEAL_MARGIN_SECONDS = 0.5

#: Lo que dice la mesa mientras suena un GIF (sin resultado ni saldo).
DEAL_BUSY = "## 🃏 Repartiendo…"
DEALER_BUSY = "## 🃏 Juega la banca…"
PLAYER_BUSY = "## 🃏 Carta para ti…"
INSURE_BUSY = "## 🛡️ La banca mira su carta…"

COLOR_PLAYING = discord.Color.from_rgb(21, 83, 64)
COLOR_WIN = discord.Color.from_rgb(255, 196, 0)
COLOR_LOSS = discord.Color.from_rgb(80, 84, 92)
COLOR_PUSH = discord.Color.from_rgb(120, 140, 160)

RULES_FOOTER = "Blackjack paga 3:2 · seguro 2:1 · la banca se planta en 17"
#: Lo que dice el jefe de sala cuando baraja porque sospecha que cuentas.
PIT_BOSS_LINES = (
    "🕵️ El jefe de sala te ha visto mover los labios contando. Baraja el zapato.",
    "🕵️ «Aquí no se cuenta, que esto es un casino». El jefe de sala baraja.",
    "🕵️ Subes la ficha justo cuando quedan figuras. Casualidad, seguro. Se baraja.",
    "🕵️ El jefe de sala llama a Rain Man por el pinganillo y baraja el zapato.",
    "🕵️ El jefe de sala no te quita ojo. Zapato nuevo.",
    "🕵️ Te han reconocido del equipo del MIT. Zapato nuevo.",
)
#: Al llegar a la carta de corte.
SHUFFLE_LINE = "🔀 Ha salido la carta de corte: zapato nuevo de 6 barajas."

BLACKJACK_LINES = (
    "🂡 ¡BLACKJACK!",
    "💥 ¡BLACKJACK!",
    "🔥 ¡BLACKJACK!",
    "🂡 ¡VEINTIUNO!",
    "🂡 ¡BLACKJACK! Toma mangó",
    "🂡 ¡BLACKJACK! ¿Con pulpa? ¡Sin pulpa!",
    "🂡 ¡21! Así, de salida",
    "🂡 ¡21! Ni contando cartas sale tan bonito",
    "🂡 ¡21! El as y su pareja de baile",
    "🂡 ¡BLACKJACK! Sin despeinarte",
    "🂡 ¡21! A la primera, como los valientes",
    "🂡 ¡21! Esto es el Real Madrid en el minuto 93",
)
WIN_LINES = (
    "¡Ganas",
    "¡Le ganas a la banca!",
    "¡Cobras",
    "¡Toma ya!",
    "¡Eso es!",
    "La banca hoy paga la ronda.",
    "La banca se queda con cara de tonta.",
    "¿Qué? ¿Qué? ¡Toma mangó!",
    "La banca pide el rescate.",
    "Plantado y cobrando.",
    "El crupier ya no te mira igual.",
    "Ganas por la mínima, que es como se gana con estilo.",
    "Tremendo. Tremendo, de verdad.",
    "Hoy la banca se va a casa con hambre.",
    "Veintiuno de los buenos.",
)
LOSS_LINES = (
    "La banca gana.",
    "Casi.",
    "La próxima es tuya.",
    "Otra mano, otra historia.",
    "Hazme café… ¡Quiero café!",
    "Yo soy yo… y así me tienes que soportar.",
    "La banca gana. Como siempre, la muy pesada.",
    "Luis, sé fuerte.",
    "La banca tenía un as escondido en el escote.",
    "Otra vez será.",
    "La banca no tiene corazón, solo figuras.",
    "Hoy no es tu día. Mañana, a lo mejor.",
    "Sofía, cállate, que me desconcentras.",
    "La banca te ha hecho un Ocean's Eleven.",
    "Te han ganado por un pelo.",
)
BUST_LINES = (
    "Te pasas.",
    "Demasiado.",
    "Una carta de más.",
    "Te pasas de frenada.",
    "Estoy destruido, pero por fuera… por dentro estoy nuevo.",
    "La avaricia rompe el saco.",
    "Pediste una de más y vino un diez.",
    "Te pasas. Veintidós ya no es veintiuno.",
    "Más cartas que promesas de campaña.",
    "Te ha mirado el diez y te has venido arriba.",
    "Una más y salías en el Guinness.",
)

EditFn = Callable[..., Awaitable[Any]]


# -- La mano sin dinero (también sobre copias, para pintar antes del clic) -----------


def hand_over(game: BlackjackGame) -> bool:
    """Si ya no queda nada que decidir: ni turno del jugador ni seguro."""
    return not game.player_turn and not game.insurance_pending


def resolve(game: BlackjackGame) -> None:
    """Si se acabó tu turno, la banca destapa y roba de un tirón (lo anima el GIF)."""
    if not hand_over(game) or game.settled:
        return
    game.reveal_hole()
    while game.dealer_should_draw():
        game.dealer_draw()


def next_streak(streak: int, game: BlackjackGame) -> int:
    """La racha tras una mano pagada: sube al ganar, se corta al perder y aguanta al empatar."""
    if game.net > 0:
        return streak + 1
    if game.net < 0:
        return 0
    return streak


def likely_actions(game: BlackjackGame) -> list[Action]:
    """Las acciones legales de la mano activa, la más probable primero (para pintarlas).

    Lo de siempre en una mesa: plantarse con 17 o más, doblar con 10 u 11, separar ases
    y ochos y, si no, pedir.
    """
    hand = game.current
    total = hand_total(hand.cards)[0]
    legal = [a for a in (Action.HIT, Action.STAND, Action.DOUBLE, Action.SPLIT) if game.can(a)]
    first = Action.STAND if total >= 17 else Action.HIT
    if total in (10, 11) and Action.DOUBLE in legal:
        first = Action.DOUBLE
    if Action.SPLIT in legal and hand.cards[0].rank in (1, 8):
        first = Action.SPLIT
    return [first, *(a for a in legal if a is not first)]


# -- Presentación -------------------------------------------------------------------


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


def natural_insurance_text(stake: int, cost: int) -> str:
    """Lo que cobra un blackjack propio con y sin seguro, con las cifras de la apuesta.

    Asegurado es el «dinero par» de las mesas: si la banca tiene blackjack, la mano
    empata y el seguro paga; si no, la mano paga 3:2 y el seguro se pierde. Con una
    apuesta par sale lo mismo en los dos casos; con una impar, el redondeo del 3:2
    y de la media apuesta los separa en una unidad.
    """
    prize = blackjack_prize(stake)
    low, high = sorted((cost * INSURANCE_PAYS, prize - cost))
    insured = (
        f"{format_amount(low)} pase lo que pase (dinero par)"
        if low == high
        else f"{format_amount(low)} o {format_amount(high)}"
    )
    plain = f"{format_amount(prize)} o empate"
    return f" Tienes blackjack: asegurado cobras {insured}; sin seguro, {plain}."


def table_embed(
    *,
    owner: str,
    game: BlackjackGame | None,
    balance: int,
    stake: int,
    streak: int,
    headline: str | None = None,
    shoe_left: int | None = None,
    note: str | None = None,
    busy: str | None = None,
    image: str = PNG_NAME,
    skin_label: str | None = None,
) -> discord.Embed:
    """Embed de la mesa. `headline` es el resultado de la última mano, si acabó.

    Args:
        shoe_left: Cartas que quedan en el zapato de la mesa, para quien cuenta.
        note: Aviso de la mano (se ha barajado o el jefe de sala sospecha).
        busy: Texto mientras suena un GIF (repartiendo, juega la banca…): no enseña
            saldo ni resultado, que se verían antes que las cartas.
        image: Nombre del archivo que lleva la imagen del mensaje (el GIF o el PNG).
        skin_label: El tapete que lleva el dueño, si no es el de serie.
    """
    if busy is not None:
        description = busy
        color = COLOR_PLAYING
    elif game is None:
        description = "Pulsa 🃏 **Repartir** para empezar."
        color = COLOR_PLAYING
    elif game.insurance_pending:
        cost = game.insurance_cost
        offer = (
            f"🛡️ **Seguro** por {format_amount(cost)}: paga 2:1 si tiene blackjack."
            if cost
            else "Con una ficha de 1 no hay seguro."
        )
        if cost and game.current.natural:
            offer += natural_insurance_text(game.stake, cost)
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
    if note:
        description += f"\n-# {note}"
    if busy is None and balance == 0 and (game is None or game.settled):
        description += "\n\n**Estás a cero.** `imv` te recarga."
    embed = discord.Embed(title="🃏 Blackjack", description=description, color=color)
    if busy is None:
        embed.add_field(name="Saldo", value=format_amount(balance))
    playing = game is not None and (busy is not None or not game.settled)
    embed.add_field(
        name="En juego" if playing else "Ficha",
        value=format_amount(game.total_stake if playing else stake),  # type: ignore[union-attr]
    )
    if busy is None:
        embed.add_field(name="Racha", value=f"🔥 {streak}" if streak >= 2 else "—")
    if shoe_left is not None:
        embed.add_field(name="Zapato", value=f"🂠 {shoe_left} cartas · corte en {CUT_CARD}")
    embed.set_image(url=f"attachment://{image}")
    footer = f"Mesa de {owner} · {RULES_FOOTER}"
    if skin_label:
        footer += f" · Tapete: {skin_label}"
    embed.set_footer(text=footer)
    return embed


# -- Mesa ---------------------------------------------------------------------------


class BlackjackTable(discord.ui.View):
    """Mesa de blackjack de un jugador: un mensaje con botones.

    Guarda la partida en curso, la ficha, la racha y el zapato. Solo su dueño puede
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
        #: Zapato del jugador: dura entre manos hasta la carta de corte (se cuentan cartas)
        #: y sigue en la próxima mesa que abra (`Blackjack.shoes`).
        self.shoe: list[Card]
        #: Apuesta más baja jugada con este zapato, para el jefe de sala.
        self.lowest: int | None
        self.shoe, self.lowest = cog.shoes.get((guild_id, owner.id), ([], None))
        #: Zapato ya barajado para cuando toque barajar, sorteado antes sin enseñarlo: así
        #: se pinta antes del clic la mano que sale de él. Solo con `cog.ahead`.
        self._next_shoe: list[Card] | None = None
        #: Aviso de la mano en curso: se ha barajado o el jefe de sala ha sospechado.
        self.shoe_note: str | None = None
        #: Lo que pasó con el zapato al repartir la última mano, para los logros:
        #: `(jefe de sala, carta de corte, cuenta verdadera, veces la apuesta más baja)`.
        self._shoe_event: tuple[bool, bool, float, int | None] | None = None
        self.streak = 0
        self.headline: str | None = None
        self.message: discord.Message | None = None
        self.started_at = 0.0
        self._last_interaction: discord.Interaction | None = None
        self._busy = False
        #: El archivo que lleva ahora el mensaje (el GIF de una jugada que deja la mano
        #: abierta, o el PNG): lo que se edita sin subir imagen sigue apuntando a él.
        self._image = PNG_NAME
        #: Mano ya pagada que falta contar para los logros, con su saldo e IRPF.
        self._to_track: tuple[BlackjackGame, int, int] | None = None
        #: Último saldo conocido (al pagar o cambiar la ficha): las fichas de ×2 y All-in
        #: que se pintan antes del clic.
        self._balance: int | None = None
        #: Sube cuando la ficha se sale de lo pintado para repartir: hay que repintar.
        self._deal_round = 0
        #: GIF pintados por adelantado para cada botón de la jugada en curso (`prepare`).
        self.ahead = Ahead(cog.ahead, name="el blackjack")
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
        # El 🎨: elegir o comprar el tapete de esta mesa.
        self.paint_button = tienda.skin_button(self.cog.bot, GAME, row=1)
        if self.paint_button is not None:
            self.add_item(self.paint_button)
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
        if self.paint_button is not None:
            self.paint_button.disabled = locked
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
        self.ahead.drop()  # lo que quede por pintar ya no lo verá nadie
        # Una mano ya pagada se ve tal cual: solo hay que apagar los botones, sin
        # volver a subir la mesa (~1 s).
        unsettled = self.game is not None and not self.game.settled
        if unsettled:
            await self.force_settle()
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        try:
            kwargs: dict[str, Any] = {"view": self}
            if self.game is not None and unsettled:
                png = await self.cog.renderer.still(self.game, streak=self.streak, skin=self.skin())
                kwargs["attachments"] = [discord.File(io.BytesIO(png), filename=PNG_NAME)]
                self._image = PNG_NAME
            kwargs["embed"] = await self.current_embed()
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
        resolve(game)
        await self._settle(game)
        await self._track()

    # -- Juego ----------------------------------------------------------------------

    async def balance(self) -> int:
        """Saldo actual del dueño."""
        return await self.cog.economy.balance(self.guild_id, self.owner.id)

    def skin(self) -> dict[str, object]:
        """El tapete que lleva ahora el dueño de la mesa (`tienda.skin_params`)."""
        return tienda.skin_params(self.cog.bot, self.guild_id, self.owner.id, GAME)

    async def current_embed(
        self, balance: int | None = None, *, busy: str | None = None, image: str | None = None
    ) -> discord.Embed:
        """Embed con el estado actual de la mesa (sin saldo mientras suena un GIF, `busy`).

        Args:
            image: El archivo que se sube con esta edición; si no se sube ninguno, el que
                ya lleva el mensaje.
        """
        if balance is None and busy is None:
            balance = await self.balance()
        worn = tienda.skin_of(self.cog.bot, self.guild_id, self.owner.id, GAME)
        return table_embed(
            owner=self.owner.display_name,
            game=self.game,
            balance=balance or 0,
            stake=self.stake,
            streak=self.streak,
            headline=self.headline,
            shoe_left=len(self.shoe) if self.shoe else None,
            note=self.shoe_note,
            busy=busy,
            image=image or self._image,
            skin_label=worn.label if worn else None,
        )

    # -- El zapato --------------------------------------------------------------------

    def _shoe_plan(self, stake: int) -> tuple[bool, bool, float, int | None, bool]:
        """Qué pasa con el zapato si se reparte con `stake`, sin tocar nada.

        Returns:
            `(jefe de sala, carta de corte, cuenta verdadera, veces la apuesta más baja,
            se baraja)`.
        """
        caught = pit_boss_shuffles(self.shoe, stake=stake, lowest=self.lowest)
        cut = bool(self.shoe) and needs_shuffle(self.shoe)
        spread = None if self.lowest is None else stake // max(1, self.lowest)
        return caught, cut, true_count(self.shoe), spread, caught or needs_shuffle(self.shoe)

    def _shoe_for(self, stake: int) -> list[Card]:
        """El zapato con el que se reparte la mano: el de la mesa o uno recién barajado.

        Se baraja al llegar a la carta de corte o si el jefe de sala sospecha
        (`pit_boss_shuffles`). La mano reparte de esta misma lista, así que lo que
        sale se queda fuera para las siguientes.
        """
        caught, cut, count, spread, fresh = self._shoe_plan(stake)
        if fresh:
            self.shoe, self._next_shoe = self._next_shoe or self.cog.new_shoe(), None
            self.lowest, count = None, 0.0
        self.shoe_note = random.choice(PIT_BOSS_LINES) if caught else SHUFFLE_LINE if cut else None
        self._shoe_event = (caught, cut, count, None if caught else spread)
        self.lowest = stake if self.lowest is None else min(self.lowest, stake)
        self.cog.shoes[self.guild_id, self.owner.id] = (self.shoe, self.lowest)
        return self.shoe

    @staticmethod
    def _shoe_change(caught: bool, cut: bool) -> str | None:
        """Lo que la escena anima antes de repartir: el jefe de sala, la carta de corte o nada."""
        return "boss" if caught else "cut" if cut else None

    # -- GIF por adelantado -----------------------------------------------------------

    def _deal_stakes(self) -> list[int]:
        """Las fichas con las que se puede repartir ahora: la puesta y las de ½, ×2 y All-in."""
        stakes = [self.stake, max(1, self.stake // 2)]
        if (balance := self._balance) is not None:
            stakes += [max(1, min(self.stake * 2, balance)), balance]
        seen: list[int] = []
        for stake in stakes:
            if stake > 0 and stake not in seen and (balance is None or stake <= balance):
                seen.append(stake)
        return seen

    def _key(self) -> tuple[Any, ...]:
        """Lo que decide los GIF que se pintan: la jugada en curso y el tapete.

        Durante la mano, su estado; entre manos, el zapato, la racha y las fichas
        pintadas (`_deal_round`). El tapete va al final: si el dueño lo cambia, lo
        pintado ya no vale.
        """
        skin = self.skin()["key"]
        game = self.game
        if game is not None and not game.settled:
            cards = len(game.dealer) + sum(len(h.cards) for h in game.hands)
            return ("turn", id(game), game.insurance_pending, game.active, cards, skin)
        return (
            "deal",
            id(game),
            id(self._next_shoe),
            len(self.shoe),
            self.lowest,
            self.streak,
            self._deal_round,
            skin,
        )

    def prepare(self) -> None:
        """Pinta en segundo plano el GIF de cada botón que puede pulsar el jugador.

        Con el seguro por decidir, «Sin seguro» y «Seguro»; en tu turno, cada acción
        legal (la más probable primero, `likely_actions`); entre manos, «Repartir» con
        cada ficha de `_deal_stakes`. Cada jugada se hace sobre una copia de la mano
        (o con una copia del zapato), sin tocar el dinero. Va de uno en uno y cada dibujo
        comprueba antes de empezar que la mesa sigue igual (`Ahead`).
        """
        game = self.game
        if not self.cog.ahead or self.is_finished() or game is None:
            return
        scene, skin, streak = self.cog.renderer, self.skin(), self.streak

        async def paint_turn(item: Any) -> Media:
            kind, choice = item
            before, future = deepcopy(game), deepcopy(game)
            if kind == "insure":
                future.decide_insurance(choice)
            else:
                future.act(choice)
            resolve(future)
            after = streak
            if hand_over(future):
                future.settle()
                after = next_streak(streak, future)
            return await scene.step(before, future, streak=(streak, after), skin=skin)

        if game.insurance_pending:
            order: list[Any] = [("insure", False)]
            if game.can_insure():
                order.append(("insure", True))
            self.ahead.plan(self._key(), order, paint_turn)
            return
        if game.player_turn:
            order = [("act", action) for action in likely_actions(game)]
            self.ahead.plan(self._key(), order, paint_turn)
            return
        if not game.settled:
            return
        if self._next_shoe is None:
            self._next_shoe = self.cog.new_shoe()
        if self.ahead.key == self._key() and ("deal", self.stake) not in self.ahead.futures:
            # La ficha nueva no estaba entre las pintadas: se vuelve a planear con ella.
            self._deal_round += 1
        fresh_shoe, old_shoe, old_left = self._next_shoe, list(self.shoe), len(self.shoe)

        async def paint_deal(item: Any) -> Media:
            stake = item[1]
            caught, cut, _, _, fresh = self._shoe_plan(stake)
            future = BlackjackGame(stake=stake, shoe=list(fresh_shoe if fresh else old_shoe))
            future.deal()
            resolve(future)
            after = streak
            if hand_over(future):
                future.settle()
                after = next_streak(streak, future)
            shoe = Shoe(left=old_left, shuffle=self._shoe_change(caught, cut))
            return await scene.step(game, future, shoe=shoe, streak=(streak, after), skin=skin)

        order = [("deal", stake) for stake in self._deal_stakes()]
        self.ahead.plan(self._key(), order, paint_deal)

    # -- Dibujo ---------------------------------------------------------------------------

    async def _show(
        self,
        before: BlackjackGame | None,
        edit_fn: EditFn,
        *,
        ready: Media | None,
        streak: tuple[int, int],
        busy: str,
        shoe: Shoe | None = None,
        later: EditFn | None = None,
        balance: int | None = None,
    ) -> None:
        """Enseña el GIF de la jugada (de `before` a la mano de ahora).

        El GIF suena con los botones apagados y `busy` en el texto, y al acabar se cambia
        por el PNG de cómo queda la mesa, con los botones encendidos y, si la mano acabó,
        el resultado y el saldo (`balance`). Mientras se pinta (si no estaba pintado por
        adelantado, `ready`), la mesa ya apaga los botones, en paralelo al dibujo.

        Args:
            later: Cómo se cambia el mensaje después de la primera edición, si no es con
                `edit_fn` (el primer envío de `.bj` crea el mensaje y luego se edita).
        """
        game = self.game
        assert game is not None
        self._update_buttons(locked=True)

        async def show_waiting() -> bool:
            try:
                await edit_fn(embed=await self.current_embed(busy=busy), view=self)
            except discord.HTTPException:
                logger.warning("No se pudo apagar la mesa de blackjack", exc_info=True)
                return False
            return True

        media, shown = ready, False
        if media is None:
            media, shown = await asyncio.gather(
                self.cog.renderer.step(before, game, shoe=shoe, streak=streak, skin=self.skin()),
                show_waiting(),
            )
        after = later or edit_fn
        send = after if shown else edit_fn
        if media.gif is not None:
            await send(
                embed=await self.current_embed(busy=busy, image=GIF_NAME),
                attachments=[discord.File(io.BytesIO(media.gif), filename=GIF_NAME)],
                view=self,
            )
            self._image = GIF_NAME
            # Lo siguiente (otra jugada o repartir otra) se pinta mientras se ve esta.
            self.prepare()
            await asyncio.sleep(media.seconds + REVEAL_MARGIN_SECONDS)
            send = after
        self._update_buttons()
        await send(
            embed=await self.current_embed(balance, image=PNG_NAME),
            attachments=[discord.File(io.BytesIO(media.png), filename=PNG_NAME)],
            view=self,
        )
        self._image = PNG_NAME
        if media.gif is None:
            self.prepare()

    async def start(self, first_edit: EditFn, later: EditFn | None = None) -> str | None:
        """Cobra la ficha y reparte una mano nueva.

        Args:
            first_edit: Cómo se enseña la jugada (enviar el mensaje o editarlo).
            later: Cómo se cambia el mensaje después, si no es con `first_edit`.

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
        ready = await self.ahead.take(self._key(), ("deal", self.stake))
        self.ahead.drop()
        prev, old_left = self.game, len(self.shoe)
        self.game = BlackjackGame(stake=self.stake, shoe=self._shoe_for(self.stake))
        caught, cut = self._shoe_event[:2]  # type: ignore[index]
        shoe = None if prev is None else Shoe(left=old_left, shuffle=self._shoe_change(caught, cut))
        self.headline = None
        self.game.deal()
        await self._play(prev, first_edit, ready=ready, busy=DEAL_BUSY, shoe=shoe, later=later)
        return None

    async def _play(self, before: BlackjackGame | None, edit_fn: EditFn, **kwargs: Any) -> None:
        """Juega la banca si toca, paga la mano si acabó (sin enseñarla) y enseña el GIF."""
        game = self.game
        assert game is not None
        resolve(game)
        streak = self.streak
        balance = await self._settle(game) if hand_over(game) else None
        await self._show(before, edit_fn, streak=(streak, self.streak), balance=balance, **kwargs)
        await self._track()

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
            ready = await self.ahead.take(self._key(), ("act", action))
            self.ahead.drop()
            before = deepcopy(game)
            game.act(action)
            self._last_interaction = interaction
            busy = DEALER_BUSY if hand_over(game) else PLAYER_BUSY
            await self._play(before, interaction.edit_original_response, ready=ready, busy=busy)
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
            ready = await self.ahead.take(self._key(), ("insure", take))
            self.ahead.drop()
            before = deepcopy(game)
            game.decide_insurance(take)
            self._last_interaction = interaction
            await self._play(
                before, interaction.edit_original_response, ready=ready, busy=INSURE_BUSY
            )
        finally:
            self._busy = False
        if take or game.settled:
            await renta.remind(self.cog.bot, interaction)

    async def _settle(self, game: BlackjackGame) -> int:
        """Decide la mano, paga y actualiza la racha. Devuelve el saldo final.

        Si la mano ya se pagó (p. ej. el apagado la cerró mientras sonaba el GIF)
        no se vuelve a pagar.
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
        self._balance = balance
        self.streak = next_streak(self.streak, game)
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
        delta = blackjack_stats(game, streak=self.streak)
        if self._shoe_event is not None:
            caught, cut, count, spread = self._shoe_event
            delta.merge(
                blackjack_shoe_stats(game, caught=caught, cut=cut, count=count, spread=spread)
            )
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
        if balance is not None:
            self._balance = balance
        self._update_buttons()
        await edit(interaction, embed=await self.current_embed(balance), view=self)
        self._last_interaction = interaction
        # La ficha sale en la imagen de repartir: si no estaba pintada, se pinta ya.
        self.prepare()

    async def _halve(self, interaction: discord.Interaction) -> None:
        self.stake = max(1, self.stake // 2)
        await self._refresh(interaction)

    async def _double_stake(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        balance = await self.balance()
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

    async def _deal_again(self, interaction: discord.Interaction) -> None:
        if self._busy or (self.game is not None and not self.game.settled):
            await ack(interaction)
            return
        self._busy = True
        try:
            await ack(interaction)
            self._last_interaction = interaction
            error = await self.start(interaction.edit_original_response)
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
        renderer: BlackjackScene | None = None,
        shoe_factory: Callable[[], list[Card]] = new_shoe,
        casino_channel_ids: CasinoChannelIds = frozenset(),
        ahead: bool | None = None,
    ) -> None:
        """Prepara el cog; el dibujo por defecto es `BlackjackScene`.

        Args:
            ahead: Si las mesas pintan por adelantado el GIF de cada botón
                (`BlackjackTable.prepare`). Por defecto, solo con el dibujo de verdad:
                con un `renderer` de prueba, cada dibujo de más gastaría el azar de guion.
        """
        self.bot = bot
        self.economy = economy
        self.ahead = renderer is None if ahead is None else ahead
        self.renderer = renderer or BlackjackScene()
        self.new_shoe = shoe_factory
        self.casino_channel_ids = casino_channel_ids
        # Mesas abiertas: para resolver sus manos si el bot se apaga. Cada
        # mesa sale de aquí al caducar, así que el tamaño está acotado.
        self.tables: set[BlackjackTable] = set()
        #: Zapato de cada jugador `(servidor, miembro)` y su apuesta más baja: pasa de una
        #: mesa a la siguiente. Vive en memoria (un reinicio baraja) y es uno por jugador.
        self.shoes: dict[tuple[int, int], tuple[list[Card], int | None]] = {}

    async def cog_unload(self) -> None:
        """Al apagar, planta y paga las manos a medias para no quedarse con el dinero."""
        for table in list(self.tables):
            try:
                await table.force_settle()
            except Exception:
                logger.exception("No se pudo cerrar una mano de blackjack al apagar")
            table.stop()
        self.tables.clear()
        await self.renderer.close()

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
        if stake > balance:
            await send_error(insufficient_text(balance))
            return
        # Una mesa por jugador: dos con el mismo zapato estropearían lo pintado por adelantado.
        for old in [t for t in self.tables if (t.guild_id, t.owner.id) == (guild.id, user.id)]:
            if old._busy or (old.game is not None and not old.game.settled):
                where = getattr(old.message, "jump_url", None)
                await send_error(
                    "Acaba antes la mano de tu otra mesa" + (f": {where}" if where else ".")
                )
                return
            old.stop()  # que no atienda más clics mientras se cierra
            await old.on_timeout()

        table = BlackjackTable(self, guild_id=guild.id, owner=user, stake=stake)

        async def first_edit(**kwargs: Any) -> None:
            # Lo primero que se envía es la mesa «repartiendo…» (Discord da 3 s) o, si el
            # GIF ya está, con él. Lo siguiente, ya con mensaje, se edita.
            if attachments := kwargs.pop("attachments", None):
                kwargs["files"] = attachments
            table.message = await send(**kwargs)

        async def later(**kwargs: Any) -> None:
            assert table.message is not None
            await table.message.edit(**kwargs)

        # La mesa se apunta antes de cobrar: si Discord no deja enseñarla (la respuesta
        # llega tarde), la mano se cierra en el acto y nadie se queda con dinero colgado.
        self.tables.add(table)
        table._busy = True  # otra mesa que se abra mientras reparte no puede cerrarla
        try:
            error = await table.start(first_edit, later)
        except discord.HTTPException:
            self.tables.discard(table)
            table.stop()  # que no pinte por adelantado una mesa que nadie ve
            await table.force_settle()
            raise
        finally:
            table._busy = False
        if error is not None:
            self.tables.discard(table)
            await send_error(error)

    @app_commands.command(
        name="blackjack", description="Blackjack con tus yapdollars: reparte al momento."
    )
    @app_commands.describe(cantidad="Apuesta: 500, 2k, all… (por defecto 100)")
    @app_commands.guild_only()
    async def blackjack(
        self, interaction: discord.Interaction, cantidad: str | None = None
    ) -> None:
        """Reparte una mano de blackjack con botones para jugarla.

        Solo en los canales del casino que elige `garito` (sin elegir, los de `CASINO_CHANNEL_IDS`)
        y en sus hilos. Cobra la apuesta al repartir y paga al terminar la mano.
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
    await bot.add_cog(Blackjack(bot, economy=bot.economy, casino_channel_ids=bot.casino_channels))
