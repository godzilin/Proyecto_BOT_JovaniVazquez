"""Pruebas de bot.cogs.slots: la máquina con botones, el comando `slots`, Ráfaga y Auto.

Se usa la economía real sobre un SQLite temporal (para comprobar que el
dinero se mueve de verdad), rodillos trucados y un renderizador falso.
"""

from __future__ import annotations

import asyncio
import itertools
import random
from collections.abc import Iterable
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

import bot.cogs.slots as slots_module
from bot.cogs.slots import (
    BURST_SPINS,
    GIF_NAME,
    PNG_NAME,
    TIER_LINES,
    SlotMachineView,
    Slots,
    SlotsPlay,
    auto_text,
    machine_embed,
    parse_stake,
    result_text,
    spinning_embed,
    ticket_text,
)
from bot.repositories.economy import EconomyRepository
from bot.repositories.slots import SlotsRepository
from bot.services.achievements import slots_autoplay_stats
from bot.services.autoplay import AUTOPLAY_MAX, AUTOPLAY_MIN_GAP, StopReason
from bot.services.economy import STARTING_BALANCE, EconomyService, format_amount
from bot.services.slots import (
    BONUS_FREE_SPINS,
    BONUS_MAX,
    BONUS_NEAR_MISS,
    DOUBLE_MAX,
    FREE_SPINS,
    HEAT_DECAY_SECONDS,
    HEAT_MAX,
    POT_SEED,
    REEL_STRIPS,
    SEVEN,
    THREE_OF_A_KIND,
    BonusMeter,
    Kind,
    SlotMachine,
    WinTier,
    daily_stake,
    pot_share,
    respin_price,
    spin_at,
)
from bot.services.slots_render import SlotsMedia
from bot.services.taxes import gambling_day_tax

GUILD_ID = 1
OWNER_ID = 10
CASINO_CHANNEL = 555
ALL_STOPS = list(itertools.product(*(range(len(s)) for s in REEL_STRIPS)))


def stops_where(predicate) -> tuple[int, int, int]:  # noqa: ANN001
    for stops in ALL_STOPS:
        if predicate(spin_at(stops)):
            return stops
    raise AssertionError("Ninguna parada cumple la condición")


LOSS = stops_where(lambda s: not s.pay_halves and not s.scatters and not s.near_miss)
CHERRY = stops_where(lambda s: s.kind == Kind.CHERRY and not s.scatters)
GRAPES = stops_where(lambda s: s.kind == Kind.THREE and s.symbol == "G" and not s.scatters)
JACKPOT = stops_where(lambda s: s.is_jackpot)
FREE = stops_where(lambda s: s.triggers_free_spins and not s.pay_halves)
NEAR_SEVEN = stops_where(
    lambda s: s.line[:2] == (SEVEN, SEVEN) and s.teaser == SEVEN and not s.pay_halves
)
SEVENS = stops_where(lambda s: s.kind == Kind.THREE and s.symbol == SEVEN and not s.scatters)


