"""Dibujo de las carreras: la carrera con Node y la parrilla y el boleto con Chromium.

Discord no ejecuta JavaScript en los mensajes, así que el JavaScript corre
aquí, en el bot: la escena vive en `assets/caballos/escena.html`. Tiene dos
mitades que se pintan de forma distinta:

- **La carrera** (los fotogramas y el podio) es solo canvas 2D y la pinta
  Node con Skia (`bot.services.node_scene`), sin navegador y con los
  fotogramas en RGBA crudo. Guarda estado de un fotograma al siguiente (el
  polvo de los cascos), así que va en un solo proceso y en orden
  (`NodeScene.sequence`). Si no hay Node, la pinta Chromium.
- **La parrilla y el boleto** son HTML/CSS con capturas de pantalla: esos
  siguen en Chromium (Playwright) y no tienen otro camino que Pillow.

De ahí salen los fotogramas con los que este módulo monta el GIF. Así hay
degradados, sombras, desenfoque, sedas con su dibujo y caballos con el galope
articulado, cosas que con Pillow a mano quedaban pobres.

Python sigue decidiendo todo: dónde está cada caballo en cada fotograma, el
ritmo, la cámara lenta del foto-finish y la lluvia salen de
`bot.services.horses_render` (`frame_times`, `screen_positions`), que es la
misma cuenta que usa el dibujo con Pillow. La escena solo pinta.

**Si falla un pintor, se pasa al siguiente:** Node, Chromium y, al final,
Pillow. El primer fallo de cada uno se avisa una vez en el log y desde
entonces se salta. El juego nunca se queda sin imagen.

Coste: una carrera son ~130-170 fotogramas. Node arranca en ~0,4 s y ocupa
~90 MB; el GIF se monta después en un hilo, fuera del event loop. Chromium
arranca en ~1-2 s la primera vez y ocupa ~150-250 MB mientras está abierto
(ahora solo para la parrilla y el boleto, o si Node falla); ambos se cierran
solos tras `IDLE_SECONDS` sin uso. Solo hay una pestaña y un proceso de Node,
y una carrera se pinta detrás de otra.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PIL import Image

from bot.services.horses import (
    ODDS_TRIALS,
    SEGMENTS,
    BetKind,
    Horse,
    HorseRecord,
    Odds,
    Pick,
    RaceCard,
    RaceResult,
    Tip,
    format_odds,
    margin_text,
    photo_finish,
)
from bot.services.horses_render import (
    FINAL_FRAME_MS,
    FINISH_X,
    FRAME_MS,
    GATE_FRAMES,
    SLOW_MOTION,
    HorseRenderer,
    Media,
    frame_times,
    rain_start,
    screen_positions,
)
from bot.services.node_scene import NodeScene
from bot.utils.gif import local_palette_gif

logger = logging.getLogger(__name__)

SCENE = Path(__file__).resolve().parent.parent / "assets" / "caballos" / "escena.html"
#: Tras este rato sin dibujar nada, el navegador se cierra para liberar memoria.
IDLE_SECONDS = 10 * 60
#: Fotogramas que se piden al navegador de una vez (cada uno es un PNG en base64).
BATCH = 40
#: Zancadas por segundo del GIF: con 70-80 ms por fotograma, unos 5 fotogramas por zancada.
STRIDES_PER_SECOND = 2.6
#: Fotogramas en que se abren las puertas de los cajones.
GATE_OPENING_FRAMES = 4


def _thousands(value: int) -> str:
    return f"{value:,}".replace(",", ".")


#: Capas de la escena, una por caballo y repartidas para que en una carrera
#: no salgan todos iguales: castaño, alazán, tordo, negro, palomino y castaño oscuro.
BAY, CHESTNUT, GREY = (122, 68, 36), (170, 92, 44), (178, 178, 184)
BLACK, PALOMINO, DARK_BAY = (52, 44, 42), (214, 172, 98), (86, 54, 34)
SCENE_COATS: dict[str, tuple[int, int, int]] = {
    "falcon": CHESTNUT, "manual": BAY, "paguita": BAY, "gofio": PALOMINO,
    "fango": BLACK, "puerta": GREY, "pegasus": BLACK, "uco": BAY,
    "colchon": GREY, "rodalies": CHESTNUT, "timple": PALOMINO, "robuso": BAY,
    "wepa": CHESTNUT, "nextgen": DARK_BAY, "amnistia": CHESTNUT, "bono": DARK_BAY,
}  # fmt: skip
#: En la escena los cajones están más adentro que en Pillow: los caballos son
#: más largos y si no, salen cortados por la izquierda.
SCENE_START_X = 130


def horse_json(horse: Horse, number: int) -> dict[str, Any]:
    """Lo que la escena necesita de un caballo: dorsal, nombre, capa y sedas."""
    coat = SCENE_COATS.get(horse.key, BAY)
    mane = tuple(max(0, c - 70) for c in coat)
    return {
        "n": number,
        "name": horse.name,
        "coat": list(coat),
        "mane": list(mane),
        # Un lucero en la frente a los que tocan, siempre los mismos.
        "blaze": sum(map(ord, horse.key)) % 3 == 0,
        "silks": [list(horse.silks[0]), list(horse.silks[1])],
        "pattern": horse.pattern,
    }


def ranking_at(result: RaceResult, distance: int, t: float) -> list[int]:
    """Orden de la carrera a los `t` segundos; los que ya han entrado, por su llegada."""
    finished = [i for i in result.order if result.times[i] <= t]
    running = sorted(
        (i for i in range(len(result.times)) if i not in finished),
        key=lambda i: (-result.position_at(i, t, distance), result.times[i]),
    )
    return finished + running


def _segment(result: RaceResult, index: int, t: float) -> int:
    splits = result.splits[index]
    for segment in range(SEGMENTS):
        if t < splits[segment + 1]:
            return segment
    return SEGMENTS


class SceneRenderer:
    """Pinta la carrera con Node y la parrilla y el boleto con Chromium; el plan B es Pillow.

    Args:
        fallback: El dibujo de Pillow, para cuando no hay Node ni navegador.
        executable_path: Chromium concreto (si no, el que instaló Playwright).
        idle_seconds: Cuánto se deja abierto el navegador sin usarlo.
        node: Node concreto (si no, el del PATH).
    """

    def __init__(
        self,
        fallback: HorseRenderer | None = None,
        *,
        executable_path: str | None = None,
        idle_seconds: float = IDLE_SECONDS,
        node: str | None = None,
    ) -> None:
        self.fallback = fallback or HorseRenderer()
        self.executable_path = executable_path
        self.idle_seconds = idle_seconds
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None
        self._lock = asyncio.Lock()
        self._idle: asyncio.TimerHandle | None = None
        #: Pinta la carrera sin navegador; tiene su propio `disabled`.
        self.painter = NodeScene(
            SCENE, name="El pintor de las carreras", node=node, idle_seconds=idle_seconds
        )
        #: Si Chromium falló (para la parrilla, el boleto y la carrera sin Node):
        #: no se reintenta en cada carrera, se usa Pillow.
        self.disabled = False

    # -- Navegador -----------------------------------------------------------------------

    async def _ready(self) -> Any:
        """La pestaña con la escena cargada (arranca el navegador si hace falta)."""
        if self._page is not None and not self._page.is_closed():
            return self._page
        from playwright.async_api import async_playwright  # import perezoso: es pesado

        if self._playwright is None:
            self._playwright = await async_playwright().start()
        if self._browser is None or not self._browser.is_connected():
            self._browser = await self._playwright.chromium.launch(
                executable_path=self.executable_path,
                args=["--disable-gpu", "--disable-dev-shm-usage"],
            )
        self._page = await self._browser.new_page(
            viewport={"width": 640, "height": 360}, device_scale_factor=1
        )
        await self._page.goto(SCENE.as_uri())
        await self._page.evaluate("document.fonts.ready.then(() => true)")
        return self._page

    def _touch(self) -> None:
        """Programa el cierre del navegador tras `idle_seconds` sin uso."""
        if self._idle is not None:
            self._idle.cancel()
        loop = asyncio.get_running_loop()
        self._idle = loop.call_later(self.idle_seconds, lambda: asyncio.ensure_future(self.close()))

    async def close(self) -> None:
        """Cierra Node y el navegador (al apagar el bot o tras un rato sin carreras)."""
        await self.painter.close()
        async with self._lock:
            if self._idle is not None:
                self._idle.cancel()
                self._idle = None
            browser, playwright = self._browser, self._playwright
            self._page = self._browser = self._playwright = None
            try:
                if browser is not None:
                    await browser.close()
                if playwright is not None:
                    await playwright.stop()
            except Exception:
                logger.debug("No se pudo cerrar el navegador de las carreras", exc_info=True)

    async def _with_page(self, work: Any) -> Any:
        """Ejecuta `work(page)` con la pestaña; `None` si no hay navegador."""
        if self.disabled:
            return None
        async with self._lock:
            try:
                page = await self._ready()
                result = await work(page)
            except Exception:
                logger.warning(
                    "El navegador de las carreras no está disponible; se dibuja con Pillow",
                    exc_info=True,
                )
                self.disabled = True
                self._page = None
                return None
            self._touch()
            return result

    # -- Parrilla y boleto ---------------------------------------------------------------

    async def card(
        self,
        card: RaceCard,
        odds: Odds,
        records: dict[str, HorseRecord],
        *,
        tip: Tip | None = None,
        pot: int | None = None,
    ) -> bytes:
        """PNG de la parrilla de salida."""
        favourite = odds.favourite()
        data = {
            "name": card.name,
            "gp": card.grand_prix,
            "distanceText": f"{_thousands(card.distance)} m",
            "going": card.going.key,
            "goingLabel": card.going.label,
            "rain": round(card.rain_chance * 100),
            "pot": f"{_thousands(pot)} Y$" if pot is not None else None,
            "trials": _thousands(ODDS_TRIALS),
            "rows": [
                {
                    "horse": horse_json(horse, i + 1),
                    "p": odds.win[i],
                    "odds": format_odds(odds.odds(Pick(BetKind.WIN, (i,)))),
                    "form": list(records.get(horse.key, HorseRecord()).form),
                    "likes": horse.going.key,
                    "likesLabel": horse.going.label,
                    "tired": horse.key in card.tired,
                    "tip": tip.confidence if tip is not None and tip.horse == i else None,
                    "fav": i == favourite,
                }
                for i, horse in enumerate(card.horses)
            ],
        }

        async def work(page: Any) -> bytes:
            await page.evaluate("d => renderCard(d)", data)
            return await page.locator("#shot").screenshot(omit_background=True)

        png = await self._with_page(work)
        if png is None:
            return await asyncio.to_thread(
                self.fallback.card, card, odds, records, tip=tip, pot=pot
            )
        return png

    async def ticket(
        self,
        *,
        race: str,
        player: str,
        pick: Pick,
        names: Sequence[str],
        stake: int,
        odds: int,
        won: bool | None = None,
        prize: int | None = None,
        pot_share: int = 0,
        horses: Sequence[Horse] = (),
    ) -> bytes:
        """PNG del boleto; con `won=True` lleva el sello de PREMIADO."""
        shown = prize if prize is not None else stake * odds // 100
        data = {
            "race": race,
            "player": player[:24],
            "numbers": pick.numbers(),
            "kind": pick.kind.label,
            "horses": [
                horse_json(horse, number + 1)
                for horse, number in zip(horses, pick.horses, strict=False)
            ]
            or [
                {
                    "n": n + 1,
                    "name": name,
                    "silks": [[90, 90, 90], [200, 200, 200]],
                    "pattern": "liso",
                }
                for n, name in zip(pick.horses, names, strict=False)
            ],
            "stake": f"{_thousands(stake)} Y$",
            "odds": format_odds(odds),
            "prize": f"{_thousands(shown)} Y$",
            "won": bool(won),
            "pot": f"{_thousands(pot_share)} Y$" if pot_share else None,
        }

        async def work(page: Any) -> bytes:
            await page.evaluate("d => renderTicket(d)", data)
            return await page.locator("#shot").screenshot(omit_background=True)

        png = await self._with_page(work)
        if png is None:
            return await asyncio.to_thread(
                lambda: self.fallback.ticket(
                    race=race,
                    player=player,
                    pick=pick,
                    names=names,
                    stake=stake,
                    odds=odds,
                    won=won,
                    prize=prize,
                    pot_share=pot_share,
                )
            )
        return png

    # -- Carrera -------------------------------------------------------------------------

    def race_meta(self, card: RaceCard) -> dict[str, Any]:
        """El fondo de una carrera: caballos, terreno, distancia, postes y meta."""
        markers = []
        for left in (800, 400, 200):
            if left < card.distance:
                x = SCENE_START_X + (1 - left / card.distance) * (FINISH_X - SCENE_START_X)
                markers.append({"x": x, "label": str(left)})
        wet = card.going.wetter()
        return {
            "seed": sum(map(ord, card.name)) * 7919 % 100_000,
            "name": card.name,
            "gp": card.grand_prix,
            "going": card.going.key,
            "wetGoing": wet.key,
            "goingLabel": card.going.label.upper(),
            "wetLabel": wet.label.upper(),
            "distanceText": f"{_thousands(card.distance)} m",
            "startX": SCENE_START_X,
            "finishX": FINISH_X,
            "markers": markers,
            "horses": [horse_json(h, i + 1) for i, h in enumerate(card.horses)],
        }

    def race_states(self, card: RaceCard, result: RaceResult) -> list[dict[str, Any]]:
        """Lo que se ve en cada fotograma: posiciones, zancadas, cajones, lluvia y marcador."""
        times, slow = frame_times(card, result)
        rain_at = rain_start(result)
        winner_time = result.times[result.winner]
        photo = photo_finish(result, card.distance)
        states = []
        clock = 0.0  # segundos del GIF, para el ritmo de las zancadas
        flashed = False
        for n, t in enumerate(times):
            gate = n < GATE_FRAMES
            if not gate:
                clock += FRAME_MS / 1000 / (SLOW_MOTION if slow[n] else 1)
            opening = n - GATE_FRAMES
            meters = [result.position_at(i, t, card.distance) for i in range(card.size)]
            lead = min(max(meters), card.distance)
            flash = 0.0
            if photo and not flashed and t >= winner_time:
                flash, flashed = 0.65, True
            states.append(
                {
                    "frame": n,
                    "t": t,
                    "gate": gate,
                    "gateOpen": 0.0 if gate else min(1.0, (opening + 1) / GATE_OPENING_FRAMES),
                    "xs": screen_positions(card, result, t, start_x=SCENE_START_X),
                    "cycles": [
                        0.15 if gate else (clock * STRIDES_PER_SECOND + i * 0.29) % 1
                        for i in range(card.size)
                    ],
                    "rain": rain_at is not None and t >= rain_at,
                    "ranking": ranking_at(result, card.distance, t),
                    "remaining": max(0, int(round((card.distance - lead) / 10)) * 10),
                    "stumble": [
                        i
                        for i, segment in result.stumbles.items()
                        if not gate and _segment(result, i, t) == segment
                    ],
                    "bolt": [
                        i
                        for i, segment in result.bolted.items()
                        if not gate and _segment(result, i, t) >= segment
                    ],
                    "flash": flash,
                    "frozen": False,
                }
            )
        return states

    def podium_data(
        self, card: RaceCard, result: RaceResult, odds: Odds | None, states: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """El podio del último fotograma y, si hizo falta, el instante de la foto-finish."""
        rows = []
        for place, index in enumerate(result.order[:3]):
            if place == 0:
                note = "Ganador"
                shown = (
                    format_odds(odds.odds(Pick(BetKind.WIN, (index,)))) if odds is not None else ""
                )
            else:
                note = f"a {margin_text(result.lengths_behind(index, card.distance))}"
                shown = ""
            rows.append({"index": index, "note": note, "odds": shown})
        last = dict(states[-1], flash=0.0, frozen=True)
        data: dict[str, Any] = {"podium": rows, "last": last, "photo": None}
        if photo_finish(result, card.distance):
            t = result.times[result.winner]
            rain_at = rain_start(result)
            data["photo"] = dict(
                last,
                t=t,
                xs=screen_positions(card, result, t, start_x=SCENE_START_X),
                rain=rain_at is not None and t >= rain_at,
                stumble=[],
                bolt=[],
            )
        return data

    async def race(self, card: RaceCard, result: RaceResult, odds: Odds | None = None) -> Media:
        """GIF de la carrera y PNG de la llegada."""
        states = self.race_states(card, result)
        podium = self.podium_data(card, result, odds, states)
        meta = self.race_meta(card)

        calls: list[tuple[str, list[Any]]] = [("setupRace", [meta])]
        calls += [
            ("renderFrames", [states[start : start + BATCH]])
            for start in range(0, len(states), BATCH)
        ]
        calls.append(("renderPodium", [podium]))
        parts = await self.painter.sequence(calls)
        if parts is not None:
            # La primera llamada (setupRace) no devuelve imágenes.
            frames = [patch["image"] for part in parts for patch in part]
            return await asyncio.to_thread(self._encode, frames)

        async def work(page: Any) -> list[str]:
            await page.evaluate("m => setupRace(m)", meta)
            urls: list[str] = []
            for start in range(0, len(states), BATCH):
                urls += await page.evaluate("s => renderFrames(s)", states[start : start + BATCH])
            urls.append(await page.evaluate("d => renderPodium(d)", podium))
            return urls

        urls = await self._with_page(work)
        if urls is None:
            return await asyncio.to_thread(self.fallback.race, card, result)
        return await asyncio.to_thread(lambda: self._encode(self._decode(urls)))

    @staticmethod
    def _decode(urls: list[str]) -> list[Image.Image]:
        """Los data URL de PNG de Chromium, abiertos como imágenes."""
        return [
            Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("RGB")
            for url in urls
        ]

    @staticmethod
    def _encode(frames: list[Image.Image]) -> Media:
        """El GIF (todos menos el último, que es el podio) y el PNG de la llegada."""
        durations = [FRAME_MS] * (len(frames) - 1) + [FINAL_FRAME_MS]
        gif = local_palette_gif(frames, durations)
        png = io.BytesIO()
        # `optimize=True` tarda ~230 ms para ahorrar ~5 %.
        frames[-1].save(png, format="PNG", compress_level=6)
        seconds = FRAME_MS * (len(frames) - 1) / 1000
        return Media(gif=gif, png=png.getvalue(), seconds=seconds)
