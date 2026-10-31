"""Aspecto de Jovani: perfil que cambia con el servidor y ficha pública `jovani`.

Cada 10 minutos el bot mira qué look toca en cada servidor (reglas en
`bot.services.aspecto`), cambia su perfil de ese servidor si hace falta y turna su
estado. El perfil por servidor (avatar, banner y bio) no toca el perfil global del
bot; si un look no tiene avatar, se usa el del look 🎤 De diario y, si tampoco lo
tiene, el global. Un avatar puede ser un GIF animado; el banner no, porque lleva texto
dibujado encima.

- **Estado:** Discord solo admite uno por bot, no uno por servidor, así que sale de
  los datos del servidor con más miembros.
- **Cambios de perfil:** van uno a uno (avatar, banner y bio) para que, si Discord
  rechaza uno, los demás se apliquen y se vea cuál ha fallado en `aspecto`. Tras un
  429 no se vuelve a tocar el perfil hasta que pase la espera (`blocked_until`).
- **Datos:** `Snapshot` junta cifras de la telemetría, el casino, la cuenta del
  Estado, los logros y `picar`. Solo son totales del servidor. Los contadores de la
  telemetría se borran a los 400 días, así que cada día se acumula lo cerrado en
  `aspecto_totals` para que los totales «desde siempre» no bajen.

`aspecto` es un comando de administración y vive en el cog `Admin`, que comprueba el
permiso en cada uso; aquí está su lógica (`manage`). `jovani` es público y tiene sus
logros (🎤 Jovani): cuentan días distintos y looks vistos, y un look fijado a mano
no cuenta para que nadie los consiga a golpe de `aspecto`.

Requisitos: ningún permiso especial en el servidor. Efectos persistentes: el perfil
del bot en el servidor, las tablas de `bot.repositories.aspecto` y las imágenes de
`.data/aspecto/`, que se borran si el bot sale del servidor.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
import time
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot import __version__
from bot.cogs import achievements as logros
from bot.cogs import pets as mascotas
from bot.cogs.beernight import Beernight
from bot.repositories.aspecto import AspectoRepository, LookImageStore
from bot.services import aspecto as rules
from bot.services.achievements import jovani_stats
from bot.services.aspecto import AspectoError, AspectoState, Look, LookOverride, Snapshot
from bot.services.aspecto_render import draw_banner, prepare_avatar, prepare_banner
from bot.services.economy import format_amount
from bot.services.image_input import MAX_INPUT_BYTES, ImageTooLargeError, InvalidImageError
from bot.services.levels import TIMEZONE
from bot.services.pets import Event, Moment
from bot.services.telemetry import TimedLock
from bot.utils.cogs import find_cog
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

#: Cuánto vale una foto de los datos: cada una son varias consultas a la base.
SNAPSHOT_SECONDS = 600
#: Cuánto se espera a Discord en cada cambio de perfil antes de dejarlo para luego.
EDIT_TIMEOUT_SECONDS = 120
#: Motivo que queda en el registro de auditoría del servidor.
EDIT_REASON = "Cambio de look (aspecto)"
#: Nombre de cada pieza del perfil en los avisos de `aspecto`.
FIELD_NAMES = {"avatar": "Avatar", "banner": "Banner", "bio": "Bio"}
#: Las dos imágenes de un look, con su función de preparación.
IMAGE_PREPARERS = {"avatar": prepare_avatar, "banner": prepare_banner}

#: Acciones de `aspecto`, con la etiqueta que ve el administrador en `/aspecto`.
ACTIONS: dict[str, str] = {
    "fijar": "Fijar un look",
    "auto": "Volver a lo automático",
    "crear": "Crear un look",
    "borrar": "Borrar un look propio",
    "avatar": "Cambiar el avatar de un look",
    "banner": "Cambiar el fondo del banner de un look",
    "bio": "Cambiar la bio de un look",
    "estado": "Cambiar el estado de un look",
    "apagar": "Apagar y volver al perfil global",
    "encender": "Encender",
}


class _Blocked(Exception):
    """Discord pide esperar antes de tocar otra vez el perfil."""

    def __init__(self, seconds: float, reason: str) -> None:
        super().__init__(reason)
        self.seconds = seconds


def _retry_after(error: discord.HTTPException) -> float:
    """Segundos que pide esperar Discord; si no lo dice, `BLOCK_SECONDS`."""
    try:
        return float(error.response.headers["Retry-After"])
    except (AttributeError, KeyError, TypeError, ValueError):
        return float(rules.BLOCK_SECONDS)


def _lines(pairs: list[tuple[str, int]]) -> str:
    """Líneas de una ficha, sin las cifras a cero."""
    return "\n".join(f"{label}: **{value:,}**".replace(",", ".") for label, value in pairs if value)


def card_embed(
    guild: discord.Guild, look: Look, bio: str, snapshot: Snapshot, *, started_at: float
) -> discord.Embed:
    """La ficha de `jovani`.

    Args:
        guild: Servidor, para el avatar y el banner que se ven en él.
        look: Look puesto.
        bio: Texto de cabecera.
        snapshot: Datos del momento.
        started_at: Cuándo arrancó el bot (epoch).
    """
    embed = discord.Embed(
        title=f"🎤 Jovani Vázquez · v{__version__}",
        description=bio,
        color=discord.Color.from_rgb(*look.top),
    )
    embed.add_field(name="Look", value=look.name)
    embed.add_field(name="Encendido", value=f"<t:{int(started_at)}:R>")
    week, lifetime = snapshot.week, snapshot.lifetime
    if text := _lines(
        [
            ("Comandos", week.commands),
            ("Mal escritos", week.mistyped),
            ("Dibujos", week.drawings),
            ("Canciones", week.songs),
            ("Mensajes", week.messages),
        ]
    ):
        star = f"\nComando estrella: **{week.top_command}**" if week.top_command else ""
        embed.add_field(name="📅 Esta semana", value=text + star, inline=False)
    if text := _lines(
        [
            ("Comandos", lifetime.commands),
            ("Dibujos", lifetime.drawings),
            ("Canciones", lifetime.songs),
            ("Reinicios", snapshot.restarts),
            ("Logros desbloqueados", snapshot.achievements),
            ("Dragones matados", snapshot.dragons),
        ]
    ):
        embed.add_field(name="🕰️ Desde siempre", value=text, inline=False)
    money = []
    if snapshot.house_net:
        verb = "gana" if snapshot.house_net > 0 else "pierde"
        money.append(f"La casa {verb} **{format_amount(abs(snapshot.house_net))}** esta semana")
    if snapshot.state_balance:
        money.append(f"El Estado guarda **{format_amount(snapshot.state_balance)}**")
    if money:
        embed.add_field(name="💰 Dinero", value="\n".join(money), inline=False)
    me = guild.me
    if me is not None:
        embed.set_thumbnail(url=me.display_avatar.url)
        if me.display_banner is not None:
            embed.set_image(url=me.display_banner.url)
    return embed


class Aspecto(commands.Cog):
    """Cambia el perfil de Jovani según el servidor y enseña su ficha.

    Args:
        bot: Cliente, con la telemetría y los repositorios de donde salen los datos.
        repository: Estado, looks y totales de cada servidor.
        images: Imágenes de los looks.
    """

    def __init__(
        self, bot: BotClient, repository: AspectoRepository, images: LookImageStore
    ) -> None:
        self.bot = bot
        self.repository = repository
        self.images = images
        self.started_at = time.time()
        # Un solo cambio de perfil a la vez: la tarea y `aspecto` leen y guardan el estado.
        self._lock = TimedLock("aspecto:Aspecto._lock")
        self._snapshots: dict[int, tuple[float, Snapshot]] = {}
        self._overrides: dict[int, dict[str, LookOverride]] = {}
        # Dragones recién matados. Van en memoria para no hacer esperar a `picar` por el
        # candado; se pasan al estado en la siguiente vuelta (10 minutos como mucho).
        self._dragons: dict[int, float] = {}
        self._turn = 0

    async def cog_load(self) -> None:
        self._tick.start()

    async def cog_unload(self) -> None:
        self._tick.cancel()

    @tasks.loop(minutes=10)
    async def _tick(self) -> None:
        """Pone el look que toca en cada servidor y turna el estado. Nunca se para."""
        for guild in self.bot.guilds:
            try:
                await self.sync(guild)
            except Exception:
                # Si saliera la excepción, `tasks.loop` no volvería a dar ninguna vuelta.
                logger.exception("No se pudo revisar el aspecto del servidor %s", guild.id)
        try:
            await self._rotate_status()
        except Exception:
            logger.exception("No se pudo cambiar el estado del bot")

    @_tick.before_loop
    async def _before_tick(self) -> None:
        await self.bot.wait_until_ready()

    # --- Looks de cada servidor --------------------------------------------

    async def _overrides_of(self, guild_id: int) -> dict[str, LookOverride]:
        """Lo cambiado por los administradores; se guarda en memoria para el autocompletado."""
        if guild_id not in self._overrides:
            self._overrides[guild_id] = await self.repository.overrides(guild_id)
        return self._overrides[guild_id]

    @staticmethod
    def _custom_keys(overrides: dict[str, LookOverride]) -> set[str]:
        return {key for key, override in overrides.items() if override.name}

    def _known(self, overrides: dict[str, LookOverride]) -> set[str]:
        return set(rules.BUILTIN_BY_KEY) | self._custom_keys(overrides)

    @staticmethod
    def _look(key: str, overrides: dict[str, LookOverride]) -> Look:
        if (look := rules.BUILTIN_BY_KEY.get(key)) is not None:
            return look
        override = overrides.get(key)
        return rules.custom_look(override) if override is not None else rules.NORMAL

    def _desired(
        self, guild: discord.Guild, state: AspectoState, overrides: dict[str, LookOverride]
    ) -> str:
        """Clave del look que toca ahora en `guild`."""
        cog = find_cog(self.bot, Beernight)
        created = guild.created_at.astimezone(TIMEZONE)
        return rules.desired_key(
            state,
            datetime.now(TIMEZONE),
            known=self._known(overrides),
            beernight=cog is not None and guild.id in cog.nights,
            anniversary=(created.month, created.day),
        )

    async def look_options(self, guild_id: int, current: str) -> list[tuple[str, str]]:
        """Looks que encajan con lo escrito: pares `(nombre, clave)`, para autocompletar."""
        overrides = await self._overrides_of(guild_id)
        wanted = current.lower()
        options = [
            (look.name, look.key)
            for look in (self._look(key, overrides) for key in self._ordered(overrides))
            if wanted in look.name.lower() or wanted in look.key
        ]
        return options[:25]

    def _ordered(self, overrides: dict[str, LookOverride]) -> list[str]:
        """Claves de los looks, primero los de serie y luego los propios."""
        return [look.key for look in rules.BUILTIN_LOOKS] + sorted(self._custom_keys(overrides))

    def mark_dragon(self, guild_id: int) -> None:
        """Apunta que se ha matado al dragón de `picar`; el look sale en la siguiente vuelta."""
        self._dragons[guild_id] = time.time() + rules.DRAGON_SECONDS

    async def _state(self, guild_id: int) -> AspectoState:
        """Estado guardado, con los dragones que aún no se habían apuntado."""
        state = await self.repository.state(guild_id)
        if (until := self._dragons.pop(guild_id, None)) is not None:
            state = replace(state, dragon_until=max(state.dragon_until, until))
        return state

    # --- Aplicar el look ---------------------------------------------------

    async def sync(self, guild: discord.Guild, *, force: bool = False) -> AspectoState:
        """Deja el perfil de `guild` con el look que toca.

        Args:
            force: Lo ha pedido un administrador: no espera entre cambios.

        Returns:
            El estado ya guardado.
        """
        async with self._lock:
            now = datetime.now(TIMEZONE)
            moment = now.timestamp()
            stored = await self._state(guild.id)
            state = rules.seen_version(stored, moment, __version__)
            if state.pinned_key is not None and not rules.pin_active(state, moment):
                state = replace(state, pinned_key=None, pinned_until=None)
            overrides = await self._overrides_of(guild.id)
            plan = rules.plan_profile(
                state, self._desired(guild, state, overrides), now, force=force
            )
            if not plan.empty:
                state = await self._apply(guild, state, plan, overrides, now)
            await self.repository.save_state(guild.id, state)
            return state

    async def _avatar_for(self, guild_id: int, key: str) -> bytes | None:
        """Avatar de un look; si no tiene, el del look de diario; si no, ninguno."""
        data = await self.images.load(guild_id, key, "avatar")
        if data is None and key != rules.NORMAL.key:
            data = await self.images.load(guild_id, rules.NORMAL.key, "avatar")
        return data

    async def _apply(
        self,
        guild: discord.Guild,
        state: AspectoState,
        plan: rules.ProfilePlan,
        overrides: dict[str, LookOverride],
        now: datetime,
    ) -> AspectoState:
        """Sube al perfil lo que diga `plan` y devuelve el estado resultante."""
        moment = now.timestamp()
        look = self._look(plan.look_key, overrides)
        seed = now.date().toordinal()
        snapshot = await self.snapshot(guild)
        problems: list[str] = []
        avatar_mark = state.applied_avatar
        try:
            if plan.avatar:
                avatar = await self._avatar_for(guild.id, look.key)
                mark = hashlib.sha256(avatar).hexdigest() if avatar is not None else None
                if mark != avatar_mark and await self._edit(guild, problems, avatar=avatar):
                    avatar_mark = mark
            if plan.banner:
                base = await self.images.load(guild.id, look.key, "banner")
                lines = rules.banner_lines(snapshot, seed=seed)
                banner = await asyncio.to_thread(
                    draw_banner, look, base, look.name, lines, __version__
                )
                await self._edit(guild, problems, banner=banner)
            if plan.bio:
                bio = rules.bio_text(look, overrides.get(look.key), snapshot, seed=seed)
                await self._edit(guild, problems, bio=bio)
        except _Blocked as blocked:
            logger.warning("Perfil en espera en %s: %s", guild.id, blocked)
            return replace(
                state,
                applied_avatar=avatar_mark,
                blocked_until=moment + blocked.seconds,
                last_error=str(blocked),
            )
        return replace(
            state,
            applied_key=look.key,
            applied_avatar=avatar_mark,
            profile_at=moment,
            refreshed_day=now.date().isoformat(),
            last_error=" ".join(problems) or None,
        )

    async def _edit(
        self, guild: discord.Guild, problems: list[str], **fields: bytes | str | None
    ) -> bool:
        """Cambia una pieza del perfil.

        Args:
            problems: Donde apuntar el aviso si Discord rechaza el cambio.
            fields: La pieza, con su valor (`None` la quita).

        Returns:
            Si se ha cambiado.

        Raises:
            _Blocked: Si Discord pide esperar o no contesta: no hay que seguir.
        """
        name = FIELD_NAMES[next(iter(fields))]
        try:
            await asyncio.wait_for(
                guild.me.edit(reason=EDIT_REASON, **fields),  # type: ignore[arg-type]
                EDIT_TIMEOUT_SECONDS,
            )
        except TimeoutError as error:
            raise _Blocked(rules.BLOCK_SECONDS, "Discord no ha contestado a tiempo.") from error
        except discord.HTTPException as error:
            if error.status == 429:
                raise _Blocked(_retry_after(error), "Discord pide esperar.") from error
            logger.warning("Discord rechazó el cambio de %s en %s: %s", name, guild.id, error)
            problems.append(f"{name}: Discord lo ha rechazado (error {error.status}).")
            return False
        return True

    async def _rotate_status(self) -> None:
        """Turna el estado del bot con el look y los datos del servidor con más miembros."""
        guilds = self.bot.guilds
        if not guilds:
            return
        guild = max(guilds, key=lambda g: (g.member_count or 0, -g.id))
        state = await self.repository.state(guild.id)
        if not state.enabled:
            await self.bot.change_presence(activity=None)
            return
        overrides = await self._overrides_of(guild.id)
        look = self._look(state.applied_key or rules.NORMAL.key, overrides)
        lines = rules.status_lines(look, overrides.get(look.key), await self.snapshot(guild))
        text = rules.pick(lines, self._turn)
        self._turn += 1
        await self.bot.change_presence(activity=discord.CustomActivity(name=text))

    # --- Datos -------------------------------------------------------------

    async def _lifetime(self, guild_id: int, today: date) -> dict[str, int]:
        """Totales desde siempre: lo acumulado más lo que lleva hoy.

        Acumula antes los días cerrados que falten, para que no se pierdan cuando la
        telemetría los borre.
        """
        telemetry = self.bot.telemetry_repository
        yesterday = (today - timedelta(days=1)).isoformat()
        stored, through = await self.repository.totals(guild_id)
        if through is None or through < yesterday:
            since = None
            if through is not None:
                since = (date.fromisoformat(through) + timedelta(days=1)).isoformat()
            closed = await telemetry.usage_counts(
                guild_id, rules.USAGE_KINDS, since_day=since, until_day=yesterday
            )
            await self.repository.add_totals(guild_id, yesterday, rules.usage_totals(closed))
            stored, _through = await self.repository.totals(guild_id)
        recent = await telemetry.usage_counts(
            guild_id, rules.USAGE_KINDS, since_day=today.isoformat()
        )
        totals = dict(stored)
        for kind, value in rules.usage_totals(recent).items():
            totals[kind] = totals.get(kind, 0) + value
        return totals

    async def snapshot(self, guild: discord.Guild) -> Snapshot:
        """Datos del servidor, de hace `SNAPSHOT_SECONDS` como mucho.

        Si la base falla se devuelve lo último que hubiera: unas cifras viejas no
        deben impedir cambiar de look ni enseñar la ficha.
        """
        cached = self._snapshots.get(guild.id)
        moment = time.monotonic()
        if cached is not None and moment - cached[0] < SNAPSHOT_SECONDS:
            return cached[1]
        today = datetime.now(TIMEZONE).date()
        week_start = (today - timedelta(days=6)).isoformat()
        bot = self.bot
        try:
            week = await bot.telemetry_repository.usage_counts(
                guild.id, rules.USAGE_KINDS, since_day=week_start
            )
            lifetime = await self._lifetime(guild.id, today)
            snapshot = Snapshot(
                week=rules.stats_from(rules.usage_totals(week), top=rules.top_command(week)),
                lifetime=rules.stats_from(lifetime),
                house_net=await bot.casino_stats.house_net(guild.id, week_start),
                state_balance=await bot.economy.state_balance(guild.id),
                achievements=await bot.achievements.unlock_count(guild.id),
                dragons=await bot.picar.dragons(guild.id),
                restarts=lifetime.get("restarts", 0),
            )
        except (OSError, sqlite3.Error):
            logger.exception("No se pudieron leer los datos de Jovani en %s", guild.id)
            return cached[1] if cached is not None else Snapshot()
        self._snapshots[guild.id] = (moment, snapshot)
        return snapshot

    # --- jovani ------------------------------------------------------------

    @app_commands.command(name="jovani", description="Enseña la ficha y las cifras de Jovani.")
    @app_commands.guild_only()
    async def jovani(self, interaction: discord.Interaction) -> None:
        """Publica la ficha de Jovani: versión, look y datos curiosos del servidor."""
        await self._jovani_impl(InteractionResponder(interaction))

    @commands.command(name="jovani")
    @commands.guild_only()
    async def jovani_text(self, ctx: commands.Context) -> None:
        """Versión de texto de `/jovani`."""
        await self._jovani_impl(ContextResponder(ctx))

    async def _jovani_impl(self, responder: CommandResponder) -> None:
        """Enseña la ficha y, después, apunta los logros."""
        guild = responder.guild
        member = responder.member
        if guild is None or member is None:
            await responder.send_error("Este comando solo funciona en un servidor.")
            return
        await responder.start_progress("🎤 Un momento…")
        now = datetime.now(TIMEZONE)
        state = await self.repository.state(guild.id)
        overrides = await self._overrides_of(guild.id)
        key = self._desired(guild, state, overrides) if state.enabled else rules.NORMAL.key
        look = self._look(state.applied_key or key, overrides)
        snapshot = await self.snapshot(guild)
        bio = rules.bio_text(look, overrides.get(look.key), snapshot, seed=now.date().toordinal())
        embed = card_embed(guild, look, bio, snapshot, started_at=self.started_at)
        if pet := await mascotas.cameo(self.bot, guild.id, member.id, Moment(Event.JOVANI)):
            embed.description = f"{bio}\n{pet}"
        await responder.finish(embed=embed)
        automatic = state.enabled and not rules.pin_active(state, now.timestamp())
        counted = look.key if automatic and look.key in rules.BUILTIN_BY_KEY else None
        await logros.track(
            self.bot, guild.id, member, responder.channel, jovani_stats(counted, when=now)
        )

    # --- aspecto (el comando vive en el cog `Admin`) -------------------------

    async def manage(
        self,
        responder: CommandResponder,
        action: str,
        look: str,
        image: discord.Attachment | None,
        text: str,
        hours: int | None,
    ) -> None:
        """Atiende `aspecto`: hace la acción y enseña cómo queda (solo a quien lo pide).

        El permiso lo comprueba el cog `Admin` antes de llegar aquí.

        Args:
            responder: Por dónde contestar.
            action: Una clave de `ACTIONS`; vacía solo enseña el panel.
            look: Clave del look sobre el que actuar.
            image: Imagen de `avatar` o `banner`; sin imagen, se quita la que hubiera.
            text: Nombre en `crear`; bio o estado en `bio` y `estado`, y sin texto
                vuelven los de serie.
            hours: Horas de `fijar`; sin horas, hasta que se quite.
        """
        guild = responder.guild
        if guild is None:
            await responder.send_error("Este comando solo funciona en un servidor.")
            return
        await responder.start_progress("🪞 Cambiando el aspecto…", ephemeral=True)
        none = discord.AllowedMentions.none()
        try:
            message = await self._act(guild, action, look, image, text.strip(), hours)
            embed = await self._panel(guild)
        except AspectoError as error:
            await responder.finish(f"⚠️ {error}", allowed_mentions=none)
            return
        except (OSError, sqlite3.Error):
            logger.exception("No se pudo guardar el aspecto de %s", guild.id)
            await responder.finish("⚠️ No se pudo guardar. Inténtalo otra vez.")
            return
        await responder.finish(message, embed=embed, allowed_mentions=none)

    def _key_of(self, look: str, overrides: dict[str, LookOverride]) -> str:
        """Clave de un look que existe.

        Raises:
            AspectoError: Si no hay ningún look con esa clave.
        """
        key = look.strip().lower()
        if not key:
            raise AspectoError("Falta decir qué look.")
        if key not in self._known(overrides):
            raise AspectoError(f"No hay ningún look `{key[:32]}`.")
        return key

    async def _act(
        self,
        guild: discord.Guild,
        action: str,
        look: str,
        image: discord.Attachment | None,
        text: str,
        hours: int | None,
    ) -> str | None:
        """Hace la acción de `aspecto`. Devuelve el texto que la confirma."""
        overrides = await self._overrides_of(guild.id)
        match action:
            case "":
                return None
            case "fijar":
                return await self._pin(guild, self._key_of(look, overrides), hours)
            case "auto":
                await self._set_state(guild, pinned_key=None, pinned_until=None)
                await self.sync(guild, force=True)
                return "✅ Lo fijado se ha quitado: el look vuelve a ser automático."
            case "crear":
                return await self._create(guild, text)
            case "borrar":
                return await self._delete(guild, self._key_of(look, overrides), overrides)
            case "avatar" | "banner":
                return await self._image(guild, self._key_of(look, overrides), action, image)
            case "bio" | "estado":
                return await self._text(guild, self._key_of(look, overrides), action, text)
            case "apagar":
                return await self._turn_off(guild)
            case "encender":
                await self._set_state(guild, enabled=True)
                await self.sync(guild, force=True)
                await self._rotate_quietly()
                return "✅ Jovani vuelve a cambiar de aspecto."
        raise AspectoError(f"No conozco la acción `{action[:32]}`.")

    async def _set_state(self, guild: discord.Guild, **changes: object) -> None:
        """Cambia campos del estado guardado."""
        async with self._lock:
            state = await self._state(guild.id)
            await self.repository.save_state(guild.id, replace(state, **changes))  # type: ignore[arg-type]

    async def _pin(self, guild: discord.Guild, key: str, hours: int | None) -> str:
        if hours is not None and not 1 <= hours <= rules.MAX_PIN_HOURS:
            raise AspectoError(f"Las horas van de 1 a {rules.MAX_PIN_HOURS}.")
        until = time.time() + hours * 3600 if hours is not None else None
        await self._set_state(guild, pinned_key=key, pinned_until=until)
        await self.sync(guild, force=True)
        await self._rotate_quietly()
        return f"✅ Look fijado: `{key}`."

    async def _create(self, guild: discord.Guild, name: str) -> str:
        clean = " ".join(name.split())
        if not clean:
            raise AspectoError("Falta el nombre del look.")
        if len(clean) > rules.NAME_MAX:
            raise AspectoError(f"El nombre pasa de {rules.NAME_MAX} caracteres.")
        key = rules.slugify(clean)
        if key in rules.BUILTIN_BY_KEY:
            raise AspectoError("Ya hay un look de serie con ese nombre.")
        await self.repository.create_look(guild.id, LookOverride(key, name=clean))
        self._overrides.pop(guild.id, None)
        return f"✅ Look creado: `{key}`. Fíjalo con la acción «{ACTIONS['fijar']}»."

    async def _delete(
        self, guild: discord.Guild, key: str, overrides: dict[str, LookOverride]
    ) -> str:
        if key not in self._custom_keys(overrides):
            raise AspectoError("Los looks de serie no se borran; se pueden cambiar.")
        state = await self.repository.state(guild.id)
        await self.repository.delete_look(guild.id, key)
        await self.images.delete_look(guild.id, key)
        self._overrides.pop(guild.id, None)
        if state.pinned_key == key:
            await self._set_state(guild, pinned_key=None, pinned_until=None)
            await self.sync(guild, force=True)
        return f"✅ Look borrado: `{key}`."

    async def _read_image(self, attachment: discord.Attachment, kind: str) -> bytes:
        """Descarga y prepara un adjunto; todo adjunto es entrada no confiable.

        Raises:
            AspectoError: Si no es una imagen válida o no se puede descargar.
        """
        if not (attachment.content_type or "").startswith("image/"):
            raise AspectoError("El adjunto tiene que ser una imagen.")
        if attachment.size > MAX_INPUT_BYTES:
            raise AspectoError(f"La imagen pasa de {MAX_INPUT_BYTES // (1024 * 1024)} MB.")
        try:
            data = await attachment.read()
            return await asyncio.to_thread(IMAGE_PREPARERS[kind], data)
        except discord.HTTPException as error:
            raise AspectoError("No se pudo descargar la imagen.") from error
        except (ImageTooLargeError, InvalidImageError) as error:
            raise AspectoError(str(error)) from error

    async def _refresh_if_applied(self, guild: discord.Guild, key: str) -> None:
        """Vuelve a subir el perfil si el cambio se ve en el look puesto.

        El avatar del look de diario hace de reserva de los que no tienen, así que
        cambiarlo puede afectar al look puesto aunque sea otro.
        """
        state = await self.repository.state(guild.id)
        if state.applied_key == key or (key == rules.NORMAL.key and state.applied_key):
            await self.sync(guild, force=True)

    async def _image(
        self, guild: discord.Guild, key: str, kind: str, image: discord.Attachment | None
    ) -> str:
        label = "avatar" if kind == "avatar" else "fondo del banner"
        if image is None:
            await self.images.delete(guild.id, key, kind)
            message = f"✅ {label.capitalize()} de `{key}` quitado."
        else:
            data = await self._read_image(image, kind)
            await self.images.save(guild.id, key, kind, data)
            message = f"✅ {label.capitalize()} de `{key}` cambiado."
        await self._refresh_if_applied(guild, key)
        return message

    async def _text(self, guild: discord.Guild, key: str, kind: str, text: str) -> str:
        limit = rules.BIO_MAX if kind == "bio" else rules.STATUS_MAX
        value = rules.check_template(text, limit) if text else None
        overrides = await self._overrides_of(guild.id)
        current = overrides.get(key, LookOverride(key))
        changed = replace(current, bio=value) if kind == "bio" else replace(current, status=value)
        await self.repository.save_override(guild.id, changed)
        self._overrides.pop(guild.id, None)
        await self._refresh_if_applied(guild, key)
        await self._rotate_quietly()
        label = "Bio" if kind == "bio" else "Estado"
        return f"✅ {label} de `{key}`: {'cambiado' if value else 'vuelve el de serie'}."

    async def _turn_off(self, guild: discord.Guild) -> str:
        """Quita el perfil del servidor, para que se vea el global, y deja de cambiarlo."""
        async with self._lock:
            state = await self._state(guild.id)
            problems: list[str] = []
            try:
                await self._edit(guild, problems, avatar=None)
                await self._edit(guild, problems, banner=None)
                await self._edit(guild, problems, bio=None)
            except _Blocked as blocked:
                problems.append(str(blocked))
            await self.repository.save_state(
                guild.id,
                replace(
                    state,
                    enabled=False,
                    applied_key=None,
                    applied_avatar=None,
                    last_error=" ".join(problems) or None,
                ),
            )
        await self._rotate_quietly()
        return "✅ Apagado. Si queda algo del perfil de este servidor, sale en el panel."

    async def _rotate_quietly(self) -> None:
        """Cambia ya el estado; si falla, la tarea lo pondrá en su siguiente vuelta."""
        try:
            await self._rotate_status()
        except (discord.DiscordException, OSError, sqlite3.Error):
            logger.warning("No se pudo cambiar el estado del bot", exc_info=True)

    async def _panel(self, guild: discord.Guild) -> discord.Embed:
        """Panel de `aspecto`: cómo está y qué looks hay. Sin bromas: es de administración."""
        state = await self.repository.state(guild.id)
        overrides = await self._overrides_of(guild.id)
        moment = time.time()
        applied = state.applied_key or "ninguno"
        lines = [
            f"**Estado:** {'encendido' if state.enabled else 'apagado'}",
            f"**Puesto ahora:** `{applied}`",
        ]
        if rules.pin_active(state, moment):
            until = f"hasta <t:{int(state.pinned_until)}:f>" if state.pinned_until else "sin fecha"
            lines.append(f"**Fijado:** `{state.pinned_key}` ({until})")
        else:
            lines.append("**Fijado:** nada, va en automático")
        if moment < state.blocked_until:
            lines.append(f"**En espera hasta:** <t:{int(state.blocked_until)}:t>")
        if state.last_error:
            lines.append(f"**Último aviso:** {state.last_error}")
        lines.append("")
        for key in self._ordered(overrides):
            look = self._look(key, overrides)
            kinds = await self.images.kinds(guild.id, key)
            marks = "".join(
                mark for kind, mark in (("avatar", "🖼️"), ("banner", "🌄")) if kind in kinds
            )
            override = overrides.get(key)
            if override is not None and override.bio:
                marks += "📝"
            if override is not None and override.status:
                marks += "💬"
            lines.append(f"{look.name} · `{key}` {marks}".rstrip())
        lines += ["", "🖼️ avatar · 🌄 fondo del banner · 📝 bio · 💬 estado, propios de ese look"]
        return discord.Embed(
            title="🪞 Aspecto de Jovani",
            description="\n".join(lines),
            color=discord.Color.from_rgb(*rules.NORMAL.top),
        )

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra lo guardado de un servidor cuando el bot sale de él."""
        await self.repository.delete_guild_data(guild.id)
        await self.images.delete_guild(guild.id)
        self._snapshots.pop(guild.id, None)
        self._overrides.pop(guild.id, None)
        self._dragons.pop(guild.id, None)


def dragon_killed(bot: commands.Bot, guild_id: int) -> None:
    """Avisa de que han matado al dragón de `picar`. Nunca lanza."""
    if (cog := find_cog(bot, Aspecto)) is not None:
        cog.mark_dragon(guild_id)


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con el repositorio y las imágenes compartidos del bot."""
    await bot.add_cog(Aspecto(bot, bot.aspecto, bot.aspecto_images))
