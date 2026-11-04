"""Preparación y reproducción ligera de los sonidos de entrada al canal de voz.

El bot corre en un NAS, así que el diseño minimiza el trabajo en cada
reproducción y lo concentra en el momento de subir el sonido:

1. Al subir un audio, `ffmpeg` lo convierte UNA vez a Ogg/Opus a 48 kHz
   estéreo con paquetes de 20 ms (el formato exacto que transmite Discord)
   y normaliza su sonoridad, para que el 100 % suene parecido para todos.
2. Al cambiar el volumen se vuelve a generar el clip final a partir de esa
   fuente; el volumen queda "horneado" en el archivo.
3. Al reproducir no se lanza `ffmpeg`: `OpusPacketSource` lee los paquetes
   Opus del archivo y se los entrega a `discord.py` tal cual. Sin
   decodificar, sin recodificar y sin procesos externos.

Los adjuntos de Discord son entrada no confiable: `ffmpeg` y `ffprobe` se
lanzan con `-protocol_whitelist file` para que un archivo manipulado (por
ejemplo, una lista HLS) no pueda hacer que lean URLs de red.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from pathlib import Path

import discord
from discord.oggparse import OggError, OggStream

logger = logging.getLogger(__name__)

#: Duración máxima del clip. Se deja un margen mínimo por el redondeo de los
#: contenedores (un audio de "3 s" puede medir 3,02 s).
MAX_CLIP_SECONDS = 3.0
CLIP_DURATION_TOLERANCE_SECONDS = 0.1

#: Tamaño máximo del adjunto. 3 s de WAV a 96 kHz/24 bits ocupan ~1,7 MB;
#: 8 MB deja sitio para formatos poco eficientes sin aceptar archivos absurdos.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

MIN_VOLUME_PERCENT = 10
MAX_VOLUME_PERCENT = 200
DEFAULT_VOLUME_PERCENT = 100

#: Sonoridad objetivo (LUFS) de la normalización y techo del limitador. El
#: limitador evita que el 200 % sature: sube el volumen percibido sin clipping.
TARGET_LOUDNESS_LUFS = -16
LIMITER_CEILING = 0.95

#: Tiempo máximo para cualquier llamada a ffmpeg/ffprobe sobre un clip corto.
FFMPEG_TIMEOUT_SECONDS = 30

#: Duración de cada paquete Opus generado (`-frame_duration 20`).
PACKET_SECONDS = 0.02

#: Cabeceras Ogg/Opus que no son audio y no deben enviarse a Discord.
_OPUS_HEADER_PREFIXES = (b"OpusHead", b"OpusTags")


class EntranceSoundError(Exception):
    """Error esperado al preparar un sonido; su mensaje es apto para el usuario."""


def validate_volume(volume_percent: int) -> int:
    """Comprueba que el volumen esté en el rango admitido y lo devuelve.

    Raises:
        EntranceSoundError: Si está fuera de `MIN_VOLUME_PERCENT`-`MAX_VOLUME_PERCENT`.
    """
    if not MIN_VOLUME_PERCENT <= volume_percent <= MAX_VOLUME_PERCENT:
        raise EntranceSoundError(
            f"El volumen debe estar entre {MIN_VOLUME_PERCENT} y {MAX_VOLUME_PERCENT} %."
        )
    return volume_percent


async def _run(*args: str) -> bytes:
    """Ejecuta un binario sin shell y devuelve su salida estándar.

    Raises:
        EntranceSoundError: Si el proceso falla o supera el tiempo máximo.
    """
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=FFMPEG_TIMEOUT_SECONDS
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        raise EntranceSoundError("El audio tardó demasiado en procesarse.") from None
    if process.returncode != 0:
        # stderr puede ser largo; basta el final para diagnosticar.
        logger.warning("%s falló (%s): %s", args[0], process.returncode, stderr[-500:])
        raise EntranceSoundError("No pude leer ese archivo de audio.")
    return stdout


async def probe_audio_duration(path: Path) -> float:
    """Devuelve la duración en segundos del primer flujo de audio de `path`.

    Raises:
        EntranceSoundError: Si no es un audio legible o no tiene duración.
    """
    output = await _run(
        "ffprobe",
        "-v", "error",
        "-protocol_whitelist", "file",
        "-select_streams", "a:0",
        "-show_entries", "stream=duration:format=duration",
        "-of", "json",
        str(path),
    )  # fmt: skip
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        raise EntranceSoundError("No pude leer ese archivo de audio.") from None
    streams = data.get("streams") or []
    if not streams:
        raise EntranceSoundError("Ese archivo no contiene audio.")
    # Algunos contenedores solo informan la duración a nivel de formato.
    for raw in (streams[0].get("duration"), (data.get("format") or {}).get("duration")):
        try:
            duration = float(raw)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            return duration
    raise EntranceSoundError("No pude saber cuánto dura ese audio.")


def check_duration(duration_seconds: float, max_seconds: float = MAX_CLIP_SECONDS) -> None:
    """Rechaza clips más largos que `max_seconds` (con su tolerancia).

    Args:
        max_seconds: Duración máxima; por defecto, la de los sonidos de entrada.
            La beernight admite clips algo más largos.

    Raises:
        EntranceSoundError: Con la duración real, para que el usuario sepa cuánto recortar.
    """
    if duration_seconds > max_seconds + CLIP_DURATION_TOLERANCE_SECONDS:
        raise EntranceSoundError(
            f"El audio dura {duration_seconds:.1f} s y el máximo es "
            f"{max_seconds:.0f} s. Recórtalo y vuelve a subirlo."
        )


def _opus_output_args(bitrate_kbps: int) -> tuple[str, ...]:
    """Argumentos de salida comunes: Opus 48 kHz estéreo en paquetes de 20 ms."""
    return (
        "-ar", "48000",
        "-ac", "2",
        "-c:a", "libopus",
        "-b:a", f"{bitrate_kbps}k",
        "-frame_duration", "20",
        "-map_metadata", "-1",
        "-f", "ogg",
    )  # fmt: skip


async def build_source_clip(
    upload_path: Path, output_path: Path, max_seconds: float = MAX_CLIP_SECONDS
) -> None:
    """Convierte el adjunto subido en la fuente normalizada del sonido.

    Se guarda a 128 kbps para que regenerarla con otro volumen apenas pierda
    calidad. `-t` corta igualmente en `max_seconds` por si `ffprobe` se quedara
    corto. El resultado ya es Opus en paquetes de 20 ms: se puede reproducir tal
    cual con `OpusPacketSource` (lo hace la beernight).
    """
    await _run(
        "ffmpeg", "-y", "-v", "error",
        "-protocol_whitelist", "file",
        "-i", str(upload_path),
        "-map", "0:a:0", "-vn", "-sn", "-dn",
        "-t", str(max_seconds),
        "-af", f"loudnorm=I={TARGET_LOUDNESS_LUFS}:TP=-1.5:LRA=11",
        *_opus_output_args(128),
        str(output_path),
    )  # fmt: skip


async def render_clip(source_path: Path, output_path: Path, volume_percent: int) -> None:
    """Genera el clip final con el volumen aplicado, listo para enviarse a Discord.

    `level=false` desactiva el auto-nivel de `alimiter`, que si no volvería a
    subir o bajar el volumen por su cuenta y anularía el ajuste del usuario.
    """
    validate_volume(volume_percent)
    gain = volume_percent / 100
    await _run(
        "ffmpeg", "-y", "-v", "error",
        "-protocol_whitelist", "file",
        "-i", str(source_path),
        "-af", f"volume={gain:.2f},alimiter=limit={LIMITER_CEILING}:level=false",
        *_opus_output_args(96),
        str(output_path),
    )  # fmt: skip


async def build_raw_source(
    upload_path: Path, output_path: Path, *, start: float, seconds: float
) -> None:
    """Guarda `seconds` del adjunto a partir del segundo `start`, sin retocarlo.

    Es la fuente de las ranuras de `entrada`: se recorta, se normaliza y se le
    aplican efecto y volumen después, en `render_slot`, sobre el trozo que
    suena. Normalizar el trozo y no la fuente entera hace que el 100 % suene
    igual de fuerte aunque el audio original tenga partes flojas.

    `-ss` delante de `-i` busca sin decodificar lo anterior: un audio largo
    no cuesta más que uno corto.
    """
    await _run(
        "ffmpeg", "-y", "-v", "error",
        "-protocol_whitelist", "file",
        "-ss", f"{start:.3f}",
        "-i", str(upload_path),
        "-map", "0:a:0", "-vn", "-sn", "-dn",
        "-t", f"{seconds:.3f}",
        *_opus_output_args(128),
        str(output_path),
    )  # fmt: skip


def slot_filters(*, effect_filter: str, fade: bool, volume_percent: int) -> str:
    """La cadena de filtros de una ranura: efecto, fundido, sonoridad y volumen.

    El fundido de salida se hace dando la vuelta al audio (`areverse`), así no
    hace falta saber cuánto dura. `loudnorm` va después del efecto para que el
    eco o el robot no cambien lo fuerte que suena.
    """
    validate_volume(volume_percent)
    parts = [effect_filter] if effect_filter else []
    if fade:
        parts.append("afade=t=in:d=0.15,areverse,afade=t=in:d=0.4,areverse")
    parts.append(f"loudnorm=I={TARGET_LOUDNESS_LUFS}:TP=-1.5:LRA=11")
    parts.append(f"volume={volume_percent / 100:.2f},alimiter=limit={LIMITER_CEILING}:level=false")
    return ",".join(parts)


async def render_slot(
    source_path: Path,
    output_path: Path,
    *,
    start_ms: int,
    length_ms: int,
    volume_percent: int,
    effect_filter: str = "",
    speed: float = 1.0,
    fade: bool = False,
) -> None:
    """Genera el clip de una ranura desde su fuente, listo para mandarlo a Discord.

    Args:
        start_ms: Dónde empieza el trozo dentro de la fuente.
        length_ms: Lo que debe durar el clip.
        speed: Cuánto acelera el efecto: se toman `length_ms × speed` de la
            fuente para que, acelerado o frenado, dure lo pedido.
    """
    span = length_ms * speed / 1000
    await _run(
        "ffmpeg", "-y", "-v", "error",
        "-protocol_whitelist", "file",
        "-ss", f"{start_ms / 1000:.3f}",
        "-t", f"{span:.3f}",
        "-i", str(source_path),
        "-af", slot_filters(effect_filter=effect_filter, fade=fade, volume_percent=volume_percent),
        "-t", f"{length_ms / 1000:.3f}",
        *_opus_output_args(96),
        str(output_path),
    )  # fmt: skip


def read_opus_packets(data: bytes) -> list[bytes]:
    """Extrae los paquetes de audio Opus de un archivo Ogg ya en memoria.

    Se descartan las cabeceras `OpusHead` y `OpusTags`, que describen el
    flujo pero no son audio.

    Raises:
        EntranceSoundError: Si el archivo no es un Ogg válido o está vacío.
    """
    try:
        packets = [
            packet
            for packet in OggStream(io.BytesIO(data)).iter_packets()
            if not packet.startswith(_OPUS_HEADER_PREFIXES)
        ]
    except OggError:
        raise EntranceSoundError("El sonido guardado está dañado. Vuelve a subirlo.") from None
    if not packets:
        raise EntranceSoundError("El sonido guardado está vacío. Vuelve a subirlo.")
    return packets


class OpusPacketSource(discord.AudioSource):
    """Fuente de audio que entrega paquetes Opus ya codificados.

    `discord.py` llama a `read()` cada 20 ms desde su hilo de reproducción.
    Como `is_opus()` devuelve `True`, envía cada paquete sin tocarlo: no hay
    codificación en el proceso del bot ni proceso `ffmpeg` hijo.
    """

    def __init__(self, packets: list[bytes]) -> None:
        self._packets = iter(packets)

    def read(self) -> bytes:
        """Devuelve el siguiente paquete, o `b""` al terminar."""
        return next(self._packets, b"")

    def is_opus(self) -> bool:
        """Indica a `discord.py` que los datos ya están en Opus."""
        return True


async def play_packets(voice: discord.VoiceClient, packets: list[bytes]) -> None:
    """Reproduce paquetes Opus y espera a que terminen (con un tope de seguridad).

    La usan los sonidos de entrada y la beernight. Si Discord no deja
    reproducir (ya suena otra cosa), se registra y se sigue.
    """
    loop = asyncio.get_running_loop()
    finished = asyncio.Event()

    def _after(error: Exception | None) -> None:
        # Lo llama el hilo de audio de discord.py, no el event loop.
        if error is not None:
            logger.warning("Error de reproducción de un clip: %s", error)
        loop.call_soon_threadsafe(finished.set)

    try:
        voice.play(OpusPacketSource(packets), after=_after)
    except discord.ClientException:
        logger.warning("No se pudo reproducir un clip", exc_info=True)
        return
    try:
        await asyncio.wait_for(finished.wait(), timeout=len(packets) * PACKET_SECONDS + 5)
    except TimeoutError:
        voice.stop()
