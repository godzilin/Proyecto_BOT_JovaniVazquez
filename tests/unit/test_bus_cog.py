"""Pruebas de bot.cogs.bus: la mesa del Autobús con botones, precarga y dinero.

Se usa la economía real sobre un SQLite temporal y un dibujante falso (las
imágenes tienen sus propias pruebas) con el margen del GIF a cero. Las cartas se
fuerzan poniendo `view.deck` antes de jugar: la partida usa las ya sorteadas.
"""

from __future__ import annotations

import asyncio
import random
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import ui
from interaction_fakes import fake_interaction

from bot.cogs import bus as bus_cog
from bot.cogs.bus import COLOR_IDLE, Bus, BusView
from bot.repositories import sqlite as sqlite_module
from bot.repositories.economy import EconomyRepository
from bot.services.blackjack import Card
from bot.services.bus import Pick, Status
from bot.services.bus_render import Media, Reveal
from bot.services.economy import STARTING_BALANCE, EconomyService

GUILD_ID = 1
OWNER_ID = 10
# Palos de blackjack.SUITS: 0 ♠, 1 ♥, 2 ♦, 3 ♣.
SPADE, HEART, DIAMOND, CLUB = range(4)
#: Unas cartas para ir hasta el final: 7♥, 10♠, 9♣, 4♦ y A♣.
ROUTE = (Card(7, HEART), Card(10, SPADE), Card(9, CLUB), Card(4, DIAMOND), Card(1, CLUB))
WINNING_PICKS = (Pick.RED, Pick.HIGHER, Pick.INSIDE, Pick.DIAMONDS, Pick.TURN_BLACK)


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bus_cog, "REVEAL_MARGIN_SECONDS", 0)


def make_user(user_id: int = OWNER_ID) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = "Diego" if user_id == OWNER_ID else "Intruso"
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


def make_interaction(user_id: int = OWNER_ID) -> MagicMock:
    return fake_interaction(make_user(user_id))


def fake_renderer() -> MagicMock:
    renderer = MagicMock()
    renderer.reveal = AsyncMock(
        return_value=Reveal(
            win=Media(gif=b"WIN", png=b"winpng", seconds=0.0),
            lose=Media(gif=b"LOSE", png=b"losepng", seconds=0.0),
        )
    )
    renderer.board = AsyncMock(return_value=b"png")
    renderer.close = AsyncMock()
    return renderer


async def make_cog(tmp_path: Path) -> Bus:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    return Bus(
        MagicMock(),
        economy=EconomyService(repository),
        rng=random.Random(1),
        renderer=fake_renderer(),
    )


async def open_table(
    cog: Bus, *, amount: str = "100", cards: tuple[Card, ...] = ROUTE
) -> tuple[BusView, AsyncMock]:
    send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    errors = AsyncMock()
    await cog._guagua_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(),
        amount_text=amount,
        send=send,
        send_error=errors,
    )
    errors.assert_not_awaited()
    (view,) = cog.views
    view.deck = cards
    await asyncio.sleep(0)  # deja correr la precarga de la primera mano
    return view, send


def labels(view: BusView) -> list[str]:
    return [item.label or "" for item in view.children if isinstance(item, ui.Button)]


def button(view: BusView, prefix: str) -> ui.Button:
    return next(
        b for b in view.children if isinstance(b, ui.Button) and (b.label or "").startswith(prefix)
    )


async def balance(cog: Bus) -> int:
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


async def press(view: BusView, pick: Pick, *, user_id: int = OWNER_ID) -> MagicMock:
    interaction = make_interaction(user_id)
    await view._pick(interaction, pick)
    return interaction


# -- Abrir la mesa ----------------------------------------------------------------------


async def test_abrir_la_mesa_no_cobra_y_enseña_el_color_con_sus_probabilidades(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path)
    view, send = await open_table(cog, amount="250")
    assert await balance(cog) == STARTING_BALANCE
    assert ledger(tmp_path) == []
    assert view.game is None
    assert labels(view) == [
        "🔴 Rojo · ×1,98 · 50 %",
        "⚫ Negro · ×1,98 · 50 %",
        "½",
        "×2",
        "💰 All-in",
        "📋 Tabla",
    ]
    description = send.await_args.kwargs["embed"].description or ""
    assert "¿Subes al autobús?" in description
    assert "🔴 **Rojo** · 50 % · ×1,98 → 495 Y$" in description
    assert send.await_args.kwargs["embed"].color == COLOR_IDLE


