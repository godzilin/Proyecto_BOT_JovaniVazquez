"""Pruebas de las cajas: retorno, colecciones, sorteo, tapetes repetidos y temporadas.

El retorno de cada caja (~95 % de su precio a valor de catálogo) sale de la función pura
`expected_value`; el sorteo se prueba con un azar de guion (`Scripted`) para forzar la
rareza o el tapete, y con muchas tiradas para ver que las frecuencias siguen la tabla.
"""

from __future__ import annotations

import random
from collections import Counter
from datetime import datetime

import pytest
from lootbox_fakes import Scripted

from bot.services.levels import TIMEZONE
from bot.services.lootboxes import (
    BOX_BY_KEY,
    BOXES,
    COLLECTIONS,
    HIELO_ODDS,
    NORMAL_COLLECTIONS,
    ODDS,
    PIECE_BY_KEY,
    PIECES,
    SEASON_SHARE,
    SEASONAL_COLLECTIONS,
    TAPETE,
    Box,
    Rarity,
    expected_value,
    in_season,
    return_rate,
    reveal_frame,
    roll,
    tapetes_for,
)
from bot.services.rastro import untradeable
from bot.services.shop import Kind
from bot.services.skins import HIELO, SKIN_BY_CATALOG_KEY, SKIN_BY_KEY

# -- Retorno ----------------------------------------------------------------------------


@pytest.mark.parametrize("box", BOXES, ids=lambda b: b.key)
def test_cada_caja_devuelve_de_media_entre_93_y_97_por_ciento(box: Box) -> None:
    assert 0.93 <= return_rate(box) <= 0.97, (box.key, return_rate(box))
    assert expected_value(box) == pytest.approx(return_rate(box) * box.price)


@pytest.mark.parametrize("box", BOXES, ids=lambda b: b.key)
def test_las_probabilidades_de_cada_caja_suman_uno(box: Box) -> None:
    assert sum(box.odds.values()) == pytest.approx(1.0, abs=1e-6)
    assert set(box.odds) <= {"C", "R", "E", "L", "M", TAPETE, HIELO_ODDS}
    assert all(chance > 0 for chance in box.odds.values())


def test_el_retorno_cuenta_los_tapetes() -> None:
    """Quitar el tapete del maletín y del cofre baja su retorno: el tapete está en la cuenta."""
    for key in ("maletin_ministro", "cofre_rey_emerito"):
        box = BOX_BY_KEY[key]
        with_tapete = expected_value(box)
        original = dict(ODDS[key])
        try:
            share = ODDS[key].pop(TAPETE)
            ODDS[key]["C"] += share
            assert expected_value(box) < with_tapete
        finally:
            ODDS[key] = original


def test_solo_el_maletin_y_el_cofre_dan_tapetes() -> None:
    assert {b.key for b in BOXES if b.uses_tapetes} == {"maletin_ministro", "cofre_rey_emerito"}
    for box in BOXES:
        if not box.uses_tapetes:
            assert tapetes_for(box) == ()


def test_los_tapetes_de_una_caja_son_los_que_se_compran_sin_limites() -> None:
    for key in ("maletin_ministro", "cofre_rey_emerito"):
        box = BOX_BY_KEY[key]
        tapetes = tapetes_for(box)
        assert len(tapetes) >= 5
        for skin in tapetes:
            assert skin.game == "tapete" and skin.for_sale
            assert skin.stock is None and skin.season is None and not skin.custom
            assert skin.min_level <= box.min_level, "una caja no se salta el nivel del tapete"
    cofre, maletin = BOX_BY_KEY["cofre_rey_emerito"], BOX_BY_KEY["maletin_ministro"]
    assert {s.key for s in tapetes_for(maletin)} <= {s.key for s in tapetes_for(cofre)}


# -- Colecciones ------------------------------------------------------------------------


def test_colecciones_de_ocho_piezas_normales_y_tres_de_temporada_de_seis() -> None:
    assert len(NORMAL_COLLECTIONS) >= 6 and len(SEASONAL_COLLECTIONS) == 3
    assert all(c.size == 8 for c in NORMAL_COLLECTIONS)
    assert all(c.size == 6 for c in SEASONAL_COLLECTIONS)
    assert {c.season.name for c in SEASONAL_COLLECTIONS} == {"Navidad", "Halloween", "Aniversario"}
    assert len(PIECES) == sum(c.size for c in COLLECTIONS) == len(PIECE_BY_KEY)


def test_hay_piezas_de_las_cinco_rarezas_y_el_valor_sube_con_la_rareza() -> None:
    by_rarity = {r: [p.price for p in PIECES if p.rarity is r] for r in Rarity}
    assert all(by_rarity[r] for r in Rarity)
    means = [sum(prices) / len(prices) for prices in by_rarity.values()]
    assert means == sorted(means)
    # Cada rareza vale claramente más que la anterior: no se pisan.
    for lower, higher in zip(Rarity, list(Rarity)[1:], strict=False):
        assert max(by_rarity[lower]) < min(by_rarity[higher])


