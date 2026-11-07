"""Genera los sonidos de las tragaperras de la plaza del Jovaniverso.

Para qué existe: las tragaperras 3D de la plaza (`universo/`) necesitan efectos
(botón, giro, paradas, tensión, monedas, premios...). Cada sonido es una mezcla de
dos procedencias: capas sintetizadas aquí con numpy (osciladores, ruido filtrado,
campanillas FM, golpes graves, arpegios) y capas de paquetes sin royalties de
Kenney (https://kenney.nl, licencia CC0, sin atribución obligatoria): Casino Audio,
Interface Sounds e Impact Sounds. Algunos sonidos son solo sintetizados.

Qué hace, en orden:

1. Descarga los tres paquetes a `docs/.sonidos_cache/` (ignorada por git). La web de
   Kenney rechaza el User-Agent por defecto y el enlace del zip sale de
   `/download-link/<hex de "assets/<paquete>">`, que exige el `Referer` de la página.
2. Sintetiza y mezcla cada sonido con semillas fijas (mismo resultado siempre).
3. Recorta silencios, aplica fundidos, iguala el volumen (los clics más bajos que las
   fanfarrias) y deja el pico en -1,5 dBFS antes de codificar.
4. Codifica MP3 mono de 44,1 kHz (MP3 porque Safari/iOS no decodifica Ogg en todas las
   versiones) en `universo/src/sonidos/`, escribe su `LEEME.md` y mide cada MP3
   (duración, pico, RMS, peso). Sale con error si algo no cumple lo pedido.

Uso (requiere ffmpeg en el PATH o en `C:/ffmpeg/bin`, numpy, y red la primera vez):

    .venv/Scripts/python docs/sonidos_tragas.py

No toca nada más del repositorio. Para cambiar un sonido se edita su función de abajo
y se vuelve a ejecutar; el `LEEME.md` se rehace solo.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
import wave
import zipfile
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "docs" / ".sonidos_cache"
SALIDA = ROOT / "universo" / "src" / "sonidos"
SR = 44100
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
CC0 = "http://creativecommons.org/publicdomain/zero/1.0/"
PAQUETES = {
    "casino-audio": "Casino Audio 1.1",
    "interface-sounds": "Interface Sounds",
    "impact-sounds": "Impact Sounds",
}
FFMPEG = shutil.which("ffmpeg") or "C:/ffmpeg/bin/ffmpeg"
BITRATE = "80k"
PICO_PREVIO_DB = -1.5  # margen: el MP3 decodificado se pasa unas décimas del pico original
PESO_MAX_KB = 220

# ---------------------------------------------------------------- paquetes externos


def descargar(paquete: str) -> Path:
    """Baja el zip de un paquete de Kenney a la caché (una sola vez) y devuelve su ruta."""
    destino = CACHE / f"{paquete}.zip"
    if destino.exists():
        return destino
    CACHE.mkdir(parents=True, exist_ok=True)
    pagina = f"https://kenney.nl/assets/{paquete}"
    peticion = urllib.request.Request(
        "https://kenney.nl/download-link/" + f"assets/{paquete}".encode().hex(),
        headers={"User-Agent": UA, "Referer": pagina},
    )
    enlace = json.load(urllib.request.urlopen(peticion, timeout=60))["url"]
    datos = urllib.request.urlopen(
        urllib.request.Request(enlace, headers={"User-Agent": UA}), timeout=120
    ).read()
    destino.write_bytes(datos)
    return destino


def decodificar(datos: bytes) -> np.ndarray:
    """Pasa un audio (ogg) a mono, 44,1 kHz, float32."""
    r = subprocess.run(
        [
            FFMPEG,
            "-v",
            "error",
            "-i",
            "pipe:0",
            "-f",
            "f32le",
            "-ac",
            "1",
            "-ar",
            str(SR),
            "pipe:1",
        ],
        input=datos,
        capture_output=True,
        check=True,
    )
    return np.frombuffer(r.stdout, dtype="<f4").astype(np.float64)


_usadas: set[str] = set()  # capas externas de la función en curso (para el LEEME)
_ext_cache: dict[str, np.ndarray] = {}


def ext(clave: str, dur: float | None = None) -> np.ndarray:
    """Capa externa `paquete:archivo` (sin `.ogg`), sin silencio inicial, pico 1 y cortada."""
    _usadas.add(clave)
    if clave not in _ext_cache:
        paquete, nombre = clave.split(":")
        with zipfile.ZipFile(descargar(paquete)) as z:
            x = decodificar(z.read(f"Audio/{nombre}.ogg"))
        x = recortar(x)
        _ext_cache[clave] = x / np.abs(x).max()
    x = _ext_cache[clave]
    return fundir(x[: int(dur * SR)], 0.0, 0.01) if dur else x


# ---------------------------------------------------------------- utilidades de señal


def T(d: float) -> np.ndarray:
    """Eje de tiempo de `d` segundos."""
    return np.arange(int(round(d * SR))) / SR


def ex(t, tau):
    """Caída exponencial."""
    return np.exp(-t / tau)


def ad(t, ataque, tau):
    """Ataque lineal corto y caída exponencial."""
    return np.minimum(t / ataque, 1.0) * np.exp(-t / tau)


def midi(n):
    return 440.0 * 2 ** ((np.asarray(n, dtype=float) - 69) / 12)


def filtro(x, fc, tipo="lp", orden=3):
    """Filtro en frecuencia con respuesta suave (pasa-bajos `lp` o pasa-altos `hp`)."""
    f = np.fft.rfftfreq(len(x), 1 / SR)
    r = (f / fc) ** (2 * orden)
    m = 1 / np.sqrt(1 + r) if tipo == "lp" else np.sqrt(r / (1 + r))
    return np.fft.irfft(np.fft.rfft(x) * m, len(x))


def pitch(x, razon):
    """Cambia el tono (y la duración) remuestreando."""
    return np.interp(np.arange(0, len(x) - 1, razon), np.arange(len(x)), x)


def fundir(x, entrada, salida):
    """Fundido lineal de entrada y salida (en segundos)."""
    x = x.copy()
    a, b = min(int(entrada * SR), len(x)), min(int(salida * SR), len(x))
    if a:
        x[:a] *= np.linspace(0, 1, a)
    if b:
        x[-b:] *= np.linspace(1, 0, b)
    return x


def recortar(x, umbral_db=-50):
    """Quita el silencio del principio y del final."""
    pico = np.abs(x).max()
    vivo = np.nonzero(np.abs(x) > pico * 10 ** (umbral_db / 20))[0]
    return x[vivo[0] : vivo[-1] + 1] if len(vivo) else x


def ruido(rng, d):
    return rng.standard_normal(int(round(d * SR)))


def campana(f, d, idx=3.0, ratio=3.5, tau=None):
    """Campanilla FM: la modulación decae más rápido que la envolvente."""
    t = T(d)
    tau = tau or d / 4
    mod = idx * ex(t, tau * 0.6) * np.sin(2 * np.pi * f * ratio * t)
    return ad(t, 0.002, tau) * np.sin(2 * np.pi * f * t + mod)


def golpe(d=0.2, f0=130, f1=45, tau=0.07):
    """Bombo / golpe grave: seno que cae de tono."""
    t = T(d)
    fase = 2 * np.pi * np.cumsum(f1 + (f0 - f1) * ex(t, tau * 0.4)) / SR
    return ad(t, 0.002, tau) * np.sin(fase)


def tic(rng, f=2200, d=0.025):
    """Chasquido de trinquete: ruido agudo y un tono corto."""
    t = T(d)
    return ex(t, d / 5) * (
        0.6 * filtro(ruido(rng, d), 2500, "hp") + 0.5 * np.sin(2 * np.pi * f * t)
    )


def metal(f, d=0.5):
    """Tono inarmónico de metal golpeado."""
    t = T(d)
    return ad(t, 0.001, d / 4) * sum(
        a * np.sin(2 * np.pi * f * r * t) for r, a in ((1, 1), (2.76, 0.6), (5.4, 0.4), (8.9, 0.2))
    )


def metales(f, d, vib=5.5, glide=0.0, brillo=1.0):
    """Nota de metal (suma de armónicos con vibrato), para fanfarrias."""
    t = T(d)
    fr = (
        f
        * 2 ** (glide * t / d / 12)
        * (1 + 0.004 * np.sin(2 * np.pi * vib * t) * np.minimum(t / 0.2, 1))
    )
    fase = 2 * np.pi * np.cumsum(fr) / SR
    y = sum(np.sin(h * fase) / h ** (1.1 / brillo) for h in range(1, 9))
    env = np.minimum(t / 0.03, 1) * np.minimum((d - t) / (0.25 * d + 1e-9), 1) ** 0.7
    return y * env / 3


class Mezcla:
    """Suma de capas colocadas en el tiempo."""

    def __init__(self):
        self.capas: list[tuple[float, np.ndarray, float]] = []

    def add(self, t0, x, g=1.0):
        self.capas.append((t0, np.asarray(x, dtype=float), g))
        return self

    def arpegio(self, t0, notas, paso, g=1.0, d=0.5, idx=3.0):
        for i, n in enumerate(notas):
            self.add(t0 + i * paso, campana(float(midi(n)), d, idx), g)
        return self

    def acorde(self, t0, notas, d, g=1.0, **kw):
        for n in notas:
            self.add(t0, metales(float(midi(n)), d, **kw), g / len(notas) ** 0.5)
        return self

    def lluvia(self, rng, t0, t1, n, claves, g0, g1):
        """Monedas cayendo: capas externas de fichas con tono variado más tintineos FM."""
        for _ in range(n):
            t = t0 + (t1 - t0) * rng.random()
            g = g0 + (g1 - g0) * (t - t0) / (t1 - t0)
            self.add(
                t, pitch(ext(claves[rng.integers(len(claves))], 0.15), 0.9 + 0.4 * rng.random()), g
            )
            if rng.random() < 0.5:
                f = float(midi(88 + rng.integers(0, 8)))
                self.add(t + 0.01, campana(f, 0.12, 2.0, 3.5), g * 0.35)

    def render(self, dur):
        buf = np.zeros(int(dur * SR))
        for t0, x, g in self.capas:
            i = int(round(t0 * SR))
            if i < len(buf):
                trozo = x[: len(buf) - i]
                buf[i : i + len(trozo)] += g * trozo
        return buf


# ---------------------------------------------------------------- los sonidos

SONIDOS: dict[str, dict] = {}


def sonido(nombre, dur, rms_db, sintetico, mide, fade_in=0.003, fade_out=0.03):
    """Registra un sonido: duración, RMS objetivo (dBFS), texto del LEEME y duración máxima."""

    def deco(fn):
        SONIDOS[nombre] = dict(
            fn=fn, dur=dur, rms=rms_db, sint=sintetico, max=mide, fin=fade_in, fout=fade_out
        )
        return fn

    return deco


def rng_de(nombre):
    return np.random.default_rng(zlib.crc32(nombre.encode()))


@sonido("boton", 0.10, -22, "clic seco (ruido agudo + tono de 1,9 kHz + golpecito grave)", 0.15)
def _boton(r):
    t = T(0.12)
    m = Mezcla()
    m.add(0, 0.8 * ex(t, 0.002) * filtro(ruido(r, 0.12), 3000, "hp"))
    m.add(0, 0.6 * ex(t, 0.012) * np.sin(2 * np.pi * 1900 * t))
    m.add(0, 0.7 * golpe(0.06, 220, 120, 0.02))
    m.add(0, ext("interface-sounds:switch_002", 0.12), 0.5)
    return m


@sonido(
    "girar",
    0.65,
    -18,
    "motor que sube (diente de sierra 70-300 Hz) y trinquete que se acelera",
    0.7,
)
def _girar(r):
    t = T(0.6)
    f = 70 + 230 * np.minimum(t / 0.5, 1) ** 2
    fase = 2 * np.pi * np.cumsum(f) / SR
    motor = sum(np.sin(h * fase) / h for h in range(1, 7)) * ad(t, 0.05, 0.4) * 0.5
    m = Mezcla().add(0.02, filtro(motor, 1500))
    for i in range(8):
        m.add(0.05 + 0.5 * (i / 8) ** 0.8, tic(r), 0.7)
    m.add(0, ext("impact-sounds:impactMetal_light_001", 0.25), 0.9)
    m.add(0.05, ext("casino-audio:dice-shake-2", 0.45), 0.35)
    return m


@sonido(
    "parada",
    0.28,
    -18,
    "golpe grave (110 a 50 Hz) + chasquido agudo + resonancia metálica de 620 Hz",
    0.3,
)
def _parada(r):
    m = Mezcla()
    m.add(0, 1.1 * golpe(0.18, 110, 50, 0.06))
    m.add(0, tic(r, 3000, 0.02), 0.8)
    m.add(0, 0.3 * metal(620, 0.25))
    m.add(0, ext("impact-sounds:impactMetal_medium_002", 0.25), 0.9)
    return m


@sonido(
    "parada_lenta",
    0.85,
    -17,
    "tics de trinquete que se frenan + golpe grave profundo + campana FM de 330 Hz",
    0.9,
)
def _parada_lenta(r):
    m = Mezcla()
    for i, t in enumerate((0.0, 0.09, 0.2, 0.35)):
        m.add(t, tic(r, 2000 - 150 * i), 0.6)
    m.add(0.5, 1.2 * golpe(0.35, 90, 38, 0.12))
    m.add(0.5, campana(330, 0.4, 2.0, 2.76, 0.18), 0.3)
    m.add(0.5, 0.3 * metal(440, 0.4))
    m.add(0.5, ext("impact-sounds:impactMetal_heavy_001", 0.35), 0.9)
    m.add(0.5, ext("impact-sounds:impactBell_heavy_000", 0.4), 0.4)
    return m


@sonido(
    "tension",
    2.5,
    -18,
    "solo sintetizado: dos tonos que suben 2 octavas, trémolo que se acelera, ruido agudo y latido",
    2.6,
    fade_in=0.02,
    fade_out=0.02,
)
def _tension(r):
    t = T(2.5)
    u = t / 2.5
    fase = 2 * np.pi * np.cumsum(200 * 4**u) / SR
    tono = (
        np.sin(fase)
        + 0.5 * u * np.sin(2 * fase)
        + 0.3 * u * np.sin(3 * fase)
        + 0.4 * np.sin(1.5 * fase)
    )
    trem = 0.65 + 0.35 * np.sin(2 * np.pi * np.cumsum(4 + 14 * u) / SR)
    m = Mezcla().add(0, tono * (0.35 + 0.65 * u) * trem * 0.6)
    m.add(0, filtro(ruido(r, 2.5), 3500, "hp") * u**2 * 0.25)
    tk = 0.1
    while tk < 2.45:
        m.add(tk, tic(r, 1200), 0.4 * (0.4 + u[int(tk * SR)]))
        tk += 0.25 * (1 - 0.75 * tk / 2.5)
    return m


def _moneda(notas, claves):
    def fn(r):
        m = Mezcla()
        for k, f in enumerate(notas):
            t = T(0.14)
            m.add(
                0.004 * k, ad(t, 0.001, 0.05 - 0.01 * k) * np.sin(2 * np.pi * f * t), 0.8 - 0.25 * k
            )
        m.add(0, campana(notas[0] * 1.5, 0.14, 1.5, 3.5, 0.04), 0.3)
        m.add(0, ext(claves, 0.08), 0.5)
        return m

    return fn


for _i, (_notas, _clave) in enumerate(
    (
        ((2637, 3520), "casino-audio:chips-collide-1"),
        ((2794, 3729), "casino-audio:chips-collide-2"),
        ((2349, 3136), "casino-audio:chips-collide-3"),
    ),
    start=1,
):
    sonido(
        f"moneda_{_i}",
        0.10,
        -24,
        "dos parciales agudos y una campanilla FM (tono distinto en cada variante)",
        0.15,
    )(_moneda(_notas, _clave))


@sonido(
    "casi",
    0.85,
    -18,
    "tres notas de metal que se desinflan (sol, mi, do con caída de tono) y un soplido de ruido",
    0.9,
)
def _casi(r):
    m = Mezcla()
    m.add(0.0, metales(float(midi(67)), 0.17, brillo=0.7), 0.5)
    m.add(0.2, metales(float(midi(64)), 0.17, brillo=0.7), 0.5)
    m.add(0.4, metales(float(midi(60)), 0.45, glide=-2.5, brillo=0.7), 0.6)
    t = T(0.4)
    m.add(0.45, filtro(ruido(r, 0.4), 1500) * ad(t, 0.02, 0.15) * 0.25)
    m.add(0.0, ext("interface-sounds:drop_002", 0.3), 0.3)
    return m


@sonido(
    "premio",
    1.0,
    -18,
    "arpegio de campanillas FM (do-mi-sol-do) y acorde de metal de do mayor",
    1.1,
)
def _premio(r):
    m = Mezcla().arpegio(0, (72, 76, 79, 84), 0.075, 0.8, 0.5)
    m.acorde(0.3, (72, 76, 79), 0.65, 0.35)
    m.add(0.3, ext("casino-audio:chips-stack-2", 0.2), 0.5)
    m.add(0.45, ext("casino-audio:chips-handle-2", 0.2), 0.4)
    return m


@sonido(
    "granpremio",
    2.0,
    -16,
    "arpegio de siete campanillas, acorde de metal de cuatro notas, brillos y lluvia de tintineos",
    2.1,
)
def _granpremio(r):
    m = Mezcla().arpegio(0, (72, 76, 79, 84, 88, 91, 96), 0.07, 0.7, 0.6)
    m.acorde(0.5, (67, 72, 76, 79), 1.4, 0.5)
    m.arpegio(0.5, (84, 88, 91, 96), 0.0, 0.4, 1.2)
    m.lluvia(r, 0.5, 1.6, 12, [f"casino-audio:chips-handle-{i}" for i in (1, 3, 5)], 0.5, 0.2)
    m.add(0.5, ext("impact-sounds:impactBell_heavy_001", 1.2), 0.45)
    return m


@sonido(
    "mega",
    2.5,
    -15,
    "dos arpegios (do y re mayor), acorde de metal de cinco notas, golpe grave y lluvia de monedas",
    2.6,
)
def _mega(r):
    m = Mezcla().arpegio(0, (72, 76, 79, 84, 88, 91, 96), 0.06, 0.65, 0.5)
    m.arpegio(0.5, (74, 78, 81, 86, 90, 93, 98), 0.06, 0.7, 0.6)
    m.acorde(1.0, (62, 66, 69, 74, 78), 1.55, 0.55)
    m.arpegio(1.0, (86, 90, 93, 98), 0.0, 0.4, 1.4)
    m.add(1.0, 1.0 * golpe(0.4, 100, 40, 0.12))
    m.lluvia(r, 1.0, 2.2, 18, [f"casino-audio:chips-stack-{i}" for i in (1, 3, 5)], 0.55, 0.2)
    m.add(1.0, ext("impact-sounds:impactBell_heavy_002", 1.5), 0.5)
    return m


@sonido(
    "epico",
    3.5,
    -15,
    "subida de ruido y seno, bombo, tres acordes de metal, cascada de campanillas y monedas",
    3.6,
    fade_out=0.15,
)
def _epico(r):
    m = Mezcla()
    t = T(1.0)
    u = t / 1.0
    fase = 2 * np.pi * np.cumsum(150 * 8**u) / SR
    m.add(0, np.sin(fase) * u**1.5 * 0.7)
    m.add(0, filtro(ruido(r, 1.0), 2000, "hp") * u**2 * 0.35)
    m.add(1.0, 1.3 * golpe(0.6, 110, 36, 0.18))
    m.add(1.0, ext("impact-sounds:impactMetal_heavy_002", 0.5), 0.8)
    m.acorde(1.0, (60, 64, 67, 72), 0.4, 0.6)
    m.acorde(1.35, (62, 67, 71, 74), 0.4, 0.6)
    m.acorde(1.7, (64, 68, 71, 76, 83), 1.8, 0.65)
    m.arpegio(
        1.0, tuple(72 + n for n in (0, 4, 7, 12, 16, 19, 24, 28, 31, 36, 40, 43)), 0.05, 0.6, 0.8
    )
    m.arpegio(1.7, (88, 92, 95, 100), 0.0, 0.4, 1.6)
    m.lluvia(r, 1.0, 3.1, 30, [f"casino-audio:chips-stack-{i}" for i in (2, 4, 6)], 0.6, 0.25)
    m.add(1.0, ext("impact-sounds:impactBell_heavy_003", 1.2), 0.5)
    m.add(1.7, ext("impact-sounds:impactBell_heavy_004", 1.6), 0.45)
    return m


@sonido(
    "bote",
    5.0,
    -15,
    "acordes de metal, campanas FM que repican en bucle, golpes graves y lluvia densa de monedas",
    5.1,
    fade_out=0.25,
)
def _bote(r):
    m = Mezcla()
    m.acorde(0, (55, 60, 64, 67), 1.2, 0.5)
    m.acorde(2.4, (60, 64, 67, 72, 76), 2.6, 0.55)
    for i in range(23):
        m.add(
            0.1 + i * 0.16,
            campana(float(midi((84, 88, 91, 96, 91, 88)[i % 6])), 0.6, 2.5),
            0.55 * (1 - 0.4 * i / 23),
        )
    for t in (0.0, 2.4):
        m.add(t, 1.1 * golpe(0.5, 100, 38, 0.15))
    m.lluvia(
        r,
        0.0,
        4.6,
        70,
        [f"casino-audio:chips-{k}-{i}" for k in ("handle", "stack") for i in (1, 2, 3, 4)],
        0.6,
        0.25,
    )
    for t, k in (
        (0.0, "impactBell_heavy_004"),
        (1.2, "impactBell_heavy_000"),
        (2.4, "impactBell_heavy_001"),
    ):
        m.add(t, ext(f"impact-sounds:{k}", 1.4), 0.5)
    return m


@sonido(
    "gratis",
    1.5,
    -17.5,
    "carrera pentatónica de campanillas, brillo con trémolo de 12 Hz y acorde suave de metal",
    1.6,
    fade_out=0.08,
)
def _gratis(r):
    m = Mezcla().arpegio(0, (72, 74, 76, 79, 81, 84, 86, 88, 91, 93, 96, 98), 0.045, 0.6, 0.45, 2.0)
    m.arpegio(0.6, (84, 88, 91, 96), 0.0, 0.5, 0.9)
    m.acorde(0.55, (72, 76, 79, 84), 0.9, 0.3)
    t = T(0.9)
    m.add(
        0.6,
        np.sin(2 * np.pi * 3136 * t)
        * ad(t, 0.01, 0.35)
        * (0.6 + 0.4 * np.sin(2 * np.pi * 12 * t))
        * 0.2,
    )
    m.add(0, ext("interface-sounds:confirmation_003", 0.4), 0.4)
    m.add(0.55, ext("interface-sounds:glass_003", 0.5), 0.5)
    return m


@sonido(
    "auto", 0.2, -22, "dos pitidos de interfaz (784 y 1175 Hz) con caída suave", 0.25, fade_out=0.02
)
def _auto(r):
    m = Mezcla()
    for t0, f, d in ((0.0, 784, 0.07), (0.07, 1175, 0.12)):
        t = T(d)
        m.add(
            t0,
            ad(t, 0.003, d / 2.5) * (np.sin(2 * np.pi * f * t) + 0.25 * np.sin(4 * np.pi * f * t)),
        )
    m.add(0, ext("interface-sounds:toggle_002", 0.08), 0.5)
    return m


# ---------------------------------------------------------------- acabado y codificación


def db(x):
    return 20 * np.log10(max(x, 1e-9))


def acabar(x, rms_db, fin, fout):
    """Recorta, funde, comprime un poco el pico y deja el volumen objetivo con pico seguro."""
    x = recortar(x)
    x = np.tanh(1.6 * x / np.abs(x).max()) / np.tanh(1.6)  # suaviza los picos aislados
    x = x - x.mean()
    x = fundir(x, fin, fout)
    x *= 10 ** (rms_db / 20) / np.sqrt(np.mean(x**2))
    tope = 10 ** (PICO_PREVIO_DB / 20)
    if np.abs(x).max() > tope:
        x *= tope / np.abs(x).max()
    return x


def a_wav(x, ruta):
    with wave.open(str(ruta), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.round(x * 32767).astype("<i2").tobytes())


def a_mp3(wav, mp3):
    subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-i", str(wav), "-map_metadata", "-1", "-fflags", "+bitexact",
         "-flags", "+bitexact", "-ac", "1", "-ar", str(SR), "-c:a", "libmp3lame", "-b:a", BITRATE,
         "-write_xing", "0", str(mp3)],
        check=True,
    )  # fmt: skip


def medir(mp3):
    """Duración, pico y RMS (dBFS) del MP3 ya decodificado."""
    x = decodificar(mp3.read_bytes())
    return len(x) / SR, db(np.abs(x).max()), db(np.sqrt(np.mean(x**2)))


def escribir_leeme(capas):
    f = [
        "# Sonidos de las tragaperras de la plaza",
        "",
        "MP3 mono de 44,1 kHz (~80 kbps) que usa la página del Jovaniverso. Cada uno es una",
        "mezcla de capas sintetizadas con numpy y de capas de paquetes de Kenney",
        "(https://kenney.nl), todos con licencia CC0 (dominio público, sin atribución",
        f"obligatoria: {CC0}). Lo genera `docs/sonidos_tragas.py`; no se editan a mano.",
        "",
        "Regenerar (necesita red la primera vez, ffmpeg y numpy):",
        "",
        "    .venv/Scripts/python docs/sonidos_tragas.py",
        "",
        "Es determinista: semillas fijas y codificación bit a bit repetible.",
        "",
        "## Paquetes externos",
        "",
    ]
    for p, n in PAQUETES.items():
        f.append(f"- **{n}**, de Kenney Vleugels: https://kenney.nl/assets/{p} (licencia CC0).")
    f += ["", "## Archivos", ""]
    for nombre, info in SONIDOS.items():
        f.append(f"### `{nombre}.mp3`")
        f.append(f"- Sintetizado: {info['sint']}.")
        if capas[nombre]:
            f.append("- Externas (paquete, archivo original, URL, licencia):")
            for c in sorted(capas[nombre]):
                p, n = c.split(":")
                f.append(f"  - {PAQUETES[p]}, `Audio/{n}.ogg`, https://kenney.nl/assets/{p}, CC0.")
        else:
            f.append("- Externas: ninguna (solo sintetizado).")
        f.append("")
    (SALIDA / "LEEME.md").write_text("\n".join(f), encoding="utf-8")


def main():
    SALIDA.mkdir(parents=True, exist_ok=True)
    tmp = CACHE / "wav"
    tmp.mkdir(parents=True, exist_ok=True)
    capas, filas, fallos = {}, [], []
    for nombre, info in SONIDOS.items():
        _usadas.clear()
        mezcla = info["fn"](rng_de(nombre))
        capas[nombre] = set(_usadas)
        x = acabar(mezcla.render(info["dur"]), info["rms"], info["fin"], info["fout"])
        wav, mp3 = tmp / f"{nombre}.wav", SALIDA / f"{nombre}.mp3"
        a_wav(x, wav)
        a_mp3(wav, mp3)
        dur, pico, rms = medir(mp3)
        kb = mp3.stat().st_size / 1024
        filas.append((nombre, dur, pico, rms, kb, hashlib.sha1(mp3.read_bytes()).hexdigest()[:8]))
        if dur > info["max"] or pico > -0.9:
            fallos.append(
                f"{nombre}: duración {dur:.2f} s (máx {info['max']}), pico {pico:.2f} dBFS"
            )
    escribir_leeme(capas)
    print(f"{'archivo':16}{'dur s':>7}{'pico dB':>9}{'RMS dB':>9}{'KB':>7}  hash")
    for n, d, p, r, kb, h in filas:
        print(f"{n:16}{d:7.2f}{p:9.2f}{r:9.1f}{kb:7.1f}  {h}")
    total = sum(f[4] for f in filas)
    print(f"total {total:.1f} KB")
    if total > PESO_MAX_KB:
        fallos.append(f"peso total {total:.1f} KB > {PESO_MAX_KB}")
    if fallos:
        sys.exit("No cumple:\n" + "\n".join(fallos))


if __name__ == "__main__":
    main()