async def test_al_abrir_ya_se_esta_dibujando_la_primera_mano(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    await open_table(cog)
    cog.renderer.reveal.assert_awaited_once()
    assert cog.renderer.reveal.await_args.args[1] == 1


async def test_sin_saldo_no_se_abre_la_mesa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    errors = AsyncMock()
    await cog._guagua_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(),
        amount_text="5000",
        send=AsyncMock(),
        send_error=errors,
    )
    errors.assert_awaited_once()
    assert "Necesitas 5.000 Y$" in errors.await_args.args[0]
    assert not cog.views


# -- Jugar ------------------------------------------------------------------------------


async def test_acertar_el_color_cobra_la_apuesta_y_ofrece_la_altura(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog)
    interaction = await press(view, Pick.RED)
    assert ledger(tmp_path) == [("autobus:apuesta", -100)]
    game = view.game
    assert game is not None and game.playing and game.wins == 1
    # Con un 7 delante: 7 alturas mayores, 5 menores y 1 igual.
    assert labels(view) == [
        "⬆️ Mayor · ×3,67 · 54 %",
        "⬇️ Menor · ×5,14 · 38 %",
        "🟰 Igual · ×25,74 · 7,7 %",
        "💰 Cobrar 198 Y$",
        "📋 Tabla",
    ]
    gif_call, png_call = interaction.edit_original_response.await_args_list
    assert gif_call.kwargs["attachments"][0].fp.read() == b"WIN"
    assert png_call.kwargs["attachments"][0].filename == "autobus.png"


async def test_el_gif_de_espera_no_destripa_y_luego_vuelven_los_botones(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog, cards=(Card(2, SPADE), *ROUTE[1:]))
    states: list[list[bool]] = []

    async def capture(**kwargs: object) -> None:
        states.append([b.disabled for b in view.children if isinstance(b, ui.Button)])

    interaction = make_interaction()
    interaction.edit_original_response.side_effect = capture
    await view._pick(interaction, Pick.RED)

    gif_call, png_call = interaction.edit_original_response.await_args_list
    waiting = gif_call.kwargs["embed"]
    assert waiting.color == COLOR_IDLE
    assert "Pides **🔴 Rojo**" in (waiting.description or "")
    for hint in ("2♠", "Ha salido", "-100"):
        assert hint not in (waiting.description or "")
    assert gif_call.kwargs["attachments"][0].fp.read() == b"LOSE"
    # Durante el GIF todo está apagado menos la tabla; después vuelven los botones.
    assert states[0][:-1] == [True] * (len(states[0]) - 1)
    assert not any(states[1])
    assert "Ha salido **2♠** y pediste **rojo**" in (png_call.kwargs["embed"].description or "")


async def test_fallar_pierde_la_apuesta_y_baraja_otra_partida(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog)
    await press(view, Pick.RED)
    await press(view, Pick.LOWER)  # 10 contra 7: menor falla
    assert view.game is not None and view.game.status is Status.LOST
    # Perder no apunta premio: solo queda la apuesta en el libro.
    assert ledger(tmp_path) == [("autobus:apuesta", -100)]
    assert await balance(cog) == STARTING_BALANCE - 100
    # Cartas nuevas para la siguiente, y su primera mano ya se está dibujando.
    assert view.deck != ROUTE
    assert cog.renderer.reveal.await_args.args[0] == view.deck
    assert labels(view)[:2] == ["🔴 Rojo · ×1,98 · 50 %", "⚫ Negro · ×1,98 · 50 %"]


async def test_tras_acertar_se_dibuja_la_siguiente_mano_antes_de_pulsarla(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog)
    await press(view, Pick.RED)
    hands = [call.args[1] for call in cog.renderer.reveal.await_args_list]
    assert 2 in hands
    assert all(call.args[0] == ROUTE for call in cog.renderer.reveal.await_args_list[1:])


async def test_mayor_es_imposible_con_un_as(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog, cards=(Card(1, HEART), *ROUTE[1:]))
    await press(view, Pick.RED)
    higher = button(view, "⬆️ Mayor")
    assert higher.disabled
    assert higher.label == "⬆️ Mayor · imposible"


