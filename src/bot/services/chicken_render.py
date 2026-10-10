"""Dibujo del Pollo: la carretera, el pollo y los coches, en GIF y PNG.

Vista cenital de una carretera genérica: acera a la izquierda, carriles de
asfalto con una alcantarilla en cada uno que lleva su multiplicador, y la meta
a la derecha. El pollo cruza de izquierda a derecha y los coches bajan por
los carriles. Cuando el pollo cruza un carril, cae una valla que para el
tráfico de ese carril (como en el Chicken Road original) y el coche que venía
se queda frenado detrás.

Todo se dibuja con formas de Pillow; no hay sprites. Lo único externo son las
fuentes (Luckiest Guy de los Botes para los números, Montserrat para el
resto).

**Un paso es un GIF y luego un PNG**, como las tiradas de los Botes:

1. Tensión: el pollo se agacha y tiembla en el bordillo, la alcantarilla del
   siguiente carril parpadea y al fondo del carril se encienden unos faros.
   Dura más cuanto más alto es el multiplicador en juego
   (`tension_seconds`). Es idéntica gane o pierda: no delata nada.
2. Salto: el pollo cruza al carril con la cámara siguiéndole.
3. El coche entra por arriba. Si el carril era seguro, cae la valla y el
   coche frena en seco con marcas de neumático; si no, atropella al pollo
   (estrella, plumas y pollo aplastado).

El último fotograma del GIF es exactamente el PNG que el cog pone después
(`board`), con 60 s de duración para que no se note el bucle mientras
llega el PNG.

El autocobro es un solo GIF con saltos rápidos y la tensión larga solo en
el último carril.

Coste: el fondo de cada dificultad (asfalto, líneas, alcantarillas con sus
números) se pinta una vez y se guarda (~4-10 MB por dificultad). Cada
fotograma recorta la ventana de la cámara y dibuja encima lo que se mueve.
Un paso son ~20-35 fotogramas, ~0,2-0,4 s de CPU fuera del event loop, y el
GIF pesa ~100-250 KB porque cada fotograma solo guarda lo que cambia
(`bot.utils.gif.local_palette_gif`).

Daltonismo (deuteranopia): nada depende solo del color. Carril cruzado =
valla con rayas y alcantarilla con borde grueso y ✓; atropello = estrella de
picos y pollo aplastado con ojos en X.
"""

from __future__ import annotations

import io
import math
import random
import threading
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from bot.services.chicken import (
    ChickenGame,
    Difficulty,
    Status,
    format_multiplier,
    multiplier_cents,
    short_multiplier,
)
from bot.utils.gif import local_palette_gif

ASSETS = Path(__file__).resolve().parent.parent / "assets"
TITLE_FONT = ASSETS / "botes" / "fonts" / "luckiest-guy.woff"
TEXT_FONT = ASSETS / "memes" / "fonts" / "MontserratBold.ttf"

# -- Medidas (en píxeles de la imagen final; se dibuja al doble) -----------------------

W, H = 640, 320
#: Se dibuja al doble y se reduce: círculos y letras sin dientes de sierra.
S = 2
HUD_H = 50
SIDEWALK = 128
LANE = 104
FINISH = 170
#: Altura de la fila de alcantarillas, donde pisa el pollo.
GROUND_Y = 236
#: Valla que para el tráfico en los carriles cruzados.
BARRIER_Y = 168
MANHOLE_R = 30
#: Caja de los dibujos de alcantarilla (cabe el aro de brillo y la ✓ de encima).
SPRITE_W = 100
SPRITE_H = 112
SPRITE_TOP = 62
#: Hueco de la cámara a la izquierda del pollo.
CAMERA_LEAD = 180

FRAME_MS = 60
FINAL_FRAME_MS = 60_000

# -- Colores --------------------------------------------------------------------------

ASPHALT = (54, 57, 66)
ASPHALT_DARK = (44, 46, 54)
LINE = (236, 236, 228)
SIDEWALK_TOP = (196, 196, 204)
SIDEWALK_JOINT = (168, 168, 178)
CURB = (232, 232, 236)
GRASS = (92, 160, 84)
MANHOLE_RIM = (30, 31, 36)
MANHOLE = (96, 100, 110)
MANHOLE_GRID = (78, 82, 92)
GOLD = (255, 196, 0)
GOLD_DARK = (176, 120, 0)
WHITE = (250, 250, 246)
INK = (28, 26, 30)
TEXT = (240, 242, 246)
MUTED = (170, 174, 184)
HUD_BG = (18, 19, 23)
BEAK = (255, 156, 24)
COMB = (232, 52, 52)
BOOM = (255, 128, 48)
HEADLIGHT = (255, 244, 170)

CAR_COLORS = (
    (220, 60, 60),
    (58, 118, 230),
    (250, 202, 40),
    (236, 236, 240),
    (44, 44, 52),
    (255, 140, 30),
    (150, 92, 220),
    (40, 170, 170),
)

#: Vehículos posibles: (clave, nombre para el texto, largo, ancho).
VEHICLES: tuple[tuple[str, str, int, int], ...] = (
    ("car", "un utilitario", 92, 54),
    ("car", "un SUV de concesionario", 100, 60),
    ("van", "una furgoneta de reparto", 116, 60),
    ("truck", "un camión de mudanzas", 168, 64),
    ("bus", "un autobús de línea", 184, 66),
    ("moto", "un repartidor en moto", 62, 26),
    ("taxi", "un taxi con prisa", 94, 54),
)


def vehicle_for(seed: int, lane: int) -> str:
    """Nombre del vehículo del carril `lane` de una partida (para el texto)."""
    return VEHICLES[_vehicle_index(seed, lane)][1]


def _vehicle_index(seed: int, lane: int) -> int:
    return random.Random(seed * 1_000 + lane).randrange(len(VEHICLES))


