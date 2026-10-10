"""Autobús: qué se ve en la mesa, la animación de cada carta y el dibujo con Pillow.

La imagen buena la pinta Chromium con canvas (`bot.services.bus_scene` y
`assets/autobus/escena.html`). Este módulo decide **qué** se ve y lo pinta
también con Pillow, más sencillo, para cuando no hay navegador. Las dos
versiones leen los mismos diccionarios (`board_state`, `reveal_states`), así
que cada carta, el autobús y los carteles están en el mismo sitio en las dos.

**La mesa** (`Board`): arriba, una carretera con cinco paradas (COLOR, ALTURA,
RANGO, PALO y LA VUELTA, la última dorada) y el autobús en la parada que toca;
debajo, cinco huecos de carta (el quinto, separado y dorado, es el doble o
nada). El hueco activo va resaltado con su pregunta encima; al cobrar, la
carta que venía se enseña semitransparente con «ERA ESTA».

**La animación de una mano** (`reveal_states`) se dibuja *antes* de que el
jugador elija, así que solo depende de las cartas, de la mano y de si acierta
(nunca de la apuesta ni de cifras de dinero):

1. Reposo y tensión: la carta se levanta y tiembla, más tiempo cuanto más alta
   es la mano (la primera es corta; la cuarta y la quinta, mucho más largas).
2. Giro: la carta gira sobre sí misma (escala horizontal con el coseno) y se
   descubre. Es el **tramo común**: idéntico gane o pierda.
3. Cola «win»: brillo, marca de acierto y el autobús avanza a la siguiente
   parada (con cartel de «autobús completo» en la cuarta y de «la vuelta»,
   dorado y con destellos, en la quinta).
4. Cola «lose»: marca roja, la carta tiembla y el autobús se avería, con humo.

Las fases lentas se hacen con fotogramas más largos, no con más fotogramas
(`FRAME_MS` es el general; cada estado trae su `ms`): un GIF de ~40 fotogramas
en vez de cien. El primer fotograma de la cola «lose» va entero (`full`)
porque en el navegador se dibuja justo después de la cola «win», no del tramo
común.
"""

from __future__ import annotations

import io
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from bot.services.blackjack import Card
from bot.services.bus import Hand
from bot.services.cards_render import CardRenderer
from bot.utils.gif import local_palette_gif

W, H = 640, 360
#: Se dibuja al doble y se reduce: bordes suaves (como la moneda).
S = 2
#: Milisegundos de un fotograma normal de la animación.
FRAME_MS = 50
#: El último fotograma se queda quieto un minuto (el cog pone el siguiente).
FINAL_FRAME_MS = 60_000
#: Fotogramas de la tensión de la mano 1; cada mano suma `TENSION_PER_HAND`.
TENSION_FRAMES = 6
TENSION_PER_HAND = 3
#: Fotogramas del giro de la carta.
FLIP_FRAMES = 8

#: Centro de cada hueco de carta (y de su parada); el quinto va separado.
SLOT_X = (66, 184, 302, 420, 574)
CARD_W, CARD_H, CARD_Y = 90, 126, 132
CARD_CY = CARD_Y + CARD_H // 2
ROAD_TOP, ROAD_BOT = 58, 94
BUS_BASE = 91
MARK_Y = 278
#: Momento (segundos desde la avería) del humo que se queda quieto en la mesa.
FINAL_SMOKE = 0.9

#: La pregunta de cada mano, encima del hueco activo.
QUESTIONS = (
    "¿ROJO O NEGRO?",
    "¿MAYOR, MENOR O IGUAL?",
    "¿DENTRO, FUERA O POSTE?",
    "¿QUÉ PALO?",
    "¿DOBLE O NADA?",
)

ASSETS = Path(__file__).resolve().parent.parent / "assets"
TITLE_FONT = ASSETS / "botes" / "fonts" / "luckiest-guy.woff"
TEXT_FONT = ASSETS / "memes" / "fonts" / "MontserratBold.ttf"


