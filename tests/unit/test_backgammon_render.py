"""Pruebas de bot.services.backgammon_render: fotogramas de cada animación y dibujo de reserva.

Python decide lo que se ve en cada fotograma (el vuelo de cada ficha, cómo ruedan los
dados, el giro del cubo, el cartel); aquí se comprueban esos estados sin dibujar nada,
y el dibujo de Pillow solo se mira por fuera (tamaño, que el GIF se juega una vez, que
cada estado se pinta distinto). La escena de canvas tiene sus propias pruebas
(`test_backgammon_scene.py`).
"""

from __future__ import annotations

import io
import math

import pytest
from PIL import Image, ImageChops

from bot.services.backgammon import (
    BAR,
    OFF,
    Position,
    Side,
    Step,
    dice_of,
    legal_plays,
)
from bot.services.backgammon_render import (
    FRAME_MS,
    GEOMETRY,
    LAND_MS,
    OPENING_PAUSE_MS,
    ROLL_FRAMES,
    BackgammonRenderer,
    Cube,
    H,
    Media,
    Player,
    W,
    banner,
    bar_xy,
    board_state,
    canvas_text,
    chain,
    cube_steps,
    cube_xy,
    end_steps,
    half_center,
    meta_state,
    move_steps,
    off_xy,
    opening_steps,
    point_x,
    rest_dice,
    roll_steps,
    stack_xy,
)

INITIAL = Position.initial()
META = meta_state(
    Player("Diego", "Fichas blancas"),
    Player("Jovani el implacable de Moncloa", "Nivel difícil"),
    title="BACKGAMMON",
    subtitle="Mesa de Yapland",
    seed=7,
)


def position_with(
    points: dict[int, int],
    bar: tuple[int, int] = (0, 0),
    off: tuple[int, int] = (0, 0),
) -> Position:
    """Una posición suelta: `points` da las fichas de cada casilla (negativo, negras)."""
    values = [0] * 24
    for index, value in points.items():
        values[index] = value
    return Position(tuple(values), bar, off)


#: Las blancas pueden comer la ficha negra sola del punto 9 (índice) con un 3.
BLOT = position_with({12: 5, 5: 3, 23: 2, 9: -1, 16: -3, 0: -2, 18: -4})
#: Las blancas lo tienen todo en casa: con un 5 sacan del punto 5.
HOME = position_with({0: 3, 1: 3, 2: 3, 3: 3, 4: 3, 20: -4, 21: -4, 22: -4, 23: -3})
#: Una ficha blanca en la barra y la entrada abierta.
ON_BAR = position_with({12: 5, 7: 3, 5: 5, 23: 1, 16: -3, 11: -5, 18: -5}, bar=(1, 0))


def play_of(position: Position, side: Side, roll: tuple[int, int], wanted) -> tuple[Step, ...]:
    """La primera jugada legal con `roll` que cumple `wanted`."""
    for play in legal_plays(position, side, dice_of(roll)):
        if wanted(play):
            return play
    raise AssertionError("No hay jugada con esa condición")


def after(position: Position, side: Side, steps) -> Position:
    for step in steps:
        position = position.apply(side, step)
    return position


def flights(steps: list[tuple[dict, int]]) -> list[dict]:
    return [state["flying"][0] for state, _ms in steps if state["flying"]]


def total_ms(steps: list[tuple[dict, int]]) -> int:
    return sum(ms for _state, ms in steps)


def stack_total(state: dict) -> int:
    """Las fichas que hay en el tablero, barra y bandeja de un estado."""
    return sum(abs(v) for v in state["points"]) + sum(state["bar"]) + sum(state["off"])


# -- La geometría --------------------------------------------------------------------------------


def test_los_puntos_siguen_la_disposicion_estandar_vista_desde_las_blancas() -> None:
    bottom = [point_x(i) for i in range(12)]
    top = [point_x(i) for i in range(12, 24)]
    # Abajo, de derecha a izquierda 0..5 y luego, tras la barra, 6..11.
    assert bottom[:6] == sorted(bottom[:6], reverse=True)
    assert bottom[6:] == sorted(bottom[6:], reverse=True)
    assert min(bottom[:6]) > GEOMETRY["bar_x"] > max(bottom[6:])
    # Arriba, de izquierda a derecha 12..17 y, tras la barra, 18..23.
    assert top[:6] == sorted(top[:6]) and top[6:] == sorted(top[6:])
    assert max(top[:6]) < GEOMETRY["bar_x"] < min(top[6:])
    # Cada punto de arriba está enfrente del de abajo que le corresponde.
    assert point_x(0) == point_x(23) and point_x(11) == point_x(12)


