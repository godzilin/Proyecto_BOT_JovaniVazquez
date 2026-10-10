"""Pruebas de bot.cogs.chicken: carretera con botones, animación y dinero.

Se usa la economía real sobre un SQLite temporal, un dibujante falso (las
imágenes tienen sus propias pruebas) y el carril del atropello fijado a mano.
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

from bot.cogs import chicken as chicken_cog
from bot.cogs.chicken import (
    COLOR_PLAYING,
    Chicken,
    ChickenView,
    near_miss,
    parse_auto,
)
from bot.repositories.economy import EconomyRepository
from bot.services.achievements import chicken_stats
from bot.services.chicken import (
    DIFFICULTY_BY_KEY,
    ChickenGame,
    Status,
    lanes_for_target,
    multiplier_cents,
    payout,
)
from bot.services.chicken_render import Media
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.taxes import gambling_day_tax

GUILD_ID = 1
OWNER_ID = 10
MEDIA = DIFFICULTY_BY_KEY["media"]


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chicken_cog, "REVEAL_MARGIN_SECONDS", 0)


def make_user(user_id: int = OWNER_ID) -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = "Diego"
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


def make_interaction(user_id: int = OWNER_ID) -> MagicMock:
    return fake_interaction(make_user(user_id))


def fake_renderer() -> MagicMock:
    renderer = MagicMock()
    renderer.hops = AsyncMock(return_value=Media(gif=b"GIF89a", png=b"png", seconds=0.0))
    renderer.board = AsyncMock(return_value=b"png")
    renderer.start = AsyncMock(return_value=b"png")
    renderer.close = AsyncMock()
    return renderer


async def make_cog(tmp_path: Path) -> Chicken:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    return Chicken(
        MagicMock(),
        economy=EconomyService(repository),
        rng=random.Random(7),
        renderer=fake_renderer(),
    )


async def open_road(
    cog: Chicken, *, amount: str = "100", hit: int | None = None, auto: int | None = None
) -> ChickenView:
    send = AsyncMock(return_value=MagicMock())
    errors = AsyncMock()
    await cog._pollo_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(),
        amount_text=amount,
        difficulty=MEDIA,
        auto=auto,
        send=send,
        send_error=errors,
    )
    errors.assert_not_awaited()
    send.assert_awaited_once()
    assert send.await_args.kwargs["file"].filename == "pollo.png"
    (view,) = cog.views
    assert view.game is not None
    view.game.hit_lane = hit
    return view


def labels(view: ChickenView) -> list[str]:
    return [item.label or "" for item in view.children if isinstance(item, ui.Button)]


async def balance(cog: Chicken) -> int:
    return await cog.economy.balance(GUILD_ID, OWNER_ID)


async def test_pollo_cobra_la_apuesta_y_ensena_cruzar_y_cobrar(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = await open_road(cog)
    assert await balance(cog) == STARTING_BALANCE - 100
    names = labels(view)
    assert names[0].startswith("🐔 Cruzar · ×1,12")
    assert names[1] == "💰 Cobrar"
    # Desde la acera no se puede cobrar, y en plena partida no hay menús.
    cash = next(b for b in view.children if isinstance(b, ui.Button) and b.label == "💰 Cobrar")
    assert cash.disabled
    assert not [i for i in view.children if isinstance(i, ui.Select)]


async def test_cruzar_ensena_gif_y_luego_png_sin_destripar(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = await open_road(cog, hit=2)
    first = make_interaction()
    states: list[list[bool]] = []

    async def capture(**kwargs: object) -> None:
        # La vista es el mismo objeto en las dos ediciones: se mira al momento.
        states.append([b.disabled for b in view.children if isinstance(b, ui.Button)])

    first.edit_original_response.side_effect = capture
    await view._cross(first)
    first.response.defer.assert_awaited_once()
    assert view.game is not None and view.game.crossed == 1
    wait_call, gif_call, png_call = first.edit_original_response.await_args_list
    # Mientras se pinta, la carretera se apaga sin adjuntos nuevos.
    assert "attachments" not in wait_call.kwargs
    assert gif_call.kwargs["attachments"][0].filename == "pollo.gif"
    assert png_call.kwargs["attachments"][0].filename == "pollo.png"
    # Mientras se pinta y durante el GIF todo está apagado; después, Cruzar y Cobrar vuelven.
    assert all(states[0]) and all(states[1]) and not any(states[2])

    second = make_interaction()
    await view._cross(second)
    assert view.game.status is Status.SPLAT
    waiting = second.edit_original_response.await_args_list[1].kwargs["embed"]
    # Mientras se ve el GIF ni el texto ni el color dicen cómo acaba.
    assert "PLAF" not in (waiting.description or "")
    assert "atropellado" not in (waiting.description or "")
    assert waiting.color == COLOR_PLAYING
    final = second.edit_original_response.await_args_list[2].kwargs["embed"]
    assert "atropellado" in (final.description or "")
    assert await balance(cog) == STARTING_BALANCE - 100


async def test_cobrar_paga_y_dice_donde_estaba_el_coche(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = await open_road(cog, amount="1000", hit=6)
    for _ in range(3):
        await view._cross(make_interaction())
    interaction = make_interaction()
    await view._cash_out(interaction)
    assert view.game is not None and view.game.status is Status.CASHED
    prize = payout(1_000, MEDIA, 3)
    assert await balance(cog) == STARTING_BALANCE - 1_000 + prize
    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    assert "carril 6" in (embed.description or "")
    # Al acabar vuelven 🔁, la apuesta y los dos menús.
    assert labels(view)[0] == "🔁 Jugar · 1.000 Y$"
    assert len([i for i in view.children if isinstance(i, ui.Select)]) == 2


async def test_autocobro_cruza_y_cobra_solo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = await open_road(cog, amount="1000", hit=None, auto=300)
    assert any(name.startswith("🎯 Hasta ×3,00") for name in labels(view))
    await view._auto_run(make_interaction())
    game = view.game
    assert game is not None and game.status is Status.CASHED
    assert game.crossed == lanes_for_target(MEDIA, 300)
    assert game.cents >= 300
    withheld = gambling_day_tax(game.payout - 1_000, 0)
    assert await balance(cog) == STARTING_BALANCE - 1_000 + game.payout - withheld
    cog.renderer.hops.assert_awaited_once()
    assert cog.renderer.hops.call_args.kwargs["start"] == 0


async def test_jugar_con_autocobro_desde_la_acera(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = await open_road(cog, hit=1, auto=200)
    await view._cross(make_interaction())  # atropellado en el primero
    assert "🎯 Auto ×2,00" in labels(view)
    interaction = make_interaction()
    await view._again_auto(interaction)
    game = view.game
    assert game is not None and not game.playing
    # Apagar, GIF y resultado.
    assert interaction.edit_original_response.await_count == 3


async def test_llegar_a_la_meta_cobra_el_premio_gordo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = await open_road(cog, hit=None)
    for _ in range(MEDIA.lanes):
        await view._cross(make_interaction())
    game = view.game
    assert game is not None and game.finished_road and game.status is Status.CASHED
    assert game.cents == multiplier_cents(MEDIA, MEDIA.lanes)


async def test_caducar_desde_la_acera_devuelve_la_apuesta(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = await open_road(cog)
    await view.force_settle()
    assert await balance(cog) == STARTING_BALANCE
    assert view.game is None


async def test_caducar_a_medias_cobra(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = await open_road(cog, amount="1000", hit=None)
    await view._cross(make_interaction())
    await cog.cog_unload()
    assert await balance(cog) == STARTING_BALANCE - 1_000 + payout(1_000, MEDIA, 1)


async def test_solo_el_dueno_juega(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = await open_road(cog)
    stranger = make_interaction(user_id=99)
    assert not await view.interaction_check(stranger)
    stranger.response.send_message.assert_awaited_once()


async def test_cambiar_dificultad_quita_un_autocobro_imposible(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    view = await open_road(cog, hit=1, auto=1_000)
    await view._cross(make_interaction())
    await view._choose_difficulty(make_interaction(), "facil")
    assert view.difficulty.key == "facil" and view.auto is None
    assert cog.prefs(GUILD_ID, OWNER_ID)[0].key == "facil"


async def test_autocobro_por_encima_de_la_meta_se_rechaza(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    errors = AsyncMock()
    await cog._pollo_impl(
        guild=MagicMock(id=GUILD_ID),
        channel=None,
        user=make_user(),
        amount_text="100",
        difficulty=DIFFICULTY_BY_KEY["facil"],
        auto=500,
        send=AsyncMock(),
        send_error=errors,
    )
    errors.assert_awaited_once()
    assert await balance(cog) == STARTING_BALANCE


async def test_comando_de_texto_entiende_cantidad_dificultad_y_autocobro(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path)
    cog._pollo_impl = AsyncMock()  # type: ignore[method-assign]
    ctx = MagicMock()
    ctx.typing.return_value.__aenter__ = AsyncMock()
    ctx.typing.return_value.__aexit__ = AsyncMock(return_value=False)
    await Chicken.pollo_text.callback(cog, ctx, "500", "hardcore", "x3")
    kwargs = cog._pollo_impl.await_args.kwargs
    assert kwargs["amount_text"] == "500"
    assert kwargs["difficulty"].key == "hardcore"
    assert kwargs["auto"] == 300


@pytest.mark.parametrize(
    ("text", "cents"), [("3", 300), ("x2,5", 250), ("10x", 1_000), ("×1.5", 150)]
)
def test_autocobro_escrito(text: str, cents: int) -> None:
    assert parse_auto(text) == cents


@pytest.mark.parametrize("text", ["1", "tres", "x"])
def test_autocobro_mal_escrito(text: str) -> None:
    with pytest.raises(ValueError):
        parse_auto(text)


def test_frases_del_casi() -> None:
    def cashed(hit: int | None, crossed: int) -> ChickenGame:
        game = ChickenGame(stake=100, difficulty=MEDIA, hit_lane=hit)
        game.crossed = crossed
        game.status = Status.CASHED
        return game

    assert "Por los pelos" in near_miss(cashed(3, 2), "un taxi con prisa")
    assert "carril 4" in near_miss(cashed(4, 2), "un taxi con prisa")
    assert "5 carriles libres" in near_miss(cashed(9, 3), "un taxi con prisa")
    assert "libre hasta la meta" in near_miss(cashed(None, 3), "un taxi con prisa")


def test_estadisticas_de_logros_del_pollo() -> None:
    splat = ChickenGame(stake=100, difficulty=DIFFICULTY_BY_KEY["hardcore"], hit_lane=1)
    splat.cross()
    delta = chicken_stats(splat, vehicle="bus")
    assert delta.add["chicken_splats"] == 1
    assert delta.add["chicken_first_splat"] == 1
    assert delta.add["chicken_hit_bus"] == 1
    assert delta.peak["chicken_lanes_max_hardcore"] == 0

    close = ChickenGame(stake=100, difficulty=MEDIA, hit_lane=2)
    close.cross()
    close.cash_out()
    delta = chicken_stats(close)
    assert delta.add["chicken_close"] == 1
    assert delta.add["chicken_gallina"] == 1
    assert delta.peak["chicken_mult_max"] == close.cents


# -- GIF por adelantado ----------------------------------------------------------------


class RecordingRenderer:
    """Dibujo falso que apunta cada GIF pedido: carriles cruzados, cómo acaba y desde dónde."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, Status, int]] = []
        self.gate: asyncio.Event | None = None

    async def hops(self, game: ChickenGame, *, start: int, seed: int, note: str = "") -> Media:
        if self.gate is not None:
            await self.gate.wait()
        self.calls.append((game.crossed, game.status, start))
        return Media(gif=f"GIF{len(self.calls)}".encode(), png=b"png", seconds=0.0)

    async def board(self, game: ChickenGame, *, seed: int, note: str = "") -> bytes:
        return b"png"

    async def start(self, difficulty: object, *, stake: int, seed: int) -> bytes:
        return b"png"

    async def close(self) -> None:
        return None


