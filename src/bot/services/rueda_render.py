"""Lo que se ve de la Rueda, fotograma a fotograma, y su dibujo de reserva con Pillow.

Python decide todo lo que se ve y la escena (`assets/rueda/escena.html`) solo pinta:
`meta_state` es lo fijo (las porciones, el expositor de la semana, la racha y si
la mascota activa es el hámster) y cada estado de `spin_states` dice cuánto ha
girado la rueda, dónde está la aguja, qué hacen las bombillas y qué cartel sale.
El último estado de `spin_states` es el mismo que `result_state`, así que el GIF
acaba exactamente en la imagen quieta que lo sustituye.

**El giro** (`spin_motion`) es una función del tiempo, como los rodillos de la
tragaperras:

1. Un tirón hacia atrás (`BACK_DEGREES`) para coger impulso.
2. Arranque hasta la velocidad máxima (`TOP_SPEED`) en `ACCEL_MS`.
3. Frenada larga con curva `(1 - u)^p` hasta parar exactamente en el punto
   sorteado (`rueda.landing_angle`). Las vueltas se eligen para que la frenada
   dure unos `DECEL_SECONDS`.

Si la aguja va a parar cerca de una raya (el expositor, los 15.000 Y$, los 5.000 o
el maletín) hay **tensión** (`tension`): una vuelta más y una frenada más lenta
(`TENSE_POWER`), para que se vea pasar la raya despacio.

**Fotogramas solo donde se notan.** Mientras la rueda va rápida, uno cada 50 ms
(20 por segundo); cuando frena, cada fotograma dura lo que tarda en avanzar
`SLOW_STEP` grados, hasta 130 ms. Es cámara lenta con fotogramas largos, no con
más fotogramas: así el GIF no engorda.

**La aguja** es una lengüeta que chocan los clavos de los bordes de las porciones
(`pointer_deflection`): se dobla al acercarse un clavo y vuelve de golpe al pasar.
Cuanto más rápido gira la rueda, menos se dobla (a 20 fotogramas por segundo no
se vería el temblor).

Todo es determinista: el giro sale de `Draw.seed`, así que pintar antes del clic y
pintar al pulsar dan los mismos bytes.
"""

from __future__ import annotations

import io
import random
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from bot.services.rueda import PRIZE_BY_KEY, Draw, PrizeKind, Wedge, landing_angle
from bot.utils.gif import local_palette_gif

W, H = 600, 400
#: Centro y radio de la rueda (a 1x); la escena los lee de `meta.geo`.
CX, CY, RADIUS = 214, 206, 176

#: Lo que retrocede la rueda antes de arrancar y lo que tarda.
BACK_DEGREES = 9.0
BACK_MS = 250
HOLD_MS = 60
#: Fotogramas del tirón: va despacio, así que bastan pocos.
WINDUP_MS = 80
#: Arranque hasta la velocidad máxima.
ACCEL_MS = 450
#: Velocidad máxima, en grados por segundo.
TOP_SPEED = 820.0
#: Potencia de la curva de frenada: cuanto más alta, más larga la cola lenta.
POWER = 3.0
TENSE_POWER = 3.8
#: Duración buscada de la frenada (se ajusta con las vueltas).
DECEL_SECONDS = 3.4
#: Grados que avanza la rueda en un fotograma lento, y lo más que dura uno.
SLOW_STEP = 14.0
FAST_MS = 50
SLOW_MAX_MS = 130
#: Por encima de esta velocidad (°/s) los fotogramas son de `FAST_MS`.
FAST_SPEED = 360.0
#: Grados a una raya por debajo de los cuales hay tensión.
TENSION_DEGREES = 14.0
#: Lo más que se dobla la aguja, en grados, y en cuántos grados antes de un clavo empieza.
POINTER_MAX = 24.0
POINTER_REACH = 5.0
#: Bombillas del aro.
BULBS = 24

#: Lo que dura el último fotograma del GIF (se queda quieto hasta que llega el PNG).
FINAL_FRAME_MS = 60_000
#: Fotogramas y duración de cada paso del final.
GLOW_FRAMES, GLOW_MS = 3, 80
BANNER_FRAMES, BANNER_MS = 5, 60
BURST_FRAMES, BURST_MS = 8, 70

#: Premios que celebran con confeti.
CELEBRATE = frozenset(
    {"y1000", "y2500", "y5000", "y15000", "xp1000", "regalo", "maletin", "expositor", "otra"}
)