async def test_cobrar_paga_y_dice_que_carta_venia(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog)
    await press(view, Pick.RED)
    interaction = make_interaction()
    await view._cash_out(interaction)
    assert view.game is not None and view.game.status is Status.CASHED
    assert await balance(cog) == STARTING_BALANCE - 100 + 198
    description = interaction.edit_original_response.await_args.kwargs["embed"].description
    assert "Cobras **198 Y$**" in description
    assert "La siguiente era **10♠**: con **mayor**" in description
    board = cog.renderer.board.await_args.args[0]
    assert board.ghost == Card(10, SPADE)
    assert board.banner is not None and board.banner.title == "¡COBRADO!"


async def test_el_autobus_entero_y_la_vuelta_se_cobran_solos(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog, amount="10")
    for pick in WINNING_PICKS[:4]:
        await press(view, pick)
    game = view.game
    assert game is not None and game.playing and game.completed
    assert labels(view)[:2] == ["🔴 Rojo · ×191,21 · 50 %", "⚫ Negro · ×191,21 · 50 %"]
    await press(view, Pick.TURN_BLACK)
    assert game.status is Status.CASHED and game.turned
    assert game.payout == 1_912
    # Un premio así ya paga la retención del juego, que se queda el Estado.
    tax = await cog.economy.state_balance(GUILD_ID)
    assert tax > 0
    assert await balance(cog) == STARTING_BALANCE - 10 + game.payout - tax


async def test_un_intruso_no_juega_pero_puede_ver_la_tabla(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog)
    intruder = make_interaction(user_id=99)
    intruder.data = {"custom_id": "autobus:rojo"}
    assert not await view.interaction_check(intruder)
    peek = make_interaction(user_id=99)
    peek.data = {"custom_id": "autobus:table"}
    assert await view.interaction_check(peek)
    await view._paytable(peek)
    embed = peek.response.send_message.await_args.kwargs["embed"]
    assert "Con un 7" in (embed.description or "")
    assert peek.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_al_caducar_cobra_la_partida_a_medias(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog)
    await press(view, Pick.RED)
    await view.on_timeout()
    assert view.game is not None and view.game.status is Status.CASHED
    assert await balance(cog) == STARTING_BALANCE - 100 + 198
    assert not cog.views


async def test_al_apagar_cobra_y_cierra_el_navegador(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog)
    await press(view, Pick.RED)
    await cog.cog_unload()
    assert view.game is not None and view.game.status is Status.CASHED
    cog.renderer.close.assert_awaited_once()


# -- Que el clic se note ----------------------------------------------------------------


async def test_si_la_mano_aun_se_dibuja_la_mesa_se_apaga_ya_y_sin_destripar(
    tmp_path: Path,
) -> None:
    """Con el GIF aún a medias, el clic se nota al momento y los botones no dicen nada.

    Antes la mesa seguía igual hasta tener el GIF (parecía que el botón no
    respondía) y luego reconstruía los botones, que ya decían si se había acertado.
    """
    cog = await make_cog(tmp_path)
    view, _ = await open_table(cog)
    await press(view, Pick.RED)
    release = asyncio.Event()
    ready = cog.renderer.reveal.return_value

    async def slow_reveal(*args: object, **kwargs: object) -> Reveal:
        await release.wait()
        return ready

    cog.renderer.reveal.side_effect = slow_reveal
    view.cancel_reveals()  # la segunda mano ya pintada se tira: se dibujará lenta
    before = labels(view)
    interaction = make_interaction()
    task = asyncio.create_task(view._pick(interaction, Pick.LOWER))

    async def first_edit() -> None:
        while not interaction.edit_original_response.await_count:
            await asyncio.sleep(0)

    # Sin tapar la espera no llega ninguna edición hasta tener el GIF.
    await asyncio.wait_for(first_edit(), 1)
    first = interaction.edit_original_response.await_args.kwargs
    assert "attachments" not in first
    assert first["embed"].color == COLOR_IDLE
    # Apagados y con las etiquetas de antes de jugar.
    assert labels(view) == before
    assert all(b.disabled for b in view.children if isinstance(b, ui.Button))
    release.set()
    await task
    assert interaction.edit_original_response.await_count == 3