def _car_color(seed: int, lane: int) -> tuple[int, int, int]:
    return CAR_COLORS[random.Random(seed * 7_919 + lane).randrange(len(CAR_COLORS))]


def tension_seconds(next_cents: int) -> float:
    """Lo que dura la espera antes de saber si te atropellan.

    Crece con el logaritmo del multiplicador en juego: ×1,1 son ~0,7 s, ×2
    ~1,05 s, ×16 ~2 s y desde ×60 o así se queda en el tope de 2,6 s.
    """
    doublings = math.log2(max(next_cents, 100) / 100)
    return min(2.6, 0.7 + 0.35 * doublings)


@dataclass(frozen=True, slots=True)
class Media:
    """Imágenes de un paso: GIF (vacío si no hay animación), PNG final y duración."""

    gif: bytes
    png: bytes
    seconds: float


# -- Escena -----------------------------------------------------------------------------


@dataclass(slots=True)
class Car:
    """Un vehículo en un carril: dónde va el morro y si frena."""

    lane: int
    y_front: float
    braking: bool = False


@dataclass(slots=True)
class Scene:
    """Todo lo que cambia entre fotogramas.

    Lo leen el dibujo de Pillow (`ChickenRenderer`) y la escena de canvas
    (`bot.services.chicken_scene`): la animación se decide aquí una sola vez.
    """

    difficulty: Difficulty
    seed: int
    #: Carriles con valla bajada (los cruzados).
    fenced: int
    #: Posición del pollo en carriles (0 = acera, puede ser fraccionaria al saltar).
    chicken_at: float
    hop: float = 0.0
    pose: str = "idle"
    shake: float = 0.0
    #: Carril cuya alcantarilla parpadea y cuánto (0-1).
    glow_lane: int | None = None
    glow: float = 0.0
    #: Faros al fondo del carril (0-1).
    headlights: float = 0.0
    cars: list[Car] = field(default_factory=list)
    #: Valla que está cayendo: (carril, progreso 0-1).
    falling_fence: tuple[int, float] | None = None
    skid: int | None = None
    #: Impacto del atropello (0-1) en el carril del pollo.
    impact: float = 0.0
    splat: bool = False
    #: Coche fantasma donde estaba el atropello (al cobrar).
    ghost_lane: int | None = None
    celebrate: float = 0.0
    hud_cents: int = 100
    hud_value: int | None = None
    hud_note: str = ""
    stake: int = 0
    #: Cámara fija (borde izquierdo) en vez de seguir al pollo.
    camera: float | None = None
    #: Rótulo de cómic sobre el atropello («¡PLAF!»).
    plaf: bool = False


def lane_x(lane: float) -> float:
    """Centro horizontal, en el mundo, de la posición `lane` (0 = acera)."""
    if lane <= 0:
        return SIDEWALK / 2 + lane * LANE
    return SIDEWALK + (lane - 0.5) * LANE


def finish_x(difficulty: Difficulty) -> float:
    """Centro de la meta."""
    return SIDEWALK + difficulty.lanes * LANE + FINISH / 2


def world_width(difficulty: Difficulty) -> int:
    return SIDEWALK + difficulty.lanes * LANE + FINISH


def camera_for(difficulty: Difficulty, chicken_x: float) -> float:
    """Borde izquierdo de la cámara: sigue al pollo sin salirse del mundo."""
    return max(0.0, min(chicken_x - CAMERA_LEAD, world_width(difficulty) - W))


# -- Línea de tiempo (la comparten Pillow y la escena de canvas) ---------------------------


def start_scene(difficulty: Difficulty, *, stake: int, seed: int) -> Scene:
    """Antes de jugar: el pollo en la acera y la primera ficha encendida."""
    return Scene(
        difficulty=difficulty,
        seed=seed,
        fenced=0,
        chicken_at=0,
        glow_lane=1,
        glow=0.6,
        hud_cents=100,
        hud_value=None,
        hud_note="Pulsa Cruzar",
        stake=stake,
    )


def resting_scene(game: ChickenGame, *, seed: int, note: str = "") -> Scene:
    """El fotograma quieto de la partida: en juego, atropellada o cobrada."""
    difficulty = game.difficulty
    scene = Scene(
        difficulty=difficulty,
        seed=seed,
        fenced=game.crossed,
        chicken_at=game.crossed,
        hud_cents=game.cents,
        hud_value=game.cashout_value if game.crossed else None,
        hud_note=note,
        stake=game.stake,
    )
    if game.playing:
        if not game.finished_road:
            scene.glow_lane = game.crossed + 1
            scene.glow = 0.7
        return scene
    if game.status is Status.SPLAT:
        scene.chicken_at = game.crossed + 1
        scene.splat = True
        scene.plaf = True
        scene.pose = "splat"
        scene.hud_value = 0
        return scene
    # Cobrado: celebra y enseña dónde estaba el coche.
    scene.pose = "win"
    scene.celebrate = 1.0
    if game.finished_road:
        scene.chicken_at = difficulty.lanes + 1.15
    if game.hit_lane is not None:
        scene.ghost_lane = game.hit_lane
    return scene


