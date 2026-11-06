"""Pruebas de los minijuegos de los cinco sectores de la 9.48.0 (`business_games`).

La brasa (guachinche), los encargos (panadería), la balanza (pescadería), la revisión
(móviles) y la foto (peluquería): las reglas de cada pulsación, lo que se cobra, lo que se
pierde y que la cola sale de lo que queda en el stock. El azar se fuerza eligiendo el
cliente o fijando lo que hay en la mesa, no se confía en que salga.
"""

from __future__ import annotations

import random
from dataclasses import replace

import pytest

from bot.services.business import REPUTATION, Business, regular_names, usual_order
from bot.services.business_catalog import (
    BURNT,
    FISH_GRAMS,
    GRILLED,
    LENGTHS,
    POINT_BY_KEY,
    SECTOR_BY_KEY,
    STYLE_TWISTS,
)
from bot.services.business_games import (
    AUTOPLAY_GAMES,
    BAGS,
    SCORES,
    TIP_SHARE,
    UPSET,
    WEIGHT_FAIR,
    WEIGHT_HEAVY,
    WEIGHT_OK,
    Client,
    Style,
    TurnGame,
    autoplay,
    best_weight,
    make_clients,
    restyle,
    twists_for,
    usual_style,
)

NEW = ("guachinche", "panaderia", "pescaderia", "moviles", "peluqueria")


def business(sector: str, seed: int = 7, **stock: int) -> Business:
    info = SECTOR_BY_KEY[sector]
    return Business(
        user_id=1,
        sector=sector,
        barrio="schamann",
        name="Prueba",
        seed=seed,
        stock={p.key: stock.get(p.key, p.pack * 3) for p in info.products},
    )


def game_with(sector: str, client: Client, **stock: int) -> TurnGame:
    return TurnGame(business(sector, **stock), [client], now=0.0)


def find(sector: str, cond, *, loyalty: int = 0, **stock: int) -> tuple[Business, Client]:  # noqa: ANN001
    """El primer cliente de las semillas 1 en adelante que cumple `cond`."""
    for seed in range(1, 500):
        shop = business(sector, seed, **stock)
        shop.regulars = {name: loyalty for name in regular_names(seed)}
        for client in make_clients(shop, SECTOR_BY_KEY[sector].clients, seed):
            if cond(client):
                return shop, client
    raise AssertionError("ningún cliente cumple la condición")


# -- Lo común ----------------------------------------------------------------------------


def test_cada_sector_nuevo_tiene_su_propio_minijuego() -> None:
    games = [SECTOR_BY_KEY[key].game for key in NEW]
    old = {SECTOR_BY_KEY[key].game for key in ("cafeteria", "tienda", "taller")}
    assert len(set(games)) == 5 and not set(games) & old
    assert set(games) == AUTOPLAY_GAMES
    assert set(games) <= set(TIP_SHARE)


def test_cada_resultado_nuevo_tiene_nota_y_lo_que_mueve_la_reputacion_existe() -> None:
    results = ("raw", "overdone", "burnt", "wrong_bag", "missed_bag", "honest", "heavy", "light",
               "refused", "missed", "upsell", "unhappy", "mess", "too_short")  # fmt: skip
    events = ("raw", "overdone", "burnt", "wrong_bag", "honest", "exact_weight", "heavy",
              "light", "refused", "missed_fault", "upsell", "too_short", "unhappy")  # fmt: skip
    assert set(results) <= set(SCORES) and set(events) <= set(REPUTATION)
    assert UPSET.isdisjoint({"ok", "honest"})


@pytest.mark.parametrize("sector", NEW)
def test_la_misma_semilla_da_la_misma_cola(sector: str) -> None:
    first = make_clients(business(sector), 6, 42)
    second = make_clients(business(sector), 6, 42)
    assert first == second and first


@pytest.mark.parametrize("sector", NEW)
def test_sin_stock_no_viene_nadie(sector: str) -> None:
    info = SECTOR_BY_KEY[sector]
    assert make_clients(business(sector, **{p.key: 0 for p in info.products}), 6, 1) == []


@pytest.mark.parametrize("sector", NEW)
def test_quien_juega_bien_atiende_a_todos_sin_enfadar_a_nadie(sector: str) -> None:
    for seed in range(40):
        shop = business(sector, seed)
        game = TurnGame(shop, make_clients(shop, SECTOR_BY_KEY[sector].clients, seed), now=0.0)
        now = 0.0
        while not game.finished(now):
            now = autoplay(game, now, click=1.0, wrong=False, rng=random.Random(seed))
        results = {o.result for o in game.outcomes}
        # En la brasa, ni jugando bien se evita del todo pasarse: el último empujón es a ciegas.
        assert results <= (
            {"ok", "honest", "overdone"} if sector == "guachinche" else {"ok", "honest"}
        )


