"""Pruebas de bot.cogs.tower: la torre con botones y el movimiento de dinero.

Se usa la economía real sobre un SQLite temporal y dragones colocados a mano.
"""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import ui
from interaction_fakes import fake_interaction
from render_fakes import FakeTowerScene

from bot.cogs.tower import (
    BOOM,
    BURN_LINES,
    CASH_LINES,
    COLOR_BURNED,
    COLOR_PLAYING,
    DRAGON,
    EGG,
    TOP_LINES,
    Tower,
    TowerBoard,
)
from bot.repositories.economy import STATE_ACCOUNT_ID, EconomyRepository
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.tower import (
    FLOORS,
    MILESTONES,
    Difficulty,
    Status,
    TowerGame,
    multiplier_cents,
    payout,
)

GUILD_ID = 1
OWNER_ID = 10


def make_user(user_id: int = OWNER_ID) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = "Diego"
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


def make_interaction(user_id: int = OWNER_ID) -> MagicMock:
    return fake_interaction(make_user(user_id))


async def make_cog(tmp_path: Path) -> Tower:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    # Sin Node ni navegador: la torre de texto, instantánea (las de imagen, más abajo).
    return Tower(
        MagicMock(),
        economy=EconomyService(repository),
        rng=random.Random(7),
        renderer=FakeTowerScene(images=False),  # type: ignore[arg-type]
    )


async def open_board(
    cog: Tower, *, amount: str = "100", difficulty: Difficulty | None = Difficulty.EASY
) -> TowerBoard:
    send = AsyncMock(return_value=MagicMock())
    errors = AsyncMock()
    await cog._torre_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(),
        amount_text=amount,
        difficulty=difficulty,
        send=send,
        send_error=errors,
    )
    errors.assert_not_awaited()
    (board,) = cog.boards
    return board


def place(board: TowerBoard, dragon_doors: set[int]) -> TowerGame:
    """Pone los mismos dragones en los nueve pisos (la prueba elige la partida)."""
    assert board.game is not None
    board.game.dragons = tuple(frozenset(dragon_doors) for _ in range(FLOORS))
    return board.game


def buttons(board: TowerBoard) -> list[ui.Button]:
    return [item for item in board.walk_children() if isinstance(item, ui.Button)]


def picks(board: TowerBoard) -> list[ui.Button]:
    return [b for b in buttons(board) if (b.custom_id or "").startswith("torre:pick:")]


def ledger_sum(tmp_path: Path, user_id: int = OWNER_ID) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            (GUILD_ID, user_id),
        ).fetchone()
    return int(total)


def state_balance(tmp_path: Path) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        row = connection.execute(
            "SELECT balance FROM economy_wallets WHERE guild_id = ? AND user_id = ?",
            (GUILD_ID, STATE_ACCOUNT_ID),
        ).fetchone()
    return int(row[0]) if row else 0


