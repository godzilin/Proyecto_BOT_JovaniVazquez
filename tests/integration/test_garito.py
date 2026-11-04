"""`garito` con el bot real: lo que elige Admin lo ven al momento todos los juegos.

Los canales los cambia el cog `Admin` y los leen los 25 cogs del casino, así que se
prueba con `BotClient` e `INITIAL_EXTENSIONS`, como en producción (CLAUDE.md, «Entre
cogs»). También que, con `.`, el aviso de canal sale y se borra solo.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from render_fakes import use_fake_drawings

from bot.app import INITIAL_EXTENSIONS, BotClient
from bot.utils.channels import NOTICE_SECONDS

GUILD_ID = 1
CASINO = 5
OTHER = 6


@pytest.fixture(autouse=True)
def fake_drawings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin dibujar: aquí no se abre ninguna mesa."""
    use_fake_drawings(monkeypatch)


async def load_bot(tmp_path: Path) -> BotClient:
    client = BotClient(command_prefix=".", database_path=tmp_path / "bot.sqlite3")
    await client.economy.repository.initialize()
    await client.casino_stats.initialize()
    await client.achievements.initialize()
    await client.work.repository.initialize()
    await client.casino_channels.repository.initialize()
    await client.casino_channels.load()
    for extension in INITIAL_EXTENSIONS:
        await client.load_extension(extension)
    return client


def make_channel(channel_id: int, parent_id: int | None = None) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.parent_id = parent_id
    channel.guild = MagicMock(id=GUILD_ID)
    channel.mention = f"<#{channel_id}>"
    return channel


def make_ctx(client: BotClient, command: str, channel: MagicMock) -> MagicMock:
    ctx = MagicMock()
    ctx.bot = client
    ctx.guild = MagicMock(id=GUILD_ID)
    ctx.channel = channel
    ctx.author = MagicMock(id=10, bot=False)
    ctx.command = client.get_command(command)
    ctx.send = AsyncMock()
    ctx.message = MagicMock(spec=discord.Message)
    ctx.message.delete = AsyncMock()
    return ctx


async def test_todos_los_juegos_leen_los_canales_que_elige_garito(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    try:
        games = [cog for cog in client.cogs.values() if hasattr(cog, "casino_channel_ids")]
        assert len(games) >= 20
        for cog in games:
            assert cog.casino_channel_ids is client.casino_channels, type(cog).__name__

        admin = client.get_cog("Admin")
        responder = MagicMock()
        responder.guild = MagicMock(id=GUILD_ID)
        responder.send = AsyncMock()
        await admin._garito_impl(responder, make_channel(CASINO), False)

        casino = client.get_cog("Casino")
        check = sys.modules[type(casino).__module__].casino_channel_error
        for cog in games:
            rule = cog.casino_channel_ids
            assert check(rule, make_channel(OTHER), "El juego") is not None
            assert check(rule, make_channel(CASINO), "El juego") is None
            assert check(rule, make_channel(99, parent_id=CASINO), "El juego") is None
    finally:
        await client.close()


async def test_con_punto_fuera_del_casino_el_aviso_se_borra_solo(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    try:
        await client.casino_channels.toggle(GUILD_ID, CASINO)
        casino = client.get_cog("Casino")
        ctx = make_ctx(client, "ruleta", make_channel(OTHER))

        await casino.ruleta_text.callback(casino, ctx)

        ctx.send.assert_awaited_once()
        assert ctx.send.await_args.args[0].startswith("🎰 La ruleta está en <#5>.")
        assert ctx.send.await_args.kwargs["delete_after"] == NOTICE_SECONDS
        ctx.message.delete.assert_awaited_once_with(delay=NOTICE_SECONDS)
    finally:
        await client.close()


async def test_con_punto_fuera_del_tajo_la_pala_se_borra_sola(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    try:
        await client.work.toggle_channel(GUILD_ID, CASINO)
        work = client.get_cog("Work")
        ctx = make_ctx(client, "pala", make_channel(OTHER))

        await work.pala_text.callback(work, ctx)

        ctx.send.assert_awaited_once()
        assert ctx.send.await_args.args[0].startswith("🪏 La pala está en <#5>.")
        assert ctx.send.await_args.kwargs["delete_after"] == NOTICE_SECONDS
        ctx.message.delete.assert_awaited_once_with(delay=NOTICE_SECONDS)
    finally:
        await client.close()
