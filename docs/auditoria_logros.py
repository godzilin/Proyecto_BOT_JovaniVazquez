"""Auditoría de rarezas de los logros, calculada con el código real.

Uso, desde la raíz del repo:

    python docs/auditoria_logros.py            # tabla de discrepancias
    python docs/auditoria_logros.py --todo     # todos los logros con su estimación
    python docs/auditoria_logros.py --json     # lo mismo en JSON
    python docs/auditoria_logros.py --juego pachinko --jugadores 4 --dias 20
                                               # solo un juego y con menos horizonte

La rareza de un logro debería reflejar cuánto le cuesta a un miembro activo:

    ▫️ Común       tres días o menos (lo normal al empezar)
    🔹 Raro        hasta dos semanas
    💠 Épico       hasta dos meses
    🌟 Legendario  hasta ocho meses
    👑 Mítico      más de ocho meses, o suerte de una entre muchas decenas de miles

Cómo se estima, según el tipo de logro:

1. **Juegos del casino**: se simulan jugadores con el código de cada juego y la
   función de estadísticas que usa el cog (`slots_stats`, `pachinko_stats`…).
   Cada jugador juega `RITMO_CASINO[juego]` partidas al día con una estrategia
   de jugador normal (apuestas y retiradas variadas, ver cada `_jugar_*`), y se
   apunta el día en que salta cada logro. La estimación es la mediana de los
   jugadores; si menos de la mitad lo consigue en `HORIZONTE` días, sale `>`.
2. **Contadores** (mensajes, voz, reacciones, IMV…): meta / `RITMO[estadística]`,
   el ritmo diario de un miembro activo que hace esa cosa.
3. **Fechas** (Halloween, Reyes…) y logros de suerte o decisión pura que no se
   simulan: no salen en la tabla; se revisan a mano (ver `docs/auditoria-logros.md`).
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, "src")

from bot.services import blackjack as bj  # noqa: E402
from bot.services import (  # noqa: E402
    bus,
    chicken,
    coin,
    craps,
    crash,
    hold_win,
    mines,
    pachinko,
    roulette,
    slots,
)
from bot.services import horses as caballos  # noqa: E402
from bot.services.achievements import (  # noqa: E402
    AVAILABLE,
    CATALOG,
    CATEGORY_BY_KEY,
    CHICKEN_VEHICLE_KINDS,
    Achievement,
    Rarity,
    StatDelta,
    blackjack_stats,
    bus_stats,
    casino_stats,
    chicken_stats,
    coin_stats,
    craps_stats,
    crash_stats,
    hold_win_bonus_stats,
    hold_win_stats,
    horses_stats,
    mines_stats,
    pachinko_stats,
    roulette_stats,
    slots_bonus_stats,
    slots_double_stats,
    slots_respin_stats,
    slots_stats,
)
from bot.services.levels import TIMEZONE  # noqa: E402
from bot.services.pachinko_motion import motion_for  # noqa: E402

#: Límites (en días) de cada rareza, de menor a mayor.
BANDAS = (
    (3, Rarity.COMMON),
    (14, Rarity.RARE),
    (60, Rarity.EPIC),
    (240, Rarity.LEGENDARY),
)
HORIZONTE = 400
APUESTA = 100
NOON = datetime(2026, 3, 10, 18, tzinfo=TIMEZONE)


def rareza_para(dias: float) -> Rarity:
    """Rareza que corresponde a un esfuerzo de `dias` días."""
    for limite, rareza in BANDAS:
        if dias <= limite:
            return rareza
    return Rarity.MYTHIC


# -- Ritmos de un miembro activo ---------------------------------------------------

#: Partidas al día de alguien que juega a ese juego (con Auto y turbo donde los hay).
RITMO_CASINO = {
    "roulette": 40,
    "blackjack": 40,
    "slots": 200,
    "botes": 150,
    "crash": 30,
    "mines": 40,
    "chicken": 60,
    "coin": 60,
    "bus": 60,
    "dice": 50,
    "pachinko": 60,
    "horses": 30,
}

#: Cuánto suma al día cada contador para un miembro activo que hace esa cosa.
RITMO: dict[str, float] = {
    # Chat
    "messages_total": 150, "msg_replies": 30, "msg_questions": 15, "msg_mentions": 8,
    "msg_attachments": 4, "msg_links": 4, "msg_stickers": 2, "msg_long": 0.3,
    "msg_short": 20, "msg_caps": 1, "msg_night": 10, "msg_morning": 5, "msg_rae": 3,
    "msg_exclaim": 2, "msg_everyone": 0.05, "msg_mass_ping": 0.1, "msg_spoiler": 0.3,
    "msg_code": 0.5, "msg_emoji_heavy": 0.5, "msg_only_emoji": 4, "msg_stretch": 1,
    "msg_siesta": 15, "msg_office": 40, "msg_weekend": 30,
    "msg_laughs": 15, "laugh_es": 10, "laugh_en": 2, "msg_xd": 5, "laugh_emoji": 3,
    "laugh_skull": 1, "laugh_smash": 0.5, "laugh_intl": 0.1, "laugh_phrase": 1,
    "msg_laugh_caps": 1, "msg_laugh_dry": 0.3, "laugh_night": 1, "laugh_replies": 5,
    "laughs_caused": 3, "laugh_at_bot": 0.3, "laugh_reacts_given": 3,
    "laugh_reacts_received": 3,
    "msg_canario": 2, "msg_boricua": 0.5, "msg_swear": 4, "msg_thanks": 2, "msg_sorry": 0.5,
    "msg_good_morning": 0.7, "msg_good_night": 0.5, "msg_politics": 1, "msg_hacienda": 0.3,
    "msg_bizum_ask": 0.2, "msg_first_of_day": 0.1, "msg_echo": 0.3, "msg_necro": 0.1,
    "msg_edits": 3,
    "reactions_given": 20, "reactions_received": 20, "greetings_sent": 0.05,
    "greetings_received": 0.02, "welcomes_given": 0.05,
    # Voz (minutos)
    "voice_minutes": 120, "voice_night": 30, "voice_stream": 20, "voice_video": 5,
    "voice_muted": 30, "voice_alone": 10, "voice_duo": 40, "voice_morning": 10,
    "voice_siesta": 30, "voice_weekend": 60, "voice_deaf": 10, "voice_afk": 15,
    "voice_music": 20, "voice_stream_crowd": 5, "voice_multitask": 2,
    "voice_joins": 4, "voice_hops": 2, "voice_ghost": 0.2, "voice_stream_starts": 1,
    # Funcionalidades sueltas
    "todo_added": 1, "todo_done": 0.8, "music_queued": 10, "music_skips": 3,
    "music_stops": 0.5, "music_removes": 0.3, "img_made": 3, "img_magik": 1,
    "img_video": 0.5, "img_on_others": 1.5, "img_self": 1, "babel_phrases": 1,
    "babel_renames": 0.3, "entrance_saved": 0.1, "entrance_played": 4, "hk_clock": 1,
    "logros_views": 2, "logros_others": 1, "logros_ranking": 0.5,
    # Economía y trabajo
    "imv_claims": 1, "work_shifts": 6, "lottery_bets": 5, "lottery_scratches": 5,
    "shop_purchases": 1, "bizum_sent_count": 1, "bizum_received_count": 1,
    "renta_filed": 1 / 7, "interest_days": 1, "work_guards": 1, "work_hk_shifts": 6,
}  # fmt: skip


# -- Simulación del casino ---------------------------------------------------------


@dataclass
class Jugador:
    """Estado de un jugador simulado: sus estadísticas y lo que lleva su juego."""

    rng: random.Random
    stats: dict[str, int] = field(default_factory=dict)
    extra: dict[str, object] = field(default_factory=dict)

    def sumar(self, delta: StatDelta) -> None:
        for stat, value in delta.add.items():
            self.stats[stat] = self.stats.get(stat, 0) + value
        for stat, value in delta.peak.items():
            self.stats[stat] = max(self.stats.get(stat, 0), value)


def _con_casino(delta: StatDelta, *, stake: int, net: int) -> StatDelta:
    delta.merge(casino_stats(stake=stake, net=net, balance_after=1_000_000))
    return delta


def _jugar_slots(j: Jugador) -> StatDelta:
    """Una tirada de un jugador normal, con lo que haga después.

    Re-gira el tercer rodillo la mitad de las veces que se le ofrece (y sigue
    re-girando mientras se quede a uno), llena la barra de bonus con cada
    tirada pagada, y juega a doble o nada la mitad de
    los premios, doblando otra vez la mitad de las veces que acierta. El bote
    cae también por el tope oculto, sorteado entre la semilla y `POT_CAP`.
    """
    rng = j.rng
    machine = slots.SlotMachine(rng.randrange)
    free = j.extra.get("free", 0)
    heat = j.extra.get("heat", 0)
    session = j.extra.get("session", 0) + 1
    pot = j.extra.get("pot", slots.POT_SEED)
    hit_at = j.extra.get("hit_at") or rng.randint(slots.POT_SEED, slots.POT_CAP)
    drought = j.extra.get("drought", 0)
    hot = heat >= slots.HEAT_MAX
    spin = machine.spin()
    payout = slots.line_payout(spin, APUESTA, hot=hot)
    # El bote lo alimentan cinco jugadores: el simulado y cuatro más.
    pot += 5 * slots.pot_share(APUESTA)
    drought += 5
    jackpot, mystery = 0, False
    if spin.kind == slots.Kind.JACKPOT or pot >= hit_at:
        mystery = spin.kind != slots.Kind.JACKPOT
        jackpot, pot = pot, slots.POT_SEED
        hit_at = rng.randint(slots.POT_SEED, slots.POT_CAP)
    meter, bonus_stake, fill_spins = j.extra.get("bonus", slots.BonusMeter()), 0, 0
    if not free:
        fill_spins = meter.spins + 1
        meter, bonus_stake = slots.add_bonus(meter, spin, APUESTA, rng.randrange)
    j.extra.update(
        bonus=meter,
        free=(free - 1 if free else 0)
        + (slots.FREE_SPINS if spin.triggers_free_spins else 0)
        + (slots.BONUS_FREE_SPINS if bonus_stake else 0),
        heat=slots.next_heat(heat, paid=payout > 0, was_hot=hot),
        session=0 if session >= 150 else session,
        pot=pot,
        hit_at=hit_at,
        drought=0 if jackpot else drought,
    )
    delta = slots_stats(
        spin,
        stake=APUESTA,
        payout=payout,
        jackpot=jackpot,
        free=bool(free),
        hot=hot,
        turbo=rng.random() < 0.7,
        session_spins=session,
        when=NOON,
        tier=slots.win_tier(payout + jackpot, APUESTA),
        mystery=mystery,
        drought=drought if jackpot else 0,
    )
    delta.add["slots_auto"] = 1 if rng.random() < 0.05 else 0
    if bonus_stake:
        delta.merge(slots_bonus_stats(fill_spins=fill_spins))
    paid = 0 if free else APUESTA
    net = payout + jackpot - paid

    chain, current = 0, spin
    while current.teaser is not None and rng.random() < 0.5:
        chain += 1
        price = slots.respin_price(current, APUESTA, pot)
        current = machine.respin(current)
        won = slots.line_payout(current, APUESTA)
        delta.merge(slots_respin_stats(price=price, payout=won, jackpot=0, chain=chain))
        paid += price
        net += won - price

    amount, doubles = payout + jackpot, 0
    while amount and doubles < slots.DOUBLE_MAX and rng.random() < 0.5:
        won = rng.random() < 0.5
        delta.merge(slots_double_stats(amount=amount, won=won, chain=doubles + won))
        paid += amount
        net += amount if won else -amount
        if not won:
            break
        doubles += 1
        amount *= 2
    return _con_casino(delta, stake=paid, net=net)


def _jugar_botes(j: Jugador) -> StatDelta:
    rng = j.rng
    meters = j.extra.setdefault("meters", hold_win.Meters())
    spin = hold_win.spin_base(rng)
    payout = hold_win.to_amount(spin.ways_points + spin.collect_points, APUESTA)
    trigger = hold_win.fill_cases(meters, spin, APUESTA, rng)  # type: ignore[arg-type]
    delta = hold_win_stats(
        spin,
        theme="volcan",
        stake=APUESTA,
        payout=payout,
        trigger=trigger,
        turbo=rng.random() < 0.7,
        session_spins=1,
        when=NOON,
    )
    delta.add["botes_auto"] = 1 if rng.random() < 0.05 else 0
    net = payout - APUESTA
    if trigger is not None:
        game = hold_win.BonusGame.start(
            trigger,
            mini=meters.mini,  # type: ignore[attr-defined]
            major=meters.major,  # type: ignore[attr-defined]
            rng=rng,
        )
        steps = []
        while not game.finished:
            steps.append(game.step(rng))
        result = game.result()
        hold_win.reset_won_jackpots(meters, result.jackpots)  # type: ignore[arg-type]
        amount = hold_win.to_amount(result.points, game.stake)
        delta.merge(hold_win_bonus_stats(result, steps, amount=amount))
        net += amount
    return _con_casino(delta, stake=APUESTA, net=net)


_TABLEROS = (("clasica", 40), ("sakura", 30), ("dragon", 15), ("oni", 15))


def _elegir(rng: random.Random, tabla: tuple[tuple[object, int], ...]) -> object:
    return rng.choices([x for x, _ in tabla], weights=[w for _, w in tabla])[0]


def _jugar_pachinko(j: Jugador) -> StatDelta:
    rng = j.rng
    board = pachinko.BOARDS[_elegir(rng, _TABLEROS)]  # type: ignore[index]
    volley = pachinko.PachinkoMachine(rng.randrange, rng.randrange).launch(board)
    won = pachinko.payout(volley, APUESTA)
    session = j.extra.get("session", 0) + 1
    j.extra["session"] = 0 if session >= 60 else session
    delta = pachinko_stats(
        volley,
        motion=motion_for(volley),
        stake=APUESTA,
        won=won,
        turbo=rng.random() < 0.5,
        session_volleys=session,
        when=NOON,
    )
    delta.add["pachinko_burst"] = 1 if rng.random() < 0.03 else 0
    return _con_casino(delta, stake=APUESTA, net=won - APUESTA)


_MINAS = ((1, 10), (2, 30), (3, 20), (5, 20), (8, 10), (12, 10))
_OBJETIVOS = ((150, 30), (200, 25), (300, 15), (500, 12), (1_000, 9), (2_500, 5), (10_000, 3),
              (100_000, 1))  # fmt: skip


def _jugar_minas(j: Jugador) -> StatDelta:
    rng = j.rng
    game = mines.MinesGame.new(APUESTA, _elegir(rng, _MINAS), rng)  # type: ignore[arg-type]
    target = _elegir(rng, _OBJETIVOS)
    dice = rng.random() < 0.1
    while game.playing:
        tile = game.random_hidden(rng)
        alive = game.reveal(tile, random_pick=dice)
        if alive and (game.cleared or game.cents >= target):  # type: ignore[operator]
            if game.playing:
                game.cash_out()
    return _con_casino(mines_stats(game), stake=APUESTA, net=game.net)


_DIFICULTADES = (("facil", 20), ("media", 45), ("dificil", 25), ("hardcore", 10))
_POR_CLAVE = {d.key: d for d in chicken.DIFFICULTIES}


def _jugar_pollo(j: Jugador) -> StatDelta:
    rng = j.rng
    difficulty = _POR_CLAVE[_elegir(rng, _DIFICULTADES)]  # type: ignore[index]
    game = chicken.ChickenGame.new(APUESTA, difficulty, rng)
    target = _elegir(rng, _OBJETIVOS + ((10**9, 2),))
    if rng.random() < 0.3:
        top = chicken.multiplier_cents(difficulty, difficulty.lanes)
        game.cross_until(min(target, top))  # type: ignore[type-var]
    else:
        while game.playing and not game.finished_road and game.cents < target:  # type: ignore[operator]
            game.cross()
    if game.playing and game.crossed:
        game.cash_out()
    elif game.playing:
        game.cross()
        if game.playing:
            game.cash_out()
    vehicle = rng.choice(CHICKEN_VEHICLE_KINDS) if game.status is chicken.Status.SPLAT else None
    return _con_casino(chicken_stats(game, vehicle=vehicle), stake=APUESTA, net=game.net)


#: Aciertos a los que cobra un jugador normal de cara o cruz (el 10 es ir a por el oro).
_RACHAS = ((1, 30), (2, 25), (3, 20), (4, 11), (5, 7), (6, 3), (7, 2), (10, 2))
#: Cómo elige lado: siempre el mismo, alternando o al azar.
_ESTILOS = (("cara", 25), ("cruz", 25), ("alterna", 10), ("azar", 40))


def _jugar_moneda(j: Jugador) -> StatDelta:
    rng = j.rng
    game = coin.CoinGame.new(APUESTA, rng)
    target = _elegir(rng, _RACHAS)
    estilo = _elegir(rng, _ESTILOS)
    side = coin.Side.CRUZ if estilo == "cruz" else coin.Side.CARA
    while game.playing and not game.maxed and game.wins < target:  # type: ignore[operator]
        if estilo == "azar":
            side = rng.choice(list(coin.Side))
        game.flip(side, rng)
        if estilo == "alterna":
            side = side.other
    if game.playing:
        game.cash_out()
    return _con_casino(coin_stats(game, when=NOON), stake=APUESTA, net=game.net)


#: Parada en la que se baja un jugador normal del autobús (5 = se juega la vuelta).
_PARADAS = ((1, 25), (2, 25), (3, 20), (4, 20), (5, 10))
#: Cómo elige en cada mano: la opción más probable, una cualquiera o la más larga.
_ELECCIONES = (("segura", 75), ("azar", 20), ("larga", 5))


def _jugar_autobus(j: Jugador) -> StatDelta:
    rng = j.rng
    game = bus.BusGame.new(APUESTA, rng)
    target = _elegir(rng, _PARADAS)
    while game.playing and game.hand is not None and game.wins < target:  # type: ignore[operator]
        options = [o for o in game.options() if o.chance]
        estilo = _elegir(rng, _ELECCIONES)
        if estilo == "segura":
            top = max(o.chance for o in options)
            pick = rng.choice([o for o in options if o.chance == top]).pick
        elif estilo == "larga":
            pick = min(options, key=lambda o: o.chance).pick
        else:
            pick = rng.choice(options).pick
        game.play(pick)
    if game.playing:
        game.cash_out()
    return _con_casino(bus_stats(game, when=NOON), stake=APUESTA, net=game.net)


#: Apuesta de salida de un jugador normal de dados: casi siempre Pase.
_APUESTAS_DADOS = ((craps.Bet.PASS, 85), (craps.Bet.DONT, 15))
#: Cuántas Odds pone con el punto puesto: ninguna, una ficha o al tope.
_ODDS = ((0, 40), (1, 30), (craps.ODDS_MAX, 30))


def _jugar_dados(j: Jugador) -> StatDelta:
    rng = j.rng
    hand = j.extra.get("mano")
    if not isinstance(hand, craps.Hand) or hand.seven_out:
        hand = craps.Hand()
        j.extra["mano"] = hand
    game = craps.CrapsGame.new(APUESTA, _elegir(rng, _APUESTAS_DADOS))  # type: ignore[arg-type]
    hand.observe(game.play(rng))
    if game.playing:
        fichas = _elegir(rng, _ODDS)
        if fichas:
            game.add_odds(APUESTA * fichas)  # type: ignore[operator]
    while game.playing:
        hand.observe(game.play(rng))
    return _con_casino(craps_stats(game, hand, when=NOON), stake=game.wagered, net=game.net)


_CRASH = ((120, 15), (150, 20), (200, 25), (300, 12), (500, 10), (1_000, 8), (2_000, 4),
          (5_000, 3), (10_000, 2), (100_000, 1))  # fmt: skip


def _jugar_crash(j: Jugador) -> StatDelta:
    rng = j.rng
    point = crash.crash_point(rng.random())
    target = _elegir(rng, _CRASH)
    auto = rng.random() < 0.4
    cashed = target if target <= point else None  # type: ignore[operator]
    seat = SimpleNamespace(
        cashed_cents=cashed,
        by_auto=auto and cashed is not None,
        net=(crash.payout(APUESTA, cashed) - APUESTA) if cashed else -APUESTA,
    )
    players = rng.choice((1, 1, 2, 2, 3, 4, 6))
    last = cashed is not None and players > 1 and rng.random() < 0.15
    delta = crash_stats(seat, crash_cents=point, players=players, last_out=last)  # type: ignore[arg-type]
    return _con_casino(delta, stake=APUESTA, net=seat.net)


def _jugar_ruleta(j: Jugador) -> StatDelta:
    rng = j.rng
    wheel = roulette.Wheel(rng.randrange)
    bets = []
    for _ in range(rng.choice((1, 1, 1, 2, 3, 5))):
        r = rng.random()
        if r < 0.55:
            bet = roulette.OUTSIDE_BETS[rng.choice(("red", "black"))]
        elif r < 0.8:
            bet = roulette.inside_bet(frozenset({rng.randrange(38)}))
        else:
            bet = roulette.OUTSIDE_BETS[rng.choice(list(roulette.OUTSIDE_BETS))]
        if all(w.bet != bet for w in bets):
            bets.append(roulette.Wager(bet, APUESTA))
    # Uno de cada diez plenos sale de los botones 🔥 Caliente y ❄️ Frío.
    hunches = {
        w.bet.key: rng.choice(("hot", "cold"))
        for w in bets
        if w.bet.straight and rng.random() < 0.1
    }
    outcome = roulette.play_round(wheel, bets)
    streak = j.extra.get("streak", 0) + 1 if outcome.won else 0
    previous = j.extra.get("prev")
    j.extra.update(streak=streak, prev=outcome.pocket)
    delta = roulette_stats(  # type: ignore[arg-type]
        outcome, table_streak=streak, previous_pocket=previous, hunches=hunches
    )
    return _con_casino(delta, stake=outcome.stake, net=outcome.net)


def _jugar_blackjack(j: Jugador) -> StatDelta:
    rng = j.rng
    game = bj.BlackjackGame(APUESTA, shoe=bj.new_shoe(rng.shuffle))
    game.deal()
    while game.player_turn:
        hand = game.current
        up = game.dealer[0].value
        if game.can(bj.Action.SPLIT) and hand.cards[0].value in (1, 8):
            game.act(bj.Action.SPLIT)
        elif game.can(bj.Action.DOUBLE) and hand.total in (10, 11) and up < 10:
            game.act(bj.Action.DOUBLE)
        elif hand.total < 17 and not (hand.total >= 13 and up <= 6):
            game.act(bj.Action.HIT)
        elif hand.total >= 17 and rng.random() < 0.01:
            game.act(bj.Action.HIT)  # el kamikaze de turno
        else:
            game.act(bj.Action.STAND)
    game.reveal_hole()
    while game.dealer_should_draw():
        game.dealer_draw()
    game.settle()
    return _con_casino(blackjack_stats(game), stake=game.total_stake, net=game.net)


#: Carreras ya preparadas (parrilla y cuotas): estimar las cuotas es lo caro y
#: con unas decenas de parrillas distintas basta para que salga de todo.
_PARRILLAS: list[tuple[caballos.RaceCard, caballos.Odds]] = []
_TIPOS = ((caballos.BetKind.WIN, 55), (caballos.BetKind.PLACE, 15),
          (caballos.BetKind.EXACTA, 18), (caballos.BetKind.TRIFECTA, 12))  # fmt: skip
_VIAS = (("panel", 65), ("sanxe", 15), ("pueblo", 10), ("azar", 10))


def _parrillas() -> list[tuple[caballos.RaceCard, caballos.Odds]]:
    if not _PARRILLAS:
        rng = np.random.default_rng(1)
        for n in range(60):
            # Una de cada diez carreras es Gran Premio (como mucho uno cada 4 h).
            card = caballos.new_card(rng, {}, now=0, grand_prix=n % 10 == 0)
            _PARRILLAS.append((card, caballos.estimate(card, rng, trials=20_000)))
    return _PARRILLAS


def _jugar_caballos(j: Jugador) -> StatDelta:
    rng = j.rng
    card, odds = rng.choice(_parrillas())
    kind = _elegir(rng, _TIPOS)
    # Un jugador normal tira hacia los favoritos, pero no siempre.
    weights = [p**0.7 + 1e-6 for p in odds.win]
    horses = []
    while len(horses) < kind.picks:  # type: ignore[attr-defined]
        horse = rng.choices(range(card.size), weights=weights)[0]
        if horse not in horses:
            horses.append(horse)
    pick = caballos.Pick(kind, tuple(horses))  # type: ignore[arg-type]
    cents = odds.odds(pick)
    result = caballos.run_race(card, np.random.default_rng(rng.getrandbits(64)))
    won = pick.wins(result.order)
    share = 0
    if card.grand_prix and caballos.pot_eligible(pick, APUESTA, result.order):
        share = caballos.GRAND_PRIX_POT // rng.choice((1, 1, 2))
    prize = (caballos.payout(APUESTA, cents) if won else 0) + share
    tip = caballos.sanxe_tip(odds, np.random.default_rng(rng.getrandbits(64)))
    delta = horses_stats(
        card=card,
        result=result,
        pick=pick,
        stake=APUESTA,
        odds_cents=cents,
        net=prize - APUESTA,
        pot_share=share,
        tip_horse=tip.horse,
        alone=rng.random() < 0.15,
        via=_elegir(rng, _VIAS),  # type: ignore[arg-type]
        players=rng.choice((1, 1, 2, 3, 4, 6)),
        favourite=odds.favourite(),
        favourite_odds=odds.odds(caballos.Pick(caballos.BetKind.WIN, (odds.favourite(),))),
        photo=caballos.photo_finish(result, card.distance),
        comeback=caballos.comeback(result, card.distance, pick.horses[0]),
        tax_delta=0,
        when=NOON,
    )
    return _con_casino(delta, stake=APUESTA, net=prize - APUESTA)


JUEGOS: dict[str, Callable[[Jugador], StatDelta]] = {
    "slots": _jugar_slots,
    "botes": _jugar_botes,
    "pachinko": _jugar_pachinko,
    "mines": _jugar_minas,
    "chicken": _jugar_pollo,
    "coin": _jugar_moneda,
    "bus": _jugar_autobus,
    "dice": _jugar_dados,
    "crash": _jugar_crash,
    "roulette": _jugar_ruleta,
    "blackjack": _jugar_blackjack,
    "horses": _jugar_caballos,
}


def _met(a: Achievement, stats: dict[str, int]) -> bool:
    return all(stats.get(s, 0) >= g for s, g in a.conditions)


def simular(
    juego: str, jugadores: int, semilla: int = 1, horizonte: int = HORIZONTE
) -> dict[str, float]:
    """Mediana de días hasta desbloquear cada logro de `juego` (inf si no da tiempo).

    El pachinko calcula el movimiento de las bolas con choques en cada tanda
    (~0,1 s), así que simular 400 días de 60 tandas cuesta horas: para él se
    usa `horizonte` bajo y el resto se saca de las probabilidades (ver
    `docs/auditoria-logros.md`).
    """
    objetivos = [a for a in AVAILABLE if a.category == juego]
    ritmo = RITMO_CASINO[juego]
    jugar = JUEGOS[juego]
    dias: dict[str, list[float]] = {a.id: [] for a in objetivos}
    for n in range(jugadores):
        j = Jugador(random.Random(semilla * 1000 + n))
        pendientes = list(objetivos)
        partida = 0
        while pendientes and partida < horizonte * ritmo:
            partida += 1
            j.sumar(jugar(j))
            if partida % 10 and partida > 50:
                continue  # mirar cada 10 partidas basta y va diez veces más rápido
            for a in [a for a in pendientes if _met(a, j.stats)]:
                dias[a.id].append(partida / ritmo)
                pendientes.remove(a)
    out = {}
    for achievement_id, values in dias.items():
        values += [float("inf")] * (jugadores - len(values))
        out[achievement_id] = statistics.median(values)
    return out


def contador(a: Achievement) -> float | None:
    """Días para un logro de un solo contador con ritmo conocido."""
    if len(a.conditions) != 1 or a.stat not in RITMO:
        return None
    return a.goal / RITMO[a.stat]


def auditar(
    jugadores: int, solo: str | None = None, horizonte: int = HORIZONTE
) -> list[dict[str, object]]:
    """Estimación de cada logro: días, rareza sugerida y la actual.

    Args:
        solo: Si se da, audita únicamente los logros de ese juego.
        horizonte: Días que juega cada jugador simulado antes de rendirse.
    """
    estimado: dict[str, tuple[float, str]] = {}
    for juego in JUEGOS:
        if solo and juego != solo:
            continue
        for achievement_id, dias in simular(juego, jugadores, horizonte=horizonte).items():
            estimado[achievement_id] = (dias, "simulado")
    for a in AVAILABLE:
        if solo and a.category != solo:
            continue
        if a.id not in estimado and (dias := contador(a)) is not None:
            estimado[a.id] = (dias, "ritmo")
    filas = []
    for a in CATALOG:
        if a.id not in estimado:
            continue
        dias, metodo = estimado[a.id]
        sugerida = rareza_para(dias)
        filas.append(
            {
                "id": a.id,
                "categoria": CATEGORY_BY_KEY[a.category].title,
                "nombre": a.name,
                "dias": round(dias, 2) if dias != float("inf") else None,
                "metodo": metodo,
                "actual": a.rarity.label,
                "sugerida": sugerida.label,
                "salto": list(Rarity).index(sugerida) - list(Rarity).index(a.rarity),
            }
        )
    return filas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--jugadores", type=int, default=9)
    parser.add_argument("--todo", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--juego", choices=sorted(JUEGOS), help="auditar solo este juego")
    parser.add_argument("--dias", type=int, default=HORIZONTE, help="horizonte de cada jugador")
    args = parser.parse_args()
    filas = auditar(args.jugadores, args.juego, args.dias)
    if not args.todo:
        filas = [f for f in filas if f["salto"]]
    if args.json:
        print(json.dumps(filas, ensure_ascii=False, indent=1))
        return
    for f in filas:
        dias = f["dias"] if f["dias"] is not None else f">{args.dias}"
        print(
            f"{f['salto']:+d} {f['id']:24} {str(dias):>8} d  {f['actual']:>10} → "
            f"{f['sugerida']:<10} ({f['metodo']}) {f['categoria']}"
        )


if __name__ == "__main__":
    main()
