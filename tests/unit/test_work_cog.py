"""Pruebas de `bot.cogs.work`: el panel de `pala`, el minijuego con botones y los canales.

Usan el servicio real sobre un SQLite temporal; Discord se sustituye por
dobles (`MagicMock`). `roll` se fija para que no haya accidentes ni eventos.
"""

from __future__ import annotations

import asyncio
import random
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import ui
from interaction_fakes import fake_interaction

from bot.cogs import work as work_cog
from bot.cogs.work import PalaPanel, Work, game_id, parse_game_id
from bot.repositories.economy import EconomyRepository
from bot.repositories.work import WorkRepository
from bot.services import pala
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.levels import local_day
from bot.services.pala import WorkService
from bot.services.work import (
    BATTERY_MAX,
    BATTERY_MIN,
    LEGAL_EXTRAS_PER_WEEK,
    MEGAVIRAL,
    ORDINARY_SHIFTS,
    PENSION_CAP,
    Contract,
    Mechanic,
    ShiftRecord,
    battery_eta,
    rested_at,
)
from bot.services.work_catalog import JOB_BY_KEY
from bot.services.work_games import GRACE_SECONDS

GUILD = 1
OWNER = 10


@pytest.fixture(autouse=True)
def no_dice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pala, "roll", lambda rng, chance: False)


def make_user(user_id: int = OWNER) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = "Diego"
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


def make_interaction(user_id: int = OWNER) -> MagicMock:
    interaction = fake_interaction(make_user(user_id))
    interaction.message = MagicMock()
    interaction.message.edit = AsyncMock()
    return interaction


async def make_cog(tmp_path: Path) -> Work:
    economy_repo = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await economy_repo.initialize()
    repository = WorkRepository(tmp_path / "bot.db")
    await repository.initialize()
    service = WorkService(repository, EconomyService(economy_repo), rng=random.Random(5))
    bot = MagicMock()
    bot.birthdays = None
    return Work(bot, service)


async def open_panel(
    cog: Work, channel_id: int = 99, *, work: bool = True
) -> tuple[PalaPanel | None, AsyncMock]:
    """Abre `pala` y, con `work`, pasa del inicio al panel del curro (el botón 🪏 Trabajo)."""
    message = MagicMock()
    message.edit = AsyncMock()
    send = AsyncMock(return_value=message)
    errors = AsyncMock()
    await cog._open(
        guild=MagicMock(id=GUILD),
        channel=MagicMock(id=channel_id, parent_id=None),
        user=make_user(),
        send=send,
        send_error=errors,
    )
    panel = next(iter(cog.panels), None)
    if panel is not None and work:
        await panel.refresh()
    return panel, errors


def buttons(panel: PalaPanel) -> list[ui.Button]:
    return [item for item in panel.walk_children() if isinstance(item, ui.Button)]


def texts(panel: PalaPanel) -> str:
    return "\n".join(
        item.content for item in panel.walk_children() if isinstance(item, ui.TextDisplay)
    )


def check_limits(panel: PalaPanel) -> None:
    """Lo que Discord rechazaría al enviar: demasiados componentes o texto."""
    assert panel.total_children_count <= 40
    assert len(texts(panel)) <= 4000
    for item in panel.walk_children():
        if isinstance(item, ui.ActionRow):
            assert len(item.children) <= 5


async def test_sin_curro_el_panel_ofrece_todos_los_oficios(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, errors = await open_panel(cog)
    errors.assert_not_awaited()
    assert panel is not None
    (select,) = [i for i in panel.walk_children() if isinstance(i, ui.Select)]
    assert {o.value for o in select.options} == {
        "obra",
        "hosteleria",
        "politica",
        "sanidad",
        "oficina",
        "reparto",
        "funcionariado",
        "influencer",
    }
    assert "Coge la pala" in texts(panel)
    check_limits(panel)


async def test_firmar_y_fichar_un_turno_entero_con_botones(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "obra")
    assert any(b.label == "⛏️ Fichar" for b in buttons(panel))
    check_limits(panel)

    await panel._clock_in(make_interaction())
    assert panel.shift is not None
    game = panel.shift.game
    assert game.mechanic is Mechanic.DIG
    check_limits(panel)
    while panel.shift is not None:
        current = game.current
        assert current is not None
        await panel.game_click(make_interaction(), game.index, current.answer[0])
        if panel.shift is not None:
            check_limits(panel)
    assert "Nómina" in texts(panel) and "100/100" in texts(panel)
    check_limits(panel)
    balance = await cog.service.economy.balance(GUILD, OWNER)
    assert balance > STARTING_BALANCE
    await asyncio.sleep(0)  # deja que el temporizador cancelado se cierre
    assert not cog.timers


async def test_la_memoria_ensena_y_luego_oculta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "hosteleria")
    await panel._clock_in(make_interaction())
    assert panel.shift is not None and panel.shift.game.showing
    assert [b.label for b in buttons(panel)] == ["✅ Memorizado"]
    await panel.game_click(make_interaction(), panel.shift.game.index, "ver")
    assert not panel.shift.game.showing
    assert len(buttons(panel)) >= 5
    check_limits(panel)
    await panel._finish(None)


