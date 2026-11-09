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
from bot.services.universo_editor import (
    CASINO_MODEL_VALUE,
    CASINO_PIECE_VALUE,
    CASINO_SIGN_VALUE,
    CASINO_SLOT_VALUE,
    add_casino,
    casino_value,
)
from bot.services.work_catalog import JOB_BY_KEY
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
    # Con los depósitos el banco vale mucho más, pero deberle se mide con su beneficio.
    hostage = house_rivals_stats(mine=0, casino=0, bank=10_000, bank_profit=100, debts=200)
    assert hostage.add == {"bank_hostage": 1}
    assert house_rivals_stats(mine=500, casino=0, bank=900, state=400, debts=0).add == {
        "richer_than_state": 1
    }
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
    # El banco no ha ganado nada, pero custodia los depósitos: los 100 de Ana y la caja del
    # casino.
    assert "🥇 **🏦 El Banco**" in shown
    assert f"🏧 depósitos de todos {balance:,} Y$".replace(",", ".") in shown
    assert "🥈 **🎰 El Casino**" in shown
    assert f"🥉 📍 m{P_ANA}" in shown
    assert "📍 Tú: puesto 3 de 3" in shown
    assert f"🎰 El Casino: te faltan {balance - 200:,} Y$".replace(",", ".") in shown
    # El Estado no ha recaudado nada: no sale en la lista, pero sí en la línea de rivales.
    assert "🏛️ El Estado: en números rojos" in shown
    # La cabecera solo suma a los miembros.
    assert "1 con patrimonio, la banca y el Estado" in shown
    assert not any("richer_than_casino" in c.args[4].add for c in track.await_args_list)


# -- El Estado paga las nóminas públicas -------------------------------------------------


async def test_la_nomina_del_funcionario_sale_de_la_caja_del_estado(tmp_path: Path) -> None:
    economy, _shop, _lottery = await stores(tmp_path)
    await economy.repository.apply(P_GUILD, STATE_ACCOUNT_ID, [LedgerEntry(100_000, "prueba")])
    before = await economy.state_balance(P_GUILD)
    result = await economy.pay_salary(
        P_GUILD, P_ANA, gross=10_000, concept="pala:funcionariado", public=True
    )
    slip = result.payslip
    book = await economy.state_book(P_GUILD)
    # Paga el bruto y la cotización de la empresa, y se cobra a sí mismo los impuestos.
    assert book.payroll == book.payroll_week == slip.gross + slip.ss_employer
    assert book.self_taxes == slip.total_taxes > 0
    assert book.cash == before - slip.net
    # Una nómina privada no sale del Estado.
    await economy.pay_salary(P_GUILD, P_ANA, gross=10_000, concept="pala:obra")
    assert (await economy.state_book(P_GUILD)).payroll == book.payroll


async def test_un_estado_sin_caja_paga_lo_que_tiene(tmp_path: Path) -> None:
    economy, _shop, _lottery = await stores(tmp_path)
    await economy.repository.apply(P_GUILD, STATE_ACCOUNT_ID, [LedgerEntry(1_000, "prueba")])
    before = await economy.state_balance(P_GUILD)
    assert 0 < before < 10_000
    result = await economy.pay_salary(
        P_GUILD, P_ANA, gross=10_000, concept="pala:funcionariado", public=True
    )
    book = await economy.state_book(P_GUILD)
    # Lo que falta para el bruto sale de la nada, como antes.
    assert book.payroll == before
    assert book.cash == result.payslip.total_taxes


def test_solo_el_funcionariado_con_plaza_lo_paga_el_estado() -> None:
    job = JOB_BY_KEY["funcionariado"]
    assert job.paid_by_state(job.positions[0])
    giratoria = job.branch("giratoria")
    assert giratoria is not None and not job.paid_by_state(giratoria.positions[0])
    assert not JOB_BY_KEY["obra"].paid_by_state(JOB_BY_KEY["obra"].positions[0])


# -- El casino del Jovaniverso -----------------------------------------------------------


def test_el_casino_vale_mas_cuanto_mas_se_construye() -> None:
    world = add_casino({})
    base = casino_value(world)
    # Seis tragaperras, el letrero del bote y el toldo (una malla).
    assert base == 6 * CASINO_SLOT_VALUE + CASINO_SIGN_VALUE + CASINO_MODEL_VALUE
    wall = {"nombre": "pared", "tipo": "piezas", "piezas": [{}, {}, {}], "colision": None}
    near = {"asset": "pared", "pos": [-8.0, 8, 75.0], "rot": [0, 0, 0], "esc": [1, 1, 1]}
    far = {**near, "pos": [200.0, 8, 200.0]}
    world = {**world, "a/pared": wall, "c/9/9": {"objetos": [near, far]}}
    assert casino_value(world) == base + 3 * CASINO_PIECE_VALUE
    # Sin tragaperras no hay casino.
    assert casino_value({"a/pared": wall, "c/9/9": {"objetos": [near]}}) == 0
