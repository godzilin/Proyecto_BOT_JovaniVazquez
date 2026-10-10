"""Pruebas de bot.services.horses_scene: el dibujo con navegador y su plan B con Pillow.

Las que necesitan Chromium se saltan si no hay navegador en la máquina; las de
los fotogramas (qué se le pasa a la escena) y las del plan B no lo necesitan.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
import pytest_asyncio
from PIL import Image

from bot.services import horses as h
from bot.services.horses import SEGMENTS, STABLE_BY_KEY, BetKind, Going, Pick, RaceCard, RaceResult
from bot.services.horses_render import GATE_FRAMES, H, W
from bot.services.horses_scene import SCENE, SceneRenderer, horse_json, ranking_at

SIX = ("falcon", "manual", "paguita", "gofio", "fango", "uco")
#: Las pruebas con navegador comparten un bucle de eventos: el `browser` se abre una vez
#: y sus pruebas lo comparten (Playwright está atado al bucle que lo arrancó).
module_loop = pytest.mark.asyncio(loop_scope="module")
#: Chromium del entorno de desarrollo, si lo hay (en Docker lo instala Playwright).
LOCAL_CHROMIUM = Path("/opt/pw-browsers/chromium")
#: Un Node que no existe: la carrera se pinta con Chromium o con Pillow, no con Node.
NO_NODE = "/no/existe/node"


def card_of(**kw) -> RaceCard:  # noqa: ANN003
    return RaceCard(
        horses=tuple(STABLE_BY_KEY[k] for k in SIX), going=Going.SECO, distance=1_200, **kw
    )


def photo_result(gaps: tuple[float, ...] = (0.0, 0.01, 1.0, 1.5, 3.0, 4.0)) -> RaceResult:
    order = (3, 0, 1, 2, 4, 5)
    times = [0.0] * 6
    for place, index in enumerate(order):
        times[index] = 75.0 + gaps[place]
    splits = tuple(tuple(t * s / SEGMENTS for s in range(SEGMENTS + 1)) for t in times)
    return RaceResult(
        order=order, times=tuple(times), splits=splits, stumbles={1: 3}, bolted={5: 4}, rained=True
    )


def test_la_escena_existe_y_trae_sus_funciones() -> None:
    html = SCENE.read_text(encoding="utf-8")
    for function in ("setupRace", "renderFrames", "renderPodium", "renderCard", "renderTicket"):
        assert f"window.{function}" in html


def test_cada_caballo_lleva_dorsal_capa_y_sedas() -> None:
    data = horse_json(STABLE_BY_KEY["falcon"], 3)
    assert data["n"] == 3
    assert data["name"] == "Falcon Presidencial"
    assert len(data["coat"]) == 3
    assert data["silks"] == [list(c) for c in STABLE_BY_KEY["falcon"].silks]


def test_los_fotogramas_empiezan_en_los_cajones_y_acaban_tras_el_tercero() -> None:
    card, result = card_of(), photo_result()
    states = SceneRenderer().race_states(card, result)
    assert all(s["gate"] and s["gateOpen"] == 0 for s in states[:GATE_FRAMES])
    assert states[GATE_FRAMES]["gateOpen"] > 0
    assert states[-1]["t"] >= result.times[result.order[2]]
    remaining = [s["remaining"] for s in states]
    assert remaining == sorted(remaining, reverse=True)
    assert remaining[-1] == 0
    assert all(0 <= c < 1 for s in states for c in s["cycles"])
    # Llueve desde la mitad, hay un tropiezo, un desbocado y un fogonazo en la meta.
    assert not states[0]["rain"] and states[-1]["rain"]
    assert any(s["stumble"] == [1] for s in states)
    assert states[-1]["bolt"] == [5]
    assert sum(s["flash"] > 0 for s in states) == 1


def test_el_podio_lleva_la_foto_solo_si_hace_falta() -> None:
    card, result = card_of(), photo_result()
    odds = h.estimate(card, np.random.default_rng(1), trials=2_000)
    renderer = SceneRenderer()
    data = renderer.podium_data(card, result, odds, renderer.race_states(card, result))
    assert [row["index"] for row in data["podium"]] == [3, 0, 1]
    assert data["podium"][0]["odds"].endswith("x")
    assert data["photo"]["t"] == result.times[3]
    calm = photo_result(gaps=(0.0, 1.0, 2.0, 3.0, 4.0, 5.0))
    assert renderer.podium_data(card, calm, odds, renderer.race_states(card, calm))["photo"] is None


def test_la_clasificacion_respeta_el_orden_de_llegada() -> None:
    result = photo_result()
    assert ranking_at(result, 1_200, 10_000.0) == list(result.order)
    assert sorted(ranking_at(result, 1_200, 10.0)) == list(range(6))


async def test_sin_navegador_dibuja_con_pillow() -> None:
    renderer = SceneRenderer(executable_path="/no/existe/chromium", node=NO_NODE)
    media = await renderer.race(card_of(), photo_result())
    assert renderer.disabled
    assert Image.open(io.BytesIO(media.gif)).format == "GIF"
    png = await renderer.ticket(
        race="Premio", player="Ana", pick=Pick(BetKind.WIN, (0,)), names=["Falcon"],
        stake=100, odds=250,
    )  # fmt: skip
    assert Image.open(io.BytesIO(png)).format == "PNG"
    await renderer.close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser() -> SceneRenderer:
    """Un solo Chromium para las tres pruebas con navegador del módulo.

    Arrancarlo cuesta ~0,8 s y antes se hacía en cada prueba. Compartirlo no cambia lo
    que se comprueba: cada prueba sigue pidiendo su dibujo al mismo `SceneRenderer`,
    que reutiliza la pestaña como hace en producción entre carreras.
    """
    path = str(LOCAL_CHROMIUM) if LOCAL_CHROMIUM.exists() else None
    renderer = SceneRenderer(executable_path=path, node=NO_NODE)
    card = card_of()
    odds = h.estimate(card, np.random.default_rng(1), trials=2_000)
    png = await renderer.card(card, odds, {})
    if renderer.disabled:
        await renderer.close()
        pytest.skip("No hay Chromium en esta máquina")
    renderer.test_card_png = png  # type: ignore[attr-defined]
    yield renderer
    await renderer.close()


@module_loop
async def test_con_navegador_la_parrilla_es_un_png_ancho(browser: SceneRenderer) -> None:
    image = Image.open(io.BytesIO(browser.test_card_png))  # type: ignore[attr-defined]
    assert image.format == "PNG"
    assert image.width == W
    assert image.height > 6 * 40
    # No es una captura en blanco: hay colores distintos.
    assert len(image.convert("RGB").getcolors(1 << 20) or []) > 100


@module_loop
async def test_con_navegador_la_carrera_es_un_gif_que_cabe_en_discord(
    browser: SceneRenderer,
) -> None:
    card = card_of(rain_chance=0.5)
    odds = h.estimate(card, np.random.default_rng(1), trials=2_000)
    media = await browser.race(card, photo_result(), odds)
    gif = Image.open(io.BytesIO(media.gif))
    assert gif.size == (W, H)
    assert gif.n_frames > GATE_FRAMES
    assert len(media.gif) < 8 * 1024 * 1024
    assert Image.open(io.BytesIO(media.png)).size == (W, H)
    assert not browser.disabled


@module_loop
async def test_con_navegador_el_boleto_premiado_lleva_sello(browser: SceneRenderer) -> None:
    kwargs = {
        "race": "Premio Puerta del Sol",
        "player": "Diego",
        "pick": Pick(BetKind.EXACTA, (0, 1)),
        "names": ["Falcon Presidencial", "Manual de Resistencia"],
        "stake": 500,
        "odds": 1_250,
        "horses": [STABLE_BY_KEY["falcon"], STABLE_BY_KEY["manual"]],
    }
    plain = Image.open(io.BytesIO(await browser.ticket(**kwargs))).convert("RGB")
    won = Image.open(io.BytesIO(await browser.ticket(**kwargs, won=True, prize=6_250)))
    assert plain.size == won.size
    assert plain.tobytes() != won.convert("RGB").tobytes()
