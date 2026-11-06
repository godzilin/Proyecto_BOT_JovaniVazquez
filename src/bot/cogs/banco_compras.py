"""💳 Financiar: la oferta del banco para pagar a plazos cualquier compra, desde donde se compra.

No es un cog: es la pantalla que abre el botón 💳 Financiar de cada compra (la caja, la ficha y
el carrito de la tienda, las obras y divisiones de `pisos`, los negocios, la formación de
`pala`…). Las reglas, las ofertas y el IPC del crédito están en `bot.services.credit`; la firma,
en `HousingService.finance`.

Quien compra le pasa un `Deal`: qué se compra (`credit.Purchase`) y cómo se compra (`buy`, el
camino de siempre de esa compra, que se ejecuta con el dinero ya en el monedero). La pantalla
enseña el plan (normal, 0 % TIN de la semana o Paga en 3), el plazo, el seguro y la carencia, la
ficha con la letra pequeña y si cabe en tu 35 %. ✍️ Firmar y comprar firma, compra y, si la
compra no sale, deshace la financiación (`HousingService.unfinance`).

- **En el sitio** (`Deal.host`): la caja de la tienda es un mensaje privado, así que la oferta
  sustituye a la caja y ↩️ Volver la devuelve; al comprar, se ve el ticket de la caja.
- **Aparte**: desde un panel público (`pisos`), la oferta sale en un mensaje privado nuevo y la
  compra repinta el panel por su cuenta.

Dinero: `EconomyService.housing` (fiscalidad en `HousingRepository.finance` y
`bot.services.credit`); la compra, por su camino de siempre. Firmar llama a `renta.remind`, el
resultado lleva `renta.hint` con la mascota y los logros van en 🏦 Banco (`financed_stats`).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

import discord
from discord import ui
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import renta
from bot.repositories.economy import BalanceLimitError, InsufficientFundsError
from bot.repositories.housing import HousingError, LoanDesk
from bot.repositories.housing_finance import LOAN_BNPL, LOAN_PURCHASE
from bot.services import credit
from bot.services import loans as lending
from bot.services.achievements import financed_stats
from bot.services.economy import format_amount
from bot.services.levels import TIMEZONE, local_day
from bot.services.pets import Event, Moment
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit, notify

if TYPE_CHECKING:
    from bot.cogs.banco import Banco

logger = logging.getLogger(__name__)

COLOR = discord.Color.from_rgb(46, 110, 180)
#: Lo que pone el botón en cada compra.
LABEL = "💳 Financiar"
#: Cuántas cuotas salen elegidas al abrir (o la más cercana del plan).
DEFAULT_TERM = 12
#: Lo que dice el banco arriba de la oferta (la mitad, normal).
SLOGANS = (
    "Llévatelo hoy y págalo poco a poco.",
    "Tu yo del futuro paga. Él sabrá cómo.",
    "Financiación inmediata: la respuesta, en segundos.",
    "Sin entrada. Con salida, ya veremos.",
    "Elige el plazo que mejor te venga.",
    "Cuotas cómodas para quien no mira el total.",
)

#: `Deal.buy`: compra con el dinero ya en el monedero y devuelve `None` si ha comprado o por
#: qué no. No contesta al clic (la oferta ya lo ha aceptado y enseña el resultado).
Buy = Callable[[discord.Interaction], Awaitable[str | None]]


@dataclass
class Deal:
    """Lo que quien compra le pasa a la oferta.

    Attributes:
        purchase: Qué se compra y lo que cuesta.
        buy: Cómo se compra (ver `Buy`).
        host: La vista de la compra, si la oferta va en su mismo mensaje: ↩️ Volver la
            enseña de nuevo y, tras comprar, se enseña con su resultado.
        after: Lo que hace la compra después de enseñar el resultado (logros, anuncios).
    """

    purchase: credit.Purchase
    buy: Buy
    host: ui.LayoutView | None = None
    after: Callable[[discord.Interaction], Awaitable[None]] | None = None


def _plans_today(amount: int, category: str, works: bool) -> tuple[credit.Plan, ...]:
    if credit.refusal(amount) is not None:
        return ()
    today = local_day(datetime.now(TIMEZONE).timestamp())
    return credit.plans_for(credit.Purchase("", amount, category, works), today)


def teaser(amount: int, category: str, *, works: bool = False) -> str | None:
    """Lo que se enseña junto al precio de algo que se puede financiar, o `None` si no.

    Lo más atractivo que le toca hoy: Paga en 3 (sin intereses) o el 0 % TIN de la semana con
    su cuota exacta, que no depende de quién compra. El plan normal va al tipo de cada perfil,
    así que solo se dice hasta cuántas cuotas llega.
    """
    plans = _plans_today(amount, category, works)
    if credit.Plan.BNPL in plans:
        monthly = credit.offer(amount, credit.Plan.BNPL, 3).quote.monthly
        return f"💳 Paga en 3 de {format_amount(monthly)}, sin intereses"
    if credit.Plan.ZERO in plans:
        months = max(credit.ZERO_TERMS)
        monthly = credit.offer(amount, credit.Plan.ZERO, months).quote.monthly
        return f"💳 0 % TIN esta semana: {months} cuotas de {format_amount(monthly)}"
    if plans:
        return f"💳 A plazos, hasta {max(credit.terms(credit.Plan.NORMAL, works=works))} cuotas"
    return None


def zero_pitch() -> str:
    """Las categorías con 0 % TIN esta semana («🌴 Canarias y 🔨 Obras de pisos»)."""
    today = local_day(datetime.now(TIMEZONE).timestamp())
    return " y ".join(credit.category_name(c) for c in credit.zero_picks(today))


def badge(amount: int, category: str) -> str | None:
    """La etiqueta corta de una lista: `💳` si se financia, `💳 0 %` con el 0 % TIN."""
    plans = _plans_today(amount, category, False)
    if not plans:
        return None
    return "💳 0 %" if credit.Plan.ZERO in plans else "💳"


def _pct(rate: float) -> str:
    """`7,25 %`."""
    return f"{rate * 100:.2f}".replace(".", ",") + " %"


def _times(total: int, amount: int) -> str:
    """`×1,23`."""
    return f"×{total / amount:.2f}".replace(".", ",") if amount else ""


def _night(day_offset: int) -> int:
    """El cierre de la noche de dentro de `day_offset` días (epoch), para `<t:…:R>`."""
    day = local_day(datetime.now(TIMEZONE).timestamp()) + timedelta(days=day_offset + 1)
    return int(datetime.combine(day, time.min, TIMEZONE).timestamp())


def _button(
    label: str,
    callback: Callable[[discord.Interaction], Awaitable[None]],
    *,
    custom_id: str,
    style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    disabled: bool = False,
) -> ui.Button:
    button: ui.Button = ui.Button(label=label, style=style, disabled=disabled, custom_id=custom_id)
    button.callback = callback  # type: ignore[method-assign]
    return button


class FinanceView(ui.LayoutView):
    """La oferta para financiar una compra: plan, plazo, seguro, carencia y la ficha."""

    def __init__(
        self,
        bot: commands.Bot,
        owner: discord.abc.User,
        guild_id: int,
        deal: Deal,
        *,
        desk: LoanDesk,
        income: int,
    ) -> None:
        super().__init__(timeout=600)
        self.bot = bot
        self.owner = owner
        self.guild_id = guild_id
        self.deal = deal
        self.desk = desk
        self.income = income
        self.day = local_day(datetime.now(TIMEZONE).timestamp())
        self.plans = credit.plans_for(deal.purchase, self.day)
        self.plan = self.plans[0] if self.plans else credit.Plan.NORMAL
        self.months = self._term(DEFAULT_TERM)
        self.insured = False
        self.grace = False
        self.notice: str | None = None
        self.result: str | None = None
        #: Lo que tenía al firmar (para el logro de financiar lo que podía pagar).
        self.balance_before = 0
        self.rebuild()

    # -- Cuentas -----------------------------------------------------------------------

    def _term(self, wanted: int) -> int:
        terms = credit.terms(self.plan, works=self.deal.purchase.works)
        return min(terms, key=lambda t: abs(t - wanted))

    @property
    def rate(self) -> float:
        """El TIN del plan normal con tu perfil (los otros, 0)."""
        if self.plan is not credit.Plan.NORMAL:
            return 0.0
        factors = lending.profile_factors(self.desk.profile, insured=self.insured)
        return credit.purchase_rate(self.desk.euribor, factors)

    @property
    def offer(self) -> credit.Offer:
        return credit.offer(
            self.deal.purchase.amount, self.plan, self.months, self.rate,
            insured=self.insured, grace=self.grace,
        )  # fmt: skip

    def problem(self) -> str | None:
        """Por qué no se puede firmar ahora, o `None`."""
        purchase = self.deal.purchase
        if not self.plans:
            return credit.refusal(purchase.amount) or "Esto no se financia."
        if self.desk.asnef:
            return lending.ASNEF_REFUSAL
        kind = LOAN_BNPL if self.plan is credit.Plan.BNPL else LOAN_PURCHASE
        cap = credit.MAX_BNPL if kind == LOAN_BNPL else credit.MAX_FINANCED
        if sum(loan.kind == kind for loan in self.desk.purchases) >= cap:
            return f"Ya tienes {cap} compras así sin terminar de pagar: el banco espera."
        room = lending.room_for(self.income, self.desk.installments)
        if self.plan is not credit.Plan.BNPL and self.offer.quote.monthly > room:
            return (
                f"No cabe en tu 35 %: te caben {format_amount(room)} al mes más y esto pide "
                f"{format_amount(self.offer.quote.monthly)}. Prueba un plazo más largo."
            )
        return None

    # -- Pantalla ------------------------------------------------------------------------

    def text(self) -> str:
        """La ficha de la oferta, con la letra pequeña a la vista."""
        purchase, offer = self.deal.purchase, self.offer
        quote = offer.quote
        lines = [
            f"### 💳 Financiar · {purchase.label}",
            f"-# «{SLOGANS[self.day.toordinal() % len(SLOGANS)]}» — Banco",
            f"Cuesta **{format_amount(purchase.amount)}** con impuestos.",
        ]
        if purchase.cash:
            lines.append(
                f"-# Y **{format_amount(purchase.cash)}** al contado de tu saldo: lo que no se "
                "financia (lo numerado, los títulos, tasas y fianzas)."
            )
        if not self.plans:
            return "\n".join([*lines, self.problem() or ""])
        wait = credit.GRACE_DAYS if offer.grace else 0
        lines += [
            f"> 📅 **{offer.months} cuotas de {format_amount(quote.monthly)}** · la primera "
            f"<t:{_night(1 + wait)}:R>",
            f"> 📈 TIN {_pct(quote.rate)} · **TAE {_pct(offer.tae)}**",
            f"> 💸 Pagas **{format_amount(offer.total)}** por algo de "
            f"{format_amount(purchase.amount)}: {format_amount(offer.extra)} más "
            f"({_times(offer.total, purchase.amount)})",
        ]
        small = []
        if quote.fee:
            small.append(f"comisión de apertura {format_amount(quote.fee)}, también a plazos")
        if quote.premium:
            small.append(
                f"seguro{' obligatorio' if self.plan is credit.Plan.ZERO else ''} "
                f"{format_amount(quote.premium)} + {format_amount(quote.premium_tax)} de IPS "
                "con cada cuota"
            )
        if offer.grace:
            small.append(f"{credit.GRACE_DAYS} días sin pagar, pero los intereses corren")
        if self.plan is credit.Plan.BNPL:
            small.append(
                f"no mira tu 35 %, pero cada cuota que no pagues lleva "
                f"{format_amount(credit.late_fee(quote.payment))} de recargo"
            )
        if small:
            lines.append("-# Letra pequeña: " + "; ".join(small) + ".")
        if self.plan is credit.Plan.ZERO:
            lines.append(
                "-# 🔥 0 % TIN solo esta semana en "
                + " y ".join(credit.category_name(c) for c in credit.zero_picks(self.day))
                + ". El tipo es 0; la TAE, no."
            )
        if ipc := credit.ipc(self.guild_id):
            lines.append(
                f"-# 📈 Con lo que hay financiado en el servidor, los precios ya suben un {ipc} %."
            )
        if problem := self.problem():
            lines.append(f"⛔ {problem}")
        lines.append(
            "-# Puedes echarte atrás hasta la primera cuota devolviendo lo prestado, en `banco`."
        )
        return "\n".join(lines)

    def rebuild(self) -> None:
        """Pinta la oferta o, ya firmada, lo que ha pasado."""
        self.clear_items()
        container = ui.Container(accent_colour=COLOR)
        if self.result is not None:
            container.add_item(ui.TextDisplay(self.result))
            self.add_item(container)
            return
        body = self.text()
        if self.notice:
            body += f"\n\n{self.notice}"
        container.add_item(ui.TextDisplay(body))
        self.add_item(container)
        if len(self.plans) > 1:
            row: ui.ActionRow = ui.ActionRow()
            for plan in self.plans:
                label = "🔥 0 % TIN" if plan is credit.Plan.ZERO else "🏦 Normal"
                style = (
                    discord.ButtonStyle.primary
                    if plan is self.plan
                    else discord.ButtonStyle.secondary
                )
                row.add_item(
                    _button(
                        label, self._plan(plan), custom_id=f"financiar:plan:{plan}", style=style
                    )
                )
            self.add_item(row)
        terms = credit.terms(self.plan, works=self.deal.purchase.works) if self.plans else ()
        if len(terms) > 1:
            select: ui.Select = ui.Select(
                custom_id="financiar:plazo",
                options=[
                    discord.SelectOption(
                        label=f"{t} cuotas", value=str(t), default=t == self.months
                    )
                    for t in terms
                ],
            )
            select.callback = self._pick_term  # type: ignore[method-assign]
            self.add_item(ui.ActionRow(select))
        actions: ui.ActionRow = ui.ActionRow()
        actions.add_item(
            _button(
                f"✍️ Firmar y comprar ({format_amount(self.offer.quote.monthly)}/cuota)"
                if self.plans
                else "✍️ Firmar y comprar",
                self._sign,
                custom_id="financiar:firmar",
                style=discord.ButtonStyle.success,
                disabled=self.problem() is not None,
            )
        )
        if self.plan is credit.Plan.NORMAL and self.plans:
            actions.add_item(
                _button(
                    "🛡️ Con seguro" if self.insured else "🛡️ Sin seguro",
                    self._toggle_insurance,
                    custom_id="financiar:seguro",
                    style=discord.ButtonStyle.primary
                    if self.insured
                    else discord.ButtonStyle.secondary,
                )
            )
            actions.add_item(
                _button(
                    f"🛌 Carencia {credit.GRACE_DAYS} días" + (" ✓" if self.grace else ""),
                    self._toggle_grace,
                    custom_id="financiar:carencia",
                    style=discord.ButtonStyle.primary
                    if self.grace
                    else discord.ButtonStyle.secondary,
                )
            )
        actions.add_item(
            _button(
                "↩️ Volver" if self.deal.host is not None else "Cancelar",
                self._back,
                custom_id="financiar:volver",
            )
        )
        self.add_item(actions)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """La oferta es solo de quien la pidió (en la práctica ya es privada)."""
        return interaction.user.id == self.owner.id

    # -- Botones (solo memoria salvo firmar) -------------------------------------------

    def _plan(self, plan: credit.Plan) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            self.plan, self.notice = plan, None
            self.months = self._term(self.months)
            if plan is not credit.Plan.NORMAL:
                self.insured = self.grace = False
            self.rebuild()
            await interaction.response.edit_message(view=self)

        return callback

    async def _pick_term(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values", []) if interaction.data else []
        if values and values[0].isdigit():
            self.months = self._term(int(values[0]))
        self.notice = None
        self.rebuild()
        await interaction.response.edit_message(view=self)

    async def _toggle_insurance(self, interaction: discord.Interaction) -> None:
        self.insured, self.notice = not self.insured, None
        self.rebuild()
        await interaction.response.edit_message(view=self)

    async def _toggle_grace(self, interaction: discord.Interaction) -> None:
        self.grace, self.notice = not self.grace, None
        self.rebuild()
        await interaction.response.edit_message(view=self)

    async def _back(self, interaction: discord.Interaction) -> None:
        self.stop()
        if self.deal.host is not None:
            await interaction.response.edit_message(view=self.deal.host)
            return
        self.result = "Nada firmado. El banco te espera cuando quieras."
        self.rebuild()
        await interaction.response.edit_message(view=self)

    async def _sign(self, interaction: discord.Interaction) -> None:
        # El ack va antes de todo: firmar y comprar toca la base de datos dos veces.
        await ack(interaction)
        cog = _bank(self.bot)
        if cog is None:
            await notify(interaction, "El banco está cerrado ahora mismo.")
            return
        key = (self.guild_id, self.owner.id)
        if key in cog.working:
            await notify(interaction, "Un momento, que el banco está con lo anterior.")
            return
        cog.working.add(key)
        try:
            bought, offer = await self._sign_and_buy(cog, interaction)
        finally:
            cog.working.discard(key)
        if not bought:
            self.rebuild()
            await edit(interaction, view=self)
            return
        self.stop()
        if self.deal.host is not None:
            await edit(interaction, view=self.deal.host)
        else:
            self.result = await self._receipt(offer)
            self.rebuild()
            await edit(interaction, view=self)
        if self.deal.after is not None:
            await self.deal.after(interaction)
        await renta.remind(self.bot, interaction)
        purchase = self.deal.purchase
        await logros.track(
            self.bot, self.guild_id, self.owner, interaction.channel,
            financed_stats(
                plan=str(offer.plan), amount=purchase.amount, grace=offer.grace,
                works=purchase.works, coffee=purchase.category == "cafe",
                could_pay=self.balance_before >= purchase.amount,
                open_count=len(self.desk.purchases) + 1, ipc=credit.ipc(self.guild_id),
                trap=offer.plan is credit.Plan.ZERO and offer.months == min(credit.ZERO_TERMS),
                category=purchase.category, cash=purchase.cash,
            ),
        )  # fmt: skip

    async def _sign_and_buy(
        self, cog: Banco, interaction: discord.Interaction
    ) -> tuple[bool, credit.Offer]:
        """Firma y compra; si la compra no sale, deshace la financiación. `(compró, oferta)`."""
        offer = self.offer
        service = cog.housing
        self.balance_before = await service.economy.balance(self.guild_id, self.owner.id)
        try:
            signed, _ = await service.finance(
                self.guild_id, self.owner.id, self.deal.purchase, plan=self.plan,
                months=self.months, insured=self.insured, grace=self.grace, rate=self.rate,
            )  # fmt: skip
        except HousingError as error:
            self.notice = f"⛔ {error}"
            return False, offer
        except (OSError, sqlite3.Error, InsufficientFundsError, BalanceLimitError):
            logger.exception("No se pudo financiar la compra de %s", self.owner.id)
            self.notice = "⛔ El banco no responde ahora mismo. Prueba en un rato."
            return False, offer
        try:
            error = await self.deal.buy(interaction)
        except Exception:
            # Sin esto quedaría un préstamo vivo por una compra que no se hizo.
            logger.exception("La compra financiada de %s falló", self.owner.id)
            error = "La compra no ha salido."
        if error is None:
            await service.refresh_ipc(self.guild_id)
            return True, offer
        await service.unfinance(self.guild_id, self.owner.id, signed.loan.id)
        self.notice = (
            f"⛔ {error.removeprefix('❌ ')} La financiación se ha deshecho: no debes nada."
        )
        return False, offer

    async def _receipt(self, offer: credit.Offer) -> str:
        purchase = self.deal.purchase
        lines = [
            f"### ✅ Comprado: {purchase.label}",
            f"Pagas {offer.months} cuotas de **{format_amount(offer.quote.monthly)}** (la "
            f"primera <t:{_night(1 + (credit.GRACE_DAYS if offer.grace else 0))}:R>): "
            f"{format_amount(offer.total)} en total por algo de {format_amount(purchase.amount)}.",
            "-# Las cuotas salen en `saldo` y en `banco`, donde puedes amortizar o echarte atrás.",
        ]
        moment = Moment(Event.BUY)
        if (hint := await renta.hint(self.bot, self.guild_id, self.owner.id, moment)) is not None:
            lines.append(hint)
        return "\n".join(lines)


def _bank(bot: commands.Bot) -> Banco | None:
    from bot.cogs.banco import Banco  # banco importa este módulo

    return find_cog(bot, Banco)


async def open_finance(bot: commands.Bot, interaction: discord.Interaction, deal: Deal) -> bool:
    """Abre la oferta para financiar una compra.

    Con `deal.host`, en el mismo mensaje (la caja de la tienda); si no, en un mensaje privado
    nuevo. Devuelve `False` sin el banco (sin contestar al clic).
    """
    cog = _bank(bot)
    if cog is None or interaction.guild is None:
        return False
    await ack(interaction, new_message=deal.host is None)
    try:
        desk, income = await cog.housing.loan_desk(interaction.guild.id, interaction.user.id)
    except (OSError, sqlite3.Error):
        logger.exception("No se pudo abrir la financiación en %s", interaction.guild.id)
        await edit(interaction, content="El banco está cerrado ahora mismo.")
        return True
    view = FinanceView(bot, interaction.user, interaction.guild.id, deal, desk=desk, income=income)
    await edit(interaction, view=view)
    return True
