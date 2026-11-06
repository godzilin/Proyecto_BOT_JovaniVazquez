"""Pruebas de bot.services.pachinko_render: geometría, línea de tiempo y GIF por tablero."""

from __future__ import annotations

import io
import logging
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from bot.services.pachinko import BOARDS, CLASSIC, ONI, Ball, Board, Draw, Kind, build_volley
from bot.services.pachinko_motion import LAUNCH_GAP, motion_for
from bot.services.pachinko_physics import PIN_R, geometry_for, library
from bot.services.pachinko_pieces import PIECES_DIR, expected_sizes
from bot.services.pachinko_render import (
    CENTER_STOP,
    HEIGHT,
    PIN,
    POCKET_FLASH,
    REACH_STOP,
    SUPER_REACH_STOP,
    THEMES,
    WIDTH,
    PachinkoRenderer,
    build_timeline,
    bulb_palette,
    draw_length,
    layout_for,
    piece_problems,
)

ALL_BOARDS = list(BOARDS.values())


def ball_in(board: Board, pocket: int, trajectory: int = 0) -> Ball:
    return Ball((1,) * pocket + (0,) * (board.rows - pocket), trajectory)


def slowest_and_fastest(board: Board, pocket: int) -> tuple[int, int]:
    """Índices de la caída más lenta y la más rápida hacia `pocket`."""
    falls = library(board)[pocket]
    order = sorted(range(len(falls)), key=lambda i: falls[i].frames)
    return order[-1], order[0]


def volley_of(board: Board, starts: int, others: int, draws) -> object:  # noqa: ANN001
    """Tanda con `starts` bolas en START y el resto en el bolsillo `others`."""
    balls = [ball_in(board, board.start_pocket)] * starts
    balls += [ball_in(board, others)] * (10 - starts)
    iterator = iter(draws)
    return build_volley(board, balls, lambda: next(iterator))


MISS = Draw((1, 2, 3), Kind.MISS, False, 0)
REACH = Draw((4, 5, 4), Kind.MISS, True, 0)
RUSH = Draw((5, 5, 5), Kind.RUSH, True, 4)


@pytest.fixture(scope="module")
def renderer() -> PachinkoRenderer:
    return PachinkoRenderer()


def test_cada_tablero_tiene_su_tema() -> None:
    assert set(THEMES) == set(BOARDS)


@pytest.mark.parametrize("board", ALL_BOARDS, ids=lambda b: b.key)
def test_el_tablero_cabe_en_la_imagen(board: Board) -> None:
    layout = layout_for(board)
    assert layout.pocket_x(0) - layout.dx / 2 > 16
    assert layout.pocket_x(board.rows) + layout.dx / 2 < WIDTH - 16
    assert layout.tray_top + 30 < HEIGHT


def lone_ball(board: Board, pocket: int, trajectory: int):  # noqa: ANN201
    """Movimiento de una tanda de una sola bola: sin otras bolas, cae como en la biblioteca."""
    volley = build_volley(board, [ball_in(board, pocket, trajectory)], lambda: MISS)
    return motion_for(volley).balls[0]


@pytest.mark.parametrize("board", ALL_BOARDS, ids=lambda b: b.key)
def test_la_bola_sigue_su_caida_acaba_en_su_bolsillo_y_desaparece(board: Board) -> None:
    layout = layout_for(board)
    motion = lone_ball(board, 2, trajectory=5)
    points = library(board)[2][5].points
    assert motion.points == points
    assert motion.position(-1) is None
    assert motion.position(0) == points[0]
    assert motion.position(7) == points[7]
    x, y = motion.position(len(points) - 1)
    assert abs(x - layout.pocket_x(2)) < layout.dx / 2
    assert y >= layout.pocket_top
    assert motion.position(motion.landing) is None
    assert motion.landing == len(points) == motion.frames


def test_una_bola_lanzada_mas_tarde_se_ve_desde_su_fotograma_de_salida() -> None:
    motion = lone_ball(CLASSIC, 3, trajectory=1)
    late = replace(motion, launch=9)
    assert late.position(8) is None
    assert late.position(9) == motion.points[0]
    assert late.position(9 + len(motion.points)) is None


def test_entre_dos_fotogramas_la_bola_va_por_el_punto_medio() -> None:
    motion = lone_ball(CLASSIC, 3, trajectory=1)
    (x0, y0), (x1, y1) = library(CLASSIC)[3][1].points[4:6]
    x, y = motion.position(4.5)
    assert (x, y) == pytest.approx(((x0 + x1) / 2, (y0 + y1) / 2))


@pytest.mark.parametrize("board", ALL_BOARDS, ids=lambda b: b.key)
def test_los_clavos_dibujados_son_los_de_la_fisica(
    renderer: PachinkoRenderer, board: Board
) -> None:
    assets = renderer.assets(board)
    geometry = geometry_for(board)
    assert assets.layout.geometry is geometry
    for x, y in geometry.pins:
        center = sum(assets.base.getpixel((round(x), round(y))))
        beside = sum(assets.base.getpixel((round(x) + PIN_R + 3, round(y))))
        # El clavo es claro (metálico, con brillo) sobre un campo oscuro.
        assert center > beside + 200
    for pocket in range(board.rows + 1):
        assert assets.layout.pocket_x(pocket) == geometry.pocket_x(pocket)


