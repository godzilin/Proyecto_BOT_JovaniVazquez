"""Impuesto sobre el Patrimonio: cada lunes, Perro Sanxe cobra a quien tenga mucho.

Una tarea cada 10 minutos mira si la semana anterior ya se cobró en cada
servidor; si no, cobra la cuota (`bot.services.taxes.wealth_quote`) a todos los
miembros cuyo patrimonio (efectivo, los inmuebles de `pisos` a su valor fiscal y
los objetos de la tienda) pase del mínimo exento, con el límite conjunto con el
IRPF de la semana (art. 31 de la Ley 19/1991), y lo anuncia en `#chat-general` (o
en el canal del sistema). Quien pasa de 37.000.000 Y$ declara también el Impuesto
de Solidaridad de las Grandes Fortunas, que en Canarias sale a cero.
La primera semana tras activarlo en un servidor no se cobra, para que nadie pague
por sorpresa al desplegar.

`saldo` avisa de lo que te tocaría pagar y `hacienda` suma lo recaudado. No
es una interacción, así que el aviso de la Renta no aplica; el anuncio
público hace de recordatorio. Logros: `wealth_tax_paid`, `wealth_tax_weeks`,
`wealth_embargo`, `wealth_joint_limit`, `wealth_goods_taxed` y
`wealth_solidarity` (categoría Economía y Hacienda).

Consultas (no mueven dinero; ver `bot.services.net_worth`):

- `patrimonio` y la sección 🏰 Patrimonio de `perfil` (`patrimonio_embed`): todos
  los activos de alguien (efectivo, pisos, bienes de la tienda, boletos de lotería
  pendientes y la renta por cobrar), cada inmueble con la parte que tiene según sus
  participaciones (`home_lines`), con su valor, su puesto y el Patrimonio que le
  tocaría pagar el lunes (`quote`, la misma cuenta que usa `saldo` con
  `wealth_due`).
- `fortunas` (`FortunasPanel`): el patrimonio de todo el servidor, de qué es y cómo
  de repartido está (Gini y lo que tiene el más rico), el podio con su avatar y el
  desglose de cada uno, la lista por páginas de 10 con el título que luce cada uno
  (`shop.title_of`) y tu puesto, con ◀ ▶ y 📍 Yo.

Logros de las dos: `patrimonio_stats`, y de los botones de `fortunas`,
`fortunas_click_stats` (Economía y Hacienda).

El 🎰 Casino y el 🏦 Banco salen en `fortunas` como dos fortunas más, en su puesto y con
medalla si les toca (`houses`, cuentas en `bot.services.house_books`): lo que gana la casa y
el beneficio del banco desde el primer día, menos lo que han pagado a Hacienda. El puesto
de cada miembro cuenta con ellos, y `fortunas` y `perfil` dicen cuánto le falta (o le saca)
a cada uno; los logros de superarlos salen de `house_rivals_stats`. Cada lunes, con el
Patrimonio, pagan su impuesto (`EconomyService.charge_house_taxes`): el casino la tasa del
juego y el banco el Impuesto sobre Sociedades, y el anuncio lo cuenta.

Permisos: enviar mensajes e insertar enlaces en el canal del anuncio.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks

from bot.cogs import achievements as logros
from bot.cogs import business as negocio
from bot.cogs import pisos
from bot.cogs import shop as tienda
from bot.repositories.business import companies_in
from bot.repositories.economy import BANK_ACCOUNT_ID, CASINO_ACCOUNT_ID
from bot.repositories.housing import assets_in as housing_assets_in
from bot.repositories.shop import wealth_goods_in
from bot.services import housing as rules
from bot.services.achievements import (
    StatDelta,
    fortunas_click_stats,
    house_rivals_stats,
    patrimonio_stats,
)
from bot.services.economy import (
    EconomyService,
    WealthCharge,
    format_amount,
    week_label,
    week_start,
)
from bot.services.house_books import (
    BANK,
    CASINO,
    BankBook,
    CasinoBook,
    HouseCharge,
    rate_of,
)
from bot.services.house_books import (
    NAMES as HOUSE_NAMES,
)
from bot.services.house_books import (
    TAX_NAMES as HOUSE_TAX_NAMES,
)
from bot.services.housing_catalog import BARRIO_BY_KEY, VPO_BY_KEY
from bot.services.levels import local_day
from bot.services.net_worth import (
    NetWorth,
    build,
    gini,
    holding_line,
    home_line,
    top_share,
    verdict,
)
from bot.services.shop import Kind
from bot.services.taxes import (
    SOLIDARITY_WEALTH_THRESHOLD,
    TAX_COLLECTOR,
    WEALTH_MAX_REDUCTION,
    WEALTH_MINIMUM,
    WealthQuote,
    wealth_quote,
)
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient
    from bot.repositories.housing import HousingRepository
    from bot.repositories.lottery import LotteryRepository
    from bot.repositories.shop import ShopRepository

logger = logging.getLogger(__name__)

ANNOUNCE_CHANNEL_NAME = "chat-general"
#: Cuántos contribuyentes se nombran en el anuncio.
ANNOUNCE_TOP = 5
COLOR = discord.Color.from_rgb(170, 21, 27)
#: Miembros por página en `fortunas` (en la primera, los 3 del podio y 7 más).
FORTUNES_PAGE = 10
#: Segundos que el panel de `fortunas` atiende sus botones.
FORTUNES_TIMEOUT = 600
_NO_MENTIONS = discord.AllowedMentions.none()


_MEDALS = ("🥇", "🥈", "🥉")


def _decimal(value: float, digits: int) -> str:
    """`0.4321, 2` → `0,43`."""
    return f"{value:.{digits}f}".replace(".", ",")


def wealth_embed(
    week: date, charges: tuple[WealthCharge, ...], names: dict[int, str]
) -> discord.Embed:
    """Anuncio público del cobro semanal del Patrimonio."""
    total = sum(c.tax + c.debt for c in charges)
    lines = [
        f"{_MEDALS[i] if i < len(_MEDALS) else '▫️'} **{names[c.user_id]}** paga "
        f"{format_amount(c.tax)} (tenía {format_amount(c.balance)})"
        + (
            f" y le embargan {format_amount(c.debt)} de sus alquileres: todo en ladrillo"
            if c.debt
            else ""
        )
        + (
            f". El límite con el IRPF le perdona {format_amount(c.reduced)}: rico de rentas bajas"
            if c.reduced
            else ""
        )
        for i, c in enumerate(charges[:ANNOUNCE_TOP])
    ]
    if len(charges) > ANNOUNCE_TOP:
        lines.append(f"…y {len(charges) - ANNOUNCE_TOP} más.")
    if fortunes := [c for c in charges if c.solidarity_subject]:
        paid = sum(c.solidarity for c in fortunes)
        who = "declara" if len(fortunes) == 1 else "declaran"
        lines.append(
            f"\n💎 {len(fortunes)} {who} el Impuesto de Solidaridad de las Grandes Fortunas "
            f"y paga{'n' if len(fortunes) > 1 else ''} {format_amount(paid)}: se descuenta el "
            "Patrimonio y Canarias no lo bonifica. Montero lo inventó para Madrid."
        )
    who = "rico paga" if len(charges) == 1 else "ricos pagan"
    embed = discord.Embed(
        title=f"🐶 {TAX_COLLECTOR} pasó por el Patrimonio",
        description=(
            f"Semana {week_label(week)}: {len(charges)} {who} "
            f"**{format_amount(total)}** en total.\n\n" + "\n".join(lines)
        ),
        color=COLOR,
    )
    embed.set_footer(
        text=(
            f"Paga cada lunes por lo que tengas (efectivo, pisos y objetos de la tienda) por "
            f"encima de {format_amount(WEALTH_MINIMUM)}, con el límite del 60 % de tu renta. "
            f"El ladrillo también se lo come {TAX_COLLECTOR}."
        )
    )
    return embed


def _share(part: int, whole: int) -> str:
    """`part` en tanto por ciento entero de `whole`, o «<1 %» si hay algo pero no llega."""
    percent = part * 100 / whole if whole else 0.0
    return "<1 %" if 0 < percent < 1 else f"{percent:.0f} %"


def _breakdown(worth: NetWorth) -> str:
    """De qué es el patrimonio de alguien, en una línea de subtexto."""
    return (
        f"-# 💵 {format_amount(worth.cash)}"
        + (f" · 🛍️ {format_amount(worth.goods_value)}" if worth.goods_value else "")
        + (f" · 🏠 {format_amount(worth.homes)}" if worth.homes else "")
        + (f" · 🏢 {format_amount(worth.companies)}" if worth.companies else "")
        + (f" · 🎟️ {format_amount(worth.lottery)}" if worth.lottery else "")
        + (f" · 📬 {format_amount(worth.renta)}" if worth.renta else "")
        + (f" · 🏦 −{format_amount(worth.debts)}" if worth.debts > 0 else "")
        + (f" · 🤝 {format_amount(-worth.debts)}" if worth.debts < 0 else "")
    )


#: Ids del Casino y del Banco en las listas de `fortunas`.
HOUSE_IDS = (CASINO_ACCOUNT_ID, BANK_ACCOUNT_ID)


def is_house(user_id: int) -> bool:
    """Si un id de `fortunas` es el Casino o el Banco, no un miembro."""
    return user_id in HOUSE_IDS


def house_worths(casino: CasinoBook, bank: BankBook) -> tuple[NetWorth, NetWorth]:
    """El Casino y el Banco como dos `NetWorth` más, para ponerlos en la lista.

    El casino lo tiene todo en caja. El banco tiene su caja (lo cobrado menos lo prestado,
    casi siempre negativa) y lo que le deben, que va en `debts` en negativo: así su total
    es la caja más lo que le deben, igual que el de un avalista al que le deben dinero.
    """
    return (
        NetWorth(user_id=CASINO_ACCOUNT_ID, cash=casino.total),
        NetWorth(user_id=BANK_ACCOUNT_ID, cash=bank.cash - bank.taxes, debts=-bank.owed),
    )


def casino_line(book: CasinoBook) -> str:
    """De qué es la fortuna del casino, en una línea de subtexto."""
    return (
        f"-# 🎲 apostado {format_amount(book.staked)} · 💸 pagado en premios "
        f"{format_amount(book.paid)}"
        + (f" · 🐶 −{format_amount(book.taxes)}" if book.taxes else "")
    )


def bank_line(book: BankBook) -> str:
    """De qué es la fortuna del banco, en una línea de subtexto."""
    cash = book.cash - book.taxes
    return (
        f"-# 💵 caja {'−' if cash < 0 else ''}{format_amount(abs(cash))} · 🤝 le deben "
        f"{format_amount(book.owed)}"
        + (f" · 🐶 −{format_amount(book.taxes)}" if book.taxes else "")
    )


def rival_part(name: str, mine: int, theirs: int) -> str:
    """Cómo está alguien frente al casino o al banco: «🎰 El Casino: te faltan 300 Y$»."""
    if theirs <= 0:
        return f"{name}: en números rojos"
    if mine > theirs:
        return f"{name}: le sacas {format_amount(mine - theirs)}"
    if mine == theirs:
        return f"{name}: empatas"
    return f"{name}: te faltan {format_amount(theirs - mine)}"


def rivals_line(everyone: tuple[NetWorth, ...] | list[NetWorth], user_id: int) -> str:
    """Una línea con lo que separa a `user_id` del Casino y del Banco (vacía sin ellos)."""
    mine = next((w.total for w in everyone if w.user_id == user_id), 0)
    return " · ".join(
        rival_part(HOUSE_NAMES[key], mine, worth.total)
        for key, account in ((CASINO, CASINO_ACCOUNT_ID), (BANK, BANK_ACCOUNT_ID))
        if (worth := next((w for w in everyone if w.user_id == account), None)) is not None
    )


def house_tax_embed(week: date, charges: list[HouseCharge]) -> discord.Embed:
    """Anuncio de lo que han pagado el Casino y el Banco el lunes."""
    lines = []
    for charge in charges:
        name = HOUSE_NAMES[charge.entity]
        tax = HOUSE_TAX_NAMES[charge.entity]
        rate = f"{rate_of(charge.entity) * 100:.0f} %"
        if charge.tax:
            lines.append(
                f"{name} paga **{format_amount(charge.tax)}** de {tax} ({rate} de "
                f"{format_amount(charge.base)} ganados por encima de lo que ya tributó)"
            )
        else:
            lines.append(f"{name} no paga {tax}: no ha ganado nada por encima de lo que ya tributó")
    return discord.Embed(
        title=f"🐶 {TAX_COLLECTOR} también pasó por la banca",
        description=f"Semana {week_label(week)}.\n\n" + "\n".join(lines),
        color=COLOR,
    ).set_footer(
        text="El casino paga la tasa del juego sobre lo que gana la casa y el banco, "
        "Sociedades sobre su beneficio. Las semanas en pérdidas se compensan después."
    )


def fortunes_summary(ranked: list[NetWorth]) -> str:
    """Cabecera de `fortunas`: el patrimonio de todos, de qué es y cómo de repartido está.

    Los tantos por ciento son de lo que tienen (sin restar deudas), del más grande al menor.
    Solo cuentan los miembros: el Casino y el Banco salen en la lista, pero no en el total.
    """
    ranked = [w for w in ranked if not is_house(w.user_id)]
    totals = [w.total for w in ranked]
    assets = sum(w.assets for w in ranked)
    debts = sum(w.debts for w in ranked)
    parts = sorted(
        (
            (sum(w.homes for w in ranked), "🏠", "pisos"),
            (sum(w.cash for w in ranked), "💵", "efectivo"),
            (sum(w.companies for w in ranked), "🏢", "sociedades"),
            (sum(w.goods_value for w in ranked), "🛍️", "tienda"),
            (sum(w.lottery + w.renta for w in ranked), "🎟️", "boletos y renta"),
        ),
        key=lambda part: -part[0],
    )
    value = gini(totals)
    filled = round(value * 10)
    return "\n".join(
        [
            f"## {format_amount(sum(totals))}",
            " · ".join(
                f"{emoji} {_share(part, assets)} {name}" for part, emoji, name in parts if part
            ),
            *([f"🏦 −{format_amount(debts)} de deudas"] if debts > 0 else []),
            f"{'▰' * filled}{'▱' * (10 - filled)} Gini **{_decimal(value, 2)}** · el más rico "
            f"tiene el {_decimal(top_share(totals, 0) * 100, 0)} % · el 10 % más rico, el "
            f"{_decimal(top_share(totals, 0.1) * 100, 0)} %",
        ]
    )


def you_line(everyone: tuple[NetWorth, ...], user_id: int) -> str:
    """El puesto de `user_id` en `fortunas` y lo que le separa del de arriba (o del 2.º)."""
    ranked = [w for w in everyone if w.total > 0]
    rank = next((i for i, w in enumerate(ranked) if w.user_id == user_id), None)
    if rank is None:
        total = next((w.total for w in everyone if w.user_id == user_id), 0)
        return f"📍 Tú: no sales en la lista (patrimonio neto de {format_amount(total)})"
    me = ranked[rank]
    line = f"📍 Tú: puesto {rank + 1} de {len(ranked)} · **{format_amount(me.total)}**"
    if rank == 0:
        if len(ranked) > 1:
            line += f" · le sacas {format_amount(me.total - ranked[1].total)} al 2.º"
        return line
    gap = ranked[rank - 1].total - me.total
    if gap == 0:
        return f"{line} · empatas con el {rank}.º"
    return f"{line} · te faltan {format_amount(gap)} para el {rank}.º"


def you_lines(everyone: tuple[NetWorth, ...], user_id: int) -> str:
    """`you_line` y, debajo, lo que le separa del Casino y del Banco."""
    rivals = rivals_line(everyone, user_id)
    return you_line(everyone, user_id) + (f"\n🎯 {rivals}" if rivals else "")


@dataclass(frozen=True, slots=True)
class Fortunes:
    """Lo que enseña `fortunas`, leído una vez al abrirlo.

    Attributes:
        guild_id: Servidor.
        everyone: El patrimonio de cada miembro, del más rico al menos (también los que no
            tienen nada o deben más de lo que tienen, para la línea de «Tú»).
        names: El nombre de cada uno de la lista, con su título.
        avatars: El avatar de los del podio que siguen en el servidor.
        read_at: Cuándo se leyeron las cifras.
        houses: La línea de desglose del Casino y del Banco (`HOUSE_IDS`), si están en
            `everyone`.
    """

    guild_id: int
    everyone: tuple[NetWorth, ...]
    names: dict[int, str]
    avatars: dict[int, str]
    read_at: float
    houses: dict[int, str] = field(default_factory=dict)

    @property
    def members(self) -> int:
        """Cuántos miembros salen en la lista (sin el Casino ni el Banco)."""
        return sum(1 for w in self.ranked if not is_house(w.user_id))

    @property
    def ranked(self) -> list[NetWorth]:
        """Los que salen en la lista: patrimonio neto positivo."""
        return [w for w in self.everyone if w.total > 0]


class FortunasPanel(ui.LayoutView):
    """`fortunas`: el total del servidor, el podio, la lista por páginas y tu puesto.

    Las cifras se leen una vez al abrir (`Fortunes`); pasar de página solo cambia memoria,
    así que se contesta directamente con `edit_message`. Quien no lo abrió y pulsa un botón
    recibe su propia copia en privado, con las mismas cifras, y el panel de quien lo abrió
    no se mueve (como el de `hacienda`).

    Args:
        bot: Para los logros de los botones.
        fortunes: Las cifras.
        owner: Quien lo abrió: el único que lo mueve y de quien habla «Tú».
        page: Página de inicio, desde 0.
    """

    def __init__(
        self, bot: commands.Bot, fortunes: Fortunes, owner: discord.abc.User, *, page: int = 0
    ) -> None:
        super().__init__(timeout=FORTUNES_TIMEOUT)
        self.bot = bot
        self.fortunes = fortunes
        self.owner = owner
        self.pages = max(1, -(-len(fortunes.ranked) // FORTUNES_PAGE))
        self.page = min(max(page, 0), self.pages - 1)
        self.message: discord.Message | None = None
        self.interaction: discord.Interaction | None = None
        self.rebuild()

    def rebuild(self) -> None:
        """Monta la página actual."""
        self.clear_items()
        ranked = self.fortunes.ranked
        box = ui.Container(accent_colour=COLOR)
        box.add_item(
            ui.TextDisplay(
                f"# 💎 Fortunas del servidor\n-# {self.fortunes.members} con patrimonio"
                + (" y la banca" if self.fortunes.houses else "")
                + f" · cifras de <t:{int(self.fortunes.read_at)}:R>"
            )
        )
        box.add_item(ui.Separator())
        if not ranked:
            box.add_item(ui.TextDisplay("Nadie tiene nada: ni un Y$ en todo el servidor."))
            self.add_item(box)
            return
        box.add_item(ui.TextDisplay(fortunes_summary(ranked)))
        box.add_item(ui.Separator())
        start = self.page * FORTUNES_PAGE
        lines = []
        for rank, worth in enumerate(ranked[start : start + FORTUNES_PAGE], start + 1):
            name = self.fortunes.names.get(worth.user_id, f"<@{worth.user_id}>")
            you = " 📍" if worth.user_id == self.owner.id else ""
            if rank > len(_MEDALS):
                lines.append(f"`{rank}.`{you} {name} · **{format_amount(worth.total)}**")
                continue
            text = (
                f"{_MEDALS[rank - 1]}{you} {name}\n**{format_amount(worth.total)}**\n"
                + self.fortunes.houses.get(worth.user_id, _breakdown(worth))
            )
            avatar = self.fortunes.avatars.get(worth.user_id)
            box.add_item(
                ui.Section(text, accessory=ui.Thumbnail(avatar)) if avatar else ui.TextDisplay(text)
            )
        if lines:
            if self.page == 0:
                box.add_item(ui.Separator())
            box.add_item(ui.TextDisplay("\n".join(lines)))
        box.add_item(ui.Separator())
        box.add_item(
            ui.TextDisplay(
                you_lines(self.fortunes.everyone, self.owner.id)
                + "\n-# Efectivo, pisos, sociedades, bienes de la tienda, boletos por sortear "
                "y renta por cobrar, menos lo que se debe. El casino cuenta lo que gana la casa "
                "y el banco, su caja más lo que le deben, menos sus impuestos. Gini (solo "
                "miembros): 0 si todos tienen lo mismo, 1 si uno lo tiene todo."
            )
        )
        if self.pages > 1:
            row: ui.ActionRow = ui.ActionRow()
            for key, label, emoji, disabled in (
                ("anterior", "◀", None, self.page == 0),
                ("pagina", f"{self.page + 1}/{self.pages}", None, True),
                ("siguiente", "▶", None, self.page == self.pages - 1),
                ("yo", "Yo", "📍", False),
            ):
                button: ui.Button = ui.Button(
                    label=label,
                    emoji=emoji,
                    style=discord.ButtonStyle.secondary,
                    disabled=disabled,
                    custom_id=f"fortunas:{key}",
                )
                button.callback = self._go(key)  # type: ignore[method-assign]
                row.add_item(button)
            box.add_item(row)
        self.add_item(box)

    def _go(self, key: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            user = interaction.user
            ranked = self.fortunes.ranked
            rank = next((i for i, w in enumerate(ranked, 1) if w.user_id == user.id), 0)
            if key == "yo":
                # Quien no sale en la lista está por debajo de todos: la última página.
                page = ((rank or len(ranked)) - 1) // FORTUNES_PAGE
            else:
                page = self.page + (1 if key == "siguiente" else -1)
            if user.id == self.owner.id:
                panel = self
                panel.page = min(max(page, 0), self.pages - 1)
                panel.rebuild()
                await interaction.response.edit_message(view=panel, allowed_mentions=_NO_MENTIONS)
            else:
                panel = FortunasPanel(self.bot, self.fortunes, user, page=page)
                await interaction.response.send_message(
                    view=panel, ephemeral=True, allowed_mentions=_NO_MENTIONS
                )
                panel.interaction = interaction
            if user.bot:
                return
            await logros.track(
                self.bot,
                self.fortunes.guild_id,
                user,
                interaction.channel,
                fortunas_click_stats(
                    turned=key != "yo",
                    last_page=panel.pages > 1 and panel.page == panel.pages - 1,
                    me=key == "yo",
                    rank=rank,
                    borrowed=panel is not self,
                ),
            )

        return callback

    async def on_timeout(self) -> None:
        """Apaga los botones al caducar."""
        for child in self.walk_children():
            if isinstance(child, ui.Button):
                child.disabled = True
        try:
            if self.interaction is not None:
                await self.interaction.edit_original_response(view=self)
            elif self.message is not None:
                await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar un panel de fortunas", exc_info=True)


class Patrimonio(commands.Cog):
    """Cobra el Impuesto sobre el Patrimonio cada semana y lo anuncia."""

    def __init__(
        self,
        bot: commands.Bot,
        economy: EconomyService,
        *,
        shop: ShopRepository | None = None,
        lottery: LotteryRepository | None = None,
        housing: HousingRepository | None = None,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.shop = shop
        self.lottery = lottery
        self.housing = housing
        #: `(servidor, lunes)` ya revisados, para no tocar la base de datos cada
        #: 10 minutos durante toda la semana.
        self._done: set[tuple[int, date]] = set()

    async def cog_load(self) -> None:
        self._weekly.start()

    async def cog_unload(self) -> None:
        self._weekly.cancel()

    @tasks.loop(minutes=10)
    async def _weekly(self) -> None:
        for guild in self.bot.guilds:
            await self.run_guild(guild)

    @_weekly.before_loop
    async def _before_weekly(self) -> None:
        await self.bot.wait_until_ready()

    async def run_guild(self, guild: discord.Guild) -> None:
        """Cobra la semana cerrada en `guild` si falta, y la anuncia. Nunca lanza."""
        last_week = week_start(local_day(time.time())) - timedelta(days=7)
        if (guild.id, last_week) in self._done:
            return
        housing = self.housing

        def assets(connection: sqlite3.Connection) -> dict[int, int]:
            """Pisos a su valor fiscal y sociedades a su valor teórico, sumados."""
            out = housing_assets_in(connection, guild.id) if housing is not None else {}
            for user_id, value in companies_in(connection, guild.id).items():
                out[user_id] = out.get(user_id, 0) + value
            return out

        goods = (
            (lambda connection: wealth_goods_in(connection, guild.id, time.time()))
            if self.shop is not None
            else None
        )
        try:
            week, run = await self.economy.charge_wealth_tax(guild.id, assets=assets, goods=goods)
        except Exception:
            # Nunca lanza: si saliera la excepción, `tasks.loop` dejaría de cobrar el
            # Patrimonio para siempre (ahora lee también los pisos).
            logger.exception("No se pudo cobrar el Patrimonio en el servidor %s", guild.id)
            return
        houses = await self.charge_houses(guild.id, week)
        if houses is not None:
            # Si falla el de la banca, se vuelve a intentar en 10 minutos: el Patrimonio de
            # los miembros no se repite, porque `charge_wealth_tax` ya apuntó la semana.
            self._done.add((guild.id, week))
        embeds = []
        if run.charges:
            names = {}
            for charge in run.charges:
                member = guild.get_member(charge.user_id)
                names[charge.user_id] = (
                    discord.utils.escape_markdown(member.display_name)
                    if member is not None
                    else f"<@{charge.user_id}>"
                )
            embeds.append(wealth_embed(week, run.charges, names))
        if houses and any(charge.tax for charge in houses):
            embeds.append(house_tax_embed(week, houses))
        if not embeds:
            return
        channel = (
            discord.utils.get(guild.text_channels, name=ANNOUNCE_CHANNEL_NAME)
            or guild.system_channel
        )
        if channel is not None:
            try:
                await channel.send(
                    embeds=embeds,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.HTTPException):
                logger.warning("No se pudo anunciar el Patrimonio en %s", guild.id, exc_info=True)
        for charge in run.charges:
            logros.note(
                self.bot,
                guild.id,
                charge.user_id,
                StatDelta(
                    add={
                        "wealth_tax_paid": charge.tax,
                        "wealth_tax_weeks": 1,
                        **({"wealth_embargo": 1} if charge.debt else {}),
                        **({"wealth_joint_limit": 1} if charge.reduced else {}),
                        **({"wealth_goods_taxed": 1} if charge.goods else {}),
                        **({"wealth_solidarity": 1} if charge.solidarity_subject else {}),
                    }
                ),
                channel.id if channel is not None else None,
            )

    async def charge_houses(self, guild_id: int, week: date) -> list[HouseCharge] | None:
        """Cobra la semana al Casino y al Banco. `None` si falla (no lanza).

        Devuelve lo cobrado ahora (vacío si ya estaba o es la primera semana, que solo se
        apunta: el lunes siguiente pagan todo lo de antes).
        """
        try:
            lent, owed = await self.housing.bank_exposure(guild_id) if self.housing else (0, 0)
            return await self.economy.charge_house_taxes(
                guild_id, week=week, purchase_lent=lent, owed=owed
            )
        except Exception:
            logger.exception("No se pudo cobrar a la banca en el servidor %s", guild_id)
            return None

    # -- Patrimonio y `fortunas` --------------------------------------------------------

    async def fortunes(self, guild_id: int) -> list[NetWorth]:
        """Patrimonio de todos los miembros, del más rico al menos."""
        now = time.time()
        balances = await self.economy.member_balances(guild_id)
        holdings = await self.shop.holdings(guild_id, None, now) if self.shop else []
        lottery = await self.lottery.open_stakes(guild_id) if self.lottery else {}
        homes, homes_fiscal = (
            await self.housing.member_values(guild_id) if self.housing else ({}, {})
        )
        debts = await self.housing.member_debts(guild_id) if self.housing else {}
        companies = await negocio.company_values(self.bot, guild_id)
        users = (
            set(balances) | {row[0] for row in holdings} | set(lottery) | set(homes)
            | set(companies)
        )  # fmt: skip
        renta = {}
        for user_id in users:
            pending = await self.economy.pending_declarations(guild_id, user_id)
            if total := sum(d.refund for d in pending):
                renta[user_id] = total
        return build(balances, holdings, lottery, renta, homes, homes_fiscal, debts, companies)

    @staticmethod
    def _name(guild: discord.Guild, user_id: int) -> str:
        member = guild.get_member(user_id)
        if member is None:
            return f"<@{user_id}>"
        return discord.utils.escape_markdown(member.display_name)

    def _titled(self, guild: discord.Guild, user_id: int) -> str:
        """Nombre para `fortunas`, con el título que luce si tiene uno."""
        name = self._name(guild, user_id)
        if (title := tienda.title_of(self.bot, guild.id, user_id)) is not None:
            return f"{name} · *{title}*"
        return name

    async def houses(self, guild_id: int) -> tuple[CasinoBook, BankBook] | None:
        """Las cuentas del Casino y del Banco, o `None` si no se pueden leer (no lanza)."""
        try:
            lent, owed = await self.housing.bank_exposure(guild_id) if self.housing else (0, 0)
            return await self.economy.house_books(guild_id, purchase_lent=lent, owed=owed)
        except (OSError, sqlite3.Error):
            logger.exception("No se pudieron leer las cuentas del casino y del banco")
            return None

    async def standings(
        self, guild_id: int
    ) -> tuple[list[NetWorth], tuple[CasinoBook, BankBook] | None]:
        """Los miembros con el Casino y el Banco entre ellos, del más rico al menos.

        Returns:
            `(la lista, las cuentas de la banca)`; sin cuentas, la lista es solo de miembros.
        """
        everyone = await self.fortunes(guild_id)
        books = await self.houses(guild_id)
        if books is not None:
            everyone = sorted(
                [*everyone, *house_worths(*books)], key=lambda w: (-w.total, w.user_id)
            )
        return everyone, books

    async def net_worth(self, guild_id: int, user_id: int) -> tuple[NetWorth, int, int]:
        """Patrimonio de un miembro, su puesto en `fortunas` y cuántos tienen algo.

        El puesto cuenta con el Casino y el Banco, como en `fortunas`.
        """
        everyone, _books = await self.standings(guild_id)
        worth = next((w for w in everyone if w.user_id == user_id), None)
        if worth is None:
            worth = NetWorth(user_id=user_id)
        ranked = [w for w in everyone if w.total > 0]
        rank = next((i for i, w in enumerate(ranked, 1) if w.user_id == user_id), len(ranked))
        return worth, rank, len(ranked)

    async def home_lines(self, guild_id: int, user_id: int) -> list[str]:
        """Cada inmueble de alguien con su parte según sus participaciones, del que más vale.

        Su parte vale lo que el inmueble entero a precio de hoy por sus participaciones: es lo
        que suma 🏠 Pisos (`HousingRepository.member_values`).
        """
        if self.housing is None:
            return []
        market = await self.housing.market(guild_id)
        rows: list[tuple[int, str]] = []
        for holding in market.of_member(user_id):
            listing = market.listing(holding.listing)
            if listing is None:
                continue
            value = market.value(holding)
            whole = rules.price(listing, market.index(listing.barrio))
            percent = holding.shares * 100 // rules.SHARES
            place = BARRIO_BY_KEY[listing.barrio].name
            rows.append((value, home_line(listing.title, place, percent, value, whole)))
        for key, owner in market.vpo.items():
            if owner.owner_id == user_id:
                unit = VPO_BY_KEY[key]
                line = home_line(f"VPO {unit.name}", unit.town, 100, unit.price, unit.price)
                rows.append((unit.price, line))
        return [line for _value, line in sorted(rows, key=lambda row: -row[0])]

    async def quote(
        self, guild_id: int, user_id: int, *, cash: int, homes_fiscal: int, objects: int
    ) -> WealthQuote:
        """Lo que el lunes le cobraría de Patrimonio con lo que tiene ahora.

        Es la misma cuenta en `perfil` y en `saldo`: efectivo, pisos a su valor fiscal y
        objetos de la tienda, con el límite conjunto del IRPF de esta semana (art. 31) y
        las Grandes Fortunas.

        Args:
            objects: Valor de los objetos de la tienda que tributan (los trofeos).
        """
        irpf_base, irpf_quota = await self.economy.week_irpf(guild_id, user_id)
        return wealth_quote(
            cash + homes_fiscal + objects,
            unproductive=objects,
            irpf_base=irpf_base,
            irpf_quota=irpf_quota,
        )

    async def patrimonio_embed(
        self,
        guild: discord.Guild,
        author: discord.abc.User,
        target: discord.abc.User,
        channel: object,
    ) -> discord.Embed:
        """Sección 🏰 Patrimonio de `perfil`: activos de `target` y lo que paga el lunes.

        Apunta los logros de mirar el patrimonio (propio o ajeno) a `author`.
        """
        everyone, books = await self.standings(guild.id)
        worth = next((w for w in everyone if w.user_id == target.id), None) or NetWorth(
            user_id=target.id
        )
        ranked = [w for w in everyone if w.total > 0]
        rank = next((i for i, w in enumerate(ranked, 1) if w.user_id == target.id), len(ranked))
        people = len(ranked)
        rivals = rivals_line(everyone, target.id)
        lines = [
            f"💵 Efectivo: **{format_amount(worth.cash)}**",
            f"🏠 Pisos: **{format_amount(worth.homes)}**",
            *(
                [f"🏢 Su sociedad: **{format_amount(worth.companies)}** (valor teórico)"]
                if worth.companies
                else []
            ),
            f"🛍️ Bienes de la tienda: **{format_amount(worth.goods_value)}** ({len(worth.goods)})",
            f"🎟️ Boletos de lotería por sortear: **{format_amount(worth.lottery)}**",
            f"📬 Renta por cobrar: **{format_amount(worth.renta)}**",
        ]
        if worth.debts > 0:
            lines.append(
                f"🏦 Deudas: **−{format_amount(worth.debts)}** (hipotecas, préstamos y lo que "
                "debe al banco y a sus avalistas)"
            )
        elif worth.debts < 0:
            lines.append(f"🤝 Le deben sus avalados: **{format_amount(-worth.debts)}**")
        embed = discord.Embed(
            title=f"🏰 Patrimonio de {discord.utils.escape_markdown(target.display_name)}",
            description=(
                f"# {format_amount(worth.total)}\n"
                + (f"Puesto {rank} de {people} en `fortunas`\n" if worth.total > 0 else "")
                + (f"🎯 {rivals}\n" if rivals else "")
                + f"*{verdict(worth, rank, people)}*"
            ),
            color=COLOR,
        )
        if await pisos.asnef(self.bot, guild.id, target.id):
            lines.append(
                "📛 En el fichero de morosos (ASNEF): ni préstamos ni hipotecas hasta "
                "pagar lo que debe al banco"
            )
        embed.add_field(
            name="Activos y deudas" if worth.debts else "Activos",
            value="\n".join(lines),
            inline=False,
        )
        if homes := await self.home_lines(guild.id, target.id):
            # Los que quepan enteros en el campo (1.024 caracteres), dejando sitio al «Y N más».
            shown: list[str] = []
            for i, line in enumerate(homes):
                more = f"-# Y {len(homes) - i} inmuebles más."
                if len("\n".join([*shown, line, more])) > 1_024:
                    shown.append(more)
                    break
                shown.append(line)
            embed.add_field(
                name="🏠 Pisos, según sus participaciones",
                value="\n".join(shown),
                inline=False,
            )
        if worth.goods:
            shown = [holding_line(item) for item in worth.goods[:15]]
            if len(worth.goods) > 15:
                shown.append(f"-# Y {len(worth.goods) - 15} cosas más.")
            text = "\n".join(shown)
            embed.add_field(name="Bienes", value=text[:1_024], inline=False)
        objects = sum(item.value for item in worth.goods if item.kind == Kind.TROPHY.value)
        quote = await self.quote(
            guild.id,
            target.id,
            cash=worth.cash,
            homes_fiscal=worth.homes_fiscal + worth.companies,
            objects=objects,
        )
        debt = await self.economy.tax_debt(guild.id, target.id)
        text = (
            f"El lunes te cobraría {format_amount(quote.due)} de Patrimonio (efectivo, pisos "
            f"a su valor fiscal y objetos de la tienda, por lo que pase de "
            f"{format_amount(WEALTH_MINIMUM)}). Lo que no tengas en efectivo queda como "
            "deuda: se cobra el lunes siguiente o se embarga de tus alquileres."
            if quote.due
            else f"No llegas al mínimo del Patrimonio ({format_amount(WEALTH_MINIMUM)} "
            "entre efectivo, pisos y objetos). Sanxe te perdona esta semana."
        )
        if quote.reduced:
            text += (
                f"\nCon tu IRPF de esta semana, el límite conjunto (art. 31) te rebaja "
                f"{format_amount(quote.reduced)}: Patrimonio e IRPF no pasan del 60 % de lo "
                f"que ganas, y siempre pagas al menos un {1 - WEALTH_MAX_REDUCTION:.0%}."
            ).replace("%.", " %.")
        if quote.subject:
            text += (
                f"\n💎 Pasas de {format_amount(SOLIDARITY_WEALTH_THRESHOLD)}: declaras las "
                f"Grandes Fortunas y pagas {format_amount(quote.solidarity)}. Se descuenta "
                "el Patrimonio y Canarias no lo bonifica."
            )
        if debt:
            text += (
                f"\nDebes {format_amount(debt)} a Hacienda de semanas anteriores: se cobran "
                "del monedero el lunes y de lo próximo que te den tus pisos."
            )
        embed.add_field(name=f"🐶 {TAX_COLLECTOR}", value=text, inline=False)
        embed.set_footer(
            text="Pisos a precio de mercado de hoy por las participaciones de cada uno. Bienes al "
            "precio pagado sin IGIC; lo que caduca vale la vida que le queda."
        )
        if not author.bot:
            own = target.id == author.id
            await logros.track(
                self.bot,
                guild.id,
                author,
                channel,
                patrimonio_stats(
                    listing=False,
                    snooping=not own,
                    net_worth=worth.total if own else 0,
                    illiquid=own and worth.illiquid_share >= 0.5,
                    rank=rank if own else 0,
                    people=people,
                ),
            )
            rivals_delta = house_rivals_stats(
                mine=worth.total, casino=books[0].total, bank=books[1].total, debts=worth.debts
            ) if own and books is not None else None  # fmt: skip
            if rivals_delta is not None and rivals_delta.add:
                await logros.track(self.bot, guild.id, author, channel, rivals_delta)
        return embed

    async def _patrimonio_impl(
        self, responder: CommandResponder, member: discord.Member | None
    ) -> None:
        guild = responder.guild
        author = responder.member
        if guild is None or author is None:
            await responder.send_error("El patrimonio solo se mira dentro de un servidor.")
            return
        # Lee el dinero, los pisos, la tienda y los negocios: «pensando…» mientras tanto.
        await responder.start_progress("🏰 Contando el patrimonio…")
        embed = await self.patrimonio_embed(guild, author, member or author, responder.channel)
        await responder.finish(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(
        name="patrimonio",
        description="Lo que tiene un miembro, valorado: dinero, su parte de cada piso y más.",
    )
    @app_commands.describe(miembro="De quién ver el patrimonio (por defecto, el tuyo)")
    @app_commands.guild_only()
    async def patrimonio(
        self, interaction: discord.Interaction, miembro: discord.Member | None = None
    ) -> None:
        """La sección 🏰 Patrimonio de `perfil`, sin abrir el perfil."""
        await self._patrimonio_impl(InteractionResponder(interaction), miembro)

    @commands.command(name="patrimonio")
    @commands.guild_only()
    async def patrimonio_text(
        self, ctx: commands.Context, miembro: discord.Member | None = None
    ) -> None:
        """Versión de texto: `.patrimonio` o `.patrimonio @alguien`."""
        await self._patrimonio_impl(ContextResponder(ctx), miembro)

    async def fortunas_panel(self, guild: discord.Guild, author: discord.abc.User) -> FortunasPanel:
        """El panel de `fortunas` de `author`, con las cifras de ahora."""
        everyone, books = await self.standings(guild.id)
        ranked = [w for w in everyone if w.total > 0]
        avatars = {}
        for worth in ranked[: len(_MEDALS)]:
            if is_house(worth.user_id):
                continue
            if (found := guild.get_member(worth.user_id)) is not None:
                avatars[worth.user_id] = found.display_avatar.url
        names = {
            w.user_id: self._titled(guild, w.user_id) for w in ranked if not is_house(w.user_id)
        }
        houses: dict[int, str] = {}
        if books is not None:
            casino, bank = books
            names[CASINO_ACCOUNT_ID] = f"**{HOUSE_NAMES[CASINO]}**"
            names[BANK_ACCOUNT_ID] = f"**{HOUSE_NAMES[BANK]}**"
            houses = {CASINO_ACCOUNT_ID: casino_line(casino), BANK_ACCOUNT_ID: bank_line(bank)}
        fortunes = Fortunes(
            guild_id=guild.id,
            everyone=tuple(everyone),
            names=names,
            avatars=avatars,
            read_at=time.time(),
            houses=houses,
        )
        return FortunasPanel(self.bot, fortunes, author)

    async def _fortunas_logros(
        self, author: discord.abc.User, channel: object, fortunes: Fortunes
    ) -> None:
        """Los logros de abrir `fortunas` (después de enseñarlo)."""
        if author.bot:
            return
        ranked = fortunes.ranked
        rank = next((i for i, w in enumerate(ranked, 1) if w.user_id == author.id), 0)
        await logros.track(
            self.bot,
            fortunes.guild_id,
            author,
            channel,
            patrimonio_stats(
                listing=True,
                snooping=False,
                net_worth=0,
                illiquid=False,
                rank=rank,
                people=len(ranked),
            ),
        )
        casino = next((w for w in fortunes.everyone if w.user_id == CASINO_ACCOUNT_ID), None)
        bank = next((w for w in fortunes.everyone if w.user_id == BANK_ACCOUNT_ID), None)
        if casino is not None and bank is not None:
            mine = next((w for w in fortunes.everyone if w.user_id == author.id), None)
            delta = house_rivals_stats(
                mine=mine.total if mine else 0, casino=casino.total, bank=bank.total,
                debts=mine.debts if mine else 0,
            )  # fmt: skip
            if delta.add:
                await logros.track(self.bot, fortunes.guild_id, author, channel, delta)

    @app_commands.command(
        name="fortunas", description="Lista de todos los miembros por patrimonio."
    )
    @app_commands.guild_only()
    async def fortunas(self, interaction: discord.Interaction) -> None:
        """La lista Forbes del servidor y cómo de repartida está la riqueza."""
        assert interaction.guild is not None  # guild_only
        # Lee el dinero, los pisos, la tienda, los negocios y la Renta de todos: se acepta antes.
        await ack(interaction)
        panel = await self.fortunas_panel(interaction.guild, interaction.user)
        await edit(interaction, view=panel, allowed_mentions=_NO_MENTIONS)
        panel.interaction = interaction
        await self._fortunas_logros(interaction.user, interaction.channel, panel.fortunes)

    @commands.command(name="fortunas")
    @commands.guild_only()
    async def fortunas_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.fortunas`)."""
        assert ctx.guild is not None  # guild_only
        async with ctx.typing():
            panel = await self.fortunas_panel(ctx.guild, ctx.author)
        panel.message = await ctx.send(view=panel, allowed_mentions=_NO_MENTIONS)
        await self._fortunas_logros(ctx.author, ctx.channel, panel.fortunes)


async def wealth_due(
    bot: commands.Bot, guild_id: int, user_id: int, cash: int
) -> WealthQuote | None:
    """El Patrimonio que el lunes cobraría a alguien con `cash` en efectivo (para `saldo`).

    `None` sin el cog o si falla la lectura de los pisos o de la tienda.
    """
    cog = find_cog(bot, Patrimonio)
    if cog is None:
        return None
    try:
        homes = (await cog.housing.member_values(guild_id))[1] if cog.housing else {}
        goods = await cog.shop.holdings(guild_id, user_id, time.time()) if cog.shop else []
        objects = sum(value for _u, _n, _e, kind, value, _x in goods if kind == Kind.TROPHY.value)
        companies = await negocio.company_values(bot, guild_id)
        return await cog.quote(
            guild_id,
            user_id,
            cash=cash,
            homes_fiscal=homes.get(user_id, 0) + companies.get(user_id, 0),
            objects=objects,
        )
    except (OSError, sqlite3.Error):
        logger.exception("No se pudo calcular el Patrimonio de %s", user_id)
        return None


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía compartida del bot."""
    await bot.add_cog(
        Patrimonio(bot, bot.economy, shop=bot.shop, lottery=bot.lottery, housing=bot.housing)
    )
