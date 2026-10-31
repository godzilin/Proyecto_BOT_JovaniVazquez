"""Reglas del aspecto de Jovani: qué look toca y qué hay que cambiar en su perfil.

Un *look* es un conjunto de avatar, banner, bio y estados. Jovani cambia de look
según lo que pasa en el servidor (lunes de Hacienda, beernight, Carnaval, un dragón
muerto en `picar`…) y los administradores pueden fijar uno con `aspecto`.

Este módulo es lógica pura: no importa Discord ni toca la base de datos o el disco.
El cog `bot.cogs.aspecto` le pasa el estado y aplica lo que decide. Los looks de
serie viven en `BUILTIN_LOOKS`; sus `key` se guardan en la base y no se cambian nunca.

Límites de Discord: no publica cuántas veces se puede cambiar un avatar y pide no
fijar esos límites en el código, así que `MIN_PROFILE_SECONDS` no es un límite de
Discord, sino una decisión del proyecto para cambiar el perfil pocas veces. Si
Discord contesta que no, el cog respeta la espera con `blocked_until`. El límite de
la bio (`BIO_MAX`) sale de una fuente externa, no de la documentación oficial: si
Discord rechaza una bio, se ve en el panel de `aspecto` y el resto sigue funcionando.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

from bot import __version__
from bot.services.economy import format_amount

#: Caracteres de la bio del perfil por servidor (dato no oficial, ver arriba).
BIO_MAX = 190
#: Caracteres de un estado personalizado de Discord.
STATUS_MAX = 128
#: Caracteres del nombre de un look creado por un administrador.
NAME_MAX = 32
#: Looks propios por servidor, además de los de serie.
MAX_CUSTOM_LOOKS = 20
#: Horas que se puede fijar un look como mucho (30 días).
MAX_PIN_HOURS = 720

#: Tiempo mínimo entre dos cambios automáticos de perfil. Es una decisión del
#: proyecto (ver el docstring del módulo), no un límite publicado por Discord.
MIN_PROFILE_SECONDS = 1800
#: Espera tras un rechazo de Discord que no dice cuánto hay que esperar.
BLOCK_SECONDS = 3600
#: Hora canaria desde la que se repinta el banner y se renueva la bio de cada día.
REFRESH_HOUR = 6
#: Cuánto dura el look de 🦺 tras desplegar una versión nueva.
UPDATED_SECONDS = 6 * 3600
#: Cuánto dura el look de 🐉 tras matar al dragón de `picar`.
DRAGON_SECONDS = 24 * 3600

#: Variables que admiten una bio y un estado escritos por un administrador.
TEMPLATE_VARIABLES = frozenset({"version", "dato"})

_VARIABLE = re.compile(r"\{([^{}]*)\}")
_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}$")

#: Tipos de la telemetría de los que salen las cifras de uso.
USAGE_KINDS: tuple[str, ...] = (
    "slash",
    "text",
    "uso_mal",
    "render",
    "musica",
    "message",
    "arranque",
)

Color = tuple[int, int, int]


class AspectoError(ValueError):
    """Lo que ha pedido un administrador no se puede hacer; el texto se le enseña."""


@dataclass(frozen=True)
class Look:
    """Un look de Jovani.

    Attributes:
        key: Identificador estable; es lo que se guarda.
        name: Nombre con emoji, como sale en `aspecto` y en `jovani`.
        top: Color de arriba del degradado del banner.
        bottom: Color de abajo del degradado del banner.
        bios: Bios posibles; admiten `{version}` y `{dato}`.
        statuses: Estados propios del look, que se alternan con los datos.
    """

    key: str
    name: str
    top: Color
    bottom: Color
    bios: tuple[str, ...]
    statuses: tuple[str, ...]


@dataclass(frozen=True)
class LookOverride:
    """Lo que un administrador ha cambiado de un look, o un look propio entero.

    Attributes:
        key: Clave del look.
        name: Nombre del look propio; `None` en un look de serie.
        bio: Bio que sustituye a las de serie, o `None`.
        status: Estado que sustituye a los de serie, o `None`.
    """

    key: str
    name: str | None = None
    bio: str | None = None
    status: str | None = None


@dataclass(frozen=True)
class AspectoState:
    """Estado guardado de un servidor. Sin fila en la base, estos valores.

    Attributes:
        enabled: Si Jovani cambia su perfil en este servidor.
        pinned_key: Look fijado a mano, si lo hay.
        pinned_until: Hasta cuándo (epoch); `None` es hasta que se quite.
        dragon_until: Hasta cuándo dura el look de 🐉 (epoch).
        updated_until: Hasta cuándo dura el look de 🦺 (epoch).
        last_version: Última versión vista, para saber si hay una nueva.
        applied_key: Look puesto ahora en Discord.
        applied_avatar: Huella del avatar puesto; `None` es que no hay ninguno propio.
        profile_at: Último cambio de perfil (epoch).
        refreshed_day: Último día en que se repintaron banner y bio (`AAAA-MM-DD`).
        blocked_until: Hasta cuándo no se toca el perfil tras un rechazo (epoch).
        last_error: Último aviso de Discord, para enseñarlo en `aspecto`.
    """

    enabled: bool = True
    pinned_key: str | None = None
    pinned_until: float | None = None
    dragon_until: float = 0.0
    updated_until: float = 0.0
    last_version: str | None = None
    applied_key: str | None = None
    applied_avatar: str | None = None
    profile_at: float = 0.0
    refreshed_day: str | None = None
    blocked_until: float = 0.0
    last_error: str | None = None


@dataclass(frozen=True)
class ProfilePlan:
    """Qué hay que cambiar en el perfil. Vacío si no toca nada.

    Attributes:
        look_key: Look que debe quedar puesto.
        avatar: Si hay que mirar el avatar (solo se sube si ha cambiado).
        banner: Si hay que repintar y subir el banner.
        bio: Si hay que escribir la bio.
    """

    look_key: str
    avatar: bool = False
    banner: bool = False
    bio: bool = False

    @property
    def empty(self) -> bool:
        """Si no hay nada que cambiar."""
        return not (self.avatar or self.banner or self.bio)


@dataclass(frozen=True)
class WeekStats:
    """Cifras de uso de un periodo; todas son totales, sin datos de nadie.

    Attributes:
        commands: Comandos usados, con `/` y con `.`.
        mistyped: Comandos mal escritos.
        drawings: Imágenes y GIF dibujados.
        songs: Canciones escuchadas enteras.
        skipped: Canciones saltadas.
        messages: Mensajes en el servidor.
        top_command: Comando más usado, o `None`.
    """

    commands: int = 0
    mistyped: int = 0
    drawings: int = 0
    songs: int = 0
    skipped: int = 0
    messages: int = 0
    top_command: str | None = None


@dataclass(frozen=True)
class Snapshot:
    """Lo que se sabe del bot en un momento: la materia prima de bio, estado y ficha.

    Attributes:
        week: Cifras de los últimos 7 días.
        lifetime: Cifras desde que hay telemetría.
        house_net: Lo que ha ganado la casa esta semana (negativo si pierde).
        state_balance: Saldo de la cuenta del Estado.
        achievements: Logros desbloqueados en el servidor.
        dragons: Dragones matados en `picar`.
        restarts: Veces que ha arrancado el bot.
    """

    week: WeekStats = field(default_factory=WeekStats)
    lifetime: WeekStats = field(default_factory=WeekStats)
    house_net: int = 0
    state_balance: int = 0
    achievements: int = 0
    dragons: int = 0
    restarts: int = 0


# Las frases siguen la Biblia: la mayoría normales, las de Jovani espaciadas y ninguna
# con muletillas boricuas. Las claves de estos looks no se cambian nunca.
NORMAL = Look(
    key="normal",
    name="🎤 De diario",
    top=(28, 92, 168),
    bottom=(14, 32, 72),
    bios=(
        "Jovani Vázquez, versión {version}. {dato}",
        "Bot del servidor, versión {version}. {dato}",
        "Yo soy yo… y así me tienes que soportar. Versión {version}. {dato}",
    ),
    statuses=(
        "Mirando el servidor",
        "De guardia en el canal",
        "¿Con pulpa? ¡Sin pulpa!",
        "Haciendo café para todos",
    ),
)
PIJAMA = Look(
    key="pijama",
    name="🌙 Pijama",
    top=(38, 34, 88),
    bottom=(10, 10, 32),
    bios=(
        "Turno de noche, versión {version}. {dato}",
        "Duermo con un ojo abierto. Versión {version}. {dato}",
    ),
    statuses=(
        "Turno de noche",
        "Durmiendo con un ojo abierto",
        "Kebab de madrugada",
        "Quisiera ser una mosca… dormida",
    ),
)
CASINO = Look(
    key="casino",
    name="🎰 Noche de casino",
    top=(122, 24, 42),
    bottom=(36, 8, 18),
    bios=(
        "Noche de casino, versión {version}. {dato}",
        "Las mesas están abiertas. Versión {version}. {dato}",
    ),
    statuses=(
        "Repartiendo cartas",
        "Mesas abiertas",
        "La casa siempre gana, pero hoy invita",
        "Girando la ruleta",
    ),
)
HACIENDA = Look(
    key="hacienda",
    name="🧾 Lunes de Hacienda",
    top=(70, 84, 96),
    bottom=(24, 30, 36),
    bios=(
        "Hoy toca la Renta. Versión {version}. {dato}",
        "Lunes de Renta y Patrimonio. Versión {version}. {dato}",
    ),
    statuses=(
        "Repasando la Renta",
        "Calculando el Patrimonio",
        "Buscando el borrador",
        "La reunión podía ser un correo",
    ),
)
ACTUALIZADO = Look(
    key="actualizado",
    name="🦺 Recién actualizado",
    top=(214, 134, 20),
    bottom=(92, 48, 8),
    bios=(
        "Recién actualizado a la {version}. {dato}",
        "Estoy destruido, pero por fuera… por dentro estoy nuevo. Versión {version}.",
    ),
    statuses=(
        "Estrenando versión",
        "Recién salido del taller",
        "Probando lo nuevo",
        "Por dentro estoy nuevo",
    ),
)
BEERNIGHT = Look(
    key="beernight",
    name="🍻 Beernight",
    top=(196, 142, 26),
    bottom=(84, 50, 8),
    bios=(
        "Hay beernight, versión {version}. {dato}",
        "Noche de cervezas. Versión {version}. {dato}",
    ),
    statuses=(
        "En plena beernight",
        "Contando tragos",
        "Sirviendo otra ronda",
        "Víctor le dijo a Joel: móntate en mi barra",
    ),
)
DRAGON = Look(
    key="dragon",
    name="🐉 Matadragones",
    top=(92, 22, 120),
    bottom=(24, 6, 36),
    bios=(
        "Alguien ha matado al dragón de `picar`. Versión {version}. {dato}",
        "Hoy hay dragón en el menú. Versión {version}. {dato}",
    ),
    statuses=(
        "Han matado al dragón",
        "Limpiando escamas",
        "El End está en silencio",
        "Dracarys, pero en diferido",
    ),
)
CARNAVAL = Look(
    key="carnaval",
    name="🎭 Carnaval",
    top=(204, 36, 132),
    bottom=(56, 16, 112),
    bios=(
        "Es Carnaval. Versión {version}. {dato}",
        "Disfrazado de bot serio. Versión {version}. {dato}",
    ),
    statuses=(
        "Buscando el disfraz",
        "En la cabalgata",
        "Disfrazado de bot serio",
        "Es Carnaval y se nota",
    ),
)
CANARIAS = Look(
    key="canarias",
    name="🏝️ Día de Canarias",
    top=(20, 132, 196),
    bottom=(238, 196, 44),
    bios=(
        "Feliz Día de Canarias. Versión {version}. {dato}",
        "Hoy, gofio para todos. Versión {version}. {dato}",
    ),
    statuses=(
        "Feliz Día de Canarias",
        "Con gofio en la mochila",
        "Esperando la guagua",
        "¡Azul, azul, como el mar azul!",
    ),
)
HALLOWEEN = Look(
    key="halloween",
    name="🎃 Halloween",
    top=(226, 106, 16),
    bottom=(28, 12, 36),
    bios=(
        "Noche de Halloween. Versión {version}. {dato}",
        "Truco o trato. Versión {version}. {dato}",
    ),
    statuses=(
        "Truco o trato",
        "Tallando la calabaza",
        "Noche de Halloween",
        "Me ha asustado la declaración de la Renta",
    ),
)
NAVIDAD = Look(
    key="navidad",
    name="🎄 Navidad",
    top=(176, 28, 36),
    bottom=(16, 76, 44),
    bios=(
        "Felices fiestas. Versión {version}. {dato}",
        "Con el cuñado en la mesa. Versión {version}. {dato}",
    ),
    statuses=(
        "Felices fiestas",
        "Envolviendo regalos",
        "Sobreviviendo al cuñado",
        "Esperando el Gordo",
    ),
)
ANIVERSARIO = Look(
    key="aniversario",
    name="🎂 Aniversario",
    top=(232, 92, 132),
    bottom=(92, 28, 96),
    bios=(
        "Hoy cumple años el servidor. Versión {version}. {dato}",
        "Aniversario del servidor. Versión {version}. {dato}",
    ),
    statuses=(
        "Soplando las velas",
        "Aniversario del servidor",
        "Cortando la tarta",
        "Un año más y sin cerrar",
    ),
)

#: Looks de serie, en el orden en que salen en `aspecto`.
BUILTIN_LOOKS: tuple[Look, ...] = (
    NORMAL,
    PIJAMA,
    CASINO,
    HACIENDA,
    ACTUALIZADO,
    BEERNIGHT,
    DRAGON,
    CARNAVAL,
    CANARIAS,
    HALLOWEEN,
    NAVIDAD,
    ANIVERSARIO,
)
BUILTIN_BY_KEY: dict[str, Look] = {look.key: look for look in BUILTIN_LOOKS}


def easter(year: int) -> date:
    """Domingo de Pascua del calendario gregoriano (algoritmo anónimo de Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def is_carnival(day: date) -> bool:
    """Del Jueves Lardero al Domingo de Piñata.

    Cada isla celebra el suyo en fechas propias; esta es la semana común, ligada a
    la Pascua, para no tener que mantener un calendario a mano.
    """
    sunday = easter(day.year)
    return sunday - timedelta(days=52) <= day <= sunday - timedelta(days=42)


def holiday_look(day: date, anniversary: tuple[int, int] | None = None) -> Look | None:
    """El look de fiesta de un día, si lo hay.

    Args:
        day: Día en hora canaria.
        anniversary: `(mes, día)` en que se creó el servidor.
    """
    if anniversary == (day.month, day.day):
        return ANIVERSARIO
    if is_carnival(day):
        return CARNAVAL
    if (day.month, day.day) == (5, 30):
        return CANARIAS
    if (day.month, day.day) == (10, 31):
        return HALLOWEEN
    if (day.month, day.day) >= (12, 20) or (day.month, day.day) <= (1, 6):
        return NAVIDAD
    return None


def automatic_look(
    state: AspectoState,
    now: datetime,
    *,
    beernight: bool = False,
    anniversary: tuple[int, int] | None = None,
) -> Look:
    """El look que toca sin contar lo fijado a mano. Gana el primero que encaja.

    Args:
        state: Estado del servidor, con los eventos en marcha.
        now: Ahora, con zona horaria canaria.
        beernight: Si hay una beernight abierta en el servidor.
        anniversary: `(mes, día)` en que se creó el servidor.
    """
    moment = now.timestamp()
    if moment < state.dragon_until:
        return DRAGON
    if beernight:
        return BEERNIGHT
    if moment < state.updated_until:
        return ACTUALIZADO
    if (holiday := holiday_look(now.date(), anniversary)) is not None:
        return holiday
    if now.weekday() == 0:
        return HACIENDA
    if now.weekday() in (4, 5) and now.hour >= 20:
        return CASINO
    if 2 <= now.hour < 7:
        return PIJAMA
    return NORMAL


def pin_active(state: AspectoState, moment: float) -> bool:
    """Si hay un look fijado a mano que sigue en vigor."""
    if state.pinned_key is None:
        return False
    return state.pinned_until is None or moment < state.pinned_until


def desired_key(
    state: AspectoState,
    now: datetime,
    *,
    known: frozenset[str] | set[str],
    beernight: bool = False,
    anniversary: tuple[int, int] | None = None,
) -> str:
    """Clave del look que debe estar puesto, con lo fijado a mano por delante.

    Args:
        known: Claves que existen en el servidor. Un look fijado que se ha borrado
            no cuenta, por si la fila quedó de antes.
    """
    moment = now.timestamp()
    if pin_active(state, moment) and state.pinned_key in known:
        assert state.pinned_key is not None  # `pin_active` lo garantiza
        return state.pinned_key
    return automatic_look(state, now, beernight=beernight, anniversary=anniversary).key


def plan_profile(
    state: AspectoState, key: str, now: datetime, *, force: bool = False
) -> ProfilePlan:
    """Decide qué hay que cambiar para que el perfil quede con el look `key`.

    Cambiar de look toca las tres piezas, con `MIN_PROFILE_SECONDS` entre un cambio
    y el siguiente. El primer repaso del día, desde `REFRESH_HOUR`, renueva banner y
    bio para que sus cifras no se queden viejas.

    Args:
        force: Lo ha pedido un administrador: no espera `MIN_PROFILE_SECONDS`. Sí
            respeta `blocked_until`, porque ahí es Discord quien ha dicho que no.
    """
    moment = now.timestamp()
    if not state.enabled or moment < state.blocked_until:
        return ProfilePlan(key)
    if key != state.applied_key:
        if force or moment - state.profile_at >= MIN_PROFILE_SECONDS:
            return ProfilePlan(key, avatar=True, banner=True, bio=True)
        return ProfilePlan(key)
    if force:
        return ProfilePlan(key, avatar=True, banner=True, bio=True)
    if now.hour >= REFRESH_HOUR and state.refreshed_day != now.date().isoformat():
        return ProfilePlan(key, banner=True, bio=True)
    return ProfilePlan(key)


def seen_version(state: AspectoState, moment: float, version: str) -> AspectoState:
    """Apunta la versión en marcha y, si es nueva, arranca el look de 🦺.

    La primera vez no hay versión con la que comparar y no se arranca nada.
    """
    if state.last_version == version:
        return state
    until = moment + UPDATED_SECONDS if state.last_version is not None else state.updated_until
    return replace(state, last_version=version, updated_until=until)


def slugify(name: str) -> str:
    """Clave de un look propio a partir de su nombre.

    Solo letras sin tilde, cifras y guiones: la clave acaba en el nombre de un archivo,
    así que nunca se usa lo que escribe el administrador tal cual.

    Raises:
        AspectoError: Si del nombre no sale ninguna clave válida.
    """
    plain = name.lower().translate(str.maketrans("áéíóúüñ", "aeiouun"))
    key = re.sub(r"[^a-z0-9]+", "-", plain).strip("-")[:24].strip("-")
    if not valid_key(key):
        raise AspectoError("El nombre tiene que llevar alguna letra o cifra.")
    return key


def valid_key(key: str) -> bool:
    """Si `key` es segura como clave de look (y como parte del nombre de un archivo)."""
    return _KEY.fullmatch(key) is not None


def check_template(text: str, limit: int) -> str:
    """Valida una bio o un estado escrito por un administrador.

    Args:
        text: Lo escrito.
        limit: Caracteres máximos, con las variables sin sustituir.

    Returns:
        El texto sin espacios sobrantes.

    Raises:
        AspectoError: Si está vacío, es demasiado largo o usa una variable que no existe.
    """
    clean = " ".join(text.split())
    if not clean:
        raise AspectoError("El texto está vacío.")
    if len(clean) > limit:
        raise AspectoError(f"El texto pasa de {limit} caracteres.")
    unknown = sorted(set(_VARIABLE.findall(clean)) - TEMPLATE_VARIABLES)
    if unknown:
        allowed = ", ".join(f"{{{name}}}" for name in sorted(TEMPLATE_VARIABLES))
        raise AspectoError(f"La variable {{{unknown[0]}}} no existe. Se pueden usar: {allowed}.")
    return clean


def fill_template(text: str, *, version: str, fact: str) -> str:
    """Sustituye las variables. Sin `str.format`: el texto puede venir de un administrador."""
    values = {"version": version, "dato": fact}
    return _VARIABLE.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def clip(text: str, limit: int) -> str:
    """Recorta `text` a `limit` caracteres, con puntos suspensivos si hace falta."""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _count(value: int, one: str, many: str) -> str:
    """Una cifra con puntos de miles y su nombre en singular o en plural."""
    return f"{value:,}".replace(",", ".") + f" {one if value == 1 else many}"


def facts(snapshot: Snapshot) -> list[str]:
    """Datos curiosos del momento, en frases cortas y normales, sin cifras a cero.

    Solo salen totales del servidor, nunca quién ha hecho qué.
    """
    week = snapshot.week
    lines = []
    if week.commands:
        lines.append(f"⌨️ {_count(week.commands, 'comando', 'comandos')} esta semana")
    if week.top_command:
        lines.append(f"🏆 Comando de la semana: {week.top_command}")
    if week.mistyped:
        wrong = _count(week.mistyped, "comando mal escrito", "comandos mal escritos")
        lines.append(f"🤦 {wrong} esta semana")
    if week.drawings:
        lines.append(f"🖼️ {_count(week.drawings, 'dibujo', 'dibujos')} esta semana")
    if week.songs:
        heard = _count(week.songs, "canción escuchada", "canciones escuchadas")
        lines.append(f"🎵 {heard} esta semana")
    if week.skipped:
        skipped = _count(week.skipped, "canción saltada", "canciones saltadas")
        lines.append(f"⏭️ {skipped} esta semana")
    if snapshot.house_net:
        verb = "gana" if snapshot.house_net > 0 else "pierde"
        lines.append(f"🎰 La casa {verb} {format_amount(abs(snapshot.house_net))} esta semana")
    if snapshot.state_balance:
        lines.append(f"🏛️ El Estado guarda {format_amount(snapshot.state_balance)}")
    if snapshot.achievements:
        done = _count(snapshot.achievements, "logro desbloqueado", "logros desbloqueados")
        lines.append(f"🏅 {done}")
    if snapshot.dragons:
        lines.append(f"🐉 {_count(snapshot.dragons, 'dragón matado', 'dragones matados')}")
    if snapshot.restarts:
        lines.append(f"🔁 {_count(snapshot.restarts, 'reinicio', 'reinicios')}")
    return lines


def pick[T](options: Sequence[T], seed: int) -> T:
    """Una opción fija para cada `seed`, para que todo se repita y se pueda probar."""
    return options[seed % len(options)]


def bio_text(
    look: Look,
    override: LookOverride | None,
    snapshot: Snapshot,
    *,
    seed: int,
    version: str = __version__,
) -> str:
    """La bio del perfil para un look, ya rellena y dentro del límite.

    Args:
        seed: Cambia cada día para que no salga siempre la misma frase y el mismo dato.
    """
    template = override.bio if override and override.bio else pick(look.bios, seed)
    lines = facts(snapshot)
    fact = pick(lines, seed) if lines else "Aquí sigo."
    return clip(" ".join(fill_template(template, version=version, fact=fact).split()), BIO_MAX)


def status_lines(
    look: Look,
    override: LookOverride | None,
    snapshot: Snapshot,
    *,
    version: str = __version__,
) -> list[str]:
    """Estados que se van turnando: los del look, intercalados con los datos.

    Los datos son la mayoría: el estado informa y la broma va espaciada.
    """
    own = [override.status] if override and override.status else list(look.statuses)
    data = [f"v{version}", *facts(snapshot)]
    mixed: list[str] = []
    for index, line in enumerate(data):
        mixed.append(line)
        if index % 2 == 1:
            mixed.append(own[(index // 2) % len(own)])
    if len(mixed) < 2:
        mixed.extend(own)
    fact = pick(data, 0)
    return [
        clip(" ".join(fill_template(line, version=version, fact=fact).split()), STATUS_MAX)
        for line in mixed
    ]


def banner_lines(snapshot: Snapshot, *, seed: int, count: int = 3) -> list[str]:
    """Los datos que se dibujan en el banner; cambian cada día con `seed`."""
    lines = facts(snapshot)
    if len(lines) <= count:
        return lines
    start = seed % len(lines)
    return [lines[(start + index) % len(lines)] for index in range(count)]


def usage_totals(counts: Mapping[str, Mapping[str, int]]) -> dict[str, int]:
    """Convierte los contadores de la telemetría en las cifras que se acumulan.

    Args:
        counts: Eventos por tipo y nombre (`TelemetryRepository.usage_counts`).

    Returns:
        Totales con las claves de `WeekStats`, más `restarts`.
    """
    return {
        "commands": sum(counts.get("slash", {}).values()) + sum(counts.get("text", {}).values()),
        "mistyped": sum(counts.get("uso_mal", {}).values()),
        "drawings": sum(counts.get("render", {}).values()),
        "songs": counts.get("musica", {}).get("escuchada", 0),
        "skipped": counts.get("musica", {}).get("saltada", 0),
        "messages": sum(counts.get("message", {}).values()),
        "restarts": sum(counts.get("arranque", {}).values()),
    }


def top_command(counts: Mapping[str, Mapping[str, int]]) -> str | None:
    """Comando más usado sumando `/` y `.`; a igualdad, el primero por orden alfabético."""
    used = Counter(counts.get("slash", {}))
    used.update(counts.get("text", {}))
    return min(used, key=lambda name: (-used[name], name)) if used else None


def stats_from(totals: Mapping[str, int], *, top: str | None = None) -> WeekStats:
    """`WeekStats` a partir de unos totales de `usage_totals`."""
    return WeekStats(
        commands=totals.get("commands", 0),
        mistyped=totals.get("mistyped", 0),
        drawings=totals.get("drawings", 0),
        songs=totals.get("songs", 0),
        skipped=totals.get("skipped", 0),
        messages=totals.get("messages", 0),
        top_command=top,
    )


def custom_look(override: LookOverride) -> Look:
    """El `Look` de uno creado por un administrador, con los colores del de diario."""
    return replace(NORMAL, key=override.key, name=override.name or override.key)