class FakeRenderer:
    """Devuelve bytes fijos y apunta lo que se le pide: las pruebas no miran la imagen."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def spin(
        self, spin, *, turbo: bool = False, won: int = 0, stake: int = 0, respin: bool = False
    ) -> SlotsMedia:  # noqa: ANN001, E501
        self.calls.append(("spin", {"stops": spin.stops, "won": won, "respin": respin}))
        return SlotsMedia(gif=b"" if turbo else b"GIF", png=b"PNG", seconds=0.0)

    async def still(self, spin, *, highlight: bool | None = None) -> bytes:  # noqa: ANN001
        self.calls.append(("still", {"stops": spin.stops, "highlight": highlight}))
        return b"STILL"

    async def double(self, **kwargs) -> SlotsMedia | None:  # noqa: ANN003
        self.calls.append(("double", kwargs))
        return SlotsMedia(gif=b"DOUBLE", png=b"CARD", seconds=0.0)

    async def close(self) -> None:
        return None


class RiggedMachine(SlotMachine):
    """Para los rodillos donde se le diga, en orden; después, siempre en `LOSS`."""

    def __init__(self, sequence: Iterable[tuple[int, int, int]] = ()) -> None:
        super().__init__()
        self.sequence = list(sequence)

    def spin(self):  # noqa: ANN201
        stops = self.sequence.pop(0) if self.sequence else LOSS
        return spin_at(stops)

    def respin(self, spin):  # noqa: ANN001, ANN201
        stops = self.sequence.pop(0) if self.sequence else LOSS
        return spin_at(stops, count_scatters=False)


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """La animación no hace falta esperarla en las pruebas."""
    monkeypatch.setattr(slots_module, "REVEAL_MARGIN_SECONDS", 0)


async def make_cog(
    tmp_path: Path,
    sequence=(),  # noqa: ANN001
    channels=frozenset(),  # noqa: ANN001
    *,
    clock=None,  # noqa: ANN001
    coin=None,  # noqa: ANN001
    randbelow=None,  # noqa: ANN001
) -> Slots:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    slots_repository = SlotsRepository(tmp_path / "bot.db")
    await slots_repository.initialize()
    return Slots(
        MagicMock(),
        economy=EconomyService(repository),
        renderer=FakeRenderer(),  # type: ignore[arg-type]
        machine=RiggedMachine(sequence),
        casino_channel_ids=channels,
        repository=slots_repository,
        clock=clock or (lambda: 1_000_000.0),
        coin=coin,
        randbelow=randbelow or (lambda _n: 0),
    )


def make_user(user_id: int = OWNER_ID, name: str = "Diego") -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = name
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


def make_interaction(user_id: int = OWNER_ID) -> MagicMock:
    return fake_interaction(make_user(user_id))


def make_view(cog: Slots, stake: int = 100) -> SlotMachineView:
    return SlotMachineView(cog, guild_id=GUILD_ID, owner=make_user(), stake=stake)


def attachment_names(call) -> list[str]:  # noqa: ANN001
    return [file.filename for file in call.kwargs["attachments"]]


# -- Presentación -------------------------------------------------------------------


def make_play(stops, *, stake=100, payout=0, jackpot=0, free=False, hot=False) -> SlotsPlay:  # noqa: ANN001
    settlement = MagicMock()
    settlement.jackpot = jackpot
    settlement.mystery = False
    settlement.drought = 0
    settlement.bet.balance = 1_000
    return SlotsPlay(
        spin=spin_at(stops),
        stake=stake,
        free=free,
        hot=hot,
        payout=payout,
        settlement=settlement,
        media=SlotsMedia(b"", b"", 0.0),
        heat=0,
        session_spins=1,
    )


def test_el_medio_premio_se_celebra_como_premio() -> None:
    text = result_text(make_play(CHERRY, payout=50), random.Random(0))
    assert "Cobras 50 Y$" in text
    assert "-" not in text.splitlines()[0]


def test_el_jackpot_dice_cuanto_se_lleva_del_bote() -> None:
    text = result_text(make_play(JACKPOT, jackpot=12_345), random.Random(0))
    assert "12.345 Y$ del bote" in text


def test_un_trio_enseña_sus_simbolos() -> None:
    text = result_text(make_play(GRAPES, payout=1_000), random.Random(0))
    assert "🍇 🍇 🍇" in text
    assert "+900 Y$" in text


def test_los_tickets_anuncian_los_giros_gratis() -> None:
    assert "GIROS GRATIS" in result_text(make_play(FREE), random.Random(0))


def test_resumen_de_rafaga() -> None:
    plays = [make_play(GRAPES, payout=1_000), *[make_play(LOSS) for _ in range(9)]]
    text = auto_text(plays)
    assert "10 tiradas" in text
    assert "+0 Y$" not in text
    assert "1 con premio" in text


def test_maquina_caliente_avisa_de_la_siguiente_tirada() -> None:
    embed = machine_embed(
        owner="Diego",
        balance=10,
        stake=5,
        pot=POT_SEED,
        heat=HEAT_MAX,
        free_spins=0,
        free_stake=0,
        turbo=False,
    )
    assert any("caliente" in field.name for field in embed.fields)


def test_apuesta_por_defecto_y_formatos() -> None:
    assert parse_stake(None, 1_000) == 100
    assert parse_stake(None, 40) == 40
    assert parse_stake("all", 777) == 777
    with pytest.raises(ValueError):
        parse_stake("azul", 1_000)


# -- Máquina ------------------------------------------------------------------------


async def test_tirar_cobra_gira_y_paga(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [GRAPES])
    view = make_view(cog)
    interaction = make_interaction()

    await view.play(interaction)

    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == (
        STARTING_BALANCE - 100 + 1_000 - gambling_day_tax(1_000 - 100)
    )
    interaction.response.defer.assert_awaited_once()
    first, final = interaction.edit_original_response.await_args_list
    assert attachment_names(first) == [GIF_NAME]
    assert attachment_names(final) == [PNG_NAME]
    assert await cog.pot(GUILD_ID) == POT_SEED + pot_share(100)


async def test_en_turbo_solo_se_edita_una_vez(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog)
    view.turbo = True
    interaction = make_interaction()

    await view.play(interaction)

    assert attachment_names(interaction.edit_original_response.await_args) == [PNG_NAME]
    interaction.edit_original_response.assert_awaited_once()


async def test_el_turbo_se_recuerda_para_la_siguiente_maquina(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    await make_view(cog)._toggle_turbo(make_interaction())
    assert make_view(cog).turbo


async def test_sin_saldo_no_gira_y_avisa_en_privado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog, stake=STARTING_BALANCE + 1)
    interaction = make_interaction()

    await view.play(interaction)

    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
    interaction.edit_original_response.assert_not_awaited()

    assert await cog.pot(GUILD_ID) == POT_SEED


async def test_clic_mientras_gira_se_ignora(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog)
    view._busy = True
    interaction = make_interaction()

    await view.play(interaction)

    interaction.response.defer.assert_awaited_once()
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE


async def test_otro_miembro_no_puede_jugar_en_tu_maquina(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog)
    interaction = make_interaction(user_id=99)

    assert not await view.interaction_check(interaction)
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_los_tickets_dan_giros_gratis_que_no_se_cobran(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [FREE])
    view = make_view(cog)

    await view.play(make_interaction())
    assert view.free_spins == FREE_SPINS
    after_trigger = await cog.economy.balance(GUILD_ID, OWNER_ID)

    view.stake = 500  # cambiar la apuesta no cambia la de los giros gratis
    await view.play(make_interaction())

    assert view.free_spins == FREE_SPINS - 1
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == after_trigger


async def test_los_tickets_en_un_giro_gratis_suman_mas_giros(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [FREE, FREE])
    view = make_view(cog)

    await view.play(make_interaction())
    await view.play(make_interaction())

    assert view.free_spins == 2 * FREE_SPINS - 1


async def test_la_maquina_caliente_paga_doble(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [CHERRY] * HEAT_MAX + [GRAPES])
    view = make_view(cog)
    for _ in range(HEAT_MAX):
        await view.play(make_interaction())
    assert cog.heat(GUILD_ID, OWNER_ID) == HEAT_MAX
    before = await cog.economy.balance(GUILD_ID, OWNER_ID)
    state_before = (await cog.economy.treasury(GUILD_ID, since=0)).balance

    await view.play(make_interaction())

    withheld = (await cog.economy.treasury(GUILD_ID, since=0)).balance - state_before
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == before - 100 + 2_000 - withheld
    assert cog.heat(GUILD_ID, OWNER_ID) == 0


async def test_rafaga_juega_diez_tiradas_con_una_sola_edicion(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog, stake=10)
    interaction = make_interaction()

    await view._burst(interaction)

    assert view.session_spins == BURST_SPINS
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 10 * BURST_SPINS
    interaction.response.defer.assert_awaited_once()
    interaction.edit_original_response.assert_awaited_once()


async def test_rafaga_para_cuando_no_llega_el_dinero(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog, stake=300)

    await view._burst(make_interaction())

    assert view.session_spins == 3
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 900


async def test_rafaga_para_con_el_bote_y_lo_anuncia(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [LOSS, JACKPOT])
    view = make_view(cog)
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    view.message = MagicMock()
    view.message.channel = channel

    await view._burst(make_interaction())

    assert view.session_spins == 2
    assert "JOVANAZO" in channel.send.await_args.args[0]
    assert await cog.pot(GUILD_ID) == POT_SEED


async def test_al_cerrar_se_juegan_los_giros_gratis_pendientes(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [FREE])
    view = make_view(cog)
    await view.play(make_interaction())

    await view.on_timeout()

    assert view.free_spins == 0
    assert view.session_spins == 1 + FREE_SPINS


async def test_all_in_y_mitad_cambian_la_apuesta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog)

    await view._all_in(make_interaction())
    assert view.stake == STARTING_BALANCE
    await view._halve(make_interaction())
    assert view.stake == STARTING_BALANCE // 2


async def test_la_tabla_de_premios_es_privada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    interaction = make_interaction()

    await make_view(cog)._paytable(interaction)

    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


# -- Comando ------------------------------------------------------------------------


async def test_slots_abre_la_maquina_parada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    send = AsyncMock(return_value=MagicMock())
    channel = MagicMock()
    channel.id = CASINO_CHANNEL

    await cog._slots_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=channel,
        user=make_user(),
        amount_text="250",
        send=send,
        send_error=AsyncMock(),
    )

    kwargs = send.await_args.kwargs
    assert kwargs["file"].filename == PNG_NAME
    assert kwargs["view"].stake == 250
    assert kwargs["view"] in cog.machines


async def test_slots_fuera_del_casino_se_rechaza(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, channels=frozenset({CASINO_CHANNEL}))
    send_error = AsyncMock()
    channel = MagicMock()
    channel.id = 1234

    await cog._slots_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=channel,
        user=make_user(),
        amount_text=None,
        send=AsyncMock(),
        send_error=send_error,
    )

    assert "tragaperras" in send_error.await_args.args[0].lower()


async def test_slots_con_mas_de_lo_que_tienes_avisa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    send_error = AsyncMock()

    await cog._slots_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=MagicMock(),
        user=make_user(),
        amount_text="5000",
        send=AsyncMock(),
        send_error=send_error,
    )

    send_error.assert_awaited_once()


# -- ▶️ Auto ------------------------------------------------------------------------


@pytest.fixture
def fast_gap(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Sin espera entre tiradas; devuelve las esperas pedidas a `asyncio.sleep`."""
    waited: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay: float, *args: object) -> None:
        waited.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return waited


