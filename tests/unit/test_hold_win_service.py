"""Pruebas de bot.services.hold_win: ways, recogida, maletines, bonus y retorno."""

from __future__ import annotations

import bisect
import itertools
import random
from collections.abc import Sequence

import pytest

from bot.services import hold_win as hold_win_module
from bot.services.hold_win import (
    CASE_SIZE,
    CELLS,
    GRAND,
    MAJOR_BASE,
    MAJOR_MAX,
    MIN_STAKE,
    MINI_BASE,
    MINI_CHIP,
    MINI_MAX,
    RESET_VALUE,
    ROWS,
    THEMES,
    WAYS_PAYS,
    BonusGame,
    BonusKind,
    BonusStep,
    Case,
    Cell,
    Kind,
    Meters,
    Tier,
    Trigger,
    evaluate_base,
    fill_cases,
    format_multiplier,
    reset_won_jackpots,
    simulate,
    spin_base,
    to_amount,
)

PAY = [Cell(Kind.PAY, symbol=i) for i in range(6)]
WILD = Cell(Kind.WILD)
COLLECT = Cell(Kind.COLLECT)


def coin(value: int, tier: Tier = Tier.GREEN) -> Cell:
    return Cell(Kind.COIN, tier=tier, value=value)


def grid_with(cells: dict[tuple[int, int], Cell]) -> list[Cell]:
    """Rejilla con las casillas indicadas por (rodillo, fila) y fichas en el resto.

    Las fichas de bote no forman ways ni son monedas: no estorban.
    """
    filler = Cell(Kind.CHIP, symbol="mini")
    return [cells.get(divmod(index, ROWS), filler) for index in range(CELLS)]


# -- Unidades -----------------------------------------------------------------------


def test_los_puntos_se_pasan_a_yapdollars_redondeando_hacia_abajo() -> None:
    assert to_amount(100, 250) == 250
    assert to_amount(15, 10) == 1
    assert to_amount(10, 99) == 9


def test_formato_de_multiplicadores() -> None:
    assert format_multiplier(250) == "×2,5"
    assert format_multiplier(1_000) == "×10"
    assert format_multiplier(5) == "×0,05"


# -- Ways ---------------------------------------------------------------------------


def test_tres_rodillos_seguidos_pagan_por_cada_combinacion() -> None:
    cells = {(0, 0): PAY[5], (0, 1): PAY[5], (1, 2): PAY[5], (2, 3): PAY[5]}
    spin = evaluate_base(grid_with(cells))
    (win,) = [w for w in spin.wins if w.symbol == 5]
    assert win.reels == 3
    assert win.ways == 2
    assert win.points == 2 * WAYS_PAYS[5][0]


def test_el_comodin_completa_ways_en_los_rodillos_del_medio() -> None:
    cells = {(0, 0): PAY[4], (1, 0): WILD, (2, 0): PAY[4], (3, 1): WILD, (4, 2): PAY[4]}
    spin = evaluate_base(grid_with(cells))
    (win,) = [w for w in spin.wins if w.symbol == 4]
    assert win.reels == 5
    assert win.points == WAYS_PAYS[4][2]
    assert spin.wild_win


def test_un_hueco_corta_el_premio() -> None:
    cells = {(0, 0): PAY[5], (1, 0): PAY[5], (3, 0): PAY[5], (4, 0): PAY[5]}
    spin = evaluate_base(grid_with(cells))
    assert not [w for w in spin.wins if w.symbol == 5]


# -- Monedas y recogida --------------------------------------------------------------


def test_sin_recogedor_las_monedas_no_pagan() -> None:
    spin = evaluate_base(grid_with({(1, 0): coin(30), (3, 2): coin(200, Tier.RED)}))
    assert spin.collectors == 0
    assert spin.collect_points == 0
    assert spin.near_miss


def test_el_recogedor_del_rodillo_1_cobra_todas_las_monedas() -> None:
    cells = {(0, 3): COLLECT, (1, 0): coin(30), (3, 2): coin(200, Tier.RED)}
    spin = evaluate_base(grid_with(cells))
    assert spin.collectors == 1
    assert spin.collect_points == 230


def test_con_recogedor_en_los_dos_extremos_se_cobra_el_doble() -> None:
    cells = {(0, 0): COLLECT, (4, 1): COLLECT, (2, 0): coin(50)}
    spin = evaluate_base(grid_with(cells))
    assert spin.collectors == 2
    assert spin.collect_points == 100


def test_la_anticipacion_pide_recogedor_en_el_1_y_monedas_antes_del_5() -> None:
    cells = {(0, 0): COLLECT, (1, 0): coin(10), (2, 0): coin(10)}
    assert evaluate_base(grid_with(cells)).anticipation
    cells = {(0, 0): COLLECT, (1, 0): coin(10), (4, 0): coin(10)}
    assert not evaluate_base(grid_with(cells)).anticipation


