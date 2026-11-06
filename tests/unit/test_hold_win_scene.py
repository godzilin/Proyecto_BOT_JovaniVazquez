"""La escena de Botes: el ritmo de cada jugada (Python) y que Node la pinte bien."""

from __future__ import annotations

import asyncio
import io
import json
import random
import time
from pathlib import Path

import pytest
import pytest_asyncio
from PIL import Image, ImageChops, ImageStat
from test_node_scene import needs_node, node_ready, own_node

from bot.cogs.hold_win import base_panel, bonus_panel
from bot.services import browser_scene, node_scene
from bot.services.browser_scene import BrowserScene
from bot.services.hold_win import (
    CASE_SIZE,
    REELS,
    ROWS,
    THEMES,
    BonusGame,
    Cell,
    Kind,
    Meters,
    Tier,
    Trigger,
    cell_index,
    evaluate_base,
    fill_cases,
    spin_base,
    to_amount,
)
from bot.services.hold_win_render import Banner
from bot.services.hold_win_scene import (
    OUT_H,
    OUT_W,
    SCENE,
    BonusFinish,
    HoldWinScene,
    base_states,
    base_strips,
    bonus_end_state,
    bonus_intro_states,
    bonus_step_states,
    bonus_still_state,
    durations,
    filled_cases,
    meta_state,
    still_state,
    tease_hit,
    tease_of,
    tracks_for,
)
from bot.services.node_scene import NodeScene

module_loop = pytest.mark.asyncio(loop_scope="module")
LOCAL_CHROMIUM = Path("/opt/pw-browsers/chromium")
NO_CHROMIUM = "/no/existe/chromium"
NO_NODE = "/no/existe/node"

STAKE = 100
VOLCAN = THEMES["volcan"]


def find_spin(predicate, seed: int = 1):  # noqa: ANN001, ANN201
    rng = random.Random(seed)
    for _ in range(300_000):
        spin = spin_base(rng)
        if predicate(spin):
            return spin
    raise AssertionError("no hay tirada así")


def play(spin, meters: Meters | None = None):  # noqa: ANN001, ANN201
    """Los fotogramas de `spin` con sus maletines antes y después."""
    meters = meters or Meters()
    before = base_panel(meters, STAKE)
    fill_cases(meters, spin, STAKE, random.Random(0))
    after = base_panel(meters, STAKE)
    states, extra = base_states("volcan", spin, stake=STAKE, before=before, after=after)
    return states, extra, before, after


def seconds(states: list[dict]) -> float:
    for state in states[:-1]:
        assert state["ms"] > 0 and state["ms"] % 10 == 0, state["ms"]
    assert "ms" not in states[-1]
    return sum(state["ms"] for state in states[:-1]) / 1000


def coin(tier: Tier, value: int) -> Cell:
    return Cell(Kind.COIN, tier=tier, value=value)


def epic_spin():  # noqa: ANN201
    grid = [Cell(Kind.PAY, symbol=k % 4) for k in range(20)]
    grid[cell_index(0, 0)] = Cell(Kind.COLLECT)
    for reel, row in ((0, 2), (1, 1), (1, 3), (2, 0), (3, 0), (3, 2)):
        grid[cell_index(reel, row)] = coin(Tier.RED, 500)
    grid[cell_index(4, 3)] = Cell(Kind.COLLECT)
    return evaluate_base(grid)


def is_rescue_miss(spin) -> bool:  # noqa: ANN001
    tease = tease_of(spin)
    return tease.kind == "rescue" and not tease_hit(spin, tease)


# -- Juego base -------------------------------------------------------------------------------


def test_sin_nada_en_juego_la_tirada_es_corta() -> None:
    spin = find_spin(lambda s: not s.points and tease_of(s).kind is None)
    states, *_ = play(spin)
    assert seconds(states) < 2.4
    json.dumps(states)


