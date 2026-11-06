"""Pruebas del cog `pisos`: el panel en imágenes, la navegación, la caja y cada noche."""

from __future__ import annotations

import asyncio
import random
import sqlite3
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
from interaction_fakes import fake_interaction

from bot.cogs.pisos import (
    BARRIO,
    CACHE_SIZE,
    CAJA,
    CALLES,
    CANDIDATOS,
    CARTERA,
    FICHA,
    GESTION,
    MAPA,
    VPO,
    Painter,
    Panel,
    Pisos,
    SharesModal,
    TenantOffer,
    ecb_embed,
    effort_text,
    news_embed,
    promotion_embed,
    requirements_text,
)
from bot.repositories.economy import EconomyRepository, LedgerEntry
from bot.repositories.housing import DayRun, HousingRepository, Market, MemberDay, News
from bot.repositories.housing_finance import HOME_RENTED, Promotion
from bot.services import housing as rules
from bot.services.achievements import PISOS_EVENT_PREFIX
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.housing_catalog import BARRIOS, KINDS, listings_of
from bot.services.housing_market import HousingService
from bot.services.levels import TIMEZONE

GUILD = 1


class FakeScene:
    """Pinta «PNG» de mentira y cuenta cuántas pantallas le piden."""

    def __init__(self) -> None:
        self.painted: list[dict[str, Any]] = []

    async def paint(self, states: list[dict[str, Any]]) -> list[bytes]:
        self.painted.extend(states)
        return [f"png:{state['screen']}:{len(self.painted)}".encode() for state in states]

    async def close(self) -> None:
        return None


class Clock:
    def __init__(self, when: datetime) -> None:
        self.now = when.timestamp()

    def __call__(self) -> float:
        return self.now


def member(user_id: int, name: str) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = name
    user.bot = False
    return user


async def make(tmp_path: Path) -> tuple[Pisos, FakeScene, Clock]:
    clock = Clock(datetime(2026, 10, 14, 12, tzinfo=TIMEZONE))
    economy_repo = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await economy_repo.initialize()
    housing = HousingRepository(tmp_path / "bot.db")
    await housing.initialize()
    economy = EconomyService(economy_repo, clock=clock)
    service = HousingService(housing, economy, clock=clock, rng=random.Random(3))
    scene = FakeScene()
    cog = Pisos(MagicMock(), economy=economy, repository=housing, service=service,
                renderer=scene)  # type: ignore[arg-type]  # fmt: skip
    return cog, scene, clock


def guild_with(*members: MagicMock) -> MagicMock:
    guild = MagicMock(spec=discord.Guild)
    guild.id = GUILD
    by_id = {m.id: m for m in members}
    guild.get_member = by_id.get
    return guild


async def open_panel(cog: Pisos, owner: MagicMock, guild: MagicMock) -> Panel:
    ctx = MagicMock()
    ctx.guild = guild
    ctx.author = owner
    ctx.channel = MagicMock()
    ctx.send = AsyncMock(return_value=MagicMock(spec=discord.Message))
    await cog.pisos_text.callback(cog, ctx)  # type: ignore[attr-defined]
    kwargs = ctx.send.await_args.kwargs
    assert isinstance(kwargs["file"], discord.File)
    assert kwargs.get("embed") is None  # solo imagen
    panel = kwargs["view"]
    assert isinstance(panel, Panel)
    return panel


def clicked(user: MagicMock, events: list[str], **data: Any) -> MagicMock:
    interaction = fake_interaction(user, events=events)
    interaction.data = data
    interaction.channel = MagicMock()
    return interaction


async def test_el_panel_abre_el_mapa_y_pinta_los_barrios_por_adelantado(tmp_path: Path) -> None:
    cog, scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    panel = await open_panel(cog, ana, guild_with(ana))

    assert panel.screen == MAPA
    assert scene.painted[0]["screen"] == "mapa"
    await cog.painter._worker  # el adelanto: los 20 barrios y la cartera
    screens = [s["screen"] for s in scene.painted[1:]]
    # Desde la 9.15.0 el barrio se abre por su plano de calles.
    assert screens.count("calles") == len(BARRIOS) and "cartera" in screens


async def test_el_adelanto_pinta_de_uno_en_uno_y_lo_mas_probable_primero(tmp_path: Path) -> None:
    cog, scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(5_000_000, "prueba")])
    await cog.service.buy(GUILD, ana.id, "taco_bajo", 100)
    sizes: list[int] = []
    paint = scene.paint

    async def counted(states: list[dict[str, Any]]) -> list[bytes]:
        sizes.append(len(states))
        await asyncio.sleep(0)  # un dibujo de verdad deja pasar a los demás
        return await paint(states)

    scene.paint = counted  # type: ignore[method-assign]
    await open_panel(cog, ana, guild_with(ana))
    await cog.painter._worker

    assert set(sizes) == {1}  # nunca varias pantallas de golpe
    order = [s["screen"] for s in scene.painted[1:]]
    assert order[:2] == ["cartera", "vpo"]  # lo que se pulsa más, primero
    assert scene.painted[3]["barrio"]["key"] == "taco"  # y los barrios donde ya tienes algo


async def test_un_clic_sobre_lo_que_se_pinta_por_adelantado_espera_y_no_lo_pinta_otra_vez() -> None:
    scene, gate = FakeScene(), asyncio.Event()
    paint = scene.paint

    async def held(states: list[dict[str, Any]]) -> list[bytes]:
        await gate.wait()
        return await paint(states)

    scene.paint = held  # type: ignore[method-assign]
    painter = Painter(scene)  # type: ignore[arg-type]
    state = {"screen": "mapa", "x": 1}
    painter.prefetch([state])
    await asyncio.sleep(0)  # el adelanto ya está pintando (parado en la puerta)
    click = asyncio.create_task(painter.paint([dict(state)]))
    await asyncio.sleep(0)
    assert not click.done()

    gate.set()
    assert await click == await painter.paint([state])
    assert len(scene.painted) == 1


async def test_el_adelanto_suelta_lo_que_ya_no_vale() -> None:
    scene, valid = FakeScene(), {"yes": True}
    paint = scene.paint

    async def changing(states: list[dict[str, Any]]) -> list[bytes]:
        valid["yes"] = False  # el panel cambia de pantalla mientras se pinta la primera
        return await paint(states)

    scene.paint = changing  # type: ignore[method-assign]
    painter = Painter(scene)  # type: ignore[arg-type]
    painter.prefetch(
        [{"screen": "a"}, {"screen": "b"}, {"screen": "c"}], still=lambda: valid["yes"]
    )
    assert painter._worker is not None
    await painter._worker
    assert [s["screen"] for s in scene.painted] == ["a"]


async def test_desde_la_ficha_y_la_caja_se_pinta_antes_el_mapa_la_cartera_y_la_ficha(
    tmp_path: Path,
) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    panel = await open_panel(cog, ana, guild_with(ana))
    panel.listing = "taco_bajo"
    panel.screen = FICHA
    assert (MAPA, {}) in panel._neighbours() and (CARTERA, {}) in panel._neighbours()
    panel.screen = BARRIO
    panel.barrio = "taco"
    # Los nueve inmuebles de Taco (el solar de la décima tarjeta no tiene ficha).
    assert [s for s, _ in panel._neighbours()].count(FICHA) == 9 and (
        CARTERA,
        {},
    ) in panel._neighbours()
    panel.screen = CAJA
    assert panel._neighbours() == [(FICHA, {"listing": "taco_bajo"}), (CARTERA, {}), (MAPA, {})]


def promotion_in(barrio: str) -> Market:
    """Un mercado con la obra nueva de `barrio` (la décima tarjeta, en lugar del solar)."""
    listing = rules.new_promotion(1, [barrio], random.Random(7))
    return Market(promotions={listing.key: Promotion(listing, 10, date(2026, 10, 19))})


