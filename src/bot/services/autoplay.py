"""Autoplay: encadenar jugadas de un juego del casino, con sus condiciones de parada.

Este módulo no sabe de Discord ni de ningún juego en concreto. Pone el bucle, el
contador de pérdidas, el tope de jugadas, el intervalo mínimo entre ediciones, el
flag de «Parar» y el texto del motivo de parada. Cada juego enchufa solo lo suyo:
una función `step` que juega **una** ronda completa (cobrar, animar, enseñar,
apuntar logros) y devuelve su resultado.

Cómo lo enchufa un juego (la tragaperras es el ejemplo, `bot.cogs.slots`; el
pachinko lo hace igual en `bot.cogs.pachinko` y pasa `unit="tandas"` a los textos
de `stop_text` y `summary`):

1. Al pulsar ▶️ Auto, el botón crea una `AutoplaySession(stake=apuesta)` y llama
   a `session.start(step, on_finish=..., name="slots-autoplay-<id>")`. Eso
   devuelve ya una tarea con nombre; el clic puede terminar de contestar.
2. `step(number)` hace lo mismo que el botón de jugar una vez, pero editando el
   mensaje **sin el token de la interacción** (caduca a los 15 minutos): con
   `Message.edit` o `PartialMessage.edit`. Devuelve un `SpinResult`:
   `net` (lo ganado menos lo apostado en esa ronda), `big_prize=True` si fue un
   premio gordo que conviene que se vea, y `edited_at` (`time.monotonic()` de su
   última edición) para que el bucle espere solo lo que falte del intervalo. Si
   no puede jugar (saldo, banca), lanza `AutoplayStop(StopReason.NO_FUNDS)` o
   `BANK_LIMIT` **antes** de cobrar nada.
3. El botón se convierte en ⏹️ Parar mientras `session.running`. Pulsarlo solo
   llama a `session.request_stop()` (memoria, sin base de datos) y contesta con
   `interaction.response.edit_message`: la ronda en curso termina entera (nunca
   se corta una animación ni un cobro) y el bucle sale después.
4. `on_finish(outcome)` se ejecuta al acabar, con las jugadas, el neto y el
   motivo (`outcome.summary()` da el titular listo para el mensaje). Ahí el juego
   devuelve los botones a su sitio y apunta los logros de sesión.
5. Al caducar la vista o descargar el cog, `await session.close()` pide parar,
   espera a la ronda en curso y, si tarda demasiado, cancela la tarea.

Condiciones de parada, por orden de prioridad cuando coinciden en la misma ronda:
premio gordo, pérdidas netas de la sesión iguales o mayores que
`LOSS_LIMIT_MULTIPLIER` veces la apuesta, tope de `AUTOPLAY_MAX` rondas y Parar a
mano. Fuera del bucle, el juego avisa de saldo insuficiente y del límite de la
banca con `AutoplayStop`. Entre dos rondas se deja como mínimo `AUTOPLAY_MIN_GAP`
segundos desde la última edición, porque Discord admite unas 5 ediciones cada
5 segundos por canal.

El dinero no se toca aquí: lo mueve el `step` del juego con su servicio de
siempre, así que el tratamiento fiscal, los logros y la Renta son los del juego.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from bot.services.economy import format_amount

logger = logging.getLogger(__name__)

#: Rondas como máximo en una sesión de autoplay.
AUTOPLAY_MAX = 25
#: Segundos mínimos entre dos ediciones del mensaje (Discord: 5 cada 5 s por canal).
AUTOPLAY_MIN_GAP = 1.2
#: Se para al perder, en neto, esta cantidad de veces la apuesta.
LOSS_LIMIT_MULTIPLIER = 10
#: Segundos que `close` espera a la ronda en curso antes de cancelar la tarea.
CLOSE_GRACE_SECONDS = 8.0


class StopReason(StrEnum):
    """Por qué terminó una sesión de autoplay."""

    MAX_SPINS = "max"
    NO_FUNDS = "funds"
    BANK_LIMIT = "bank"
    BIG_PRIZE = "prize"
    LOSS_LIMIT = "loss"
    MANUAL = "manual"
    CLOSED = "closed"
    ERROR = "error"


class AutoplayStop(Exception):  # noqa: N818 - no es un error: es una forma de parar
    """Lo lanza el `step` de un juego para terminar la sesión sin haber jugado la ronda."""

    def __init__(self, reason: StopReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SpinResult:
    """Lo que devuelve una ronda al bucle.

    Attributes:
        net: Ganado menos apostado en la ronda (negativo si se perdió).
        big_prize: Si fue un premio gordo: la sesión para para que se vea.
        edited_at: `time.monotonic()` de la última edición del mensaje; `None`
            si no se editó (cuenta desde que la ronda devuelve el resultado).
        stop: Motivo para parar después de contar esta ronda (por ejemplo, el
            mensaje ya no se puede editar), o `None`.
    """

    net: int
    big_prize: bool = False
    edited_at: float | None = None
    stop: StopReason | None = None


@dataclass(frozen=True, slots=True)
class AutoplayOutcome:
    """Resultado de una sesión terminada."""

    spins: int
    net: int
    reason: StopReason
    loss_limit: int = 0
    max_spins: int = AUTOPLAY_MAX

    def reason_text(self, *, unit: str = "tiradas") -> str:
        """El motivo de parada, en una frase para el usuario (`unit`: ver `stop_text`)."""
        return stop_text(
            self.reason, loss_limit=self.loss_limit, max_spins=self.max_spins, unit=unit
        )

    def summary(self, *, icon: str = "▶️", unit: str = "tiradas") -> str:
        """Titular y motivo: «▶️ Auto: 17 tiradas · -1.230 Y$» y debajo por qué paró.

        Args:
            unit: Las rondas del juego, en plural (`"tandas"` en el pachinko).
        """
        sign = "+" if self.net > 0 else "-" if self.net < 0 else "±"
        return (
            f"### {icon} Auto: {self.spins} {unit} · {sign}{format_amount(abs(self.net))}\n"
            f"-# {self.reason_text(unit=unit)}"
        )


def stop_text(
    reason: StopReason,
    *,
    loss_limit: int = 0,
    max_spins: int = AUTOPLAY_MAX,
    unit: str = "tiradas",
) -> str:
    """Frase del motivo de parada (con el tono del bot).

    Args:
        unit: Cómo llama el juego a sus rondas, en plural y acabado en «s»
            (`"tiradas"` en la tragaperras, `"tandas"` en el pachinko,
            `"rascas"` en la lotería).
    """
    texts = {
        StopReason.MAX_SPINS: f"Parado: tope de {max_spins} {unit} por sesión.",
        StopReason.NO_FUNDS: f"Parado: no te llega para más {unit}.",
        StopReason.BANK_LIMIT: "Parado: la banca no puede pagar tanto.",
        StopReason.BIG_PRIZE: "Parado: ¡premio gordo! Disfrútalo antes de seguir.",
        StopReason.LOSS_LIMIT: (
            f"Parado: has perdido {format_amount(loss_limit)} "
            f"({LOSS_LIMIT_MULTIPLIER} veces la apuesta). Techo de gasto."
        ),
        StopReason.MANUAL: "Parado a mano.",
        StopReason.CLOSED: "Parado: la máquina se ha cerrado.",
        StopReason.ERROR: "Parado: algo ha fallado. Tu saldo está a salvo.",
    }
    return texts[reason]


#: Una ronda del juego: recibe su número (desde 1) y devuelve su resultado.
Step = Callable[[int], Awaitable[SpinResult]]
#: Se llama al terminar, con el resultado de la sesión.
Finish = Callable[[AutoplayOutcome], Awaitable[None]]


class AutoplaySession:
    """Una sesión de autoplay de un jugador: el bucle y su estado.

    Args:
        stake: Apuesta de la sesión. El límite de pérdidas es
            `LOSS_LIMIT_MULTIPLIER` veces esta cifra.
        max_spins: Tope de rondas.
        min_gap: Segundos mínimos entre la última edición de una ronda y la
            siguiente (y antes del mensaje final).
    """

    def __init__(
        self,
        *,
        stake: int,
        max_spins: int = AUTOPLAY_MAX,
        min_gap: float = AUTOPLAY_MIN_GAP,
    ) -> None:
        self.stake = stake
        self.max_spins = max_spins
        self.min_gap = min_gap
        self.loss_limit = stake * LOSS_LIMIT_MULTIPLIER
        self.spins = 0
        self.net = 0
        #: Se pone a `True` cuando el mensaje ya enseña el botón de Parar.
        self.armed = False
        self.task: asyncio.Task[None] | None = None
        self._stop: StopReason | None = None
        self._last_edit = time.monotonic()

    # -- Estado ---------------------------------------------------------------------

    @property
    def running(self) -> bool:
        """Si la tarea del bucle sigue viva."""
        return self.task is not None and not self.task.done()

    @property
    def stop_requested(self) -> bool:
        """Si alguien ha pedido parar (el bucle sale al acabar la ronda en curso)."""
        return self._stop is not None

    def request_stop(self, reason: StopReason = StopReason.MANUAL) -> None:
        """Pide parar. Solo marca un flag en memoria: no espera ni toca nada."""
        if self._stop is None:
            self._stop = reason

    # -- Bucle ----------------------------------------------------------------------

    def start(self, step: Step, *, on_finish: Finish, name: str) -> asyncio.Task[None]:
        """Lanza el bucle en una tarea con nombre, cancelable con `close`.

        Raises:
            RuntimeError: Si la sesión ya está en marcha.
        """
        if self.task is not None:
            raise RuntimeError("La sesión de autoplay ya se ha lanzado")
        self.task = asyncio.create_task(self._main(step, on_finish), name=name)
        return self.task

    async def _main(self, step: Step, on_finish: Finish) -> None:
        outcome = await self.run(step)
        # El mensaje final también es una edición: respeta el mismo intervalo.
        if outcome.spins and outcome.reason is not StopReason.CLOSED:
            await self._wait_gap()
        try:
            await on_finish(outcome)
        except Exception:
            logger.exception("Falló el cierre de una sesión de autoplay")

    async def run(self, step: Step) -> AutoplayOutcome:
        """Juega rondas con `step` hasta que se cumpla una condición de parada."""
        reason = await self._loop(step)
        return AutoplayOutcome(
            spins=self.spins,
            net=self.net,
            reason=reason,
            loss_limit=self.loss_limit,
            max_spins=self.max_spins,
        )

    async def _loop(self, step: Step) -> StopReason:
        while True:
            if self._stop is not None:
                return self._stop
            try:
                result = await step(self.spins + 1)
            except AutoplayStop as stop:
                return stop.reason
            except Exception:
                logger.exception("Falló una ronda del autoplay")
                return StopReason.ERROR
            self.spins += 1
            self.net += result.net
            self._last_edit = result.edited_at if result.edited_at is not None else time.monotonic()
            if result.big_prize:
                return StopReason.BIG_PRIZE
            if result.stop is not None:
                return result.stop
            if self.net <= -self.loss_limit:
                return StopReason.LOSS_LIMIT
            if self.spins >= self.max_spins:
                return StopReason.MAX_SPINS
            if self._stop is not None:
                return self._stop
            await self._wait_gap()

    async def _wait_gap(self) -> None:
        """Espera lo que falte para que pasen `min_gap` segundos desde la última edición."""
        remaining = self.min_gap - (time.monotonic() - self._last_edit)
        if remaining > 0:
            await asyncio.sleep(remaining)

    # -- Cierre ---------------------------------------------------------------------

    async def close(self, *, grace: float | None = None) -> None:
        """Para la sesión y espera a que acabe; si tarda más de `grace`, la cancela.

        Lo usan el cierre de la vista y la descarga del cog. Nunca lanza.

        Args:
            grace: Segundos de espera a la ronda en curso (por defecto,
                `CLOSE_GRACE_SECONDS`).
        """
        if grace is None:
            grace = CLOSE_GRACE_SECONDS
        self.request_stop(StopReason.CLOSED)
        task = self.task
        if task is None or task.done() or task is asyncio.current_task():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), grace)
        except TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        except asyncio.CancelledError:
            task.cancel()
            raise
        except Exception:
            logger.exception("La sesión de autoplay acabó con error al cerrarla")