ASSETS = Path(__file__).resolve().parent.parent / "assets"
TITLE_FONT = ASSETS / "botes" / "fonts" / "luckiest-guy.woff"
TEXT_FONT = ASSETS / "memes" / "fonts" / "MontserratBold.ttf"


@dataclass(frozen=True, slots=True)
class Media:
    """Lo que sube el cog: el GIF del giro, el PNG final y cuánto dura el GIF."""

    gif: bytes
    png: bytes
    seconds: float


# -- Lo fijo -------------------------------------------------------------------------------


def meta_state(
    wedges: Sequence[Wedge],
    *,
    showcase: dict[str, Any] | None = None,
    streak: int = 0,
    pet: str | None = None,
) -> dict[str, Any]:
    """Lo que no cambia en todo el giro.

    Args:
        showcase: El expositor de la semana: `{"key", "name", "left", "of"}` o `None`
            si se han agotado.
        streak: Días seguidos girando.
        pet: Clave de la especie de la mascota activa (el hámster sale en la escena).
    """
    return {
        "geo": {"cx": CX, "cy": CY, "r": RADIUS, "w": W, "h": H, "bulbs": BULBS},
        "wedges": [
            {
                "i": w.index,
                "a0": round(w.start, 4),
                "a1": round(w.end, 4),
                "key": w.prize.key,
                "kind": w.prize.kind.value,
                "label": w.prize.label,
                "sliver": w.sliver,
            }
            for w in wedges
        ],
        "showcase": showcase,
        "streak": streak,
        "pet": pet if pet == "hamster" else None,
    }


def rest_angle(seed: int) -> float:
    """Dónde está la rueda antes de girar (sale de la semilla: cada día una distinta)."""
    return round(random.Random(seed ^ 0x5EED).uniform(0, 360), 3)


# -- El giro --------------------------------------------------------------------------------


def _slivers(wedges: Sequence[Wedge]) -> list[float]:
    return [w.middle for w in wedges if w.sliver]


def tension(wedges: Sequence[Wedge], draw: Draw) -> bool:
    """Si la aguja va a parar cerca de una raya (o en una): frenada más lenta."""
    wedge = wedges[draw.wedge]
    point = wedge.start + wedge.width * draw.stop
    for middle in _slivers(wedges):
        gap = abs((point - middle + 180) % 360 - 180)
        if gap <= TENSION_DEGREES:
            return True
    return False


@dataclass(frozen=True, slots=True)
class Motion:
    """El giro entero: dónde está la rueda en cada momento."""

    start: float
    total: float
    decel_ms: float
    power: float

    @property
    def release(self) -> float:
        """Ángulo al soltar el tirón."""
        return self.start - BACK_DEGREES

    @property
    def accel_end(self) -> float:
        return self.release + TOP_SPEED * ACCEL_MS / 2000

    @property
    def duration_ms(self) -> float:
        return BACK_MS + HOLD_MS + ACCEL_MS + self.decel_ms

    def at(self, ms: float) -> tuple[float, float]:
        """`(ángulo, velocidad en °/s)` a los `ms` milisegundos de empezar."""
        if ms < BACK_MS:
            u = ms / BACK_MS
            ease = u * u * (3 - 2 * u)
            return self.start - BACK_DEGREES * ease, 0.0
        ms -= BACK_MS
        if ms < HOLD_MS:
            return self.release, 0.0
        ms -= HOLD_MS
        if ms < ACCEL_MS:
            t = ms / 1000
            return self.release + 0.5 * TOP_SPEED / (
                ACCEL_MS / 1000
            ) * t * t, TOP_SPEED * ms / ACCEL_MS
        ms -= ACCEL_MS
        u = min(1.0, ms / self.decel_ms)
        rest = self.start + self.total - self.accel_end
        angle = self.accel_end + rest * (1 - (1 - u) ** self.power)
        speed = TOP_SPEED * (1 - u) ** (self.power - 1)
        return angle, speed


def spin_motion(wedges: Sequence[Wedge], draw: Draw) -> Motion:
    """El giro que lleva la rueda de su reposo (`rest_angle`) al punto sorteado."""
    start = rest_angle(draw.seed)
    target = landing_angle(wedges[draw.wedge], draw.stop)
    tense = tension(wedges, draw)
    power = TENSE_POWER if tense else POWER
    accel = TOP_SPEED * ACCEL_MS / 2000 - BACK_DEGREES
    wanted = TOP_SPEED * DECEL_SECONDS / power + accel
    base = (target - start) % 360
    turns = max(1, round((wanted - base) / 360)) + (1 if tense else 0)
    total = base + 360 * turns
    rest = total - accel
    decel_ms = power * rest / TOP_SPEED * 1000
    return Motion(start=start, total=total, decel_ms=decel_ms, power=power)