def barrio_buttons(panel: Panel) -> dict[str, discord.ui.Button]:
    """Los botones de la pantalla del barrio por su `custom_id`."""
    return {b.custom_id: b for b in panel.children if isinstance(b, discord.ui.Button)}


async def test_el_barrio_pone_sus_botones_en_dos_filas_de_cinco_como_las_tarjetas(
    tmp_path: Path,
) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    panel = await open_panel(cog, ana, guild_with(ana))
    panel.screen, panel.barrio = BARRIO, "taco"
    panel.rebuild()
    buttons = barrio_buttons(panel)

    for slot, item in enumerate(listings_of("taco")):  # los 9 del catálogo
        button = buttons[f"pisos:ver:{slot}"]
        assert button.label == f"{slot + 1} · {KINDS[item.kind].name}"
        assert button.row == 1 + slot // 5 and not button.disabled
    # La décima tarjeta de la imagen es el solar: su hueco en la rejilla, apagado.
    solar = buttons["pisos:ver:9"]
    assert solar.label == "🚧 10 · Solar" and solar.disabled and solar.row == 2
    assert buttons["pisos:mapa"].row == buttons["pisos:cartera"].row == 3
    rows = [b.row for b in buttons.values()]
    assert [rows.count(r) for r in (1, 2, 3)] == [5, 5, 3]
    assert buttons["pisos:calles"].row == 3  # 🗺️ Calles (9.14.0)
    assert len(panel.children) == 1 + 13 <= 25  # el desplegable y los trece botones
    # Un clic atrasado en el hueco del solar no abre nada.
    await panel._open_listing(clicked(ana, [], custom_id="pisos:ver:9"))
    assert panel.screen == BARRIO


async def test_con_obra_nueva_la_decima_tarjeta_es_un_boton_que_abre_su_ficha(
    tmp_path: Path,
) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    panel = await open_panel(cog, ana, guild_with(ana))
    panel.market = promotion_in("taco")
    panel.screen, panel.barrio = BARRIO, "taco"
    panel.rebuild()
    buttons = barrio_buttons(panel)

    obra = buttons["pisos:ver:9"]
    kind = KINDS[panel.market.promotions["obra_1"].listing.kind].name
    assert obra.label == f"🏗️ 10 · {kind}" and not obra.disabled and obra.row == 2
    assert not any("Solar" in (b.label or "") for b in buttons.values())
    assert len(panel.children) == 1 + 13 <= 25
    panel._go = AsyncMock()  # type: ignore[method-assign]  # sin base de datos: el mercado es de mentira
    click = clicked(ana, [], custom_id="pisos:ver:9")
    await panel._open_listing(click)
    panel._go.assert_awaited_once_with(click, FICHA)
    assert panel.listing == "obra_1"


async def test_desde_un_barrio_se_precargan_las_fichas_una_a_una_el_mapa_y_la_cartera(
    tmp_path: Path,
) -> None:
    cog, scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    panel = await open_panel(cog, ana, guild_with(ana))
    await cog.painter._worker
    sizes: list[int] = []
    paint = scene.paint

    async def counted(states: list[dict[str, Any]]) -> list[bytes]:
        sizes.append(len(states))
        await asyncio.sleep(0)
        return await paint(states)

    scene.paint = counted  # type: ignore[method-assign]
    for market, fichas in ((Market(), 9), (promotion_in("taco"), 10)):
        cog.painter.cache.clear()
        scene.painted.clear()
        panel.market = market
        panel.screen, panel.barrio = BARRIO, "taco"
        await panel._prefetch()
        await cog.painter._worker
        painted = [(s["screen"], s.get("key")) for s in scene.painted]
        # Cada inmueble en el orden de sus botones; el solar no tiene ficha que pintar.
        keys = [key for screen, key in painted if screen == "ficha"]
        assert keys == [item.key for item in market.listings_of("taco")] and len(keys) == fichas
        assert sorted(screen for screen, _ in painted) == sorted(
            ["calles", "cartera", "mapa"] + ["ficha"] * fichas
        )
    assert set(sizes) == {1}  # de una en una, nunca varias a la vez


async def test_el_adelanto_de_un_barrio_se_suelta_si_se_cambia_de_pantalla(tmp_path: Path) -> None:
    cog, scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    panel = await open_panel(cog, ana, guild_with(ana))
    await cog.painter._worker
    cog.painter.cache.clear()
    scene.painted.clear()
    panel.screen, panel.barrio = BARRIO, "taco"
    paint = scene.paint

    async def leaving(states: list[dict[str, Any]]) -> list[bytes]:
        panel.screen = MAPA  # se va al mapa mientras se pinta la primera ficha
        return await paint(states)

    scene.paint = leaving  # type: ignore[method-assign]
    await panel._prefetch()
    await cog.painter._worker
    assert [s["screen"] for s in scene.painted] == ["ficha"]  # el resto ya no vale


async def test_la_cache_aguanta_los_20_barrios_y_lo_que_se_precarga_desde_un_barrio() -> None:
    assert CACHE_SIZE >= len(BARRIOS) + 13  # los 20 barrios del mapa y los 13 de un barrio
    painter = Painter(FakeScene())  # type: ignore[arg-type]
    states = [{"screen": "ficha", "n": n} for n in range(CACHE_SIZE + 5)]
    for state in states:
        await painter.paint([state])
    assert len(painter.cache) == CACHE_SIZE
    assert painter.key(states[0]) not in painter.cache  # la más vieja se suelta
    assert painter.key(states[-1]) in painter.cache


async def test_navegar_contesta_antes_de_pintar_y_no_resube_lo_que_ya_se_ve(
    tmp_path: Path,
) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    panel = await open_panel(cog, ana, guild_with(ana))
    events: list[str] = []

    barrio = BARRIOS[4].key
    interaction = clicked(ana, events, values=[barrio])
    await panel._pick_barrio(interaction)

    assert events[0] == "response.defer"
    assert panel.screen == CALLES and panel.barrio == barrio  # el barrio, por sus calles
    assert "attachments" in interaction.edit_original_response.await_args.kwargs

    again = clicked(ana, [], values=[barrio])
    await panel._pick_barrio(again)
    assert "attachments" not in again.edit_original_response.await_args.kwargs

    slot = clicked(ana, [], custom_id="pisos:ver:2")
    await panel._open_listing(slot)
    assert panel.screen == FICHA and panel.listing == listings_of(barrio)[2].key


async def test_solo_el_duenio_mueve_su_panel(tmp_path: Path) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana, bea = member(10, "Ana"), member(20, "Bea")
    panel = await open_panel(cog, ana, guild_with(ana, bea))
    interaction = fake_interaction(bea)
    assert await panel.interaction_check(interaction) is False
    interaction.response.send_message.assert_awaited_once()


async def test_comprar_por_la_caja_cobra_y_sella_pagado(tmp_path: Path) -> None:
    cog, scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(5_000_000, "prueba")])
    panel = await open_panel(cog, ana, guild_with(ana))
    panel.barrio = "schamann"
    await panel._open_listing(clicked(ana, [], custom_id="pisos:ver:1"))
    assert panel.listing == "schamann_bajo"

    form = fake_interaction(ana)
    await panel._buy(form)
    modal = form.response.send_modal.await_args.args[0]
    assert isinstance(modal, SharesModal) and modal.most == rules.SHARES

    modal.amount._value = "40"  # lo que escribe en el formulario
    submit = clicked(ana, [])
    await modal.on_submit(submit)
    assert panel.screen == CAJA and panel.pending == (False, 40)
    preview = scene.painted[-1] if scene.painted[-1]["screen"] == "caja" else None
    assert preview is None or preview["stamp"] is None

    pay = clicked(ana, [])
    await panel._pay(pay)

    assert panel.done is not None and panel.done[0] == "PAGADO"
    market = await cog.service.market(GUILD)
    assert market.shares_of("schamann_bajo") == {ana.id: 40}
    total = panel.done[1].quote.total
    assert await cog.economy.balance(GUILD, ana.id) == STARTING_BALANCE + 5_000_000 - total
    painted = [s for s in scene.painted if s["screen"] == "caja" and s["stamp"] == "PAGADO"]
    assert painted

    await panel._go_portfolio(clicked(ana, []))
    assert panel.screen == CARTERA