@pytest.mark.parametrize("count", [1, 5, 6, 10, 15])
def test_un_monton_cabe_en_su_punto_y_se_comprime_a_partir_de_seis(count: int) -> None:
    for index in (0, 11, 12, 23):
        ys = [stack_xy(index, k, count)[1] for k in range(count)]
        low, high = GEOMETRY["top"], GEOMETRY["bottom"]
        tip = GEOMETRY["length"]
        assert all(low <= y <= high for y in ys)
        # Como mucho llega a donde llegaría uno de cinco a paso completo.
        reach = abs(ys[-1] - ys[0]) + GEOMETRY["r"]
        assert reach <= 4 * GEOMETRY["step"] + GEOMETRY["r"] + 0.01 and reach < tip
        gaps = {round(abs(b - a), 3) for a, b in zip(ys, ys[1:], strict=False)}
        if count <= 5:
            assert gaps <= {float(GEOMETRY["step"])}
    # Los de abajo crecen hacia arriba y los de arriba, hacia abajo.
    assert stack_xy(3, 4, 5)[1] < stack_xy(3, 0, 5)[1]
    assert stack_xy(15, 4, 5)[1] > stack_xy(15, 0, 5)[1]


def test_las_fichas_de_la_barra_se_apilan_del_centro_hacia_su_lado() -> None:
    white = [bar_xy(Side.WHITE, k, 3)[1] for k in range(3)]
    black = [bar_xy(Side.BLACK, k, 3)[1] for k in range(3)]
    assert white == sorted(white) and all(y > GEOMETRY["mid"] for y in white)
    assert black == sorted(black, reverse=True) and all(y < GEOMETRY["mid"] for y in black)
    # Con muchas se comprimen sin salirse del tapete.
    crowded = [bar_xy(Side.WHITE, k, 9)[1] for k in range(9)]
    assert max(crowded) < GEOMETRY["felt"][3] - GEOMETRY["r"] - GEOMETRY["cube"] / 2 + 6


def test_las_fichas_sacadas_se_apilan_de_canto_en_la_bandeja_de_su_lado() -> None:
    top, bottom = GEOMETRY["tray"][1], GEOMETRY["tray"][3]
    white = [off_xy(Side.WHITE, k)[1] for k in range(15)]
    black = [off_xy(Side.BLACK, k)[1] for k in range(15)]
    assert white == sorted(white, reverse=True) and black == sorted(black)
    assert all(GEOMETRY["mid"] < y < bottom for y in white)
    assert all(top < y < GEOMETRY["mid"] for y in black)


def test_los_dados_caen_a_la_derecha_para_las_blancas_y_a_la_izquierda_para_las_negras() -> None:
    assert half_center(Side.WHITE) > GEOMETRY["bar_x"] > half_center(Side.BLACK)
    white = rest_dice(Side.WHITE, (3, 1))
    black = rest_dice(Side.BLACK, (3, 1))
    assert all(d["x"] > GEOMETRY["bar"][1] for d in white)
    assert all(d["x"] < GEOMETRY["bar"][0] for d in black)


def test_el_cubo_esta_en_medio_de_la_barra_o_pegado_a_la_mitad_de_su_dueno() -> None:
    assert cube_xy(Cube()) == (GEOMETRY["bar_x"], GEOMETRY["mid"])
    assert cube_xy(Cube(2, None, True)) == cube_xy(Cube())
    assert cube_xy(Cube(2, Side.WHITE, True)) == cube_xy(Cube())  # ofrecido: en el centro
    white = cube_xy(Cube(2, Side.WHITE))
    black = cube_xy(Cube(2, Side.BLACK))
    assert white[0] > GEOMETRY["bar"][1] > GEOMETRY["bar"][0] > black[0]
    assert white[1] == black[1] == GEOMETRY["mid"]


# -- Lo fijo de la partida -----------------------------------------------------------------------


def test_los_nombres_van_sin_emojis_y_la_geometria_en_meta() -> None:
    meta = meta_state(
        Player("Diego 🎲", "Fichas ⚪ blancas"),
        Player("Jovani 🤖", ""),
        title="T",
        subtitle="S",
        seed=3,
    )
    assert meta["white"] == {
        "name": canvas_text("Diego 🎲"),
        "tag": canvas_text("Fichas ⚪ blancas"),
    }
    assert meta["white"]["name"] == "Diego" and meta["black"]["name"] == "Jovani"
    assert meta["black"]["tag"] == "?"
    assert meta["geom"] == GEOMETRY and meta["geom"] is not GEOMETRY
    assert (meta["title"], meta["subtitle"], meta["seed"]) == ("T", "S", 3)
    assert (W, H) == (GEOMETRY["w"], GEOMETRY["h"])


def test_la_skin_va_tal_cual_en_meta_y_puede_faltar() -> None:
    assert META["skin"] is None
    skin = {"key": "oro", "felt": ["#fff", "#aaa", "#333"], "pattern": "oro"}
    meta = meta_state(Player("A"), Player("B"), title="T", subtitle="S", seed=1, skin=skin)
    assert meta["skin"] == skin


# -- Un fotograma quieto -------------------------------------------------------------------------


