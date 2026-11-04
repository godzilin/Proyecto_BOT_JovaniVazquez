"""Guerra (Casino War) con botones: comando `guerra`.

Sigue las mismas normas que el blackjack (`bot.cogs.blackjack`): mesa individual
que solo pulsa su dueño, dinero siempre a través de `EconomyService`, mismo
control de canales (`CASINO_CHANNEL_IDS`) y fichas con ½ / ×2 / All-in.
`.guerra 500` reparte al momento. Las reglas están en `bot.services.war`: una
carta para ti y otra para la banca, gana la más alta y, si empatan, eliges entre
🏳️ rendirte (media apuesta de vuelta) y ⚔️ ir a la guerra (otra apuesta igual). El
botón 🤝 Empate 10:1 suma una apuesta lateral igual a la ficha, que se cobra al
repartir.

El dinero en juego se cobra al repartir (y al ir a la guerra) y el premio se paga
al terminar la mano: `place_bet` + `pay_winnings`, así que tributa como juego
(retención diaria del 19 % y declaración de la renta de los lunes, igual que el
blackjack). Si la mesa caduca o el bot se apaga con un empate sin decidir, la mano
se resuelve rindiéndose (es lo que menos pierde) para que nunca se quede dinero
colgado. Si el proceso muere de golpe a mitad de un empate, lo apostado en esa
mano se pierde (queda registrado en el libro de movimientos).

La mesa se dibuja con canvas (`bot.services.war_scene`, escena `assets/guerra/escena.html`):
tapete con skins (botón 🎨), cartas grandes que salen del zapato y se voltean, la ficha y
la lateral, y un GIF por jugada (repartir, ir a la guerra, rendirse) de ~2 s cuyo último
fotograma se queda como PNG. Las cartas de la próxima mano se sortean al acabar la
anterior, sin enseñarlas, y mientras se ve el GIF de ahora (y mientras se decide un
empate) la mesa pinta en segundo plano el GIF de cada botón posible (`WarTable.prepare`):
al pulsar solo queda subirlo. La skin y el zapato entran en la clave del plan.

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
from bot.services.achievements import casino_stats, war_stats
from bot.services.bacara_render import Media
from bot.services.blackjack import Card, new_shoe
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
from bot.services.war import BURN_CARDS, TIE_PAYS, WAR_TIE_BONUS, Result, WarGame, main_net
from bot.services.war_scene import WarScene
from bot.utils.ahead import Ahead
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

GAME = "guerra"
DEFAULT_STAKE = 100
TABLE_TIMEOUT = 180
PNG_NAME = "guerra.png"
GIF_NAME = "guerra.gif"
#: Lo que se deja pasar tras el GIF antes de enseñar el resultado: lo que tarda el cliente
#: en descargarlo (si se queda corto, el resultado sale antes de acabar la animación).
REVEAL_MARGIN_SECONDS = 0.5

COLOR_PLAYING = discord.Color.from_rgb(21, 83, 64)
COLOR_WAR = discord.Color.from_rgb(150, 40, 35)
COLOR_WIN = discord.Color.from_rgb(255, 196, 0)
COLOR_LOSS = discord.Color.from_rgb(80, 84, 92)
COLOR_PUSH = discord.Color.from_rgb(120, 140, 160)

#: Lo que dice la mesa mientras suena cada GIF (sin resultado ni saldo).
DEAL_BUSY = "## 🃏 Repartiendo…\nLa tuya primero."
WAR_BUSY = f"## ⚔️ ¡A la guerra!\nLa banca quema {BURN_CARDS} cartas y reparte otras dos."
SURRENDER_BUSY = "## 🏳️ Bandera blanca\nTe rindes: media ficha de vuelta."

RULES_FOOTER = f"Gana la carta más alta · empate: rendirse o guerra · lateral {TIE_PAYS}:1"

#: Al ganar a la primera.
WIN_LINES = (
    "¡Tu carta manda!",
    "Carta más alta, bolsillo más lleno.",
    "La banca baja la cabeza.",
    "Ganas por un rango.",
    "¡Toma mangó!",
    "Victoria por goleada de rango.",
    "La banca pide un recuento. Cobras igual.",
    "Ganas, y sin pactar con nadie.",
    "Tu carta gobierna en solitario.",
    "¿Con pulpa? ¡Sin pulpa! Ganas tú.",
    "La banca se queda mirando su carta, a ver si crece.",
    "Ganas la mano y el respeto de la mesa.",
)
#: Al perder a la primera.
LOSS_LINES = (
    "La banca tiene la carta más alta.",
    "Tu carta se queda corta.",
    "Perdiste por una carta. Casi nada, o sea, todo.",
    "Menos mal que no era a la guerra.",
    "La banca saca más que tú. Y no hay recurso.",
    "Estoy destruido, pero por fuera… por dentro estoy nuevo.",
    "La banca cobra y tú lo llamas aprendizaje.",
    "Hoy la baraja no está de tu parte.",
    "Sofía, cállate, que pierdo la concentración.",
    "Perdiste. Pídele un recuento al Tribunal Supremo.",
    "La guerra es así: unas veces se gana y otras se pierde.",
    "La banca te ha pasado por encima.",
)
#: Al aparecer el empate, mientras eliges.
TIE_LINES = (
    "Empate. Nadie parpadea.",
    "Mismo rango, distinto palo, igual problema.",
    "Empate. ¿Pacto o guerra?",
    "Empate a todo, menos a nervios.",
    "Dos cartas iguales y un dilema.",
    "Empate. La diplomacia no paga, pero tampoco quema.",
    "Tablas. Toca decidir.",
    "Empate. Esto huele a guerra.",
)
#: Al rendirse.
SURRENDER_LINES = (
    "Bandera blanca, media ficha y a casa.",
    "Te rindes y recuperas la mitad. Dignidad, la justa.",
    "Retirada a tiempo.",
    "Te rindes. Gila ya había llamado al enemigo para avisar.",
    "Rendición con honores y con media ficha.",
    "Media ficha de vuelta. Puigdemont ha huido por menos.",
    "Mejor media ficha que ninguna.",
    "Haz el amor y no la guerra.",
)
#: Al ir a la guerra, mientras la banca quema las cartas.
WAR_LINES = (
    f"La banca quema {BURN_CARDS} cartas y reparte otras dos.",
    f"{BURN_CARDS} cartas al fuego, como los discos duros de Bárcenas.",
    "¡A la guerra!",
    "Se declara la guerra. Que Dios reparta suerte.",
    f"{BURN_CARDS} cartas quemadas y cero explicaciones.",
    "Se quema el mazo y se decide el honor.",
    "Esto ya es personal.",
)
#: Al ganar la guerra.
WAR_WIN_LINES = (
    "¡Ganas la guerra!",
    "Guerra ganada, y te devuelven la ficha original.",
    "Victoria sin bajas.",
    "La guerra fría se calienta a tu favor.",
    "¡Veni, vidi, vici!",
    "Ganas la guerra, y la banca firma el armisticio.",
    "¡Toma mangó, y con medalla!",
)
#: Al perder la guerra.
WAR_LOSE_LINES = (
    "Guerra perdida: las dos fichas, a la banca.",
    "Dos fichas menos y mil excusas más.",
    "La banca arrasa.",
    "Guerra perdida. Si lo cuenta el NO-DO, es una victoria heroica.",
    "Perdiste la batalla y la guerra.",
    "Estoy destruido, pero por fuera… por dentro estoy nuevo.",
    "Retirada sin honores.",
)
#: Si la guerra vuelve a empatar.
WAR_TIE_LINES = (
    "¡Doble empate! La guerra paga premio.",
    "Empate en la guerra: cobras la guerra, la original y el premio.",
    "Guerra fría con premio: no gana nadie, pero cobras tú.",
    "Segundo empate y la banca te da un premio extra sin saber por qué.",
    "Tablas en la guerra, y tú con el botín.",
    "¡Toma mangó, doble empate!",
)
#: Acierto y fallo de la lateral de empate.
SIDE_HIT_LINES = (
    "¡La lateral acierta!",
    "Apostaste al empate y el empate vino.",
    "La lateral lo vio venir.",
    "El empate paga diez a uno.",
)
SIDE_MISS_LINES = (
    "Sin empate, la lateral se queda en la mesa.",
    "El empate no vino. La banca da las gracias.",
    "Lateral perdida.",
)

EditFn = Callable[..., Awaitable[Any]]


# -- Presentación -------------------------------------------------------------------


def side_line(game: WarGame, rng: random.Random) -> str:
    """Línea con lo que hizo la lateral de empate, si se jugó."""
    if not game.side:
        return ""
    if game.tied:
        return (
            f"\n🤝 {rng.choice(SIDE_HIT_LINES)} Empate {TIE_PAYS}:1 "
            f"+{format_amount(game.side * TIE_PAYS)}."
        )
    return f"\n🤝 {rng.choice(SIDE_MISS_LINES)} -{format_amount(game.side)}."


def result_headline(game: WarGame, rng: random.Random | None = None) -> str:
    """Titular grande con lo ganado o perdido en la mano principal, y la lateral debajo."""
    rng = rng or random.Random()
    net = main_net(game)
    stake = game.stake
    match game.result:
        case Result.WIN:
            text = f"# {rng.choice(WIN_LINES)} +{format_amount(net)}"
        case Result.LOSE:
            text = f"# -{format_amount(stake)}\n### {rng.choice(LOSS_LINES)}"
        case Result.SURRENDER:
            text = (
                f"# 🏳️ Te rindes\n### {rng.choice(SURRENDER_LINES)}\n"
                f"Recuperas {format_amount(game.main_return)}."
            )
        case Result.WAR_WIN:
            text = f"# ⚔️ {rng.choice(WAR_WIN_LINES)} +{format_amount(net)}"
        case Result.WAR_TIE:
            text = (
                f"# ⚔️ {rng.choice(WAR_TIE_LINES)} +{format_amount(net)}\n"
                f"Premio extra de {format_amount(stake * WAR_TIE_BONUS)} incluido."
            )
        case _:
            text = f"# -{format_amount(-net)}\n### {rng.choice(WAR_LOSE_LINES)}"
    text += side_line(game, rng)
    if game.side:
        sign = "+" if game.net > 0 else "-" if game.net < 0 else ""
        text += f"\n**Total de la mano: {sign}{format_amount(abs(game.net))}**"
    return text


def table_embed(
    *,
    owner: str,
    game: WarGame | None,
    balance: int,
    stake: int,
    side_on: bool,
    streak: int,
    paid: bool,
    headline: str | None = None,
    busy: str | None = None,
    image: str = PNG_NAME,
    skin_label: str | None = None,
    rng: random.Random | None = None,
) -> discord.Embed:
    """Embed de la mesa. `headline` es el resultado de la última mano, si acabó.

    Args:
        paid: Si la mano ya se pagó (la mesa está lista para otra).
        busy: Texto mientras suena el GIF (repartiendo, a la guerra…): no enseña saldo ni
            resultado, que se verían antes que las cartas.
        image: Nombre del archivo que lleva la imagen del mensaje (el GIF o el PNG).
        skin_label: El tapete que lleva el dueño, si no es el de serie.
    """
    rng = rng or random.Random()
    if busy is not None:
        description = busy
        color = COLOR_WAR if "guerra" in busy.lower() else COLOR_PLAYING
    elif game is None:
        description = "Pulsa 🃏 **Repartir**. El as manda y los palos no cuentan."
        color = COLOR_PLAYING
    elif game.awaiting_choice:
        cost = format_amount(game.extra_stake)
        description = (
            f"## 🤝 ¡Empate a {game.player[0].label}!\n{rng.choice(TIE_LINES)}\n"
            f"🏳️ **Rendirte**: recuperas {format_amount(game.stake // 2)}.\n"
            f"⚔️ **A la guerra** (otra ficha de {cost}): si tu carta iguala o supera a la de "
            f"la banca, cobras {cost} y se te devuelve la original; si no, caen las dos. "
            f"Si empatas otra vez, premio de {format_amount(game.stake * WAR_TIE_BONUS)} más."
        )
        color = COLOR_WAR
    else:
        description = headline or result_headline(game, rng)
        color = COLOR_WIN if game.net > 0 else COLOR_PUSH if game.net == 0 else COLOR_LOSS
    if busy is None and balance == 0 and (game is None or paid):
        description += "\n\n**Estás a cero.** `imv` te recarga."
    embed = discord.Embed(title="⚔️ Guerra", description=description, color=color)
    playing = game is not None and not paid
    if busy is None:
        embed.add_field(name="Saldo", value=format_amount(balance))
    embed.add_field(
        name="En juego" if playing else "Ficha",
        value=format_amount(game.total_stake if playing else stake),  # type: ignore[union-attr]
    )
    if busy is None:
        embed.add_field(name="Racha", value=f"🔥 {streak}" if streak >= 2 else "—")
        embed.add_field(
            name=f"Empate {TIE_PAYS}:1",
            value=f"✅ {format_amount(stake)}" if side_on else "—",
        )
    embed.set_image(url=f"attachment://{image}")
    footer = f"Mesa de {owner} · {RULES_FOOTER}"
    if skin_label:
        footer += f" · Tapete: {skin_label}"
    embed.set_footer(text=footer)
    return embed


# -- Mesa ---------------------------------------------------------------------------


class WarTable(discord.ui.View):
    """Mesa de la Guerra de un jugador: un mensaje con botones.

    Guarda la mano en curso, la ficha, la lateral y las rachas. Solo su dueño puede
    pulsarla.
    """

    def __init__(self, cog: War, *, guild_id: int, owner: discord.abc.User, stake: int) -> None:
        super().__init__(timeout=TABLE_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        self.stake = stake
        #: Si se repartirá con la apuesta lateral de empate.
        self.side_on = False
        self.game: WarGame | None = None
        self.streak = 0
        #: Rachas de la mesa para los logros: manos ganadas, perdidas y empatadas seguidas.
        self.win_run = 0
        self.lose_run = 0
        self.tie_run = 0
        self.headline: str | None = None
        self.message: discord.Message | None = None
        self.started_at = 0
        self._last_interaction: discord.Interaction | None = None
        self._busy = False
        #: Si la mano en curso ya se pagó (un solo pago por mano, también al apagar).
        self._paid = True
        #: Mano ya pagada que falta contar para los logros, con su saldo e IRPF.
        self._to_track: tuple[WarGame, int, int] | None = None
        #: Zapato de la próxima mano, sorteado al acabar la anterior (sin enseñarlo): así se
        #: pinta antes del clic. Solo con `cog.ahead`; si no, se baraja al repartir.
        self._next_shoe: list[Card] | None = None
        #: GIF pintados por adelantado: repartir (con y sin lateral) o, en un empate, los de
        #: ir a la guerra y rendirse (`prepare`).
        self.ahead = Ahead(cog.ahead, name="la guerra")
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

    def _build_buttons(self) -> None:
        gray, green = discord.ButtonStyle.secondary, discord.ButtonStyle.success
        red = discord.ButtonStyle.danger
        self.surrender_button = self._add(
            "🏳️ Rendirse", 0, self._surrender, style=gray, custom_id="surrender"
        )
        self.war_button = self._add("⚔️ A la guerra", 0, self._war, style=red, custom_id="war")
        self.deal_button = self._add(
            "🃏 Repartir", 1, self._deal_again, style=green, custom_id="deal"
        )
        self.stake_buttons = [
            self._add("½", 1, self._halve, custom_id="half"),
            self._add("×2", 1, self._double_stake, custom_id="x2"),
            self._add("💰 All-in", 1, self._all_in, custom_id="allin"),
        ]
        self.side_button = self._add(
            f"🤝 Empate {TIE_PAYS}:1", 2, self._toggle_side, style=gray, custom_id="side"
        )
        # El 🎨: elegir o comprar el tapete de esta mesa.
        self.paint_button = tienda.skin_button(self.cog.bot, GAME, row=2)
        if self.paint_button is not None:
            self.add_item(self.paint_button)
        self._update_buttons()

    def _update_buttons(self, *, locked: bool = False) -> None:
        """Activa rendirse y la guerra solo con un empate por decidir; el resto, entre manos."""
        deciding = self.game is not None and self.game.awaiting_choice
        for button in (self.surrender_button, self.war_button):
            button.disabled = locked or not deciding
        for button in (self.deal_button, *self.stake_buttons, self.side_button):
            button.disabled = locked or deciding
        if self.paint_button is not None:
            self.paint_button.disabled = locked
        self.side_button.style = (
            discord.ButtonStyle.success if self.side_on else discord.ButtonStyle.secondary
        )
        self.side_button.label = f"🤝 Empate {TIE_PAYS}:1" + (" ✅" if self.side_on else "")

    # -- Ciclo de vida --------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño juega en su mesa."""
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message(
            f"Esta mesa es de {self.owner.display_name}. Abre la tuya con `guerra`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Cierra la mesa; si quedaba un empate sin decidir, se rinde y se paga."""
        self.cog.tables.discard(self)
        self.ahead.drop()  # lo que quede por pintar ya no lo verá nadie
        # Una mano ya pagada se ve tal cual: solo hay que apagar los botones, sin
        # volver a subir la mesa (~1 s).
        unsettled = self.game is not None and not self._paid
        if unsettled:
            await self.force_settle()
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        try:
            embed = await self.current_embed()
            kwargs: dict[str, Any] = {"embed": embed, "view": self}
            if self.game is not None and unsettled:
                png = await self.cog.renderer.still(self.game, skin=self.skin())
                kwargs["attachments"] = [discord.File(io.BytesIO(png), filename=PNG_NAME)]
            if self._last_interaction is not None:
                await self._last_interaction.edit_original_response(**kwargs)
            elif self.message is not None:
                await self.message.edit(**kwargs)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar la mesa de la guerra", exc_info=True)

    async def force_settle(self) -> None:
        """Termina la mano sin animación (caducidad o apagado) y paga lo que toque."""
        game = self.game
        if game is None or self._paid:
            return
        if game.awaiting_choice:
            game.surrender()
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
        self, balance: int | None = None, *, busy: str | None = None, image: str = PNG_NAME
    ) -> discord.Embed:
        """Embed con el estado actual de la mesa."""
        if balance is None and busy is None:
            balance = await self.balance()
        worn = tienda.skin_of(self.cog.bot, self.guild_id, self.owner.id, GAME)
        return table_embed(
            owner=self.owner.display_name,
            game=self.game,
            balance=balance or 0,
            stake=self.stake,
            side_on=self.side_on,
            streak=self.streak,
            paid=self._paid,
            headline=self.headline,
            busy=busy,
            image=image,
            skin_label=worn.label if worn else None,
        )

    # -- GIF por adelantado -----------------------------------------------------------

    def _key(self) -> tuple[Any, ...]:
        """Lo que decide los GIF que se pintan: la mano y el tapete.

        La ficha entra al repartir (sale en la imagen); la lateral no, porque se pintan las
        dos. El tapete va al final: si el dueño lo cambia, lo pintado ya no vale.
        """
        skin = self.skin()["key"]
        game = self.game
        if game is not None and game.awaiting_choice:
            return ("tie", id(game), skin)
        return ("deal", id(self._next_shoe), self.stake, skin)

    def prepare(self) -> None:
        """Pinta en segundo plano el GIF de cada botón que puede pulsar el jugador.

        Entre manos, «Repartir» con la mano siguiente ya sorteada (con la lateral puesta y sin
        ella, primero la que está elegida); con un empate por decidir, «A la guerra» y
        «Rendirse». Va de uno en uno y cada dibujo comprueba antes de empezar que la mesa
        sigue igual (`Ahead`).
        """
        if not self.cog.ahead or self.is_finished():
            return
        game, renderer, skin = self.game, self.cog.renderer, self.skin()
        if game is not None and game.awaiting_choice:

            async def paint_choice(item: Any) -> Media:
                future = deepcopy(game)
                if item == "war":
                    future.go_to_war()
                    return await renderer.war(future, skin=skin)
                future.surrender()
                return await renderer.surrender(future, skin=skin)

            self.ahead.plan(self._key(), ["war", "surrender"], paint_choice)
            return
        if game is not None and not self._paid:
            return
        if self._next_shoe is None:
            self._next_shoe = self.cog.new_shoe()
        shoe, stake = self._next_shoe, self.stake

        async def paint_deal(item: Any) -> Media:
            future = WarGame(stake=stake, side=stake if item[1] else 0, shoe=list(shoe))
            future.deal()
            return await renderer.deal(future, skin=skin)

        order = [("deal", self.side_on), ("deal", not self.side_on)]
        self.ahead.plan(self._key(), order, paint_deal)

    # -- Dibujo ---------------------------------------------------------------------------

    async def _paint(self, game: WarGame, kind: str) -> Media:
        """Pinta ahora el GIF de `kind` (repartir, guerra o rendirse), si no estaba hecho."""
        scene, skin = self.cog.renderer, self.skin()
        if kind == "war":
            return await scene.war(game, skin=skin)
        if kind == "surrender":
            return await scene.surrender(game, skin=skin)
        return await scene.deal(game, skin=skin)

    async def _show(
        self,
        game: WarGame,
        kind: str,
        edit_fn: EditFn,
        *,
        ready: Media | None = None,
        later: EditFn | None = None,
        balance: int | None = None,
        wait: bool = True,
    ) -> None:
        """Enseña el GIF de `kind` y, cuando acaba, el PNG con el resultado.

        Mientras se pinta (si no estaba ya pintado por adelantado), la mesa ya enseña la
        jugada con los botones apagados y sin resultado, en paralelo al dibujo.

        Args:
            ready: El GIF pintado por adelantado, si lo había.
            later: Cómo se cambia el mensaje después de la primera edición, si no es con
                `edit_fn` (el primer envío de `.guerra` crea el mensaje y luego se edita).
            wait: Si hay que tapar la espera cuando el GIF no estaba pintado.
        """
        self._update_buttons(locked=True)
        busy = WAR_BUSY if kind == "war" else SURRENDER_BUSY if kind == "surrender" else DEAL_BUSY

        async def show_waiting() -> bool:
            try:
                await edit_fn(embed=await self.current_embed(busy=busy), view=self)
            except discord.HTTPException:
                logger.warning("No se pudo apagar la mesa de la guerra", exc_info=True)
                return False
            return True

        media, shown = ready, False
        if media is None:
            if wait:
                media, shown = await asyncio.gather(self._paint(game, kind), show_waiting())
            else:
                media = await self._paint(game, kind)
        after = later or edit_fn
        if media.gif is not None:
            await (after if shown else edit_fn)(
                embed=await self.current_embed(busy=busy, image=GIF_NAME),
                attachments=[discord.File(io.BytesIO(media.gif), filename=GIF_NAME)],
                view=self,
            )
            # Lo que pueda pulsar el jugador después se pinta mientras se ve esta animación.
            self.prepare()
            await asyncio.sleep(media.seconds + REVEAL_MARGIN_SECONDS)
            shown = True
        self._update_buttons()
        # Sin nada enseñado aún (sin GIF y sin la espera tapada) no hay mensaje que editar.
        await (after if shown else edit_fn)(
            embed=await self.current_embed(balance),
            attachments=[discord.File(io.BytesIO(media.png), filename=PNG_NAME)],
            view=self,
        )
        if media.gif is None:
            self.prepare()

    @property
    def side(self) -> int:
        """Apuesta lateral de la próxima mano: igual a la ficha, si está activada."""
        return self.stake if self.side_on else 0

    async def start(self, first_edit: EditFn, later: EditFn | None = None) -> str | None:
        """Cobra la ficha (y la lateral) y reparte una mano nueva.

        Args:
            first_edit: Cómo se enseña el GIF (enviar el mensaje o editarlo).
            later: Cómo se cambia el mensaje después del GIF, si no es con `first_edit`.

        Returns:
            Un mensaje de error para el usuario si no se pudo repartir, o `None`.
        """
        side = self.side
        try:
            await self.cog.economy.place_bet(
                self.guild_id, self.owner.id, game=GAME, stake=self.stake + side
            )
        except InsufficientFundsError as error:
            return insufficient_text(error.balance, self.stake + side)
        # Para las porras: una mano repartida antes del cierre no cuenta.
        self.started_at = int(time.time())
        ready = await self.ahead.take(self._key(), ("deal", self.side_on))
        self.ahead.drop()
        shoe, self._next_shoe = self._next_shoe or self.cog.new_shoe(), None
        self.game = WarGame(stake=self.stake, side=side, shoe=shoe)
        self._paid = False
        self.headline = None
        self.game.deal()
        await self._play(self.game, "deal", first_edit, ready=ready, later=later)
        return None

    async def _play(self, game: WarGame, kind: str, edit_fn: EditFn, **kwargs: Any) -> None:
        """Enseña el GIF; si la mano acabó, la paga antes (sin enseñarla) y cuenta los logros."""
        if game.awaiting_choice:
            await self._show(game, kind, edit_fn, **kwargs)
            return
        balance = await self._settle(game)
        await self._show(game, kind, edit_fn, balance=balance, **kwargs)
        await self._track()

    async def _decide(self, interaction: discord.Interaction, *, war: bool) -> None:
        """Resuelve el empate: rendirse o ir a la guerra (con su segunda apuesta)."""
        game = self.game
        if self._busy or game is None or not game.awaiting_choice:
            await ack(interaction)
            return
        self._busy = True
        try:
            # Cobrar, pagar y dibujar la mesa tardan: se acepta el clic antes.
            await ack(interaction)
            kind = "war" if war else "surrender"
            if war:
                extra = game.extra_stake
                try:
                    await self.cog.economy.place_bet(
                        self.guild_id, self.owner.id, game=GAME, stake=extra
                    )
                except InsufficientFundsError as error:
                    await notify(
                        interaction,
                        f"Para ir a la guerra necesitas {format_amount(extra)} más y tienes "
                        f"{format_amount(error.balance)}. Siempre puedes rendirte.",
                    )
                    return
            ready = await self.ahead.take(self._key(), kind)
            self.ahead.drop()
            if war:
                game.go_to_war()
            else:
                game.surrender()
            self._last_interaction = interaction
            await self._play(game, kind, interaction.edit_original_response, ready=ready)
        finally:
            self._busy = False
        await renta.remind(self.cog.bot, interaction)

    async def _surrender(self, interaction: discord.Interaction) -> None:
        await self._decide(interaction, war=False)

    async def _war(self, interaction: discord.Interaction) -> None:
        await self._decide(interaction, war=True)

    async def _settle(self, game: WarGame) -> int:
        """Paga la mano y actualiza las rachas. Devuelve el saldo final.

        Si la mano ya se pagó (p. ej. el apagado la cerró mientras la banca
        quemaba cartas en pantalla) no se vuelve a pagar.
        """
        if self._paid:
            return await self.balance()
        self._paid = True
        tax_note: str | None = None
        tax_delta = 0
        try:
            settlement = await self.cog.economy.pay_winnings(
                self.guild_id, self.owner.id, game=GAME, amount=game.total_return
            )
            balance = settlement.balance
            tax_note = gambling_tax_line(settlement)
            tax_delta = settlement.tax_delta
        except BalanceLimitError:
            logger.warning("Premio de la guerra por encima del saldo máximo; no se paga.")
            balance = await self.balance()
        self._to_track = (game, balance, tax_delta)
        if game.net > 0:
            self.streak += 1
        elif game.net < 0:
            self.streak = 0
        self._update_runs(game)
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

    def _update_runs(self, game: WarGame) -> None:
        """Manos seguidas ganadas, perdidas y empatadas en la primera carta."""
        if game.won:
            self.win_run, self.lose_run = self.win_run + 1, 0
        elif game.result in (Result.LOSE, Result.WAR_LOSE):
            self.win_run, self.lose_run = 0, self.lose_run + 1
        else:
            self.win_run = self.lose_run = 0
        self.tie_run = self.tie_run + 1 if game.tied else 0

    async def _track(self) -> None:
        """Cuenta la última mano pagada para los logros (una sola vez).

        Se llama después de enseñar el resultado, para que el aviso de un logro
        no se adelante a las cartas.
        """
        if self._to_track is None:
            return
        game, balance, tax_delta = self._to_track
        self._to_track = None
        delta = war_stats(game, win_run=self.win_run, lose_run=self.lose_run, tie_run=self.tie_run)
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
                ("war", int(game.went_to_war)),
                ("started", self.started_at),
            ),
        )

    # -- Fichas y repartir ----------------------------------------------------------

    def _fits(self, balance: int) -> int:
        """Ficha más alta que cabe en `balance` contando la lateral, si está activada."""
        return max(1, balance // (2 if self.side_on else 1))

    async def _refresh(self, interaction: discord.Interaction, balance: int | None = None) -> None:
        # El embed lee el saldo de la base de datos: se acepta el clic antes.
        await ack(interaction)
        self._update_buttons()
        await edit(interaction, embed=await self.current_embed(balance), view=self)
        self._last_interaction = interaction
        # La ficha sale en la imagen: lo pintado para la de antes ya no vale.
        self.prepare()

    async def _halve(self, interaction: discord.Interaction) -> None:
        self.stake = max(1, self.stake // 2)
        await self._refresh(interaction)

    async def _double_stake(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        balance = await self.balance()
        self.stake = max(1, min(self.stake * 2, self._fits(balance)))
        await self._refresh(interaction, balance)

    async def _all_in(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        balance = await self.balance()
        if balance == 0:
            await notify(interaction, insufficient_text(0))
            return
        self.stake = self._fits(balance)
        await self._refresh(interaction, balance)

    async def _toggle_side(self, interaction: discord.Interaction) -> None:
        self.side_on = not self.side_on
        await self._refresh(interaction)

    async def _deal_again(self, interaction: discord.Interaction) -> None:
        if self._busy or (self.game is not None and not self._paid):
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


class War(commands.Cog, name="Guerra"):
    """Guerra (Casino War) con los yapdollars de la economía del bot."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        renderer: WarScene | None = None,
        shoe_factory: Callable[[], list[Card]] = new_shoe,
        casino_channel_ids: CasinoChannelIds = frozenset(),
        ahead: bool | None = None,
    ) -> None:
        """Prepara el cog; el dibujo por defecto es `WarScene`.

        Args:
            ahead: Si las mesas pintan por adelantado el GIF de cada botón
                (`WarTable.prepare`). Por defecto, solo con el dibujo de verdad: con un
                `renderer` de prueba, cada dibujo de más gastaría el azar de guion.
        """
        self.bot = bot
        self.economy = economy
        self.ahead = renderer is None if ahead is None else ahead
        self.renderer = renderer or WarScene()
        self.new_shoe = shoe_factory
        self.casino_channel_ids = casino_channel_ids
        # Mesas abiertas: para resolver sus manos si el bot se apaga. Cada
        # mesa sale de aquí al caducar, así que el tamaño está acotado.
        self.tables: set[WarTable] = set()

    async def cog_unload(self) -> None:
        """Al apagar, resuelve los empates a medias para no quedarse con el dinero."""
        for table in list(self.tables):
            try:
                await table.force_settle()
            except Exception:
                logger.exception("No se pudo cerrar una mano de la guerra al apagar")
            table.stop()
        self.tables.clear()
        await self.renderer.close()

    async def _guerra_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> None:
        """Lógica compartida entre `/guerra` y `.guerra`: abre la mesa y reparte ya."""
        if guild is None:
            await send_error("La guerra solo se juega dentro de un servidor.")
            return
        if error := casino_channel_error(self.casino_channel_ids, channel, "La guerra"):
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

        table = WarTable(self, guild_id=guild.id, owner=user, stake=stake)

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
        try:
            error = await table.start(first_edit, later)
        except discord.HTTPException:
            self.tables.discard(table)
            table.stop()  # que no pinte por adelantado una mesa que nadie ve
            await table.force_settle()
            raise
        if error is not None:
            self.tables.discard(table)
            await send_error(error)
            return

    @app_commands.command(
        name="guerra",
        description="Guerra: tu carta contra la de la banca, y a la guerra si empatas.",
    )
    @app_commands.describe(cantidad="Apuesta: 500, 2k, all… (por defecto 100)")
    @app_commands.guild_only()
    async def guerra(self, interaction: discord.Interaction, cantidad: str | None = None) -> None:
        """Reparte una mano de la Guerra con botones para jugarla.

        Solo en los canales del casino que elige `garito` (sin elegir, los de `CASINO_CHANNEL_IDS`)
        y en sus hilos. Cobra la apuesta al repartir y paga al terminar la mano.
        """

        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            return await interaction.original_response()

        await self._guerra_impl(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            send=send,
            send_error=InteractionResponder(interaction).send_error,
        )
        await renta.remind(self.bot, interaction)

    @commands.command(name="guerra")
    @commands.guild_only()
    async def guerra_text(self, ctx: commands.Context, cantidad: str | None = None) -> None:
        """Versión de texto: `.guerra`, `.guerra 500`, `.guerra all`."""

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        await self._guerra_impl(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            amount_text=cantidad,
            send=send,
            send_error=ContextResponder(ctx).send_error,
        )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(War(bot, economy=bot.economy, casino_channel_ids=bot.casino_channels))