def autoplay_view(cog: Slots, stake: int = 10) -> SlotMachineView:
    """Máquina con un mensaje normal (`.tragas`): se edita con `message.edit`, sin token."""
    view = make_view(cog, stake)
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    view.message = MagicMock()
    view.message.channel = channel
    view.message.edit = AsyncMock()
    return view


async def run_autoplay(view: SlotMachineView, interaction: MagicMock | None = None) -> MagicMock:
    """Pulsa ▶️ Auto y espera a que acabe la sesión. Devuelve la interacción del clic."""
    interaction = interaction or make_interaction()
    await view._autoplay_click(interaction)
    assert view.autoplay is not None
    await view.autoplay.task  # type: ignore[arg-type]
    return interaction


def edited_names(view: SlotMachineView) -> list[list[str]]:
    return [
        [file.filename for file in call.kwargs["attachments"]]
        for call in view.message.edit.await_args_list
        if "attachments" in call.kwargs
    ]


def last_description(view: SlotMachineView) -> str:
    return view.message.edit.await_args.kwargs["embed"].description


async def test_auto_encadena_tiradas_con_animacion_hasta_el_tope(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    cog = await make_cog(tmp_path, [GRAPES, GRAPES])
    view = autoplay_view(cog, stake=10)

    interaction = await run_autoplay(view)

    assert view.session_spins == AUTOPLAY_MAX
    # Cada tirada: el GIF y, después, el PNG final; y al cerrar, el resumen sin imagen.
    assert edited_names(view) == [[GIF_NAME], [PNG_NAME]] * AUTOPLAY_MAX
    assert view.message.edit.await_count == 2 * AUTOPLAY_MAX + 1
    assert "25 tiradas" in last_description(view)
    assert "tope" in last_description(view)
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) > 0
    # Contesta al clic una sola vez y nunca edita con el token de la interacción.
    interaction.response.defer.assert_awaited_once()
    interaction.edit_original_response.assert_not_awaited()
    assert view.autoplay is None
    assert not view._busy
    assert not any(item.disabled for item in view.children)  # type: ignore[attr-defined]
    assert view.autoplay_button.label == "▶️ Auto"
    assert view.timeout == slots_module.MACHINE_TIMEOUT