async def test_sin_dinero_la_caja_avisa_y_no_compra(tmp_path: Path) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    panel = await open_panel(cog, ana, guild_with(ana))
    panel.listing = "adeje_chalet"
    panel.screen, panel.pending = CAJA, (False, rules.SHARES)
    pay = clicked(ana, [])

    await panel._pay(pay)

    assert panel.done is None
    assert "No te llega" in pay.followup.send.await_args.args[0]
    assert (await cog.service.market(GUILD)).holdings == {}


async def test_el_pintor_no_pinta_dos_veces_la_misma_pantalla() -> None:
    scene = FakeScene()
    painter = Painter(scene)  # type: ignore[arg-type]
    state = {"screen": "mapa", "x": 1}
    first = await painter.paint([state])
    second = await painter.paint([dict(state)])
    assert first == second and len(scene.painted) == 1


async def test_cada_noche_se_cierran_los_dias_sin_romper_nada(tmp_path: Path) -> None:
    cog, _scene, clock = await make(tmp_path)
    await cog.economy.repository.apply(GUILD, 10, [LedgerEntry(5_000_000, "prueba")])
    await cog.service.buy(GUILD, 10, "taco_bajo", 100)
    clock.now += 86_400

    first = await cog.close_guild(GUILD)
    again = await cog.close_guild(GUILD)
    clock.now += 2 * 86_400
    later = await cog.close_guild(GUILD)

    # La primera vez solo se cierra ayer; luego, todos los días que falten.
    assert [r.day.day for r in first] == [14] and again == []
    assert [r.day.day for r in later] == [15, 16]
    assert (await cog.service.market(GUILD)).leases["taco_bajo"].arrears >= 0


async def test_la_funcion_puente_de_saldo_no_rompe_sin_el_cog() -> None:
    from bot.cogs import pisos

    bot = SimpleNamespace(get_cog=lambda _name: None, cogs={})
    assert await pisos.pending(bot, GUILD, 10) == []  # type: ignore[arg-type]


async def test_un_segundo_clic_en_pagar_no_vuelve_a_cobrar(tmp_path: Path) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(5_000_000, "prueba")])
    panel = await open_panel(cog, ana, guild_with(ana))
    await panel.refresh()
    panel.listing, panel.screen, panel.pending = "taco_trastero", CAJA, (False, 10)

    await panel._pay(clicked(ana, []))
    balance = await cog.economy.balance(GUILD, ana.id)
    await panel._pay(clicked(ana, []))

    assert await cog.economy.balance(GUILD, ana.id) == balance
    assert (await cog.service.market(GUILD)).shares_of("taco_trastero") == {ana.id: 10}


async def test_si_otro_compra_en_el_barrio_la_caja_se_actualiza_y_no_cobra(
    tmp_path: Path,
) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana, bea = member(10, "Ana"), member(20, "Bea")
    for user in (ana, bea):
        await cog.economy.repository.apply(GUILD, user.id, [LedgerEntry(5_000_000, "prueba")])
    panel = await open_panel(cog, ana, guild_with(ana, bea))
    await panel.refresh()
    # (Un trastero de 52.000 Y$ no se nota: el precio se redondea y no cambiaría.)
    panel.listing, panel.screen, panel.pending = "taco_bajo", CAJA, (False, 10)
    await cog.service.buy(GUILD, bea.id, "taco_piso3", 100)  # sube el índice de Taco
    before = await cog.economy.balance(GUILD, ana.id)
    pay = clicked(ana, [])

    await panel._pay(pay)

    assert panel.done is None
    assert "ha cambiado" in pay.followup.send.await_args.args[0]
    assert await cog.economy.balance(GUILD, ana.id) == before
    await panel._pay(clicked(ana, []))  # con la caja ya actualizada, sí
    assert panel.done is not None


async def test_un_fallo_en_un_servidor_no_para_la_tarea_de_cada_noche(tmp_path: Path) -> None:
    cog, _scene, _clock = await make(tmp_path)
    broken = AsyncMock(side_effect=[RuntimeError("roto"), []])
    cog.close_guild = broken  # type: ignore[method-assign]
    cog.bot.guilds = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
    await cog._nightly.coro(cog)
    assert broken.await_count == 2


async def test_todas_las_pantallas_respetan_los_limites_de_discord(tmp_path: Path) -> None:
    """Etiquetas, opciones y filas: Discord rechaza el mensaje entero si una se pasa."""
    from bot.services.housing_catalog import LISTINGS

    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(10**9, "prueba")])
    for listing in LISTINGS[:30]:
        await cog.service.buy(GUILD, ana.id, listing.key, 60)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute("UPDATE housing_leases SET status = 'impago', tenant = 'familia'")
        # Un piso vacío con tres candidatos: la pantalla con más botones de la fase 2.
        connection.execute(
            "UPDATE housing_leases SET status = 'vacio', tenant = NULL, pick = 'yo' "
            "WHERE listing = 'vegueta_edificio'"
        )
        for tenant in ("medico", "abuela", "familia"):
            connection.execute(
                "INSERT INTO housing_candidates (guild_id, listing, tenant, until) "
                "VALUES (?, 'vegueta_edificio', ?, '2026-12-31')",
                (GUILD, tenant),
            )
    for listing in LISTINGS[:6]:
        await cog.service.create_auction(GUILD, ana.id, listing.key, 10, 1_000)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        for event in ("averia", "humedades", "vecino"):
            connection.execute(
                "INSERT INTO housing_decisions (guild_id, listing, event, cost, day, until) "
                "VALUES (?, 'vegueta_edificio', ?, 99999, '2026-10-14', '2026-10-16')",
                (GUILD, event),
            )
    # Una junta con cuatro votaciones abiertas (la pantalla con más botones de la fase 6).
    bea = member(20, "Bea")
    await cog.economy.repository.apply(GUILD, bea.id, [LedgerEntry(10**9, "prueba")])
    await cog.service.buy(GUILD, ana.id, "vegueta_edificio", 60)
    await cog.service.buy(GUILD, bea.id, "vegueta_edificio", 40)
    for kind, value in (("renta", "mercado"), ("seguro", "1"), ("eleccion", "gestora"),
                        ("venta", "")):  # fmt: skip
        await cog.service.propose(GUILD, ana.id, "vegueta_edificio", kind, value)
    panel = await open_panel(cog, ana, guild_with(ana, bea))
    await panel.refresh()
    for screen, pending in ((MAPA, None), (BARRIO, None), (FICHA, None), (CARTERA, None),
                            (CAJA, (False, 10)), (CAJA, (True, 10)), (VPO, None),
                            (GESTION, None), (CANDIDATOS, None), ("subastas", None),
                            ("reformas", None), ("imprevistos", None), ("resenas", None),
                            ("junta", None), ("calles", None), ("dividir", None),
                            ("edificio", None), ("llaves", None), ("solar", None)):  # fmt: skip
        panel.screen, panel.pending, panel.done = screen, pending, None
        # 🏗️ Solares con más botones: un solar del centro (los cinco proyectos) y P10.
        panel.solar, panel.level = "triana_c1_s", 10
        panel.barrio, panel.listing = "vegueta", "vegueta_edificio"
        panel.rebuild()
        rows: dict[int, int] = {}
        for item in panel.children:
            rows[item.row or 0] = rows.get(item.row or 0, 0) + (
                5 if isinstance(item, discord.ui.Select) else 1
            )
            if isinstance(item, discord.ui.Button):
                assert len(item.label or "") <= 80
                assert len(item.custom_id or "") <= 100
            if isinstance(item, discord.ui.Select):
                assert 1 <= len(item.options) <= 25
                assert len(item.placeholder or "") <= 150
                for option in item.options:
                    assert len(option.label) <= 100
                    assert len(option.description or "") <= 100
        assert len(panel.children) <= 25 and max(rows) <= 4
        assert all(width <= 5 for width in rows.values()), (screen, rows)
        # Discord rechaza el mensaje entero si dos componentes repiten `custom_id` (9.15.1).
        ids = [getattr(item, "custom_id", None) for item in panel.children]
        assert len(ids) == len(set(ids)), (screen, sorted(i for i in ids if ids.count(i) > 1))
    for selling, most in ((False, 100), (True, 60)):
        modal = SharesModal(panel, selling=selling, most=most)
        assert len(modal.title) <= 45
        assert len(modal.amount.label) <= 45
        assert len(modal.amount.placeholder or "") <= 100


