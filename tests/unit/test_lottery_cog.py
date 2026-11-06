"""El panel de la lotería con imágenes: rasca animado, boleto al comprar y sorteo en dos tiempos.

El dibujo es el doble de `render_fakes` (`FakeLotteryScene`): aquí se mira qué se sube y
cuándo, no cómo se ve (eso, en `test_lottery_scene.py`). Discord es el doble de
`interaction_fakes`, que admite una sola respuesta como el de verdad.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction
from render_fakes import FakeLotteryScene

from bot.cogs.lottery import (
    CHECK_PNG,
    DRAW_GIF,
    DRAW_PNG,
    POSTER_PNG,
    SCRATCH_GIF,
    SCRATCH_PNG,
    TICKET_PNG,
    Loteria,
    LotteryPanel,
)
from bot.repositories.economy import STATE_ACCOUNT_ID, EconomyRepository
from bot.repositories.lottery import LotteryRepository
from bot.services.achievements import (
    lottery_buy_stats,
    lottery_check_stats,
    scratch_autoplay_stats,
    scratch_stats,
)
from bot.services.autoplay import AutoplaySession, StopReason
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.lottery import DRAW_TIMEZONE, GAME_BY_KEY, Pick, next_draw, random_pick

GUILD = 1
ALICE = 10
NOW = datetime(2026, 10, 5, 19, 0, tzinfo=DRAW_TIMEZONE).timestamp()  # lunes
X10 = GAME_BY_KEY["x10"]
PRIMITIVA = GAME_BY_KEY["primitiva"]
JUEVES = GAME_BY_KEY["jueves"]


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin esperar a que acabe el GIF: el resultado se escribe al momento."""
    monkeypatch.setitem(Loteria.scratch.__globals__, "REVEAL_MARGIN_SECONDS", 0)
    monkeypatch.setitem(Loteria.scratch.__globals__, "DRAW_REVEAL_MARGIN_SECONDS", 0)


async def make(tmp_path: Path, clock: Clock, bot: MagicMock | None = None) -> Loteria:
    database = tmp_path / "bot.sqlite3"
    repository = EconomyRepository(database, starting_balance=STARTING_BALANCE)
    await repository.initialize()
    lottery = LotteryRepository(database)
    await lottery.initialize()
    if bot is None:
        bot = MagicMock()
        bot.get_guild.return_value = None
    economy = EconomyService(repository, clock=clock)
    return Loteria(
        bot, economy, lottery, clock=clock, rng=random.Random(7), scene=FakeLotteryScene()
    )


def owner() -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = ALICE
    user.display_name = "Ana"
    user.bot = False
    return user


def click(user: MagicMock) -> MagicMock:
    interaction = fake_interaction(user)
    interaction.guild = MagicMock(id=GUILD)
    interaction.channel_id = 5
    return interaction


def attachments(call: object) -> list[str] | None:
    """Los nombres de los adjuntos de una edición, o `None` si no tocó los adjuntos."""
    files = call.kwargs.get("attachments")  # type: ignore[attr-defined]
    return None if files is None else [f.filename for f in files]


async def settle_later(cog: Loteria) -> None:
    """Espera a lo que estaba esperando a una animación (resultados y logros)."""
    while cog._later:  # noqa: SLF001
        await asyncio.gather(*list(cog._later))  # noqa: SLF001


async def test_el_panel_abre_con_el_cartel_y_no_lo_vuelve_a_subir(tmp_path: Path) -> None:
    cog = await make(tmp_path, Clock())
    panel = await cog.open_panel(MagicMock(id=GUILD), owner())
    assert panel.image is not None and panel.image[0] == POSTER_PNG
    first = panel.edit_kwargs()
    assert [f.filename for f in first["attachments"]] == [POSTER_PNG]
    # Lo mismo otra vez: sin adjuntos, Discord ya tiene la imagen.
    await panel.load()
    assert "attachments" not in panel.edit_kwargs()
    # Una pestaña sin imagen la quita.
    panel.tab = "primitiva"
    await panel.load()
    assert panel.edit_kwargs()["attachments"] == []
    # El cartel no se repinta si no cambia lo que enseña.
    assert cog.scene.modes.count("cartel") == 1  # type: ignore[attr-defined]


