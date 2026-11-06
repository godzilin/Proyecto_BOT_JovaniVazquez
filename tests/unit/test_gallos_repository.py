"""Pruebas de bot.repositories.gallos: la ficha del gallo de cada miembro en SQLite."""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import pytest

from bot.repositories import sqlite
from bot.repositories.gallos import GallosRepository
from bot.services.gallos import (
    FORM_PER_FIGHT,
    FORM_PER_TRAIN,
    Finish,
    Stat,
    TrainError,
    default_name,
)


async def make_repository(tmp_path: Path) -> GallosRepository:
    repository = GallosRepository(tmp_path / "bot.db")
    await repository.initialize()
    return repository


async def test_un_gallo_nuevo_lleva_el_nombre_de_serie(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    record = await repository.get(1, 10)
    assert record.name == default_name(10) and record.fights == 0


async def test_pelear_y_renombrar_se_guardan(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    day = date(2026, 10, 12)
    before, after = await repository.record_fight(1, 10, won=True, finish=Finish.KO, day=day)
    assert before.fights == 0 and (after.fights, after.wins, after.kos) == (1, 1, 1)
    renamed = await repository.rename(1, 10, "Espolón de Oro")
    assert renamed.name == "Espolón de Oro" and renamed.wins == 1
    stored = await repository.get(1, 10)
    assert stored == renamed and stored.last_day == day


async def test_ranking_y_borrar_servidor(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    day = date(2026, 10, 12)
    await repository.record_fight(1, 10, won=True, finish=Finish.KO, day=day)
    await repository.record_fight(1, 11, won=False, finish=Finish.KO, day=day)
    await repository.record_fight(2, 12, won=True, finish=Finish.KO, day=day)
    ranking = await repository.ranking(1)
    assert [user for user, _ in ranking] == [10, 11]
    await repository.delete_guild_data(1)
    assert await repository.ranking(1) == []
    assert [user for user, _ in await repository.ranking(2)] == [12]


def train(repository: GallosRepository, stat: Stat, *, level: int, now: float, day: date) -> object:
    """Corre el `reserve` del entrenamiento en una transacción, como lo hace la economía."""
    connection = sqlite.connect(repository.database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            result = repository.train(1, 10, stat, level=level, now=now, day=day)(connection)
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")
        return result
    finally:
        connection.close()


async def test_los_stats_y_el_cupo_de_entrenamiento_se_guardan(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    day = date(2026, 10, 15)
    before, after = train(repository, Stat.STRENGTH, level=0, now=5_000.0, day=day)
    assert before.strength == 0 and after.strength == 3
    stored = await repository.get(1, 10)
    assert stored == after
    assert (stored.last_train, stored.train_day, stored.trains_today) == (5_000.0, day, 1)
    assert (stored.speed, stored.endurance) == (0, 0)
    assert (stored.form, stored.form_at) == (FORM_PER_TRAIN, 5_000.0)


async def test_una_pelea_contra_la_casa_guarda_la_forma_gastada(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    day = date(2026, 10, 15)
    train(repository, Stat.STRENGTH, level=0, now=5_000.0, day=day)
    _, after = await repository.record_fight(
        1, 10, won=False, finish=Finish.POINTS, day=day, now=5_000.0
    )
    assert after.form == max(0, FORM_PER_TRAIN - FORM_PER_FIGHT)
    assert await repository.get(1, 10) == after


def test_entrenar_con_un_nivel_viejo_o_sin_cupo_no_toca_nada(tmp_path: Path) -> None:
    repository = GallosRepository(tmp_path / "bot.db")
    asyncio.run(repository.initialize())
    day = date(2026, 10, 15)
    train(repository, Stat.SPEED, level=0, now=5_000.0, day=day)
    with pytest.raises(TrainError, match="ha cambiado"):
        train(repository, Stat.SPEED, level=0, now=99_999.0, day=day)
    with pytest.raises(TrainError, match="descansa"):
        train(repository, Stat.SPEED, level=3, now=5_100.0, day=day)
    assert asyncio.run(repository.get(1, 10)).speed == 3


def make_old_database(path: Path) -> None:
    """Una base de antes de los stats: la tabla `gallos` con solo las columnas de entonces."""
    connection = sqlite.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE gallos (
                guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, name TEXT NOT NULL,
                fights INTEGER NOT NULL DEFAULT 0, wins INTEGER NOT NULL DEFAULT 0,
                kos INTEGER NOT NULL DEFAULT 0, streak INTEGER NOT NULL DEFAULT 0,
                best_streak INTEGER NOT NULL DEFAULT 0, day_streak INTEGER NOT NULL DEFAULT 0,
                last_day TEXT, PRIMARY KEY (guild_id, user_id)
            )
            """
        )
        connection.execute(
            "INSERT INTO gallos (guild_id, user_id, name, fights, wins) "
            "VALUES (1, 10, 'Viejo', 7, 4)"
        )
        connection.commit()
    finally:
        connection.close()


async def test_la_migracion_deja_a_los_gallos_que_ya_existian_con_stats_a_cero(
    tmp_path: Path,
) -> None:
    make_old_database(tmp_path / "bot.db")
    repository = GallosRepository(tmp_path / "bot.db")
    await repository.initialize()
    await repository.initialize()  # repetirla no rompe nada
    old = await repository.get(1, 10)
    assert (old.name, old.fights, old.wins) == ("Viejo", 7, 4)
    assert (old.strength, old.speed, old.endurance, old.deaths) == (0, 0, 0, 0)
    assert old.last_train == 0 and old.train_day is None and old.trains_today == 0
    assert (old.form, old.form_at) == (0, 0)
    train(repository, Stat.ENDURANCE, level=0, now=1_000_000.0, day=date(2026, 10, 15))
    assert (await repository.get(1, 10)).endurance == 3


async def test_enterrar_pasa_la_ficha_al_cementerio_y_da_un_gallo_nuevo(tmp_path: Path) -> None:
    repository = await make_repository(tmp_path)
    day = date(2026, 10, 15)
    await repository.record_fight(1, 10, won=True, finish=Finish.KO, day=day)
    train(repository, Stat.STRENGTH, level=0, now=1_000_000.0, day=day)
    await repository.rename(1, 10, "Espolón de Oro")
    fresh = await repository.bury(1, 10, killer_kind="casa", killer="marte", now=1_700_000_000.0)
    assert fresh.name == default_name(10) and fresh.fights == 0 and fresh.strength == 0
    assert fresh.deaths == 1
    assert await repository.get(1, 10) == fresh
    (grave,) = await repository.graveyard(1, 10)
    assert (grave.name, grave.fights, grave.wins, grave.kos) == ("Espolón de Oro", 1, 1, 1)
    assert (grave.strength, grave.speed, grave.endurance) == (3, 0, 0)
    assert (grave.killer_kind, grave.killer, grave.died_at) == ("casa", "marte", 1_700_000_000.0)


async def test_el_cementerio_enseña_los_ultimos_primero_y_es_de_cada_miembro(
    tmp_path: Path,
) -> None:
    repository = await make_repository(tmp_path)
    for i, name in enumerate(("Uno", "Dos", "Tres", "Cuatro")):
        await repository.rename(1, 10, name)
        await repository.bury(1, 10, killer_kind="miembro", killer="11", now=float(i))
    await repository.bury(1, 11, killer_kind="casa", killer="temu", now=9.0)
    assert [g.name for g in await repository.graveyard(1, 10)] == ["Cuatro", "Tres", "Dos"]
    assert [g.name for g in await repository.graveyard(1, 10, limit=10)][-1] == "Uno"
    assert len(await repository.graveyard(1, 11)) == 1
    assert (await repository.get(1, 10)).deaths == 4
    await repository.delete_guild_data(1)
    assert await repository.graveyard(1, 10) == []
