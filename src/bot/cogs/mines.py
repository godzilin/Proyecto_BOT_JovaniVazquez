"""Minas: `minas`, un tablero de 5×5 con diamantes y minas escondidas.

Cada jugador abre su propio tablero, que solo él puede pulsar. `minas 500 5`
cobra 500 Y$ y juega con 5 minas. La primera casilla siempre es segura y
devuelve la apuesta (retirarse ahí no cuenta como jugada: ni logros, ni porras, ni
cajas gratis); cada casilla segura después sube el multiplicador.
💰 **Cobrar** se lleva apuesta × multiplicador, y pisar una 💣 lo pierde
todo. 🎲 **Al azar** destapa una casilla cualquiera.

Mientras se juega, el texto lleva la cuenta de casillas (💎 7/23), lo que
añadiría la siguiente y su probabilidad, frases al pasar por 3, 5, 10,
medio tablero… y el aviso de récord personal de casillas en una partida.

Al acabar, el tablero enseña dónde estaban las minas y deja jugar otra con
🔁, cambiar la apuesta (½, ×2, 💰 All-in) y elegir de 1 a 12 minas en un
menú que dice cuánto paga cada opción (más minas, más riesgo, más pago).

El tablero usa los componentes nuevos de Discord (`LayoutView`): un bloque
con el texto y las 25 casillas como botones, una fila de botones y el menú
de minas. Son los 40 componentes que admite un mensaje. No hay imágenes: cada clic
es una edición de texto y botones, así que es instantáneo y casi no gasta
ancho de banda.

Reglas en `bot.services.mines`. Dinero: la apuesta se cobra al empezar
(`place_bet`) y se paga al cobrar o al explotar (`pay_winnings`, con 0 si
explota), que es cuando se ajusta el IRPF del día. Si el tablero caduca
(3 min sin tocarlo) o el bot se apaga de forma ordenada con una partida a
medias, se cobra sola; si no se había destapado nada, se devuelve la apuesta.

Solo se juega en los canales del casino que elige `garito` (sin elegir, los de `CASINO_CHANNEL_IDS`)
y en sus hilos. Permisos del bot en el canal: enviar mensajes.
"""

from __future__ import annotations

import logging
import random
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs.casino import casino_channel_error, insufficient_text
from bot.services.achievements import casino_stats, mines_stats
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
from bot.services.mines import (
    DEFAULT_MINES,
    MAX_MINES,
    MIN_MINES,
    SIZE,
    MinesError,
    MinesGame,
    Status,
    check_mines,
    format_multiplier,
    milestone,
    risk_summary,
)
from bot.services.pets import bet_moment
from bot.services.telemetry import TimedLock
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

GAME = "minas"
DEFAULT_STAKE = 100
BOARD_TIMEOUT = 180
#: Desde este multiplicador (en centésimas) se anuncia el premio en el canal.
SHOUT_CENTS = 2_500

HIDDEN = "❔"
GEM = "💎"
MINE = "💣"
BOOM = "💥"

COLOR_PLAYING = discord.Color.from_rgb(88, 101, 242)
COLOR_HOT = discord.Color.from_rgb(255, 196, 0)
COLOR_BUSTED = discord.Color.from_rgb(80, 84, 92)
COLOR_CASHED = discord.Color.from_rgb(255, 196, 0)

# Textos en el tono de Jovani Vázquez.
CASH_LINES = (
    "¡Cobras!",
    "¡Eso es!",
    "¡Qué olfato!",
    "¡Desminado con éxito!",
    "¡Toma mangó!",
    "¿Con pulpa? ¡Sin pulpa! ¡Con plata!",
    "¡Plantado a tiempo!",
    "Pies de plomo y bolsillo lleno.",
    "¡Mina de litio sin multinacional!",
    "Sabías cuándo parar.",
    "¡Te la llevas antes de que la vea Hacienda!",
    "¡Diamantes para el bolsillo!",
)
BOOM_LINES = (
    "¡BOOM!",
    "¡Kaboom!",
    "¡Se acabó la fiesta!",
    "Esa casilla no era.",
    "¡Te pilló la UCO!",
    "Estoy destruido, pero por fuera… por dentro estoy nuevo.",
    "¡Pisaste donde no debías!",
    "¡Mina de carbón cerrada por Bruselas!",
    "La avaricia pisa minas.",
    "¡Fuegos artificiales, pero de los caros!",
)
#: Estadística de logros con el récord de casillas en una partida.
RECORD_STAT = "mines_streak_max"


