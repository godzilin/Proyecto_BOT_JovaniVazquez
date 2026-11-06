"""Persistencia de lo que no es el mercado en `pisos`: hipotecas, VPO, vivienda habitual y más.

Funciones sueltas que reciben la conexión: corren dentro de la transacción que abre
`bot.repositories.housing` (y esta, dentro de la de `EconomyRepository.housing` cuando hay
dinero), así que lo de aquí y lo del mercado se guarda junto o no se guarda.

Tablas (en el mismo archivo SQLite que el resto del bot):

- `housing_mortgages`: cada hipoteca (de un inmueble del mercado, `listing`, o de una
  VPO, `vpo:<clave>`), con lo pendiente, el tipo fijo al firmar, la cuota y lo que se
  debe de cuotas sin pagar. `kind` dice si se firmó al comprar (`compra`) o sobre algo que
  ya se tenía (`rehipoteca`, 9.1.0): los intereses de esta no se deducen de la renta.
- `housing_euribor`: el Euríbor simulado de cada lunes.
- `housing_bank_debts`: lo que se sigue debiendo al banco después de una ejecución
  hipotecaria (no hay dación en pago) o del vencimiento de un préstamo.
- `housing_loans`: los préstamos de `banco` (reglas en `bot.services.loans`), con lo
  pendiente, el tipo fijo, la cuota, la prima del seguro, lo que se debe de cuotas sin
  pagar y lo cobrado. `kind` dice si es personal, un anticipo de rentas (`rentas`, con su
  inmueble en `listing`) o un préstamo al negocio de `pala` (`negocio` si es de un autónomo,
  `sociedad` si lo firma su sociedad, con el id del negocio en `listing`), y `aval`, quién lo
  avala (`docs/garantias.md`).
- `housing_vpo`: de quién es cada VPO y lo que debe de recibos.
- `housing_vpo_signups`, `housing_vpo_reservations` y `housing_vpo_draws`: quién está
  apuntado al sorteo, a quién le ha tocado y qué lunes se sorteó.
- `housing_homes`: dónde vive cada miembro (su piso, su VPO o el que alquila a otro
  miembro). Es su vivienda habitual.
- `housing_offers`: las ofertas del fondo buitre (fase 4).
- `housing_socimi`: quién ha montado su SOCIMI (fase 4).
- `housing_promotions`: la obra nueva de cada servidor (fase 5): las promociones que
  levanta cada lunes quien está en la rama Ladrillo de `pala`, una por barrio. Son
  inmuebles como los del catálogo, pero solo de ese servidor.
- `housing_member_debts`: lo que un miembro debe a otro por haberle avalado: el avalista
  al que reclamó el banco tiene la acción de reembolso contra el avalado (art. 1838 CC). Se
  cobra cada noche del monedero del deudor y se embarga de lo que cobre (9.3.0).
- `housing_deposits`: la fianza (y la garantía adicional) que deja un miembro al
  alquilar el piso de otro, mientras espera en el depósito de fianzas, y lo que se
  queda el casero si lo desahucian debiendo meses (`forfeit`).

Solo IDs de miembros, claves del catálogo, importes y fechas.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from bot.services import housing as rules
from bot.services.housing_catalog import Listing

#: Tablas de este módulo, para crearlas y para borrarlas al salir de un servidor.
TABLES = (
    "housing_mortgages",
    "housing_euribor",
    "housing_bank_debts",
    "housing_vpo",
    "housing_vpo_signups",
    "housing_vpo_reservations",
    "housing_vpo_draws",
    "housing_homes",
    "housing_supply_cuts",
    "housing_offers",
    "housing_socimi",
    "housing_deposits",
    "housing_promotions",
    "housing_loans",
    "housing_member_debts",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS housing_mortgages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    listing TEXT NOT NULL,
    principal INTEGER NOT NULL CHECK (principal > 0),
    balance INTEGER NOT NULL CHECK (balance >= 0),
    rate REAL NOT NULL,
    payment INTEGER NOT NULL CHECK (payment >= 0),
    months_left INTEGER NOT NULL,
    unpaid_count INTEGER NOT NULL DEFAULT 0,
    unpaid_amount INTEGER NOT NULL DEFAULT 0 CHECK (unpaid_amount >= 0),
    aval TEXT,
    status TEXT NOT NULL DEFAULT 'activa',
    created_at REAL NOT NULL,
    kind TEXT NOT NULL DEFAULT 'compra'
);

CREATE INDEX IF NOT EXISTS housing_mortgages_member
    ON housing_mortgages (guild_id, user_id, status);

CREATE TABLE IF NOT EXISTS housing_euribor (
    guild_id INTEGER NOT NULL,
    week TEXT NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY (guild_id, week)
);

CREATE TABLE IF NOT EXISTS housing_bank_debts (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL CHECK (amount >= 0),
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS housing_vpo (
    guild_id INTEGER NOT NULL,
    unit TEXT NOT NULL,
    owner_id INTEGER NOT NULL,
    paid INTEGER NOT NULL,
    since REAL NOT NULL,
    arrears INTEGER NOT NULL DEFAULT 0 CHECK (arrears >= 0),
    arrears_ibi INTEGER NOT NULL DEFAULT 0 CHECK (arrears_ibi >= 0),
    PRIMARY KEY (guild_id, unit)
);

CREATE TABLE IF NOT EXISTS housing_vpo_signups (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    since REAL NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS housing_vpo_reservations (
    guild_id INTEGER NOT NULL,
    unit TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    until TEXT NOT NULL,
    PRIMARY KEY (guild_id, unit)
);

CREATE TABLE IF NOT EXISTS housing_vpo_draws (
    guild_id INTEGER NOT NULL,
    week TEXT NOT NULL,
    PRIMARY KEY (guild_id, week)
);

CREATE TABLE IF NOT EXISTS housing_homes (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    ref TEXT NOT NULL,
    since REAL NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

-- `casa`: hasta qué día (incluido) está sin luz quien no pudo pagar los suministros.
CREATE TABLE IF NOT EXISTS housing_supply_cuts (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    until TEXT NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS housing_offers (
    guild_id INTEGER NOT NULL,
    listing TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    price INTEGER NOT NULL,
    until TEXT NOT NULL,
    PRIMARY KEY (guild_id, listing, user_id)
);

CREATE TABLE IF NOT EXISTS housing_socimi (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    since REAL NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS housing_promotions (
    guild_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    barrio TEXT NOT NULL,
    kind TEXT NOT NULL,
    m2 INTEGER NOT NULL,
    title TEXT NOT NULL,
    blurb TEXT NOT NULL,
    floor TEXT NOT NULL,
    energy TEXT NOT NULL,
    promoter_id INTEGER NOT NULL,
    week TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (guild_id, key)
);

CREATE TABLE IF NOT EXISTS housing_loans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    principal INTEGER NOT NULL CHECK (principal > 0),
    balance INTEGER NOT NULL CHECK (balance >= 0),
    rate REAL NOT NULL,
    payment INTEGER NOT NULL CHECK (payment >= 0),
    months INTEGER NOT NULL,
    months_left INTEGER NOT NULL,
    fee INTEGER NOT NULL DEFAULT 0,
    premium INTEGER NOT NULL DEFAULT 0,
    premium_tax INTEGER NOT NULL DEFAULT 0,
    covers_left INTEGER NOT NULL DEFAULT 0,
    unpaid_count INTEGER NOT NULL DEFAULT 0,
    unpaid_amount INTEGER NOT NULL DEFAULT 0 CHECK (unpaid_amount >= 0),
    paid INTEGER NOT NULL DEFAULT 0,
    interest_paid INTEGER NOT NULL DEFAULT 0,
    first_day TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'activo'
        CHECK (status IN ('activo', 'pagado', 'desistido', 'vencido')),
    created_at REAL NOT NULL,
    closed_at REAL,
    kind TEXT NOT NULL DEFAULT 'personal',
    listing TEXT,
    aval TEXT
);

CREATE INDEX IF NOT EXISTS housing_loans_member
    ON housing_loans (guild_id, user_id, status);

CREATE TABLE IF NOT EXISTS housing_member_debts (
    guild_id INTEGER NOT NULL,
    creditor_id INTEGER NOT NULL,
    debtor_id INTEGER NOT NULL,
    amount INTEGER NOT NULL CHECK (amount >= 0),
    since REAL NOT NULL,
    PRIMARY KEY (guild_id, creditor_id, debtor_id)
);

CREATE TABLE IF NOT EXISTS housing_deposits (
    guild_id INTEGER NOT NULL,
    listing TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL CHECK (amount >= 0),
    forfeit INTEGER NOT NULL DEFAULT 0 CHECK (forfeit >= 0),
    since REAL NOT NULL,
    PRIMARY KEY (guild_id, listing, user_id)
);
"""

