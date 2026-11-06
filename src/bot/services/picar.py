"""Reglas del juego del pico (`picar`): precios, valores de los minerales y premios.

Lógica pura, sin Discord ni dibujo. La física (`bot.services.picar_physics`) juega la
partida y aquí se le pone precio:

- Cada pico tiene un coste fijo (`OFFERS`) y un tope: el premio del dragón, en veces
  el coste.
- Cada mineral roto suma un multiplicador (`Calibration.values`, en diezmilésimas del
  coste, `SCALE`: 25.000 es ×2,50). La suma es el multiplicador de la partida y el premio
  es el coste por ese multiplicador, redondeado hacia abajo a Y$ enteros y sin pasar del
  tope. Los bloques de multiplicador rotos (`Run.multiplier`) multiplican esa suma antes
  del tope. Llegar al dragón paga el tope. Con céntimos no bastaba: el netherite rompe tantos
  minerales que el carbón ya valía el céntimo mínimo, y un céntimo más en un mineral
  movía el RTP de otro pico varios puntos.

**Biblioteca de semillas.** El juego no simula partidas cualesquiera: elige una semilla
de 0 a `Calibration.library - 1` y juega esa. El calibrador (`docs/calibrar_picar.py`)
ha simulado todas, así que el RTP de cada pico es una cuenta exacta sobre la lista
(`Calibration.rtp`, una fracción) y no una estimación. El calibrador elige los valores
de los minerales (`BASE_VALUES` por un factor `k` de cada pico) para que el RTP quede lo
más cerca posible del 99,5 % sin pasarse.

**Comprobaciones al arrancar** (`check`). La calibración solo vale para la física, el
mundo y los precios con los que se hizo:

- `params_hash` resume la física y el mundo: si alguien toca un parámetro sin
  recalibrar, no coincide.
- La huella (`fingerprint`) vuelve a jugar unas pocas semillas: si el NAS calcula
  distinto que el PC donde se calibró, o cambió el código del motor, no coincide.
- El coste y el tope de cada pico tienen que ser los calibrados: el redondeo a Y$ enteros
  hace que el RTP dependa del coste.

Si algo falla, el juego se apaga y el log dice por qué (ver `bot.cogs.picar`).
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from fractions import Fraction
from functools import cache
from pathlib import Path

from bot.services.picar_physics import PICKS, Run, params_hash, simulate
from bot.services.picar_world import MINERALS

CALIBRATION = Path(__file__).resolve().parent.parent / "assets" / "picar" / "calibracion.json"

#: Unidades de multiplicador por cada vez el coste (diezmilésimas).
SCALE = 10_000
#: RTP al que apunta la calibración.
TARGET_RTP = Fraction(995, 1000)
#: Semillas que vuelve a jugar `fingerprint` (al arrancar el bot y en las pruebas).
FINGERPRINT_SEEDS = 48


@dataclass(frozen=True)
class Offer:
    """Lo que cuesta un pico y lo más que puede pagar."""

    key: str
    name: str
    #: Precio de la tirada, en Y$.
    cost: int
    #: Premio del dragón y tope de cualquier premio, en veces el coste.
    top: int
    #: Multiplica los valores de sus minerales antes de calibrar (el oro, kamikaze). Es
    #: ambientación: el factor `k` de cada pico lo absorbe y el RTP es el mismo.
    bonus: int = 1


OFFERS: dict[str, Offer] = {
    offer.key: offer
    for offer in (
        Offer("wood", "Madera", 10, 50),
        Offer("stone", "Piedra", 50, 150),
        Offer("iron", "Hierro", 200, 500),
        Offer("gold", "Oro", 200, 1500, bonus=3),
        Offer("diamond", "Diamante", 1_000, 2500),
        Offer("netherite", "Netherite", 5_000, 5000),
    )
}

#: Valor relativo de cada mineral. El calibrador los multiplica por el `k` de cada pico.
BASE_VALUES: dict[str, int] = {
    "coal": 1,
    "iron": 2,
    "redstone": 2,
    "lapis": 3,
    "gold": 4,
    "diamond": 10,
    "emerald": 12,
    "quartz": 2,
    "nether_gold": 3,
    "ancient_debris": 40,
    "end_crystal": 120,
}
assert set(BASE_VALUES) == set(MINERALS), "cada mineral necesita su valor base"


def mult_units(values: dict[str, int], broken: dict[str, int], multiplier: int = 1) -> int:
    """Multiplicador de una partida en `SCALE`.

    La suma de lo que vale cada mineral roto, por el producto de los bloques de
    multiplicador. Es lineal en los valores: por eso basta un factor por pico para
    calibrar (ver el módulo).
    """
    return sum(values.get(key, 0) * count for key, count in broken.items()) * multiplier


def payout(offer: Offer, units: int, *, dragon: bool) -> int:
    """Premio en Y$ de una partida con multiplicador `units` (el tope si hay dragón)."""
    top = offer.cost * offer.top
    if dragon:
        return top
    return min(top, offer.cost * units // SCALE)


def format_mult(units: int) -> str:
    """El multiplicador como se enseña: `×12,50` a partir de 125.000 unidades.

    Con dos decimales, redondeado hacia abajo; por debajo de ×0,10, con tres (lo que
    suma un carbón con netherite), para que no salga «×0,00».
    """
    if 0 < units < SCALE // 10:
        whole, rest = divmod(units // 10, 1000)
        return f"×{whole},{rest:03d}"
    whole, rest = divmod(units // 100, 100)
    return f"×{whole:,}".replace(",", ".") + f",{rest:02d}"


@dataclass(frozen=True)
class PickCalibration:
    """La calibración de un pico."""

    cost: int
    top: int
    #: Lo que suma cada mineral, en `SCALE`.
    values: dict[str, int]
    #: RTP exacto sobre la biblioteca.
    rtp: Fraction
    #: Resumen para el informe y la ayuda (probabilidades, percentiles…).
    stats: dict[str, float]


@dataclass(frozen=True)
class Calibration:
    """Lo que guarda `calibracion.json`."""

    library: int
    params: str
    fingerprint: str
    picks: dict[str, PickCalibration]

    @classmethod
    def load(cls, path: Path = CALIBRATION) -> Calibration:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            library=int(data["library"]),
            params=data["params"],
            fingerprint=data["fingerprint"],
            picks={
                key: PickCalibration(
                    cost=int(item["cost"]),
                    top=int(item["top"]),
                    values={k: int(v) for k, v in item["values"].items()},
                    rtp=Fraction(item["rtp"]),
                    stats=dict(item.get("stats", {})),
                )
                for key, item in data["picks"].items()
            },
        )


@cache  # el motor no cambia en un proceso: ~1 s de CPU que no se repite en cada arranque
def fingerprint(seeds: int = FINGERPRINT_SEEDS) -> str:
    """Huella de las primeras `seeds` semillas de cada pico, jugadas con este motor."""
    digest = hashlib.sha256()
    for key in sorted(PICKS):
        for seed in range(seeds):
            digest.update(simulate(seed, key, record=False).fingerprint().encode())
    return digest.hexdigest()


def check(cal: Calibration, *, replay: bool = True) -> str | None:
    """Por qué la calibración no vale con este código; `None` si vale.

    Args:
        replay: Si vuelve a jugar las semillas de la huella (~1 s de CPU; el bot lo hace
            en un hilo al arrancar).
    """
    if cal.params != params_hash():
        return "los parámetros de la física o del mundo han cambiado sin recalibrar"
    if set(cal.picks) != set(OFFERS):
        return "la calibración no tiene los mismos picos que el juego"
    for key, offer in OFFERS.items():
        pick = cal.picks[key]
        if (pick.cost, pick.top) != (offer.cost, offer.top):
            return f"el coste o el tope de {offer.name} no son los calibrados"
        if pick.rtp > TARGET_RTP:
            return f"el RTP de {offer.name} pasa del objetivo"
    if replay and cal.fingerprint != fingerprint():
        return "este motor no juega igual que el que se calibró (la huella no coincide)"
    return None


@dataclass(frozen=True)
class Outcome:
    """Una tirada jugada y pagada."""

    pick: str
    seed: int
    run: Run
    units: int
    payout: int

    @property
    def dragon(self) -> bool:
        return self.run.dragon

    @property
    def capped(self) -> bool:
        """Si el premio se quedó en el tope sin llegar al dragón."""
        offer = OFFERS[self.pick]
        return not self.run.dragon and self.payout == offer.cost * offer.top


def settle(pick: str, run: Run, cal: Calibration) -> Outcome:
    """Pone precio a una partida ya jugada con la calibración `cal`."""
    offer = OFFERS[pick]
    units = mult_units(cal.picks[pick].values, run.broken, run.multiplier)
    return Outcome(pick, run.seed, run, units, payout(offer, units, dragon=run.dragon))


def draw_seed(cal: Calibration) -> int:
    """Semilla de la biblioteca para una tirada nueva."""
    return secrets.randbelow(cal.library)


def play(pick: str, cal: Calibration, seed: int | None = None) -> Outcome:
    """Juega y paga una tirada (con fotogramas, para dibujarla). Sin `seed`, una al azar."""
    if seed is None:
        seed = draw_seed(cal)
    if not 0 <= seed < cal.library:
        raise ValueError("La semilla no está en la biblioteca.")
    return settle(pick, simulate(seed, pick), cal)
