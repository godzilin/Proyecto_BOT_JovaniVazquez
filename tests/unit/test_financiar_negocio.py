"""💳 Financiar en el negocio de un autónomo: mejoras, campañas, mercancía, cursos, montarlo y
trasladarse. La sociedad paga con su caja y no lleva el botón.

Se comprueba que el botón sale solo a quien toca, que la compra del trato hace lo mismo que
la de siempre y que, si el servicio la rechaza, el trato recibe el motivo (y `FinanceView`
deshace la financiación).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from discord import ui
from test_business_cog import (
    GUILD,
    MONTAR,
    OWNER,
    PREVIA,
    button,
    check_limits,
    click,
    labels,
    make,
    open_home,
    opened_business,
)

from bot.cogs import banco_compras as financiar
from bot.cogs import business as business_cog
from bot.repositories.economy import LedgerEntry
from bot.services import credit
from bot.services.negocio import BusinessError


def capture_deal(monkeypatch: pytest.MonkeyPatch) -> list[financiar.Deal]:
    """Cambia `open_finance` por uno que se queda con el trato en vez de abrir el banco."""
    deals: list[financiar.Deal] = []

    async def open_finance(_bot: object, _interaction: object, deal: financiar.Deal) -> bool:
        deals.append(deal)
        return True

    monkeypatch.setattr(business_cog.financiar, "open_finance", open_finance)
    return deals


def ids(panel: ui.LayoutView) -> list[str]:
    return [
        i.custom_id
        for i in panel.walk_children()
        if isinstance(i, ui.Button | ui.Select) and i.custom_id
    ]


async def test_un_autonomo_financia_una_mejora_y_se_instala(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _work, cog, panel = await opened_business(tmp_path)
    await button(panel, "🛠️ Mejoras").callback(click())
    await panel._pick_equipment(click(), "cafetera2")
    assert any(label.startswith(financiar.LABEL) for label in labels(panel))
    found = ids(panel)
    assert "negocio-panel:financiar-mejora" in found and len(found) == len(set(found))
    assert "💳" in panel.text()
    check_limits(panel)
    deals = capture_deal(monkeypatch)
    await button(panel, financiar.LABEL).callback(click())
    (deal,) = deals
    assert deal.purchase.category == "negocio" and credit.refusal(deal.purchase.amount) is None
    before = await cog.service.economy.balance(GUILD, OWNER)
    assert await deal.buy(click()) is None
    assert await cog.service.economy.balance(GUILD, OWNER) == before - deal.purchase.amount
    business = await cog.service.business(GUILD, OWNER)
    assert business is not None and business.equipment == ["cafetera2"]
    assert deal.after is not None
    await deal.after(click())
    assert "cafetera2" not in labels(panel) and panel.equipment == ""


async def test_una_sociedad_no_ve_el_boton_de_financiar(tmp_path: Path) -> None:
    _work, cog, panel = await opened_business(tmp_path)
    await button(panel, "🏢 Sociedad").callback(click())
    await panel._pick_capital(click(), "100")
    await button(panel, "✍️ Constituir").callback(click())
    await button(panel, "⬅️ Volver").callback(click())
    await button(panel, "🛠️ Mejoras").callback(click())
    await panel._pick_equipment(click(), "cafetera2")
    assert any(label.startswith("🛒 Instalar") for label in labels(panel))
    assert not any(label.startswith(financiar.LABEL) for label in labels(panel))
    assert "💳" not in panel.text()


async def test_si_la_compra_falla_el_trato_devuelve_el_motivo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _work, cog, panel = await opened_business(tmp_path)
    await button(panel, "🛠️ Mejoras").callback(click())
    await panel._pick_equipment(click(), "cafetera2")
    deals = capture_deal(monkeypatch)
    await panel._finance_install(click())
    (deal,) = deals
    monkeypatch.setattr(
        cog.service, "buy_equipment", AsyncMock(side_effect=BusinessError("No te llega."))
    )
    assert await deal.buy(click()) == "No te llega."
    business = await cog.service.business(GUILD, OWNER)
    assert business is not None and business.equipment == []


async def test_financiar_una_campana_y_la_mercancia(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _work, cog, panel = await opened_business(tmp_path)
    deals = capture_deal(monkeypatch)
    await button(panel, "🛠️ Mejoras").callback(click())
    await panel._pick_campaign(click(), "buzoneo")
    await panel._finance_launch(click())
    assert await deals[-1].buy(click()) is None
    business = await cog.service.business(GUILD, OWNER)
    assert business is not None and business.campaign == "buzoneo"
    await button(panel, "⬅️ Volver").callback(click())
    await button(panel, "📦 Almacén").callback(click())
    await panel._pick_product(click(), "cafe")
    assert "negocio-panel:financiar:1" in ids(panel)
    check_limits(panel)
    before = business.stock["cafe"]
    await panel._finance_stock(1)(click())
    assert await deals[-1].buy(click()) is None
    business = await cog.service.business(GUILD, OWNER)
    assert business is not None and business.stock["cafe"] > before


async def test_financiar_un_curso_cuesta_lo_mismo_que_pagarlo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _work, cog, panel = await opened_business(tmp_path)
    business = await cog.service.business(GUILD, OWNER)
    assert business is not None
    candidate = cog.service.candidates(business)[0]
    jornada = 2 if candidate["part_time"] else 4
    await cog.service.hire(
        GUILD, OWNER, candidate["key"], contract="indefinido", jornada=jornada, shift="manana"
    )
    await button(panel, "👥 Plantilla").callback(click())
    assert "negocio-panel:curso-financiar" in ids(panel)
    check_limits(panel)
    business = await cog.service.business(GUILD, OWNER)
    assert business is not None
    panel.employee = business.staff[0]["id"]
    deals = capture_deal(monkeypatch)
    await panel._finance_course(click(), "trato")
    (deal,) = deals
    before = await cog.service.economy.balance(GUILD, OWNER)
    assert await deal.buy(click()) is None
    assert await cog.service.economy.balance(GUILD, OWNER) == before - deal.purchase.amount


async def test_montarlo_financia_solo_el_acondicionamiento(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_cog, cog, _scene = await make(tmp_path)
    await cog.service.economy.repository.apply(GUILD, OWNER, [LedgerEntry(2_000_000, "prueba")])
    home, _send = await open_home(work_cog)
    await button(home, "🏪 Montar negocio").callback(click())
    panel = next(iter(cog.panels))
    assert panel.screen == MONTAR
    await panel._pick_sector(click(), "taller")
    assert panel.screen == PREVIA
    await button(panel, "➡️ Seguir").callback(click())
    assert "negocio-panel:financiar-abrir" in ids(panel)
    assert "💳" in panel.text()
    check_limits(panel)
    deals = capture_deal(monkeypatch)
    await button(panel, financiar.LABEL).callback(click())
    (deal,) = deals
    quote = business_cog.opening_quote(
        business_cog.SECTOR_BY_KEY["taller"], business_cog.BARRIO_BY_KEY[panel.draft["barrio"]]
    )
    assert deal.purchase.works and deal.purchase.amount == quote.setup + quote.setup_igic
    before = await cog.service.economy.balance(GUILD, OWNER)
    assert await deal.buy(click()) is None
    # Al contado salen la tasa, la fianza y el acondicionamiento (y la cuota de autónomo): el
    # trato solo financia lo último.
    assert await cog.service.economy.balance(GUILD, OWNER) <= before - quote.total
    assert await cog.service.business(GUILD, OWNER) is not None


async def test_trasladarse_financia_solo_el_acondicionamiento(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _work, cog, panel = await opened_business(tmp_path)
    await button(panel, "🚚 Traslado").callback(click())
    await panel._pick_move_barrio(click(), "schamann")
    await panel._pick_move_size(click(), "0")
    assert "negocio-panel:financiar-traslado" in ids(panel)
    assert "💳" in panel.text()
    deals = capture_deal(monkeypatch)
    await button(panel, financiar.LABEL).callback(click())
    (deal,) = deals
    assert deal.purchase.works
    assert await deal.buy(click()) is None
    business = await cog.service.business(GUILD, OWNER)
    assert business is not None and business.barrio == "schamann"
    assert deal.after is not None
    await deal.after(click())