def timeline(game: ChickenGame, *, start: int, seed: int) -> list[tuple[Scene, int]]:
    """Los fotogramas (escena y milisegundos) de los carriles cruzados desde `start`.

    Los carriles intermedios (solo en el autocobro) son una carrera
    rápida con la cámara saltando por páginas, para que el GIF pese poco.
    El último carril lleva la tensión entera. Si `game` acabó atropellado,
    el último salto es el del atropello. Detrás va el fotograma quieto de
    `resting_scene(game)`, que no está en la lista.

    Args:
        game: La partida ya avanzada.
        start: Carriles cruzados antes del paso.
        seed: Azar de la partida (vehículos y colores).

    Raises:
        ValueError: Si no hay ningún carril que enseñar.
    """
    difficulty = game.difficulty
    splat = game.status is Status.SPLAT
    lanes = list(range(start + 1, game.crossed + 1))
    if splat:
        lanes.append(game.crossed + 1)
    if not lanes:
        raise ValueError("No hay saltos que dibujar.")
    frames: list[tuple[Scene, int]] = []

    def push(scene: Scene, ms: int) -> None:
        frames.append((scene, ms))

    def cents(lane: int) -> int:
        return multiplier_cents(difficulty, lane)

    auto_note = f"Autocobro {format_multiplier(game.auto_target)}" if game.auto_target else ""

    # Carrera del autocobro: salto y aterrizaje por carril, cámara por páginas.
    camera: float | None = None
    for lane in lanes[:-1]:
        x = lane_x(lane)
        if camera is None or x - camera > W - 150:
            camera = camera_for(difficulty, x)
        hop = Scene(
            difficulty=difficulty,
            seed=seed,
            fenced=lane - 1,
            chicken_at=lane - 0.5,
            hop=1.0,
            pose="jump",
            camera=camera,
            hud_cents=cents(lane - 1),
            hud_note=auto_note,
            stake=game.stake,
        )
        push(hop, 45)
        push(
            replace(hop, chicken_at=lane, hop=0.0, pose="idle", fenced=lane, hud_cents=cents(lane)),
            75,
        )

    # Último carril: tensión, salto y coche.
    lane = lanes[-1]
    target = cents(lane)
    base = Scene(
        difficulty=difficulty,
        seed=seed,
        fenced=lane - 1,
        chicken_at=lane - 1,
        hud_cents=cents(lane - 1),
        hud_note=f"Siguiente {format_multiplier(target)}",
        stake=game.stake,
    )
    steps = max(2, round(tension_seconds(target) * 1000 / 90))
    for i in range(steps):
        progress = (i + 1) / steps
        push(
            replace(
                base,
                pose="crouch" if i % 2 == 0 else "scared",
                shake=(1.0 + 2.5 * progress) * (1 if i % 2 else -1),
                glow_lane=lane,
                glow=0.35 + 0.65 * (i % 2),
                headlights=progress,
            ),
            90,
        )
    for i in range(1, 6):
        t = i / 5
        push(
            replace(
                base,
                chicken_at=lane - 1 + t,
                hop=math.sin(math.pi * t),
                pose="jump",
                glow_lane=lane,
                headlights=1.0,
            ),
            50,
        )
    landed = replace(base, chicken_at=lane, pose="scared")
    # El coche entra igual en los dos casos: no se sabe nada hasta el final.
    for y in (-12.0, 70.0):
        push(replace(landed, cars=[Car(lane, y)]), 50)
    if splat:
        push(replace(landed, cars=[Car(lane, GROUND_Y - 22)]), 45)
        for y, impact, ms in (
            (GROUND_Y + 40, 1.0, 80),
            (GROUND_Y + 130, 0.6, 70),
            (H + 70, 0.3, 80),
        ):
            push(
                replace(landed, cars=[Car(lane, y)], splat=True, impact=impact, hud_value=0),
                ms,
            )
    else:
        stop = BARRIER_Y - 8
        for i, (fall, y) in enumerate(((0.5, 120.0), (1.0, stop + 6), (1.0, stop))):
            push(
                replace(
                    landed,
                    falling_fence=(lane, fall),
                    cars=[Car(lane, y, braking=True)],
                    skid=lane if i else None,
                    hud_cents=target,
                ),
                60,
            )

    return frames


# -- Fuentes ---------------------------------------------------------------------------


@lru_cache(maxsize=16)
def _title(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(TITLE_FONT), size * S)


@lru_cache(maxsize=16)
def _text(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(TEXT_FONT), size * S)


def _xy(*values: float) -> list[float]:
    return [v * S for v in values]


# -- Renderizador ------------------------------------------------------------------------