@dataclass(frozen=True, slots=True)
class Media:
    """Lo que el cog sube: el GIF de la mano, el PNG final y cuánto dura el GIF.

    Attributes:
        gif: El GIF; `None` en el plan B de Pillow (solo imagen fija).
        png: El último fotograma.
        seconds: Lo que dura el GIF hasta el último fotograma (0 sin GIF).
    """

    gif: bytes | None
    png: bytes
    seconds: float


@dataclass(frozen=True, slots=True)
class Banner:
    """Un cartel sobre la mesa. `kind`: `win`, `lose`, `cash` o `gold`."""

    kind: str
    title: str
    sub: str = ""


@dataclass(frozen=True, slots=True)
class Board:
    """La mesa quieta.

    Attributes:
        cards: Cartas ya descubiertas, en orden (0 a 5).
        results: Acierto o fallo de cada una (misma longitud que `cards`).
        active: Hueco (0-4) que toca jugar, resaltado; `None` si no se juega.
        ghost: Al cobrar, la carta siguiente, semitransparente («ERA ESTA»).
        banner: Cartel sobre la mesa.
    """

    cards: tuple[Card, ...] = ()
    results: tuple[bool, ...] = ()
    active: int | None = 0
    ghost: Card | None = None
    banner: Banner | None = None


@dataclass(frozen=True, slots=True)
class Reveal:
    """Lo que se precarga para una mano: el GIF si acierta y el GIF si falla."""

    win: Media
    lose: Media


# -- Estados: lo que lee la escena ---------------------------------------------------------------


def meta_state() -> dict[str, Any]:
    """Lo fijo de la escena: el nombre de cada parada."""
    return {"stops": [Hand(n).stop.upper() for n in range(1, 6)]}


def _card(card: Card) -> dict[str, Any]:
    return {"rank": card.rank, "suit": card.suit, "label": card.label, "red": card.is_red}


def _slot(
    card: Card | None = None,
    *,
    down: bool = False,
    flip: float | None = None,
    lift: float = 0.0,
    dx: float = 0.0,
    rot: float = 0.0,
    glow: float = 0.0,
    glow_kind: str = "win",
    mark: str | None = None,
    mark_a: float = 1.0,
    ghost: bool = False,
) -> dict[str, Any]:
    """Un hueco: vacío, con el dorso (`down`) o con una carta (`flip` 0 dorso, 1 cara)."""
    if flip is None:
        flip = 0.0 if down else 1.0
    return {
        "card": None if card is None else _card(card),
        "down": down,
        "flip": flip,
        "lift": lift,
        "dx": dx,
        "rot": rot,
        "glow": glow,
        "glowKind": glow_kind,
        "mark": mark,
        "markA": mark_a,
        "ghost": ghost,
    }


def _bus(
    x: float,
    *,
    hop: float = 0.0,
    dx: float = 0.0,
    tilt: float = 0.0,
    soot: float = 0.0,
    alert: float = 0.0,
    smoke: Sequence[Sequence[float]] = (),
) -> dict[str, Any]:
    """El autobús: `x` es la parada (con decimales entre dos)."""
    return {
        "x": x,
        "hop": hop,
        "dx": dx,
        "tilt": tilt,
        "soot": soot,
        "alert": alert,
        "smoke": [list(p) for p in smoke],
    }


def _state(
    slots: list[dict[str, Any]],
    bus: dict[str, Any],
    *,
    active: int | None,
    banner: dict[str, Any] | None = None,
    sparks: Sequence[Sequence[float]] = (),
    ms: int = FRAME_MS,
    full: bool = False,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "slots": slots,
        "active": active,
        "question": QUESTIONS[active] if active is not None else None,
        "bus": bus,
        "sparks": [list(s) for s in sparks],
        "banner": banner,
        "ms": ms,
    }
    if full:
        state["full"] = True
    return state


def _banner(banner: Banner, a: float = 1.0) -> dict[str, Any]:
    return {"kind": banner.kind, "title": banner.title, "sub": banner.sub, "a": a}