async def test_torre_cobra_y_dibuja_una_fila_de_puertas_mas_controles(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 100
    # Fácil: 4 puertas, con el custom_id fijo que busca el rescate de clics.
    assert [b.custom_id for b in picks(board)] == [f"torre:pick:{i}" for i in range(4)]
    assert board.total_children_count <= 40
    custom_ids = {b.custom_id for b in buttons(board)}
    assert {"torre:cashout", "torre:random"} <= custom_ids
    cash = next(b for b in buttons(board) if b.custom_id == "torre:cashout")
    assert cash.disabled  # hasta subir el primer piso no hay nada que cobrar
    # Antes de subir el primer piso, el menú de dificultad sigue a mano.
    (select,) = [i for i in board.walk_children() if isinstance(i, ui.Select)]
    assert select.custom_id == "torre:nivel" and len(select.options) == 5


async def test_las_puertas_cambian_con_la_dificultad(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    for difficulty in Difficulty:
        board = await open_board(cog, difficulty=difficulty)
        assert len(picks(board)) == difficulty.width
        cog.boards.clear()


async def test_la_dificultad_por_defecto_es_facil_y_se_recuerda(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog, difficulty=None)
    assert board.difficulty is Difficulty.EASY
    cog.boards.clear()
    board = await open_board(cog, difficulty=Difficulty.HARD)
    cog.boards.clear()
    board = await open_board(cog, difficulty=None)
    assert board.difficulty is Difficulty.HARD


async def test_huevo_sube_sin_ack_y_cobrar_paga(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    place(board, {0})
    climb = make_interaction()
    await board._climb(climb, 1)
    # Subir de piso solo cambia memoria: se contesta con la edición, sin ack.
    climb.response.defer.assert_not_awaited()
    climb.response.edit_message.assert_awaited_once()
    await board._climb(make_interaction(), 2)
    assert board.game is not None and board.game.floor == 2
    interaction = make_interaction()
    await board._cash_out(interaction)
    paid = payout(100, Difficulty.EASY, 2)
    assert board.game.status is Status.CASHED
    balance = await cog.economy.balance(GUILD_ID, OWNER_ID)
    tax = state_balance(tmp_path)
    assert balance == STARTING_BALANCE - 100 + paid - tax
    assert ledger_sum(tmp_path) == balance
    interaction.response.defer.assert_awaited_once()
    interaction.edit_original_response.assert_awaited_once()
    # Tras cobrar, la fila de controles vuelve a ser la de jugar otra.
    assert any((b.label or "").startswith("🔁 Jugar") for b in buttons(board))
    assert all(b.disabled for b in picks(board))


async def test_pisar_un_dragon_lo_pierde_todo_y_ensena_la_torre_entera(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    place(board, {0})
    await board._climb(make_interaction(), 1)
    interaction = make_interaction()
    await board._climb(interaction, 0)
    interaction.response.defer.assert_awaited_once()  # pagar va a la base de datos: ack primero
    assert board.game is not None and board.game.status is Status.BURNED
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 100
    assert ledger_sum(tmp_path) == STARTING_BALANCE - 100
    text = board.header()
    tower = "\n".join(ln for ln in text.splitlines() if "`" in ln)
    # Los nueve pisos con su dragón a la vista; el que quemó, como 💥.
    assert tower.count(DRAGON) + tower.count(BOOM) == FLOORS
    assert tower.count(BOOM) == 1
    assert tower.count(EGG) == 1
    assert any(f"{line} -100" in text for line in BURN_LINES)
    assert all(b.disabled for b in picks(board))


async def test_la_torre_se_dibuja_de_arriba_abajo_con_el_multiplicador_de_cada_piso(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog, difficulty=Difficulty.HARD)
    place(board, {0})
    await board._climb(make_interaction(), 1)
    lines = [ln for ln in board.header().splitlines() if "`" in ln]
    assert len(lines) == FLOORS
    assert "`9`" in lines[0] and "`1`" in lines[-1]
    assert lines[0].startswith("▫️") and "⬛⬛" in lines[0]
    assert lines[-1].count(EGG) == 1  # el huevo elegido en el piso 1
    assert lines[-2].startswith("👉") and "`2`" in lines[-2]  # el piso actual
    # Cada piso enseña su multiplicador (el del piso 1 en Difícil, ×1,98).
    assert "×1,98" in lines[-1]
    for floor, line in enumerate(reversed(lines), start=1):
        assert multiplier_cents(Difficulty.HARD, floor) // 100 >= 1
        assert f"`{floor}`" in line


async def test_cobrar_sin_subir_no_hace_nada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    interaction = make_interaction()
    await board._cash_out(interaction)
    interaction.response.defer.assert_awaited_once()
    assert board.game is not None and board.game.playing


async def test_coronar_la_torre_cobra_solo_y_sin_tope(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog, amount="100", difficulty=Difficulty.HARD)
    place(board, {0})
    for _ in range(FLOORS):
        await board._climb(make_interaction(), 1)
    assert board.game is not None and board.game.status is Status.CASHED
    assert board.game.crowned
    assert board.game.payout == payout(100, Difficulty.HARD, FLOORS) == 50_688
    # Cobrado de verdad, menos la retención del juego.
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) > STARTING_BALANCE
    text = board.header()
    assert any(line in text for line in TOP_LINES)


async def test_un_cobro_normal_usa_las_frases_de_cobro(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    place(board, {0})
    await board._climb(make_interaction(), 1)
    await board._cash_out(make_interaction())
    text = board.header()
    assert any(line in text for line in CASH_LINES)


async def test_al_azar_elige_una_puerta_del_piso(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog, difficulty=Difficulty.NORMAL)
    await board._random(make_interaction())
    assert board.game is not None
    assert board.game.random_picks == 1


async def test_otro_miembro_no_puede_jugar_tu_torre(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    interaction = make_interaction(user_id=99)
    assert not await board.interaction_check(interaction)
    interaction.response.send_message.assert_awaited_once()


async def test_doble_clic_mientras_se_procesa_se_ignora(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    place(board, {0})
    async with board._lock:
        interaction = make_interaction()
        await board._climb(interaction, 1)
    interaction.response.defer.assert_awaited_once()
    assert board.game is not None and board.game.floor == 0


async def test_jugar_otra_vuelve_a_cobrar_y_la_dificultad_se_cambia(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    place(board, {0})
    await board._climb(make_interaction(), 1)
    await board._climb(make_interaction(), 0)  # dragón
    await board._choose_level(make_interaction(), "experto")
    assert board.difficulty is Difficulty.EXPERT
    assert cog.difficulty_for(GUILD_ID, OWNER_ID) is Difficulty.EXPERT
    assert board.total_children_count <= 40
    await board._double(make_interaction())
    assert board.stake == 200
    await board._again(make_interaction())
    assert board.game is not None and board.game.playing
    assert board.game.difficulty is Difficulty.EXPERT
    assert len(picks(board)) == 3
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 300


async def test_cambiar_apuesta_en_plena_partida_se_ignora(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    interaction = make_interaction()
    await board._double(interaction)
    interaction.response.defer.assert_awaited_once()
    assert board.stake == 100


async def test_jugar_otra_sin_saldo_avisa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog, amount="all")
    place(board, {0})
    await board._climb(make_interaction(), 1)
    await board._climb(make_interaction(), 0)
    interaction = make_interaction()
    await board._again(interaction)
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True


async def test_apagar_con_partida_a_medias_cobra_o_devuelve(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    await cog.cog_unload()
    # Sin subir ningún piso: se devuelve la apuesta.
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE
    assert board.game is None

    board = await open_board(cog)
    place(board, {0})
    await board._climb(make_interaction(), 1)
    await cog.cog_unload()
    assert board.game is not None and board.game.status is Status.CASHED
    balance = await cog.economy.balance(GUILD_ID, OWNER_ID)
    assert ledger_sum(tmp_path) == balance


async def test_si_discord_no_deja_ensenar_la_torre_se_devuelve_la_apuesta(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path)
    send = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=404), "Unknown"))
    with pytest.raises(discord.HTTPException):
        await cog._torre_impl(
            guild=MagicMock(id=GUILD_ID),
            channel=None,
            user=make_user(),
            amount_text="100",
            difficulty=Difficulty.EASY,
            send=send,
            send_error=AsyncMock(),
        )
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE
    assert not cog.boards


async def test_torre_caducada_cobra_y_apaga_los_botones(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    board.message = MagicMock(edit=AsyncMock())
    place(board, {0})
    await board._climb(make_interaction(), 1)
    await board.on_timeout()
    assert board.game is not None and board.game.status is Status.CASHED
    assert all(b.disabled for b in buttons(board))


async def test_torre_fuera_del_casino_se_rechaza(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    cog.casino_channel_ids = frozenset({1234})
    errors = AsyncMock()
    await cog._torre_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=MagicMock(id=555),
        user=make_user(),
        amount_text="100",
        difficulty=None,
        send=AsyncMock(),
        send_error=errors,
    )
    errors.assert_awaited_once()
    assert not cog.boards


async def test_torre_sin_saldo_o_con_apuesta_rara_se_rechaza(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    errors = AsyncMock()
    await cog._torre_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(),
        amount_text="tonteria",
        difficulty=None,
        send=AsyncMock(),
        send_error=errors,
    )
    errors.assert_awaited_once()
    assert not cog.boards


async def test_cobro_enorme_se_anuncia(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    game = TowerGame(
        stake=100,
        difficulty=Difficulty.HARD,
        dragons=tuple(frozenset({0}) for _ in range(FLOORS)),
    )
    for _ in range(5):
        game.climb(1)
    game.cash_out()  # ×31,68
    await cog.shout(game, make_user(), channel)
    channel.send.assert_awaited_once()
    channel.send.reset_mock()
    small = TowerGame(
        stake=100, difficulty=Difficulty.EASY, dragons=tuple(frozenset({0}) for _ in range(FLOORS))
    )
    small.climb(1)
    small.cash_out()
    await cog.shout(small, make_user(), channel)
    channel.send.assert_not_awaited()


async def test_el_texto_cuenta_pisos_y_lo_que_suma_el_siguiente(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog, difficulty=Difficulty.NORMAL)
    assert "Elige una puerta" in board.header()
    place(board, {0})
    for door in (1, 2, 1):
        await board._climb(make_interaction(), door)
    text = board.header()
    assert f"🏰 **3/{FLOORS}**" in text
    assert any(line in text for line in MILESTONES[3])
    extra = payout(100, Difficulty.NORMAL, 4) - payout(100, Difficulty.NORMAL, 3)
    assert f"(+{extra} Y$)" in text
    assert "67 % de que sea 🥚" in text  # dos de cada tres puertas son huevo


async def test_batir_el_record_se_avisa(tmp_path: Path) -> None:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    load = AsyncMock(return_value=2)
    cog = Tower(
        MagicMock(),
        economy=EconomyService(repository),
        rng=random.Random(7),
        load_record=load,
        renderer=FakeTowerScene(images=False),  # type: ignore[arg-type]
    )
    board = await open_board(cog)
    place(board, {0})
    for door in (1, 2):
        await board._climb(make_interaction(), door)
    assert "Récord personal" not in board.header()
    await board._climb(make_interaction(), 3)
    assert "🏅 ¡Récord personal!" in board.header()
    await board._cash_out(make_interaction())
    assert await cog.record(GUILD_ID, OWNER_ID) == 3
    load.assert_awaited_once()  # el récord se lee una vez y luego va en memoria


async def test_el_menu_de_dificultad_sale_al_abrir_y_rehace_la_partida_sin_cobrar_otra_vez(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    assert len([i for i in board.walk_children() if isinstance(i, ui.Select)]) == 1
    assert board.total_children_count <= 40
    await board._choose_level(make_interaction(), "maestro")
    assert board.game is not None and board.game.playing
    assert board.game.difficulty is Difficulty.MASTER and board.game.stake == 100
    assert all(len(floor) == 3 for floor in board.game.dragons)
    assert len(picks(board)) == 4
    assert cog.difficulty_for(GUILD_ID, OWNER_ID) is Difficulty.MASTER
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 100


async def test_tras_subir_el_primer_piso_el_menu_desaparece_y_no_cambia_nada(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    place(board, {0})
    await board._climb(make_interaction(), 1)
    assert not [i for i in board.walk_children() if isinstance(i, ui.Select)]
    interaction = make_interaction()
    await board._choose_level(interaction, "maestro")
    interaction.response.defer.assert_awaited_once()
    assert board.game is not None and board.game.difficulty is Difficulty.EASY


async def test_la_partida_terminada_no_deja_el_menu_ni_las_puertas_activas(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path)
    board = await open_board(cog)
    place(board, {0})
    await board._climb(make_interaction(), 0)
    assert board.game is not None and board.game.status is Status.BURNED
    assert [i for i in board.walk_children() if isinstance(i, ui.Select)]
    assert all(b.disabled for b in picks(board))


# -- Con imagen ---------------------------------------------------------------------------


async def image_cog(tmp_path: Path, *, ahead: bool = False) -> Tower:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    return Tower(
        MagicMock(),
        economy=EconomyService(repository),
        rng=random.Random(7),
        renderer=FakeTowerScene(),  # type: ignore[arg-type]
        ahead=ahead,
    )


def uploads(interaction: MagicMock) -> list[str]:
    """Nombre del archivo que sube cada edición (o `-` si no sube nada)."""
    names = []
    for call in interaction.edit_original_response.await_args_list:
        files = call.kwargs.get("attachments")
        names.append(files[0].filename if files else "-")
    return names


@pytest.fixture(autouse=True)
def no_reveal_margin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bot.cogs.tower.REVEAL_MARGIN_SECONDS", 0)


async def test_con_imagen_la_torre_sale_con_su_png_y_sin_la_torre_de_texto(
    tmp_path: Path,
) -> None:
    cog = await image_cog(tmp_path)
    send = AsyncMock(return_value=MagicMock())
    await cog._torre_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(),
        amount_text="100",
        difficulty=Difficulty.EASY,
        send=send,
        send_error=AsyncMock(),
    )
    (board,) = cog.boards
    assert send.await_args.kwargs["file"].filename == "torre.png"
    assert board.image == "torre.png"
    # Embed como los demás juegos: con `MediaGallery`, Discord de PC repetía el GIF
    # viejo al cambiar al PNG y se mezclaban fotogramas.
    assert send.await_args.kwargs["embed"].image.url == "attachment://torre.png"
    assert not isinstance(board, ui.LayoutView)
    # La imagen ya enseña la torre: el texto no la repite.
    assert not [ln for ln in board.header().splitlines() if "`" in ln]


async def test_subir_un_piso_ensena_el_gif_y_despues_el_png_sin_delatar_nada(
    tmp_path: Path,
) -> None:
    cog = await image_cog(tmp_path)
    board = await open_board(cog)
    place(board, {0})
    seen: list[str] = []
    interaction = make_interaction()
    original = interaction.edit_original_response

    async def spy(**kwargs: object) -> object:
        seen.append(board.header())
        return await original(**kwargs)

    interaction.edit_original_response = AsyncMock(side_effect=spy)
    await board._climb(interaction, 1)
    interaction.response.defer.assert_awaited_once()
    # Sin pintar por adelantado: primero apaga los botones, luego el GIF y el PNG.
    assert uploads(interaction) == ["-", "torre.gif", "torre.png"]
    # Mientras: la puerta elegida, sin saldo ni multiplicador nuevo.
    for waiting in seen[:2]:
        assert "Puerta 2" in waiting and "Saldo" not in waiting and "×" not in waiting
    assert "Saldo" in seen[2]
    assert board.game is not None and board.game.floor == 1
    assert not board._busy and board.image == "torre.png"


async def test_el_embed_del_gif_no_delata_el_dragon_y_el_png_si(tmp_path: Path) -> None:
    cog = await image_cog(tmp_path)
    board = await open_board(cog)
    place(board, {0})
    interaction = make_interaction()
    seen: list[tuple[str | None, object]] = []

    async def spy(**kwargs: object) -> None:
        embed = kwargs["embed"]
        assert isinstance(embed, discord.Embed)
        seen.append((embed.image.url, embed.color))

    interaction.edit_original_response = AsyncMock(side_effect=spy)
    await board._climb(interaction, 0)  # dragón
    # Mientras se ve el GIF, el color de antes de jugar; el rojo, con el PNG.
    assert seen == [
        ("attachment://torre.png", COLOR_PLAYING),
        ("attachment://torre.gif", COLOR_PLAYING),
        ("attachment://torre.png", COLOR_BURNED),
    ]


async def test_quemarse_cobra_y_acaba_con_el_png_de_la_torre_destapada(tmp_path: Path) -> None:
    cog = await image_cog(tmp_path)
    board = await open_board(cog)
    place(board, {0})
    interaction = make_interaction()
    await board._climb(interaction, 0)
    assert board.game is not None and board.game.status is Status.BURNED
    assert uploads(interaction) == ["-", "torre.gif", "torre.png"]
    assert any((b.label or "").startswith("🔁 Jugar") for b in buttons(board))
    assert ledger_sum(tmp_path) == STARTING_BALANCE - 100


async def test_cobrar_con_imagen_paga_y_ensena_su_gif(tmp_path: Path) -> None:
    cog = await image_cog(tmp_path)
    board = await open_board(cog)
    place(board, {0})
    await board._climb(make_interaction(), 1)
    interaction = make_interaction()
    await board._cash_out(interaction)
    assert board.game is not None and board.game.status is Status.CASHED
    assert uploads(interaction) == ["-", "torre.gif", "torre.png"]
    assert ("cashout",) in cog.renderer.calls  # type: ignore[attr-defined]


async def test_cada_boton_sale_pintado_antes_del_clic(tmp_path: Path) -> None:
    cog = await image_cog(tmp_path, ahead=True)
    board = await open_board(cog)
    place(board, {0})
    assert board._painting is not None
    await board._painting
    renderer: FakeTowerScene = cog.renderer  # type: ignore[assignment]
    assert board._dice is not None
    # Las cuatro puertas, 🎲 con su puerta ya sorteada y el primer piso de las
    # otras cuatro dificultades del menú.
    picks_painted = [c for c in renderer.calls if c[0] == "pick"]
    assert picks_painted == [("pick", d, False) for d in range(4)] + [
        ("pick", board._dice[2], True)
    ]
    assert sum(c[0] == "opening" for c in renderer.calls) == 4
    painted = len(renderer.calls)
    click = make_interaction()
    await board._climb(click, 1)
    # Pintado de antes: el GIF sale en la primera edición, sin espera.
    assert uploads(click) == ["torre.gif", "torre.png"]
    assert board._painting is not None
    await board._painting
    # El clic no pinta (usa el GIF hecho); lo nuevo es ya el piso siguiente.
    later = renderer.calls[painted:]
    assert later[:4] == [("pick", d, False) for d in range(4)]
    assert ("cashout",) in later


async def test_al_azar_usa_la_puerta_sorteada_al_pintar(tmp_path: Path) -> None:
    cog = await image_cog(tmp_path, ahead=True)
    board = await open_board(cog, difficulty=Difficulty.NORMAL)
    assert board._painting is not None
    await board._painting
    assert board._dice is not None and board.game is not None
    door = board._dice[2]
    board.game.dragons = tuple(frozenset({(door + 1) % 3}) for _ in range(FLOORS))
    await board._random(make_interaction())
    assert board.game.picks == [door] and board.game.random_picks == 1


async def test_cambiar_la_apuesta_al_acabar_no_vuelve_a_subir_la_imagen(tmp_path: Path) -> None:
    cog = await image_cog(tmp_path)
    board = await open_board(cog)
    place(board, {0})
    await board._climb(make_interaction(), 0)  # dragón
    interaction = make_interaction()
    await board._halve(interaction)
    assert uploads(interaction) == ["-"]
    assert board.image == "torre.png"
    again = make_interaction()
    await board._again(again)
    assert uploads(again) == ["torre.png"]  # la partida nueva sí trae su primer piso


async def test_el_menu_antes_del_primer_piso_cambia_la_imagen(tmp_path: Path) -> None:
    cog = await image_cog(tmp_path)
    board = await open_board(cog)
    interaction = make_interaction()
    await board._choose_level(interaction, "maestro")
    assert board.game is not None and board.game.difficulty is Difficulty.MASTER
    assert uploads(interaction) == ["torre.png"]
    assert ("opening", Difficulty.MASTER, 100) in cog.renderer.calls  # type: ignore[attr-defined]


async def test_sin_pintor_a_mitad_de_partida_vuelve_la_torre_de_texto(tmp_path: Path) -> None:
    cog = await image_cog(tmp_path)
    board = await open_board(cog)
    place(board, {0})
    cog.renderer.images = False  # type: ignore[attr-defined]
    interaction = make_interaction()
    await board._climb(interaction, 1)
    assert board.image is None
    assert [ln for ln in board.header().splitlines() if "`" in ln]
    assert interaction.edit_original_response.await_args_list[-1].kwargs["attachments"] == []


async def test_torre_con_imagen_caducada_cobra_y_ensena_la_torre_destapada(
    tmp_path: Path,
) -> None:
    cog = await image_cog(tmp_path)
    board = await open_board(cog)
    board.message = MagicMock(edit=AsyncMock())
    place(board, {0})
    await board._climb(make_interaction(), 1)
    board._last_interaction = None
    await board.on_timeout()
    assert board.game is not None and board.game.status is Status.CASHED
    (call,) = board.message.edit.await_args_list
    assert call.kwargs["attachments"][0].filename == "torre.png"
