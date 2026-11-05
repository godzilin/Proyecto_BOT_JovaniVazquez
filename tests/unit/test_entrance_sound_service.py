"""Pruebas de bot.services.entrance_sound con `ffmpeg` real sobre audios sintéticos.

Generan tonos de pocos segundos en una carpeta temporal; no usan red ni Discord.
Se omiten si la máquina no tiene `ffmpeg`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from bot.services.entrance_sound import (
    MAX_CLIP_SECONDS,
    EntranceSoundError,
    OpusPacketSource,
    build_source_clip,
    check_duration,
    probe_audio_duration,
    read_opus_packets,
    render_clip,
    validate_volume,
)

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg no está instalado",
)


def make_tone(path: Path, seconds: float) -> Path:
    """Escribe un WAV con un tono de 440 Hz de la duración indicada."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            str(path),
        ],
        check=True,
    )  # fmt: skip
    return path


def test_validate_volume_acepta_los_extremos_y_rechaza_fuera_de_rango() -> None:
    assert validate_volume(10) == 10
    assert validate_volume(200) == 200
    with pytest.raises(EntranceSoundError):
        validate_volume(201)
    with pytest.raises(EntranceSoundError):
        validate_volume(9)


def test_check_duration_tolera_el_redondeo_pero_no_un_clip_largo() -> None:
    check_duration(MAX_CLIP_SECONDS + 0.05)
    with pytest.raises(EntranceSoundError, match="3.5 s"):
        check_duration(3.5)


def test_opus_packet_source_entrega_paquetes_y_luego_vacio() -> None:
    source = OpusPacketSource([b"a", b"b"])
    assert source.is_opus()
    assert [source.read(), source.read(), source.read()] == [b"a", b"b", b""]


def test_read_opus_packets_rechaza_datos_que_no_son_ogg() -> None:
    with pytest.raises(EntranceSoundError):
        read_opus_packets(b"esto no es un ogg")


@requires_ffmpeg
@pytest.mark.asyncio
async def test_flujo_completo_genera_paquetes_de_20_ms_sin_cabeceras(tmp_path: Path) -> None:
    upload = make_tone(tmp_path / "tono.wav", 2.0)
    assert await probe_audio_duration(upload) == pytest.approx(2.0, abs=0.05)

    source = tmp_path / "source.ogg"
    await build_source_clip(upload, source)
    clip = tmp_path / "clip.ogg"
    await render_clip(source, clip, 200)

    packets = read_opus_packets(clip.read_bytes())
    # 2 s / 20 ms = 100 paquetes; el codificador puede añadir uno de relleno.
    assert 100 <= len(packets) <= 102
    assert not any(packet.startswith((b"OpusHead", b"OpusTags")) for packet in packets)


@requires_ffmpeg
@pytest.mark.asyncio
async def test_probe_audio_duration_rechaza_un_archivo_que_no_es_audio(tmp_path: Path) -> None:
    bogus = tmp_path / "falso.mp3"
    bogus.write_bytes(b"no soy un mp3")
    with pytest.raises(EntranceSoundError):
        await probe_audio_duration(bogus)


# -- Cifrado de voz (DAVE) -------------------------------------------------------------------


class _FakeVoiceState:
    """El estado de conexión de `discord.py` que mira `wait_until_audible`."""

    def __init__(self, version: int, ready_after: int | None) -> None:
        self.dave_protocol_version = version
        self._ready_after = ready_after
        self.checks = 0

    @property
    def can_encrypt(self) -> bool:
        self.checks += 1
        return self._ready_after is not None and self.checks > self._ready_after


async def test_sin_cifrado_de_voz_no_se_espera() -> None:
    from types import SimpleNamespace

    from bot.services.entrance_sound import wait_until_audible

    state = _FakeVoiceState(version=0, ready_after=None)
    assert await wait_until_audible(SimpleNamespace(_connection=state), timeout=1) is True
    assert state.checks == 0


async def test_con_cifrado_espera_a_que_las_claves_esten_listas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    import bot.services.entrance_sound as module

    monkeypatch.setattr(module, "DAVE_POLL_SECONDS", 0.001)
    state = _FakeVoiceState(version=1, ready_after=3)
    assert await module.wait_until_audible(SimpleNamespace(_connection=state), timeout=1) is True
    assert state.checks == 4  # Tres veces sin claves y la cuarta, listo.


async def test_si_el_cifrado_nunca_esta_listo_no_se_cuelga(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    import bot.services.entrance_sound as module

    monkeypatch.setattr(module, "DAVE_POLL_SECONDS", 0.001)
    state = _FakeVoiceState(version=1, ready_after=None)
    assert (
        await module.wait_until_audible(SimpleNamespace(_connection=state), timeout=0.05) is False
    )
