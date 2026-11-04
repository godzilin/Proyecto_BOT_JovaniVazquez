"""Botes: máquinas de monedas y recogida: el Volcán, la Mina y el Olimpo (`tragas volcan`).

No tiene comando propio: se abre desde `tragas`, que junta todas las máquinas
(`bot.cogs.slots` llama a `open_theme`). Antes era `volcan`.

Las reglas y los números viven en `bot.services.hold_win` (`THEMES` dice qué
máquinas hay) y el dibujo en `bot.services.hold_win_scene` (una escena de Node con
tensión, como la tragaperras clásica; si Node y el navegador fallan, cae al Pillow de
`bot.services.hold_win_render`). Cada jugador abre su propia máquina, un mensaje con
botones que solo él puede pulsar.

**Juego base.** Botones:

- 🎰 **Tirar**: cobra, gira y paga los ways y la recogida.
- 🔁 **Auto ×10**: diez tiradas sin animación y un resumen. Para si sale un
  bonus, si no llega el dinero o si la banca no puede pagar.
- ⚡ **Turbo**: sin GIF, solo la imagen final (más rápido y casi sin datos).
- **½**, **×2**, 💰 **All-in** cambian la apuesta; 📋 **Premios**, la tabla.

**Bonus.** Cuando un maletín se llena, la máquina pasa al bonus con una entrada
animada (los maletines destellan, la rejilla se vacía, caen las monedas del bonus y sale
su nombre): 🎰 **Girar** juega una tirada (el contador vuelve a 3 cada vez que cae
algo) y ⏩ **Auto** juega todas las que queden seguidas. Si la máquina se cierra o el
bot se apaga con un bonus a medias, se juega solo hasta el final y se paga: nadie
pierde un bonus.

**Se pinta antes del clic** (`HoldWinView.prepare`, como `SlotMachineView` en la
clásica): la próxima tirada ya está sorteada (`_upcoming`, solo en memoria y nunca a la
vista) y mientras se ve la jugada actual se pintan, de una en una y de la más probable
a la que menos (`_plans`), las imágenes de cada botón que se puede pulsar:

- En el juego base: 🎰 Tirar a la apuesta de ahora, Tirar con la de ½, ×2 y All-in (las
  monedas y los botes llevan su valor en Y$, así que la imagen depende de la apuesta) y
  la imagen final de un 🔁 Auto ×10 (sus diez tiradas están sorteadas y los maletines se
  llenan tirada a tirada sobre una copia).
- En el bonus: la próxima tirada, jugada ya sobre una copia del bonus. Al pulsar 🎰 Girar
  (o en cada paso de ⏩ Auto) esa copia pasa a ser el bonus, así lo pintado y lo jugado son
  lo mismo.

Una imagen pintada vale si coincide todo lo que la decide (`hold_win_scene.base_key` y
`bonus_key`: la rejilla, la apuesta, los botes y maletines de antes y de después, el
turbo): si no (otra máquina del mismo jugador movió los maletines, por ejemplo), se
pinta en el momento. La entrada al bonus no se puede pintar antes (depende del bonus que
sale): empieza a pintarse al jugar la tirada que llena el maletín, mientras se ve su GIF.

**Dinero e impuestos.** Cada tirada base es una apuesta del casino
(`EconomyService.settle_bet`) y el bonus se paga al acabar con
`EconomyService.pay_winnings`. Tratamiento fiscal: juego, como la ruleta y la
tragaperras. Los premios de máquinas y casinos son ganancia patrimonial de la
base general (art. 33.1 de la Ley 35/2006, del IRPF) y las pérdidas solo
compensan ganancias de juego del mismo periodo (art. 33.5.d): por eso todo
entra en la retención diaria del casino y en la renta semanal. Los botes
también, porque el gravamen especial del 20 % (disposición adicional 33ª) es
solo para loterías del Estado, ONCE y Cruz Roja. El bonus no cobra apuesta: lo
apostado ya se cobró en las tiradas que llenaron el maletín.

Las máquinas solo se abren en los canales del casino que elige `garito` (sin elegir, los de
`CASINO_CHANNEL_IDS`) y en sus hilos. Permisos del bot en el canal: enviar mensajes, insertar
enlaces (embeds) y adjuntar archivos.
"""

from __future__ import annotations

import asyncio
import copy
import io
import logging
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs.casino import casino_channel_error, insufficient_text
from bot.services.achievements import (
    StatDelta,
    casino_stats,
    hold_win_bonus_stats,
    hold_win_stats,
)
from bot.services.casino_channels import CasinoChannelIds
from bot.services.economy import (
    BalanceLimitError,
    BetSettlement,
    EconomyService,
    InsufficientFundsError,
    format_amount,
    gambling_tax_line,
    parse_amount,
)
from bot.services.hold_win import (
    CASE_SIZE,
    COIN_VALUES,
    GRAND,
    GRAND_BONUS_ODDS,
    MAJOR_BASE,
    MAJOR_COINS,
    MAJOR_MAX,
    MIN_STAKE,
    MINI_BASE,
    MINI_COINS,
    MINI_MAX,
    RESET_VALUE,
    THEMES,
    WAYS_PAYS,
    BaseSpin,
    BonusGame,
    BonusKind,
    BonusResult,
    BonusStep,
    Cell,
    Meters,
    Theme,
    Tier,
    Trigger,
    fill_cases,
    format_multiplier,
    reset_won_jackpots,
    spin_base,
    to_amount,
    win_tier,
)
from bot.services.hold_win_render import Banner, BonusPanel, Media, Panel
from bot.services.hold_win_scene import (
    BonusFinish,
    HoldWinScene,
    base_key,
    bonus_key,
    meta_state,
    shows_slip,
    tease_hit,
    tease_of,
)
from bot.services.levels import TIMEZONE
from bot.services.pets import bet_moment
from bot.services.taxes import TAX_COLLECTOR
from bot.services.telemetry import TimedLock
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit, notify

if TYPE_CHECKING:
    from bot.app import BotClient
    from bot.repositories.hold_win import HoldWinRepository

logger = logging.getLogger(__name__)

#: Apuesta por defecto al abrir la máquina sin indicar cantidad.
DEFAULT_STAKE = 100
#: Segundos sin pulsar nada tras los que la máquina se cierra.
MACHINE_TIMEOUT = 180
#: Margen tras la animación: el cliente tarda un poco en empezar el GIF.
REVEAL_MARGIN_SECONDS = 0.4
#: Y un poco más cuanto más pesa el GIF (por MB), para que un móvil lento no se pierda el
#: final: el último fotograma es igual que el PNG, así que esperar de más no se ve.
REVEAL_SECONDS_PER_MB = 0.4
#: Pausa entre tiradas del ⏩ Auto del bonus en turbo (sin GIF que esperar).
BONUS_AUTO_PAUSE = 1.0
#: Segundos que se ve la tirada que llena el maletín antes de la entrada al bonus.
BONUS_INTRO_PAUSE = 1.5
AUTO_SPINS = 10
#: A partir de cuántas veces la apuesta se anuncia el premio en el canal.
SHOUT_MULTIPLIER = 50

GIF_NAME = "botes.gif"
PNG_NAME = "botes.png"

COLOR_IDLE = discord.Color.from_rgb(43, 45, 49)
COLOR_SPIN = discord.Color.from_rgb(222, 178, 70)
COLOR_WIN = discord.Color.from_rgb(255, 196, 0)
COLOR_LOSS = discord.Color.from_rgb(80, 84, 92)
COLOR_BONUS = discord.Color.from_rgb(170, 60, 255)

TIER_SHAPES = {Tier.GREEN: "🟢 verde", Tier.BLUE: "🔷 azul", Tier.RED: "⭐ roja"}
BONUS_TIER = {BonusKind.GREEN: Tier.GREEN, BonusKind.BLUE: Tier.BLUE, BonusKind.RED: Tier.RED}

# -- Textos en el tono de Jovani Vázquez ---------------------------------------------------