def test_las_fichas_suben_los_botes_sin_pasar_del_maximo() -> None:
    meters = Meters()
    meters.add_chips({"mini": 2, "major": 0})
    assert meters.mini == MINI_BASE + 2 * MINI_CHIP
    meters.add_chips({"mini": 1_000, "major": 1_000})
    assert (meters.mini, meters.major) == (MINI_MAX, MAJOR_MAX)


# -- Maletines ----------------------------------------------------------------------


def test_el_bonus_se_juega_a_la_apuesta_media_del_maletin() -> None:
    case = Case(Tier.RED)
    case.add(CASE_SIZE[Tier.RED] - 1, 10)
    case.add(1, 10_000)
    assert case.full
    expected = (10 * (CASE_SIZE[Tier.RED] - 1) + 10_000) // CASE_SIZE[Tier.RED]
    assert case.empty() == expected
    assert case.coins == 0


def test_lo_que_sobra_del_maletin_pasa_al_siguiente() -> None:
    case = Case(Tier.GREEN)
    case.add(CASE_SIZE[Tier.GREEN] + 2, 100)
    case.empty()
    assert case.coins == 2
    assert case.average_stake() == 100


def test_un_maletin_lleno_dispara_su_bonus() -> None:
    meters = Meters()
    meters.cases[Tier.BLUE].add(CASE_SIZE[Tier.BLUE] - 1, 100)
    spin = evaluate_base(grid_with({(1, 1): coin(80, Tier.BLUE)}))

    class NoUpgrade(random.Random):
        def randrange(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
            return 1

    trigger = fill_cases(meters, spin, 100, NoUpgrade())
    assert trigger == Trigger(BonusKind.BLUE, 100, upgraded=False)
    assert meters.cases[Tier.BLUE].coins == 0


def test_dos_maletines_llenos_a_la_vez_dan_el_gran_bonus() -> None:
    meters = Meters()
    meters.cases[Tier.GREEN].add(CASE_SIZE[Tier.GREEN] - 1, 100)
    meters.cases[Tier.RED].add(CASE_SIZE[Tier.RED] - 1, 100)
    spin = evaluate_base(grid_with({(1, 1): coin(10), (3, 1): coin(200, Tier.RED)}))
    trigger = fill_cases(meters, spin, 100, random.Random(0))
    assert trigger is not None
    assert trigger.kind == BonusKind.GRAND


def test_sin_maletin_lleno_no_hay_bonus_pero_se_guardan_las_monedas() -> None:
    meters = Meters()
    spin = evaluate_base(grid_with({(1, 1): coin(10), (3, 1): coin(10)}))
    assert fill_cases(meters, spin, 50, random.Random(0)) is None
    assert meters.cases[Tier.GREEN].coins == 2
    assert meters.cases[Tier.GREEN].stake_sum == 100


# -- Bonus --------------------------------------------------------------------------


def start(kind: str = BonusKind.GREEN, seed: int = 0) -> BonusGame:
    return BonusGame.start(
        Trigger(kind, 100, False), mini=MINI_BASE, major=MAJOR_BASE, rng=random.Random(seed)
    )


def test_el_bonus_empieza_con_monedas_del_color_y_tres_tiradas() -> None:
    game = start(BonusKind.RED)
    assert game.respins_left == RESET_VALUE
    assert game.coins >= 3
    assert all(c.tier == Tier.RED for c in game.board if c is not None)


def test_las_monedas_iniciales_van_donde_cayeron_en_la_tirada() -> None:
    game = BonusGame.start(
        Trigger(BonusKind.GREEN, 100, False),
        mini=MINI_BASE,
        major=MAJOR_BASE,
        rng=random.Random(1),
        seed_cells=[0, 7],
    )
    assert game.board[0] is not None and game.board[7] is not None


class Script(random.Random):
    """Azar trucado para el bonus: nada cae, salvo en las casillas vacías indicadas.

    `lands` son posiciones en el orden en que se recorren las casillas vacías.
    `picks` son las tiradas de `randrange` de lo que cae (tipo, valor…).
    """

    def __init__(self, lands: set[int], picks: list[int] | None = None) -> None:
        super().__init__(0)
        self.lands = lands
        self.calls = 0
        self.picks = iter(picks) if picks is not None else None

    def random(self) -> float:
        index = self.calls
        self.calls += 1
        return 0.0 if index in self.lands else 0.999

    def randrange(self, start, stop=None, step=1):  # noqa: ANN001, ANN201
        if self.picks is not None:
            return next(self.picks)
        return super().randrange(start, stop, step)


def test_si_no_cae_nada_se_gasta_una_tirada_y_si_cae_vuelve_a_tres() -> None:
    game = start()
    empty = [i for i, c in enumerate(game.board) if c is None]
    game.step(Script(set()))
    assert game.respins_left == RESET_VALUE - 1
    game.step(Script({0}))  # cae algo en la primera casilla vacía
    assert game.respins_left == game.reset_value
    assert game.board[empty[0]] is not None


def test_el_bonus_termina_al_quedarse_sin_tiradas() -> None:
    game = start()
    for _ in range(RESET_VALUE):
        game.step(Script(set()))
    assert game.finished
    with pytest.raises(RuntimeError):
        game.step(Script(set()))


def test_los_botes_se_ganan_por_monedas_y_no_se_multiplican() -> None:
    game = start()
    game.board = [coin(10) for _ in range(15)] + [None] * 5
    game.multiplier = 3
    result = game.result()
    assert result.jackpots == ("mini", "major")
    assert result.points == 150 * 3 + MINI_BASE + MAJOR_BASE


def test_pantalla_llena_da_el_grand() -> None:
    game = start()
    game.board = [coin(10) for _ in range(CELLS)]
    assert game.finished
    result = game.result()
    assert "grand" in result.jackpots
    assert result.jackpot_values[-1] == GRAND


def test_el_multiplicador_inmediato_multiplica_las_monedas_de_la_mesa() -> None:
    game = start()
    game.board = [coin(20)] + [None] * (CELLS - 1)
    # 880 cae en el tramo del multiplicador inmediato y 50 en el de ×2.
    step = game.step(Script({0}, picks=[880, 50]))
    assert step.instant == 20
    assert game.board[0] == coin(40)


def test_reset_de_botes_ganados() -> None:
    meters = Meters(mini=MINI_MAX, major=MAJOR_MAX)
    reset_won_jackpots(meters, ("mini",))
    assert (meters.mini, meters.major) == (MINI_BASE, MAJOR_MAX)


def test_los_modificadores_desaparecen_en_la_tirada_siguiente() -> None:
    game = start()
    game.board[19] = Cell(Kind.TICKET, value=2)
    step: BonusStep = game.step(Script(set()))
    assert step.board[19] is None


# -- Máquinas y retorno -----------------------------------------------------------------


def test_cada_maquina_tiene_seis_simbolos_y_sus_bonus() -> None:
    assert set(THEMES) == {"volcan", "mina", "olimpo"}
    for theme in THEMES.values():
        assert len(theme.symbols) == len(WAYS_PAYS)
        assert set(theme.bonus_names) == set(BonusKind.ALL)


def test_tirada_al_azar_tiene_veinte_casillas_y_el_recogedor_solo_en_los_extremos() -> None:
    rng = random.Random(3)
    for _ in range(500):
        spin = spin_base(rng)
        assert len(spin.grid) == CELLS
        for index in spin.collector_cells:
            assert index // ROWS in (0, 4)
        for index, cell in enumerate(spin.grid):
            if cell.kind == Kind.WILD:
                assert index // ROWS in (1, 2, 3)


def test_la_apuesta_minima_da_premios_de_al_menos_un_yapdollar() -> None:
    assert to_amount(10, MIN_STAKE) >= 1


@pytest.mark.lenta
def test_el_retorno_ronda_el_94_por_ciento(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulación corta con semilla fija: el número exacto está en el docstring.

    Con 150.000 tiradas el error típico ronda los dos puntos (los bonus y,
    sobre todo, el Grand dan mucha varianza), así que el margen es amplio.
    El ajuste fino se hizo con varios millones de tiradas.

    No se puede recortar: con 40 semillas distintas, 40.000 tiradas dan un retorno
    de 0,91 a 1,10 y 80.000 de 0,90 a 1,03, fuera de la horquilla (0,88 a 1,0) en
    ambos casos. Lo que sí se acelera es `_pick` (el sorteo con pesos, lo más
    llamado): las sumas acumuladas de cada tabla se calculan una vez y se busca
    con `bisect`. Gasta la misma tirada de `rng` y devuelve lo mismo que el
    original, así que la simulación sale idéntica.
    """
    # Se guarda también la tabla para que su `id` no se reutilice mientras dura la caché.
    cumulative: dict[int, tuple[list[int], list[object], Sequence]] = {}

    def fast_pick(rng: random.Random, table: Sequence[tuple[object, int]]) -> object:
        cached = cumulative.get(id(table))
        if cached is None:
            cached = (
                list(itertools.accumulate(weight for _item, weight in table)),
                [item for item, _weight in table],
                table,
            )
            cumulative[id(table)] = cached
        sums, items, _table = cached
        return items[bisect.bisect_right(sums, rng.randrange(sums[-1]))]

    monkeypatch.setattr(hold_win_module, "_pick", fast_pick)
    sim = simulate(150_000, random.Random(2026))
    assert 0.88 < sim.rtp < 1.0
    assert 0.38 < sim.part("ways") < 0.46
    assert 0.18 < sim.part("collect") < 0.25
    assert 0.2 < sim.part("bonus") < 0.4
    assert 45 < sim.bonus_every < 75
