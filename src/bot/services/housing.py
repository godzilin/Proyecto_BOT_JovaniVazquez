"""Reglas de `pisos`: lo que vale un inmueble, lo que renta y lo que cuesta tenerlo.

Lógica pura, sin Discord ni base de datos. Los datos (barrios, inmuebles,
inquilinos) están en `bot.services.housing_catalog`; el dinero lo mueve
`EconomyService.housing` y lo que pasa cada día lo guarda
`bot.repositories.housing`.

**Escalas.**

- Precios a la escala general, 10 Y$ = 1 € (`taxes.YAPDOLLARS_PER_EURO`), como la
  tienda: un piso cuesta lo que pide el portal multiplicado por diez.
- **Un día del bot es un mes del contrato:** cada noche el inquilino paga el mes y
  se paga el mes de comunidad, seguro e IBI. Así un piso se paga solo en unos
  meses de verdad y no en veinte años.
- El IRPF no mira la etiqueta: cada Y$ de renta cuenta igual que uno de nómina o de
  premio (la vara única de `bot.services.taxes`), y la semana es el año fiscal. Cada
  cobro de renta se apunta con lo que la declaración necesita (ingresos, intereses y
  reparaciones, el resto de gastos y la reducción del contrato).

**Participaciones.** Cada inmueble se divide en `SHARES` participaciones: quien
tiene las 100 tiene el piso entero; quien compra 3, el 3 % de todo (lo que vale,
lo que renta y lo que cuesta). Es la copropiedad del art. 392 del Código Civil,
con los mismos impuestos que un piso entero. Decide la renta quien tiene la
mayoría (art. 398 CC); si nadie la tiene, la gestora pide la de referencia.

**Impuestos y gastos** (cada uno con su norma, en el comentario de su constante):
ITP al comprar, notaría y comisión de la agencia (no son impuestos: el dinero
desaparece, salvo su IGIC, que va al Estado), IBI cada mes, IRPF de la renta y de la
ganancia al vender. El
Impuesto sobre el Patrimonio cuenta los inmuebles a su valor fiscal
(`fiscal_value`).

**De dónde sale el dinero.** Los inquilinos y los compradores son ficticios: la
renta y lo cobrado al vender se crean de la nada, y lo pagado al comprar y los
gastos desaparecen. El tope que pide la Biblia («Nada crece sin tope») es que solo
hay un inmueble de cada en el servidor (los 180 del catálogo y hasta 20 de obra
nueva): la renta del servidor no puede crecer sin fin aunque se reinvierta.
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, timedelta
from functools import cache

from bot.services.housing_catalog import (
    BARRIO_BY_KEY,
    BARRIO_NEWS,
    BUILDING_UNITS,
    BUSINESS_KINDS,
    KINDS,
    LISTING_BY_KEY,
    LISTINGS,
    NOMAD_DEMAND,
    PROMOTION_BLURBS,
    PROMOTION_FLOORS,
    PROMOTION_NAMES,
    RENT_LEVELS,
    ROOM_KIND,
    TENANT_BY_KEY,
    TENANTS,
    BarrioNews,
    Listing,
    Reform,
    RentLevel,
    Tenant,
)
from bot.services.housing_streets import STREET_UNIT_BY_KEY, STREET_UNITS, is_solar_unit
from bot.services.taxes import (
    IGIC_BY_KEY,
    IGIC_GENERAL_RATE,
    IMPUTED_RATE,
    WAGE_YAPDOLLARS_PER_EURO,
    YAPDOLLARS_PER_EURO,
    capital_gain_tax,
    igic,
)

#: Participaciones en que se divide cada inmueble.
SHARES = 100

#: ITP de los inmuebles en Canarias: tipo general del 6,5 % (art. 31 del Decreto
#: Legislativo 1/2009). Lo paga quien compra (art. 8.a del RDL 1/1993). El tipo
#: reducido de la vivienda habitual (5 %) no aplica: aquí se compra para alquilar.
ITP_RATE = 0.065
#: Notaría, registro y gestoría, más o menos un 1,5 % del precio. No es un
#: impuesto: desaparece (los aranceles del RD 1426/1989 y del RD 1427/1989).
NOTARY_RATE = 0.015
#: Comisión de la agencia al vender, a cargo de quien vende: un 3 %.
SALE_FEE_RATE = 0.03
#: Notaría, registro, gestoría y agencia son servicios profesionales: pagan el IGIC
#: general (art. 51 de la Ley 4/2012), que va al Estado (desde la 8.0.0).
SERVICES_IGIC = IGIC_GENERAL_RATE
#: Valor catastral: la mitad del valor de mercado (coeficiente de referencia al
#: mercado RM de 0,5, Orden EHA/1213/2005). No sigue al índice del barrio: el
#: catastro va siempre años por detrás.
CADASTRAL_SHARE = 0.5
#: Tipo del IBI urbano. La ley deja a cada ayuntamiento entre el 0,4 % y el 1,1 %
#: (art. 72.1 del RDL 2/2004); se usa uno intermedio para todos.
IBI_RATE = 0.0055
#: Seguro del hogar (multirriesgo): un 1,5 % de la renta de referencia al mes. Hasta la 8.1.0
#: era un 3 % «con impago» que no pagaba nada; el de impago va aparte (`premium`).
INSURANCE_SHARE = 0.015
#: Impuesto sobre las Primas de Seguros: el 8 % de la prima, que paga el tomador y va al
#: Estado (art. 12 de la Ley 13/1996). Los seguros no llevan IGIC.
IPS_RATE = IGIC_BY_KEY["primas"].rate
#: Amortización deducible: el 3 % anual del valor de la construcción (art. 14.2.a
#: del RIRPF), que se toma como el 60 % de lo pagado (el resto es suelo).
AMORTIZATION_RATE = 0.03
CONSTRUCTION_SHARE = 0.6
#: Reducción del rendimiento neto positivo del alquiler de vivienda (art. 23.2 LIRPF,
#: redacción de la Ley 12/2023): el 50 % general y el 90 % si, en zona tensionada, un
#: contrato nuevo baja la renta más de un 5 % respecto al anterior (`contract_reduction`).
RENT_REDUCTION = 0.5
TENSE_RENT_REDUCTION = 0.9
TENSE_RENT_CUT = 0.05
#: El 70 %: vivienda que se alquila por primera vez en zona tensionada a un inquilino de
#: 18 a 35 años (art. 23.2.b.1.º LIRPF, Ley 12/2023).
YOUNG_RENT_REDUCTION = 0.7
YOUNG_AGES = (18, 35)

#: Lo que se mueve el índice del barrio si se compra (o se vende) todo el barrio de
#: golpe. Cada operación lo mueve en proporción a lo que pesa en el barrio
#: (`index_after`): comprar el trastero apenas lo toca y comprar la villa, mucho.
#: Comprar encarece el barrio a los demás. Tiene que quedarse por debajo de lo que cuesta
#: una ida y vuelta (ITP, notaría y agencia, ~11 %): si no, comprar algo pequeño para
#: inflar lo que ya se tiene en el barrio saldría a cuenta. Hasta la 7.22 era un 1,5 % y
#: el índice no se movía: la zona tensionada y el gran tenedor no saltaban nunca.
INDEX_STEP = 0.10
#: Cada día el índice recorre esta parte de lo que le falta para llegar a lo que tienen
#: comprado los miembros (`ownership_level`): media vida de unas tres semanas.
INDEX_REVERSION = 0.03
INDEX_MIN = 0.7
INDEX_MAX = 2.5

#: Probabilidad diaria de que un inquilino se vaya antes de tiempo.
EARLY_LEAVE = 0.004
#: Al acabar el contrato, probabilidad de que lo renueve con la misma renta.
RENEWAL = 0.7
#: Probabilidad diaria de que quien no paga se ponga al día solo.
PAY_BACK = 0.04
#: Probabilidad diaria de que entren okupas en un piso vacío desde hace una semana.
SQUAT = 0.003
SQUAT_AFTER_DAYS = 7
#: Días que tarda el juzgado: desahucio por impago y por ocupación.
EVICTION_DAYS = (10, 20)
SQUAT_EVICTION_DAYS = (15, 30)

#: Estados de un inmueble con dueño.
VACANT = "vacio"
RENTED = "alquilado"
DEFAULTING = "impago"
EVICTING = "desahucio"
SQUATTED = "okupa"


def _euros(amount: float) -> int:
    """Euros a Y$, a la escala general."""
    return round(amount * YAPDOLLARS_PER_EURO)


def _m2(listing: Listing) -> float:
    kind = KINDS[listing.kind]
    return kind.fixed_m2 if kind.fixed_m2 is not None else listing.m2


def catalog_price(listing: Listing) -> int:
    """Precio del inmueble entero, sin reformas y con el índice a 1, en Y$ (a miles)."""
    barrio = BARRIO_BY_KEY[listing.barrio]
    kind = KINDS[listing.kind]
    euros = _m2(listing) * barrio.price_m2 * kind.price_factor
    return int(round(_euros(euros), -3))


def base_price(listing: Listing) -> int:
    """Precio del inmueble entero con el índice del barrio a 1, con sus reformas (a miles)."""
    return int(round(catalog_price(listing) * (1 + listing.value_bonus), -3))


def price(listing: Listing, index: float = 1.0) -> int:
    """Lo que vale hoy el inmueble entero, con el índice de su barrio."""
    return int(round(base_price(listing) * index, -3))


def share_price(listing: Listing, index: float = 1.0) -> int:
    """Lo que vale una participación (1/`SHARES`)."""
    return price(listing, index) // SHARES


def reference_rent(listing: Listing, index: float = 1.0) -> int:
    """Renta de referencia de un mes (un día del bot), en Y$ (a decenas)."""
    barrio = BARRIO_BY_KEY[listing.barrio]
    kind = KINDS[listing.kind]
    euros = _m2(listing) * barrio.rent_m2 * kind.rent_factor * index * (1 + listing.rent_bonus)
    return int(round(_euros(euros), -1))


def asking_rent(listing: Listing, level: RentLevel, index: float = 1.0) -> int:
    """Lo que pide el dueño al mes con su nivel de renta."""
    return int(round(reference_rent(listing, index) * level.factor, -1))


def cadastral_value(listing: Listing) -> int:
    """Valor catastral del inmueble entero (no sigue al índice ni ve las reformas: el
    catastro va siempre años por detrás)."""
    return round(catalog_price(listing) * CADASTRAL_SHARE)


@dataclass(frozen=True, slots=True)
class MonthlyCosts:
    """Lo que cuesta tener el inmueble entero un mes (un día del bot), en Y$.

    Attributes:
        community: Comunidad o mantenimiento. Desaparece.
        insurance: Seguro del hogar con impago. Desaparece.
        ibi: IBI del mes. Va al Estado (en el juego no hay ayuntamientos).
    """

    community: int
    insurance: int
    ibi: int

    @property
    def total(self) -> int:
        """Todo junto."""
        return self.community + self.insurance + self.ibi


def monthly_room_community() -> int:
    """Comunidad y suministros de un mes de cada habitación de un piso por habitaciones."""
    return _euros(KINDS[ROOM_KIND].community)


def monthly_costs(listing: Listing) -> MonthlyCosts:
    """Gastos de un mes del inmueble entero."""
    return MonthlyCosts(
        community=_euros(KINDS[listing.kind].community),
        insurance=round(reference_rent(listing) * INSURANCE_SHARE),
        ibi=round(cadastral_value(listing) * IBI_RATE / 12),
    )


def fiscal_value(listing: Listing, shares: int, paid: int) -> int:
    """Valor de unas participaciones para el Impuesto sobre el Patrimonio.

    El mayor entre el valor catastral y el precio de adquisición (art. 10.1 de la
    Ley 19/1991). Como el catastral es la mitad del mercado, casi siempre manda lo
    pagado.

    Args:
        shares: Participaciones que tiene.
        paid: Lo que pagó por ellas, sin impuestos ni gastos.
    """
    return max(cadastral_value(listing) * shares // SHARES, paid)


def is_dwelling(listing: Listing, level: str) -> bool:
    """Si un alquiler es de vivienda (art. 2 LAU): solo ese tiene la reducción del art. 23.2.

    No lo son los locales, garajes y trasteros, ni el alquiler de temporada de los
    nómadas digitales (art. 3 LAU), ni el vacacional (un alquiler turístico). Una habitación
    sí, si quien la alquila vive ahí (DGT V0412-2025): no en la de los nómadas.
    """
    kinds = HOME_KINDS | {ROOM_KIND}
    return listing.kind in kinds and level not in (VACATION_LEVEL, NOMAD_LEVEL)


def contract_reduction(
    listing: Listing,
    level: str,
    *,
    index: float,
    rent: int,
    last_rent: int,
    young: bool = False,
    day: date | None = None,
) -> float:
    """Reducción del art. 23.2 LIRPF de un contrato, fijada al firmarlo (Ley 12/2023).

    - 90 %: vivienda en zona de mercado residencial tensionado con un contrato nuevo
      cuya renta baja más de un 5 % de la última del anterior (`TENSE_RENT_CUT`).
    - 70 %: vivienda en zona tensionada que se alquila por primera vez, a un inquilino de
      18 a 35 años (`young`).
    - 60 %: vivienda rehabilitada (una reforma integral que pasa del 25 % del valor de la
      construcción, art. 41.1 RIRPF) en los dos años (24 días) anteriores al contrato
      (`Listing.rehab_until`, mirado el día de la firma, `day`).
    - 50 %: cualquier otro alquiler de vivienda.
    - 0: lo que no es vivienda (`is_dwelling`).

    «Por primera vez» se mira en el propio inmueble: sin contrato anterior apuntado
    (`last_rent` a 0; un inmueble que vuelve al mercado empieza de cero). Simplificación: no
    se mira si el casero del contrato anterior era el mismo. La escala del RDL 29/2026, sin
    convalidar, no se aplica (decisión del proyecto).
    """
    if not is_dwelling(listing, level):
        return 0.0
    if tense(index) and last_rent > 0 and rent < last_rent * (1 - TENSE_RENT_CUT):
        return TENSE_RENT_REDUCTION
    if tense(index) and last_rent == 0 and young:
        return YOUNG_RENT_REDUCTION
    if day is not None and listing.rehab_until is not None and day <= listing.rehab_until:
        return REHAB_REDUCTION
    return RENT_REDUCTION


def lease_reduction(listing: Listing, lease: Lease) -> float:
    """Reducción del contrato en vigor: la que se fijó al firmar, o la general si es de antes."""
    if lease.reduction is not None:
        return lease.reduction
    return RENT_REDUCTION if is_dwelling(listing, lease.level) else 0.0


#: Deducción canaria por alquilar una vivienda vacía (art. 16 del Decreto Legislativo
#: 1/2009): la vivienda tiene que haber imputado renta el periodo anterior (aquí, una semana
#: vacía, que es un año del IRPF) o alquilarse en los seis meses siguientes a comprarla sin
#: inquilino (seis días). El importe y el tope, en `bot.services.taxes`.
VACANT_BONUS_EMPTY_DAYS = 7
VACANT_BONUS_BOUGHT_DAYS = 6


def imputed_rent(listing: Listing, shares: int) -> int:
    """Renta imputada de un mes vacío (un día del bot) de unas participaciones (art. 85 LIRPF).

    El 1,1 % anual del valor catastral (`taxes.IMPUTED_RATE`), repartido en doce meses.
    """
    return round(cadastral_value(listing) * IMPUTED_RATE / 12 * shares / SHARES)


def amortization(paid: int) -> int:
    """Amortización de un mes sobre lo pagado por unas participaciones."""
    return round(paid * CONSTRUCTION_SHARE * AMORTIZATION_RATE / 12)


@dataclass(frozen=True, slots=True)
class PurchaseQuote:
    """Lo que cuesta comprar unas participaciones.

    Attributes:
        shares: Cuántas.
        price: Su valor de mercado.
        itp: ITP (al Estado).
        notary: Notaría, registro y gestoría (desaparece).
        notary_igic: IGIC de la notaría (al Estado, `SERVICES_IGIC`).
    """

    shares: int
    price: int
    itp: int
    notary: int
    notary_igic: int = 0

    @property
    def total(self) -> int:
        """Todo lo que sale del bolsillo."""
        return self.price + self.itp + self.notary + self.notary_igic


def purchase_quote(listing: Listing, shares: int, index: float = 1.0) -> PurchaseQuote:
    """Cuenta de una compra al índice de hoy (el índice sube después de comprar)."""
    if not 1 <= shares <= SHARES:
        raise ValueError("Las participaciones van de 1 a 100.")
    value = share_price(listing, index) * shares
    notary = round(value * NOTARY_RATE)
    return PurchaseQuote(
        shares=shares,
        price=value,
        itp=round(value * ITP_RATE),
        notary=notary,
        notary_igic=igic(notary, SERVICES_IGIC),
    )


@dataclass(frozen=True, slots=True)
class SaleQuote:
    """Lo que se cobra al vender unas participaciones al mercado.

    Attributes:
        shares: Cuántas.
        price: Su valor de mercado.
        fee: Comisión de la agencia (desaparece).
        arrears: Deudas del inmueble que le tocan (comunidad e IBI sin pagar):
            se descuentan del precio, porque la deuda va con el piso (art. 9.1.e
            de la Ley de Propiedad Horizontal).
        arrears_ibi: La parte de `arrears` que es IBI (va al Estado).
        cost: Lo que le costaron (precio, ITP y gastos: art. 35.1 LIRPF).
        gain: Ganancia patrimonial (puede ser negativa).
        gain_tax: IRPF de la ganancia, base del ahorro al 19 % (`capital_gain_tax`).
        fee_igic: IGIC de la comisión de la agencia (al Estado, `SERVICES_IGIC`).
    """

    shares: int
    price: int
    fee: int
    arrears: int
    arrears_ibi: int
    cost: int
    gain: int
    gain_tax: int
    fee_igic: int = 0

    @property
    def received(self) -> int:
        """Lo que llega al bolsillo."""
        return max(0, self.price - self.fee - self.fee_igic - self.arrears - self.gain_tax)


def sale_quote(
    listing: Listing,
    shares: int,
    owned: int,
    cost: int,
    *,
    index: float = 1.0,
    arrears: int = 0,
    arrears_ibi: int = 0,
    pool: int | None = None,
    value: int | None = None,
) -> SaleQuote:
    """Cuenta de una venta al índice de hoy.

    Args:
        shares: Participaciones que vende.
        owned: Participaciones que tiene (para repartir lo que le costaron).
        cost: Lo que le costaron todas las que tiene.
        arrears: Deudas del inmueble (las de las participaciones que tienen dueño).
        arrears_ibi: La parte de `arrears` que es IBI.
        pool: Participaciones que tienen todos los dueños juntos (`owned` si no se
            dice). La deuda se reparte entre ellas: quien lo tiene todo y vende de una
            en una paga lo mismo que vendiéndolo de golpe.
        value: Precio pactado de las participaciones (la oferta del fondo buitre); si
            no se dice, el de mercado al índice de hoy.
    """
    if not 1 <= shares <= owned <= SHARES:
        raise ValueError("No puedes vender más participaciones de las que tienes.")
    pool = max(owned, pool or owned)
    value = share_price(listing, index) * shares if value is None else value
    fee = round(value * SALE_FEE_RATE)
    fee_igic = igic(fee, SERVICES_IGIC)
    selling = fee + fee_igic
    debt = arrears * shares // pool
    debt_ibi = min(debt, arrears_ibi * shares // pool)
    debt = min(debt, value - selling)
    debt_ibi = min(debt_ibi, debt)
    part_cost = cost * shares // owned
    gain = value - selling - debt - part_cost
    return SaleQuote(
        shares=shares,
        price=value,
        fee=fee,
        arrears=debt,
        arrears_ibi=debt_ibi,
        cost=part_cost,
        gain=gain,
        gain_tax=min(capital_gain_tax(gain), value - selling - debt),
        fee_igic=fee_igic,
    )


#: Lo que pesan juntas en el índice de su barrio las unidades de sus calles (9.14.0): lo mismo
#: que sus nueve destacados. Así los destacados mueven el índice como siempre y comprar todo el
#: barrio, calles incluidas, lo sube el doble que comprar solo los nueve.
STREETS_WEIGHT = 1.0


@cache
def _featured_value(barrio: str) -> int:
    """Lo que valen los nueve destacados de un barrio."""
    return sum(base_price(item) for item in LISTINGS if item.barrio == barrio)


@cache
def _streets_value(barrio: str) -> int:
    """Lo que valen juntas las unidades de las calles de un barrio."""
    return sum(base_price(item) for item in STREET_UNITS if item.barrio == barrio)


def barrio_weight(listing: Listing) -> float:
    """Lo que pesa un inmueble en su barrio: su precio entre el de todos los destacados del
    barrio (los nueve juntos pesan 1).

    Las unidades de las calles pesan su precio entre el de todas las de su barrio, y juntas
    `STREETS_WEIGHT`. La obra nueva (y una planta de un edificio dividido) cuenta con los nueve
    y ella misma, así que nunca pesa más que todo el barrio. Lo construido en un solar (9.56.0)
    pesa como una unidad más de las calles: si contara como la obra nueva, cada vivienda de una
    torre pesaría lo que un destacado y el promotor inflaría su barrio solo con construir.
    """
    if listing.key in STREET_UNIT_BY_KEY or is_solar_unit(listing.key):
        return STREETS_WEIGHT * base_price(listing) / _streets_value(listing.barrio)
    total = _featured_value(listing.barrio)
    if listing.key not in LISTING_BY_KEY:
        total += base_price(listing)
    return base_price(listing) / total


def index_after(index: float, listing: Listing, shares: int, *, bought: bool) -> float:
    """Índice del barrio tras comprar o vender `shares` participaciones de `listing`.

    Comprar multiplica por `1 + INDEX_STEP × peso × shares/100` y vender divide por lo
    mismo: una compra y su venta dejan el índice exactamente igual. Sin redondear,
    para que el redondeo no lo suba un poco en cada vuelta.
    """
    factor = 1 + INDEX_STEP * barrio_weight(listing) * shares / SHARES
    moved = index * factor if bought else index / factor
    return min(INDEX_MAX, max(INDEX_MIN, moved))


def ownership_level(holdings: Iterable[tuple[Listing, int]]) -> float:
    """El índice al que tiende un barrio: el de lo que tienen comprado los miembros.

    Es el mismo producto que deja `index_after` comprando cada participación: si los
    miembros tienen medio barrio, se queda alrededor de un 5 % por encima y no vuelve a 1
    mientras lo sigan teniendo. Así un barrio acaparado se encarece y se tensiona.

    Args:
        holdings: `(inmueble, participaciones con dueño)` de cada inmueble del barrio.
    """
    level = 1.0
    for listing, shares in holdings:
        level *= 1 + INDEX_STEP * barrio_weight(listing) * shares / SHARES
    return min(INDEX_MAX, max(INDEX_MIN, level))


def index_next_day(index: float, level: float = 1.0) -> float:
    """El índice de mañana: va poco a poco hacia `level` (`ownership_level`)."""
    return index + (level - index) * INDEX_REVERSION


# -- El día a día de un inmueble --------------------------------------------------------

#: Cómo se elige al inquilino: la gestora (al azar, como siempre) o el dueño entre los
#: candidatos que van llegando (`PICK_SELF`).
PICK_AGENCY = "gestora"
PICK_SELF = "yo"
#: Candidatos esperando a la vez en un piso vacío, y los días que espera cada uno.
MAX_CANDIDATES = 3
CANDIDATE_DAYS = 3


@dataclass(frozen=True, slots=True)
class Lease:
    """Lo que pasa en un inmueble con dueño.

    Attributes:
        status: `VACANT`, `RENTED`, `DEFAULTING`, `EVICTING` o `SQUATTED`.
        level: Clave de `RENT_LEVELS` que pide el dueño (vale para el próximo
            contrato: a mitad de uno no se puede subir, art. 18 LAU).
        tenant: Clave del inquilino (si hay).
        rent: Renta del contrato en vigor, Y$ al mes.
        until: Último día del contrato.
        since: Desde qué día está en este estado.
        unpaid: Meses que debe el inquilino.
        eviction_at: Día de la vista del desahucio.
        arrears: Gastos sin pagar del inmueble entero (comunidad, seguro e IBI).
        arrears_ibi: La parte de `arrears` que se debe al Estado: el IBI y, desde la
            8.1.0, el impuesto de primas y el IGIC de las reparaciones que no se pudieron
            pagar (al cobrarse más tarde se apuntan como IBI).
        member: El miembro que vive de alquiler, si el inquilino es del servidor.
        bono: Si ese miembro cobra el Bono Alquiler Joven.
        license: Si tiene licencia de vivienda vacacional.
        last_rent: Última renta del contrato anterior (0 si no lo hubo): para la
            reducción del 90 % de quien la rebaja en zona tensionada.
        reduction: Reducción del art. 23.2 del contrato en vigor, fijada al firmarlo;
            `None` en los de antes de la 8.0.0 (la general, `contract_reduction`).
        pick: Quién elige al próximo inquilino (`PICK_AGENCY` o `PICK_SELF`).
        insured: Si tiene contratado el seguro de impago (`covered`).
        claims: Meses que el seguro ha pagado en el impago de ahora.
        solvent: Si el miembro de alquiler pasó el estudio de solvencia al firmar (su
            nómina llegaba a tres veces la renta): el seguro de impago le cubre.
        bonus: Si el contrato en vigor da la deducción canaria por alquilar una vivienda
            vacía (art. 16 del Decreto Legislativo 1/2009) y aún no se ha aplicado.
        works_until: Último día de las obras, si está «En obras» (`OBRAS`).
        mood: Humor del inquilino, de 0 a 100 (`MOOD_BASE` al firmar): lo que se arregla
            bien lo sube y lo que se deja o se arregla mal lo baja (`mood_factor`).
        home_claim_until: Hasta qué día la prima del seguro del hogar va recargada por un
            parte (`HOME_CLAIM_SURCHARGE`).
        rating: Nota media de las últimas reseñas del vacacional (0 si no hay).
        reviews: Cuántas reseñas cuentan en esa media (las últimas `REVIEW_WINDOW`).
        license_until: Último día de la licencia vacacional (se renueva si se puede).
        license_denied_until: Hasta qué día no se puede volver a pedir la licencia (la
            comunidad votó que no).
        term: Días que dura el próximo contrato (`TERMS`), como `level`: 0 es el de serie
            de su nivel (`contract_months`).
        leaving: Por qué se va el inquilino el día `until` en vez de renovar: `""` (renueva
            si quiere), `LEAVE_NO_RENEW`, `LEAVE_EXTENDED` (ya tuvo su prórroga),
            `LEAVE_KEYS` (le compraron las llaves) o `LEAVE_NEED` (art. 9.3 LAU).
        keys_ask: Lo que pide el inquilino por irse tras rechazar una oferta (0 si nada).
        keys_wait: Hasta qué día no quiere volver a hablar de irse (ni acepta su precio).
        signed: Día en que se firmó el contrato en vigor (`None` en los de antes de la 9.23).
    """

    status: str = VACANT
    level: str = "referencia"
    tenant: str | None = None
    rent: int = 0
    until: date | None = None
    since: date | None = None
    unpaid: int = 0
    eviction_at: date | None = None
    arrears: int = 0
    arrears_ibi: int = 0
    member: int | None = None
    bono: bool = False
    license: bool = False
    last_rent: int = 0
    reduction: float | None = None
    pick: str = PICK_AGENCY
    insured: bool = False
    claims: int = 0
    solvent: bool = False
    bonus: bool = False
    works_until: date | None = None
    mood: int = 70
    home_claim_until: date | None = None
    rating: float = 0.0
    reviews: int = 0
    license_until: date | None = None
    license_denied_until: date | None = None
    term: int = 0
    leaving: str = ""
    keys_ask: int = 0
    keys_wait: date | None = None
    signed: date | None = None


@dataclass(frozen=True, slots=True)
class Event:
    """Un imprevisto del día.

    Attributes:
        key: Clave estable (para las noticias y los logros).
        cost: Lo que cuesta al inmueble entero, en Y$ (0 si no cuesta nada).
        tenant: El inquilino del que va (el candidato que llega, `candidato`).
    """

    key: str
    cost: int = 0
    tenant: str | None = None


@dataclass(frozen=True, slots=True)
class EventKind:
    """Un tipo de imprevisto, con su probabilidad diaria y su coste.

    Attributes:
        key: Clave estable.
        chance: Probabilidad diaria.
        community_months: El coste en meses de comunidad (la derrama, la ITE).
        euros: El coste en euros, `(mínimo, máximo)` (las averías).
        kinds: Tipos de inmueble a los que les puede pasar (vacío: a todos).
        worn: Si el desgaste del inquilino lo hace más probable (`Tenant.wear`).
    """

    key: str
    chance: float
    community_months: int = 0
    euros: tuple[int, int] = (0, 0)
    kinds: frozenset[str] = frozenset()
    worn: bool = False


#: Lo que tiene instalaciones que se rompen (las viviendas, sus habitaciones y los negocios
#: con techo).
_HOMES = frozenset(
    {"estudio", "bajo", "piso2", "piso3", "atico", "chalet", "edificio", "terrera", "cueva"}
    | {"finca", "casona", "bungalow", "duplex", "loft", "local", "nave", "habitacion"}
)
#: Lo que está en una comunidad de propietarios (derramas, ITE y vecinos).
_BLOCKS = frozenset(
    {"estudio", "bajo", "piso2", "piso3", "atico", "edificio", "duplex", "loft", "local"}
    | {"bungalow"}
)

#: Imprevistos (los textos están en el cog). Una avería cada tres meses del juego
#: y una derrama al año, más o menos.
EVENTS: tuple[EventKind, ...] = (
    EventKind("averia", 0.010, euros=(120, 450), kinds=_HOMES, worn=True),
    EventKind("humedades", 0.004, euros=(300, 900), kinds=_HOMES, worn=True),
    EventKind("derrama", 0.004, community_months=15, kinds=_BLOCKS),
    EventKind("ite", 0.002, community_months=30, kinds=_BLOCKS),
    EventKind("vecino", 0.010, kinds=_BLOCKS),
    EventKind("cerradura", 0.004, euros=(80, 200)),
)
#: Lo que se arregla (y paga IGIC en la factura): no la derrama ni la ITE, que son cuotas
#: de la comunidad.
REPAIR_EVENTS = frozenset({"averia", "humedades", "cerradura"})


@dataclass(frozen=True, slots=True)
class DayOutcome:
    """Lo que pasa un día en un inmueble.

    Attributes:
        lease: Cómo queda.
        rent: Renta cobrada del inmueble entero (0 si no paga nadie).
        costs: Gastos del mes del inmueble entero (sin los imprevistos).
        extra: Coste de los imprevistos del día.
        events: Imprevistos y cambios de estado del día, para las noticias
            (`firma`, `renueva`, `se_va`, `impago`, `paga`, `okupa`, `desahucio`,
            `candidato`, `seguro`, los finales de contrato de la 9.23 —`prorroga`,
            `no_renovado`, `sale_llaves`, `sale_necesidad`— y las claves de `EVENTS`).
        insurer: Lo que paga hoy el seguro de impago por la renta que no se cobra.
    """

    lease: Lease
    rent: int
    costs: MonthlyCosts
    extra: int
    events: tuple[Event, ...]
    insurer: int = 0


# -- Inquilinos: cómo es cada uno ---------------------------------------------------

#: Riesgo de impago (ya relativo a la media) por debajo del cual un inquilino es de riesgo
#: «bajo», y por encima del cual es «alto». El seguro de impago no cubre a los de riesgo alto.
RISK_LOW = 0.75
RISK_HIGH = 1.25


@dataclass(frozen=True, slots=True)
class TenantFactors:
    """Lo que un inquilino cambia del día a día, relativo a la media de su grupo.

    Attributes:
        risk: Multiplica la probabilidad de dejar de pagar.
        stay: Multiplica la de irse antes y la de no renovar.
        wear: Multiplica las averías y las humedades.
    """

    risk: float = 1.0
    stay: float = 1.0
    wear: float = 1.0


NEUTRAL = TenantFactors()


def _group(kind: str) -> str:
    """El grupo de inquilinos de un tipo de inmueble: cada negocio, las habitaciones o las
    personas."""
    return kind if kind in BUSINESS_KINDS or kind == ROOM_KIND else "vivienda"


def tenant_pool(level: str, kind: str) -> tuple[Tenant, ...]:
    """Los inquilinos que firmarían un contrato de `level` en un inmueble de tipo `kind`."""
    return tuple(t for t in TENANTS if t.rents(level, kind))


@cache
def _pool_means(level: str, group: str) -> tuple[float, float, float]:
    kind = "piso2" if group == "vivienda" else group
    pool = tenant_pool(level, kind)
    if not pool:
        return (1.0, 1.0, 1.0)
    n = len(pool)
    return (
        sum(t.risk for t in pool) / n,
        sum(t.stay for t in pool) / n,
        sum(t.wear for t in pool) / n,
    )


def tenant_factors(tenant: str | None, level: str, kind: str) -> TenantFactors:
    """Cómo cambia el día a día un inquilino, dividido por la media de los que podían salir.

    La gestora elige al azar entre los que encajan (`_tenant_for`), así que con ella la
    media de cada factor es 1: los alquileres de quien no elige siguen como estaban. Un
    inquilino que no está en el catálogo (o un miembro) es neutro.
    """
    item = TENANT_BY_KEY.get(tenant or "")
    if item is None:
        return NEUTRAL
    risk, stay, wear = _pool_means(level, _group(kind))
    return TenantFactors(risk=item.risk / risk, stay=item.stay / stay, wear=item.wear / wear)


def risk_tier(factors: TenantFactors) -> str:
    """`bajo`, `medio` o `alto`: lo que enseña la tarjeta del candidato."""
    if factors.risk < RISK_LOW:
        return "bajo"
    if factors.risk > RISK_HIGH:
        return "alto"
    return "medio"


def is_young(tenant: str | None) -> bool:
    """Si un inquilino del catálogo tiene de 18 a 35 años (los negocios no tienen edad)."""
    item = TENANT_BY_KEY.get(tenant or "")
    return item is not None and YOUNG_AGES[0] <= item.age <= YOUNG_AGES[1]


def _tenant_for(level: str, kind: str, rng: random.Random) -> str:
    candidates = [t.key for t in TENANTS if t.rents(level, kind)]
    return rng.choice(candidates)


# -- Seguro de impago y desahucio -----------------------------------------------------

#: Prima del seguro de impago al mes con la renta de referencia: un 4 % de la renta (en la
#: vida real, del 3 al 6 % de la renta de un año). Sube con lo que se deja de pagar en
#: cada nivel de renta (`premium_rate`). Desaparece (la aseguradora no existe) y lleva el
#: impuesto de primas (`IPS_RATE`).
PREMIUM_RATE = 0.04
#: Paga la renta desde el segundo mes sin pagar (el primero es la franquicia) y hasta 12
#: meses por impago.
COVER_FROM = 2
COVER_MONTHS = 12
#: Abogado y procurador del desahucio, que son obligatorios (art. 23 LEC): tres meses de
#: renta, entre 3.000 y 15.000 Y$, más el IGIC de los servicios. Si el seguro de impago
#: cubre al inquilino, los paga él.
EVICTION_FEE_MONTHS = 3
EVICTION_FEE_MIN = 3_000
EVICTION_FEE_MAX = 15_000


#: Cómo sube la prima con el impago de cada nivel (exponente sobre la proporción de impago).
#: Medido con el código real (60 caseros, 1.500 días, desahucio al tercer mes): la aseguradora
#: paga, con los abogados, un 81 % de lo que cobra con la renta asequible, un 75 % con la de
#: referencia y un 83 % con la de mercado. Gana algo, como en la vida real: asegurarse no es
#: un negocio, quita el susto.
PREMIUM_CURVE = 0.85


def premium_rate(level: str) -> float:
    """Prima mensual del seguro de impago sobre la renta, según lo que se impaga con ese nivel."""
    base = RENT_LEVELS["referencia"].default
    rent_level = RENT_LEVELS.get(level, RENT_LEVELS["referencia"])
    return PREMIUM_RATE * (rent_level.default / base) ** PREMIUM_CURVE


def insurable(listing: Listing, lease: Lease) -> bool:
    """Si la aseguradora cubre el contrato en vigor (aunque no esté contratado el seguro).

    Solo vivienda habitual (ni temporada, ni vacacional, ni negocios) y solo inquilinos
    que pasan el estudio de solvencia: los del catálogo de riesgo bajo o medio y los
    miembros cuya nómina llegaba a tres veces la renta al firmar.
    """
    if not is_dwelling(listing, lease.level):
        return False
    if lease.member is not None:
        return lease.solvent
    if lease.tenant is None:
        return False
    return risk_tier(tenant_factors(lease.tenant, lease.level, listing.kind)) != "alto"


def covered(listing: Listing, lease: Lease) -> bool:
    """Si el seguro de impago está contratado y cubre a este inquilino."""
    return lease.insured and insurable(listing, lease)


def premium(listing: Listing, lease: Lease) -> int:
    """Prima de un mes del seguro de impago (sin el impuesto de primas), o 0 si no cubre."""
    if not covered(listing, lease) or lease.status not in (RENTED, DEFAULTING, EVICTING):
        return 0
    return round(lease.rent * premium_rate(lease.level))


def insure_month(listing: Listing, lease: Lease) -> tuple[int, Lease]:
    """Lo que paga hoy el seguro por la renta que no llega, y el contrato con ese mes contado.

    Paga desde el segundo mes de impago y mientras dura el juicio, hasta `COVER_MONTHS`.
    """
    if not covered(listing, lease) or lease.status not in (DEFAULTING, EVICTING):
        return 0, lease
    if lease.claims >= COVER_MONTHS:
        return 0, lease
    if lease.status == DEFAULTING and lease.unpaid < COVER_FROM:
        return 0, lease
    return lease.rent, replace(lease, claims=lease.claims + 1)


def eviction_fee(rent: int) -> int:
    """Abogado y procurador de un desahucio (sin IGIC)."""
    return min(EVICTION_FEE_MAX, max(EVICTION_FEE_MIN, rent * EVICTION_FEE_MONTHS))


# -- Duración del contrato y recuperar el piso (9.23.0, `docs/pisos-llaves.md`) ----------

#: Lo que puede durar un contrato de vivienda o de local, de 1 a 5 años (a un día = un mes).
#: En la ley, uno de menos de 5 años se prorroga cada año a voluntad del inquilino hasta los
#: 5 (art. 9.1 LAU); aquí lo firmado es lo que dura, o elegir no cambiaría nada.
TERMS = (12, 24, 36, 48, 60)
TERM_DEFAULT = 60
#: Con 1 año, lo que baja encontrar inquilino y lo que sube el impago (en proporción, menos
#: con más años): `docs/pisos_sim.py --duraciones`.
TERM_FIND_CUT = 0.4
TERM_RISK_UP = 0.3
#: Por qué se va el inquilino al acabar el contrato (`Lease.leaving`).
LEAVE_NO_RENEW = "no_renueva"
LEAVE_EXTENDED = "prorroga"
LEAVE_KEYS = "llaves"
LEAVE_NEED = "necesidad"
#: La prórroga de la Ley de Vivienda en zona tensionada (art. 10.2 LAU, Ley 12/2023): la pide
#: con esta probabilidad un inquilino del catálogo (un miembro, siempre), una vez y por un año.
TENSE_EXTENSION = 0.5
EXTENSION_DAYS = 12
#: Comprar las llaves: medio mes de renta por cada 12 días que le quedan (un mes como
#: mínimo), y lo que tarda en irse una vez aceptado (sigue pagando hasta entonces).
KEYS_DAYS_PER_MONTH = 24
KEYS_MOVE_DAYS = 2
#: Lo que multiplica la oferta generosa al precio justo.
KEYS_GENEROUS = 1.5
#: Si los alquileres han subido desde que firmó, el inquilino pide además esta parte de lo
#: que le costaría de más alquilar uno igual hoy hasta que acabe su contrato. Así echarlo para
#: subirle la renta solo compensa si el barrio se ha disparado (`docs/pisos_sim.py
#: --duraciones`, 48 días por delante: con la justa, a la par si ha subido un 15 % y 5,7 meses
#: de renta si un 40 %; con la generosa, nunca). Sin esto, con un 20 % se ganaban 5 meses.
KEYS_MARKET_SHARE = 0.5
#: Lo más que se puede ofrecer a un miembro por las llaves (se escribe a mano).
KEYS_MAX = 10_000_000
#: El precio de verdad de cada inquilino es el justo por un factor lognormal, fijo para cada
#: contrato: con la oferta justa acepta el 60 % de las veces y con la generosa, el 90 %.
KEYS_LOG_MU = -0.1
KEYS_LOG_SIGMA = 0.394
#: Lo que le sienta mal que le pidan irse si dice que no, y los días que no quiere volver a
#: hablar del tema (su contraoferta vale mientras tanto).
KEYS_MOOD_HIT = 10
KEYS_WAIT_DAYS = 7
#: Un inquilino de riesgo alto al que se le ofrece poco (por las buenas o la justa) se
#: atrinchera con esta probabilidad: deja de pagar y toca desahuciarlo.
TRENCH_CHANCE = 0.10
#: Fama de casero: cuántos pisos ha recuperado comprando llaves en los últimos `FAME_DAYS`
#: días. Cada uno encarece el siguiente un `FAME_STEP` y espanta a los candidatos
#: (`FAME_FIND_CUT` por punto, hasta `FAME_FIND_MIN`).
FAME_DAYS = 30
FAME_STEP = 0.2
FAME_FIND_CUT = 0.1
FAME_FIND_MIN = 0.5
#: Necesidad del arrendador (art. 9.3 LAU): tras 12 días (un año) de contrato, el inquilino
#: se va en 2 (dos meses de preaviso) y el dueño tiene que vivir ahí 12 días; si se va
#: antes, paga 1 mes de renta por cada 12 días que le quedaban al contrato.
NEED_AFTER_DAYS = 12
NEED_NOTICE_DAYS = 2
NEED_STAY_DAYS = 12


def days_left(lease: Lease, day: date) -> int:
    """Días que le quedan al contrato en vigor (1 como mínimo, si lo hay)."""
    return max(1, (lease.until - day).days) if lease.until is not None else 0


def recoverable(lease: Lease) -> bool:
    """Si hay a quién pedirle que se vaya: alguien al día que no se va ya por otra razón."""
    return (
        lease.status == RENTED
        and (lease.tenant is not None or lease.member is not None)
        and lease.level != VACATION_LEVEL
        and lease.leaving not in (LEAVE_KEYS, LEAVE_NEED)
    )


def _round_up(amount: float) -> int:
    return int(math.ceil(amount / 100 - 1e-9)) * 100


def keys_fair(rent: int, left: int, stay: float, mood: int, fame: int, market_rent: int = 0) -> int:
    """El precio justo de las llaves, en Y$ (a cientos, hacia arriba).

    Args:
        left: Días que le quedan al contrato.
        stay: Lo que el inquilino tiende a irse (`TenantFactors.stay`, 1 en un miembro): el
            que se iría solo pide menos.
        mood: Su humor. Contento pide más; enfadado no pide menos, o dejarle las averías sin
            arreglar sería la forma de echarlo.
        fame: La fama del casero (`fame`).
        market_rent: Lo que pide hoy un piso igual (`asking_rent`): si es más que su renta,
            pide `KEYS_MARKET_SHARE` de la diferencia por cada mes que le queda.
    """
    months = max(1.0, left / KEYS_DAYS_PER_MONTH)
    attachment = min(1.5, max(0.7, 1 / max(stay, 0.01)))
    happy = 1 + max(0, mood - MOOD_BASE) / 100
    moving = KEYS_MARKET_SHARE * max(0, market_rent - rent) * left
    return _round_up((rent * months * attachment * happy + moving) * (1 + FAME_STEP * fame))


def keys_odds(offer: int, fair: int) -> float:
    """Probabilidad de que acepte `offer` cuando lo justo es `fair` (la que sale en el botón)."""
    if offer <= 0 or fair <= 0:
        return 0.0
    z = (math.log(offer / fair) - KEYS_LOG_MU) / KEYS_LOG_SIGMA
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def keys_reserve(fair: int, seed: str) -> int:
    """Lo que pide de verdad por irse: el justo por un factor fijo para cada contrato.

    `seed` sale del contrato (inmueble, inquilino y día de la firma): volver a preguntar no
    cambia lo que pide, aunque sí cambia el justo (le quedan menos días, sube tu fama).
    """
    z = random.Random(seed).gauss(0, 1)
    return _round_up(fair * math.exp(KEYS_LOG_MU + KEYS_LOG_SIGMA * z))


def free_odds(stay: float, left: int, fame: int) -> float:
    """Probabilidad de que se vaya por las buenas, sin cobrar nada.

    Más si ya pensaba irse (`stay` alto) o le queda poco contrato; nunca si el casero
    tiene fama de echar a la gente.
    """
    if fame > 0:
        return 0.0
    return min(0.35, max(0.05, 0.05 + 0.10 * stay + 0.15 * (1 - min(left, 60) / 60)))


def fame_find(fame: int) -> float:
    """Lo que la fama del casero multiplica encontrar inquilino."""
    return max(FAME_FIND_MIN, 1 - FAME_FIND_CUT * fame)


def need_fine(rent: int, left: int) -> int:
    """La indemnización del art. 9.3 LAU si el dueño no vive ahí lo que debe: 1 mes de renta
    por cada 12 días (año) que le quedaban al contrato, y 1 como mínimo."""
    return rent * max(1, math.ceil(left / 12))


def need_problem(listing: Listing, lease: Lease, day: date) -> str | None:
    """Por qué no se puede pedir el piso para vivir en él (art. 9.3 LAU), o `None`.

    Solo mira el contrato; que sea todo suyo y no viva en otro sitio lo mira el repositorio.
    """
    if not recoverable(lease):
        return "No hay a quién pedírselo."
    if listing.kind not in HOME_KINDS:
        return "La necesidad es para vivir: en esto no se vive."
    if lease.level == NOMAD_LEVEL:
        return "Es un contrato de temporada: ese acaba solo."
    start = lease.signed or lease.since
    if start is not None and (day - start).days < NEED_AFTER_DAYS:
        ready = start + timedelta(days=NEED_AFTER_DAYS)
        return f"Hasta el {ready:%d/%m} no lleva un año de contrato (art. 9.3 LAU)."
    return None


@dataclass(frozen=True, slots=True)
class KeysQuote:
    """Lo que pediría hoy un inquilino del catálogo por irse, y lo que sale en los botones.

    Attributes:
        fair: El precio justo (`keys_fair`).
        reserve: Lo que pide de verdad (`keys_reserve`): no se enseña.
        free: Probabilidad de que se vaya por las buenas (`free_odds`).
        risk: Su riesgo de impago (`risk_tier`): uno alto puede atrincherarse.
        fame: La fama del casero.
        left: Días que le quedan al contrato.
    """

    fair: int
    reserve: int
    free: float
    risk: str
    fame: int
    left: int

    def odds(self, offer: str) -> float:
        """La probabilidad de que acepte cada oferta (la que sale en su botón)."""
        if offer == KEYS_FREE:
            return self.free
        return keys_odds(keys_offer_amount(offer, self.fair), self.fair)


def keys_quote(
    listing: Listing, lease: Lease, day: date, fame: int, seed: str, index: float = 1.0
) -> KeysQuote:
    """Lo que pediría hoy el inquilino del catálogo de `lease` por irse (`KeysQuote`).

    `seed` identifica el contrato (inmueble, inquilino y día de la firma): lo que pide de
    verdad no cambia por volver a preguntar. `index` es el del barrio: si los alquileres han
    subido, pide más.
    """
    factors = tenant_factors(lease.tenant, lease.level, listing.kind)
    left = days_left(lease, day)
    level = RENT_LEVELS.get(lease.level, RENT_LEVELS["referencia"])
    market_rent = asking_rent(listing, level, index)
    fair = keys_fair(lease.rent, left, factors.stay, lease.mood, fame, market_rent)
    return KeysQuote(
        fair=fair,
        reserve=keys_reserve(fair, seed),
        free=free_odds(factors.stay, left, fame),
        risk=risk_tier(factors),
        fame=fame,
        left=left,
    )


#: Las tres ofertas por las llaves, en el orden de los botones.
KEYS_FREE = "buenas"
KEYS_FAIR = "justa"
KEYS_RICH = "generosa"
KEYS_OFFERS = (KEYS_FREE, KEYS_FAIR, KEYS_RICH)


def keys_offer_amount(offer: str, fair: int) -> int:
    """Lo que se ofrece con cada botón."""
    if offer == KEYS_FREE:
        return 0
    return fair if offer == KEYS_FAIR else _round_up(fair * KEYS_GENEROUS)


@dataclass(frozen=True, slots=True)
class KeysAnswer:
    """Lo que contesta un inquilino del catálogo a una oferta por las llaves.

    Attributes:
        accepted: Si se va.
        paid: Lo que se le paga si se va (0 por las buenas).
        ask: Su contraoferta si no se va (0 si se atrinchera).
        trench: Si se atrinchera: deja de pagar.
    """

    accepted: bool
    paid: int = 0
    ask: int = 0
    trench: bool = False


def keys_answer(
    offer: str, fair: int, reserve: int, free: float, risk: str, rng: random.Random
) -> KeysAnswer:
    """Si acepta la oferta: por las buenas con `free` de probabilidad; con dinero, si llega
    a lo que pide de verdad (`reserve`). Si no, contraoferta con ese precio, salvo que sea
    de riesgo alto, le hayan ofrecido poco y se atrinchere (`TRENCH_CHANCE`)."""
    amount = keys_offer_amount(offer, fair)
    if offer == KEYS_FREE:
        if rng.random() < free:
            return KeysAnswer(True)
    elif amount >= reserve:
        return KeysAnswer(True, paid=amount)
    if risk == "alto" and offer != KEYS_RICH and rng.random() < TRENCH_CHANCE:
        return KeysAnswer(False, trench=True)
    return KeysAnswer(False, ask=reserve)


def keys_refused(lease: Lease, day: date, answer: KeysAnswer) -> Lease:
    """El contrato tras decir que no: humor abajo, su precio y unos días sin hablar del tema.

    Si se atrinchera, deja de pagar desde hoy (el impago de siempre).
    """
    lease = replace(
        lease, mood=max(0, lease.mood - KEYS_MOOD_HIT), keys_ask=answer.ask,
        keys_wait=day + timedelta(days=KEYS_WAIT_DAYS),
    )  # fmt: skip
    if answer.trench:
        lease = replace(lease, status=DEFAULTING, since=day, unpaid=1)
    return lease


def leaves_in(lease: Lease, day: date, why: str, days: int) -> Lease:
    """El contrato de quien se va en `days` días por `why` (`LEAVE_KEYS` o `LEAVE_NEED`).

    Sigue pagando hasta entonces; si el contrato ya acababa antes, se va ese día.
    """
    until = day + timedelta(days=days)
    if lease.until is not None and lease.until < until:
        until = lease.until
    return replace(lease, until=until, leaving=why, keys_ask=0, keys_wait=None)


# -- Noticias de barrio ---------------------------------------------------------------

#: Probabilidad de que un barrio sin noticia salga en las de un lunes, y cuántas salen como
#: mucho a la vez. Cada una dura una semana.
NEWS_CHANCE = 0.12
NEWS_MAX = 3
NEWS_DAYS = 7


def news_fits(news: BarrioNews, barrio: str, day: date) -> bool:
    """Si una noticia puede salir en un barrio ese día (tipo de barrio, isla y mes)."""
    spec = BARRIO_BY_KEY[barrio]
    return (
        (not news.themes or spec.theme in news.themes)
        and (not news.islands or spec.island in news.islands)
        and (not news.months or day.month in news.months)
    )


def monday_news(day: date, busy: set[str], rng: random.Random) -> list[tuple[str, str]]:
    """Las noticias de barrio de un lunes: `(barrio, noticia)`.

    Args:
        busy: Barrios que aún tienen una noticia en marcha (no salen en otra).
    """
    out: list[tuple[str, str]] = []
    for barrio in sorted(BARRIO_BY_KEY):
        if len(out) >= NEWS_MAX:
            break
        if barrio in busy or rng.random() >= NEWS_CHANCE:
            continue
        pool = [n for n in BARRIO_NEWS if news_fits(n, barrio, day)]
        if not pool:
            continue
        news = rng.choices(pool, weights=[n.weight for n in pool])[0]
        out.append((barrio, news.key))
    return out


def find_chance(
    listing: Listing, level: RentLevel, day: date, news: BarrioNews | None = None
) -> float:
    """Probabilidad de que un inmueble vacío encuentre inquilino (o un candidato) hoy.

    La vacacional depende de la temporada (`season_factor`) y los nómadas, del tipo de
    barrio (`NOMAD_DEMAND`): buscan playa y fibra, no un polígono. La noticia del barrio,
    si la hay, la sube o la baja.
    """
    find = level.find
    if level.key == VACATION_LEVEL:
        find *= season_factor(day)
    elif level.key == NOMAD_LEVEL:
        find *= NOMAD_DEMAND[BARRIO_BY_KEY[listing.barrio].theme]
    if news is not None:
        find *= news.find
        if level.key == VACATION_LEVEL:
            find *= news.vacation
        elif level.key == NOMAD_LEVEL:
            find *= news.nomad
    return min(1.0, find)


def contract_months(listing: Listing, level: RentLevel, term: int = 0) -> int:
    """Lo que dura un contrato: el que eligió el dueño (`term`) o el de su nivel.

    Los nómadas (temporada) y el vacacional tienen su duración y no se elige. La habitación
    no pasa de `ROOM_MONTHS`: se rige por el Código Civil y no por la LAU (criterio
    mayoritario de las Audiencias), sin las prórrogas obligatorias de cinco años.
    """
    months = term if term and level.months == TERM_DEFAULT else level.months
    return min(months, ROOM_MONTHS) if listing.kind == ROOM_KIND else months


def term_choices(listing: Listing, level: RentLevel) -> tuple[int, ...]:
    """Las duraciones que puede elegir el dueño con esa renta (ninguna si es fija)."""
    if level.months != TERM_DEFAULT or listing.kind == ROOM_KIND:
        return ()
    return TERMS


def term_factors(listing: Listing, level: RentLevel, term: int) -> tuple[float, float]:
    """`(encontrar inquilino, impago)` que multiplica firmar más corto que cinco años.

    Va en proporción a lo que le falta para los 60 días: con 1 año, `1 - TERM_FIND_CUT` y
    `1 + TERM_RISK_UP`; con 5, nada. Quien busca estabilidad no firma un año, y quien lo
    firma suele ser quien peor paga.
    """
    if not term_choices(listing, level):
        return 1.0, 1.0
    short = (TERM_DEFAULT - contract_months(listing, level, term)) / (TERM_DEFAULT - TERMS[0])
    return 1 - TERM_FIND_CUT * short, 1 + TERM_RISK_UP * short


def sign(listing: Listing, lease: Lease, tenant: str, day: date, index: float) -> Lease:
    """El contrato que firma un inquilino del catálogo con la renta que pide el inmueble.

    Lo usan la gestora (`advance_day`) y el dueño que elige entre los candidatos.
    """
    level = RENT_LEVELS.get(lease.level, RENT_LEVELS["referencia"])
    rent = asking_rent(listing, level, index)
    if level.key == VACATION_LEVEL and superhost(lease.rating, lease.reviews):
        rent = round(rent * (1 + SUPERHOST_PREMIUM), -1)
    return replace(
        lease,
        status=RENTED,
        tenant=tenant,
        rent=rent,
        until=day + timedelta(days=contract_months(listing, level, lease.term)),
        since=day,
        signed=day,
        unpaid=0,
        claims=0,
        mood=MOOD_BASE,
        leaving="",
        keys_ask=0,
        keys_wait=None,
        reduction=contract_reduction(
            listing, level.key, index=index, rent=rent, last_rent=lease.last_rent,
            young=is_young(tenant), day=day,
        ),
    )  # fmt: skip


def _vacated(lease: Lease, day: date) -> Lease:
    """El inmueble se queda vacío: sin inquilino, ni contrato, ni impago que cubrir."""
    return replace(
        lease, status=VACANT, tenant=None, rent=0, until=None, since=day, unpaid=0,
        eviction_at=None, claims=0, last_rent=lease.rent, bonus=False, leaving="", keys_ask=0,
        keys_wait=None, signed=None,
    )  # fmt: skip


def member_gone(lease: Lease, day: date) -> Lease:
    """El piso que deja un miembro de alquiler, por lo que sea: vacío y sin contrato."""
    return replace(
        lease, status=VACANT, member=None, bono=False, rent=0, last_rent=lease.rent, until=None,
        since=day, unpaid=0, eviction_at=None, leaving="", keys_ask=0, keys_wait=None,
        signed=None,
    )  # fmt: skip


def contract_end(
    listing: Listing, lease: Lease, day: date, index: float, rng: random.Random,
    renew: float,
) -> tuple[Lease, Event]:  # fmt: skip
    """El día que acaba un contrato de un inquilino del catálogo: renueva, se va o prorroga.

    Si el dueño no quiere renovar (`Lease.leaving`), se va; en zona tensionada pide antes,
    una vez, la prórroga de la Ley de Vivienda (art. 10.2 LAU, Ley 12/2023), con
    `TENSE_EXTENSION` de probabilidad. Si no se ha dicho nada, renueva con probabilidad
    `renew` por otro contrato de la duración elegida ese día, con la misma renta.
    """
    tenant = lease.tenant
    if lease.leaving == LEAVE_NO_RENEW and tense(index) and rng.random() < TENSE_EXTENSION:
        extended = replace(
            lease, until=day + timedelta(days=EXTENSION_DAYS), leaving=LEAVE_EXTENDED
        )
        return extended, Event("prorroga", tenant=tenant)
    if lease.leaving:
        key = {LEAVE_KEYS: "sale_llaves", LEAVE_NEED: "sale_necesidad"}.get(
            lease.leaving, "no_renovado"
        )
        return _vacated(lease, day), Event(key, tenant=tenant)
    if rng.random() < renew:
        level = RENT_LEVELS.get(lease.level, RENT_LEVELS["referencia"])
        months = contract_months(listing, level, lease.term)
        return replace(lease, until=day + timedelta(days=months)), Event("renueva")
    return _vacated(lease, day), Event("se_va")


def advance_day(
    listing: Listing,
    lease: Lease,
    day: date,
    index: float,
    rng: random.Random,
    news: BarrioNews | None = None,
    boost: float = 1.0,
) -> DayOutcome:
    """Un mes de la vida de un inmueble con dueño (un día del bot).

    Orden: imprevistos, desahucio que llega a su fecha, el inquilino paga o no,
    se va, renueva o deja de pagar, y si está vacío, se busca inquilino (o entran
    okupas). La renta solo se cobra si hay inquilino al día al empezar. Cómo es el
    inquilino (`tenant_factors`) y la noticia del barrio cambian las probabilidades. Si
    el dueño elige al inquilino, lo que llega es un candidato (`candidato`) y el piso
    sigue vacío. Al final, el seguro de impago paga lo que toque (`insure_month`).

    Args:
        lease: Cómo estaba ayer.
        day: El día que se cierra.
        index: Índice del barrio (para la renta de un contrato nuevo).
        rng: Azar inyectable.
        news: La noticia del barrio en marcha, si hay.
        boost: Lo que multiplica encontrar inquilino por otras razones: la nota de las
            reseñas del vacacional (`rating_factor`) o la fibra para los nómadas.
    """
    level = RENT_LEVELS.get(lease.level, RENT_LEVELS["referencia"])
    factors = tenant_factors(lease.tenant, lease.level, listing.kind)
    # El humor del inquilino cambia lo que se queda: contento renueva más y se va menos.
    factors = replace(factors, stay=factors.stay * mood_factor(lease.mood))
    # Firmar corto espanta a quien busca estabilidad y atrae a quien peor paga.
    term_find, term_risk = term_factors(listing, level, lease.term)
    living = lease.status in (RENTED, DEFAULTING)
    extra, rolled = roll_events(
        listing, rng, wear=factors.wear if living else 1.0,
        repairs=news.repairs if news else 1.0, neighbour=news.neighbour if news else 1.0,
    )  # fmt: skip
    events = list(rolled)

    rent = 0
    status = lease.status
    if status == OBRAS:
        # En obras no se alquila; al acabar, vuelve a estar vacío y listo para alquilar.
        if lease.works_until is None or day >= lease.works_until:
            lease = replace(lease, status=VACANT, since=day, works_until=None)
            events.append(Event("obra_terminada"))
        return DayOutcome(lease=lease, rent=0, costs=monthly_costs(listing), extra=extra,
                          events=tuple(events))  # fmt: skip
    if status in (HABITUAL, OWN_BUSINESS) or lease.member is not None:
        # Vive su dueño, tiene ahí su negocio o un miembro de alquiler: no hay inquilinos
        # ficticios que entren, se vayan o dejen de pagar (lo del miembro lo decide su monedero).
        return DayOutcome(lease=lease, rent=0, costs=monthly_costs(listing), extra=extra,
                          events=tuple(events))  # fmt: skip
    if status == EVICTING and lease.eviction_at is not None and day >= lease.eviction_at:
        lease = _vacated(lease, day)
        events.append(Event("desahucio"))
    elif status == RENTED:
        rent = lease.rent
        if lease.until is not None and day >= lease.until:
            renew = max(0.0, 1 - (1 - RENEWAL) * factors.stay)
            lease, end = contract_end(listing, lease, day, index, rng, renew)
            events.append(end)
        elif rng.random() < (
            level.default * factors.risk * term_risk * (news.default if news else 1.0)
        ):
            lease = replace(lease, status=DEFAULTING, since=day, unpaid=1)
            rent = 0
            events.append(Event("impago"))
        elif rng.random() < EARLY_LEAVE * factors.stay:
            lease = _vacated(lease, day)
            events.append(Event("se_va"))
    elif status == DEFAULTING:
        if rng.random() < PAY_BACK:
            # Lo que el seguro ya adelantó lo recupera él (se subroga en la deuda).
            rent = lease.rent * (lease.unpaid + 1 - lease.claims)
            lease = replace(lease, status=RENTED, unpaid=0, since=day, claims=0)
            events.append(Event("paga"))
        else:
            lease = replace(lease, unpaid=lease.unpaid + 1)
    elif status == VACANT:
        vacant_for = (day - lease.since).days if lease.since is not None else 0
        if rng.random() < min(1.0, find_chance(listing, level, day, news) * boost * term_find):
            tenant = _tenant_for(level.key, listing.kind, rng)
            if lease.pick == PICK_SELF:
                events.append(Event("candidato", tenant=tenant))
            else:
                lease = sign(listing, lease, tenant, day, index)
                events.append(Event("firma"))
        # Una habitación vacía no se okupa: en el piso vive gente.
        elif (
            listing.kind != ROOM_KIND
            and vacant_for >= SQUAT_AFTER_DAYS
            and rng.random() < SQUAT * (news.squat if news else 1.0)
        ):
            lease = replace(lease, status=SQUATTED, tenant=None, rent=0, since=day)
            events.append(Event("okupa"))
    insurer, lease = insure_month(listing, lease)
    if insurer:
        events.append(Event("seguro", insurer))
    return DayOutcome(
        lease=lease,
        rent=rent,
        costs=monthly_costs(listing),
        extra=extra,
        events=tuple(events),
        insurer=insurer,
    )


def roll_events(
    listing: Listing,
    rng: random.Random,
    *,
    wear: float = 1.0,
    repairs: float = 1.0,
    neighbour: float = 1.0,
) -> tuple[int, tuple[Event, ...]]:
    """Los imprevistos de un mes: `(lo que cuestan, cuáles)`.

    Args:
        wear: Desgaste del inquilino (`TenantFactors.wear`): averías y humedades.
        repairs: Lo que la noticia del barrio cambia averías, humedades y cerraduras.
        neighbour: Lo que cambia las quejas del vecino.
    """
    events: list[Event] = []
    extra = 0
    for kind in EVENTS:
        if kind.kinds and listing.kind not in kind.kinds:
            continue
        chance = kind.chance
        if kind.worn:
            chance *= wear
        if kind.key in REPAIR_EVENTS:
            chance *= repairs
        if kind.key == "humedades":
            chance *= listing.damp
        elif kind.key in REPAIR_EVENTS:
            chance *= listing.breakdowns
        elif kind.key == "vecino":
            chance *= neighbour
        if rng.random() >= chance:
            continue
        cost = _euros(KINDS[listing.kind].community * kind.community_months)
        if kind.euros[1]:
            cost += _euros(rng.randint(*kind.euros))
        extra += cost
        events.append(Event(kind.key, cost))
    return extra, tuple(events)


def repairs_of(events: Iterable[Event]) -> int:
    """Lo que cuestan las reparaciones de unos imprevistos (las que llevan IGIC)."""
    return sum(e.cost for e in events if e.key in REPAIR_EVENTS)


def start_eviction(lease: Lease, day: date, rng: random.Random) -> Lease:
    """Pide el desahucio: el juzgado da fecha para la vista.

    Raises:
        ValueError: Si nadie debe nada ni hay okupas.
    """
    if lease.status == DEFAULTING:
        low, high = EVICTION_DAYS
    elif lease.status == SQUATTED:
        low, high = SQUAT_EVICTION_DAYS
    else:
        raise ValueError("Solo se desahucia a quien no paga o a los okupas.")
    return replace(
        lease, status=EVICTING, since=day, eviction_at=day + timedelta(days=rng.randint(low, high))
    )


@dataclass(frozen=True, slots=True)
class Payout:
    """Cómo se reparte un día entre lo que se debe y lo que cobran los dueños.

    Todo es del inmueble entero; el reparto entre participaciones lo hace
    `split`.

    Attributes:
        ibi: IBI pagado hoy (del mes y el atrasado). Va al Estado.
        costs: Comunidad, seguros e imprevistos pagados hoy. Desaparecen.
        net: Lo que queda para los dueños.
        arrears: Deuda que queda.
        arrears_ibi: La parte de `arrears` que se debe al Estado.
        taxes: Impuesto de primas e IGIC de las reparaciones pagados hoy. Van al Estado.
        taxes_left: La parte de `arrears_ibi` que son esos impuestos de hoy (el resto es IBI).
    """

    ibi: int
    costs: int
    net: int
    arrears: int
    arrears_ibi: int
    taxes: int = 0
    taxes_left: int = 0


def settle_day(
    rent: int,
    costs: MonthlyCosts,
    extra: int,
    arrears: int,
    arrears_ibi: int,
    taxes: int = 0,
) -> Payout:
    """Paga con la renta del día lo que se debe, por orden, y deja el resto a deber.

    Primero lo del Estado: el IBI (del mes y el atrasado) y los impuestos del día
    (`taxes`: el de primas de los seguros y el IGIC de las reparaciones). Luego la
    comunidad, los seguros y los imprevistos (del día y los atrasados). Lo que la renta no
    cubre se intenta cobrar del monedero de los dueños (`HousingDebit`, un recibo
    domiciliado) y lo que no llegue queda como deuda del inmueble: se paga después o se
    descuenta al vender. Lo que se queda debiendo al Estado va con el IBI atrasado.
    """
    owed_ibi = costs.ibi + arrears_ibi
    owed_other = costs.community + costs.insurance + extra + (arrears - arrears_ibi)
    pay_ibi = min(rent, owed_ibi)
    pay_taxes = min(rent - pay_ibi, max(0, taxes))
    pay_other = min(rent - pay_ibi - pay_taxes, owed_other)
    left_ibi = owed_ibi - pay_ibi + max(0, taxes) - pay_taxes
    left_other = owed_other - pay_other
    return Payout(
        ibi=pay_ibi,
        costs=pay_other,
        net=rent - pay_ibi - pay_taxes - pay_other,
        arrears=left_ibi + left_other,
        arrears_ibi=left_ibi,
        taxes=pay_taxes,
        taxes_left=max(0, taxes) - pay_taxes,
    )


def split(amount: int, holders: dict[int, int]) -> dict[int, int]:
    """Reparte `amount` entero entre los dueños, según sus participaciones.

    `amount` ya es solo de las participaciones que tienen dueño (`owned_share`); los
    céntimos que sobran del redondeo van a quien más tiene.

    Args:
        holders: Participaciones de cada miembro.
    """
    owned = sum(holders.values())
    if amount <= 0 or not owned:
        return {member: 0 for member in holders}
    parts = {member: amount * shares // owned for member, shares in holders.items()}
    leftover = amount - sum(parts.values())
    if leftover:
        top = max(holders, key=lambda member: (holders[member], -member))
        parts[top] += leftover
    return parts


def owned_share(amount: int, owned: int) -> int:
    """La parte de una cifra del inmueble entero que toca a las participaciones con dueño.

    Las que siguen en venta no cobran renta ni pagan gastos: son de la promotora.
    """
    return amount * owned // SHARES


def manager(holders: dict[int, int]) -> int | None:
    """Quién decide la renta: quien tiene la mayoría absoluta (art. 398 CC), o nadie."""
    for member, shares in holders.items():
        if shares * 2 > SHARES:
            return member
    return None


# -- Fase 2: hipoteca, VPO y vivienda habitual ------------------------------------------

#: Estado de un inmueble en el que vive su dueño: no se alquila, paga sus recibos.
HABITUAL = "habitual"
#: Estado de un local o una nave en la que su dueño tiene su negocio de `pala` (fase 2 de
#: los negocios): no se alquila ni se vende, paga sus recibos y no imputa renta, porque está
#: afecto a una actividad (art. 85.1 LIRPF y art. 29.1.a LIRPF).
OWN_BUSINESS = "negocio"
#: Tipos de inmueble en los que cabe un negocio propio.
BUSINESS_SPACE_KINDS = frozenset({"local", "nave"})
#: Tipos de inmueble que son vivienda (un trastero o un garaje no lo son).
HOME_KINDS = frozenset(
    {"estudio", "bajo", "piso2", "piso3", "atico", "chalet", "edificio", "terrera", "cueva"}
    | {"finca", "casona", "bungalow", "duplex", "loft"}
)

#: Lo que presta el banco sobre el precio (sin ITP ni notaría): el 80 %. Con el aval del
#: Estado (la línea de avales del ICO para la primera vivienda de jóvenes y familias con
#: menores, de 2023), el 100 % en la VPO.
MORTGAGE_LTV = 0.8
VPO_LTV = 1.0
#: Diferencial sobre el Euríbor: el tipo es fijo al firmar (Euríbor del día + 1 %).
MORTGAGE_SPREAD = 0.01
#: 30 años: 360 meses, que a un día = un mes son 360 días.
MORTGAGE_MONTHS = 360
#: La cuota no puede pasar de esta parte de los ingresos (lo que piden los bancos).
DTI_MAX = 0.35
#: Parte de las rentas de alquiler que el banco cuenta como ingreso.
RENT_INCOME_SHARE = 0.7
#: Días de ingresos que mira el banco (las nóminas de un mes de verdad).
INCOME_DAYS = 30
#: Euríbor simulado: empieza en el real de 2026 y se mueve un cuarto de punto los lunes.
EURIBOR_START = 0.022
EURIBOR_STEP = 0.0025
EURIBOR_MIN = 0.0
EURIBOR_MAX = 0.05
#: Ejecución hipotecaria (art. 693.2 LEC, redacción de la Ley 5/2019): impagos de 12
#: cuotas o del 3 % del capital en la primera mitad del préstamo.
EXECUTION_INSTALLMENTS = 12
EXECUTION_SHARE = 0.03
#: Sin pujas, el banco se adjudica el inmueble por el 70 % de su valor (art. 671 LEC).
AUCTION_SHARE = 0.7
#: Vivienda habitual exenta en el Patrimonio hasta 300.000 € (art. 4.Nueve.c de la Ley
#: 19/1991), a 10 Y$ por euro.
HOME_WEALTH_EXEMPT = 3_000_000
#: Emanciparse: los bufos de vivir en tu casa (la batería, la familia) los da el Confort
#: de la casa (`bot.services.home`).
#: El aval de tus padres (favor `aval` de `pala`): el banco suma a tus ingresos la mitad
#: de la pensión de tu padre, la media de jubilación (unos 1.500 € al mes), porque la otra
#: mitad la necesitan para vivir. Va a la escala de las nóminas (`WAGE_YAPDOLLARS_PER_EURO`),
#: como lo que mira el banco. La hipoteca queda avalada (`PARENTS_AVAL`): si la ejecutan,
#: tus padres pierden el piso del pueblo y la familia de `pala` se hunde.
PARENTS_PENSION_EUR = 1_500
PARENTS_SHARE = 0.5
PARENTS_AVAL = "padres"
#: IPREM en euros al año (600 € × 14 pagas, el de 2025 prorrogado). La VPO pide no
#: pasar de 3,5 veces el IPREM de ingresos.
IPREM_EUR_YEAR = 8_400
VPO_INCOME_IPREM = 3.5


def installment(principal: int, annual_rate: float, months: int = MORTGAGE_MONTHS) -> int:
    """Cuota mensual (un día del bot) de un préstamo francés, en Y$."""
    if principal <= 0:
        return 0
    rate = annual_rate / 12
    if rate <= 0:
        return -(-principal // months)
    return math.ceil(principal * rate / (1 - (1 + rate) ** -months))


def mortgage_month(balance: int, annual_rate: float, payment: int) -> tuple[int, int]:
    """`(intereses, amortización)` de una cuota: lo que no son intereses baja el capital."""
    interest = round(balance * annual_rate / 12)
    return interest, max(0, min(balance, payment - interest))


def next_euribor(value: float, rng: random.Random) -> float:
    """Euríbor del lunes siguiente: sube, baja o se queda un cuarto de punto."""
    moved = value + rng.choice((-EURIBOR_STEP, 0.0, EURIBOR_STEP))
    return round(min(EURIBOR_MAX, max(EURIBOR_MIN, moved)), 4)


#: Lo que dice el BCE desde Fráncfort cuando el Euríbor sube, baja o se queda quieto. El
#: Euríbor no lo fija el BCE (es lo que se prestan los bancos), pero lo sigue de cerca:
#: aquí cada lunes sale una frase de su presidencia con el movimiento.
ECB_UP: tuple[str, ...] = (
    "Lagarde sube tipos «para domar la inflación»; tu cuota no tiene quién la dome",
    "Los halcones de Fráncfort ganan la votación: la paloma acaba en paella",
    "El BCE endurece la política monetaria. Tu nevera ya la había endurecido antes",
    "«La inflación es transitoria», dijeron. Lo transitorio era tu poder adquisitivo",
    "Fráncfort sube un cuarto de punto y el Bundesbank lo celebra con salchichas",
    "Subida de tipos: los bancos aplauden, los hipotecados rezan, Sánchez culpa a Putin",
    "Lagarde avisa: «Haremos lo que haga falta». Esta vez, lo que haga falta es cobrarte",
    "El BCE sube tipos y Feijóo dice que con él no pasaría. Con él gobierna Fráncfort igual",
)
ECB_DOWN: tuple[str, ...] = (
    "Lagarde baja tipos: los fondos buitre ya están mirando tu barrio",
    "Whatever it takes, versión rebajas: el BCE abarata el dinero (para quien ya lo tiene)",
    "Fráncfort baja un cuarto de punto. El precio de los pisos lo sube medio",
    "El BCE recorta tipos y las inmobiliarias reimprimen los carteles con otro cero",
    "Bajada de tipos: Bruselas lo celebra, Alemania protesta y tu casero sube la renta",
    "Dinero más barato: lo justo para que el ático suba otro 10 %",
    "Lagarde abre el grifo. El agua llega antes a Blackstone que a tu cuenta",
    "El BCE baja tipos y el Gobierno se lo apunta en el Manual de resistencia",
)
ECB_HOLD: tuple[str, ...] = (
    "El BCE mantiene los tipos «atado y bien atado» hasta nueva orden",
    "Lagarde no toca nada: reunión de seis horas para decir que todo sigue igual",
    "Tipos congelados. Lo único que se mueve en Fráncfort es el canapé",
    "Fráncfort espera «a tener más datos». Los datos esperan a tener más Fráncfort",
    "El BCE no mueve ficha: ni halcones ni palomas, gaviotas de Las Canteras",
    "Política monetaria en pausa, como la reforma de la financiación autonómica",
    "Tipos quietos: el BCE vigila la inflación desde una mesa larguísima, como Putin",
)


def ecb_phrase(before: float, after: float, week: date) -> str:
    """La frase del BCE de un lunes: según el Euríbor suba, baje o se quede quieto.

    Sale siempre la misma para la misma semana y el mismo movimiento (la elige el
    número del lunes), así que no hace falta guardarla para enseñarla otra vez.
    """
    if after > before:
        pool = ECB_UP
    elif after < before:
        pool = ECB_DOWN
    else:
        pool = ECB_HOLD
    return pool[week.toordinal() // 7 % len(pool)]


def vpo_income_cap() -> int:
    """Neto diario de nómina máximo para la VPO, en Y$ de nómina (escala de `pala`)."""
    return round(IPREM_EUR_YEAR * VPO_INCOME_IPREM * WAGE_YAPDOLLARS_PER_EURO / 365)


def parents_income() -> int:
    """Lo que suma al día el aval de tus padres a lo que mira el banco, en Y$ de nómina."""
    return round(PARENTS_PENSION_EUR * PARENTS_SHARE * WAGE_YAPDOLLARS_PER_EURO / INCOME_DAYS)


def daily_income(work_net: int, rent: int) -> int:
    """Lo que el banco cree que ganas al día: nóminas de 30 días y el 70 % de las rentas."""
    return (work_net + round(rent * RENT_INCOME_SHARE)) // INCOME_DAYS


def affordable(income: int, installments: int) -> bool:
    """Si unas cuotas (al día) caben en el 35 % de los ingresos diarios."""
    return installments <= income * DTI_MAX


def must_execute(unpaid_count: int, unpaid_amount: int, principal: int) -> bool:
    """Si el banco ya puede ejecutar la hipoteca (Ley 5/2019)."""
    return unpaid_count >= EXECUTION_INSTALLMENTS or (
        unpaid_amount > 0 and unpaid_amount >= principal * EXECUTION_SHARE
    )


def vpo_costs(unit_price: int, community_eur: int) -> MonthlyCosts:
    """Recibos de un mes de una VPO: comunidad e IBI (sin seguro con impago: no se alquila)."""
    return MonthlyCosts(
        community=_euros(community_eur),
        insurance=0,
        ibi=round(unit_price * CADASTRAL_SHARE * IBI_RATE / 12),
    )


# -- Fase 3: miembros de alquiler y Bono Alquiler Joven ---------------------------------

#: Bono Alquiler Joven: 250 € al mes (RD 42/2022), como mucho la mitad de la renta. Lo paga
#: la cuenta del Estado mientras le quede dinero: si no, «partida agotada».
BONO_EUR = 250
BONO_MAX = BONO_EUR * YAPDOLLARS_PER_EURO
BONO_SHARE = 0.5
#: Ingresos máximos para el bono: 3 veces el IPREM (RD 42/2022, art. 4).
BONO_INCOME_IPREM = 3.0


#: Sobreesfuerzo: el Banco de España habla de sobreesfuerzo cuando el alquiler se come
#: más del 30 % de lo que se gana.
OVERBURDEN = 0.30
#: Lo que piden los caseros para alquilar: una nómina de 3 veces la renta.
INCOME_RENT_MULTIPLE = 3
#: Fianza de un mes de renta en la vivienda (art. 36.1 LAU) y, si la nómina no llega a
#: 3 veces la renta, la garantía adicional de dos meses más, el máximo que permite el
#: art. 36.5 LAU. Con el aval de tus padres (`pala`) no se pide la garantía.
DEPOSIT_MONTHS = 1
GUARANTEE_MONTHS = 2


def effort(rent: int, daily_net: int) -> float | None:
    """Parte del sueldo que se va en el alquiler: la renta de un mes (un día) entre el neto de
    nómina de un día (`work_income`). `None` si no cobra nómina."""
    if daily_net <= 0:
        return None
    return rent / daily_net


def needs_guarantee(rent: int, daily_net: int, *, aval: bool) -> bool:
    """Si el casero pide la garantía adicional: nómina por debajo de 3 veces la renta y sin aval."""
    return not aval and daily_net < rent * INCOME_RENT_MULTIPLE


def deposit_for(rent: int, *, guarantee: bool) -> int:
    """Lo que se deja al firmar: la fianza y, si se pide, la garantía adicional."""
    return rent * (DEPOSIT_MONTHS + (GUARANTEE_MONTHS if guarantee else 0))


def bono_amount(rent: int) -> int:
    """Lo que paga el bono un mes: 250 € como mucho y nunca más de la mitad de la renta."""
    return max(0, min(BONO_MAX, round(rent * BONO_SHARE)))


def bono_income_cap() -> int:
    """Neto diario de nómina máximo para el bono, en Y$ de nómina (escala de `pala`)."""
    return round(IPREM_EUR_YEAR * BONO_INCOME_IPREM * WAGE_YAPDOLLARS_PER_EURO / 365)


# -- Fase 4: vacacional, gran tenedor, fondo buitre y SOCIMI ----------------------------

#: Nivel de renta de la vivienda vacacional (solo con licencia).
VACATION_LEVEL = "vacacional"
#: Nivel de renta de los nómadas digitales (su demanda depende del barrio).
NOMAD_LEVEL = "nomadas"
#: Lo que cuesta la licencia (proyecto, cédula y tasas), en Y$. La tasa va al Estado.
LICENSE_FEE = 30_000
#: Plazas vacacionales por barrio: la ley canaria limita las viviendas vacacionales por
#: zona; aquí, una por barrio. Si está cogida, no hay licencia.
LICENSE_PER_BARRIO = 1
#: IGIC de las estancias vacacionales: el tipo general (art. 51 de la Ley 4/2012). Va
#: dentro de lo que paga el turista: el dueño se queda el resto y lo ingresa en el Estado.
VACATION_IGIC = 0.07
#: Gran tenedor en zona tensionada: 5 viviendas o más (art. 3.k de la Ley 12/2023).
BIG_HOLDER_HOMES = 5
#: Un barrio está tensionado cuando su índice pasa de este valor (los precios subieron
#: un 6 %, más o menos lo que pasa cuando los miembros tienen el 60 % del barrio): allí
#: el gran tenedor no puede pedir más que la renta de referencia (art. 17.7).
TENSE_INDEX = 1.06
#: Niveles que el gran tenedor no puede pedir en zona tensionada.
CAPPED_LEVELS = frozenset({"mercado", "nomadas", VACATION_LEVEL})
#: Recargo del IBI de la vivienda vacía más de 2 años (24 días) de quien tiene 4 o más
#: (art. 72.4 del RDL 2/2004, recargo de hasta el 150 %).
EMPTY_DAYS = 24
EMPTY_OWNER_HOMES = 4
EMPTY_IBI_FACTOR = 2.5
#: El fondo buitre ofrece un 15 % sobre el valor de mercado por un inmueble entero de quien
#: tiene 3 enteros o un edificio, cada lunes; la oferta dura una semana.
VULTURE_HOMES = 3
VULTURE_PREMIUM = 0.15
VULTURE_DAYS = 7
#: SOCIMI: hace falta tener 3 inmuebles enteros y pagar la salida al BME Growth. Sus
#: rentas no pagan IRPF del alquiler sino el 19 % del dividendo (base del ahorro, art. 66
#: LIRPF): la sociedad tributa al 0 % (art. 9 de la Ley 11/2009).
SOCIMI_HOMES = 3
SOCIMI_FEE = 100_000
SOCIMI_RATE = 0.19


def season_factor(day: date) -> float:
    """Ocupación vacacional: en Canarias la temporada alta es el invierno (de noviembre a marzo)."""
    return 1.0 if day.month in (11, 12, 1, 2, 3) else 0.4


def vacation_igic(rent: int) -> int:
    """IGIC que va dentro de una estancia vacacional que cuesta `rent` con todo."""
    return rent - round(rent / (1 + VACATION_IGIC))


def tense(index: float) -> bool:
    """Si un barrio está en zona tensionada."""
    return index >= TENSE_INDEX


def big_holder(whole_homes: int) -> bool:
    """Si alguien es gran tenedor (en zona tensionada)."""
    return whole_homes >= BIG_HOLDER_HOMES


def level_allowed(level: str, *, index: float, whole_homes: int, license: bool) -> bool:
    """Si se puede pedir una renta: la vacacional pide licencia y el gran tenedor tiene tope."""
    if level == VACATION_LEVEL and not license:
        return False
    return not (level in CAPPED_LEVELS and tense(index) and big_holder(whole_homes))


def socimi_tax(profit: int) -> int:
    """El 19 % del dividendo que reparte la SOCIMI de un mes de alquiler."""
    return round(max(0, profit) * SOCIMI_RATE)


# -- Fase 5: obra nueva de la rama Ladrillo --------------------------------------------

#: Las claves de la obra nueva: `obra_1`, `obra_2`… (ningún barrio se llama así).
PROMOTION_PREFIX = "obra_"
#: Tipos de la obra nueva y sus metros, `(tipo, mínimo, máximo)`.
PROMOTION_KINDS: tuple[tuple[str, int, int], ...] = (
    ("piso2", 70, 90),
    ("piso3", 90, 120),
    ("atico", 85, 115),
)
#: Comisión del promotor: de cada participación de su promoción que se compra, el 5 % del
#: precio va a quien la levantó en vez de desaparecer (el margen de la promotora). Es
#: rendimiento de una actividad económica (art. 27 LIRPF, el promotor de `pala` es
#: autónomo) y se retiene a cuenta como el resto de la renta general.
PROMOTER_FEE = 0.05
#: Días hacia atrás en que hay que haber fichado en la rama Ladrillo para levantar la
#: promoción del lunes.
BUILDER_DAYS = 7


def is_promotion(key: str) -> bool:
    """Si una clave es de la obra nueva."""
    return key.startswith(PROMOTION_PREFIX)


def new_promotion(number: int, free_barrios: list[str], rng: random.Random) -> Listing:
    """La promoción número `number` del servidor, en uno de los barrios que no tienen.

    Es un inmueble como los del catálogo: su precio y su renta salen de sus metros y del
    barrio. Certificado A: es obra nueva (lo único nuevo de la burbuja).

    Raises:
        ValueError: Si no queda ningún barrio libre (el tope: una por barrio).
    """
    if not free_barrios:
        raise ValueError("Ya hay obra nueva en todos los barrios.")
    barrio = rng.choice(sorted(free_barrios))
    kind, low, high = rng.choice(PROMOTION_KINDS)
    return Listing(
        key=f"{PROMOTION_PREFIX}{number}",
        barrio=barrio,
        kind=kind,
        m2=rng.randint(low, high),
        title=f"Obra nueva: {rng.choice(PROMOTION_NAMES)}",
        blurb=rng.choice(PROMOTION_BLURBS),
        floor=rng.choice(PROMOTION_FLOORS[kind]),
        energy="A",
    )


def promoter_fee(price: int) -> int:
    """Lo que se lleva el promotor de una compra en su promoción."""
    return round(max(0, price) * PROMOTER_FEE)


# -- Ampliación, fase 3: compraventa entre miembros y subastas -------------------------

#: Valor de referencia del Catastro: el 90 % del valor de mercado (factor de minoración 0,9
#: de la Orden HFP/1104/2021). Es la base mínima del ITP entre particulares (art. 10.2 del
#: RDL 1/1993, tras la Ley 11/2021): vender barato no baja el impuesto.
REFERENCE_SHARE = 0.9
#: ITP de la adjudicación en subasta judicial de un inmueble en Canarias: 7 % (art. 31.f del
#: Decreto Legislativo 1/2009). Su base, el mayor entre lo pujado y el valor de referencia
#: (TSJ de Galicia, 2026: el Supremo aún no lo ha fijado).
AUCTION_ITP_RATE = 0.07
#: Las subastas duran un día del bot (en el Portal de Subastas del BOE son 20 días). Si alguien
#: puja en la última hora, se alarga una hora: así no gana quien puja en el último segundo
#: (regla del juego).
AUCTION_SECONDS = 24 * 3600
AUCTION_EXTEND_SECONDS = 3600
#: Cada puja sube al menos un 1 % la anterior.
AUCTION_STEP = 0.01
#: Subasta judicial (art. 670 LEC, simplificado): puja mínima del 50 % del valor; gana la
#: mejor si llega al 70 %; por debajo, solo si no es la vivienda habitual del ejecutado, o
#: si cubre toda la deuda y llega al 60 %. Sin puja que valga, el banco se la queda al 70 %
#: (`AUCTION_SHARE`, como hasta ahora: decisión del proyecto, aunque la LO 1/2025 cambió el
#: art. 671).
JUDICIAL_MIN_SHARE = 0.5
JUDICIAL_HOME_MIN_SHARE = 0.6
#: Lo adjudicado en una subasta judicial no se puede vender al mercado en 30 días (sí a otro
#: miembro): si no, comprar al 70 % y revender al 100 % sería un pelotazo seguro.
AUCTION_LOCK_DAYS = 30


def reference_value(listing: Listing, shares: int, index: float = 1.0) -> int:
    """Valor de referencia de unas participaciones: el 90 % de su valor de mercado de hoy."""
    return round(share_price(listing, index) * shares * REFERENCE_SHARE)


@dataclass(frozen=True, slots=True)
class MemberSaleQuote:
    """Las cuentas de una venta de participaciones a otro miembro (o en una subasta).

    Attributes:
        shares: Participaciones.
        price: Lo que se paga.
        value: Su valor de mercado de hoy.
        reference: Su valor de referencia (`reference_value`).
        itp: ITP que paga el comprador, sobre el mayor entre `price` y `reference`.
        notary: Notaría, registro y gestoría del comprador (desaparecen).
        notary_igic: Su IGIC (al Estado).
        cost: Lo que le costaron al vendedor las que vende.
        gain: Ganancia patrimonial del vendedor (puede ser negativa).
        gain_tax: IRPF de esa ganancia (19 %).
    """

    shares: int
    price: int
    value: int
    reference: int
    itp: int
    notary: int
    notary_igic: int
    cost: int
    gain: int
    gain_tax: int

    @property
    def buyer_total(self) -> int:
        """Lo que sale del bolsillo del comprador (sin hipoteca)."""
        return self.price + self.itp + self.notary + self.notary_igic

    @property
    def gift(self) -> int:
        """Lo que se regala (precio por debajo del valor): exento de Donaciones (proyecto)."""
        return max(0, self.value - self.price)


def member_sale_quote(
    listing: Listing,
    shares: int,
    owned: int,
    cost: int,
    price: int,
    *,
    index: float = 1.0,
    itp_rate: float = ITP_RATE,
    market_value_gain: bool = True,
) -> MemberSaleQuote:
    """Cuenta de una venta de participaciones entre miembros.

    - El comprador paga el ITP sobre el mayor entre el precio y el valor de referencia
      (art. 10.2 del RDL 1/1993) y la notaría con su IGIC. No hay agencia.
    - El vendedor tributa la ganancia con el mayor entre el precio y el valor de mercado
      (art. 35.2 LIRPF: si vende por debajo, Hacienda mira lo que valía). En una subasta, el
      precio es el de mercado (`market_value_gain` falso).
    - Las deudas del piso no se descuentan: siguen con el inmueble (art. 9.1.e LPH).

    Args:
        owned: Participaciones que tiene el vendedor (para repartir lo que le costaron).
        cost: Lo que le costaron todas.
        itp_rate: El tipo del ITP (7 % en una subasta judicial, `AUCTION_ITP_RATE`).

    Raises:
        ValueError: Si vende más de las que tiene o el precio no es positivo.
    """
    if not 1 <= shares <= owned <= SHARES:
        raise ValueError("No puedes vender más participaciones de las que tienes.")
    if price <= 0:
        raise ValueError("El precio tiene que ser mayor que cero.")
    value = share_price(listing, index) * shares
    reference = reference_value(listing, shares, index)
    notary = round(price * NOTARY_RATE)
    part_cost = cost * shares // owned
    transfer = max(price, value) if market_value_gain else price
    gain = transfer - part_cost
    return MemberSaleQuote(
        shares=shares,
        price=price,
        value=value,
        reference=reference,
        itp=round(max(price, reference) * itp_rate),
        notary=notary,
        notary_igic=igic(notary, SERVICES_IGIC),
        cost=part_cost,
        gain=gain,
        gain_tax=capital_gain_tax(gain),
    )


def next_bid(best: int, minimum: int) -> int:
    """La puja más baja que se acepta: la mínima o un 1 % más que la mejor (y 1 Y$ más)."""
    if best <= 0:
        return minimum
    return max(minimum, best + max(1, math.ceil(best * AUCTION_STEP)))


def auction_end(ends_at: float, now: float) -> float:
    """Cuándo acaba una subasta tras una puja: si quedaba menos de una hora, una hora más."""
    return (
        max(ends_at, now + AUCTION_EXTEND_SECONDS)
        if ends_at - now < AUCTION_EXTEND_SECONDS
        else ends_at
    )


def judicial_ok(bid: int, value: int, owed: int, *, habitual: bool) -> bool:
    """Si la mejor puja de una subasta judicial se lleva el inmueble (art. 670 LEC simplificado).

    Args:
        value: Valor de lo subastado (el de mercado de hoy).
        owed: Todo lo que debe el ejecutado por esa hipoteca y las deudas del piso.
        habitual: Si es la vivienda habitual del ejecutado.
    """
    if bid < value * JUDICIAL_MIN_SHARE:
        return False
    if bid >= value * AUCTION_SHARE:
        return True
    if not habitual:
        return True
    return bid >= owed and bid >= value * JUDICIAL_HOME_MIN_SHARE


# -- Ampliación, fase 4: reformas e imprevistos que se deciden -------------------------

#: Estado de un inmueble en obras: no se alquila (`Lease.works_until`).
OBRAS = "obras"
#: ICIO, el impuesto de las obras (art. 102 del RDL 2/2004): el 4 % del coste, el máximo; va
#: al Estado porque en el juego no hay ayuntamientos. Más la tasa de la licencia de obra.
ICIO_RATE = 0.04
WORKS_LICENSE_FEE = 1_500
#: Lo que sube el valor del inmueble por cada Y$ que cuesta una reforma (sin impuestos), y
#: lo más que pueden subir entre todas el valor y la renta: reformar para vender pierde.
REFORM_VALUE_SHARE = 0.6
REFORM_MAX_BONUS = 0.25
#: Rehabilitación (art. 41.1 del Reglamento del IRPF): una reforma integral que cuesta más del
#: 25 % del valor de la construcción (el 60 % del precio, sin el suelo). Los contratos que se
#: firman en los dos años siguientes (24 días) tienen la reducción del 60 % (art. 23.2.c).
REHAB_SHARE = 0.25
REHAB_DAYS = 24
REHAB_REDUCTION = 0.6
#: Humor del inquilino: el de partida y lo que se pide para sacar el logro de «contento».
MOOD_BASE = 70
#: El parte al seguro del hogar: la franquicia que paga el dueño y lo que se recarga la prima
#: durante 30 días.
HOME_CLAIM_EXCESS = 1_500
HOME_CLAIM_SURCHARGE = 1.2
HOME_CLAIM_DAYS = 30
#: Días que tiene el dueño para decidir un imprevisto; si no, la gestora lo arregla.
DECISION_DAYS = 2


def mood_factor(mood: int) -> float:
    """Lo que el humor multiplica irse antes y no renovar: 1 con el de partida."""
    return min(1.6, max(0.5, 1 + (MOOD_BASE - mood) / 100))


def reform_cost(listing: Listing, reform: Reform) -> int:
    """Lo que cuesta una reforma (sin IGIC ni ICIO), en Y$ a la escala general (a cientos)."""
    units = BUILDING_UNITS if listing.kind == "edificio" else 1
    euros = reform.per_m2 * _m2(listing) + reform.fixed * units
    return int(round(_euros(euros), -2))


@dataclass(frozen=True, slots=True)
class ReformQuote:
    """Las cuentas de una reforma.

    Attributes:
        cost: La obra (desaparece).
        igic: Su IGIC, al general (al Estado).
        icio: El ICIO, el 4 % (al Estado).
        license: La tasa de la licencia de obra (al Estado).
        rehab: Si cuenta como rehabilitación para la reducción del 60 %.
    """

    cost: int
    igic: int
    icio: int
    license: int
    rehab: bool

    @property
    def total(self) -> int:
        """Todo lo que sale del bolsillo."""
        return self.cost + self.igic + self.icio + self.license


def reform_quote(listing: Listing, reform: Reform, index: float = 1.0, ipc: int = 0) -> ReformQuote:
    """La cuenta de una reforma: obra, IGIC, ICIO, licencia y si es rehabilitación.

    Args:
        ipc: El IPC del crédito, en % (`credit`): encarece la obra.
    """
    cost = reform_cost(listing, reform)
    cost += cost * ipc // 100
    construction = price(listing, index) * CONSTRUCTION_SHARE
    return ReformQuote(
        cost=cost,
        igic=igic(cost, SERVICES_IGIC),
        icio=round(cost * ICIO_RATE),
        license=WORKS_LICENSE_FEE,
        rehab=reform.rehab and cost > construction * REHAB_SHARE,
    )


def improved(listing: Listing, done: Iterable[tuple[Reform, int, date, bool]]) -> Listing:
    """El inmueble con sus reformas terminadas: renta, valor, averías y certificado.

    Suman a lo que ya trae el inmueble: 0 en el catálogo, y lo que le toca a cada planta o
    habitación de un inmueble dividido (`split_units`).

    Args:
        done: `(reforma, lo que costó, día que acabó, si fue rehabilitación)`.
    """
    rent = value = 0.0
    breakdowns = damp = 1.0
    energy = listing.energy
    rehab_until: date | None = None
    catalog = catalog_price(listing)
    for reform, cost, finished, rehab in done:
        rent += reform.rent_bonus
        value += REFORM_VALUE_SHARE * cost / catalog if catalog else 0.0
        breakdowns *= reform.breakdowns
        damp *= reform.damp
        if reform.energy and reform.energy < energy:
            energy = reform.energy
        if rehab:
            until = finished + timedelta(days=REHAB_DAYS)
            rehab_until = until if rehab_until is None else max(rehab_until, until)
    return replace(
        listing,
        rent_bonus=listing.rent_bonus + min(REFORM_MAX_BONUS, rent),
        value_bonus=listing.value_bonus + min(REFORM_MAX_BONUS, value),
        breakdowns=breakdowns,
        damp=damp,
        energy=energy,
        rehab_until=rehab_until,
    )


@dataclass(frozen=True, slots=True)
class Choice:
    """Una forma de resolver un imprevisto.

    Attributes:
        key: Clave estable (para los logros).
        label: El botón.
        share: Lo que se paga del coste del imprevisto (1 = entero).
        flat: Lo que se paga fijo, en Y$ (la mediación).
        mood: Lo que cambia el humor del inquilino.
        relapse: Probabilidad de que vuelva a pasar (la chapuza).
        bad_mood: Con `relapse` a 0, probabilidad de que salga mal y baje el humor (hablar
            con el vecino), por `mood` negativo aparte: `(probabilidad, humor)`.
        claim: Si se da parte al seguro del hogar (paga la franquicia y recarga la prima).
    """

    key: str
    label: str
    share: float = 1.0
    flat: int = 0
    mood: int = 0
    relapse: float = 0.0
    bad_mood: tuple[float, int] = (0.0, 0)
    claim: bool = False


#: Las formas de resolver cada imprevisto que se decide; la primera es la de la gestora (la
#: de siempre: se arregla bien). La derrama y la ITE son obligatorias (art. 10 LPH).
DECISIONS: dict[str, tuple[Choice, ...]] = {
    "averia": (
        Choice("tecnico", "🧰 Técnico oficial", mood=5),
        Choice("cunado", "🪛 La chapuza del cuñado", share=0.35, mood=-5, relapse=0.4),
        # El inquilino la arregla y la descuenta de la renta, con un 20 % más (art. 21.5 LAU).
        Choice("espera", "⏳ Que espere", share=1.2, mood=-20),
    ),
    "humedades": (
        Choice("reparar", "🔧 Repararlas bien", mood=5),
        Choice("pintar", "🎨 Pintar encima", share=0.2, mood=-5, relapse=0.6),
        Choice("parte", "🛡️ Dar parte al seguro", share=0.0, claim=True),
    ),
    "cerradura": (
        Choice("urgencia", "🔑 Cerrajero de urgencia"),
        Choice("manana", "🕗 Mañana por la mañana", share=0.5, mood=-10),
    ),
    "vecino": (
        Choice("hablar", "🗣️ Hablar con él", share=0.0, bad_mood=(0.5, -10)),
        Choice("mediacion", "🤝 Mediación vecinal", share=0.0, flat=3_000, mood=5),
        Choice("ignorar", "🤷 Ignorarlo", share=0.0, mood=-15),
    ),
}


@dataclass(frozen=True, slots=True)
class Resolution:
    """Cómo queda un imprevisto resuelto.

    Attributes:
        pay: Lo que se paga hoy (sin IGIC).
        mood: Lo que cambia el humor.
        relapse: Si vuelve a pasar (nace otro imprevisto igual).
        claim: Si se ha dado parte al seguro del hogar.
    """

    pay: int
    mood: int
    relapse: bool = False
    claim: bool = False


def resolve(event: str, choice: str | None, cost: int, rng: random.Random) -> Resolution:
    """Resuelve un imprevisto con la forma elegida (o la de la gestora si no se eligió).

    Raises:
        ValueError: Si el imprevisto no se decide o la forma no existe.
    """
    options = DECISIONS[event]
    picked = next((c for c in options if c.key == choice), options[0])
    pay = round(cost * picked.share) + picked.flat
    if picked.claim:
        pay = min(cost, HOME_CLAIM_EXCESS)
    mood = picked.mood
    if picked.bad_mood[0] and rng.random() < picked.bad_mood[0]:
        mood += picked.bad_mood[1]
    relapse = bool(picked.relapse) and rng.random() < picked.relapse
    return Resolution(pay=pay, mood=mood, relapse=relapse, claim=picked.claim)


# -- Ampliación, fase 5: vacacional con reseñas -----------------------------------------

#: Al acabar cada estancia vacacional, la probabilidad de que el huésped deje reseña; la
#: media cuenta las últimas `REVIEW_WINDOW`.
REVIEW_CHANCE = 0.7
REVIEW_WINDOW = 20
#: La nota de partida de una estancia normal. La ocupación va de ×0,5 con 1,5 estrellas o
#: menos a ×1,3 con 4,8 o más, en línea recta (con menos de 3 reseñas, ×1).
RATING_BASE = 4.0
RATING_LOW = (1.5, 0.5)
RATING_HIGH = (4.8, 1.3)
RATING_MIN_REVIEWS = 3
#: Superanfitrión: 4,8 de media con 10 reseñas o más. Cobra un 10 % más.
SUPERHOST_RATING = 4.8
SUPERHOST_REVIEWS = 10
SUPERHOST_PREMIUM = 0.10
#: Gastos de cada estancia: la limpieza (30 €) y la comisión de la plataforma (15 % de lo que
#: paga el huésped), los dos con su IGIC. Desaparecen (ni la limpiadora ni la plataforma
#: existen) y son gastos deducibles del alquiler (art. 13 del Reglamento del IRPF).
CLEANING_FEE = 300
PLATFORM_FEE = 0.15
#: Ley 6/2025 de Canarias (uso turístico de viviendas): la declaración vale 5 años (60 días)
#: y se renueva si el barrio no está tensionado; en zona tensionada no hay licencias nuevas
#: (art. 5.1), la obra nueva no puede (pide 10 años de antigüedad) y en un bloque tiene que
#: aprobarlo la comunidad: 3/5 de propietarios y cuotas (arts. 7.3 y 17.12 LPH). Aquí votan
#: los vecinos ficticios: aprueban 6 de cada 10 veces; si dicen que no, una semana sin pedirla.
LICENSE_DAYS = 60
NEIGHBOURS_APPROVE = 0.6
LICENSE_RETRY_DAYS = 7
#: Lo que se quejan de más o de menos los huéspedes y los barrios que les gustan.
GUEST_TASTE: dict[str, tuple[float, frozenset[str]]] = {
    "britanicos": (0.0, frozenset({"playa", "lujo"})),
    "despedida": (-0.4, frozenset({"centro", "playa"})),
    "nordicos": (0.2, frozenset({"playa"})),
    "surferos": (0.0, frozenset({"playa"})),
    "senderistas": (0.0, frozenset({"rural", "historico"})),
    "luna_miel": (-0.1, frozenset({"lujo", "playa"})),
    "ciclistas": (0.0, frozenset({"rural", "lujo"})),
    "buceo": (0.0, frozenset({"marinero", "playa"})),
    "amigas": (0.0, frozenset({"centro", "playa"})),
    "numerosa": (-0.2, frozenset({"playa"})),
}
#: Lo que se le nota a la reseña: barrio que le gusta, cada imprevisto sin arreglar, el ruido.
LIKES_THEME = 0.3
PENDING_STARS = 0.7
NOISE_STARS = 0.5


def review_stars(
    listing: Listing,
    guest: str | None,
    *,
    furniture: float,
    pending: int,
    noisy: bool,
    rng: random.Random,
) -> int:
    """Las estrellas (de 1 a 5) de la reseña de una estancia vacacional.

    Args:
        guest: Quién se ha alojado (`GUEST_TASTE`).
        furniture: Lo que suben los muebles del dueño (`housing_furniture.furniture_stars`).
        pending: Imprevistos sin arreglar en el inmueble.
        noisy: Si ha habido jaleo (el vecino, una noticia de ruido en el barrio).
    """
    taste, likes = GUEST_TASTE.get(guest or "", (0.0, frozenset()))
    stars = RATING_BASE + taste + furniture
    if BARRIO_BY_KEY[listing.barrio].theme in likes:
        stars += LIKES_THEME
    if listing.rent_bonus > 0:
        stars += 0.2  # reformado: se nota en las fotos
    stars -= min(2, pending) * PENDING_STARS
    if noisy:
        stars -= NOISE_STARS
    stars += rng.uniform(-0.6, 0.6)
    return max(1, min(5, round(stars)))


def rating_factor(rating: float, reviews: int) -> float:
    """Lo que la nota media multiplica la ocupación del vacacional (de ×0,5 a ×1,3)."""
    if reviews < RATING_MIN_REVIEWS:
        return 1.0
    (low, at_low), (high, at_high) = RATING_LOW, RATING_HIGH
    share = min(1.0, max(0.0, (rating - low) / (high - low)))
    return round(at_low + share * (at_high - at_low), 3)


def superhost(rating: float, reviews: int) -> bool:
    """Si una vivienda vacacional es de Superanfitrión."""
    return reviews >= SUPERHOST_REVIEWS and rating >= SUPERHOST_RATING


def stay_costs(rent: int, *, out: bool) -> int:
    """Comisión de la plataforma de un día de estancia y, si el huésped se va, la limpieza.

    Sin IGIC (va aparte, `SERVICES_IGIC`).
    """
    return round(max(0, rent) * PLATFORM_FEE) + (CLEANING_FEE if out else 0)


def license_problem(listing: Listing, *, index: float, promotion: bool) -> str | None:
    """Por qué la Ley 6/2025 de Canarias no da licencia nueva a un inmueble, o `None`."""
    if tense(index):
        return (
            "El barrio está en zona tensionada: la Ley 6/2025 de Canarias no da licencias "
            "vacacionales nuevas ahí (art. 5.1)."
        )
    if promotion:
        return (
            "Es obra nueva: la Ley 6/2025 de Canarias pide 10 años de antigüedad para "
            "convertir una vivienda en vacacional."
        )
    return None


def needs_neighbours(listing: Listing) -> bool:
    """Si la licencia la tiene que aprobar la comunidad (un piso en un bloque, no el edificio)."""
    return listing.kind in _BLOCKS and listing.kind not in ("edificio", "local")


# -- Ampliación, fase 6: junta de copropietarios ------------------------------------------

#: Lo que dura una votación de la junta. Si votan todos antes, se cierra en el momento.
VOTE_SECONDS = 24 * 3600
#: Mayorías, en tanto por uno de las participaciones de los miembros. Quien no vota cuenta a
#: favor (art. 17.8 LPH: el silencio vale como sí).
MAJORITY = "mayoria"
THREE_FIFTHS = "tres_quintos"
UNANIMITY = "unanimidad"


@dataclass(frozen=True, slots=True)
class Motion:
    """Algo que se puede votar en la junta.

    Attributes:
        key: Su clave (`housing_votes.kind`).
        emoji: Para los botones y la pantalla.
        name: Cómo se llama.
        threshold: La mayoría que pide (`MAJORITY`, `THREE_FIFTHS` o `UNANIMITY`).
        law: La norma de esa mayoría.
    """

    key: str
    emoji: str
    name: str
    threshold: str
    law: str


MOTIONS: tuple[Motion, ...] = (
    Motion("renta", "💶", "La renta que se pide", MAJORITY, "art. 398 CC"),
    Motion("seguro", "🛡️", "El seguro de impago", MAJORITY, "art. 398 CC"),
    Motion("eleccion", "✋", "Quién elige al inquilino", MAJORITY, "art. 398 CC"),
    Motion("administra", "🧑‍💼", "Quién administra", MAJORITY, "art. 398 CC"),
    Motion("derrama", "🏦", "Derrama al fondo de reserva", MAJORITY, "art. 17.7 LPH"),
    Motion("reforma", "🏗️", "Una reforma", THREE_FIFTHS, "art. 17.4 LPH"),
    Motion("licencia", "🏖️", "La licencia vacacional", THREE_FIFTHS, "art. 17.12 LPH"),
    Motion("venta", "🤝", "Vender el inmueble entero", UNANIMITY, "art. 397 CC"),
    Motion("plantas", "🏢", "Dividirlo (por plantas o en dos)", UNANIMITY, "art. 397 CC"),
    Motion("habitaciones", "🛏️", "Alquilarlo por habitaciones", MAJORITY, "art. 398 CC"),
)
MOTION_BY_KEY: dict[str, Motion] = {m.key: m for m in MOTIONS}
#: Fondo de reserva (art. 9.1.f LPH): el 10 % de la comunidad se guarda y paga las derramas y
#: la ITE. Es un número del inmueble: la comunidad ya desaparece al pagarse.
RESERVE_SHARE = 0.10
RESERVE_EVENTS = frozenset({"derrama", "ite"})
#: Tipos de inmueble que están en un bloque con comunidad (los que tienen derramas e ITE).
BLOCK_KINDS = _BLOCKS
#: Derrama al fondo de reserva que se puede votar, en Y$.
LEVY_MIN = 1_000
LEVY_MAX = 1_000_000


def passes(threshold: str, total: int, against: int) -> bool:
    """Si una votación sale adelante: `against` son las participaciones que votan que no."""
    yes = total - against
    if threshold == UNANIMITY:
        return against == 0
    if threshold == THREE_FIFTHS:
        return yes * 5 >= total * 3
    return yes * 2 > total


def division_minimum(values: list[int]) -> int:
    """Puja mínima de la acción de división: el valor de mercado de todo (arts. 400 y 404 CC)."""
    return max(1, sum(values))


# -- Dividir por plantas y alquilar por habitaciones (9.12.0) ---------------------------

#: Las dos formas de dividir un inmueble (`housing_splits.mode`). Por plantas sale una
#: división horizontal: cada planta es un inmueble con sus dueños, que se vende por separado.
#: Por habitaciones el piso sigue siendo uno (se compra y se vende entero) y cada habitación
#: tiene su inquilino; se deshace cuando están todas vacías.
FLOORS = "plantas"
ROOMS = "habitaciones"
SPLIT_MODES = (FLOORS, ROOMS)
#: El contrato del piso mientras se alquila por habitaciones: no se cierra de noche (lo hacen
#: sus habitaciones) ni se alquila entero.
BY_ROOMS = "por_habitaciones"
#: Lo que separa la clave del inmueble de la de cada planta (`triana_edificio~2`) o
#: habitación (`isleta_piso3~h1`).
UNIT_SEP = "~"
ROOM_TAG = "h"
#: Lo que se puede dividir por plantas y lo que se puede alquilar por habitaciones.
FLOOR_KINDS = frozenset({"edificio", "casona", "chalet", "duplex"})
#: Lo que se divide en dos, izquierda y derecha (9.16.0), con los metros que hacen falta para
#: que cada mitad siga siendo habitable (o un local que sirva). Va por la misma obra, los
#: mismos impuestos y las mismas cuentas que dividir por plantas (`FLOORS`).
HALVES_MIN_M2 = {"piso3": 90, "atico": 90, "loft": 70, "local": 70, "bajo": 60}
ROOM_PARENT_KINDS = frozenset({"piso3", "atico", "duplex", "terrera", "chalet", "casona"})
#: Plantas: juntas piden un 25 % más que el inmueble entero (lo pequeño se alquila más caro
#: por metro), repartido por metros con el bajo un poco por debajo y el ático por encima.
FLOOR_RENT_GAIN = 0.25
FLOOR_WEIGHTS = {"bajo": 0.9, "atico": 1.2}
#: La obra de dividir: cocina, baño y contadores por vivienda, y escalera e instalaciones
#: comunes por metro (euros). Lleva IGIC, ICIO y licencia, como las reformas, y la escritura
#: de división horizontal paga AJD al 0,75 % del valor (art. 36 del Decreto Legislativo
#: 1/2009). Sube el valor como una reforma (`REFORM_VALUE_SHARE`): dividir para vender pierde.
FLOOR_UNIT_EUR = 20_000
FLOOR_M2_EUR = 80
#: Un edificio dividido desde la 9.13.0 lleva un local en el bajo y un trastero por vivienda
#: (`storage_of`): tabiques y puerta de cada trastero, en euros, y los metros que se anuncian.
STORAGE_EUR = 2_000
STORAGE_M2 = 5
FLOOR_DAYS = 8
FLOOR_MAX = 5
SPLIT_AJD_RATE = 0.0075
#: Habitaciones: una por cada 30 m² (de 3 a 6), amuebladas con cerradura (euros por
#: habitación, con IGIC; no es obra, así que ni ICIO ni licencia). Dos meses en prepararlas.
ROOM_M2 = 30
ROOMS_MIN, ROOMS_MAX = 3, 6
ROOM_EUR = 4_000
ROOM_DAYS = 2
#: Lo más que dura el contrato de una habitación: va por el Código Civil, no por la LAU.
ROOM_MONTHS = 12
#: Lo que piden de más las habitaciones juntas frente al piso entero, por tipo de barrio:
#: donde hay universidad, playa o mucho trabajo de temporada se llenan; en el campo, no.
ROOM_RENT_GAIN: dict[str, float] = {
    "centro": 0.25,
    "historico": 0.25,
    "playa": 0.23,
    "marinero": 0.20,
    "obrero": 0.18,
    "lujo": 0.05,
    "rural": 0.03,
}


def unit_key(parent: str, mode: str, number: int) -> str:
    """La clave de la planta o habitación `number` (desde 1) de un inmueble dividido."""
    return f"{parent}{UNIT_SEP}{ROOM_TAG if mode == ROOMS else ''}{number}"


def parent_key(key: str) -> str | None:
    """El inmueble del que sale una planta o habitación (`None` si no sale de ninguno)."""
    head, sep, _tail = key.rpartition(UNIT_SEP)
    return head if sep else None


def is_room(key: str) -> bool:
    """Si una clave es la de una habitación (`isleta_piso3~h1`)."""
    _head, sep, tail = key.rpartition(UNIT_SEP)
    return bool(sep) and tail.startswith(ROOM_TAG)


def floors_of(listing: Listing) -> int:
    """Plantas de un inmueble: las del anuncio de un edificio («Bajo a 3.º» son cuatro), dos
    en los demás."""
    if listing.kind != "edificio":
        return 2
    found = re.search(r" a (\d+)\.", listing.floor)
    return min(FLOOR_MAX, int(found.group(1)) + 1) if found else 2


def storage_of(listing: Listing) -> int:
    """Trasteros que salen al dividir un edificio: uno por vivienda (las plantas de encima del
    bajo, que pasa a ser un local). Los demás inmuebles no tienen."""
    return floors_of(listing) - 1 if listing.kind == "edificio" else 0


def rooms_of(listing: Listing) -> int:
    """Habitaciones que salen de un piso: una por cada `ROOM_M2` metros."""
    return max(ROOMS_MIN, min(ROOMS_MAX, listing.m2 // ROOM_M2))


def split_problem(listing: Listing, mode: str, *, promotion: bool = False) -> str | None:
    """Por qué no se puede dividir así un inmueble (`None` si se puede)."""
    if mode == FLOORS:
        if UNIT_SEP in listing.key:
            return "Ya es una parte de un inmueble dividido: no se divide otra vez."
        least = HALVES_MIN_M2.get(listing.kind)
        if listing.kind not in FLOOR_KINDS and (least is None or listing.m2 < least):
            return (
                "Por plantas se dividen los edificios, las casonas, los chalés y los dúplex; en "
                "dos, los pisos de tres dormitorios y los áticos de 90 m² o más, los lofts y "
                "locales de 70 y los bajos de 60."
            )
        if promotion:
            return "La obra nueva se vende tal como la entrega el promotor."
        return None
    if listing.kind not in ROOM_PARENT_KINDS:
        return (
            "Por habitaciones se alquilan los pisos de tres dormitorios, los áticos, los dúplex, "
            "las casas terreras, los chalés y las casonas."
        )
    return None


def halves(listing: Listing) -> bool:
    """Si dividir un inmueble lo parte en dos (izquierda y derecha) y no por plantas."""
    return listing.kind not in FLOOR_KINDS and listing.kind in HALVES_MIN_M2


def _floor_plan(parent: Listing, units: int) -> list[tuple[str, str]]:
    """`(tipo, planta)` de cada planta de un inmueble, de abajo arriba, y detrás sus trasteros.

    Un edificio dividido desde la 9.13.0 guarda en `units` sus plantas más sus trasteros: el bajo
    es un local y hay un trastero por vivienda. Los que se dividieron antes guardaron solo sus
    plantas y siguen como estaban, con el bajo de vivienda y sin trasteros.
    """
    if halves(parent):
        # En dos: cada mitad es del mismo tipo (dos locales, dos bajos, dos áticos) o, en un
        # piso o un loft, un piso de dos dormitorios o un estudio según sus metros.
        half = parent.m2 // 2
        same = parent.kind in ("local", "bajo", "atico")
        kind = parent.kind if same else ("piso2" if half >= 50 else "estudio")
        return [(kind, f"{parent.floor} izda."), (kind, f"{parent.floor} dcha.")]
    storage = units - floors_of(parent) if parent.kind == "edificio" else 0
    floors = units - storage
    m2 = parent.m2 // floors
    middle = "piso3" if m2 >= 90 else "piso2" if m2 >= 50 else "estudio"
    if parent.kind == "edificio":
        labels = ["Bajo", *(f"{i}.º" for i in range(1, floors - 1)), "Ático"]
        kinds = ["local" if storage else "bajo", *([middle] * (floors - 2)), "atico"]
        rooms = [("trastero", f"Trastero {n}") for n in range(1, storage + 1)]
        return [*zip(kinds, labels, strict=True), *rooms]
    if parent.kind == "duplex":
        parts = parent.floor.split(" y ")
        labels = parts if len(parts) == 2 else ["Planta de abajo", "Planta de arriba"]
        return list(zip([middle, "atico"], labels, strict=True))
    return [("bajo", "Planta baja"), (middle, "Planta alta")]


def split_units(parent: Listing, mode: str, units: int, cost: int) -> tuple[Listing, ...]:
    """Las plantas o habitaciones de un inmueble dividido, con su renta y su valor.

    `parent` va sin reformas: las suyas se copian a cada planta al dividir y suman aparte
    (`improved`). Las plantas piden juntas `FLOOR_RENT_GAIN` más que el entero, repartido
    por metros con `FLOOR_WEIGHTS`, y valen lo que él más `REFORM_VALUE_SHARE` de la obra
    (`cost`). Los trasteros de un edificio piden y valen lo de cualquier trastero del barrio,
    y salen de esa misma cuenta: no suben el total. Las habitaciones piden `ROOM_RENT_GAIN`
    más a partes iguales y valen lo que el piso (los muebles no hacen más caro el piso). Cada
    una lleva en `rent_bonus` y `value_bonus` lo que le falta a su tipo para llegar ahí.
    """
    barrio = BARRIO_BY_KEY[parent.barrio]
    if mode == FLOORS:
        plan = _floor_plan(parent, units)
        gain, value = FLOOR_RENT_GAIN, base_price(parent) + REFORM_VALUE_SHARE * cost
    else:
        plan = [(ROOM_KIND, f"Habitación {n}") for n in range(1, units + 1)]
        gain, value = ROOM_RENT_GAIN[barrio.theme], base_price(parent)
    floors = sum(1 for kind, _label in plan if kind != "trastero")
    m2 = parent.m2 // floors
    sizes = [STORAGE_M2 if kind == "trastero" else m2 for kind, _label in plan]
    sizes[0] += parent.m2 - m2 * floors
    rent_eur = (
        _m2(parent) * barrio.rent_m2 * KINDS[parent.kind].rent_factor
        * (1 + parent.rent_bonus) * (1 + gain)
    )  # fmt: skip
    units_ = [
        Listing(
            key=unit_key(parent.key, mode, number),
            barrio=parent.barrio,
            kind=kind,
            m2=size,
            title=f"{label} · {parent.title}",
            blurb=parent.blurb,
            floor=label,
            energy=parent.energy,
            breakdowns=parent.breakdowns,
            damp=parent.damp,
            rehab_until=parent.rehab_until,
        )
        for number, ((kind, label), size) in enumerate(zip(plan, sizes, strict=True), start=1)
    ]
    # Los trasteros, a precio de trastero; el resto se reparte entre las plantas por metros.
    storage = [u for u in units_ if u.kind == "trastero"]
    rent_eur -= sum(_m2(u) * barrio.rent_m2 * KINDS[u.kind].rent_factor for u in storage)
    value -= sum(catalog_price(u) for u in storage)
    weights = {u.key: FLOOR_WEIGHTS.get(u.kind, 1.0) * u.m2 for u in units_ if u not in storage}
    total = sum(weights.values())
    out = []
    for unit in units_:
        if unit in storage:
            out.append(unit)
            continue
        share = weights[unit.key] / total
        raw = _m2(unit) * barrio.rent_m2 * KINDS[unit.kind].rent_factor
        out.append(
            replace(
                unit,
                rent_bonus=rent_eur * share / raw - 1,
                value_bonus=value * share / catalog_price(unit) - 1,
            )
        )
    return tuple(out)


@dataclass(frozen=True, slots=True)
class SplitQuote:
    """Las cuentas de dividir un inmueble.

    Attributes:
        mode: `FLOORS` o `ROOMS`.
        units: Plantas (con los trasteros de un edificio) o habitaciones que salen.
        cost: La obra o los muebles (desaparece).
        igic: Su IGIC, al general (al Estado).
        icio: El ICIO de la obra (al Estado; 0 en las habitaciones).
        license: La tasa de la licencia de obra (al Estado; 0 en las habitaciones).
        ajd: El AJD de la escritura de división horizontal (al Estado; 0 en las habitaciones).
        days: Meses (días del bot) sin alquilar mientras se prepara.
    """

    mode: str
    units: int
    cost: int
    igic: int
    icio: int
    license: int
    ajd: int
    days: int

    @property
    def total(self) -> int:
        """Todo lo que sale del bolsillo."""
        return self.cost + self.igic + self.icio + self.license + self.ajd


def split_quote(listing: Listing, mode: str, index: float = 1.0, ipc: int = 0) -> SplitQuote:
    """La cuenta de dividir un inmueble por plantas o de alquilarlo por habitaciones.

    Args:
        ipc: El IPC del crédito, en % (`credit`): encarece la obra o los muebles.
    """
    if mode == FLOORS:
        floors, storage = floors_of(listing), storage_of(listing)
        units = floors + storage
        euros = FLOOR_UNIT_EUR * floors + STORAGE_EUR * storage + FLOOR_M2_EUR * listing.m2
        cost = int(round(_euros(euros), -2))
        cost += cost * ipc // 100
        return SplitQuote(
            mode, units, cost, igic(cost, SERVICES_IGIC), round(cost * ICIO_RATE),
            WORKS_LICENSE_FEE, round(price(listing, index) * SPLIT_AJD_RATE), FLOOR_DAYS,
        )  # fmt: skip
    units = rooms_of(listing)
    cost = int(round(_euros(ROOM_EUR * units), -2))
    cost += cost * ipc // 100
    return SplitQuote(mode, units, cost, igic(cost, SERVICES_IGIC), 0, 0, 0, ROOM_DAYS)
