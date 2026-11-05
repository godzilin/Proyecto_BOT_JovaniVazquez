"""Pruebas de bot.cogs.predice: el panel, apostar, resolver, el bucle y lo pendiente.

Se usa la economía real y el repositorio de traducciones real sobre un SQLite temporal, un
reloj falso a las 14:15 del 5 de noviembre de 2026 y las preguntas de verdad de
`tests/fixtures/polymarket/elecciones.json` (más algunas inventadas con `replace`). Polymarket
y el traductor se sustituyen por dobles. El bucle no se arranca solo: cada prueba llama a
`refresh()`, `settle()` o `tick()`.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import ui
from interaction_fakes import fake_interaction

import bot.repositories.sqlite as sqlite_module
from bot.cogs import predice as cog_module
from bot.cogs.predice import (
    CLOSED_TEXT,
    LISTED,
    REFRESH_EVERY,
    SETTLE_EVERY,
    Predice,
    PredictView,
    Resolved,
    StakeModal,
    clip,
    odds_text,
    pending,
    percent_text,
    result_message,
    slip_card,
    win_streak,
)
from bot.repositories.economy import EconomyRepository
from bot.repositories.predice import PredictRepository
from bot.services.babel import TranslationError
from bot.services.economy import STARTING_BALANCE, EconomyService, PredictBet, format_amount
from bot.services.pending import Side as PendingSide
from bot.services.polymarket import (
    Event,
    Market,
    PolymarketError,
    Resolution,
    parse_event,
    parse_time,
)
from bot.services.predice import (
    EVENTS_PER_TAG,
    GAME,
    LOOKAHEAD,
    MAX_STAKE,
    MIN_STAKE,
    SECTIONS,
    VOID_AFTER,
    build_sections,
    payout,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "polymarket"
NOW = parse_time("2026-11-05T14:15:00Z")
assert NOW is not None
END = NOW + 10 * 86_400
GUILD_ID = 1
CHANNEL_ID = 555
OTHER_CHANNEL_ID = 556
ANA, LEO = 10, 11


def elecciones() -> list[Event]:
    raw = json.loads((FIXTURES / "elecciones.json").read_text(encoding="utf-8"))
    parsed = [parse_event(item) for item in raw]
    assert None not in parsed
    return parsed  # type: ignore[return-value]


BASE_EVENT = next(e for e in elecciones() if e.title.startswith("Spain General Election: PP + Vox"))
BASE_MARKET = BASE_EVENT.markets[0]


def make_market(
    market_id: str = "m1",
    *,
    mid: float = 0.62,
    group: str = "",
    question: str | None = None,
    end: float = END,
    **changes: Any,
) -> Market:
    """Un mercado Sí/No apostable, con el punto medio `mid` y un hueco de 0,02."""
    return replace(
        BASE_MARKET,
        id=market_id,
        question=question or f"Will {market_id} happen?",
        group_title=group,
        bid=round(mid - 0.01, 4),
        ask=round(mid + 0.01, 4),
        prices=(mid, 1 - mid),
        end=end,
        game_start=None,
        token_ids=(f"{market_id}-si", f"{market_id}-no"),
        **changes,
    )


def make_event(
    event_id: str, *markets: Market, title: str | None = None, tags: tuple[str, ...] = ("economy",),
    **changes: Any,
) -> Event:  # fmt: skip
    return replace(
        BASE_EVENT,
        id=event_id,
        slug=f"slug-{event_id}",
        title=title or f"Event {event_id}",
        tags=tags,
        start=None,
        end=END,
        image="",
        markets=markets,
        **changes,
    )


SINGLE = make_event(
    "s1",
    make_market("ms1", mid=0.62, question="Will inflation fall below 2%?"),
    title="Will inflation fall?",
)
MULTI = make_event(
    "w1",
    make_market("mw1", mid=0.70, group="PP", question="Will PP win?"),
    make_market("mw2", mid=0.20, group="PSOE", question="Will PSOE win?"),
    make_market("mw3", mid=0.05, group="Vox", question="Will Vox win?"),
    title="Who wins the election?",
    tags=("geopolitics",),
)


class FakeClock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> float:
        return self.now


class FakeClient:
    """Polymarket sin red: lo que se ponga en `by_tag`, `markets` y `quotes`."""

    def __init__(self) -> None:
        self.by_tag: dict[str, Any] = {}
        self.markets: dict[str, Any] = {}
        self.quotes: dict[str, Any] = {}
        self.calls: list[dict[str, Any]] = []

    async def events(self, **kwargs: Any) -> list[Event]:
        self.calls.append(kwargs)
        value = self.by_tag.get(kwargs["tag_slug"], [])
        if isinstance(value, Exception):
            raise value
        return list(value)

    async def market(self, market_id: str) -> Market | None:
        value = self.markets.get(market_id)
        if isinstance(value, Exception):
            raise value
        return value

    async def quote(self, token_id: str) -> tuple[float, float] | None:
        value = self.quotes.get(token_id)
        if isinstance(value, Exception):
            raise value
        return value


class FakeTranslator:
    """Traduce poniendo «ES » delante; falla pasadas `fail_after` llamadas."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.fail_after: int | None = None

    async def __call__(self, text: str, source: str, target: str) -> str:
        self.calls.append((text, source, target))
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise TranslationError("límite de consultas")
        return f"ES {text}"


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


def make_interaction(
    user_id: int = ANA, *, guild_id: int | None = GUILD_ID, events: list[str] | None = None
) -> MagicMock:
    interaction = fake_interaction(make_user(user_id), events=events)
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


def answer(interaction: MagicMock) -> ui.LayoutView:
    """La vista de la tarjeta que se mandó con `notify` tras aceptar el clic."""
    return interaction.followup.send.await_args.kwargs["view"]


def sent_text(channel: MagicMock) -> str:
    """El texto del último mensaje mandado a un canal (dentro de su vista)."""
    return texts(channel.send.await_args.kwargs["view"])


def open_view(
    cog: Predice,
    section: str = "economia",
    event_id: str | None = None,
    market_id: str | None = None,
) -> PredictView:
    view = PredictView(cog, owner_id=ANA, section=section)
    view.event_id = event_id
    view.market_id = market_id
    view.rebuild()
    return view


def load_offers(cog: Predice) -> None:
    """Las preguntas en memoria, como si acabaran de llegar de Polymarket."""
    cog.offers = build_sections(
        {"economia": [SINGLE], "mundo": [MULTI], "espana": elecciones()}, now=NOW
    )
    cog.offers_at = NOW


def set_live(
    cog: Predice, market: Market, bid: float | None = None, ask: float | None = None
) -> None:
    """La cotización en vivo del Sí (por defecto, la del propio mercado)."""
    cog.client.quotes[market.token_ids[0]] = (  # type: ignore[union-attr]
        market.bid if bid is None else bid,
        market.ask if ask is None else ask,
    )


def resolved_market(market: Market, winner: int | None) -> Market:
    """El mercado ya resuelto: gana el Sí (0), el No (1) o se reparte al 50 % (`None`)."""
    prices = (0.5, 0.5) if winner is None else ((1.0, 0.0) if winner == 0 else (0.0, 1.0))
    return replace(
        market, active=False, closed=True, accepting=False,
        resolution=Resolution.RESOLVED, prices=prices,
    )  # fmt: skip


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
    monkeypatch.setattr(cog_module, "TRANSLATE_PAUSE", 0)
    return found


