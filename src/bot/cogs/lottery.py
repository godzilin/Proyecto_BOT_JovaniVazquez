"""Loterías: `loteria`, un panel con botones para todos los juegos del Estado.

Un solo comando abre el panel. Las pestañas cambian de juego sin escribir
nada más:

- 🏠 **Inicio**: el cartel de la administración (los botes en grande y la
  Nacional), los próximos sorteos y lo que lleva vendido y repartido el Estado.
- 🎫 **Nacional**: jueves, sábado, Navidad y Niño. Décimo al azar, billete
  entero (10 décimos) o número elegido. Al comprar sale el décimo dibujado.
- 🔵 **Primitiva**, 🟢 **Bonoloto**, 🟡 **Gordo** y ⭐ **Euromillones**: apuestas
  automáticas (1, 5 o 10) o elegidas a mano, con su tabla de premios y el bote
  que sube cada día. Al comprar sale el resguardo con las bolas.
- 🟣 **Rascas**: X10 y 7 y Media de la ONCE. Una moneda rasca el boleto en un GIF
  y el resultado se escribe cuando acaba. El siguiente rasca de cada botón se
  sortea y se pinta mientras se ve el anterior (`ScratchPlan`), así que sale al
  instante; lo sorteado solo vive en memoria y no se ve hasta pagarlo.
- 🎟️ **Mis boletos**: lo que tienes en juego, lo último que ha salido y el
  último boleto sorteado con los aciertos marcados.

Con `/` el panel es efímero (solo lo ves tú). Con `.loteria` es público, y si
otro pulsa un botón se le abre su propio panel. Las imágenes las pinta
`bot.services.lottery_scene`; si no hay pintor, todo sigue en texto (el rasca,
con las casillas tapadas con spoilers). Una imagen que no ha cambiado no se
vuelve a subir (`LotteryPanel.edit_kwargs`).

Sorteos: una tarea cada minuto celebra los sorteos que ya tocan (solo los que
tienen apuestas en el servidor), paga los premios y lo anuncia en el canal donde
se compró por última vez: primero el GIF de las bolas saliendo del bombo y, al
acabar, el resultado con los premiados (así el texto no destripa la animación).
Las reglas, las probabilidades y el reparto están en `bot.services.lottery`. En
los juegos de bote las probabilidades dependen de cuántas personas (no bots)
tiene el servidor (`Loteria.members`); necesita el intent de miembros para
contarlas; sin la lista en caché usa el recuento de Discord y, sin servidor, las
reales. El bote de cada juego empieza a crecer la primera vez que se mira
(`Loteria.pots`).

Dinero: compras y premios pasan por `EconomyService.lottery`. Las compras van
al Estado sin IGIC y los premios pagan el gravamen especial del 20 % por
encima de 400.000 Y$ (ver su docstring). Comprar y rascar llaman a
`renta.remind`. Los sorteos no son interacciones: no avisan de la renta.
Los boletos por sortear salen en `saldo` (`pending`).

Logros: categoría Loterías, con `achievements.track` al comprar, al rascar (al
acabar la animación: antes destriparía el rasca), al cobrar un premio de sorteo
(después del anuncio) y al comprobar el último boleto en 🎟️ Mis boletos.

Permisos: enviar mensajes y adjuntar archivos en el canal de los anuncios.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import random
import secrets
import sqlite3
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks

from bot.cogs import achievements as logros
from bot.cogs import pets as mascotas
from bot.cogs import renta
from bot.cogs.casino import casino_channel_error
from bot.repositories.lottery import Draw, DrawChanged, LotteryRepository, Pot, Ticket
from bot.services import lottery_render as render
from bot.services.achievements import (
    StatDelta,
    lottery_buy_stats,
    lottery_check_stats,
    lottery_prize_stats,
    scratch_stats,
)
from bot.services.economy import (
    CURRENCY_EMOJI,
    EconomyService,
    InsufficientFundsError,
    LotteryPayout,
    format_amount,
)
from bot.services.lottery import (
    DRAW_TIMEZONE,
    GAME_BY_KEY,
    JACKPOT_GROWTH_DAYS,
    Game,
    Kind,
    LotteryError,
    Pick,
    Prize,
    Settlement,
    category_prizes,
    draw_label,
    draw_result,
    format_pick,
    format_result,
    gravamen,
    guarantee_for,
    jackpot_estimate,
    jackpot_key,
    jackpot_minimum,
    next_draw,
    parse_pick,
    random_pick,
    real_odds,
    scratch,
    scratch_grid,
    scratch_odds,
    server_odds,
    settle_nacional,
    settle_pool,
)
from bot.services.lottery_render import Media, PosterGame
from bot.services.lottery_scene import LotteryScene
from bot.services.pending import Pending, Side
from bot.services.pets import Event, Moment
from bot.services.taxes import LOTTERY_EXEMPT, TAX_COLLECTOR
from bot.services.telemetry import TimedLock
from bot.utils.channels import send_notice
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

TITLE = "Loterías y Apuestas de Jovani"
COLOR = discord.Color.from_rgb(0, 122, 204)
VIEW_TIMEOUT = 600
#: Pestañas del panel: `(clave, etiqueta)`. Dos filas de botones.
TABS: tuple[tuple[str, str], ...] = (
    ("inicio", "🏠 Inicio"),
    ("nacional", "🎫 Nacional"),
    ("primitiva", "🔵 Primitiva"),
    ("bonoloto", "🟢 Bonoloto"),
    ("gordo", "🟡 Gordo"),
    ("euromillones", "⭐ Euromillones"),
    ("rasca", "🟣 Rascas"),
    ("mios", "🎟️ Mis boletos"),
)
NACIONAL_GAMES = ("jueves", "sabado", "navidad", "nino")
POOL_GAMES = ("primitiva", "bonoloto", "gordo", "euromillones")
SCRATCH_GAMES = ("x10", "7ymedia")
#: Apuestas automáticas que ofrece cada botón.
QUICK_BETS = (1, 5, 10)
#: Décimos de un billete entero.
BILLETE = 10
#: Boletos del historial en "Mis boletos".
HISTORY_SIZE = 15
#: Filas de boletos que mira `saldo` (salen antes las de sorteos pendientes).
#: shortcut: quien tenga más filas por sortear ve solo estas; subirlo si pasa.
PENDING_TICKETS = 200
#: Ganadores que se nombran en el anuncio de un sorteo.
ANNOUNCE_WINNERS = 10
#: Nombres de los adjuntos del panel y del anuncio.
POSTER_PNG = "cartel.png"
TICKET_PNG = "boleto.png"
CHECK_PNG = "comprobar.png"
SCRATCH_GIF = "rasca.gif"
DRAW_GIF = "sorteo.gif"
DRAW_PNG = "sorteo.png"
#: Lo que se espera tras la animación antes de escribir el resultado: cubre lo que
#: tarda el cliente en bajar el GIF y empezar a verlo.
REVEAL_MARGIN_SECONDS = 0.8
DRAW_REVEAL_MARGIN_SECONDS = 2.0
#: Apuestas de un sorteo que caben en el resguardo de «Mis boletos».
CHECK_ROWS = 10

#: Frases del rasca con premio (la mitad, normales).
SCRATCH_WIN_LINES = (
    "Tres iguales. Premio.",
    "Ha salido la tercera.",
    "Buen rasca.",
    "La uña ha merecido la pena.",
    "¿Qué? ¿Qué? ¡Toma mangó!",
    "Esto pide un café. ¡Quiero café!",
)
#: Frases del rasca sin premio.
SCRATCH_LOSE_LINES = (
    "Sin premio esta vez.",
    "No hay tres iguales.",
    "Nada en este.",
    "El látex era lo más valioso del boleto.",
    "Otro cartón para el contenedor azul.",
    "Estoy destruido, pero por fuera… por dentro estoy nuevo.",
)
#: Frases del rasca sin premio que enseña dos casillas del premio más alto.
SCRATCH_NEAR_LINES = (
    "Dos del premio más alto y la tercera no.",
    "Casi: dos casillas del máximo.",
    "Faltó una casilla para el gordo.",
    "Más cerca que la 33 de Alonso.",
    "Tan cerca que ya olía a champán.",
)

PLACEHOLDERS = {
    Kind.LOTTO: "3 14 22 30 41 49 R7   (el reintegro es opcional)",
    Kind.GORDO: "3 14 22 30 41 C7   (la clave es opcional)",
    Kind.EURO: "3 14 22 30 41 * 2 9   (las estrellas son opcionales)",
}


def _name(user: discord.abc.User) -> str:
    return discord.utils.escape_markdown(user.display_name)


def _button(
    label: str,
    callback: Callable[[discord.Interaction], Awaitable[None]],
    *,
    custom_id: str,
    style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    disabled: bool = False,
) -> ui.Button:
    """Botón con su callback ya enganchado y su `custom_id` fijo."""
    button: ui.Button = ui.Button(label=label, style=style, disabled=disabled, custom_id=custom_id)
    button.callback = callback  # type: ignore[method-assign]
    return button


def _thousands(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _when(moment: float) -> str:
    return f"<t:{int(moment)}:f> (<t:{int(moment)}:R>)"


# -- Textos ---------------------------------------------------------------------------


def odds_table(game: Game, members: int | None = None) -> str:
    """Tabla de categorías de un juego de bote: qué acertar, probabilidad y premio.

    Con `members`, las probabilidades y los premios fijos son los del servidor
    (`server_odds`, `category_prizes`) y una nota debajo da la probabilidad real
    del bote.
    """
    odds = server_odds(game, members) if members is not None else real_odds(game)
    prizes = category_prizes(game, members)
    rows = []
    for cat in game.categories:
        prize = "BOTE" if cat.jackpot else format_amount(prizes[cat.key])
        rows.append(
            f"{cat.label:<9} {cat.rule:<18} 1 entre {_thousands(round(odds[cat.key])):>11}  {prize}"
        )
    if game.reintegro:
        name = "Reintegro" if game.kind is Kind.LOTTO else "Clave"
        rows.append(f"{name:<9} {'la apuesta':<18} 1 entre {'10':>11}  devuelve el precio")
    table = "```\n" + "\n".join(rows) + "\n```"
    jackpot = next(c for c in game.categories if c.jackpot)
    if members is None or round(odds[jackpot.key]) >= round(real_odds(game)[jackpot.key]):
        return table
    return table + (
        f"\n-# Probabilidades a la medida del servidor ({_thousands(members)} personas): si "
        "cada uno juega una apuesta por sorteo, el bote cae más o menos una vez al mes. "
        f"En la vida real, 1 entre {_thousands(jackpot.odds(game.combinations))}."
    )


def growth_line(game: Game, pot: Pot, now: float) -> str:
    """Cuánto sube el mínimo del bote que pone el Estado y hasta cuándo."""
    days = (now - pot.since) / 86_400
    if days >= JACKPOT_GROWTH_DAYS:
        return (
            f"El Estado garantiza {format_amount(jackpot_minimum(game, days))}, lo máximo: "
            f"lleva {JACKPOT_GROWTH_DAYS} días sin acertante."
        )
    return (
        f"El Estado sube el bote mínimo {format_amount(game.jackpot_daily)} cada día sin "
        f"acertante (empieza en {format_amount(game.jackpot_floor)} y para a los "
        f"{JACKPOT_GROWTH_DAYS} días)."
    )


def nacional_table(game: Game) -> str:
    """Premios principales de un sorteo de la Nacional, por décimo."""
    program = game.program
    assert program is not None
    lines = []
    for prize in program.main:
        count = f"{prize.count} × " if prize.count > 1 else ""
        lines.append(f"{prize.label}: {count}**{format_amount(prize.amount)}**")
    if program.pedrea:
        lines.append(
            f"Pedrea: {_thousands(program.pedrea.count)} × {format_amount(program.pedrea.amount)}"
        )
    for digits, count, amount in program.extractions:
        lines.append(f"Extracciones de {digits} cifras: {count} × {format_amount(amount)}")
    lines.append(
        f"Aproximaciones, centenas y terminaciones · Reintegro: {format_amount(game.price)}"
    )
    lines.append(
        f"-# 1 entre 100.000 para el 1er premio. Devuelve el {game.prize_rate:.0%} "
        "de lo vendido.".replace("%", " %")
    )
    return "\n".join(lines)


def scratch_card(game: Game) -> str:
    """Tabla resumida de un rasca."""
    assert game.scratch is not None
    prizes, total = game.scratch
    top = ", ".join(format_amount(p.amount) for p in prizes[:3])
    return (
        f"### {game.emoji} {game.name} · {format_amount(game.price)}\n"
        f"{game.blurb}\nPremios gordos: {top}…\n"
        f"-# 1 de cada {str(scratch_odds(game)).replace('.', ',')} boletos tiene premio · "
        f"devuelve el {game.prize_rate:.0%} · emisión de {_thousands(total)} boletos".replace(
            "%", " %"
        )
    )


def scratch_text(game: Game, prize: Prize | None, cells: list[int], tax: int) -> str:
    """El rasca recién comprado, con las casillas tapadas (spoilers de Discord).

    Es el rasca sin pintor (sin Node ni Chromium): se rasca pulsando cada casilla.
    """
    width = max(len(format_amount(c)) for c in cells)
    rows = []
    for row in range(3):
        rows.append(
            " ".join(f"||`{format_amount(c):^{width}}`||" for c in cells[row * 3 : row * 3 + 3])
        )
    if prize is None:
        verdict = "||Esta vez no. El próximo rasca tiene tu nombre.||"
    else:
        verdict = f"||🎉 ¡Tres iguales! Premio de **{format_amount(prize.amount)}**||"
    lines = [f"### {game.emoji} {game.name} · rasca las casillas", *rows, verdict]
    if tax:
        lines.append(
            f"-# 🐶 {TAX_COLLECTOR} se queda ||{format_amount(tax)}|| de gravamen especial "
            f"(20 % de lo que pasa de {format_amount(LOTTERY_EXEMPT)})."
        )
    return "\n".join(lines)


def scratch_verdict(game: Game, prize: Prize | None, tax: int, *, near: bool, line: str) -> str:
    """El resultado de un rasca ya dibujado, para cuando acaba la animación."""
    if prize is None:
        head = f"{game.emoji} **{game.name}** · sin premio."
    else:
        head = f"🎉 **{game.name}** · premio de **{format_amount(prize.amount)}**."
    lines = [f"{head} {line}"]
    if near and prize is None:
        assert game.scratch is not None
        top = format_amount(game.scratch[0][0].amount)
        lines.append(f"-# Dos casillas de {top}, el premio más alto: faltó la tercera.")
    if tax:
        lines.append(
            f"-# 🐶 Gravamen especial: {format_amount(tax)} (20 % de lo que pasa de "
            f"{format_amount(LOTTERY_EXEMPT)})."
        )
    return "\n".join(lines)


def ticket_tags(game: Game, labels: list[str]) -> frozenset[str]:
    """Etiquetas de logros de un boleto premiado (ver `LOTTERY_TAGS`)."""
    tags: set[str] = set()
    for label in labels:
        if label == "Reintegro":
            tags.add("reintegro")
        elif label == "Pedrea":
            tags.add("pedrea")
        elif game.key == "navidad" and label == "EL GORDO":
            tags.add("gordo_navidad")
    if game.categories:
        jackpot = next(c for c in game.categories if c.jackpot)
        names = {f"{c.label} ({c.rule})": c for c in game.categories}
        for label in labels:
            cat = names.get(label)
            if cat is None:
                continue
            if cat is jackpot or (game.key == "primitiva" and cat.key == "1"):
                tags.add("jackpot")
            if game.kind is Kind.LOTTO and cat.key == "4":
                tags.add("lotto4")
            if game.kind is Kind.LOTTO and cat.key in ("2", "3"):
                tags.add("lotto5")
    return frozenset(tags)


# -- Panel ----------------------------------------------------------------------------


class PickModal(ui.Modal):
    """Apuestas o número elegidos a mano."""

    def __init__(self, panel: LotteryPanel, game: Game) -> None:
        super().__init__(title=f"{game.emoji} {game.name}"[:45])
        self.panel = panel
        self.game = game
        if game.kind is Kind.NACIONAL:
            self.picks: ui.TextInput = ui.TextInput(
                label="Número (5 cifras)", placeholder="48288", min_length=1, max_length=5
            )
            self.amount: ui.TextInput | None = ui.TextInput(
                label=f"Décimos (1-{BILLETE})", default="1", min_length=1, max_length=2
            )
            self.add_item(self.picks)
            self.add_item(self.amount)
        else:
            self.picks = ui.TextInput(
                label="Una apuesta por línea (hasta 10)",
                style=discord.TextStyle.paragraph,
                placeholder=PLACEHOLDERS[game.kind],
                max_length=500,
            )
            self.amount = None
            self.add_item(self.picks)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Lee las apuestas y compra."""
        rng = self.panel.cog.rng
        try:
            if self.amount is not None:
                quantity = int(self.amount.value.strip() or "1")
                if not 1 <= quantity <= BILLETE:
                    raise LotteryError(f"Entre 1 y {BILLETE} décimos.")
                picks = [(parse_pick(self.game, self.picks.value, rng), quantity)]
            else:
                lines = [line for line in self.picks.value.splitlines() if line.strip()]
                if not 1 <= len(lines) <= 10:
                    raise LotteryError("Entre 1 y 10 apuestas, una por línea.")
                picks = [(parse_pick(self.game, line, rng), 1) for line in lines]
        except (LotteryError, ValueError) as error:
            message = str(error) if isinstance(error, LotteryError) else "Eso no es un número."
            self.panel.notice = f"❌ {message}"
            self.panel.rebuild()
            await interaction.response.edit_message(view=self.panel)
            return
        await self.panel.cog.buy(interaction, self.panel, self.game, picks)


