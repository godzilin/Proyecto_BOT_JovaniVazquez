"""Apuestas deportivas: `deportes`, cuotas fijas sobre partidos de verdad.

`/deportes` (y `.deportes`) abre un panel (componentes de Discord, `SportsView`) con los
próximos partidos de cada competición de `bot.services.sports.SPORTS` (LaLiga y la
Champions):

- Arriba, un botón por competición y 🎟️ **Mis apuestas**. Debajo, los `LISTED` partidos que
  antes empiezan, cada uno con su 1X2 y su botón, y un desplegable con los siguientes.
- La ficha de un partido enseña la probabilidad de cada resultado y los goles esperados, y
  una pestaña por mercado (`bot.services.sports.Market`): Resultado (1X2), Más/menos
  goles, Ambos marcan y Resultado exacto (en un desplegable). Los mercados de goles solo
  salen si el partido tiene más/menos de las casas (`Match.goals`).
- Cada opción abre un formulario con la cantidad (500, 2k, mitad, all…) y apuesta a esa
  cuota, que queda fijada. Si la cuota ha cambiado desde que se pintó el panel, avisa y no
  cobra. Una apuesta por mercado y partido, y hasta `MAX_STAKE` por partido sumando
  mercados.
- 🧾 **Combinada** pone el panel en modo combinada: el botón de cada opción pasa a
  «Añadir» y la opción entra en el boleto, una por partido (otra del mismo partido la
  sustituye) y de `MIN_LEGS` a `MAX_LEGS`, sin el exacto (`COMBO_MARKETS`). Montar el boleto
  solo toca memoria (`SportsView.legs`). Su página enseña las patas, la cuota total
  (`combo_odds`) y lo que se cobraría (con el tope `MAX_COMBO_PAYOUT`), y «Apostar
  combinada» abre el formulario. Lo apostado cuenta entero en el tope de cada partido.
- Al apostar llega, en privado, el boleto dibujado (`bot.services.sports_scene`); al
  acabar el partido, el marcador final dibujado sale en el canal donde se apostó, con
  quién cobra y quién no. Si no se puede dibujar, los dos salen en texto.

Solo lo maneja quien lo abre. Con `/deportes` el panel es efímero; con `.deportes` se ve
en el canal. Abrirlo y moverse por él no toca la base de datos: los partidos viven en
memoria (`matches`) y se rehacen cada vez que llegan cuotas nuevas.

Cuotas y resultados. Cada 5 minutos (`tick`) el cog mira qué toca pedir a The Odds API
(`bot.services.sports.odds_due`, `totals_due` y `scores_due`), lo guarda en
`bot.repositories.sports` y resuelve lo que ya tiene resultado (`settle`): paga y anuncia
el marcador (solo menciona a quien cobra o recupera). Lo que lleva `VOID_AFTER` sin
resultado se anula y se devuelve. Sin `ODDS_API_KEY` no se pide nada y el panel dice que
no está configurado; las apuestas que quedaran se anulan a su hora.

Combinadas resueltas (`settle_combos`). En cada vuelta se apunta cómo queda cada pata ya
jugada (`leg_status`). Con una fallada, la combinada se pierde al momento; con todas
acertadas o anuladas, se paga a la cuota de las acertadas (`combo_outcome`); si se anulan
todas, se devuelve. Las patas resueltas se guardan aunque la combinada siga abierta: así 🎟️
Mis apuestas enseña por dónde va y no hace falta volver a pedir marcadores viejos. El
resultado sale en el canal donde se apostó, una línea por combinada.

Dinero (`EconomyService.sports_bet`, `settle_sports_bet` y `void_sports_bet`; para las
combinadas, `sports_combo_bet`, `settle_sports_combo` y `void_sports_combo`): lo
apostado espera en el depósito de las apuestas deportivas y, al resolverse, apuesta y
premio entran a la vez en el día del casino, con la retención del IRPF del juego.
Tratamiento fiscal en `EconomyService.sports_bet`. Las cuotas llevan el margen de la casa
(`Market.rtp`): de media desaparece más dinero del que se crea. Lo que espera sale en
`saldo` (`pending`). Apostar es gastar: lleva `renta.remind` y la línea de `renta.hint`.

Logros: sección ⚽ Deportes del grupo 🎰 Casino (`sports_bet_stats`,
`sports_result_stats`), más lo común del casino al resolverse (`casino_play` y
`apuestas.record`). No admite porras (`porras.EXCLUDED_GAMES`): se resuelve días después.

Telemetría: cada consulta a la API cuenta como evento `deportes` (`cuotas` o
`resultados`, con lo que tarda y si falla) y los créditos que quedan, como
`créditos de la API`: así el informe dice si el plan gratis llega.

Si `CASINO_CHANNEL_IDS` está configurado, solo se abre en esos canales. Permisos del bot
en el canal: enviar mensajes, insertar enlaces y adjuntar archivos (las imágenes). No
necesita intents especiales.
"""

from __future__ import annotations

import asyncio
import io
import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import aiohttp
import discord
from discord import app_commands, ui
from discord.ext import commands, tasks

from bot.cogs import achievements as logros
from bot.cogs import apuestas, renta
from bot.cogs.casino import casino_channel_error
from bot.repositories.economy import BetSettlement
from bot.repositories.sports import SportsRepository
from bot.services import sports_scene
from bot.services.achievements import (
    casino_stats,
    sports_bet_stats,
    sports_combo_bet_stats,
    sports_combo_result_stats,
    sports_combo_void_stats,
    sports_result_stats,
    sports_slip_stats,
    sports_void_stats,
)
from bot.services.casino_stats import mult
from bot.services.economy import (
    CURRENCY_EMOJI,
    BalanceLimitError,
    EconomyService,
    InsufficientFundsError,
    SportsBet,
    SportsBetTakenError,
    SportsCombo,
    SportsLeg,
    SportsStakeCapError,
    format_amount,
    is_all_in,
    parse_amount,
)
from bot.services.levels import TIMEZONE
from bot.services.pending import Pending, Side
from bot.services.pets import Moment
from bot.services.sports import (
    COMBO_MARKETS,
    EXACT_MAX,
    GAME,
    GOAL_LINES,
    LOOKAHEAD_SECONDS,
    MAX_COMBO_PAYOUT,
    MAX_LEGS,
    MAX_STAKE,
    MIN_LEGS,
    MIN_STAKE,
    RESULT_DELAY,
    RTP,
    SPORT_BY_KEY,
    SPORTS,
    VOID_AFTER,
    FeedError,
    LegStatus,
    Market,
    Match,
    OddsClient,
    Pick,
    Score,
    combo_odds,
    combo_outcome,
    combo_payout,
    days_from,
    leg_status,
    line_text,
    market_of,
    odds_due,
    parse_odds,
    parse_scores,
    payout,
    pick_text,
    scores_due,
    totals_due,
    wins,
)
from bot.services.sports_teams import team_style
from bot.services.telemetry import TimedLock, report_event
from bot.utils.cogs import find_cog
from bot.utils.interactions import ack, edit, notify

if TYPE_CHECKING:
    from bot.app import BotClient

logger = logging.getLogger(__name__)

#: Segundos que se puede manejar el panel (menos que los 15 minutos del token de Discord).
VIEW_TIMEOUT = 600
#: Partidos con su botón en la portada; los siguientes, en un desplegable (25 como mucho).
LISTED = 6
MENU_SIZE = 25
#: Apuestas que enseña 🎟️ Mis apuestas y que pueden salir en `saldo`.
SLIP_SIZE = 15
#: Combinadas que enseña 🎟️ Mis apuestas (cada una ocupa varias líneas).
COMBO_SLIP = 5
#: Apostantes por mensaje de resultado; el resto sale como «y N más».
RESULT_LINES = 20
#: Segundos que se espera a la API de cuotas.
REQUEST_TIMEOUT_SECONDS = 20
#: Los partidos que empezaron hace más de esto se borran (una vez al día).
PRUNE_AFTER = 14 * 86_400
WEEKDAYS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
COLOR = discord.Color.from_rgb(46, 160, 67)
#: Color de cada competición en el panel y en las imágenes.
LEAGUE_COLORS = {
    "soccer_spain_la_liga": discord.Color.from_rgb(255, 75, 68),
    "soccer_uefa_champs_league": discord.Color.from_rgb(59, 108, 255),
}
TICKET_PNG = "boleto.png"
COMBO_PNG = "combinada.png"
#: Icono de cada estado de una pata o de una combinada.
LEG_ICONS = {"open": "⏳", "won": "✅", "lost": "❌", "void": "↩️"}
FINAL_PNG = "final.png"
#: Etiqueta corta de cada mercado en las pestañas y en el marcador final.
TABS = {
    Market.RESULT: "1X2",
    Market.GOALS: "Goles",
    Market.BOTH: "Ambos marcan",
    Market.EXACT: "Exacto",
}

NOT_CONFIGURED = (
    "Las apuestas deportivas no están configuradas en este bot: falta la clave de la API "
    "de cuotas (`ODDS_API_KEY`)."
)
RULES = (
    f"-# Cuota fija al apostar · se cobra con el marcador final · una apuesta por mercado y "
    f"de {format_amount(MIN_STAKE)} a {format_amount(MAX_STAKE)} por partido, sumando mercados."
)
COMBO_RULES = (
    f"De {MIN_LEGS} a {MAX_LEGS} partidos, una opción de cada uno. La cuota es el producto de "
    f"las cuotas y se cobra solo si aciertan todas. Si un partido se anula, cuenta como 1,00. "
    f"Lo apostado cuenta en el tope de cada partido y se cobra como mucho "
    f"{format_amount(MAX_COMBO_PAYOUT)}. El resultado exacto no entra."
)


def odds_text(odds: int) -> str:
    """`185` → `×1,85`."""
    return mult(odds / 100)


