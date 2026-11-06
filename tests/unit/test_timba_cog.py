"""Pruebas de bot.cogs.timba: la mesa del canal, sentarse, los turnos, el dinero y la comisión.

Se usa la economía real sobre un SQLite temporal, un dibujo falso y un reloj falso
cuyo `sleep` ejecuta, en cada espera de la mesa, el siguiente clic del guion
(`FakeClock.script`): así se juegan los turnos sin esperar. Sin guion, cada turno
se agota (el jugador se duerme). El bucle no se arranca solo: cada prueba llama a
`play_hand()`.
"""

from __future__ import annotations

import asyncio
import random
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

from bot.cogs import timba as cog_module
from bot.cogs.timba import MAX_TIMEOUTS, Phase, Table, Timba
from bot.repositories.economy import STATE_ACCOUNT_ID, EconomyRepository
from bot.services.blackjack import Card
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.taxes import gambling_day_tax
from bot.services.timba import TimbaHand

GUILD_ID = 1
ANA, LEO = 10, 11
START = 1_800_000_000.0
RANKS = {"A": 1, "T": 10, "J": 11, "Q": 12, "K": 13, **{str(n): n for n in range(2, 10)}}
SUITS = {"s": 0, "h": 1, "d": 2, "c": 3}


def cards(text: str) -> tuple[Card, ...]:
    return tuple(Card(RANKS[c[0]], SUITS[c[1]]) for c in text.split())


