"""El botón 🌌 Jovaniverso que se añade a todos los paneles (`bot.cogs.universo`)."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import discord
import pytest
from discord import ui

from bot.cogs import universo
from bot.services.achievements import BY_ID, universo_teaser_stats, with_derived
from bot.utils.interactions import is_claimed
from tests.interaction_fakes import fake_interaction


@pytest.fixture
def promo() -> Iterator[None]:
    universo.install_promo()
    universo.install_promo()  # dos veces no pone dos botones
    yield
    universo.uninstall_promo()


def _view(module: str, buttons: int, *, layout: bool = False) -> ui.BaseView:
    view: ui.BaseView = ui.LayoutView() if layout else ui.View()
    for i in range(buttons):
        button = ui.Button(label=str(i), custom_id=f"x:{i}", row=None if layout else i // 5)
        view.add_item(ui.ActionRow(button) if layout else button)
    # Las vistas de verdad son clases de su cog: se imita con `__module__`.
    view.__class__ = type("Panel", (type(view),), {"__module__": module})
    return view


def _ids(components: list[dict]) -> list[str]:
    return [item["custom_id"] for row in components for item in row["components"]]


def test_un_panel_lleva_el_boton_en_una_fila_nueva(promo: None) -> None:
    components = _view("bot.cogs.coin", 3).to_components()
    assert len(components) == 2
    assert components[-1]["components"][0]["custom_id"] == "universo:promo:coin"
    assert is_claimed("universo:promo:coin")


def test_con_cinco_filas_va_al_final_de_la_ultima_si_cabe(promo: None) -> None:
    assert _ids(_view("bot.cogs.coin", 22).to_components())[-1] == "universo:promo:coin"
    assert "universo:promo:coin" not in _ids(_view("bot.cogs.coin", 25).to_components())


def test_un_layout_view_tambien_lo_lleva(promo: None) -> None:
    components = _view("bot.cogs.mines", 2, layout=True).to_components()
    assert _ids(components)[-1] == "universo:promo:mines"


def test_sin_boton_en_admin_en_el_universo_en_vistas_vacias_ni_al_quitarlo(promo: None) -> None:
    assert "universo:promo:admin" not in _ids(_view("bot.cogs.admin", 2).to_components())
    assert len(_view("bot.cogs.universo", 1).to_components()) == 1
    assert _view("bot.cogs.coin", 0).to_components() == []
    universo.uninstall_promo()
    assert len(_view("bot.cogs.coin", 3).to_components()) == 1


@pytest.mark.parametrize("is_open", [True, False])
async def test_pulsarlo_avisa_en_privado_y_apunta_el_logro(is_open: bool) -> None:
    cog = universo.Universo(MagicMock(), client_secret="x" if is_open else "", port=0)
    interaction = fake_interaction()
    interaction.type = discord.InteractionType.component
    interaction.data = {"custom_id": "universo:promo:slots"}
    interaction.user.bot = False
    with patch.object(universo.logros, "note") as note:
        await cog.on_interaction(interaction)
    kwargs = interaction.response.send_message.call_args.kwargs
    assert kwargs["ephemeral"] is True
    assert "ya están" in kwargs["embed"].description
    assert ("view" in kwargs) is is_open
    assert note.call_args.args[3] == universo_teaser_stats("slots")


async def test_los_demas_clics_no_los_contesta() -> None:
    cog = universo.Universo(MagicMock(), client_secret="x", port=0)
    interaction = fake_interaction()
    interaction.type = discord.InteractionType.component
    interaction.data = {"custom_id": "coin:cara"}
    await cog.on_interaction(interaction)
    interaction.response.send_message.assert_not_called()


def test_los_logros_cuentan_paneles_distintos() -> None:
    assert {"universo_teasers_1", "universo_teasers_10", "universo_teasers_25"} <= BY_ID.keys()
    stats = {f"universo_teaser_m{i}": 3 for i in range(10)}
    assert with_derived(stats)["universo_teasers"] == 10
