"""Cara o cruz: el vuelo de la moneda, lo que se ve en cada fotograma y el dibujo con Pillow.

La imagen buena la pinta Chromium con canvas (`bot.services.coin_scene` y
`assets/moneda/escena.html`). Este módulo decide **qué** se ve y lo pinta
también con Pillow, más sencillo, para cuando no hay navegador. Las dos
versiones leen los mismos diccionarios (`board_state`, `toss_states`), así que
la moneda está en el mismo sitio y cae igual en las dos.

**La cámara.** La mesa se ve desde delante y algo por encima
(`ELEVATION`). La moneda gira sobre un eje horizontal: tumbada con la cara
arriba se ve como una elipse achatada; de pie (de canto) se ve casi redonda,
mirando a la cámara, con el borde apoyado en el tapete. El giro es un solo
ángulo (`Pose.spin`): 0 es cara arriba, π cruz arriba y ±π/2 de canto. De
ese ángulo salen el achatado de la elipse (`sin(spin + ELEVATION)`), qué cara
se ve (su signo), el grosor del canto y la sombra.

**El lanzamiento** (`toss_poses`):

1. Reposo: la moneda en la mesa con la cara que salió la última vez.
2. Vuelo: sube y baja en parábola mientras gira. Dura más cuanto más hay en
   juego (`tension`): con ×512 en juego, el vuelo es casi el doble.
3. Aterrizaje: llega torcida, rebota y se asienta en el lado que toca. El
   canto aterriza más torcido y se tambalea de un lado a otro antes de
   quedarse de pie: parece que va a caer y no cae.
4. Revelado: el cartel del resultado aparece y el último fotograma, quieto
   un minuto, es el PNG que el cog pone después.

Durante el vuelo la escalera de multiplicadores no se mueve: el resultado no
se sabe hasta que la moneda se para.
"""

from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from bot.services.coin import (
    MAX_FLIPS,
    CoinGame,
    Outcome,
    Side,
    Status,
    format_multiplier,
    multiplier,
)
from bot.utils.gif import local_palette_gif

W, H = 640, 360
#: Dónde descansa la moneda: centro horizontal y línea del tapete en la que se apoya.
COIN_X, TABLE_Y = 232, 268
#: Radio y grosor de la moneda en reposo (píxeles).
RADIUS, THICKNESS = 74.0, 10.0
#: Inclinación de la cámara sobre la mesa.
ELEVATION = math.radians(32)
#: Altura máxima del vuelo (en las mismas unidades que el radio).
LIFT_MAX = 150.0
#: Cuánto crece la moneda en lo más alto (se acerca a la cámara).
LIFT_GROWTH = 0.3

FRAME_MS = 40
#: El último fotograma se queda quieto: el PNG llega antes de que se note el bucle.
FINAL_FRAME_MS = 60_000
REST_FRAMES = 3
FLIGHT_FRAMES = 24
#: Fotogramas de vuelo de más por cada acierto ya conseguido (la tensión).
FLIGHT_FRAMES_PER_WIN = 2
SETTLE_FRAMES = 14
EDGE_SETTLE_FRAMES = 30
REVEAL_FRAMES = 8

ASSETS = Path(__file__).resolve().parent.parent / "assets"
TITLE_FONT = ASSETS / "botes" / "fonts" / "luckiest-guy.woff"
TEXT_FONT = ASSETS / "memes" / "fonts" / "MontserratBold.ttf"


@dataclass(frozen=True, slots=True)
class Media:
    """Lo que el cog sube: el GIF del lanzamiento, el PNG final y cuánto dura el GIF."""

    gif: bytes
    png: bytes
    seconds: float


@dataclass(frozen=True, slots=True)
class Pose:
    """Dónde está la moneda en un fotograma.

    Attributes:
        lift: Altura sobre la mesa (0 apoyada).
        spin: Giro sobre el eje horizontal: 0 cara arriba, π cruz arriba,
            ±π/2 de canto.
        speed: Lo que ha girado desde el fotograma anterior (para la estela).
    """

    lift: float
    spin: float
    speed: float = 0.0


