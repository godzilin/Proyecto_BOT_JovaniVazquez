"""Pruebas de las galleras y el torneo del domingo de los gallos.

Las reglas puras de `bot.services.gallos_torneo`, las tablas de
`bot.repositories.gallos` y lo que hace el cog: la vista 🏠 de la gallera, la
forma que se reparte al entrenar y el anuncio del torneo.
"""

from __future__ import annotations

import random
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from interaction_fakes import fake_interaction
from test_gallos_cog import (
    GUILD_ID,
    OWNER_ID,
    RIVAL_ID,
    WIN,
    Graded,
    make_cog,
    make_user,
    open_panel,
)

from bot.cogs import gallos as gallos_cog
from bot.repositories.gallos import GallosRepository
from bot.services import gallos_gym as gym
from bot.services import gallos_torneo as torneo
from bot.services.gallos import RoosterRecord, Stat
from bot.services.levels import TIMEZONE

SUNDAY_NIGHT = datetime(2026, 11, 8, 21, 30, tzinfo=TIMEZONE).timestamp()
DAY = date(2026, 11, 8)


# -- Reglas ----------------------------------------------------------------------------------


def test_la_gallera_da_forma_hasta_el_tope_del_dia() -> None:
    record = RoosterRecord(name="Pepe")
    for _ in range(torneo.CLUB_FORM_DAILY + 3):
        record = torneo.after_club_form(record, now=0.0, day=DAY)
    assert record.form == torneo.CLUB_FORM_DAILY * torneo.CLUB_FORM
    assert torneo.club_form_today(record, DAY) == torneo.CLUB_FORM_DAILY
    assert torneo.club_form_today(record, date(2026, 11, 9)) == 0


def test_el_nombre_de_la_gallera_se_limpia_y_tiene_tope() -> None:
    assert torneo.clean_club_name("  Los **del**   Pino ") == "Los del Pino"
    with pytest.raises(ValueError):
        torneo.clean_club_name("@@@")
    with pytest.raises(ValueError):
        torneo.clean_club_name("x" * (torneo.CLUB_NAME_MAX + 1))


def test_el_torneo_es_el_domingo_por_la_noche_una_vez_por_semana() -> None:
    assert torneo.due(SUNDAY_NIGHT)
    saturday = datetime(2026, 11, 7, 22, 0, tzinfo=TIMEZONE).timestamp()
    sunday_noon = datetime(2026, 11, 8, 12, 0, tzinfo=TIMEZONE).timestamp()
    assert not torneo.due(saturday) and not torneo.due(sunday_noon)
    assert torneo.week_key(SUNDAY_NIGHT) == torneo.week_key(saturday) == "2026-11-02"


def rooster(
    name: str, power: int, *, last_day: date | None = DAY, **extra: object
) -> RoosterRecord:
    return RoosterRecord(
        name=name, strength=power, speed=power, endurance=power, last_day=last_day, fights=1,
        **extra,  # type: ignore[arg-type]
    )  # fmt: skip


def test_entran_los_mas_fuertes_que_han_peleado_esta_semana_y_no_estan_lesionados() -> None:
    records = [(i, rooster(f"G{i}", i * 10)) for i in range(1, 10)]
    records.append((50, rooster("Viejo", 100, last_day=date(2026, 10, 1))))
    records.append((51, rooster("Cojo", 100, injured_until=SUNDAY_NIGHT + 60)))
    field = torneo.entrants(records, now=SUNDAY_NIGHT)
    assert [e.user_id for e in field] == [9, 8, 7, 6, 5, 4, 3, 2]


def test_el_torneo_juega_eliminatorias_hasta_un_campeon() -> None:
    field = torneo.entrants([(i, rooster(f"G{i}", i * 10)) for i in range(1, 9)], now=0.0)
    bracket = torneo.play(field, random.Random(3))
    assert bracket is not None
    assert [len(r) for r in bracket.rounds] == [4, 2, 1]
    assert bracket.wins(bracket.champion.user_id) == 3
    # Cabezas de serie: el primero contra el último.
    first = bracket.rounds[0][0]
    assert {first.red.user_id, first.blue.user_id} == {8, 1}
    assert torneo.play(field[:3], random.Random(1)) is None
    four = torneo.play(field[:6], random.Random(1))
    assert four is not None and [len(r) for r in four.rounds] == [2, 1]


def test_la_gallera_de_la_semana_es_la_que_mas_combates_gana() -> None:
    field = [torneo.Entrant(i, f"G{i}", 100 - i, 0, club=1 if i < 4 else 2) for i in range(8)]
    bracket = torneo.play(field, random.Random(5))
    assert bracket is not None
    club, wins = torneo.best_club(bracket) or (0, 0)
    totals = {c: 0 for c in (1, 2)}
    for rnd in bracket.rounds:
        for match in rnd:
            totals[match.winner.club or 0] += 1
    assert wins == max(totals.values()) and totals[club] == wins


# -- Repositorio --------------------------------------------------------------------------


async def make_repository(tmp_path: Path) -> GallosRepository:
    repository = GallosRepository(tmp_path / "bot.db")
    await repository.initialize()
    return repository


