"""Reglas de las tragaperras de la plaza del Jovaniverso (la Activity).

La plaza tiene una fila de `MACHINES` máquinas clásicas, numeradas de 1 en adelante
(la 0 es la de Discord, `tragas`). Cada una tiene su calor y su barra de bonus, que
guarda el cog de la tragaperras (`Slots.play(machine=...)`), y sus giros gratis
pendientes, que viven aquí, en memoria, como en la máquina de Discord viven en su
vista.

La página no decide nada: pide cada tirada y el servidor comprueba con `Seats`:

- que la máquina existe y que el miembro no juega a más de `MAX_AT_ONCE` a la vez
  (una máquina cuenta como suya mientras ha tirado hace menos de `ACTIVE_SECONDS`);
- que no tira más rápido que `MIN_GAP_SECONDS` en la misma máquina (lo que dura la
  tirada más corta), así que el Auto no puede ir más deprisa que a mano;
- el ▶️ Auto, con las reglas de `bot.services.autoplay`: `AUTOPLAY_MAX` tiradas,
  para con un premio gordo y al perder `LOSS_LIMIT_MULTIPLIER` veces la apuesta.
  Lo lleva la página (pide la siguiente cuando acaba de enseñar la anterior), y si
  se cierra la Activity simplemente deja de pedir: nada gira sin nadie delante.

Todo con tope: como mucho `MACHINES` asientos por miembro, y los que llevan
`IDLE_SECONDS` sin tirar se olvidan (`sweep`). Los giros gratis olvidados se pierden,
igual que los de una máquina de Discord que caduca sin jugarlos.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.services.achievements import UNIVERSO_SLOT_MACHINES
from bot.services.autoplay import AUTOPLAY_MAX, LOSS_LIMIT_MULTIPLIER, StopReason, stop_text
from bot.services.slots import BONUS_FREE_SPINS, FREE_SPINS

#: Máquinas de la fila de la plaza (de la 1 a la `MACHINES`); sus logros las cuentan.
MACHINES = UNIVERSO_SLOT_MACHINES
#: Máquinas a la vez por miembro (una a mano y dos en Auto, o tres en Auto).
MAX_AT_ONCE = 3
#: Una máquina sigue siendo «tuya» mientras has tirado en ella hace menos de esto.
ACTIVE_SECONDS = 20.0
#: Segundos mínimos entre dos tiradas de la misma máquina: lo que dura la más corta.
MIN_GAP_SECONDS = 1.4
#: Un asiento sin tirar en este tiempo se olvida (con sus giros gratis).
IDLE_SECONDS = 600.0


class SeatError(Exception):
    """Una tirada que no se puede hacer; el texto se enseña tal cual en la página."""


@dataclass(slots=True)
class Auto:
    """Un ▶️ Auto en marcha en una máquina."""

    stake: int
    spins: int = 0
    net: int = 0

    @property
    def loss_limit(self) -> int:
        """Lo que se puede perder en neto antes de parar."""
        return self.stake * LOSS_LIMIT_MULTIPLIER


@dataclass(slots=True)
class Seat:
    """Lo que el servidor recuerda de una máquina de un miembro."""

    last_at: float = float("-inf")
    free_spins: int = 0
    free_stake: int = 0
    session_spins: int = 0
    auto: Auto | None = None


@dataclass(frozen=True, slots=True)
class Turn:
    """Cómo se juega la tirada que se pide: con su apuesta y si es gratis."""

    stake: int
    free: bool
    session_spins: int
    auto: bool
    #: La apuesta elegida (la del Auto si hay uno), aunque esta tirada sea gratis.
    bet: int


class Seats:
    """Los asientos de todos los miembros en las máquinas de la plaza."""

    def __init__(self) -> None:
        self._seats: dict[tuple[int, int, int], Seat] = {}

    def seat(self, guild_id: int, user_id: int, machine: int) -> Seat:
        """El asiento de un miembro en una máquina (uno vacío si nunca se ha sentado)."""
        return self._seats.get((guild_id, user_id, machine)) or Seat()

    def begin(
        self,
        guild_id: int,
        user_id: int,
        machine: int,
        *,
        stake: int,
        auto: bool,
        now: float,
    ) -> Turn:
        """Comprueba una tirada pedida, reserva la máquina y dice cómo se juega.

        Reservar (apuntar la hora ya) hace que dos peticiones a la vez a la misma
        máquina no pasen las dos. Los giros gratis pendientes van antes que la
        apuesta, como en Discord.

        Args:
            stake: La apuesta que pide la página (la del Auto si ya hay uno en marcha).
            auto: Si la tirada es del ▶️ Auto (la primera lo arranca).

        Raises:
            SeatError: Máquina que no existe, demasiadas a la vez o demasiado rápido.
        """
        if not 1 <= machine <= MACHINES:
            raise SeatError("Esa máquina no existe.")
        if stake < 1:
            raise SeatError("La apuesta mínima es 1 Y$.")
        seat = self._seats.get((guild_id, user_id, machine))
        if seat is not None and now - seat.last_at < MIN_GAP_SECONDS:
            raise SeatError("La máquina aún está girando.")
        busy = self.active(guild_id, user_id, now)
        if machine not in busy and len(busy) >= MAX_AT_ONCE:
            raise SeatError(f"Como mucho {MAX_AT_ONCE} máquinas a la vez.")
        seat = self._seats.setdefault((guild_id, user_id, machine), Seat())
        seat.last_at = now
        if auto and seat.auto is not None:
            stake = seat.auto.stake
        free = seat.free_spins > 0
        return Turn(
            stake=seat.free_stake if free else stake,
            free=free,
            session_spins=seat.session_spins + 1,
            auto=auto,
            bet=stake,
        )

    def finish(
        self,
        guild_id: int,
        user_id: int,
        machine: int,
        turn: Turn,
        *,
        now: float,
        net: int,
        big_prize: bool,
        triggers_free_spins: bool,
        bonus_stake: int,
    ) -> tuple[StopReason, Auto] | None:
        """Apunta una tirada ya jugada y cobrada; si el Auto para, por qué y cómo acabó.

        Args:
            net: Ganado menos pagado en la tirada.
            big_prize: Si es un premio gordo (`SlotsPlay.big_prize`): el Auto para.
            triggers_free_spins: Si han salido 🎟️ 🎟️ 🎟️.
            bonus_stake: La apuesta de los giros de la barra de bonus llena, o 0.
        """
        key = (guild_id, user_id, machine)
        seat = self._seats.setdefault(key, Seat())
        seat.last_at = now
        seat.session_spins = turn.session_spins
        if turn.free:
            seat.free_spins -= 1
        # Todos los pendientes se juegan a la menor apuesta: no se llena barato y se cobra caro.
        grants = []
        if triggers_free_spins:
            grants.append((FREE_SPINS, turn.stake))
        if bonus_stake:
            grants.append((BONUS_FREE_SPINS, bonus_stake))
        for count, stake in grants:
            seat.free_stake = min(seat.free_stake, stake) if seat.free_spins else stake
            seat.free_spins += count
        if not turn.auto:
            seat.auto = None
            return None
        auto = seat.auto or Auto(stake=turn.bet)
        seat.auto = auto
        auto.spins += 1
        auto.net += net
        reason = None
        if big_prize:
            reason = StopReason.BIG_PRIZE
        elif -auto.net >= auto.loss_limit:
            reason = StopReason.LOSS_LIMIT
        elif auto.spins >= AUTOPLAY_MAX:
            reason = StopReason.MAX_SPINS
        if reason is None:
            return None
        seat.auto = None
        return reason, auto

    def stop(self, guild_id: int, user_id: int, machine: int) -> Auto | None:
        """⏹️ Parar a mano: quita el Auto de la máquina y lo devuelve (`None` si no había)."""
        seat = self._seats.get((guild_id, user_id, machine))
        if seat is None:
            return None
        auto, seat.auto = seat.auto, None
        return auto

    def active(self, guild_id: int, user_id: int, now: float) -> set[int]:
        """Máquinas en las que el miembro ha tirado hace menos de `ACTIVE_SECONDS`."""
        return {
            machine
            for (g, u, machine), seat in self._seats.items()
            if g == guild_id and u == user_id and now - seat.last_at < ACTIVE_SECONDS
        }

    def sweep(self, now: float) -> None:
        """Olvida los asientos que llevan `IDLE_SECONDS` sin tirar."""
        for key in [k for k, s in self._seats.items() if now - s.last_at >= IDLE_SECONDS]:
            del self._seats[key]

    def __len__(self) -> int:
        return len(self._seats)


def auto_stop_text(reason: StopReason, stake: int) -> str:
    """El motivo de parada del Auto, igual que en Discord."""
    return stop_text(reason, loss_limit=stake * LOSS_LIMIT_MULTIPLIER, max_spins=AUTOPLAY_MAX)
