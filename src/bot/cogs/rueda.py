"""La Rueda: `rueda`, una vuelta gratis al día con dinero, XP, cajas y objetos únicos.

Cada miembro abre su panel con `/rueda` o `.rueda` y pulsa 🎡 **Girar**. Hay una
vuelta por día (de 00:00 a 00:00 en hora canaria); si sale 🔁 Otra vuelta, se gira
otra vez en el acto. Las porciones miden lo que pesa su premio (`bot.services.rueda`)
y 📜 **Bases** enseña las probabilidades exactas.

**Sorteado antes de girar.** Al abrir el panel, la vuelta se sortea y se guarda
(`repositories.rueda`): reabrir el panel o reiniciar el bot no cambia lo que toca.
Con eso la mesa pinta el GIF en segundo plano mientras el miembro mira el panel
(`WheelView.prepare`) y, al pulsar, solo queda subirlo.

**Al pulsar:** se acepta el clic (`ack`), se marca la vuelta como girada (si dos
clics llegan a la vez, solo uno gana: `RuedaRepository.claim`), se da el premio y
se sube el GIF. Cuando acaba, se cambia por el PNG con el resultado. Si el premio no
se puede dar, la vuelta vuelve a quedar por girar.

**Premios y dinero** (Biblia, «Dinero, impuestos y la Renta»):

- Dinero: `EconomyService.pay_income`, ganancia patrimonial con retención (art. 33.1
  LIRPF), con su línea de Perro Sanxe. Es un sorteo promocional sin apuesta, no
  juego del casino: no pasa por `settle_bet` ni por `apuestas.record`.
- Cajas, la piruleta, las piezas de la Feria y los títulos del expositor van a la
  mochila (`bot.cogs.shop.give_key` y `give_numbered`) y su precio de catálogo se
  apunta como renta en especie de la semana (`EconomyService.record_prize_in_kind`).
- XP: `bot.cogs.message_stats.give_xp`, que paga la subida de nivel si la hay.
- Si el expositor se agota entre el sorteo y el clic, o el miembro ya tiene ese
  título, se pagan `SHOWCASE_FALLBACK` Y$ en dinero.

Después de enseñar el resultado: el aviso de la Renta (`renta.remind`), la mascota
(`renta.hint` con `Event.WHEEL`, que puede traer a la urraca), los logros de la
sección 🎪 Rueda y, con los premios gordos, un anuncio en el canal.

Solo se gira en los canales del casino que elige `garito` (sin elegir, en cualquiera).
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import message_stats as niveles
from bot.cogs import pets as mascotas
from bot.cogs import renta
from bot.cogs import shop as tienda
from bot.cogs.casino import casino_channel_error
from bot.repositories.rueda import RuedaRepository, SpinRow
from bot.services.achievements import rueda_stats
from bot.services.casino_channels import CasinoChannelIds
from bot.services.economy import (
    BalanceLimitError,
    EconomyService,
    IncomeResult,
    format_amount,
    tax_line,
)
from bot.services.levels import TIMEZONE
from bot.services.pets import Event, Moment, Mood
from bot.services.rueda import (
    PRIZE_BY_KEY,
    SHOWCASE_FALLBACK,
    Draw,
    PrizeKind,
    Wedge,
    chances,
    countdown,
    day_of,
    draw,
    layout,
    next_reset,
    showcase_for,
    streak,
    week_of,
)
from bot.services.rueda_catalog import (
    PIECE_BY_KEY,
    PIECE_WEIGHT,
    TITLE_BY_KEY,
    TITLE_KEYS,
    TITLE_STOCK,
    pick_piece,
)
from bot.services.rueda_render import (
    CELEBRATE,
    Media,
    banner_for,
    board_state,
    meta_state,
    result_state,
    spin_states,
)
from bot.services.rueda_scene import RuedaScene
from bot.services.taxes import TAX_COLLECTOR
from bot.services.telemetry import TimedLock
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import ContextResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

VIEW_TIMEOUT = 600
GIF_NAME = "rueda.gif"
PNG_NAME = "rueda.png"
#: Margen tras el GIF antes de poner el PNG (lo que tarda en llegar), más un poco
#: por cada MB: un GIF pesado tarda más en descargarse.
REVEAL_MARGIN_SECONDS = 0.5
REVEAL_PER_MB = 0.4
#: Premios que se anuncian en el canal.
SHOUT = frozenset({"expositor", "y15000", "y5000", "maletin"})

COLOR_IDLE = discord.Color.from_rgb(155, 89, 182)
COLOR_WIN = discord.Color.from_rgb(241, 196, 15)
COLOR_NOTHING = discord.Color.from_rgb(96, 100, 110)

# Frases del resultado: más de la mitad, normales; las bromas, espaciadas.
WIN_LINES = (
    "Buena vuelta.",
    "Premio para la mochila.",
    "¡Toma mangó!",
    "Mañana, otra.",
    "La Rueda está de tu parte.",
    "¿Qué? ¿Qué? ¡Premio!",
    "Eso es girar bien.",
    "Ni el tiovivo da tantas alegrías.",
)
AGAIN_LINES = (
    "Otra vuelta, gratis.",
    "Repite: la Rueda te deja girar otra vez.",
    "¡Otra! ¡Otra!",
    "Vuelve a girar.",
)
NOTHING_LINES = (
    "Hoy no ha tocado. Mañana hay otra vuelta.",
    "Nada por hoy.",
    "Inspección: la Rueda también tiene días malos.",
    "Mañana será otro día.",
    f"{TAX_COLLECTOR} revisa la Rueda y no encuentra nada que llevarse. Tú tampoco.",
)


def _shuffle_pick(lines: tuple[str, ...], seed: int) -> str:
    return lines[random.Random(seed).randrange(len(lines))]


@dataclass(slots=True)
class Award:
    """Lo que se ha dado en una vuelta, para el texto, los logros y la mascota."""

    prize: str
    lines: list[str] = field(default_factory=list)
    money: int = 0
    xp: int = 0
    in_kind: int = 0
    income: IncomeResult | None = None


def _resolve(wedges: tuple[Wedge, ...], spin: Draw) -> Draw:
    """La vuelta sobre las porciones de ahora.

    Si cambiaron entre el sorteo y el giro (se encendieron o apagaron los niveles), la
    aguja va a la primera porción con el mismo premio.
    """
    if spin.wedge < len(wedges) and wedges[spin.wedge].prize.key == spin.prize:
        return spin
    for wedge in wedges:
        if wedge.prize.key == spin.prize:
            return Draw(wedge.index, spin.prize, 0.5, spin.seed, spin.item)
    return spin


def _detail(spin: Draw) -> str | None:
    """La línea pequeña del cartel que ya se sabe al sortear (la pieza, el título)."""
    prize = PRIZE_BY_KEY[spin.prize]
    if prize.kind is PrizeKind.PIECE and spin.item in PIECE_BY_KEY:
        piece = PIECE_BY_KEY[spin.item]
        return f"{piece.name} · {piece.rarity}"
    if prize.kind is PrizeKind.SHOWCASE and spin.item in TITLE_BY_KEY:
        return TITLE_BY_KEY[spin.item].name
    return None


class WheelView(ui.View):
    """El panel de la Rueda de un miembro: imagen, texto y botones.

    Guarda la vuelta pendiente (ya sorteada y en la base) y el GIF pintado por
    adelantado. No guarda dinero ni premios: los da la economía y la tienda.
    """

    def __init__(self, cog: Rueda, *, guild: discord.Guild, owner: discord.abc.User) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild = guild
        self.owner = owner
        self.channel: object = None
        self.message: discord.Message | None = None
        self.wedges: tuple[Wedge, ...] = layout()
        self.meta: dict[str, Any] = {}
        #: La vuelta que se gira al pulsar, o `None` si ya no quedan hoy.
        self.pending: SpinRow | None = None
        #: La última vuelta girada hoy (para enseñarla si no quedan).
        self.last: SpinRow | None = None
        self.text = ""
        self.color = COLOR_IDLE
        self._busy = False
        self._lock = TimedLock("rueda:WheelView._lock")
        self._plan: tuple[tuple[Any, ...], asyncio.Future[Media | None]] | None = None
        self._painting: asyncio.Task[None] | None = None
        self.spin_button = ui.Button(
            label="Girar", emoji="🎡", style=discord.ButtonStyle.success, custom_id="rueda:girar"
        )
        self.spin_button.callback = self._spin  # type: ignore[method-assign]
        self.rules_button = ui.Button(
            label="Bases", emoji="📜", style=discord.ButtonStyle.secondary, custom_id="rueda:bases"
        )
        self.rules_button.callback = self._rules  # type: ignore[method-assign]
        self.add_item(self.spin_button)
        self.add_item(self.rules_button)

    # -- Texto y botones ---------------------------------------------------------------

    def embed(self, *, image: str = PNG_NAME, text: str | None = None) -> discord.Embed:
        embed = discord.Embed(title="🎪 La Rueda", description=text or self.text, color=self.color)
        embed.set_image(url=f"attachment://{image}")
        embed.set_footer(text="Una vuelta gratis al día · 📜 Bases: probabilidades y premios")
        return embed

    def refresh_buttons(self) -> None:
        """El botón de girar según lo que queda hoy."""
        if self.pending is not None:
            again = self.pending.spin > 0
            self.spin_button.label = "Otra vuelta" if again else "Girar"
            self.spin_button.emoji = "🔁" if again else "🎡"
            self.spin_button.disabled = False
            self.spin_button.style = discord.ButtonStyle.success
        else:
            left = next_reset(self.cog.clock()) - self.cog.clock()
            self.spin_button.label = f"Vuelve en {countdown(left)}"
            self.spin_button.emoji = "⏳"
            self.spin_button.disabled = True
            self.spin_button.style = discord.ButtonStyle.secondary
        self.rules_button.disabled = False

    def disable_all(self) -> None:
        for item in self.children:
            if isinstance(item, ui.Button):
                item.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño gira su Rueda; las bases las puede mirar cualquiera."""
        custom_id = (interaction.data or {}).get("custom_id")
        if interaction.user.id == self.owner.id or custom_id == "rueda:bases":
            return True
        await interaction.response.send_message(
            f"Esta Rueda es de {self.owner.display_name}. Abre la tuya con `rueda`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        self.cog.views.discard(self)
        self.disable_all()
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                logger.debug("No se pudo cerrar el panel de la Rueda", exc_info=True)

    # -- GIF por adelantado --------------------------------------------------------------

    def _plan_key(self) -> tuple[Any, ...] | None:
        """Lo que decide el GIF de la vuelta pendiente: la vuelta y lo que se ve alrededor."""
        if self.pending is None:
            return None
        spin = self.pending.draw
        return (self.pending.day, self.pending.spin, spin, repr(self.meta))

    def prepare(self) -> None:
        """Pinta en segundo plano el GIF de la vuelta pendiente, si no lo está ya.

        Solo hay un GIF posible (lo que toca ya está sorteado), así que es un dibujo.
        No se cancela a medias: si mientras tanto cambia la vuelta, ese dibujo se tira.
        """
        key = self._plan_key()
        if not self.cog.ahead or key is None or self.is_finished():
            return
        if self._plan is not None and self._plan[0] == key:
            return
        future: asyncio.Future[Media | None] = asyncio.get_running_loop().create_future()
        self._plan = (key, future)
        previous = self._painting
        self._painting = asyncio.create_task(self._paint_ahead(key, future, previous))

    async def _paint_ahead(
        self,
        key: tuple[Any, ...],
        future: asyncio.Future[Media | None],
        previous: asyncio.Task[None] | None,
    ) -> None:
        if previous is not None:
            await asyncio.gather(previous, return_exceptions=True)
        media: Media | None = None
        if self._plan_key() == key and self.pending is not None:
            try:
                media = await self.paint(self.pending.draw)
            except Exception:
                logger.warning("No se pudo pintar por adelantado la Rueda", exc_info=True)
        if not future.done():
            future.set_result(media)

    def _take_plan(self) -> asyncio.Future[Media | None] | None:
        plan, self._plan = self._plan, None
        if plan is None or plan[0] != self._plan_key():
            return None
        return plan[1]

    async def paint(self, spin: Draw, detail: str | None = None) -> Media:
        """El GIF del giro de `spin` y el PNG del final (`detail`: la línea del cartel)."""
        prize = PRIZE_BY_KEY[spin.prize]
        banner = banner_for(spin.prize, prize.kind, detail or _detail(spin))
        states, durations = spin_states(
            self.wedges, spin, banner, hamster=self.meta.get("pet") == "hamster"
        )
        return await self.cog.renderer.spin(self.meta, states, durations)

    # -- Botones -------------------------------------------------------------------------

    async def _rules(self, interaction: discord.Interaction) -> None:
        """📜 Bases: las probabilidades de cada premio. Solo memoria: contesta directo."""
        await interaction.response.send_message(embed=rules_embed(self.wedges), ephemeral=True)

    async def _spin(self, interaction: discord.Interaction) -> None:
        """🎡 Girar: marca la vuelta, da el premio y enseña el giro."""
        if self._busy:
            await ack(interaction)
            return
        await ack(interaction)
        self._busy = True
        try:
            async with self._lock:
                await self._play(interaction)
        finally:
            self._busy = False

    async def _play(self, interaction: discord.Interaction) -> None:
        cog = self.cog
        row = self.pending
        if row is None:
            await notify(interaction, "Hoy ya has girado la Rueda. Vuelve mañana.")
            return
        ready = self._take_plan()
        now = cog.clock()
        if not await cog.repository.claim(self.guild.id, self.owner.id, row.day, row.spin, now):
            await notify(
                interaction, "Esa vuelta ya está girada. Abre la Rueda otra vez con `rueda`."
            )
            return
        spin = row.draw
        try:
            award = await cog.award(self.guild, self.owner, self.channel, row)
        except Exception:
            logger.exception("No se pudo dar el premio de la Rueda a %s", self.owner.id)
            await cog.repository.unclaim(self.guild.id, self.owner.id, row.day, row.spin)
            await notify(interaction, "No se ha podido dar el premio. La vuelta sigue pendiente.")
            return
        detail = None
        if award.prize != spin.prize:
            # El expositor se agotó: el GIF tiene que enseñar lo que se ha dado de verdad.
            spin = Draw(spin.wedge, award.prize, spin.stop, spin.seed)
            detail = "El expositor, en dinero"
            ready = None
        self.last = SpinRow(row.day, row.spin, spin, row.created_at, now)
        # Lo que queda hoy: otra vuelta si ha salido, si no, nada.
        self.pending = None
        if award.prize == "otra":
            self.pending = await cog.next_spin(self.guild.id, self.owner.id, row.day, self.wedges)

        for item in self.children:
            if isinstance(item, ui.Button):
                item.disabled = True

        async def painted() -> Media:
            media = await ready if ready is not None else None
            return media if media is not None else await self.paint(spin, detail)

        media: Media
        if ready is not None and ready.done() and ready.result() is not None:
            media = await painted()
        else:
            # Sin GIF listo, el panel apaga los botones ya: si no, el clic no se nota.
            waiting = edit(interaction, embed=self.embed(text="🎡 Girando…"), view=self)
            media, _ = await asyncio.gather(painted(), waiting)
        await edit(
            interaction,
            embed=self.embed(image=GIF_NAME, text="🎡 Girando…"),
            attachments=[discord.File(io.BytesIO(media.gif), filename=GIF_NAME)],
            view=self,
        )
        self.prepare()
        await asyncio.sleep(
            media.seconds + REVEAL_MARGIN_SECONDS + REVEAL_PER_MB * len(media.gif) / 1_000_000
        )
        hint = await cog.after_hint(self.guild.id, self.owner.id, award)
        self.text = cog.result_text(award, spin, hint=hint, pending=self.pending is not None)
        self.color = COLOR_NOTHING if award.prize == "nada" else COLOR_WIN
        self.refresh_buttons()
        try:
            await edit(
                interaction,
                embed=self.embed(),
                attachments=[discord.File(io.BytesIO(media.png), filename=PNG_NAME)],
                view=self,
            )
        except discord.HTTPException:
            logger.warning("No se pudo enseñar el resultado de la Rueda", exc_info=True)
        await renta.remind(cog.bot, interaction)
        await cog.after_spin(self.guild, self.owner, self.channel, row, award)


def rules_embed(wedges: tuple[Wedge, ...]) -> discord.Embed:
    """Las bases de la Rueda: cada premio con su probabilidad y lo que hace."""
    odds = chances(wedges)
    lines = []
    for key, chance in sorted(odds.items(), key=lambda kv: -kv[1]):
        prize = PRIZE_BY_KEY[key]
        name = prize.name[0].upper() + prize.name[1:]
        percent = f"{chance * 100:.1f}".replace(".", ",")
        lines.append(f"{prize.emoji} {name}: **{percent} %**")
    lines += [
        "",
        "Una vuelta al día, de 00:00 a 00:00 (hora canaria). Cada porción mide lo que pesa "
        "su premio: lo que ves es lo que hay.",
        f"🏆 El expositor es un título numerado que cambia cada lunes ({TITLE_STOCK} de cada "
        f"uno). Si se agota o ya lo tienes, se pagan {format_amount(SHOWCASE_FALLBACK)}.",
        "🧩 Las piezas de la colección de la Feria solo salen aquí. Completarla regala el "
        "Tapete de la Feria.",
        f"{TAX_COLLECTOR}: el dinero tributa en el IRPF con retención. Lo que no es dinero "
        "cuenta en la Renta de la semana por su precio de catálogo.",
    ]
    return discord.Embed(
        title="📜 Bases de la Rueda", description="\n".join(lines), color=COLOR_IDLE
    )


class Rueda(commands.Cog, name="Rueda"):
    """La Rueda de cada día: un sorteo gratis con premios de la economía y la tienda."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        repository: RuedaRepository,
        economy: EconomyService,
        casino_channel_ids: CasinoChannelIds = frozenset(),
        rng: random.Random | None = None,
        renderer: RuedaScene | None = None,
        ahead: bool | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Prepara el cog; el dibujo por defecto es `RuedaScene`.

        Args:
            ahead: Si los paneles pintan por adelantado el GIF de la vuelta pendiente.
                Por defecto, solo con el dibujo de verdad.
        """
        self.bot = bot
        self.repository = repository
        self.economy = economy
        self.casino_channel_ids = casino_channel_ids
        # `secrets` usa el azar del sistema operativo: no se puede predecir.
        self.rng = rng or secrets.SystemRandom()
        self.ahead = renderer is None if ahead is None else ahead
        self.renderer = renderer or RuedaScene()
        self.clock = clock
        self.views: set[WheelView] = set()

    async def cog_unload(self) -> None:
        for view in list(self.views):
            view.stop()
        self.views.clear()
        await self.renderer.close()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        await self.repository.delete_guild_data(guild.id)

    # -- Sorteo ------------------------------------------------------------------------

    async def _showcase(self, guild_id: int, day: Any) -> dict[str, Any] | None:
        """El expositor de esta semana (`{"key", "name", "left", "of"}`) o `None`."""
        left = await tienda.units_left(self.bot, guild_id, TITLE_KEYS)
        sold_out = frozenset(k for k in TITLE_KEYS if left.get(k, TITLE_STOCK) <= 0)
        key = showcase_for(week_of(day), TITLE_KEYS, sold_out)
        if key is None:
            return None
        return {
            "key": key,
            "name": TITLE_BY_KEY[key].name,
            "left": left.get(key, TITLE_STOCK),
            "of": TITLE_STOCK,
        }

    def _draw(self, wedges: tuple[Wedge, ...], showcase: str | None) -> Draw:
        wedge, stop, seed = draw(self.rng, wedges)
        item = None
        if wedge.prize.kind is PrizeKind.PIECE:
            item = pick_piece(self.rng.randrange(PIECE_WEIGHT)).key
        elif wedge.prize.kind is PrizeKind.SHOWCASE:
            item = showcase
        return Draw(wedge.index, wedge.prize.key, stop, seed, item)

    async def next_spin(
        self, guild_id: int, user_id: int, day: Any, wedges: tuple[Wedge, ...]
    ) -> SpinRow:
        """Sortea y guarda la vuelta que sigue a las de hoy (la primera o una «otra vuelta»)."""
        rows = await self.repository.day(guild_id, user_id, day)
        spin = rows[-1].spin + 1 if rows else 0
        showcase = await self._showcase(guild_id, day)
        new = self._draw(wedges, showcase["key"] if showcase else None)
        return await self.repository.create(guild_id, user_id, day, spin, new, self.clock())

    # -- Premios -------------------------------------------------------------------------

    async def _pay(self, guild_id: int, user_id: int, amount: int, award: Award) -> None:
        income = await self.economy.pay_income(
            guild_id, user_id, gross=amount, concept="rueda:premio"
        )
        award.money = amount
        award.income = income
        award.lines.append(tax_line(income.gross, income.tax, income.rate))

    async def _item(self, guild_id: int, user_id: int, key: str, award: Award) -> bool:
        item = await tienda.give_key(self.bot, guild_id, user_id, key)
        if item is None:
            return False
        award.in_kind = item.price
        await self.economy.record_prize_in_kind(
            guild_id, user_id, value=item.price, concept="rueda:especie"
        )
        return True

    async def award(
        self, guild: discord.Guild, member: discord.abc.User, channel: object, row: SpinRow
    ) -> Award:
        """Da el premio de una vuelta ya girada.

        Raises:
            RuntimeError: Si no se pudo dar (el cog deshace la vuelta).
        """
        spin = row.draw
        prize = PRIZE_BY_KEY[spin.prize]
        award = Award(prize=prize.key)
        g, u = guild.id, member.id
        if prize.kind is PrizeKind.MONEY:
            try:
                await self._pay(g, u, prize.amount, award)
            except BalanceLimitError:
                award.lines.append("-# Tu saldo está en el máximo: el premio no cabe.")
        elif prize.kind is PrizeKind.XP:
            if await niveles.give_xp(self.bot, guild, member, channel, prize.amount):
                award.xp = prize.amount
            else:
                award.lines.append("-# Los niveles están apagados en el servidor: no hay XP.")
        elif prize.kind in (PrizeKind.BOX, PrizeKind.TREAT):
            assert prize.item is not None
            if not await self._item(g, u, prize.item, award):
                raise RuntimeError(f"La tienda no tiene {prize.item}")
        elif prize.kind is PrizeKind.PIECE:
            piece = PIECE_BY_KEY.get(spin.item or "")
            if piece is None or not await self._item(g, u, piece.key, award):
                raise RuntimeError(f"La tienda no tiene la pieza {spin.item}")
            award.lines.append(f"🧩 {piece.emoji} **{piece.name}** ({piece.rarity}).")
        elif prize.kind is PrizeKind.SHOWCASE:
            given = await tienda.give_numbered(self.bot, g, u, spin.item) if spin.item else None
            if given is None:
                award.prize = "y15000"
                award.lines.append(
                    "🏆 El título del expositor ya no estaba (o ya lo tenías): "
                    f"te llevas {format_amount(SHOWCASE_FALLBACK)}."
                )
                await self._pay(g, u, SHOWCASE_FALLBACK, award)
                await self.repository.set_item(g, u, row.day, row.spin, "y15000", None)
            else:
                item, serial, edition = given
                award.in_kind = item.price
                await self.economy.record_prize_in_kind(
                    g, u, value=item.price, concept="rueda:especie"
                )
                award.lines.append(
                    f"🏆 {item.emoji} **{item.name}**, n.º {serial} de {edition}. "
                    "Lúcelo desde la mochila."
                )
        if award.in_kind:
            award.lines.append(
                f"-# 🐶 Premio en especie: {format_amount(award.in_kind)} cuentan en tu Renta "
                "de esta semana."
            )
        return award

    def result_text(self, award: Award, spin: Draw, *, hint: str | None, pending: bool) -> str:
        """El texto del resultado: qué ha tocado, cómo queda y cuándo hay otra vuelta."""
        prize = PRIZE_BY_KEY[award.prize]
        if prize.kind is PrizeKind.NOTHING:
            head = f"🔎 **Inspección.** {_shuffle_pick(NOTHING_LINES, spin.seed)}"
        elif prize.kind is PrizeKind.AGAIN:
            head = f"🔁 **Otra vuelta.** {_shuffle_pick(AGAIN_LINES, spin.seed)}"
        elif prize.kind is PrizeKind.SHOWCASE:
            head = f"🏆 **¡El expositor!** {_shuffle_pick(WIN_LINES, spin.seed)}"
        else:
            line = _shuffle_pick(WIN_LINES, spin.seed)
            head = f"{prize.emoji} Te ha tocado **{prize.name}**. {line}"
        lines = [head, *award.lines]
        if award.income is not None:
            lines.append(f"💳 Saldo: {format_amount(award.income.balance)}.")
        if hint:
            lines.append(hint)
        if pending:
            lines.append("Pulsa 🔁 **Otra vuelta** para girar de nuevo.")
        else:
            left = next_reset(self.clock()) - self.clock()
            lines.append(f"⏳ La próxima vuelta, en {countdown(left)}.")
        return "\n".join(lines)

    async def after_hint(self, guild_id: int, user_id: int, award: Award) -> str | None:
        """La línea de la mascota (y de la Renta, si el premio tributa)."""
        kind = PRIZE_BY_KEY[award.prize].kind
        feel = Mood.BAD if kind is PrizeKind.NOTHING else Mood.GOOD
        moment = Moment(Event.WHEEL, notable=award.prize in CELEBRATE, feel=feel)
        if award.money or award.in_kind:
            return await renta.hint(self.bot, guild_id, user_id, moment)
        return await mascotas.cameo(self.bot, guild_id, user_id, moment)

    async def after_spin(
        self,
        guild: discord.Guild,
        member: discord.abc.User,
        channel: object,
        row: SpinRow,
        award: Award,
    ) -> None:
        """Lo que va después de enseñar el resultado: logros y anuncio en el canal."""
        g, u = guild.id, member.id
        try:
            days = await self.repository.spin_days(g, u)
            rows = await self.repository.day(g, u, row.day)
            owned = await tienda.owned_keys(self.bot, g, u)
        except Exception:
            logger.exception("No se pudieron leer los datos de logros de la Rueda")
            return
        again_today = sum(1 for r in rows if r.claimed and r.draw.prize == "otra")
        delta = rueda_stats(
            award.prize,
            when=datetime.fromtimestamp(self.clock(), TIMEZONE),
            money=award.money,
            xp=award.xp,
            in_kind=award.in_kind,
            streak=streak(days, row.day),
            again_today=again_today,
            after_again=row.spin > 0,
            pieces_owned=sum(1 for key in PIECE_BY_KEY if key in owned),
            hamster=mascotas.active_species(self.bot, g, u) == "hamster",
        )
        await logros.track(self.bot, g, member, channel, delta)
        await self.shout(award, member, channel)

    async def shout(self, award: Award, member: discord.abc.User, channel: object) -> None:
        """Anuncia en el canal los premios gordos, para que se vean."""
        if award.prize not in SHOUT or not isinstance(channel, discord.abc.Messageable):
            return
        prize = PRIZE_BY_KEY[award.prize]
        try:
            await channel.send(
                f"📣 🎪 ¡{member.mention} ha ganado **{prize.name}** en la Rueda!",
                allowed_mentions=discord.AllowedMentions(users=[member], everyone=False),
            )
        except discord.HTTPException:
            logger.debug("No se pudo anunciar un premio de la Rueda", exc_info=True)

    # -- El panel ------------------------------------------------------------------------

    async def open_panel(
        self, guild: discord.Guild, channel: object, user: discord.abc.User
    ) -> tuple[WheelView, bytes]:
        """Prepara el panel de un miembro: sortea la vuelta si toca y pinta la rueda."""
        view = WheelView(self, guild=guild, owner=user)
        view.channel = channel
        today = day_of(self.clock())
        levels = await niveles.levels_enabled(self.bot, guild.id)
        view.wedges = layout(levels=levels)
        rows = await self.repository.day(guild.id, user.id, today)
        if not rows:
            view.pending = await self.next_spin(guild.id, user.id, today, view.wedges)
        elif not rows[-1].claimed:
            view.pending = rows[-1]
        else:
            view.last = rows[-1]
            if rows[-1].draw.prize == "otra":
                view.pending = await self.next_spin(guild.id, user.id, today, view.wedges)
        if view.pending is not None:
            view.pending = SpinRow(
                view.pending.day,
                view.pending.spin,
                _resolve(view.wedges, view.pending.draw),
                view.pending.created_at,
                view.pending.claimed_at,
            )
        showcase = await self._showcase(guild.id, today)
        days = await self.repository.spin_days(guild.id, user.id)
        pet = mascotas.active_species(self.bot, guild.id, user.id)
        view.meta = meta_state(view.wedges, showcase=showcase, streak=streak(days, today), pet=pet)
        view.text = self.intro_text(showcase, streak(days, today), view)
        view.refresh_buttons()
        if view.pending is not None:
            state = board_state(view.pending.draw)
        elif view.last is not None:
            last = _resolve(view.wedges, view.last.draw)
            prize = PRIZE_BY_KEY[last.prize]
            state = result_state(
                view.wedges, last, banner_for(last.prize, prize.kind, _detail(last))
            )
        else:
            state = board_state(None, hint=False)
        png = await self.renderer.board(view.meta, state)
        return view, png

    def intro_text(self, showcase: dict[str, Any] | None, days: int, view: WheelView) -> str:
        lines = ["Una vuelta gratis al día. Se renueva a las 00:00 (hora canaria)."]
        if showcase is not None:
            lines.append(
                f"🏆 Expositor de la semana: **{showcase['name']}** "
                f"(quedan {showcase['left']} de {showcase['of']})."
            )
        else:
            lines.append("🏆 Los títulos del expositor están agotados.")
        if days:
            lines.append(f"🔥 Racha: {days} {'día' if days == 1 else 'días'} seguidos.")
        if view.pending is None:
            left = next_reset(self.clock()) - self.clock()
            lines.append(f"Hoy ya has girado. ⏳ La próxima vuelta, en {countdown(left)}.")
        elif view.pending.spin > 0:
            lines.append("Te queda 🔁 **otra vuelta**.")
        return "\n".join(lines)

    async def _rueda_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        before: Callable[[], Awaitable[None]],
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> None:
        if guild is None:
            await send_error("La Rueda solo se gira dentro de un servidor.")
            return
        if error := casino_channel_error(self.casino_channel_ids, channel, "La Rueda"):
            await send_error(error)
            return
        await before()
        view, png = await self.open_panel(guild, channel, user)
        view.message = await send(
            embed=view.embed(), file=discord.File(io.BytesIO(png), filename=PNG_NAME), view=view
        )
        self.views.add(view)
        view.prepare()

    @app_commands.command(
        name="rueda",
        description="La Rueda: una vuelta gratis al día con dinero, XP, cajas y premios únicos.",
    )
    @app_commands.guild_only()
    async def rueda(self, interaction: discord.Interaction) -> None:
        """Abre tu Rueda del día. Solo en los canales del casino."""

        async def before() -> None:
            # Sortear, leer la tienda y pintar puede pasar de 3 s en el NAS.
            await interaction.response.defer(thinking=True)

        async def send(**kwargs: Any) -> discord.Message:
            return await interaction.followup.send(**kwargs, wait=True)

        async def send_error(text: str) -> None:
            await interaction.response.send_message(text, ephemeral=True)

        await self._rueda_impl(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            before=before,
            send=send,
            send_error=send_error,
        )

    @commands.command(name="rueda")
    @commands.guild_only()
    async def rueda_text(self, ctx: commands.Context) -> None:
        """Versión de texto: `.rueda`."""
        responder = ContextResponder(ctx)

        async def before() -> None:
            return None

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        await self._rueda_impl(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            before=before,
            send=send,
            send_error=responder.send_error,
        )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía y el repositorio compartidos del bot."""
    await bot.add_cog(
        Rueda(
            bot,
            repository=bot.rueda,
            economy=bot.economy,
            casino_channel_ids=bot.casino_channels,
        )
    )