def test_el_reach_y_el_super_reach_alargan_la_tirada() -> None:
    assert draw_length(MISS)[0] == CENTER_STOP
    assert draw_length(REACH)[0] == REACH_STOP
    assert draw_length(Draw((7, 7, 7), Kind.SUPER, True, 1))[0] == SUPER_REACH_STOP
    assert draw_length(RUSH)[1] > draw_length(Draw((4, 4, 4), Kind.ATARI, True, 1))[1]


def test_la_pantalla_juega_la_reserva_en_orden_sin_solaparse() -> None:
    volley = volley_of(CLASSIC, 3, 0, [REACH, MISS, RUSH])
    motion = motion_for(volley)
    timeline = build_timeline(volley, motion)
    assert timeline.landings == tuple(ball.landing for ball in motion.balls)
    arrivals = sorted(timeline.landings[:3])  # las tres bolas de START salen las primeras
    first, second, third = timeline.slots
    assert [first.queued, second.queued, third.queued] == arrivals
    assert first.start == first.queued
    assert second.start == max(second.queued, first.end)  # espera en la reserva si hace falta
    assert third.start == max(third.queued, second.end)
    assert timeline.frames == max(third.end, max(timeline.landings) + POCKET_FLASH)


def test_la_reserva_sigue_el_orden_de_llegada_y_las_perdidas_son_las_ultimas() -> None:
    """Cada bola tarda lo que dura su caída: la primera en salir puede ser la última en llegar."""
    start = CLASSIC.start_pocket
    slow, fast = slowest_and_fastest(CLASSIC, start)
    falls = library(CLASSIC)[start]
    assert falls[slow].frames - falls[fast].frames > 3 * LAUNCH_GAP
    # Seis bolas por START: se lanzan lentas primero, así que llegan al revés.
    balls = [ball_in(CLASSIC, start, slow)] * 3 + [ball_in(CLASSIC, start, fast)] * 3
    balls += [ball_in(CLASSIC, 0)] * 4
    volley = build_volley(CLASSIC, balls, lambda: MISS)
    motion = motion_for(volley)
    timeline = build_timeline(volley, motion)
    expected = [ball.landing for ball in motion.balls]
    assert list(timeline.landings) == expected
    assert expected[0] > expected[3]  # la primera en salir llega después de la cuarta
    arrivals = sorted(expected[:6])
    assert [slot.queued for slot in timeline.slots] == arrivals[:4]
    assert list(timeline.wasted_at) == arrivals[4:]
    assert volley.wasted == 2
    assert timeline.frames >= max(expected)


def test_el_contador_del_rush_llega_a_todos_los_premios() -> None:
    slot = build_timeline(volley_of(CLASSIC, 1, 3, [RUSH])).slots[0]
    assert slot.jackpots_at(slot.center_stop - 1) == 0
    assert slot.jackpots_at(slot.center_stop) == 1
    assert slot.jackpots_at(slot.end - 1) == RUSH.jackpots


@pytest.mark.parametrize("board", ALL_BOARDS, ids=lambda b: b.key)
def test_el_gif_acaba_en_la_misma_imagen_que_el_png(
    renderer: PachinkoRenderer, board: Board
) -> None:
    media = renderer.render(volley_of(board, 2, 1, [REACH, RUSH]))
    gif = Image.open(io.BytesIO(media.gif))
    gif.seek(gif.n_frames - 1)
    last = gif.convert("RGB")
    png = Image.open(io.BytesIO(media.png)).convert("RGB")
    assert last.tobytes() == png.tobytes()
    assert media.seconds > 0


@pytest.mark.lenta
def test_la_misma_tanda_se_dibuja_siempre_igual_y_otra_caida_se_ve_distinta(
    renderer: PachinkoRenderer,
) -> None:
    volley = volley_of(CLASSIC, 2, 1, [REACH, RUSH])
    assert renderer.render(volley).gif == renderer.render(volley).gif
    slow, fast = slowest_and_fastest(CLASSIC, 1)
    other = build_volley(CLASSIC, [ball_in(CLASSIC, 1, fast)] * 10, lambda: REACH)
    same = build_volley(CLASSIC, [ball_in(CLASSIC, 1, slow)] * 10, lambda: REACH)
    assert renderer.render(other).gif != renderer.render(same).gif


def test_cada_tablero_se_ve_distinto(renderer: PachinkoRenderer) -> None:
    images = {renderer.idle_png(board) for board in ALL_BOARDS}
    assert len(images) == len(ALL_BOARDS)


def test_el_turbo_no_hace_gif(renderer: PachinkoRenderer) -> None:
    media = renderer.render(volley_of(CLASSIC, 0, 3, []), turbo=True)
    assert media.gif == b""
    assert media.png.startswith(b"\x89PNG")


