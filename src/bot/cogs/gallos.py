"""Peleas de gallos: `gallos`, tu gallo contra los de la casa o contra el de otra persona.

- `gallos` (o `gallos 500`) abre tu reñidero, un panel que solo toca su dueño:
  la ficha de tu gallo (nombre, victorias, KO, rachas y sus tres stats), un
  desplegable con los gallos de la casa (cada uno con su cuota y su
  probabilidad, que cambian con el poder de tu gallo) y los botones
  🐓 **¡A pelear!**, ½, ×2 y 💰 All-in para la apuesta, ✏️ **Nombre** para
  bautizar al gallo, 🎨 para cambiarle el aspecto (skins de la tienda; hay
  tres gratis) y 🏋️ **Entrenar**, que abre el gimnasio: un botón por stat
  (💪 fuerza, 🪶 velocidad, ❤️ aguante).
- `gallos @alguien 500` le reta a un duelo de 500 Y$ por cabeza. El retado
  acepta o rechaza; quien reta puede retirarlo. Al aceptar se cobra a los dos y
  se abre la grada (`LOBBY_SECONDS`): el público apuesta por 🔴 uno u 🔵 otro a
  la porra, y cuando los dos dueños pulsan ✅ **¡Suelta el gallo!** (o se acaba
  el tiempo) empieza la pelea. El ganador se lleva el bote menos la comisión de
  la casa; la grada se reparte lo apostado entre los que acierten.

Cada pelea es un GIF en el reñidero (`bot.services.gallos_scene`, en Node con
canvas): los gallos entran, pelean por asaltos con sus picotazos, espolonazos
y patadas voladoras, y acaba por KO con estrellitas, porque uno sale por patas
o a los puntos. Reglas y cuotas en `bot.services.gallos`.

Dinero (`EconomyService`): juego, como el resto del casino (premios como
ganancia patrimonial de la base general, art. 33.1 LIRPF, y pérdidas que solo
compensan ganancias de juego, art. 33.5.d LIRPF): entra en la retención
diaria del casino y en la renta semanal. Contra la casa, `place_bet` al pelear
y `pay_winnings` con el premio (0 si pierde) antes de enseñar el GIF. En un
duelo, `place_bet` a los dos al aceptar y a cada apostante de grada al
apostar; al empezar la pelea se paga a todos con `pay_winnings` (0 a quien
pierde), antes del GIF: un apagado a mitad no deja a nadie sin cobrar. La
comisión del duelo y de la grada se destruye (es el margen de la casa, no un
impuesto). Si el bot se apaga con la grada abierta, se devuelve lo apostado.

**Entrenar** (`Gallos.train`) es una compra, no un juego: paga **IGIC general
(7 %)**, porque es una prestación de servicios hecha en Canarias a título
oneroso (art. 4 de la Ley 20/1991; tipo general del art. 51 de la Ley 4/2012).
Va por `EconomyService.purchase` con el motivo `gallos:entreno`: la base va a la
caja de la tienda, el IGIC al Estado y el stat sube en la misma transacción
(`GallosRepository.train`). Es un sumidero: no crea dinero. Sin IRPF (gastar no
es renta) y sin retención de juego (no es una apuesta).

**La forma.** El panel enseña la forma del gallo (`gallos.form_now`), la ventaja
que da contra el rival elegido (`gallos.house_edge`) y hasta qué apuesta llega
(`gallos.edge_cap`). La cuota de cada pelea sale de `gallos.house_odds` con la
forma que se ve en el panel, y la pelea gasta forma (`record_fight` con `now`).
Entrenar da forma. Los duelos no la gastan.

**Morir.** Quien pierde por KO, contra la casa o en un duelo, muere con
probabilidad `gallos.death_chance` (baja con el aguante y con la forma),
sorteada con el azar del cog. La ficha pasa al cementerio, el dueño empieza con un gallo
nuevo y se cuenta con una línea de luto. Morir no mueve dinero.

Solo se pelea en los canales del casino que elige `garito` (sin elegir, los de `CASINO_CHANNEL_IDS`)
y en sus hilos. Permisos del bot en el canal: enviar mensajes, insertar enlaces y adjuntar archivos.
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
import random
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from fractions import Fraction
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs import banco_compras as financiar
from bot.cogs import shop as tienda
from bot.cogs.casino import casino_channel_error, insufficient_text
from bot.repositories.gallos import GallosRepository
from bot.services import credit, skins
from bot.services import gallos_gym as gym
from bot.services import gallos_torneo as torneo
from bot.services.achievements import (
    StatDelta,
    casino_stats,
    gallos_bet_stats,
    gallos_drill_stats,
    gallos_stats,
    gallos_train_stats,
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
from bot.services.gallos import (
    AGE_FIGHTS,
    AGE_STEP,
    DEADLY_EDGE,
    DUEL_CUT,
    FORM_DECAY_PER_DAY,
    FORM_PER_FIGHT,
    FORM_PER_TRAIN,
    GAME,
    GRUDGE_DEATH,
    GRUDGE_EDGE,
    GRUDGE_WINS,
    HOUSE,
    INHERIT_SHARE,
    INJURY_HOURS,
    NAME_MAX,
    POOL_CUT,
    RIVAL_BY_KEY,
    STAT_MAX,
    TRAIN_CONCEPT,
    TRAIN_COOLDOWN,
    TRAIN_PER_DAY,
    Corner,
    Fallen,
    Fight,
    Finish,
    PoolBet,
    Rival,
    RoosterRecord,
    Stat,
    TrainError,
    aged,
    check_training,
    clean_name,
    dies,
    draw_winner,
    duel_chance,
    duel_prize,
    edge_cap,
    fight_injury,
    form_now,
    format_odds,
    grudge_against,
    house_edge,
    house_multiplier,
    house_odds,
    injure,
    injured,
    injury_text,
    narrate,
    pool_odds,
    pool_payouts,
    pool_totals,
    retire_error,
    simulate,
    stat_bar,
    train_gain,
    train_price,
    trains_today,
    with_form,
)
from bot.services.gallos_render import Fighter, Media, intro_state, meta_state
from bot.services.gallos_scene import GallosScene
from bot.services.levels import TIMEZONE, local_day
from bot.services.pets import Event, Moment, bet_moment
from bot.services.taxes import IGIC_GENERAL_RATE, TAX_COLLECTOR, igic
from bot.services.telemetry import TimedLock
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import ContextResponder, InteractionResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

DEFAULT_STAKE = 100
#: El rival de la casa que sale elegido al abrir el panel (el del 50 %).
DEFAULT_RIVAL = "falcon"
VIEW_TIMEOUT = 300
#: Cuánto espera un reto a que lo acepten.
CHALLENGE_SECONDS = 120
#: Cuánto está abierta la grada antes de soltar los gallos.
LOBBY_SECONDS = 60
#: Cuánto espera una invitación de sparring y cuánto hay para elegir en cada asalto.
SPARRING_OPEN_SECONDS = 300
SPARRING_PICK_SECONDS = 45
#: Desde aquí se anuncia en el canal una victoria contra la casa.
SHOUT_NET = 10_000
#: Margen tras el GIF antes de poner el PNG: lo que tarda en llegar y empezar.
REVEAL_MARGIN_SECONDS = 0.5
#: Victorias con las que retirar a un gallo cuenta como retirar a un campeón (logro).
GALLOS_CHAMPION_WINS = 100
#: Botones del panel que puede pulsar cualquiera, no solo el dueño.
PUBLIC_IDS = frozenset({f"{GAME}:feed", f"{GAME}:cheer", f"{GAME}:sabotage"})
GIF_NAME = "gallos.gif"
PNG_NAME = "gallos.png"
SKIN_GAME = "gallos"

COLOR_IDLE = discord.Color.from_rgb(214, 120, 40)
COLOR_WIN = discord.Color.from_rgb(255, 196, 0)
COLOR_LOST = discord.Color.from_rgb(80, 84, 92)
COLOR_DUEL = discord.Color.from_rgb(200, 30, 60)

WIN_LINES = (
    "¡Kikirikí!",
    "¡Ese es mi gallo!",
    "¡Dale, campeón!",
    "¡Toma mangó!",
    "¡Yo soy yo, y así me tienes que soportar!",
    "¡Gallo de pelea con escolta, como un ministro!",
    "¡El gallo con los huevos bien puestos!",
    "¡Victoria en el reñidero!",
    "¡Cresta alta y espolón afilado!",
    "¡Rey del corral!",
    "El rival se va con el rabo entre las plumas.",
    "¡Ni Rocky subía así las escaleras!",
    "¿Con pulpa? ¡Sin pulpa! ¡Con plumas!",
)
LOSE_LINES = (
    "¡Caldo de gallina!",
    "¡Al puchero!",
    "¡Nooo!",
    "¡Gallo en pepitoria!",
    "¡El gallo dimite! Algo inédito.",
    "Estoy destruido, pero por fuera… por dentro estoy nuevo.",
    "¡Más desplumado que un autónomo!",
    "Hoy no era su día.",
    "El gallo rival pegaba más fuerte.",
)
WAITING_LINES = (
    "🐓 Los gallos salen al ruedo…",
    "📣 ¡Hagan sus apuestas… ya no!",
    "🥁 Se calienta el reñidero…",
    "👀 Todo el barrio mirando el reñidero…",
    "☕ El barrio pide café y apuesta… ¡Quiero café!",
    "🪰 Quisiera ser una mosca… para verla desde el palco…",
    "🐓 Plumas al viento…",
    "📣 Trump anuncia que su gallo es el más grande…",
    "🔔 Suena la campana…",
    "🐓 Los gallos se miran de reojo…",
    "🦎 Lagartijo, iguana, lagarto… y gallo.",
)


#: Frases de entrenamiento por stat; `{name}` es el nombre del gallo.
TRAIN_LINES: dict[Stat, tuple[str, ...]] = {
    Stat.STRENGTH: (
        "**{name}** sube la escalera de Filadelfia con un cubo de alpiste. Rocky llora de orgullo.",
        "**{name}** levanta mancuernas hasta que le tiembla la cresta.",
        "**{name}** hace sentadillas con un saco de pienso a la espalda.",
        "**{name}** empuja un carro de la compra lleno cuesta arriba.",
        "**{name}** tira de la cuerda contra tres gallinas. Gana él.",
        "**{name}** levanta pesas y presume de pechuga en el espejo.",
        "**{name}** pide jugo de china de el bueno entre serie y serie. Sin pulpa.",
        "**{name}** entrena fuerza. El saco de boxeo pide la baja.",
    ),
    Stat.SPEED: (
        "**{name}** corre la vuelta al corral con viento de cola.",
        "**{name}** persigue una lombriz más rápido de lo que un ministro huye de una pregunta.",
        "**{name}** hace series de cien metros y deja al Correcaminos en ridículo.",
        "**{name}** esquiva conos de obra con más cintura que un torero.",
        "**{name}** sprinta por el patio y se le vuelan dos plumas.",
        "**{name}** entrena velocidad. Bip, bip.",
        "**{name}** corre detrás del camión del pienso y casi lo alcanza.",
    ),
    Stat.ENDURANCE: (
        "**{name}** aguanta una hora de reunión de vecinos sin pestañear. Es lo más duro que ha "
        "hecho.",
        "**{name}** hace fondo en el parque: diez kilómetros y ni una queja.",
        "**{name}** llega a Hacienda a las 14:59 y sale vivo. Ahora ya puede con todo.",
        "**{name}** aguanta un concierto de reguetón en primera fila.",
        "**{name}** hace plancha mientras suena un discurso de Rajoy: «un vaso es un vaso», y "
        "él, firme.",
        "**{name}** aguanta la siesta canaria sin dormirse. Héroe.",
        "**{name}** entrena aguante. El sol del mediodía se rinde antes que él.",
        "**{name}** pide café entre series: ¡quiero café! Y se lo toma con la cresta bien alta.",
    ),
}
#: Líneas de luto cuando muere un gallo; `{name}` es su nombre. Cuentan la pena, sin regodearse.
DEATH_LINES = (
    "**{name}** cuelga el espolón.",
    "Entierro de la sardina para **{name}**: bombo, capuchas de cartón y llanto de mentira.",
    "Tres días de luto por **{name}** en el reñidero.",
    "**{name}** descansa en paz.",
    "Funeral de Estado para **{name}**: banda de música y un discurso de Rajoy que no entiende "
    "nadie.",
    "**{name}** se va al corral grande.",
    "Cae **{name}**. El gallinero entero guarda un minuto de silencio.",
    "**{name}** pasa a mejor corral. El cementerio de gallos ya tiene lista de espera, como "
    "la sanidad.",
    "Adiós a **{name}**. Era un gallo de los de antes.",
    "Estoy destruido, pero por fuera… **{name}**, por dentro, ya no.",
    "Muere **{name}** y Erdoğan ofrece un mausoleo de mil habitaciones. Lo rechazan por humilde.",
    "**{name}** se despide del reñidero con la cresta alta.",
    "Adiós a **{name}**. ¿Con pulpa? ¡Sin pulpa! Ni pulpa ni espolón. Descansa en paz.",
    "**{name}** ya no cantará al amanecer. El barrio, por fin, duerme.",
)
#: Línea del entierro y del gallo nuevo; `{name}` es el caído y `{new}`, el que lo sustituye.
BURIAL_LINES = (
    "⚰️ **{name}** queda en el cementerio del reñidero. **{new}**, el gallo nuevo, ya picotea "
    "en su jaula: a cero y con ganas.",
    "⚰️ Quema de la sardina y a otra cosa: **{new}** entra al ruedo con el contador a cero.",
    "⚰️ Hacienda reclama la herencia de **{name}**. No hay: las plumas ya tributaron. "
    "**{new}** sale al corral.",
    "⚰️ Se inaugura la tumba de **{name}** con corte de cinta y sobrecoste. **{new}** ocupa la "
    "jaula vacía.",
    "⚰️ **{new}** llega al corral sin saber lo que le espera.",
    "⚰️ Rey muerto, gallo puesto: **{new}** empieza de cero.",
    "⚰️ **{name}** ya es historia, y la ficha se la guardan en el cementerio. **{new}** nace "
    "sin deudas y sin músculos.",
)


def _percent(chance: Fraction) -> str:
    return f"{float(chance) * 100:.0f} %"


def stat_line(record: RoosterRecord) -> str:
    """`💪 12 · 🪶 5 · ❤️ 0 · ⚡ poder 6`: los stats de un gallo en una línea."""
    parts = [f"{s.emoji} {record.stat(s)}" for s in Stat]
    return " · ".join([*parts, f"⚡ poder {record.power:.0f}"])


def stat_lines(record: RoosterRecord) -> list[str]:
    """Una línea con barra por stat y otra con el poder (la ficha del gallo)."""
    lines = [f"{s.emoji} **{s.label}** `{stat_bar(record.stat(s))}` {record.stat(s)}" for s in Stat]
    lines.append(f"⚡ **Poder** `{stat_bar(round(record.power))}` {record.power:.0f}")
    return lines


def form_line(form: int) -> str:
    """`🔋 Forma ███████░░░ 70`: la forma del gallo con su barra."""
    return f"🔋 **Forma** `{stat_bar(form)}` {form}"


def edge_line(rival: Rival, form: int, power: float) -> str:
    """Lo que la forma le saca a la casa contra `rival`, y hasta qué apuesta llega."""
    edge = house_edge(rival, form)
    if not edge:
        return (
            "Sin forma la casa paga su 95 % de siempre. Entrena para ganarle ventaja."
            if form <= 0
            else f"{rival.label} es tan flojo que la forma no te da ventaja contra él."
        )
    return (
        f"Ventaja por la forma: +{float(edge) * 100:.1f} % de lo apostado, hasta "
        f"{format_amount(edge_cap(power))} de apuesta. Cada pelea gasta forma."
    )


ROUND_NAMES = {4: "Cuartos", 2: "Semifinales", 1: "Final"}


def tournament_text(
    bracket: torneo.Bracket,
    records: list[tuple[int, RoosterRecord]],
    club_names: dict[int, str],
    now: float,
) -> str:
    """El anuncio del torneo del domingo con la cartelera de la semana."""
    champion = bracket.champion
    lines = [
        f"## 🏆 {champion.name} (<@{champion.user_id}>) gana el torneo",
        f"En la final, contra {bracket.finalist.name} (<@{bracket.finalist.user_id}>).",
    ]
    for matches in bracket.rounds:
        lines.append(f"**{ROUND_NAMES.get(len(matches), 'Ronda')}**")
        for match in matches:
            loser = match.blue if match.winner is match.red else match.red
            lines.append(f"-# {match.winner.name} 🆚 {loser.name}: gana {match.winner.name}")
    best = torneo.best_club(bracket)
    lines.append("### 📋 Cartelera de la semana")
    if best is not None and best[0] in club_names:
        lines.append(
            f"🏠 Gallera de la semana: **{club_names[best[0]]}** ({best[1]} combates ganados)"
        )
    alive = [(user, record) for user, record in records if record.fights]
    if alive:
        fit_user, fit = max(alive, key=lambda item: form_now(item[1], now))
        strong_user, strong = max(alive, key=lambda item: item[1].power)
        streak_user, streak = max(alive, key=lambda item: item[1].streak)
        lines.append(
            f"🔋 En mejor forma: **{fit.name}** (<@{fit_user}>), {round(form_now(fit, now))}"
        )
        lines.append(f"⚡ Más poder: **{strong.name}** (<@{strong_user}>), {strong.power:.0f}")
        if streak.streak:
            lines.append(
                f"🔥 Racha más larga: **{streak.name}** (<@{streak_user}>), "
                f"{streak.streak} seguidas"
            )
    lines.append(
        "-# Cada domingo por la noche, con los gallos más fuertes que han peleado esa semana."
    )
    return "\n".join(lines)


def killer_text(fallen: Fallen) -> str:
    """Quién mató a un gallo: el rival de la casa o el gallo de un miembro (o si se retiró)."""
    if fallen.killer_kind == "retiro":
        return "🎖️ retirado con honores"
    if fallen.killer_kind == "casa":
        rival = RIVAL_BY_KEY.get(fallen.killer)
        return f"a manos de {rival.label if rival else 'la casa'}"
    return f"a manos del gallo de <@{fallen.killer}>"


def fallen_lines(fallen: list[Fallen]) -> list[str]:
    """«🪦 En memoria de…»: los últimos gallos caídos, el más reciente primero."""
    if not fallen:
        return []
    lines = ["### 🪦 En memoria de… (y 🎖️ leyendas retiradas)"]
    for dead in fallen:
        lines.append(
            f"**{dead.name}** · {dead.fights} pelea{'s' if dead.fights != 1 else ''}, "
            f"{dead.wins} victoria{'s' if dead.wins != 1 else ''} · "
            f"💪 {dead.strength} 🪶 {dead.speed} ❤️ {dead.endurance} · {killer_text(dead)} "
            f"· <t:{int(dead.died_at)}:d>"
        )
    return lines


def mourning_lines(dead: RoosterRecord, new_name: str) -> list[str]:
    """El luto y el entierro de un gallo que muere (frases al azar de `DEATH_LINES`)."""
    return [
        f"## 🪦 {random.choice(DEATH_LINES).format(name=dead.name)}",
        random.choice(BURIAL_LINES).format(name=dead.name, new=new_name),
    ]


# -- Lo común a las dos mesas ------------------------------------------------------------


@dataclass(slots=True)
class Owner:
    """Un dueño y su gallo en una pelea."""

    member: discord.abc.User
    record: RoosterRecord
    skin: skins.Skin | None

    @property
    def skin_key(self) -> str:
        return self.skin.key if self.skin is not None else "serie"

    @property
    def paid_skin(self) -> bool:
        return self.skin is not None and not self.skin.free


def fighter_of(owner: Owner, odds: str = "") -> Fighter:
    """El gallo de un dueño tal como lo pinta la escena."""
    return Fighter(
        name=owner.record.name,
        tag=owner.member.display_name,
        skin=skins.params(SKIN_GAME, owner.skin),
        odds=odds,
    )


def fighter_of_rival(rival: Rival, multiplier: Fraction) -> Fighter:
    """Un gallo de la casa tal como lo pinta la escena, con la cuota de la pelea."""
    look = dict(skins.GAMES[SKIN_GAME].defaults)
    look.update(rival.look)
    look["key"] = f"casa_{rival.key}"
    return Fighter(
        name=rival.name,
        tag=rival.origin,
        skin=look,
        odds=format_odds(multiplier),
    )


def record_line(record: RoosterRecord) -> str:
    """`12 peleas · 8 victorias (5 KO) · racha 3 · mejor 5`."""
    if not record.fights:
        return "Aún no ha pisado el reñidero."
    parts = [
        f"{record.fights} pelea{'s' if record.fights != 1 else ''}",
        f"{record.wins} victoria{'s' if record.wins != 1 else ''} ({record.kos} KO)",
    ]
    if record.streak:
        parts.append(f"🔥 racha {record.streak}")
    parts.append(f"mejor racha {record.best_streak}")
    return " · ".join(parts)


class NameModal(ui.Modal, title="✏️ Bautiza a tu gallo"):
    """Pide el nombre del gallo."""

    name: ui.TextInput = ui.TextInput(
        label="Nombre", placeholder="Kikirikí", max_length=NAME_MAX, min_length=1
    )

    def __init__(self, panel: HousePanel) -> None:
        super().__init__()
        self.panel = panel

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Valida el nombre, lo guarda y repinta el panel."""
        try:
            name = clean_name(str(self.name.value))
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        # Guardarlo y repintar van a la base de datos y al pintor: se acepta antes.
        await ack(interaction)
        await self.panel.rename(interaction, name)


