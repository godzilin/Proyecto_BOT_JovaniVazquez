"""Reproductor de música por servidor: cola, control y conexión de voz.

Cada servidor tiene su propio `GuildMusicState` con cola, pista actual y
volumen independientes. Un único bucle por servidor (`_player_loop`) saca
pistas de la cola, las reproduce y espera a que acaben; cuando la cola se
queda vacía espera nuevas pistas y, pasado `IDLE_DISCONNECT_SECONDS`, sale
del canal. Parar es cancelar ese bucle: su `finally` limpia la cola y
desconecta, sea cual sea el motivo (`parar`, inactividad, canal vacío o
expulsión). Así no hay banderas compartidas entre el hilo de audio y el
event loop.

La extracción de audio (`yt-dlp`) es bloqueante y va en `asyncio.to_thread`.
El hilo de audio de `discord.py` solo avisa del fin de pista con
`loop.call_soon_threadsafe`.

Cada acción tiene un único nombre corto, idéntico en las dos interfaces:
comando de aplicación (`/poner`) y comando de texto (`.poner`). Ambas
comparten exactamente la misma lógica de negocio a través de `CommandResponder`
(`bot.utils.responder`), una abstracción compartida con el resto de cogs
que oculta si el origen fue una `discord.Interaction` o un mensaje de texto.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.services.achievements import StatDelta, music_queue_stats, music_volume_stats
from bot.services.entrance_sound import wait_until_audible
from bot.services.music import (
    DEFAULT_VOLUME_PERCENT,
    MAX_TRACK_DURATION_SECONDS,
    MAX_VOLUME_PERCENT,
    MIN_VOLUME_PERCENT,
    MusicQueue,
    QueueFullError,
    Track,
    TrackTooLongError,
    TrackUnavailableError,
    build_track_from_info,
    format_duration,
    format_ffmpeg_headers,
    volume_percent_to_factor,
)
from bot.services.music_source import extract_track_info
from bot.services.telemetry import TimedLock, report_event
from bot.utils.responder import CommandResponder, ContextResponder, InteractionResponder

logger = logging.getLogger(__name__)


def _since(start: float) -> float:
    """Milisegundos desde `start` (`perf_counter`), para la telemetría."""
    return (time.perf_counter() - start) * 1000


# Tiempo sin reproducir nada antes de abandonar el canal de voz. Evita que
# el bot ocupe un canal indefinidamente tras vaciarse la cola.
IDLE_DISCONNECT_SECONDS = 5 * 60

# Una pista que acaba antes de esto sin que nadie la salte no llegó a sonar:
# ffmpeg sale enseguida si la URL da 403 o ha caducado, y discord.py no lo
# trata como error.
FAILED_START_SECONDS = 3.0

# Reconectar el flujo de audio ante cortes de red transitorios del origen.
FFMPEG_BEFORE_OPTIONS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
FFMPEG_OPTIONS = "-vn"


class GuildMusicState:
    """Estado de reproducción de un servidor: cola, conexión y volumen."""

    def __init__(self) -> None:
        self.queue = MusicQueue()
        self.voice_client: discord.VoiceClient | None = None
        self.current: Track | None = None
        self.volume_percent: int = DEFAULT_VOLUME_PERCENT
        # Bucle de reproducción (`Music._player_loop`); `None` si no hay.
        self.player: asyncio.Task[None] | None = None
        # Despierta al bucle cuando llega una pista con la cola vacía.
        self.wakeup = asyncio.Event()
        # Dos `poner` a la vez con el bot fuera del canal conectarían dos veces.
        self.connect_lock = TimedLock("music:GuildMusicState.connect_lock")
        # `saltar` lo marca para que el corte no se avise como fallo.
        self.skip_requested = False
        # Canal donde se pidió la última pista, para poder avisar de
        # fallos de reproducción que ocurren fuera de una interacción.
        self.text_channel: discord.abc.Messageable | None = None


class Music(commands.Cog):
    """Comandos de reproducción de música en canales de voz."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._states: dict[int, GuildMusicState] = {}

    def _get_state(self, guild_id: int) -> GuildMusicState:
        """Obtiene el estado del servidor, creándolo la primera vez."""
        state = self._states.get(guild_id)
        if state is None:
            state = GuildMusicState()
            self._states[guild_id] = state
        return state

    async def _require_connected_state(self, responder: CommandResponder) -> GuildMusicState | None:
        """Valida contexto de servidor, conexión de voz y canal del miembro.

        Responde con un mensaje de error y devuelve `None` si algo falla,
        para que el comando que llama pueda simplemente devolver el
        control sin duplicar el manejo de errores.
        """
        guild = responder.guild
        if guild is None:
            await responder.send_error("Este comando solo está disponible dentro de un servidor.")
            return None

        state = self._get_state(guild.id)
        if state.voice_client is None:
            await responder.send_error("El bot no está conectado a ningún canal de voz.")
            return None

        member = responder.member
        if (
            member is None
            or member.voice is None
            or member.voice.channel != state.voice_client.channel
        ):
            await responder.send_error("Debes estar en el mismo canal de voz que el bot.")
            return None

        return state

    @app_commands.command(name="poner", description="Reproduce una canción o la añade a la cola.")
    @app_commands.guild_only()
    @app_commands.describe(consulta="Nombre de la canción o enlace a reproducir.")
    async def play(self, interaction: discord.Interaction, consulta: str) -> None:
        """Une el bot al canal de voz del miembro y encola o reproduce la pista."""
        await self._play_impl(InteractionResponder(interaction), consulta)

    @commands.command(name="poner")
    @commands.guild_only()
    async def play_text(self, ctx: commands.Context, *, consulta: str) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._play_impl(ContextResponder(ctx), consulta)

    async def _play_impl(self, responder: CommandResponder, consulta: str) -> None:
        """Lógica compartida por todas las variantes de reproducir una pista."""
        guild = responder.guild
        if guild is None:
            await responder.send_error("Este comando solo está disponible dentro de un servidor.")
            return

        member = responder.member
        if member is None or member.voice is None:
            await responder.send_error(
                "Debes estar conectado a un canal de voz para reproducir música."
            )
            return

        state = self._get_state(guild.id)
        if state.voice_client is not None and state.voice_client.channel != member.voice.channel:
            await responder.send_error("Debes estar en el mismo canal de voz que el bot.")
            return

        await responder.start_progress()

        # Se busca antes de entrar al canal: si no hay pista, el bot no entra.
        searching = time.perf_counter()
        try:
            track = await self._resolve(consulta, member.display_name)
        except yt_dlp.utils.DownloadError as error:
            logger.info("No se pudo resolver la consulta de música %r: %s", consulta, error)
            report_event("musica", "búsqueda sin resultado", _since(searching))
            await responder.finish(
                "No se encontró ninguna pista reproducible para esa búsqueda o enlace."
            )
            return
        except TrackTooLongError as error:
            report_event("musica", "pista demasiado larga", _since(searching))
            await responder.finish(
                f"Esa pista dura demasiado o es un directo sin duración conocida "
                f"(límite: {format_duration(MAX_TRACK_DURATION_SECONDS)}). Duración detectada: "
                f"{format_duration(error.duration_seconds)}."
            )
            return
        except TrackUnavailableError:
            report_event("musica", "búsqueda sin audio", _since(searching))
            await responder.finish("La fuente no proporcionó audio reproducible para esa consulta.")
            return
        report_event("musica", "búsqueda", _since(searching))

        if not await self._ensure_connected(state, guild, member.voice.channel):
            await responder.finish("No se pudo conectar al canal de voz. Inténtalo de nuevo.")
            return

        starting_now = state.current is None and len(state.queue) == 0
        try:
            state.queue.add(track)
        except QueueFullError:
            report_event("musica", "cola llena")
            await responder.finish(
                "La cola de este servidor está llena; espera a que avancen pistas."
            )
            return
        position = len(state.queue)
        # La posición va en el campo de los ms: así salen la media y el máximo de la
        # cola. 0 es que suena ya.
        report_event("musica", "pistas en cola", 0.0 if starting_now else float(position))
        state.text_channel = responder.channel
        state.wakeup.set()
        if state.player is None:
            state.player = asyncio.create_task(
                self._player_loop(guild.id), name=f"music-player-{guild.id}"
            )

        if starting_now:
            await responder.finish(
                f"▶️ Reproduciendo ahora: **{track.title}** "
                f"({format_duration(track.duration_seconds)})."
            )
        else:
            await responder.finish(
                f"🎶 Añadida a la cola en la posición {position}: **{track.title}** "
                f"({format_duration(track.duration_seconds)})."
            )
        logros.note_for(
            self.bot,
            responder,
            music_queue_stats(
                query=consulta,
                title=track.title,
                duration_seconds=track.duration_seconds,
                position=1 if starting_now else position,
            ),
        )

    @staticmethod
    async def _resolve(query: str, requested_by: str) -> Track:
        """Busca la consulta con `yt-dlp` fuera del event loop y la valida."""
        info = await asyncio.to_thread(extract_track_info, query)
        return build_track_from_info(info, requested_by=requested_by)

    async def _ensure_connected(
        self,
        state: GuildMusicState,
        guild: discord.Guild,
        channel: discord.VoiceChannel | discord.StageChannel,
    ) -> bool:
        """Conecta el bot al canal si aún no lo está; `False` si no se pudo."""
        async with state.connect_lock:
            if state.voice_client is not None:
                return True
            try:
                if guild.voice_client is not None:
                    # Conexión ajena a la música: un sonido de entrada de pocos
                    # segundos (`bot.cogs.entrance`). La música tiene prioridad.
                    await guild.voice_client.disconnect(force=True)
                # Ensordecido: Discord deja de enviar al bot el audio de los
                # demás, que no necesita, y la bajada en voz cae a casi cero.
                state.voice_client = await channel.connect(self_deaf=True)
                # Hasta que el cifrado de voz está listo no se oye nada: sin
                # esperar, cada canción perdía su primer segundo o dos.
                await wait_until_audible(state.voice_client)
            except (discord.ClientException, TimeoutError) as error:
                logger.warning(
                    "No se pudo conectar al canal de voz %s del servidor %s: %s",
                    channel.id,
                    guild.id,
                    error,
                )
                return False
            return True

    async def _player_loop(self, guild_id: int) -> None:
        """Reproduce la cola del servidor hasta que se para o se queda inactiva.

        Es la única tarea que cambia `current` y la conexión mientras suena
        música. Cancelarla es la forma de parar: su `finally` deja el estado
        limpio y sale del canal.
        """
        state = self._states[guild_id]
        try:
            while state.voice_client is not None and state.voice_client.is_connected():
                track = state.queue.pop_next()
                if track is None:
                    state.wakeup.clear()
                    try:
                        await asyncio.wait_for(state.wakeup.wait(), IDLE_DISCONNECT_SECONDS)
                    except TimeoutError:
                        return
                    continue
                await self._play_track(state, track)
        except Exception:
            logger.exception("Error inesperado en el reproductor del servidor %s", guild_id)
        finally:
            state.player = None
            state.current = None
            state.queue.clear()
            voice, state.voice_client = state.voice_client, None
            if voice is not None:
                voice.stop()
                try:
                    await voice.disconnect(force=True)
                except discord.HTTPException:
                    logger.warning("Error al desconectar del canal de voz", exc_info=True)

    async def _play_track(self, state: GuildMusicState, track: Track) -> None:
        """Reproduce una pista y vuelve cuando acaba, se salta o no llega a sonar."""
        state.current = track
        state.skip_requested = False
        try:
            if track.is_stale():
                track = await self._resolve(track.webpage_url, track.requested_by)
                state.current = track
            before_options = FFMPEG_BEFORE_OPTIONS
            headers = format_ffmpeg_headers(track.http_headers)
            if headers:
                # ffmpeg exige que el valor de -headers vaya entre comillas
                # y termine en salto de línea; se concatena tras el resto.
                before_options = f'{before_options} -headers "{headers}"'
            source = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(
                    track.stream_url, before_options=before_options, options=FFMPEG_OPTIONS
                ),
                volume=volume_percent_to_factor(state.volume_percent),
            )
            loop = asyncio.get_running_loop()
            finished = asyncio.Event()

            def _after_playback(error: Exception | None) -> None:
                # Lo llama el hilo de audio de discord.py, no el event loop.
                if error is not None:
                    logger.warning("Error de reproducción de %r: %s", track.title, error)
                loop.call_soon_threadsafe(finished.set)

            started = loop.time()
            state.voice_client.play(source, after=_after_playback)
            await finished.wait()
            # Lo que sonó, en ms: con `saltada` se ve cuánto aguanta la gente una pista.
            played = (loop.time() - started) * 1000
            if not state.skip_requested and loop.time() - started < FAILED_START_SECONDS:
                logger.warning("La pista %r terminó nada más empezar", track.webpage_url)
                report_event("musica", "fallo al sonar", error=True)
                await self._notify(state, f"⚠️ No se pudo reproducir **{track.title}**.")
            else:
                report_event("musica", "saltada" if state.skip_requested else "escuchada", played)
        except (
            yt_dlp.utils.DownloadError,
            TrackUnavailableError,
            TrackTooLongError,
            discord.ClientException,
        ):
            logger.warning("No se pudo reproducir %r", track.webpage_url, exc_info=True)
            report_event("musica", "fallo al sonar", error=True)
            await self._notify(state, f"⚠️ No se pudo reproducir **{track.title}**.")
        finally:
            state.current = None

    async def _stop_player(self, state: GuildMusicState) -> None:
        """Para la música, vacía la cola y sale del canal (vía el `finally` del bucle)."""
        player = state.player
        if player is None or player is asyncio.current_task():
            return
        player.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await player

    async def _notify(self, state: GuildMusicState, content: str) -> None:
        """Avisa en el último canal conocido; ignora fallos de envío."""
        if state.text_channel is None:
            return
        try:
            await state.text_channel.send(content)
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("No se pudo enviar un aviso de música", exc_info=True)

    @app_commands.command(name="pausar", description="Pausa la canción actual.")
    @app_commands.guild_only()
    async def pause(self, interaction: discord.Interaction) -> None:
        """Pausa la reproducción actual sin vaciar la cola."""
        await self._pause_impl(InteractionResponder(interaction))

    @commands.command(name="pausar")
    @commands.guild_only()
    async def pause_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._pause_impl(ContextResponder(ctx))

    async def _pause_impl(self, responder: CommandResponder) -> None:
        """Lógica compartida por todas las variantes de pausar."""
        state = await self._require_connected_state(responder)
        if state is None:
            return
        if state.voice_client is None or not state.voice_client.is_playing():
            await responder.send_error("No hay ninguna pista reproduciéndose.")
            return
        state.voice_client.pause()
        await responder.send("⏸️ Reproducción pausada.")

    @app_commands.command(name="seguir", description="Reanuda la canción pausada.")
    @app_commands.guild_only()
    async def resume(self, interaction: discord.Interaction) -> None:
        """Reanuda la reproducción si estaba en pausa."""
        await self._resume_impl(InteractionResponder(interaction))

    @commands.command(name="seguir")
    @commands.guild_only()
    async def resume_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._resume_impl(ContextResponder(ctx))

    async def _resume_impl(self, responder: CommandResponder) -> None:
        """Lógica compartida por todas las variantes de reanudar."""
        state = await self._require_connected_state(responder)
        if state is None:
            return
        if state.voice_client is None or not state.voice_client.is_paused():
            await responder.send_error("No hay ninguna pista en pausa.")
            return
        state.voice_client.resume()
        await responder.send("▶️ Reproducción reanudada.")

    @app_commands.command(name="saltar", description="Salta a la siguiente canción.")
    @app_commands.guild_only()
    async def skip(self, interaction: discord.Interaction) -> None:
        """Corta la pista actual; el bucle del reproductor pasa a la siguiente."""
        await self._skip_impl(InteractionResponder(interaction))

    @commands.command(name="saltar")
    @commands.guild_only()
    async def skip_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._skip_impl(ContextResponder(ctx))

    async def _skip_impl(self, responder: CommandResponder) -> None:
        """Lógica compartida por todas las variantes de saltar."""
        state = await self._require_connected_state(responder)
        if state is None:
            return
        if state.voice_client is None or not (
            state.voice_client.is_playing() or state.voice_client.is_paused()
        ):
            await responder.send_error("No hay ninguna pista que saltar.")
            return
        state.skip_requested = True
        state.voice_client.stop()
        await responder.send("⏭️ Pista saltada.")
        logros.note_for(self.bot, responder, StatDelta(add={"music_skips": 1}))

    @app_commands.command(name="parar", description="Detiene la música y desconecta al bot.")
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction) -> None:
        """Vacía la cola, detiene la reproducción y desconecta del canal de voz."""
        await self._stop_impl(InteractionResponder(interaction))

    @commands.command(name="parar")
    @commands.guild_only()
    async def stop_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._stop_impl(ContextResponder(ctx))

    async def _stop_impl(self, responder: CommandResponder) -> None:
        """Lógica compartida por todas las variantes de parar."""
        state = await self._require_connected_state(responder)
        if state is None:
            return
        await self._stop_player(state)
        await responder.send("⏹️ Música detenida y bot desconectado.")
        logros.note_for(self.bot, responder, StatDelta(add={"music_stops": 1}))

    @app_commands.command(name="cola", description="Muestra la canción actual y la cola.")
    @app_commands.guild_only()
    async def queue_(self, interaction: discord.Interaction) -> None:
        """Lista la pista en curso y hasta diez pistas siguientes."""
        await self._queue_impl(InteractionResponder(interaction))

    @commands.command(name="cola")
    @commands.guild_only()
    async def queue_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._queue_impl(ContextResponder(ctx))

    async def _queue_impl(self, responder: CommandResponder) -> None:
        """Lógica compartida por todas las variantes de mostrar la cola."""
        guild = responder.guild
        if guild is None:
            await responder.send_error("Este comando solo está disponible dentro de un servidor.")
            return

        state = self._get_state(guild.id)
        upcoming = state.queue.snapshot()
        if state.current is None and not upcoming:
            await responder.send_error("No hay ninguna pista en reproducción ni en cola.")
            return

        lines = []
        if state.current is not None:
            lines.append(
                f"▶️ **Ahora:** {state.current.title} "
                f"({format_duration(state.current.duration_seconds)}) — "
                f"pedida por {state.current.requested_by}"
            )
        for position, track in enumerate(upcoming[:10], start=1):
            lines.append(
                f"{position}. {track.title} ({format_duration(track.duration_seconds)}) — "
                f"pedida por {track.requested_by}"
            )
        if len(upcoming) > 10:
            lines.append(f"… y {len(upcoming) - 10} pista(s) más.")

        await responder.send("\n".join(lines))

    @app_commands.command(name="quitar", description="Quita una canción de la cola.")
    @app_commands.guild_only()
    @app_commands.describe(posicion="Posición en /cola, empezando en 1.")
    async def remove(self, interaction: discord.Interaction, posicion: int) -> None:
        """Elimina la pista en la posición indicada sin afectar a la actual."""
        await self._remove_impl(InteractionResponder(interaction), posicion)

    @commands.command(name="quitar")
    @commands.guild_only()
    async def remove_text(self, ctx: commands.Context, posicion: int) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._remove_impl(ContextResponder(ctx), posicion)

    async def _remove_impl(self, responder: CommandResponder, posicion: int) -> None:
        """Lógica compartida por todas las variantes de quitar una pista."""
        state = await self._require_connected_state(responder)
        if state is None:
            return
        try:
            track = state.queue.remove_at(posicion)
        except IndexError:
            await responder.send_error(f"No hay ninguna pista en la posición {posicion}.")
            return
        await responder.send(f"🗑️ Se quitó de la cola: **{track.title}**.")
        logros.note_for(self.bot, responder, StatDelta(add={"music_removes": 1}))

    @app_commands.command(name="vaciar", description="Vacía la cola sin parar la canción actual.")
    @app_commands.guild_only()
    async def clear(self, interaction: discord.Interaction) -> None:
        """Vacía la cola de pistas pendientes."""
        await self._clear_impl(InteractionResponder(interaction))

    @commands.command(name="vaciar")
    @commands.guild_only()
    async def clear_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        await self._clear_impl(ContextResponder(ctx))

    async def _clear_impl(self, responder: CommandResponder) -> None:
        """Lógica compartida por todas las variantes de limpiar la cola."""
        state = await self._require_connected_state(responder)
        if state is None:
            return
        state.queue.clear()
        await responder.send("🧹 Cola vaciada.")
        logros.note_for(self.bot, responder, StatDelta(add={"music_clears": 1}))

    @app_commands.command(name="volumen", description="Cambia el volumen (1-200%).")
    @app_commands.guild_only()
    @app_commands.describe(valor="Porcentaje de volumen entre 1 y 200.")
    async def volume(
        self,
        interaction: discord.Interaction,
        valor: app_commands.Range[int, MIN_VOLUME_PERCENT, MAX_VOLUME_PERCENT],
    ) -> None:
        """Ajusta el volumen, incluso mientras una pista ya se reproduce."""
        await self._volume_impl(InteractionResponder(interaction), valor)

    @commands.command(name="volumen")
    @commands.guild_only()
    async def volume_text(self, ctx: commands.Context, valor: int) -> None:
        """Versión de texto (`.`) del comando slash homónimo."""
        if not (MIN_VOLUME_PERCENT <= valor <= MAX_VOLUME_PERCENT):
            await ctx.send(
                f"El volumen debe estar entre {MIN_VOLUME_PERCENT} y {MAX_VOLUME_PERCENT}%."
            )
            return
        await self._volume_impl(ContextResponder(ctx), valor)

    async def _volume_impl(
        self,
        responder: CommandResponder,
        valor: int,
    ) -> None:
        """Lógica compartida por todas las variantes de ajustar el volumen."""
        state = await self._require_connected_state(responder)
        if state is None:
            return
        state.volume_percent = valor
        if state.voice_client is not None and isinstance(
            state.voice_client.source, discord.PCMVolumeTransformer
        ):
            state.voice_client.source.volume = volume_percent_to_factor(valor)
        await responder.send(f"🔊 Volumen ajustado al {valor}%.")
        logros.note_for(self.bot, responder, music_volume_stats(valor))

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Para la música si echan al bot del canal o este se queda sin humanos."""
        state = self._states.get(member.guild.id)
        if state is None or state.voice_client is None:
            return

        if member.id == self.bot.user.id:
            if after.channel is None:
                await self._stop_player(state)
            return

        channel = state.voice_client.channel
        if channel is not None and not any(not candidate.bot for candidate in channel.members):
            await self._stop_player(state)

    async def cog_unload(self) -> None:
        """Para todos los reproductores (y sale de los canales) al descargar el cog."""
        for state in self._states.values():
            await self._stop_player(state)


async def setup(bot: commands.Bot) -> None:
    """Registra el cog de música en el cliente."""
    await bot.add_cog(Music(bot))
