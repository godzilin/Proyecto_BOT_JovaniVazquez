"""Pruebas de `garito`, que elige en qué canales se juega al casino (cog `Admin`)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord

from bot.cogs.admin import Admin
from bot.repositories.casino_settings import CasinoSettingsRepository
from bot.services.casino_channels import CasinoChannels

GUILD = 1


async def make_admin(tmp_path: Path, default: frozenset[int] = frozenset()) -> Admin:
    repository = CasinoSettingsRepository(tmp_path / "bot.sqlite3")
    await repository.initialize()
    bot = MagicMock()
    bot.casino_channels = CasinoChannels(repository, default=default)
    await bot.casino_channels.load()
    return Admin(bot)


def make_responder() -> MagicMock:
    responder = MagicMock()
    responder.guild = MagicMock(id=GUILD)
    responder.send = AsyncMock()
    responder.send_error = AsyncMock()
    return responder


def make_channel(channel_id: int) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.mention = f"<#{channel_id}>"
    return channel


async def test_garito_anade_quita_y_abre_todos_los_canales(tmp_path: Path) -> None:
    admin = await make_admin(tmp_path)
    rules = admin.bot.casino_channels
    responder = make_responder()

    await admin._garito_impl(responder, None, False)
    assert "cualquiera" in responder.send.await_args.args[0]

    await admin._garito_impl(responder, make_channel(5), False)
    assert rules.ids(GUILD) == frozenset({5})
    assert "añadido" in responder.send.await_args.args[0]
    assert responder.send.await_args.kwargs["ephemeral"] is True

    await admin._garito_impl(responder, make_channel(5), False)
    assert rules.ids(GUILD) == frozenset()
    assert "quitado" in responder.send.await_args.args[0]

    await admin._garito_impl(responder, make_channel(7), False)
    await admin._garito_impl(responder, None, True)
    assert rules.ids(GUILD) == frozenset()
    assert "cualquier canal" in responder.send.await_args.args[0]


async def test_sin_elegir_valen_los_de_la_configuracion_y_se_dice(tmp_path: Path) -> None:
    admin = await make_admin(tmp_path, default=frozenset({9}))
    responder = make_responder()

    await admin._garito_impl(responder, None, False)
    text = responder.send.await_args.args[0]
    assert "<#9>" in text
    assert "configuración del bot" in text

    # El primer canal que se añade parte de los de la configuración.
    await admin._garito_impl(responder, make_channel(5), False)
    assert admin.bot.casino_channels.ids(GUILD) == frozenset({5, 9})
    assert "configuración del bot" not in responder.send.await_args.args[0]


async def test_todos_anula_los_de_la_configuracion(tmp_path: Path) -> None:
    admin = await make_admin(tmp_path, default=frozenset({9}))
    await admin._garito_impl(make_responder(), None, True)
    assert admin.bot.casino_channels.ids(GUILD) == frozenset()