def rest_spin(face: Side | Outcome) -> float:
    """Ángulo de la moneda en reposo con esa cara arriba (o de canto)."""
    if face in (Side.CARA, Outcome.CARA):
        return 0.0
    if face in (Side.CRUZ, Outcome.CRUZ):
        return math.pi
    return math.pi / 2


def flight_frames(tension: int) -> int:
    """Fotogramas de vuelo con `tension` aciertos ya en juego."""
    return FLIGHT_FRAMES + FLIGHT_FRAMES_PER_WIN * min(tension, MAX_FLIPS - 1)


def toss_poses(start: Side, outcome: Outcome, *, tension: int, seed: int) -> list[Pose]:
    """El lanzamiento entero, fotograma a fotograma (sin los del revelado).

    Args:
        start: Cara que hay arriba antes de lanzar.
        outcome: Cómo tiene que caer.
        tension: Aciertos ya en juego: alargan el vuelo.
        seed: Para que el mismo lanzamiento se dibuje igual.
    """
    rng = random.Random(seed)
    s0 = rest_spin(start)
    target = rest_spin(outcome)
    if outcome is Outcome.EDGE and rng.random() < 0.5:
        target = -math.pi / 2  # de pie mirando con la cruz
    turns = 4 + rng.randint(0, 2) + tension // 3
    s_end = s0 + 2 * math.pi * turns + ((target - s0) % (2 * math.pi))
    edge = outcome is Outcome.EDGE
    # Llega sin acabar de girar y se asienta; el canto, más torcido.
    tilt = 0.95 if edge else 0.6
    s_land = s_end - tilt

    poses = [Pose(0.0, s0) for _ in range(REST_FRAMES)]
    flight = flight_frames(tension)
    for i in range(1, flight + 1):
        u = i / flight
        poses.append(Pose(LIFT_MAX * 4 * u * (1 - u), s0 + (s_land - s0) * u))
    settle = EDGE_SETTLE_FRAMES if edge else SETTLE_FRAMES
    for j in range(1, settle + 1):
        p = j / settle
        if edge:
            # Se tambalea de un lado a otro, cada vez menos, y se queda de pie.
            spin = s_end - tilt * math.exp(-2.6 * p) * math.cos(5.5 * math.pi * p)
            bounce = 10 * math.exp(-7 * p) * abs(math.sin(3 * math.pi * p))
        else:
            spin = s_end - tilt * math.exp(-4.5 * p) * math.cos(3 * math.pi * p)
            bounce = 14 * math.exp(-5 * p) * abs(math.sin(3 * math.pi * p))
        if j == settle:
            spin, bounce = s_end, 0.0
        poses.append(Pose(bounce, spin))
    return [
        Pose(p.lift, p.spin, abs(p.spin - poses[i - 1].spin) if i else 0.0)
        for i, p in enumerate(poses)
    ]


def coin_geometry(lift: float, spin: float) -> dict[str, Any]:
    """Lo que hay que pintar de la moneda en una postura (ver la docstring del módulo).

    Returns:
        `x`, `y`: centro de la cara que se ve; `back`: centro de la de detrás;
        `rx`, `ry`: semiejes de la elipse de la cara; `face`: qué cara se ve;
        `shine`: brillo (0-1); `shadow`: elipse de la sombra en el tapete.
    """
    angle = spin + ELEVATION
    squash = math.sin(angle)
    scale = 1 + LIFT_GROWTH * lift / LIFT_MAX
    radius = RADIUS * scale
    # El punto más bajo de la moneda toca el tapete: de pie, el centro sube un radio.
    z = lift + RADIUS * abs(math.sin(spin)) + THICKNESS / 2 * abs(math.cos(spin))
    center_y = TABLE_Y - z * math.cos(ELEVATION)
    # Centro de la cara de la corona respecto al de la moneda (en pantalla, hacia arriba).
    offset = THICKNESS / 2 * math.cos(angle) * scale
    cara_y = center_y - offset
    cruz_y = center_y + offset
    face = Side.CARA if squash >= 0 else Side.CRUZ
    shadow_k = lift / LIFT_MAX
    return {
        "x": COIN_X,
        "y": cara_y if face is Side.CARA else cruz_y,
        "back": cruz_y if face is Side.CARA else cara_y,
        "rx": radius,
        "ry": max(0.6, radius * abs(squash)),
        "face": face.key,
        "shine": 0.5 + 0.5 * math.cos(2 * angle),
        "shadow": {
            "x": COIN_X,
            "y": TABLE_Y,
            "rx": RADIUS * (1 + 0.25 * shadow_k),
            "ry": max(3.0, RADIUS * abs(math.cos(spin)) * math.sin(ELEVATION)),
            "a": 0.55 * (1 - 0.7 * shadow_k),
        },
    }


