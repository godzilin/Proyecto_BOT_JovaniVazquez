"""El pico: `picar`, una tirada con un pico que cae rompiendo bloques.

Cada jugador abre su propio pozo, un mensaje con un botón por pico que solo él puede
pulsar. Al pulsar uno se paga su precio y el pico cae por un mundo de bloques: rebota,
gira y rompe lo que pilla. Cada golpe le quita vida; cada mineral roto suma premio, y los
bloques de multiplicador (oro ×2, diamante ×3, netherite ×5, baliza ×10) lo multiplican
todo. Cuando se queda sin vida, cobra lo acumulado. Si baja lo bastante pasa al Nether y
luego al End; en el fondo espera el dragón, que paga el premio máximo del pico.

Botones:

- Un pico por botón, con su precio (🪵 Madera 10 Y$… 🖤 Netherite 5.000 Y$). Todos
  devuelven lo mismo de media (99,5 %); cambia el riesgo y lo hondo que se llega.
- 📋 **Premios**: precios, topes y cómo de hondo suele llegar cada pico.
- 🏆 **Récords**: tu fila más honda con cada pico y el ranking del servidor.

Reglas, precios y calibración en `bot.services.picar`; la física, en
`bot.services.picar_physics`; el dibujo, en `bot.services.picar_scene`; las tiradas, en
`bot.repositories.picar`.

**Orden de una tirada** (`_dig`): se acepta el clic, se juega la semilla (en un hilo), se
cobra y paga de una vez con `settle_bet` y se apunta la tirada. Solo entonces se dibuja:
si el bot se cae a mitad del GIF, el dinero ya está movido y la tirada apuntada. La
partida se enseña en varios GIF seguidos (`PicarScene.parts`): cada uno se pone cuando
el anterior llega a su pausa final (el rótulo de la dimensión), y mientras se ve uno se
pinta el siguiente. Al acabar, el último se cambia por un PNG con el resultado y vuelven
los botones. Logros, estadísticas, Renta y anuncio van después, para no destripar nada.

**Calibración.** Al cargar, el cog comprueba en un hilo que `calibracion.json` vale para
esta física y estos precios (`picar.check`). Si no, el pozo no abre y el log dice por qué.

Si `CASINO_CHANNEL_IDS` está configurado, solo se juega en esos canales. Permisos del bot
en el canal: enviar mensajes, insertar enlaces y adjuntar archivos.
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands, ui
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, aspecto, renta
from bot.cogs.casino import casino_channel_error, insufficient_text
from bot.repositories.picar import PicarRepository, PlayRecord
from bot.services.achievements import casino_stats, picar_stats
from bot.services.economy import (
    BalanceLimitError,
    BetSettlement,
    EconomyService,
    InsufficientFundsError,
    format_amount,
    gambling_tax_line,
)
from bot.services.levels import TIMEZONE
from bot.services.pets import bet_moment
from bot.services.picar import (
    OFFERS,
    SCALE,
    Calibration,
    Outcome,
    check,
    format_mult,
    play,
)
from bot.services.picar_scene import UPLOAD_FIXED_MS, Part, PicarScene, upload_ms
from bot.services.picar_world import DRAGON_ROW, layer_at
from bot.services.taxes import TAX_COLLECTOR
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

GAME = "picar"
VIEW_TIMEOUT = 600
#: Parte del límite de subida del servidor que puede pesar cada GIF.
UPLOAD_SHARE = 0.8
#: Margen tras el final de la animación (el principio de su pausa) antes de cambiarla: la
#: pausa ya dura lo que tarda en verse el GIF siguiente (`picar_scene.swap_ms`).
REVEAL_MARGIN_SECONDS = 0.1
GIF_NAME = "picar.gif"
PNG_NAME = "picar.png"
#: Premio, en veces el precio, desde el que se anuncia en el canal.
SHOUT_MULTIPLIER = 50

EMOJIS = {
    "wood": "🪵",
    "stone": "🪨",
    "iron": "⛓️",
    "gold": "🥇",
    "diamond": "💎",
    "netherite": "🖤",
}

COLOR_IDLE = discord.Color.from_rgb(94, 162, 72)
COLOR_DIGGING = discord.Color.from_rgb(120, 110, 150)
COLOR_WIN = discord.Color.from_rgb(255, 196, 0)
COLOR_LOSS = discord.Color.from_rgb(80, 84, 92)
COLOR_DRAGON = discord.Color.from_rgb(170, 90, 255)

# Textos en el tono de Jovani Vázquez: la mitad, normales.
DIG_LINES = (
    "El pico cae.",
    "Abajo que va.",
    "A ver hasta dónde llega.",
    "Picando.",
    "¡Toma mangó, a la mina!",
    "Ni el túnel de Fomento avanza tan rápido.",
    "¿Con pulpa? ¡Con pico!",
    "Cuidado con la TNT.",
)
WIN_LINES = (
    "Buena bajada.",
    "El pozo paga.",
    "Sale ganando.",
    "Minerales a la saca.",
    "¡Toma mangó!",
    "Eso no lo saca ni la ley de minas.",
    "Hasta el capataz aplaude.",
    "Más rico que el que encontró el oro en Rodalquilar.",
)
LOSS_LINES = (
    "El pico no aguantó.",
    "Poca veta esta vez.",
    "Se quedó en la roca.",
    "Otra vez será.",
    "Estoy destruido, como el pico.",
    "Lo único que se llevó fue polvo.",
    "Ni para el bocadillo de la cuadrilla.",
    "Sofía, cállate, que se rompió.",
)
DRAGON_LINES = (
    "El dragón ha caído.",
    "Fin del juego, literalmente.",
    "Ni Steve con armadura de netherite.",
    "¡Toma mangó, dragón!",
)


@dataclass(frozen=True, slots=True)
class Dig:
    """Una tirada jugada, pagada y apuntada."""

    outcome: Outcome
    settlement: BetSettlement
    play_id: int
    record: int | None
    when: datetime
    #: Récord del servidor con ese pico antes de la tirada (`None` si nadie lo había usado).
    server_record: int | None = None
    #: Días seguidos que lleva el miembro jugando en el pozo, contando hoy.
    streak: int = 1

    @property
    def cost(self) -> int:
        return OFFERS[self.outcome.pick].cost

    @property
    def net(self) -> int:
        return self.outcome.payout - self.cost

    @property
    def balance(self) -> int:
        return self.settlement.balance

    @property
    def new_record(self) -> bool:
        return self.record is None or self.outcome.run.max_row > self.record

    @property
    def server_record_beaten(self) -> bool:
        """Si bate el récord que ya tenía alguien del servidor con ese pico."""
        return self.server_record is not None and self.outcome.run.max_row > self.server_record


def _plural_layer(row: int) -> str:
    return layer_at(min(max(row, 0), DRAGON_ROW - 1)).name


def panel_embed(owner: str, *, balance: int, records: dict[str, int]) -> discord.Embed:
    """El pozo recién abierto o en reposo: los picos, sus precios y tus récords."""
    embed = discord.Embed(
        title="⛏️ El pozo",
        description=(
            "Elige un pico y paga su precio. Cae rebotando y rompiendo bloques: cada mineral "
            "suma premio y los bloques de oro, diamante, netherite, la baliza y el misterioso "
            "(no dice cuánto hasta romperlo) lo multiplican todo. "
            "Cuando se queda sin vida, cobras lo acumulado. En el fondo del End espera el "
            "dragón, que paga el máximo."
        ),
        color=COLOR_IDLE,
    )
    lines = []
    for key, offer in OFFERS.items():
        best = records.get(key)
        record = f" · récord capa {-best}" if best is not None else ""
        lines.append(
            f"{EMOJIS[key]} **{offer.name}** · {format_amount(offer.cost)} · "
            f"dragón ×{offer.top:,}{record}".replace(",", ".")
        )
    embed.add_field(name="Picos", value="\n".join(lines), inline=False)
    embed.set_footer(text=f"Pozo de {owner} · Saldo: {format_amount(balance)}")
    return embed


def digging_embed(owner: str, pick: str, part: int) -> discord.Embed:
    """Mientras cae el pico."""
    offer = OFFERS[pick]
    embed = discord.Embed(
        title=f"⛏️ Picando con {offer.name.lower()}…",
        description=random.choice(DIG_LINES) if part == 0 else "Sigue bajando…",
        color=COLOR_DIGGING,
    )
    embed.set_footer(text=f"Pozo de {owner} · {format_amount(offer.cost)} en juego")
    return embed


def result_text(dig: Dig) -> str:
    """El resultado en el embed: premio, multiplicador, hasta dónde llegó y el récord."""
    outcome, offer = dig.outcome, OFFERS[dig.outcome.pick]
    run = outcome.run
    mult = format_mult(outcome.payout * SCALE // offer.cost)
    if outcome.dragon:
        head = f"🐉 **¡DRAGÓN!** {random.choice(DRAGON_LINES)}"
    elif dig.net > 0:
        head = f"💰 {random.choice(WIN_LINES)}"
    else:
        head = f"🪨 {random.choice(LOSS_LINES)}"
    lines = [
        head,
        f"Premio: **{format_amount(outcome.payout)}** ({mult}) · "
        f"neto {'+' if dig.net >= 0 else ''}{format_amount(dig.net)}",
        f"Llegó a la **capa {-run.max_row}** ({_plural_layer(run.max_row)})"
        + (f" · multiplicador ×{run.multiplier}" if run.multiplier > 1 else ""),
    ]
    if dig.new_record:
        lines.append(f"🏅 Nuevo récord con {offer.name.lower()}: capa {-run.max_row}")
    elif dig.record is not None:
        lines.append(f"Tu récord con {offer.name.lower()}: capa {-dig.record}")
    return "\n".join(lines)


def result_embed(owner: str, dig: Dig, text: str) -> discord.Embed:
    outcome = dig.outcome
    color = COLOR_DRAGON if outcome.dragon else COLOR_WIN if dig.net > 0 else COLOR_LOSS
    embed = discord.Embed(
        title=f"{EMOJIS[outcome.pick]} Pico de {OFFERS[outcome.pick].name.lower()}",
        description=text,
        color=color,
    )
    embed.set_footer(
        text=f"Pozo de {owner} · Saldo: {format_amount(dig.balance)} · Tirada #{dig.play_id}"
    )
    return embed


def prizes_embed(cal: Calibration) -> discord.Embed:
    """📋 Premios: precio, tope y lo hondo que llega cada pico, de la calibración."""
    embed = discord.Embed(
        title="📋 Premios del pozo",
        description=(
            "Todos los picos devuelven de media el 99,5 % de lo que cuestan: la cuenta es "
            "exacta sobre una biblioteca de semillas. Lo que cambia es el riesgo. El premio "
            "es la suma de los minerales por los multiplicadores, con el tope del dragón."
        ),
        color=COLOR_IDLE,
    )
    for key, offer in OFFERS.items():
        stats = cal.picks[key].stats
        dragon = stats.get("dragon", 0)
        dragon_text = f"1 de cada {round(1 / dragon):,}".replace(",", ".") if dragon else "nunca"
        embed.add_field(
            name=f"{EMOJIS[key]} {offer.name} · {format_amount(offer.cost)}",
            value=(
                f"Tope ×{offer.top:,} · gana el {stats.get('win', 0):.0%}\n".replace(",", ".")
                + f"Nether {stats.get('nether', 0):.1%} · End {stats.get('end', 0):.2%}\n"
                + f"Dragón: {dragon_text}"
            ),
        )
    return embed


class PicarView(ui.View):
    """El pozo de un jugador: un botón por pico, Premios y Récords."""

    def __init__(self, cog: Picar, *, guild_id: int, owner: discord.abc.User) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        self.message: discord.Message | None = None
        self.busy = False
        for i, (key, offer) in enumerate(OFFERS.items()):
            button: ui.Button[PicarView] = ui.Button(
                label=f"{offer.name} · {offer.cost:,}".replace(",", "."),
                emoji=EMOJIS[key],
                style=discord.ButtonStyle.success
                if key in ("diamond", "netherite")
                else discord.ButtonStyle.primary,
                custom_id=f"picar:{key}",
                row=i // 3,
            )
            button.callback = self._dig_callback(key)
            self.add_item(button)
        prizes: ui.Button[PicarView] = ui.Button(
            label="Premios", emoji="📋", custom_id="picar:prizes", row=2
        )
        prizes.callback = self._prizes
        self.add_item(prizes)
        records: ui.Button[PicarView] = ui.Button(
            label="Récords", emoji="🏆", custom_id="picar:records", row=2
        )
        records.callback = self._records
        self.add_item(records)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message(
                f"Este pozo es de {self.owner.display_name}. Abre el tuyo con `/picar`.",
                ephemeral=True,
            )
            return False
        return True

    def _set_enabled(self, enabled: bool) -> None:
        for item in self.children:
            if isinstance(item, ui.Button):
                item.disabled = not enabled

    async def on_timeout(self) -> None:
        self._set_enabled(False)
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    def _dig_callback(self, key: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await self._dig(interaction, key)

        return callback

    async def _dig(self, interaction: discord.Interaction, key: str) -> None:
        """Respuesta a un pico: cobra, juega, apunta, enseña y paga."""
        if self.busy:
            # Doble clic mientras cae el pico: se ignora sin mostrar error.
            await ack(interaction)
            return
        self.busy = True
        dig: Dig | None = None
        try:
            # Jugar, cobrar y apuntar tardan: se acepta el clic antes.
            await ack(interaction)
            if self.cog.disabled_reason is not None:
                await notify(interaction, "El pozo está cerrado por mantenimiento.")
                return
            try:
                dig = await self.cog.dig(self.guild_id, self.owner.id, key)
            except InsufficientFundsError as error:
                await notify(interaction, insufficient_text(error.balance, OFFERS[key].cost))
                return
            except BalanceLimitError:
                await notify(interaction, "La banca no puede pagar tanto ahora mismo.")
                return
            self._set_enabled(False)
            await self.cog.show(
                dig,
                owner=self.owner,
                edit=lambda **kwargs: edit(interaction, **kwargs),
                view=self,
                limit=_upload_limit(interaction.guild),
            )
        finally:
            self.busy = False
        if dig is None:
            return
        await renta.remind(self.cog.bot, interaction)
        channel = getattr(self.message, "channel", None) or interaction.channel
        await self.cog.track(dig, self.owner, channel, guild_id=self.guild_id)
        await self.cog.shout(dig, self.owner, channel)

    async def _prizes(self, interaction: discord.Interaction) -> None:
        """📋 Premios: solo lee la calibración en memoria."""
        if self.cog.calibration is None:
            await interaction.response.send_message(
                "El pozo está cerrado por mantenimiento.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=prizes_embed(self.cog.calibration), ephemeral=True
        )

    async def _records(self, interaction: discord.Interaction) -> None:
        """🏆 Récords: los tuyos y el ranking del servidor (lee la base de datos)."""
        await ack(interaction, new_message=True)
        embed = await self.cog.records_embed(interaction.guild, self.owner)
        await edit(interaction, embed=embed)


def _upload_limit(guild: discord.Guild | None) -> int:
    """Bytes que puede pesar cada GIF: una parte del límite de subida del servidor."""
    limit = getattr(guild, "filesize_limit", None)
    if not isinstance(limit, int) or limit <= 0:
        limit = 10 * 1024 * 1024
    return int(limit * UPLOAD_SHARE)


EditFn = Callable[..., Awaitable[Any]]


class Picar(commands.Cog, name="Pico"):
    """El pozo del casino: un pico que cae rompiendo bloques."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        repository: PicarRepository,
        scene: PicarScene | None = None,
        calibration: Calibration | None = None,
        casino_channel_ids: frozenset[int] = frozenset(),
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.repository = repository
        self.scene = scene or PicarScene()
        self.casino_channel_ids = casino_channel_ids
        self.calibration = calibration
        #: Por qué el pozo no abre (calibración que no vale); `None` si abre.
        self.disabled_reason: str | None = "comprobando la calibración"
        self._check_task: asyncio.Task[None] | None = None

    async def cog_load(self) -> None:
        """Comprueba la calibración en segundo plano (~1 s de CPU, en un hilo)."""
        self._check_task = asyncio.create_task(self._check(), name="picar-calibracion")

    async def _check(self) -> None:
        try:
            if self.calibration is None:
                self.calibration = await asyncio.to_thread(Calibration.load)
            reason = await asyncio.to_thread(check, self.calibration)
        except Exception as error:  # el pozo no abre, pero el bot sigue
            logger.exception("No se pudo leer la calibración del pico")
            reason = f"no se pudo leer la calibración ({error})"
        self.disabled_reason = reason
        if reason is not None:
            logger.warning("El pozo (picar) queda cerrado: %s", reason)

    async def cog_unload(self) -> None:
        if self._check_task is not None:
            self._check_task.cancel()
        await self.scene.close()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        await self.repository.delete_guild_data(guild.id)

    # -- Una tirada -----------------------------------------------------------------------

    async def dig(self, guild_id: int, user_id: int, pick: str) -> Dig:
        """Juega una tirada, mueve el dinero y la apunta (antes de dibujar nada).

        Fiscalmente es juego, como la ruleta o el pachinko: `settle_bet` cobra el precio
        del pico, paga el premio y ajusta la retención diaria del IRPF, porque la ganancia
        es una ganancia patrimonial de la base general (art. 33.1 LIRPF) y las pérdidas
        del mismo día compensan (art. 33.5.d LIRPF). Lo retenido va a la cuenta del Estado
        en la misma transacción.

        Raises:
            InsufficientFundsError: Si el saldo no cubre el precio.
            BalanceLimitError: Si el premio superaría el saldo máximo.
        """
        assert self.calibration is not None
        cal = self.calibration
        outcome = await asyncio.to_thread(play, pick, cal)
        settlement = await self.economy.settle_bet(
            guild_id, user_id, game=GAME, stake=OFFERS[pick].cost, payout=outcome.payout
        )
        run = outcome.run
        when = datetime.now(TIMEZONE)
        saved = await self.repository.record(
            guild_id,
            user_id,
            pick=pick,
            seed=outcome.seed,
            params=cal.params,
            cost=OFFERS[pick].cost,
            payout=outcome.payout,
            units=outcome.units,
            multiplier=run.multiplier,
            max_row=run.max_row,
            dragon=run.dragon,
            day=when.date(),
        )
        return Dig(
            outcome, settlement, saved.id, saved.best, when,
            server_record=saved.server_best, streak=saved.streak,
        )  # fmt: skip

    async def play_parts(
        self, outcome: Outcome, *, edit_fn: EditFn, embed_for: Callable[[int], discord.Embed],
        view: ui.View | None, limit: int,
    ) -> None:  # fmt: skip
        """Enseña los GIF de una tirada uno detrás de otro en el mismo mensaje.

        Mientras se ve uno se pinta el siguiente. Si el anterior acaba en un corte, el
        siguiente se empieza a subir antes de que llegue a esa pausa, lo que se espera que
        tarde la subida (`upload_ms`), para que llegue justo cuando el anterior se para:
        esperar a la pausa dejaba al pico quieto lo que tardaba en subirse (unos 3 s con un
        GIF de 6 MB). Si acaba en el rótulo de una dimensión, se sube al empezar el rótulo,
        que así se ve. Vuelve cuando el último ha llegado a su pausa.
        """
        assert self.calibration is not None
        values = self.calibration.picks[outcome.pick].values
        parts = self.scene.parts(outcome, values, budget=limit)
        try:
            pending: asyncio.Task[Part] | None = asyncio.ensure_future(anext(parts))
            # El último GIF puesto y cuándo dejó Discord de subirlo (empieza a verse).
            previous: tuple[Part, float] | None = None
            index = 0
            while pending is not None:
                try:
                    part = await pending
                except StopAsyncIteration:
                    return
                if previous is not None:
                    before, on_screen = previous
                    moving = before.ms - before.hold_ms
                    if before.cut:
                        moving -= upload_ms(len(part.gif))
                    start = on_screen + moving / 1000 + REVEAL_MARGIN_SECONDS
                    await asyncio.sleep(max(0.0, start - time.monotonic()))
                await edit_fn(
                    embed=embed_for(index),
                    attachments=[discord.File(io.BytesIO(part.gif), filename=GIF_NAME)],
                    view=view,
                )
                previous = (part, time.monotonic())
                pending = None if part.last else asyncio.ensure_future(anext(parts))
                index += 1
            if previous is not None:
                # Lo que sigue (la imagen final) también tarda en subirse.
                last, on_screen = previous
                moving = last.ms - last.hold_ms - UPLOAD_FIXED_MS
                start = on_screen + moving / 1000 + REVEAL_MARGIN_SECONDS
                await asyncio.sleep(max(0.0, start - time.monotonic()))
        finally:
            await parts.aclose()

    async def show(
        self, dig: Dig, *, owner: discord.abc.User, edit: EditFn, view: PicarView, limit: int
    ) -> None:
        """Enseña la tirada (los GIF y luego el PNG con el resultado) y devuelve los botones.

        El dinero ya está cobrado y pagado: si Discord falla al editar, el saldo es
        correcto.
        """
        outcome = dig.outcome
        name = owner.display_name
        try:
            await self.play_parts(
                outcome,
                edit_fn=edit,
                embed_for=lambda part: digging_embed(name, outcome.pick, part),
                view=view,
                limit=limit,
            )
        except discord.HTTPException:
            logger.warning("No se pudo enseñar la animación del pico", exc_info=True)
        assert self.calibration is not None
        png = await self.scene.still(outcome, self.calibration.picks[outcome.pick].values)
        text = result_text(dig)
        if tax := gambling_tax_line(dig.settlement):
            text += f"\n{tax}"
        if hint := await renta.hint(
            self.bot,
            view.guild_id,
            owner.id,
            bet_moment(stake=dig.cost, net=dig.net, balance_after=dig.balance),
        ):
            text += f"\n{hint}"
        view._set_enabled(True)
        await edit(
            embed=result_embed(name, dig, text),
            attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
            view=view,
        )

    async def track(
        self, dig: Dig, user: discord.abc.User, channel: object, *, guild_id: int
    ) -> None:
        """Logros y estadísticas de la tirada, después de enseñarla."""
        outcome = dig.outcome
        delta = picar_stats(
            outcome.run,
            pick=outcome.pick,
            cost=dig.cost,
            payout=outcome.payout,
            new_record=dig.new_record,
            when=dig.when,
            capped=outcome.capped,
            server_record=dig.server_record_beaten,
            streak=dig.streak,
            tax=dig.settlement.tax_delta,
        )
        delta.merge(
            casino_stats(
                stake=dig.cost,
                net=dig.net,
                balance_after=dig.balance,
                tax_delta=dig.settlement.tax_delta,
            )
        )
        await logros.casino_play(self.bot, guild_id, user, channel, delta, net=dig.net)
        await apuestas.record(
            self.bot,
            guild_id,
            user,
            game=GAME,
            stake=dig.cost,
            net=dig.net,
            balance_after=dig.balance,
            tax=dig.settlement.tax_delta,
        )
        if outcome.dragon:
            aspecto.dragon_killed(self.bot, guild_id)

    async def shout(self, dig: Dig, user: discord.abc.User, channel: object) -> None:
        """Anuncia en el canal el dragón, el End y los premios enormes."""
        if not isinstance(channel, discord.abc.Messageable):
            return
        outcome, offer = dig.outcome, OFFERS[dig.outcome.pick]
        if outcome.dragon:
            text = (
                f"📣 🐉 ¡{user.mention} ha matado al **dragón** con un pico de "
                f"{offer.name.lower()}! Se lleva **{format_amount(outcome.payout)}**. "
                f"{TAX_COLLECTOR} ya está bajando por el pozo."
            )
        elif outcome.payout >= SHOUT_MULTIPLIER * offer.cost:
            text = (
                f"📣 ⛏️ ¡{user.mention} ha sacado **{format_amount(outcome.payout)}** del pozo "
                f"con un pico de {offer.name.lower()}!"
            )
        else:
            return
        try:
            await channel.send(
                text, allowed_mentions=discord.AllowedMentions(users=[user], everyone=False)
            )
        except discord.HTTPException:
            logger.debug("No se pudo anunciar un premio del pico", exc_info=True)

    async def records_embed(
        self, guild: discord.Guild | None, owner: discord.abc.User
    ) -> discord.Embed:
        """🏆 Récords: los del dueño del pozo y el top 3 del servidor por pico."""
        embed = discord.Embed(title="🏆 Récords del pozo", color=COLOR_IDLE)
        if guild is None:
            return embed
        params = self.calibration.params if self.calibration is not None else ""
        mine = await self.repository.records(guild.id, owner.id, params)
        lines = []
        for key, offer in OFFERS.items():
            best = mine.get(key)
            lines.append(
                f"{EMOJIS[key]} {offer.name}: "
                + (f"capa {-best} ({_plural_layer(best)})" if best is not None else "sin bajar")
            )
        embed.add_field(name=f"Los de {owner.display_name}", value="\n".join(lines), inline=False)
        for key, offer in OFFERS.items():
            top = await self.repository.ranking(guild.id, key, params, limit=3)
            if not top:
                continue
            value = "\n".join(
                f"{i}. <@{user_id}> · capa {-row}" for i, (user_id, row) in enumerate(top, 1)
            )
            embed.add_field(name=f"{EMOJIS[key]} {offer.name}", value=value)
        return embed

    async def replay(self, responder: CommandResponder, play_id: int) -> None:
        """Reproduce una tirada guardada para auditarla (sin mover dinero).

        Vuelve a jugar su semilla con el motor actual. Si la tirada se jugó con otra
        calibración (otro hash), no se puede reproducir igual y se dice.
        """
        guild = responder.guild
        if guild is None:
            await responder.send_error("Solo dentro de un servidor.")
            return
        record = await self.repository.get(guild.id, play_id)
        if record is None:
            await responder.send_error(f"No hay ninguna tirada #{play_id} en este servidor.")
            return
        cal = self.calibration
        if cal is None or self.disabled_reason is not None:
            await responder.send_error("El pozo está cerrado: no hay calibración que valga.")
            return
        if record.params != cal.params:
            await responder.send_error(
                f"La tirada #{play_id} se jugó con otra calibración ({record.params[:12]}…): "
                "este motor no la reproduce igual."
            )
            return
        channel = responder.channel
        if channel is None:
            await responder.send_error("No encuentro el canal para enseñar la tirada.")
            return
        outcome = await asyncio.to_thread(play, record.pick, cal, record.seed)
        same = (outcome.payout, outcome.run.max_row) == (record.payout, record.max_row)
        await responder.send(f"🔁 Reproduciendo la tirada #{play_id}.", ephemeral=True)
        message = await channel.send(embed=replay_embed(record, outcome, same=same))
        await self.play_parts(
            outcome,
            edit_fn=message.edit,
            embed_for=lambda _: replay_embed(record, outcome, same=same),
            view=None,
            limit=_upload_limit(guild),
        )
        png = await self.scene.still(outcome, cal.picks[outcome.pick].values)
        await message.edit(
            embed=replay_embed(record, outcome, same=same),
            attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
        )

    # -- picar ----------------------------------------------------------------------------

    async def _picar_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> None:
        """Lógica compartida entre `/picar` y `.picar`: abre el pozo."""
        if guild is None:
            await send_error("El pozo solo se abre dentro de un servidor.")
            return
        if error := casino_channel_error(self.casino_channel_ids, channel, "El pozo"):
            await send_error(error)
            return
        if self.disabled_reason is not None:
            await send_error("El pozo está cerrado por mantenimiento.")
            return
        balance = await self.economy.balance(guild.id, user.id)
        assert self.calibration is not None  # sin calibración, `disabled_reason` lo para
        records = await self.repository.records(guild.id, user.id, self.calibration.params)
        view = PicarView(self, guild_id=guild.id, owner=user)
        view.message = await send(
            embed=panel_embed(user.display_name, balance=balance, records=records), view=view
        )

    @app_commands.command(name="picar", description="Un pico que cae rompiendo bloques.")
    @app_commands.guild_only()
    async def picar(self, interaction: discord.Interaction) -> None:
        """Abre un pozo con un botón por pico.

        Solo en los canales de `CASINO_CHANNEL_IDS` si está configurado. Las tiradas
        mueven yapdollars a través de la economía del bot.
        """

        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            return await interaction.original_response()

        await self._picar_impl(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            send=send,
            send_error=InteractionResponder(interaction).send_error,
        )

    @commands.command(name="picar")
    @commands.guild_only()
    async def picar_text(self, ctx: commands.Context) -> None:
        """Versión de texto: `.picar`."""

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        await self._picar_impl(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            send=send,
            send_error=ContextResponder(ctx).send_error,
        )


def replay_embed(record: PlayRecord, outcome: Outcome, *, same: bool) -> discord.Embed:
    """Ficha de una tirada reproducida: lo guardado y lo que sale ahora."""
    offer = OFFERS[record.pick]
    check_line = (
        "✅ Sale igual que se guardó."
        if same
        else "⚠️ No sale igual que se guardó: revisa la calibración."
    )
    return discord.Embed(
        title=f"🔁 Tirada #{record.id}",
        description=(
            f"<@{record.user_id}> · pico de {offer.name.lower()} · semilla {record.seed}\n"
            f"Guardado: {format_amount(record.payout)}, capa {-record.max_row}\n"
            f"Ahora: {format_amount(outcome.payout)}, capa {-outcome.run.max_row}\n"
            f"{check_line}"
        ),
        color=COLOR_IDLE if same else COLOR_LOSS,
    )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía y el repositorio compartidos del bot."""
    await bot.add_cog(
        Picar(
            bot,
            economy=bot.economy,
            repository=bot.picar,
            casino_channel_ids=bot.casino_channel_ids,
        )
    )