#: Viviendas habituales: el piso propio, la VPO o el piso que se alquila a otro miembro.
HOME_OWN = "propia"
HOME_VPO = "vpo"
HOME_RENTED = "alquiler"


#: Hipoteca firmada al comprar el inmueble (o la VPO).
MORTGAGE_PURCHASE = "compra"
#: Hipoteca nueva sobre algo que ya se tenía (`banco`, 🔐 Garantías).
MORTGAGE_REMORTGAGE = "rehipoteca"
#: Préstamo personal de `banco` (🏦 Préstamos).
LOAN_PERSONAL = "personal"
#: Anticipo de las rentas de un inmueble (`banco`, 💸 Anticipos): `listing` dice cuál.
LOAN_ADVANCE = "rentas"
#: Préstamo al negocio de un autónomo (`banco`, 🏪 Negocio): `listing` es el id del negocio.
#: Lo cobra cada noche el negocio, no `pisos`.
LOAN_BUSINESS = "negocio"
#: El mismo, firmado por su sociedad limitada (o pasado a ella al constituirla), con el aval
#: de su socio: es deuda de la sociedad, no de su dueño (`user_id`).
LOAN_COMPANY = "sociedad"
#: Financiación de una compra (💳 Financiar, `bot.services.credit`): `listing` dice qué se
#: compró. Las de Paga en 3 van aparte porque llevan recargo si no se pagan.
LOAN_PURCHASE = "compra"
LOAN_BNPL = "plazos"
#: Columnas añadidas después de crear las tablas: `(tabla, columna, DDL)`, para las bases de
#: datos de antes (las migra `HousingRepository.initialize`).
LATER_COLUMNS = (
    ("housing_mortgages", "kind", f"kind TEXT NOT NULL DEFAULT '{MORTGAGE_PURCHASE}'"),
    ("housing_loans", "kind", "kind TEXT NOT NULL DEFAULT 'personal'"),
    ("housing_loans", "listing", "listing TEXT"),
    ("housing_loans", "aval", "aval TEXT"),
)