def percent(chance: float) -> str:
    """`0,88` → `88 %` (sin decimales, al estilo español)."""
    return f"{round(chance * 100)} %"


class MinesBoard(ui.LayoutView):
    """Tablero de un jugador: el texto, las 25 casillas y los controles.

    Guarda la partida en curso (o la última), la apuesta y las minas. No
    guarda dinero: se cobra y se paga siempre por la economía.
    """

    def __init__(
        self, cog: Mines, *, guild_id: int, owner: discord.abc.User, stake: int, mines: int
    ) -> None:
        super().__init__(timeout=BOARD_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        self.stake = stake
        self.mines = mines
        self.game: MinesGame | None = None
        self.balance = 0
        self.note: str | None = None
        self.message: discord.Message | None = None
        self.channel: object = None
        #: Récord de casillas del dueño al empezar la partida en curso.
        self.record_before = 0
        self._lock = TimedLock("mines:MinesBoard._lock")
        self._last_interaction: discord.Interaction | None = None

    # -- Dibujo -----------------------------------------------------------------------

    def header(self) -> str:
        """Texto de arriba: estado de la partida, multiplicadores y apuesta."""
        game = self.game
        name = self.owner.display_name
        lines = [f"### 💣 Minas · {name}"]
        if game is None:
            lines.append("Pulsa 🔁 para jugar.")
        elif game.playing:
            if game.gems:
                lines.append(
                    f"# {format_multiplier(game.cents)} · {format_amount(game.cashout_value)}"
                )
                progress = f"{GEM} **{game.gems}/{game.safe_total}**"
                if cheer := milestone(game.gems, game.safe_total):
                    progress += f" · {cheer}"
                lines.append(progress)
                if self.record_before and game.gems > self.record_before:
                    lines.append(
                        f"🏅 ¡Récord personal! Antes llegabas a {self.record_before} casillas."
                    )
            else:
                lines.append("# Elige una casilla")
                lines.append(f"-# La primera siempre es buena {GEM} y te devuelve la apuesta.")
            if game.gems and game.next_value is not None:
                extra = game.next_value - game.cashout_value
                lines.append(
                    f"-# Siguiente: {format_multiplier(game.next_cents or 0)} "
                    f"(+{format_amount(extra)}) · {percent(float(game.safe_chance))} "
                    "de que sea buena"
                )
        elif game.status is Status.CASHED:
            sign = "+" if game.net >= 0 else "-"
            if game.risk_free:
                lines.append(
                    "# ↩️ Te retiras sin arriesgar\nRecuperas tu apuesta "
                    f"(**{format_amount(game.payout)}**). Como la primera casilla siempre es "
                    "buena, no cuenta como jugada."
                )
            else:
                lines.append(
                    f"# {GEM} {random.choice(CASH_LINES)} {sign}{format_amount(abs(game.net))}\n"
                    f"Cobras **{format_amount(game.payout)}** en {format_multiplier(game.cents)} "
                    f"con {GEM} {game.gems}/{game.safe_total}"
                )
            if cheer := milestone(game.gems, game.safe_total):
                lines.append(cheer)
            if self.record_before and game.gems > self.record_before:
                lines.append(f"🏅 ¡Récord personal: {game.gems} casillas!")
        else:
            lines.append(f"# {BOOM} {random.choice(BOOM_LINES)} -{format_amount(game.stake)}")
            if game.gems:
                lines.append(
                    f"Llegaste a {GEM} {game.gems}/{game.safe_total}. "
                    f"Te ibas a llevar {format_amount(game.cashout_value)}."
                )
            if self.record_before and game.gems > self.record_before:
                lines.append(f"🏅 Aun así, récord personal: {game.gems} casillas.")
        if self.note:
            lines.append(self.note)
        stake = game.stake if game is not None and game.playing else self.stake
        mines = game.mines if game is not None and game.playing else self.mines
        lines.append(
            f"-# Apuesta {format_amount(stake)} · {mines} mina{'s' if mines != 1 else ''} · "
            f"Saldo {format_amount(self.balance)}"
        )
        if self.balance == 0 and (game is None or not game.playing):
            lines.append("**Estás a cero.** `imv` te recarga.")
        return "\n".join(lines)

    def color(self) -> discord.Color:
        """Color del borde según cómo va la partida."""
        game = self.game
        if game is None or game.playing:
            return COLOR_HOT if game is not None and game.cents >= 200 else COLOR_PLAYING
        return COLOR_CASHED if game.status is Status.CASHED else COLOR_BUSTED

    def tile_button(self, tile: int) -> ui.Button:
        """Una casilla, según lo que se sabe de ella."""
        game = self.game
        style = discord.ButtonStyle.secondary
        emoji = HIDDEN
        disabled = game is None or not game.playing
        if game is not None:
            over = not game.playing
            if tile in game.revealed:
                emoji, style, disabled = GEM, discord.ButtonStyle.success, True
            elif tile == game.exploded:
                emoji, style = BOOM, discord.ButtonStyle.danger
            elif over and tile in game.mine_tiles:
                emoji = MINE
            elif over:
                emoji = GEM
        button: ui.Button = ui.Button(
            emoji=emoji, style=style, disabled=disabled, custom_id=f"{GAME}:tile:{tile}"
        )
        button.callback = self._tile_callback(tile)  # type: ignore[method-assign]
        return button

    def control_button(
        self,
        label: str,
        custom_id: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        disabled: bool = False,
    ) -> ui.Button:
        """Un botón de la fila de controles."""
        button: ui.Button = ui.Button(
            label=label, style=style, disabled=disabled, custom_id=f"{GAME}:{custom_id}"
        )
        button.callback = callback  # type: ignore[method-assign]
        return button

    def rebuild(self) -> None:
        """Vuelve a montar los componentes con el estado actual."""
        self.clear_items()
        container = ui.Container(accent_colour=self.color())
        container.add_item(ui.TextDisplay(self.header()))
        container.add_item(ui.Separator())
        for row in range(SIZE):
            action_row: ui.ActionRow = ui.ActionRow()
            for col in range(SIZE):
                action_row.add_item(self.tile_button(row * SIZE + col))
            container.add_item(action_row)
        self.add_item(container)

        controls: ui.ActionRow = ui.ActionRow()
        game = self.game
        green, blue = discord.ButtonStyle.success, discord.ButtonStyle.primary
        if game is not None and game.playing:
            label = f"💰 Cobrar {format_amount(game.cashout_value)}" if game.gems else "💰 Cobrar"
            controls.add_item(
                self.control_button(
                    label, "cashout", self._cash_out, style=green, disabled=not game.gems
                )
            )
            controls.add_item(self.control_button("🎲 Al azar", "random", self._random, style=blue))
        else:
            controls.add_item(
                self.control_button(
                    f"🔁 Jugar · {format_amount(self.stake)}", "again", self._again, style=green
                )
            )
            controls.add_item(self.control_button("½", "half", self._halve))
            controls.add_item(self.control_button("×2", "x2", self._double))
            controls.add_item(self.control_button("💰 All-in", "allin", self._all_in))
        self.add_item(controls)
        if self._mines_editable():
            mines_row: ui.ActionRow = ui.ActionRow()
            mines_row.add_item(self.mines_select())
            self.add_item(mines_row)

    def mines_select(self) -> ui.Select:
        """Menú de 1 a 12 minas, con el premio de limpiar el tablero en cada opción."""
        options = [
            discord.SelectOption(
                label=f"💣 {m} mina{'s' if m != 1 else ''}",
                value=str(m),
                description=risk_summary(m),
                default=m == self.mines,
            )
            for m in range(MIN_MINES, MAX_MINES + 1)
        ]
        select: ui.Select = ui.Select(
            custom_id=f"{GAME}:mines", options=options, placeholder="Elige cuántas minas"
        )

        async def callback(interaction: discord.Interaction) -> None:
            await self._choose_mines(interaction, int(select.values[0]))

        select.callback = callback  # type: ignore[method-assign]
        return select

    def disable_all(self) -> None:
        """Apaga todos los botones y el menú (tablero caducado)."""
        for item in self.walk_children():
            if isinstance(item, ui.Button | ui.Select):
                item.disabled = True

    # -- Ciclo de vida ----------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño juega en su tablero."""
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message(
            f"Este tablero es de {self.owner.display_name}. Abre el tuyo con `minas`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Caduca: si había partida a medias, se cobra sola."""
        self.cog.boards.discard(self)
        await self.force_settle()
        self.rebuild()
        self.disable_all()
        try:
            if self._last_interaction is not None:
                await self._last_interaction.edit_original_response(view=self)
            elif self.message is not None:
                await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar el tablero de Minas", exc_info=True)

    async def force_settle(self) -> None:
        """Cierra la partida a medias sin que nadie pierda: cobra o devuelve."""
        async with self._lock:
            game = self.game
            if game is None or not game.playing:
                return
            if game.gems:
                game.cash_out()
                await self._settle(game)
                return
            # Ni una casilla destapada: se devuelve la apuesta (neto 0).
            try:
                await self.cog.economy.pay_winnings(
                    self.guild_id, self.owner.id, game=GAME, amount=game.stake
                )
            except BalanceLimitError:
                logger.warning("No se pudo devolver una apuesta de Minas.")
            self.game = None

    # -- Juego ------------------------------------------------------------------------

    async def start(self) -> str | None:
        """Cobra la apuesta y coloca las minas.

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
        self.started_at = time.time()
        self.game = MinesGame.new(self.stake, self.mines, self.cog.rng)
        self.record_before = await self.cog.record(self.guild_id, self.owner.id)
        self.note = None
        self.rebuild()
        return None

    async def _settle(self, game: MinesGame) -> BetSettlement | None:
        """Paga la partida terminada (0 si explotó) y deja preparado el resultado."""
        try:
            settlement = await self.cog.economy.pay_winnings(
                self.guild_id, self.owner.id, game=GAME, amount=game.payout
            )
        except BalanceLimitError:
            logger.warning("Premio de Minas por encima del saldo máximo; no se paga.")
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
        self, interaction: discord.Interaction, game: MinesGame, settlement: BetSettlement | None
    ) -> None:
        """Lo que va después de enseñar el final: renta, récord, logros y anuncio."""
        await renta.remind(self.cog.bot, interaction)
        if game.risk_free:
            # Retirarse en la casilla segura es una devolución: ni logros, ni porras, ni cajas.
            return
        self.cog.note_record(self.guild_id, self.owner.id, game.gems)
        delta = mines_stats(game)
        delta.merge(
            casino_stats(
                stake=game.stake,
                net=game.net,
                balance_after=settlement.balance if settlement else self.balance,
                tax_delta=settlement.tax_delta if settlement else 0,
            )
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
            balance_after=settlement.balance if settlement else self.balance,
            tax=settlement.tax_delta if settlement else 0,
            details=(
                ("boom", int(game.status is Status.BUSTED)),
                ("started", int(getattr(self, "started_at", 0))),
            ),
        )
        await self.cog.shout(game, self.owner, self.channel)

    async def _reveal(self, interaction: discord.Interaction, tile: int | None) -> None:
        """Destapa `tile` (o una al azar) y enseña el resultado."""
        if self._lock.locked():
            # Doble clic mientras se procesa el anterior: se ignora.
            await ack(interaction)
            return
        async with self._lock:
            game = self.game
            if game is None or not game.playing:
                await ack(interaction)
                return
            random_pick = tile is None
            try:
                target = game.random_hidden(self.cog.rng) if tile is None else tile
                safe = game.reveal(target, random_pick=random_pick)
            except MinesError:
                await ack(interaction)
                return
            settlement = None
            if not safe or game.cleared:
                # Pagar va a la base de datos: se acepta el clic antes. Una casilla
                # buena sin más se pinta directamente, que es lo más rápido.
                await ack(interaction)
            if not safe:
                settlement = await self._settle(game)
            elif game.cleared:
                game.cash_out()
                settlement = await self._settle(game)
            else:
                self.note = None
            self._last_interaction = interaction
            self.rebuild()
            await edit(interaction, view=self)
        if not game.playing:
            await self._after_game(interaction, game, settlement)

    def _tile_callback(self, tile: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await self._reveal(interaction, tile)

        return callback

    async def _random(self, interaction: discord.Interaction) -> None:
        await self._reveal(interaction, None)

    async def _cash_out(self, interaction: discord.Interaction) -> None:
        """💰 Cobrar: se retira con el multiplicador actual."""
        if self._lock.locked():
            await ack(interaction)
            return
        async with self._lock:
            game = self.game
            if game is None or not game.playing or not game.gems:
                await ack(interaction)
                return
            await ack(interaction)
            game.cash_out()
            settlement = await self._settle(game)
            self._last_interaction = interaction
            self.rebuild()
            await edit(interaction, view=self)
        await self._after_game(interaction, game, settlement)

    async def _again(self, interaction: discord.Interaction) -> None:
        """🔁 Jugar: cobra otra vez y coloca minas nuevas."""
        if self._lock.locked():
            await ack(interaction)
            return
        async with self._lock:
            if self.game is not None and self.game.playing:
                await ack(interaction)
                return
            await ack(interaction)
            error = await self.start()
            if error is not None:
                await notify(interaction, error)
                return
            self._last_interaction = interaction
            await edit(interaction, view=self)
        await renta.remind(self.cog.bot, interaction)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        """Pinta el tablero tras cambiar apuesta o minas (sin dinero de por medio).

        Lee el saldo de la base de datos: se acepta el clic antes.
        """
        await ack(interaction)
        self.balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
        self.note = None
        self.rebuild()
        self._last_interaction = interaction
        await edit(interaction, view=self)

    def _idle(self) -> bool:
        return self.game is None or not self.game.playing

    def _mines_editable(self) -> bool:
        """Si el menú 💣 se puede usar: sin partida o antes de destapar la primera.

        El comando ya cobra la apuesta y empieza la partida, así que sin esto el
        menú no salía la primera vez. Las minas se colocan en el primer clic,
        de modo que cambiarlas antes no altera nada de lo ya jugado.
        """
        game = self.game
        return game is None or not game.playing or not game.revealed

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

    async def _choose_mines(self, interaction: discord.Interaction, mines: int) -> None:
        """Menú 💣: cambia las minas de la partida recién empezada o de la siguiente.

        Con la partida empezada y ninguna casilla destapada, se rehace con las
        minas nuevas y la misma apuesta, que ya está cobrada: no se mueve dinero.
        """
        if self._lock.locked() or not self._mines_editable():
            await ack(interaction)
            return
        try:
            check_mines(mines)
        except ValueError:
            await ack(interaction)
            return
        async with self._lock:
            game = self.game
            if game is not None and game.playing:
                if game.revealed:
                    await ack(interaction)
                    return
                self.game = MinesGame.new(game.stake, mines, self.cog.rng)
            self.mines = mines
            self.cog.set_mines(self.guild_id, self.owner.id, mines)
            await self._refresh(interaction)


# -- Cog -------------------------------------------------------------------------------


class Mines(commands.Cog, name="Minas"):
    """Minas con los yapdollars de la economía del bot."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        casino_channel_ids: CasinoChannelIds = frozenset(),
        rng: random.Random | None = None,
        load_record: Callable[[int, int], Awaitable[int]] | None = None,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.casino_channel_ids = casino_channel_ids
        # `secrets` usa el azar del sistema operativo: no se puede predecir.
        self.rng = rng or secrets.SystemRandom()
        # Tableros abiertos: para cerrar sus partidas si el bot se apaga.
        # Cada tablero sale de aquí al caducar, así que el tamaño está acotado.
        self.boards: set[MinesBoard] = set()
        # Minas elegidas por (servidor, miembro); se pierden al reiniciar.
        self._mines: dict[tuple[int, int], int] = {}
        # Récord de casillas por (servidor, miembro). Se lee una vez de los
        # logros (`mines_streak_max`, que sobrevive a reinicios) y luego se
        # lleva en memoria. Crece como mucho hasta los miembros que han jugado.
        self._records: dict[tuple[int, int], int] = {}
        self._load_record = load_record

    async def record(self, guild_id: int, user_id: int) -> int:
        """Récord de casillas de un miembro en una partida (0 si no se sabe)."""
        key = (guild_id, user_id)
        if key not in self._records:
            value = 0
            if self._load_record is not None:
                try:
                    value = await self._load_record(guild_id, user_id)
                except Exception:
                    logger.exception("No se pudo leer el récord de Minas de %s", user_id)
            self._records[key] = value
        return self._records[key]

    def note_record(self, guild_id: int, user_id: int, gems: int) -> None:
        """Apunta las casillas de una partida terminada si baten el récord."""
        key = (guild_id, user_id)
        self._records[key] = max(self._records.get(key, 0), gems)

    def mines_for(self, guild_id: int, user_id: int) -> int:
        """Número de minas que eligió un miembro la última vez."""
        return self._mines.get((guild_id, user_id), DEFAULT_MINES)

    def set_mines(self, guild_id: int, user_id: int, mines: int) -> None:
        """Recuerda el número de minas de un miembro."""
        self._mines[(guild_id, user_id)] = mines

    async def cog_unload(self) -> None:
        """Al apagar, cobra o devuelve las partidas a medias."""
        for board in list(self.boards):
            try:
                await board.force_settle()
            except Exception:
                logger.exception("No se pudo cerrar una partida de Minas al apagar")
            board.stop()
        self.boards.clear()

    async def shout(self, game: MinesGame, user: discord.abc.User, channel: object) -> None:
        """Anuncia en el canal los cobros enormes, para que se vea."""
        if game.status is not Status.CASHED or game.cents < SHOUT_CENTS:
            return
        if not isinstance(channel, discord.abc.Messageable):
            return
        text = (
            f"📣 {GEM} ¡{user.mention} ha cobrado **{format_multiplier(game.cents)}** en Minas "
            f"con {game.mines} mina{'s' if game.mines != 1 else ''}! "
            f"**+{format_amount(game.net)}**"
        )
        try:
            await channel.send(
                text, allowed_mentions=discord.AllowedMentions(users=[user], everyone=False)
            )
        except discord.HTTPException:
            logger.debug("No se pudo anunciar un premio de Minas", exc_info=True)

    # -- minas -----------------------------------------------------------------------

    async def _minas_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        mines: int | None,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> None:
        """Lógica compartida de `/minas` y `.minas`: abre el tablero y empieza ya."""
        if guild is None:
            await send_error("Minas solo se juega dentro de un servidor.")
            return
        if error := casino_channel_error(self.casino_channel_ids, channel, "Minas"):
            await send_error(error)
            return
        if mines is not None:
            try:
                check_mines(mines)
            except ValueError as error:
                await send_error(str(error))
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
        if mines is not None:
            self.set_mines(guild.id, user.id, mines)

        board = MinesBoard(
            self,
            guild_id=guild.id,
            owner=user,
            stake=stake,
            mines=self.mines_for(guild.id, user.id),
        )
        board.channel = channel
        error = await board.start()
        if error is not None:
            await send_error(error)
            return
        board.message = await send(view=board)
        self.boards.add(board)

    @staticmethod
    def parse_mines(text: str | None) -> int | None:
        """`5` → 5; `None` si no se indicó.

        Raises:
            ValueError: Si no es un número de minas permitido.
        """
        if text is None:
            return None
        value = text.strip().lower().removesuffix("m")
        if not value.isdigit():
            raise ValueError(f"Las minas van de {MIN_MINES} a {MAX_MINES}.")
        check_mines(int(value))
        return int(value)

    @app_commands.command(
        name="minas", description="Minas: destapa diamantes y cobra antes de pisar una mina."
    )
    @app_commands.describe(
        cantidad="Apuesta: 500, 2k, all… (por defecto 100)",
        minas="De 1 a 12: más minas, más riesgo y más premio (por defecto, la última vez o 2)",
    )
    @app_commands.guild_only()
    async def minas(
        self,
        interaction: discord.Interaction,
        cantidad: str | None = None,
        minas: app_commands.Range[int, MIN_MINES, MAX_MINES] | None = None,
    ) -> None:
        """Abre un tablero de Minas y empieza la partida.

        Solo en los canales del casino que elige `garito` (sin elegir, los de `CASINO_CHANNEL_IDS`)
        y en sus hilos. Cobra la apuesta al empezar y paga al cobrar.
        """

        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            return await interaction.original_response()

        await self._minas_impl(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            mines=minas,
            send=send,
            send_error=InteractionResponder(interaction).send_error,
        )
        await renta.remind(self.bot, interaction)

    @commands.command(name="minas")
    @commands.guild_only()
    async def minas_text(
        self, ctx: commands.Context, cantidad: str | None = None, minas: str | None = None
    ) -> None:
        """Versión de texto: `.minas`, `.minas 500` o `.minas 500 5`."""
        responder = ContextResponder(ctx)
        try:
            mines = self.parse_mines(minas)
        except ValueError as error:
            await responder.send_error(str(error))
            return

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        await self._minas_impl(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            amount_text=cantidad,
            mines=mines,
            send=send,
            send_error=responder.send_error,
        )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""

    async def load_record(guild_id: int, user_id: int) -> int:
        profile = await bot.achievements.profile(guild_id, user_id)
        return profile.stats.get(RECORD_STAT, 0)

    await bot.add_cog(
        Mines(
            bot,
            economy=bot.economy,
            casino_channel_ids=bot.casino_channels,
            load_record=load_record,
        )
    )
