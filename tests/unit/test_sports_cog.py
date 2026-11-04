"""Pruebas de bot.cogs.sports: el panel, apostar, resolver, el bucle y lo pendiente.

Se usa la economía real y el repositorio de partidos real sobre un SQLite temporal, un
reloj falso y los partidos puestos a mano en memoria (`Deportes.matches`). La API de
cuotas se sustituye por un cliente falso. El bucle no se arranca solo: cada prueba llama
a `tick()` o `settle()`.
"""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import ui
from interaction_fakes import fake_interaction
from render_fakes import FakeSportsScene

import bot.repositories.sqlite as sqlite_module
from bot.cogs import sports as cog_module
from bot.cogs.sports import (
    NOT_CONFIGURED,
    RESULT_LINES,
    Deportes,
    Resolved,
    SportsView,
    StakeModal,
    card,
    chances,
    final_state,
    header,
    match_card,
    option_text,
    pending,
    result_message,
    slip_card,
    ticket_state,
    win_streak,
)
from bot.repositories.economy import EconomyRepository
from bot.repositories.sports import SportsRepository
from bot.services.economy import STARTING_BALANCE, EconomyService, SportsBet, format_amount
from bot.services.pending import Side
from bot.services.sports import (
    MAX_STAKE,
    MIN_STAKE,
    RESULT_DELAY,
    SPORTS,
    VOID_AFTER,
    FeedError,
    FeedReply,
    Market,
    Match,
    Pick,
    Score,
    market_of,
    payout,
    priced,
)

GUILD_ID = 1
CHANNEL_ID = 555
OTHER_CHANNEL_ID = 556
ANA, LEO = 10, 11
START = 1_800_000_000.0
LIGA = SPORTS[0].key
CHAMPIONS = SPORTS[1].key
ODDS = {Pick.HOME: 185, Pick.DRAW: 360, Pick.AWAY: 410}
FAIR = {Pick.HOME: 0.45, Pick.DRAW: 0.27, Pick.AWAY: 0.28}


class FakeClock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> float:
        return self.now


def make_match(
    event_id: str = "e1", *, sport: str = LIGA, commence: float = START + 3_600
) -> Match:
    return Match(event_id, sport, "Real Madrid", "Barcelona", commence, dict(ODDS))


def make_goals_match(event_id: str = "e1", **kwargs: object) -> Match:
    """Un partido con más/menos (y por tanto con mercados de goles)."""
    return priced(make_match(event_id, **kwargs), {2.5: 0.5}, FAIR)  # type: ignore[arg-type]


def make_user(user_id: int, name: str = "Ana") -> MagicMock:
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.display_name = name
    user.mention = f"<@{user_id}>"
    user.bot = False
    return user


def make_channel(channel_id: int = CHANNEL_ID) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.send = AsyncMock()
    return channel


def make_interaction(user_id: int = ANA, *, guild_id: int | None = GUILD_ID) -> MagicMock:
    interaction = fake_interaction(make_user(user_id))
    interaction.guild_id = guild_id
    interaction.channel_id = CHANNEL_ID
    interaction.channel = make_channel()
    interaction.original_response = AsyncMock()
    return interaction


def said(interaction: MagicMock) -> str:
    """Lo último que se avisó con `notify` después de aceptar el clic (un aviso de texto)."""
    return interaction.followup.send.await_args.args[0]


def texts(view: ui.LayoutView) -> str:
    """Todo el texto de una vista (los `TextDisplay`, recorriendo sus hijos)."""
    return "\n".join(i.content for i in view.walk_children() if isinstance(i, ui.TextDisplay))


def widgets(view: ui.LayoutView) -> list[ui.Button | ui.Select]:
    """Los botones y desplegables de una vista, en orden."""
    return [i for i in view.walk_children() if isinstance(i, ui.Button | ui.Select)]


def ids(view: ui.LayoutView) -> list[str]:
    """Los `custom_id` de los botones y desplegables de una vista."""
    return [w.custom_id for w in widgets(view) if w.custom_id]


def widget(view: ui.LayoutView, custom_id: str) -> ui.Button | ui.Select:
    return next(w for w in widgets(view) if w.custom_id == custom_id)


def ticket(interaction: MagicMock) -> ui.LayoutView:
    """La vista del boleto que se mandó al apostar."""
    return interaction.followup.send.await_args.kwargs["view"]


def sent_text(channel: MagicMock) -> str:
    """El texto del último mensaje mandado a un canal (dentro de su vista)."""
    return texts(channel.send.await_args.kwargs["view"])


def open_view(cog: Deportes, event_id: str | None = "e1", market: Market = Market.RESULT):
    view = SportsView(cog, owner_id=ANA, sport=LIGA)
    view.event_id = event_id
    view.market = market
    view.rebuild()
    return view