class ChickenRenderer:
    """Dibuja la carretera del Pollo; seguro para usar desde varios hilos."""

    def __init__(self) -> None:
        self._worlds: dict[str, Image.Image] = {}
        self._sprites: dict[tuple[int, bool, float, bool], Image.Image] = {}
        self._huds: dict[tuple, Image.Image] = {}
        # Reentrante: pintar el mundo (con el cerrojo) pide dibujos de alcantarilla.
        self._lock = threading.RLock()

    # -- API ----------------------------------------------------------------------------

    def warm(self, difficulty: Difficulty) -> None:
        """Pinta y guarda el fondo de una dificultad (para no hacerlo en el primer clic)."""
        self._world(difficulty)

    def board(self, game: ChickenGame, *, seed: int, note: str = "") -> bytes:
        """PNG del estado actual de la partida (o de cómo acabó)."""
        return self._png(self._frame(resting_scene(game, seed=seed, note=note)))

    def start(self, difficulty: Difficulty, *, stake: int, seed: int) -> bytes:
        """PNG antes de jugar: el pollo en la acera."""
        return self._png(self._frame(start_scene(difficulty, stake=stake, seed=seed)))

    def hops(self, game: ChickenGame, *, start: int, seed: int, note: str = "") -> Media:
        """GIF de los carriles cruzados desde `start` hasta el estado de `game` (ver `timeline`).

        El último fotograma es el PNG de `board(game)`.

        Raises:
            ValueError: Si no hay ningún carril que enseñar.
        """
        steps = timeline(game, start=start, seed=seed)
        frames = [self._frame(scene) for scene, _ms in steps]
        frames.append(self._frame(resting_scene(game, seed=seed, note=note)))
        durations = [ms for _scene, ms in steps]
        seconds = sum(durations) / 1000
        durations.append(FINAL_FRAME_MS)
        gif = local_palette_gif(frames, durations)
        return Media(gif=gif, png=self._png(frames[-1]), seconds=seconds)

    # -- Mundo estático ---------------------------------------------------------------------

    def _world(self, difficulty: Difficulty) -> Image.Image:
        """Acera, carriles, alcantarillas con número y meta, al doble de tamaño."""
        with self._lock:
            world = self._worlds.get(difficulty.key)
            if world is None:
                world = self._paint_world(difficulty)
                self._worlds[difficulty.key] = world
            return world

    def _paint_world(self, difficulty: Difficulty) -> Image.Image:
        width = world_width(difficulty)
        image = Image.new("RGB", (width * S, H * S), ASPHALT)
        d = ImageDraw.Draw(image)

        # Acera con baldosas, césped y bordillo.
        d.rectangle(_xy(0, 0, SIDEWALK, H), fill=SIDEWALK_TOP)
        d.rectangle(_xy(0, 0, 22, H), fill=GRASS)
        for y in range(0, H, 34):
            d.line(_xy(22, y, SIDEWALK - 10, y), fill=SIDEWALK_JOINT, width=2 * S)
        for x in (58, 94):
            d.line(_xy(x, 0, x, H), fill=SIDEWALK_JOINT, width=2 * S)
        d.rectangle(_xy(SIDEWALK - 10, 0, SIDEWALK, H), fill=CURB)

        # Carriles: textura suave, línea continua en los bordes y discontinua entre carriles.
        road_x0 = SIDEWALK
        road_x1 = SIDEWALK + difficulty.lanes * LANE
        noise = random.Random(difficulty.key)
        for _ in range(difficulty.lanes * 40):
            x = noise.uniform(road_x0, road_x1)
            y = noise.uniform(0, H)
            r = noise.uniform(1, 3)
            d.ellipse(_xy(x - r, y - r, x + r, y + r), fill=ASPHALT_DARK)
        d.line(_xy(road_x0 + 6, 0, road_x0 + 6, H), fill=LINE, width=3 * S)
        d.line(_xy(road_x1 - 6, 0, road_x1 - 6, H), fill=LINE, width=3 * S)
        for lane in range(1, difficulty.lanes):
            x = SIDEWALK + lane * LANE
            for y in range(-10, H, 44):
                d.line(_xy(x, y, x, y + 24), fill=LINE, width=3 * S)

        # Alcantarillas con el multiplicador de cada carril.
        for lane in range(1, difficulty.lanes + 1):
            self._manhole(image, lane_x(lane), multiplier_cents(difficulty, lane), crossed=False)

        # Meta: bandera a cuadros y zona dorada.
        fx = road_x1
        d.rectangle(_xy(fx, 0, fx + FINISH, H), fill=(60, 52, 30))
        square = 13
        for row in range(0, H // square + 1):
            for col in range(2):
                color = WHITE if (row + col) % 2 == 0 else INK
                d.rectangle(
                    _xy(
                        fx + col * square, row * square, fx + (col + 1) * square, (row + 1) * square
                    ),
                    fill=color,
                )
        cx = fx + FINISH / 2 + 10
        top = multiplier_cents(difficulty, difficulty.lanes)
        d.rounded_rectangle(
            _xy(cx - 62, GROUND_Y - 186, cx + 62, GROUND_Y - 92),
            radius=16 * S,
            fill=GOLD,
            outline=INK,
            width=4 * S,
        )
        d.text(_xy(cx, GROUND_Y - 160), "META", font=_title(26), fill=INK, anchor="mm")
        meta = short_multiplier(top)
        d.text(_xy(cx, GROUND_Y - 120), meta, font=_title(22), fill=INK, anchor="mm")
        return image

    def _manhole(
        self,
        image: Image.Image,
        cx: float,
        cents: int,
        *,
        crossed: bool,
        glow: float = 0,
        label: bool = True,
    ) -> None:
        """Pega la alcantarilla de un carril centrada en `cx` (de un caché de dibujos)."""
        glow = round(glow * 20) / 20
        key = (cents, crossed, glow, label)
        with self._lock:
            sprite = self._sprites.get(key)
        if sprite is None:
            sprite = self._paint_manhole(cents, crossed=crossed, glow=glow, label=label)
            with self._lock:
                # Acotado: unas pocas alcantarillas por dificultad y estado.
                if len(self._sprites) > 1_024:
                    self._sprites.clear()
                self._sprites[key] = sprite
        x0, y0 = cx - SPRITE_W / 2, GROUND_Y - SPRITE_TOP
        image.paste(sprite, (round(x0 * S), round(y0 * S)), sprite)

    @staticmethod
    def _paint_manhole(cents: int, *, crossed: bool, glow: float, label: bool) -> Image.Image:
        """Dibujo de una alcantarilla: tapa, número y, si está cruzada, ✓ y oro."""
        sprite = Image.new("RGBA", (SPRITE_W * S, SPRITE_H * S), (0, 0, 0, 0))
        d = ImageDraw.Draw(sprite)
        r = MANHOLE_R
        cx, cy = SPRITE_W / 2, SPRITE_TOP
        if glow:
            g = r + 6 + 4 * glow
            d.ellipse(
                _xy(cx - g, cy - g, cx + g, cy + g),
                outline=(255, 255, 255, int(120 + 135 * glow)),
                width=4 * S,
            )
        rim = GOLD_DARK if crossed else MANHOLE_RIM
        fill = GOLD if crossed else MANHOLE
        d.ellipse(_xy(cx - r, cy - r, cx + r, cy + r), fill=rim)
        inner = r - 5
        d.ellipse(_xy(cx - inner, cy - inner, cx + inner, cy + inner), fill=fill)
        if not crossed:
            for k in (-12, 0, 12):
                d.line(
                    _xy(cx - inner + 4, cy + k, cx + inner - 4, cy + k),
                    fill=MANHOLE_GRID,
                    width=2 * S,
                )
        if label:
            text = short_multiplier(cents)
            size = 15 if len(text) <= 5 else 13 if len(text) <= 6 else 11
            color = INK if crossed else TEXT
            d.text(
                _xy(cx, cy + 1),
                text,
                font=_title(size),
                fill=color,
                anchor="mm",
                stroke_width=0 if crossed else 2 * S,
                stroke_fill=MANHOLE_RIM,
            )
        if crossed:
            # ✓ dibujada: no depende del color.
            d.line(
                _xy(cx - 8, cy - r - 12, cx - 2, cy - r - 6, cx + 10, cy - r - 20),
                fill=WHITE,
                width=4 * S,
                joint="curve",
            )
        return sprite

    # -- Fotograma -------------------------------------------------------------------------

    def _frame(self, scene: Scene) -> Image.Image:
        difficulty = scene.difficulty
        chicken_x = (
            lane_x(scene.chicken_at)
            if scene.chicken_at <= difficulty.lanes
            else lane_x(difficulty.lanes) + (scene.chicken_at - difficulty.lanes) * LANE
        )
        cam = scene.camera if scene.camera is not None else camera_for(difficulty, chicken_x)
        world = self._world(difficulty)
        frame = world.crop((int(cam * S), 0, int(cam * S) + W * S, H * S))
        # Modo RGBA sobre una imagen RGB: los rellenos con transparencia se mezclan.
        d = ImageDraw.Draw(frame, "RGBA")

        def sx(x: float) -> float:
            return x - cam

        first = max(1, int(cam // LANE) - 1)
        last = min(difficulty.lanes, int((cam + W) // LANE) + 1)
        visible = range(first, last + 1)

        # Carriles cruzados: alcantarilla dorada, valla y coche parado detrás.
        for lane in visible:
            x = sx(lane_x(lane))
            cents = multiplier_cents(difficulty, lane)
            under = abs(scene.chicken_at - lane) < 0.3
            if under and scene.splat:
                # El número no asoma por debajo del pollo aplastado.
                self._manhole(frame, x, cents, crossed=False, label=False)
            elif lane <= scene.fenced:
                self._manhole(frame, x, cents, crossed=True, label=not under)
                self._fence(d, x, 1.0)
                if lane != scene.ghost_lane and random.Random(scene.seed + lane).random() < 0.55:
                    self._vehicle(d, x, BARRIER_Y - 10, scene.seed, lane, braking=True)
            elif lane == scene.glow_lane and scene.glow:
                self._manhole(frame, x, cents, crossed=False, glow=scene.glow)
        if scene.falling_fence is not None:
            lane, progress = scene.falling_fence
            self._manhole(frame, sx(lane_x(lane)), multiplier_cents(difficulty, lane), crossed=True)
            self._fence(d, sx(lane_x(lane)), progress)
        if scene.skid is not None:
            x = sx(lane_x(scene.skid))
            for dx in (-16, 16):
                d.line(
                    _xy(x + dx, BARRIER_Y - 120, x + dx, BARRIER_Y - 30),
                    fill=(24, 24, 28, 200),
                    width=5 * S,
                )

        # Faros de lo que viene por el carril siguiente.
        if scene.headlights and scene.glow_lane is not None:
            x = sx(lane_x(scene.glow_lane))
            h = scene.headlights
            reach = HUD_H + 20 + 110 * h
            d.polygon(
                _xy(x - 30, HUD_H, x + 30, HUD_H, x + 48, reach, x - 48, reach),
                fill=(*HEADLIGHT, int(105 * h)),
            )
            for dx in (-16, 16):
                for r, alpha in ((18, 50), (12, 110), (7, 255)):
                    rr = r * (0.5 + 0.5 * h)
                    d.ellipse(
                        _xy(x + dx - rr, HUD_H + 4 - rr, x + dx + rr, HUD_H + 4 + rr),
                        fill=(*HEADLIGHT, int(alpha * h)),
                    )

        if scene.ghost_lane is not None and scene.ghost_lane in visible:
            x = sx(lane_x(scene.ghost_lane))
            ghost = Image.new("RGBA", frame.size, (0, 0, 0, 0))
            self._vehicle(
                ImageDraw.Draw(ghost), x, GROUND_Y + 40, scene.seed, scene.ghost_lane, braking=True
            )
            alpha = ghost.getchannel("A").point(lambda v: v * 45 // 100)
            ghost.putalpha(alpha)
            frame.paste(ghost, (0, 0), ghost)
            d.rounded_rectangle(
                _xy(x - 46, 60, x + 46, 88),
                radius=8 * S,
                fill=(*BOOM, 235),
                outline=INK,
                width=2 * S,
            )
            d.text(_xy(x, 74), "¡AQUÍ!", font=_title(15), fill=INK, anchor="mm")

        # Pollo (detrás del coche si le atropellan, para que el coche le pase por encima).
        cx = sx(chicken_x) + scene.shake
        cy = GROUND_Y + 6 - scene.hop * 46
        if scene.splat:
            # Marcas de rueda por todo el carril y por encima del pollo aplastado.
            for dx in (-16, 16):
                d.line(_xy(cx + dx, GROUND_Y - 50, cx + dx, H), fill=(20, 20, 24, 150), width=6 * S)
            self._chicken(d, cx, GROUND_Y + 6, "splat")
            for dx in (-16, 16):
                d.line(
                    _xy(cx + dx, GROUND_Y - 14, cx + dx, GROUND_Y + 12),
                    fill=(20, 20, 24, 170),
                    width=6 * S,
                )
            if not scene.impact:
                self._feathers(d, cx, GROUND_Y - 10, 0.9, scene.seed)
        for car in scene.cars:
            self._vehicle(
                d, sx(lane_x(car.lane)), car.y_front, scene.seed, car.lane, braking=car.braking
            )
        if not scene.splat:
            self._chicken(d, cx, cy, scene.pose, hop=scene.hop)
        if scene.impact:
            self._burst(d, cx, GROUND_Y - 24, 26 + 30 * scene.impact)
            self._feathers(d, cx, GROUND_Y - 20, 1.2 - scene.impact, scene.seed)
        if scene.celebrate:
            self._sparkles(d, cx, cy - 40, scene.seed)
        if scene.plaf:
            self._bubble(d, cx + 30, GROUND_Y - 96, "¡PLAF!")

        self._hud(frame, scene)
        return frame.reduce(S)

    # -- Piezas ---------------------------------------------------------------------------

    def _fence(self, d: ImageDraw.ImageDraw, cx: float, progress: float) -> None:
        """Valla de obra con rayas que corta el carril; `progress` = cuánto ha caído."""
        half = LANE / 2 - 8
        y = BARRIER_Y - (1 - progress) * 60
        alpha = int(255 * min(1.0, 0.3 + progress))
        for px in (cx - half + 4, cx + half - 4):
            d.rectangle(_xy(px - 3, y - 4, px + 3, y + 16), fill=(70, 70, 76, alpha))
        d.rounded_rectangle(
            _xy(cx - half, y - 10, cx + half, y + 4),
            radius=3 * S,
            fill=(245, 245, 245, alpha),
            outline=(*INK, alpha),
            width=2 * S,
        )
        stripe = 14
        x = cx - half + 4
        while x < cx + half - 4:
            x2 = min(x + stripe / 2, cx + half - 4)
            d.polygon(
                _xy(x, y + 3, x + 6, y - 9, x2 + 6, y - 9, x2, y + 3), fill=(214, 40, 40, alpha)
            )
            x += stripe

    def _vehicle(
        self,
        d: ImageDraw.ImageDraw,
        cx: float,
        y_front: float,
        seed: int,
        lane: int,
        *,
        braking: bool = False,
    ) -> None:
        """Vehículo visto desde arriba, con el morro hacia abajo (baja por el carril)."""
        kind, _name, length, width = VEHICLES[_vehicle_index(seed, lane)]
        color = _car_color(seed, lane)
        if kind == "taxi":
            color = (250, 250, 250)
        x0, x1 = cx - width / 2, cx + width / 2
        y0, y1 = y_front - length, y_front
        darker = tuple(max(0, c - 50) for c in color)
        # Sombra.
        d.rounded_rectangle(_xy(x0 + 4, y0 + 6, x1 + 4, y1 + 6), radius=12 * S, fill=(0, 0, 0, 90))
        if kind == "moto":
            d.rounded_rectangle(_xy(cx - 5, y0, cx + 5, y1), radius=5 * S, fill=INK)
            d.rounded_rectangle(
                _xy(x0, y0 + 14, x1, y1 - 12), radius=10 * S, fill=color, outline=INK, width=2 * S
            )
            d.rectangle(_xy(x0 - 10, y1 - 22, x1 + 10, y1 - 18), fill=INK)
            d.ellipse(
                _xy(cx - 11, y0 + 22, cx + 11, y0 + 44),
                fill=(250, 200, 40),
                outline=INK,
                width=2 * S,
            )
            d.rectangle(
                _xy(cx - 14, y0 + 2, cx + 14, y0 + 20),
                fill=(40, 170, 120),
                outline=INK,
                width=2 * S,
            )
            self._lights(d, cx, y1, 6, braking, single=True)
            return
        # Ruedas.
        for wy in (y0 + 18, y1 - 30):
            d.rounded_rectangle(_xy(x0 - 4, wy, x0 + 6, wy + 20), radius=3 * S, fill=INK)
            d.rounded_rectangle(_xy(x1 - 6, wy, x1 + 4, wy + 20), radius=3 * S, fill=INK)
        if kind == "truck":
            cab = 48
            d.rounded_rectangle(
                _xy(x0, y0, x1, y1 - cab - 4),
                radius=6 * S,
                fill=(232, 232, 236),
                outline=INK,
                width=3 * S,
            )
            for k in range(1, 5):
                yy = y0 + k * (length - cab) / 5
                d.line(_xy(x0 + 6, yy, x1 - 6, yy), fill=(200, 200, 206), width=2 * S)
            d.rounded_rectangle(
                _xy(x0, y1 - cab, x1, y1), radius=10 * S, fill=color, outline=INK, width=3 * S
            )
            d.rounded_rectangle(
                _xy(x0 + 6, y1 - 20, x1 - 6, y1 - 8), radius=4 * S, fill=(120, 190, 240)
            )
            self._lights(d, cx, y1, width / 2 - 8, braking)
            return
        radius = 8 if kind in ("van", "bus") else 16
        d.rounded_rectangle(
            _xy(x0, y0, x1, y1), radius=radius * S, fill=color, outline=INK, width=3 * S
        )
        glass = (120, 190, 240)
        if kind == "bus":
            d.rectangle(_xy(x0 + 8, y0 + 10, x1 - 8, y1 - 34), fill=darker)
            for k in range(3):
                yy = y0 + 22 + k * 40
                d.rectangle(
                    _xy(cx - 14, yy, cx + 14, yy + 22),
                    fill=(210, 210, 216),
                    outline=INK,
                    width=2 * S,
                )
            d.rounded_rectangle(_xy(x0 + 5, y1 - 26, x1 - 5, y1 - 8), radius=4 * S, fill=glass)
        elif kind == "van":
            d.rectangle(_xy(x0 + 6, y0 + 8, x1 - 6, y1 - 38), fill=darker)
            d.polygon(
                _xy(x0 + 6, y1 - 34, x1 - 6, y1 - 34, x1 - 10, y1 - 14, x0 + 10, y1 - 14),
                fill=glass,
            )
        else:
            d.polygon(
                _xy(x0 + 8, y0 + 12, x1 - 8, y0 + 12, x1 - 4, y0 + 22, x0 + 4, y0 + 22), fill=glass
            )
            d.rounded_rectangle(_xy(x0 + 7, y0 + 26, x1 - 7, y1 - 38), radius=8 * S, fill=darker)
            d.polygon(
                _xy(x0 + 4, y1 - 36, x1 - 4, y1 - 36, x1 - 10, y1 - 18, x0 + 10, y1 - 18),
                fill=glass,
            )
            if kind == "taxi":
                d.rounded_rectangle(
                    _xy(cx - 14, y0 + 40, cx + 14, y0 + 52),
                    radius=3 * S,
                    fill=(250, 202, 40),
                    outline=INK,
                    width=2 * S,
                )
                d.text(_xy(cx, y0 + 46), "TAXI", font=_text(7), fill=INK, anchor="mm")
        self._lights(d, cx, y1, width / 2 - 8, braking)

    def _lights(
        self,
        d: ImageDraw.ImageDraw,
        cx: float,
        y_front: float,
        spread: float,
        braking: bool,
        *,
        single: bool = False,
    ) -> None:
        offsets = (0,) if single else (-spread, spread)
        for dx in offsets:
            d.ellipse(
                _xy(cx + dx - 6, y_front - 7, cx + dx + 6, y_front + 3),
                fill=HEADLIGHT,
                outline=INK,
                width=S,
            )
        if not braking:
            # Haces de luz hacia delante.
            for dx in offsets:
                d.polygon(
                    _xy(
                        cx + dx - 6,
                        y_front,
                        cx + dx + 6,
                        y_front,
                        cx + dx + 16,
                        y_front + 46,
                        cx + dx - 16,
                        y_front + 46,
                    ),
                    fill=(*HEADLIGHT, 70),
                )

    def _chicken(
        self, d: ImageDraw.ImageDraw, cx: float, feet: float, pose: str, *, hop: float = 0.0
    ) -> None:
        """El pollo, de perfil mirando a la derecha. `feet` es la altura de las patas."""
        o = 3  # grosor del contorno
        if pose == "splat":
            d.ellipse(_xy(cx - 50, feet - 12, cx + 50, feet + 10), fill=(0, 0, 0, 90))
            d.ellipse(
                _xy(cx - 46, feet - 20, cx + 44, feet + 4), fill=WHITE, outline=INK, width=o * S
            )
            for dx, dy in ((-30, -14), (24, -16), (-8, -18)):
                d.ellipse(
                    _xy(cx + dx - 7, feet + dy - 5, cx + dx + 7, feet + dy + 3),
                    fill=WHITE,
                    outline=INK,
                    width=2 * S,
                )
            # Patas tiesas.
            for dx in (-14, 4):
                d.line(_xy(cx + dx, feet - 6, cx + dx - 8, feet + 18), fill=BEAK, width=4 * S)
            # Cresta, pico y ojos en X.
            d.ellipse(_xy(cx + 24, feet - 22, cx + 36, feet - 10), fill=COMB, outline=INK, width=S)
            d.polygon(
                _xy(cx + 38, feet - 10, cx + 52, feet - 6, cx + 38, feet - 2),
                fill=BEAK,
                outline=INK,
            )
            for ex in (cx + 18, cx + 30):
                d.line(_xy(ex - 4, feet - 12, ex + 4, feet - 4), fill=INK, width=2 * S)
                d.line(_xy(ex - 4, feet - 4, ex + 4, feet - 12), fill=INK, width=2 * S)
            return

        squash = {"crouch": 0.86, "scared": 0.92}.get(pose, 1.0)
        stretch = 1.0 + 0.08 * hop

        def y(v: float) -> float:
            return feet - v * squash * stretch

        # Sombra (se aleja al saltar).
        shadow = 22 - 8 * hop
        d.ellipse(_xy(cx - shadow, GROUND_Y + 2, cx + shadow, GROUND_Y + 12), fill=(0, 0, 0, 80))
        # Patas.
        if pose == "jump":
            d.line(_xy(cx - 8, y(14), cx - 16, y(2)), fill=BEAK, width=4 * S)
            d.line(_xy(cx + 4, y(14), cx - 2, y(2)), fill=BEAK, width=4 * S)
        else:
            for lx in (cx - 8, cx + 4):
                d.line(_xy(lx, y(16), lx, feet - 2), fill=BEAK, width=4 * S)
                d.line(_xy(lx - 6, feet, lx + 7, feet), fill=BEAK, width=4 * S)
        # Cola.
        for k, (tx, ty) in enumerate(((-30, 42), (-34, 34), (-32, 26))):
            d.ellipse(
                _xy(cx + tx - 7, y(ty) - 6, cx + tx + 9, y(ty) + 6),
                fill=WHITE,
                outline=INK,
                width=2 * S,
            )
            del k
        # Cuerpo.
        d.ellipse(_xy(cx - 28, y(52), cx + 22, y(12)), fill=WHITE, outline=INK, width=o * S)
        # Ala.
        if pose in ("jump", "win"):
            d.polygon(
                _xy(cx - 12, y(38), cx - 30, y(64), cx - 2, y(50), cx + 6, y(36)),
                fill=(236, 236, 230),
                outline=INK,
            )
            if pose == "win":
                d.polygon(
                    _xy(cx + 4, y(40), cx + 18, y(70), cx + 22, y(44)),
                    fill=(236, 236, 230),
                    outline=INK,
                )
        else:
            d.ellipse(
                _xy(cx - 16, y(40), cx + 8, y(24)), fill=(232, 232, 226), outline=INK, width=2 * S
            )
        # Cabeza.
        hx, hy, hr = cx + 14, y(58), 15
        for k, dx in enumerate((-8, 0, 8)):
            r = 6 if k != 1 else 7
            d.ellipse(
                _xy(hx + dx - r, hy - hr - r - 2, hx + dx + r, hy - hr + r - 2),
                fill=COMB,
                outline=INK,
                width=2 * S,
            )
        d.ellipse(_xy(hx - hr, hy - hr, hx + hr, hy + hr), fill=WHITE, outline=INK, width=o * S)
        d.polygon(_xy(hx + 12, hy - 4, hx + 28, hy + 1, hx + 12, hy + 6), fill=BEAK, outline=INK)
        d.ellipse(_xy(hx + 6, hy + 5, hx + 14, hy + 16), fill=COMB, outline=INK, width=S)
        # Ojo: más grande con miedo.
        er = 4.5 if pose in ("crouch", "scared") else 3.5
        d.ellipse(_xy(hx + 4 - er, hy - 6 - er, hx + 4 + er, hy - 6 + er), fill=INK)
        d.ellipse(_xy(hx + 4, hy - 8, hx + 6, hy - 6), fill=WHITE)
        if pose in ("crouch", "scared"):
            d.line(_xy(hx - 2, hy - 15, hx + 9, hy - 12), fill=INK, width=2 * S)
            # Gota de sudor.
            d.ellipse(
                _xy(hx - 16, hy - 12, hx - 9, hy - 3), fill=(120, 190, 240), outline=INK, width=S
            )

    def _burst(self, d: ImageDraw.ImageDraw, cx: float, cy: float, radius: float) -> None:
        """Estrella de 10 picos del atropello."""
        points = []
        for i in range(20):
            r = radius if i % 2 == 0 else radius * 0.45
            angle = math.pi * i / 10 - math.pi / 2
            points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
        d.polygon(_xy(*[v for p in points for v in p]), fill=BOOM, outline=INK)
        r = radius * 0.3
        d.ellipse(_xy(cx - r, cy - r, cx + r, cy + r), fill=(255, 230, 160))

    def _feathers(
        self, d: ImageDraw.ImageDraw, cx: float, cy: float, spread: float, seed: int
    ) -> None:
        rng = random.Random(seed)
        for _ in range(9):
            angle = rng.uniform(0, 2 * math.pi)
            dist = (24 + rng.uniform(0, 30)) * (0.6 + spread)
            x = cx + math.cos(angle) * dist
            y = cy + math.sin(angle) * dist * 0.7
            d.ellipse(_xy(x - 6, y - 3, x + 6, y + 3), fill=WHITE, outline=INK, width=S)

    def _bubble(self, d: ImageDraw.ImageDraw, cx: float, cy: float, text: str) -> None:
        """Bocadillo de cómic con picos, como las onomatopeyas de los tebeos."""
        points = []
        for i in range(24):
            r = 58 if i % 2 == 0 else 42
            angle = math.pi * i / 12
            points += [cx + r * math.cos(angle), cy + r * 0.6 * math.sin(angle)]
        d.polygon(_xy(*points), fill=WHITE, outline=INK, width=3 * S)
        d.text(
            _xy(cx, cy),
            text,
            font=_title(20),
            fill=BOOM,
            anchor="mm",
            stroke_width=S,
            stroke_fill=INK,
        )

    def _sparkles(self, d: ImageDraw.ImageDraw, cx: float, cy: float, seed: int) -> None:
        rng = random.Random(seed + 5)
        for _ in range(6):
            x = cx + rng.uniform(-50, 50)
            y = cy + rng.uniform(-40, 10)
            r = rng.uniform(5, 9)
            d.polygon(
                _xy(
                    x,
                    y - r,
                    x + r / 3,
                    y - r / 3,
                    x + r,
                    y,
                    x + r / 3,
                    y + r / 3,
                    x,
                    y + r,
                    x - r / 3,
                    y + r / 3,
                    x - r,
                    y,
                    x - r / 3,
                    y - r / 3,
                ),
                fill=GOLD,
                outline=INK,
            )

    def _hud(self, frame: Image.Image, scene: Scene) -> None:
        """Pega el marcador de arriba (de un caché: las letras son lo más lento)."""
        key = (
            scene.difficulty.key,
            scene.splat,
            scene.hud_cents,
            scene.hud_value,
            scene.hud_note,
            bool(scene.celebrate),
        )
        with self._lock:
            hud = self._huds.get(key)
        if hud is None:
            hud = Image.new("RGB", (W * S, HUD_H * S), HUD_BG)
            self._paint_hud(ImageDraw.Draw(hud), scene)
            with self._lock:
                if len(self._huds) > 512:
                    self._huds.clear()
                self._huds[key] = hud
        frame.paste(hud, (0, 0))

    def _paint_hud(self, d: ImageDraw.ImageDraw, scene: Scene) -> None:
        difficulty = scene.difficulty
        d.rectangle(_xy(0, 0, W, HUD_H), fill=HUD_BG)
        d.text(_xy(14, 9), f"POLLO · {difficulty.name.upper()}", font=_title(18), fill=GOLD)
        d.text(_xy(14, 31), difficulty.road, font=_text(11), fill=MUTED)
        right = W - 14
        if scene.splat:
            d.text(
                _xy(right, 25),
                "¡ATROPELLADO!",
                font=_title(26),
                fill=BOOM,
                anchor="rm",
                stroke_width=S,
                stroke_fill=INK,
            )
            return
        big = format_multiplier(scene.hud_cents)
        d.text(_xy(right, 18), big, font=_title(26), fill=WHITE, anchor="rm")
        sub = scene.hud_note
        if scene.hud_value is not None:
            amount = f"{scene.hud_value:,}".replace(",", ".") + " Y$"
            sub = f"{amount} · {sub}" if sub else amount
        if sub:
            d.text(
                _xy(right, 40),
                sub,
                font=_text(11),
                fill=GOLD if scene.celebrate else MUTED,
                anchor="rm",
            )

    # -- Codificación ------------------------------------------------------------------------

    @staticmethod
    def _png(image: Image.Image) -> bytes:
        buffer = io.BytesIO()
        image.quantize(colors=128, method=Image.Quantize.FASTOCTREE).save(
            buffer, format="PNG", optimize=True
        )
        return buffer.getvalue()