def test_un_estado_lleva_la_posicion_y_las_cuentas_de_pips_de_los_dos() -> None:
    state = board_state(INITIAL, turn=Side.BLACK)
    assert state["points"] == list(INITIAL.points)
    assert state["bar"] == [0, 0] and state["off"] == [0, 0]
    assert state["pips"] == [167, 167]
    assert state["turn"] == Side.BLACK.value
    assert state["labels"] == Side.WHITE.value
    assert board_state(INITIAL)["turn"] == 0
    assert board_state(INITIAL, labels=Side.BLACK)["labels"] == Side.BLACK.value
    crowded = position_with({5: 2}, bar=(1, 0), off=(12, 0))
    state = board_state(crowded)
    assert state["bar"] == [1, 0] and state["off"] == [12, 0]
    assert state["pips"] == [2 * 6 + 25, 0]


def test_las_marcas_son_casillas_validas_sin_repetir_y_ordenadas() -> None:
    state = board_state(INITIAL, marks=[OFF, 3, BAR, 3, 7])
    assert state["marks"] == [3, 7, BAR, OFF]
    assert board_state(INITIAL)["marks"] == []
    assert board_state(INITIAL, marks=[-1, 26])["marks"] == []


def test_el_cubo_con_valor_uno_ensena_64_y_ofrecido_se_ilumina() -> None:
    assert board_state(INITIAL)["cube"]["v"] == 64
    assert board_state(INITIAL, cube=Cube(4, Side.WHITE))["cube"]["v"] == 4
    assert board_state(INITIAL, cube=Cube(4, Side.WHITE))["cube"]["owner"] == Side.WHITE.value
    plain = board_state(INITIAL, cube=Cube(2, Side.BLACK))["cube"]
    offered = board_state(INITIAL, cube=Cube(2, None, True))["cube"]
    assert plain["glow"] == 0 and offered["glow"] == 1
    assert offered["scale"] > plain["scale"]
    assert (offered["x"], offered["y"]) == (GEOMETRY["bar_x"], GEOMETRY["mid"])


def test_el_cartel_lleva_su_altura_y_el_confeti_el_color_del_ganador() -> None:
    end = banner("¡GANAS!", "Bien", kind="win")
    state = board_state(INITIAL, end=end, confetti_t=0.5, winner=Side.BLACK)
    assert state["banner"]["title"] == "¡GANAS!" and state["banner"]["y"] == GEOMETRY["banner_y"]
    assert state["confetti"] == {"t": 0.5, "side": Side.BLACK.value}
    won = position_with({}, off=(15, 3))
    assert board_state(won, confetti_t=1)["confetti"]["side"] == Side.WHITE.value
    assert board_state(INITIAL, confetti_t=1)["confetti"]["side"] == 0
    assert board_state(INITIAL)["confetti"] is None and board_state(INITIAL)["banner"] is None


# -- Los dados quietos ---------------------------------------------------------------------------


def test_los_dados_quietos_ensenan_la_tirada_y_los_gastados_se_apagan() -> None:
    dice = rest_dice(Side.WHITE, (5, 2))
    assert [d["v"] for d in dice] == [5, 2] and not any(d["used"] for d in dice)
    assert all(d["side"] == Side.WHITE.value and d["z"] == 0 for d in dice)
    spent = rest_dice(Side.BLACK, (5, 2), used=1)
    assert [d["used"] for d in spent] == [True, False]
    assert all(d["side"] == Side.BLACK.value for d in spent)


def test_un_doble_son_cuatro_dados_en_fila() -> None:
    dice = rest_dice(Side.WHITE, (4, 4), used=3)
    assert [d["v"] for d in dice] == [4, 4, 4, 4]
    assert [d["used"] for d in dice] == [True, True, True, False]
    xs = [d["x"] for d in dice]
    assert xs == sorted(xs) and len({round(x) for x in xs}) == 4
    assert all(a + 40 < b for a, b in zip(xs, xs[1:], strict=False))  # no se pisan


# -- Tirada de dados -----------------------------------------------------------------------------


def test_los_dados_acaban_quietos_en_su_mitad_ensenando_la_tirada() -> None:
    steps, final = roll_steps(INITIAL, Side.WHITE, (5, 3), seed=11)
    assert final["dice"] == rest_dice(Side.WHITE, (5, 3))
    assert [d["v"] for d in final["dice"]] == [5, 3]
    assert final["turn"] == Side.WHITE.value and final["flying"] is None
    assert final["points"] == list(INITIAL.points)
    assert len(steps) == ROLL_FRAMES - 1 and all(ms == FRAME_MS for _s, ms in steps)
    # Al final del recorrido ya casi están donde se paran.
    last = steps[-1][0]["dice"]
    for moving, rest in zip(last, final["dice"], strict=True):
        assert moving["z"] == 0 and moving["v"] == rest["v"]
        assert math.hypot(moving["x"] - rest["x"], moving["y"] - rest["y"]) < 14
        # En 3D ya enseña su cara y casi no gira: el dado no salta al pararse.
        assert max(abs(p - q) for p, q in zip(moving["m"], rest["m"], strict=True)) < 0.1


