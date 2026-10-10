"""La ruleta con canvas: en Node, si no en Chromium y, si no, con Pillow.

La escena (`assets/ruleta/escena.html`) pinta la rueda en perspectiva, la bola
con su sombra, los rayos, el marcador de la derecha y los carteles. Python
decide todo lo que se ve (`meta_state`, `board_state` y `spin_states`): la
escena solo pinta. Así la tirada ya está resuelta cuando empieza a dibujarse
y una prueba puede comprobar dónde cae la bola sin abrir un navegador.

**Los trucos de la ruleta de verdad**, todos en el dibujo (el número ya está
decidido y cobrado cuando se pinta):

- *El suspense.* La bola corre por la pista en sentido contrario a la rueda,
  frena, choca con un rombo, cae, salta de casilla en casilla y se para.
  Los últimos saltos van más lentos (fotogramas más largos), como la cámara
  lenta de las mesas en directo. La rueda frena hasta dejar el número
  ganador delante, abajo, donde se ve más grande.
- *Los rayos* (`roulette.Wheel.strike`). Con la bola ya en la pista caen uno
  a uno sobre sus números y el multiplicador se queda pegado a la casilla.
- *El casi* (`RoundOutcome.near_miss`). Si un pleno perdido está a una o dos
  casillas, la bola cae primero en él y se sale en el último salto.
- *Lo que se recupera.* Si se acierta algo pero se pierde dinero en total,
  el cartel celebra lo recuperado en verde («¡RECUPERAS 200 Y$!»), igual que
  las tragaperras celebran las «pérdidas disfrazadas de premio» (Dixon et al.,
  Addiction, 2010). La pérdida va debajo, en pequeño.
- *El marcador.* Los últimos números, los calientes y los fríos del servidor
  (`roulette.hot_cold`), como las pantallas de las mesas de casino.

La misma escena se pinta de tres maneras, de mejor a peor:

1. **Node con Skia** (`bot.services.node_scene`): sin navegador y con los
   fotogramas en RGBA crudo, sin pasar por PNG.
2. **Chromium** (`bot.services.browser_scene`), si no hay Node o falla; cada
   recuadro se comprime en PNG y viaja a Python.
3. **Pillow** (`bot.services.roulette_render`, la rueda de antes, sin marcador
   ni rayos), si tampoco hay navegador.

El primer fallo de cada uno se avisa en el log y desde entonces se usa el
siguiente. El juego nunca se queda sin imagen.

Coste de una tirada: ~70 fotogramas, pintados y montados en el GIF en un hilo.
El GIF pesa ~1,5-2 MB. Node y Chromium son propios de la ruleta, como los de la
moneda y los dados, y se cierran tras 10 minutos sin uso.
"""

from __future__ import annotations

import asyncio
import io
import math
import random
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from bot.services import browser_scene
from bot.services.browser_scene import BrowserScene
from bot.services.node_scene import NodeScene, patch_png
from bot.services.roulette import (
    WHEEL_ORDER,
    RoundOutcome,
    Wager,
    color,
    hot_cold,
    label,
)
from bot.services.roulette_render import SPIN_SECONDS, WheelRenderer
from bot.utils.gif import local_palette_gif

SCENE = Path(__file__).resolve().parent.parent / "assets" / "ruleta" / "escena.html"
#: Tamaño de la imagen, el mismo que la moneda y los dados.
W, H = 640, 360

FRAME_MS = 40
#: La pista va a 20 fotogramas por segundo: la bola corre tanto que no se nota y
#: cada fotograma de menos son ~45 ms menos de dibujo y de GIF.
TRACK_FRAME_MS = 50
#: Los saltos de la bola van a cámara lenta: fotogramas más largos, no más fotogramas.
HOP_FRAME_MS = 70
SETTLE_FRAME_MS = 90
#: El último fotograma se queda quieto un minuto por si el cliente repite el GIF.
FINAL_FRAME_MS = 60_000

STEP = 360 / len(WHEEL_ORDER)

# Fases del giro, en fotogramas.
TRACK_FRAMES = 32  # la bola en la pista; los rayos caen en esta fase
DROP_FRAMES = 8  # de la pista a la primera casilla, con el golpe en un rombo
HOP_FRAMES = 6  # cada salto entre casillas
SETTLE_FRAMES = 5  # botes pequeños en la casilla final
REVEAL_FRAMES = 10  # el cartel aparece

