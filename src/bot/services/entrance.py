"""Reglas de los sonidos de entrada (`entrada`): ranuras, mejoras, efectos y modos.

Aquí no hay Discord ni disco: solo datos y funciones puras que usan el cog
(`bot.cogs.entrance`) y el almacén (`bot.repositories.entrance_sounds`).

Cada miembro tiene una ficha (`Profile`) con lo que ha comprado y cómo quiere
que suenen sus entradas, y una ranura (`Slot`) por sonido guardado:

- Ranuras de entrada, de la 1 a la 7. La primera es gratis; el resto se compran
  en orden (`SLOT_PRICES`).
- Duración máxima, de 3 s a 5 s en tramos de medio segundo (`LENGTH_PRICES`).
- La ranura de salida (`EXIT_SLOT`), que suena al irte de voz. Se compra aparte.

Las compras son un sumidero de dinero con IGIC al tipo general (ver
`Entrance.buy` en el cog): una prestación de servicios hecha en Canarias a
título oneroso.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from enum import StrEnum

#: Ranura que suena al salir de voz. Las de entrada van de 1 a `MAX_SLOTS`.
EXIT_SLOT = 0
#: Ranuras de entrada como máximo.
MAX_SLOTS = 7
#: Ranuras de entrada que tiene todo el mundo sin pagar.
FREE_SLOTS = 1

#: Duración máxima de serie, en décimas de segundo (3 s).
BASE_TENTHS = 30
#: Tope de la duración que se puede comprar (5 s).
MAX_TENTHS = 50
#: Cada mejora de duración suma medio segundo.
TENTHS_STEP = 5

#: Precio base (sin IGIC) de cada ranura de entrada, por su número. Una partida
#: del casino son 100 Y$ y el IMV máximo, 1.500 al día: las siete juntas son
#: unas 90 jornadas de IMV, un capricho caro a propósito.
SLOT_PRICES: dict[int, int] = {
    2: 5_000,
    3: 10_000,
    4: 16_000,
    5: 24_000,
    6: 34_000,
    7: 50_000,
}
#: Precio base de cada tramo de duración, por la duración que deja (en décimas).
LENGTH_PRICES: dict[int, int] = {
    35: 4_000,
    40: 8_000,
    45: 14_000,
    50: 24_000,
}
#: Precio base de la ranura de salida.
EXIT_PRICE = 20_000

#: Lo que se guarda del audio subido, a partir del segundo elegido. Da margen
#: para recortar otra vez o alargar el sonido al comprar más duración sin
#: volver a subirlo, y ocupa unos 480 KB por ranura.
SOURCE_SECONDS = 30.0

MIN_NAME = 1
MAX_NAME = 32
#: Bloques de la onda que se dibuja en texto en el panel.
WAVE_WIDTH = 16
_WAVE_BLOCKS = "▁▂▃▄▅▆▇█"


class Upgrade(StrEnum):
    """Lo que se compra en el panel. El valor es estable (libro y logros)."""

    SLOT = "ranura"
    LENGTH = "duracion"
    EXIT = "salida"


class Mode(StrEnum):
    """Qué ranura suena al entrar. El valor es estable (se guarda)."""

    FIXED = "fijo"
    RANDOM = "azar"
    CYCLE = "turnos"


MODE_LABELS: dict[Mode, str] = {
    Mode.FIXED: "📌 Siempre la misma",
    Mode.RANDOM: "🎲 Al azar",
    Mode.CYCLE: "🔁 Por turnos",
}


class EntranceError(Exception):
    """Algo que el miembro no puede hacer; el mensaje es apto para enseñárselo."""


@dataclass(frozen=True, slots=True)
class Effect:
    """Un efecto que se aplica al guardar el sonido (no cuesta nada al sonar).

    Attributes:
        key: Clave estable (se guarda y la cuentan los logros).
        emoji: Icono del panel.
        label: Nombre que ve el miembro.
        filter: Filtro de `ffmpeg` (vacío para dejarlo tal cual).
        speed: Cuánto acelera el audio. Para que el clip dure lo pedido se
            recorta `duración × speed` del original antes de aplicarlo.
    """

    key: str
    emoji: str
    label: str
    filter: str
    speed: float = 1.0


EFFECTS: tuple[Effect, ...] = (
    Effect("normal", "🎵", "Sin efecto", ""),
    Effect("eco", "🏔️", "Eco", "aecho=0.8:0.85:180:0.35"),
    Effect("catedral", "⛪", "Catedral", "aecho=0.8:0.88:120|260|420:0.4|0.3|0.2"),
    Effect(
        "robot",
        "🤖",
        "Robot",
        "afftfilt=real='hypot(re,im)*sin(0)':imag='hypot(re,im)*cos(0)':win_size=512:overlap=0.75",
    ),
    Effect("ardilla", "🐿️", "Ardilla", "asetrate=67200,aresample=48000", speed=1.4),
    Effect("grave", "🐻", "Grave", "asetrate=36000,aresample=48000", speed=0.75),
    Effect("radio", "📻", "Radio vieja", "highpass=f=400,lowpass=f=3000,acrusher=bits=10:mix=0.3"),
    Effect("alreves", "🔄", "Al revés", "areverse"),
)
EFFECTS_BY_KEY: dict[str, Effect] = {e.key: e for e in EFFECTS}
DEFAULT_EFFECT = EFFECTS[0]


def effect_of(key: str) -> Effect:
    """El efecto de una clave guardada; si ya no existe, sin efecto."""
    return EFFECTS_BY_KEY.get(key, DEFAULT_EFFECT)


@dataclass(frozen=True, slots=True)
class Slot:
    """Un sonido guardado en una ranura.

    Attributes:
        slot: Número de la ranura (`EXIT_SLOT` para la salida).
        name: Nombre que le pone su dueño.
        volume: Volumen en %.
        effect: Clave del efecto (`EFFECTS`).
        fade: Si entra y sale con fundido.
        start_ms: Dónde empieza el clip dentro de la fuente guardada.
        length_ms: Duración pedida; el clip dura esto o menos si la fuente se acaba.
        source_ms: Lo que dura la fuente guardada (hasta `SOURCE_SECONDS`).
        clip_ms: Lo que dura el clip final de verdad.
        wave: Onda en texto (`wave_text`).
        plays: Veces que ha sonado.
    """

    slot: int
    name: str
    volume: int
    effect: str
    fade: bool
    start_ms: int
    length_ms: int
    source_ms: int
    clip_ms: int
    wave: str
    plays: int = 0

    @property
    def is_exit(self) -> bool:
        return self.slot == EXIT_SLOT


@dataclass(frozen=True, slots=True)
class Profile:
    """Lo que tiene un miembro: mejoras compradas, modo y sonidos.

    Attributes:
        slots: Ranuras de entrada compradas (contando la gratis).
        max_tenths: Duración máxima en décimas de segundo.
        exit_owned: Si ha comprado la ranura de salida.
        mode: Qué ranura suena al entrar.
        active: La ranura que suena en modo fijo.
        sounds: Ranuras con sonido, por número.
    """

    slots: int = FREE_SLOTS
    max_tenths: int = BASE_TENTHS
    exit_owned: bool = False
    mode: Mode = Mode.FIXED
    active: int = 1
    sounds: dict[int, Slot] = field(default_factory=dict)

    @property
    def max_ms(self) -> int:
        return self.max_tenths * 100

    @property
    def entrances(self) -> list[Slot]:
        """Los sonidos de entrada, en orden de ranura."""
        return [s for n, s in sorted(self.sounds.items()) if n != EXIT_SLOT]

    @property
    def exit(self) -> Slot | None:
        return self.sounds.get(EXIT_SLOT) if self.exit_owned else None

    @property
    def empty(self) -> bool:
        """Sin nada que guardar: ni sonidos ni compras ni ajustes."""
        return self == Profile()

    def with_sound(self, sound: Slot) -> Profile:
        return replace(self, sounds={**self.sounds, sound.slot: sound})

    def without(self, slot: int) -> Profile:
        sounds = {n: s for n, s in self.sounds.items() if n != slot}
        return replace(self, sounds=sounds)

    def free_slot(self) -> int | None:
        """La primera ranura de entrada comprada y vacía, o `None`."""
        return next((n for n in range(1, self.slots + 1) if n not in self.sounds), None)

    def default_slot(self) -> int:
        """Dónde va un audio subido con `/entrada archivo:` sin elegir ranura.

        La activa si es la única que se tiene; si no, la primera vacía; y si
        están todas llenas, la activa (que se reemplaza).
        """
        if self.slots == 1:
            return 1
        free = self.free_slot()
        return free if free is not None else self.active


# -- Compras ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Offer:
    """La siguiente compra posible de un tipo.

    Attributes:
        upgrade: Qué se compra.
        level: El valor que queda tras comprarla (número de ranuras, décimas o 1).
        base: Precio sin IGIC.
    """

    upgrade: Upgrade
    level: int
    base: int


def next_offer(profile: Profile, upgrade: Upgrade) -> Offer | None:
    """La siguiente mejora de ese tipo que se puede comprar, o `None` si está al máximo."""
    if upgrade is Upgrade.SLOT:
        level = profile.slots + 1
        return Offer(upgrade, level, SLOT_PRICES[level]) if level in SLOT_PRICES else None
    if upgrade is Upgrade.LENGTH:
        level = profile.max_tenths + TENTHS_STEP
        return Offer(upgrade, level, LENGTH_PRICES[level]) if level in LENGTH_PRICES else None
    return None if profile.exit_owned else Offer(upgrade, 1, EXIT_PRICE)


def current_level(profile: Profile, upgrade: Upgrade) -> int:
    """El valor que tiene ahora de una mejora (el que comprueba la compra)."""
    if upgrade is Upgrade.SLOT:
        return profile.slots
    if upgrade is Upgrade.LENGTH:
        return profile.max_tenths
    return int(profile.exit_owned)


def apply_offer(profile: Profile, offer: Offer) -> Profile:
    """La ficha tras comprar `offer`.

    Raises:
        EntranceError: Si la ficha ya no está donde se calculó la oferta (otra
            compra en otro panel entre medias): no se cobra.
    """
    if next_offer(profile, offer.upgrade) != offer:
        raise EntranceError("Tus mejoras han cambiado mientras mirabas. Abre el panel otra vez.")
    if offer.upgrade is Upgrade.SLOT:
        return replace(profile, slots=offer.level)
    if offer.upgrade is Upgrade.LENGTH:
        return replace(profile, max_tenths=offer.level)
    return replace(profile, exit_owned=True)


def offer_label(offer: Offer) -> str:
    """Qué se compra, en pocas palabras («Ranura 3», «Hasta 4,5 s»)."""
    if offer.upgrade is Upgrade.SLOT:
        return f"Ranura {offer.level}"
    if offer.upgrade is Upgrade.LENGTH:
        return f"Hasta {seconds_text(offer.level * 100)}"
    return "Sonido de salida"


def total_spent(profile: Profile) -> int:
    """Lo que lleva gastado en mejoras (sin IGIC), para el panel y los logros."""
    slots = sum(p for n, p in SLOT_PRICES.items() if n <= profile.slots)
    lengths = sum(p for n, p in LENGTH_PRICES.items() if n <= profile.max_tenths)
    return slots + lengths + (EXIT_PRICE if profile.exit_owned else 0)


# -- Qué suena -------------------------------------------------------------------------


def pick(profile: Profile, last: int | None, rng: random.Random) -> Slot | None:
    """La ranura de entrada que suena ahora, según el modo.

    Args:
        last: La que sonó la vez anterior (en memoria), para no repetir al azar
            y para seguir el turno.
    """
    sounds = profile.entrances
    if not sounds:
        return None
    if profile.mode is Mode.RANDOM:
        pool = [s for s in sounds if s.slot != last] if len(sounds) > 1 else sounds
        return rng.choice(pool)
    if profile.mode is Mode.CYCLE:
        if last is None:
            return sounds[0]
        return next((s for s in sounds if s.slot > last), sounds[0])
    return profile.sounds.get(profile.active) or sounds[0]


def next_mode(mode: Mode) -> Mode:
    """El modo siguiente, para el botón que los va rotando."""
    modes = list(Mode)
    return modes[(modes.index(mode) + 1) % len(modes)]


# -- Textos y onda ---------------------------------------------------------------------


def seconds_text(ms: int) -> str:
    """`3500` → «3,5 s»; `3000` → «3 s»."""
    seconds = round(ms / 1000, 1)
    text = f"{seconds:.1f}".rstrip("0").rstrip(".")
    return f"{text.replace('.', ',')} s"


def parse_seconds(text: str | None, *, default: float | None = None) -> float | None:
    """Lee un instante escrito por el miembro: «12», «12,5», «1:05» o «1:05.5».

    Raises:
        EntranceError: Si no es un número de segundos válido.
    """
    if text is None or not text.strip():
        return default
    cleaned = text.strip().lower().removesuffix("s").strip().replace(",", ".")
    minutes = 0
    if ":" in cleaned:
        head, _, cleaned = cleaned.partition(":")
        if not head.isdigit():
            raise EntranceError(f"No entiendo «{text.strip()}». Escribe los segundos: 12 o 1:05.")
        minutes = int(head)
    try:
        seconds = float(cleaned)
    except ValueError:
        raise EntranceError(
            f"No entiendo «{text.strip()}». Escribe los segundos: 12 o 1:05."
        ) from None
    if seconds < 0 or seconds != seconds or (minutes and seconds >= 60):
        raise EntranceError(f"No entiendo «{text.strip()}». Escribe los segundos: 12 o 1:05.")
    return minutes * 60 + seconds


def clean_name(text: str | None, fallback: str) -> str:
    """El nombre de una ranura sin saltos de línea ni marcas raras, o `fallback`."""
    if text is None:
        return fallback
    name = " ".join(text.replace("`", "'").split())[:MAX_NAME]
    return name if len(name) >= MIN_NAME else fallback


def wave_text(packets: list[bytes], width: int = WAVE_WIDTH) -> str:
    """Una onda en bloques (`▁▃▆█▆▃▁`) sacada del tamaño de los paquetes Opus.

    Opus a tasa variable gasta más bytes donde hay más sonido y casi nada en
    los silencios, así que el tamaño de cada paquete sirve de volumen
    aproximado sin decodificar el audio ni lanzar `ffmpeg`. No es una onda
    exacta: es para que cada ranura se reconozca de un vistazo.
    """
    if not packets:
        return ""
    sizes = [len(p) for p in packets]
    chunk = max(1, len(sizes) // width)
    levels = [max(sizes[i : i + chunk]) for i in range(0, len(sizes), chunk)][:width]
    low, high = min(levels), max(levels)
    span = (high - low) or 1
    top = len(_WAVE_BLOCKS) - 1
    return "".join(_WAVE_BLOCKS[round((v - low) / span * top)] for v in levels)