async def test_abrir_otro_panel_para_el_anterior_del_mismo_miembro(tmp_path: Path) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana, bea = member(10, "Ana"), member(20, "Bea")
    guild = guild_with(ana, bea)
    first = await open_panel(cog, ana, guild)
    other = await open_panel(cog, bea, guild)
    second = await open_panel(cog, ana, guild)
    assert first.is_finished() and first not in cog.panels
    assert second in cog.panels and other in cog.panels


def salary(tmp_path: Path, user_id: int, net_per_day: int, clock: Clock) -> None:
    """Nóminas de los últimos 30 días que suman `net_per_day` × 30 (lo que mira el banco)."""
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "INSERT INTO economy_payroll (guild_id, user_id, concept, gross, ss_worker, "
            "ss_employer, irpf, net, created_at) VALUES (?, ?, 'pala:obra', ?, 0, 0, 0, ?, ?)",
            (GUILD, user_id, net_per_day * 30, net_per_day * 30, clock.now - 3600),
        )


def ids(panel: Panel) -> list[str]:
    return [getattr(item, "custom_id", "") for item in panel.children]


async def test_la_caja_compra_con_hipoteca_y_luego_se_amortiza_con_dos_clics(
    tmp_path: Path,
) -> None:
    cog, scene, clock = await make(tmp_path)
    ana = member(10, "Ana")
    salary(tmp_path, ana.id, 20_000, clock)
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(1_000_000, "prueba")])
    panel = await open_panel(cog, ana, guild_with(ana))
    panel.listing = "schamann_bajo"
    await panel.open_checkout(clicked(ana, []), selling=False, shares=rules.SHARES)
    assert "pisos:financiar" in ids(panel)

    await panel._toggle_mortgage(clicked(ana, []))
    assert panel.financed
    labels = [line["label"] for line in scene.painted[-1]["lines"]]
    assert "Sale de tu bolsillo" in labels
    await panel._pay(clicked(ana, []))

    purchase = panel.done[1]  # type: ignore[index]
    assert purchase.loan == round(purchase.quote.price * rules.MORTGAGE_LTV)
    pocket = purchase.quote.total - purchase.loan
    assert await cog.economy.balance(GUILD, ana.id) == STARTING_BALANCE + 1_000_000 - pocket

    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(10_000_000, "prueba")])
    await panel._back_to_listing(clicked(ana, []))
    assert "pisos:amortizar" in ids(panel)
    arm = clicked(ana, [], custom_id="pisos:amortizar")
    await panel._arm(arm)
    arm.response.edit_message.assert_awaited_once()
    assert "pisos:amortizar:si" in ids(panel)  # el primer clic no cobra
    assert (await cog.service.market(GUILD)).mortgages_of(ana.id)

    await panel._amortize(clicked(ana, []))
    assert not (await cog.service.market(GUILD)).mortgages_of(ana.id)
    assert panel.confirm is None and "pisos:amortizar" not in ids(panel)


async def test_sin_nomina_la_hipoteca_avisa_y_no_compra(tmp_path: Path) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    panel = await open_panel(cog, ana, guild_with(ana))
    panel.listing = "schamann_bajo"
    panel.screen, panel.pending, panel.financed = CAJA, (False, rules.SHARES), True
    pay = clicked(ana, [])

    await panel._pay(pay)

    assert panel.done is None
    assert "banco" in pay.followup.send.await_args.args[0]
    assert (await cog.service.market(GUILD)).holdings == {}


async def test_la_vpo_se_apunta_se_sortea_y_se_compra_desde_su_pantalla(tmp_path: Path) -> None:
    cog, scene, clock = await make(tmp_path)
    ana = member(10, "Ana")
    panel = await open_panel(cog, ana, guild_with(ana))
    await panel._go_vpo(clicked(ana, []))
    shown = scene.painted[-1]
    assert shown["screen"] == "vpo" and shown["next_draw"] == "lunes 19/10"
    assert "pisos:sorteo" in ids(panel)

    await panel._vpo_signup(clicked(ana, []))
    assert ana.id in (await cog.service.market(GUILD)).signups
    clock.now += 6 * 86_400  # martes 20: se cierra el lunes 19, que sortea
    (run,) = await cog.close_guild(GUILD)
    assert run.draws and run.draws[0][0] == ana.id

    salary(tmp_path, ana.id, 6_000, clock)
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(50_000, "notaría")])
    await panel._go_vpo(clicked(ana, []))
    await panel._arm(clicked(ana, [], custom_id="pisos:comprar_vpo"))
    buy = clicked(ana, [])
    await panel._vpo_buy(buy)
    market = await cog.service.market(GUILD)
    assert market.vpo_of(ana.id) == run.draws[0][1]
    assert "pisos:devolver" in ids(panel)

    # El Estado la recompra con lo que cobró al venderla.
    await panel._arm(clicked(ana, [], custom_id="pisos:devolver"))
    await panel._vpo_return(clicked(ana, []))
    market = await cog.service.market(GUILD)
    assert market.vpo_of(ana.id) is None and ana.id not in market.homes
    assert "pisos:sorteo" in ids(panel)


async def test_alquilar_a_un_miembro_le_pide_que_acepte(tmp_path: Path) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana, bea = member(10, "Ana"), member(20, "Bea")
    bea.mention = "<@20>"
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(5_000_000, "prueba")])
    await cog.economy.repository.apply(GUILD, bea.id, [LedgerEntry(100_000, "fianza")])
    await cog.service.buy(GUILD, ana.id, "taco_bajo", rules.SHARES)
    panel = await open_panel(cog, ana, guild_with(ana, bea))
    panel.barrio, panel.listing = "taco", "taco_bajo"
    await panel._back_to_listing(clicked(ana, []))
    assert {"pisos:vivir", "pisos:miembro", "pisos:licencia"} <= set(ids(panel))

    await panel._arm(clicked(ana, [], custom_id="pisos:miembro"))
    assert "pisos:inquilino" in ids(panel)
    pick = clicked(ana, [], values=[str(bea.id)])
    await panel._pick_tenant(pick)
    offer = pick.followup.send.await_args.kwargs["view"]
    assert isinstance(offer, TenantOffer) and offer.tenant is bea
    text = pick.followup.send.await_args.kwargs["embed"].description
    assert "nómina" in text and "garantía adicional" in text  # Bea no cobra nómina

    stranger = fake_interaction(ana)
    await offer.accept.callback(stranger)  # type: ignore[attr-defined]
    stranger.response.send_message.assert_awaited_once()  # no es para ella

    yes = clicked(bea, [])
    await offer.accept.callback(yes)  # type: ignore[attr-defined]
    market = await cog.service.market(GUILD)
    assert market.leases["taco_bajo"].member == bea.id
    assert market.homes[bea.id] == (HOME_RENTED, "taco_bajo")
    assert offer.is_finished()
    rent = market.leases["taco_bajo"].rent
    deposit = rules.deposit_for(rent, guarantee=True)
    assert await cog.economy.balance(GUILD, bea.id) == STARTING_BALANCE + 100_000 - deposit


def test_la_tasa_de_esfuerzo_habla_como_el_banco_de_espana() -> None:
    assert "sobreesfuerzo" in effort_text(4_000, 6_000)
    assert "unicornio" in effort_text(1_000, 6_000)
    assert "casero" in effort_text(9_000, 6_000)
    assert "nada" in effort_text(4_000, 0)
    assert "ni aval" in requirements_text(1_000, 6_000)
    assert "padres" in requirements_text(4_000, 6_000)


