"""Cog de administración: moderación y utilidades solo para administradores.

Comandos (todos con `/` y con `.`, mismo nombre; `tajo` elige dónde se usa `pala`):
`purge`, `mute`, `unmute`, `kick`, `ban`, `unban`, `lock`, `unlock`,
`slow`, `say`, `nick`, `role`, `bienv`, `aspecto`, `niveles`, `cambios`, `catalogo`, `tajo`,
`informe` (descarga la telemetría interna, ver `bot.cogs.telemetry`), `robuso` (si
Robuso está en Hong Kong o en Madrid, para sus logros: ver `bot.cogs.robuso`),
`bloquear`, `crear`, `dinero`, `repicar` (solo con `.`: vuelve a jugar una tirada del
pico para auditarla, sin mover dinero), `enchufe` y `amanece`.

`bloquear` es el freno de emergencia para un fallo o un exploit. Como `reinicio`, solo
lo usan los IDs de `DEPLOYERS` (Yeyo y Dani), sean o no administradores, y no sale en
`ayuda`. Un comando bloqueado
no se ejecuta para nadie (ni con `/` ni con `.`), y los botones, menús y formularios
que vienen de su mismo archivo (las mesas que ya estaban abiertas, el 🔁 de otra
partida) tampoco responden. Se guarda en la base de datos, así que sigue bloqueado
tras un reinicio o un despliegue hasta que se desbloquea con el mismo comando.
Los mismos controles cierran la puerta durante un reinicio: ningún comando salvo
`reinicio` y `bloquear`, y los botones con un aviso o parados (modo reinicio, ver
`bot.cogs.deploy`).

`crear` mete en la mochila de un miembro un objeto de la tienda (para devolver uno
perdido por un fallo), con los mismos dos IDs y sin salir en `ayuda`. Solo objetos:
un rol no se daría en Discord, un potenciador no caducaría y una mascota se adopta.
No cobra nada ni gasta existencias, y lo que da llega sin número de serie.

`dinero` mete yapdollars en la cuenta de un miembro (o en la tuya), con los mismos dos
IDs y sin salir en `ayuda`: para devolver lo perdido por un fallo o dar un premio.
Salen de la nada, como un regalo de cumpleaños: exentos de IRPF (`EconomyService.grant`)
y, si debe a Hacienda, con su embargo. Quedan en el libro con su motivo (`admin:dinero`).
Con `quitar` (`.dinero quitar 5000`) los saca en vez de darlos; si no le llega, se queda a cero.

`enchufe` sube de puesto en `pala` a un miembro sin pedirle barra, días, tareas ni
formación. `amanece` le vacía las franjas de hoy (curro y negocio) y las horas extra
de la semana, y le llena la batería. Los dos son solo para Yeyo (`SECRET_OWNER`), solo
con `.` y en secreto: no salen en `ayuda` ni en la telemetría, con cualquier otro el
bot calla como ante un comando que no existe, borran el mensaje y contestan por privado.
También se escriben por privado al bot: valen para el único servidor que comparten el
bot y el miembro (`_secret_guild`), y el miembro va por su ID o su nombre de usuario.

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
quién la pidió. Las sanciones (`mute`, `unmute`, `kick`, `ban`, `unban`) se
apuntan además en el historial del panel `admin` (`bot.cogs.moderation.record`),
que junta estas mismas acciones en un menú.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import io
import json
import logging
import math
import re
import sqlite3
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, Literal

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui.view import BaseView, ViewStore

from bot.cogs import moderation as historial
from bot.cogs import robuso as robuso_logros
from bot.cogs.aspecto import ACTIONS, Aspecto
from bot.cogs.deploy import DEPLOYERS, Deploy, can_post, news_channel, resolve_target
from bot.cogs.errors import BLOCKED_ERROR, CLOSING_CLICK, RESTART_CLICK, RESTART_ERROR
from bot.cogs.telemetry import Telemetry, report_inputs
from bot.repositories.news import NewsSettings
from bot.repositories.welcome import WelcomeSettings
from bot.services.economy import format_amount
from bot.services.levels import MAX_XP_COOLDOWN_SECONDS, MIN_XP_COOLDOWN_SECONDS
from bot.services.moderation import (
    MAX_PURGE,
    MAX_SLOWMODE_SECONDS,
    format_duration,
    hierarchy_error,
    parse_duration,
    parse_user_id,
)
from bot.services.pala import WorkError
from bot.services.shop import Kind, ShopItem
from bot.services.telemetry import LISTENER_ITEM, TelemetryService
from bot.services.welcome import GifError, classify_gif
from bot.utils.cogs import find_cog
from bot.utils.interactions import notify
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
# Lo que acepta `.robuso`: dónde está, sin espacios ni mayúsculas.
ROBUSO_PLACES = {"hongkong": True, "madrid": False}
# Palabras que en `bienv` quitan el GIF y vuelven al vídeo de Kratos.
GIF_RESET_WORDS = {"quitar", "video", "vídeo", "ninguno"}
# Lo que `bloquear` no bloquea, ni el modo reinicio: el desbloqueo y el despliegue.
UNBLOCKABLE = {"bloquear", "reinicio"}
# Comandos del cog que filtran por `DEPLOYERS` en vez de por Administrador.
DEPLOYER_COMMANDS = {"bloquear", "crear", "dinero"}
# Tope de `dinero` por vez: un cero de más al teclear no rompe la economía.
MAX_DINERO = 100_000_000
# Único que puede usar los comandos secretos (`enchufe`, `amanece`): Yeyo (Dani no).
SECRET_OWNER = 403646452414545921
ENCHUFE_USAGE = "Uso: `.enchufe @miembro [puestos] [rama]` (por privado, su ID o su nombre)."
AMANECE_USAGE = "Uso: `.amanece` (a ti) o `.amanece @miembro` (por privado, su ID o su nombre)."
# Por privado, el secreto no sabe de qué servidor hablas si compartís más de uno.
SECRET_GUILD_ERROR = "No sé en qué servidor: compartimos más de uno o ninguno con {name}."
# Marca de los métodos de discord.py ya envueltos por `install_click_block`.
_BLOCK_GUARD = "_blocked_command_guard"

# Ventana del informe de uso (`informe`), en días. Más de 90 no aporta: los
# usuarios por día se borran a los 90 (`bot.repositories.telemetry`).
DEFAULT_REPORT_DAYS = 30
MAX_REPORT_DAYS = 90
INFORME_USAGE = (
    f"Uso: `.informe [días]` (1 a {MAX_REPORT_DAYS}) para descargarlo, `.informe #canal` "
    "para publicarlo ahí cada día o `.informe quitar` para dejar de publicarlo."
)

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


def pick_object(items: list[ShopItem], text: str) -> ShopItem | str:
    """El objeto que se pide en `crear`: por id (el autocompletado), nombre o clave.

    Un trozo del nombre vale si solo lo tiene un objeto: si lo tienen varios, no se
    adivina cuál era.

    Returns:
        El objeto, o un mensaje de error para enseñarlo tal cual.
    """
    text = text.strip()
    needle = text.casefold()
    if text.isdigit():
        # Un número es el id del autocompletado; si no es de un objeto, no se busca
        # en los nombres («1» daría el Modelo 100).
        found = [i for i in items if i.id == int(text)]
    else:
        found = [i for i in items if needle in (i.name.casefold(), i.catalog_key)]
        found = found or [i for i in items if needle and needle in i.name.casefold()]
    if len(found) == 1:
        return found[0]
    if found:
        names = ", ".join(f"{i.emoji} {i.name}" for i in found[:8])
        return (
            f"Hay varios objetos con ese nombre: {names}. Escribe el nombre entero o "
            "elígelo en la lista de `/crear`."
        )
    return f"No hay ningún objeto «{discord.utils.escape_markdown(text)}» en la tienda."


def _blocks_click(module: str, interaction: discord.Interaction) -> bool:
    """Si un botón, menú o formulario del archivo `module` es de un comando bloqueado."""
    cog = find_cog(interaction.client, Admin)  # type: ignore[arg-type]
    if cog is None or interaction.guild_id is None:
        return False
    return module in cog.blocked_modules(interaction.guild_id)


def _restart(bot: object) -> Deploy | None:
    """El cog `Deploy` si hay un reinicio en marcha (pedido o apagándose)."""
    deploy = find_cog(bot, Deploy)  # type: ignore[arg-type]
    return deploy if deploy is not None and (deploy.restarting or deploy.closing) else None


Locate = Callable[..., tuple[str, discord.Interaction]]

# Por dónde corre discord.py cada clic y formulario, y de qué archivo es cada uno: la
# vista o el formulario, o la clase del botón dinámico (los del Rastro, `lista`…).
_DISPATCHERS: tuple[tuple[type, str, Locate], ...] = (
    (BaseView, "_scheduled_task", lambda view, _item, i: (type(view).__module__, i)),
    (discord.ui.Modal, "_scheduled_task", lambda form, i, *_: (type(form).__module__, i)),
    (ViewStore, "schedule_dynamic_item_call", lambda _store, _t, cls, i, *_: (cls.__module__, i)),
)


#: Lo que se espera a que el listener conteste un botón suyo antes de avisar del reinicio.
LISTENER_WAIT = 2.5


async def _after_listener(args: tuple[Any, ...], interaction: discord.Interaction) -> None:
    """Si el botón lo contesta un listener (el minijuego de `pala` o del negocio), le
    deja contestar primero: así el aviso va aparte y no le quita la respuesta."""
    item = args[0] if args else None
    if not getattr(item, LISTENER_ITEM, False):
        return
    deadline = time.monotonic() + LISTENER_WAIT
    while not interaction.response.is_done() and time.monotonic() < deadline:
        await asyncio.sleep(0.05)


def _guarded(original: Callable[..., Awaitable[Any]], locate: Locate) -> Callable[..., Any]:
    """Envuelve un reparto de discord.py: el clic bloqueado se contesta y no sigue.

    Con un reinicio pedido, el clic sigue (para terminar la partida) y la primera vez
    se avisa; con el bot apagándose, se para.
    """

    @functools.wraps(original)
    async def run(self: Any, *args: Any) -> Any:
        module, interaction = locate(self, *args)
        if _blocks_click(module, interaction):
            await notify(interaction, BLOCKED_ERROR)
            return None
        restart = _restart(interaction.client)
        if restart is not None and restart.closing:
            await _after_listener(args, interaction)
            with contextlib.suppress(discord.HTTPException):
                await notify(interaction, CLOSING_CLICK)
            return None
        result = await original(self, *args)
        # Después del clic, para no comerle sus 3 segundos. shortcut: el 🔁 de otra
        # partida es un clic y la abre igual (el reinicio la espera como a las demás);
        # si da guerra, que el 🔁 de cada juego mire `restarting` antes de cobrar.
        if restart is not None and interaction.user.id not in restart.warned:
            restart.warned.add(interaction.user.id)
            await _after_listener(args, interaction)
            with contextlib.suppress(discord.HTTPException):
                await notify(interaction, RESTART_CLICK)
        return result

    return run


def install_click_block() -> None:
    """Para los clics y formularios de los comandos bloqueados, una vez por proceso.

    Los métodos de `_DISPATCHERS` son internos de discord.py y corren el callback de cada
    botón, menú, formulario o botón dinámico (también los que rescata `bot.cogs.errors`);
    si cambian, falla `tests/integration/test_bloquear.py`. Sin el cog `Admin`, el
    envoltorio solo mira el modo reinicio (al apagarse, `Admin` se descarga antes que
    `Deploy`).
    shortcut: no para los clics que atiende un listener propio (los del minijuego de
    `pala`, `claim_custom_ids`); si un fallo pasa por ahí, añadir allí la misma comprobación.
    """
    for owner, name, locate in _DISPATCHERS:
        original = owner.__dict__[name]
        if not getattr(original, _BLOCK_GUARD, False):
            wrapped = _guarded(original, locate)
            setattr(wrapped, _BLOCK_GUARD, True)
            setattr(owner, name, wrapped)


class Admin(commands.Cog):
    """Comandos de moderación reservados a administradores."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        #: Comandos bloqueados con `bloquear`, por servidor (copia en memoria de la base).
        self.blocked: dict[int, set[str]] = {}
        self._previous_tree_check = bot.tree.interaction_check

    async def cog_load(self) -> None:
        """Lee los comandos bloqueados y pone el control en `.`, `/` y los botones."""
        try:
            self.blocked = await self.bot.moderation.blocked_commands()  # type: ignore[attr-defined]
        except sqlite3.Error:
            logger.exception("No se pudieron leer los comandos bloqueados")
        self.bot.add_check(self._text_allowed)
        self.bot.tree.interaction_check = self._slash_allowed  # type: ignore[method-assign]
        install_click_block()

    async def cog_unload(self) -> None:
        """Quita los controles de `cog_load` (los botones dejan de mirar al no haber cog)."""
        self.bot.remove_check(self._text_allowed)
        self.bot.tree.interaction_check = self._previous_tree_check  # type: ignore[method-assign]

    def blocked_modules(self, guild_id: int) -> set[str]:
        """Archivos de los comandos bloqueados en el servidor: sus vistas no responden."""
        modules = set()
        for name in self.blocked.get(guild_id, ()):
            command = self.bot.get_command(name) or self.bot.tree.get_command(name)
            if command is not None and command.module:
                modules.add(command.module)
        return modules

    async def _text_allowed(self, ctx: commands.Context) -> bool:
        """Control global de los comandos `.` (`bot.add_check`): el bloqueado no corre."""
        if (
            ctx.command is not None
            and ctx.command.qualified_name not in UNBLOCKABLE
            and _restart(self.bot) is not None
        ):
            raise commands.DisabledCommand(RESTART_ERROR)
        if (
            ctx.guild is not None
            and ctx.command is not None
            and ctx.command.qualified_name in self.blocked.get(ctx.guild.id, ())
        ):
            # `Errors` lo contesta con `BLOCKED_ERROR`.
            raise commands.DisabledCommand()
        return True

    async def _slash_allowed(self, interaction: discord.Interaction) -> bool:
        """Control global de los comandos `/` (el `interaction_check` del árbol).

        Con `False` discord.py no ejecuta el comando ni pasa por el gestor de errores,
        así que el aviso se da aquí. El autocompletado no se para: no hace nada.
        """
        name = (interaction.data or {}).get("name")
        if interaction.type is not discord.InteractionType.application_command:
            return True
        if name not in UNBLOCKABLE and _restart(self.bot) is not None:
            text = RESTART_ERROR
        elif interaction.guild_id is not None and name in self.blocked.get(
            interaction.guild_id, ()
        ):
            text = BLOCKED_ERROR
        else:
            return True
        await interaction.response.send_message(text, ephemeral=True)
        return False

    # --- Autorización -----------------------------------------------------

    async def cog_check(self, ctx: commands.Context) -> bool:  # type: ignore[override]
        """Solo administradores y solo dentro de un servidor (comandos `.`)."""
        if ctx.command is not None and ctx.command.extras.get("secret"):
            return True  # Los secretos callan por su cuenta con todos menos Yeyo (`_secret_start`).
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if ctx.command is not None and ctx.command.name in DEPLOYER_COMMANDS:
            return True  # Lo filtra su `_impl` por `DEPLOYERS`, como `reinicio`.
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
        if interaction.command is not None and interaction.command.name in DEPLOYER_COMMANDS:
            return True  # Lo filtra su `_impl` por `DEPLOYERS`, como `reinicio`.
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

    async def _log(
        self,
        responder: CommandResponder,
        user_id: int,
        kind: str,
        reason: str | None,
        seconds: int | None = None,
    ) -> None:
        """Apunta la sanción en el historial del panel `admin` (tras contestar)."""
        if responder.guild is not None and responder.member is not None:
            await historial.record(
                self.bot, responder.guild.id, user_id, responder.member.id, kind, reason, seconds
            )

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
            await self._log(responder, member.id, "callar", reason, int(duration.total_seconds()))

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
            await self._log(responder, member.id, "hablar", None)

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
            await self._log(responder, member.id, "echar", reason)

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
            await self._log(responder, member.id, "banear", reason)

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
        await self._log(responder, user_id, "indultar", None)

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

    # --- aspecto ----------------------------------------------------------

    @app_commands.command(name="aspecto", description="Configura los looks de Jovani.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        accion="Qué hacer; sin acción solo enseña el panel.",
        look="Look sobre el que actuar.",
        imagen="Imagen para el avatar o el banner; sin imagen se quita la que haya.",
        texto="Nombre del look nuevo, bio o estado; admite {version} y {dato}.",
        horas="Horas que dura un look fijado; sin horas, hasta quitarlo.",
    )
    @app_commands.choices(
        accion=[app_commands.Choice(name=name, value=key) for key, name in ACTIONS.items()]
    )
    async def aspecto(
        self,
        interaction: discord.Interaction,
        accion: str = "",
        look: str = "",
        imagen: discord.Attachment | None = None,
        texto: app_commands.Range[str, 1, 200] = "",
        horas: app_commands.Range[int, 1, 720] | None = None,
    ) -> None:
        """Cambia o fija los looks de Jovani y enseña cómo queda (solo a ti).

        Efecto visible: el avatar, el banner, la bio y el estado del bot.
        """
        await self._aspecto_impl(
            InteractionResponder(interaction), accion, look, imagen, texto, horas
        )

    @aspecto.autocomplete("look")
    async def aspecto_look_options(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Propone los looks del servidor al escribir."""
        cog = find_cog(self.bot, Aspecto)
        if cog is None or interaction.guild_id is None:
            return []
        options = await cog.look_options(interaction.guild_id, current)
        return [app_commands.Choice(name=name, value=key) for name, key in options]

    @commands.command(name="aspecto")
    async def aspecto_text(
        self, ctx: commands.Context, accion: str = "", look: str = "", *, texto: str = ""
    ) -> None:
        """Versión de texto: `.aspecto`, `.aspecto fijar casino 6`, `.aspecto auto`,
        `.aspecto crear Modo tortilla`, `.aspecto borrar tortilla`, `.aspecto bio casino
        Texto`, `.aspecto estado casino Texto` y `.aspecto avatar casino` o `.aspecto banner
        casino` con la imagen adjunta."""
        accion = accion.lower()
        image = next(
            (a for a in ctx.message.attachments if (a.content_type or "").startswith("image/")),
            None,
        )
        hours = None
        if accion == "fijar" and texto.strip():
            # Un texto que no es un número no puede acabar fijando el look para siempre.
            if not texto.strip().isdecimal():
                await ctx.send("⚠️ Las horas tienen que ser un número: `.aspecto fijar casino 6`.")
                return
            hours = int(texto)
        if accion == "crear":
            look, texto = "", f"{look} {texto}"
        await self._aspecto_impl(ContextResponder(ctx), accion, look, image, texto, hours)

    async def _aspecto_impl(
        self,
        responder: CommandResponder,
        action: str,
        look: str,
        image: discord.Attachment | None,
        text: str,
        hours: int | None,
    ) -> None:
        """Pasa `aspecto` al cog que lleva los looks."""
        cog = find_cog(self.bot, Aspecto)
        if cog is None:
            await responder.send_error("El aspecto no está disponible ahora mismo.")
            return
        await cog.manage(responder, action, look, image, text, hours)

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

    # --- robuso -----------------------------------------------------------

    @app_commands.command(
        name="robuso", description="Dice si Robuso está en Hong Kong o en Madrid."
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(donde="Dónde está ahora; sin elegir, enseña dónde está apuntado.")
    @app_commands.choices(
        donde=[
            app_commands.Choice(name="Hong Kong", value="hongkong"),
            app_commands.Choice(name="Madrid", value="madrid"),
        ]
    )
    async def robuso(
        self, interaction: discord.Interaction, donde: app_commands.Choice[str] | None = None
    ) -> None:
        """Apunta dónde está Robuso: sus logros de Hong Kong solo cuentan allí (solo lo ves tú)."""
        place = None if donde is None else ROBUSO_PLACES[donde.value]
        await self._robuso_impl(InteractionResponder(interaction), place)

    @commands.command(name="robuso")
    async def robuso_text(self, ctx: commands.Context, *, arg: str = "") -> None:
        """Versión de texto: `.robuso`, `.robuso hongkong` o `.robuso madrid`."""
        word = arg.strip().lower().replace(" ", "")
        if word and word not in ROBUSO_PLACES:
            await ctx.send("Uso: `.robuso` (dónde está), `.robuso hongkong` o `.robuso madrid`.")
            return
        await self._robuso_impl(ContextResponder(ctx), ROBUSO_PLACES.get(word))

    async def _robuso_impl(self, responder: CommandResponder, hong_kong: bool | None) -> None:
        """Cambia o enseña dónde está Robuso y responde cómo queda."""
        guild = responder.guild
        if guild is None:
            await responder.send_error("Esto solo funciona en un servidor.")
            return
        # Lee y escribe sus estadísticas de logros: se acepta el comando antes.
        await responder.start_progress(ephemeral=True)
        changed: bool | None = None
        try:
            if hong_kong is None:
                hong_kong = await robuso_logros.in_hong_kong(self.bot, guild.id)
                ready = hong_kong is not None
            else:
                changed = await robuso_logros.set_place(
                    self.bot, guild.id, hong_kong=hong_kong, channel=responder.channel
                )
                ready = changed is not None
        except (LookupError, OSError, sqlite3.Error):
            logger.exception("No se pudo apuntar dónde está Robuso en %s", guild.id)
            await responder.finish("No se ha podido guardar ahora mismo. Prueba en un rato.")
            return
        if not ready:
            await responder.finish("Los logros de Robuso no están disponibles ahora mismo.")
            return
        where = "Hong Kong" if hong_kong else "Madrid"
        if changed is None:
            text = f"Robuso está apuntado en **{where}**."
        elif changed:
            text = f"Hecho: Robuso está ahora en **{where}**."
        else:
            text = f"Robuso ya estaba apuntado en **{where}**."
        await responder.finish(text)

    # --- bloquear ---------------------------------------------------------

    @app_commands.command(
        name="bloquear", description="Bloquea un comando con un fallo, o lo vuelve a abrir."
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        comando="Nombre del comando (sin / ni punto); sin nombre, enseña los bloqueados."
    )
    async def bloquear(self, interaction: discord.Interaction, comando: str = "") -> None:
        """Bloquea un comando para todos o lo desbloquea si ya lo estaba (solo lo ves tú).

        Solo para los IDs de `DEPLOYERS`; que Discord oculte el `/` a quien no es
        administrador es solo estética.
        """
        await self._bloquear_impl(InteractionResponder(interaction), comando)

    @commands.command(name="bloquear", hidden=True)
    async def bloquear_text(self, ctx: commands.Context, comando: str = "") -> None:
        """Versión de texto: `.bloquear` (los bloqueados) o `.bloquear minas`."""
        await self._bloquear_impl(ContextResponder(ctx), comando)

    async def _bloquear_impl(self, responder: CommandResponder, text: str) -> None:
        """Cambia el bloqueo de un comando (o enseña los bloqueados) y responde cómo queda."""
        guild = responder.guild
        if guild is None:
            await responder.send_error("Esto solo funciona en un servidor.")
            return
        if responder.member is None or responder.member.id not in DEPLOYERS:
            await responder.send_error("Solo Yeyo y Dani pueden bloquear comandos.")
            return
        blocked = self.blocked.setdefault(guild.id, set())
        word = text.strip().lower().lstrip("/.")
        if not word:
            await responder.send(
                "Comandos bloqueados: " + ", ".join(f"`{n}`" for n in sorted(blocked))
                if blocked
                else "No hay ningún comando bloqueado.",
                ephemeral=True,
            )
            return
        command = self.bot.get_command(word) or self.bot.tree.get_command(word)
        if command is None:
            await responder.send_error(f"No hay ningún comando `{word}`.")
            return
        name = command.name  # `.bj` es `blackjack`
        if name in UNBLOCKABLE:
            await responder.send_error(f"`{name}` no se puede bloquear.")
            return
        block = name not in blocked
        await responder.start_progress(ephemeral=True)
        try:
            await self.bot.moderation.set_blocked(guild.id, name, block)  # type: ignore[attr-defined]
        except sqlite3.Error:
            logger.exception("No se pudo guardar el bloqueo de %s en %s", name, guild.id)
            await responder.finish("No se ha podido guardar ahora mismo. Prueba en un rato.")
            return
        if not block:
            blocked.discard(name)
            await responder.finish(f"🔓 `{name}` vuelve a funcionar.")
            return
        blocked.add(name)
        # Los botones se paran por archivo: los de otros comandos del mismo también.
        siblings = sorted(
            c.name
            for c in self.bot.commands
            if c.module == command.module and c.name != name and not c.hidden
        )
        also = (
            "\nOjo: los botones se paran por archivo, así que tampoco responden los de "
            + ", ".join(f"`{n}`" for n in siblings)
            + " (sus comandos siguen funcionando)."
            if siblings
            else ""
        )
        await responder.finish(
            f"🔒 `{name}` bloqueado: nadie puede usarlo, ni con `/` ni con `.`, ni pulsar los "
            f"botones de lo que ya estaba abierto. Se desbloquea con `bloquear {name}`.{also}"
        )

    # --- crear ------------------------------------------------------------

    @app_commands.command(
        name="crear", description="Mete un objeto de la tienda en la mochila de un miembro."
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        miembro="A quién se le da.", objeto="El objeto (nombre o parte del nombre)."
    )
    async def crear(
        self, interaction: discord.Interaction, miembro: discord.Member, objeto: str
    ) -> None:
        """Da un objeto sin cobrarlo ni gastar existencias (solo lo ves tú).

        Solo para los IDs de `DEPLOYERS`; que Discord oculte el `/` a quien no es
        administrador es solo estética.
        """
        await self._crear_impl(InteractionResponder(interaction), miembro, objeto)

    @crear.autocomplete("objeto")
    async def _crear_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild is None or interaction.user.id not in DEPLOYERS:
            return []
        needle = current.casefold()
        return [
            app_commands.Choice(name=f"{item.emoji} {item.name}"[:100], value=str(item.id))
            for item in await self._objects(interaction.guild.id)
            if needle in item.name.casefold()
        ][:25]

    @commands.command(name="crear", hidden=True)
    async def crear_text(
        self, ctx: commands.Context, miembro: discord.Member, *, objeto: str
    ) -> None:
        """Versión de texto: `.crear @miembro Patata con forma de España`."""
        await self._crear_impl(ContextResponder(ctx), miembro, objeto)

    async def _objects(self, guild_id: int) -> list[ShopItem]:
        """Los objetos del catálogo del servidor, ocultos y retirados de la venta incluidos."""
        items = await self.bot.shop.items(guild_id, include_hidden=True)  # type: ignore[attr-defined]
        return [item for item in items if item.kind is Kind.TROPHY]

    async def _crear_impl(
        self, responder: CommandResponder, member: discord.Member, text: str
    ) -> None:
        """Busca el objeto, lo mete en la mochila de `member` y responde cómo queda."""
        guild = responder.guild
        if guild is None:
            await responder.send_error("Esto solo funciona en un servidor.")
            return
        if responder.member is None or responder.member.id not in DEPLOYERS:
            await responder.send_error("Solo Yeyo y Dani pueden crear objetos.")
            return
        if member.bot:
            await responder.send_error("Los bots no tienen mochila.")
            return
        await responder.start_progress(ephemeral=True)
        try:
            item = pick_object(await self._objects(guild.id), text)
            if isinstance(item, str):
                await responder.finish(item)
                return
            _entry, dupe = await self.bot.shop.grant(  # type: ignore[attr-defined]
                guild.id, member.id, item, time.time()
            )
        except sqlite3.Error:
            logger.exception("No se pudo crear un objeto en %s", guild.id)
            await responder.finish("No se ha podido guardar ahora mismo. Prueba en un rato.")
            return
        logger.info(
            "%s crea el objeto %s para %s en %s", responder.member.id, item.id, member.id, guild.id
        )
        also = " Ya tenía otro igual." if dupe else ""
        await responder.finish(
            f"🎁 {item.emoji} **{item.name}** ya está en la mochila de {member.mention}.{also}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # --- dinero -----------------------------------------------------------

    @app_commands.command(name="dinero", description="Da o quita yapdollars a un miembro o a ti.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        cantidad="Cuántos Y$.", miembro="A quién (si no, a ti).", quitar="Quitar en vez de dar."
    )
    async def dinero(
        self,
        interaction: discord.Interaction,
        cantidad: app_commands.Range[int, 1, MAX_DINERO],
        miembro: discord.Member | None = None,
        quitar: bool = False,
    ) -> None:
        """Da dinero sin quitárselo a nadie, o lo quita (solo lo ves tú).

        Solo para los IDs de `DEPLOYERS`; que Discord oculte el `/` a quien no es
        administrador es solo estética.
        """
        await self._dinero_impl(InteractionResponder(interaction), miembro, cantidad, quitar)

    @commands.command(name="dinero", hidden=True)
    async def dinero_text(
        self,
        ctx: commands.Context,
        quitar: Literal["quitar"] | None,
        cantidad: int,
        miembro: discord.Member | None = None,
    ) -> None:
        """Versión de texto: `.dinero [quitar] 5000` (a ti) o `.dinero [quitar] 5000 @miembro`."""
        await self._dinero_impl(ContextResponder(ctx), miembro, cantidad, quitar is not None)

    async def _dinero_impl(
        self,
        responder: CommandResponder,
        member: discord.Member | None,
        amount: int,
        take: bool = False,
    ) -> None:
        """Mete (o con `take`, saca) `amount` en la cuenta de `member` o de quien lo pide."""
        guild = responder.guild
        if guild is None:
            await responder.send_error("Esto solo funciona en un servidor.")
            return
        if responder.member is None or responder.member.id not in DEPLOYERS:
            await responder.send_error("Solo Yeyo y Dani pueden dar dinero.")
            return
        member = member or responder.member
        if member.bot:
            await responder.send_error("Los bots no tienen cuenta.")
            return
        if not 1 <= amount <= MAX_DINERO:
            await responder.send_error(
                f"La cantidad va de 1 a {format_amount(MAX_DINERO)} por vez."
            )
            return
        await responder.start_progress(ephemeral=True)
        try:
            economy = self.bot.economy  # type: ignore[attr-defined]
            if take:
                amount, balance = await economy.revoke(
                    guild.id, member.id, amount=amount, reason="admin:dinero"
                )
            else:
                balance = await economy.grant(
                    guild.id, member.id, amount=amount, reason="admin:dinero"
                )
        except sqlite3.Error:
            logger.exception("No se pudo dar dinero en %s", guild.id)
            await responder.finish("No se ha podido guardar ahora mismo. Prueba en un rato.")
            return
        verb, word = ("quita", "menos") if take else ("da", "para")
        logger.info(
            "%s %s %s Y$ a %s en %s", responder.member.id, verb, amount, member.id, guild.id
        )
        await responder.finish(
            f"💸 {format_amount(amount)} {word} {member.mention}. Su saldo: "
            f"{format_amount(balance)}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # --- enchufe ----------------------------------------------------------

    @commands.command(name="enchufe", hidden=True, extras={"secret": True})
    async def enchufe(self, ctx: commands.Context, *, texto: str = "") -> None:
        """`.enchufe @miembro [puestos] [rama]`: sube de puesto en `pala` sin pedir nada.

        Solo para Yeyo (`SECRET_OWNER`). El texto llega entero y se lee aquí: un
        argumento que falta o no se entiende no da error a la vista de nadie.
        """
        if not await self._secret_start(ctx):
            return
        words = texto.split()
        member = None
        if words:
            with contextlib.suppress(commands.BadArgument):
                member = await commands.MemberConverter().convert(ctx, words[0])
        if member is None:
            await self._whisper(ctx, ENCHUFE_USAGE)
            return
        if (guild := await self._secret_guild(ctx, member)) is None:
            return
        rest = words[1:]
        steps = max(1, int(rest.pop(0))) if rest and rest[0].isdigit() else 1
        try:
            done, status = await self.bot.work.force_promotion(  # type: ignore[attr-defined]
                guild.id, member.id, steps, " ".join(rest)
            )
        except WorkError as error:
            await self._whisper(ctx, f"{member.display_name}: {error}")
            return
        except sqlite3.Error:
            logger.exception("No se pudo guardar el ascenso de %s en %s", member.id, guild.id)
            await self._whisper(ctx, "No se ha podido guardar ahora mismo. Prueba en un rato.")
            return
        lines = []
        if done:
            logger.info("%s sube %s puestos a %s en %s", ctx.author.id, done, member.id, guild.id)
            plural = "s" if done > 1 else ""
            lines.append(
                f"📈 {member.display_name} sube {done} puesto{plural}: "
                f"ahora es **{status.position.title}**."
            )
        if status.fork:
            names = ", ".join(f"{b.emoji} {b.name}" for b in status.fork)
            lines.append(f"Para seguir toca elegir rama ({names}): `.enchufe @miembro 1 <rama>`.")
        elif done < steps:
            lines.append(f"{member.display_name} ya está en lo más alto ({status.position.title}).")
        await self._whisper(ctx, "\n".join(lines))

    # --- amanece ----------------------------------------------------------

    @commands.command(name="amanece", hidden=True, extras={"secret": True})
    async def amanece(self, ctx: commands.Context, *, texto: str = "") -> None:
        """`.amanece [@miembro]`: empieza su día de `pala` de nuevo (sin miembro, el tuyo).

        Franjas de hoy del curro y del negocio a cero, horas extra de la semana libres y
        batería llena (`WorkService.reset_day`). Solo para Yeyo, como `enchufe`.
        """
        if not await self._secret_start(ctx):
            return
        member = ctx.author
        if words := texto.split():
            member = None
            with contextlib.suppress(commands.BadArgument):
                member = await commands.MemberConverter().convert(ctx, words[0])
            if member is None:
                await self._whisper(ctx, AMANECE_USAGE)
                return
        if (guild := await self._secret_guild(ctx, member)) is None:
            return
        try:
            hired = await self.bot.work.reset_day(guild.id, member.id)  # type: ignore[attr-defined]
        except sqlite3.Error:
            logger.exception("No se pudo reiniciar el día de %s en %s", member.id, guild.id)
            await self._whisper(ctx, "No se ha podido guardar ahora mismo. Prueba en un rato.")
            return
        logger.info("%s reinicia el día de %s en %s", ctx.author.id, member.id, guild.id)
        await self._whisper(
            ctx,
            f"🌅 {member.display_name}: franjas de hoy a cero, horas extra de la semana libres "
            "y batería al 100 %."
            if hired
            else f"🌅 {member.display_name} no tiene curro: batería al 100 % y turnos de hoy "
            "del negocio a cero.",
        )

    # --- secretos -----------------------------------------------------------

    async def _secret_start(self, ctx: commands.Context) -> bool:
        """Si lo escribe Yeyo, y borra su mensaje salvo en un privado; si no, nada."""
        if ctx.author.id != SECRET_OWNER:
            # Solo en el log del NAS: dice qué ID ve el bot si a Yeyo no le responde.
            logger.info("%s ignorado: lo escribe %s, no Yeyo", ctx.command, ctx.author.id)
            return False
        if ctx.guild is not None:
            with contextlib.suppress(discord.HTTPException):
                await ctx.message.delete()
        return True

    async def _secret_guild(
        self, ctx: commands.Context, member: discord.abc.User
    ) -> discord.Guild | None:
        """Servidor del secreto: el del mensaje o, por privado, el único con `member`.

        Si por privado no hay uno solo (el bot y el miembro comparten varios o ninguno),
        se lo dice a Yeyo y devuelve `None`.
        """
        if ctx.guild is not None:
            return ctx.guild
        shared = [g for g in self.bot.guilds if g.get_member(member.id) is not None]
        if len(shared) == 1:
            return shared[0]
        await self._whisper(ctx, SECRET_GUILD_ERROR.format(name=member.display_name))
        return None

    async def _whisper(self, ctx: commands.Context, text: str) -> None:
        """Contesta a un secreto por privado; si no admite privados, solo queda en el log."""
        try:
            await ctx.author.send(text, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            logger.warning("No se pudo mandar por privado la respuesta de %s", ctx.command)

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

    # --- informe ----------------------------------------------------------

    @app_commands.command(name="informe", description="Descarga el informe interno de uso del bot.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        dias=f"Días hacia atrás (1–{MAX_REPORT_DAYS}; por defecto {DEFAULT_REPORT_DAYS}).",
        canal="Canal privado donde publicar el informe cada día (en vez de descargarlo).",
        quitar="Deja de publicar el informe cada día.",
    )
    async def informe(
        self,
        interaction: discord.Interaction,
        dias: app_commands.Range[int, 1, MAX_REPORT_DAYS] = DEFAULT_REPORT_DAYS,
        canal: discord.TextChannel | app_commands.AppCommandThread | None = None,
        quitar: bool = False,
    ) -> None:
        """Manda el informe de uso en un JSON que solo ves tú, o elige dónde publicarlo."""
        channel_id = canal.id if canal is not None else None
        await self._informe_impl(InteractionResponder(interaction), dias, channel_id, quitar)

    @commands.command(name="informe")
    async def informe_text(self, ctx: commands.Context, *args: str) -> None:
        """Versión de texto: acepta en cualquier orden unos días y un #canal o `quitar`."""
        days = DEFAULT_REPORT_DAYS
        channel_id: int | None = None
        remove = False
        for arg in args:
            if arg.isdigit() and 1 <= int(arg) <= MAX_REPORT_DAYS:
                days = int(arg)
            elif arg.lower() == "quitar":
                remove = True
            elif (converted := await _channel_or_thread_id(ctx, arg)) is not None:
                channel_id = converted
            else:
                await ctx.send(INFORME_USAGE)
                return
        await self._informe_impl(ContextResponder(ctx), days, channel_id, remove)

    async def _informe_impl(
        self,
        responder: CommandResponder,
        days: int,
        channel_id: int | None = None,
        remove: bool = False,
    ) -> None:
        """Adjunta el informe de los últimos `days` días, o cambia su canal diario.

        El informe (ver `bot.services.telemetry.build_report`) lleva cantidades y
        seudónimos (u1, u2…), nunca mensajes ni IDs de miembros. Sirve para que un
        agente decida mejoras: se guarda en `docs/metricas/` del fork y se analiza
        con `docs/analisis_metricas.py`. Con un canal, el cog `Telemetry` lo publica
        ahí cada día (`publish_daily`); con `remove`, deja de hacerlo.
        """
        guild = responder.guild
        telemetry = getattr(self.bot, "telemetry", None)
        if guild is None or not isinstance(telemetry, TelemetryService):
            await responder.send_error("La telemetría no está disponible ahora mismo.")
            return
        if channel_id is not None or remove:
            await self._report_channel_impl(responder, guild, telemetry, channel_id, remove)
            return
        await responder.start_progress("📊 Preparando el informe…", ephemeral=True)
        names, channel_names = report_inputs(self.bot, guild)
        report = await telemetry.report(
            guild_id=guild.id, days=days, commands=names, channel_names=channel_names
        )
        data = json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode()
        daily = await telemetry.report_channel(guild.id)
        await responder.finish(
            f"📊 Informe de {days} días ({math.ceil(len(data) / 1024)} KB). Súbelo a "
            "`docs/metricas/` del fork o pásaselo a Claude.\n"
            + (
                f"Cada día se publica en <#{daily}>."
                if daily is not None
                else "No se publica cada día; elige un canal privado con `/informe canal:`."
            ),
            file=discord.File(io.BytesIO(data), filename=f"informe-{report['hasta']}.json"),
        )

    async def _report_channel_impl(
        self,
        responder: CommandResponder,
        guild: discord.Guild,
        telemetry: TelemetryService,
        channel_id: int | None,
        remove: bool,
    ) -> None:
        """Guarda o quita el canal del informe diario y, si hay uno, publica ya el de hoy."""
        if remove and channel_id is not None:
            await responder.send_error("Elige un canal o «quitar», no las dos cosas.")
            return
        target = None if channel_id is None else await resolve_target(guild, channel_id)
        if channel_id is not None and (target is None or not can_post(target, guild.me)):
            await responder.send_error(
                "No encuentro ese canal o hilo, o no puedo escribir en él. Si es privado, "
                "dame permiso para verlo y escribir."
            )
            return
        if not await telemetry.set_report_channel(guild.id, channel_id):
            await responder.send_error("La telemetría no está disponible ahora mismo.")
            return
        if target is None:
            await responder.send("🗑️ El informe ya no se publica cada día.", ephemeral=True)
            return
        await responder.start_progress("📊 Publicando el informe de hoy…", ephemeral=True)
        cog = find_cog(self.bot, Telemetry)
        published = cog is not None and await cog.publish_daily(guild)
        await responder.finish(
            f"✅ El informe se publicará cada día en {target.mention}. "
            + ("Ya está el de hoy." if published else "⚠️ El de hoy no se pudo publicar.")
            + " Que sea privado: lleva nombres de canales y cifras de uso."
        )

    # --- repicar ----------------------------------------------------------

    @commands.command(name="repicar")
    async def replay_dig_text(self, ctx: commands.Context, tirada: int) -> None:
        """Vuelve a jugar una tirada del pico para auditarla.

        Reproduce una tirada de `picar` con su semilla, sin mover dinero. Solo con `.`:
        no gasta uno de los slash commands, que son limitados.
        """
        await self._replay_dig_impl(ContextResponder(ctx), tirada)

    async def _replay_dig_impl(self, responder: CommandResponder, play_id: int) -> None:
        from bot.cogs.picar import Picar

        cog = find_cog(self.bot, Picar)
        if cog is None:
            await responder.send_error("El pozo (`picar`) no está cargado.")
            return
        await cog.replay(responder, play_id)


async def setup(bot: commands.Bot) -> None:
    """Registra el cog de administración en el cliente."""
    await bot.add_cog(Admin(bot))