def test_los_dados_entran_desde_fuera_por_su_lado_y_botan_en_el_aire() -> None:
    for side in (Side.WHITE, Side.BLACK):
        steps, _final = roll_steps(INITIAL, side, (6, 2), seed=4)
        first = steps[0][0]["dice"]
        assert all(d["side"] == side.value for d in first)
        # Entran por el borde de su lado: las blancas por la derecha y las negras por la izquierda.
        far = GEOMETRY["w"] if side is Side.WHITE else 0
        assert all(abs(d["x"] - far) < 150 for d in first)
        assert all(abs(d["x"] - half_center(side)) > 150 for d in first)
        airborne = [d["z"] for state, _ms in steps for d in state["dice"]]
        assert max(airborne) == pytest.approx(1.0, abs=0.05) and min(airborne) == 0
        # Dan vueltas: la cara cambia por el camino y el dado se aplasta al girar.
        faces = {d["v"] for state, _ms in steps for d in state["dice"]}
        assert len(faces) > 2
        assert min(d["sx"] for state, _ms in steps for d in state["dice"]) < 0.6


def test_la_misma_semilla_pinta_la_misma_tirada_y_otra_semilla_otra() -> None:
    same = roll_steps(INITIAL, Side.WHITE, (3, 1), seed=5)
    again = roll_steps(INITIAL, Side.WHITE, (3, 1), seed=5)
    other = roll_steps(INITIAL, Side.WHITE, (3, 1), seed=6)
    assert same == again
    assert same[0] != other[0]
    assert same[1] == other[1]  # el reposo no depende de la semilla


def test_un_doble_rueda_con_cuatro_dados() -> None:
    steps, final = roll_steps(INITIAL, Side.BLACK, (2, 2), seed=1)
    assert all(len(state["dice"]) == 4 for state, _ms in steps)
    assert [d["v"] for d in final["dice"]] == [2, 2, 2, 2]


def test_la_tirada_lleva_el_cubo_y_los_numeros_que_se_le_pasan() -> None:
    cube = Cube(2, Side.WHITE)
    steps, final = roll_steps(INITIAL, Side.BLACK, (3, 1), seed=2, cube=cube, labels=Side.BLACK)
    for state in [s for s, _ms in steps] + [final]:
        assert state["cube"]["v"] == 2 and state["labels"] == Side.BLACK.value
        assert state["turn"] == Side.BLACK.value


# -- Salida --------------------------------------------------------------------------------------


def test_la_salida_tira_un_dado_por_color_en_su_mitad_y_los_junta_en_la_de_quien_sale() -> None:
    steps, final = opening_steps(INITIAL, (2, 5), seed=3)
    pause = [(state, ms) for state, ms in steps if ms == OPENING_PAUSE_MS]
    assert len(pause) == 1
    apart, _ms = pause[0]
    white, black = apart["dice"]
    assert (white["side"], black["side"]) == (Side.WHITE.value, Side.BLACK.value)
    assert (white["v"], black["v"]) == (2, 5)
    assert white["x"] == half_center(Side.WHITE) and black["x"] == half_center(Side.BLACK)
    assert apart["turn"] == 0
    # Saca más el de las negras (5 contra 2): los dos acaban en la mitad izquierda.
    assert final["turn"] == Side.BLACK.value
    assert all(abs(d["x"] - half_center(Side.BLACK)) < 30 for d in final["dice"])
    assert [d["v"] for d in final["dice"]] == [2, 5]
    assert {d["side"] for d in final["dice"]} == {Side.WHITE.value, Side.BLACK.value}


def test_en_la_salida_sale_el_dado_mayor() -> None:
    _steps, white_starts = opening_steps(INITIAL, (6, 1), seed=3)
    _steps, black_starts = opening_steps(INITIAL, (1, 6), seed=3)
    assert white_starts["turn"] == Side.WHITE.value
    assert all(d["x"] > GEOMETRY["bar"][1] for d in white_starts["dice"])
    assert black_starts["turn"] == Side.BLACK.value
    assert opening_steps(INITIAL, (6, 1), seed=3) == opening_steps(INITIAL, (6, 1), seed=3)


# -- Mover fichas --------------------------------------------------------------------------------


def test_el_final_de_una_jugada_cuadra_con_aplicar_los_pasos_a_la_posicion() -> None:
    roll = (3, 3)
    play = play_of(INITIAL, Side.WHITE, roll, lambda p: len(p) == 4)
    _steps, final = move_steps(INITIAL, Side.WHITE, play, roll)
    expected = after(INITIAL, Side.WHITE, play)
    assert final == board_state(
        expected,
        turn=Side.BLACK,
        dice=final["dice"],
        marks={s for step in play for s in (step.source, step.target)},
    )
    assert final["points"] == list(expected.points) and final["pips"] == [
        expected.pip(Side.WHITE),
        expected.pip(Side.BLACK),
    ]
    assert stack_total(final) == stack_total(board_state(INITIAL))


