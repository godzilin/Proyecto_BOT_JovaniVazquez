"""Carreras de caballos: `caballo`, una carrera por canal con cuotas de verdad.

Solo hay carrera cuando alguien la pide: `/caballo` o `.caballo` abre la del
canal (o apuesta en la que esté abierta). No hay carreras programadas ni
avisos a nadie, salvo el anuncio de quien se lleva el bote del Gran Premio.

Cada carrera pasa por tres momentos en el mismo mensaje:

1. **Parrilla** (2 minutos como mucho): la imagen con los caballos, su
   forma, el terreno y las cuotas a ganador; el pronóstico de Perro Sanxe; el
   parte del tiempo; y, en vivo, quién va con quién y con quién va «el
   pueblo». Botones: 🎟️ **Apostar** abre un panel privado con el tipo de
   boleto (ganador, colocado, gemela o trío), los caballos y la cantidad;
   🐶 **Lo de Sanxe**, 🐑 **Con el pueblo** y 🎲 **Al azar** apuestan tu ficha a
   ganador de un toque; 🪙 **Ficha** la cambia. Un boleto por persona y carrera.
   ✅ **Listo**: quien ya tiene boleto avisa de que no espera a nadie más.
   Cuando todos los que han apostado están listos, los caballos salen sin
   esperar al reloj.
   `.caballo 500 3` apuesta 500 a ganador al 3; `.caballo 500 3-5` a la gemela;
   `.caballo 500 3-5-1` al trío; `.caballo 500 3 colocado` a colocado.
2. **Carrera**: el GIF con los caballos galopando. Se dibuja con canvas en
   Node, sin navegador (`bot.services.horses_scene`); si no hay Node, en
   Chromium y, si tampoco, con Pillow (`bot.services.horses_render`). La
   parrilla y el boleto son HTML/CSS y los captura Chromium.
3. **Llegada**: el podio, la narración, quién cobra y quién no, y lo que se te
   escapó si fallaste por poco. Botón 🏇 **Otra carrera**.

El **Gran Premio** sale solo, en la carrera que toque cuando se han corrido
`GRAND_PRIX_EVERY` carreras normales y han pasado `GRAND_PRIX_COOLDOWN`
desde el último: ocho caballos, 2.400 m y un bote para quien acierte el
ganador con un boleto de 100 Y$ o más. Si nadie acierta, el bote crece para el
siguiente. Quien se lo lleva sale mencionado en un anuncio aparte, con su
boleto premiado.

Reglas, cuotas y narración en `bot.services.horses`; historial del establo en
`bot.repositories.horses`.

Dinero (`EconomyService`): la apuesta se cobra al hacer el boleto
(`place_bet`) y el premio se paga al salir los caballos (`pay_winnings`, con
0 si falla), antes de enseñar la carrera: así un apagado a mitad del GIF no
deja a nadie sin cobrar. Tratamiento fiscal: juego, como el resto del casino
(premios del juego como ganancia patrimonial de la base general, art. 33.1
LIRPF, y pérdidas que solo compensan ganancias de juego, art. 33.5.d LIRPF):
entra en la retención diaria del casino y en la renta semanal. El bote del
Gran Premio va dentro del mismo pago y tributa igual. **El bote sale de la
nada**, como los premios de la casa en cualquier juego: decisión del
proyecto. Lo compensan el margen de las cuotas (la casa se queda el 5-10 % de
lo apostado) y la retención del casino. Cifras en `bot.services.horses`
(`GRAND_PRIX_POT` y siguientes): como mucho un Gran Premio cada 4 horas por
servidor y un boleto por persona, así que nadie puede cubrir todos los
caballos para quedarse el bote.

Si el bot se apaga durante la parrilla, se devuelve lo apostado. Si
`CASINO_CHANNEL_IDS` está configurado, solo se corre en esos canales.

Permisos del bot en el canal: enviar mensajes, insertar enlaces, adjuntar
archivos y añadir reacciones (para confirmar `.caballo` sin escribir). No
necesita intents especiales.
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
import secrets
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

import discord
import numpy as np
from discord import app_commands
from discord.ext import commands

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs.casino import casino_channel_error, insufficient_text
from bot.repositories.horses import HorseRepository, Meta
from bot.services.achievements import casino_stats, horses_stats
from bot.services.economy import (
    BalanceLimitError,
    BetSettlement,
    EconomyService,
    InsufficientFundsError,
    format_amount,
    gambling_tax_line,
    parse_amount,
)
from bot.services.horses import (
    BET_KINDS,
    GAME,
    GRAND_PRIX_COOLDOWN,
    GRAND_PRIX_EVERY,
    GRAND_PRIX_MIN_STAKE,
    RTP,
    BetKind,
    HorseError,
    HorseRecord,
    Odds,
    Pick,
    RaceCard,
    RaceResult,
    Tip,
    comeback,
    commentary,
    estimate,
    format_odds,
    grand_prix_due,
    margin_text,
    near_miss,
    new_card,
    next_pot,
    parse_pick,
    payout,
    photo_finish,
    place_slots,
    pot_eligible,
    run_race,
    sanxe_tip,
)
from bot.services.horses_render import Media
from bot.services.horses_scene import SceneRenderer
from bot.services.levels import TIMEZONE
from bot.services.pets import bet_moment
from bot.services.taxes import TAX_COLLECTOR
from bot.utils.interactions import ack, edit, notify
from bot.utils.responder import ContextResponder

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

DEFAULT_STAKE = 100
#: Parrilla abierta a apuestas como mucho. Si todos los que han apostado
#: pulsan ✅ Listo, los caballos salen antes.
LOBBY_SECONDS = 120
GRAND_PRIX_LOBBY_SECONDS = 120
#: Una carrera que sale con ✅ Listo antes de esto es un «visto y no visto» (logro).
FLASH_START_SECONDS = 15
#: Margen tras el GIF antes de enseñar la llegada (lo que tarda en cargar).
REVEAL_MARGIN_SECONDS = 1.5
#: Boletos que se listan por nombre; el resto se resume.
LIST_LIMIT = 12
#: Cuota a partir de la cual un boleto ganador se enseña con su sello (10x).
BIG_TICKET_ODDS = 1_000
#: Boletos premiados que se publican como imagen tras una carrera.
MAX_BIG_TICKETS = 3
#: Cuánto vive el botón de 🏇 Otra carrera.
REMATCH_SECONDS = 15 * 60

CARD_PNG = "parrilla.png"
RACE_GIF = "carrera.gif"
FINISH_PNG = "llegada.png"

COLOR_LOBBY = discord.Color.from_rgb(46, 125, 50)
COLOR_GRAND_PRIX = discord.Color.from_rgb(255, 196, 0)
COLOR_RUNNING = discord.Color.from_rgb(88, 101, 242)
COLOR_FINISH = discord.Color.from_rgb(43, 45, 49)

NO_MENTIONS = discord.AllowedMentions.none()


class Phase(Enum):
    """Momento de la carrera."""

    LOBBY = "lobby"
    RUNNING = "running"
    DONE = "done"


@dataclass(slots=True, eq=False)
class Ticket:
    """El boleto de una persona en una carrera.

    Attributes:
        via: Cómo apostó (`panel`, `sanxe`, `pueblo`, `azar` o `texto`), para los logros.
        interaction: La interacción con la que apostó, si fue con botón o
            slash: sirve para mandarle su resultado en privado.
    """

    user: discord.abc.User
    pick: Pick
    stake: int
    odds: int
    via: str
    interaction: discord.Interaction | None = None
    prize: int = 0
    pot_share: int = 0
    settlement: BetSettlement | None = None
    #: Cuándo se hizo el boleto (epoch). Las porras no cuentan los anteriores a su cierre.
    placed_at: float = 0.0
    #: Si ha pulsado ✅ Listo.
    ready: bool = False

    @property
    def net(self) -> int:
        """Ganado menos apostado."""
        return self.prize - self.stake


def short_name(name: str) -> str:
    """Nombre recortado para las listas."""
    return name if len(name) <= 16 else name[:15] + "…"


def tax_summary(tickets: list[Ticket]) -> str | None:
    """Subtexto con lo que retiene o devuelve Hacienda a cada uno en la carrera."""
    taken, given = [], []
    for ticket in tickets:
        settlement = ticket.settlement
        if settlement is None or not settlement.tax_delta:
            continue
        text = (
            f"{format_amount(abs(settlement.tax_delta))} a {short_name(ticket.user.display_name)}"
        )
        (taken if settlement.tax_delta > 0 else given).append(text)
    parts = []
    if taken:
        parts.append("se lleva " + ", ".join(taken))
    if given:
        parts.append("devuelve " + ", ".join(given))
    return f"-# 🐶 {TAX_COLLECTOR} {' y '.join(parts)}." if parts else None


def horse_label(card: RaceCard, index: int) -> str:
    """`3 · Gofio Express`."""
    return f"{index + 1} · {card.horses[index].name}"


def pick_names(card: RaceCard, pick: Pick) -> list[str]:
    """Nombres de los caballos de un boleto, en su orden."""
    return [card.horses[i].name for i in pick.horses]


# -- Panel de apuesta -----------------------------------------------------------------------------


class AmountModal(discord.ui.Modal, title="🪙 Tu ficha"):
    """Pide la cantidad que se apuesta con los botones rápidos y el panel."""

    amount: discord.ui.TextInput = discord.ui.TextInput(
        label="Cantidad (500, 2k, all…)", placeholder="100", max_length=12
    )

    def __init__(self, race: Race, panel: BetPanel | None = None) -> None:
        super().__init__()
        self.race = race
        self.panel = panel

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Guarda la ficha; si viene del panel, lo redibuja con la nueva cantidad."""
        cog = self.race.cog
        # Leer el saldo va a la base de datos: se acepta el formulario antes.
        await ack(interaction)
        balance = await cog.economy.balance(self.race.guild_id, interaction.user.id)
        try:
            stake = parse_amount(str(self.amount.value), balance)
        except ValueError as error:
            await notify(interaction, str(error))
            return
        cog.set_ficha(self.race.guild_id, interaction.user.id, stake)
        if self.panel is not None:
            self.panel.stake = stake
            await edit(interaction, content=self.panel.text(), view=self.panel.refresh())
            return
        await notify(interaction, f"🪙 Tu ficha: **{format_amount(stake)}**.")