WIN_TITLES = {1: "¡GRAN PREMIO!", 2: "¡MEGA PREMIO!", 3: "¡PREMIO ÉPICO!"}
WIN_LINES = (
    "¡Eso es!",
    "¡Cobras!",
    "¡Tilín, tilín!",
    "¡Toma mangó!",
    "¡American people want mango!",
    "¡Monedas como en la cuenta de Bárcenas!",
    "¡Lluvia de monedas!",
    "¡Al maletín!",
    "¡La máquina suelta la pasta!",
    "¿Con pulpa? ¡Sin pulpa! ¡Con monedas!",
    "Monedas para todos.",
    "¡Premio!",
)
COLLECT_LINES = {
    "volcan": (
        "🌋 ¡El volcán escupe dinero!",
        "🌋 ¡Erupción de monedas!",
        "🌋 ¡El Teide se pone celoso!",
        "🌋 ¡Erupción fiscal, y esta vez a tu favor!",
    ),
    "mina": (
        "🚃 ¡La vagoneta sale cargada de oro!",
        "🚃 ¡Filón!",
        "🚃 ¡Se recoge el litio antes de que llegue la multinacional!",
        "🚃 ¡La vagoneta vuelve a rodar!",
        "🚃 ¡Pico, pala y monedas!",
    ),
    "olimpo": (
        "⚡ ¡Zeus recoge las monedas con el rayo!",
        "⚡ ¡Rescate griego, pero a tu favor!",
        "⚡ ¡Monedas del Olimpo!",
        "⚡ ¡Los dioses están de tu parte!",
        "⚡ ¡Esto la troika no lo vio venir!",
    ),
}
NEAR_MISS_LINES = (
    "Tanta moneda y sin nadie que la recoja…",
    "Las monedas ahí tiradas. Al maletín van.",
    "Monedas sin recoger, como los fondos europeos sin ejecutar.",
    "Monedas en la mesa, ahí se quedan.",
    "Faltó el recogedor.",
    "Mucha moneda suelta y ningún recogedor.",
)
#: Lo que se ve con animación (`bot.services.hold_win_scene`): el recogedor que llega al
#: quinto rodillo tras la espera, el que pasa de largo y el cinco de fila que se hace esperar.
RESCUE_LINES = (
    "🛟 ¡Rescate en el último rodillo!",
    "🛟 ¡Llega in extremis!",
    "🛟 ¡Rescatado! Y sin hombres de negro en Moncloa.",
    "🛟 ¡Al rescate, como la UME!",
    "🛟 ¡Llegó! Más puntual que Rodalies, que tampoco es mucho decir.",
)
SLIP_LINES = (
    "Pasó de largo.",
    "Ni paró.",
    "Visto y no visto.",
    "Se fue sin frenar, como la factura de la luz.",
    "Pasó de largo, como el AVE por Extremadura.",
)
FIVE_WAIT_LINES = (
    "¡Llega al quinto! Costó, pero llegó.",
    "¡Cinco de fila tras la espera! Al quinto pino y de vuelta.",
)
#: En el bonus: algo cae en la última tirada, o cae la moneda que faltaba para un bote.
LAST_SAVE_LINES = (
    "¡Salvada en la última!",
    "¡En el descuento, como el gol de Ramos en Lisboa!",
    "¡Prórroga, y a seguir!",
    "¡Salvada por la campana!",
)
JACKPOT_GOALS = ((MINI_COINS, "MINI"), (MAJOR_COINS, "MAJOR"), (20, "GRAND"))
LOSS_LINES = (
    "Ni una chispa. Otra.",
    "Calentando…",
    "Como el tren de Canarias: ni está ni se le espera.",
    "La máquina está en huelga de celo.",
    "Hazme café, que esto no arranca. ¡Quiero café!",
    "Nada por aquí.",
    "Estoy destruido, pero por fuera… por dentro estoy nuevo.",
)
#: Frases de una tirada sin premio que solo tienen gracia en su máquina.
THEME_LOSS_LINES = {
    "volcan": (
        "El volcán duerme la siesta.",
        "La lava no sale hoy.",
    ),
    "mina": (
        "Pura roca. Ni una pepita.",
        "Mina cerrada por Bruselas: cero monedas.",
        "Aquí el litio se lo lleva otro, y a ti te toca la piedra.",
        "Picar, picar y nada.",
    ),
    "olimpo": (
        "Zeus se ha ido a por otro rescate.",
        "Ni un rayo. Otra.",
        "Los dioses están de siesta.",
        "Ha pasado la troika: recortes y cero monedas.",
        "Mucho estadio olímpico y ningún premio.",
    ),
}
BONUS_INTRO = {
    "volcan": "La montaña ruge. Caen monedas y se quedan pegadas a la roca.",
    "mina": "Baja la jaula y se oye picar. Caen pepitas y se quedan en la roca, "
    "no como el litio, que no se queda en el pueblo.",
    "olimpo": "Truenan los cielos. Zeus reparte monedas y la troika mira desde abajo "
    "sin atreverse a pedirlas.",
}


def bonus_name(theme: Theme, kind: str) -> str:
    """`Bonus Lava` o el nombre del gran bonus (`¡ERUPCIÓN!`)."""
    name = theme.bonus_names[kind]
    return name if kind == BonusKind.GRAND else f"Bonus {name}"


@dataclass(frozen=True, slots=True)
class BasePlay:
    """Una tirada base ya cobrada y pagada.

    Attributes:
        payout: Lo cobrado en Y$ (ways más recogida).
        meters: Maletines y botes tras la tirada.
        before: Botes y maletines que se veían antes de la tirada (a la apuesta).
        trigger: Bonus que se dispara, si alguno.
        bonus: Partida de bonus ya empezada, si se dispara.
    """

    spin: BaseSpin
    stake: int
    payout: int
    settlement: BetSettlement
    meters: Meters
    before: Panel
    trigger: Trigger | None
    bonus: BonusGame | None
    media: Media
    session_spins: int

    @property
    def net(self) -> int:
        """Ganancia (positiva) o pérdida (negativa)."""
        return self.payout - self.stake

    @property
    def balance(self) -> int:
        """Saldo tras la tirada, con el IRPF ajustado."""
        return self.settlement.balance


@dataclass(frozen=True, slots=True)
class BonusPayout:
    """Un bonus terminado y pagado."""

    result: BonusResult
    stake: int
    amount: int
    settlement: BetSettlement
    kind: str
    steps: tuple[BonusStep, ...]


def base_panel(meters: Meters, stake: int) -> Panel:
    """Botes y maletines del juego base, en Y$ a la apuesta actual."""
    return Panel(
        mini=to_amount(meters.mini, stake),
        major=to_amount(meters.major, stake),
        grand=to_amount(GRAND, stake),
        cases=tuple((meters.cases[t].coins, CASE_SIZE[t]) for t in Tier),
    )


def bonus_panel(theme: Theme, game: BonusGame) -> BonusPanel:
    """Marcadores del bonus."""
    return BonusPanel(
        name=bonus_name(theme, game.kind),
        mini=to_amount(game.mini, game.stake),
        major=to_amount(game.major, game.stake),
        grand=to_amount(GRAND, game.stake),
        respins_left=game.respins_left,
        reset_value=game.reset_value,
        multiplier=game.multiplier,
        coins=game.coins,
        won=game.jackpots(),
        maximized=game.maximized,
    )


def base_banner(spin: BaseSpin, payout: int) -> Banner | None:
    """Cartel de la tirada: solo si paga."""
    if payout <= 0:
        return None
    tier = win_tier(spin.points)
    return Banner(WIN_TITLES.get(tier), f"+{format_amount(payout)}", tier)


def bonus_banner(result: BonusResult, amount: int) -> Banner:
    """Cartel del último fotograma de un bonus que acaba (para el Pillow de reserva)."""
    tier = win_tier(result.points)
    return Banner(
        WIN_TITLES.get(tier, "¡BONUS COBRADO!"), f"+{format_amount(amount)}", max(1, tier)
    )


def reveal_delay(media: Media) -> float:
    """Lo que se espera tras subir el GIF antes de cambiarlo por el PNG final."""
    return media.seconds + REVEAL_MARGIN_SECONDS + len(media.gif) / 2**20 * REVEAL_SECONDS_PER_MB


def base_text(theme: Theme, play: BasePlay, rng: random.Random | None = None) -> str:
    """Bloque grande con lo que ha pasado en una tirada base."""
    rng = rng or random.Random()
    spin = play.spin
    lines: list[str] = []
    tier = win_tier(spin.points)
    if tier:
        lines.append(f"# 💥 {WIN_TITLES[tier]}")
        lines.append(f"## +{format_amount(play.payout)}")
    elif play.net > 0:
        lines.append(f"# {rng.choice(WIN_LINES)} +{format_amount(play.net)}")
    elif play.payout > 0:
        lines.append(f"## Cobras {format_amount(play.payout)}")
    elif spin.near_miss:
        lines.append(f"## {rng.choice(NEAR_MISS_LINES)}")
    else:
        lines.append(
            f"## -{format_amount(play.stake)} · "
            f"{rng.choice(LOSS_LINES + THEME_LOSS_LINES[theme.key])}"
        )
    if play.media.gif:
        # Lo que solo se ve con animación: la espera del quinto rodillo y cómo acaba.
        tease = tease_of(spin)
        if tease.kind is not None and tease_hit(spin, tease):
            if tease.kind in ("rescue", "rescue_big"):
                lines.append(f"### {rng.choice(RESCUE_LINES)}")
            elif tease.kind == "ways":
                lines.append(f"### {theme.symbols[-1][0]} {rng.choice(FIVE_WAIT_LINES)}")
        elif shows_slip(theme.key, spin):
            lines.append(f"-# {theme.collect[0]} {rng.choice(SLIP_LINES)}")

    if spin.collectors:
        times = " ×2" if spin.collectors == 2 else ""
        lines.append(
            f"{rng.choice(COLLECT_LINES[theme.key])} {len(spin.coins)} monedas{times}: "
            f"+{format_amount(to_amount(spin.collect_points, play.stake))}"
        )
    best = max(spin.wins, key=lambda w: w.points, default=None)
    if best is not None:
        emoji = theme.symbols[best.symbol][0]
        ways = f" · {best.ways} ways" if best.ways > 1 else ""
        lines.append(
            f"{emoji * best.reels} +{format_amount(to_amount(best.points, play.stake))}{ways}"
        )
    chips = spin.chips
    if chips["mini"] or chips["major"]:
        parts = [f"{n} {name.upper()}" for name, n in chips.items() if n]
        lines.append(f"🎰 Ficha de bote: {' y '.join(parts)} suben.")
    if play.trigger is not None:
        name = bonus_name(theme, play.trigger.kind)
        lines.append(f"### 🎁 ¡{name.strip('¡!')}! Se llena el maletín.")
        if play.trigger.upgraded:
            lines.append("💫 ¡Y sube al gran bonus, con monedas de los tres colores!")
    return "\n".join(lines)


