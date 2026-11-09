"""Minijuegos del trabajo (`pala`): cavar, detectar, memoria, diálogo, clasificar y calcular.

Lógica pura: generan las rondas a partir del contenido de
`bot.services.work_catalog`, reciben pulsaciones y calculan la puntuación. El
cog solo dibuja botones y llama a `MiniGame.press`.

Son juegos de decidir bien con un reloj global, no de reflejos: Discord
tarda de 100 a 500 ms en ir y volver por cada pulsación y ese retraso varía,
así que un juego de pulsar a tiempo sería injusto. La velocidad solo da un
pequeño extra (`TIME_BONUS`) si se acaban todas las rondas antes de tiempo.

Puntuación (0–100): aciertos sobre el máximo posible, más el extra de tiempo.

Las herramientas de curro (`bot.services.work_tools`) cambian algo la partida:
más tiempo, un fallo gratis (se repite la jugada), el seguro que hace que
romper algo al cavar no reste y la chuleta que tacha una respuesta mala.

- **Cavar**: cada turno trae un plano (qué marca del suelo es segura, cuál
  esconde una tubería o un cable y cuál es roca). Cada palada ofrece varios
  sitios: cavar en el seguro suma, en la roca no suma y romper algo resta.
- **Detectar**: una lista con una trampa (el obrero que se escaquea, la
  partida con sobrecoste, la cláusula de letra pequeña, el colado del buffet,
  el paquete perdido, la disposición colada en una ley, el inversor de verdad
  entre los mariachis, el programa que hunde la audiencia, el proyecto cripto que es un
  timo, el fallo que tumba la app pública, el gasto que quema la caja). Acertar suma.
- **Memoria**: se enseña una secuencia (una comanda, una votación) y, al
  ocultarla, hay que repetirla en orden. Cada elemento acertado suma; un fallo
  acaba la ronda.
- **Diálogo**: una situación y tres respuestas. La buena suma.
- **Clasificar**: cada ronda trae un papel o una persona y unos sitios fijos
  (los negociados, los registros). Mandarlo al suyo suma.
- **Calcular**: varias ofertas con números y una cuenta (Y$ por kilómetro,
  me gusta por visita). Elegir la mejor suma; siempre gana por un margen claro
  (`CALC_MARGIN`), para que no dependa de redondeos.

De P5 en adelante el minijuego del puesto alterna, un día cada uno, con otro que no es de
acertar una respuesta sino de decidir con lo que hay en la mesa (`Position.alts`). Su
contenido está en `bot.services.work_rotation`:

- **Cuadrante** (`PlanGame`): unas piezas con su tamaño y unos carriles con sitio contado
  (operaciones y quirófanos, palés y muelles). Se ve la cola entera y cada pieza se manda
  a un carril donde quepa o se deja fuera. Siempre hay una forma de encajarlo todo; lo
  que se queda fuera resta. El fallo gratis de las herramientas deshace una jugada.
- **Negociación** (`DealGame`): varios tratos contra alguien con un aguante oculto. Cada
  apretón sube lo que sacas; si aprietas cuando ya no aguanta, se levanta y te quedas con
  el mínimo. Lo que hace (mira el reloj, recoge los papeles) dice cuánto le queda, según
  su carácter: el de libro no engaña, el farolero exagera y el de cara de póker no enseña
  nada. Tantear dice lo que aguanta, a cambio de un poco del trato (gratis con chuleta).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from bot.services.economy import format_amount
from bot.services.work import GUARD_TIME_FACTOR, TIRED_TIME_FACTOR, Mechanic, Position
from bot.services.work_catalog import (
    APP_BIEN,
    APP_FALLOS,
    BUDGET_ITEMS,
    CODE_BUGS,
    COLADAS,
    COLLAB_FINE,
    COLLAB_SHADY,
    DIALOGUE_PACKS,
    DIG_DANGERS,
    DIG_HINTS,
    DIG_MARKS,
    DINERS_NORMAL,
    GATECRASHERS,
    GUESTS_LEGIT,
    INSPECTOR_TELLS,
    LABOUR_INSPECTORS,
    LEYES_LIMPIAS,
    LOST_PARCELS,
    MEMORY_PACKS,
    PARCELS_TRACKED,
    POLICY_FINE,
    POLICY_TRAPS,
    PR_DANGER,
    PR_SAFE,
    RESTAURANTS,
    RIDER_SLACKING,
    RIDER_WORKING,
    RIDERS_NORMAL,
    RUG_SERIOS,
    RUG_TIMOS,
    RUNWAY_QUEMA,
    RUNWAY_RAZONABLE,
    SHARE_HITS,
    SHARE_SINKS,
    SICAV_MARIACHIS,
    SICAV_REAL,
    SLACKING,
    SORT_PACKS,
    TAX_CLEAN,
    TAX_FRAUD,
    TRIAGE_TRIVIAL,
    TRIAGE_URGENT,
    VIDEO_TOPICS,
    WORKER_NAMES,
    WORKING,
)
from bot.services.work_rotation import DEAL_PACKS, DEAL_TELLS, PLAN_PACKS, DealPack, PlanPack
from bot.services.work_tools import NO_PERKS, Perks

#: Puntos extra (sobre 100) por acabar antes de tiempo, proporcionales a lo que sobra.
TIME_BONUS = 10
#: En memoria: el reloj se para hasta estos segundos mientras se enseña cada secuencia.
MEMORIZE_SECONDS = 5.0
#: Segundos de margen tras el reloj para pulsaciones que ya iban de camino.
GRACE_SECONDS = 1.5
#: En calcular, la mejor oferta vale al menos esto más que la segunda.
CALC_MARGIN = 1.25


@dataclass(slots=True)
class Round:
    """Una ronda.

    Attributes:
        prompt: Texto de la ronda (situación, lista, pregunta).
        options: Etiquetas de los botones.
        answer: Índices correctos; en memoria, la secuencia en orden.
        penalty: Índices que restan un punto (romper algo al cavar).
        neutral: Índices que no suman ni restan (roca).
        reveal: En memoria, lo que se enseña antes de ocultarlo.
    """

    prompt: str
    options: list[str]
    answer: list[int]
    penalty: set[int] = field(default_factory=set)
    neutral: set[int] = field(default_factory=set)
    reveal: str | None = None

    @property
    def worth(self) -> int:
        """Puntos que vale la ronda si se hace perfecta."""
        return len(self.answer)


@dataclass(slots=True)
class MiniGame:
    """Una partida de un minijuego.

    Attributes:
        mechanic: Motor.
        header: Lo que se enseña siempre arriba (el plano, la regla de oro).
        rounds: Rondas en orden.
        seconds: Tiempo total.
        started_at: Inicio (epoch).
    """

    mechanic: Mechanic
    header: str
    rounds: list[Round]
    seconds: float
    started_at: float
    index: int = 0
    #: En memoria: cuántos elementos de la secuencia lleva bien en la ronda.
    step: int = 0
    #: En memoria: si se está enseñando la secuencia.
    showing: bool = False
    points: int = 0
    broken: int = 0
    perfect_rounds: int = 0
    correct: int = 0
    last: str = ""
    finished_at: float | None = None
    #: Fallos gratis que quedan (herramientas).
    retries: int = 0
    #: Si romper algo al cavar no resta (seguro).
    insured: bool = False
    #: Si los diálogos llevan una respuesta mala tachada (chuleta).
    fifty: bool = False
    #: Claves de las herramientas que cuentan en este turno.
    tools: tuple[str, ...] = ()
    #: Fallos perdonados por una herramienta.
    saved: int = 0
    #: Roturas que pagó el seguro.
    insured_breaks: int = 0
    #: Pulsaciones falladas (sin contar las perdonadas).
    misses: int = 0
    #: Pulsaciones que han contado (aciertos y fallos).
    presses: int = 0
    #: Si la primera pulsación del turno fue un fallo.
    first_miss: bool = False
    #: Pulsaciones que llegaron tarde, a una ronda ya pasada (las cuenta el cog).
    stale: int = 0
    #: En memoria: segundos que el reloj ha estado parado mientras se memorizaba.
    frozen: float = 0.0
    #: En memoria: cuándo empezó a enseñarse la secuencia de la ronda en juego.
    shown_at: float = 0.0

    def __post_init__(self) -> None:
        self.showing = self.mechanic is Mechanic.MEMORY
        if self.showing:
            self._freeze(self.started_at)

    @property
    def deadline(self) -> float:
        """Momento en que se acaba el tiempo (se mueve en memoria, ver `hide`)."""
        return self.started_at + self.seconds + self.frozen

    @property
    def max_points(self) -> int:
        """Puntos posibles en toda la partida."""
        return sum(r.worth for r in self.rounds) or 1

    @property
    def current(self) -> Round | None:
        """Ronda en juego, o `None` si ya no quedan."""
        return self.rounds[self.index] if self.index < len(self.rounds) else None

    @property
    def completed(self) -> bool:
        """Si se jugaron todas las rondas (no se acabó el tiempo antes)."""
        return self.current is None

    @property
    def time_left(self) -> float:
        """Segundos que sobraron al acabar todas las rondas (0 si no se acabaron)."""
        if not self.completed or self.finished_at is None:
            return 0.0
        return max(0.0, self.deadline - self.finished_at)

    def finished(self, now: float) -> bool:
        """Si ya no se puede jugar (sin rondas o sin tiempo)."""
        return self.finished_at is not None or self.current is None or now > self.deadline

    def finish(self, now: float) -> None:
        """Cierra la partida (por tiempo o por terminar)."""
        if self.finished_at is None:
            self.finished_at = min(now, self.deadline)

    def _freeze(self, now: float) -> None:
        """Memoria: empieza a enseñar la secuencia con el reloj parado (como mucho 5 s)."""
        self.shown_at = now
        self.frozen += MEMORIZE_SECONDS

    def hide(self, now: float) -> None:
        """Memoria: oculta la secuencia y empieza a repetir.

        El reloj solo se para lo que se tardó en memorizar, hasta `MEMORIZE_SECONDS`: lo
        que no se usó se devuelve y el reloj sigue corriendo desde ahora.
        """
        if self.showing and not self.finished(now):
            self.showing = False
            self.frozen -= max(0.0, MEMORIZE_SECONDS - (now - self.shown_at))

    def _next_round(self, now: float) -> None:
        self.index += 1
        self.step = 0
        self.showing = self.mechanic is Mechanic.MEMORY
        if self.current is None:
            self.finish(now)
        elif self.showing:
            self._freeze(now)

    def _count(self, hit: bool) -> None:
        if not hit:
            if self.presses == 0:
                self.first_miss = True
            self.misses += 1
        self.presses += 1

    def _forgive(self, wrong: str) -> bool:
        """Gasta un fallo gratis si queda. Devuelve si lo ha perdonado."""
        if self.retries <= 0:
            return False
        self.retries -= 1
        self.saved += 1
        self.last = f"🛟 {wrong}, pero tu herramienta te salva: inténtalo otra vez."
        return True

    def press(self, option: int, now: float) -> bool:
        """Registra una pulsación. Devuelve si ha sido un acierto.

        Las pulsaciones fuera de tiempo (con un pequeño margen) cierran la
        partida sin contar. Un fallo perdonado por una herramienta no pasa de
        ronda: se repite la jugada.
        """
        current = self.current
        if current is None or self.finished_at is not None:
            return False
        if now > self.deadline + GRACE_SECONDS:
            self.finish(now)
            return False
        if self.mechanic is Mechanic.MEMORY:
            return self._press_memory(current, option, now)
        if not 0 <= option < len(current.options):
            return False
        hit = option in current.answer
        right = current.options[current.answer[0]]
        if hit:
            self.points += 1
            self.correct += 1
            self.last = f"✅ ¡Bien! {current.options[option]}"
        elif option in current.penalty:
            if self._forgive("Ibas a reventar una tubería"):
                return False
            self.broken += 1
            if self.insured:
                self.insured_breaks += 1
                self.last = "💥📄 Rompiste algo, pero paga el seguro: no resta."
            else:
                self.points -= 1
                self.last = "💥 ¡Rompiste algo! Resta un punto."
        elif option in current.neutral:
            if self._forgive("Eso era roca"):
                return False
            self.last = f"🪨 Roca: no suma. Lo seguro era {right}."
        else:
            if self._forgive("Fallo"):
                return False
            self.last = f"❌ Fallo. Era {right}."
        self._count(hit)
        self._next_round(now)
        return hit

    def _press_memory(self, current: Round, option: int, now: float) -> bool:
        if self.showing or not 0 <= option < len(current.options):
            return False
        expected = current.options[current.answer[self.step]]
        if option == current.answer[self.step]:
            self.points += 1
            self.step += 1
            self._count(True)
            self.last = f"✅ {current.options[option]}"
            if self.step == len(current.answer):
                self.perfect_rounds += 1
                self.correct += 1
                self.last = "✅ ¡Ronda perfecta!"
                self._next_round(now)
            return True
        if self._forgive(f"No era {current.options[option]}"):
            return False
        self._count(False)
        self.last = (
            f"❌ Fallaste en el {self.step + 1}.º: tocaba {expected}. "
            f"Llevabas {self.step}/{len(current.answer)}."
        )
        self._next_round(now)
        return False

    def score(self) -> int:
        """Puntuación final (0–100)."""
        base = 100 * max(0, self.points) / self.max_points
        bonus = 0.0
        if self.current is None and self.finished_at is not None:
            left = max(0.0, self.deadline - self.finished_at)
            bonus = TIME_BONUS * left / self.seconds
        return max(0, min(100, round(base + bonus)))


# -- Generadores ------------------------------------------------------------------------


def _shuffled(rng: random.Random, good: list[str], bad: list[str]) -> tuple[list[str], list[int]]:
    options = good + bad
    order = list(range(len(options)))
    rng.shuffle(order)
    shuffled = [options[i] for i in order]
    answer = [order.index(i) for i in range(len(good))]
    return shuffled, answer


def _dig(rng: random.Random, spots: int, rounds: int) -> tuple[str, list[Round]]:
    marks = list(DIG_MARKS)
    rng.shuffle(marks)
    safe, danger, rock = marks[:2], marks[2:4], marks[4:6]
    dangers = rng.sample(DIG_DANGERS, 2)
    header = (
        f"📜 **Plano de hoy:** cava en {safe[0]} o {safe[1]} · "
        f"{danger[0]} = {dangers[0]} · {danger[1]} = {dangers[1]} · "
        f"{rock[0]} {rock[1]} = roca"
    )
    built = []
    for number in range(1, rounds + 1):
        others = [rng.choice(danger + rock) for _ in range(spots - 1)]
        cells = [rng.choice(safe), *others]
        order = list(range(spots))
        rng.shuffle(order)
        options = [cells[i] for i in order]
        answer = [order.index(0)]
        penalty = {i for i, mark in enumerate(options) if mark in danger}
        neutral = {i for i, mark in enumerate(options) if mark in rock}
        prompt = f"⛏️ Palada {number}/{rounds}. ¿Dónde cavas?"
        if rng.random() < 0.3:
            pointed = rng.randrange(spots)
            prompt += "\n" + rng.choice(DIG_HINTS).format(mark=f"el {pointed + 1}.º")
        built.append(
            Round(
                prompt,
                [f"{i + 1}. {mark}" for i, mark in enumerate(options)],
                answer,
                penalty,
                neutral,
            )
        )
    return header, built


def _culprits(
    rng: random.Random,
    rounds: int,
    *,
    ask: str,
    working: tuple[str, ...],
    slacking: tuple[str, ...],
) -> list[Round]:
    """Rondas de «¿quién se escaquea?»: cinco nombres y uno que no hace lo que debe."""
    built = []
    for number in range(1, rounds + 1):
        names = rng.sample(WORKER_NAMES, 5)
        lines = rng.sample(working, 4)
        culprit = rng.randrange(5)
        lines.insert(culprit, rng.choice(slacking))
        text = "\n".join(f"**{name}**: {line}" for name, line in zip(names, lines, strict=True))
        built.append(Round(f"{ask.format(n=number, total=rounds)}\n{text}", names, [culprit]))
    return built


def _slackers(rng: random.Random, rounds: int) -> tuple[str, list[Round]]:
    built = _culprits(
        rng,
        rounds,
        ask="👷 Ronda {n}/{total}. ¿Quién se escaquea?",
        working=WORKING,
        slacking=SLACKING,
    )
    return "🦺 Eres el encargado. Pilla al que no da palo al agua.", built


def _fleet(rng: random.Random, rounds: int) -> tuple[str, list[Round]]:
    built = _culprits(
        rng,
        rounds,
        ask="🛵 Ronda {n}/{total}. ¿Qué rider se está comiendo el pedido?",
        working=RIDER_WORKING,
        slacking=RIDER_SLACKING,
    )
    return "📊 Eres jefe de flota. A los clientes les llegan las papas contadas.", built


def _overruns(rng: random.Random, rounds: int) -> tuple[str, list[Round]]:
    built = []
    for number in range(1, rounds + 1):
        items = rng.sample(BUDGET_ITEMS, 5)
        inflated = rng.randrange(5)
        lines = []
        for i, (name, price) in enumerate(items):
            factor = rng.uniform(6, 12) if i == inflated else rng.uniform(0.85, 1.2)
            amount = f"{round(price * factor):,}".replace(",", ".")
            lines.append(f"`{i + 1}.` {name} · **{amount} Y$**")
        built.append(
            Round(
                f"📑 Presupuesto {number}/{rounds} del ayuntamiento. ¿Qué partida lleva "
                f"sobrecoste?\n" + "\n".join(lines),
                [f"{i + 1}. {name}"[:80] for i, (name, _price) in enumerate(items)],
                [inflated],
            )
        )
    return (
        "🏗️ Eres constructor. Antes de firmar, encuentra la partida inflada "
        "(o hazte el loco y que la encuentre la UCO).",
        built,
    )


def _memory(rng: random.Random, pack_key: str, length: int, rounds: int) -> tuple[str, list[Round]]:
    pack = MEMORY_PACKS[pack_key]
    built = []
    for number in range(rounds):
        size = min(len(pack.items), max(5, length + 2))
        shown = rng.sample(pack.items, size)
        sequence = rng.sample(range(size), min(length + number // 2, size))
        labels = [f"{emoji} {name}" for emoji, name in shown]
        reveal = (
            pack.intro.format(n=rng.randint(1, 30))
            + "\n# "
            + " → ".join(shown[i][0] for i in sequence)
        )
        reveal += "\n-# " + " → ".join(shown[i][1] for i in sequence)
        built.append(
            Round(
                f"🧠 Ronda {number + 1}/{rounds}. {pack.ask}",
                labels,
                sequence,
                reveal=reveal,
            )
        )
    return "", built


def _odd_one(
    rng: random.Random,
    rounds: int,
    *,
    ask: str,
    good: tuple[str, ...],
    bad: tuple[str, ...],
    label: str,
) -> list[Round]:
    """Rondas de «encuentra el distinto»: uno de `good` entre cuatro de `bad`."""
    built = []
    for number in range(1, rounds + 1):
        lines = rng.sample(bad, 4)
        target = rng.randrange(5)
        lines.insert(target, rng.choice(good))
        text = "\n".join(f"**{label} {i + 1}**: {line}" for i, line in enumerate(lines))
        built.append(
            Round(
                f"{ask.format(n=number, total=rounds)}\n{text}",
                [f"{label} {i + 1}" for i in range(5)],
                [target],
            )
        )
    return built


def _triage(rng: random.Random, rounds: int) -> tuple[str, list[Round]]:
    built = _odd_one(
        rng,
        rounds,
        ask="🚑 Triaje {n}/{total}. ¿Quién pasa primero?",
        good=TRIAGE_URGENT,
        bad=TRIAGE_TRIVIAL,
        label="Box",
    )
    return "🏥 Urgencias a reventar. Prioridad 1: lo que no puede esperar.", built


def _bugs(rng: random.Random, rounds: int) -> tuple[str, list[Round]]:
    built = []
    for number, (lines, bug) in enumerate(rng.sample(CODE_BUGS, rounds), 1):
        code = "\n".join(f"{i + 1} | {line}" for i, line in enumerate(lines))
        built.append(
            Round(
                f"🐛 Bug {number}/{rounds}. ¿Qué línea está mal?\n```py\n{code}\n```",
                [f"Línea {i + 1}" for i in range(len(lines))],
                [bug],
            )
        )
    return "💻 Ticket urgente: «No funciona». Encuentra el fallo.", built


def _reviews(rng: random.Random, rounds: int) -> tuple[str, list[Round]]:
    built = _odd_one(
        rng,
        rounds,
        ask="🔍 Revisión {n}/{total}. ¿Qué pull request no aprobarías jamás?",
        good=PR_DANGER,
        bad=PR_SAFE,
        label="PR",
    )
    return "🛡️ Eres el último muro antes de producción.", built


def _audits(rng: random.Random, rounds: int) -> tuple[str, list[Round]]:
    built = _odd_one(
        rng,
        rounds,
        ask="🔎 Inspección {n}/{total}. ¿Qué declaración huele a fraude?",
        good=TAX_FRAUD,
        bad=TAX_CLEAN,
        label="Declaración",
    )
    return "🐶 Eres inspector de Hacienda. Perro Sanxe confía en ti.", built


def _collabs(rng: random.Random, rounds: int) -> tuple[str, list[Round]]:
    built = _odd_one(
        rng,
        rounds,
        ask="💼 Propuesta {n}/{total}. ¿Cuál rechazas (si te queda vergüenza)?",
        good=COLLAB_SHADY,
        bad=COLLAB_FINE,
        label="Marca",
    )
    return "📩 Tu agencia te pasa propuestas. Una te puede costar la reputación.", built


def _fine_print(rng: random.Random, rounds: int) -> tuple[str, list[Round]]:
    built = _odd_one(
        rng,
        rounds,
        ask="📄 Póliza {n}/{total}. ¿Qué cláusula deja tirado al asegurado?",
        good=POLICY_TRAPS,
        bad=POLICY_FINE,
        label="Cláusula",
    )
    return "🔍 Eres médico de mutua. Antes de autorizar, encuentra la letra pequeña.", built


def _gatecrashers(rng: random.Random, rounds: int) -> tuple[str, list[Round]]:
    built = _odd_one(
        rng,
        rounds,
        ask="🟢 Buffet {n}/{total}. ¿Quién se ha colado sin pulsera del todo incluido?",
        good=GATECRASHERS,
        bad=GUESTS_LEGIT,
        label="Huésped",
    )
    return "🔍 Diriges un resort. Antes de cobrar el buffet, pilla al colado.", built


def _guide_inspector(rng: random.Random, rounds: int) -> tuple[str, list[Round]]:
    built = _odd_one(
        rng,
        rounds,
        ask="🔎 Sala {n}/{total}. ¿Quién es el inspector de la Guía?",
        good=INSPECTOR_TELLS,
        bad=DINERS_NORMAL,
        label="Mesa",
    )
    return "⭐ Tu estrella está en juego. Descubre al inspector antes del postre.", built


def _lost_parcel(rng: random.Random, rounds: int) -> tuple[str, list[Round]]:
    built = _odd_one(
        rng,
        rounds,
        ask="📦 Nave {n}/{total}. ¿Cuál es el paquete perdido?",
        good=LOST_PARCELS,
        bad=PARCELS_TRACKED,
        label="Paquete",
    )
    return (
        "🔍 Diriges las operaciones de Correos. Entre cuatro envíos normales hay uno perdido.",
        built,
    )


def _labour_inspector(rng: random.Random, rounds: int) -> tuple[str, list[Round]]:
    built = _odd_one(
        rng,
        rounds,
        ask="🕵️ Turno {n}/{total}. ¿Quién es la inspectora de Trabajo infiltrada?",
        good=LABOUR_INSPECTORS,
        bad=RIDERS_NORMAL,
        label="Rider",
    )
    return (
        "🚀 Tu imperio es legal. Casi. Descubre a la inspectora antes de que pida el contrato.",
        built,
    )


def _colada(rng: random.Random, rounds: int) -> tuple[str, list[Round]]:
    built = _odd_one(
        rng,
        rounds,
        ask="🧐 Decreto {n}/{total}. ¿Qué disposición no pinta nada en su ley?",
        good=COLADAS,
        bad=LEYES_LIMPIAS,
        label="Texto",
    )
    return "📚 Eres secretario general técnico. Nadie lee la letra pequeña, tú sí.", built


def _mariachis(rng: random.Random, rounds: int) -> tuple[str, list[Round]]:
    built = _odd_one(
        rng,
        rounds,
        ask="🥂 SICAV {n}/{total}. Cuatro de los cinco socios son mariachis. ¿Quién invierte "
        "de verdad?",
        good=SICAV_REAL,
        bad=SICAV_MARIACHIS,
        label="Socio",
    )
    return "🎺 Consejero de una SICAV: para llegar a cien socios hace falta mucho mariachi.", built


def _share(rng: random.Random, rounds: int) -> tuple[str, list[Round]]:
    built = _odd_one(
        rng,
        rounds,
        ask="📺 Parrilla {n}/{total}. ¿Qué programa está hundiendo la audiencia?",
        good=SHARE_SINKS,
        bad=SHARE_HITS,
        label="Programa",
    )
    return "📊 Diriges la cadena. Entre cinco programas hay uno que se come el share.", built


def _rugpull(rng: random.Random, rounds: int) -> tuple[str, list[Round]]:
    built = _odd_one(
        rng,
        rounds,
        ask="🪙 Proyecto {n}/{total}. ¿Cuál es el rug pull (el que se llevará el dinero y "
        "desaparecerá)?",
        good=RUG_TIMOS,
        bad=RUG_SERIOS,
        label="Proyecto",
    )
    return "🚀 Eres gurú cripto: recomienda lo que quieras, pero distingue el timo.", built


def _public_app(rng: random.Random, rounds: int) -> tuple[str, list[Round]]:
    built = _odd_one(
        rng,
        rounds,
        ask="📲 Revisión {n}/{total}. ¿Qué fallo es el que tumba la app pública?",
        good=APP_FALLOS,
        bad=APP_BIEN,
        label="Pantalla",
    )
    return (
        "🏛️ Diriges la transformación digital del Ministerio. La app sale el lunes: entre "
        "cinco pantallas hay una que la tumba.",
        built,
    )


def _runway(rng: random.Random, rounds: int) -> tuple[str, list[Round]]:
    built = _odd_one(
        rng,
        rounds,
        ask="🔥 Gasto {n}/{total}. ¿Cuál quema la caja sin vender ni fabricar nada?",
        good=RUNWAY_QUEMA,
        bad=RUNWAY_RAZONABLE,
        label="Gasto",
    )
    return "🦄 Te quedan seis meses de caja. Entre cinco gastos hay uno que la quema.", built


#: Contenidos de detectar y su generador.
_SPOT = {
    "vagos": _slackers,
    "sobrecostes": _overruns,
    "triaje": _triage,
    "bugs": _bugs,
    "revisiones": _reviews,
    "flota": _fleet,
    "inspeccion": _audits,
    "colaboraciones": _collabs,
    "letra_pequena": _fine_print,
    "colados": _gatecrashers,
    "inspector": _guide_inspector,
    "perdido": _lost_parcel,
    "inspector_trabajo": _labour_inspector,
    "colada": _colada,
    "mariachis": _mariachis,
    "share": _share,
    "rugpull": _rugpull,
    "app_publica": _public_app,
    "runway": _runway,
}


def _sort(rng: random.Random, pack_key: str, rounds: int) -> tuple[str, list[Round]]:
    """Rondas de clasificar: un papel por ronda y los sitios del paquete como botones."""
    pack = SORT_PACKS[pack_key]
    rounds = min(rounds, len(pack.items))
    built = [
        Round(f"{pack.ask.format(n=number, total=rounds)} **{text}**", list(pack.bins), [target])
        for number, (text, target) in enumerate(rng.sample(pack.items, rounds), 1)
    ]
    return pack.rule, built


def _best_index(values: list[float]) -> int | None:
    """Índice de la mejor si saca `CALC_MARGIN` a la segunda; si no, `None`."""
    ranked = sorted(range(len(values)), key=values.__getitem__, reverse=True)
    best, second = ranked[0], ranked[1]
    return best if values[best] >= values[second] * CALC_MARGIN else None


def _decimal(value: float, places: int = 2) -> str:
    return f"{value:.{places}f}".replace(".", ",")


def _orders(rng: random.Random, offers: int, rounds: int) -> tuple[str, list[Round]]:
    """Reparto: ¿qué pedido paga más por kilómetro?"""
    built = []
    for number in range(1, rounds + 1):
        while True:
            km = [round(rng.uniform(0.8, 6.0), 1) for _ in range(offers)]
            pay = [round((k * rng.uniform(0.5, 1.6) + 1) * 100) for k in km]  # Y$, a 100 el euro
            best = _best_index([p / k for p, k in zip(pay, km, strict=True)])
            if best is not None:
                break
        names = rng.sample(RESTAURANTS, offers)
        lines = [
            f"`{i + 1}.` {name} · {_decimal(km[i], 1)} km · **{format_amount(pay[i])}**"
            for i, name in enumerate(names)
        ]
        built.append(
            Round(
                f"🛵 Pedido {number}/{rounds}. ¿Cuál paga más por kilómetro?\n" + "\n".join(lines),
                [f"{i + 1}. {name}"[:80] for i, name in enumerate(names)],
                [best],
            )
        )
    return "📱 La app te ofrece pedidos. Cuenta rápido: Y$ entre kilómetros.", built


def _metrics(rng: random.Random, rounds: int) -> tuple[str, list[Round]]:
    """Influencer: ¿qué vídeo tiene más engagement (me gusta entre visitas)?"""
    built = []
    for number in range(1, rounds + 1):
        while True:
            views = [rng.randrange(2_000, 80_000, 100) for _ in range(4)]
            likes = [round(v * rng.uniform(0.01, 0.12)) for v in views]
            best = _best_index([lk / v for lk, v in zip(likes, views, strict=True)])
            if best is not None:
                break
        topics = rng.sample(VIDEO_TOPICS, 4)
        lines = [
            f"`{i + 1}.` {topic} · {views[i]:,} visitas · {likes[i]:,} ❤️".replace(",", ".")
            for i, topic in enumerate(topics)
        ]
        built.append(
            Round(
                f"📊 Métricas {number}/{rounds}. ¿Qué vídeo tiene más engagement?\n"
                + "\n".join(lines),
                [f"{i + 1}. {topic.removeprefix('📹 ')}"[:80] for i, topic in enumerate(topics)],
                [best],
            )
        )
    header = "📈 La marca quiere saber qué funciona: me gusta entre visitas, no visitas a secas."
    return header, built


def _dialogue(
    rng: random.Random, pack_key: str, rounds: int, *, fifty: bool = False
) -> tuple[str, list[Round]]:
    """Rondas de diálogo. Con `fifty` (chuleta) sobra una respuesta mala."""
    pack = DIALOGUE_PACKS[pack_key]
    rounds = min(rounds, len(pack.items))
    built = []
    for number, item in enumerate(rng.sample(pack.items, rounds), 1):
        bad = list(item.bad)
        if fifty and len(bad) > 1:
            bad.remove(rng.choice(bad))
        options, answer = _shuffled(rng, [item.good], bad)
        letters = "ABCD"
        body = "\n".join(f"**{letters[i]}.** {text}" for i, text in enumerate(options))
        built.append(
            Round(
                f"💬 {number}/{rounds}. {item.situation}\n{body}",
                list(letters[: len(options)]),
                answer,
            )
        )
    return pack.rule, built


# -- Rotación: Cuadrante ------------------------------------------------------------------

#: Lo que cabe en cada carril de un Cuadrante.
PLAN_CAPACITY = 6
#: Formas de llenar un carril entero: de ellas salen las piezas, así que todo encaja.
PLAN_SPLITS = ((4, 2), (3, 3), (4, 1, 1), (3, 2, 1), (2, 2, 2))
#: Lo que resta, por unidad, una pieza que se queda fuera (además de no sumar).
PLAN_SKIP_PENALTY = 1
#: Piezas con condición (solo en un carril o vetada en uno) según el puesto.
PLAN_RULES = {5: 1, 6: 1, 7: 2, 8: 2, 9: 3, 10: 3}


@dataclass(slots=True)
class Piece:
    """Una pieza de un Cuadrante.

    Attributes:
        name: Cómo se enseña.
        size: Lo que ocupa.
        only: Único carril al que puede ir (`None` = cualquiera).
        ban: Carril al que no puede ir (`None` = ninguno vetado).
    """

    name: str
    size: int
    only: int | None = None
    ban: int | None = None

    def allows(self, lane: int) -> bool:
        """Si su condición le deja ir a ese carril."""
        return lane != self.ban and self.only in (None, lane)


@dataclass(slots=True)
class PlanGame(MiniGame):
    """Un Cuadrante: mandar cada pieza de la cola a un carril donde quepa.

    `index` cuenta las jugadas (también deshacer): así un clic que salió con el tablero de
    antes no cuenta, como en el resto de minijuegos. `rounds` va vacío.

    Attributes:
        pack: Contenido (nombres de carriles, unidad, botón de dejar fuera).
        lanes: Índices de los carriles de `pack.lanes` que se usan.
        capacity: Lo que cabe en cada carril.
        pieces: La cola, en orden.
        placed: Carril (posición en `lanes`) de cada pieza ya decidida; `None`, fuera.
        undone: Veces que se ha deshecho una jugada.
        solved: Lo que ya ha calculado `best` (la cola no cambia en toda la partida).
    """

    pack: PlanPack | None = None
    lanes: tuple[int, ...] = ()
    capacity: int = PLAN_CAPACITY
    pieces: list[Piece] = field(default_factory=list)
    placed: list[int | None] = field(default_factory=list)
    undone: int = 0
    solved: dict[tuple[int, tuple[int, ...]], int] = field(default_factory=dict, repr=False)

    @property
    def max_points(self) -> int:
        """Lo que suman todas las piezas."""
        return sum(piece.size for piece in self.pieces) or 1

    def fill(self) -> list[int]:
        """Lo ocupado en cada carril."""
        fill = [0] * len(self.lanes)
        for piece, lane in zip(self.pieces, self.placed, strict=False):
            if lane is not None:
                fill[lane] += piece.size
        return fill

    def _open(self) -> list[int]:
        """Carriles donde cabe la pieza en juego."""
        piece = self.pieces[len(self.placed)]
        fill = self.fill()
        return [
            lane
            for lane in range(len(self.lanes))
            if piece.allows(lane) and fill[lane] + piece.size <= self.capacity
        ]

    def _lane(self, lane: int) -> str:
        assert self.pack is not None
        return self.pack.lanes[self.lanes[lane]]

    def _size(self, piece: Piece) -> str:
        """«4 h», «1 día»: el tamaño con su unidad, en singular si es una."""
        assert self.pack is not None
        unit = self.pack.unit
        return f"{piece.size} {unit[:-1] if piece.size == 1 and unit.endswith('s') else unit}"

    def _condition(self, piece: Piece) -> str:
        if piece.only is not None:
            return f" · 🔒 solo {self._lane(piece.only)}"
        if piece.ban is not None:
            return f" · 🚫 no en {self._lane(piece.ban)}"
        return ""

    @property
    def can_undo(self) -> bool:
        """Si queda un fallo gratis para deshacer la última jugada."""
        return self.retries > 0 and bool(self.placed)

    @property
    def current(self) -> Round | None:
        """El tablero y la pieza en juego, con un botón por carril donde cabe."""
        if len(self.placed) >= len(self.pieces):
            return None
        assert self.pack is not None
        unit = self.pack.unit
        lines = [
            f"`{self._lane(lane)}` {'▰' * used}{'▱' * (self.capacity - used)} "
            f"{used}/{self.capacity} {unit}"
            for lane, used in enumerate(self.fill())
        ]
        turn = len(self.placed)
        piece = self.pieces[turn]
        lines.append(
            f"**Ahora ({turn + 1}/{len(self.pieces)}):** {piece.name} · "
            f"**{self._size(piece)}**{self._condition(piece)}"
        )
        if queue := self.pieces[turn + 1 :]:
            lines.append(
                "**Después:** "
                + " · ".join(f"{p.name} {self._size(p)}{self._condition(p)}" for p in queue)
            )
        options = [self._lane(lane) for lane in self._open()] + [self.pack.skip]
        if self.can_undo:
            options.append("↩️ Deshacer (🛟)")
        return Round("\n".join(lines), options, [])

    def press(self, option: int, now: float) -> bool:
        """Manda la pieza en juego a un carril, la deja fuera o deshace la anterior."""
        current = self.current
        if current is None or self.finished_at is not None:
            return False
        if now > self.deadline + GRACE_SECONDS:
            self.finish(now)
            return False
        if not 0 <= option < len(current.options):
            return False
        assert self.pack is not None
        self.index += 1
        open_lanes = self._open()
        if option > len(open_lanes):
            # Deshacer: gasta el fallo gratis y devuelve la última pieza a la cola.
            self.retries -= 1
            self.saved += 1
            self.undone += 1
            lane = self.placed.pop()
            piece = self.pieces[len(self.placed)]
            if lane is None:
                self.points += piece.size * PLAN_SKIP_PENALTY
                self.misses -= 1
            else:
                self.points -= piece.size
                self.correct -= 1
            self.presses -= 1
            self.last = f"↩️ Deshecho: {piece.name} vuelve a la cola."
            return False
        piece = self.pieces[len(self.placed)]
        hit = option < len(open_lanes)
        if hit:
            self.placed.append(open_lanes[option])
            self.points += piece.size
            self.correct += 1
            self.last = f"✅ {piece.name} → {self._lane(open_lanes[option])}"
        else:
            self.placed.append(None)
            self.points -= piece.size * PLAN_SKIP_PENALTY
            self.last = f"🗑️ {piece.name} se queda fuera: {self._size(piece)} sin colocar."
        self._count(hit)
        if len(self.placed) == len(self.pieces):
            if all(lane is not None for lane in self.placed):
                self.perfect_rounds += 1
                self.last = "✅ ¡Cuadrante completo!"
            self.finish(now)
        return hit

    def best(self) -> int:
        """Botón que pulsaría quien deja fuera lo menos posible (pruebas y calibrado).

        Busca a fondo: son pocas piezas. No se llama desde el bot.
        """
        pieces, start, capacity = self.pieces, len(self.placed), self.capacity

        seen = self.solved

        def lost(turn: int, fill: tuple[int, ...]) -> int:
            if turn == len(pieces):
                return 0
            if (key := (turn, fill)) in seen:
                return seen[key]
            piece = pieces[turn]
            worst = piece.size + lost(turn + 1, fill)
            for lane in range(len(fill)):
                if worst == 0:
                    break
                if piece.allows(lane) and fill[lane] + piece.size <= capacity:
                    after = (*fill[:lane], fill[lane] + piece.size, *fill[lane + 1 :])
                    worst = min(worst, lost(turn + 1, after))
            seen[key] = worst
            return worst

        fill = self.fill()
        open_lanes = self._open()
        piece = pieces[start]
        costs = [
            lost(start + 1, tuple(f + piece.size * (i == lane) for i, f in enumerate(fill)))
            for lane in open_lanes
        ]
        if costs and min(costs) < piece.size + lost(start + 1, tuple(fill)):
            return costs.index(min(costs))
        return len(open_lanes)


def _plan(
    rng: random.Random, pack_key: str, level: int, factor: int
) -> tuple[PlanPack, tuple[int, ...], list[Piece]]:
    """Piezas de un Cuadrante que encajan todas: salen de llenar cada carril entero."""
    pack = PLAN_PACKS[pack_key]
    lanes = tuple(sorted(rng.sample(range(len(pack.lanes)), 3 if level <= 6 else 4)))
    sizes = [
        (size, lane)
        for lane in range(len(lanes))
        for _ in range(factor)
        for size in rng.choice(PLAN_SPLITS)
    ]
    rng.shuffle(sizes)
    names = rng.sample(pack.pieces, min(len(sizes), len(pack.pieces)))
    pieces = [
        Piece(names[i % len(names)] + (" (bis)" if i >= len(names) else ""), size)
        for i, (size, _lane) in enumerate(sizes)
    ]
    # Las condiciones respetan la solución: «solo» es su carril y «no en», otro.
    for n, i in enumerate(rng.sample(range(len(pieces)), PLAN_RULES.get(level, 3))):
        home = sizes[i][1]
        if n % 2:
            pieces[i].only = home
        else:
            pieces[i].ban = rng.choice([lane for lane in range(len(lanes)) if lane != home])
    return pack, lanes, pieces


# -- Rotación: Negociación ----------------------------------------------------------------

#: Lo que tienes de salida en un trato y lo que sube cada apretón, en % de lo que pedías.
DEAL_START = 40
DEAL_STEP = 15
#: Lo que te queda si se levanta de la mesa.
DEAL_BREAK = 20
#: Lo que cuesta tantear.
DEAL_PROBE = 5
#: Caracteres: cómo enseña cada uno lo que le queda de aguante.
DEAL_KINDS = {
    "libro": "📖 **De libro:** lo que ves es lo que hay.",
    "farol": "🎭 **Farolero:** exagera. Aguanta un apretón más de lo que aparenta.",
    "poker": "🧊 **Cara de póker:** no enseña nada. Aguanta 2 como poco.",
}
#: Quién se sienta enfrente según el puesto (de ahí salen también los tratos del turno).
DEAL_TABLE = {
    5: ("libro", "libro", "farol"),
    6: ("libro", "libro", "farol"),
    7: ("libro", "farol", "poker"),
    8: ("libro", "farol", "poker"),
}
DEAL_TABLE_TOP = ("farol", "poker", "poker")
#: Aguante más alto: con él se llega al 100 %.
DEAL_STAMINA = (100 - DEAL_START) // DEAL_STEP


@dataclass(slots=True)
class Deal:
    """Un trato de una Negociación.

    Attributes:
        who: Con quién.
        what: Qué se negocia.
        kind: Su carácter (clave de `DEAL_KINDS`).
        stamina: Apretones que aguanta.
        tells: Lo que hace tras 0, 1… `stamina` apretones.
    """

    who: str
    what: str
    kind: str
    stamina: int
    tells: list[str]

    @property
    def best(self) -> int:
        """Lo más que se le puede sacar."""
        return DEAL_START + DEAL_STEP * self.stamina


def deal_mood(kind: str, left: int) -> str:
    """Cómo se le ve (clave de `DEAL_TELLS`) con `left` apretones de aguante."""
    if kind == "poker":
        return "poker"
    if kind == "farol":
        left -= 1
    return "calm" if left >= 2 else "tense" if left == 1 else "limit"


@dataclass(slots=True)
class DealGame(MiniGame):
    """Una Negociación: apretar, tantear o cerrar en cada trato.

    `index` cuenta las jugadas, como en `PlanGame`. `rounds` va vacío. Con la chuleta
    (`fifty`), tantear no cuesta.

    Attributes:
        pack: Contenido (la escena).
        deals: Los tratos, en orden.
        turn: Trato en juego.
        pushed: Apretones dados en el trato en juego.
        probed: Si ya se ha tanteado en el trato en juego.
        walked: Tratos en los que se levantó de la mesa.
        probes: Veces que se ha tanteado.
        got: Lo sacado en cada trato cerrado o roto.
        peeked: Si se tanteó en cada trato cerrado o roto.
    """

    pack: DealPack | None = None
    deals: list[Deal] = field(default_factory=list)
    turn: int = 0
    pushed: int = 0
    probed: bool = False
    walked: int = 0
    probes: int = 0
    got: list[int] = field(default_factory=list)
    peeked: list[bool] = field(default_factory=list)

    @property
    def max_points(self) -> int:
        """Lo que suma sacar lo más posible de cada trato."""
        return sum(deal.best for deal in self.deals) or 1

    @property
    def left(self) -> int:
        """Apretones que aguanta todavía el de enfrente."""
        return self.deals[self.turn].stamina - self.pushed

    @property
    def offer(self) -> int:
        """Lo que te llevas si cierras ahora."""
        cost = DEAL_PROBE if self.probed and not self.fifty else 0
        return DEAL_START + DEAL_STEP * self.pushed - cost

    @property
    def current(self) -> Round | None:
        """El trato en juego: con quién, por dónde vas y cómo se le ve."""
        if self.turn >= len(self.deals):
            return None
        deal = self.deals[self.turn]
        lines = [
            f"🤝 **Trato {self.turn + 1}/{len(self.deals)}:** {deal.what}, con {deal.who}.",
            DEAL_KINDS[deal.kind],
            f"Vas por el **{self.offer} %** de lo que pedías.",
            f"*{deal.tells[self.pushed]}*",
        ]
        if self.probed:
            lines.append(f"👀 Tanteado: aguanta **{self.left}** más.")
        options = [f"💪 Apretar (+{DEAL_STEP})", f"🤝 Cerrar con el {self.offer} %"]
        if not self.probed:
            options.append("👀 Tantear (gratis)" if self.fifty else f"👀 Tantear (−{DEAL_PROBE})")
        return Round("\n".join(lines), options, [])

    def _close(self, amount: int, now: float) -> None:
        self.got.append(amount)
        self.peeked.append(self.probed)
        self.points += amount
        self.turn += 1
        self.pushed = 0
        self.probed = False
        if self.turn == len(self.deals):
            self.finish(now)

    def press(self, option: int, now: float) -> bool:
        """Aprieta (0), cierra (1) o tantea (2). Devuelve si la jugada ha salido bien."""
        current = self.current
        if current is None or self.finished_at is not None:
            return False
        if now > self.deadline + GRACE_SECONDS:
            self.finish(now)
            return False
        if not 0 <= option < len(current.options):
            return False
        self.index += 1
        deal, left = self.deals[self.turn], self.left
        if option == 2:
            self.probed = True
            self.probes += 1
            self.last = f"👀 Tanteas: aguanta {left} más."
            return True
        if option == 0:
            if left > 0:
                self.pushed += 1
                self.last = f"💪 Aprietas y sube al {self.offer} %."
                return True
            if self._forgive("Se levantaba de la mesa"):
                return False
            self.walked += 1
            self._count(False)
            self.last = (
                f"🚪 Se levanta de la mesa: te quedas con el acuerdo de mínimos, el {DEAL_BREAK} %."
            )
            self._close(DEAL_BREAK, now)
            return False
        offer = self.offer
        short = deal.best - offer
        good = short <= DEAL_PROBE
        if good:
            self.correct += 2
            self.perfect_rounds += short == 0
            self.last = f"✅ Cerrado al {offer} %: era su límite."
        else:
            self.correct += left == 1
            self.last = f"🤝 Cerrado al {offer} %. Aguantaba {left} más: llegaba al {deal.best} %."
        self._count(good)
        self._close(offer, now)
        return good

    def best(self) -> int:
        """Botón de quien supiera el aguante (pruebas y calibrado)."""
        return 0 if self.left > 0 else 1


def _deal(
    rng: random.Random, pack_key: str, level: int, factor: int
) -> tuple[DealPack, list[Deal]]:
    """Los tratos de una Negociación: carácter según el puesto y aguante al azar."""
    pack = DEAL_PACKS[pack_key]
    kinds = list(DEAL_TABLE.get(level, DEAL_TABLE_TOP)) * factor
    rng.shuffle(kinds)
    count = len(kinds)
    who = [pack.who[i % len(pack.who)] for i in rng.sample(range(count), count)]
    what = [pack.what[i % len(pack.what)] for i in rng.sample(range(count), count)]
    deals = []
    for i, kind in enumerate(kinds):
        stamina = rng.randint(2 if kind == "poker" else 1, DEAL_STAMINA)
        tells = [
            rng.choice(DEAL_TELLS[deal_mood(kind, stamina - pushed)])
            for pushed in range(stamina + 1)
        ]
        deals.append(Deal(who[i], what[i], kind, stamina, tells))
    return pack, deals


def rounds_for(position: Position) -> int:
    """Rondas del minijuego de un puesto (más tiempo, más rondas)."""
    if position.mechanic is Mechanic.DIG:
        return 8 if position.seconds <= 30 else 10
    if position.mechanic is Mechanic.SPOT:
        return 6
    if position.mechanic is Mechanic.MEMORY:
        return {30: 3, 40: 4}.get(position.seconds, 5)
    if position.mechanic is Mechanic.SORT:
        return 6 if position.seconds <= 30 else 8
    if position.mechanic is Mechanic.CALC:
        # Hacer cuentas lleva más que leer: menos rondas por segundo.
        return 4 if position.seconds <= 30 else 6
    return 5 if position.seconds <= 40 else 6


def new_game(
    position: Position,
    rng: random.Random,
    *,
    now: float,
    tired: bool = False,
    guard: bool = False,
    perks: Perks = NO_PERKS,
) -> MiniGame:
    """Prepara el minijuego de un puesto.

    Args:
        tired: Reventado: el reloj corre con un 30 % menos de tiempo.
        guard: Guardia: el doble de rondas y el doble de tiempo.
        perks: Lo que dan las herramientas de curro del miembro.
    """
    factor = GUARD_TIME_FACTOR if guard else 1
    seconds = position.seconds * factor * (TIRED_TIME_FACTOR if tired else 1)
    seconds *= 1 + perks.extra_time
    tools = tuple(t.key for t in perks.tools)
    kind, _, arg = position.content.partition(":")
    if position.mechanic is Mechanic.PLAN:
        plan, lanes, pieces = _plan(rng, kind, position.level, factor)
        return PlanGame(
            Mechanic.PLAN,
            plan.scene,
            [],
            seconds,
            now,
            retries=perks.retries,
            tools=tools,
            pack=plan,
            lanes=lanes,
            capacity=PLAN_CAPACITY * factor,
            pieces=pieces,
        )
    if position.mechanic is Mechanic.DEAL:
        table, deals = _deal(rng, kind, position.level, factor)
        return DealGame(
            Mechanic.DEAL,
            table.scene,
            [],
            seconds,
            now,
            retries=perks.retries,
            fifty=perks.fifty,
            tools=tools,
            pack=table,
            deals=deals,
        )
    rounds = rounds_for(position) * factor
    if position.mechanic is Mechanic.DIG:
        header, built = _dig(rng, int(arg or 3), rounds)
    elif position.mechanic is Mechanic.SPOT:
        header, built = _SPOT[kind](rng, min(rounds, len(CODE_BUGS)) if kind == "bugs" else rounds)
    elif position.mechanic is Mechanic.MEMORY:
        header, built = _memory(rng, kind, int(arg or 3), rounds)
    elif position.mechanic is Mechanic.SORT:
        header, built = _sort(rng, kind, rounds)
    elif position.mechanic is Mechanic.CALC:
        if kind == "pedidos":
            header, built = _orders(rng, int(arg or 3), rounds)
        else:
            header, built = _metrics(rng, rounds)
    else:
        header, built = _dialogue(rng, kind, rounds, fifty=perks.fifty)
    return MiniGame(
        position.mechanic,
        header,
        built,
        seconds,
        now,
        retries=perks.retries,
        insured=perks.insured,
        fifty=perks.fifty and position.mechanic is Mechanic.DIALOGUE,
        tools=tools,
    )