class BetPanel(discord.ui.View):
    """Panel privado para hacer un boleto: tipo, caballos en orden y cantidad."""

    def __init__(self, race: Race, user_id: int, stake: int) -> None:
        super().__init__(timeout=LOBBY_SECONDS * 3)
        self.race = race
        self.user_id = user_id
        self.stake = stake
        self.kind = BetKind.WIN
        self.horses: list[int | None] = [None, None, None]
        self.refresh()

    def pick(self) -> Pick | None:
        """El boleto elegido, si está completo y sin caballos repetidos."""
        chosen = self.horses[: self.kind.picks]
        if any(h is None for h in chosen) or len(set(chosen)) != len(chosen):
            return None
        return Pick(self.kind, tuple(h for h in chosen if h is not None))

    def text(self) -> str:
        """Lo que dice el panel: el boleto que llevas montado y lo que pagaría."""
        race = self.race
        lines = [
            f"### 🎟️ {race.card.name}",
            f"{self.kind.emoji} **{self.kind.label}**: {self.kind.explain(race.card.size)}",
        ]
        pick = self.pick()
        if pick is None:
            lines.append("Elige los caballos en los menús.")
        else:
            odds = race.odds.odds(pick)
            names = " → ".join(pick_names(race.card, pick))
            lines.append(
                f"**{pick.numbers()}** ({names}) a **{format_odds(odds)}**: con "
                f"{format_amount(self.stake)} cobras {format_amount(payout(self.stake, odds))}."
            )
        lines.append(f"-# Sale <t:{math.ceil(race.lobby_ends)}:R> · un boleto por persona.")
        return "\n".join(lines)

    def refresh(self) -> BetPanel:
        """Monta los menús y botones según el tipo de boleto elegido."""
        self.clear_items()
        race = self.race
        kind_select: discord.ui.Select = discord.ui.Select(
            placeholder="Tipo de boleto",
            options=[
                discord.SelectOption(
                    label=kind.label,
                    value=kind.key,
                    emoji=kind.emoji,
                    description=kind.explain(race.card.size)[:100],
                    default=kind is self.kind,
                )
                for kind in BetKind
            ],
            row=0,
        )
        kind_select.callback = self._on_kind  # type: ignore[method-assign]
        self.add_item(kind_select)
        for slot in range(self.kind.picks):
            select: discord.ui.Select = discord.ui.Select(
                placeholder=f"{slot + 1}º" if self.kind.picks > 1 else "Caballo",
                options=[self._horse_option(i, slot) for i in range(race.card.size)],
                row=slot + 1,
            )
            select.callback = self._slot_callback(slot)  # type: ignore[method-assign]
            self.add_item(select)
        confirm: discord.ui.Button = discord.ui.Button(
            label=f"Apostar {format_amount(self.stake)}",
            emoji="✅",
            style=discord.ButtonStyle.success,
            disabled=self.pick() is None,
            row=4,
        )
        confirm.callback = self._on_confirm  # type: ignore[method-assign]
        self.add_item(confirm)
        amount: discord.ui.Button = discord.ui.Button(
            label="Cantidad", emoji="🪙", style=discord.ButtonStyle.secondary, row=4
        )
        amount.callback = self._on_amount  # type: ignore[method-assign]
        self.add_item(amount)
        return self

    def _horse_option(self, index: int, slot: int) -> discord.SelectOption:
        race = self.race
        description = None
        if self.kind in (BetKind.WIN, BetKind.PLACE):
            odds = race.odds.odds(Pick(self.kind, (index,)))
            description = f"Paga {format_odds(odds)}"
        elif index == race.tip.horse:
            description = "El de Perro Sanxe"
        return discord.SelectOption(
            label=horse_label(race.card, index),
            value=str(index),
            description=description,
            default=self.horses[slot] == index,
        )

    def _slot_callback(self, slot: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            values = interaction.data.get("values", []) if interaction.data else []
            self.horses[slot] = int(values[0]) if values else None
            await interaction.response.edit_message(content=self.text(), view=self.refresh())

        return callback

    async def _on_kind(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values", []) if interaction.data else []
        if values:
            self.kind = BET_KINDS[values[0]]
        await interaction.response.edit_message(content=self.text(), view=self.refresh())

    async def _on_amount(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(AmountModal(self.race, self))

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        pick = self.pick()
        if pick is None:
            await ack(interaction)
            return

        await self.race.bet_from_button(interaction, pick, via="panel", stake=self.stake)
        self.stop()


class LobbyView(discord.ui.View):
    """Botones de la parrilla. Los pulsa cualquiera: la carrera es de todos."""

    def __init__(self, race: Race) -> None:
        super().__init__(timeout=None)
        self.race = race
        self._add("Apostar", "🎟️", discord.ButtonStyle.success, race.open_panel)
        self._add("Lo de Sanxe", "🐶", discord.ButtonStyle.primary, race.follow_sanxe)
        self._add("Con el pueblo", "🐑", discord.ButtonStyle.primary, race.follow_crowd)
        self._add("Al azar", "🎲", discord.ButtonStyle.secondary, race.random_bet)
        self._add("Ficha", "🪙", discord.ButtonStyle.secondary, race.set_ficha)
        self._add("Listo", "✅", discord.ButtonStyle.success, race.mark_ready)

    def _add(
        self,
        label: str,
        emoji: str,
        style: discord.ButtonStyle,
        callback: Callable[[discord.Interaction], Awaitable[None]],
    ) -> None:
        button: discord.ui.Button = discord.ui.Button(label=label, emoji=emoji, style=style)
        button.callback = callback  # type: ignore[method-assign]
        self.add_item(button)


class RematchView(discord.ui.View):
    """Botón de 🏇 Otra carrera bajo la llegada."""

    def __init__(self, cog: Horses) -> None:
        super().__init__(timeout=REMATCH_SECONDS)
        self.cog = cog
        button: discord.ui.Button = discord.ui.Button(
            label="Otra carrera", emoji="🏇", style=discord.ButtonStyle.success
        )
        button.callback = self._on_click  # type: ignore[method-assign]
        self.add_item(button)

    async def _on_click(self, interaction: discord.Interaction) -> None:
        await self.cog.rematch(interaction)


# -- Carrera ---------------------------------------------------------------------------------------


class Race:
    """La carrera de un canal: su parrilla, sus boletos, su mensaje y su bucle.

    El dinero nunca vive aquí: se cobra y se paga siempre por la economía.
    """

    def __init__(
        self,
        cog: Horses,
        *,
        guild_id: int,
        channel: discord.abc.Messageable,
        card: RaceCard,
        odds: Odds,
        tip: Tip,
        records: dict[str, HorseRecord],
        meta: Meta,
        card_png: bytes,
    ) -> None:
        self.cog = cog
        self.guild_id = guild_id
        self.channel = channel
        self.card = card
        self.odds = odds
        self.tip = tip
        self.records = records
        self.meta = meta
        self.card_png = card_png
        self.message: discord.Message | None = None
        self.phase = Phase.LOBBY
        seconds = GRAND_PRIX_LOBBY_SECONDS if card.grand_prix else LOBBY_SECONDS
        self.lobby_ends = cog.wall_clock() + seconds
        self.tickets: dict[int, Ticket] = {}
        self.result: RaceResult | None = None
        #: La carrera ya corrida y dibujada mientras la parrilla está abierta (`prepare`).
        self.prepared: asyncio.Task[tuple[RaceResult, Media]] | None = None
        self.task: asyncio.Task[None] | None = None
        self._edit_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        #: Se activa cuando todos los que han apostado han pulsado ✅ Listo.
        self.go = asyncio.Event()
        #: Quién dio el último ✅ Listo y cuándo salieron por ello (para los logros).
        self.starter: int | None = None
        self.opened_at = cog.wall_clock()
        self.early_start: float | None = None
        self.view = LobbyView(self)
        self.stopping = False
        self.sleeping = False

    # -- Vista -----------------------------------------------------------------------------

    def crowd_horse(self) -> tuple[int, int] | None:
        """Caballo con más boletos (por su primer caballo) y su porcentaje, si hay boletos."""
        if not self.tickets:
            return None
        counts = Counter(t.pick.horses[0] for t in self.tickets.values())
        horse, votes = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))
        return horse, round(100 * votes / len(self.tickets))

    def ticket_line(self, ticket: Ticket) -> str:
        """`🎟️ Diego · 🥇 Ganador 3 · 500 Y$ · 2,85x` (✅ si ya está listo)."""
        mark = "✅" if ticket.ready else "🎟️"
        return (
            f"{mark} {short_name(ticket.user.display_name)} · {ticket.pick.label()} · "
            f"{format_amount(ticket.stake)} · {format_odds(ticket.odds)}"
        )

    def tickets_block(self) -> str:
        """Lista de boletos con tope de líneas."""
        tickets = list(self.tickets.values())
        lines = [self.ticket_line(t) for t in tickets[:LIST_LIMIT]]
        if len(tickets) > LIST_LIMIT:
            lines.append(f"… y {len(tickets) - LIST_LIMIT} más")
        return "\n".join(lines) or "Nadie todavía. ¿Quién abre la ventanilla?"

    def lobby_embed(self) -> discord.Embed:
        """La parrilla: cuándo salen, el parte, Perro Sanxe, el pueblo y los boletos."""
        card = self.card
        going = card.going
        lines = [f"### 🏁 Salen <t:{math.ceil(self.lobby_ends)}:R>"]
        weather = f"{going.emoji} Pista {going.label.lower()} · {card.distance:,} m".replace(
            ",", "."
        )
        if card.rain_chance:
            weather += f" · ☔ {round(card.rain_chance * 100)} % de lluvia"
        lines.append(weather)
        tip = self.tip
        lines.append(
            f"🐶 **{TAX_COLLECTOR}** lo tiene claro: el **{horse_label(card, tip.horse)}** "
            f"(seguridad {tip.confidence} %)."
        )
        if (crowd := self.crowd_horse()) is not None and len(self.tickets) >= 2:
            horse, share = crowd
            lines.append(f"🐑 El pueblo va con el **{horse_label(card, horse)}** ({share} %).")
        lines.append("Pulsa 🎟️ **Apostar** o escribe `caballo 500 3`.")
        if self.tickets:
            ready = sum(t.ready for t in self.tickets.values())
            lines.append(f"✅ Listos {ready}/{len(self.tickets)}: si estáis todos, salen ya.")
        title = f"🏆 {card.name}" if card.grand_prix else f"🏇 {card.name}"
        embed = discord.Embed(
            title=title,
            description="\n".join(lines),
            color=COLOR_GRAND_PRIX if card.grand_prix else COLOR_LOBBY,
        )
        total = sum(t.stake for t in self.tickets.values())
        embed.add_field(
            name=f"Boletos ({len(self.tickets)}) · {format_amount(total)}",
            value=self.tickets_block(),
            inline=False,
        )
        embed.add_field(name="💰 Gran Premio", value=self.grand_prix_text(), inline=False)
        embed.set_image(url=f"attachment://{CARD_PNG}")
        embed.set_footer(
            text=(
                f"Un boleto por persona · ganador y colocado devuelven el "
                f"{round(RTP['ganador'] * 100)} %, gemela el {round(RTP['gemela'] * 100)} % "
                f"y trío el {round(RTP['trio'] * 100)} % · colocado: "
                f"{place_slots(card.size)} primeros"
            )
        )
        return embed

    def grand_prix_text(self) -> str:
        """El bote: el de esta carrera o lo que falta para el siguiente."""
        pot = format_amount(self.meta.pot)
        if self.card.grand_prix:
            return (
                f"**{pot}** para quien acierte el ganador con un boleto de "
                f"{format_amount(GRAND_PRIX_MIN_STAKE)} o más (ganador, gemela o trío). "
                "Si nadie acierta, el bote crece."
            )
        left = max(0, GRAND_PRIX_EVERY - self.meta.since_grand_prix)
        if left:
            races = "carrera" if left == 1 else "carreras"
            return f"Bote de **{pot}** · faltan {left} {races} para el próximo."
        ready = self.meta.last_grand_prix + self.cog.grand_prix_cooldown
        return f"Bote de **{pot}** · el próximo sale a partir de <t:{math.ceil(ready)}:t>."

    def closing_embed(self) -> discord.Embed:
        """Apuestas cerradas mientras se acaba de dibujar la carrera: la parrilla, sin botones."""
        embed = discord.Embed(
            title=f"🔔 Cajones cerrados · {self.card.name}",
            description="Los caballos entran en los cajones… Ya no se admiten boletos.",
            color=COLOR_RUNNING,
        )
        embed.add_field(
            name=f"Boletos ({len(self.tickets)})", value=self.tickets_block(), inline=False
        )
        embed.set_image(url=f"attachment://{CARD_PNG}")
        return embed

    def running_embed(self) -> discord.Embed:
        """La carrera en marcha: el GIF y los boletos, sin destripar nada."""
        embed = discord.Embed(
            title=f"🏇 ¡Y salen! {self.card.name}",
            description="📻 Retransmite Radio Moncloa.",
            color=COLOR_RUNNING,
        )
        embed.add_field(
            name=f"Boletos ({len(self.tickets)})", value=self.tickets_block(), inline=False
        )
        embed.set_image(url=f"attachment://{RACE_GIF}")
        return embed

    def finish_embed(self, pot_winners: list[Ticket], pot: int) -> discord.Embed:
        """La llegada: podio, narración, quién cobra y la mordida de Hacienda."""
        card = self.card
        result = self.result
        assert result is not None
        medals = ("🥇", "🥈", "🥉")
        podium = []
        for place, index in enumerate(result.order[:3]):
            line = f"{medals[place]} **{horse_label(card, index)}**"
            if place == 0:
                line += f" · pagaba {format_odds(self.odds.odds(Pick(BetKind.WIN, (index,))))}"
            else:
                line += f" · a {margin_text(result.lengths_behind(index, card.distance))}"
            podium.append(line)
        lines = ["\n".join(podium), "", *(f"> {line}" for line in commentary(card, result))]
        embed = discord.Embed(
            title=f"🏁 {card.name}",
            description="\n".join(lines),
            color=COLOR_GRAND_PRIX if card.grand_prix else COLOR_FINISH,
        )
        results = []
        for ticket in sorted(self.tickets.values(), key=lambda t: -t.net):
            name = short_name(ticket.user.display_name)
            if ticket.prize:
                extra = f" · 💰 bote +{format_amount(ticket.pot_share)}" if ticket.pot_share else ""
                results.append(
                    f"✅ {name} · {ticket.pick.label()} · +{format_amount(ticket.net)}{extra}"
                )
            else:
                results.append(
                    f"❌ {name} · {ticket.pick.label()} · -{format_amount(ticket.stake)}"
                )
        if len(results) > LIST_LIMIT:
            results = [*results[:LIST_LIMIT], f"… y {len(results) - LIST_LIMIT} más"]
        value = "\n".join(results)
        if note := tax_summary(list(self.tickets.values())):
            value += f"\n{note}"
        # Un campo de embed admite 1.024 caracteres; con mucha gente, se corta.
        if len(value) > 1_024:
            value = value[:1_023] + "…"
        embed.add_field(name="Boletos", value=value or "—", inline=False)
        if card.grand_prix:
            if pot_winners:
                names = ", ".join(short_name(t.user.display_name) for t in pot_winners)
                verb = "se reparten" if len(pot_winners) > 1 else "se lleva"
                text = f"🏆 {names} {verb} **{format_amount(pot)}**."
            else:
                text = f"Desierto. El bote crece: **{format_amount(next_pot(pot, won=False))}**."
            embed.add_field(name="💰 Bote del Gran Premio", value=text, inline=False)
        embed.set_image(url=f"attachment://{FINISH_PNG}")
        embed.set_footer(text="🏇 Otra carrera abre la siguiente en este canal.")
        return embed

    async def edit(self, **kwargs: Any) -> None:
        """Edita el mensaje de la carrera; un fallo de Discord no rompe nada."""
        if self.message is None:
            return
        try:
            await self.message.edit(**kwargs)
        except discord.HTTPException:
            logger.debug("No se pudo editar la carrera", exc_info=True)

    def edit_soon(self) -> None:
        """Redibuja la parrilla sin esperar; si la edición anterior no ha terminado, se salta."""
        if self._edit_task is not None and not self._edit_task.done():
            return
        self._edit_task = asyncio.create_task(self.edit(embed=self.lobby_embed(), view=self.view))

    # -- Boletos ---------------------------------------------------------------------------

    async def place(
        self,
        user: discord.abc.User,
        pick: Pick,
        stake: int,
        *,
        via: str,
        interaction: discord.Interaction | None = None,
    ) -> Ticket:
        """Cobra la apuesta y apunta el boleto.

        Raises:
            InsufficientFundsError: Si no le llega.
            HorseError: Si ya tiene boleto o la carrera ya ha salido.
        """
        async with self._lock:
            if self.phase is not Phase.LOBBY:
                raise HorseError("Los caballos ya han salido. Espera a la siguiente.")
            if user.id in self.tickets:
                raise HorseError("Ya tienes boleto en esta carrera: uno por persona.")
            await self.cog.economy.place_bet(self.guild_id, user.id, game=GAME, stake=stake)
            ticket = Ticket(
                user=user,
                pick=pick,
                stake=stake,
                odds=self.odds.odds(pick),
                via=via,
                interaction=interaction,
                placed_at=self.cog.wall_clock(),
            )
            self.tickets[user.id] = ticket
            return ticket

    def confirm_text(self, ticket: Ticket) -> str:
        """Confirmación privada de un boleto."""
        names = " → ".join(pick_names(self.card, ticket.pick))
        return (
            f"🎟️ **{ticket.pick.label()}** ({names}) · {format_amount(ticket.stake)} a "
            f"**{format_odds(ticket.odds)}**. Si acierta cobras "
            f"**{format_amount(payout(ticket.stake, ticket.odds))}**."
        )

    async def ticket_file(self, ticket: Ticket) -> discord.File:
        """El boleto dibujado, antes de la carrera."""
        png = await self.cog.renderer.ticket(
            race=self.card.name,
            player=ticket.user.display_name,
            pick=ticket.pick,
            names=pick_names(self.card, ticket.pick),
            stake=ticket.stake,
            odds=ticket.odds,
            horses=[self.card.horses[i] for i in ticket.pick.horses],
        )
        return discord.File(io.BytesIO(png), filename="boleto.png")

    async def _stake_or_error(self, interaction: discord.Interaction, stake: int) -> int | None:
        balance = await self.cog.economy.balance(self.guild_id, interaction.user.id)
        if balance <= 0 or stake > balance:
            await notify(interaction, insufficient_text(balance, stake))
            return None
        return stake

    async def bet_from_button(
        self,
        interaction: discord.Interaction,
        pick: Pick,
        *,
        via: str,
        stake: int | None = None,
    ) -> None:
        """Apuesta desde un botón (de la parrilla o del panel privado) y lo confirma."""
        if stake is None:
            stake = self.cog.ficha(self.guild_id, interaction.user.id)
        # Saldo, cobro y boleto dibujado tardan: se acepta el clic antes.
        await ack(interaction)
        if await self._stake_or_error(interaction, stake) is None:
            return
        try:
            ticket = await self.place(
                interaction.user, pick, stake, via=via, interaction=interaction
            )
        except InsufficientFundsError as error:
            await notify(interaction, insufficient_text(error.balance, stake))
            return
        except HorseError as error:
            await notify(interaction, str(error))
            return
        from_lobby = (
            self.message is not None
            and interaction.message is not None
            and interaction.message.id == self.message.id
        )
        if from_lobby:
            await edit(interaction, embed=self.lobby_embed(), view=self.view)
            await interaction.followup.send(
                self.confirm_text(ticket), file=await self.ticket_file(ticket), ephemeral=True
            )
        else:
            # Desde el panel privado: el panel se convierte en el boleto. Primero
            # el texto y luego, cuando esté dibujada, la imagen.
            await edit(interaction, content=self.confirm_text(ticket), view=None)
            try:
                await interaction.edit_original_response(
                    attachments=[await self.ticket_file(ticket)]
                )
            except discord.HTTPException:
                logger.debug("No se pudo pegar el boleto al panel", exc_info=True)
            self.edit_soon()
        await renta.remind(self.cog.bot, interaction)

    async def open_panel(self, interaction: discord.Interaction) -> None:
        """🎟️ Apostar: panel privado para montar el boleto."""
        if not await self._can_bet(interaction):
            return
        panel = BetPanel(
            self, interaction.user.id, self.cog.ficha(self.guild_id, interaction.user.id)
        )
        await interaction.response.send_message(panel.text(), view=panel, ephemeral=True)

    async def _can_bet(self, interaction: discord.Interaction) -> bool:
        if self.phase is not Phase.LOBBY:
            await interaction.response.send_message(
                "Los caballos ya han salido. Espera a la siguiente.", ephemeral=True
            )
            return False
        if interaction.user.id in self.tickets:
            ticket = self.tickets[interaction.user.id]
            await interaction.response.send_message(
                f"Ya llevas boleto: {ticket.pick.label()} · {format_amount(ticket.stake)}.",
                ephemeral=True,
            )
            return False
        return True

    async def follow_sanxe(self, interaction: discord.Interaction) -> None:
        """🐶 Lo de Sanxe: tu ficha a ganador al caballo del pronóstico."""
        if await self._can_bet(interaction):
            await self.bet_from_button(
                interaction, Pick(BetKind.WIN, (self.tip.horse,)), via="sanxe"
            )

    async def follow_crowd(self, interaction: discord.Interaction) -> None:
        """🐑 Con el pueblo: tu ficha a ganador al caballo con más boletos (o al favorito)."""
        if not await self._can_bet(interaction):
            return
        crowd = self.crowd_horse()
        horse = crowd[0] if crowd is not None else self.odds.favourite()
        await self.bet_from_button(interaction, Pick(BetKind.WIN, (horse,)), via="pueblo")

    async def random_bet(self, interaction: discord.Interaction) -> None:
        """🎲 Al azar: tu ficha a ganador a un caballo cualquiera."""
        if not await self._can_bet(interaction):
            return
        horse = self.cog.rng.randrange(self.card.size)
        await self.bet_from_button(interaction, Pick(BetKind.WIN, (horse,)), via="azar")

    async def set_ficha(self, interaction: discord.Interaction) -> None:
        """🪙 Ficha: cambia la cantidad de los botones rápidos."""
        await interaction.response.send_modal(AmountModal(self))

    async def mark_ready(self, interaction: discord.Interaction) -> None:
        """✅ Listo: si todos los que han apostado lo pulsan, los caballos salen ya.

        Solo mira y cambia memoria, así que contesta directamente.
        """
        ticket = self.tickets.get(interaction.user.id)
        if self.phase is not Phase.LOBBY or self.go.is_set():
            text = "Los caballos ya han salido."
        elif ticket is None:
            text = "Primero haz tu boleto: ✅ Listo es para quien ya ha apostado."
        elif ticket.ready:
            text = "Ya estabas listo. Falta que se decidan los demás."
        else:
            ticket.ready = True
            waiting = [t for t in self.tickets.values() if not t.ready]
            if waiting:
                names = ", ".join(short_name(t.user.display_name) for t in waiting[:3])
                more = f" y {len(waiting) - 3} más" if len(waiting) > 3 else ""
                text = f"✅ Listo. Falta{'n' if len(waiting) > 1 else ''}: {names}{more}."
            else:
                self.starter = interaction.user.id
                self.early_start = self.cog.wall_clock()
                self.go.set()
                text = "✅ Todos listos. ¡Salen!"
        await interaction.response.send_message(text, ephemeral=True)
        if not self.go.is_set():
            self.edit_soon()

    # -- Ciclo -----------------------------------------------------------------------------

    async def nap(self, seconds: float, *, wake: asyncio.Event | None = None) -> None:
        """Espera del bucle; el único sitio donde el apagado lo puede cortar.

        Con `wake`, la espera acaba antes si ese evento se activa (✅ Listo).
        """
        if self.stopping:
            raise asyncio.CancelledError
        self.sleeping = True
        try:
            if wake is None:
                await self.cog.sleep(seconds)
                return
            waiters = {
                asyncio.ensure_future(self.cog.sleep(seconds)),
                asyncio.ensure_future(wake.wait()),
            }
            try:
                await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for waiter in waiters:
                    waiter.cancel()
                await asyncio.gather(*waiters, return_exceptions=True)
        finally:
            self.sleeping = False

    async def run(self) -> None:
        """Parrilla, carrera y llegada; si nadie apuesta, los caballos vuelven a la cuadra."""
        try:
            await self.nap(max(0.0, self.lobby_ends - self.cog.wall_clock()), wake=self.go)
            if not self.tickets:
                await self.close()
                return
            await self.race()
        except asyncio.CancelledError:
            if not self.stopping:
                raise
        except Exception:
            logger.exception("La carrera de caballos ha fallado; se devuelve lo apostado")
            await self.shutdown()
        finally:
            self.cog.release(self)

    def prepare(self) -> None:
        """Corre y dibuja la carrera en segundo plano mientras se apuesta.

        Dibujarla lleva ~5-8 s y así no se espera al cerrar la parrilla. Es
        justo: el resultado no depende de los boletos (sale de su propio
        azar, distinto del de las cuotas) y no se enseña ni se guarda en
        ningún sitio hasta que salen los caballos. Decidirlo al abrir o al
        cerrar da exactamente las mismas probabilidades.
        """
        self.prepared = asyncio.create_task(self._prepare(), name="caballos-dibujo")

    async def _prepare(self) -> tuple[RaceResult, Media]:
        result = run_race(self.card, self.cog.np_rng())
        return result, await self.cog.renderer.race(self.card, result, self.odds)

    async def race(self) -> None:
        """Corre, paga, enseña el GIF y luego la llegada."""
        async with self._lock:
            self.phase = Phase.RUNNING
        media: Media | None = None
        result: RaceResult | None = None
        if self.prepared is not None:
            if not self.prepared.done():
                # Si todos están listos antes de que acabe el dibujo, que se note ya:
                # se cierran las apuestas a la vez que se termina de pintar.
                await self._close_bets()
            try:
                result, media = await self.prepared
            except Exception:
                logger.exception("No se pudo preparar la carrera; se corre ahora")
        if result is None:
            result = run_race(self.card, self.cog.np_rng())
        self.result = result
        pot_winners, pot = await self.pay(result)
        if self._edit_task is not None:
            await asyncio.gather(self._edit_task, return_exceptions=True)
        if media is None:
            media = await self.cog.renderer.race(self.card, result, self.odds)
        await self.edit(
            embed=self.running_embed(),
            view=None,
            attachments=[discord.File(io.BytesIO(media.gif), filename=RACE_GIF)],
        )
        await self.nap(media.seconds + REVEAL_MARGIN_SECONDS)
        self.phase = Phase.DONE
        await self.edit(
            embed=self.finish_embed(pot_winners, pot),
            view=RematchView(self.cog),
            attachments=[discord.File(io.BytesIO(media.png), filename=FINISH_PNG)],
        )
        self.view.stop()
        await self.announce(pot_winners, pot)
        await self.personal_results()
        await self.track(pot_winners)

    async def _close_bets(self) -> None:
        """Quita los botones de la parrilla y dice que salen, sin esperar al dibujo."""
        if self._edit_task is not None and not self._edit_task.done():
            await asyncio.gather(self._edit_task, return_exceptions=True)
        self._edit_task = asyncio.create_task(self.edit(embed=self.closing_embed(), view=None))

    async def pay(self, result: RaceResult) -> tuple[list[Ticket], int]:
        """Paga cada boleto (0 si falla) y el bote, y apunta la carrera en el establo.

        Returns:
            Quién se lleva el bote del Gran Premio y de cuánto era.
        """
        order = result.order
        pot = 0
        winners: list[Ticket] = []
        if self.card.grand_prix:
            pot = (await self.cog.repository.meta(self.guild_id)).pot
            winners = [t for t in self.tickets.values() if pot_eligible(t.pick, t.stake, order)]
        share = pot // len(winners) if winners else 0
        for ticket in self.tickets.values():
            if ticket.pick.wins(order):
                ticket.prize = payout(ticket.stake, ticket.odds)
            if any(t is ticket for t in winners):
                ticket.pot_share = share
                ticket.prize += share
            try:
                ticket.settlement = await self.cog.economy.pay_winnings(
                    self.guild_id, ticket.user.id, game=GAME, amount=ticket.prize
                )
            except BalanceLimitError:
                logger.warning("Premio de caballos por encima del saldo máximo; no se paga.")
                ticket.prize = 0
                ticket.pot_share = 0
        finish = [(self.card.horses[i].key, place + 1) for place, i in enumerate(order)]
        new_pot = next_pot(pot, won=bool(winners)) if self.card.grand_prix else None
        try:
            self.meta = await self.cog.repository.record_race(
                self.guild_id,
                finish,
                now=self.cog.wall_clock(),
                grand_prix=self.card.grand_prix,
                pot=new_pot,
            )
        except Exception:
            logger.exception("No se pudo apuntar la carrera en el establo")
        return winners, pot

    async def announce(self, pot_winners: list[Ticket], pot: int) -> None:
        """Anuncios aparte: el bote del Gran Premio (con mención) y los boletos gordos."""
        if pot_winners:
            mentions = " ".join(t.user.mention for t in pot_winners)
            files = [await self._premiado(t) for t in pot_winners[:MAX_BIG_TICKETS]]
            try:
                await self.channel.send(
                    f"## 🏆 ¡Bote del {self.card.name}!\n{mentions} "
                    f"{'se reparten' if len(pot_winners) > 1 else 'se lleva'} "
                    f"**{format_amount(pot)}** por acertar a "
                    f"**{self.card.horses[self.result.winner].name}**.",  # type: ignore[union-attr]
                    files=files,
                    allowed_mentions=discord.AllowedMentions(
                        users=[t.user for t in pot_winners], everyone=False, roles=False
                    ),
                )
            except discord.HTTPException:
                logger.debug("No se pudo anunciar el bote del Gran Premio", exc_info=True)
        big = [
            t
            for t in self.tickets.values()
            if t.prize and t.odds >= BIG_TICKET_ODDS and all(t is not w for w in pot_winners)
        ][:MAX_BIG_TICKETS]
        if big:
            names = ", ".join(short_name(t.user.display_name) for t in big)
            try:
                await self.channel.send(
                    ("🎟️ ¡Boletos premiados! " if len(big) > 1 else "🎟️ ¡Boleto premiado! ") + names,
                    files=[await self._premiado(t) for t in big],
                    allowed_mentions=NO_MENTIONS,
                )
            except discord.HTTPException:
                logger.debug("No se pudo publicar un boleto premiado", exc_info=True)

    async def _premiado(self, ticket: Ticket) -> discord.File:
        png = await self.cog.renderer.ticket(
            race=self.card.name,
            player=ticket.user.display_name,
            pick=ticket.pick,
            names=pick_names(self.card, ticket.pick),
            stake=ticket.stake,
            odds=ticket.odds,
            won=True,
            prize=ticket.prize,
            pot_share=ticket.pot_share,
            horses=[self.card.horses[i] for i in ticket.pick.horses],
        )
        return discord.File(io.BytesIO(png), filename=f"premiado-{ticket.user.id}.png")

    async def personal_results(self) -> None:
        """A quien apostó con botón o slash: su resultado en privado, con Hacienda y su mascota."""
        result = self.result
        assert result is not None
        for ticket in self.tickets.values():
            interaction = ticket.interaction
            if interaction is None:
                continue
            settlement = ticket.settlement
            if ticket.prize:
                text = (
                    f"## ✅ ¡Boleto premiado! {ticket.pick.label()}\n"
                    f"Cobras **{format_amount(ticket.prize)}** (+{format_amount(ticket.net)})."
                )
            else:
                position = result.position(ticket.pick.horses[0])
                text = (
                    f"## ❌ {ticket.pick.label()}\n"
                    f"Tu {self.card.horses[ticket.pick.horses[0]].name} entró {position}º. "
                    f"Pierdes {format_amount(ticket.stake)}."
                )
                if miss := near_miss(self.card, result, self.odds, ticket.pick, ticket.stake):
                    text += f"\n😫 {miss}"
            if settlement is not None and (note := gambling_tax_line(settlement)):
                text += f"\n{note}"
            if hint := await renta.hint(
                self.cog.bot,
                self.guild_id,
                ticket.user.id,
                bet_moment(
                    stake=ticket.stake,
                    net=ticket.net,
                    balance_after=settlement.balance if settlement is not None else 0,
                ),
            ):
                text += f"\n{hint}"
            try:
                await interaction.followup.send(text, ephemeral=True)
            except discord.HTTPException:
                logger.debug("No se pudo mandar el resultado privado de caballos", exc_info=True)

    async def track(self, pot_winners: list[Ticket]) -> None:
        """Logros y estadísticas del casino de cada boleto, después de enseñar la llegada."""
        result = self.result
        assert result is not None
        crowd = Counter(t.pick.horses[0] for t in self.tickets.values())
        when = datetime.fromtimestamp(self.cog.wall_clock(), TIMEZONE)
        favourite = self.odds.favourite()
        photo = photo_finish(result, self.card.distance)
        flash = (
            self.early_start is not None and self.early_start - self.opened_at < FLASH_START_SECONDS
        )
        for ticket in self.tickets.values():
            settlement = ticket.settlement
            first = ticket.pick.horses[0]
            delta = horses_stats(
                card=self.card,
                result=result,
                pick=ticket.pick,
                stake=ticket.stake,
                odds_cents=ticket.odds,
                net=ticket.net,
                pot_share=ticket.pot_share,
                tip_horse=self.tip.horse,
                alone=crowd[first] == 1 and len(self.tickets) >= 3,
                via=ticket.via,
                players=len(self.tickets),
                favourite=favourite,
                favourite_odds=self.odds.odds(Pick(BetKind.WIN, (favourite,))),
                photo=photo,
                comeback=comeback(result, self.card.distance, first),
                tax_delta=settlement.tax_delta if settlement else 0,
                when=when,
                ready=ticket.ready,
                starter=ticket.user.id == self.starter,
                flash=flash,
            )
            delta.merge(
                casino_stats(
                    stake=ticket.stake,
                    net=ticket.net,
                    balance_after=settlement.balance if settlement else 0,
                    tax_delta=settlement.tax_delta if settlement else 0,
                )
            )
            await logros.casino_play(
                self.cog.bot, self.guild_id, ticket.user, self.channel, delta, net=ticket.net
            )
            await apuestas.record(
                self.cog.bot,
                self.guild_id,
                ticket.user,
                game=GAME,
                stake=ticket.stake,
                net=ticket.net,
                balance_after=settlement.balance if settlement else 0,
                tax=settlement.tax_delta if settlement else 0,
                details=(("started", int(ticket.placed_at)),),
            )

    async def close(self) -> None:
        """Parrilla vacía: los caballos vuelven a la cuadra y el mensaje pierde los botones."""
        self.phase = Phase.DONE
        if self.prepared is not None:
            self.prepared.cancel()
        embed = discord.Embed(
            title=f"🏇 {self.card.name}",
            description="Nadie ha apostado. Los caballos vuelven a la cuadra.\n"
            "`caballo` para abrir otra carrera.",
            color=COLOR_FINISH,
        )
        await self.edit(embed=embed, view=None, attachments=[])
        self.view.stop()

    async def shutdown(self) -> None:
        """Cierre ordenado: en la parrilla se devuelve lo apostado.

        Si los caballos ya salieron, los premios ya están pagados (se pagan
        antes de enseñar el GIF) y no hay nada que devolver.
        """
        async with self._lock:
            if self.phase is Phase.LOBBY:
                for ticket in self.tickets.values():
                    try:
                        await self.cog.economy.pay_winnings(
                            self.guild_id, ticket.user.id, game=GAME, amount=ticket.stake
                        )
                    except BalanceLimitError:
                        logger.warning("No se pudo devolver una apuesta de caballos.")
                self.tickets.clear()
            self.phase = Phase.DONE
        embed = discord.Embed(
            title=f"🏇 {self.card.name}",
            description="Carrera suspendida. Se devuelve lo apostado.",
            color=COLOR_FINISH,
        )
        if self.result is None:
            await self.edit(embed=embed, view=None, attachments=[])
        self.view.stop()


# -- Cog -------------------------------------------------------------------------------------------


class Horses(commands.Cog, name="Caballos"):
    """Las carreras de caballos del casino: una carrera por canal, cuando alguien la pide."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        economy: EconomyService,
        repository: HorseRepository,
        renderer: SceneRenderer | None = None,
        casino_channel_ids: frozenset[int] = frozenset(),
        rng: Any = None,
        wall_clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        grand_prix_cooldown: float | None = None,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.repository = repository
        # Dibuja con Chromium y, si no hay, con Pillow (ver `bot.services.horses_scene`).
        self.renderer = renderer or SceneRenderer()
        self.casino_channel_ids = casino_channel_ids
        # `secrets` usa el azar del sistema operativo: no se puede predecir.
        self.rng = rng or secrets.SystemRandom()
        self.wall_clock = wall_clock
        self.sleep = sleep
        self.grand_prix_cooldown = (
            GRAND_PRIX_COOLDOWN if grand_prix_cooldown is None else grand_prix_cooldown
        )
        #: Carrera abierta de cada canal.
        self.races: dict[int, Race] = {}
        #: Canales en los que se está preparando una carrera (para no abrir dos).
        self._opening: set[int] = set()
        #: Servidores con un Gran Premio en marcha (como mucho uno a la vez).
        self._grand_prix: set[int] = set()
        # Ficha por (servidor, miembro). Crece como mucho hasta el número de
        # miembros que han jugado; se pierde al reiniciar.
        self._fichas: dict[tuple[int, int], int] = {}

    def np_rng(self) -> np.random.Generator:
        """Generador de numpy sembrado con el azar del cog (del sistema, salvo en pruebas)."""
        return np.random.default_rng(self.rng.getrandbits(64))

    def ficha(self, guild_id: int, user_id: int) -> int:
        """Ficha recordada de un miembro."""
        return self._fichas.get((guild_id, user_id), DEFAULT_STAKE)

    def set_ficha(self, guild_id: int, user_id: int, stake: int) -> None:
        """Recuerda la ficha de un miembro."""
        self._fichas[(guild_id, user_id)] = max(1, stake)

    def release(self, race: Race) -> None:
        """Libera el canal (y el Gran Premio del servidor) cuando una carrera acaba."""
        channel_id = getattr(race.channel, "id", 0)
        if self.races.get(channel_id) is race:
            del self.races[channel_id]
        if race.card.grand_prix:
            self._grand_prix.discard(race.guild_id)

    async def cog_unload(self) -> None:
        """Al apagar, devuelve lo apostado en las parrillas abiertas."""
        for race in list(self.races.values()):
            race.stopping = True
            if race.prepared is not None and race.phase is Phase.LOBBY:
                race.prepared.cancel()
            if race.task is not None:
                if race.sleeping:
                    race.task.cancel()
                await asyncio.gather(race.task, return_exceptions=True)
            try:
                await race.shutdown()
            except Exception:
                logger.exception("No se pudo cerrar una carrera al apagar")
        self.races.clear()
        await self.renderer.close()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Borra el historial del establo de un servidor que ha echado al bot."""
        await self.repository.delete_guild_data(guild.id)

    # -- Abrir y apostar -----------------------------------------------------------------------

    async def open_race(self, guild_id: int, channel: discord.abc.Messageable) -> Race:
        """Prepara una carrera nueva: sortea la parrilla, calcula las cuotas y la dibuja."""
        records = await self.repository.records(guild_id)
        meta = await self.repository.meta(guild_id)
        now = self.wall_clock()
        grand_prix = guild_id not in self._grand_prix and grand_prix_due(
            since=meta.since_grand_prix,
            last=meta.last_grand_prix,
            now=now,
            cooldown=self.grand_prix_cooldown,
        )
        rng = self.np_rng()
        card = new_card(rng, records, now=now, grand_prix=grand_prix)
        odds = await asyncio.to_thread(estimate, card, rng)
        tip = sanxe_tip(odds, rng)
        card_png = await self.renderer.card(
            card,
            odds,
            records,
            tip=tip,
            pot=meta.pot if grand_prix else None,
        )
        if grand_prix:
            self._grand_prix.add(guild_id)
        return Race(
            self,
            guild_id=guild_id,
            channel=channel,
            card=card,
            odds=odds,
            tip=tip,
            records=records,
            meta=meta,
            card_png=card_png,
        )

    def start(self, race: Race) -> None:
        """Arranca el bucle de una carrera recién abierta."""
        channel_id = getattr(race.channel, "id", 0)
        race.task = asyncio.create_task(race.run(), name=f"caballos-{channel_id}")
        race.prepare()

    async def ensure_race(
        self, guild_id: int, channel: discord.abc.Messageable
    ) -> tuple[Race | None, bool]:
        """La carrera del canal: la que hay o una nueva que se publica ya.

        Returns:
            La carrera (`None` si hay otra preparándose) y si es nueva.
        """
        channel_id = getattr(channel, "id", 0)
        race = self.races.get(channel_id)
        if race is not None:
            return race, False
        if channel_id in self._opening:
            return None, False
        self._opening.add(channel_id)
        try:
            race = await self.open_race(guild_id, channel)
            race.message = await channel.send(
                embed=race.lobby_embed(),
                view=race.view,
                file=discord.File(io.BytesIO(race.card_png), filename=CARD_PNG),
                allowed_mentions=NO_MENTIONS,
            )
        except Exception:
            if race is not None and race.card.grand_prix:
                self._grand_prix.discard(guild_id)
            raise
        finally:
            self._opening.discard(channel_id)
        self.races[channel_id] = race
        self.start(race)
        return race, True

    async def _caballo_impl(
        self,
        *,
        guild: discord.Guild | None,
        channel: object,
        user: discord.abc.User,
        amount_text: str | None,
        pick_text: str | None,
        kind_text: str | None,
        confirm: Callable[[str, Race, Ticket | None], Awaitable[None]],
        send_error: Callable[[str], Awaitable[None]],
        interaction: discord.Interaction | None = None,
    ) -> None:
        """Lógica de `/caballo` y `.caballo`: abre la carrera y, si hay boleto, apuesta."""
        if guild is None or not isinstance(channel, discord.abc.Messageable):
            await send_error("Las carreras solo se corren dentro de un servidor.")
            return
        if error := casino_channel_error(self.casino_channel_ids, channel, "El hipódromo"):
            await send_error(error)
            return
        stake: int | None = None
        if amount_text is not None or pick_text is not None:
            balance = await self.economy.balance(guild.id, user.id)
            try:
                stake = (
                    parse_amount(amount_text, balance)
                    if amount_text
                    else max(1, min(self.ficha(guild.id, user.id), balance))
                )
            except ValueError as error:
                await send_error(str(error))
                return
            if balance <= 0 or stake > balance:
                await send_error(insufficient_text(balance, stake))
                return
            self.set_ficha(guild.id, user.id, stake)
        existing = self.races.get(getattr(channel, "id", 0))
        if existing is not None and existing.phase is not Phase.LOBBY:
            await send_error("Hay una carrera en pista. En cuanto acabe, abre la siguiente.")
            return
        race, _new = await self.ensure_race(guild.id, channel)
        if race is None:
            await send_error("Se está preparando una carrera en este canal; un segundo.")
            return
        if pick_text is None:
            await confirm("", race, None)
            return
        try:
            pick = parse_pick(pick_text, race.card.size, kind_text)
        except HorseError as error:
            await send_error(str(error))
            return
        assert stake is not None
        try:
            ticket = await race.place(
                user,
                pick,
                stake,
                via="comando",
                interaction=interaction,
            )
        except InsufficientFundsError as error:
            await send_error(insufficient_text(error.balance, stake))
            return
        except HorseError as error:
            await send_error(str(error))
            return
        race.edit_soon()
        await confirm(race.confirm_text(ticket), race, ticket)

    async def rematch(self, interaction: discord.Interaction) -> None:
        """🏇 Otra carrera: abre la siguiente en el canal (o avisa de la que hay)."""
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.abc.Messageable):
            await interaction.response.defer()
            return
        existing = self.races.get(getattr(channel, "id", 0))
        if existing is not None:
            if existing.phase is Phase.LOBBY:
                await existing.open_panel(interaction)
            else:
                await interaction.response.send_message(
                    "Hay una carrera en pista. En cuanto acabe, abre la siguiente.",
                    ephemeral=True,
                )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            race, _new = await self.ensure_race(guild.id, channel)
        except discord.HTTPException:
            logger.exception("No se pudo abrir otra carrera")
            await interaction.followup.send("No he podido abrir la carrera.", ephemeral=True)
            return
        text = "🏇 Carrera abierta: pulsa 🎟️ **Apostar** en la parrilla."
        if race is None:
            text = "Se está preparando una carrera en este canal; un segundo."
        await interaction.followup.send(text, ephemeral=True)

    @app_commands.command(
        name="caballo",
        description="Carreras de caballos: abre la carrera del canal o apuesta en ella.",
    )
    @app_commands.describe(
        cantidad="Apuesta: 500, 2k, all… (por defecto tu ficha o 100)",
        caballos="Dorsales: 3 (ganador), 3-5 (gemela) o 3-5-1 (trío)",
        tipo="Tipo de boleto (si no, sale de cuántos caballos elijas)",
    )
    @app_commands.choices(tipo=[app_commands.Choice(name=k.label, value=k.key) for k in BetKind])
    @app_commands.guild_only()
    async def caballo(
        self,
        interaction: discord.Interaction,
        cantidad: str | None = None,
        caballos: str | None = None,
        tipo: str | None = None,
    ) -> None:
        """Abre la carrera del canal o apuesta en la que esté en la parrilla.

        Sin caballos, abre el panel privado de apuesta. Solo en los canales de
        `CASINO_CHANNEL_IDS` si está configurado. Cobra la apuesta al hacer el
        boleto y paga al salir los caballos.
        """
        # Preparar la carrera (cuotas e imagen) puede pasar de los 3 s de Discord.
        await interaction.response.defer(ephemeral=True, thinking=True)

        async def confirm(text: str, race: Race, ticket: Ticket | None) -> None:
            if ticket is not None:
                await interaction.followup.send(
                    text, file=await race.ticket_file(ticket), ephemeral=True
                )
                return
            if interaction.user.id in race.tickets:
                await interaction.followup.send(
                    race.confirm_text(race.tickets[interaction.user.id]), ephemeral=True
                )
                return
            panel = BetPanel(
                race, interaction.user.id, self.ficha(race.guild_id, interaction.user.id)
            )
            await interaction.followup.send(panel.text(), view=panel, ephemeral=True)

        async def send_error(text: str) -> None:
            await interaction.followup.send(text, ephemeral=True)

        await self._caballo_impl(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            amount_text=cantidad,
            pick_text=caballos,
            kind_text=tipo,
            confirm=confirm,
            send_error=send_error,
            interaction=interaction,
        )
        await renta.remind(self.bot, interaction)

    @commands.command(name="caballo")
    @commands.guild_only()
    async def caballo_text(
        self,
        ctx: commands.Context,
        cantidad: str | None = None,
        caballos: str | None = None,
        tipo: str | None = None,
    ) -> None:
        """Versión de texto: `.caballo`, `.caballo 500 3`, `.caballo 500 3-5-1`...

        `.caballo 500 3 colocado` cambia el tipo de boleto.
        """

        async def confirm(text: str, race: Race, ticket: Ticket | None) -> None:
            if ticket is None:
                return
            # Sin mensajes privados en comandos de texto: una reacción basta.
            try:
                await ctx.message.add_reaction("🎟️")
            except discord.HTTPException:
                await ctx.send(text, allowed_mentions=NO_MENTIONS)

        await self._caballo_impl(
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            amount_text=cantidad,
            pick_text=caballos,
            kind_text=tipo,
            confirm=confirm,
            send_error=ContextResponder(ctx).send_error,
        )


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía y el establo compartidos del bot."""
    await bot.add_cog(
        Horses(
            bot,
            economy=bot.economy,
            repository=bot.horses,
            casino_channel_ids=bot.casino_channel_ids,
        )
    )
