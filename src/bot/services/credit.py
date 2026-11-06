"""Financiar compras (💳 Financiar, 9.20.0) y el IPC del crédito, sin Discord ni base de datos.

Cualquier compra de un bien o un servicio (la tienda, las obras y divisiones de `pisos`, lo
que se compra para un negocio, la formación de `pala`, el gallo…) ofrece pagarse a plazos con
el banco de los préstamos (`loans`). El botón sale siempre, te llegue o no el dinero: es la
tentación. Es una crítica al crédito barato, que hace que todo parezca asequible y acaba
encareciendo el dinero de todos (ver «IPC del crédito»).

**Cómo funciona.** El banco abona lo que cuesta la compra (más su comisión de apertura,
que también se financia) y la compra se paga en el acto: el dinero va al vendedor. Es un
préstamo más de `housing_loans` (`kind = 'compra'`, o `'plazos'` en Paga en 3, con lo comprado
en `listing`): se cobra cada noche con el cierre de `pisos`, cuenta en el 35 % de lo que
ingresas, y si no se paga hay fichero de morosos, vencimiento a las 3 cuotas y embargo, como
cualquier préstamo. Lo prestado se crea y las cuotas desaparecen (el banco no existe). Si la
compra falla después de firmar (se agotó, cambió el precio), la financiación se deshace sola.

**Crédito vinculado** (art. 29 de la Ley 16/2011): lo financiado es esa compra. Hasta la
primera cuota se puede desistir devolviendo lo prestado, como en un préstamo personal, y lo
comprado se queda (se ha pagado al contado con ese dinero).

**Los planes** (`Plan`):

- **Normal**: el tipo de un préstamo personal con tu perfil más `EXTRA_SPREAD` (la prisa se
  paga: el crédito en el punto de venta es más caro que pedirlo en el banco), la comisión de
  apertura de siempre y el seguro de protección de pagos si quieres. Admite **carencia**: no
  pagas nada en `GRACE_DAYS` días, pero los intereses corren y se suman a lo que debes.
- **0 % TIN** (`ZERO`): cada semana, `ZERO_PICKS` categorías (pasillos de la tienda, obras,
  negocios…) se venden «sin intereses». Es verdad que el tipo es 0, pero hay una comisión de
  apertura del `ZERO_FEE` y el seguro es obligatorio: la TAE no es 0 y la ficha lo dice, que
  para eso la Ley 16/2011 obliga a darla.
- **Paga en 3** (`BNPL`): de `BNPL_MIN` a `BNPL_MAX`, tres cuotas sin intereses ni comisión.
  No mira tu 35 % al firmar (los aplazamientos sin intereses ni gastos se quedan fuera de la
  Ley 16/2011 hasta que entre la Directiva (UE) 2023/2225), pero su cuota cuenta
  después, y cada cuota que no llega lleva un recargo (`late_fee`) que también desaparece.

**Lo que no se financia:** lo emblemático (lo numerado y los títulos de la tienda: la misma
regla que el Monte de Piedad), el casino y las apuestas, los impuestos y las tasas (la Renta ya
se fracciona), las pujas (son depósitos) y las compras entre miembros (el Rastro o el piso de
otro): dos podrían inventarse una venta y quedarse con dinero que el banco acaba de crear.
Tampoco la entrada de un piso: para eso está la hipoteca.

**IPC del crédito.** El dinero que presta el banco es nuevo, y el que todavía se debe sigue
dando vueltas por el servidor. Con la teoría cuantitativa del dinero (MV = PQ), si hay más
dinero para las mismas cosas, suben los precios: los de la tienda y los de las obras suben un
1 % por cada 1 % que supone lo financiado y aún sin devolver (`credit_alive`) sobre el dinero de
los miembros, hasta `IPC_CAP`. Al devolverse, bajan. Lo pagan todos, también quien nunca ha
pedido nada: así el crédito barato le quita valor al dinero. Se recalcula en momentos fijos
(`HousingService.refresh_ipc`: cada 10 minutos con el cierre de `pisos` y al terminar o
deshacer una compra financiada), y la tienda y las obras lo leen de aquí (`ipc`) al enseñar
el precio y al cobrarlo: así lo enseñado vale al pagar, y lo que se financia es lo que cuesta.

**Fiscalidad.** Lo financiado no es renta de nadie (es un préstamo) y la compra paga sus
impuestos de siempre (el IGIC de la tienda o el de la obra). Los intereses no se deducen,
como los de un préstamo personal. El seguro lleva el IPS del 8 % al Estado.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from functools import cache

from bot.services import loans as lending
from bot.services.housing import IPS_RATE, installment


class Plan(StrEnum):
    """Cómo se financia una compra."""

    NORMAL = "normal"
    ZERO = "cero"
    BNPL = "plazos"


#: Plazos que se ofrecen, en cuotas (días del bot). Las obras y divisiones de `pisos`, más.
TERMS = (3, 6, 12, 24)
WORKS_TERMS = (*TERMS, 48)
ZERO_TERMS = (6, 12)
BNPL_TERMS = (3,)
#: Lo que se suma al tipo de un préstamo personal en el punto de venta.
EXTRA_SPREAD = 0.02
#: Comisión de apertura del 0 % TIN y la prima de su seguro obligatorio (sobre cada cuota):
#: a 12 cuotas sale algo mejor que el plan normal; a 6, peor (la trampa está en la letra
#: pequeña, no en el tipo).
ZERO_FEE = 0.03
ZERO_PREMIUM_SHARE = 0.02
#: Categorías con 0 % TIN cada semana.
ZERO_PICKS = 2
#: Lo que se financia: lo del préstamo personal (1.000 a 750.000 Y$) y, por debajo, Paga en 3.
MIN_AMOUNT = lending.MIN_PRINCIPAL
MAX_AMOUNT = lending.MAX_PRINCIPAL
BNPL_MIN = 100
BNPL_MAX = MIN_AMOUNT - 1
#: Recargo de cada cuota de Paga en 3 que no llega: su parte de la cuota, con un mínimo.
BNPL_LATE_SHARE = 0.25
BNPL_LATE_MIN = 25
#: Días sin pagar de la carencia (los intereses corren).
GRACE_DAYS = 7
#: Financiaciones vivas a la vez por persona (las normales y las de 0 %, y las de Paga en 3).
MAX_FINANCED = 8
MAX_BNPL = 3
#: Lo más que suben los precios con el IPC del crédito, en %.
IPC_CAP = 20

#: Las categorías que no son pasillos de la tienda, con su nombre.
CATEGORY_NAMES = {
    "obras": "🔨 Obras de pisos",
    "negocio": "🏪 Negocios",
    "pala": "⛏️ Formación",
    "entrada": "🔔 Entradas",
}


@cache
def categories() -> tuple[str, ...]:
    """Las categorías de lo que se compra (para el 0 % TIN de la semana): cada pasillo de la
    tienda (`tienda:<pasillo>`) y `CATEGORY_NAMES`."""
    from bot.services.shop_catalog import AISLES  # shop_catalog importa shop, que nos importa

    return (*(f"tienda:{aisle.key}" for aisle in AISLES), *CATEGORY_NAMES)


def category_name(category: str) -> str:
    """El nombre de una categoría, con su icono («🌴 Canarias», «🔨 Obras de pisos»)."""
    from bot.services.shop_catalog import AISLE_BY_KEY

    if category.startswith("tienda:"):
        aisle = AISLE_BY_KEY.get(category.removeprefix("tienda:"))
        return aisle.label if aisle is not None else "la tienda"
    return CATEGORY_NAMES.get(category, category)


@dataclass(frozen=True, slots=True)
class Purchase:
    """Lo que se quiere financiar.

    Attributes:
        label: Qué es, como sale en `saldo` y en el banco («Porsche Cayenne», «Reforma de
            la cocina en …»).
        amount: Lo que cuesta, con impuestos (lo que se paga al vendedor).
        category: De qué categoría es (`categories`), para el 0 % TIN de la semana.
        works: Si es una obra o una división de `pisos` (admite `WORKS_TERMS`).
        cash: Lo que se paga al contado en la misma compra porque no se financia (lo único
            de un carrito, la tasa y la fianza de un local): no entra en `amount`.
    """

    label: str
    amount: int
    category: str
    works: bool = False
    cash: int = 0


def zero_picks(day: date) -> tuple[str, ...]:
    """Las categorías con 0 % TIN la semana de `day` (de lunes a domingo), iguales en todos
    los servidores para que se pueda hablar de ellas."""
    year, week, _ = day.isocalendar()
    return tuple(random.Random(f"cero:{year}:{week}").sample(categories(), ZERO_PICKS))


def plans_for(purchase: Purchase, day: date) -> tuple[Plan, ...]:
    """Cómo se puede financiar una compra hoy (vacío si no se puede)."""
    if BNPL_MIN <= purchase.amount <= BNPL_MAX:
        return (Plan.BNPL,)
    if not MIN_AMOUNT <= purchase.amount <= MAX_AMOUNT:
        return ()
    if purchase.category in zero_picks(day):
        return (Plan.ZERO, Plan.NORMAL)
    return (Plan.NORMAL,)


def refusal(amount: int) -> str | None:
    """Por qué una compra no se puede financiar por su importe, o `None`."""
    if amount < BNPL_MIN:
        return f"El banco no financia nada por debajo de {BNPL_MIN} Y$. Ni el chicle."
    if amount > MAX_AMOUNT:
        return f"El banco financia compras hasta {MAX_AMOUNT:,} Y$.".replace(",", ".")
    return None


def terms(plan: Plan, *, works: bool = False) -> tuple[int, ...]:
    """Los plazos de un plan."""
    if plan is Plan.BNPL:
        return BNPL_TERMS
    if plan is Plan.ZERO:
        return ZERO_TERMS
    return WORKS_TERMS if works else TERMS


def purchase_rate(euribor: float, factors: list[lending.Factor]) -> float:
    """El TIN de una financiación normal: el de un préstamo personal más `EXTRA_SPREAD`."""
    return round(lending.loan_rate(euribor, factors) + EXTRA_SPREAD, 4)


@dataclass(frozen=True, slots=True)
class Offer:
    """La oferta de financiar una compra.

    Attributes:
        plan: El plan.
        amount: Lo que cuesta la compra (lo que va al vendedor).
        quote: La ficha del préstamo: `principal` es la compra más la comisión de apertura
            (que también se financia) y `received`, la compra.
        grace: Si lleva carencia.
        balance: Lo que se debe al empezar a pagar: lo prestado y, con carencia, los
            intereses de esos días.
    """

    plan: Plan
    amount: int
    quote: lending.Quote
    grace: bool
    balance: int

    @property
    def months(self) -> int:
        """Cuántas cuotas."""
        return self.quote.months

    @property
    def total(self) -> int:
        """Lo que se paga entre todas las cuotas."""
        return self.quote.total

    @property
    def extra(self) -> int:
        """Lo que se paga de más sobre lo que cuesta."""
        return self.total - self.amount

    @property
    def tae(self) -> float:
        """La TAE, con la comisión, el seguro y la carencia dentro."""
        return lending.annual_equivalent(
            self.amount, self.quote.monthly, self.months, delay=GRACE_DAYS if self.grace else 0
        )


def offer(
    amount: int,
    plan: Plan,
    months: int,
    rate: float = 0.0,
    *,
    insured: bool = False,
    grace: bool = False,
) -> Offer:
    """La oferta de financiar `amount` con un plan, a `months` cuotas.

    El 0 % y Paga en 3 fijan su tipo (0), su comisión y su seguro (obligatorio en el 0 %,
    nunca en Paga en 3), y solo el plan normal admite la carencia.
    """
    fee_share = lending.OPENING_FEE
    if plan is not Plan.NORMAL:
        rate, grace = 0.0, False
        insured = plan is Plan.ZERO
        fee_share = ZERO_FEE if plan is Plan.ZERO else 0.0
    principal = math.ceil(amount / (1 - fee_share))
    balance = round(principal * (1 + rate / 12) ** GRACE_DAYS) if grace else principal
    payment = installment(balance, rate, months)
    share = ZERO_PREMIUM_SHARE if plan is Plan.ZERO else lending.INSURANCE_SHARE
    premium = round(payment * share) if insured else 0
    quote = lending.Quote(
        principal=principal,
        months=months,
        rate=rate,
        payment=payment,
        fee=principal - amount,
        premium=premium,
        premium_tax=round(premium * IPS_RATE),
    )
    return Offer(plan, amount, quote, grace, balance)


def late_fee(payment: int) -> int:
    """El recargo de una cuota de Paga en 3 que no llega."""
    return max(BNPL_LATE_MIN, math.ceil(payment * BNPL_LATE_SHARE))


# -- IPC del crédito -----------------------------------------------------------------------


def ipc_pct(credit_alive: int, money: int) -> int:
    """Lo que suben los precios (en %) con lo financiado aún sin devolver.

    Args:
        credit_alive: Lo que se debe de financiaciones (`compra` y `plazos`) en el servidor.
        money: El dinero de los miembros (sus monederos).
    """
    if credit_alive <= 0:
        return 0
    if money <= 0:
        return IPC_CAP
    return min(IPC_CAP, credit_alive * 100 // money)


#: El IPC de cada servidor, en %. Lo pone el cog de `pisos` (`set_ipc`).
_IPC: dict[int, int] = {}


def ipc(guild_id: int) -> int:
    """El IPC del crédito de un servidor, en % (0 si nadie debe nada o aún no se ha medido)."""
    return _IPC.get(guild_id, 0)


def set_ipc(guild_id: int, pct: int) -> None:
    """Fija el IPC del crédito de un servidor."""
    _IPC[guild_id] = max(0, min(IPC_CAP, pct))


def inflate(price: int, pct: int) -> int:
    """Un precio con el IPC del crédito encima."""
    return price + price * pct // 100
