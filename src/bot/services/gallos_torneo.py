"""Galleras y el torneo del domingo de los gallos (`gallos`): reglas puras, sin Discord.

**Galleras.** Un club de hasta `CLUB_MAX` miembros de un servidor. Cuando uno
acaba un minijuego de entrenamiento, cada uno de los demás gana `CLUB_FORM` de
forma, como mucho `CLUB_FORM_DAILY` al día (`after_club_form`): entrenar en
grupo anima, pero no sustituye a entrenar. No mueve dinero.

**El torneo del domingo.** Cada domingo desde `TOURNAMENT_HOUR` (hora canaria)
el cog celebra un torneo por servidor (`week_key` dice de qué semana es y
`due` si ya toca). Entran los `TOURNAMENT_SIZE` gallos con más poder (y, a
igualdad, más forma) de los que han peleado esa semana y no están lesionados
(`entrants`); con menos de `TOURNAMENT_MIN` no hay torneo. Eliminatorias a un
combate: gana con la probabilidad de un duelo (`gallos.duel_chance`) con el
poder de cada uno más un empujón de la forma (`effective_power`). No se pinta ni
se apuesta: el premio es el título, los logros y salir en la cartelera. La
gallera de la semana es la que más combates del torneo ha ganado entre sus
miembros (`best_club`).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from bot.services.gallos import (
    FORM_MAX,
    Corner,
    RoosterRecord,
    draw_winner,
    duel_chance,
    form_now,
    injured,
    with_form,
)
from bot.services.levels import TIMEZONE

#: Miembros de una gallera, largo de su nombre y la forma que se reparte al entrenar.
CLUB_MAX = 5
CLUB_NAME_MAX = 32
CLUB_FORM = 1
CLUB_FORM_DAILY = 6

#: Gallos del torneo, mínimo para celebrarlo y hora del domingo a la que se juega.
TOURNAMENT_SIZE = 8
TOURNAMENT_MIN = 4
TOURNAMENT_HOUR = 21
#: Lo que pesa la forma en el poder de un combate del torneo (forma 100 → +10 de poder).
FORM_WEIGHT = 0.1


# -- Galleras ---------------------------------------------------------------------------


class ClubError(ValueError):
    """No se puede hacer eso con la gallera; el mensaje se enseña tal cual."""


@dataclass(frozen=True, slots=True)
class Club:
    """Una gallera: su nombre, quién la fundó y sus miembros (por orden de llegada)."""

    id: int
    name: str
    owner_id: int
    members: tuple[int, ...]

    @property
    def full(self) -> bool:
        return len(self.members) >= CLUB_MAX


def club_form_today(record: RoosterRecord, day: date) -> int:
    """Forma que ya ha recibido hoy el gallo de su gallera."""
    return record.club_form if record.club_day == day else 0


def after_club_form(record: RoosterRecord, *, now: float, day: date) -> RoosterRecord:
    """La ficha de un compañero de gallera cuando otro acaba un entrenamiento.

    Suma `CLUB_FORM` sin pasar de `CLUB_FORM_DAILY` al día (si ya llegó, no cambia).
    """
    got = club_form_today(record, day)
    gain = min(CLUB_FORM, CLUB_FORM_DAILY - got)
    if gain <= 0:
        return record
    after = with_form(record, form_now(record, now) + gain, now)
    return replace(after, club_day=day, club_form=got + gain)


def clean_club_name(text: str) -> str:
    """Valida el nombre de una gallera: sin formato ni menciones, como mucho `CLUB_NAME_MAX`.

    Raises:
        ValueError: Con un mensaje mostrable si queda vacío o es demasiado largo.
    """
    name = " ".join("".join(c for c in text if c not in "`*_~|<>@#\\").split())
    if not name:
        raise ValueError("Ponle a la gallera un nombre con letras de verdad.")
    if len(name) > CLUB_NAME_MAX:
        raise ValueError(f"Como mucho {CLUB_NAME_MAX} caracteres.")
    return name


# -- El torneo ----------------------------------------------------------------------------


def week_key(now: float) -> str:
    """La semana del torneo de `now` (lunes de la semana canaria, `AAAA-MM-DD`)."""
    day = datetime.fromtimestamp(now, TIMEZONE).date()
    return (day - timedelta(days=day.weekday())).isoformat()


def due(now: float) -> bool:
    """Si ya toca el torneo de esta semana (domingo desde `TOURNAMENT_HOUR`)."""
    moment = datetime.fromtimestamp(now, TIMEZONE)
    return moment.weekday() == 6 and moment.hour >= TOURNAMENT_HOUR


@dataclass(frozen=True, slots=True)
class Entrant:
    """Un gallo del torneo."""

    user_id: int
    name: str
    power: float
    form: float
    club: int | None = None


def effective_power(entrant: Entrant) -> float:
    """Poder con el que pelea en el torneo: el suyo más el empujón de la forma."""
    return entrant.power + FORM_WEIGHT * min(FORM_MAX, entrant.form)


def entrants(
    records: list[tuple[int, RoosterRecord]],
    *,
    now: float,
    clubs: dict[int, int] | None = None,
) -> list[Entrant]:
    """Los gallos que entran al torneo, del más fuerte al más flojo (los cabezas de serie).

    Han de haber peleado en los últimos 7 días y no estar lesionados.
    """
    clubs = clubs or {}
    since = datetime.fromtimestamp(now, TIMEZONE).date() - timedelta(days=6)
    pool = [
        Entrant(user, record.name, record.power, form_now(record, now), clubs.get(user))
        for user, record in records
        if record.last_day is not None and record.last_day >= since and not injured(record, now)
    ]
    pool.sort(key=lambda e: (effective_power(e), e.form, -e.user_id), reverse=True)
    return pool[:TOURNAMENT_SIZE]


@dataclass(frozen=True, slots=True)
class Match:
    """Un combate del torneo."""

    red: Entrant
    blue: Entrant
    winner: Entrant


@dataclass(frozen=True, slots=True)
class Bracket:
    """El torneo jugado: los combates por ronda y el campeón."""

    rounds: tuple[tuple[Match, ...], ...]

    @property
    def champion(self) -> Entrant:
        return self.rounds[-1][0].winner

    @property
    def finalist(self) -> Entrant:
        final = self.rounds[-1][0]
        return final.blue if final.winner is final.red else final.red

    def wins(self, user_id: int) -> int:
        """Combates que ha ganado ese gallo en el torneo."""
        return sum(1 for rnd in self.rounds for m in rnd if m.winner.user_id == user_id)


def play(field: list[Entrant], rng: random.Random) -> Bracket | None:
    """Juega el torneo con los gallos de `entrants` (ya ordenados por cabeza de serie).

    Con menos de `TOURNAMENT_MIN` no hay torneo (`None`). Si no son una potencia
    de dos, solo juegan los primeros que lo sean (8 o 4). Cruces de cabeza de
    serie: el primero contra el último, el segundo contra el penúltimo…
    """
    if len(field) < TOURNAMENT_MIN:
        return None
    size = 8 if len(field) >= 8 else 4
    alive = field[:size]
    rounds: list[tuple[Match, ...]] = []
    while len(alive) > 1:
        half = len(alive) // 2
        matches = []
        for red, blue in zip(alive[:half], reversed(alive[half:]), strict=True):
            chance = duel_chance(effective_power(red), effective_power(blue))
            winner = red if draw_winner(chance, rng) is Corner.RED else blue
            matches.append(Match(red, blue, winner))
        rounds.append(tuple(matches))
        alive = [m.winner for m in matches]
    return Bracket(tuple(rounds))


def best_club(bracket: Bracket) -> tuple[int, int] | None:
    """La gallera con más combates ganados en el torneo: `(id, victorias)`, o `None`."""
    totals: dict[int, int] = {}
    for rnd in bracket.rounds:
        for match in rnd:
            if match.winner.club is not None:
                totals[match.winner.club] = totals.get(match.winner.club, 0) + 1
    if not totals:
        return None
    club = max(totals, key=lambda c: (totals[c], -c))
    return club, totals[club]