def test_el_rescate_hace_esperar_y_frena_con_fotogramas_largos() -> None:
    calm = find_spin(lambda s: not s.points and tease_of(s).kind is None)
    rescue = find_spin(is_rescue_miss)
    calm_states, *_ = play(calm)
    states, *_ = play(rescue)
    assert seconds(states) > seconds(calm_states) + 1.5
    teasing = [s for s in states if s["focus"] == REELS - 1]
    assert teasing and all(s["glow"] > 0 for s in teasing)
    assert all(s["bulbs"]["shape"] == "diamond" for s in teasing)
    assert max(s["ms"] for s in teasing) > 70  # cámara lenta: fotogramas largos
    assert len(states) < 90


def test_cada_espera_tiene_su_luz_y_su_forma() -> None:
    shapes = {}
    for kind in ("rescue", "double", "ways"):
        spin = find_spin(lambda s, k=kind: tease_of(s).kind == k)
        states, *_ = play(spin)
        teasing = [s for s in states if s["focus"] == REELS - 1]
        shapes[kind] = {(s["bulbs"]["shape"], s["glowColor"]) for s in teasing}
        assert len(shapes[kind]) == 1
    assert len({next(iter(v)) for v in shapes.values()}) == 3


def test_el_rescate_gordo_apaga_lo_demas() -> None:
    spin = find_spin(lambda s: tease_of(s).kind == "rescue_big")
    states, *_ = play(spin)
    assert max(s["dim"] for s in states) > 0.5
    dimmed = [s for s in states if s["dim"] > 0]
    assert all(set(tease_of(spin).cells) <= set(s["keep"]) for s in dimmed)


def test_las_paradas_varian_de_una_tirada_a_otra_pero_se_repiten_para_la_misma() -> None:
    rng = random.Random(4)
    lands = {
        tuple(t.land for t in tracks_for(s, tease_of(s)))
        for s in (spin_base(rng) for _ in range(20))
    }
    assert len(lands) > 5
    spin = find_spin(lambda s: s.points > 0)
    assert play(spin)[:2] == play(spin)[:2]


def test_los_rodillos_acaban_en_su_tirada_y_el_ultimo_es_el_quieto() -> None:
    spin = find_spin(lambda s: s.collectors and s.wins)
    states, extra, before, after = play(spin)
    spinning = [s for s in states if s["reels"] is not None]
    assert spinning[-1]["reels"] == [1.0] * REELS
    strips = extra["strips"]
    for reel in range(REELS):
        assert strips[reel][1 : 1 + ROWS] == [
            states[-1]["board"][cell_index(reel, row)] for row in range(ROWS)
        ]
    assert states[-1] == still_state(
        spin, stake=STAKE, panel=after, full=filled_cases(before, spin)
    )


def test_si_el_rescate_no_llega_a_veces_se_ve_pasar_el_recogedor() -> None:
    rng = random.Random(9)
    seen = 0
    for _ in range(40):
        spin = spin_base(rng)
        while not is_rescue_miss(spin):
            spin = spin_base(rng)
        tease = tease_of(spin)
        strips = base_strips("volcan", spin, tease, tracks_for(spin, tease), STAKE)
        seen += strips[-1][ROWS + 1] == "c"
        assert "c" not in strips[-1][1 : 1 + ROWS]
    assert 10 < seen < 40


def test_las_monedas_vuelan_a_su_maletin_y_el_lleno_se_ve_lleno() -> None:
    spin = find_spin(lambda s: s.coins_by_tier()[Tier.GREEN] >= 1 and not s.points)
    meters = Meters()
    meters.cases[Tier.GREEN].coins = CASE_SIZE[Tier.GREEN] - 1
    states, _extra, before, after = play(spin, meters)
    assert any(s["fly"] for s in states)
    assert filled_cases(before, spin)[0]
    last = states[-1]["footer"]["cases"][0]
    assert last[1] == "¡LLENO!" and last[3] is True and last[0] == 1.0


