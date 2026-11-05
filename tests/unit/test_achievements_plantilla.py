"""Pruebas de las estadísticas de logros de la plantilla de un negocio (fase 4 y, desde la
9.41.0, los puestos y los horarios)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from bot.services import business_staff as staff
from bot.services.achievements import (
    STAFF_HORARIOS_STAT,
    staff_course_stats,
    staff_fire_stats,
    staff_hire_stats,
    staff_night_stats,
    staff_permanent_stats,
    staff_raise_stats,
    staff_refused_stats,
    staff_reschedule_stats,
    staff_turns_stats,
    with_derived,
)
from bot.services.business_games import Client, Outcome, TurnSummary
from bot.services.negocio import NightRun, StaffTurn

DAY = date(2026, 10, 22)


def _summary(grade: str, *revenues: int, tips: int = 0) -> TurnSummary:
    outcomes = tuple(
        Outcome(Client(seed=i, line="Hola"), "ok", revenue=r) for i, r in enumerate(revenues)
    )
    return TurnSummary(outcomes, sum(revenues), tips, 0, 0, {}, 0.0, {}, 80, grade)


def _turn(
    summary: TurnSummary | None = None, *, absent: bool = False, role: str = "", led: bool = False
) -> StaffTurn:
    return StaffTurn(1, "Ana", 1, DAY, 2, summary=summary, absent=absent, role=role, led=led)


def _employee(*, age: int = 30, profile: str = "general", contract: str = "indefinido",
              jornada: int = staff.FULL_DAY, wage: int = 6_000, role: str = "",
              horario: str = "") -> dict:  # fmt: skip
    candidate = {
        "key": 1, "name": "Ana Mesa", "age": age, "profile": profile, "traits": [],
        "skills": {"acierto": 40.0, "rapidez": 90.0, "trato": 50.0},
    }  # fmt: skip
    franjas = staff.HORARIO_BY_KEY[horario].franjas if horario else None
    return staff.new_employee(
        candidate, seq=1, contract=contract, jornada=jornada, shift="manana", wage=wage,
        start=DAY, role=role, franjas=franjas,
    )  # fmt: skip


def test_turnos_de_la_plantilla() -> None:
    delta = staff_turns_stats(
        [
            _turn(_summary("S", 300, 200, 0, tips=50)),
            _turn(_summary("D", 100)),
            _turn(absent=True),
            _turn(),
        ]
    )
    assert delta.add == {
        "staff_turns": 2,
        "staff_clients": 3,
        "staff_sales": 650,
        "staff_turn_s": 1,
        "staff_turn_d": 1,
        "staff_absences": 1,
        "staff_idle": 1,
    }
    assert delta.peak == {"staff_sales_turn_max": 550, "staff_clients_turn_max": 2}


def test_sin_turnos_jugados_no_hay_maximos() -> None:
    delta = staff_turns_stats(iter([_turn(absent=True)]))
    assert delta.add == {"staff_absences": 1}
    assert delta.peak == {}


def test_noche_con_plantilla_suma_eventos_y_turnos() -> None:
    run = NightRun(
        1, "Bar", DAY, 0, 0, payroll=12_000, fine=400_000,
        staff=("sin_cobrar", "sin_cobrar", "inspeccion", "curso", "prueba_superada",
               "fin_contrato", "art50", "dimision"),
        turns=(_turn(_summary("A", 500)),),
    )  # fmt: skip
    delta = staff_night_stats(run)
    assert delta.add == {
        "staff_nights": 1,
        "staff_payroll": 12_000,
        "staff_unpaid": 2,
        "staff_inspected": 1,
        "staff_fines": 400_000,
        "staff_courses_done": 1,
        "staff_trials": 1,
        "staff_contract_end": 1,
        "staff_art50": 1,
        "staff_quit": 1,
        "staff_turns": 1,
        "staff_clients": 1,
        "staff_sales": 500,
    }
    assert delta.peak["staff_sales_turn_max"] == 500


def test_noche_sin_plantilla_no_suma_nada() -> None:
    assert not staff_night_stats(NightRun(1, "Bar", DAY, 0, 0))


def test_contratar() -> None:
    senior = _employee(age=50, profile="parado")
    business = SimpleNamespace(staff=[_employee(), senior])
    delta = staff_hire_stats(business, senior)  # type: ignore[arg-type]
    assert delta.add == {
        "staff_hired": 1,
        "staff_hired_indefinido": 1,
        "staff_hired_bonus": 1,
        "staff_hired_senior": 1,
        "staff_horario_0123": 1,
    }
    assert delta.peak == {"staff_size_max": 2, "staff_wage_max": 6_000, "staff_roles_max": 1}
    young = _employee(age=19, contract="b", jornada=staff.HALF_DAY, wage=3_000)
    delta = staff_hire_stats(SimpleNamespace(staff=[young]), young)  # type: ignore[arg-type]
    assert delta.add == {
        "staff_hired": 1,
        "staff_hired_b": 1,
        "staff_hired_half": 1,
        "staff_hired_young": 1,
        "staff_horario_12": 1,
    }


def test_contratar_un_puesto_y_un_horario_nuevos() -> None:
    rider = _employee(role="repartidor", horario="1256")
    team = [_employee(), _employee(role="limpieza", horario="67"), rider]
    delta = staff_hire_stats(
        SimpleNamespace(staff=team, sector="heladeria"),  # type: ignore[arg-type]
        rider,
    )
    assert delta.add["staff_hired_role_repartidor"] == 1
    assert delta.add["staff_hired_partida"] == 1 and delta.add["staff_horario_1256"] == 1
    assert delta.add["staff_hired_rider_helados"] == 1
    assert "staff_hired_redes_taller" not in delta.add
    assert delta.peak["staff_roles_max"] == 3
    # El gerente no tiene horario y su contratación no cuenta como puesto nuevo (ya tiene logros).
    boss = _employee(role=staff.MANAGER)
    delta = staff_hire_stats(SimpleNamespace(staff=[boss], sector="taller"), boss)  # type: ignore[arg-type]
    assert not any(k.startswith(("staff_hired_role_", "staff_horario_")) for k in delta.add)


def test_cambiar_el_horario_y_los_horarios_distintos() -> None:
    employee = _employee(horario="012")
    employee["humor"] = 80.0
    plan = staff.reschedule(employee, "0145", DAY)
    assert staff_reschedule_stats(employee, plan).add == {
        "staff_rescheduled": 1,
        "staff_rescheduled_more": 1,
        "staff_horario_0145": 1,
    }
    assert staff_refused_stats().add == {"staff_refused_less": 1}
    stats = {f"staff_horario_{h.key}": 1 for h in staff.HORARIOS[:5]}
    assert with_derived(stats)[STAFF_HORARIOS_STAT] == 5
    assert with_derived({"staff_horario_nada": 3})[STAFF_HORARIOS_STAT] == 0


def test_turnos_con_encargado_y_a_domicilio() -> None:
    delta = staff_turns_stats(
        [
            _turn(_summary("A", 300), led=True),
            _turn(_summary("B", 400, 200, tips=40), role="repartidor"),
            _turn(led=True),  # sin clientes: no cuenta
        ]
    )
    assert delta.add["staff_led_turns"] == 1
    assert delta.add["staff_deliveries"] == 2 and delta.add["staff_delivery_sales"] == 640


def test_la_noche_de_la_plantilla_de_apoyo() -> None:
    run = NightRun(
        1, "Bar", DAY, 0, 0, payroll=9_000, support=("limpieza", "redes", "rrhh"), kept=12,
        manager=("fresco",),
    )  # fmt: skip
    add = staff_night_stats(run).add
    assert add["staff_clean_nights"] == add["staff_social_nights"] == add["staff_hr_nights"] == 1
    assert "staff_stock_nights" not in add
    assert add["staff_kept_units"] == 12 and add["staff_fresh_restocks"] == 1


def test_despedir() -> None:
    gratis = staff.Dismissal("prueba", "No supera", 0, "", "")
    assert staff_fire_stats(gratis, 0).add == {"staff_fired": 1, "staff_fired_prueba": 1}
    caro = staff.Dismissal("improcedente", "Improcedente", 8_000, "", "")
    assert staff_fire_stats(caro, 1_500).add == {
        "staff_fired": 1,
        "staff_fired_improcedente": 1,
        "staff_severance": 8_000,
        "staff_bonus_refund": 1_500,
    }


def test_subir_sueldo_y_hacer_fijo() -> None:
    delta = staff_raise_stats(_employee(wage=7_200))
    assert delta.add == {"staff_raises": 1}
    assert delta.peak == {"staff_wage_max": 7_200}
    assert staff_permanent_stats().add == {"staff_permanent": 1}


def test_curso_mira_la_habilidad_como_quedara() -> None:
    employee = _employee()
    employee["course"] = "rapidez"
    delta = staff_course_stats(employee, 8_825, 805)
    assert delta.add == {"staff_courses": 1, "staff_course_spent": 8_825, "staff_fundae": 805}
    assert delta.peak == {"staff_skill_max": 100}  # 90 + 15, con tope en 100
    employee["course"] = "acierto"
    delta = staff_course_stats(employee, 9_630, 0)
    assert "staff_fundae" not in delta.add
    assert delta.peak == {"staff_skill_max": 90}