class FakeClock:
    """Reloj que avanza al dormir y, en cada siesta, juega el siguiente clic del guion."""

    def __init__(self) -> None:
        self.now = START
        self.script: list[Callable[[], Awaitable[None]]] = []

    def wall(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        if self.script:
            await self.script.pop(0)()
        self.now += seconds


class FakeRenderer:
    async def board(self, **kwargs: Any) -> bytes:
        return b"PNG"

    async def timba(self, meta: dict[str, Any], state: dict[str, Any]) -> bytes:
        return b"TIMBA"

    async def close(self) -> None:
        pass


def make_user(user_id: int, name: str) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = name
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


USERS = {ANA: make_user(ANA, "Ana"), LEO: make_user(LEO, "Leo")}


def click(user_id: int) -> MagicMock:
    interaction = fake_interaction(USERS[user_id])
    interaction.guild = None
    return interaction


def make_channel() -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 777
    message = MagicMock()
    message.edit = AsyncMock()
    message.delete = AsyncMock()
    channel.send = AsyncMock(return_value=message)
    return channel


async def make_table(tmp_path: Path) -> tuple[Timba, Table, FakeClock]:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    clock = FakeClock()
    cog = Timba(
        MagicMock(),
        economy=EconomyService(repository),
        renderer=FakeRenderer(),  # type: ignore[arg-type]
        rng=random.Random(5),
        wall_clock=clock.wall,
        sleep=clock.sleep,
    )
    cog.start = MagicMock()  # type: ignore[method-assign]
    table, new = await cog.ensure_table(GUILD_ID, make_channel(), ANA, 20)
    assert table is not None and new
    await table.sit(USERS[ANA])
    await table.sit(USERS[LEO])
    return cog, table, clock


async def balance(cog: Timba, user_id: int) -> int:
    return await cog.economy.balance(GUILD_ID, user_id)


def state(tmp_path: Path) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        row = connection.execute(
            "SELECT balance FROM economy_wallets WHERE guild_id = ? AND user_id = ?",
            (GUILD_ID, STATE_ACCOUNT_ID),
        ).fetchone()
    return int(row[0]) if row else 0


def act(table: Table, user_id: int, how: str, amount: int = 0) -> Callable[[], Awaitable[None]]:
    """Un clic del guion: `call`, `fold` o `raise` (a `amount`)."""

    async def run() -> None:
        interaction = click(user_id)
        if how == "call":
            await table.call_click(interaction)
        elif how == "fold":
            await table.fold_click(interaction)
        else:
            await table.raise_callback(amount)(interaction)

    return run


@pytest.fixture
def rigged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ana con ases y Leo con siete-dos contra una mesa seca."""
    real = TimbaHand.deal

    def deal(*args: Any, **kwargs: Any) -> TimbaHand:
        hand = real(*args, **kwargs)
        holes = {ANA: cards("Ah As"), LEO: cards("7d 2c")}
        for player in hand.players:
            player.hole = holes[player.user_id]  # type: ignore[assignment]
        hand.board = cards("Kd 9c 6h 3s Jc")
        return hand

    monkeypatch.setattr(cog_module.TimbaHand, "deal", deal)


# -- Sentarse --------------------------------------------------------------------------


async def test_abrir_sienta_y_pide_dinero_para_la_ciega(tmp_path: Path) -> None:
    cog, table, _ = await make_table(tmp_path)
    assert [c.user.id for c in table.chairs] == [ANA, LEO]
    assert await balance(cog, ANA) == STARTING_BALANCE, "sentarse no cobra"
    poor = make_user(99, "Pobre")
    await cog.economy.place_bet(GUILD_ID, 99, game="test", stake=STARTING_BALANCE - 10)
    with pytest.raises(cog_module.TimbaError, match="hacen falta"):
        await table.sit(poor)


async def test_no_se_sienta_dos_veces(tmp_path: Path) -> None:
    _, table, _ = await make_table(tmp_path)
    with pytest.raises(cog_module.TimbaError, match="Ya estás"):
        await table.sit(USERS[ANA])


# -- Una mano --------------------------------------------------------------------------


async def test_retirarse_antes_del_flop_le_da_las_ciegas_al_otro_sin_comision(
    tmp_path: Path,
) -> None:
    cog, table, clock = await make_table(tmp_path)
    # A dos, el botón (Ana) pone la pequeña y habla primero.
    clock.script = [act(table, ANA, "fold")]
    await table.play_hand()
    assert await balance(cog, ANA) == STARTING_BALANCE - 10
    assert await balance(cog, LEO) == STARTING_BALANCE + 10 - gambling_day_tax(10)
    # Sin comisión: al Estado solo le llega lo que retiene el casino a Leo.
    assert state(tmp_path) == gambling_day_tax(10)
    assert table.phase is Phase.LOBBY and table.hands == 1
    assert "Ana** se retira" in (table.lobby_embed().description or "")


async def test_enfrentamiento_gana_la_mejor_mano_y_la_comision_va_al_estado(
    tmp_path: Path, rigged: None
) -> None:
    cog, table, clock = await make_table(tmp_path)
    clock.script = [act(table, ANA, "raise", 100), act(table, LEO, "call")]
    # Después del flop habla Leo (la grande): pasan los dos hasta el final.
    clock.script += [act(table, LEO, "call"), act(table, ANA, "call")] * 3
    await table.play_hand()
    hand = table.hand
    assert hand is not None and hand.result is not None and hand.result.showdown
    rake = hand.result.rake
    assert rake > 0
    withheld = gambling_day_tax(100 - rake)
    assert await balance(cog, ANA) == STARTING_BALANCE + 100 - rake - withheld
    assert await balance(cog, LEO) == STARTING_BALANCE - 100
    assert state(tmp_path) == rake + withheld, "la comisión va al Estado (más el IRPF del juego)"
    total = await balance(cog, ANA) + await balance(cog, LEO) + state(tmp_path)
    assert total == 2 * STARTING_BALANCE, "la mano no crea ni destruye dinero"
    text = table.lobby_embed().description or ""
    assert "pareja de ases" in text and "comisión (IAJ)" in text


async def test_quien_se_duerme_se_retira_y_a_las_dos_se_levanta(tmp_path: Path) -> None:
    _, table, _ = await make_table(tmp_path)
    for _ in range(2 * MAX_TIMEOUTS):
        if len(table.chairs) < 2:
            break
        await table.play_hand()
    assert len(table.chairs) < 2
    assert "se ha dormido" in (table.lobby_embed().description or "")


async def test_no_es_tu_turno_y_tus_cartas_en_privado(tmp_path: Path) -> None:
    _, table, clock = await make_table(tmp_path)
    seen: dict[str, Any] = {}

    async def peek() -> None:
        wrong = click(LEO)
        await table.call_click(wrong)
        seen["wrong"] = wrong.response.send_message.await_args.args[0]
        mine = click(ANA)
        await table.my_cards(mine)
        seen["cards"] = mine.response.send_message.await_args.args[0]
        await act(table, ANA, "fold")()

    clock.script = [peek]
    await table.play_hand()
    assert "No es tu turno" in seen["wrong"]
    assert seen["cards"].startswith("🃏 Tus cartas:")


async def test_los_botones_llevan_emojis_que_discord_acepta(tmp_path: Path) -> None:
    """🂠 (dorso de carta) no es emoji para Discord: rechazaba (400) cada edición con la mano
    en juego y nadie veía los botones de jugar. Del bloque de naipes, solo 🃏 es emoji."""
    _, table, clock = await make_table(tmp_path)
    assert table.message is not None
    clock.script = [act(table, ANA, "fold")]
    await table.play_hand()
    views = [c.kwargs["view"] for c in table.message.edit.await_args_list if c.kwargs.get("view")]
    emojis = {str(item.emoji) for view in views for item in view.children if item.emoji}
    assert "🃏" in emojis, "se ha subido la mesa de la mano"
    assert not [e for e in emojis if any(0x1F0A0 <= ord(c) <= 0x1F0FF and c != "🃏" for c in e)]


async def test_levantarse_a_mitad_de_mano_se_retira_y_se_va(tmp_path: Path) -> None:
    _, table, clock = await make_table(tmp_path)

    async def leave() -> None:
        await table.leave_click(click(ANA))

    clock.script = [leave]
    await table.play_hand()
    assert [c.user.id for c in table.chairs] == [LEO]


async def test_al_apagar_a_mitad_de_mano_se_devuelve_todo(tmp_path: Path) -> None:
    cog, table, clock = await make_table(tmp_path)

    async def stop() -> None:
        await table.shutdown()

    clock.script = [stop]
    await table.play_hand()
    assert await balance(cog, ANA) == STARTING_BALANCE
    assert await balance(cog, LEO) == STARTING_BALANCE


async def test_las_placas_del_final_dicen_lo_que_gana_o_pierde_cada_uno(
    tmp_path: Path, rigged: None
) -> None:
    """Antes salía lo apartado menos cero (+980 al que perdía 20): ya pagado, no vale."""
    _, table, clock = await make_table(tmp_path)
    clock.script = [act(table, ANA, "call")] + [
        act(table, LEO, "call"),
        act(table, ANA, "call"),
    ] * 4
    await table.play_hand()
    views = {v.name: v for v in table.seat_views(reveal=True)}
    assert views["Leo"].badge == ("lose", "-20 Y$")
    kind, text = views["Ana"].badge  # type: ignore[misc]
    assert kind == "win" and text.startswith("+") and views["Ana"].reveal


async def test_el_turno_se_agota_en_el_plazo_que_se_ve(tmp_path: Path) -> None:
    _, table, clock = await make_table(tmp_path)
    publish = table.publish

    async def slow_publish() -> None:
        await publish()
        clock.now += 5  # pintar y subir la mesa tarda

    naps: list[float] = []
    sleep = clock.sleep

    async def spy(seconds: float) -> None:
        naps.append(seconds)
        await sleep(seconds)

    table.publish = slow_publish  # type: ignore[method-assign]
    table.cog.sleep = spy
    await table.play_hand()
    assert naps[0] == cog_module.TURN_SECONDS - 5


async def test_la_mesa_de_entre_manos_no_tapa_la_mano_que_empieza(tmp_path: Path) -> None:
    """La edición del último ✅ Listo, aún en vuelo al empezar la mano, llegaba a Discord
    después que la de la mano: se veían los botones de entre manos y quien hablaba se
    dormía (se retiraba) sin poder jugar."""
    _, table, clock = await make_table(tmp_path)
    assert table.message is not None
    shown: list[str] = []

    async def edit(**kwargs: Any) -> None:
        title = kwargs["embed"].title
        if "mano" not in title:  # la de entre manos tarda más en llegar
            await asyncio.sleep(0.05)
        shown.append(title)

    table.message.edit = edit  # type: ignore[method-assign]
    table.edit_soon()
    seen: list[str] = []

    async def look() -> None:
        await asyncio.sleep(0.1)  # lo que hubiera en vuelo ya ha llegado
        seen.append(shown[-1])
        await act(table, ANA, "fold")()

    clock.script = [look]
    await table.play_hand()
    assert seen == ["♣️ Timba · mano 1"]


# -- Listo -----------------------------------------------------------------------------


async def test_listo_de_todos_empieza_la_mano_ya(tmp_path: Path) -> None:
    _, table, _ = await make_table(tmp_path)
    await table.mark_ready(click(ANA))
    assert not table.go.is_set()
    assert "✅ Ana" in (table.lobby_embed().description or "")
    await table.mark_ready(click(LEO))
    assert table.go.is_set()
    await table.sit(make_user(12, "Eva"))
    assert not table.go.is_set(), "quien llega no está listo"
    await table.play_hand()
    assert not any(c.ready for c in table.chairs) and not table.go.is_set()


# -- Pintar por adelantado -------------------------------------------------------------


def late_paints(table: Table) -> list[int]:
    """Apunta las imágenes que se pintan al momento (la mano real), no por adelantado."""
    late: list[int] = []
    paint = table.paint

    async def spy(hand: TimbaHand, *, reveal: bool = False) -> bytes:
        if hand is table.hand:
            late.append(len(hand.events))
        return await paint(hand, reveal=reveal)

    table.paint = spy  # type: ignore[method-assign]
    return late


async def test_la_mesa_del_que_se_duerme_ya_esta_pintada(tmp_path: Path) -> None:
    _, table, _ = await make_table(tmp_path)
    late = late_paints(table)
    await table.play_hand()  # Ana se duerme y se retira
    assert table.hands == 1
    assert len(late) == 1, "solo la primera imagen (el reparto no se planeó)"


async def test_cada_boton_ya_esta_pintado(tmp_path: Path, rigged: None) -> None:
    _, table, clock = await make_table(tmp_path)
    late = late_paints(table)
    clock.script = [act(table, ANA, "raise", 40), act(table, LEO, "call")]
    clock.script += [act(table, LEO, "call"), act(table, ANA, "call")] * 3
    await table.play_hand()
    hand = table.hand
    assert hand is not None and hand.result is not None and hand.result.showdown
    assert len(late) == 1, "subir al mínimo, igualar y pasar estaban pintados"


async def test_una_cantidad_a_mano_se_pinta_al_momento(tmp_path: Path) -> None:
    _, table, clock = await make_table(tmp_path)
    late = late_paints(table)
    clock.script = [act(table, ANA, "raise", 77), act(table, LEO, "fold")]
    await table.play_hand()
    assert len(late) == 2, "77 no tiene botón: solo esa se pinta tarde"


async def test_la_proxima_mano_se_reparte_y_pinta_durante_la_espera(tmp_path: Path) -> None:
    _, table, clock = await make_table(tmp_path)
    late = late_paints(table)
    table.next_hand_at = clock.now + cog_module.START_SECONDS
    table.plan_deal()
    assert table._dealing is not None
    await table._dealing
    planned = table.next_deal
    assert planned is not None
    await table.play_hand()
    assert table.hand is planned and late == []


async def test_si_cambia_lo_que_se_juega_se_reparte_de_nuevo(tmp_path: Path) -> None:
    cog, table, clock = await make_table(tmp_path)
    table.next_hand_at = clock.now + cog_module.START_SECONDS
    table.plan_deal()
    assert table._dealing is not None
    await table._dealing
    planned = table.next_deal
    await cog.economy.place_bet(GUILD_ID, ANA, game="test", stake=STARTING_BALANCE - 500)
    await table.play_hand()
    assert table.hand is not planned and table.next_deal is None
