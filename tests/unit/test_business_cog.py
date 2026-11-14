"""Pruebas del panel del negocio (`bot.cogs.business`) y del inicio de `pala` (`bot.cogs.work`).

Con los servicios de verdad sobre una base de datos temporal, los dos cogs montados en un
bot falso que sí los encuentra (`get_cog`) y una escena de mentira que devuelve un PNG por
pantalla (o `None`, para ver el panel en texto).
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import ui
from interaction_fakes import fake_interaction

from bot.cogs.business import (
    CADENA,
    CANDIDATOS,
    CIERRE,
    CONFIRMAR,
    DESPIDO,
    MONTAR,
    NAVE,
    PLANTILLA,
    PORTADA,
    PREVIA,
    SOCIEDAD,
    TURNO,
    VENTA,
    Negocio,
    NegocioPanel,
    parse_game_id,
    pressed_at,
)
from bot.cogs.work import PalaPanel, Work
from bot.repositories.business import BusinessRepository
from bot.repositories.economy import EconomyRepository, LedgerEntry
from bot.repositories.work import WorkRepository
from bot.services import business_staff as staff
from bot.services import pala
from bot.services.business_catalog import POINT_BY_KEY, SECTOR_BY_KEY
from bot.services.business_games import COINS, best_weight
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.negocio import BusinessService
from bot.services.pala import WorkService
from bot.services.work import Wish

GUILD = 1
OWNER = 10


class FakeScene:
    """Escena de mentira: apunta lo que pinta y devuelve un PNG de pega (o nada)."""

    def __init__(self, *, works: bool = True) -> None:
        self.works = works
        self.painted: list[dict[str, Any]] = []

    async def paint(self, states: list[dict[str, Any]]) -> list[bytes] | None:
        self.painted.extend(states)
        if not self.works:
            return None
        return [f"png:{s['screen']}".encode() for s in states]

    async def close(self) -> None:
        return None


@pytest.fixture(autouse=True)
def sin_azar_de_la_pala(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pala, "wish_for", lambda *args: Wish("no", "Nada.", lambda f: False))
    monkeypatch.setattr(pala, "roll", lambda rng, chance: False)


def make_user(user_id: int = OWNER) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = "Diego"
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


def click(user: MagicMock | None = None) -> MagicMock:
    interaction = fake_interaction(user or make_user())
    interaction.guild_id = GUILD
    interaction.message = MagicMock(edit=AsyncMock())
    interaction.data = {}
    return interaction


async def make(tmp_path: Path, *, works: bool = True) -> tuple[Work, Negocio, FakeScene]:
    database = tmp_path / "bot.db"
    economy_repo = EconomyRepository(database, starting_balance=STARTING_BALANCE)
    await economy_repo.initialize()
    economy = EconomyService(economy_repo)
    work_repo = WorkRepository(database)
    await work_repo.initialize()
    work = WorkService(work_repo, economy, rng=random.Random(1))
    repository = BusinessRepository(database)
    await repository.initialize()
    negocio = BusinessService(repository, economy, work, rng=random.Random(1))
    work.business = negocio
    bot = MagicMock()
    bot.birthdays = None
    cogs: dict[str, Any] = {}
    bot.get_cog = lambda name: cogs.get(name)
    scene = FakeScene(works=works)
    work_cog = Work(bot, work)
    business_cog = Negocio(bot, negocio, renderer=scene)  # type: ignore[arg-type]
    cogs["Trabajo"], cogs["Negocio"] = work_cog, business_cog
    return work_cog, business_cog, scene


async def open_home(cog: Work) -> tuple[PalaPanel, AsyncMock]:
    send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    await cog._open(
        guild=MagicMock(id=GUILD),
        channel=MagicMock(id=99, parent_id=None),
        user=make_user(),
        send=send,
        send_error=AsyncMock(),
    )
    return next(iter(cog.panels)), send


def labels(view: ui.View) -> list[str]:
    return [i.label or "" for i in view.walk_children() if isinstance(i, ui.Button)]


def button(view: ui.View, label: str) -> ui.Button:
    return next(
        b
        for b in view.walk_children()
        if isinstance(b, ui.Button) and (b.label or "").startswith(label)
    )


def galleries(view: ui.View) -> int:
    return sum(1 for i in view.walk_children() if isinstance(i, ui.MediaGallery))


def check_limits(view: ui.LayoutView) -> None:
    """Lo que Discord rechazaría: demasiados componentes, texto o botones por fila."""
    assert view.total_children_count <= 40
    text = "\n".join(i.content for i in view.walk_children() if isinstance(i, ui.TextDisplay))
    assert len(text) <= 4000
    for item in view.walk_children():
        if isinstance(item, ui.ActionRow):
            assert len(item.children) <= 5


async def opened_business(
    tmp_path: Path, *, works: bool = True
) -> tuple[Work, Negocio, NegocioPanel]:
    work_cog, business_cog, _scene = await make(tmp_path, works=works)
    await business_cog.service.economy.repository.apply(
        GUILD, OWNER, [LedgerEntry(2_000_000, "prueba")]
    )
    await business_cog.service.open(
        GUILD, OWNER, sector="cafeteria", barrio="triana", name="Bar Pepe", palette=0, emblem=0
    )
    for product in SECTOR_BY_KEY["cafeteria"].products:
        await business_cog.service.buy(GUILD, OWNER, product.key, 2)
    home, _send = await open_home(work_cog)
    await button(home, "🏪 Negocio").callback(click())
    panel = next(iter(business_cog.panels))
    return work_cog, business_cog, panel


# -- El inicio ----------------------------------------------------------------------


async def test_el_inicio_pinta_el_dia_y_ofrece_buscar_curro_y_montar_negocio(
    tmp_path: Path,
) -> None:
    work_cog, _business_cog, scene = await make(tmp_path)
    panel, send = await open_home(work_cog)
    assert scene.painted[0]["screen"] == "dia"
    assert [s["kind"] for s in scene.painted[0]["slots"]] == ["libre"] * 8 + ["noche"] * 4
    assert scene.painted[0]["night_from"] == 8
    assert "💼 Buscar curro" in labels(panel) and "🏪 Montar negocio" in labels(panel)
    files = send.await_args.kwargs["files"]
    assert len(files) == 1 and files[0].filename == "pala.png"
    assert galleries(panel) == 1
    check_limits(panel)


async def test_con_curro_el_inicio_lleva_al_trabajo_y_el_trabajo_quita_la_imagen(
    tmp_path: Path,
) -> None:
    work_cog, _business_cog, scene = await make(tmp_path)
    await work_cog.service.hire(GUILD, OWNER, "obra")
    panel, _send = await open_home(work_cog)
    slots = [s["kind"] for s in scene.painted[0]["slots"]]
    assert slots == ["jornada"] * 4 + ["libre"] * 4 + ["noche"] * 4
    interaction = click()
    await button(panel, "🪏 Trabajo").callback(interaction)
    assert interaction.edit_original_response.await_args.kwargs["attachments"] == []
    assert "🏠 Inicio" in labels(panel) and "⛏️ Fichar" in labels(panel)
    check_limits(panel)
    # Y vuelve al inicio con su imagen.
    back = click()
    await button(panel, "🏠 Inicio").callback(back)
    assert len(back.edit_original_response.await_args.kwargs["attachments"]) == 1


async def test_sin_pintor_el_inicio_sale_en_texto_con_las_franjas(tmp_path: Path) -> None:
    work_cog, _business_cog, _scene = await make(tmp_path, works=False)
    panel, send = await open_home(work_cog)
    assert galleries(panel) == 0
    assert send.await_args.kwargs["files"] == []
    text = "\n".join(i.content for i in panel.walk_children() if isinstance(i, ui.TextDisplay))
    assert "⬜" in text and "🌙 ⬛⬛⬛⬛" in text


async def test_la_jornada_se_reduce_desde_el_panel_del_curro(tmp_path: Path) -> None:
    work_cog, _business_cog, _scene = await make(tmp_path)
    await work_cog.service.hire(GUILD, OWNER, "obra")
    panel, _send = await open_home(work_cog)
    await button(panel, "🪏 Trabajo").callback(click())
    await panel._jornada(click(), "j:2")
    status = await work_cog.service.status(GUILD, OWNER)
    assert status is not None and status.contract.jornada_next == 2
    await panel._jornada(click(), "x:on")  # sin un «año» en el oficio no se puede
    status = await work_cog.service.status(GUILD, OWNER)
    assert status is not None and not status.contract.on_excedencia


# -- Montar -------------------------------------------------------------------------


async def test_montar_un_negocio_desde_el_inicio(tmp_path: Path) -> None:
    work_cog, business_cog, scene = await make(tmp_path)
    await business_cog.service.economy.repository.apply(
        GUILD, OWNER, [LedgerEntry(2_000_000, "prueba")]
    )
    home, _send = await open_home(work_cog)
    await button(home, "🏪 Montar negocio").callback(click())
    assert not work_cog.panels  # el mensaje pasa al panel del negocio
    panel = next(iter(business_cog.panels))
    assert panel.screen == MONTAR and scene.painted[-1]["screen"] == "montar"
    check_limits(panel)
    await panel._pick_sector(click(), "taller")
    assert panel.screen == PREVIA and panel.draft["barrio"]
    assert scene.painted[-1]["screen"] == "negocio" and scene.painted[-1]["mode"] == "preview"
    check_limits(panel)
    await panel._pick_palette(click(), "3")
    await panel._pick_emblem(click(), "5")
    await button(panel, "➡️ Seguir").callback(click())
    assert panel.screen == CONFIRMAR
    check_limits(panel)
    await button(panel, "✅ Montarlo").callback(click())
    business = await business_cog.service.business(GUILD, OWNER)
    assert business is not None and business.sector == "taller"
    assert (business.palette, business.emblem) == (3, 5)
    assert panel.screen == PORTADA
    check_limits(panel)


async def test_sin_dinero_montar_avisa_y_no_crea_nada(tmp_path: Path) -> None:
    work_cog, business_cog, _scene = await make(tmp_path)
    home, _send = await open_home(work_cog)
    await button(home, "🏪 Montar negocio").callback(click())
    panel = next(iter(business_cog.panels))
    await panel._pick_sector(click(), "cafeteria")
    await button(panel, "➡️ Seguir").callback(click())
    interaction = click()
    await button(panel, "✅ Montarlo").callback(interaction)
    assert "No te llega" in interaction.followup.send.await_args.args[0]
    assert await business_cog.service.business(GUILD, OWNER) is None


# -- La portada y el turno ------------------------------------------------------------


async def test_cada_pantalla_del_negocio_cabe_en_discord(tmp_path: Path) -> None:
    _work_cog, _business_cog, panel = await opened_business(tmp_path)
    assert panel.screen == PORTADA
    check_limits(panel)
    for name in (
        "📦 Almacén", "⬅️ Volver", "📊 Cuentas", "⬅️ Volver", "🛠️ Mejoras", "⬅️ Volver",
        "⭐ Reseñas", "⬅️ Volver", "🚚 Traslado", "⬅️ Volver", "🚪 Cerrar", "⬅️ No", "🎨 Rótulo",
    ):  # fmt: skip
        await button(panel, name).callback(click())
        check_limits(panel)
    await button(panel, "⬅️ Volver").callback(click())
    await button(panel, "📦 Almacén").callback(click())
    await panel._pick_product(click(), "bocadillo")
    check_limits(panel)
    await button(panel, "⬅️ Volver").callback(click())
    await button(panel, "🛠️ Mejoras").callback(click())
    await panel._pick_equipment(click(), "terraza")
    await panel._pick_campaign(click(), "radio")
    check_limits(panel)
    assert any(label.startswith("🛒 Instalar") for label in labels(panel))
    assert any(label.startswith("📣 Lanzar") for label in labels(panel))


async def test_mejorar_el_local_y_trasladarlo_desde_el_panel(tmp_path: Path) -> None:
    _work_cog, business_cog, panel = await opened_business(tmp_path)
    await button(panel, "🛠️ Mejoras").callback(click())
    await panel._pick_equipment(click(), "cafetera2")
    await button(panel, "🛒 Instalar").callback(click())
    business = await business_cog.service.business(GUILD, OWNER)
    assert business is not None and business.equipment == ["cafetera2"]
    assert not any(label.startswith("🛒 Instalar") for label in labels(panel))
    await panel._pick_campaign(click(), "buzoneo")
    await button(panel, "📣 Lanzar").callback(click())
    business = await business_cog.service.business(GUILD, OWNER)
    assert business is not None and business.campaign == "buzoneo"
    await button(panel, "⬅️ Volver").callback(click())
    await button(panel, "🚚 Traslado").callback(click())
    await panel._pick_move_barrio(click(), "schamann")
    await panel._pick_move_size(click(), "0")
    await button(panel, "🚚 Trasladarse").callback(click())
    business = await business_cog.service.business(GUILD, OWNER)
    assert business is not None and business.barrio == "schamann" and business.size == 0
    assert panel.screen == PORTADA
    # El precio de un producto desde el almacén.
    await button(panel, "📦 Almacén").callback(click())
    await panel._pick_product(click(), "cafe")
    await panel._set_product_price(click(), "barato")
    business = await business_cog.service.business(GUILD, OWNER)
    assert business is not None and business.prices == {"cafe": "barato"}


async def test_cerrar_liquidando_desde_el_panel(tmp_path: Path) -> None:
    _work_cog, business_cog, panel = await opened_business(tmp_path)
    await button(panel, "🚪 Cerrar").callback(click())
    assert not any(label.startswith("🤝 Traspasar") for label in labels(panel))
    await button(panel, "🚪 Cerrar y liquidar").callback(click())
    assert await business_cog.service.business(GUILD, OWNER) is None
    assert panel.screen == MONTAR


async def test_comprar_desde_el_almacen(tmp_path: Path) -> None:
    _work_cog, business_cog, panel = await opened_business(tmp_path)
    await button(panel, "📦 Almacén").callback(click())
    await panel._pick_product(click(), "zumo")
    before = (await business_cog.service.business(GUILD, OWNER)).stock["zumo"]  # type: ignore[union-attr]
    await button(panel, "🛒 5 lotes").callback(click())
    after = (await business_cog.service.business(GUILD, OWNER)).stock["zumo"]  # type: ignore[union-attr]
    assert after == before + 5 * SECTOR_BY_KEY["cafeteria"].product("zumo").pack  # type: ignore[union-attr]


async def play(panel: NegocioPanel) -> None:
    """Juega el turno pulsando los botones del minijuego como haría el dueño."""
    while panel.turn is not None:
        game = panel.turn.game
        client = game.current
        assert client is not None
        if game.sector.game == "comandas":
            actions = [f"p:{k}" for k, n in client.order.items() for _ in range(n)] + ["serve"]
        elif game.sector.game == "caja":
            due, actions = game.due(), ["thief"] if client.hidden else []
            for coin in COINS:
                while due >= coin:
                    actions.append(f"c:{coin}")
                    due -= coin
            actions.append("charge")
        else:
            actions = [f"d:{client.fault}", "q:0"]
        for action in actions:
            turn = panel.turn
            if turn is None:
                return
            # El botón tiene que estar en el panel con su `custom_id` de esta ronda.
            ids = [
                parse_game_id(b.custom_id or "")
                for b in panel.walk_children()
                if isinstance(b, ui.Button)
            ]
            assert (panel.token, turn.game.round, action) in ids or action in ("thief",)
            await panel.game_click(click(), turn.game.round, action)


async def test_un_turno_entero_cobra_y_acaba_en_el_tique(tmp_path: Path) -> None:
    _work_cog, business_cog, panel = await opened_business(tmp_path)
    await button(panel, "🔑 Abrir").callback(click())
    assert panel.screen == TURNO and (GUILD, OWNER) in business_cog.working
    check_limits(panel)
    await play(panel)
    assert panel.screen == CIERRE and panel.result is not None
    assert panel.result.summary.sales > 0
    assert (GUILD, OWNER) not in business_cog.working
    business = await business_cog.service.business(GUILD, OWNER)
    assert business is not None and business.turns_today == 1
    assert business.sales_today == panel.result.summary.sales
    check_limits(panel)


def next_action(game: Any) -> str:
    """Lo que pulsaría ahora quien juega bien un minijuego de la 11.3.0."""
    client = game.current
    kind = game.sector.game
    if kind == "brasa":
        low = POINT_BY_KEY[client.point][2] if client.dish else 0
        return "fire:1" if client.dish and game.heat < low else "serve"
    if kind == "encargos":
        return "none" if client.bag < 0 else f"b:{client.bag}"
    if kind == "balanza":
        missing = [i for i in best_weight(client.pieces, client.grams) if i not in game.scale]
        return f"w:{missing[0]}" if missing else "weigh"
    if kind == "revision":
        missing = [k for k in client.faults if k not in game.marked]
        return f"m:{missing[0]}" if missing else "fix"
    target, style = client.target, game.style
    if style.length != target.length:
        return f"l:{target.length}"
    for flag in ("fade", "beard", "wash"):
        if getattr(style, flag) != getattr(target, flag):
            return flag
    return f"col:{target.color}" if style.color != target.color else "done"


@pytest.mark.parametrize(
    "sector", ["guachinche", "panaderia", "pescaderia", "moviles", "peluqueria"]
)
async def test_un_turno_de_cada_minijuego_nuevo_se_juega_con_sus_botones(
    tmp_path: Path, sector: str
) -> None:
    work_cog, business_cog, _scene = await make(tmp_path)
    await business_cog.service.economy.repository.apply(
        GUILD, OWNER, [LedgerEntry(2_000_000, "prueba")]
    )
    await business_cog.service.open(
        GUILD, OWNER, sector=sector, barrio="triana", name="Prueba", palette=0, emblem=0
    )
    for product in SECTOR_BY_KEY[sector].products:
        await business_cog.service.buy(GUILD, OWNER, product.key, 3)
    home, _send = await open_home(work_cog)
    await button(home, "🏪 Negocio").callback(click())
    panel = next(iter(business_cog.panels))
    await button(panel, "🔑 Abrir").callback(click())
    assert panel.screen == TURNO
    clicks = 0
    while panel.turn is not None:
        turn = panel.turn
        check_limits(panel)
        action = next_action(turn.game)
        ids = [
            parse_game_id(b.custom_id or "")
            for b in panel.walk_children()
            if isinstance(b, ui.Button) and not b.disabled
        ]
        assert (panel.token, turn.game.round, action) in ids, action
        await panel.game_click(click(), turn.game.round, action)
        clicks += 1
        assert clicks < 200
    assert panel.screen == CIERRE and panel.result is not None
    results = {o.result for o in panel.result.summary.outcomes}
    assert results <= {"ok", "honest", "overdone"} and panel.result.summary.sales > 0


async def test_el_primer_cliente_se_pinta_antes_de_abrir(tmp_path: Path) -> None:
    _work_cog, business_cog, panel = await opened_business(tmp_path)
    painter = business_cog.painter
    assert painter._worker is not None
    await painter._worker
    scene: FakeScene = painter.scene  # type: ignore[assignment]
    ahead = [s for s in scene.painted if s["screen"] == "cliente"]
    assert len(ahead) == 1
    await button(panel, "🔑 Abrir").callback(click())
    # El cliente que sale es el que ya estaba pintado: no se vuelve a pintar.
    assert panel.scene_state() == ahead[0]
    assert scene.painted.count(ahead[0]) == 1


async def test_un_clic_de_una_ronda_vieja_no_cuenta(tmp_path: Path) -> None:
    _work_cog, _business_cog, panel = await opened_business(tmp_path)
    await button(panel, "🔑 Abrir").callback(click())
    assert panel.turn is not None
    old = panel.turn.game.round
    await panel.game_click(click(), old, "p:cafe")
    await panel.game_click(click(), old + 50, "serve")  # de un cliente que ya no está
    assert panel.turn is not None and panel.turn.game.outcomes == []


async def test_un_clic_con_el_panel_subiendo_otra_imagen_se_acepta_sin_esperar(
    tmp_path: Path,
) -> None:
    _work_cog, _business_cog, panel = await opened_business(tmp_path)
    await button(panel, "🔑 Abrir").callback(click())
    assert panel.turn is not None
    interaction = click()
    # El reloj de la paciencia (u otro clic) tiene el candado mientras sube su imagen.
    await panel._render_lock.acquire()
    pressed = asyncio.create_task(panel.game_click(interaction, panel.turn.game.round, "p:cafe"))
    await asyncio.sleep(0)
    assert interaction.response.is_done()
    panel._render_lock.release()
    await pressed


def order_actions(panel: NegocioPanel) -> list[str]:
    """Los clics que sirven bien la mesa del cliente de ahora (cafetería)."""
    assert panel.turn is not None
    client = panel.turn.game.current
    assert client is not None
    return [f"p:{k}" for k, n in client.order.items() for _ in range(n)] + ["serve"]


async def test_un_clic_que_salio_a_tiempo_cuenta_aunque_llegue_tarde(tmp_path: Path) -> None:
    _work_cog, business_cog, panel = await opened_business(tmp_path)
    await button(panel, "🔑 Abrir").callback(click())
    assert panel.turn is not None
    game = panel.turn.game
    deadline = game.client_deadline
    # Los clics llegan medio segundo después de la paciencia, pero se pulsaron antes.
    business_cog.service._clock = lambda: deadline + 0.5  # type: ignore[method-assign]
    sent = datetime.fromtimestamp(deadline - 0.2, UTC)
    for action in order_actions(panel):
        interaction = click()
        interaction.created_at = sent
        await panel.game_click(interaction, game.round, action)
    assert game.outcomes[0].result == "ok"


def test_la_hora_del_clic_no_se_va_mas_alla_del_margen() -> None:
    interaction = click()
    interaction.created_at = datetime.fromtimestamp(900.0, UTC)  # reloj de Discord muy atrás
    assert pressed_at(interaction, 1_000.0) == 999.0
    interaction.created_at = datetime.fromtimestamp(1_005.0, UTC)  # o adelantado
    assert pressed_at(interaction, 1_000.0) == 1_000.0
    assert pressed_at(click(), 1_000.0) == 1_000.0  # sin hora de Discord, la de llegada


async def test_lo_que_tarda_en_subir_el_cliente_no_gasta_su_paciencia(tmp_path: Path) -> None:
    _work_cog, business_cog, panel = await opened_business(tmp_path)
    clock = [business_cog.service.now()]
    business_cog.service._clock = lambda: clock[0]  # type: ignore[method-assign]
    await button(panel, "🔑 Abrir").callback(click())
    assert panel.turn is not None
    game = panel.turn.game
    *items, last = order_actions(panel)
    for action in items:
        await panel.game_click(click(), game.round, action)
    served = click()
    respond = served.response.edit_message.side_effect

    async def slow_upload(*args: object, **kwargs: object) -> None:
        await respond(*args, **kwargs)
        clock[0] += 3.0  # Discord tarda 3 s en enseñar al siguiente cliente

    served.response.edit_message.side_effect = slow_upload
    await panel.game_click(served, game.round, last)
    assert len(game.outcomes) == 1
    # Su paciencia empieza cuando se ve, no cuando se sirvió al anterior.
    assert game.since == pytest.approx(clock[0])
    assert game.client_deadline == pytest.approx(clock[0] + game.patience)


async def test_mientras_atiendes_tu_negocio_no_se_ficha_en_la_pala(tmp_path: Path) -> None:
    work_cog, _business_cog, panel = await opened_business(tmp_path)
    await work_cog.service.hire(GUILD, OWNER, "obra")
    await button(panel, "🔑 Abrir").callback(click())
    work_panel = PalaPanel(work_cog, guild_id=GUILD, owner=make_user())
    interaction = click()
    await work_panel._clock_in(interaction)
    assert "negocio" in interaction.followup.send.await_args.args[0]
    assert work_panel.shift is None


async def test_sin_pintor_el_negocio_sale_en_texto(tmp_path: Path) -> None:
    _work_cog, _business_cog, panel = await opened_business(tmp_path, works=False)
    assert galleries(panel) == 0
    text = "\n".join(i.content for i in panel.walk_children() if isinstance(i, ui.TextDisplay))
    assert "Bar Pepe" in text and "Caja de hoy" in text


async def test_del_negocio_se_vuelve_al_inicio(tmp_path: Path) -> None:
    work_cog, business_cog, panel = await opened_business(tmp_path)
    await button(panel, "🏠 Inicio").callback(click())
    assert panel not in business_cog.panels
    home = next(iter(work_cog.panels))
    assert "🏪 Negocio" in labels(home)


async def test_constituir_la_sociedad_y_operar_con_ella_desde_el_panel(tmp_path: Path) -> None:
    _work_cog, business_cog, panel = await opened_business(tmp_path)
    await button(panel, "🏢 Sociedad").callback(click())
    assert panel.screen == SOCIEDAD
    check_limits(panel)
    await panel._pick_capital(click(), "100")
    await button(panel, "✍️ Constituir").callback(click())
    business = await business_cog.service.business(GUILD, OWNER)
    assert business is not None and business.is_company
    assert business.company["capital"] == 100
    check_limits(panel)
    assert "💶 Aportar" in labels(panel) and "💼 Sueldo" in labels(panel)
    await panel.company_action(click(), "aportar", "50k")
    await panel.company_action(click(), "sueldo", "20000")
    view = await business_cog.service.company_view(GUILD, OWNER)
    assert view is not None and view.cash == 100 + 50_000 - 20_000
    # Un importe mal escrito avisa y no hace nada.
    bad = click()
    await panel.company_action(bad, "prestar", "mucho")
    assert view.business.company["loan"] == 0


# -- Fase 4: la plantilla -------------------------------------------------------------


async def test_contratar_formar_y_despedir_desde_el_panel(tmp_path: Path) -> None:
    _work_cog, business_cog, panel = await opened_business(tmp_path)
    await button(panel, "👥 Plantilla").callback(click())
    assert panel.screen == PLANTILLA
    check_limits(panel)
    assert "Aún no tienes a nadie" in panel.text()
    await button(panel, "👥 Candidatos").callback(click())
    assert panel.screen == CANDIDATOS
    check_limits(panel)
    business = await business_cog.service.business(GUILD, OWNER)
    assert business is not None
    candidate = business_cog.service.candidates(business)[0]
    await panel._pick_candidate(click(), str(candidate["key"]))
    await panel._pick_contract(click(), "temporal")
    await panel._pick_horario(click(), "56")
    check_limits(panel)
    assert any(label.startswith("✍️ Contratar") for label in labels(panel))
    await button(panel, "✍️ Contratar").callback(click())
    assert panel.screen == PLANTILLA
    business = await business_cog.service.business(GUILD, OWNER)
    assert business is not None and len(business.staff) == 1
    hired = business.staff[0]
    assert (hired["contract"], hired["shift"], hired["jornada"]) == ("temporal", "tarde", 2)
    check_limits(panel)
    assert "📄 Hacerle fijo" in labels(panel) and "🚪 Despedir" in labels(panel)
    await panel._raise(click(), "0.1")
    await panel._course(click(), "trato")
    business = await business_cog.service.business(GUILD, OWNER)
    assert business is not None
    assert business.staff[0]["wage"] > hired["wage"] and business.staff[0]["course"] == "trato"
    await button(panel, "📄 Hacerle fijo").callback(click())
    await button(panel, "🚪 Despedir").callback(click())
    assert panel.screen == DESPIDO
    check_limits(panel)
    await button(panel, "No supera el período de prueba").callback(click())
    business = await business_cog.service.business(GUILD, OWNER)
    assert business is not None and business.staff == []
    assert panel.screen == PLANTILLA


async def test_la_plantilla_sale_en_texto_sin_pintor(tmp_path: Path) -> None:
    _work_cog, business_cog, panel = await opened_business(tmp_path)
    business = await business_cog.service.business(GUILD, OWNER)
    assert business is not None
    candidate = business_cog.service.candidates(business)[0]
    jornada = 2 if candidate["part_time"] else 4
    await business_cog.service.hire(
        GUILD, OWNER, candidate["key"], contract="b", jornada=jornada, shift="manana"
    )
    await button(panel, "👥 Plantilla").callback(click())
    assert candidate["name"] in panel.text() and "En B" in panel.text()
    await button(panel, "👥 Candidatos").callback(click())
    assert "Candidatos de hoy" in panel.text()


# -- Fase 5: la cadena, la nave, el gerente y vender ------------------------------------


async def test_la_cadena_la_nave_y_el_gerente_desde_el_panel(tmp_path: Path) -> None:
    _work_cog, business_cog, panel = await opened_business(tmp_path)
    service = business_cog.service
    await service.economy.repository.apply(GUILD, OWNER, [LedgerEntry(5_000_000, "prueba")])
    first = panel.biz
    await button(panel, "🗺️ Cadena").callback(click())
    assert panel.screen == CADENA
    check_limits(panel)
    # Otro local, con el rótulo del de ahora.
    await button(panel, "➕ Otro local").callback(click())
    assert panel.screen == MONTAR and panel.draft["name"] == "Bar Pepe"
    await panel._pick_sector(click(), "tienda")
    await panel._pick_barrio(click(), "vegueta")
    await button(panel, "➡️ Seguir").callback(click())
    await button(panel, "✅ Montarlo").callback(click())
    owned = await service.owned(GUILD, OWNER)
    assert len(owned) == 2 and panel.biz == owned[1].id and panel.screen == PORTADA
    assert owned[1].name == "Bar Pepe"
    # Ir al otro local desde la cadena.
    await button(panel, "🗺️ Cadena").callback(click())
    check_limits(panel)
    await panel._pick_local(click(), str(first))
    assert panel.biz == first and panel.screen == PORTADA
    assert panel.status is not None and panel.status.business.sector == "cafeteria"
    # La nave: alquilarla, importar y traer al almacén.
    await button(panel, "🗺️ Cadena").callback(click())
    await button(panel, "🏭 Nave").callback(click())
    assert panel.screen == NAVE
    check_limits(panel)
    await panel._pick_nave_barrio(click(), "telde")
    await button(panel, "🏭 Abrir la nave").callback(click())
    assert await service.nave(GUILD, OWNER) is not None
    check_limits(panel)
    await panel._pick_import(click(), "cafe")
    check_limits(panel)
    await button(panel, "🚢 Importar").callback(click())
    nave = await service.nave(GUILD, OWNER)
    assert nave is not None and len(nave.orders) == 1
    assert button(panel, "🚪 Dejar la nave").disabled  # con un contenedor en camino, no
    # Un gerente para el local.
    await button(panel, "⬅️ Cadena").callback(click())
    await button(panel, "⬅️ Volver").callback(click())
    await button(panel, "👥 Plantilla").callback(click())
    await button(panel, "👥 Candidatos").callback(click())
    business = await service.business(GUILD, OWNER, first)
    assert business is not None
    pick = next(c for c in service.candidates(business) if not c["part_time"])
    await panel._pick_candidate(click(), str(pick["key"]))
    await panel._pick_role(click(), staff.MANAGER)
    check_limits(panel)
    await button(panel, "✍️ Contratar").callback(click())
    business = await service.business(GUILD, OWNER, first)
    assert business is not None and staff.manager(business) is not None
    assert "gerente" in panel.text()


async def test_vender_la_sociedad_desde_el_panel(tmp_path: Path) -> None:
    _work_cog, business_cog, panel = await opened_business(tmp_path)
    service = business_cog.service
    await service.economy.repository.apply(GUILD, OWNER, [LedgerEntry(1_000_000, "prueba")])
    await service.constitute(GUILD, OWNER, 300_000)
    business = await service.business(GUILD, OWNER)
    assert business is not None
    business.company["results"] = [150_000, 150_000, 150_000]
    await service.repository.save(GUILD, business)
    await button(panel, "🏢 Sociedad").callback(click())
    await button(panel, "💼 Vender la sociedad").callback(click())
    assert panel.screen == VENTA
    check_limits(panel)
    assert "El comprador paga" in panel.text()
    await button(panel, "💼 Vender ·").callback(click())
    assert await service.owned(GUILD, OWNER) == []
    assert panel.screen == MONTAR
