"""Pruebas del aviso de canal equivocado (`bot.utils.channels`) y de cómo sale con `.`."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from bot.cogs.casino import casino_channel_error
from bot.utils.channels import (
    NOTICE_SECONDS,
    ChannelNotice,
    channel_allowed,
    send_notice,
    wrong_channel,
)
from bot.utils.responder import ContextResponder


def make_ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.send = AsyncMock()
    ctx.message = MagicMock(spec=discord.Message)
    ctx.message.delete = AsyncMock()
    ctx.author = MagicMock()
    ctx.command.qualified_name = "pala"
    return ctx


def test_sin_canales_elegidos_vale_cualquiera() -> None:
    assert channel_allowed(frozenset(), SimpleNamespace(id=1))
    assert wrong_channel(frozenset(), SimpleNamespace(id=1), "La pala", "🪏") is None


def test_un_hilo_de_un_canal_permitido_tambien_vale() -> None:
    allowed = frozenset({5})
    assert channel_allowed(allowed, SimpleNamespace(id=5, parent_id=None))
    assert channel_allowed(allowed, SimpleNamespace(id=99, parent_id=5))
    assert not channel_allowed(allowed, SimpleNamespace(id=99, parent_id=7))


def test_el_aviso_solo_dice_donde_esta() -> None:
    notice = wrong_channel(frozenset({7, 5}), SimpleNamespace(id=1), "La pala", "🪏")
    assert isinstance(notice, ChannelNotice)
    assert notice == "🪏 La pala está en <#5>, <#7>."


def test_el_verbo_concuerda_con_el_juego() -> None:
    channel = SimpleNamespace(id=1)
    assert casino_channel_error(frozenset({5}), channel, "Los dados") == (
        "🎰 Los dados están en <#5>."
    )
    assert casino_channel_error(frozenset({5}), channel, "La ruleta") == (
        "🎰 La ruleta está en <#5>."
    )
    assert casino_channel_error(frozenset({5}), SimpleNamespace(id=5), "La ruleta") is None


async def test_con_punto_el_aviso_y_el_comando_se_borran_solos() -> None:
    ctx = make_ctx()
    await send_notice(ctx, "🪏 La pala está en <#5>.")
    ctx.send.assert_awaited_once()
    assert ctx.send.await_args.kwargs["delete_after"] == NOTICE_SECONDS
    assert "se borra solo" in ctx.send.await_args.args[0]
    ctx.message.delete.assert_awaited_once_with(delay=NOTICE_SECONDS)


async def test_el_responder_de_texto_borra_solo_los_avisos_de_canal() -> None:
    ctx = make_ctx()
    await ContextResponder(ctx).send_error(ChannelNotice("🪏 La pala está en <#5>."))
    assert ctx.send.await_args.kwargs["delete_after"] == NOTICE_SECONDS

    ctx = make_ctx()
    await ContextResponder(ctx).send_error("No te llega el saldo.")
    ctx.send.assert_awaited_once_with("No te llega el saldo.")
    ctx.message.delete.assert_not_awaited()