async def test_el_temporizador_espera_si_la_memoria_alarga_el_plazo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "hosteleria")
    await panel._clock_in(make_interaction())
    shift = panel.shift
    assert shift is not None and panel._timer is not None
    panel._timer.cancel()
    game = shift.game
    clock = [game.started_at]
    monkeypatch.setattr(cog.service, "now", lambda: clock[0])

    async def sleep(seconds: float) -> None:
        clock[0] += seconds
        if clock[0] == game.started_at + 1:
            game.frozen += 30  # mientras dormía, otra secuencia paró el reloj

    monkeypatch.setattr(asyncio, "sleep", sleep)
    await panel._deadline(shift)
    assert panel.shift is None
    assert clock[0] >= game.started_at + game.seconds + 30 + GRACE_SECONDS


async def test_si_se_acaba_el_tiempo_el_turno_se_cobra_solo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "politica")
    await panel._clock_in(make_interaction())
    await panel.on_timeout()
    assert panel.shift is None
    assert "Turno ordinario" in texts(panel)
    status = await cog.service.status(GUILD, OWNER)
    assert status is not None and status.contract.shifts_today == 1


async def test_pasado_el_limite_legal_el_jefe_ofrece_el_b(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "obra")
    status = await cog.service.status(GUILD, OWNER)
    assert status is not None
    contract = status.contract
    contract.shifts_today = ORDINARY_SHIFTS
    contract.extras_week = LEGAL_EXTRAS_PER_WEEK
    contract.shift_day = local_day(cog.service.now()).isoformat()
    await cog.service.repository.save_contract(GUILD, OWNER, contract)
    await panel._clock_in(make_interaction())
    assert panel.shift is None
    assert any("en B" in (b.label or "") for b in buttons(panel))
    await panel._clock_in_black(make_interaction())
    assert panel.shift is not None
    await panel._finish(None)
    assert "En B" in texts(panel)


async def test_la_pala_solo_se_coge_en_los_canales_del_tajo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    await cog.service.toggle_channel(GUILD, 5)
    panel, errors = await open_panel(cog, channel_id=6)
    assert panel is None
    errors.assert_awaited_once()
    assert errors.await_args.args[0] == "🪏 La pala está en <#5>."
    panel, errors = await open_panel(cog, channel_id=5)
    assert panel is not None
    errors.assert_not_awaited()


async def test_otro_miembro_no_puede_tocar_tu_pala(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    intruder = make_interaction(user_id=77)
    assert not await panel.interaction_check(intruder)
    intruder.response.send_message.assert_awaited_once()


async def test_la_maquina_de_cafe_recarga_y_avisa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "obra")
    await panel._coffee(make_interaction(), "barraquito")
    assert "barraquito" in texts(panel)
    assert await cog.service.economy.balance(GUILD, OWNER) < STARTING_BALANCE


async def test_tajo_anade_quita_y_abre_todos_los_canales(tmp_path: Path) -> None:
    from bot.cogs.admin import Admin

    cog = await make_cog(tmp_path)
    bot = MagicMock()
    bot.work = cog.service
    admin = Admin(bot)
    responder = MagicMock()
    responder.guild = MagicMock(id=GUILD)
    responder.send = AsyncMock()
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 5
    channel.mention = "<#5>"

    await admin._tajo_impl(responder, channel, False)
    assert await cog.service.channels(GUILD) == frozenset({5})
    assert "añadido" in responder.send.await_args.args[0]
    await admin._tajo_impl(responder, channel, False)
    assert await cog.service.channels(GUILD) == frozenset()
    await cog.service.toggle_channel(GUILD, 7)
    await admin._tajo_impl(responder, None, True)
    assert await cog.service.channels(GUILD) == frozenset()
    assert "cualquier canal" in responder.send.await_args.args[0]