class ClubModal(ui.Modal, title="🏠 Funda una gallera"):
    """Pide el nombre de la gallera nueva."""

    name: ui.TextInput = ui.TextInput(
        label="Nombre",
        placeholder="Los del Pino",
        max_length=torneo.CLUB_NAME_MAX,
        min_length=1,
    )

    def __init__(self, panel: HousePanel) -> None:
        super().__init__()
        self.panel = panel

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Valida el nombre y funda la gallera (se guarda: se acepta antes)."""
        try:
            name = torneo.clean_club_name(str(self.name.value))
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await ack(interaction)
        await self.panel.found_club(interaction, name)


# -- Contra la casa ----------------------------------------------------------------------


class HousePanel(ui.View):
    """El reñidero de un miembro: su gallo contra los de la casa.

    No guarda dinero: se cobra y se paga siempre por la economía.
    """

    def __init__(self, cog: Gallos, *, guild_id: int, owner: discord.abc.User, stake: int) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        self.stake = stake
        self.rival = RIVAL_BY_KEY[DEFAULT_RIVAL]
        self.record = RoosterRecord(name="")
        self.balance = 0
        #: Si se ve el gimnasio (🏋️) en lugar del reñidero.
        self.training = False
        #: El minijuego de la sesión que se acaba de pagar (`None` si no hay ninguno).
        self.drill: Drill | None = None
        #: ☠️ A muerte: paga más, pero perder por KO mata seguro.
        self.deadly = False
        #: Si ya se ha pulsado 🎖️ Retirarlo una vez (hay que confirmarlo con otra).
        self.confirm_retire = False
        #: La vista de la gallera (🏠): la del dueño y las que tienen hueco, ya leídas.
        self.club_view = False
        self.club: torneo.Club | None = None
        self.open_clubs: list[torneo.Club] = []
        self.club_lines: list[str] = []
        #: Qué presentación se ve ahora (rival, nombre, apuesta, poder); `None` si otra imagen.
        self._poster: tuple[object, ...] | None = None
        self.channel: object = None
        self.message: discord.Message | None = None
        self.result: str | None = None
        self._busy = False
        self._lock = TimedLock("gallos:HousePanel._lock")
        self._last_interaction: discord.Interaction | None = None

    # -- Texto ------------------------------------------------------------------------

    @property
    def power(self) -> float:
        """Poder del gallo del panel (0 a 100)."""
        return self.record.power

    @property
    def form(self) -> int:
        """Forma del gallo ahora mismo, redondeada (la que fija la cuota)."""
        return round(form_now(self.record, self.cog.wall_clock()))

    def odds(self, rival: Rival | None = None) -> tuple[Fraction, Fraction]:
        """Probabilidad y cuota contra `rival` (el elegido) con la apuesta, poder y forma.

        Con ☠️ a muerte y el pique del rival, si lo hay.
        """
        rival = rival or self.rival
        return house_odds(
            rival,
            self.power,
            self.form,
            self.stake,
            deadly=self.deadly,
            grudge=grudge_against(self.record, rival.key),
        )

    def description(self) -> str:
        """Texto del embed: tu gallo, el rival elegido y, si lo hay, el último resultado."""
        rival = self.rival
        chance, multiplier = self.odds()
        lines = []
        if self.result:
            lines.append(self.result)
        lines.append(f"### 🐓 {self.record.name}")
        lines.append(f"-# {record_line(self.record)}")
        lines.append(f"-# {stat_line(self.record)}")
        lines.append(form_line(self.form))
        if aged(self.record):
            lines.append(
                f"🎂 Veterano: con {AGE_FIGHTS} peleas o más pierde 1 de cada stat cada "
                f"{AGE_STEP} peleas. Retirarlo (🏋️) da una cría que hereda parte de sus stats."
            )
        if injured(self.record, self.cog.wall_clock()):
            lines.append(injury_text(self.record, self.cog.wall_clock()))
        lines.append(f"### 🆚 {rival.label}")
        lines.append(f"*{rival.blurb}*")
        lines.append(
            f"Cuota **{format_odds(multiplier)}** · le ganas el "
            f"{_percent(chance)} de las veces · si ganas, cobras "
            f"**{format_amount(int(self.stake * multiplier))}**"
        )
        lines.append(f"-# {edge_line(rival, self.form, self.power)}")
        if grudge_against(self.record, rival.key):
            lines.append(
                f"🔥 **{rival.label} está picado** contigo: le has ganado "
                f"{self.record.grudge_wins} seguidas. La ventaja de la forma vale "
                f"×{float(GRUDGE_EDGE):g}".replace(".", ",")
                + f", pero si pierdes por KO hay ×{GRUDGE_DEATH:g}".replace(".", ",")
                + " de probabilidades de morir."
            )
        if self.deadly:
            lines.append(
                f"☠️ **A muerte:** la casa paga hasta un +{float(DEADLY_EDGE) * 100:g} % más "
                "(según lo duro que sea el rival), pero **si pierdes por KO, tu gallo muere**."
            )
        lines.append(
            f"-# Apuesta {format_amount(self.stake)} · Saldo {format_amount(self.balance)}"
        )
        if self.balance == 0:
            lines.append("**Estás a cero.** `imv` te recarga.")
        return "\n".join(lines)

    def gym_text(self) -> str:
        """Texto del gimnasio: los stats, lo que cuesta cada uno y cuánto queda de cupo."""
        now = self.cog.wall_clock()
        today = trains_today(self.record, local_day(now))
        lines = []
        if self.result:
            lines.append(self.result)
        lines.append(f"### 🏋️ Gimnasio de {self.record.name}")
        lines += stat_lines(self.record)
        lines.append(form_line(round(form_now(self.record, now))))
        trait = gym.trait_for(self.owner.id, self.record)
        lines.append(f"-# Personalidad: {trait.label}. {trait.blurb}")
        if combo := gym.combo_now(self.record, today_day := local_day(now)):
            lines.append(
                f"🔥 Combo de hoy: {combo} sesión{'es' if combo != 1 else ''} buena"
                f"{'s' if combo != 1 else ''} seguida{'s' if combo != 1 else ''} "
                f"(×{gym.combo_factor(combo):.1f} de forma en la siguiente)".replace(".", ",")
            )
        del today_day
        if injured(self.record, now):
            lines.append(injury_text(self.record, now) + " Entrenar sí puede.")
        lines.append(
            f"-# Entrenar sube un stat +3 hasta el 40, +2 hasta el 70 y +1 después, sin pasar "
            f"de {STAT_MAX} (un stat al máximo solo da forma). Después juegas un minijuego: "
            f"según la nota, la sesión da de {FORM_PER_TRAIN // 2} a "
            f"{FORM_PER_TRAIN * 3 // 2} de forma, y las sesiones buenas seguidas del día, "
            "más. Cuesta lo que marca cada botón (IGIC del "
            f"{IGIC_GENERAL_RATE * 100:g} % incluido) y sube con el nivel."
        )
        lines.append(
            f"-# La forma baja {FORM_DECAY_PER_DAY} al día y cada pelea contra la casa gasta "
            f"{FORM_PER_FIGHT}. Con forma, la casa paga más contra los rivales duros; agotado, "
            "tu gallo tiene más papeletas de morir si cae por KO."
        )
        prices = [train_price(self.record.stat(s)) for s in Stat if self.record.stat(s) < STAT_MAX]
        if prices and (hint := financiar.teaser(min(prices) + igic(min(prices)), "gallos")):
            lines.append(f"-# {hint}.")
        lines.append(
            f"-# Hoy {today}/{TRAIN_PER_DAY} entrenamientos · una hora de descanso entre uno y "
            f"otro · Saldo {format_amount(self.balance)}"
        )
        wait = self.record.last_train + TRAIN_COOLDOWN - now
        if wait > 0:
            lines.append(f"⏳ Tu gallo descansa {math.ceil(wait / 60)} min más.")
        return "\n".join(lines)

    def club_text(self) -> str:
        """Texto de la gallera: la tuya con sus miembros, o cómo fundar o entrar en una."""
        lines = []
        if self.result:
            lines.append(self.result)
        if self.club is None:
            lines.append("### 🏠 Galleras")
            lines.append(
                f"Una gallera es un club de hasta {torneo.CLUB_MAX} miembros. Cada vez que uno "
                f"acaba un minijuego de entrenamiento, los demás ganan +{torneo.CLUB_FORM} de "
                f"forma (hasta {torneo.CLUB_FORM_DAILY} al día), y el torneo del domingo elige "
                "la gallera de la semana. Funda una o entra en una que tenga hueco."
            )
            if not self.open_clubs:
                lines.append("-# Aún no hay galleras con hueco en el servidor.")
            return "\n".join(lines)
        club = self.club
        lines.append(f"### 🏠 {club.name}")
        lines.append(
            f"-# {len(club.members)}/{torneo.CLUB_MAX} miembros · fundada por <@{club.owner_id}>"
        )
        lines += self.club_lines
        lines.append(
            f"-# Cuando un miembro acaba un minijuego, los demás ganan +{torneo.CLUB_FORM} de "
            f"forma (hasta {torneo.CLUB_FORM_DAILY} al día)."
        )
        return "\n".join(lines)

    def drill_text(self) -> str:
        """Texto del minijuego en marcha (o de la decisión del suplemento)."""
        drill = self.drill
        assert drill is not None
        stat = drill.stat
        lines = [f"### {gym.TITLES[drill.game.kind]} · {stat.emoji} {stat.label}"]
        if drill.event is not None:
            lines.append(drill.result)
            lines.append(gym.event_text(drill.event, self.record.name))
            return "\n".join(lines)
        lines.append(drill.game.text())
        if drill.coach is not None:
            lines.append(f"🧑‍🏫 Lo juega **{drill.coach.display_name}**.")
        elif drill.coach_open:
            lines.append("🧑‍🏫 Cualquiera puede jugarlo: el primero que pulse se queda con él.")
        if drill.cheers:
            lines.append(
                f"📣 {len(drill.cheers)} ánimo{'s' if len(drill.cheers) != 1 else ''} del público: "
                f"+{round((gym.cheer_factor(len(drill.cheers)) - 1) * 100)} % de forma."
            )
        if drill.saboteur is not None:
            lines.append(
                f"🍌 **{drill.saboteur.display_name}** ha tirado una cáscara de plátano: la nota "
                f"bajará {round(gym.SABOTAGE_PENALTY * 10)} puntos."
            )
        trait = gym.trait_for(self.owner.id, self.record)
        factor = gym.trait_factor(trait, stat)
        combo = gym.combo_now(self.record, local_day(self.cog.wall_clock()))
        extras = []
        if combo:
            extras.append(f"🔥 combo ×{gym.combo_factor(combo):.1f}".replace(".", ","))
        if factor != 1:
            extras.append(f"{trait.label} ×{factor:.1f}".replace(".", ","))
        lines.append(
            "-# La sesión ya está pagada y el stat ya ha subido. El minijuego suma forma según "
            f"la nota (de ×0,5 a ×1,5) y con {round(gym.PERFECT_SCORE * 10)}/10 o más, +1 al "
            "stat. Si lo dejas, te quedas con lo pagado."
            + (f" Ahora: {' · '.join(extras)}." if extras else "")
        )
        return "\n".join(lines)

    def embed(
        self, *, image: str = PNG_NAME, text: str | None = None, color: discord.Color | None = None
    ) -> discord.Embed:
        """Embed con la imagen adjunta (`attachment://`)."""
        if text is None:
            if self.drill is not None:
                text = self.drill_text()
            elif self.club_view:
                text = self.club_text()
            else:
                text = self.gym_text() if self.training else self.description()
        embed = discord.Embed(
            title=f"🐓 Reñidero de {self.owner.display_name}",
            description=text,
            color=color or COLOR_IDLE,
        )
        embed.set_image(url=f"attachment://{image}")
        return embed

    # -- Componentes ------------------------------------------------------------------

    def _rebuild_gym(self, *, busy: bool) -> None:
        """Los botones del gimnasio: uno por stat, con lo que sube y lo que cuesta."""
        today = trains_today(self.record, local_day(self.cog.wall_clock()))
        for stat in Stat:
            level = self.record.stat(stat)
            base = train_price(level)
            goes_to = min(STAT_MAX, level + train_gain(level))
            label = (
                f"{stat.emoji} {stat.label} al máximo · forma"
                if level >= STAT_MAX
                else f"{stat.emoji} {stat.label} {level} → {goes_to}"
            ) + f" · {format_amount(base + igic(base))}"
            self._button(
                label,
                f"train_{stat.value}",
                self._trainer(stat),
                style=discord.ButtonStyle.primary,
                row=0,
                disabled=busy or today >= TRAIN_PER_DAY,
            )
        self._button("↩️ Volver al reñidero", "train_back", self._leave_gym, row=1, disabled=busy)
        self._button("🌽 Echarle de comer", "feed", self._feed, row=2, disabled=busy)
        self._button(
            "🎖️ ¿Seguro? Pulsa otra vez" if self.confirm_retire else "🎖️ Retirarlo",
            "retire",
            self._retire,
            style=discord.ButtonStyle.danger
            if self.confirm_retire
            else discord.ButtonStyle.secondary,
            row=2,
            disabled=busy,
        )
        self._button(
            "🤝 Buscar sparring",
            "spar_open",
            self._open_sparring,
            row=2,
            disabled=busy or today >= TRAIN_PER_DAY,
        )
        for stat in Stat:
            level = self.record.stat(stat)
            base = train_price(level)
            if credit.refusal(base + igic(base)) is None:
                self._button(
                    f"{financiar.LABEL} {stat.emoji} {stat.label}",
                    f"financiar_{stat.value}",
                    self._financer(stat),
                    row=1,
                    disabled=busy or today >= TRAIN_PER_DAY,
                )

    def _rebuild_drill(self, *, busy: bool) -> None:
        """Los botones del minijuego (o de la decisión del suplemento), con `custom_id` fijo."""
        drill = self.drill
        assert drill is not None
        if drill.event is not None:
            self._button(
                "💊 Tomárselo", "evt_yes", self._supplement_yes, style=discord.ButtonStyle.danger,
                row=0, disabled=busy,
            )  # fmt: skip
            self._button("🚫 Ni de broma", "evt_no", self._supplement_no, row=0, disabled=busy)
            return
        for button in drill.game.buttons():
            self._button(
                button.label,
                f"gym:{button.key}",
                self._presser(button.key),
                style=getattr(discord.ButtonStyle, button.style),
                row=button.row,
                disabled=busy or button.disabled,
            )
        self._button("🏳️ Dejarlo", "gym_quit", self._quit_drill, row=4, disabled=busy)
        if drill.coach is None:
            self._button(
                "🧑‍🏫 Que lo juegue otro" if not drill.coach_open else "🧑‍🏫 Lo juego yo",
                "gym_coach",
                self._toggle_coach,
                row=4,
                disabled=busy,
            )
        self._button(
            f"📣 Animar ({len(drill.cheers)}/{gym.CHEER_MAX})",
            "cheer",
            self._cheer,
            style=discord.ButtonStyle.success,
            row=4,
            disabled=busy or len(drill.cheers) >= gym.CHEER_MAX,
        )
        self._button(
            f"🍌 Sabotear · {format_amount(gym.SABOTAGE_PRICE + igic(gym.SABOTAGE_PRICE))}",
            "sabotage",
            self._sabotage,
            row=4,
            disabled=busy or drill.saboteur is not None,
        )

    def _rebuild_club(self, *, busy: bool) -> None:
        """Los botones de la gallera: fundar o entrar si no tienes, salir si tienes."""
        if self.club is None:
            self._button(
                "➕ Fundar gallera", "club_found", self._found_club_modal,
                style=discord.ButtonStyle.success, row=1, disabled=busy,
            )  # fmt: skip
            if self.open_clubs:
                select: ui.Select = ui.Select(
                    placeholder="Entrar en una gallera…",
                    row=0,
                    disabled=busy,
                    custom_id=f"{GAME}:club_join",
                    options=[
                        discord.SelectOption(
                            label=club.name[:100],
                            value=str(club.id),
                            description=f"{len(club.members)}/{torneo.CLUB_MAX} miembros",
                        )
                        for club in self.open_clubs[:25]
                    ],
                )
                select.callback = self._join_club  # type: ignore[method-assign]
                self.add_item(select)
        else:
            self._button(
                "🚪 Salir de la gallera", "club_leave", self._leave_club, row=1, disabled=busy
            )
        self._button("↩️ Volver al reñidero", "club_back", self._leave_club_view, row=1,
                     disabled=busy)  # fmt: skip

    def rebuild(self, *, busy: bool = False) -> None:
        """Vuelve a montar el desplegable y los botones con el estado actual."""
        self.clear_items()
        if self.drill is not None:
            self._rebuild_drill(busy=busy)
            return
        if self.club_view:
            self._rebuild_club(busy=busy)
            return
        if self.training:
            self._rebuild_gym(busy=busy)
            return
        select: ui.Select = ui.Select(
            placeholder="Elige rival",
            row=0,
            disabled=busy,
            custom_id=f"{GAME}:rival",
            options=[
                discord.SelectOption(
                    label=f"{r.name} · {format_odds(odds[1])}"[:100],
                    value=r.key,
                    emoji=r.emoji,
                    description=(f"Poder {r.power} · le ganas el {_percent(odds[0])}. {r.blurb}")[
                        :100
                    ],
                    default=r.key == self.rival.key,
                )
                for r, odds in ((r, self.odds(r)) for r in HOUSE)
            ],
        )
        select.callback = self._choose_rival  # type: ignore[method-assign]
        self.add_item(select)
        self._button(
            f"🐓 ¡A pelear! · {format_amount(self.stake)}",
            "fight",
            self._fight,
            style=discord.ButtonStyle.danger,
            row=1,
            disabled=busy,
        )
        self._button(
            "☠️ A muerte: sí" if self.deadly else "☠️ A muerte: no",
            "deadly",
            self._toggle_deadly,
            style=discord.ButtonStyle.danger if self.deadly else discord.ButtonStyle.secondary,
            row=3,
            disabled=busy,
        )
        self._button("½", "half", self._halve, row=1, disabled=busy)
        self._button("×2", "x2", self._double, row=1, disabled=busy)
        self._button("💰 All-in", "allin", self._all_in, row=1, disabled=busy)
        self._button("✏️ Nombre", "name", self._name, row=2, disabled=busy)
        self._button("🏋️ Entrenar", "train", self._gym, row=2, disabled=busy)
        self._button("🌽 Echarle de comer", "feed", self._feed, row=3, disabled=busy)
        self._button("🏠 Gallera", "club", self._open_club_view, row=3, disabled=busy)
        if (button := tienda.skin_button(self.cog.bot, SKIN_GAME, row=2)) is not None:
            button.disabled = busy
            self.add_item(button)

    def _button(
        self,
        label: str,
        custom_id: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        row: int,
        disabled: bool = False,
    ) -> None:
        button: ui.Button = ui.Button(
            label=label, style=style, disabled=disabled, custom_id=f"{GAME}:{custom_id}", row=row
        )
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)

    def disable_all(self) -> None:
        """Apaga todo (panel caducado o pelea en marcha)."""
        for item in self.children:
            if isinstance(item, ui.Button | ui.Select):
                item.disabled = True

    # -- Ciclo de vida ----------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Solo el dueño pelea con su gallo; los demás, lo que es para ellos.

        Cualquiera puede echarle de comer y animar o sabotear su minijuego; si el
        dueño lo deja, otro lo juega en su lugar (el entrenador). Cuando hay
        entrenador, el dueño ya no toca los botones del minijuego.
        """
        custom_id = str((interaction.data or {}).get("custom_id", ""))
        user_id = interaction.user.id
        drill = self.drill
        game_button = custom_id.startswith(f"{GAME}:gym:")
        if game_button and drill is not None and drill.coach is not None:
            if user_id == drill.coach.id:
                return True
            message = f"Este minijuego lo está jugando {drill.coach.display_name}."
        elif user_id == self.owner.id:
            return True
        elif custom_id in PUBLIC_IDS or (
            game_button and drill is not None and drill.coach_open and drill.coach is None
        ):
            return True
        else:
            message = f"Este gallo es de {self.owner.display_name}. Saca el tuyo con `gallos`."
        await interaction.response.send_message(message, ephemeral=True)
        return False

    async def on_timeout(self) -> None:
        """Caduca: apaga los botones."""
        self.cog.panels.discard(self)
        self.disable_all()
        try:
            if self._last_interaction is not None:
                await self._last_interaction.edit_original_response(view=self)
            elif self.message is not None:
                await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug("No se pudo cerrar el reñidero", exc_info=True)

    # -- Imagen -----------------------------------------------------------------------

    def _owner(self) -> Owner:
        return Owner(
            self.owner,
            self.record,
            tienda.skin_of(self.cog.bot, self.guild_id, self.owner.id, SKIN_GAME),
        )

    def _meta(
        self, seed: int, owner: Owner | None = None, multiplier: Fraction | None = None
    ) -> dict[str, Any]:
        """La presentación; con `owner` se pinta ese gallo (el que peleó, aunque haya muerto).

        `multiplier` es la cuota de la pelea ya jugada (la de antes de gastar forma).
        """
        owner = owner or self._owner()
        multiplier = self.odds()[1] if multiplier is None else multiplier
        return meta_state(
            fighter_of(owner, odds=format_amount(self.stake)),
            fighter_of_rival(self.rival, multiplier),
            title="PELEA DE GALLOS",
            subtitle=f"Contra la casa · {format_odds(multiplier)}",
            seed=seed,
        )

    async def poster(self) -> bytes:
        """PNG de la presentación: tu gallo frente al rival elegido."""
        return await self.cog.renderer.still(self._meta(0), intro_state())

    async def repaint(self, interaction: discord.Interaction) -> None:
        """Repinta la presentación, el texto y los botones."""
        key = (self.rival, self.record.name, self.stake, self.power, self.form, self.odds())
        if key == self._poster:
            # Ya se ve esta misma presentación: subirla otra vez cuesta ~1 s y no cambia nada.
            self.rebuild()
            self._last_interaction = interaction
            await edit(interaction, embed=self.embed(), view=self)
            return
        png = await self.poster()
        self.rebuild()
        self._last_interaction = interaction
        await edit(
            interaction,
            embed=self.embed(),
            attachments=[discord.File(io.BytesIO(png), filename=PNG_NAME)],
            view=self,
        )
        self._poster = key

    # -- Botones ----------------------------------------------------------------------

    async def _choose_rival(self, interaction: discord.Interaction) -> None:
        """Elige rival: cambia la presentación (dibujar es lento: se acepta antes)."""
        values = interaction.data.get("values", []) if interaction.data else []
        await ack(interaction)
        if self._busy or not values or values[0] not in RIVAL_BY_KEY:
            return
        self.rival = RIVAL_BY_KEY[values[0]]
        self.result = None
        await self.repaint(interaction)

    async def _toggle_deadly(self, interaction: discord.Interaction) -> None:
        """☠️ A muerte: cambia la cuota, así que se repinta (se acepta antes)."""
        await ack(interaction)
        if self._busy:
            return
        self.deadly = not self.deadly
        self.result = None
        await self.repaint(interaction)

    async def _name(self, interaction: discord.Interaction) -> None:
        """✏️ Nombre: abre el formulario (tiene que ser la primera respuesta)."""
        if self._busy:
            await interaction.response.send_message("Espera a que acabe la pelea.", ephemeral=True)
            return
        await interaction.response.send_modal(NameModal(self))

    async def rename(self, interaction: discord.Interaction, name: str) -> None:
        """Guarda el nombre nuevo (ya validado) y repinta."""
        before = self.record.name
        self.record = await self.cog.repository.rename(self.guild_id, self.owner.id, name)
        await self.repaint(interaction)
        if name != before:
            await logros.track(
                self.cog.bot,
                self.guild_id,
                self.owner,
                self.channel,
                StatDelta(add={"gallos_renamed": 1}),
            )

    # -- Gimnasio ---------------------------------------------------------------------

    async def _gym(self, interaction: discord.Interaction) -> None:
        """🏋️ Entrenar: abre el gimnasio. Solo cambia memoria, así que contesta directo."""
        if self._busy:
            await interaction.response.send_message("Espera a que acabe la pelea.", ephemeral=True)
            return
        if (self.guild_id, self.owner.id) in self.cog.busy:
            await interaction.response.send_message(
                "Tu gallo está en un duelo: nada de gimnasio hasta que acabe.", ephemeral=True
            )
            return
        self.training = True
        self.result = None
        self.rebuild()
        self._last_interaction = interaction
        await edit(interaction, embed=self.embed(), view=self)

    async def _leave_gym(self, interaction: discord.Interaction) -> None:
        """↩️ Volver al reñidero: la cuota cambia con el poder, así que se repinta la imagen."""
        await ack(interaction)
        if self._busy:
            return
        self.training = False
        self.result = None
        await self.repaint(interaction)

    def _trainer(self, stat: Stat) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await self._train(interaction, stat)

        return callback

    async def _train(self, interaction: discord.Interaction, stat: Stat) -> None:
        """Entrena un stat: cobra con IGIC y sube el stat en la misma transacción."""
        if self._busy:
            await interaction.response.send_message(
                "Espera, que ya se está moviendo.", ephemeral=True
            )
            return
        # Cobrar y guardar van a la base de datos: se acepta el clic ya.
        await ack(interaction)
        self._busy = True
        self._last_interaction = interaction
        training: Training | None = None
        try:
            outcome = await self.cog.train(self.guild_id, self.owner.id, self.record, stat)
            if isinstance(outcome, str):
                self.result = f"❌ {outcome}"
            else:
                training = outcome
                self.record, self.balance = outcome.after, outcome.balance
                self._start_drill(outcome, await self._train_text(outcome))
            self.rebuild()
            await edit(interaction, embed=self.embed(), view=self)
        except discord.HTTPException:
            logger.warning("No se pudo enseñar un entrenamiento de gallos", exc_info=True)
        finally:
            self._busy = False
        if training is not None:
            await self._after_train(interaction, training)

    def _financer(self, stat: Stat) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await self._finance_train(interaction, stat)

        return callback

    async def _finance_train(self, interaction: discord.Interaction, stat: Stat) -> None:
        """💳 Financiar un entrenamiento: la oferta del banco, en privado; el panel se repinta."""
        if self._busy:
            await interaction.response.send_message(
                "Espera, que ya se está moviendo.", ephemeral=True
            )
            return
        # El precio de la oferta es el de este panel; si el gallo cambió, `train` ya se queja.
        base = train_price(self.record.stat(stat))
        done: list[Training] = []

        async def buy(_interaction: discord.Interaction) -> str | None:
            outcome = await self.cog.train(self.guild_id, self.owner.id, self.record, stat)
            if isinstance(outcome, str):
                return outcome
            done.append(outcome)
            return None

        async def after(after_interaction: discord.Interaction) -> None:
            training = done[0]
            self.record, self.balance = training.after, training.balance
            self._start_drill(training, await self._train_text(training))
            self.rebuild()
            try:
                if self._last_interaction is not None:
                    await self._last_interaction.edit_original_response(
                        embed=self.embed(), view=self
                    )
                elif self.message is not None:
                    await self.message.edit(embed=self.embed(), view=self)
            except discord.HTTPException:
                logger.warning("No se pudo repintar el reñidero tras financiar", exc_info=True)
            await self._after_train(after_interaction, training)

        purchase = credit.Purchase(
            f"🏋️ Entrenar a {self.record.name}: {stat.label}", base + igic(base), "gallos"
        )
        deal = financiar.Deal(purchase, buy, after=after)
        if not await financiar.open_finance(self.cog.bot, interaction, deal):
            await interaction.response.send_message(
                "El banco está cerrado ahora mismo.", ephemeral=True
            )

    def _start_drill(self, training: Training, paid: str) -> None:
        """Empieza el minijuego de una sesión recién pagada (uno de los dos de su stat)."""
        rng = self.cog.rng
        game = gym.start(gym.pick_kind(training.stat, rng), rng, self.cog.wall_clock())
        self.drill = Drill(stat=training.stat, game=game, paid=paid)
        self.result = None

    def _presser(self, key: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await self._press(interaction, key)

        return callback

    async def _press(self, interaction: discord.Interaction, key: str) -> None:
        """Un botón del minijuego: solo cambia memoria hasta que acaba (entonces, guarda)."""
        drill = self.drill
        if drill is None or drill.event is not None or self._busy:
            await interaction.response.defer()
            return
        if drill.coach is None and drill.coach_open and interaction.user.id != self.owner.id:
            drill.coach = interaction.user
        drill.game.press(key, self.cog.wall_clock())
        if not drill.game.done:
            self.rebuild()
            self._last_interaction = interaction
            await interaction.response.edit_message(embed=self.embed(), view=self)
            return
        # Acaba: la forma y el combo se guardan en la base de datos, así que se acepta antes.
        await ack(interaction)
        await self._finish_drill(interaction)

    async def _finish_drill(self, interaction: discord.Interaction) -> None:
        """Guarda lo que deja el minijuego, sortea un evento y enseña el resultado."""
        drill = self.drill
        assert drill is not None
        self._busy = True
        self._last_interaction = interaction
        now = self.cog.wall_clock()
        day = local_day(now)
        trait = gym.trait_for(self.owner.id, self.record)
        event = gym.roll_event(self.cog.rng)
        sessions: list[gym.Session] = []

        score = gym.drill_score(drill.game.score, drill.saboteur is not None)

        def change(record: RoosterRecord) -> RoosterRecord:
            after, session = gym.after_session(
                record,
                drill.stat,
                score,
                trait=trait,
                now=now,
                day=day,
                cheers=len(drill.cheers),
            )
            sessions.append(session)
            if event is not None and not event.choice:
                after = gym.apply_event(after, event, now)
            return after

        try:
            before, self.record = await self.cog.repository.change(
                self.guild_id, self.owner.id, change
            )
            session = sessions[-1]
            drill.result = self._session_text(drill, before, session, event)
            if event is not None and event.choice:
                drill.event = event
            else:
                self.drill = None
                self.result = drill.result + "\n" + drill.paid
            self.rebuild()
            await edit(interaction, embed=self.embed(), view=self)
        except discord.HTTPException:
            logger.warning("No se pudo enseñar el final de un minijuego de gallos", exc_info=True)
            return
        finally:
            self._busy = False
        await self.cog.share_club_form(self.guild_id, self.owner.id, now)
        if drill.coach is not None:
            await logros.track(
                self.cog.bot,
                self.guild_id,
                drill.coach,
                self.channel,
                StatDelta(
                    add={
                        "gallos_coached": 1,
                        "gallos_coached_perfect": int(session.score >= gym.PERFECT_SCORE),
                    }
                ),
            )
        if drill.saboteur is not None and session.score >= gym.PERFECT_SCORE:
            await logros.track(
                self.cog.bot,
                self.guild_id,
                self.owner,
                self.channel,
                StatDelta(add={"gallos_sabotaged_perfect": 1}),
            )
        await logros.track(
            self.cog.bot,
            self.guild_id,
            self.owner,
            self.channel,
            gallos_drill_stats(
                kind=drill.game.kind,
                score=session.score,
                bonus=session.bonus,
                combo=session.combo,
                event=event.key if event is not None else None,
                form=round(form_now(self.record, now)),
            ),
        )

    def _session_text(
        self,
        drill: Drill,
        before: RoosterRecord,
        session: gym.Session,
        event: gym.GymEvent | None,
    ) -> str:
        """El resultado del minijuego: la nota, la forma, el combo y el +1 si lo hay."""
        now = self.cog.wall_clock()
        grade = round(session.score * 10)
        lines = [
            f"# {gym.TITLES[drill.game.kind]}: {grade}/10",
            drill.game.summary(),
            f"🔋 Forma {round(form_now(before, now))} → {round(form_now(self.record, now))} "
            f"(+{round(session.form)} del minijuego)",
        ]
        if drill.saboteur is not None:
            lines.append(
                f"🍌 Saboteado por {drill.saboteur.display_name}: "
                f"{round(drill.game.score * 10)}/10 se queda en {grade}/10."
            )
        if drill.coach is not None:
            lines.append(f"🧑‍🏫 Lo jugó {drill.coach.display_name}.")
        if session.bonus:
            stat = drill.stat
            lines.append(
                f"⭐ Sesión perfecta: {stat.emoji} {stat.label} +{session.bonus} "
                f"({self.record.stat(stat)})."
            )
        if session.combo >= 2:
            lines.append(f"🔥 {session.combo} sesiones buenas seguidas hoy.")
        if event is not None and not event.choice:
            lines.append(gym.event_text(event, self.record.name))
        return "\n".join(lines)

    async def _quit_drill(self, interaction: discord.Interaction) -> None:
        """🏳️ Dejarlo: cierra el minijuego sin tocar nada (lo pagado ya está guardado)."""
        drill = self.drill
        if drill is None or self._busy:
            await interaction.response.defer()
            return
        self.drill = None
        self.result = "🏳️ Dejas el minijuego. Te quedas con lo pagado.\n" + drill.paid
        self.rebuild()
        self._last_interaction = interaction
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _supplement_yes(self, interaction: discord.Interaction) -> None:
        """💊 Tomárselo: sube el stat y sortea la lesión (se guarda: se acepta antes)."""
        drill = self.drill
        if drill is None or drill.event is None or self._busy:
            await interaction.response.defer()
            return
        await ack(interaction)
        self._busy = True
        self._last_interaction = interaction
        now = self.cog.wall_clock()
        hurt: list[bool] = []

        def change(record: RoosterRecord) -> RoosterRecord:
            after, injured_now = gym.take_supplement(record, drill.stat, self.cog.rng, now)
            hurt.append(injured_now)
            return after

        try:
            before, self.record = await self.cog.repository.change(
                self.guild_id, self.owner.id, change
            )
            stat = drill.stat
            line = (
                f"💊 Se lo toma: {stat.emoji} {stat.label} {before.stat(stat)} → "
                f"{self.record.stat(stat)}."
            )
            if hurt[-1]:
                line += " " + injury_text(self.record, now)
            self.drill = None
            self.result = "\n".join([drill.result, line, drill.paid])
            self.rebuild()
            await edit(interaction, embed=self.embed(), view=self)
        except discord.HTTPException:
            logger.warning("No se pudo enseñar el suplemento de gallos", exc_info=True)
            return
        finally:
            self._busy = False
        await logros.track(
            self.cog.bot,
            self.guild_id,
            self.owner,
            self.channel,
            StatDelta(
                add={"gallos_supplements": 1, ("gallos_supplement_hurt" if hurt[-1] else
                                               "gallos_supplement_safe"): 1}
            ),
        )  # fmt: skip

    async def _supplement_no(self, interaction: discord.Interaction) -> None:
        """🚫 Ni de broma: no se lo toma (solo cambia memoria)."""
        drill = self.drill
        if drill is None or drill.event is None or self._busy:
            await interaction.response.defer()
            return
        self.drill = None
        self.result = "\n".join([drill.result, "🚫 Lo rechaza. Mejor así.", drill.paid])
        self.rebuild()
        self._last_interaction = interaction
        await interaction.response.edit_message(embed=self.embed(), view=self)
        await logros.track(
            self.cog.bot,
            self.guild_id,
            self.owner,
            self.channel,
            StatDelta(add={"gallos_supplement_refused": 1}),
        )

    async def _load_club(self) -> None:
        """Lee la gallera del dueño (con la ficha de cada miembro) y las que tienen hueco."""
        repository = self.cog.repository
        self.club = await repository.club_of(self.guild_id, self.owner.id)
        self.open_clubs = [c for c in await repository.clubs(self.guild_id) if not c.full]
        self.club_lines = []
        if self.club is None:
            return
        now = self.cog.wall_clock()
        for user_id in self.club.members:
            record = await repository.get(self.guild_id, user_id)
            self.club_lines.append(
                f"🐓 **{record.name}** (<@{user_id}>) · ⚡ {record.power:.0f} · "
                f"🔋 {round(form_now(record, now))} · {record.wins} victorias"
                + (f" · 🏆 ×{record.titles}" if record.titles else "")
            )

    async def _open_club_view(self, interaction: discord.Interaction) -> None:
        """🏠 Gallera: lee la base de datos, así que se acepta antes."""
        await ack(interaction)
        if self._busy:
            return
        self.club_view = True
        self.result = None
        await self._load_club()
        self.rebuild()
        self._last_interaction = interaction
        await edit(interaction, embed=self.embed(), view=self)

    async def _leave_club_view(self, interaction: discord.Interaction) -> None:
        """↩️ Volver: al reñidero (se repinta la imagen, como al salir del gimnasio)."""
        await ack(interaction)
        self.club_view = False
        self.result = None
        await self.repaint(interaction)

    async def _found_club_modal(self, interaction: discord.Interaction) -> None:
        """➕ Fundar: abre el formulario (tiene que ser la primera respuesta)."""
        await interaction.response.send_modal(ClubModal(self))

    async def found_club(self, interaction: discord.Interaction, name: str) -> None:
        """Funda la gallera con el nombre ya validado y enseña cómo queda."""
        try:
            await self.cog.repository.found_club(
                self.guild_id, self.owner.id, name, now=self.cog.wall_clock()
            )
            self.result = f"🏠 Fundas la gallera **{name}**."
        except torneo.ClubError as error:
            self.result = f"❌ {error}"
        await self._load_club()
        self.rebuild()
        self._last_interaction = interaction
        await edit(interaction, embed=self.embed(), view=self)
        if self.club is not None and self.club.owner_id == self.owner.id:
            await logros.track(
                self.cog.bot, self.guild_id, self.owner, self.channel,
                StatDelta(add={"gallos_clubs_founded": 1}),
            )  # fmt: skip

    async def _join_club(self, interaction: discord.Interaction) -> None:
        """Entra en la gallera elegida (se guarda: se acepta antes)."""
        values = interaction.data.get("values", []) if interaction.data else []
        await ack(interaction)
        if not values:
            return
        try:
            club = await self.cog.repository.join_club(
                self.guild_id, self.owner.id, int(values[0]), now=self.cog.wall_clock()
            )
            self.result = f"🏠 Entras en **{club.name}**."
            joined = True
        except torneo.ClubError as error:
            self.result = f"❌ {error}"
            joined = False
        await self._load_club()
        self.rebuild()
        self._last_interaction = interaction
        await edit(interaction, embed=self.embed(), view=self)
        if joined:
            size = len(self.club.members) if self.club else 0
            await logros.track(
                self.cog.bot,
                self.guild_id,
                self.owner,
                self.channel,
                StatDelta(add={"gallos_clubs_joined": 1}, peak={"gallos_club_size_max": size}),
            )

    async def _leave_club(self, interaction: discord.Interaction) -> None:
        """🚪 Salir de la gallera (se guarda: se acepta antes)."""
        await ack(interaction)
        name = self.club.name if self.club is not None else ""
        await self.cog.repository.leave_club(self.guild_id, self.owner.id)
        self.result = f"🚪 Sales de **{name}**." if name else None
        await self._load_club()
        self.rebuild()
        self._last_interaction = interaction
        await edit(interaction, embed=self.embed(), view=self)

    async def _retire(self, interaction: discord.Interaction) -> None:
        """🎖️ Retirarlo: el primer clic pide confirmar (memoria); el segundo, guarda."""
        if self._busy or (self.guild_id, self.owner.id) in self.cog.busy:
            await interaction.response.send_message(
                "Ahora no: tu gallo está ocupado.", ephemeral=True
            )
            return
        if error := retire_error(self.record):
            await interaction.response.send_message(error, ephemeral=True)
            return
        if not self.confirm_retire:
            self.confirm_retire = True
            self.result = (
                f"🎖️ Si lo retiras, **{self.record.name}** pasa al corral de leyendas y su cría "
                f"empieza con el {round(INHERIT_SHARE * 100)} % de sus stats, sin forma y con "
                "el historial a cero. Pulsa otra vez para confirmarlo."
            )
            self.rebuild()
            self._last_interaction = interaction
            await interaction.response.edit_message(embed=self.embed(), view=self)
            return
        await ack(interaction)
        self.confirm_retire = False
        self._busy = True
        self._last_interaction = interaction
        old = self.record
        try:
            self.record = await self.cog.repository.retire(
                self.guild_id, self.owner.id, now=self.cog.wall_clock()
            )
            self.result = (
                f"## 🎖️ {old.name} se retira con honores\n"
                f"{old.fights} peleas y {old.wins} victorias. Su cría, **{self.record.name}**, "
                f"hereda 💪 {self.record.strength} · 🪶 {self.record.speed} · "
                f"❤️ {self.record.endurance} (generación {self.record.generation + 1})."
            )
            self.rebuild()
            await edit(interaction, embed=self.embed(), view=self)
        except discord.HTTPException:
            logger.warning("No se pudo enseñar un retiro de gallos", exc_info=True)
        finally:
            self._busy = False
        await logros.track(
            self.cog.bot,
            self.guild_id,
            self.owner,
            self.channel,
            StatDelta(
                add={
                    "gallos_retired": 1,
                    "gallos_retired_champion": int(old.wins >= GALLOS_CHAMPION_WINS),
                },
                peak={"gallos_generation_max": self.record.generation},
            ),
        )

    async def _open_sparring(self, interaction: discord.Interaction) -> None:
        """🤝 Buscar sparring: publica la invitación en el canal (se acepta antes de mandarla)."""
        key = (self.guild_id, self.owner.id)
        if self._busy or key in self.cog.busy:
            await interaction.response.send_message(
                "Tu gallo ya está en un duelo o en un sparring.", ephemeral=True
            )
            return
        now = self.cog.wall_clock()
        try:
            check_training(self.record, Stat.STRENGTH, now=now, day=local_day(now))
        except TrainError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await ack(interaction)
        sparring = Sparring(self.cog, guild_id=self.guild_id, channel=self.channel, host=self.owner)
        self.cog.busy.add(key)
        self.cog.sparrings.add(sparring)
        try:
            await sparring.open()
        except Exception:
            self.cog.release_sparring(sparring)
            raise
        await notify(interaction, "🤝 Invitación publicada: el primero que acepte es tu sparring.")

    async def _feed(self, interaction: discord.Interaction) -> None:
        """🌽 Echarle de comer: otro miembro le da forma al gallo (se guarda: se acepta antes)."""
        feeder = interaction.user
        if feeder.id == self.owner.id:
            await interaction.response.send_message(
                "A tu gallo le das de comer entrenándolo. Esto es para los gallos de otros.",
                ephemeral=True,
            )
            return
        now = self.cog.wall_clock()
        day = local_day(now)
        key = (self.guild_id, self.owner.id, day)
        if feeder.id in self.cog.fed.get(key, set()):
            await interaction.response.send_message(
                f"Hoy ya le has echado de comer a **{self.record.name}**.", ephemeral=True
            )
            return
        await ack(interaction)
        try:
            _, self.record = await self.cog.repository.change(
                self.guild_id,
                self.owner.id,
                lambda record: gym.after_feed(record, now=now, day=day),
            )
        except gym.FeedError as error:
            await notify(interaction, str(error))
            return
        self.cog.fed.setdefault(key, set()).add(feeder.id)
        await notify(
            interaction,
            f"🌽 Le echas de comer a **{self.record.name}**, el gallo de "
            f"{self.owner.display_name}: +{gym.FEED_FORM} de forma.",
        )
        try:
            self.rebuild()
            await edit(interaction, embed=self.embed(), view=self)
        except discord.HTTPException:
            logger.debug("No se pudo repintar el reñidero tras echarle de comer", exc_info=True)
        await logros.track(
            self.cog.bot, self.guild_id, feeder, self.channel,
            StatDelta(add={"gallos_fed_others": 1}),
        )  # fmt: skip
        await logros.track(
            self.cog.bot, self.guild_id, self.owner, self.channel,
            StatDelta(add={"gallos_fed_by_others": 1}),
        )  # fmt: skip

    async def _toggle_coach(self, interaction: discord.Interaction) -> None:
        """🧑‍🏫 Abre o cierra el minijuego a que lo juegue otro (solo cambia memoria)."""
        drill = self.drill
        if drill is None or drill.coach is not None or self._busy:
            await interaction.response.defer()
            return
        drill.coach_open = not drill.coach_open
        self.rebuild()
        self._last_interaction = interaction
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _cheer(self, interaction: discord.Interaction) -> None:
        """📣 Animar: un ánimo por persona y minijuego, hasta `CHEER_MAX` (solo cambia memoria)."""
        drill = self.drill
        fan = interaction.user
        if drill is None or drill.event is not None:
            await interaction.response.defer()
            return
        if fan.id == self.owner.id:
            await interaction.response.send_message(
                "Animarte a ti mismo no cuenta. Que te animen los demás.", ephemeral=True
            )
            return
        if fan.id in drill.cheers or len(drill.cheers) >= gym.CHEER_MAX:
            await interaction.response.send_message(
                "Ya has animado este minijuego (o ya van los ánimos que caben).", ephemeral=True
            )
            return
        drill.cheers.add(fan.id)
        self.rebuild()
        await interaction.response.edit_message(embed=self.embed(), view=self)
        await logros.track(
            self.cog.bot, self.guild_id, fan, self.channel, StatDelta(add={"gallos_cheers": 1})
        )

    async def _sabotage(self, interaction: discord.Interaction) -> None:
        """🍌 Sabotear: paga la cáscara (IGIC) y baja la nota del minijuego. Uno por minijuego.

        Tratamiento fiscal: una compra, como entrenar (IGIC general del 7 %, art. 4
        de la Ley 20/1991 y art. 51 de la Ley 4/2012): la base va a la caja de la
        tienda y el IGIC al Estado (`EconomyService.purchase`, motivo
        `gallos:sabotaje`). Es un sumidero; sin IRPF ni retención de juego.
        """
        drill = self.drill
        rival = interaction.user
        if drill is None or drill.event is not None:
            await interaction.response.defer()
            return
        if rival.id == self.owner.id:
            await interaction.response.send_message(
                "Sabotearte a ti mismo ya lo haces sin pagar.", ephemeral=True
            )
            return
        if drill.saboteur is not None:
            await interaction.response.send_message(
                "A este minijuego ya le han tirado una cáscara.", ephemeral=True
            )
            return
        await ack(interaction)
        base = gym.SABOTAGE_PRICE
        tax = igic(base)
        try:
            _, balance = await self.cog.economy.purchase(
                self.guild_id,
                rival.id,
                base=base,
                tax=tax,
                concept=gym.SABOTAGE_CONCEPT,
                reserve=lambda _connection: None,
            )
        except InsufficientFundsError as error:
            await notify(interaction, insufficient_text(error.balance, base + tax))
            return
        # Entre el clic y el cobro el minijuego puede haber acabado: la cáscara cae igual en
        # la nota si sigue en marcha; si no, se ha pagado por nada (como en la vida real).
        if self.drill is drill and not drill.game.done and drill.saboteur is None:
            drill.saboteur = rival
        lines = [
            f"🍌 Tiras una cáscara de plátano en el gimnasio de **{self.record.name}**.",
            f"-# 🐶 {TAX_COLLECTOR} se lleva {format_amount(tax)} de IGIC "
            f"(tipo general, {IGIC_GENERAL_RATE * 100:g} % de {format_amount(base)}). "
            f"Pagas {format_amount(base + tax)}. Saldo {format_amount(balance)}.",
        ]
        if hint := await renta.hint(self.cog.bot, self.guild_id, rival.id, Moment(Event.BUY)):
            lines.append(hint)
        await notify(interaction, "\n".join(lines))
        try:
            self.rebuild()
            await edit(interaction, embed=self.embed(), view=self)
        except discord.HTTPException:
            logger.debug("No se pudo repintar el reñidero tras un sabotaje", exc_info=True)
        await renta.remind(self.cog.bot, interaction)
        await logros.track(
            self.cog.bot, self.guild_id, rival, self.channel, StatDelta(add={"gallos_sabotages": 1})
        )
        await logros.track(
            self.cog.bot,
            self.guild_id,
            self.owner,
            self.channel,
            StatDelta(add={"gallos_sabotaged": 1}),
        )

    async def _train_text(self, training: Training) -> str:
        """El resultado del entrenamiento: lo que sube, una frase y la mordida del IGIC."""
        stat, before, after = training.stat, training.before, training.after
        now = self.cog.wall_clock()
        form = f"🔋 Forma {round(form_now(before, now))} → {round(form_now(after, now))}"
        title = (
            f"{stat.emoji} {stat.label} {before.stat(stat)} → {after.stat(stat)} · {form}"
            if after.stat(stat) != before.stat(stat)
            else form
        )
        lines = [
            f"### 🏋️ {title}",
            random.choice(TRAIN_LINES[stat]).format(name=after.name),
            f"-# 🐶 {TAX_COLLECTOR} se lleva {format_amount(training.tax)} de IGIC "
            f"(tipo general, {IGIC_GENERAL_RATE * 100:g} % de {format_amount(training.base)}). "
            f"Pagas {format_amount(training.total)}.",
        ]
        if hint := await renta.hint(self.cog.bot, self.guild_id, self.owner.id, Moment(Event.BUY)):
            lines.append(hint)
        return "\n".join(lines)

    async def _after_train(self, interaction: discord.Interaction, training: Training) -> None:
        """Lo que va después de contestar: aviso de la Renta y logros."""
        await renta.remind(self.cog.bot, interaction)
        today = trains_today(training.after, local_day(self.cog.wall_clock()))
        await logros.track(
            self.cog.bot,
            self.guild_id,
            self.owner,
            self.channel,
            gallos_train_stats(
                record=training.after,
                paid=training.total,
                today=today,
                when=datetime.now(TIMEZONE),
                form=round(form_now(training.after, self.cog.wall_clock())),
            ),
        )

    def _idle(self) -> bool:
        return not self._busy

    async def _restake(self, interaction: discord.Interaction, stake: int) -> None:
        self.stake = max(1, stake)
        self.result = None
        self.rebuild()
        self._last_interaction = interaction
        await edit(interaction, embed=self.embed(), view=self)

    async def _halve(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        if self._idle():
            self.balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
            await self._restake(interaction, self.stake // 2)

    async def _double(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        if self._idle():
            self.balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
            await self._restake(interaction, min(self.stake * 2, max(1, self.balance)))

    async def _all_in(self, interaction: discord.Interaction) -> None:
        await ack(interaction)
        if not self._idle():
            return
        self.balance = await self.cog.economy.balance(self.guild_id, self.owner.id)
        if self.balance <= 0:
            await notify(interaction, insufficient_text(0))
            return
        await self._restake(interaction, self.balance)

    async def _fight(self, interaction: discord.Interaction) -> None:
        """🐓 ¡A pelear!: cobra, decide, paga y enseña la pelea."""
        # Cobrar y dibujar van antes que nada: se acepta el clic ya.
        await ack(interaction)
        if self._busy:
            return
        self._busy = True
        self._last_interaction = interaction
        try:
            outcome = await self._play()
            if isinstance(outcome, str):
                await notify(interaction, outcome)
                return
            await self._show(interaction, outcome)
        except discord.HTTPException:
            logger.warning("No se pudo enseñar una pelea de gallos", exc_info=True)
            return
        finally:
            self._busy = False
        await self._after(interaction, outcome)

    async def _play(self) -> HouseFight | str:
        """Cobra la apuesta, decide la pelea y paga, todo antes de enseñarla."""
        rival, stake = self.rival, self.stake
        async with self._lock:
            # Con un duelo abierto el gallo ya está apostado: no pelea dos veces a la vez.
            if (self.guild_id, self.owner.id) in self.cog.busy:
                return "Tu gallo está en un duelo: nada de peleas con la casa hasta que acabe."
            if injured(self.record, self.cog.wall_clock()):
                return injury_text(self.record, self.cog.wall_clock())
            try:
                await self.cog.economy.place_bet(
                    self.guild_id, self.owner.id, game=GAME, stake=stake
                )
            except InsufficientFundsError as error:
                return insufficient_text(error.balance, stake)
            rng = self.cog.rng
            # La probabilidad y la cuota salen del poder y la forma que se vieron en el
            # panel: la misma `p` que se sortea es la que fija lo que se cobra.
            form = self.form
            deadly = self.deadly
            grudge = grudge_against(self.record, rival.key)
            chance, multiplier = self.odds(rival)
            fight = simulate(draw_winner(chance, rng), rng)
            won = fight.winner is Corner.RED
            payout = int(stake * multiplier) if won else 0
            settlement = await self.cog.pay(self.guild_id, self.owner.id, payout)
            self.balance = (
                settlement.balance
                if settlement
                else await self.cog.economy.balance(self.guild_id, self.owner.id)
            )
            _before, after = await self.cog.repository.record_fight(
                self.guild_id,
                self.owner.id,
                won=won,
                finish=fight.finish,
                day=datetime.now(TIMEZONE).date(),
                now=self.cog.wall_clock(),
                rival=rival.key,
            )
            owner = Owner(
                self.owner,
                after,
                tienda.skin_of(self.cog.bot, self.guild_id, self.owner.id, SKIN_GAME),
            )
            self.record = after
            fallen = None
            hurt = False
            if (
                not won
                and fight.finish is Finish.KO
                and dies(after, rng, form, deadly=deadly, grudge=grudge)
            ):
                fallen = after
                self.record = await self.cog.bury(
                    self.guild_id, self.owner.id, killer_kind="casa", killer=rival.key
                )
            elif not won and fight.finish is Finish.KO and fight_injury(form, rng):
                hurt = True
                now = self.cog.wall_clock()
                _, self.record = await self.cog.repository.change(
                    self.guild_id, self.owner.id, lambda record: injure(record, now)
                )
            return HouseFight(
                rival=rival,
                stake=stake,
                fight=fight,
                payout=payout,
                settlement=settlement,
                seed=rng.randrange(1, 2**31),
                owner=owner,
                chance=chance,
                multiplier=multiplier,
                form=form,
                hurt=hurt,
                deadly=deadly,
                grudge=grudge,
                grudge_wins=after.grudge_wins if grudge_against(after, rival.key) else 0,
                fallen=fallen,
                reborn=self.record.name if fallen else None,
            )

    async def _show(self, interaction: discord.Interaction, game: HouseFight) -> None:
        """Enseña la pelea (con los botones apagados mientras se pinta) y el resultado."""
        self.disable_all()
        waiting = (
            f"# {random.choice(WAITING_LINES)}\n"
            f"**{game.owner.record.name}** 🆚 **{game.rival.label}** · "
            f"{format_amount(game.stake)} a {format_odds(game.multiplier)}"
        )
        names = {Corner.RED: game.owner.record.name, Corner.BLUE: game.rival.name}

        async def show_waiting() -> None:
            try:
                await edit(interaction, embed=self.embed(text=waiting), view=self)
            except discord.HTTPException:
                logger.warning("No se pudo apagar el reñidero", exc_info=True)

        meta = self._meta(game.seed, game.owner, game.multiplier)
        _, media = await asyncio.gather(
            show_waiting(), self.cog.renderer.fight(meta, game.fight, names, seed=game.seed)
        )
        self._poster = None
        await edit(
            interaction,
            embed=self.embed(image=GIF_NAME, text=waiting),
            attachments=[discord.File(io.BytesIO(media.gif), filename=GIF_NAME)],
            view=self,
        )
        await asyncio.sleep(media.seconds + REVEAL_MARGIN_SECONDS)
        self.result = await self._result_text(game, names)
        self.rebuild()
        await edit(
            interaction,
            embed=self.embed(color=COLOR_WIN if game.won else COLOR_LOST),
            attachments=[discord.File(io.BytesIO(media.png), filename=PNG_NAME)],
            view=self,
        )

    async def _result_text(self, game: HouseFight, names: dict[Corner, str]) -> str:
        fight = game.fight
        lines = []
        if game.won and game.net < 0:
            # Con un gallo muy entrenado contra uno flojo la cuota baja de ×1: ganar sale caro.
            lines.append(
                f"# 🏆 {random.choice(WIN_LINES)} {format_amount(game.net)}\n"
                f"Tu gallo gana, pero a {format_odds(game.multiplier)} "
                "la casa te devuelve menos de lo apostado. Así de favorito eres."
            )
            lines.append(
                f"**{names[Corner.RED]}** gana a {game.rival.label} {fight.finish.label} y cobras "
                f"**{format_amount(game.payout)}**."
            )
        elif game.won:
            lines.append(f"# 🏆 {random.choice(WIN_LINES)} +{format_amount(game.net)}")
            lines.append(
                f"**{names[Corner.RED]}** gana a {game.rival.label} {fight.finish.label} y cobras "
                f"**{format_amount(game.payout)}**."
            )
        else:
            lines.append(f"# 💀 {random.choice(LOSE_LINES)} -{format_amount(game.stake)}")
            lines.append(f"{game.rival.label} gana {fight.finish.label}.")
        lines += narrate(fight, names, {Corner.BLUE: game.rival.signature})
        if game.fallen is not None and game.reborn is not None:
            lines += mourning_lines(game.fallen, game.reborn)
        elif game.hurt:
            lines.append(
                "🤕 Peleó agotado y sale lesionado: no podrá pelear en "
                f"{INJURY_HOURS} h. Entrenar sí puede."
            )
        if game.settlement is not None and (tax := gambling_tax_line(game.settlement)):
            lines.append(tax)
        if hint := await renta.hint(
            self.cog.bot,
            self.guild_id,
            self.owner.id,
            bet_moment(stake=game.stake, net=game.net, balance_after=self.balance),
        ):
            lines.append(hint)
        return "\n".join(lines)

    async def _after(self, interaction: discord.Interaction, game: HouseFight) -> None:
        """Lo que va después de enseñar el resultado: renta, logros, estadísticas y anuncio."""
        await renta.remind(self.cog.bot, interaction)
        tax = game.settlement.tax_delta if game.settlement else 0
        delta = gallos_stats(
            fight=game.fight,
            corner=Corner.RED,
            rival=game.rival.key,
            chance=float(game.chance),
            stake=game.stake,
            net=game.net,
            record=game.owner.record,
            skin=game.owner.skin_key,
            paid_skin=game.owner.paid_skin,
            crowd_pool=0,
            rake=0,
            when=datetime.now(TIMEZONE),
            power=game.owner.record.power,
            died=game.fallen,
            form=game.form,
            # Lo que paga de más la forma frente a la cuota de serie con la misma probabilidad.
            edge_gain=game.payout - int(game.stake * house_multiplier(game.chance))
            if game.won
            else 0,
            full_cap=game.stake == edge_cap(game.owner.record.power)
            and house_edge(game.rival, game.form) > 0,
            hurt=game.hurt,
            deadly=game.deadly,
            grudge=game.grudge,
            grudge_wins=game.grudge_wins,
        )
        delta.merge(
            casino_stats(stake=game.stake, net=game.net, balance_after=self.balance, tax_delta=tax)
        )
        await logros.casino_play(
            self.cog.bot, self.guild_id, self.owner, self.channel, delta, net=game.net
        )
        await apuestas.record(
            self.cog.bot,
            self.guild_id,
            self.owner,
            game=GAME,
            stake=game.stake,
            net=game.net,
            balance_after=self.balance,
            tax=tax,
            details=(("ko", int(game.fight.finish is Finish.KO)), ("won", int(game.won))),
        )
        if game.grudge_wins == GRUDGE_WINS:
            await self.cog.shout(
                self.channel,
                f"🔥 {game.rival.label} se ha picado con **{game.owner.record.name}**, el gallo "
                f"de {self.owner.mention}: le ha ganado {GRUDGE_WINS} seguidas. La próxima vez "
                "paga más, pero pega más fuerte.",
                self.owner,
            )
        if game.won and game.net >= SHOUT_NET:
            await self.cog.shout(
                self.channel,
                f"📣 🐓 ¡**{game.owner.record.name}**, el gallo de {self.owner.mention}, le ha "
                f"ganado a {game.rival.label}! **+{format_amount(game.net)}**",
                self.owner,
            )


@dataclass(frozen=True, slots=True)
class HouseFight:
    """Una pelea contra la casa ya cobrada y pagada."""

    rival: Rival
    stake: int
    fight: Fight
    payout: int
    settlement: BetSettlement | None
    seed: int
    owner: Owner
    #: Probabilidad que tenía tu gallo de ganar y la cuota que se le pagaba.
    chance: Fraction
    multiplier: Fraction
    #: Forma con la que salió a pelear (antes de gastarla).
    form: int
    #: Si se ha lesionado por perder por KO agotado.
    hurt: bool = False
    #: Si fue ☠️ a muerte y si el rival estaba picado al empezar.
    deadly: bool = False
    grudge: bool = False
    #: Victorias seguidas contra el rival tras la pelea, si con ellas está picado (0 si no).
    grudge_wins: int = 0
    #: La ficha del gallo si ha muerto en esta pelea, y el nombre del que lo sustituye.
    fallen: RoosterRecord | None = None
    reborn: str | None = None

    @property
    def won(self) -> bool:
        return self.fight.winner is Corner.RED

    @property
    def net(self) -> int:
        return self.payout - self.stake


# -- Duelos ------------------------------------------------------------------------------


class Phase(Enum):
    """Momento de un duelo."""

    CHALLENGE = "reto"
    LOBBY = "grada"
    FIGHT = "pelea"
    DONE = "fin"


@dataclass(slots=True)
class Spectator:
    """Un apostante de grada: su apuesta y la última interacción (para su resultado privado)."""

    member: discord.abc.User
    bet: PoolBet
    interaction: discord.Interaction | None = None
    settlement: BetSettlement | None = None
    payout: int = 0


class BetModal(ui.Modal, title="🎟️ Apuesta de grada"):
    """Pide cuánto apuesta el público por un gallo."""

    amount: ui.TextInput = ui.TextInput(
        label="Cantidad (500, 2k, all…)", placeholder="100", max_length=12
    )

    def __init__(self, duel: Duel, corner: Corner) -> None:
        super().__init__()
        self.duel = duel
        self.corner = corner

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Cobra la apuesta y la apunta en la grada."""
        # Leer el saldo y cobrar van a la base de datos: se acepta antes.
        await ack(interaction)
        await self.duel.place(interaction, self.corner, str(self.amount.value))


class DuelView(ui.View):
    """Los botones de un duelo; quién puede pulsar cada uno lo decide el duelo."""

    def __init__(self, duel: Duel) -> None:
        super().__init__(timeout=None)
        self.duel = duel

    def _button(
        self,
        label: str,
        custom_id: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        row: int = 0,
    ) -> None:
        button: ui.Button = ui.Button(
            label=label[:80], style=style, custom_id=f"{GAME}:{custom_id}", row=row
        )
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)

    def challenge(self) -> DuelView:
        """Botones del reto: aceptar, rechazar o retirarlo."""
        self.clear_items()
        self._button("✅ Aceptar", "accept", self.duel.accept, style=discord.ButtonStyle.success)
        self._button("❌ Rechazar", "reject", self.duel.reject, style=discord.ButtonStyle.danger)
        self._button("🗑️ Retirar", "withdraw", self.duel.withdraw)
        return self

    def lobby(self) -> DuelView:
        """Botones de la grada: apostar por cada gallo y soltar los gallos."""
        self.clear_items()
        duel = self.duel
        for corner in Corner:
            style = (
                discord.ButtonStyle.danger if corner is Corner.RED else discord.ButtonStyle.primary
            )
            self._button(
                f"{corner.emoji} Apostar por {duel.owner(corner).record.name}",
                f"bet_{corner.value}",
                duel.bet_red if corner is Corner.RED else duel.bet_blue,
                style=style,
            )
        self._button(
            f"✅ ¡Suelta el gallo! ({len(duel.ready)}/2)",
            "ready",
            duel.mark_ready,
            style=discord.ButtonStyle.success,
            row=1,
        )
        return self


class Duel:
    """Un duelo entre dos miembros, del reto al resultado, en un mismo mensaje.

    Guarda quién apuesta y cuánto solo para repartir: el dinero se cobra y se
    paga siempre por la economía.
    """

    def __init__(
        self,
        cog: Gallos,
        *,
        guild_id: int,
        channel: object,
        red: Owner,
        blue: Owner,
        stake: int,
    ) -> None:
        self.cog = cog
        self.guild_id = guild_id
        self.channel = channel
        self.owners = {Corner.RED: red, Corner.BLUE: blue}
        self.stake = stake
        #: Probabilidad de que gane el rojo, de los poderes de los dos gallos.
        self.chance = duel_chance(red.record.power, blue.record.power)
        #: Gallos que mueren en este duelo (la ficha con la que murieron) y la ficha
        #: de cada uno tras pelear, antes de enterrarlo.
        self.fallen: dict[Corner, RoosterRecord] = {}
        self.finals: dict[Corner, RoosterRecord] = {}
        self.phase = Phase.CHALLENGE
        self.view = DuelView(self)
        self.message: discord.Message | None = None
        self.crowd: dict[int, Spectator] = {}
        self.ready: set[int] = set()
        self.interactions: dict[int, discord.Interaction] = {}
        self.settlements: dict[Corner, BetSettlement | None] = {}
        self.lobby_ends = 0.0
        self.started_at = 0.0
        self.go = asyncio.Event()
        self.task: asyncio.Task[None] | None = None
        self.fight: Fight | None = None
        self.seed = cog.rng.randrange(1, 2**31)
        self.prepared: asyncio.Task[Media] | None = None
        self._lock = TimedLock("gallos:Duel._lock")
        self.timer: asyncio.Task[None] | None = None

    def owner(self, corner: Corner) -> Owner:
        """El dueño de una esquina."""
        return self.owners[corner]

    def corner_of(self, user_id: int) -> Corner | None:
        """La esquina de un dueño, o `None` si no pelea."""
        for corner, owner in self.owners.items():
            if owner.member.id == user_id:
                return corner
        return None

    @property
    def members(self) -> list[discord.abc.User]:
        return [self.owners[c].member for c in Corner]

    # -- Texto e imagen -----------------------------------------------------------------

    def names(self) -> dict[Corner, str]:
        return {c: self.owners[c].record.name for c in Corner}

    def meta(self) -> dict[str, Any]:
        odds = pool_odds({k: s.bet for k, s in self.crowd.items()})
        return meta_state(
            fighter_of(self.owners[Corner.RED], odds=self._odds_text(odds[Corner.RED])),
            fighter_of(self.owners[Corner.BLUE], odds=self._odds_text(odds[Corner.BLUE])),
            title="DUELO DE GALLOS",
            subtitle=f"Bote {format_amount(duel_prize(self.stake))}",
            seed=self.seed,
        )

    @staticmethod
    def _odds_text(odds: Fraction | None) -> str:
        return format_odds(odds) if odds is not None else "sin apuestas"

    def _corner_line(self, corner: Corner) -> str:
        owner = self.owners[corner]
        return (
            f"{corner.emoji} **{owner.record.name}** de {owner.member.mention} · "
            f"{record_line(owner.record)}\n-# {stat_line(owner.record)}"
        )

    def _chance_line(self) -> str:
        """`-# Probabilidad de ganar: 🔴 55 % · 🔵 45 %` (solo si los poderes difieren)."""
        if self.chance == Fraction(1, 2):
            return ""
        return (
            f"-# Probabilidad de ganar: {Corner.RED.emoji} {_percent(self.chance)} · "
            f"{Corner.BLUE.emoji} {_percent(1 - self.chance)}, por el poder de cada gallo."
        )

    def embed(
        self, text: str, *, image: str = PNG_NAME, color: discord.Color = COLOR_DUEL
    ) -> discord.Embed:
        red, blue = self.members
        embed = discord.Embed(
            title=f"🐓 Duelo: {red.display_name} 🆚 {blue.display_name}",
            description=text,
            color=color,
        )
        embed.set_image(url=f"attachment://{image}")
        return embed

    def challenge_text(self) -> str:
        red, blue = self.members
        return "\n".join(
            [
                f"# 🥊 {red.mention} reta a {blue.mention}",
                f"**{format_amount(self.stake)}** por cabeza · el ganador se lleva "
                f"**{format_amount(duel_prize(self.stake))}** "
                f"(la casa se queda el {int(DUEL_CUT * 100)} %).",
                self._corner_line(Corner.RED),
                self._corner_line(Corner.BLUE),
                *([line] if (line := self._chance_line()) else []),
                f"-# {blue.display_name} tiene <t:{int(self.lobby_ends)}:R> para aceptar.",
            ]
        )

    def lobby_text(self) -> str:
        bets = {k: s.bet for k, s in self.crowd.items()}
        totals = pool_totals(bets)
        odds = pool_odds(bets)
        lines = [
            "# 🎟️ ¡Hagan sus apuestas!",
            f"Bote del duelo: **{format_amount(duel_prize(self.stake))}**.",
            self._corner_line(Corner.RED),
            self._corner_line(Corner.BLUE),
            *([line] if (line := self._chance_line()) else []),
            "### Grada",
        ]
        for corner in Corner:
            backers = sum(1 for s in self.crowd.values() if s.bet.corner is corner)
            lines.append(
                f"{corner.emoji} {format_amount(totals[corner])} de {backers} "
                f"persona{'s' if backers != 1 else ''} · paga {self._odds_text(odds[corner])}"
            )
        lines.append(
            f"-# La grada se reparte lo apostado entre los que acierten, menos el "
            f"{int(POOL_CUT * 100)} % de la casa. Los gallos salen <t:{int(self.lobby_ends)}:R> o "
            "cuando sus dueños pulsen ✅."
        )
        return "\n".join(lines)

    async def poster(self) -> bytes:
        return await self.cog.renderer.still(self.meta(), intro_state())

    async def edit(self, **kwargs: Any) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(**kwargs)
        except discord.HTTPException:
            logger.debug("No se pudo editar un duelo de gallos", exc_info=True)

    # -- Reto -------------------------------------------------------------------------

    async def open(self, send: Callable[..., Awaitable[discord.Message]]) -> None:
        """Manda el reto y espera a que lo acepten."""
        self.lobby_ends = self.cog.wall_clock() + CHALLENGE_SECONDS
        png = await self.poster()
        self.message = await send(
            content=self.owners[Corner.BLUE].member.mention,
            embed=self.embed(self.challenge_text()),
            file=discord.File(io.BytesIO(png), filename=PNG_NAME),
            view=self.view.challenge(),
            allowed_mentions=discord.AllowedMentions(users=[self.owners[Corner.BLUE].member]),
        )
        self.timer = asyncio.create_task(self._expire(), name="gallos-reto")

    async def _expire(self) -> None:
        await self.cog.sleep(CHALLENGE_SECONDS)
        async with self._lock:
            if self.phase is not Phase.CHALLENGE:
                return
            self.phase = Phase.DONE
        self.cog.release(self)
        await self.edit(
            content=None,
            embed=self.embed("⌛ Nadie contestó al reto. Los gallos vuelven al corral."),
            view=None,
        )

    async def _only(self, interaction: discord.Interaction, corner: Corner, what: str) -> bool:
        if interaction.user.id == self.owners[corner].member.id:
            return True
        await interaction.response.send_message(what, ephemeral=True)
        return False

    async def accept(self, interaction: discord.Interaction) -> None:
        """✅ Aceptar: cobra a los dos y abre la grada."""
        if not await self._only(interaction, Corner.BLUE, "Este reto no es para ti."):
            return
        await ack(interaction)
        async with self._lock:
            if self.phase is not Phase.CHALLENGE:
                return
            error = await self._charge_fighters()
            if error:
                await notify(interaction, error)
                return
            self.phase = Phase.LOBBY
            self.interactions[interaction.user.id] = interaction
            self.started_at = time.time()
            self.lobby_ends = self.cog.wall_clock() + LOBBY_SECONDS
            # La pelea se decide y se pinta ya, mientras se apuesta: el resultado no
            # depende de las apuestas (sale de su propio azar, con la probabilidad que dan
            # los poderes de los dos gallos) y no se enseña ni se guarda en ningún sitio
            # hasta que salen los gallos.
            rng = self.cog.rng
            self.fight = simulate(draw_winner(self.chance, rng), rng)
            self.prepared = asyncio.create_task(
                self.cog.renderer.fight(self.meta(), self.fight, self.names(), seed=self.seed),
                name="gallos-dibujo",
            )
        if self.timer is not None:
            self.timer.cancel()
        await edit(
            interaction, content=None, embed=self.embed(self.lobby_text()), view=self.view.lobby()
        )
        self.task = asyncio.create_task(self.run(), name="gallos-duelo")
        await renta.remind(self.cog.bot, interaction)

    async def _charge_fighters(self) -> str | None:
        """Cobra la apuesta a los dos; si al segundo no le llega, devuelve la del primero."""
        charged: list[discord.abc.User] = []
        for corner in (Corner.BLUE, Corner.RED):
            member = self.owners[corner].member
            try:
                await self.cog.economy.place_bet(
                    self.guild_id, member.id, game=GAME, stake=self.stake
                )
            except InsufficientFundsError as error:
                for refunded in charged:
                    await self.cog.pay(self.guild_id, refunded.id, self.stake)
                if corner is Corner.BLUE:
                    return insufficient_text(error.balance, self.stake)
                return f"A {member.display_name} ya no le llega para el duelo."
            charged.append(member)
        return None

    async def reject(self, interaction: discord.Interaction) -> None:
        """❌ Rechazar: el reto se acaba sin mover dinero."""
        if not await self._only(interaction, Corner.BLUE, "Este reto no es para ti."):
            return
        if self._lock.locked():
            # Están aceptando (cobro en la base de datos): sin esperar al candado.
            await interaction.response.defer()
            return
        async with self._lock:
            if self.phase is not Phase.CHALLENGE:
                await interaction.response.defer()
                return
            self.phase = Phase.DONE
        if self.timer is not None:
            self.timer.cancel()
        self.cog.release(self)
        red, blue = self.members
        await edit(
            interaction,
            content=None,
            embed=self.embed(f"🐔 {blue.mention} rechaza el reto. Co-co-co…"),
            view=None,
        )
        await logros.track(
            self.cog.bot, self.guild_id, blue, self.channel, StatDelta(add={"gallos_rejects": 1})
        )
        await logros.track(
            self.cog.bot, self.guild_id, red, self.channel, StatDelta(add={"gallos_rejected": 1})
        )

    async def withdraw(self, interaction: discord.Interaction) -> None:
        """🗑️ Retirar: quien reta se echa atrás antes de que acepten."""
        if not await self._only(interaction, Corner.RED, "Solo quien reta puede retirarlo."):
            return
        if self._lock.locked():
            # Están aceptando (cobro en la base de datos): sin esperar al candado.
            await interaction.response.defer()
            return
        async with self._lock:
            if self.phase is not Phase.CHALLENGE:
                await interaction.response.defer()
                return
            self.phase = Phase.DONE
        if self.timer is not None:
            self.timer.cancel()
        self.cog.release(self)
        await edit(
            interaction,
            content=None,
            embed=self.embed("🗑️ Reto retirado. Aquí no ha pasado nada."),
            view=None,
        )

    # -- Grada ------------------------------------------------------------------------

    async def bet_red(self, interaction: discord.Interaction) -> None:
        await self._open_bet(interaction, Corner.RED)

    async def bet_blue(self, interaction: discord.Interaction) -> None:
        await self._open_bet(interaction, Corner.BLUE)

    async def _open_bet(self, interaction: discord.Interaction, corner: Corner) -> None:
        """Abre el formulario de la apuesta (las comprobaciones solo miran memoria)."""
        if self.phase is not Phase.LOBBY:
            await interaction.response.send_message("La grada ya está cerrada.", ephemeral=True)
            return
        if self.corner_of(interaction.user.id) is not None:
            await interaction.response.send_message(
                "Tú ya te juegas tu gallo: la grada es para el público.", ephemeral=True
            )
            return
        if interaction.user.id in self.crowd:
            await interaction.response.send_message(
                "Ya tienes tu apuesta en esta pelea.", ephemeral=True
            )
            return
        await interaction.response.send_modal(BetModal(self, corner))

    async def place(self, interaction: discord.Interaction, corner: Corner, text: str) -> None:
        """Cobra una apuesta de grada (el formulario ya está aceptado)."""
        user = interaction.user
        balance = await self.cog.economy.balance(self.guild_id, user.id)
        try:
            stake = parse_amount(text, balance)
        except ValueError as error:
            await notify(interaction, str(error))
            return
        if stake <= 0 or stake > balance:
            await notify(interaction, insufficient_text(balance, stake))
            return
        async with self._lock:
            if self.phase is not Phase.LOBBY:
                await notify(interaction, "La grada ya está cerrada.")
                return
            if user.id in self.crowd:
                await notify(interaction, "Ya tienes tu apuesta en esta pelea.")
                return
            try:
                await self.cog.economy.place_bet(self.guild_id, user.id, game=GAME, stake=stake)
            except InsufficientFundsError as error:
                await notify(interaction, insufficient_text(error.balance, stake))
                return
            self.crowd[user.id] = Spectator(user, PoolBet(corner, stake), interaction)
        await notify(
            interaction,
            f"🎟️ {format_amount(stake)} por {corner.emoji} **{self.owners[corner].record.name}**. "
            "Te digo cómo acaba en cuanto suelten los gallos.",
        )
        await self.edit(embed=self.embed(self.lobby_text()), view=self.view.lobby())
        await renta.remind(self.cog.bot, interaction)

    async def mark_ready(self, interaction: discord.Interaction) -> None:
        """✅ ¡Suelta el gallo!: cuando los dos dueños lo pulsan, empieza la pelea."""
        if self.corner_of(interaction.user.id) is None:
            await interaction.response.send_message(
                "Solo los dueños de los gallos pueden soltarlos.", ephemeral=True
            )
            return
        if self.phase is not Phase.LOBBY:
            await interaction.response.defer()
            return
        self.ready.add(interaction.user.id)
        self.interactions[interaction.user.id] = interaction
        await interaction.response.edit_message(view=self.view.lobby())
        if len(self.ready) == 2:
            self.go.set()
        await renta.remind(self.cog.bot, interaction)

    # -- Pelea ------------------------------------------------------------------------

    async def run(self) -> None:
        """Espera a que acabe la grada (o a los dos ✅) y pelea."""
        try:
            wait = max(0.0, self.lobby_ends - self.cog.wall_clock())
            sleeper = asyncio.ensure_future(self.cog.sleep(wait))
            waker = asyncio.ensure_future(self.go.wait())
            try:
                await asyncio.wait({sleeper, waker}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in (sleeper, waker):
                    task.cancel()
                await asyncio.gather(sleeper, waker, return_exceptions=True)
            await self.brawl()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("El duelo de gallos ha fallado; se devuelve lo apostado")
            await self.shutdown()
        finally:
            self.cog.release(self)

    async def brawl(self) -> None:
        """Paga a todos y enseña la pelea (el GIF ya se pintó durante la grada)."""
        async with self._lock:
            if self.phase is not Phase.LOBBY:
                return
            self.phase = Phase.FIGHT
            fight = self.fight
            assert fight is not None
            prize = duel_prize(self.stake)
            for corner in Corner:
                won = fight.winner is corner
                self.settlements[corner] = await self.cog.pay(
                    self.guild_id, self.owners[corner].member.id, prize if won else 0
                )
            payouts = pool_payouts({k: s.bet for k, s in self.crowd.items()}, fight.winner)
            for user_id, spectator in self.crowd.items():
                spectator.payout = payouts[user_id]
                spectator.settlement = await self.cog.pay(self.guild_id, user_id, spectator.payout)
            day = datetime.now(TIMEZONE).date()
            for corner in Corner:
                owner = self.owners[corner]
                _before, owner.record = await self.cog.repository.record_fight(
                    self.guild_id,
                    owner.member.id,
                    won=fight.winner is corner,
                    finish=fight.finish,
                    day=day,
                )
                self.finals[corner] = owner.record
            # Lo que se enseña (nombres, ficha, dibujo) se fija antes de enterrar a nadie.
            names = self.names()
            waiting = f"# {random.choice(WAITING_LINES)}\n{self._corner_line(Corner.RED)}\n" + (
                self._corner_line(Corner.BLUE)
            )
            meta = self.meta()
            loser_record = self.finals[fight.loser]
            loser_form = form_now(loser_record, self.cog.wall_clock())
            if fight.finish is Finish.KO and dies(loser_record, self.cog.rng, loser_form):
                self.fallen[fight.loser] = self.finals[fight.loser]
                loser = self.owners[fight.loser]
                loser.record = await self.cog.bury(
                    self.guild_id,
                    loser.member.id,
                    killer_kind="miembro",
                    killer=str(self.owners[fight.winner].member.id),
                )
        await self.edit(embed=self.embed(waiting), view=None)
        assert self.prepared is not None
        try:
            media = await self.prepared
        except Exception:
            logger.warning("No se pudo pintar el duelo por adelantado", exc_info=True)
            media = await self.cog.renderer.fight(meta, fight, names, seed=self.seed)
        await self.edit(
            embed=self.embed(waiting, image=GIF_NAME),
            attachments=[discord.File(io.BytesIO(media.gif), filename=GIF_NAME)],
        )
        await self.cog.sleep(media.seconds + REVEAL_MARGIN_SECONDS)
        await self.edit(
            content=None,
            embed=self.embed(self.result_text(fight, names), color=COLOR_WIN),
            attachments=[discord.File(io.BytesIO(media.png), filename=PNG_NAME)],
        )
        self.phase = Phase.DONE
        self.cog.release(self)
        await self.tell_everyone(fight)
        await self.track(fight)

    def result_text(self, fight: Fight, names: dict[Corner, str]) -> str:
        winner = self.owners[fight.winner]
        loser = self.owners[fight.loser]
        prize = duel_prize(self.stake)
        lines = [
            f"# 🏆 ¡Gana {fight.winner.emoji} {winner.record.name}!",
            f"El gallo de {winner.member.mention} tumba al de {loser.member.mention} "
            f"{fight.finish.label} y se lleva **{format_amount(prize)}** "
            f"(+{format_amount(prize - self.stake)}).",
            *narrate(fight, names),
        ]
        for corner, dead in self.fallen.items():
            lines += mourning_lines(dead, self.owners[corner].record.name)
        if self.crowd:
            winners = [s for s in self.crowd.values() if s.payout > s.bet.stake]
            refund = all(s.payout == s.bet.stake for s in self.crowd.values())
            if refund:
                lines.append("🎟️ Nadie apostó por el otro gallo: la grada recupera lo suyo.")
            elif winners:
                best = max(winners, key=lambda s: s.payout - s.bet.stake)
                lines.append(
                    f"🎟️ {len(winners)} de {len(self.crowd)} en la grada aciertan. El que más: "
                    f"{best.member.mention}, +{format_amount(best.payout - best.bet.stake)}."
                )
            else:
                lines.append("🎟️ Nadie en la grada lo vio venir.")
        return "\n".join(lines)

    async def tell_everyone(self, fight: Fight) -> None:
        """A cada uno, en privado, lo suyo: lo cobrado, Hacienda y su mascota."""
        for corner in Corner:
            owner = self.owners[corner]
            interaction = self.interactions.get(owner.member.id)
            settlement = self.settlements.get(corner)
            won = fight.winner is corner
            net = (duel_prize(self.stake) if won else 0) - self.stake
            text = (
                f"🏆 Tu gallo gana: +{format_amount(net)}"
                if won
                else f"💀 Tu gallo pierde: -{format_amount(self.stake)}"
            )
            await self._tell(interaction, owner.member.id, text, settlement, self.stake, net)
        for spectator in self.crowd.values():
            stake = spectator.bet.stake
            net = spectator.payout - stake
            if spectator.payout == stake:
                text = f"🎟️ Te devuelven tus {format_amount(stake)}."
            elif spectator.payout:
                text = (
                    f"🎟️ ¡Acertaste! Cobras {format_amount(spectator.payout)} "
                    f"(+{format_amount(net)})."
                )
            else:
                text = f"🎟️ Fallaste: -{format_amount(stake)}."
            await self._tell(
                spectator.interaction, spectator.member.id, text, spectator.settlement, stake, net
            )

    async def _tell(
        self,
        interaction: discord.Interaction | None,
        user_id: int,
        text: str,
        settlement: BetSettlement | None,
        stake: int,
        net: int,
    ) -> None:
        if interaction is None:
            return
        if settlement is not None and (tax := gambling_tax_line(settlement)):
            text += f"\n{tax}"
        balance = settlement.balance if settlement is not None else 0
        if hint := await renta.hint(
            self.cog.bot,
            self.guild_id,
            user_id,
            bet_moment(stake=stake, net=net, balance_after=balance),
        ):
            text += f"\n{hint}"
        try:
            await interaction.followup.send(text, ephemeral=True)
        except discord.HTTPException:
            logger.debug("No se pudo mandar el resultado privado de un duelo", exc_info=True)

    async def track(self, fight: Fight) -> None:
        """Logros y estadísticas del casino de cada uno, después de enseñar el resultado."""
        when = datetime.now(TIMEZONE)
        bets = {k: s.bet for k, s in self.crowd.items()}
        crowd_pool = sum(pool_totals(bets).values())
        refund = all(s.payout == s.bet.stake for s in self.crowd.values())
        prize = duel_prize(self.stake)
        for corner in Corner:
            owner = self.owners[corner]
            won = fight.winner is corner
            net = (prize if won else 0) - self.stake
            settlement = self.settlements.get(corner)
            balance = settlement.balance if settlement else 0
            tax = settlement.tax_delta if settlement else 0
            delta = gallos_stats(
                fight=fight,
                corner=corner,
                rival=None,
                chance=float(self.chance if corner is Corner.RED else 1 - self.chance),
                stake=self.stake,
                net=net,
                record=self.finals.get(corner, owner.record),
                skin=owner.skin_key,
                paid_skin=owner.paid_skin,
                crowd_pool=crowd_pool,
                # La comisión del bote sale a medias de los dos.
                rake=(2 * self.stake - prize) // 2,
                when=when,
                power=self.finals.get(corner, owner.record).power,
                died=self.fallen.get(corner),
                killed=won and fight.loser in self.fallen,
            )
            delta.merge(
                casino_stats(stake=self.stake, net=net, balance_after=balance, tax_delta=tax)
            )
            await logros.casino_play(
                self.cog.bot, self.guild_id, owner.member, self.channel, delta, net=net
            )
            await apuestas.record(
                self.cog.bot,
                self.guild_id,
                owner.member,
                game=GAME,
                stake=self.stake,
                net=net,
                balance_after=balance,
                tax=tax,
                details=(
                    ("ko", int(fight.finish is Finish.KO)),
                    ("won", int(won)),
                    ("started", int(self.started_at)),
                ),
            )
        for spectator in self.crowd.values():
            stake = spectator.bet.stake
            net = spectator.payout - stake
            settlement = spectator.settlement
            balance = settlement.balance if settlement else 0
            tax = settlement.tax_delta if settlement else 0
            won = spectator.bet.corner is fight.winner
            odds_cents = spectator.payout * 100 // stake if won and not refund else 0
            delta = gallos_bet_stats(
                won=won,
                refunded=refund,
                odds_cents=odds_cents,
                rake=0 if refund else int(stake * POOL_CUT),
                when=when,
            )
            delta.merge(casino_stats(stake=stake, net=net, balance_after=balance, tax_delta=tax))
            await logros.casino_play(
                self.cog.bot, self.guild_id, spectator.member, self.channel, delta, net=net
            )
            await apuestas.record(
                self.cog.bot,
                self.guild_id,
                spectator.member,
                game=GAME,
                stake=stake,
                net=net,
                balance_after=balance,
                tax=tax,
                details=(("won", int(won)), ("started", int(self.started_at))),
            )

    async def shutdown(self) -> None:
        """Cierre ordenado: con la grada abierta se devuelve lo apostado a todos."""
        async with self._lock:
            if self.phase is Phase.LOBBY:
                for member in self.members:
                    await self.cog.pay(self.guild_id, member.id, self.stake)
                for user_id, spectator in self.crowd.items():
                    await self.cog.pay(self.guild_id, user_id, spectator.bet.stake)
                self.crowd.clear()
                text = "🛑 Duelo suspendido. Se devuelve lo apostado."
            elif self.phase is Phase.CHALLENGE:
                text = "🛑 Reto cancelado: el reñidero cierra."
            else:
                self.phase = Phase.DONE
                return
            self.phase = Phase.DONE
        if self.timer is not None:
            self.timer.cancel()
        if self.prepared is not None:
            self.prepared.cancel()
        await self.edit(content=None, embed=self.embed(text), view=None, attachments=[])


# -- Cog ---------------------------------------------------------------------------------


class SparringView(ui.View):
    """Los botones de un sparring: aceptar y, en el pulso, las tres jugadas."""

    def __init__(self, sparring: Sparring) -> None:
        super().__init__(timeout=None)
        self.sparring = sparring

    def _button(
        self,
        label: str,
        custom_id: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    ) -> None:
        button: ui.Button = ui.Button(label=label, style=style, custom_id=f"{GAME}:{custom_id}")
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)

    def invitation(self) -> SparringView:
        self.clear_items()
        self._button("🤝 Acepto", "spar_join", self.sparring.join, discord.ButtonStyle.success)
        return self

    def moves(self) -> SparringView:
        self.clear_items()
        for move in gym.PULSO_MOVES:
            self._button(
                gym.PULSO_LABELS[move],
                f"spar:{move}",
                self.sparring.picker(move),
                discord.ButtonStyle.primary,
            )
        return self


class Sparring:
    """Un sparring de pulso entre dos miembros, en público.

    Quien lo busca publica la invitación (`open`) y el primero que acepta
    (`join`) es su compañero. Al aceptar se cobra a los dos una sesión de 💪
    fuerza, como en el gimnasio (`Gallos.train`): el stat sube y se gana la
    forma del pago. Luego juegan `SPARRING_ROUNDS` asaltos de pulso a la vez: cada
    uno elige en secreto y, cuando han elegido los dos, se resuelve. La nota de
    cada uno (sus puntos entre los asaltos) es la del minijuego de su sesión
    (`gallos_gym.after_session`) y el ganador se lleva `SPARRING_WIN_FORM` de
    forma de más. Si alguien deja de elegir, los asaltos que faltan los pierde
    él (`SPARRING_PICK_SECONDS` por asalto).
    """

    def __init__(
        self, cog: Gallos, *, guild_id: int, channel: object, host: discord.abc.User
    ) -> None:
        self.cog = cog
        self.guild_id = guild_id
        self.channel = channel
        self.host = host
        self.guest: discord.abc.User | None = None
        self.view = SparringView(self)
        self.message: discord.Message | None = None
        self.trainings: dict[int, Training] = {}
        self.picks: dict[int, str] = {}
        self.points: dict[int, float] = {host.id: 0.0}
        self.assault = 0
        self.log: list[str] = []
        self.phase = "open"
        self.timer: asyncio.Task[None] | None = None
        self._lock = TimedLock("gallos:Sparring._lock")

    @property
    def members(self) -> list[discord.abc.User]:
        return [m for m in (self.host, self.guest) if m is not None]

    def name(self, user: discord.abc.User) -> str:
        training = self.trainings.get(user.id)
        rooster = training.after.name if training else ""
        return f"**{rooster}** ({user.display_name})" if rooster else user.display_name

    def text(self) -> str:
        """El texto del mensaje en cada fase."""
        if self.phase == "open":
            return (
                f"## 🤝 {self.host.display_name} busca sparring de pulso\n"
                f"Cinco asaltos de pulso entre dos gallos. Cada uno paga su sesión de 💪 fuerza "
                f"(lo que marque su gimnasio, IGIC incluido) y su gallo sube como en el "
                "gimnasio; la nota del pulso le da forma y el ganador se lleva "
                f"+{gym.SPARRING_WIN_FORM} más.\n"
                f"-# Caduca en {SPARRING_OPEN_SECONDS // 60} min si nadie acepta."
            )
        assert self.guest is not None
        host, guest = self.host, self.guest
        lines = [
            f"## 🤝 Sparring: {self.name(host)} 🆚 {self.name(guest)}",
            f"Puntos: {self.points[host.id]:g} a {self.points[guest.id]:g}",
        ]
        lines += [f"-# {line}" for line in self.log[-3:]]
        if self.phase == "fight":
            waiting = [m.display_name for m in self.members if m.id not in self.picks]
            lines.append(
                f"**Asalto {self.assault + 1} de {gym.SPARRING_ROUNDS}.** Cada uno elige en "
                "secreto. Aguantar gana a empujar, empujar gana a amagar y amagar gana a "
                "aguantar."
            )
            lines.append(f"-# Falta por elegir: {', '.join(waiting)}." if waiting else "")
            lines.append(
                f"-# {SPARRING_PICK_SECONDS} s por asalto: quien no elige, pierde el asalto."
            )
        return "\n".join(line for line in lines if line)

    def embed(self, text: str | None = None) -> discord.Embed:
        return discord.Embed(
            title="🐓 Gimnasio del reñidero", description=text or self.text(), color=COLOR_DUEL
        )

    async def edit(self, **kwargs: Any) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(**kwargs)
        except discord.HTTPException:
            logger.debug("No se pudo editar un sparring de gallos", exc_info=True)

    async def open(self) -> None:
        """Publica la invitación y deja corriendo su caducidad."""
        send = self.channel.send
        self.message = await send(embed=self.embed(), view=self.view.invitation())
        self.timer = asyncio.create_task(self._expire(), name="gallos-sparring")

    async def _expire(self) -> None:
        await self.cog.sleep(SPARRING_OPEN_SECONDS)
        async with self._lock:
            if self.phase != "open":
                return
            self.phase = "done"
        self.cog.release_sparring(self)
        await self.edit(embed=self.embed("🤝 Nadie ha aceptado el sparring."), view=None)

    async def join(self, interaction: discord.Interaction) -> None:
        """🤝 Acepto: cobra la sesión a los dos y empieza el pulso (se acepta antes de cobrar)."""
        user = interaction.user
        if user.id == self.host.id:
            await interaction.response.send_message(
                "No puedes hacer sparring contigo mismo.", ephemeral=True
            )
            return
        if (self.guild_id, user.id) in self.cog.busy:
            await interaction.response.send_message(
                "Tu gallo ya está en un duelo o en un sparring.", ephemeral=True
            )
            return
        await ack(interaction)
        async with self._lock:
            if self.phase != "open":
                await notify(interaction, "Este sparring ya no está libre.")
                return
            guest_record = await self.cog.repository.get(self.guild_id, user.id)
            guest = await self.cog.train(self.guild_id, user.id, guest_record, Stat.STRENGTH)
            if isinstance(guest, str):
                await notify(interaction, f"❌ {guest}")
                return
            host_record = await self.cog.repository.get(self.guild_id, self.host.id)
            host = await self.cog.train(self.guild_id, self.host.id, host_record, Stat.STRENGTH)
            if isinstance(host, str):
                await self.cog.refund_training(self.guild_id, user.id, guest)
                await notify(
                    interaction,
                    f"❌ {self.host.display_name} no puede entrenar ahora; se te devuelve "
                    "lo pagado.",
                )
                return
            self.guest = user
            self.trainings = {user.id: guest, self.host.id: host}
            self.points[user.id] = 0.0
            self.cog.busy.add((self.guild_id, user.id))
            self.phase = "fight"
        if self.timer is not None:
            self.timer.cancel()
        self.timer = asyncio.create_task(self._pick_clock(0), name="gallos-sparring-asalto")
        await edit(interaction, embed=self.embed(), view=self.view.moves())
        taxes = " · ".join(
            f"{m.display_name} {format_amount(self.trainings[m.id].total)} "
            f"({format_amount(self.trainings[m.id].tax)} de IGIC)"
            for m in self.members
        )
        await notify(
            interaction,
            f"🤝 Pagáis la sesión de fuerza: {taxes}. 🐶 {TAX_COLLECTOR} se lleva el IGIC "
            f"(tipo general, {IGIC_GENERAL_RATE * 100:g} %).",
            ephemeral=False,
        )
        await renta.remind(self.cog.bot, interaction)

    async def _pick_clock(self, assault: int) -> None:
        """Si al acabar el tiempo de un asalto falta alguien por elegir, lo pierde él."""
        await self.cog.sleep(SPARRING_PICK_SECONDS)
        async with self._lock:
            if self.phase != "fight" or self.assault != assault:
                return
            finished = self._resolve()
        if finished:
            await self._finish(None)
            return
        self.timer = asyncio.create_task(
            self._pick_clock(self.assault), name="gallos-sparring-asalto"
        )
        await self.edit(embed=self.embed(), view=self.view.moves())

    def picker(self, move: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await self.pick(interaction, move)

        return callback

    async def pick(self, interaction: discord.Interaction, move: str) -> None:
        """Una jugada: solo cambia memoria hasta el último asalto (entonces, guarda)."""
        user = interaction.user
        if user.id not in {m.id for m in self.members}:
            await interaction.response.send_message("Este sparring no es tuyo.", ephemeral=True)
            return
        if self.phase != "fight" or user.id in self.picks:
            await interaction.response.send_message(
                "Ya has elegido en este asalto.", ephemeral=True
            )
            return
        self.picks[user.id] = move
        finished = False
        if len(self.picks) == 2:
            finished = self._resolve()
        if not finished:
            await interaction.response.edit_message(embed=self.embed(), view=self.view.moves())
            if len(self.picks) == 0:
                if self.timer is not None:
                    self.timer.cancel()
                self.timer = asyncio.create_task(
                    self._pick_clock(self.assault), name="gallos-sparring-asalto"
                )
            return
        await ack(interaction)
        await self._finish(interaction)

    def _resolve(self) -> bool:
        """Resuelve el asalto con lo elegido (lo que falte, vacío). Devuelve si se acabó."""
        assert self.guest is not None
        host, guest = self.host, self.guest
        mine, theirs = self.picks.get(host.id, ""), self.picks.get(guest.id, "")
        gained = gym.pulso_points(mine, theirs)
        self.points[host.id] += gained
        self.points[guest.id] += 1 - gained
        label = {m: m for m in gym.PULSO_MOVES}
        self.log.append(
            f"Asalto {self.assault + 1}: {host.display_name} {label.get(mine, 'no elige')}, "
            f"{guest.display_name} {label.get(theirs, 'no elige')}."
        )
        self.picks = {}
        self.assault += 1
        if self.assault >= gym.SPARRING_ROUNDS:
            self.phase = "done"
            return True
        return False

    async def _finish(self, interaction: discord.Interaction | None) -> None:
        """Guarda la nota de cada uno como su minijuego y enseña cómo ha acabado."""
        assert self.guest is not None
        if self.timer is not None and interaction is not None:
            self.timer.cancel()
        now = self.cog.wall_clock()
        day = local_day(now)
        host, guest = self.host, self.guest
        top = max(self.points.values())
        winners = [m for m in self.members if self.points[m.id] == top]
        winner = winners[0] if len(winners) == 1 else None
        lines = [f"## 🤝 Sparring: {self.name(host)} 🆚 {self.name(guest)}"]
        lines.append(
            f"### {self.points[host.id]:g} a {self.points[guest.id]:g} · "
            + (f"gana {winner.display_name}" if winner else "empate")
        )
        lines += [f"-# {line}" for line in self.log]
        sessions: dict[int, gym.Session] = {}
        afters: dict[int, RoosterRecord] = {}
        for member in self.members:
            score = self.points[member.id] / gym.SPARRING_ROUNDS
            won = winner is not None and member.id == winner.id
            change = self._change(member.id, score, won, now, day, sessions)
            _, afters[member.id] = await self.cog.repository.change(
                self.guild_id, member.id, change
            )
            training, after = self.trainings[member.id], afters[member.id]
            lines.append(
                f"{self.name(member)}: 💪 Fuerza {training.before.strength} → {after.strength} · "
                f"🔋 Forma {round(form_now(training.before, now))} → {round(form_now(after, now))}"
            )
        self.cog.release_sparring(self)
        if interaction is not None:
            await edit(interaction, embed=self.embed("\n".join(lines)), view=None)
        else:
            await self.edit(embed=self.embed("\n".join(lines)), view=None)
        for member in self.members:
            other = guest if member.id == host.id else host
            won = winner is not None and member.id == winner.id
            await logros.track(
                self.cog.bot,
                self.guild_id,
                member,
                self.channel,
                StatDelta(
                    add={
                        "gallos_sparrings": 1,
                        "gallos_sparring_wins": int(won),
                        "gallos_sparring_shutout": int(won and self.points[other.id] == 0),
                    }
                ),
            )
            await logros.track(
                self.cog.bot,
                self.guild_id,
                member,
                self.channel,
                gallos_drill_stats(
                    kind="pulso",
                    score=sessions[member.id].score,
                    bonus=sessions[member.id].bonus,
                    combo=sessions[member.id].combo,
                    event=None,
                    form=round(form_now(afters[member.id], now)),
                ),
            )
        if interaction is not None:
            await renta.remind(self.cog.bot, interaction)

    @staticmethod
    def _change(
        user_id: int,
        score: float,
        won: bool,
        now: float,
        day: date,
        sessions: dict[int, gym.Session],
    ) -> Callable[[RoosterRecord], RoosterRecord]:
        """El cambio de la ficha de uno: su nota como minijuego y, si gana, la forma de más."""

        def change(record: RoosterRecord) -> RoosterRecord:
            trait = gym.trait_for(user_id, record)
            after, session = gym.after_session(
                record, Stat.STRENGTH, score, trait=trait, now=now, day=day
            )
            sessions[user_id] = session
            if won:
                after = with_form(after, form_now(after, now) + gym.SPARRING_WIN_FORM, now)
            return after

        return change

    async def shutdown(self) -> None:
        """Al apagar: corta la espera; lo pagado se queda (como dejar el minijuego)."""
        if self.timer is not None:
            self.timer.cancel()
        self.phase = "done"
        await self.edit(view=None)


@dataclass(slots=True)
class Drill:
    """El minijuego de una sesión ya pagada, mientras se juega.

    Attributes:
        stat: El stat entrenado.
        game: El minijuego (`gallos_gym`).
        paid: El texto del pago (lo que subió, la frase y el IGIC), para el final.
        result: El texto del final del minijuego, cuando acaba.
        event: El evento que pide decidir (el suplemento), mientras se decide.
        cheers: Quién lo ha animado (IDs), hasta `gallos_gym.CHEER_MAX`.
        saboteur: Quién lo ha saboteado (uno como mucho).
        coach_open: Si el dueño deja que lo juegue otro.
        coach: Quién lo juega en su lugar (el primero que pulsa, si está abierto).
    """

    stat: Stat
    game: gym.GymGame
    paid: str
    result: str = ""
    event: gym.GymEvent | None = None
    cheers: set[int] = field(default_factory=set)
    saboteur: discord.abc.User | None = None
    coach_open: bool = False
    coach: discord.abc.User | None = None


@dataclass(frozen=True, slots=True)
class Training:
    """Un entrenamiento ya cobrado y apuntado.

    Attributes:
        stat: El stat que ha subido.
        before: La ficha de antes.
        after: La ficha de después.
        base: Base imponible (el precio sin IGIC).
        tax: IGIC cobrado sobre la base.
        balance: Saldo del miembro tras pagar.
    """

    stat: Stat
    before: RoosterRecord
    after: RoosterRecord
    base: int
    tax: int
    balance: int

    @property
    def total(self) -> int:
        """Lo que ha pagado el miembro: base más IGIC."""
        return self.base + self.tax


class Gallos(commands.Cog, name="Gallos"):
    """Peleas de gallos contra la casa y entre miembros, con los yapdollars de la economía."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        repository: GallosRepository,
        casino_channel_ids: CasinoChannelIds = frozenset(),
        rng: random.Random | None = None,
        renderer: GallosScene | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.repository = repository
        self.casino_channel_ids = casino_channel_ids
        # `secrets` usa el azar del sistema operativo: no se puede predecir.
        self.rng = rng or secrets.SystemRandom()
        self.renderer = renderer or GallosScene()
        self.sleep = sleep
        self.wall_clock = wall_clock
        self.panels: set[HousePanel] = set()
        self.duels: set[Duel] = set()
        #: Quién está en un reto o un duelo (servidor, miembro): uno a la vez.
        self.busy: set[tuple[int, int]] = set()
        #: Quién le ha echado de comer hoy a cada gallo: (servidor, dueño, día) → IDs.
        # shortcut: en memoria, se pierde al reiniciar (alguien podría repetir ese día); el
        # tope de `FEEDS_PER_DAY` por gallo sí se guarda en la ficha.
        self.fed: dict[tuple[int, int, date], set[int]] = {}
        self.sparrings: set[Sparring] = set()

    async def cog_unload(self) -> None:
        """Al apagar, devuelve lo apostado en las gradas abiertas y cierra el pintor."""
        self._tournaments.cancel()
        for duel in list(self.duels):
            try:
                await duel.shutdown()
            except Exception:
                logger.exception("No se pudo cerrar un duelo de gallos al apagar")
            if duel.task is not None:
                duel.task.cancel()
        self.duels.clear()
        for sparring in list(self.sparrings):
            try:
                await sparring.shutdown()
            except Exception:
                logger.exception("No se pudo cerrar un sparring de gallos al apagar")
        self.sparrings.clear()
        for panel in list(self.panels):
            panel.stop()
        self.panels.clear()
        await self.renderer.close()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra las fichas de los gallos del servidor que deja el bot."""
        await self.repository.delete_guild_data(guild.id)

    async def share_club_form(self, guild_id: int, user_id: int, now: float) -> None:
        """Le da forma a cada compañero de gallera de quien acaba de entrenar (no lanza)."""
        try:
            club = await self.repository.club_of(guild_id, user_id)
            if club is None:
                return
            day = local_day(now)
            for mate in club.members:
                if mate != user_id:
                    await self.repository.change(
                        guild_id,
                        mate,
                        lambda record: torneo.after_club_form(record, now=now, day=day),
                    )
        except Exception:
            logger.exception("No se pudo repartir la forma de la gallera")

    async def cog_load(self) -> None:
        """Arranca el reloj del torneo del domingo."""
        self._tournaments.start()

    @tasks.loop(minutes=10)
    async def _tournaments(self) -> None:
        try:
            await self.run_tournaments()
        except Exception:
            # Una excepción sin capturar pararía la tarea para siempre.
            logger.exception("Error jugando el torneo de gallos")

    @_tournaments.before_loop
    async def _before_tournaments(self) -> None:
        await self.bot.wait_until_ready()

    async def run_tournaments(self) -> int:
        """Juega el torneo de esta semana en cada servidor al que le falte, si ya toca.

        Returns:
            Cuántos torneos ha anunciado.
        """
        now = self.wall_clock()
        if not torneo.due(now):
            return 0
        week = torneo.week_key(now)
        played = 0
        for guild_id, channel_id in await self.repository.tournament_targets(week):
            if await self.tournament(guild_id, channel_id, week, now):
                played += 1
        return played

    async def tournament(self, guild_id: int, channel_id: int, week: str, now: float) -> bool:
        """Juega y anuncia el torneo de un servidor. Devuelve si lo ha anunciado."""
        records = await self.repository.records(guild_id)
        clubs = await self.repository.club_ids(guild_id)
        field = torneo.entrants(records, now=now, clubs=clubs)
        bracket = torneo.play(field, self.rng)
        champion = bracket.champion.user_id if bracket is not None else None
        if not await self.repository.finish_tournament(guild_id, week, champion):
            return False
        if bracket is None:
            return False
        names = {c.id: c.name for c in await self.repository.clubs(guild_id)}
        text = tournament_text(bracket, records, names, now)
        channel = self.bot.get_channel(channel_id)
        if isinstance(channel, discord.abc.Messageable):
            try:
                await channel.send(
                    embed=discord.Embed(
                        title="🏆 Torneo del domingo del reñidero",
                        description=text,
                        color=COLOR_WIN,
                    ),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                logger.warning("No se pudo anunciar el torneo de gallos", exc_info=True)
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return True
        best = torneo.best_club(bracket)
        for entrant in {
            e.user_id: e for rnd in bracket.rounds for m in rnd for e in (m.red, m.blue)
        }.values():
            member = guild.get_member(entrant.user_id)
            if member is None:
                continue
            wins = bracket.wins(entrant.user_id)
            add = {"gallos_tournaments": 1, "gallos_tournament_wins": wins}
            if entrant.user_id == bracket.champion.user_id:
                add["gallos_tournament_titles"] = 1
            if entrant.user_id in (bracket.champion.user_id, bracket.finalist.user_id):
                add["gallos_tournament_finals"] = 1
            if best is not None and entrant.club == best[0]:
                add["gallos_club_of_week"] = 1
            await logros.track(self.bot, guild_id, member, channel, StatDelta(add=add))
        return True

    def release_sparring(self, sparring: Sparring) -> None:
        """Libera a los dos de un sparring terminado (o caducado)."""
        self.sparrings.discard(sparring)
        for member in sparring.members:
            self.busy.discard((sparring.guild_id, member.id))

    async def refund_training(self, guild_id: int, user_id: int, training: Training) -> None:
        """Devuelve un entrenamiento cobrado que no se llega a hacer (el sparring que no empieza).

        Tratamiento fiscal: factura rectificativa (`EconomyService.refund_purchase`): la
        caja devuelve la base y el Estado el IGIC, y la ficha vuelve a como estaba.
        """
        await self.economy.refund_purchase(
            guild_id,
            user_id,
            base=training.base,
            tax=training.tax,
            concept=TRAIN_CONCEPT,
            release=self.repository.restore(guild_id, user_id, training.before),
        )

    def release(self, duel: Duel) -> None:
        """Libera a los dos dueños de un duelo terminado."""
        self.duels.discard(duel)
        for member in duel.members:
            self.busy.discard((duel.guild_id, member.id))

    async def pay(self, guild_id: int, user_id: int, amount: int) -> BetSettlement | None:
        """Paga lo devuelto por una pelea (`pay_winnings`, 0 si pierde) y ajusta el IRPF.

        Tratamiento fiscal: juego (ver la docstring del módulo). `None` si el
        premio pasaría del saldo máximo y no se pudo pagar.
        """
        try:
            return await self.economy.pay_winnings(guild_id, user_id, game=GAME, amount=amount)
        except BalanceLimitError:
            logger.warning("Premio de gallos por encima del saldo máximo; no se paga.")
            return None

    async def train(
        self, guild_id: int, user_id: int, record: RoosterRecord, stat: Stat
    ) -> Training | str:
        """Cobra un entrenamiento y sube el stat, todo en una transacción.

        Tratamiento fiscal: IGIC general (7 %), porque entrenar es una prestación
        de servicios hecha en Canarias a título oneroso (art. 4 de la Ley 20/1991)
        y el tipo general es el del art. 51 de la Ley 4/2012 (ver
        `EconomyService.purchase`). La base (`gallos.train_price`) va a la caja de
        la tienda, el IGIC al Estado y el stat sube dentro de la misma
        transacción (`GallosRepository.train`). Es un sumidero: no crea dinero. No
        hay IRPF (gastar no es renta) ni retención de juego (no es una apuesta).

        Args:
            record: La ficha que ve el miembro; de ella salen el precio y las
                comprobaciones previas. El repositorio las repite al cobrar.

        Returns:
            El `Training` si se ha pagado, o el texto del problema (sin saldo,
            descansando, tope diario, stat al máximo) sin haber movido dinero.
        """
        now = self.wall_clock()
        day = local_day(now)
        try:
            check_training(record, stat, now=now, day=day)
        except TrainError as error:
            return str(error)
        level = record.stat(stat)
        base = train_price(level)
        tax = igic(base)
        try:
            (before, after), balance = await self.economy.purchase(
                guild_id,
                user_id,
                base=base,
                tax=tax,
                concept=TRAIN_CONCEPT,
                reserve=self.repository.train(
                    guild_id, user_id, stat, level=level, now=now, day=day
                ),
            )
        except TrainError as error:
            return str(error)
        except InsufficientFundsError as error:
            return insufficient_text(error.balance, base + tax)
        return Training(stat, before, after, base, tax, balance)

    async def bury(
        self, guild_id: int, user_id: int, *, killer_kind: str, killer: str
    ) -> RoosterRecord:
        """Entierra al gallo del miembro y devuelve la ficha del nuevo (no mueve dinero)."""
        return await self.repository.bury(
            guild_id, user_id, killer_kind=killer_kind, killer=killer, now=self.wall_clock()
        )

    async def shout(self, channel: object, text: str, user: discord.abc.User) -> None:
        """Anuncia en el canal las victorias gordas."""
        if not isinstance(channel, discord.abc.Messageable):
            return
        try:
            await channel.send(
                text, allowed_mentions=discord.AllowedMentions(users=[user], everyone=False)
            )
        except discord.HTTPException:
            logger.debug("No se pudo anunciar una pelea de gallos", exc_info=True)

    async def _corner(self, guild_id: int, member: discord.abc.User) -> Owner:
        """El dueño con la ficha de su gallo y la skin que lleva."""
        await tienda.grant_free_skins(self.bot, guild_id, member.id, SKIN_GAME)
        record = await self.repository.get(guild_id, member.id)
        return Owner(member, record, tienda.skin_of(self.bot, guild_id, member.id, SKIN_GAME))

    async def gallo_embed(self, guild_id: int, member: discord.abc.User) -> discord.Embed:
        """La sección 🐓 Gallo de `perfil`: la ficha del gallo de un miembro."""
        record = await self.repository.get(guild_id, member.id)
        skin = tienda.skin_of(self.bot, guild_id, member.id, SKIN_GAME)
        ranking = await self.repository.ranking(guild_id, limit=100)
        place = next((i for i, (uid, _r) in enumerate(ranking, 1) if uid == member.id), None)
        fallen = await self.repository.graveyard(guild_id, member.id, limit=3)
        lines = [
            f"### 🐓 {record.name}",
            f"Aspecto: {skin.label if skin else '🐓 Gallo colorado (de serie)'}",
            *stat_lines(record),
            form_line(round(form_now(record, self.wall_clock()))),
            f"Personalidad: {gym.trait_for(member.id, record).label}"
            + (f" · generación {record.generation + 1}" if record.generation else ""),
            record_line(record),
        ]
        if injured(record, self.wall_clock()):
            lines.append(injury_text(record, self.wall_clock()))
        if record.titles:
            lines.append(
                f"🏆 {record.titles} torneo{'s' if record.titles != 1 else ''} del domingo"
            )
        if record.fights:
            lines.append(
                f"{record.losses} derrota{'s' if record.losses != 1 else ''} · "
                f"{round(100 * record.wins / record.fights)} % de victorias"
            )
        if record.day_streak > 1:
            lines.append(f"📅 {record.day_streak} días seguidos peleando")
        if place is not None:
            lines.append(f"🏟️ Puesto {place} del reñidero del servidor")
        lines += fallen_lines(fallen)
        lines.append(
            "-# `gallos` para pelear · 🏋️ para entrenarlo · ✏️ para bautizarlo · "
            "🎨 para cambiarle el aspecto · si pierde por KO puede morir"
        )
        return discord.Embed(
            title=f"🐓 El gallo de {member.display_name}",
            description="\n".join(lines),
            color=COLOR_IDLE,
        )

    # -- gallos -----------------------------------------------------------------------

    async def _gallos_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        rival: discord.abc.User | None,
        send: Callable[..., Awaitable[discord.Message]],
        send_error: Callable[[str], Awaitable[None]],
    ) -> bool:
        """Lógica compartida de `/gallos` y `.gallos`: abre el reñidero o reta a alguien.

        Returns:
            Si ha retado a alguien (para el aviso de la Renta del comando).
        """
        if guild is None:
            await send_error("Las peleas de gallos solo se hacen dentro de un servidor.")
            return False
        if error := casino_channel_error(self.casino_channel_ids, channel, "El reñidero"):
            await send_error(error)
            return False
        balance = await self.economy.balance(guild.id, user.id)
        try:
            stake = (
                parse_amount(amount_text, balance)
                if amount_text
                else max(1, min(DEFAULT_STAKE, balance))
            )
        except ValueError as error:
            await send_error(str(error))
            return False
        if balance <= 0 or stake > balance:
            await send_error(insufficient_text(balance, stake))
            return False
        if rival is None:
            await self._open_panel(guild.id, channel, user, stake, balance, send)
            return False
        if rival.id == user.id:
            await send_error("No puedes retarte a ti mismo. Para eso está el espejo.")
            return False
        if rival.bot:
            await send_error("Los bots no tienen gallo. Pelea contra la casa con `gallos`.")
            return False
        for member in (user, rival):
            if (guild.id, member.id) in self.busy:
                await send_error(f"{member.display_name} ya está en un duelo de gallos.")
                return False
        red = await self._corner(guild.id, user)
        blue = await self._corner(guild.id, rival)
        for owner in (red, blue):
            if injured(owner.record, self.wall_clock()):
                await send_error(injury_text(owner.record, self.wall_clock()))
                return False
        duel = Duel(self, guild_id=guild.id, channel=channel, red=red, blue=blue, stake=stake)
        self.busy.update({(guild.id, user.id), (guild.id, rival.id)})
        self.duels.add(duel)
        try:
            await duel.open(send)
        except Exception:
            self.release(duel)
            raise
        await logros.track(
            self.bot, guild.id, user, channel, StatDelta(add={"gallos_challenges": 1})
        )
        return True

    async def _open_panel(
        self,
        guild_id: int,
        channel: object,
        user: discord.abc.User,
        stake: int,
        balance: int,
        send: Callable[..., Awaitable[discord.Message]],
    ) -> None:
        panel = HousePanel(self, guild_id=guild_id, owner=user, stake=stake)
        panel.channel = channel
        if isinstance(channel_id := getattr(channel, "id", None), int):
            # Ahí se anunciará el torneo del domingo de este servidor.
            await self.repository.remember_channel(guild_id, channel_id)
        panel.balance = balance
        panel.record = (await self._corner(guild_id, user)).record
        png = await panel.poster()
        panel.rebuild()
        panel.message = await send(
            embed=panel.embed(), file=discord.File(io.BytesIO(png), filename=PNG_NAME), view=panel
        )
        self.panels.add(panel)

    @app_commands.command(
        name="gallos",
        description="Peleas de gallos: contra los de la casa o reta al gallo de alguien.",
    )
    @app_commands.describe(
        cantidad="Apuesta: 500, 2k, all… (por defecto 100)",
        rival="Persona a la que retar a un duelo (si no, peleas contra la casa)",
    )
    @app_commands.guild_only()
    async def gallos(
        self,
        interaction: discord.Interaction,
        cantidad: str | None = None,
        rival: discord.Member | None = None,
    ) -> None:
        """Abre tu reñidero o reta a alguien.

        Solo en los canales del casino que elige `garito` (sin elegir, los de `CASINO_CHANNEL_IDS`)
        y en sus hilos. Contra la casa cobra al pelear; en un duelo, a los dos al aceptar.
        """
        responder = InteractionResponder(interaction)

        async def send(**kwargs: Any) -> discord.Message:
            await interaction.response.send_message(**kwargs)
            return await interaction.original_response()

        challenged = await self._gallos_impl(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            rival=rival,
            send=send,
            send_error=responder.send_error,
        )
        if challenged:
            # El que reta recibe aquí su resultado privado del duelo.
            for duel in self.duels:
                if duel.owner(Corner.RED).member.id == interaction.user.id:
                    duel.interactions[interaction.user.id] = interaction

    @commands.command(name="gallos")
    @commands.guild_only()
    async def gallos_text(self, ctx: commands.Context, *args: str) -> None:
        """Versión de texto: `.gallos`, `.gallos 500`, `.gallos @alguien 500`.

        La cantidad y la mención van en cualquier orden.
        """
        responder = ContextResponder(ctx)
        rival = next((m for m in ctx.message.mentions if m.id != ctx.author.id), None)
        amounts = [a for a in args if not (a.startswith("<@") and a.endswith(">"))]
        if len(amounts) > 1:
            await responder.send_error("No entiendo tanto. Ejemplo: `.gallos @alguien 500`.")
            return

        async def send(**kwargs: Any) -> discord.Message:
            return await ctx.send(**kwargs)

        await self._gallos_impl(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            amount_text=amounts[0] if amounts else None,
            rival=rival,
            send=send,
            send_error=responder.send_error,
        )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía y las fichas de los gallos del bot."""
    await bot.add_cog(
        Gallos(
            bot,
            economy=bot.economy,
            repository=bot.gallos,
            casino_channel_ids=bot.casino_channels,
        )
    )
