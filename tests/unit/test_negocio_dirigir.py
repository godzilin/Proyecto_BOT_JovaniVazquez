"""Pruebas de los clientes especiales, de Dirigir el turno y del Consejo de la semana.

Las reglas están en `bot.services.business_games` (`SPECIALS`) y
`bot.services.business_direct`; los casos de uso, en `BusinessService`; los botones, en el
panel del negocio (`bot.cogs.business`).
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from discord import ui
from test_business_cog import (
    GUILD as COG_GUILD,
)
from test_business_cog import (
    OWNER,
    button,
    check_limits,
    click,
    labels,
    opened_business,
)
from test_negocio_plantilla import GUILD, USER, Clock, hire_first, make, open_cafe

from bot.cogs.business import MESA, PORTADA, TURNO, NegocioPanel, parse_game_id
from bot.services import business_direct as direct
from bot.services import business_staff as staff
from bot.services import pala
from bot.services.achievements import business_stats, mesa_stats
from bot.services.business import COUNCIL_BOOST, REPUTATION, Business, boost
from bot.services.business_catalog import SECTOR_BY_KEY
from bot.services.business_games import (
    DARE_TIP,
    SPECIAL_BY_KEY,
    SPECIALS,
    TurnGame,
    make_clients,
    mark_special,
)
from bot.services.negocio import BusinessError
from bot.services.work import Wish
from bot.services.work_games import ORDAGO_PASS, DeskGame

NOW = 1_000.0


@pytest.fixture(autouse=True)
def sin_azar_de_la_pala(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pala, "wish_for", lambda *args: Wish("no", "Nada.", lambda f: False))
    monkeypatch.setattr(pala, "roll", lambda rng, chance: False)


# -- Clientes especiales ------------------------------------------------------------------


def cafe() -> Business:
    business = Business(USER, "cafeteria", "triana", "Bar", seed=5)
    business.stock = {p.key: 200 for p in SECTOR_BY_KEY["cafeteria"].products}
    return business


def serve(special: str, grant: bool | None, *, wait: float = 1.0, seed: int = 42) -> TurnGame:
    """Atiende bien al primer cliente, que es `special`, tras decidir `grant` (`None`, nada)."""
    business = cafe()
    clients = make_clients(business, 1, seed)
    clients[0].special = special
    game = TurnGame(business, clients, now=NOW)
    if grant is not None:
        game.press(f"s:{int(grant)}", NOW)
    for key, n in clients[0].order.items():
        for _ in range(n):
            game.press(f"p:{key}", NOW + wait)
    game.press("serve", NOW + wait)
    return game


def test_el_cliente_especial_sale_de_su_propia_semilla_y_no_cambia_la_cola() -> None:
    business = cafe()
    plain = make_clients(business, 6, 42)
    marked = make_clients(business, 6, 42)
    seen = set()
    for seed in range(60):
        for client in marked:
            client.special = ""
        mark_special(marked, seed)
        specials = [c.special for c in marked if c.special]
        assert len(specials) <= 1
        seen.update(specials)
        again = make_clients(business, 6, 42)
        mark_special(again, seed)
        assert [c.special for c in again] == [c.special for c in marked]
    assert seen == set(SPECIAL_BY_KEY)
    assert [c.order for c in plain] == [c.order for c in marked]


def test_sin_cliente_especial_el_turno_es_el_de_siempre() -> None:
    plain, marked = serve("", None), serve("influencer", False)
    assert plain.outcomes[0].revenue == marked.outcomes[0].revenue
    assert plain.outcomes[0].events == marked.outcomes[0].events
    assert (plain.outcomes[0].special, marked.outcomes[0].special) == ("", "influencer")


def test_hacerle_precio_al_influencer_cobra_la_mitad_y_sube_la_reputacion() -> None:
    plain, granted = serve("", None).outcomes[0], serve("influencer", True).outcomes[0]
    assert granted.revenue == round(plain.revenue * 0.5)
    assert granted.granted and granted.events.count("special") == 2
    assert granted.reputation == pytest.approx(plain.reputation + 2 * REPUTATION["special"])


def test_al_que_regatea_sin_rebaja_le_sienta_mal_la_mitad_de_las_veces() -> None:
    sour = {
        "special_bad" in serve("regateo", False, seed=seed).outcomes[0].events for seed in range(12)
    }
    assert sour == {True, False}
    granted = serve("regateo", True).outcomes[0]
    assert "special_bad" not in granted.events and "special" in granted.events


def test_el_reto_del_que_tiene_prisa_paga_si_se_le_atiende_rapido() -> None:
    plain = serve("", None).outcomes[0]
    fast = serve("prisas", True).outcomes[0]
    assert fast.speed == "fast"
    assert fast.tip == plain.tip + round(plain.revenue * DARE_TIP)
    slow = serve("prisas", True, wait=20.0).outcomes[0]
    assert slow.speed != "fast" and "special_bad" in slow.events and slow.tip == 0
    assert (
        serve("prisas", False, wait=20.0).outcomes[0].events
        == serve("", None, wait=20.0).outcomes[0].events
    )


def test_al_cliente_especial_se_le_contesta_una_sola_vez_y_sin_contestar_es_que_no() -> None:
    business = cafe()
    clients = make_clients(business, 2, 42)
    clients[0].special = "queja"
    game = TurnGame(business, clients, now=NOW)
    game.press("s:1", NOW)
    game.press("s:0", NOW)
    assert game.granted is True
    assert serve("queja", None).outcomes[0].granted is False


def test_un_cliente_especial_mal_atendido_no_cambia_nada() -> None:
    business = cafe()
    clients = make_clients(business, 1, 42)
    clients[0].special = "influencer"
    game = TurnGame(business, clients, now=NOW)
    game.press("s:1", NOW)
    # Una sola unidad de más: mal servido.
    for key, n in clients[0].order.items():
        for _ in range(n + 1):
            game.press(f"p:{key}", NOW + 1)
    game.press("serve", NOW + 1)
    outcome = game.outcomes[0]
    assert outcome.result == "wrong" and "special" not in outcome.events
    assert (outcome.special, outcome.granted) == ("influencer", True)


def test_la_plantilla_no_ve_clientes_especiales() -> None:
    business = cafe()
    employee = {
        "id": 1, "name": "Yaiza", "traits": [], "humor": 80,
        "skills": dict.fromkeys(staff.SKILLS, 70.0),
    }  # fmt: skip
    turn = staff.play_turn(business, employee, make_clients(business, 6, 42), start=NOW, franja=1)
    assert all(not outcome.special for outcome in turn.outcomes)


# -- Dirigir: el juego --------------------------------------------------------------------


def hands() -> list[direct.Hand]:
    return [
        direct.Hand("Ana", {"acierto": 90.0, "rapidez": 20.0, "trato": 50.0}),
        direct.Hand("Beto", {"acierto": 30.0, "rapidez": 80.0, "trato": 40.0}),
        direct.Hand("Caro", {"acierto": 40.0, "rapidez": 50.0, "trato": 95.0}),
    ]


def directing(seed: int = 3) -> direct.DirectGame:
    game = direct.direct_game(hands(), random.Random(seed), now=NOW)
    game.ordago = ORDAGO_PASS
    return game


def test_dirigir_reparte_dos_tareas_por_empleado_como_mucho() -> None:
    game = directing()
    assert len(game.tasks) == direct.DIRECT_TASKS
    while (current := game.current) is not None:
        assert 1 <= len(current.options) <= len(game.hands)
        game.press(0, NOW + 1)
    assert all(game.load(i) <= direct.DIRECT_LOAD for i in range(len(game.hands)))
    assert sum(game.load(i) for i in range(len(game.hands))) == direct.DIRECT_TASKS


def test_el_mejor_reparto_es_un_100_y_uno_peor_no() -> None:
    for seed in range(8):
        best = directing(seed)
        while best.current is not None:
            best.press(best.best(), NOW + 1)
        assert best.score() == 100
    lazy = directing(1)
    while lazy.current is not None:
        lazy.press(len(lazy.current.options) - 1, NOW + 1)
    assert lazy.score() < 100


def test_dirigir_dice_a_quien_ha_ido_cada_tarea() -> None:
    game = directing()
    text, need = game.tasks[0]
    game.press(0, NOW + 1)
    assert text in game.last and "Ana" in game.last and direct.SKILLS[need] in game.last


def test_cuanto_mejor_se_dirige_menos_falla_la_plantilla() -> None:
    assert direct.direction_lead(0) == 0
    assert direct.direction_lead(100) == direct.DIRECT_LEAD
    assert 0 < direct.direction_lead(60) < direct.direction_lead(90)


# -- Dirigir y el Consejo: el servicio ----------------------------------------------------


async def test_para_dirigir_hacen_falta_dos_empleados_que_atiendan(tmp_path: Path) -> None:
    service = await make(tmp_path, Clock())
    await open_cafe(service)
    with pytest.raises(BusinessError, match="dos empleados"):
        await service.start_direct(GUILD, USER)
    await hire_first(service)
    with pytest.raises(BusinessError, match="dos empleados"):
        await service.start_direct(GUILD, USER)


async def test_dirigir_gasta_una_franja_y_bateria_y_deja_la_mano_del_dueno(tmp_path: Path) -> None:
    service = await make(tmp_path, Clock())
    await open_cafe(service)
    await hire_first(service)
    await hire_first(service)
    battery = await service.work.battery(GUILD, USER)
    money = await service.economy.balance(GUILD, USER)
    business, game, before = await service.start_direct(GUILD, USER)
    assert before == battery and await service.work.battery(GUILD, USER) < battery
    assert len(game.hands) == 2
    saved = await service.business(GUILD, USER)
    assert saved is not None and saved.turns_today == 1
    while game.current is not None:
        game.press(game.best(), service._clock())
    cut = await service.finish_direct(GUILD, USER, business.id, game)
    saved = await service.business(GUILD, USER)
    assert saved is not None
    assert saved.support["direction"] == direct.direction_lead(game.score()) > 0
    assert cut == pytest.approx(staff.LEAD_MISS * saved.support["direction"])
    assert await service.economy.balance(GUILD, USER) == money  # no mueve dinero
    with pytest.raises(BusinessError, match="ya has dirigido"):
        await service.start_direct(GUILD, USER)


async def test_la_plantilla_falla_menos_el_dia_que_se_dirige(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    service = await make(tmp_path, clock)
    await open_cafe(service)
    await hire_first(service)
    business = await service.business(GUILD, USER)
    assert business is not None
    business.support["direction"] = 0.8
    await service.repository.save(GUILD, business)
    leads: list[float] = []
    real = staff.play_turn

    def spy(*args: object, lead: float = 0.0, **kwargs: object) -> TurnGame:
        leads.append(lead)
        return real(*args, lead=lead, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(staff, "play_turn", spy)
    clock.now += 4.5 * 3600
    await service.staff_turns(GUILD)
    assert leads and all(lead == pytest.approx(0.8) for lead in leads)


async def test_el_consejo_es_para_sociedades_o_cadenas_y_una_vez_por_semana(
    tmp_path: Path,
) -> None:
    service = await make(tmp_path, Clock())
    await open_cafe(service, money=9_000_000)
    with pytest.raises(BusinessError, match="sociedad o varios locales"):
        await service.start_council(GUILD, USER)
    await service.open(
        GUILD, USER, sector="tienda", barrio="triana", name="Tienda Pepe", palette=1, emblem=2
    )
    game = await service.start_council(GUILD, USER)
    assert isinstance(game, DeskGame) and len(game.cards) == 8
    while game.current is not None:
        game.press(game.best(), service._clock())
    moved = await service.finish_council(GUILD, USER, game)
    owned = await service.owned(GUILD, USER)
    assert len(owned) == 2
    assert all(b.council == {"week": b.week, "score": game.score()} for b in owned)
    assert moved == pytest.approx(COUNCIL_BOOST * (game.score() - 50) / 50)
    with pytest.raises(BusinessError, match="ya se ha celebrado"):
        await service.start_council(GUILD, USER)


def test_el_consejo_mueve_los_clientes_solo_esa_semana() -> None:
    business = cafe()
    business.week = "2026-11-09"
    assert boost(business, None) == 0
    business.council = {"week": "2026-11-09", "score": 100}
    assert boost(business, None) == pytest.approx(COUNCIL_BOOST)
    business.council = {"week": "2026-11-09", "score": 0}
    assert boost(business, None) == pytest.approx(-COUNCIL_BOOST)
    business.council = {"week": "2026-11-02", "score": 100}
    assert boost(business, None) == 0


# -- El panel -----------------------------------------------------------------------------


async def test_el_cliente_especial_trae_sus_dos_botones_en_el_panel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def always(clients: list, seed: int) -> None:
        clients[0].special = "influencer"

    monkeypatch.setattr("bot.services.negocio.mark_special", always)
    _work, _cog, panel = await opened_business(tmp_path)
    await button(panel, "🔑 Abrir").callback(click())
    assert panel.screen == TURNO and panel.turn is not None
    special = SPECIAL_BY_KEY["influencer"]
    assert special.take in labels(panel) and special.skip in labels(panel)
    text = "\n".join(i.content for i in panel.walk_children() if isinstance(i, ui.TextDisplay))
    assert "Cliente especial" in text
    check_limits(panel)
    game = panel.turn.game
    await panel.game_click(click(), game.round, "s:1")
    assert game.granted is True
    assert special.take not in labels(panel)
    await panel._finish(None)
    assert panel.result is not None
    stats = business_stats(panel.result).add
    assert stats["biz_special"] == 1


async def staffed(tmp_path: Path) -> tuple[object, NegocioPanel]:
    _work, cog, panel = await opened_business(tmp_path)
    service = cog.service
    for _ in range(2):
        business = await service.business(COG_GUILD, OWNER)
        assert business is not None
        candidate = service.candidates(business)[0]
        jornada = staff.HALF_DAY if candidate["part_time"] else staff.FULL_DAY
        await service.hire(
            COG_GUILD,
            OWNER,
            candidate["key"],
            contract="indefinido",
            jornada=jornada,
            shift="manana",
        )
    await panel.show(click())
    return cog, panel


async def mesa_play(panel: NegocioPanel) -> None:
    while panel.mesa is not None:
        game = panel.mesa[1]
        ids = [
            parse_game_id(b.custom_id or "")
            for b in panel.walk_children()
            if isinstance(b, ui.Button)
        ]
        option = game.best()  # type: ignore[attr-defined]
        assert (panel.token, game.index, f"m:{option}") in ids
        check_limits(panel)
        await panel.game_click(click(), game.index, f"m:{option}")


async def test_dirigir_un_turno_entero_desde_el_panel(tmp_path: Path) -> None:
    cog, panel = await staffed(tmp_path)
    assert any(label.startswith("🧭 Dirigir") for label in labels(panel))
    assert not any(label.startswith("🏛️ Consejo") for label in labels(panel))
    await button(panel, "🧭 Dirigir").callback(click())
    assert panel.screen == MESA and panel.mesa is not None
    assert (COG_GUILD, OWNER) in cog.working  # type: ignore[attr-defined]
    await mesa_play(panel)
    assert panel.screen == PORTADA and panel.mesa is None
    assert (COG_GUILD, OWNER) not in cog.working  # type: ignore[attr-defined]
    business = await cog.service.business(COG_GUILD, OWNER)  # type: ignore[attr-defined]
    assert business is not None and business.support["direction"] > 0
    assert button(panel, "🧭 Dirigir").disabled


async def test_un_clic_viejo_en_dirigir_no_cuenta(tmp_path: Path) -> None:
    _cog, panel = await staffed(tmp_path)
    await button(panel, "🧭 Dirigir").callback(click())
    assert panel.mesa is not None
    game = panel.mesa[1]
    await panel.game_click(click(), game.index + 5, "m:0")
    assert game.index == 0
    await panel._finish_mesa(None)


async def test_sin_plantilla_no_sale_el_boton_de_dirigir(tmp_path: Path) -> None:
    _work, _cog, panel = await opened_business(tmp_path)
    assert not any(label.startswith("🧭 Dirigir") for label in labels(panel))


def test_dirigir_y_el_consejo_cuentan_para_sus_logros() -> None:
    game = directing()
    while game.current is not None:
        game.press(game.best(), NOW + 1)
    assert mesa_stats("dirigir", game).add == {"biz_direct": 1, "biz_direct_perfect": 1}
    council = direct.council_game(random.Random(2), now=NOW)
    council.ordago = ORDAGO_PASS
    while council.current is not None:
        council.press(council.best(), NOW + 1)
    add = mesa_stats("consejo", council).add
    assert add["biz_council"] == 1 and "biz_council_crisis" not in add
    assert len(SPECIALS) == 4