async def make_cog(tmp_path: Path, *, offers: bool = True) -> tuple[Predice, FakeClock]:
    economy_repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await economy_repository.initialize()
    repository = PredictRepository(tmp_path / "bot.db")
    await repository.initialize()
    clock = FakeClock()
    bot = MagicMock()
    channels = {
        CHANNEL_ID: make_channel(CHANNEL_ID),
        OTHER_CHANNEL_ID: make_channel(OTHER_CHANNEL_ID),
    }
    bot.get_channel = MagicMock(side_effect=lambda channel_id: channels.get(channel_id))
    bot.channels = channels
    guild = MagicMock()
    guild.get_member = MagicMock(side_effect=lambda user_id: make_user(user_id))
    bot.get_guild = MagicMock(return_value=guild)
    cog = Predice(
        bot,
        EconomyService(economy_repository),
        repository,
        clock=clock,
        client=FakeClient(),  # type: ignore[arg-type]
        translator=FakeTranslator(),
    )
    if offers:
        load_offers(cog)
        set_live(cog, SINGLE.markets[0])
    return cog, clock


async def stake_on(
    cog: Predice,
    user_id: int,
    event: Event,
    market: Market,
    *,
    side: int = 0,
    stake: int = 100,
    odds: int = 151,
    channel_id: int = CHANNEL_ID,
) -> PredictBet:
    """Apuesta directamente en la economía (sin pasar por el formulario)."""
    ticket = PredictBet(
        id=0,
        guild_id=GUILD_ID,
        user_id=user_id,
        channel_id=channel_id,
        event_id=event.id,
        event_title=event.title,
        market_id=market.id,
        question=market.question,
        side=side,
        side_label=("Sí", "No")[side],
        section="economia",
        odds=odds,
        stake=stake,
        ends=market.end or END,
        created_at=NOW,
    )
    bet, _ = await cog.economy.predict_bet(ticket, game=GAME)
    return bet


async def place(
    cog: Predice,
    interaction: MagicMock,
    *,
    event: Event = SINGLE,
    market: Market | None = None,
    side: int = 0,
    seen: int = 151,
    text: str = "100",
) -> None:
    await cog.place(
        interaction,
        event_id=event.id,
        market_id=(market or event.markets[0]).id,
        side=side,
        seen=seen,
        text=text,
    )


# -- Textos -------------------------------------------------------------------------------


def test_la_cuota_se_escribe_con_coma_y_la_probabilidad_en_enteros() -> None:
    assert odds_text(151) == "×1,51"
    assert percent_text(0.884) == "88 %"
    assert percent_text(0.5) == "50 %"
    assert percent_text(0.0049) == "<1 %"
    assert percent_text(0.0099) == "<1 %"
    assert percent_text(0.01) == "1 %"


def test_clip_corta_con_puntos_suspensivos() -> None:
    assert clip("corto", 10) == "corto"
    assert clip("a" * 20, 10) == "a" * 9 + "…"


async def test_las_opciones_ponen_las_fechas_en_español_o_usan_la_pregunta(
    tmp_path: Path,
) -> None:
    cog, _ = await make_cog(tmp_path)
    dated = make_market("md", group="December 31, 2026")
    plain = make_market("mp", question="Will it rain?")

    assert cog.option_name(dated) == "31 de diciembre de 2026"
    assert cog.option_name(plain) == "Will it rain?"
    cog.translations["Will it rain?"] = "¿Lloverá?"
    assert cog.option_name(plain) == "¿Lloverá?"


# -- Preguntas y traducciones -------------------------------------------------------------


async def test_refresh_rellena_cada_seccion_con_sus_etiquetas(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path, offers=False)
    client = cog.client
    client.by_tag = {"spain": elecciones(), "economy": [SINGLE], "geopolitics": [MULTI]}  # type: ignore[union-attr]

    await cog.refresh(NOW)

    assert [o.event.id for o in cog.offers["economia"]] == ["s1"]
    assert [o.event.id for o in cog.offers["mundo"]] == ["w1"]
    assert {o.event.id for o in cog.offers["espana"]} == {"1138701", "1178432"}
    assert cog.offers["cultura"] == []
    assert cog.offers_at == NOW
    calls = client.calls  # type: ignore[union-attr]
    assert {c["tag_slug"] for c in calls} == {t for s in SECTIONS for t in s.tags}
    for call in calls:
        assert call["closed"] is False
        assert (call["end_min"], call["end_max"]) == (NOW, NOW + LOOKAHEAD)
        assert (call["order"], call["limit"]) == ("volume24hr", EVENTS_PER_TAG)


async def test_si_falla_una_etiqueta_su_seccion_conserva_lo_anterior(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path, offers=False)
    cog.client.by_tag = {"spain": elecciones(), "economy": [SINGLE], "geopolitics": [MULTI]}  # type: ignore[union-attr]
    await cog.refresh(NOW)
    before = cog.offers["economia"]
    other = make_event("s2", make_market("ms2", mid=0.4), title="Another one")
    cog.client.by_tag = {  # type: ignore[union-attr]
        "spain": elecciones(),
        "economy": PolymarketError("caído"),
        "geopolitics": [MULTI, other],
    }

    await cog.refresh(NOW + 60)

    assert cog.offers["economia"] == before
    assert {o.event.id for o in cog.offers["mundo"]} == {"w1", "s2"}
    assert cog.offers_at == NOW + 60


async def test_si_fallan_todas_las_etiquetas_no_cambia_la_hora_de_los_precios(
    tmp_path: Path,
) -> None:
    cog, _ = await make_cog(tmp_path, offers=False)
    cog.client.by_tag = {  # type: ignore[union-attr]
        tag: PolymarketError("caído") for s in SECTIONS for tag in s.tags
    }

    await cog.refresh(NOW)

    assert cog.offers_at is None
    assert all(offers == [] for offers in cog.offers.values())


async def test_refresh_traduce_guarda_y_usa_las_traducciones(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path, offers=False)
    cog.client.by_tag = {"economy": [SINGLE], "geopolitics": [MULTI]}  # type: ignore[union-attr]

    await cog.refresh(NOW)

    assert cog.translations["Will inflation fall?"] == "ES Will inflation fall?"
    assert cog.translations["Will inflation fall below 2%?"] == "ES Will inflation fall below 2%?"
    assert cog.translations["Who wins the election?"] == "ES Who wins the election?"
    assert all(call[1:] == ("en", "es") for call in cog.translator.calls)  # type: ignore[union-attr]
    saved = await cog.repository.translations()
    assert saved == cog.translations
    assert cog.title(SINGLE) == "ES Will inflation fall?"
    assert "ES Will inflation fall?" in texts(open_view(cog))


async def test_lo_ya_traducido_no_se_vuelve_a_pedir(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path, offers=False)
    cog.client.by_tag = {"economy": [SINGLE]}  # type: ignore[union-attr]
    await cog.refresh(NOW)
    first = len(cog.translator.calls)  # type: ignore[union-attr]

    await cog.refresh(NOW + 60)

    assert first == 2
    assert len(cog.translator.calls) == first  # type: ignore[union-attr]