async def test_auto_ensena_el_contador_de_tiradas_en_cada_resultado(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    """El contador «tirada n/25» sale en el resultado de cada tirada del Auto.

    Antes, la línea fiscal reutilizaba el nombre `note` y lo pisaba: el
    contador no salía nunca.
    """
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=100)

    await run_autoplay(view)

    results = [
        call.kwargs["embed"].description
        for call in view.message.edit.await_args_list
        if [f.filename for f in call.kwargs.get("attachments", [])] == [PNG_NAME]
    ]
    assert len(results) == view.session_spins
    for number, description in enumerate(results, start=1):
        assert f"tirada {number}/{AUTOPLAY_MAX}" in description


async def test_auto_cobra_cada_tirada_una_vez(tmp_path: Path, fast_gap: list[float]) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=100)

    await run_autoplay(view)

    # Todo pierde: para al perder 10 apuestas, y el saldo cuadra con las tiradas.
    assert view.session_spins == 10
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 10 * 100
    assert "Techo de gasto" in last_description(view)


async def test_auto_en_turbo_solo_manda_la_imagen_final_y_deja_el_intervalo(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=100)
    view.turbo = True

    await run_autoplay(view)

    assert set(map(tuple, edited_names(view))) == {(PNG_NAME,)}
    assert view.message.edit.await_count == view.session_spins + 1
    assert fast_gap  # esperó entre tiradas
    assert all(0 < wait <= AUTOPLAY_MIN_GAP for wait in fast_gap)


async def test_auto_para_cuando_no_llega_el_saldo(tmp_path: Path, fast_gap: list[float]) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=300)

    await run_autoplay(view)

    assert view.session_spins == 3
    assert "no te llega" in last_description(view)
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 900


async def test_auto_sin_saldo_desde_el_principio_avisa_en_privado_y_no_edita(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=STARTING_BALANCE + 1)

    interaction = await run_autoplay(view)

    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
    view.message.edit.assert_not_awaited()
    assert view.session_spins == 0
    assert not view._busy
    assert view.autoplay is None