async def test_comprar_ensena_el_resguardo_y_el_decimo(tmp_path: Path) -> None:
    clock = Clock()
    cog = await make(tmp_path, clock)
    panel = LotteryPanel(cog, MagicMock(id=GUILD), owner())
    panel.tab = "primitiva"
    interaction = click(panel.owner)
    picks = [(random_pick(PRIMITIVA, cog.rng), 1) for _ in range(3)]

    await cog.buy(interaction, panel, PRIMITIVA, picks)

    call = interaction.edit_original_response.await_args
    assert attachments(call) == [TICKET_PNG]
    assert "✅" in (panel.notice or "")
    assert cog.scene.modes[-1] == "boleto"  # type: ignore[attr-defined]

    panel.tab = "nacional"
    await cog.buy(click(panel.owner), panel, JUEVES, [(Pick((48_288,)), 10)])
    # El doble pinta los mismos bytes: el décimo no se vuelve a subir, pero se ha pintado.
    assert panel.image == (TICKET_PNG, b"PNG")
    assert cog.scene.modes[-1] == "decimo"  # type: ignore[attr-defined]


async def test_el_rasca_sube_el_gif_y_escribe_el_resultado_al_acabar(tmp_path: Path) -> None:
    cog = await make(tmp_path, Clock())
    panel = LotteryPanel(cog, MagicMock(id=GUILD), owner())
    panel.tab = "rasca"
    interaction = click(panel.owner)

    await cog.scratch(interaction, panel, X10)

    first = interaction.edit_original_response.await_args_list[0]
    assert attachments(first) == [SCRATCH_GIF]
    rascando = panel.notice
    await settle_later(cog)
    last = interaction.edit_original_response.await_args_list[-1]
    # El resultado llega después, sin volver a subir el GIF.
    assert len(interaction.edit_original_response.await_args_list) == 2
    assert attachments(last) is None
    assert "Rascando" in (rascando or "") and panel.notice != rascando
    assert "premio" in (panel.notice or "").lower()
    balance = await cog.economy.balance(GUILD, ALICE)
    assert panel.balance == balance
    assert balance + await cog.economy.state_balance(GUILD) == STARTING_BALANCE


async def test_sin_saldo_el_rasca_sorteado_se_guarda_sin_ensenarse(tmp_path: Path) -> None:
    cog = await make(tmp_path, Clock())
    # Se gasta todo antes de rascar.
    await cog.economy.lottery(
        GUILD, charges=[(ALICE, STARTING_BALANCE, "loteria:x10")], hook=lambda c: None
    )
    panel = LotteryPanel(cog, MagicMock(id=GUILD), owner())
    plan = cog.plan_scratch(X10)
    panel.scratches[X10.key] = plan
    interaction = click(panel.owner)

    await cog.scratch(interaction, panel, X10)

    assert panel.scratches[X10.key] is plan
    assert "❌" in (panel.notice or "")
    assert attachments(interaction.edit_original_response.await_args) in (None, [])
    assert "rasca" not in cog.scene.modes  # type: ignore[attr-defined]


async def test_el_rasca_de_un_boton_se_pinta_antes_del_clic(tmp_path: Path) -> None:
    cog = await make(tmp_path, Clock())
    cog.ahead = True
    panel = LotteryPanel(cog, MagicMock(id=GUILD), owner())
    panel.prepare_scratches()
    assert set(panel.scratches) == {"x10", "7ymedia"}
    await panel._painting  # noqa: SLF001
    plan = panel.scratches["x10"]
    assert plan.drawing is not None and plan.drawing.done()
    interaction = click(panel.owner)

    await cog.scratch(interaction, panel, X10)

    # Se cobró lo sorteado y se usó lo pintado: no se pintó otra vez ese rasca.
    assert cog.scene.modes.count("rasca") == 2  # type: ignore[attr-defined]
    # Y ya hay otro sorteado para el siguiente clic, pintándose.
    assert panel.scratches["x10"] is not plan
    await panel._painting  # noqa: SLF001
    assert cog.scene.modes.count("rasca") == 3  # type: ignore[attr-defined]
    await settle_later(cog)