def test_el_cartel_sube_de_nivel_mientras_cuenta_la_recogida() -> None:
    spin = epic_spin()
    states, *_ = play(spin)
    tiers = [s["banner"]["tier"] for s in states if s["banner"]]
    assert tiers[0] == "big" and tiers[-1] == "epic"
    assert tiers == sorted(tiers, key=["big", "mega", "epic"].index)
    beams = [s["beam"] for s in states if s["beam"]]
    assert beams and beams[0]["from"] == cell_index(0, 0)
    # Con dos recogedores, el segundo pase sale del otro.
    assert beams[-1]["from"] == cell_index(4, 3)
    meters = [s["meter"]["text"] for s in states if s["meter"]]
    assert meters[-1].replace(".", "").startswith(str(to_amount(spin.points, STAKE)))


# -- Bonus --------------------------------------------------------------------------------------


def bonus_step(predicate, seed: int = 5):  # noqa: ANN001, ANN201
    """Una tirada del bonus que cumple `predicate(panel antes, tirada, final)`."""
    rng = random.Random(seed)
    for _ in range(20_000):
        game = BonusGame.start(Trigger("grand", STAKE, False), mini=180, major=700, rng=rng)
        while not game.finished:
            before = list(game.board)
            pb = bonus_panel(VOLCAN, game)
            step = game.step(rng)
            pa = bonus_panel(VOLCAN, game)
            finish = None
            if game.finished:
                result = game.result()
                finish = BonusFinish(result, to_amount(result.points, game.stake))
            if predicate(pb, step, finish):
                return game, before, step, pb, pa, finish
    raise AssertionError("no hay tirada de bonus así")


def run(case):  # noqa: ANN001, ANN201
    game, before, step, pb, pa, finish = case
    return bonus_step_states(
        before, step, stake=game.stake, panel_before=pb, panel_after=pa, finish=finish
    )


def test_las_casillas_vacias_paran_de_una_en_una() -> None:
    case = bonus_step(lambda pb, st, fin: st.landings and fin is None)
    states, extra = run(case)
    _game, before, step, _pb, pa, _fin = case
    assert set(extra["cells"]) == {
        str(i) for i, c in enumerate(before) if c is None or not c.is_coin
    }
    rest = {}
    for n, state in enumerate(states):
        for index, p, _blur, _flash in state["spin"]:
            if p == 1.0 and index not in rest:
                rest[index] = n
    assert len(set(rest.values())) > 3  # no paran todas a la vez
    assert states[-1] == bonus_still_state(step.board, stake=case[0].stake, panel=pa)
    json.dumps([states, extra])


def test_la_ultima_tirada_va_mas_despacio() -> None:
    normal = run(bonus_step(lambda pb, st, fin: pb.respins_left == 3 and fin is None))[0]
    last = run(bonus_step(lambda pb, st, fin: pb.respins_left == 1 and st.landings))[0]
    assert any(s["footer"]["bonus"]["tense"] > 0 for s in last if s["footer"])
    assert not any(s["footer"]["bonus"]["tense"] > 0 for s in normal if s["footer"])


def test_a_una_moneda_del_bote_la_ultima_casilla_se_hace_esperar() -> None:
    states, _ = run(bonus_step(lambda pb, st, fin: pb.coins == 19))
    chasing = [s for s in states if s["aura"]]
    assert chasing and max(s["dim"] for s in chasing) > 0.5
    assert max(s["ms"] for s in chasing) > 70


def test_los_misteriosos_tiemblan_y_se_giran() -> None:
    case = bonus_step(lambda pb, st, fin: any(x.mystery for x in st.landings) and fin is None)
    states, _ = run(case)
    assert any(s["shake"] for s in states)
    flips = [s["flip"] for s in states if s["flip"]]
    assert flips[0][0][1] < flips[-1][0][1] == 1.0


def test_el_ticket_vuela_al_multiplicador() -> None:
    case = bonus_step(lambda pb, st, fin: st.multiplier_added and fin is None)
    states, _ = run(case)
    _game, _before, step, pb, pa, _fin = case
    mults = [s["footer"]["bonus"]["mult"] for s in states if s["footer"]]
    assert mults[0] == f"×{pb.multiplier}" and mults[-1] == f"×{pa.multiplier}"
    assert any(f[2] == "t" for s in states for f in s["fly"])