def test_cada_paso_levanta_la_ficha_de_arriba_la_lleva_en_arco_y_la_posa_en_su_montón() -> None:
    step = Step(12, 7, 5)
    steps, final = move_steps(INITIAL, Side.WHITE, [step], (5, 3))
    in_air = flights(steps)
    assert in_air and all(f["side"] == Side.WHITE.value for f in in_air)
    # Sale de arriba del montón de origen (la quinta de cinco) y acaba arriba del de destino.
    assert (in_air[0]["x"], in_air[0]["y"]) == stack_xy(12, 4, 5)
    assert (in_air[-1]["x"], in_air[-1]["y"]) == stack_xy(7, 3, 4)
    assert final["points"][12] == 4 and final["points"][7] == 4
    heights = [f["h"] for f in in_air]
    peak = heights.index(max(heights))
    assert 0 < peak < len(heights) - 1 and heights[-1] < max(heights)
    assert heights[: peak + 1] == sorted(heights[: peak + 1])  # sube hasta lo más alto del arco
    assert max(heights) > heights[0] > 0  # se levanta antes de salir
    # Mientras vuela, el montón de origen ya no la tiene y el de destino aún no.
    for state, _ms in steps:
        if state["flying"]:
            assert state["points"][12] == 4 and state["points"][7] == 3
            assert state["pips"] == [167, 167]  # los pips cambian al posarse, no antes
    # De izquierda a derecha, del punto 13 al 8 (abajo): la sombra avanza sin volver atrás.
    xs = [f["x"] for f in in_air]
    assert xs == sorted(xs)


def test_al_posarse_la_ficha_se_apaga_el_dado_gastado_y_cambian_los_pips() -> None:
    steps, final = move_steps(INITIAL, Side.WHITE, [Step(12, 7, 5), Step(7, 4, 3)], (5, 3))
    landed = [(state, ms) for state, ms in steps if ms == LAND_MS]
    assert len(landed) == 2
    first, second = landed[0][0], landed[1][0]
    assert first["flying"] is None
    assert [d["used"] for d in first["dice"]] == [True, False]
    assert [d["used"] for d in second["dice"]] == [True, True]
    assert first["pips"] == [167 - 5, 167]
    assert second["pips"] == [167 - 8, 167]
    assert final["dice"] == second["dice"]
    # Antes de posarse, el dado sigue encendido.
    before = [state for state, ms in steps if state["flying"]][0]
    assert not any(d["used"] for d in before["dice"])


def test_se_apaga_el_dado_que_vale_lo_que_se_gasta_y_no_siempre_el_primero() -> None:
    # Con el 3 primero: se apaga el segundo dado (el 3), no el primero.
    play = play_of(INITIAL, Side.WHITE, (5, 3), lambda p: p[0].die == 3)
    _steps, final = move_steps(INITIAL, Side.WHITE, play[:1], (5, 3))
    assert [d["used"] for d in final["dice"]] == [False, True]


def test_en_un_doble_cada_paso_gasta_un_dado_y_used_before_cuenta_los_ya_gastados() -> None:
    play = play_of(INITIAL, Side.WHITE, (3, 3), lambda p: len(p) == 4)
    steps, final = move_steps(INITIAL, Side.WHITE, play[:2], (3, 3), used_before=1)
    assert [d["used"] for d in final["dice"]] == [True, True, True, False]
    landed = [state for state, ms in steps if ms == LAND_MS]
    assert [sum(d["used"] for d in s["dice"]) for s in landed] == [2, 3]


def test_comer_manda_la_ficha_rival_a_la_barra_justo_despues() -> None:
    play = play_of(BLOT, Side.WHITE, (3, 1), lambda p: any(s.hit for s in p))
    hit = next(s for s in play if s.hit)
    steps, final = move_steps(BLOT, Side.WHITE, [hit], (3, 1))
    in_air = [(state, state["flying"][0]) for state, _ms in steps if state["flying"]]
    mine = [f for _s, f in in_air if f["side"] == Side.WHITE.value]
    theirs = [f for _s, f in in_air if f["side"] == Side.BLACK.value]
    assert mine and theirs
    # Primero vuela la mía y justo después, la rival, desde el punto hasta la barra.
    order = [f["side"] for _s, f in in_air]
    assert order == sorted(order)
    assert (theirs[0]["x"], theirs[0]["y"]) == stack_xy(hit.target, 0, 1)
    assert (theirs[-1]["x"], theirs[-1]["y"]) == bar_xy(Side.BLACK, 0, 1)
    # Mientras la rival vuela, ya no está en el punto ni en la barra todavía.
    for state, fly in in_air:
        if fly["side"] == Side.BLACK.value:
            assert state["points"][hit.target] == 1 and state["bar"] == [0, 0]
    assert final["bar"] == [0, 1] and final["points"][hit.target] == 1
    assert BAR in final["marks"] and hit.target in final["marks"]
    assert stack_total(final) == stack_total(board_state(BLOT))


