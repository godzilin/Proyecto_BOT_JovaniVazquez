"""🔀 Otras tragaperras: el desplegable, sus bloqueos y los logros de cambiar de máquina.

Aquí se prueban las vistas sueltas (la clásica y las de Botes). Que cambiar de verdad abra
la otra máquina en el mismo mensaje cruza de un cog a otro y se prueba con el bot real en
`tests/integration/test_cog_bridges.py`.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

import bot.cogs.slots as slots_module
from bot.cogs import tragas_cambio
from bot.cogs.hold_win import HoldWin, HoldWinView
from bot.cogs.slots import SlotMachineView, Slots
from bot.cogs.tragas_cambio import SWITCH_LABEL, machine_keys, machine_options
from bot.repositories.economy import EconomyRepository
from bot.repositories.hold_win import HoldWinRepository
from bot.repositories.slots import SlotsRepository
from bot.services.achievements import (
    SLOTS_MACHINES,
    SLOTS_SWITCH_KINDS_STAT,
    SLOTS_SWITCH_PREFIX,
    slots_switch_stats,
    with_derived,
)
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.hold_win import THEMES

GUILD_ID = 1
OWNER_ID = 10


def make_user() -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = OWNER_ID
    user.display_name = "Diego"
    user.mention = f"<@{OWNER_ID}>"
    user.bot = False
    return user


async def make_slots(tmp_path: Path) -> Slots:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    slots_repository = SlotsRepository(tmp_path / "bot.db")
    await slots_repository.initialize()
    return Slots(
        MagicMock(),
        economy=EconomyService(repository),
        renderer=MagicMock(),
        repository=slots_repository,
    )


async def make_hold_win(tmp_path: Path) -> HoldWin:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    hold_repository = HoldWinRepository(tmp_path / "bot.db")
    await hold_repository.initialize()
    return HoldWin(
        MagicMock(),
        economy=EconomyService(repository),
        repository=hold_repository,
        renderer=MagicMock(),
    )


async def classic(tmp_path: Path) -> SlotMachineView:
    cog = await make_slots(tmp_path)
    return SlotMachineView(cog, guild_id=GUILD_ID, owner=make_user(), stake=100)


async def botes(tmp_path: Path, key: str = "volcan") -> HoldWinView:
    cog = await make_hold_win(tmp_path)
    return HoldWinView(cog, theme=THEMES[key], guild_id=GUILD_ID, owner=make_user(), stake=100)


async def make_views(tmp_path: Path) -> list[SlotMachineView | HoldWinView]:
    return [await classic(tmp_path), await botes(tmp_path)]


def ids(view: discord.ui.View) -> list[str | None]:
    return [item.custom_id for item in view.children]  # type: ignore[attr-defined]


def button(view: discord.ui.View, custom_id: str) -> discord.ui.Button:
    for item in view.children:
        if getattr(item, "custom_id", None) == custom_id:
            return item  # type: ignore[return-value]
    raise AssertionError(f"No hay un componente {custom_id}: {ids(view)}")


# -- Opciones del desplegable --------------------------------------------------------


def test_la_clasica_de_achievements_slots_y_cambio_son_la_misma_clave() -> None:
    assert tragas_cambio.CLASSIC == SLOTS_MACHINES[0] == slots_module.CLASSIC == "clasica"
    assert machine_keys() == [tragas_cambio.CLASSIC, *THEMES]
    assert tuple(machine_keys()) == SLOTS_MACHINES


@pytest.mark.parametrize("current", [tragas_cambio.CLASSIC, *THEMES])
def test_las_opciones_son_todas_las_maquinas_menos_la_actual(current: str) -> None:
    options = machine_options(current)
    values = [option.value for option in options]
    assert current not in values
    assert sorted(values) == sorted(key for key in machine_keys() if key != current)
    assert len(values) == len(set(values))


@pytest.mark.parametrize("current", [tragas_cambio.CLASSIC, *THEMES])
def test_las_opciones_caben_en_un_desplegable_de_discord(current: str) -> None:
    options = machine_options(current)
    assert 1 <= len(options) <= 25
    for option in options:
        assert option.label and len(option.label) <= 100
        assert option.description and len(option.description) <= 100
        assert option.emoji is not None
    # Discord lo acepta de verdad al montarlo.
    discord.ui.Select(options=options, custom_id="x:pick")


# -- Botones ------------------------------------------------------------------------


async def test_las_dos_vistas_tienen_el_boton_otras_tragaperras_con_custom_id_fijo(
    tmp_path: Path,
) -> None:
    clasica, volcan = await make_views(tmp_path)
    assert button(clasica, "tragaperras:other").label == SWITCH_LABEL
    assert button(volcan, "volcan:other").label == SWITCH_LABEL
    for key in THEMES:
        view = await botes(tmp_path, key)
        assert button(view, f"{key}:other").label == SWITCH_LABEL
        assert view.switch_prefix == view.machine_key == key
    assert clasica.machine_key == tragas_cambio.CLASSIC
    assert clasica.switch_prefix == "tragaperras"


async def test_ninguna_fila_pasa_de_cinco_componentes(tmp_path: Path) -> None:
    for view in await make_views(tmp_path):
        rows = Counter(item.row for item in view.children)
        assert max(rows.values()) <= 5, rows
    # La clásica con todos los extras de la fila de abajo a la vez.
    view = await classic(tmp_path)
    view.respin_offer = (MagicMock(), 100, 10, 1)
    view.double_offer = (100, 0)
    view.daily_streak = 1
    view._set_enabled(True)
    rows = Counter(item.row for item in view.children)
    assert max(rows.values()) <= 5, rows
    # También con el desplegable a la vista.
    for view in await make_views(tmp_path):
        view._show_picker()
        rows = Counter(item.row for item in view.children)
        assert max(rows.values()) <= 5, rows


async def test_los_custom_id_son_fijos_y_no_se_repiten(tmp_path: Path) -> None:
    for view in await make_views(tmp_path):
        first = ids(view)
        assert all(first), first
        assert len(first) == len(set(first))
        assert ids(view) == first
        view._show_picker()
        picking = ids(view)
        assert picking == [f"{view.switch_prefix}:pick", f"{view.switch_prefix}:back"]


# -- Desplegable y Volver -----------------------------------------------------------


async def test_pulsar_otras_tragaperras_enseña_el_desplegable_y_volver(tmp_path: Path) -> None:
    for view in await make_views(tmp_path):
        interaction = fake_interaction(make_user())

        await button(view, f"{view.switch_prefix}:other").callback(interaction)

        interaction.response.edit_message.assert_awaited_once()
        assert interaction.response.edit_message.await_args.kwargs["view"] is view
        interaction.response.send_message.assert_not_called()
        assert view.picking
        pick = button(view, f"{view.switch_prefix}:pick")
        assert isinstance(pick, discord.ui.Select)
        assert [o.value for o in pick.options] == [
            o.value for o in machine_options(view.machine_key)
        ]
        back = button(view, f"{view.switch_prefix}:back")
        assert back.label == "↩️ Volver"
        assert not pick.disabled and not back.disabled


async def test_volver_recupera_los_mismos_botones(tmp_path: Path) -> None:
    for view in await make_views(tmp_path):
        before = ids(view)
        labels = [getattr(item, "label", None) for item in view.children]
        await button(view, f"{view.switch_prefix}:other").callback(fake_interaction(make_user()))
        assert ids(view) != before
        interaction = fake_interaction(make_user())

        await button(view, f"{view.switch_prefix}:back").callback(interaction)

        interaction.response.edit_message.assert_awaited_once()
        assert not view.picking
        assert ids(view) == before
        assert [getattr(item, "label", None) for item in view.children] == labels
        assert not any(isinstance(item, discord.ui.Select) for item in view.children)


# -- Bloqueos -----------------------------------------------------------------------


async def assert_blocked(view: discord.ui.View, expected: str | None = None) -> None:
    before = ids(view)
    interaction = fake_interaction(make_user())

    await button(view, f"{view.switch_prefix}:other").callback(interaction)  # type: ignore[attr-defined]

    interaction.response.send_message.assert_awaited_once()
    call = interaction.response.send_message.await_args
    assert call.kwargs["ephemeral"] is True
    if expected is not None:
        assert expected in call.args[0]
    interaction.response.edit_message.assert_not_called()
    assert not view.picking  # type: ignore[attr-defined]
    assert ids(view) == before


async def test_la_clasica_con_giros_gratis_no_deja_cambiar(tmp_path: Path) -> None:
    view = await classic(tmp_path)
    view.free_spins = 3
    await assert_blocked(view, "3 giros gratis")


async def test_la_clasica_con_auto_en_marcha_no_deja_cambiar(tmp_path: Path) -> None:
    view = await classic(tmp_path)
    view.autoplay = MagicMock()
    await assert_blocked(view, "Auto")


async def test_la_clasica_ocupada_no_deja_cambiar(tmp_path: Path) -> None:
    view = await classic(tmp_path)
    view._busy = True
    await assert_blocked(view, tragas_cambio.BUSY_TEXT)


async def test_botes_ocupada_no_deja_cambiar(tmp_path: Path) -> None:
    view = await botes(tmp_path)
    view._busy = True
    await assert_blocked(view, tragas_cambio.BUSY_TEXT)


@pytest.mark.parametrize("key", list(THEMES))
async def test_botes_con_un_bonus_a_medias_no_deja_cambiar(tmp_path: Path, key: str) -> None:
    view = await botes(tmp_path, key)
    view.bonus = MagicMock()
    await assert_blocked(view, THEMES[key].named)


async def test_sin_bloqueos_se_puede_cambiar(tmp_path: Path) -> None:
    for view in await make_views(tmp_path):
        assert view.switch_blocker() is None
        assert view._cannot_switch() is None


# -- Cierre -------------------------------------------------------------------------


async def test_al_caducar_con_el_desplegable_abierto_quedan_los_botones_apagados(
    tmp_path: Path,
) -> None:
    for view in await make_views(tmp_path):
        original = ids(view) if not view.picking else []
        await button(view, f"{view.switch_prefix}:other").callback(fake_interaction(make_user()))
        assert view.picking
        view.cog.machines.add(view)

        await view.on_timeout()

        assert not view.picking
        assert view not in view.cog.machines
        assert ids(view) == original
        assert not any(isinstance(item, discord.ui.Select) for item in view.children)
        assert all(item.disabled for item in view.children)  # type: ignore[attr-defined]


async def test_retirar_para_la_vista_y_la_saca_de_las_maquinas(tmp_path: Path) -> None:
    for view in await make_views(tmp_path):
        view.cog.machines.add(view)

        ticket = await view.retire()

        assert ticket is None  # sin tiradas no hay ticket
        assert view.is_finished()
        assert view not in view.cog.machines


async def test_retirar_la_clasica_con_tiradas_devuelve_el_ticket(tmp_path: Path) -> None:
    view = await classic(tmp_path)
    view.cog.machines.add(view)
    view.session_spins = 4
    view.session_staked = 400
    view.session_gross = 100

    ticket = await view.retire()

    assert ticket and "4" in ticket
    assert view.is_finished()
    assert view not in view.cog.machines


# -- Logros -------------------------------------------------------------------------


def test_slots_switch_stats_cuenta_el_cambio_y_la_maquina() -> None:
    delta = slots_switch_stats("mina")
    assert delta.add == {"slots_switches": 1, f"{SLOTS_SWITCH_PREFIX}mina": 1}
    assert SLOTS_SWITCH_PREFIX == "slots_switch_to_"


def test_las_tragaperras_distintas_a_las_que_se_cambia_salen_en_el_derivado() -> None:
    assert with_derived({})[SLOTS_SWITCH_KINDS_STAT] == 0
    stats = {
        f"{SLOTS_SWITCH_PREFIX}mina": 3,
        f"{SLOTS_SWITCH_PREFIX}volcan": 1,
        f"{SLOTS_SWITCH_PREFIX}clasica": 0,  # a cero no cuenta
        f"{SLOTS_SWITCH_PREFIX}inventada": 5,  # no es una máquina
    }
    assert with_derived(stats)[SLOTS_SWITCH_KINDS_STAT] == 2
    every = {f"{SLOTS_SWITCH_PREFIX}{key}": 1 for key in SLOTS_MACHINES}
    assert with_derived(every)[SLOTS_SWITCH_KINDS_STAT] == len(SLOTS_MACHINES)


# -- Elegir en el desplegable con algo en marcha ------------------------------------


async def test_elegir_con_la_maquina_ocupada_solo_acepta_el_clic(tmp_path: Path) -> None:
    for view in await make_views(tmp_path):
        view._show_picker()
        view.picker._values = ["clasica" if view.machine_key != "clasica" else "volcan"]
        view._busy = True
        before = ids(view)
        interaction = fake_interaction(make_user())

        await view._pick(interaction)

        interaction.response.defer.assert_awaited_once()
        interaction.response.edit_message.assert_not_called()
        interaction.response.send_message.assert_not_called()
        interaction.followup.send.assert_not_called()
        interaction.edit_original_response.assert_not_called()
        assert view.picking
        assert ids(view) == before
        assert not view.is_finished()


async def test_elegir_con_un_bloqueo_avisa_en_privado_y_devuelve_los_botones(
    tmp_path: Path,
) -> None:
    view = await classic(tmp_path)
    original = ids(view)
    view._show_picker()
    view.picker._values = ["volcan"]
    view.free_spins = 2
    interaction = fake_interaction(make_user())

    await view._pick(interaction)

    interaction.response.edit_message.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
    assert "2 giros gratis" in interaction.followup.send.await_args.args[0]
    assert not view.picking
    assert ids(view) == original
