"""Cog de administración: moderación y utilidades solo para administradores.

Comandos (todos con `/` y con `.`, mismo nombre; `tajo` elige dónde se usa `pala`):
`purge`, `mute`, `unmute`, `kick`, `ban`, `unban`, `lock`, `unlock`,
`slow`, `say`, `nick`, `role`, `bienv`, `niveles`, `cambios`, `catalogo`, `tajo`.

Autorización: solo miembros con el permiso **Administrador** del servidor.
Se comprueba en el servidor en cada invocación (`cog_check` para `.` e
`interaction_check` para `/`); además, `default_permissions` oculta los
slash commands del menú a quien no es administrador, pero eso es solo
estética (ver Biblia.txt, sección 6).

Permisos que necesita el bot, según el comando: Gestionar mensajes
(`purge`), Aislar temporalmente a miembros (`mute`/`unmute`), Expulsar
(`kick`), Banear (`ban`/`unban`), Gestionar canales (`lock`/`unlock`/
`slow`), Gestionar apodos (`nick`) y Gestionar roles (`role` y los roles
que se venden con `catalogo`). Si falta
alguno, el comando lo dice en vez de fallar en silencio.

Cada acción queda en el registro de auditoría de Discord con el motivo y
quién la pidió.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from bot.cogs.deploy import can_post, news_channel, resolve_target
from bot.repositories.news import NewsSettings
from bot.repositories.welcome import WelcomeSettings
from bot.services.levels import MAX_XP_COOLDOWN_SECONDS, MIN_XP_COOLDOWN_SECONDS
from bot.services.moderation import (
    MAX_PURGE,
    MAX_SLOWMODE_SECONDS,
    format_duration,
    hierarchy_error,
    parse_duration,
    parse_user_id,
)
from bot.services.welcome import GifError, classify_gif
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

logger = logging.getLogger(__name__)

NOT_ADMIN = "Solo los administradores pueden usar este comando."
BOT_FORBIDDEN = "No tengo permiso para hacer eso. Revisa mis roles y permisos en el servidor."
# Discord borra en bloque solo mensajes de menos de 14 días; los más antiguos
# irían de uno en uno (una petición por mensaje), así que `purge` los ignora.
BULK_DELETE_WINDOW = timedelta(days=14)
# Mensajes recientes que `purge` revisa cuando filtra por miembro.
PURGE_SCAN_LIMIT = 500
# Segundos que sigue visible la confirmación de `.borrar` antes de borrarse.
PURGE_NOTICE_SECONDS = 5
# Discord limita el motivo del registro de auditoría a 512 caracteres.
AUDIT_REASON_LIMIT = 512
MAX_NICK_LENGTH = 32
# `say` puede mencionar usuarios, pero nunca @everyone, @here ni roles.
SAY_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=True)
# Palabras que en `.tajo` permiten la pala en cualquier canal.
TAJO_ALL_WORDS = {"todos", "todas", "cualquiera", "quitar"}
# Palabras que en `bienv` quitan el GIF y vuelven al vídeo de Kratos.
GIF_RESET_WORDS = {"quitar", "video", "vídeo", "ninguno"}

# Acciones de `niveles`; "mismo" vuelve a anunciar donde se sube de nivel.
LEVEL_ACTIONS = ("importar", "activar", "desactivar", "mismo")
NIVELES_USAGE = (
    "Uso: `.niveles` (estado), `.niveles importar`, `.niveles activar`, "
    "`.niveles desactivar`, `.niveles #canal`, `.niveles mismo` o `.niveles <segundos>` "
    f"({MIN_XP_COOLDOWN_SECONDS}–{MAX_XP_COOLDOWN_SECONDS})."
)

# Acciones de `cambios`: encender o apagar el aviso, su detalle y volver al canal de serie.
NEWS_ACTIONS = ("activar", "desactivar", "detallado", "resumen", "defecto")
CAMBIOS_USAGE = (
    "Uso: `.cambios` (estado), `.cambios activar`, `.cambios desactivar`, "
    "`.cambios detallado`, `.cambios resumen`, `.cambios #canal` (o un hilo) o `.cambios defecto` "
    "(vuelve a #chat-general)."
)


# Mención de canal (`<#123>`) o ID suelto, para los hilos que la caché no conoce.
CHANNEL_REFERENCE = re.compile(r"<#(\d+)>|(\d{15,20})")


async def _channel_or_thread_id(ctx: commands.Context, arg: str) -> int | None:
    """ID del canal de texto o hilo que nombra `arg`; `None` si no parece ninguno.

    Los hilos archivados no están en la caché y los conversores no los
    encuentran por nombre; por mención o ID valen igual y se comprueban luego
    contra Discord (`resolve_target`).
    """
    for converter in (commands.TextChannelConverter(), commands.ThreadConverter()):
        try:
            return (await converter.convert(ctx, arg)).id
        except commands.BadArgument:
            continue
    match = CHANNEL_REFERENCE.fullmatch(arg)
    return int(match[1] or match[2]) if match else None


PurgeableChannel = discord.TextChannel | discord.Thread | discord.VoiceChannel


def _audit_reason(actor: discord.abc.User, reason: str | None) -> str:
    """Motivo para el registro de auditoría, con quién ejecutó el comando."""
    text = f"{reason.strip() if reason else 'Sin motivo'} (por {actor})"
    return text[:AUDIT_REASON_LIMIT]


class Admin(commands.Cog):
    """Comandos de moderación reservados a administradores."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # --- Autorización -----------------------------------------------------

    async def cog_check(self, ctx: commands.Context) -> bool:  # type: ignore[override]
        """Solo administradores y solo dentro de un servidor (comandos `.`)."""
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if not isinstance(ctx.author, discord.Member) or (
            not ctx.author.guild_permissions.administrator
        ):
            raise commands.MissingPermissions(["administrator"])
        return True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:  # type: ignore[override]
        """Solo administradores y solo dentro de un servidor (comandos `/`)."""
        if interaction.guild is None:
            raise app_commands.NoPrivateMessage(
                interaction.command.name if interaction.command else ""
            )
        user = interaction.user
        if not isinstance(user, discord.Member) or not user.guild_permissions.administrator:
            raise app_commands.MissingPermissions(["administrator"])
        return True

    # --- Utilidades comunes -----------------------------------------------

    async def _attempt(self, responder: CommandResponder, action: Awaitable[object]) -> bool:
        """Ejecuta una llamada a Discord; si el bot no tiene permiso, lo explica.

        Returns:
            `True` si la acción se completó.
        """
        try:
            await action
        except discord.Forbidden:
            await responder.send_error(BOT_FORBIDDEN)
            return False
        return True

    async def _check_target(
        self, responder: CommandResponder, target: discord.Member, *, allow_self: bool = False
    ) -> bool:
        """Comprueba la jerarquía de roles; si no se puede, lo explica."""
        guild = target.guild
        actor = responder.member
        if actor is None:
            await responder.send_error(NOT_ADMIN)
            return False
        error = hierarchy_error(
            actor, target, guild.me, owner_id=guild.owner_id or 0, allow_self=allow_self
        )
        if error is not None:
            await responder.send_error(error)
            return False
        return True

    # --- purge ------------------------------------------------------------

    async def _purge(
        self,
        channel: PurgeableChannel,
        amount: int,
        member: discord.Member | None,
        reason: str,
        before: discord.Message | None = None,
    ) -> int:
        """Borra hasta `amount` mensajes recientes (de `member`, si se indica).

        Returns:
            Número de mensajes borrados.
        """
        deleted = 0

        def check(message: discord.Message) -> bool:
            nonlocal deleted
            if member is not None and message.author.id != member.id:
                return False
            if deleted >= amount:
                return False
            deleted += 1
            return True

        removed = await channel.purge(
            limit=amount if member is None else PURGE_SCAN_LIMIT,
            check=check,
            before=before,
            after=discord.utils.utcnow() - BULK_DELETE_WINDOW,
            # Con `after`, discord.py recorre el historial del más antiguo al más
            # nuevo y borraría los mensajes de hace casi 14 días, no los últimos.
            oldest_first=False,
            reason=reason,
        )
        return len(removed)

    @app_commands.command(name="borrar", description="Borra mensajes recientes del canal.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        cantidad="Cuántos mensajes (1-100).", miembro="Solo los de este miembro."
    )
    async def purge(
        self,
        interaction: discord.Interaction,
        cantidad: app_commands.Range[int, 1, MAX_PURGE],
        miembro: discord.Member | None = None,
    ) -> None:
        """Borra los últimos mensajes del canal; responde en efímero."""
        responder = InteractionResponder(interaction)
        channel = interaction.channel
        if not isinstance(channel, PurgeableChannel):
            await responder.send_error("Aquí no se pueden borrar mensajes.")
            return
        await responder.start_progress(ephemeral=True)
        try:
            count = await self._purge(
                channel, cantidad, miembro, _audit_reason(interaction.user, "purge")
            )
        except discord.Forbidden:
            await responder.finish(BOT_FORBIDDEN)
            return
        await responder.finish(f"🧹 {count} mensaje(s) borrado(s).")

    @commands.command(name="borrar")
    async def purge_text(
        self, ctx: commands.Context, cantidad: int, miembro: discord.Member | None = None
    ) -> None:
        """Versión de texto (`.`); la confirmación se borra sola a los pocos segundos."""
        if not 1 <= cantidad <= MAX_PURGE:
            await ctx.send(f"La cantidad debe estar entre 1 y {MAX_PURGE}.")
            return
        if not isinstance(ctx.channel, PurgeableChannel):
            await ctx.send("Aquí no se pueden borrar mensajes.")
            return
        try:
            count = await self._purge(
                ctx.channel,
                cantidad,
                miembro,
                _audit_reason(ctx.author, "purge"),
                before=ctx.message,
            )
            await ctx.message.delete()
        except discord.Forbidden:
            await ctx.send(BOT_FORBIDDEN)
            return
        await ctx.send(f"🧹 {count} mensaje(s) borrado(s).", delete_after=PURGE_NOTICE_SECONDS)

    # --- mute / unmute ----------------------------------------------------

    @app_commands.command(name="callar", description="Aísla a un miembro durante un tiempo.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        miembro="A quién aislar.",
        duracion="Ej.: 10m, 2h, 1d, 1h30m (máx. 28d). Sin unidad, minutos.",
        motivo="Queda en el registro de auditoría.",
    )
    async def mute(
        self,
        interaction: discord.Interaction,
        miembro: discord.Member,
        duracion: str,
        motivo: str | None = None,
    ) -> None:
        """Aplica un aislamiento temporal (timeout) de Discord."""
        await self._mute_impl(InteractionResponder(interaction), miembro, duracion, motivo)

    @commands.command(name="callar")
    async def mute_text(
        self, ctx: commands.Context, miembro: discord.Member, duracion: str, *, motivo: str = ""
    ) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._mute_impl(ContextResponder(ctx), miembro, duracion, motivo or None)

    async def _mute_impl(
        self, responder: CommandResponder, member: discord.Member, text: str, reason: str | None
    ) -> None:
        duration = parse_duration(text)
        if duration is None:
            await responder.send_error("Duración no válida. Ej.: `10m`, `2h`, `1d` (máx. 28d).")
            return
        if not await self._check_target(responder, member):
            return
        assert responder.member is not None
        if await self._attempt(
            responder, member.timeout(duration, reason=_audit_reason(responder.member, reason))
        ):
            await responder.send(
                f"🔇 {member.mention} aislado durante {format_duration(duration)}."
            )

    @app_commands.command(name="hablar", description="Quita el aislamiento a un miembro.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def unmute(self, interaction: discord.Interaction, miembro: discord.Member) -> None:
        """Retira el timeout del miembro."""
        await self._unmute_impl(InteractionResponder(interaction), miembro)

    @commands.command(name="hablar")
    async def unmute_text(self, ctx: commands.Context, miembro: discord.Member) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._unmute_impl(ContextResponder(ctx), miembro)

    async def _unmute_impl(self, responder: CommandResponder, member: discord.Member) -> None:
        if not member.is_timed_out():
            await responder.send_error(f"{member.display_name} no está aislado.")
            return
        if not await self._check_target(responder, member):
            return
        assert responder.member is not None
        if await self._attempt(
            responder, member.timeout(None, reason=_audit_reason(responder.member, None))
        ):
            await responder.send(f"🔊 {member.mention} ya puede hablar.")

    # --- kick / ban / unban -----------------------------------------------

    @app_commands.command(name="echar", description="Expulsa a un miembro del servidor.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def kick(
        self, interaction: discord.Interaction, miembro: discord.Member, motivo: str | None = None
    ) -> None:
        """Expulsa al miembro; puede volver con una invitación."""
        await self._kick_impl(InteractionResponder(interaction), miembro, motivo)

    @commands.command(name="echar")
    async def kick_text(
        self, ctx: commands.Context, miembro: discord.Member, *, motivo: str = ""
    ) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._kick_impl(ContextResponder(ctx), miembro, motivo or None)

    async def _kick_impl(
        self, responder: CommandResponder, member: discord.Member, reason: str | None
    ) -> None:
        if not await self._check_target(responder, member):
            return
        assert responder.member is not None
        if await self._attempt(
            responder, member.kick(reason=_audit_reason(responder.member, reason))
        ):
            await responder.send(f"👢 {member} expulsado.")

    @app_commands.command(name="banear", description="Banea a un miembro del servidor.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def ban(
        self, interaction: discord.Interaction, miembro: discord.Member, motivo: str | None = None
    ) -> None:
        """Banea al miembro sin borrar sus mensajes anteriores."""
        await self._ban_impl(InteractionResponder(interaction), miembro, motivo)

    @commands.command(name="banear")
    async def ban_text(
        self, ctx: commands.Context, miembro: discord.Member, *, motivo: str = ""
    ) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._ban_impl(ContextResponder(ctx), miembro, motivo or None)

    async def _ban_impl(
        self, responder: CommandResponder, member: discord.Member, reason: str | None
    ) -> None:
        if not await self._check_target(responder, member):
            return
        assert responder.member is not None
        if await self._attempt(
            responder,
            member.ban(reason=_audit_reason(responder.member, reason), delete_message_seconds=0),
        ):
            await responder.send(f"🔨 {member} baneado.")

    @app_commands.command(name="indultar", description="Levanta el baneo de un usuario.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(usuario="ID del usuario baneado.")
    async def unban(self, interaction: discord.Interaction, usuario: str) -> None:
        """Quita el baneo; el ID va como texto porque los IDs no caben en un entero de Discord."""
        await self._unban_impl(InteractionResponder(interaction), usuario)

    @commands.command(name="indultar")
    async def unban_text(self, ctx: commands.Context, usuario: str) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._unban_impl(ContextResponder(ctx), usuario)

    async def _unban_impl(self, responder: CommandResponder, text: str) -> None:
        user_id = parse_user_id(text)
        guild = responder.guild
        if user_id is None or guild is None or responder.member is None:
            await responder.send_error("Necesito el ID numérico del usuario.")
            return
        try:
            await guild.unban(
                discord.Object(id=user_id), reason=_audit_reason(responder.member, None)
            )
        except discord.NotFound:
            await responder.send_error("Ese usuario no está baneado.")
            return
        except discord.Forbidden:
            await responder.send_error(BOT_FORBIDDEN)
            return
        await responder.send(
            f"✅ Baneo levantado a <@{user_id}>.", allowed_mentions=discord.AllowedMentions.none()
        )

    # --- lock / unlock / slow ---------------------------------------------

    async def _set_locked(self, responder: CommandResponder, locked: bool) -> None:
        """Bloquea o desbloquea el canal actual para `@everyone`.

        Solo toca los permisos de escribir y de crear hilos de `@everyone`;
        los roles con permisos propios (moderadores, bots) siguen escribiendo.
        """
        channel = responder.channel
        guild = responder.guild
        if not isinstance(channel, discord.TextChannel) or guild is None:
            await responder.send_error("Solo funciona en canales de texto.")
            return
        assert responder.member is not None
        everyone = guild.default_role
        overwrite = channel.overwrites_for(everyone)
        if (overwrite.send_messages is False) == locked:
            await responder.send_error(
                "El canal ya está bloqueado." if locked else "El canal no está bloqueado."
            )
            return
        value = False if locked else None
        overwrite.update(
            send_messages=value, create_public_threads=value, create_private_threads=value
        )
        if await self._attempt(
            responder,
            channel.set_permissions(
                everyone,
                overwrite=None if overwrite.is_empty() else overwrite,
                reason=_audit_reason(responder.member, "lock" if locked else "unlock"),
            ),
        ):
            await responder.send("🔒 Canal bloqueado." if locked else "🔓 Canal desbloqueado.")

    @app_commands.command(name="cerrar", description="Impide escribir en este canal.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def lock(self, interaction: discord.Interaction) -> None:
        """Quita a `@everyone` el permiso de escribir en el canal actual."""
        await self._set_locked(InteractionResponder(interaction), True)

    @commands.command(name="cerrar")
    async def lock_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._set_locked(ContextResponder(ctx), True)

    @app_commands.command(name="abrir", description="Vuelve a permitir escribir en este canal.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def unlock(self, interaction: discord.Interaction) -> None:
        """Deshace `lock` en el canal actual."""
        await self._set_locked(InteractionResponder(interaction), False)

    @commands.command(name="abrir")
    async def unlock_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._set_locked(ContextResponder(ctx), False)

    @app_commands.command(name="lento", description="Modo lento del canal (0 lo quita).")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(segundos="Espera entre mensajes, 0-21600.")
    async def slow(
        self,
        interaction: discord.Interaction,
        segundos: app_commands.Range[int, 0, MAX_SLOWMODE_SECONDS],
    ) -> None:
        """Cambia el modo lento del canal actual."""
        await self._slow_impl(InteractionResponder(interaction), segundos)

    @commands.command(name="lento")
    async def slow_text(self, ctx: commands.Context, segundos: int) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._slow_impl(ContextResponder(ctx), segundos)

    async def _slow_impl(self, responder: CommandResponder, seconds: int) -> None:
        channel = responder.channel
        if not 0 <= seconds <= MAX_SLOWMODE_SECONDS:
            await responder.send_error(f"Debe estar entre 0 y {MAX_SLOWMODE_SECONDS} segundos.")
            return
        if not isinstance(channel, discord.TextChannel | discord.Thread):
            await responder.send_error("Solo funciona en canales de texto e hilos.")
            return
        assert responder.member is not None
        if await self._attempt(
            responder,
            channel.edit(slowmode_delay=seconds, reason=_audit_reason(responder.member, "slow")),
        ):
            await responder.send(
                f"🐢 Modo lento: {format_duration(timedelta(seconds=seconds))}."
                if seconds
                else "🐇 Modo lento desactivado."
            )

    # --- say --------------------------------------------------------------

    @app_commands.command(name="decir", description="El bot escribe tu mensaje.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(texto="Lo que dirá el bot.", canal="Dónde (por defecto, aquí).")
    async def say(
        self,
        interaction: discord.Interaction,
        texto: app_commands.Range[str, 1, 2000],
        canal: discord.TextChannel | None = None,
    ) -> None:
        """Publica `texto` como el bot. Nunca menciona a @everyone, @here ni roles."""
        responder = InteractionResponder(interaction)
        target = canal or interaction.channel
        if not isinstance(target, discord.abc.Messageable):
            await responder.send_error("No puedo escribir ahí.")
            return
        if await self._attempt(responder, target.send(texto, allowed_mentions=SAY_MENTIONS)):
            await responder.send("📣 Enviado.", ephemeral=True)

    @commands.command(name="decir")
    async def say_text(self, ctx: commands.Context, *, texto: str) -> None:
        """Versión de texto (`.`): borra tu mensaje y el bot escribe en su lugar."""
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass  # Sin "Gestionar mensajes" el texto se publica igualmente.
        await ctx.send(texto[:2000], allowed_mentions=SAY_MENTIONS)

    # --- nick / role ------------------------------------------------------

    @app_commands.command(name="apodo", description="Cambia o quita el apodo de un miembro.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(apodo="Vacío para quitarlo.")
    async def nick(
        self,
        interaction: discord.Interaction,
        miembro: discord.Member,
        apodo: app_commands.Range[str, 1, MAX_NICK_LENGTH] | None = None,
    ) -> None:
        """Cambia el apodo del miembro en este servidor."""
        await self._nick_impl(InteractionResponder(interaction), miembro, apodo)

    @commands.command(name="apodo")
    async def nick_text(
        self, ctx: commands.Context, miembro: discord.Member, *, apodo: str = ""
    ) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._nick_impl(ContextResponder(ctx), miembro, apodo or None)

    async def _nick_impl(
        self, responder: CommandResponder, member: discord.Member, nick: str | None
    ) -> None:
        if nick is not None and len(nick) > MAX_NICK_LENGTH:
            await responder.send_error(f"Máximo {MAX_NICK_LENGTH} caracteres.")
            return
        if not await self._check_target(responder, member, allow_self=True):
            return
        assert responder.member is not None
        if await self._attempt(
            responder, member.edit(nick=nick, reason=_audit_reason(responder.member, "nick"))
        ):
            await responder.send(
                f"✏️ Apodo de {member.mention}: **{discord.utils.escape_markdown(nick)}**."
                if nick
                else f"✏️ Apodo de {member.mention} quitado.",
                allowed_mentions=discord.AllowedMentions.none(),
            )

    @app_commands.command(name="rol", description="Da o quita un rol a un miembro.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def role(
        self, interaction: discord.Interaction, miembro: discord.Member, rol: discord.Role
    ) -> None:
        """Si el miembro tiene el rol, se lo quita; si no, se lo da."""
        await self._role_impl(InteractionResponder(interaction), miembro, rol)

    @commands.command(name="rol")
    async def role_text(
        self, ctx: commands.Context, miembro: discord.Member, *, rol: discord.Role
    ) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._role_impl(ContextResponder(ctx), miembro, rol)

    async def _role_impl(
        self, responder: CommandResponder, member: discord.Member, role: discord.Role
    ) -> None:
        actor = responder.member
        guild = member.guild
        assert actor is not None
        if role.is_default() or role.managed:
            await responder.send_error("Ese rol no se puede asignar a mano.")
            return
        if actor.id != guild.owner_id and role >= actor.top_role:
            await responder.send_error("Ese rol es igual o superior al tuyo.")
            return
        if role >= guild.me.top_role:
            await responder.send_error("Ese rol es igual o superior al mío; súbeme en la lista.")
            return
        reason = _audit_reason(actor, "role")
        had_role = role in member.roles
        action = (
            member.remove_roles(role, reason=reason)
            if had_role
            else member.add_roles(role, reason=reason)
        )
        if await self._attempt(responder, action):
            verb = "quitado a" if had_role else "dado a"
            await responder.send(
                f"🏷️ Rol {role.mention} {verb} {member.mention}.",
                allowed_mentions=discord.AllowedMentions.none(),
            )

    # --- bienv ------------------------------------------------------------

    @app_commands.command(name="bienv", description="Configura el GIF y el canal de bienvenida.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        gif="Enlace de Tenor, Giphy o .gif; «quitar» vuelve al vídeo.",
        canal="Canal donde se da la bienvenida.",
    )
    async def bienv(
        self,
        interaction: discord.Interaction,
        gif: app_commands.Range[str, 1, 512] | None = None,
        canal: discord.TextChannel | None = None,
    ) -> None:
        """Cambia el GIF o el canal de bienvenida y enseña cómo queda (solo a ti)."""
        await self._bienv_impl(InteractionResponder(interaction), gif, canal)

    @commands.command(name="bienv")
    async def bienv_text(
        self,
        ctx: commands.Context,
        canal: discord.TextChannel | None = None,
        *,
        gif: str = "",
    ) -> None:
        """Versión de texto: `.bienv`, `.bienv <enlace>`, `.bienv quitar`, `.bienv #canal`."""
        await self._bienv_impl(ContextResponder(ctx), gif.strip() or None, canal)

    async def _bienv_impl(
        self,
        responder: CommandResponder,
        gif: str | None,
        channel: discord.TextChannel | None,
    ) -> None:
        """Guarda lo que haya cambiado y responde con el resumen y una vista previa.

        Sin argumentos solo enseña la configuración actual.
        """
        guild = responder.guild
        member = responder.member
        repository = getattr(self.bot, "welcome", None)
        welcome = self.bot.get_cog("Welcome")
        if guild is None or member is None or repository is None or welcome is None:
            await responder.send_error("La bienvenida no está disponible ahora mismo.")
            return
        current: WelcomeSettings = await repository.settings(guild.id)
        gif_url = current.gif_url
        if gif is not None:
            if gif.lower() in GIF_RESET_WORDS:
                gif_url = None
            else:
                try:
                    classify_gif(gif)
                except GifError as error:
                    await responder.send_error(str(error))
                    return
                gif_url = gif.strip()
        updated = WelcomeSettings(
            gif_url=gif_url,
            channel_id=channel.id if channel is not None else current.channel_id,
        )
        if updated != current:
            await repository.save_settings(guild.id, updated)

        target = welcome.welcome_channel(guild, updated)
        lines = ["✅ Bienvenida actualizada." if updated != current else "👋 Bienvenida actual."]
        lines.append(f"Canal: {target.mention if target else '⚠️ ninguno (crea #chat-general)'}")
        lines.append(
            f"GIF: <{gif_url}>" if gif_url else "GIF: ninguno, se manda el vídeo de Kratos."
        )
        if target is not None and not target.permissions_for(guild.me).send_messages:
            lines.append("⚠️ No puedo escribir en ese canal; revisa mis permisos.")
        content, embed = await welcome.preview(member, updated)
        lines += ["", "**Así se verá:**", content]
        await responder.send(
            "\n".join(lines),
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # --- niveles ----------------------------------------------------------

    @app_commands.command(name="niveles", description="Enciende, apaga y configura los niveles.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        accion="Qué hacer; sin acción solo enseña el estado.",
        canal="Canal donde anunciar las subidas de nivel.",
        cooldown="Segundos entre mensajes que dan XP.",
    )
    @app_commands.choices(
        accion=[
            app_commands.Choice(name="Importar el historial y encender", value="importar"),
            app_commands.Choice(name="Encender", value="activar"),
            app_commands.Choice(name="Apagar (no borra nada)", value="desactivar"),
            app_commands.Choice(name="Anunciar donde se sube de nivel", value="mismo"),
        ]
    )
    async def niveles(
        self,
        interaction: discord.Interaction,
        accion: app_commands.Choice[str] | None = None,
        canal: discord.TextChannel | None = None,
        cooldown: app_commands.Range[int, MIN_XP_COOLDOWN_SECONDS, MAX_XP_COOLDOWN_SECONDS]
        | None = None,
    ) -> None:
        """Cambia lo que se pida y responde con el estado de los niveles (solo a ti)."""
        action = accion.value if accion is not None else None
        await self._niveles_impl(InteractionResponder(interaction), action, canal, cooldown)

    @commands.command(name="niveles")
    async def niveles_text(self, ctx: commands.Context, *args: str) -> None:
        """Versión de texto; acepta en cualquier orden una acción, un #canal y unos segundos."""
        action: str | None = None
        channel: discord.TextChannel | None = None
        cooldown: int | None = None
        for arg in args:
            word = arg.lower()
            if word in LEVEL_ACTIONS and action is None:
                action = word
                continue
            if arg.isdigit() and cooldown is None:
                cooldown = int(arg)
                if not MIN_XP_COOLDOWN_SECONDS <= cooldown <= MAX_XP_COOLDOWN_SECONDS:
                    await ctx.send(NIVELES_USAGE)
                    return
                continue
            try:
                converted = await commands.TextChannelConverter().convert(ctx, arg)
            except commands.BadArgument:
                converted = None
            if converted is None or channel is not None:
                await ctx.send(NIVELES_USAGE)
                return
            channel = converted
        await self._niveles_impl(ContextResponder(ctx), action, channel, cooldown)

    async def _niveles_impl(
        self,
        responder: CommandResponder,
        action: str | None,
        channel: discord.TextChannel | None,
        cooldown: int | None,
    ) -> None:
        """Aplica la acción y los ajustes pedidos y termina con el estado actual.

        Las acciones las ejecuta el cog `MessageStats`, que es quien lleva la
        importación del historial y el XP.
        """
        guild = responder.guild
        stats = self.bot.get_cog("MessageStats")
        if guild is None or stats is None:
            await responder.send_error("Los niveles no están disponibles ahora mismo.")
            return
        repository = stats.repository  # type: ignore[attr-defined]
        if action == "mismo" and channel is not None:
            await responder.send_error("Elige un canal o «mismo», no las dos cosas.")
            return

        lines: list[str] = []
        if channel is not None:
            await repository.set_level_announce_channel(guild.id, channel.id)
            lines.append(f"📣 Las subidas de nivel se anunciarán en {channel.mention}.")
            if not channel.permissions_for(guild.me).send_messages:
                lines.append("⚠️ No puedo escribir en ese canal; revisa mis permisos.")
        if cooldown is not None:
            await repository.set_level_cooldown(guild.id, cooldown)
            lines.append(f"⏱️ Un mensaje dará XP como mucho cada {cooldown} s.")

        progress = action == "importar"
        if action == "importar":
            # Buscar hilos archivados puede tardar más de los 3 s de una interacción.
            await responder.start_progress("🔎 Buscando canales e hilos...", ephemeral=True)
            lines.append(await stats.start_import(guild, responder.channel))  # type: ignore[attr-defined]
        elif action == "activar":
            lines.append(await stats.activate(guild))  # type: ignore[attr-defined]
        elif action == "desactivar":
            await repository.disable_levels(guild.id)
            lines.append("🔴 Niveles apagados. El XP guardado se queda donde está.")
        elif action == "mismo":
            await repository.set_level_announce_channel(guild.id, None)
            lines.append("📣 Las subidas de nivel se anunciarán donde se suba.")

        if lines:
            lines.append("")
        lines.append(await stats.overview(guild))  # type: ignore[attr-defined]
        text = "\n".join(lines)
        mentions = discord.AllowedMentions.none()
        if progress:
            await responder.finish(text, allowed_mentions=mentions)
        else:
            await responder.send(text, ephemeral=True, allowed_mentions=mentions)

    # --- cambios ----------------------------------------------------------

    @app_commands.command(name="cambios", description="Configura el aviso de novedades del bot.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        accion="Qué cambiar; sin acción solo enseña la configuración.",
        canal="Canal o hilo donde publicar el aviso.",
    )
    @app_commands.choices(
        accion=[
            app_commands.Choice(name="Publicar el aviso", value="activar"),
            app_commands.Choice(name="No publicar el aviso", value="desactivar"),
            app_commands.Choice(name="Detallado: lista y ficha de cada cambio", value="detallado"),
            app_commands.Choice(name="Resumen: solo la lista de cambios", value="resumen"),
            app_commands.Choice(name="Volver al canal por defecto", value="defecto"),
        ]
    )
    async def cambios(
        self,
        interaction: discord.Interaction,
        accion: app_commands.Choice[str] | None = None,
        # Un hilo llega tal cual (`AppCommandThread`): si está archivado no está en la
        # caché y pedir un `discord.Thread` haría fallar el comando antes de empezar.
        canal: discord.TextChannel | app_commands.AppCommandThread | None = None,
    ) -> None:
        """Cambia dónde y cómo se publican las novedades tras cada despliegue (solo a ti)."""
        action = accion.value if accion is not None else None
        channel_id = canal.id if canal is not None else None
        await self._cambios_impl(InteractionResponder(interaction), action, channel_id)

    @commands.command(name="cambios")
    async def cambios_text(self, ctx: commands.Context, *args: str) -> None:
        """Versión de texto; acepta en cualquier orden una acción y un #canal o hilo."""
        action: str | None = None
        channel_id: int | None = None
        for arg in args:
            word = arg.lower()
            if word in NEWS_ACTIONS and action is None:
                action = word
                continue
            converted = await _channel_or_thread_id(ctx, arg)
            if converted is None or channel_id is not None:
                await ctx.send(CAMBIOS_USAGE)
                return
            channel_id = converted
        await self._cambios_impl(ContextResponder(ctx), action, channel_id)

    async def _cambios_impl(
        self,
        responder: CommandResponder,
        action: str | None,
        channel_id: int | None,
    ) -> None:
        """Guarda lo que haya cambiado y responde con la configuración del aviso.

        Sin argumentos solo la enseña. Los ajustes viven en `bot.news`
        (`NewsRepository`) y los lee el cog `Despliegue` al publicar.
        """
        guild = responder.guild
        repository = getattr(self.bot, "news", None)
        if guild is None or repository is None:
            await responder.send_error("El aviso de novedades no está disponible ahora mismo.")
            return
        if action == "defecto" and channel_id is not None:
            await responder.send_error("Elige un canal o «defecto», no las dos cosas.")
            return
        if channel_id is not None and await resolve_target(guild, channel_id) is None:
            await responder.send_error(
                "No encuentro ese canal o hilo, o no es de texto. Si es un hilo privado, "
                "añádeme a él primero."
            )
            return
        current: NewsSettings = await repository.settings(guild.id)
        if channel_id is None:
            channel_id = None if action == "defecto" else current.channel_id
        updated = NewsSettings(
            enabled={"activar": True, "desactivar": False}.get(action or "", current.enabled),
            channel_id=channel_id,
            detailed={"detallado": True, "resumen": False}.get(action or "", current.detailed),
        )
        if updated != current:
            await repository.save_settings(guild.id, updated)

        target = await news_channel(guild, updated)
        lines = [
            "✅ Aviso de novedades actualizado." if updated != current else "📜 Aviso de novedades."
        ]
        lines.append(f"Estado: {'se publica' if updated.enabled else 'no se publica'}.")
        lines.append(
            "Formato: "
            + (
                "detallado (lista de cambios y una ficha por PR con su descripción)."
                if updated.detailed
                else "resumen (solo la lista de cambios)."
            )
        )
        if updated.channel_id is not None and (target is None or target.id != updated.channel_id):
            lines.append("⚠️ El canal elegido ya no existe o no lo veo; uso el de por defecto.")
        lines.append(f"Canal: {target.mention if target else '⚠️ ninguno (crea #chat-general)'}")
        if target is not None and not can_post(target, guild.me):
            lines.append("⚠️ No puedo escribir ahí; revisa mis permisos.")
        await responder.send(
            "\n".join(lines), ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    # --- tajo -------------------------------------------------------------

    @app_commands.command(name="tajo", description="Elige en qué canales se coge la pala.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        canal="Canal que añadir o quitar; sin canal, enseña los que hay.",
        todos="Permitir la pala en cualquier canal (borra la lista).",
    )
    async def tajo(
        self,
        interaction: discord.Interaction,
        canal: discord.TextChannel | None = None,
        todos: bool = False,
    ) -> None:
        """Añade o quita un canal de `pala`, o los quita todos (solo lo ves tú)."""
        await self._tajo_impl(InteractionResponder(interaction), canal, todos)

    @commands.command(name="tajo")
    async def tajo_text(self, ctx: commands.Context, *, arg: str = "") -> None:
        """Versión de texto: `.tajo`, `.tajo #canal` o `.tajo todos`."""
        word = arg.strip().lower()
        if word in TAJO_ALL_WORDS:
            await self._tajo_impl(ContextResponder(ctx), None, True)
            return
        channel: discord.TextChannel | None = None
        if word:
            try:
                channel = await commands.TextChannelConverter().convert(ctx, arg.strip())
            except commands.BadArgument:
                await ctx.send("Uso: `.tajo` (estado), `.tajo #canal` o `.tajo todos`.")
                return
        await self._tajo_impl(ContextResponder(ctx), channel, False)

    async def _tajo_impl(
        self,
        responder: CommandResponder,
        channel: discord.TextChannel | None,
        clear: bool,
    ) -> None:
        """Cambia los canales de `pala` y responde con cómo quedan."""
        guild = responder.guild
        work = getattr(self.bot, "work", None)
        if guild is None or work is None:
            await responder.send_error("El trabajo no está disponible ahora mismo.")
            return
        lines: list[str] = []
        if clear:
            await work.clear_channels(guild.id)
            lines.append("🪏 La pala se puede coger en cualquier canal.")
        elif channel is not None:
            added, _ = await work.toggle_channel(guild.id, channel.id)
            lines.append(f"🪏 {channel.mention} {'añadido a' if added else 'quitado de'} la pala.")
            if added and not channel.permissions_for(guild.me).send_messages:
                lines.append("⚠️ No puedo escribir en ese canal; revisa mis permisos.")
        allowed = await work.channels(guild.id)
        lines.append(
            "Canales de la pala: " + ", ".join(f"<#{c}>" for c in sorted(allowed))
            if allowed
            else "Canales de la pala: cualquiera."
        )
        await responder.send(
            "\n".join(lines), ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    # --- catalogo ---------------------------------------------------------

    @app_commands.command(
        name="catalogo", description="Monta la tienda: roles, XP y coleccionables."
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def catalogo(self, interaction: discord.Interaction) -> None:
        """Abre la trastienda (solo la ves tú): crear, editar, rebajar y retirar artículos."""
        shop = self.bot.get_cog("Tienda")
        if shop is None:
            await InteractionResponder(interaction).send_error(
                "La tienda no está disponible ahora mismo."
            )
            return
        await shop.open_admin_panel(interaction=interaction)  # type: ignore[attr-defined]

    @commands.command(name="catalogo")
    async def catalogo_text(self, ctx: commands.Context) -> None:
        """Versión de texto: deja el panel en el canal, pero solo tú lo puedes tocar."""
        shop = self.bot.get_cog("Tienda")
        if shop is None:
            await ctx.send("La tienda no está disponible ahora mismo.")
            return
        await shop.open_admin_panel(ctx=ctx)  # type: ignore[attr-defined]


async def setup(bot: commands.Bot) -> None:
    """Registra el cog de administración en el cliente."""
    await bot.add_cog(Admin(bot))