def test_las_piezas_se_pueden_vender_y_cambiar_en_el_rastro() -> None:
    for piece in PIECES:
        assert untradeable(Kind.TROPHY, piece.key) is None, piece.key
    for box in BOXES:
        assert untradeable(Kind.TROPHY, box.key) is None, box.key
    # Y los tapetes que dan las cajas se pueden vender como cualquier tapete de tienda.
    for skin in tapetes_for(BOX_BY_KEY["cofre_rey_emerito"]):
        assert untradeable(Kind.TROPHY, skin.catalog_key) is None, skin.key


def test_las_piezas_caras_piden_nivel_como_todo_el_lujo() -> None:
    for piece in PIECES:
        if piece.price >= 100_000:
            assert piece.min_level >= 10
        assert piece.price > 0


# -- Sorteo -----------------------------------------------------------------------------


def test_las_frecuencias_del_sorteo_siguen_la_tabla() -> None:
    box = BOX_BY_KEY["caja_regalo"]
    rng = random.Random(2026)
    runs = 30_000
    seen = Counter(roll(box, rng).rarity for _ in range(runs))  # type: ignore[union-attr]
    for letter, chance in box.odds.items():
        if letter == HIELO_ODDS:
            continue
        rarity = {"C": Rarity.COMMON, "R": Rarity.RARE, "E": Rarity.EPIC, "L": Rarity.LEGENDARY,
                  "M": Rarity.MYTHIC}[letter]  # fmt: skip
        assert seen[rarity] / runs == pytest.approx(chance, abs=0.012), letter


def test_el_retorno_real_de_la_caja_de_regalo_sigue_al_calculado() -> None:
    box = BOX_BY_KEY["caja_regalo"]
    rng = random.Random(7)
    runs = 60_000
    total = sum(roll(box, rng).price for _ in range(runs))  # type: ignore[union-attr]
    assert total / runs == pytest.approx(expected_value(box), rel=0.08)


def test_la_rareza_elegida_decide_la_pieza() -> None:
    box = BOX_BY_KEY["caja_carton"]
    for letter, rarity in (("C", Rarity.COMMON), ("R", Rarity.RARE), ("M", Rarity.MYTHIC)):
        drop = roll(box, Scripted([letter]))
        assert drop is not None and drop.piece is not None
        assert drop.rarity is rarity and drop.piece.rarity is rarity and drop.skin is None
        assert drop.piece.collection in {c.key for c in NORMAL_COLLECTIONS}


def test_un_tapete_sale_de_la_lista_de_la_caja() -> None:
    box = BOX_BY_KEY["cofre_rey_emerito"]
    drop = roll(box, Scripted([TAPETE]))
    assert drop is not None and drop.skin is not None and drop.piece is None
    assert drop.skin in tapetes_for(box) and not drop.rerolled
    assert drop.catalog_key == drop.skin.catalog_key and drop.price == drop.skin.price


def test_un_tapete_repetido_se_vuelve_a_sortear_entre_los_coleccionables() -> None:
    box = BOX_BY_KEY["cofre_rey_emerito"]
    owned = {s.catalog_key for s in tapetes_for(box)}
    drop = roll(box, Scripted([TAPETE, "L"]), owned=owned)
    assert drop is not None and drop.skin is None and drop.rerolled
    assert drop.piece is not None and drop.piece.rarity is Rarity.LEGENDARY
    # Con alguno que le falta, sale ese y solo ese.
    missing = tapetes_for(box)[3]
    drop = roll(box, Scripted([TAPETE]), owned=owned - {missing.catalog_key})
    assert drop is not None and drop.skin == missing and not drop.rerolled


def test_un_tapete_que_el_servidor_no_vende_tampoco_sale() -> None:
    box = BOX_BY_KEY["maletin_ministro"]
    only_pieces = {p.key for p in PIECES}
    drop = roll(box, Scripted([TAPETE]), have=only_pieces)
    assert drop is not None and drop.skin is None and drop.rerolled


@pytest.mark.parametrize("box", BOXES, ids=lambda b: b.key)
def test_el_hielo_negro_sale_de_cualquier_caja_una_vez_de_cada_50_000(box: Box) -> None:
    assert box.odds[HIELO_ODDS] == pytest.approx(1 / 50_000)
    hielo = SKIN_BY_KEY[HIELO]
    assert not hielo.for_sale and hielo not in tapetes_for(box)
    drop = roll(box, Scripted([HIELO_ODDS]))
    assert drop is not None and drop.skin == hielo and not drop.rerolled
    text = reveal_frame("**Ana**", box, drop, dupe=False, owned=0, rng=random.Random(1),
                        price_text="80.000 Y$")  # fmt: skip
    assert "HIELO NEGRO" in text


