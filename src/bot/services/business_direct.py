"""Los dos juegos del dueño que no son atender: Dirigir el turno y el Consejo semanal.

Lógica pura, sin Discord ni dinero. Los dos reutilizan el motor de los minijuegos de `pala`
(`bot.services.work_games.MiniGame`: botones por `Round`, reloj, nota de 0 a 100 y órdago
final), así que el panel del negocio los pinta igual.

- **Dirigir** (`DirectGame`): con dos o más empleados que atienden, el dueño puede gastar
  una franja en repartir las tareas del turno entre ellos en vez de atender él. Cada tarea
  pide una habilidad (acierto, rapidez o trato) y cada empleado coge dos como mucho. La
  nota es lo bien que casan tareas y habilidades frente al mejor reparto posible, y lo que
  queda de día la plantilla falla menos (`direction_lead`, que se suma a la mano del
  encargado de turno en `business_staff.play_turn`).
- **Consejo** (`council_game`): quien tiene una sociedad o varios locales celebra un
  consejo por semana: un Despacho (`work_games.DeskGame`) con las barras de un negocio. Su
  nota mueve los clientes de todos sus locales lo que queda de semana
  (`business.council_boost`).

Los textos informan y van en normal (sección «Personalidad» de `Biblia.txt`).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from bot.services.work import Mechanic
from bot.services.work_games import (
    GRACE_SECONDS,
    ORDAGO_OFFER,
    DeskGame,
    MiniGame,
    Round,
    desk_cards,
)
from bot.services.work_rotation import DeskPack

#: Habilidades de un empleado (`business_staff.skill`) y cómo se enseñan.
SKILLS: dict[str, str] = {"acierto": "🎯", "rapidez": "⚡", "trato": "😊"}
#: Tareas que coge cada empleado como mucho y tareas de un turno como mucho.
DIRECT_LOAD = 2
DIRECT_TASKS = 6
#: Empleados que entran en el reparto como mucho (caben en una fila de botones).
DIRECT_STAFF = 4
#: Segundos para dirigir un turno.
DIRECT_SECONDS = 75
#: Lo que suma a la mano del encargado dirigir con un 100 (`business_staff.lead`).
DIRECT_LEAD = 1.0
#: La mano con la gente más alta que cuenta entre el encargado y el dueño.
LEAD_CAP = 1.5
#: Tareas de un turno, por la habilidad que piden.
TASKS: dict[str, tuple[str, ...]] = {
    "acierto": (
        "🧮 Cuadrar la caja",
        "📦 Contar el pedido del mayorista",
        "🧾 Repasar las cuentas del día",
        "🏷️ Cambiar los precios del cartel",
        "🔧 Revisar la máquina que falla",
        "📋 Hacer el inventario",
    ),
    "rapidez": (
        "⚡ La hora punta",
        "🚚 Descargar el camión",
        "🧹 Recoger antes de cerrar",
        "📞 Los pedidos por teléfono",
        "🧺 Reponer lo que se acaba",
        "🏃 Un recado urgente",
    ),
    "trato": (
        "😤 Un cliente con una queja",
        "👵 La habitual que quiere charla",
        "🆕 Enseñar al nuevo",
        "🤝 Atender al proveedor",
        "📝 Contestar las reseñas",
        "🎂 Un encargo especial",
    ),
}

#: Segundos del Consejo. Lo que mueve los clientes de la semana está en
#: `business.council_boost`.
COUNCIL_SECONDS = 90
COUNCIL_PACK = DeskPack(
    "🏛️ Consejo de la semana: tus locales, tus cuentas y cuatro frentes que no se pueden "
    "caer a la vez.",
    ("💰 Caja", "👥 Plantilla", "⭐ Clientes", "🧾 Hacienda"),
    (
        (
            "La plantilla pide revisar los sueldos.",
            (("Subirlos", 1, 0), ("Dar un día libre", 1, 2), ("Dejarlo para luego", 0, 1)),
        ),
        (
            "El asesor propone una forma «creativa» de apuntar los gastos.",
            (("Hacerle caso", 0, 3), ("Hacerlo por el libro", 3, 0), ("Cambiar de asesor", 3, 1)),
        ),
        (
            "Un proveedor ofrece género más barato y algo peor.",
            (("Comprarlo", 0, 2), ("Seguir con el bueno", 2, 0), ("Mezclar los dos", 0, 1)),
        ),
        (
            "Hay cola en la puerta todos los sábados.",
            (("Reforzar el turno", 2, 0), ("Que hagan horas", 2, 1), ("Subir precios", 0, 2)),
        ),
        (
            "Llega un requerimiento por un trimestre mal presentado.",
            (("Pagar y callar", 3, 0), ("Recurrirlo", 0, 3), ("Que lo vea la gestoría", 3, 1)),
        ),
        (
            "Las reseñas de esta semana han sido flojas.",
            (
                ("Invitar a volver", 2, 0),
                ("Formar al equipo", 1, 0),
                ("Contestarlas todas", 2, 1),
            ),
        ),
        (
            "Un empleado bueno tiene una oferta de la competencia.",
            (("Igualársela", 1, 0), ("Dejarle ir", 0, 1), ("Prometerle un ascenso", 1, 2)),
        ),
        (
            "Sobra caja este mes.",
            (("Reformar el local", 2, 0), ("Guardarla", 0, 1), ("Adelantar impuestos", 3, 0)),
        ),
        (
            "La Inspección de Trabajo pide los cuadrantes.",
            (("Enseñarlos tal cual", 3, 1), ("Ordenarlos antes", 3, 0), ("Pedir más plazo", 1, 3)),
        ),
        (
            "Una campaña de publicidad cuesta más de lo previsto.",
            (("Pagarla entera", 2, 0), ("Recortarla", 0, 2), ("Hacerla en redes", 2, 1)),
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class Hand:
    """Un empleado en el reparto de Dirigir.

    Attributes:
        name: Su nombre.
        skills: Sus tres habilidades (0-100), por clave de `SKILLS`.
    """

    name: str
    skills: dict[str, float]


@dataclass(slots=True)
class DirectGame(MiniGame):
    """Dirigir un turno: dar cada tarea al empleado que mejor la hace.

    `index` cuenta las jugadas, como en los minijuegos de rotación de `pala`; `rounds` va
    vacío. La nota es la suma de la habilidad pedida de quien coge cada tarea frente a la
    del mejor reparto (`best_total`).

    Attributes:
        hands: Los empleados, en el orden de los botones.
        tasks: Las tareas del turno: `(texto, habilidad)`.
        given: A quién se ha dado cada tarea ya repartida (índice de `hands`).
        best_total: Lo que suma el mejor reparto posible.
    """

    hands: list[Hand] = field(default_factory=list)
    tasks: list[tuple[str, str]] = field(default_factory=list)
    given: list[int] = field(default_factory=list)
    best_total: float = 1.0

    @property
    def max_points(self) -> int:
        """Lo que suma el mejor reparto."""
        return max(1, round(self.best_total))

    def load(self, hand: int) -> int:
        """Tareas que lleva ya ese empleado."""
        return self.given.count(hand)

    def _free(self) -> list[int]:
        """Empleados a los que aún les cabe una tarea."""
        return [i for i in range(len(self.hands)) if self.load(i) < DIRECT_LOAD]

    @property
    def current(self) -> Round | None:
        """La plantilla con sus habilidades, la tarea en juego y las que vienen."""
        if self.ordago == ORDAGO_OFFER:
            return self._ordago_round()
        turn = len(self.given)
        if turn >= len(self.tasks):
            return None
        lines = [
            f"👤 **{hand.name}** · "
            + " ".join(f"{emoji} {round(hand.skills[key])}" for key, emoji in SKILLS.items())
            + f" · {self.load(i)}/{DIRECT_LOAD} tareas"
            for i, hand in enumerate(self.hands)
        ]
        text, need = self.tasks[turn]
        lines.append(f"**Tarea {turn + 1}/{len(self.tasks)}:** {text} · pide {SKILLS[need]} {need}")
        if queue := self.tasks[turn + 1 :]:
            lines.append("**Después:** " + " · ".join(f"{t} {SKILLS[k]}" for t, k in queue))
        return Round("\n".join(lines), [self.hands[i].name for i in self._free()], [])

    def press(self, option: int, now: float) -> bool:
        """Da la tarea en juego al empleado del botón `option`."""
        current = self.current
        if current is None or self.finished_at is not None:
            return False
        if now > self.deadline + GRACE_SECONDS:
            self.finish(now)
            return False
        if self.ordago == ORDAGO_OFFER:
            return self._press_ordago(option, now)
        free = self._free()
        if not 0 <= option < len(free):
            return False
        self.index += 1
        hand = free[option]
        text, need = self.tasks[len(self.given)]
        value = self.hands[hand].skills[need]
        top = max(self.hands[i].skills[need] for i in free)
        self.given.append(hand)
        self.points += round(value)
        hit = value >= top
        self.correct += hit
        self._count(hit)
        self.last = f"✅ {text} → {self.hands[hand].name} ({SKILLS[need]} {round(value)})"
        if len(self.given) == len(self.tasks):
            self._done(now)
        return hit

    def score(self) -> int:
        """Lo repartido frente al mejor reparto, más el extra de tiempo."""
        base = 100 * min(self.points, self.best_total) / self.best_total
        return max(0, min(100, round(base + self._time_bonus())))

    def best(self) -> int:
        """Botón del mejor reparto desde aquí (pruebas)."""
        if self.ordago == ORDAGO_OFFER:
            return 0
        free = self._free()
        totals = [
            _best_from(self.hands, self.tasks, [*self.given, hand]) + self.hands[hand].skills[need]
            for hand in free
            for need in [self.tasks[len(self.given)][1]]
        ]
        return totals.index(max(totals))


def _best_from(hands: list[Hand], tasks: list[tuple[str, str]], given: list[int]) -> float:
    """Lo más que suman las tareas que quedan, con lo ya repartido en `given`."""
    turn = len(given)
    if turn == len(tasks):
        return 0.0
    need = tasks[turn][1]
    return max(
        hands[hand].skills[need] + _best_from(hands, tasks, [*given, hand])
        for hand in range(len(hands))
        if given.count(hand) < DIRECT_LOAD
    )


def direct_game(hands: list[Hand], rng: random.Random, *, now: float) -> DirectGame:
    """Prepara un turno de Dirigir con esos empleados (dos como poco)."""
    hands = hands[:DIRECT_STAFF]
    count = min(DIRECT_TASKS, DIRECT_LOAD * len(hands))
    needs = [rng.choice(list(TASKS)) for _ in range(count)]
    tasks = [(rng.choice(TASKS[need]), need) for need in needs]
    return DirectGame(
        Mechanic.PLAN,
        "🧭 Hoy diriges tú: da cada tarea a quien mejor la hace. Cada uno coge "
        f"{DIRECT_LOAD} como mucho.",
        [],
        DIRECT_SECONDS,
        now,
        hands=hands,
        tasks=tasks,
        best_total=_best_from(hands, tasks, []),
    )


def direction_lead(score: int) -> float:
    """Lo que suma a la mano del encargado haber dirigido el turno con esa nota."""
    return round(DIRECT_LEAD * max(0, min(100, score)) / 100, 2)


def council_game(rng: random.Random, *, now: float) -> DeskGame:
    """Prepara el Consejo de la semana: un Despacho con las barras de un negocio."""
    cards = desk_cards(rng, COUNCIL_PACK)
    return DeskGame(
        Mechanic.DESK, COUNCIL_PACK.scene, [], COUNCIL_SECONDS, now, pack=COUNCIL_PACK, cards=cards
    )
