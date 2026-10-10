"""Pruebas de bot.cogs.craps: la mesa de dados con botones, animación y dinero.

Se usa la economía real sobre un SQLite temporal, un dibujante falso (las imágenes
tienen sus propias pruebas), el margen del GIF a cero y el azar de guion de
`craps_fakes`: cada tirada son dos dados y cada pulsación de tirar gasta una.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from craps_fakes import Scripted
from discord import ui
from interaction_fakes import fake_interaction

from bot.cogs import craps as craps_cog
from bot.cogs.craps import (
    COLOR_IDLE,
    COLOR_LOST,
    COLOR_POINT,
    COLOR_PUSH,
    COLOR_WON,
    SHOUT_POINTS,
    Craps,
    CrapsView,
    outcome_line,
    percent,
)
from bot.repositories import sqlite as sqlite_module
from bot.repositories.economy import EconomyRepository
from bot.services.craps import (
    ODDS_MAX,
    Bet,
    CrapsGame,
    Hand,
    Roll,
    Status,
    format_odds,
    milestone,
    odds_profit,
)
from bot.services.craps_render import OPENING_REST, Media, Table
from bot.services.economy import STARTING_BALANCE, EconomyService, format_amount
from bot.services.taxes import gambling_day_tax

PASS, DONT = Bet.PASS, Bet.DONT
GUILD_ID = 1
OWNER_ID = 10

#: Tiradas con nombre (dos dados cada una).
NATURAL = (3, 4)  # 7
ELEVEN = (5, 6)
SNAKE_EYES = (1, 1)  # 2
BOXCARS = (6, 6)  # 12
SIX = (1, 5)  # 6, un punto
SIX_MADE = (2, 4)
NOTHING = (6, 5)  # 11: con el punto puesto no decide


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(craps_cog, "REVEAL_MARGIN_SECONDS", 0)


def make_user(user_id: int = OWNER_ID) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = "Diego" if user_id == OWNER_ID else "Intruso"
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


def make_interaction(user_id: int = OWNER_ID, *, events: list[str] | None = None) -> MagicMock:
    return fake_interaction(make_user(user_id), events=events)


def fake_renderer() -> MagicMock:
    renderer = MagicMock()
    renderer.throw = AsyncMock(
        return_value=Media(gif=b"GIF89a", png=b"png", seconds=0.0, rest=OPENING_REST)
    )
    renderer.board = AsyncMock(return_value=b"png")
    renderer.close = AsyncMock()
    return renderer


async def make_cog(tmp_path: Path, *rolls: tuple[int, int]) -> Craps:
    """Cog con economía real y azar de guion (las tiradas que se vayan a hacer, en orden)."""
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    return Craps(
        MagicMock(),
        economy=EconomyService(repository),
        rng=Scripted(*rolls),
        renderer=fake_renderer(),
    )


async def open_table(
    cog: Craps, *, amount: str = "100", bet: Bet | None = None, channel: object = None
) -> tuple[CrapsView, AsyncMock]:
    send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    errors = AsyncMock()
    await cog._dados_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=channel,
        user=make_user(),
        amount_text=amount,
        bet=bet,
        send=send,
        send_error=errors,
    )
    errors.assert_not_awaited()
    send.assert_awaited_once()
    assert send.await_args.kwargs["file"].filename == "dados.png"
    (view,) = cog.views
    return view, send


def labels(view: CrapsView) -> list[str]:
    return [item.label or "" for item in view.children if isinstance(item, ui.Button)]


def button(view: CrapsView, prefix: str) -> ui.Button:
    return next(
        b for b in view.children if isinstance(b, ui.Button) and (b.label or "").startswith(prefix)
    )


async def balance(cog: Craps) -> int:
    return await cog.economy.balance(GUILD_ID, OWNER_ID)


def ledger(tmp_path: Path) -> list[tuple[str, int]]:
    """Movimientos del dueño (motivo, importe), en orden y sin el saldo de bienvenida."""
    connection = sqlite_module.connect(tmp_path / "bot.db")
    try:
        rows = connection.execute(
            "SELECT reason, delta FROM economy_ledger WHERE guild_id = ? AND user_id = ? "
            "ORDER BY id",
            (GUILD_ID, OWNER_ID),
        ).fetchall()
    finally:
        connection.close()
    return [(row[0], row[1]) for row in rows if row[0] not in ("bienvenida", "prueba")]


async def press(view: CrapsView, which: str, *, user_id: int = OWNER_ID) -> MagicMock:
    """Pulsa un botón por su método (`_pass`, `_dont`, `_roll`, `_odds_one`, `_odds_max`…)."""
    interaction = make_interaction(user_id)
    await getattr(view, which)(interaction)
    return interaction


def played(view: CrapsView) -> CrapsGame:
    assert view.game is not None
    return view.game


def settle_tax(net: int) -> int:
    return gambling_day_tax(net, 0)


# -- Abrir la mesa ----------------------------------------------------------------------


async def test_abrir_la_mesa_no_cobra_y_enseña_pase_y_no_pase(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, send = await open_table(cog, amount="250")
    assert await balance(cog) == STARTING_BALANCE
    assert ledger(tmp_path) == []
    assert view.game is None
    cog.renderer.board.assert_awaited_once()
    cog.renderer.throw.assert_not_awaited()
    assert labels(view) == [
        f"✅ Pase · {format_amount(250)}",
        f"🚫 No pase · {format_amount(250)}",
        "½",
        "×2",
        "💰 All-in",
    ]
    embed = send.await_args.kwargs["embed"]
    assert "¿Pase o no pase?" in (embed.description or "")
    assert embed.color == COLOR_IDLE
    assert not any(b.disabled for b in view.children if isinstance(b, ui.Button))


async def test_sin_apuesta_se_juega_la_de_por_defecto_y_sin_saldo_no_abre_mesa(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path)
    send, errors = AsyncMock(), AsyncMock()
    kwargs = dict(guild=MagicMock(id=GUILD_ID), channel=None, user=make_user(), bet=None)
    await cog._dados_impl(amount_text=None, send=send, send_error=errors, **kwargs)
    (view,) = cog.views
    assert view.stake == craps_cog.DEFAULT_STAKE

    needed = STARTING_BALANCE * 5
    await cog._dados_impl(amount_text=str(needed), send=send, send_error=errors, **kwargs)
    errors.assert_awaited_once()
    assert f"Necesitas {format_amount(needed)}" in errors.await_args.args[0]
    assert len(cog.views) == 1


async def test_los_dados_solo_se_juegan_en_un_servidor(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    errors = AsyncMock()
    await cog._dados_impl(
        guild=None,
        channel=None,
        user=make_user(),
        amount_text="100",
        bet=None,
        send=AsyncMock(),
        send_error=errors,
    )
    errors.assert_awaited_once()
    assert "servidor" in errors.await_args.args[0]
    assert not cog.views


async def test_solo_se_juega_en_los_canales_del_casino(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    cog.casino_channel_ids = frozenset({555})
    errors = AsyncMock()
    await cog._dados_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=MagicMock(spec=discord.TextChannel, id=1),
        user=make_user(),
        amount_text="100",
        bet=None,
        send=AsyncMock(),
        send_error=errors,
    )
    errors.assert_awaited_once()
    assert not cog.views


# -- La salida: Pase y No pase ----------------------------------------------------------


async def test_pulsar_pase_cobra_la_apuesta_y_tira_la_salida(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX)
    view, _ = await open_table(cog, amount="100")
    await press(view, "_pass")
    assert await balance(cog) == STARTING_BALANCE - 100
    assert ledger(tmp_path) == [("dados:apuesta", -100)]
    cog.renderer.throw.assert_awaited_once()
    game = played(view)
    assert game.status is Status.POINT and game.point == 6 and game.bet is PASS
    assert game.stake == 100


async def test_pulsar_no_pase_apuesta_en_contra(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX)
    view, _ = await open_table(cog)
    await press(view, "_dont")
    assert played(view).bet is DONT and view.bet is DONT


async def test_un_natural_con_pase_paga_el_doble_y_el_libro_cuadra(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, NATURAL)
    start = await cog.economy.grant(GUILD_ID, OWNER_ID, amount=9_000, reason="prueba")
    view, _ = await open_table(cog, amount="3000")
    interaction = await press(view, "_pass")

    game = played(view)
    assert game.status is Status.WON
    assert game.payout == 2 * game.stake and game.net == game.stake
    tax = settle_tax(game.net)
    assert tax > 0
    final = await balance(cog)
    assert final == start - 3_000 + game.payout - tax
    movements = ledger(tmp_path)
    assert movements[:2] == [("dados:apuesta", -3_000), ("dados:premio", game.payout)]
    # Cada yapdollar que se mueve está en el libro: saldo = inicial + suma de movimientos.
    assert start + sum(delta for _reason, delta in movements) == final
    assert await cog.economy.state_balance(GUILD_ID) == tax
    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    assert f"+{format_amount(game.net)}" in (embed.description or "")
    assert embed.color == COLOR_WON
    # Al acabar vuelven la apuesta y los botones de salida.
    assert labels(view)[0] == f"✅ Pase · {format_amount(3_000)}"


async def test_una_pifia_con_pase_paga_cero_y_el_dinero_no_va_al_estado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SNAKE_EYES)
    view, _ = await open_table(cog, amount="300")
    interaction = await press(view, "_pass")
    game = played(view)
    assert game.status is Status.LOST and game.payout == 0 and game.net == -300
    assert await balance(cog) == STARTING_BALANCE - 300
    assert ledger(tmp_path) == [("dados:apuesta", -300)]
    assert await cog.economy.state_balance(GUILD_ID) == 0
    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    assert f"-{format_amount(300)}" in (embed.description or "")
    assert embed.color == COLOR_LOST
    assert labels(view)[0].startswith("✅ Pase")  # se puede volver a jugar


async def test_no_pase_gana_con_una_pifia_y_pierde_con_un_natural(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SNAKE_EYES, NATURAL)
    view, _ = await open_table(cog, amount="100")
    await press(view, "_dont")
    assert played(view).status is Status.WON
    won = await balance(cog)
    assert won == STARTING_BALANCE - 100 + 200 - settle_tax(100)
    await press(view, "_dont")
    assert played(view).status is Status.LOST
    assert await balance(cog) == won - 100


async def test_la_barra_del_12_con_no_pase_devuelve_la_apuesta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, BOXCARS)
    view, _ = await open_table(cog, amount="200")
    interaction = await press(view, "_dont")
    game = played(view)
    assert game.status is Status.PUSH and game.net == 0
    assert await balance(cog) == STARTING_BALANCE
    assert ledger(tmp_path) == [("dados:apuesta", -200), ("dados:premio", 200)]
    assert await cog.economy.state_balance(GUILD_ID) == 0
    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    assert embed.color == COLOR_PUSH and "Empate" in (embed.description or "")
    assert "barra" in (embed.description or "")


async def test_el_12_con_pase_pierde(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, BOXCARS)
    view, _ = await open_table(cog)
    await press(view, "_pass")
    assert played(view).status is Status.LOST


# -- El punto ---------------------------------------------------------------------------


async def test_con_el_punto_puesto_los_botones_son_tirar_y_odds(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX)
    view, _ = await open_table(cog, amount="100")
    interaction = await press(view, "_pass")
    assert labels(view) == [
        "🎲 Tirar",
        f"➕ Odds {format_amount(100)} · {format_odds(PASS, 6)}",
        "⏫ Odds al máximo",
    ]
    assert not any(b.disabled for b in view.children if isinstance(b, ui.Button))
    final = interaction.edit_original_response.await_args.kwargs["embed"]
    assert "Punto: 6" in (final.description or "") and final.color == COLOR_POINT
    assert format_odds(PASS, 6) in (final.description or "")


async def test_con_no_pase_el_texto_del_punto_dice_que_vale_un_siete(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX)
    view, _ = await open_table(cog)
    await press(view, "_dont")
    assert "Ahora te vale un **7**" in view.description()
    assert format_odds(DONT, 6) in labels(view)[1]


async def test_hacer_el_punto_con_pase_gana_y_paga(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX, NOTHING, SIX_MADE)
    view, _ = await open_table(cog, amount="100")
    await press(view, "_pass")
    nothing = await press(view, "_roll")  # un 11 no decide con el punto puesto
    assert played(view).status is Status.POINT
    assert "Sale" in (nothing.edit_original_response.await_args.kwargs["embed"].description or "")
    assert ledger(tmp_path) == [("dados:apuesta", -100)]  # ni se paga ni se cobra más
    await press(view, "_roll")
    game = played(view)
    assert game.status is Status.WON and len(game.rolls) == 3
    assert await balance(cog) == STARTING_BALANCE - 100 + 200 - settle_tax(100)
    assert ("dados:premio", 200) in ledger(tmp_path)


async def test_el_siete_fuera_pierde_pase_y_acaba_la_mano(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX, NATURAL)
    view, _ = await open_table(cog)
    await press(view, "_pass")
    interaction = await press(view, "_roll")
    assert played(view).status is Status.LOST
    assert view.hand.seven_out
    assert "siete fuera" in (
        interaction.edit_original_response.await_args.kwargs["embed"].description or ""
    )
    assert await balance(cog) == STARTING_BALANCE - 100


async def test_el_siete_fuera_gana_no_pase(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX, NATURAL)
    view, _ = await open_table(cog)
    await press(view, "_dont")
    await press(view, "_roll")
    assert played(view).status is Status.WON
    assert await balance(cog) == STARTING_BALANCE - 100 + 200 - settle_tax(100)


async def test_tirar_sin_partida_no_hace_nada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, NATURAL)
    view, _ = await open_table(cog)
    interaction = await press(view, "_roll")
    interaction.response.defer.assert_awaited_once()
    assert view.game is None and await balance(cog) == STARTING_BALANCE
    cog.renderer.throw.assert_not_awaited()


# -- Odds -------------------------------------------------------------------------------


async def test_poner_odds_cobra_lo_mismo_que_la_apuesta_y_repinta_la_mesa(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, SIX)
    view, _ = await open_table(cog, amount="100")
    await press(view, "_pass")
    interaction = await press(view, "_odds_one")

    game = played(view)
    assert game.odds == 100 and game.status is Status.POINT
    assert await balance(cog) == STARTING_BALANCE - 200
    assert ledger(tmp_path) == [("dados:apuesta", -100), ("dados:apuesta", -100)]
    assert cog.renderer.board.await_count == 2  # al abrir y al poner las odds
    assert interaction.edit_original_response.await_args.kwargs["attachments"][0].filename == (
        "dados.png"
    )
    assert f"Odds {format_amount(100)} a {format_odds(PASS, 6)}" in view.description()
    interaction.response.defer.assert_awaited_once()


async def test_las_odds_no_tiran_los_dados(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX)
    view, _ = await open_table(cog)
    await press(view, "_pass")
    await press(view, "_odds_one")
    assert cog.renderer.throw.await_count == 1
    assert len(played(view).rolls) == 1


async def test_las_odds_llegan_al_tope_y_entonces_se_apagan_los_botones(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX)
    view, _ = await open_table(cog, amount="100")
    await press(view, "_pass")
    for _ in range(ODDS_MAX):
        await press(view, "_odds_one")
    game = played(view)
    assert game.odds == ODDS_MAX * 100 and game.odds_full
    assert button(view, "➕ Odds").disabled and button(view, "⏫").disabled
    assert not button(view, "🎲").disabled
    # Un clic de más (llega tarde, con el botón ya apagado en el cliente) no cobra otra vez.
    before = await balance(cog)
    await press(view, "_odds_one")
    assert game.odds == ODDS_MAX * 100 and await balance(cog) == before


async def test_odds_al_maximo_pone_hasta_el_tope_si_llega_el_saldo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX)
    view, _ = await open_table(cog, amount="100")
    await press(view, "_pass")
    await press(view, "_odds_max")
    assert played(view).odds == ODDS_MAX * 100
    assert await balance(cog) == STARTING_BALANCE - 100 - ODDS_MAX * 100


async def test_odds_al_maximo_pone_lo_que_llegue_del_saldo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX)
    stake = STARTING_BALANCE // 2
    view, _ = await open_table(cog, amount=str(stake))
    await press(view, "_pass")
    left = await balance(cog)
    assert 0 < left < ODDS_MAX * stake
    await press(view, "_odds_max")
    assert played(view).odds == left
    assert await balance(cog) == 0
    # Con el saldo a cero ya no hay más que poner: avisa y no cambia nada.
    interaction = await press(view, "_odds_max")
    interaction.followup.send.assert_awaited_once()
    assert played(view).odds == left


async def test_una_odds_sin_saldo_avisa_y_no_cobra(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX)
    stake = STARTING_BALANCE - 100
    view, _ = await open_table(cog, amount=str(stake))
    await press(view, "_pass")
    assert await balance(cog) == 100
    await cog.economy.place_bet(GUILD_ID, OWNER_ID, game="otro", stake=60)
    interaction = await press(view, "_odds_one")
    assert played(view).odds == 0
    assert await balance(cog) == 40
    interaction.followup.send.assert_awaited_once()
    assert "Necesitas" in interaction.followup.send.await_args.args[0]


async def test_odds_ganadoras_con_pase_cobran_apuesta_mas_odds_con_su_premio(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, SIX, SIX_MADE)
    view, _ = await open_table(cog, amount="100")
    await press(view, "_pass")
    await press(view, "_odds_one")
    await press(view, "_odds_one")
    await press(view, "_roll")

    game = played(view)
    assert game.status is Status.WON and game.odds == 200
    assert game.payout == 2 * 100 + 200 + odds_profit(PASS, 6, 200)
    assert game.net == game.payout - game.wagered
    tax = settle_tax(game.net)
    final = await balance(cog)
    assert final == STARTING_BALANCE - game.wagered + game.payout - tax
    movements = ledger(tmp_path)
    assert ("dados:premio", game.payout) in movements
    assert STARTING_BALANCE + sum(delta for _r, delta in movements) == final
    assert f"Las Odds pagan {format_odds(PASS, 6)}" in view.description()


async def test_odds_perdedoras_se_pierden_con_la_apuesta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX, NATURAL)
    view, _ = await open_table(cog, amount="100")
    await press(view, "_pass")
    await press(view, "_odds_one")
    await press(view, "_roll")
    game = played(view)
    assert game.status is Status.LOST and game.net == -200
    assert await balance(cog) == STARTING_BALANCE - 200
    assert "-" + format_amount(200) in view.description()


async def test_odds_ganadoras_con_no_pase_cobran_la_cuota_inversa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX, NATURAL)
    view, _ = await open_table(cog, amount="100")
    await press(view, "_dont")
    await press(view, "_odds_max")
    await press(view, "_roll")
    game = played(view)
    assert game.status is Status.WON and game.odds == ODDS_MAX * 100
    assert game.payout == 200 + game.odds + odds_profit(DONT, 6, game.odds)
    assert ("dados:premio", game.payout) in ledger(tmp_path)


async def test_las_odds_sin_punto_no_hacen_nada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog)
    for which in ("_odds_one", "_odds_max"):
        interaction = await press(view, which)
        interaction.response.defer.assert_awaited_once()
    assert view.game is None
    assert await balance(cog) == STARTING_BALANCE and ledger(tmp_path) == []


# -- Los botones ------------------------------------------------------------------------


async def test_cada_boton_contesta_una_sola_vez_y_antes_de_editar(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX, NATURAL)
    view, _ = await open_table(cog)
    events: list[str] = []
    first = make_interaction(events=events)
    await view._pass(first)
    first.response.defer.assert_awaited_once()  # cualquier segunda respuesta lanzaría error
    first.response.edit_message.assert_not_awaited()
    assert events[0] == "response.defer"
    assert events.count("edit_original_response") == 3  # apagar, GIF y PNG
    events.clear()
    odds = make_interaction(events=events)
    await view._odds_one(odds)
    assert events[0] == "response.defer" and events.count("response.defer") == 1
    assert events.count("edit_original_response") == 1
    events.clear()
    roll = make_interaction(events=events)
    await view._roll(roll)
    assert events[0] == "response.defer" and events.count("response.defer") == 1


async def test_el_gif_es_neutro_y_luego_viene_el_png_sin_destripar(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, NATURAL)
    view, _ = await open_table(cog)
    states: list[list[bool]] = []

    async def capture(**kwargs: object) -> None:
        states.append([b.disabled for b in view.children if isinstance(b, ui.Button)])

    interaction = make_interaction()
    interaction.edit_original_response.side_effect = capture
    await view._pass(interaction)

    wait_call, gif_call, png_call = interaction.edit_original_response.await_args_list
    # Antes del GIF, la mesa apaga los botones y enseña la jugada sin adjuntos nuevos.
    assert "attachments" not in wait_call.kwargs
    assert wait_call.kwargs["embed"].description == gif_call.kwargs["embed"].description
    assert gif_call.kwargs["attachments"][0].filename == "dados.gif"
    assert png_call.kwargs["attachments"][0].filename == "dados.png"
    waiting = gif_call.kwargs["embed"]
    # Mientras ruedan los dados, ni el texto ni el color dicen cómo caen.
    assert waiting.color == COLOR_IDLE
    assert "tirada de salida" in (waiting.description or "")
    for hint in ("natural", "Sale", "+", "-100", "Punto"):
        assert hint not in (waiting.description or "")
    # Mientras se pinta y durante el GIF todo está apagado; después, vuelven los botones.
    assert all(states[0]) and all(states[1]) and not any(states[2])
    assert "natural" in (png_call.kwargs["embed"].description or "")


async def test_el_texto_del_gif_de_una_tirada_con_punto_nombra_el_punto_sin_decir_el_resultado(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, SIX, NATURAL)
    view, _ = await open_table(cog)
    await press(view, "_pass")
    interaction = await press(view, "_roll")
    waiting = interaction.edit_original_response.await_args_list[0].kwargs["embed"]
    assert "a por el **6**" in (waiting.description or "")
    assert "siete fuera" not in (waiting.description or "")


async def test_pulsar_con_la_mesa_ocupada_no_tira_otra_vez(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, NATURAL)
    view, _ = await open_table(cog)
    view._busy = True  # la tirada anterior sigue rodando
    interaction = await press(view, "_pass")
    interaction.response.defer.assert_awaited_once()
    assert view.game is None
    assert await balance(cog) == STARTING_BALANCE
    cog.renderer.throw.assert_not_awaited()


async def test_sin_saldo_para_la_apuesta_avisa_y_no_empieza(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, NATURAL)
    view, _ = await open_table(cog, amount="900")
    await cog.economy.place_bet(GUILD_ID, OWNER_ID, game="otro", stake=500)
    interaction = await press(view, "_pass")
    assert view.game is None
    assert await balance(cog) == 500
    interaction.followup.send.assert_awaited_once()
    assert "Necesitas 900 Y$" in interaction.followup.send.await_args.args[0]
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True


async def test_la_apuesta_se_cambia_con_medio_doble_y_all_in(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog, amount="200")
    await view._halve(make_interaction())
    assert view.stake == 100 and labels(view)[0] == f"✅ Pase · {format_amount(100)}"
    await view._double(make_interaction())
    await view._double(make_interaction())
    assert view.stake == 400
    await view._all_in(make_interaction())
    assert view.stake == STARTING_BALANCE
    await view._double(make_interaction())  # no se puede apostar más de lo que hay
    assert view.stake == STARTING_BALANCE
    # Cambiar la apuesta no mueve dinero.
    assert await balance(cog) == STARTING_BALANCE
    assert ledger(tmp_path) == []


async def test_la_apuesta_no_se_cambia_con_el_punto_puesto(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX)
    view, _ = await open_table(cog, amount="100")
    await press(view, "_pass")
    for which in ("_halve", "_double", "_all_in"):
        interaction = await press(view, which)
        interaction.response.defer.assert_awaited_once()
    assert view.stake == 100 and played(view).stake == 100


async def test_all_in_sin_saldo_avisa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog, amount="100")
    await cog.economy.place_bet(GUILD_ID, OWNER_ID, game="otro", stake=STARTING_BALANCE)
    interaction = await press(view, "_all_in")
    interaction.followup.send.assert_awaited_once()
    assert view.stake == 100


async def test_otro_usuario_no_puede_tocar_la_mesa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, NATURAL)
    view, _ = await open_table(cog)
    stranger = make_interaction(user_id=99)
    assert await view.interaction_check(stranger) is False
    stranger.response.send_message.assert_awaited_once()
    assert "Estos dados son de Diego" in stranger.response.send_message.await_args.args[0]
    assert stranger.response.send_message.await_args.kwargs["ephemeral"] is True
    assert view.game is None and await balance(cog) == STARTING_BALANCE

    owner = make_interaction()
    assert await view.interaction_check(owner) is True
    owner.response.send_message.assert_not_awaited()


# -- La mano ----------------------------------------------------------------------------


async def test_la_mano_sigue_entre_partidas_mientras_no_haya_siete_fuera(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, NATURAL, SIX, SIX_MADE)
    view, _ = await open_table(cog)
    await press(view, "_pass")  # natural: gana
    assert view.hand.rolls == 1
    await press(view, "_pass")  # nueva partida, misma mano
    await press(view, "_roll")
    assert view.hand.rolls == 3 and view.hand.points == [6]
    assert not view.hand.seven_out
    assert len(view.history) == 3


async def test_la_mano_se_reinicia_tras_un_siete_fuera_al_empezar_la_siguiente(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, SIX, NATURAL, SIX)
    view, _ = await open_table(cog)
    await press(view, "_pass")
    await press(view, "_roll")
    assert view.hand.seven_out and view.hand.rolls == 2
    old_hand = view.hand
    await press(view, "_pass")
    assert view.hand is not old_hand
    assert not view.hand.seven_out and view.hand.rolls == 1 and view.hand.points == []
    assert len(view.history) == 1


async def test_la_mesa_pinta_el_historial_de_la_mano(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX, NOTHING)
    view, _ = await open_table(cog)
    await press(view, "_pass")
    await press(view, "_roll")
    table = view.table()
    assert [roll.dice for roll, _bet in table.history] == [SIX, NOTHING]
    assert table.game is view.game and table.hand is view.hand
    assert cog.renderer.throw.await_args.args[0].history == table.history


# -- Texto ------------------------------------------------------------------------------


def test_percent_usa_coma_y_quita_el_cero_decimal() -> None:
    assert percent(0.5) == "50 %"
    assert percent(0.4949) == "49,5 %"
    assert percent(1 / 3) == "33,3 %"


@pytest.mark.parametrize(
    ("roll", "bet", "expected"),
    [
        (Roll((3, 4)), PASS, "natural"),
        (Roll((1, 1)), PASS, "pifia"),
        (Roll((6, 6)), DONT, "la barra"),
        (Roll((6, 6)), PASS, "pifia"),
        (Roll((2, 2), 4), PASS, "punto hecho"),
        (Roll((3, 4), 4), PASS, "siete fuera"),
        (Roll((6, 5), 4), PASS, "Sale"),
    ],
)
def test_outcome_line_describe_la_tirada(roll: Roll, bet: Bet, expected: str) -> None:
    game = CrapsGame.new(100, bet)
    game.rolls.append(roll)
    assert expected in outcome_line(game)


def test_outcome_line_destaca_el_punto_hecho_por_las_malas() -> None:
    game = CrapsGame.new(100, PASS)
    game.rolls.append(Roll((3, 3), 6))
    assert "Por las malas" in outcome_line(game)
    game.rolls.append(Roll((1, 5), 6))
    assert "Por las malas" not in outcome_line(game)


async def test_las_manos_calientes_se_celebran_en_el_texto(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX, SIX_MADE)
    view, _ = await open_table(cog)
    view.hand = Hand(points=[4])  # este será el segundo punto
    await press(view, "_pass")
    await press(view, "_roll")
    cheer = milestone(2)
    assert cheer and cheer in view.description()


async def test_el_pie_cuenta_la_mano_y_el_saldo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX, NOTHING)
    view, _ = await open_table(cog)
    await press(view, "_pass")
    assert "Mano: 1 tirada, 0 puntos" in view.footer()
    await press(view, "_roll")
    assert "Mano: 2 tiradas, 0 puntos" in view.footer()
    assert f"Saldo {format_amount(await balance(cog))}" in view.footer()


# -- Caducar y apagar -------------------------------------------------------------------


async def test_al_caducar_con_el_punto_puesto_el_bot_tira_solo_hasta_decidir_y_paga(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, SIX, NOTHING, SIX_MADE)
    view, _ = await open_table(cog, amount="100")
    await press(view, "_pass")
    await view.on_timeout()
    game = played(view)
    assert game.status is Status.WON and len(game.rolls) == 3
    assert view not in cog.views
    assert all(b.disabled for b in view.children if isinstance(b, ui.Button))
    assert await balance(cog) == STARTING_BALANCE - 100 + 200 - settle_tax(100)
    assert [r for r, _ in ledger(tmp_path)].count("dados:premio") == 1


async def test_al_caducar_con_el_punto_puesto_la_imagen_enseña_la_tirada_que_decidio(
    tmp_path: Path,
) -> None:
    """Si no, el PNG se quedaría con el disco ON y los dados de antes de caducar."""
    cog = await make_cog(tmp_path, SIX, NOTHING, SIX_MADE)
    view, _ = await open_table(cog, amount="100")
    await press(view, "_pass")
    cog.renderer.board.reset_mock()
    await view.on_timeout()
    (table, rest), _ = cog.renderer.board.await_args
    assert table.game is played(view) and table.game.status is Status.WON
    assert (rest[0].value, rest[1].value) == SIX_MADE
    assert (rest[0].x, rest[0].y) == (OPENING_REST[0].x, OPENING_REST[0].y)


async def test_caducar_tras_una_partida_terminada_no_repinta_la_imagen(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, NATURAL)
    view, _ = await open_table(cog, amount="100")
    await press(view, "_pass")
    cog.renderer.board.reset_mock()
    await view.on_timeout()
    cog.renderer.board.assert_not_awaited()


async def test_al_caducar_el_bot_puede_perder_la_partida_por_el_jugador(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX, NATURAL)
    view, _ = await open_table(cog, amount="100")
    await press(view, "_pass")
    await view.on_timeout()
    assert played(view).status is Status.LOST
    assert await balance(cog) == STARTING_BALANCE - 100


async def test_caducar_sin_partida_no_hace_nada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog)
    await view.on_timeout()
    assert view.game is None and ledger(tmp_path) == []
    assert all(b.disabled for b in view.children if isinstance(b, ui.Button))


async def test_force_settle_no_paga_dos_veces(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX, SIX_MADE)
    view, _ = await open_table(cog)
    await press(view, "_pass")
    await view.force_settle()
    await view.force_settle()
    assert [r for r, _ in ledger(tmp_path)].count("dados:premio") == 1


async def test_al_apagar_el_bot_se_deciden_las_partidas_y_se_cierra_el_navegador(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, SIX, SIX_MADE)
    view, _ = await open_table(cog, amount="100")
    await press(view, "_pass")
    await cog.cog_unload()
    assert played(view).status is Status.WON
    assert await balance(cog) == STARTING_BALANCE - 100 + 200 - settle_tax(100)
    assert not cog.views
    cog.renderer.close.assert_awaited_once()


async def test_al_apagar_con_odds_puestas_se_pagan_tambien(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX, SIX_MADE)
    view, _ = await open_table(cog, amount="100")
    await press(view, "_pass")
    await press(view, "_odds_max")
    await cog.cog_unload()
    game = played(view)
    assert game.status is Status.WON and game.odds == ODDS_MAX * 100
    assert ("dados:premio", game.payout) in ledger(tmp_path)


# -- Comandos ---------------------------------------------------------------------------


async def test_dados_500_pase_tira_directamente(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX)
    view, send = await open_table(cog, amount="500", bet=PASS)
    assert await balance(cog) == STARTING_BALANCE - 500
    assert played(view).point == 6
    cog.renderer.throw.assert_awaited_once()
    message = send.return_value
    # La mesa se abrió con los botones apagados y luego se editó con el GIF y el PNG.
    assert [
        call.kwargs["attachments"][0].filename
        for call in message.edit.await_args_list
        if "attachments" in call.kwargs
    ] == ["dados.gif", "dados.png"]


async def test_dados_con_apuesta_sin_saldo_para_ella_avisa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX)
    send, errors = AsyncMock(), AsyncMock()
    await cog._dados_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(),
        amount_text=str(STARTING_BALANCE + 1),
        bet=PASS,
        send=send,
        send_error=errors,
    )
    errors.assert_awaited_once()
    send.assert_not_awaited()
    assert await balance(cog) == STARTING_BALANCE


def text_context() -> MagicMock:
    ctx = MagicMock()
    ctx.guild = MagicMock(id=GUILD_ID)
    ctx.channel = None
    ctx.author = make_user()
    ctx.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    return ctx


@pytest.mark.parametrize(
    ("args", "stake", "bet"),
    [
        (("500", "pase"), 500, PASS),
        (("pase", "500"), 500, PASS),
        (("no", "pase", "500"), 500, DONT),
        (("500", "no", "pase"), 500, DONT),
        (("500", "nopase"), 500, DONT),
        (("nopase",), craps_cog.DEFAULT_STAKE, DONT),
        (("pase",), craps_cog.DEFAULT_STAKE, PASS),
        (("No", "Pase"), craps_cog.DEFAULT_STAKE, DONT),
    ],
)
async def test_el_comando_de_texto_entiende_cantidad_y_apuesta_en_cualquier_orden(
    tmp_path: Path, args: tuple[str, ...], stake: int, bet: Bet
) -> None:
    cog = await make_cog(tmp_path / "_".join(args), SIX)
    ctx = text_context()
    await cog.dados_text.callback(cog, ctx, *args)
    (view,) = cog.views
    assert view.stake == stake
    assert played(view).bet is bet and played(view).status is Status.POINT
    assert await balance(cog) == STARTING_BALANCE - stake


async def test_el_comando_de_texto_solo_con_cantidad_abre_la_mesa_sin_tirar(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path)
    ctx = text_context()
    await cog.dados_text.callback(cog, ctx, "500")
    (view,) = cog.views
    assert view.stake == 500 and view.game is None
    assert await balance(cog) == STARTING_BALANCE


async def test_el_comando_de_texto_rechaza_lo_que_no_entiende(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    ctx = text_context()
    ctx.send = AsyncMock()
    await cog.dados_text.callback(cog, ctx, "100", "rosa")
    assert "No entiendo «rosa»" in ctx.send.await_args.args[0]
    assert not cog.views


async def test_el_comando_de_barra_abre_la_mesa_y_tira_con_apuesta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX)
    interaction = make_interaction()
    interaction.guild = MagicMock(id=GUILD_ID)
    interaction.channel = None
    interaction.original_response = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    await cog.dados.callback(cog, interaction, "200", "nopase")
    interaction.response.send_message.assert_awaited_once()
    assert await balance(cog) == STARTING_BALANCE - 200
    (view,) = cog.views
    assert played(view).bet is DONT


async def test_el_comando_de_barra_sin_apuesta_solo_abre_la_mesa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    interaction = make_interaction()
    interaction.guild = MagicMock(id=GUILD_ID)
    interaction.channel = None
    interaction.original_response = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    await cog.dados.callback(cog, interaction, "200", None)
    (view,) = cog.views
    assert view.game is None and await balance(cog) == STARTING_BALANCE


# -- Anuncio en el canal ----------------------------------------------------------------


def channel_mock() -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    return channel


def made_game() -> CrapsGame:
    game = CrapsGame.new(100, PASS)
    game.roll(SIX)
    game.roll(SIX_MADE)
    assert game.last is not None and game.last.made
    return game


async def test_el_anuncio_sale_con_los_puntos_de_la_mano_y_no_antes(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    channel, user = channel_mock(), make_user()
    for points in range(SHOUT_POINTS):
        await cog.shout(made_game(), Hand(points=[6] * points), user, channel)
    channel.send.assert_not_awaited()

    await cog.shout(made_game(), Hand(points=[6] * SHOUT_POINTS), user, channel)
    channel.send.assert_awaited_once()
    text = channel.send.await_args.args[0]
    assert user.mention in text and f"**{SHOUT_POINTS} puntos**" in text


async def test_el_anuncio_no_sale_si_la_ultima_tirada_no_hizo_punto(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    channel = channel_mock()
    game = CrapsGame.new(100, PASS)
    game.roll(NATURAL)
    await cog.shout(game, Hand(points=[6] * (SHOUT_POINTS + 2)), make_user(), channel)
    channel.send.assert_not_awaited()
    await cog.shout(CrapsGame.new(100, PASS), Hand(), make_user(), channel)
    channel.send.assert_not_awaited()


async def test_el_anuncio_sin_canal_no_falla(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    await cog.shout(made_game(), Hand(points=[6] * SHOUT_POINTS), make_user(), None)


async def test_el_quinto_punto_de_una_mano_se_anuncia_de_verdad(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX, SIX_MADE)
    channel = channel_mock()
    view, _ = await open_table(cog, channel=channel)
    view.hand = Hand(points=[4] * (SHOUT_POINTS - 1))  # esta mano ya lleva cuatro
    await press(view, "_pass")
    channel.send.assert_not_awaited()
    await press(view, "_roll")
    channel.send.assert_awaited_once()
    assert f"**{SHOUT_POINTS} puntos**" in channel.send.await_args.args[0]


async def test_el_cuarto_punto_de_una_mano_no_se_anuncia(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, SIX, SIX_MADE)
    channel = channel_mock()
    view, _ = await open_table(cog, channel=channel)
    view.hand = Hand(points=[4] * (SHOUT_POINTS - 2))
    await press(view, "_pass")
    await press(view, "_roll")
    channel.send.assert_not_awaited()


# -- Logros y porras --------------------------------------------------------------------


async def test_apuestas_record_recibe_made_sevenout_rolls_y_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = AsyncMock()
    monkeypatch.setattr(craps_cog.apuestas, "record", record)
    monkeypatch.setattr(craps_cog.logros, "casino_play", AsyncMock())
    cog = await make_cog(tmp_path, SIX, SIX_MADE)
    view, _ = await open_table(cog, amount="100")
    await press(view, "_pass")
    record.assert_not_awaited()  # la partida sigue: aún no hay nada que apuntar
    await press(view, "_roll")

    kwargs = record.await_args.kwargs
    assert kwargs["game"] == "dados"
    game = played(view)
    assert kwargs["stake"] == game.wagered and kwargs["net"] == game.net
    assert kwargs["details"] == (
        ("made", 1),
        ("sevenout", 0),
        ("rolls", 2),
        ("started", int(view.started_at)),
    )
    assert view.started_at > 0


async def test_apuestas_record_marca_el_siete_fuera(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = AsyncMock()
    monkeypatch.setattr(craps_cog.apuestas, "record", record)
    monkeypatch.setattr(craps_cog.logros, "casino_play", AsyncMock())
    cog = await make_cog(tmp_path, SIX, NATURAL)
    view, _ = await open_table(cog)
    await press(view, "_pass")
    await press(view, "_roll")
    details = dict(record.await_args.kwargs["details"])
    assert details["made"] == 0 and details["sevenout"] == 1 and details["rolls"] == 2


async def test_al_terminar_se_apuntan_los_logros_y_la_jugada_despues_de_enseñar_el_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    casino_play = AsyncMock(side_effect=lambda *a, **k: events.append("logros"))
    record = AsyncMock(side_effect=lambda *a, **k: events.append("apuestas"))
    monkeypatch.setattr(craps_cog.logros, "casino_play", casino_play)
    monkeypatch.setattr(craps_cog.apuestas, "record", record)
    cog = await make_cog(tmp_path, SNAKE_EYES)
    view, _ = await open_table(cog, amount="100")
    interaction = make_interaction(events=events)
    await view._pass(interaction)

    delta = casino_play.await_args.args[4]
    assert delta.add["dice_games"] == 1
    assert casino_play.await_args.kwargs["net"] == -100
    # Primero se contesta y se enseña el PNG final; los logros y las porras, después.
    assert events == [
        "response.defer",
        "edit_original_response",
        "edit_original_response",
        "edit_original_response",
        "logros",
        "apuestas",
    ]


async def test_una_salida_que_pone_el_punto_todavia_no_apunta_logros(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    casino_play = AsyncMock()
    monkeypatch.setattr(craps_cog.logros, "casino_play", casino_play)
    monkeypatch.setattr(craps_cog.apuestas, "record", AsyncMock())
    cog = await make_cog(tmp_path, SIX)
    view, _ = await open_table(cog)
    await press(view, "_pass")
    casino_play.assert_not_awaited()


# -- GIF por adelantado ----------------------------------------------------------------


class RecordingRenderer:
    """Dibujo falso que apunta cada GIF pedido: las tiradas de la mano y la apuesta."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[tuple[int, int]], Bet, int, int]] = []
        self.gate: asyncio.Event | None = None

    async def throw(self, table: Table, *, seed: int) -> Media:
        if self.gate is not None:
            await self.gate.wait()
        game = table.game
        assert game is not None
        dice = [roll.dice for roll, _bet in table.history]
        self.calls.append((dice, game.bet, game.odds, seed))
        gif = f"GIF{len(self.calls)}".encode()
        return Media(gif=gif, png=b"PNG", seconds=0.0, rest=OPENING_REST)

    async def board(self, table: Table, rest: object) -> bytes:
        return b"PNG"

    async def close(self) -> None:
        return None


