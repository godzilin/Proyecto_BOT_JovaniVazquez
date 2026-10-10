"""Pruebas de bot.cogs.horses: la parrilla, los boletos, la carrera y el dinero.

Se usa la economía real y el establo real sobre un SQLite temporal, un reloj
falso, un renderizador falso y cuotas estimadas con pocas carreras (para ir
rápido). El resultado de cada carrera se fija a mano sustituyendo `run_race`.
El bucle no se arranca solo: cada prueba llama a `race()` o `run()`.
"""

from __future__ import annotations

import asyncio
import random
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

from bot.cogs import horses as cog_module
from bot.cogs.horses import Horses, Phase, Race
from bot.repositories.economy import STATE_ACCOUNT_ID, EconomyRepository
from bot.repositories.horses import HorseRepository
from bot.services import horses as h
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.horses import (
    GRAND_PRIX_POT,
    GRAND_PRIX_POT_STEP,
    SEGMENTS,
    BetKind,
    Pick,
    RaceResult,
)
from bot.services.horses_render import Media

GUILD_ID = 1
CHANNEL_ID = 555
ANA, LEO, EVA = 10, 11, 12
START = 1_800_000_000.0


class FakeClock:
    """Reloj que solo avanza cuando el bucle "duerme"."""

    def __init__(self) -> None:
        self.now = START

    def wall(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeRenderer:
    async def card(self, *args, **kwargs) -> bytes:  # noqa: ANN002, ANN003
        return b"PNG"

    async def race(self, *args, **kwargs) -> Media:  # noqa: ANN002, ANN003
        return Media(gif=b"GIF", png=b"PNG", seconds=1.0)

    async def ticket(self, **kwargs) -> bytes:  # noqa: ANN003
        return b"TICKET"

    async def close(self) -> None:
        pass


def make_user(user_id: int, name: str = "Ana") -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = name
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


def make_channel() -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = CHANNEL_ID
    message = MagicMock()
    message.id = 999
    message.edit = AsyncMock()
    channel.send = AsyncMock(return_value=message)
    channel.test_message = message
    return channel


def make_interaction(user_id: int, name: str = "Ana", message_id: int | None = 999) -> MagicMock:
    interaction = fake_interaction(make_user(user_id, name))
    interaction.guild = None
    interaction.message = MagicMock(id=message_id) if message_id is not None else None
    return interaction


def fixed_result(order: tuple[int, ...], gaps: tuple[float, ...] | None = None) -> RaceResult:
    size = len(order)
    gaps = gaps or tuple(float(i) for i in range(size))
    times = [0.0] * size
    for place, index in enumerate(order):
        times[index] = 100.0 + gaps[place]
    splits = tuple(tuple(t * s / SEGMENTS for s in range(SEGMENTS + 1)) for t in times)
    return RaceResult(order=order, times=tuple(times), splits=splits)


@pytest.fixture(autouse=True)
def quick_odds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cuotas con pocas carreras simuladas: lo que se prueba aquí no es su precisión."""
    real = h.estimate
    monkeypatch.setattr(cog_module, "estimate", lambda card, rng: real(card, rng, trials=2_000))


async def make_cog(tmp_path: Path) -> tuple[Horses, FakeClock]:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    horses = HorseRepository(tmp_path / "bot.db")
    await horses.initialize()
    clock = FakeClock()
    cog = Horses(
        MagicMock(),
        economy=EconomyService(repository),
        repository=horses,
        renderer=FakeRenderer(),  # type: ignore[arg-type]
        rng=random.Random(7),
        wall_clock=clock.wall,
        sleep=clock.sleep,
    )
    cog.start = MagicMock()  # type: ignore[method-assign]
    return cog, clock


async def caballo(
    cog: Horses,
    channel: MagicMock,
    *,
    user_id: int = ANA,
    name: str = "Ana",
    amount: str | None = None,
    pick: str | None = None,
    kind: str | None = None,
) -> AsyncMock:
    errors = AsyncMock()
    await cog._caballo_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=channel,
        user=make_user(user_id, name),
        amount_text=amount,
        pick_text=pick,
        kind_text=kind,
        confirm=AsyncMock(),
        send_error=errors,
    )
    return errors


def state_balance(tmp_path: Path) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        row = connection.execute(
            "SELECT balance FROM economy_wallets WHERE guild_id = ? AND user_id = ?",
            (GUILD_ID, STATE_ACCOUNT_ID),
        ).fetchone()
    return int(row[0]) if row else 0


def ledger_sum(tmp_path: Path, user_id: int) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            (GUILD_ID, user_id),
        ).fetchone()
    return int(total)


def force(monkeypatch: pytest.MonkeyPatch, result: RaceResult) -> None:
    monkeypatch.setattr(cog_module, "run_race", lambda card, rng: result)


# -- Abrir y apostar ------------------------------------------------------------------------


async def test_caballo_sin_nada_abre_la_parrilla(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    errors = await caballo(cog, channel)
    errors.assert_not_awaited()
    race = cog.races[CHANNEL_ID]
    assert race.phase is Phase.LOBBY
    assert race.card.size == h.FIELD_SIZE
    assert not race.card.grand_prix
    kwargs = channel.send.await_args.kwargs
    assert kwargs["embed"].title.startswith("🏇")
    assert kwargs["file"].filename == cog_module.CARD_PNG
    assert kwargs["allowed_mentions"] == cog_module.NO_MENTIONS
    cog.start.assert_called_once_with(race)


async def test_caballo_con_boleto_abre_y_cobra(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    errors = await caballo(cog, make_channel(), amount="500", pick="3-5")
    errors.assert_not_awaited()
    race = cog.races[CHANNEL_ID]
    ticket = race.tickets[ANA]
    assert ticket.pick == Pick(BetKind.EXACTA, (2, 4))
    assert ticket.odds == race.odds.odds(ticket.pick)
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 500
    assert cog.ficha(GUILD_ID, ANA) == 500


async def test_un_boleto_por_persona(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    await caballo(cog, channel, amount="100", pick="1")
    errors = await caballo(cog, channel, amount="100", pick="2")
    errors.assert_awaited_once()
    assert "uno por persona" in errors.await_args.args[0]
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 100


async def test_boleto_mal_escrito_no_cobra(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    errors = await caballo(cog, make_channel(), amount="100", pick="9")
    errors.assert_awaited_once()
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE


async def test_sin_saldo_no_apuesta(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    errors = await caballo(cog, make_channel(), amount=str(STARTING_BALANCE + 1), pick="1")
    errors.assert_awaited_once()
    assert CHANNEL_ID not in cog.races


async def test_fuera_del_casino_se_rechaza(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    cog.casino_channel_ids = frozenset({1234})
    errors = await caballo(cog, make_channel(), amount="100", pick="1")
    assert "1234" in errors.await_args.args[0]
    assert CHANNEL_ID not in cog.races


async def test_con_carrera_en_pista_no_se_abre_otra(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    await caballo(cog, channel)
    cog.races[CHANNEL_ID].phase = Phase.RUNNING
    errors = await caballo(cog, channel, amount="100", pick="1")
    assert "en pista" in errors.await_args.args[0]


async def test_los_botones_rapidos_apuestan_la_ficha(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    await caballo(cog, channel)
    race = cog.races[CHANNEL_ID]
    race.message = channel.test_message
    cog.set_ficha(GUILD_ID, ANA, 250)

    sanxe = make_interaction(ANA)
    await race.follow_sanxe(sanxe)
    assert race.tickets[ANA].pick == Pick(BetKind.WIN, (race.tip.horse,))
    assert race.tickets[ANA].stake == 250
    assert race.tickets[ANA].via == "sanxe"
    sanxe.response.defer.assert_awaited_once()
    sanxe.edit_original_response.assert_awaited_once()
    assert sanxe.followup.send.await_args.kwargs["ephemeral"] is True

    crowd = make_interaction(LEO, "Leo")
    await race.follow_crowd(crowd)
    assert race.tickets[LEO].pick.horses == (race.tip.horse,)
    assert race.tickets[LEO].via == "pueblo"

    again = make_interaction(ANA)
    await race.random_bet(again)
    again.response.send_message.assert_awaited_once()
    assert "Ya llevas boleto" in again.response.send_message.await_args.args[0]


async def test_el_panel_monta_un_trio_y_apuesta(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    await caballo(cog, make_channel())
    race = cog.races[CHANNEL_ID]
    panel = cog_module.BetPanel(race, ANA, 300)
    assert panel.pick() is None
    panel.kind = BetKind.TRIFECTA
    panel.horses = [0, 1, 1]
    assert panel.pick() is None  # caballo repetido
    panel.horses = [0, 1, 2]
    panel.refresh()
    assert panel.pick() == Pick(BetKind.TRIFECTA, (0, 1, 2))
    assert "Trío" in panel.text()
    interaction = make_interaction(ANA, message_id=None)
    await panel._on_confirm(interaction)
    assert race.tickets[ANA].stake == 300
    text, image = interaction.edit_original_response.await_args_list
    assert text.kwargs["view"] is None
    assert "attachments" in image.kwargs


async def test_el_panel_tiene_un_menu_por_caballo_del_boleto(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    await caballo(cog, make_channel())
    race = cog.races[CHANNEL_ID]
    panel = cog_module.BetPanel(race, ANA, 100)
    selects = [c for c in panel.children if isinstance(c, discord.ui.Select)]
    assert len(selects) == 2  # tipo y caballo
    panel.kind = BetKind.TRIFECTA
    panel.refresh()
    selects = [c for c in panel.children if isinstance(c, discord.ui.Select)]
    assert len(selects) == 4


# -- La carrera y el dinero ------------------------------------------------------------------


async def test_quien_acierta_cobra_la_cuota_y_el_libro_cuadra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    await caballo(cog, channel, amount="500", pick="1")
    await caballo(cog, channel, user_id=LEO, name="Leo", amount="300", pick="2")
    race = cog.races[CHANNEL_ID]
    race.message = channel.test_message
    force(monkeypatch, fixed_result((0, 1, 2, 3, 4, 5)))

    await race.race()

    ana, leo = race.tickets[ANA], race.tickets[LEO]
    assert ana.prize == h.payout(500, ana.odds)
    assert leo.prize == 0
    for user_id in (ANA, LEO):
        assert await cog.economy.balance(GUILD_ID, user_id) == ledger_sum(tmp_path, user_id)
    retained = sum(t.settlement.tax_delta for t in (ana, leo) if t.settlement)
    assert state_balance(tmp_path) == retained
    assert ana.settlement is not None
    expected = STARTING_BALANCE + ana.net - ana.settlement.tax_delta
    assert await cog.economy.balance(GUILD_ID, ANA) == expected
    assert race.phase is Phase.DONE
    final = channel.test_message.edit.await_args.kwargs
    assert final["embed"].title.startswith("🏁")
    assert isinstance(final["view"], cog_module.RematchView)


async def test_la_carrera_se_apunta_en_el_establo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    await caballo(cog, channel, amount="100", pick="1")
    race = cog.races[CHANNEL_ID]
    force(monkeypatch, fixed_result((5, 4, 3, 2, 1, 0)))
    await race.race()
    records = await cog.repository.records(GUILD_ID)
    winner = race.card.horses[5].key
    assert records[winner].wins == 1
    assert records[winner].form == (1,)
    assert records[race.card.horses[0].key].form == (6,)
    meta = await cog.repository.meta(GUILD_ID)
    assert (meta.races, meta.since_grand_prix) == (1, 1)


async def test_el_resultado_privado_dice_lo_que_se_escapo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    await caballo(cog, channel)
    race = cog.races[CHANNEL_ID]
    race.message = channel.test_message
    interaction = make_interaction(ANA)
    await race.bet_from_button(interaction, Pick(BetKind.WIN, (1,)), via="panel", stake=100)
    force(monkeypatch, fixed_result((0, 1, 2, 3, 4, 5), gaps=(0, 0.02, 2, 3, 4, 5)))
    monkeypatch.setattr(cog_module.renta, "hint", AsyncMock(return_value=None))
    await race.race()
    text = interaction.followup.send.await_args_list[-1].args[0]
    assert "❌" in text
    assert "Entró 2º" in text


async def test_apagar_en_la_parrilla_devuelve_lo_apostado(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    await caballo(cog, channel, amount="400", pick="2")
    await cog.cog_unload()
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE
    assert cog.races == {}


async def test_parrilla_vacia_vuelve_a_la_cuadra(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    await caballo(cog, channel)
    race = cog.races[CHANNEL_ID]
    race.message = channel.test_message
    await race.run()
    assert CHANNEL_ID not in cog.races
    embed = channel.test_message.edit.await_args.kwargs["embed"]
    assert "cuadra" in embed.description


async def test_bucle_completo_corre_y_libera_el_canal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, clock = await make_cog(tmp_path)
    channel = make_channel()
    await caballo(cog, channel, amount="100", pick="1")
    race = cog.races[CHANNEL_ID]
    race.message = channel.test_message
    force(monkeypatch, fixed_result((0, 1, 2, 3, 4, 5)))
    await race.run()
    assert CHANNEL_ID not in cog.races
    assert clock.now >= race.lobby_ends
    errors = await caballo(cog, channel)
    errors.assert_not_awaited()
    assert cog.races[CHANNEL_ID] is not race


async def test_listo_es_solo_para_quien_tiene_boleto(tmp_path: Path) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    await caballo(cog, channel)
    race = cog.races[CHANNEL_ID]
    race.message = channel.test_message
    nadie = make_interaction(ANA)
    await race.mark_ready(nadie)
    assert "Primero haz tu boleto" in nadie.response.send_message.await_args.args[0]
    assert not race.go.is_set()


async def test_si_todos_estan_listos_salen_sin_esperar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, clock = await make_cog(tmp_path)

    async def slow_sleep(seconds: float) -> None:
        # La parrilla no acaba nunca por reloj: solo ✅ Listo la cierra.
        if seconds >= cog_module.LOBBY_SECONDS / 2:
            await asyncio.Event().wait()
        clock.now += seconds

    cog.sleep = slow_sleep  # type: ignore[method-assign]
    channel = make_channel()
    await caballo(cog, channel, amount="100", pick="1")
    await caballo(cog, channel, user_id=LEO, name="Leo", amount="100", pick="2")
    race = cog.races[CHANNEL_ID]
    race.message = channel.test_message
    force(monkeypatch, fixed_result((0, 1, 2, 3, 4, 5)))
    task = asyncio.create_task(race.run())
    await asyncio.sleep(0)

    ana = make_interaction(ANA)
    await race.mark_ready(ana)
    assert "Falta: Leo" in ana.response.send_message.await_args.args[0]
    assert race.tickets[ANA].ready
    assert not race.go.is_set()
    otra = make_interaction(ANA)
    await race.mark_ready(otra)
    assert "Ya estabas listo" in otra.response.send_message.await_args.args[0]

    leo = make_interaction(LEO, "Leo")
    await race.mark_ready(leo)
    assert "Salen" in leo.response.send_message.await_args.args[0]
    assert race.go.is_set()
    assert race.starter == LEO
    await asyncio.wait_for(task, timeout=5)
    assert race.phase is Phase.DONE
    assert clock.now < race.lobby_ends
    assert CHANNEL_ID not in cog.races


# -- Gran Premio ---------------------------------------------------------------------------


async def open_grand_prix(cog: Horses, monkeypatch: pytest.MonkeyPatch) -> tuple[Race, MagicMock]:
    monkeypatch.setattr(cog_module, "grand_prix_due", lambda **_: True)
    channel = make_channel()
    await caballo(cog, channel)
    race = cog.races[CHANNEL_ID]
    race.message = channel.test_message
    assert race.card.grand_prix
    assert race.card.size == h.GRAND_PRIX_FIELD
    return race, channel


async def test_el_bote_del_gran_premio_se_reparte_y_se_anuncia(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _clock = await make_cog(tmp_path)
    race, channel = await open_grand_prix(cog, monkeypatch)
    users = {ANA: "Ana", LEO: "Leo", EVA: "Eva"}
    picks = {ANA: Pick(BetKind.WIN, (0,)), LEO: Pick(BetKind.EXACTA, (0, 1)),
             EVA: Pick(BetKind.PLACE, (0,))}  # fmt: skip
    for user_id, name in users.items():
        await race.place(make_user(user_id, name), picks[user_id], 100, via="panel")
    force(monkeypatch, fixed_result(tuple(range(8))))

    await race.race()

    share = GRAND_PRIX_POT // 2
    assert race.tickets[ANA].pot_share == share
    assert race.tickets[LEO].pot_share == share
    assert race.tickets[EVA].pot_share == 0  # colocado no entra en el bote
    assert race.tickets[ANA].prize == h.payout(100, race.tickets[ANA].odds) + share
    meta = await cog.repository.meta(GUILD_ID)
    assert meta.pot == GRAND_PRIX_POT
    assert meta.since_grand_prix == 0
    announcement = channel.send.await_args_list[1]
    assert "<@10>" in announcement.args[0] and "<@11>" in announcement.args[0]
    assert "<@12>" not in announcement.args[0]
    mentioned = {u.id for u in announcement.kwargs["allowed_mentions"].users}
    assert mentioned == {ANA, LEO}


async def test_gran_premio_desierto_hace_crecer_el_bote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _clock = await make_cog(tmp_path)
    race, channel = await open_grand_prix(cog, monkeypatch)
    await race.place(make_user(ANA), Pick(BetKind.WIN, (3,)), 100, via="panel")
    force(monkeypatch, fixed_result(tuple(range(8))))
    await race.race()
    assert race.tickets[ANA].prize == 0
    meta = await cog.repository.meta(GUILD_ID)
    assert meta.pot == GRAND_PRIX_POT + GRAND_PRIX_POT_STEP
    # Sin bote ni boletos gordos: no hay anuncios aparte (solo la parrilla).
    assert channel.send.await_count == 1


async def test_un_boleto_pequeno_no_entra_en_el_bote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _clock = await make_cog(tmp_path)
    race, _channel = await open_grand_prix(cog, monkeypatch)
    await race.place(make_user(ANA), Pick(BetKind.WIN, (0,)), 99, via="panel")
    force(monkeypatch, fixed_result(tuple(range(8))))
    await race.race()
    assert race.tickets[ANA].pot_share == 0
    assert (await cog.repository.meta(GUILD_ID)).pot == GRAND_PRIX_POT + GRAND_PRIX_POT_STEP


async def test_solo_un_gran_premio_a_la_vez_por_servidor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _clock = await make_cog(tmp_path)
    await open_grand_prix(cog, monkeypatch)
    other = make_channel()
    other.id = CHANNEL_ID + 1
    await caballo(cog, other)
    assert not cog.races[CHANNEL_ID + 1].card.grand_prix


async def test_los_boletos_gordos_se_publican_sin_mencionar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    await caballo(cog, channel)
    race = cog.races[CHANNEL_ID]
    race.message = channel.test_message
    ticket = await race.place(make_user(ANA), Pick(BetKind.TRIFECTA, (0, 1, 2)), 10, via="panel")
    assert ticket.odds >= cog_module.BIG_TICKET_ODDS
    force(monkeypatch, fixed_result((0, 1, 2, 3, 4, 5)))
    await race.race()
    big = channel.send.await_args_list[-1]
    assert big.kwargs["allowed_mentions"] == cog_module.NO_MENTIONS
    assert "premiado" in big.args[0]


async def test_la_carrera_se_dibuja_mientras_se_apuesta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`prepare` corre y dibuja durante la parrilla; al salir se usa eso y no se redibuja."""
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    await caballo(cog, channel, amount="100", pick="1")
    race = cog.races[CHANNEL_ID]
    race.message = channel.test_message
    force(monkeypatch, fixed_result((0, 1, 2, 3, 4, 5)))
    calls = []
    real = cog.renderer.race

    async def counting(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        calls.append(args)
        return await real(*args, **kwargs)

    cog.renderer.race = counting  # type: ignore[method-assign]
    race.prepare()
    await race.race()
    assert len(calls) == 1
    assert race.tickets[ANA].prize == h.payout(100, race.tickets[ANA].odds)


async def test_cada_boleto_dice_a_las_porras_cuando_se_hizo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Las porras no cuentan jugadas empezadas antes de su cierre: el boleto manda `started`."""
    cog, clock = await make_cog(tmp_path)
    channel = make_channel()
    await caballo(cog, channel, amount="100", pick="1")
    race = cog.races[CHANNEL_ID]
    race.message = channel.test_message
    record = AsyncMock()
    monkeypatch.setattr(cog_module.apuestas, "record", record)
    force(monkeypatch, fixed_result((0, 1, 2, 3, 4, 5)))
    await race.race()
    assert record.await_args.kwargs["details"] == (("started", int(START)),)


async def test_si_la_carrera_aun_se_dibuja_se_cierran_las_apuestas_al_momento(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con todos listos antes de acabar el dibujo, la parrilla pierde los botones ya.

    Antes se quedaba igual, con los botones vivos, hasta tener el GIF (5-8 s).
    """
    cog, _clock = await make_cog(tmp_path)
    channel = make_channel()
    await caballo(cog, channel, amount="100", pick="1")
    race = cog.races[CHANNEL_ID]
    race.message = channel.test_message
    force(monkeypatch, fixed_result((0, 1, 2, 3, 4, 5)))
    release = asyncio.Event()
    real = cog.renderer.race

    async def slow(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        await release.wait()
        return await real(*args, **kwargs)

    cog.renderer.race = slow  # type: ignore[method-assign]
    race.prepare()
    edits = channel.test_message.edit
    edits.reset_mock()
    task = asyncio.create_task(race.race())

    def closing() -> dict | None:
        for call in edits.await_args_list:
            embed = call.kwargs.get("embed")
            if embed is not None and "Cajones cerrados" in (embed.title or ""):
                return call.kwargs
        return None

    async def closed() -> None:
        while closing() is None:
            await asyncio.sleep(0)

    # El dibujo sigue parado y la parrilla ya está cerrada.
    await asyncio.wait_for(closed(), 1)
    shown = closing()
    assert shown is not None
    assert shown["view"] is None and "attachments" not in shown
    release.set()
    await task
    # Después, el GIF de la carrera, nunca antes del cierre.
    kinds = [
        "gif" if (call.kwargs.get("attachments") or [None])[0] is not None else "texto"
        for call in edits.await_args_list
    ]
    gif_at = kinds.index("gif")
    assert edits.await_args_list[gif_at].kwargs["attachments"][0].filename == cog_module.RACE_GIF
    assert (
        edits.await_args_list.index(next(c for c in edits.await_args_list if c.kwargs is shown))
        < gif_at
    )