async def test_no_se_puede_fichar_en_dos_paneles_a_la_vez(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    first, _ = await open_panel(cog)
    assert first is not None
    await first._hire(make_interaction(), "obra")
    second, _ = await open_panel(cog)
    second = next(p for p in cog.panels if p is not first)
    await first._clock_in(make_interaction())
    blocked = make_interaction()
    await second._clock_in(blocked)
    assert second.shift is None
    blocked.response.defer.assert_awaited_once()
    blocked.followup.send.assert_awaited_once()
    assert "otro panel" in blocked.followup.send.await_args.args[0]
    await first._finish(None)
    assert not cog.working


async def level_up(cog: Work, job: str, level: int) -> None:
    status = await cog.service.status(GUILD, OWNER)
    assert status is not None and status.contract.job == job
    status.contract.level = level
    await cog.service.repository.save_contract(GUILD, OWNER, status.contract)


async def test_sanidad_tiene_boton_de_guardia_y_la_guardia_se_juega(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "sanidad")
    assert not any(b.label == "🚑 Guardia" for b in buttons(panel))  # celador
    await level_up(cog, "sanidad", 3)
    await panel.refresh(make_interaction())
    assert any(b.label == "🚑 Guardia" for b in buttons(panel))
    check_limits(panel)
    await panel._clock_in_guard(make_interaction())
    assert panel.shift is not None
    await panel._finish(None)
    assert "saliente" in texts(panel)
    blocked = make_interaction()
    await panel._clock_in(blocked)
    assert "saliente" in blocked.followup.send.await_args.args[0]


async def test_oficina_teletrabajo_y_hong_kong_desde_el_panel(tmp_path: Path) -> None:
    from bot.repositories.economy import LedgerEntry

    cog = await make_cog(tmp_path)
    await cog.service.economy.repository.apply(GUILD, OWNER, [LedgerEntry(50_000, "prueba")])
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "oficina")
    labels = [b.label or "" for b in buttons(panel)]
    assert "🏠 Teletrabajo" in labels
    assert not any("Hong Kong" in label for label in labels)  # becario
    await level_up(cog, "oficina", 3)
    await panel.refresh(make_interaction())
    assert any("Irse a Hong Kong" in (b.label or "") for b in buttons(panel))
    check_limits(panel)
    await panel._go_abroad(make_interaction())
    assert "Te vas a Hong Kong" in texts(panel)
    assert any("Volver a casa" in (b.label or "") for b in buttons(panel))
    # Viviendo fuera no se cambia de oficio desde el panel.
    assert not [
        i
        for i in panel.walk_children()
        if isinstance(i, ui.Select) and "curro" in (i.placeholder or "")
    ]
    await panel._clock_in(make_interaction())
    await panel._finish(None)
    assert "Payslip" in texts(panel) and "Salaries tax" in texts(panel)
    await panel.refresh(make_interaction())
    await panel._go_home(make_interaction())
    assert "Vuelves a casa" in texts(panel)


def custom_ids(panel: PalaPanel) -> dict[str, str]:
    """`etiqueta → custom_id` de los botones que se ven ahora mismo."""
    return {b.label or "": b.custom_id or "" for b in buttons(panel)}


def component_interaction(custom_id: str, user_id: int = OWNER) -> MagicMock:
    interaction = make_interaction(user_id)
    interaction.type = discord.InteractionType.component
    interaction.data = {"custom_id": custom_id, "component_type": 2}
    return interaction


def test_el_custom_id_del_minijuego_va_y_vuelve() -> None:
    assert parse_game_id(game_id(3, 7, 2)) == (3, 7, 2)
    assert parse_game_id(game_id(3, 0, "ver")) == (3, 0, "ver")
    assert parse_game_id("pala:3:7:otra") is None
    assert parse_game_id("pala:x:7:1") is None
    assert parse_game_id("tienda:1") is None


