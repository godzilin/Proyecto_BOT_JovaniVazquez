"""Pruebas de las combinadas en bot.cogs.sports: el boleto del panel, apostar y resolver.

Mismo montaje que `test_sports_cog`: economía y partidos reales sobre un SQLite temporal,
reloj falso y partidos puestos a mano en memoria. Montar el boleto solo toca memoria y se
contesta con `edit_message`; apostar acepta el formulario antes de ir a la base.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from interaction_fakes import fake_interaction  # noqa: F401 - lo usa make_interaction

import bot.repositories.sqlite as sqlite_module
from bot.cogs import sports as cog_module
from bot.cogs.sports import (
    ComboStakeModal,
    Deportes,
    SportsView,
    combo_result_message,
    pending,
    slip_card,
)
from bot.services.economy import STARTING_BALANCE, SportsCombo, SportsLeg, format_amount
from bot.services.pending import Side
from bot.services.sports import (
    MAX_COMBO_PAYOUT,
    MAX_LEGS,
    MAX_STAKE,
    RESULT_DELAY,
    VOID_AFTER,
    FeedReply,
    Market,
    Match,
    Score,
    combo_odds,
    combo_payout,
)
from tests.unit.test_sports_cog import (
    ANA,
    CHANNEL_ID,
    GUILD_ID,
    LIGA,
    START,
    FakeOdds,
    _final,
    _fresh_odds,
    check_limits,
    ids,
    make_cog,
    make_goals_match,
    make_interaction,
    make_match,
    said,
    sent_text,
    stake_on,
    store,
    texts,
    widget,
)


@pytest.fixture(autouse=True)
def hooks(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Logros, apuestas y Renta se apuntan sin ir a los cogs, como en `test_sports_cog`."""
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


async def combo_cog(tmp_path: Path, games: int = 3) -> tuple[Deportes, object]:
    """Un cog con `games` partidos de LaLiga, de una hora en una hora."""
    cog, clock = await make_cog(tmp_path)
    cog.client = MagicMock()
    cog.matches[LIGA] = [
        make_match(f"e{n}", commence=START + 3_600 * (n + 1)) for n in range(games)
    ]
    return cog, clock


def panel(cog: Deportes) -> SportsView:
    return SportsView(cog, owner_id=ANA, sport=LIGA)


async def click(view: SportsView, custom_id: str) -> MagicMock:
    interaction = make_interaction()
    await widget(view, custom_id).callback(interaction)
    return interaction


async def add(view: SportsView, event_id: str, pick: str = "1") -> MagicMock:
    """Abre el partido y pulsa «Añadir» en la opción, como haría alguien en el panel."""
    view.combo_page = False
    view.event_id = event_id
    view.rebuild()
    return await click(view, f"deportes:anadir:{pick}")


async def combining(cog: Deportes) -> SportsView:
    view = panel(cog)
    await click(view, "deportes:combinada")
    return view


def legs_of(cog: Deportes, *picks: tuple[str, str]) -> list[tuple[str, str, int]]:
    out = []
    for event_id, pick in picks:
        match = cog.find(event_id)
        assert match is not None
        out.append((event_id, pick, match.price(pick)))
    return out


async def bet_combo(
    cog: Deportes, *picks: tuple[str, str], stake: int = 100
) -> tuple[MagicMock, SportsCombo]:
    interaction = make_interaction()
    await cog.place_combo(interaction, legs_of(cog, *picks), text=str(stake))
    [combo] = await cog.economy.open_sports_combos()
    return interaction, combo


# -- El boleto en el panel ----------------------------------------------------------------


async def test_el_boton_combinada_abre_el_boleto_sin_tocar_la_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, _ = await combo_cog(tmp_path)
    view = panel(cog)
    connections: list[str] = []
    monkeypatch.setattr(sqlite_module, "connect", lambda *a, **k: connections.append("db"))

    interaction = await click(view, "deportes:combinada")

    assert connections == []
    interaction.response.edit_message.assert_awaited_once()
    assert view.combining and view.combo_page
    assert "Tu combinada" in texts(view)
    assert widget(view, "deportes:combinar").disabled


async def test_en_modo_combinada_las_opciones_anaden_y_el_exacto_no_sale(tmp_path: Path) -> None:
    cog, _ = await combo_cog(tmp_path)
    cog.matches[LIGA] = [make_goals_match("e0")]
    view = await combining(cog)
    view.combo_page = False
    view.event_id = "e0"
    view.market = Market.EXACT
    view.rebuild()

    found = ids(view)
    assert view.market is Market.RESULT
    assert "deportes:anadir:1" in found and "deportes:apostar:1" not in found
    assert widget(view, f"deportes:mercado:{Market.EXACT.value}").disabled
    view.market = Market.GOALS
    view.rebuild()
    assert "deportes:anadir:+2.5" in ids(view)