async def test_el_lunes_francfort_habla_en_el_canal_si_hay_mercado(tmp_path: Path) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    channel = MagicMock(spec=discord.TextChannel)
    channel.name = "chat-general"
    channel.send = AsyncMock()
    guild = guild_with(ana)
    guild.text_channels = [channel]
    cog.bot.get_guild = lambda _id: guild
    monday = datetime(2026, 10, 19).date()
    await cog._announce_ecb(GUILD, monday, 0.02, 0.0225)
    channel.send.assert_not_awaited()  # nadie tiene pisos: no se molesta a nadie

    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(5_000_000, "prueba")])
    await cog.service.buy(GUILD, ana.id, "taco_bajo", rules.SHARES)
    await cog._announce_ecb(GUILD, monday, 0.02, 0.0225)
    embed = channel.send.await_args.kwargs["embed"]
    assert rules.ecb_phrase(0.02, 0.0225, monday) in embed.description
    assert "2,00 % → 2,25 %" in embed.description and "3,25 % fijo" in embed.description
    assert isinstance(ecb_embed(0.02, 0.02, monday), discord.Embed)


async def test_si_ejecutan_lo_que_avalaron_tus_padres_lo_paga_la_familia(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from bot.cogs import pisos

    cog, _scene, _clock = await make(tmp_path)
    run = DayRun(day=datetime(2026, 10, 20).date())
    run.aval_executions = [10]
    cog.service.close_days = AsyncMock(return_value=[run])  # type: ignore[method-assign]
    cog.tax_stats = AsyncMock(return_value={})  # type: ignore[method-assign]
    lost: list[int] = []
    noted: dict[int, Any] = {}

    async def lose(_bot: object, _guild: int, who: int) -> None:
        lost.append(who)

    def note(_bot: object, _guild: int, who: int, delta: Any, *_rest: object) -> None:
        noted[who] = delta

    monkeypatch.setattr(pisos.curro, "parents_lose_home", lose)
    monkeypatch.setattr(pisos.logros, "note", note)
    await cog.close_guild(GUILD)
    assert lost == [10]
    assert noted[10].add[f"{PISOS_EVENT_PREFIX}ejecucion_aval"] == 1


async def test_cada_noche_lo_personal_va_solo_a_su_miembro(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from bot.cogs import pisos

    cog, _scene, _clock = await make(tmp_path)
    run = DayRun(day=datetime(2026, 10, 19).date())
    run.news = [
        News("taco_bajo", run.day, "ejecucion", 1, 10, None),
        News("taco_bajo", run.day, "impago", 1, 30, None),  # 30: el miembro de alquiler
    ]
    run.owners = {"taco_bajo": (10, 20)}
    run.members = {20: MemberDay(rent=100)}
    run.mortgage_unpaid, run.bono, run.installments = [20], {30: 500}, {10: 1}
    cog.service.close_days = AsyncMock(return_value=[run])  # type: ignore[method-assign]
    cog.tax_stats = AsyncMock(return_value={})  # type: ignore[method-assign]
    noted: dict[int, Any] = {}

    def note(_bot: object, _guild: int, who: int, delta: Any, *_rest: object) -> None:
        noted[who] = delta

    monkeypatch.setattr(pisos.logros, "note", note)

    await cog.close_guild(GUILD)

    ev = PISOS_EVENT_PREFIX
    assert noted[10].add[f"{ev}ejecucion"] == 1 and noted[10].add["pisos_installments"] == 1
    assert f"{ev}ejecucion" not in noted[20].add
    assert noted[20].add[f"{ev}cuota_impagada"] == 1 and noted[20].add[f"{ev}impago"] == 1
    assert noted[30].add == {"pisos_bono": 500}


async def test_la_obra_nueva_se_anuncia_y_apunta_el_logro_del_promotor(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from bot.cogs import pisos

    cog, _scene, clock = await make(tmp_path)
    clock.now = datetime(2026, 10, 20, 12, tzinfo=TIMEZONE).timestamp()  # martes
    ana = member(10, "Ana")
    channel = MagicMock(spec=discord.TextChannel)
    channel.name = "chat-general"
    channel.send = AsyncMock()
    guild = guild_with(ana)
    guild.text_channels = [channel]
    cog.bot.get_guild = lambda _id: guild
    asked: list[int] = []

    async def builders(_bot: object, guild_id: int) -> tuple[int, ...]:
        asked.append(guild_id)
        return (ana.id,)

    noted: list[tuple[int, Any]] = []

    def note(_bot: object, _guild: int, who: int, delta: Any, *_rest: object) -> None:
        noted.append((who, delta))

    monkeypatch.setattr(pisos.curro, "ladrillo_builders", builders)
    monkeypatch.setattr(pisos.logros, "note", note)
    runs = await cog.close_guild(GUILD)  # cierra el lunes 19
    assert asked == [GUILD]
    (run,) = [r for r in runs if r.promotions]
    embed = channel.send.await_args_list[-1].kwargs["embed"]
    assert "Ana" in embed.description and "5 %" in embed.description
    assert any(who == ana.id and d.add.get("pisos_promotions") == 1 for who, d in noted)
    listing = (await cog.service.market(GUILD)).listing(run.promotions[0][1])
    assert listing is not None
    assert promotion_embed(listing, 1.0, "Ana").title.endswith(listing.title.split(": ")[1])


# -- Fase 2 de la ampliación (8.1.0) ------------------------------------------------------


async def test_gestionar_elige_inquilino_entre_candidatos_y_firma(tmp_path: Path) -> None:
    cog, _scene, clock = await make(tmp_path)
    ana = member(10, "Ana")
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(5_000_000, "prueba")])
    await cog.service.buy(GUILD, ana.id, "taco_bajo", rules.SHARES)
    panel = await open_panel(cog, ana, guild_with(ana))
    panel.barrio, panel.listing = "taco", "taco_bajo"
    await panel._back_to_listing(clicked(ana, []))
    assert "pisos:gestion" in ids(panel)
    await panel._go_gestion(clicked(ana, []))
    assert panel.screen == GESTION and "pisos:elegir" in ids(panel)
    assert "pisos:seguro" in ids(panel)

    events: list[str] = []
    await panel._toggle_pick(clicked(ana, events))
    assert events[0] == "response.defer"  # contesta antes de tocar la base de datos
    market = await cog.service.market(GUILD)
    assert market.leases["taco_bajo"].pick == rules.PICK_SELF
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "INSERT INTO housing_candidates (guild_id, listing, tenant, until) "
            "VALUES (?, 'taco_bajo', 'medico', '2026-10-17')",
            (GUILD,),
        )
    await panel._go_candidates(clicked(ana, []))
    assert panel.screen == CANDIDATOS
    assert {"pisos:firmar:1", "pisos:descartar:1"} <= set(ids(panel))

    sign = clicked(ana, [], custom_id="pisos:firmar:1")
    await panel._sign(sign)
    lease = (await cog.service.market(GUILD)).leases["taco_bajo"]
    assert lease.status == rules.RENTED and lease.tenant == "medico"
    assert panel.screen == FICHA


async def test_el_seguro_se_contrata_con_dos_clics(tmp_path: Path) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(5_000_000, "prueba")])
    await cog.service.buy(GUILD, ana.id, "taco_bajo", rules.SHARES)
    panel = await open_panel(cog, ana, guild_with(ana))
    panel.listing = "taco_bajo"
    await panel._go_gestion(clicked(ana, []))
    await panel._arm(clicked(ana, [], custom_id="pisos:seguro"))
    assert "pisos:seguro:si" in ids(panel)
    await panel._toggle_insurance(clicked(ana, []))
    assert (await cog.service.market(GUILD)).leases["taco_bajo"].insured


