"""Pruebas de bot.cogs.coin: la mesa de Cara o cruz con botones, animación y dinero.

Se usa la economía real sobre un SQLite temporal, un dibujante falso (las imágenes
tienen sus propias pruebas), el margen del GIF a cero y el azar de guion de
`coin_fakes`: el primer resultado es el que saldrá al primer lanzamiento y cada
lanzamiento gasta uno más para sortear el siguiente.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from coin_fakes import Scripted
from discord import ui
from interaction_fakes import fake_interaction

from bot.cogs import coin as coin_cog
from bot.cogs.coin import COLOR_IDLE, Coin, CoinView
from bot.repositories import sqlite as sqlite_module
from bot.repositories.economy import EconomyRepository
from bot.services.coin import MAX_FLIPS, Outcome, Side, Status
from bot.services.coin_render import Media
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.taxes import gambling_day_tax

CARA, CRUZ, EDGE = Outcome.CARA, Outcome.CRUZ, Outcome.EDGE
GUILD_ID = 1
OWNER_ID = 10


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(coin_cog, "REVEAL_MARGIN_SECONDS", 0)


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
    renderer.toss = AsyncMock(return_value=Media(gif=b"GIF89a", png=b"png", seconds=0.0))
    renderer.board = AsyncMock(return_value=b"png")
    renderer.close = AsyncMock()
    return renderer


async def make_cog(tmp_path: Path, *results: Outcome) -> Coin:
    """Cog con economía real y azar de guion (`results` más un último sorteo de relleno)."""
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    return Coin(
        MagicMock(),
        economy=EconomyService(repository),
        rng=Scripted(*results, CARA),
        renderer=fake_renderer(),
    )


async def open_table(
    cog: Coin, *, amount: str = "100", pick: Side | None = None
) -> tuple[CoinView, AsyncMock]:
    send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    errors = AsyncMock()
    await cog._moneda_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(),
        amount_text=amount,
        pick=pick,
        send=send,
        send_error=errors,
    )
    errors.assert_not_awaited()
    send.assert_awaited_once()
    assert send.await_args.kwargs["file"].filename == "moneda.png"
    (view,) = cog.views
    return view, send


def labels(view: CoinView) -> list[str]:
    return [item.label or "" for item in view.children if isinstance(item, ui.Button)]


def button(view: CoinView, prefix: str) -> ui.Button:
    return next(
        b for b in view.children if isinstance(b, ui.Button) and (b.label or "").startswith(prefix)
    )


async def balance(cog: Coin) -> int:
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
    return [(row[0], row[1]) for row in rows if row[0] != "bienvenida"]


async def press(view: CoinView, side: Side, *, user_id: int = OWNER_ID) -> MagicMock:
    interaction = make_interaction(user_id)
    await (view._flip_cara if side is Side.CARA else view._flip_cruz)(interaction)
    return interaction


async def cash(view: CoinView) -> MagicMock:
    interaction = make_interaction()
    await view._cash_out(interaction)
    return interaction


# -- Abrir la mesa ----------------------------------------------------------------------


async def test_abrir_la_mesa_no_cobra_y_enseña_los_botones_de_lado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, CARA)
    view, send = await open_table(cog, amount="250")
    assert await balance(cog) == STARTING_BALANCE
    assert ledger(tmp_path) == []
    assert view.game is None
    cog.renderer.board.assert_awaited_once()
    cog.renderer.toss.assert_not_awaited()
    assert labels(view) == ["👑 Cara · 250 Y$", "✈️ Cruz · 250 Y$", "½", "×2", "💰 All-in"]
    embed = send.await_args.kwargs["embed"]
    assert "¿Cara o cruz?" in (embed.description or "")
    assert embed.color == COLOR_IDLE
    assert not any(b.disabled for b in view.children if isinstance(b, ui.Button))


async def test_sin_apuesta_se_juega_la_de_por_defecto_y_sin_saldo_no_abre_mesa(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path)
    send, errors = AsyncMock(), AsyncMock()
    await cog._moneda_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(),
        amount_text=None,
        pick=None,
        send=send,
        send_error=errors,
    )
    (view,) = cog.views
    assert view.stake == 100

    await cog._moneda_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(),
        amount_text="5000",
        pick=None,
        send=send,
        send_error=errors,
    )
    errors.assert_awaited_once()
    assert "Necesitas 5.000 Y$" in errors.await_args.args[0]
    assert len(cog.views) == 1


async def test_la_moneda_solo_se_juega_en_un_servidor(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    errors = AsyncMock()
    await cog._moneda_impl(
        guild=None,
        channel=None,
        user=make_user(),
        amount_text="100",
        pick=None,
        send=AsyncMock(),
        send_error=errors,
    )
    errors.assert_awaited_once()
    assert "servidor" in errors.await_args.args[0]
    assert not cog.views


# -- Lanzar ----------------------------------------------------------------------------


async def test_pulsar_cara_cobra_la_apuesta_y_lanza(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, CARA)
    view, _ = await open_table(cog, amount="100")
    await press(view, Side.CARA)
    assert await balance(cog) == STARTING_BALANCE - 100
    assert ledger(tmp_path) == [("moneda:apuesta", -100)]
    cog.renderer.toss.assert_awaited_once()
    game = view.game
    assert game is not None and game.wins == 1 and game.playing
    assert game.last is not None and game.last.pick is Side.CARA
    # Con una racha en marcha, los botones ofrecen doblar otra vez o cobrar lo que hay.
    assert labels(view) == ["👑 Cara · ×4", "✈️ Cruz · ×4", "💰 Cobrar 200 Y$"]


async def test_el_lanzamiento_enseña_el_gif_neutro_y_luego_el_png_sin_destripar(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, CRUZ)
    view, _ = await open_table(cog)
    states: list[list[bool]] = []

    async def capture(**kwargs: object) -> None:
        states.append([b.disabled for b in view.children if isinstance(b, ui.Button)])

    interaction = make_interaction()
    interaction.edit_original_response.side_effect = capture
    await view._flip_cara(interaction)

    wait_call, gif_call, png_call = interaction.edit_original_response.await_args_list
    # Antes del GIF, la mesa apaga los botones y enseña la jugada sin adjuntos nuevos.
    assert "attachments" not in wait_call.kwargs
    assert wait_call.kwargs["embed"].description == gif_call.kwargs["embed"].description
    assert gif_call.kwargs["attachments"][0].filename == "moneda.gif"
    assert png_call.kwargs["attachments"][0].filename == "moneda.png"
    waiting = gif_call.kwargs["embed"]
    # Mientras vuela la moneda, ni el texto ni el color dicen cómo cae.
    assert waiting.color == COLOR_IDLE
    assert "Pides" in (waiting.description or "")
    for hint in ("Cruz", "Fallaste", "DE CANTO", "-100"):
        assert hint not in (waiting.description or "")
    # Mientras se pinta y durante el GIF todo está apagado; después, vuelven los botones.
    assert all(states[0]) and all(states[1]) and not any(states[2])
    final = png_call.kwargs["embed"]
    assert "Ha salido **✈️ Cruz** y pediste cara" in (final.description or "")
    # El GIF se pidió con la cara de reposo de la que parte la moneda.
    assert cog.renderer.toss.await_args.kwargs["start"] is Side.CARA


async def test_tras_un_acierto_la_mesa_se_apaga_antes_de_pintar_y_sin_destripar(
    tmp_path: Path,
) -> None:
    """Con un dibujo lento, el clic se nota ya: los botones se apagan mientras se pinta.

    Antes, tras un acierto, la mesa seguía igual durante todo el dibujo y parecía
    que el botón no respondía (y un segundo clic se perdía en silencio).
    """
    cog = await make_cog(tmp_path, CARA, CRUZ)
    view, _ = await open_table(cog)
    await press(view, Side.CARA)
    painting = asyncio.Event()
    release = asyncio.Event()
    toss = cog.renderer.toss.return_value

    async def slow_toss(*args: object, **kwargs: object) -> Media:
        painting.set()
        await release.wait()
        return toss

    cog.renderer.toss.side_effect = slow_toss
    interaction = make_interaction()
    task = asyncio.create_task(view._flip_cara(interaction))
    await painting.wait()
    await asyncio.sleep(0)
    # El dibujo sigue en marcha y la mesa ya está apagada.
    first = interaction.edit_original_response.await_args_list[0].kwargs
    assert all(b.disabled for b in view.children if isinstance(b, ui.Button))
    # Las etiquetas son las de antes de lanzar: ni ×8 ni la apuesta, que delatarían el resultado.
    assert labels(first["view"])[:2] == ["👑 Cara · ×4", "✈️ Cruz · ×4"]
    assert "Fallaste" not in (first["embed"].description or "")
    release.set()
    await task
    assert interaction.edit_original_response.await_count == 3


async def test_la_moneda_parte_de_la_cara_que_quedo_arriba(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, CRUZ, CARA, CARA)
    view, _ = await open_table(cog)
    await press(view, Side.CRUZ)  # acierta: cruz arriba
    assert view.face is CRUZ
    await press(view, Side.CRUZ)
    assert cog.renderer.toss.await_args.kwargs["start"] is Side.CRUZ


# -- Cobrar y el libro -----------------------------------------------------------------


async def test_acertar_dos_veces_y_cobrar_paga_la_apuesta_por_cuatro_y_el_libro_cuadra(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, CARA, CRUZ, CARA)
    view, _ = await open_table(cog, amount="500")
    await press(view, Side.CARA)
    await press(view, Side.CRUZ)
    interaction = await cash(view)

    game = view.game
    assert game is not None and game.status is Status.CASHED
    assert game.payout == 2_000 and game.net == 1_500
    tax = gambling_day_tax(game.net, 0)
    assert tax > 0
    final = await balance(cog)
    assert final == STARTING_BALANCE - 500 + 2_000 - tax
    movements = ledger(tmp_path)
    assert movements[:2] == [("moneda:apuesta", -500), ("moneda:premio", 2_000)]
    # Cada yapdollar que se mueve está en el libro: saldo = inicial + suma de movimientos.
    assert STARTING_BALANCE + sum(delta for _reason, delta in movements) == final
    assert await cog.economy.state_balance(GUILD_ID) == tax
    # El mensaje final enseña el cobro y la siguiente que habría salido.
    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    assert "2.000 Y$" in (embed.description or "")
    assert "La siguiente habría salido **👑 Cara**" in (embed.description or "")
    # Al acabar vuelven la apuesta y los botones de reparto.
    assert labels(view)[0] == "👑 Cara · 500 Y$"


async def test_cobrar_con_una_sola_respuesta_a_discord_y_el_png_final(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, CARA, CARA)
    view, _ = await open_table(cog)
    await press(view, Side.CARA)
    interaction = await cash(view)
    interaction.response.defer.assert_awaited_once()
    interaction.response.edit_message.assert_not_awaited()
    interaction.response.send_message.assert_not_awaited()
    cog.renderer.board.assert_awaited()
    assert interaction.edit_original_response.await_args.kwargs["attachments"][0].filename == (
        "moneda.png"
    )


async def test_cobrar_sin_partida_o_sin_aciertos_no_hace_nada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog)
    interaction = await cash(view)  # ni siquiera hay botón: se ignora el clic
    interaction.response.defer.assert_awaited_once()
    assert view.game is None
    assert await balance(cog) == STARTING_BALANCE


# -- Perder ------------------------------------------------------------------------------


async def test_fallar_paga_cero_y_el_dinero_no_va_al_estado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, CRUZ)
    view, _ = await open_table(cog, amount="300")
    interaction = await press(view, Side.CARA)
    game = view.game
    assert game is not None and game.status is Status.LOST
    assert game.payout == 0 and game.net == -300
    assert await balance(cog) == STARTING_BALANCE - 300
    assert ledger(tmp_path) == [("moneda:apuesta", -300)]
    assert await cog.economy.state_balance(GUILD_ID) == 0
    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    assert "-300 Y$" in (embed.description or "")
    assert labels(view)[0] == "👑 Cara · 300 Y$"  # se puede volver a jugar


async def test_el_canto_paga_cero_aunque_se_pidiera_bien(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, EDGE)
    view, _ = await open_table(cog, amount="200")
    interaction = await press(view, Side.CRUZ)
    game = view.game
    assert game is not None and game.status is Status.EDGE
    assert game.payout == 0 and game.net == -200
    assert await balance(cog) == STARTING_BALANCE - 200
    assert await cog.economy.state_balance(GUILD_ID) == 0
    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    assert "DE CANTO" in (embed.description or "")


async def test_el_canto_tras_una_racha_pierde_solo_la_apuesta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, CARA, EDGE, CARA)
    view, _ = await open_table(cog, amount="100")
    await press(view, Side.CARA)
    interaction = await press(view, Side.CARA)
    game = view.game
    assert game is not None and game.status is Status.EDGE and game.wins == 1
    assert await balance(cog) == STARTING_BALANCE - 100
    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    assert "Llevabas ×2" in (embed.description or "")


async def test_tras_perder_se_puede_volver_a_jugar_con_otra_apuesta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, CRUZ, CARA, CARA, CARA)
    view, _ = await open_table(cog, amount="100")
    await press(view, Side.CARA)  # pierde
    await press(view, Side.CARA)  # empieza otra: cobra otros 100 y acierta
    assert await balance(cog) == STARTING_BALANCE - 200
    assert [reason for reason, _ in ledger(tmp_path)] == ["moneda:apuesta", "moneda:apuesta"]
    assert view.game is not None and view.game.wins == 1


# -- Los botones ------------------------------------------------------------------------


async def test_cada_boton_contesta_una_sola_vez_y_antes_de_editar(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, CARA, CARA)
    view, _ = await open_table(cog)
    events: list[str] = []
    flip = make_interaction(events=events)
    await view._flip_cara(flip)
    flip.response.defer.assert_awaited_once()  # cualquier segunda respuesta lanzaría error
    flip.response.edit_message.assert_not_awaited()
    assert events[0] == "response.defer"
    assert events.count("edit_original_response") == 3  # jugada, GIF y PNG
    events.clear()
    out = make_interaction(events=events)
    await view._cash_out(out)
    assert events[0] == "response.defer"
    assert events.count("response.defer") == 1


async def test_pulsar_dos_veces_seguidas_no_lanza_dos_monedas(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, CARA, CARA)
    view, _ = await open_table(cog)
    view._busy = True  # el lanzamiento anterior sigue en el aire
    interaction = await press(view, Side.CARA)
    interaction.response.defer.assert_awaited_once()
    assert view.game is None
    assert await balance(cog) == STARTING_BALANCE
    cog.renderer.toss.assert_not_awaited()


async def test_sin_saldo_para_la_apuesta_avisa_y_no_empieza(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, CARA)
    view, _ = await open_table(cog, amount="900")
    await cog.economy.place_bet(GUILD_ID, OWNER_ID, game="otro", stake=500)
    interaction = await press(view, Side.CARA)
    assert view.game is None
    assert await balance(cog) == 500
    interaction.followup.send.assert_awaited_once()
    assert "Necesitas 900 Y$" in interaction.followup.send.await_args.args[0]
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True


async def test_la_apuesta_se_cambia_con_medio_doble_y_all_in(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog, amount="200")
    await view._halve(make_interaction())
    assert view.stake == 100 and labels(view)[0] == "👑 Cara · 100 Y$"
    await view._double(make_interaction())
    await view._double(make_interaction())
    assert view.stake == 400
    await view._all_in(make_interaction())
    assert view.stake == STARTING_BALANCE
    # Cambiar la apuesta no mueve dinero.
    assert await balance(cog) == STARTING_BALANCE
    assert ledger(tmp_path) == []


async def test_otro_usuario_no_puede_tocar_la_mesa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, CARA)
    view, _ = await open_table(cog)
    stranger = make_interaction(user_id=99)
    assert await view.interaction_check(stranger) is False
    stranger.response.send_message.assert_awaited_once()
    assert "Esta moneda es de Diego" in stranger.response.send_message.await_args.args[0]
    assert stranger.response.send_message.await_args.kwargs["ephemeral"] is True
    assert view.game is None and await balance(cog) == STARTING_BALANCE

    owner = make_interaction()
    assert await view.interaction_check(owner) is True
    owner.response.send_message.assert_not_awaited()


# -- Racha a medias y tope --------------------------------------------------------------


async def test_force_settle_cobra_la_racha_a_medias(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, CARA, CARA)
    view, _ = await open_table(cog, amount="100")
    await press(view, Side.CARA)
    await view.force_settle()
    game = view.game
    assert game is not None and game.status is Status.CASHED
    assert await balance(cog) == STARTING_BALANCE - 100 + 200 - gambling_day_tax(100, 0)
    assert ("moneda:premio", 200) in ledger(tmp_path)
    # Una segunda vez no cobra otra vez.
    await view.force_settle()
    assert [r for r, _ in ledger(tmp_path)].count("moneda:premio") == 1


async def test_force_settle_sin_partida_no_hace_nada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog)
    await view.force_settle()
    assert view.game is None and ledger(tmp_path) == []


async def test_al_caducar_la_mesa_cobra_la_racha_y_apaga_los_botones(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, CARA, CARA)
    view, _ = await open_table(cog, amount="100")
    await press(view, Side.CARA)
    await view.on_timeout()
    assert view.game is not None and view.game.status is Status.CASHED
    assert view not in cog.views
    assert all(b.disabled for b in view.children if isinstance(b, ui.Button))
    assert await balance(cog) > STARTING_BALANCE - 100


async def test_al_apagar_el_bot_se_cobran_las_rachas_y_se_cierra_el_navegador(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, CARA, CARA)
    view, _ = await open_table(cog, amount="100")
    await press(view, Side.CARA)
    await cog.cog_unload()
    assert view.game is not None and view.game.status is Status.CASHED
    assert not cog.views
    cog.renderer.close.assert_awaited_once()


async def test_a_los_diez_aciertos_cobra_sola_la_moneda_de_oro(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, *[CARA] * (MAX_FLIPS + 1))
    view, _ = await open_table(cog, amount="10")
    last = make_interaction()
    for _ in range(MAX_FLIPS - 1):
        await press(view, Side.CARA)
    assert view.game is not None and view.game.playing
    await view._flip_cara(last)
    game = view.game
    assert game.status is Status.CASHED and game.maxed
    assert game.payout == 10_240
    tax = gambling_day_tax(game.net, 0)
    assert await balance(cog) == STARTING_BALANCE - 10 + 10_240 - tax
    embed = last.edit_original_response.await_args.kwargs["embed"]
    assert "DIEZ SEGUIDAS" in (embed.description or "")
    # Ya no se puede lanzar más: es una partida nueva la que cobraría otra apuesta.
    assert labels(view)[0] == "👑 Cara · 10 Y$"


# -- Comandos ---------------------------------------------------------------------------


async def test_moneda_500_cara_lanza_directamente(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, CARA)
    view, send = await open_table(cog, amount="500", pick=Side.CARA)
    assert await balance(cog) == STARTING_BALANCE - 500
    assert view.game is not None and view.game.wins == 1
    cog.renderer.toss.assert_awaited_once()
    message = send.return_value
    # La mesa se abrió con los botones apagados y luego se editó con el GIF y el PNG.
    assert [
        call.kwargs["attachments"][0].filename
        for call in message.edit.await_args_list
        if "attachments" in call.kwargs
    ] == ["moneda.gif", "moneda.png"]


async def test_el_comando_de_texto_acepta_cantidad_y_lado_en_cualquier_orden(
    tmp_path: Path,
) -> None:
    for args in (("500", "cara"), ("cara", "500")):
        cog = await make_cog(tmp_path / args[0], CRUZ)
        ctx = MagicMock()
        ctx.guild = MagicMock(id=GUILD_ID)
        ctx.channel = None
        ctx.author = make_user()
        ctx.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
        await cog.moneda_text.callback(cog, ctx, *args)
        (view,) = cog.views
        assert view.stake == 500
        assert view.game is not None and view.game.last is not None
        assert view.game.last.pick is Side.CARA
        assert await balance(cog) == STARTING_BALANCE - 500


async def test_el_comando_de_texto_rechaza_lo_que_no_entiende(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    ctx = MagicMock()
    ctx.guild = MagicMock(id=GUILD_ID)
    ctx.channel = None
    ctx.author = make_user()
    ctx.send = AsyncMock()
    await cog.moneda_text.callback(cog, ctx, "100", "rosa")
    assert "No entiendo «rosa»" in ctx.send.await_args.args[0]
    assert not cog.views


async def test_el_comando_de_barra_abre_la_mesa_y_lanza_con_lado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, CARA)
    interaction = make_interaction()
    interaction.guild = MagicMock(id=GUILD_ID)
    interaction.channel = None
    interaction.original_response = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    await cog.moneda.callback(cog, interaction, "200", "cara")
    interaction.response.send_message.assert_awaited_once()
    assert await balance(cog) == STARTING_BALANCE - 200
    (view,) = cog.views
    assert view.game is not None and view.game.wins == 1


async def test_solo_se_juega_en_los_canales_del_casino(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    cog.casino_channel_ids = frozenset({555})
    errors = AsyncMock()
    await cog._moneda_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=MagicMock(spec=discord.TextChannel, id=1),
        user=make_user(),
        amount_text="100",
        pick=None,
        send=AsyncMock(),
        send_error=errors,
    )
    errors.assert_awaited_once()
    assert not cog.views


async def test_al_terminar_se_apuntan_los_logros_y_la_jugada_despues_de_enseñar_el_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    casino_play = AsyncMock(side_effect=lambda *a, **k: events.append("logros"))
    record = AsyncMock(side_effect=lambda *a, **k: events.append("apuestas"))
    monkeypatch.setattr(coin_cog.logros, "casino_play", casino_play)
    monkeypatch.setattr(coin_cog.apuestas, "record", record)
    cog = await make_cog(tmp_path, CRUZ)
    view, _ = await open_table(cog, amount="100")
    interaction = make_interaction(events=events)
    await view._flip_cara(interaction)

    delta = casino_play.await_args.args[4]
    assert delta.add["coin_games"] == 1 and delta.add["coin_losses"] == 1
    assert casino_play.await_args.kwargs["net"] == -100
    assert record.await_args.kwargs["game"] == "moneda"
    assert record.await_args.kwargs["stake"] == 100 and record.await_args.kwargs["net"] == -100
    # Primero se contesta y se enseña el PNG final; los logros y las porras, después.
    assert events == [
        "response.defer",
        "edit_original_response",
        "edit_original_response",
        "edit_original_response",
        "logros",
        "apuestas",
    ]