async def test_auto_para_con_el_bote_lo_anuncia_y_deja_ver_la_tirada(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    cog = await make_cog(tmp_path, [LOSS, JACKPOT])
    view = autoplay_view(cog, stake=10)

    await run_autoplay(view)

    assert view.session_spins == 2
    assert "premio gordo" in last_description(view)
    assert "del bote" in last_description(view)  # el resumen no tapa la tirada
    assert "JOVANAZO" in view.message.channel.send.await_args.args[0]
    assert await cog.pot(GUILD_ID) == POT_SEED


async def test_auto_para_con_un_premio_de_cincuenta_veces_la_apuesta(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    sevens = stops_where(lambda s: s.kind == Kind.THREE and s.symbol == "7" and not s.scatters)
    cog = await make_cog(tmp_path, [LOSS, sevens])
    view = autoplay_view(cog, stake=10)

    await run_autoplay(view)

    assert view.session_spins == 2
    assert "premio gordo" in last_description(view)


async def test_auto_juega_los_giros_gratis_como_tirar(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    cog = await make_cog(tmp_path, [FREE])
    view = autoplay_view(cog, stake=100)

    await run_autoplay(view)

    # Una tirada pagada que da 5 gratis (no se cobran) y 9 pagadas más hasta el límite.
    assert view.free_spins == 0
    assert view.session_spins == 1 + FREE_SPINS + 9
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 10 * 100


async def test_parar_contesta_sin_ack_y_la_tirada_en_curso_acaba(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=10)
    events: list[str] = []
    stop_click = fake_interaction(make_user(), events=events)
    pressed = False

    async def press_stop_in_third_spin(**kwargs: object) -> None:
        nonlocal pressed
        if view.session_spins == 3 and not pressed:
            pressed = True
            await view._autoplay_click(stop_click)

    view.message.edit.side_effect = press_stop_in_third_spin

    await run_autoplay(view)

    assert events == ["response.edit_message"]  # directa: sin ack, sin base de datos
    stop_click.response.defer.assert_not_awaited()
    assert stop_click.response.edit_message.await_args.kwargs["view"] is view
    # La tercera tirada acabó entera (GIF y PNG) y la cuarta no se jugó.
    assert view.session_spins == 3
    assert "Parado a mano" in last_description(view)
    assert edited_names(view)[-2:] == [[GIF_NAME], [PNG_NAME]]


async def test_mientras_corre_solo_se_puede_pulsar_parar(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=10)
    seen: list[dict[str, bool]] = []

    async def spy(**kwargs: object) -> None:
        seen.append({item.label: item.disabled for item in view.children})  # type: ignore[attr-defined]

    view.message.edit.side_effect = spy

    await run_autoplay(view)

    running = seen[:-1]  # la última edición es el resumen, con todo activo
    assert running
    for buttons in running:
        assert buttons["⏹️ Parar"] is False
        assert [label for label, disabled in buttons.items() if not disabled] == ["⏹️ Parar"]
    assert not any(seen[-1].values())


async def test_dos_clics_a_la_vez_en_auto_no_cobran_dos_veces(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=100)
    first, second = make_interaction(), make_interaction()

    await asyncio.gather(view._autoplay_click(first), view._autoplay_click(second))
    assert view.autoplay is not None
    await view.autoplay.task  # type: ignore[arg-type]

    # El segundo clic se acepta y se ignora: ni para la sesión ni abre otra.
    second.response.defer.assert_awaited_once()
    assert view.session_spins == 10
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 10 * 100


async def test_tirar_mientras_corre_el_auto_se_rechaza(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=100)
    extra = make_interaction()
    tried = False

    async def try_to_spin(**kwargs: object) -> None:
        nonlocal tried
        if not tried:
            tried = True
            await view.play(extra)

    view.message.edit.side_effect = try_to_spin

    await run_autoplay(view)

    extra.response.defer.assert_awaited_once()
    extra.edit_original_response.assert_not_awaited()
    assert view.session_spins == 10
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 10 * 100


async def test_un_segundo_auto_mientras_corre_no_abre_otra_sesion(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=100)
    view._busy = True  # p. ej. una tirada de Tirar en curso
    interaction = make_interaction()

    await view._autoplay_click(interaction)

    interaction.response.defer.assert_awaited_once()
    assert view.autoplay is None
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE


async def test_un_parar_antes_de_que_se_vea_el_boton_se_ignora(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=100)
    await view._autoplay_click(make_interaction())
    assert view.autoplay is not None and not view.autoplay.armed
    early = make_interaction()

    await view._autoplay_click(early)  # el doble clic de ▶️ Auto

    early.response.defer.assert_awaited_once()
    assert not view.autoplay.stop_requested
    await view.autoplay.task  # type: ignore[arg-type]
    assert view.session_spins == 10


async def test_al_descargar_el_cog_el_auto_acaba_la_tirada_y_se_cierra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=10)
    cog.machines.add(view)
    in_gap = asyncio.Event()
    real_sleep = asyncio.sleep

    async def stuck_sleep(delay: float, *args: object) -> None:
        in_gap.set()
        await real_sleep(3600)

    monkeypatch.setattr(asyncio, "sleep", stuck_sleep)
    monkeypatch.setattr("bot.services.autoplay.CLOSE_GRACE_SECONDS", 0.05)
    await view._autoplay_click(make_interaction())
    task = view.autoplay.task  # type: ignore[union-attr]
    assert task.get_name().startswith("slots-autoplay-")
    await in_gap.wait()

    await cog.cog_unload()

    assert task.done()
    assert view.autoplay is None
    assert not view._busy
    assert view.session_spins == 1


async def test_al_caducar_la_vista_el_auto_se_detiene_limpio(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=10)
    release = asyncio.Event()

    async def hold_in_first_spin(**kwargs: object) -> None:
        await release.wait()

    view.message.edit.side_effect = hold_in_first_spin
    await view._autoplay_click(make_interaction())
    task = view.autoplay.task  # type: ignore[union-attr]
    await asyncio.sleep(0)
    closing = asyncio.create_task(view.on_timeout())
    await asyncio.sleep(0)
    release.set()
    await closing

    assert task.done()
    assert view.autoplay is None
    assert view.session_spins < AUTOPLAY_MAX


async def test_auto_sin_token_edita_un_mensaje_de_slash_por_el_canal(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog)
    message = MagicMock(spec=discord.InteractionMessage)
    message.id = 1234
    message.channel = MagicMock()
    partial = message.channel.get_partial_message.return_value
    partial.edit = AsyncMock()
    view.message = message
    interaction = make_interaction()

    assert view._message_edit(interaction) is partial.edit
    message.channel.get_partial_message.assert_called_once_with(1234)
    view.message = MagicMock(spec=discord.Message)
    assert view._message_edit(interaction) is view.message.edit
    view.message = None
    assert view._message_edit(interaction) is interaction.edit_original_response


async def test_auto_se_para_si_no_se_puede_editar_el_mensaje(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=10)
    gone = MagicMock(status=404, reason="Not Found")
    view.message.edit.side_effect = discord.NotFound(gone, "Unknown Message")

    await run_autoplay(view)

    # La tirada ya estaba cobrada y se cuenta; la siguiente no se juega a ciegas.
    assert view.session_spins == 1
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 10
    assert not view._busy


async def test_auto_avisa_de_la_renta_una_sola_vez_por_sesion(
    tmp_path: Path, fast_gap: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=100)
    remind = AsyncMock()
    monkeypatch.setattr(slots_module.renta, "remind", remind)

    interaction = await run_autoplay(view)

    assert view.session_spins == 10
    remind.assert_awaited_once_with(cog.bot, interaction)


async def test_el_boton_de_rafaga_sigue_con_su_custom_id_y_el_auto_tiene_el_suyo(
    tmp_path: Path,
) -> None:
    view = make_view(await make_cog(tmp_path))
    ids = {item.label: item.custom_id for item in view.children}  # type: ignore[attr-defined]
    assert ids[f"🔁 Ráfaga ×{BURST_SPINS}"] == "tragaperras:auto"
    assert ids["▶️ Auto"] == "tragaperras:autoplay"


def test_resumen_de_logros_de_sesion_segun_el_motivo() -> None:
    manual = slots_autoplay_stats(spins=12, net=300, reason=StopReason.MANUAL).add
    assert manual["slots_autoplay_manual"] == manual["slots_autoplay_exit_ahead"] == 1
    assert slots_autoplay_stats(spins=0, net=0, reason=StopReason.NO_FUNDS).add == {}


# -- Revamp: re-giro, doble o nada, giro del día, calor guardado y ticket ---------------


async def treasury(cog: Slots) -> int:
    return (await cog.economy.treasury(GUILD_ID, since=0)).balance


async def test_el_casi_premio_ofrece_re_girar_el_tercero_a_su_precio(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [NEAR_SEVEN])
    view = make_view(cog)

    await view.play(make_interaction())

    assert view.respin_offer is not None
    expected = respin_price(spin_at(NEAR_SEVEN), 100, await cog.pot(GUILD_ID))
    assert view.respin_offer[2] == expected
    assert view.respin_button in view.children
    assert format_amount(expected) in view.respin_button.label


async def test_re_girar_cobra_el_precio_y_paga_a_la_apuesta_original(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [NEAR_SEVEN, SEVENS])
    view = make_view(cog)
    await view.play(make_interaction())
    price = view.respin_offer[2]
    before = await cog.economy.balance(GUILD_ID, OWNER_ID)
    tax_before = await treasury(cog)

    await view._respin(make_interaction())

    withheld = await treasury(cog) - tax_before
    prize = 100 * THREE_OF_A_KIND[SEVEN]
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == before - price + prize - withheld
    assert view.respin_offer is None
    assert view.session_staked == 100 + price


async def test_si_el_bote_ha_subido_el_re_giro_no_se_cobra_y_avisa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [NEAR_SEVEN])
    view = make_view(cog)
    await view.play(make_interaction())
    spin, stake, _price, chain = view.respin_offer
    view.respin_offer = (spin, stake, 1, chain)  # el botón enseñaba un precio viejo
    before = await cog.economy.balance(GUILD_ID, OWNER_ID)
    interaction = make_interaction()

    await view._respin(interaction)

    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == before
    assert "ahora cuesta" in interaction.followup.send.await_args.args[0]


async def test_doble_o_nada_ganado_dobla_y_deja_seguir(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [GRAPES], coin=lambda: True)
    view = make_view(cog)
    await view.play(make_interaction())
    assert view.double_offer == (1_000, 0)
    before = await cog.economy.balance(GUILD_ID, OWNER_ID)
    tax_before = await treasury(cog)

    await view._double_black(make_interaction())

    withheld = await treasury(cog) - tax_before
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == before + 1_000 - withheld
    assert view.double_offer == (2_000, 1)
    assert view.red_button in view.children and view.black_button in view.children


async def test_doble_o_nada_perdido_se_lleva_lo_cobrado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [GRAPES], coin=lambda: False)
    view = make_view(cog)
    await view.play(make_interaction())
    before = await cog.economy.balance(GUILD_ID, OWNER_ID)
    tax_before = await treasury(cog)

    await view._double_red(make_interaction())

    returned = tax_before - await treasury(cog)
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == before - 1_000 + returned
    assert view.double_offer is None
    assert view.red_button not in view.children
    assert "⚫ Negro" in view.last_text