#: Fotograma en que cae cada rayo y cuánto dura su destello.
STRIKE_FIRST, STRIKE_EVERY, STRIKE_FLASH = 4, 4, 3
#: Fotogramas que dura el «NO VA MÁS» del principio.
CALLOUT_FRAMES = 12

# Radios en fracción del radio de la rueda: la escena los pasa a píxeles.
R_TRACK = 0.94  # la pista de madera por la que corre la bola
R_POCKET = 0.62  # el fondo de las casillas
#: Vueltas de la bola (en la pantalla) y de la rueda durante el giro.
BALL_LAPS = 3.0
WHEEL_TURN = 300.0

#: Bets que se enseñan en el marcador; el resto se resume en «y N más».
PANEL_BETS = 5
HISTORY_SHOWN = 12

# Sin emojis: el Chromium del Docker no trae su fuente.
_EMOJI = re.compile(r"[\U0001F000-\U0001FFFF☀-➿️]")


def amount(value: int) -> str:
    """Cantidad como en el resto del bot: `1.200 Y$`."""
    return f"{value:,}".replace(",", ".") + " Y$"


def plain(text: str) -> str:
    """Texto sin emojis, para la escena."""
    return _EMOJI.sub("", text).strip()


def _pocket(pocket: int) -> dict[str, str]:
    return {"l": label(pocket), "c": color(pocket)}


@dataclass(frozen=True, slots=True)
class Panel:
    """Lo que enseña el marcador de la derecha.

    Attributes:
        history: Números del servidor, el más reciente primero (hasta 100).
        wagers: Fichas de la tirada (o las de la última, en la mesa quieta).
        returns: Lo devuelto por cada apuesta, si ya se sabe; marca cuál ha entrado.
        lucky: Rayos ya caídos, `{casilla: multiplicador}`.
    """

    history: Sequence[int] = ()
    wagers: Sequence[Wager] = ()
    returns: Sequence[int] | None = None
    lucky: dict[int, int] = field(default_factory=dict)

    def state(self) -> dict[str, Any]:
        """Diccionario que lee la escena (ver la cabecera de `escena.html`)."""
        hot, cold = hot_cold(list(self.history))
        bets = []
        for index, wager in enumerate(self.wagers[:PANEL_BETS]):
            result = ""
            if self.returns is not None:
                result = "win" if self.returns[index] else "lose"
            bets.append({"t": plain(wager.bet.name), "s": amount(wager.stake), "w": result})
        return {
            "history": [_pocket(p) for p in list(self.history)[:HISTORY_SHOWN]],
            "hot": [{**_pocket(p), "n": f"{n}x"} for p, n in hot],
            "cold": [{**_pocket(p), "n": str(n)} for p, n in cold],
            "bets": bets,
            "more": max(0, len(self.wagers) - PANEL_BETS),
            "total": amount(sum(w.stake for w in self.wagers)) if self.wagers else "",
            "lucky": [{**_pocket(p), "m": m} for p, m in self.lucky.items()],
        }


def meta_state() -> dict[str, Any]:
    """Lo fijo: las casillas de la rueda en su orden, en sentido horario."""
    return {"pockets": [_pocket(p) for p in WHEEL_ORDER], "w": W, "h": H}


def result_banner(outcome: RoundOutcome, rng: random.Random | None = None) -> dict[str, str]:
    """Cartel del resultado: `kind` (su estilo), `title` y `sub`.

    Estilos: `lucky` (pleno con rayo), `big` (premio de 8 a 1 o más), `win`,
    `ldw` (acierta algo y pierde: se celebra lo recuperado), `push` (se queda
    igual), `near` (casi: un pleno al lado o un rayo en su número) y `lose`.
    """
    rng = rng or random.Random()
    if outcome.lucky_hit:
        return {
            "kind": "lucky",
            "title": f"¡RAYO x{outcome.lucky_hit}!",
            "sub": f"+{amount(outcome.net)}",
        }
    if outcome.won:
        if outcome.max_payout >= 8:
            title = "¡PLENAZO!" if outcome.max_payout >= 29 else "¡PREMIAZO!"
            return {"kind": "big", "title": title, "sub": f"+{amount(outcome.net)}"}
        title = rng.choice(("¡GANAS!", "¡DENTRO!", "¡COBRAS!", "¡TOMA YA!"))
        return {"kind": "win", "title": title, "sub": f"+{amount(outcome.net)}"}
    if outcome.total_return and outcome.net < 0:
        return {
            "kind": "ldw",
            "title": f"¡RECUPERAS {amount(outcome.total_return)}!",
            "sub": f"de {amount(outcome.stake)} en la mesa",
        }
    if outcome.total_return:
        return {"kind": "push", "title": "TE QUEDAS IGUAL", "sub": "Ni pierdes ni ganas"}
    if outcome.near_miss is not None:
        return {
            "kind": "near",
            "title": "¡POR UNA CASILLA!",
            "sub": f"Tu {label(outcome.near_miss)} estaba ahí al lado",
        }
    covered = [
        n for w in outcome.wagers if w.bet.straight for n in w.bet.numbers if n in outcome.lucky
    ]
    if covered:
        number = covered[0]
        return {
            "kind": "near",
            "title": "¡TENÍAS RAYO!",
            "sub": f"x{outcome.lucky[number]} en tu {label(number)}",
        }
    return {
        "kind": "lose",
        "title": rng.choice(("LA PRÓXIMA ES LA BUENA", "LA BOLA NO QUISO", "UF, POR POCO")),
        "sub": f"-{amount(outcome.stake)}",
    }