def pointer_deflection(wedges: Sequence[Wedge], angle: float, speed: float) -> float:
    """Lo que se dobla la aguja (grados, positivo hacia donde gira la rueda).

    El clavo más cercano por la izquierda la empuja según se acerca; cuando la pasa,
    la aguja vuelve casi de golpe.
    """
    if speed > TOP_SPEED * 0.95:
        return 0.0
    strength = POINTER_MAX * max(0.2, 1 - speed / 700)
    best = 0.0
    for wedge in wedges:
        # Dónde está el clavo (borde de la porción) respecto a la aguja, a las 12.
        d = (wedge.start + angle + 180) % 360 - 180
        if -POINTER_REACH < d <= 0:
            best = max(best, strength * (1 + d / POINTER_REACH))
        elif 0 < d < 1.2:
            best = max(best, strength * (1 - d / 1.2) * 0.5)
    return round(best, 2)


def _ms(value: float) -> int:
    """Duración de un fotograma: múltiplo de 10 ms (la unidad del GIF son centésimas)."""
    return max(FAST_MS, min(SLOW_MAX_MS, int(round(value / 10)) * 10))


def spin_times(motion: Motion) -> list[tuple[float, int]]:
    """Instantes de cada fotograma del giro y lo que dura cada uno, hasta que para."""
    out: list[tuple[float, int]] = []
    t = 0.0
    end = motion.duration_ms
    while t < end:
        angle, speed = motion.at(t)
        if t < BACK_MS + HOLD_MS:
            step = WINDUP_MS
        elif speed >= FAST_SPEED or t < BACK_MS + HOLD_MS + ACCEL_MS:
            step = FAST_MS
        else:
            step = _ms(SLOW_STEP / max(speed, 1e-6) * 1000)
        final_angle, _ = motion.at(end)
        if final_angle - angle < 0.2:
            break
        out.append((t, step))
        t += step
    return out


# -- Estados ------------------------------------------------------------------------------


def banner_for(prize_key: str, kind: PrizeKind, detail: str | None = None) -> dict[str, str]:
    """El cartel del resultado: qué ha tocado, en grande, y una línea debajo."""
    prize = PRIZE_BY_KEY[prize_key]
    if kind is PrizeKind.MONEY:
        return {"kind": "money", "title": f"¡{prize.name}!", "sub": detail or "Premio en dinero"}
    if kind is PrizeKind.XP:
        return {"kind": "xp", "title": f"¡+{prize.name}!", "sub": detail or "Experiencia"}
    if kind is PrizeKind.BOX:
        names = {"carton": "CAJA DE CARTÓN", "regalo": "CAJA DE REGALO", "maletin": "¡EL MALETÍN!"}
        title = names.get(prize_key, prize.label)
        return {"kind": "item", "title": title if title.startswith("¡") else f"¡{title}!",
                "sub": detail or "A la mochila"}  # fmt: skip
    if kind is PrizeKind.TREAT:
        return {"kind": "item", "title": "¡PIRULETA!", "sub": detail or "Para tu mascota"}
    if kind is PrizeKind.PIECE:
        return {"kind": "item", "title": "¡PIEZA DE LA FERIA!", "sub": detail or "Colección"}
    if kind is PrizeKind.AGAIN:
        return {"kind": "again", "title": "¡OTRA VUELTA!", "sub": detail or "Gira otra vez"}
    if kind is PrizeKind.SHOWCASE:
        return {"kind": "showcase", "title": "¡EL EXPOSITOR!", "sub": detail or "Premio gordo"}
    return {"kind": "nothing", "title": "INSPECCIÓN", "sub": detail or "Hoy no toca"}


def _base(**changes: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "angle": 0.0,
        "blur": 0.0,
        "pointer": 0.0,
        "lights": 0,
        "mode": "idle",
        "win": -1,
        "winA": 0.0,
        "banner": None,
        "bannerA": 0.0,
        "bannerS": 1.0,
        "burst": -1.0,
        "seed": 0,
        "hamster": 0.0,
        "hint": False,
    }
    state.update(changes)
    return state