def _ghosts(pose: Pose) -> list[dict[str, Any]]:
    """Estela: la moneda un poco antes, transparente, cuando gira deprisa."""
    if pose.speed < 0.5 or pose.lift < 1:
        return []
    return [
        dict(coin_geometry(pose.lift, pose.spin - pose.speed * k / 3), a=alpha)
        for k, alpha in ((1, 0.28), (2, 0.14))
    ]


# -- Lo que se ve: escalera, historial y cartel ---------------------------------------------


def _amount(value: int) -> str:
    return f"{value:,}".replace(",", ".") + " Y$"


def meta_state(stake: int) -> dict[str, Any]:
    """Lo fijo de una partida: la apuesta y la escalera de multiplicadores."""
    return {
        "stake": _amount(stake),
        "ladder": [
            {"m": format_multiplier(multiplier(level)), "v": _amount(stake * multiplier(level))}
            for level in range(1, MAX_FLIPS + 1)
        ],
    }


def _history(game: CoinGame | None, upto: int | None = None) -> list[dict[str, Any]]:
    flips = [] if game is None else game.flips[:upto]
    return [{"o": f.outcome.value, "won": f.won} for f in flips]


def banner_for(game: CoinGame) -> dict[str, str] | None:
    """El cartel del último resultado: qué salió y qué supone.

    `kind` decide el color: `win`, `lose`, `edge`, `cash` o `gold`.
    """
    last = game.last
    if game.status is Status.CASHED:
        if game.maxed:
            return {
                "kind": "gold",
                "title": "¡MONEDA DE ORO!",
                "sub": f"Diez seguidas · +{_amount(game.net)}",
            }
        return {
            "kind": "cash",
            "title": "¡COBRADO!",
            "sub": f"+{_amount(game.net)} en {format_multiplier(game.multiplier)}",
        }
    if last is None:
        return None
    if game.status is Status.EDGE:
        return {"kind": "edge", "title": "¡DE CANTO!", "sub": "Perro Sanxe se la queda"}
    side = last.outcome.side
    assert side is not None
    if game.status is Status.LOST:
        return {
            "kind": "lose",
            "title": f"¡{side.label.upper()}!",
            "sub": f"Pediste {last.pick.label.lower()} · adiós a {_amount(game.pot)}",
        }
    return {
        "kind": "win",
        "title": f"¡{side.label.upper()}!",
        "sub": f"{format_multiplier(game.multiplier)} · {_amount(game.pot)}",
    }


def _ladder(game: CoinGame | None, *, level: int | None = None) -> dict[str, Any]:
    if game is None:
        return {"level": 0, "state": "play"}
    state = {
        Status.PLAYING: "play",
        Status.LOST: "lost",
        Status.EDGE: "lost",
        Status.CASHED: "cash",
    }[game.status]
    return {"level": game.wins if level is None else level, "state": state}


def board_state(game: CoinGame | None, *, face: Side | Outcome) -> dict[str, Any]:
    """El fotograma quieto: la moneda en la mesa y el cartel del resultado, si lo hay."""
    pose = Pose(0.0, rest_spin(face))
    return {
        "coin": coin_geometry(pose.lift, pose.spin),
        "ghosts": [],
        "ladder": _ladder(game),
        "history": _history(game),
        "pick": None,
        "banner": banner_for(game) if game is not None else None,
        "bannerA": 1.0,
        "hint": game is None or (game.playing and not game.flips),
    }