async def test_se_traducen_como_mucho_los_textos_de_cada_ronda(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _ = await make_cog(tmp_path, offers=False)
    monkeypatch.setattr(cog_module, "TRANSLATE_PER_REFRESH", 3)
    cog.client.by_tag = {  # type: ignore[union-attr]
        "economy": [make_event(f"x{n}", make_market(f"mx{n}", mid=0.5)) for n in range(5)]
    }

    await cog.refresh(NOW)

    # Cada pregunta tiene título y pregunta distintos: 10 textos, 3 traducidos.
    assert len(cog.translator.calls) == 3  # type: ignore[union-attr]
    assert len(await cog.repository.translations()) == 3


async def test_un_error_del_traductor_para_la_ronda_sin_romper_el_refresh(
    tmp_path: Path,
) -> None:
    cog, _ = await make_cog(tmp_path, offers=False)
    cog.translator.fail_after = 1  # type: ignore[union-attr]
    cog.client.by_tag = {"economy": [SINGLE], "geopolitics": [MULTI]}  # type: ignore[union-attr]

    await cog.refresh(NOW)

    assert len(cog.translator.calls) == 2  # type: ignore[union-attr]
    assert len(cog.translations) == 1
    assert [o.event.id for o in cog.offers["economia"]] == ["s1"]
    assert cog.offers_at == NOW
    assert len(await cog.repository.translations()) == 1


async def test_al_cargar_se_leen_las_traducciones_guardadas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _ = await make_cog(tmp_path)
    await cog.repository.save_translation("Will inflation fall?", "¿Bajará la inflación?", now=NOW)
    monkeypatch.setattr(type(cog._feed), "start", MagicMock())  # noqa: SLF001

    await cog.cog_load()

    assert cog.translations == {"Will inflation fall?": "¿Bajará la inflación?"}
    assert cog.title(SINGLE) == "¿Bajará la inflación?"
    await cog.cog_unload()


async def test_tick_pide_las_preguntas_si_toca_y_resuelve_si_toca(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path, offers=False)
    cog.refresh = AsyncMock()  # type: ignore[method-assign]
    cog.settle = AsyncMock()  # type: ignore[method-assign]

    await cog.tick()
    cog.refresh.assert_awaited_once_with(NOW)
    cog.settle.assert_awaited_once_with(NOW)
    cog.offers_at = NOW

    clock.now = NOW + 60
    await cog.tick()
    assert cog.refresh.await_count == 1
    assert cog.settle.await_count == 1

    clock.now = NOW + SETTLE_EVERY
    await cog.tick()
    assert cog.refresh.await_count == 1
    assert cog.settle.await_count == 2

    clock.now = NOW + REFRESH_EVERY
    await cog.tick()
    assert cog.refresh.await_count == 2


# -- El panel -----------------------------------------------------------------------------


@pytest.mark.parametrize("section", [s.key for s in SECTIONS])
async def test_el_panel_se_construye_en_cada_seccion_dentro_de_los_limites(
    tmp_path: Path, section: str
) -> None:
    cog, _ = await make_cog(tmp_path)

    view = open_view(cog, section)

    assert len(list(view.walk_children())) <= 40
    assert view.content_length() <= 4_000
    assert all(i.startswith("predice:") for i in ids(view))
    assert len(ids(view)) == len(set(ids(view)))
    assert f"predice:seccion:{section}" in ids(view)
    assert widget(view, f"predice:seccion:{section}").disabled is True
    assert "predice:mis" in ids(view)
    assert "Polymarket" in texts(view)


async def test_la_portada_enseña_las_preguntas_con_sus_dos_primeras_opciones(
    tmp_path: Path,
) -> None:
    cog, _ = await make_cog(tmp_path)

    mundo = texts(open_view(cog, "mundo"))
    economia = texts(open_view(cog, "economia"))

    assert "🔮 **Predicciones** · 🌍 Mundo" in mundo
    assert "**Who wins the election?**" in mundo
    assert "PP 70 % · PSOE 20 %" in mundo
    assert "Vox" not in mundo
    assert f"cierra <t:{int(END)}:R>" in mundo
    assert "Will inflation fall?" in economia
    assert "Sí 62 %" in economia
    assert "Precios de hace menos de 1 min" in economia


async def test_la_portada_dice_hace_cuanto_son_los_precios(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    clock.now = NOW + 5 * 60

    assert "Precios de hace 5 min" in texts(open_view(cog))


async def test_sin_preguntas_la_portada_lo_dice_con_una_linea_normal(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)

    view = open_view(cog, "cultura")

    assert "no hay preguntas abiertas" in texts(view)
    assert not any(i.startswith("predice:evento:") for i in ids(view))


async def test_antes_de_la_primera_carga_pone_cargando(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path, offers=False)

    assert "Cargando preguntas…" in texts(open_view(cog, "cultura"))


async def test_la_portada_pone_seis_preguntas_y_el_resto_en_un_desplegable(
    tmp_path: Path,
) -> None:
    cog, _ = await make_cog(tmp_path)
    many = [
        make_event(f"x{n}", make_market(f"mx{n}", mid=0.5), volume_24h=1000.0 - n)
        for n in range(25)
    ]
    cog.offers["economia"] = build_sections({"economia": many}, now=NOW)["economia"]

    view = open_view(cog)

    assert sorted(i for i in ids(view) if i.startswith("predice:evento:")) == sorted(
        f"predice:evento:x{n}" for n in range(LISTED)
    )
    select = widget(view, "predice:mas")
    assert isinstance(select, ui.Select)
    assert len(select.options) == 25 - LISTED
    assert len(list(view.walk_children())) <= 40
    assert view.content_length() <= 4_000


async def test_con_titulos_enormes_el_panel_cabe_en_los_limites(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    many = [
        make_event(f"x{n}", make_market(f"mx{n}", mid=0.5), title="T" * 400, volume_24h=n)
        for n in range(25)
    ]
    big = make_event(
        "big",
        *(make_market(f"b{n}", mid=0.04, group="G" * 200) for n in range(25)),
        title="B" * 400,
    )
    cog.offers["economia"] = build_sections({"economia": [*many, big]}, now=NOW)["economia"]

    home = open_view(cog)
    event = open_view(cog, event_id="big")

    for view in (home, event):
        assert len(list(view.walk_children())) <= 40
        assert view.content_length() <= 4_000
    select = widget(event, "predice:opcion")
    assert isinstance(select, ui.Select)
    assert len(select.options) == 25
    assert all(len(o.label) <= 100 for o in select.options)


async def test_el_panel_se_abre_en_la_primera_seccion_con_preguntas(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    interaction = make_interaction()

    await cog.predice.callback(cog, interaction)

    view = interaction.response.send_message.await_args.kwargs["view"]
    assert isinstance(view, PredictView)
    assert view.section == "espana"
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    assert view.message is interaction.original_response.return_value


async def test_abrir_el_panel_no_toca_la_base_de_datos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _ = await make_cog(tmp_path)
    monkeypatch.setattr(sqlite_module, "connect", lambda *a, **k: pytest.fail("toca la base"))
    interaction = make_interaction()

    await cog.predice.callback(cog, interaction)

    interaction.response.defer.assert_not_awaited()


async def test_la_ficha_de_una_pregunta_sola_enseña_el_si_y_el_no_con_su_cuota(
    tmp_path: Path,
) -> None:
    cog, _ = await make_cog(tmp_path)

    view = open_view(cog, event_id="s1")

    text = texts(view)
    assert "## Will inflation fall?" in text
    assert "Will inflation fall below 2%?" in text
    assert "**Sí 62 %** · No 38 %" in text
    assert widget(view, "predice:si").label == "Sí ×1,51"
    assert widget(view, "predice:no").label == "No ×2,47"
    assert "predice:volver" in ids(view)
    [link] = [w for w in widgets(view) if w.custom_id is None]
    assert link.label == "Reglas en Polymarket"
    assert link.url == "https://polymarket.com/event/slug-s1"
    assert len(list(view.walk_children())) <= 40


async def test_la_ficha_lleva_la_miniatura_si_el_evento_tiene_imagen(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.offers["economia"] = build_sections(
        {"economia": [replace(SINGLE, image="https://example.com/a.png")]}, now=NOW
    )["economia"]

    view = open_view(cog, event_id="s1")

    [thumb] = [i for i in view.walk_children() if isinstance(i, ui.Thumbnail)]
    assert thumb.media.url == "https://example.com/a.png"
    assert not any(isinstance(i, ui.Thumbnail) for i in open_view(cog, "mundo").walk_children())


async def test_solo_se_ofrecen_los_lados_que_tienen_cuota(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    favorite = make_event("f1", make_market("mf1", mid=0.945), title="Almost sure")
    cog.offers["economia"] = build_sections({"economia": [favorite]}, now=NOW)["economia"]

    view = open_view(cog, event_id="f1")

    assert "predice:si" not in ids(view)
    assert "predice:no" in ids(view)


async def test_la_ficha_de_varias_opciones_las_lista_y_deja_elegir(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)

    view = open_view(cog, "mundo", event_id="w1")

    text = texts(view)
    assert "**PP** — 70 %" in text
    assert "**PSOE** — 20 %" in text
    assert "**Vox** — 5 %" in text
    select = widget(view, "predice:opcion")
    assert isinstance(select, ui.Select)
    assert select.placeholder == "Elige una opción…"
    assert [o.value for o in select.options] == ["mw1", "mw2", "mw3"]
    assert "predice:si" not in ids(view)


async def test_elegir_una_opcion_enseña_su_ficha_con_los_dos_lados_y_volver_sube_un_nivel(
    tmp_path: Path,
) -> None:
    cog, _ = await make_cog(tmp_path)
    view = open_view(cog, "mundo", event_id="w1")
    select = widget(view, "predice:opcion")
    select._values = ["mw2"]  # type: ignore[attr-defined]  # noqa: SLF001
    interaction = make_interaction()

    await select.callback(interaction)

    interaction.response.edit_message.assert_awaited_once()
    text = texts(view)
    assert "**PSOE**" in text
    assert "**Sí 20 %** · No 80 %" in text
    assert widget(view, "predice:si").label == "Sí ×4,70"
    assert widget(view, "predice:no").label == "No ×1,17"
    assert "predice:opcion" not in ids(view)

    await widget(view, "predice:volver").callback(make_interaction())
    assert view.event_id == "w1"
    assert view.market_id is None
    assert "predice:opcion" in ids(view)

    await widget(view, "predice:volver").callback(make_interaction())
    assert view.event_id is None
    assert "predice:evento:w1" in ids(view)


async def test_navegar_responde_directo_con_edit_message_sin_tocar_la_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _ = await make_cog(tmp_path)
    view = open_view(cog)
    monkeypatch.setattr(sqlite_module, "connect", lambda *a, **k: pytest.fail("toca la base"))

    for custom_id, check in (
        ("predice:evento:s1", lambda: view.event_id == "s1"),
        ("predice:volver", lambda: view.event_id is None),
        ("predice:seccion:mundo", lambda: view.section == "mundo"),
    ):
        interaction = make_interaction()
        await widget(view, custom_id).callback(interaction)
        interaction.response.edit_message.assert_awaited_once_with(view=view)
        interaction.response.defer.assert_not_awaited()
        assert check()


async def test_el_desplegable_de_mas_preguntas_abre_la_elegida(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    many = [
        make_event(f"x{n}", make_market(f"mx{n}", mid=0.5), volume_24h=1000.0 - n)
        for n in range(10)
    ]
    cog.offers["economia"] = build_sections({"economia": many}, now=NOW)["economia"]
    view = open_view(cog)
    select = widget(view, "predice:mas")
    select._values = ["x8"]  # type: ignore[attr-defined]  # noqa: SLF001
    interaction = make_interaction()

    await select.callback(interaction)

    interaction.response.edit_message.assert_awaited_once()
    assert view.event_id == "x8"
    assert "## Event x8" in texts(view)


async def test_una_pregunta_que_se_cierra_vuelve_a_la_portada(
    tmp_path: Path,
) -> None:
    cog, clock = await make_cog(tmp_path)
    view = open_view(cog, event_id="s1")
    clock.now = END + 1

    view.rebuild()

    assert view.event_id is None
    assert "Will inflation fall?" not in texts(view)


async def test_otra_persona_no_maneja_el_panel(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    view = open_view(cog)
    interaction = make_interaction(LEO)

    assert await view.interaction_check(interaction) is False
    assert "/predice" in interaction.response.send_message.await_args.args[0]
    assert await view.interaction_check(make_interaction(ANA)) is True


async def test_pulsar_un_lado_abre_el_formulario_como_primera_respuesta(
    tmp_path: Path,
) -> None:
    cog, _ = await make_cog(tmp_path)
    view = open_view(cog, event_id="s1")
    interaction = make_interaction()

    await widget(view, "predice:no").callback(interaction)

    interaction.response.defer.assert_not_awaited()
    interaction.response.send_modal.assert_awaited_once()
    modal = interaction.response.send_modal.await_args.args[0]
    assert isinstance(modal, StakeModal)
    assert modal.title == "No ×2,47"
    assert (modal.market_id, int(modal.side), modal.odds) == ("ms1", 1, 247)


async def test_el_formulario_de_una_opcion_lleva_su_nombre(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    view = open_view(cog, "mundo", event_id="w1", market_id="mw1")
    interaction = make_interaction()

    await widget(view, "predice:si").callback(interaction)

    assert interaction.response.send_modal.await_args.args[0].title == "Sí ×1,34 · PP"


async def test_el_formulario_apuesta_a_la_cuota_que_vio(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.place = AsyncMock()  # type: ignore[method-assign]
    view = open_view(cog, event_id="s1")
    interaction = make_interaction()
    await widget(view, "predice:si").callback(interaction)
    modal = interaction.response.send_modal.await_args.args[0]
    modal.amount._value = "50"  # noqa: SLF001
    submit = make_interaction()

    await modal.on_submit(submit)

    cog.place.assert_awaited_once_with(
        submit, event_id="s1", market_id="ms1", side=0, seen=151, text="50"
    )


async def test_un_lado_de_una_pregunta_cerrada_no_abre_el_formulario(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    view = open_view(cog, event_id="s1")
    clock.now = END + 1
    interaction = make_interaction()

    await widget(view, "predice:si").callback(interaction)

    interaction.response.send_modal.assert_not_awaited()
    assert interaction.response.send_message.await_args.args[0] == CLOSED_TEXT


async def test_el_comando_de_texto_manda_el_panel_al_canal(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    ctx = MagicMock()
    ctx.author = make_user(ANA)
    ctx.channel = make_channel()
    ctx.send = AsyncMock()

    await cog.predice_text.callback(cog, ctx)

    ctx.send.assert_awaited_once()
    assert isinstance(ctx.send.await_args.kwargs["view"], PredictView)


async def test_fuera_del_canal_del_casino_no_se_abre(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.casino_channel_ids = frozenset({999})
    interaction = make_interaction()

    await cog.predice.callback(cog, interaction)

    assert "están en <#999>" in interaction.response.send_message.await_args.args[0]
    assert "Las predicciones" in interaction.response.send_message.await_args.args[0]


async def test_los_comandos_se_llaman_igual_con_barra_y_con_punto(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)

    assert cog.predice.name == "predice"
    assert cog.predice_text.name == "predice"
    assert not cog.predice_text.aliases


async def test_al_caducar_se_apagan_los_botones_pero_no_el_enlace(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    view = open_view(cog, event_id="s1")
    view.message = MagicMock(edit=AsyncMock())

    await view.on_timeout()

    assert all(w.disabled for w in widgets(view) if w.custom_id)
    view.message.edit.assert_awaited_once_with(view=view)


# -- Apostar ------------------------------------------------------------------------------


async def test_apostar_cobra_guarda_la_apuesta_y_contesta_antes_de_la_renta(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.translations["Will inflation fall?"] = "¿Bajará la inflación?"
    events: list[str] = []
    interaction = make_interaction(events=events)
    hooks.remind.side_effect = lambda *a, **k: events.append("renta.remind")

    await place(cog, interaction)

    # Aceptado antes de tocar nada y la tarjeta, antes de la Renta.
    assert events == ["response.defer", "followup.send", "renta.remind"]
    text = texts(answer(interaction))
    assert "×1,51" in text
    assert format_amount(100) in text
    assert format_amount(payout(100, 151)) in text
    assert f"<t:{int(END)}:R>" in text
    assert format_amount(STARTING_BALANCE - 100) in text
    assert "retención del IRPF del juego" in text
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 100
    [bet] = await cog.economy.open_predict_bets()
    assert (bet.user_id, bet.channel_id, bet.event_id, bet.market_id) == (
        ANA,
        CHANNEL_ID,
        "s1",
        "ms1",
    )
    assert (bet.odds, bet.stake, bet.side, bet.side_label) == (151, 100, 0, "Sí")
    assert (bet.event_title, bet.question) == ("¿Bajará la inflación?", "¿Bajará la inflación?")
    assert (bet.section, bet.ends) == ("economia", END)
    hooks.remind.assert_awaited_once()
    hooks.track.assert_awaited_once()


async def test_apostar_alimenta_los_logros_con_lo_que_se_hizo(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    cog, _ = await make_cog(tmp_path)

    await place(cog, make_interaction(), text="500")

    delta = hooks.track.await_args.args[4]
    assert delta.add["predict_bets"] == 1
    assert delta.add["predict_staked"] == 500
    assert delta.add["predict_side_yes"] == 1


async def test_apostar_a_una_opcion_guarda_su_nombre_y_el_titulo(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.translations["Who wins the election?"] = "¿Quién gana las elecciones?"
    market = MULTI.markets[1]
    set_live(cog, market)

    await place(cog, make_interaction(), event=MULTI, market=market, side=1, seen=117)

    [bet] = await cog.economy.open_predict_bets()
    assert (bet.event_title, bet.question) == ("¿Quién gana las elecciones?", "PSOE")
    assert (bet.side, bet.side_label, bet.odds, bet.section) == (1, "No", 117, "mundo")


async def test_un_precio_peor_se_rechaza_con_la_cuota_nueva_y_sin_cobrar(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    cog, _ = await make_cog(tmp_path)
    set_live(cog, SINGLE.markets[0], 0.65, 0.67)  # el Sí sube: su cuota baja a ×1,42
    interaction = make_interaction()

    await place(cog, interaction, seen=151)

    assert "La cuota ha cambiado" in said(interaction)
    assert "×1,42" in said(interaction)
    assert "antes, ×1,51" in said(interaction)
    assert "Vuelve a pulsarlo" in said(interaction)
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE
    assert await cog.economy.open_predict_bets() == []
    hooks.remind.assert_not_awaited()
    hooks.track.assert_not_awaited()


async def test_un_precio_mejor_se_acepta_a_la_cuota_que_vio(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    set_live(cog, SINGLE.markets[0], 0.57, 0.59)  # el Sí baja: su cuota sube a ×1,62
    interaction = make_interaction()

    await place(cog, interaction, seen=151)

    [bet] = await cog.economy.open_predict_bets()
    assert bet.odds == 151


async def test_sin_cotizacion_no_se_cobra_nada(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client.quotes.clear()  # type: ignore[union-attr]
    interaction = make_interaction()

    await place(cog, interaction)

    assert "No he podido confirmar el precio" in said(interaction)
    assert "No se ha cobrado nada" in said(interaction)
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE


async def test_si_polymarket_falla_al_confirmar_el_precio_no_se_cobra(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.client.quotes[SINGLE.markets[0].token_ids[0]] = PolymarketError("caído")  # type: ignore[union-attr]
    interaction = make_interaction()

    await place(cog, interaction)

    assert "No he podido confirmar el precio" in said(interaction)
    assert await cog.economy.open_predict_bets() == []


async def test_con_un_hueco_demasiado_ancho_no_hay_precio_fiable(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    set_live(cog, SINGLE.markets[0], 0.50, 0.70)
    interaction = make_interaction()

    await place(cog, interaction)

    assert "no hay un precio fiable" in said(interaction)
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE


async def test_una_pregunta_cerrada_no_admite_predicciones(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    clock.now = END + 1
    interaction = make_interaction()

    await place(cog, interaction)

    assert said(interaction) == CLOSED_TEXT
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE


async def test_una_pregunta_que_ya_no_esta_no_admite_predicciones(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    cog.offers["economia"] = []
    interaction = make_interaction()

    await place(cog, interaction)

    assert said(interaction) == CLOSED_TEXT


async def test_no_se_apuesta_fuera_de_un_servidor(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    interaction = make_interaction(guild_id=None)

    await place(cog, interaction)

    assert "dentro de un servidor" in said(interaction)
    assert await cog.economy.open_predict_bets() == []


async def test_una_segunda_prediccion_a_la_misma_pregunta_avisa_sin_cobrar(
    tmp_path: Path,
) -> None:
    cog, _ = await make_cog(tmp_path)
    await place(cog, make_interaction())
    interaction = make_interaction()

    await place(cog, interaction, side=1, seen=247)

    assert "Ya tienes una predicción a esta pregunta" in said(interaction)
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 100
    assert len(await cog.economy.open_predict_bets()) == 1


async def test_otra_pregunta_del_mismo_evento_se_cobra_y_cuenta_para_los_logros(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    cog, _ = await make_cog(tmp_path)
    for market in MULTI.markets[:2]:
        set_live(cog, market)
    await place(cog, make_interaction(), event=MULTI, market=MULTI.markets[0], seen=134)
    interaction = make_interaction()

    await place(cog, interaction, event=MULTI, market=MULTI.markets[1], seen=470)

    assert len(await cog.economy.open_predict_bets()) == 2
    assert hooks.track.await_args.args[4].peak == {"predict_event_markets_max": 2}


async def test_el_tope_por_evento_suma_las_preguntas_del_mismo_evento(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    await cog.economy.grant(GUILD_ID, ANA, amount=200_000, reason="prueba")
    for market in MULTI.markets[:2]:
        set_live(cog, market)
    await place(
        cog, make_interaction(), event=MULTI, market=MULTI.markets[0], seen=134, text="40000"
    )
    interaction = make_interaction()

    await place(cog, interaction, event=MULTI, market=MULTI.markets[1], seen=470, text="20000")

    assert "en este te caben" in said(interaction)
    assert format_amount(MAX_STAKE - 40_000) in said(interaction)
    assert len(await cog.economy.open_predict_bets()) == 1


async def test_con_el_tope_lleno_avisa_de_que_no_cabe_mas(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    await cog.economy.grant(GUILD_ID, ANA, amount=200_000, reason="prueba")
    for market in MULTI.markets[:2]:
        set_live(cog, market)
    await place(
        cog, make_interaction(), event=MULTI, market=MULTI.markets[0], seen=134,
        text=str(MAX_STAKE),
    )  # fmt: skip
    interaction = make_interaction()

    await place(cog, interaction, event=MULTI, market=MULTI.markets[1], seen=470, text="all")

    assert "Ya llevas" in said(interaction)


async def test_all_se_recorta_a_lo_que_cabe_en_el_evento(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    await cog.economy.grant(GUILD_ID, ANA, amount=200_000, reason="prueba")
    before = await cog.economy.balance(GUILD_ID, ANA)
    for market in MULTI.markets[:2]:
        set_live(cog, market)
    await place(
        cog, make_interaction(), event=MULTI, market=MULTI.markets[0], seen=134, text="30000"
    )

    await place(cog, make_interaction(), event=MULTI, market=MULTI.markets[1], seen=470, text="all")

    stakes = sorted(b.stake for b in await cog.economy.open_predict_bets())
    assert stakes == [20_000, 30_000]
    assert await cog.economy.balance(GUILD_ID, ANA) == before - MAX_STAKE


async def test_all_con_poco_saldo_apuesta_todo_lo_que_hay(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)

    await place(cog, make_interaction(), text="all")

    [bet] = await cog.economy.open_predict_bets()
    assert bet.stake == STARTING_BALANCE
    assert await cog.economy.balance(GUILD_ID, ANA) == 0


@pytest.mark.parametrize("text", [str(MIN_STAKE - 1), str(MAX_STAKE + 1), "0", "1m"])
async def test_una_cantidad_fuera_del_rango_avisa_sin_cobrar(tmp_path: Path, text: str) -> None:
    cog, _ = await make_cog(tmp_path)
    await cog.economy.grant(GUILD_ID, ANA, amount=2_000_000, reason="prueba")
    before = await cog.economy.balance(GUILD_ID, ANA)
    interaction = make_interaction()

    await place(cog, interaction, text=text)

    assert interaction.followup.send.await_count == 1
    assert await cog.economy.balance(GUILD_ID, ANA) == before
    assert await cog.economy.open_predict_bets() == []


async def test_el_rango_avisa_con_el_minimo_y_el_maximo(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    interaction = make_interaction()

    await place(cog, interaction, text="5")

    assert format_amount(MIN_STAKE) in said(interaction)
    assert format_amount(MAX_STAKE) in said(interaction)


async def test_si_no_le_llega_avisa_con_su_saldo_sin_cobrar(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    interaction = make_interaction()

    await place(cog, interaction, text="5000")

    assert "No te llega" in said(interaction)
    assert format_amount(STARTING_BALANCE) in said(interaction)
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE


async def test_una_cantidad_que_no_se_entiende_avisa(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    interaction = make_interaction()

    await place(cog, interaction, text="mucho")

    assert "No entiendo" in said(interaction)


async def test_la_mascota_opina_en_la_tarjeta_de_la_apuesta(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    cog, _ = await make_cog(tmp_path)
    hooks.hint.return_value = "-# 🐶 Ladrido de prueba"
    interaction = make_interaction()

    await place(cog, interaction)

    assert "Ladrido de prueba" in texts(answer(interaction))
    assert hooks.hint.await_args.args[1:3] == (GUILD_ID, ANA)


# -- Mis predicciones ---------------------------------------------------------------------


async def test_mis_predicciones_usa_un_mensaje_privado_nuevo_y_lista_abiertas_primero(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    cog, clock = await make_cog(tmp_path)
    market = SINGLE.markets[0]
    set_live(cog, market)
    done = await stake_on(cog, ANA, MULTI, MULTI.markets[0], stake=50, odds=134)
    await cog.economy.settle_predict_bet(GUILD_ID, done.id, payout=payout(50, 134), game=GAME)
    await stake_on(cog, ANA, SINGLE, market)
    interaction = make_interaction()
    view = open_view(cog)

    await widget(view, "predice:mis").callback(interaction)

    interaction.response.defer.assert_awaited_once()
    assert interaction.response.defer.await_args.kwargs == {"ephemeral": True, "thinking": True}
    text = texts(interaction.edit_original_response.await_args.kwargs["view"])
    assert text.index("Esperando resultado") < text.index("Últimas resueltas")
    assert (
        "⏳ 100 Y$ a Sí en «Will inflation fall?» — Will inflation fall below 2%? a ×1,51" in text
    )
    assert f"(cobrarías {format_amount(payout(100, 151))})" in text
    assert f"se resuelve hacia <t:{int(END)}:R>" in text
    assert "✅ 50 Y$ a Sí en «Who wins the election?» — Will PP win? a ×1,34 · cobraste" in text
    assert hooks.track.await_args.args[4].add == {"predict_slips": 1}
    assert clock.now == NOW


async def test_mis_predicciones_sin_ninguna_lo_dice(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    interaction = make_interaction()

    await cog.slip(interaction)

    assert "Aún no has hecho ninguna predicción" in texts(
        interaction.edit_original_response.await_args.kwargs["view"]
    )


async def test_la_ficha_de_mis_predicciones_enseña_cada_estado() -> None:
    def bet(bet_id: int, status: str, **changes: Any) -> PredictBet:
        return PredictBet(
            id=bet_id, guild_id=1, user_id=ANA, channel_id=2, event_id="e", event_title="Título",
            market_id=f"m{bet_id}", question="Título", side=0, side_label="Sí", section="espana",
            odds=200, stake=100, ends=END, created_at=NOW, status=status, **changes,
        )  # fmt: skip

    view = slip_card([bet(1, "won", payout=200), bet(2, "lost"), bet(3, "void", payout=100)], "Ana")

    text = texts(view)
    assert "✅ 100 Y$ a Sí en «Título» a ×2,00 · cobraste **200 Y$**" in text
    assert "❌ 100 Y$ a Sí en «Título» a ×2,00" in text
    assert "↩️ 100 Y$ a Sí en «Título» · anulada y devuelta" in text


# -- Resolver -----------------------------------------------------------------------------


async def two_bets(cog: Predice) -> tuple[Market, PredictBet, PredictBet]:
    market = SINGLE.markets[0]
    ana = await stake_on(cog, ANA, SINGLE, market)
    leo = await stake_on(cog, LEO, SINGLE, market, side=1, odds=247)
    return market, ana, leo


async def test_settle_paga_a_quien_acierta_y_no_a_quien_falla(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    market, _, _ = await two_bets(cog)
    cog.client.markets[market.id] = resolved_market(market, 0)  # type: ignore[union-attr]
    clock.now = END + 3_600

    resolved = await cog.settle(clock.now)

    assert {r.bet.user_id: r.bet.status for r in resolved} == {ANA: "won", LEO: "lost"}
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 100 + payout(100, 151)
    assert await cog.economy.balance(GUILD_ID, LEO) == STARTING_BALANCE - 100
    assert await cog.economy.open_predict_bets() == []


async def test_settle_manda_un_solo_mensaje_por_pregunta_y_canal_y_menciona_a_quien_cobra(
    tmp_path: Path,
) -> None:
    cog, clock = await make_cog(tmp_path)
    market, _, _ = await two_bets(cog)
    cog.client.markets[market.id] = resolved_market(market, 0)  # type: ignore[union-attr]
    clock.now = END + 3_600

    await cog.settle(clock.now)

    channel = cog.bot.channels[CHANNEL_ID]
    channel.send.assert_awaited_once()
    assert channel.send.await_args.args == ()
    text = sent_text(channel)
    assert "🔮 **Will inflation fall?** — Will inflation fall below 2%?" in text
    assert "Resultado: **Sí**" in text
    assert f"✅ <@{ANA}> Sí a ×1,51: cobra **{format_amount(151)}** (+{format_amount(51)})" in text
    assert f"❌ <@{LEO}> No a ×2,47: pierde {format_amount(100)}" in text
    mentions = channel.send.await_args.kwargs["allowed_mentions"]
    assert [user.id for user in mentions.users] == [ANA]
    assert mentions.everyone is False
    assert mentions.roles is False
    assert "Polymarket" not in text and "$" not in text.replace("Y$", "")


async def test_settle_gana_el_no_cuando_el_mercado_resuelve_a_no(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    market, _, _ = await two_bets(cog)
    cog.client.markets[market.id] = resolved_market(market, 1)  # type: ignore[union-attr]
    clock.now = END + 3_600

    resolved = await cog.settle(clock.now)

    assert {r.bet.user_id: r.bet.status for r in resolved} == {ANA: "lost", LEO: "won"}
    assert "Resultado: **No**" in sent_text(cog.bot.channels[CHANNEL_ID])


async def test_si_solo_pierden_no_se_menciona_a_nadie(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    market = SINGLE.markets[0]
    await stake_on(cog, ANA, SINGLE, market)
    cog.client.markets[market.id] = resolved_market(market, 1)  # type: ignore[union-attr]
    clock.now = END + 3_600

    await cog.settle(clock.now)

    channel = cog.bot.channels[CHANNEL_ID]
    assert channel.send.await_args.kwargs["allowed_mentions"].users == []
    assert f"❌ <@{ANA}>" in sent_text(channel)


async def test_settle_separa_los_canales(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    market = SINGLE.markets[0]
    await stake_on(cog, ANA, SINGLE, market)
    await stake_on(cog, LEO, SINGLE, market, channel_id=OTHER_CHANNEL_ID)
    cog.client.markets[market.id] = resolved_market(market, 0)  # type: ignore[union-attr]
    clock.now = END + 3_600

    await cog.settle(clock.now)

    for channel_id, user in ((CHANNEL_ID, ANA), (OTHER_CHANNEL_ID, LEO)):
        channel = cog.bot.channels[channel_id]
        channel.send.assert_awaited_once()
        assert f"<@{user}>" in sent_text(channel)


@pytest.mark.parametrize("split", [True, False])
async def test_una_pregunta_al_50_o_caducada_devuelve_lo_apostado(
    tmp_path: Path, split: bool
) -> None:
    cog, clock = await make_cog(tmp_path)
    market = SINGLE.markets[0]
    await stake_on(cog, ANA, SINGLE, market)
    if split:
        cog.client.markets[market.id] = resolved_market(market, None)  # type: ignore[union-attr]
        clock.now = END + 3_600
    else:
        cog.client.markets[market.id] = market  # type: ignore[union-attr]
        clock.now = END + VOID_AFTER

    resolved = await cog.settle(clock.now)

    [item] = resolved
    assert item.bet.status == "void"
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE
    text = sent_text(cog.bot.channels[CHANNEL_ID])
    assert f"↩️ <@{ANA}> recupera {format_amount(100)}" in text
    assert ("se resolvió al 50 %" if split else "no se resolvió a tiempo") in text
    mentions = cog.bot.channels[CHANNEL_ID].send.await_args.kwargs["allowed_mentions"]
    assert [user.id for user in mentions.users] == [ANA]


async def test_un_mercado_desaparecido_se_anula_pasado_el_plazo(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    await stake_on(cog, ANA, SINGLE, SINGLE.markets[0])
    clock.now = END + VOID_AFTER

    resolved = await cog.settle(clock.now)

    assert [r.bet.status for r in resolved] == ["void"]


async def test_si_polymarket_falla_las_predicciones_siguen_abiertas(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    market = SINGLE.markets[0]
    await stake_on(cog, ANA, SINGLE, market)
    cog.client.markets[market.id] = PolymarketError("caído")  # type: ignore[union-attr]
    clock.now = END + VOID_AFTER + 1

    resolved = await cog.settle(clock.now)

    assert resolved == []
    assert len(await cog.economy.open_predict_bets()) == 1
    cog.bot.channels[CHANNEL_ID].send.assert_not_awaited()


async def test_un_mercado_que_falla_no_frena_a_los_demas(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    first, second = MULTI.markets[0], SINGLE.markets[0]
    await stake_on(cog, ANA, MULTI, first, odds=134)
    await stake_on(cog, ANA, SINGLE, second)
    cog.client.markets[first.id] = PolymarketError("caído")  # type: ignore[union-attr]
    cog.client.markets[second.id] = resolved_market(second, 0)  # type: ignore[union-attr]
    clock.now = END + 3_600

    resolved = await cog.settle(clock.now)

    assert [r.bet.market_id for r in resolved] == [second.id]
    assert [b.market_id for b in await cog.economy.open_predict_bets()] == [first.id]


async def test_un_mercado_sin_resolver_aun_no_se_toca(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    market = SINGLE.markets[0]
    await stake_on(cog, ANA, SINGLE, market)
    cog.client.markets[market.id] = replace(market, closed=True, resolution=Resolution.PENDING)  # type: ignore[union-attr]
    clock.now = END + 3_600

    assert await cog.settle(clock.now) == []
    assert len(await cog.economy.open_predict_bets()) == 1


async def test_una_prediccion_nunca_se_resuelve_dos_veces(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    market = SINGLE.markets[0]
    await stake_on(cog, ANA, SINGLE, market)
    cog.client.markets[market.id] = resolved_market(market, 0)  # type: ignore[union-attr]
    clock.now = END + 3_600

    first = await cog.settle(clock.now)
    balance = await cog.economy.balance(GUILD_ID, ANA)
    second = await cog.settle(clock.now)

    assert len(first) == 1
    assert second == []
    assert await cog.economy.balance(GUILD_ID, ANA) == balance
    cog.bot.channels[CHANNEL_ID].send.assert_awaited_once()


async def test_si_otro_la_cierra_a_la_vez_no_se_paga_dos_veces(tmp_path: Path) -> None:
    cog, clock = await make_cog(tmp_path)
    market = SINGLE.markets[0]
    bet = await stake_on(cog, ANA, SINGLE, market)
    cog.client.markets[market.id] = resolved_market(market, 0)  # type: ignore[union-attr]
    await cog.economy.settle_predict_bet(GUILD_ID, bet.id, payout=151, game=GAME)
    balance = await cog.economy.balance(GUILD_ID, ANA)

    assert await cog._close(bet, cog_module.Verdict.WON, resolved_market(market, 0)) is None  # noqa: SLF001
    assert await cog._close(bet, cog_module.Verdict.VOID, market) is None  # noqa: SLF001
    assert await cog.economy.balance(GUILD_ID, ANA) == balance


async def test_si_el_premio_pasa_el_saldo_maximo_se_anula_y_se_devuelve(
    tmp_path: Path,
) -> None:
    from bot.services.economy import BalanceLimitError

    cog, clock = await make_cog(tmp_path)
    market = SINGLE.markets[0]
    await stake_on(cog, ANA, SINGLE, market)
    cog.client.markets[market.id] = resolved_market(market, 0)  # type: ignore[union-attr]
    cog.economy.settle_predict_bet = AsyncMock(side_effect=BalanceLimitError)  # type: ignore[method-assign]
    clock.now = END + 3_600

    [item] = await cog.settle(clock.now)

    assert item.bet.status == "void"
    assert item.reason == "limit"
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE


async def test_settle_apunta_logros_y_estadisticas_del_casino(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    cog, clock = await make_cog(tmp_path)
    market, _, _ = await two_bets(cog)
    cog.client.markets[market.id] = resolved_market(market, 0)  # type: ignore[union-attr]
    clock.now = END + 3_600

    await cog.settle(clock.now)

    assert hooks.casino_play.await_count == 2
    assert hooks.record.await_count == 2
    won = next(c for c in hooks.record.await_args_list if c.kwargs["net"] > 0)
    assert won.kwargs["game"] == GAME
    assert (won.kwargs["stake"], won.kwargs["net"]) == (100, 51)
    delta = next(c.args[4] for c in hooks.casino_play.await_args_list if c.kwargs["net"] > 0)
    assert delta.add["predict_wins"] == 1
    assert delta.add["predict_settled"] == 1
    assert delta.add["casino_wagered"] == 100
    assert delta.peak["predict_win_streak_max"] == 1


async def test_las_anuladas_cuentan_como_devolucion_y_no_como_jugada(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    cog, clock = await make_cog(tmp_path)
    market = SINGLE.markets[0]
    await stake_on(cog, ANA, SINGLE, market)
    cog.client.markets[market.id] = resolved_market(market, None)  # type: ignore[union-attr]
    clock.now = END + 3_600

    await cog.settle(clock.now)

    hooks.casino_play.assert_not_awaited()
    hooks.record.assert_not_awaited()
    assert hooks.track.await_args.args[4].add == {"predict_refunds": 1}


async def test_la_racha_cuenta_los_aciertos_seguidos(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    cog, clock = await make_cog(tmp_path)
    old = await stake_on(cog, ANA, MULTI, MULTI.markets[0], odds=134)
    await cog.economy.settle_predict_bet(GUILD_ID, old.id, payout=payout(100, 134), game=GAME)
    market = SINGLE.markets[0]
    await stake_on(cog, ANA, SINGLE, market)
    cog.client.markets[market.id] = resolved_market(market, 0)  # type: ignore[union-attr]
    clock.now = END + 3_600

    await cog.settle(clock.now)

    delta = hooks.casino_play.await_args.args[4]
    assert delta.peak["predict_win_streak_max"] == 2


def test_win_streak_para_en_la_primera_fallada_e_ignora_las_anuladas() -> None:
    def bet(bet_id: int, status: str, settled: float) -> PredictBet:
        return PredictBet(
            id=bet_id, guild_id=1, user_id=ANA, channel_id=2, event_id="e", event_title="T",
            market_id=f"m{bet_id}", question="T", side=0, side_label="Sí", section="espana",
            odds=200, stake=100, ends=END, created_at=NOW, status=status, settled_at=settled,
        )  # fmt: skip

    bets = [
        bet(1, "won", 10), bet(2, "lost", 20), bet(3, "won", 30),
        bet(4, "void", 40), bet(5, "won", 50), bet(6, "open", 0),
    ]  # fmt: skip

    assert win_streak(bets) == 2
    assert win_streak([]) == 0


def test_el_mensaje_del_resultado_recorta_las_lineas_de_mas() -> None:
    bets = [
        PredictBet(
            id=n, guild_id=1, user_id=100 + n, channel_id=2, event_id="e", event_title="Título",
            market_id="m", question="Título", side=0, side_label="Sí", section="espana",
            odds=200, stake=100, ends=END, created_at=NOW, status="lost",
        )
        for n in range(cog_module.RESULT_LINES + 3)
    ]  # fmt: skip
    resolved = [Resolved(b, "No", "", None, 0) for b in bets]

    text = result_message(resolved)

    assert "Y 3 predicciones más" in text
    assert text.count("❌") == cog_module.RESULT_LINES


# -- Lo pendiente en `saldo` --------------------------------------------------------------


async def test_pending_enseña_las_predicciones_abiertas_con_lo_que_cobrarian(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _ = await make_cog(tmp_path)
    monkeypatch.setattr(cog_module, "find_cog", lambda bot, cls: cog)
    await stake_on(cog, ANA, SINGLE, SINGLE.markets[0])
    multi = await stake_on(cog, ANA, MULTI, MULTI.markets[0], odds=134, stake=50)
    await cog.economy.settle_predict_bet(GUILD_ID, multi.id, payout=0, game=GAME)

    [line] = await pending(cog.bot, GUILD_ID, ANA)

    assert line.side is PendingSide.HELD
    assert line.kind == "predict_bet"
    assert (line.emoji, line.amount, line.when) == ("🔮", 100, END)
    assert line.text == (
        f"{format_amount(100)} a Sí en «Will inflation fall?» — Will inflation fall below 2%? "
        f"(cobrarías {format_amount(payout(100, 151))})"
    )
    assert await pending(cog.bot, GUILD_ID, LEO) == []


async def test_pending_omite_la_pregunta_si_es_el_titulo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _ = await make_cog(tmp_path)
    monkeypatch.setattr(cog_module, "find_cog", lambda bot, cls: cog)
    market = replace(SINGLE.markets[0], question="Will inflation fall?")
    await stake_on(cog, ANA, SINGLE, market)

    [line] = await pending(cog.bot, GUILD_ID, ANA)

    assert " — " not in line.text
    assert line.text.startswith(f"{format_amount(100)} a Sí en «Will inflation fall?» (cobrarías")


async def test_pending_sin_el_cog_o_con_un_fallo_no_lanza(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _ = await make_cog(tmp_path)
    monkeypatch.setattr(cog_module, "find_cog", lambda bot, cls: None)
    assert await pending(cog.bot, GUILD_ID, ANA) == []

    monkeypatch.setattr(cog_module, "find_cog", lambda bot, cls: cog)
    cog.economy.predict_bets = AsyncMock(side_effect=OSError("disco"))  # type: ignore[method-assign]
    assert await pending(cog.bot, GUILD_ID, ANA) == []


async def test_al_caducar_la_portada_apaga_tambien_los_desplegables(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    many = [make_event(f"x{n}", make_market(f"mx{n}", mid=0.5)) for n in range(10)]
    cog.offers["economia"] = build_sections({"economia": many}, now=NOW)["economia"]
    view = open_view(cog)
    view.message = MagicMock(edit=AsyncMock())

    await view.on_timeout()

    assert isinstance(widget(view, "predice:mas"), ui.Select)
    assert all(w.disabled for w in widgets(view))


async def test_un_evento_con_una_sola_opcion_con_nombre_ensena_la_opcion(tmp_path: Path) -> None:
    cog, _ = await make_cog(tmp_path)
    one = make_event("o1", make_market("mo1", mid=0.4, group="22%+"), title="Vox % of vote?")
    cog.offers["economia"] = build_sections({"economia": [one]}, now=NOW)["economia"]

    assert "22%+ 40 %" in texts(open_view(cog))
    event_page = open_view(cog, event_id="o1")
    assert "**22%+** — 40 %" in texts(event_page)
    assert "predice:opcion" in ids(event_page)
    option_page = open_view(cog, event_id="o1", market_id="mo1")
    assert "**22%+**" in texts(option_page)
    assert "predice:si" in ids(option_page)
