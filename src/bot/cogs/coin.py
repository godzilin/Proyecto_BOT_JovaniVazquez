"""Cara o cruz: `moneda`, doble o nada con una moneda que a veces cae de canto.

Cada jugador abre su propia mesa, que solo él toca. `moneda 500` deja la
apuesta en 500 Y$ y espera a que se elija lado: 👑 **Cara** o ✈️ **Cruz**
cobran la apuesta y lanzan. Si aciertas, lo que hay en juego se dobla y
puedes 💰 **Cobrar** o volver a pulsar Cara o Cruz para jugártelo otra vez.
Si fallas, lo pierdes todo. A las diez seguidas (×1.024) se cobra sola la
moneda de oro. `moneda 500 cara` lanza directamente.

Uno de cada cien lanzamientos la moneda cae **de canto**: se queda de pie,
Perro Sanxe la sella y se pierde lo que hubiera en juego. Es la ventaja de la
casa (ver `bot.services.coin`).

Cada lanzamiento es un GIF: la moneda sube girando, cae, rebota y se asienta
(o se tambalea y se queda de pie). El vuelo dura más cuanto más hay en
juego. Después el bot cambia el GIF por un PNG con el resultado y vuelve a
activar los botones. El dibujo lo hace Node con canvas
(`bot.services.coin_scene`), con Chromium o Pillow de reserva.

Para que el clic se note al instante, la mesa pinta por adelantado los dos GIF
posibles de la siguiente tirada (pidiendo cara y pidiendo cruz). Se puede
porque el resultado se sortea antes de pulsar (`CoinGame.upcoming`; el de la
primera tirada lo sortea la mesa, `_opening`) y no se enseña hasta lanzar. Al
pulsar solo queda subir el GIF.

Al cobrar, el texto dice cómo habría caído la siguiente («🔮 la siguiente
habría salido cruz»), que es lo que hace volver a jugar.

Dinero: la apuesta se cobra al lanzar la primera (`place_bet`) y se paga al
cobrar, al fallar o al caer de canto (`pay_winnings`, con 0 si se pierde),
que es cuando se ajusta el IRPF del día. Si la mesa caduca (3 min sin
tocarla) o el bot se apaga de forma ordenada con una racha a medias, se
cobra sola: nunca hay partida empezada sin un acierto.

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
from datetime import datetime
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs.casino import casino_channel_error, insufficient_text
from bot.services.achievements import casino_stats, coin_stats
from bot.services.coin import (
    EDGE_CHANCE,
    CoinError,
    CoinGame,
    Outcome,
    Side,
    Status,
    format_multiplier,
    milestone,
    multiplier,
    parse_side,
    toss,
    win_chance,
)
from bot.services.coin_render import Media
from bot.services.coin_scene import CoinScene
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

GAME = "moneda"
DEFAULT_STAKE = 100
VIEW_TIMEOUT = 180
#: Desde este multiplicador se anuncia el cobro en el canal (cinco seguidas).
SHOUT_MULTIPLIER = 32
#: Margen tras el GIF antes de poner el PNG: lo que tarda en llegar y empezar.
REVEAL_MARGIN_SECONDS = 0.5
GIF_NAME = "moneda.gif"
PNG_NAME = "moneda.png"

COLOR_IDLE = discord.Color.from_rgb(88, 101, 242)
COLOR_HOT = discord.Color.from_rgb(255, 196, 0)
COLOR_LOST = discord.Color.from_rgb(80, 84, 92)
COLOR_EDGE = discord.Color.from_rgb(123, 75, 214)

# Textos en el tono de Jovani Vázquez.
WIN_LINES = ("¡Wepa!", "¡Eso es, mi amor!", "¡Acertaste!", "¡Dale!", "¡Qué suerte, bendito!")
CASH_LINES = ("¡Wepa!", "¡Cobras, mi amor!", "¡A la saca!", "¡Plata en mano!")
LOSE_LINES = ("¡Ay, bendito!", "¡Fallaste!", "¡Se acabó la racha!", "¡Nooo!")
EDGE_LINES = (
    "La moneda se ha quedado **de pie**. {collector} la sella y se la queda.",
    "**De canto.** Ni cara ni cruz: Hacienda. {collector} ya la ha embargado.",
    "¿De canto? {collector} lo llama «ajuste técnico» y se lleva la moneda.",
)
FLYING_LINES = (
    "🪙 La moneda vuela…",
    "🪙 ¡Arriba!",
    "🙏 Que salga, que salga…",
    "😬 Gira, gira, gira…",
    "👀 Todos miran la moneda…",
)


def percent(chance: float) -> str:
    """`0,495` → `49,5 %`."""
    return f"{chance * 100:.1f}".replace(".", ",").replace(",0", "") + " %"


class CoinView(ui.View):
    """Mesa de un jugador: imagen, texto y botones.

    Guarda la partida en curso (o la última), la apuesta y la cara que quedó
    arriba (para que el siguiente lanzamiento empiece desde ahí). No guarda
    dinero: se cobra y se paga siempre por la economía.
    """

    def __init__(self, cog: Coin, *, guild_id: int, owner: discord.abc.User, stake: int) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        self.stake = stake
        self.game: CoinGame | None = None
        self.face: Side | Outcome = Side.CARA
        self.balance = 0
        self.note: str | None = None
        self.message: discord.Message | None = None
        self.channel: object = None
        self.started_at = 0.0
        self._busy = False
        self._lock = asyncio.Lock()
        self._last_interaction: discord.Interaction | None = None
        #: Primer lanzamiento de la próxima partida, sorteado por adelantado.
        self._opening: Outcome | None = None
        #: Semilla del vuelo de la próxima tirada, sorteada por adelantado.
        self._seed: int | None = None
        #: GIF pintados por adelantado: para qué estado valen y uno por lado pedido.
        self._plan: tuple[tuple[Any, ...], dict[Side, asyncio.Future[Media | None]]] | None = None
        self._painting: asyncio.Task[None] | None = None

    # -- Texto ------------------------------------------------------------------------

    def resting_side(self) -> Side:
        """La cara que hay arriba antes de lanzar (de canto se vuelve a la corona)."""
        if isinstance(self.face, Side):
            return self.face
        return self.face.side or Side.CARA

    def description(self) -> str:
        """Texto del embed según cómo va la partida."""
        game = self.game
        lines: list[str] = []
        if game is None:
            lines.append("# 🪙 ¿Cara o cruz?")
            lines.append(
                "Si aciertas, doblas. Luego cobras o te la juegas otra vez: doble o nada.\n"
                f"-# Una de cada {round(1 / EDGE_CHANCE)} cae de canto y se la queda "
                f"{TAX_COLLECTOR}."
            )
        elif game.playing:
            last = game.last
            assert last is not None
            lines.append(f"# {format_multiplier(game.multiplier)} · {format_amount(game.pot)}")
            streak = f"{last.outcome.label}: **{random.choice(WIN_LINES)}**"
            if game.wins > 1:
                streak += f" · 🔥 {game.wins} seguidas"
            lines.append(streak)
            if cheer := milestone(game.wins):
                lines.append(cheer)
            nxt = multiplier(game.wins + 1)
            lines.append(
                f"-# Siguiente: {format_multiplier(nxt)} (+{format_amount(game.pot)}) · "
                f"{percent(float(win_chance()))} de acertar"
            )
        elif game.status is Status.CASHED:
            lines.append(f"# 💰 {random.choice(CASH_LINES)} +{format_amount(game.net)}")
            lines.append(
                f"Cobras **{format_amount(game.payout)}** en {format_multiplier(game.multiplier)} "
                f"tras {game.wins} acierto{'s' if game.wins != 1 else ''}."
            )
            if game.maxed and (cheer := milestone(game.wins)):
                lines.append(cheer)
            elif not game.maxed:
                lines.append(f"🔮 La siguiente habría salido **{game.upcoming.label}**.")
        elif game.status is Status.EDGE:
            lines.append(f"# 🐶 ¡DE CANTO! -{format_amount(game.stake)}")
            lines.append(random.choice(EDGE_LINES).format(collector=TAX_COLLECTOR))
            if game.wins:
                lines.append(
                    f"Llevabas {format_multiplier(game.multiplier)}: "
                    f"{format_amount(game.pot)} para el Estado… o eso dicen."
                )
        else:
            last = game.last
            assert last is not None
            lines.append(f"# 💥 {random.choice(LOSE_LINES)} -{format_amount(game.stake)}")
            lines.append(f"Ha salido **{last.outcome.label}** y pediste {last.pick.label.lower()}.")
            if game.wins:
                lines.append(
                    f"Llevabas {format_multiplier(game.multiplier)}: "
                    f"te ibas a llevar {format_amount(game.pot)}."
                )
        if self.note:
            lines.append(self.note)
        lines.append(self.footer())
        if self.balance == 0 and (game is None or not game.playing):
            lines.append("**Estás a cero.** `imv` te recarga.")
        return "\n".join(lines)

    def footer(self) -> str:
        """Línea pequeña: apuesta y saldo."""
        game = self.game
        stake = game.stake if game is not None and game.playing else self.stake
        return f"-# Apuesta {format_amount(stake)} · Saldo {format_amount(self.balance)}"

    def color(self) -> discord.Color:
        game = self.game
        if game is None:
            return COLOR_IDLE
        if game.playing or game.status is Status.CASHED:
            return COLOR_HOT
        return COLOR_EDGE if game.status is Status.EDGE else COLOR_LOST

    def embed(
        self, *, image: str = PNG_NAME, text: str | None = None, color: discord.Color | None = None
    ) -> discord.Embed:
        """Embed con la imagen adjunta (`attachment://`)."""
        embed = discord.Embed(
            title=f"🪙 Cara o cruz · {self.owner.display_name}",
            description=text if text is not None else self.description(),
            color=color or self.color(),
        )
        embed.set_image(url=f"attachment://{image}")
        return embed

    def flying_text(self, pick: Side) -> str:
        """Texto mientras se ve el GIF, preparado ANTES de lanzar: no delata nada."""
        game = self.game
        lines = [f"# {random.choice(FLYING_LINES)}", f"Pides **{pick.emoji} {pick.label}**"]
        if game is not None and game.playing:
            lines.append(f"A por {format_multiplier(game.multiplier * 2)}…")
        lines.append(self.footer())
        return "\n".join(lines)

    # -- Componentes ------------------------------------------------------------------

    def rebuild(self, *, busy: bool = False) -> None:
        """Vuelve a montar los botones con el estado actual."""
        self.clear_items()
        game = self.game
        playing = game is not None and game.playing
        for side in Side:
            if playing:
                assert game is not None
                label = f"{side.emoji} {side.label} · {format_multiplier(game.multiplier * 2)}"
            else:
                label = f"{side.emoji} {side.label} · {format_amount(self.stake)}"
            self._button(
                label,
                side.key,
                self._flip_cara if side is Side.CARA else self._flip_cruz,
                style=discord.ButtonStyle.primary,
                row=0,
                disabled=busy,
            )
        if playing:
            assert game is not None
            self._button(
                f"💰 Cobrar {format_amount(game.pot)}",
                "cashout",
                self._cash_out,
                style=discord.ButtonStyle.success,
                row=0,
                disabled=busy,
            )
            return
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
            f"Esta moneda es de {self.owner.display_name}. Saca la tuya con `moneda`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Caduca: si había una racha a medias, se cobra sola."""
        self.cog.views.discard(self)
        await self.force_settle()
        self.rebuild()
        self.disable_all()
        try:
            if self._last_interaction is not None:
                await self._last_interaction.edit_original_response(embed=self.embed(), view=self)
            elif self.message is not None:
                await self.message.edit(embed=self.embed(), view=self)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar la mesa de la moneda", exc_info=True)

    async def force_settle(self) -> None:
        """Cobra la racha a medias (siempre lleva al menos un acierto)."""
        async with self._lock:
            game = self.game
            if game is None or not game.playing:
                return
            game.cash_out()
            await self._settle(game)

    # -- GIF por adelantado -------------------------------------------------------------

    def _plan_key(self) -> tuple[Any, ...]:
        """Lo que decide el GIF de la próxima tirada, salvo el lado que se pida."""
        game = self.game
        if game is not None and game.playing:
            return (id(game), len(game.flips), game.upcoming, self.resting_side(), self._seed)
        return (None, self.stake, self._opening, self.resting_side(), self._seed)

    def _predict(self, pick: Side) -> CoinGame:
        """La partida tal como quedará si se pide `pick` (sobre una copia)."""
        game = self.game
        if game is not None and game.playing:
            future = deepcopy(game)
        else:
            assert self._opening is not None
            future = CoinGame.new(self.stake, self.cog.rng, upcoming=self._opening)
        # El siguiente sorteo de la copia no se dibuja: vale cualquier azar.
        future.flip(pick, random.Random(0))
        if future.playing and future.maxed:
            future.cash_out()
        return future

    def prepare(self) -> None:
        """Pinta en segundo plano los GIF de la próxima tirada, si no lo están ya.

        Se llama en cuanto sale el GIF de una tirada (la siguiente moneda ya está
        sorteada, así que se pinta mientras se ve esta), al cobrar, al abrir la
        mesa y al cambiar la apuesta. Los dos
        lados se pintan uno detrás de otro (primero el último que se pidió) y
        nunca se cancelan a medias, porque el pintor no admite cortar un dibujo:
        si mientras tanto la mesa cambia, el que aún no ha empezado no se pinta.
        """
        if not self.cog.ahead or self.is_finished():
            return
        game = self.game
        if (game is None or not game.playing) and self._opening is None:
            self._opening = toss(self.cog.rng)
        if self._seed is None:
            self._seed = self.cog.rng.randrange(1, 2**31)
        key = self._plan_key()
        if self._plan is not None and self._plan[0] == key:
            return
        loop = asyncio.get_running_loop()
        last = game.last.pick if game is not None and game.last is not None else Side.CARA
        order = [last, last.other]
        futures: dict[Side, asyncio.Future[Media | None]] = {
            side: loop.create_future() for side in order
        }
        self._plan = (key, futures)
        previous = self._painting
        self._painting = asyncio.create_task(self._paint_ahead(key, order, futures, previous))

    async def _paint_ahead(
        self,
        key: tuple[Any, ...],
        order: list[Side],
        futures: dict[Side, asyncio.Future[Media | None]],
        previous: asyncio.Task[None] | None,
    ) -> None:
        # Un plan detrás de otro: así un plan viejo nunca hace esperar dos dibujos.
        if previous is not None:
            await asyncio.gather(previous, return_exceptions=True)
        for pick in order:
            media: Media | None = None
            if self._plan_key() == key and not self.is_finished():
                try:
                    media = await self.cog.renderer.toss(
                        self._predict(pick), start=key[3], seed=key[4]
                    )
                except Exception:
                    logger.warning("No se pudo pintar por adelantado la moneda", exc_info=True)
            if not futures[pick].done():
                futures[pick].set_result(media)

    def _take_plan(self, pick: Side) -> asyncio.Future[Media | None] | None:
        """El GIF por adelantado de pedir `pick`, si vale para la mesa tal como está."""
        plan, self._plan = self._plan, None
        if plan is None or plan[0] != self._plan_key():
            return None
        return plan[1].get(pick)

    # -- Dinero y partida -------------------------------------------------------------

    async def _start(self) -> str | None:
        """Cobra la apuesta y prepara una partida nueva.

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
        self.game = CoinGame.new(self.stake, self.cog.rng, upcoming=self._opening)
        self._opening = None
        self.note = None
        return None

    async def _settle(self, game: CoinGame) -> BetSettlement | None:
        """Paga la partida terminada (0 si falló o cayó de canto) y prepara la nota fiscal."""
        try:
            settlement = await self.cog.economy.pay_winnings(
                self.guild_id, self.owner.id, game=GAME, amount=game.payout
            )
        except BalanceLimitError:
            logger.warning("Premio de la moneda por encima del saldo máximo; no se paga.")
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
        game: CoinGame,
        settlement: BetSettlement | None,
    ) -> None:
        """Lo que va después de enseñar el final: renta, logros, estadísticas y anuncio."""
        if interaction is not None:
            await renta.remind(self.cog.bot, interaction)
        balance = settlement.balance if settlement else self.balance
        tax = settlement.tax_delta if settlement else 0
        delta = coin_stats(game, when=datetime.now(TIMEZONE))
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
                ("edge", int(game.status is Status.EDGE)),
                ("started", int(self.started_at)),
            ),
        )
        await self.cog.shout(game, self.owner, self.channel)

    async def _show_toss(
        self,
        editor: Callable[..., Awaitable[Any]],
        *,
        start: Side,
        waiting: str,
        seed: int,
        ready: asyncio.Future[Media | None] | None = None,
    ) -> None:
        """Enseña el GIF del lanzamiento y después el PNG con el resultado.

        Mientras se pinta el GIF (1-2 s), la mesa ya enseña la jugada con los
        botones apagados: si no, tras el clic no cambia nada y parece que el botón
        no responde. Los botones se apagan sin reconstruirlos, con las etiquetas
        de antes de lanzar, porque las nuevas (×8 o la apuesta) delatarían el
        resultado.

        Si el GIF ya estaba pintado por adelantado (`ready`), va directo, sin ese
        paso intermedio: una ida y vuelta menos a Discord.
        """
        game = self.game
        assert game is not None
        for item in self.children:
            if isinstance(item, ui.Button):
                item.disabled = True

        async def show_waiting() -> None:
            try:
                await editor(embed=self.embed(text=waiting, color=COLOR_IDLE), view=self)
            except discord.HTTPException:
                logger.warning("No se pudo apagar la mesa de la moneda", exc_info=True)

        async def paint() -> Media:
            media = await ready if ready is not None else None
            if media is None:
                media = await self.cog.renderer.toss(game, start=start, seed=seed)
            return media

        media: Media
        if ready is not None and ready.done() and ready.result() is not None:
            media = await paint()
        else:
            _, media = await asyncio.gather(show_waiting(), paint())
        await editor(
            # Color neutro: el del final delataría el resultado antes del GIF.
            embed=self.embed(image=GIF_NAME, text=waiting, color=COLOR_IDLE),
            attachments=[discord.File(io.BytesIO(media.gif), filename=GIF_NAME)],
            view=self,
        )
        # La siguiente moneda ya está sorteada: se pinta mientras se ve esta.
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
        pick: Side,
        editor: Callable[..., Awaitable[Any]],
        interaction: discord.Interaction | None = None,
    ) -> str | None:
        """Lanza pidiendo `pick`: empieza partida si no la hay, enseña el GIF y liquida.

        Args:
            editor: Cómo se cambia el mensaje de la mesa (la interacción ya
                aceptada o el mensaje del comando de texto).
            interaction: La del botón, para el aviso de la Renta.

        Returns:
            Un error para el usuario si no se pudo lanzar, o `None`.
        """
        if self._busy:
            return None
        self._busy = True
        settlement: BetSettlement | None = None
        game: CoinGame | None = None
        try:
            async with self._lock:
                ready = self._take_plan(pick)
                if self.game is None or not self.game.playing:
                    if error := await self._start():
                        return error
                game = self.game
                assert game is not None
                start = self.resting_side()
                waiting = self.flying_text(pick)
                seed = self._seed if self._seed is not None else self.cog.rng.randrange(1, 2**31)
                self._seed = None
                try:
                    game.flip(pick, self.cog.rng)
                except CoinError:
                    return None
                last = game.last
                assert last is not None
                self.face = last.outcome
                if game.playing and game.maxed:
                    game.cash_out()
                if not game.playing:
                    settlement = await self._settle(game)
                else:
                    self.note = None
                if interaction is not None:
                    self._last_interaction = interaction
            await self._show_toss(editor, start=start, waiting=waiting, seed=seed, ready=ready)
        except discord.HTTPException:
            logger.warning("No se pudo enseñar un lanzamiento de la moneda", exc_info=True)
        finally:
            self._busy = False
        if game is not None and not game.playing:
            await self._after_game(interaction, game, settlement)
        elif interaction is not None and game is not None and len(game.flips) == 1:
            # Primera tirada: ya se ha cobrado la apuesta.
            await renta.remind(self.cog.bot, interaction)
        return None

    async def _flip(self, interaction: discord.Interaction, pick: Side) -> None:
        """👑 Cara o ✈️ Cruz: lanza (y empieza partida si hacía falta)."""
        # Cobrar la apuesta y dibujar van antes que nada: se acepta el clic ya.
        await ack(interaction)
        if self._busy:
            return

        async def editor(**kwargs: Any) -> None:
            await edit(interaction, **kwargs)

        if error := await self.play(pick, editor, interaction):
            await notify(interaction, error)

    async def _flip_cara(self, interaction: discord.Interaction) -> None:
        await self._flip(interaction, Side.CARA)

    async def _flip_cruz(self, interaction: discord.Interaction) -> None:
        await self._flip(interaction, Side.CRUZ)

    async def _cash_out(self, interaction: discord.Interaction) -> None:
        """💰 Cobrar: se retira con lo que hay en juego."""
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
            await self._board(interaction)
            self.prepare()
        finally:
            self._busy = False
        await self._after_game(interaction, game, settlement)

    async def _board(self, interaction: discord.Interaction) -> None:
        """Pone el PNG de la mesa quieta (sin animación)."""
        png = await self.cog.renderer.board(self.game, stake=self.stake, face=self.face)
        self.rebuild()
        await edit(
            interaction,
            embed=self.embed(),
            attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
            view=self,
        )

    def _idle(self) -> bool:
        return not self._busy and (self.game is None or not self.game.playing)

    async def _restake(self, interaction: discord.Interaction, stake: int) -> None:
        """Cambia la apuesta y repinta el texto y los botones (la imagen se queda)."""
        self.stake = max(1, stake)
        self.note = None
        self._last_interaction = interaction
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


class Coin(commands.Cog, name="Moneda"):
    """Cara o cruz (doble o nada) con los yapdollars de la economía del bot."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        casino_channel_ids: frozenset[int] = frozenset(),
        rng: random.Random | None = None,
        renderer: CoinScene | None = None,
        ahead: bool | None = None,
    ) -> None:
        """Prepara el cog; el dibujo por defecto es `CoinScene`.

        Args:
            ahead: Si las mesas pintan por adelantado el GIF de la próxima tirada
                (`CoinView.prepare`). Por defecto, solo con el dibujo de verdad: con
                un `renderer` de prueba, cada dibujo de más se contaría como jugada.
        """
        self.bot = bot
        self.economy = economy
        self.casino_channel_ids = casino_channel_ids
        # `secrets` usa el azar del sistema operativo: no se puede predecir.
        self.rng = rng or secrets.SystemRandom()
        self.ahead = renderer is None if ahead is None else ahead
        self.renderer = renderer or CoinScene()
        # Mesas abiertas: para cobrar sus rachas si el bot se apaga.
        self.views: set[CoinView] = set()

    async def cog_unload(self) -> None:
        """Al apagar, cobra las rachas a medias y cierra el navegador."""
        for view in list(self.views):
            try:
                await view.force_settle()
            except Exception:
                logger.exception("No se pudo cerrar una partida de la moneda al apagar")
            view.stop()
        self.views.clear()
        await self.renderer.close()

    async def shout(self, game: CoinGame, user: discord.abc.User, channel: object) -> None:
        """Anuncia en el canal los cobros gordos y los cantos, para que se vean."""
        if not isinstance(channel, discord.abc.Messageable):
            return
        if game.status is Status.EDGE:
            text = (
                f"📣 🪙 ¡A {user.mention} se le ha quedado la moneda **de canto**! "
                f"{TAX_COLLECTOR} se lleva {format_amount(game.pot)}."
            )
        elif game.status is Status.CASHED and game.multiplier >= SHOUT_MULTIPLIER:
            head = "¡ha sacado la moneda de oro" if game.maxed else "ha cobrado"
            text = (
                f"📣 🪙 ¡{user.mention} {head} **{format_multiplier(game.multiplier)}** "
                f"a cara o cruz! **+{format_amount(game.net)}**"
            )
        else:
            return
        try:
            await channel.send(
                text, allowed_mentions=discord.AllowedMentions(users=[user], everyone=False)
            )
        except discord.HTTPException:
            logger.debug("No se pudo anunciar una jugada de la moneda", exc_info=True)

    # -- moneda -----------------------------------------------------------------------

    async def _moneda_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        pick: Side | None,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> None:
        """Lógica compartida de `/moneda` y `.moneda`: abre la mesa y, con lado, lanza."""
        if guild is None:
            await send_error("Cara o cruz solo se juega dentro de un servidor.")
            return
        if error := casino_channel_error(self.casino_channel_ids, channel, "Cara o cruz"):
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

        view = CoinView(self, guild_id=guild.id, owner=user, stake=stake)
        view.channel = channel
        view.balance = balance
        png = await self.renderer.board(None, stake=stake, face=Side.CARA)
        view.rebuild(busy=pick is not None)
        view.message = await send(
            embed=view.embed(), file=discord.File(io.BytesIO(png), filename=PNG_NAME), view=view
        )
        self.views.add(view)
        if pick is None:
            view.prepare()
        else:
            message = view.message

            async def editor(**kwargs: Any) -> None:
                await message.edit(**kwargs)

            if error := await view.play(pick, editor):
                view.rebuild()
                await message.edit(view=view)
                await send_error(error)

    @app_commands.command(
        name="moneda",
        description="Cara o cruz: doble o nada. Una de cada cien cae de canto y es para Hacienda.",
    )
    @app_commands.describe(
        cantidad="Apuesta: 500, 2k, all… (por defecto 100)",
        lado="Cara o cruz para lanzar ya (si no, eliges con los botones)",
    )
    @app_commands.choices(
        lado=[app_commands.Choice(name=f"{s.emoji} {s.label}", value=s.key) for s in Side]
    )
    @app_commands.guild_only()
    async def moneda(
        self,
        interaction: discord.Interaction,
        cantidad: str | None = None,
        lado: str | None = None,
    ) -> None:
        """Abre una mesa de cara o cruz y, si se elige lado, lanza.

        Solo en los canales de `CASINO_CHANNEL_IDS` si está configurado. Cobra
        la apuesta al lanzar la primera y paga al cobrar.
        """
        responder = InteractionResponder(interaction)
        pick = parse_side(lado) if lado else None

        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            return await interaction.original_response()

        await self._moneda_impl(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            pick=pick,
            send=send,
            send_error=responder.send_error,
        )
        if pick is not None:
            await renta.remind(self.bot, interaction)

    @commands.command(name="moneda")
    @commands.guild_only()
    async def moneda_text(self, ctx: commands.Context, *args: str) -> None:
        """Versión de texto: `.moneda`, `.moneda 500`, `.moneda 500 cara`, `.moneda cruz`.

        La cantidad y el lado van en cualquier orden.
        """
        responder = ContextResponder(ctx)
        amount_text: str | None = None
        pick: Side | None = None
        for arg in args:
            try:
                pick = parse_side(arg)
            except ValueError:
                if amount_text is not None:
                    await responder.send_error(f"No entiendo «{arg}». Ejemplo: `.moneda 500 cara`.")
                    return
                amount_text = arg

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        await self._moneda_impl(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            amount_text=amount_text,
            pick=pick,
            send=send,
            send_error=responder.send_error,
        )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(Coin(bot, economy=bot.economy, casino_channel_ids=bot.casino_channel_ids))