def vpo_ref(unit: str) -> str:
    """Clave de una VPO donde se guarda como inmueble (hipotecas, noticias)."""
    return f"vpo:{unit}"


@dataclass(frozen=True, slots=True)
class Mortgage:
    """Una hipoteca.

    Attributes:
        listing: El inmueble (o `vpo:<clave>`).
        principal: Lo prestado.
        balance: Lo que queda por devolver.
        rate: Tipo anual fijo.
        payment: Cuota de cada mes (un día del bot).
        months_left: Cuotas que quedan.
        unpaid_count: Cuotas seguidas sin pagar del todo.
        unpaid_amount: Lo que se debe de cuotas sin pagar.
        aval: `ico` si la avala el Estado (VPO), `padres` o `None`.
        kind: `compra` o `rehipoteca`.
    """

    id: int
    user_id: int
    listing: str
    principal: int
    balance: int
    rate: float
    payment: int
    months_left: int
    unpaid_count: int
    unpaid_amount: int
    aval: str | None
    kind: str = MORTGAGE_PURCHASE

    @property
    def owed(self) -> int:
        """Todo lo que hay que devolver para cancelarla."""
        return self.balance + self.unpaid_amount

    @property
    def remortgage(self) -> bool:
        """Si se firmó sobre algo que ya se tenía: no compró nada (IRPF y Patrimonio)."""
        return self.kind == MORTGAGE_REMORTGAGE


def _mortgage(row: sqlite3.Row) -> Mortgage:
    return Mortgage(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        listing=str(row["listing"]),
        principal=int(row["principal"]),
        balance=int(row["balance"]),
        rate=float(row["rate"]),
        payment=int(row["payment"]),
        months_left=int(row["months_left"]),
        unpaid_count=int(row["unpaid_count"]),
        unpaid_amount=int(row["unpaid_amount"]),
        aval=row["aval"],
        kind=str(row["kind"]),
    )


def mortgages_in(
    connection: sqlite3.Connection, guild_id: int, user_id: int | None = None
) -> list[Mortgage]:
    """Hipotecas activas del servidor (o de un miembro), de la más antigua a la más nueva."""
    if user_id is None:
        rows = connection.execute(
            "SELECT * FROM housing_mortgages WHERE guild_id = ? AND status = 'activa' ORDER BY id",
            (guild_id,),
        )
    else:
        rows = connection.execute(
            "SELECT * FROM housing_mortgages WHERE guild_id = ? AND user_id = ? "
            "AND status = 'activa' ORDER BY id",
            (guild_id, user_id),
        )
    return [_mortgage(r) for r in rows]