def smoke_puffs(t: float) -> list[list[float]]:
    """Bolas de humo `[dx, dy, radio, alfa]` (respecto al autobús) `t` segundos tras la avería.

    Sale un chorro continuo desde la parte de atrás del techo: cada bola nace
    en su momento, sube, se ensancha y se desvanece, y vuelve a nacer.
    """
    count, gap = 16, 0.09
    life = count * gap
    puffs = []
    for k in range(count):
        age = t - k * gap
        if age < 0:
            continue
        age %= life
        grow = age / life
        puffs.append(
            [
                -32 - age * 8 + math.sin(k * 1.7 + age * 3) * 4,
                -42 - age * 58,
                9 + age * 18,
                round(0.95 * (1 - grow) ** 1.2, 3),
            ]
        )
    return puffs


def _burst(
    x: float, y: float, t: float, seed: int, *, count: int, size: float = 7.0, spread: float = 70
) -> list[list[float]]:
    """Chispas `[x, y, radio, alfa, clase]` que salen de (x, y) y caen; `t` va de 0 a 1."""
    rng = random.Random(seed)
    sparks = []
    for _ in range(count):
        angle = rng.uniform(0, math.tau)
        speed = rng.uniform(0.4, 1.0) * spread
        kind = rng.choice((0, 0, 1, 2))
        px = x + math.cos(angle) * speed * t
        py = y + math.sin(angle) * speed * t + 40 * t * t
        radius = size * rng.uniform(0.6, 1.2) * (1 - 0.35 * t)
        alpha = max(0.0, 1 - t**2)
        sparks.append([round(px, 1), round(py, 1), round(radius, 1), round(alpha, 2), kind])
    return sparks