@dataclass(slots=True)
class ScratchPlan:
    """Un rasca sorteado antes de comprarlo, para pintarlo mientras se ve el anterior.

    Solo vive en la memoria del panel de su dueño y no se enseña hasta pagarlo.

    Attributes:
        game: El rasca.
        prize: Lo que tiene (`None`, sin premio).
        cells: Sus nueve casillas.
        seed: Del orden de rascado, el camino de la moneda y el número de serie.
        drawing: El dibujo, en marcha o acabado (`None` si aún no ha empezado).
    """

    game: Game
    prize: Prize | None
    cells: list[int]
    seed: int
    drawing: asyncio.Task[Media | None] | None = None

    @property
    def near(self) -> bool:
        """Si, sin premio, enseña dos casillas del premio más alto."""
        assert self.game.scratch is not None
        top = self.game.scratch[0][0].amount
        return render.scratch_near_miss(self.cells, self.prize and self.prize.amount, top)


class LotteryPanel(ui.LayoutView):
    """El panel de loterías de un miembro: pestañas, compra, imágenes y resultados."""

    def __init__(self, cog: Loteria, guild: discord.Guild, owner: discord.abc.User) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild = guild
        self.owner = owner
        self.tab = "inicio"
        self.nacional = "jueves"
        self.notice: str | None = None
        self.body = ""
        self.balance = 0
        self.message: discord.Message | None = None
        self.interaction: discord.Interaction | None = None
        #: La imagen del panel `(nombre, bytes)`: cartel, boleto, rasca o comprobación.
        self.image: tuple[str, bytes] | None = None
        self._image_serial = 0
        #: La imagen que ya tiene Discord (`None`, ninguna): la misma no se vuelve a subir.
        self._sent_serial: int | None = None
        #: El próximo rasca de cada botón, sorteado y pintándose (`ScratchPlan`).
        self.scratches: dict[str, ScratchPlan] = {}
        self._painting: asyncio.Task[None] | None = None
        #: Sube con cada rasca: el resultado de uno que ya no se ve no se escribe.
        self.scratch_serial = 0

    async def load(self, *, keep_image: bool = False) -> None:
        """Lee lo que hace falta para la pestaña actual y repinta.

        Args:
            keep_image: Deja la imagen que hay (el boleto recién comprado, el rasca)
                en vez de la de la pestaña.
        """
        self.balance = await self.cog.economy.balance(self.guild.id, self.owner.id)
        self.body = await self.cog.tab_text(self.guild.id, self.owner.id, self.tab, self.nacional)
        if not keep_image:
            self.set_image(await self.cog.tab_image(self.guild.id, self.owner, self.tab))
        self.rebuild()

    def set_image(self, image: tuple[str, bytes | None] | None) -> None:
        """Cambia la imagen del panel; con los mismos bytes no cuenta como nueva."""
        new = image if image is not None and image[1] else None
        if new == self.image:
            return
        self.image = new  # type: ignore[assignment]
        self._image_serial += 1

    def edit_kwargs(self) -> dict[str, Any]:
        """Para cambiar el mensaje del panel: la imagen solo se sube si ha cambiado."""
        kwargs: dict[str, Any] = {"view": self}
        current = self._image_serial if self.image is not None else None
        if current != self._sent_serial:
            kwargs["attachments"] = self.files()
            self._sent_serial = current
        return kwargs

    def send_kwargs(self) -> dict[str, Any]:
        """Para mandar el panel en un mensaje nuevo."""
        self._sent_serial = self._image_serial if self.image is not None else None
        files = self.files()
        return {"view": self, "files": files} if files else {"view": self}

    def files(self) -> list[discord.File]:
        """La imagen como adjunto (vacío si no hay)."""
        if self.image is None:
            return []
        name, data = self.image
        return [discord.File(io.BytesIO(data), filename=name)]

    @property
    def game(self) -> Game | None:
        """Juego de la pestaña actual (el sorteo elegido en la Nacional)."""
        if self.tab == "nacional":
            return GAME_BY_KEY[self.nacional]
        return GAME_BY_KEY.get(self.tab)

    def rebuild(self) -> None:
        """Monta los componentes con el estado actual."""
        self.clear_items()
        container = ui.Container(accent_colour=COLOR)
        container.add_item(
            ui.TextDisplay(
                f"# 🎟️ {TITLE}\n-# {CURRENCY_EMOJI} Saldo de {_name(self.owner)}: "
                f"{format_amount(self.balance)} · Todo lo que juegas va al Estado"
            )
        )
        for start in (0, 5):
            row: ui.ActionRow = ui.ActionRow()
            for key, label in TABS[start : start + 5]:
                selected = key == self.tab
                style = discord.ButtonStyle.primary if selected else discord.ButtonStyle.secondary
                row.add_item(
                    _button(label, self._tab(key), custom_id=f"loteria:tab:{key}", style=style)
                )
            container.add_item(row)
        container.add_item(ui.Separator())
        if self.tab == "nacional":
            subtabs: ui.ActionRow = ui.ActionRow()
            for key in NACIONAL_GAMES:
                game = GAME_BY_KEY[key]
                style = (
                    discord.ButtonStyle.primary
                    if key == self.nacional
                    else discord.ButtonStyle.secondary
                )
                short = game.name.replace("Lotería Nacional del ", "").replace("Lotería de", "")
                short = short.replace("Lotería del", "").strip().capitalize()
                subtabs.add_item(
                    _button(
                        f"{game.emoji} {short}",
                        self._nacional(key),
                        custom_id=f"loteria:nacional:{key}",
                        style=style,
                    )
                )
            container.add_item(subtabs)
        if self.image is not None:
            container.add_item(
                ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{self.image[0]}"))
            )
        container.add_item(ui.TextDisplay(self.body[:3500]))
        actions = self._actions()
        if actions is not None:
            container.add_item(actions)
        if self.notice:
            container.add_item(ui.Separator())
            container.add_item(ui.TextDisplay(self.notice[:1900]))
        self.add_item(container)

    def _actions(self) -> ui.ActionRow | None:
        game = self.game
        row: ui.ActionRow = ui.ActionRow()
        if self.tab == "rasca":
            for key in SCRATCH_GAMES:
                scratch_game = GAME_BY_KEY[key]
                row.add_item(
                    _button(
                        f"{scratch_game.emoji} Rascar {scratch_game.name.replace('Rasca ', '')} · "
                        f"{format_amount(scratch_game.price)}",
                        self._scratch(key),
                        custom_id=f"loteria:rascar:{key}",
                        style=discord.ButtonStyle.success,
                    )
                )
            return row
        if game is None:
            return None
        if game.kind is Kind.NACIONAL:
            row.add_item(
                _button(
                    f"🎲 Décimo al azar · {format_amount(game.price)}",
                    self._quick(game.key, 1),
                    custom_id=f"loteria:rapido:{game.key}:1",
                    style=discord.ButtonStyle.success,
                )
            )
            row.add_item(
                _button(
                    f"🎲 Billete (10 décimos) · {format_amount(game.price * BILLETE)}",
                    self._quick(game.key, BILLETE),
                    custom_id=f"loteria:rapido:{game.key}:{BILLETE}",
                    style=discord.ButtonStyle.success,
                )
            )
            row.add_item(
                _button(
                    "✍️ Elegir número",
                    self._choose(game.key),
                    custom_id=f"loteria:elegir:{game.key}",
                )
            )
            return row
        for count in QUICK_BETS:
            label = "apuesta" if count == 1 else "apuestas"
            row.add_item(
                _button(
                    f"🎲 {count} {label} · {format_amount(game.price * count)}",
                    self._quick(game.key, count),
                    custom_id=f"loteria:rapido:{game.key}:{count}",
                    style=discord.ButtonStyle.success,
                )
            )
        row.add_item(
            _button(
                "✍️ Elegir números", self._choose(game.key), custom_id=f"loteria:elegir:{game.key}"
            )
        )
        return row

    # -- Rascas por adelantado ---------------------------------------------------------

    def prepare_scratches(self) -> None:
        """Sortea el próximo rasca de cada botón que no tenga y los pinta de uno en uno.

        Se llama al abrir la pestaña de rascas y después de cada rasca. Los
        dibujos no se cortan a medias: el que falta lo empieza el clic si llega antes.
        """
        if not self.cog.ahead:
            return
        for key in SCRATCH_GAMES:
            if key not in self.scratches:
                self.scratches[key] = self.cog.plan_scratch(GAME_BY_KEY[key])
        if self._painting is None or self._painting.done():
            self._painting = asyncio.create_task(self._paint_scratches())

    async def _paint_scratches(self) -> None:
        for key in SCRATCH_GAMES:
            plan = self.scratches.get(key)
            if plan is not None:
                await self.cog.scratch_media(plan)

    def take_scratch(self, game: Game) -> ScratchPlan:
        """El rasca sorteado de ese botón, o uno nuevo si no había."""
        return self.scratches.pop(game.key, None) or self.cog.plan_scratch(game)

    # -- Botones ----------------------------------------------------------------------

    async def _redirect(self, interaction: discord.Interaction) -> bool:
        """Si no es el dueño, le abre su propio panel. Devuelve si lo hizo."""
        if interaction.user.id == self.owner.id:
            return False
        own = LotteryPanel(self.cog, self.guild, interaction.user)
        own.tab, own.nacional = self.tab, self.nacional
        # Cargar el panel va a la base de datos: se acepta el clic antes.
        await ack(interaction, new_message=True)
        await own.load()
        await edit(interaction, **own.edit_kwargs())
        own.interaction = interaction
        if own.tab == "rasca":
            own.prepare_scratches()
        return True

    def _tab(self, key: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            if await self._redirect(interaction):
                return
            await ack(interaction)
            self.tab, self.notice = key, None
            await self.load()
            await edit(interaction, **self.edit_kwargs())
            if key == "rasca":
                self.prepare_scratches()
            elif key == "mios":
                await self.cog.note_check(interaction, self)

        return callback

    def _nacional(self, key: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            if await self._redirect(interaction):
                return
            await ack(interaction)
            self.nacional, self.notice = key, None
            await self.load()
            await edit(interaction, **self.edit_kwargs())

        return callback

    def _quick(self, key: str, count: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            if await self._redirect(interaction):
                return
            game = GAME_BY_KEY[key]
            rng = self.cog.rng
            if game.kind is Kind.NACIONAL:
                picks = [(random_pick(game, rng), count)]
            else:
                picks = [(random_pick(game, rng), 1) for _ in range(count)]
            await self.cog.buy(interaction, self, game, picks)

        return callback

    def _choose(self, key: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            if await self._redirect(interaction):
                return
            await interaction.response.send_modal(PickModal(self, GAME_BY_KEY[key]))

        return callback

    def _scratch(self, key: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            if await self._redirect(interaction):
                return
            await self.cog.scratch(interaction, self, GAME_BY_KEY[key])

        return callback

    async def on_timeout(self) -> None:
        """Apaga los botones al caducar y suelta los rascas sorteados."""
        self.scratches.clear()
        for child in self.walk_children():
            if isinstance(child, ui.Button):
                child.disabled = True
        try:
            if self.interaction is not None:
                await self.interaction.edit_original_response(view=self)
            elif self.message is not None:
                await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar un panel de loterías", exc_info=True)


# -- Cog ------------------------------------------------------------------------------


class Loteria(commands.Cog):
    """Loterías del Estado: panel, compras, rascas y sorteos."""

    def __init__(
        self,
        bot: commands.Bot,
        economy: EconomyService,
        repository: LotteryRepository,
        *,
        casino_channel_ids: frozenset[int] = frozenset(),
        clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
        scene: LotteryScene | None = None,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.repository = repository
        self.casino_channel_ids = casino_channel_ids
        self.clock = clock
        # Las bolas salen del generador del sistema operativo: nadie puede
        # predecir un sorteo mirando los anteriores.
        self.rng = rng or secrets.SystemRandom()
        self._settling = TimedLock("lottery:Loteria._settling")
        self.scene = scene or LotteryScene()
        #: Sortear y pintar el próximo rasca antes del clic. Las pruebas con un azar
        #: de guion lo apagan: cada sorteo de más gastaría sus tiradas.
        self.ahead = rng is None
        #: El cartel de cada servidor y lo que enseña: no se repinta si no cambia.
        self._posters: dict[int, tuple[tuple, bytes]] = {}
        #: Lo que espera a que acabe una animación (el resultado de un rasca o de un
        #: sorteo): se guarda para que no lo recoja el recolector a mitad.
        self._later: set[asyncio.Task[None]] = set()

    async def cog_load(self) -> None:
        """Crea las tablas (idempotente) y arranca los sorteos."""
        await self.repository.initialize()
        self._draws.start()

    async def cog_unload(self) -> None:
        """Para la tarea de sorteos, espera lo que quedaba por escribir y cierra el pintor."""
        self._draws.cancel()
        if self._later:
            await asyncio.wait(list(self._later), timeout=10)
        await self.scene.close()

    def later(self, coroutine: Awaitable[None]) -> None:
        """Deja corriendo algo que espera a una animación, sin perderlo de vista."""
        task = asyncio.ensure_future(coroutine)
        self._later.add(task)
        task.add_done_callback(self._later.discard)

    # -- Dibujos ---------------------------------------------------------------------

    async def _still(self, state: dict[str, Any]) -> bytes | None:
        """PNG de una imagen quieta, o `None` si no se puede (el panel sigue en texto)."""
        if self.scene.disabled:
            return None
        try:
            return await self.scene.still(state)
        except Exception:
            # Una imagen que falla no puede dejar a nadie sin comprar ni ver sus boletos.
            logger.exception("No se pudo dibujar una imagen de la lotería (%s)", state["mode"])
            return None

    async def _animate(
        self, meta: dict[str, Any], states: list[dict[str, Any]], durations: list[int]
    ) -> Media | None:
        """GIF de un rasca o un sorteo, o `None` si no se puede (sale en texto)."""
        if self.scene.disabled:
            return None
        try:
            return await self.scene.animate(meta, states, durations)
        except Exception:
            logger.exception("No se pudo dibujar una animación de la lotería (%s)", meta["mode"])
            return None

    def plan_scratch(self, game: Game) -> ScratchPlan:
        """Sortea un rasca sin cobrarlo (`ScratchPlan`): premio, casillas y semilla."""
        prize = scratch(game, self.rng)
        cells = scratch_grid(game, prize, self.rng)
        return ScratchPlan(game, prize, cells, self.rng.randrange(2**31))

    async def scratch_media(self, plan: ScratchPlan) -> Media | None:
        """El GIF del rasca, empezándolo si nadie lo había empezado."""
        if plan.drawing is None:
            plan.drawing = asyncio.create_task(self._paint_scratch(plan))
        return await plan.drawing

    async def _paint_scratch(self, plan: ScratchPlan) -> Media | None:
        prize = plan.prize.amount if plan.prize else None
        order = render.scratch_order(plan.cells, prize, random.Random(plan.seed))
        tax = gravamen(prize) if prize else 0
        assert plan.game.scratch is not None
        states, durations = render.scratch_states(
            plan.cells,
            order,
            render.scratch_banner(plan.prize, tax),
            top=plan.game.scratch[0][0].amount,
        )
        meta = render.scratch_meta(plan.game, plan.cells, seed=plan.seed)
        return await self._animate(meta, states, durations)

    async def tab_image(
        self, guild_id: int, owner: discord.abc.User, tab: str
    ) -> tuple[str, bytes | None] | None:
        """La imagen de una pestaña: el cartel en 🏠 Inicio y el último boleto en 🎟️."""
        if tab == "inicio":
            return POSTER_PNG, await self.poster(guild_id)
        if tab == "mios":
            return CHECK_PNG, await self.check_image(guild_id, owner)
        return None

    async def poster(self, guild_id: int) -> bytes | None:
        """El cartel de la administración: botes y próximos sorteos (`render.poster_state`).

        Se pinta otra vez solo si cambia lo que enseña (un bote, un sorteo, un día).
        """
        now = self.clock()
        botes = await self.jackpots(guild_id)
        pots = await self.pots(guild_id)
        pools = [
            PosterGame(
                GAME_BY_KEY[key],
                next_draw(GAME_BY_KEY[key], now),
                botes[key],
                hot=botes[key] >= 2 * GAME_BY_KEY[key].jackpot_floor,
            )
            for key in POOL_GAMES
        ]
        nacional = []
        for key in NACIONAL_GAMES:
            game = GAME_BY_KEY[key]
            assert game.program is not None
            nacional.append(PosterGame(game, next_draw(game, now), game.program.main[0].amount))
        state = render.poster_state(pools, nacional, now=now)
        key = (repr(state), *(pots[k].since for k in POOL_GAMES))
        cached = self._posters.get(guild_id)
        if cached is not None and cached[0] == key:
            return cached[1]
        png = await self._still(state)
        if png is not None:
            self._posters[guild_id] = (key, png)
        return png

    async def check_image(self, guild_id: int, owner: discord.abc.User) -> bytes | None:
        """El último boleto sorteado de un miembro, con los aciertos marcados."""
        last = await self.repository.last_drawn(guild_id, owner.id)
        if last is None or last[0].result is None:
            return None
        draw, tickets = last
        return await self._still(self._ticket_state(draw, tickets, owner))

    def _ticket_state(
        self, draw: Draw, tickets: list[Ticket], owner: discord.abc.User
    ) -> dict[str, Any]:
        """El décimo (el mejor premiado) o el resguardo de unos boletos de un sorteo."""
        game = GAME_BY_KEY[draw.game]
        if game.kind is Kind.NACIONAL:
            best = max(tickets, key=lambda t: (t.prize, -t.id))
            return render.ticket_state(
                game,
                Pick.decode(best.pick).numbers[0],
                draw_at=draw.draw_at,
                quantity=best.quantity,
                ticket_id=best.id,
                owner=owner.display_name,
                result=draw.result,
            )
        summary = draw.summary or {}
        return render.slip_state(
            game,
            [Pick.decode(t.pick) for t in tickets[:CHECK_ROWS]],
            draw_at=draw.draw_at,
            owner=owner.display_name,
            jackpot=int(summary.get("jackpot", 0)),
            prizes=category_prizes(game, self.members(draw.guild_id)),
            result=draw.result,
        )

    async def note_check(self, interaction: discord.Interaction, panel: LotteryPanel) -> None:
        """Cuenta que alguien ha comprobado su último boleto sorteado y deja hablar a la mascota."""
        if panel.image is None or panel.image[0] != CHECK_PNG:
            return
        last = await self.repository.last_drawn(panel.guild.id, panel.owner.id)
        if last is None:
            return
        won = sum(t.prize for t in last[1])
        moment = Moment(Event.LOTTERY_WIN if won else Event.LOTTERY_LOSE)
        if line := await mascotas.cameo(self.bot, panel.guild.id, panel.owner.id, moment):
            panel.notice = line
            panel.rebuild()
            await edit(interaction, view=panel)
        logros.note(
            self.bot,
            panel.guild.id,
            panel.owner.id,
            lottery_check_stats(when=datetime.fromtimestamp(self.clock(), DRAW_TIMEZONE)),
            interaction.channel_id,
        )

    # -- Textos de las pestañas ------------------------------------------------------

    async def tab_text(self, guild_id: int, user_id: int, tab: str, nacional: str) -> str:
        """Cuerpo de una pestaña del panel."""
        if tab == "inicio":
            return await self._home_text(guild_id, user_id)
        if tab == "mios":
            return await self._mine_text(guild_id, user_id)
        if tab == "rasca":
            return "\n\n".join(scratch_card(GAME_BY_KEY[key]) for key in SCRATCH_GAMES)
        key = nacional if tab == "nacional" else tab
        return await self._game_text(guild_id, user_id, GAME_BY_KEY[key])

    def members(self, guild_id: int) -> int | None:
        """Personas (no bots) del servidor, o `None` si el bot no lo tiene en caché.

        Con `None` las loterías usan las probabilidades y el bote garantizado reales.
        """
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return None
        humans = sum(1 for member in getattr(guild, "members", ()) if not member.bot)
        if humans:
            return humans
        # Sin la lista de miembros en caché, el recuento de Discord (con bots).
        count = getattr(guild, "member_count", None)
        return count if isinstance(count, int) and count > 0 else None

    async def pots(self, guild_id: int) -> dict[str, Pot]:
        """Los botes del servidor; los que aún no existían empiezan a crecer ahora."""
        return await self.repository.open_pots(guild_id, POOL_GAMES, self.clock())

    async def jackpots(self, guild_id: int) -> dict[str, int]:
        """Bote que se anuncia para el próximo sorteo de cada juego de bote."""
        now = self.clock()
        pots = await self.pots(guild_id)
        open_draws = await self.repository.open_draws(guild_id)
        balance = await self.economy.state_balance(guild_id)
        botes = {}
        for key in POOL_GAMES:
            game = GAME_BY_KEY[key]
            pot = pots[key]
            draw = open_draws.get((key, next_draw(game, now)))
            botes[key] = jackpot_estimate(
                game,
                sales=draw.sales if draw is not None else 0,
                carry=pot.carry,
                guarantee=guarantee_for(game, balance, (now - pot.since) / 86_400),
            )
        return botes

    async def _home_text(self, guild_id: int, user_id: int) -> str:
        now = self.clock()
        botes = await self.jackpots(guild_id)
        lines = ["## Próximos sorteos"]
        for key in (*NACIONAL_GAMES, *POOL_GAMES):
            game = GAME_BY_KEY[key]
            at = next_draw(game, now)
            extra = f" · bote **{format_amount(botes[key])}**" if key in botes else ""
            lines.append(f"{game.emoji} **{game.name}** · <t:{int(at)}:R>{extra}")
        totals = await self.repository.totals(guild_id)
        lines.append(
            f"\n🏛️ El Estado ha vendido **{format_amount(totals.sold)}** y repartido "
            f"**{format_amount(totals.paid)}** en premios "
            f"({_thousands(totals.winners)} boletos premiados)."
        )
        lines.append(
            "-# Los botes suben cada día que nadie los acierta: el Estado garantiza un "
            "mínimo que crece hasta que alguien se lo lleva."
        )
        return "\n".join(lines)

    async def _game_text(self, guild_id: int, user_id: int, game: Game) -> str:
        now = self.clock()
        at = next_draw(game, now)
        lines = [
            f"## {game.emoji} {game.name}",
            game.blurb,
            f"🗓️ Próximo sorteo: {_when(at)} · {format_amount(game.price)} "
            f"{'el décimo' if game.kind is Kind.NACIONAL else 'la apuesta'}",
        ]
        if game.categories:
            bote = (await self.jackpots(guild_id))[game.key]
            pot = (await self.pots(guild_id))[game.key]
            lines.append(f"💰 Bote: **{format_amount(bote)}**")
            lines.append(f"-# {growth_line(game, pot, now)}")
            lines.append(odds_table(game, self.members(guild_id)))
        else:
            lines.append(nacional_table(game))
        mine = await self.repository.member_tickets(guild_id, user_id, game=game.key, draw_at=at)
        if mine:
            units = sum(t.quantity for t in mine)
            shown = [
                f"`{format_pick(game, Pick.decode(t.pick))}`"
                + (f" ×{t.quantity}" if t.quantity > 1 else "")
                for t in mine[:8]
            ]
            more = f" y {len(mine) - 8} más" if len(mine) > 8 else ""
            lines.append(f"🎟️ Llevas {units} en este sorteo: " + ", ".join(shown) + more)
        last = await self.repository.last_draw(guild_id, game.key)
        if last is not None and last.result is not None:
            lines.append(
                f"-# Último sorteo ({draw_label(game, last.draw_at)}): "
                + format_result(game, last.result).split("\n")[0]
            )
        return "\n".join(lines)

    async def _mine_text(self, guild_id: int, user_id: int) -> str:
        records = await self.repository.history(guild_id, user_id, limit=HISTORY_SIZE)
        if not records:
            return "## 🎟️ Mis boletos\nNada todavía. Hoy puede ser tu día."
        pending = [r for r in records if r.status == "open"]
        done = [r for r in records if r.status != "open"]
        lines = ["## 🎟️ Mis boletos"]
        if pending:
            lines.append("**En juego**")
            for record in pending:
                game = GAME_BY_KEY[record.game]
                count = f" ×{record.quantity}" if record.quantity > 1 else ""
                lines.append(
                    f"{game.emoji} `{format_pick(game, Pick.decode(record.pick))}`{count} · "
                    f"<t:{int(record.draw_at)}:R>"
                )
        if done:
            lines.append("**Últimos resultados**")
            for record in done:
                game = GAME_BY_KEY[record.game]
                outcome = (
                    f"✅ {format_amount(record.prize)} ({record.detail})"
                    if record.prize
                    else "❌ Nada"
                )
                lines.append(
                    f"{game.emoji} `{format_pick(game, Pick.decode(record.pick))}` · {outcome}"
                )
        return "\n".join(lines)

    # -- Compras ---------------------------------------------------------------------

    async def open_panel(self, guild: discord.Guild, owner: discord.abc.User) -> LotteryPanel:
        """Panel listo para enviar."""
        panel = LotteryPanel(self, guild, owner)
        await panel.load()
        return panel

    async def buy(
        self,
        interaction: discord.Interaction,
        panel: LotteryPanel,
        game: Game,
        picks: list[tuple[Pick, int]],
    ) -> None:
        """Cobra unas apuestas (o décimos), las apunta y repinta el panel.

        Responde a la interacción editando el panel; después avisa de la
        renta y apunta los logros.
        """
        guild, user = panel.guild, interaction.user
        # Cobrar y apuntar los boletos van a la base de datos: se acepta el clic antes.
        await ack(interaction)
        now = self.clock()
        draw_at = next_draw(game, now)
        units = sum(quantity for _, quantity in picks)
        cost = game.price * units
        try:
            (_, owned), balances = await self.economy.lottery(
                guild.id,
                charges=[(user.id, cost, f"loteria:{game.key}")],
                hook=self.repository.reserve(
                    guild.id,
                    user.id,
                    game=game.key,
                    draw_at=draw_at,
                    picks=[(pick.encode(), quantity) for pick, quantity in picks],
                    price=game.price,
                    channel_id=interaction.channel_id,
                    now=now,
                    limit=game.max_per_draw,
                ),
            )
        except LotteryError as error:
            panel.notice = f"❌ {error}"
        except InsufficientFundsError as error:
            panel.notice = (
                f"❌ Son {format_amount(cost)} y tienes "
                f"{format_amount(error.balance)}. Cobra el `imv` y vuelve."
            )
        else:
            shown = ", ".join(
                f"`{format_pick(game, pick)}`" + (f" ×{quantity}" if quantity > 1 else "")
                for pick, quantity in picks
            )
            lines = [
                f"✅ **{game.name}**, sorteo del <t:{int(draw_at)}:f>: {shown}",
                f"-# Pagas {format_amount(cost)}, sin IGIC (la lotería está exenta). "
                f"Llevas {owned} de {game.max_per_draw} en este sorteo.",
            ]
            if hint := await renta.hint(self.bot, guild.id, user.id, Moment(Event.BUY)):
                lines.append(hint)
            jackpot = (await self.jackpots(guild.id))[game.key] if game.categories else 0
            panel.notice = "\n".join(lines)
            image = await self._bought_image(game, picks, draw_at, user, jackpot=jackpot)
            panel.set_image((TICKET_PNG, image))
            await panel.load(keep_image=True)
            await edit(interaction, **panel.edit_kwargs())
            await renta.remind(self.bot, interaction)
            await logros.track(
                self.bot,
                guild.id,
                user,
                interaction.channel,
                lottery_buy_stats(
                    game=game.key,
                    units=units,
                    cost=cost,
                    owned_in_draw=owned,
                    balance_after=balances[user.id],
                    jackpot=jackpot,
                    picks=[pick.numbers for pick, _ in picks],
                    when=datetime.fromtimestamp(now, DRAW_TIMEZONE),
                ),
            )
            return
        panel.rebuild()
        await edit(interaction, **panel.edit_kwargs())

    async def _bought_image(
        self,
        game: Game,
        picks: list[tuple[Pick, int]],
        draw_at: float,
        user: discord.abc.User,
        *,
        jackpot: int,
    ) -> bytes | None:
        """El décimo o el resguardo de lo que se acaba de comprar."""
        if game.kind is Kind.NACIONAL:
            pick, quantity = picks[0]
            state = render.ticket_state(
                game,
                pick.numbers[0],
                draw_at=draw_at,
                quantity=quantity,
                ticket_id=render.seed_of(user.id, draw_at),
                owner=user.display_name,
            )
        else:
            state = render.slip_state(
                game,
                [pick for pick, _ in picks],
                draw_at=draw_at,
                owner=user.display_name,
                jackpot=jackpot,
                prizes={},
            )
        return await self._still(state)

    async def scratch(
        self, interaction: discord.Interaction, panel: LotteryPanel, game: Game
    ) -> None:
        """Vende un rasca y lo enseña rascándose; el resultado se escribe al acabar.

        El rasca ya estaba sorteado y casi siempre pintado (`LotteryPanel.prepare_scratches`):
        solo queda cobrarlo y subir el GIF. Sin pintor, sale como antes, con las casillas
        tapadas con spoilers.
        """
        guild, user = panel.guild, interaction.user
        await ack(interaction)
        plan = panel.take_scratch(game)
        prize = plan.prize
        gross = prize.amount if prize else 0
        tax = gravamen(gross) if gross else 0
        payouts = [LotteryPayout(user.id, gross, tax, game.key)] if gross else []
        try:
            _, balances = await self.economy.lottery(
                guild.id,
                charges=[(user.id, game.price, f"loteria:{game.key}")],
                payouts=payouts,
                hook=self.repository.record_scratch(
                    guild.id,
                    user.id,
                    game=game.key,
                    cost=game.price,
                    prize=gross,
                    tax=tax,
                    now=self.clock(),
                ),
            )
        except InsufficientFundsError as error:
            # El rasca sorteado vuelve a su sitio: sin pagar no se ve.
            panel.scratches[game.key] = plan
            panel.notice = (
                f"❌ El rasca cuesta {format_amount(game.price)} y tienes "
                f"{format_amount(error.balance)}."
            )
            panel.rebuild()
            await edit(interaction, **panel.edit_kwargs())
            return
        balance = balances[user.id]
        panel.scratch_serial += 1
        serial = panel.scratch_serial
        media = await self.scratch_media(plan) if not self.scene.disabled else None
        moment = Moment(Event.LOTTERY_WIN if prize is not None else Event.LOTTERY_LOSE)
        stats = scratch_stats(
            cost=game.price,
            prize=gross,
            tax=tax,
            top=prize is not None and game.scratch is not None and prize is game.scratch[0][0],
            balance_after=balance,
            near=plan.near,
            when=datetime.fromtimestamp(self.clock(), DRAW_TIMEZONE),
        )
        if media is None:
            lines = [scratch_text(game, prize, plan.cells, tax)]
            if hint := await renta.hint(self.bot, guild.id, user.id, moment):
                lines.append(hint)
            panel.notice = "\n".join(lines)
            panel.balance = balance
            panel.set_image(None)
            panel.rebuild()
            await edit(interaction, **panel.edit_kwargs())
            await self._after_scratch(interaction, stats)
            panel.prepare_scratches()
            return
        # El saldo de arriba aún no lleva el premio: lo diría antes que el GIF.
        panel.balance = balance - gross + tax
        panel.notice = f"🪙 Rascando un **{game.name}**…"
        panel.set_image((SCRATCH_GIF, media.gif))
        panel.rebuild()
        await edit(interaction, **panel.edit_kwargs())
        panel.prepare_scratches()
        self.later(
            self._reveal_scratch(
                interaction, panel, plan, serial, balance=balance, tax=tax, moment=moment,
                stats=stats, wait=media.seconds + REVEAL_MARGIN_SECONDS,
            )
        )  # fmt: skip

    async def _reveal_scratch(
        self,
        interaction: discord.Interaction,
        panel: LotteryPanel,
        plan: ScratchPlan,
        serial: int,
        *,
        balance: int,
        tax: int,
        moment: Moment,
        stats: StatDelta,
        wait: float,
    ) -> None:
        """Cuando acaba el GIF: el resultado, el saldo con el premio, la renta y los logros.

        Si entretanto se ha rascado otro, el panel ya enseña ese y no se toca; los
        logros y el aviso de la renta salen igual.
        """
        try:
            await asyncio.sleep(wait)
            if serial == panel.scratch_serial:
                pool = SCRATCH_WIN_LINES if plan.prize else SCRATCH_LOSE_LINES
                if plan.near:
                    pool = SCRATCH_NEAR_LINES
                lines = [
                    scratch_verdict(
                        plan.game, plan.prize, tax, near=plan.near, line=random.choice(pool)
                    )
                ]
                if hint := await renta.hint(self.bot, panel.guild.id, panel.owner.id, moment):
                    lines.append(hint)
                panel.notice = "\n".join(lines)
                panel.balance = balance
                panel.rebuild()
                with contextlib.suppress(discord.HTTPException):
                    await edit(interaction, **panel.edit_kwargs())
        finally:
            await self._after_scratch(interaction, stats)

    async def _after_scratch(self, interaction: discord.Interaction, stats: StatDelta) -> None:
        """Lo que va después de enseñar el rasca: el aviso de la renta y los logros."""
        await renta.remind(self.bot, interaction)
        assert interaction.guild is not None
        await logros.track(
            self.bot, interaction.guild.id, interaction.user, interaction.channel, stats
        )

    # -- Sorteos ---------------------------------------------------------------------

    @tasks.loop(minutes=1)
    async def _draws(self) -> None:
        try:
            await self.run_due_draws()
        except Exception:
            # Una excepción sin capturar pararía la tarea para siempre.
            logger.exception("Error celebrando sorteos de lotería")

    @_draws.before_loop
    async def _before_draws(self) -> None:
        await self.bot.wait_until_ready()

    async def run_due_draws(self) -> int:
        """Celebra todos los sorteos que ya tocan. Devuelve cuántos ha cerrado."""
        async with self._settling:
            done = 0
            for draw in await self.repository.due_draws(self.clock()):
                if await self.settle(draw):
                    done += 1
            return done

    async def settle(self, draw: Draw) -> bool:
        """Celebra un sorteo: saca las bolas, paga y lo anuncia.

        Returns:
            Si se ha cerrado (no lo hace si otro proceso ya lo cerró o si
            entró una apuesta a última hora; se reintenta en el siguiente minuto).
        """
        game = GAME_BY_KEY[draw.game]
        tickets = await self.repository.tickets(draw.id)
        members = self.members(draw.guild_id)
        if game.categories and members is not None:
            result = draw_result(
                game,
                self.rng,
                tickets=[(Pick.decode(t.pick), t.quantity) for t in tickets],
                odds=server_odds(game, members),
            )
        else:
            result = draw_result(game, self.rng)
        carry: int | None = None
        jackpot_won = False
        next_jackpot = 0
        if game.kind is Kind.NACIONAL:
            settlement = settle_nacional(
                game,
                tickets=[(t.id, Pick.decode(t.pick).numbers[0], t.quantity) for t in tickets],
                result=result,
            )
        else:
            pot = (await self.pots(draw.guild_id))[game.key]
            guarantee = guarantee_for(
                game,
                await self.economy.state_balance(draw.guild_id),
                (draw.draw_at - pot.since) / 86_400,
            )
            settlement = settle_pool(
                game,
                sales=draw.sales,
                carry=pot.carry,
                guarantee=guarantee,
                tickets=[(t.id, Pick.decode(t.pick)) for t in tickets],
                result=result,
                members=members,
            )
            carry = settlement.carry
            jackpot_won = any(
                c.winners for c in settlement.categories if c.key == jackpot_key(game)
            )
            # Lo que se anunciará para el próximo sorteo: si se acertó, el mínimo vuelve a
            # empezar; si no, sigue subiendo.
            since = draw.draw_at if jackpot_won else pot.since
            next_jackpot = max(
                settlement.carry,
                guarantee_for(
                    game,
                    await self.economy.state_balance(draw.guild_id),
                    (next_draw(game, draw.draw_at) - since) / 86_400,
                ),
            )
        by_id = {t.id: t for t in tickets}
        prizes: dict[int, tuple[int, int, str]] = {}
        totals: dict[int, list[int]] = defaultdict(lambda: [0, 0])
        for ticket_id, gross in settlement.prizes.items():
            ticket = by_id[ticket_id]
            # El gravamen se calcula por décimo o apuesta, que es como exime la ley.
            tax = gravamen(gross // ticket.quantity, ticket.quantity)
            prizes[ticket_id] = (gross, tax, ", ".join(settlement.labels[ticket_id]))
            totals[ticket.user_id][0] += gross
            totals[ticket.user_id][1] += tax
        payouts = [
            LotteryPayout(user_id, gross, tax, game.key)
            for user_id, (gross, tax) in sorted(totals.items())
        ]
        summary = {
            "categories": [[c.key, c.winners, c.prize] for c in settlement.categories],
            "carry": settlement.carry,
            "topup": settlement.topup,
            "sales": draw.sales,
            "paid": settlement.paid,
            "jackpot": settlement.jackpot,
        }
        try:
            await self.economy.lottery(
                draw.guild_id,
                payouts=payouts,
                hook=self.repository.close(
                    draw,
                    result=result,
                    summary=summary,
                    prizes=prizes,
                    carry=carry,
                    now=self.clock(),
                    jackpot_won=jackpot_won,
                ),
            )
        except DrawChanged:
            logger.info("El sorteo %s cambió mientras se repartía; se reintenta", draw.id)
            return False
        await self.announce(
            draw,
            game,
            result,
            settlement,
            tickets_by_id=by_id,
            prizes=prizes,
            next_jackpot=next_jackpot,
        )
        return True

    async def announce(
        self,
        draw: Draw,
        game: Game,
        result: dict,
        settlement: Settlement,
        *,
        tickets_by_id: dict,
        prizes: dict[int, tuple[int, int, str]],
        next_jackpot: int = 0,
    ) -> None:
        """Anuncia el sorteo en su canal y apunta los logros. Nunca lanza.

        Con pintor, primero sale el GIF de las bolas con solo el título y, cuando
        acaba, el mensaje cambia al resultado con los premiados y la imagen quieta:
        el texto no destripa la animación. Los logros, también después.
        """
        guild = self.bot.get_guild(draw.guild_id)
        channel = self.bot.get_channel(draw.channel_id) if draw.channel_id else None
        if guild is None:
            return
        by_user: dict[int, list[tuple[int, int, frozenset[str]]]] = defaultdict(list)
        for ticket_id, (gross, tax, _) in prizes.items():
            ticket = tickets_by_id[ticket_id]
            by_user[ticket.user_id].append(
                (gross, tax, ticket_tags(game, settlement.labels[ticket_id]))
            )
        message: discord.Message | None = None
        media: Media | None = None
        if isinstance(channel, discord.abc.Messageable):
            text = self.result_text(
                guild, draw, game, result, settlement, prizes, tickets_by_id, next_jackpot
            )
            meta, states, durations = self.draw_animation(
                draw, game, result, settlement, by_user, next_jackpot
            )
            media = await self._animate(meta, states, durations)
            none = discord.AllowedMentions.none()
            try:
                if media is None:
                    await channel.send(text[:2000], allowed_mentions=none)
                else:
                    heading = (
                        f"## {game.emoji} Sorteo · {draw_label(game, draw.draw_at)}\n"
                        "-# Salen las bolas…"
                    )
                    gif = discord.File(io.BytesIO(media.gif), filename=DRAW_GIF)
                    message = await channel.send(heading, file=gif, allowed_mentions=none)
            except discord.HTTPException:
                logger.warning("No se pudo anunciar el sorteo %s", draw.id, exc_info=True)
        if message is not None and media is not None:
            self.later(self._reveal_draw(message, text, media, guild, channel, by_user))
        else:
            await self._track_prizes(guild, channel, by_user)

    def draw_animation(
        self,
        draw: Draw,
        game: Game,
        result: dict,
        settlement: Settlement,
        by_user: dict[int, list[tuple[int, int, frozenset[str]]]],
        next_jackpot: int,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[int]]:
        """La animación del sorteo con su cartel: el bote o los premiados del servidor."""
        if game.kind is Kind.NACIONAL:
            paid = sum(gross for rows in by_user.values() for gross, _, _ in rows)
            banner = render.nacional_banner(winners=len(by_user), paid=paid)
            return render.nacional_draw(game, result, draw_at=draw.draw_at, banner=banner)
        won = next(c for c in settlement.categories if c.key == jackpot_key(game)).winners
        banner = render.draw_banner(
            won=won,
            jackpot=settlement.jackpot,
            next_jackpot=next_jackpot,
            next_draw_at=next_draw(game, draw.draw_at),
            winners=len(by_user),
        )
        return render.pool_draw(game, result, draw_at=draw.draw_at, banner=banner)

    async def _reveal_draw(
        self,
        message: discord.Message,
        text: str,
        media: Media,
        guild: discord.Guild,
        channel: object,
        by_user: dict[int, list[tuple[int, int, frozenset[str]]]],
    ) -> None:
        """Cuando acaban las bolas: el resultado escrito, la imagen quieta y los logros."""
        try:
            await asyncio.sleep(media.seconds + DRAW_REVEAL_MARGIN_SECONDS)
            png = discord.File(io.BytesIO(media.png), filename=DRAW_PNG)
            try:
                await message.edit(
                    content=text[:2000],
                    attachments=[png],
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                logger.warning("No se pudo escribir el resultado del sorteo", exc_info=True)
        finally:
            await self._track_prizes(guild, channel, by_user)

    async def _track_prizes(
        self,
        guild: discord.Guild,
        channel: object,
        by_user: dict[int, list[tuple[int, int, frozenset[str]]]],
    ) -> None:
        for user_id, rows in by_user.items():
            member = guild.get_member(user_id)
            if member is not None:
                await logros.track(self.bot, guild.id, member, channel, lottery_prize_stats(rows))

    def result_text(
        self,
        guild: discord.Guild,
        draw: Draw,
        game: Game,
        result: dict,
        settlement: Settlement,
        prizes: dict[int, tuple[int, int, str]],
        tickets_by_id: dict,
        next_jackpot: int = 0,
    ) -> str:
        """Texto público con el resultado de un sorteo."""
        lines = [f"## {game.emoji} Sorteo · {draw_label(game, draw.draw_at)}"]
        lines.append(format_result(game, result))
        # Un renglón por persona: su total y su mejor boleto, para no llenar el canal.
        per_user: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for ticket_id, (gross, _tax, _detail) in prizes.items():
            per_user[tickets_by_id[ticket_id].user_id].append((gross, ticket_id))
        ranking = sorted(per_user.items(), key=lambda item: -sum(g for g, _ in item[1]))
        if ranking:
            lines.append("**Premiados**")
            for user_id, won in ranking[:ANNOUNCE_WINNERS]:
                member = guild.get_member(user_id)
                who = _name(member) if member else f"<@{user_id}>"
                best_gross, best_id = max(won)
                pick = format_pick(game, Pick.decode(tickets_by_id[best_id].pick))
                best = f"`{pick}` · {format_amount(best_gross)} ({prizes[best_id][2]})"
                if len(won) == 1:
                    lines.append(f"🎉 **{who}** · {best}")
                else:
                    total = sum(g for g, _ in won)
                    lines.append(
                        f"🎉 **{who}** · {len(won)} boletos, {format_amount(total)} · mejor: {best}"
                    )
            if len(ranking) > ANNOUNCE_WINNERS:
                lines.append(f"…y {len(ranking) - ANNOUNCE_WINNERS} premiados más.")
        else:
            lines.append(
                "Esta vez no le ha tocado a nadie del servidor. El próximo sorteo es el bueno 🍀"
            )
        if game.categories:
            jackpot = next(c for c in game.categories if c.jackpot)
            won = next(c for c in settlement.categories if c.key == jackpot.key).winners
            following = next_draw(game, draw.draw_at)
            if won:
                lines.append(
                    f"💥 **¡BOTE!** {format_amount(settlement.jackpot)} para {won} "
                    f"acertante{'s' if won != 1 else ''} de la {jackpot.label}."
                )
            else:
                lines.append(
                    f"💰 Bote sin dueño: el sorteo de <t:{int(following)}:R> tiene "
                    f"**{format_amount(max(next_jackpot, settlement.carry))}** y sigue subiendo."
                )
        taxed = sum(tax for _, tax, _ in prizes.values())
        footer = (
            f"-# Vendido: {format_amount(draw.sales)} · En premios: "
            f"{format_amount(settlement.paid)}"
        )
        if taxed:
            footer += f" · {TAX_COLLECTOR} se queda {format_amount(taxed)} de gravamen especial"
        lines.append(footer)
        return "\n".join(lines)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra las loterías del servidor que el bot abandona."""
        await self.repository.delete_guild_data(guild.id)

    # -- Comandos --------------------------------------------------------------------

    @app_commands.command(
        name="loteria",
        description="Nacional, Primitiva, Bonoloto, Gordo, Euromillones y rascas de la ONCE.",
    )
    @app_commands.guild_only()
    async def loteria(self, interaction: discord.Interaction) -> None:
        """Abre el panel de loterías, efímero (solo lo ve quien lo abre)."""
        assert interaction.guild is not None  # guild_only
        channel = interaction.channel
        if error := casino_channel_error(self.casino_channel_ids, channel, "La lotería"):
            await interaction.response.send_message(error, ephemeral=True)
            return
        await ack(interaction, new_message=True)
        panel = await self.open_panel(interaction.guild, interaction.user)
        await edit(
            interaction, **panel.edit_kwargs(), allowed_mentions=discord.AllowedMentions.none()
        )
        panel.interaction = interaction

    @commands.command(name="loteria")
    @commands.guild_only()
    async def loteria_text(self, ctx: commands.Context) -> None:
        """Versión de texto: el panel es público y cada uno juega en el suyo."""
        assert ctx.guild is not None  # guild_only
        if error := casino_channel_error(self.casino_channel_ids, ctx.channel, "La lotería"):
            await send_notice(ctx, error)
            return
        panel = await self.open_panel(ctx.guild, ctx.author)
        panel.message = await ctx.send(
            **panel.send_kwargs(), allowed_mentions=discord.AllowedMentions.none()
        )


async def pending(bot: commands.Bot, guild_id: int, user_id: int) -> list[Pending]:
    """Los boletos de alguien que esperan sorteo (para `saldo`), uno por sorteo, o `[]`."""
    cog = find_cog(bot, Loteria)
    if cog is None:
        return []
    try:
        records = await cog.repository.history(guild_id, user_id, limit=PENDING_TICKETS)
    except (OSError, sqlite3.Error):
        logger.exception("No se pudieron leer los boletos de %s", user_id)
        return []
    draws: dict[tuple[str, float], tuple[int, int]] = {}
    for record in records:
        if record.status == "open":
            count, cost = draws.get((record.game, record.draw_at), (0, 0))
            draws[(record.game, record.draw_at)] = (count + record.quantity, cost + record.cost)
    items = []
    for (key, draw_at), (count, cost) in draws.items():
        game = GAME_BY_KEY.get(key)
        name = f"{game.emoji} {game.name}" if game is not None else key
        unit = "boleto" if count == 1 else "boletos"
        items.append(
            Pending(
                Side.COLLECT, "lottery", "🎟️",
                f"{count} {unit} de {name} por sortear ({format_amount(cost)} jugados)",
                0, draw_at,
            )
        )  # fmt: skip
    return items


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía y las loterías del bot."""
    await bot.add_cog(
        Loteria(bot, bot.economy, bot.lottery, casino_channel_ids=bot.casino_channel_ids)
    )
