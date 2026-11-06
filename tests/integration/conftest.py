"""Arranque barato del bot real en las pruebas de integración.

Montar `BotClient` y cargar sus cogs cuesta ~2 s y 330 pruebas lo hacen. Dos costes se evitan
sin cambiar lo que se comprueba:

- Los esquemas de los repositorios se crean una vez por proceso en una plantilla de SQLite y
  cada `BotClient` arranca con una copia (los `initialize` siguen corriendo, pero con las
  tablas ya hechas).
- El precalentamiento de piezas del pachinko (~0,3 s de CPU en un hilo que sigue pintando
  mientras corre la prueba) se omite: el dibujo las monta al primer uso, como siempre.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from bot.app import BotClient
from bot.repositories import sqlite
from bot.services import pachinko_render

#: Qué repositorios guardan sus tablas en la base (los `initialize` que repiten las pruebas).
_REPOSITORIES: tuple[Callable[[BotClient], Any], ...] = (
    lambda c: c.message_stats,
    lambda c: c.economy.repository,
    lambda c: c.casino_stats,
    lambda c: c.casino_channels.repository,
    lambda c: c.birthdays,
    lambda c: c.achievements,
    lambda c: c.welcome,
    lambda c: c.news,
    lambda c: c.shop,
    lambda c: c.housing,
    lambda c: c.pets,
    lambda c: c.lottery,
    lambda c: c.todo,
    lambda c: c.hold_win,
    lambda c: c.picar,
    lambda c: c.slots_repository,
    lambda c: c.horses,
    lambda c: c.gallos,
    lambda c: c.enraya,
    lambda c: c.backgammon,
    lambda c: c.poker,
    lambda c: c.porras,
    lambda c: c.sports,
    lambda c: c.predice,
    lambda c: c.rueda,
    lambda c: c.work.repository,
    lambda c: c.negocio.repository,
    lambda c: c.beernight,
    lambda c: c.telemetry_repository,
    lambda c: c.last_seen,
    lambda c: c.moderation,
    lambda c: c.aspecto,
)


def _build_template(path: Path, init: Callable[..., None]) -> None:
    async def schemas() -> None:
        client = BotClient.__new__(BotClient)
        init(client, command_prefix=".", database_path=path)
        for repository in _REPOSITORIES:
            await repository(client).initialize()

    # Un hilo con su propio bucle: la fixture puede correr con el bucle de la prueba ya creado.
    with ThreadPoolExecutor(1) as pool:
        pool.submit(asyncio.run, schemas()).result()
    # Con el WAL vaciado, el archivo suelto lleva ya todo (y el modo WAL, que se guarda en él).
    sqlite.checkpoint(path)


@pytest.fixture(autouse=True)
def _arranque_barato(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    original = BotClient.__init__
    template = tmp_path_factory.getbasetemp() / "plantilla" / "bot.sqlite3"

    def init(self: BotClient, *, database_path: Path, **kwargs: Any) -> None:
        if not database_path.exists():
            if not template.exists():
                _build_template(template, original)
            database_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(template, database_path)
        original(self, database_path=database_path, **kwargs)

    monkeypatch.setattr(BotClient, "__init__", init)
    monkeypatch.setattr(pachinko_render.PachinkoRenderer, "warm_up", lambda self: None)
    yield
