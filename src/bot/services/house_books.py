"""El casino y el banco como dos fortunas más: lo que han ganado y lo que pagan a Hacienda.

Ninguno de los dos tiene monedero: lo que pierde un jugador en el casino y lo que se paga
al banco desaparece del libro, y lo que el casino paga en premios y el banco presta se crea
de la nada. Su fortuna se calcula al vuelo con `economy_ledger` (cada movimiento, con su
motivo, desde el primer día) y las tablas de hipotecas y préstamos, así que sale ya con
todo lo apostado y financiado antes de que existiera esta cuenta. No mueve dinero ni toca
Discord: recibe cifras y devuelve cifras.

Qué es la fortuna de cada uno:

- **🎰 Casino:** lo apostado en todos los juegos menos lo pagado en premios (lo que «gana
  la casa», la misma cifra que 📒 Cuentas de `banco` y 📒 Libro de `apuestas`), menos los
  impuestos que ha pagado.
- **🏦 Banco:** su caja (lo cobrado menos lo prestado) más lo que le deben (hipotecas,
  préstamos, cuotas sin pagar y la deuda tras una ejecución o un vencimiento), menos los
  impuestos que ha pagado. Es su beneficio: intereses, comisiones, tasaciones y seguros,
  menos lo que se ha perdido en impagos. Lo que debe cada miembro al banco ya le resta en
  `fortunas`; aquí cuenta a favor del banco.

Lo cobrado y lo prestado salen de los motivos del libro (`BANK_PAYOUTS` y
`BANK_RECEIPTS`), de miembros y de sociedades. Las hipotecas de una compra no pasan por el
monedero de quien compra (el banco paga al vendedor o al catálogo), así que lo prestado en
ellas sale de `housing_mortgages` (`purchase_lent`). Una prueba
(`tests/unit/test_house_books.py`) falla si aparece un motivo nuevo del banco sin
clasificar.

En `fortunas` (solo para enseñar: no cambia lo que pagan):

- **🏦 Banco:** además de su beneficio, custodia los depósitos de todos: el efectivo de los
  miembros, la caja del Estado y la del Casino. Cuentan como suyos, con lo que le deben, y
  por eso superarlo es casi imposible.
- **🎰 Casino:** lo que gana la casa más lo que vale lo construido en el Jovaniverso
  (`universo_editor.casino_value`): cuanto más grande el casino, más rico.
- **🏛️ Estado:** su caja (`StateBook`), lo que paga en nóminas públicas (`pala`,
  funcionariado) y lo que se cobra a sí mismo en impuestos de esas nóminas. No paga ningún
  impuesto aparte: ya se los paga a sí mismo.

Impuestos (los cobra `bot.cogs.patrimonio` cada lunes, con `EconomyService.charge_house_taxes`):

- El casino paga la **tasa fiscal sobre el juego** sobre lo que gana la casa: la de los
  casinos va del 20 % al 55 % según sus ingresos brutos (art. 3.4 del RDL 16/1977); aquí, el
  primer tramo, un 20 % fijo (`CASINO_RATE`).
- El banco paga el **Impuesto sobre Sociedades** de las entidades de crédito, un 30 %
  (art. 29.6 LIS), sobre su beneficio (`BANK_RATE`).

La base de cada lunes es lo que su beneficio acumulado pase del máximo que ya tributó
(`peak`): si una semana pierde, no paga hasta recuperar lo perdido, como la compensación de
bases negativas del art. 26 LIS. El primer lunes el máximo es 0, así que tributa todo lo
ganado desde el primer día.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Claves de las dos cuentas (columna `entity` de `economy_house_tax`).
CASINO = "casino"
BANK = "banco"

#: Tasa fiscal sobre el juego de los casinos: primer tramo de la tarifa del art. 3.4 del
#: RDL 16/1977, fijo.
CASINO_RATE = 0.20
#: Impuesto sobre Sociedades de las entidades de crédito (art. 29.6 LIS).
BANK_RATE = 0.30

#: Motivos del libro en los que el banco paga (entra dinero a un miembro o a una sociedad).
#: Los préstamos personales, la financiación de una compra, las rehipotecas y los anticipos
#: entran en el monedero; la comisión devuelta al desistir, también.
BANK_PAYOUTS = (
    "banco:prestamo",
    "banco:financiacion",
    "banco:rehipoteca",
    "banco:anticipo",
    "banco:apertura_devuelta",
    "negocio:prestamo:firma",
)
#: Motivos del libro en los que el banco cobra (sale dinero de un miembro o una sociedad):
#: cuotas, amortizaciones, cancelaciones al vender, comisiones, tasaciones, lo que se
#: devuelve al desistir, lo que se embarga de las nóminas y lo cobrado de deudas y
#: ejecuciones. El IPS de las primas no está aquí: va al Estado con su propio motivo.
BANK_RECEIPTS = (
    "pisos:hipoteca",
    "pisos:amortizacion",
    "pisos:hipoteca_cancelada",
    "pisos:banco",
    "pisos:deuda_banco",
    "banco:cuota",
    "banco:amortizacion",
    "banco:compensacion",
    "banco:apertura",
    "banco:tasacion",
    "banco:desistimiento",
    "embargo:banco",
    "negocio:prestamo:apertura",
    "negocio:prestamo:amortizacion",
    "negocio:prestamo:compensacion",
    "negocio:prestamo:intereses",
    "negocio:prestamo:cuota",
    "negocio:prestamo:cancelacion",
)
BANK_REASONS = BANK_PAYOUTS + BANK_RECEIPTS
#: Motivos que empiezan por `banco:` pero no son del banco: el reembolso al avalista va de
#: un miembro a otro.
NOT_BANK = ("banco:reembolso",)


@dataclass(frozen=True, slots=True)
class CasinoBook:
    """Las cuentas del casino.

    Attributes:
        staked: Todo lo apostado desde el primer día.
        paid: Todo lo pagado en premios y botes.
        plays: Jugadas.
        taxes: Lo que ha pagado a Hacienda.
    """

    staked: int = 0
    paid: int = 0
    plays: int = 0
    taxes: int = 0

    @property
    def profit(self) -> int:
        """Lo que gana la casa antes de impuestos (negativo si pierde)."""
        return self.staked - self.paid

    @property
    def total(self) -> int:
        """Su fortuna: lo ganado menos los impuestos."""
        return self.profit - self.taxes


@dataclass(frozen=True, slots=True)
class BankBook:
    """Las cuentas del banco para su fortuna.

    Attributes:
        flow: Lo cobrado menos lo pagado según el libro (`BANK_REASONS`).
        purchase_lent: Lo prestado en hipotecas de compra, que no pasa por el libro.
        owed: Lo que le deben ahora (capital pendiente, cuotas sin pagar y deudas).
        taxes: Lo que ha pagado a Hacienda.
    """

    flow: int = 0
    purchase_lent: int = 0
    owed: int = 0
    taxes: int = 0

    @property
    def cash(self) -> int:
        """Su caja antes de impuestos: lo cobrado menos todo lo prestado (suele ser negativa)."""
        return self.flow - self.purchase_lent

    @property
    def profit(self) -> int:
        """Su beneficio antes de impuestos: la caja más lo que le deben."""
        return self.cash + self.owed

    @property
    def total(self) -> int:
        """Su fortuna: el beneficio menos los impuestos."""
        return self.profit - self.taxes


@dataclass(frozen=True, slots=True)
class StateBook:
    """Las cuentas del Estado para `fortunas`.

    Attributes:
        cash: Saldo de su cuenta (lo recaudado menos lo gastado).
        payroll: Lo pagado en nóminas públicas desde el primer día (bruto y cotización de
            la empresa).
        payroll_week: Lo mismo, desde el lunes.
        self_taxes: Lo que ha vuelto a su cuenta de esas nóminas (retención y cotizaciones):
            los impuestos que se paga a sí mismo.
    """

    cash: int = 0
    payroll: int = 0
    payroll_week: int = 0
    self_taxes: int = 0


def casino_book(rows: list[tuple[str, int, int]], taxes: int = 0) -> CasinoBook:
    """Las cuentas del casino con las filas de `EconomyService.casino_ledger`.

    Args:
        rows: `(motivo, movimientos, suma)` de los motivos `:apuesta`, `:premio` y `:bote`.
        taxes: Lo que ha pagado ya a Hacienda.
    """
    staked = -sum(total for reason, _n, total in rows if reason.endswith(":apuesta"))
    paid = sum(total for reason, _n, total in rows if not reason.endswith(":apuesta"))
    plays = sum(count for reason, count, _t in rows if reason.endswith(":apuesta"))
    return CasinoBook(staked=staked, paid=paid, plays=plays, taxes=taxes)


@dataclass(frozen=True, slots=True)
class HouseCharge:
    """Lo que paga una de las dos cuentas un lunes.

    Attributes:
        entity: `CASINO` o `BANK`.
        profit: Su beneficio acumulado antes de impuestos al cobrar.
        peak: El máximo que ha tributado ya, después de este cobro.
        base: Lo que tributa esta semana.
        tax: La cuota.
    """

    entity: str
    profit: int
    peak: int
    base: int
    tax: int


def rate_of(entity: str) -> float:
    """El tipo de cada cuenta."""
    return CASINO_RATE if entity == CASINO else BANK_RATE


def house_charge(entity: str, profit: int, peak: int) -> HouseCharge:
    """Lo que paga `entity` con su beneficio acumulado y el máximo que ya tributó.

    Solo tributa lo que pasa del máximo: una semana de pérdidas no devuelve nada, y hasta
    recuperarlas no se vuelve a pagar (art. 26 LIS). Con `peak = 0` (el primer lunes)
    tributa todo lo ganado desde el primer día.
    """
    base = max(0, profit - max(0, peak))
    return HouseCharge(
        entity=entity,
        profit=profit,
        peak=max(peak, profit, 0),
        base=base,
        tax=round(base * rate_of(entity)),
    )


#: Clave del Estado en `NAMES` (no paga impuesto de la casa: no está en `TAX_NAMES`).
STATE = "estado"
#: Nombre de cada cuenta en `fortunas` y `hacienda`.
NAMES = {CASINO: "🎰 El Casino", BANK: "🏦 El Banco", STATE: "🏛️ El Estado"}
#: Impuesto de cada cuenta, en el anuncio del lunes.
TAX_NAMES = {CASINO: "tasa del juego", BANK: "Impuesto sobre Sociedades"}