async def test_doble_o_nada_tiene_tope(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [CHERRY], coin=lambda: True)
    view = make_view(cog)
    await view.play(make_interaction())
    for _ in range(DOUBLE_MAX):
        await view._double_red(make_interaction())
    assert view.double_offer is None
    assert view.session_gross == 50 * 2**DOUBLE_MAX + sum(50 * 2**k for k in range(DOUBLE_MAX))


async def test_el_giro_del_dia_es_gratis_y_solo_uno(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [GRAPES, GRAPES])
    view = make_view(cog)
    ready, streak = await cog.daily_status(GUILD_ID, OWNER_ID)
    assert ready and streak == 1
    view.daily_streak = streak
    tax_before = await treasury(cog)

    await view._daily(make_interaction())

    withheld = await treasury(cog) - tax_before
    prize = daily_stake(1) * THREE_OF_A_KIND["G"]
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE + prize - withheld
    assert await cog.pot(GUILD_ID) == POT_SEED  # no aporta al bote
    assert await cog.daily_status(GUILD_ID, OWNER_ID) == (False, 1)
    assert view.daily_button not in view.children

    second = make_view(cog)
    second.daily_streak = 1  # una máquina abierta antes de cobrarlo
    interaction = make_interaction()
    await second._daily(interaction)
    assert "Ya has cobrado" in interaction.followup.send.await_args.args[0]