def add_mortgage_in(
    connection: sqlite3.Connection,
    guild_id: int,
    user_id: int,
    listing: str,
    principal: int,
    rate: float,
    payment: int,
    now: float,
    aval: str | None = None,
    *,
    months: int = rules.MORTGAGE_MONTHS,
    kind: str = MORTGAGE_PURCHASE,
) -> int:
    """Firma una hipoteca y devuelve su id."""
    cursor = connection.execute(
        """
        INSERT INTO housing_mortgages
            (guild_id, user_id, listing, principal, balance, rate, payment, months_left, aval,
             created_at, kind)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (guild_id, user_id, listing, principal, principal, rate, payment, months, aval, now,
         kind),
    )  # fmt: skip
    return int(cursor.lastrowid or 0)


def save_mortgage_in(
    connection: sqlite3.Connection,
    mortgage: Mortgage,
    *,
    balance: int,
    payment: int,
    months_left: int,
    unpaid_count: int,
    unpaid_amount: int,
    status: str = "activa",
) -> None:
    """Guarda cómo queda una hipoteca."""
    connection.execute(
        """
        UPDATE housing_mortgages SET balance = ?, payment = ?, months_left = ?,
            unpaid_count = ?, unpaid_amount = ?, status = ?
        WHERE id = ?
        """,
        (balance, payment, months_left, unpaid_count, unpaid_amount, status, mortgage.id),
    )


def close_mortgage_in(connection: sqlite3.Connection, mortgage_id: int, status: str) -> None:
    """Cierra una hipoteca (`pagada`, `cancelada` al vender o `ejecutada`)."""
    connection.execute(
        "UPDATE housing_mortgages SET balance = 0, unpaid_amount = 0, status = ? WHERE id = ?",
        (status, mortgage_id),
    )


def euribor_in(connection: sqlite3.Connection, guild_id: int) -> float:
    """El Euríbor de esta semana (el de partida si aún no se ha movido)."""
    row = connection.execute(
        "SELECT value FROM housing_euribor WHERE guild_id = ? ORDER BY week DESC LIMIT 1",
        (guild_id,),
    ).fetchone()
    return float(row["value"]) if row else rules.EURIBOR_START


def set_euribor_in(connection: sqlite3.Connection, guild_id: int, week: date, value: float) -> bool:
    """Apunta el Euríbor de una semana. `False` si ya estaba."""
    cursor = connection.execute(
        "INSERT OR IGNORE INTO housing_euribor (guild_id, week, value) VALUES (?, ?, ?)",
        (guild_id, week.isoformat(), value),
    )
    return cursor.rowcount > 0


def member_debts_in(connection: sqlite3.Connection, guild_id: int) -> dict[tuple[int, int], int]:
    """`{(acreedor, deudor): importe}` de lo que se deben los miembros por los avales."""
    return {
        (int(r["creditor_id"]), int(r["debtor_id"])): int(r["amount"])
        for r in connection.execute(
            "SELECT creditor_id, debtor_id, amount FROM housing_member_debts "
            "WHERE guild_id = ? AND amount > 0",
            (guild_id,),
        )
    }


def add_member_debt_in(
    connection: sqlite3.Connection,
    guild_id: int,
    creditor: int,
    debtor: int,
    amount: int,
    now: float,
) -> None:
    """Suma a lo que `debtor` debe a `creditor` (el avalista que pagó por él)."""
    if amount <= 0:
        return
    connection.execute(
        """
        INSERT INTO housing_member_debts (guild_id, creditor_id, debtor_id, amount, since)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (guild_id, creditor_id, debtor_id) DO UPDATE SET
            amount = amount + excluded.amount
        """,
        (guild_id, creditor, debtor, amount, now),
    )


def set_member_debt_in(
    connection: sqlite3.Connection, guild_id: int, creditor: int, debtor: int, amount: int
) -> None:
    """Deja lo que se debe en `amount` (y borra la fila si ya no se debe nada)."""
    if amount > 0:
        connection.execute(
            "UPDATE housing_member_debts SET amount = ? "
            "WHERE guild_id = ? AND creditor_id = ? AND debtor_id = ?",
            (amount, guild_id, creditor, debtor),
        )
    else:
        connection.execute(
            "DELETE FROM housing_member_debts "
            "WHERE guild_id = ? AND creditor_id = ? AND debtor_id = ?",
            (guild_id, creditor, debtor),
        )


def bank_debts_in(connection: sqlite3.Connection, guild_id: int) -> dict[int, int]:
    """Lo que debe cada miembro al banco tras una ejecución."""
    return {
        int(r["user_id"]): int(r["amount"])
        for r in connection.execute(
            "SELECT user_id, amount FROM housing_bank_debts WHERE guild_id = ? AND amount > 0",
            (guild_id,),
        )
    }


def add_bank_debt_in(
    connection: sqlite3.Connection, guild_id: int, user_id: int, amount: int
) -> None:
    """Suma (o resta, con negativo) a la deuda con el banco, sin bajar de 0."""
    connection.execute(
        """
        INSERT INTO housing_bank_debts (guild_id, user_id, amount) VALUES (?, ?, MAX(0, ?))
        ON CONFLICT (guild_id, user_id) DO UPDATE SET amount = MAX(0, amount + excluded.amount)
        """,
        (guild_id, user_id, amount),
    )


# -- Préstamos personales ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Loan:
    """Un préstamo personal del banco.

    Attributes:
        principal: Lo prestado.
        balance: Lo que queda por devolver.
        rate: TIN anual fijo.
        payment: Cuota de cada mes (un día del bot), sin seguro.
        months: Plazo al firmar.
        months_left: Cuotas que quedan.
        fee: Comisión de apertura cobrada al firmar (vuelve si se desiste).
        premium: Prima del seguro con cada cuota (0 sin seguro).
        premium_tax: Su impuesto (IPS).
        covers_left: Cuotas que aún puede pagar el seguro si no llegas.
        unpaid_count: Cuotas seguidas sin pagar del todo.
        unpaid_amount: Lo que se debe de cuotas sin pagar.
        paid: Lo cobrado hasta hoy (cuotas, primas e impuesto).
        interest_paid: Lo que de eso fueron intereses.
        first_day: Primer cierre de `pisos` que cobra cuota (ISO); hasta entonces se puede
            desistir.
        status: `activo`, `pagado`, `desistido` o `vencido`.
        kind: `personal`, `rentas` (anticipo de las rentas de `listing`), `negocio` o
            `sociedad` (préstamo al negocio `listing`), `compra` o `plazos` (financiación
            de la compra `listing`).
        listing: El inmueble de un anticipo de rentas, el id del negocio o lo comprado.
        aval: Quién lo avala (`miembro:<id>`), o `None`.
    """

    id: int
    user_id: int
    principal: int
    balance: int
    rate: float
    payment: int
    months: int
    months_left: int
    fee: int
    premium: int
    premium_tax: int
    covers_left: int
    unpaid_count: int
    unpaid_amount: int
    paid: int
    interest_paid: int
    first_day: str
    status: str
    created_at: float
    kind: str = LOAN_PERSONAL
    listing: str | None = None
    aval: str | None = None

    @property
    def owed(self) -> int:
        """Todo lo que hay que devolver para cancelarlo (sin la compensación)."""
        return self.balance + self.unpaid_amount

    @property
    def monthly(self) -> int:
        """Lo que sale cada mes con cuota: cuota, prima e impuesto."""
        return self.payment + self.premium + self.premium_tax if self.months_left > 0 else 0

    @property
    def insured(self) -> bool:
        """Si lleva el seguro de protección de pagos."""
        return self.premium > 0

    @property
    def advance(self) -> bool:
        """Si es un anticipo de rentas (no cuenta en el tope de préstamos personales)."""
        return self.kind == LOAN_ADVANCE

    @property
    def purchase(self) -> bool:
        """Si financia una compra (💳 Financiar, también Paga en 3)."""
        return self.kind in (LOAN_PURCHASE, LOAN_BNPL)

    @property
    def business_id(self) -> int | None:
        """El negocio de un préstamo al negocio (`None` si no lo es)."""
        if self.kind in (LOAN_BUSINESS, LOAN_COMPANY) and self.listing:
            return int(self.listing)
        return None

    @property
    def corporate(self) -> bool:
        """Si lo debe una sociedad (su dueño solo lo avala)."""
        return self.kind == LOAN_COMPANY


def _loan(row: sqlite3.Row) -> Loan:
    return Loan(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        principal=int(row["principal"]),
        balance=int(row["balance"]),
        rate=float(row["rate"]),
        payment=int(row["payment"]),
        months=int(row["months"]),
        months_left=int(row["months_left"]),
        fee=int(row["fee"]),
        premium=int(row["premium"]),
        premium_tax=int(row["premium_tax"]),
        covers_left=int(row["covers_left"]),
        unpaid_count=int(row["unpaid_count"]),
        unpaid_amount=int(row["unpaid_amount"]),
        paid=int(row["paid"]),
        interest_paid=int(row["interest_paid"]),
        first_day=str(row["first_day"]),
        status=str(row["status"]),
        created_at=float(row["created_at"]),
        kind=str(row["kind"]),
        listing=row["listing"],
        aval=row["aval"],
    )


def loans_in(connection: sqlite3.Connection, guild_id: int) -> list[Loan]:
    """Préstamos activos del servidor, del más antiguo al más nuevo."""
    rows = connection.execute(
        "SELECT * FROM housing_loans WHERE guild_id = ? AND status = 'activo' ORDER BY id",
        (guild_id,),
    )
    return [_loan(r) for r in rows]


def business_loans_in(
    connection: sqlite3.Connection, guild_id: int, business_id: int
) -> list[Loan]:
    """Préstamos activos de un negocio, del más antiguo al más nuevo."""
    rows = connection.execute(
        "SELECT * FROM housing_loans WHERE guild_id = ? AND status = 'activo' "
        "AND kind IN (?, ?) AND listing = ? ORDER BY id",
        (guild_id, LOAN_BUSINESS, LOAN_COMPANY, str(business_id)),
    )
    return [_loan(r) for r in rows]


def loan_in(connection: sqlite3.Connection, guild_id: int, loan_id: int) -> Loan | None:
    """Un préstamo cualquiera del servidor, esté como esté."""
    row = connection.execute(
        "SELECT * FROM housing_loans WHERE guild_id = ? AND id = ?", (guild_id, loan_id)
    ).fetchone()
    return _loan(row) if row else None


def add_loan_in(
    connection: sqlite3.Connection,
    guild_id: int,
    user_id: int,
    *,
    principal: int,
    rate: float,
    payment: int,
    months: int,
    fee: int,
    premium: int,
    premium_tax: int,
    covers: int,
    first_day: str,
    now: float,
    kind: str = LOAN_PERSONAL,
    listing: str | None = None,
    balance: int | None = None,
) -> int:
    """Firma un préstamo (o un anticipo de las rentas de `listing`) y devuelve su id.

    Args:
        balance: Lo que se debe al empezar, si no es lo prestado (la carencia de una
            financiación suma los intereses de esos días).
    """
    cursor = connection.execute(
        """
        INSERT INTO housing_loans
            (guild_id, user_id, principal, balance, rate, payment, months, months_left, fee,
             premium, premium_tax, covers_left, first_day, created_at, kind, listing)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (guild_id, user_id, principal, principal if balance is None else balance, rate,
         payment, months, months, fee, premium, premium_tax, covers, first_day, now, kind,
         listing),
    )  # fmt: skip
    return int(cursor.lastrowid or 0)