async def test_desahuciar_cobra_el_abogado_y_sin_dinero_avisa(tmp_path: Path) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(5_000_000, "prueba")])
    await cog.service.buy(GUILD, ana.id, "taco_bajo", rules.SHARES)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "UPDATE housing_leases SET status = 'impago', tenant = 'rider', rent = 3000, unpaid = 3"
        )
    panel = await open_panel(cog, ana, guild_with(ana))
    panel.listing, panel.screen = "taco_bajo", FICHA
    await panel.refresh()
    balance = await cog.economy.balance(GUILD, ana.id)
    press = clicked(ana, [])
    await panel._evict(press)
    fee = rules.eviction_fee(3_000)
    assert await cog.economy.balance(GUILD, ana.id) == balance - fee - round(
        fee * rules.SERVICES_IGIC
    )
    assert (await cog.service.market(GUILD)).leases["taco_bajo"].status == rules.EVICTING

    # Otro piso, sin dinero para el abogado: avisa y no pide nada.
    await cog.service.buy(GUILD, ana.id, "taco_trastero", rules.SHARES)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "UPDATE housing_leases SET status = 'okupa' WHERE listing = 'taco_trastero'"
        )
    left = await cog.economy.balance(GUILD, ana.id)
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(-left, "prueba")])
    panel.listing = "taco_trastero"
    await panel.refresh()
    broke = clicked(ana, [])
    await panel._evict(broke)
    text = " ".join(str(c) for c in broke.followup.send.await_args_list)
    assert "abogado" in text
    assert (await cog.service.market(GUILD)).leases["taco_trastero"].status == rules.SQUATTED


def test_las_noticias_de_barrio_salen_en_un_mensaje() -> None:
    embed = news_embed([("taco", "fabrica"), ("canteras", "bandera_azul")])
    assert "Taco" in embed.description and "Las Canteras" in embed.description
    assert "fábrica" in embed.description


# -- Fase 3 de la ampliación (8.2.0) ------------------------------------------------------


async def test_vender_a_un_miembro_respeta_el_derecho_preferente(tmp_path: Path) -> None:
    from bot.cogs.pisos import PREFERENCE_SECONDS, MemberSaleOffer

    cog, _scene, clock = await make(tmp_path)
    ana, bea, carol = member(10, "Ana"), member(20, "Bea"), member(30, "Carol")
    for user in (ana, bea, carol):
        user.mention = f"<@{user.id}>"
        await cog.economy.repository.apply(GUILD, user.id, [LedgerEntry(10_000_000, "prueba")])
    await cog.service.buy(GUILD, ana.id, "taco_bajo", 60)
    await cog.service.buy(GUILD, carol.id, "taco_bajo", 40)
    guild = guild_with(ana, bea, carol)
    panel = await open_panel(cog, ana, guild)
    panel.listing = "taco_bajo"
    await panel.choose_buyer(clicked(ana, []), 60, 300_000)
    assert "pisos:comprador" in ids(panel)
    pick = clicked(ana, [], values=[str(bea.id)])
    await panel._pick_buyer(pick)
    offer = pick.followup.send.await_args.kwargs["view"]
    assert isinstance(offer, MemberSaleOffer)
    assert [m.id for m in offer.preferred] == [carol.id]  # retracto de la copropietaria

    early = clicked(bea, [])
    await offer.cash.callback(early)  # type: ignore[attr-defined]
    early.response.send_message.assert_awaited_once()  # Bea tiene que esperar
    assert (await cog.service.market(GUILD)).shares_of("taco_bajo")[ana.id] == 60

    clock.now += PREFERENCE_SECONDS + 1
    buy = clicked(bea, [])
    await offer.cash.callback(buy)  # type: ignore[attr-defined]
    market = await cog.service.market(GUILD)
    assert market.shares_of("taco_bajo") == {carol.id: 40, bea.id: 60}
    assert offer.is_finished()


async def test_subastar_pujar_y_cerrar_desde_el_panel(tmp_path: Path) -> None:
    from bot.cogs.pisos import SUBASTAS

    cog, _scene, clock = await make(tmp_path)
    ana, bea = member(10, "Ana"), member(20, "Bea")
    for user in (ana, bea):
        await cog.economy.repository.apply(GUILD, user.id, [LedgerEntry(10_000_000, "prueba")])
    await cog.service.buy(GUILD, ana.id, "taco_bajo", rules.SHARES)
    channel = MagicMock(spec=discord.TextChannel)
    channel.name = "chat-general"
    channel.send = AsyncMock()
    guild = guild_with(ana, bea)
    guild.text_channels = [channel]
    cog.bot.get_guild = lambda _id: guild
    panel = await open_panel(cog, ana, guild)
    panel.listing = "taco_bajo"
    await panel.refresh()
    await panel.open_auction(clicked(ana, []), 100, 50_000)
    assert "Subasta" in channel.send.await_args.kwargs["embed"].title
    (auction,) = (await cog.service.market(GUILD)).auctions

    other = await open_panel(cog, bea, guild)
    await other._go_auctions(clicked(bea, []))
    assert other.screen == SUBASTAS and "pisos:pujar" in ids(other)
    events: list[str] = []
    await other.place_bid(clicked(bea, events), auction.id, 60_000)
    assert events[0] == "response.defer"
    assert (await cog.service.market(GUILD)).auctions[0].best_bidder == bea.id

    clock.now = auction.ends_at + 1
    run = await cog.close_auctions(GUILD)
    assert run is not None and run.results[0].winner == bea.id
    assert "Fin de la subasta" in channel.send.await_args.kwargs["embed"].title
    assert (await cog.service.market(GUILD)).shares_of("taco_bajo") == {bea.id: rules.SHARES}


# -- Fase 4 de la ampliación (8.3.0) ------------------------------------------------------


async def test_reformar_con_dos_clics_y_decidir_un_imprevisto(tmp_path: Path) -> None:
    from bot.cogs.pisos import IMPREVISTOS, REFORMAS

    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(10_000_000, "prueba")])
    panel = await open_panel(cog, ana, guild_with(ana))
    panel.listing, panel.screen = "taco_bajo", FICHA

    def reforms_label() -> str:
        panel.rebuild()
        return next(b.label for b in panel.children if b.custom_id == "pisos:reformas")

    # Antes de comprarlo ya se ve que admite reformas y qué hace falta.
    assert "hay que tenerlo entero" in reforms_label()
    await cog.service.buy(GUILD, ana.id, "taco_bajo", rules.SHARES)
    panel.market = await cog.service.market(GUILD)
    assert reforms_label().startswith("🏗️ Reformar: puedes")
    await panel._go_reforms(clicked(ana, []))
    assert panel.screen == REFORMAS and "pisos:reforma:pintura" in ids(panel)
    assert "💳" in (panel._finance_hint() or "")
    await panel._arm(clicked(ana, [], custom_id="pisos:reforma:pintura"))
    assert "pisos:reforma:pintura:si" in ids(panel)
    await panel._reform(clicked(ana, [], custom_id="pisos:reforma:pintura:si"))
    market = await cog.service.market(GUILD)
    assert market.leases["taco_bajo"].status == rules.OBRAS
    assert "pintura" in market.reforms["taco_bajo"]

    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "INSERT INTO housing_decisions (guild_id, listing, event, cost, day, until) "
            "VALUES (?, 'taco_bajo', 'averia', 3000, '2026-10-14', '2026-10-16')",
            (GUILD,),
        )
    await panel._back_to_listing(clicked(ana, []))
    assert "pisos:imprevistos" in ids(panel)
    await panel._go_decisions(clicked(ana, []))
    assert panel.screen == IMPREVISTOS
    (decision,) = (await cog.service.market(GUILD)).decisions["taco_bajo"]
    await panel._decide(clicked(ana, [], custom_id=f"pisos:decidir:{decision.id}:cunado"))
    (decision,) = (await cog.service.market(GUILD)).decisions["taco_bajo"]
    assert decision.choice == "cunado"


# -- Fase 6 de la ampliación (8.5.0) ------------------------------------------------------


