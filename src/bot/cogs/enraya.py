"""4 en raya: `4enraya`, contra Jovani (la máquina) o en duelo contra otra persona.

- `4enraya` abre tu mesa contra Jovani, un panel que solo toca su dueño: el
  tablero, los botones **1**-**7** para soltar ficha, 🏳️ para rendirse, el
  desplegable con los cuatro niveles de Jovani (de 🥴 con resaca a 🔥 con café
  de Puerto Rico) y 🎯 **Retos** con la ficha y los retos de hoy. Al acabar,
  🔁 **Revancha**; quien empieza se va turnando. Contra Jovani **no se
  apuesta**: es un juego de habilidad y con cuotas fijas quien juega bien sacaría
  dinero sin parar. Lo que paga son los tres **retos del día** y la racha de días
  jugando (`bot.services.enraya`).
- `4enraya @alguien [cantidad]` le reta a un duelo, con apuesta por cabeza o
  amistoso si no se dice cantidad. El retado acepta o rechaza; quien reta puede
  retirarlo. Al aceptar se cobra a los dos y se sortean los colores (las rojas
  salen). Cada turno dura `TURN_SECONDS`: quien se duerme, pierde. Mientras haya
  menos de `GRADA_MOVES` fichas, el público apuesta a la porra por 🔴 o 🟡 con
  las reglas de la grada de los gallos. El ganador se lleva el bote menos la
  comisión de la casa; en tablas cada uno recupera lo suyo.

Cada jugada es un GIF (`bot.services.enraya_scene`, canvas en Node): la ficha
cae con gravedad y bota, y la de Jovani detrás de una pausa para «pensar»; al
ganar brillan las cuatro y salta el confeti. **Se pinta antes del clic**: en
cada turno, la mesa pinta en segundo plano el GIF de cada columna posible (con
la respuesta de Jovani ya decidida con la semilla del turno), el de rendirse,
el de dormirse en un duelo, el de la revancha y el tablero con cada nivel. Al
pulsar solo queda subirlo.

Dinero (`EconomyService`):

- Retos del día: premio con IRPF (`pay_income`), como ganancia patrimonial que
  no procede de una transmisión (art. 33.1 LIRPF), igual que los retos del club
  del póker.
- Duelos y grada: juego, como el resto del casino (premios como ganancia
  patrimonial de la base general, art. 33.1 LIRPF, y pérdidas que solo
  compensan ganancias de juego, art. 33.5.d LIRPF): entra en la retención
  diaria del casino y en la renta semanal. `place_bet` a los dos al aceptar y a
  cada apostante de grada al apostar; al acabar se paga a todos con
  `pay_winnings` (0 a quien pierde), antes de enseñar el final. La comisión se
  destruye (es el margen de la casa, no un impuesto). Si el bot se apaga a mitad
  de un duelo, se devuelve lo apostado.

Solo se juega en los canales del casino que elige `garito` (sin elegir, los de `CASINO_CHANNEL_IDS`)
y en sus hilos. Permisos del bot en el canal: enviar mensajes, insertar enlaces y adjuntar archivos.
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
import secrets
import time
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs import pets as mascotas
from bot.cogs.casino import casino_channel_error, insufficient_text
from bot.repositories.enraya import EnrayaRepository, GameRecord
from bot.services.achievements import StatDelta, casino_stats, enraya_bet_stats, enraya_stats
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
from bot.services.enraya import (
    COLS,
    DEFAULT_LEVEL,
    DIRECTION_NAMES,
    GAME,
    GRADA_MOVES,
    LEVEL_BY_KEY,
    LEVELS,
    TIER_NAMES,
    Board,
    Disc,
    Ending,
    GameEvent,
    Level,
    PlayerRecord,
    blocks_and_misses,
    daily_challenges,
    grada_odds,
    grada_open,
    grada_payouts,
    grada_totals,
    jovani_move,
    plan_seed,
    progress_for,
    reward_for,
)
from bot.services.enraya_render import (
    Media,
    Player,
    banner,
    board_state,
    end_steps,
    meta_state,
    move_steps,
)
from bot.services.enraya_scene import EnrayaScene
from bot.services.gallos import DUEL_CUT, POOL_CUT, PoolBet, duel_prize, format_odds
from bot.services.levels import TIMEZONE
from bot.services.pets import Event, Moment, bet_moment
from bot.services.poker_club import streak_multiplier_pct
from bot.services.telemetry import TimedLock
from bot.utils.ahead import Ahead
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

COMMAND = "4enraya"
#: Lo que aguanta abierta la mesa contra Jovani sin tocarla (menos que los 15 min del token).
VIEW_TIMEOUT = 600
#: Cuánto espera un reto a que lo acepten.
CHALLENGE_SECONDS = 120
#: Lo que dura un turno en un duelo; quien se duerme, pierde.
TURN_SECONDS = 60
#: Desde aquí se anuncia en el canal un duelo ganado.
SHOUT_NET = 10_000
#: Margen tras el GIF antes de enseñar el final: lo que tarda en llegar y empezar.
REVEAL_MARGIN_SECONDS = 0.5
GIF_NAME = "enraya.gif"
PNG_NAME = "enraya.png"
JOVANI = "Jovani"

COLOR_IDLE = discord.Color.from_rgb(36, 86, 214)
COLOR_WIN = discord.Color.from_rgb(255, 196, 0)
COLOR_LOST = discord.Color.from_rgb(80, 84, 92)
COLOR_DRAW = discord.Color.from_rgb(90, 140, 220)
COLOR_DUEL = discord.Color.from_rgb(220, 40, 60)

# -- Frases ---------------------------------------------------------------------------------

#: Lo que dice Jovani al tapar una raya tuya.
JOVANI_BLOCKS = (
    "«¡Ni lo sueñes!»",
    "«Tapada.»",
    "«Por ahí no pasas.»",
    "«¿Qué? ¿Qué? ¡Toma mangó! Tapada.»",
    "«Esa raya la paro como Hacienda tu devolución.»",
    "«Ahí no, que es zona de bajas emisiones.»",
    "«Te la he visto venir desde Bayamón.»",
    "«Cordón sanitario.»",
    "«Esa columna está cerrada por obras.»",
    "«Sofía, cállate, que estoy tapando.»",
    "«Ni una raya, ni media.»",
)
#: Lo que dice al dejarte una amenaza encima.
JOVANI_THREATS = (
    "«Ojo, que te la lío.»",
    "«Uy, uy, uy… ¡Quiero café!»",
    "«Lagartijo, iguana, lagarto… y raya.»",
    "«Mira bien el tablero.»",
    "«Esto huele a cuatro en raya.»",
    "«Te tengo pillado y no lo sabes.»",
    "«Yo soy yo… y esta raya es mía.»",
    "«Huelo a moción de censura…»",
    "«Tú sigue, tú sigue.»",
    "«Una más y te canto el kikirikí.»",
)
#: Lo que dice cuando le tapas tú una suya.
JOVANI_BLOCKED = (
    "«Me la has tapado.»",
    "«Me has hecho la cobra, como a Bisbal.»",
    "«Estoy destruido, pero por fuera… por dentro estoy nuevo.»",
    "«Eso no me lo esperaba.»",
    "«Pido amparo al Tribunal Supremo.»",
    "«Vale, vale, tú ganas esta.»",
    "«¿Con pulpa? ¡Sin pulpa! Y sin raya.»",
)
#: Lo que dice de vez en cuando en una jugada sin más.
JOVANI_CHATTER = (
    "«¿Con pulpa? ¡Sin pulpa!»",
    "«En Bayamón esto se juega con dominó.»",
    "«Hazme café, que esto va para largo.»",
    "«Ficha puesta, y sin preguntar.»",
    "«Esta partida dura más que una reforma en casa.»",
    "«Y Víctor le dijo a Joel: móntate en mi motora, que esta raya es nuestra.»",
    "«Desayuna con huevo, jugo de china de el bueno… y ficha al centro.»",
    "«Columna tomada.»",
    "«Esto lo tengo más pensado que tú.»",
    "«Quisiera ser una mosca para ver qué estás pensando.»",
)
WIN_LINES = (
    "¡Jovani pierde!",
    "¡Cuatro en raya!",
    "¡Toma mangó!",
    "¡Jovani pide recuento!",
    "¡Jovani dimite! Algo inédito.",
    "¡Raya y para casa!",
    "¡Raya limpia!",
    "Jovani no se lo cree.",
    "¡Victoria con las fichas bien puestas!",
)
LOSE_LINES = (
    "¡Cuatro en raya de Jovani!",
    "¡Jovani te aplica el 155!",
    "¡Te ha tapado hasta el aire!",
    "Jovani gana esta.",
    "Estoy destruido, pero por fuera… por dentro estoy nuevo.",
    "La raya se te escapó por un hueco.",
    "¡Jovani canta victoria!",
)
DRAW_LINES = (
    "Tablas.",
    "Empate técnico. Toca repetir elecciones.",
    "Tablero lleno y nadie gobierna.",
    "Ni tú ni yo.",
    "Tablas. Revancha cuando quieras.",
)
RESIGN_LINES = (
    "Tira la toalla.",
    "Se retira a reflexionar cinco días, pero no vuelve.",
    "Se rinde y se va a casa.",
    "Abandona la partida con la cabeza alta.",
)
SLEEP_LINES = (
    "se ha dormido en mitad de la partida",
    "se ha quedado roque como Rajoy con el Marca",
    "se ha ido de puente como las Cortes en diciembre",
    "se ha ido a por café (¡quiero café!) y no ha vuelto",
)
WAITING_LINES = (
    "🔴 Fichas cayendo…",
    "🟡 Se mueve el tablero…",
    "👀 Todo el bar mirando el tablero…",
    "🗳️ Escrutinio al 99 %…",
)
#: Avisos de que Jovani amenaza (o tú) para el texto de la mesa.
THREAT_NOTE = "⚠️ Jovani tiene raya en la **{cols}**."


def _cols_text(columns: list[int]) -> str:
    return ", ".join(str(c + 1) for c in columns)


def short_name(name: str) -> str:
    """Nombre recortado para las listas."""
    return name if len(name) <= 16 else name[:15] + "…"


# -- Dibujos por adelantado -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Played:
    """Una jugada resuelta y pintada: el tablero tras ella, el GIF y lo que dice Jovani."""

    board: Board
    media: Media
    note: str | None = None


@dataclass(slots=True)
class Settled:
    """Lo que dejó una partida en la ficha de un miembro: retos, racha y Y$ cobrados."""

    record: GameRecord | None = None
    lines: list[str] = field(default_factory=list)
    paid: int = 0
    tax: int = 0
    retos_full: bool = False


def center_out(columns: list[int]) -> list[int]:
    """Las columnas de más centrada a menos: las que más se juegan, primero."""
    return sorted(columns, key=lambda c: (abs(3 - c), c))


def player_line(record: PlayerRecord) -> str:
    """`12 partidas · 8 victorias · racha 3 · mejor 5`."""
    if not record.games:
        return "Aún no ha jugado ninguna partida."
    parts = [
        f"{record.games} partida{'s' if record.games != 1 else ''}",
        f"{record.wins} victoria{'s' if record.wins != 1 else ''}",
    ]
    if record.draws:
        parts.append(f"{record.draws} tablas")
    if record.streak:
        parts.append(f"🔥 racha {record.streak}")
    parts.append(f"mejor racha {record.best_streak}")
    return " · ".join(parts)


def card_tag(record: PlayerRecord) -> str:
    """La línea pequeña bajo el nombre en el tablero (sin emojis: la escena no los tiene)."""
    if not record.games:
        return "Debuta hoy"
    return f"{record.wins} V · racha {record.streak}"


# -- Contra Jovani --------------------------------------------------------------------------


class SoloTable(ui.View):
    """La mesa de un miembro contra Jovani. Sin dinero: solo los retos pagan."""

    def __init__(
        self, cog: Enraya, *, guild_id: int, owner: discord.abc.User, channel: object
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        self.channel = channel
        self.level: Level = LEVEL_BY_KEY[DEFAULT_LEVEL]
        self.record = PlayerRecord()
        self.board = Board()
        #: Si el miembro sale en la partida actual (lleva las rojas) o sale Jovani.
        self.player_first = True
        self.games = 0
        self.over = False
        self.turn_seed = cog.rng.randrange(1, 2**31)
        self.note: str | None = None
        self.result: str | None = None
        self.message: discord.Message | None = None
        self._last_interaction: discord.Interaction | None = None
        self._busy = False
        self.ahead = Ahead(cog.ahead, name="el 4 en raya")

    @property
    def player(self) -> Disc:
        return Disc.RED if self.player_first else Disc.YELLOW

    @property
    def fresh(self) -> bool:
        """Si el miembro aún no ha soltado ninguna ficha en esta partida."""
        return self.board.count(self.player) == 0

    # -- Texto e imagen ---------------------------------------------------------------------

    def meta(self, level: Level | None = None) -> dict[str, Any]:
        level = level or self.level
        me = Player(self.owner.display_name, card_tag(self.record))
        jovani = Player(JOVANI, level.name)
        red, yellow = (me, jovani) if self.player_first else (jovani, me)
        return meta_state(
            red, yellow, title="4 EN RAYA", subtitle=f"Contra {level.name}", seed=self.turn_seed
        )

    def description(self) -> str:
        if self.result is not None:
            return self.result
        level = self.level
        lines = [f"### {level.emoji} {level.name}", f"*{level.blurb}*"]
        if self.note:
            lines.append(self.note)
        # La pista de dónde amenaza Jovani solo en los niveles fáciles: arriba, a pelo.
        if level.order <= 1 and (threats := self.board.threats(self.player.other)):
            lines.append(THREAT_NOTE.format(cols=_cols_text(threats)))
        lines.append(f"Te toca: suelta tu ficha {self.player.emoji} con los botones **1**-**7**.")
        lines.append(f"-# {player_line(self.record)}")
        lines.append("-# Contra Jovani no se apuesta: pagan los 🎯 retos del día.")
        return "\n".join(lines)

    def embed(
        self, *, image: str = PNG_NAME, text: str | None = None, color: discord.Color | None = None
    ) -> discord.Embed:
        embed = discord.Embed(
            title=f"🔴 4 en raya · {self.owner.display_name} 🆚 Jovani",
            description=text if text is not None else self.description(),
            color=color or COLOR_IDLE,
        )
        embed.set_image(url=f"attachment://{image}")
        return embed

    def end_banner(self, board: Board, level: Level) -> dict[str, Any] | None:
        if board.winner is self.player:
            return banner("¡GANAS!", f"Le ganas a {level.name}", kind="win")
        if board.winner is not None:
            return banner("¡GANA JOVANI!", level.name, kind="lose")
        if board.full:
            return banner("TABLAS", "Tablero lleno y nadie gobierna", kind="draw")
        return None

    # -- Componentes ------------------------------------------------------------------------

    def rebuild(self) -> None:
        """Monta los botones según el momento de la partida."""
        self.clear_items()
        if self.over:
            self._button("🔁 Revancha", "rematch", self._rematch, discord.ButtonStyle.success, 0)
            self._button("🎯 Retos", "retos", self._retos, discord.ButtonStyle.secondary, 0)
            self._level_select(1)
            return
        legal = set(self.board.legal())
        for column in range(COLS):
            self._button(
                str(column + 1),
                f"col{column}",
                self._column_callback(column),
                discord.ButtonStyle.primary,
                0 if column < 4 else 1,
                disabled=column not in legal,
            )
        if not self.fresh:
            self._button("🏳️", "resign", self._resign, discord.ButtonStyle.danger, 1)
        self._button("🎯", "retos", self._retos, discord.ButtonStyle.secondary, 1)
        if self.fresh and not self.board.moves:
            self._level_select(2)

    def _level_select(self, row: int) -> None:
        select: ui.Select = ui.Select(
            placeholder="Nivel de Jovani",
            row=row,
            custom_id=f"{GAME}:level",
            options=[
                discord.SelectOption(
                    label=level.name,
                    value=level.key,
                    emoji=level.emoji,
                    description=level.blurb[:100],
                    default=level.key == self.level.key,
                )
                for level in LEVELS
            ],
        )
        select.callback = self._choose_level  # type: ignore[method-assign]
        self.add_item(select)

    def _button(
        self,
        label: str,
        custom_id: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        style: discord.ButtonStyle,
        row: int,
        *,
        disabled: bool = False,
    ) -> None:
        button: ui.Button = ui.Button(
            label=label, style=style, custom_id=f"{GAME}:{custom_id}", row=row, disabled=disabled
        )
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)

    def _column_callback(self, column: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await self._column(interaction, column)

        return callback

    def disable_all(self) -> None:
        for item in self.children:
            if isinstance(item, ui.Button | ui.Select):
                item.disabled = True

    # -- Ciclo de vida ----------------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño juega en su mesa."""
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message(
            f"Esta mesa es de {self.owner.display_name}. Abre la tuya con `{COMMAND}`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Caduca: si había partida a medias, cuenta como rendirse (para que no se escape nadie)."""
        self.cog.panels.discard(self)
        self.ahead.drop()
        if not self.over and not self.fresh and not self._busy:
            self.over = True
            await self._settle(Ending.RESIGN)
        self.disable_all()
        try:
            if self._last_interaction is not None:
                await self._last_interaction.edit_original_response(view=self)
            elif self.message is not None:
                await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar la mesa del 4 en raya", exc_info=True)

    # -- Pintar antes del clic --------------------------------------------------------------

    def _key(self) -> tuple[Any, ...]:
        if self.over:
            return ("over", self.games, self.level.key, self.player_first)
        return ("turn", self.games, len(self.board.moves), self.level.key, self.turn_seed)

    def prepare(self) -> None:
        """Pinta en segundo plano lo de cada botón de este momento.

        En un turno: cada columna (con la respuesta de Jovani), rendirse y, con
        el tablero vacío, el tablero con cada nivel. Al acabar: la revancha.
        """
        key = self._key()
        if self.over:
            self.ahead.plan(key, ["rematch"], lambda _item: self._rematch_media())
            return
        board, level, seed, meta = self.board, self.level, self.turn_seed, self.meta()
        order: list[Hashable] = list(center_out(board.legal()))
        if not self.fresh:
            order.append("resign")
        if not board.moves:
            order += [f"level:{lv.key}" for lv in LEVELS if lv.key != level.key]

        async def paint(item: Hashable) -> Any:
            if isinstance(item, int):
                return await self._resolve(board, item, level=level, seed=seed, meta=meta)
            if item == "resign":
                return await self._resign_media(board, meta)
            other = LEVEL_BY_KEY[str(item).split(":", 1)[1]]
            return await self.cog.renderer.still(
                self.meta(other), board_state(board, turn=board.turn)
            )

        self.ahead.plan(key, order, paint)

    async def _resolve(
        self, board: Board, column: int, *, level: Level, seed: int, meta: dict[str, Any]
    ) -> Played:
        """Tu ficha en `column`, la respuesta de Jovani (si sigue la partida) y el GIF."""
        after = board.play(column)
        moves = [after.last]
        note = None
        final = after
        if not after.finished:
            rng = plan_seed(seed, column)
            reply = await asyncio.to_thread(jovani_move, after, level, rng)
            final = after.play(reply)
            moves.append(final.last)
            note = self._jovani_note(board, after, reply, final, rng)
        steps, last = self._steps(board, moves, final, level)
        media = await self.cog.renderer.animate(meta, steps, last)
        return Played(final, media, note)

    def _steps(
        self, board: Board, moves: list[Any], final: Board, level: Level
    ) -> tuple[list[tuple[dict, int]], dict[str, Any]]:
        return move_steps(board, moves, end=self.end_banner(final, level))

    def _jovani_note(
        self, before: Board, after: Board, reply: int, final: Board, rng: random.Random
    ) -> str:
        """Lo que dice Jovani al soltar su ficha (y dónde la suelta)."""
        where = (
            f"🟡 Jovani suelta en la **{reply + 1}**."
            if self.player_first
            else (f"🔴 Jovani suelta en la **{reply + 1}**.")
        )
        me = self.player
        if final.finished:
            return where
        if reply in after.threats(me):
            return f"{where} {rng.choice(JOVANI_BLOCKS)}"
        if before.threats(me.other) and not after.threats(me.other) and after.last is not None:
            return f"{where} {rng.choice(JOVANI_BLOCKED)}"
        if final.threats(me.other):
            return f"{where} {rng.choice(JOVANI_THREATS)}"
        if rng.random() < 0.3:
            return f"{where} {rng.choice(JOVANI_CHATTER)}"
        return where

    async def _resign_media(self, board: Board, meta: dict[str, Any]) -> Media:
        end = banner("TE RINDES", "Dimisión irrevocable", kind="lose")
        steps = end_steps(board, end=end)
        return await self.cog.renderer.animate(
            meta, steps, board_state(board, end={**end, "a": 1.0})
        )

    async def _rematch_media(self) -> Played | bytes:
        """La revancha: el tablero vacío o, si sale Jovani, su primera ficha."""
        player_first = not self.player_first
        seed = self.cog.rng.randrange(1, 2**31)
        me = Player(self.owner.display_name, card_tag(self.record))
        jovani = Player(JOVANI, self.level.name)
        red, yellow = (me, jovani) if player_first else (jovani, me)
        meta = meta_state(
            red, yellow, title="4 EN RAYA", subtitle=f"Contra {self.level.name}", seed=seed
        )
        if player_first:
            png = await self.cog.renderer.still(meta, board_state(Board(), turn=Disc.RED))
            return png
        rng = plan_seed(seed, -1)
        opening = await asyncio.to_thread(jovani_move, Board(), self.level, rng)
        board = Board().play(opening)
        steps, last = move_steps(Board(), [board.last])
        media = await self.cog.renderer.animate(meta, steps, last)
        return Played(board, media, f"🔴 Jovani sale en la **{opening + 1}**.")

    # -- Botones ----------------------------------------------------------------------------

    async def _column(self, interaction: discord.Interaction, column: int) -> None:
        """Un botón 1-7: tu ficha, la de Jovani y el GIF (pintado de antes si se puede)."""
        if self._busy:
            await interaction.response.defer()
            return
        if self.over or column not in self.board.legal():
            await interaction.response.send_message("Esa columna no admite ficha.", ephemeral=True)
            return
        await ack(interaction)
        self._busy = True
        self._last_interaction = interaction
        try:
            played = await self.ahead.take(self._key(), column)
            self.ahead.drop()
            if played is None:
                played = await self._cover(
                    interaction,
                    self._resolve(
                        self.board,
                        column,
                        level=self.level,
                        seed=self.turn_seed,
                        meta=self.meta(),
                    ),
                )
            self.board = played.board
            self.note = played.note
            if self.board.finished:
                await self._finish(interaction, played.media)
                return
            self.turn_seed = self.cog.rng.randrange(1, 2**31)
            self.rebuild()
            await edit(
                interaction,
                embed=self.embed(image=GIF_NAME),
                attachments=[discord.File(io.BytesIO(played.media.gif), filename=GIF_NAME)],
                view=self,
            )
            self.prepare()
        except discord.HTTPException:
            logger.warning("No se pudo enseñar una jugada del 4 en raya", exc_info=True)
        finally:
            self._busy = False

    async def _cover(self, interaction: discord.Interaction, work: Awaitable[Any]) -> Any:
        """Tapa la espera: apaga los botones mientras se pinta lo que no estaba pintado."""
        self.disable_all()

        async def show() -> None:
            try:
                await edit(interaction, view=self)
            except discord.HTTPException:
                logger.debug("No se pudieron apagar los botones del 4 en raya", exc_info=True)

        _, result = await asyncio.gather(show(), work)
        return result

    async def _resign(self, interaction: discord.Interaction) -> None:
        """🏳️: te rindes (cuenta como perder)."""
        if self._busy or self.over or self.fresh:
            await interaction.response.defer()
            return
        await ack(interaction)
        self._busy = True
        self._last_interaction = interaction
        try:
            media = await self.ahead.take(self._key(), "resign")
            self.ahead.drop()
            if media is None:
                media = await self._cover(interaction, self._resign_media(self.board, self.meta()))
            await self._finish(interaction, media, resigned=True)
        except discord.HTTPException:
            logger.warning("No se pudo enseñar la rendición del 4 en raya", exc_info=True)
        finally:
            self._busy = False

    async def _rematch(self, interaction: discord.Interaction) -> None:
        """🔁 Revancha: partida nueva; sale quien no salió la última vez."""
        if self._busy or not self.over:
            await interaction.response.defer()
            return
        await ack(interaction)
        self._busy = True
        self._last_interaction = interaction
        try:
            ready = await self.ahead.take(self._key(), "rematch")
            self.ahead.drop()
            if ready is None:
                ready = await self._cover(interaction, self._rematch_media())
            self.games += 1
            self.player_first = not self.player_first
            self.over = False
            self.result = None
            self.turn_seed = self.cog.rng.randrange(1, 2**31)
            if isinstance(ready, Played):
                self.board = ready.board
                self.note = ready.note
                image, data = GIF_NAME, ready.media.gif
            else:
                self.board = Board()
                self.note = None
                image, data = PNG_NAME, ready
            self.rebuild()
            await edit(
                interaction,
                embed=self.embed(image=image),
                attachments=[discord.File(io.BytesIO(data), filename=image)],
                view=self,
            )
            self.prepare()
        except discord.HTTPException:
            logger.warning("No se pudo empezar la revancha del 4 en raya", exc_info=True)
        finally:
            self._busy = False

    async def _choose_level(self, interaction: discord.Interaction) -> None:
        """Cambia el nivel de Jovani (con el tablero vacío o entre partidas)."""
        values = interaction.data.get("values", []) if interaction.data else []
        if self._busy or not values or values[0] not in LEVEL_BY_KEY:
            await interaction.response.defer()
            return
        level = LEVEL_BY_KEY[values[0]]
        if self.over:
            # Entre partidas la imagen es el final de la anterior: solo cambia el texto.
            self.level = level
            self.rebuild()
            await interaction.response.edit_message(view=self)
            self.prepare()
            return
        if self.board.moves:
            await interaction.response.defer()
            return
        await ack(interaction)
        self._busy = True
        self._last_interaction = interaction
        try:
            png = await self.ahead.take(self._key(), f"level:{level.key}")
            self.ahead.drop()
            self.level = level
            if png is None:
                png = await self.cog.renderer.still(
                    self.meta(), board_state(self.board, turn=self.board.turn)
                )
            self.rebuild()
            await edit(
                interaction,
                embed=self.embed(),
                attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
                view=self,
            )
            self.prepare()
        except discord.HTTPException:
            logger.warning("No se pudo cambiar el nivel del 4 en raya", exc_info=True)
        finally:
            self._busy = False

    async def _retos(self, interaction: discord.Interaction) -> None:
        """🎯: tu ficha y los retos de hoy, en privado."""
        await ack(interaction, new_message=True)
        embed = await self.cog.enraya_embed(self.guild_id, self.owner)
        await edit(interaction, embed=embed)

    # -- Final ------------------------------------------------------------------------------

    def _ending(self, *, resigned: bool) -> Ending:
        if resigned:
            return Ending.RESIGN
        if self.board.winner is self.player:
            return Ending.WIN
        if self.board.winner is not None:
            return Ending.LOSS
        return Ending.DRAW

    async def _finish(
        self,
        interaction: discord.Interaction,
        media: Media,
        *,
        resigned: bool = False,
    ) -> None:
        """Enseña el GIF del final, apunta la partida (retos) y luego el resultado."""
        self.over = True
        ending = self._ending(resigned=resigned)
        self.disable_all()
        waiting = f"# {random.choice(WAITING_LINES)}"
        await edit(
            interaction,
            embed=self.embed(image=GIF_NAME, text=waiting),
            attachments=[discord.File(io.BytesIO(media.gif), filename=GIF_NAME)],
            view=self,
        )
        settle = asyncio.create_task(self._settle(ending))
        await self.cog.sleep(media.seconds + REVEAL_MARGIN_SECONDS)
        settled = await settle
        self.result = await self._result_text(ending, settled)
        self.rebuild()
        color = {Ending.WIN: COLOR_WIN, Ending.DRAW: COLOR_DRAW}.get(ending, COLOR_LOST)
        await edit(
            interaction,
            embed=self.embed(color=color),
            attachments=[discord.File(io.BytesIO(media.png), filename=PNG_NAME)],
            view=self,
        )
        self.prepare()
        if settled.paid:
            await renta.remind(self.cog.bot, interaction)

    async def _settle(self, ending: Ending) -> Settled:
        """Apunta la partida en la ficha, paga los retos y alimenta los logros."""
        blocks, missed = blocks_and_misses(self.board, self.player)
        event = GameEvent(
            ending=ending,
            disc=self.player,
            level=self.level,
            board=self.board,
            blocks=blocks,
            missed=missed,
        )
        settled = await self.cog.settle(self.guild_id, self.owner, event)
        if settled.record is not None:
            self.record = settled.record.after
        delta = enraya_stats(
            event,
            self.record,
            when=datetime.fromtimestamp(self.cog.wall_clock(), TIMEZONE),
            jovani_first=not self.player_first,
            retos=len(settled.record.completed) if settled.record else 0,
            retos_full=settled.retos_full,
        )
        await logros.track(self.cog.bot, self.guild_id, self.owner, self.channel, delta)
        return settled

    async def _result_text(self, ending: Ending, settled: Settled) -> str:
        level = self.level
        board = self.board
        lines = []
        if ending is Ending.WIN:
            directions = {line.direction for line in board.winning_lines}
            how = " y ".join(sorted({DIRECTION_NAMES[d] for d in directions}))
            lines.append(f"# 🏆 {random.choice(WIN_LINES)}")
            lines.append(
                f"Le ganas a {level.emoji} **{level.name}** con una raya {how} "
                f"en {board.count(self.player)} fichas."
            )
        elif ending is Ending.LOSS:
            lines.append(f"# 💀 {random.choice(LOSE_LINES)}")
            lines.append(f"{level.emoji} **{level.name}** hace cuatro en raya.")
        elif ending is Ending.DRAW:
            lines.append(f"# 🤝 {random.choice(DRAW_LINES)}")
        else:
            lines.append(f"# 🏳️ {random.choice(RESIGN_LINES)}")
            lines.append(f"Te rindes ante {level.emoji} **{level.name}**.")
        _blocks, missed = blocks_and_misses(board, self.player)
        if missed and ending is not Ending.WIN:
            lines.append(
                f"-# 🙈 Tuviste raya hecha {missed} vez{'es' if missed != 1 else ''} y no la viste."
            )
        lines += settled.lines
        lines.append(f"-# {player_line(self.record)}")
        moment = Moment(Event.WIN if ending is Ending.WIN else Event.LOSE)
        if ending is Ending.DRAW:
            moment = Moment()
        if settled.paid:
            hint = await renta.hint(self.cog.bot, self.guild_id, self.owner.id, moment)
        else:
            hint = await mascotas.cameo(self.cog.bot, self.guild_id, self.owner.id, moment)
        if hint:
            lines.append(hint)
        lines.append("-# 🔁 Revancha para otra (sale quien no salió) · 🎯 tus retos de hoy.")
        return "\n".join(lines)


# -- Duelos ---------------------------------------------------------------------------------


class Phase(Enum):
    """Momento de un duelo."""

    CHALLENGE = "reto"
    PLAYING = "partida"
    DONE = "fin"


@dataclass(slots=True)
class Spectator:
    """Un apostante de grada: su apuesta y la última interacción (para su resultado privado)."""

    member: discord.abc.User
    disc: Disc
    stake: int
    interaction: discord.Interaction | None = None
    settlement: BetSettlement | None = None
    payout: int = 0

    @property
    def bet(self) -> PoolBet:
        return PoolBet(self.disc.corner, self.stake)


class BetModal(ui.Modal, title="🎟️ Apuesta de grada"):
    """Pide cuánto apuesta el público por un color."""

    amount: ui.TextInput = ui.TextInput(
        label="Cantidad (500, 2k, all…)", placeholder="100", max_length=12
    )

    def __init__(self, duel: Duel, disc: Disc) -> None:
        super().__init__()
        self.duel = duel
        self.disc = disc

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Cobra la apuesta y la apunta en la grada."""
        # Leer el saldo y cobrar van a la base de datos: se acepta antes.
        await ack(interaction)
        await self.duel.place(interaction, self.disc, str(self.amount.value))


class DuelView(ui.View):
    """Los botones de un duelo; quién puede pulsar cada uno lo decide el duelo."""

    def __init__(self, duel: Duel) -> None:
        super().__init__(timeout=None)
        self.duel = duel

    def _button(
        self,
        label: str,
        custom_id: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        row: int = 0,
        disabled: bool = False,
    ) -> None:
        button: ui.Button = ui.Button(
            label=label[:80],
            style=style,
            custom_id=f"{GAME}:{custom_id}",
            row=row,
            disabled=disabled,
        )
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)

    def challenge(self) -> DuelView:
        """Botones del reto: aceptar, rechazar o retirarlo."""
        self.clear_items()
        self._button("✅ Aceptar", "accept", self.duel.accept, style=discord.ButtonStyle.success)
        self._button("❌ Rechazar", "reject", self.duel.reject, style=discord.ButtonStyle.danger)
        self._button("🗑️ Retirar", "withdraw", self.duel.withdraw)
        return self

    def playing(self, *, busy: bool = False) -> DuelView:
        """Columnas, rendirse y, mientras está abierta, la grada."""
        self.clear_items()
        duel = self.duel
        legal = set(duel.board.legal())
        style = (
            discord.ButtonStyle.danger
            if duel.board.turn is Disc.RED
            else discord.ButtonStyle.primary
        )
        for column in range(COLS):
            self._button(
                str(column + 1),
                f"col{column}",
                duel.column_callback(column),
                style=style,
                row=0 if column < 4 else 1,
                disabled=busy or column not in legal,
            )
        self._button("🏳️", "resign", duel.resign, row=1, disabled=busy)
        if grada_open(duel.board):
            for disc in Disc:
                self._button(
                    f"{disc.emoji} Apostar por {short_name(duel.member(disc).display_name)}",
                    f"bet_{disc.value}",
                    duel.bet_callback(disc),
                    row=2,
                    disabled=busy,
                )
        return self


class Duel:
    """Un duelo entre dos miembros, del reto al resultado, en un mismo mensaje.

    Guarda quién apuesta y cuánto solo para repartir: el dinero se cobra y se
    paga siempre por la economía.
    """

    def __init__(
        self,
        cog: Enraya,
        *,
        guild_id: int,
        channel: object,
        challenger: discord.abc.User,
        rival: discord.abc.User,
        stake: int,
    ) -> None:
        self.cog = cog
        self.guild_id = guild_id
        self.channel = channel
        self.challenger = challenger
        self.rival = rival
        self.stake = stake
        self.players: dict[Disc, discord.abc.User] = {Disc.RED: challenger, Disc.YELLOW: rival}
        self.records: dict[Disc, PlayerRecord] = {
            Disc.RED: PlayerRecord(),
            Disc.YELLOW: PlayerRecord(),
        }
        self.board = Board()
        self.phase = Phase.CHALLENGE
        self.view = DuelView(self)
        self.message: discord.Message | None = None
        self.crowd: dict[int, Spectator] = {}
        self.interactions: dict[int, discord.Interaction] = {}
        self.settlements: dict[Disc, BetSettlement | None] = {}
        self.deadline = 0.0
        self.started_at = 0.0
        self.seed = cog.rng.randrange(1, 2**31)
        self.timer: asyncio.Task[None] | None = None
        self.ahead = Ahead(cog.ahead, name="el 4 en raya")
        self._lock = TimedLock("enraya:Duel._lock")
        self._busy = False
        self.note: str | None = None

    def member(self, disc: Disc) -> discord.abc.User:
        return self.players[disc]

    def disc_of(self, user_id: int) -> Disc | None:
        for disc, member in self.players.items():
            if member.id == user_id:
                return disc
        return None

    @property
    def members(self) -> list[discord.abc.User]:
        return [self.challenger, self.rival]

    # -- Texto e imagen ---------------------------------------------------------------------

    def meta(self) -> dict[str, Any]:
        red, yellow = self.players[Disc.RED], self.players[Disc.YELLOW]
        subtitle = (
            f"Duelo · bote {format_amount(duel_prize(self.stake))}"
            if self.stake
            else "Duelo amistoso"
        )
        return meta_state(
            Player(red.display_name, card_tag(self.records[Disc.RED])),
            Player(yellow.display_name, card_tag(self.records[Disc.YELLOW])),
            title="4 EN RAYA",
            subtitle=subtitle,
            seed=self.seed,
        )

    def embed(
        self, text: str, *, image: str = PNG_NAME, color: discord.Color = COLOR_DUEL
    ) -> discord.Embed:
        embed = discord.Embed(
            title=f"🔴 4 en raya: {self.challenger.display_name} 🆚 {self.rival.display_name}",
            description=text,
            color=color,
        )
        embed.set_image(url=f"attachment://{image}")
        return embed

    def _money_line(self) -> str:
        if not self.stake:
            return "Duelo **amistoso**: sin apuesta, pero cuenta para la ficha y los retos."
        return (
            f"**{format_amount(self.stake)}** por cabeza · el ganador se lleva "
            f"**{format_amount(duel_prize(self.stake))}** (la casa se queda el "
            f"{int(DUEL_CUT * 100)} %). En tablas, cada uno recupera lo suyo."
        )

    def challenge_text(self) -> str:
        return "\n".join(
            [
                f"# 🥊 {self.challenger.mention} reta a {self.rival.mention} a un 4 en raya",
                self._money_line(),
                f"-# {self.rival.display_name} tiene <t:{int(self.deadline)}:R> para aceptar. "
                "Los colores se sortean; las rojas salen.",
            ]
        )

    def playing_text(self) -> str:
        turn = self.board.turn
        lines = [
            f"{Disc.RED.emoji} {self.players[Disc.RED].mention} · "
            f"{player_line(self.records[Disc.RED])}",
            f"{Disc.YELLOW.emoji} {self.players[Disc.YELLOW].mention} · "
            f"{player_line(self.records[Disc.YELLOW])}",
            self._money_line(),
        ]
        if self.note:
            lines.append(self.note)
        lines.append(
            f"## {turn.emoji} Le toca a {self.players[turn].mention} · "
            f"se duerme <t:{int(self.deadline)}:R>"
        )
        bets = {k: s.bet for k, s in self.crowd.items()}
        if grada_open(self.board) or self.crowd:
            totals = grada_totals(bets)
            odds = grada_odds(bets)
            state = f"abierta hasta la ficha {GRADA_MOVES}" if grada_open(self.board) else "cerrada"
            lines.append(f"### 🎟️ Grada ({state})")
            for disc in Disc:
                backers = sum(1 for s in self.crowd.values() if s.disc is disc)
                pays = format_odds(odds[disc]) if odds[disc] is not None else "sin apuestas"
                lines.append(
                    f"{disc.emoji} {format_amount(totals[disc])} de {backers} "
                    f"persona{'s' if backers != 1 else ''} · paga {pays}"
                )
            lines.append(
                f"-# La grada se reparte lo apostado entre los que acierten, menos el "
                f"{int(POOL_CUT * 100)} % de la casa. En tablas se devuelve todo."
            )
        return "\n".join(lines)

    async def edit_message(self, **kwargs: Any) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(**kwargs)
        except discord.HTTPException:
            logger.debug("No se pudo editar un duelo de 4 en raya", exc_info=True)

    # -- Reto -------------------------------------------------------------------------------

    async def open(self, send: Callable[..., Awaitable[discord.Message]]) -> None:
        """Manda el reto y espera a que lo acepten."""
        self.deadline = self.cog.wall_clock() + CHALLENGE_SECONDS
        png = await self.cog.renderer.still(self.meta(), board_state(self.board))
        sent = await send(
            content=self.rival.mention,
            embed=self.embed(self.challenge_text()),
            file=discord.File(io.BytesIO(png), filename=PNG_NAME),
            view=self.view.challenge(),
            allowed_mentions=discord.AllowedMentions(users=[self.rival]),
        )
        # Un duelo puede pasar de los 15 minutos que vale el token de la respuesta
        # del slash: el mensaje se edita como bot, por el canal.
        partial = getattr(self.channel, "get_partial_message", None)
        self.message = (
            partial(sent.id)
            if isinstance(self.channel, discord.abc.Messageable) and callable(partial)
            else sent
        )
        self.timer = asyncio.create_task(self._expire(), name=f"{GAME}-reto")

    async def _expire(self) -> None:
        await self.cog.sleep(CHALLENGE_SECONDS)
        async with self._lock:
            if self.phase is not Phase.CHALLENGE:
                return
            self.phase = Phase.DONE
        self.cog.release(self)
        await self.edit_message(
            content=None,
            embed=self.embed("⌛ Nadie contestó al reto. Las fichas vuelven a la caja."),
            view=None,
        )

    async def _only(
        self, interaction: discord.Interaction, user: discord.abc.User, what: str
    ) -> bool:
        if interaction.user.id == user.id:
            return True
        await interaction.response.send_message(what, ephemeral=True)
        return False

    async def accept(self, interaction: discord.Interaction) -> None:
        """✅ Aceptar: cobra a los dos, sortea los colores y empieza la partida."""
        if not await self._only(interaction, self.rival, "Este reto no es para ti."):
            return
        await ack(interaction)
        async with self._lock:
            if self.phase is not Phase.CHALLENGE:
                return
            if self.stake and (error := await self._charge_players()):
                await notify(interaction, error)
                return
            self.phase = Phase.PLAYING
            self.interactions[interaction.user.id] = interaction
            self.started_at = time.time()
            if self.cog.rng.random() < 0.5:
                self.players = {Disc.RED: self.rival, Disc.YELLOW: self.challenger}
            for disc, member in self.players.items():
                self.records[disc] = await self.cog.repository.get(self.guild_id, member.id)
            self.deadline = self.cog.wall_clock() + TURN_SECONDS
        if self.timer is not None:
            self.timer.cancel()
        png = await self.cog.renderer.still(self.meta(), board_state(self.board, turn=Disc.RED))
        await edit(
            interaction,
            content=None,
            embed=self.embed(self.playing_text()),
            attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
            view=self.view.playing(),
        )
        self._arm_timer()
        self.prepare()
        if self.stake:
            await renta.remind(self.cog.bot, interaction)

    async def _charge_players(self) -> str | None:
        """Cobra la apuesta a los dos; si al segundo no le llega, devuelve la del primero."""
        charged: list[discord.abc.User] = []
        for member in (self.rival, self.challenger):
            try:
                await self.cog.economy.place_bet(
                    self.guild_id, member.id, game=GAME, stake=self.stake
                )
            except InsufficientFundsError as error:
                for refunded in charged:
                    await self.cog.pay(self.guild_id, refunded.id, self.stake)
                if member is self.rival:
                    return insufficient_text(error.balance, self.stake)
                return f"A {member.display_name} ya no le llega para el duelo."
            charged.append(member)
        return None

    async def reject(self, interaction: discord.Interaction) -> None:
        """❌ Rechazar: el reto se acaba sin mover dinero."""
        if not await self._only(interaction, self.rival, "Este reto no es para ti."):
            return
        if self._lock.locked():
            # Están aceptando (cobro en la base de datos): sin esperar al candado.
            await interaction.response.defer()
            return
        async with self._lock:
            if self.phase is not Phase.CHALLENGE:
                await interaction.response.defer()
                return
            self.phase = Phase.DONE
        if self.timer is not None:
            self.timer.cancel()
        self.cog.release(self)
        await edit(
            interaction,
            content=None,
            embed=self.embed(
                f"🙅 {self.rival.mention} rechaza el reto. Ni con la amnistía se sienta a jugar."
            ),
            view=None,
        )
        await logros.track(
            self.cog.bot,
            self.guild_id,
            self.rival,
            self.channel,
            StatDelta(add={"enraya_rejects": 1}),
        )
        await logros.track(
            self.cog.bot,
            self.guild_id,
            self.challenger,
            self.channel,
            StatDelta(add={"enraya_rejected": 1}),
        )

    async def withdraw(self, interaction: discord.Interaction) -> None:
        """🗑️ Retirar: quien reta se echa atrás antes de que acepten."""
        if not await self._only(interaction, self.challenger, "Solo quien reta puede retirarlo."):
            return
        if self._lock.locked():
            # Están aceptando (cobro en la base de datos): sin esperar al candado.
            await interaction.response.defer()
            return
        async with self._lock:
            if self.phase is not Phase.CHALLENGE:
                await interaction.response.defer()
                return
            self.phase = Phase.DONE
        if self.timer is not None:
            self.timer.cancel()
        self.cog.release(self)
        await edit(
            interaction,
            content=None,
            embed=self.embed("🗑️ Reto retirado. Aquí no ha pasado nada."),
            view=None,
        )

    # -- Grada ------------------------------------------------------------------------------

    def bet_callback(self, disc: Disc) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await self._open_bet(interaction, disc)

        return callback

    async def _open_bet(self, interaction: discord.Interaction, disc: Disc) -> None:
        """Abre el formulario de la apuesta (las comprobaciones solo miran memoria)."""
        if self.phase is not Phase.PLAYING or not grada_open(self.board):
            await interaction.response.send_message("La grada ya está cerrada.", ephemeral=True)
            return
        if self.disc_of(interaction.user.id) is not None:
            await interaction.response.send_message(
                "Tú ya te juegas la partida: la grada es para el público.", ephemeral=True
            )
            return
        if interaction.user.id in self.crowd:
            await interaction.response.send_message(
                "Ya tienes tu apuesta en este duelo.", ephemeral=True
            )
            return
        await interaction.response.send_modal(BetModal(self, disc))

    async def place(self, interaction: discord.Interaction, disc: Disc, text: str) -> None:
        """Cobra una apuesta de grada (el formulario ya está aceptado)."""
        user = interaction.user
        balance = await self.cog.economy.balance(self.guild_id, user.id)
        try:
            stake = parse_amount(text, balance)
        except ValueError as error:
            await notify(interaction, str(error))
            return
        if stake <= 0 or stake > balance:
            await notify(interaction, insufficient_text(balance, stake))
            return
        async with self._lock:
            if self.phase is not Phase.PLAYING or not grada_open(self.board):
                await notify(interaction, "La grada ya está cerrada.")
                return
            if user.id in self.crowd:
                await notify(interaction, "Ya tienes tu apuesta en este duelo.")
                return
            try:
                await self.cog.economy.place_bet(self.guild_id, user.id, game=GAME, stake=stake)
            except InsufficientFundsError as error:
                await notify(interaction, insufficient_text(error.balance, stake))
                return
            self.crowd[user.id] = Spectator(user, disc, stake, interaction)
        await notify(
            interaction,
            f"🎟️ {format_amount(stake)} por {disc.emoji} **{self.member(disc).display_name}**. "
            "Te digo cómo acaba en cuanto caiga la última ficha.",
        )
        if not self._busy:
            await self.edit_message(embed=self.embed(self.playing_text()), view=self.view.playing())
        await renta.remind(self.cog.bot, interaction)

    # -- Partida ----------------------------------------------------------------------------

    def column_callback(self, column: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await self.drop(interaction, column)

        return callback

    def _key(self) -> tuple[Any, ...]:
        return ("duel", len(self.board.moves))

    def prepare(self) -> None:
        """Pinta en segundo plano cada columna de quien toca, dormirse y rendirse."""
        board, meta = self.board, self.meta()
        order: list[Hashable] = [*center_out(board.legal()), "timeout", *Disc]

        async def paint(item: Hashable) -> Media | Played:
            if isinstance(item, int):
                return await self._resolve(board, item, meta)
            if item == "timeout":
                return await self._end_media(board, meta, board.turn, Ending.TIMEOUT)
            assert isinstance(item, Disc)
            return await self._end_media(board, meta, item, Ending.RESIGN)

        self.ahead.plan(self._key(), order, paint)

    def _banner_for(self, board: Board, loser: Disc | None, ending: Ending) -> dict[str, Any]:
        if ending is Ending.DRAW:
            return banner("TABLAS", "Tablero lleno y nadie gobierna", kind="draw")
        assert loser is not None
        winner = self.players[loser.other].display_name
        if ending is Ending.TIMEOUT:
            return banner("¡SE DUERME!", f"Gana {winner} por tiempo", kind="time")
        if ending is Ending.RESIGN:
            return banner("SE RINDE", f"Gana {winner}", kind="lose")
        return banner(f"¡GANA {winner.upper()[:14]}!", "Cuatro en raya", kind="win")

    async def _resolve(self, board: Board, column: int, meta: dict[str, Any]) -> Played:
        after = board.play(column)
        end = None
        if after.winner is not None:
            end = self._banner_for(after, after.winner.other, Ending.WIN)
        elif after.full:
            end = self._banner_for(after, None, Ending.DRAW)
        steps, last = move_steps(board, [after.last], end=end)
        return Played(after, await self.cog.renderer.animate(meta, steps, last))

    async def _end_media(
        self, board: Board, meta: dict[str, Any], loser: Disc, ending: Ending
    ) -> Media:
        end = self._banner_for(board, loser, ending)
        return await self.cog.renderer.animate(
            meta, end_steps(board, end=end), board_state(board, end={**end, "a": 1.0})
        )

    async def drop(self, interaction: discord.Interaction, column: int) -> None:
        """Un botón 1-7: solo quien toca suelta ficha."""
        disc = self.disc_of(interaction.user.id)
        if disc is None:
            await interaction.response.send_message(
                "Tú no juegas este duelo. Apuesta en la grada o reta a alguien con "
                f"`{COMMAND} @alguien`.",
                ephemeral=True,
            )
            return
        if self.phase is not Phase.PLAYING or self._busy:
            await interaction.response.defer()
            return
        if disc is not self.board.turn:
            await interaction.response.send_message("Espera tu turno.", ephemeral=True)
            return
        if column not in self.board.legal():
            await interaction.response.send_message("Esa columna está llena.", ephemeral=True)
            return
        await ack(interaction)
        self.interactions[interaction.user.id] = interaction
        async with self._lock:
            if self.phase is not Phase.PLAYING or disc is not self.board.turn or self._busy:
                return
            self._busy = True
            try:
                played = await self.ahead.take(self._key(), column)
                self.ahead.drop()
                if played is None:
                    self.view.playing(busy=True)

                    async def cover() -> None:
                        try:
                            await edit(interaction, view=self.view)
                        except discord.HTTPException:
                            logger.debug("No se pudo apagar el duelo", exc_info=True)

                    _, played = await asyncio.gather(
                        cover(), self._resolve(self.board, column, self.meta())
                    )
                if self.timer is not None:
                    self.timer.cancel()
                before = self.board
                self.board = played.board
                self.note = self._note(before, column, disc)
                if self.board.finished:
                    loser = self.board.winner.other if self.board.winner else None
                    ending = Ending.WIN if loser else Ending.DRAW
                    await self._finish(played.media, loser, ending)
                    return
                self.deadline = self.cog.wall_clock() + TURN_SECONDS
                await edit(
                    interaction,
                    embed=self.embed(self.playing_text(), image=GIF_NAME),
                    attachments=[discord.File(io.BytesIO(played.media.gif), filename=GIF_NAME)],
                    view=self.view.playing(),
                )
                self._arm_timer()
                self.prepare()
            except discord.HTTPException:
                logger.warning("No se pudo enseñar una jugada de un duelo", exc_info=True)
            finally:
                self._busy = False

    def _note(self, before: Board, column: int, disc: Disc) -> str:
        name = self.players[disc].display_name
        line = f"{disc.emoji} {name} suelta en la **{column + 1}**."
        if column in before.threats(disc.other) and column not in before.threats(disc):
            line += " 🧱 ¡Y tapa la raya!"
        if threats := self.board.threats(disc):
            line += f" ⚠️ Amenaza en la **{_cols_text(threats)}**."
        return line

    def _arm_timer(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
        self.timer = asyncio.create_task(
            self._turn_timeout(len(self.board.moves)), name=f"{GAME}-turno"
        )

    async def _turn_timeout(self, moves: int) -> None:
        await self.cog.sleep(max(0.0, self.deadline - self.cog.wall_clock()))
        async with self._lock:
            if self.phase is not Phase.PLAYING or len(self.board.moves) != moves or self._busy:
                return
            self._busy = True
            try:
                loser = self.board.turn
                media = await self.ahead.take(self._key(), "timeout")
                self.ahead.drop()
                if media is None:
                    media = await self._end_media(self.board, self.meta(), loser, Ending.TIMEOUT)
                await self._finish(media, loser, Ending.TIMEOUT)
            finally:
                self._busy = False

    async def resign(self, interaction: discord.Interaction) -> None:
        """🏳️: el que lo pulsa se rinde (cuando sea, toque o no)."""
        disc = self.disc_of(interaction.user.id)
        if disc is None:
            await interaction.response.send_message("Tú no juegas este duelo.", ephemeral=True)
            return
        if self.phase is not Phase.PLAYING or self._busy:
            await interaction.response.defer()
            return
        await ack(interaction)
        self.interactions[interaction.user.id] = interaction
        async with self._lock:
            if self.phase is not Phase.PLAYING or self._busy:
                return
            self._busy = True
            try:
                if self.timer is not None:
                    self.timer.cancel()
                media = await self.ahead.take(self._key(), disc)
                self.ahead.drop()
                if media is None:
                    media = await self._end_media(self.board, self.meta(), disc, Ending.RESIGN)
                await self._finish(media, disc, Ending.RESIGN)
            finally:
                self._busy = False

    # -- Final ------------------------------------------------------------------------------

    async def _finish(self, media: Media, loser: Disc | None, ending: Ending) -> None:
        """Paga, enseña el final y apunta la partida de los dos (se llama con el candado)."""
        self.phase = Phase.DONE
        self.ahead.drop()
        winner = loser.other if loser is not None else None
        if self.stake:
            for disc in Disc:
                amount = self.stake if winner is None else duel_prize(self.stake) * (disc is winner)
                self.settlements[disc] = await self.cog.pay(
                    self.guild_id, self.players[disc].id, amount
                )
        payouts = grada_payouts({k: s.bet for k, s in self.crowd.items()}, winner)
        for user_id, spectator in self.crowd.items():
            spectator.payout = payouts[user_id]
            spectator.settlement = await self.cog.pay(self.guild_id, user_id, spectator.payout)
        waiting = f"# {random.choice(WAITING_LINES)}"
        await self.edit_message(
            content=None,
            embed=self.embed(waiting, image=GIF_NAME),
            attachments=[discord.File(io.BytesIO(media.gif), filename=GIF_NAME)],
            view=None,
        )
        settle = asyncio.create_task(self._settle_both(loser, ending))
        await self.cog.sleep(media.seconds + REVEAL_MARGIN_SECONDS)
        settled = await settle
        lines = self.result_lines(winner, loser, ending)
        for disc in Disc:
            lines += settled[disc].lines
        await self.edit_message(
            content=None,
            embed=self.embed("\n".join(lines), color=COLOR_DRAW if winner is None else COLOR_WIN),
            attachments=[discord.File(io.BytesIO(media.png), filename=PNG_NAME)],
            view=None,
        )
        self.cog.release(self)
        await self.tell_everyone(winner, settled)
        await self.track(winner, loser, ending, settled)

    def result_lines(self, winner: Disc | None, loser: Disc | None, ending: Ending) -> list[str]:
        prize = duel_prize(self.stake)
        if winner is None or loser is None:
            lines = [f"# 🤝 {random.choice(DRAW_LINES)}"]
            if self.stake:
                lines.append(f"Cada uno recupera sus {format_amount(self.stake)}.")
        else:
            champ, beaten = self.players[winner], self.players[loser]
            if ending is Ending.TIMEOUT:
                lines = [f"# 😴 {beaten.display_name} {random.choice(SLEEP_LINES)}"]
                lines.append(f"Gana {winner.emoji} {champ.mention} por tiempo.")
            elif ending is Ending.RESIGN:
                lines = [f"# 🏳️ {random.choice(RESIGN_LINES)}"]
                lines.append(f"{beaten.mention} se rinde y gana {winner.emoji} {champ.mention}.")
            else:
                directions = {line.direction for line in self.board.winning_lines}
                how = " y ".join(sorted({DIRECTION_NAMES[d] for d in directions}))
                lines = [f"# 🏆 ¡Gana {winner.emoji} {champ.display_name}!"]
                lines.append(
                    f"{champ.mention} le hace a {beaten.mention} cuatro en raya {how} "
                    f"en {self.board.count(winner)} fichas."
                )
            if self.stake:
                lines.append(
                    f"Se lleva **{format_amount(prize)}** (+{format_amount(prize - self.stake)})."
                )
        if self.crowd:
            refund = all(s.payout == s.stake for s in self.crowd.values())
            winners = [s for s in self.crowd.values() if s.payout > s.stake]
            if refund:
                lines.append("🎟️ La grada recupera lo suyo.")
            elif winners:
                best = max(winners, key=lambda s: s.payout - s.stake)
                lines.append(
                    f"🎟️ {len(winners)} de {len(self.crowd)} en la grada aciertan. El que más: "
                    f"{best.member.mention}, +{format_amount(best.payout - best.stake)}."
                )
            else:
                lines.append("🎟️ Nadie en la grada lo vio venir.")
        return lines

    async def _settle_both(self, loser: Disc | None, ending: Ending) -> dict[Disc, Settled]:
        """Ficha, retos y racha de los dos jugadores."""
        result: dict[Disc, Settled] = {}
        for disc in Disc:
            if loser is None:
                mine = Ending.DRAW
            elif disc is loser:
                mine = ending if ending in (Ending.RESIGN, Ending.TIMEOUT) else Ending.LOSS
            else:
                mine = Ending.WIN
            blocks, missed = blocks_and_misses(self.board, disc)
            event = GameEvent(
                ending=mine,
                disc=disc,
                level=None,
                board=self.board,
                blocks=blocks,
                missed=missed,
                stake=self.stake,
            )
            member = self.players[disc]
            settled = await self.cog.settle(self.guild_id, member, event, name=member.display_name)
            if settled.record is not None:
                self.records[disc] = settled.record.after
            result[disc] = settled
        return result

    async def tell_everyone(self, winner: Disc | None, settled: dict[Disc, Settled]) -> None:
        """A cada uno, en privado, lo suyo: lo cobrado, Hacienda y su mascota."""
        prize = duel_prize(self.stake)
        for disc in Disc:
            member = self.players[disc]
            if not self.stake and not settled[disc].paid:
                continue
            won = winner is disc
            net = 0 if winner is None else (prize if won else 0) - self.stake
            if not self.stake:
                text = "🎯 Retos cobrados."
            elif winner is None:
                text = f"🤝 Tablas: recuperas tus {format_amount(self.stake)}."
            elif won:
                text = f"🏆 Ganas el duelo: +{format_amount(net)}"
            else:
                text = f"💀 Pierdes el duelo: -{format_amount(self.stake)}"
            await self._tell(
                self.interactions.get(member.id), member.id, text, self.settlements.get(disc),
                self.stake, net,
            )  # fmt: skip
            if settled[disc].paid and (interaction := self.interactions.get(member.id)):
                await renta.remind(self.cog.bot, interaction)
        for spectator in self.crowd.values():
            net = spectator.payout - spectator.stake
            if spectator.payout == spectator.stake:
                text = f"🎟️ Te devuelven tus {format_amount(spectator.stake)}."
            elif spectator.payout:
                text = (
                    f"🎟️ ¡Acertaste! Cobras {format_amount(spectator.payout)} "
                    f"(+{format_amount(net)})."
                )
            else:
                text = f"🎟️ Fallaste: -{format_amount(spectator.stake)}."
            await self._tell(
                spectator.interaction, spectator.member.id, text, spectator.settlement,
                spectator.stake, net,
            )  # fmt: skip

    async def _tell(
        self,
        interaction: discord.Interaction | None,
        user_id: int,
        text: str,
        settlement: BetSettlement | None,
        stake: int,
        net: int,
    ) -> None:
        if interaction is None:
            return
        if settlement is not None and (tax := gambling_tax_line(settlement)):
            text += f"\n{tax}"
        balance = settlement.balance if settlement is not None else 0
        moment = (
            bet_moment(stake=stake, net=net, balance_after=balance)
            if stake
            else Moment(Event.MONEY)
        )
        if hint := await renta.hint(self.cog.bot, self.guild_id, user_id, moment):
            text += f"\n{hint}"
        try:
            await interaction.followup.send(text, ephemeral=True)
        except discord.HTTPException:
            logger.debug("No se pudo mandar el resultado privado de un duelo", exc_info=True)

    async def track(
        self,
        winner: Disc | None,
        loser: Disc | None,
        ending: Ending,
        settled: dict[Disc, Settled],
    ) -> None:
        """Logros y estadísticas del casino de cada uno, después de enseñar el resultado."""
        when = datetime.fromtimestamp(self.cog.wall_clock(), TIMEZONE)
        bets = {k: s.bet for k, s in self.crowd.items()}
        crowd_pool = sum(grada_totals(bets).values())
        refund = all(s.payout == s.stake for s in self.crowd.values())
        prize = duel_prize(self.stake)
        for disc in Disc:
            member = self.players[disc]
            mine = settled[disc]
            won = winner is disc
            net = 0 if winner is None or not self.stake else (prize if won else 0) - self.stake
            record = mine.record.after if mine.record else self.records[disc]
            event_ending = (
                Ending.DRAW
                if loser is None
                else Ending.WIN
                if won
                else ending
                if ending in (Ending.RESIGN, Ending.TIMEOUT)
                else Ending.LOSS
            )
            blocks, missed = blocks_and_misses(self.board, disc)
            event = GameEvent(
                ending=event_ending,
                disc=disc,
                level=None,
                board=self.board,
                blocks=blocks,
                missed=missed,
                stake=self.stake,
            )
            delta = enraya_stats(
                event,
                record,
                when=when,
                net=net,
                crowd_pool=crowd_pool,
                rake=(2 * self.stake - prize) // 2 if self.stake and winner is not None else 0,
                rival_slept=won and ending is Ending.TIMEOUT,
                retos=len(mine.record.completed) if mine.record else 0,
                retos_full=mine.retos_full,
            )
            if not self.stake:
                await logros.track(self.cog.bot, self.guild_id, member, self.channel, delta)
                continue
            settlement = self.settlements.get(disc)
            balance = settlement.balance if settlement else 0
            tax = settlement.tax_delta if settlement else 0
            delta.merge(
                casino_stats(stake=self.stake, net=net, balance_after=balance, tax_delta=tax)
            )
            await logros.casino_play(
                self.cog.bot, self.guild_id, member, self.channel, delta, net=net
            )
            await apuestas.record(
                self.cog.bot,
                self.guild_id,
                member,
                game=GAME,
                stake=self.stake,
                net=net,
                balance_after=balance,
                tax=tax,
                details=(("won", int(won)), ("started", int(self.started_at))),
            )
            if won and net >= SHOUT_NET:
                await self.cog.shout(
                    self.channel,
                    f"📣 🔴 ¡{member.mention} gana un 4 en raya de "
                    f"{format_amount(self.stake)} por cabeza! **+{format_amount(net)}**",
                    member,
                )
        for spectator in self.crowd.values():
            stake = spectator.stake
            net = spectator.payout - stake
            settlement = spectator.settlement
            balance = settlement.balance if settlement else 0
            tax = settlement.tax_delta if settlement else 0
            won = winner is not None and spectator.disc is winner
            odds_cents = spectator.payout * 100 // stake if won and not refund else 0
            delta = enraya_bet_stats(
                won=won,
                refunded=refund,
                odds_cents=odds_cents,
                rake=0 if refund else int(stake * POOL_CUT),
                when=when,
            )
            delta.merge(casino_stats(stake=stake, net=net, balance_after=balance, tax_delta=tax))
            await logros.casino_play(
                self.cog.bot, self.guild_id, spectator.member, self.channel, delta, net=net
            )
            await apuestas.record(
                self.cog.bot,
                self.guild_id,
                spectator.member,
                game=GAME,
                stake=stake,
                net=net,
                balance_after=balance,
                tax=tax,
                details=(("won", int(won)), ("started", int(self.started_at))),
            )

    async def shutdown(self) -> None:
        """Cierre ordenado: con la partida a medias se devuelve lo apostado a todos."""
        async with self._lock:
            if self.phase is Phase.PLAYING:
                if self.stake:
                    for member in self.members:
                        await self.cog.pay(self.guild_id, member.id, self.stake)
                for user_id, spectator in self.crowd.items():
                    await self.cog.pay(self.guild_id, user_id, spectator.stake)
                self.crowd.clear()
                text = "🛑 Duelo suspendido. Se devuelve lo apostado."
            elif self.phase is Phase.CHALLENGE:
                text = "🛑 Reto cancelado: el bot cierra la mesa."
            else:
                return
            self.phase = Phase.DONE
        if self.timer is not None:
            self.timer.cancel()
        self.ahead.drop()
        await self.edit_message(content=None, embed=self.embed(text), view=None, attachments=[])


# -- Cog ------------------------------------------------------------------------------------


class Enraya(commands.Cog, name="Enraya"):
    """4 en raya contra Jovani (con retos del día) y duelos entre miembros con grada."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        repository: EnrayaRepository,
        casino_channel_ids: CasinoChannelIds = frozenset(),
        rng: random.Random | None = None,
        renderer: EnrayaScene | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        wall_clock: Callable[[], float] = time.time,
        ahead: bool | None = None,
    ) -> None:
        """
        Args:
            ahead: Si las mesas pintan por adelantado cada botón. Por defecto, solo
                con el dibujo de verdad: con un `renderer` de prueba cada dibujo de
                más gastaría el azar de guion.
        """
        self.bot = bot
        self.economy = economy
        self.repository = repository
        self.casino_channel_ids = casino_channel_ids
        # `secrets` usa el azar del sistema operativo: no se puede predecir.
        self.rng = rng or secrets.SystemRandom()
        self.ahead = renderer is None if ahead is None else ahead
        self.renderer = renderer or EnrayaScene()
        self.sleep = sleep
        self.wall_clock = wall_clock
        self.panels: set[SoloTable] = set()
        self.duels: set[Duel] = set()
        #: Quién está en un reto o un duelo (servidor, miembro): uno a la vez.
        self.busy: set[tuple[int, int]] = set()

    async def cog_unload(self) -> None:
        """Al apagar, devuelve lo apostado en los duelos a medias y cierra el pintor."""
        for duel in list(self.duels):
            try:
                await duel.shutdown()
            except Exception:
                logger.exception("No se pudo cerrar un duelo de 4 en raya al apagar")
        self.duels.clear()
        for panel in list(self.panels):
            panel.ahead.drop()
            panel.stop()
        self.panels.clear()
        await self.renderer.close()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra las fichas y los retos del servidor que deja el bot."""
        await self.repository.forget_guild(guild.id)

    def release(self, duel: Duel) -> None:
        """Libera a los dos jugadores de un duelo terminado."""
        self.duels.discard(duel)
        for member in duel.members:
            self.busy.discard((duel.guild_id, member.id))

    def today(self) -> date:
        return datetime.fromtimestamp(self.wall_clock(), TIMEZONE).date()

    async def pay(self, guild_id: int, user_id: int, amount: int) -> BetSettlement | None:
        """Paga lo devuelto por un duelo o la grada (`pay_winnings`, 0 si pierde).

        Tratamiento fiscal: juego (ver la docstring del módulo). `None` si el
        premio pasaría del saldo máximo y no se pudo pagar.
        """
        try:
            return await self.economy.pay_winnings(guild_id, user_id, game=GAME, amount=amount)
        except BalanceLimitError:
            logger.warning("Premio del 4 en raya por encima del saldo máximo; no se paga.")
            return None

    async def settle(
        self, guild_id: int, member: discord.abc.User, event: GameEvent, *, name: str = ""
    ) -> Settled:
        """Apunta una partida en la ficha de `member` y paga los retos que cumpla.

        Tratamiento fiscal de los retos: premio con retención de IRPF
        (`EconomyService.pay_income`), ganancia patrimonial que no procede de una
        transmisión (art. 33.1 LIRPF), como los retos del club del póker.

        Args:
            name: Si se da, las líneas lo nombran (en un duelo salen las de los dos).
        """
        today = self.today()
        retos = daily_challenges(guild_id, member.id, today)
        settled = Settled()
        try:
            record = await self.repository.record_game(
                guild_id,
                member.id,
                event,
                day=today,
                progress=progress_for(retos, event),
                goals={c.key: c.goal for c in retos},
            )
        except Exception:
            logger.exception("No se pudo apuntar una partida del 4 en raya")
            return settled
        settled.record = record
        who = f"{short_name(name)} " if name else ""
        by_key = {c.key: c for c in retos}
        for key in record.completed:
            challenge = by_key[key]
            gross = reward_for(challenge, record.after.day_streak)
            try:
                paid = await self.economy.pay_income(
                    guild_id, member.id, gross=gross, concept=f"{GAME}:reto:{key}"
                )
            except BalanceLimitError:
                continue
            settled.paid += gross
            settled.tax += paid.tax
            tax = f" (IRPF -{format_amount(paid.tax)})" if paid.tax else ""
            settled.lines.append(
                f"-# 🎯 {who}cumple el reto «{challenge.label}»: +{format_amount(gross)}{tax}"
            )
        done = sum(1 for c in retos if record.progress.get(c.key, 0) >= c.goal)
        if record.completed and done == len(retos):
            settled.retos_full = True
            settled.lines.append(f"-# 🏅 {who}completa los tres retos de hoy.")
        if record.after.day_streak > record.before.day_streak and record.after.day_streak >= 2:
            pct = streak_multiplier_pct(record.after.day_streak)
            settled.lines.append(
                f"-# 🔥 {who}lleva {record.after.day_streak} días seguidos jugando: retos +{pct} %."
            )
        return settled

    async def shout(self, channel: object, text: str, user: discord.abc.User) -> None:
        """Anuncia en el canal los duelos gordos."""
        if not isinstance(channel, discord.abc.Messageable):
            return
        try:
            await channel.send(
                text, allowed_mentions=discord.AllowedMentions(users=[user], everyone=False)
            )
        except discord.HTTPException:
            logger.debug("No se pudo anunciar un duelo de 4 en raya", exc_info=True)

    async def enraya_embed(self, guild_id: int, member: discord.abc.User) -> discord.Embed:
        """La sección 🔴 4 en raya de `perfil` (y el botón 🎯): ficha, racha y retos de hoy."""
        record = await self.repository.get(guild_id, member.id)
        today = self.today()
        progress = await self.repository.daily(guild_id, member.id, today)
        ranking = await self.repository.ranking(guild_id, limit=100)
        place = next((i for i, (uid, _r) in enumerate(ranking, 1) if uid == member.id), None)
        alive = record.last_day is not None and (today - record.last_day).days <= 1
        streak = record.day_streak if alive else 0
        lines = [player_line(record)]
        if record.games:
            lines.append(
                f"{record.losses} derrota{'s' if record.losses != 1 else ''} · "
                f"{round(100 * record.wins / record.games)} % de victorias"
            )
        if record.best_level >= 0:
            best = LEVELS[record.best_level]
            lines.append(f"Mejor victoria contra Jovani: {best.emoji} {best.name}")
        if place is not None:
            lines.append(f"🏟️ Puesto {place} del servidor")
        pct = streak_multiplier_pct(max(streak, 1))
        days = "día" if streak == 1 else "días"
        lines.append(f"🔥 Racha: **{streak} {days}** jugando (retos +{pct} %, hasta +100 %).")
        lines.append("### 🎯 Retos de hoy")
        for challenge in daily_challenges(guild_id, member.id, today):
            got, done = progress.get(challenge.key, (0, False))
            reward = reward_for(challenge, max(streak, 1))
            mark = "✅" if done else "▫️"
            lines.append(
                f"{mark} **{TIER_NAMES[challenge.tier]}** · {challenge.label} "
                f"({min(got, challenge.goal)}/{challenge.goal}) · {format_amount(reward)}"
            )
        lines.append(f"-# Se renuevan a medianoche. `{COMMAND}` para jugar.")
        return discord.Embed(
            title=f"🔴 4 en raya · {member.display_name}",
            description="\n".join(lines),
            color=COLOR_IDLE,
        )

    # -- 4enraya ----------------------------------------------------------------------------

    async def _enraya_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        rival: discord.abc.User | None,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> Duel | None:
        """Lógica compartida de `/4enraya` y `.4enraya`: abre tu mesa o reta a alguien.

        Returns:
            El duelo si ha retado a alguien (para guardar la interacción de quien reta).
        """
        if guild is None:
            await send_error("El 4 en raya solo se juega dentro de un servidor.")
            return None
        if error := casino_channel_error(self.casino_channel_ids, channel, "El 4 en raya"):
            await send_error(error)
            return None
        if rival is None:
            if amount_text:
                await send_error(
                    "Contra Jovani no se apuesta: pagan los 🎯 retos del día. "
                    f"Para jugarte dinero, reta a alguien: `{COMMAND} @alguien 500`."
                )
                return None
            await self._open_panel(guild.id, channel, user, send)
            return None
        if rival.id == user.id:
            await send_error("No puedes retarte a ti mismo. Para eso está Jovani.")
            return None
        if rival.bot:
            await send_error(f"Los bots no juegan. Para eso está Jovani: `{COMMAND}`.")
            return None
        stake = 0
        if amount_text:
            balance = await self.economy.balance(guild.id, user.id)
            try:
                stake = parse_amount(amount_text, balance)
            except ValueError as error:
                await send_error(str(error))
                return None
            if stake <= 0 or stake > balance:
                await send_error(insufficient_text(balance, stake))
                return None
        for member in (user, rival):
            if (guild.id, member.id) in self.busy:
                await send_error(f"{member.display_name} ya está en un duelo de 4 en raya.")
                return None
        duel = Duel(
            self, guild_id=guild.id, channel=channel, challenger=user, rival=rival, stake=stake
        )
        self.busy.update({(guild.id, user.id), (guild.id, rival.id)})
        self.duels.add(duel)
        try:
            await duel.open(send)
        except Exception:
            self.release(duel)
            raise
        await logros.track(
            self.bot, guild.id, user, channel, StatDelta(add={"enraya_challenges": 1})
        )
        return duel

    async def _open_panel(
        self,
        guild_id: int,
        channel: object,
        user: discord.abc.User,
        send: Callable[..., Awaitable[discord.Message]],
    ) -> None:
        panel = SoloTable(self, guild_id=guild_id, owner=user, channel=channel)
        panel.record = await self.repository.get(guild_id, user.id)
        png = await self.renderer.still(panel.meta(), board_state(panel.board, turn=Disc.RED))
        panel.rebuild()
        panel.message = await send(
            embed=panel.embed(), file=discord.File(io.BytesIO(png), filename=PNG_NAME), view=panel
        )
        self.panels.add(panel)
        panel.prepare()

    @app_commands.command(
        name=COMMAND,
        description="4 en raya: contra Jovani (con retos del día) o reta a alguien, con apuesta.",
    )
    @app_commands.describe(
        rival="Persona a la que retar (si no, juegas contra Jovani)",
        cantidad="Apuesta del duelo por cabeza: 500, 2k, all… (sin cantidad, amistoso)",
    )
    @app_commands.guild_only()
    async def enraya(
        self,
        interaction: discord.Interaction,
        rival: discord.Member | None = None,
        cantidad: str | None = None,
    ) -> None:
        """Abre tu mesa contra Jovani o reta a alguien.

        Solo en los canales del casino que elige `garito` (sin elegir, los de `CASINO_CHANNEL_IDS`)
        y en sus hilos. En un duelo con apuesta se cobra a los dos al aceptar.
        """
        responder = InteractionResponder(interaction)

        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            return await interaction.original_response()

        duel = await self._enraya_impl(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            rival=rival,
            send=send,
            send_error=responder.send_error,
        )
        if duel is not None:
            # Quien reta recibe aquí su resultado privado del duelo.
            duel.interactions[interaction.user.id] = interaction

    @commands.command(name=COMMAND)
    @commands.guild_only()
    async def enraya_text(self, ctx: commands.Context, *args: str) -> None:
        """Versión de texto: `.4enraya`, `.4enraya @alguien`, `.4enraya @alguien 500`.

        La cantidad y la mención van en cualquier orden.
        """
        responder = ContextResponder(ctx)
        rival = next((m for m in ctx.message.mentions if m.id != ctx.author.id), None)
        amounts = [a for a in args if not (a.startswith("<@") and a.endswith(">"))]
        if len(amounts) > 1:
            await responder.send_error(f"No entiendo tanto. Ejemplo: `.{COMMAND} @alguien 500`.")
            return

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        await self._enraya_impl(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            amount_text=amounts[0] if amounts else None,
            rival=rival,
            send=send,
            send_error=responder.send_error,
        )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía y las fichas del 4 en raya del bot."""
    await bot.add_cog(
        Enraya(
            bot,
            economy=bot.economy,
            repository=bot.enraya,
            casino_channel_ids=bot.casino_channels,
        )
    )