async def test_fundar_entrar_llenar_y_salir_de_una_gallera(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    club = await repository.found_club(1, 10, "Los del Pino", now=1.0)
    assert club.members == (10,) and club.owner_id == 10
    with pytest.raises(torneo.ClubError, match="Ya estás"):
        await repository.found_club(1, 10, "Otra", now=2.0)
    with pytest.raises(torneo.ClubError, match="Ya hay"):
        await repository.found_club(1, 11, "Los del Pino", now=2.0)
    for user in range(11, 10 + torneo.CLUB_MAX):
        await repository.join_club(1, user, club.id, now=float(user))
    with pytest.raises(torneo.ClubError, match="llena"):
        await repository.join_club(1, 99, club.id, now=99.0)
    # Quien la fundó se va: pasa al más antiguo.
    after = await repository.leave_club(1, 10)
    assert after is not None and after.owner_id == 11 and 10 not in after.members
    assert await repository.club_of(1, 10) is None
    assert (await repository.club_ids(1))[12] == club.id
    for user in range(11, 10 + torneo.CLUB_MAX):
        await repository.leave_club(1, user)
    assert await repository.clubs(1) == []


async def test_el_torneo_se_juega_una_vez_por_semana_y_suma_el_titulo(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    await repository.remember_channel(1, 555)
    assert await repository.tournament_targets("2026-11-02") == [(1, 555)]
    assert await repository.finish_tournament(1, "2026-11-02", 10)
    assert not await repository.finish_tournament(1, "2026-11-02", 10)
    assert await repository.tournament_targets("2026-11-02") == []
    assert (await repository.get(1, 10)).titles == 1
    assert await repository.tournament_targets("2026-11-09") == [(1, 555)]


# -- El cog ----------------------------------------------------------------------------------


async def test_la_vista_de_la_gallera_funda_entra_y_sale(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    panel = await open_panel(cog)
    await panel._open_club_view(fake_interaction(make_user(OWNER_ID)))
    assert panel.club_view and panel.club is None
    ids = {c.custom_id for c in panel.children if isinstance(c, discord.ui.Button)}
    assert ids == {"gallos:club_found", "gallos:club_back"}
    await panel.found_club(fake_interaction(make_user(OWNER_ID)), "Los del Pino")
    assert panel.club is not None and panel.club.name == "Los del Pino"
    assert "Los del Pino" in panel.club_text()
    await panel._leave_club(fake_interaction(make_user(OWNER_ID)))
    assert panel.club is None and await cog.repository.clubs(GUILD_ID) == []


async def test_acabar_un_minijuego_da_forma_a_los_companeros_de_gallera(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gallos_cog.gym, "start", lambda kind, rng, now: Graded(0.5))
    monkeypatch.setattr(gallos_cog.gym, "roll_event", lambda rng: None)
    cog = await make_cog(tmp_path, WIN)
    cog.wall_clock = lambda: 1_000_000.0
    club = await cog.repository.found_club(GUILD_ID, OWNER_ID, "Los del Pino", now=1.0)
    await cog.repository.join_club(GUILD_ID, RIVAL_ID, club.id, now=2.0)
    panel = await open_panel(cog)
    await panel._train(fake_interaction(make_user(OWNER_ID)), Stat.STRENGTH)
    await panel._press(fake_interaction(make_user(OWNER_ID)), "ok")
    mate = await cog.repository.get(GUILD_ID, RIVAL_ID)
    assert mate.form == torneo.CLUB_FORM


async def test_el_torneo_se_anuncia_en_el_canal_guardado(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, WIN)
    cog.wall_clock = lambda: SUNDAY_NIGHT
    for user in range(20, 28):
        await cog.repository.record_fight(GUILD_ID, user, won=True, finish=gym_finish(), day=DAY)
    await cog.repository.remember_channel(GUILD_ID, 777)
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    cog.bot.get_channel = MagicMock(return_value=channel)
    cog.bot.get_guild = MagicMock(return_value=None)
    assert await cog.run_tournaments() == 1
    channel.send.assert_awaited_once()
    text = channel.send.await_args.kwargs["embed"].description
    assert "gana el torneo" in text and "Cartelera" in text
    assert await cog.run_tournaments() == 0
    titles = [(await cog.repository.get(GUILD_ID, u)).titles for u in range(20, 28)]
    assert sorted(titles) == [0] * 7 + [1]


def gym_finish() -> object:
    from bot.services.gallos import Finish

    return Finish.POINTS


def test_el_minijuego_de_prueba_sigue_siendo_un_minijuego() -> None:
    assert isinstance(Graded(1.0), gym.GymGame)


def test_las_porras_del_reñidero_miran_el_ko_y_la_muerte() -> None:
    from bot.services.casino_stats import Play
    from bot.services.porras import PROPOSITION_BY_KEY, propositions_for

    keys = {p.key for p in propositions_for("gallos", 3)}
    assert {"gallo_ko", "gallo_vivo"} <= keys
    assert "gallo_ko" not in {p.key for p in propositions_for("minas", 3)}

    def play(**details: int) -> Play:
        return Play("gallos", 100, 0, 0, 0, tuple(details.items()))

    ko = PROPOSITION_BY_KEY["gallo_ko"]
    assert ko.decide([play(ko=1, won=1), play()], 1) == 0
    assert ko.decide([play(ko=1, won=0)], 1) == 1
    alive = PROPOSITION_BY_KEY["gallo_vivo"]
    assert alive.decide([play(), play(died=1)], 1) == 1
    assert alive.decide([play()], 1) == 0