def bonus_text(
    theme: Theme,
    game: BonusGame,
    step: BonusStep | None,
    *,
    left_before: int | None = None,
    rng: random.Random | None = None,
) -> str:
    """Texto del bonus tras una tirada (o al empezar).

    Args:
        left_before: Tiradas que quedaban antes de esta (si era la última y cae algo, se dice).
    """
    rng = rng or random.Random()
    lines = [f"# 🎁 {bonus_name(theme, game.kind)}"]
    if step is None:
        lines.append(BONUS_INTRO[theme.key])
        lines.append(
            f"Quedan **{game.respins_left}** tiradas y vuelven a {game.reset_value} cada vez "
            "que cae algo."
        )
    else:
        if step.landings:
            coins = sum(1 for landing in step.landings if landing.cell.is_coin)
            parts = []
            if coins:
                parts.append("una moneda" if coins == 1 else f"{coins} monedas")
            if step.multiplier_added:
                parts.append(f"ticket +×{step.multiplier_added}")
            if step.extra:
                parts.append(f"+{step.extra} tirada extra")
            if step.instant != 10:
                factor = f"{step.instant / 10:.1f}".rstrip("0").rstrip(".").replace(".", ",")
                parts.append(f"¡las monedas ×{factor}!")
            if step.maximized:
                parts.append("¡botes al máximo!")
            saved = f"{rng.choice(LAST_SAVE_LINES)} " if left_before == 1 else ""
            lines.append(f"## {saved}¡Cae {', '.join(parts)}! Vuelve a {game.respins_left}.")
            before = game.coins - coins
            for goal, name in JACKPOT_GOALS:
                if before == goal - 1 and game.coins >= goal:
                    lines.append(
                        f"### 🏆 ¡Por los pelos! Cae la moneda que faltaba para el {name}."
                    )
        else:
            left = game.respins_left
            if left == 1:
                lines.append("## Nada… ¡queda la última!")
            elif left:
                lines.append(f"## Nada… quedan {left}.")
            else:
                lines.append("## Se acabó.")
    current = to_amount(game.coin_points, game.stake) * game.multiplier
    lines.append(
        f"Monedas: **{game.coins}/20** · en la mesa {format_amount(current)}"
        + (f" (×{game.multiplier})" if game.multiplier > 1 else "")
    )
    won = game.jackpots()
    if won:
        lines.append("🏆 Asegurado: " + " + ".join(name.upper() for name in won))
    if game.coins < MINI_COINS:
        lines.append(f"-# {MINI_COINS - game.coins} para el MINI")
    elif game.coins < MAJOR_COINS:
        lines.append(f"-# {MAJOR_COINS - game.coins} para el MAJOR")
    elif game.coins < 20:
        lines.append(f"-# ¡{20 - game.coins} para el GRAND!")
    return "\n".join(lines)


def bonus_end_text(theme: Theme, payout: BonusPayout) -> str:
    """Resumen del bonus pagado."""
    result = payout.result
    tier = win_tier(result.points)
    title = WIN_TITLES.get(tier, "¡Bonus cobrado!")
    lines = [f"# 🎁 {title}", f"## +{format_amount(payout.amount)}"]
    coins = to_amount(result.coin_points, payout.stake)
    detail = f"{result.coins} monedas · {format_amount(coins)}"
    if result.multiplier > 1:
        detail += f" ×{result.multiplier}"
    lines.append(detail)
    for name, value in zip(result.jackpots, result.jackpot_values, strict=True):
        lines.append(f"### 🏆 ¡{name.upper()}! +{format_amount(to_amount(value, payout.stake))}")
    if result.coins == 19:
        lines.append("¡Una moneda para el GRAND! Sofía, cállate, que no respiro.")
    return "\n".join(lines)


def paytable_embed(theme: Theme) -> discord.Embed:
    """Tabla de premios y reglas de una máquina (se manda en privado)."""
    lines = ["**Ways** (de izquierda a derecha, cualquier fila; 3, 4 y 5 rodillos):"]
    for index in reversed(range(len(theme.symbols))):
        emoji, _name = theme.symbols[index]
        pays = " / ".join(format_multiplier(p) for p in WAYS_PAYS[index])
        lines.append(f"{emoji} {pays} por way")
    lines.append(f"{theme.wild[0]} comodín en los rodillos 2 a 4 (no sustituye monedas).")
    lines.append("")
    lines.append("**Monedas** (llevan el premio escrito):")
    for tier in Tier:
        values = [v for v, _w in COIN_VALUES[tier]]
        lines.append(
            f"{TIER_SHAPES[tier]}: {format_multiplier(min(values))} a "
            f"{format_multiplier(max(values))} la apuesta"
        )
    lines.append(
        f"{theme.collect[0]} en el rodillo 1 o el 5 recoge todas las monedas; en los dos, el doble."
    )
    lines.append("")
    lines.append("**Maletines**: cada moneda que cae entra en el de su color, se cobre o no.")
    lines.append(
        " · ".join(f"{TIER_SHAPES[t].split()[0]} {CASE_SIZE[t]}" for t in Tier)
        + f" monedas. Lleno = bonus. 1 de cada {GRAND_BONUS_ODDS} sube al gran bonus."
    )
    lines.append("")
    lines.append(
        f"**Bonus**: {RESET_VALUE} tiradas; cada vez que cae algo vuelven a {RESET_VALUE}. "
        "Las monedas se quedan. 🎟️ suma al multiplicador, 🔁 +1 tirada, 💥 multiplica las "
        "monedas, 🏆 sube los botes al máximo y ❓ es una sorpresa."
    )
    lines.append(
        f"**Botes**: MINI con {MINI_COINS} monedas ({format_multiplier(MINI_BASE)} a "
        f"{format_multiplier(MINI_MAX)}), MAJOR con {MAJOR_COINS} "
        f"({format_multiplier(MAJOR_BASE)} a {format_multiplier(MAJOR_MAX)}) y GRAND con la "
        f"pantalla llena ({format_multiplier(GRAND)}). Las fichas del juego base suben el MINI "
        "y el MAJOR."
    )
    lines.append(f"-# Apuesta mínima: {format_amount(MIN_STAKE)}. Retorno medio ~94 %.")
    return discord.Embed(
        title=f"📋 {theme.emoji} {theme.title}: premios y reglas",
        description="\n".join(lines),
        color=COLOR_WIN,
    )


def parse_stake(amount_text: str | None, balance: int) -> int:
    """Apuesta del comando: la indicada (`500`, `2k`, `all`) o la de por defecto.

    Raises:
        ValueError: Con un mensaje mostrable si no se entiende o no llega al mínimo.
    """
    if not amount_text:
        return max(MIN_STAKE, min(DEFAULT_STAKE, balance))
    stake = parse_amount(amount_text, balance)
    if stake < MIN_STAKE:
        raise ValueError(f"La apuesta mínima es {format_amount(MIN_STAKE)}.")
    return stake


def tax_note(settlements: list[BetSettlement]) -> str | None:
    """Línea de IRPF de una jugada o de varias (suma de ajustes)."""
    if not settlements:
        return None
    if len(settlements) == 1:
        return gambling_tax_line(settlements[0])
    delta = sum(s.tax_delta for s in settlements)
    last = settlements[-1]
    if delta > 0:
        return (
            f"-# 🐶 {TAX_COLLECTOR} se lleva {format_amount(delta)} de IRPF. "
            f"Hoy vas {format_amount(last.day_net)} arriba."
        )
    if delta < 0:
        return (
            f"-# 🐶 {TAX_COLLECTOR} te devuelve {format_amount(-delta)}: "
            "tus pérdidas de hoy compensan lo que habías ganado."
        )
    return None


EditFn = Callable[..., Awaitable[Any]]
#: Lo pintado por adelantado de una máquina, por su clave (ver `HoldWinView.prepare`).
ReadyFn = Callable[[tuple[Any, ...]], "Awaitable[Any] | None"]