def board_state(draw: Draw | None, *, hint: bool = True) -> dict[str, Any]:
    """La rueda quieta antes de girar (con la vuelta sorteada, en su reposo)."""
    angle = rest_angle(draw.seed) if draw is not None else 0.0
    return _base(angle=angle, hint=hint, seed=draw.seed if draw else 0)


def result_state(wedges: Sequence[Wedge], draw: Draw, banner: dict[str, str]) -> dict[str, Any]:
    """La rueda parada en el premio con su cartel: el último fotograma y el PNG."""
    angle = (rest_angle(draw.seed) + spin_motion(wedges, draw).total) % 360
    return _base(
        angle=round(angle, 4),
        mode="win" if banner["kind"] != "nothing" else "lose",
        lights=0,
        win=draw.wedge,
        winA=1.0,
        banner=banner,
        bannerA=1.0,
        seed=draw.seed,
    )


def spin_states(
    wedges: Sequence[Wedge], draw: Draw, banner: dict[str, str], *, hamster: bool = False
) -> tuple[list[dict[str, Any]], list[int]]:
    """Los fotogramas del giro y del final, con lo que dura cada uno (ms).

    El primero es `board_state` sin la pista y el último, `result_state`.
    """
    motion = spin_motion(wedges, draw)
    states: list[dict[str, Any]] = []
    durations: list[int] = []
    previous = motion.start
    clock = 0.0
    for index, (t, step) in enumerate(spin_times(motion)):
        angle, speed = motion.at(t)
        blur = min(14.0, abs(angle - previous) * 0.6) if speed > FAST_SPEED else 0.0
        previous = angle
        states.append(
            _base(
                angle=round(angle % 360, 4),
                blur=round(blur, 2),
                pointer=pointer_deflection(wedges, angle, speed),
                lights=index,
                mode="spin" if t >= BACK_MS else "idle",
                seed=draw.seed,
                hamster=round(t / 1000 * (1 + speed / 120), 3) if hamster else 0.0,
            )
        )
        durations.append(step)
        clock = t + step
    final = result_state(wedges, draw, banner)
    hamster_at = clock / 1000 * 1.0 if hamster else 0.0
    rest = dict(final, banner=None, bannerA=0.0, winA=0.0, mode="spin", hamster=hamster_at)
    # La aguja se suelta y la porción ganadora se enciende.
    for k in range(1, GLOW_FRAMES + 1):
        states.append(dict(rest, winA=round(k / GLOW_FRAMES, 3), mode=final["mode"], lights=k))
        durations.append(GLOW_MS)
    # El cartel salta (escala con rebote) mientras las bombillas parpadean.
    scales = (0.55, 0.85, 1.08, 1.02, 1.0)
    for k in range(BANNER_FRAMES):
        states.append(
            dict(
                final,
                bannerA=round(min(1.0, (k + 1) / 3), 3),
                bannerS=scales[k],
                lights=GLOW_FRAMES + 1 + k,
                hamster=hamster_at,
            )
        )
        durations.append(BANNER_MS)
    if draw.prize in CELEBRATE:
        for k in range(BURST_FRAMES):
            states.append(
                dict(
                    final,
                    burst=round(k * BURST_MS / 1000, 3),
                    lights=GLOW_FRAMES + BANNER_FRAMES + 1 + k,
                    hamster=hamster_at,
                )
            )
            durations.append(BURST_MS)
    states.append(final)
    durations.append(FINAL_FRAME_MS)
    return states, durations


def seconds(durations: Sequence[int]) -> float:
    """Lo que dura el GIF hasta su último fotograma."""
    return sum(durations[:-1]) / 1000


# -- Dibujo con Pillow (sin Node ni navegador) ------------------------------------------------

S = 2
#: Colores de cada premio en el dibujo de reserva (la escena tiene los suyos).
COLORS: dict[str, tuple[int, int, int]] = {
    "y300": (37, 99, 235),
    "y600": (124, 58, 237),
    "y1000": (13, 148, 136),
    "y2500": (234, 88, 12),
    "y5000": (250, 204, 21),
    "y15000": (255, 230, 120),
    "xp250": (219, 39, 119),
    "xp1000": (190, 24, 93),
    "carton": (161, 98, 7),
    "regalo": (220, 38, 38),
    "maletin": (24, 24, 27),
    "chuche": (244, 114, 182),
    "pieza": (22, 163, 74),
    "otra": (6, 182, 212),
    "nada": (82, 82, 91),
    "expositor": (255, 255, 255),
}