async def ahead_road(
    tmp_path: Path, *, hit: int | None, auto: int | None = None
) -> tuple[ChickenView, RecordingRenderer]:
    """Carretera con el atropello en `hit` y el dibujo por adelantado encendido después."""
    cog = await make_cog(tmp_path)
    renderer = RecordingRenderer()
    cog.renderer = renderer  # type: ignore[assignment]
    view = await open_road(cog, hit=hit, auto=auto)
    cog.ahead = True
    view.prepare()
    return view, renderer


async def settle_painting(view: ChickenView) -> None:
    while view._painting is not None and not view._painting.done():
        await asyncio.sleep(0)


def gif_of(interaction: MagicMock) -> bytes:
    for call in interaction.edit_original_response.await_args_list:
        files = call.kwargs.get("attachments") or []
        if files and files[0].filename == "pollo.gif":
            return files[0].fp.read()
    raise AssertionError("no se subió ningún GIF")


async def test_el_proximo_cruce_se_pinta_antes_y_es_el_que_sale(tmp_path: Path) -> None:
    view, renderer = await ahead_road(tmp_path, hit=3)
    await settle_painting(view)
    # Antes de pulsar: el carril 1, que es seguro, y sin cobrar nada más.
    assert renderer.calls == [(1, Status.PLAYING, 0)]
    interaction = make_interaction()
    await view._cross(interaction)
    # Sale el GIF ya pintado, sin paso intermedio: GIF y resultado.
    assert gif_of(interaction) == b"GIF1"
    assert interaction.edit_original_response.await_count == 2
    # Mientras se veía, ya se pintó el siguiente.
    await settle_painting(view)
    assert renderer.calls[-1] == (2, Status.PLAYING, 1)