def test_el_recuento_cuenta_moneda_a_moneda_y_acaba_en_el_total() -> None:
    case = bonus_step(lambda pb, st, fin: fin is not None and fin.result.coins >= 6)
    states, _ = run(case)
    game, _before, step, _pb, pa, finish = case
    counting = [s for s in states if s["meter"] and s["meter"]["label"] == "BONUS"]
    texts = [s["meter"]["text"] for s in counting]
    assert len(set(texts)) > finish.result.coins // 2
    assert states[-1] == bonus_end_state(step.board, stake=game.stake, panel=pa, finish=finish)
    assert states[-1]["meter"]["text"] == states[-1]["meter"]["fit"]


def test_la_entrada_al_bonus_empieza_donde_acaba_la_tirada() -> None:
    spin = find_spin(lambda s: s.coins_by_tier()[Tier.GREEN] >= 1)
    meters = Meters()
    meters.cases[Tier.GREEN].coins = CASE_SIZE[Tier.GREEN] - 1
    before = base_panel(meters, STAKE)
    trigger = fill_cases(meters, spin, STAKE, random.Random(1))
    after = base_panel(meters, STAKE)
    assert trigger is not None
    game = BonusGame.start(trigger, mini=meters.mini, major=meters.major, rng=random.Random(2))
    states = bonus_intro_states(
        spin,
        base_stake=STAKE,
        base_panel=after,
        full=filled_cases(before, spin),
        game=game,
        panel=bonus_panel(VOLCAN, game),
        name="BONUS CENIZA",
    )
    still = still_state(spin, stake=STAKE, panel=after, full=filled_cases(before, spin))
    assert states[0]["board"] == still["board"]
    assert states[-1]["banner"]["text"] == "BONUS CENIZA"
    assert seconds(states) < 3


# -- La escena de canvas ---------------------------------------------------------------------------


def panels(spin, meters: Meters | None = None):  # noqa: ANN001, ANN201
    """Botes y maletines (antes, después) de `spin`."""
    meters = meters or Meters()
    before = base_panel(meters, STAKE)
    fill_cases(meters, spin, STAKE, random.Random(0))
    return before, base_panel(meters, STAKE)


def test_la_escena_no_usa_math_random_y_deja_que_el_pintor_ponga_su_exportframe() -> None:
    html = SCENE.read_text(encoding="utf-8")
    assert "Math.random" not in html
    assert "window.exportFrame ??=" in html
    assert "toDataURL" not in html.split("window.exportFrame ??=")[0]


def test_la_meta_lleva_lo_que_la_escena_necesita_de_cada_maquina() -> None:
    for theme in THEMES:
        meta = meta_state(theme)
        assert meta["theme"] == theme and meta["out"] == [OUT_W, OUT_H]
        assert {"p0", "p5", "w", "c", "ticket", "extra", "instant", "max", "mystery"} <= set(
            meta["symbols"]
        )
        assert meta["style"]["effect"] in ("lava", "gold", "lightning")
        json.dumps(meta)
    assert len({meta_state(t)["symbolsKey"] for t in THEMES}) == 3


async def test_sin_node_ni_chromium_se_dibuja_con_el_pillow_de_antes() -> None:
    scene = HoldWinScene(executable_path=NO_CHROMIUM, node=NO_NODE)
    spin = find_spin(lambda s: s.collectors and s.wins)
    before, after = panels(spin)
    media = await scene.base("volcan", spin, stake=STAKE, before=before, after=after)
    assert scene.disabled
    assert Image.open(io.BytesIO(media.gif)).format == "GIF" and media.seconds > 0
    assert Image.open(io.BytesIO(media.png)).format == "PNG"
    still = await scene.still("volcan", spin, stake=STAKE, panel=after)
    assert Image.open(io.BytesIO(still)).format == "PNG"
    await scene.close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def scene() -> HoldWinScene:
    """Las máquinas de Botes con Node (y sin Chromium), compartidas por el módulo."""
    if not node_ready():
        pytest.skip("No hay Node con @napi-rs/canvas")
    escena = HoldWinScene(executable_path=NO_CHROMIUM)
    yield escena
    await escena.close()


