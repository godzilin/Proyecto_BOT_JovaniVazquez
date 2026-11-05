"""Persistencia de la economía: monederos, libro de movimientos y recompensas.

Toda la economía del bot (casino, recompensas diarias y lo que venga después)
usa una única moneda, los yapdollars, y pasa por este repositorio. Las reglas
de negocio (cuánto se paga, cuándo) viven en `bot.services.economy`; aquí solo
se garantiza que cada movimiento sea atómico y quede registrado.

Modelo de datos:

- `economy_wallets`: saldo actual por servidor y usuario. Es una caché del
  libro: siempre coincide con la suma de sus movimientos.
- `economy_ledger`: una fila por movimiento (positivo o negativo) con su
  motivo y el saldo resultante. Permite auditar y revertir errores.
- `economy_daily`: último IMV (la recompensa diaria, exenta de IRPF)
  reclamado y racha actual.
- `economy_wallets` con `user_id = STATE_ACCOUNT_ID`: la cuenta del Estado,
  donde acaba todo lo recaudado. Empieza en 0, no con el saldo de bienvenida.
- `economy_gambling_days`: resultado neto del casino por usuario y día
  (premios menos apuestas) y lo retenido sobre él (el 19 % de la ganancia
  neta del día: si después se pierde, se devuelve).
- `economy_declarations`: lo que sale a devolver de cada semana (lunes ISO) y si
  está pendiente de presentar con la Renta, presentado, caducado o sin nada que
  devolver (`none`). Las de antes de la 8.0.0 solo eran del casino y los donativos.
- `economy_tax_records`: un registro por ingreso sujeto a IRPF, con lo retenido,
  su clase de renta (`kind`: trabajo, pensión, actividad, inmueble, premio…) y lo
  que la declaración necesita de él (Seguridad Social, gastos del inmueble, impuesto
  pagado fuera). Las filas de antes de la 8.0.0 no tienen clase.
- `economy_irpf_regime`, `economy_irpf_weeks` y `economy_irpf_returns`: la primera
  semana con las reglas de la 8.0.0 en cada servidor, las semanas ya cerradas y la
  declaración de cada miembro cada lunes (cuota, deducciones, retenciones, lo
  cobrado, lo que quedó a deber o a devolver y el tipo, que es el suelo de la
  retención de la semana siguiente). Desde la 8.7.0 la que sale a pagar guarda
  también cómo se paga (`payment`: pendiente, fraccionada o pagada), el plazo y el
  segundo plazo (el 40 % del jueves) con lo que se cobró de él.
- `economy_irpf_carry`: lo que una declaración pasa a las cuatro siguientes
  (intereses y reparaciones de un inmueble que no cupieron, bases negativas).
- `economy_irpf_extras`: lo pagado por el alquiler y la hipoteca de la vivienda
  habitual, para las deducciones canarias.
- `economy_wealth_weeks` y `economy_wealth_tax`: semanas en las que ya se
  cobró el Impuesto sobre el Patrimonio y lo que pagó cada uno.
- `economy_solidarity_tax`: cada declaración del Impuesto de Solidaridad de las
  Grandes Fortunas (quien pasa de 37.000.000 Y$ un lunes), con lo que pagó (cero
  mientras Canarias no bonifique el Patrimonio).
- `economy_wallets` con `user_id = SLOTS_POT_ACCOUNT_ID`: el bote común de la
  tragaperras. Crece con una parte de cada apuesta y se lo lleva entero
  quien saque el jackpot; la casa lo vuelve a sembrar al vaciarse.
- `economy_slots_jackpots`: cada jackpot de la tragaperras (quién, cuánto,
  cuándo y si cayó solo por el tope oculto), para enseñar el último en la
  máquina.
- `economy_slots_mystery`: el bote «tiene que caer antes de X» de cada
  servidor. `hit_at` es la cifra oculta del bote a la que cae solo, sorteada
  entre la semilla y el tope cada vez que se vacía; `spins`, las tiradas
  pagadas desde el último bote.
- `economy_wallets` con `user_id = POKER_POT_ACCOUNT_ID`: el 💎 Bote progresivo
  del póker. Crece con una parte de cada apuesta del bote; la escalera real se
  lo lleva entero (y la casa lo vuelve a sembrar) y la escalera de color, el 10 %.
- `economy_wallets` con `user_id = SHOP_ACCOUNT_ID`: la caja de la tienda, que
  se queda con la base imponible de lo que se vende.
- `economy_consumption_tax`: el IGIC de cada compra de la tienda (y, con signo
  negativo, el de cada devolución), para que `hacienda` lo cuente.
- `economy_lottery_tax`: el gravamen especial de cada premio de lotería
  (disposición adicional 33ª LIRPF), para que `hacienda` lo cuente.
- `economy_public_debt`: deuda pública. Si el Estado tiene que pagar un
  premio de lotería y no le llega el saldo, emite deuda por lo que falta: el
  dinero se crea, queda apuntado aquí y `hacienda` lo enseña.
- `economy_donations`: donativos a las ONGs. Cada ONG tiene su monedero con
  un `user_id` negativo (ver `bot.services.donations`), así el dinero donado
  no desaparece: se queda en la ONG, que es lo que ella quería.
- `economy_bizums`: cada Bizum entre miembros (quién, a quién, cuánto y
  cuándo), sin el concepto. Sirve para sumar lo enviado en el día.
- `economy_payroll`: cada nómina de `pala` con su desglose (bruto,
  cotizaciones del trabajador y de la empresa, IRPF y neto). Las
  cotizaciones cuentan como recaudación en `hacienda`, a nombre de quien
  cobró la nómina; el neto de la última semana (con lo que deja el negocio)
  reduce el IMV.
- `economy_sanctions`: multas y regularizaciones (p. ej. la Inspección de
  Trabajo que pilla un turno en negro). Van al Estado y cuentan en `hacienda`.
- `economy_imv_suspensions`: hasta cuándo no se puede cobrar el IMV de un
  miembro (sanción por cobrar en negro).
- `economy_residence`: quién vive (y trabaja) fuera de España y desde cuándo.
  Mientras tanto no cobra el IMV. Las nóminas de fuera llevan su país en
  `economy_payroll.country` y lo que se queda el otro país va a su cuenta
  (`HK_ACCOUNT_ID`), no al Estado.
- `economy_interest_days` y `economy_interest`: días en los que ya se pagaron
  los intereses de la cuenta y lo que cobró cada uno (saldo medio, bruto y
  retención del 19 %), con una marca de si ya se le ha avisado.
- `economy_interest_streaks`: rachas de días seguidos de cada miembro para los
  logros de la cuenta (cobrar el máximo, no bajar de un saldo, no tocar nada).
- `economy_interest_weeks` y `economy_interest_savings`: semanas ya liquidadas
  con la escala del ahorro y lo que pagó cada uno de más. Retenciones y
  liquidaciones cuentan en `hacienda`.
- `economy_wallets` con `user_id = PORRA_ACCOUNT_ID`: el depósito de las
  porras. Guarda lo apostado hasta que la porra se resuelve o se anula; al
  liquidar se vacía entero (premios, IAJ, derechos de imagen o devoluciones).
- `economy_porra_bets`: cada apuesta a una porra (quién, a qué opción y cuánto).
  Es la verdad del dinero de cada porra: el reparto se calcula con estas filas.
- `economy_porra_settled`: porras ya liquidadas, para no pagar dos veces.
- `economy_wallets` con `user_id = SPORTS_ACCOUNT_ID`: el depósito de las apuestas
  deportivas (`deportes`). Guarda lo apostado a cada partido hasta que se resuelve.
- `economy_sports_bets`: cada apuesta deportiva (quién, a qué partido, mercado y
  opción, a qué cuota y cuánto) y cómo acabó. Una por mercado, partido y persona, y lo
  de todos los mercados de un partido no pasa del tope que diga quien apuesta.
- `economy_sports_combos` y `economy_sports_combo_legs`: cada combinada deportiva (quién,
  cuánto, a qué cuota total y cómo acabó) y sus patas (partido, opción, cuota y cómo
  acabó cada una). Su dinero espera en el mismo depósito que las sencillas, y lo apostado
  en una combinada abierta cuenta en el tope de cada partido que toca.
- `economy_wallets` con `user_id = PREDICT_ACCOUNT_ID`: el depósito de las predicciones
  (`predice`). Guarda lo apostado a cada pregunta de Polymarket hasta que se resuelve.
- `economy_predict_bets`: cada predicción (quién, a qué pregunta y opción, a qué cuota y
  cuánto) y cómo acabó. Una por pregunta y persona, y lo de todas las preguntas de un
  mismo evento no pasa del tope que diga quien apuesta.
- `economy_gaming_tax`: el Impuesto sobre Actividades de Juego de cada porra
  (y, con `porra_id = 0`, la comisión de cada bote de la `timba`),
  a nombre de cada apostante (art. 48 de la Ley 13/2011), para `hacienda`.
- `economy_wallets` con `user_id = HOUSING_DEPOSIT_ACCOUNT_ID`: el depósito de
  fianzas de `pisos`. Guarda la fianza de cada miembro de alquiler hasta que se va.
- `economy_wallets` con `user_id = MARKET_ACCOUNT_ID`: el depósito del Rastro.
- `economy_wallets` con `user_id = HOUSING_AUCTION_ACCOUNT_ID`: el depósito de las subastas
  de `pisos`: la mejor puja de cada una (con su ITP y su notaría) espera aquí.
  Guarda la puja más alta de cada subasta (con su ITP) hasta que se adjudica, o
  hasta devolverla entera a quien es superado.
- `economy_market_tax`: el ITP (de quien compra) y el IRPF de la plusvalía (de
  quien vende) de cada venta del Rastro, para que `hacienda` los cuente.
- `economy_housing_tax`: el ITP de cada compra de `pisos`, el IBI de cada mes y
  el IRPF de la ganancia de cada venta, a nombre de cada copropietario, para que
  `hacienda` los cuente. El IRPF de las rentas va en `economy_tax_records`
  (concepto `pisos:renta`).
- `economy_business_tax`: el IGIC, las tasas (apertura, traslado y terraza), la retención
  del alquiler del local y el IRPF de la ganancia de un traspaso (`plusvalia`) que paga cada
  negocio de `pala` (`business`), para que `hacienda` los cuente. El IRPF a cuenta de sus
  ventas va en `economy_tax_records` (clase `actividad`).
- `economy_tax_debts` y `economy_debt_payments`: lo que se debe a Hacienda porque no
  llegó en efectivo (el Patrimonio de quien lo tiene todo en ladrillo, la declaración
  que sale a pagar) y lo que se ha ido cobrando: del monedero cada lunes, el 30 % de
  todo lo que cobra (`_garnish_in`), de lo que cobra por sus pisos, de lo que le salía
  a devolver o lo que liquida él mismo con la Renta.
- `economy_irpf_regularizations`: servidores a los que se devolvió una vez el IRPF de
  la ventana vieja de 7 días (7.x). Ya no se usa; queda por las bases antiguas.

Los saldos son enteros y nunca negativos. Cada operación abre su propia
transacción `BEGIN IMMEDIATE`, así dos botones pulsados a la vez no pueden
gastar el mismo dinero dos veces.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from bot.repositories import sqlite
from bot.services.levels import TIMEZONE, local_day
from bot.services.loans import wage_garnish
from bot.services.taxes import (
    INCOME_BECKHAM,
    INCOME_BUSINESS,
    INCOME_FOREIGN,
    INCOME_IMAGE,
    INCOME_IMPUTED,
    INCOME_PENSION,
    INCOME_PRIZE,
    INCOME_PROPERTY,
    INCOME_WORK,
    Pace,
    PropertyWeek,
    WeekIncome,
    capital_gain_tax,
    garnish,
    second_installment,
    week_tax,
)

if TYPE_CHECKING:
    from bot.services.interest import DayOutcome, LedgerRow, SavingsSettlement, Streaks
    from bot.services.taxes import ForeignPayslip, Payslip, WealthQuote, WeekReturn, WeekTax

T = TypeVar("T")

# Límite de seguridad muy por debajo del máximo de INTEGER en SQLite (2^63).
# Ningún uso normal se acerca; existe para que una racha absurda no desborde.
MAX_BALANCE = 10**15

#: `user_id` de la cuenta del Estado en `economy_wallets`. Ningún usuario de
#: Discord tiene id 0, así que no choca con nadie. Recibe todo lo que se
#: recauda; qué se hace con ese dinero está por decidir.
STATE_ACCOUNT_ID = 0

#: `user_id` del bote de la tragaperras. Negativo, como las ONGs (que usan
#: -1, -2…), para que no pague Patrimonio ni salga en las cuentas de miembros.
SLOTS_POT_ACCOUNT_ID = -100

#: `user_id` de la caja de la tienda: recibe la base imponible de cada compra
#: (el IGIC va al Estado). Así lo gastado no desaparece del libro.
SHOP_ACCOUNT_ID = -200

#: `user_id` de la hacienda de Hong Kong: recibe el salaries tax y el MPF de quien
#: trabaja allí con `pala`. No es el Estado español: no cuenta en `hacienda`.
HK_ACCOUNT_ID = -300

#: `user_id` del depósito de las porras: lo apostado espera aquí a que la porra
#: se resuelva, así no desaparece del libro mientras tanto.
PORRA_ACCOUNT_ID = -400

#: `user_id` del 💎 Bote progresivo del póker, aparte del de la tragaperras.
POKER_POT_ACCOUNT_ID = -500

#: `user_id` del depósito del Rastro: la puja más alta de cada subasta espera aquí.
#: La mochila usa el mismo id para los objetos que están en un puesto.
MARKET_ACCOUNT_ID = -600

#: `user_id` del depósito de fianzas de `pisos`: la fianza (y la garantía adicional) de un
#: miembro que alquila el piso de otro espera aquí hasta que se va, como el depósito que
#: llevan las comunidades autónomas (disposición adicional 3.ª de la LAU).
HOUSING_DEPOSIT_ACCOUNT_ID = -700

#: `user_id` del depósito de las subastas de `pisos`: la mejor puja de cada subasta, con lo
#: que pagará de ITP y notaría, espera aquí y se devuelve entera a quien superan.
HOUSING_AUCTION_ACCOUNT_ID = -800

#: `user_id` del depósito de las apuestas deportivas: lo apostado a un partido espera aquí
#: hasta que se sabe el resultado (o se anula), así no desaparece del libro mientras tanto.
SPORTS_ACCOUNT_ID = -900

#: `user_id` del Monte de Piedad (`vender`) en la mochila: lo que se le vende o se empeña
#: queda a su nombre (no se usa, no se luce y no cuenta en el patrimonio de nadie). No
#: tiene monedero: compra y presta con la caja de la tienda (`SHOP_ACCOUNT_ID`).
PAWN_ACCOUNT_ID = -1000

#: `user_id` del depósito de las predicciones (`predice`): lo apostado a una pregunta de
#: Polymarket espera aquí hasta que se resuelve (o se anula), como en `deportes`.
PREDICT_ACCOUNT_ID = -1100

#: Las cuentas de las sociedades limitadas de los negocios de `pala` (fase 3): la de un
#: negocio es `COMPANY_ACCOUNT_BASE - id del negocio`. Negativas y lejos de las fijas y de
#: las ONGs (-1, -2…), así no salen como miembros ni pagan Patrimonio por su cuenta.
COMPANY_ACCOUNT_BASE = -1_000_000


def company_account(business_id: int) -> int:
    """La cuenta (`user_id` de `economy_wallets`) de la sociedad de un negocio."""
    return COMPANY_ACCOUNT_BASE - business_id


#: Motivo del libro de las rentas de `pisos` (y concepto en `economy_tax_records`).
HOUSING_RENT_CONCEPT = "pisos:renta"
#: Conceptos de las filas de antes de la 8.0.0 que guardaban la renta de `pisos` a la
#: escala de la retención de entonces (`rent_to_fiscal`, × 12 / 365).
_LEGACY_RENT_CONCEPTS = (HOUSING_RENT_CONCEPT, "pisos:bono")

#: Lo que la declaración arrastra a las semanas siguientes (`economy_irpf_carry`):
#: intereses y reparaciones de un inmueble que no cupieron (art. 23.1.a LIRPF) y bases
#: liquidables generales negativas (art. 50 LIRPF). Los cuatro años de la ley son cuatro
#: semanas.
CARRY_FINANCE = "finance"
CARRY_BASE = "base"
CARRY_WEEKS = 4

#: Lo que la declaración necesita además de la renta (`economy_irpf_extras`): lo pagado
#: por la vivienda habitual (deducción canaria por inversión) y por el alquiler de la
#: vivienda habitual (deducción canaria del inquilino).
EXTRA_HOME = "vivienda"
EXTRA_RENT = "alquiler"


@dataclass(frozen=True, slots=True)
class FiscalWeek:
    """Una semana fiscal: de lunes a lunes, a las 00:00 en hora canaria.

    La construye el servicio (`economy.fiscal_week`), que sabe de husos y cambios de hora;
    aquí solo se usa para leer las filas de la semana.

    Attributes:
        monday: Lunes ISO.
        start: Ese lunes a las 00:00, en epoch.
        end: El lunes siguiente a las 00:00, en epoch.
        previous_start: El lunes anterior a las 00:00, en epoch (la semana anterior
            acaba en `start`).
    """

    monday: str
    start: float
    end: float
    previous_start: float

    @property
    def next_monday(self) -> str:
        """Lunes ISO de la semana siguiente."""
        return (date.fromisoformat(self.monday) + timedelta(days=7)).isoformat()

    @property
    def previous_monday(self) -> str:
        """Lunes ISO de la semana anterior."""
        return (date.fromisoformat(self.monday) - timedelta(days=7)).isoformat()

    @property
    def previous(self) -> FiscalWeek:
        """La semana anterior (su `previous_start` es aproximado: una semana antes)."""
        return FiscalWeek(
            self.previous_monday,
            self.previous_start,
            self.start,
            self.previous_start - (self.start - self.previous_start),
        )


@dataclass(frozen=True, slots=True)
class IrpfReturn:
    """La declaración de un miembro en el cierre del lunes.

    Attributes:
        user_id: El miembro.
        base: Base liquidable general de la semana.
        quota: Cuota íntegra.
        deductions: Deducciones de la cuota.
        withheld: Retenciones de la semana.
        result: Cuota líquida menos retenciones: positivo a pagar, negativo a devolver.
        charged: Lo cobrado del monedero (si salía a pagar), en los dos plazos.
        debt: Lo que no llegó y queda como deuda con Hacienda.
        refund: Lo que queda pendiente de devolver (se presenta con la Renta).
        compensated: Devolución que se quedó Hacienda para pagar deudas.
        collected: Deuda de semanas anteriores cobrada del monedero.
        rate: Tipo de la semana (el suelo de la retención de la siguiente).
        payment: Cómo va el pago de la que sale a pagar: `PAYMENT_PENDING` (el miembro
            aún no ha elegido), `PAYMENT_SPLIT` (pagado el 60 %, falta el jueves) o
            `PAYMENT_PAID`. Vacío si no sale a pagar o es de antes de la 8.7.0 (se
            cobraba sola el lunes).
        pay_due: Final del plazo para pagarla o fraccionarla (epoch); si no, se cobra
            entera.
        second: El 40 % del segundo plazo, si se fraccionó.
        second_due: Cuándo se cobra el segundo plazo (epoch).
        second_paid: Lo que se cobró del segundo plazo (el resto quedó como deuda).
        second_at: Cuándo se cobró (0 si aún no).
    """

    user_id: int
    base: int
    quota: int
    deductions: int
    withheld: int
    result: int
    charged: int = 0
    debt: int = 0
    refund: int = 0
    compensated: int = 0
    collected: int = 0
    rate: float = 0.0
    payment: str = ""
    pay_due: float = 0.0
    second: int = 0
    second_due: float = 0.0
    second_paid: int = 0
    second_at: float = 0.0


#: Estados del pago de una declaración a pagar (`IrpfReturn.payment`).
PAYMENT_PENDING = "pending"
PAYMENT_SPLIT = "split"
PAYMENT_PAID = "paid"


@dataclass(frozen=True, slots=True)
class IrpfPayment:
    """Un pago de la declaración que sale a pagar: entera, el 60 % o el 40 % del jueves.

    Attributes:
        user_id: El miembro.
        week: Lunes ISO de la semana declarada.
        due: Lo que tocaba pagar en este plazo.
        charged: Lo cobrado del monedero (va al Estado).
        debt: Lo que no llegó: queda como deuda con Hacienda, sin recargo.
        second: Lo que queda para el jueves (0 si no queda nada).
        second_due: Cuándo se cobra eso (epoch, 0 si no queda nada).
        balance: Saldo del miembro después.
        automatic: Lo ha cobrado el bot solo (plazo vencido o segundo plazo), no el miembro.
    """

    user_id: int
    week: str
    due: int
    charged: int
    debt: int
    second: int
    second_due: float
    balance: int
    automatic: bool = False


@dataclass(frozen=True, slots=True)
class IrpfRun:
    """Resultado de cerrar el IRPF de una semana en un servidor.

    Attributes:
        done_before: La semana ya estaba cerrada; no se ha tocado nada.
        legacy: La semana es de antes de la 8.0.0 en este servidor: no se cierra así.
        returns: Una declaración por miembro con renta, de más a pagar a más a devolver.
    """

    done_before: bool = False
    legacy: bool = False
    returns: tuple[IrpfReturn, ...] = ()


def _irpf_return_of(row: sqlite3.Row) -> IrpfReturn:
    """Una fila de `economy_irpf_returns` como `IrpfReturn`."""
    return IrpfReturn(
        user_id=int(row["user_id"]),
        base=int(row["base"]),
        quota=int(row["quota"]),
        deductions=int(row["deductions"]),
        withheld=int(row["withheld"]),
        result=int(row["result"]),
        charged=int(row["charged"]),
        debt=int(row["debt"]),
        refund=int(row["refund"]),
        compensated=int(row["compensated"]),
        collected=int(row["collected"]),
        rate=float(row["rate"]),
        payment=str(row["payment"]),
        pay_due=float(row["pay_due"]),
        second=int(row["second"]),
        second_due=float(row["second_due"]),
        second_paid=int(row["second_paid"]),
        second_at=float(row["second_at"]),
    )


class InsufficientFundsError(Exception):
    """El movimiento dejaría el saldo en negativo.

    Attributes:
        balance: Saldo disponible en el momento del intento.
    """

    def __init__(self, balance: int) -> None:
        super().__init__(f"Saldo insuficiente: {balance}")
        self.balance = balance


class BalanceLimitError(Exception):
    """El movimiento superaría `MAX_BALANCE`."""


class TillEmptyError(Exception):
    """La caja de la tienda no llega para lo que tiene que pagar el Monte de Piedad."""


class PorraClosedError(Exception):
    """La porra ya está liquidada: no admite apuestas ni otro pago."""


class PorraSideError(Exception):
    """El miembro ya apostó a otra opción de la misma porra.

    Attributes:
        outcome: La opción a la que apostó.
    """

    def __init__(self, outcome: int) -> None:
        super().__init__(f"Ya apostó a la opción {outcome}")
        self.outcome = outcome


class PorraCapError(Exception):
    """La apuesta pasaría el tope del bote.

    Attributes:
        room: Lo que aún cabe en el bote.
    """

    def __init__(self, room: int) -> None:
        super().__init__(f"Solo caben {room}")
        self.room = room


class SportsBetTakenError(Exception):
    """El miembro ya tiene una apuesta a ese mercado del partido: una por mercado."""


class SportsStakeCapError(Exception):
    """La apuesta pasaría el tope de lo apostado a un partido, sumando mercados.

    Attributes:
        room: Lo que aún se puede apostar a ese partido.
    """

    def __init__(self, room: int) -> None:
        super().__init__(f"Solo caben {room}")
        self.room = room


class PredictBetTakenError(Exception):
    """El miembro ya tiene una predicción a esa pregunta: una por pregunta."""


class PredictStakeCapError(Exception):
    """La predicción pasaría el tope de lo apostado a un evento, sumando sus preguntas.

    Attributes:
        room: Lo que aún se puede apostar a ese evento.
    """

    def __init__(self, room: int) -> None:
        super().__init__(f"Solo caben {room}")
        self.room = room


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """Un movimiento a aplicar sobre un monedero.

    Attributes:
        delta: Cantidad a sumar (positiva) o restar (negativa).
        reason: Motivo corto y estable, p. ej. `"ruleta:apuesta"`.
    """

    delta: int
    reason: str


@dataclass(frozen=True, slots=True)
class DailyClaim:
    """Estado de la recompensa diaria de un usuario."""

    last_claimed_at: float
    streak: int


@dataclass(frozen=True, slots=True)
class BetSettlement:
    """Resultado de mover dinero de una apuesta, con el IRPF del día.

    Attributes:
        balance: Saldo final del jugador.
        tax_delta: Retención aplicada ahora; negativa si es una devolución.
        day_net: Resultado neto del casino hoy (premios menos apuestas).
        day_withheld: Total retenido hoy tras este movimiento.
    """

    balance: int
    tax_delta: int = 0
    day_net: int = 0
    day_withheld: int = 0


@dataclass(frozen=True, slots=True)
class PokerSettlement:
    """Resultado de pagar una mano de póker.

    Attributes:
        bet: Saldo e IRPF del jugador, como en cualquier apuesta.
        pot_won: Lo que se ha llevado del 💎 Bote (0 si nada).
        pot: El bote tras la mano (ya resembrado si se vació).
    """

    bet: BetSettlement
    pot_won: int
    pot: int


@dataclass(frozen=True, slots=True)
class SlotsSettlement:
    """Resultado de mover el dinero de una tirada de tragaperras.

    Attributes:
        bet: Saldo e IRPF del jugador, como en cualquier apuesta.
        jackpot: Lo que se ha llevado del bote (0 si no hay jackpot).
        pot: Bote tras la tirada (ya resembrado si se vació).
        mystery: Si el bote ha caído solo, por llegar a la cifra oculta del
            tope («tiene que caer antes de X»), y no por la combinación.
        spins_since: Tiradas pagadas sin bote tras esta tirada; 0 si acaba
            de caer.
        drought: Si el bote ha caído en esta tirada, cuántas tiradas pagadas
            llevaba sin caer, incluida esta; 0 si no ha caído.
    """

    bet: BetSettlement
    jackpot: int
    pot: int
    mystery: bool = False
    spins_since: int = 0
    drought: int = 0


@dataclass(frozen=True, slots=True)
class JackpotRecord:
    """Un jackpot de la tragaperras.

    Attributes:
        mystery: Si cayó solo, por llegar a la cifra oculta del tope.
    """

    user_id: int
    amount: int
    won_at: float
    mystery: bool = False


@dataclass(frozen=True, slots=True)
class Treasury:
    """Resumen de la cuenta del Estado de un servidor.

    Attributes:
        balance: Saldo actual de la cuenta.
        collected_total: Todo lo recaudado desde siempre.
        collected_since: Lo recaudado desde el instante pedido (p. ej. el año).
        top_contributors: `(user_id, total retenido)` de quienes más han
            pagado, de más a menos.
        debt: Deuda pública emitida para pagar premios de lotería.
    """

    balance: int
    collected_total: int
    collected_since: int
    top_contributors: tuple[tuple[int, int], ...]
    debt: int = 0


@dataclass(frozen=True, slots=True)
class LotteryPayout:
    """Un premio de lotería a pagar desde la cuenta del Estado.

    Attributes:
        user_id: Quién cobra.
        gross: Premio bruto.
        tax: Gravamen especial que se queda el Estado (ver `taxes.lottery_tax`).
        concept: Motivo corto para el libro (`"primitiva"`, `"x10"`…).
    """

    user_id: int
    gross: int
    tax: int
    concept: str


@dataclass(frozen=True, slots=True)
class WealthCharge:
    """Lo que pagó un miembro de Impuesto sobre el Patrimonio en una semana.

    Attributes:
        balance: Su patrimonio ese lunes (efectivo, inmuebles y objetos), la base.
        tax: Lo que pagó ese lunes con su efectivo (Patrimonio y Grandes Fortunas).
        debt: Lo que no le llegó: queda como deuda con Hacienda y se embarga de lo
            que cobre por sus pisos.
        reduced: Lo que le rebajó el límite conjunto con el IRPF (art. 31).
        goods: Lo que sumaron sus objetos de la tienda a la base.
        solidarity_subject: Si pasó del umbral de Grandes Fortunas y lo declaró.
        solidarity: Lo que pagó de Grandes Fortunas (cero en Canarias).
    """

    user_id: int
    balance: int
    tax: int
    debt: int = 0
    reduced: int = 0
    goods: int = 0
    solidarity_subject: bool = False
    solidarity: int = 0


@dataclass(frozen=True, slots=True)
class WealthRun:
    """Resultado de pasar el Patrimonio por un servidor.

    Attributes:
        done_before: La semana ya estaba cobrada; no se ha tocado nada.
        activation: Era la primera vez en este servidor: se marca la semana
            sin cobrar, para que nadie pague por sorpresa al desplegar.
        charges: Quién ha pagado, de más a menos.
    """

    done_before: bool = False
    activation: bool = False
    charges: tuple[WealthCharge, ...] = ()


@dataclass(frozen=True, slots=True)
class InterestRun:
    """Resultado de pagar los intereses de un día en un servidor.

    Attributes:
        done_before: El día ya estaba pagado; no se ha tocado nada.
        outcomes: Un resultado por monedero de miembro, haya cobrado o no (las
            rachas y algunos logros también miran a quien no cobra).
    """

    done_before: bool = False
    outcomes: tuple[DayOutcome, ...] = ()


@dataclass(frozen=True, slots=True)
class SavingsRun:
    """Resultado de liquidar el ahorro de una semana en un servidor."""

    done_before: bool = False
    settlements: tuple[SavingsSettlement, ...] = ()


@dataclass(frozen=True, slots=True)
class InterestNotice:
    """Intereses y liquidaciones que un miembro aún no ha visto.

    Attributes:
        days: Días cobrados sin avisar.
        gross: Bruto de esos días.
        tax: Retención de esos días.
        first_day: El más antiguo (ISO), para saber cuánto tiempo ha estado fuera.
        savings: Liquidaciones del ahorro sin avisar: `(lunes ISO, cobrado, tipo %)`.
    """

    days: int = 0
    gross: int = 0
    tax: int = 0
    first_day: str | None = None
    savings: tuple[tuple[str, int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class DonationReceipt:
    """Resultado de un donativo.

    Attributes:
        balance: Saldo de quien dona tras donar.
        ongs_supported: A cuántas ONGs distintas ha donado ya.
        donated_week: Lo que lleva donado esta semana.
    """

    balance: int
    ongs_supported: int
    donated_week: int


@dataclass(frozen=True, slots=True)
class PorraBetReceipt:
    """Resultado de apostar a una porra.

    Attributes:
        balance: Saldo de quien apuesta, tras apostar.
        stake: Lo que lleva apostado en esa porra, esta apuesta incluida.
        pool: Bote de la porra tras la apuesta.
    """

    balance: int
    stake: int
    pool: int


@dataclass(frozen=True, slots=True)
class PorraPayment:
    """Lo que ha movido la liquidación de una porra.

    Attributes:
        bets: Resultado de cada apostante (saldo e IRPF del día), por miembro.
        image_tax: Retención de los derechos de imagen (0 si no hubo).
        subject_balance: Saldo del protagonista tras cobrarlos, o `None`.
    """

    bets: dict[int, BetSettlement]
    image_tax: int = 0
    subject_balance: int | None = None


@dataclass(frozen=True, slots=True)
class SportsBet:
    """Una apuesta deportiva (`deportes`), tal y como se guarda.

    Attributes:
        id: Id de la fila (0 antes de guardarla).
        channel_id: Canal donde se apostó: ahí sale el resultado.
        event_id: Id del partido en The Odds API.
        sport: Competición (`bot.services.sports.SPORTS`).
        home: Equipo de casa.
        away: Equipo de fuera.
        commence: Comienzo del partido (epoch).
        pick: Opción apostada (`bot.services.sports.MARKET_PICKS`): `1`, `X`, `2`,
            `+2.5`, `si`, `2-1`…
        odds: Cuota fijada al apostar, en centésimas.
        stake: Lo apostado.
        created_at: Cuándo se apostó (epoch).
        status: `open`, `won`, `lost` o `void` (anulada y devuelta).
        payout: Lo cobrado, apuesta incluida (lo devuelto si se anuló; 0 si falló).
        settled_at: Cuándo se resolvió (epoch), o `None`.
        market: Mercado de `pick` (`bot.services.sports.Market`): `1x2`, `goles`,
            `ambos` o `exacto`.
    """

    id: int
    guild_id: int
    user_id: int
    channel_id: int
    event_id: str
    sport: str
    home: str
    away: str
    commence: float
    pick: str
    odds: int
    stake: int
    created_at: float
    status: str = "open"
    payout: int = 0
    settled_at: float | None = None
    market: str = "1x2"


@dataclass(frozen=True, slots=True)
class PredictBet:
    """Una predicción de `predice`, tal y como se guarda.

    Los textos se guardan en español tal y como se vieron al apostar: la pregunta puede
    cambiar de traducción o desaparecer de Polymarket antes de resolverse.

    Attributes:
        id: Id de la fila (0 antes de guardarla).
        channel_id: Canal donde se apostó: ahí sale el resultado.
        event_id: Id del evento en Polymarket (el tope de lo apostado va por evento).
        event_title: Título del evento.
        market_id: Id del mercado en Polymarket (la pregunta que se resuelve).
        question: La pregunta u opción apostada, como se enseñó.
        side: Resultado elegido: 0 el primero del mercado (Sí), 1 el segundo (No).
        side_label: Su nombre, como se enseñó («Sí», «No», «Más»…).
        section: Sección del panel (`bot.services.predice.SECTIONS`).
        odds: Cuota fijada al apostar, en centésimas.
        stake: Lo apostado.
        ends: Cuándo acaba el mercado según Polymarket (epoch): de ahí cuenta el plazo
            para anularla si no se resuelve.
        created_at: Cuándo se apostó (epoch).
        status: `open`, `won`, `lost` o `void` (anulada y devuelta).
        payout: Lo cobrado, apuesta incluida (lo devuelto si se anuló; 0 si falló).
        settled_at: Cuándo se resolvió (epoch), o `None`.
    """

    id: int
    guild_id: int
    user_id: int
    channel_id: int
    event_id: str
    event_title: str
    market_id: str
    question: str
    side: int
    side_label: str
    section: str
    odds: int
    stake: int
    ends: float
    created_at: float
    status: str = "open"
    payout: int = 0
    settled_at: float | None = None


@dataclass(frozen=True, slots=True)
class SportsLeg:
    """Una pata de una combinada de `deportes`: un partido y una opción.

    Attributes:
        event_id: Id del partido en The Odds API.
        sport: Competición (`bot.services.sports.SPORTS`).
        home: Equipo de casa.
        away: Equipo de fuera.
        commence: Comienzo del partido (epoch).
        market: Mercado de `pick` (`bot.services.sports.COMBO_MARKETS`).
        pick: Opción apostada (`1`, `X`, `2`, `+2.5`, `si`…).
        odds: Cuota de la pata fijada al apostar, en centésimas.
        status: `open`, `won`, `lost` o `void` (`bot.services.sports.LegStatus`).
    """

    event_id: str
    sport: str
    home: str
    away: str
    commence: float
    market: str
    pick: str
    odds: int
    status: str = "open"


@dataclass(frozen=True, slots=True)
class SportsCombo:
    """Una combinada de `deportes`: varias patas que se cobran solo si aciertan todas.

    Attributes:
        id: Id de la fila (0 antes de guardarla).
        channel_id: Canal donde se apostó: ahí sale el resultado.
        stake: Lo apostado.
        odds: Cuota total fijada al apostar (`bot.services.sports.combo_odds`).
        created_at: Cuándo se apostó (epoch).
        legs: Las patas, en el orden en que se eligieron.
        status: `open`, `won`, `lost` o `void` (anulada y devuelta).
        payout: Lo cobrado, apuesta incluida (lo devuelto si se anuló; 0 si falló).
        settled_at: Cuándo se resolvió (epoch), o `None`.
    """

    id: int
    guild_id: int
    user_id: int
    channel_id: int
    stake: int
    odds: int
    created_at: float
    legs: tuple[SportsLeg, ...]
    status: str = "open"
    payout: int = 0
    settled_at: float | None = None

    @property
    def commence(self) -> float:
        """Cuándo empieza el primer partido."""
        return min(leg.commence for leg in self.legs)

    @property
    def ends(self) -> float:
        """Cuándo empieza el último partido (hacia esa hora se sabe todo)."""
        return max(leg.commence for leg in self.legs)


def _sports_leg(row: sqlite3.Row) -> SportsLeg:
    """Una fila de `economy_sports_combo_legs` como `SportsLeg`."""
    return SportsLeg(
        event_id=str(row["event_id"]),
        sport=str(row["sport"]),
        home=str(row["home"]),
        away=str(row["away"]),
        commence=float(row["commence"]),
        market=str(row["market"]),
        pick=str(row["pick"]),
        odds=int(row["odds"]),
        status=str(row["status"]),
    )


def _sports_combo(row: sqlite3.Row, legs: Sequence[SportsLeg]) -> SportsCombo:
    """Una fila de `economy_sports_combos` y sus patas como `SportsCombo`."""
    return SportsCombo(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        user_id=int(row["user_id"]),
        channel_id=int(row["channel_id"]),
        stake=int(row["stake"]),
        odds=int(row["odds"]),
        created_at=float(row["created_at"]),
        legs=tuple(legs),
        status=str(row["status"]),
        payout=int(row["payout"]),
        settled_at=None if row["settled_at"] is None else float(row["settled_at"]),
    )


def _sports_bet(row: sqlite3.Row) -> SportsBet:
    """Una fila de `economy_sports_bets` como `SportsBet`."""
    return SportsBet(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        user_id=int(row["user_id"]),
        channel_id=int(row["channel_id"]),
        event_id=str(row["event_id"]),
        sport=str(row["sport"]),
        home=str(row["home"]),
        away=str(row["away"]),
        commence=float(row["commence"]),
        pick=str(row["pick"]),
        odds=int(row["odds"]),
        stake=int(row["stake"]),
        created_at=float(row["created_at"]),
        status=str(row["status"]),
        payout=int(row["payout"]),
        settled_at=None if row["settled_at"] is None else float(row["settled_at"]),
        market=str(row["market"]),
    )


def _predict_bet(row: sqlite3.Row) -> PredictBet:
    """Una fila de `economy_predict_bets` como `PredictBet`."""
    return PredictBet(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        user_id=int(row["user_id"]),
        channel_id=int(row["channel_id"]),
        event_id=str(row["event_id"]),
        event_title=str(row["event_title"]),
        market_id=str(row["market_id"]),
        question=str(row["question"]),
        side=int(row["side"]),
        side_label=str(row["side_label"]),
        section=str(row["section"]),
        odds=int(row["odds"]),
        stake=int(row["stake"]),
        ends=float(row["ends"]),
        created_at=float(row["created_at"]),
        status=str(row["status"]),
        payout=int(row["payout"]),
        settled_at=None if row["settled_at"] is None else float(row["settled_at"]),
    )


@dataclass(frozen=True, slots=True)
class BizumReceipt:
    """Resultado de un Bizum entre dos miembros.

    Attributes:
        sender_balance: Saldo de quien envía, tras enviar.
        receiver_balance: Saldo de quien recibe, tras recibir.
        sent_today: Lo enviado hoy por quien envía, este Bizum incluido.
    """

    sender_balance: int
    receiver_balance: int
    sent_today: int


@dataclass(frozen=True, slots=True)
class MarketPayment:
    """El dinero de una venta del Rastro (`bot.services.rastro.Deal`).

    Attributes:
        buyer_id: Quien compra; paga el precio y el ITP.
        seller_id: Quien vende; cobra el precio menos la plusvalía.
        price: Precio pactado o puja ganadora.
        itp_base: Base del ITP (el mayor entre precio y valor de referencia).
        itp: ITP de quien compra.
        gain: Ganancia de quien vende.
        gain_tax: IRPF de esa ganancia.
        escrowed: Si quien compra ya lo pagó al pujar y sale del depósito.
    """

    buyer_id: int
    seller_id: int
    price: int
    itp_base: int
    itp: int
    gain: int
    gain_tax: int
    escrowed: bool = False


@dataclass(frozen=True, slots=True)
class MarketMove:
    """Todo lo que mueve una operación del Rastro, en este orden.

    Attributes:
        hold: `(miembro, importe)` que entra en el depósito (una puja con su ITP).
        refunds: `(miembro, importe)` que salen del depósito (pujas superadas o
            subastas que no se pueden adjudicar).
        payment: La venta, si se cierra una.
        payments: Más ventas en la misma operación (las dos mitades de un trueque),
            que se pagan después de `payment` y en este orden.
    """

    hold: tuple[int, int] | None = None
    refunds: tuple[tuple[int, int], ...] = ()
    payment: MarketPayment | None = None
    payments: tuple[MarketPayment, ...] = ()


@dataclass(frozen=True, slots=True)
class MonteMove:
    """El dinero de una operación del Monte de Piedad, entre un miembro y la caja de la tienda.

    Attributes:
        user_id: El miembro.
        amount: Lo que le entra (positivo: la caja paga una venta o un préstamo) o lo que
            paga (negativo: desempeña y vuelve a la caja).
        reason: Motivo del libro del miembro.
        till_reason: Motivo del libro de la caja de la tienda.
        garnish: Si lo que cobra es renta que paga el embargo de su deuda con Hacienda
            (la venta sí; el préstamo no, porque se devuelve).
    """

    user_id: int
    amount: int
    reason: str
    till_reason: str
    garnish: bool = False


@dataclass(frozen=True, slots=True)
class HousingTax:
    """Un impuesto de `pisos` a nombre de un miembro: sale de su monedero al Estado.

    Attributes:
        user_id: Quien paga.
        kind: `itp`, `ibi`, `plusvalia`, `socimi`, `licencia`, `igic` (estancias
            vacacionales), `igic_gastos` (IGIC de la notaría, la agencia, las reparaciones
            y el abogado del desahucio), `ips` (impuesto de primas de los seguros), `icio`
            (impuesto de las obras de una reforma o una división), `licencia_obra` (su tasa)
            o `ajd` (actos jurídicos documentados de la escritura de división horizontal).
        base: Base imponible.
        tax: Cuota; positiva.
    """

    user_id: int
    kind: str
    base: int
    tax: int


@dataclass(frozen=True, slots=True)
class HousingRent:
    """Un cobro de `pisos` (la renta de un mes, el bono, la comisión), con su IRPF.

    Attributes:
        user_id: Quien cobra.
        gross: Lo que entra en el monedero antes del IRPF.
        kind: Clase de renta (`taxes.INCOME_*`): la del alquiler es capital inmobiliario;
            el bono, una ganancia patrimonial; la comisión del promotor, actividad.
            `None` si no tributa en el IRPF (la SOCIMI paga su 19 % aparte).
        ref: El inmueble, para la renta de un alquiler.
        income: Rentas íntegras, si no son todo `gross` (el IGIC del vacacional va dentro
            de lo cobrado y no es renta).
        finance: Intereses de la hipoteca y reparaciones del mes (con tope en la declaración).
        costs: IBI, comunidad, seguro y amortización del mes.
        upkeep: Reparaciones y seguro de una vivienda alquilada (deducción del casero).
        premium: Primas del seguro de impago con su impuesto, si la renta no pasa de 800 €
            al mes (deducción del art. 15 quater).
        bonus: Deducción por alquilar una vivienda vacía (art. 16), la vez que se aplica.
        reduction: Reducción del art. 23.2 de este contrato (0 si no es vivienda).
        concept: Motivo del libro y concepto en `economy_tax_records`.
        tag: Para qué es (un inmueble); si un pago del Estado no se hace, se avisa
            con esta etiqueta en `settle`.
    """

    user_id: int
    gross: int
    kind: str | None = INCOME_PROPERTY
    ref: str = ""
    income: int | None = None
    finance: int = 0
    costs: int = 0
    upkeep: int = 0
    premium: int = 0
    bonus: int = 0
    reduction: float = 0.0
    concept: str = HOUSING_RENT_CONCEPT
    tag: str = ""

    @property
    def property_week(self) -> PropertyWeek:
        """Este cobro como tramo de su inmueble en la semana."""
        return PropertyWeek(
            ref=self.ref,
            income=self.gross if self.income is None else self.income,
            finance=self.finance,
            costs=self.costs,
            reduction=self.reduction,
            upkeep=self.upkeep,
            premium=self.premium,
            bonus=self.bonus,
        )


@dataclass(frozen=True, slots=True)
class HousingPull:
    """Un pago de un miembro que se hace entero o no se hace (la renta de un inquilino).

    Attributes:
        user_id: Quien paga.
        amount: Cuánto; si no le llega, no se cobra nada y la etiqueta no sale en
            los pagados (el inquilino deja de pagar).
        tag: Para qué es (el inmueble).
        reason: Motivo del libro.
        home: Si es el alquiler de su vivienda habitual (deducción canaria del inquilino).
    """

    user_id: int
    amount: int
    tag: str
    reason: str
    home: bool = False


@dataclass(frozen=True, slots=True)
class HousingDebit:
    """Un recibo de un inmueble que se cobra del monedero de un dueño, si le llega.

    Es lo que la renta del día no cubre (un piso vacío también paga IBI y comunidad):
    se cobra lo que haya y lo que falte vuelve al inmueble como deuda (`HousingMove.settle`).

    Attributes:
        user_id: El dueño.
        tag: El inmueble (`Listing.key`).
        ibi: IBI que le toca (va al Estado).
        other: Comunidad, seguro e imprevistos que le tocan (desaparecen), o la cuota de
            la hipoteca (el banco no existe: también desaparece).
        reason: Motivo del libro de `other`.
        home: Si es la cuota de la hipoteca de su vivienda habitual: lo pagado cuenta
            para la deducción canaria por inversión en vivienda habitual.
        taxes: `(clase, cuota)` de otros impuestos del día que van al Estado (el de primas
            de los seguros, el IGIC de las reparaciones): se cobran después del IBI y
            antes que `other`, y lo que no llegue se debe con el IBI.
        to: Si `other` es lo que debe a otro miembro (el reembolso a su avalista), a quién
            va lo cobrado; si no, desaparece.
    """

    user_id: int
    tag: str
    ibi: int
    other: int
    reason: str = "pisos:comunidad"
    home: bool = False
    taxes: tuple[tuple[str, int], ...] = ()
    to: int | None = None


@dataclass(frozen=True, slots=True)
class HousingMove:
    """Todo el dinero de una operación de `pisos`, en este orden.

    Attributes:
        rents: Rentas que se cobran, cada una con su retención.
        entries: `(miembro, movimiento)` sin impuesto: el precio, la notaría, la
            agencia, la comunidad. Lo que sale desaparece y lo que entra se crea.
        taxes: Impuestos que pagan los miembros y van al Estado.
        debits: Recibos que se cobran del monedero hasta donde llegue.
        settle: Recibe, dentro de la misma transacción, lo que no llegó de los recibos
            por etiqueta (`{tag: (IBI, resto)}`) para guardarlo como deuda, y lo que el
            Estado no pudo pagar de `grants` (`(0, importe)`).
        pulls: Pagos de miembros que van primero, enteros o nada.
        then: Recibe las etiquetas de `pulls` que se pagaron y devuelve lo que se mueve
            después (la renta que reciben los caseros solo si el inquilino pagó).
        grants: Pagos del Estado con su IRPF (el Bono Alquiler Joven): solo si la
            cuenta del Estado llega; si no, «partida agotada».
        imputations: `(miembro, inmueble, renta imputada)` de los pisos vacíos (art. 85
            LIRPF): no mueven dinero, solo suman a su base de la semana.
        expenses: `(miembro, inmueble, gasto)` deducibles de un inmueble que no llegan con
            una renta (el abogado y el procurador de un desahucio, art. 13.c RIRPF): no
            mueven dinero aquí (se pagan en `entries` o `taxes`), solo restan en su base.
    """

    rents: tuple[HousingRent, ...] = ()
    entries: tuple[tuple[int, LedgerEntry], ...] = ()
    taxes: tuple[HousingTax, ...] = ()
    debits: tuple[HousingDebit, ...] = ()
    settle: Callable[[sqlite3.Connection, dict[str, tuple[int, int]]], None] | None = None
    pulls: tuple[HousingPull, ...] = ()
    then: Callable[[sqlite3.Connection, frozenset[str]], HousingMove] | None = None
    grants: tuple[HousingRent, ...] = ()
    imputations: tuple[tuple[int, str, int], ...] = ()
    expenses: tuple[tuple[int, str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class BusinessCost:
    """Un pago de un negocio de `pala`: lo que desaparece y lo que va al Estado.

    Attributes:
        concept: Motivo del libro (`negocio:compras`, `negocio:alquiler`…).
        gone: Lo que se lleva quien no existe (el mayorista, el casero, la luz).
        taxes: `(clase, importe)` que van al Estado: `igic`, `tasa`, `retencion` (la del
            IRPF del casero, art. 100 RIRPF), `cotizacion` (los seguros sociales de la
            plantilla) o `retencion_plantilla` (el IRPF de sus nóminas). Se cobran antes que
            `gone`.
        expense: Si lo pagado es gasto deducible de la actividad (`INCOME_BUSINESS` en
            negativo, con criterio de caja: se apunta lo que de verdad se paga).
        debt_surcharge: Si es un recibo (`BusinessMove.bills`) de un autónomo y no llega
            entero, lo que falta pasa a su deuda con Hacienda con este recargo (los seguros
            sociales de la plantilla, art. 30 LGSS). `None`: queda como deuda del negocio.
    """

    concept: str
    gone: int = 0
    taxes: tuple[tuple[str, int], ...] = ()
    expense: bool = True
    debt_surcharge: float | None = None

    @property
    def total(self) -> int:
        """Lo que cuesta entero."""
        return self.gone + sum(amount for _kind, amount in self.taxes)


@dataclass(frozen=True, slots=True)
class BusinessMove:
    """Todo el dinero de una operación de un negocio (`EconomyRepository.business`).

    El negocio es de un autónomo: su caja es el monedero del dueño. En este orden:

    Attributes:
        user_id: El dueño.
        refund: Fianza que vuelve del depósito de fianzas al cerrar (dinero propio: no es
            renta ni se embarga).
        forfeit: Fianza que se queda el casero por lo que se le debía (sale del depósito y
            desaparece).
        payments: Pagos enteros o nada (montar, comprar al mayorista, pagar la deuda del
            local): si no llega, `InsufficientFundsError` y no se guarda nada.
        deposit: Fianza que va al depósito de fianzas al montar (entera o nada).
        sales: Ventas de un turno: las crea la demanda, que no existe.
        sold_cost: Coste al mayorista de lo vendido: la retención a cuenta va sobre
            `sales - sold_cost`.
        bills: Recibos de la noche: se cobran por orden hasta donde llegue el monedero.
        settle: Recibe, dentro de la transacción, lo que no llegó de `bills`.
        amortization: Amortización de la noche: gasto de la actividad sin pago (fase 2).
        proceeds: Lo que paga quien se queda las mejoras y la clientela al traspasar o
            liquidar (fase 2): lo crea quien no existe.
        gain: La ganancia patrimonial de `proceeds` (lo cobrado menos lo que quedaba por
            amortizar): tributa al 19 % en el momento, como el Rastro.

    Fase 3, la sociedad limitada (`account`): el negocio tiene caja propia. Todo lo de
    arriba sale y entra de `account` y no del monedero del dueño, sin IRPF (la sociedad
    paga el Impuesto sobre Sociedades, que va en `bills`) ni embargos de las deudas del
    dueño. Entre el dueño y su sociedad:

    Attributes:
        account: La cuenta de la sociedad (`company_account`), o `None` si es de autónomo.
        contribution: Lo que el dueño mete en la caja (capital o aportación de socio), entero
            o nada, antes que los pagos.
        loan_in: Lo que el dueño le presta a la sociedad, entero o nada.
        loan_back: Lo que la sociedad le devuelve del préstamo, entero o nada.
        salary: Sueldo de administrador: sale de la caja y le llega al dueño con la
            retención `salary_rate` (art. 101.2 LIRPF), como rendimiento del trabajo.
        salary_rate: El tipo de esa retención.
        dividend: Dividendo: sale de la caja y le llega con la retención del 19 %.
        liquidation: Si se disuelve: después de `bills` (sus acreedores, por orden), la caja
            devuelve el préstamo del socio (`liquidation_loan`, que cobra el último: es
            subordinado, art. 281 TRLC) y lo que quede es la cuota de liquidación del dueño.
        liquidation_basis: Lo que el dueño metió (capital y aportaciones): la ganancia es
            lo que pase de ahí, al 19 % en el momento.
        liquidation_loan: Lo que se le debe al dueño de su préstamo.
        cover_limit: Lo más que el dueño paga de lo que la sociedad no llegue a pagar a sus
            acreedores (`bills` sin impuestos): la diferencia hasta 3.000 € del capital (art.
            4.1 LSC), o todo si dejó la causa de disolución sin remediar (art. 367 LSC). Lo que
            no llegue a pagar a Hacienda (las `bills` de concepto `sociedad:hacienda`) se le
            deriva entero como administrador (art. 43.1.b LGT): lo que no tenga, a su deuda.
            Lo que no llegue a pagar al banco (`BANK_LOAN_CONCEPT`) tampoco entra: lo asume
            el socio como préstamo, porque lo avalaba (`settle`).

    Préstamo al negocio (`banco`, 9.7.0):

    Attributes:
        bank_loan: Lo que presta el banco: entra en la caja antes que los pagos (la comisión
            va en `payments`). No es renta: se devuelve. El banco no existe: se crea.

    Vender la sociedad (fase 5, 9.11.0):

    Attributes:
        sale: Lo que paga el comprador por las participaciones: se crea y le llega al dueño,
            con el 19 % de lo que pase de `liquidation_basis` (ganancia patrimonial del
            ahorro, en el momento). La caja de la sociedad se va con el comprador (sale del
            juego) y el préstamo del socio (`liquidation_loan`) se lo paga el comprador por su
            valor, sin impuestos.
    """

    user_id: int
    refund: int = 0
    forfeit: int = 0
    payments: tuple[BusinessCost, ...] = ()
    deposit: int = 0
    sales: int = 0
    sold_cost: int = 0
    bills: tuple[BusinessCost, ...] = ()
    settle: Callable[[sqlite3.Connection, int], None] | None = None
    amortization: int = 0
    proceeds: int = 0
    gain: int = 0
    account: int | None = None
    contribution: int = 0
    loan_in: int = 0
    loan_back: int = 0
    salary: int = 0
    salary_rate: float = 0.0
    dividend: int = 0
    liquidation: bool = False
    liquidation_basis: int = 0
    liquidation_loan: int = 0
    cover_limit: int = 0
    bank_loan: int = 0
    sale: int = 0


#: Los pagos de un negocio a su banco (`BusinessCost.concept`): `negocio:prestamo:intereses`,
#: `…:cuota`, `…:cancelacion`…
BANK_LOAN_CONCEPT = "negocio:prestamo:"


@dataclass(frozen=True, slots=True)
class BusinessPaid:
    """Cómo quedó el dinero de una operación de un negocio.

    Attributes:
        balance: Saldo final del dueño.
        withheld: IRPF retenido a cuenta de las ventas.
        garnished: Lo embargado de las ventas por deudas con Hacienda.
        bank_garnished: Lo embargado de las ventas por deudas con el banco (art. 607 LEC).
        unpaid: Lo que no llegó de los recibos.
        gain_tax: IRPF de la ganancia patrimonial de un traspaso (o de la liquidación de la
            sociedad).
        company: Saldo final de la caja de la sociedad (0 si es de autónomo).
        salary_tax: IRPF retenido del sueldo de administrador.
        dividend_tax: IRPF retenido de los dividendos.
        liquidated: La cuota de liquidación que le llegó al dueño.
        loan_repaid: Lo que se le devolvió de su préstamo al liquidar.
        covered: Lo que el dueño pagó de las deudas de su sociedad al liquidarla.
        derived_debt: Lo que se le quedó como deuda con Hacienda (art. 43.1.b LGT).
    """

    balance: int
    withheld: int = 0
    garnished: int = 0
    unpaid: int = 0
    bank_garnished: int = 0
    gain_tax: int = 0
    company: int = 0
    salary_tax: int = 0
    dividend_tax: int = 0
    liquidated: int = 0
    loan_repaid: int = 0
    covered: int = 0
    derived_debt: int = 0


class EconomyRepository:
    """Acceso SQLite a la economía; comparte archivo con el resto del bot.

    Args:
        database_path: Ruta del archivo SQLite persistente.
        starting_balance: Saldo con el que se abre un monedero nuevo. Se
            registra como un movimiento `"bienvenida"` para que el libro cuadre.
        irpf_start: Primera semana (lunes ISO) que se puede declarar con las reglas de
            la 8.0.0 (`_regime_in`). Si el bot nuevo arranca antes, las semanas
            anteriores siguen con la regla vieja. `None`: la semana en que arranque.
    """

    def __init__(
        self, database_path: Path, *, starting_balance: int, irpf_start: str | None = None
    ) -> None:
        if starting_balance < 0:
            raise ValueError("El saldo inicial no puede ser negativo.")
        self.database_path = database_path
        self.starting_balance = starting_balance
        self.irpf_start = irpf_start

    def _connect(self) -> sqlite3.Connection:
        """Abre una conexión en modo autocommit; las transacciones son explícitas."""
        return sqlite.connect(self.database_path, isolation_level=None)

    async def _run(self, operation: Callable[..., T], *args: object) -> T:
        """Ejecuta una operación SQLite fuera del event loop."""
        return await asyncio.to_thread(operation, *args)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """Abre una conexión con transacción de escritura y la cierra siempre.

        `BEGIN IMMEDIATE` toma el bloqueo de escritura al empezar, antes de
        leer el saldo: dos operaciones simultáneas sobre el mismo monedero se
        serializan y la segunda ve el saldo ya actualizado por la primera.
        """
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
        finally:
            connection.close()

    async def initialize(self) -> None:
        """Crea las tablas de la economía si no existen."""
        await self._run(self._initialize_sync)

    def _initialize_sync(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS economy_wallets (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    balance INTEGER NOT NULL CHECK (balance >= 0),
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS economy_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    delta INTEGER NOT NULL,
                    balance_after INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    -- julianday en vez de unixepoch(): SQLite de Debian bookworm es 3.40.
                    created_at REAL NOT NULL
                        DEFAULT ((julianday('now') - 2440587.5) * 86400.0)
                );

                CREATE INDEX IF NOT EXISTS economy_ledger_member
                    ON economy_ledger (guild_id, user_id, id);

                CREATE TABLE IF NOT EXISTS economy_daily (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    last_claimed_at REAL NOT NULL,
                    streak INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS economy_tax_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    concept TEXT NOT NULL,
                    gross INTEGER NOT NULL,
                    withheld INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS economy_tax_records_member
                    ON economy_tax_records (guild_id, user_id, created_at);

                CREATE TABLE IF NOT EXISTS economy_gambling_days (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    net INTEGER NOT NULL DEFAULT 0,
                    withheld INTEGER NOT NULL DEFAULT 0 CHECK (withheld >= 0),
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, user_id, day)
                );

                CREATE TABLE IF NOT EXISTS economy_slots_jackpots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL CHECK (amount > 0),
                    won_at REAL NOT NULL,
                    mystery INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS economy_slots_mystery (
                    guild_id INTEGER PRIMARY KEY,
                    hit_at INTEGER NOT NULL,
                    spins INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS economy_irpf_regularizations (
                    guild_id INTEGER PRIMARY KEY,
                    done_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS economy_wealth_weeks (
                    guild_id INTEGER NOT NULL,
                    week_start TEXT NOT NULL,
                    processed_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, week_start)
                );

                CREATE TABLE IF NOT EXISTS economy_wealth_tax (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    week_start TEXT NOT NULL,
                    balance INTEGER NOT NULL,
                    tax INTEGER NOT NULL CHECK (tax > 0),
                    created_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, user_id, week_start)
                );

                CREATE TABLE IF NOT EXISTS economy_solidarity_tax (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    week_start TEXT NOT NULL,
                    base INTEGER NOT NULL,
                    tax INTEGER NOT NULL CHECK (tax >= 0),
                    created_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, user_id, week_start)
                );

                CREATE TABLE IF NOT EXISTS economy_donations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    ong TEXT NOT NULL,
                    amount INTEGER NOT NULL CHECK (amount > 0),
                    week_start TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS economy_donations_member
                    ON economy_donations (guild_id, user_id, week_start);

                CREATE TABLE IF NOT EXISTS economy_ong_raffles (
                    guild_id INTEGER NOT NULL,
                    draw_day TEXT NOT NULL,
                    ong TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, draw_day, ong)
                );

                CREATE TABLE IF NOT EXISTS economy_bizums (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    sender_id INTEGER NOT NULL,
                    receiver_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL CHECK (amount > 0),
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS economy_bizums_sender
                    ON economy_bizums (guild_id, sender_id, created_at);

                CREATE TABLE IF NOT EXISTS economy_consumption_tax (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    concept TEXT NOT NULL,
                    base INTEGER NOT NULL,
                    tax INTEGER NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS economy_lottery_tax (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    concept TEXT NOT NULL,
                    gross INTEGER NOT NULL,
                    tax INTEGER NOT NULL CHECK (tax > 0),
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS economy_public_debt (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL CHECK (amount > 0),
                    concept TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS economy_payroll (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    concept TEXT NOT NULL,
                    gross INTEGER NOT NULL CHECK (gross >= 0),
                    ss_worker INTEGER NOT NULL CHECK (ss_worker >= 0),
                    ss_employer INTEGER NOT NULL CHECK (ss_employer >= 0),
                    irpf INTEGER NOT NULL CHECK (irpf >= 0),
                    net INTEGER NOT NULL,
                    country TEXT NOT NULL DEFAULT 'es',
                    foreign_tax INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS economy_payroll_member
                    ON economy_payroll (guild_id, user_id, created_at);

                CREATE TABLE IF NOT EXISTS economy_bank_hidden (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    since REAL NOT NULL,
                    until REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS economy_bank_hidden_member
                    ON economy_bank_hidden (guild_id, user_id);

                CREATE TABLE IF NOT EXISTS economy_sanctions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    concept TEXT NOT NULL,
                    amount INTEGER NOT NULL CHECK (amount > 0),
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS economy_residence (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    country TEXT NOT NULL,
                    since REAL NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS economy_imv_suspensions (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    until REAL NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE INDEX IF NOT EXISTS economy_ledger_time
                    ON economy_ledger (guild_id, created_at);

                CREATE TABLE IF NOT EXISTS economy_interest_days (
                    guild_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    processed_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, day)
                );

                CREATE TABLE IF NOT EXISTS economy_interest (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    average INTEGER NOT NULL CHECK (average >= 0),
                    gross INTEGER NOT NULL CHECK (gross > 0),
                    tax INTEGER NOT NULL CHECK (tax >= 0),
                    created_at REAL NOT NULL,
                    notified INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id, day)
                );

                CREATE TABLE IF NOT EXISTS economy_interest_streaks (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    capped INTEGER NOT NULL DEFAULT 0,
                    floor INTEGER NOT NULL DEFAULT 0,
                    resist INTEGER NOT NULL DEFAULT 0,
                    still INTEGER NOT NULL DEFAULT 0,
                    ant INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS economy_interest_weeks (
                    guild_id INTEGER NOT NULL,
                    week_start TEXT NOT NULL,
                    processed_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, week_start)
                );

                CREATE TABLE IF NOT EXISTS economy_interest_savings (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    week_start TEXT NOT NULL,
                    gross INTEGER NOT NULL,
                    withheld INTEGER NOT NULL,
                    quota INTEGER NOT NULL,
                    rate INTEGER NOT NULL,
                    charged INTEGER NOT NULL CHECK (charged >= 0),
                    created_at REAL NOT NULL,
                    notified INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id, week_start)
                );

                CREATE TABLE IF NOT EXISTS economy_porra_bets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    porra_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    outcome INTEGER NOT NULL CHECK (outcome >= 0),
                    stake INTEGER NOT NULL CHECK (stake > 0),
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS economy_porra_bets_porra
                    ON economy_porra_bets (guild_id, porra_id);

                CREATE TABLE IF NOT EXISTS economy_porra_settled (
                    guild_id INTEGER NOT NULL,
                    porra_id INTEGER NOT NULL,
                    refund INTEGER NOT NULL,
                    settled_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, porra_id)
                );

                CREATE TABLE IF NOT EXISTS economy_sports_bets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    sport TEXT NOT NULL,
                    home TEXT NOT NULL,
                    away TEXT NOT NULL,
                    commence REAL NOT NULL,
                    pick TEXT NOT NULL,
                    odds INTEGER NOT NULL CHECK (odds > 100),
                    stake INTEGER NOT NULL CHECK (stake > 0),
                    created_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    payout INTEGER NOT NULL DEFAULT 0,
                    settled_at REAL,
                    market TEXT NOT NULL DEFAULT '1x2',
                    UNIQUE (guild_id, user_id, event_id, market)
                );

                CREATE INDEX IF NOT EXISTS economy_sports_bets_open
                    ON economy_sports_bets (status, commence);

                CREATE TABLE IF NOT EXISTS economy_sports_combos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    odds INTEGER NOT NULL CHECK (odds > 100),
                    stake INTEGER NOT NULL CHECK (stake > 0),
                    created_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    payout INTEGER NOT NULL DEFAULT 0,
                    settled_at REAL
                );

                CREATE INDEX IF NOT EXISTS economy_sports_combos_open
                    ON economy_sports_combos (status);

                CREATE INDEX IF NOT EXISTS economy_sports_combos_user
                    ON economy_sports_combos (guild_id, user_id);

                CREATE TABLE IF NOT EXISTS economy_sports_combo_legs (
                    combo_id INTEGER NOT NULL REFERENCES economy_sports_combos (id),
                    position INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    sport TEXT NOT NULL,
                    home TEXT NOT NULL,
                    away TEXT NOT NULL,
                    commence REAL NOT NULL,
                    market TEXT NOT NULL,
                    pick TEXT NOT NULL,
                    odds INTEGER NOT NULL CHECK (odds > 100),
                    status TEXT NOT NULL DEFAULT 'open',
                    PRIMARY KEY (combo_id, position),
                    UNIQUE (combo_id, event_id)
                );

                CREATE INDEX IF NOT EXISTS economy_sports_combo_legs_event
                    ON economy_sports_combo_legs (event_id);

                CREATE TABLE IF NOT EXISTS economy_predict_bets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    event_title TEXT NOT NULL,
                    market_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    side INTEGER NOT NULL CHECK (side IN (0, 1)),
                    side_label TEXT NOT NULL,
                    section TEXT NOT NULL,
                    odds INTEGER NOT NULL CHECK (odds > 100),
                    stake INTEGER NOT NULL CHECK (stake > 0),
                    ends REAL NOT NULL,
                    created_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    payout INTEGER NOT NULL DEFAULT 0,
                    settled_at REAL,
                    UNIQUE (guild_id, user_id, market_id)
                );

                CREATE INDEX IF NOT EXISTS economy_predict_bets_open
                    ON economy_predict_bets (status, ends);

                CREATE INDEX IF NOT EXISTS economy_predict_bets_event
                    ON economy_predict_bets (guild_id, user_id, event_id);

                CREATE TABLE IF NOT EXISTS economy_gaming_tax (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    porra_id INTEGER NOT NULL,
                    tax INTEGER NOT NULL CHECK (tax >= 0),
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS economy_market_tax (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    kind TEXT NOT NULL CHECK (kind IN ('itp', 'plusvalia')),
                    base INTEGER NOT NULL CHECK (base >= 0),
                    tax INTEGER NOT NULL CHECK (tax > 0),
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS economy_business_tax (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    tax INTEGER NOT NULL CHECK (tax > 0),
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS economy_housing_tax (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    base INTEGER NOT NULL CHECK (base >= 0),
                    tax INTEGER NOT NULL CHECK (tax > 0),
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS economy_tax_debts (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL CHECK (amount >= 0),
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS economy_debt_payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL CHECK (amount > 0),
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS economy_declarations (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    week_start TEXT NOT NULL,
                    refund INTEGER NOT NULL CHECK (refund >= 0),
                    status TEXT NOT NULL
                        CHECK (status IN ('pending', 'claimed', 'expired', 'none')),
                    claimed_at REAL,
                    PRIMARY KEY (guild_id, user_id, week_start)
                );

                CREATE TABLE IF NOT EXISTS economy_irpf_regime (
                    guild_id INTEGER PRIMARY KEY,
                    since_week TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS economy_irpf_weeks (
                    guild_id INTEGER NOT NULL,
                    week_start TEXT NOT NULL,
                    processed_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, week_start)
                );

                CREATE TABLE IF NOT EXISTS economy_irpf_returns (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    week_start TEXT NOT NULL,
                    base INTEGER NOT NULL,
                    quota INTEGER NOT NULL,
                    deductions INTEGER NOT NULL,
                    withheld INTEGER NOT NULL,
                    result INTEGER NOT NULL,
                    charged INTEGER NOT NULL DEFAULT 0 CHECK (charged >= 0),
                    debt INTEGER NOT NULL DEFAULT 0 CHECK (debt >= 0),
                    refund INTEGER NOT NULL DEFAULT 0 CHECK (refund >= 0),
                    compensated INTEGER NOT NULL DEFAULT 0 CHECK (compensated >= 0),
                    collected INTEGER NOT NULL DEFAULT 0 CHECK (collected >= 0),
                    rate REAL NOT NULL DEFAULT 0,
                    savings INTEGER NOT NULL DEFAULT 0,
                    savings_quota INTEGER NOT NULL DEFAULT 0,
                    inputs TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    notified INTEGER NOT NULL DEFAULT 0,
                    opened INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id, week_start)
                );

                CREATE TABLE IF NOT EXISTS economy_irpf_carry (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    ref TEXT NOT NULL DEFAULT '',
                    week_start TEXT NOT NULL,
                    amount INTEGER NOT NULL CHECK (amount >= 0),
                    PRIMARY KEY (guild_id, user_id, kind, ref, week_start)
                );

                CREATE TABLE IF NOT EXISTS economy_irpf_extras (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    amount INTEGER NOT NULL CHECK (amount > 0),
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS economy_irpf_extras_member
                    ON economy_irpf_extras (guild_id, user_id, created_at);
                """
            )
            # Bases de antes de la 8.0.0: cada ingreso se apunta ahora con su clase de
            # renta y lo que la declaración necesita de él. Las filas viejas quedan con
            # `kind` a NULL y se leen con la regla de entonces (`_legacy_income_in`).
            self._add_columns(
                connection,
                "economy_tax_records",
                {
                    "kind": "TEXT",
                    "costs": "INTEGER NOT NULL DEFAULT 0",
                    "finance": "INTEGER NOT NULL DEFAULT 0",
                    "upkeep": "INTEGER NOT NULL DEFAULT 0",
                    "ref": "TEXT NOT NULL DEFAULT ''",
                    "reduction": "REAL NOT NULL DEFAULT 0",
                    "credit": "INTEGER NOT NULL DEFAULT 0",
                    # 8.1.0: deducciones canarias del seguro de impago y de la vivienda vacía.
                    "premium": "INTEGER NOT NULL DEFAULT 0",
                    "bonus": "INTEGER NOT NULL DEFAULT 0",
                },
            )
            # Semana (lunes ISO) en que se cerró la declaración: el plazo para presentarla
            # cuenta desde ahí. NULL en las de antes de la 8.0.0 (ver `_sync_declarations_in`).
            self._add_columns(connection, "economy_declarations", {"filed_week": "TEXT"})
            # 8.7.0: la que sale a pagar se paga al abrirla, entera o en dos plazos.
            self._add_columns(
                connection,
                "economy_irpf_returns",
                {
                    "payment": "TEXT NOT NULL DEFAULT ''",
                    "pay_due": "REAL NOT NULL DEFAULT 0",
                    "second": "INTEGER NOT NULL DEFAULT 0",
                    "second_due": "REAL NOT NULL DEFAULT 0",
                    "second_paid": "INTEGER NOT NULL DEFAULT 0",
                    "second_at": "REAL NOT NULL DEFAULT 0",
                },
            )
            # 8.7.0: el aviso del embargo (`renta.hint`) mira lo cobrado desde la última
            # vez en cada acción con dinero, y el cobro de los plazos busca cada 10 minutos
            # las declaraciones sin pagar.
            connection.execute(
                "CREATE INDEX IF NOT EXISTS economy_debt_payments_member "
                "ON economy_debt_payments (guild_id, user_id, created_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS economy_irpf_returns_payment "
                "ON economy_irpf_returns (guild_id, payment)"
            )
            # Bases de datos de antes del bote misterioso: los jackpots de
            # entonces cayeron todos por la combinación.
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(economy_slots_jackpots)")
            }
            if "mystery" not in columns:
                connection.execute(
                    "ALTER TABLE economy_slots_jackpots "
                    "ADD COLUMN mystery INTEGER NOT NULL DEFAULT 0"
                )
            self._drop_housing_tax_check(connection)
            self._sports_bets_by_market(connection)
        finally:
            connection.close()

    @staticmethod
    def _add_columns(connection: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
        """Añade a `table` las columnas que le falten (`ALTER TABLE ADD COLUMN`).

        Solo añade, con valor por defecto: una versión anterior del bot sigue leyendo la
        tabla sin problemas si hubiera que volver atrás.
        """
        have = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        with connection:
            for name, ddl in columns.items():
                if name not in have:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    @staticmethod
    def _drop_housing_tax_check(connection: sqlite3.Connection) -> None:
        """Quita el `CHECK (kind IN (...))` de `economy_housing_tax` en bases de antes.

        La primera versión de `pisos` solo apuntaba ITP y plusvalía y la tabla lo
        exigía. Las fases 2 a 4 apuntan también IBI, IGIC del vacacional, la tasa de
        la licencia y la SOCIMI: con el `CHECK` viejo, la transacción de `housing`
        entera fallaba (el día de rentas del servidor incluido). `CREATE TABLE IF
        NOT EXISTS` no cambia una tabla que ya existe, así que se rehace sin él,
        copiando las filas, en una sola transacción.
        """
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'economy_housing_tax'"
        ).fetchone()
        if row is None or "CHECK (kind IN" not in (row[0] or ""):
            return
        with connection:
            connection.execute(
                """
                CREATE TABLE economy_housing_tax_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    base INTEGER NOT NULL CHECK (base >= 0),
                    tax INTEGER NOT NULL CHECK (tax > 0),
                    created_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO economy_housing_tax_new
                    (id, guild_id, user_id, kind, base, tax, created_at)
                SELECT id, guild_id, user_id, kind, base, tax, created_at
                FROM economy_housing_tax
                """
            )
            connection.execute("DROP TABLE economy_housing_tax")
            connection.execute("ALTER TABLE economy_housing_tax_new RENAME TO economy_housing_tax")

    @staticmethod
    def _sports_bets_by_market(connection: sqlite3.Connection) -> None:
        """Pasa `economy_sports_bets` de una apuesta por partido a una por mercado (9.22.0).

        La tabla de antes no tiene `market` y su `UNIQUE` es por partido. `CREATE TABLE IF
        NOT EXISTS` no cambia una tabla que ya existe, así que se rehace con la columna y
        el `UNIQUE` nuevo, copiando las filas (todas del 1X2), en una sola transacción. Una
        versión anterior del bot sigue leyéndola: no nombra la columna y su valor por
        defecto es el 1X2.
        """
        columns = {row[1] for row in connection.execute("PRAGMA table_info(economy_sports_bets)")}
        if not columns or "market" in columns:
            return
        with connection:
            connection.execute(
                """
                CREATE TABLE economy_sports_bets_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    sport TEXT NOT NULL,
                    home TEXT NOT NULL,
                    away TEXT NOT NULL,
                    commence REAL NOT NULL,
                    pick TEXT NOT NULL,
                    odds INTEGER NOT NULL CHECK (odds > 100),
                    stake INTEGER NOT NULL CHECK (stake > 0),
                    created_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    payout INTEGER NOT NULL DEFAULT 0,
                    settled_at REAL,
                    market TEXT NOT NULL DEFAULT '1x2',
                    UNIQUE (guild_id, user_id, event_id, market)
                )
                """
            )
            names = (
                "id, guild_id, user_id, channel_id, event_id, sport, home, away, commence, "
                "pick, odds, stake, created_at, status, payout, settled_at"
            )
            connection.execute(
                f"INSERT INTO economy_sports_bets_new ({names}) "
                f"SELECT {names} FROM economy_sports_bets"
            )
            connection.execute("DROP TABLE economy_sports_bets")
            connection.execute("ALTER TABLE economy_sports_bets_new RENAME TO economy_sports_bets")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS economy_sports_bets_open "
                "ON economy_sports_bets (status, commence)"
            )

    # -- Monederos -----------------------------------------------------------------

    def _ensure_wallet(self, connection: sqlite3.Connection, guild_id: int, user_id: int) -> int:
        """Devuelve el saldo, abriendo el monedero con el saldo inicial si no existe.

        Debe llamarse dentro de una transacción ya abierta.
        """
        row = connection.execute(
            "SELECT balance FROM economy_wallets WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        if row is not None:
            return int(row["balance"])
        connection.execute(
            "INSERT INTO economy_wallets (guild_id, user_id, balance) VALUES (?, ?, ?)",
            (guild_id, user_id, self.starting_balance),
        )
        if self.starting_balance:
            self._log(
                connection,
                guild_id,
                user_id,
                self.starting_balance,
                self.starting_balance,
                "bienvenida",
            )
        return self.starting_balance

    @staticmethod
    def _log(
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        delta: int,
        balance_after: int,
        reason: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO economy_ledger (guild_id, user_id, delta, balance_after, reason)
            VALUES (?, ?, ?, ?, ?)
            """,
            (guild_id, user_id, delta, balance_after, reason),
        )

    async def balance(self, guild_id: int, user_id: int) -> int:
        """Devuelve el saldo del usuario, abriendo su monedero si es la primera vez."""
        return await self._run(self._balance_sync, guild_id, user_id)

    def _balance_sync(self, guild_id: int, user_id: int) -> int:
        with self._transaction() as connection:
            return self._ensure_wallet(connection, guild_id, user_id)

    async def contributions(
        self, guild_id: int, user_id: int, start: float, end: float
    ) -> tuple[int, int]:
        """`(por cuenta ajena, RETA)`: lo cotizado entre `start` y `end`.

        Por cuenta ajena, la del trabajador y la de la empresa de las nóminas de España; el
        RETA, las cuotas de autónomo (filas sin bruto de `autonomos`).
        """
        return await self._run(self._contributions_sync, guild_id, user_id, start, end)

    def _contributions_sync(
        self, guild_id: int, user_id: int, start: float, end: float
    ) -> tuple[int, int]:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN concept != 'autonomos' AND country = 'es'
                                      THEN ss_worker + ss_employer END), 0),
                    COALESCE(SUM(CASE WHEN concept = 'autonomos' AND gross = 0
                                      THEN ss_worker END), 0)
                FROM economy_payroll
                WHERE guild_id = ? AND user_id = ? AND created_at >= ? AND created_at < ?
                """,
                (guild_id, user_id, start, end),
            ).fetchone()
        finally:
            connection.close()
        return int(row[0]), int(row[1])

    async def transfer_from_state(
        self, guild_id: int, user_id: int, *, amount: int, reason: str
    ) -> int:
        """Paga `amount` de la cuenta del Estado a un miembro (lo que haya en ella). Devuelve
        el saldo final del miembro."""
        return await self._run(self._transfer_from_state_sync, guild_id, user_id, amount, reason)

    def _transfer_from_state_sync(
        self, guild_id: int, user_id: int, amount: int, reason: str
    ) -> int:
        with self._transaction() as connection:
            self._ensure_wallet(connection, guild_id, user_id)
            paid = min(amount, max(0, self._state_balance_in(connection, guild_id)))
            if not paid:
                return self._ensure_wallet(connection, guild_id, user_id)
            self._apply_in_transaction(
                connection, guild_id, STATE_ACCOUNT_ID, (LedgerEntry(-paid, reason),)
            )
            return self._apply_in_transaction(
                connection, guild_id, user_id, (LedgerEntry(paid, reason),)
            )

    async def account_balance(self, guild_id: int, account: int) -> int:
        """El saldo de una cuenta que no es de un miembro (0 si no existe). No la abre."""
        return await self._run(self._account_balance_sync, guild_id, account)

    def _account_balance_sync(self, guild_id: int, account: int) -> int:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT balance FROM economy_wallets WHERE guild_id = ? AND user_id = ?",
                (guild_id, account),
            ).fetchone()
        finally:
            connection.close()
        return int(row[0]) if row is not None else 0

    async def apply(
        self,
        guild_id: int,
        user_id: int,
        entries: Sequence[LedgerEntry],
        *,
        required_balance: int = 0,
        garnish_at: float | None = None,
    ) -> int:
        """Aplica varios movimientos como una sola operación atómica.

        Los movimientos se aplican en orden y ninguno puede dejar el saldo
        en negativo: así una apuesta (`-100`) seguida de su premio (`+200`)
        exige tener los 100 antes de cobrar. O se aplican todos o ninguno.

        Args:
            guild_id: Servidor del monedero.
            user_id: Dueño del monedero.
            entries: Movimientos a aplicar, en orden.
            required_balance: Saldo mínimo exigido antes de empezar, para
                operaciones cuyo coste no es un movimiento directo.
            garnish_at: Si es dinero nuevo para el miembro, el momento del cobro: lo que
                entre de más paga el embargo de su deuda (`_garnish_in`). Sin él, no se
                embarga nada (apuntes de prueba, el IMV, lo cobrado en negro).

        Returns:
            El saldo final.

        Raises:
            InsufficientFundsError: Si algún paso deja el saldo por debajo de 0
                o no se alcanza `required_balance`. No se aplica nada.
            BalanceLimitError: Si el saldo superaría `MAX_BALANCE`.
        """
        return await self._run(
            self._apply_sync, guild_id, user_id, tuple(entries), required_balance, garnish_at
        )

    def _apply_sync(
        self,
        guild_id: int,
        user_id: int,
        entries: tuple[LedgerEntry, ...],
        required_balance: int,
        garnish_at: float | None = None,
    ) -> int:
        with self._transaction() as connection:
            balance = self._apply_in_transaction(
                connection, guild_id, user_id, entries, required_balance
            )
            if garnish_at is not None:
                received = sum(entry.delta for entry in entries)
                balance -= self._garnish_in(connection, guild_id, user_id, received, garnish_at)
            return balance

    def _apply_in_transaction(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        entries: Sequence[LedgerEntry],
        required_balance: int = 0,
    ) -> int:
        initial = self._ensure_wallet(connection, guild_id, user_id)
        if initial < required_balance:
            raise InsufficientFundsError(initial)
        balance = initial
        for entry in entries:
            balance += entry.delta
            if balance < 0:
                raise InsufficientFundsError(initial)
            if balance > MAX_BALANCE:
                raise BalanceLimitError
            self._log(connection, guild_id, user_id, entry.delta, balance, entry.reason)
        connection.execute(
            "UPDATE economy_wallets SET balance = ? WHERE guild_id = ? AND user_id = ?",
            (balance, guild_id, user_id),
        )
        return balance

    # -- Renta de la semana y retenciones --------------------------------------------

    @staticmethod
    def _record_in(
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        *,
        now: float,
        concept: str,
        kind: str,
        gross: int,
        withheld: int,
        costs: int = 0,
        finance: int = 0,
        upkeep: int = 0,
        ref: str = "",
        reduction: float = 0.0,
        credit: int = 0,
        premium: int = 0,
        bonus: int = 0,
    ) -> None:
        """Apunta un ingreso sujeto con su clase de renta en `economy_tax_records`.

        `gross` es el íntegro en Y$, sin pasar a ninguna escala: la vara del IRPF se
        aplica al integrar la semana (`bot.services.taxes.week_tax`).
        """
        connection.execute(
            """
            INSERT INTO economy_tax_records
                (guild_id, user_id, created_at, concept, gross, withheld, kind, costs,
                 finance, upkeep, ref, reduction, credit, premium, bonus)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id, user_id, now, concept, gross, withheld, kind, costs, finance,
                upkeep, ref, reduction, credit, premium, bonus,
            ),
        )  # fmt: skip

    def _regime_in(self, connection: sqlite3.Connection, guild_id: int, current_week: str) -> str:
        """Primera semana (lunes ISO) que el servidor declara con las reglas de la 8.0.0.

        La primera vez que se pregunta queda fijada a la semana en curso (o a
        `irpf_start`, si es posterior: desplegar el sábado no cambia las reglas de la
        semana que ya se ha vivido). Las anteriores se siguen declarando con la regla
        vieja y la primera ya con la nueva, aunque haya empezado antes del cambio (sus
        filas viejas se leen con `_legacy_income_in`).
        """
        first = max(current_week, self.irpf_start or current_week)
        connection.execute(
            "INSERT OR IGNORE INTO economy_irpf_regime (guild_id, since_week) VALUES (?, ?)",
            (guild_id, first),
        )
        (since,) = connection.execute(
            "SELECT since_week FROM economy_irpf_regime WHERE guild_id = ?", (guild_id,)
        ).fetchone()
        return str(since)

    @staticmethod
    def _legacy_income_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, start: float, end: float
    ) -> tuple[WeekIncome, int]:
        """Las filas de antes de la 8.0.0 de una semana, como renta de la vara nueva.

        Entonces `gross` iba a la escala de la retención de cada fuente: las nóminas, a 10
        Y$ por euro (la de verdad está en `economy_payroll`); la renta de los pisos, a ×
        12 / 365 (`rent_to_fiscal`); el resto, tal cual. Solo hace falta para la semana del
        cambio y el tipo de la siguiente.

        Returns:
            `(renta, nóminas de la semana)`.
        """
        rows = connection.execute(
            """
            SELECT r.concept, r.gross, p.gross AS wage, p.ss_worker, p.country
            FROM economy_tax_records r
            LEFT JOIN economy_payroll p
                ON p.guild_id = r.guild_id AND p.user_id = r.user_id
               AND p.created_at = r.created_at AND p.concept = r.concept
            WHERE r.guild_id = ? AND r.user_id = ? AND r.kind IS NULL
              AND r.created_at >= ? AND r.created_at < ?
            """,
            (guild_id, user_id, start, end),
        ).fetchall()
        work = social = wages = other = prizes = image = business = 0
        for row in rows:
            gross = int(row["gross"])
            concept = str(row["concept"])
            if row["wage"] is not None:
                if (row["country"] or "es") != "es":
                    work += gross * 10  # la parte sujeta de fuera, de 10 a 100 Y$ por euro
                elif not int(row["ss_worker"]) and concept != "pala:pension":
                    business += int(row["wage"])  # un autónomo no cotiza por turno
                else:
                    work += int(row["wage"])
                    social += int(row["ss_worker"])
                    wages += int(row["wage"]) if int(row["ss_worker"]) else 0
            elif concept in _LEGACY_RENT_CONCEPTS:
                other += gross * 365 // 12
            elif concept.endswith(":imagen"):
                image += gross
            else:
                prizes += gross
        income = WeekIncome(
            work=work, social=social, business=business, other=other, prizes=prizes, image=image
        )
        return income, wages

    @staticmethod
    def _carries_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, week: str
    ) -> tuple[dict[str, int], int]:
        """Lo arrastrado de las cuatro semanas anteriores a `week` que sigue vivo.

        Returns:
            `(intereses y reparaciones por inmueble, base negativa)`.
        """
        oldest = (date.fromisoformat(week) - timedelta(days=7 * CARRY_WEEKS)).isoformat()
        rows = connection.execute(
            """
            SELECT kind, ref, SUM(amount) AS amount FROM economy_irpf_carry
            WHERE guild_id = ? AND user_id = ? AND week_start >= ? AND week_start < ?
              AND amount > 0
            GROUP BY kind, ref
            """,
            (guild_id, user_id, oldest, week),
        ).fetchall()
        finance = {str(r["ref"]): int(r["amount"]) for r in rows if r["kind"] == CARRY_FINANCE}
        base = sum(int(r["amount"]) for r in rows if r["kind"] == CARRY_BASE)
        return finance, base

    def _week_income_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        week: FiscalWeek,
        *,
        exclude_day: str | None = None,
    ) -> tuple[WeekIncome, int]:
        """Toda la renta de la base general de un miembro en una semana, por clases.

        Lee los ingresos con retención, los días de casino (sin `exclude_day`, el que se
        está ajustando), la cuota de autónomo (gasto de la actividad) y lo arrastrado de
        las semanas anteriores.

        Returns:
            `(renta, nóminas de la semana)`; las nóminas, para la base de cotización.
        """
        income, wages = self._legacy_income_in(connection, guild_id, user_id, week.start, week.end)
        rows = connection.execute(
            """
            SELECT kind, ref, SUM(gross) AS gross, SUM(costs) AS costs,
                   SUM(finance) AS finance, SUM(upkeep) AS upkeep,
                   SUM(premium) AS premium, SUM(bonus) AS bonus,
                   SUM(gross * reduction) AS reduced
            FROM economy_tax_records
            WHERE guild_id = ? AND user_id = ? AND kind IS NOT NULL
              AND created_at >= ? AND created_at < ?
            GROUP BY kind, ref
            """,
            (guild_id, user_id, week.start, week.end),
        ).fetchall()
        carry, negative = self._carries_in(connection, guild_id, user_id, week.monday)
        amounts: dict[str, int] = {}
        for row in rows:
            kind, gross = str(row["kind"]), int(row["gross"])
            if kind == INCOME_PROPERTY:
                ref = str(row["ref"])
                income = income.plus(
                    prop=PropertyWeek(
                        ref=ref,
                        income=gross,
                        finance=int(row["finance"]),
                        costs=int(row["costs"]),
                        reduction=float(row["reduced"]) / gross if gross > 0 else 0.0,
                        carry=carry.get(ref, 0),
                        upkeep=int(row["upkeep"]),
                        premium=int(row["premium"]),
                        bonus=int(row["bonus"]),
                    )
                )
                continue
            if kind == INCOME_WORK:
                wages += gross
                amounts["social"] = amounts.get("social", 0) + int(row["costs"])
            field = {
                INCOME_WORK: "work",
                INCOME_FOREIGN: "work",
                INCOME_PENSION: "work",
                INCOME_BUSINESS: "business",
                INCOME_PRIZE: "prizes",
                INCOME_IMAGE: "image",
                INCOME_BECKHAM: "beckham",
                INCOME_IMPUTED: "imputed",
            }.get(kind, "prizes")
            amounts[field] = amounts.get(field, 0) + gross
        (fees,) = connection.execute(
            """
            SELECT COALESCE(SUM(ss_worker), 0) FROM economy_payroll
            WHERE guild_id = ? AND user_id = ? AND gross = 0 AND country = 'es'
              AND created_at >= ? AND created_at < ?
            """,
            (guild_id, user_id, week.start, week.end),
        ).fetchone()
        (gambling,) = connection.execute(
            """
            SELECT COALESCE(SUM(net), 0) FROM economy_gambling_days
            WHERE guild_id = ? AND user_id = ? AND day >= ? AND day < ? AND day != ?
            """,
            (guild_id, user_id, week.monday, week.next_monday, exclude_day or ""),
        ).fetchone()
        amounts["business"] = amounts.get("business", 0) - int(fees)
        amounts["gambling"] = int(gambling)
        amounts["negative"] = negative
        return income.plus(**amounts), wages

    def _pace_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        week: FiscalWeek,
        *,
        exclude_day: str | None = None,
    ) -> Pace:
        """Lo que mira la retención de un cobro: el tipo de la semana pasada y la renta de esta.

        El tipo de la semana pasada es el de su declaración, si ya se cerró; si no (las
        primeras horas del lunes, o la semana del cambio a la 8.0.0), el que da su renta.
        """
        current, wages = self._week_income_in(
            connection, guild_id, user_id, week, exclude_day=exclude_day
        )
        previous = week.previous
        row = connection.execute(
            """
            SELECT rate FROM economy_irpf_returns
            WHERE guild_id = ? AND user_id = ? AND week_start = ?
            """,
            (guild_id, user_id, previous.monday),
        ).fetchone()
        last, last_wages = self._week_income_in(connection, guild_id, user_id, previous)
        rate = float(row["rate"]) if row is not None else week_tax(last).rate
        return Pace(previous_rate=rate, previous_wages=last_wages, wages=wages, current=current)

    def _credit_income_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        *,
        gross: int,
        concept: str,
        now: float,
        withhold: Callable[[int, Pace], int],
        week: FiscalWeek,
        kind: str = INCOME_PRIZE,
    ) -> tuple[int, int]:
        """Ingresa `gross`, retiene lo que diga `withhold` y lo registra.

        En el libro quedan dos movimientos, como en una nómina: el bruto
        (`concept`) y la retención (`irpf:concept`). La retención entra en la
        cuenta del Estado en la misma transacción.

        Args:
            withhold: Recibe `(bruto, renta de la semana)` y devuelve la retención en Y$.
                La regla vive en `bot.services.taxes`.
            week: Semana fiscal en curso.
            kind: Clase de renta (`taxes.INCOME_*`); por defecto, un premio.

        Returns:
            `(retención, saldo_final)`.
        """
        pace = self._pace_in(connection, guild_id, user_id, week)
        tax = max(0, min(gross, withhold(gross, pace)))
        entries = [LedgerEntry(gross, concept)]
        if tax:
            entries.append(LedgerEntry(-tax, f"irpf:{concept}"))
        balance = self._apply_in_transaction(connection, guild_id, user_id, entries)
        if tax:
            self._credit_state_in(connection, guild_id, tax, f"irpf:{concept}")
        self._record_in(
            connection, guild_id, user_id, now=now, concept=concept, kind=kind, gross=gross,
            withheld=tax,
        )  # fmt: skip
        balance -= self._garnish_in(connection, guild_id, user_id, gross - tax, now)
        return tax, balance

    async def credit_income(
        self,
        guild_id: int,
        user_id: int,
        *,
        gross: int,
        concept: str,
        now: float,
        withhold: Callable[[int, Pace], int],
        week: FiscalWeek,
        kind: str = INCOME_PRIZE,
    ) -> tuple[int, int]:
        """Versión atómica e independiente de `_credit_income_in`.

        Returns:
            `(retención, saldo_final)`.
        """
        return await self._run(
            self._credit_income_sync, guild_id, user_id, gross, concept, now, withhold, week, kind
        )

    def _credit_income_sync(
        self,
        guild_id: int,
        user_id: int,
        gross: int,
        concept: str,
        now: float,
        withhold: Callable[[int, Pace], int],
        week: FiscalWeek,
        kind: str,
    ) -> tuple[int, int]:
        with self._transaction() as connection:
            return self._credit_income_in(
                connection,
                guild_id,
                user_id,
                gross=gross,
                concept=concept,
                now=now,
                withhold=withhold,
                week=week,
                kind=kind,
            )

    async def week_income(
        self, guild_id: int, user_id: int, week: FiscalWeek
    ) -> tuple[WeekIncome, Pace]:
        """La renta de un miembro en una semana y lo que miraría su próxima retención."""
        return await self._run(self._week_income_sync, guild_id, user_id, week)

    def _week_income_sync(
        self, guild_id: int, user_id: int, week: FiscalWeek
    ) -> tuple[WeekIncome, Pace]:
        connection = self._connect()
        try:
            pace = self._pace_in(connection, guild_id, user_id, week)
            return pace.current, pace
        finally:
            connection.close()

    async def week_withheld(self, guild_id: int, user_id: int, week: FiscalWeek) -> int:
        """Todo lo retenido a un miembro en una semana (ingresos y días de casino)."""
        return await self._run(self._week_withheld_sync, guild_id, user_id, week)

    def _week_withheld_sync(self, guild_id: int, user_id: int, week: FiscalWeek) -> int:
        connection = self._connect()
        try:
            (income,) = connection.execute(
                """
                SELECT COALESCE(SUM(withheld), 0) FROM economy_tax_records
                WHERE guild_id = ? AND user_id = ? AND created_at >= ? AND created_at < ?
                """,
                (guild_id, user_id, week.start, week.end),
            ).fetchone()
            (gambling,) = connection.execute(
                """
                SELECT COALESCE(SUM(withheld), 0) FROM economy_gambling_days
                WHERE guild_id = ? AND user_id = ? AND day >= ? AND day < ?
                """,
                (guild_id, user_id, week.monday, week.next_monday),
            ).fetchone()
            return int(income) + int(gambling)
        finally:
            connection.close()

    async def week_savings(self, guild_id: int, user_id: int, week: FiscalWeek) -> int:
        """Intereses brutos de un miembro en una semana (la base del ahorro)."""
        return await self._run(self._week_savings_sync, guild_id, user_id, week)

    def _week_savings_sync(self, guild_id: int, user_id: int, week: FiscalWeek) -> int:
        connection = self._connect()
        try:
            return self._week_savings_in(connection, guild_id, user_id, week)
        finally:
            connection.close()

    # -- Casino con IRPF -----------------------------------------------------------

    async def settle_gamble(
        self,
        guild_id: int,
        user_id: int,
        entries: Sequence[LedgerEntry],
        *,
        day: str,
        now: float,
        adjust_tax: bool,
        day_tax: Callable[[int], int],
    ) -> BetSettlement:
        """Mueve el dinero de una apuesta y ajusta el IRPF del día, todo atómico.

        El resultado neto del día cambia en la suma de `entries`. Si
        `adjust_tax`, la retención del día se recalcula para que sea siempre
        la que corresponde a la ganancia neta: se cobra la diferencia o, si
        las pérdidas la han reducido, se devuelve desde la cuenta del Estado.

        Args:
            day: Día (ISO, hora canaria) al que se imputa el movimiento.
            adjust_tax: `False` para cobrar una apuesta cuyo resultado llega
                después (blackjack): la retención se ajusta al pagar.
            day_tax: Recibe la ganancia neta del día (≥ 0) y devuelve la retención
                total del día (el 19 % fijo: la declaración del lunes la ajusta).

        Raises:
            InsufficientFundsError: Si el jugador no cubre la apuesta.
            BalanceLimitError: Si el saldo superaría el máximo.
        """
        return await self._run(
            self._settle_gamble_sync,
            guild_id,
            user_id,
            tuple(entries),
            day,
            now,
            adjust_tax,
            day_tax,
        )

    def _settle_gamble_sync(
        self,
        guild_id: int,
        user_id: int,
        entries: tuple[LedgerEntry, ...],
        day: str,
        now: float,
        adjust_tax: bool,
        day_tax: Callable[[int], int],
    ) -> BetSettlement:
        with self._transaction() as connection:
            return self._settle_gamble_in(
                connection,
                guild_id,
                user_id,
                entries,
                day=day,
                now=now,
                adjust_tax=adjust_tax,
                day_tax=day_tax,
            )

    def _settle_gamble_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        entries: Sequence[LedgerEntry],
        *,
        day: str,
        now: float,
        adjust_tax: bool,
        day_tax: Callable[[int], int],
    ) -> BetSettlement:
        """Cuerpo de `settle_gamble` dentro de una transacción ya abierta.

        Si el miembro debe a Hacienda, se embarga el 30 % de lo que la jugada sube la
        ganancia neta del día, ya sin su IRPF: la apuesta propia que vuelve (un empate,
        una devolución) no es dinero nuevo y no se toca.
        """
        balance = self._apply_in_transaction(connection, guild_id, user_id, entries)
        moved = sum(entry.delta for entry in entries)
        connection.execute(
            """
            INSERT INTO economy_gambling_days (guild_id, user_id, day, net, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id, day) DO UPDATE SET
                net = net + excluded.net, updated_at = excluded.updated_at
            """,
            (guild_id, user_id, day, moved, now),
        )
        row = connection.execute(
            """
            SELECT net, withheld FROM economy_gambling_days
            WHERE guild_id = ? AND user_id = ? AND day = ?
            """,
            (guild_id, user_id, day),
        ).fetchone()
        net, withheld = int(row["net"]), int(row["withheld"])
        won = max(0, net) - max(0, net - moved)
        if not adjust_tax:
            balance -= self._garnish_in(connection, guild_id, user_id, won, now)
            return BetSettlement(balance, 0, net, withheld)

        delta = max(0, day_tax(max(net, 0))) - withheld
        if delta > 0:
            # Nunca deja al jugador en negativo; lo que falte se cobra en
            # el siguiente ajuste, porque el objetivo se recalcula siempre.
            delta = min(delta, balance)
            if delta:
                balance = self._apply_in_transaction(
                    connection, guild_id, user_id, (LedgerEntry(-delta, "irpf:juego"),)
                )
                self._credit_state_in(connection, guild_id, delta, "irpf:juego")
        elif delta < 0:
            refund = min(-delta, self._state_balance_in(connection, guild_id))
            delta = -refund
            if refund:
                self._apply_in_transaction(
                    connection,
                    guild_id,
                    STATE_ACCOUNT_ID,
                    (LedgerEntry(-refund, "devolucion:irpf:juego"),),
                )
                balance = self._apply_in_transaction(
                    connection,
                    guild_id,
                    user_id,
                    (LedgerEntry(refund, "devolucion:irpf:juego"),),
                )
        withheld += delta
        connection.execute(
            """
            UPDATE economy_gambling_days SET withheld = ?
            WHERE guild_id = ? AND user_id = ? AND day = ?
            """,
            (withheld, guild_id, user_id, day),
        )
        balance -= self._garnish_in(connection, guild_id, user_id, won - max(0, delta), now)
        return BetSettlement(balance, delta, net, withheld)

    # -- Tragaperras -----------------------------------------------------------------

    def _pot_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        seed: int,
        account: int = SLOTS_POT_ACCOUNT_ID,
        reason: str = "bote:semilla",
    ) -> int:
        """Saldo de un bote (el de la tragaperras salvo otro `account`); lo crea con `seed`."""
        row = connection.execute(
            "SELECT balance FROM economy_wallets WHERE guild_id = ? AND user_id = ?",
            (guild_id, account),
        ).fetchone()
        if row is not None:
            return int(row["balance"])
        connection.execute(
            "INSERT INTO economy_wallets (guild_id, user_id, balance) VALUES (?, ?, 0)",
            (guild_id, account),
        )
        if not seed:
            return 0
        return self._apply_in_transaction(
            connection, guild_id, account, (LedgerEntry(seed, reason),)
        )

    async def slots_pot(self, guild_id: int, *, seed: int) -> int:
        """Bote actual de la tragaperras; lo siembra con `seed` si no existía."""
        return await self._run(self._slots_pot_sync, guild_id, seed)

    def _slots_pot_sync(self, guild_id: int, seed: int) -> int:
        with self._transaction() as connection:
            return self._pot_in(connection, guild_id, seed)

    async def last_jackpot(self, guild_id: int) -> JackpotRecord | None:
        """Último jackpot del servidor, si ha habido alguno."""
        return await self._run(self._last_jackpot_sync, guild_id)

    def _last_jackpot_sync(self, guild_id: int) -> JackpotRecord | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT user_id, amount, won_at, mystery FROM economy_slots_jackpots
                WHERE guild_id = ? ORDER BY id DESC LIMIT 1
                """,
                (guild_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        return JackpotRecord(
            int(row["user_id"]), int(row["amount"]), float(row["won_at"]), bool(row["mystery"])
        )

    async def settle_slots(
        self,
        guild_id: int,
        user_id: int,
        *,
        game: str,
        stake: int,
        payout: int,
        share: int,
        jackpot: bool,
        seed: int,
        day: str,
        now: float,
        day_tax: Callable[[int], int],
        cap: int = 0,
        draw_hit: Callable[[int, int], int] | None = None,
    ) -> SlotsSettlement:
        """Cobra una tirada, paga la línea y mueve el bote, todo en una transacción.

        Para el jugador es una apuesta más (`settle_gamble`): la apuesta, el
        premio y, si hay jackpot, el bote entran en su neto de juego del día
        y en su IRPF. Del dinero apostado, `share` pasa al bote; el resto se
        lo queda la casa. Con jackpot, el jugador se lleva el bote entero
        (con la parte de esta misma tirada) y la casa lo vuelve a sembrar.

        Con `cap > 0` el bote es además «tiene que caer antes de `cap`», como
        los misteriosos de los casinos: cada vez que se siembra se sortea con
        `draw_hit` una cifra oculta entre la semilla y el tope, y la tirada
        cuya aportación hace llegar el bote a esa cifra se lo lleva entero
        aunque la combinación no fuera jackpot (`mystery=True`). Cobra y
        tributa exactamente igual que el jackpot normal. Un giro gratis no
        aporta al bote, así que nunca lo hace caer ni cuenta como tirada.

        Args:
            stake: Apuesta cobrada; 0 en un giro gratis.
            payout: Lo que devuelve la línea, apuesta incluida.
            share: Parte de `stake` que va al bote.
            seed: Lo que pone la casa en un bote nuevo o recién vaciado.
            cap: Tope del bote misterioso; 0 lo desactiva (solo cae por la
                combinación).
            draw_hit: Sorteo de la cifra oculta: recibe `(low, high)` y
                devuelve un entero de ese intervalo, ambos incluidos. Se
                inyecta para que las pruebas fuercen el azar; obligatorio si
                `cap > 0`.

        Raises:
            InsufficientFundsError: Si el jugador no cubre la apuesta. No se
                mueve nada, tampoco el bote.
            BalanceLimitError: Si el saldo superaría el máximo.
        """
        return await self._run(
            self._settle_slots_sync,
            guild_id,
            user_id,
            game,
            stake,
            payout,
            share,
            jackpot,
            seed,
            day,
            now,
            day_tax,
            cap,
            draw_hit,
        )

    def _settle_slots_sync(
        self,
        guild_id: int,
        user_id: int,
        game: str,
        stake: int,
        payout: int,
        share: int,
        jackpot: bool,
        seed: int,
        day: str,
        now: float,
        day_tax: Callable[[int], int],
        cap: int,
        draw_hit: Callable[[int, int], int] | None,
    ) -> SlotsSettlement:
        if stake < 0 or payout < 0 or not 0 <= share <= stake or cap < 0:
            raise ValueError("Movimiento de tragaperras inválido.")
        if cap and draw_hit is None:
            raise ValueError("El bote misterioso necesita `draw_hit`.")
        with self._transaction() as connection:
            pot = self._pot_in(connection, guild_id, seed)
            hit_at, spins = self._mystery_in(connection, guild_id, pot, seed, cap, draw_hit)
            if stake:
                spins += 1
            # El misterio solo lo dispara una aportación: el bote cruza la
            # cifra oculta con el dinero de esta tirada, nunca sin moverse.
            mystery = not jackpot and cap > 0 and share > 0 and pot + share >= hit_at
            won = pot + share if jackpot or mystery else 0
            entries = []
            if stake:
                entries.append(LedgerEntry(-stake, f"{game}:apuesta"))
            if payout:
                entries.append(LedgerEntry(payout, f"{game}:premio"))
            if won:
                entries.append(LedgerEntry(won, f"{game}:bote"))
            bet = self._settle_gamble_in(
                connection,
                guild_id,
                user_id,
                entries,
                day=day,
                now=now,
                adjust_tax=True,
                day_tax=day_tax,
            )
            pot_moves = []
            if share:
                pot_moves.append(LedgerEntry(share, f"{game}:aporte"))
            if won:
                pot_moves.append(LedgerEntry(-won, f"{game}:bote"))
                if seed:
                    pot_moves.append(LedgerEntry(seed, "bote:semilla"))
                connection.execute(
                    """
                    INSERT INTO economy_slots_jackpots
                        (guild_id, user_id, amount, won_at, mystery)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (guild_id, user_id, won, now, int(mystery)),
                )
            if pot_moves:
                pot = self._apply_in_transaction(
                    connection, guild_id, SLOTS_POT_ACCOUNT_ID, pot_moves
                )
            drought = 0
            if won:
                drought, spins = spins, 0
                if cap and draw_hit is not None:
                    hit_at = draw_hit(seed, cap)
            connection.execute(
                "UPDATE economy_slots_mystery SET hit_at = ?, spins = ? WHERE guild_id = ?",
                (hit_at, spins, guild_id),
            )
            return SlotsSettlement(
                bet=bet,
                jackpot=won,
                pot=pot,
                mystery=mystery,
                spins_since=spins,
                drought=drought,
            )

    # -- Timba: póker entre jugadores ------------------------------------------------------

    async def settle_timba(
        self,
        guild_id: int,
        *,
        game: str,
        payouts: dict[int, int],
        rakes: dict[int, int],
        day: str,
        now: float,
        day_tax: Callable[[int], int],
    ) -> dict[int, BetSettlement]:
        """Paga una mano de la timba a todos los que la jugaron, en una sola transacción.

        Lo que cada uno se jugaba ya se cobró al empezar (`place_bet`); aquí se le
        devuelve lo que no gastó más lo que gana (`payouts`, 0 si lo perdió todo)
        y se le ajusta el IRPF del día. La comisión (`rakes`, por ganador) va al
        Estado como Impuesto sobre Actividades de Juego y se apunta en
        `economy_gaming_tax` con `porra_id = 0`.

        Raises:
            BalanceLimitError: Si un saldo superaría el máximo. No se mueve nada.
        """
        return await self._run(
            self._settle_timba_sync,
            guild_id,
            game,
            dict(payouts),
            dict(rakes),
            day,
            now,
            day_tax,
        )

    def _settle_timba_sync(
        self,
        guild_id: int,
        game: str,
        payouts: dict[int, int],
        rakes: dict[int, int],
        day: str,
        now: float,
        day_tax: Callable[[int], int],
    ) -> dict[int, BetSettlement]:
        if any(v < 0 for v in (*payouts.values(), *rakes.values())):
            raise ValueError("Pago de timba inválido.")
        with self._transaction() as connection:
            results: dict[int, BetSettlement] = {}
            for user_id in sorted(payouts):
                amount = payouts[user_id]
                entries = (LedgerEntry(amount, f"{game}:premio"),) if amount else ()
                results[user_id] = self._settle_gamble_in(
                    connection,
                    guild_id,
                    user_id,
                    entries,
                    day=day,
                    now=now,
                    adjust_tax=True,
                    day_tax=day_tax,
                )
            total = sum(rakes.values())
            if total:
                self._credit_state_in(connection, guild_id, total, f"iaj:{game}")
                connection.executemany(
                    """
                    INSERT INTO economy_gaming_tax (guild_id, user_id, porra_id, tax, created_at)
                    VALUES (?, ?, 0, ?, ?)
                    """,
                    [(guild_id, uid, tax, now) for uid, tax in sorted(rakes.items()) if tax],
                )
            return results

    # -- Póker: el 💎 Bote progresivo ---------------------------------------------------

    async def poker_pot(self, guild_id: int, *, seed: int) -> int:
        """El 💎 Bote del póker; lo siembra con `seed` si no existía."""
        return await self._run(self._poker_pot_sync, guild_id, seed)

    def _poker_pot_sync(self, guild_id: int, seed: int) -> int:
        with self._transaction() as connection:
            return self._pot_in(connection, guild_id, seed, POKER_POT_ACCOUNT_ID, "poker:semilla")

    async def settle_poker(
        self,
        guild_id: int,
        user_id: int,
        *,
        game: str,
        payout: int,
        share: int,
        pot_permille: int,
        seed: int,
        day: str,
        now: float,
        day_tax: Callable[[int], int],
    ) -> PokerSettlement:
        """Paga una mano de póker ya cobrada y mueve el 💎 Bote, todo en una transacción.

        Las apuestas (ante, ir y la del bote) ya se cobraron con `place_bet`. Aquí
        se paga `payout` (lo que devuelven la mano y la parte fija del bote), pasa
        `share` de la apuesta del bote al bote común y, si la jugada se lleva una
        parte del bote (`pot_permille`, tanto por mil), se le paga también. Todo
        entra en el neto de juego del día y en su IRPF. Si se lleva el bote entero,
        la casa lo vuelve a sembrar con `seed`.

        Raises:
            BalanceLimitError: Si el saldo superaría el máximo. No se mueve nada.
        """
        return await self._run(
            self._settle_poker_sync,
            guild_id,
            user_id,
            game,
            payout,
            share,
            pot_permille,
            seed,
            day,
            now,
            day_tax,
        )

    def _settle_poker_sync(
        self,
        guild_id: int,
        user_id: int,
        game: str,
        payout: int,
        share: int,
        pot_permille: int,
        seed: int,
        day: str,
        now: float,
        day_tax: Callable[[int], int],
    ) -> PokerSettlement:
        if payout < 0 or share < 0 or not 0 <= pot_permille <= 1000:
            raise ValueError("Pago de póker inválido.")
        with self._transaction() as connection:
            pot = self._pot_in(connection, guild_id, seed, POKER_POT_ACCOUNT_ID, "poker:semilla")
            # La aportación de esta mano entra antes de cobrar: el que se lleva el
            # bote se lleva también lo suyo, como en la tragaperras.
            won = (pot + share) * pot_permille // 1000
            entries = []
            if payout:
                entries.append(LedgerEntry(payout, f"{game}:premio"))
            if won:
                entries.append(LedgerEntry(won, f"{game}:bote"))
            bet = self._settle_gamble_in(
                connection,
                guild_id,
                user_id,
                entries,
                day=day,
                now=now,
                adjust_tax=True,
                day_tax=day_tax,
            )
            pot_moves = []
            if share:
                pot_moves.append(LedgerEntry(share, f"{game}:aporte"))
            if won:
                pot_moves.append(LedgerEntry(-won, f"{game}:bote"))
            if pot_permille == 1000 and seed:
                pot_moves.append(LedgerEntry(seed, "poker:semilla"))
            if pot_moves:
                pot = self._apply_in_transaction(
                    connection, guild_id, POKER_POT_ACCOUNT_ID, pot_moves
                )
            return PokerSettlement(bet=bet, pot_won=won, pot=pot)

    @staticmethod
    def _mystery_in(
        connection: sqlite3.Connection,
        guild_id: int,
        pot: int,
        seed: int,
        cap: int,
        draw_hit: Callable[[int, int], int] | None,
    ) -> tuple[int, int]:
        """Cifra oculta y tiradas sin bote del servidor; crea la fila la primera vez.

        La cifra se sortea desde el bote actual (no desde la semilla) y, si el
        servidor ya tiene un bote por encima del tope, hasta justo por encima
        de él: así cae en cuanto alguien aporte, no nunca. Se sortea al crear
        la fila y también si la guardada no vale para el tope de ahora: sin
        tope (`cap=0`) la fila guarda `hit_at = 0` solo para contar tiradas,
        y si luego se baja el tope una cifra por encima de él no caería
        «antes de X».

        Debe llamarse dentro de una transacción ya abierta. Lo sorteado se
        guarda al final de la tirada, en `_settle_slots_sync`.
        """
        connection.execute(
            "INSERT OR IGNORE INTO economy_slots_mystery (guild_id, hit_at, spins)"
            " VALUES (?, 0, 0)",
            (guild_id,),
        )
        row = connection.execute(
            "SELECT hit_at, spins FROM economy_slots_mystery WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()
        hit_at, spins = int(row["hit_at"]), int(row["spins"])
        high = max(cap, pot + 1)
        if cap and draw_hit is not None and not 0 < hit_at <= high:
            hit_at = draw_hit(max(seed, pot), high)
        return hit_at, spins

    async def slots_spins_since(self, guild_id: int) -> int:
        """Tiradas pagadas desde el último bote del servidor (0 si no hay ninguna)."""
        return await self._run(self._slots_spins_since_sync, guild_id)

    def _slots_spins_since_sync(self, guild_id: int) -> int:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT spins FROM economy_slots_mystery WHERE guild_id = ?", (guild_id,)
            ).fetchone()
        finally:
            connection.close()
        return int(row["spins"]) if row is not None else 0

    # -- Porras --------------------------------------------------------------------

    @staticmethod
    def _porra_settled_in(connection: sqlite3.Connection, guild_id: int, porra_id: int) -> bool:
        row = connection.execute(
            "SELECT 1 FROM economy_porra_settled WHERE guild_id = ? AND porra_id = ?",
            (guild_id, porra_id),
        ).fetchone()
        return row is not None

    @staticmethod
    def _porra_rows_in(
        connection: sqlite3.Connection, guild_id: int, porra_id: int
    ) -> list[tuple[int, int, int]]:
        return [
            (int(user_id), int(outcome), int(stake))
            for user_id, outcome, stake in connection.execute(
                """
                SELECT user_id, outcome, stake FROM economy_porra_bets
                WHERE guild_id = ? AND porra_id = ? ORDER BY id
                """,
                (guild_id, porra_id),
            )
        ]

    async def porra_bet(
        self,
        guild_id: int,
        porra_id: int,
        user_id: int,
        *,
        outcome: int,
        stake: int,
        cap: int,
        game: str,
        day: str,
        now: float,
        day_tax: Callable[[int], int],
    ) -> PorraBetReceipt:
        """Cobra una apuesta a una porra y la deja en el depósito, todo atómico.

        Es una apuesta del casino que se resuelve después (como `place_bet`):
        resta en el día del jugador pero no ajusta su IRPF hasta que se liquide.

        Args:
            outcome: Opción a la que apuesta.
            cap: Tope del bote de la porra.

        Raises:
            PorraClosedError: Si la porra ya está liquidada.
            PorraSideError: Si ya apostó a otra opción de esta porra.
            PorraCapError: Si no cabe en el bote.
            InsufficientFundsError: Si no le llega.
        """
        return await self._run(
            self._porra_bet_sync,
            guild_id,
            porra_id,
            user_id,
            outcome,
            stake,
            cap,
            game,
            day,
            now,
            day_tax,
        )

    def _porra_bet_sync(
        self,
        guild_id: int,
        porra_id: int,
        user_id: int,
        outcome: int,
        stake: int,
        cap: int,
        game: str,
        day: str,
        now: float,
        day_tax: Callable[[int], int],
    ) -> PorraBetReceipt:
        if stake <= 0 or outcome < 0:
            raise ValueError("Apuesta de porra inválida.")
        with self._transaction() as connection:
            if self._porra_settled_in(connection, guild_id, porra_id):
                raise PorraClosedError
            rows = self._porra_rows_in(connection, guild_id, porra_id)
            mine = [row for row in rows if row[0] == user_id]
            if mine and mine[0][1] != outcome:
                raise PorraSideError(mine[0][1])
            pool = sum(row[2] for row in rows)
            if pool + stake > cap:
                raise PorraCapError(max(0, cap - pool))
            bet = self._settle_gamble_in(
                connection,
                guild_id,
                user_id,
                (LedgerEntry(-stake, f"{game}:apuesta"),),
                day=day,
                now=now,
                adjust_tax=False,
                day_tax=day_tax,
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO economy_wallets (guild_id, user_id, balance)
                VALUES (?, ?, 0)
                """,
                (guild_id, PORRA_ACCOUNT_ID),
            )
            self._apply_in_transaction(
                connection, guild_id, PORRA_ACCOUNT_ID, (LedgerEntry(stake, f"{game}:deposito"),)
            )
            connection.execute(
                """
                INSERT INTO economy_porra_bets
                    (guild_id, porra_id, user_id, outcome, stake, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (guild_id, porra_id, user_id, outcome, stake, now),
            )
            return PorraBetReceipt(
                balance=bet.balance,
                stake=sum(row[2] for row in mine) + stake,
                pool=pool + stake,
            )

    async def porra_bets(self, guild_id: int, porra_id: int) -> list[tuple[int, int, int]]:
        """Apuestas de una porra como filas `(miembro, opción, apuesta)`, en orden."""
        return await self._run(self._porra_bets_sync, guild_id, porra_id)

    def _porra_bets_sync(self, guild_id: int, porra_id: int) -> list[tuple[int, int, int]]:
        connection = self._connect()
        try:
            return self._porra_rows_in(connection, guild_id, porra_id)
        finally:
            connection.close()

    async def porra_settled(self, guild_id: int, porra_id: int) -> bool:
        """Si la porra ya está liquidada."""
        return await self._run(self._porra_settled_sync, guild_id, porra_id)

    def _porra_settled_sync(self, guild_id: int, porra_id: int) -> bool:
        connection = self._connect()
        try:
            return self._porra_settled_in(connection, guild_id, porra_id)
        finally:
            connection.close()

    async def settle_porra(
        self,
        guild_id: int,
        porra_id: int,
        *,
        payouts: dict[int, int],
        taxes: dict[int, int],
        refund: bool,
        image: int,
        subject_id: int,
        game: str,
        day: str,
        now: float,
        day_tax: Callable[[int], int],
        week: FiscalWeek,
        image_withhold: Callable[[int, Pace], int],
    ) -> PorraPayment:
        """Vacía el depósito de una porra: premios, IAJ, imagen o devoluciones.

        Todo en una transacción: o cobra todo el mundo o nadie. Comprueba que el
        reparto suma justo lo apostado y que cada apostante sale en `payouts`.

        Args:
            payouts: Lo que vuelve a cada apostante (0 a quien falla).
            taxes: IAJ de cada apostante; va al Estado.
            refund: Si es una devolución (anulada o sin aciertos). Entonces se
                deshace la apuesta (`<juego>:apuesta` en positivo) en vez de
                pagar un premio.
            image: Derechos de imagen brutos del protagonista.
            week: Semana fiscal en curso (para apuntar los derechos de imagen).
            image_withhold: Retención de los derechos de imagen, con la firma de
                `_credit_income_in`.

        Raises:
            PorraClosedError: Si ya estaba liquidada. No se mueve nada.
            ValueError: Si el reparto no cuadra con lo apostado.
        """
        return await self._run(
            self._settle_porra_sync,
            guild_id,
            porra_id,
            dict(payouts),
            dict(taxes),
            refund,
            image,
            subject_id,
            game,
            day,
            now,
            day_tax,
            week,
            image_withhold,
        )

    def _settle_porra_sync(
        self,
        guild_id: int,
        porra_id: int,
        payouts: dict[int, int],
        taxes: dict[int, int],
        refund: bool,
        image: int,
        subject_id: int,
        game: str,
        day: str,
        now: float,
        day_tax: Callable[[int], int],
        week: FiscalWeek,
        image_withhold: Callable[[int, Pace], int],
    ) -> PorraPayment:
        with self._transaction() as connection:
            if self._porra_settled_in(connection, guild_id, porra_id):
                raise PorraClosedError
            rows = self._porra_rows_in(connection, guild_id, porra_id)
            pool = sum(row[2] for row in rows)
            bettors = {row[0] for row in rows}
            if set(payouts) != bettors or not set(taxes) <= bettors:
                raise ValueError("El reparto no tiene a los mismos apostantes que el libro.")
            if min([*payouts.values(), *taxes.values(), image, 0]) < 0:
                raise ValueError("El reparto tiene cantidades negativas.")
            if sum(payouts.values()) + sum(taxes.values()) + image != pool:
                raise ValueError("El reparto no suma lo apostado.")
            if pool:
                self._apply_in_transaction(
                    connection,
                    guild_id,
                    PORRA_ACCOUNT_ID,
                    (LedgerEntry(-pool, f"{game}:deposito"),),
                )
            results: dict[int, BetSettlement] = {}
            for user_id in sorted(bettors):
                amount = payouts[user_id]
                reason = f"{game}:apuesta" if refund else f"{game}:premio"
                entries = (LedgerEntry(amount, reason),) if amount else ()
                results[user_id] = self._settle_gamble_in(
                    connection,
                    guild_id,
                    user_id,
                    entries,
                    day=day,
                    now=now,
                    adjust_tax=True,
                    day_tax=day_tax,
                )
            tax_total = sum(taxes.values())
            if tax_total:
                self._credit_state_in(connection, guild_id, tax_total, f"iaj:{game}")
                connection.executemany(
                    """
                    INSERT INTO economy_gaming_tax (guild_id, user_id, porra_id, tax, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (guild_id, user_id, porra_id, tax, now)
                        for user_id, tax in sorted(taxes.items())
                        if tax
                    ],
                )
            image_tax, subject_balance = 0, None
            if image:
                image_tax, subject_balance = self._credit_income_in(
                    connection,
                    guild_id,
                    subject_id,
                    gross=image,
                    concept=f"{game}:imagen",
                    now=now,
                    withhold=image_withhold,
                    week=week,
                    kind=INCOME_IMAGE,
                )
            connection.execute(
                """
                INSERT INTO economy_porra_settled (guild_id, porra_id, refund, settled_at)
                VALUES (?, ?, ?, ?)
                """,
                (guild_id, porra_id, int(refund), now),
            )
            return PorraPayment(bets=results, image_tax=image_tax, subject_balance=subject_balance)

    # -- Apuestas deportivas -------------------------------------------------------

    async def sports_bet(
        self, bet: SportsBet, *, game: str, cap: int | None = None
    ) -> tuple[SportsBet, int]:
        """Cobra una apuesta deportiva y la deja en el depósito, todo atómico.

        El dinero sale ya del monedero, pero no entra en el día del casino hasta que se
        resuelve (`settle_sports_bet`): la ganancia o pérdida de una apuesta es del día
        en que se sabe el resultado.

        Args:
            bet: La apuesta sin guardar (`id` 0).
            game: Prefijo de los motivos del libro (`deportes:deposito`).
            cap: Lo más que se puede apostar al partido, sumando sus mercados (`None`,
                sin tope).

        Returns:
            La apuesta guardada, con su `id`, y el saldo de quien apuesta.

        Raises:
            SportsBetTakenError: Ya tiene una apuesta a ese mercado del partido. No se
                cobra nada.
            SportsStakeCapError: Pasaría el tope del partido. No se cobra nada.
            InsufficientFundsError: No le llega.
        """
        return await self._run(self._sports_bet_sync, bet, game, cap)

    def _sports_bet_sync(self, bet: SportsBet, game: str, cap: int | None) -> tuple[SportsBet, int]:
        if bet.stake <= 0 or bet.odds <= 100:
            raise ValueError("Apuesta deportiva inválida.")
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT market, stake FROM economy_sports_bets
                WHERE guild_id = ? AND user_id = ? AND event_id = ?
                """,
                (bet.guild_id, bet.user_id, bet.event_id),
            ).fetchall()
            if any(row["market"] == bet.market for row in rows):
                raise SportsBetTakenError
            staked = sum(int(row["stake"]) for row in rows)
            staked += self._combo_staked_in(connection, bet.guild_id, bet.user_id, bet.event_id)
            if cap is not None and staked + bet.stake > cap:
                raise SportsStakeCapError(max(0, cap - staked))
            balance = self._apply_in_transaction(
                connection,
                bet.guild_id,
                bet.user_id,
                (LedgerEntry(-bet.stake, f"{game}:deposito"),),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO economy_wallets (guild_id, user_id, balance)
                VALUES (?, ?, 0)
                """,
                (bet.guild_id, SPORTS_ACCOUNT_ID),
            )
            self._apply_in_transaction(
                connection,
                bet.guild_id,
                SPORTS_ACCOUNT_ID,
                (LedgerEntry(bet.stake, f"{game}:deposito"),),
            )
            cursor = connection.execute(
                """
                INSERT INTO economy_sports_bets
                    (guild_id, user_id, channel_id, event_id, sport, home, away, commence,
                     pick, odds, stake, created_at, market)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bet.guild_id,
                    bet.user_id,
                    bet.channel_id,
                    bet.event_id,
                    bet.sport,
                    bet.home,
                    bet.away,
                    bet.commence,
                    bet.pick,
                    bet.odds,
                    bet.stake,
                    bet.created_at,
                    bet.market,
                ),
            )
            return replace(bet, id=int(cursor.lastrowid or 0), status="open"), balance

    async def settle_sports_bet(
        self,
        guild_id: int,
        bet_id: int,
        *,
        payout: int,
        game: str,
        day: str,
        now: float,
        day_tax: Callable[[int], int],
    ) -> tuple[SportsBet, BetSettlement] | None:
        """Resuelve una apuesta deportiva: vacía su depósito y paga, todo atómico.

        Lo apostado vuelve del depósito y se juega en el día de hoy junto al premio
        (`<juego>:apuesta` y `<juego>:premio`), así el día del casino ve la ganancia neta
        de la apuesta y su IRPF se ajusta como en cualquier jugada.

        Args:
            payout: Lo que cobra, apuesta incluida; 0 si ha fallado.

        Returns:
            La apuesta resuelta y lo que ha movido en su día del casino, o `None` si ya
            estaba resuelta (no se mueve nada).

        Raises:
            BalanceLimitError: Si el premio pasaría el saldo máximo. No se mueve nada.
        """
        return await self._run(
            self._settle_sports_bet_sync, guild_id, bet_id, payout, game, day, now, day_tax
        )

    def _settle_sports_bet_sync(
        self,
        guild_id: int,
        bet_id: int,
        payout: int,
        game: str,
        day: str,
        now: float,
        day_tax: Callable[[int], int],
    ) -> tuple[SportsBet, BetSettlement] | None:
        if payout < 0:
            raise ValueError("El premio no puede ser negativo.")
        with self._transaction() as connection:
            bet = self._open_sports_bet_in(connection, guild_id, bet_id)
            if bet is None:
                return None
            self._apply_in_transaction(
                connection,
                guild_id,
                SPORTS_ACCOUNT_ID,
                (LedgerEntry(-bet.stake, f"{game}:deposito"),),
            )
            # Lo apostado vuelve al monedero fuera del día del casino y se juega dentro:
            # el día solo ve la apuesta y el premio, no el depósito.
            self._apply_in_transaction(
                connection, guild_id, bet.user_id, (LedgerEntry(bet.stake, f"{game}:deposito"),)
            )
            entries = [LedgerEntry(-bet.stake, f"{game}:apuesta")]
            if payout:
                entries.append(LedgerEntry(payout, f"{game}:premio"))
            result = self._settle_gamble_in(
                connection,
                guild_id,
                bet.user_id,
                entries,
                day=day,
                now=now,
                adjust_tax=True,
                day_tax=day_tax,
            )
            status = "won" if payout else "lost"
            self._close_sports_bet_in(connection, bet, status, payout, now)
            return replace(bet, status=status, payout=payout, settled_at=now), result

    async def void_sports_bet(
        self, guild_id: int, bet_id: int, *, game: str, now: float
    ) -> tuple[SportsBet, int] | None:
        """Anula una apuesta deportiva y devuelve lo apostado, sin tocar el casino.

        Returns:
            La apuesta anulada y el saldo de su dueño, o `None` si ya estaba resuelta.
        """
        return await self._run(self._void_sports_bet_sync, guild_id, bet_id, game, now)

    def _void_sports_bet_sync(
        self, guild_id: int, bet_id: int, game: str, now: float
    ) -> tuple[SportsBet, int] | None:
        with self._transaction() as connection:
            bet = self._open_sports_bet_in(connection, guild_id, bet_id)
            if bet is None:
                return None
            self._apply_in_transaction(
                connection,
                guild_id,
                SPORTS_ACCOUNT_ID,
                (LedgerEntry(-bet.stake, f"{game}:deposito"),),
            )
            # Dinero propio que vuelve: ni casino ni embargo (Biblia, «Embargo de la deuda»).
            balance = self._apply_in_transaction(
                connection,
                guild_id,
                bet.user_id,
                (LedgerEntry(bet.stake, f"{game}:devolucion"),),
            )
            self._close_sports_bet_in(connection, bet, "void", bet.stake, now)
            return replace(bet, status="void", payout=bet.stake, settled_at=now), balance

    @staticmethod
    def _open_sports_bet_in(
        connection: sqlite3.Connection, guild_id: int, bet_id: int
    ) -> SportsBet | None:
        row = connection.execute(
            """
            SELECT * FROM economy_sports_bets
            WHERE guild_id = ? AND id = ? AND status = 'open'
            """,
            (guild_id, bet_id),
        ).fetchone()
        return _sports_bet(row) if row is not None else None

    @staticmethod
    def _close_sports_bet_in(
        connection: sqlite3.Connection, bet: SportsBet, status: str, payout: int, now: float
    ) -> None:
        connection.execute(
            """
            UPDATE economy_sports_bets SET status = ?, payout = ?, settled_at = ?
            WHERE guild_id = ? AND id = ?
            """,
            (status, payout, now, bet.guild_id, bet.id),
        )

    async def open_sports_bets(self) -> list[SportsBet]:
        """Las apuestas deportivas sin resolver de todos los servidores, por comienzo."""
        return await self._run(self._open_sports_bets_sync)

    def _open_sports_bets_sync(self) -> list[SportsBet]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM economy_sports_bets WHERE status = 'open'
                ORDER BY commence, id
                """
            ).fetchall()
        finally:
            connection.close()
        return [_sports_bet(row) for row in rows]

    async def sports_match_bets(
        self, guild_id: int, user_id: int, event_id: str
    ) -> list[SportsBet]:
        """Las apuestas de alguien a un partido, una por mercado."""
        return await self._run(self._sports_match_bets_sync, guild_id, user_id, event_id)

    def _sports_match_bets_sync(
        self, guild_id: int, user_id: int, event_id: str
    ) -> list[SportsBet]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM economy_sports_bets
                WHERE guild_id = ? AND user_id = ? AND event_id = ?
                ORDER BY id
                """,
                (guild_id, user_id, event_id),
            ).fetchall()
        finally:
            connection.close()
        return [_sports_bet(row) for row in rows]

    # -- Combinadas deportivas -------------------------------------------------------

    @staticmethod
    def _single_staked_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, event_id: str
    ) -> int:
        """Lo apostado en sencillas a un partido."""
        row = connection.execute(
            """
            SELECT COALESCE(SUM(stake), 0) FROM economy_sports_bets
            WHERE guild_id = ? AND user_id = ? AND event_id = ?
            """,
            (guild_id, user_id, event_id),
        ).fetchone()
        return int(row[0])

    @staticmethod
    def _combo_staked_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, event_id: str
    ) -> int:
        """Lo apostado en combinadas abiertas que tocan un partido (entero en cada uno).

        Las ya perdidas no cuentan: una pata fallada en otro partido deja este sin riesgo.
        """
        row = connection.execute(
            """
            SELECT COALESCE(SUM(c.stake), 0) FROM economy_sports_combos AS c
            JOIN economy_sports_combo_legs AS l ON l.combo_id = c.id
            WHERE c.guild_id = ? AND c.user_id = ? AND c.status = 'open' AND l.event_id = ?
            """,
            (guild_id, user_id, event_id),
        ).fetchone()
        return int(row[0])

    async def sports_staked(
        self, guild_id: int, user_id: int, event_ids: Sequence[str]
    ) -> dict[str, int]:
        """Lo que alguien lleva apostado a cada partido, sencillas y combinadas abiertas."""
        return await self._run(self._sports_staked_sync, guild_id, user_id, tuple(event_ids))

    def _sports_staked_sync(
        self, guild_id: int, user_id: int, event_ids: tuple[str, ...]
    ) -> dict[str, int]:
        connection = self._connect()
        try:
            return {
                event_id: self._single_staked_in(connection, guild_id, user_id, event_id)
                + self._combo_staked_in(connection, guild_id, user_id, event_id)
                for event_id in event_ids
            }
        finally:
            connection.close()

    async def sports_combo_bet(
        self, combo: SportsCombo, *, game: str, cap: int | None = None
    ) -> tuple[SportsCombo, int]:
        """Cobra una combinada y la deja en el depósito de las deportivas, todo atómico.

        Como `sports_bet`: el dinero sale ya del monedero pero no entra en el día del
        casino hasta que se resuelve.

        Args:
            combo: La combinada sin guardar (`id` 0), con sus patas.
            game: Prefijo de los motivos del libro (`deportes:deposito`).
            cap: Lo más que se puede apostar a cada partido, sumando sencillas y
                combinadas (`None`, sin tope). Lo de la combinada cuenta entero en cada uno.

        Returns:
            La combinada guardada, con su `id`, y el saldo de quien apuesta.

        Raises:
            SportsStakeCapError: Pasaría el tope de algún partido (`room`, lo que cabe en
                el más cargado). No se cobra nada.
            InsufficientFundsError: No le llega.
        """
        return await self._run(self._sports_combo_bet_sync, combo, game, cap)

    def _sports_combo_bet_sync(
        self, combo: SportsCombo, game: str, cap: int | None
    ) -> tuple[SportsCombo, int]:
        events = [leg.event_id for leg in combo.legs]
        if (
            combo.stake <= 0
            or combo.odds <= 100
            or len(combo.legs) < 2
            or len(set(events)) != len(events)
            or any(leg.odds <= 100 for leg in combo.legs)
        ):
            raise ValueError("Combinada deportiva inválida.")
        with self._transaction() as connection:
            if cap is not None:
                staked = max(
                    self._single_staked_in(connection, combo.guild_id, combo.user_id, event)
                    + self._combo_staked_in(connection, combo.guild_id, combo.user_id, event)
                    for event in events
                )
                if staked + combo.stake > cap:
                    raise SportsStakeCapError(max(0, cap - staked))
            balance = self._apply_in_transaction(
                connection,
                combo.guild_id,
                combo.user_id,
                (LedgerEntry(-combo.stake, f"{game}:deposito"),),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO economy_wallets (guild_id, user_id, balance)
                VALUES (?, ?, 0)
                """,
                (combo.guild_id, SPORTS_ACCOUNT_ID),
            )
            self._apply_in_transaction(
                connection,
                combo.guild_id,
                SPORTS_ACCOUNT_ID,
                (LedgerEntry(combo.stake, f"{game}:deposito"),),
            )
            cursor = connection.execute(
                """
                INSERT INTO economy_sports_combos
                    (guild_id, user_id, channel_id, odds, stake, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    combo.guild_id,
                    combo.user_id,
                    combo.channel_id,
                    combo.odds,
                    combo.stake,
                    combo.created_at,
                ),
            )
            combo_id = int(cursor.lastrowid or 0)
            connection.executemany(
                """
                INSERT INTO economy_sports_combo_legs
                    (combo_id, position, event_id, sport, home, away, commence, market,
                     pick, odds)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        combo_id,
                        position,
                        leg.event_id,
                        leg.sport,
                        leg.home,
                        leg.away,
                        leg.commence,
                        leg.market,
                        leg.pick,
                        leg.odds,
                    )
                    for position, leg in enumerate(combo.legs)
                ],
            )
            legs = tuple(replace(leg, status="open") for leg in combo.legs)
            return replace(combo, id=combo_id, status="open", legs=legs), balance

    async def mark_sports_legs(
        self, guild_id: int, combo_id: int, statuses: dict[str, str]
    ) -> SportsCombo | None:
        """Apunta cómo han quedado algunas patas de una combinada que sigue abierta.

        No mueve dinero: sirve para que 🎟️ Mis apuestas enseñe por dónde va.

        Args:
            statuses: Estado nuevo de cada pata, por partido.

        Returns:
            La combinada con sus patas al día, o `None` si ya estaba resuelta.
        """
        return await self._run(self._mark_sports_legs_sync, guild_id, combo_id, statuses)

    def _mark_sports_legs_sync(
        self, guild_id: int, combo_id: int, statuses: dict[str, str]
    ) -> SportsCombo | None:
        with self._transaction() as connection:
            if self._open_sports_combo_in(connection, guild_id, combo_id) is None:
                return None
            self._mark_legs_in(connection, combo_id, statuses)
            return self._open_sports_combo_in(connection, guild_id, combo_id)

    async def settle_sports_combo(
        self,
        guild_id: int,
        combo_id: int,
        *,
        statuses: dict[str, str],
        payout: int,
        game: str,
        day: str,
        now: float,
        day_tax: Callable[[int], int],
    ) -> tuple[SportsCombo, BetSettlement] | None:
        """Resuelve una combinada: apunta sus patas, vacía su depósito y paga, todo atómico.

        Igual que `settle_sports_bet`: lo apostado vuelve del depósito y se juega en el
        día de hoy junto al premio, así el día del casino ve la ganancia neta.

        Args:
            statuses: Estado de las patas que acaban de resolverse, por partido.
            payout: Lo que cobra, apuesta incluida; 0 si ha fallado.

        Returns:
            La combinada resuelta y lo que ha movido en su día del casino, o `None` si ya
            estaba resuelta (no se mueve nada).

        Raises:
            BalanceLimitError: Si el premio pasaría el saldo máximo. No se mueve nada.
        """
        return await self._run(
            self._settle_sports_combo_sync,
            guild_id,
            combo_id,
            statuses,
            payout,
            game,
            day,
            now,
            day_tax,
        )

    def _settle_sports_combo_sync(
        self,
        guild_id: int,
        combo_id: int,
        statuses: dict[str, str],
        payout: int,
        game: str,
        day: str,
        now: float,
        day_tax: Callable[[int], int],
    ) -> tuple[SportsCombo, BetSettlement] | None:
        if payout < 0:
            raise ValueError("El premio no puede ser negativo.")
        with self._transaction() as connection:
            combo = self._open_sports_combo_in(connection, guild_id, combo_id)
            if combo is None:
                return None
            self._apply_in_transaction(
                connection,
                guild_id,
                SPORTS_ACCOUNT_ID,
                (LedgerEntry(-combo.stake, f"{game}:deposito"),),
            )
            # Como en las sencillas: el depósito vuelve fuera del día del casino y la
            # apuesta y el premio se juegan dentro.
            self._apply_in_transaction(
                connection,
                guild_id,
                combo.user_id,
                (LedgerEntry(combo.stake, f"{game}:deposito"),),
            )
            entries = [LedgerEntry(-combo.stake, f"{game}:apuesta")]
            if payout:
                entries.append(LedgerEntry(payout, f"{game}:premio"))
            result = self._settle_gamble_in(
                connection,
                guild_id,
                combo.user_id,
                entries,
                day=day,
                now=now,
                adjust_tax=True,
                day_tax=day_tax,
            )
            status = "won" if payout else "lost"
            self._mark_legs_in(connection, combo_id, statuses)
            self._close_sports_combo_in(connection, guild_id, combo_id, status, payout, now)
            closed = self._sports_combo_in(connection, guild_id, combo_id)
            assert closed is not None
            return closed, result

    async def void_sports_combo(
        self, guild_id: int, combo_id: int, *, statuses: dict[str, str], game: str, now: float
    ) -> tuple[SportsCombo, int] | None:
        """Anula una combinada (todas sus patas anuladas) y devuelve lo apostado.

        Returns:
            La combinada anulada y el saldo de su dueño, o `None` si ya estaba resuelta.
        """
        return await self._run(
            self._void_sports_combo_sync, guild_id, combo_id, statuses, game, now
        )

    def _void_sports_combo_sync(
        self, guild_id: int, combo_id: int, statuses: dict[str, str], game: str, now: float
    ) -> tuple[SportsCombo, int] | None:
        with self._transaction() as connection:
            combo = self._open_sports_combo_in(connection, guild_id, combo_id)
            if combo is None:
                return None
            self._apply_in_transaction(
                connection,
                guild_id,
                SPORTS_ACCOUNT_ID,
                (LedgerEntry(-combo.stake, f"{game}:deposito"),),
            )
            # Dinero propio que vuelve: ni casino ni embargo, como `void_sports_bet`.
            balance = self._apply_in_transaction(
                connection,
                guild_id,
                combo.user_id,
                (LedgerEntry(combo.stake, f"{game}:devolucion"),),
            )
            self._mark_legs_in(connection, combo_id, statuses)
            self._close_sports_combo_in(connection, guild_id, combo_id, "void", combo.stake, now)
            closed = self._sports_combo_in(connection, guild_id, combo_id)
            assert closed is not None
            return closed, balance

    @staticmethod
    def _mark_legs_in(
        connection: sqlite3.Connection, combo_id: int, statuses: dict[str, str]
    ) -> None:
        connection.executemany(
            """
            UPDATE economy_sports_combo_legs SET status = ?
            WHERE combo_id = ? AND event_id = ?
            """,
            [(status, combo_id, event_id) for event_id, status in statuses.items()],
        )

    @staticmethod
    def _close_sports_combo_in(
        connection: sqlite3.Connection,
        guild_id: int,
        combo_id: int,
        status: str,
        payout: int,
        now: float,
    ) -> None:
        connection.execute(
            """
            UPDATE economy_sports_combos SET status = ?, payout = ?, settled_at = ?
            WHERE guild_id = ? AND id = ?
            """,
            (status, payout, now, guild_id, combo_id),
        )

    @staticmethod
    def _combos_with_legs(
        connection: sqlite3.Connection, rows: Sequence[sqlite3.Row]
    ) -> list[SportsCombo]:
        """Las combinadas de `rows` con sus patas, en el mismo orden."""
        if not rows:
            return []
        ids = [int(row["id"]) for row in rows]
        marks = ", ".join("?" for _ in ids)
        legs: dict[int, list[SportsLeg]] = {combo_id: [] for combo_id in ids}
        for leg in connection.execute(
            f"""
            SELECT * FROM economy_sports_combo_legs
            WHERE combo_id IN ({marks}) ORDER BY combo_id, position
            """,  # noqa: S608 - solo marcas `?`, los ids van como parámetros
            ids,
        ).fetchall():
            legs[int(leg["combo_id"])].append(_sports_leg(leg))
        return [_sports_combo(row, legs[int(row["id"])]) for row in rows]

    def _sports_combo_in(
        self, connection: sqlite3.Connection, guild_id: int, combo_id: int
    ) -> SportsCombo | None:
        row = connection.execute(
            "SELECT * FROM economy_sports_combos WHERE guild_id = ? AND id = ?",
            (guild_id, combo_id),
        ).fetchone()
        return self._combos_with_legs(connection, [row])[0] if row is not None else None

    def _open_sports_combo_in(
        self, connection: sqlite3.Connection, guild_id: int, combo_id: int
    ) -> SportsCombo | None:
        combo = self._sports_combo_in(connection, guild_id, combo_id)
        return combo if combo is not None and combo.status == "open" else None

    async def open_sports_combos(self) -> list[SportsCombo]:
        """Las combinadas sin resolver de todos los servidores, con sus patas."""
        return await self._run(self._open_sports_combos_sync)

    def _open_sports_combos_sync(self) -> list[SportsCombo]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM economy_sports_combos WHERE status = 'open' ORDER BY id"
            ).fetchall()
            return self._combos_with_legs(connection, rows)
        finally:
            connection.close()

    async def sports_combos(self, guild_id: int, user_id: int, *, limit: int) -> list[SportsCombo]:
        """Las combinadas de alguien: las abiertas y luego las últimas resueltas."""
        return await self._run(self._sports_combos_sync, guild_id, user_id, limit)

    def _sports_combos_sync(self, guild_id: int, user_id: int, limit: int) -> list[SportsCombo]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM economy_sports_combos WHERE guild_id = ? AND user_id = ?
                ORDER BY status = 'open' DESC, COALESCE(settled_at, created_at) DESC, id DESC
                LIMIT ?
                """,
                (guild_id, user_id, limit),
            ).fetchall()
            return self._combos_with_legs(connection, rows)
        finally:
            connection.close()

    async def sports_bets(self, guild_id: int, user_id: int, *, limit: int) -> list[SportsBet]:
        """Las apuestas deportivas de alguien: las abiertas y luego las últimas resueltas."""
        return await self._run(self._sports_bets_sync, guild_id, user_id, limit)

    def _sports_bets_sync(self, guild_id: int, user_id: int, limit: int) -> list[SportsBet]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM economy_sports_bets WHERE guild_id = ? AND user_id = ?
                ORDER BY status = 'open' DESC, COALESCE(settled_at, commence) DESC, id DESC
                LIMIT ?
                """,
                (guild_id, user_id, limit),
            ).fetchall()
        finally:
            connection.close()
        return [_sports_bet(row) for row in rows]

    # -- Predicciones (`predice`) ----------------------------------------------------

    async def predict_bet(
        self, bet: PredictBet, *, game: str, cap: int | None = None
    ) -> tuple[PredictBet, int]:
        """Cobra una predicción y la deja en su depósito, todo atómico.

        Como `sports_bet`: el dinero sale ya del monedero, pero no entra en el día del
        casino hasta que se resuelve (`settle_predict_bet`).

        Args:
            bet: La predicción sin guardar (`id` 0).
            game: Prefijo de los motivos del libro (`predice:deposito`).
            cap: Lo más que se puede apostar al evento, sumando sus preguntas (`None`,
                sin tope).

        Returns:
            La predicción guardada, con su `id`, y el saldo de quien apuesta.

        Raises:
            PredictBetTakenError: Ya tiene una predicción a esa pregunta. No se cobra nada.
            PredictStakeCapError: Pasaría el tope del evento. No se cobra nada.
            InsufficientFundsError: No le llega.
        """
        return await self._run(self._predict_bet_sync, bet, game, cap)

    def _predict_bet_sync(
        self, bet: PredictBet, game: str, cap: int | None
    ) -> tuple[PredictBet, int]:
        if bet.stake <= 0 or bet.odds <= 100 or bet.side not in (0, 1):
            raise ValueError("Predicción inválida.")
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT market_id, stake FROM economy_predict_bets
                WHERE guild_id = ? AND user_id = ? AND event_id = ?
                """,
                (bet.guild_id, bet.user_id, bet.event_id),
            ).fetchall()
            if any(row["market_id"] == bet.market_id for row in rows):
                raise PredictBetTakenError
            taken = connection.execute(
                """
                SELECT 1 FROM economy_predict_bets
                WHERE guild_id = ? AND user_id = ? AND market_id = ?
                """,
                (bet.guild_id, bet.user_id, bet.market_id),
            ).fetchone()
            if taken is not None:
                # La misma pregunta guardada con otro evento (Polymarket la movió).
                raise PredictBetTakenError
            staked = sum(int(row["stake"]) for row in rows)
            if cap is not None and staked + bet.stake > cap:
                raise PredictStakeCapError(max(0, cap - staked))
            balance = self._apply_in_transaction(
                connection,
                bet.guild_id,
                bet.user_id,
                (LedgerEntry(-bet.stake, f"{game}:deposito"),),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO economy_wallets (guild_id, user_id, balance)
                VALUES (?, ?, 0)
                """,
                (bet.guild_id, PREDICT_ACCOUNT_ID),
            )
            self._apply_in_transaction(
                connection,
                bet.guild_id,
                PREDICT_ACCOUNT_ID,
                (LedgerEntry(bet.stake, f"{game}:deposito"),),
            )
            cursor = connection.execute(
                """
                INSERT INTO economy_predict_bets
                    (guild_id, user_id, channel_id, event_id, event_title, market_id,
                     question, side, side_label, section, odds, stake, ends, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bet.guild_id,
                    bet.user_id,
                    bet.channel_id,
                    bet.event_id,
                    bet.event_title,
                    bet.market_id,
                    bet.question,
                    bet.side,
                    bet.side_label,
                    bet.section,
                    bet.odds,
                    bet.stake,
                    bet.ends,
                    bet.created_at,
                ),
            )
            return replace(bet, id=int(cursor.lastrowid or 0), status="open"), balance

    async def settle_predict_bet(
        self,
        guild_id: int,
        bet_id: int,
        *,
        payout: int,
        game: str,
        day: str,
        now: float,
        day_tax: Callable[[int], int],
    ) -> tuple[PredictBet, BetSettlement] | None:
        """Resuelve una predicción: vacía su depósito y paga, todo atómico.

        Como `settle_sports_bet`: lo apostado vuelve del depósito y se juega en el día de
        hoy junto al premio (`<juego>:apuesta` y `<juego>:premio`).

        Args:
            payout: Lo que cobra, apuesta incluida; 0 si ha fallado.

        Returns:
            La predicción resuelta y lo que ha movido en su día del casino, o `None` si ya
            estaba resuelta (no se mueve nada).

        Raises:
            BalanceLimitError: Si el premio pasaría el saldo máximo. No se mueve nada.
        """
        return await self._run(
            self._settle_predict_bet_sync, guild_id, bet_id, payout, game, day, now, day_tax
        )

    def _settle_predict_bet_sync(
        self,
        guild_id: int,
        bet_id: int,
        payout: int,
        game: str,
        day: str,
        now: float,
        day_tax: Callable[[int], int],
    ) -> tuple[PredictBet, BetSettlement] | None:
        if payout < 0:
            raise ValueError("El premio no puede ser negativo.")
        with self._transaction() as connection:
            bet = self._open_predict_bet_in(connection, guild_id, bet_id)
            if bet is None:
                return None
            self._apply_in_transaction(
                connection,
                guild_id,
                PREDICT_ACCOUNT_ID,
                (LedgerEntry(-bet.stake, f"{game}:deposito"),),
            )
            # Lo apostado vuelve al monedero fuera del día del casino y se juega dentro:
            # el día solo ve la apuesta y el premio, no el depósito.
            self._apply_in_transaction(
                connection, guild_id, bet.user_id, (LedgerEntry(bet.stake, f"{game}:deposito"),)
            )
            entries = [LedgerEntry(-bet.stake, f"{game}:apuesta")]
            if payout:
                entries.append(LedgerEntry(payout, f"{game}:premio"))
            result = self._settle_gamble_in(
                connection,
                guild_id,
                bet.user_id,
                entries,
                day=day,
                now=now,
                adjust_tax=True,
                day_tax=day_tax,
            )
            status = "won" if payout else "lost"
            self._close_predict_bet_in(connection, bet, status, payout, now)
            return replace(bet, status=status, payout=payout, settled_at=now), result

    async def void_predict_bet(
        self, guild_id: int, bet_id: int, *, game: str, now: float
    ) -> tuple[PredictBet, int] | None:
        """Anula una predicción y devuelve lo apostado, sin tocar el casino.

        Returns:
            La predicción anulada y el saldo de su dueño, o `None` si ya estaba resuelta.
        """
        return await self._run(self._void_predict_bet_sync, guild_id, bet_id, game, now)

    def _void_predict_bet_sync(
        self, guild_id: int, bet_id: int, game: str, now: float
    ) -> tuple[PredictBet, int] | None:
        with self._transaction() as connection:
            bet = self._open_predict_bet_in(connection, guild_id, bet_id)
            if bet is None:
                return None
            self._apply_in_transaction(
                connection,
                guild_id,
                PREDICT_ACCOUNT_ID,
                (LedgerEntry(-bet.stake, f"{game}:deposito"),),
            )
            # Dinero propio que vuelve: ni casino ni embargo (Biblia, «Embargo de la deuda»).
            balance = self._apply_in_transaction(
                connection,
                guild_id,
                bet.user_id,
                (LedgerEntry(bet.stake, f"{game}:devolucion"),),
            )
            self._close_predict_bet_in(connection, bet, "void", bet.stake, now)
            return replace(bet, status="void", payout=bet.stake, settled_at=now), balance

    @staticmethod
    def _open_predict_bet_in(
        connection: sqlite3.Connection, guild_id: int, bet_id: int
    ) -> PredictBet | None:
        row = connection.execute(
            """
            SELECT * FROM economy_predict_bets
            WHERE guild_id = ? AND id = ? AND status = 'open'
            """,
            (guild_id, bet_id),
        ).fetchone()
        return _predict_bet(row) if row is not None else None

    @staticmethod
    def _close_predict_bet_in(
        connection: sqlite3.Connection, bet: PredictBet, status: str, payout: int, now: float
    ) -> None:
        connection.execute(
            """
            UPDATE economy_predict_bets SET status = ?, payout = ?, settled_at = ?
            WHERE guild_id = ? AND id = ?
            """,
            (status, payout, now, bet.guild_id, bet.id),
        )

    async def open_predict_bets(self) -> list[PredictBet]:
        """Las predicciones sin resolver de todos los servidores, por fin del mercado."""
        return await self._run(self._open_predict_bets_sync)

    def _open_predict_bets_sync(self) -> list[PredictBet]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM economy_predict_bets WHERE status = 'open' ORDER BY ends, id"
            ).fetchall()
        finally:
            connection.close()
        return [_predict_bet(row) for row in rows]

    async def predict_event_bets(
        self, guild_id: int, user_id: int, event_id: str
    ) -> list[PredictBet]:
        """Las predicciones de alguien a un evento, una por pregunta."""
        return await self._run(self._predict_event_bets_sync, guild_id, user_id, event_id)

    def _predict_event_bets_sync(
        self, guild_id: int, user_id: int, event_id: str
    ) -> list[PredictBet]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM economy_predict_bets
                WHERE guild_id = ? AND user_id = ? AND event_id = ?
                ORDER BY id
                """,
                (guild_id, user_id, event_id),
            ).fetchall()
        finally:
            connection.close()
        return [_predict_bet(row) for row in rows]

    async def predict_bets(self, guild_id: int, user_id: int, *, limit: int) -> list[PredictBet]:
        """Las predicciones de alguien: abiertas primero y luego las últimas resueltas."""
        return await self._run(self._predict_bets_sync, guild_id, user_id, limit)

    def _predict_bets_sync(self, guild_id: int, user_id: int, limit: int) -> list[PredictBet]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM economy_predict_bets WHERE guild_id = ? AND user_id = ?
                ORDER BY status = 'open' DESC, COALESCE(settled_at, created_at) DESC, id DESC
                LIMIT ?
                """,
                (guild_id, user_id, limit),
            ).fetchall()
        finally:
            connection.close()
        return [_predict_bet(row) for row in rows]

    # -- Declaración semanal -------------------------------------------------------

    def _sync_declarations_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        *,
        current_week: str,
        week_end: Callable[[str], float],
        refund_for: Callable[[int, int, int], int],
        window_seconds: float,
        claim_weeks: int,
        deduction_for: Callable[[int, int, int], int] | None = None,
    ) -> None:
        """Crea las declaraciones de semanas viejas que falten y caduca las que sobran.

        Es la regla de antes de la 8.0.0, solo para las semanas anteriores al cambio en
        el servidor (`_regime_in`): se calcula al vuelo cuando el usuario juega o
        pregunta, y hay declaración en las semanas con casino o con donativos. Las de
        después las crea el cierre de los lunes (`close_irpf_week`). Lo que caduca vale
        para todas.

        Args:
            current_week: Lunes (ISO) de la semana en curso.
            week_end: Fin (epoch) de una semana dado su lunes ISO.
            refund_for: Recibe `(neto, retenido, renta de otros ingresos en los
                7 días previos al cierre)` y devuelve lo que sale a devolver
                del casino.
            window_seconds: Ventana con la que la regla vieja miraba las otras rentas.
            claim_weeks: Semanas que hay para presentar una declaración a devolver;
                después caduca y el dinero se queda en el Estado.
            deduction_for: Recibe `(donado, base de la semana, IRPF pagado y no
                devuelto)` y devuelve la deducción por donativos. `None`, sin
                deducción.
        """
        since = self._regime_in(connection, guild_id, current_week)
        # `date(day, '-6 days', 'weekday 1')` es el lunes de la semana de `day`.
        weeks = connection.execute(
            """
            SELECT week FROM (
                SELECT date(day, '-6 days', 'weekday 1') AS week FROM economy_gambling_days
                WHERE guild_id = :guild AND user_id = :user AND day < :current
                UNION
                SELECT week_start AS week FROM economy_donations
                WHERE guild_id = :guild AND user_id = :user AND week_start < :current
            )
            WHERE week NOT IN (
                SELECT week_start FROM economy_declarations
                WHERE guild_id = :guild AND user_id = :user
            )
            ORDER BY week
            """,
            {"guild": guild_id, "user": user_id, "current": min(current_week, since)},
        ).fetchall()
        for (week,) in weeks:
            end = week_end(week)
            start = end - 7 * 86_400
            net, withheld = connection.execute(
                """
                SELECT COALESCE(SUM(net), 0), COALESCE(SUM(withheld), 0)
                FROM economy_gambling_days
                WHERE guild_id = ? AND user_id = ? AND date(day, '-6 days', 'weekday 1') = ?
                """,
                (guild_id, user_id, week),
            ).fetchone()
            # La regla vieja mira las otras rentas a su escala: las filas de antes ya lo
            # están; las nuevas (si el bot nuevo cobró algo antes de su primera semana) se
            # pasan: las nóminas de 100 a 10 Y$ por euro y la renta de los pisos, a × 12 / 365.
            (others,) = connection.execute(
                """
                SELECT COALESCE(SUM(CASE
                    WHEN kind IS NULL THEN gross
                    WHEN kind IN (?, ?, ?, ?) THEN gross / 10
                    WHEN kind = ? THEN gross * 12 / 365
                    WHEN kind = ? THEN 0
                    ELSE gross END), 0)
                FROM economy_tax_records
                WHERE guild_id = ? AND user_id = ? AND created_at > ? AND created_at <= ?
                """,
                (
                    INCOME_WORK, INCOME_FOREIGN, INCOME_PENSION, INCOME_BUSINESS,
                    INCOME_PROPERTY, INCOME_IMPUTED,
                    guild_id, user_id, end - window_seconds, end,
                ),
            ).fetchone()  # fmt: skip
            refund = max(0, refund_for(int(net), int(withheld), int(others)))
            if deduction_for is not None:
                refund += self._donation_refund_in(
                    connection,
                    guild_id,
                    user_id,
                    week=week,
                    start=start,
                    end=end,
                    gambling_net=int(net),
                    gambling_withheld=int(withheld) - refund,
                    deduction_for=deduction_for,
                )
            connection.execute(
                """
                INSERT INTO economy_declarations (guild_id, user_id, week_start, refund, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (guild_id, user_id, week, refund, "pending" if refund else "none"),
            )
        # Una declaración se presenta en las `claim_weeks` semanas que empiezan con la de su
        # cierre; después prescribe. El cierre es `filed_week` (lo apunta el cierre del lunes,
        # aunque llegue tarde porque el bot estuvo apagado). Las de antes de la 8.0.0 no lo tienen:
        # cuentan desde la semana siguiente a la suya, y nunca desde antes del cambio, para
        # que desplegar no deje caducar de golpe las que seguían vivas con la regla vieja.
        cutoff = (date.fromisoformat(current_week) - timedelta(weeks=claim_weeks)).isoformat()
        connection.execute(
            """
            UPDATE economy_declarations SET status = 'expired'
            WHERE guild_id = ? AND user_id = ? AND status = 'pending'
              AND COALESCE(filed_week, MAX(date(week_start, '+7 days'), ?)) <= ?
            """,
            (guild_id, user_id, since, cutoff),
        )

    @staticmethod
    def _donation_refund_in(
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        *,
        week: str,
        start: float,
        end: float,
        gambling_net: int,
        gambling_withheld: int,
        deduction_for: Callable[[int, int, int], int],
    ) -> int:
        """Deducción por los donativos de `week`, limitada por el IRPF de esa semana.

        La cuota es lo retenido en la semana (ingresos más casino, ya sin lo que
        devuelve el casino) y la base, la renta sujeta de la semana.
        """
        (donated,) = connection.execute(
            """
            SELECT COALESCE(SUM(amount), 0) FROM economy_donations
            WHERE guild_id = ? AND user_id = ? AND week_start = ?
            """,
            (guild_id, user_id, week),
        ).fetchone()
        if not donated:
            return 0
        gross, income_withheld = connection.execute(
            """
            SELECT COALESCE(SUM(gross), 0), COALESCE(SUM(withheld), 0) FROM economy_tax_records
            WHERE guild_id = ? AND user_id = ? AND created_at >= ? AND created_at < ?
            """,
            (guild_id, user_id, start, end),
        ).fetchone()
        base = int(gross) + max(gambling_net, 0)
        tax_paid = int(income_withheld) + max(gambling_withheld, 0)
        return max(0, deduction_for(int(donated), base, tax_paid))

    @staticmethod
    def _pending_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int
    ) -> list[tuple[str, int]]:
        rows = connection.execute(
            """
            SELECT week_start, refund FROM economy_declarations
            WHERE guild_id = ? AND user_id = ? AND status = 'pending'
            ORDER BY week_start
            """,
            (guild_id, user_id),
        ).fetchall()
        return [(str(r["week_start"]), int(r["refund"])) for r in rows]

    async def pending_declarations(
        self, guild_id: int, user_id: int, **rules: object
    ) -> list[tuple[str, int]]:
        """Declaraciones pendientes `(lunes ISO, a devolver)`, de la más antigua a la última.

        `rules` son los argumentos con nombre de `_sync_declarations_in`.
        """
        return await self._run(self._pending_sync, guild_id, user_id, rules)

    def _pending_sync(
        self, guild_id: int, user_id: int, rules: dict[str, object]
    ) -> list[tuple[str, int]]:
        with self._transaction() as connection:
            self._sync_declarations_in(connection, guild_id, user_id, **rules)  # type: ignore[arg-type]
            return self._pending_in(connection, guild_id, user_id)

    async def claim_declarations(
        self, guild_id: int, user_id: int, *, now: float, **rules: object
    ) -> tuple[list[tuple[str, int]], int, int]:
        """Presenta todas las declaraciones pendientes y paga la devolución.

        El dinero sale de la cuenta del Estado, que es donde está lo retenido.

        Returns:
            `(semanas presentadas, total devuelto, saldo final)`.
        """
        return await self._run(self._claim_sync, guild_id, user_id, now, rules)

    def _claim_sync(
        self, guild_id: int, user_id: int, now: float, rules: dict[str, object]
    ) -> tuple[list[tuple[str, int]], int, int]:
        with self._transaction() as connection:
            self._sync_declarations_in(connection, guild_id, user_id, **rules)  # type: ignore[arg-type]
            # Lo que no pueda pagar el Estado (no crea dinero) sigue pendiente: se presentan
            # las semanas más viejas primero y de la que no llegue entera se baja lo pagado.
            available = self._state_balance_in(connection, guild_id)
            claimed: list[tuple[str, int]] = []
            for week, refund in self._pending_in(connection, guild_id, user_id):
                take = min(refund, available)
                if take <= 0:
                    break
                available -= take
                claimed.append((week, take))
                connection.execute(
                    """
                    UPDATE economy_declarations
                    SET status = CASE WHEN refund = ? THEN 'claimed' ELSE status END,
                        claimed_at = CASE WHEN refund = ? THEN ? ELSE claimed_at END,
                        refund = CASE WHEN refund = ? THEN refund ELSE refund - ? END
                    WHERE guild_id = ? AND user_id = ? AND week_start = ?
                    """,
                    (take, take, now, take, take, guild_id, user_id, week),
                )
            pending = claimed
            total = sum(take for _week, take in claimed)
            if total:
                self._apply_in_transaction(
                    connection,
                    guild_id,
                    STATE_ACCOUNT_ID,
                    (LedgerEntry(-total, "devolucion:renta"),),
                )
                balance = self._apply_in_transaction(
                    connection, guild_id, user_id, (LedgerEntry(total, "devolucion:renta"),)
                )
            else:
                balance = self._ensure_wallet(connection, guild_id, user_id)
            return pending, total, balance

    @staticmethod
    def _state_balance_in(connection: sqlite3.Connection, guild_id: int) -> int:
        row = connection.execute(
            "SELECT balance FROM economy_wallets WHERE guild_id = ? AND user_id = ?",
            (guild_id, STATE_ACCOUNT_ID),
        ).fetchone()
        return int(row["balance"]) if row else 0

    def _credit_state_in(
        self, connection: sqlite3.Connection, guild_id: int, amount: int, reason: str
    ) -> None:
        """Ingresa `amount` en la cuenta del Estado (se abre a 0 si no existía)."""
        connection.execute(
            """
            INSERT OR IGNORE INTO economy_wallets (guild_id, user_id, balance)
            VALUES (?, ?, 0)
            """,
            (guild_id, STATE_ACCOUNT_ID),
        )
        self._apply_in_transaction(
            connection, guild_id, STATE_ACCOUNT_ID, (LedgerEntry(amount, reason),)
        )

    async def casino_ledger(
        self, guild_id: int, user_id: int | None
    ) -> tuple[list[tuple[str, int, int]], int, int, int, float | None]:
        """Lo que dice el libro del casino desde el primer día (para `apuestas`).

        Solo lee. Mira los movimientos de los miembros (no los del Estado ni los
        de los botes) cuyo motivo acaba en `:apuesta`, `:premio` o `:bote`, y
        los del IRPF del juego y la renta.

        Args:
            user_id: Un miembro, o `None` para todo el servidor.

        Returns:
            `(filas, retenido, devuelto en el día, devuelto en la renta, primer
            movimiento)`, donde cada fila es `(motivo, movimientos, suma)`.
        """
        return await self._run(self._casino_ledger_sync, guild_id, user_id)

    def _casino_ledger_sync(
        self, guild_id: int, user_id: int | None
    ) -> tuple[list[tuple[str, int, int]], int, int, int, float | None]:
        # Los miembros tienen ids de Discord (positivos); el Estado es 0 y las
        # cuentas de la casa son negativas.
        where = "guild_id = ? AND user_id > 0"
        args: tuple[object, ...] = (guild_id,)
        if user_id is not None:
            where += " AND user_id = ?"
            args = (guild_id, user_id)
        connection = self._connect()
        try:
            rows = [
                (str(reason), int(count), int(total or 0))
                for reason, count, total in connection.execute(
                    f"""
                    SELECT reason, COUNT(*), SUM(delta) FROM economy_ledger
                    WHERE {where} AND (reason LIKE '%:apuesta' OR reason LIKE '%:premio'
                        OR reason LIKE '%:bote')
                    GROUP BY reason
                    """,
                    args,
                )
            ]
            sums = dict(
                connection.execute(
                    f"""
                    SELECT reason, SUM(delta) FROM economy_ledger
                    WHERE {where} AND reason IN
                        ('irpf:juego', 'devolucion:irpf:juego', 'devolucion:renta')
                    GROUP BY reason
                    """,
                    args,
                ).fetchall()
            )
            (first_at,) = connection.execute(
                f"SELECT MIN(created_at) FROM economy_ledger WHERE {where} "
                "AND reason LIKE '%:apuesta'",
                args,
            ).fetchone()
        finally:
            connection.close()
        return (
            rows,
            -int(sums.get("irpf:juego") or 0),
            int(sums.get("devolucion:irpf:juego") or 0),
            int(sums.get("devolucion:renta") or 0),
            float(first_at) if first_at is not None else None,
        )

    async def ledger_summary(
        self,
        guild_id: int,
        *,
        account: int | None,
        reasons: Sequence[str],
        since: float | None = None,
    ) -> dict[str, tuple[int, int]]:
        """`{motivo: (movimientos, suma)}` del libro, para las cuentas de `banco`. Solo lee.

        Args:
            account: Una cuenta (`SHOP_ACCOUNT_ID`…) o `None` para todos los miembros.
            reasons: Los motivos que interesan (los que no tengan movimientos no salen).
            since: Desde cuándo (epoch); `None`, desde el primer día.
        """
        return await self._run(self._ledger_summary_sync, guild_id, account, tuple(reasons), since)

    def _ledger_summary_sync(
        self,
        guild_id: int,
        account: int | None,
        reasons: tuple[str, ...],
        since: float | None,
    ) -> dict[str, tuple[int, int]]:
        if not reasons:
            return {}
        where = "guild_id = ? AND " + ("user_id = ?" if account is not None else "user_id > 0")
        args: list[object] = [guild_id] if account is None else [guild_id, account]
        if since is not None:
            where += " AND created_at >= ?"
            args.append(since)
        marks = ", ".join("?" for _ in reasons)
        connection = self._connect()
        try:
            rows = connection.execute(
                f"SELECT reason, COUNT(*), SUM(delta) FROM economy_ledger WHERE {where} "
                f"AND reason IN ({marks}) GROUP BY reason",  # solo marcadores `?`
                (*args, *reasons),
            ).fetchall()
        finally:
            connection.close()
        return {str(reason): (int(count), int(total or 0)) for reason, count, total in rows}

    async def system_balances(self, guild_id: int, accounts: Sequence[int]) -> dict[int, int]:
        """Saldo de unas cuentas que no son de miembros, sin abrir las que no existen (0)."""
        return await self._run(self._system_balances_sync, guild_id, tuple(accounts))

    def _system_balances_sync(self, guild_id: int, accounts: tuple[int, ...]) -> dict[int, int]:
        out = dict.fromkeys(accounts, 0)
        if not accounts:
            return out
        marks = ", ".join("?" for _ in accounts)
        connection = self._connect()
        try:
            rows = connection.execute(
                f"SELECT user_id, balance FROM economy_wallets WHERE guild_id = ? "
                f"AND user_id IN ({marks})",  # solo marcadores `?`
                (guild_id, *accounts),
            ).fetchall()
        finally:
            connection.close()
        out.update({int(user_id): int(balance) for user_id, balance in rows})
        return out

    async def member_balances(self, guild_id: int) -> dict[int, int]:
        """Saldo de cada miembro con monedero (sin el Estado ni las cuentas de la casa)."""
        return await self._run(self._member_balances_sync, guild_id)

    def _member_balances_sync(self, guild_id: int) -> dict[int, int]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT user_id, balance FROM economy_wallets WHERE guild_id = ? AND user_id > 0",
                (guild_id,),
            ).fetchall()
        finally:
            connection.close()
        return {int(user_id): int(balance) for user_id, balance in rows}

    async def tax_breakdown(
        self, guild_id: int, *, since: float | None = None
    ) -> dict[int, dict[str, int]]:
        """Todo lo que cada miembro ha pagado al Estado, impuesto a impuesto.

        Solo lee. Las claves son las de `bot.services.tax_report.TAX_KINDS`:
        IRPF del trabajo y de otros ingresos, del casino y del ahorro (intereses y
        plusvalías del Rastro), ITP del Rastro,
        Seguridad Social del trabajador (con la cuota de autónomos) y de la
        empresa, IGIC (las devoluciones restan), Patrimonio, gravamen de
        loterías, IAJ de las porras, multas, lo devuelto en la renta (positivo;
        resta) y lo pagado en Hong Kong (que no va al Estado).

        Args:
            since: Epoch desde el que contar, o `None` para todo.
        """
        return await self._run(self._tax_breakdown_sync, guild_id, since)

    def _tax_breakdown_sync(self, guild_id: int, since: float | None) -> dict[int, dict[str, int]]:
        # Una sola vista con (miembro, tipo, importe, momento). El IRPF de las
        # nóminas está en `economy_tax_records` y en `economy_payroll`: se
        # cuenta por la nómina y se resta del resto de rentas para no duplicarlo.
        parts = """
            SELECT user_id, 'irpf_trabajo' AS kind, irpf AS amount, created_at AS at
            FROM economy_payroll WHERE guild_id = :guild
            UNION ALL
            SELECT user_id, 'irpf_otros', -irpf, created_at
            FROM economy_payroll WHERE guild_id = :guild
            UNION ALL
            SELECT user_id,
                   CASE WHEN concept = 'pisos:renta' THEN 'irpf_alquiler' ELSE 'irpf_otros' END,
                   withheld, created_at
            FROM economy_tax_records WHERE guild_id = :guild
            UNION ALL
            SELECT user_id, 'irpf_casino', withheld, updated_at
            FROM economy_gambling_days WHERE guild_id = :guild
            UNION ALL
            SELECT user_id, 'irpf_ahorro', tax, created_at
            FROM economy_interest WHERE guild_id = :guild
            UNION ALL
            SELECT user_id, 'irpf_ahorro', charged, created_at
            FROM economy_interest_savings WHERE guild_id = :guild
            UNION ALL
            SELECT user_id, 'ss_trabajador', ss_worker, created_at
            FROM economy_payroll WHERE guild_id = :guild AND country = 'es'
            UNION ALL
            SELECT user_id, 'ss_empresa', ss_employer, created_at
            FROM economy_payroll WHERE guild_id = :guild AND country = 'es'
            UNION ALL
            SELECT user_id, 'extranjero', foreign_tax + ss_worker + ss_employer, created_at
            FROM economy_payroll WHERE guild_id = :guild AND country != 'es'
            UNION ALL
            SELECT user_id, 'igic', tax, created_at
            FROM economy_consumption_tax WHERE guild_id = :guild
            UNION ALL
            SELECT user_id, 'patrimonio', tax, created_at
            FROM economy_wealth_tax WHERE guild_id = :guild
            UNION ALL
            SELECT user_id, 'deudas', amount, created_at
            FROM economy_debt_payments WHERE guild_id = :guild
            UNION ALL
            SELECT user_id, 'irpf_renta', charged, created_at
            FROM economy_irpf_returns WHERE guild_id = :guild
            UNION ALL
            SELECT user_id, 'grandes_fortunas', tax, created_at
            FROM economy_solidarity_tax WHERE guild_id = :guild
            UNION ALL
            SELECT user_id, 'loteria', tax, created_at
            FROM economy_lottery_tax WHERE guild_id = :guild
            UNION ALL
            SELECT user_id, 'iaj', tax, created_at
            FROM economy_gaming_tax WHERE guild_id = :guild
            UNION ALL
            SELECT user_id, 'itp', tax, created_at
            FROM economy_market_tax WHERE guild_id = :guild AND kind = 'itp'
            UNION ALL
            SELECT user_id, 'irpf_ahorro', tax, created_at
            FROM economy_market_tax WHERE guild_id = :guild AND kind = 'plusvalia'
            UNION ALL
            SELECT user_id,
                   CASE kind WHEN 'itp' THEN 'itp_pisos' WHEN 'ibi' THEN 'ibi'
                        WHEN 'igic' THEN 'igic' WHEN 'igic_gastos' THEN 'igic'
                        WHEN 'ips' THEN 'igic' WHEN 'licencia' THEN 'tasas'
                        WHEN 'licencia_obra' THEN 'tasas' WHEN 'icio' THEN 'icio'
                        WHEN 'ajd' THEN 'itp_pisos'
                        ELSE 'irpf_ahorro' END,
                   tax, created_at
            FROM economy_housing_tax WHERE guild_id = :guild
            UNION ALL
            SELECT user_id,
                   CASE kind WHEN 'tasa' THEN 'tasas' WHEN 'retencion' THEN 'irpf_otros'
                        WHEN 'retencion_plantilla' THEN 'irpf_otros'
                        WHEN 'cotizacion' THEN 'ss_empresa' WHEN 'multa' THEN 'multas'
                        WHEN 'aiem' THEN 'aiem'
                        WHEN 'plusvalia' THEN 'irpf_ahorro' WHEN 'dividendo' THEN 'irpf_ahorro'
                        WHEN 'sociedades' THEN 'sociedades' ELSE 'igic' END,
                   tax, created_at
            FROM economy_business_tax WHERE guild_id = :guild
            UNION ALL
            SELECT user_id, 'multas', amount, created_at
            FROM economy_sanctions WHERE guild_id = :guild
            UNION ALL
            SELECT user_id, 'devuelto', delta, created_at
            FROM economy_ledger WHERE guild_id = :guild AND reason = 'devolucion:renta'
        """
        connection = self._connect()
        try:
            rows = connection.execute(
                f"""
                SELECT user_id, kind, SUM(amount) FROM ({parts})
                WHERE user_id > 0 AND (:since IS NULL OR at >= :since)
                GROUP BY user_id, kind
                """,
                {"guild": guild_id, "since": since},
            ).fetchall()
        finally:
            connection.close()
        out: dict[int, dict[str, int]] = {}
        for user_id, kind, total in rows:
            if total:
                out.setdefault(int(user_id), {})[str(kind)] = int(total)
        return out

    async def reason_totals(self, guild_id: int, reason: str) -> dict[int, int]:
        """Suma del libro por miembro para un motivo (p. ej. la renta de los `pisos`)."""
        return await self._run(self._reason_totals_sync, guild_id, reason)

    def _reason_totals_sync(self, guild_id: int, reason: str) -> dict[int, int]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT user_id, SUM(delta) FROM economy_ledger "
                "WHERE guild_id = ? AND user_id > 0 AND reason = ? GROUP BY user_id",
                (guild_id, reason),
            ).fetchall()
        finally:
            connection.close()
        return {int(user_id): int(total) for user_id, total in rows}

    async def housing_tax_breakdown(self, guild_id: int) -> dict[int, dict[str, int]]:
        """Todo lo que cada miembro ha pagado por sus `pisos`, desde siempre.

        Solo lee. Claves: `itp` (al comprar), `ibi`, `alquiler` (IRPF de las rentas) y
        `ganancia` (IRPF de lo ganado al vender). Es lo mismo que cuenta
        `tax_breakdown`, separado del resto para el bloque 🏠 de `hacienda`.
        """
        return await self._run(self._housing_tax_breakdown_sync, guild_id)

    def _housing_tax_breakdown_sync(self, guild_id: int) -> dict[int, dict[str, int]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT user_id,
                       CASE kind WHEN 'plusvalia' THEN 'ganancia' WHEN 'socimi' THEN 'ganancia'
                            ELSE kind END,
                       SUM(tax)
                FROM economy_housing_tax WHERE guild_id = :guild AND user_id > 0
                GROUP BY user_id, kind
                UNION ALL
                SELECT user_id, 'alquiler', SUM(withheld)
                FROM economy_tax_records
                WHERE guild_id = :guild AND user_id > 0 AND concept = :rent
                GROUP BY user_id
                """,
                {"guild": guild_id, "rent": HOUSING_RENT_CONCEPT},
            ).fetchall()
        finally:
            connection.close()
        out: dict[int, dict[str, int]] = {}
        for user_id, kind, total in rows:
            if total:
                out.setdefault(int(user_id), {})[str(kind)] = int(total)
        return out

    async def business_tax_breakdown(self, guild_id: int) -> dict[int, dict[str, int]]:
        """Todo lo que cada dueño ha pagado por sus negocios de `pala`, desde siempre.

        Solo lee. Claves de `tax_report.BUSINESS_TAXES`: `igic`, `aiem`, `cotizacion` y
        `retencion_plantilla` (la Seguridad Social y el IRPF de las nóminas de la plantilla),
        `irpf` (el suyo: a cuenta de las ventas, sueldo de administrador, dividendos y
        ganancias de un traspaso o de vender la sociedad), `sociedades`, `retencion` (la del
        casero del local), `tasa` y `multa`. Es lo mismo que cuenta `tax_breakdown`, separado
        para la pestaña 🏪 de `hacienda`.
        """
        return await self._run(self._business_tax_breakdown_sync, guild_id)

    def _business_tax_breakdown_sync(self, guild_id: int) -> dict[int, dict[str, int]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT user_id,
                       CASE WHEN kind IN ('dividendo', 'plusvalia') THEN 'irpf'
                            WHEN kind IN ('aiem', 'cotizacion', 'retencion_plantilla',
                                          'sociedades', 'retencion', 'tasa', 'multa') THEN kind
                            ELSE 'igic' END,
                       SUM(tax)
                FROM economy_business_tax WHERE guild_id = :guild AND user_id > 0
                GROUP BY 1, 2
                UNION ALL
                SELECT user_id, 'irpf', SUM(withheld)
                FROM economy_tax_records
                WHERE guild_id = :guild AND user_id > 0
                  AND (concept LIKE 'negocio:%' OR concept LIKE 'sociedad:%')
                GROUP BY user_id
                """,
                {"guild": guild_id},
            ).fetchall()
        finally:
            connection.close()
        out: dict[int, dict[str, int]] = {}
        for user_id, kind, total in rows:
            if total:
                amounts = out.setdefault(int(user_id), {})
                amounts[str(kind)] = amounts.get(str(kind), 0) + int(total)
        return out

    async def solidarity_summary(self, guild_id: int) -> tuple[int, int, int]:
        """`(declaraciones, contribuyentes, lo pagado)` de las Grandes Fortunas del servidor."""
        return await self._run(self._solidarity_summary_sync, guild_id)

    def _solidarity_summary_sync(self, guild_id: int) -> tuple[int, int, int]:
        connection = self._connect()
        try:
            filed, people, paid = connection.execute(
                """
                SELECT COUNT(*), COUNT(DISTINCT user_id), COALESCE(SUM(tax), 0)
                FROM economy_solidarity_tax WHERE guild_id = ?
                """,
                (guild_id,),
            ).fetchone()
        finally:
            connection.close()
        return int(filed), int(people), int(paid)

    async def treasury(self, guild_id: int, since: float, top: int) -> Treasury:
        """Saldo y recaudación de la cuenta del Estado del servidor."""
        return await self._run(self._treasury_sync, guild_id, since, top)

    def _treasury_sync(self, guild_id: int, since: float, top: int) -> Treasury:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT balance FROM economy_wallets WHERE guild_id = ? AND user_id = ?",
                (guild_id, STATE_ACCOUNT_ID),
            ).fetchone()
            # Retenciones de ingresos y del casino, Patrimonio, IGIC, gravamen de
            # loterías, Seguridad Social de las nóminas (la de la empresa se apunta a
            # quien cobra), multas, IRPF de los intereses e ITP y plusvalías del
            # Rastro, en una sola vista.
            taxes = """
                SELECT user_id, withheld, created_at AS at FROM economy_tax_records
                WHERE guild_id = :guild
                UNION ALL
                SELECT user_id, withheld, updated_at AS at FROM economy_gambling_days
                WHERE guild_id = :guild
                UNION ALL
                SELECT user_id, tax AS withheld, created_at AS at FROM economy_wealth_tax
                WHERE guild_id = :guild
                UNION ALL
                SELECT user_id, tax AS withheld, created_at AS at FROM economy_solidarity_tax
                WHERE guild_id = :guild
                UNION ALL
                SELECT user_id, tax AS withheld, created_at AS at FROM economy_consumption_tax
                WHERE guild_id = :guild
                UNION ALL
                SELECT user_id, tax AS withheld, created_at AS at FROM economy_lottery_tax
                WHERE guild_id = :guild
                UNION ALL
                SELECT user_id, tax AS withheld, created_at AS at FROM economy_gaming_tax
                WHERE guild_id = :guild
                UNION ALL
                SELECT user_id, ss_worker + ss_employer AS withheld, created_at AS at
                FROM economy_payroll WHERE guild_id = :guild AND country = 'es'
                UNION ALL
                SELECT user_id, amount AS withheld, created_at AS at FROM economy_sanctions
                WHERE guild_id = :guild
                UNION ALL
                SELECT user_id, tax AS withheld, created_at AS at FROM economy_interest
                WHERE guild_id = :guild
                UNION ALL
                SELECT user_id, charged AS withheld, created_at AS at
                FROM economy_interest_savings WHERE guild_id = :guild
                UNION ALL
                SELECT user_id, tax AS withheld, created_at AS at FROM economy_market_tax
                WHERE guild_id = :guild
                UNION ALL
                SELECT user_id, tax AS withheld, created_at AS at FROM economy_housing_tax
                WHERE guild_id = :guild
                UNION ALL
                SELECT user_id, amount AS withheld, created_at AS at FROM economy_debt_payments
                WHERE guild_id = :guild
                UNION ALL
                SELECT user_id, charged AS withheld, created_at AS at FROM economy_irpf_returns
                WHERE guild_id = :guild
            """
            total, recent = connection.execute(
                f"""
                SELECT COALESCE(SUM(withheld), 0),
                       COALESCE(SUM(CASE WHEN at >= :since THEN withheld END), 0)
                FROM ({taxes})
                """,
                {"guild": guild_id, "since": since},
            ).fetchone()
            contributors = connection.execute(
                f"""
                SELECT user_id, SUM(withheld) AS paid FROM ({taxes})
                GROUP BY user_id HAVING paid > 0
                ORDER BY paid DESC, user_id ASC LIMIT :top
                """,
                {"guild": guild_id, "top": top},
            ).fetchall()
            (debt,) = connection.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM economy_public_debt WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()
            return Treasury(
                balance=int(row["balance"]) if row else 0,
                collected_total=int(total),
                collected_since=int(recent),
                top_contributors=tuple((int(r["user_id"]), int(r["paid"])) for r in contributors),
                debt=int(debt),
            )
        finally:
            connection.close()

    # -- Impuesto sobre el Patrimonio -------------------------------------------------

    async def charge_wealth_tax(
        self,
        guild_id: int,
        *,
        week: str,
        now: float,
        tax_for: Callable[[int, int, int, int], WealthQuote],
        assets: Callable[[sqlite3.Connection], dict[int, int]] | None = None,
        goods: Callable[[sqlite3.Connection], dict[int, int]] | None = None,
        fiscal: FiscalWeek | None = None,
        irpf_of: Callable[[WeekIncome, int], tuple[int, int]] | None = None,
    ) -> WealthRun:
        """Cobra el Patrimonio de `week` a todos los miembros del servidor, una vez.

        Todo va en una transacción: o pagan todos o nadie, y la semana queda
        marcada. Lo cobrado va a la cuenta del Estado. Las cuentas especiales
        (Estado y ONGs, con `user_id` <= 0) no pagan. La base es el saldo más lo
        que devuelvan `assets` y `goods`; se paga con el saldo y lo que no llegue
        queda como deuda. Quien pasa del umbral de Grandes Fortunas lo declara en
        `economy_solidarity_tax`, pague o no.

        Args:
            week: Lunes ISO de la semana que se cierra.
            tax_for: Recibe `(patrimonio, parte que no da rentas, bases del IRPF de la
                semana, cuotas del IRPF de la semana)` y devuelve la cuenta
                (`bot.services.taxes.wealth_quote`).
            assets: Lee, en la misma transacción, el valor fiscal de lo que tiene
                cada miembro además del efectivo (los inmuebles de `pisos`).
            goods: Lee, en la misma transacción, lo que valen sus objetos de la tienda
                (`repositories.shop.wealth_goods_in`): no dan rentas.
            fiscal: La semana fiscal que se cierra, para el IRPF del límite conjunto.
            irpf_of: Recibe `(renta de la semana, intereses de la semana)` y devuelve
                `(suma de bases, suma de cuotas)` del IRPF, general y del ahorro. Si la
                declaración de la semana ya se cerró, se usa la suya.
        """
        return await self._run(
            self._charge_wealth_sync, guild_id, week, now, tax_for, assets, goods, fiscal, irpf_of
        )

    def _irpf_totals_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        fiscal: FiscalWeek,
        irpf_of: Callable[[WeekIncome, int], tuple[int, int]],
    ) -> tuple[int, int]:
        """`(bases, cuotas)` del IRPF de un miembro en una semana, para el límite conjunto."""
        row = connection.execute(
            """
            SELECT base + savings AS base, quota + savings_quota AS quota
            FROM economy_irpf_returns WHERE guild_id = ? AND user_id = ? AND week_start = ?
            """,
            (guild_id, user_id, fiscal.monday),
        ).fetchone()
        if row is not None:
            return int(row["base"]), int(row["quota"])
        income, _wages = self._week_income_in(connection, guild_id, user_id, fiscal)
        return irpf_of(income, self._week_savings_in(connection, guild_id, user_id, fiscal))

    @staticmethod
    def _week_savings_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, fiscal: FiscalWeek
    ) -> int:
        """Intereses brutos de un miembro en una semana (la base del ahorro)."""
        (gross,) = connection.execute(
            """
            SELECT COALESCE(SUM(gross), 0) FROM economy_interest
            WHERE guild_id = ? AND user_id = ? AND day >= ? AND day < ?
            """,
            (guild_id, user_id, fiscal.monday, fiscal.next_monday),
        ).fetchone()
        return int(gross)

    def _charge_wealth_sync(
        self,
        guild_id: int,
        week: str,
        now: float,
        tax_for: Callable[[int, int, int, int], WealthQuote],
        assets: Callable[[sqlite3.Connection], dict[int, int]] | None,
        goods: Callable[[sqlite3.Connection], dict[int, int]] | None = None,
        fiscal: FiscalWeek | None = None,
        irpf_of: Callable[[WeekIncome, int], tuple[int, int]] | None = None,
    ) -> WealthRun:
        with self._transaction() as connection:
            done = connection.execute(
                "SELECT 1 FROM economy_wealth_weeks WHERE guild_id = ? AND week_start = ?",
                (guild_id, week),
            ).fetchone()
            if done is not None:
                return WealthRun(done_before=True)
            (previous,) = connection.execute(
                "SELECT COUNT(*) FROM economy_wealth_weeks WHERE guild_id = ?", (guild_id,)
            ).fetchone()
            connection.execute(
                "INSERT INTO economy_wealth_weeks (guild_id, week_start, processed_at) "
                "VALUES (?, ?, ?)",
                (guild_id, week, now),
            )
            if not previous:
                return WealthRun(activation=True)
            wallets = connection.execute(
                """
                SELECT user_id, balance FROM economy_wallets
                WHERE guild_id = ? AND user_id > 0 AND balance > 0
                """,
                (guild_id,),
            ).fetchall()
            cash = {int(row["user_id"]): int(row["balance"]) for row in wallets}
            extra = assets(connection) if assets is not None else {}
            things = goods(connection) if goods is not None else {}
            charges = []
            members = set(cash) | {m for m, v in extra.items() if m > 0 and v > 0}
            members |= {m for m, v in things.items() if m > 0 and v > 0}
            for user_id in sorted(members):
                balance = cash.get(user_id, 0)
                owned = things.get(user_id, 0)
                # Las deudas de los pisos (hipotecas, banco) restan también del efectivo.
                worth = max(0, balance + extra.get(user_id, 0) + owned)
                base = quota = 0
                if fiscal is not None and irpf_of is not None:
                    base, quota = self._irpf_totals_in(
                        connection, guild_id, user_id, fiscal, irpf_of
                    )
                quote = tax_for(worth, owned, base, quota)
                if quote.subject:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO economy_solidarity_tax
                            (guild_id, user_id, week_start, base, tax, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (guild_id, user_id, week, worth, max(0, quote.solidarity), now),
                    )
                due = max(0, quote.due) + max(0, quote.solidarity)
                tax = min(balance, due)
                # Lo que no llega en efectivo se embarga de lo que cobre por sus pisos
                # (art. 169 de la Ley 58/2003): tenerlo todo en ladrillo no libra del impuesto.
                debt = due - tax
                if not due:
                    continue
                if tax:
                    self._apply_in_transaction(
                        connection, guild_id, user_id, (LedgerEntry(-tax, "patrimonio"),)
                    )
                    self._credit_state_in(connection, guild_id, tax, "patrimonio")
                    connection.execute(
                        """
                        INSERT INTO economy_wealth_tax
                            (guild_id, user_id, week_start, balance, tax, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (guild_id, user_id, week, worth, tax, now),
                    )
                if debt:
                    connection.execute(
                        """
                        INSERT INTO economy_tax_debts (guild_id, user_id, amount)
                        VALUES (?, ?, ?)
                        ON CONFLICT (guild_id, user_id)
                            DO UPDATE SET amount = amount + excluded.amount
                        """,
                        (guild_id, user_id, debt),
                    )
                charges.append(
                    WealthCharge(
                        user_id, worth, tax, debt, reduced=quote.reduced, goods=owned,
                        solidarity_subject=quote.subject, solidarity=quote.solidarity,
                    )
                )  # fmt: skip
            charges.sort(key=lambda c: (-(c.tax + c.debt), c.user_id))
            return WealthRun(charges=tuple(charges))

    # -- Cierre del IRPF de los lunes --------------------------------------------------

    async def close_irpf_week(
        self,
        guild_id: int,
        *,
        week: FiscalWeek,
        current_week: str,
        now: float,
        returns: Callable[..., WeekReturn],
        savings_tax: Callable[[int], int],
        deadlines: tuple[float, float],
    ) -> IrpfRun:
        """Cierra el IRPF de una semana: la declaración de cada miembro con renta, una vez.

        Todo en una transacción y por orden, para cada miembro:

        1. La declaración (`returns`, `bot.services.taxes.week_return`) con su renta de la
           semana, lo retenido, los donativos, lo pagado por la vivienda habitual y el
           alquiler, el impuesto pagado fuera y los intereses (para el nivel de renta).
        2. Los arrastres: lo usado de semanas anteriores se gasta (lo más viejo primero) y
           lo que no cupo (intereses y reparaciones de cada inmueble, base negativa) pasa
           a las cuatro siguientes.
        3. Si sale a pagar, queda pendiente (`PAYMENT_PENDING`): su dueño la paga al
           abrirla, entera o fraccionada (`pay_irpf_return`), y si no ha elegido al
           acabar el plazo se cobra entera (`settle_irpf_payments`).
        4. Si sale a devolver, primero paga lo que debiera (compensación de oficio, art.
           73 de la Ley 58/2003) y el resto queda pendiente para presentarlo con la Renta
           (`economy_declarations`), donde caduca a las cuatro semanas.
        5. Lo que debiera de antes (IRPF o Patrimonio) se cobra del monedero: el dinero es
           lo primero que se embarga (art. 169.2.a de la Ley 58/2003).

        Las semanas de antes de la 8.0.0 en el servidor no se cierran así (`legacy`).

        Args:
            week: La semana que se cierra (ya acabada).
            current_week: Lunes ISO de la semana en curso, por si es la primera vez que el
                servidor pasa por aquí (`_regime_in`).
            returns: Calcula la declaración (`taxes.week_return`, con sus argumentos).
            savings_tax: Cuota del ahorro de unos intereses semanales, para el límite
                conjunto del Patrimonio.
            deadlines: `(final del plazo para pagar, cobro del segundo plazo)` en epoch,
                para la que salga a pagar (`economy.installment_deadlines`).
        """
        return await self._run(
            self._close_irpf_sync,
            guild_id,
            week,
            current_week,
            now,
            returns,
            savings_tax,
            deadlines,
        )

    def _close_irpf_sync(
        self,
        guild_id: int,
        week: FiscalWeek,
        current_week: str,
        now: float,
        returns: Callable[..., WeekReturn],
        savings_tax: Callable[[int], int],
        deadlines: tuple[float, float],
    ) -> IrpfRun:
        with self._transaction() as connection:
            since = self._regime_in(connection, guild_id, current_week)
            if week.monday < since:
                return IrpfRun(legacy=True)
            done = connection.execute(
                "SELECT 1 FROM economy_irpf_weeks WHERE guild_id = ? AND week_start = ?",
                (guild_id, week.monday),
            ).fetchone()
            if done is not None:
                return IrpfRun(done_before=True)
            connection.execute(
                "INSERT INTO economy_irpf_weeks (guild_id, week_start, processed_at) "
                "VALUES (?, ?, ?)",
                (guild_id, week.monday, now),
            )
            window = {
                "guild": guild_id,
                "start": week.start,
                "end": week.end,
                "monday": week.monday,
                "next": week.next_monday,
            }
            members = connection.execute(
                """
                SELECT user_id FROM economy_tax_records
                WHERE guild_id = :guild AND created_at >= :start AND created_at < :end
                UNION
                SELECT user_id FROM economy_gambling_days
                WHERE guild_id = :guild AND day >= :monday AND day < :next
                UNION
                SELECT user_id FROM economy_donations
                WHERE guild_id = :guild AND week_start = :monday
                UNION
                SELECT user_id FROM economy_irpf_extras
                WHERE guild_id = :guild AND created_at >= :start AND created_at < :end
                """,
                window,
            ).fetchall()
            filed = [
                self._file_return_in(
                    connection,
                    guild_id,
                    int(row[0]),
                    week,
                    now,
                    returns,
                    savings_tax,
                    filed_week=current_week,
                    deadlines=deadlines,
                )  # fmt: skip
                for row in members
                if int(row[0]) > 0
            ]
            # Quien debe a Hacienda y no ha tenido renta esta semana también paga del
            # monedero (los que declaran ya lo hicieron en `_file_return_in`).
            declared = {r.user_id for r in filed}
            debtors = connection.execute(
                "SELECT user_id FROM economy_tax_debts WHERE guild_id = ? AND amount > 0",
                (guild_id,),
            ).fetchall()
            for (debtor,) in debtors:
                if int(debtor) > 0 and int(debtor) not in declared:
                    self._collect_debt_in(connection, guild_id, int(debtor), now)
            oldest = (date.fromisoformat(week.monday) - timedelta(days=7 * CARRY_WEEKS)).isoformat()
            connection.execute(
                "DELETE FROM economy_irpf_carry WHERE guild_id = ? AND week_start < ?",
                (guild_id, oldest),
            )
            filed.sort(key=lambda r: (-r.result, r.user_id))
            return IrpfRun(returns=tuple(filed))

    def _file_return_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        week: FiscalWeek,
        now: float,
        returns: Callable[..., WeekReturn],
        savings_tax: Callable[[int], int],
        *,
        filed_week: str,
        deadlines: tuple[float, float],
    ) -> IrpfReturn:
        """La declaración de un miembro (ver `close_irpf_week`), dentro de la transacción.

        Args:
            filed_week: Lunes ISO de la semana en que se cierra (desde ahí cuenta el plazo
                para presentar lo que salga a devolver).
            deadlines: `(final del plazo para pagar, cobro del segundo plazo)`.
        """
        income, _wages = self._week_income_in(connection, guild_id, user_id, week)
        withheld, credit, foreign = connection.execute(
            """
            SELECT COALESCE(SUM(withheld), 0), COALESCE(SUM(credit), 0),
                   COALESCE(SUM(CASE WHEN kind = ? THEN gross ELSE 0 END), 0)
            FROM economy_tax_records
            WHERE guild_id = ? AND user_id = ? AND created_at >= ? AND created_at < ?
            """,
            (INCOME_FOREIGN, guild_id, user_id, week.start, week.end),
        ).fetchone()
        (gambling_withheld,) = connection.execute(
            """
            SELECT COALESCE(SUM(withheld), 0) FROM economy_gambling_days
            WHERE guild_id = ? AND user_id = ? AND day >= ? AND day < ?
            """,
            (guild_id, user_id, week.monday, week.next_monday),
        ).fetchone()
        (donated,) = connection.execute(
            """
            SELECT COALESCE(SUM(amount), 0) FROM economy_donations
            WHERE guild_id = ? AND user_id = ? AND week_start = ?
            """,
            (guild_id, user_id, week.monday),
        ).fetchone()
        extras = {
            str(kind): int(amount)
            for kind, amount in connection.execute(
                """
                SELECT kind, SUM(amount) FROM economy_irpf_extras
                WHERE guild_id = ? AND user_id = ? AND created_at >= ? AND created_at < ?
                GROUP BY kind
                """,
                (guild_id, user_id, week.start, week.end),
            ).fetchall()
        }
        savings = self._week_savings_in(connection, guild_id, user_id, week)
        arguments = {
            "withheld": int(withheld) + int(gambling_withheld),
            "donated": int(donated),
            "home_invested": extras.get(EXTRA_HOME, 0),
            "foreign_credit": int(credit),
            "foreign_income": int(foreign),
            "savings": savings,
            "rent_paid": extras.get(EXTRA_RENT, 0),
        }
        filed = returns(income, **arguments)
        # Lo retenido por fuente, para explicar la declaración (`/renta`, modo detallado).
        by_source = {
            str(kind or "antes"): int(amount)
            for kind, amount in connection.execute(
                """
                SELECT kind, SUM(withheld) FROM economy_tax_records
                WHERE guild_id = ? AND user_id = ? AND created_at >= ? AND created_at < ?
                GROUP BY kind
                """,
                (guild_id, user_id, week.start, week.end),
            ).fetchall()
            if amount
        }
        if gambling_withheld:
            by_source["juego"] = int(gambling_withheld)
        inputs = json.dumps(
            {"income": income.to_dict(), "arguments": arguments, "withheld_by": by_source},
            ensure_ascii=False,
        )
        self._apply_carries_in(connection, guild_id, user_id, week.monday, filed.tax)
        row = connection.execute(
            "SELECT amount FROM economy_tax_debts WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        owed = int(row["amount"]) if row else 0
        result = filed.result
        refund = compensated = 0
        # La que sale a pagar no se cobra ahora: la paga su dueño al abrirla (entera o en
        # dos plazos) o se cobra entera al acabar el plazo (`_settle_irpf_payments_sync`).
        payment, pay_due, second_due = "", 0.0, 0.0
        if result > 0:
            payment, (pay_due, second_due) = PAYMENT_PENDING, deadlines
        elif result < 0:
            compensated = min(-result, owed)
            refund = -result - compensated
            owed -= compensated
        # Lo compensado no se apunta en `economy_debt_payments`: ese dinero ya estaba en el
        # Estado (es lo retenido de más) y Hacienda ya lo contó al retenerlo. Queda en
        # `economy_irpf_returns.compensated`.
        if row is not None:
            connection.execute(
                "UPDATE economy_tax_debts SET amount = ? WHERE guild_id = ? AND user_id = ?",
                (owed, guild_id, user_id),
            )
        # Lo que debía de antes se cobra del monedero.
        collected = self._collect_debt_in(connection, guild_id, user_id, now)
        if refund:
            connection.execute(
                """
                INSERT OR IGNORE INTO economy_declarations
                    (guild_id, user_id, week_start, refund, status, filed_week)
                VALUES (?, ?, ?, ?, 'pending', ?)
                """,
                (guild_id, user_id, week.monday, refund, filed_week),
            )
        connection.execute(
            """
            INSERT INTO economy_irpf_returns
                (guild_id, user_id, week_start, base, quota, deductions, withheld, result,
                 refund, compensated, collected, rate, savings, savings_quota, inputs,
                 created_at, payment, pay_due, second_due)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id, user_id, week.monday, filed.tax.base, filed.tax.quota,
                filed.deductions, filed.withheld, result, refund, compensated, collected,
                filed.tax.rate, savings, savings_tax(savings), inputs, now, payment,
                pay_due, second_due,
            ),
        )  # fmt: skip
        return IrpfReturn(
            user_id=user_id,
            base=filed.tax.base,
            quota=filed.tax.quota,
            deductions=filed.deductions,
            withheld=filed.withheld,
            result=result,
            refund=refund,
            compensated=compensated,
            collected=collected,
            rate=filed.tax.rate,
            payment=payment,
            pay_due=pay_due,
            second_due=second_due,
        )

    def _collect_debt_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        now: float,
        concept: str = "embargo:deuda",
    ) -> int:
        """Cobra del monedero lo que un miembro debe a Hacienda, hasta donde llegue.

        El dinero es lo primero que se embarga (art. 169.2.a de la Ley 58/2003). Va al
        Estado y queda en `economy_debt_payments`.

        Args:
            concept: Motivo del libro (`deuda:pago` si la liquida él mismo).

        Returns:
            Lo cobrado.
        """
        row = connection.execute(
            "SELECT amount FROM economy_tax_debts WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        owed = int(row["amount"]) if row else 0
        paid = min(owed, self._ensure_wallet(connection, guild_id, user_id)) if owed else 0
        if paid <= 0:
            return 0
        self._apply_in_transaction(connection, guild_id, user_id, (LedgerEntry(-paid, concept),))
        self._credit_state_in(connection, guild_id, paid, concept)
        connection.execute(
            "UPDATE economy_tax_debts SET amount = amount - ? WHERE guild_id = ? AND user_id = ?",
            (paid, guild_id, user_id),
        )
        connection.execute(
            "INSERT INTO economy_debt_payments (guild_id, user_id, amount, created_at) "
            "VALUES (?, ?, ?, ?)",
            (guild_id, user_id, paid, now),
        )
        return paid

    # -- Pago de la Renta a pagar (8.7.0) ---------------------------------------------

    def _charge_installment_in(
        self, connection: sqlite3.Connection, guild_id: int, user_id: int, amount: int
    ) -> tuple[int, int, int]:
        """Cobra un plazo de la Renta del monedero; lo que no llegue pasa a deuda.

        Lo cobrado va al Estado (`irpf:declaracion`); la deuda, sin recargo, a
        `economy_tax_debts`, donde la cobran el embargo y el barrido de los lunes.

        Returns:
            `(cobrado, deuda, saldo_final)`.
        """
        balance = self._ensure_wallet(connection, guild_id, user_id)
        charged = min(balance, amount)
        if charged:
            balance = self._apply_in_transaction(
                connection, guild_id, user_id, (LedgerEntry(-charged, "irpf:declaracion"),)
            )
            self._credit_state_in(connection, guild_id, charged, "irpf:declaracion")
        debt = amount - charged
        if debt:
            connection.execute(
                """
                INSERT INTO economy_tax_debts (guild_id, user_id, amount) VALUES (?, ?, ?)
                ON CONFLICT (guild_id, user_id) DO UPDATE SET amount = amount + excluded.amount
                """,
                (guild_id, user_id, debt),
            )
        return charged, debt, balance

    def _pay_return_in(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        split: bool,
        automatic: bool,
    ) -> IrpfPayment:
        """Paga una declaración pendiente: entera, o el 60 % y deja el 40 % para el jueves."""
        guild_id, user_id, week = int(row["guild_id"]), int(row["user_id"]), str(row["week_start"])
        result = int(row["result"])
        second = second_installment(result) if split else 0
        due = result - second
        charged, debt, balance = self._charge_installment_in(connection, guild_id, user_id, due)
        connection.execute(
            """
            UPDATE economy_irpf_returns
            SET payment = ?, charged = charged + ?, debt = debt + ?, second = ?
            WHERE guild_id = ? AND user_id = ? AND week_start = ?
            """,
            (
                PAYMENT_SPLIT if split else PAYMENT_PAID, charged, debt, second,
                guild_id, user_id, week,
            ),
        )  # fmt: skip
        return IrpfPayment(
            user_id=user_id,
            week=week,
            due=due,
            charged=charged,
            debt=debt,
            second=second,
            second_due=float(row["second_due"]) if split else 0.0,
            balance=balance,
            automatic=automatic,
        )

    async def pay_irpf_return(
        self, guild_id: int, user_id: int, week: str, *, split: bool
    ) -> IrpfPayment | None:
        """Paga la declaración pendiente de una semana: entera o fraccionada.

        Tratamiento fiscal: el ingreso de la autoliquidación del IRPF (art. 97 LIRPF), al
        Estado. Fraccionada, el 60 % ahora y el 40 % lo cobra `settle_irpf_payments` al
        final del jueves, sin intereses ni recargo (art. 62.2 del Reglamento del IRPF).
        Lo que no llegue del monedero queda como deuda con Hacienda.

        Returns:
            Lo pagado, o `None` si ya no estaba pendiente (pagada o cobrada sola).
        """
        return await self._run(self._pay_irpf_return_sync, guild_id, user_id, week, split)

    def _pay_irpf_return_sync(
        self, guild_id: int, user_id: int, week: str, split: bool
    ) -> IrpfPayment | None:
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM economy_irpf_returns
                WHERE guild_id = ? AND user_id = ? AND week_start = ? AND payment = ?
                """,
                (guild_id, user_id, week, PAYMENT_PENDING),
            ).fetchone()
            if row is None:
                return None
            return self._pay_return_in(connection, row, split=split, automatic=False)

    async def settle_irpf_payments(self, guild_id: int, now: float) -> tuple[IrpfPayment, ...]:
        """Cobra los plazos de la Renta que han vencido en un servidor.

        Las declaraciones que nadie ha pagado al acabar su plazo se cobran enteras, como
        una domiciliación; las fraccionadas cobran su 40 % al final del jueves. En los
        dos casos lo que no llegue queda como deuda con Hacienda, sin recargo. Es
        idempotente: cada plazo se cobra una vez.

        Returns:
            Un pago por plazo cobrado ahora.
        """
        return await self._run(self._settle_irpf_payments_sync, guild_id, now)

    def _settle_irpf_payments_sync(self, guild_id: int, now: float) -> tuple[IrpfPayment, ...]:
        with self._transaction() as connection:
            payments = [
                self._pay_return_in(connection, row, split=False, automatic=True)
                for row in connection.execute(
                    """
                    SELECT * FROM economy_irpf_returns
                    WHERE guild_id = ? AND payment = ? AND pay_due <= ?
                    ORDER BY week_start, user_id
                    """,
                    (guild_id, PAYMENT_PENDING, now),
                ).fetchall()
            ]
            due = connection.execute(
                """
                SELECT * FROM economy_irpf_returns
                WHERE guild_id = ? AND payment = ? AND second_due <= ?
                ORDER BY week_start, user_id
                """,
                (guild_id, PAYMENT_SPLIT, now),
            ).fetchall()
            for row in due:
                user_id, second = int(row["user_id"]), int(row["second"])
                charged, debt, balance = self._charge_installment_in(
                    connection, guild_id, user_id, second
                )
                connection.execute(
                    """
                    UPDATE economy_irpf_returns
                    SET payment = ?, charged = charged + ?, debt = debt + ?, second_paid = ?,
                        second_at = ?
                    WHERE guild_id = ? AND user_id = ? AND week_start = ?
                    """,
                    (
                        PAYMENT_PAID, charged, debt, charged, now, guild_id, user_id,
                        row["week_start"],
                    ),
                )  # fmt: skip
                payments.append(
                    IrpfPayment(
                        user_id=user_id,
                        week=str(row["week_start"]),
                        due=second,
                        charged=charged,
                        debt=debt,
                        second=0,
                        second_due=0.0,
                        balance=balance,
                        automatic=True,
                    )
                )
            return tuple(payments)

    async def pay_tax_debt(self, guild_id: int, user_id: int, now: float) -> tuple[int, int, int]:
        """Liquida la deuda con Hacienda del monedero, hasta donde llegue (botón de la Renta).

        Tratamiento fiscal: el pago voluntario de una deuda tributaria (art. 62 de la Ley
        58/2003), al Estado. Sin recargo, como la deuda.

        Returns:
            `(pagado, deuda que queda, saldo_final)`.
        """
        return await self._run(self._pay_tax_debt_sync, guild_id, user_id, now)

    def _pay_tax_debt_sync(self, guild_id: int, user_id: int, now: float) -> tuple[int, int, int]:
        with self._transaction() as connection:
            paid = self._collect_debt_in(connection, guild_id, user_id, now, "deuda:pago")
            row = connection.execute(
                "SELECT amount FROM economy_tax_debts WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            left = int(row["amount"]) if row else 0
            return paid, left, self._ensure_wallet(connection, guild_id, user_id)

    def _garnish_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        received: int,
        now: float,
        *,
        concept: str = "embargo:deuda",
    ) -> int:
        """Embarga el 30 % de lo que acaba de cobrar quien debe a Hacienda (`taxes.garnish`).

        Va en la misma transacción que el cobro, después de sus impuestos (`received` es
        lo que le ha llegado limpio), y nunca pasa de la deuda ni del saldo. Va al Estado
        y queda en `economy_debt_payments`, de donde sale el aviso (`irpf_notices`). Lo
        llaman todos los cobros salvo los exentos de embargo (ver `taxes.GARNISH_SHARE`).

        Returns:
            Lo embargado (0 si no debe nada).
        """
        if user_id <= 0 or received <= 0:
            return 0
        row = connection.execute(
            "SELECT amount FROM economy_tax_debts WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        if row is None or int(row["amount"]) <= 0:
            return 0
        most = min(garnish(received), self._ensure_wallet(connection, guild_id, user_id))
        return self._collect_tax_debt_in(connection, guild_id, user_id, most, now, concept)

    async def irpf_notices(
        self, guild_id: int, user_id: int, since: float, until: float
    ) -> tuple[int, int, int, int]:
        """Lo que ha cobrado Hacienda sola entre `since` y `until`, para avisar (`renta.hint`).

        Returns:
            `(embargado, segundo plazo cobrado, segundo plazo que quedó a deber, deuda que
            queda)`.
        """
        return await self._run(self._irpf_notices_sync, guild_id, user_id, since, until)

    def _irpf_notices_sync(
        self, guild_id: int, user_id: int, since: float, until: float
    ) -> tuple[int, int, int, int]:
        connection = self._connect()
        try:
            (embargoed,) = connection.execute(
                """
                SELECT COALESCE(SUM(amount), 0) FROM economy_debt_payments
                WHERE guild_id = ? AND user_id = ? AND created_at > ? AND created_at <= ?
                """,
                (guild_id, user_id, since, until),
            ).fetchone()
            paid, unpaid = connection.execute(
                """
                SELECT COALESCE(SUM(second_paid), 0), COALESCE(SUM(second - second_paid), 0)
                FROM economy_irpf_returns
                WHERE guild_id = ? AND user_id = ? AND second_at > ? AND second_at <= ?
                """,
                (guild_id, user_id, since, until),
            ).fetchone()
            row = connection.execute(
                "SELECT amount FROM economy_tax_debts WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            return int(embargoed), int(paid), int(unpaid), int(row["amount"]) if row else 0
        finally:
            connection.close()

    @staticmethod
    def _apply_carries_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, week: str, tax: WeekTax
    ) -> None:
        """Gasta lo arrastrado que ha usado la semana y apunta lo que pasa a las siguientes.

        `tax` es la `WeekTax` de la declaración: sus `settlements` (por inmueble, lo usado
        y lo que sobra) y su base negativa (la usada y la nueva).
        """
        oldest = (date.fromisoformat(week) - timedelta(days=7 * CARRY_WEEKS)).isoformat()

        def spend(kind: str, ref: str, amount: int) -> None:
            rows = connection.execute(
                """
                SELECT week_start, amount FROM economy_irpf_carry
                WHERE guild_id = ? AND user_id = ? AND kind = ? AND ref = ?
                  AND week_start >= ? AND week_start < ? AND amount > 0
                ORDER BY week_start
                """,
                (guild_id, user_id, kind, ref, oldest, week),
            ).fetchall()
            for row in rows:
                if amount <= 0:
                    break
                take = min(amount, int(row["amount"]))
                connection.execute(
                    """
                    UPDATE economy_irpf_carry SET amount = amount - ?
                    WHERE guild_id = ? AND user_id = ? AND kind = ? AND ref = ? AND week_start = ?
                    """,
                    (take, guild_id, user_id, kind, ref, row["week_start"]),
                )
                amount -= take

        def keep(kind: str, ref: str, amount: int) -> None:
            if amount <= 0:
                return
            connection.execute(
                """
                INSERT INTO economy_irpf_carry (guild_id, user_id, kind, ref, week_start, amount)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (guild_id, user_id, kind, ref, week_start)
                    DO UPDATE SET amount = amount + excluded.amount
                """,
                (guild_id, user_id, kind, ref, week, amount),
            )

        for ref, used, left in tax.settlements:
            spend(CARRY_FINANCE, ref, used)
            keep(CARRY_FINANCE, ref, left)
        spend(CARRY_BASE, "", tax.negative_used)
        keep(CARRY_BASE, "", tax.negative)

    async def irpf_statement(
        self, guild_id: int, user_id: int, week: str | None = None
    ) -> tuple[str, IrpfReturn, dict[str, object], bool] | None:
        """Una declaración cerrada: `(lunes ISO, declaración, entradas, abierta)`.

        Sin `week`, la última. Las entradas son lo que se le pasó a la declaración (la
        renta por clases, lo retenido por fuente…), para rehacerla y explicarla.
        """
        return await self._run(self._irpf_statement_sync, guild_id, user_id, week)

    def _irpf_statement_sync(
        self, guild_id: int, user_id: int, week: str | None
    ) -> tuple[str, IrpfReturn, dict[str, object], bool] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT * FROM economy_irpf_returns
                WHERE guild_id = ? AND user_id = ? AND (? IS NULL OR week_start = ?)
                ORDER BY week_start DESC LIMIT 1
                """,
                (guild_id, user_id, week, week),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        inputs = json.loads(str(row["inputs"]) or "{}")
        return str(row["week_start"]), _irpf_return_of(row), inputs, bool(row["opened"])

    async def open_irpf_return(self, guild_id: int, user_id: int, week: str) -> None:
        """Marca como abierta una declaración (ya se ha visto su resultado)."""
        await self._run(self._open_irpf_return_sync, guild_id, user_id, week)

    def _open_irpf_return_sync(self, guild_id: int, user_id: int, week: str) -> None:
        with self._transaction() as connection:
            connection.execute(
                "UPDATE economy_irpf_returns SET opened = 1, notified = 1 "
                "WHERE guild_id = ? AND user_id = ? AND week_start = ?",
                (guild_id, user_id, week),
            )

    async def take_irpf_notices(self, guild_id: int, user_id: int) -> list[str]:
        """Semanas (lunes ISO) con declaración cerrada de las que no se ha avisado al miembro.

        Quedan como avisadas: el aviso sale una vez (`renta.hint`) y no dice el resultado,
        que se ve al abrirla con `renta`.
        """
        return await self._run(self._take_irpf_notices_sync, guild_id, user_id)

    def _take_irpf_notices_sync(self, guild_id: int, user_id: int) -> list[str]:
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT week_start FROM economy_irpf_returns
                WHERE guild_id = ? AND user_id = ? AND notified = 0 AND opened = 0
                ORDER BY week_start
                """,
                (guild_id, user_id),
            ).fetchall()
            if rows:
                connection.execute(
                    "UPDATE economy_irpf_returns SET notified = 1 "
                    "WHERE guild_id = ? AND user_id = ? AND notified = 0",
                    (guild_id, user_id),
                )
            return [str(row["week_start"]) for row in rows]

    async def irpf_weeks_closed(self, guild_id: int) -> tuple[str | None, str | None]:
        """`(primera semana de la 8.0.0, última semana cerrada)` del servidor, en lunes ISO."""
        return await self._run(self._irpf_weeks_closed_sync, guild_id)

    def _irpf_weeks_closed_sync(self, guild_id: int) -> tuple[str | None, str | None]:
        connection = self._connect()
        try:
            regime = connection.execute(
                "SELECT since_week FROM economy_irpf_regime WHERE guild_id = ?", (guild_id,)
            ).fetchone()
            (last,) = connection.execute(
                "SELECT MAX(week_start) FROM economy_irpf_weeks WHERE guild_id = ?", (guild_id,)
            ).fetchone()
        finally:
            connection.close()
        return (str(regime[0]) if regime else None, str(last) if last else None)

    async def irpf_regime(self, guild_id: int, current_week: str) -> str:
        """Primera semana con las reglas de la 8.0.0 (la fija si aún no lo está)."""
        return await self._run(self._irpf_regime_sync, guild_id, current_week)

    def _irpf_regime_sync(self, guild_id: int, current_week: str) -> str:
        with self._transaction() as connection:
            return self._regime_in(connection, guild_id, current_week)

    # -- Intereses de la cuenta ---------------------------------------------------------

    @staticmethod
    def _day_rows_in(
        connection: sqlite3.Connection,
        guild_id: int,
        start: float,
        end: float,
        user_id: int | None = None,
    ) -> tuple[dict[int, int], dict[int, list[LedgerRow]]]:
        """Saldo al empezar el día y movimientos del día, por miembro.

        Todo sale del libro, no de fotos guardadas: no hace falta una tarea a
        medianoche y, si el bot estuvo caído, el saldo medio sigue siendo exacto.
        El saldo de apertura es el saldo de ahora menos lo que se ha movido desde
        `start`; un monedero que aún no existía empieza en 0.

        Args:
            user_id: Solo ese miembro; `None` para todos los del servidor.
        """
        from bot.services.interest import LedgerRow

        member = "AND user_id = :user" if user_id is not None else "AND user_id > 0"
        params = {"guild": guild_id, "start": start, "end": end, "user": user_id}
        balances = {
            int(row["user_id"]): int(row["balance"])
            for row in connection.execute(
                f"SELECT user_id, balance FROM economy_wallets WHERE guild_id = :guild {member}",
                params,
            )
        }
        moved = {
            int(row["user_id"]): int(row["moved"])
            for row in connection.execute(
                f"""
                SELECT user_id, SUM(delta) AS moved FROM economy_ledger
                WHERE guild_id = :guild AND created_at >= :start {member}
                GROUP BY user_id
                """,
                params,
            )
        }
        openings = {uid: balance - moved.get(uid, 0) for uid, balance in balances.items()}
        rows: dict[int, list[LedgerRow]] = {}
        for row in connection.execute(
            f"""
            SELECT user_id, created_at, delta, balance_after, reason FROM economy_ledger
            WHERE guild_id = :guild AND created_at >= :start AND created_at < :end {member}
            ORDER BY id
            """,
            params,
        ):
            rows.setdefault(int(row["user_id"]), []).append(
                LedgerRow(
                    float(row["created_at"]),
                    int(row["delta"]),
                    int(row["balance_after"]),
                    str(row["reason"]),
                )
            )
        return openings, rows

    async def interest_day_rows(
        self, guild_id: int, user_id: int, start: float
    ) -> tuple[int, list[LedgerRow]]:
        """Saldo de un miembro al empezar el día `start` y sus movimientos desde entonces."""
        return await self._run(self._interest_day_rows_sync, guild_id, user_id, start)

    def _interest_day_rows_sync(
        self, guild_id: int, user_id: int, start: float
    ) -> tuple[int, list[LedgerRow]]:
        connection = self._connect()
        try:
            openings, rows = self._day_rows_in(
                connection, guild_id, start, float("inf"), user_id=user_id
            )
            return openings.get(user_id, 0), rows.get(user_id, [])
        finally:
            connection.close()

    async def last_interest_day(self, guild_id: int) -> str | None:
        """Último día (ISO) cuyos intereses ya se pagaron en el servidor."""
        return await self._run(self._last_interest_day_sync, guild_id)

    def _last_interest_day_sync(self, guild_id: int) -> str | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT MAX(day) AS day FROM economy_interest_days WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()
            return str(row["day"]) if row and row["day"] else None
        finally:
            connection.close()

    async def pay_interest_day(
        self,
        guild_id: int,
        *,
        day: str,
        start: float,
        end: float,
        now: float,
        settle: Callable[[int, int, list[LedgerRow], Streaks], DayOutcome],
    ) -> InterestRun:
        """Paga los intereses de `day` a todos los miembros del servidor, una vez.

        Todo va en una transacción: o cobran todos o nadie, y el día queda
        marcado. Cada cobro deja en el libro el bruto (`intereses`) y la
        retención (`irpf:intereses`), que entra en la cuenta del Estado. Las
        cuentas especiales (Estado, ONGs, bote, tienda, con `user_id` <= 0) no
        cobran.

        Args:
            day: Día ISO que se paga.
            start: Su medianoche inicial (epoch).
            end: Su medianoche final.
            settle: Recibe `(miembro, saldo inicial, movimientos, rachas)` y
                devuelve el resultado (regla en `bot.services.interest`).
        """
        return await self._run(self._pay_interest_day_sync, guild_id, day, start, end, now, settle)

    def _pay_interest_day_sync(
        self,
        guild_id: int,
        day: str,
        start: float,
        end: float,
        now: float,
        settle: Callable[[int, int, list[LedgerRow], Streaks], DayOutcome],
    ) -> InterestRun:
        from bot.services.interest import Streaks

        with self._transaction() as connection:
            done = connection.execute(
                "SELECT 1 FROM economy_interest_days WHERE guild_id = ? AND day = ?",
                (guild_id, day),
            ).fetchone()
            if done is not None:
                return InterestRun(done_before=True)
            connection.execute(
                "INSERT INTO economy_interest_days (guild_id, day, processed_at) VALUES (?, ?, ?)",
                (guild_id, day, now),
            )
            openings, rows = self._day_rows_in(connection, guild_id, start, end)
            streaks = {
                int(row["user_id"]): Streaks(
                    int(row["capped"]),
                    int(row["floor"]),
                    int(row["resist"]),
                    int(row["still"]),
                    int(row["ant"]),
                )
                for row in connection.execute(
                    "SELECT * FROM economy_interest_streaks WHERE guild_id = ?", (guild_id,)
                )
            }
            outcomes = []
            for user_id in sorted(openings):
                outcome = settle(
                    user_id,
                    openings[user_id],
                    rows.get(user_id, []),
                    streaks.get(user_id, Streaks()),
                )
                payment = outcome.payment
                if payment.gross > 0:
                    entries = [LedgerEntry(payment.gross, "intereses")]
                    if payment.tax:
                        entries.append(LedgerEntry(-payment.tax, "irpf:intereses"))
                    self._apply_in_transaction(connection, guild_id, user_id, entries)
                    received = payment.gross - payment.tax
                    self._garnish_in(connection, guild_id, user_id, received, now)
                    if payment.tax:
                        self._credit_state_in(connection, guild_id, payment.tax, "irpf:intereses")
                    connection.execute(
                        """
                        INSERT INTO economy_interest
                            (guild_id, user_id, day, average, gross, tax, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (guild_id, user_id, day, payment.average, payment.gross, payment.tax, now),
                    )
                new = outcome.streaks
                connection.execute(
                    """
                    INSERT INTO economy_interest_streaks
                        (guild_id, user_id, capped, floor, resist, still, ant)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (guild_id, user_id) DO UPDATE SET
                        capped = excluded.capped, floor = excluded.floor,
                        resist = excluded.resist, still = excluded.still, ant = excluded.ant
                    """,
                    (guild_id, user_id, new.capped, new.floor, new.resist, new.still, new.ant),
                )
                outcomes.append(outcome)
            return InterestRun(outcomes=tuple(outcomes))

    async def settle_savings(
        self,
        guild_id: int,
        *,
        week: str,
        first_day: str,
        last_day: str,
        now: float,
        settle: Callable[[int, int, int, int], SavingsSettlement],
    ) -> SavingsRun:
        """Liquida con la escala del ahorro los intereses de una semana, una vez.

        Suma lo cobrado de `first_day` a `last_day` (ISO, ambos incluidos) por
        cada miembro y cobra la diferencia entre la cuota y lo ya retenido
        (`irpf:ahorro`), que va al Estado. Todo en una transacción.

        Args:
            week: Lunes ISO de la semana.
            settle: Recibe `(miembro, bruto, retenido, saldo)` y devuelve la
                liquidación (regla en `bot.services.interest.savings_settlement`).
        """
        return await self._run(
            self._settle_savings_sync, guild_id, week, first_day, last_day, now, settle
        )

    def _settle_savings_sync(
        self,
        guild_id: int,
        week: str,
        first_day: str,
        last_day: str,
        now: float,
        settle: Callable[[int, int, int, int], SavingsSettlement],
    ) -> SavingsRun:
        with self._transaction() as connection:
            done = connection.execute(
                "SELECT 1 FROM economy_interest_weeks WHERE guild_id = ? AND week_start = ?",
                (guild_id, week),
            ).fetchone()
            if done is not None:
                return SavingsRun(done_before=True)
            connection.execute(
                "INSERT INTO economy_interest_weeks (guild_id, week_start, processed_at) "
                "VALUES (?, ?, ?)",
                (guild_id, week, now),
            )
            totals = connection.execute(
                """
                SELECT i.user_id, SUM(i.gross) AS gross, SUM(i.tax) AS tax, w.balance
                FROM economy_interest i
                JOIN economy_wallets w ON w.guild_id = i.guild_id AND w.user_id = i.user_id
                WHERE i.guild_id = ? AND i.day BETWEEN ? AND ?
                GROUP BY i.user_id
                """,
                (guild_id, first_day, last_day),
            ).fetchall()
            settlements = []
            for row in totals:
                user_id = int(row["user_id"])
                result = settle(user_id, int(row["gross"]), int(row["tax"]), int(row["balance"]))
                if result.charged:
                    self._apply_in_transaction(
                        connection,
                        guild_id,
                        user_id,
                        (LedgerEntry(-result.charged, "irpf:ahorro"),),
                    )
                    self._credit_state_in(connection, guild_id, result.charged, "irpf:ahorro")
                connection.execute(
                    """
                    INSERT INTO economy_interest_savings
                        (guild_id, user_id, week_start, gross, withheld, quota, rate, charged,
                         created_at, notified)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        guild_id,
                        user_id,
                        week,
                        result.gross,
                        result.withheld,
                        result.quota,
                        round(result.rate * 100),
                        result.charged,
                        now,
                        # Sin nada que cobrar no hay nada que contar.
                        0 if result.charged else 1,
                    ),
                )
                settlements.append(result)
            return SavingsRun(settlements=tuple(settlements))

    async def last_interest(self, guild_id: int, user_id: int) -> tuple[str, int, int] | None:
        """Último cobro de intereses de un miembro: `(día ISO, bruto, retención)`."""
        return await self._run(self._last_interest_sync, guild_id, user_id)

    def _last_interest_sync(self, guild_id: int, user_id: int) -> tuple[str, int, int] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT day, gross, tax FROM economy_interest
                WHERE guild_id = ? AND user_id = ? ORDER BY day DESC LIMIT 1
                """,
                (guild_id, user_id),
            ).fetchone()
            return (str(row["day"]), int(row["gross"]), int(row["tax"])) if row else None
        finally:
            connection.close()

    async def take_interest_notice(self, guild_id: int, user_id: int) -> InterestNotice:
        """Lo que el miembro aún no ha visto de la cuenta, y lo marca como visto."""
        return await self._run(self._take_interest_notice_sync, guild_id, user_id)

    def _take_interest_notice_sync(self, guild_id: int, user_id: int) -> InterestNotice:
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS days, COALESCE(SUM(gross), 0) AS gross,
                       COALESCE(SUM(tax), 0) AS tax, MIN(day) AS first_day
                FROM economy_interest WHERE guild_id = ? AND user_id = ? AND notified = 0
                """,
                (guild_id, user_id),
            ).fetchone()
            savings = connection.execute(
                """
                SELECT week_start, charged, rate FROM economy_interest_savings
                WHERE guild_id = ? AND user_id = ? AND notified = 0 ORDER BY week_start
                """,
                (guild_id, user_id),
            ).fetchall()
            for table in ("economy_interest", "economy_interest_savings"):
                # `table` sale de una tupla fija, nunca de entrada del usuario.
                connection.execute(
                    f"UPDATE {table} SET notified = 1 "
                    "WHERE guild_id = ? AND user_id = ? AND notified = 0",
                    (guild_id, user_id),
                )
            return InterestNotice(
                days=int(row["days"]),
                gross=int(row["gross"]),
                tax=int(row["tax"]),
                first_day=row["first_day"],
                savings=tuple(
                    (str(s["week_start"]), int(s["charged"]), int(s["rate"])) for s in savings
                ),
            )

    async def last_savings(
        self, guild_id: int, user_id: int
    ) -> tuple[str, int, int, int, int, int] | None:
        """Última liquidación del ahorro: `(lunes, bruto, retenido, cuota, tipo %, cobrado)`."""
        return await self._run(self._last_savings_sync, guild_id, user_id)

    def _last_savings_sync(
        self, guild_id: int, user_id: int
    ) -> tuple[str, int, int, int, int, int] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT week_start, gross, withheld, quota, rate, charged
                FROM economy_interest_savings
                WHERE guild_id = ? AND user_id = ? ORDER BY week_start DESC LIMIT 1
                """,
                (guild_id, user_id),
            ).fetchone()
            if row is None:
                return None
            return (
                str(row["week_start"]),
                int(row["gross"]),
                int(row["withheld"]),
                int(row["quota"]),
                int(row["rate"]),
                int(row["charged"]),
            )
        finally:
            connection.close()

    # -- Donativos ---------------------------------------------------------------------

    async def donate(
        self,
        guild_id: int,
        user_id: int,
        *,
        ong: str,
        ong_account: int,
        amount: int,
        week: str,
        now: float,
    ) -> DonationReceipt:
        """Pasa `amount` del miembro a la cuenta de la ONG y apunta el donativo.

        Raises:
            InsufficientFundsError: Si no tiene tanto. No se mueve nada.
        """
        return await self._run(
            self._donate_sync, guild_id, user_id, ong, ong_account, amount, week, now
        )

    def _donate_sync(
        self,
        guild_id: int,
        user_id: int,
        ong: str,
        ong_account: int,
        amount: int,
        week: str,
        now: float,
    ) -> DonationReceipt:
        if ong_account >= 0:
            raise ValueError("Las cuentas de ONG tienen id negativo.")
        with self._transaction() as connection:
            balance = self._apply_in_transaction(
                connection, guild_id, user_id, (LedgerEntry(-amount, f"donativo:{ong}"),)
            )
            connection.execute(
                "INSERT OR IGNORE INTO economy_wallets (guild_id, user_id, balance) "
                "VALUES (?, ?, 0)",
                (guild_id, ong_account),
            )
            self._apply_in_transaction(
                connection, guild_id, ong_account, (LedgerEntry(amount, "donativo"),)
            )
            connection.execute(
                """
                INSERT INTO economy_donations
                    (guild_id, user_id, ong, amount, week_start, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (guild_id, user_id, ong, amount, week, now),
            )
            ongs, week_total = connection.execute(
                """
                SELECT COUNT(DISTINCT ong),
                       COALESCE(SUM(CASE WHEN week_start = ? THEN amount END), 0)
                FROM economy_donations WHERE guild_id = ? AND user_id = ?
                """,
                (week, guild_id, user_id),
            ).fetchone()
            return DonationReceipt(balance, int(ongs), int(week_total))

    # -- Bizum entre miembros ----------------------------------------------------------

    async def bizum(
        self,
        guild_id: int,
        sender_id: int,
        receiver_id: int,
        *,
        amount: int,
        now: float,
        day_start: float,
    ) -> BizumReceipt:
        """Pasa `amount` de un miembro a otro y apunta el Bizum.

        Sin impuestos: el dinero sale entero de uno y llega entero al otro
        (ver `bot.services.bizum`). Los dos monederos se tocan en la misma
        transacción, así que no hay un momento en que el dinero no esté en
        ninguno de los dos.

        Args:
            day_start: Inicio (epoch) del día local, para sumar lo enviado hoy.

        Raises:
            InsufficientFundsError: Si a quien envía no le llega. No se mueve nada.
            BalanceLimitError: Si quien recibe superaría el saldo máximo.
        """
        return await self._run(
            self._bizum_sync, guild_id, sender_id, receiver_id, amount, now, day_start
        )

    def _bizum_sync(
        self,
        guild_id: int,
        sender_id: int,
        receiver_id: int,
        amount: int,
        now: float,
        day_start: float,
    ) -> BizumReceipt:
        if amount <= 0:
            raise ValueError("Un Bizum tiene que ser positivo.")
        # Solo entre miembros: el Estado, las ONGs, el bote y la caja de la tienda
        # tienen id 0 o negativo y no pueden mandar ni recibir Bizums.
        if sender_id <= 0 or receiver_id <= 0 or sender_id == receiver_id:
            raise ValueError("Un Bizum va de un miembro a otro distinto.")
        with self._transaction() as connection:
            sender_balance = self._apply_in_transaction(
                connection, guild_id, sender_id, (LedgerEntry(-amount, "bizum:enviado"),)
            )
            receiver_balance = self._apply_in_transaction(
                connection, guild_id, receiver_id, (LedgerEntry(amount, "bizum:recibido"),)
            )
            receiver_balance -= self._garnish_in(connection, guild_id, receiver_id, amount, now)
            connection.execute(
                """
                INSERT INTO economy_bizums (guild_id, sender_id, receiver_id, amount, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (guild_id, sender_id, receiver_id, amount, now),
            )
            (sent_today,) = connection.execute(
                """
                SELECT COALESCE(SUM(amount), 0) FROM economy_bizums
                WHERE guild_id = ? AND sender_id = ? AND created_at >= ?
                """,
                (guild_id, sender_id, day_start),
            ).fetchone()
            return BizumReceipt(sender_balance, receiver_balance, int(sent_today))

    # -- Compras de la tienda --------------------------------------------------------

    def _open_empty_wallet(
        self, connection: sqlite3.Connection, guild_id: int, account_id: int
    ) -> None:
        """Abre a 0 una cuenta que no es de un miembro (sin saldo de bienvenida)."""
        connection.execute(
            "INSERT OR IGNORE INTO economy_wallets (guild_id, user_id, balance) VALUES (?, ?, 0)",
            (guild_id, account_id),
        )

    async def purchase(
        self,
        guild_id: int,
        user_id: int,
        *,
        base: int,
        tax: int,
        concept: str,
        now: float,
        reserve: Callable[[sqlite3.Connection], T],
    ) -> tuple[T, int]:
        """Cobra una compra: la base a la caja de la tienda y el IGIC al Estado.

        `reserve` lo pone la tienda y corre dentro de la misma transacción,
        antes del cobro: comprueba existencias y límites y apunta la venta. Si
        lanza, o si al miembro no le llega, no se mueve nada.

        Returns:
            `(lo que devuelva reserve, saldo final)`.

        Raises:
            InsufficientFundsError: Si no le llega el saldo.
        """
        return await self.purchase_lines(
            guild_id, user_id, lines=[(base, tax, concept)], now=now, reserve=reserve
        )

    async def purchase_lines(
        self,
        guild_id: int,
        user_id: int,
        *,
        lines: Sequence[tuple[int, int, str]],
        now: float,
        reserve: Callable[[sqlite3.Connection], T],
    ) -> tuple[T, int]:
        """Cobra un carrito de la tienda: varias líneas `(base, IGIC, concepto)` de una vez.

        Todo o nada, como `purchase`: si `reserve` lanza o no le llega para el
        total, no se mueve nada. Cada línea va al libro con su concepto.

        Raises:
            InsufficientFundsError: Si no le llega para el total.
        """
        return await self._run(self._purchase_sync, guild_id, user_id, tuple(lines), now, reserve)

    def _purchase_sync(
        self,
        guild_id: int,
        user_id: int,
        lines: tuple[tuple[int, int, str], ...],
        now: float,
        reserve: Callable[[sqlite3.Connection], T],
    ) -> tuple[T, int]:
        if not lines or any(base <= 0 or tax < 0 for base, tax, _concept in lines):
            raise ValueError("Una compra necesita base positiva e IGIC no negativo.")
        with self._transaction() as connection:
            result = reserve(connection)
            entries = []
            for base, tax, concept in lines:
                entries.append(LedgerEntry(-base, f"tienda:{concept}"))
                if tax:
                    entries.append(LedgerEntry(-tax, "tienda:igic"))
            balance = self._apply_in_transaction(connection, guild_id, user_id, entries)
            self._open_empty_wallet(connection, guild_id, SHOP_ACCOUNT_ID)
            for base, tax, concept in lines:
                self._apply_in_transaction(
                    connection, guild_id, SHOP_ACCOUNT_ID, (LedgerEntry(base, "tienda:venta"),)
                )
                if tax:
                    self._credit_state_in(connection, guild_id, tax, "igic:tienda")
                connection.execute(
                    """
                    INSERT INTO economy_consumption_tax
                        (guild_id, user_id, concept, base, tax, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (guild_id, user_id, concept, base, tax, now),
                )
            return result, balance

    async def refund_purchase(
        self,
        guild_id: int,
        user_id: int,
        *,
        base: int,
        tax: int,
        concept: str,
        now: float,
        release: Callable[[sqlite3.Connection], None],
    ) -> int:
        """Deshace una compra: la caja devuelve la base y el Estado el IGIC.

        `release` lo pone la tienda (marca la venta como devuelta y repone la
        unidad) y corre en la misma transacción.

        Returns:
            El saldo final del miembro.
        """
        return await self._run(
            self._refund_purchase_sync, guild_id, user_id, base, tax, concept, now, release
        )

    def _refund_purchase_sync(
        self,
        guild_id: int,
        user_id: int,
        base: int,
        tax: int,
        concept: str,
        now: float,
        release: Callable[[sqlite3.Connection], None],
    ) -> int:
        with self._transaction() as connection:
            release(connection)
            self._open_empty_wallet(connection, guild_id, SHOP_ACCOUNT_ID)
            self._apply_in_transaction(
                connection, guild_id, SHOP_ACCOUNT_ID, (LedgerEntry(-base, "tienda:devolucion"),)
            )
            entries = [LedgerEntry(base, f"devolucion:{concept}")]
            if tax:
                self._apply_in_transaction(
                    connection, guild_id, STATE_ACCOUNT_ID, (LedgerEntry(-tax, "igic:devolucion"),)
                )
                entries.append(LedgerEntry(tax, "devolucion:igic"))
                # Factura rectificativa: el IGIC devuelto resta de lo recaudado.
                connection.execute(
                    """
                    INSERT INTO economy_consumption_tax
                        (guild_id, user_id, concept, base, tax, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (guild_id, user_id, concept, -base, -tax, now),
                )
            return self._apply_in_transaction(connection, guild_id, user_id, entries)

    # -- Rastro ------------------------------------------------------------------------

    async def market(
        self,
        guild_id: int,
        *,
        now: float,
        prepare: Callable[[sqlite3.Connection], tuple[T, MarketMove]],
    ) -> tuple[T, dict[int, int]]:
        """Mueve el dinero de una operación del Rastro (puja, venta o cierre).

        `prepare` lo pone `bot.repositories.rastro` y corre dentro de la misma
        transacción, antes que el dinero: comprueba el puesto, mueve el objeto de
        la mochila y dice qué dinero se mueve. Si lanza, o si a alguien no le
        llega, no se guarda nada (tampoco lo de `prepare`).

        En orden: la puja entra en el depósito (`MARKET_ACCOUNT_ID`), salen las
        devoluciones y se paga la venta: quien compra paga precio e ITP (de su
        monedero o del depósito, si pujó), quien vende cobra el precio menos la
        plusvalía y los dos impuestos van al Estado y a `economy_market_tax`.

        Returns:
            `(lo que devuelva prepare, saldo final de cada miembro tocado)`.

        Raises:
            InsufficientFundsError: Si a quien puja o compra no le llega.
            BalanceLimitError: Si a alguien no le cabe lo que cobra.
        """
        return await self._run(self._market_sync, guild_id, now, prepare)

    def _market_sync(
        self,
        guild_id: int,
        now: float,
        prepare: Callable[[sqlite3.Connection], tuple[T, MarketMove]],
    ) -> tuple[T, dict[int, int]]:
        with self._transaction() as connection:
            result, move = prepare(connection)
            balances: dict[int, int] = {}
            self._open_empty_wallet(connection, guild_id, MARKET_ACCOUNT_ID)

            def deposit(amount: int, reason: str) -> None:
                self._apply_in_transaction(
                    connection, guild_id, MARKET_ACCOUNT_ID, (LedgerEntry(amount, reason),)
                )

            def charge(member: int, entries: list[LedgerEntry]) -> None:
                if entries:
                    balances[member] = self._apply_in_transaction(
                        connection, guild_id, member, entries
                    )

            if move.hold is not None:
                bidder, amount = move.hold
                charge(bidder, [LedgerEntry(-amount, "rastro:puja")])
                deposit(amount, "rastro:puja")
            for member, amount in move.refunds:
                deposit(-amount, "rastro:devolucion")
                charge(member, [LedgerEntry(amount, "rastro:puja_devuelta")])
            for pay in (*((move.payment,) if move.payment else ()), *move.payments):
                if pay.escrowed:
                    deposit(-(pay.price + pay.itp), "rastro:adjudicado")
                else:
                    entries = [LedgerEntry(-pay.price, "rastro:compra")] if pay.price else []
                    if pay.itp:
                        entries.append(LedgerEntry(-pay.itp, "rastro:itp"))
                    charge(pay.buyer_id, entries)
                entries = [LedgerEntry(pay.price, "rastro:venta")] if pay.price else []
                if pay.gain_tax:
                    entries.append(LedgerEntry(-pay.gain_tax, "rastro:plusvalia"))
                charge(pay.seller_id, entries)
                if entries and (paid := self._garnish_in(
                    connection, guild_id, pay.seller_id, pay.price - pay.gain_tax, now
                )):  # fmt: skip
                    balances[pay.seller_id] -= paid
                for member, kind, base, tax in (
                    (pay.buyer_id, "itp", pay.itp_base, pay.itp),
                    (pay.seller_id, "plusvalia", pay.gain, pay.gain_tax),
                ):
                    if not tax:
                        continue
                    self._credit_state_in(connection, guild_id, tax, f"rastro:{kind}")
                    connection.execute(
                        """
                        INSERT INTO economy_market_tax
                            (guild_id, user_id, kind, base, tax, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (guild_id, member, kind, base, tax, now),
                    )
                for member in (pay.buyer_id, pay.seller_id):
                    if member not in balances:
                        balances[member] = self._ensure_wallet(connection, guild_id, member)
            return result, balances

    # -- Monte de Piedad ---------------------------------------------------------------

    async def monte(
        self,
        guild_id: int,
        *,
        now: float,
        prepare: Callable[[sqlite3.Connection], tuple[T, MonteMove]],
    ) -> tuple[T, int]:
        """Mueve el dinero de una operación del Monte de Piedad (vender, empeñar, desempeñar).

        `prepare` lo pone `bot.repositories.monte` y corre dentro de la misma transacción,
        antes que el dinero: comprueba el objeto o la papeleta, lo mueve en la mochila y
        dice qué dinero se mueve. Si lanza, o si no llega el dinero, no se guarda nada.

        El dinero va entre el miembro y la caja de la tienda (`SHOP_ACCOUNT_ID`): el Monte
        paga con lo que se gastó en la tienda y lo que se le devuelve vuelve a la caja. Una
        venta paga además el embargo de la deuda con Hacienda (`MonteMove.garnish`).

        Returns:
            `(lo que devuelva prepare, saldo final del miembro)`.

        Raises:
            TillEmptyError: Si la caja de la tienda no llega para pagar.
            InsufficientFundsError: Si al miembro no le llega para desempeñar.
            BalanceLimitError: Si al miembro no le cabe lo que cobra.
        """
        return await self._run(self._monte_sync, guild_id, now, prepare)

    def _monte_sync(
        self,
        guild_id: int,
        now: float,
        prepare: Callable[[sqlite3.Connection], tuple[T, MonteMove]],
    ) -> tuple[T, int]:
        with self._transaction() as connection:
            result, move = prepare(connection)
            self._open_empty_wallet(connection, guild_id, SHOP_ACCOUNT_ID)
            amount = move.amount
            if amount > 0:
                if self._ensure_wallet(connection, guild_id, SHOP_ACCOUNT_ID) < amount:
                    raise TillEmptyError
                self._apply_in_transaction(
                    connection, guild_id, SHOP_ACCOUNT_ID, (LedgerEntry(-amount, move.till_reason),)
                )
            balance = self._apply_in_transaction(
                connection, guild_id, move.user_id, (LedgerEntry(amount, move.reason),)
            )
            if amount < 0:
                self._apply_in_transaction(
                    connection, guild_id, SHOP_ACCOUNT_ID, (LedgerEntry(-amount, move.till_reason),)
                )
            elif move.garnish:
                balance -= self._garnish_in(connection, guild_id, move.user_id, amount, now)
            return result, balance

    # -- Pisos ------------------------------------------------------------------------

    async def housing(
        self,
        guild_id: int,
        *,
        now: float,
        week: FiscalWeek,
        withhold: Callable[[HousingRent, Pace], int],
        prepare: Callable[[sqlite3.Connection], tuple[T, HousingMove]],
    ) -> tuple[T, dict[int, int]]:
        """Mueve el dinero de una operación de `pisos` (compra, venta o un día de rentas).

        `prepare` lo pone `bot.repositories.housing` y corre dentro de la misma
        transacción, antes que el dinero: comprueba y guarda quién tiene qué y dice
        qué dinero se mueve. Si lanza, o si a alguien no le llega, no se guarda nada
        (tampoco lo de `prepare`).

        En orden: se cobran las rentas (cada una con su retención, que va al Estado,
        y con su clase de renta en `economy_tax_records`), se aplican los movimientos
        sin impuesto y se cobran los impuestos (al Estado y a `economy_housing_tax`).
        Las imputaciones de los pisos vacíos solo se apuntan, y lo pagado por el
        alquiler y la hipoteca de la vivienda habitual queda en `economy_irpf_extras`
        para las deducciones canarias de la declaración.

        Args:
            week: Semana fiscal en curso.
            withhold: Recibe `(cobro, renta de la semana)` y devuelve la retención
                (regla en `bot.services.taxes`).

        Returns:
            `(lo que devuelva prepare, saldo final de cada miembro tocado)`.

        Raises:
            InsufficientFundsError: Si a quien compra no le llega.
            BalanceLimitError: Si a alguien no le cabe lo que cobra.
        """
        return await self._run(self._housing_sync, guild_id, now, week, withhold, prepare)

    def _housing_sync(
        self,
        guild_id: int,
        now: float,
        week: FiscalWeek,
        withhold: Callable[[HousingRent, Pace], int],
        prepare: Callable[[sqlite3.Connection], tuple[T, HousingMove]],
    ) -> tuple[T, dict[int, int]]:
        with self._transaction() as connection:
            result, move = prepare(connection)
            balances: dict[int, int] = {}
            before: dict[int, int] = {}
            self._housing_move_in(connection, guild_id, now, week, withhold, move, balances, before)
            for member in sorted(before):
                gained = balances.get(member, before[member]) - before[member]
                if gained > 0 and (paid := self._garnish_in(
                    connection, guild_id, member, gained, now, concept="embargo:patrimonio"
                )):  # fmt: skip
                    balances[member] = balances.get(member, before[member]) - paid
            return result, balances

    async def business(
        self,
        guild_id: int,
        *,
        now: float,
        week: FiscalWeek,
        withhold: Callable[[int, Pace], int],
        prepare: Callable[[sqlite3.Connection], tuple[T, BusinessMove]],
    ) -> tuple[T, BusinessPaid]:
        """Mueve el dinero de una operación de un negocio de `pala`.

        `prepare` lo pone `bot.services.negocio` y corre dentro de la misma transacción,
        antes que el dinero: lee y guarda el negocio y dice qué dinero se mueve. Si lanza,
        o si no llega para un pago entero, no se guarda nada (tampoco lo de `prepare`).

        Args:
            week: Semana fiscal en curso.
            withhold: Recibe `(margen del turno, renta de la semana)` y devuelve la
                retención a cuenta (regla en `bot.services.taxes`).

        Returns:
            `(lo que devuelva prepare, cómo quedó el dinero)`.

        Raises:
            InsufficientFundsError: Si no llega para un pago entero.
            BalanceLimitError: Si no le cabe lo que cobra.
        """
        return await self._run(self._business_sync, guild_id, now, week, withhold, prepare)

    def _business_sync(
        self,
        guild_id: int,
        now: float,
        week: FiscalWeek,
        withhold: Callable[[int, Pace], int],
        prepare: Callable[[sqlite3.Connection], tuple[T, BusinessMove]],
    ) -> tuple[T, BusinessPaid]:
        with self._transaction() as connection:
            result, move = prepare(connection)
            owner = move.user_id
            self._ensure_wallet(connection, guild_id, owner)
            company = move.account is not None
            # La caja que paga y cobra: la de la sociedad o el monedero del dueño.
            wallet = move.account if move.account is not None else owner
            if company:
                self._open_empty_wallet(connection, guild_id, wallet)

            def whole(member: int, amount: int, reason: str) -> None:
                if self._ensure_wallet(connection, guild_id, member) < amount:
                    raise InsufficientFundsError(self._ensure_wallet(connection, guild_id, member))
                self._apply_in_transaction(
                    connection, guild_id, member, (LedgerEntry(-amount, reason),)
                )

            tax = garnished = bank = 0
            if move.forfeit > 0:
                self._open_empty_wallet(connection, guild_id, HOUSING_DEPOSIT_ACCOUNT_ID)
                self._apply_in_transaction(
                    connection, guild_id, HOUSING_DEPOSIT_ACCOUNT_ID,
                    (LedgerEntry(-move.forfeit, "negocio:fianza_perdida"),),
                )  # fmt: skip
            if move.refund > 0:
                self._open_empty_wallet(connection, guild_id, HOUSING_DEPOSIT_ACCOUNT_ID)
                self._apply_in_transaction(
                    connection, guild_id, HOUSING_DEPOSIT_ACCOUNT_ID,
                    (LedgerEntry(-move.refund, "negocio:fianza_devuelta"),),
                )  # fmt: skip
                self._apply_in_transaction(
                    connection, guild_id, wallet,
                    (LedgerEntry(move.refund, "negocio:fianza_devuelta"),),
                )  # fmt: skip
            if move.bank_loan > 0:
                self._apply_in_transaction(
                    connection, guild_id, wallet,
                    (LedgerEntry(move.bank_loan, f"{BANK_LOAN_CONCEPT}firma"),),
                )  # fmt: skip
            for amount, reason in ((move.contribution, "sociedad:aportacion"),
                                   (move.loan_in, "sociedad:prestamo")):  # fmt: skip
                if company and amount > 0:
                    whole(owner, amount, reason)
                    self._apply_in_transaction(
                        connection, guild_id, wallet, (LedgerEntry(amount, reason),)
                    )
            if company and move.loan_back > 0:
                whole(wallet, move.loan_back, "sociedad:devolucion_prestamo")
                self._apply_in_transaction(
                    connection, guild_id, owner,
                    (LedgerEntry(move.loan_back, "sociedad:devolucion_prestamo"),),
                )  # fmt: skip
            for cost in move.payments:
                if cost.total <= 0:
                    continue
                if self._ensure_wallet(connection, guild_id, wallet) < cost.total:
                    raise InsufficientFundsError(self._ensure_wallet(connection, guild_id, wallet))
                self._business_cost_in(
                    connection, guild_id, wallet, cost, now, cost.total, owner=owner
                )
            if move.deposit > 0:
                self._open_empty_wallet(connection, guild_id, HOUSING_DEPOSIT_ACCOUNT_ID)
                self._apply_in_transaction(
                    connection, guild_id, wallet, (LedgerEntry(-move.deposit, "negocio:fianza"),)
                )
                self._apply_in_transaction(
                    connection, guild_id, HOUSING_DEPOSIT_ACCOUNT_ID,
                    (LedgerEntry(move.deposit, "negocio:fianza"),),
                )  # fmt: skip
            if move.sales > 0 and company:
                # Las ventas de una sociedad son suyas: ni IRPF ni embargos del dueño.
                self._apply_in_transaction(
                    connection, guild_id, wallet, (LedgerEntry(move.sales, "negocio:ventas"),)
                )
            elif move.sales > 0:
                pace = self._pace_in(connection, guild_id, owner, week)
                margin = max(0, move.sales - move.sold_cost)
                tax = max(0, min(move.sales, withhold(margin, pace)))
                entries = [LedgerEntry(move.sales, "negocio:ventas")]
                if tax:
                    entries.append(LedgerEntry(-tax, "irpf:negocio:ventas"))
                self._apply_in_transaction(connection, guild_id, owner, entries)
                if tax:
                    self._credit_state_in(connection, guild_id, tax, "irpf:negocio:ventas")
                self._record_in(
                    connection, guild_id, owner, now=now, concept="negocio:ventas",
                    kind=INCOME_BUSINESS, gross=move.sales, withheld=tax,
                )  # fmt: skip
                garnished = self._garnish_in(connection, guild_id, owner, move.sales - tax, now)
                bank = self._bank_garnish_in(
                    connection, guild_id, owner, move.sales - tax - garnished, now
                )
            gain_tax = 0
            if move.proceeds > 0 and company:
                # La ganancia de una sociedad tributa en su Impuesto sobre Sociedades.
                self._apply_in_transaction(
                    connection, guild_id, wallet, (LedgerEntry(move.proceeds, "negocio:traspaso"),)
                )
            elif move.proceeds > 0:
                gain_tax = min(move.proceeds, capital_gain_tax(move.gain))
                entries = [LedgerEntry(move.proceeds, "negocio:traspaso")]
                if gain_tax:
                    entries.append(LedgerEntry(-gain_tax, "irpf:negocio:traspaso"))
                    self._business_tax_in(connection, guild_id, owner, "plusvalia", gain_tax, now)
                    self._credit_state_in(connection, guild_id, gain_tax, "irpf:negocio:traspaso")
                self._apply_in_transaction(connection, guild_id, owner, entries)
                left = move.proceeds - gain_tax
                got = self._garnish_in(connection, guild_id, owner, left, now)
                garnished += got
                bank += self._bank_garnish_in(connection, guild_id, owner, left - got, now)
            if move.amortization > 0 and not company:
                self._record_in(
                    connection, guild_id, owner, now=now, concept="negocio:amortizacion",
                    kind=INCOME_BUSINESS, gross=-move.amortization, withheld=0,
                )  # fmt: skip
            unpaid = unpaid_tax = unpaid_bank = 0
            for cost in move.bills:
                if cost.total <= 0:
                    continue
                room = min(cost.total, self._ensure_wallet(connection, guild_id, wallet))
                unpaid += cost.total - room
                if cost.concept.startswith("sociedad:hacienda"):
                    unpaid_tax += cost.total - room
                elif cost.concept.startswith(BANK_LOAN_CONCEPT):
                    unpaid_bank += cost.total - room
                if cost.total > room and not company and cost.debt_surcharge is not None:
                    # Lo que el autónomo no paga de esto es deuda suya con Hacienda, con su
                    # recargo: la cobran el embargo y el barrido de los lunes.
                    connection.execute(
                        """
                        INSERT INTO economy_tax_debts (guild_id, user_id, amount)
                        VALUES (?, ?, ?)
                        ON CONFLICT (guild_id, user_id)
                        DO UPDATE SET amount = amount + excluded.amount
                        """,
                        (guild_id, owner, round((cost.total - room) * (1 + cost.debt_surcharge))),
                    )
                if room > 0:
                    self._business_cost_in(
                        connection, guild_id, wallet, cost, now, room, owner=owner
                    )
            salary_tax = dividend_tax = liquidated = 0
            if company and move.salary > 0:
                whole(wallet, move.salary, "sociedad:sueldo")
                salary_tax = min(move.salary, round(move.salary * move.salary_rate))
                net = move.salary - salary_tax
                entries = [LedgerEntry(move.salary, "sociedad:sueldo")]
                if salary_tax:
                    entries.append(LedgerEntry(-salary_tax, "irpf:sociedad:sueldo"))
                    self._credit_state_in(connection, guild_id, salary_tax, "irpf:sociedad:sueldo")
                self._apply_in_transaction(connection, guild_id, owner, entries)
                # Rendimiento del trabajo (art. 17.2.e LIRPF): a la Renta y, como nómina, al
                # IMV y al banco.
                self._record_in(
                    connection, guild_id, owner, now=now, concept="sociedad:sueldo",
                    kind=INCOME_WORK, gross=move.salary, withheld=salary_tax,
                )  # fmt: skip
                connection.execute(
                    "INSERT INTO economy_payroll (guild_id, user_id, concept, gross, ss_worker, "
                    "ss_employer, irpf, net, created_at) VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?)",
                    (guild_id, owner, "sociedad:sueldo", move.salary, salary_tax, net, now),
                )
                got = self._garnish_in(connection, guild_id, owner, net, now)
                garnished += got
                bank += self._bank_garnish_in(connection, guild_id, owner, net - got, now)
            if company and move.dividend > 0:
                whole(wallet, move.dividend, "sociedad:dividendo")
                dividend_tax = min(move.dividend, capital_gain_tax(move.dividend))
                entries = [LedgerEntry(move.dividend, "sociedad:dividendo")]
                if dividend_tax:
                    entries.append(LedgerEntry(-dividend_tax, "irpf:sociedad:dividendo"))
                    self._business_tax_in(
                        connection, guild_id, owner, "dividendo", dividend_tax, now
                    )
                    self._credit_state_in(
                        connection, guild_id, dividend_tax, "irpf:sociedad:dividendo"
                    )
                self._apply_in_transaction(connection, guild_id, owner, entries)
                net = move.dividend - dividend_tax
                got = self._garnish_in(connection, guild_id, owner, net, now)
                garnished += got
                bank += self._bank_garnish_in(connection, guild_id, owner, net - got, now)
            loan_repaid = covered = derived = 0
            if company and move.liquidation:
                loan_repaid = min(
                    move.liquidation_loan, max(0, self._ensure_wallet(connection, guild_id, wallet))
                )
                if loan_repaid:
                    whole(wallet, loan_repaid, "sociedad:devolucion_prestamo")
                    self._apply_in_transaction(
                        connection, guild_id, owner,
                        (LedgerEntry(loan_repaid, "sociedad:devolucion_prestamo"),),
                    )  # fmt: skip
                liquidated = max(0, self._ensure_wallet(connection, guild_id, wallet))
                if liquidated:
                    whole(wallet, liquidated, "sociedad:liquidacion")
                    gain = liquidated - move.liquidation_basis
                    gain_tax = min(liquidated, capital_gain_tax(gain))
                    entries = [LedgerEntry(liquidated, "sociedad:liquidacion")]
                    if gain_tax:
                        entries.append(LedgerEntry(-gain_tax, "irpf:sociedad:liquidacion"))
                        self._business_tax_in(
                            connection, guild_id, owner, "plusvalia", gain_tax, now
                        )
                        self._credit_state_in(
                            connection, guild_id, gain_tax, "irpf:sociedad:liquidacion"
                        )
                    self._apply_in_transaction(connection, guild_id, owner, entries)
                    net = liquidated - gain_tax
                    got = self._garnish_in(connection, guild_id, owner, net, now)
                    garnished += got
                    bank += self._bank_garnish_in(connection, guild_id, owner, net - got, now)
                # Lo que la sociedad no llegó a pagar: a Hacienda, el administrador; al resto,
                # el socio hasta su límite.
                if unpaid_tax:
                    paid = min(unpaid_tax, max(0, self._ensure_wallet(connection, guild_id, owner)))
                    if paid:
                        self._apply_in_transaction(
                            connection, guild_id, owner,
                            (LedgerEntry(-paid, "sociedad:derivacion"),),
                        )  # fmt: skip
                        self._credit_state_in(connection, guild_id, paid, "sociedad:derivacion")
                        self._business_tax_in(connection, guild_id, owner, "sociedades", paid, now)
                    covered += paid
                    derived = unpaid_tax - paid
                    if derived:
                        connection.execute(
                            """
                            INSERT INTO economy_tax_debts (guild_id, user_id, amount)
                            VALUES (?, ?, ?)
                            ON CONFLICT (guild_id, user_id)
                            DO UPDATE SET amount = amount + excluded.amount
                            """,
                            (guild_id, owner, derived),
                        )
                rest = min(unpaid - unpaid_tax - unpaid_bank, move.cover_limit)
                paid = min(rest, max(0, self._ensure_wallet(connection, guild_id, owner)))
                if paid:
                    self._apply_in_transaction(
                        connection, guild_id, owner,
                        (LedgerEntry(-paid, "sociedad:responsabilidad"),),
                    )  # fmt: skip
                    covered += paid
            if company and move.sale > 0:
                # La sociedad se va con el comprador, con su caja: sale del juego.
                cash = max(0, self._ensure_wallet(connection, guild_id, wallet))
                if cash:
                    self._apply_in_transaction(
                        connection, guild_id, wallet, (LedgerEntry(-cash, "sociedad:venta:caja"),)
                    )
                gain_tax = min(move.sale, capital_gain_tax(move.sale - move.liquidation_basis))
                entries = [LedgerEntry(move.sale, "sociedad:venta")]
                if move.liquidation_loan > 0:
                    entries.append(LedgerEntry(move.liquidation_loan, "sociedad:venta:prestamo"))
                if gain_tax:
                    entries.append(LedgerEntry(-gain_tax, "irpf:sociedad:venta"))
                    self._business_tax_in(connection, guild_id, owner, "plusvalia", gain_tax, now)
                    self._credit_state_in(connection, guild_id, gain_tax, "irpf:sociedad:venta")
                self._apply_in_transaction(connection, guild_id, owner, entries)
                net = move.sale + max(0, move.liquidation_loan) - gain_tax
                got = self._garnish_in(connection, guild_id, owner, net, now)
                garnished += got
                bank += self._bank_garnish_in(connection, guild_id, owner, net - got, now)
            if move.settle is not None:
                move.settle(connection, unpaid)
            balance = self._ensure_wallet(connection, guild_id, owner)
            cash = self._ensure_wallet(connection, guild_id, wallet) if company else 0
            return result, BusinessPaid(
                balance, tax, garnished, unpaid, bank, gain_tax, cash, salary_tax, dividend_tax,
                liquidated, loan_repaid, covered, derived,
            )  # fmt: skip

    @staticmethod
    def _business_tax_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, kind: str, tax: int,
        now: float,
    ) -> None:  # fmt: skip
        """Apunta un impuesto de un negocio para `hacienda` (a nombre de su dueño)."""
        connection.execute(
            "INSERT INTO economy_business_tax (guild_id, user_id, kind, tax, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (guild_id, user_id, kind, tax, now),
        )

    def _business_cost_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        cost: BusinessCost,
        now: float,
        most: int,
        *,
        owner: int | None = None,
    ) -> int:
        """Cobra hasta `most` de un pago de un negocio: primero los impuestos, luego el resto.

        Los impuestos van al Estado y a `economy_business_tax` (a nombre del dueño); lo demás
        desaparece. Si es gasto de la actividad de un autónomo (paga su monedero), lo cobrado
        se apunta en `economy_tax_records` en negativo; el de una sociedad (paga su caja) va a
        su Impuesto sobre Sociedades, que lleva el negocio.

        Args:
            user_id: Quien paga: el dueño o la caja de su sociedad.
            owner: El dueño (por defecto, quien paga).

        Returns:
            El saldo final de quien paga.
        """
        owner = user_id if owner is None else owner
        room = most
        entries: list[LedgerEntry] = []
        for kind, amount in cost.taxes:
            cut = min(room, max(0, amount))
            if cut <= 0:
                continue
            room -= cut
            reason = f"{cost.concept}:{kind}"
            entries.append(LedgerEntry(-cut, reason))
            self._credit_state_in(connection, guild_id, cut, reason)
            self._business_tax_in(connection, guild_id, owner, kind, cut, now)
        gone = min(room, max(0, cost.gone))
        if gone:
            entries.append(LedgerEntry(-gone, cost.concept))
        balance = self._apply_in_transaction(connection, guild_id, user_id, entries)
        paid = most - room + gone
        if cost.expense and paid and user_id == owner:
            self._record_in(
                connection, guild_id, user_id, now=now, concept=cost.concept,
                kind=INCOME_BUSINESS, gross=-paid, withheld=0,
            )  # fmt: skip
        return balance

    def _housing_move_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        now: float,
        week: FiscalWeek,
        withhold: Callable[[HousingRent, Pace], int],
        move: HousingMove,
        balances: dict[int, int],
        before: dict[int, int],
    ) -> None:
        """Aplica una etapa de `HousingMove` (y la siguiente, si la hay).

        Orden: pagos de miembros (enteros o nada), lo que venga después de saber cuáles
        se pagaron (`then`), rentas con su retención, pagos del Estado, movimientos sin
        impuesto, impuestos y recibos (hasta donde llegue cada monedero).
        """

        def touch(member: int) -> int:
            if member not in before:
                if member < 0:
                    # Una cuenta del sistema (el depósito de fianzas) se abre sin bienvenida.
                    self._open_empty_wallet(connection, guild_id, member)
                before[member] = self._ensure_wallet(connection, guild_id, member)
            return balances.get(member, before[member])

        def extra(member: int, kind: str, amount: int) -> None:
            if amount > 0:
                connection.execute(
                    "INSERT INTO economy_irpf_extras (guild_id, user_id, kind, amount, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (guild_id, member, kind, amount, now),
                )

        def credit(rent: HousingRent) -> None:
            touch(rent.user_id)
            tax = 0
            if rent.kind is not None:
                pace = self._pace_in(connection, guild_id, rent.user_id, week)
                tax = max(0, min(rent.gross, withhold(rent, pace)))
            entries = [LedgerEntry(rent.gross, rent.concept)]
            if tax:
                entries.append(LedgerEntry(-tax, f"irpf:{rent.concept}"))
            balances[rent.user_id] = self._apply_in_transaction(
                connection, guild_id, rent.user_id, entries
            )
            if tax:
                self._credit_state_in(connection, guild_id, tax, f"irpf:{rent.concept}")
            if rent.kind is not None:
                item = rent.property_week
                self._record_in(
                    connection, guild_id, rent.user_id, now=now, concept=rent.concept,
                    kind=rent.kind, gross=item.income, withheld=tax, costs=rent.costs,
                    finance=rent.finance, upkeep=rent.upkeep, ref=rent.ref,
                    reduction=rent.reduction, premium=rent.premium, bonus=rent.bonus,
                )  # fmt: skip

        for member, ref, amount in move.imputations:
            if amount > 0:
                self._record_in(
                    connection, guild_id, member, now=now, concept="pisos:imputacion",
                    kind=INCOME_IMPUTED, gross=amount, withheld=0, ref=ref,
                )  # fmt: skip
        for member, ref, amount in move.expenses:
            if amount > 0:
                self._record_in(
                    connection, guild_id, member, now=now, concept=HOUSING_RENT_CONCEPT,
                    kind=INCOME_PROPERTY, gross=0, withheld=0, costs=amount, ref=ref,
                )  # fmt: skip
        paid_tags: set[str] = set()
        for pull in move.pulls:
            if pull.amount <= 0:
                paid_tags.add(pull.tag)
                continue
            if touch(pull.user_id) < pull.amount:
                continue
            balances[pull.user_id] = self._apply_in_transaction(
                connection, guild_id, pull.user_id, (LedgerEntry(-pull.amount, pull.reason),)
            )
            if pull.home:
                extra(pull.user_id, EXTRA_RENT, pull.amount)
            paid_tags.add(pull.tag)
        unpaid: dict[str, tuple[int, int]] = {}
        for rent in move.rents:
            if rent.gross > 0:
                credit(rent)
        for grant in move.grants:
            if grant.gross <= 0:
                continue
            if self._state_balance_in(connection, guild_id) < grant.gross:
                left = unpaid.get(grant.tag, (0, 0))
                unpaid[grant.tag] = (left[0], left[1] + grant.gross)
                continue
            self._apply_in_transaction(
                connection, guild_id, STATE_ACCOUNT_ID, (LedgerEntry(-grant.gross, grant.concept),)
            )
            credit(grant)
        for member, entry in move.entries:
            if entry.delta:
                touch(member)
                balances[member] = self._apply_in_transaction(
                    connection, guild_id, member, (entry,)
                )
        for item in move.taxes:
            if item.tax <= 0:
                continue
            touch(item.user_id)
            reason = f"pisos:{item.kind}"
            balances[item.user_id] = self._apply_in_transaction(
                connection, guild_id, item.user_id, (LedgerEntry(-item.tax, reason),)
            )
            self._credit_state_in(connection, guild_id, item.tax, reason)
            connection.execute(
                """
                INSERT INTO economy_housing_tax
                    (guild_id, user_id, kind, base, tax, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (guild_id, item.user_id, item.kind, max(0, item.base), item.tax, now),
            )
        for debit in move.debits:
            balance = touch(debit.user_id)
            ibi = min(balance, max(0, debit.ibi))
            charges = [LedgerEntry(-ibi, "pisos:ibi")] if ibi else []
            room = balance - ibi
            owed_taxes = paid_taxes = 0
            for kind, due in debit.taxes:
                owed_taxes += max(0, due)
                cut = min(room, max(0, due))
                if not cut:
                    continue
                room -= cut
                paid_taxes += cut
                charges.append(LedgerEntry(-cut, f"pisos:{kind}"))
                self._credit_state_in(connection, guild_id, cut, f"pisos:{kind}")
                connection.execute(
                    """
                    INSERT INTO economy_housing_tax
                        (guild_id, user_id, kind, base, tax, created_at)
                    VALUES (?, ?, ?, 0, ?, ?)
                    """,
                    (guild_id, debit.user_id, kind, cut, now),
                )
            other = min(room, max(0, debit.other))
            if other:
                charges.append(LedgerEntry(-other, debit.reason))
            if charges:
                balances[debit.user_id] = self._apply_in_transaction(
                    connection, guild_id, debit.user_id, charges
                )
            if other and debit.to is not None:
                # El reembolso al avalista: lo que paga el deudor llega entero a quien le avaló.
                touch(debit.to)
                balances[debit.to] = self._apply_in_transaction(
                    connection, guild_id, debit.to, (LedgerEntry(other, f"{debit.reason}:cobro"),)
                )
            if ibi:
                self._credit_state_in(connection, guild_id, ibi, "pisos:ibi")
                connection.execute(
                    """
                    INSERT INTO economy_housing_tax
                        (guild_id, user_id, kind, base, tax, created_at)
                    VALUES (?, ?, 'ibi', 0, ?, ?)
                    """,
                    (guild_id, debit.user_id, ibi, now),
                )
            if debit.home:
                extra(debit.user_id, EXTRA_HOME, other)
            left = unpaid.get(debit.tag, (0, 0))
            unpaid[debit.tag] = (
                left[0] + max(0, debit.ibi) - ibi + owed_taxes - paid_taxes,
                left[1] + max(0, debit.other) - other,
            )
        if move.settle is not None:
            move.settle(connection, unpaid)
        if move.then is not None:
            self._housing_move_in(
                connection,
                guild_id,
                now,
                week,
                withhold,
                move.then(connection, frozenset(paid_tags)),
                balances,
                before,
            )

    def _collect_tax_debt_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        most: int,
        now: float,
        concept: str = "embargo:patrimonio",
    ) -> int:
        """Embarga hasta `most` de lo que acaba de cobrar un miembro para su deuda con Hacienda.

        Embargo de créditos y rentas (art. 169 de la Ley 58/2003): la deuda (el Patrimonio
        o la declaración que no le llegó en efectivo) se cobra de lo que entra. Lo usa
        `_garnish_in`, que decide cuánto. Va al Estado y `hacienda` lo cuenta como deudas
        cobradas.

        Args:
            concept: Motivo del libro (`embargo:patrimonio` en las rentas de `pisos`, que
                llegan solas cada noche; `embargo:deuda` en lo demás).

        Returns:
            Lo embargado (0 si no debe nada).
        """
        row = connection.execute(
            "SELECT amount FROM economy_tax_debts WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        owed = int(row["amount"]) if row else 0
        paid = min(owed, most)
        if paid <= 0:
            return 0
        self._apply_in_transaction(connection, guild_id, user_id, (LedgerEntry(-paid, concept),))
        self._credit_state_in(connection, guild_id, paid, concept)
        connection.execute(
            "UPDATE economy_tax_debts SET amount = amount - ? WHERE guild_id = ? AND user_id = ?",
            (paid, guild_id, user_id),
        )
        connection.execute(
            "INSERT INTO economy_debt_payments (guild_id, user_id, amount, created_at) "
            "VALUES (?, ?, ?, ?)",
            (guild_id, user_id, paid, now),
        )
        return paid

    async def tax_debt(self, guild_id: int, user_id: int) -> int:
        """Lo que un miembro debe todavía a Hacienda: Patrimonio o IRPF (0 si nada)."""
        return await self._run(self._tax_debt_sync, guild_id, user_id)

    def _tax_debt_sync(self, guild_id: int, user_id: int) -> int:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT amount FROM economy_tax_debts WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
        finally:
            connection.close()
        return int(row["amount"]) if row else 0

    # -- Loterías ----------------------------------------------------------------------

    async def state_balance(self, guild_id: int) -> int:
        """Saldo de la cuenta del Estado (0 si aún no existe)."""
        return await self._run(self._state_balance_sync, guild_id)

    def _state_balance_sync(self, guild_id: int) -> int:
        connection = self._connect()
        try:
            return self._state_balance_in(connection, guild_id)
        finally:
            connection.close()

    async def lottery(
        self,
        guild_id: int,
        *,
        charges: Sequence[tuple[int, int, str]],
        payouts: Sequence[LotteryPayout],
        now: float,
        hook: Callable[[sqlite3.Connection], T],
    ) -> tuple[T, dict[int, int]]:
        """Cobra boletos y paga premios de lotería con la cuenta del Estado de banca.

        Todo va en una transacción: `hook` (lo pone el repositorio de
        loterías: apunta boletos o cierra un sorteo) corre primero y, si lanza,
        no se mueve nada.

        Args:
            charges: `(user_id, importe, motivo)` de cada compra. El dinero va
                entero al Estado.
            payouts: Premios. El Estado paga el bruto y se queda el gravamen.
                Si no le llega el saldo, emite deuda pública por lo que falta.

        Returns:
            `(lo que devuelva hook, saldo final de cada miembro tocado)`.

        Raises:
            InsufficientFundsError: Si a alguien no le llega para su compra.
        """
        return await self._run(
            self._lottery_sync, guild_id, tuple(charges), tuple(payouts), now, hook
        )

    def _lottery_sync(
        self,
        guild_id: int,
        charges: tuple[tuple[int, int, str], ...],
        payouts: tuple[LotteryPayout, ...],
        now: float,
        hook: Callable[[sqlite3.Connection], T],
    ) -> tuple[T, dict[int, int]]:
        if any(cost <= 0 for _, cost, _ in charges):
            raise ValueError("Un boleto cuesta algo.")
        if any(p.gross <= 0 or not 0 <= p.tax <= p.gross for p in payouts):
            raise ValueError("Premio o gravamen fuera de rango.")
        balances: dict[int, int] = {}
        with self._transaction() as connection:
            result = hook(connection)
            for user_id, cost, reason in charges:
                balances[user_id] = self._apply_in_transaction(
                    connection, guild_id, user_id, (LedgerEntry(-cost, reason),)
                )
                self._credit_state_in(connection, guild_id, cost, "loteria:venta")
            for payout in payouts:
                self._open_empty_wallet(connection, guild_id, STATE_ACCOUNT_ID)
                shortfall = payout.gross - self._state_balance_in(connection, guild_id)
                if shortfall > 0:
                    self._credit_state_in(connection, guild_id, shortfall, "deuda:emision")
                    connection.execute(
                        "INSERT INTO economy_public_debt (guild_id, amount, concept, created_at) "
                        "VALUES (?, ?, ?, ?)",
                        (guild_id, shortfall, payout.concept, now),
                    )
                self._apply_in_transaction(
                    connection,
                    guild_id,
                    STATE_ACCOUNT_ID,
                    (LedgerEntry(-payout.gross, f"loteria:premio:{payout.concept}"),),
                )
                entries = [LedgerEntry(payout.gross, f"loteria:{payout.concept}")]
                if payout.tax:
                    entries.append(LedgerEntry(-payout.tax, "gravamen:loteria"))
                balances[payout.user_id] = self._apply_in_transaction(
                    connection, guild_id, payout.user_id, entries
                )
                balances[payout.user_id] -= self._garnish_in(
                    connection, guild_id, payout.user_id, payout.gross - payout.tax, now
                )
                if payout.tax:
                    self._credit_state_in(connection, guild_id, payout.tax, "gravamen:loteria")
                    connection.execute(
                        """
                        INSERT INTO economy_lottery_tax
                            (guild_id, user_id, concept, gross, tax, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (guild_id, payout.user_id, payout.concept, payout.gross, payout.tax, now),
                    )
            return result, balances

    async def ong_totals(self, guild_id: int) -> dict[str, tuple[int, int]]:
        """Por ONG: `(total recaudado, donantes distintos)` en el servidor."""
        return await self._run(self._ong_totals_sync, guild_id)

    def _ong_totals_sync(self, guild_id: int) -> dict[str, tuple[int, int]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT ong, SUM(amount) AS total, COUNT(DISTINCT user_id) AS donors
                FROM economy_donations WHERE guild_id = ? GROUP BY ong
                """,
                (guild_id,),
            ).fetchall()
            return {str(r["ong"]): (int(r["total"]), int(r["donors"])) for r in rows}
        finally:
            connection.close()

    async def ong_board(
        self, guild_id: int, ong: str | None = None, limit: int = 10
    ) -> list[tuple[int, int]]:
        """Los que más han donado (a `ong`, o a todas): `(user_id, total)`, de más a menos."""
        return await self._run(self._ong_board_sync, guild_id, ong, limit)

    def _ong_board_sync(self, guild_id: int, ong: str | None, limit: int) -> list[tuple[int, int]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT user_id, SUM(amount) AS total FROM economy_donations
                WHERE guild_id = ? AND (? IS NULL OR ong = ?)
                GROUP BY user_id ORDER BY total DESC, MIN(created_at) LIMIT ?
                """,
                (guild_id, ong, ong, limit),
            ).fetchall()
            return [(int(r["user_id"]), int(r["total"])) for r in rows]
        finally:
            connection.close()

    async def ong_member(self, guild_id: int, user_id: int) -> dict[str, int]:
        """Lo que ha donado un miembro a cada ONG, desde siempre."""
        return await self._run(self._ong_member_sync, guild_id, user_id)

    def _ong_member_sync(self, guild_id: int, user_id: int) -> dict[str, int]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT ong, SUM(amount) AS total FROM economy_donations
                WHERE guild_id = ? AND user_id = ? GROUP BY ong
                """,
                (guild_id, user_id),
            ).fetchall()
            return {str(r["ong"]): int(r["total"]) for r in rows}
        finally:
            connection.close()

    async def draw_ong_raffles(
        self,
        guild_id: int,
        draw_day: str,
        window: tuple[float, float],
        draw: Callable[[str, dict[int, tuple[int, int]]], int | None],
        now: float,
    ) -> list[tuple[str, int]]:
        """Sortea, una sola vez por ONG y sorteo, entre quienes donaron en `window`.

        Args:
            draw_day: Día del sorteo (ISO); con la ONG, lo que no se repite.
            window: `(desde, hasta)` en epoch: los donativos que dan boleto.
            draw: Recibe la ONG y sus participantes, `{user_id: (donado en la ventana,
                donado a esa ONG hasta el final de la ventana)}`, y devuelve el ganador.

        Returns:
            Los sorteos hechos ahora, `(ong, ganador)`. Los ya hechos no se repiten.
        """
        return await self._run(self._draw_ong_raffles_sync, guild_id, draw_day, window, draw, now)

    def _draw_ong_raffles_sync(
        self,
        guild_id: int,
        draw_day: str,
        window: tuple[float, float],
        draw: Callable[[str, dict[int, tuple[int, int]]], int | None],
        now: float,
    ) -> list[tuple[str, int]]:
        start, end = window
        with self._transaction() as connection:
            done = {
                str(row[0])
                for row in connection.execute(
                    "SELECT ong FROM economy_ong_raffles WHERE guild_id = ? AND draw_day = ?",
                    (guild_id, draw_day),
                )
            }
            entries: dict[str, dict[int, tuple[int, int]]] = {}
            for ong, user_id, recent, total in connection.execute(
                """
                SELECT ong, user_id,
                       SUM(CASE WHEN created_at >= ? THEN amount ELSE 0 END),
                       SUM(amount)
                FROM economy_donations
                WHERE guild_id = ? AND created_at < ?
                GROUP BY ong, user_id
                """,
                (start, guild_id, end),
            ):
                if recent > 0 and str(ong) not in done:
                    entries.setdefault(str(ong), {})[int(user_id)] = (int(recent), int(total))
            drawn = []
            for ong in sorted(entries):
                winner = draw(ong, entries[ong])
                if winner is None:
                    continue
                connection.execute(
                    """
                    INSERT INTO economy_ong_raffles (guild_id, draw_day, ong, user_id, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (guild_id, draw_day, ong, winner, now),
                )
                drawn.append((ong, winner))
            return drawn

    # -- IMV (recompensa diaria) ----------------------------------------------------

    async def claim_daily(
        self,
        guild_id: int,
        user_id: int,
        *,
        now: float,
        decide: Callable[[DailyClaim | None], tuple[int, int] | None],
    ) -> tuple[int, int] | None:
        """Cobra el IMV de forma atómica. Está exento de IRPF (art. 7.y LIRPF).

        La regla (espera, racha y cantidad) la pone el servicio a través de
        `decide`: recibe el estado anterior y devuelve `(cantidad, racha)` o
        `None` si todavía no toca. Se evalúa dentro de la transacción para
        que dos `.daily` simultáneos no cobren dos veces.

        Al estar exento no deja registro en `economy_tax_records`: no cuenta
        para la renta con la que se calculan las retenciones.

        Returns:
            `(cantidad, saldo_final)` si se cobró; `None` si `decide` lo rechazó.
        """
        return await self._run(self._claim_daily_sync, guild_id, user_id, now, decide)

    def _claim_daily_sync(
        self,
        guild_id: int,
        user_id: int,
        now: float,
        decide: Callable[[DailyClaim | None], tuple[int, int] | None],
    ) -> tuple[int, int] | None:
        with self._transaction() as connection:
            decision = decide(self._daily_state_in(connection, guild_id, user_id))
            if decision is None:
                return None
            amount, streak = decision
            balance = self._apply_in_transaction(
                connection, guild_id, user_id, (LedgerEntry(amount, "imv"),)
            )
            connection.execute(
                """
                INSERT INTO economy_daily (guild_id, user_id, last_claimed_at, streak)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    last_claimed_at = excluded.last_claimed_at,
                    streak = excluded.streak
                """,
                (guild_id, user_id, now, streak),
            )
            return amount, balance

    async def daily_state(self, guild_id: int, user_id: int) -> DailyClaim | None:
        """Devuelve la última recompensa diaria reclamada, si existe."""
        return await self._run(self._daily_state_sync, guild_id, user_id)

    def _daily_state_sync(self, guild_id: int, user_id: int) -> DailyClaim | None:
        connection = self._connect()
        try:
            return self._daily_state_in(connection, guild_id, user_id)
        finally:
            connection.close()

    @staticmethod
    def _daily_state_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int
    ) -> DailyClaim | None:
        row = connection.execute(
            """
            SELECT last_claimed_at, streak FROM economy_daily
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        ).fetchone()
        if row is None:
            return None
        return DailyClaim(last_claimed_at=float(row["last_claimed_at"]), streak=int(row["streak"]))

    # -- Nóminas, dinero en negro y sanciones ----------------------------------------

    async def credit_salary(
        self,
        guild_id: int,
        user_id: int,
        *,
        gross: int,
        concept: str,
        now: float,
        payslip_for: Callable[[int, Pace], Payslip],
        week: FiscalWeek,
        kind: str = INCOME_WORK,
    ) -> tuple[Payslip, int]:
        """Paga una nómina: bruto, cotización, IRPF y lo de la empresa, todo junto.

        En el libro del trabajador quedan tres movimientos: el bruto
        (`concept`), la Seguridad Social (`ss:concept`) y la retención
        (`irpf:concept`). Al Estado le entran la retención, la cotización del
        trabajador y la de la empresa; esta última es dinero nuevo, porque la
        empresa no existe. El desglose queda en `economy_payroll` y el bruto, con
        su clase de renta y su Seguridad Social, en `economy_tax_records`, que es la
        renta que ven las demás retenciones y la declaración.

        Args:
            payslip_for: Recibe `(bruto, renta de la semana)` y devuelve la nómina.
                La regla vive en `bot.services.taxes`.
            week: Semana fiscal en curso.
            kind: Clase de renta: trabajo, pensión, actividad (autónomos) o Beckham.

        Returns:
            `(nómina, saldo_final)`.
        """
        return await self._run(
            self._credit_salary_sync,
            guild_id,
            user_id,
            gross,
            concept,
            now,
            payslip_for,
            week,
            kind,
        )

    def _credit_salary_sync(
        self,
        guild_id: int,
        user_id: int,
        gross: int,
        concept: str,
        now: float,
        payslip_for: Callable[[int, Pace], Payslip],
        week: FiscalWeek,
        kind: str,
    ) -> tuple[Payslip, int]:
        with self._transaction() as connection:
            slip = payslip_for(gross, self._pace_in(connection, guild_id, user_id, week))
            entries = [LedgerEntry(slip.gross, concept)]
            if slip.ss_worker:
                entries.append(LedgerEntry(-slip.ss_worker, f"ss:{concept}"))
            if slip.irpf:
                entries.append(LedgerEntry(-slip.irpf, f"irpf:{concept}"))
            balance = self._apply_in_transaction(connection, guild_id, user_id, entries)
            if slip.irpf:
                self._credit_state_in(connection, guild_id, slip.irpf, f"irpf:{concept}")
            if slip.ss_worker:
                self._credit_state_in(connection, guild_id, slip.ss_worker, f"ss:{concept}")
            if slip.ss_employer:
                self._credit_state_in(
                    connection, guild_id, slip.ss_employer, f"ss_empresa:{concept}"
                )
            self._record_in(
                connection, guild_id, user_id, now=now, concept=concept, kind=kind,
                gross=slip.gross, withheld=slip.irpf, costs=slip.ss_worker,
            )  # fmt: skip
            connection.execute(
                """
                INSERT INTO economy_payroll (guild_id, user_id, concept, gross, ss_worker,
                                             ss_employer, irpf, net, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    user_id,
                    concept,
                    slip.gross,
                    slip.ss_worker,
                    slip.ss_employer,
                    slip.irpf,
                    slip.net,
                    now,
                ),
            )
            balance -= self._garnish_in(connection, guild_id, user_id, slip.net, now)
            balance -= self._bank_garnish_in(connection, guild_id, user_id, slip.net, now)
            return slip, balance

    def _bank_garnish_in(
        self, connection: sqlite3.Connection, guild_id: int, user_id: int, net: int, now: float
    ) -> int:
        """Embarga de una nómina, una pensión o las ventas de un negocio la deuda con el banco.

        La deuda con el banco es la que deja un préstamo vencido o una ejecución
        hipotecaria (`housing_bank_debts`). El juzgado embarga el sueldo con la escala del
        art. 607 LEC sobre todo lo cobrado en el día (`loans.wage_garnish`), después del
        embargo de Hacienda, que va primero (art. 77 de la Ley 58/2003). La escala vale
        también para lo que gana un autónomo (art. 607.6) y todo se acumula (art. 607.3):
        el día suma las nóminas y pensiones y las ventas de su negocio (lo cobrado ya
        apuntado, `net` incluido). El banco no existe: lo embargado desaparece y baja la
        deuda. Lo que quede de la escala después del banco va a lo que debe a los avalistas
        que pagaron por él (`housing_member_debts`, la acción de reembolso del art. 1838
        CC): eso sí llega a su monedero.

        Returns:
            Lo embargado (0 si no debe nada al banco ni a sus avalistas).
        """
        try:
            row = connection.execute(
                "SELECT amount FROM housing_bank_debts WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
        except sqlite3.OperationalError:
            # Sin las tablas de `pisos` (una base solo con la economía) no hay deudas.
            return 0
        try:
            members = connection.execute(
                "SELECT creditor_id, amount FROM housing_member_debts "
                "WHERE guild_id = ? AND debtor_id = ? AND amount > 0 ORDER BY since, creditor_id",
                (guild_id, user_id),
            ).fetchall()
        except sqlite3.OperationalError:
            # Una base de antes de la 9.3.0 que aún no ha abierto `pisos`.
            members = []
        owed = int(row["amount"]) if row else 0
        if (owed <= 0 and not members) or net <= 0:
            return 0
        start = datetime.combine(local_day(now), time.min, TIMEZONE).timestamp()
        (today,) = connection.execute(
            "SELECT COALESCE(SUM(net), 0) FROM economy_payroll "
            "WHERE guild_id = ? AND user_id = ? AND created_at >= ? AND net > 0",
            (guild_id, user_id, start),
        ).fetchone()
        (sales,) = connection.execute(
            "SELECT COALESCE(SUM(gross - withheld), 0) FROM economy_tax_records "
            "WHERE guild_id = ? AND user_id = ? AND created_at >= ? AND concept = 'negocio:ventas'",
            (guild_id, user_id, start),
        ).fetchone()
        today = int(today) + int(sales)
        # Lo de ahora (la nómina o las ventas del turno) ya está apuntado.
        scale = min(
            wage_garnish(int(today) - net, net), self._ensure_wallet(connection, guild_id, user_id)
        )
        taken = min(scale, owed)
        if taken > 0:
            self._apply_in_transaction(
                connection, guild_id, user_id, (LedgerEntry(-taken, "embargo:banco"),)
            )
            connection.execute(
                "UPDATE housing_bank_debts SET amount = amount - ? "
                "WHERE guild_id = ? AND user_id = ?",
                (taken, guild_id, user_id),
            )
        room = scale - taken
        for member in members:
            if room <= 0:
                break
            creditor, cut = int(member["creditor_id"]), min(room, int(member["amount"]))
            room -= cut
            taken += cut
            self._apply_in_transaction(
                connection, guild_id, user_id, (LedgerEntry(-cut, "embargo:reembolso"),)
            )
            self._ensure_wallet(connection, guild_id, creditor)
            self._apply_in_transaction(
                connection, guild_id, creditor, (LedgerEntry(cut, "embargo:reembolso:cobro"),)
            )
            connection.execute(
                "UPDATE housing_member_debts SET amount = amount - ? "
                "WHERE guild_id = ? AND creditor_id = ? AND debtor_id = ?",
                (cut, guild_id, creditor, user_id),
            )
        return taken

    async def charge_contribution(
        self, guild_id: int, user_id: int, *, amount: int, concept: str, now: float
    ) -> tuple[int, int]:
        """Cobra una cotización sin nómina (la cuota de autónomos) y la manda al Estado.

        Queda en `economy_payroll` como una fila sin bruto y con neto negativo:
        así cuenta como Seguridad Social en `hacienda` y resta del neto semanal
        que mira el IMV. Si no llega el saldo, se cobra lo que haya.

        Returns:
            `(cobrado, saldo_final)`.
        """
        return await self._run(
            self._charge_contribution_sync, guild_id, user_id, amount, concept, now
        )

    def _charge_contribution_sync(
        self, guild_id: int, user_id: int, amount: int, concept: str, now: float
    ) -> tuple[int, int]:
        with self._transaction() as connection:
            balance = self._ensure_wallet(connection, guild_id, user_id)
            charged = min(balance, amount)
            if charged <= 0:
                return 0, balance
            balance = self._apply_in_transaction(
                connection, guild_id, user_id, (LedgerEntry(-charged, f"ss:{concept}"),)
            )
            self._credit_state_in(connection, guild_id, charged, f"ss:{concept}")
            connection.execute(
                """
                INSERT INTO economy_payroll (guild_id, user_id, concept, gross, ss_worker,
                                             ss_employer, irpf, net, created_at)
                VALUES (?, ?, ?, 0, ?, 0, 0, ?, ?)
                """,
                (guild_id, user_id, concept, charged, -charged, now),
            )
            return charged, balance

    async def contribution_paid(self, guild_id: int, user_id: int, *, start: float) -> bool:
        """Si ha pagado la cuota de autónomos (`concept = 'autonomos'`) desde `start`."""
        return await self._run(self._contribution_paid_sync, guild_id, user_id, start)

    def _contribution_paid_sync(self, guild_id: int, user_id: int, start: float) -> bool:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT 1 FROM economy_payroll
                WHERE guild_id = ? AND user_id = ? AND gross = 0 AND concept = 'autonomos'
                  AND created_at >= ?
                LIMIT 1
                """,
                (guild_id, user_id, start),
            ).fetchone()
        finally:
            connection.close()
        return row is not None

    async def work_net(
        self, guild_id: int, user_id: int, since: float, *, bank: bool = False
    ) -> int:
        """Neto cobrado trabajando desde `since`: nóminas más lo que deja el negocio propio.

        El negocio cuenta con sus ventas menos la retención y los gastos de la actividad
        (`economy_tax_records`, concepto `negocio:`), nunca en negativo: comprar stock no
        sube el IMV ni el banco lo resta de la nómina. Lo cobrado en negro no cuenta.

        Args:
            bank: Lo que mira el banco: sin los turnos de `pala` ni del negocio cobrados
                dentro de un tramo de `hide_from_bank` (las pensiones y opciones, sí).
        """
        return await self._run(self._work_net_sync, guild_id, user_id, since, bank)

    def _work_net_sync(self, guild_id: int, user_id: int, since: float, bank: bool) -> int:
        # Para el banco, fuera lo cobrado dentro de un tramo de `hide_from_bank` (`t`, la fila).
        hidden = """EXISTS (
            SELECT 1 FROM economy_bank_hidden h
            WHERE h.guild_id = t.guild_id AND h.user_id = t.user_id
              AND t.created_at BETWEEN h.since AND h.until
        )"""
        # Las pensiones y las opciones no salen de los turnos: esas las ve siempre.
        payroll = (
            "AND (concept NOT LIKE 'pala:%' OR concept IN ('pala:pension', 'pala:opciones') "
            f"OR NOT {hidden})"
            if bank
            else ""
        )
        connection = self._connect()
        try:
            (net,) = connection.execute(
                """
                SELECT COALESCE(SUM(net), 0) FROM economy_payroll t
                WHERE guild_id = ? AND user_id = ? AND created_at > ? AND country = 'es'
                """
                + payroll,
                (guild_id, user_id, since),
            ).fetchone()
            (business,) = connection.execute(
                """
                SELECT COALESCE(SUM(gross - withheld), 0) FROM economy_tax_records t
                WHERE guild_id = ? AND user_id = ? AND created_at > ? AND kind = ?
                  AND concept LIKE 'negocio:%'
                """
                + (f"AND NOT {hidden}" if bank else ""),
                (guild_id, user_id, since, INCOME_BUSINESS),
            ).fetchone()
            return int(net) + max(0, int(business))
        finally:
            connection.close()

    async def hide_from_bank(self, guild_id: int, user_id: int, since: float, until: float) -> None:
        """Lo que cobre trabajando entre `since` y `until` no lo ve el banco (`work_net`)."""
        await self._run(self._hide_from_bank_sync, guild_id, user_id, since, until)

    def _hide_from_bank_sync(self, guild_id: int, user_id: int, since: float, until: float) -> None:
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO economy_bank_hidden (guild_id, user_id, since, until) "
                "VALUES (?, ?, ?, ?)",
                (guild_id, user_id, since, until),
            )

    async def payroll_totals(
        self, guild_id: int, user_id: int, since: float
    ) -> tuple[int, int, int, int]:
        """`(bruto, Seguridad Social total, IRPF, neto)` de las nóminas desde `since`."""
        return await self._run(self._payroll_totals_sync, guild_id, user_id, since)

    def _payroll_totals_sync(
        self, guild_id: int, user_id: int, since: float
    ) -> tuple[int, int, int, int]:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(gross), 0), COALESCE(SUM(ss_worker + ss_employer), 0),
                       COALESCE(SUM(irpf), 0), COALESCE(SUM(net), 0)
                FROM economy_payroll
                WHERE guild_id = ? AND user_id = ? AND created_at > ? AND country = 'es'
                """,
                (guild_id, user_id, since),
            ).fetchone()
            return int(row[0]), int(row[1]), int(row[2]), int(row[3])
        finally:
            connection.close()

    async def other_taxes(self, guild_id: int, user_id: int, since: float) -> int:
        """IGIC, Patrimonio, IRPF de los intereses, lo del Rastro y lo pagado en la
        declaración desde `since`.

        Para el logro «Socio de Hacienda».
        """
        return await self._run(self._other_taxes_sync, guild_id, user_id, since)

    def _other_taxes_sync(self, guild_id: int, user_id: int, since: float) -> int:
        connection = self._connect()
        try:
            (igic,) = connection.execute(
                """
                SELECT COALESCE(SUM(tax), 0) FROM economy_consumption_tax
                WHERE guild_id = ? AND user_id = ? AND created_at > ?
                """,
                (guild_id, user_id, since),
            ).fetchone()
            (wealth,) = connection.execute(
                """
                SELECT COALESCE(SUM(tax), 0) FROM economy_wealth_tax
                WHERE guild_id = ? AND user_id = ? AND created_at > ?
                """,
                (guild_id, user_id, since),
            ).fetchone()
            (interest,) = connection.execute(
                """
                SELECT COALESCE(SUM(tax), 0) FROM economy_interest
                WHERE guild_id = ? AND user_id = ? AND created_at > ?
                """,
                (guild_id, user_id, since),
            ).fetchone()
            (savings,) = connection.execute(
                """
                SELECT COALESCE(SUM(charged), 0) FROM economy_interest_savings
                WHERE guild_id = ? AND user_id = ? AND created_at > ?
                """,
                (guild_id, user_id, since),
            ).fetchone()
            (market,) = connection.execute(
                """
                SELECT COALESCE(SUM(tax), 0) FROM economy_market_tax
                WHERE guild_id = ? AND user_id = ? AND created_at > ?
                """,
                (guild_id, user_id, since),
            ).fetchone()
            (declared,) = connection.execute(
                """
                SELECT COALESCE(SUM(charged), 0) FROM economy_irpf_returns
                WHERE guild_id = ? AND user_id = ? AND created_at > ?
                """,
                (guild_id, user_id, since),
            ).fetchone()
            return (
                int(igic) + int(wealth) + int(interest) + int(savings) + int(market) + int(declared)
            )
        finally:
            connection.close()

    async def sanction(
        self, guild_id: int, user_id: int, *, amount: int, concept: str, now: float
    ) -> tuple[int, int]:
        """Cobra una multa al Estado; si no llega el saldo, se queda con lo que haya.

        Returns:
            `(cobrado, saldo_final)`.
        """
        return await self._run(self._sanction_sync, guild_id, user_id, amount, concept, now)

    def _sanction_sync(
        self, guild_id: int, user_id: int, amount: int, concept: str, now: float
    ) -> tuple[int, int]:
        with self._transaction() as connection:
            balance = self._ensure_wallet(connection, guild_id, user_id)
            charged = min(balance, amount)
            if charged <= 0:
                return 0, balance
            balance = self._apply_in_transaction(
                connection, guild_id, user_id, (LedgerEntry(-charged, f"multa:{concept}"),)
            )
            self._credit_state_in(connection, guild_id, charged, f"multa:{concept}")
            connection.execute(
                """
                INSERT INTO economy_sanctions (guild_id, user_id, concept, amount, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (guild_id, user_id, concept, charged, now),
            )
            return charged, balance

    async def suspend_imv(self, guild_id: int, user_id: int, until: float) -> None:
        """Deja sin IMV a un miembro hasta `until` (alarga, nunca acorta)."""
        await self._run(self._suspend_imv_sync, guild_id, user_id, until)

    def _suspend_imv_sync(self, guild_id: int, user_id: int, until: float) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO economy_imv_suspensions (guild_id, user_id, until) VALUES (?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET until = MAX(until, excluded.until)
                """,
                (guild_id, user_id, until),
            )

    async def imv_suspended_until(self, guild_id: int, user_id: int) -> float:
        """Hasta cuándo está suspendido el IMV (0 si no lo está)."""
        return await self._run(self._imv_suspended_sync, guild_id, user_id)

    def _imv_suspended_sync(self, guild_id: int, user_id: int) -> float:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT until FROM economy_imv_suspensions WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            return float(row["until"]) if row else 0.0
        finally:
            connection.close()

    # -- Trabajar fuera ------------------------------------------------------------------

    async def credit_foreign_salary(
        self,
        guild_id: int,
        user_id: int,
        *,
        gross: int,
        concept: str,
        country: str,
        account_id: int,
        now: float,
        payslip_for: Callable[[int, int, Pace], ForeignPayslip],
        week: FiscalWeek,
        foreign_window_seconds: float,
    ) -> tuple[ForeignPayslip, int]:
        """Paga una nómina cobrada fuera de España.

        En el libro: el bruto, la cotización del otro país (`mpf:concept`), su
        impuesto (`impuesto_ext:concept`) y, si hay, el IRPF español. Lo del otro
        país (las dos cotizaciones y el impuesto) va a `account_id`; el IRPF, al
        Estado. La parte sujeta en España queda en `economy_tax_records` como trabajo
        en el extranjero, con lo pagado allí que la declaración puede deducir.

        Args:
            payslip_for: Recibe `(bruto, bruto de fuera de la ventana, renta española
                de la semana)` y devuelve la nómina.
            week: Semana fiscal en curso.
            foreign_window_seconds: Ventana de lo cobrado en el otro país, que
                tiene su propio año fiscal.

        Returns:
            `(nómina, saldo_final)`.
        """
        return await self._run(
            self._credit_foreign_sync,
            guild_id,
            user_id,
            gross,
            concept,
            country,
            account_id,
            now,
            payslip_for,
            week,
            foreign_window_seconds,
        )

    def _credit_foreign_sync(
        self,
        guild_id: int,
        user_id: int,
        gross: int,
        concept: str,
        country: str,
        account_id: int,
        now: float,
        payslip_for: Callable[[int, int, Pace], ForeignPayslip],
        week: FiscalWeek,
        foreign_window_seconds: float,
    ) -> tuple[ForeignPayslip, int]:
        with self._transaction() as connection:
            (recent_foreign,) = connection.execute(
                """
                SELECT COALESCE(SUM(gross), 0) FROM economy_payroll
                WHERE guild_id = ? AND user_id = ? AND created_at > ? AND country = ?
                """,
                (guild_id, user_id, now - foreign_window_seconds, country),
            ).fetchone()
            pace = self._pace_in(connection, guild_id, user_id, week)
            slip = payslip_for(gross, int(recent_foreign), pace)
            entries = [LedgerEntry(slip.gross, concept)]
            if slip.mpf_worker:
                entries.append(LedgerEntry(-slip.mpf_worker, f"mpf:{concept}"))
            if slip.hk_tax:
                entries.append(LedgerEntry(-slip.hk_tax, f"impuesto_ext:{concept}"))
            if slip.irpf:
                entries.append(LedgerEntry(-slip.irpf, f"irpf:{concept}"))
            balance = self._apply_in_transaction(connection, guild_id, user_id, entries)
            if slip.foreign:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO economy_wallets (guild_id, user_id, balance)
                    VALUES (?, ?, 0)
                    """,
                    (guild_id, account_id),
                )
                self._apply_in_transaction(
                    connection,
                    guild_id,
                    account_id,
                    (LedgerEntry(slip.foreign, f"impuesto_ext:{concept}"),),
                )
            if slip.irpf:
                self._credit_state_in(connection, guild_id, slip.irpf, f"irpf:{concept}")
            if slip.taxable > 0:
                self._record_in(
                    connection, guild_id, user_id, now=now, concept=concept,
                    kind=INCOME_FOREIGN, gross=slip.taxable, withheld=slip.irpf,
                    credit=slip.credit,
                )  # fmt: skip
            connection.execute(
                """
                INSERT INTO economy_payroll (guild_id, user_id, concept, gross, ss_worker,
                                             ss_employer, irpf, net, country, foreign_tax,
                                             created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    user_id,
                    concept,
                    slip.gross,
                    slip.mpf_worker,
                    slip.mpf_employer,
                    slip.irpf,
                    slip.net,
                    country,
                    slip.hk_tax,
                    now,
                ),
            )
            balance -= self._garnish_in(connection, guild_id, user_id, slip.net, now)
            return slip, balance

    async def foreign_taxes(self, guild_id: int, user_id: int, country: str) -> int:
        """Todo lo que se ha quedado otro país (impuesto y cotizaciones) de un miembro."""
        return await self._run(self._foreign_taxes_sync, guild_id, user_id, country)

    def _foreign_taxes_sync(self, guild_id: int, user_id: int, country: str) -> int:
        connection = self._connect()
        try:
            (total,) = connection.execute(
                """
                SELECT COALESCE(SUM(foreign_tax + ss_worker + ss_employer), 0)
                FROM economy_payroll WHERE guild_id = ? AND user_id = ? AND country = ?
                """,
                (guild_id, user_id, country),
            ).fetchone()
            return int(total)
        finally:
            connection.close()

    async def set_residence(
        self, guild_id: int, user_id: int, country: str | None, now: float
    ) -> None:
        """Apunta que vive fuera (`country`) o que ha vuelto (`None`)."""
        await self._run(self._set_residence_sync, guild_id, user_id, country, now)

    def _set_residence_sync(
        self, guild_id: int, user_id: int, country: str | None, now: float
    ) -> None:
        with self._transaction() as connection:
            if country is None:
                connection.execute(
                    "DELETE FROM economy_residence WHERE guild_id = ? AND user_id = ?",
                    (guild_id, user_id),
                )
                return
            connection.execute(
                """
                INSERT INTO economy_residence (guild_id, user_id, country, since)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    country = excluded.country, since = excluded.since
                """,
                (guild_id, user_id, country, now),
            )

    async def residence(self, guild_id: int, user_id: int) -> str | None:
        """País donde vive, o `None` si vive en España."""
        return await self._run(self._residence_sync, guild_id, user_id)

    def _residence_sync(self, guild_id: int, user_id: int) -> str | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT country FROM economy_residence WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            return str(row["country"]) if row else None
        finally:
            connection.close()

    # -- Limpieza ------------------------------------------------------------------

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra toda la economía de un servidor cuando el bot sale de él."""
        await self._run(self._delete_guild_data_sync, guild_id)

    def _delete_guild_data_sync(self, guild_id: int) -> None:
        with self._transaction() as connection:
            # Las patas no llevan servidor: se borran por su combinada, antes que ella.
            connection.execute(
                """
                DELETE FROM economy_sports_combo_legs WHERE combo_id IN (
                    SELECT id FROM economy_sports_combos WHERE guild_id = ?
                )
                """,
                (guild_id,),
            )
            for table in (
                "economy_wallets",
                "economy_ledger",
                "economy_daily",
                "economy_tax_records",
                "economy_gambling_days",
                "economy_declarations",
                "economy_wealth_weeks",
                "economy_wealth_tax",
                "economy_solidarity_tax",
                "economy_donations",
                "economy_ong_raffles",
                "economy_bizums",
                "economy_slots_jackpots",
                "economy_slots_mystery",
                "economy_consumption_tax",
                "economy_business_tax",
                "economy_lottery_tax",
                "economy_public_debt",
                "economy_payroll",
                "economy_bank_hidden",
                "economy_sanctions",
                "economy_imv_suspensions",
                "economy_residence",
                "economy_interest_days",
                "economy_interest",
                "economy_interest_streaks",
                "economy_interest_weeks",
                "economy_interest_savings",
                "economy_porra_bets",
                "economy_porra_settled",
                "economy_sports_bets",
                "economy_sports_combos",
                "economy_predict_bets",
                "economy_gaming_tax",
                "economy_market_tax",
                "economy_housing_tax",
                "economy_tax_debts",
                "economy_debt_payments",
            ):
                # `table` sale de una tupla fija, nunca de entrada del usuario.
                connection.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