def save_loan_in(
    connection: sqlite3.Connection,
    loan: Loan,
    *,
    balance: int,
    months_left: int,
    covers_left: int,
    unpaid_count: int,
    unpaid_amount: int,
    paid: int,
    interest_paid: int,
) -> None:
    """Guarda cómo queda un préstamo activo tras una noche."""
    connection.execute(
        """
        UPDATE housing_loans SET balance = ?, months_left = ?, covers_left = ?,
            unpaid_count = ?, unpaid_amount = ?, paid = ?, interest_paid = ?
        WHERE id = ?
        """,
        (balance, months_left, covers_left, unpaid_count, unpaid_amount, paid, interest_paid,
         loan.id),
    )  # fmt: skip


def close_loan_in(
    connection: sqlite3.Connection, loan_id: int, status: str, now: float, *, paid: int = 0
) -> None:
    """Cierra un préstamo (`pagado`, `desistido` o `vencido`), sumando lo cobrado al cerrar."""
    connection.execute(
        """
        UPDATE housing_loans SET balance = 0, unpaid_amount = 0, months_left = 0, status = ?,
            paid = paid + ?, closed_at = ?
        WHERE id = ?
        """,
        (status, paid, now, loan_id),
    )


def loan_history_in(connection: sqlite3.Connection, guild_id: int, user_id: int) -> tuple[int, int]:
    """`(préstamos devueltos, impagos)` de un miembro: lo que mira el banco de su historial.

    Los impagos son los préstamos vencidos y las hipotecas ejecutadas.
    """
    (paid,) = connection.execute(
        "SELECT COUNT(*) FROM housing_loans WHERE guild_id = ? AND user_id = ? "
        "AND status = 'pagado'",
        (guild_id, user_id),
    ).fetchone()
    (defaults,) = connection.execute(
        """
        SELECT (SELECT COUNT(*) FROM housing_loans
                WHERE guild_id = :g AND user_id = :u AND status = 'vencido')
             + (SELECT COUNT(*) FROM housing_mortgages
                WHERE guild_id = :g AND user_id = :u AND status = 'ejecutada')
        """,
        {"g": guild_id, "u": user_id},
    ).fetchone()
    return int(paid), int(defaults)