async def test_los_clics_del_minijuego_llegan_por_el_listener(tmp_path: Path) -> None:
    """Los botones del minijuego no dependen del registro de la vista de discord.py."""
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "obra")
    await panel._clock_in(make_interaction())
    assert panel.shift is not None
    game = panel.shift.game
    current = game.current
    assert current is not None
    right = game_id(panel.token, game.index, current.answer[0])
    assert right in custom_ids(panel).values()
    # La vista no lo atiende (ni siquiera para su dueño): lo hace el listener.
    click = component_interaction(right)
    assert not await panel.interaction_check(click)
    click.response.send_message.assert_not_awaited()
    await cog.on_interaction(click)
    assert game.index == 1 and game.correct == 1
    click.response.edit_message.assert_awaited_once()
    await panel._finish(None)


async def test_un_clic_de_una_ronda_ya_pasada_se_acepta_sin_contar(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "obra")
    await panel._clock_in(make_interaction())
    assert panel.shift is not None
    game = panel.shift.game
    current = game.current
    assert current is not None
    await panel.game_click(make_interaction(), 0, current.answer[0])
    late = make_interaction()
    await panel.game_click(late, 0, current.answer[0])
    late.response.defer.assert_awaited_once()
    late.response.edit_message.assert_not_awaited()
    assert game.index == 1 and game.presses == 1 and game.stale == 1
    await panel._finish(None)


async def test_un_clic_con_el_panel_ocupado_se_acepta_antes_de_esperar(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "obra")
    await panel._clock_in(make_interaction())
    assert panel.shift is not None
    game = panel.shift.game
    current = game.current
    assert current is not None
    # Otro clic (o el cierre) tiene el candado mientras habla con Discord.
    await panel._render_lock.acquire()
    click = make_interaction()
    pressed = asyncio.create_task(panel.game_click(click, game.index, current.answer[0]))
    await asyncio.sleep(0)
    click.response.defer.assert_awaited_once()
    panel._render_lock.release()
    await pressed
    assert game.index == 1 and game.correct == 1
    click.edit_original_response.assert_awaited_once()  # y el panel se redibuja igual
    await panel._finish(None)


async def test_una_rafaga_en_la_memoria_cuenta_todas_las_pulsaciones(tmp_path: Path) -> None:
    """Clics seguidos con los botones de antes de redibujar: cuentan todos, en orden."""
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "politica")
    await panel._clock_in(make_interaction())
    assert panel.shift is not None
    game = panel.shift.game
    assert "Memoriza la secuencia" in texts(panel)
    await cog.on_interaction(component_interaction(custom_ids(panel)["✅ Memorizado"]))
    current = game.current
    assert current is not None and not game.showing
    ids = custom_ids(panel)
    sequence = [ids[current.options[i]] for i in current.answer]
    clicks = [component_interaction(custom_id) for custom_id in sequence]
    await asyncio.gather(*(cog.on_interaction(click) for click in clicks))
    assert game.perfect_rounds == 1 and game.index == 1
    for click in clicks:
        responses = click.response.edit_message.await_count + click.response.defer.await_count
        assert responses == 1
    # Solo el último redibuja; y la ronda nueva enseña cómo acabó la anterior.
    assert clicks[-1].response.edit_message.await_count == 1
    assert "Ronda anterior:** ✅" in texts(panel)
    await panel._finish(None)


async def test_un_fallo_en_la_memoria_se_ve_en_la_ronda_siguiente(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "politica")
    await panel._clock_in(make_interaction())
    assert panel.shift is not None
    game = panel.shift.game
    await panel.game_click(make_interaction(), 0, "ver")
    current = game.current
    assert current is not None
    wrong = next(i for i in range(len(current.options)) if i != current.answer[0])
    await panel.game_click(make_interaction(), 0, wrong)
    assert game.showing and game.index == 1
    assert "Fallaste en el 1.º: tocaba" in texts(panel)
    assert current.options[current.answer[0]] in texts(panel)
    await panel._finish(None)


async def test_otro_miembro_no_puede_jugar_tu_turno(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "obra")
    await panel._clock_in(make_interaction())
    assert panel.shift is not None
    current = panel.shift.game.current
    assert current is not None
    intruder = component_interaction(game_id(panel.token, 0, current.answer[0]), user_id=77)
    await cog.on_interaction(intruder)
    intruder.response.send_message.assert_awaited_once()
    assert panel.shift.game.presses == 0
    await panel._finish(None)