async def ahead_cog(tmp_path: Path, *rolls: tuple[int, int]) -> tuple[Craps, RecordingRenderer]:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    renderer = RecordingRenderer()
    cog = Craps(
        MagicMock(),
        economy=EconomyService(repository),
        rng=Scripted(*rolls, NOTHING, NOTHING, NOTHING),
        renderer=renderer,  # type: ignore[arg-type]
        ahead=True,
    )
    return cog, renderer


async def settle_painting(view: CrapsView) -> None:
    """Espera a que la mesa acabe de pintar por adelantado."""
    while view._painting is not None and not view._painting.done():
        await asyncio.sleep(0)


def gif_bytes(interaction: MagicMock) -> bytes:
    """El GIF que se subió con la interacción (la edición con un `.gif`)."""
    for call in interaction.edit_original_response.await_args_list:
        files = call.kwargs.get("attachments") or []
        if files and files[0].filename == "dados.gif":
            return files[0].fp.read()
    raise AssertionError("no se subió ningún GIF")


async def test_al_abrir_la_mesa_se_pintan_ya_pase_y_no_pase(tmp_path: Path) -> None:
    cog, renderer = await ahead_cog(tmp_path, SIX)
    view, _ = await open_table(cog)
    await settle_painting(view)
    # Antes de pulsar: la misma salida (el 6) con Pase y con No pase, sin cobrar nada.
    assert [(call[0], call[1]) for call in renderer.calls] == [
        ([SIX], PASS),
        ([SIX], Bet.DONT),
    ]
    assert await balance(cog) == STARTING_BALANCE

    interaction = await press(view, "_dont")
    # Sube el GIF pintado con No pase, sin dibujar otro ni pasar por «apagar».
    assert gif_bytes(interaction) == b"GIF2"
    assert interaction.edit_original_response.await_count == 2
    assert played(view).bet is Bet.DONT and played(view).point == 6