def kickoff_text(epoch: float) -> str:
    """`sáb 25/10 21:00` en hora canaria (para donde no caben fechas de Discord)."""
    local = datetime.fromtimestamp(epoch, TIMEZONE)
    return f"{WEEKDAYS[local.weekday()]} {local:%d/%m %H:%M}"


def odds_line(match: Match) -> str:
    """`1 ×1,85 · X ×3,60 · 2 ×4,10`; `—` en un signo sin cuota."""
    return " · ".join(
        f"{pick.value} {odds_text(match.odds[pick]) if pick in match.open_picks else '—'}"
        for pick in Pick
    )


def league_color(sport: str) -> discord.Color:
    """El color de una competición (verde si no tiene)."""
    return LEAGUE_COLORS.get(sport, COLOR)


def chances(match: Match) -> dict[Pick, float]:
    """Probabilidad de cada signo que se enseña, sacada de las cuotas del bot.

    Un signo sin cuota (tan favorito que no llegaba a ×1,01) se queda con lo que falta.
    """
    seen = {pick: RTP * 100 / match.odds[pick] for pick in match.open_picks}
    missing = [pick for pick in Pick if pick not in seen]
    rest = max(0.0, 1 - sum(seen.values()))
    for pick in missing:
        seen[pick] = rest / len(missing)
    total = sum(seen.values()) or 1.0
    return {pick: seen[pick] / total for pick in Pick}


def bar(share: float, width: int = 10) -> str:
    """`▰▰▰▰▱▱▱▱▱▱` para una probabilidad de 0 a 1."""
    full = max(0, min(width, round(share * width)))
    return "▰" * full + "▱" * (width - full)


def percent(share: float) -> str:
    """`0.484` → `48 %`."""
    return f"{round(share * 100)} %"


def decimal(value: float) -> str:
    """`1.84` → `1,8`."""
    return f"{value:.1f}".replace(".", ",")


def match_card(match: Match) -> str:
    """La ficha de un partido: cuándo, la probabilidad de cada signo y los goles esperados."""
    start = int(match.commence)
    lines = [f"## {match.title}", f"🕒 <t:{start}:F> · <t:{start}:R>", ""]
    share = chances(match)
    for pick in Pick:
        lines.append(
            f"`{pick.value}` {bar(share[pick])} **{percent(share[pick])}** · {match.team(pick)}"
        )
    if match.goals is not None:
        lines.append(
            f"-# ⚽ Goles esperados: {match.home} {decimal(match.goals.home)} · "
            f"{match.away} {decimal(match.goals.away)}"
        )
    return "\n".join(lines)


def option_text(match: Match, pick: str) -> str:
    """Una opción de la ficha: qué es, su cuota, su probabilidad y lo que cobran 1.000 Y$."""
    odds = match.price(pick)
    name = pick_text(pick, match.home, match.away)
    if not odds:
        return f"**{name}** · sin cuota"
    market = market_of(pick) or Market.RESULT
    share = market.rtp * 100 / odds
    return (
        f"**{name}** · **{odds_text(odds)}**\n-# {percent(share)} · 1.000 Y$ cobran "
        f"{format_amount(payout(1_000, odds))}"
    )


def header(sport: str, matches: list[Match], *, configured: bool, odds_at: float | None) -> str:
    """El título del panel y la línea de la competición."""
    league = SPORT_BY_KEY[sport].label
    days = LOOKAHEAD_SECONDS // 86_400
    note = f"{league} · {len(matches)} partidos en {days} días" if matches else league
    if configured and odds_at is not None:
        note += f" · cuotas de <t:{int(odds_at)}:R>"
    return f"# ⚽ Apuestas deportivas\n-# {note}"


def leg_text(leg: SportsLeg) -> str:
    """Una pata en una línea: `**1 (Real Madrid)** en Real Madrid – Barcelona a 1,85`."""
    return (
        f"**{pick_text(leg.pick, leg.home, leg.away)}** en {leg.home} – {leg.away} a "
        f"{odds_text(leg.odds)}"
    )


def combo_lines(combo: SportsCombo) -> str:
    """Las patas de una combinada, una por línea con su estado."""
    return "\n".join(f"{LEG_ICONS.get(leg.status, '⏳')} {leg_text(leg)}" for leg in combo.legs)


def combo_slip(combos: list[SportsCombo]) -> str:
    """La sección de combinadas de 🎟️ Mis apuestas."""
    blocks = []
    for combo in combos:
        size = f"{len(combo.legs)} patas"
        if combo.status == "open":
            head = (
                f"⏳ {format_amount(combo.stake)} a una combinada de {size} a "
                f"{odds_text(combo.odds)} · cobrarías "
                f"{format_amount(combo_payout(combo.stake, combo.odds))}"
            )
        elif combo.status == "won":
            head = (
                f"✅ {format_amount(combo.stake)} a una combinada de {size} · cobraste "
                f"**{format_amount(combo.payout)}**"
            )
        elif combo.status == "lost":
            head = (
                f"❌ {format_amount(combo.stake)} a una combinada de {size} a "
                f"{odds_text(combo.odds)}"
            )
        else:
            head = f"↩️ {format_amount(combo.stake)} a una combinada de {size} · anulada y devuelta"
        blocks.append(f"{head}\n{combo_lines(combo)}")
    return "\n".join(blocks)


def slip_card(
    bets: list[SportsBet], name: str, combos: list[SportsCombo] | None = None
) -> ui.LayoutView:
    """🎟️ Mis apuestas: las que esperan resultado, las últimas resueltas y las combinadas."""
    combos = combos or []
    view = ui.LayoutView(timeout=None)
    box = ui.Container(accent_colour=COLOR)
    box.add_item(ui.TextDisplay(f"# 🎟️ Apuestas deportivas de {name}"))
    waiting, done = [], []
    for bet in bets:
        what = f"**{pick_text(bet.pick, bet.home, bet.away)}** en {bet.home} – {bet.away}"
        if bet.status == "open":
            waiting.append(
                f"⏳ {format_amount(bet.stake)} a {what} a {odds_text(bet.odds)}\n"
                f"-# cobrarías {format_amount(payout(bet.stake, bet.odds))} · "
                f"empieza <t:{int(bet.commence)}:R>"
            )
        elif bet.status == "won":
            done.append(
                f"✅ {format_amount(bet.stake)} a {what} a {odds_text(bet.odds)} · "
                f"cobraste **{format_amount(bet.payout)}**"
            )
        elif bet.status == "lost":
            done.append(f"❌ {format_amount(bet.stake)} a {what} a {odds_text(bet.odds)}")
        else:
            done.append(f"↩️ {format_amount(bet.stake)} a {what} · anulada y devuelta")
    # Un TextDisplay admite 4.000 caracteres; 15 apuestas no llegan ni a la mitad.
    if waiting:
        box.add_item(ui.Separator())
        box.add_item(ui.TextDisplay("### Esperando resultado\n" + "\n".join(waiting)))
    if done:
        box.add_item(ui.Separator())
        box.add_item(ui.TextDisplay("### Últimas resueltas\n" + "\n".join(done)))
    if combos:
        box.add_item(ui.Separator())
        # Un TextDisplay admite 4.000 caracteres: se corta por si un día hay muchas.
        box.add_item(ui.TextDisplay(("### 🧾 Combinadas\n" + combo_slip(combos))[:4_000]))
    if not bets and not combos:
        box.add_item(ui.TextDisplay("Aún no has apostado a ningún partido. Elige uno en el panel."))
    view.add_item(box)
    return view


def short_date(epoch: float) -> str:
    """`sáb 1/11` en hora canaria."""
    local = datetime.fromtimestamp(epoch, TIMEZONE)
    return f"{WEEKDAYS[local.weekday()]} {local.day}/{local.month}"


def ticket_state(bet: SportsBet, player: str) -> dict[str, Any]:
    """El boleto de una apuesta para la escena (`sports_scene`), todo ya en texto."""
    sport = SPORT_BY_KEY.get(bet.sport)
    market = market_of(bet.pick) or Market.RESULT
    local = datetime.fromtimestamp(bet.commence, TIMEZONE)
    return {
        "kind": "boleto",
        "league": sport.name if sport else bet.sport,
        "accent": str(league_color(bet.sport)),
        "home": team_style(bet.home),
        "away": team_style(bet.away),
        "kickoff": f"{short_date(bet.commence)} · {local:%H:%M}",
        "market": market.label,
        "pick": pick_text(bet.pick, bet.home, bet.away),
        "odds": odds_text(bet.odds),
        "stake": format_amount(bet.stake),
        "payout": format_amount(payout(bet.stake, bet.odds)),
        "player": player,
        "serial": f"Nº {bet.id:06d}",
    }


def final_state(resolved: list[Resolved]) -> dict[str, Any] | None:
    """El marcador final para la escena, o `None` si el partido se anuló."""
    first, score = resolved[0].bet, resolved[0].score
    if score is None:
        return None
    sport = SPORT_BY_KEY.get(first.sport)
    exact = f"{score.home}-{score.away}" if max(score.home, score.away) <= EXACT_MAX else "Otro"
    won = sum(1 for r in resolved if r.bet.status == "won")
    lost = len(resolved) - won
    summary = " · ".join(
        part
        for part in (
            f"{won} {'cobra' if won == 1 else 'cobran'}" if won else "",
            f"{lost} {'pierde' if lost == 1 else 'pierden'}" if lost else "",
        )
        if part
    )
    return {
        "kind": "final",
        "league": sport.name if sport else first.sport,
        "accent": str(league_color(first.sport)),
        "home": team_style(first.home),
        "away": team_style(first.away),
        "homeGoals": score.home,
        "awayGoals": score.away,
        "date": short_date(first.commence),
        "chips": [
            ["Resultado", score.pick.value],
            ["Goles", str(score.home + score.away)],
            ["Ambos marcan", "Sí" if score.home and score.away else "No"],
            ["Exacto", exact],
        ],
        "summary": summary,
    }