def test_el_hielo_negro_repetido_se_vuelve_a_sortear() -> None:
    box = BOX_BY_KEY["caja_carton"]
    drop = roll(box, Scripted([HIELO_ODDS, "R"]), owned={SKIN_BY_KEY[HIELO].catalog_key})
    assert drop is not None and drop.skin is None and drop.rerolled
    assert drop.piece is not None and drop.piece.rarity is Rarity.RARE


def test_sin_piezas_en_el_servidor_la_caja_no_da_nada() -> None:
    assert roll(BOX_BY_KEY["caja_carton"], random.Random(1), have=set()) is None


def test_si_falta_una_pieza_se_da_otra_de_su_rareza_antes_que_nada() -> None:
    box = BOX_BY_KEY["caja_carton"]
    commons = [p for p in PIECES if p.rarity is Rarity.COMMON]
    have = {p.key for p in PIECES} - {commons[0].key}
    for seed in range(200):
        drop = roll(box, Scripted(["C"], seed), have=have)
        assert drop is not None and drop.piece is not None
        assert drop.piece.key != commons[0].key
        assert drop.piece.rarity is Rarity.COMMON


def test_un_tapete_no_cuenta_como_pieza_en_el_texto_y_dice_que_es_un_tapete() -> None:
    box = BOX_BY_KEY["maletin_ministro"]
    drop = roll(box, Scripted([TAPETE]))
    assert drop is not None
    text = reveal_frame("**Ana**", box, drop, dupe=False, owned=0, rng=random.Random(1),
                        price_text="30.000 Y$")  # fmt: skip
    assert "TAPETE" in text and drop.name in text


def test_el_texto_final_cuenta_repetidas_y_progreso_de_la_coleccion() -> None:
    box = BOX_BY_KEY["caja_carton"]
    drop = roll(box, Scripted(["R"]))
    assert drop is not None and drop.piece is not None
    text = reveal_frame("**Ana**", box, drop, dupe=True, owned=3, rng=random.Random(1),
                        price_text="1.800 Y$")  # fmt: skip
    collection = next(c for c in COLLECTIONS if c.key == drop.piece.collection)
    assert drop.piece.name in text and "Repetida" in text
    assert f"3/{collection.size}" in text and drop.rarity.label in text


# -- Temporadas -------------------------------------------------------------------------


def month(m: int) -> datetime:
    return datetime(2026, m, 15, 12, tzinfo=TIMEZONE)


def test_las_cajas_de_temporada_solo_se_venden_en_sus_meses() -> None:
    navidad, halloween, aniversario = (
        BOX_BY_KEY[k] for k in ("caja_navidad", "caja_halloween", "caja_aniversario")
    )
    assert [m for m in range(1, 13) if in_season(navidad, month(m))] == [1, 12]
    assert [m for m in range(1, 13) if in_season(halloween, month(m))] == [10]
    assert [m for m in range(1, 13) if in_season(aniversario, month(m))] == [10]
    for key in ("caja_carton", "caja_regalo", "maletin_ministro", "cofre_rey_emerito"):
        assert all(in_season(BOX_BY_KEY[key], month(m)) for m in range(1, 13))


def test_una_caja_de_temporada_saca_mas_a_menudo_su_coleccion() -> None:
    box = BOX_BY_KEY["caja_navidad"]
    rng = random.Random(11)
    runs = 20_000
    drops = [roll(box, rng) for _ in range(runs)]
    seasonal = sum(1 for d in drops if d and d.piece and d.piece.collection == "navidad")
    normal = sum(1 for d in drops if d and d.piece and d.piece.collection != "navidad")
    assert seasonal / runs == pytest.approx(SEASON_SHARE, abs=0.02)
    assert normal > 0.3 * runs, "las normales también salen"
    # Una caja que no es de temporada nunca saca piezas de temporada.
    carton = [roll(BOX_BY_KEY["caja_carton"], rng) for _ in range(3_000)]
    assert not any(d and d.piece and d.piece.collection in {"navidad", "halloween"} for d in carton)


def test_cada_caja_de_temporada_apunta_a_su_coleccion() -> None:
    for box in BOXES:
        if box.season is not None:
            assert box.collection is not None
            collection = next(c for c in COLLECTIONS if c.key == box.collection)
            assert collection.season is not None and collection.season.name == box.season.name
        else:
            assert box.collection is None


def test_los_tapetes_del_catalogo_existen_con_su_clave() -> None:
    for box in BOXES:
        for skin in tapetes_for(box):
            assert SKIN_BY_CATALOG_KEY[skin.catalog_key] is skin
