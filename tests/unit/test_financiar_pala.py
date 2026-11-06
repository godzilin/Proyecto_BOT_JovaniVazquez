"""💳 Financiar en `pala` (formación, vuelo, café) y en el gimnasio de `gallos`.

Se comprueba que la opción sale solo si el banco financia el importe, que sus `custom_id`
no se repiten y que la compra del trato hace lo mismo que el botón de siempre.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from discord import ui
from interaction_fakes import fake_interaction
from test_gallos_cog import OWNER_ID, WIN
from test_gallos_cog import make_cog as make_gallos_cog
from test_gallos_cog import make_user as gallos_user
from test_gallos_cog import open_panel as open_gallos
from test_work_cog import (
    GUILD,
    OWNER,
    PalaPanel,
    check_limits,
    level_up,
    make_cog,
    make_interaction,
    open_panel,
    texts,
)

from bot.cogs import banco_compras as financiar
from bot.cogs import work as work_cog
from bot.repositories.economy import LedgerEntry
from bot.services import credit
from bot.services.economy import STARTING_BALANCE
from bot.services.gallos import Stat, train_price
from bot.services.pala import WorkError
from bot.services.taxes import igic
from bot.services.work import FLIGHT_PRICE


@pytest.fixture(autouse=True)
def no_dice(monkeypatch: pytest.MonkeyPatch) -> None:
    from bot.services import pala

    monkeypatch.setattr(pala, "roll", lambda rng, chance: False)


def capture_deal(monkeypatch: pytest.MonkeyPatch, module: object) -> list[financiar.Deal]:
    """Cambia `open_finance` por uno que se queda con el trato en vez de abrir el banco."""
    deals: list[financiar.Deal] = []

    async def open_finance(_bot: object, _interaction: object, deal: financiar.Deal) -> bool:
        deals.append(deal)
        return True

    monkeypatch.setattr(module.financiar, "open_finance", open_finance)  # type: ignore[attr-defined]
    return deals


def ids(panel: PalaPanel) -> list[str]:
    return [
        item.custom_id
        for item in panel.walk_children()
        if isinstance(item, ui.Button | ui.Select) and item.custom_id
    ]


async def abroad_panel(tmp_path: Path) -> PalaPanel:
    cog = await make_cog(tmp_path)
    await cog.service.economy.repository.apply(GUILD, OWNER, [LedgerEntry(50_000, "prueba")])
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "oficina")
    await level_up(cog, "oficina", 3)
    await panel.refresh(make_interaction())
    return panel


async def test_financiar_sale_con_custom_id_unico(tmp_path: Path) -> None:
    panel = await abroad_panel(tmp_path)
    assert credit.refusal(FLIGHT_PRICE) is None
    found = ids(panel)
    assert "pala-panel:financiar_vuelo" in found
    assert len(found) == len(set(found))
    assert "-# 🎓 Formación: 💳" in texts(panel) and "-# ✈️ Vuelo a Hong Kong: 💳" in texts(panel)
    assert "-# ☕ Café: 💳" in texts(panel)
    check_limits(panel)
    (cafe,) = [
        i
        for i in panel.walk_children()
        if isinstance(i, ui.Select) and i.custom_id.endswith("cafe")
    ]
    financed = [o.value for o in cafe.options if o.value.startswith("fin:")]
    assert financed and all(o.label.startswith(financiar.LABEL) for o in cafe.options[-2:])


async def test_sin_vuelo_no_hay_boton_de_financiar_el_vuelo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "obra")
    assert "pala-panel:financiar_vuelo" not in ids(panel)


async def test_comprar_el_cafe_financiado_cobra_y_devuelve_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "obra")
    deals = capture_deal(monkeypatch, work_cog)
    await panel._finance_coffee(make_interaction(), "barraquito")
    (deal,) = deals
    assert deal.purchase.category == "cafe"
    assert credit.refusal(deal.purchase.amount) is None
    before = await cog.service.economy.balance(GUILD, OWNER)
    assert await deal.buy(make_interaction()) is None
    assert await cog.service.economy.balance(GUILD, OWNER) == before - deal.purchase.amount
    assert deal.after is not None
    await deal.after(make_interaction())
    assert "barraquito" in texts(panel)


async def test_el_vuelo_financiado_devuelve_el_error_del_servicio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    panel = await abroad_panel(tmp_path)
    deals = capture_deal(monkeypatch, work_cog)
    await panel._finance_flight(make_interaction())
    (deal,) = deals
    assert deal.purchase.amount == FLIGHT_PRICE
    monkeypatch.setattr(
        panel.service, "move_abroad", AsyncMock(side_effect=WorkError("No te llega."))
    )
    assert await deal.buy(make_interaction()) == "No te llega."


async def test_entrenar_al_gallo_financiado_cobra_y_devuelve_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bot.cogs import gallos as gallos_cog

    cog = await make_gallos_cog(tmp_path, WIN)
    panel = await open_gallos(cog)
    await panel._gym(fake_interaction(gallos_user(OWNER_ID)))
    found = [c.custom_id for c in panel.children if isinstance(c, ui.Button)]
    assert len(found) == len(set(found))
    assert "💳" in panel.gym_text()
    assert {f"gallos:financiar_{s.value}" for s in Stat} <= set(found)
    deals = capture_deal(monkeypatch, gallos_cog)
    await panel._finance_train(fake_interaction(gallos_user(OWNER_ID)), Stat.STRENGTH)
    (deal,) = deals
    price = train_price(0) + igic(train_price(0))
    assert (deal.purchase.category, deal.purchase.amount) == ("gallos", price)
    assert await deal.buy(fake_interaction(gallos_user(OWNER_ID))) is None
    assert await cog.economy.balance(GUILD, OWNER_ID) == STARTING_BALANCE - price
    # Sin descansar, el servicio lo rechaza y el trato recibe el motivo.
    again = await deal.buy(fake_interaction(gallos_user(OWNER_ID)))
    assert isinstance(again, str) and again