async def test_anadir_mete_la_opcion_y_vuelve_a_la_portada(tmp_path: Path) -> None:
    cog, _ = await combo_cog(tmp_path)
    view = await combining(cog)

    interaction = await add(view, "e0", "X")

    interaction.response.edit_message.assert_awaited_once()
    assert {e: p for e, (_, p) in view.legs.items()} == {"e0": "X"}
    assert view.event_id is None
    assert "Añadido a la combinada: X (empate)" in texts(view)
    assert "En tu combinada: X (empate)" in texts(view)
    assert widget(view, "deportes:combinada").label == "🧾 Combinada (1)"


async def test_otra_opcion_del_mismo_partido_sustituye_y_la_misma_la_quita(
    tmp_path: Path,
) -> None:
    cog, _ = await combo_cog(tmp_path)
    view = await combining(cog)
    await add(view, "e0", "1")

    await add(view, "e0", "2")
    assert {e: p for e, (_, p) in view.legs.items()} == {"e0": "2"}

    view.event_id = "e0"
    view.rebuild()
    assert widget(view, "deportes:anadir:2").label == "Quitar"
    await click(view, "deportes:anadir:2")
    assert view.legs == {}


async def test_no_se_pasa_del_maximo_de_partidos(tmp_path: Path) -> None:
    cog, _ = await combo_cog(tmp_path, games=MAX_LEGS + 1)
    view = await combining(cog)
    for n in range(MAX_LEGS):
        await add(view, f"e{n}")

    # Lleno: se va solo al boleto.
    assert view.combo_page
    assert widget(view, "deportes:mas").disabled
    view.combo_page = False
    view.event_id = f"e{MAX_LEGS}"
    view.rebuild()
    assert widget(view, "deportes:anadir:1").disabled
    assert len(view.legs) == MAX_LEGS


async def test_el_boleto_lleno_cabe_en_discord(tmp_path: Path) -> None:
    cog, _ = await combo_cog(tmp_path, games=MAX_LEGS)
    view = await combining(cog)
    for n in range(MAX_LEGS):
        await add(view, f"e{n}")
    view.event_id = "e0"
    view.rebuild()

    check_limits(view)
    view.combo_page = False
    view.rebuild()
    check_limits(view)


async def test_el_boleto_ensena_la_cuota_total_y_lo_que_se_cobraria(tmp_path: Path) -> None:
    cog, _ = await combo_cog(tmp_path)
    view = await combining(cog)
    await add(view, "e0", "1")
    await add(view, "e1", "2")

    await click(view, "deportes:combinada")

    odds = combo_odds([185, 410])
    assert "×7,58" in texts(view) and odds == 758
    assert format_amount(combo_payout(100, odds)) in texts(view)
    assert not widget(view, "deportes:combinar").disabled
    await click(view, "deportes:quitar:e1")
    assert widget(view, "deportes:combinar").disabled


async def test_un_partido_que_empieza_sale_solo_del_boleto(tmp_path: Path) -> None:
    cog, clock = await combo_cog(tmp_path)
    view = await combining(cog)
    await add(view, "e0")
    await add(view, "e1")
    clock.now = START + 3_600  # empieza e0

    await click(view, "deportes:combinada")

    assert list(view.legs) == ["e1"]
    assert "ya ha empezado: Real Madrid – Barcelona" in texts(view)


async def test_apostar_combinada_abre_el_formulario_como_primera_respuesta(
    tmp_path: Path,
) -> None:
    cog, _ = await combo_cog(tmp_path)
    view = await combining(cog)
    await add(view, "e0", "1")
    await add(view, "e1", "X")
    await click(view, "deportes:combinada")

    interaction = await click(view, "deportes:combinar")

    interaction.response.defer.assert_not_awaited()
    modal = interaction.response.send_modal.await_args.args[0]
    assert isinstance(modal, ComboStakeModal)
    assert modal.legs == [("e0", "1", 185), ("e1", "X", 360)]


async def test_vaciar_sale_del_modo_combinada(tmp_path: Path) -> None:
    cog, _ = await combo_cog(tmp_path)
    view = await combining(cog)
    await add(view, "e0")
    await click(view, "deportes:combinada")

    await click(view, "deportes:vaciar")

    assert view.legs == {} and not view.combining and not view.combo_page
    assert "deportes:apostar:1" not in ids(view)  # portada normal
    assert widget(view, "deportes:ver:e0").label == "Apostar"


