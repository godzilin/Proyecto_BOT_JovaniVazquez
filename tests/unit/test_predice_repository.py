"""Pruebas del repositorio de `predice`: la tabla de traducciones."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bot.repositories.predice import PredictRepository

NOW = 1_800_000_000.0


async def make_repo(tmp_path: Path) -> PredictRepository:
    repo = PredictRepository(tmp_path / "bot.db")
    await repo.initialize()
    return repo


async def test_initialize_crea_la_tabla_y_es_repetible(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)
    await repo.initialize()

    with sqlite3.connect(tmp_path / "bot.db") as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(predict_translations)")]
    assert columns == ["source", "text", "created_at"]


async def test_sin_traducciones_devuelve_un_diccionario_vacio(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)

    assert await repo.translations() == {}


async def test_guardar_y_leer_traducciones(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)

    await repo.save_translation("Will PP win?", "¿Ganará el PP?", now=NOW)
    await repo.save_translation("Bitcoin above 100k?", "¿Bitcoin por encima de 100k?", now=NOW + 1)

    assert await repo.translations() == {
        "Will PP win?": "¿Ganará el PP?",
        "Bitcoin above 100k?": "¿Bitcoin por encima de 100k?",
    }


async def test_guardar_otra_vez_el_mismo_texto_lo_sustituye(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)
    await repo.save_translation("Will PP win?", "¿Ganará el PP?", now=NOW)

    await repo.save_translation("Will PP win?", "¿Ganará el Partido Popular?", now=NOW + 60)

    assert await repo.translations() == {"Will PP win?": "¿Ganará el Partido Popular?"}
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        [(created,)] = connection.execute("SELECT created_at FROM predict_translations")
    assert created == NOW + 60


async def test_las_traducciones_sobreviven_a_un_repositorio_nuevo(tmp_path: Path) -> None:
    repo = await make_repo(tmp_path)
    await repo.save_translation("Yes", "Sí", now=NOW)

    again = PredictRepository(tmp_path / "bot.db")

    assert await again.translations() == {"Yes": "Sí"}
