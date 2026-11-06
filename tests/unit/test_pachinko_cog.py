"""Pruebas de bot.cogs.pachinko: máquina con botones, dinero, Ráfaga, ▶️ Auto y precarga.

Se usa la economía real sobre un SQLite temporal (para comprobar que el
dinero se mueve de verdad y que el libro cuadra), tandas trucadas y un
renderizador falso. Las máquinas de prueba no precargan la tanda siguiente
(así cada sorteo ocurre en el clic que lo pide); las pruebas de la precarga la
encienden con `make_cog(..., preload=True)`.
"""

from __future__ import annotations

import asyncio
import logging
import random
import sqlite3
import threading
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction

import bot.cogs.pachinko as pachinko_module
from bot.cogs.pachinko import (
    BURST_VOLLEYS,
    GIF_NAME,
    PNG_NAME,
    RANDOM_BOARD,
    Pachinko,
    PachinkoPlay,
    PachinkoView,
    burst_text,
    machine_embed,
    parse_args,
    parse_stake,
    paytable_embed,
    result_text,
)
from bot.repositories.economy import EconomyRepository
from bot.services.achievements import (
    PACHINKO_CLEAN_BOUNCES,
    PACHINKO_SLOW_FRAMES,
    PACHINKO_SWIFT_FRAMES,
    pachinko_autoplay_stats,
    pachinko_stats,
)
from bot.services.autoplay import AUTOPLAY_MAX, AUTOPLAY_MIN_GAP, StopReason
from bot.services.economy import STARTING_BALANCE, STATE_ACCOUNT_ID, EconomyService
from bot.services.levels import TIMEZONE
from bot.services.pachinko import (
    BALLS,
    BOARDS,
    CLASSIC,
    MIN_STAKE,
    ONI,
    SAKURA,
    Ball,
    Board,
    Draw,
    Kind,
    PachinkoMachine,
    Volley,
    build_volley,
)
from bot.services.pachinko_motion import BallMotion, VolleyMotion, motion_for
from bot.services.pachinko_physics import Start
from bot.services.pachinko_render import PachinkoMedia

GUILD_ID = 1
OWNER_ID = 10
CASINO_CHANNEL = 555
MISS = Draw((1, 2, 3), Kind.MISS, False, 0)


START_POCKET = CLASSIC.start_pocket
FEVER_BALLS = CLASSIC.fever_balls


def ball_in(pocket: int, board: Board = CLASSIC) -> Ball:
    return Ball((1,) * pocket + (0,) * (board.rows - pocket))


def blank(board: Board = CLASSIC) -> Volley:
    """Diez bolas en OUT: no devuelve nada."""
    return build_volley(board, [ball_in(3, board)] * 10, lambda: MISS)


BLANK = blank()
#: Un rush de 4 premios gordos en la Clásica: 4 × 30 bolas.
RUSH = build_volley(
    CLASSIC,
    [ball_in(START_POCKET)] + [ball_in(3)] * 9,
    lambda: Draw((5, 5, 5), Kind.RUSH, True, 4),
)
#: Devuelve algo, pero menos de lo apostado (3 bolas de 10).
SMALL = build_volley(CLASSIC, [ball_in(1)] + [ball_in(3)] * 9, lambda: MISS)


class FakeRenderer:
    """Devuelve bytes fijos: las pruebas no necesitan dibujar el tablero.

    Apunta en `motions` el movimiento que recibe cada dibujo.
    """

    def __init__(self) -> None:
        self.motions: list[VolleyMotion | None] = []
        #: Las tandas que se han dibujado con animación o con `render` a secas.
        self.rendered: list[Volley] = []
        #: Si se pone, `render` espera a que se active (desde otro hilo).
        self.gate: threading.Event | None = None

    def render(self, volley, *, turbo: bool = False, motion=None) -> PachinkoMedia:  # noqa: ANN001
        if self.gate is not None:
            assert self.gate.wait(10), "la prueba no abrió la compuerta"
        self.rendered.append(volley)
        self.motions.append(motion)
        return PachinkoMedia(gif=b"" if turbo else b"GIF", png=b"PNG", seconds=0.0)

    def still_png(self, volley, motion=None) -> bytes:  # noqa: ANN001
        self.motions.append(motion)
        return b"STILL"

    def idle_png(self, board) -> bytes:  # noqa: ANN001
        return f"IDLE:{board.key}".encode()


class RiggedMachine(PachinkoMachine):
    """Lanza las tandas que se le digan, en orden; después, tandas en blanco.

    Apunta en `boards` el tablero que pide cada tanda.
    """

    def __init__(self, sequence: Iterable[Volley] = ()) -> None:
        super().__init__()
        self.sequence = list(sequence)
        self.boards: list[str] = []

    def launch(self, board: Board) -> Volley:
        self.boards.append(board.key)
        return self.sequence.pop(0) if self.sequence else blank(board)


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """La animación no hace falta esperarla en las pruebas."""
    monkeypatch.setattr(pachinko_module, "REVEAL_MARGIN_SECONDS", 0)


async def make_cog(
    tmp_path: Path,
    sequence=(),  # noqa: ANN001
    channels=frozenset(),  # noqa: ANN001
    *,
    preload: bool = False,
    machine: PachinkoMachine | None = None,
) -> Pachinko:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    return Pachinko(
        MagicMock(),
        economy=EconomyService(repository),
        renderer=FakeRenderer(),  # type: ignore[arg-type]
        machine=machine or RiggedMachine(sequence),
        casino_channel_ids=channels,
        preload=preload,
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


def make_view(cog: Pachinko, stake: int = 100) -> PachinkoView:
    return PachinkoView(cog, guild_id=GUILD_ID, owner=make_user(), stake=stake)


def attachment_names(call) -> list[str]:  # noqa: ANN001
    return [file.filename for file in call.kwargs["attachments"]]


def ledger_sum(tmp_path: Path, user_id: int) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            (GUILD_ID, user_id),
        ).fetchone()
    return int(total)


def make_play(volley: Volley, *, stake: int = 100, won: int = 0) -> PachinkoPlay:
    settlement = MagicMock()
    settlement.balance = 1_000
    settlement.tax_delta = 0
    return PachinkoPlay(
        volley=volley,
        stake=stake,
        won=won,
        settlement=settlement,
        media=PachinkoMedia(b"", b"", 0.0),
        session_volleys=1,
        motion=motion_for(volley),
    )


# -- Presentación -------------------------------------------------------------------


def test_el_rush_se_celebra_con_sus_numeros() -> None:
    text = result_text(make_play(RUSH, won=1_200), random.Random(0))
    assert "5 5 5" in text
    assert "×4" in text


def test_devolver_menos_de_lo_apostado_se_celebra_como_premio() -> None:
    text = result_text(make_play(SMALL, won=30), random.Random(0))
    assert "Cobras" in text