async def test_el_sorteo_sale_animado_y_el_resultado_despues(tmp_path: Path) -> None:
    clock = Clock()
    bot = MagicMock()
    guild = MagicMock(spec=discord.Guild)
    guild.id = GUILD
    guild.get_member = MagicMock(return_value=None)
    bot.get_guild.return_value = guild
    channel = MagicMock(spec=discord.TextChannel)
    message = MagicMock(edit=AsyncMock())
    channel.send = AsyncMock(return_value=message)
    bot.get_channel.return_value = channel
    cog = await make(tmp_path, clock, bot)
    cog.members = lambda guild_id: 20  # type: ignore[method-assign]
    panel = LotteryPanel(cog, guild, owner())
    picks = [(random_pick(PRIMITIVA, cog.rng), 1) for _ in range(5)]
    await cog.buy(click(panel.owner), panel, PRIMITIVA, picks)

    clock.now = next_draw(PRIMITIVA, NOW) + 1
    assert await cog.run_due_draws() == 1

    sent = channel.send.await_args
    assert sent.kwargs["file"].filename == DRAW_GIF
    assert "Salen las bolas" in sent.args[0]
    message.edit.assert_not_awaited()
    await settle_later(cog)
    edited = message.edit.await_args
    assert [f.filename for f in edited.kwargs["attachments"]] == [DRAW_PNG]
    assert "Sorteo" in edited.kwargs["content"] and "Bote" in edited.kwargs["content"]


async def test_mis_boletos_ensena_el_ultimo_boleto_sorteado(tmp_path: Path) -> None:
    clock = Clock()
    cog = await make(tmp_path, clock)
    panel = LotteryPanel(cog, MagicMock(id=GUILD), owner())
    assert await cog.tab_image(GUILD, panel.owner, "mios") == (CHECK_PNG, None)
    await cog.buy(click(panel.owner), panel, JUEVES, [(Pick((7,)), 1)])
    clock.now = next_draw(JUEVES, NOW) + 1
    await cog.run_due_draws()
    assert await cog.tab_image(GUILD, panel.owner, "mios") == (CHECK_PNG, b"PNG")
    assert cog.scene.modes[-1] == "decimo"  # type: ignore[attr-defined]


def test_estadisticas_nuevas_de_la_loteria() -> None:
    when = datetime(2026, 12, 22, 3, 0, tzinfo=DRAW_TIMEZONE)
    decimo = lottery_buy_stats(
        game="navidad", units=1, cost=200, owned_in_draw=1, balance_after=50,
        picks=[(12_321,), (0,), (40_666,)], when=when,
    )  # fmt: skip
    assert {"lottery_capicua", "lottery_zeros", "lottery_666", "lottery_xmas_last"} <= set(
        decimo.add
    )
    assert decimo.add["lottery_day_20261222"] == 1
    bote = lottery_buy_stats(
        game="primitiva", units=10, cost=100, owned_in_draw=10, balance_after=50,
        jackpot=30_000, picks=[(1, 2, 3, 4, 5, 6)], when=when,
    )  # fmt: skip
    assert bote.peak["lottery_jackpot_seen"] == 30_000
    assert bote.peak["lottery_pool_bets_max"] == 10
    assert bote.add["lottery_123456"] == 1
    # Un décimo no cuenta para el tope de los juegos de bote.
    assert "lottery_pool_bets_max" not in decimo.peak
    friday = datetime(2026, 11, 13, 4, 0, tzinfo=DRAW_TIMEZONE)
    rasca = scratch_stats(
        cost=20, prize=0, tax=0, top=False, balance_after=5, near=True, when=friday
    )
    assert {"lottery_scratch_near", "lottery_scratch_night", "lottery_scratch_friday13"} <= set(
        rasca.add
    )
    assert lottery_check_stats(when=friday).peak == {"lottery_check_day_20261113": 1}


async def test_el_libro_cuadra_tras_rascar_varias_veces(tmp_path: Path) -> None:
    cog = await make(tmp_path, Clock())
    panel = LotteryPanel(cog, MagicMock(id=GUILD), owner())
    for _ in range(5):
        await cog.scratch(click(panel.owner), panel, X10)
    await settle_later(cog)
    alice = await cog.economy.balance(GUILD, ALICE)
    state = await cog.economy.state_balance(GUILD)
    assert alice + state == STARTING_BALANCE
    assert STATE_ACCOUNT_ID != ALICE