def test_una_ficha_entra_desde_la_barra_y_sale_de_arriba_de_las_que_haya_en_ella() -> None:
    play = play_of(ON_BAR, Side.WHITE, (4, 2), lambda p: p[0].source == BAR)
    steps, final = move_steps(ON_BAR, Side.WHITE, play[:1], (4, 2))
    in_air = flights(steps)
    assert (in_air[0]["x"], in_air[0]["y"]) == bar_xy(Side.WHITE, 0, 1)
    assert (in_air[-1]["x"], in_air[-1]["y"]) == stack_xy(play[0].target, 0, 1)
    assert final["bar"] == [0, 0] and final["points"][play[0].target] == 1
    # Con dos en la barra sale la de arriba (la última del montón).
    two = position_with({12: 5, 5: 5, 23: 1, 11: -5}, bar=(2, 0))
    entry = play_of(two, Side.WHITE, (4, 2), lambda p: p[0].source == BAR)[0]
    more, _final = move_steps(two, Side.WHITE, [entry], (4, 2))
    assert (flights(more)[0]["x"], flights(more)[0]["y"]) == bar_xy(Side.WHITE, 1, 2)


def test_sacar_una_ficha_la_lleva_a_la_bandeja_de_su_lado() -> None:
    play = play_of(HOME, Side.WHITE, (5, 2), lambda p: any(s.target == OFF for s in p))
    out = next(s for s in play if s.target == OFF)
    steps, final = move_steps(HOME, Side.WHITE, [out], (5, 2))
    in_air = flights(steps)
    assert (in_air[-1]["x"], in_air[-1]["y"]) == off_xy(Side.WHITE, 0)
    assert final["off"] == [1, 0] and OFF in final["marks"]
    assert sum(final["points"][:6]) == 14
    # La siguiente se apila encima de la anterior.
    second_position = HOME.apply(Side.WHITE, out)
    again = play_of(second_position, Side.WHITE, (5, 2), lambda p: any(s.target == OFF for s in p))
    next_out = next(s for s in again if s.target == OFF)
    more, _final = move_steps(second_position, Side.WHITE, [next_out], (5, 2))
    assert flights(more)[-1]["y"] == off_xy(Side.WHITE, 1)[1] < off_xy(Side.WHITE, 0)[1]


def test_una_ficha_negra_sale_a_la_bandeja_de_arriba() -> None:
    position = position_with({20: -3, 21: -3, 22: -3, 23: -3, 19: -3, 0: 5, 1: 4, 2: 6})
    play = play_of(position, Side.BLACK, (6, 2), lambda p: any(s.target == OFF for s in p))
    out = next(s for s in play if s.target == OFF)
    steps, final = move_steps(position, Side.BLACK, [out], (6, 2))
    assert flights(steps)[-1]["y"] < GEOMETRY["mid"]
    assert final["off"] == [0, 1]


def test_una_jugada_sin_pasos_solo_cambia_el_turno() -> None:
    steps, final = move_steps(INITIAL, Side.WHITE, [], (6, 6))
    assert steps == []
    assert final["turn"] == Side.BLACK.value and final["points"] == list(INITIAL.points)
    assert final["marks"] == [] and not any(d["used"] for d in final["dice"])


def test_un_paso_ilegal_no_se_anima() -> None:
    with pytest.raises(ValueError):
        move_steps(INITIAL, Side.WHITE, [Step(12, 5, 6)], (6, 2))


def test_con_cartel_la_jugada_acaba_con_el_cartel_el_confeti_y_nadie_con_el_turno() -> None:
    win = position_with({0: 1}, off=(14, 3))
    end = banner("¡GANAS!", "Has sacado las 15 fichas", kind="win")
    steps, final = move_steps(
        win, Side.WHITE, [Step(0, OFF, 1)], (1, 2), end=end, winner=Side.WHITE
    )
    assert final["banner"]["a"] == 1.0 and final["banner"]["title"] == "¡GANAS!"
    assert final["confetti"] is None and final["turn"] == 0
    assert final["off"] == [15, 3]
    confetti = [s["confetti"] for s, _ms in steps if s["confetti"]]
    assert confetti and confetti == sorted(confetti, key=lambda c: c["t"])
    assert confetti[0]["side"] == Side.WHITE.value and confetti[-1]["t"] == 1
    alphas = [s["banner"]["a"] for s, _ms in steps if s["banner"]]
    assert alphas == sorted(alphas) and alphas[-1] == 1.0
    # Sin ganador (rendirse, dormirse) hay cartel y no hay confeti.
    quiet, quiet_final = move_steps(
        win, Side.WHITE, [Step(0, OFF, 1)], (1, 2), end=banner("TIEMPO", kind="time")
    )
    assert not any(s["confetti"] for s, _ms in quiet) and quiet_final["banner"]["kind"] == "time"