def toss_states(game: CoinGame, *, start: Side, seed: int) -> list[dict[str, Any]]:
    """Fotogramas del último lanzamiento de `game` (ya resuelto), revelado incluido.

    Hasta que la moneda se para, la escalera y el historial enseñan lo de
    antes del lanzamiento. Luego aparece el cartel.
    """
    last = game.last
    assert last is not None
    before = len(game.flips) - 1
    level_before = sum(1 for f in game.flips[:before] if f.won)
    poses = toss_poses(start, last.outcome, tension=level_before, seed=seed)
    states = []
    for pose in poses:
        states.append(
            {
                "coin": coin_geometry(pose.lift, pose.spin),
                "ghosts": _ghosts(pose),
                "ladder": {"level": level_before, "state": "flying"},
                "history": _history(game, before),
                "pick": last.pick.key,
                "banner": None,
                "bannerA": 0.0,
                "hint": False,
            }
        )
    final = board_state(game, face=last.outcome)
    for k in range(1, REVEAL_FRAMES + 1):
        states.append(dict(final, bannerA=min(1.0, k / (REVEAL_FRAMES - 2)), pick=last.pick.key))
    return states


def toss_seconds(states: list[dict[str, Any]]) -> float:
    """Lo que dura el GIF hasta el último fotograma."""
    return FRAME_MS * (len(states) - 1) / 1000


# -- Dibujo con Pillow (sin navegador) -------------------------------------------------------

S = 2  # se dibuja al doble y se reduce: bordes suaves
GOLD = (222, 178, 62)
GOLD_DARK = (150, 108, 28)
SILVER = (206, 210, 216)
SILVER_DARK = (128, 134, 144)
BANNER_COLORS = {
    "win": (60, 200, 110),
    "lose": (230, 70, 70),
    "edge": (150, 90, 230),
    "cash": (255, 196, 0),
    "gold": (255, 196, 0),
}


@cache
def _font(size: int, *, title: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(TITLE_FONT if title else TEXT_FONT), size * S)


def _unit_polygon(
    points: list[tuple[float, float]], cx: float, cy: float, r: float
) -> list[tuple[float, float]]:
    return [(cx + x * r, cy + y * r) for x, y in points]


CROWN = [(-0.5, 0.18), (-0.56, -0.3), (-0.28, -0.02), (0.0, -0.4), (0.28, -0.02),
         (0.56, -0.3), (0.5, 0.18)]  # fmt: skip
PLANE = [(0.0, -0.62), (0.07, -0.45), (0.08, -0.1), (0.62, 0.1), (0.62, 0.2), (0.08, 0.1),
         (0.06, 0.42), (0.26, 0.55), (0.26, 0.62), (0.0, 0.56), (-0.26, 0.62), (-0.26, 0.55),
         (-0.06, 0.42), (-0.08, 0.1), (-0.62, 0.2), (-0.62, 0.1), (-0.08, -0.1),
         (-0.07, -0.45)]  # fmt: skip


@cache
def _face(side: str) -> Image.Image:
    """Una cara de la moneda, de frente, al doble de tamaño."""
    size = int(RADIUS * 2 * S)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = size / 2
    r = size / 2
    d.ellipse((0, 0, size - 1, size - 1), fill=GOLD, outline=GOLD_DARK, width=3 * S)
    inner = r * 0.7
    d.ellipse((c - inner, c - inner, c + inner, c + inner), fill=SILVER, outline=GOLD_DARK)
    for k in range(12):  # las doce estrellas del anillo
        a = 2 * math.pi * k / 12 - math.pi / 2
        x, y = c + math.cos(a) * r * 0.85, c + math.sin(a) * r * 0.85
        d.ellipse((x - 3 * S, y - 3 * S, x + 3 * S, y + 3 * S), fill=GOLD_DARK)
    if side == "cara":
        d.polygon(_unit_polygon(CROWN, c, c, inner), fill=SILVER_DARK)
        band = inner * 0.5
        d.rectangle((c - band, c + inner * 0.18, c + band, c + inner * 0.34), fill=SILVER_DARK)
    else:
        d.polygon(_unit_polygon(PLANE, c, c, inner), fill=SILVER_DARK)
    return img