def test_el_gif_no_se_dispara_de_tamano(renderer: PachinkoRenderer) -> None:
    """Un super rush largo en Oni con la reserva llena es de lo más largo que sale."""
    draws = [RUSH, REACH, REACH, Draw((7, 7, 7), Kind.SUPER, True, 12)]
    media = renderer.render(volley_of(ONI, 4, 0, draws))
    assert len(media.gif) < 1_200_000


def test_la_maquina_parada_es_un_png(renderer: PachinkoRenderer) -> None:
    renderer.warm_up()
    for board in ALL_BOARDS:
        assert renderer.idle_png(board).startswith(b"\x89PNG")
    assert renderer.still_png(volley_of(ONI, 0, 3, [])).startswith(b"\x89PNG")


# -- Piezas pintadas con canvas ---------------------------------------------------------


@pytest.mark.parametrize("board", ALL_BOARDS, ids=lambda b: b.key)
def test_las_piezas_existen_y_miden_lo_que_deben(board: Board) -> None:
    layout = layout_for(board)
    sizes = expected_sizes(len(bulb_palette(THEMES[board.key])), board.rows + 1, layout.dx)
    assert sizes
    for name, size in sizes.items():
        with Image.open(PIECES_DIR / board.key / name) as image:
            assert image.size == size, name
            assert image.mode == "RGBA", name


@pytest.mark.parametrize("board", ALL_BOARDS, ids=lambda b: b.key)
def test_las_piezas_guardadas_estan_al_dia(board: Board) -> None:
    """Si falla, hay que regenerarlas con `python docs/pachinko_piezas.py`."""
    assert piece_problems(board) == []


def test_las_piezas_se_dan_por_viejas_si_cambia_algo_de_lo_que_se_pinto(tmp_path: Path) -> None:
    folder = tmp_path / CLASSIC.key
    shutil.copytree(PIECES_DIR / CLASSIC.key, folder)
    assert piece_problems(CLASSIC, tmp_path) == []
    manifest = folder / "piezas.json"
    manifest.write_text(manifest.read_text().replace('"hash": "', '"hash": "0'), encoding="utf-8")
    assert "cambiado" in piece_problems(CLASSIC, tmp_path)[0]
    (folder / "bola.png").unlink()
    assert "falta bola.png" in piece_problems(CLASSIC, tmp_path)


def test_el_render_usa_los_png_del_tablero(renderer: PachinkoRenderer) -> None:
    assets = renderer.assets(CLASSIC)
    with Image.open(PIECES_DIR / CLASSIC.key / "fondo.png") as fondo:
        assert assets.base.tobytes() == fondo.convert("RGB").tobytes()
    assert assets.lit_strip is not None
    assert assets.bulbs is not None


@pytest.mark.lenta
def test_sin_piezas_el_render_sigue_funcionando_con_pillow_y_avisa_una_vez(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Se borra una pieza suelta (la bola) y, aparte, un tablero entero (Oni)."""
    shutil.copytree(PIECES_DIR / CLASSIC.key, tmp_path / CLASSIC.key)
    (tmp_path / CLASSIC.key / "bola.png").unlink()
    (tmp_path / CLASSIC.key / "fondo.png").write_bytes(b"no soy un PNG")
    renderer = PachinkoRenderer(pieces_dir=tmp_path)
    with caplog.at_level(logging.WARNING, logger="bot.services.pachinko_pieces"):
        for board in (CLASSIC, ONI):
            media = renderer.render(volley_of(board, 2, 1, [REACH, RUSH]))
            assert media.gif.startswith(b"GIF8")
            assert media.png.startswith(b"\x89PNG")
            renderer.render(volley_of(board, 2, 1, [REACH, RUSH]))
    assets = renderer.assets(CLASSIC)
    assert assets.bulbs is not None  # lo que sí estaba, se usa
    assert renderer.assets(ONI).bulbs is None and renderer.assets(ONI).lit_full is not None
    warnings = [r for r in caplog.records if "Pillow" in r.getMessage()]
    assert len(warnings) == 2  # una por tablero, aunque se dibuje más de una tanda
    assert "bola.png" in warnings[0].getMessage()


def test_el_dibujo_con_pillow_acaba_igual_en_gif_y_png(tmp_path: Path) -> None:
    renderer = PachinkoRenderer(pieces_dir=tmp_path)  # carpeta vacía: todo con Pillow
    media = renderer.render(volley_of(CLASSIC, 2, 1, [REACH, RUSH]))
    gif = Image.open(io.BytesIO(media.gif))
    gif.seek(gif.n_frames - 1)
    png = Image.open(io.BytesIO(media.png)).convert("RGB")
    assert gif.convert("RGB").tobytes() == png.tobytes()
    clavo = geometry_for(CLASSIC).pins[0]
    assert renderer.assets(CLASSIC).base.getpixel((round(clavo[0]), round(clavo[1]))) == PIN
