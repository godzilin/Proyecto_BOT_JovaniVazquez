"""Pruebas de los horarios y los puestos de la plantilla de los negocios (9.41.0).

Las reglas puras de `bot.services.business_staff` (los 21 horarios, lo que pide cada puesto, sus
efectos, el encargado de turno, cambiar el horario) y los casos de uso de `BusinessService` con la
base de datos de verdad: contratar con puesto y horario, el repartidor con su cupo aparte, la noche
de la plantilla de apoyo (limpieza, mozo de almacén, community manager y recursos humanos) y el
cambio de horario, con el libro cuadrando y el IGIC de lo que repone el mozo en el Estado.
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
from bot.services.business import BOOST_CAP, Business, boost, pool_factor
from bot.services.business_catalog import SECTOR_BY_KEY
from bot.services.business_games import make_clients
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.negocio import BusinessError, BusinessService, StaffRefusedError
from bot.services.pala import WorkService
from bot.services.work import Wish

GUILD = 1
USER = 10
# Martes 6 de octubre de 2026 a las 8 de la mañana, hora canaria: no ha acabado ninguna franja.
START = 1_791_270_000.0
DAY = 86_400
TODAY = date(2026, 10, 6)


class Clock:
    def __init__(self, now: float = START) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def sin_azar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pala, "wish_for", lambda *args: Wish("no", "Nada.", lambda f: False))
    monkeypatch.setattr(pala, "roll", lambda rng, chance: False)
    # Nadie falta ni se va: las pruebas miran lo que hace cada puesto.
    monkeypatch.setattr(staff, "absent", lambda *args: False)
    monkeypatch.setattr(staff, "quits", lambda *args: False)


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


async def open_shop(
    service: BusinessService, sector: str = "cafeteria", money: int = 5_000_000
) -> None:
    """Un local grande (caben seis) con algo de todo en el almacén."""
    await service.economy.grant(GUILD, USER, amount=money, reason="prueba")
    await service.open(
        GUILD, USER, sector=sector, barrio="triana", name="Bar Pepe", palette=1, emblem=2
    )
    business = await service.business(GUILD, USER)
    assert business is not None
    business.size = 2
    await service.repository.save(GUILD, business)
    for product in SECTOR_BY_KEY[sector].products:
        await service.buy(GUILD, USER, product.key, 5)


async def hire(service: BusinessService, role: str = "", horario: str = "0123") -> dict:
    """Contrata con `role` y `horario` al primer candidato de hoy que quiera esa jornada."""
    business = await service.business(GUILD, USER)
    assert business is not None
    wanted = staff.HORARIO_BY_KEY[horario]
    pick = next(
        c
        for c in service.candidates(business)
        if not (c["part_time"] and wanted.jornada != staff.HALF_DAY)
    )
    _business, employee = await service.hire(
        GUILD, USER, pick["key"], contract="indefinido", jornada=wanted.jornada,
        shift=wanted.shift, role=role, horario=horario,
    )  # fmt: skip
    return employee


def employee_of(level: float = 50.0, *, role: str = "", horario: str = "0123", **extra: object):  # noqa: ANN201
    candidate = {
        "key": 1, "name": "Yaiza Perdomo", "age": 30, "profile": "general", "traits": [],
        "skills": dict.fromkeys(staff.SKILLS, level),
    }  # fmt: skip
    schedule = staff.HORARIO_BY_KEY[horario]
    employee = staff.new_employee(
        candidate, seq=1, contract="indefinido", jornada=schedule.jornada, shift=schedule.shift,
        wage=staff.role_ask(candidate["skills"], (), schedule.jornada, role), start=TODAY,
        role=role, franjas=schedule.franjas,
    )  # fmt: skip
    employee.update(extra)
    return employee


# -- Reglas puras: los horarios ------------------------------------------------------------


def test_hay_21_horarios_que_caben_en_un_desplegable_y_estan_los_de_antes() -> None:
    assert len(staff.HORARIOS) == 21 <= 25
    assert len({h.key for h in staff.HORARIOS}) == 21
    kinds = [h.kind for h in staff.HORARIOS]
    assert (kinds.count("completa"), kinds.count("partida"), kinds.count("seis")) == (5, 3, 6)
    assert kinds.count("media") == 7
    for shift in ("manana", "tarde"):
        for jornada in (staff.FULL_DAY, staff.HALF_DAY):
            key = staff.horario_key(staff.legacy_franjas(shift, jornada))
            assert key in staff.HORARIO_BY_KEY
    split = staff.HORARIO_BY_KEY["1256"]
    assert split.label == "Partida · 10:00-14:00 y 18:00-22:00"
    assert split.short == "Partida · 10-14 y 18-22 h" and split.jornada == staff.FULL_DAY
    assert staff.HORARIO_BY_KEY["4567"].hours == "16:00-24:00"
    assert all(h.jornada == staff.HALF_DAY for h in staff.horarios_for(part_time=True))


def test_un_empleado_de_antes_sin_franjas_guardadas_sigue_con_su_turno() -> None:
    old = employee_of()
    del old["franjas"]
    old.update(shift="tarde", jornada=staff.HALF_DAY)
    assert staff.franjas(old) == (5, 6)
    assert staff.horario_of(old) == staff.HORARIO_BY_KEY["56"]
    del old["role"]
    assert staff.role_of(old).key == ""
    boss = employee_of(role=staff.MANAGER)
    assert staff.franjas(boss) == () and staff.horario_of(boss) is None


def test_la_partida_quema_y_el_encargado_anima() -> None:
    split = employee_of(horario="1256")
    whole = employee_of(horario="0123")
    for employee in (split, whole):
        staff.night_mood(employee, paid=True, grades=[])
    assert split["humor"] - whole["humor"] == pytest.approx(staff.PARTIDA_MOOD)
    led = employee_of()
    staff.night_mood(led, paid=True, grades=[], led=True)
    assert led["humor"] - whole["humor"] == pytest.approx(staff.LEAD_MOOD)
    # Recursos humanos quita parte de lo malo, no de lo bueno.
    unpaid, shielded = employee_of(), employee_of()
    staff.night_mood(unpaid, paid=False, grades=[])
    staff.night_mood(shielded, paid=False, grades=[], shield=0.5)
    assert 70 - shielded["humor"] == pytest.approx((70 - unpaid["humor"]) * 0.5)


# -- Reglas puras: los puestos ---------------------------------------------------------------


def test_cada_puesto_pide_lo_suyo_y_nunca_menos_del_salario_minimo() -> None:
    skills = dict.fromkeys(staff.SKILLS, 0.0)
    for role in staff.ROLES:
        for jornada in (staff.FULL_DAY, staff.SIX_HOURS, staff.HALF_DAY):
            asked = staff.role_ask(skills, (), jornada, role.key)
            real = jornada if role.schedule else staff.FULL_DAY
            assert asked >= staff.smi_night(real)
    good = dict.fromkeys(staff.SKILLS, 60.0)
    base = staff.ask(good, (), staff.FULL_DAY)
    assert staff.role_ask(good, (), staff.FULL_DAY, "encargado") > base
    assert staff.role_ask(good, (), staff.FULL_DAY, "limpieza") < base
    # El gerente sigue pidiendo lo de antes de la 9.41.0.
    assert staff.manager_ask(good, ()) == -(-round(base * staff.MANAGER_PREMIUM) // 10) * 10


def test_los_puestos_de_apoyo_dejan_su_efecto_por_sus_horas_y_su_habilidad() -> None:
    business = Business(1, "cafeteria", "triana", "Bar", seed=7)
    business.staff = [
        employee_of(role="limpieza", horario="67", id=1),
        employee_of(80, role="redes", horario="0123", id=2),
        employee_of(role="rrhh", horario="0123", id=3),
    ]
    help_ = staff.support(business, TODAY)
    assert help_.reputation == pytest.approx(staff.CLEAN_REPUTATION / 2)
    assert help_.patience == pytest.approx(staff.CLEAN_PATIENCE / 2)
    assert help_.clients == pytest.approx(staff.SOCIAL_BOOST * 1.3)
    assert help_.shield == pytest.approx(staff.HR_SHIELD) and help_.keep == 0
    # Quien no ha venido no deja nada.
    business.staff[0]["absent_day"] = TODAY.isoformat()
    assert staff.support(business, TODAY).reputation == 0
    # Lo que publica el community manager suma a la campaña, con tope.
    business.support = {"clients": 0.4}
    business.campaign, business.campaign_until = "radio", TODAY.isoformat()
    assert boost(business, TODAY) == BOOST_CAP
    assert pool_factor(business, TODAY) == pytest.approx(business.size_info.pool * 1.5)
    assert "+30 % de clientes" in staff.effect_text(staff.ROLE_BY_KEY["redes"])


def test_quien_cabe_en_el_local_y_que_puesto_hay_en_cada_sector() -> None:
    business = Business(1, "taller", "triana", "Taller", seed=7, size=0)
    assert staff.hire_problem(business, "reponedor") is not None  # nada que caduque
    assert staff.hire_problem(business, "repartidor") is not None  # el coche no cabe
    assert staff.hire_problem(business, "no-existe") == "Ese puesto no existe."
    business.staff = [employee_of(role="limpieza", id=1)]
    assert "ya tiene limpieza" in (staff.hire_problem(business, "limpieza") or "")
    business.staff.append(employee_of(id=2))
    assert "caben 2" in (staff.hire_problem(business, "") or "")
    assert staff.hire_problem(business, staff.MANAGER) is None  # el gerente no ocupa puesto
    assert [r.key for r in staff.roles_for(business)] == [
        "", "encargado", staff.MANAGER, "limpieza", "redes", "rrhh",
    ]  # fmt: skip


def test_con_recursos_humanos_llega_un_candidato_mas_y_los_demas_no_cambian() -> None:
    business = Business(1, "cafeteria", "triana", "Bar", seed=99)
    before = staff.candidates(business, TODAY)
    business.staff = [employee_of(role="rrhh", id=1)]
    after = staff.candidates(business, TODAY)
    assert len(after) == len(before) + staff.HR_CANDIDATES
    assert [c["key"] for c in after[: len(before)]] == [c["key"] for c in before]


def test_el_encargado_hace_fallar_menos_a_los_demas() -> None:
    business = Business(1, "cafeteria", "triana", "Bar", seed=3)
    business.stock = {p.key: 500 for p in business.sector_info.products}
    clumsy = employee_of(5)
    wrong = {0.0: 0, 1.5: 0}
    for lead in wrong:
        for n in range(40):
            clients = make_clients(business, 6, n)
            game = staff.play_turn(business, clumsy, clients, start=n * 7200.0, franja=1, lead=lead)
            wrong[lead] += game.summary().count("wrong")
    assert wrong[1.5] < wrong[0.0]


# -- Reglas puras: cambiar el horario ---------------------------------------------------------


def test_cambiar_el_horario_mantiene_el_sueldo_por_franja_y_vale_desde_manana() -> None:
    employee = employee_of(horario="0123")
    same = staff.reschedule(employee, "4567", TODAY)
    assert (same.kind, same.wage, same.start) == ("igual", employee["wage"], "2026-10-07")
    more = staff.reschedule(employee_of(horario="012"), "0123", TODAY)
    assert more.kind == "mas" and more.wage > employee_of(horario="012")["wage"]
    less = staff.reschedule(employee, "12", TODAY)
    assert less.kind == "menos" and less.wage >= staff.worth(employee) // 2
    with pytest.raises(ValueError, match="ya trabaja"):
        staff.reschedule(employee, "0123", TODAY)
    with pytest.raises(ValueError, match="no tiene horario"):
        staff.reschedule(employee_of(role=staff.MANAGER), "12", TODAY)
    student = employee_of(horario="12", traits=["estudiante", "leal"])
    with pytest.raises(ValueError, match="media jornada"):
        staff.reschedule(student, "0123", TODAY)
    employee["humor"] = staff.FEWER_HOURS_HUMOR - 1
    with pytest.raises(staff.Refusal):
        staff.reschedule(employee, "12", TODAY)


def test_el_horario_nuevo_entra_al_dia_siguiente_con_su_sueldo_y_su_bonificacion() -> None:
    employee = employee_of(horario="0123", bonus=360, bonus_until="2026-10-30")
    plan = staff.reschedule(employee, "12", TODAY)
    staff.plan_reschedule(employee, plan)
    assert employee["humor"] == 70 + staff.RESCHEDULE_MOOD["menos"]
    assert not staff.apply_reschedule(employee, TODAY)
    assert staff.franjas(employee) == (0, 1, 2, 3)
    assert staff.apply_reschedule(employee, TODAY + timedelta(days=1))
    assert staff.franjas(employee) == (1, 2) and employee["jornada"] == staff.HALF_DAY
    assert employee["wage"] == plan.wage and employee["bonus"] == 180 and not employee["next"]


# -- Casos de uso con la base de datos ---------------------------------------------------------


async def test_contratar_con_puesto_y_horario(tmp_path: Path) -> None:
    service = await make(tmp_path, Clock())
    await open_shop(service)
    boss = await hire(service, "encargado", "1256")
    assert staff.franjas(boss) == (1, 2, 5, 6) and boss["role"] == "encargado"
    skills, traits = boss["skills"], boss["traits"]
    assert boss["wage"] == staff.role_ask(skills, traits, staff.FULL_DAY, "encargado")
    with pytest.raises(BusinessError, match="ya tiene encargado"):
        await hire(service, "encargado")
    with pytest.raises(BusinessError, match="no está en la lista"):
        await service.hire(
            GUILD, USER, 1, contract="indefinido", jornada=4, shift="manana", horario="0167"
        )
    total, balance = wallet(tmp_path, USER)
    assert total == balance


async def test_el_encargado_y_el_repartidor_juegan_sus_franjas(tmp_path: Path) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await open_shop(service)
    clerk = await hire(service, "", "0123")
    await hire(service, "encargado", "0123")
    rider = await hire(service, "repartidor", "45")
    clock.now = START + 12 * 3600  # las 20:00: han acabado las franjas 0 a 5
    turns = await service.staff_turns(GUILD)
    mine = [t for t in turns if t.employee == clerk["id"] and t.summary is not None]
    assert mine and all(t.led for t in mine)
    away = [t for t in turns if t.employee == rider["id"]]
    assert {t.franja for t in away} == {4, 5} and all(t.role == "repartidor" for t in away)
    assert not any(t.led for t in away)
    # El cupo a domicilio se gasta con cada pedido que entra, se atienda o no.
    served = sum(len(t.summary.outcomes) for t in away if t.summary is not None)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (used,) = connection.execute(
            "SELECT used FROM business_demand WHERE sector = ?",
            (staff.delivery_key("cafeteria"),),
        ).fetchone()
    assert used == served > 0
    business = await service.business(GUILD, USER)
    assert business is not None
    assert next(e for e in business.staff if e["id"] == clerk["id"])["led_day"] == "2026-10-06"


async def test_la_noche_de_la_plantilla_de_apoyo(tmp_path: Path) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await open_shop(service)
    await hire(service, "limpieza", "67")
    await hire(service, "redes", "0123")
    await hire(service, "reponedor", "01")
    before = await service.business(GUILD, USER)
    assert before is not None
    reputation = before.reputation
    # Se acabó el zumo: esta noche el mozo lo repone (lo demás aún llega para mañana).
    before.stock["zumo"] = 0
    await service.repository.save(GUILD, before)
    state = wallet(tmp_path, STATE_ACCOUNT_ID)[1]
    clock.now = START + DAY
    runs = await service.close_nights(GUILD)
    run = runs[0]
    assert set(run.support) == {"limpieza", "redes", "reponedor"}
    assert run.kept > 0 and "fresco" in run.manager  # sin gerente, el mozo repone lo fresco
    after = await service.business(GUILD, USER)
    assert after is not None
    assert after.reputation > reputation and run.cleaned > 0
    assert after.support["clients"] > 0 and after.support["patience"] > 0
    assert after.stock["zumo"] >= after.sector_info.product("zumo").pack  # type: ignore[union-attr]
    # Lo que compra el mozo paga su IGIC al Estado y el libro cuadra.
    total, balance = wallet(tmp_path, USER)
    assert total == balance
    assert wallet(tmp_path, STATE_ACCOUNT_ID)[1] > state


async def test_cambiar_el_horario_desde_el_servicio(tmp_path: Path) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await open_shop(service)
    employee = await hire(service, "", "0123")
    business, moved, plan = await service.reschedule(GUILD, USER, employee["id"], "4567")
    assert plan.kind == "igual" and moved["next"]["from"] == "2026-10-07"
    assert staff.franjas(moved) == (0, 1, 2, 3)  # hoy, el de siempre
    clock.now = START + DAY
    runs = await service.close_nights(GUILD)
    assert runs[0].rescheduled == 1
    after = await service.business(GUILD, USER)
    assert after is not None
    assert staff.franjas(after.staff[0]) == (4, 5, 6, 7)
    after.staff[0]["humor"] = 10.0
    await service.repository.save(GUILD, after)
    with pytest.raises(StaffRefusedError):
        await service.reschedule(GUILD, USER, employee["id"], "56")
    assert business.id == after.id