def test_sin_cartel_el_turno_pasa_al_rival_y_no_hay_cartel() -> None:
    _steps, final = move_steps(INITIAL, Side.BLACK, [], (6, 6))
    assert final["turn"] == Side.WHITE.value and final["banner"] is None


def test_la_jugada_lleva_el_cubo_y_los_numeros_en_todos_sus_fotogramas() -> None:
    cube = Cube(4, Side.BLACK)
    steps, final = move_steps(
        INITIAL, Side.WHITE, [Step(12, 7, 5)], (5, 3), cube=cube, labels=Side.BLACK
    )
    for state in [s for s, _ms in steps] + [final]:
        assert state["cube"]["v"] == 4 and state["cube"]["owner"] == Side.BLACK.value
        assert state["labels"] == Side.BLACK.value


# -- El cubo -------------------------------------------------------------------------------------


def test_doblar_levanta_el_cubo_lo_gira_con_el_valor_nuevo_y_lo_lleva_a_quien_acepta() -> None:
    before, after_cube = Cube(), Cube(2, Side.BLACK)
    steps, final = cube_steps(INITIAL, before, after_cube, turn=Side.WHITE)
    cubes = [state["cube"] for state, _ms in steps]
    assert cubes[0]["v"] == 64 and cubes[0]["lift"] < 1 and cubes[0]["x"] == GEOMETRY["bar_x"]
    # Gira: a mitad del giro cambia el valor que se ve, de 64 (el 1) a 2.
    shown = [c["v"] for c in cubes]
    assert shown[0] == 64 and shown[-1] == 2 and shown.index(2) > 0
    flips = [c["flip"] for c in cubes]
    assert max(flips) == pytest.approx(math.pi, abs=0.4) and flips[0] == 0
    # Y se desliza hasta el lado del que lo acepta, que es donde queda.
    assert final["cube"]["v"] == 2 and final["cube"]["owner"] == Side.BLACK.value
    assert (final["cube"]["x"], final["cube"]["y"]) == cube_xy(after_cube)
    xs = [c["x"] for c in cubes]
    assert xs == sorted(xs, reverse=True)
    assert final["turn"] == Side.WHITE.value and final["flying"] is None
    assert max(c["lift"] for c in cubes) == 1.0 and cubes[-1]["lift"] < 0.5


def test_el_cubo_ofrecido_se_queda_en_el_centro_iluminado() -> None:
    steps, final = cube_steps(INITIAL, Cube(), Cube(2, None, True), turn=Side.BLACK)
    assert final["cube"]["v"] == 2 and final["cube"]["glow"] == 1
    assert (final["cube"]["x"], final["cube"]["y"]) == (GEOMETRY["bar_x"], GEOMETRY["mid"])
    assert all(
        state["cube"]["x"] == GEOMETRY["bar_x"] and state["cube"]["y"] == GEOMETRY["mid"]
        for state, _ms in steps
    )
    glows = [state["cube"]["glow"] for state, _ms in steps]
    assert glows[0] == 0 and glows == sorted(glows) and glows[-1] <= 1


def test_aceptar_un_doblado_lleva_el_cubo_ofrecido_del_centro_a_su_dueno() -> None:
    offered, accepted = Cube(2, None, True), Cube(2, Side.WHITE)
    steps, final = cube_steps(INITIAL, offered, accepted)
    assert all(state["cube"]["v"] == 2 for state, _ms in steps)  # el valor no cambia: no gira
    assert all(state["cube"]["flip"] == 0 for state, _ms in steps)
    assert final["cube"]["x"] > GEOMETRY["bar"][1] and final["cube"]["glow"] == 0


# -- El cartel -----------------------------------------------------------------------------------


def test_el_cartel_aparece_poco_a_poco_y_con_ganador_hay_confeti() -> None:
    end = banner("TE RINDES", "Pierdes la partida", kind="lose")
    steps, final = end_steps(INITIAL, end=end)
    alphas = [state["banner"]["a"] for state, _ms in steps]
    assert alphas == sorted(alphas) and alphas[-1] == 1.0 and alphas[0] < 0.5
    assert final["banner"]["a"] == 1.0 and not any(s["confetti"] for s, _ms in steps)
    assert final["turn"] == 0 and final["points"] == list(INITIAL.points)
    won_steps, won_final = end_steps(
        INITIAL, end=banner("¡GANAS!", kind="win"), winner=Side.WHITE, cube=Cube(2, Side.WHITE)
    )
    assert len(won_steps) > len(steps)
    assert all(s["confetti"]["side"] == Side.WHITE.value for s, _ms in won_steps)
    assert won_final["confetti"] is None and won_final["cube"]["v"] == 2


# -- Juntar animaciones --------------------------------------------------------------------------