def _smooth(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def _back(u: float) -> float:
    """Entrada con un pequeño rebote (0 → 1 pasando por ~1,1)."""
    u = min(1.0, max(0.0, u))
    c = 1.9
    return 1 + (c + 1) * (u - 1) ** 3 + c * (u - 1) ** 2


def bus_stop_for(board: Board) -> tuple[float, bool]:
    """Parada del autobús en una mesa quieta y si está averiado.

    Con la mano en juego espera en su parada; al cobrar, en la de la carta
    siguiente (o en la última si ya dio la vuelta); si falló, se queda donde
    falló.
    """
    if False in board.results:
        return float(max(len(board.cards) - 1, 0)), True
    if board.active is not None:
        return float(board.active), False
    return float(min(len(board.cards), 4)), False


def board_state(board: Board) -> dict[str, Any]:
    """El estado de la mesa quieta, tal como lo leen la escena y Pillow."""
    slots = []
    for i in range(5):
        if i < len(board.cards):
            won = board.results[i] if i < len(board.results) else True
            slots.append(_slot(board.cards[i], mark="win" if won else "lose"))
        elif i == len(board.cards) and board.ghost is not None:
            slots.append(_slot(board.ghost, ghost=True))
        elif i == board.active:
            slots.append(_slot(down=True))
        else:
            slots.append(_slot())
    x, broken = bus_stop_for(board)
    bus = _bus(x, soot=0.6 if broken else 0.0, tilt=-0.05 if broken else 0.0)
    if broken:
        bus["smoke"] = smoke_puffs(FINAL_SMOKE)
    banner = None if board.banner is None else _banner(board.banner)
    return _state(slots, bus, active=board.active, banner=banner, ms=FINAL_FRAME_MS)


def tension_frames(hand: int) -> int:
    """Fotogramas de tensión de una mano (1-5): cuanto más alta, más larga."""
    return TENSION_FRAMES + TENSION_PER_HAND * (hand - 1)


def reveal_states(
    cards: Sequence[Card], hand: int, *, seed: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Los estados de la animación de la mano `hand`: `(común, cola win, cola lose)`.

    El GIF si acierta es `común + win` y si falla, `común + lose`. No depende
    de lo que pida el jugador: solo de las cartas, de la mano y de si acierta.

    Args:
        cards: Las cartas de la partida (al menos `hand`).
        hand: La mano que se juega, de 1 a 5.
        seed: Semilla de las chispas y del temblor.

    Raises:
        ValueError: Si la mano no está entre 1 y 5 o faltan cartas.
    """
    if not 1 <= hand <= 5 or len(cards) < hand:
        raise ValueError("Hace falta una mano de 1 a 5 y al menos esas cartas.")
    i = hand - 1
    card = cards[i]
    rng = random.Random(seed)
    phase = rng.uniform(0, math.tau)
    side = rng.choice((-1, 1))
    ms = FRAME_MS + 10 * i

    def slots(**changes: Any) -> list[dict[str, Any]]:
        row = [_slot(cards[j], mark="win") for j in range(i)]
        row.append(changes.pop("target", None) or _slot(card, down=True))
        row += [_slot() for _ in range(4 - i)]
        return row

    def target(**kw: Any) -> dict[str, Any]:
        return _slot(card, **kw)

    bus = _bus(float(i))

    # Tramo común --------------------------------------------------------------------------
    common = [_state(slots(), bus, active=i, ms=250)]
    n = tension_frames(hand)
    peak_lift = 5.0 + 3.0 * i
    for k in range(n):
        u = (k + 1) / n
        wobble = math.sin(k * 2.1 + phase)
        amp = (1.0 + 0.6 * i) * u
        lift = peak_lift * _smooth(u * 1.4) + 0.8 * math.sin(k * 0.9)
        t = target(
            down=True,
            lift=lift,
            dx=amp * wobble,
            rot=0.012 * (1 + 0.3 * i) * u * math.cos(k * 1.9 + phase),
            glow=(0.1 + 0.12 * i) * u,
            glow_kind="gold",
        )
        common.append(_state(slots(target=t), bus, active=i, ms=ms))
    last = common[-1]["slots"][i]
    for k in range(1, FLIP_FRAMES + 1):
        u = k / FLIP_FRAMES
        t = target(
            flip=_smooth(u),
            lift=last["lift"] * (1 - u) + 18 * math.sin(math.pi * u),
            dx=last["dx"] * (1 - u),
            rot=last["rot"] * (1 - u),
            glow=last["glow"] * (1 - u),
            glow_kind="gold",
        )
        common.append(_state(slots(target=t), bus, active=i, ms=40))
    common.append(_state(slots(target=target()), bus, active=i, ms=min(150 + 100 * i, 500)))

    # Cola win -----------------------------------------------------------------------------
    cx, cy = SLOT_X[i], CARD_CY
    win: list[dict[str, Any]] = []
    pops, mark_a = 4, (0.5, 1.3, 1.1, 1.0)
    final = hand == 5
    kind = "gold" if final else "win"
    glow = "gold" if final else "win"
    spark_total = pops + (8 if final else 6)
    for k in range(pops):
        t = target(
            lift=(8, 6, 3, 0)[k],
            glow=(1.0, 0.9, 0.7, 0.5)[k],
            glow_kind=glow,
            mark="win",
            mark_a=mark_a[k],
        )
        burst = _burst(cx, cy, (k + 1) / spark_total, seed + 1, count=14 if final else 9)
        win.append(_state(slots(target=t), bus, active=None, sparks=burst))
    won_card = target(glow=0.35, glow_kind=glow, mark="win")
    if final:
        title, sub = "¡LA VUELTA!", "DOBLE O NADA"
    elif hand == 4:
        title, sub = "¡AUTOBÚS COMPLETO!", "4 DE 4"
    else:
        title, sub = "¡SIGUIENTE PARADA!", f"PARADA {hand} DE 4"
    banner = Banner(kind, title, sub)
    steps = 6 if final else 5
    end_slots = slots(target=won_card)
    if not final:
        end_slots[i + 1] = _slot(down=True)
    # Las chispas llegan a 0,8 (no a 1) para que las del último fotograma, que se
    # queda quieto, sigan viéndose.
    last_sparks: list[list[float]] = []
    for k in range(1, steps + 1):
        u = k / steps
        sparks = _burst(cx, cy, 0.8 * (pops + k) / spark_total, seed + 1, count=14 if final else 9)
        if final:
            hop = 9 * abs(math.sin(u * math.pi * 3))
            sparks += [
                s
                for m in range(3)
                for s in _burst(
                    SLOT_X[(m * 2) % 5 + m % 2],
                    230,
                    0.8 * u,
                    seed + 7 * m,
                    count=6,
                    size=8,
                    spread=55,
                )
            ]
            frame = _state(
                slots(target=won_card), _bus(float(i), hop=hop), active=None, sparks=sparks
            )
        elif k < steps:
            moving = _bus(i + _smooth(u), hop=2.5 * math.sin(u * math.pi))
            frame = _state(slots(target=won_card), moving, active=None, sparks=sparks)
        else:
            frame = _state(end_slots, _bus(i + 1.0), active=i + 1, sparks=sparks)
        win.append(frame)
        last_sparks = sparks
    # El cartel entra con el resto quieto: así solo cambia el recuadro de abajo.
    for b in range(1, 4):
        win.append(
            _state(
                end_slots,
                _bus(float(i) if final else i + 1.0),
                active=None if final else i + 1,
                banner=_banner(banner, _back(b / 3)),
                sparks=last_sparks,
            )
        )
    win[-1]["banner"] = _banner(banner)

    # Cola lose ----------------------------------------------------------------------------
    lose: list[dict[str, Any]] = []
    shake, lose_banner = 8, Banner("lose", "¡FIN DEL TRAYECTO!", "")
    for k in range(shake):
        decay = 1 - k / shake
        grow = k / (shake - 1)
        t = target(
            dx=side * 6 * math.cos(k * 2.2) * decay,
            rot=side * 0.04 * math.sin(k * 2.2) * decay,
            glow=1.0 - 0.6 * grow,
            glow_kind="lose",
            mark="lose",
            mark_a=(0.5, 1.4, 1.15)[k] if k < 3 else 1.0,
        )
        broken = _bus(
            float(i),
            dx=1.6 * math.sin(k * 3.1) * decay,
            tilt=-0.05 * grow,
            soot=0.6 * grow,
            alert=1.0 if k % 2 == 0 and k < shake - 1 else 0.0,
            smoke=smoke_puffs(FINAL_SMOKE * grow) if k >= 2 else (),
        )
        lose.append(_state(slots(target=t), broken, active=None, full=(k == 0)))
    # El cartel entra con el resto quieto (como en la cola win): solo cambia el recuadro de abajo.
    dead = lose[-1]
    for b in range(1, 4):
        lose.append(dict(dead, banner=_banner(lose_banner, _back(b / 3)), ms=FRAME_MS))
    lose[-1]["banner"] = _banner(lose_banner)
    return common, win, lose


def durations(states: Sequence[dict[str, Any]]) -> list[int]:
    """Milisegundos de cada fotograma; el último se queda quieto un minuto."""
    return [int(s["ms"]) for s in states[:-1]] + [FINAL_FRAME_MS]


# -- Pillow: el dibujo de reserva --------------------------------------------------------------

GOLD = (255, 214, 92)
BANNER_COLORS = {
    "win": (40, 200, 116),
    "lose": (214, 56, 56),
    "cash": (64, 148, 232),
    "gold": (244, 196, 52),
}
GLOW_COLORS = {"win": (93, 255, 154), "lose": (255, 77, 77), "gold": (255, 210, 74)}


@cache
def _font(size: int, *, title: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(TITLE_FONT if title else TEXT_FONT), size * S)


@cache
def _cards() -> CardRenderer:
    return CardRenderer()


def _background() -> Image.Image:
    """Tapete, ciudad y carretera: lo que no se mueve."""
    img = Image.new("RGB", (W * S, H * S), (17, 84, 52))
    d = ImageDraw.Draw(img)
    for r in range(0, 170, 8):  # viñeta: bandas oscuras en los bordes
        shade = int(40 * (1 - r / 170))
        d.rectangle(
            (r * S, r * S, (W - r) * S, (H - r) * S),
            outline=(17 - shade // 4, 84 - shade, 52 - shade // 2),
            width=8 * S,
        )
    d.rectangle((0, 0, W * S, (ROAD_BOT + 6) * S), fill=(18, 44, 62))
    rng = random.Random(7)
    x = -6
    while x < W:
        bw, bh = int(16 + rng.random() * 26), int(14 + rng.random() * 22)
        d.rectangle((x * S, (ROAD_TOP - bh) * S, (x + bw) * S, ROAD_TOP * S), fill=(12, 34, 51))
        for wy in range(ROAD_TOP - bh + 3, ROAD_TOP - 4, 5):
            for wx in range(x + 3, x + bw - 3, 5):
                if rng.random() < 0.35:
                    d.rectangle((wx * S, wy * S, (wx + 2) * S, (wy + 2) * S), fill=(150, 130, 70))
        x += bw + 1 + int(rng.random() * 4)
    d.rectangle((0, (ROAD_TOP - 3) * S, W * S, (ROAD_TOP + 2) * S), fill=(89, 96, 107))
    d.rectangle((0, (ROAD_TOP + 2) * S, W * S, ROAD_BOT * S), fill=(36, 39, 46))
    for dash in range(0, W, 22):
        d.rectangle((dash * S, 75 * S, (dash + 12) * S, 77 * S), fill=(150, 135, 75))
    d.rectangle((0, ROAD_BOT * S, W * S, (ROAD_BOT + 4) * S), fill=(141, 150, 163))
    d.rectangle((0, (ROAD_BOT + 4) * S, W * S, (ROAD_BOT + 7) * S), fill=(75, 82, 93))
    for i, cx in enumerate(SLOT_X):
        pole = (199, 154, 46) if i == 4 else (170, 178, 191)
        d.rectangle(((cx - 1) * S, 24 * S, (cx + 2) * S, ROAD_TOP * S), fill=pole)
    for y in range(118, 300, 11):
        d.line((497 * S, y * S, 497 * S, (y + 5) * S), fill=(96, 120, 60), width=2 * S)
    return img


def _centered(
    d: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font: Any, fill: Any
) -> None:
    d.text((xy[0] * S, xy[1] * S), text, font=font, fill=fill, anchor="mm")


def _paint_plates(d: ImageDraw.ImageDraw, meta: dict[str, Any], state: dict[str, Any]) -> None:
    bus = state["bus"]
    for i, cx in enumerate(SLOT_X):
        slot = state["slots"][i]
        won, lost = slot["mark"] == "win", slot["mark"] == "lose"
        here = state["active"] == i or (
            state["active"] is None and round(bus["x"]) == i and not won and not lost
        )
        if i == 4:
            fill, text = (232, 176, 40), (59, 41, 5)
        elif won:
            fill, text = (45, 190, 112), (5, 43, 21)
        elif lost:
            fill, text = (214, 70, 70), (255, 255, 255)
        elif here:
            fill, text = (250, 210, 80), (59, 41, 5)
        else:
            fill, text = (34, 78, 150), (232, 240, 255)
        d.rounded_rectangle(
            ((cx - 42) * S, 5 * S, (cx + 42) * S, 25 * S),
            5 * S,
            fill=fill,
            outline=(240, 240, 240),
            width=S,
        )
        _centered(d, (cx, 15.5), meta["stops"][i], _font(10), text)


def _paint_bus(img: Image.Image, state: dict[str, Any]) -> None:
    bus = state["bus"]
    lo = max(0, min(int(bus["x"]), 3))
    hi = lo + 1
    px = SLOT_X[lo] + (SLOT_X[hi] - SLOT_X[lo]) * (bus["x"] - lo)
    x, y = (px + bus["dx"]) * S, (BUS_BASE - bus["hop"]) * S
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse(
        (x - 43 * S, (BUS_BASE + 2) * S - 4 * S, x + 43 * S, (BUS_BASE + 2) * S + 4 * S),
        fill=(0, 0, 0, 100),
    )
    d.rounded_rectangle(
        (x - 39 * S, y - 35 * S, x + 39 * S, y - 8 * S),
        6 * S,
        fill=(245, 190, 20),
        outline=(122, 82, 6),
        width=S,
    )
    d.rectangle((x - 38 * S, y - 17 * S, x + 38 * S, y - 11 * S), fill=(31, 95, 191))
    for k in range(5):
        wx = x + (-34 + k * 12.5) * S
        d.rounded_rectangle(
            (wx, y - 31 * S, wx + 10.5 * S, y - 20 * S),
            2 * S,
            fill=(185, 230, 247),
            outline=(36, 52, 71),
        )
    d.polygon(
        [
            (x + 30 * S, y - 31 * S),
            (x + 34.5 * S, y - 31 * S),
            (x + 37 * S, y - 19 * S),
            (x + 30 * S, y - 19 * S),
        ],
        fill=(185, 230, 247),
        outline=(36, 52, 71),
    )
    if bus["soot"] > 0:
        d.rounded_rectangle(
            (x - 39 * S, y - 35 * S, x + 39 * S, y - 8 * S),
            6 * S,
            fill=(12, 10, 10, int(120 * bus["soot"])),
        )
    for wx in (-24, 24):
        d.ellipse((x + (wx - 7) * S, y - 13 * S, x + (wx + 7) * S, y + 1 * S), fill=(20, 22, 26))
        d.ellipse((x + (wx - 2) * S, y - 8 * S, x + (wx + 2) * S, y - 4 * S), fill=(154, 160, 170))
    for dx, dy, r, a in bus["smoke"]:
        cx, cy = (px + dx) * S, (BUS_BASE + dy) * S
        d.ellipse((cx - r * S, cy - r * S, cx + r * S, cy + r * S), fill=(85, 85, 92, int(255 * a)))
    img.paste(layer, (0, 0), layer)


def _paint_card(img: Image.Image, i: int, slot: dict[str, Any]) -> None:
    if slot["card"] is None and not slot["down"]:
        return
    card = None
    faceup = slot["card"] is not None and slot["flip"] > 0.5
    if faceup:
        card = Card(slot["card"]["rank"], slot["card"]["suit"])
    sprite = _cards().card_sprite(card).resize((CARD_W * S, CARD_H * S), Image.Resampling.LANCZOS)
    if slot["ghost"]:
        alpha = sprite.getchannel("A").point(lambda v: v // 2)
        sprite.putalpha(alpha)
    x = int((SLOT_X[i] - CARD_W / 2 + slot["dx"]) * S)
    y = int((CARD_Y - slot["lift"]) * S)
    if slot["glow"] > 0:
        glow = GLOW_COLORS.get(slot["glowKind"], GLOW_COLORS["win"])
        ImageDraw.Draw(img).rounded_rectangle(
            (x - 3 * S, y - 3 * S, x + (CARD_W + 3) * S, y + (CARD_H + 3) * S),
            10 * S,
            outline=glow,
            width=3 * S,
        )
    img.paste(sprite, (x, y), sprite)


def _paint_slots(img: Image.Image, state: dict[str, Any]) -> None:
    d = ImageDraw.Draw(img)
    for i, slot in enumerate(state["slots"]):
        left, right = (SLOT_X[i] - CARD_W // 2) * S, (SLOT_X[i] + CARD_W // 2) * S
        if slot["card"] is None and not slot["down"]:
            outline = (190, 150, 60) if i == 4 else (80, 130, 100)
            d.rounded_rectangle(
                (left, CARD_Y * S, right, (CARD_Y + CARD_H) * S),
                8 * S,
                fill=(14, 66, 40),
                outline=outline,
                width=2 * S,
            )
            _centered(d, (SLOT_X[i], CARD_CY + 3), str(i + 1), _font(40, title=True), (40, 110, 76))
    if state["active"] is not None:
        i = state["active"]
        d.rounded_rectangle(
            (
                (SLOT_X[i] - CARD_W // 2 - 5) * S,
                (CARD_Y - 5) * S,
                (SLOT_X[i] + CARD_W // 2 + 5) * S,
                (CARD_Y + CARD_H + 5) * S,
            ),
            11 * S,
            outline=(255, 215, 94),
            width=3 * S,
        )
    for i, slot in enumerate(state["slots"]):
        _paint_card(img, i, slot)
        d = ImageDraw.Draw(img)
        if slot["ghost"]:
            _centered(d, (SLOT_X[i], CARD_CY + 36), " ERA ESTA ", _font(15, title=True), GOLD)
        if slot["mark"]:
            ok = slot["mark"] == "win"
            cx, cy, r = SLOT_X[i] * S, MARK_Y * S, 12 * S
            d.ellipse(
                (cx - r, cy - r, cx + r, cy + r),
                fill=(31, 174, 98) if ok else (216, 58, 58),
                outline=(255, 255, 255),
                width=2 * S,
            )
            if ok:
                d.line(
                    [
                        (cx - 5.5 * S, cy + 0.5 * S),
                        (cx - 1.6 * S, cy + 4.6 * S),
                        (cx + 5.8 * S, cy - 4.2 * S),
                    ],
                    fill=(255, 255, 255),
                    width=3 * S,
                )
            else:
                d.line(
                    [(cx - 4.6 * S, cy - 4.6 * S), (cx + 4.6 * S, cy + 4.6 * S)],
                    fill=(255, 255, 255),
                    width=3 * S,
                )
                d.line(
                    [(cx + 4.6 * S, cy - 4.6 * S), (cx - 4.6 * S, cy + 4.6 * S)],
                    fill=(255, 255, 255),
                    width=3 * S,
                )


def _paint_banner(img: Image.Image, banner: dict[str, Any]) -> None:
    d = ImageDraw.Draw(img)
    title_font, sub_font = _font(30, title=True), _font(13)
    tw = d.textlength(banner["title"], font=title_font) / S
    sw = d.textlength(banner["sub"], font=sub_font) / S + 22 if banner["sub"] else 0
    w = max(250.0, tw + sw + (56 if sw else 44))
    left, right = (W / 2 - w / 2) * S, (W / 2 + w / 2) * S
    color = BANNER_COLORS.get(banner["kind"], (200, 200, 200))
    d.rounded_rectangle(
        (left, 294 * S, right, 344 * S), 12 * S, fill=color, outline=(255, 255, 255), width=2 * S
    )
    title_x = (W / 2 - w / 2 + 22 + tw / 2) if sw else W / 2
    _centered(d, (title_x, 321), banner["title"], title_font, (255, 255, 255))
    if sw:
        px = W / 2 + w / 2 - 14 - sw / 2
        d.rounded_rectangle(
            ((px - sw / 2) * S, 306 * S, (px + sw / 2) * S, 332 * S), 13 * S, fill=(6, 20, 14)
        )
        _centered(d, (px, 320), banner["sub"], sub_font, (255, 224, 138))


def paint(meta: dict[str, Any], state: dict[str, Any]) -> Image.Image:
    """Un fotograma con Pillow, a 640×360 (más sencillo que el de Chromium)."""
    img = _background().copy()
    _paint_plates(ImageDraw.Draw(img), meta, state)
    _paint_bus(img, state)
    d = ImageDraw.Draw(img)
    if state["active"] is not None and state["question"]:
        font = _font(10)
        w = d.textlength(state["question"], font=font) / S + 24
        cx = min(max(SLOT_X[state["active"]], w / 2 + 8), W - w / 2 - 8)
        d.rounded_rectangle(
            ((cx - w / 2) * S, 106 * S, (cx + w / 2) * S, 125 * S),
            9 * S,
            fill=(4, 22, 14),
            outline=(255, 214, 110),
            width=S,
        )
        _centered(d, (cx, 115.5), state["question"], font, (255, 224, 138))
    _paint_slots(img, state)
    if state["banner"]:
        _paint_banner(img, state["banner"])
    return img.convert("RGB").reduce(S)


def encode(frames: Sequence[Image.Image], frame_ms: Sequence[int]) -> Media:
    """GIF (con el último fotograma quieto) y PNG del último fotograma.

    Args:
        frames: Los fotogramas enteros, a 640×360.
        frame_ms: Milisegundos de cada uno (ver `durations`).
    """
    gif = local_palette_gif(list(frames), list(frame_ms))
    png = io.BytesIO()
    frames[-1].save(png, format="PNG", optimize=True)
    seconds = sum(frame_ms[:-1]) / 1000
    return Media(gif=gif, png=png.getvalue(), seconds=seconds)


def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


class BusRenderer:
    """Dibujo de reserva con Pillow: el mismo contenido que la escena, más sencillo."""

    def board(self, board: Board) -> bytes:
        """PNG de la mesa quieta."""
        return _png(paint(meta_state(), board_state(board)))

    def reveal(self, cards: Sequence[Card], hand: int, *, seed: int) -> Reveal:
        """Las dos mesas finales de la mano (sin GIF): la que acierta y la que falla."""
        _, win, lose = reveal_states(cards, hand, seed=seed)
        meta = meta_state()
        return Reveal(
            win=Media(gif=None, png=_png(paint(meta, win[-1])), seconds=0.0),
            lose=Media(gif=None, png=_png(paint(meta, lose[-1])), seconds=0.0),
        )