@pytest.mark.parametrize("sector", NEW)
def test_el_siguiente_cliente_se_pinta_con_la_mesa_limpia(sector: str) -> None:
    shop = business(sector)
    game = TurnGame(shop, make_clients(shop, 4, 3), now=0.0)
    game.heat, game.scale, game.marked = 55, [0], ["pantalla"]
    game.style = Style(length="corto", fade=True)
    ahead = game.ahead()
    assert ahead.current is game.clients[1] and game.current is game.clients[0]
    assert (ahead.heat, ahead.scale, ahead.marked, ahead.style.length) == (0, [], [], "")
    assert game.heat == 55  # el de ahora no se toca


# -- La brasa ------------------------------------------------------------------------------


def grill(point: str = "punto", dish: str = "carne", **extra: int) -> Client:
    return Client(seed=1, line="Hola.", order={dish: 1, **extra}, dish=dish, point=point)


def test_brasa_en_su_punto_cobra_la_mesa_y_en_el_centro_lo_clava() -> None:
    _key, _ask, low, high = POINT_BY_KEY["punto"]
    for heat, perfect in ((low, False), ((low + high) // 2, True), (high, False)):
        game = game_with("guachinche", grill(vino=2))
        game.heat = heat
        game.press("serve", 1.0)
        outcome = game.outcomes[0]
        assert outcome.result == "ok" and outcome.perfect is perfect and outcome.heat == heat
        assert outcome.revenue == game.price_of("carne") + 2 * game.price_of("vino")
        assert outcome.units == {"carne": 1, "vino": 2}


def test_brasa_cruda_devuelven_el_plato_y_pagan_lo_demas() -> None:
    game = game_with("guachinche", grill(papas=1))
    game.heat = POINT_BY_KEY["punto"][2] - 1
    game.press("serve", 1.0)
    outcome = game.outcomes[0]
    assert outcome.result == "raw" and "raw" in outcome.events
    assert outcome.revenue == game.price_of("papas")
    assert outcome.waste_cost == SECTOR_BY_KEY["guachinche"].product("carne").cost


def test_brasa_pasada_cobra_sin_propina_y_a_cien_se_quema_sola() -> None:
    game = game_with("guachinche", grill("poco"))
    game.heat = POINT_BY_KEY["poco"][3] + 5
    game.press("serve", 0.5)
    assert game.outcomes[0].result == "overdone" and game.outcomes[0].tip == 0
    burn = game_with("guachinche", grill("muy", vino=1))
    burn.heat = BURNT - 1
    burn.press("fire:1", 1.0)
    outcome = burn.outcomes[0]
    assert outcome.result == "burnt" and outcome.revenue == burn.price_of("vino")
    assert outcome.units == {"vino": 1, "carne": 1}


def test_brasa_cada_fuego_sube_lo_suyo_y_es_repetible() -> None:
    one, two = game_with("guachinche", grill()), game_with("guachinche", grill())
    one.press("fire:1", 1.0)
    two.press("fire:1", 1.0)
    assert one.heat == two.heat and 6 <= one.heat <= 14
    one.press("fire:2", 2.0)
    assert 21 <= one.heat <= 44 and one.flips == 2


def test_una_mesa_del_guachinche_lleva_como_mucho_un_plato_de_brasa() -> None:
    for seed in range(30):
        for client in make_clients(business("guachinche", seed), 6, seed):
            grilled = [k for k in client.order if k in GRILLED]
            assert len(grilled) <= 1 and (client.dish in grilled or not grilled)


def test_el_habitual_del_guachinche_pide_su_punto_de_siempre_sin_decirlo() -> None:
    shop, client = find("guachinche", lambda c: c.usual and c.dish, loyalty=6)
    assert client.order == usual_order(shop.seed, client.regular, shop.sector_info)


# -- Los encargos --------------------------------------------------------------------------


def test_en_la_balda_solo_una_bolsa_tiene_su_nombre_y_lo_que_dice() -> None:
    for seed in range(40):
        for client in make_clients(business("panaderia", seed), 8, seed):
            assert len(client.bags) == BAGS
            matches = [
                i for i, b in enumerate(client.bags)
                if b.label == client.label and client.clue in b.order
            ]  # fmt: skip
            if client.bag >= 0:
                assert matches == [client.bag] and client.bags[client.bag].order == client.order
            else:
                assert matches == []


def test_dar_su_bolsa_cobra_y_la_gemela_se_pierde() -> None:
    _shop, client = find("panaderia", lambda c: c.bag >= 0 and not c.usual)
    game = game_with("panaderia", client)
    game.press(f"b:{client.bag}", 1.0)
    assert game.outcomes[0].result == "ok"
    assert game.outcomes[0].revenue == sum(game.price_of(k) * n for k, n in client.order.items())
    twin = next(i for i, b in enumerate(client.bags) if b.label == client.label and i != client.bag)
    wrong = game_with("panaderia", client)
    wrong.press(f"b:{twin}", 1.0)
    outcome = wrong.outcomes[0]
    assert outcome.result == "wrong_bag" and outcome.revenue == 0 and outcome.waste_cost > 0


def test_decir_que_no_esta_vale_solo_si_no_esta() -> None:
    _shop, absent = find("panaderia", lambda c: c.bag < 0)
    game = game_with("panaderia", absent)
    game.press("none", 1.0)
    assert game.outcomes[0].result == "honest" and game.outcomes[0].score == 100
    _shop, present = find("panaderia", lambda c: c.bag >= 0)
    game = game_with("panaderia", present)
    game.press("none", 1.0)
    assert game.outcomes[0].result == "missed_bag" and "wrong_bag" in game.outcomes[0].events


# -- La balanza ----------------------------------------------------------------------------


def test_cada_pedido_de_la_balanza_se_puede_servir_con_las_piezas() -> None:
    for seed in range(40):
        for client in make_clients(business("pescaderia", seed), 6, seed):
            low, high, _avg = FISH_GRAMS[client.species]
            assert all(low <= g <= high for g in client.pieces)
            best = sum(client.pieces[i] for i in best_weight(client.pieces, client.grams))
            assert abs(best - client.grams) <= client.grams * WEIGHT_FAIR


def fish(grams: int, pieces: tuple[int, ...]) -> Client:
    return Client(seed=1, line="Hola.", species="vieja", grams=grams, pieces=pieces)


def test_la_balanza_cobra_por_peso_y_marca_cuanto_se_aleja() -> None:
    pieces = (400, 600, 300, 900, 250)
    for chosen, result in (((0, 1), "ok"), ((0, 1, 4), "heavy"), ((0, 2), "light"),
                           ((1, 3), "refused")):  # fmt: skip
        game = game_with("pescaderia", fish(1_000, pieces))
        for index in chosen:
            game.press(f"w:{index}", 1.0)
        grams = game.weighed()
        game.press("weigh", 2.0)
        outcome = game.outcomes[0]
        assert outcome.result == result, (chosen, grams)
        if result == "refused":
            assert outcome.revenue == 0 and outcome.units == {}
        else:
            assert outcome.revenue == game.weight_price("vieja", grams)
            assert outcome.units == {"vieja": len(chosen)}
    assert 1 + WEIGHT_OK < 1_150 / 1_000 < 1 + WEIGHT_HEAVY


def test_poner_y_quitar_una_pieza_la_deja_fuera_y_sin_piezas_no_se_pesa() -> None:
    game = game_with("pescaderia", fish(500, (250, 260)))
    game.press("w:0", 1.0)
    game.press("w:0", 1.5)
    assert game.scale == [] and game.weighed() == 0
    game.press("weigh", 2.0)
    assert not game.outcomes and "vacía" in game.last


def test_lo_justo_en_la_balanza_lo_clava() -> None:
    game = game_with("pescaderia", fish(500, (250, 250, 300)))
    game.press("w:0", 1.0)
    game.press("w:1", 1.0)
    game.press("weigh", 1.0)
    assert game.outcomes[0].perfect and "exact_weight" in game.outcomes[0].events


# -- La revisión ---------------------------------------------------------------------------


def phone(*faults: str, protector: bool = False) -> Client:
    return Client(
        seed=1, line="Hola.", faults=faults, order={k: 1 for k in faults}, protector=protector,
        symptom="Algo.",
    )  # fmt: skip


def test_marcar_todas_las_averias_las_arregla_y_tres_lo_clavan() -> None:
    game = game_with("moviles", phone("pantalla", "altavoz", "tapa"))
    for key in ("tapa", "pantalla", "altavoz"):
        game.press(f"m:{key}", 1.0)
    game.press("fix", 2.0)
    outcome = game.outcomes[0]
    assert outcome.result == "ok" and outcome.perfect
    assert outcome.revenue == sum(game.price_of(k) for k in ("pantalla", "altavoz", "tapa"))


def test_el_protector_rayado_no_es_la_pantalla() -> None:
    good = game_with("moviles", phone("conector", protector=True))
    good.press("m:conector", 1.0)
    good.press("fix", 1.0)
    assert good.outcomes[0].result == "ok" and good.outcomes[0].trap
    bad = game_with("moviles", phone("conector", protector=True))
    bad.press("m:conector", 1.0)
    bad.press("m:pantalla", 1.0)
    bad.press("fix", 1.0)
    outcome = bad.outcomes[0]
    assert outcome.result == "upsell" and not outcome.trap
    assert outcome.revenue == bad.price_of("conector")
    assert outcome.waste_cost == SECTOR_BY_KEY["moviles"].product("pantalla").cost


def test_dejarse_una_averia_cobra_lo_arreglado_y_vuelve() -> None:
    game = game_with("moviles", phone("bateria_movil", "camara_movil"))
    game.press("m:bateria_movil", 1.0)
    game.press("fix", 1.0)
    outcome = game.outcomes[0]
    assert outcome.result == "missed" and "missed_fault" in outcome.events
    assert outcome.revenue == game.price_of("bateria_movil")


def test_sin_la_pieza_no_se_puede_marcar_ni_reparar_sin_marcar() -> None:
    game = game_with("moviles", phone("altavoz"), altavoz=0)
    game.press("m:altavoz", 1.0)
    assert game.marked == [] and "No te queda" in game.last
    game.press("fix", 1.0)
    assert not game.outcomes


def test_las_averias_del_movil_salen_de_las_piezas_que_quedan() -> None:
    for seed in range(30):
        for client in make_clients(business("moviles", seed, pantalla=0, tapa=0), 4, seed):
            assert 1 <= len(client.faults) <= 3 and len(set(client.faults)) == len(client.faults)
            assert not {"pantalla", "tapa"} & set(client.faults)
            assert not (client.protector and "pantalla" in client.faults)


# -- La foto -------------------------------------------------------------------------------


def test_cada_cambio_de_la_foto_hace_lo_que_dice() -> None:
    photo = Style(length="medio", fade=True, color="tinte")
    assert restyle(photo, "igual") == photo
    assert restyle(photo, "corto").length == "corto"
    assert restyle(photo, "sin_degradado").fade is False
    assert restyle(photo, "natural").color == "natural"
    assert restyle(photo, "mechas").color == "mechas"
    assert restyle(photo, "barba").beard and restyle(photo, "peinado").wash
    assert {key for key, _ in STYLE_TWISTS} >= set(twists_for(photo, beard=True))
    assert "barba" not in twists_for(photo, beard=False)
    assert "corto" not in twists_for(Style(length="rapado"), beard=False)


def test_lo_que_pide_la_peluqueria_nunca_es_mas_largo_de_lo_que_trae() -> None:
    for seed in range(40):
        for client in make_clients(business("peluqueria", seed), 4, seed):
            assert client.target is not None and client.photo is not None
            assert LENGTHS.index(client.target.length) <= LENGTHS.index(client.hair)
            assert client.order == client.target.services()
            if client.twist:
                assert restyle(client.photo, client.twist) == client.target


def cut(target: Style, twist: str = "igual", hair: str = "largo", beard: bool = True) -> Client:
    return Client(
        seed=1, line="Hola.", photo=target, twist=twist, target=target, hair=hair, beard=beard,
        order=target.services(),
    )  # fmt: skip


def play(game: TurnGame, style: Style) -> None:
    game.press(f"l:{style.length}", 1.0)
    if style.fade:
        game.press("fade", 1.0)
    if style.color != "natural":
        game.press(f"col:{style.color}", 1.0)
    if style.beard:
        game.press("beard", 1.0)
    if style.wash:
        game.press("wash", 1.0)
    game.press("done", 2.0)


def test_la_foto_bien_hecha_cobra_cada_servicio() -> None:
    target = Style(length="corto", fade=True, color="mechas", beard=True)
    game = game_with("peluqueria", cut(target))
    play(game, target)
    outcome = game.outcomes[0]
    assert outcome.result == "ok"
    assert outcome.revenue == sum(
        game.price_of(k) for k in ("corte", "degradado", "mechas", "barba")
    )


def test_cortar_de_mas_no_tiene_arreglo_y_lo_demas_paga_lo_pedido() -> None:
    target = Style(length="medio")
    short = game_with("peluqueria", cut(target))
    play(short, replace(target, length="corto"))
    assert short.outcomes[0].result == "too_short" and short.outcomes[0].revenue == 0
    long = game_with("peluqueria", cut(target))
    play(long, replace(target, length="largo", color="tinte"))
    outcome = long.outcomes[0]
    assert outcome.result == "mess" and outcome.revenue == long.price_of("corte")
    assert outcome.waste_cost == SECTOR_BY_KEY["peluqueria"].product("tinte").cost
    one = game_with("peluqueria", cut(target))
    play(one, replace(target, wash=True))
    assert one.outcomes[0].result == "unhappy"


def test_sin_elegir_largo_no_se_termina() -> None:
    game = game_with("peluqueria", cut(Style(length="corto")))
    game.press("done", 1.0)
    assert not game.outcomes and "largo" in game.last


def test_el_corte_de_siempre_del_habitual_no_cambia() -> None:
    assert usual_style(5, "Yeray") == usual_style(5, "Yeray")
    _shop, client = find("peluqueria", lambda c: c.usual, loyalty=6)
    assert client.twist == "" and client.target == client.photo
