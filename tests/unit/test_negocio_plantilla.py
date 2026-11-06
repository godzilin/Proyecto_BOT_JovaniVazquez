"""Pruebas de la plantilla de los negocios (fase 4): `bot.services.business_staff` y los casos
de uso de `BusinessService` con la base de datos de verdad.

Cubren que el empleado juega con el mismo minijuego y siempre igual, que la nómina de cada
noche cuadra al céntimo con lo que recibe el Estado, lo que pasa si no llega (deuda con
recargo, atrasos, art. 50 ET), los despidos y sus indemnizaciones, la Inspección en B, el
cierre y la sociedad limitada.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from bot.repositories.business import BusinessRepository
from bot.repositories.economy import STATE_ACCOUNT_ID, EconomyRepository
from bot.repositories.work import WorkRepository
from bot.services import business_staff as staff
from bot.services import pala
from bot.services.business import Business
from bot.services.business_catalog import SECTOR_BY_KEY
from bot.services.business_games import make_clients
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.negocio import BusinessError, BusinessService
from bot.services.pala import WorkService
from bot.services.work import Wish

GUILD = 1
USER = 10
# Martes 6 de octubre de 2026 a mediodía, hora canaria: las franjas 0 y 1 ya han acabado.
START = 1_791_284_400.0
DAY = 86_400
TODAY = date(2026, 10, 6)


class Clock:
    def __init__(self, now: float = START) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def sin_azar_de_la_pala(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pala, "wish_for", lambda *args: Wish("no", "Nada.", lambda f: False))
    monkeypatch.setattr(pala, "roll", lambda rng, chance: False)


async def make(tmp_path: Path, clock: Clock) -> BusinessService:
    database = tmp_path / "bot.db"
    economy_repo = EconomyRepository(database, starting_balance=STARTING_BALANCE)
    await economy_repo.initialize()
    economy = EconomyService(economy_repo, clock=clock)
    work_repo = WorkRepository(database)
    await work_repo.initialize()
    work = WorkService(work_repo, economy, clock=clock, rng=random.Random(1))
    repository = BusinessRepository(database)
    await repository.initialize()
    service = BusinessService(repository, economy, work, clock=clock, rng=random.Random(1))
    work.business = service
    return service


def wallet(tmp_path: Path, user_id: int) -> tuple[int, int]:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            (GUILD, user_id),
        ).fetchone()
        row = connection.execute(
            "SELECT balance FROM economy_wallets WHERE guild_id = ? AND user_id = ?",
            (GUILD, user_id),
        ).fetchone()
    return int(total), int(row[0]) if row else 0


def business_taxes(tmp_path: Path) -> dict[str, int]:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        rows = connection.execute(
            "SELECT kind, SUM(tax) FROM economy_business_tax GROUP BY kind"
        ).fetchall()
    return {str(kind): int(total) for kind, total in rows}


def tax_debt(tmp_path: Path) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        row = connection.execute(
            "SELECT amount FROM economy_tax_debts WHERE guild_id = ? AND user_id = ?",
            (GUILD, USER),
        ).fetchone()
    return int(row[0]) if row else 0


async def open_cafe(service: BusinessService, money: int = 3_000_000) -> None:
    await service.economy.grant(GUILD, USER, amount=money, reason="prueba")
    await service.open(
        GUILD, USER, sector="cafeteria", barrio="triana", name="Bar Pepe", palette=1, emblem=2
    )
    for product in SECTOR_BY_KEY["cafeteria"].products:
        await service.buy(GUILD, USER, product.key, 5)


async def hire_first(
    service: BusinessService, contract: str = "indefinido", shift: str = "manana"
) -> dict:
    business = await service.business(GUILD, USER)
    assert business is not None
    candidate = service.candidates(business)[0]
    jornada = staff.HALF_DAY if candidate["part_time"] else staff.FULL_DAY
    _business, employee = await service.hire(
        GUILD, USER, candidate["key"], contract=contract, jornada=jornada, shift=shift
    )
    return employee


# -- Reglas puras ---------------------------------------------------------------------


def employee_of(level: float = 50.0, **extra: object) -> dict:
    candidate = {
        "key": 1, "name": "Yaiza Perdomo", "age": 30, "profile": "general", "traits": [],
        "skills": dict.fromkeys(staff.SKILLS, level),
    }  # fmt: skip
    employee = staff.new_employee(
        candidate, seq=1, contract="indefinido", jornada=staff.FULL_DAY, shift="manana",
        wage=staff.ask(candidate["skills"], (), staff.FULL_DAY), start=TODAY,
    )  # fmt: skip
    employee.update(extra)
    return employee


def test_el_smi_de_una_noche_es_un_dia_del_salario_minimo_de_2026() -> None:
    assert staff.smi_night() == round(17_094 / 365 * 100)
    assert staff.smi_night(staff.HALF_DAY) == round(staff.smi_night() / 2)


def test_lo_que_pide_nunca_baja_del_salario_minimo_y_sube_con_lo_que_sabe() -> None:
    low = staff.ask(dict.fromkeys(staff.SKILLS, 0.0), (), staff.FULL_DAY)
    high = staff.ask(dict.fromkeys(staff.SKILLS, 90.0), (), staff.FULL_DAY)
    assert staff.smi_night() <= low < high


def test_la_nomina_lleva_las_cotizaciones_de_2026_y_el_temporal_paga_mas_desempleo() -> None:
    employee = employee_of()
    slip = staff.payslip(employee, "cafeteria", TODAY)
    gross = employee["wage"]
    assert slip.ss_worker == round(gross * (0.047 + 0.0155 + 0.001 + 0.0015))
    assert slip.ss_employer == round(gross * (0.236 + 0.055 + 0.002 + 0.006 + 0.0075 + 0.015))
    assert slip.net == gross - slip.ss_worker - slip.irpf
    employee["contract"] = "temporal"
    temporary = staff.payslip(employee, "cafeteria", TODAY)
    assert temporary.ss_employer > slip.ss_employer and temporary.ss_worker > slip.ss_worker
    taller = staff.payslip(employee_of(), "taller", TODAY)
    assert taller.ss_employer == round(gross * (0.236 + 0.055 + 0.002 + 0.006 + 0.0075 + 0.0445))


def test_quien_falta_no_cobra_el_dia_y_el_b_no_cotiza() -> None:
    employee = employee_of()
    assert staff.payslip(employee, "cafeteria", TODAY, docked=True).gross == 0
    employee["contract"] = "b"
    slip = staff.payslip(employee, "cafeteria", TODAY)
    assert slip.gross == slip.net == employee["wage"] and slip.to_state == 0


def test_las_indemnizaciones_son_la_parte_de_lo_cobrado_de_cada_despido() -> None:
    employee = employee_of(nights=73)
    wage = employee["wage"]
    assert staff.severance(employee, "fin") == round(12 * wage * 73 / 365)
    assert staff.severance(employee, "improcedente") == round(33 * wage * 73 / 365)
    objective = staff.severance(employee, "objetivo")
    assert objective == round(20 * wage * 73 / 365 + 15 * 7 * wage / 365)  # con el preaviso
    long = employee_of(nights=10_000)
    assert staff.severance(long, "improcedente") == round(24 / 12 * 7 * long["wage"])  # tope


def test_los_despidos_que_se_pueden_segun_el_caso() -> None:
    employee = employee_of()
    kinds = [d.kind for d in staff.dismissals(employee, TODAY, losses=False)]
    assert kinds == ["prueba"]  # en el período de prueba, sin más
    later = TODAY + timedelta(days=staff.TRIAL_NIGHTS)
    options = {d.kind: d for d in staff.dismissals(employee, later, losses=False)}
    assert not options["disciplinario"].allowed and not options["objetivo"].allowed
    assert options["improcedente"].allowed
    employee["absences"] = staff.ABSENCES_FOR_DISCIPLINE
    options = {d.kind: d for d in staff.dismissals(employee, later, losses=True)}
    assert options["disciplinario"].allowed and options["disciplinario"].amount == 0
    assert options["objetivo"].allowed


def test_la_multa_sube_con_varios_trabajadores_sin_alta() -> None:
    assert staff.fine(1) == 375_000
    assert staff.fine(2) == round(2 * 375_000 * 1.2)
    assert staff.fine(9) == round(9 * 375_000 * 1.5)


def test_la_bonificacion_del_parado_mayor_de_45_es_la_mayor_y_dura_tres_semanas() -> None:
    candidate = {
        "key": 1, "name": "Paco Mesa", "age": 50, "profile": "parado", "traits": [],
        "skills": dict.fromkeys(staff.SKILLS, 40.0),
    }  # fmt: skip
    hired = staff.new_employee(
        candidate, seq=1, contract="indefinido", jornada=staff.FULL_DAY, shift="tarde",
        wage=9_000, start=TODAY,
    )  # fmt: skip
    assert hired["bonus"] == staff.night_amount(128 * 12)
    assert hired["bonus_until"] == (TODAY + timedelta(days=20)).isoformat()
    temporary = staff.new_employee(
        candidate, seq=2, contract="temporal", jornada=staff.FULL_DAY, shift="tarde",
        wage=9_000, start=TODAY,
    )  # fmt: skip
    assert temporary["bonus"] == 0  # solo el indefinido
    banned = staff.new_employee(
        candidate, seq=3, contract="indefinido", jornada=staff.FULL_DAY, shift="tarde",
        wage=9_000, start=TODAY, bonified=False,
    )  # fmt: skip
    assert banned["bonus"] == 0


def test_el_empleado_juega_el_mismo_minijuego_y_siempre_igual() -> None:
    business = Business(USER, "cafeteria", "triana", "Bar", seed=5)
    business.stock = {p.key: 200 for p in SECTOR_BY_KEY["cafeteria"].products}
    employee = employee_of(70.0)
    clients = make_clients(business, 6, 42)
    first = staff.play_turn(business, employee, clients, start=1_000.0, franja=1).summary()
    clients = make_clients(business, 6, 42)
    again = staff.play_turn(business, employee, clients, start=1_000.0, franja=1).summary()
    assert first.sales == again.sales and first.grade == again.grade
    assert len(first.outcomes) == 6 and first.served > 0


@pytest.mark.parametrize(
    "sector", ["guachinche", "panaderia", "pescaderia", "moviles", "peluqueria"]
)
def test_la_plantilla_juega_los_minijuegos_nuevos_y_el_novato_vende_menos(sector: str) -> None:
    info = SECTOR_BY_KEY[sector]
    totals = {}
    for level in (10.0, 95.0):
        sales = 0
        for seed in range(20):
            business = Business(
                user_id=1, sector=sector, barrio="schamann", name="Prueba", seed=seed,
                stock={p.key: 200 for p in info.products},
            )  # fmt: skip
            clients = make_clients(business, info.clients, seed)
            game = staff.play_turn(business, employee_of(level), clients, start=1_000.0, franja=1)
            summary = game.summary()
            assert len(summary.outcomes) == len(clients) and game.current is None
            sales += summary.sales
        totals[level] = sales
    assert totals[10.0] < totals[95.0]


def test_quien_sabe_mas_falla_menos_y_el_quemado_falla_mas() -> None:
    sector = SECTOR_BY_KEY["cafeteria"]
    novice, expert = employee_of(10.0), employee_of(90.0)
    assert staff.miss_rate(expert, sector, 1) < staff.miss_rate(novice, sector, 1)
    burnt = employee_of(90.0, humor=10.0)
    assert staff.miss_rate(burnt, sector, 1) > staff.miss_rate(expert, sector, 1)
    barista = employee_of(50.0, traits=["barista", "madrugador"])
    assert staff.miss_rate(barista, sector, 1) < staff.miss_rate(employee_of(50.0), sector, 1)


def test_los_candidatos_del_dia_son_siempre_los_mismos_y_no_repiten_al_contratado() -> None:
    business = Business(USER, "cafeteria", "triana", "Bar", seed=5)
    first = staff.candidates(business, TODAY)
    assert first == staff.candidates(business, TODAY)
    assert len(first) == staff.CANDIDATES_PER_DAY
    business.seen = [first[0]["key"]]
    assert [c["key"] for c in staff.candidates(business, TODAY)] == [c["key"] for c in first[1:]]
    assert staff.candidates(business, TODAY + timedelta(days=1)) != first


# -- Contratar y los turnos de la plantilla --------------------------------------------


async def test_contratar_no_mueve_dinero_y_el_local_tiene_tope(tmp_path: Path) -> None:
    service = await make(tmp_path, Clock())
    await open_cafe(service)
    before = wallet(tmp_path, USER)
    employee = await hire_first(service)
    assert wallet(tmp_path, USER) == before
    assert employee["from_franja"] == 2  # a mediodía ya han pasado dos franjas
    await hire_first(service, shift="tarde")
    with pytest.raises(BusinessError, match="caben 2"):
        await hire_first(service)


async def test_no_acepta_menos_de_lo_que_pide(tmp_path: Path) -> None:
    service = await make(tmp_path, Clock())
    await open_cafe(service)
    business = await service.business(GUILD, USER)
    assert business is not None
    candidate = service.candidates(business)[0]
    jornada = staff.HALF_DAY
    with pytest.raises(BusinessError, match="pide al menos"):
        await service.hire(
            GUILD, USER, candidate["key"], contract="temporal", jornada=jornada,
            shift="tarde", wage=staff.ask(candidate["skills"], candidate["traits"], jornada) - 10,
        )  # fmt: skip


async def test_la_plantilla_juega_sus_franjas_y_cobra_como_el_dueno(tmp_path: Path) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await open_cafe(service)
    employee = await hire_first(service)
    state_before = wallet(tmp_path, STATE_ACCOUNT_ID)[1]
    owner_before = wallet(tmp_path, USER)[1]
    clock.now = START + 4.5 * 3600  # las 16:30: han acabado las franjas 2 y 3
    turns = await service.staff_turns(GUILD)
    assert [t.franja for t in turns] == [2, 3]
    sales = sum(t.summary.sales for t in turns if t.summary)
    assert sales > 0
    business = await service.business(GUILD, USER)
    assert business is not None
    assert business.staff_done == 4 and business.staff_today["turns"] == 2
    hired = business.staff[0]
    assert hired["id"] == employee["id"] and hired["turns"] == 2
    assert hired["skills"]["acierto"] > employee["skills"]["acierto"]  # aprende trabajando
    withheld = wallet(tmp_path, STATE_ACCOUNT_ID)[1] - state_before
    assert wallet(tmp_path, USER)[1] - owner_before == sales - withheld
    assert await service.staff_turns(GUILD) == []  # lo jugado no se repite


async def test_la_nomina_de_la_noche_cuadra_con_lo_que_recibe_el_estado(tmp_path: Path) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await open_cafe(service)
    await hire_first(service)
    business = await service.business(GUILD, USER)
    assert business is not None
    pay = service.payroll_tonight(business)
    clock.now = START + DAY  # pasa la noche del martes
    runs = await service.close_nights(GUILD)
    assert runs and runs[0].payroll == pay.cost
    taxes = business_taxes(tmp_path)
    assert taxes["cotizacion"] == pay.ss
    assert taxes.get("retencion_plantilla", 0) == pay.irpf
    total, balance = wallet(tmp_path, USER)
    assert total == balance
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        rows = dict(
            connection.execute(
                "SELECT concept, SUM(gross) FROM economy_tax_records WHERE kind = 'actividad' "
                "GROUP BY concept"
            ).fetchall()
        )
    # Bruto y cuota de la empresa son gasto de la actividad.
    assert rows["negocio:nominas"] + rows["negocio:seguros_sociales"] == -pay.cost
    business = await service.business(GUILD, USER)
    assert business is not None
    assert business.staff[0]["nights"] == 1 and business.wage_debt == 0


async def test_hacienda_enseña_lo_que_paga_el_empresario_por_su_plantilla(tmp_path: Path) -> None:
    from bot.repositories.economy import Treasury
    from bot.services.tax_report import Snapshot, bills, owners_page

    clock = Clock()
    service = await make(tmp_path, clock)
    await open_cafe(service)
    await hire_first(service)
    business = await service.business(GUILD, USER)
    assert business is not None
    pay = service.payroll_tonight(business)
    clock.now = START + DAY
    await service.close_nights(GUILD)

    mine = (await service.economy.business_tax_breakdown(GUILD))[USER]

    raw = business_taxes(tmp_path)
    assert mine["cotizacion"] == raw["cotizacion"] == pay.ss
    assert mine.get("retencion_plantilla", 0) == pay.irpf
    assert mine["igic"] == raw["igic"] and mine["tasa"] == raw["tasa"]
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (own,) = connection.execute(
            "SELECT COALESCE(SUM(withheld), 0) FROM economy_tax_records "
            "WHERE concept LIKE 'negocio:%'"
        ).fetchone()
    assert mine.get("irpf", 0) == own
    # Todo lo del negocio está también en la factura del dueño.
    everything = await service.economy.tax_breakdown(GUILD)
    (bill,) = bills(everything)
    assert sum(mine.values()) <= bill.total
    snapshot = Snapshot(
        guild_id=GUILD, year=2026, treasury=Treasury(0, 0, 0, ()), bills=[bill],
        housing={}, business={USER: mine}, solidarity=(0, 0, 0), names={USER: "Ana"},
    )  # fmt: skip
    page = owners_page(snapshot)
    assert "🏢 Seguridad Social de la plantilla" in page and "Ana · **" in page
    assert "🏢 SS plantilla" in page and "🛒 IGIC" in page


async def test_si_no_llega_se_le_debe_y_los_seguros_sociales_van_a_deuda_con_recargo(
    tmp_path: Path,
) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await open_cafe(service)
    await hire_first(service)
    balance = await service.economy.balance(GUILD, USER)
    await service.economy.sanction(GUILD, USER, amount=balance, concept="prueba")
    business = await service.business(GUILD, USER)
    assert business is not None
    business.stock = {}  # sin nada que vender: la plantilla no trae dinero
    await service.repository.save(GUILD, business)
    pay = service.payroll_tonight(business)
    clock.now = START + DAY
    runs = await service.close_nights(GUILD)
    assert "sin_cobrar" in runs[0].staff
    business = await service.business(GUILD, USER)
    assert business is not None
    assert business.wage_debt == pay.net == business.staff[0]["unpaid"]
    assert tax_debt(tmp_path) == round(pay.state * (1 + staff.LATE_SURCHARGE))
    # Con dos noches sin cobrar en la semana, se va con la indemnización del improcedente.
    clock.now = START + 2 * DAY
    runs = await service.close_nights(GUILD)
    assert "art50" in runs[-1].staff
    business = await service.business(GUILD, USER)
    assert business is not None and business.staff == []
    assert business.wage_debt > 2 * pay.net
    # En cuanto hay dinero, lo primero que se paga cada noche son los atrasos.
    await service.economy.grant(GUILD, USER, amount=1_000_000, reason="prueba")
    clock.now = START + 3 * DAY
    await service.close_nights(GUILD)
    business = await service.business(GUILD, USER)
    assert business is not None and business.wage_debt == 0


async def test_despedir_en_prueba_es_gratis_y_el_improcedente_se_paga(tmp_path: Path) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await open_cafe(service)
    employee = await hire_first(service)
    before = wallet(tmp_path, USER)[1]
    with pytest.raises(BusinessError):
        await service.dismiss(GUILD, USER, employee["id"], "improcedente")
    _business, gone, choice, refund = await service.dismiss(GUILD, USER, employee["id"], "prueba")
    assert choice.amount == 0 and refund == 0 and gone["id"] == employee["id"]
    assert wallet(tmp_path, USER)[1] == before
    other = await hire_first(service, shift="tarde")
    clock.now = START + (staff.TRIAL_NIGHTS + 1) * DAY
    await service.close_nights(GUILD)
    business = await service.business(GUILD, USER)
    assert business is not None
    hired = business.staff[0]
    with pytest.raises(BusinessError, match="No puedes"):
        await service.dismiss(GUILD, USER, other["id"], "disciplinario")
    before = wallet(tmp_path, USER)[1]
    _business, _gone, choice, _refund = await service.dismiss(
        GUILD, USER, other["id"], "improcedente"
    )
    assert choice.amount == staff.severance(hired, "improcedente") > 0
    assert wallet(tmp_path, USER)[1] == before - choice.amount


async def test_el_temporal_se_acaba_con_sus_12_dias(tmp_path: Path) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await open_cafe(service, money=6_000_000)
    await hire_first(service, contract="temporal")
    clock.now = START + staff.TEMPORARY_NIGHTS * DAY
    runs = await service.close_nights(GUILD)
    assert "fin_contrato" in runs[-1].staff
    business = await service.business(GUILD, USER)
    assert business is not None and business.staff == []
    assert business.wage_debt > 0  # sus 12 días, que se pagan la noche siguiente


async def test_en_b_no_cotiza_hasta_que_pasa_la_inspeccion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await open_cafe(service)
    await hire_first(service, contract="b")
    clock.now = START + DAY
    await service.close_nights(GUILD)
    assert "cotizacion" not in business_taxes(tmp_path)
    monkeypatch.setattr(staff, "INSPECTION_NIGHT", 1.0)
    clock.now = START + 2 * DAY
    runs = await service.close_nights(GUILD)
    assert "inspeccion" in runs[-1].staff and runs[-1].fine > staff.fine(1)
    taxes = business_taxes(tmp_path)
    assert taxes["multa"] == staff.fine(1) and taxes["cotizacion"] > 0
    business = await service.business(GUILD, USER)
    assert business is not None and business.staff[0]["contract"] == "indefinido"


async def test_cerrar_despide_con_20_dias_y_traspasar_no(tmp_path: Path) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await open_cafe(service)
    await hire_first(service)
    clock.now = START + (staff.TRIAL_NIGHTS + 1) * DAY
    await service.close_nights(GUILD)
    business = await service.business(GUILD, USER)
    assert business is not None
    expected = staff.severance(business.staff[0], "objetivo")
    closing = await service.close(GUILD, USER)
    assert closing.staff == 1 and closing.severance == expected > 0 and closing.fogasa == 0


async def test_formar_cuesta_su_curso_menos_fundae_y_sube_la_habilidad(tmp_path: Path) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await open_cafe(service)
    employee = await hire_first(service)
    before = wallet(tmp_path, USER)[1]
    _business, trained, paid, credit = await service.train(GUILD, USER, employee["id"], "acierto")
    assert credit == min(staff.COURSE_COST, staff.fundae_week())
    assert paid == staff.COURSE_COST - credit + round(staff.COURSE_COST * 0.07)
    assert wallet(tmp_path, USER)[1] == before - paid
    with pytest.raises(BusinessError, match="ya está"):
        await service.train(GUILD, USER, employee["id"], "trato")
    clock.now = START + DAY
    await service.close_nights(GUILD)
    business = await service.business(GUILD, USER)
    assert business is not None
    assert business.staff[0]["skills"]["acierto"] >= trained["skills"]["acierto"] + 15


async def test_subir_el_sueldo_y_hacer_fijo(tmp_path: Path) -> None:
    service = await make(tmp_path, Clock())
    await open_cafe(service)
    employee = await hire_first(service, contract="temporal")
    _business, raised = await service.raise_wage(GUILD, USER, employee["id"], 0.10)
    assert raised["wage"] == int(round(employee["wage"] * 1.1, -1))
    assert raised["humor"] > employee["humor"]
    _business, fixed = await service.make_permanent(GUILD, USER, employee["id"])
    assert fixed["contract"] == "indefinido" and fixed["until"] == ""
    with pytest.raises(BusinessError):
        await service.make_permanent(GUILD, USER, employee["id"])


async def test_la_sociedad_paga_la_plantilla_de_su_caja_y_la_resta_en_sociedades(
    tmp_path: Path,
) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await open_cafe(service)
    await service.constitute(GUILD, USER, 300_000)
    await service.contribute(GUILD, USER, 500_000)
    await hire_first(service)
    owner_before = wallet(tmp_path, USER)[1]
    business = await service.business(GUILD, USER)
    assert business is not None
    pay = service.payroll_tonight(business)
    clock.now = START + DAY
    await service.close_nights(GUILD)
    assert wallet(tmp_path, USER)[1] == owner_before  # su monedero no paga nada
    business = await service.business(GUILD, USER)
    assert business is not None
    assert business.week_totals["personal"] == -pay.cost
    assert await service.staff_dues(GUILD, USER) == (0, 0)
