"""Pruebas del 🎰 Casino y el 🏦 Banco como fortunas (`bot.services.house_books`).

Cubren las cuentas (lo que gana la casa, la caja y el beneficio del banco), el impuesto de
cada lunes con el historial entero y la compensación de pérdidas, que el libro del banco
cuadra con las tablas de préstamos e hipotecas, que el cobro entra al Estado una sola vez
por semana y que todo motivo del libro con pinta de banco está clasificado.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.cogs import patrimonio as patrimonio_cog
from bot.cogs.patrimonio import Patrimonio
from bot.repositories.economy import (
    BANK_ACCOUNT_ID,
    CASINO_ACCOUNT_ID,
    STATE_ACCOUNT_ID,
    LedgerEntry,
)
from bot.services.achievements import hacienda_house_stats, house_rivals_stats
from bot.services.house_books import (
    BANK,
    BANK_RATE,
    BANK_REASONS,
    CASINO,
    CASINO_RATE,
    NOT_BANK,
    BankBook,
    casino_book,
    house_charge,
)
from tests.unit.test_loans import ANA, BAJO, GUILD, Clock, make, salary, sign
from tests.unit.test_patrimonio_hacienda import ANA as P_ANA
from tests.unit.test_patrimonio_hacienda import GUILD as P_GUILD
from tests.unit.test_patrimonio_hacienda import guild_of, member, stores, texts

SRC = Path(__file__).resolve().parents[2] / "src" / "bot"
MONDAY = date(2026, 10, 5)
NEXT_MONDAY = date(2026, 10, 12)


# -- Cuentas ---------------------------------------------------------------------------


def test_el_casino_gana_lo_apostado_menos_los_premios_y_los_impuestos() -> None:
    rows = [("ruleta:apuesta", 3, -900), ("ruleta:premio", 1, 500), ("slots:bote", 1, 100)]
    book = casino_book(rows, taxes=50)
    assert (book.staked, book.paid, book.plays) == (900, 600, 3)
    assert book.profit == 300 and book.total == 250


def test_el_banco_es_su_caja_mas_lo_que_le_deben() -> None:
    # Prestó 1.000 en una hipoteca de compra, cobró 120 de cuotas y le quedan 900.
    book = BankBook(flow=120, purchase_lent=1_000, owed=900, taxes=6)
    assert book.cash == -880
    assert book.profit == 20 and book.total == 14


def test_el_primer_lunes_paga_todo_lo_ganado_desde_el_primer_dia() -> None:
    charge = house_charge(CASINO, 10_000, 0)
    assert (charge.base, charge.tax, charge.peak) == (10_000, round(10_000 * CASINO_RATE), 10_000)
    bank = house_charge(BANK, 1_000, 0)
    assert bank.tax == round(1_000 * BANK_RATE)


def test_las_semanas_en_perdidas_se_compensan_despues() -> None:
    down = house_charge(CASINO, 7_000, 10_000)
    assert (down.base, down.tax, down.peak) == (0, 0, 10_000)
    back = house_charge(CASINO, 12_000, down.peak)
    assert (back.base, back.peak) == (2_000, 12_000)
    red = house_charge(CASINO, -500, 0)
    assert (red.base, red.tax, red.peak) == (0, 0, 0)


# -- Logros ----------------------------------------------------------------------------


def test_ganar_al_casino_y_al_banco() -> None:
    both = house_rivals_stats(mine=1_000, casino=900, bank=800, debts=0)
    assert both.add == {"richer_than_casino": 1, "richer_than_bank": 1, "richer_than_both": 1}
    close = house_rivals_stats(mine=960, casino=1_000, bank=5_000, debts=0)
    assert close.add == {"casino_close": 1}
    red = house_rivals_stats(mine=0, casino=-10, bank=100, debts=200)
    assert red.add == {"casino_in_red": 1, "bank_hostage": 1}
    # Un casino en rojos no se «gana»: no tiene mérito.
    assert "richer_than_casino" not in red.add
    assert hacienda_house_stats(paid=10, casino=5, bank=50).add == {"hacienda_over_casino": 1}
    assert hacienda_house_stats(paid=10, casino=0, bank=0).add == {}


# -- Con la base de datos --------------------------------------------------------------


def state_balance(tmp_path: Path) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        row = connection.execute(
            "SELECT balance FROM economy_wallets WHERE guild_id = ? AND user_id = ?",
            (GUILD, STATE_ACCOUNT_ID),
        ).fetchone()
    return int(row[0]) if row else 0


async def books(service, economy):  # noqa: ANN001, ANN201
    lent, owed = await service.repository.bank_exposure(GUILD)
    return await economy.house_books(GUILD, purchase_lent=lent, owed=owed)


async def test_el_libro_del_banco_cuadra_con_sus_prestamos(tmp_path: Path) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, 6_000, clock)
    signed, _ = await sign(service, 20_000, months=24, insured=True)
    _casino, bank = await books(service, economy)
    # Recién firmado, el banco solo ha ganado la comisión.
    assert bank.profit == signed.quote.fee
    for d in (2, 3, 4):
        clock.to(d)
        await service.close_days(GUILD)
    book = await service.repository.bank_book(GUILD)
    _casino, bank = await books(service, economy)
    assert bank.profit == book.loan_fees + book.loan_interest + book.premiums
    (loan,) = (await service.market(GUILD)).loans_of(ANA)
    closed, _ = await service.repay_loan(GUILD, ANA, loan.id)
    _casino, after = await books(service, economy)
    assert after.owed == 0
    assert after.profit == bank.profit + closed.fee


async def test_una_hipoteca_de_compra_le_da_al_banco_sus_intereses(tmp_path: Path) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)
    salary(tmp_path, 9_000, clock)
    await economy.repository.apply(GUILD, ANA, [LedgerEntry(5_000_000, "prueba")])
    await service.buy(GUILD, ANA, BAJO.key, 100, financed=True)
    _casino, bank = await books(service, economy)
    assert bank.profit == 0 and bank.owed > 0
    for d in (2, 3):
        clock.to(d)
        await service.close_days(GUILD)
    (mortgage,) = (await service.market(GUILD)).mortgages
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (paid,) = connection.execute(
            "SELECT -SUM(delta) FROM economy_ledger WHERE reason = 'pisos:hipoteca'"
        ).fetchone()
    _casino, bank = await books(service, economy)
    # Lo cobrado en cuotas menos el capital devuelto: los intereses.
    assert bank.profit == paid - (mortgage.principal - mortgage.balance) > 0


async def test_el_lunes_cobra_una_vez_y_lo_ingresa_al_estado(tmp_path: Path) -> None:
    clock = Clock(1)
    economy, service = await make(tmp_path, clock)
    await economy.settle_bet(GUILD, ANA, game="ruleta", stake=1_000, payout=0)
    lent, owed = await service.repository.bank_exposure(GUILD)

    async def charge(week: date):  # noqa: ANN202
        return await economy.charge_house_taxes(GUILD, week=week, purchase_lent=lent, owed=owed)

    before = state_balance(tmp_path)
    # La primera vez solo se apunta la semana: nadie paga al desplegar.
    assert await charge(MONDAY) == []
    assert state_balance(tmp_path) == before
    paid = await charge(NEXT_MONDAY)
    by_entity = {c.entity: c for c in paid}
    assert by_entity[CASINO].tax == round(1_000 * CASINO_RATE)
    assert by_entity[BANK].tax == 0
    assert state_balance(tmp_path) == before + by_entity[CASINO].tax
    # La misma semana no se cobra dos veces.
    assert await charge(NEXT_MONDAY) == []
    assert state_balance(tmp_path) == before + by_entity[CASINO].tax
    casino, _bank = await economy.house_books(GUILD, purchase_lent=lent, owed=owed)
    assert casino.taxes == by_entity[CASINO].tax and casino.total == 1_000 - casino.taxes
    breakdown = await economy.house_tax_breakdown(GUILD)
    assert breakdown == {CASINO_ACCOUNT_ID: {"tasa_juego": by_entity[CASINO].tax}}
    assert BANK_ACCOUNT_ID not in breakdown
    treasury = await economy.treasury(GUILD, since=0)
    assert treasury.collected_total >= by_entity[CASINO].tax


# -- Motivos del libro -----------------------------------------------------------------


def test_todo_motivo_del_banco_esta_clasificado() -> None:
    """Un motivo nuevo de `banco:`, `negocio:prestamo:` o de la hipoteca tiene que ir en
    `BANK_PAYOUTS`, `BANK_RECEIPTS` o `NOT_BANK`; si no, la fortuna del banco descuadra."""
    found: set[str] = set()
    for path in [*(SRC / "repositories").glob("*.py"), *(SRC / "services").glob("*.py")]:
        text = path.read_text(encoding="utf-8")
        found |= set(re.findall(r'"((?:banco:|pisos:hipoteca|pisos:deuda)[a-z_:]*)"', text))
        found |= {
            f"negocio:prestamo:{tail}"
            for tail in re.findall(r"\{BANK_LOAN_CONCEPT\}([a-z_]+)", text)
        }
    # Los prefijos sueltos (`tag.startswith("banco:")`) no son motivos.
    found = {reason for reason in found if not reason.endswith(":")}
    unknown = found - set(BANK_REASONS) - set(NOT_BANK)
    assert not unknown, f"Motivos del banco sin clasificar: {sorted(unknown)}"


# -- `fortunas` ------------------------------------------------------------------------


async def test_fortunas_pone_al_casino_en_su_puesto(tmp_path: Path) -> None:
    economy, shop, lottery = await stores(tmp_path)
    balance = await economy.balance(P_GUILD, P_ANA)
    # Ana se lo deja casi todo en la ruleta: el casino pasa a ser más rico que ella.
    await economy.settle_bet(P_GUILD, P_ANA, game="ruleta", stake=balance - 100, payout=0)
    cog = Patrimonio(MagicMock(), economy, shop=shop, lottery=lottery)
    ana = member(P_ANA, "Ana")
    track = AsyncMock()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(patrimonio_cog.logros, "track", track)
        panel = await cog.fortunas_panel(guild_of(), ana)
        await cog._fortunas_logros(ana, None, panel.fortunes)
    shown = "\n".join(texts(panel))
    assert "🥇 **🎰 El Casino**" in shown
    assert f"🥈 📍 m{P_ANA}" in shown
    assert "📍 Tú: puesto 2 de 2" in shown
    assert f"🎰 El Casino: te faltan {balance - 200:,} Y$".replace(",", ".") in shown
    # El banco no ha ganado nada: no sale en la lista, pero sí en la línea de rivales.
    assert "🏦 El Banco: en números rojos" in shown
    # La cabecera solo suma a los miembros.
    assert "1 con patrimonio y la banca" in shown
    assert not any("richer_than_casino" in c.args[4].add for c in track.await_args_list)