async def test_un_panel_caducado_avisa_al_pulsar(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    click = component_interaction(game_id(999, 0, 1))
    await cog.on_interaction(click)
    assert "caducó" in click.response.send_message.await_args.args[0]


async def test_las_herramientas_de_la_mochila_ayudan_en_el_turno(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = AsyncMock(return_value=frozenset({"chaleco_reflectante", "reloj_fichar", "huevo"}))
    monkeypatch.setattr(work_cog.tienda, "owned_keys", owned)
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "obra")
    await panel._clock_in(make_interaction())
    assert panel.shift is not None
    game = panel.shift.game
    assert game.retries == 1
    assert set(game.tools) == {"chaleco_reflectante", "reloj_fichar"}
    assert "🛠️" in texts(panel) and "fallo gratis" in texts(panel)
    current = game.current
    assert current is not None
    wrong = next(i for i in range(len(current.options)) if i not in current.answer)
    await panel.game_click(make_interaction(), 0, wrong)
    assert game.index == 0 and game.saved == 1
    assert "tu herramienta te salva" in texts(panel)
    assert "fallo gratis gastado" in texts(panel)
    await panel._finish(None)
    assert panel._tools_owned == 2


def test_el_panel_dice_cuando_se_llena_la_bateria() -> None:
    contract = Contract(job="obra", level=1, battery=50.0, battery_at=1_000.0)
    status = MagicMock(battery=50.0, now=1_000.0, contract=contract)
    note = work_cog.battery_note(status)
    assert note == f" · llena <t:{int(battery_eta(contract, BATTERY_MAX, 1_000.0))}:R>"
    assert "fichar" not in note
    assert work_cog.battery_note(MagicMock(battery=float(BATTERY_MAX), now=0.0)) == " · llena"


def test_el_panel_dice_cuando_se_puede_volver_a_fichar() -> None:
    contract = Contract(job="obra", level=1, battery=float(BATTERY_MIN), battery_at=1_000.0)
    status = MagicMock(battery=float(BATTERY_MIN), now=1_000.0, contract=contract)
    note = work_cog.battery_note(status)
    assert f"vuelves a fichar <t:{int(rested_at(contract, 1_000.0))}:R>" in note
    assert f"llena <t:{int(battery_eta(contract, BATTERY_MAX, 1_000.0))}:R>" in note


async def test_cada_oficio_nuevo_ensena_lo_suyo_en_el_panel(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "reparto")
    assert "Valoración en la app: ⭐ 4,5" in texts(panel)
    assert not any(b.label == "📚 Estudiar" for b in buttons(panel))
    await panel._hire(make_interaction(), "influencer")
    assert "Seguidores" in texts(panel)
    await panel._hire(make_interaction(), "funcionariado")
    assert "Trienios: 0" in texts(panel)
    check_limits(panel)


async def test_estudiar_la_oposicion_con_botones_no_paga(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "funcionariado")
    assert any(b.label == "📚 Estudiar" for b in buttons(panel))
    await panel._clock_in_study(make_interaction())
    assert panel.shift is not None
    game = panel.shift.game
    assert game.mechanic is Mechanic.DIALOGUE
    while panel.shift is not None:
        current = game.current
        assert current is not None
        await panel.game_click(make_interaction(), game.index, current.answer[0])
    assert "Estudio de la oposición" in texts(panel) and "Nómina" not in texts(panel)
    check_limits(panel)
    assert await cog.service.economy.balance(GUILD, OWNER) == STARTING_BALANCE
    await asyncio.sleep(0)


# -- Carrera larga: ramas, jubilación, pensión y anuncios -----------------------------------


async def set_career(cog: Work, level: int, branch: str = "") -> None:
    """Pone el contrato de política en ese puesto y rama."""
    contract = await cog.service.repository.contract(GUILD, OWNER)
    assert contract is not None and contract.job == "politica"
    contract.level, contract.branch = level, branch
    await cog.service.repository.save_contract(GUILD, OWNER, contract)


def button(panel: PalaPanel, label: str) -> ui.Button:
    return next(b for b in buttons(panel) if (b.label or "").startswith(label))


async def politics_panel(tmp_path: Path) -> tuple[Work, PalaPanel, AsyncMock]:
    """Panel de política con un canal que apunta lo que se anuncia."""
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    channel_send = AsyncMock()
    panel.channel = MagicMock(send=channel_send)
    await panel._hire(make_interaction(), "politica")
    return cog, panel, channel_send


async def test_al_final_del_tronco_el_ascenso_ofrece_las_ramas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pala, "promotion_blockers", lambda *args, **kwargs: [])
    cog, panel, channel_send = await politics_panel(tmp_path)
    await set_career(cog, 5)
    await panel.refresh(make_interaction())
    assert "eliges rama" in texts(panel).lower()
    await button(panel, "📈 Ascender").callback(make_interaction())
    text = texts(panel)
    assert "Moncloa" in text and "Bruselas" in text and "Comisario europeo" in text
    assert "al día" in text  # la pensión de cada rama
    check_limits(panel)
    await button(panel, "🏛️ Moncloa").callback(make_interaction())
    assert "Secretario de Estado" in texts(panel)
    status = await cog.service.status(GUILD, OWNER)
    assert status is not None and status.contract.branch == "moncloa"
    announced = channel_send.await_args
    assert "Secretario de Estado" in announced.args[0]
    assert announced.kwargs["allowed_mentions"].users is False  # sin mencionar a nadie


