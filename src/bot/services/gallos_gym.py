"""Minijuegos de entrenamiento de los gallos (`gallos`): seis, dos por stat.

Lógica pura, sin Discord ni dinero ni reloj. Cada juego es una pequeña máquina
de estados que mueve el cog con botones: `text()` dice la pantalla, `buttons()`
los botones de ahora, `press(key, now)` aplica una pulsación y `score` (0 a 1)
vale cuando `done`. Todo el azar sale del `random.Random` que se le pasa, y el
tiempo entra por parámetro (`now`). Nada depende de lo rápido que se pulse,
salvo el límite total de `obstaculos`, que es generoso.

- 💪 fuerza: `pesas` (arriesgar o soltar) y `pulso` (leer las pistas del rival).
- 🪶 velocidad: `secuencia` (memoria) y `obstaculos` (acertar rápido).
- ❤️ aguante: `fondo` (repartir la energía) y `sauna` (arriesgar con el riesgo a la vista).
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from itertools import product

from bot.services.gallos import Stat

KINDS: dict[Stat, tuple[str, str]] = {
    Stat.STRENGTH: ("pesas", "pulso"),
    Stat.SPEED: ("secuencia", "obstaculos"),
    Stat.ENDURANCE: ("fondo", "sauna"),
}
TITLES: dict[str, str] = {
    "pesas": "🏋️ Pesas",
    "pulso": "🤼 Pulso",
    "secuencia": "🧠 Secuencia",
    "obstaculos": "🏃 Obstáculos",
    "fondo": "🛣️ Fondo",
    "sauna": "🧖 Sauna",
}

PESAS_LEVELS = 8
PESAS_FAIL = (0.02, 0.04, 0.07, 0.10, 0.15, 0.21, 0.30)  # de subir del nivel 1 al 2... 7 al 8
PESAS_BASE_KG = 20
PESAS_KG_PER_LEVEL = 10

PULSO_ROUNDS = 5
PULSO_TELL = 0.7
PULSO_MOVES = ("empujar", "aguantar", "amagar")
PULSO_BEATS = {
    "aguantar": "empujar",
    "empujar": "amagar",
    "amagar": "aguantar",
}  # clave gana a valor
PULSO_HINTS = {
    "empujar": "Tensa el brazo.",
    "aguantar": "Respira hondo y se planta.",
    "amagar": "Le bailan los ojos.",
}
PULSO_LABELS = {"empujar": "💪 Empujar", "aguantar": "🛡️ Aguantar", "amagar": "🎭 Amagar"}

SEC_LENGTHS = (4, 5, 6)
SEC_SYMBOLS = {"sec_maiz": "🌽", "sec_gusano": "🪱", "sec_trigo": "🌾", "sec_agua": "💧"}

OBS_COUNT = 8
OBS_TIME = 20.0
OBS_KINDS = {  # clave del botón correcto -> (obstáculo, botón)
    "obs_saltar": ("🪨 Una piedra", "⬆️ Saltar"),
    "obs_agachar": ("🌿 Una rama baja", "⬇️ Agacharse"),
    "obs_esquivar": ("🐕 Un perro", "↔️ Esquivar"),
}

FONDO_LAPS = 5
FONDO_ENERGY = 10
FONDO_PACES = {  # clave: (etiqueta, distancia, coste)
    "fondo_suave": ("🐢 Suave", 1, 1),
    "fondo_normal": ("🏃 Normal", 2, 2),
    "fondo_sprint": ("🔥 Sprint", 3, 4),
}
FONDO_TERRAIN_COST = {"llano": 0, "cuesta": 1, "bajada": -1}
FONDO_COLLAPSE_FACTOR = 0.5

SAUNA_ROUNDS = 8
SAUNA_RISK = (0.0, 0.03, 0.06, 0.10, 0.15, 0.21, 0.30)  # de aguantar desde la ronda 1... 7
SAUNA_BASE_C = 60
SAUNA_C_PER_ROUND = 8

# Jugador "best" de `autoplay`: se planta cuando el riesgo pasa de aquí y se equivoca a veces.
BEST_PESAS_MAX_RISK = 0.21
BEST_SAUNA_MAX_RISK = 0.21
BEST_SLIP = 0.12


@dataclass(frozen=True, slots=True)
class GymButton:
    """Un botón de la pantalla; el cog usa `gallos:gym:{key}` de `custom_id`."""

    key: str
    label: str
    style: str  # "primary", "secondary", "success" o "danger"
    row: int = 0
    disabled: bool = False


def _pct(p: float) -> str:
    return f"{round(p * 100)} %"


class GymGame(ABC):
    """Base de los juegos: `press` ignora lo que no toca y delega en `_apply`."""

    kind: str
    stat: Stat

    def __init__(self) -> None:
        self.done = False
        self._score = 0.0

    @property
    def score(self) -> float:
        return self._score if self.done else 0.0

    def _finish(self, score: float) -> None:
        self.done = True
        self._score = max(0.0, min(1.0, score))

    @abstractmethod
    def text(self) -> str: ...

    @abstractmethod
    def _buttons(self) -> list[GymButton]: ...

    @abstractmethod
    def _apply(self, key: str, now: float) -> None: ...

    @abstractmethod
    def summary(self) -> str: ...

    def buttons(self) -> list[GymButton]:
        return [] if self.done else self._buttons()

    def press(self, key: str, now: float) -> None:
        if not self.done and any(b.key == key for b in self._buttons()):
            self._apply(key, now)


class Pesas(GymGame):
    kind = "pesas"
    stat = Stat.STRENGTH

    def __init__(self, rng: random.Random) -> None:
        super().__init__()
        self._rng = rng
        self.level = 1
        self.failed = False

    def _kg(self) -> int:
        return PESAS_BASE_KG + PESAS_KG_PER_LEVEL * self.level

    def text(self) -> str:
        if self.done:
            return f"**{TITLES[self.kind]}**\n{self.summary()}"
        risk = PESAS_FAIL[self.level - 1]
        return (
            f"**{TITLES[self.kind]}**\n"
            f"Tu gallo sujeta **{self._kg()} kg** (nivel {self.level} de {PESAS_LEVELS}).\n"
            f"Si subes a {self._kg() + PESAS_KG_PER_LEVEL} kg, "
            f"la probabilidad de que se le caiga es del **{_pct(risk)}**.\n"
            "⬆️ Más peso: arriesga para ganar más. ✅ Soltar: te quedas con lo que llevas. "
            "Si se cae, ganas muy poco."
        )

    def _buttons(self) -> list[GymButton]:
        return [
            GymButton("pesas_up", "⬆️ Más peso", "danger"),
            GymButton("pesas_stop", "✅ Soltar", "success"),
        ]

    def _apply(self, key: str, now: float) -> None:
        if key == "pesas_stop":
            self._finish((self.level - 1) / (PESAS_LEVELS - 1))
        elif self._rng.random() < PESAS_FAIL[self.level - 1]:
            self.failed = True
            self._finish((self.level - 1) / (2 * (PESAS_LEVELS - 1)))
        else:
            self.level += 1
            if self.level == PESAS_LEVELS:
                self._finish(1.0)

    def summary(self) -> str:
        if self.failed:
            return f"Se le cayó la barra con {self._kg()} kg al intentar subir más."
        if self.level == PESAS_LEVELS:
            return f"Levantó el máximo: {self._kg()} kg."
        return f"Soltó la barra a tiempo con {self._kg()} kg."


class Pulso(GymGame):
    kind = "pulso"
    stat = Stat.STRENGTH

    def __init__(self, rng: random.Random) -> None:
        super().__init__()
        self._rng = rng
        self.assault = 0
        self.points = 0.0
        self.log: list[str] = []
        self._move = ""
        self.hint_move = ""
        self._next_assault()

    def _next_assault(self) -> None:
        self._move = self._rng.choice(PULSO_MOVES)
        if self._rng.random() < PULSO_TELL:
            self.hint_move = self._move
        else:
            self.hint_move = self._rng.choice([m for m in PULSO_MOVES if m != self._move])

    def text(self) -> str:
        head = (
            f"**{TITLES[self.kind]}**\nCinco asaltos contra un sparring. "
            f"Puntos: **{self.points:g}** de {PULSO_ROUNDS}."
        )
        last = f"\nAsalto anterior: {self.log[-1]}" if self.log else ""
        if self.done:
            return f"{head}{last}\n{self.summary()}"
        return (
            f"{head}{last}\n\n**Asalto {self.assault + 1} de {PULSO_ROUNDS}.** "
            f"{PULSO_HINTS[self.hint_move]}\n"
            "El rival elige en secreto y la pista no siempre es fiable. "
            "Aguantar gana a empujar, empujar gana a amagar y amagar gana a aguantar."
        )

    def _buttons(self) -> list[GymButton]:
        return [GymButton(f"pulso_{m}", PULSO_LABELS[m], "primary") for m in PULSO_MOVES]

    def _apply(self, key: str, now: float) -> None:
        mine = key.removeprefix("pulso_")
        if mine == self._move:
            gain, verdict = 0.5, "empate"
        elif PULSO_BEATS[mine] == self._move:
            gain, verdict = 1.0, "punto para ti"
        else:
            gain, verdict = 0.0, "punto para el rival"
        self.points += gain
        self.log.append(f"tú {mine}, el rival {self._move}: {verdict}.")
        self.assault += 1
        if self.assault == PULSO_ROUNDS:
            self._finish(self.points / PULSO_ROUNDS)
        else:
            self._next_assault()

    def summary(self) -> str:
        return f"Sacó {self.points:g} puntos de {PULSO_ROUNDS} contra el sparring."


class Secuencia(GymGame):
    kind = "secuencia"
    stat = Stat.SPEED

    def __init__(self, rng: random.Random) -> None:
        super().__init__()
        keys = list(SEC_SYMBOLS)
        self.rounds = [[rng.choice(keys) for _ in range(n)] for n in SEC_LENGTHS]
        self.round = 0
        self.pos = 0
        self.showing = True
        self.results: list[float] = []
        self.note = ""

    def text(self) -> str:
        head = f"**{TITLES[self.kind]}**\n"
        if self.done:
            return f"{head}{self.summary()}"
        n = len(self.rounds[self.round])
        if self.showing:
            seq = " ".join(SEC_SYMBOLS[k] for k in self.rounds[self.round])
            return (
                f"{head}{self.note}**Ronda {self.round + 1} de {len(SEC_LENGTHS)}.** "
                f"Memoriza el orden:\n\n# {seq}\n\n"
                "Cuando lo tengas, pulsa Listo: se oculta y la repites con los botones."
            )
        return (
            f"{head}**Ronda {self.round + 1} de {len(SEC_LENGTHS)}.** "
            f"Repite la secuencia: llevas {self.pos} de {n}.\nUn fallo termina la ronda."
        )

    def _buttons(self) -> list[GymButton]:
        if self.showing:
            return [GymButton("sec_ready", "👀 Listo", "success")]
        return [GymButton(k, s, "secondary") for k, s in SEC_SYMBOLS.items()]

    def _apply(self, key: str, now: float) -> None:
        seq = self.rounds[self.round]
        if self.showing:
            self.showing, self.note = False, ""
            return
        if key == seq[self.pos]:
            self.pos += 1
            if self.pos < len(seq):
                return
            self.results.append(1.0)
            self.note = "Ronda perfecta.\n"
        else:
            self.results.append(self.pos / len(seq))
            self.note = f"Fallo: acertaste {self.pos} de {len(seq)}.\n"
        self.round += 1
        self.pos = 0
        if self.round == len(SEC_LENGTHS):
            self._finish(sum(self.results) / len(self.results))
        else:
            self.showing = True

    def summary(self) -> str:
        parts = ", ".join(
            f"{round(r * n)}/{n}" for r, n in zip(self.results, SEC_LENGTHS, strict=False)
        )
        return f"Aciertos por ronda: {parts}."


class Obstaculos(GymGame):
    kind = "obstaculos"
    stat = Stat.SPEED

    def __init__(self, rng: random.Random) -> None:
        super().__init__()
        self.course = [rng.choice(list(OBS_KINDS)) for _ in range(OBS_COUNT)]
        self.index = 0
        self.correct = 0
        self.start: float | None = None
        self.last_now = 0.0
        self.timed_out = False

    def text(self) -> str:
        head = f"**{TITLES[self.kind]}**\n"
        if self.done:
            return f"{head}{self.summary()}"
        if self.start is None:
            return (
                f"{head}Un circuito de {OBS_COUNT} obstáculos y {OBS_TIME:g} segundos en total. "
                "Piedra: saltar. Rama baja: agacharse. Perro: esquivar.\nPulsa Salida para empezar."
            )
        left = max(0.0, OBS_TIME - (self.last_now - self.start))
        name = OBS_KINDS[self.course[self.index]][0]
        return (
            f"{head}**Obstáculo {self.index + 1} de {OBS_COUNT}:** {name}.\n"
            f"Aciertos: {self.correct}. Tiempo restante: {left:.0f} s."
        )

    def _buttons(self) -> list[GymButton]:
        if self.start is None:
            return [GymButton("obs_go", "🏁 ¡Salida!", "success")]
        return [GymButton(k, label, "primary") for k, (_, label) in OBS_KINDS.items()]

    def _apply(self, key: str, now: float) -> None:
        if self.start is None:
            self.start = self.last_now = now
            return
        if now - self.start > OBS_TIME:
            self.timed_out = True
            self._finish(self.correct / OBS_COUNT)
            return
        self.last_now = now
        self.correct += key == self.course[self.index]
        self.index += 1
        if self.index == OBS_COUNT:
            self._finish(self.correct / OBS_COUNT)

    def summary(self) -> str:
        extra = (
            " Se acabó el tiempo y los obstáculos que faltaban cuentan como fallos."
            if self.timed_out
            else ""
        )
        return f"Superó {self.correct} de {OBS_COUNT} obstáculos.{extra}"


def _lap_cost(terrain: str, pace: str) -> int:
    return max(0, FONDO_PACES[pace][2] + FONDO_TERRAIN_COST[terrain])


def _best_distance(terrains: list[str]) -> int:
    """Distancia máxima sin quedarse sin energía para esta secuencia de terrenos."""
    best = 0
    for paces in product(FONDO_PACES, repeat=len(terrains)):
        energy = FONDO_ENERGY
        for t, p in zip(terrains, paces, strict=True):
            energy -= _lap_cost(t, p)
            if energy < 0:
                break
        else:
            best = max(best, sum(FONDO_PACES[p][1] for p in paces))
    return best


class Fondo(GymGame):
    kind = "fondo"
    stat = Stat.ENDURANCE

    def __init__(self, rng: random.Random) -> None:
        super().__init__()
        self.terrains = [rng.choice(list(FONDO_TERRAIN_COST)) for _ in range(FONDO_LAPS)]
        self.best = _best_distance(self.terrains)
        self.lap = 0
        self.energy = FONDO_ENERGY
        self.distance = 0
        self.collapsed = False

    def text(self) -> str:
        head = f"**{TITLES[self.kind]}**\n"
        if self.done:
            return f"{head}{self.summary()}"
        nxt = (
            self.terrains[self.lap + 1]
            if self.lap + 1 < FONDO_LAPS
            else "ninguna, es la última vuelta"
        )
        costs = ", ".join(
            f"{FONDO_PACES[p][0]}: {FONDO_PACES[p][1]} de distancia y "
            f"{_lap_cost(self.terrains[self.lap], p)} de energía"
            for p in FONDO_PACES
        )
        return (
            f"{head}**Vuelta {self.lap + 1} de {FONDO_LAPS}.** "
            f"Terreno ahora: **{self.terrains[self.lap]}**. "
            f"Siguiente: {nxt}.\n"
            f"Energía: **{self.energy}**. Distancia: **{self.distance}**.\n"
            f"En esta vuelta: {costs}.\n"
            "La cuesta cuesta 1 de energía más y la bajada 1 menos. "
            "Si te quedas sin energía, el gallo se desploma."
        )

    def _buttons(self) -> list[GymButton]:
        return [GymButton(k, v[0], "primary") for k, v in FONDO_PACES.items()]

    def _apply(self, key: str, now: float) -> None:
        cost = _lap_cost(self.terrains[self.lap], key)
        if cost > self.energy:
            self.collapsed = True
            self._finish(self.distance / self.best * FONDO_COLLAPSE_FACTOR)
            return
        self.energy -= cost
        self.distance += FONDO_PACES[key][1]
        self.lap += 1
        if self.lap == FONDO_LAPS:
            self._finish(self.distance / self.best)

    def summary(self) -> str:
        if self.collapsed:
            return (
                f"Se desplomó en la vuelta {self.lap + 1} sin energía, "
                f"con {self.distance} de distancia "
                f"(lo máximo era {self.best})."
            )
        return (
            f"Terminó las {FONDO_LAPS} vueltas con {self.distance} de distancia "
            f"(lo máximo era {self.best})."
        )


class Sauna(GymGame):
    kind = "sauna"
    stat = Stat.ENDURANCE

    def __init__(self, rng: random.Random) -> None:
        super().__init__()
        self._rng = rng
        self.round = 1
        self.dizzy = False

    def _temp(self) -> int:
        return SAUNA_BASE_C + SAUNA_C_PER_ROUND * self.round

    def text(self) -> str:
        head = f"**{TITLES[self.kind]}**\n"
        if self.done:
            return f"{head}{self.summary()}"
        risk = SAUNA_RISK[self.round - 1]
        return (
            f"{head}Ronda {self.round} de {SAUNA_ROUNDS}. Temperatura: **{self._temp()} °C**.\n"
            f"Si aguantas la ronda siguiente, la probabilidad de marearse es del "
            f"**{_pct(risk)}**.\n"
            "🔥 Aguantar: sigues y ganas más. 🚪 Salir: te quedas con lo que llevas. "
            "Si se marea, ganas muy poco."
        )

    def _buttons(self) -> list[GymButton]:
        return [
            GymButton("sauna_stay", "🔥 Aguantar", "danger"),
            GymButton("sauna_out", "🚪 Salir", "success"),
        ]

    def _apply(self, key: str, now: float) -> None:
        if key == "sauna_out":
            self._finish((self.round - 1) / (SAUNA_ROUNDS - 1))
        elif self._rng.random() < SAUNA_RISK[self.round - 1]:
            self.dizzy = True
            self._finish((self.round - 1) / (2 * (SAUNA_ROUNDS - 1)))
        else:
            self.round += 1
            if self.round == SAUNA_ROUNDS:
                self._finish(1.0)

    def summary(self) -> str:
        if self.dizzy:
            return f"Se mareó a {self._temp()} °C en la ronda {self.round}."
        if self.round == SAUNA_ROUNDS:
            return f"Aguantó las {SAUNA_ROUNDS} rondas hasta {self._temp()} °C."
        return f"Salió a tiempo en la ronda {self.round}, a {self._temp()} °C."


_GAMES: dict[str, type[GymGame]] = {
    "pesas": Pesas,
    "pulso": Pulso,
    "secuencia": Secuencia,
    "obstaculos": Obstaculos,
    "fondo": Fondo,
    "sauna": Sauna,
}


def start(kind: str, rng: random.Random, now: float) -> GymGame:
    """Empieza un juego nuevo; `ValueError` si no existe."""
    if kind not in _GAMES:
        raise ValueError(f"Entrenamiento desconocido: {kind!r}")
    return _GAMES[kind](rng)  # type: ignore[call-arg]


def pick_kind(stat: Stat, rng: random.Random) -> str:
    """Uno de los dos juegos del stat, a partes iguales."""
    return rng.choice(KINDS[stat])


def _best_key(game: GymGame, keys: list[str], rng: random.Random) -> str:
    if isinstance(game, Pesas):
        return "pesas_up" if PESAS_FAIL[game.level - 1] <= BEST_PESAS_MAX_RISK else "pesas_stop"
    if isinstance(game, Sauna):
        return "sauna_stay" if SAUNA_RISK[game.round - 1] <= BEST_SAUNA_MAX_RISK else "sauna_out"
    if isinstance(game, Pulso):
        return "pulso_" + next(m for m in PULSO_MOVES if PULSO_BEATS[m] == game.hint_move)
    if isinstance(game, Fondo):
        left = (
            FONDO_LAPS - game.lap - 1
        )  # vueltas que quedan tras esta: reserva de 1 de energía cada una
        for pace in ("fondo_sprint", "fondo_normal", "fondo_suave"):
            if game.energy - _lap_cost(game.terrains[game.lap], pace) >= left:
                return pace
        return "fondo_suave"
    if len(keys) == 1:  # salida o "listo"
        return keys[0]
    right = (
        game.rounds[game.round][game.pos]
        if isinstance(game, Secuencia)
        else game.course[game.index]
    )
    return rng.choice([k for k in keys if k != right]) if rng.random() < BEST_SLIP else right


def autoplay(game: GymGame, policy: str, rng: random.Random, now: float = 0.0) -> float:
    """Juega hasta el final ("best" o "random", una pulsación por segundo) y da la nota."""
    while not game.done:
        keys = [b.key for b in game.buttons() if not b.disabled]
        key = rng.choice(keys) if policy == "random" else _best_key(game, keys, rng)
        game.press(key, now)
        now += 1.0
    return game.score