async def test_el_atropello_tambien_se_pinta_antes_y_coincide(tmp_path: Path) -> None:
    view, renderer = await ahead_road(tmp_path, hit=1)
    await settle_painting(view)
    # Lo pintado es el atropello en el carril 1: no ha cruzado ninguno.
    assert renderer.calls == [(0, Status.SPLAT, 0)]
    interaction = make_interaction()
    await view._cross(interaction)
    assert view.game is not None and view.game.status is Status.SPLAT
    assert gif_of(interaction) == b"GIF1"


async def test_el_autocobro_tambien_se_pinta_antes(tmp_path: Path) -> None:
    view, renderer = await ahead_road(tmp_path, hit=None, auto=200)
    await settle_painting(view)
    # Dos GIF: cruzar uno y el autocobro entero, que acaba cobrado.
    kinds = {(crossed, status) for crossed, status, _start in renderer.calls}
    assert (1, Status.PLAYING) in kinds
    assert any(status is Status.CASHED for _crossed, status in kinds)
    interaction = make_interaction()
    await view._auto_run(interaction)
    assert view.game is not None and view.game.status is Status.CASHED
    assert gif_of(interaction) == b"GIF2"


async def test_si_aun_se_pinta_la_carretera_se_apaga_ya_y_sin_destripar(tmp_path: Path) -> None:
    view, renderer = await ahead_road(tmp_path, hit=1)
    renderer.gate = asyncio.Event()
    before = labels(view)
    interaction = make_interaction()
    task = asyncio.create_task(view._cross(interaction))

    async def first_edit() -> None:
        while not interaction.edit_original_response.await_count:
            await asyncio.sleep(0)

    await asyncio.wait_for(first_edit(), 1)
    first = interaction.edit_original_response.await_args.kwargs
    assert "attachments" not in first and first["embed"].color == COLOR_PLAYING
    # Apagados con las etiquetas de antes: nada de 🔁 Jugar, que diría que se acabó.
    assert labels(view) == before
    assert all(b.disabled for b in view.children if isinstance(b, ui.Button))
    renderer.gate.set()
    await task
    assert interaction.edit_original_response.await_count == 3