async def test_los_ascensos_del_tronco_no_se_anuncian(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pala, "promotion_blockers", lambda *args, **kwargs: [])
    _cog, panel, channel_send = await politics_panel(tmp_path)
    await panel.refresh(make_interaction())
    await button(panel, "📈 Ascender").callback(make_interaction())
    await button(panel, "✅ Acepto").callback(make_interaction())
    assert "Concejal" in texts(panel)
    channel_send.assert_not_awaited()


async def test_jubilarse_y_cobrar_la_pension_desde_el_panel(tmp_path: Path) -> None:
    cog, panel, channel_send = await politics_panel(tmp_path)
    await set_career(cog, 10, "bruselas")
    await panel.refresh(make_interaction())
    assert "jubilarte" in texts(panel)
    await button(panel, "🏖️ Jubilarse").callback(make_interaction())
    assert "No tiene vuelta atrás" in texts(panel) and "Moncloa" in texts(panel)
    await button(panel, "✅ Me jubilo").callback(make_interaction())
    assert "Te jubilas" in texts(panel)
    assert "se jubila" in channel_send.await_args.args[0]
    status = await cog.service.status(GUILD, OWNER)
    assert status is not None and status.contract.branch == "moncloa"
    check_limits(panel)
    await button(panel, "💰 Cobrar pensión").callback(make_interaction())
    assert "Pensión de hoy" in texts(panel) and "Neto" in texts(panel)
    assert not any("Cobrar pensión" in (b.label or "") for b in buttons(panel))
    again = make_interaction()
    await panel._claim_pension(again)
    assert "Ya cobraste" in again.followup.send.await_args.args[0]
    lines = await cog.career_lines(GUILD, OWNER)
    assert any("Jubilado de" in line and "Bruselas" in line for line in lines)


