"""Pruebas de bot.cogs.blackjack: mesa con botones y movimiento de dinero.

Se usa la economía real sobre un SQLite temporal, un zapato preparado (las
cartas salen en el orden indicado) y un renderizador falso.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

import bot.cogs.blackjack as blackjack_module
from bot.cogs.blackjack import PNG_NAME, Blackjack, BlackjackTable
from bot.repositories.economy import EconomyRepository
from bot.services.blackjack import MAX_STAKE, Action, Card
from bot.services.economy import STARTING_BALANCE, EconomyService

GUILD_ID = 1
OWNER_ID = 10
CASINO_CHANNEL = 555


def c(rank: int, suit: int = 0) -> Card:
    return Card(rank, suit)


def stacked(*cards: Card) -> Callable[[], list[Card]]:
    """Zapato que reparte `cards` en ese orden (jugador, banca, jugador, banca…).

    Se rellena por debajo con sietes para que nunca se acabe.
    """

    def factory() -> list[Card]:
        return [c(7)] * 20 + list(reversed(cards))

    return factory


class FakeRenderer:
    def render(self, **kwargs) -> bytes:  # noqa: ANN003
        return b"PNG"


@pytest.fixture(autouse=True)
def no_dealer_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(blackjack_module, "DEALER_STEP_SECONDS", 0)


async def make_cog(tmp_path: Path, *cards: Card, channels=frozenset()) -> Blackjack:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    return Blackjack(
        MagicMock(),
        economy=EconomyService(repository),
        renderer=FakeRenderer(),  # type: ignore[arg-type]
        shoe_factory=stacked(*cards),
        casino_channel_ids=channels,
    )


def make_user(user_id: int = OWNER_ID) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = "Diego"
    return user


def make_interaction(user_id: int = OWNER_ID) -> MagicMock:
    return fake_interaction(make_user(user_id))


async def open_table(cog: Blackjack, *, amount: str | None = "100", channel_id: int = 1):  # noqa: ANN201
    message = MagicMock()
    message.edit = AsyncMock()
    send = AsyncMock(return_value=message)
    send_error = AsyncMock()
    await cog._bj_impl(
        guild=SimpleNamespace(id=GUILD_ID),  # type: ignore[arg-type]
        channel=SimpleNamespace(id=channel_id),
        user=make_user(),
        amount_text=amount,
        send=send,
        send_error=send_error,
    )
    table = send.await_args.kwargs["view"] if send.await_args else None
    return table, send, send_error, message


async def balance(cog: Blackjack) -> int:
    return await cog.economy.balance(GUILD_ID, OWNER_ID)


async def test_bj_reparte_al_momento_y_cobra_la_apuesta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(9), c(6), c(8))

    table, send, send_error, _ = await open_table(cog)

    send_error.assert_not_awaited()
    assert [f.filename for f in send.await_args.kwargs["files"]] == [PNG_NAME]
    assert isinstance(table, BlackjackTable)
    assert table.game.player_turn
    assert table in cog.tables
    assert await balance(cog) == STARTING_BALANCE - 100


async def test_blackjack_al_repartir_paga_3_a_2(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(1), c(9), c(13), c(8))

    table, *_ = await open_table(cog)

    assert table.game.settled
    assert await balance(cog) == STARTING_BALANCE + 150
    assert not table.deal_button.disabled


async def test_pedir_y_pasarse_pierde_la_apuesta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(9), c(6), c(8), c(10))
    table, *_ = await open_table(cog)
    interaction = make_interaction()

    await table.act(interaction, Action.HIT)

    assert table.game.settled
    assert table.game.hands[0].busted
    assert await balance(cog) == STARTING_BALANCE - 100
    # El clic se acepta antes de pagar; luego, banca destapando y resultado.
    interaction.response.defer.assert_awaited_once()
    assert interaction.edit_original_response.await_count >= 2


async def test_plantarse_y_la_banca_roba_hasta_pasarse(tmp_path: Path) -> None:
    # Jugador 10+8 = 18; banca 10+6 = 16, roba un 10 y se pasa.
    cog = await make_cog(tmp_path, c(10), c(10), c(8), c(6), c(10))
    table, *_ = await open_table(cog)

    await table.act(make_interaction(), Action.STAND)

    assert len(table.game.dealer) == 3
    assert await balance(cog) == STARTING_BALANCE + 100
    assert table.streak == 1


async def test_doblar_cobra_el_doble_y_da_una_carta(tmp_path: Path) -> None:
    # Jugador 6+5 = 11, dobla y recibe un 10 (21); banca 10+8 = 18.
    cog = await make_cog(tmp_path, c(6), c(10), c(5), c(8), c(10))
    table, *_ = await open_table(cog)

    await table.act(make_interaction(), Action.DOUBLE)

    assert table.game.hands[0].doubled
    assert await balance(cog) == STARTING_BALANCE + 200


async def test_doblar_sin_saldo_avisa_y_no_cambia_la_mano(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(6), c(10), c(5), c(8), c(10))
    table, *_ = await open_table(cog, amount="all")
    interaction = make_interaction()

    await table.act(interaction, Action.DOUBLE)

    assert "necesitas" in interaction.followup.send.await_args.args[0]

    assert len(table.game.hands[0].cards) == 2
    assert await balance(cog) == 0


async def test_separar_cobra_otra_apuesta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(8), c(10), c(8, 1), c(9), c(3), c(2))
    table, *_ = await open_table(cog)

    await table.act(make_interaction(), Action.SPLIT)

    assert len(table.game.hands) == 2
    assert await balance(cog) == STARTING_BALANCE - 200


async def test_accion_ilegal_se_ignora(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(9), c(6), c(8))
    table, *_ = await open_table(cog)
    interaction = make_interaction()

    await table.act(interaction, Action.SPLIT)

    interaction.response.defer.assert_awaited_once()
    assert await balance(cog) == STARTING_BALANCE - 100


async def test_otro_miembro_no_puede_jugar_tu_mano(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(9), c(6), c(8))
    table, *_ = await open_table(cog)
    intruder = make_interaction(user_id=99)

    assert not await table.interaction_check(intruder)
    assert intruder.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_repartir_otra_mano_vuelve_a_cobrar(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(9), c(6), c(8), c(10))
    table, *_ = await open_table(cog)
    await table.act(make_interaction(), Action.HIT)  # se pasa: -100

    await table._deal_again(make_interaction())

    assert table.game.player_turn
    assert await balance(cog) == STARTING_BALANCE - 200


async def test_repartir_con_la_mano_en_juego_se_ignora(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(9), c(6), c(8))
    table, *_ = await open_table(cog)
    interaction = make_interaction()

    await table._deal_again(interaction)

    interaction.response.defer.assert_awaited_once()
    assert await balance(cog) == STARTING_BALANCE - 100


async def test_mesa_caducada_planta_y_paga_la_mano(tmp_path: Path) -> None:
    # Jugador 20 contra banca 10+7 = 17: al caducar se planta y gana.
    cog = await make_cog(tmp_path, c(10), c(10), c(10), c(7))
    table, _, _, message = await open_table(cog)

    await table.on_timeout()

    assert table.game.settled
    assert await balance(cog) == STARTING_BALANCE + 100
    assert table not in cog.tables
    message.edit.assert_awaited()


async def test_apagar_el_bot_paga_las_manos_abiertas(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(10), c(10), c(7))
    table, *_ = await open_table(cog)

    await cog.cog_unload()

    assert table.game.settled
    assert await balance(cog) == STARTING_BALANCE + 100
    assert not cog.tables


async def test_bj_fuera_del_casino_se_rechaza(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, channels=frozenset({CASINO_CHANNEL}))

    table, send, send_error, _ = await open_table(cog, channel_id=2)

    send.assert_not_awaited()
    assert f"<#{CASINO_CHANNEL}>" in send_error.await_args.args[0]
    assert await balance(cog) == STARTING_BALANCE


async def test_bj_con_mas_de_lo_que_tienes_avisa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)

    _, send, send_error, _ = await open_table(cog, amount="5k")

    send.assert_not_awaited()
    assert "No te llega" in send_error.await_args.args[0]


async def test_all_in_tras_la_mano_pone_todo_el_saldo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(9), c(6), c(8), c(10))
    table, *_ = await open_table(cog)
    await table.act(make_interaction(), Action.HIT)

    await table._all_in(make_interaction())

    assert table.stake == STARTING_BALANCE - 100


async def test_la_banca_con_blackjack_se_lleva_la_apuesta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(13), c(9), c(1))

    table, *_ = await open_table(cog)

    assert table.game.settled
    assert table.game.net == -100
    assert await balance(cog) == STARTING_BALANCE - 100


async def test_con_un_as_de_la_banca_salen_los_botones_del_seguro(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(1), c(9), c(13))

    table, *_ = await open_table(cog)

    assert table.game.insurance_pending
    assert all(b in table.children for b in table.insurance_buttons.values())
    assert all(b.disabled for b in table.action_buttons.values())
    assert table.deal_button.disabled


async def test_seguro_cobra_media_apuesta_y_paga_2_a_1(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(1), c(9), c(13))
    table, *_ = await open_table(cog)
    interaction = make_interaction()

    await table.insure(interaction, take=True)

    assert table.game.settled
    assert table.game.insurance_paid
    assert await balance(cog) == STARTING_BALANCE
    interaction.response.defer.assert_awaited_once()
    assert all(b not in table.children for b in table.insurance_buttons.values())


async def test_sin_seguro_y_sin_blackjack_de_la_banca_se_juega(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(1), c(9), c(7))
    table, *_ = await open_table(cog)

    await table.insure(make_interaction(), take=False)

    assert table.game.player_turn
    assert not table.action_buttons[Action.HIT].disabled
    assert await balance(cog) == STARTING_BALANCE - 100


async def test_la_apuesta_inicial_no_pasa_del_tope_de_la_mesa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(9), c(6), c(8))
    await cog.economy.grant(GUILD_ID, OWNER_ID, amount=MAX_STAKE * 3, reason="prueba")
    start = await balance(cog)

    table, _, send_error, _ = await open_table(cog, amount="all")

    send_error.assert_not_awaited()
    assert table.game.stake == MAX_STAKE
    assert await balance(cog) == start - MAX_STAKE


async def test_all_in_y_doblar_ficha_se_quedan_en_el_tope(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, c(10), c(9), c(6), c(8), c(10))
    await cog.economy.grant(GUILD_ID, OWNER_ID, amount=MAX_STAKE * 3, reason="prueba")
    table, *_ = await open_table(cog)
    await table.act(make_interaction(), Action.HIT)

    await table._all_in(make_interaction())
    assert table.stake == MAX_STAKE
    await table._double_stake(make_interaction())
    assert table.stake == MAX_STAKE