def button(panel: LotteryPanel, custom_id: str) -> discord.ui.Button:
    (found,) = [c for c in panel.walk_children() if getattr(c, "custom_id", None) == custom_id]
    return found  # type: ignore[return-value]


async def test_el_turbo_ensena_el_rasca_ya_rascado_en_una_sola_edicion(tmp_path: Path) -> None:
    cog = await make(tmp_path, Clock())
    panel = LotteryPanel(cog, MagicMock(id=GUILD), owner())
    panel.tab = "rasca"
    panel.rebuild()
    toggle = click(panel.owner)
    await button(panel, "loteria:turbo").callback(toggle)
    assert panel.turbo and (GUILD, ALICE) in cog.turbo
    # Se recuerda al abrir otro panel.
    assert LotteryPanel(cog, MagicMock(id=GUILD), owner()).turbo
    interaction = click(panel.owner)

    await cog.scratch(interaction, panel, X10)

    (call,) = interaction.edit_original_response.await_args_list
    assert attachments(call) == [SCRATCH_PNG]
    assert "Rascando" not in (panel.notice or "") and "premio" in (panel.notice or "").lower()
    assert not cog._later  # noqa: SLF001 - nada espera a una animación
    assert panel.balance == await cog.economy.balance(GUILD, ALICE)


async def run_auto(cog: Loteria, panel: LotteryPanel, interaction: MagicMock) -> AutoplaySession:
    cog.start_autoplay(interaction, panel, X10)
    session = panel.autoplay
    assert session is not None and session.task is not None
    await session.task
    return session


async def test_el_auto_rasca_hasta_una_parada_y_el_libro_cuadra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(Loteria.scratch.__globals__, "AUTOPLAY_MIN_GAP", 0)
    cog = await make(tmp_path, Clock())
    panel = LotteryPanel(cog, MagicMock(id=GUILD), owner())
    panel.tab = "rasca"

    session = await run_auto(cog, panel, click(panel.owner))

    assert session.spins >= 1 and panel.autoplay is None
    assert "▶️ Auto:" in (panel.notice or "") and "rascas" in (panel.notice or "")
    alice = await cog.economy.balance(GUILD, ALICE)
    assert alice + await cog.economy.state_balance(GUILD) == STARTING_BALANCE
    assert alice - STARTING_BALANCE == session.net
    assert not cog._autoplays  # noqa: SLF001


async def test_con_el_auto_en_marcha_solo_vale_parar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(Loteria.scratch.__globals__, "AUTOPLAY_MIN_GAP", 0)
    cog = await make(tmp_path, Clock())
    panel = LotteryPanel(cog, MagicMock(id=GUILD), owner())
    panel.tab = "rasca"
    panel.rebuild()
    await button(panel, "loteria:auto:x10").callback(click(panel.owner))
    session = panel.autoplay
    assert session is not None
    while not session.armed:
        await asyncio.sleep(0)
    enabled = [
        c.custom_id
        for c in panel.walk_children()
        if isinstance(c, discord.ui.Button)
        if not c.disabled
    ]
    assert enabled == ["loteria:auto:x10"]
    late = click(panel.owner)
    late.data = {"custom_id": "loteria:rascar:x10"}
    assert not await panel.interaction_check(late)

    await button(panel, "loteria:auto:x10").callback(click(panel.owner))
    assert session.stop_requested
    assert session.task is not None
    await session.task

    assert session.spins <= 2 and panel.autoplay is None
    assert "Parado a mano" in (panel.notice or "")


def test_estadisticas_del_auto_de_rascas() -> None:
    assert scratch_autoplay_stats(spins=0, net=0, reason=StopReason.NO_FUNDS).add == {}
    quit_ = scratch_autoplay_stats(spins=1, net=-20, reason=StopReason.MANUAL).add
    assert quit_ == {"lottery_scratch_auto_sessions": 1, "lottery_scratch_auto_quick_quit": 1}
    assert (
        "lottery_scratch_auto_full"
        in scratch_autoplay_stats(spins=25, net=0, reason=StopReason.MAX_SPINS).add
    )
    rasca = scratch_stats(
        cost=20, prize=0, tax=0, top=False, balance_after=5, turbo=True, auto=True
    )
    assert rasca.add["lottery_scratch_turbo"] == rasca.add["lottery_scratch_auto"] == 1
