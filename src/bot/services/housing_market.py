"""Casos de uso de `pisos`: comprar, vender, la renta, el inquilino, desahuciar y cerrar los días.

Junta las reglas (`bot.services.housing`), la persistencia
(`bot.repositories.housing`) y el dinero (`EconomyService.housing`). No sabe nada de
Discord: lo usa `bot.cogs.pisos`.
"""

from __future__ import annotations

import logging
import random
import secrets
import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, timedelta

from bot.repositories.housing import (
    AdvanceDesk,
    AuctionRun,
    Bid,
    Built,
    DayRun,
    Decision,
    Eviction,
    HousingError,
    HousingRepository,
    KeysDeal,
    LoanClosed,
    LoanDesk,
    Market,
    MemberKeys,
    MemberSale,
    News,
    Purchase,
    ReformDone,
    RemortgageDesk,
    Sale,
    SignedAdvance,
    SignedLoan,
    SignedRemortgage,
    Signing,
    SplitDone,
    TenantDeal,
    VoteResult,
    VpoDeal,
    guarantor_problem,
)
from bot.repositories.housing_junta import Vote
from bot.repositories.housing_trade import Auction
from bot.services import credit
from bot.services import housing as rules
from bot.services import loans as lending
from bot.services.economy import EconomyService, InsufficientFundsError
from bot.services.housing import Lease
from bot.services.levels import local_day

#: Días cerrados que se recuperan como mucho si el bot ha estado apagado.
CATCH_UP_DAYS = 7


@dataclass(frozen=True, slots=True)
class Trade:
    """Una compra o venta hecha, con el saldo con que queda quien la hace."""

    deal: Purchase | Sale
    balance: int


logger = logging.getLogger(__name__)