def card(text: str, *, color: discord.Color, image: str | None = None) -> ui.LayoutView:
    """Un bloque sin botones: la imagen adjunta `image` (si la hay) y el texto."""
    view = ui.LayoutView(timeout=None)
    box = ui.Container(accent_colour=color)
    if image is not None:
        box.add_item(ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{image}")))
    box.add_item(ui.TextDisplay(text))
    view.add_item(box)
    return view


@dataclass(frozen=True, slots=True)
class Resolved:
    """Una apuesta recién resuelta, para el mensaje del resultado y los logros.

    Attributes:
        bet: La apuesta ya cerrada (`won`, `lost` o `void`).
        score: El marcador, o `None` si se anuló.
        settlement: Lo que movió en el día del casino (`None` si se anuló).
        balance: Saldo de su dueño al terminar.
    """

    bet: SportsBet
    score: Score | None
    settlement: BetSettlement | None
    balance: int


def result_message(resolved: list[Resolved]) -> str:
    """Mensaje del resultado de un partido para los que apostaron en un canal."""
    first = resolved[0].bet
    league = SPORT_BY_KEY[first.sport].label if first.sport in SPORT_BY_KEY else first.sport
    score = resolved[0].score
    if score is None:
        lines = [
            f"↩️ **{first.home} – {first.away}** ({league}) no tiene resultado: aplazado, "
            "suspendido o sin datos. Se devuelve lo apostado."
        ]
        lines += [
            f"• <@{r.bet.user_id}> recupera {format_amount(r.bet.stake)}"
            for r in resolved[:RESULT_LINES]
        ]
    else:
        lines = [f"⚽ **Final: {first.home} {score.home}–{score.away} {first.away}** · {league}"]
        for r in resolved[:RESULT_LINES]:
            bet = r.bet
            what = f"{pick_text(bet.pick, bet.home, bet.away)} a {odds_text(bet.odds)}"
            if bet.status == "won":
                tax = r.settlement.tax_delta if r.settlement is not None else 0
                irpf = f" · 🐶 IRPF −{format_amount(tax)}" if tax > 0 else ""
                lines.append(
                    f"✅ <@{bet.user_id}> {what}: cobra **{format_amount(bet.payout)}** "
                    f"(+{format_amount(bet.payout - bet.stake)}){irpf}"
                )
            else:
                lines.append(f"❌ <@{bet.user_id}> {what}: pierde {format_amount(bet.stake)}")
    if len(resolved) > RESULT_LINES:
        lines.append(f"-# Y {len(resolved) - RESULT_LINES} apuestas más.")
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class ResolvedCombo:
    """Una combinada recién resuelta, para el mensaje del resultado y los logros.

    Attributes:
        combo: La combinada ya cerrada (`won`, `lost` o `void`) con sus patas al día.
        odds: Cuota con la que se ha cobrado (la de las patas acertadas; 0 si no).
        settlement: Lo que movió en el día del casino (`None` si se anuló).
        balance: Saldo de su dueño al terminar.
    """

    combo: SportsCombo
    odds: int
    settlement: BetSettlement | None
    balance: int


def combo_result_message(resolved: list[ResolvedCombo]) -> str:
    """Las combinadas resueltas de un canal en esta vuelta, una por línea con sus patas."""
    lines = ["# 🧾 Combinadas resueltas"]
    for item in resolved[:RESULT_LINES]:
        combo = item.combo
        size = f"combinada de {len(combo.legs)} patas"
        if combo.status == "won":
            tax = item.settlement.tax_delta if item.settlement is not None else 0
            irpf = f" · 🐶 IRPF −{format_amount(tax)}" if tax > 0 else ""
            capped = " (el tope)" if combo.payout >= MAX_COMBO_PAYOUT else ""
            head = (
                f"✅ <@{combo.user_id}> acierta su {size} a {odds_text(item.odds)}: cobra "
                f"**{format_amount(combo.payout)}**{capped} "
                f"(+{format_amount(combo.payout - combo.stake)}){irpf}"
            )
        elif combo.status == "lost":
            failed = next((leg for leg in combo.legs if leg.status == "lost"), None)
            where = (
                f": falla «{pick_text(failed.pick, failed.home, failed.away)}» en "
                f"{failed.home} – {failed.away}"
                if failed is not None
                else ""
            )
            head = f"❌ <@{combo.user_id}> pierde su {size}{where} · −{format_amount(combo.stake)}"
        else:
            head = (
                f"↩️ <@{combo.user_id}> recupera {format_amount(combo.stake)}: ningún partido "
                f"de su {size} tiene resultado"
            )
        marks = " ".join(LEG_ICONS.get(leg.status, "⏳") for leg in combo.legs)
        lines.append(f"{head}\n-# {marks}")
    if len(resolved) > RESULT_LINES:
        lines.append(f"-# Y {len(resolved) - RESULT_LINES} combinadas más.")
    return "\n".join(lines)


def _ms(started: float) -> float:
    """Milisegundos desde `started` (`time.perf_counter`)."""
    return (time.perf_counter() - started) * 1000


def _report_credits(remaining: int | None) -> None:
    """Apunta en la telemetría los créditos de la API que quedan (en el campo de `ms`)."""
    if remaining is not None:
        report_event("deportes", "créditos de la API", float(remaining))


def win_streak(bets: list[SportsBet]) -> int:
    """Apuestas acertadas seguidas, de la última resuelta hacia atrás (sin anuladas)."""
    settled = sorted(
        (b for b in bets if b.status in ("won", "lost")),
        key=lambda b: (b.settled_at or 0.0, b.id),
        reverse=True,
    )
    streak = 0
    for bet in settled:
        if bet.status != "won":
            break
        streak += 1
    return streak


# -- Panel ------------------------------------------------------------------------------


class StakeModal(discord.ui.Modal):
    """Pide cuánto apostar a una opción."""

    amount: discord.ui.TextInput = discord.ui.TextInput(
        label="¿Cuánto?", placeholder="500, 2k, mitad, all…", max_length=20
    )

    def __init__(self, view: SportsView, match: Match, pick: str) -> None:
        odds = match.price(pick)
        name = pick_text(pick, match.home, match.away)
        super().__init__(title=f"{name} · {odds_text(odds)}"[:45])
        self.panel = view
        self.match = match
        self.pick = pick
        self.odds = odds

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Hace la apuesta."""
        await self.panel.cog.place(
            interaction, self.match, self.pick, seen=self.odds, text=str(self.amount.value)
        )


class ComboStakeModal(discord.ui.Modal):
    """Pide cuánto apostar a una combinada, con las cuotas que se ven al abrirlo."""

    amount: discord.ui.TextInput = discord.ui.TextInput(
        label="¿Cuánto?", placeholder="500, 2k, mitad, all…", max_length=20
    )

    def __init__(self, view: SportsView, legs: list[tuple[str, str, int]]) -> None:
        odds = combo_odds(price for _, _, price in legs)
        super().__init__(title=f"Combinada de {len(legs)} · {odds_text(odds)}"[:45])
        self.panel = view
        self.legs = legs

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Hace la combinada."""
        await self.panel.cog.place_combo(
            interaction, self.legs, text=str(self.amount.value), panel=self.panel
        )


class SportsView(ui.LayoutView):
    """Panel de `deportes`: competición, partido, mercado y opción.

    Todo sale de memoria (`Deportes.upcoming`), así que cada clic se contesta directamente
    con `edit_message`. Los botones llevan `custom_id` fijo (el del partido o la opción).

    En modo combinada (`combining`) el botón de cada opción la añade al boleto (`legs`, en
    memoria) en vez de apostar, y la página de la combinada (`combo_page`) enseña el boleto.

    Args:
        cog: El cog, que tiene los partidos en memoria y apuesta.
        owner_id: Quien abrió el panel (el único que lo maneja).
        sport: Competición que se enseña.
    """

    def __init__(self, cog: Deportes, *, owner_id: int, sport: str) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog = cog
        self.owner_id = owner_id
        self.sport = sport
        self.event_id: str | None = None
        self.market = Market.RESULT
        self.message: discord.Message | discord.InteractionMessage | None = None
        #: El boleto de la combinada: partido → (el partido al añadirlo, la opción).
        self.legs: dict[str, tuple[Match, str]] = {}
        self.combining = False
        self.combo_page = False
        #: Aviso de una sola vez arriba del panel (lo que se acaba de añadir o quitar).
        self.note: str | None = None
        self.rebuild()

    def selected(self) -> Match | None:
        """El partido elegido, si sigue sin empezar."""
        return next((m for m in self.cog.upcoming(self.sport) if m.event_id == self.event_id), None)

    def rebuild(self) -> None:
        """Rehace el panel con la competición, el partido y el mercado elegidos."""
        self.clear_items()
        self._drop_started()
        matches = self.cog.upcoming(self.sport)
        match = None if self.combo_page else self.selected()
        configured = self.cog.client is not None
        box = ui.Container(accent_colour=league_color(self.sport))
        box.add_item(
            ui.TextDisplay(
                header(
                    self.sport,
                    matches,
                    configured=configured,
                    odds_at=self.cog.odds_at.get(self.sport),
                )
            )
        )
        box.add_item(self._top_row(back=match is not None or self.combo_page))
        box.add_item(ui.Separator())
        if not configured:
            box.add_item(ui.TextDisplay(NOT_CONFIGURED))
        elif self.combo_page:
            self._combo_page(box)
        else:
            if self.combining:
                box.add_item(ui.TextDisplay(self._combo_status()))
            self._content(box, match, matches)
        self.note = None
        box.add_item(ui.Separator())
        box.add_item(ui.TextDisplay(RULES))
        self.add_item(box)

    def _content(self, box: ui.Container, match: Match | None, matches: list[Match]) -> None:
        """La ficha del partido, la portada o el aviso de que no hay partidos."""
        if match is not None:
            self._match_page(box, match)
        elif matches:
            self._home_page(box, matches)
        else:
            days = LOOKAHEAD_SECONDS // 86_400
            box.add_item(
                ui.TextDisplay(
                    f"No hay partidos con cuotas en los próximos {days} días. Mira la otra "
                    "competición o vuelve más tarde."
                )
            )

    # -- Combinada -----------------------------------------------------------------------

    def _drop_started(self) -> None:
        """Quita del boleto los partidos que ya han empezado o se han quedado sin cuotas."""
        gone = [snap for event, (snap, _) in self.legs.items() if self.cog.find(event) is None]
        for snap in gone:
            del self.legs[snap.event_id]
        if gone:
            names = ", ".join(snap.title for snap in gone)
            self.note = f"Se ha quitado de la combinada lo que ya ha empezado: {names}."

    def _leg_pick(self, event_id: str) -> str | None:
        """La opción del boleto para un partido, si la hay."""
        leg = self.legs.get(event_id)
        return leg[1] if leg is not None else None

    def _prices(self) -> list[int]:
        """La cuota de ahora de cada pata del boleto (0 si se ha quedado sin)."""
        prices = []
        for event_id, (snap, pick) in self.legs.items():
            current = self.cog.find(event_id) or snap
            prices.append(current.price(pick))
        return prices

    def _combo_status(self) -> str:
        """La línea del boleto arriba de la portada y de la ficha en modo combinada."""
        count = len(self.legs)
        if count < MIN_LEGS:
            text = (
                f"🧾 **Modo combinada** · {count} de {MAX_LEGS} partidos. Abre un partido y "
                f"pulsa «Añadir» en una opción: hacen falta al menos {MIN_LEGS}."
            )
        else:
            prices = self._prices()
            odds = combo_odds(prices) if all(prices) else 0
            total = f" · cuota **{odds_text(odds)}**" if odds else ""
            text = (
                f"🧾 **Modo combinada** · {count} de {MAX_LEGS} partidos{total}. Pulsa "
                "🧾 Combinada para apostarla."
            )
        if self.note:
            text += f"\n-# {self.note}"
        return text

    def _combo_page(self, box: ui.Container) -> None:
        """El boleto: las patas con su botón de quitar, la cuota total y apostar."""
        intro = f"## 🧾 Tu combinada\n{COMBO_RULES}"
        if self.note:
            intro += f"\n-# {self.note}"
        box.add_item(ui.TextDisplay(intro))
        box.add_item(ui.Separator())
        if not self.legs:
            box.add_item(
                ui.TextDisplay(
                    "Aún no hay ningún partido. Pulsa «Añadir partidos», abre uno y elige una "
                    "opción."
                )
            )
        for event_id, (snap, pick) in self.legs.items():
            current = self.cog.find(event_id) or snap
            odds = current.price(pick)
            text = (
                f"**{pick_text(pick, current.home, current.away)}** · "
                f"{odds_text(odds) if odds else 'sin cuota'}\n"
                f"-# {current.title} · 🕒 {kickoff_text(current.commence)}"
            )
            box.add_item(
                ui.Section(
                    text,
                    accessory=self._button(
                        "Quitar", f"deportes:quitar:{event_id}", self._remove_callback(event_id)
                    ),
                )
            )
        prices = self._prices()
        ready = len(prices) >= MIN_LEGS and all(prices)
        if ready:
            odds = combo_odds(prices)
            example = 100
            capped = (
                f" (como mucho {format_amount(MAX_COMBO_PAYOUT)})"
                if payout(example, odds) > MAX_COMBO_PAYOUT
                else ""
            )
            summary = (
                f"Cuota total **{odds_text(odds)}** · con {format_amount(example)} cobrarías "
                f"{format_amount(combo_payout(example, odds))}{capped}."
            )
        elif self.legs and not all(prices):
            summary = "Una opción se ha quedado sin cuota: quítala para poder apostar."
        else:
            summary = f"Añade al menos {MIN_LEGS} partidos para poder apostar."
        box.add_item(ui.Separator())
        box.add_item(ui.TextDisplay(summary))
        row: ui.ActionRow = ui.ActionRow()
        row.add_item(
            self._button(
                "Apostar combinada",
                "deportes:combinar",
                self._combo_stake,
                style=discord.ButtonStyle.success,
                disabled=not ready,
            )
        )
        row.add_item(
            self._button(
                "Añadir partidos",
                "deportes:mas",
                self._more,
                style=discord.ButtonStyle.primary,
                disabled=len(self.legs) >= MAX_LEGS,
            )
        )
        row.add_item(
            self._button(
                "Vaciar y salir",
                "deportes:vaciar",
                self._clear,
                style=discord.ButtonStyle.danger,
            )
        )
        box.add_item(row)

    def _button(
        self,
        label: str,
        custom_id: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        disabled: bool = False,
    ) -> ui.Button:
        button: ui.Button = ui.Button(
            label=label[:80], style=style, disabled=disabled, custom_id=custom_id
        )
        button.callback = callback  # type: ignore[method-assign]
        return button

    def _top_row(self, *, back: bool) -> ui.ActionRow:
        row: ui.ActionRow = ui.ActionRow()
        if back:
            row.add_item(self._button("◀ Partidos", "deportes:volver", self._back))
        for sport in SPORTS:
            row.add_item(
                self._button(
                    sport.label,
                    f"deportes:liga:{sport.key}",
                    self._sport_callback(sport.key),
                    style=(
                        discord.ButtonStyle.primary
                        if sport.key == self.sport
                        else discord.ButtonStyle.secondary
                    ),
                )
            )
        row.add_item(self._button("🎟️ Mis apuestas", "deportes:mis", self._slip))
        count = len(self.legs)
        row.add_item(
            self._button(
                f"🧾 Combinada ({count})" if count else "🧾 Combinada",
                "deportes:combinada",
                self._open_combo,
                style=(
                    discord.ButtonStyle.primary if self.combining else discord.ButtonStyle.secondary
                ),
            )
        )
        return row

    def _home_page(self, box: ui.Container, matches: list[Match]) -> None:
        """La portada: los primeros partidos con su botón y el resto en un desplegable."""
        for match in matches[:LISTED]:
            extra = " · ⚽ goles" if match.goals is not None else ""
            text = (
                f"**{match.title}**\n-# 🕒 {kickoff_text(match.commence)} · "
                f"{odds_line(match)}{extra}"
            )
            if (chosen := self._leg_pick(match.event_id)) is not None:
                text += f"\n-# 🧾 En tu combinada: {pick_text(chosen, match.home, match.away)}"
            box.add_item(
                ui.Section(
                    text,
                    accessory=self._button(
                        "Elegir" if self.combining else "Apostar",
                        f"deportes:ver:{match.event_id}",
                        self._open_callback(match.event_id),
                        style=discord.ButtonStyle.success,
                    ),
                )
            )
        rest = matches[LISTED : LISTED + MENU_SIZE]
        if rest:
            select: ui.Select = ui.Select(
                placeholder=f"Más partidos ({len(rest)})…",
                options=[
                    discord.SelectOption(
                        label=f"{kickoff_text(m.commence)} · {m.title}"[:100],
                        value=m.event_id,
                        description=odds_line(m)[:100],
                    )
                    for m in rest
                ],
                custom_id="deportes:partido",
            )
            select.callback = self._pick_match(select)  # type: ignore[method-assign]
            box.add_item(ui.ActionRow(select))

    def _match_page(self, box: ui.Container, match: Match) -> None:
        """La ficha de un partido: probabilidades, pestañas de mercados y sus opciones."""
        box.add_item(ui.TextDisplay(match_card(match)))
        box.add_item(ui.Separator())
        offered = match.markets()
        if self.combining:
            # El exacto no entra en las combinadas (`COMBO_MARKETS`).
            offered = [m for m in offered if m in COMBO_MARKETS]
        if self.market not in offered:
            self.market = Market.RESULT
        tabs: ui.ActionRow = ui.ActionRow()
        for market in Market:
            tabs.add_item(
                self._button(
                    TABS[market],
                    f"deportes:mercado:{market.value}",
                    self._market_callback(market),
                    style=(
                        discord.ButtonStyle.primary
                        if market is self.market
                        else discord.ButtonStyle.secondary
                    ),
                    disabled=market not in offered,
                )
            )
        box.add_item(tabs)
        if self.market is Market.RESULT:
            for pick in Market.RESULT.picks:
                box.add_item(self._option(match, pick))
        elif self.market is Market.GOALS:
            chance = [
                f"más de {line_text(line)}, {percent(Market.GOALS.rtp * 100 / odds)}"
                for line in GOAL_LINES
                if (odds := match.price(f"+{line}"))
            ]
            box.add_item(
                ui.TextDisplay(
                    "Goles de los dos equipos juntos al final del partido."
                    + (f"\n-# Probabilidad: {' · '.join(chance)}" if chance else "")
                )
            )
            for line in GOAL_LINES:
                row: ui.ActionRow = ui.ActionRow()
                for pick, label in ((f"+{line}", "Más"), (f"-{line}", "Menos")):
                    odds = match.price(pick)
                    row.add_item(
                        self._pick_button(
                            match,
                            pick,
                            f"{label} de {line_text(line)} · {odds_text(odds) if odds else '—'}",
                        )
                    )
                box.add_item(row)
        elif self.market is Market.BOTH:
            box.add_item(ui.TextDisplay("¿Marcan los dos equipos al menos un gol?"))
            for pick in Market.BOTH.picks:
                box.add_item(self._option(match, pick))
        else:
            box.add_item(
                ui.TextDisplay(
                    f"El marcador final exacto. «Otro resultado» gana si algún equipo mete "
                    f"más de {EXACT_MAX} goles."
                )
            )
            options = [
                discord.SelectOption(
                    label=f"{pick if pick != 'otro' else 'Otro resultado'} · "
                    f"{odds_text(match.price(pick))}",
                    value=pick,
                    description=(
                        f"{match.home} {pick.replace('-', ' – ')} {match.away}"
                        if pick != "otro"
                        else f"Algún equipo mete {EXACT_MAX + 1} o más"
                    )[:100],
                )
                for pick in Market.EXACT.picks
                if match.price(pick)
            ]
            select: ui.Select = ui.Select(
                placeholder="Elige el marcador…", options=options, custom_id="deportes:exacto"
            )
            select.callback = self._pick_exact(select)  # type: ignore[method-assign]
            box.add_item(ui.ActionRow(select))

    def _option(self, match: Match, pick: str) -> ui.Section:
        """Una opción con su texto y su botón de apostar (o de añadir a la combinada)."""
        return ui.Section(option_text(match, pick), accessory=self._pick_button(match, pick))

    def _pick_button(self, match: Match, pick: str, label: str | None = None) -> ui.Button:
        """El botón de una opción: apostar o, en modo combinada, añadirla o quitarla.

        Args:
            label: Texto del botón cuando no basta con «Apostar» (las líneas de goles).
        """
        odds = match.price(pick)
        if not self.combining:
            return self._button(
                label or "Apostar",
                f"deportes:apostar:{pick}",
                self._bet_callback(pick),
                style=discord.ButtonStyle.success,
                disabled=not odds,
            )
        chosen = self._leg_pick(match.event_id) == pick
        full = match.event_id not in self.legs and len(self.legs) >= MAX_LEGS
        if label is None:
            text = "Quitar" if chosen else "Añadir"
        else:
            text = f"{'✓' if chosen else '➕'} {label}"
        return self._button(
            text,
            f"deportes:anadir:{pick}",
            self._add_callback(pick),
            style=discord.ButtonStyle.danger if chosen else discord.ButtonStyle.primary,
            disabled=not odds or full,
        )

    # -- Callbacks ---------------------------------------------------------------------

    def _sport_callback(self, sport: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            self.sport = sport
            self.event_id = None
            self.market = Market.RESULT
            self.combo_page = False
            await self.show(interaction)

        return callback

    async def _back(self, interaction: discord.Interaction) -> None:
        self.event_id = None
        self.market = Market.RESULT
        self.combo_page = False
        await self.show(interaction)

    async def _open_combo(self, interaction: discord.Interaction) -> None:
        self.combining = True
        self.combo_page = True
        await self.show(interaction)

    async def _more(self, interaction: discord.Interaction) -> None:
        self.combo_page = False
        self.event_id = None
        self.market = Market.RESULT
        await self.show(interaction)

    async def _clear(self, interaction: discord.Interaction) -> None:
        self.legs.clear()
        self.combining = False
        self.combo_page = False
        await self.show(interaction)

    def _remove_callback(self, event_id: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            self.legs.pop(event_id, None)
            await self.show(interaction)

        return callback

    def _add_callback(self, pick: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await self._add(interaction, pick)

        return callback

    async def _add(self, interaction: discord.Interaction, pick: str) -> None:
        """Añade la opción al boleto, la cambia por la que había del partido o la quita.

        Solo toca memoria: se contesta directamente. Al añadir se vuelve a la portada para
        elegir el siguiente partido, o al boleto si ya está lleno.
        """
        match = self.selected()
        market = market_of(pick)
        if match is None or not match.price(pick) or market not in COMBO_MARKETS:
            await interaction.response.send_message(
                "Ese partido ya ha empezado o esa opción no se puede combinar.", ephemeral=True
            )
            return
        name = pick_text(pick, match.home, match.away)
        if self._leg_pick(match.event_id) == pick:
            del self.legs[match.event_id]
            self.note = f"Quitado de la combinada: {name}."
            await self.show(interaction)
            return
        if match.event_id not in self.legs and len(self.legs) >= MAX_LEGS:
            await interaction.response.send_message(
                f"La combinada ya tiene {MAX_LEGS} partidos, el máximo. Quita uno para "
                "añadir otro.",
                ephemeral=True,
            )
            return
        replaced = match.event_id in self.legs
        self.legs[match.event_id] = (match, pick)
        self.note = (
            f"Cambiado en la combinada: {name} en {match.title}."
            if replaced
            else f"Añadido a la combinada: {name} en {match.title}."
        )
        self.event_id = None
        self.market = Market.RESULT
        self.combo_page = len(self.legs) >= MAX_LEGS
        await self.show(interaction)

    async def _combo_stake(self, interaction: discord.Interaction) -> None:
        # Un formulario tiene que ser la primera respuesta: aquí solo se mira memoria.
        self._drop_started()
        legs = []
        for event_id, (snap, pick) in self.legs.items():
            current = self.cog.find(event_id) or snap
            legs.append((event_id, pick, current.price(pick)))
        if len(legs) < MIN_LEGS or not all(odds for _, _, odds in legs):
            await interaction.response.send_message(
                f"La combinada necesita al menos {MIN_LEGS} partidos sin empezar y con cuota.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(ComboStakeModal(self, legs))

    def _open_callback(self, event_id: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            self.event_id = event_id
            self.market = Market.RESULT
            await self.show(interaction)

        return callback

    def _pick_match(self, select: ui.Select) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            self.event_id = select.values[0]
            self.market = Market.RESULT
            await self.show(interaction)

        return callback

    def _market_callback(self, market: Market) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            self.market = market
            await self.show(interaction)

        return callback

    def _bet_callback(self, pick: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await self._stake(interaction, pick)

        return callback

    def _pick_exact(self, select: ui.Select) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            await self._stake(interaction, select.values[0])

        return callback

    async def _stake(self, interaction: discord.Interaction, pick: str) -> None:
        # Un formulario tiene que ser la primera respuesta: aquí solo se mira memoria.
        match = self.selected()
        if match is None or not match.price(pick):
            await interaction.response.send_message(
                "Ese partido ya ha empezado o esa opción ya no tiene cuota.", ephemeral=True
            )
            return
        await interaction.response.send_modal(StakeModal(self, match, pick))

    async def _slip(self, interaction: discord.Interaction) -> None:
        await self.cog.slip(interaction)

    async def show(self, interaction: discord.Interaction) -> None:
        """Repinta el panel. Todo sale de memoria: se contesta directamente."""
        self.rebuild()
        await interaction.response.edit_message(view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Este panel es de otra persona. Abre el tuyo con `/deportes`.", ephemeral=True
        )
        return False

    async def on_timeout(self) -> None:
        """Apaga los botones al caducar (sin la vista, el panel se quedaría vacío)."""
        if self.message is None:
            return
        for child in self.walk_children():
            if isinstance(child, ui.Button | ui.Select):
                child.disabled = True
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug("No se pudo apagar el panel de deportes al caducar")


# -- Cog ----------------------------------------------------------------------------------


class Deportes(commands.Cog):
    """Apuestas deportivas con cuotas fijas.

    Args:
        bot: Cliente.
        economy: Economía (el dinero de las apuestas).
        repository: Partidos, cuotas y resultados.
        api_key: Clave de The Odds API; vacía, no se pide nada.
        casino_channel_ids: Canales del casino (vacío = cualquiera).
        clock: Fuente de tiempo (epoch); inyectable en pruebas.
        session: Sesión HTTP; inyectable en pruebas. Si no se da, se crea al cargar.
        scene: Quien dibuja el boleto y el marcador; si no se da, `SportsScene`.
    """

    def __init__(
        self,
        bot: commands.Bot,
        economy: EconomyService,
        repository: SportsRepository,
        *,
        api_key: str = "",
        casino_channel_ids: frozenset[int] = frozenset(),
        clock: Callable[[], float] = time.time,
        session: aiohttp.ClientSession | None = None,
        scene: sports_scene.SportsScene | None = None,
    ) -> None:
        self.bot = bot
        self.economy = economy
        self.repository = repository
        self.api_key = api_key
        self.casino_channel_ids = casino_channel_ids
        self.clock = clock
        self._session = session
        self._own_session = session is None
        self.scene = scene or sports_scene.SportsScene()
        self.client: OddsClient | None = None
        #: Próximos partidos de cada competición y cuándo llegaron sus cuotas.
        self.matches: dict[str, list[Match]] = {}
        self.odds_at: dict[str, float] = {}
        self._settling = TimedLock("sports:Deportes._settling")
        self._pruned = 0.0

    async def cog_load(self) -> None:
        """Carga los partidos guardados, abre la conexión con la API y arranca el bucle."""
        now = self.clock()
        try:
            for sport in SPORTS:
                await self._reload(sport.key, now)
        except sqlite3.Error:
            # Sin partidos en memoria el panel sale vacío hasta la siguiente consulta.
            logger.exception("No se pudieron leer los partidos guardados de deportes")
        if self.api_key:
            if self._session is None:
                timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
                self._session = aiohttp.ClientSession(timeout=timeout)
            self.client = OddsClient(self._session, self.api_key)
        self._feed.start()

    async def cog_unload(self) -> None:
        """Para el bucle y cierra la conexión y el dibujo."""
        self._feed.cancel()
        if self._own_session and self._session is not None:
            await self._session.close()
        await self.scene.close()

    async def _reload(self, sport: str, now: float) -> None:
        """Vuelve a leer de la base los próximos partidos de una competición."""
        self.matches[sport] = await self.repository.upcoming(sport, now=now)
        state = await self.repository.feed(sport)
        if state.odds_at is not None:
            self.odds_at[sport] = state.odds_at

    def upcoming(self, sport: str) -> list[Match]:
        """Los partidos de una competición que aún no han empezado (de memoria)."""
        now = self.clock()
        return [m for m in self.matches.get(sport, []) if m.commence > now]

    def find(self, event_id: str) -> Match | None:
        """Un partido que aún no ha empezado, por su id (de memoria)."""
        for sport in SPORTS:
            for match in self.upcoming(sport.key):
                if match.event_id == event_id:
                    return match
        return None

    async def _draw(self, state: dict[str, Any] | None) -> bytes | None:
        """Un PNG de la escena, o `None` si no se puede (el mensaje sale en texto)."""
        if state is None or self.scene.disabled:
            return None
        try:
            return await self.scene.still(state)
        except Exception:
            # Una imagen que falla no puede tumbar la apuesta ni el anuncio del resultado.
            logger.exception("No se pudo dibujar una imagen de deportes")
            return None

    # -- Apostar ---------------------------------------------------------------------

    async def place(
        self, interaction: discord.Interaction, match: Match, pick: str, *, seen: int, text: str
    ) -> None:
        """Cobra la apuesta del formulario a la cuota que se vio, si sigue igual."""
        # Cobrar va a la base de datos: se acepta antes.
        await ack(interaction)
        user = interaction.user
        guild_id = interaction.guild_id
        market = market_of(pick)
        if guild_id is None or user.bot:
            await notify(interaction, "Solo se apuesta dentro de un servidor.")
            return
        if market is None:
            await notify(interaction, "Esa opción no existe.")
            return
        now = self.clock()
        current = self.find(match.event_id)
        if current is None:
            await notify(interaction, "Ese partido ya ha empezado o ya no tiene cuotas.")
            return
        odds = current.price(pick)
        name = pick_text(pick, current.home, current.away)
        if not odds:
            await notify(interaction, "Esa opción ya no tiene cuota.")
            return
        if odds != seen:
            await notify(
                interaction,
                f"La cuota ha cambiado: «{name}» paga ahora {odds_text(odds)} (antes, "
                f"{odds_text(seen)}). Vuelve a pulsarlo si te sigue valiendo.",
            )
            return
        placed = await self.economy.sports_match_bets(guild_id, user.id, current.event_id)
        if any(bet.market == market for bet in placed):
            await notify(
                interaction,
                f"Ya tienes una apuesta de {market.label.lower()} en este partido: una por "
                "mercado.",
            )
            return
        staked = await self.economy.sports_staked(guild_id, user.id, [current.event_id])
        room = MAX_STAKE - staked[current.event_id]
        balance = await self.economy.balance(guild_id, user.id)
        try:
            stake = parse_amount(text, balance)
        except ValueError as error:
            await notify(interaction, str(error))
            return
        if is_all_in(text):
            stake = min(stake, room)
        if not MIN_STAKE <= stake <= room:
            await notify(interaction, self._cap_text(room))
            return
        ticket = SportsBet(
            id=0,
            guild_id=guild_id,
            user_id=user.id,
            channel_id=interaction.channel_id or 0,
            event_id=current.event_id,
            sport=current.sport,
            home=current.home,
            away=current.away,
            commence=current.commence,
            pick=pick,
            odds=odds,
            stake=stake,
            created_at=now,
            market=market.value,
        )
        try:
            bet, balance = await self.economy.sports_bet(ticket, game=GAME, cap=MAX_STAKE)
        except SportsBetTakenError:
            await notify(interaction, "Ya tienes una apuesta a ese mercado: una por mercado.")
            return
        except SportsStakeCapError as error:
            await notify(interaction, self._cap_text(error.room))
            return
        except InsufficientFundsError as error:
            await notify(interaction, f"No te llega: tienes {format_amount(error.balance)}.")
            return
        lines = [
            f"🎟️ Apuestas **{format_amount(stake)}** a **{name}** en **{current.title}** a "
            f"{odds_text(odds)}.",
            f"Si aciertas, cobras **{format_amount(payout(stake, odds))}**. Empieza "
            f"<t:{int(bet.commence)}:R> y el resultado sale en este canal.",
            f"{CURRENCY_EMOJI} Te quedan **{format_amount(balance)}**",
            "-# 🐶 Lo que ganes cuenta en el casino del día en que acaba el partido, con la "
            "retención del IRPF del juego.",
        ]
        # Apostar es un resultado con dinero: la mascota activa puede opinar.
        if hint := await renta.hint(self.bot, guild_id, user.id, Moment()):
            lines.append(hint)
        png = await self._draw(ticket_state(bet, user.display_name))
        color = league_color(current.sport)
        if png is None:
            await notify(interaction, view=card("\n".join(lines), color=color))
        else:
            await notify(
                interaction,
                view=card("\n".join(lines), color=color, image=TICKET_PNG),
                file=discord.File(io.BytesIO(png), filename=TICKET_PNG),
            )
        # Apostar es gastar: gancho de la Renta (ver Biblia.txt, sección 4).
        await renta.remind(self.bot, interaction)
        await logros.track(
            self.bot,
            guild_id,
            user,
            interaction.channel,
            sports_bet_stats(
                stake=stake,
                odds=odds,
                pick=pick,
                sport=current.sport,
                balance_before=balance + stake,
                lead=current.commence - now,
                when=datetime.fromtimestamp(now, TIMEZONE),
                market=market.value,
                match_markets=len({b.market for b in placed} | {market.value}),
            ),
        )

    async def place_combo(
        self,
        interaction: discord.Interaction,
        legs: list[tuple[str, str, int]],
        *,
        text: str,
        panel: SportsView | None = None,
    ) -> None:
        """Cobra la combinada del formulario a las cuotas que se vieron, si siguen igual.

        Args:
            legs: Cada pata como (partido, opción, cuota que se vio).
            text: La cantidad escrita en el formulario.
            panel: El panel desde el que se apostó; si sale bien, se vacía su boleto.
        """
        # Cobrar va a la base de datos: se acepta antes.
        await ack(interaction)
        user = interaction.user
        guild_id = interaction.guild_id
        if guild_id is None or user.bot:
            await notify(interaction, "Solo se apuesta dentro de un servidor.")
            return
        events = [event_id for event_id, _, _ in legs]
        if not MIN_LEGS <= len(legs) <= MAX_LEGS or len(set(events)) != len(events):
            await notify(
                interaction,
                f"Una combinada lleva de {MIN_LEGS} a {MAX_LEGS} partidos distintos.",
            )
            return
        now = self.clock()
        matches: list[Match] = []
        changed: list[str] = []
        for event_id, pick, seen in legs:
            current = self.find(event_id)
            if current is None:
                await notify(
                    interaction,
                    "Un partido de la combinada ya ha empezado o ya no tiene cuotas. Vuelve "
                    "a abrir 🧾 Combinada: se quita solo.",
                )
                return
            odds = current.price(pick)
            name = pick_text(pick, current.home, current.away)
            if market_of(pick) not in COMBO_MARKETS or not odds:
                await notify(interaction, f"«{name}» ya no se puede combinar.")
                return
            if odds != seen:
                changed.append(f"«{name}» paga ahora {odds_text(odds)} (antes, {odds_text(seen)})")
            matches.append(current)
        if changed:
            await notify(
                interaction,
                "Han cambiado cuotas de la combinada: " + "; ".join(changed) + ". Vuelve a "
                "pulsar «Apostar combinada» si te sigue valiendo.",
            )
            return
        staked = await self.economy.sports_staked(guild_id, user.id, events)
        room = MAX_STAKE - max(staked.values())
        balance = await self.economy.balance(guild_id, user.id)
        try:
            stake = parse_amount(text, balance)
        except ValueError as error:
            await notify(interaction, str(error))
            return
        if is_all_in(text):
            stake = min(stake, room)
        if not MIN_STAKE <= stake <= room:
            await notify(interaction, self._combo_cap_text(room))
            return
        prices = [seen for _, _, seen in legs]
        odds = combo_odds(prices)
        ticket = SportsCombo(
            id=0,
            guild_id=guild_id,
            user_id=user.id,
            channel_id=interaction.channel_id or 0,
            stake=stake,
            odds=odds,
            created_at=now,
            legs=tuple(
                SportsLeg(
                    event_id=match.event_id,
                    sport=match.sport,
                    home=match.home,
                    away=match.away,
                    commence=match.commence,
                    market=market_of(pick).value,  # type: ignore[union-attr]
                    pick=pick,
                    odds=seen,
                )
                for match, (_, pick, seen) in zip(matches, legs, strict=True)
            ),
        )
        try:
            combo, balance = await self.economy.sports_combo_bet(ticket, game=GAME, cap=MAX_STAKE)
        except SportsStakeCapError as error:
            await notify(interaction, self._combo_cap_text(error.room))
            return
        except InsufficientFundsError as error:
            await notify(interaction, f"No te llega: tienes {format_amount(error.balance)}.")
            return
        if panel is not None:
            # Apostada: el boleto del panel se vacía para no apostarla dos veces sin querer.
            panel.legs.clear()
            panel.combining = False
            panel.combo_page = False
            panel.rebuild()
            try:
                await edit(interaction, view=panel)
            except discord.HTTPException:
                logger.debug("No se pudo vaciar el boleto del panel de deportes")
        prize = combo_payout(stake, odds)
        capped = " (el tope por combinada)" if prize < payout(stake, odds) else ""
        lines = [
            f"🧾 Apuestas **{format_amount(stake)}** a una combinada de **{len(legs)} partidos** "
            f"a **{odds_text(odds)}**:",
            "\n".join(f"• {leg_text(leg)} · 🕒 {kickoff_text(leg.commence)}" for leg in combo.legs),
            f"Si aciertas todas, cobras **{format_amount(prize)}**{capped}. Se resuelve en "
            "cuanto falle una o acabe la última, y el resultado sale en este canal.",
            f"{CURRENCY_EMOJI} Te quedan **{format_amount(balance)}**",
            "-# 🐶 Lo que ganes cuenta en el casino del día en que se resuelve, con la "
            "retención del IRPF del juego.",
        ]
        # Apostar es un resultado con dinero: la mascota activa puede opinar.
        if hint := await renta.hint(self.bot, guild_id, user.id, Moment()):
            lines.append(hint)
        await notify(interaction, view=card("\n".join(lines), color=COLOR))
        # Apostar es gastar: gancho de la Renta (ver Biblia.txt, sección 4).
        await renta.remind(self.bot, interaction)
        await logros.track(
            self.bot,
            guild_id,
            user,
            interaction.channel,
            sports_combo_bet_stats(
                stake=stake,
                odds=odds,
                legs=len(legs),
                sports={leg.sport for leg in combo.legs},
                markets={leg.market for leg in combo.legs},
                balance_before=balance + stake,
            ),
        )

    @staticmethod
    def _combo_cap_text(room: int) -> str:
        """El aviso del tope de una combinada, con lo que aún cabe."""
        if room < MIN_STAKE:
            return (
                f"Algún partido de la combinada ya tiene {format_amount(MAX_STAKE)} tuyos, el "
                "máximo sumando sencillas y combinadas."
            )
        return (
            f"La combinada va de {format_amount(MIN_STAKE)} a {format_amount(MAX_STAKE)} y "
            f"cuenta en el tope de cada partido que toca: te caben {format_amount(room)}."
        )

    @staticmethod
    def _cap_text(room: int) -> str:
        """El aviso del tope por partido, con lo que aún cabe."""
        if room < MIN_STAKE:
            return (
                f"Ya llevas {format_amount(MAX_STAKE)} en este partido, el máximo sumando "
                "mercados y combinadas."
            )
        return (
            f"La apuesta va de {format_amount(MIN_STAKE)} a {format_amount(MAX_STAKE)} por "
            f"partido, sumando mercados y combinadas: en este te caben {format_amount(room)}."
        )

    async def slip(self, interaction: discord.Interaction) -> None:
        """🎟️ Mis apuestas, en un mensaje privado aparte."""
        await ack(interaction, new_message=True)
        guild_id = interaction.guild_id or 0
        bets = await self.economy.sports_bets(guild_id, interaction.user.id, limit=SLIP_SIZE)
        combos = await self.economy.sports_combos(guild_id, interaction.user.id, limit=COMBO_SLIP)
        name = discord.utils.escape_markdown(interaction.user.display_name)
        await edit(interaction, view=slip_card(bets, name, combos))
        await logros.track(
            self.bot, guild_id, interaction.user, interaction.channel, sports_slip_stats()
        )

    # -- Cuotas y resultados ---------------------------------------------------------

    @tasks.loop(minutes=5)
    async def _feed(self) -> None:
        try:
            await self.tick()
        except Exception:
            # Una excepción sin capturar pararía la tarea para siempre.
            logger.exception("Error con las apuestas deportivas")

    @_feed.before_loop
    async def _before_feed(self) -> None:
        await self.bot.wait_until_ready()

    async def tick(self) -> None:
        """Pide a la API lo que toque y resuelve lo que ya tiene resultado."""
        now = self.clock()
        if self.client is not None:
            # Qué partidos jugados esperan marcador: sencillas y patas de combinadas.
            waits = [(b.sport, b.commence) for b in await self.economy.open_sports_bets()]
            waits += [
                (leg.sport, leg.commence)
                for combo in await self.economy.open_sports_combos()
                for leg in combo.legs
                if leg.status == LegStatus.OPEN
            ]
            states = {sport.key: await self.repository.feed(sport.key) for sport in SPORTS}
            # Los créditos son de la cuenta, no de la competición: vale la última respuesta.
            latest = max(states.values(), key=lambda s: max(s.odds_at or 0.0, s.scores_at or 0.0))
            remaining = latest.remaining
            for sport in SPORTS:
                state = states[sport.key]
                kickoffs = [m.commence for m in self.upcoming(sport.key)]
                next_kickoff = min(kickoffs, default=None)
                if odds_due(
                    now=now,
                    fetched_at=state.odds_at,
                    next_kickoff=next_kickoff,
                    remaining=remaining,
                ):
                    with_totals = totals_due(
                        now=now,
                        fetched_at=state.totals_at,
                        next_kickoff=next_kickoff,
                        remaining=remaining,
                    )
                    remaining = await self._fetch_odds(
                        sport.key, now, remaining, totals=with_totals
                    )
                waiting = [
                    commence
                    for key, commence in waits
                    if key == sport.key and commence + RESULT_DELAY <= now
                ]
                oldest = min(waiting, default=None)
                if oldest is not None and scores_due(
                    now=now, checked_at=state.scores_at, oldest=oldest
                ):
                    remaining = await self._fetch_scores(
                        sport.key, now, days_from(now=now, oldest=oldest), remaining
                    )
        await self.settle(now)
        await self.settle_combos(now)
        if now - self._pruned >= 86_400:
            self._pruned = now
            await self.repository.prune(before=now - PRUNE_AFTER)

    async def _fetch_odds(
        self, sport: str, now: float, remaining: int | None, *, totals: bool = False
    ) -> int | None:
        """Pide y guarda las cuotas de una competición. Devuelve los créditos que quedan.

        Args:
            totals: Si la consulta lleva también el más/menos (un crédito más). Si no, los
                mercados de goles se ajustan con el que ya se sabía de cada partido.
        """
        assert self.client is not None
        started = time.perf_counter()
        try:
            reply = await self.client.odds(sport, now=now, totals=totals)
        except FeedError as error:
            logger.warning("Cuotas de %s: %s", sport, error)
            report_event("deportes", "cuotas", _ms(started), error=True)
            await self.repository.mark_checked(sport, "odds_at", now=now)
            return remaining
        report_event("deportes", "cuotas", _ms(started))
        _report_credits(reply.remaining)
        known = {m.event_id: m.totals for m in self.matches.get(sport, [])}
        # El ajuste de los goles tarda unos milisegundos por partido: fuera del bucle.
        matches = await asyncio.to_thread(
            lambda: parse_odds(reply.events, now=now, known_totals=known)
        )
        await self.repository.save_odds(
            sport, matches, now=now, remaining=reply.remaining, totals=totals
        )
        await self._reload(sport, now)
        return reply.remaining if reply.remaining is not None else remaining

    async def _fetch_scores(
        self, sport: str, now: float, days: int, remaining: int | None
    ) -> int | None:
        """Pide y guarda los resultados de una competición. Devuelve los créditos que quedan."""
        assert self.client is not None
        started = time.perf_counter()
        try:
            reply = await self.client.scores(sport, days=days)
        except FeedError as error:
            logger.warning("Resultados de %s: %s", sport, error)
            report_event("deportes", "resultados", _ms(started), error=True)
            await self.repository.mark_checked(sport, "scores_at", now=now)
            return remaining
        report_event("deportes", "resultados", _ms(started))
        _report_credits(reply.remaining)
        await self.repository.save_scores(
            sport, parse_scores(reply.events), now=now, remaining=reply.remaining
        )
        return reply.remaining if reply.remaining is not None else remaining

    async def settle(self, now: float) -> list[Resolved]:
        """Resuelve las apuestas con resultado y anula las que ya no lo tendrán.

        Returns:
            Lo resuelto en esta vuelta (también se anuncia y se cuenta).
        """
        async with self._settling:
            bets = [b for b in await self.economy.open_sports_bets() if b.commence <= now]
            if not bets:
                return []
            scores = await self.repository.scores(sorted({b.event_id for b in bets}))
            resolved = []
            for bet in bets:
                score = scores.get(bet.event_id)
                if score is None and now < bet.commence + VOID_AFTER:
                    continue
                if (done := await self._close(bet, score)) is not None:
                    resolved.append(done)
        groups: dict[tuple[int, int, str], list[Resolved]] = {}
        for item in resolved:
            key = (item.bet.guild_id, item.bet.channel_id, item.bet.event_id)
            groups.setdefault(key, []).append(item)
        for group in groups.values():
            await self._announce(group)
            await self._count(group)
        return resolved

    async def _close(self, bet: SportsBet, score: Score | None) -> Resolved | None:
        """Paga o anula una apuesta; `None` si otro ya la había cerrado."""
        if score is not None:
            prize = payout(bet.stake, bet.odds) if wins(bet.pick, score) else 0
            try:
                done = await self.economy.settle_sports_bet(
                    bet.guild_id, bet.id, payout=prize, game=GAME
                )
            except BalanceLimitError:
                logger.warning("La apuesta %s pasaría el saldo máximo; se anula.", bet.id)
                score = None
            else:
                if done is None:
                    return None
                closed, settlement = done
                return Resolved(closed, score, settlement, settlement.balance)
        voided = await self.economy.void_sports_bet(bet.guild_id, bet.id, game=GAME)
        if voided is None:
            return None
        closed, balance = voided
        return Resolved(closed, None, None, balance)

    async def settle_combos(self, now: float) -> list[ResolvedCombo]:
        """Apunta las patas jugadas y resuelve las combinadas que ya se saben.

        Returns:
            Las combinadas cerradas en esta vuelta (también se anuncian y se cuentan).
        """
        async with self._settling:
            combos = [
                combo
                for combo in await self.economy.open_sports_combos()
                if any(leg.status == LegStatus.OPEN and leg.commence <= now for leg in combo.legs)
            ]
            if not combos:
                return []
            events = sorted(
                {
                    leg.event_id
                    for combo in combos
                    for leg in combo.legs
                    if leg.status == LegStatus.OPEN and leg.commence <= now
                }
            )
            scores = await self.repository.scores(events)
            resolved = []
            for combo in combos:
                if (done := await self._advance_combo(combo, scores, now)) is not None:
                    resolved.append(done)
        groups: dict[tuple[int, int], list[ResolvedCombo]] = {}
        for item in resolved:
            groups.setdefault((item.combo.guild_id, item.combo.channel_id), []).append(item)
        for group in groups.values():
            await self._announce_combos(group)
            await self._count_combos(group)
        return resolved

    async def _advance_combo(
        self, combo: SportsCombo, scores: dict[str, Score], now: float
    ) -> ResolvedCombo | None:
        """Apunta las patas que ya se saben y, si con ellas se sabe todo, la cierra."""
        updates = {}
        for leg in combo.legs:
            if leg.status != LegStatus.OPEN or leg.commence > now:
                continue
            status = leg_status(leg.pick, scores.get(leg.event_id), commence=leg.commence, now=now)
            if status is not LegStatus.OPEN:
                updates[leg.event_id] = status.value
        if not updates:
            return None
        outcome, odds = combo_outcome(
            (LegStatus(updates.get(leg.event_id, leg.status)), leg.odds) for leg in combo.legs
        )
        if outcome is LegStatus.OPEN:
            await self.economy.mark_sports_legs(combo.guild_id, combo.id, updates)
            return None
        if outcome is not LegStatus.VOID:
            prize = combo_payout(combo.stake, odds) if outcome is LegStatus.WON else 0
            try:
                done = await self.economy.settle_sports_combo(
                    combo.guild_id, combo.id, statuses=updates, payout=prize, game=GAME
                )
            except BalanceLimitError:
                logger.warning("La combinada %s pasaría el saldo máximo; se anula.", combo.id)
            else:
                if done is None:
                    return None
                closed, settlement = done
                return ResolvedCombo(closed, odds, settlement, settlement.balance)
        voided = await self.economy.void_sports_combo(
            combo.guild_id, combo.id, statuses=updates, game=GAME
        )
        if voided is None:
            return None
        closed, balance = voided
        return ResolvedCombo(closed, 0, None, balance)

    async def _announce_combos(self, group: list[ResolvedCombo]) -> None:
        """Las combinadas resueltas en el canal donde se apostaron; menciona a quien cobra."""
        channel = self.bot.get_channel(group[0].combo.channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            return
        pinged = [r.combo.user_id for r in group if r.combo.payout > 0][:RESULT_LINES]
        mentions = discord.AllowedMentions(
            everyone=False, roles=False, users=[discord.Object(id=u) for u in pinged]
        )
        try:
            await channel.send(
                view=card(combo_result_message(group), color=COLOR), allowed_mentions=mentions
            )
        except discord.HTTPException:
            logger.warning("No se pudo anunciar el resultado de las combinadas")

    async def _count_combos(self, group: list[ResolvedCombo]) -> None:
        """Logros y estadísticas del casino de cada combinada resuelta."""
        for item in group:
            combo = item.combo
            guild = self.bot.get_guild(combo.guild_id)
            member = guild.get_member(combo.user_id) if guild is not None else None
            if member is None:
                continue
            channel = self.bot.get_channel(combo.channel_id)
            if item.settlement is None:
                await logros.track(
                    self.bot, combo.guild_id, member, channel, sports_combo_void_stats()
                )
                continue
            statuses = [leg.status for leg in combo.legs]
            net = combo.payout - combo.stake
            delta = sports_combo_result_stats(
                stake=combo.stake,
                odds=item.odds,
                payout=combo.payout,
                legs=len(combo.legs),
                won=statuses.count(LegStatus.WON),
                lost=statuses.count(LegStatus.LOST),
                void=statuses.count(LegStatus.VOID),
            )
            delta.merge(
                casino_stats(
                    stake=combo.stake,
                    net=net,
                    balance_after=item.balance,
                    tax_delta=item.settlement.tax_delta,
                )
            )
            await logros.casino_play(self.bot, combo.guild_id, member, channel, delta, net=net)
            await apuestas.record(
                self.bot,
                combo.guild_id,
                member,
                game=GAME,
                stake=combo.stake,
                net=net,
                balance_after=item.balance,
                tax=item.settlement.tax_delta,
            )

    async def _announce(self, group: list[Resolved]) -> None:
        """El resultado en el canal donde se apostó; menciona a quien cobra o recupera."""
        channel = self.bot.get_channel(group[0].bet.channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            return
        pinged = [r.bet.user_id for r in group if r.bet.payout > 0][:RESULT_LINES]
        mentions = discord.AllowedMentions(
            everyone=False, roles=False, users=[discord.Object(id=u) for u in pinged]
        )
        text = result_message(group)
        png = await self._draw(final_state(group))
        color = league_color(group[0].bet.sport)
        try:
            if png is None:
                await channel.send(view=card(text, color=color), allowed_mentions=mentions)
            else:
                await channel.send(
                    view=card(text, color=color, image=FINAL_PNG),
                    file=discord.File(io.BytesIO(png), filename=FINAL_PNG),
                    allowed_mentions=mentions,
                )
        except discord.HTTPException:
            logger.warning("No se pudo anunciar el resultado de %s", group[0].bet.event_id)

    async def _count(self, group: list[Resolved]) -> None:
        """Logros y estadísticas del casino de cada apuesta resuelta."""
        for item in group:
            bet = item.bet
            guild = self.bot.get_guild(bet.guild_id)
            member = guild.get_member(bet.user_id) if guild is not None else None
            if member is None:
                continue
            channel = self.bot.get_channel(bet.channel_id)
            if item.score is None or item.settlement is None:
                await logros.track(self.bot, bet.guild_id, member, channel, sports_void_stats())
                continue
            net = bet.payout - bet.stake
            streak = 0
            if bet.status == "won":
                recent = await self.economy.sports_bets(bet.guild_id, bet.user_id, limit=SLIP_SIZE)
                streak = win_streak(recent)
            delta = sports_result_stats(
                stake=bet.stake,
                odds=bet.odds,
                pick=bet.pick,
                payout=bet.payout,
                home=item.score.home,
                away=item.score.away,
                streak=streak,
                market=bet.market,
            )
            delta.merge(
                casino_stats(
                    stake=bet.stake,
                    net=net,
                    balance_after=item.balance,
                    tax_delta=item.settlement.tax_delta,
                )
            )
            await logros.casino_play(self.bot, bet.guild_id, member, channel, delta, net=net)
            await apuestas.record(
                self.bot,
                bet.guild_id,
                member,
                game=GAME,
                stake=bet.stake,
                net=net,
                balance_after=item.balance,
                tax=item.settlement.tax_delta,
            )

    # -- Comandos --------------------------------------------------------------------

    def _view(self, owner_id: int) -> SportsView:
        """Un panel nuevo, en la primera competición con partidos."""
        sport = next((s.key for s in SPORTS if self.upcoming(s.key)), SPORTS[0].key)
        return SportsView(self, owner_id=owner_id, sport=sport)

    @app_commands.command(
        name="deportes",
        description="Apuesta a partidos de LaLiga y la Champions: resultado y goles.",
    )
    @app_commands.guild_only()
    async def deportes(self, interaction: discord.Interaction) -> None:
        """Abre el panel de apuestas deportivas (efímero). Sin permisos especiales."""
        if error := casino_channel_error(
            self.casino_channel_ids, interaction.channel, "Las apuestas deportivas"
        ):
            await interaction.response.send_message(error, ephemeral=True)
            return
        view = self._view(interaction.user.id)
        await interaction.response.send_message(view=view, ephemeral=True)
        view.message = await interaction.original_response()

    @commands.command(name="deportes")
    @commands.guild_only()
    async def deportes_text(self, ctx: commands.Context) -> None:
        """Versión de texto (`.deportes`): el panel se ve en el canal."""
        if error := casino_channel_error(
            self.casino_channel_ids, ctx.channel, "Las apuestas deportivas"
        ):
            await ctx.send(error)
            return
        view = self._view(ctx.author.id)
        view.message = await ctx.send(view=view)


async def pending(bot: commands.Bot, guild_id: int, user_id: int) -> list[Pending]:
    """Las apuestas y combinadas deportivas de alguien que esperan resultado (para `saldo`).

    Nunca lanza: si algo falla se registra y `saldo` sale sin estas líneas.
    """
    cog = find_cog(bot, Deportes)
    if cog is None:
        return []
    try:
        bets = await cog.economy.sports_bets(guild_id, user_id, limit=SLIP_SIZE)
        combos = await cog.economy.sports_combos(guild_id, user_id, limit=SLIP_SIZE)
    except (OSError, sqlite3.Error):
        logger.exception("No se pudieron leer las apuestas deportivas de %s", user_id)
        return []
    lines = [
        Pending(
            Side.HELD,
            "sports_bet",
            "⚽",
            f"{format_amount(bet.stake)} a {pick_text(bet.pick, bet.home, bet.away)} en "
            f"{bet.home} – {bet.away} (cobrarías {format_amount(payout(bet.stake, bet.odds))})",
            bet.stake,
            bet.commence,
        )
        for bet in bets
        if bet.status == "open"
    ]
    lines += [
        Pending(
            Side.HELD,
            "sports_combo",
            "🧾",
            f"{format_amount(combo.stake)} a una combinada de {len(combo.legs)} partidos a "
            f"{odds_text(combo.odds)} (cobrarías "
            f"{format_amount(combo_payout(combo.stake, combo.odds))})",
            combo.stake,
            combo.ends,
        )
        for combo in combos
        if combo.status == "open"
    ]
    return lines


async def setup(bot: BotClient) -> None:  # type: ignore[override]
    """Registra el cog con la economía, los partidos y la clave de la API del bot."""
    await bot.add_cog(
        Deportes(
            bot,
            bot.economy,
            bot.sports,
            api_key=bot.odds_api_key,
            casino_channel_ids=bot.casino_channel_ids,
        )
    )