async def test_proponer_y_votar_en_la_junta_desde_el_panel(tmp_path: Path) -> None:
    from bot.cogs.pisos import JUNTA

    cog, _scene, _clock = await make(tmp_path)
    ana, bea = member(10, "Ana"), member(20, "Bea")
    for who in (ana, bea):
        await cog.economy.repository.apply(GUILD, who.id, [LedgerEntry(10_000_000, "prueba")])
    await cog.service.buy(GUILD, ana.id, "taco_bajo", 60)
    await cog.service.buy(GUILD, bea.id, "taco_bajo", 40)
    guild = guild_with(ana, bea)
    panel = await open_panel(cog, ana, guild)
    panel.listing = "taco_bajo"
    await panel._go_gestion(clicked(ana, []))
    assert "pisos:junta" in ids(panel)
    await panel._go_junta(clicked(ana, []))
    assert panel.screen == JUNTA and "pisos:proponer" in ids(panel)
    await panel._propose(clicked(ana, [], values=["renta:mercado"]))
    (vote,) = (await cog.service.market(GUILD)).votes
    assert f"pisos:junta:si:{vote.id}" in ids(panel)
    other = await open_panel(cog, bea, guild)
    other.listing = "taco_bajo"
    await other._go_junta(clicked(bea, []))
    await other._vote(clicked(bea, [], custom_id=f"pisos:junta:no:{vote.id}"))
    market = await cog.service.market(GUILD)
    assert market.votes == () and market.leases["taco_bajo"].level == "mercado"
    # La acción de división, con dos clics: el inmueble entero sale a subasta.
    cog.announce_auction = AsyncMock()  # type: ignore[method-assign]
    await panel._go_junta(clicked(ana, []))
    await panel._arm(clicked(ana, [], custom_id="pisos:division"))
    assert "pisos:division:si" in ids(panel)
    await panel._divide(clicked(ana, [], custom_id="pisos:division:si"))
    assert (await cog.service.market(GUILD)).division_of("taco_bajo") is not None
    assert "pisos:proponer" not in ids(panel)


# -- Plantas y habitaciones (9.12.0) ----------------------------------------------------


async def test_dividir_por_plantas_desde_el_panel_y_abrir_cada_planta(tmp_path: Path) -> None:
    from bot.cogs.pisos import DIVIDIR, PLANTAS

    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(10_000_000, "prueba")])
    await cog.service.buy(GUILD, ana.id, "canteras_duplex", rules.SHARES)
    panel = await open_panel(cog, ana, guild_with(ana))
    panel.barrio, panel.listing = "canteras", "canteras_duplex"
    await panel._go_gestion(clicked(ana, []))
    assert "pisos:dividir" in ids(panel)
    await panel._go_split(clicked(ana, []))
    assert panel.screen == DIVIDIR
    assert "💳" in (panel._finance_hint() or "")
    assert {"pisos:dividir:plantas", "pisos:dividir:habitaciones"} <= set(ids(panel))
    await panel._arm(clicked(ana, [], custom_id="pisos:dividir:plantas"))
    assert "pisos:dividir:plantas:si" in ids(panel)
    events: list[str] = []
    await panel._split(clicked(ana, events, custom_id="pisos:dividir:plantas:si"))
    assert events[0] == "response.defer"  # contesta antes de tocar la base de datos
    market = await cog.service.market(GUILD)
    assert market.split_mode("canteras_duplex") == rules.FLOORS
    assert panel.screen == PLANTAS and {"pisos:unidad:0", "pisos:unidad:1"} <= set(ids(panel))
    # Desde el barrio, la tarjeta del dúplex abre sus plantas, y cada planta su ficha.
    await panel._go_barrio(clicked(ana, []))
    slot = [i.key for i in market.listings_of("canteras")].index("canteras_duplex")
    await panel._open_listing(clicked(ana, [], custom_id=f"pisos:ver:{slot}"))
    assert panel.screen == PLANTAS and panel.listing == "canteras_duplex"
    await panel._open_unit(clicked(ana, [], custom_id="pisos:unidad:1"))
    assert panel.screen == FICHA and panel.listing == "canteras_duplex~2"
    assert "pisos:vender" in ids(panel)  # cada planta se vende sola
    await panel._go_parent(clicked(ana, []))
    assert panel.screen == PLANTAS and panel.listing == "canteras_duplex"


async def test_por_habitaciones_la_ficha_lleva_a_ellas_y_no_se_venden_sueltas(
    tmp_path: Path,
) -> None:
    from bot.cogs.pisos import PLANTAS

    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(10_000_000, "prueba")])
    await cog.service.buy(GUILD, ana.id, "isleta_piso3", rules.SHARES)
    await cog.service.split(GUILD, ana.id, "isleta_piso3", rules.ROOMS)
    panel = await open_panel(cog, ana, guild_with(ana))
    panel.barrio, panel.listing = "isleta", "isleta_piso3"
    await panel._back_to_listing(clicked(ana, []))
    assert "pisos:plantas" in ids(panel) and "pisos:renta" not in ids(panel)
    await panel._go_units(clicked(ana, []))
    assert panel.screen == PLANTAS and "pisos:juntar" in ids(panel)
    await panel._open_unit(clicked(ana, [], custom_id="pisos:unidad:0"))
    assert panel.listing == "isleta_piso3~h1"
    assert "pisos:comprar" not in ids(panel) and "pisos:vender" not in ids(panel)
    assert "pisos:renta" in ids(panel)  # la renta se pide en cada habitación
    await panel._go_gestion(clicked(ana, []))
    assert "pisos:elegir" in ids(panel) and "pisos:junta" not in ids(panel)
    # Juntarlas con dos clics: el piso vuelve a alquilarse entero.
    await panel._go_parent(clicked(ana, []))
    await panel._arm(clicked(ana, [], custom_id="pisos:juntar"))
    await panel._join_rooms(clicked(ana, [], custom_id="pisos:juntar:si"))
    market = await cog.service.market(GUILD)
    assert market.split_mode("isleta_piso3") is None and panel.screen == FICHA


async def test_el_boton_verde_lleva_a_los_candidatos_de_un_sitio_y_al_siguiente(
    tmp_path: Path,
) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(10_000_000, "prueba")])
    await cog.service.buy(GUILD, ana.id, "taco_bajo", rules.SHARES)
    await cog.service.buy(GUILD, ana.id, "isleta_piso3", rules.SHARES)
    await cog.service.split(GUILD, ana.id, "isleta_piso3", rules.ROOMS)
    panel = await open_panel(cog, ana, guild_with(ana))
    await panel.refresh()
    panel.rebuild()
    assert "pisos:candidatos_todos" not in ids(panel)  # nadie espera: no sale
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute("UPDATE housing_leases SET status = 'vacio', pick = 'yo' "
                           "WHERE listing IN ('taco_bajo', 'isleta_piso3~h2')")  # fmt: skip
        for key, tenant in (("taco_bajo", "medico"), ("isleta_piso3~h2", "coliving"),
                            ("isleta_piso3~h2", "astrofisica")):  # fmt: skip
            connection.execute(
                "INSERT INTO housing_candidates (guild_id, listing, tenant, until) "
                "VALUES (?, ?, ?, '2026-12-31')",
                (GUILD, key, tenant),
            )
    await panel._go_map(clicked(ana, []))
    button = next(
        i for i in panel.children if getattr(i, "custom_id", "") == "pisos:candidatos_todos"
    )
    assert button.label == "👥 Candidatos (3)" and button.style == discord.ButtonStyle.success
    await panel._go_portfolio(clicked(ana, []))
    assert "pisos:candidatos_todos" in ids(panel)
    await panel._go_waiting(clicked(ana, []))
    assert panel.screen == CANDIDATOS and panel.listing == "taco_bajo"
    assert "pisos:candidatos_siguiente" in ids(panel)
    # Firmar con el único candidato del bajo lleva a los de la habitación.
    await panel._sign(clicked(ana, [], custom_id="pisos:firmar:1"))
    assert panel.screen == CANDIDATOS and panel.listing == "isleta_piso3~h2"
    assert "pisos:candidatos_siguiente" not in ids(panel)
    await panel._sign(clicked(ana, [], custom_id="pisos:firmar:1"))
    market = await cog.service.market(GUILD)
    assert market.leases["isleta_piso3~h2"].tenant == "coliving"
    assert panel.screen == FICHA  # ya no espera nadie más