# -- El movimiento --------------------------------------------------------------------------


def _ease_out(t: float, power: float = 2.2) -> float:
    return 1 - (1 - min(max(t, 0.0), 1.0)) ** power


def landings(outcome: RoundOutcome, rng: random.Random) -> list[int]:
    """Índices de la rueda donde cae la bola, el último es el ganador.

    Con un casi, la penúltima casilla es la del pleno perdido: la bola cae en
    él y se sale. Sin casi, dos o tres casillas al azar a un lado del ganador.
    Los índices pueden pasar de 0-37: es la misma casilla dando la vuelta.
    """
    final = WHEEL_ORDER.index(outcome.pocket)
    if outcome.near_miss is not None:
        tease = WHEEL_ORDER.index(outcome.near_miss)
        # El camino más corto del casi al ganador, con un índice sin dar la vuelta.
        diff = (tease - final + 19) % 38 - 19
        tease = final + diff
        side = 1 if diff > 0 else -1
        return [tease + side, tease, final]
    side = rng.choice((-1, 1))
    first = final + side * rng.randint(2, 4)
    return [first, final + side, final] if rng.random() < 0.6 else [first, final]


def spin_frames(outcome: RoundOutcome, rng: random.Random) -> list[dict[str, Any]]:
    """Rueda, bola y duración de cada fotograma del giro (sin marcador ni carteles).

    Ángulos en grados, en sentido horario desde arriba, vistos desde arriba
    (la escena aplica la perspectiva). La casilla `i` está en `wheel + i * STEP`.
    La bola: ángulo `a`, radio `r` (fracción del radio de la rueda) y altura
    `z` en píxeles sobre el plato.
    """
    stops = landings(outcome, rng)
    final_index = stops[-1]
    # La rueda acaba con el ganador abajo (180°) y deja de girar cuando la bola cae.
    wheel_end = 180 - final_index * STEP
    land = TRACK_FRAMES + DROP_FRAMES
    frames: list[dict[str, Any]] = []

    def wheel_at(f: int) -> float:
        return wheel_end - WHEEL_TURN * (1 - _ease_out(f / land, 1.6))

    # Pista y caída: la bola va al revés que la rueda y frena hasta su primera casilla.
    first_angle = wheel_end + stops[0] * STEP
    start = first_angle + BALL_LAPS * 360
    for f in range(land + 1):
        t = f / land
        angle = first_angle + (start - first_angle) * (1 - _ease_out(t, 2.0))
        drop = max(0.0, (f - TRACK_FRAMES) / DROP_FRAMES)
        radius = R_TRACK - (R_TRACK - R_POCKET) * _ease_out(drop, 1.5)
        z = 0.0
        if 0 < drop < 1:
            # El golpe contra un rombo a mitad de la caída.
            z = 9 * math.sin(math.pi * drop) * (1.2 - drop)
        ms = TRACK_FRAME_MS if f < TRACK_FRAMES else FRAME_MS
        frames.append({"wheel": wheel_at(f), "ball": {"a": angle, "r": radius, "z": z}, "ms": ms})
    # Saltos de casilla en casilla, a cámara lenta y cada vez más bajos.
    for hop, (a, b) in enumerate(zip(stops, stops[1:], strict=False)):
        height = 14 / (1 + hop) + 2 * abs(b - a)
        for f in range(1, HOP_FRAMES + 1):
            t = f / HOP_FRAMES
            index = a + (b - a) * (t * t * (3 - 2 * t))
            frames.append(
                {
                    "wheel": wheel_end,
                    "ball": {
                        "a": wheel_end + index * STEP,
                        "r": R_POCKET + 0.07 * math.sin(math.pi * t),
                        "z": height * math.sin(math.pi * t),
                    },
                    "ms": HOP_FRAME_MS,
                }
            )
    rest = wheel_end + final_index * STEP
    for f in range(1, SETTLE_FRAMES + 1):
        z = 3.5 * abs(math.sin(f * math.pi / 2.5)) * (1 - f / SETTLE_FRAMES)
        frames.append(
            {"wheel": wheel_end, "ball": {"a": rest, "r": R_POCKET, "z": z}, "ms": SETTLE_FRAME_MS}
        )
    return frames