class HousingService:
    """`pisos` para un bot.

    Args:
        repository: Persistencia de `pisos`.
        economy: El único camino del dinero.
        clock: Segundos epoch; inyectable en pruebas.
        rng: Azar de los inquilinos y los imprevistos; inyectable en pruebas.
    """

    def __init__(
        self,
        repository: HousingRepository,
        economy: EconomyService,
        *,
        clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
    ) -> None:
        self.repository = repository
        self.economy = economy
        self._clock = clock
        # `secrets` usa el azar del sistema: nadie puede adivinar quién dejará de pagar.
        self.rng = rng or secrets.SystemRandom()

    def today(self) -> date:
        """Hoy en hora canaria."""
        return local_day(self._clock())

    def now(self) -> float:
        """Ahora (epoch), con el reloj del servicio (inyectable en pruebas)."""
        return self._clock()

    async def market(self, guild_id: int) -> Market:
        """Todo el mercado del servidor."""
        return await self.repository.market(guild_id)

    async def news(self, guild_id: int, listings: tuple[str, ...], limit: int = 8) -> list[News]:
        """Lo último que ha pasado en unos inmuebles."""
        return await self.repository.news(guild_id, listings, limit)

    async def income(self, guild_id: int, user_id: int) -> int:
        """Lo que el banco cree que gana alguien al día (nóminas, negocio y 70 % de las rentas)."""
        work = await self.economy.work_net_days(guild_id, user_id, rules.INCOME_DAYS)
        rent = await self.repository.income(
            guild_id, user_id, self.today() - timedelta(days=rules.INCOME_DAYS)
        )
        return rules.daily_income(work, rent.rent)

    async def work_income(self, guild_id: int, user_id: int) -> int:
        """Neto medio de nómina y negocio al día de los últimos 30 días (lo miran VPO y bono)."""
        work = await self.economy.work_net_days(guild_id, user_id, rules.INCOME_DAYS)
        return work // rules.INCOME_DAYS

    async def lender_view(self, guild_id: int, user_id: int) -> tuple[int, bool]:
        """`(lo que el banco cree que ingresa al día, si cobra nómina, pensión o negocio)`."""
        work = await self.economy.work_net_days(guild_id, user_id, rules.INCOME_DAYS)
        rent = await self.repository.income(
            guild_id, user_id, self.today() - timedelta(days=rules.INCOME_DAYS)
        )
        return rules.daily_income(work, rent.rent), work > 0

    async def loan_desk(self, guild_id: int, user_id: int) -> tuple[LoanDesk, int]:
        """Lo que ve el banco de un miembro para un préstamo y lo que ingresa al día."""
        income, payroll = await self.lender_view(guild_id, user_id)
        desk = await self.repository.loan_desk(guild_id, user_id, payroll=payroll)
        return desk, income

    async def take_loan(
        self,
        guild_id: int,
        user_id: int,
        *,
        principal: int,
        months: int,
        insured: bool,
        rate: float,
        guarantor: int | None = None,
    ) -> tuple[SignedLoan, int]:
        """Firma un préstamo personal (ver `EconomyService.housing`), con avalista si lo hay.

        Raises:
            HousingError: Si debe a Hacienda o el banco no presta (ver `take_loan` del
                repositorio).
        """
        if await self.economy.tax_debt(guild_id, user_id) > 0:
            # Lo prestado se embargaría al entrar: el banco no presta a quien debe a Hacienda.
            raise HousingError(
                "Debes dinero a Hacienda y tendrías la cuenta embargada: el banco no presta. "
                "Liquida la deuda en `renta`."
            )
        desk, income = await self.loan_desk(guild_id, user_id)
        guarantor_income = await self._guarantor_income(guild_id, guarantor)
        now = self._clock()
        run = self.repository.take_loan(
            guild_id, user_id, principal=principal, months=months, insured=insured, rate=rate,
            income=income, payroll=desk.profile.payroll, day=local_day(now), now=now,
            guarantor=guarantor, guarantor_income=guarantor_income,
        )  # fmt: skip
        signed, balances = await self.economy.housing(guild_id, run)
        return signed, balances[user_id]

    async def repay_loan(self, guild_id: int, user_id: int, loan_id: int) -> tuple[LoanClosed, int]:
        """Amortiza un préstamo entero con su compensación.

        Raises:
            HousingError: Si no es suyo o ya está cerrado.
            InsufficientFundsError: Si no le llega. No se mueve nada.
        """
        run = self.repository.repay_loan(guild_id, user_id, loan_id, self._clock())
        closed, balances = await self.economy.housing(guild_id, run)
        return closed, balances[user_id]

    async def desist_loan(
        self, guild_id: int, user_id: int, loan_id: int
    ) -> tuple[LoanClosed, int]:
        """Desiste de un préstamo antes de la primera cuota.

        Raises:
            HousingError: Si no es suyo, ya está cerrado o ya se cobró una cuota.
            InsufficientFundsError: Si ya no tiene lo recibido. No se mueve nada.
        """
        run = self.repository.desist_loan(guild_id, user_id, loan_id, self._clock())
        closed, balances = await self.economy.housing(guild_id, run)
        return closed, balances[user_id]

    async def finance(
        self,
        guild_id: int,
        user_id: int,
        purchase: credit.Purchase,
        *,
        plan: credit.Plan,
        months: int,
        insured: bool = False,
        grace: bool = False,
        rate: float = 0.0,
    ) -> tuple[SignedLoan, int]:
        """Financia una compra (💳 Financiar): al monedero llega lo que cuesta, para pagarla
        justo después con el camino de siempre. Si esa compra falla, `unfinance`.

        Raises:
            HousingError: Si debe a Hacienda o el banco no financia (ver `finance` del
                repositorio).
        """
        if await self.economy.tax_debt(guild_id, user_id) > 0:
            # Lo prestado se embargaría al entrar, como en un préstamo personal.
            raise HousingError(
                "Debes dinero a Hacienda y tendrías la cuenta embargada: el banco no financia "
                "nada. Liquida la deuda en `renta`."
            )
        income, payroll = await self.lender_view(guild_id, user_id)
        now = self._clock()
        run = self.repository.finance(
            guild_id, user_id, purchase, plan=plan, months=months, insured=insured,
            grace=grace, rate=rate, income=income, payroll=payroll, day=local_day(now), now=now,
        )  # fmt: skip
        signed, balances = await self.economy.housing(guild_id, run)
        # El IPC no se recalcula aquí: la compra va justo después y tiene que costar lo que
        # se ha financiado. Lo recalcula quien compra al terminar (`refresh_ipc`).
        return signed, balances[user_id]

    async def unfinance(self, guild_id: int, user_id: int, loan_id: int) -> None:
        """Deshace una financiación cuya compra no salió: devuelve lo prestado y la comisión.

        Nunca lanza: si ya no puede devolverlo (lo gastó entretanto), la financiación sigue
        viva y se paga como cualquier otra, y queda en el registro.
        """
        try:
            await self.desist_loan(guild_id, user_id, loan_id)
        except (HousingError, InsufficientFundsError, OSError, sqlite3.Error):
            logger.exception("No se pudo deshacer la financiación #%s de %s", loan_id, user_id)
        await self.refresh_ipc(guild_id)

    async def refresh_ipc(self, guild_id: int) -> int:
        """Recalcula el IPC del crédito de un servidor (`credit.set_ipc`) y lo devuelve.

        Solo en momentos fijos (cada 10 minutos con el cierre de `pisos` y al terminar o
        deshacer una compra financiada): así un precio enseñado sigue valiendo al pagar.
        """
        alive, money = await self.repository.credit_alive(guild_id)
        pct = credit.ipc_pct(alive, money)
        credit.set_ipc(guild_id, pct)
        return pct

    async def _guarantor_income(self, guild_id: int, guarantor: int | None) -> int:
        """Lo que ingresa al día un avalista (0 sin avalista).

        Raises:
            HousingError: Si el avalista debe a Hacienda: el banco no lo acepta.
        """
        if guarantor is None:
            return 0
        if await self.economy.tax_debt(guild_id, guarantor) > 0:
            raise HousingError(
                "Tu avalista debe dinero a Hacienda y tiene la cuenta embargada: el banco no "
                "lo acepta."
            )
        return await self.income(guild_id, guarantor)

    async def guarantor_room(
        self, guild_id: int, user_id: int, guarantor: int
    ) -> tuple[int, str | None]:
        """Lo que le cabe al mes a un posible avalista en su 35 % y por qué no vale, si no vale."""
        market = await self.repository.market(guild_id)
        problem = guarantor_problem(market, user_id, guarantor)
        if problem is None and await self.economy.tax_debt(guild_id, guarantor) > 0:
            problem = "Debe dinero a Hacienda: el banco no lo acepta de avalista."
        income = await self.income(guild_id, guarantor)
        return lending.room_for(income, market.installments_of(guarantor)), problem

    async def remortgage_desk(self, guild_id: int, user_id: int) -> tuple[RemortgageDesk, int]:
        """Lo que ve el banco de alguien para rehipotecar y lo que ingresa al día."""
        desk = await self.repository.remortgage_desk(guild_id, user_id)
        return desk, await self.income(guild_id, user_id)

    async def remortgage(
        self,
        guild_id: int,
        user_id: int,
        key: str,
        *,
        principal: int,
        months: int,
        rate: float,
        guarantor: int | None = None,
    ) -> tuple[SignedRemortgage, int]:
        """Firma una rehipoteca (ver `EconomyService.housing` y `docs/garantias.md`).

        Raises:
            HousingError: Si debe a Hacienda o el banco no presta (ver `remortgage` del
                repositorio).
        """
        if await self.economy.tax_debt(guild_id, user_id) > 0:
            raise HousingError(
                "Debes dinero a Hacienda y tendrías la cuenta embargada: el banco no presta. "
                "Liquida la deuda en `renta`."
            )
        income = await self.income(guild_id, user_id)
        guarantor_income = await self._guarantor_income(guild_id, guarantor)
        now = self._clock()
        run = self.repository.remortgage(
            guild_id, user_id, key, principal=principal, months=months, rate=rate,
            income=income, day=local_day(now), now=now, guarantor=guarantor,
            guarantor_income=guarantor_income,
        )  # fmt: skip
        signed, balances = await self.economy.housing(guild_id, run)
        return signed, balances[user_id]

    async def advance_desk(self, guild_id: int, user_id: int) -> AdvanceDesk:
        """Lo que ve el banco de alguien para adelantarle rentas (💸 Anticipos)."""
        return await self.repository.advance_desk(guild_id, user_id, self.today())

    async def advance(
        self, guild_id: int, user_id: int, key: str, *, months: int, rate: float
    ) -> tuple[SignedAdvance, int]:
        """Firma un anticipo de rentas (fiscalidad en `HousingRepository.advance`).

        No mira el 35 %: la cuota la paga el inquilino (pero cuenta para lo siguiente).

        Raises:
            HousingError: Si debe a Hacienda o el banco no adelanta (ver `advance` del
                repositorio).
        """
        if await self.economy.tax_debt(guild_id, user_id) > 0:
            # Lo adelantado se embargaría al entrar, como un préstamo.
            raise HousingError(
                "Debes dinero a Hacienda y tendrías la cuenta embargada: el banco no adelanta "
                "rentas. Liquida la deuda en `renta`."
            )
        now = self._clock()
        run = self.repository.advance(
            guild_id, user_id, key, months=months, rate=rate, day=local_day(now), now=now
        )
        signed, balances = await self.economy.housing(guild_id, run)
        return signed, balances[user_id]

    async def buy(
        self,
        guild_id: int,
        user_id: int,
        key: str,
        shares: int,
        *,
        expected: int | None = None,
        financed: bool = False,
        aval: bool = False,
    ) -> Trade:
        """Compra participaciones, al contado o con hipoteca (ver `EconomyService.housing`).

        Args:
            expected: Lo que sale del bolsillo según la caja; si sube, no se compra.
            financed: Con hipoteca del 80 % (el banco mira `income`).
            aval: Si tus padres te avalan (`pala`): el banco suma
                `housing.parents_income` si sin ella no llega. `Purchase.aval` dice si
                hizo falta, y entonces quien llama gasta el favor.

        Raises:
            HousingError: Si no se puede (quedan menos libres, el banco no presta…).
            PriceChanged: Si lo que sale del bolsillo ya no es `expected`.
            InsufficientFundsError: Si no le llega. No se mueve nada.
        """
        now = self._clock()
        income = await self.income(guild_id, user_id) if financed else 0
        run = self.repository.buy(
            guild_id, user_id, key, shares, local_day(now), now, expected=expected,
            financed=financed, income=income,
            aval_income=rules.parents_income() if financed and aval else 0,
        )  # fmt: skip
        purchase, balances = await self.economy.housing(guild_id, run)
        return Trade(purchase, balances[user_id])

    async def sell(
        self, guild_id: int, user_id: int, key: str, shares: int, *, expected: int | None = None
    ) -> Trade:
        """Vende participaciones al mercado.

        Args:
            expected: Lo que la caja dijo que cobraría; si ha cambiado, no se vende.

        Raises:
            HousingError: Si no tiene tantas.
            PriceChanged: Si lo que cobraría ya no es `expected`.
        """
        now = self._clock()
        run = self.repository.sell(
            guild_id, user_id, key, shares, local_day(now), now, expected=expected
        )
        sale, balances = await self.economy.housing(guild_id, run)
        return Trade(sale, balances[user_id])

    async def amortize(self, guild_id: int, user_id: int, mortgage_id: int) -> tuple[int, int]:
        """Cancela una hipoteca de golpe. Devuelve `(lo pagado, saldo)`.

        Raises:
            HousingError: Si la hipoteca ya no existe.
            InsufficientFundsError: Si no le llega.
        """
        now = self._clock()
        run = self.repository.amortize(guild_id, user_id, mortgage_id, local_day(now), now)
        paid, balances = await self.economy.housing(guild_id, run)
        return paid, balances[user_id]

    async def vpo_signup(self, guild_id: int, user_id: int, *, join: bool = True) -> None:
        """Se apunta (o se borra) del sorteo de VPO de los lunes.

        Raises:
            HousingError: Si gana demasiado (más de 3,5 veces el IPREM) o ya tiene casa.
        """
        if join and await self.work_income(guild_id, user_id) > rules.vpo_income_cap():
            raise HousingError(
                "Ganas demasiado para una VPO: el límite son 3,5 veces el IPREM. "
                "Felicidades, eres clase media: búscate la vida en el mercado."
            )
        await self.repository.vpo_signup(guild_id, user_id, self._clock(), join)

    async def vpo_buy(self, guild_id: int, user_id: int, unit: str) -> tuple[VpoDeal, int]:
        """Compra la VPO que le ha tocado. Devuelve `(la compra, saldo)`.

        Raises:
            HousingError: Si no le toca, gana demasiado o el banco no presta.
            InsufficientFundsError: Si no le llega para la notaría.
        """
        if await self.work_income(guild_id, user_id) > rules.vpo_income_cap():
            raise HousingError("Desde que te apuntaste ganas demasiado: la VPO ya no es para ti.")
        now = self._clock()
        income = await self.income(guild_id, user_id)
        run = self.repository.vpo_buy(guild_id, user_id, unit, local_day(now), now, income=income)
        deal, balances = await self.economy.housing(guild_id, run)
        return deal, balances[user_id]

    async def vpo_return(self, guild_id: int, user_id: int) -> tuple[VpoDeal, int]:
        """Devuelve su VPO al Estado a precio tasado. Devuelve `(la devolución, saldo)`.

        Raises:
            HousingError: Si no tiene VPO.
            InsufficientFundsError: Si el Estado no tiene partida para recomprarla.
        """
        now = self._clock()
        run = self.repository.vpo_return(guild_id, user_id, local_day(now), now)
        deal, balances = await self.economy.housing(guild_id, run)
        return deal, balances[user_id]

    async def set_home(self, guild_id: int, user_id: int, key: str) -> None:
        """Se muda a un piso suyo (vivienda habitual)."""
        now = self._clock()
        await self.repository.set_home(guild_id, user_id, key, local_day(now), now)

    async def leave_home(self, guild_id: int, user_id: int) -> str:
        """Deja su vivienda habitual. Devuelve el tipo que deja."""
        now = self._clock()
        return await self.repository.leave_home(guild_id, user_id, local_day(now), now)

    async def rent_to_member(
        self, guild_id: int, owner_id: int, key: str, member_id: int, *, aval: bool = False
    ) -> TenantDeal:
        """Alquila un piso a un miembro, que deja la fianza; si gana poco, cobra el Bono.

        Args:
            aval: Si sus padres le avalan (`pala`): no se le pide la garantía adicional.
                `TenantDeal.aval` dice si hizo falta, y entonces quien llama gasta el favor.

        Raises:
            HousingError: Si no se puede firmar.
            InsufficientFundsError: Si no le llega para la fianza.
        """
        now = self._clock()
        income = await self.work_income(guild_id, member_id)
        run = self.repository.rent_to_member(
            guild_id, owner_id, key, member_id, local_day(now), now,
            bono=income <= rules.bono_income_cap(), income=income, aval=aval,
        )  # fmt: skip
        deal, _balances = await self.economy.housing(guild_id, run)
        return deal

    async def buy_license(self, guild_id: int, user_id: int, key: str) -> tuple[int, int]:
        """Pide la licencia vacacional. Devuelve `(tasa, saldo)`; tasa 0 si la comunidad dice no."""
        now = self._clock()
        run = self.repository.buy_license(guild_id, user_id, key, local_day(now), now, self.rng)
        fee, balances = await self.economy.housing(guild_id, run)
        if user_id not in balances:  # la comunidad dijo que no: no se ha movido dinero
            return fee, await self.economy.balance(guild_id, user_id)
        return fee, balances[user_id]

    async def found_socimi(self, guild_id: int, user_id: int) -> tuple[int, int]:
        """Monta su SOCIMI. Devuelve `(lo pagado, saldo)`."""
        now = self._clock()
        run = self.repository.found_socimi(guild_id, user_id, local_day(now), now)
        fee, balances = await self.economy.housing(guild_id, run)
        return fee, balances[user_id]

    async def accept_offer(self, guild_id: int, user_id: int, key: str) -> Trade:
        """Vende entero al fondo buitre al precio de su oferta."""
        market = await self.market(guild_id)
        held = market.holdings.get(key, {}).get(user_id)
        if held is None:
            raise HousingError("No tienes nada en este inmueble.")
        now = self._clock()
        run = self.repository.sell(
            guild_id, user_id, key, held.shares, local_day(now), now, vulture=True
        )
        sale, balances = await self.economy.housing(guild_id, run)
        return Trade(sale, balances[user_id])

    async def set_level(self, guild_id: int, user_id: int, key: str, level: str) -> Lease:
        """Cambia la renta que pide un inmueble (para el próximo contrato)."""
        return await self.repository.set_level(guild_id, user_id, key, level)

    async def evict(self, guild_id: int, user_id: int, key: str) -> tuple[Eviction, int]:
        """Pide el desahucio de quien no paga o de los okupas. Devuelve `(desahucio, saldo)`.

        Raises:
            HousingError: Si no le toca o no hay a quién desahuciar.
            InsufficientFundsError: Si no le llega para el abogado y el procurador.
        """
        now = self._clock()
        run = self.repository.evict(guild_id, user_id, key, local_day(now), now, self.rng)
        eviction, balances = await self.economy.housing(guild_id, run)
        return eviction, balances.get(user_id, await self.economy.balance(guild_id, user_id))

    # -- 9.23.0: duración del contrato y recuperar el piso (`docs/pisos-llaves.md`) ---------

    async def set_term(self, guild_id: int, user_id: int, key: str, term: int) -> Lease:
        """Cambia lo que dura el próximo contrato (`housing.TERMS`)."""
        return await self.repository.set_term(guild_id, user_id, key, term)

    async def set_no_renew(self, guild_id: int, user_id: int, key: str, on: bool) -> Lease:
        """Avisa de que no renovará el contrato en vigor (o se echa atrás)."""
        now = self._clock()
        return await self.repository.set_no_renew(guild_id, user_id, key, on, local_day(now), now)

    async def offer_keys(
        self, guild_id: int, user_id: int, key: str, offer: str
    ) -> tuple[KeysDeal, int]:
        """Le ofrece al inquilino del catálogo dinero (o nada) por irse (`housing.KEYS_OFFERS`).

        Returns:
            `(lo que contestó, saldo)`.

        Raises:
            HousingError: Si no se puede preguntar.
            InsufficientFundsError: Si no le llega para lo que ofrece.
        """
        now = self._clock()
        run = self.repository.offer_keys(guild_id, user_id, key, offer, local_day(now), now,
                                         self.rng)  # fmt: skip
        deal, balances = await self.economy.housing(guild_id, run)
        return deal, balances.get(user_id, await self.economy.balance(guild_id, user_id))

    async def accept_keys_ask(self, guild_id: int, user_id: int, key: str) -> tuple[KeysDeal, int]:
        """Le paga al inquilino lo que pidió al rechazar la oferta. Devuelve `(trato, saldo)`.

        Raises:
            HousingError: Si no hay contraoferta o caducó.
            InsufficientFundsError: Si no le llega.
        """
        now = self._clock()
        run = self.repository.accept_keys_ask(guild_id, user_id, key, local_day(now), now)
        deal, balances = await self.economy.housing(guild_id, run)
        return deal, balances.get(user_id, await self.economy.balance(guild_id, user_id))

    async def drop_keys_ask(self, guild_id: int, user_id: int, key: str) -> Lease:
        """No acepta lo que pidió el inquilino."""
        return await self.repository.drop_keys_ask(guild_id, user_id, key)

    async def member_keys(
        self, guild_id: int, owner_id: int, key: str, member_id: int, amount: int
    ) -> MemberKeys:
        """Le compra las llaves a un miembro de alquiler que ha aceptado `amount`.

        Raises:
            HousingError: Si ya no se puede.
            InsufficientFundsError: Si al casero no le llega.
        """
        now = self._clock()
        run = self.repository.member_keys(guild_id, owner_id, key, member_id, amount,
                                          local_day(now), now)  # fmt: skip
        deal, _balances = await self.economy.housing(guild_id, run)
        return deal

    async def claim_need(self, guild_id: int, user_id: int, key: str) -> tuple[Lease, int]:
        """Pide el piso para vivir en él (art. 9.3 LAU). Devuelve `(contrato, indemnización)`."""
        now = self._clock()
        return await self.repository.claim_need(guild_id, user_id, key, local_day(now), now)

    async def leave_home_paying(self, guild_id: int, user_id: int) -> tuple[int, int]:
        """Deja antes de tiempo el piso que pidió para vivir. Devuelve `(lo pagado, saldo)`.

        Raises:
            HousingError: Si no hay indemnización que pagar.
            InsufficientFundsError: Si no le llega.
        """
        now = self._clock()
        run = self.repository.leave_home_paying(guild_id, user_id, local_day(now), now)
        fine, balances = await self.economy.housing(guild_id, run)
        return fine, balances.get(user_id, await self.economy.balance(guild_id, user_id))

    async def set_pick(self, guild_id: int, user_id: int, key: str, pick: str) -> Lease:
        """Cambia quién elige al próximo inquilino (`housing.PICK_AGENCY` o `PICK_SELF`)."""
        return await self.repository.set_pick(guild_id, user_id, key, pick)

    async def set_insurance(self, guild_id: int, user_id: int, key: str, on: bool) -> Lease:
        """Contrata o da de baja el seguro de impago de un inmueble."""
        return await self.repository.set_insurance(guild_id, user_id, key, on)

    async def sign_candidate(self, guild_id: int, user_id: int, key: str, tenant: str) -> Signing:
        """Firma con uno de los candidatos que esperan en un piso."""
        now = self._clock()
        return await self.repository.sign_candidate(
            guild_id, user_id, key, tenant, local_day(now), now
        )

    async def discard_candidate(self, guild_id: int, user_id: int, key: str, tenant: str) -> None:
        """Descarta a un candidato."""
        await self.repository.discard_candidate(guild_id, user_id, key, tenant)

    async def member_sale(
        self,
        guild_id: int,
        seller: int,
        buyer: int,
        key: str,
        shares: int,
        price: int,
        *,
        expected: int | None = None,
        financed: bool = False,
        aval: bool = False,
    ) -> tuple[MemberSale, int]:
        """Vende participaciones a otro miembro. Devuelve `(la venta, saldo del comprador)`.

        Args:
            expected: Lo que la oferta dijo que pagaría el comprador de su bolsillo.
            financed: Si el comprador paga con hipoteca (el banco mira sus ingresos).
            aval: Si al comprador le avalan sus padres (`pala`).

        Raises:
            HousingError: Si no se puede.
            PriceChanged: Si al comprador le costaría más de lo que decía la oferta.
            InsufficientFundsError: Si al comprador no le llega.
        """
        now = self._clock()
        income = await self.income(guild_id, buyer) if financed else 0
        run = self.repository.member_sale(
            guild_id, seller, buyer, key, shares, price, local_day(now), now,
            expected=expected, financed=financed, income=income,
            aval_income=rules.parents_income() if financed and aval else 0,
        )  # fmt: skip
        sale, balances = await self.economy.housing(guild_id, run)
        return sale, balances.get(buyer, 0)

    async def create_auction(
        self, guild_id: int, seller: int, key: str, shares: int, min_bid: int
    ) -> Auction:
        """Saca a subasta unas participaciones durante un día."""
        return await self.repository.create_auction(
            guild_id, seller, key, shares, min_bid, self._clock()
        )

    async def bid(self, guild_id: int, user_id: int, auction_id: int, amount: int) -> Bid:
        """Puja en una subasta (deja la puja, su ITP y su notaría en el depósito).

        Raises:
            HousingError: Si la subasta ha terminado o la puja no llega.
            InsufficientFundsError: Si no le llega para el depósito.
        """
        run = self.repository.bid(guild_id, user_id, auction_id, amount, self._clock())
        result, _balances = await self.economy.housing(guild_id, run)
        return result

    async def start_reform(
        self, guild_id: int, user_id: int, key: str, reform: str
    ) -> tuple[ReformDone, int]:
        """Empieza una reforma (paga la obra, su IGIC, el ICIO y la licencia). `(obra, saldo)`.

        Raises:
            HousingError: Si no se puede.
            InsufficientFundsError: Si no le llega.
        """
        now = self._clock()
        run = self.repository.start_reform(guild_id, user_id, key, reform, local_day(now), now)
        done, balances = await self.economy.housing(guild_id, run)
        return done, balances[user_id]

    async def split(
        self, guild_id: int, user_id: int, key: str, mode: str
    ) -> tuple[SplitDone, int]:
        """Divide un inmueble por plantas o lo prepara para alquilarlo por habitaciones.

        Paga la obra (o los muebles) con su IGIC y, por plantas, el ICIO, la licencia y el AJD
        de la escritura. `(lo hecho, saldo)`.

        Raises:
            HousingError: Si no se puede.
            InsufficientFundsError: Si no le llega.
        """
        now = self._clock()
        run = self.repository.split(guild_id, user_id, key, mode, local_day(now), now)
        done, balances = await self.economy.housing(guild_id, run)
        return done, balances[user_id]

    async def build(
        self,
        guild_id: int,
        user_id: int,
        solar: str,
        project: str,
        level: int,
        *,
        expected: int | None = None,
    ) -> tuple[Built, int]:
        """Compra un solar y empieza a construir (rama Ladrillo de `pala`). `(la obra, saldo)`.

        El dinero y su tratamiento fiscal, en `HousingRepository.build` y `docs/solares.md`.

        Raises:
            HousingError: Si no se puede (o el precio ha subido: `PriceChanged`).
            InsufficientFundsError: Si no le llega.
        """
        now = self._clock()
        run = self.repository.build(
            guild_id, user_id, solar, project, level, local_day(now), now, expected=expected
        )
        done, balances = await self.economy.housing(guild_id, run)
        return done, balances[user_id]

    async def bubble_gauge(self, guild_id: int) -> tuple[int, int]:
        """`(obra empezada, lo comprado)` de las últimas semanas, para 🏗️ Solares."""
        return await self.repository.bubble_gauge(guild_id, local_day(self._clock()))

    async def join_rooms(self, guild_id: int, user_id: int, key: str) -> None:
        """Deja de alquilar un piso por habitaciones (con todas vacías). No mueve dinero.

        Raises:
            HousingError: Si no se puede.
        """
        await self.repository.join_rooms(guild_id, user_id, key, self._clock())

    async def choose(self, guild_id: int, user_id: int, decision_id: int, choice: str) -> Decision:
        """Elige cómo resolver un imprevisto (se paga con el cierre de la noche)."""
        return await self.repository.choose(guild_id, user_id, decision_id, choice)

    async def close_auctions(self, guild_id: int) -> AuctionRun:
        """Cierra las subastas que han terminado (la tarea de cada 10 minutos)."""
        run = self.repository.close_auctions(guild_id, self._clock())
        result, _balances = await self.economy.housing(guild_id, run)
        return result

    async def propose(
        self, guild_id: int, user_id: int, key: str, kind: str, value: str = "", amount: int = 0
    ) -> Vote:
        """Propone algo a la junta del inmueble (vota sí al proponerlo).

        Raises:
            HousingError: Si no hay junta, ya se vota eso o la propuesta no vale.
        """
        return await self.repository.propose(
            guild_id, user_id, key, kind, value, amount, self._clock()
        )

    async def vote(self, guild_id: int, user_id: int, vote_id: int, yes: bool) -> VoteResult:
        """Vota en la junta; si ya han votado todos, se cierra y se hace lo aprobado.

        Raises:
            HousingError: Si la votación ya se cerró o no es dueño.
        """
        run = self.repository.vote(guild_id, user_id, vote_id, yes, self._clock(), self.rng)
        result, _balances = await self.economy.housing(guild_id, run)
        return result

    async def close_votes(self, guild_id: int) -> list[VoteResult]:
        """Cierra las votaciones de la junta que han llegado a su hora (cada 10 minutos)."""
        run = self.repository.close_votes(guild_id, self._clock(), self.rng)
        results, _balances = await self.economy.housing(guild_id, run)
        return results

    async def divide(self, guild_id: int, user_id: int, key: str) -> Auction:
        """Acción de división: saca el inmueble entero a subasta un día.

        Raises:
            HousingError: Si no hay varios dueños o ya está en subasta.
        """
        return await self.repository.divide(guild_id, user_id, key, self._clock())

    async def close_days(
        self,
        guild_id: int,
        builders: Callable[[], Awaitable[tuple[int, ...]]] | None = None,
        furniture: Callable[[int], Awaitable[frozenset[str]]] | None = None,
    ) -> list[DayRun]:
        """Cierra los días que falten hasta ayer (como mucho `CATCH_UP_DAYS`).

        La primera vez en un servidor solo se cierra ayer. Cada día va en su
        propia transacción; si uno falla, los anteriores quedan cerrados.

        Args:
            builders: Devuelve quienes fichan en la rama Ladrillo de `pala` (la obra nueva
                del lunes). Solo se llama si hay que cerrar un lunes.
            furniture: Devuelve las claves de la tienda que tiene un miembro (los muebles del
                vacacional). Se pregunta una vez por cada quien decide un vacacional o un
                piso de nómadas.
        """
        now = self._clock()
        today = local_day(now)
        last = await self.repository.last_day(guild_id)
        first = today - timedelta(days=1) if last is None else last + timedelta(days=1)
        first = max(first, today - timedelta(days=CATCH_UP_DAYS))
        owned: dict[int, frozenset[str]] = {}
        if furniture is not None and first < today:
            market = await self.repository.market(guild_id)
            for key, lease in market.leases.items():
                boss = market.manager(key)
                if boss is not None and boss not in owned and lease.level in (
                    rules.VACATION_LEVEL, rules.NOMAD_LEVEL,
                ):  # fmt: skip
                    owned[boss] = await furniture(boss)
        runs = []
        day = first
        while day < today:
            who = await builders() if builders is not None and day.weekday() == 0 else ()
            run, _balances = await self.economy.housing(
                guild_id, self.repository.close_day(guild_id, day, now, self.rng, who, owned)
            )
            if not run.done_before:
                runs.append(run)
            day += timedelta(days=1)
        return runs