# -- Apostar ------------------------------------------------------------------------------


async def test_apostar_una_combinada_cobra_y_confirma_con_sus_patas(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    cog, _ = await combo_cog(tmp_path)
    interaction, combo = await bet_combo(cog, ("e0", "1"), ("e1", "2"))

    interaction.response.defer.assert_awaited_once()
    message = texts(interaction.followup.send.await_args.kwargs["view"])
    assert "combinada de **2 partidos** a **×7,58**" in message
    assert "1 (Real Madrid)" in message and "2 (Barcelona)" in message
    assert format_amount(combo_payout(100, 758)) in message
    assert (combo.stake, combo.odds, combo.channel_id) == (100, 758, CHANNEL_ID)
    assert [(leg.event_id, leg.pick, leg.odds) for leg in combo.legs] == [
        ("e0", "1", 185),
        ("e1", "2", 410),
    ]
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 100
    hooks.remind.assert_awaited_once()
    stats = hooks.track.await_args.args[4]
    assert stats.add["sports_combos"] == 1
    assert stats.peak["sports_combo_odds_max"] == 758


async def test_la_combinada_lleva_su_boleto_dibujado_con_una_fila_por_partido(
    tmp_path: Path,
) -> None:
    cog, _ = await combo_cog(tmp_path)

    interaction, combo = await bet_combo(cog, ("e0", "1"), ("e1", "2"))

    [state] = cog.scene.states
    assert state["kind"] == "combinada"
    assert [leg["pick"] for leg in state["legs"]] == ["1 (Real Madrid)", "2 (Barcelona)"]
    assert (state["odds"], state["serial"]) == ("×7,58", f"Nº C{combo.id:05d}")
    assert interaction.followup.send.await_args.kwargs["file"].filename == "combinada.png"


async def test_si_no_se_puede_dibujar_la_combinada_sale_en_texto(tmp_path: Path) -> None:
    cog, _ = await combo_cog(tmp_path)
    cog.scene.still = AsyncMock(return_value=None)

    interaction, _ = await bet_combo(cog, ("e0", "1"), ("e1", "2"))

    assert "file" not in interaction.followup.send.await_args.kwargs
    assert "combinada de **2 partidos**" in texts(
        interaction.followup.send.await_args.kwargs["view"]
    )


async def test_apostar_desde_el_panel_vacia_su_boleto(tmp_path: Path) -> None:
    cog, _ = await combo_cog(tmp_path)
    view = await combining(cog)
    await add(view, "e0")
    await add(view, "e1")
    interaction = make_interaction()

    await cog.place_combo(
        interaction, legs_of(cog, ("e0", "1"), ("e1", "1")), text="100", panel=view
    )

    assert view.legs == {} and not view.combining
    interaction.edit_original_response.assert_awaited_once()


async def test_si_cambia_una_cuota_avisa_sin_cobrar(tmp_path: Path) -> None:
    cog, _ = await combo_cog(tmp_path)
    interaction = make_interaction()

    await cog.place_combo(interaction, [("e0", "1", 185), ("e1", "1", 170)], text="100")

    assert "paga ahora ×1,85 (antes, ×1,70)" in said(interaction)
    assert await cog.economy.open_sports_combos() == []
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE


async def test_un_partido_ya_empezado_no_deja_apostar(tmp_path: Path) -> None:
    cog, clock = await combo_cog(tmp_path)
    legs = legs_of(cog, ("e0", "1"), ("e1", "1"))
    clock.now = START + 3_600
    interaction = make_interaction()

    await cog.place_combo(interaction, legs, text="100")

    assert "ya ha empezado" in said(interaction)
    assert await cog.economy.open_sports_combos() == []


async def test_un_exacto_o_un_partido_repetido_no_se_combina(tmp_path: Path) -> None:
    cog, _ = await combo_cog(tmp_path)
    cog.matches[LIGA] = [make_goals_match("e0"), make_goals_match("e1")]
    exact = cog.find("e1").price("1-0")  # type: ignore[union-attr]

    first = make_interaction()
    await cog.place_combo(first, [("e0", "1", 185), ("e1", "1-0", exact)], text="100")
    second = make_interaction()
    await cog.place_combo(second, [("e0", "1", 185), ("e0", "2", 410)], text="100")

    assert "no se puede combinar" in said(first)
    assert "partidos distintos" in said(second)
    assert await cog.economy.open_sports_combos() == []


async def test_la_combinada_cuenta_en_el_tope_de_las_sencillas_y_al_reves(
    tmp_path: Path,
) -> None:
    cog, _ = await combo_cog(tmp_path)
    await cog.economy.grant(GUILD_ID, ANA, amount=10 * MAX_STAKE, reason="test:saldo")
    await stake_on(cog, ANA, "1", stake=MAX_STAKE - 100, match=cog.find("e0"))

    too_much = make_interaction()
    await cog.place_combo(too_much, legs_of(cog, ("e0", "X"), ("e1", "1")), text="200")
    assert f"te caben {format_amount(100)}" in said(too_much)
    await cog.place_combo(make_interaction(), legs_of(cog, ("e0", "X"), ("e1", "1")), text="all")
    [combo] = await cog.economy.open_sports_combos()
    assert combo.stake == 100

    # El otro partido de la combinada ya lleva 100 suyos: a la sencilla le caben 49.900.
    single = make_interaction()
    await cog.place(single, cog.find("e1"), "2", seen=410, text=str(MAX_STAKE))  # type: ignore[arg-type]
    assert format_amount(MAX_STAKE - 100) in said(single)


# -- Resolver -----------------------------------------------------------------------------


async def test_una_pata_fallada_pierde_la_combinada_al_momento(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    cog, clock = await combo_cog(tmp_path)
    _, combo = await bet_combo(cog, ("e0", "1"), ("e1", "1"))
    first = cog.find("e0")
    assert first is not None
    await store(cog, first, Score(0, 1))
    clock.now = first.commence + RESULT_DELAY

    [done] = await cog.settle_combos(clock.now)

    assert done.combo.status == "lost"
    assert [leg.status for leg in done.combo.legs] == ["lost", "open"]
    text = sent_text(cog.bot.channels[CHANNEL_ID])
    assert "pierde su combinada de 2 patas: falla «1 (Real Madrid)»" in text
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 100
    delta = hooks.casino_play.await_args.args[4]
    assert delta.add["sports_combo_losses"] == 1
    assert "sports_combo_first_leg" in delta.add
    hooks.record.assert_awaited_once()


async def test_las_patas_acertadas_se_apuntan_y_la_ultima_cobra(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    cog, clock = await combo_cog(tmp_path)
    await bet_combo(cog, ("e0", "1"), ("e1", "2"))
    first, second = cog.find("e0"), cog.find("e1")
    assert first is not None and second is not None
    await store(cog, first, Score(2, 0))
    clock.now = first.commence + RESULT_DELAY

    assert await cog.settle_combos(clock.now) == []
    [still] = await cog.economy.open_sports_combos()
    assert [leg.status for leg in still.legs] == ["won", "open"]
    cog.bot.channels[CHANNEL_ID].send.assert_not_awaited()

    await store(cog, second, Score(0, 3))
    clock.now = second.commence + RESULT_DELAY
    [done] = await cog.settle_combos(clock.now)

    prize = combo_payout(100, 758)
    assert (done.combo.status, done.combo.payout) == ("won", prize)
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 100 + prize
    channel = cog.bot.channels[CHANNEL_ID]
    assert "acierta su combinada de 2 patas a ×7,58" in sent_text(channel)
    mentioned = channel.send.await_args.kwargs["allowed_mentions"].users
    assert [u.id for u in mentioned] == [ANA]
    assert hooks.casino_play.await_args.args[4].add["sports_combo_wins"] == 1


async def test_fallar_la_ultima_pata_cuenta_como_por_una_pata(
    tmp_path: Path, hooks: SimpleNamespace
) -> None:
    cog, clock = await combo_cog(tmp_path)
    await bet_combo(cog, ("e0", "1"), ("e1", "1"), ("e2", "1"))
    for event_id, score in (("e0", Score(1, 0)), ("e1", Score(2, 1)), ("e2", Score(0, 0))):
        match = cog.find(event_id) or next(m for m in cog.matches[LIGA] if m.event_id == event_id)
        await store(cog, match, score)
    clock.now = START + 3 * 3_600 + RESULT_DELAY

    [done] = await cog.settle_combos(clock.now)

    assert [leg.status for leg in done.combo.legs] == ["won", "won", "lost"]
    assert "sports_combo_last_leg" in hooks.casino_play.await_args.args[4].add


async def test_una_pata_sin_resultado_cuenta_como_uno(tmp_path: Path) -> None:
    cog, clock = await combo_cog(tmp_path)
    await bet_combo(cog, ("e0", "1"), ("e1", "2"))
    first = cog.matches[LIGA][0]
    await store(cog, first, Score(3, 1))
    clock.now = cog.matches[LIGA][1].commence + VOID_AFTER

    [done] = await cog.settle_combos(clock.now)

    assert [leg.status for leg in done.combo.legs] == ["won", "void"]
    assert (done.combo.status, done.odds) == ("won", 185)
    assert done.combo.payout == combo_payout(100, 185)


async def test_sin_ningun_resultado_se_devuelve(tmp_path: Path, hooks: SimpleNamespace) -> None:
    cog, clock = await combo_cog(tmp_path)
    await bet_combo(cog, ("e0", "1"), ("e1", "2"))
    clock.now = cog.matches[LIGA][1].commence + VOID_AFTER

    [done] = await cog.settle_combos(clock.now)

    assert done.combo.status == "void"
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE
    assert "recupera" in sent_text(cog.bot.channels[CHANNEL_ID])
    assert hooks.track.await_args.args[4].add == {"sports_combo_refunds": 1}
    hooks.casino_play.assert_not_awaited()


async def test_resolver_dos_veces_no_paga_dos_veces(tmp_path: Path) -> None:
    cog, clock = await combo_cog(tmp_path)
    await bet_combo(cog, ("e0", "1"), ("e1", "1"))
    for match in cog.matches[LIGA][:2]:
        await store(cog, match, Score(1, 0))
    clock.now = START + 2 * 3_600 + RESULT_DELAY

    assert len(await cog.settle_combos(clock.now)) == 1
    assert await cog.settle_combos(clock.now) == []
    assert await cog.economy.balance(GUILD_ID, ANA) == STARTING_BALANCE - 100 + combo_payout(
        100, combo_odds([185, 185])
    )


async def test_tick_pide_marcadores_por_una_combinada_aunque_no_haya_sencillas(
    tmp_path: Path,
) -> None:
    cog, clock = await combo_cog(tmp_path)
    await bet_combo(cog, ("e0", "1"), ("e1", "1"))
    first = cog.matches[LIGA][0]
    await store(cog, first)
    clock.now = first.commence + RESULT_DELAY
    await _fresh_odds(cog, clock.now)
    feed = cog.client = FakeOdds()  # type: ignore[assignment]
    feed.scores_reply = FeedReply([_final(first, 0, 2)], 300)

    await cog.tick()

    assert feed.scores_calls == [(LIGA, 1)]
    [combo] = await cog.economy.sports_combos(GUILD_ID, ANA, limit=1)
    assert combo.status == "lost"


# -- Saldo y Mis apuestas -----------------------------------------------------------------


async def test_pending_cuenta_las_combinadas_abiertas(tmp_path: Path) -> None:
    cog, _ = await combo_cog(tmp_path)
    await bet_combo(cog, ("e0", "1"), ("e2", "2"))
    bot = MagicMock()
    bot.cogs = {"Deportes": cog}
    bot.get_cog = MagicMock(return_value=cog)

    items = await pending(bot, GUILD_ID, ANA)

    [line] = [i for i in items if i.kind == "sports_combo"]
    assert line.side is Side.HELD
    assert line.amount == 100
    assert line.when == cog.matches[LIGA][2].commence
    assert "combinada de 2 partidos a ×7,58" in line.text


def test_mis_apuestas_ensena_las_combinadas_con_sus_patas() -> None:
    leg = SportsLeg("e0", LIGA, "Real Madrid", "Barcelona", START, "1x2", "1", 185, "won")
    other = SportsLeg("e1", LIGA, "Betis", "Sevilla", START, "1x2", "X", 320)
    combo = SportsCombo(1, GUILD_ID, ANA, CHANNEL_ID, 100, 592, START, (leg, other))

    text = texts(slip_card([], "Ana", [combo]))

    assert "Combinadas" in text
    assert "combinada de 2 patas a ×5,92" in text
    assert "✅ **1 (Real Madrid)**" in text and "⏳ **X (empate)**" in text


def test_el_mensaje_de_una_ganada_con_tope_lo_dice() -> None:
    leg = SportsLeg("e0", LIGA, "A", "B", START, "1x2", "1", 2_500, "won")
    combo = SportsCombo(
        1, GUILD_ID, ANA, CHANNEL_ID, MAX_STAKE, 62_500, START, (leg, leg), "won", MAX_COMBO_PAYOUT
    )

    text = combo_result_message([cog_module.ResolvedCombo(combo, 62_500, None, 0)])

    assert "(el tope)" in text


def test_las_cuotas_de_los_partidos_de_prueba_son_las_que_se_esperan() -> None:
    # Guarda contra un cambio de `make_match`: las pruebas de arriba cuentan con estas cuotas.
    match: Match = make_match()
    assert (match.price("1"), match.price("X"), match.price("2")) == (185, 360, 410)