def spin_states(
    outcome: RoundOutcome, *, history: Sequence[int], seed: int
) -> list[dict[str, Any]]:
    """Todos los fotogramas de una tirada, para la escena.

    Args:
        outcome: La tirada ya resuelta.
        history: Números del servidor antes de esta tirada, el más reciente primero.
        seed: Semilla del camino de la bola, el cartel y las chispas.

    Cada estado lleva `wheel`, `ball`, `ms` y además:

    - `lucky`: rayos ya caídos, `[{i, m, a, bolt}]`: índice en la rueda,
      multiplicador, opacidad del cartelito y destello del rayo (0-1).
    - `callout`: `{t, a}`, el «NO VA MÁS» del crupier.
    - `glow`: `[{i, kind, a}]`, casillas resaltadas (`win` o `near`).
    - `banner`, `bannerA` y `fx`: el cartel, su opacidad y las chispas
      (`{kind, t}`: `coins` o `bolts`, con `t` de 0 a 1).
    - `panel`: el marcador (`Panel.state`).
    - `full`: si ha cambiado algo fuera de la rueda (el marcador).
    """
    rng = random.Random(seed)
    frames = spin_frames(outcome, rng)
    banner = result_banner(outcome, rng)
    order = list(outcome.lucky.items())
    final_index = WHEEL_ORDER.index(outcome.pocket)
    after = Panel(
        history=[outcome.pocket, *history],
        wagers=outcome.wagers,
        returns=outcome.returns,
        lucky=dict(outcome.lucky),
    )
    near = outcome.near_miss
    near_index = WHEEL_ORDER.index(near) if near is not None else None

    spinning_panel = Panel(history=history, wagers=outcome.wagers).state()
    states: list[dict[str, Any]] = []
    previous_panel: dict[str, Any] | None = None
    for f, frame in enumerate(frames):
        lucky = []
        for n, (pocket, mult) in enumerate(order):
            at = STRIKE_FIRST + n * STRIKE_EVERY
            if f >= at:
                since = f - at
                bolt = max(0.0, 1 - since / STRIKE_FLASH)
                lucky.append(
                    {
                        "i": WHEEL_ORDER.index(pocket),
                        "m": mult,
                        "a": min(1.0, (since + 1) / 3),
                        "bolt": bolt,
                    }
                )
        # Los rayos salen en las placas de la rueda y no en el marcador hasta el
        # final: cambiar el marcador obliga a devolver el fotograma entero.
        panel = spinning_panel
        state = {
            **frame,
            "lucky": lucky,
            "callout": {"t": "NO VA MÁS", "a": max(0.0, 1 - f / CALLOUT_FRAMES)}
            if f < CALLOUT_FRAMES
            else None,
            "glow": [],
            "banner": None,
            "bannerA": 0.0,
            "fx": None,
            "panel": panel,
        }
        state["full"] = panel != previous_panel
        previous_panel = panel
        states.append(state)

    final_panel = after.state()
    last = states[-1]
    fx_kind = {"lucky": "bolts", "big": "coins", "win": "coins", "ldw": "coins"}.get(banner["kind"])
    for f in range(1, REVEAL_FRAMES + 1):
        t = f / REVEAL_FRAMES
        glow = [{"i": final_index, "kind": "win", "a": min(1.0, t * 2)}]
        if near_index is not None:
            glow.append({"i": near_index, "kind": "near", "a": min(1.0, t * 2)})
        states.append(
            {
                **last,
                "ms": FRAME_MS * 2 if f < REVEAL_FRAMES else FINAL_FRAME_MS,
                "callout": None,
                "glow": glow,
                "banner": banner,
                "bannerA": _ease_out(t * 1.6, 3),
                "fx": {"kind": fx_kind, "t": t} if fx_kind else None,
                "panel": final_panel,
                "full": f == 1,
            }
        )
    # El último fotograma es el PNG de la mesa: sin chispas a medias.
    states[-1]["fx"] = None
    return states