def _font(size: int, *, title: bool = False) -> ImageFont.FreeTypeFont:
    path = TITLE_FONT if title else TEXT_FONT
    try:
        return ImageFont.truetype(str(path), size * S)
    except OSError:
        return ImageFont.load_default()


def paint(meta: dict[str, Any], state: dict[str, Any]) -> Image.Image:
    """Un fotograma con Pillow, a `W`×`H`: porciones de colores, aguja y cartel."""
    img = Image.new("RGB", (W * S, H * S), (20, 12, 40))
    d = ImageDraw.Draw(img)
    cx, cy, r = CX * S, CY * S, RADIUS * S
    d.ellipse(
        (cx - r - 12 * S, cy - r - 12 * S, cx + r + 12 * S, cy + r + 12 * S), fill=(201, 151, 40)
    )
    for wedge in meta["wedges"]:
        # Pillow mide desde las 3 en sentido horario; la rueda, desde las 12.
        a0 = wedge["a0"] + state["angle"] - 90
        a1 = wedge["a1"] + state["angle"] - 90
        color = COLORS.get(wedge["key"], (120, 120, 120))
        if state["win"] == wedge["i"] and state["winA"] > 0:
            color = tuple(min(255, int(c + (255 - c) * 0.35 * state["winA"])) for c in color)
        d.pieslice((cx - r, cy - r, cx + r, cy + r), a0, a1, fill=color, outline=(240, 200, 90))
    d.ellipse((cx - 30 * S, cy - 30 * S, cx + 30 * S, cy + 30 * S), fill=(40, 20, 60),
              outline=(240, 200, 90), width=3 * S)  # fmt: skip
    tip = cy - r + 8 * S
    d.polygon([(cx - 13 * S, cy - r - 22 * S), (cx + 13 * S, cy - r - 22 * S), (cx, tip)],
              fill=(250, 220, 120), outline=(120, 70, 10))  # fmt: skip
    d.text((505 * S, 40 * S), "LA RUEDA", font=_font(30, title=True), fill=(255, 226, 120),
           anchor="mm")  # fmt: skip
    showcase = meta.get("showcase")
    if showcase:
        d.text((505 * S, 90 * S), "EXPOSITOR", font=_font(12), fill=(230, 220, 255), anchor="mm")
        d.text((505 * S, 112 * S), showcase["name"], font=_font(13), fill=(255, 255, 255),
               anchor="mm")  # fmt: skip
    banner = state.get("banner")
    if banner and state["bannerA"] > 0:
        d.rounded_rectangle((cx - 170 * S, cy + 40 * S, cx + 170 * S, cy + 120 * S), 18 * S,
                            fill=(30, 16, 60), outline=(255, 214, 110), width=3 * S)  # fmt: skip
        d.text((cx, cy + 70 * S), banner["title"], font=_font(28, title=True),
               fill=(255, 236, 160), anchor="mm")  # fmt: skip
        d.text((cx, cy + 102 * S), banner["sub"], font=_font(12), fill=(235, 230, 255),
               anchor="mm")  # fmt: skip
    return img.reduce(S)


def encode(frames: list[Image.Image], durations: Sequence[int]) -> Media:
    """GIF (con su último fotograma quieto) y PNG del último fotograma."""
    gif = local_palette_gif(frames, list(durations))
    png = io.BytesIO()
    frames[-1].save(png, format="PNG", compress_level=6)
    return Media(gif=gif, png=png.getvalue(), seconds=seconds(durations))


class RuedaRenderer:
    """Dibujo de reserva con Pillow: la misma rueda, más sencilla."""

    def board(self, meta: dict[str, Any], state: dict[str, Any]) -> bytes:
        """PNG de la rueda quieta."""
        out = io.BytesIO()
        paint(meta, state).save(out, format="PNG", compress_level=6)
        return out.getvalue()

    def spin(
        self, meta: dict[str, Any], states: list[dict[str, Any]], durations: Sequence[int]
    ) -> Media:
        """GIF del giro (un fotograma de cada tres, para no tardar) y PNG del final."""
        keep = [i for i in range(len(states)) if i % 3 == 0 or i == len(states) - 1]
        frames = [paint(meta, states[i]) for i in keep]
        merged = []
        for n, i in enumerate(keep):
            nxt = keep[n + 1] if n + 1 < len(keep) else len(states)
            merged.append(sum(durations[i:nxt]))
        return encode(frames, merged)