async def test_el_calor_se_guarda_y_se_enfria_si_te_vas(tmp_path: Path) -> None:
    now = [1_000_000.0]
    cog = await make_cog(tmp_path, [CHERRY, CHERRY], clock=lambda: now[0])
    view = make_view(cog)
    await view.play(make_interaction())
    await view.play(make_interaction())
    assert cog.heat(GUILD_ID, OWNER_ID) == 2

    # Otro arranque del bot, media hora después.
    now[0] += 3 * HEAT_DECAY_SECONDS
    reopened = await make_cog(tmp_path, clock=lambda: now[0])
    await reopened.load_heat(GUILD_ID, OWNER_ID)
    assert reopened.heat(GUILD_ID, OWNER_ID) == 2
    assert reopened.cool_down(GUILD_ID, OWNER_ID) == 2
    assert reopened.heat(GUILD_ID, OWNER_ID) == 0


async def test_el_embed_lleva_el_cartel_de_premios_y_el_tope_del_bote(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    embed = await make_view(cog, stake=200).current_embed()
    fields = {field.name: field.value for field in embed.fields}
    assert "Premios a 200 Y$" in fields
    table = fields["Premios a 200 Y$"] + "".join(fields.values())
    assert f"**{format_amount(200 * THREE_OF_A_KIND[SEVEN])}**" in table
    assert "Cae antes de" in fields["💰 Bote"]


async def test_la_sesion_enseña_premios_en_bruto_y_el_ticket_el_neto(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [CHERRY, CHERRY, LOSS])
    view = make_view(cog)
    for _ in range(3):
        await view.play(make_interaction())
    embed = await view.current_embed()
    assert any(f.name == "🎫 Premios cobrados" and "100 Y$" in f.value for f in embed.fields)

    text = ticket_text(spins=3, staked=view.session_staked, gross=view.session_gross)
    assert "Neto: -200 Y$" in text


def test_un_premio_de_diez_veces_se_celebra_como_gran_premio() -> None:
    play = make_play(GRAPES, payout=1_000)
    assert play.tier == WinTier.BIG
    assert result_text(play, random.Random(0)).splitlines()[0][2:] in TIER_LINES[WinTier.BIG]


def test_mientras_gira_se_ve_el_cartel_de_premios() -> None:
    embed = spinning_embed(owner="Diego", stake=300, free=False, hot=False, pot=POT_SEED)
    fields = {field.name: field.value for field in embed.fields}
    assert "Premios a 300 Y$" in fields
    assert f"**{format_amount(300 * THREE_OF_A_KIND[SEVEN])}**" in "".join(fields.values())


async def test_la_barra_de_bonus_llena_da_giros_gratis_a_la_apuesta_media(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [LOSS], randbelow=lambda _n: 0)
    # Con la barra a uno del final, un punto más la llena.
    cog._bonus[(GUILD_ID, OWNER_ID, 0)] = BonusMeter(BONUS_MAX - 1, 50 * 9, 9)
    view = make_view(cog)

    await view.play(make_interaction())

    assert view.free_spins == BONUS_FREE_SPINS
    assert view.free_stake == (50 * 9 + 100) // 10
    assert cog.bonus(GUILD_ID, OWNER_ID) == BonusMeter()
    assert "BARRA DE BONUS LLENA" in view.last_text


async def test_la_barra_de_bonus_se_guarda_y_sale_en_la_maquina(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [NEAR_SEVEN], randbelow=lambda _n: 0)
    view = make_view(cog)
    await view.play(make_interaction())

    reopened = await make_cog(tmp_path)
    await reopened.load_heat(GUILD_ID, OWNER_ID)
    assert reopened.bonus(GUILD_ID, OWNER_ID).points == BONUS_NEAR_MISS
    embed = await make_view(reopened).current_embed()
    assert any(f.name.startswith("🎁 Bonus") and "10 %" in f.value for f in embed.fields)


async def test_los_giros_gratis_no_llenan_la_barra(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [FREE, NEAR_SEVEN], randbelow=lambda _n: 2)
    view = make_view(cog)
    await view.play(make_interaction())
    points = cog.bonus(GUILD_ID, OWNER_ID).points

    await view.play(make_interaction())  # giro gratis

    assert cog.bonus(GUILD_ID, OWNER_ID).points == points


def test_tragas_lee_la_maquina_y_la_cantidad_en_cualquier_orden() -> None:
    from bot.cogs.slots import CLASSIC, MACHINE_CHOICES, parse_machine_args

    assert parse_machine_args(()) == (None, None)
    assert parse_machine_args(("500",)) == (None, "500")
    assert parse_machine_args(("volcan", "500")) == ("volcan", "500")
    assert parse_machine_args(("2k", "Volcán")) == ("volcan", "2k")
    assert parse_machine_args(("clasica", "all")) == (CLASSIC, "all")
    assert parse_machine_args(("Mina", "50")) == ("mina", "50")
    assert parse_machine_args(("olimpo",)) == ("olimpo", None)
    assert {c.value for c in MACHINE_CHOICES} >= {CLASSIC, "volcan", "mina", "olimpo"}


# -- Doble o nada pintado y pintar antes del clic ------------------------------------------


def renderer_calls(cog: Slots, name: str, since: int = 0) -> list[dict]:
    return [kw for call, kw in cog.renderer.calls[since:] if call == name]


async def test_el_doble_o_nada_gira_la_carta_y_la_deja_destapada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [GRAPES], coin=lambda: False)
    view = make_view(cog)
    await view.play(make_interaction())
    interaction = make_interaction()

    await view._double_black(interaction)

    edits = interaction.edit_original_response.await_args_list
    assert attachment_names(edits[0]) == [GIF_NAME]
    assert "Doble o nada" in edits[0].kwargs["embed"].description
    assert attachment_names(edits[-1]) == [PNG_NAME]
    (call,) = renderer_calls(cog, "double")
    assert call["fresh"] and call["stops"] == GRAPES
    assert (call["pick"], call["won"], call["history"]) == ("black", False, [])
    # Pierde eligiendo negro: la carta es roja y queda arriba, en las últimas cartas.
    assert view.double_history in (["H"], ["D"])