def test_chain_junta_las_animaciones_suma_duraciones_y_se_queda_con_el_ultimo_final() -> None:
    a = move_steps(INITIAL, Side.WHITE, [Step(12, 7, 5)], (5, 3))
    b = roll_steps(INITIAL, Side.BLACK, (6, 2), seed=1)
    c = cube_steps(INITIAL, Cube(), Cube(2, Side.WHITE))
    joined, final = chain(a, b, c)
    assert joined == a[0] + b[0] + c[0] and final == c[1]
    assert total_ms(joined) == total_ms(a[0]) + total_ms(b[0]) + total_ms(c[0])
    paused, _final = chain(a, b, c, pause_ms=300)
    assert total_ms(paused) == total_ms(joined) + 2 * 300
    # La pausa enseña quieto el final de la anterior.
    assert (a[1], 300) in paused and (b[1], 300) in paused
    assert chain(a) == a


def test_chain_sin_animaciones_falla() -> None:
    with pytest.raises(ValueError):
        chain()


def test_un_turno_entero_encadenado_conserva_las_fichas_y_acaba_en_el_estado_del_ultimo() -> None:
    play = play_of(BLOT, Side.WHITE, (3, 3), lambda p: any(s.hit for s in p))
    moved = after(BLOT, Side.WHITE, play)
    steps, final = chain(
        move_steps(BLOT, Side.WHITE, play, (3, 3)),
        roll_steps(moved, Side.BLACK, (6, 1), seed=2),
        pause_ms=200,
    )
    assert final["turn"] == Side.BLACK.value and final["points"] == list(moved.points)
    # En cada fotograma están todas las fichas: en el tablero o en el aire.
    for state, _ms in steps:
        assert stack_total(state) + len(state["flying"] or []) == stack_total(final)


# -- Pillow (reserva) ----------------------------------------------------------------------------


def png_size(png: bytes) -> tuple[int, int]:
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG"
    return image.size


def test_pillow_pinta_un_png_del_tamano_de_la_escena_y_el_gif_se_juega_una_vez() -> None:
    renderer = BackgammonRenderer()
    state = board_state(INITIAL, turn=Side.WHITE, dice=rest_dice(Side.WHITE, (3, 1)))
    assert png_size(renderer.still(META, state)) == (W, H)
    steps, final = move_steps(INITIAL, Side.WHITE, [Step(12, 7, 5)], (5, 3))
    media = renderer.animate(META, steps, final)
    assert isinstance(media, Media) and media.seconds == total_ms(steps) / 1000
    gif = Image.open(io.BytesIO(media.gif))
    assert gif.format == "GIF" and gif.size == (W, H) and gif.n_frames > 10
    assert gif.info.get("loop") is None  # no se repite
    assert png_size(media.png) == (W, H)


def test_pillow_pinta_distinto_cada_estado_que_se_ve() -> None:
    renderer = BackgammonRenderer()
    base = board_state(INITIAL, turn=Side.WHITE)
    variants = {
        "turno": board_state(INITIAL, turn=Side.BLACK),
        "dados": board_state(INITIAL, turn=Side.WHITE, dice=rest_dice(Side.WHITE, (6, 5))),
        "cubo": board_state(INITIAL, turn=Side.WHITE, cube=Cube(4, Side.WHITE)),
        "marcas": board_state(INITIAL, turn=Side.WHITE, marks=[5, 12]),
        "numeros": board_state(INITIAL, turn=Side.WHITE, labels=Side.BLACK),
        "vuelo": board_state(
            INITIAL, turn=Side.WHITE, flying=[{"x": 300, "y": 200, "h": 30, "side": 1}]
        ),
        "cartel": board_state(
            INITIAL, turn=Side.WHITE, end={**banner("FIN", "x", kind="win"), "a": 1.0}
        ),
        "barra": board_state(position_with({12: 4}, bar=(1, 1)), turn=Side.WHITE),
        "bandeja": board_state(position_with({12: 4}, off=(3, 2)), turn=Side.WHITE),
        "montón": board_state(position_with({12: 9}), turn=Side.WHITE),
    }
    reference = renderer.frame(META, base).convert("RGB")
    for name, state in variants.items():
        image = renderer.frame(META, state).convert("RGB")
        assert ImageChops.difference(reference, image).getbbox() is not None, name


def test_pillow_no_conoce_las_skins_y_pinta_siempre_el_tapete_de_serie() -> None:
    renderer = BackgammonRenderer()
    skin = {"key": "rojo", "felt": ["#aa2222", "#881111", "#440808"], "pattern": ""}
    red = meta_state(Player("A"), Player("B"), title="T", subtitle="S", seed=1, skin=skin)
    plain = meta_state(Player("A"), Player("B"), title="T", subtitle="S", seed=1)
    state = board_state(INITIAL)
    a, b = renderer.frame(red, state), renderer.frame(plain, state)
    assert ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox() is None