async def test_con_el_punto_se_pinta_la_tirada_siguiente_y_es_la_que_sale(tmp_path: Path) -> None:
    cog, renderer = await ahead_cog(tmp_path, SIX, SIX_MADE)
    view, _ = await open_table(cog)
    await press(view, "_pass")
    await settle_painting(view)
    painted = renderer.calls[-1]
    # Con el punto puesto solo hay un botón que tira: un solo GIF, el del 6 y luego el 4-2.
    assert painted[0] == [SIX, SIX_MADE]
    calls = len(renderer.calls)
    interaction = await press(view, "_roll")
    assert gif_bytes(interaction) == f"GIF{calls}".encode()
    assert played(view).status is Status.WON
    assert [roll.dice for roll in played(view).rolls] == painted[0]


async def test_poner_odds_vuelve_a_pintar_con_las_fichas_nuevas(tmp_path: Path) -> None:
    cog, renderer = await ahead_cog(tmp_path, SIX, SIX_MADE)
    view, _ = await open_table(cog)
    await press(view, "_pass")
    await settle_painting(view)
    assert renderer.calls[-1][2] == 0
    await press(view, "_odds_one")
    await settle_painting(view)
    # El GIF de la próxima tirada ya lleva las Odds, y es el que sale al tirar.
    assert renderer.calls[-1][2] == 100
    calls = len(renderer.calls)
    interaction = await press(view, "_roll")
    assert gif_bytes(interaction) == f"GIF{calls}".encode()


async def test_si_no_ha_acabado_de_pintarse_la_mesa_se_apaga_y_se_espera(tmp_path: Path) -> None:
    cog, renderer = await ahead_cog(tmp_path, NATURAL)
    renderer.gate = asyncio.Event()
    view, _ = await open_table(cog)
    interaction = make_interaction()
    task = asyncio.create_task(view._pass(interaction))
    while not interaction.edit_original_response.await_count:
        await asyncio.sleep(0)
    assert "attachments" not in interaction.edit_original_response.await_args.kwargs
    # Apagados con las etiquetas de antes de tirar: siguen siendo Pase y No pase.
    assert all(button.disabled for button in view.children if isinstance(button, ui.Button))
    assert labels(view)[0].startswith(PASS.emoji)
    renderer.gate.set()
    await task
    assert interaction.edit_original_response.await_count == 3
    assert gif_bytes(interaction) == b"GIF1"