def test_la_reserva_llena_lo_dice() -> None:
    volley = build_volley(CLASSIC, [ball_in(START_POCKET)] * 6 + [ball_in(3)] * 4, lambda: MISS)
    assert "limbo" in result_text(make_play(volley), random.Random(0))


def test_resumen_de_la_rafaga() -> None:
    text = burst_text([make_play(BLANK), make_play(RUSH, won=1_200)], "Parado: ¡ATARI!")
    assert "2 tandas" in text and "×4" in text and "ATARI" in text


def test_apuesta_por_defecto_minimo_y_formatos() -> None:
    assert parse_stake(None, 1_000) == 100
    assert parse_stake(None, 40) == 40
    assert parse_stake("all", 777) == 777
    with pytest.raises(ValueError):
        parse_stake("5", 1_000)
    with pytest.raises(ValueError):
        parse_stake("azul", 1_000)


# -- Dinero -------------------------------------------------------------------------


async def test_lanzar_cobra_anima_y_paga(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [RUSH])
    view = make_view(cog)
    interaction = make_interaction()

    await view._launch(interaction)

    won = 100 * 4 * FEVER_BALLS // 10
    balance = await cog.economy.balance(GUILD_ID, OWNER_ID)
    treasury = await cog.economy.treasury(GUILD_ID, since=0)
    # Lo que no ha llegado al jugador es exactamente lo que se ha quedado el Estado.
    assert balance + treasury.balance == STARTING_BALANCE - 100 + won
    assert ledger_sum(tmp_path, OWNER_ID) == balance
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) == treasury.balance
    interaction.response.defer.assert_awaited_once()
    first, final = interaction.edit_original_response.await_args_list
    assert attachment_names(first) == [GIF_NAME]
    assert attachment_names(final) == [PNG_NAME]


async def test_perder_despues_de_ganar_devuelve_el_irpf_desde_el_estado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [RUSH])
    await cog.economy.grant(GUILD_ID, OWNER_ID, amount=100_000, reason="test")
    view = make_view(cog, stake=1_000)
    await view._launch(make_interaction())
    after_win = (await cog.economy.treasury(GUILD_ID, since=0)).balance

    view.stake = 10_000
    await view._launch(make_interaction())  # BLANK: pierde 10.000

    after_loss = (await cog.economy.treasury(GUILD_ID, since=0)).balance
    assert after_loss < after_win
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) == after_loss
    assert ledger_sum(tmp_path, OWNER_ID) == await cog.economy.balance(GUILD_ID, OWNER_ID)


async def test_en_turbo_solo_se_edita_una_vez(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog)
    view.turbo = True
    interaction = make_interaction()

    await view._launch(interaction)

    assert attachment_names(interaction.edit_original_response.await_args) == [PNG_NAME]
    interaction.edit_original_response.assert_awaited_once()


async def test_el_turbo_se_recuerda_para_la_siguiente_maquina(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    await make_view(cog)._toggle_turbo(make_interaction())
    assert make_view(cog).turbo


async def test_sin_saldo_no_lanza_y_avisa_en_privado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog, stake=STARTING_BALANCE + 10)
    interaction = make_interaction()

    await view._launch(interaction)

    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
    interaction.edit_original_response.assert_not_awaited()
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE


async def test_clic_mientras_caen_las_bolas_se_ignora(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog)
    view._busy = True
    interaction = make_interaction()

    await view._launch(interaction)

    interaction.response.defer.assert_awaited_once()
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE


async def test_otro_miembro_no_puede_jugar_en_tu_maquina(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    interaction = make_interaction(user_id=99)
    assert not await make_view(cog).interaction_check(interaction)
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_la_rafaga_en_turbo_juega_cinco_tandas_con_una_sola_edicion(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog, stake=10)
    view.turbo = True
    interaction = make_interaction()

    await view._burst(interaction)

    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 10 * BURST_VOLLEYS
    interaction.edit_original_response.assert_awaited_once()
    assert attachment_names(interaction.edit_original_response.await_args) == [PNG_NAME]
    assert view.session_volleys == BURST_VOLLEYS
    assert cog.renderer.rendered == []


async def test_la_rafaga_sin_turbo_anima_las_bolas_de_las_cinco_tandas(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [SMALL, RUSH])
    view = make_view(cog, stake=10)
    interaction = make_interaction()

    await view._burst(interaction)

    assert view.session_volleys == 2  # el rush para la Ráfaga
    (together,) = cog.renderer.rendered
    assert together.balls == SMALL.balls + RUSH.balls
    assert together.draws == SMALL.draws + RUSH.draws
    gif, png = interaction.edit_original_response.await_args_list
    assert attachment_names(gif) == [GIF_NAME]
    assert attachment_names(png) == [PNG_NAME]
    assert "2 tandas" in view.last_text


async def test_la_rafaga_sin_turbo_dibuja_cinco_veces_mas_bolas(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog, stake=10)
    await view._burst(make_interaction())
    (together,) = cog.renderer.rendered
    assert len(together.balls) == BALLS * BURST_VOLLEYS


async def test_la_rafaga_para_con_un_atari_y_lo_anuncia(tmp_path: Path) -> None:
    long_rush = build_volley(
        CLASSIC,
        [ball_in(START_POCKET)] + [ball_in(3)] * 9,
        lambda: Draw((3, 3, 3), Kind.RUSH, True, 5),
    )
    cog = await make_cog(tmp_path, [BLANK, long_rush])
    view = make_view(cog)
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    view.message = MagicMock(channel=channel)

    await view._burst(make_interaction())

    assert view.session_volleys == 2
    assert "Parado" in view.last_text
    channel.send.assert_awaited_once()  # 5 premios gordos encadenados: se anuncia


async def test_un_rush_corto_no_llena_el_canal(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    await cog.shout(make_play(RUSH, won=1_200), make_user(), channel)
    channel.send.assert_not_awaited()


async def test_el_movimiento_se_calcula_fuera_del_event_loop_tambien_sin_dibujar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Turbo y Ráfaga no dibujan, pero los logros necesitan los choques: `motion_for` siempre."""
    threads: list[str] = []
    real = pachinko_module.motion_for

    def spy(volley: Volley) -> VolleyMotion:
        threads.append(threading.current_thread().name)
        return real(volley)

    monkeypatch.setattr(pachinko_module, "motion_for", spy)
    cog = await make_cog(tmp_path, [RUSH, BLANK])

    drawn = await cog.play(GUILD_ID, OWNER_ID, stake=100, board=CLASSIC, turbo=False)
    silent = await cog.play(GUILD_ID, OWNER_ID, stake=100, board=CLASSIC, turbo=True, render=False)

    assert len(threads) == 2
    assert threading.main_thread().name not in threads
    assert drawn.motion == motion_for(RUSH) and silent.motion == motion_for(BLANK)
    assert cog.renderer.motions == [drawn.motion]  # el dibujo reutiliza el mismo cálculo


async def test_si_el_movimiento_falla_no_se_mueve_dinero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(volley: Volley) -> VolleyMotion:
        raise RuntimeError("la física no cuadra")

    monkeypatch.setattr(pachinko_module, "motion_for", broken)
    cog = await make_cog(tmp_path, [RUSH])
    with pytest.raises(RuntimeError):
        await cog.play(GUILD_ID, OWNER_ID, stake=100, board=CLASSIC, turbo=True)
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE


async def test_la_rafaga_en_turbo_dibuja_el_ultimo_con_su_movimiento(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog, stake=10)
    view.turbo = True
    await view._burst(make_interaction())
    assert len(cog.renderer.motions) == 1
    assert isinstance(cog.renderer.motions[0], VolleyMotion)


async def test_la_mitad_no_baja_del_minimo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog, stake=15)
    await view._halve(make_interaction())
    assert view.stake == MIN_STAKE


# -- Tableros -----------------------------------------------------------------------


async def test_la_maquina_empieza_en_la_clasica(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog)
    assert view.board is CLASSIC
    await view._launch(make_interaction())
    assert cog.machine.boards == ["clasica"]


async def test_elegir_tablero_cambia_la_imagen_y_las_tandas(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = make_view(cog)
    view.board_select._values = ["oni"]  # lo que Discord rellena al elegir
    interaction = make_interaction()

    await view._choose_board(interaction)

    kwargs = interaction.edit_original_response.await_args.kwargs

    assert kwargs["attachments"][0].filename == PNG_NAME
    assert "Oni" in kwargs["embed"].title
    assert [o.default for o in view.board_select.options if o.value == "oni"] == [True]
    await view._launch(make_interaction())
    assert cog.machine.boards == ["oni"]
    # Se recuerda para la próxima máquina.
    assert make_view(cog).board is ONI


async def test_al_azar_cada_tanda_cae_en_un_tablero(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    picks = iter([0, 3, 1, 2, 0])
    cog.machine._randbelow = lambda n: next(picks)  # type: ignore[attr-defined]
    view = PachinkoView(cog, guild_id=GUILD_ID, owner=make_user(), stake=10, board_key=RANDOM_BOARD)
    view.turbo = True
    assert view.board is None

    await view._burst(make_interaction())

    assert cog.machine.boards == ["sakura", "oni", "clasica", "dragon", "sakura"]
    assert "Al azar" in (await view.current_embed()).title


async def test_la_rafaga_animada_al_azar_juega_las_cinco_en_un_tablero(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    picks = iter([3])
    cog.machine._randbelow = lambda n: next(picks)  # type: ignore[attr-defined]
    view = PachinkoView(cog, guild_id=GUILD_ID, owner=make_user(), stake=10, board_key=RANDOM_BOARD)

    await view._burst(make_interaction())

    assert cog.machine.boards == ["oni"] * BURST_VOLLEYS
    assert cog.renderer.rendered[0].board is ONI


def test_el_texto_dice_que_tablero_ha_tocado_al_azar() -> None:
    volley = blank(ONI)
    text = result_text(make_play(volley), random.Random(0), random_board=True)
    assert "Oni" in text


def test_la_tabla_de_premios_resume_todos_los_tableros() -> None:
    text = paytable_embed(SAKURA).description
    assert all(board.name in text for board in BOARDS.values())
    assert "Sakura" in paytable_embed(None).description


def test_el_embed_dice_el_riesgo() -> None:
    embed = machine_embed(owner="Diego", balance=1_000, stake=100, turbo=False, board=ONI)
    assert "Oni" in embed.title
    assert any(field.value == ONI.risk for field in embed.fields)


def test_cantidad_y_tablero_en_cualquier_orden() -> None:
    assert parse_args("500", "oni") == ("500", "oni")
    assert parse_args("Dragón", "2k") == ("2k", "dragon")
    assert parse_args("azar", None) == (None, RANDOM_BOARD)
    assert parse_args(None, None) == (None, None)
    with pytest.raises(ValueError):
        parse_args("oni", "sakura")
    with pytest.raises(ValueError):
        parse_args("500", "600")


# -- Comando ------------------------------------------------------------------------


async def test_pachinko_abre_la_maquina_parada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    send = AsyncMock(return_value=MagicMock())

    await cog._pachinko_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=MagicMock(id=CASINO_CHANNEL),
        user=make_user(),
        amount_text="250",
        send=send,
        send_error=AsyncMock(),
    )

    kwargs = send.await_args.kwargs
    assert kwargs["file"].filename == PNG_NAME
    assert kwargs["view"].stake == 250


async def test_pachinko_con_tablero_lo_abre_y_lo_recuerda(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    send = AsyncMock(return_value=MagicMock())

    await cog._pachinko_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=MagicMock(id=CASINO_CHANNEL),
        user=make_user(),
        amount_text=None,
        send=send,
        send_error=AsyncMock(),
        board_key="sakura",
    )

    assert send.await_args.kwargs["view"].board is SAKURA
    assert cog.board_default(GUILD_ID, OWNER_ID) == "sakura"


async def test_pachinko_fuera_del_casino_se_rechaza(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, channels=frozenset({CASINO_CHANNEL}))
    send_error = AsyncMock()

    await cog._pachinko_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=MagicMock(id=1234),
        user=make_user(),
        amount_text=None,
        send=AsyncMock(),
        send_error=send_error,
    )

    assert "pachinko" in send_error.await_args.args[0].lower()


async def test_pachinko_con_mas_de_lo_que_tienes_avisa(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    send_error = AsyncMock()

    await cog._pachinko_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=MagicMock(),
        user=make_user(),
        amount_text="5000",
        send=AsyncMock(),
        send_error=send_error,
    )

    send_error.assert_awaited_once()


# -- Logros -------------------------------------------------------------------------


def test_estadisticas_de_un_rush() -> None:
    delta = pachinko_stats(
        RUSH,
        motion=motion_for(RUSH),
        stake=100,
        won=1_200,
        turbo=False,
        session_volleys=3,
        when=datetime(2026, 1, 1, 4, tzinfo=TIMEZONE),
    )
    assert delta.add["pachinko_volleys"] == 1
    assert delta.add["pachinko_atari"] == 1
    assert delta.add["pachinko_rush"] == 1
    assert delta.add["pachinko_night"] == 1
    assert delta.peak["pachinko_renchan_max"] == 4
    assert delta.peak["pachinko_win_max"] == 1_100
    assert "pachinko_super" not in delta.add
    assert delta.add["pachinko_board_clasica"] == 1
    assert delta.add["pachinko_atari_clasica"] == 1


def test_estadisticas_de_una_tanda_en_blanco() -> None:
    delta = pachinko_stats(
        BLANK,
        motion=motion_for(BLANK),
        stake=100,
        won=0,
        turbo=True,
        session_volleys=1,
        when=datetime(2026, 1, 1, 12, tzinfo=TIMEZONE),
    )
    assert delta.add["pachinko_blank"] == 1
    assert delta.add["pachinko_turbo"] == 1
    assert "pachinko_win_max" not in delta.peak


def fake_motion(*falls: tuple[int, ...], pocket: int = 0) -> VolleyMotion:
    """Movimiento de mentira para fijar cifras.

    Una bola por `(fotogramas, rebotes, choques)`, con opcionalmente su bolsillo y los
    fotogramas de retraso al final.
    """
    balls = []
    for index, (frames, bounces, collisions, *extra) in enumerate(falls):
        balls.append(
            BallMotion(
                pocket=extra[0] if extra else pocket,
                start=Start(100.0, 0.0),
                launch=index * 3,
                points=((100.0, 170.0),) * frames,
                landing=index * 3 + frames,
                bounces=bounces,
                collisions=collisions,
                delay=extra[1] if len(extra) > 1 else 0,
            )
        )
    return VolleyMotion(tuple(balls))


WHEN = datetime(2026, 1, 1, 12, tzinfo=TIMEZONE)


def stats_of(volley: Volley, motion: VolleyMotion):  # noqa: ANN201
    return pachinko_stats(
        volley, motion=motion, stake=100, won=0, turbo=False, session_volleys=1, when=WHEN
    )


def test_estadisticas_de_rebotes_y_duracion_de_una_tanda_fija() -> None:
    """Tres bolas con cifras conocidas (limpia, lenta y rápida) y siete normales."""
    clean = (30, PACHINKO_CLEAN_BOUNCES, 0)
    slow = (PACHINKO_SLOW_FRAMES + 1, 12, 0)
    swift = (PACHINKO_SWIFT_FRAMES - 1, 9, 0)
    motion = fake_motion(clean, slow, swift, *[(30, 11, 0)] * 7)
    delta = stats_of(BLANK, motion)
    assert delta.add["pachinko_bounces"] == PACHINKO_CLEAN_BOUNCES + 12 + 9 + 77
    assert delta.peak["pachinko_bounce_volley_max"] == delta.add["pachinko_bounces"]
    assert delta.peak["pachinko_bounce_max"] == 12
    assert delta.add["pachinko_slow_balls"] == 1
    assert delta.add["pachinko_swift_balls"] == 1
    assert delta.add["pachinko_clean_balls"] == 1


def test_las_cifras_de_la_duracion_se_cuentan_en_su_limite() -> None:
    motion = fake_motion(
        (PACHINKO_SLOW_FRAMES, 11, 0),
        (PACHINKO_SLOW_FRAMES - 1, 11, 0),
        (PACHINKO_SWIFT_FRAMES, 11, 0),
        (PACHINKO_SWIFT_FRAMES + 1, 11, 0),
        (30, PACHINKO_CLEAN_BOUNCES + 1, 0),
    )
    delta = stats_of(BLANK, motion)
    assert delta.add["pachinko_slow_balls"] == 1
    assert delta.add["pachinko_swift_balls"] == 1
    assert "pachinko_clean_balls" not in delta.add


def test_estadisticas_de_choques_de_una_tanda_fija() -> None:
    """Seis bolas chocan (cuatro choques) y dos de ellas acaban en las esquinas de Sakura."""
    rows = SAKURA.rows
    falls = [
        (30, 11, 2), (30, 11, 2), (30, 11, 1), (30, 11, 1), (30, 11, 0), (30, 11, 0),
        (30, 11, 1, 0), (30, 11, 1, rows), (30, 11, 0, 0, 6), (30, 11, 0, 3, 3),
    ]  # fmt: skip
    delta = stats_of(blank(SAKURA), fake_motion(*falls, pocket=3))
    assert delta.add["pachinko_hits"] == 4  # cada choque cuenta una vez aunque lo sufran dos
    assert delta.peak["pachinko_hits_max"] == 4
    assert delta.peak["pachinko_hit_ball_max"] == 2
    assert delta.peak["pachinko_balls_hit_max"] == 6
    assert delta.add["pachinko_hit_corner"] == 2  # la de la esquina sin choque no cuenta
    assert delta.add["pachinko_delayed"] == 2
    assert "pachinko_no_hits" not in delta.add


def test_una_tanda_sin_choques_cuenta_como_tranquila() -> None:
    delta = stats_of(BLANK, fake_motion(*[(30, 11, 0)] * 10))
    assert delta.add["pachinko_no_hits"] == 1
    assert "pachinko_hits" not in delta.add
    assert "pachinko_hit_corner" not in delta.add
    assert delta.peak["pachinko_hits_max"] == 0


def test_las_estadisticas_salen_del_movimiento_real_con_choques() -> None:
    """Con la tanda de choques de `test_pachinko_motion`, los contadores son los del movimiento."""
    volley = build_volley(
        CLASSIC,
        [
            Ball((0, 1, 1, 0, 0, 0, 0, 0, 0, 0), 12),
            Ball((0, 1, 1, 1, 1, 1, 0, 1, 0, 1), 7),
            Ball((0, 1, 1, 1, 1, 0, 1, 0, 0, 1), 19),
            Ball((1, 0, 0, 1, 0, 0, 1, 0, 0, 0), 17),
            Ball((0, 1, 0, 0, 1, 1, 1, 1, 1, 0), 5),
            Ball((0, 1, 1, 1, 0, 1, 0, 1, 1, 0), 16),
            Ball((0, 1, 0, 0, 1, 1, 0, 1, 0, 1), 8),
            Ball((1, 1, 1, 0, 1, 0, 0, 0, 0, 0), 9),
            Ball((0, 0, 1, 1, 0, 1, 0, 0, 1, 1), 13),
            Ball((1, 0, 0, 1, 0, 0, 0, 0, 1, 0), 16),
        ],
        lambda: MISS,
    )
    motion = motion_for(volley)
    delta = stats_of(volley, motion)
    assert motion.collisions > 0
    assert delta.add["pachinko_bounces"] == motion.bounces
    assert delta.add["pachinko_hits"] == motion.collisions
    assert delta.peak["pachinko_hits_max"] == motion.collisions
    assert delta.peak["pachinko_hit_ball_max"] == max(b.collisions for b in motion.balls)
    assert "pachinko_clean_volleys" not in delta.add


def test_los_rebotes_no_dependen_de_lo_que_paga_la_tanda() -> None:
    """La caída solo se ve: con las mismas bolas, dos sorteos cuentan los mismos rebotes."""
    balls = [Ball((1,) * 5 + (0,) * 5, index) for index in range(10)]
    when = datetime(2026, 1, 1, 12, tzinfo=TIMEZONE)
    miss_volley = build_volley(CLASSIC, balls, lambda: MISS)
    rush_volley = build_volley(CLASSIC, balls, lambda: RUSH.draws[0])
    miss = pachinko_stats(
        miss_volley, motion=motion_for(miss_volley),
        stake=100, won=0, turbo=False, session_volleys=1, when=when,
    )  # fmt: skip
    rush = pachinko_stats(
        rush_volley, motion=motion_for(rush_volley),
        stake=100, won=5_000, turbo=False, session_volleys=1, when=when,
    )  # fmt: skip
    assert miss.add["pachinko_bounces"] == rush.add["pachinko_bounces"]
    assert miss.peak["pachinko_bounce_max"] == rush.peak["pachinko_bounce_max"]


# -- ▶️ Auto ------------------------------------------------------------------------

#: Diez bolas en un bolsillo que devuelve 1 bola cada una: ni gana ni pierde.
EVEN = build_volley(CLASSIC, [ball_in(2)] * 10, lambda: MISS)
#: Un rush de 5 premios gordos (de los que se anuncian en el canal).
LONG_RUSH = build_volley(
    CLASSIC,
    [ball_in(START_POCKET)] + [ball_in(3)] * 9,
    lambda: Draw((3, 3, 3), Kind.RUSH, True, 5),
)


@pytest.fixture
def fast_gap(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Sin espera entre tandas; devuelve las esperas pedidas a `asyncio.sleep`."""
    waited: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay: float, *args: object) -> None:
        waited.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return waited


def autoplay_view(cog: Pachinko, stake: int = 10, board_key: str | None = None) -> PachinkoView:
    """Máquina con un mensaje normal (`.pachinko`): se edita con `message.edit`, sin token."""
    view = PachinkoView(cog, guild_id=GUILD_ID, owner=make_user(), stake=stake, board_key=board_key)
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    view.message = MagicMock()
    view.message.channel = channel
    view.message.edit = AsyncMock()
    return view


async def run_autoplay(view: PachinkoView, interaction: MagicMock | None = None) -> MagicMock:
    """Pulsa ▶️ Auto y espera a que acabe la sesión. Devuelve la interacción del clic."""
    interaction = interaction or make_interaction()
    await view._autoplay_click(interaction)
    assert view.autoplay is not None
    await view.autoplay.task  # type: ignore[arg-type]
    return interaction


def edited_names(view: PachinkoView) -> list[list[str]]:
    return [
        [file.filename for file in call.kwargs["attachments"]]
        for call in view.message.edit.await_args_list
        if "attachments" in call.kwargs
    ]


def last_description(view: PachinkoView) -> str:
    return view.message.edit.await_args.kwargs["embed"].description


async def test_auto_encadena_tandas_con_animacion_hasta_el_tope(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    cog = await make_cog(tmp_path, [EVEN] * AUTOPLAY_MAX)
    view = autoplay_view(cog, stake=100)

    interaction = await run_autoplay(view)

    assert view.session_volleys == AUTOPLAY_MAX
    # Cada tanda: el GIF y, después, el PNG final; y al cerrar, el resumen sin imagen.
    assert edited_names(view) == [[GIF_NAME], [PNG_NAME]] * AUTOPLAY_MAX
    assert view.message.edit.await_count == 2 * AUTOPLAY_MAX + 1
    assert "25 tandas" in last_description(view)
    assert "tope de 25 tandas" in last_description(view)
    assert "tirada" not in last_description(view)
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE
    # Contesta al clic una sola vez y nunca edita con el token de la interacción.
    interaction.response.defer.assert_awaited_once()
    interaction.edit_original_response.assert_not_awaited()
    assert view.autoplay is None
    assert not view._busy
    assert not any(item.disabled for item in view.children)  # type: ignore[attr-defined]
    assert view.autoplay_button.label == "▶️ Auto"
    assert view.timeout == pachinko_module.MACHINE_TIMEOUT


async def test_auto_cuenta_las_tandas_en_el_texto_de_cada_una(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    cog = await make_cog(tmp_path, [EVEN] * AUTOPLAY_MAX)
    view = autoplay_view(cog, stake=100)
    seen: list[str] = []

    async def spy(**kwargs: object) -> None:
        if "embed" in kwargs and "Auto ·" in kwargs["embed"].description:  # type: ignore[attr-defined]
            seen.append(kwargs["embed"].description)  # type: ignore[attr-defined]

    view.message.edit.side_effect = spy
    await run_autoplay(view)

    assert any("tanda 1/25" in text and "neto ±0 Y$" in text for text in seen)
    assert any("tanda 25/25" in text for text in seen)


async def test_auto_cobra_cada_tanda_una_vez(tmp_path: Path, fast_gap: list[float]) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=100)

    await run_autoplay(view)

    # Todo pierde: para al perder 10 apuestas, y el saldo cuadra con las tandas.
    assert view.session_volleys == 10
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 10 * 100
    assert ledger_sum(tmp_path, OWNER_ID) == await cog.economy.balance(GUILD_ID, OWNER_ID)
    assert "Techo de gasto" in last_description(view)


async def test_auto_en_turbo_solo_manda_la_imagen_final_y_deja_el_intervalo(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=100)
    view.turbo = True

    await run_autoplay(view)

    assert set(map(tuple, edited_names(view))) == {(PNG_NAME,)}
    assert view.message.edit.await_count == view.session_volleys + 1
    assert fast_gap  # esperó entre tandas
    assert all(0 < wait <= AUTOPLAY_MIN_GAP for wait in fast_gap)


async def test_auto_para_cuando_no_llega_el_saldo(tmp_path: Path, fast_gap: list[float]) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=300)

    await run_autoplay(view)

    assert view.session_volleys == 3
    assert "no te llega para más tandas" in last_description(view)
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 900


async def test_auto_sin_saldo_desde_el_principio_avisa_en_privado_y_no_edita(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=STARTING_BALANCE + 10)

    interaction = await run_autoplay(view)

    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
    view.message.edit.assert_not_awaited()
    assert view.session_volleys == 0
    assert not view._busy
    assert view.autoplay is None
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE


async def test_auto_para_con_un_atari_lo_anuncia_y_deja_ver_la_tanda(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    cog = await make_cog(tmp_path, [BLANK, LONG_RUSH])
    view = autoplay_view(cog, stake=10)

    await run_autoplay(view)

    assert view.session_volleys == 2
    assert "premio gordo" in last_description(view)
    assert "3 3 3" in last_description(view)  # el resumen no tapa la tanda
    view.message.channel.send.assert_awaited_once()
    assert (
        "ATARI" in view.message.channel.send.await_args.args[0]
        or "RUSH" in (view.message.channel.send.await_args.args[0])
    )


async def test_auto_para_con_cualquier_atari_aunque_sea_corto(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    cog = await make_cog(tmp_path, [BLANK, BLANK, RUSH])
    view = autoplay_view(cog, stake=10)

    await run_autoplay(view)

    assert view.session_volleys == 3
    assert "premio gordo" in last_description(view)
    view.message.channel.send.assert_not_awaited()  # un rush de 4 no se anuncia


async def test_auto_para_con_un_atari_sorteado_por_el_azar(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    """Con el sorteo forzado (todas las bolas por START y todo sale), la primera tanda es atari."""
    calls = 0

    def forced(n: int) -> int:
        nonlocal calls
        if n == 2:  # cara, cruz, cara, cruz…: cada bola acaba en el bolsillo del centro
            calls += 1
            return calls % 2
        return 0

    cog = await make_cog(tmp_path, machine=PachinkoMachine(forced))
    view = autoplay_view(cog, stake=10, board_key="clasica")

    await run_autoplay(view)

    assert view.session_volleys == 1
    assert "premio gordo" in last_description(view)
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) > STARTING_BALANCE


async def test_auto_juega_cada_tanda_al_azar_en_un_tablero_distinto(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    cog = await make_cog(tmp_path)
    picks = iter([0, 3, 1, 2, 0, 1, 2, 3, 0, 1])
    cog.machine._randbelow = lambda n: next(picks)  # type: ignore[attr-defined]
    view = autoplay_view(cog, stake=100, board_key=RANDOM_BOARD)

    await run_autoplay(view)

    assert cog.machine.boards[:5] == ["sakura", "oni", "clasica", "dragon", "sakura"]
    assert "Al azar" in view.message.edit.await_args_list[1].kwargs["embed"].title


async def test_parar_contesta_sin_ack_y_la_tanda_en_curso_acaba(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=10)
    events: list[str] = []
    stop_click = fake_interaction(make_user(), events=events)
    pressed = False

    async def press_stop_in_third_volley(**kwargs: object) -> None:
        nonlocal pressed
        if view.session_volleys == 3 and not pressed:
            pressed = True
            await view._autoplay_click(stop_click)

    view.message.edit.side_effect = press_stop_in_third_volley

    await run_autoplay(view)

    assert events == ["response.edit_message"]  # directa: sin ack, sin base de datos
    stop_click.response.defer.assert_not_awaited()
    assert stop_click.response.edit_message.await_args.kwargs["view"] is view
    # La tercera tanda acabó entera (GIF y PNG) y la cuarta no se jugó.
    assert view.session_volleys == 3
    assert "Parado a mano" in last_description(view)
    assert edited_names(view)[-2:] == [[GIF_NAME], [PNG_NAME]]
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 30


async def test_mientras_corre_solo_se_puede_pulsar_parar(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=10)
    seen: list[dict[str, bool]] = []

    async def spy(**kwargs: object) -> None:
        seen.append({getattr(item, "label", "menú"): item.disabled for item in view.children})

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
    assert view.session_volleys == 10
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 10 * 100


async def test_lanzar_y_rafaga_mientras_corre_el_auto_se_rechazan(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=100)
    launch, burst = make_interaction(), make_interaction()
    tried = False

    async def try_to_play(**kwargs: object) -> None:
        nonlocal tried
        if not tried:
            tried = True
            await view._launch(launch)
            await view._burst(burst)

    view.message.edit.side_effect = try_to_play

    await run_autoplay(view)

    for extra in (launch, burst):
        extra.response.defer.assert_awaited_once()
        extra.edit_original_response.assert_not_awaited()
    assert view.session_volleys == 10
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 10 * 100


async def test_auto_mientras_hay_una_tanda_en_curso_no_abre_otra_sesion(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=100)
    view._busy = True  # p. ej. una tanda de Lanzar en curso
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
    assert view.session_volleys == 10


async def test_al_descargar_el_cog_el_auto_acaba_la_tanda_y_se_cierra(
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
    assert task.get_name().startswith("pachinko-autoplay-")
    await in_gap.wait()

    await cog.cog_unload()

    assert task.done()
    assert view.autoplay is None
    assert not view._busy
    assert view.session_volleys == 1
    assert not cog.machines


async def test_al_caducar_la_vista_el_auto_se_detiene_limpio(
    tmp_path: Path, fast_gap: list[float]
) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=10)
    release = asyncio.Event()

    async def hold_in_first_volley(**kwargs: object) -> None:
        await release.wait()

    view.message.edit.side_effect = hold_in_first_volley
    await view._autoplay_click(make_interaction())
    task = view.autoplay.task  # type: ignore[union-attr]
    await asyncio.sleep(0)
    closing = asyncio.create_task(view.on_timeout())
    await asyncio.sleep(0)
    release.set()
    await closing

    assert task.done()
    assert view.autoplay is None
    assert view.session_volleys < AUTOPLAY_MAX


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

    # La tanda ya estaba cobrada y se cuenta; la siguiente no se juega a ciegas.
    assert view.session_volleys == 1
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 10
    assert not view._busy


async def test_auto_avisa_de_la_renta_una_sola_vez_por_sesion(
    tmp_path: Path, fast_gap: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=100)
    remind = AsyncMock()
    monkeypatch.setattr(pachinko_module.renta, "remind", remind)

    interaction = await run_autoplay(view)

    assert view.session_volleys == 10
    remind.assert_awaited_once_with(cog.bot, interaction)


async def test_auto_apunta_los_logros_de_cada_tanda_y_de_la_sesion(
    tmp_path: Path, fast_gap: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=100)
    casino_play = AsyncMock()
    track = AsyncMock()
    record = AsyncMock()
    monkeypatch.setattr(pachinko_module.logros, "casino_play", casino_play)
    monkeypatch.setattr(pachinko_module.logros, "track", track)
    monkeypatch.setattr(pachinko_module.apuestas, "record", record)

    await run_autoplay(view)

    assert casino_play.await_count == record.await_count == 10
    for call in casino_play.await_args_list:
        assert call.args[4].add["pachinko_autoplay_volleys"] == 1
    session = track.await_args.args[4]
    assert session.add["pachinko_autoplay_sessions"] == 1
    assert session.add["pachinko_autoplay_loss_limit"] == 1


async def test_lanzar_no_cuenta_como_auto_en_los_logros(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog = await make_cog(tmp_path)
    casino_play = AsyncMock()
    monkeypatch.setattr(pachinko_module.logros, "casino_play", casino_play)

    await make_view(cog)._launch(make_interaction())

    assert "pachinko_autoplay_volleys" not in casino_play.await_args.args[4].add


async def test_auto_saca_la_pista_de_la_mascota_en_cada_tanda(
    tmp_path: Path, fast_gap: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    cog = await make_cog(tmp_path)
    view = autoplay_view(cog, stake=100)
    hint = AsyncMock(return_value="🐶 guau")
    monkeypatch.setattr(pachinko_module.renta, "hint", hint)

    await run_autoplay(view)

    assert hint.await_count == view.session_volleys == 10


async def test_los_botones_nuevos_caben_en_la_fila_y_tienen_su_custom_id(tmp_path: Path) -> None:
    view = make_view(await make_cog(tmp_path))
    buttons = [item for item in view.children if isinstance(item, discord.ui.Button)]
    ids = {item.label: item.custom_id for item in buttons}
    assert ids[f"🔁 Ráfaga ×{BURST_VOLLEYS}"] == "pachinko:burst"
    assert ids["▶️ Auto"] == "pachinko:autoplay"
    row0 = [item for item in buttons if item.row == 0]
    assert len(row0) <= 5  # Discord admite 5 por fila
    assert [item.custom_id for item in row0[:3]] == ["pachinko:go", "pachinko:burst", ids["▶️ Auto"]]


def test_los_logros_de_sesion_segun_el_motivo() -> None:
    manual = pachinko_autoplay_stats(volleys=12, net=300, reason=StopReason.MANUAL).add
    assert manual["pachinko_autoplay_manual"] == manual["pachinko_autoplay_exit_ahead"] == 1
    assert pachinko_autoplay_stats(volleys=0, net=0, reason=StopReason.NO_FUNDS).add == {}


# -- Precarga de la tanda siguiente -------------------------------------------------


class PlaySpy:
    """Apunta el `prepared` con el que se cobra cada tanda (envuelve `Pachinko.play`)."""

    def __init__(self, cog: Pachinko) -> None:
        self.prepared: list[object] = []
        self.plays: list[PachinkoPlay] = []
        real = cog.play

        async def spy(*args: object, **kwargs: object) -> PachinkoPlay:
            self.prepared.append(kwargs.get("prepared"))
            play = await real(*args, **kwargs)  # type: ignore[arg-type]
            self.plays.append(play)
            return play

        cog.play = spy  # type: ignore[method-assign]


async def preloaded(view: PachinkoView):  # noqa: ANN201
    """Espera a que la máquina acabe de preparar la tanda siguiente y la devuelve."""
    assert view._preload is not None
    return await view._preload.task


async def test_tras_lanzar_se_prepara_la_siguiente_sin_mover_dinero(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [BLANK, RUSH], preload=True)
    view = make_view(cog)

    await view._launch(make_interaction())
    after_first = await cog.economy.balance(GUILD_ID, OWNER_ID)
    prepared = await preloaded(view)

    assert prepared.volley is RUSH  # ya sorteada y dibujada...
    assert prepared.media.gif == b"GIF"
    assert RUSH in cog.renderer.rendered
    # ...pero nadie ha pagado ni cobrado nada por ella.
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == after_first == STARTING_BALANCE - 100
    assert ledger_sum(tmp_path, OWNER_ID) == after_first
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) == 0


async def test_el_segundo_lanzar_usa_la_tanda_precargada_y_no_la_dibuja_otra_vez(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, [BLANK, RUSH], preload=True)
    spy = PlaySpy(cog)
    view = make_view(cog)
    await view._launch(make_interaction())
    prepared = await preloaded(view)
    drawn_before = len(cog.renderer.rendered)

    interaction = make_interaction()
    await view._launch(interaction)

    assert spy.prepared[0] is None and spy.prepared[1] is prepared
    assert spy.plays[1].volley is RUSH and spy.plays[1].media is prepared.media
    assert RUSH in cog.renderer.rendered and cog.renderer.rendered.count(RUSH) == 1
    # El único dibujo nuevo es el de la tanda siguiente (la tercera), no el de esta.
    await preloaded(view)
    assert len(cog.renderer.rendered) == drawn_before + 1
    # Se cobró lo del rush y se enseñó el GIF precargado.
    won = 100 * 4 * FEVER_BALLS // 10
    treasury = (await cog.economy.treasury(GUILD_ID, since=0)).balance
    balance = await cog.economy.balance(GUILD_ID, OWNER_ID)
    assert balance + treasury == STARTING_BALANCE - 200 + won
    assert attachment_names(interaction.edit_original_response.await_args_list[0]) == [GIF_NAME]
    assert "5 5 5" in view.last_text


async def test_si_la_precarga_no_esta_lista_se_espera_a_ella_y_no_se_lanza_otra(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, [BLANK, RUSH, BLANK], preload=True)
    spy = PlaySpy(cog)
    view = make_view(cog)
    await view._launch(make_interaction())
    await preloaded(view)  # la tanda 2 ya está lista; la siguiente se retiene en el dibujo
    cog.renderer.gate = threading.Event()
    await view._launch(make_interaction())  # consume la lista y pide la tercera
    await asyncio.sleep(0.1)
    assert len(cog.machine.boards) == 3  # la tercera ya está sorteada, pero sin dibujar

    waiting = asyncio.create_task(view._launch(make_interaction()))
    await asyncio.sleep(0.1)
    assert not waiting.done()  # espera al dibujo en marcha...
    assert len(cog.machine.boards) == 3  # ...y no sortea otra tanda
    cog.renderer.gate.set()
    await waiting

    assert spy.prepared[2] is not None
    assert spy.plays[2].volley.draws == ()  # la BLANK que tenía en marcha
    await preloaded(view)
    assert len(cog.machine.boards) == 4  # solo la siguiente, preparada tras este clic


async def test_cambiar_de_tablero_invalida_la_precarga(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, preload=True)
    spy = PlaySpy(cog)
    view = make_view(cog)
    await view._launch(make_interaction())
    old = view._preload
    assert old is not None
    await old.task

    view.board_select._values = ["oni"]
    await view._choose_board(make_interaction())
    assert view._preload is None

    await view._launch(make_interaction())
    assert spy.prepared == [None, None]  # la de la Clásica se tiró: esta se sorteó en el clic
    assert cog.machine.boards[:3] == ["clasica", "clasica", "oni"]
    assert view._preload is not None and view._preload.board_key == "oni"


async def test_cambiar_de_turbo_invalida_la_precarga(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, preload=True)
    spy = PlaySpy(cog)
    view = make_view(cog)
    await view._launch(make_interaction())
    await preloaded(view)

    await view._toggle_turbo(make_interaction())
    assert view._preload is None
    interaction = make_interaction()
    await view._launch(interaction)

    assert spy.prepared == [None, None]
    assert attachment_names(interaction.edit_original_response.await_args) == [PNG_NAME]
    interaction.edit_original_response.assert_awaited_once()  # turbo: sin GIF
    assert view._preload is not None and view._preload.turbo is True


async def test_un_turbo_distinto_se_descarta_aunque_nadie_pasara_por_el_boton(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, preload=True)
    spy = PlaySpy(cog)
    view = make_view(cog)
    await view._launch(make_interaction())
    await preloaded(view)
    view.turbo = True  # p. ej. el turbo cambiado por otra vía

    await view._launch(make_interaction())

    assert spy.prepared == [None, None]


async def test_cambiar_la_apuesta_no_invalida_la_precarga(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, [BLANK, RUSH], preload=True)
    spy = PlaySpy(cog)
    view = make_view(cog, stake=100)
    await view._launch(make_interaction())
    prepared = await preloaded(view)

    await view._double_stake(make_interaction())
    assert view.stake == 200
    await view._launch(make_interaction())

    assert spy.prepared[1] is prepared
    assert spy.plays[1].stake == 200
    assert spy.plays[1].won == 200 * 4 * FEVER_BALLS // 10  # se paga con la apuesta del clic


async def test_la_precarga_con_al_azar_ya_trae_el_tablero_sorteado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, preload=True)
    spy = PlaySpy(cog)
    view = PachinkoView(
        cog, guild_id=GUILD_ID, owner=make_user(), stake=100, board_key=RANDOM_BOARD
    )
    picks = iter([1, 3])
    cog.machine._randbelow = lambda n: next(picks)  # type: ignore[attr-defined]

    await view._launch(make_interaction())
    prepared = await preloaded(view)
    await view._launch(make_interaction())

    assert [p.volley.board.key for p in spy.plays] == ["clasica", "oni"]
    assert prepared.board_key == RANDOM_BOARD and prepared.volley.board.key == "oni"
    assert spy.prepared[1] is prepared


async def test_la_precarga_se_prepara_fuera_del_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    threads: list[str] = []
    real = pachinko_module.motion_for

    def spy(volley: Volley) -> VolleyMotion:
        threads.append(threading.current_thread().name)
        return real(volley)

    monkeypatch.setattr(pachinko_module, "motion_for", spy)
    cog = await make_cog(tmp_path, preload=True)
    await cog.prepare("clasica", turbo=False)
    assert threads and threading.main_thread().name not in threads


async def test_hay_una_sola_precarga_por_maquina_con_tarea_con_nombre(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, preload=True)
    view = make_view(cog)
    await view._launch(make_interaction())
    first = view._preload
    assert first is not None and first.task.get_name() == f"pachinko-preload-{GUILD_ID}-{OWNER_ID}"

    view._start_preload()  # ya hay una que vale: no se lanza otra
    assert view._preload is first
    assert len(cog.machine.boards) <= 2


async def test_la_rafaga_no_usa_ni_gasta_la_precarga(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, preload=True)
    spy = PlaySpy(cog)
    view = make_view(cog, stake=10)
    await view._launch(make_interaction())
    prepared = await preloaded(view)

    await view._burst(make_interaction())

    assert spy.prepared[1:] == [None] * BURST_VOLLEYS
    assert view._preload is not None and view._preload.task.result() is prepared
    await view._launch(make_interaction())
    assert spy.prepared[-1] is prepared  # el siguiente Lanzar sí la usa


async def test_si_no_se_pulsa_nada_no_se_mueve_dinero_y_al_cerrar_se_cancela(
    tmp_path: Path,
) -> None:
    cog = await make_cog(tmp_path, preload=True)
    view = make_view(cog)
    await view._launch(make_interaction())
    task = view._preload.task  # type: ignore[union-attr]
    balance = await cog.economy.balance(GUILD_ID, OWNER_ID)

    await view.on_timeout()
    await asyncio.wait({task}, timeout=5)

    assert view._preload is None
    assert task.done()  # cancelada, o ya terminada si el dibujo falso fue más rápido
    view._start_preload()  # una máquina cerrada no vuelve a precargar
    assert view._preload is None
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == balance
    assert ledger_sum(tmp_path, OWNER_ID) == balance


async def test_al_descargar_el_cog_se_cancelan_las_precargas(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, preload=True)
    cog.renderer.gate = threading.Event()
    view = make_view(cog)
    cog.machines.add(view)
    view._start_preload()
    task = view._preload.task  # type: ignore[union-attr]
    await asyncio.sleep(0.05)  # el dibujo sigue retenido en su hilo
    assert not task.done()

    await cog.cog_unload()
    cog.renderer.gate.set()
    await asyncio.wait({task}, timeout=5)

    assert task.cancelled()
    assert view._preload is None
    assert not cog.machines


async def test_si_la_precarga_falla_se_juega_sin_ella(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    cog = await make_cog(tmp_path, preload=True)
    spy = PlaySpy(cog)
    view = make_view(cog)
    real = cog.renderer.render
    broken = True

    def render(volley, *, turbo=False, motion=None):  # noqa: ANN001, ANN202
        if broken and len(cog.renderer.rendered) >= 1:
            raise RuntimeError("Pillow se ha quedado sin memoria")
        return real(volley, turbo=turbo, motion=motion)

    monkeypatch.setattr(cog.renderer, "render", render)
    with caplog.at_level(logging.WARNING, logger=pachinko_module.__name__):
        await view._launch(make_interaction())
        with pytest.raises(RuntimeError):
            await preloaded(view)
        interaction = make_interaction()
        broken = False
        await view._launch(interaction)

    assert spy.prepared == [None, None]  # la fallida no se usó: se sorteó y dibujó en el clic
    assert any("precarga" in record.message for record in caplog.records)
    assert attachment_names(interaction.edit_original_response.await_args) == [PNG_NAME]
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 200


async def test_si_el_saldo_no_llega_la_precarga_no_cobra_nada(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, preload=True)
    view = make_view(cog)
    await view._launch(make_interaction())
    await preloaded(view)
    view.stake = STARTING_BALANCE * 2
    interaction = make_interaction()

    await view._launch(interaction)

    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
    assert await cog.economy.balance(GUILD_ID, OWNER_ID) == STARTING_BALANCE - 100


async def test_el_auto_usa_la_precarga_en_cada_paso(tmp_path: Path, fast_gap: list[float]) -> None:
    cog = await make_cog(tmp_path, [EVEN] * 4, preload=True)
    spy = PlaySpy(cog)
    view = autoplay_view(cog, stake=100)
    # La quinta tanda es un atari: la sesión es corta y exacta.
    cog.machine.sequence.append(RUSH)

    await run_autoplay(view)

    assert view.session_volleys == 5
    assert spy.prepared[0] is None
    assert all(item is not None for item in spy.prepared[1:])
    assert [p.volley for p in spy.plays] == [EVEN] * 4 + [RUSH]
    # Cobra una vez cada tanda y solo dibuja una vez cada una (en su precarga).
    assert len(cog.renderer.rendered) >= 5
    assert cog.renderer.rendered.count(RUSH) == 1


async def test_la_precarga_no_cuenta_en_los_logros_hasta_que_se_juega(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog = await make_cog(tmp_path, [BLANK, RUSH], preload=True)
    casino_play = AsyncMock()
    monkeypatch.setattr(pachinko_module.logros, "casino_play", casino_play)
    view = make_view(cog)

    await view._launch(make_interaction())
    await preloaded(view)
    assert casino_play.await_count == 1  # solo la tanda jugada

    await view._launch(make_interaction())
    assert casino_play.await_count == 2
