"""Pollo (estilo Chicken Road): `pollo`, cruzar la carretera sin que te atropellen.

Cada jugador abre su propia carretera, que solo él puede tocar. `pollo 500
dificil` cobra 500 Y$ y juega en Difícil. 🐔 **Cruzar** salta al siguiente
carril y sube el multiplicador; 💰 **Cobrar** se lleva apuesta ×
multiplicador; si te atropellan, lo pierdes todo. Llegar a la meta cobra
solo el premio gordo (×2.105 en Hardcore).

Cada paso es un GIF: el pollo tiembla en el bordillo mientras se encienden
unos faros al fondo del carril (esa espera es la tensión, más larga cuanto
más hay en juego), salta, y el coche frena en la valla o le atropella.
Después el bot cambia el GIF por un PNG con el estado nuevo y vuelve a
activar los botones. Lo pinta `bot.services.chicken_scene` con canvas, al
estilo de las demás mesas del casino (Node, con Chromium y Pillow de reserva).

**El siguiente paso ya está pintado al pulsar.** El carril del atropello se
sortea al empezar (`ChickenGame.hit_lane`) y no se enseña, así que mientras se
ve un GIF la carretera pinta en segundo plano el del próximo 🐔 Cruzar (y el
del 🎯 autocobro, si hay). Si no ha acabado a tiempo, la carretera se apaga al
momento y espera.

🎯 **Autocobro**: en un menú se elige un multiplicador objetivo (×1,5, ×3,
×10…). Con él, 🎯 cruza solo hasta alcanzarlo y cobra, todo en un GIF. Se
puede lanzar desde la acera (🎯 Auto) o a mitad de partida (🎯 Hasta…).

Al cobrar, el texto dice dónde estaba el coche («quedaban 4 carriles
libres: podías haber cobrado ×3,02»), y la imagen pinta el coche fantasma
con un «¡AQUÍ!». Al acabar se puede jugar otra con 🔁, cambiar la apuesta
(½, ×2, 💰 All-in), la dificultad y el autocobro.

Reglas en `bot.services.chicken`. Dinero: la apuesta se cobra al empezar
(`place_bet`) y se paga al cobrar o al morir (`pay_winnings`, con 0 si te
atropellan), que es cuando se ajusta el IRPF del día. Si la carretera
caduca (3 min sin tocarla) o el bot se apaga de forma ordenada con una
partida a medias, se cobra sola; si no se había cruzado nada, se devuelve
la apuesta.

Ancho de banda: cada paso sube un GIF (~200-300 KB) y un PNG.

Si `CASINO_CHANNEL_IDS` está configurado, solo se juega en esos canales.
Permisos del bot en el canal: enviar mensajes, insertar enlaces y adjuntar
archivos.
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs.casino import casino_channel_error, insufficient_text
from bot.services.achievements import casino_stats, chicken_stats
from bot.services.chicken import (
    DEFAULT_DIFFICULTY,
    DIFFICULTIES,
    DIFFICULTY_BY_KEY,
    ChickenError,
    ChickenGame,
    Difficulty,
    Status,
    auto_targets,
    difficulty_summary,
    format_multiplier,
    lanes_for_target,
    milestone,
    multiplier_cents,
    parse_difficulty,
    survival,
)
from bot.services.chicken_render import Media, vehicle_for
from bot.services.chicken_scene import ChickenScene
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
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

GAME = "pollo"
DEFAULT_STAKE = 100
VIEW_TIMEOUT = 180
#: Desde este multiplicador (en centésimas) se anuncia el premio en el canal.
SHOUT_CENTS = 2_500
#: Margen tras el GIF antes de poner el PNG: lo que tarda en llegar y empezar.
REVEAL_MARGIN_SECONDS = 0.5
GIF_NAME = "pollo.gif"
PNG_NAME = "pollo.png"

COLOR_PLAYING = discord.Color.from_rgb(88, 101, 242)
COLOR_HOT = discord.Color.from_rgb(255, 196, 0)
COLOR_SPLAT = discord.Color.from_rgb(80, 84, 92)
COLOR_CASHED = discord.Color.from_rgb(255, 196, 0)

# Textos en el tono de Jovani Vázquez.
CASH_LINES = (
    "¡Wepa!",
    "¡Cobras, mi amor!",
    "¡Eso es!",
    "¡Pollo listo!",
    "¡Al corral con la plata!",
)
SPLAT_LINES = ("¡PLAF!", "¡Ay, bendito!", "¡Pollo a la plancha!", "¡Se acabó el corral!")
TENSION_LINES = (
    "😰 Se oye un motor…",
    "🚗💨 ¿Viene algo?",
    "🙏 Que no venga nada, que no venga nada…",
    "👀 Unos faros al fondo…",
    "🐔 El pollo mira a los dos lados…",
    "😬 Ese ruido no me gusta, mi amor…",
)
AUTO_LINES = ("🎯 Piloto automático, agárrate…", "🎯 El pollo corre solo…", "🎯 ¡A toda pastilla!")
#: Estadística de logros con el récord de carriles, por dificultad.
RECORD_STAT = "chicken_lanes_max_{key}"
#: Vehículos en `vehicle_for` → clave de la estadística de atropellos.
VEHICLE_KEYS = {
    "un utilitario": "car",
    "un SUV de concesionario": "car",
    "una furgoneta de reparto": "van",
    "un camión de mudanzas": "truck",
    "un autobús de línea": "bus",
    "un repartidor en moto": "moto",
    "un taxi con prisa": "taxi",
}


def percent(chance: float) -> str:
    """`0,88` → `88 %` (sin decimales, al estilo español)."""
    return f"{round(chance * 100)} %"


def parse_auto(text: str) -> int:
    """`3`, `x3`, `3x`, `×2,5` → centésimas (300, 250).

    Raises:
        ValueError: Con un mensaje mostrable si no es un multiplicador válido.
    """
    value = text.strip().lower().replace("×", "").replace("x", "").replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d{1,2})?", value):
        raise ValueError("El autocobro es un multiplicador: `3`, `x2,5`…")
    cents = round(float(value) * 100)
    if cents < 101:
        raise ValueError("El autocobro tiene que ser mayor que ×1.")
    return cents


def near_miss(game: ChickenGame, vehicle: str) -> str:
    """Al cobrar: dónde estaba el coche y lo que se ha dejado en la mesa."""
    left = game.free_lanes_left
    missed = game.missed_cents
    missed_amount = format_amount(game.stake * missed // 100)
    if left is None:
        return (
            f"😱 La carretera estaba **libre hasta la meta**: te habrías llevado "
            f"{format_multiplier(missed)} ({missed_amount})."
        )
    if left == 0:
        return f"😮‍💨 **¡Por los pelos!** {vehicle.capitalize()} venía en el carril siguiente."
    if left <= 2:
        plural = "carril" if left == 1 else "carriles"
        return (
            f"🚗 {vehicle.capitalize()} venía en el carril {game.hit_lane}. "
            f"Te quedaste a {left} {plural} de {format_multiplier(missed)}."
        )
    return (
        f"🤬 Quedaban **{left} carriles libres**: podías haber cobrado "
        f"{format_multiplier(missed)} ({missed_amount})."
    )


class ChickenView(ui.View):
    """Carretera de un jugador: imagen, texto y botones.

    Guarda la partida en curso (o la última), la apuesta, la dificultad y el
    autocobro. No guarda dinero: se cobra y se paga siempre por la economía.
    """

    def __init__(
        self,
        cog: Chicken,
        *,
        guild_id: int,
        owner: discord.abc.User,
        stake: int,
        difficulty: Difficulty,
        auto: int | None,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        self.stake = stake
        self.difficulty = difficulty
        self.auto = auto
        self.game: ChickenGame | None = None
        self.seed = 0
        self.balance = 0
        self.note: str | None = None
        self.message: discord.Message | None = None
        self.channel: object = None
        self.record_before = 0
        self._busy = False
        self._lock = asyncio.Lock()
        self._last_interaction: discord.Interaction | None = None
        #: GIF pintados por adelantado: para qué estado valen y uno por botón que
        #: cruza (`"cross"` es 🐔 Cruzar; `"auto"`, 🎯 Hasta…).
        self._plan: tuple[tuple[Any, ...], dict[str, asyncio.Future[Media | None]]] | None = None
        self._painting: asyncio.Task[None] | None = None

    # -- Texto ------------------------------------------------------------------------

    def vehicle(self) -> str:
        """El vehículo del carril del atropello (o del siguiente) de esta partida."""
        game = self.game
        lane = (game.hit_lane or game.crossed + 1) if game is not None else 1
        return vehicle_for(self.seed, lane)

    def description(self) -> str:
        """Texto del embed según cómo va la partida."""
        game = self.game
        lines: list[str] = []
        if game is None:
            lines.append("# 🐔 ¿Cruzamos?\nPulsa 🔁 para jugar.")
        elif game.playing:
            if game.crossed:
                lines.append(
                    f"# {format_multiplier(game.cents)} · {format_amount(game.cashout_value)}"
                )
                progress = f"🛣️ Carril **{game.crossed}/{game.lanes}**"
                if cheer := milestone(game.crossed, game.lanes):
                    progress += f" · {cheer}"
                lines.append(progress)
                if self.record_before and game.crossed > self.record_before:
                    lines.append(
                        f"🏅 ¡Récord personal en {game.difficulty.name}! "
                        f"Antes llegabas a {self.record_before} carriles."
                    )
            else:
                lines.append("# 🐔 En la acera")
                lines.append("-# Cruza el primer carril o lanza el autocobro.")
            if game.next_value is not None:
                extra = game.next_value - game.cashout_value
                lines.append(
                    f"-# Siguiente: {format_multiplier(game.next_cents or 0)} "
                    f"(+{format_amount(extra)}) · {percent(float(game.safe_chance))} "
                    "de cruzar"
                )
        elif game.status is Status.CASHED:
            sign = "+" if game.net >= 0 else "-"
            lines.append(
                f"# 💰 {random.choice(CASH_LINES)} {sign}{format_amount(abs(game.net))}\n"
                f"Cobras **{format_amount(game.payout)}** en {format_multiplier(game.cents)} "
                f"tras {game.crossed}/{game.lanes} carriles"
            )
            if cheer := milestone(game.crossed, game.lanes):
                lines.append(cheer)
            if not game.finished_road:
                lines.append(near_miss(game, self.vehicle()))
            if self.record_before and game.crossed > self.record_before:
                lines.append(f"🏅 ¡Récord personal en {game.difficulty.name}: {game.crossed}!")
        else:
            lines.append(
                f"# 💥 {random.choice(SPLAT_LINES)} -{format_amount(game.stake)}\n"
                f"Te ha atropellado **{self.vehicle()}** en el carril {game.crossed + 1}."
            )
            if game.crossed:
                lines.append(
                    f"Llevabas {format_multiplier(game.cents)}: "
                    f"te ibas a llevar {format_amount(game.cashout_value)}."
                )
            if self.record_before and game.crossed > self.record_before:
                lines.append(f"🏅 Aun así, récord personal: {game.crossed} carriles.")
        if self.note:
            lines.append(self.note)
        lines.append(self.footer())
        if self.balance == 0 and (game is None or not game.playing):
            lines.append("**Estás a cero.** `imv` te recarga.")
        return "\n".join(lines)

    def footer(self) -> str:
        """Línea pequeña: apuesta, dificultad, autocobro y saldo."""
        game = self.game
        stake = game.stake if game is not None and game.playing else self.stake
        difficulty = game.difficulty if game is not None and game.playing else self.difficulty
        auto = f" · 🎯 {format_multiplier(self.auto)}" if self.auto else ""
        return (
            f"-# Apuesta {format_amount(stake)} · {difficulty.emoji} {difficulty.name}{auto} · "
            f"Saldo {format_amount(self.balance)}"
        )

    def color(self) -> discord.Color:
        game = self.game
        if game is None or game.playing:
            return COLOR_HOT if game is not None and game.cents >= 200 else COLOR_PLAYING
        return COLOR_CASHED if game.status is Status.CASHED else COLOR_SPLAT

    def embed(
        self,
        *,
        image: str = PNG_NAME,
        text: str | None = None,
        color: discord.Color | None = None,
    ) -> discord.Embed:
        """Embed con la imagen adjunta (`attachment://`)."""
        embed = discord.Embed(
            title=f"🐔 Pollo · {self.owner.display_name}",
            description=text if text is not None else self.description(),
            color=color or self.color(),
        )
        embed.set_image(url=f"attachment://{image}")
        return embed

    def crossing_text(self, *, auto: bool, target: int | None) -> str:
        """Texto mientras se ve el GIF, preparado ANTES de mover la partida.

        No puede decir nada del resultado: ni el multiplicador siguiente al
        que se llegue ni el saldo nuevo. Por eso se monta con el estado de
        antes del paso.
        """
        head = random.choice(AUTO_LINES if auto and self.auto else TENSION_LINES)
        lines = [f"# {head}"]
        if auto and self.auto:
            lines.append(f"Objetivo {format_multiplier(self.auto)}")
        elif target:
            lines.append(f"A por {format_multiplier(target)}…")
        lines.append(self.footer())
        return "\n".join(lines)

    # -- Componentes ------------------------------------------------------------------

    def rebuild(self, *, busy: bool = False) -> None:
        """Vuelve a montar botones y menús con el estado actual."""
        self.clear_items()
        game = self.game
        green = discord.ButtonStyle.success
        blue = discord.ButtonStyle.primary
        grey = discord.ButtonStyle.secondary
        if game is not None and game.playing:
            next_label = (
                f"🐔 Cruzar · {format_multiplier(game.next_cents)}" if game.next_cents else "🐔"
            )
            self._button(next_label, "cross", self._cross, style=blue, row=0, disabled=busy)
            cash = f"💰 Cobrar {format_amount(game.cashout_value)}" if game.crossed else "💰 Cobrar"
            self._button(
                cash,
                "cashout",
                self._cash_out,
                style=green,
                row=0,
                disabled=busy or not game.crossed,
            )
            if self.auto and self.auto > game.cents:
                self._button(
                    f"🎯 Hasta {format_multiplier(self.auto)}",
                    "auto",
                    self._auto_run,
                    style=grey,
                    row=0,
                    disabled=busy,
                )
            return
        self._button(
            f"🔁 Jugar · {format_amount(self.stake)}",
            "again",
            self._again,
            style=green,
            row=0,
            disabled=busy,
        )
        if self.auto:
            self._button(
                f"🎯 Auto {format_multiplier(self.auto)}",
                "again_auto",
                self._again_auto,
                style=blue,
                row=0,
                disabled=busy,
            )
        self._button("½", "half", self._halve, row=0, disabled=busy)
        self._button("×2", "x2", self._double, row=0, disabled=busy)
        self._button("💰 All-in", "allin", self._all_in, row=1 if self.auto else 0, disabled=busy)
        self.add_item(self._difficulty_select(disabled=busy))
        self.add_item(self._auto_select(disabled=busy))

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

    def _difficulty_select(self, *, disabled: bool) -> ui.Select:
        """Menú de dificultad, con el riesgo por carril y el premio de la meta."""
        options = [
            discord.SelectOption(
                label=f"{d.name} · {d.road}",
                value=d.key,
                emoji=d.emoji,
                description=difficulty_summary(d),
                default=d.key == self.difficulty.key,
            )
            for d in DIFFICULTIES
        ]
        select: ui.Select = ui.Select(
            custom_id=f"{GAME}:difficulty",
            options=options,
            placeholder="Dificultad",
            row=2,
            disabled=disabled,
        )

        async def callback(interaction: discord.Interaction) -> None:
            await self._choose_difficulty(interaction, select.values[0])

        select.callback = callback  # type: ignore[method-assign]
        return select

    def _auto_select(self, *, disabled: bool) -> ui.Select:
        """Menú de autocobro: a mano o un multiplicador objetivo con su probabilidad."""
        options = [
            discord.SelectOption(
                label="Sin autocobro (a mano)",
                value="0",
                emoji="✋",
                description="Tú decides cuándo cruzar y cuándo cobrar.",
                default=not self.auto,
            )
        ]
        for target in auto_targets(self.difficulty):
            lanes = lanes_for_target(self.difficulty, target)
            chance = float(survival(self.difficulty, lanes))
            options.append(
                discord.SelectOption(
                    label=f"Autocobro {format_multiplier(target)}",
                    value=str(target),
                    emoji="🎯",
                    description=(
                        f"{lanes} carril{'es' if lanes != 1 else ''} · "
                        f"{percent(chance) if chance >= 0.01 else '<1 %'} de llegar"
                    ),
                    default=target == self.auto,
                )
            )
        select: ui.Select = ui.Select(
            custom_id=f"{GAME}:auto",
            options=options,
            placeholder="Autocobro",
            row=3,
            disabled=disabled,
        )

        async def callback(interaction: discord.Interaction) -> None:
            value = int(select.values[0])
            await self._choose_auto(interaction, value or None)

        select.callback = callback  # type: ignore[method-assign]
        return select

    def disable_all(self) -> None:
        """Apaga todos los botones y menús (carretera caducada)."""
        for item in self.children:
            if isinstance(item, ui.Button | ui.Select):
                item.disabled = True

    # -- Ciclo de vida ----------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño juega en su carretera."""
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message(
            f"Esta carretera es de {self.owner.display_name}. Abre la tuya con `pollo`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Caduca: si había partida a medias, se cobra sola."""
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
            logger.debug("No se pudo cerrar la carretera del Pollo", exc_info=True)

    async def force_settle(self) -> None:
        """Cierra la partida a medias sin que nadie pierda: cobra o devuelve."""
        async with self._lock:
            game = self.game
            if game is None or not game.playing:
                return
            if game.crossed:
                game.cash_out()
                await self._settle(game)
                return
            try:
                await self.cog.economy.pay_winnings(
                    self.guild_id, self.owner.id, game=GAME, amount=game.stake
                )
            except BalanceLimitError:
                logger.warning("No se pudo devolver una apuesta del Pollo.")
            self.game = None

    # -- GIF por adelantado -------------------------------------------------------------

    def _plan_key(self) -> tuple[Any, ...]:
        """Lo que decide los GIF del próximo paso, salvo el botón que se pulse."""
        game = self.game
        assert game is not None
        return (id(game), game.crossed, self.auto, self.seed)

    def _predict(self, kind: str) -> tuple[ChickenGame, int]:
        """La partida tal como quedará si se pulsa `kind` (sobre una copia) y desde dónde."""
        game = deepcopy(self.game)
        assert game is not None
        start = game.crossed
        if kind == "auto":
            assert self.auto is not None
            game.cross_until(self.auto)
        else:
            game.cross()
        if game.playing and (game.finished_road or kind == "auto"):
            game.cash_out()
        return game, start

    def prepare(self) -> None:
        """Pinta en segundo plano los GIF del próximo paso, si no lo están ya.

        Se llama en cuanto sale el GIF de un paso (dónde atropellan ya está
        sorteado, así que se pinta mientras se ve este) y al poner la carretera
        quieta. Los GIF se pintan uno detrás de otro y nunca se cancelan a
        medias: si mientras tanto la partida cambia, el que aún no ha empezado no
        se pinta.
        """
        game = self.game
        if not self.cog.ahead or self.is_finished() or game is None or not game.playing:
            return
        key = self._plan_key()
        if self._plan is not None and self._plan[0] == key:
            return
        order = ["cross"]
        if self.auto and self.auto > game.cents:
            order.append("auto")
        loop = asyncio.get_running_loop()
        futures: dict[str, asyncio.Future[Media | None]] = {k: loop.create_future() for k in order}
        self._plan = (key, futures)
        previous = self._painting
        self._painting = asyncio.create_task(self._paint_ahead(key, order, futures, previous))

    async def _paint_ahead(
        self,
        key: tuple[Any, ...],
        order: list[str],
        futures: dict[str, asyncio.Future[Media | None]],
        previous: asyncio.Task[None] | None,
    ) -> None:
        # Un plan detrás de otro: así un plan viejo nunca hace esperar dos dibujos.
        if previous is not None:
            await asyncio.gather(previous, return_exceptions=True)
        for kind in order:
            media: Media | None = None
            if self.game is not None and self._plan_key() == key and not self.is_finished():
                try:
                    future, start = self._predict(kind)
                    media = await self.cog.renderer.hops(
                        future, start=start, seed=self.seed, note=self._end_note(future)
                    )
                except Exception:
                    logger.warning("No se pudo pintar por adelantado el Pollo", exc_info=True)
            if not futures[kind].done():
                futures[kind].set_result(media)

    def _take_plan(self, kind: str) -> asyncio.Future[Media | None] | None:
        """El GIF por adelantado de pulsar `kind`, si vale para la partida tal como está."""
        plan, self._plan = self._plan, None
        if plan is None or self.game is None or plan[0] != self._plan_key():
            return None
        return plan[1].get(kind)

    # -- Dinero y partida -------------------------------------------------------------

    async def start(self) -> str | None:
        """Cobra la apuesta y sortea la carretera.

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
        self.game = ChickenGame.new(self.stake, self.difficulty, self.cog.rng)
        self.seed = self.cog.rng.randrange(1, 2**31)
        self.record_before = await self.cog.record(
            self.guild_id, self.owner.id, self.difficulty.key
        )
        self.note = None
        return None

    async def _settle(self, game: ChickenGame) -> BetSettlement | None:
        """Paga la partida terminada (0 si le atropellaron) y prepara la nota fiscal."""
        try:
            settlement = await self.cog.economy.pay_winnings(
                self.guild_id, self.owner.id, game=GAME, amount=game.payout
            )
        except BalanceLimitError:
            logger.warning("Premio del Pollo por encima del saldo máximo; no se paga.")
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
        self, interaction: discord.Interaction, game: ChickenGame, settlement: BetSettlement | None
    ) -> None:
        """Lo que va después de enseñar el final: renta, récord, logros y anuncio."""
        await renta.remind(self.cog.bot, interaction)
        self.cog.note_record(self.guild_id, self.owner.id, game.difficulty.key, game.crossed)
        vehicle = VEHICLE_KEYS.get(self.vehicle(), "car") if game.status is Status.SPLAT else None
        delta = chicken_stats(game, vehicle=vehicle)
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
                ("splat", int(game.status is Status.SPLAT)),
                ("started", int(getattr(self, "started_at", 0))),
            ),
        )
        await self.cog.shout(game, self.owner, self.channel)

    def _end_note(self, game: ChickenGame) -> str:
        """Texto pequeño del marcador de la imagen al terminar."""
        if game.status is Status.CASHED:
            return "¡Meta!" if game.finished_road else "¡Cobrado!"
        return ""

    async def _show(
        self,
        interaction: discord.Interaction,
        *,
        start: int,
        waiting: str,
        ready: asyncio.Future[Media | None] | None = None,
    ) -> None:
        """Enseña el GIF del paso y después el PNG con el estado nuevo.

        La interacción ya está aplazada (`defer`): se edita con
        `edit_original_response`. Si el GIF ya estaba pintado (`ready`), va
        directo. Si no, mientras se pinta la carretera enseña el paso con los
        botones apagados. Se apagan sin reconstruirlos: los nuevos (🔁 Jugar o
        Cobrar con otra cifra) dirían cómo acaba antes de ver el GIF.
        """
        game = self.game
        assert game is not None
        note = self._end_note(game)
        for item in self.children:
            if isinstance(item, ui.Button | ui.Select):
                item.disabled = True

        async def show_waiting() -> None:
            try:
                await interaction.edit_original_response(
                    embed=self.embed(text=waiting, color=COLOR_PLAYING), view=self
                )
            except discord.HTTPException:
                logger.warning("No se pudo apagar la carretera del Pollo", exc_info=True)

        async def paint() -> Media:
            media = await ready if ready is not None else None
            if media is None:
                media = await self.cog.renderer.hops(game, start=start, seed=self.seed, note=note)
            return media

        media: Media
        if ready is not None and ready.done() and ready.result() is not None:
            media = await paint()
        else:
            _, media = await asyncio.gather(show_waiting(), paint())
        await interaction.edit_original_response(
            # Color neutro: el del final delataría el resultado antes del GIF.
            embed=self.embed(image=GIF_NAME, text=waiting, color=COLOR_PLAYING),
            attachments=[discord.File(io.BytesIO(media.gif), filename=GIF_NAME)],
            view=self,
        )
        # El paso siguiente ya está sorteado: se pinta mientras se ve este.
        self.prepare()
        await asyncio.sleep(media.seconds + REVEAL_MARGIN_SECONDS)
        self.rebuild()
        await interaction.edit_original_response(
            embed=self.embed(),
            attachments=[discord.File(io.BytesIO(media.png), filename=PNG_NAME)],
            view=self,
        )

    async def _board(self, interaction: discord.Interaction) -> None:
        """Pone el PNG del estado actual (sin animación)."""
        game = self.game
        if game is None:
            png = await self.cog.renderer.start(
                self.difficulty, stake=self.stake, seed=self.seed or 1
            )
        else:
            png = await self.cog.renderer.board(game, seed=self.seed, note=self._end_note(game))
        self.rebuild()
        await edit(
            interaction,
            embed=self.embed(),
            attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
            view=self,
        )
        self.prepare()

    async def _step(self, interaction: discord.Interaction, *, auto: bool) -> None:
        """🐔 Cruzar (un carril) o 🎯 (hasta el autocobro), con su animación."""
        if self._busy:
            await ack(interaction)
            return
        game = self.game
        if game is None or not game.playing:
            await ack(interaction)
            return
        self._busy = True
        settlement: BetSettlement | None = None
        try:
            await ack(interaction)
            async with self._lock:
                ready = self._take_plan("auto" if auto and self.auto else "cross")
                start = game.crossed
                waiting = self.crossing_text(auto=auto, target=game.next_cents)
                try:
                    if auto and self.auto:
                        game.cross_until(self.auto)
                    else:
                        game.cross()
                except ChickenError:
                    return
                if game.playing and (game.finished_road or (auto and self.auto)):
                    game.cash_out()
                if not game.playing:
                    settlement = await self._settle(game)
                else:
                    self.note = None
                self._last_interaction = interaction
            await self._show(interaction, start=start, waiting=waiting, ready=ready)
        except discord.HTTPException:
            logger.warning("No se pudo enseñar un paso del Pollo", exc_info=True)
        finally:
            self._busy = False
        if not game.playing:
            await self._after_game(interaction, game, settlement)

    async def _cross(self, interaction: discord.Interaction) -> None:
        await self._step(interaction, auto=False)

    async def _auto_run(self, interaction: discord.Interaction) -> None:
        await self._step(interaction, auto=True)

    async def _cash_out(self, interaction: discord.Interaction) -> None:
        """💰 Cobrar: se retira con el multiplicador actual."""
        if self._busy:
            await ack(interaction)
            return
        game = self.game
        if game is None or not game.playing or not game.crossed:
            await ack(interaction)
            return
        self._busy = True
        try:
            await ack(interaction)
            async with self._lock:
                game.cash_out()
                settlement = await self._settle(game)
                self._last_interaction = interaction
            await self._board(interaction)
        finally:
            self._busy = False
        await self._after_game(interaction, game, settlement)

    async def _again(self, interaction: discord.Interaction) -> None:
        """🔁 Jugar: cobra otra vez y sortea una carretera nueva."""
        await self._new_game(interaction, auto=False)

    async def _again_auto(self, interaction: discord.Interaction) -> None:
        """🎯 Auto: empieza y cruza solo hasta el autocobro, en un GIF."""
        await self._new_game(interaction, auto=True)

    async def _new_game(self, interaction: discord.Interaction, *, auto: bool) -> None:
        if self._busy or (self.game is not None and self.game.playing):
            await ack(interaction)
            return
        # Cobrar la apuesta va a la base de datos: se acepta el clic antes.
        await ack(interaction)
        async with self._lock:
            error = await self.start()
        if error is not None:
            await notify(interaction, error)
            return
        self._last_interaction = interaction
        if auto and self.auto:
            await self._step(interaction, auto=True)
        else:
            await self._board(interaction)
        await renta.remind(self.cog.bot, interaction)

    def _idle(self) -> bool:
        return not self._busy and (self.game is None or not self.game.playing)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        """Repinta tras cambiar apuesta, dificultad o autocobro (sin dinero de por medio).

        Lee el saldo (y a veces dibuja la carretera): se acepta el clic antes.
        """
        await ack(interaction)
        self.balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
        self.note = None
        self._last_interaction = interaction
        if self.game is not None and self.game.difficulty.key != self.difficulty.key:
            # La imagen de la partida anterior era de otra carretera: se pinta la acera nueva.
            self.game = None
            await self._board(interaction)
            return
        self.rebuild()
        await edit(interaction, embed=self.embed(), view=self)

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

    async def _choose_difficulty(self, interaction: discord.Interaction, key: str) -> None:
        if not self._idle() or key not in DIFFICULTY_BY_KEY:
            await ack(interaction)
            return
        self.difficulty = DIFFICULTY_BY_KEY[key]
        # Un objetivo que esta dificultad no alcanza se quita.
        if self.auto and self.auto not in auto_targets(self.difficulty):
            self.auto = None
        self.cog.set_prefs(self.guild_id, self.owner.id, self.difficulty, self.auto)
        await self._refresh(interaction)

    async def _choose_auto(self, interaction: discord.Interaction, target: int | None) -> None:
        if not self._idle():
            await ack(interaction)
            return
        self.auto = target
        self.cog.set_prefs(self.guild_id, self.owner.id, self.difficulty, self.auto)
        await self._refresh(interaction)


# -- Cog -------------------------------------------------------------------------------


class Chicken(commands.Cog, name="Pollo"):
    """El Pollo (Chicken Road) con los yapdollars de la economía del bot."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        casino_channel_ids: frozenset[int] = frozenset(),
        rng: random.Random | None = None,
        renderer: ChickenScene | None = None,
        load_record: Callable[[int, int, str], Awaitable[int]] | None = None,
        ahead: bool | None = None,
    ) -> None:
        """Prepara el cog; el dibujo por defecto es `ChickenScene`.

        Args:
            ahead: Si las carreteras pintan por adelantado el GIF del próximo paso
                (`ChickenView.prepare`). Por defecto, solo con el dibujo de verdad.
        """
        self.bot = bot
        self.economy = economy
        self.casino_channel_ids = casino_channel_ids
        # `secrets` usa el azar del sistema operativo: no se puede predecir.
        self.rng = rng or secrets.SystemRandom()
        self.ahead = renderer is None if ahead is None else ahead
        self.renderer = renderer or ChickenScene()
        # Carreteras abiertas: para cerrar sus partidas si el bot se apaga.
        self.views: set[ChickenView] = set()
        # Dificultad y autocobro por (servidor, miembro); se pierden al reiniciar.
        self._prefs: dict[tuple[int, int], tuple[Difficulty, int | None]] = {}
        # Récord de carriles por (servidor, miembro, dificultad). Se lee una vez
        # de los logros y luego se lleva en memoria.
        self._records: dict[tuple[int, int, str], int] = {}
        self._load_record = load_record

    async def record(self, guild_id: int, user_id: int, difficulty: str) -> int:
        """Récord de carriles de un miembro en una dificultad (0 si no se sabe)."""
        key = (guild_id, user_id, difficulty)
        if key not in self._records:
            value = 0
            if self._load_record is not None:
                try:
                    value = await self._load_record(guild_id, user_id, difficulty)
                except Exception:
                    logger.exception("No se pudo leer el récord del Pollo de %s", user_id)
            self._records[key] = value
        return self._records[key]

    def note_record(self, guild_id: int, user_id: int, difficulty: str, lanes: int) -> None:
        """Apunta los carriles de una partida terminada si baten el récord."""
        key = (guild_id, user_id, difficulty)
        self._records[key] = max(self._records.get(key, 0), lanes)

    def prefs(self, guild_id: int, user_id: int) -> tuple[Difficulty, int | None]:
        """Dificultad y autocobro que eligió un miembro la última vez."""
        return self._prefs.get((guild_id, user_id), (DIFFICULTY_BY_KEY[DEFAULT_DIFFICULTY], None))

    def set_prefs(
        self, guild_id: int, user_id: int, difficulty: Difficulty, auto: int | None
    ) -> None:
        """Recuerda la dificultad y el autocobro de un miembro."""
        self._prefs[(guild_id, user_id)] = (difficulty, auto)

    async def cog_unload(self) -> None:
        """Al apagar, cobra o devuelve las partidas a medias."""
        for view in list(self.views):
            try:
                await view.force_settle()
            except Exception:
                logger.exception("No se pudo cerrar una partida del Pollo al apagar")
            view.stop()
        self.views.clear()
        await self.renderer.close()

    async def shout(self, game: ChickenGame, user: discord.abc.User, channel: object) -> None:
        """Anuncia en el canal los cobros enormes y las metas, para que se vea."""
        if game.status is not Status.CASHED:
            return
        if game.cents < SHOUT_CENTS and not game.finished_road:
            return
        if not isinstance(channel, discord.abc.Messageable):
            return
        head = "¡ha cruzado la carretera entera" if game.finished_road else "ha cobrado"
        text = (
            f"📣 🐔 ¡{user.mention} {head} **{format_multiplier(game.cents)}** en el Pollo "
            f"({game.difficulty.name})! **+{format_amount(game.net)}**"
        )
        try:
            await channel.send(
                text, allowed_mentions=discord.AllowedMentions(users=[user], everyone=False)
            )
        except discord.HTTPException:
            logger.debug("No se pudo anunciar un premio del Pollo", exc_info=True)

    # -- pollo ------------------------------------------------------------------------

    async def _pollo_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        difficulty: Difficulty | None,
        auto: int | None,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> None:
        """Lógica compartida de `/pollo` y `.pollo`: abre la carretera y empieza ya."""
        if guild is None:
            await send_error("El Pollo solo se juega dentro de un servidor.")
            return
        if error := casino_channel_error(self.casino_channel_ids, channel, "El Pollo"):
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
        saved_difficulty, saved_auto = self.prefs(guild.id, user.id)
        difficulty = difficulty or saved_difficulty
        if auto is None:
            auto = saved_auto
        if auto is not None and auto >= multiplier_cents(difficulty, difficulty.lanes):
            await send_error(
                f"En {difficulty.name} la meta paga "
                f"{format_multiplier(multiplier_cents(difficulty, difficulty.lanes))}: "
                "pon un autocobro más bajo."
            )
            return
        self.set_prefs(guild.id, user.id, difficulty, auto)

        view = ChickenView(
            self, guild_id=guild.id, owner=user, stake=stake, difficulty=difficulty, auto=auto
        )
        view.channel = channel
        error = await view.start()
        if error is not None:
            await send_error(error)
            return
        assert view.game is not None
        png = await self.renderer.board(view.game, seed=view.seed)
        view.rebuild()
        view.message = await send(
            embed=view.embed(),
            file=discord.File(io.BytesIO(png), filename=PNG_NAME),
            view=view,
        )
        self.views.add(view)
        view.prepare()

    @app_commands.command(
        name="pollo",
        description="Pollo: cruza la carretera carril a carril y cobra antes del coche.",
    )
    @app_commands.describe(
        cantidad="Apuesta: 500, 2k, all… (por defecto 100)",
        dificultad="Fácil, Media, Difícil o Hardcore (por defecto, la última vez o Media)",
        autocobro="Cruza solo hasta este multiplicador y cobra: 2, x3, 10… (opcional)",
    )
    @app_commands.choices(
        dificultad=[
            app_commands.Choice(name=f"{d.name} · {difficulty_summary(d)}", value=d.key)
            for d in DIFFICULTIES
        ]
    )
    @app_commands.guild_only()
    async def pollo(
        self,
        interaction: discord.Interaction,
        cantidad: str | None = None,
        dificultad: str | None = None,
        autocobro: str | None = None,
    ) -> None:
        """Abre una carretera del Pollo y empieza la partida.

        Solo en los canales de `CASINO_CHANNEL_IDS` si está configurado. Cobra
        la apuesta al empezar y paga al cobrar.
        """
        responder = InteractionResponder(interaction)
        try:
            difficulty = parse_difficulty(dificultad) if dificultad else None
            auto = parse_auto(autocobro) if autocobro else None
        except ValueError as error:
            await responder.send_error(str(error))
            return

        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            return await interaction.original_response()

        await self._pollo_impl(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            difficulty=difficulty,
            auto=auto,
            send=send,
            send_error=responder.send_error,
        )
        await renta.remind(self.bot, interaction)

    @commands.command(name="pollo")
    @commands.guild_only()
    async def pollo_text(self, ctx: commands.Context, *args: str) -> None:
        """Versión de texto: `.pollo`, `.pollo 500`, `.pollo 500 dificil`, `.pollo 500 hardcore x3`.

        Los argumentos van en cualquier orden después de la cantidad: una
        dificultad y un autocobro (`x3`, `3x` o `×2,5`).
        """
        responder = ContextResponder(ctx)
        amount_text: str | None = None
        difficulty: Difficulty | None = None
        auto: int | None = None
        try:
            for arg in args:
                lowered = arg.lower()
                if lowered.startswith(("x", "×")) or lowered.endswith("x"):
                    auto = parse_auto(arg)
                    continue
                try:
                    difficulty = parse_difficulty(arg)
                except ValueError:
                    if amount_text is not None:
                        raise ValueError(
                            f"No entiendo «{arg}». Ejemplo: `.pollo 500 dificil x3`."
                        ) from None
                    amount_text = arg
        except ValueError as error:
            await responder.send_error(str(error))
            return

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        async with ctx.typing():
            await self._pollo_impl(
                guild=ctx.guild,
                channel=ctx.channel,
                user=ctx.author,
                amount_text=amount_text,
                difficulty=difficulty,
                auto=auto,
                send=send,
                send_error=responder.send_error,
            )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""

    async def load_record(guild_id: int, user_id: int, difficulty: str) -> int:
        profile = await bot.achievements.profile(guild_id, user_id)
        return profile.stats.get(RECORD_STAT.format(key=difficulty), 0)

    await bot.add_cog(
        Chicken(
            bot,
            economy=bot.economy,
            casino_channel_ids=bot.casino_channel_ids,
            load_record=load_record,
        )
    )