@cache
def _background() -> Image.Image:
    """El tapete con su viñeta (sin la moneda ni el marcador)."""
    img = Image.new("RGB", (W * S, H * S), (16, 70, 44))
    glow = Image.new("L", (W * S, H * S), 0)
    ImageDraw.Draw(glow).ellipse((-200 * S, 40 * S, 660 * S, 520 * S), fill=255)
    glow = glow.filter(ImageFilter.GaussianBlur(80 * S))
    img.paste(Image.new("RGB", img.size, (30, 112, 70)), mask=glow)
    d = ImageDraw.Draw(img)
    d.text((24 * S, 18 * S), "CARA O CRUZ", font=_font(28, title=True), fill=(255, 214, 90))
    return img


def _paint_coin(img: Image.Image, geo: dict[str, Any], alpha: float = 1.0) -> None:
    # Se pinta solo el recuadro de la moneda: pintar capas del tamaño de la
    # imagen entera en cada fotograma multiplicaba por cinco el tiempo.
    x, y, back = geo["x"] * S, geo["y"] * S, geo["back"] * S
    rx, ry = geo["rx"] * S, geo["ry"] * S
    left, top = int(x - rx) - 2, int(min(y, back) - ry) - 2
    box = (int(2 * rx) + 5, int(abs(y - back) + 2 * ry) + 5)
    layer = Image.new("RGBA", box, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x, y, back = x - left, y - top, back - top
    upper, lower = sorted((y, back))
    d.ellipse((x - rx, back - ry, x + rx, back + ry), fill=GOLD_DARK)
    d.rectangle((x - rx, upper, x + rx, lower), fill=GOLD_DARK)
    face = _face(geo["face"]).resize(
        (max(1, int(rx * 2)), max(1, int(ry * 2))), Image.Resampling.BILINEAR
    )
    layer.alpha_composite(face, (int(x - rx), int(y - ry)))
    if alpha < 1:
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
    img.alpha_composite(layer, (max(0, left), max(0, top)), (max(0, -left), max(0, -top)))


def _paint_shadow(img: Image.Image, shadow: dict[str, Any]) -> None:
    blur = 6 * S
    x, y, rx, ry = (shadow[k] * S for k in ("x", "y", "rx", "ry"))
    left, top = int(x - rx) - 3 * blur, int(y - ry) - 3 * blur
    mask = Image.new("L", (int(2 * rx) + 6 * blur, int(2 * ry) + 6 * blur), 0)
    cx, cy = x - left, y - top
    ImageDraw.Draw(mask).ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=int(255 * shadow["a"]))
    mask = mask.filter(ImageFilter.GaussianBlur(blur))
    img.paste((0, 0, 0, 255), (left, top, left + mask.width, top + mask.height), mask=mask)


def _paint_ladder(d: ImageDraw.ImageDraw, meta: dict[str, Any], ladder: dict[str, Any]) -> None:
    level, state = ladder["level"], ladder["state"]
    x0, x1 = 456 * S, 626 * S
    row_h = 29
    for i, row in enumerate(meta["ladder"]):
        n = i + 1
        y0 = (342 - n * row_h) * S
        y1 = y0 + (row_h - 4) * S
        fill, text = (20, 40, 30), (150, 170, 160)
        if n <= level:
            fill, text = (90, 70, 20), (255, 220, 120)
            if state == "lost":
                fill, text = (70, 30, 30), (220, 120, 120)
        if n == level:
            fill = {"lost": (150, 40, 40), "cash": (255, 196, 0)}.get(state, (210, 160, 30))
            text = (30, 20, 0) if state != "lost" else (255, 255, 255)
        if n == level + 1 and state in ("play", "flying"):
            d.rounded_rectangle((x0, y0, x1, y1), radius=8 * S, outline=(255, 214, 90), width=S)
        d.rounded_rectangle((x0, y0, x1, y1), radius=8 * S, fill=fill)
        d.text((x0 + 10 * S, y0 + 5 * S), row["m"], font=_font(13), fill=text)
        d.text((x1 - 10 * S, y0 + 5 * S), row["v"], font=_font(13), fill=text, anchor="ra")
    d.text((x0, 22 * S), f"Apuesta {meta['stake']}", font=_font(13), fill=(220, 230, 225))


