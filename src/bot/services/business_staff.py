"""Reglas de la plantilla de un negocio de `pala` (fase 4, `docs/negocios.md`).

Lógica pura, sin Discord ni base de datos. Los empleados son artificiales, como los
inquilinos de `pisos`: cada día llegan candidatos (`candidates`) con su perfil, dos rasgos
(`TRAITS`) y tres habilidades (`SKILLS`), y el dueño los contrata con un contrato temporal o
indefinido (o en B, sin alta), a jornada completa o media, de mañana o de tarde.

- **Turnos pasivos** (`play_turn`): cada franja de su turno, el empleado atiende a los
  clientes con el mismo minijuego que el dueño. Acierta y va rápido según sus habilidades,
  su humor y sus rasgos, así que su nota, sus propinas, las reseñas y la reputación salen
  del mismo `TurnGame` que las del dueño.
- **Nómina** (`payslip`): cada noche, la séptima parte de su sueldo del año (la semana es el
  año del juego, como en la `pala`), con su cotización a la Seguridad Social, la de la
  empresa y la retención del IRPF.
- **Humor** (`night_mood`): lo mueven el sueldo frente a lo que pide, sus turnos, la formación
  y no cobrar. Con poco humor falla más, falta (`absent`) y acaba yéndose (`quits`).
- **Despidos** (`dismissals`): período de prueba, fin del temporal, disciplinario, objetivo e
  improcedente, con su indemnización (`severance`).
- **Gerente** (fase 5, `MANAGER`): un empleado con ese puesto (`employee["role"]`) no atiende
  franjas; cada noche repone el stock y cubre las bajas (`business_chain.restock`). Pide más
  (`manager_ask`) y no ocupa sitio en el local (`cap` cuenta solo a los demás).
- **Horarios** (9.41.0, `HORARIOS`): el dueño elige las franjas de cada uno entre 21 horarios
  (completa, partida, seis horas o media, con su hora de entrada) y se los puede cambiar
  después (`reschedule`, desde el día siguiente). La partida cansa (`PARTIDA_MOOD`).
- **Puestos** (9.41.0, `ROLES`): además del dependiente y el gerente, el encargado de turno
  (los demás fallan menos en sus franjas), el repartidor (pedidos a domicilio de un cupo aparte,
  `DELIVERY_POOL`) y cuatro que no atienden y dejan su efecto cada día que vienen (`support`):
  limpieza, mozo de almacén, community manager y recursos humanos. Uno de cada por local, salvo
  los dependientes.

Escalas, las de los sueldos de la `pala` (`WAGE_YAPDOLLARS_PER_EURO`, 100 Y$ el euro):

- **Dinero:** un día del juego es un día de trabajo. Lo que la ley da en euros al año (el
  salario mínimo, la bonificación, el crédito de FUNDAE) se reparte entre sus 365 días
  (`night_amount`); las cifras de una vez (la multa), a 100 Y$ el euro. Con eso, el salario
  mínimo de una jornada completa sale por debajo del P1 de la `pala`, como en la vida real.
- **Plazos:** los meses pasan a noches a 7/12 de noche por mes (el año son 7 días, como la
  excedencia de la `pala`) y los años, a semanas (`months_to_nights`).
- **Indemnizaciones:** la misma parte de lo cobrado que en la vida real: 33 días por año
  trabajado son 33/365 de lo cobrado (`severance`).
- **Retención del IRPF:** la de la `pala`, con la semana de nóminas pasada a un año con la
  vara del IRPF (`retention_rate`).

El estado de cada empleado es un diccionario de `Business.staff` (`new_employee`).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, fields
from datetime import date, timedelta
from typing import Any

from bot.services.business_catalog import (
    STAFF_FIRST_NAMES,
    STAFF_LAST_NAMES,
    Sector,
)
from bot.services.business_games import AUTOPLAY_GAMES, COINS, Client, TurnGame, autoplay
from bot.services.taxes import WAGE_YAPDOLLARS_PER_EURO, WeekIncome, week_tax

# -- Escalas y cifras de la ley ------------------------------------------------------

#: Salario mínimo interprofesional de 2026, en euros al año (14 pagas de 1.221 €, art. 3 del
#: RD 126/2026).
SMI_EUR_YEAR = 17_094
#: Franjas de una jornada completa y de una media jornada (las de la `pala`).
FULL_DAY = 4
HALF_DAY = 2
#: Las ocho franjas del día (`agenda.FRANJAS_DIA`): la primera empieza a las 8 y cada una
#: dura dos horas. La de mañana va de la 0 a la 3 y la de tarde de la 4 a la 7; la media
#: jornada se queda con las dos del centro, las de más clientes.
FIRST_HOUR = 8
SHIFTS: dict[str, tuple[int, ...]] = {"manana": (0, 1, 2, 3), "tarde": (4, 5, 6, 7)}
HALF_SHIFTS: dict[str, tuple[int, ...]] = {"manana": (1, 2), "tarde": (5, 6)}
SHIFT_NAMES = {"manana": "mañana", "tarde": "tarde"}
#: Franjas de la jornada de seis horas (las 3/4 de la completa).
SIX_HOURS = 3


@dataclass(frozen=True, slots=True)
class Horario:
    """Un horario de la plantilla: las franjas del día que trabaja (9.41.0).

    La clave son sus franjas en orden (`"1256"`): se guarda en el empleado como lista
    (`employee["franjas"]`) y no se cambia nunca. La clase sale de las franjas: cuatro
    seguidas, completa; cuatro en dos tramos, partida; tres, seis horas; dos, media.
    """

    key: str
    franjas: tuple[int, ...]

    @property
    def jornada(self) -> int:
        """Franjas que trabaja (lo que mira el sueldo: `ask`)."""
        return len(self.franjas)

    @property
    def blocks(self) -> tuple[tuple[int, int], ...]:
        """Los tramos seguidos: `(primera franja, última)` de cada uno."""
        out: list[tuple[int, int]] = []
        for franja in self.franjas:
            if out and franja == out[-1][1] + 1:
                out[-1] = (out[-1][0], franja)
            else:
                out.append((franja, franja))
        return tuple(out)

    @property
    def kind(self) -> str:
        """`completa`, `partida`, `seis` o `media`."""
        if self.jornada >= FULL_DAY:
            return "partida" if len(self.blocks) > 1 else "completa"
        return "seis" if self.jornada == SIX_HOURS else "media"

    @property
    def name(self) -> str:
        """`Completa`, `Partida`, `Seis horas` o `Media`."""
        return HORARIO_KINDS[self.kind]

    @property
    def hours(self) -> str:
        """`8:00-16:00` o, la partida, `10:00-14:00 y 18:00-22:00`."""
        return " y ".join(
            f"{FIRST_HOUR + 2 * first}:00-{FIRST_HOUR + 2 * (last + 1)}:00"
            for first, last in self.blocks
        )

    @property
    def label(self) -> str:
        """`Partida · 10:00-14:00 y 18:00-22:00`."""
        return f"{self.name} · {self.hours}"

    @property
    def short(self) -> str:
        """`Partida · 10-14 y 18-22 h`, para las tarjetas."""
        hours = " y ".join(
            f"{FIRST_HOUR + 2 * first}-{FIRST_HOUR + 2 * (last + 1)}" for first, last in self.blocks
        )
        return f"{self.name} · {hours} h"

    @property
    def shift(self) -> str:
        """El turno de siempre (`manana` o `tarde`) en el que empieza: lo guardan los empleados
        de antes de la 9.41.0 y lo enseñan las vacantes del gerente."""
        return "manana" if self.franjas[0] < len(SHIFTS["manana"]) else "tarde"


#: Cómo se llama cada clase de horario.
HORARIO_KINDS = {
    "completa": "Completa", "partida": "Partida", "seis": "Seis horas", "media": "Media",
}  # fmt: skip


def _horario(franjas: tuple[int, ...] | range) -> Horario:
    franjas = tuple(franjas)
    return Horario("".join(str(f) for f in franjas), franjas)


#: Los horarios que se ofrecen, en el orden del desplegable (21 de las 25 opciones que
#: admite Discord): completa entrando a las 8, 10, 12, 14 o 16; partida con dos tramos de
#: cuatro horas y cuatro de descanso; seis horas y media jornada con cada entrada posible.
#: Los cuatro de antes de la 9.41.0 (`SHIFTS` y `HALF_SHIFTS`) están entre ellos.
HORARIOS: tuple[Horario, ...] = (
    *(_horario(range(start, start + FULL_DAY)) for start in range(5)),
    *(_horario(f) for f in ((0, 1, 4, 5), (1, 2, 5, 6), (2, 3, 6, 7))),
    *(_horario(range(start, start + SIX_HOURS)) for start in range(6)),
    *(_horario(range(start, start + HALF_DAY)) for start in range(7)),
)
HORARIO_BY_KEY = {h.key: h for h in HORARIOS}
#: Lo que baja el humor cada noche la jornada partida: ir, volver a casa y volver cansa. No
#: hay plus en el Estatuto (art. 34 ET solo pide el descanso entre jornadas).
PARTIDA_MOOD = -2.0


def horario_key(franjas: tuple[int, ...] | list[int]) -> str:
    """La clave de un horario por sus franjas."""
    return "".join(str(f) for f in sorted(franjas))


def legacy_franjas(shift: str, jornada: int) -> tuple[int, ...]:
    """Las franjas de un empleado de antes de la 9.41.0, por su turno y su jornada."""
    return (SHIFTS if jornada >= FULL_DAY else HALF_SHIFTS)[shift]


def horarios_for(part_time: bool) -> tuple[Horario, ...]:
    """Los horarios que se le pueden ofrecer: a quien solo quiere media jornada, las medias."""
    return tuple(h for h in HORARIOS if not part_time or h.jornada == HALF_DAY)


#: Cotización a la Seguridad Social del régimen general en 2026 (Orden PJC/297/2026, arts. 4,
#: 16 y 33; fracción del bruto). Del
#: trabajador: contingencias comunes 4,70 %, desempleo 1,55 % (1,60 % con contrato temporal),
#: formación profesional 0,10 % y MEI 0,15 %.
WORKER_RATES = {"indefinido": 0.0470 + 0.0155 + 0.0010 + 0.0015,
                "temporal": 0.0470 + 0.0160 + 0.0010 + 0.0015}  # fmt: skip
#: De la empresa: contingencias comunes 23,60 %, desempleo 5,50 % (6,70 % con temporal),
#: FOGASA 0,20 %, formación profesional 0,60 % y MEI 0,75 %, más la de accidentes de
#: trabajo de su actividad (`ACCIDENT_RATES`).
EMPLOYER_RATES = {"indefinido": 0.2360 + 0.0550 + 0.0020 + 0.0060 + 0.0075,
                  "temporal": 0.2360 + 0.0670 + 0.0020 + 0.0060 + 0.0075}  # fmt: skip
#: Accidentes de trabajo y enfermedades profesionales (tarifa de la DA 61.ª LGSS, que trajo
#: el RDL 3/2026 con la CNAE-2025), IT más IMS: servicios de comidas y bebidas (56) 1,50 %,
#: comercio al por menor (47) 1,65 % y reparación de vehículos de motor (9531) 4,45 %.
ACCIDENT_RATES = {
    "cafeteria": 0.0150, "heladeria": 0.0150, "tienda": 0.0165, "ferreteria": 0.0165,
    "taller": 0.0445,
    # 9.48.0. El guachinche es servicio de comidas (56) y la panadería y la pescadería,
    # comercio al por menor (47), como los de arriba. La peluquería (96.21) y la reparación
    # de móviles (95.1) van con las cifras de la tarifa anterior (1,10 % y 2,60 %), sin
    # comprobar aún en la tabla de la CNAE-2025: ver `docs/negocios.md`.
    "guachinche": 0.0150, "panaderia": 0.0165, "pescaderia": 0.0165, "moviles": 0.0260,
    "peluqueria": 0.0110,
}  # fmt: skip

#: Período de prueba (art. 14.1 ET): 3 meses en una empresa de menos de 25 trabajadores,
#: para quien no es técnico titulado.
TRIAL_NIGHTS = 2
#: Contrato temporal por circunstancias de la producción (art. 15.2 ET): 6 meses como mucho.
TEMPORARY_NIGHTS = 4
#: Indemnización en días de salario por año trabajado, y su tope en mensualidades.
SEVERANCE_DAYS = {"fin": 12, "objetivo": 20, "improcedente": 33}
SEVERANCE_CAP_MONTHS = {"objetivo": 12, "improcedente": 24}
#: Preaviso del despido objetivo (art. 53.1.c ET): 15 días, que se pagan en vez de darlos.
NOTICE_DAYS = 15
#: Faltas sin justificar con las que el despido disciplinario es procedente (art. 54.2.a ET:
#: «faltas repetidas e injustificadas de asistencia»).
ABSENCES_FOR_DISCIPLINE = 3
#: Noches sin cobrar en una semana (el año) con las que el empleado se va y pide la
#: indemnización del despido improcedente (art. 50.1.b y 50.2 ET, desde la LO 1/2025: tres
#: mensualidades sin cobrar en un año, aunque no sean seguidas; cada noche son 12/7 meses).
UNPAID_NIGHTS_TO_LEAVE = 2
#: Recargo de lo que no se ingresa a tiempo de los seguros sociales (art. 30.1.a LGSS: 10 %
#: el primer mes tras el plazo, 20 % después). Cada noche es más de un mes: el 20 %. Las
#: retenciones llevan el mismo (simplificación: los de la LGT van del 1 al 20 %).
LATE_SURCHARGE = 0.20

#: Empleados como mucho por tamaño de local (pequeño, mediano, grande), sin contar al gerente.
STAFF_CAP = (2, 4, 6)
#: Fase 5: el puesto de gerente (`employee["role"]`; los demás, `""`), uno por local y a jornada
#: completa, y lo que pide de más frente a un dependiente que sepa lo mismo (un encargado).
MANAGER = "gerente"
MANAGER_PREMIUM = 1.2
#: Candidatos que llegan cada día.
CANDIDATES_PER_DAY = 3

# -- Puestos (9.41.0) -----------------------------------------------------------------

#: Encargado de turno: lo que bajan los fallos de los demás del mostrador en las franjas que
#: trabaja (por su mano con la gente, `lead`) y lo que sube esa noche el humor de quienes
#: coincidieron con él.
LEAD_MISS = 0.45
LEAD_MOOD = 1.0
#: Repartidor: el cupo a domicilio del barrio (esta parte del de mostrador del sector, el mismo
#: para cualquier local, porque no depende de sus metros) y los pedidos de cada franja (esta
#: parte de los clientes que tendría un turno a esa hora). Es una fuente de dinero nueva con su
#: tope, como el cupo de siempre.
DELIVERY_POOL = 0.25
DELIVERY_TURN = 0.5
#: Limpieza: la reputación que sube cada noche que viene y la paciencia de los clientes al día
#: siguiente (a jornada completa, con su acierto de 50).
CLEAN_REPUTATION = 3.0
CLEAN_PATIENCE = 0.30
#: Mozo de almacén: la parte de la merma de cada noche que evita (se suma a la de las mejoras,
#: con el tope de toda) y los días de lo que se vendió que repone de lo perecedero cada noche
#: sin gerente (lo fresco, para un día: así se estropea menos).
STOCK_KEEP = 0.6
FRESH_DAYS = 1.0
#: Community manager: lo que suma al día siguiente a los clientes de cada turno y a la parte
#: del cupo del barrio, como una campaña (con la campaña, `business.BOOST_CAP` como mucho).
SOCIAL_BOOST = 0.30
#: Recursos humanos: lo que quita de lo malo del humor de los demás, lo que suma a lo que
#: aprenden trabajando y los candidatos de más cada día.
HR_SHIELD = 0.5
HR_LEARN = 0.5
HR_CANDIDATES = 1
#: El tope de lo que quita RR. HH. de lo malo del humor.
SHIELD_MAX = 0.8


@dataclass(frozen=True, slots=True)
class Role:
    """Un puesto de la plantilla (`employee["role"]`).

    Attributes:
        key: Clave estable (`""` para el dependiente; la dibuja la escena).
        name: Nombre.
        emoji: Emoji de los menús.
        text: Lo que hace, en una línea (lo ve el dueño).
        plays: `mostrador` si atiende con el minijuego, `reparto` si lleva pedidos a domicilio
            con él y `""` si no atiende.
        premium: Lo que pide de más (o de menos) frente a un dependiente que sepa lo mismo.
            Nunca por debajo del salario mínimo.
        sectors: Sectores que lo pueden contratar (vacío: todos).
        unique: Si solo hay uno por local.
        skill: La habilidad que mueve su efecto (`strength`).
        schedule: Si tiene horario. El gerente no: va a jornada completa y no ocupa sitio.
        effects: Lo que deja cada día que viene, a jornada completa y con la habilidad a 50:
            pares `(campo de Support, cantidad)`.
        duty: Lo que hace cada día, en tres o cuatro palabras (la tarjeta de la plantilla).
    """

    key: str
    name: str
    emoji: str
    text: str
    plays: str = ""
    premium: float = 1.0
    sectors: tuple[str, ...] = ()
    unique: bool = True
    skill: str = ""
    schedule: bool = True
    effects: tuple[tuple[str, float], ...] = ()
    duty: str = ""

    def fits(self, sector: str) -> bool:
        """Si un negocio de `sector` lo puede contratar."""
        return not self.sectors or sector in self.sectors

    @property
    def seat(self) -> bool:
        """Si ocupa uno de los puestos del local (`cap`): todos menos el gerente."""
        return self.schedule


#: Los sectores con algo que caduca (el mozo de almacén) y los que llevan a domicilio (el taller,
#: los móviles y la peluquería no: el coche, el móvil roto y la cabeza no caben en la mochila).
PERISHABLE_SECTORS = ("cafeteria", "tienda", "heladeria", "guachinche", "panaderia", "pescaderia")
DELIVERY_SECTORS = (
    "cafeteria", "tienda", "heladeria", "ferreteria", "guachinche", "panaderia", "pescaderia",
)  # fmt: skip

ROLES: tuple[Role, ...] = (
    Role("", "Dependiente", "🙋", "Atiende el mostrador en las franjas de su horario, con el "
         "mismo minijuego que tú.", plays="mostrador", unique=False),
    Role("encargado", "Encargado de turno", "📋", "Atiende el mostrador y, en las franjas que "
         "trabaja, los demás fallan menos y acaban el día de mejor humor.", plays="mostrador",
         premium=1.1, skill="trato"),
    Role("repartidor", "Repartidor", "🛵", "Lleva pedidos a domicilio en las franjas de su "
         "horario, de un cupo de clientes aparte del barrio.", plays="reparto",
         sectors=DELIVERY_SECTORS),
    Role(MANAGER, "Gerente", "🧑‍💼", "No atiende: cada noche repone el almacén y cubre las "
         "bajas de la plantilla.", premium=MANAGER_PREMIUM, schedule=False,
         duty="Repone y cubre bajas"),
    Role("limpieza", "Limpieza", "🧽", "No atiende. Con el local limpio sube la reputación "
         "cada noche y al día siguiente los clientes esperan más. Rinde sobre todo sin las cinco "
         "estrellas.", premium=0.9,
         skill="acierto", effects=(("reputation", CLEAN_REPUTATION),
                                   ("patience", CLEAN_PATIENCE)),
         duty="Limpia el local"),
    Role("reponedor", "Mozo de almacén", "📦", "No atiende. Rota el género y cuida la cámara: "
         "cada noche se estropea menos de lo perecedero y, si no hay gerente, repone lo fresco "
         "para el día siguiente.", premium=0.9, sectors=PERISHABLE_SECTORS, skill="rapidez",
         effects=(("keep", STOCK_KEEP),), duty="Rota el género"),
    Role("redes", "Community manager", "📱", "No atiende. Lo que publica cada día trae al "
         "siguiente más clientes y más cupo del barrio, como una campaña.", premium=1.1,
         skill="trato", effects=(("clients", SOCIAL_BOOST),), duty="Publica en redes"),
    Role("rrhh", "Recursos humanos", "🗂️", "No atiende. Cada día llega un candidato más y la "
         "plantilla se quema menos y aprende más rápido.", skill="trato",
         effects=(("shield", HR_SHIELD), ("learn", HR_LEARN)), duty="Cuida a la plantilla"),
)  # fmt: skip
ROLE_BY_KEY = {r.key: r for r in ROLES}
#: Cómo se dice cada efecto de `Role.effects` en una línea: `(antes, después, en %)`.
EFFECT_TEXTS: dict[str, tuple[str, str, bool]] = {
    "reputation": ("+", " de reputación cada noche", False),
    "patience": ("+", " de paciencia al día siguiente", True),
    "keep": ("−", " de merma", True),
    "clients": ("+", " de clientes y de cupo al día siguiente", True),
    "shield": ("−", " de lo que quema a los demás", True),
    "learn": ("+", " de lo que aprenden los demás", True),
}


def role_of(employee: dict[str, Any]) -> Role:
    """El puesto de un empleado (los de antes de la 9.41.0 son dependientes o gerentes)."""
    return ROLE_BY_KEY.get(employee.get("role", ""), ROLES[0])


def effect_text(role: Role, strength: float = 1.0) -> str:
    """Lo que deja un puesto de apoyo con esa fuerza (`strength`), en una línea, o `""`."""
    parts = []
    for key, base in role.effects:
        sign, tail, percent = EFFECT_TEXTS[key]
        value = base * strength
        number = f"{round(value * 100)} %" if percent else f"{value:.1f}".replace(".", ",")
        parts.append(f"{sign}{number}{tail}")
    return ", ".join(parts)


#: B: probabilidad de que la Inspección de Trabajo pase una noche si hay alguien sin alta, y
#: la de la noche siguiente a que se vaya enfadado (lo denuncia).
INSPECTION_NIGHT = 0.04
INSPECTION_TIPOFF = 0.5
#: Multa por cada trabajador sin alta (arts. 22.2 y 40.1.e LISOS, grave en grado mínimo:
#: 3.750 €), en Y$, y lo que sube con varios a la vez (un 20 % con dos, un 30 % con tres, un
#: 40 % con cuatro y un 50 % con cinco o más).
FINE_PER_WORKER = 3_750 * WAGE_YAPDOLLARS_PER_EURO
FINE_RAISE = (0.0, 0.0, 0.20, 0.30, 0.40, 0.50)

#: Formación: un curso sube una habilidad estos puntos (la noche siguiente) y cuesta esto,
#: base sin IGIC.
COURSE_POINTS = 15
COURSE_COST = 9_000
#: Crédito de formación de FUNDAE para una empresa de 1 a 5 trabajadores (420 € al año, DA
#: 92.ª.Cinco de la Ley 31/2022, prorrogada): se descuenta del curso.
FUNDAE_EUR_YEAR = 420.0
#: Subidas de sueldo que se pueden dar.
RAISES: tuple[float, ...] = (0.05, 0.10, 0.20)

# -- Habilidades, rasgos y perfiles ----------------------------------------------------

#: Habilidades (0-100). `acierto`: cuántas veces se equivoca. `rapidez`: lo que tarda en cada
#: pulsación (propinas y paciencia). `trato`: pillar al listillo, recordar lo de siempre de un
#: habitual y atreverse con el presupuesto caro.
SKILLS: tuple[str, ...] = ("acierto", "rapidez", "trato")
SKILL_NAMES = {"acierto": "Acierto", "rapidez": "Rapidez", "trato": "Trato"}
#: Fallos con acierto 0 y con acierto 100.
MISS_AT_ZERO = 0.30
MISS_AT_FULL = 0.03
#: Segundos por pulsación con rapidez 0 y con rapidez 100.
CLICK_AT_ZERO = 3.2
CLICK_AT_FULL = 1.2
#: Lo que sube cada habilidad un turno trabajado, sobre lo que le falta para 100.
LEARN_PER_TURN = 0.006


@dataclass(frozen=True, slots=True)
class Trait:
    """Un rasgo de un empleado.

    Attributes:
        key: Clave estable.
        name: Nombre.
        text: Lo que hace, en una línea (lo ve el dueño).
        miss: Multiplica sus fallos.
        speed: Multiplica su rapidez (más, más rápido).
        trato: Suma a su trato.
        learn: Multiplica lo que aprende trabajando.
        mood: Multiplica lo que le baja el humor.
        calm: Multiplica todo lo que le cambia el humor (sube y baja).
        absence: Multiplica las faltas.
        shift: Turno en el que rinde mejor (en el otro, peor).
        game: Minijuego en el que rinde mejor.
        loyal: No se va por el humor (solo si no cobra).
        part_time: Solo quiere media jornada.
        ask: Multiplica lo que pide de sueldo.
        start: Suma a sus habilidades al llegar.
    """

    key: str
    name: str
    text: str
    miss: float = 1.0
    speed: float = 1.0
    trato: float = 0.0
    learn: float = 1.0
    mood: float = 1.0
    calm: float = 1.0
    absence: float = 1.0
    shift: str = ""
    game: str = ""
    loyal: bool = False
    part_time: bool = False
    ask: float = 1.0
    start: float = 0.0


TRAITS: tuple[Trait, ...] = (
    Trait("madrugador", "Madrugador", "Rinde más de mañana y algo menos de tarde.",
          shift="manana"),
    Trait("noctambulo", "Noctámbulo", "Rinde más de tarde y algo menos de mañana.",
          shift="tarde"),
    Trait("simpatico", "Simpático", "Más trato con la clientela.", trato=15.0),
    Trait("despistado", "Despistado", "Se equivoca más.", miss=1.4),
    Trait("meticuloso", "Meticuloso", "Se equivoca menos, pero va más despacio.",
          miss=0.7, speed=0.85),
    Trait("nervio", "Puro nervio", "Va más rápido, pero se equivoca algo más.",
          miss=1.15, speed=1.25),
    Trait("estudiante", "Estudiante", "Aprende rápido; solo quiere media jornada y falta "
          "más en época de exámenes.", learn=1.6, absence=1.3, part_time=True, ask=0.9),
    Trait("veterano", "Veterano", "Llega sabiendo, aprende poco y pide más.",
          learn=0.6, ask=1.15, start=15.0),
    Trait("quejica", "Quejica", "Lo malo le baja más el humor.", mood=1.6),
    Trait("tranquilo", "Tranquilo", "Su humor cambia la mitad: ni lo bueno ni lo malo le "
          "afecta tanto.", calm=0.5),
    Trait("leal", "Leal", "No se va por estar quemado; solo si no cobra.", loyal=True),
    Trait("impuntual", "Impuntual", "Falta el doble.", absence=2.0),
    Trait("barista", "Barista", "Se equivoca menos en las comandas.", miss=1.0,
          game="comandas"),
    Trait("cuentas", "Buena con las cuentas", "Se equivoca menos en la caja.", game="caja"),
    Trait("manitas", "Manitas", "Se equivoca menos en el diagnóstico.", game="diagnostico"),
    Trait("ambicioso", "Ambicioso", "Aprende más, pero si gana menos de lo que vale, se "
          "quema antes.", learn=1.3, mood=1.3),
)  # fmt: skip
TRAIT_BY_KEY = {t.key: t for t in TRAITS}
#: Lo que cambia los fallos rendir en su turno o en el otro, y en su minijuego.
SHIFT_FIT = 0.8
SHIFT_MISFIT = 1.15
GAME_FIT = 0.75


@dataclass(frozen=True, slots=True)
class Profile:
    """De dónde viene un candidato (decide la bonificación de su contrato indefinido).

    Attributes:
        key: Clave estable.
        name: Nombre.
        ages: `(mínimo, máximo)` de su edad.
        weight: Lo que pesa al sortear los candidatos.
        bonus_eur_month: Bonificación de la cuota de la empresa por contratarle indefinido
            (RDL 1/2023), en euros al mes (0 si no hay).
        bonus_senior: La de quien tiene 45 años o más.
        bonus_years: Años que dura.
        law: El artículo, para el texto.
        bias: Lo que suma a cada habilidad al sortearlas.
    """

    key: str
    name: str
    ages: tuple[int, int]
    weight: float
    bonus_eur_month: float = 0.0
    bonus_senior: float = 0.0
    bonus_years: int = 0
    law: str = ""
    bias: tuple[float, float, float] = (0.0, 0.0, 0.0)


#: La de Garantía Juvenil (275 € al mes, DA 1.ª del RDL 1/2023) solo valió el primer año: el
#: joven no trae bonificación. El juego no mira el sexo ni ofrece los colectivos protegidos
#: (víctimas, exclusión social, discapacidad), que no son para un minijuego.
PROFILES: tuple[Profile, ...] = (
    Profile("general", "Con experiencia", (26, 55), 0.45),
    Profile("joven", "Joven", (18, 29), 0.30, bias=(-10.0, 10.0, -5.0)),
    Profile("parado", "Parado de larga duración", (30, 62), 0.25, 110.0, 128.0, 3,
            "art. 21 del RDL 1/2023", (5.0, -10.0, 5.0)),
)  # fmt: skip
#: Edad desde la que la bonificación del parado de larga duración es la mayor.
SENIOR_AGE = 45
#: Noches sin bonificaciones tras un despido improcedente de alguien bonificado (art. 11.4
#: del RDL 1/2023: 12 meses).
BONUS_BAN_NIGHTS = 7
PROFILE_BY_KEY = {p.key: p for p in PROFILES}


# -- Escalas --------------------------------------------------------------------------


def months_to_nights(months: float) -> int:
    """Noches que dura un plazo de `months` meses (el año son 7 días)."""
    return max(1, round(months * 7 / 12))


def night_amount(eur_year: float) -> int:
    """Lo que toca cada noche de una cifra de euros al año: su parte de un día, a 100 Y$ el
    euro (una noche de jornada completa es un día de trabajo)."""
    return round(eur_year / 365 * WAGE_YAPDOLLARS_PER_EURO)


def smi_night(jornada: int = FULL_DAY) -> int:
    """El salario mínimo de una noche para una jornada de `jornada` franjas."""
    return round(night_amount(SMI_EUR_YEAR) * jornada / FULL_DAY)


def fundae_week() -> int:
    """El crédito de formación de FUNDAE de una semana, en Y$ (sus siete días)."""
    return 7 * night_amount(FUNDAE_EUR_YEAR)


# -- Candidatos -----------------------------------------------------------------------


def _skills(profile: Profile, traits: tuple[Trait, ...], rng: random.Random) -> dict[str, float]:
    start = sum(t.start for t in traits)
    out = {}
    for skill, bias in zip(SKILLS, profile.bias, strict=True):
        out[skill] = float(max(5, min(90, round(rng.gauss(42, 14) + bias + start))))
    return out


#: Rasgos que van con la edad: `(clave, edad mínima, edad máxima)`.
TRAIT_AGES: tuple[tuple[str, int, int], ...] = (("estudiante", 18, 30), ("veterano", 35, 99))


def _traits(rng: random.Random, age: int) -> tuple[str, str]:
    # Los dos rasgos no se contradicen (nada de madrugador y noctámbulo a la vez) y van con
    # la edad (no hay estudiantes de 50 ni veteranos de 20).
    fits = [
        t
        for t in TRAITS
        if all(low <= age <= high for key, low, high in TRAIT_AGES if key == t.key)
    ]
    first = rng.choice(fits)
    rest = [t for t in fits if t.key != first.key and not (first.shift and t.shift)]
    return first.key, rng.choice(rest).key


def ask(skills: dict[str, float], traits: list[str] | tuple[str, ...], jornada: int) -> int:
    """Lo que pide de sueldo cada noche: el salario mínimo más lo que valen sus habilidades."""
    level = sum(skills.values()) / len(SKILLS) / 100
    factor = 1.0
    for key in traits:
        factor *= TRAIT_BY_KEY[key].ask
    # A decenas hacia arriba: nunca por debajo del salario mínimo.
    return _tens(smi_night(jornada) * (1 + 0.5 * level) * factor)


def _tens(amount: float) -> int:
    """A decenas hacia arriba."""
    return -(-round(amount) // 10) * 10


def role_ask(
    skills: dict[str, float], traits: list[str] | tuple[str, ...], jornada: int, role: str = ""
) -> int:
    """Lo que pide cada noche para un puesto: lo de un dependiente que sepa lo mismo, por lo
    que pide de más o de menos el puesto (`Role.premium`), a decenas hacia arriba y nunca por
    debajo del salario mínimo. El gerente, a jornada completa."""
    info = ROLE_BY_KEY.get(role, ROLES[0])
    if not info.schedule:
        jornada = FULL_DAY
    base = ask(skills, traits, jornada)
    if info.premium == 1.0:
        return base
    return max(_tens(smi_night(jornada)), _tens(base * info.premium))


def candidates_per_day(business: Any, day: date) -> int:
    """Cuántos candidatos llegan `day`: uno más si hay alguien de recursos humanos."""
    iso = day.isoformat()
    hr = any(e.get("role") == "rrhh" and e["start"] <= iso for e in business.staff)
    return CANDIDATES_PER_DAY + (HR_CANDIDATES if hr else 0)


def candidates(business: Any, day: date) -> list[dict[str, Any]]:
    """Los candidatos de hoy (siempre los mismos para el negocio y el día), sin los ya vistos.

    Cada uno: `key` (su semilla), `name`, `age`, `profile`, `traits`, `skills`, `ask` (a
    jornada completa; la media pide la mitad) y `part_time`. Con recursos humanos llega uno
    más, y los de siempre salen igual (se sortean antes).
    """
    rng = random.Random(f"{business.seed}:candidatos:{day.isoformat()}")
    out = []
    for _ in range(candidates_per_day(business, day)):
        key = rng.randrange(2**31)
        profile = rng.choices(PROFILES, weights=[p.weight for p in PROFILES])[0]
        age = rng.randint(*profile.ages)
        traits = _traits(rng, age)
        items = tuple(TRAIT_BY_KEY[k] for k in traits)
        skills = _skills(profile, items, rng)
        name = f"{rng.choice(STAFF_FIRST_NAMES)} {rng.choice(STAFF_LAST_NAMES)}"
        candidate = {
            "key": key,
            "name": name,
            "age": age,
            "profile": profile.key,
            "traits": list(traits),
            "skills": skills,
            "ask": ask(skills, traits, FULL_DAY),
            "part_time": any(t.part_time for t in items),
        }
        if key not in business.seen:
            out.append(candidate)
    return out


def new_employee(
    candidate: dict[str, Any],
    *,
    seq: int,
    contract: str,
    jornada: int,
    shift: str,
    wage: int,
    start: date,
    bonified: bool = True,
    role: str = "",
    franjas: tuple[int, ...] | None = None,
) -> dict[str, Any]:
    """Un empleado recién contratado (`Business.staff`), que empieza a trabajar `start`.

    Args:
        contract: `indefinido`, `temporal` o `b` (sin alta ni contrato).
        jornada: `FULL_DAY` o `HALF_DAY` franjas (si no se dan `franjas`).
        shift: `manana` o `tarde` (si no se dan `franjas`).
        wage: Bruto de cada noche.
        bonified: Si puede tener bonificación (al corriente con Hacienda y la Seguridad
            Social, art. 8 del RDL 1/2023, y sin despidos improcedentes de bonificados, art.
            11.4).
        role: La clave de su puesto (`ROLES`): `MANAGER` para el gerente, `""` para el
            dependiente.
        franjas: Las de su horario (`Horario.franjas`, 9.41.0); mandan sobre `jornada` y
            `shift`. El gerente no tiene.
    """
    if not ROLE_BY_KEY.get(role, ROLES[0]).schedule:
        franjas, jornada = (), FULL_DAY
    elif franjas is None:
        franjas = legacy_franjas(shift, jornada)
    else:
        jornada, shift = len(franjas), _horario(franjas).shift
    profile = PROFILE_BY_KEY[candidate["profile"]]
    bonus = 0
    bonus_until = ""
    if contract == "indefinido" and profile.bonus_eur_month and bonified:
        senior = candidate["age"] >= SENIOR_AGE
        monthly = profile.bonus_senior if senior else profile.bonus_eur_month
        bonus = night_amount(monthly * 12) * jornada // FULL_DAY
        bonus_until = (start + timedelta(days=7 * profile.bonus_years - 1)).isoformat()
    legal = contract != "b"
    return {
        "id": seq,
        "key": candidate["key"],
        "name": candidate["name"],
        "age": candidate["age"],
        "profile": candidate["profile"],
        "traits": list(candidate["traits"]),
        "skills": dict(candidate["skills"]),
        "humor": {"indefinido": 70.0, "temporal": 55.0, "b": 45.0}[contract],
        "contract": contract,
        "jornada": jornada,
        "shift": shift,
        "franjas": list(franjas),
        "wage": wage,
        "start": start.isoformat(),
        "trial_until": ((start + timedelta(days=TRIAL_NIGHTS - 1)).isoformat() if legal else ""),
        "until": (
            (start + timedelta(days=TEMPORARY_NIGHTS - 1)).isoformat()
            if contract == "temporal"
            else ""
        ),
        "bonus": bonus,
        "bonus_until": bonus_until,
        "absences": 0,
        "absent_day": "",
        "unpaid": 0,
        "unpaid_days": [],
        "nights": 0,
        "course": "",
        "turns": 0,
        "clients": 0,
        "sales": 0,
        "best": 0,
        "grades": [],
        "grades_day": "",
        "roll_day": "",
        "b_paid": 0,
        "role": role,
        # El horario que tendrá desde el día siguiente (`reschedule`), o nada.
        "next": {},
        # El último día en que atendió en una franja con el encargado de turno.
        "led_day": "",
    }


# -- Trabajar -------------------------------------------------------------------------


def is_manager(employee: dict[str, Any]) -> bool:
    """Si es el gerente del local (fase 5)."""
    return employee.get("role", "") == MANAGER


def manager_ask(skills: dict[str, float], traits: list[str] | tuple[str, ...]) -> int:
    """Lo que pide cada noche de gerente: lo de un dependiente a jornada completa, con lo que
    pide de más un encargado (`MANAGER_PREMIUM`), a decenas hacia arriba."""
    return role_ask(skills, traits, FULL_DAY, MANAGER)


def franjas(employee: dict[str, Any]) -> tuple[int, ...]:
    """Las franjas de su horario (el gerente, ninguna: no tiene horario).

    Los contratados antes de la 9.41.0 no las guardan: salen de su turno y su jornada.
    """
    if not role_of(employee).schedule:
        return ()
    stored = employee.get("franjas")
    if stored:
        return tuple(stored)
    return legacy_franjas(employee["shift"], employee["jornada"])


def horario_of(employee: dict[str, Any]) -> Horario | None:
    """Su horario (`None` para el gerente)."""
    own = franjas(employee)
    if not own:
        return None
    return HORARIO_BY_KEY.get(horario_key(own)) or _horario(own)


def plays(employee: dict[str, Any]) -> bool:
    """Si juega las franjas de su horario: atiende el mostrador o reparte."""
    return bool(role_of(employee).plays)


def works(employee: dict[str, Any], day: str, franja: int) -> bool:
    """Si le toca jugar la franja `franja` del día `day` (ISO): atiende o reparte, es de su
    horario, ya había empezado y, si entró ese día, la franja no había acabado al contratarle."""
    if not plays(employee) or franja not in franjas(employee) or employee["start"] > day:
        return False
    return employee["start"] != day or franja >= employee.get("from_franja", 0)


def strength(employee: dict[str, Any]) -> float:
    """La fuerza del efecto de un puesto de apoyo: su parte de la jornada completa por su
    habilidad (`Role.skill`: 0,5 con 0, 1 con 50 y 1,5 con 100)."""
    role = role_of(employee)
    share = len(franjas(employee)) / FULL_DAY if role.schedule else 1.0
    if not role.skill:
        return share
    return share * (0.5 + skill(employee, role.skill) / 100)


def lead(employee: dict[str, Any]) -> float:
    """La mano con la gente de un encargado de turno: 0,5 con trato 0, 1 con 50 y 1,5 con
    100. Lo que bajan los fallos de los demás es `LEAD_MISS` por esto."""
    return 0.5 + skill(employee, "trato") / 100


def present(employee: dict[str, Any], day: date) -> bool:
    """Si ha venido `day`: ya había empezado y no ha faltado (las faltas se deciden con su
    primera franja del día o, a quien no atiende, con `roll_absence`)."""
    iso = day.isoformat()
    return employee["start"] <= iso and employee["absent_day"] != iso


def roll_absence(employee: dict[str, Any], seed: int, day: date) -> bool:
    """Decide una vez al día si falta `day` y lo apunta (`absent_day`, `absences`). Devuelve
    si ha faltado hoy por esta tirada (la segunda vez del mismo día, `False`)."""
    iso = day.isoformat()
    if employee["roll_day"] == iso or employee["start"] > iso:
        return False
    employee["roll_day"] = iso
    if not absent(employee, seed, day):
        return False
    employee["absent_day"] = iso
    employee["absences"] += 1
    return True


@dataclass(frozen=True, slots=True)
class Support:
    """Lo que deja un día la plantilla de apoyo (los puestos que no atienden).

    Attributes:
        reputation: La que sube esa noche (limpieza).
        patience: Lo que suma a la paciencia de los clientes al día siguiente (limpieza).
        keep: La parte de la merma de esa noche que evita (mozo de almacén).
        clients: Lo que suma al día siguiente a los clientes y al cupo (community manager).
        shield: Lo que quita de lo malo del humor de los demás esa noche (RR. HH.).
        learn: Lo que suma a lo que aprenden los demás ese día (RR. HH.).
    """

    reputation: float = 0.0
    patience: float = 0.0
    keep: float = 0.0
    clients: float = 0.0
    shield: float = 0.0
    learn: float = 0.0


SUPPORT_FIELDS = frozenset(f.name for f in fields(Support))


def support(business: Any, day: date) -> Support:
    """Lo que deja `day` la plantilla de apoyo que ha venido, cada uno con su fuerza."""
    totals = dict.fromkeys(SUPPORT_FIELDS, 0.0)
    for employee in business.staff:
        role = role_of(employee)
        if not role.effects or not present(employee, day):
            continue
        k = strength(employee)
        for key, base in role.effects:
            totals[key] += base * k
    totals["shield"] = min(SHIELD_MAX, totals["shield"])
    return Support(**totals)


def delivery_key(sector: str) -> str:
    """La clave del cupo a domicilio de un sector en `business_demand` (aparte del de
    mostrador, y el mismo para todos los negocios del sector en el barrio)."""
    return f"{sector}:reparto"


def holder(business: Any, role: str) -> dict[str, Any] | None:
    """Quien tiene un puesto en el local, o `None`."""
    return next((e for e in business.staff if e.get("role", "") == role), None)


def hire_problem(business: Any, role: str) -> str | None:
    """Por qué no se puede contratar ahora a alguien de `role`, o `None` si se puede."""
    info = ROLE_BY_KEY.get(role)
    if info is None:
        return "Ese puesto no existe."
    if not info.fits(business.sector):
        return f"Un negocio como el tuyo no necesita {info.name.lower()}."
    if info.unique and holder(business, role) is not None:
        return f"Este local ya tiene {info.name.lower()}."
    room = cap(business.size)
    if info.seat and len(seated(business)) >= room:
        return (
            f"En un local {business.size_info.name.lower()} caben {room} empleados. "
            "Trasládate a uno mayor para contratar a más."
        )
    return None


def roles_for(business: Any) -> tuple[Role, ...]:
    """Los puestos que puede contratar el sector del negocio, en el orden del catálogo."""
    return tuple(r for r in ROLES if r.fits(business.sector))


def franja_hour(franja: int) -> int:
    """Hora canaria a la que empieza una franja."""
    return FIRST_HOUR + 2 * franja


def demand_hour(franja: int) -> int:
    """La hora que mira la demanda de una franja: la del medio."""
    return franja_hour(franja) + 1


def working(employee: dict[str, Any], day: date) -> bool:
    """Si trabaja `day`: ya ha empezado, no ha faltado y no está de curso."""
    today = day.isoformat()
    return employee["start"] <= today and employee["absent_day"] != today


def skill(employee: dict[str, Any], key: str) -> float:
    """Una habilidad, con lo que le suman sus rasgos (0-100)."""
    value = employee["skills"][key]
    if key == "trato":
        value += sum(TRAIT_BY_KEY[t].trato for t in employee["traits"])
    return max(0.0, min(100.0, value))


def humor_factor(humor: float) -> float:
    """Lo que multiplica sus fallos el humor: más, cuanto más quemado."""
    if humor < 20:
        return 1.6
    if humor < 40:
        return 1.3
    return 1.0


def miss_rate(employee: dict[str, Any], sector: Sector, franja: int) -> float:
    """La probabilidad de que se equivoque con un cliente."""
    rate = MISS_AT_ZERO - (MISS_AT_ZERO - MISS_AT_FULL) * skill(employee, "acierto") / 100
    for key in employee["traits"]:
        trait = TRAIT_BY_KEY[key]
        rate *= trait.miss
        if trait.shift:
            shift = "manana" if franja in SHIFTS["manana"] else "tarde"
            rate *= SHIFT_FIT if trait.shift == shift else SHIFT_MISFIT
        if trait.game and trait.game == sector.game:
            rate *= GAME_FIT
    return max(0.0, min(0.9, rate * humor_factor(employee["humor"])))


def click_seconds(employee: dict[str, Any]) -> float:
    """Lo que tarda en cada pulsación."""
    seconds = CLICK_AT_ZERO - (CLICK_AT_ZERO - CLICK_AT_FULL) * skill(employee, "rapidez") / 100
    for key in employee["traits"]:
        seconds /= TRAIT_BY_KEY[key].speed
    return seconds


def _quote(employee: dict[str, Any], luxury: bool) -> int:
    """El presupuesto que da en el taller (índice de `QUOTES`)."""
    trato = skill(employee, "trato")
    if trato >= 70 and luxury:
        return 2
    return 0 if trato < 35 else 1


def play_turn(
    business: Any,
    employee: dict[str, Any],
    clients: list[Client],
    *,
    start: float,
    franja: int,
    lead: float = 0.0,
) -> TurnGame:
    """Juega un turno entero del empleado con el minijuego del sector y lo devuelve acabado.

    Es el mismo `TurnGame` del dueño: la cola, la paciencia, el cambio, el diagnóstico y el
    presupuesto. El azar de sus fallos sale del turno (`start`, la franja y el empleado), así
    que el mismo turno da siempre lo mismo.

    Args:
        lead: La mano del encargado de turno que trabaja esa franja con él (`lead`; 0 si no
            hay): sus fallos bajan `LEAD_MISS` por ella.
    """
    game = TurnGame(business, clients, now=start)
    rng = random.Random(f"{business.seed}:{employee['id']}:{start}:{franja}")
    miss = miss_rate(employee, game.sector, franja) * (1 - LEAD_MISS * lead)
    click = click_seconds(employee)
    trato = skill(employee, "trato") / 100
    quote = _quote(employee, game.luxury)
    now = start
    while not game.finished(now):
        client = game.current
        assert client is not None
        wrong = rng.random() < miss
        kind = game.sector.game
        if client.usual and rng.random() >= 0.3 + 0.7 * trato:
            now += 3 * click  # no se acuerda de lo de siempre: tiene que preguntarlo
        if kind in AUTOPLAY_GAMES:  # los minijuegos de la 9.48.0
            now = autoplay(game, now, click=click, wrong=wrong, rng=rng)
            continue
        if kind == "comandas":
            order = dict(client.order)
            if wrong:  # sirve otra cosa
                key = next(iter(order))
                order[key] += 1
            for key, n in order.items():
                for _ in range(n):
                    now += click
                    game.press(f"p:{key}", now)
            game.press("serve", now)
        elif kind == "caja":
            if client.hidden and rng.random() < 0.2 + 0.8 * trato:
                game.press("thief", now)
            due = game.due() + (rng.choice((50, -50)) if wrong else 0)
            for coin in COINS:
                while due >= coin:
                    now += click
                    game.press(f"c:{coin}", now)
                    due -= coin
            game.press("charge", now)
        else:
            fault = client.fault
            if wrong:
                fault = next(k for k in client.options if k != client.fault)
            now += 3 * click
            game.press(f"d:{fault}", now)
            if game.current is client and game.step == "presupuesto":
                game.press(f"q:{quote}", now)
            elif game.current is client:  # sin el recambio
                game.press("nopart", now)
        now += click
    return game


def learn(employee: dict[str, Any], turns: int = 1, boost: float = 0.0) -> None:
    """Lo que aprende trabajando `turns` turnos: cada habilidad, sobre lo que le falta. `boost`
    es lo que suma recursos humanos (`Support.learn`)."""
    factor = 1.0 + boost
    for key in employee["traits"]:
        factor *= TRAIT_BY_KEY[key].learn
    for key in SKILLS:
        value = employee["skills"][key]
        for _ in range(turns):
            value += LEARN_PER_TURN * factor * (100 - value)
        employee["skills"][key] = round(min(100.0, value), 2)


# -- Humor, faltas y marcharse ------------------------------------------------------------


#: Lo que cobra frente a lo que vale por debajo de lo cual se queja.
WORTH_SLACK = 0.95


def worth(employee: dict[str, Any]) -> int:
    """Lo que pediría hoy por lo que sabe, en su puesto y a su jornada."""
    return role_ask(
        employee["skills"], employee["traits"], employee["jornada"], employee.get("role", "")
    )


def mood_change(employee: dict[str, Any], delta: float, *, shield: float = 0.0) -> float:
    """Aplica un cambio de humor con sus rasgos y devuelve el humor nuevo (0-100).

    `shield` es lo que quita recursos humanos de lo malo (`Support.shield`).
    """
    for key in employee["traits"]:
        trait = TRAIT_BY_KEY[key]
        delta *= trait.calm
        if delta < 0:
            delta *= trait.mood
    if delta < 0:
        delta *= 1 - min(SHIELD_MAX, shield)
    employee["humor"] = round(max(0.0, min(100.0, employee["humor"] + delta)), 2)
    return employee["humor"]


def night_mood(
    employee: dict[str, Any],
    *,
    paid: bool,
    grades: list[str],
    shield: float = 0.0,
    led: bool = False,
) -> float:
    """Lo que le cambia el humor una noche: el sueldo frente a lo que vale, sus turnos de hoy,
    si ha cobrado, la jornada partida (`PARTIDA_MOOD`) y si trabajó con el encargado de turno
    (`led`, `LEAD_MOOD`). Devuelve el cambio (antes de los rasgos y de `shield`)."""
    delta = 0.0
    schedule = horario_of(employee)
    if schedule is not None and schedule.kind == "partida":
        delta += PARTIDA_MOOD
    if led:
        delta += LEAD_MOOD
    value = worth(employee)
    if employee["wage"] >= value * 1.1:
        delta += 2.0
    elif employee["wage"] >= value:
        delta += 1.0
    elif employee["wage"] < value * WORTH_SLACK:
        # Aprende cada turno, así que siempre vale algo más: se queja con un 5 % de diferencia.
        delta -= 2.0 + 6.0 * min(1.0, (value - employee["wage"]) / value)
    delta += sum({"S": 1.0, "A": 0.5, "D": -1.0}.get(g, 0.0) for g in grades)
    if employee["contract"] == "b":
        delta -= 1.0
    if not paid:
        delta -= 20.0
    mood_change(employee, delta, shield=shield)
    return delta


# -- Cambiar el horario (9.41.0) --------------------------------------------------------

#: Lo que le cambia el humor que le cambies el horario: con las mismas horas (una
#: modificación sustancial, art. 41 ET), con más (más sueldo) o con menos (si acepta).
RESCHEDULE_MOOD = {"igual": -3.0, "mas": 3.0, "menos": -6.0}
#: Humor desde el que acepta trabajar menos horas: pasar a tiempo parcial es voluntario
#: (art. 12.4.e ET) y quemado no acepta cobrar menos.
FEWER_HOURS_HUMOR = 50.0


class Refusal(ValueError):
    """No acepta el cambio de horario: menos horas con el humor por los suelos."""


@dataclass(frozen=True, slots=True)
class Reschedule:
    """Un cambio de horario que vale desde el día siguiente.

    Attributes:
        horario: El horario nuevo.
        wage: El bruto de cada noche con él (el de cada franja se mantiene).
        kind: `igual`, `mas` o `menos` horas.
        start: Día (ISO) desde el que vale.
    """

    horario: Horario
    wage: int
    kind: str
    start: str


def reschedule(employee: dict[str, Any], key: str, day: date) -> Reschedule:
    """El cambio de su horario al de clave `key`, que vale desde el día siguiente a `day` (el
    preaviso del art. 41.3 ET, a la escala del juego).

    El bruto de cada franja se mantiene: con más horas cobra más y con menos, menos, pero nunca
    menos de lo que pediría por esa jornada (`worth`).

    Raises:
        ValueError: Si es el gerente, el horario no existe o es el mismo o si solo quiere
            media jornada; `Refusal` si no acepta trabajar menos (quemado).
    """
    name = employee["name"].split()[0]
    current = horario_of(employee)
    if current is None:
        raise ValueError("El gerente no tiene horario: va a jornada completa.")
    new = HORARIO_BY_KEY.get(key)
    if new is None:
        raise ValueError("Ese horario no está en la lista.")
    if new.key == current.key:
        raise ValueError(f"{name} ya trabaja en ese horario.")
    if new.jornada != HALF_DAY and any(TRAIT_BY_KEY[t].part_time for t in employee["traits"]):
        raise ValueError(f"{name} solo quiere media jornada.")
    kind = (
        "igual"
        if new.jornada == current.jornada
        else "mas"
        if new.jornada > current.jornada
        else "menos"
    )
    if kind == "menos" and employee["humor"] < FEWER_HOURS_HUMOR:
        raise Refusal(
            f"{name} no acepta trabajar menos horas: con lo quemado que está, no quiere cobrar "
            "menos. Pasar a menos horas es voluntario (art. 12.4.e ET)."
        )
    wage = employee["wage"]
    if kind != "igual":
        floor = role_ask(
            employee["skills"], employee["traits"], new.jornada, employee.get("role", "")
        )
        wage = max(floor, _tens(employee["wage"] * new.jornada / current.jornada))
    start = (day + timedelta(days=1)).isoformat()
    return Reschedule(new, wage, kind, start)


def plan_reschedule(employee: dict[str, Any], change: Reschedule) -> None:
    """Apunta el cambio (`employee["next"]`) y le cambia el humor ya: se entera hoy."""
    employee["next"] = {
        "franjas": list(change.horario.franjas),
        "wage": change.wage,
        "from": change.start,
    }
    mood_change(employee, RESCHEDULE_MOOD[change.kind])


def apply_reschedule(employee: dict[str, Any], day: date) -> bool:
    """Si su horario nuevo empieza `day` o antes, lo pone (franjas, jornada, turno, sueldo y
    la bonificación, que va por horas). Devuelve si lo ha puesto."""
    change = employee.get("next") or {}
    if not change or change["from"] > day.isoformat():
        return False
    new = _horario(tuple(change["franjas"]))
    old = len(franjas(employee)) or FULL_DAY
    if employee["bonus"]:
        employee["bonus"] = employee["bonus"] * new.jornada // old
    employee["franjas"] = list(new.franjas)
    employee["jornada"] = new.jornada
    employee["shift"] = new.shift
    employee["wage"] = change["wage"]
    employee["next"] = {}
    return True


def absent(employee: dict[str, Any], seed: int, day: date) -> bool:
    """Si falta `day` sin justificar (decidido por el día: siempre lo mismo)."""
    chance = 0.15 if employee["humor"] < 30 else 0.02
    for key in employee["traits"]:
        chance *= TRAIT_BY_KEY[key].absence
    return random.Random(f"{seed}:{employee['id']}:falta:{day.isoformat()}").random() < chance


def unpaid_recent(employee: dict[str, Any], day: date) -> int:
    """Noches sin cobrar en la última semana (el año del juego)."""
    since = (day - timedelta(days=6)).isoformat()
    return sum(1 for d in employee["unpaid_days"] if d >= since)


def quits(employee: dict[str, Any], seed: int, day: date) -> bool:
    """Si se va esta noche: quemado (y no es leal) o sin cobrar (art. 50.1.b ET)."""
    if unpaid_recent(employee, day) >= UNPAID_NIGHTS_TO_LEAVE:
        return True
    loyal = any(TRAIT_BY_KEY[k].loyal for k in employee["traits"])
    if loyal or employee["humor"] >= 15:
        return False
    return random.Random(f"{seed}:{employee['id']}:marcha:{day.isoformat()}").random() < 0.3


# -- La nómina ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StaffSlip:
    """La nómina de una noche de un empleado, en Y$.

    Attributes:
        gross: Bruto.
        ss_worker: Su cotización (sale del bruto).
        ss_employer: La de la empresa, ya con la bonificación restada.
        irpf: La retención del IRPF (sale del bruto).
        bonus: La bonificación de la cuota de la empresa (RDL 1/2023).
    """

    gross: int
    ss_worker: int = 0
    ss_employer: int = 0
    irpf: int = 0
    bonus: int = 0

    @property
    def net(self) -> int:
        """Lo que le llega al empleado."""
        return self.gross - self.ss_worker - self.irpf

    @property
    def cost(self) -> int:
        """Lo que le cuesta a la empresa (bruto y su cotización)."""
        return self.gross + self.ss_employer

    @property
    def to_state(self) -> int:
        """Lo que va al Estado: las dos cotizaciones y la retención."""
        return self.ss_worker + self.ss_employer + self.irpf


def retention_rate(gross_night: int, contract: str) -> float:
    """El tipo de retención de su sueldo (arts. 82 a 86 RIRPF), con su sueldo del año como
    única renta: la cuota de una semana con siete noches de nómina, sobre el bruto."""
    weekly = gross_night * 7
    if weekly <= 0:
        return 0.0
    social = round(weekly * WORKER_RATES.get(contract, WORKER_RATES["indefinido"]))
    return week_tax(WeekIncome(work=weekly, social=social)).quota / weekly


def payslip(employee: dict[str, Any], sector: str, day: date, *, docked: bool = False) -> StaffSlip:
    """La nómina de la noche de `day`.

    Args:
        docked: Si faltó ese día sin justificar: no cobra el día.
    """
    gross = 0 if docked else employee["wage"]
    if gross <= 0:
        return StaffSlip(0)
    if employee["contract"] == "b":
        return StaffSlip(gross)
    kind = employee["contract"]
    worker = round(gross * WORKER_RATES[kind])
    employer = round(gross * (EMPLOYER_RATES[kind] + ACCIDENT_RATES.get(sector, 0.015)))
    bonus = (
        min(employer, employee["bonus"])
        if employee["bonus"] and day.isoformat() <= employee["bonus_until"]
        else 0
    )
    irpf = round(gross * retention_rate(gross, kind))
    return StaffSlip(gross, worker, employer - bonus, min(irpf, gross - worker), bonus)


# -- Contratos y despidos -------------------------------------------------------------


def in_trial(employee: dict[str, Any], day: date) -> bool:
    """Si sigue en el período de prueba."""
    return bool(employee["trial_until"]) and day.isoformat() <= employee["trial_until"]


def severance(employee: dict[str, Any], kind: str) -> int:
    """La indemnización de un despido de clase `kind` (`fin`, `objetivo` o `improcedente`).

    Días de salario por año trabajado (art. 49.1.c, 53.1.b y 56.1 ET), con el año en 7
    noches: `días × bruto de la noche × noches / 365`, con su tope en mensualidades. El
    objetivo suma los 15 días de preaviso, pagados.
    """
    days = SEVERANCE_DAYS.get(kind, 0)
    if not days:
        return 0
    wage = employee["wage"]
    amount = days * wage * employee["nights"] / 365
    if (cap := SEVERANCE_CAP_MONTHS.get(kind)) is not None:
        amount = min(amount, cap / 12 * 7 * wage)
    if kind == "objetivo":
        amount += NOTICE_DAYS * 7 * wage / 365
    return round(amount)


@dataclass(frozen=True, slots=True)
class Dismissal:
    """Una forma de despedir a un empleado.

    Attributes:
        kind: `prueba`, `disciplinario`, `objetivo`, `improcedente` o `b` (sin contrato).
        name: Cómo se llama.
        amount: Lo que hay que pagarle.
        law: El artículo.
        why: Por qué se puede (o por qué no).
        allowed: Si se puede ahora.
    """

    kind: str
    name: str
    amount: int
    law: str
    why: str
    allowed: bool = True


def dismissals(employee: dict[str, Any], day: date, *, losses: bool) -> list[Dismissal]:
    """Las formas de despedirle hoy, de la más barata a la más cara.

    Args:
        losses: Si el negocio perdió dinero la semana pasada (causa económica, art. 52.c ET).
    """
    if employee["contract"] == "b":
        return [Dismissal("b", "Que no vuelva", 0, "", "No tiene contrato: no hay despido.")]
    if in_trial(employee, day):
        return [
            Dismissal(
                "prueba", "No supera el período de prueba", 0, "art. 14.2 ET",
                "En el período de prueba se acaba sin indemnización.",
            )
        ]  # fmt: skip
    out = [
        Dismissal(
            "disciplinario", "Despido disciplinario", 0, "art. 54.2.a ET",
            f"Con {ABSENCES_FOR_DISCIPLINE} faltas sin justificar es procedente: sin "
            "indemnización.",
            employee["absences"] >= ABSENCES_FOR_DISCIPLINE,
        ),
        Dismissal(
            "objetivo", "Despido objetivo", severance(employee, "objetivo"), "art. 52.c y 53 ET",
            "Por causas económicas: el negocio perdió dinero la semana pasada. 20 días por año "
            "y 15 de preaviso.",
            losses,
        ),
        Dismissal(
            "improcedente", "Despido improcedente", severance(employee, "improcedente"),
            "art. 56.1 ET", "Sin causa: 33 días por año.",
        ),
    ]  # fmt: skip
    return out


def bonus_refund(employee: dict[str, Any], day: date) -> int:
    """Lo bonificado que hay que devolver, con recargo, si se le despide de forma improcedente
    antes de los tres años (arts. 9 y 13 del RDL 1/2023). El despido objetivo o disciplinario,
    la dimisión y el período de prueba no obligan a devolverlo."""
    if not employee["bonus"] or day.isoformat() > employee["bonus_until"]:
        return 0
    return round(employee["bonus"] * employee["nights"] * (1 + LATE_SURCHARGE))


def fine(workers: int) -> int:
    """La multa de la Inspección por `workers` trabajadores sin alta (art. 40.1.e LISOS)."""
    if workers <= 0:
        return 0
    raise_ = FINE_RAISE[min(workers, len(FINE_RAISE) - 1)]
    return round(workers * FINE_PER_WORKER * (1 + raise_))


def cap(size: int) -> int:
    """Empleados como mucho en un local de tamaño `size` (sin contar al gerente)."""
    return STAFF_CAP[max(0, min(len(STAFF_CAP) - 1, size))]


def seated(business: Any) -> list[dict[str, Any]]:
    """Los que ocupan un puesto del local (`cap`): todos menos el gerente."""
    return [e for e in business.staff if role_of(e).seat]


def manager(business: Any) -> dict[str, Any] | None:
    """El gerente del local, o `None`."""
    return next((e for e in business.staff if is_manager(e)), None)


# -- La noche: la nómina de todos y la Inspección -------------------------------------


@dataclass(frozen=True, slots=True)
class Payroll:
    """Las nóminas de una noche de toda la plantilla.

    Attributes:
        slips: `(empleado, nómina)` de quien estaba contratado ese día, en el orden de la
            plantilla.
    """

    slips: tuple[tuple[dict[str, Any], StaffSlip], ...]

    def _legal(self) -> list[StaffSlip]:
        return [s for e, s in self.slips if e["contract"] != "b"]

    @property
    def net(self) -> int:
        """Lo que cobran en mano quienes tienen contrato."""
        return sum(s.net for s in self._legal())

    @property
    def black(self) -> int:
        """Lo que cobran en B quienes no tienen alta."""
        return sum(s.gross for e, s in self.slips if e["contract"] == "b")

    @property
    def ss(self) -> int:
        """Las dos cotizaciones (la del trabajador sale de su bruto)."""
        return sum(s.ss_worker + s.ss_employer for s in self._legal())

    @property
    def irpf(self) -> int:
        """Las retenciones del IRPF."""
        return sum(s.irpf for s in self._legal())

    @property
    def state(self) -> int:
        """Lo que va al Estado: cotizaciones y retenciones."""
        return self.ss + self.irpf

    @property
    def cost(self) -> int:
        """El gasto de personal de la noche: brutos y cuota de la empresa (sin el B)."""
        return sum(s.cost for s in self._legal())

    @property
    def bonus(self) -> int:
        """Lo bonificado de la cuota de la empresa."""
        return sum(s.bonus for s in self._legal())


def payroll(business: Any, day: date) -> Payroll:
    """Las nóminas de la noche de `day`: de quien ya había empezado; quien faltó no cobra
    el día."""
    today = day.isoformat()
    return Payroll(
        tuple(
            (e, payslip(e, business.sector, day, docked=e["absent_day"] == today))
            for e in business.staff
            if e["start"] <= today
        )
    )


def unregistered(business: Any, day: date) -> list[dict[str, Any]]:
    """Quienes trabajan sin alta (`b`) desde antes de `day` o ese mismo día."""
    today = day.isoformat()
    return [e for e in business.staff if e["contract"] == "b" and e["start"] <= today]


def inspection(business: Any, day: date, tonight: int) -> tuple[int, int] | None:
    """Si la Inspección de Trabajo pasa esa noche y pilla a alguien sin alta: `(multa, cuotas)`.

    La multa es la de la LISOS por cada uno (`fine`). Las cuotas son las de todo lo que se
    les pagó en B, con `tonight` (empresa y trabajador, art. 18.2 LGSS: se cotiza desde el
    primer día), con el recargo del 20 % (art. 30.1.b LGSS). Decidido por el día: siempre lo
    mismo. `None` si no pasa o no hay nadie en B.
    """
    workers = unregistered(business, day)
    if not workers:
        return None
    chance = INSPECTION_TIPOFF if business.tipoff == day.isoformat() else INSPECTION_NIGHT
    if random.Random(f"{business.seed}:inspeccion:{day.isoformat()}").random() >= chance:
        return None
    rate = (
        WORKER_RATES["indefinido"]
        + EMPLOYER_RATES["indefinido"]
        + ACCIDENT_RATES.get(business.sector, 0.015)
    )
    paid = sum(e.get("b_paid", 0) for e in workers) + tonight
    return fine(len(workers)), round(paid * rate * (1 + LATE_SURCHARGE))


def regularize(employee: dict[str, Any]) -> None:
    """Le da de alta tras la Inspección: pasa a fijo (lo era desde el primer día, art. 15.4 ET),
    sin período de prueba."""
    employee["contract"] = "indefinido"
    employee["trial_until"] = ""
    employee["until"] = ""
    mood_change(employee, 10.0)


def franjas_done(hour: int) -> int:
    """Cuántas franjas del día han acabado a la hora canaria `hour` (de 0 a 8)."""
    return max(0, min(len(SHIFTS["manana"]) * 2, (hour - FIRST_HOUR) // 2))