def check_media(media, states: list[dict]) -> None:  # noqa: ANN001
    gif = Image.open(io.BytesIO(media.gif))
    assert gif.format == "GIF" and gif.size == (OUT_W, OUT_H)
    assert gif.n_frames <= len(states)  # el GIF junta los fotogramas seguidos que no cambian nada
    assert media.seconds == pytest.approx(sum(durations(states)[:-1]) / 1000)
    png = Image.open(io.BytesIO(media.png))
    assert png.size == (OUT_W, OUT_H) and len(png.convert("RGB").getcolors(1 << 20) or []) > 200


@pytest.mark.lenta
@needs_node
@module_loop
async def test_con_node_cada_jugada_de_cada_maquina_sale_con_su_gif(scene: HoldWinScene) -> None:
    nothing = find_spin(lambda s: not s.points and tease_of(s).kind is None)
    rescue = find_spin(is_rescue_miss)
    for theme, spin in (("volcan", nothing), ("mina", epic_spin()), ("olimpo", rescue)):
        before, after = panels(spin)
        states, *_ = base_states(theme, spin, stake=STAKE, before=before, after=after)
        media = await scene.base(theme, spin, stake=STAKE, before=before, after=after)
        check_media(media, states)
    case = bonus_step(lambda pb, st, fin: st.landings and fin is None)
    game, board, step, pb, pa, finish = case
    states, _ = run(case)
    media = await scene.bonus_step(
        "mina", board, step, stake=game.stake, panel_before=pb, panel_after=pa, finish=finish
    )
    check_media(media, states)
    case = bonus_step(lambda pb, st, fin: fin is not None and fin.result.coins >= 6)
    game, board, step, pb, pa, finish = case
    states, _ = run(case)
    media = await scene.bonus_step(
        "olimpo", board, step, stake=game.stake, panel_before=pb, panel_after=pa, finish=finish
    )
    check_media(media, states)
    assert not scene.painter.disabled


@needs_node
@module_loop
async def test_la_entrada_al_bonus_se_pinta_con_node(scene: HoldWinScene) -> None:
    spin = find_spin(lambda s: s.coins_by_tier()[Tier.GREEN] >= 1)
    meters = Meters()
    meters.cases[Tier.GREEN].coins = CASE_SIZE[Tier.GREEN] - 1
    before = base_panel(meters, STAKE)
    trigger = fill_cases(meters, spin, STAKE, random.Random(1))
    after = base_panel(meters, STAKE)
    assert trigger is not None
    game = BonusGame.start(trigger, mini=meters.mini, major=meters.major, rng=random.Random(2))
    panel = bonus_panel(VOLCAN, game)
    media = await scene.bonus_intro(
        "volcan",
        spin,
        base_stake=STAKE,
        before=before,
        after=after,
        game=game,
        panel=panel,
        name="BONUS CENIZA",
        banner=Banner("BONUS CENIZA", "", 2),
    )
    states = bonus_intro_states(
        spin,
        base_stake=STAKE,
        base_panel=after,
        full=filled_cases(before, spin),
        game=game,
        panel=panel,
        name="BONUS CENIZA",
    )
    check_media(media, states)


@pytest.mark.lenta
@needs_node
@module_loop
async def test_el_png_final_es_el_ultimo_fotograma_y_la_maquina_quieta(
    scene: HoldWinScene,
) -> None:
    spin = find_spin(lambda s: s.collectors and s.wins)
    before, after = panels(spin)
    assert not any(filled_cases(before, spin))
    media = await scene.base("mina", spin, stake=STAKE, before=before, after=after)
    gif = Image.open(io.BytesIO(media.gif))
    gif.seek(gif.n_frames - 1)
    png = Image.open(io.BytesIO(media.png)).convert("RGB")
    assert sum(ImageStat.Stat(ImageChops.difference(gif.convert("RGB"), png)).mean) / 3 < 12
    still = Image.open(
        io.BytesIO(await scene.still("mina", spin, stake=STAKE, panel=after, highlight=True))
    ).convert("RGB")
    assert ImageChops.difference(still, png).getbbox() is None
    turbo = await scene.base("mina", spin, stake=STAKE, before=before, after=after, turbo=True)
    assert turbo.gif == b"" and turbo.png == media.png