def bank_debt_in(connection: sqlite3.Connection, guild_id: int, user_id: int) -> int:
    """Lo que debe un miembro al banco (0 si nada)."""
    row = connection.execute(
        "SELECT amount FROM housing_bank_debts WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    ).fetchone()
    return int(row["amount"]) if row else 0


# -- Vivienda habitual ------------------------------------------------------------------


def homes_in(connection: sqlite3.Connection, guild_id: int) -> dict[int, tuple[str, str]]:
    """`{miembro: (tipo, referencia)}` de dónde vive cada uno."""
    return {
        int(r["user_id"]): (str(r["kind"]), str(r["ref"]))
        for r in connection.execute(
            "SELECT user_id, kind, ref FROM housing_homes WHERE guild_id = ?", (guild_id,)
        )
    }


def set_home_in(
    connection: sqlite3.Connection, guild_id: int, user_id: int, kind: str, ref: str, now: float
) -> None:
    """Apunta dónde vive alguien (sustituye lo que hubiera)."""
    connection.execute(
        """
        INSERT INTO housing_homes (guild_id, user_id, kind, ref, since) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (guild_id, user_id) DO UPDATE SET
            kind = excluded.kind, ref = excluded.ref, since = excluded.since
        """,
        (guild_id, user_id, kind, ref, now),
    )


def clear_home_in(
    connection: sqlite3.Connection, guild_id: int, *, user_id: int | None = None,
    ref: str | None = None,
) -> None:  # fmt: skip
    """Borra la vivienda habitual de un miembro o la de quien viva en `ref`."""
    if user_id is not None:
        connection.execute(
            "DELETE FROM housing_homes WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )
    if ref is not None:
        connection.execute(
            "DELETE FROM housing_homes WHERE guild_id = ? AND ref = ?", (guild_id, ref)
        )


def cut_supplies_in(
    connection: sqlite3.Connection, guild_id: int, user_id: int, until: date
) -> None:
    """Le corta la luz a alguien hasta `until` (incluido): su casa no da bufos (`casa`)."""
    connection.execute(
        """
        INSERT INTO housing_supply_cuts (guild_id, user_id, until) VALUES (?, ?, ?)
        ON CONFLICT (guild_id, user_id) DO UPDATE SET until = excluded.until
        """,
        (guild_id, user_id, until.isoformat()),
    )


def supplies_cut_in(connection: sqlite3.Connection, guild_id: int, user_id: int) -> date | None:
    """El último día sin luz de alguien (puede ser pasado), o `None` si nunca se la cortaron."""
    row = connection.execute(
        "SELECT until FROM housing_supply_cuts WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    ).fetchone()
    return date.fromisoformat(row[0]) if row is not None else None


# -- VPO --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VpoOwner:
    """De quién es una VPO y lo que debe de recibos."""

    unit: str
    owner_id: int
    paid: int
    since: float
    arrears: int
    arrears_ibi: int


def vpo_in(connection: sqlite3.Connection, guild_id: int) -> dict[str, VpoOwner]:
    """Las VPO que tienen dueño."""
    return {
        str(r["unit"]): VpoOwner(
            str(r["unit"]),
            int(r["owner_id"]),
            int(r["paid"]),
            float(r["since"]),
            int(r["arrears"]),
            int(r["arrears_ibi"]),
        )  # fmt: skip
        for r in connection.execute("SELECT * FROM housing_vpo WHERE guild_id = ?", (guild_id,))
    }


def vpo_signups_in(connection: sqlite3.Connection, guild_id: int) -> list[int]:
    """Quién está apuntado al sorteo, por orden de inscripción."""
    return [
        int(r["user_id"])
        for r in connection.execute(
            "SELECT user_id FROM housing_vpo_signups WHERE guild_id = ? ORDER BY since, user_id",
            (guild_id,),
        )
    ]


def vpo_reservations_in(
    connection: sqlite3.Connection, guild_id: int
) -> dict[str, tuple[int, date]]:
    """`{vpo: (a quién le tocó, último día para comprarla)}`."""
    return {
        str(r["unit"]): (int(r["user_id"]), date.fromisoformat(r["until"]))
        for r in connection.execute(
            "SELECT unit, user_id, until FROM housing_vpo_reservations WHERE guild_id = ?",
            (guild_id,),
        )
    }


# -- Fondo buitre y SOCIMI (fase 4) -----------------------------------------------------


def offers_in(
    connection: sqlite3.Connection, guild_id: int
) -> dict[tuple[str, int], tuple[int, date]]:
    """`{(inmueble, miembro): (precio, último día)}` de las ofertas del fondo."""
    return {
        (str(r["listing"]), int(r["user_id"])): (int(r["price"]), date.fromisoformat(r["until"]))
        for r in connection.execute(
            "SELECT listing, user_id, price, until FROM housing_offers WHERE guild_id = ?",
            (guild_id,),
        )
    }


def socimi_in(connection: sqlite3.Connection, guild_id: int) -> set[int]:
    """Quién tiene su SOCIMI."""
    return {
        int(r["user_id"])
        for r in connection.execute(
            "SELECT user_id FROM housing_socimi WHERE guild_id = ?", (guild_id,)
        )
    }


@dataclass(frozen=True, slots=True)
class Deposit:
    """La fianza de un miembro de alquiler en el depósito de fianzas.

    Attributes:
        amount: Lo que dejó (fianza y garantía adicional).
        forfeit: Lo que se queda el casero por los meses que debía al desahuciarlo.
    """

    listing: str
    user_id: int
    amount: int
    forfeit: int


def deposits_in(connection: sqlite3.Connection, guild_id: int) -> list[Deposit]:
    """Las fianzas que esperan en el depósito."""
    return [
        Deposit(str(r["listing"]), int(r["user_id"]), int(r["amount"]), int(r["forfeit"]))
        for r in connection.execute(
            "SELECT listing, user_id, amount, forfeit FROM housing_deposits WHERE guild_id = ?",
            (guild_id,),
        )
    ]


def add_deposit_in(
    connection: sqlite3.Connection, guild_id: int, listing: str, user_id: int, amount: int,
    now: float,
) -> None:  # fmt: skip
    """Apunta una fianza (se suma si quedaba otra del mismo piso sin devolver)."""
    connection.execute(
        """
        INSERT INTO housing_deposits (guild_id, listing, user_id, amount, since)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (guild_id, listing, user_id) DO UPDATE SET amount = amount + excluded.amount
        """,
        (guild_id, listing, user_id, amount, now),
    )


def forfeit_deposit_in(
    connection: sqlite3.Connection, guild_id: int, listing: str, user_id: int, owed: int
) -> None:
    """Marca lo que se queda el casero de una fianza (nunca más de lo que hay)."""
    connection.execute(
        """
        UPDATE housing_deposits SET forfeit = MIN(amount, forfeit + ?)
        WHERE guild_id = ? AND listing = ? AND user_id = ?
        """,
        (max(0, owed), guild_id, listing, user_id),
    )


def delete_deposit_in(
    connection: sqlite3.Connection, guild_id: int, listing: str, user_id: int
) -> None:
    """Borra una fianza ya devuelta."""
    connection.execute(
        "DELETE FROM housing_deposits WHERE guild_id = ? AND listing = ? AND user_id = ?",
        (guild_id, listing, user_id),
    )


@dataclass(frozen=True, slots=True)
class Promotion:
    """Una promoción de obra nueva de un servidor.

    Attributes:
        listing: El inmueble, como los del catálogo (clave `obra_<n>`).
        promoter: Quien la levantó desde la rama Ladrillo de `pala`: cobra la comisión del
            promotor de lo que se venda.
        week: El lunes en que salió.
    """

    listing: Listing
    promoter: int
    week: date


def promotions_in(connection: sqlite3.Connection, guild_id: int) -> dict[str, Promotion]:
    """La obra nueva del servidor, de la más vieja a la más nueva."""
    return {
        str(r["key"]): Promotion(
            Listing(
                str(r["key"]),
                str(r["barrio"]),
                str(r["kind"]),
                int(r["m2"]),
                str(r["title"]),
                str(r["blurb"]),
                str(r["floor"]),
                str(r["energy"]),
            ),
            int(r["promoter_id"]),
            date.fromisoformat(str(r["week"])),
        )  # fmt: skip
        for r in connection.execute(
            "SELECT * FROM housing_promotions WHERE guild_id = ? ORDER BY created_at, key",
            (guild_id,),
        )
    }


def add_promotion_in(
    connection: sqlite3.Connection, guild_id: int, promotion: Promotion, now: float
) -> None:
    """Apunta una promoción nueva."""
    listing = promotion.listing
    connection.execute(
        """
        INSERT INTO housing_promotions
            (guild_id, key, barrio, kind, m2, title, blurb, floor, energy, promoter_id, week,
             created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (guild_id, listing.key, listing.barrio, listing.kind, listing.m2, listing.title,
         listing.blurb, listing.floor, listing.energy, promotion.promoter,
         promotion.week.isoformat(), now),
    )  # fmt: skip