@pytest.fixture(autouse=True)
def hooks(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Logros, apuestas y Renta se apuntan sin ir a los cogs: aquí solo se miran las llamadas."""
    found = SimpleNamespace(
        track=AsyncMock(),
        casino_play=AsyncMock(),
        record=AsyncMock(),
        remind=AsyncMock(),
        hint=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(cog_module.logros, "track", found.track)
    monkeypatch.setattr(cog_module.logros, "casino_play", found.casino_play)
    monkeypatch.setattr(cog_module.apuestas, "record", found.record)
    monkeypatch.setattr(cog_module.renta, "remind", found.remind)
    monkeypatch.setattr(cog_module.renta, "hint", found.hint)
    return found


async def make_cog(tmp_path: Path) -> tuple[Deportes, FakeClock]:
    economy_repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await economy_repository.initialize()
    sports = SportsRepository(tmp_path / "bot.db")
    await sports.initialize()
    clock = FakeClock()
    bot = MagicMock()
    channel = make_channels()
    bot.get_channel = MagicMock(side_effect=lambda channel_id: channel.get(channel_id))
    bot.channels = channel
    guild = MagicMock()
    guild.get_member = MagicMock(side_effect=lambda user_id: make_user(user_id))
    bot.get_guild = MagicMock(return_value=guild)
    cog = Deportes(
        bot, EconomyService(economy_repository), sports, clock=clock, scene=FakeSportsScene()
    )
    cog.matches[LIGA] = [make_match()]
    return cog, clock


def make_channels() -> dict[int, MagicMock]:
    return {CHANNEL_ID: make_channel(CHANNEL_ID), OTHER_CHANNEL_ID: make_channel(OTHER_CHANNEL_ID)}


async def stake_on(
    cog: Deportes,
    user_id: int,
    pick: Pick | str,
    *,
    stake: int = 100,
    match: Match | None = None,
    channel_id: int = CHANNEL_ID,
) -> SportsBet:
    """Apuesta directamente en la economía (sin pasar por el formulario)."""
    match = match or cog.matches[LIGA][0]
    pick = str(pick)
    ticket = SportsBet(
        id=0,
        guild_id=GUILD_ID,
        user_id=user_id,
        channel_id=channel_id,
        event_id=match.event_id,
        sport=match.sport,
        home=match.home,
        away=match.away,
        commence=match.commence,
        pick=pick,
        odds=match.price(pick),
        stake=stake,
        created_at=START,
        market=market_of(pick).value,  # type: ignore[union-attr]
    )
    bet, _ = await cog.economy.sports_bet(ticket, game="deportes")
    return bet


async def store(cog: Deportes, match: Match, score: Score | None = None) -> None:
    """Guarda el partido (y su marcador) en el repositorio, como si llegaran de la API."""
    await cog.repository.save_odds(match.sport, [match], now=START, remaining=None)
    if score is not None:
        await cog.repository.save_scores(
            match.sport, {match.event_id: score}, now=START, remaining=None
        )


# -- El panel -----------------------------------------------------------------------------


async def test_abrir_deportes_no_toca_la_base_de_datos_y_responde_directo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = MagicMock()
    connections: list[str] = []
    monkeypatch.setattr(sqlite_module, "connect", lambda *a, **k: connections.append("db"))
    interaction = make_interaction()

    await cog.deportes.callback(cog, interaction)

    assert connections == []
    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert isinstance(kwargs["view"], SportsView)
    assert "Real Madrid – Barcelona" in texts(kwargs["view"])
    interaction.response.defer.assert_not_awaited()


async def test_el_panel_se_abre_en_la_primera_competicion_con_partidos(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.matches[LIGA] = []
    cog.matches[CHAMPIONS] = [make_match("c1", sport=CHAMPIONS)]
    interaction = make_interaction()

    await cog.deportes.callback(cog, interaction)

    assert interaction.response.send_message.await_args.kwargs["view"].sport == CHAMPIONS


async def test_los_botones_de_la_portada_llevan_custom_id_fijo(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = MagicMock()
    cog.matches[LIGA] = [make_match(f"e{n}") for n in range(cog_module.LISTED + 2)]

    view = open_view(cog, None)

    assert sorted(ids(view)) == sorted(
        [
            f"deportes:liga:{LIGA}",
            f"deportes:liga:{CHAMPIONS}",
            "deportes:mis",
            *(f"deportes:ver:e{n}" for n in range(cog_module.LISTED)),
            "deportes:partido",
        ]
    )


async def test_los_botones_de_la_ficha_llevan_custom_id_fijo(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = MagicMock()

    view = open_view(cog)

    assert sorted(ids(view)) == sorted(
        [
            "deportes:volver",
            f"deportes:liga:{LIGA}",
            f"deportes:liga:{CHAMPIONS}",
            "deportes:mis",
            *(f"deportes:mercado:{market.value}" for market in Market),
            "deportes:apostar:1",
            "deportes:apostar:X",
            "deportes:apostar:2",
        ]
    )


async def test_cambiar_de_competicion_rehace_la_vista_sin_acudir_a_la_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = MagicMock()
    cog.matches[CHAMPIONS] = [make_match("c1", sport=CHAMPIONS)]
    view = open_view(cog)
    monkeypatch.setattr(sqlite_module, "connect", lambda *a, **k: pytest.fail("toca la base"))
    interaction = make_interaction()

    await widget(view, f"deportes:liga:{CHAMPIONS}").callback(interaction)

    assert view.sport == CHAMPIONS
    assert view.event_id is None
    interaction.response.edit_message.assert_awaited_once()
    assert interaction.response.edit_message.await_args.kwargs["view"] is view
    assert "Champions" in texts(view)
    assert "deportes:ver:c1" in ids(view)
    assert not any(i.startswith("deportes:apostar") for i in ids(view))


async def test_elegir_un_partido_del_desplegable_abre_su_ficha_con_las_cuotas(
    tmp_path: Path,
) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = MagicMock()  # con la clave de la API puesta
    cog.matches[LIGA] = [make_match(f"e{n}") for n in range(cog_module.LISTED + 2)]
    view = open_view(cog, None)
    select = widget(view, "deportes:partido")
    select._values = [f"e{cog_module.LISTED}"]  # type: ignore[attr-defined]  # noqa: SLF001
    interaction = make_interaction()

    await select.callback(interaction)

    assert view.event_id == f"e{cog_module.LISTED}"
    interaction.response.edit_message.assert_awaited_once()
    assert [i for i in ids(view) if i.startswith("deportes:apostar")] == [
        "deportes:apostar:1",
        "deportes:apostar:X",
        "deportes:apostar:2",
    ]
    text = texts(view)
    assert "×1,85" in text and "×3,60" in text and "×4,10" in text


async def test_el_boton_apostar_de_la_portada_abre_la_ficha(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = MagicMock()
    view = open_view(cog, None)
    interaction = make_interaction()

    await widget(view, "deportes:ver:e1").callback(interaction)

    assert view.event_id == "e1"
    assert "deportes:volver" in ids(view)
    interaction.response.edit_message.assert_awaited_once()


async def test_volver_deja_la_ficha_y_vuelve_a_la_portada(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = MagicMock()
    view = open_view(cog)
    interaction = make_interaction()

    await widget(view, "deportes:volver").callback(interaction)

    assert view.event_id is None
    assert "deportes:ver:e1" in ids(view)
    assert "deportes:volver" not in ids(view)


async def test_un_signo_abre_el_formulario_como_primera_respuesta(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = MagicMock()
    view = open_view(cog)
    interaction = make_interaction()

    await widget(view, "deportes:apostar:X").callback(interaction)

    interaction.response.defer.assert_not_awaited()
    interaction.response.send_modal.assert_awaited_once()
    modal = interaction.response.send_modal.await_args.args[0]
    assert isinstance(modal, StakeModal)
    assert (modal.pick, modal.odds) == ("X", 360)


async def test_un_signo_de_un_partido_que_ya_empezo_avisa_sin_formulario(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    cog.client = MagicMock()
    view = open_view(cog)
    clock.now = START + 7_200
    interaction = make_interaction()

    await widget(view, "deportes:apostar:1").callback(interaction)

    interaction.response.send_modal.assert_not_awaited()
    assert "ya ha empezado" in interaction.response.send_message.await_args.args[0]


async def test_solo_el_dueño_maneja_el_panel(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    view = SportsView(cog, owner_id=ANA, sport=LIGA)
    stranger = make_interaction(LEO)
    owner = make_interaction(ANA)

    assert await view.interaction_check(stranger) is False
    assert stranger.response.send_message.await_args.kwargs["ephemeral"] is True
    assert await view.interaction_check(owner) is True
    owner.response.send_message.assert_not_awaited()


async def test_sin_clave_de_la_api_el_panel_dice_que_no_esta_configurado(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    assert cog.client is None
    view = SportsView(cog, owner_id=ANA, sport=LIGA)

    assert NOT_CONFIGURED in texts(view)
    assert "Real Madrid" not in texts(view)

    cog.client = MagicMock()
    view.rebuild()
    assert NOT_CONFIGURED not in texts(view)


async def test_sin_partidos_el_panel_lo_dice(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = MagicMock()

    view = SportsView(cog, owner_id=ANA, sport=CHAMPIONS)

    assert "No hay partidos con cuotas" in texts(view)
    assert "Champions" in texts(view)


async def test_la_portada_lista_los_partidos_con_sus_cuotas_y_el_resto_va_al_desplegable(
    tmp_path: Path,
) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = MagicMock()
    cog.odds_at[LIGA] = START
    cog.matches[LIGA] = [make_match(f"e{n}") for n in range(cog_module.LISTED + 2)]

    view = open_view(cog, None)

    text = texts(view)
    assert "1 ×1,85 · X ×3,60 · 2 ×4,10" in text
    assert f"<t:{int(START)}:R>" in text
    select = widget(view, "deportes:partido")
    assert len(select.options) == 2
    assert "Más partidos (2)" in select.placeholder


async def test_la_portada_avisa_de_los_partidos_con_mercados_de_goles(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = MagicMock()
    cog.matches[LIGA] = [make_goals_match("g1"), make_match("e2")]

    text = texts(open_view(cog, None))

    assert text.count("⚽ goles") == 1


async def test_las_pestañas_de_mercados_salen_apagadas_sin_goles(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = MagicMock()

    view = open_view(cog)

    tabs = [w for w in widgets(view) if w.custom_id.startswith("deportes:mercado")]
    assert [tab.custom_id for tab in tabs] == [f"deportes:mercado:{m.value}" for m in Market]
    assert [tab.disabled for tab in tabs] == [False, True, True, True]
    assert tabs[0].style is discord.ButtonStyle.primary


async def test_las_pestañas_de_mercados_salen_encendidas_con_goles(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = MagicMock()
    cog.matches[LIGA] = [make_goals_match()]

    view = open_view(cog)

    tabs = [w for w in widgets(view) if w.custom_id.startswith("deportes:mercado")]
    assert [tab.disabled for tab in tabs] == [False] * 4
    assert "Goles esperados" in texts(view)


async def test_una_pestaña_enseña_las_opciones_de_su_mercado_sin_tocar_la_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = MagicMock()
    cog.matches[LIGA] = [make_goals_match()]
    view = open_view(cog)
    monkeypatch.setattr(sqlite_module, "connect", lambda *a, **k: pytest.fail("toca la base"))
    expected = {
        Market.GOALS: [f"deportes:apostar:{p}" for p in Market.GOALS.picks],
        Market.BOTH: ["deportes:apostar:si", "deportes:apostar:no"],
        Market.EXACT: ["deportes:exacto"],
        Market.RESULT: [f"deportes:apostar:{p}" for p in Market.RESULT.picks],
    }

    for market, wanted in expected.items():
        interaction = make_interaction()
        await widget(view, f"deportes:mercado:{market.value}").callback(interaction)

        assert view.market is market
        interaction.response.edit_message.assert_awaited_once()
        shown = [i for i in ids(view) if i.startswith(("deportes:apostar", "deportes:exacto"))]
        assert shown == wanted
        active = widget(view, f"deportes:mercado:{market.value}")
        assert active.style is discord.ButtonStyle.primary


async def test_si_el_mercado_ya_no_se_ofrece_la_ficha_vuelve_al_resultado(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = MagicMock()
    cog.matches[LIGA] = [make_goals_match()]
    view = open_view(cog, market=Market.GOALS)
    cog.matches[LIGA] = [make_match()]

    view.rebuild()

    assert view.market is Market.RESULT
    assert "deportes:apostar:1" in ids(view)


async def test_mas_de_2_5_abre_el_formulario_con_su_cuota(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = MagicMock()
    match = make_goals_match()
    cog.matches[LIGA] = [match]
    view = open_view(cog, market=Market.GOALS)
    interaction = make_interaction()

    await widget(view, "deportes:apostar:+2.5").callback(interaction)

    interaction.response.send_modal.assert_awaited_once()
    modal = interaction.response.send_modal.await_args.args[0]
    assert (modal.pick, modal.odds) == ("+2.5", match.price("+2.5"))
    assert "Más de 2,5 goles" in modal.title


async def test_el_exacto_del_desplegable_abre_el_formulario_como_primera_respuesta(
    tmp_path: Path,
) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = MagicMock()
    match = make_goals_match()
    cog.matches[LIGA] = [match]
    view = open_view(cog, market=Market.EXACT)
    select = widget(view, "deportes:exacto")
    select._values = ["2-1"]  # type: ignore[attr-defined]  # noqa: SLF001
    interaction = make_interaction()

    await select.callback(interaction)

    interaction.response.defer.assert_not_awaited()
    interaction.response.send_modal.assert_awaited_once()
    modal = interaction.response.send_modal.await_args.args[0]
    assert (modal.pick, modal.odds) == ("2-1", match.price("2-1"))


async def test_una_opcion_sin_cuota_avisa_sin_formulario(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = MagicMock()
    view = open_view(cog)
    interaction = make_interaction()

    await view._stake(interaction, "+2.5")  # noqa: SLF001

    interaction.response.send_modal.assert_not_awaited()
    assert "ya no tiene cuota" in interaction.response.send_message.await_args.args[0]


async def test_el_comando_de_texto_manda_el_panel_al_canal(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    ctx = MagicMock()
    ctx.author = make_user(ANA)
    ctx.channel = make_channel()
    ctx.send = AsyncMock()

    await cog.deportes_text.callback(cog, ctx)

    ctx.send.assert_awaited_once()
    assert isinstance(ctx.send.await_args.kwargs["view"], SportsView)


async def test_fuera_del_canal_del_casino_no_se_abre(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.casino_channel_ids = frozenset({999})
    interaction = make_interaction()

    await cog.deportes.callback(cog, interaction)

    assert "están en <#999>" in interaction.response.send_message.await_args.args[0]


async def test_al_caducar_se_apagan_los_botones_y_los_desplegables(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = MagicMock()
    view = open_view(cog)
    view.message = MagicMock(edit=AsyncMock())

    await view.on_timeout()

    assert all(w.disabled for w in widgets(view))
    view.message.edit.assert_awaited_once_with(view=view)


# -- Límites de Discord: 40 componentes y custom_id únicos --------------------------------


def check_limits(view: ui.LayoutView) -> None:
    total = sum(1 for _ in view.walk_children())
    assert total <= 40, f"{total} componentes"
    assert view.total_children_count <= 40
    found = ids(view)
    assert len(found) == len(set(found)), f"custom_id repetidos: {found}"


async def test_la_portada_con_31_partidos_cabe_en_discord(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = MagicMock()
    cog.matches[LIGA] = [make_goals_match(f"e{n:02d}") for n in range(31)]

    view = open_view(cog, None)

    check_limits(view)
    assert len(widget(view, "deportes:partido").options) == 25


@pytest.mark.parametrize("market", list(Market))
async def test_la_ficha_de_cada_mercado_cabe_en_discord(tmp_path: Path, market: Market) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = MagicMock()
    cog.matches[LIGA] = [make_goals_match()]

    view = open_view(cog, market=market)

    check_limits(view)
    assert view.market is market


async def test_la_ficha_sin_goles_cabe_en_discord(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = MagicMock()

    for market in Market:
        check_limits(open_view(cog, market=market))


async def test_las_opciones_del_exacto_caben_en_el_desplegable(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = MagicMock()
    cog.matches[LIGA] = [make_goals_match()]

    view = open_view(cog, market=Market.EXACT)

    values = [o.value for o in widget(view, "deportes:exacto").options]
    assert len(values) <= 25 and len(values) == len(set(values))
    assert "otro" in values and "0-0" in values


# -- Apostar ------------------------------------------------------------------------------


async def test_apostar_cobra_y_confirma_con_la_cuota_y_lo_que_cobraria(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    cog, _ = await make_cog(tmp_path)
    interaction = make_interaction()

    await cog.place(interaction, cog.matches[LIGA][0], "1", seen=185, text="100")

    # Aceptado antes de tocar la base de datos.
    interaction.response.defer.assert_awaited_once()
    message = texts(ticket(interaction))
    assert "×1,85" in message
    assert format_amount(100) in message
    assert format_amount(payout(100, 185)) in message
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 100
    [bet] = await cog.economy.open_sports_bets()
    assert (bet.user_id, bet.pick, bet.odds, bet.stake, bet.channel_id) == (
        ANA,
        "1",
        185,
        100,
        CHANNEL_ID,
    )
    hooks.remind.assert_awaited_once()
    hooks.track.assert_awaited_once()


async def test_el_boleto_lleva_la_imagen_y_el_texto_dentro_de_la_vista(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    interaction = make_interaction()

    await cog.place(interaction, cog.matches[LIGA][0], "1", seen=185, text="100")

    kwargs = interaction.followup.send.await_args.kwargs
    assert interaction.followup.send.await_args.args[0] is None
    assert kwargs["file"].filename == "boleto.png"
    assert kwargs["file"].fp.read() == b"PNG"
    assert any(isinstance(i, ui.MediaGallery) for i in kwargs["view"].walk_children())
    [state] = cog.scene.states
    assert state["kind"] == "boleto"
    assert (state["market"], state["pick"], state["odds"]) == (
        "Resultado",
        "1 (Real Madrid)",
        "×1,85",
    )
    assert state["player"] == "Ana"


class NoDrawing(FakeSportsScene):
    """Una escena que no consigue dibujar."""

    async def still(self, state: dict[str, object]) -> bytes | None:
        self.states.append(state)
        return None


async def test_si_no_se_puede_dibujar_el_boleto_sale_solo_el_texto(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.scene = NoDrawing()  # type: ignore[assignment]
    interaction = make_interaction()

    await cog.place(interaction, cog.matches[LIGA][0], "1", seen=185, text="100")

    kwargs = interaction.followup.send.await_args.kwargs
    assert "file" not in kwargs
    assert not any(isinstance(i, ui.MediaGallery) for i in kwargs["view"].walk_children())
    assert "×1,85" in texts(kwargs["view"])
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 100


async def test_si_la_escena_falla_la_apuesta_se_cobra_igual(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.scene.still = AsyncMock(side_effect=RuntimeError("Node caído"))  # type: ignore[method-assign]
    interaction = make_interaction()

    await cog.place(interaction, cog.matches[LIGA][0], "1", seen=185, text="100")

    assert "file" not in interaction.followup.send.await_args.kwargs
    assert len(await cog.economy.open_sports_bets()) == 1


async def test_apostar_a_un_partido_que_ya_empezo_avisa_sin_cobrar(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    clock.now = match.commence + 1
    interaction = make_interaction()

    await cog.place(interaction, match, "1", seen=185, text="100")

    assert "ya ha empezado" in said(interaction)
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE
    assert await cog.economy.open_sports_bets() == []


async def test_si_la_cuota_cambio_desde_que_se_vio_avisa_sin_cobrar(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    cog.matches[LIGA] = [Match(match.event_id, LIGA, match.home, match.away, match.commence,
                               {**ODDS, Pick.HOME: 170})]  # fmt: skip
    interaction = make_interaction()

    await cog.place(interaction, match, "1", seen=185, text="100")

    message = said(interaction)
    assert "La cuota ha cambiado" in message
    assert "×1,70" in message
    assert "×1,85" in message
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE


async def test_un_signo_que_se_quedo_sin_cuota_avisa_sin_cobrar(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    cog.matches[LIGA] = [Match(match.event_id, LIGA, match.home, match.away, match.commence,
                               {**ODDS, Pick.DRAW: 0})]  # fmt: skip
    interaction = make_interaction()

    await cog.place(interaction, match, "X", seen=360, text="100")

    assert "ya no tiene cuota" in said(interaction)
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE


@pytest.mark.parametrize("text", [str(MIN_STAKE - 1), str(MAX_STAKE + 1), "0", "1m"])
async def test_una_cantidad_fuera_del_rango_avisa_sin_cobrar(tmp_path: Path, text: str) -> None:
    cog, _ = await make_cog(tmp_path)
    await cog.economy.grant(GUILD_ID, ANA, amount=2_000_000, reason="prueba")
    before = await cog.economy.balance(GUILD_ID, ANA)
    interaction = make_interaction()

    await cog.place(interaction, cog.matches[LIGA][0], "1", seen=185, text=text)

    assert interaction.followup.send.await_count == 1
    assert await cog.economy.balance(GUILD_ID, ANA) == before
    assert await cog.economy.open_sports_bets() == []


async def test_el_rango_avisa_con_el_minimo_y_el_maximo(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    interaction = make_interaction()

    await cog.place(interaction, cog.matches[LIGA][0], "1", seen=185, text="5")

    assert format_amount(MIN_STAKE) in said(interaction)
    assert format_amount(MAX_STAKE) in said(interaction)


async def test_la_apuesta_minima_y_la_maxima_se_cobran(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    await cog.economy.grant(GUILD_ID, ANA, amount=100_000, reason="prueba")
    before = await cog.economy.balance(GUILD_ID, ANA)
    cog.matches[LIGA] = [make_match("e1"), make_match("e2")]

    await cog.place(make_interaction(), cog.matches[LIGA][0], "1", seen=185, text=str(MIN_STAKE))
    await cog.place(make_interaction(), cog.matches[LIGA][1], "1", seen=185, text=str(MAX_STAKE))

    assert await cog.economy.balance(GUILD_ID, ANA) == before - MIN_STAKE - MAX_STAKE


async def test_all_se_recorta_al_maximo_por_partido(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    await cog.economy.grant(GUILD_ID, ANA, amount=200_000, reason="prueba")
    before = await cog.economy.balance(GUILD_ID, ANA)
    interaction = make_interaction()

    await cog.place(interaction, cog.matches[LIGA][0], "1", seen=185, text="all")

    [bet] = await cog.economy.open_sports_bets()
    assert bet.stake == MAX_STAKE
    assert await cog.economy.balance(GUILD_ID, ANA) == before - MAX_STAKE


async def test_all_con_poco_saldo_apuesta_todo_lo_que_hay(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)

    await cog.place(make_interaction(), cog.matches[LIGA][0], "1", seen=185, text="all")

    [bet] = await cog.economy.open_sports_bets()
    assert bet.stake == STARTING_BALANCE
    assert await cog.economy.balance(GUILD_ID, ANA) == 0


async def test_una_segunda_apuesta_al_mismo_mercado_avisa_sin_cobrar(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await cog.place(make_interaction(), match, "1", seen=185, text="100")
    interaction = make_interaction()

    await cog.place(interaction, match, "2", seen=410, text="100")

    assert "Ya tienes una apuesta de resultado" in said(interaction)
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 100
    assert len(await cog.economy.open_sports_bets()) == 1


async def test_una_segunda_apuesta_en_otro_mercado_del_mismo_partido_se_cobra(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    cog, _ = await make_cog(tmp_path)
    match = make_goals_match()
    cog.matches[LIGA] = [match]
    await cog.place(make_interaction(), match, "1", seen=185, text="100")
    interaction = make_interaction()

    await cog.place(interaction, match, "+2.5", seen=match.price("+2.5"), text="200")

    assert "Más de 2,5 goles" in texts(ticket(interaction))
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 300
    bets = await cog.economy.open_sports_bets()
    assert {(b.pick, b.market, b.stake) for b in bets} == {
        ("1", "1x2", 100),
        ("+2.5", "goles", 200),
    }
    # Los logros saben cuántos mercados lleva en este partido.
    delta = hooks.track.await_args.args[4]
    assert delta.peak["sports_match_markets_max"] == 2
    assert delta.add["sports_market_goles"] == 1


async def test_dos_apuestas_al_mismo_mercado_de_goles_no_se_cobran_aunque_cambie_la_linea(
    tmp_path: Path,
) -> None:
    cog, _ = await make_cog(tmp_path)
    match = make_goals_match()
    cog.matches[LIGA] = [match]
    await cog.place(make_interaction(), match, "+2.5", seen=match.price("+2.5"), text="100")
    interaction = make_interaction()

    await cog.place(interaction, match, "-1.5", seen=match.price("-1.5"), text="100")

    assert "Ya tienes una apuesta de más/menos goles" in said(interaction)
    assert len(await cog.economy.open_sports_bets()) == 1


async def test_apostar_a_un_exacto_guarda_el_marcador_y_su_mercado(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    match = make_goals_match()
    cog.matches[LIGA] = [match]
    view = SportsView(cog, owner_id=ANA, sport=LIGA)
    modal = StakeModal(view, match, "2-1")
    modal.amount._value = "250"  # noqa: SLF001
    interaction = make_interaction()

    await modal.on_submit(interaction)

    [bet] = await cog.economy.open_sports_bets()
    assert (bet.pick, bet.market, bet.odds, bet.stake) == (
        "2-1",
        "exacto",
        match.price("2-1"),
        250,
    )
    assert "Exacto 2-1" in texts(ticket(interaction))


async def test_el_tope_por_partido_suma_los_mercados(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    await cog.economy.grant(GUILD_ID, ANA, amount=200_000, reason="prueba")
    match = make_goals_match()
    cog.matches[LIGA] = [match]
    await cog.place(make_interaction(), match, "1", seen=185, text="49000")
    before = await cog.economy.balance(GUILD_ID, ANA)
    interaction = make_interaction()

    await cog.place(interaction, match, "+2.5", seen=match.price("+2.5"), text="2000")

    assert f"te caben {format_amount(1_000)}" in said(interaction)
    assert await cog.economy.balance(GUILD_ID, ANA) == before
    assert len(await cog.economy.open_sports_bets()) == 1
    # Lo que cabe sí se puede apostar.
    await cog.place(make_interaction(), match, "+2.5", seen=match.price("+2.5"), text="1000")
    assert await cog.economy.balance(GUILD_ID, ANA) == before - 1_000


async def test_con_el_tope_lleno_otro_mercado_dice_que_no_cabe_nada(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    await cog.economy.grant(GUILD_ID, ANA, amount=200_000, reason="prueba")
    match = make_goals_match()
    cog.matches[LIGA] = [match]
    await cog.place(make_interaction(), match, "1", seen=185, text=str(MAX_STAKE))
    interaction = make_interaction()

    await cog.place(interaction, match, "si", seen=match.price("si"), text="all")

    assert "Ya llevas" in said(interaction)
    assert len(await cog.economy.open_sports_bets()) == 1


async def test_all_en_otro_mercado_se_recorta_a_lo_que_cabe(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    await cog.economy.grant(GUILD_ID, ANA, amount=200_000, reason="prueba")
    match = make_goals_match()
    cog.matches[LIGA] = [match]
    await cog.place(make_interaction(), match, "1", seen=185, text="30000")
    before = await cog.economy.balance(GUILD_ID, ANA)

    await cog.place(make_interaction(), match, "+2.5", seen=match.price("+2.5"), text="all")

    goals = next(b for b in await cog.economy.open_sports_bets() if b.market == "goles")
    assert goals.stake == MAX_STAKE - 30_000
    assert await cog.economy.balance(GUILD_ID, ANA) == before - goals.stake


async def test_el_tope_es_por_partido_y_otro_partido_tiene_el_suyo(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    await cog.economy.grant(GUILD_ID, ANA, amount=200_000, reason="prueba")
    cog.matches[LIGA] = [make_match("e1"), make_match("e2")]

    for match in cog.matches[LIGA]:
        await cog.place(make_interaction(), match, "1", seen=185, text=str(MAX_STAKE))

    assert len(await cog.economy.open_sports_bets()) == 2


async def test_si_no_le_llega_avisa_con_su_saldo_sin_cobrar(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    interaction = make_interaction()

    await cog.place(interaction, cog.matches[LIGA][0], "1", seen=185, text="5000")

    assert "No te llega" in said(interaction)
    assert format_amount(STARTING_BALANCE) in said(interaction)
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE


async def test_una_cantidad_que_no_se_entiende_avisa(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    interaction = make_interaction()

    await cog.place(interaction, cog.matches[LIGA][0], "1", seen=185, text="mucho")

    assert "No entiendo" in said(interaction)


async def test_no_se_apuesta_fuera_de_un_servidor(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    interaction = make_interaction(guild_id=None)

    await cog.place(interaction, cog.matches[LIGA][0], "1", seen=185, text="100")

    assert "dentro de un servidor" in said(interaction)
    assert await cog.economy.open_sports_bets() == []


async def test_el_formulario_apuesta_a_la_cuota_que_vio(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    view = SportsView(cog, owner_id=ANA, sport=LIGA)
    modal = StakeModal(view, cog.matches[LIGA][0], "2")
    modal.amount._value = "250"  # noqa: SLF001
    interaction = make_interaction()

    await modal.on_submit(interaction)

    [bet] = await cog.economy.open_sports_bets()
    assert (bet.pick, bet.odds, bet.stake) == ("2", 410, 250)


async def test_mis_apuestas_enseña_las_abiertas_en_un_mensaje_privado(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    cog, _ = await make_cog(tmp_path)
    await stake_on(cog, ANA, Pick.HOME)
    interaction = make_interaction()

    await cog.slip(interaction)

    interaction.response.defer.assert_awaited_once()
    assert interaction.response.defer.await_args.kwargs["ephemeral"] is True
    text = texts(interaction.edit_original_response.await_args.kwargs["view"])
    assert "Esperando resultado" in text
    assert "Real Madrid – Barcelona" in text
    hooks.track.assert_awaited_once()


# -- Resolver -----------------------------------------------------------------------------


async def test_settle_paga_a_quien_acierta_y_no_a_quien_falla(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await store(cog, match, Score(2, 1))
    await stake_on(cog, ANA, Pick.HOME)
    await stake_on(cog, LEO, Pick.AWAY)
    clock.now = match.commence + RESULT_DELAY

    resolved = await cog.settle(clock.now)

    assert {r.bet.user_id: r.bet.status for r in resolved} == {ANA: "won", LEO: "lost"}
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 100 + payout(100, 185)
    assert await cog.economy.balance(GUILD_ID, LEO) == STARTING_BALANCE - 100
    assert await cog.economy.open_sports_bets() == []


async def test_settle_manda_un_solo_mensaje_por_partido_y_canal_y_menciona_a_quien_cobra(
    tmp_path: Path,
) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await store(cog, match, Score(2, 1))
    await stake_on(cog, ANA, Pick.HOME)
    await stake_on(cog, LEO, Pick.AWAY)
    clock.now = match.commence + RESULT_DELAY

    await cog.settle(clock.now)

    channel = cog.bot.channels[CHANNEL_ID]
    channel.send.assert_awaited_once()
    assert channel.send.await_args.args == ()
    text = sent_text(channel)
    assert "Final: Real Madrid 2–1 Barcelona" in text
    assert f"✅ <@{ANA}>" in text
    assert f"❌ <@{LEO}>" in text
    mentions = channel.send.await_args.kwargs["allowed_mentions"]
    assert [user.id for user in mentions.users] == [ANA]
    assert mentions.everyone is False
    assert mentions.roles is False


async def test_settle_sube_el_marcador_final_dibujado_con_los_datos_del_partido(
    tmp_path: Path,
) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await store(cog, match, Score(2, 1))
    await stake_on(cog, ANA, Pick.HOME)
    await stake_on(cog, LEO, Pick.AWAY)
    clock.now = match.commence + RESULT_DELAY

    await cog.settle(clock.now)

    kwargs = cog.bot.channels[CHANNEL_ID].send.await_args.kwargs
    assert kwargs["file"].filename == "final.png"
    assert kwargs["file"].fp.read() == b"PNG"
    assert any(isinstance(i, ui.MediaGallery) for i in kwargs["view"].walk_children())
    [state] = cog.scene.states
    assert state["kind"] == "final"
    assert (state["homeGoals"], state["awayGoals"]) == (2, 1)
    assert state["chips"] == [
        ["Resultado", "1"],
        ["Goles", "3"],
        ["Ambos marcan", "Sí"],
        ["Exacto", "2-1"],
    ]
    assert state["summary"] == "1 cobra · 1 pierde"


async def test_settle_sin_dibujo_manda_solo_el_texto(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    cog.scene = NoDrawing()  # type: ignore[assignment]
    match = cog.matches[LIGA][0]
    await store(cog, match, Score(2, 1))
    await stake_on(cog, ANA, Pick.HOME)
    clock.now = match.commence + RESULT_DELAY

    await cog.settle(clock.now)

    kwargs = cog.bot.channels[CHANNEL_ID].send.await_args.kwargs
    assert "file" not in kwargs
    assert "Final: Real Madrid 2–1 Barcelona" in texts(kwargs["view"])


@pytest.mark.parametrize(
    ("score", "status"), [(Score(2, 1), "won"), (Score(1, 0), "lost"), (Score(0, 0), "lost")]
)
async def test_un_mas_de_2_5_se_paga_con_3_goles_y_no_con_1_o_0(
    tmp_path: Path, score: Score, status: str
) -> None:
    cog, clock = await make_cog(tmp_path)
    match = make_goals_match()
    cog.matches[LIGA] = [match]
    await store(cog, match, score)
    bet = await stake_on(cog, ANA, "+2.5", stake=100, match=match)
    clock.now = match.commence + RESULT_DELAY

    [done] = await cog.settle(clock.now)

    assert done.bet.status == status
    prize = payout(100, bet.odds) if status == "won" else 0
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 100 + prize
    assert ("Más de 2,5 goles" in sent_text(cog.bot.channels[CHANNEL_ID])) is True


async def test_cada_mercado_del_mismo_partido_se_resuelve_por_su_cuenta(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    match = make_goals_match()
    cog.matches[LIGA] = [match]
    await store(cog, match, Score(2, 1))
    for pick in ("1", "-2.5", "si", "2-1"):
        await stake_on(cog, ANA, pick, match=match)
    clock.now = match.commence + RESULT_DELAY

    resolved = await cog.settle(clock.now)

    assert {r.bet.pick: r.bet.status for r in resolved} == {
        "1": "won",
        "-2.5": "lost",
        "si": "won",
        "2-1": "won",
    }
    # Un solo mensaje para el partido, con el marcador dibujado una vez.
    cog.bot.channels[CHANNEL_ID].send.assert_awaited_once()
    assert len(cog.scene.states) == 1
    assert cog.scene.states[0]["summary"] == "3 cobran · 1 pierde"


async def test_settle_manda_un_mensaje_por_cada_canal_donde_se_aposto(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await store(cog, match, Score(0, 0))
    await stake_on(cog, ANA, Pick.DRAW, channel_id=CHANNEL_ID)
    await stake_on(cog, LEO, Pick.DRAW, channel_id=OTHER_CHANNEL_ID)
    clock.now = match.commence + RESULT_DELAY

    await cog.settle(clock.now)

    for channel_id, user_id in ((CHANNEL_ID, ANA), (OTHER_CHANNEL_ID, LEO)):
        channel = cog.bot.channels[channel_id]
        channel.send.assert_awaited_once()
        assert f"<@{user_id}>" in sent_text(channel)


async def test_settle_cuenta_el_casino_las_estadisticas_y_los_logros(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await store(cog, match, Score(2, 1))
    await stake_on(cog, ANA, Pick.HOME)
    clock.now = match.commence + RESULT_DELAY

    await cog.settle(clock.now)

    hooks.casino_play.assert_awaited_once()
    assert hooks.casino_play.await_args.kwargs["net"] == payout(100, 185) - 100
    delta = hooks.casino_play.await_args.args[4]
    assert (delta.add["sports_settled"], delta.add["sports_wins"]) == (1, 1)
    hooks.record.assert_awaited_once()
    record = hooks.record.await_args.kwargs
    assert (record["game"], record["stake"], record["net"]) == ("deportes", 100, 85)


@pytest.mark.parametrize(("pick", "draw_win"), [("X", True), ("-2.5", False)])
async def test_settle_pasa_el_mercado_a_las_estadisticas_de_los_logros(
    tmp_path: Path, hooks: SimpleNamespace, pick: str, draw_win: bool
) -> None:
    cog, clock = await make_cog(tmp_path)
    match = make_goals_match()
    cog.matches[LIGA] = [match]
    await store(cog, match, Score(0, 0))
    await stake_on(cog, ANA, pick, match=match)
    clock.now = match.commence + RESULT_DELAY

    await cog.settle(clock.now)

    delta = hooks.casino_play.await_args.args[4]
    assert delta.add["sports_wins"] == 1
    # El 0-0 acertado solo es «empate a cero» en el 1X2, no en los mercados de goles.
    assert ("sports_win_00" in delta.add) is draw_win


async def test_settle_sin_resultado_antes_de_void_after_no_hace_nada(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await stake_on(cog, ANA, Pick.HOME)
    clock.now = match.commence + VOID_AFTER - 1

    assert await cog.settle(clock.now) == []

    cog.bot.channels[CHANNEL_ID].send.assert_not_awaited()
    assert len(await cog.economy.open_sports_bets()) == 1
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 100


async def test_settle_no_hace_nada_con_un_partido_que_no_ha_empezado(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await store(cog, match, Score(2, 1))
    await stake_on(cog, ANA, Pick.HOME)
    clock.now = match.commence - 1

    assert await cog.settle(clock.now) == []

    assert len(await cog.economy.open_sports_bets()) == 1


async def test_settle_anula_y_devuelve_lo_que_pasa_void_after_sin_resultado(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await stake_on(cog, ANA, Pick.HOME)
    clock.now = match.commence + VOID_AFTER

    resolved = await cog.settle(clock.now)

    assert [r.bet.status for r in resolved] == ["void"]
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE
    channel = cog.bot.channels[CHANNEL_ID]
    channel.send.assert_awaited_once()
    assert "no tiene resultado" in sent_text(channel)
    assert f"<@{ANA}> recupera" in sent_text(channel)
    # Sin marcador no hay nada que dibujar: solo texto.
    assert "file" not in channel.send.await_args.kwargs
    assert cog.scene.states == []
    assert [u.id for u in channel.send.await_args.kwargs["allowed_mentions"].users] == [ANA]
    # Anular no es una jugada del casino.
    hooks.casino_play.assert_not_awaited()
    hooks.record.assert_not_awaited()
    hooks.track.assert_awaited_once()


async def test_settle_dos_veces_no_paga_dos_veces(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await store(cog, match, Score(2, 1))
    await stake_on(cog, ANA, Pick.HOME)
    clock.now = match.commence + RESULT_DELAY
    await cog.settle(clock.now)
    balance = await cog.economy.balance(GUILD_ID, ANA)

    assert await cog.settle(clock.now) == []

    assert await cog.economy.balance(GUILD_ID, ANA) == balance
    cog.bot.channels[CHANNEL_ID].send.assert_awaited_once()


async def test_settle_sigue_si_no_puede_anunciar_en_el_canal(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await store(cog, match, Score(2, 1))
    await stake_on(cog, ANA, Pick.HOME)
    cog.bot.channels[CHANNEL_ID].send.side_effect = discord.HTTPException(
        MagicMock(status=500), "caído"
    )
    clock.now = match.commence + RESULT_DELAY

    resolved = await cog.settle(clock.now)

    assert len(resolved) == 1
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 100 + payout(100, 185)


# -- El bucle -----------------------------------------------------------------------------


class FakeOdds:
    """Sustituye a `OddsClient`: apunta qué se pidió y contesta lo que se le dé."""

    def __init__(self) -> None:
        self.odds_calls: list[str] = []
        self.totals_calls: list[bool] = []
        self.scores_calls: list[tuple[str, int]] = []
        self.odds_reply: FeedReply | FeedError = FeedReply([], 400)
        self.scores_reply: FeedReply | FeedError = FeedReply([], 398)

    async def odds(self, sport: str, *, now: float, totals: bool = False) -> FeedReply:
        self.odds_calls.append(sport)
        self.totals_calls.append(totals)
        if isinstance(self.odds_reply, FeedError):
            raise self.odds_reply
        return self.odds_reply

    async def scores(self, sport: str, *, days: int) -> FeedReply:
        self.scores_calls.append((sport, days))
        if isinstance(self.scores_reply, FeedError):
            raise self.scores_reply
        return self.scores_reply


@pytest.fixture
def parsed_matches(monkeypatch: pytest.MonkeyPatch) -> list[Match]:
    """Lo que `parse_odds` saca de la respuesta de cuotas (la respuesta no se mira)."""
    found: list[Match] = [make_match("nuevo", commence=START + 7_200)]
    monkeypatch.setattr(
        cog_module, "parse_odds", lambda events, *, now, known_totals=None: list(found)
    )
    return found


async def test_tick_pide_cuotas_la_primera_vez_y_no_en_la_siguiente_vuelta(
    tmp_path: Path, parsed_matches: list[Match]
) -> None:
    cog, clock = await make_cog(tmp_path)
    cog.matches.clear()
    feed = cog.client = FakeOdds()  # type: ignore[assignment]

    await cog.tick()

    assert feed.odds_calls == [LIGA, CHAMPIONS]
    assert [m.event_id for m in cog.matches[LIGA]] == ["nuevo"]
    assert cog.odds_at[LIGA] == START
    clock.now += 300

    await cog.tick()

    assert feed.odds_calls == [LIGA, CHAMPIONS]
    assert feed.scores_calls == []


async def test_tick_pide_el_mas_menos_la_primera_vez_y_no_en_la_siguiente_consulta(
    tmp_path: Path, parsed_matches: list[Match]
) -> None:
    from bot.services.sports import ODDS_EVERY

    cog, clock = await make_cog(tmp_path)
    feed = cog.client = FakeOdds()  # type: ignore[assignment]

    await cog.tick()
    clock.now += ODDS_EVERY
    await cog.tick()

    assert feed.totals_calls == [True, True, False, False]


async def test_las_cuotas_se_leen_en_otro_hilo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client = FakeOdds()  # type: ignore[assignment]
    threads: list[int] = []

    def parse(events: object, *, now: float, known_totals: object = None) -> list[Match]:
        threads.append(threading.get_ident())
        return []

    monkeypatch.setattr(cog_module, "parse_odds", parse)

    await cog.tick()

    assert threads and threading.get_ident() not in threads


async def test_el_mas_menos_ya_sabido_se_pasa_al_leer_las_cuotas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.matches[LIGA] = [make_goals_match()]
    cog.client = FakeOdds()  # type: ignore[assignment]
    seen: list[object] = []

    def parse(events: object, *, now: float, known_totals: object = None) -> list[Match]:
        seen.append(known_totals)
        return []

    monkeypatch.setattr(cog_module, "parse_odds", parse)

    await cog.tick()

    assert seen[0] == {"e1": {2.5: 0.5}}


async def test_tick_vuelve_a_pedir_cuotas_cuando_toca(
    tmp_path: Path, parsed_matches: list[Match]
) -> None:
    from bot.services.sports import ODDS_EVERY

    cog, clock = await make_cog(tmp_path)
    feed = cog.client = FakeOdds()  # type: ignore[assignment]
    await cog.tick()
    clock.now += ODDS_EVERY

    await cog.tick()

    assert feed.odds_calls == [LIGA, CHAMPIONS] * 2


async def test_tick_sin_cliente_no_pide_nada_pero_resuelve(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await stake_on(cog, ANA, Pick.HOME)
    clock.now = match.commence + VOID_AFTER

    await cog.tick()

    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE


async def _fresh_odds(cog: Deportes, now: float) -> None:
    """Las cuotas de las dos competiciones acaban de pedirse: no tocan."""
    for sport in SPORTS:
        await cog.repository.mark_checked(sport.key, "odds_at", now=now)


def _final(match: Match, home: int, away: int) -> dict:
    return {
        "id": match.event_id,
        "completed": True,
        "home_team": match.home,
        "away_team": match.away,
        "scores": [
            {"name": match.home, "score": str(home)},
            {"name": match.away, "score": str(away)},
        ],
    }


async def test_tick_pide_resultados_si_hay_apuestas_de_un_partido_que_ya_acabo(
    tmp_path: Path,
) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await store(cog, match)
    await stake_on(cog, ANA, Pick.HOME)
    clock.now = match.commence + RESULT_DELAY
    await _fresh_odds(cog, clock.now)
    feed = cog.client = FakeOdds()  # type: ignore[assignment]
    feed.scores_reply = FeedReply([_final(match, 2, 1)], 300)

    await cog.tick()

    # Solo la competición de la apuesta, y la apuesta se resuelve en la misma vuelta.
    assert feed.scores_calls == [(LIGA, 1)]
    assert feed.odds_calls == []
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 100 + payout(100, 185)


async def test_tick_no_pide_resultados_antes_de_result_delay(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await stake_on(cog, ANA, Pick.HOME)
    clock.now = match.commence + RESULT_DELAY - 1
    await _fresh_odds(cog, clock.now)
    feed = cog.client = FakeOdds()  # type: ignore[assignment]

    await cog.tick()

    assert feed.scores_calls == []


async def test_tick_no_pide_resultados_sin_apuestas(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    clock.now = cog.matches[LIGA][0].commence + RESULT_DELAY + 600
    await _fresh_odds(cog, clock.now)
    feed = cog.client = FakeOdds()  # type: ignore[assignment]

    await cog.tick()

    assert feed.scores_calls == []


async def test_tick_no_repite_la_consulta_de_resultados_en_la_vuelta_siguiente(
    tmp_path: Path,
) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await store(cog, match)
    await stake_on(cog, ANA, Pick.HOME)
    clock.now = match.commence + RESULT_DELAY
    await _fresh_odds(cog, clock.now)
    feed = cog.client = FakeOdds()  # type: ignore[assignment]
    await cog.tick()
    clock.now += 300

    await cog.tick()

    assert feed.scores_calls == [(LIGA, 1)]


async def test_un_error_de_la_api_de_cuotas_no_rompe_la_vuelta_y_se_apunta_como_hecha(
    tmp_path: Path,
) -> None:
    cog, clock = await make_cog(tmp_path)
    feed = cog.client = FakeOdds()  # type: ignore[assignment]
    feed.odds_reply = FeedError("La API de cuotas contesta 500")

    await cog.tick()

    assert feed.odds_calls == [LIGA, CHAMPIONS]
    for sport in SPORTS:
        assert (await cog.repository.feed(sport.key)).odds_at == START
    clock.now += 300
    await cog.tick()
    assert feed.odds_calls == [LIGA, CHAMPIONS]


async def test_un_error_de_la_api_de_resultados_no_rompe_la_vuelta_y_se_apunta_como_hecha(
    tmp_path: Path,
) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await store(cog, match)
    await stake_on(cog, ANA, Pick.HOME)
    clock.now = match.commence + RESULT_DELAY
    await _fresh_odds(cog, clock.now)
    feed = cog.client = FakeOdds()  # type: ignore[assignment]
    feed.scores_reply = FeedError("La API de cuotas no responde (TimeoutError)")

    await cog.tick()

    assert (await cog.repository.feed(LIGA)).scores_at == clock.now
    assert len(await cog.economy.open_sports_bets()) == 1
    clock.now += 300
    await cog.tick()
    assert feed.scores_calls == [(LIGA, 1)]


async def test_el_bucle_se_traga_los_errores_de_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _ = await make_cog(tmp_path)
    monkeypatch.setattr(cog, "tick", AsyncMock(side_effect=RuntimeError("boom")))

    await cog._feed.coro(cog)  # noqa: SLF001

    cog.tick.assert_awaited_once()


# -- saldo --------------------------------------------------------------------------------


async def test_pending_da_una_linea_retenida_por_apuesta_abierta(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    cog.matches[LIGA] = [match, make_match("e2", commence=START + 9_000)]
    await stake_on(cog, ANA, Pick.HOME, stake=100)
    await stake_on(cog, ANA, Pick.DRAW, stake=250, match=cog.matches[LIGA][1])
    await stake_on(cog, LEO, Pick.AWAY)
    cog.bot.get_cog = MagicMock(return_value=cog)

    items = await pending(cog.bot, GUILD_ID, ANA)

    assert sorted((i.amount, i.when) for i in items) == [
        (100, match.commence),
        (250, START + 9_000),
    ]
    assert {(i.side, i.kind) for i in items} == {(Side.HELD, "sports_bet")}
    first = next(i for i in items if i.amount == 100)
    assert "1 (Real Madrid)" in first.text
    assert format_amount(payout(100, 185)) in first.text
    assert f"<t:{int(match.commence)}:R>" in first.line()


async def test_pending_nombra_los_mercados_de_goles_y_el_exacto(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    match = make_goals_match()
    cog.matches[LIGA] = [match]
    await stake_on(cog, ANA, "+2.5", stake=100, match=match)
    await stake_on(cog, ANA, "2-1", stake=50, match=match)
    cog.bot.get_cog = MagicMock(return_value=cog)

    items = await pending(cog.bot, GUILD_ID, ANA)

    joined = "\n".join(i.text for i in items)
    assert "Más de 2,5 goles" in joined
    assert "Exacto 2-1" in joined


async def test_pending_no_cuenta_las_apuestas_resueltas(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    match = cog.matches[LIGA][0]
    await store(cog, match, Score(2, 1))
    await stake_on(cog, ANA, Pick.HOME)
    clock.now = match.commence + RESULT_DELAY
    await cog.settle(clock.now)
    cog.bot.get_cog = MagicMock(return_value=cog)

    assert await pending(cog.bot, GUILD_ID, ANA) == []


async def test_pending_sin_el_cog_cargado_da_una_lista_vacia() -> None:
    bot = MagicMock()
    bot.get_cog = MagicMock(return_value=None)

    assert await pending(bot, GUILD_ID, ANA) == []


# -- Funciones puras ----------------------------------------------------------------------


def make_bet(
    user_id: int = ANA,
    *,
    pick: str = "1",
    market: str = "1x2",
    status: str = "won",
    stake: int = 100,
    odds: int = 185,
    bet_id: int = 1,
    settled_at: float | None = START,
    sport: str = LIGA,
) -> SportsBet:
    won = status == "won"
    return SportsBet(
        id=bet_id,
        guild_id=GUILD_ID,
        user_id=user_id,
        channel_id=CHANNEL_ID,
        event_id="e1",
        sport=sport,
        home="Real Madrid",
        away="Barcelona",
        commence=START,
        pick=pick,
        odds=odds,
        stake=stake,
        created_at=START,
        market=market,
        status=status,
        payout=payout(stake, odds) if won else (stake if status == "void" else 0),
        settled_at=settled_at,
    )


def test_result_message_cuenta_el_marcador_quien_cobra_y_quien_pierde() -> None:
    settlement = SimpleNamespace(tax_delta=12)
    text = result_message(
        [
            Resolved(make_bet(ANA), Score(2, 1), settlement, 0),  # type: ignore[arg-type]
            Resolved(make_bet(LEO, pick="2", status="lost"), Score(2, 1), settlement, 0),  # type: ignore[arg-type]
        ]
    )

    lines = text.splitlines()
    assert lines[0] == "⚽ **Final: Real Madrid 2–1 Barcelona** · 🇪🇸 LaLiga"
    assert f"✅ <@{ANA}> 1 (Real Madrid) a ×1,85: cobra **{format_amount(185)}**" in lines[1]
    assert f"(+{format_amount(85)})" in lines[1]
    assert f"IRPF −{format_amount(12)}" in lines[1]
    assert lines[2] == f"❌ <@{LEO}> 2 (Barcelona) a ×1,85: pierde {format_amount(100)}"


def test_result_message_sin_irpf_no_pone_la_retencion() -> None:
    settlement = SimpleNamespace(tax_delta=0)

    text = result_message([Resolved(make_bet(), Score(1, 0), settlement, 0)])  # type: ignore[arg-type]

    assert "IRPF" not in text


def test_result_message_de_una_anulada_dice_que_se_devuelve() -> None:
    text = result_message([Resolved(make_bet(status="void"), None, None, 0)])

    assert "no tiene resultado" in text
    assert f"<@{ANA}> recupera {format_amount(100)}" in text


def test_result_message_corta_la_lista_y_cuenta_el_resto() -> None:
    resolved = [
        Resolved(make_bet(user_id, status="lost", pick="2"), Score(1, 0), None, 0)
        for user_id in range(100, 100 + RESULT_LINES + 3)
    ]

    text = result_message(resolved)

    assert text.count("❌") == RESULT_LINES
    assert text.splitlines()[-1] == "-# Y 3 apuestas más."


def test_result_message_con_una_competicion_desconocida_no_falla() -> None:
    text = result_message([Resolved(make_bet(sport="otra"), Score(1, 0), None, 0)])

    assert "· otra" in text


def test_slip_card_separa_esperando_resultado_y_resueltas() -> None:
    bets = [
        make_bet(bet_id=1, status="open", settled_at=None),
        make_bet(bet_id=2, status="won"),
        make_bet(bet_id=3, status="lost"),
        make_bet(bet_id=4, status="void"),
    ]

    view = slip_card(bets, "Ana")

    text = texts(view)
    assert "Ana" in text.splitlines()[0]
    assert "### Esperando resultado" in text and "### Últimas resueltas" in text
    assert f"cobrarías {format_amount(185)}" in text
    done = text.split("### Últimas resueltas\n")[1].splitlines()
    assert f"cobraste **{format_amount(185)}**" in done[0] and done[0].startswith("✅")
    assert done[1].startswith("❌")
    assert "anulada y devuelta" in done[2] and done[2].startswith("↩️")
    assert not widgets(view)


def test_slip_card_nombra_el_mercado_de_cada_apuesta() -> None:
    bets = [
        make_bet(bet_id=1, status="open", settled_at=None, pick="+2.5", market="goles"),
        make_bet(bet_id=2, status="won", pick="2-1", market="exacto"),
    ]

    text = texts(slip_card(bets, "Ana"))

    assert "Más de 2,5 goles" in text
    assert "Exacto 2-1" in text


def test_slip_card_sin_apuestas_invita_a_apostar() -> None:
    text = texts(slip_card([], "Ana"))

    assert "Aún no has apostado" in text
    assert "Esperando resultado" not in text


def test_win_streak_cuenta_las_acertadas_seguidas_desde_la_ultima() -> None:
    bets = [
        make_bet(bet_id=1, status="won", settled_at=1),
        make_bet(bet_id=2, status="lost", settled_at=2),
        make_bet(bet_id=3, status="won", settled_at=3),
        make_bet(bet_id=4, status="won", settled_at=4),
    ]

    assert win_streak(bets) == 2
    assert win_streak(list(reversed(bets))) == 2


def test_win_streak_ignora_las_anuladas_y_las_abiertas_y_se_corta_con_una_perdida() -> None:
    bets = [
        make_bet(bet_id=1, status="won", settled_at=1),
        make_bet(bet_id=2, status="void", settled_at=2),
        make_bet(bet_id=3, status="won", settled_at=3),
        make_bet(bet_id=4, status="open", settled_at=None),
        make_bet(bet_id=5, status="lost", settled_at=0),
    ]

    assert win_streak(bets) == 2
    assert win_streak([make_bet(status="lost")]) == 0
    assert win_streak([]) == 0


def test_header_dice_la_competicion_los_partidos_y_cuando_llegaron_las_cuotas() -> None:
    text = header(LIGA, [make_match()], configured=True, odds_at=START)

    assert "Apuestas deportivas" in text and "LaLiga" in text
    assert "1 partidos" in text
    assert f"<t:{int(START)}:R>" in text
    assert "cuotas de" not in header(LIGA, [make_match()], configured=False, odds_at=START)
    assert "partidos en" not in header(CHAMPIONS, [], configured=True, odds_at=None)


def test_option_text_enseña_la_cuota_la_probabilidad_y_lo_que_cobrarian_1000() -> None:
    text = option_text(make_match(), "1")

    assert "×1,85" in text
    assert f"1.000 Y$ cobran {format_amount(payout(1_000, 185))}" in text
    assert "51 %" in text  # 0,94 / 1,85


def test_option_text_de_un_signo_sin_cuota_lo_dice() -> None:
    match = Match("e1", LIGA, "Real Madrid", "Barcelona", START + 3_600, {**ODDS, Pick.DRAW: 0})

    assert option_text(match, "X") == "**X (empate)** · sin cuota"
    assert option_text(make_match(), "+2.5").endswith("sin cuota")


def test_option_text_usa_el_retorno_de_su_mercado() -> None:
    match = make_goals_match()

    exact = option_text(match, "2-1")

    assert "Exacto 2-1" in exact
    assert f"{round(Market.EXACT.rtp * 10_000 / match.price('2-1'))} %" in exact


def test_match_card_enseña_cuando_la_probabilidad_de_cada_signo_y_los_goles_esperados() -> None:
    match = make_goals_match()

    text = match_card(match)

    assert f"<t:{int(match.commence)}:F>" in text
    for pick in Pick:
        assert f"`{pick.value}`" in text
    assert "Empate" in text
    assert "Goles esperados" in text
    assert "Goles esperados" not in match_card(make_match())


def test_chances_suma_uno_y_reparte_lo_que_falta_a_un_signo_sin_cuota() -> None:
    match = Match("e1", LIGA, "Real Madrid", "Barcelona", START, {**ODDS, Pick.DRAW: 0})

    shares = chances(match)

    assert sum(shares.values()) == pytest.approx(1)
    assert shares[Pick.DRAW] > 0
    assert sum(chances(make_match()).values()) == pytest.approx(1)


def test_card_pone_la_imagen_solo_si_hay() -> None:
    plain = card("hola", color=discord.Color.green())
    drawn = card("hola", color=discord.Color.green(), image="boleto.png")

    assert not any(isinstance(i, ui.MediaGallery) for i in plain.walk_children())
    [gallery] = [i for i in drawn.walk_children() if isinstance(i, ui.MediaGallery)]
    assert gallery.items[0].media.url == "attachment://boleto.png"
    assert texts(drawn) == "hola"


def test_ticket_state_lleva_todo_en_texto() -> None:
    bet = make_bet(pick="2-1", market="exacto", odds=900, bet_id=7, status="open")

    state = ticket_state(bet, "Ana")

    assert state["kind"] == "boleto"
    assert (state["market"], state["pick"], state["odds"]) == (
        "Resultado exacto",
        "Exacto 2-1",
        "×9,00",
    )
    assert (state["stake"], state["payout"]) == (format_amount(100), format_amount(900))
    assert state["serial"] == "Nº 000007"
    assert state["player"] == "Ana"


@pytest.mark.parametrize(
    ("score", "chips"),
    [
        (Score(2, 1), ["1", "3", "Sí", "2-1"]),
        (Score(0, 0), ["X", "0", "No", "0-0"]),
        (Score(0, 2), ["2", "2", "No", "0-2"]),
        (Score(5, 0), ["1", "5", "No", "Otro"]),
        (Score(4, 4), ["X", "8", "Sí", "Otro"]),
    ],
)
def test_final_state_calcula_los_chips_del_marcador(score: Score, chips: list[str]) -> None:
    state = final_state([Resolved(make_bet(), score, None, 0)])

    assert state is not None
    assert [value for _name, value in state["chips"]] == chips
    assert [name for name, _value in state["chips"]] == [
        "Resultado",
        "Goles",
        "Ambos marcan",
        "Exacto",
    ]


def test_final_state_cuenta_quien_cobra_y_quien_pierde_y_no_dibuja_una_anulada() -> None:
    won = Resolved(make_bet(ANA), Score(2, 1), None, 0)
    lost = [Resolved(make_bet(user, status="lost"), Score(2, 1), None, 0) for user in (5, 6)]

    assert final_state([won])["summary"] == "1 cobra"  # type: ignore[index]
    assert final_state(lost)["summary"] == "2 pierden"  # type: ignore[index]
    assert final_state([won, *lost])["summary"] == "1 cobra · 2 pierden"  # type: ignore[index]
    assert final_state([Resolved(make_bet(status="void"), None, None, 0)]) is None


def test_result_message_nombra_el_mercado_apostado() -> None:
    settlement = SimpleNamespace(tax_delta=0)

    text = result_message(
        [
            Resolved(make_bet(pick="+2.5", market="goles"), Score(2, 1), settlement, 0),  # type: ignore[arg-type]
            Resolved(
                make_bet(LEO, pick="2-1", market="exacto", status="lost"),
                Score(2, 1),
                settlement,
                0,
            ),  # type: ignore[arg-type]
        ]
    )

    assert "Más de 2,5 goles a ×1,85: cobra" in text
    assert "Exacto 2-1 a ×1,85: pierde" in text