@needs_node
@module_loop
async def test_un_rodillo_parado_se_pinta_igual_que_una_casilla_quieta(
    scene: HoldWinScene,
) -> None:
    """El último fotograma del giro tiene que ser idéntico al PNG quieto (modo `board`)."""
    spin = find_spin(lambda s: s.wins and s.collectors)
    states, extra, _before, after = play(spin)
    parked = [s for s in states if s["reels"] is not None][-1]
    assert parked["reels"] == [1.0] * REELS and parked["blur"] == [0.0] * REELS
    quiet = still_state(spin, stake=STAKE, panel=after)["board"]
    board = {**parked, "reels": None, "board": quiet}
    meta = {**meta_state("volcan"), "strips": extra["strips"], "cells": None}
    a = await scene.painter.render(meta, [parked], procs=1)
    b = await scene.painter.render(meta, [board], procs=1)
    assert a is not None and b is not None
    assert a[0]["image"].tobytes() == b[0]["image"].tobytes()


@needs_node
@module_loop
async def test_cada_maquina_pinta_con_su_estilo(scene: HoldWinScene) -> None:
    spin = find_spin(lambda s: s.collectors and s.wins)
    before, after = panels(spin)
    states, *_ = base_states("volcan", spin, stake=STAKE, before=before, after=after)
    beams = [s for s in states if s["beam"]]
    assert beams
    shots = {}
    for theme in THEMES:
        meta = {**meta_state(theme), "strips": None, "cells": None}
        patches = await scene.painter.render(meta, [beams[len(beams) // 2]], procs=1)
        assert patches is not None
        shots[theme] = patches[0]["image"].convert("RGB")
    for a, b in (("volcan", "mina"), ("mina", "olimpo"), ("volcan", "olimpo")):
        assert sum(ImageStat.Stat(ImageChops.difference(shots[a], shots[b])).mean) / 3 > 12


@pytest.mark.lenta
@needs_node
@module_loop
async def test_el_gif_no_pesa_mas_de_lo_acordado(scene: HoldWinScene) -> None:
    """Lo que más pesa es lo que cambia en cada fotograma: si sube, que se note aquí."""
    nothing = find_spin(lambda s: not s.points and tease_of(s).kind is None)
    before, after = panels(nothing)
    media = await scene.base("volcan", nothing, stake=STAKE, before=before, after=after)
    assert len(media.gif) < 1000 * 1024
    before, after = panels(epic_spin())
    media = await scene.base("olimpo", epic_spin(), stake=STAKE, before=before, after=after)
    assert len(media.gif) < 2800 * 1024
    case = bonus_step(lambda pb, st, fin: st.landings and fin is None and pb.respins_left == 3)
    game, board, step, pb, pa, finish = case
    media = await scene.bonus_step(
        "mina", board, step, stake=game.stake, panel_before=pb, panel_after=pa, finish=finish
    )
    assert len(media.gif) < 700 * 1024


@needs_node
@module_loop
async def test_node_y_chromium_pintan_lo_mismo_los_botes() -> None:
    """Mismo motor (Skia): solo cambia algún borde de letra, no colores ni posiciones."""
    if not LOCAL_CHROMIUM.exists():
        pytest.skip("No hay Chromium en esta máquina")
    painter = NodeScene(SCENE, name="El pintor de los Botes")
    browser = BrowserScene(
        SCENE, name="chromium", ready="loadFonts()", executable_path=str(LOCAL_CHROMIUM)
    )
    spin = epic_spin()
    before, after = panels(spin)
    states, extra, *_ = base_states("olimpo", spin, stake=STAKE, before=before, after=after)
    states = states[::5]
    meta = {**meta_state("olimpo"), "strips": None, "cells": None, **extra}
    try:
        from_browser = await browser.render(meta, states)
        from_node = await painter.render(meta, states)
    finally:
        await browser.close()
        await painter.close()
    if from_browser is None:
        pytest.skip("Chromium no arranca en esta máquina")
    assert from_node is not None
    a, b = await asyncio.gather(
        asyncio.to_thread(browser_scene.assemble, from_browser, (OUT_W, OUT_H)),
        asyncio.to_thread(browser_scene.assemble, from_node, (OUT_W, OUT_H)),
    )
    assert len(a) == len(b) == len(states)
    for x, y in zip(a, b, strict=True):
        assert sum(ImageStat.Stat(ImageChops.difference(x, y)).mean) / 3 < 4


async def split_and_whole(
    sharing: HoldWinScene, meta: dict, states: list[dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pinta `states` repartido entre procesos y de un tirón: tienen que dar los mismos bytes."""
    pool = node_scene._pool(own_node())
    deadline = time.monotonic() + 20
    while len([w for w in pool.workers if w.alive and not w.busy]) < 3:
        assert time.monotonic() < deadline, "No se han abierto los procesos de refuerzo"
        await asyncio.sleep(0.05)
    whole = await sharing.painter.render(meta, states, procs=1)
    assert whole is not None
    setups: list[str] = []
    call = node_scene._Process.call

    async def counted(self: node_scene._Process, request: dict, **kw: bool):  # noqa: ANN202
        setups.extend(["setup"] if request["call"] == "setup" else [])
        return await call(self, request, **kw)

    monkeypatch.setattr(node_scene._Process, "call", counted)
    split = await sharing.painter.render(meta, states)
    assert split is not None
    assert len(setups) > 1, "El dibujo no se ha repartido"
    assert len(split) == len(whole) == len(states)
    for a, b in zip(split, whole, strict=True):
        assert a["image"].tobytes() == b["image"].tobytes()


@pytest.mark.lenta
@needs_node
@module_loop
async def test_la_tirada_repartida_pinta_lo_mismo_que_de_un_tiron(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cada tramo empieza con su `setup` y su primer fotograma entero: sale idéntico."""
    sharing = HoldWinScene(executable_path=NO_CHROMIUM, node=own_node())
    spin = epic_spin()
    before, after = panels(spin)
    states, extra, *_ = base_states("mina", spin, stake=STAKE, before=before, after=after)
    meta = {**meta_state("mina"), "strips": None, "cells": None, **extra}
    try:
        # Abre los procesos de refuerzo: sin ellos, el primero lo pintaría todo.
        assert await sharing.painter.render(meta, states[:8]) is not None
        await split_and_whole(sharing, meta, states, monkeypatch)
    finally:
        await sharing.close()
        await node_scene.shutdown()


@pytest.mark.lenta
@needs_node
@module_loop
async def test_el_bonus_repartido_pinta_lo_mismo_que_de_un_tiron(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sharing = HoldWinScene(executable_path=NO_CHROMIUM, node=own_node())
    case = bonus_step(lambda pb, st, fin: fin is not None and fin.result.coins >= 6)
    states, extra = run(case)
    meta = {**meta_state("volcan"), "strips": None, "cells": None, **extra}
    try:
        assert await sharing.painter.render(meta, states[:8]) is not None
        await split_and_whole(sharing, meta, states, monkeypatch)
    finally:
        await sharing.close()
        await node_scene.shutdown()


def test_en_el_recuento_cada_cartel_entra_entero_antes_del_siguiente() -> None:
    case = bonus_step(lambda pb, st, fin: fin is not None and fin.result.points >= 5_000)
    states, _ = run(case)
    tiers = [s["banner"]["tier"] for s in states[:-1] if s["banner"]]
    for tier in ("big", "mega", "epic"):
        assert tiers.count(tier) >= 4, tiers