def board_state(history: Sequence[int], wagers: Sequence[Wager] = ()) -> dict[str, Any]:
    """La mesa quieta al abrirla: la bola en el último número y el marcador."""
    last = history[0] if history else None
    wheel = 180 - WHEEL_ORDER.index(last) * STEP if last is not None else 0.0
    ball = None
    if last is not None:
        ball = {"a": 180.0, "r": R_POCKET, "z": 0.0}
    return {
        "wheel": wheel,
        "ball": ball,
        "ms": FINAL_FRAME_MS,
        "lucky": [],
        "callout": {"t": "HAGAN JUEGO", "a": 1.0},
        "glow": [],
        "banner": None,
        "bannerA": 0.0,
        "fx": None,
        "panel": Panel(history=history, wagers=wagers).state(),
        "full": True,
    }


# -- Del navegador al GIF -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Media:
    """GIF del giro, PNG final y lo que dura la animación hasta el cartel."""

    gif: bytes
    png: bytes
    seconds: float


def assemble(patches: list[dict[str, Any]]) -> list[Image.Image]:
    """Fotogramas enteros a partir de los recuadros de la escena (`browser_scene.assemble`)."""
    return browser_scene.assemble(patches, (W, H))


def encode(frames: list[Image.Image], durations: list[int]) -> Media:
    """GIF (sin repetir) y PNG del último fotograma.

    `seconds` es lo que tarda en llegar el cartel: hasta ahí espera el cog
    para cambiar el GIF por el PNG.
    """
    gif = local_palette_gif(frames, durations, loop=False)
    png = io.BytesIO()
    frames[-1].save(png, format="PNG", optimize=True)
    return Media(gif=gif, png=png.getvalue(), seconds=sum(durations[:-1]) / 1000)


class RouletteScene:
    """Dibuja la ruleta con Node, si no con Chromium y, si no, con Pillow (`fallback`).

    Args:
        fallback: La rueda de Pillow de siempre.
        executable_path: Chromium concreto (si no, el que instaló Playwright).
        node: Node concreto (si no, el del PATH).
    """

    def __init__(
        self,
        fallback: WheelRenderer | None = None,
        *,
        executable_path: str | None = None,
        node: str | None = None,
    ) -> None:
        self.fallback = fallback or WheelRenderer()
        self.painter = NodeScene(SCENE, name="El pintor de la ruleta", node=node)
        self.browser = BrowserScene(
            SCENE,
            name="El navegador de la ruleta",
            viewport=(W, H),
            ready="loadFonts()",
            executable_path=executable_path,
        )

    @property
    def disabled(self) -> bool:
        """Si Node y el navegador fallaron y ya solo se dibuja con Pillow."""
        return self.painter.disabled and self.browser.disabled

    async def close(self) -> None:
        """Cierra Node y el navegador (al apagar el bot)."""
        await self.painter.close()
        await self.browser.close()

    async def _frames(self, states: list[dict[str, Any]], seed: int) -> list[dict[str, Any]] | None:
        """Recuadros de cada fotograma (ver `assemble`); `None` si no hay Node ni navegador."""
        meta = {**meta_state(), "seed": seed}
        patches = await self.painter.render(meta, states)
        if patches is None:
            patches = await self.browser.render(meta, states)
        return patches

    async def board(self, history: Sequence[int], wagers: Sequence[Wager] = ()) -> bytes:
        """PNG de la mesa recién abierta."""
        patches = await self._frames([board_state(history, wagers)], seed=0)
        if patches is None:
            return await asyncio.to_thread(self.fallback.idle_png)
        return patch_png(patches[0])

    async def spin(self, outcome: RoundOutcome, *, history: Sequence[int], seed: int) -> Media:
        """GIF de la tirada, su PNG final y lo que dura hasta el cartel."""
        states = spin_states(outcome, history=history, seed=seed)
        patches = await self._frames(states, seed)
        if patches is None:
            media = await asyncio.to_thread(self.fallback.media, outcome.pocket)
            return Media(gif=media.gif, png=media.png, seconds=SPIN_SECONDS)
        durations = [state["ms"] for state in states]
        return await asyncio.to_thread(lambda: encode(assemble(patches), durations))