async def test_jubilarse_cobrar_y_fichar_en_otro_oficio_alimentan_los_logros(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    track = AsyncMock()
    monkeypatch.setattr(work_cog.logros, "track", track)
    cog, panel, _ = await politics_panel(tmp_path)
    assert track.await_args.args[4].peak == {"work_veteran_max": 0}  # el primer curro
    await set_career(cog, 10, "bruselas")
    await panel.refresh(make_interaction())
    await button(panel, "🏖️ Jubilarse").callback(make_interaction())
    await button(panel, "✅ Me jubilo").callback(make_interaction())
    retired = track.await_args.args[4]
    assert retired.add == {"work_retired": 1}  # sin fichar en la rama, la calidad es entera
    assert retired.peak == {
        "work_retired_politica": 1,
        "work_jobs_done": 0,
        "work_veteran_max": 0,
    }
    # Ha fichado hace nada: la pensión va en jubilación activa.
    await cog.service.repository.add_shift(
        panel.guild_id,
        panel.owner.id,
        ShiftRecord("politica", 10, "2026-10-06", cog.service.now(), 80, "ordinario", 1, 1),
    )
    await button(panel, "💰 Cobrar pensión").callback(make_interaction())
    claimed = track.await_args.args[4]
    assert claimed.add["work_pensions"] == 1
    assert claimed.add["work_pension_active"] == 1  # sigue fichando: jubilación activa
    assert claimed.peak["work_pension_pct"] == 20  # una rama arriesgada con calidad entera
    await panel._hire(make_interaction(), "obra")
    hired = track.await_args.args[4]
    assert hired.add == {"work_job_changes": 1}
    assert hired.peak == {"work_veteran_max": 1}


def test_las_estadisticas_de_jubilarse_cuentan_oficios_y_calidad() -> None:
    politica = JOB_BY_KEY["politica"]
    obra = JOB_BY_KEY["obra"]
    pension = pala.Pension(
        tuple((politica, branch) for branch in politica.branches) + ((obra, obra.branches[0]),),
        30_000,
        True,
        full=30_000,
        share=1.0,
    )
    assert work_cog.jobs_completed(pension) == 1
    status = SimpleNamespace(retired=frozenset({"x", "y"}), veteran=1)
    retirement = pala.Retirement(
        politica,
        politica.branches[1],
        "Comisario",
        None,
        status,
        quality=0.3,  # type: ignore[arg-type]
    )
    delta = work_cog.retire_stats(retirement, pension)
    assert delta.add == {"work_retired": 1, "work_retired_low_quality": 1}
    assert delta.peak == {"work_retired_politica": 2, "work_jobs_done": 1, "work_veteran_max": 1}
    whole = work_cog.pension_stats(pension)
    assert whole.add == {"work_pensions": 1, "work_pension_whole": 1}
    assert whole.peak == {"work_pension_pct": 30_000 * 100 // PENSION_CAP}


async def test_sin_curro_tambien_se_cobra_la_pension(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    panel.pension = pala.Pension(
        ((JOB_BY_KEY["politica"], JOB_BY_KEY["politica"].branches[0]),), 5_000, True
    )
    panel.show_hiring({}, [])
    assert any("Cobrar pensión" in (b.label or "") for b in buttons(panel))
    check_limits(panel)


async def test_el_panel_ensena_el_deseo_del_dia_y_las_ventajas(tmp_path: Path) -> None:
    cog, panel, _ = await politics_panel(tmp_path)
    assert "Deseo del día" in texts(panel)
    assert "Ventajas del puesto" not in texts(panel)  # el pegacarteles no tiene ninguna
    assert "Desbloquea" in texts(panel)  # la del concejal
    await set_career(cog, 5)
    await panel.refresh(make_interaction())
    text = texts(panel)
    assert "Ventajas del puesto" in text and "Coche oficial" in text
    assert "Política (PSOE) (5/10)" in text


def test_los_turnos_con_hito_se_anuncian_y_los_normales_no() -> None:
    viral = MagicMock(exit_payout=0, bankrupt=False, viral=MEGAVIRAL)
    assert "VIRAL" in (work_cog.outcome_announcement("Diego", viral) or "")
    normal = MagicMock(exit_payout=0, bankrupt=False, viral=1.0)
    assert work_cog.outcome_announcement("Diego", normal) is None
    rich = MagicMock(exit_payout=1_000_000, bankrupt=False, viral=0.0)
    assert "EXIT" in (work_cog.outcome_announcement("Diego", rich) or "")


# -- Familia ------------------------------------------------------------------------------


def selects(panel: PalaPanel) -> list[ui.Select]:
    return [item for item in panel.walk_children() if isinstance(item, ui.Select)]


async def test_el_panel_explica_la_familia_y_ofrece_regalos_y_favores(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "obra")
    text = texts(panel)
    assert "❤️ Familia: 100/100 · 🏡 **En casa feliz**" in text
    assert "por hora" in text
    assert any(b.label == "🎁 Regalos" for b in buttons(panel))
    family = next(s for s in selects(panel) if s.placeholder and "Familia" in s.placeholder)
    values = {o.value for o in family.options}
    assert {"f:tupper", "f:bizum"} <= values
    assert "f:excusa" not in values  # la excusa va en los marrones
    assert "b:flores_mama" in values and "g:flores_mama" not in values  # se compra
    check_limits(panel)


def test_los_regalos_de_la_mochila_salen_en_el_desplegable() -> None:
    contract = Contract(job="obra", level=1, family_used={"flores_mama": 1_000.0})
    options = work_cog.family_options(contract, frozenset({"flores_mama"}), 1_000.0 + 3600)
    gift = next(o for o in options if o.value == "g:flores_mama")
    assert "+10 ❤️" in gift.label and "en 11 h" in (gift.description or "")


def test_el_resultado_cuenta_lo_que_costo_la_familia() -> None:
    text = work_cog.family_change(80, 69, [("turno", -6), ("madrugada", -5)])
    assert text == "❤️ 69/100 (−11: turno −6, madrugada −5)"


async def test_en_un_marron_se_puede_tirar_de_excusa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "obra")
    event = work_cog.EVENT_BY_KEY["quedarse"]
    panel.pending_event = event
    panel._show_after("Turno", event)
    excuse = next(b for b in buttons(panel) if "Me llama mi madre" in b.label)
    await excuse.callback(make_interaction())
    assert panel.pending_event is None
    assert "me llama mi madre" in texts(panel)


def test_la_oferta_del_b_dice_el_riesgo_de_dia_y_el_de_noche() -> None:
    from bot.cogs.work import black_offer_text

    day = black_offer_text(0)
    assert "10 % de que te pille la Inspección" in day and "20 % de recargo" in day
    night = black_offer_text(2)
    assert "2.ª franja de noche" in night and "40 % de que te pille la Inspección" in night
    assert "turno doble" in night


@pytest.mark.parametrize(
    ("day", "level", "branch", "mechanic", "shown", "result"),
    [
        (1, 5, "", Mechanic.DEAL, "Trato 1/3", "Tratos:"),
        (1, 6, "moncloa", Mechanic.PLAN, "Ahora (1/", "piezas encajadas"),
        (2, 9, "moncloa", Mechanic.DESK, "Decisión 1/8", "/6"),
    ],
)
async def test_el_dia_de_rotacion_se_juega_el_otro_minijuego_con_botones(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    day: int,
    level: int,
    branch: str,
    mechanic: Mechanic,
    shown: str,
    result: str,
) -> None:
    monkeypatch.setattr(pala, "rotation_day", lambda now: day)
    cog, panel, _send = await politics_panel(tmp_path)
    await set_career(cog, level, branch)
    await panel.refresh(make_interaction())
    assert work_cog.MECHANIC_NAMES[mechanic] in texts(panel)
    await panel._clock_in(make_interaction())
    assert panel.shift is not None
    game = panel.shift.game
    assert game.mechanic is mechanic and shown in texts(panel)
    check_limits(panel)
    while panel.shift is not None:
        await panel.game_click(make_interaction(), game.index, game.best())
        if panel.shift is not None:
            check_limits(panel)
    # En el Despacho no hay jugada perfecta; en los demás, jugar lo mejor es un 100.
    assert result in texts(panel)
    assert mechanic is Mechanic.DESK or "100/100" in texts(panel)
    check_limits(panel)
    # Las tareas del puesto cuentan en su contador de siempre aunque hoy rote.
    contract = await cog.service.repository.contract(GUILD, OWNER)
    usual = JOB_BY_KEY["politica"].position(level, branch).mechanic
    stat = {Mechanic.DIALOGUE: "smooth", Mechanic.SORT: "sorted"}[usual]
    assert contract is not None and contract.progress[stat] == game.correct
    await asyncio.sleep(0)


async def test_la_auditoria_se_juega_desde_el_panel_con_su_rejilla_de_botones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pala, "rotation_day", lambda now: 2)
    cog = await make_cog(tmp_path)
    panel, _ = await open_panel(cog)
    assert panel is not None
    await panel._hire(make_interaction(), "obra")
    contract = await cog.service.repository.contract(GUILD, OWNER)
    assert contract is not None
    contract.level = 5
    await cog.service.repository.save_contract(GUILD, OWNER, contract)
    await panel.refresh(make_interaction())
    await panel._clock_in(make_interaction())
    assert panel.shift is not None
    game = panel.shift.game
    assert game.mechanic is Mechanic.AUDIT
    grid = [b for b in buttons(panel) if parse_game_id(b.custom_id or "") is not None]
    assert len(grid) == 20
    check_limits(panel)
    while panel.shift is not None:
        await panel.game_click(make_interaction(), game.index, game.best())
        if panel.shift is not None:
            check_limits(panel)
    assert "100/100" in texts(panel) and "tableros limpios" in texts(panel)
    await asyncio.sleep(0)