# -- 9.23.0: duración del contrato y recuperar el piso -----------------------------------------


def _limits(panel: Panel) -> set[str]:
    """Comprueba lo que Discord exige de los componentes y devuelve sus `custom_id`."""
    rows: dict[int, int] = {}
    for item in panel.children:
        rows[item.row or 0] = rows.get(item.row or 0, 0) + (
            5 if isinstance(item, discord.ui.Select) else 1
        )
        if isinstance(item, discord.ui.Button):
            assert len(item.label or "") <= 80
        if isinstance(item, discord.ui.Select):
            for option in item.options:
                assert len(option.label) <= 100 and len(option.description or "") <= 100
    assert max(rows) <= 4 and all(width <= 5 for width in rows.values()), rows
    ids = [getattr(item, "custom_id", None) for item in panel.children]
    assert len(ids) == len(set(ids))
    return {i for i in ids if i}


async def test_recuperar_el_piso_ofrece_lo_que_toca_y_cabe_en_discord(tmp_path: Path) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(5_000_000, "prueba")])
    await cog.service.buy(GUILD, ana.id, "taco_bajo", rules.SHARES)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "UPDATE housing_leases SET status = 'alquilado', tenant = 'rider', rent = 3000, "
            "until = '2026-12-01', signed = '2026-01-01'"
        )
    panel = await open_panel(cog, ana, guild_with(ana))
    panel.listing, panel.screen = "taco_bajo", FICHA
    await panel.refresh()
    panel.rebuild()
    assert "pisos:llaves" in _limits(panel)
    panel.screen = "llaves"
    panel.rebuild()
    ids = _limits(panel)
    assert {"pisos:llaves_buenas", "pisos:llaves_justa", "pisos:llaves_generosa"} <= ids
    assert {"pisos:no_renovar", "pisos:necesidad"} <= ids
    text = panel._keys_text()
    assert "Lo justo hoy" in text and "art. 9.3 LAU" in text and "No renovar" in text
    # Con su contraoferta en pie, solo se le paga o se deja.
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute("UPDATE housing_leases SET keys_ask = 7000, keys_wait = '2026-10-20'")
    await panel.refresh()
    panel.rebuild()
    ids = _limits(panel)
    assert {"pisos:llaves_precio", "pisos:llaves_dejar"} <= ids
    assert "pisos:llaves_justa" not in ids
    # En 🧰 Gestionar se elige lo que dura el próximo contrato.
    panel.screen = GESTION
    panel.rebuild()
    assert "pisos:duracion" in _limits(panel)
    pick = next(i for i in panel.children if getattr(i, "custom_id", "") == "pisos:duracion")
    assert [o.value for o in pick.options] == [str(t) for t in rules.TERMS]


async def test_pedirlas_por_las_buenas_y_que_diga_que_no(tmp_path: Path) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(5_000_000, "prueba")])
    await cog.service.buy(GUILD, ana.id, "taco_bajo", rules.SHARES)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "UPDATE housing_leases SET status = 'alquilado', tenant = 'enfermeros', rent = 3000, "
            "until = '2026-12-01', signed = '2026-01-01'"
        )
    cog.service.rng = random.Random(0)
    cog.service.rng.random = lambda: 0.99  # type: ignore[method-assign]  # nunca por las buenas
    panel = await open_panel(cog, ana, guild_with(ana))
    panel.listing, panel.screen = "taco_bajo", "llaves"
    await panel.refresh()
    balance = await cog.economy.balance(GUILD, ana.id)
    press = clicked(ana, [])
    await panel._keys_free(press)
    text = " ".join(str(c) for c in press.followup.send.await_args_list)
    assert "no acepta" in text
    lease = (await cog.service.market(GUILD)).leases["taco_bajo"]
    assert lease.keys_ask > 0 and await cog.economy.balance(GUILD, ana.id) == balance


async def test_la_duracion_se_cambia_desde_gestionar(tmp_path: Path) -> None:
    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(5_000_000, "prueba")])
    await cog.service.buy(GUILD, ana.id, "taco_bajo", rules.SHARES)
    panel = await open_panel(cog, ana, guild_with(ana))
    panel.listing, panel.screen = "taco_bajo", GESTION
    await panel.refresh()
    await panel._pick_term(clicked(ana, [], values=["24"]))
    assert (await cog.service.market(GUILD)).leases["taco_bajo"].term == 24


async def test_mis_pisos_pasa_de_pagina_con_flechas_y_el_desplegable_sigue_a_la_imagen(
    tmp_path: Path,
) -> None:
    """Con más de 10 inmuebles, 🔑 Mis pisos va por páginas: ◀ ▶, la imagen y el menú."""
    from bot.services import housing_view as view
    from bot.services.housing_catalog import LISTINGS

    cog, scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(10**9, "prueba")])
    for listing in LISTINGS[:23]:
        await cog.service.buy(GUILD, ana.id, listing.key, 1)
    panel = await open_panel(cog, ana, guild_with(ana))
    events: list[str] = []
    await panel._go_portfolio(clicked(ana, events))
    keys = view.portfolio_keys(panel.market, ana.id)
    assert len(keys) == 23

    def buttons() -> dict[str, discord.ui.Button]:
        return {
            str(item.custom_id): item
            for item in panel.children
            if isinstance(item, discord.ui.Button)
        }

    def menu() -> list[str]:
        pick = next(i for i in panel.children if getattr(i, "custom_id", "") == "pisos:mio")
        return [option.value for option in pick.options]

    assert buttons()["pisos:pagina:antes"].disabled
    assert not buttons()["pisos:pagina:despues"].disabled
    assert menu() == keys[:10]
    # Se pinta por adelantado la página siguiente (no hay anterior).
    await cog.painter._worker
    pages = [(s["page"], s["pages"]) for s in scene.painted if s["screen"] == "cartera"]
    assert (1, 3) in pages and (2, 3) in pages

    events = []
    interaction = clicked(ana, events)
    await panel._page_next(interaction)
    assert events[0] == "response.defer"  # contesta antes de leer la base de datos
    assert panel.page == 1 and menu() == keys[10:20]
    await panel._page_next(clicked(ana, []))
    assert panel.page == 2 and menu() == keys[20:]
    assert buttons()["pisos:pagina:despues"].disabled
    # Un clic de más (un botón viejo) se queda en la última página.
    await panel._page_next(clicked(ana, []))
    assert panel.page == 2
    await panel._page_back(clicked(ana, []))
    assert panel.page == 1
    # Ir a una ficha y volver a 🔑 Mis pisos deja la misma página.
    await panel._pick_mine(clicked(ana, [], values=[keys[12]]))
    assert panel.screen == FICHA
    await panel._go_portfolio(clicked(ana, []))
    assert panel.screen == CARTERA and panel.page == 1


async def test_con_diez_inmuebles_o_menos_mis_pisos_no_lleva_flechas(tmp_path: Path) -> None:
    from bot.services.housing_catalog import LISTINGS

    cog, _scene, _clock = await make(tmp_path)
    ana = member(10, "Ana")
    await cog.economy.repository.apply(GUILD, ana.id, [LedgerEntry(10**9, "prueba")])
    for listing in LISTINGS[:10]:
        await cog.service.buy(GUILD, ana.id, listing.key, 1)
    panel = await open_panel(cog, ana, guild_with(ana))
    await panel._go_portfolio(clicked(ana, []))
    ids = {getattr(item, "custom_id", "") for item in panel.children}
    assert not {"pisos:pagina:antes", "pisos:pagina:despues"} & ids
