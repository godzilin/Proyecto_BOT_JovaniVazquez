"""Adaptador común para responder a comandos de aplicación y de texto.

Todos los comandos del bot admiten dos formas de invocación equivalentes:
como comando de aplicación (`/comando`) y como comando de texto clásico
con un prefijo (por ejemplo `.comando`). Ambas comparten exactamente la
misma lógica de negocio; lo único que cambia es cómo se envía la
respuesta a Discord (`discord.Interaction` frente a `commands.Context`).

`CommandResponder` oculta esa diferencia detrás de una interfaz única,
para que los cogs no dupliquen sus reglas entre las dos variantes (ver
Biblia.txt, norma "No duplicar reglas entre comandos").

Cada `send_error` cuenta un `rechazo` del comando en la telemetría interna: los
comandos que más contestan con un error son los que confunden o fallan.

Un aviso de canal (`bot.utils.channels.ChannelNotice`, «la pala está en…») sale con
`/` en privado, como cualquier error; con `.`, se borra solo junto con el comando
(`send_notice`), para que no quede a la vista de todo el canal.
"""

from __future__ import annotations

import abc

import discord
from discord.ext import commands

from bot.services.telemetry import report_event
from bot.utils.channels import ChannelNotice, send_notice


class CommandResponder(abc.ABC):
    """Interfaz común para responder tanto a interacciones como a mensajes."""

    #: Servidor en el que se invocó el comando, o `None` fuera de un servidor.
    guild: discord.Guild | None
    #: Miembro que invocó el comando, si se pudo resolver como tal.
    member: discord.Member | None
    #: Canal en el que se invocó el comando, para reutilizarlo si hace falta.
    channel: discord.abc.Messageable | None

    @abc.abstractmethod
    async def send(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        ephemeral: bool = False,
        allowed_mentions: discord.AllowedMentions | None = None,
    ) -> None:
        """Envía una respuesta definitiva de una sola vez (sin progreso previo).

        `ephemeral` solo tiene efecto en comandos de aplicación: los
        comandos de texto no pueden ocultar su respuesta a otros miembros.
        """

    @abc.abstractmethod
    async def send_error(self, content: str) -> None:
        """Informa de un error de forma visible solo para quien invocó el comando."""

    @abc.abstractmethod
    async def start_progress(
        self, placeholder: str = "🔎 Buscando...", *, ephemeral: bool = False
    ) -> None:
        """Marca el inicio de una operación que puede tardar.

        Con `ephemeral=True`, en comandos de aplicación tanto el aviso como el
        resultado final (`finish`) solo los ve quien invocó el comando.
        """

    @abc.abstractmethod
    async def update_progress(self, content: str) -> None:
        """Cambia el texto del aviso de progreso por `content`.

        Para operaciones largas que quieren contar por dónde van. Quien la
        llame debe espaciar las actualizaciones: cada una es una edición de
        mensaje y Discord limita su ritmo.
        """

    @abc.abstractmethod
    async def finish(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        allowed_mentions: discord.AllowedMentions | None = None,
        file: discord.File | None = None,
    ) -> None:
        """Sustituye el aviso de progreso por el resultado final.

        `file` adjunta un archivo (por ejemplo una imagen generada).
        """


class InteractionResponder(CommandResponder):
    """Adaptador de `CommandResponder` para comandos de aplicación (`/...`)."""

    def __init__(self, interaction: discord.Interaction) -> None:
        self._interaction = interaction
        self.guild = interaction.guild
        member = interaction.user
        self.member = member if isinstance(member, discord.Member) else None
        self.channel = interaction.channel

    async def send(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        ephemeral: bool = False,
        allowed_mentions: discord.AllowedMentions | None = None,
    ) -> None:
        kwargs: dict[str, object] = {"content": content, "embed": embed, "ephemeral": ephemeral}
        if allowed_mentions is not None:
            kwargs["allowed_mentions"] = allowed_mentions
        if self._interaction.response.is_done():
            await self._interaction.followup.send(**kwargs)
        else:
            await self._interaction.response.send_message(**kwargs)

    async def send_error(self, content: str) -> None:
        command = self._interaction.command
        report_event("rechazo", command.qualified_name if command is not None else "?")
        await self.send(content, ephemeral=True)

    async def start_progress(
        self, placeholder: str = "🔎 Buscando...", *, ephemeral: bool = False
    ) -> None:
        # Discord no admite un texto de "cargando" personalizado para
        # comandos de aplicación; `defer` ya muestra el indicador nativo.
        await self._interaction.response.defer(thinking=True, ephemeral=ephemeral)

    async def update_progress(self, content: str) -> None:
        await self._interaction.edit_original_response(content=content)

    async def finish(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        allowed_mentions: discord.AllowedMentions | None = None,
        file: discord.File | None = None,
    ) -> None:
        kwargs: dict[str, object] = {"content": content, "embed": embed}
        if allowed_mentions is not None:
            kwargs["allowed_mentions"] = allowed_mentions
        if file is not None:
            kwargs["attachments"] = [file]
        await self._interaction.edit_original_response(**kwargs)


class ContextResponder(CommandResponder):
    """Adaptador de `CommandResponder` para comandos de texto con prefijo (`....`)."""

    def __init__(self, ctx: commands.Context) -> None:
        self._ctx = ctx
        self.guild = ctx.guild
        self.member = ctx.author if isinstance(ctx.author, discord.Member) else None
        self.channel = ctx.channel
        self._progress_message: discord.Message | None = None

    async def send(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        ephemeral: bool = False,  # noqa: ARG002 - los mensajes de texto no admiten ocultarse.
        allowed_mentions: discord.AllowedMentions | None = None,
    ) -> None:
        await self._ctx.send(content, embed=embed, allowed_mentions=allowed_mentions)

    async def send_error(self, content: str) -> None:
        command = self._ctx.command
        report_event("rechazo", command.qualified_name if command is not None else "?")
        if isinstance(content, ChannelNotice):
            await send_notice(self._ctx, content)
            return
        await self._ctx.send(content)

    async def start_progress(
        self,
        placeholder: str = "🔎 Buscando...",
        *,
        ephemeral: bool = False,  # noqa: ARG002 - los mensajes de texto no admiten ocultarse.
    ) -> None:
        self._progress_message = await self._ctx.send(placeholder)

    async def update_progress(self, content: str) -> None:
        if self._progress_message is None:
            self._progress_message = await self._ctx.send(content)
        else:
            await self._progress_message.edit(content=content)

    async def finish(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        allowed_mentions: discord.AllowedMentions | None = None,
        file: discord.File | None = None,
    ) -> None:
        if file is not None:
            # Añadir un archivo editando un mensaje es más frágil (exige permisos
            # extra y no siempre se refleja bien en todos los clientes): se envía
            # un mensaje nuevo con el archivo y se retira el aviso de progreso.
            await self._ctx.send(content, embed=embed, allowed_mentions=allowed_mentions, file=file)
            await self._discard_progress()
        elif self._progress_message is not None:
            await self._progress_message.edit(
                content=content, embed=embed, allowed_mentions=allowed_mentions
            )
        else:
            await self._ctx.send(content, embed=embed, allowed_mentions=allowed_mentions)

    async def _discard_progress(self) -> None:
        """Borra el aviso de progreso; si no se puede, no es un error."""
        if self._progress_message is None:
            return
        try:
            await self._progress_message.delete()
        except discord.HTTPException:
            pass  # Sin permiso o ya borrado: el resultado ya se envió.