@dataclass(frozen=True, slots=True)
class BonusAhead:
    """La próxima tirada de un bonus, ya jugada sobre una copia.

    Attributes:
        source: El bonus del que sale la copia.
        spins: Tiradas que llevaba `source` al copiarlo (si ya no coinciden, no vale).
        game: La copia, tras jugar la tirada.
        step: Lo que ha pasado en ella.
    """

    source: BonusGame
    spins: int
    game: BonusGame
    step: BonusStep


def still_key(theme: str, spin: BaseSpin, stake: int, panel: Panel, lit: bool) -> tuple:
    """Lo que decide la imagen quieta del final de un 🔁 Auto ×10."""
    return ("still", theme, spin.grid, stake, panel, lit)


# -- Máquina ------------------------------------------------------------------------


class HoldWinView(discord.ui.View):
    """Máquina de un jugador: un mensaje con botones.

    Guarda la apuesta, el turbo y el bonus en curso. No guarda dinero ni
    maletines: se leen y se cambian siempre a través del cog.
    """

    def __init__(
        self, cog: HoldWin, *, theme: Theme, guild_id: int, owner: discord.abc.User, stake: int
    ) -> None:
        super().__init__(timeout=MACHINE_TIMEOUT)
        self.cog = cog
        self.theme = theme
        self.guild_id = guild_id
        self.owner = owner
        self.stake = stake
        self.turbo = cog.turbo_default(guild_id, owner.id)
        self.session_spins = 0
        self.bonus: BonusGame | None = None
        self.last_text: str | None = None
        self.last_won: bool | None = None
        self.message: discord.Message | None = None
        self._last_interaction: discord.Interaction | None = None
        self._busy = False
        # Pintar antes del clic (`prepare`): lo ya sorteado, que solo vive en memoria y se
        # gasta al jugarlo, y lo ya pintado (o pintándose) por su clave.
        self._upcoming: BaseSpin | None = None
        self._auto_next: list[BaseSpin] | None = None
        self._bonus_next: BonusAhead | None = None
        self._ahead: dict[tuple[Any, ...], asyncio.Future[Any]] = {}
        self._painting: asyncio.Task[None] | None = None
        # Entre gastar lo sorteado y enseñar la jugada, lo que se ve aún no es lo que
        # queda: no se pinta nada por adelantado hasta el siguiente `prepare`.
        self._changing = False
        self._build_buttons()

    # -- Botones --------------------------------------------------------------------

    def _add(
        self,
        label: str,
        row: int,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        custom_id: str,
    ) -> discord.ui.Button:
        button: discord.ui.Button = discord.ui.Button(
            label=label, style=style, row=row, custom_id=f"{self.theme.key}:{custom_id}"
        )
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)
        return button

    def _build_buttons(self) -> None:
        green, blue = discord.ButtonStyle.success, discord.ButtonStyle.primary
        self.spin_button = self._add("🎰 Tirar", 0, self._spin, style=green, custom_id="spin")
        self.auto_button = self._add(
            f"🔁 Auto ×{AUTO_SPINS}", 0, self._auto, style=blue, custom_id="auto"
        )
        self.turbo_button = self._add("⚡ Turbo", 0, self._toggle_turbo, custom_id="turbo")
        self.half_button = self._add("½", 1, self._halve, custom_id="half")
        self.double_button = self._add("×2", 1, self._double_stake, custom_id="x2")
        self.allin_button = self._add("💰 All-in", 1, self._all_in, custom_id="allin")
        self._add("📋 Premios", 1, self._paytable, custom_id="paytable")
        self._set_enabled(True)

    def _set_enabled(self, enabled: bool) -> None:
        """Activa o desactiva los botones y pone al día sus etiquetas según el modo."""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = not enabled
        if self.bonus is not None:
            self.spin_button.label = f"🎰 Girar ({self.bonus.respins_left})"
            self.spin_button.style = discord.ButtonStyle.primary
            self.auto_button.label = "⏩ Auto bonus"
            for button in (self.half_button, self.double_button, self.allin_button):
                button.disabled = True
        else:
            self.spin_button.label = f"🎰 Tirar · {format_amount(self.stake)}"
            self.spin_button.style = discord.ButtonStyle.success
            self.auto_button.label = f"🔁 Auto ×{AUTO_SPINS}"
        self.turbo_button.label = "⚡ Turbo: sí" if self.turbo else "⚡ Turbo"
        self.turbo_button.style = (
            discord.ButtonStyle.success if self.turbo else discord.ButtonStyle.secondary
        )

    # -- Ciclo de vida --------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño juega; al resto se le invita a abrir la suya."""
        if interaction.user.id == self.owner.id:
            return True
        await interaction.response.send_message(
            f"Esta máquina es de {self.owner.display_name}. "
            f"Abre la tuya con `tragas {self.theme.key}`.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        """Cierra la máquina; si queda un bonus, se juega hasta el final y se paga."""
        self.cog.machines.discard(self)
        payout = await self.finish_bonus_unattended()
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        kwargs: dict[str, Any] = {"view": self}
        if payout is not None:
            text = bonus_end_text(self.theme, payout) + "\n-# Bonus jugado al cerrar la máquina."
            kwargs["embed"] = await self.current_embed(text=text, won=True)
        try:
            if self._last_interaction is not None:
                await self._last_interaction.edit_original_response(**kwargs)
            elif self.message is not None:
                await self.message.edit(**kwargs)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar la máquina de botes", exc_info=True)

    async def finish_bonus_unattended(self) -> BonusPayout | None:
        """Juega y paga el bonus pendiente sin enseñarlo (cierre o apagado)."""
        if self.bonus is None:
            return None
        game, self.bonus = self.bonus, None
        try:
            game.play_out(self.cog.rng)
            payout = await self.cog.pay_bonus(self.guild_id, self.owner.id, self.theme, game)
        except Exception:
            logger.exception("No se pudo pagar un bonus al cerrar la máquina")
            return None
        await self._track_bonus(payout)
        return payout

    # -- Embeds ---------------------------------------------------------------------

    async def balance(self) -> int:
        """Saldo actual del dueño."""
        return await self.cog.economy.balance(self.guild_id, self.owner.id)

    async def current_embed(
        self,
        *,
        balance: int | None = None,
        text: str | None = None,
        won: bool | None = None,
        image: str = PNG_NAME,
    ) -> discord.Embed:
        """Embed de la máquina con el saldo actual."""
        if balance is None:
            balance = await self.balance()
        if text is None:
            text = self.last_text
        if text is None:
            text = (
                f"Pulsa 🎰 **Tirar**. Los símbolos pagan por **ways** y "
                f"{self.theme.collect[0]} recoge las monedas.\n"
                "Cada moneda llena su **maletín**: lleno, **bonus**. "
                "📋 **Premios** para las reglas."
            )
        if self.bonus is None and balance < self.stake:
            text += "\n\n**No te llega para esta apuesta.** Bájala o usa `imv`."
        if won is None:
            won = self.last_won
        if self.bonus is not None:
            color = COLOR_BONUS
        elif won is None:
            color = COLOR_IDLE
        else:
            color = COLOR_WIN if won else COLOR_LOSS
        embed = discord.Embed(
            title=f"{self.theme.emoji} {self.theme.title}", description=text, color=color
        )
        embed.add_field(name="Saldo", value=format_amount(balance))
        if self.bonus is not None:
            embed.add_field(name="Apuesta del bonus", value=format_amount(self.bonus.stake))
        else:
            embed.add_field(name="Apuesta", value=format_amount(self.stake))
        embed.set_image(url=f"attachment://{image}")
        footer = f"Máquina de {self.owner.display_name}"
        if self.turbo:
            footer += " · ⚡ Turbo"
        embed.set_footer(text=footer)
        return embed

    # -- Pintar antes del clic ---------------------------------------------------------

    def _next_spin(self) -> BaseSpin:
        """La próxima tirada, sorteada la primera vez que hace falta."""
        if self._upcoming is None:
            self._upcoming = spin_base(self.cog.rng)
        return self._upcoming

    def _take_spin(self) -> BaseSpin | None:
        """La tirada sorteada por adelantado, que se gasta al jugarla (`None` si no hay)."""
        spin, self._upcoming = self._upcoming, None
        self._changing = True
        return spin

    def _next_auto(self) -> list[BaseSpin]:
        """Las diez tiradas del próximo 🔁 Auto, sorteadas la primera vez que hacen falta."""
        if self._auto_next is None:
            self._auto_next = [spin_base(self.cog.rng) for _ in range(AUTO_SPINS)]
        return self._auto_next

    def _take_auto(self) -> list[BaseSpin]:
        """Las diez del Auto sorteadas por adelantado, que se gastan al jugarlas."""
        spins, self._auto_next = self._auto_next, None
        self._changing = True
        return spins or []

    def _next_bonus(self) -> BonusAhead:
        """La próxima tirada del bonus, jugada sobre una copia la primera vez que hace falta."""
        game = self.bonus
        assert game is not None
        ahead = self._bonus_next
        if ahead is None or ahead.source is not game or ahead.spins != game.spins:
            played = copy.deepcopy(game)
            ahead = self._bonus_next = BonusAhead(
                game, game.spins, played, played.step(self.cog.rng)
            )
        return ahead

    def _take_bonus(self) -> BonusAhead | None:
        """La tirada del bonus jugada por adelantado, si sigue valiendo para el bonus de ahora."""
        ahead, self._bonus_next = self._bonus_next, None
        self._changing = True
        if ahead is not None and ahead.source is self.bonus and ahead.spins == self.bonus.spins:
            return ahead
        return None

    def _ready(self, key: tuple[Any, ...]) -> asyncio.Future[Any] | None:
        """Lo pintado (o pintándose) por adelantado para `key`; se gasta al usarlo."""
        return self._ahead.pop(key, None)

    async def _plans(self) -> list[tuple[tuple[Any, ...], Callable[[], Awaitable[Any]]]]:
        """Cada botón que se puede pulsar ahora y su imagen, del más probable al que menos.

        En el juego base: Tirar a la apuesta de ahora, Tirar con la de ½, ×2 y All-in (la
        imagen depende de la apuesta: las monedas y los botes llevan su valor en Y$) y la
        imagen final de un 🔁 Auto ×10. En el bonus, la próxima tirada.
        """
        if self.bonus is not None:
            return self._bonus_plans()
        scene, theme, turbo, current = self.cog.renderer, self.theme, self.turbo, self.stake
        spin = self._next_spin()
        auto = self._next_auto()
        meters = await self.cog.meters(self.guild_id, self.owner.id, theme.key)
        balance = await self.balance()

        def tirada(
            stake: int,
        ) -> tuple[tuple[Any, ...], Callable[[], Awaitable[Media]]]:
            # Los maletines de después salen de jugar `fill_cases` sobre una copia: el sorteo
            # del gran bonus (la `Random` cualquiera) no cambia lo que se ve de la tirada.
            filled = copy.deepcopy(meters)
            fill_cases(filled, spin, stake, random.Random(0))
            before, after = base_panel(meters, stake), base_panel(filled, stake)
            payout = to_amount(spin.points, stake)
            key = base_key(theme.key, spin, stake=stake, before=before, after=after, turbo=turbo)
            return key, lambda: scene.base(
                theme.key,
                spin,
                stake=stake,
                before=before,
                after=after,
                banner=base_banner(spin, payout),
                turbo=turbo,
            )

        stakes = (current, max(MIN_STAKE, current // 2), max(MIN_STAKE, min(current * 2, balance)))
        plans = [tirada(s) for s in dict.fromkeys((*stakes, balance)) if MIN_STAKE <= s <= balance]
        # El 🔁 Auto ×10: sus diez tiradas, con los maletines llenándose tirada a tirada, hasta
        # que salga un bonus o no llegue el dinero. Solo se ve la imagen del final.
        chain, odds, money, last = copy.deepcopy(meters), random.Random(0), balance, None
        for each in auto:
            if money < current:
                break
            money += to_amount(each.points, current) - current
            last = each
            if fill_cases(chain, each, current, odds) is not None:
                break
        if last is not None:
            panel, lit = base_panel(chain, current), to_amount(last.points, current) > 0
            plans.append(
                (
                    still_key(theme.key, last, current, panel, lit),
                    lambda: scene.still(theme.key, last, stake=current, panel=panel, highlight=lit),
                )
            )
        return plans

    def _bonus_plans(self) -> list[tuple[tuple[Any, ...], Callable[[], Awaitable[Any]]]]:
        """La próxima tirada del bonus, ya jugada sobre una copia, con su imagen."""
        game = self.bonus
        if game is None or game.finished:
            return []
        scene, theme, turbo = self.cog.renderer, self.theme, self.turbo
        ahead = self._next_bonus()
        played, step = ahead.game, ahead.step
        before = list(game.board)
        panel_before, panel_after = bonus_panel(theme, game), bonus_panel(theme, played)
        finish = banner = None
        if played.finished:
            result = played.result()
            amount = to_amount(result.points, played.stake)
            finish, banner = BonusFinish(result, amount), bonus_banner(result, amount)
        key = bonus_key(
            theme.key,
            before,
            step,
            stake=played.stake,
            panel_before=panel_before,
            panel_after=panel_after,
            finish=finish,
            turbo=turbo,
        )
        return [
            (
                key,
                lambda: scene.bonus_step(
                    theme.key,
                    before,
                    step,
                    stake=played.stake,
                    panel_before=panel_before,
                    panel_after=panel_after,
                    finish=finish,
                    banner=banner,
                    turbo=turbo,
                ),
            )
        ]

    def prepare(self) -> None:
        """Pinta en segundo plano la imagen de cada botón que se puede pulsar ahora.

        Se llama al enseñar una jugada (en cuanto la máquina queda como se va a ver), al
        abrir la máquina, al cambiar la apuesta o el turbo y en cada camino de error que
        gasta lo sorteado. Pinta de una en una, de la más probable a la menos (`_plans`), y
        antes de cada una vuelve a mirar qué hace falta: lo que ya no vale se suelta y
        nunca se corta un dibujo a medias.
        """
        self._changing = False
        if not self.cog.ahead or self.is_finished():
            return
        if self._painting is None or self._painting.done():
            self._painting = asyncio.create_task(
                self._paint_ahead(), name=f"botes-ahead-{self.guild_id}-{self.owner.id}"
            )

    async def _paint_ahead(self) -> None:
        while not self.is_finished() and not self._changing:
            plans = await self._plans()
            if self._changing:
                return
            wanted = {key for key, _ in plans}
            for key in [k for k, f in self._ahead.items() if f.done() and k not in wanted]:
                del self._ahead[key]
            todo = next(((key, paint) for key, paint in plans if key not in self._ahead), None)
            if todo is None:
                return
            key, paint = todo
            future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
            self._ahead[key] = future
            result = None
            try:
                result = await paint()
            except Exception:
                logger.warning(
                    "No se pudo pintar por adelantado la máquina de botes", exc_info=True
                )
            finally:
                # Quien ya lo espera (un clic que llegó a mitad) no se queda colgado.
                if not future.done():
                    future.set_result(result)

    # -- Juego base -----------------------------------------------------------------

    async def _guarded_play(
        self, interaction: discord.Interaction, *, turbo: bool, render: bool = True
    ) -> BasePlay | None:
        """Una tirada base con los errores de saldo convertidos en avisos privados."""
        try:
            play = await self.cog.play_base(
                self.guild_id,
                self.owner.id,
                self.theme,
                stake=self.stake,
                turbo=turbo,
                render=render,
                session_spins=self.session_spins + 1,
                spin=self._take_spin(),
                ready=self._ready,
            )
        except InsufficientFundsError as error:
            self.prepare()
            await notify(interaction, insufficient_text(error.balance, self.stake))
            return None
        except BalanceLimitError:
            self.prepare()
            await notify(interaction, "La banca no puede pagar tanto. Baja la apuesta.")
            return None
        self.session_spins += 1
        if play.bonus is not None:
            self.bonus = play.bonus
        return play

    async def _spin(self, interaction: discord.Interaction) -> None:
        """🎰: en el juego base, una tirada; en el bonus, una tirada del bonus."""
        if self._busy:
            await ack(interaction)
            return
        if self.bonus is not None:
            await self._bonus_spin(interaction)
            return
        self._busy = True
        play: BasePlay | None = None
        try:
            # Cobrar y dibujar tardan: se acepta el clic antes.
            await ack(interaction)
            play = await self._guarded_play(interaction, turbo=self.turbo)
            if play is None:
                return
            self._last_interaction = interaction
            await self.show_base(
                play,
                first_edit=interaction.edit_original_response,
                final_edit=interaction.edit_original_response,
            )
        finally:
            self._busy = False
        await renta.remind(self.cog.bot, interaction)
        await self._track_base(play)
        await self.cog.shout_base(self.theme, play, self.owner, self._channel())

    def _channel(self) -> object:
        return getattr(self.message, "channel", None)

    async def show_base(self, play: BasePlay, *, first_edit: EditFn, final_edit: EditFn) -> None:
        """Enseña la tirada (GIF y PNG, o solo PNG en turbo) y, si toca, la entrada al bonus."""
        intro: asyncio.Task[Media] | None = None
        if self.bonus is not None:
            # La entrada al bonus se pinta mientras se ve la tirada que lo dispara; lo siguiente
            # (la primera tirada del bonus) se pinta cuando sale ella.
            intro = asyncio.create_task(self._paint_intro(play, self.bonus), name="botes-intro")
        else:
            # La máquina ya está como se va a ver: se pinta lo siguiente mientras se sube esto.
            self.prepare()
        if play.media.gif:
            self._set_enabled(False)
            await first_edit(
                embed=self._spinning_embed(),
                attachments=[discord.File(io.BytesIO(play.media.gif), filename=GIF_NAME)],
                view=self,
            )
            await asyncio.sleep(reveal_delay(play.media))
            edit = final_edit
        else:
            edit = first_edit
        text = base_text(self.theme, play)
        if note := tax_note([play.settlement]):
            text += f"\n{note}"
        if hint := await renta.hint(
            self.cog.bot,
            self.guild_id,
            self.owner.id,
            bet_moment(stake=play.stake, net=play.net, balance_after=play.balance),
        ):
            text += f"\n{hint}"
        self.last_text = text
        self.last_won = play.payout > 0
        png = play.media.png
        if intro is not None:
            # La tirada que llena el maletín se queda un momento y sigue la entrada al bonus.
            await edit(
                embed=await self.current_embed(balance=play.balance, text=text),
                attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
                view=self,
            )
            await asyncio.sleep(BONUS_INTRO_PAUSE)
            await self._show_bonus_intro(final_edit, intro)
            return
        self._set_enabled(True)
        await edit(
            embed=await self.current_embed(balance=play.balance, text=text),
            attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
            view=self,
        )

    def _spinning_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"{self.theme.emoji} {self.theme.title}",
            description=f"# 🌀 ¡Girando!\n{format_amount(self.stake)}",
            color=COLOR_SPIN,
        )
        embed.set_image(url=f"attachment://{GIF_NAME}")
        embed.set_footer(text=f"Máquina de {self.owner.display_name}")
        return embed

    async def _paint_intro(self, play: BasePlay, game: BonusGame) -> Media:
        """Pinta la entrada al bonus que dispara `play`: GIF y PNG (solo el PNG en turbo)."""
        return await self.cog.renderer.bonus_intro(
            self.theme.key,
            play.spin,
            base_stake=play.stake,
            before=play.before,
            after=base_panel(play.meters, play.stake),
            game=game,
            panel=bonus_panel(self.theme, game),
            name=bonus_name(self.theme, game.kind),
            banner=Banner(bonus_name(self.theme, game.kind), f"a {format_amount(game.stake)}", 2),
            turbo=self.turbo,
        )

    async def _show_bonus_intro(self, edit: EditFn, intro: Awaitable[Media]) -> None:
        """Enseña la entrada al bonus, ya pintada (o pintándose) desde la tirada que la dispara."""
        assert self.bonus is not None
        game = self.bonus
        media = await intro
        self.last_text = bonus_text(self.theme, game, None)
        self.last_won = None
        # La máquina ya está como se va a ver: se pinta la primera tirada del bonus.
        self.prepare()
        if media.gif:
            self._set_enabled(False)
            await edit(
                embed=await self.current_embed(image=GIF_NAME),
                attachments=[discord.File(io.BytesIO(media.gif), filename=GIF_NAME)],
                view=self,
            )
            await asyncio.sleep(reveal_delay(media))
        self._set_enabled(True)
        await edit(
            embed=await self.current_embed(),
            attachments=[discord.File(io.BytesIO(media.png), filename=PNG_NAME)],
            view=self,
        )

    async def _track_base(self, play: BasePlay | None) -> None:
        """Logros de la tirada, después de enseñarla."""
        if play is None:
            return
        tease = tease_of(play.spin)
        delta = hold_win_stats(
            play.spin,
            theme=self.theme.key,
            stake=play.stake,
            payout=play.payout,
            trigger=play.trigger,
            turbo=not play.media.gif,
            session_spins=play.session_spins,
            when=datetime.now(TIMEZONE),
            tease=tease.kind,
            tease_hit=tease_hit(play.spin, tease),
            slipped=bool(play.media.gif) and shows_slip(self.theme.key, play.spin),
        )
        delta.merge(
            casino_stats(
                stake=play.stake,
                net=play.net,
                balance_after=play.balance,
                tax_delta=play.settlement.tax_delta,
            )
        )
        await logros.casino_play(
            self.cog.bot, self.guild_id, self.owner, self._channel(), delta, net=play.net
        )
        await apuestas.record(
            self.cog.bot,
            self.guild_id,
            self.owner,
            game="botes",
            stake=play.stake,
            net=play.net,
            balance_after=play.balance,
            tax=play.settlement.tax_delta,
        )

    async def _auto(self, interaction: discord.Interaction) -> None:
        """🔁 en el juego base: diez tiradas. ⏩ en el bonus: todas las que queden."""
        if self._busy:
            await ack(interaction)
            return
        if self.bonus is not None:
            await self._bonus_auto(interaction)
            return
        self._busy = True
        plays: list[BasePlay] = []
        stopped: str | None = None
        try:
            await ack(interaction)
            self._last_interaction = interaction
            spins = self._take_auto()
            for index in range(AUTO_SPINS):
                try:
                    play = await self.cog.play_base(
                        self.guild_id,
                        self.owner.id,
                        self.theme,
                        stake=self.stake,
                        turbo=True,
                        render=False,
                        session_spins=self.session_spins + 1,
                        spin=spins[index] if index < len(spins) else None,
                    )
                except InsufficientFundsError:
                    stopped = "Parado: no te llega para otra tirada."
                    break
                except BalanceLimitError:
                    stopped = "Parado: la banca no puede pagar tanto."
                    break
                self.session_spins += 1
                plays.append(play)
                if play.bonus is not None:
                    self.bonus = play.bonus
                    stopped = "Parado: ¡se ha llenado un maletín!"
                    break
            if not plays:
                self.prepare()
                await interaction.followup.send(
                    insufficient_text(await self.balance(), self.stake), ephemeral=True
                )
                return
            last = plays[-1]
            panel, lit = base_panel(last.meters, last.stake), last.payout > 0
            ready = self._ready(still_key(self.theme.key, last.spin, last.stake, panel, lit))
            png = await ready if ready is not None else None
            if png is None:
                png = await self.cog.renderer.still(
                    self.theme.key, last.spin, stake=last.stake, panel=panel, highlight=lit
                )
            intro: asyncio.Task[Media] | None = None
            if self.bonus is not None:
                # Sale del Auto con un bonus: su entrada se pinta mientras se ve el resumen.
                intro = asyncio.create_task(self._paint_intro(last, self.bonus), name="botes-intro")
            else:
                self.prepare()
            text = self.auto_text(plays, stopped)
            if note := tax_note([p.settlement for p in plays]):
                text += f"\n{note}"
            if hint := await renta.hint(
                self.cog.bot,
                self.guild_id,
                self.owner.id,
                bet_moment(
                    stake=sum(p.stake for p in plays),
                    net=sum(p.net for p in plays),
                    balance_after=plays[-1].balance,
                ),
            ):
                text += f"\n{hint}"
            self.last_text = text
            self.last_won = sum(p.net for p in plays) > 0
            self._set_enabled(True)
            if self.bonus is not None:
                self._set_enabled(False)
            await interaction.edit_original_response(
                embed=await self.current_embed(balance=last.balance, text=text),
                attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
                view=self,
            )
            if intro is not None:
                await asyncio.sleep(BONUS_INTRO_PAUSE)
                await self._show_bonus_intro(interaction.edit_original_response, intro)
        finally:
            self._busy = False
        await renta.remind(self.cog.bot, interaction)
        for play in plays:
            await self._track_base(play)
        await logros.track(
            self.cog.bot,
            self.guild_id,
            self.owner,
            self._channel(),
            StatDelta(add={"botes_auto": 1}),
        )
        for play in plays:
            await self.cog.shout_base(self.theme, play, self.owner, self._channel())

    def auto_text(self, plays: list[BasePlay], stopped: str | None) -> str:
        """Resumen de una ronda de Auto."""
        net = sum(p.net for p in plays)
        sign = "+" if net > 0 else "-" if net < 0 else "±"
        paid = sum(1 for p in plays if p.payout > 0)
        collects = sum(1 for p in plays if p.spin.collectors)
        lines = [
            f"# 🔁 {len(plays)} tiradas · {sign}{format_amount(abs(net))}",
            f"{paid} con premio · {collects} recogidas · apostado "
            f"{format_amount(sum(p.stake for p in plays))}",
        ]
        best = max(plays, key=lambda p: p.payout)
        if best.payout > 0:
            lines.append(f"Mejor tirada: +{format_amount(best.payout)}")
        if stopped:
            lines.append(f"-# {stopped}")
        return "\n".join(lines)

    # -- Bonus ----------------------------------------------------------------------

    async def _bonus_spin(self, interaction: discord.Interaction) -> None:
        """Una tirada del bonus, con su animación."""
        assert self.bonus is not None
        self._busy = True
        payout: BonusPayout | None = None
        try:
            await ack(interaction)
            self._last_interaction = interaction
            payout = await self._bonus_step(
                first_edit=interaction.edit_original_response,
                final_edit=interaction.edit_original_response,
            )
        finally:
            self._busy = False
        if payout is not None:
            await renta.remind(self.cog.bot, interaction)
            await self._track_bonus(payout)
            await self.cog.shout_bonus(self.theme, payout, self.owner, self._channel())

    async def _bonus_auto(self, interaction: discord.Interaction) -> None:
        """Juega el bonus entero, tirada a tirada, editando el mensaje."""
        self._busy = True
        payout: BonusPayout | None = None
        try:
            await ack(interaction)
            self._last_interaction = interaction
            while self.bonus is not None:
                payout = await self._bonus_step(
                    first_edit=interaction.edit_original_response,
                    final_edit=interaction.edit_original_response,
                )
                if self.bonus is not None and self.turbo:
                    await asyncio.sleep(BONUS_AUTO_PAUSE)
        finally:
            self._busy = False
        await renta.remind(self.cog.bot, interaction)
        if payout is not None:
            await self._track_bonus(payout)
            await self.cog.shout_bonus(self.theme, payout, self.owner, self._channel())

    async def _bonus_step(self, *, first_edit: EditFn, final_edit: EditFn) -> BonusPayout | None:
        """Juega, dibuja y enseña una tirada del bonus; si termina, lo paga.

        El bonus se paga antes de enseñar el último fotograma: si Discord
        falla al editar, el dinero ya está en su sitio.
        """
        game = self.bonus
        assert game is not None
        before = list(game.board)
        panel_before = bonus_panel(self.theme, game)
        if planned := self._take_bonus():
            # Lo pintado y lo jugado son lo mismo: la copia ya jugada pasa a ser el bonus.
            game = self.bonus = planned.game
            step = planned.step
        else:
            step = game.step(self.cog.rng)
        payout: BonusPayout | None = None
        finish: BonusFinish | None = None
        banner: Banner | None = None
        if game.finished:
            self.bonus = None
            payout = await self.cog.pay_bonus(self.guild_id, self.owner.id, self.theme, game)
            finish = BonusFinish(payout.result, payout.amount)
            banner = bonus_banner(payout.result, payout.amount)
        media = await self.cog.bonus_media(
            self.theme.key,
            before,
            step,
            stake=game.stake,
            panel_before=panel_before,
            panel_after=bonus_panel(self.theme, game),
            finish=finish,
            banner=banner,
            turbo=self.turbo,
            ready=self._ready,
        )
        if payout is None:
            text = bonus_text(self.theme, game, step, left_before=panel_before.respins_left)
            won: bool | None = None
            balance = None
        else:
            text = bonus_end_text(self.theme, payout)
            if note := tax_note([payout.settlement]):
                text += f"\n{note}"
            if hint := await renta.hint(
                self.cog.bot,
                self.guild_id,
                self.owner.id,
                bet_moment(stake=0, net=payout.amount, balance_after=payout.settlement.balance),
            ):
                text += f"\n{hint}"
            won = True
            balance = payout.settlement.balance
        self.last_text = text
        self.last_won = won
        # La máquina ya está como se va a ver: se pinta lo siguiente mientras se sube esto.
        self.prepare()
        if media.gif:
            self._set_enabled(False)
            embed = await self.current_embed(
                text=f"# 🎁 {bonus_name(self.theme, game.kind)}\n🌀 Girando…", image=GIF_NAME
            )
            await first_edit(
                embed=embed,
                attachments=[discord.File(io.BytesIO(media.gif), filename=GIF_NAME)],
                view=self,
            )
            await asyncio.sleep(reveal_delay(media))
            edit = final_edit
        else:
            edit = first_edit
        self._set_enabled(True)
        await edit(
            embed=await self.current_embed(balance=balance, text=text, won=won),
            attachments=[discord.File(io.BytesIO(media.png), filename=PNG_NAME)],
            view=self,
        )
        return payout

    async def _track_bonus(self, payout: BonusPayout) -> None:
        delta = hold_win_bonus_stats(
            payout.result, payout.steps, amount=payout.amount, seen=not self.turbo
        )
        delta.merge(
            casino_stats(
                stake=0,
                net=payout.amount,
                balance_after=payout.settlement.balance,
                tax_delta=payout.settlement.tax_delta,
            )
        )
        await logros.casino_play(
            self.cog.bot, self.guild_id, self.owner, self._channel(), delta, net=payout.amount
        )
        await apuestas.record(
            self.cog.bot,
            self.guild_id,
            self.owner,
            game="botes",
            stake=0,
            net=payout.amount,
            balance_after=payout.settlement.balance,
            tax=payout.settlement.tax_delta,
        )

    # -- Ajustes --------------------------------------------------------------------

    async def _refresh(self, interaction: discord.Interaction, balance: int | None = None) -> None:
        """Actualiza la máquina (apuesta, turbo) sin tocar la imagen.

        El embed lee saldo y maletines de la base de datos: se acepta el clic antes.
        """
        await ack(interaction)
        self.prepare()
        self._set_enabled(True)
        await edit(interaction, embed=await self.current_embed(balance=balance), view=self)
        self._last_interaction = interaction

    async def _toggle_turbo(self, interaction: discord.Interaction) -> None:
        self.turbo = not self.turbo
        self.cog.set_turbo_default(self.guild_id, self.owner.id, self.turbo)
        await self._refresh(interaction)

    async def _halve(self, interaction: discord.Interaction) -> None:
        self.stake = max(MIN_STAKE, self.stake // 2)
        await self._refresh(interaction)

    async def _double_stake(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        balance = await self.balance()
        # Si el doble no cabe, se queda en todo el saldo: es lo que se busca.
        self.stake = max(MIN_STAKE, min(self.stake * 2, balance))
        await self._refresh(interaction, balance)

    async def _all_in(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        balance = await self.balance()
        if balance < MIN_STAKE:
            await notify(interaction, insufficient_text(balance, MIN_STAKE))
            return
        self.stake = balance
        await self._refresh(interaction, balance)

    async def _paytable(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=paytable_embed(self.theme), ephemeral=True)


# -- Cog ----------------------------------------------------------------------------


class HoldWin(commands.Cog, name="Botes"):
    """Las máquinas de Botes (el Volcán, la Mina y el Olimpo)."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        repository: HoldWinRepository,
        renderer: HoldWinScene | None = None,
        rng: random.Random | None = None,
        casino_channel_ids: CasinoChannelIds = frozenset(),
        ahead: bool | None = None,
    ) -> None:
        """Monta el cog.

        Args:
            renderer: El dibujo (`HoldWinScene`); las pruebas pasan uno que no pinta.
            ahead: Si las máquinas pintan antes del clic (`HoldWinView.prepare`). Por
                defecto, solo con el dibujo de verdad: con uno de prueba, cada sorteo de
                más gastaría las tiradas de guion de la prueba.
        """
        self.bot = bot
        self.economy = economy
        self.repository = repository
        self.renderer = renderer or HoldWinScene()
        self.ahead = renderer is None if ahead is None else ahead
        self.rng = rng or random.SystemRandom()
        self.casino_channel_ids = casino_channel_ids
        self._turbo: dict[tuple[int, int], bool] = {}
        # Un cerrojo por jugador y máquina: leer, cambiar y guardar los
        # maletines no puede mezclarse con otra tirada del mismo jugador.
        self._locks: dict[tuple[int, int, str], asyncio.Lock] = {}
        self.machines: set[HoldWinView] = set()
        self._warm_task: asyncio.Task[None] | None = None

    async def cog_load(self) -> None:
        """Prepara en segundo plano lo fijo de cada máquina: fondo, casillas y símbolos.

        Es lo que más tarda del primer dibujo (`meta_state`, con caché): el Node del bot ya
        está abierto desde que arranca (`node_scene.warm`) y carga la escena al pintar.
        """

        def warm() -> None:
            for key in THEMES:
                meta_state(key)

        self._warm_task = asyncio.create_task(asyncio.to_thread(warm), name="botes-warm-up")

    async def cog_unload(self) -> None:
        """Al apagar, juega y paga los bonus a medias y cierra el dibujo."""
        if self._warm_task is not None:
            self._warm_task.cancel()
        for view in list(self.machines):
            try:
                await view.finish_bonus_unattended()
            except Exception:
                logger.exception("No se pudo pagar un bonus al apagar")
            view.stop()
        self.machines.clear()
        await self.renderer.close()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra los maletines y botes del servidor que deja el bot."""
        await self.repository.delete_guild_data(guild.id)

    def turbo_default(self, guild_id: int, user_id: int) -> bool:
        """Si el miembro dejó el turbo puesto la última vez."""
        return self._turbo.get((guild_id, user_id), False)

    def set_turbo_default(self, guild_id: int, user_id: int, turbo: bool) -> None:
        """Recuerda el turbo para la próxima máquina del miembro."""
        self._turbo[(guild_id, user_id)] = turbo

    def _lock(self, guild_id: int, user_id: int, theme: str) -> asyncio.Lock:
        key = (guild_id, user_id, theme)
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = TimedLock("hold_win:HoldWin._locks")
        return lock

    async def meters(self, guild_id: int, user_id: int, theme: str) -> Meters:
        """Maletines y botes guardados de un jugador en una máquina."""
        return await self.repository.load(guild_id, user_id, theme)

    # -- Dinero ---------------------------------------------------------------------

    async def play_base(
        self,
        guild_id: int,
        user_id: int,
        theme: Theme,
        *,
        stake: int,
        turbo: bool,
        render: bool = True,
        session_spins: int = 1,
        spin: BaseSpin | None = None,
        ready: ReadyFn | None = None,
    ) -> BasePlay:
        """Juega una tirada base: cobra, paga, llena maletines y, si toca, empieza el bonus.

        La rejilla se decide antes de cobrar, pero los maletines solo cambian
        si el cobro sale bien.

        Args:
            render: `False` para no dibujar nada (Auto).
            spin: La tirada ya sorteada por adelantado (`HoldWinView._take_spin`); si no,
                se sortea aquí.
            ready: Lo pintado por adelantado de la máquina (`HoldWinView._ready`).

        Raises:
            InsufficientFundsError: Si el saldo no cubre la apuesta.
            BalanceLimitError: Si el premio superaría el saldo máximo.
        """
        if stake < MIN_STAKE:
            raise ValueError(f"La apuesta mínima es {MIN_STAKE}.")
        if spin is None:
            spin = spin_base(self.rng)
        payout = to_amount(spin.points, stake)
        async with self._lock(guild_id, user_id, theme.key):
            settlement = await self.economy.settle_bet(
                guild_id, user_id, game=theme.key, stake=stake, payout=payout
            )
            meters = await self.repository.load(guild_id, user_id, theme.key)
            # `fill_cases` cambia `meters`: lo que se ve antes de la tirada se saca ahora.
            before = base_panel(meters, stake)
            trigger = fill_cases(meters, spin, stake, self.rng)
            await self.repository.save(guild_id, user_id, theme.key, meters)
        bonus = None
        if trigger is not None:
            tiers = set(Tier) if trigger.kind == BonusKind.GRAND else {BONUS_TIER[trigger.kind]}
            seeds = [i for i, cell in spin.coins if cell.tier in tiers]
            bonus = BonusGame.start(
                trigger, mini=meters.mini, major=meters.major, rng=self.rng, seed_cells=seeds
            )
        if render:
            media = await self._base_media(
                theme.key,
                spin,
                stake=stake,
                before=before,
                after=base_panel(meters, stake),
                banner=base_banner(spin, payout),
                turbo=turbo,
                ready=ready,
            )
        else:
            media = Media(gif=b"", png=b"", seconds=0.0)
        return BasePlay(
            spin=spin,
            stake=stake,
            payout=payout,
            settlement=settlement,
            meters=meters,
            before=before,
            trigger=trigger,
            bonus=bonus,
            media=media,
            session_spins=session_spins,
        )

    async def _base_media(
        self,
        theme: str,
        spin: BaseSpin,
        *,
        stake: int,
        before: Panel,
        after: Panel,
        banner: Banner | None,
        turbo: bool,
        ready: ReadyFn | None = None,
    ) -> Media:
        """GIF y PNG de una tirada base (solo el PNG en turbo).

        Si la máquina ya la tenía pintada (o pintándose) por adelantado, se usa esa.
        """
        if ready is not None:
            key = base_key(theme, spin, stake=stake, before=before, after=after, turbo=turbo)
            planned = ready(key)
            if planned is not None and (media := await planned) is not None:
                return media
        return await self.renderer.base(
            theme, spin, stake=stake, before=before, after=after, banner=banner, turbo=turbo
        )

    async def bonus_media(
        self,
        theme: str,
        before: Sequence[Cell | None],
        step: BonusStep,
        *,
        stake: int,
        panel_before: BonusPanel,
        panel_after: BonusPanel,
        finish: BonusFinish | None,
        banner: Banner | None,
        turbo: bool,
        ready: ReadyFn | None = None,
    ) -> Media:
        """GIF y PNG de una tirada del bonus (solo el PNG en turbo).

        Si la máquina ya la tenía pintada (o pintándose) por adelantado, se usa esa.
        """
        if ready is not None:
            key = bonus_key(
                theme,
                before,
                step,
                stake=stake,
                panel_before=panel_before,
                panel_after=panel_after,
                finish=finish,
                turbo=turbo,
            )
            planned = ready(key)
            if planned is not None and (media := await planned) is not None:
                return media
        return await self.renderer.bonus_step(
            theme,
            before,
            step,
            stake=stake,
            panel_before=panel_before,
            panel_after=panel_after,
            finish=finish,
            banner=banner,
            turbo=turbo,
        )

    async def pay_bonus(
        self, guild_id: int, user_id: int, theme: Theme, game: BonusGame
    ) -> BonusPayout:
        """Paga un bonus terminado y devuelve a su base los botes ganados.

        Tratamiento fiscal: juego (`pay_winnings`), ver el docstring del módulo.
        """
        result = game.result()
        amount = to_amount(result.points, game.stake)
        async with self._lock(guild_id, user_id, theme.key):
            settlement = await self.economy.pay_winnings(
                guild_id, user_id, game=theme.key, amount=amount
            )
            if result.jackpots:
                meters = await self.repository.load(guild_id, user_id, theme.key)
                reset_won_jackpots(meters, result.jackpots)
                await self.repository.save(guild_id, user_id, theme.key, meters)
        return BonusPayout(
            result=result,
            stake=game.stake,
            amount=amount,
            settlement=settlement,
            kind=game.kind,
            steps=tuple(game.history),
        )

    # -- Anuncios -------------------------------------------------------------------

    async def _shout(self, channel: object, user: discord.abc.User, text: str) -> None:
        if not isinstance(channel, discord.abc.Messageable):
            return
        try:
            await channel.send(
                text, allowed_mentions=discord.AllowedMentions(users=[user], everyone=False)
            )
        except discord.HTTPException:
            logger.debug("No se pudo anunciar un premio de botes", exc_info=True)

    async def shout_base(
        self, theme: Theme, play: BasePlay, user: discord.abc.User, channel: object
    ) -> None:
        """Anuncia en el canal las tiradas base enormes."""
        if play.stake and play.payout >= SHOUT_MULTIPLIER * play.stake:
            await self._shout(
                channel,
                user,
                f"📣 {theme.emoji} ¡{user.mention} acaba de ganar "
                f"**{format_amount(play.payout)}** en {theme.named}!",
            )

    async def shout_bonus(
        self, theme: Theme, payout: BonusPayout, user: discord.abc.User, channel: object
    ) -> None:
        """Anuncia los botes MAJOR y GRAND y los bonus enormes."""
        jackpots = payout.result.jackpots
        if "grand" in jackpots:
            text = (
                f"📣 🏆🏆🏆 ¡{user.mention} ha llenado la pantalla {theme.of_named} y se lleva "
                f"el **GRAND**! **{format_amount(payout.amount)}** en total. {TAX_COLLECTOR} "
                "ya está afilando los dientes."
            )
        elif "major" in jackpots:
            text = (
                f"📣 🏆 ¡{user.mention} saca el **MAJOR** en {theme.named}! "
                f"**{format_amount(payout.amount)}** del bonus."
            )
        elif payout.amount >= SHOUT_MULTIPLIER * payout.stake:
            text = (
                f"📣 {theme.emoji} ¡{user.mention} revienta el bonus {theme.of_named}: "
                f"**{format_amount(payout.amount)}**!"
            )
        else:
            return
        await self._shout(channel, user, text)

    # -- Comandos -------------------------------------------------------------------

    async def _open(
        self,
        theme: Theme,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> None:
        """Lógica compartida entre `/x` y `.x`: abre la máquina."""
        if guild is None:
            await send_error(f"{theme.sentence_named} solo se juega dentro de un servidor.")
            return
        if error := casino_channel_error(self.casino_channel_ids, channel, theme.sentence_named):
            await send_error(error)
            return
        balance = await self.economy.balance(guild.id, user.id)
        try:
            stake = parse_stake(amount_text, balance)
        except ValueError as error:
            await send_error(str(error))
            return
        if stake > balance:
            await send_error(insufficient_text(balance))
            return
        view = HoldWinView(self, theme=theme, guild_id=guild.id, owner=user, stake=stake)
        meters = await self.meters(guild.id, user.id, theme.key)
        png = await self.renderer.still(
            theme.key, spin_base(self.rng), stake=stake, panel=base_panel(meters, stake)
        )
        view.message = await send(
            embed=await view.current_embed(balance=balance),
            file=discord.File(io.BytesIO(png), filename=PNG_NAME),
            view=view,
        )
        self.machines.add(view)
        view.prepare()


async def open_theme(
    bot: commands.Bot,
    theme_key: str,
    *,
    guild: discord.Guild | None,
    channel: object,
    user: discord.abc.User,
    amount_text: str | None,
    send: Callable[..., Awaitable[discord.Message]],
    send_error: Callable[[str], Awaitable[None]],
) -> bool:
    """Abre la máquina `theme_key` de `THEMES` desde `tragas`. `False` si el cog no está."""
    if (cog := find_cog(bot, HoldWin)) is None or theme_key not in THEMES:
        return False
    await cog._open(
        THEMES[theme_key],
        guild=guild,
        channel=channel,
        user=user,
        amount_text=amount_text,
        send=send,
        send_error=send_error,
    )
    return True


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía y el repositorio compartidos del bot."""
    await bot.add_cog(
        HoldWin(
            bot,
            economy=bot.economy,
            repository=bot.hold_win,
            casino_channel_ids=bot.casino_channels,
        )
    )