async def test_la_segunda_carta_no_vuelve_a_tapar_los_rodillos(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [GRAPES], coin=lambda: True)
    view = make_view(cog)
    await view.play(make_interaction())
    await view._double_red(make_interaction())
    await view._double_red(make_interaction())

    first, second = renderer_calls(cog, "double")
    assert (first["fresh"], second["fresh"]) == (True, False)
    assert second["history"] == view.double_history[:1]
    # Una tirada nueva vuelve a enseñar los rodillos.
    await view.play(make_interaction())
    assert not view.gamble_shown


async def test_el_regiro_solo_mueve_el_tercer_rodillo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [NEAR_SEVEN, LOSS])
    view = make_view(cog)
    await view.play(make_interaction())
    assert view.respin_offer is not None

    await view._respin(make_interaction())

    assert renderer_calls(cog, "spin")[-1]["respin"] is True


async def test_la_proxima_tirada_se_sortea_y_se_pinta_antes_del_clic(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [GRAPES])
    cog.ahead = True
    view = make_view(cog)
    view.prepare()
    await view._painting
    # Lo primero que se pinta es Tirar; como paga, también ½, ×2 y All-in.
    painted = renderer_calls(cog, "spin")
    assert painted[0]["stops"] == GRAPES
    assert len({call["won"] for call in painted if call["stops"] == GRAPES}) == 4
    count = len(cog.renderer.calls)

    await view.play(make_interaction())

    # Se juega la tirada sorteada y su GIF no se vuelve a pintar.
    assert view.last_stops == GRAPES
    assert not [c for c in renderer_calls(cog, "spin", count) if c["stops"] == GRAPES]


async def test_rojo_y_negro_se_pintan_antes_del_clic(tmp_path: Path) -> None:
    flips = iter([False])  # una sola moneda: la sorteada por adelantado
    cog = await make_cog(tmp_path, [GRAPES], coin=lambda: next(flips))
    view = make_view(cog)
    await view.play(make_interaction())
    cog.ahead = True
    view.prepare()
    await view._painting
    doubles = renderer_calls(cog, "double")
    assert {d["pick"] for d in doubles} == {"red", "black"}
    assert all(d["won"] is False for d in doubles)
    count = len(cog.renderer.calls)

    await view._double_red(make_interaction())

    assert view.double_offer is None
    assert not renderer_calls(cog, "double", count)


async def test_la_rafaga_juega_sus_tiradas_y_su_imagen_pintadas_antes(tmp_path: Path) -> None:
    # La primera es la de Tirar; las diez siguientes, las de la Ráfaga (acaba en 🍒).
    cog = await make_cog(tmp_path, [LOSS] * BURST_SPINS + [CHERRY])
    cog.ahead = True
    view = make_view(cog)
    view.prepare()
    await view._painting
    assert [c["stops"] for c in renderer_calls(cog, "still")] == [CHERRY]
    count = len(cog.renderer.calls)

    await view._burst(make_interaction())

    assert view.session_spins == BURST_SPINS
    assert view.last_stops == CHERRY
    # Después solo se pinta la Ráfaga siguiente, no esta otra vez.
    assert not [c for c in renderer_calls(cog, "still", count) if c["stops"] == CHERRY]


async def test_sin_pintar_por_adelantado_no_se_sortea_nada_de_mas(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [GRAPES, CHERRY])
    view = make_view(cog)
    view.prepare()
    assert view._painting is None and view._upcoming is None
    await view.play(make_interaction())
    assert view.last_stops == GRAPES


def test_un_gif_pesado_espera_un_poco_mas_antes_del_png() -> None:
    light = SlotsMedia(gif=b"x" * 1024, png=b"", seconds=2.0)
    heavy = SlotsMedia(gif=b"x" * 2**21, png=b"", seconds=2.0)
    assert slots_module.reveal_delay(heavy) == pytest.approx(
        slots_module.reveal_delay(light) + 2 * slots_module.REVEAL_SECONDS_PER_MB, abs=0.01
    )