def _paint_history(d: ImageDraw.ImageDraw, history: list[dict[str, Any]]) -> None:
    for i, flip in enumerate(history[-12:]):
        x = (30 + i * 30) * S
        y = 336 * S
        color = {"cara": GOLD, "cruz": SILVER, "canto": (150, 90, 230)}[flip["o"]]
        ring = (60, 200, 110) if flip["won"] else (230, 70, 70)
        d.ellipse((x - 11 * S, y - 11 * S, x + 11 * S, y + 11 * S), fill=color, outline=ring,
                  width=2 * S)  # fmt: skip
        letter = {"cara": "C", "cruz": "X", "canto": "!"}[flip["o"]]
        d.text((x, y), letter, font=_font(11), fill=(30, 30, 30), anchor="mm")


def _paint_banner(img: Image.Image, banner: dict[str, str], alpha: float) -> None:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    color = BANNER_COLORS[banner["kind"]]
    cx, cy = COIN_X * S, 70 * S
    d.rounded_rectangle((cx - 190 * S, cy - 34 * S, cx + 190 * S, cy + 40 * S), radius=16 * S,
                        fill=(10, 14, 12, 210), outline=color, width=3 * S)  # fmt: skip
    d.text((cx, cy - 8 * S), banner["title"], font=_font(30, title=True), fill=color, anchor="mm")
    d.text((cx, cy + 24 * S), banner["sub"], font=_font(13), fill=(235, 238, 240), anchor="mm")
    if alpha < 1:
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
    img.alpha_composite(layer)


def _base(meta: dict[str, Any], state: dict[str, Any]) -> Image.Image:
    """Tapete, escalera e historial: lo que no cambia mientras vuela la moneda."""
    img = _background().convert("RGBA")
    d = ImageDraw.Draw(img)
    _paint_ladder(d, meta, state["ladder"])
    _paint_history(d, state["history"])
    if state["hint"]:
        d.text((COIN_X * S, 70 * S), "¿CARA O CRUZ?", font=_font(30, title=True),
               fill=(255, 240, 200), anchor="mm")  # fmt: skip
    return img


def paint(
    meta: dict[str, Any], state: dict[str, Any], base: Image.Image | None = None
) -> Image.Image:
    """Un fotograma con Pillow, a 640×360. `base` reutiliza el fondo de `_base`."""
    img = (base or _base(meta, state)).copy()
    _paint_shadow(img, state["coin"]["shadow"])
    for ghost in reversed(state["ghosts"]):
        _paint_coin(img, ghost, ghost["a"])
    _paint_coin(img, state["coin"])
    if state["banner"] and state["bannerA"] > 0:
        _paint_banner(img, state["banner"], state["bannerA"])
    return img.convert("RGB").reduce(S)


def encode(frames: list[Image.Image]) -> Media:
    """GIF (con el último fotograma quieto) y PNG del último fotograma."""
    durations = [FRAME_MS] * (len(frames) - 1) + [FINAL_FRAME_MS]
    gif = local_palette_gif(frames, durations)
    png = io.BytesIO()
    # `optimize=True` tarda ~230 ms para ahorrar ~5 % (126 KB frente a 120).
    frames[-1].save(png, format="PNG", compress_level=6)
    return Media(gif=gif, png=png.getvalue(), seconds=FRAME_MS * (len(frames) - 1) / 1000)


class CoinRenderer:
    """Dibujo de reserva con Pillow: el mismo contenido que la escena, más sencillo."""

    def board(self, game: CoinGame | None, *, stake: int, face: Side | Outcome) -> bytes:
        """PNG de la mesa quieta."""
        img = paint(meta_state(stake), board_state(game, face=face))
        out = io.BytesIO()
        img.save(out, format="PNG", optimize=True)
        return out.getvalue()

    def toss(self, game: CoinGame, *, start: Side, seed: int) -> Media:
        """GIF del último lanzamiento de `game` y PNG del final."""
        meta = meta_state(game.stake)
        bases: dict[str, Image.Image] = {}
        frames = []
        for state in toss_states(game, start=start, seed=seed):
            key = repr((state["ladder"], state["history"], state["hint"]))
            if key not in bases:
                bases[key] = _base(meta, state)
            frames.append(paint(meta, state, bases[key]))
        return encode(frames)
