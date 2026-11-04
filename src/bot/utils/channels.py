"""Canales donde se permite algo (la pala, el casino) y el aviso de cuando no.

Un administrador elige dónde se coge la pala (`tajo`) y dónde se juega al casino
(`garito`). Si alguien lo pide en otro canal, el bot le dice dónde está, sin
regañar: es un aviso que informa, no una broma (Biblia, «Personalidad»).

El aviso es un `ChannelNotice`, un texto normal que sabe que es de canal. Con `/`
ya sale en privado. Con `.`, `ContextResponder.send_error` lo reconoce y lo manda
con `send_notice`: el aviso y el mensaje que lo pidió se borran solos a los
`NOTICE_SECONDS`, para que el canal no quede con la bronca a la vista de todos.

Un canal permitido vale también para sus hilos (`parent_id`).
"""

from __future__ import annotations

from collections.abc import Iterable

import discord
from discord.ext import commands

# Lo que dura a la vista el aviso de un comando de texto antes de borrarse.
NOTICE_SECONDS = 20


class ChannelNotice(str):
    """Aviso de «esto se hace en otro canal».

    Se usa como cualquier texto; solo cambia cómo lo manda un comando de texto
    (ver `send_notice`).
    """

    __slots__ = ()


def channel_allowed(allowed: frozenset[int], channel: object) -> bool:
    """`True` si `channel` (o el canal del que cuelga, si es un hilo) está en `allowed`.

    Con `allowed` vacío vale cualquier canal.
    """
    if not allowed:
        return True
    return getattr(channel, "id", None) in allowed or getattr(channel, "parent_id", None) in allowed


def mentions(channel_ids: Iterable[int]) -> str:
    """Los canales como menciones (`<#id>`), ordenados y separados por comas."""
    return ", ".join(f"<#{channel_id}>" for channel_id in sorted(channel_ids))


def wrong_channel(
    allowed: frozenset[int], channel: object, what: str, emoji: str
) -> ChannelNotice | None:
    """Aviso de que `what` («La pala», «Los dados») va en otro canal, o `None` si vale.

    El verbo concuerda con `what`: «Los dados están en…», «La ruleta está en…».
    """
    if channel_allowed(allowed, channel):
        return None
    verb = "están" if what.split(" ", 1)[0] in {"Los", "Las"} else "está"
    return ChannelNotice(f"{emoji} {what} {verb} en {mentions(allowed)}.")


async def send_notice(ctx: commands.Context, text: str) -> None:
    """Manda un aviso de canal a un comando de texto y lo borra con el comando.

    El borrado del mensaje de quien escribió el comando necesita el permiso de
    gestionar mensajes; sin él, discord.py ignora el error y solo se borra el aviso.
    """
    await ctx.send(
        f"{text}\n-# Este aviso se borra solo en {NOTICE_SECONDS} segundos.",
        delete_after=NOTICE_SECONDS,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    message = getattr(ctx, "message", None)
    if isinstance(message, discord.Message):
        await message.delete(delay=NOTICE_SECONDS)
