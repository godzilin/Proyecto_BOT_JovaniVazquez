"""Persistencia de `pisos`: quién tiene qué, qué pasa en cada inmueble y el índice de cada barrio.

Tablas del mercado (en el mismo archivo SQLite que el resto del bot):

- `housing_holdings`: las participaciones de cada miembro en cada inmueble, con lo
  que pagó (`paid`, sin impuestos: para el Patrimonio) y lo que le costó (`cost`,
  con ITP y notaría: para la ganancia al vender).
- `housing_leases`: el estado de cada inmueble con dueño (vacío, alquilado, impago,
  desahucio, okupas o vivienda habitual de su dueño), el nivel de renta, el contrato
  en vigor (con un inquilino ficticio o con un miembro, `member`, y si cobra el Bono
  Alquiler Joven), la licencia vacacional y la deuda del inmueble. Solo hay fila
  mientras alguien tenga alguna participación.
- `housing_index`: el índice de precios de cada barrio (1 si no hay fila).
- `housing_days`: días ya cerrados, para no cobrar dos veces el mismo mes.
- `housing_news`: lo que ha pasado en cada inmueble (y en las VPO, con `vpo:<clave>`),
  para el panel y los logros. Se guardan los últimos `NEWS_DAYS` días.
- `housing_income`: lo que cobró y pagó cada miembro cada día por cada inmueble,
  para el resumen del panel y lo que mira el banco. Se guardan `NEWS_DAYS` días.
- `housing_candidates`: los candidatos que esperan en un piso cuyo dueño elige al
  inquilino (`housing.PICK_SELF`), con el día hasta el que esperan.
- `housing_barrio_news`: la noticia de barrio de cada semana (`housing.monday_news`).
- `housing_rent_bonus`: los inmuebles por los que cada miembro ya se aplicó la deducción
  canaria por alquilar una vivienda vacía (una vez por inmueble, cinco como mucho).
- `housing_reforms`: las reformas de cada inmueble del servidor (en obras o terminadas). Se
  quedan con el inmueble aunque cambie de dueño o vuelva al mercado (`Market.listing` lo
  devuelve mejorado).
- `housing_decisions`: los imprevistos que espera decidir el dueño (averías, humedades,
  cerraduras y el vecino) y lo que eligió; si no elige en dos días, los arregla la gestora.
- `housing_reviews`: las últimas reseñas de cada vivienda vacacional (`REVIEW_WINDOW`); la
  media va en `housing_leases.rating`. Se borran si el inmueble vuelve al mercado.
- `housing_splits`: los inmuebles divididos (9.12.0). Por plantas es para siempre: cada
  planta (`clave~1`) tiene sus dueños, su contrato y su hipoteca, y el inmueble entero deja
  de existir. Por habitaciones (`clave~h1`) el piso sigue teniendo los dueños y su contrato
  queda en `housing.BY_ROOMS`; cada habitación tiene el suyo, y todo se deshace al juntarlas
  o cuando el piso vuelve al mercado.
- `housing_reclaims`: los pisos que su dueño ha pedido para vivir (art. 9.3 LAU, 9.23.0):
  quién, a qué miembro se lo pidió (si era uno), la indemnización si no vive ahí lo que debe
  y hasta cuándo (`until` vacío mientras el inquilino aún no se ha ido).

Hipotecas, banco, Euríbor, VPO, vivienda habitual, ofertas del fondo buitre y SOCIMI
están en `bot.repositories.housing_finance`, la junta de copropietarios (votaciones,
administradores y fondo de reserva) en `bot.repositories.housing_junta`, y las subastas y sus
pujas en `bot.repositories.housing_trade`, con funciones que corren dentro de las mismas
transacciones. Lo adjudicado en una subasta judicial queda bloqueado para venderlo al
mercado (`housing_holdings.locked_until`).

El dinero no se toca aquí. Lo que mueve dinero (`buy`, `sell`, `close_day`,
`amortize`, `vpo_buy`, `vpo_return`, `buy_license`, `found_socimi`,
`rent_to_member`, que cobra la fianza, y `evict`, que paga el abogado) devuelve una
función que corre dentro de la transacción de `EconomyRepository.housing`. Lo que no
mueve dinero (la renta que se pide, quién elige al inquilino, el seguro, firmar con un
candidato, apuntarse al sorteo, vivir en un piso) va en su propia transacción.

La fianza de un miembro de alquiler se devuelve sola la noche después de que se vaya,
por el camino que sea (se muda, lo desahucian, venden el piso, lo ejecutan): el cierre
de cada día mira las fianzas cuyo inquilino ya no vive en el piso (`_deposits_in`).

Solo se guardan IDs de miembros, claves del catálogo, importes y fechas. Todo se
borra cuando el bot sale del servidor.
"""

from __future__ import annotations

import asyncio
import random
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from functools import partial
from pathlib import Path
from typing import TypeVar

from bot.repositories import housing_finance as finance
from bot.repositories import housing_junta as junta
from bot.repositories import housing_trade as trade
from bot.repositories import sqlite
from bot.repositories.economy import (
    HOUSING_AUCTION_ACCOUNT_ID,
    HOUSING_DEPOSIT_ACCOUNT_ID,
    STATE_ACCOUNT_ID,
    HousingDebit,
    HousingMove,
    HousingPull,
    HousingRent,
    HousingTax,
    LedgerEntry,
)
from bot.repositories.housing_finance import Loan, Mortgage, Promotion, SolarWork, VpoOwner
from bot.repositories.housing_junta import Vote
from bot.repositories.housing_trade import Auction
from bot.services import credit
from bot.services import home as casa
from bot.services import housing as rules
from bot.services import housing_solars as solar_rules
from bot.services import loans as lending
from bot.services.economy import format_amount
from bot.services.housing_catalog import (
    BARRIO_BY_KEY,
    BARRIO_NEWS_BY_KEY,
    BARRIOS,
    DEFAULT_RENT_LEVEL,
    KINDS,
    LISTING_BY_KEY,
    REFORM_BY_KEY,
    RENT_LEVELS,
    REVIEW_LINES,
    VPO_BY_KEY,
    VPO_UNITS,
    BarrioNews,
    Listing,
    listings_of,
)
from bot.services.housing_furniture import furniture_stars, nomad_boost
from bot.services.housing_solars import SOLARS, solar_of, solar_units
from bot.services.housing_streets import (
    BUILDING_BY_KEY,
    STREET_UNIT_BY_KEY,
    Building,
    building_of,
    buildings_of,
    units_of_barrio,
)
from bot.services.levels import local_day
from bot.services.taxes import (
    INCOME_BUSINESS,
    INCOME_PRIZE,
    INSURANCE_DEDUCTION_RENT_MAX,
    VACANT_RENTAL_DEDUCTION_MAX,
    capital_gain_tax,
    igic,
    vacant_rental_deduction,
)

T = TypeVar("T")

#: Días de noticias y de cobros que se guardan.
NEWS_DAYS = 35
#: Motivo del libro (y concepto del IRPF) de la comisión del promotor de la obra nueva.
PROMOTER_CONCEPT = "pisos:promotor"


class HousingError(ValueError):
    """Una operación de `pisos` que no se puede hacer; el mensaje es para el miembro."""


class PriceChanged(HousingError):
    """El precio ha cambiado desde que se enseñó la caja: no se cobra nada.

    Attributes:
        amount: Lo que costaría (o se cobraría) ahora.
    """

    def __init__(self, amount: int) -> None:
        super().__init__("El precio ha cambiado mientras mirabas la caja.")
        self.amount = amount


@dataclass(frozen=True, slots=True)
class Holding:
    """Las participaciones de un miembro en un inmueble.

    Attributes:
        paid: Lo que pagó por ellas, sin impuestos ni gastos.
        cost: Lo que le costaron, con ITP y notaría.
        since: Cuándo compró la primera (epoch).
        locked_until: Hasta qué día no se pueden vender al mercado (lo adjudicado en una
            subasta judicial, `housing.AUCTION_LOCK_DAYS`); `None` si se pueden.
    """

    listing: str
    user_id: int
    shares: int
    paid: int
    cost: int
    since: float
    locked_until: date | None = None


@dataclass(frozen=True, slots=True)
class News:
    """Algo que pasó en un inmueble (o en una VPO, con `vpo:<clave>`).

    Attributes:
        kind: `compra`, `venta`, `firma`, `renueva`, `se_va`, `impago`, `paga`,
            `okupa`, `desahucio`, `juicio`, una clave de `housing.EVENTS` o lo de las
            fases 2 a 4 (`ejecucion`, `sorteo_vpo`, `compra_vpo`, `oferta_fondo`…).
        amount: Lo que costó o movió (0 si nada).
        user_id: El miembro del que va la noticia, si va de uno.
        tenant: El inquilino ficticio, si hay.
    """

    listing: str
    day: date
    kind: str
    amount: int
    user_id: int | None
    tenant: str | None


@dataclass(frozen=True, slots=True)
class Split:
    """Un inmueble dividido (`housing_splits`).

    Attributes:
        mode: `housing.FLOORS` (por plantas) o `housing.ROOMS` (por habitaciones).
        units: Plantas o habitaciones.
        cost: La obra o los muebles, sin impuestos (sube el valor de las plantas).
    """

    mode: str
    units: int
    cost: int


@dataclass(frozen=True, slots=True)
class Reclaim:
    """Un piso que su dueño ha pedido para vivir en él (art. 9.3 LAU, `housing_reclaims`).

    Attributes:
        user_id: El dueño, que tiene que vivir ahí.
        member: El miembro de alquiler al que se lo pidió (`None` si era del catálogo): cobra
            la indemnización si el dueño se va antes de tiempo.
        fine: La indemnización (`housing.need_fine`).
        until: Hasta qué día tiene que vivir ahí; `None` mientras el inquilino no se ha ido.
    """

    user_id: int
    member: int | None
    fine: int
    until: date | None = None


@dataclass(frozen=True, slots=True)
class Market:
    """Todo el mercado de un servidor en un momento.

    Attributes:
        indexes: Índice de cada barrio que se ha movido (los demás, 1).
        holdings: Participaciones de cada inmueble, por miembro.
        leases: Estado de cada inmueble con dueño.
        mortgages: Hipotecas activas.
        vpo: De quién es cada VPO.
        signups: Apuntados al sorteo de VPO.
        reservations: `{vpo: (a quién le tocó, último día)}`.
        homes: `{miembro: (tipo, referencia)}` de su vivienda habitual.
        euribor: El Euríbor de esta semana.
        bank_debts: Lo que se debe al banco tras una ejecución.
        offers: `{(inmueble, miembro): (precio, último día)}` del fondo buitre.
        socimi: Quién tiene SOCIMI.
        promotions: La obra nueva del servidor (fase 5), por clave.
        candidates: Candidatos que esperan en cada inmueble, `(inquilino, hasta)`.
        barrio_news: `{barrio: (noticia, desde, hasta)}` de las noticias de barrio.
        bonuses: Inmuebles por los que cada miembro ya se aplicó la deducción del art. 16.
        auctions: Las subastas abiertas.
        reforms: `{inmueble: {reforma: (coste, día que acaba, terminada, rehabilitación)}}`.
        decisions: Imprevistos que esperan decisión, por inmueble.
        reviews: Las últimas reseñas de cada vacacional, de la más nueva a la más vieja.
        votes: Las votaciones abiertas de las juntas.
        admins: Quién administra cada inmueble por acuerdo de su junta.
        reserves: El fondo de reserva de cada inmueble que tiene algo.
        loans: Préstamos personales activos (`banco`).
        member_debts: `{(acreedor, deudor): importe}` de los reembolsos de los avales.
        splits: Los inmuebles divididos por plantas o por habitaciones.
        ipc: El IPC del crédito del servidor, en % (`credit.ipc`): encarece las obras.
        keys_bought: Días en que cada miembro ha comprado unas llaves (las noticias `llaves` y
            `llaves_gratis` que quedan), para su fama de casero (`fame`).
        reclaims: Los pisos pedidos para vivir (art. 9.3 LAU), por inmueble.
        solars: Lo construido en los solares de las calles (9.56.0), por solar.
    """

    indexes: dict[str, float] = field(default_factory=dict)
    holdings: dict[str, dict[int, Holding]] = field(default_factory=dict)
    leases: dict[str, rules.Lease] = field(default_factory=dict)
    mortgages: tuple[Mortgage, ...] = ()
    vpo: dict[str, VpoOwner] = field(default_factory=dict)
    signups: tuple[int, ...] = ()
    reservations: dict[str, tuple[int, date]] = field(default_factory=dict)
    homes: dict[int, tuple[str, str]] = field(default_factory=dict)
    euribor: float = rules.EURIBOR_START
    bank_debts: dict[int, int] = field(default_factory=dict)
    offers: dict[tuple[str, int], tuple[int, date]] = field(default_factory=dict)
    socimi: frozenset[int] = frozenset()
    promotions: dict[str, Promotion] = field(default_factory=dict)
    candidates: dict[str, tuple[tuple[str, date], ...]] = field(default_factory=dict)
    barrio_news: dict[str, tuple[str, date, date]] = field(default_factory=dict)
    bonuses: dict[int, frozenset[str]] = field(default_factory=dict)
    auctions: tuple[Auction, ...] = ()
    reforms: dict[str, dict[str, tuple[int, date, bool, bool]]] = field(default_factory=dict)
    decisions: dict[str, tuple[Decision, ...]] = field(default_factory=dict)
    reviews: dict[str, tuple[Review, ...]] = field(default_factory=dict)
    votes: tuple[Vote, ...] = ()
    admins: dict[str, int] = field(default_factory=dict)
    reserves: dict[str, int] = field(default_factory=dict)
    loans: tuple[Loan, ...] = ()
    member_debts: dict[tuple[int, int], int] = field(default_factory=dict)
    splits: dict[str, Split] = field(default_factory=dict)
    ipc: int = 0
    keys_bought: dict[int, tuple[date, ...]] = field(default_factory=dict)
    reclaims: dict[str, Reclaim] = field(default_factory=dict)
    solars: dict[str, SolarWork] = field(default_factory=dict)
    _units: dict[str, tuple[Listing, ...]] = field(default_factory=dict, repr=False, compare=False)
    _built: dict[str, Listing] = field(default_factory=dict, repr=False, compare=False)

    def built_units(self) -> dict[str, Listing]:
        """Las unidades construidas en los solares (en obras o terminadas), por clave."""
        if self.solars and not self._built:
            for work in self.solars.values():
                for unit in solar_units(work.solar, work.project)[1]:
                    self._built[unit.key] = unit
        return self._built

    def building(self, key: str) -> Building | None:
        """Un edificio de las calles o el levantado en un solar (`None` si no existe)."""
        found = BUILDING_BY_KEY.get(key)
        work = self.solars.get(key)
        if found is None and work is not None:
            found = solar_units(work.solar, work.project)[0]
        return found

    def buildings_on(self, street: str) -> tuple[Building, ...]:
        """Los edificios de una calle, por su número; lo de su solar, al final si lo hay."""
        built = tuple(
            building
            for solar in SOLARS
            if solar.street == street and (building := self.building(solar.key)) is not None
        )
        return buildings_of(street) + built

    def building_of(self, key: str) -> Building | None:
        """El edificio en que está un inmueble (o una planta o habitación suya), si está en uno."""
        found = building_of(key)
        if found is None and (solar := solar_of(key)) is not None:
            found = self.building(solar.key)
        return found

    def manager(self, key: str) -> int | None:
        """Quién decide en un inmueble: a quien eligió la junta o la mayoría (art. 398 CC).

        Lo que eligió la junta vale mientras esa persona siga siendo dueña y haya junta (dos
        dueños o más); si no, decide quien tiene más de la mitad, o nadie.
        """
        holders = self.shares_of(key)
        admin = self.admins.get(self.owner_key(key))
        if admin is not None and admin in holders and len(holders) > 1:
            return admin
        return rules.manager(holders)

    def fame(self, user_id: int, day: date) -> int:
        """Pisos que ha recuperado comprando las llaves en los últimos `FAME_DAYS` días."""
        since = day - timedelta(days=rules.FAME_DAYS)
        return sum(1 for done in self.keys_bought.get(user_id, ()) if done > since)

    def keys_quote(self, key: str, day: date) -> rules.KeysQuote | None:
        """Lo que pide hoy por irse el inquilino del catálogo de un inmueble (o `None`)."""
        listing = self.listing(key)
        lease = self.leases.get(key)
        if listing is None or lease is None or lease.tenant is None or lease.member is not None:
            return None
        if not rules.recoverable(lease):
            return None
        boss = self.manager(key)
        fame = self.fame(boss, day) if boss is not None else 0
        seed = f"{key}:{lease.tenant}:{lease.signed or lease.since}"
        return rules.keys_quote(listing, lease, day, fame, seed, self.index(listing.barrio))

    def reclaim_of(self, user_id: int) -> tuple[str, Reclaim] | None:
        """El piso que alguien ha pedido para vivir y en el que aún tiene que vivir."""
        for key, reclaim in self.reclaims.items():
            if reclaim.user_id == user_id:
                return key, reclaim
        return None

    def votes_of(self, key: str) -> tuple[Vote, ...]:
        """Las votaciones abiertas de la junta de un inmueble."""
        return tuple(v for v in self.votes if v.listing == key)

    def division_of(self, key: str) -> Auction | None:
        """La subasta de la acción de división de un inmueble, si hay una abierta."""
        return next((a for a in self.auctions if a.listing == key and a.division), None)

    def auction_of(self, key: str, seller: int) -> Auction | None:
        """La subasta abierta de las participaciones de alguien en un inmueble, si hay."""
        return next((a for a in self.auctions if a.listing == key and a.seller == seller), None)

    def news_of(self, barrio: str, day: date) -> BarrioNews | None:
        """La noticia en marcha de un barrio ese día, si hay."""
        item = self.barrio_news.get(barrio)
        if item is None or not item[1] <= day <= item[2]:
            return None
        return BARRIO_NEWS_BY_KEY.get(item[0])

    def candidates_of(self, key: str, day: date) -> tuple[str, ...]:
        """Los candidatos que siguen esperando en un inmueble (vacío y con dueño que elige)."""
        lease = self.leases.get(key)
        if lease is None or lease.status != rules.VACANT or lease.pick != rules.PICK_SELF:
            return ()
        return tuple(t for t, until in self.candidates.get(key, ()) if until >= day)

    def listing(self, key: str) -> Listing | None:
        """El inmueble de una clave (del catálogo, de la obra nueva o una planta o habitación de
        uno dividido), con sus reformas."""
        listing = self.base(key)
        if listing is None or key not in self.reforms:
            return listing
        return self._improved(listing)

    def base(self, key: str) -> Listing | None:
        """El inmueble de una clave, sin sus reformas."""
        listing = (
            LISTING_BY_KEY.get(key) or STREET_UNIT_BY_KEY.get(key) or self.built_units().get(key)
        )
        if listing is None and (promotion := self.promotions.get(key)) is not None:
            listing = promotion.listing
        if listing is None and (parent := rules.parent_key(key)) is not None:
            listing = next((u for u in self.units_of(parent) if u.key == key), None)
        return listing

    def units_of(self, key: str) -> tuple[Listing, ...]:
        """Las plantas o habitaciones de un inmueble dividido, sin reformas (vacío si no lo está).

        Las plantas salen del inmueble sin reformas: las suyas se copiaron a cada planta al
        dividirlo. Las habitaciones, del piso con las suyas, que siguen siendo del piso.
        """
        split = self.splits.get(key)
        if split is None:
            return ()
        if key not in self._units:
            parent = self.base(key) if split.mode == rules.FLOORS else self.listing(key)
            self._units[key] = (
                rules.split_units(parent, split.mode, split.units, split.cost) if parent else ()
            )
        return self._units[key]

    def split_mode(self, key: str) -> str | None:
        """Cómo está dividido un inmueble (`housing.FLOORS` o `housing.ROOMS`), si lo está."""
        split = self.splits.get(key)
        return split.mode if split is not None else None

    def owner_key(self, key: str) -> str:
        """De qué inmueble son las participaciones de una clave: las de una habitación, del piso."""
        parent = rules.parent_key(key) if rules.is_room(key) else None
        return parent if parent is not None else key

    def room_keys(self) -> list[str]:
        """Las habitaciones de todos los pisos que se alquilan por habitaciones."""
        return [
            unit.key
            for key, split in self.splits.items()
            if split.mode == rules.ROOMS
            for unit in self.units_of(key)
        ]

    def _improved(self, listing: Listing) -> Listing:
        done = [
            (REFORM_BY_KEY[r], cost, until, rehab)
            for r, (cost, until, finished, rehab) in self.reforms.get(listing.key, {}).items()
            if finished and r in REFORM_BY_KEY
        ]
        return rules.improved(listing, done) if done else listing

    def keys(self) -> list[str]:
        """Las claves de todos los inmuebles del servidor: el catálogo, las unidades de las
        calles, la obra nueva y lo construido en los solares.

        Uno dividido por plantas deja su sitio a sus plantas. Las habitaciones no salen: sus
        participaciones son las del piso (`room_keys`).
        """
        out: list[str] = []
        for key in (*LISTING_BY_KEY, *STREET_UNIT_BY_KEY, *self.promotions, *self.built_units()):
            if self.split_mode(key) == rules.FLOORS:
                out.extend(unit.key for unit in self.units_of(key))
            else:
                out.append(key)
        return out

    def listings_of(self, barrio: str) -> tuple[Listing, ...]:
        """Los inmuebles de un barrio: los nueve del catálogo y su obra nueva, con reformas."""
        extra = tuple(p.listing for p in self.promotions.values() if p.listing.barrio == barrio)
        return tuple(self._improved(item) for item in (*listings_of(barrio), *extra))

    def index(self, barrio: str) -> float:
        """Índice de un barrio (1 si nunca se ha movido)."""
        return self.indexes.get(barrio, 1.0)

    def index_level(self, barrio: str) -> float:
        """El índice al que tiende un barrio con lo que tienen comprado los miembros (en sus
        destacados, la obra nueva, las unidades de sus calles y lo construido en sus solares)."""
        items: list[Listing] = []
        built = (unit for unit in self.built_units().values() if unit.barrio == barrio)
        streets = tuple(self._improved(unit) for unit in (*units_of_barrio(barrio), *built))
        for item in (*self.listings_of(barrio), *streets):
            if self.split_mode(item.key) == rules.FLOORS:
                items.extend(self._improved(unit) for unit in self.units_of(item.key))
            else:
                items.append(item)
        return rules.ownership_level(
            (item, sum(self.shares_of(item.key).values())) for item in items
        )

    def shares_of(self, listing: str) -> dict[int, int]:
        """Participaciones de cada dueño de un inmueble (las de una habitación, las del piso)."""
        return {m: h.shares for m, h in self.holdings.get(self.owner_key(listing), {}).items()}

    def free_shares(self, listing: str) -> int:
        """Participaciones que siguen en venta."""
        return rules.SHARES - sum(self.shares_of(listing).values())

    def of_member(self, user_id: int) -> list[Holding]:
        """Lo que tiene un miembro, en el orden del catálogo."""
        out = []
        for key in self.keys():
            holding = self.holdings.get(key, {}).get(user_id)  # solo inmuebles que existen
            if holding is not None:
                out.append(holding)
        return out

    def value(self, holding: Holding) -> int:
        """Valor de mercado de unas participaciones (0 si el inmueble ya no existe)."""
        listing = self.listing(holding.listing)
        if listing is None:
            return 0
        return rules.price(listing, self.index(listing.barrio)) * holding.shares // rules.SHARES

    def mortgages_of(self, user_id: int, listing: str | None = None) -> list[Mortgage]:
        """Hipotecas activas de un miembro (de un inmueble, si se dice)."""
        return [
            m
            for m in self.mortgages
            if m.user_id == user_id and (listing is None or m.listing == listing)
        ]

    def loans_of(self, user_id: int) -> list[Loan]:
        """Préstamos personales activos de un miembro."""
        return [loan for loan in self.loans if loan.user_id == user_id]

    def guaranteed_by(self, user_id: int) -> list[Mortgage | Loan]:
        """Lo que avala un miembro: préstamos y rehipotecas de otros (su riesgo indirecto)."""
        return [
            debt
            for debt in (*self.mortgages, *self.loans)
            if lending.guarantor_of(debt.aval) == user_id
        ]

    def advance_of(self, user_id: int, listing: str) -> Loan | None:
        """El anticipo de rentas vivo de un miembro sobre un inmueble, si tiene."""
        return next((x for x in self.loans_of(user_id) if x.advance and x.listing == listing), None)

    def installments_of(self, user_id: int) -> int:
        """Lo que paga cada mes al banco: hipotecas y préstamos con su seguro (el 35 %).

        Suma también las cuotas de lo que avala: es riesgo indirecto suyo en la CIRBE y el
        banco lo cuenta como si lo pagara él.
        """
        own = sum(m.payment for m in self.mortgages_of(user_id)) + sum(
            loan.monthly for loan in self.loans_of(user_id)
        )
        guaranteed = sum(
            debt.monthly if isinstance(debt, Loan) else debt.payment
            for debt in self.guaranteed_by(user_id)
        )
        return own + guaranteed

    def owed_to_members(self, user_id: int) -> int:
        """Lo que debe a los avalistas que pagaron por él."""
        return sum(
            amount for (_c, debtor), amount in self.member_debts.items() if debtor == user_id
        )

    def in_asnef(self, user_id: int) -> bool:
        """Si está en el fichero de morosos: cuotas sin pagar o deuda con el banco.

        Quien dejó que el banco reclamara a su avalista sigue en el fichero mientras no le
        devuelva lo pagado: el impago con el banco sigue sin saldar.
        """
        counts = [m.unpaid_count for m in self.mortgages_of(user_id)]
        counts += [loan.unpaid_count for loan in self.loans_of(user_id)]
        owed = self.bank_debts.get(user_id, 0) + self.owed_to_members(user_id)
        return lending.in_asnef(counts, owed)

    def whole_homes(self, user_id: int) -> int:
        """Viviendas que tiene enteras (para gran tenedor, fondo buitre y SOCIMI)."""
        return sum(
            1
            for h in self.of_member(user_id)
            if h.shares == rules.SHARES and self.kind_of(h.listing) in rules.HOME_KINDS
        )

    def owns_home(self, user_id: int) -> bool:
        """Si tiene alguna participación de una vivienda o una VPO (para el sorteo)."""
        if any(owner.owner_id == user_id for owner in self.vpo.values()):
            return True
        return any(self.kind_of(h.listing) in rules.HOME_KINDS for h in self.of_member(user_id))

    def vpo_of(self, user_id: int) -> str | None:
        """La VPO de un miembro, si tiene."""
        return next((u for u, owner in self.vpo.items() if owner.owner_id == user_id), None)

    def reservation_of(self, user_id: int) -> str | None:
        """La VPO que le ha tocado en el sorteo y aún puede comprar."""
        return next((u for u, (m, _until) in self.reservations.items() if m == user_id), None)

    def licensed_in(self, barrio: str) -> int:
        """Licencias vacacionales de un barrio."""
        return sum(
            1
            for key, lease in self.leases.items()
            if lease.license and (item := self.listing(key)) is not None and item.barrio == barrio
        )

    def kind_of(self, key: str) -> str:
        """El tipo de un inmueble del servidor (vacío si la clave ya no existe)."""
        listing = self.listing(key)
        return listing.kind if listing is not None else ""


@dataclass(frozen=True, slots=True)
class Review:
    """La reseña de una estancia vacacional.

    Attributes:
        day: Cuándo se fue el huésped.
        guest: Quién era (clave del catálogo de inquilinos).
        stars: De 1 a 5.
        line: Su frase (`housing_catalog.REVIEW_LINES[stars][line]`).
    """

    day: date
    guest: str
    stars: int
    line: int


@dataclass(frozen=True, slots=True)
class Decision:
    """Un imprevisto que espera decisión del dueño (`housing.DECISIONS`).

    Attributes:
        id: Su número.
        listing: El inmueble.
        event: `averia`, `humedades`, `cerradura` o `vecino`.
        cost: Lo que costaría arreglarlo bien (sin IGIC).
        day: Cuándo pasó.
        until: Último día para decidir; después lo arregla la gestora.
        choice: Lo que eligió el dueño (`Choice.key`), si eligió.
    """

    id: int
    listing: str
    event: str
    cost: int
    day: date
    until: date
    choice: str | None = None


@dataclass(frozen=True, slots=True)
class SplitDone:
    """Un inmueble recién dividido.

    Attributes:
        listing: El inmueble.
        mode: `housing.FLOORS` o `housing.ROOMS`.
        quote: Las cuentas (`housing.split_quote`).
        units: Las claves de sus plantas o habitaciones.
        until: Último día de obras (o de amueblarlas).
    """

    listing: str
    mode: str
    quote: rules.SplitQuote
    units: tuple[str, ...]
    until: date


@dataclass(frozen=True, slots=True)
class Built:
    """Una obra recién empezada en un solar (9.56.0).

    Attributes:
        solar: El solar (y su edificio).
        project: El proyecto.
        quote: Las cuentas (`housing_solars.build_quote`).
        units: Las claves de sus unidades.
        until: Último día de obra.
        index: El índice del barrio al empezar (por debajo de 1, un barrio hundido).
    """

    solar: str
    project: str
    quote: solar_rules.BuildQuote
    units: tuple[str, ...]
    until: date
    index: float = 1.0


@dataclass(frozen=True, slots=True)
class ReformDone:
    """Una reforma pagada.

    Attributes:
        listing: El inmueble.
        reform: La clave de la reforma.
        quote: Las cuentas (`housing.reform_quote`).
        until: Último día de obras.
    """

    listing: str
    reform: str
    quote: rules.ReformQuote
    until: date


@dataclass(frozen=True, slots=True)
class Purchase:
    """Una compra hecha.

    Attributes:
        quote: Las cuentas.
        shares: Participaciones que tiene ahora en total.
        index_before: Índice del barrio antes de comprar.
        index_after: Índice después (comprar lo sube).
        first: Si es lo primero que tiene en todo el mercado.
        loan: Lo que prestó el banco (0 al contado).
        payment: La cuota de esa hipoteca.
        rate: Su tipo fijo (Euríbor de la semana más el diferencial).
        aval: Si el banco solo la ha dado con el aval de tus padres.
        promoter: Si es obra nueva, quien la levantó.
        promoter_fee: Lo que cobró el promotor de esta compra (0 si compra el propio
            promotor, que no se paga comisión a sí mismo).
    """

    listing: str
    quote: rules.PurchaseQuote
    shares: int
    index_before: float
    index_after: float
    first: bool
    loan: int = 0
    payment: int = 0
    rate: float = 0.0
    aval: bool = False
    promoter: int | None = None
    promoter_fee: int = 0


@dataclass(frozen=True, slots=True)
class Sale:
    """Una venta hecha.

    Attributes:
        quote: Las cuentas.
        shares_left: Participaciones que le quedan.
        held_days: Días que tuvo el inmueble (desde la primera compra).
        payoff: Lo que se fue a cancelar su hipoteca.
        vulture: Si fue la oferta del fondo buitre.
    """

    listing: str
    quote: rules.SaleQuote
    shares_left: int
    held_days: int
    payoff: int = 0
    vulture: bool = False


@dataclass(frozen=True, slots=True)
class VpoDeal:
    """Una VPO comprada o devuelta.

    Attributes:
        unit: La VPO.
        price: Precio tasado.
        notary: Notaría (al comprar).
        loan: Hipoteca con aval del Estado (al comprar).
        payment: Su cuota.
        payoff: Lo que se fue a cancelar la hipoteca (al devolver).
        returned: Si es una devolución.
    """

    unit: str
    price: int
    notary: int = 0
    loan: int = 0
    payment: int = 0
    payoff: int = 0
    returned: bool = False


@dataclass(frozen=True, slots=True)
class TenantDeal:
    """Un alquiler firmado con un miembro.

    Attributes:
        lease: El contrato.
        deposit: Lo que dejó en el depósito de fianzas (fianza y garantía).
        guarantee: Si el casero le pidió la garantía adicional.
        aval: Si se libró de la garantía gracias al aval de sus padres.
    """

    lease: rules.Lease
    deposit: int
    guarantee: bool
    aval: bool = False


@dataclass(frozen=True, slots=True)
class MemberSale:
    """Una venta de participaciones a otro miembro.

    Attributes:
        listing: El inmueble.
        quote: Las cuentas (`housing.member_sale_quote`).
        seller: Quien vende.
        buyer: Quien compra.
        loan: Lo que prestó el banco al comprador (0 al contado).
        payment: La cuota de esa hipoteca.
        rate: Su tipo fijo.
        aval: Si el banco solo la dio con el aval de los padres del comprador.
        payoff: Lo que se fue de lo cobrado a cancelar la hipoteca del vendedor.
        moved_in: Si el comprador era el miembro de alquiler y ahora vive en lo suyo.
    """

    listing: str
    quote: rules.MemberSaleQuote
    seller: int
    buyer: int
    loan: int = 0
    payment: int = 0
    rate: float = 0.0
    aval: bool = False
    payoff: int = 0
    moved_in: bool = False


@dataclass(frozen=True, slots=True)
class Bid:
    """Una puja hecha.

    Attributes:
        auction: La subasta, ya con la puja.
        escrow: Lo que ha dejado en el depósito (la puja, el ITP y la notaría).
        outbid: A quién ha superado (se le ha devuelto lo suyo), si a alguien.
        extended: Si la subasta se ha alargado por pujar en la última hora.
    """

    auction: Auction
    escrow: int
    outbid: int | None = None
    extended: bool = False


@dataclass(frozen=True, slots=True)
class AuctionResult:
    """Cómo ha acabado una subasta.

    Attributes:
        auction: La subasta.
        winner: Quién se la lleva (`None` si nadie).
        price: Por cuánto.
        bank: Si se la ha quedado el banco al 70 % (una judicial sin puja que valga).
        cancelled: Si se ha anulado (el ejecutado pagó toda la deuda, o ya no existe).
    """

    auction: Auction
    winner: int | None = None
    price: int = 0
    bank: bool = False
    cancelled: bool = False


@dataclass(slots=True)
class AuctionRun:
    """Las subastas cerradas en una pasada (se rellena mientras se cierran).

    Tiene los mismos campos que usa una ejecución hipotecaria en `DayRun` (`executions`,
    `aval_executions`, `news`), porque una judicial sin pujas acaba en una.
    """

    results: list[AuctionResult] = field(default_factory=list)
    executions: list[tuple[int, str]] = field(default_factory=list)
    aval_executions: list[int] = field(default_factory=list)
    remortgage_executions: list[int] = field(default_factory=list)
    guarantee_claims: list[tuple[int, int, int]] = field(default_factory=list)
    news: list[News] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Eviction:
    """Un desahucio pedido.

    Attributes:
        lease: El contrato, ya con la fecha del juicio.
        fee: Abogado y procurador (sin IGIC); 0 si los paga el seguro de impago.
        fee_igic: El IGIC de esos servicios.
        insured: Si los ha pagado el seguro de impago.
        squatters: Si es contra okupas.
    """

    lease: rules.Lease
    fee: int = 0
    fee_igic: int = 0
    insured: bool = False
    squatters: bool = False


@dataclass(frozen=True, slots=True)
class KeysDeal:
    """Una oferta por las llaves a un inquilino del catálogo, con lo que contestó.

    Attributes:
        lease: El contrato después (con el día en que se va, o su contraoferta).
        answer: Lo que contestó (`housing.KeysAnswer`).
        quote: Lo que pedía (`housing.KeysQuote`); `None` al aceptar su contraoferta.
        tenant: Quién es.
    """

    lease: rules.Lease
    answer: rules.KeysAnswer
    quote: rules.KeysQuote | None
    tenant: str | None


@dataclass(frozen=True, slots=True)
class MemberKeys:
    """Las llaves que un casero le compra a un miembro de alquiler.

    Attributes:
        lease: El contrato después, con el día en que se va.
        amount: Lo pagado.
        tax: El IRPF de la ganancia patrimonial del miembro (19 %).
    """

    lease: rules.Lease
    amount: int
    tax: int


@dataclass(frozen=True, slots=True)
class Signing:
    """Un contrato firmado con un candidato elegido por el dueño.

    Attributes:
        lease: El contrato.
        tenant: El inquilino.
        risk: Su riesgo de impago (`bajo`, `medio` o `alto`).
        first: Si es el primer contrato del inmueble.
    """

    lease: rules.Lease
    tenant: str
    risk: str
    first: bool


@dataclass(frozen=True, slots=True)
class MemberDay:
    """Lo que un miembro cobró y pagó un día por todos sus inmuebles (antes del IRPF)."""

    rent: int = 0
    ibi: int = 0
    costs: int = 0


@dataclass(slots=True)
class DayRun:
    """Un día cerrado en un servidor (se rellena mientras se cierra).

    Attributes:
        done_before: Ya estaba cerrado; no se ha tocado nada.
        news: Lo que ha pasado.
        members: Lo que cobró y pagó cada dueño.
        owners: Dueños de cada inmueble con noticias (para avisarles).
        executions: `(miembro, inmueble)` de cada ejecución hipotecaria.
        draws: `(miembro, vpo)` de cada sorteo ganado.
        offers: `(miembro, inmueble, precio)` de cada oferta del fondo.
        bono: Lo que el Estado pagó de Bono Alquiler Joven a cada miembro.
        bono_unpaid: Miembros a los que no se pudo pagar el bono (partida agotada).
        mortgage_unpaid: Miembros que no pudieron pagar entera la cuota.
        installments: Cuotas de hipoteca pagadas enteras por cada miembro.
        remortgage_executions: Miembros a los que han ejecutado un inmueble rehipotecado.
        advance_orphans: Miembros que pagan la cuota de un anticipo de rentas con el
            inquilino ido o sin pagar (la pone él).
        aval_executions: Miembros a los que han ejecutado una hipoteca avalada por sus
            padres (la familia de `pala` lo paga).
        promotions: `(promotor, inmueble)` de la obra nueva que ha salido hoy (los lunes).
        euribor: `(el de antes, el nuevo)` si hoy ha sido lunes y se ha movido el
            Euríbor (aunque se quede igual), para el anuncio del BCE.
        barrio_news: `(barrio, noticia)` de las noticias de barrio que salen hoy (lunes).
        candidates: `(inmueble, inquilino)` de cada candidato que llega hoy.
        insurer: Lo que el seguro de impago ha pagado hoy a cada dueño.
        bonus: Lo que deduce cada dueño hoy por alquilar una vivienda vacía (art. 16).
        young: Inmuebles que firman hoy con la reducción del 70 % (inquilino joven).
        auctions: `(ejecutado, inmueble, subasta)` de las subastas judiciales que se abren hoy.
        resolved: `(dueños, imprevisto, forma)` de los imprevistos resueltos hoy.
        works: `(dueños, inmueble)` de las obras que acaban hoy.
        moods: Humor de los inquilinos de cada dueño al cerrar el día.
        reviews: `(dueños, inmueble, estrellas)` de cada reseña de hoy.
        superhosts: `(dueños, inmueble)` de los vacacionales que hoy llegan a Superanfitrión.
        cleanings: `(dueños, inmueble)` de cada limpieza de hoy (un huésped que se va).
        loan_installments: Cuotas de préstamos pagadas enteras por cada miembro.
        loan_unpaid: Miembros que no pudieron pagar entera la cuota de un préstamo.
        loan_covered: Miembros a los que el seguro les ha pagado hoy una cuota.
        loans_finished: Miembros que hoy han terminado de pagar un préstamo.
        loan_defaults: `(miembro, deuda)` de cada préstamo vencido hoy.
        guarantee_claims: `(avalista, avalado, importe)` que el banco reclama hoy a un
            avalista (un préstamo vencido o una ejecución que no cubre su rehipoteca).
        reimbursements: `(avalista, avalado, importe)` de lo que hoy le ha devuelto.
        homes: Lo que pasó hoy en la casa de cada miembro que vive en una (`casa`): su
            Confort y lo que pagó de suministros.
        solars_done: `(promotor, solar)` de las obras de los solares que acaban hoy.
        bubble: `(lo que baja, barrios, promotores)` si hoy (lunes) ha estallado la burbuja.
    """

    day: date
    done_before: bool = False
    news: list[News] = field(default_factory=list)
    members: dict[int, MemberDay] = field(default_factory=dict)
    owners: dict[str, tuple[int, ...]] = field(default_factory=dict)
    executions: list[tuple[int, str]] = field(default_factory=list)
    draws: list[tuple[int, str]] = field(default_factory=list)
    offers: list[tuple[int, str, int]] = field(default_factory=list)
    bono: dict[int, int] = field(default_factory=dict)
    bono_unpaid: list[int] = field(default_factory=list)
    mortgage_unpaid: list[int] = field(default_factory=list)
    installments: dict[int, int] = field(default_factory=dict)
    aval_executions: list[int] = field(default_factory=list)
    remortgage_executions: list[int] = field(default_factory=list)
    advance_orphans: list[int] = field(default_factory=list)
    promotions: list[tuple[int, str]] = field(default_factory=list)
    solars_done: list[tuple[int, str]] = field(default_factory=list)
    bubble: tuple[float, tuple[str, ...], tuple[int, ...]] | None = None
    euribor: tuple[float, float] | None = None
    barrio_news: list[tuple[str, str]] = field(default_factory=list)
    candidates: list[tuple[str, str]] = field(default_factory=list)
    insurer: dict[int, int] = field(default_factory=dict)
    bonus: dict[int, int] = field(default_factory=dict)
    young: list[str] = field(default_factory=list)
    auctions: list[tuple[int, str, int]] = field(default_factory=list)
    resolved: list[tuple[tuple[int, ...], str, str]] = field(default_factory=list)
    works: list[tuple[tuple[int, ...], str]] = field(default_factory=list)
    moods: dict[int, list[int]] = field(default_factory=dict)
    reviews: list[tuple[tuple[int, ...], str, int]] = field(default_factory=list)
    superhosts: list[tuple[tuple[int, ...], str]] = field(default_factory=list)
    cleanings: list[tuple[tuple[int, ...], str]] = field(default_factory=list)
    loan_installments: dict[int, int] = field(default_factory=dict)
    loan_unpaid: list[int] = field(default_factory=list)
    loan_covered: list[int] = field(default_factory=list)
    loans_finished: list[int] = field(default_factory=list)
    loan_defaults: list[tuple[int, int]] = field(default_factory=list)
    guarantee_claims: list[tuple[int, int, int]] = field(default_factory=list)
    reimbursements: list[tuple[int, int, int]] = field(default_factory=list)
    homes: dict[int, HomeNight] = field(default_factory=dict)


@dataclass(slots=True)
class HomeNight:
    """La noche de la casa de un miembro (`casa`), para sus logros.

    Attributes:
        place: Dónde vive.
        comfort: El Confort de su casa.
        bill: Los suministros que tocaban.
        unpaid: Lo que no le llegó; si es más de 0, le han cortado la luz.
    """

    place: casa.Place
    comfort: casa.Comfort
    bill: casa.Supplies
    unpaid: int = 0

    @property
    def paid(self) -> int:
        """Lo que pagó de suministros."""
        return self.bill.total - self.unpaid


@dataclass(frozen=True, slots=True)
class HomeStatus:
    """La casa de un miembro ahora mismo (`casa` y los bufos de `pala`).

    Attributes:
        place: Dónde vive.
        comfort: El Confort de su casa.
        cut_until: Último día sin luz, si le cortaron los suministros.
    """

    place: casa.Place
    comfort: casa.Comfort
    cut_until: date | None = None

    def cut(self, day: date) -> bool:
        """Si `day` está sin luz (la casa no da bufos)."""
        return self.cut_until is not None and day <= self.cut_until

    @property
    def bill(self) -> casa.Supplies:
        """Los suministros que le tocan esta noche."""
        return casa.supplies(self.place)


def place_of(market: Market, kind: str, ref: str) -> casa.Place | None:
    """La vivienda habitual de `(tipo, referencia)` con lo que mira el Confort, o `None` si ya
    no existe (una VPO o un inmueble que se ha ido del mercado)."""
    if kind == finance.HOME_VPO:
        unit = VPO_BY_KEY.get(ref)
        if unit is None:
            return None
        return casa.Place(casa.VPO, ref, unit.name, "vpo", unit.m2, casa.VPO_ENERGY, 0,
                          casa.VPO_THEME)  # fmt: skip
    listing = market.listing(ref)
    if listing is None:
        return None
    # Las reformas de una habitación son las del piso (`Market.units_of`).
    done = market.reforms.get(ref) or market.reforms.get(rules.parent_key(ref) or "", {})
    reforms = sum(
        1 for r, (_c, _u, finished, _rh) in done.items()
        if finished and r in REFORM_BY_KEY and listing.kind in REFORM_BY_KEY[r].kinds
    )  # fmt: skip
    barrio = BARRIO_BY_KEY.get(listing.barrio)
    kind_name = KINDS[listing.kind].name if listing.kind in KINDS else listing.kind
    return casa.Place(
        casa.OWN if kind == finance.HOME_OWN else casa.RENTED,
        ref,
        f"{kind_name} en {barrio.name}" if barrio else kind_name,
        listing.kind,
        listing.m2,
        listing.energy,
        reforms,
        barrio.theme if barrio else "",
        listing.floor,
    )


def _date(text: str | None) -> date | None:
    return date.fromisoformat(text) if text else None


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day else None


def _listing(market: Market, key: str) -> Listing:
    listing = market.listing(key)
    if listing is None:
        raise HousingError("Ese inmueble no está en el mercado.")
    if market.split_mode(key) == rules.FLOORS:
        raise HousingError("Está dividido por plantas: cada planta va por separado.")
    return listing


def _whole(market: Market, key: str) -> Listing:
    """El inmueble de algo que se hace con él entero: comprarlo, venderlo, alquilarlo a un
    miembro o pedir su licencia. Una habitación no va sola."""
    if rules.is_room(key):
        raise HousingError("Una habitación no va sola: eso se hace con el piso entero.")
    return _listing(market, key)


def _drop_rooms_in(connection: sqlite3.Connection, guild_id: int, market: Market, key: str) -> None:
    """Si el piso se alquilaba por habitaciones, deja de hacerlo: borra sus habitaciones."""
    if market.split_mode(key) != rules.ROOMS:
        return
    rooms = [(guild_id, unit.key) for unit in market.units_of(key)]
    for table in ("housing_leases", "housing_candidates", "housing_decisions"):
        # `table` sale de una tupla fija, nunca de entrada del usuario.
        connection.executemany(f"DELETE FROM {table} WHERE guild_id = ? AND listing = ?", rooms)
    connection.execute(
        "DELETE FROM housing_splits WHERE guild_id = ? AND listing = ?", (guild_id, key)
    )


def _lease_from(row: sqlite3.Row) -> rules.Lease:
    return rules.Lease(
        status=str(row["status"]),
        level=str(row["level"]),
        tenant=row["tenant"],
        rent=int(row["rent"]),
        until=_date(row["until"]),
        since=_date(row["since"]),
        unpaid=int(row["unpaid"]),
        eviction_at=_date(row["eviction_at"]),
        arrears=int(row["arrears"]),
        arrears_ibi=int(row["arrears_ibi"]),
        member=row["member"],
        bono=bool(row["bono"]),
        license=bool(row["license"]),
        last_rent=int(row["last_rent"]),
        reduction=None if row["reduction"] is None else float(row["reduction"]),
        pick=str(row["pick"]),
        insured=bool(row["insured"]),
        claims=int(row["claims"]),
        solvent=bool(row["solvent"]),
        bonus=bool(row["bonus"]),
        works_until=_date(row["works_until"]),
        mood=int(row["mood"]),
        home_claim_until=_date(row["home_claim_until"]),
        rating=float(row["rating"]),
        reviews=int(row["reviews"]),
        license_until=_date(row["license_until"]),
        license_denied_until=_date(row["license_denied_until"]),
        term=int(row["term"]),
        leaving=str(row["leaving"]),
        keys_ask=int(row["keys_ask"]),
        keys_wait=_date(row["keys_wait"]),
        signed=_date(row["signed"]),
    )


def _save_lease_in(
    connection: sqlite3.Connection, guild_id: int, listing: str, lease: rules.Lease
) -> None:
    connection.execute(
        """
        INSERT INTO housing_leases
            (guild_id, listing, status, level, tenant, rent, until, since, unpaid,
             eviction_at, arrears, arrears_ibi, member, bono, license, last_rent, reduction,
             pick, insured, claims, solvent, bonus, works_until, mood, home_claim_until,
             rating, reviews, license_until, license_denied_until, term, leaving, keys_ask,
             keys_wait, signed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (guild_id, listing) DO UPDATE SET
            status = excluded.status, level = excluded.level, tenant = excluded.tenant,
            rent = excluded.rent, until = excluded.until, since = excluded.since,
            unpaid = excluded.unpaid, eviction_at = excluded.eviction_at,
            arrears = excluded.arrears, arrears_ibi = excluded.arrears_ibi,
            member = excluded.member, bono = excluded.bono, license = excluded.license,
            last_rent = excluded.last_rent, reduction = excluded.reduction,
            pick = excluded.pick, insured = excluded.insured, claims = excluded.claims,
            solvent = excluded.solvent, bonus = excluded.bonus,
            works_until = excluded.works_until, mood = excluded.mood,
            home_claim_until = excluded.home_claim_until, rating = excluded.rating,
            reviews = excluded.reviews, license_until = excluded.license_until,
            license_denied_until = excluded.license_denied_until, term = excluded.term,
            leaving = excluded.leaving, keys_ask = excluded.keys_ask,
            keys_wait = excluded.keys_wait, signed = excluded.signed
        """,
        (
            guild_id, listing, lease.status, lease.level, lease.tenant, lease.rent,
            _iso(lease.until), _iso(lease.since), lease.unpaid, _iso(lease.eviction_at),
            lease.arrears, lease.arrears_ibi, lease.member, int(lease.bono), int(lease.license),
            lease.last_rent, lease.reduction, lease.pick, int(lease.insured), lease.claims,
            int(lease.solvent), int(lease.bonus), _iso(lease.works_until), lease.mood,
            _iso(lease.home_claim_until), lease.rating, lease.reviews, _iso(lease.license_until),
            _iso(lease.license_denied_until), lease.term, lease.leaving, lease.keys_ask,
            _iso(lease.keys_wait), _iso(lease.signed),
        ),
    )  # fmt: skip


def _desk_in(
    connection: sqlite3.Connection, guild_id: int, user_id: int, market: Market, payroll: bool
) -> LoanDesk:
    """Lo que ve el banco de un miembro, leído dentro de una conexión abierta."""
    paid_loans, defaults = finance.loan_history_in(connection, guild_id, user_id)
    mortgages = market.mortgages_of(user_id)
    loans = market.loans_of(user_id)
    asnef = market.in_asnef(user_id)
    profile = lending.Profile(
        paid_loans=paid_loans,
        defaults=defaults,
        owns_property=bool(market.of_member(user_id)) or market.vpo_of(user_id) is not None,
        mortgage_current=bool(mortgages) and not any(m.unpaid_count for m in mortgages),
        late=not asnef and any(x.unpaid_count for x in (*mortgages, *loans)),
        payroll=payroll,
    )
    return LoanDesk(
        euribor=market.euribor,
        installments=market.installments_of(user_id),
        loans=tuple(x for x in loans if x.kind == finance.LOAN_PERSONAL),
        asnef=asnef,
        bank_debt=market.bank_debts.get(user_id, 0),
        profile=profile,
        guarantees=tuple(market.guaranteed_by(user_id)),
        purchases=tuple(x for x in loans if x.purchase),
    )


def _remortgage_desk_in(market: Market, user_id: int) -> RemortgageDesk:
    """Lo que ve el banco de alguien para rehipotecar, con el mercado ya leído."""
    found = []
    for key in market.keys():
        held = market.holdings.get(key, {}).get(user_id)
        listing = market.listing(key)
        if held is None or listing is None:
            continue
        value = market.value(held)
        owed = sum(m.owed for m in market.mortgages_of(user_id, key))
        dwelling = listing.kind in rules.HOME_KINDS
        blocked = None
        try:
            _check_free(market, key, user_id, date.min)
        except HousingError as error:
            blocked = str(error)
        found.append(
            Collateral(
                key=key, title=listing.title, shares=held.shares, value=value, owed=owed,
                dwelling=dwelling,
                habitual=market.homes.get(user_id) == (finance.HOME_OWN, key),
                room=lending.remortgage_room(value, owed, home=dwelling), blocked=blocked,
            )
        )  # fmt: skip
    return RemortgageDesk(
        euribor=market.euribor,
        installments=market.installments_of(user_id),
        asnef=market.in_asnef(user_id),
        collaterals=tuple(found),
        remortgages=tuple(m for m in market.mortgages_of(user_id) if m.remortgage),
    )


def guarantor_problem(market: Market, user_id: int, guarantor: int) -> str | None:
    """Por qué `guarantor` no puede avalar a `user_id` ahora, o `None`."""
    if guarantor == user_id:
        return "No te puedes avalar a ti mismo: eso es firmar dos veces."
    if market.in_asnef(guarantor):
        return "Tu avalista está en el fichero de morosos (ASNEF): el banco no lo acepta."
    if len(market.guaranteed_by(guarantor)) >= lending.MAX_GUARANTEES:
        return (
            f"Tu avalista ya avala {lending.MAX_GUARANTEES} cosas: el banco no le deja firmar "
            "más avales."
        )
    return None


def _room_with(
    market: Market, income: int, installments: int, guarantor: int | None, guarantor_income: int
) -> int:
    """Lo que cabe al mes en el 35 %: lo tuyo y, con avalista, también lo suyo."""
    room = lending.room_for(income, installments)
    if guarantor is None:
        return room
    theirs = lending.room_for(guarantor_income, market.installments_of(guarantor))
    return lending.guaranteed_room(room, theirs)


def _advance_desk_in(market: Market, user_id: int, day: date) -> AdvanceDesk:
    """Lo que ve el banco de alguien para adelantarle rentas, con el mercado ya leído."""
    found = []
    for key in market.keys():
        held = market.holdings.get(key, {}).get(user_id)
        listing = market.listing(key)
        if held is None or listing is None:
            continue
        lease = market.leases.get(key, rules.Lease())
        rented = lease.status == rules.RENTED and (lease.tenant or lease.member) is not None
        pool = sum(market.shares_of(key).values())
        payment = lease.rent * held.shares // max(1, pool) if rented else 0
        left = (lease.until - day).days if rented and lease.until is not None else 0
        live = market.advance_of(user_id, key)
        blocked = None
        if lease.status == rules.DEFAULTING or lease.unpaid:
            blocked = f"el inquilino debe {max(1, lease.unpaid)} meses"
        elif not rented:
            blocked = "no está alquilado"
        elif lease.level == rules.VACATION_LEVEL:
            blocked = "es vacacional: no hay contrato que ceder"
        elif market.homes.get(user_id) == (finance.HOME_OWN, key):
            blocked = "vives en él"
        elif live is not None:
            blocked = f"ya tienes el anticipo #{live.id}"
        elif left < min(lending.ADVANCE_TERMS):
            blocked = f"al contrato le quedan {max(0, left)} meses"
        elif payment <= 0:
            blocked = "tu parte de la renta no llega a 1 Y$"
        else:
            try:
                _check_free(market, key, user_id, day)
            except HousingError as error:
                blocked = str(error)
        found.append(
            RentShare(
                key=key, title=listing.title, shares=held.shares, payment=payment,
                months_left=max(0, left), blocked=blocked,
            )
        )  # fmt: skip
    return AdvanceDesk(
        euribor=market.euribor,
        installments=market.installments_of(user_id),
        asnef=market.in_asnef(user_id),
        shares=tuple(found),
        advances=tuple(x for x in market.loans_of(user_id) if x.advance),
    )


def _tenant_paying_in(connection: sqlite3.Connection, guild_id: int, key: str | None) -> bool:
    """Si el inmueble sigue alquilado y al día tras el cierre de esta noche."""
    row = connection.execute(
        "SELECT status, unpaid FROM housing_leases WHERE guild_id = ? AND listing = ?",
        (guild_id, key),
    ).fetchone()
    return row is not None and row[0] == rules.RENTED and not row[1]


def _check_no_advance(market: Market, key: str, user_id: int) -> None:
    """Comprueba que alguien no tiene cedidas al banco las rentas de lo que quiere vender.

    Solo para las ventas voluntarias (al mercado, al fondo buitre, a un miembro y la subasta
    que saca el dueño): el banco no consiente. Las forzosas (ejecución, división, la junta)
    no se paran y el anticipo sigue vivo. No va en `_check_free`, que mira también la
    rehipoteca, y un anticipo no impide rehipotecar. Tampoco se vende un local con el
    negocio propio dentro (`OWN_BUSINESS`): primero se traslada o se cierra.

    Raises:
        HousingError: Si tiene un anticipo vivo sobre ese inmueble o su negocio dentro.
    """
    lease = market.leases.get(key)
    if lease is not None and lease.status == rules.OWN_BUSINESS:
        raise HousingError(
            "Tienes tu negocio en este local: trasládalo o ciérralo antes de venderlo."
        )
    live = market.advance_of(user_id, key)
    if live is not None:
        raise HousingError(
            f"Tienes cedidas las rentas de este inmueble al banco (anticipo #{live.id}): "
            "cancélalo antes de vender."
        )


def _own_loan_in(connection: sqlite3.Connection, guild_id: int, user_id: int, loan_id: int) -> Loan:
    """Un préstamo activo de `user_id`, o `HousingError`."""
    loan = finance.loan_in(connection, guild_id, loan_id)
    if loan is None or loan.user_id != user_id:
        raise HousingError("Ese préstamo no es tuyo.")
    if loan.status != "activo":
        raise HousingError("Ese préstamo ya está cerrado.")
    return loan


def _reforms_in(
    connection: sqlite3.Connection, guild_id: int
) -> dict[str, dict[str, tuple[int, date, bool, bool]]]:
    """`{inmueble: {reforma: (coste, día que acaba, terminada, rehabilitación)}}`."""
    reforms: dict[str, dict[str, tuple[int, date, bool, bool]]] = {}
    for r in connection.execute(
        "SELECT listing, reform, cost, until, finished, rehab FROM housing_reforms "
        "WHERE guild_id = ?",
        (guild_id,),
    ):
        reforms.setdefault(str(r["listing"]), {})[str(r["reform"])] = (
            int(r["cost"]),
            date.fromisoformat(r["until"]),
            bool(r["finished"]),
            bool(r["rehab"]),
        )
    return reforms


def _splits_in(connection: sqlite3.Connection, guild_id: int) -> dict[str, Split]:
    """Los inmuebles divididos por plantas o por habitaciones."""
    return {
        str(r["listing"]): Split(str(r["mode"]), int(r["units"]), int(r["cost"]))
        for r in connection.execute(
            "SELECT listing, mode, units, cost FROM housing_splits WHERE guild_id = ?",
            (guild_id,),
        )
    }


def _market_in(connection: sqlite3.Connection, guild_id: int) -> Market:
    indexes = {
        str(r["barrio"]): float(r["value"])
        for r in connection.execute(
            "SELECT barrio, value FROM housing_index WHERE guild_id = ?", (guild_id,)
        )
    }
    holdings: dict[str, dict[int, Holding]] = {}
    for r in connection.execute(
        "SELECT listing, user_id, shares, paid, cost, since, locked_until FROM housing_holdings "
        "WHERE guild_id = ?",
        (guild_id,),
    ):
        holding = Holding(
            str(r["listing"]), int(r["user_id"]), int(r["shares"]), int(r["paid"]),
            int(r["cost"]), float(r["since"]), _date(r["locked_until"]),
        )  # fmt: skip
        holdings.setdefault(holding.listing, {})[holding.user_id] = holding
    leases = {
        str(r["listing"]): _lease_from(r)
        for r in connection.execute("SELECT * FROM housing_leases WHERE guild_id = ?", (guild_id,))
    }
    candidates: dict[str, list[tuple[str, date]]] = {}
    for r in connection.execute(
        "SELECT listing, tenant, until FROM housing_candidates WHERE guild_id = ? ORDER BY id",
        (guild_id,),
    ):
        candidates.setdefault(str(r["listing"]), []).append(
            (str(r["tenant"]), date.fromisoformat(r["until"]))
        )
    barrio_news = {
        str(r["barrio"]): (str(r["news"]), date.fromisoformat(r["start"]),
                           date.fromisoformat(r["until"]))
        for r in connection.execute(
            "SELECT barrio, news, start, until FROM housing_barrio_news WHERE guild_id = ?",
            (guild_id,),
        )
    }  # fmt: skip
    bonuses: dict[int, set[str]] = {}
    for r in connection.execute(
        "SELECT user_id, listing FROM housing_rent_bonus WHERE guild_id = ?", (guild_id,)
    ):
        bonuses.setdefault(int(r["user_id"]), set()).add(str(r["listing"]))
    reforms = _reforms_in(connection, guild_id)
    reviews: dict[str, list[Review]] = {}
    for r in connection.execute(
        "SELECT * FROM housing_reviews WHERE guild_id = ? ORDER BY id DESC", (guild_id,)
    ):
        reviews.setdefault(str(r["listing"]), []).append(
            Review(date.fromisoformat(r["day"]), str(r["guest"]), int(r["stars"]), int(r["line"]))
        )
    decisions: dict[str, list[Decision]] = {}
    for r in connection.execute(
        "SELECT * FROM housing_decisions WHERE guild_id = ? ORDER BY id", (guild_id,)
    ):
        decisions.setdefault(str(r["listing"]), []).append(
            Decision(
                int(r["id"]), str(r["listing"]), str(r["event"]), int(r["cost"]),
                date.fromisoformat(r["day"]), date.fromisoformat(r["until"]), r["choice"],
            )
        )  # fmt: skip
    return Market(
        indexes=indexes,
        holdings=holdings,
        leases=leases,
        mortgages=tuple(finance.mortgages_in(connection, guild_id)),
        loans=tuple(finance.loans_in(connection, guild_id)),
        member_debts=finance.member_debts_in(connection, guild_id),
        vpo=finance.vpo_in(connection, guild_id),
        signups=tuple(finance.vpo_signups_in(connection, guild_id)),
        reservations=finance.vpo_reservations_in(connection, guild_id),
        homes=finance.homes_in(connection, guild_id),
        euribor=finance.euribor_in(connection, guild_id),
        bank_debts=finance.bank_debts_in(connection, guild_id),
        offers=finance.offers_in(connection, guild_id),
        socimi=frozenset(finance.socimi_in(connection, guild_id)),
        promotions=finance.promotions_in(connection, guild_id),
        solars=finance.solars_in(connection, guild_id),
        candidates={k: tuple(v) for k, v in candidates.items()},
        barrio_news=barrio_news,
        bonuses={k: frozenset(v) for k, v in bonuses.items()},
        auctions=trade.auctions_in(connection, guild_id),
        reforms=reforms,
        decisions={k: tuple(v) for k, v in decisions.items()},
        reviews={k: tuple(v) for k, v in reviews.items()},
        votes=junta.votes_in(connection, guild_id),
        admins=junta.admins_in(connection, guild_id),
        reserves=junta.reserves_in(connection, guild_id),
        splits=_splits_in(connection, guild_id),
        ipc=credit.ipc(guild_id),
        keys_bought=_keys_in(connection, guild_id),
        reclaims={
            str(r["listing"]): Reclaim(
                int(r["user_id"]), r["member"], int(r["fine"]), _date(r["until"])
            )
            for r in connection.execute(
                "SELECT listing, user_id, member, fine, until FROM housing_reclaims "
                "WHERE guild_id = ?",
                (guild_id,),
            )
        },
    )


def _keys_in(connection: sqlite3.Connection, guild_id: int) -> dict[int, tuple[date, ...]]:
    """Los días en que cada miembro compró unas llaves (de las noticias que quedan)."""
    days: dict[int, list[date]] = {}
    for r in connection.execute(
        "SELECT user_id, day FROM housing_news WHERE guild_id = ? AND kind IN (?, ?) "
        "AND user_id IS NOT NULL",
        (guild_id, "llaves", "llaves_gratis"),
    ):
        days.setdefault(int(r["user_id"]), []).append(date.fromisoformat(r["day"]))
    return {member: tuple(found) for member, found in days.items()}


def _credit_alive_in(connection: sqlite3.Connection, guild_id: int) -> tuple[int, int]:
    """`(lo que se debe de financiaciones, el dinero de los miembros)`, para el IPC del crédito.

    El dinero solo se mira si hay algo financiado (sin nada, el IPC es 0 igual).
    """
    (alive,) = connection.execute(
        "SELECT COALESCE(SUM(balance + unpaid_amount), 0) FROM housing_loans "
        "WHERE guild_id = ? AND status = 'activo' AND kind IN (?, ?)",
        (guild_id, finance.LOAN_PURCHASE, finance.LOAN_BNPL),
    ).fetchone()
    if not alive:
        return 0, 0
    (money,) = connection.execute(
        "SELECT COALESCE(SUM(balance), 0) FROM economy_wallets WHERE guild_id = ? AND user_id > 0",
        (guild_id,),
    ).fetchone()
    return int(alive), int(money)


def _clear_reviews_in(connection: sqlite3.Connection, guild_id: int, key: str) -> None:
    connection.execute(
        "DELETE FROM housing_reviews WHERE guild_id = ? AND listing = ?", (guild_id, key)
    )


def _review_in(
    connection: sqlite3.Connection, guild_id: int, key: str, day: date, guest: str, stars: int,
    line: int,
) -> tuple[float, int]:  # fmt: skip
    """Guarda una reseña, deja las últimas `REVIEW_WINDOW` y devuelve `(media, cuántas)`."""
    connection.execute(
        "INSERT INTO housing_reviews (guild_id, listing, day, guest, stars, line) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (guild_id, key, day.isoformat(), guest, stars, line),
    )
    connection.execute(
        "DELETE FROM housing_reviews WHERE guild_id = ? AND listing = ? AND id NOT IN "
        "(SELECT id FROM housing_reviews WHERE guild_id = ? AND listing = ? "
        "ORDER BY id DESC LIMIT ?)",
        (guild_id, key, guild_id, key, rules.REVIEW_WINDOW),
    )
    count, total = connection.execute(
        "SELECT COUNT(*), COALESCE(SUM(stars), 0) FROM housing_reviews "
        "WHERE guild_id = ? AND listing = ?",
        (guild_id, key),
    ).fetchone()
    return (round(total / count, 2) if count else 0.0), int(count)


def _set_index_in(connection: sqlite3.Connection, guild_id: int, barrio: str, value: float) -> None:
    connection.execute(
        """
        INSERT INTO housing_index (guild_id, barrio, value) VALUES (?, ?, ?)
        ON CONFLICT (guild_id, barrio) DO UPDATE SET value = excluded.value
        """,
        (guild_id, barrio, value),
    )


def _index_in(connection: sqlite3.Connection, guild_id: int, barrio: str) -> float:
    row = connection.execute(
        "SELECT value FROM housing_index WHERE guild_id = ? AND barrio = ?", (guild_id, barrio)
    ).fetchone()
    return float(row["value"]) if row else 1.0


def _bonus_ready(market: Market, key: str, listing: Listing, lease: rules.Lease, day: date) -> bool:
    """Si un contrato que se firma hoy da la deducción del art. 16 (alquilar una vivienda vacía).

    El art. 16 del Decreto Legislativo 1/2009 pide que la vivienda imputara renta en el
    periodo anterior (aquí: una semana vacía, que es un año del IRPF) o que se alquile en
    los seis meses siguientes a comprarla sin inquilino (seis días). Garajes y trasteros
    no son vivienda.
    """
    if lease.status != rules.VACANT or listing.kind not in rules.HOME_KINDS:
        return False
    empty = (day - lease.since).days if lease.since is not None else 0
    bought = [local_day(h.since) for h in market.holdings.get(key, {}).values()]
    fresh = bool(bought) and (day - max(bought)).days <= rules.VACANT_BONUS_BOUGHT_DAYS
    return empty >= rules.VACANT_BONUS_EMPTY_DAYS or fresh


def _bubble_flows_in(
    connection: sqlite3.Connection, guild_id: int, day: date, market: Market
) -> tuple[list[SolarWork], int, int]:
    """`(obras empezadas, lo que valen, lo que han pagado los miembros por pisos)` de las
    últimas `BUBBLE_WINDOW` noches: la oferta y la demanda de la burbuja."""
    since = day - timedelta(days=solar_rules.BUBBLE_WINDOW)
    recent = [w for w in market.solars.values() if w.started > since]
    demand = connection.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM housing_news WHERE guild_id = ? "
        "AND kind IN ('compra', 'compra_miembro') AND day > ?",
        (guild_id, since.isoformat()),
    ).fetchone()[0]
    return recent, sum(w.value for w in recent), int(demand)


def _news_in(connection: sqlite3.Connection, guild_id: int, news: News, now: float) -> None:
    connection.execute(
        """
        INSERT INTO housing_news (guild_id, listing, day, kind, amount, user_id, tenant, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (guild_id, news.listing, news.day.isoformat(), news.kind, news.amount, news.user_id,
         news.tenant, now),
    )  # fmt: skip


def assets_in(connection: sqlite3.Connection, guild_id: int) -> dict[int, int]:
    """Patrimonio neto de los inmuebles de cada miembro (puede ser negativo), para el Patrimonio.

    Corre dentro de la transacción de `EconomyRepository.charge_wealth_tax`:

    - Cada inmueble a su valor fiscal (`housing.fiscal_value`, art. 10 de la Ley 19/1991).
    - La vivienda habitual está exenta hasta 3.000.000 Y$ (art. 4.Nueve): el piso en el
      que vive su dueño y la VPO (que nunca llega a ese valor: no cuenta).
    - Las deudas restan (arts. 9.Dos.b y 25.Uno): las hipotecas (también las rehipotecas),
      los préstamos y lo que se debe al banco. Las hipotecas de la vivienda habitual solo
      restan en la parte de la casa que no está exenta: no se restan las cargas de un bien
      exento ni las deudas para comprarlo, y con una exención parcial, la parte proporcional
      (arts. 9.Tres y 25.Tres). La VPO nunca pasa del límite: sus hipotecas no restan.
    """
    homes = finance.homes_in(connection, guild_id)
    market = Market(
        promotions=finance.promotions_in(connection, guild_id),
        solars=finance.solars_in(connection, guild_id),
    )
    out: dict[int, int] = {}
    #: Parte que tributa de la vivienda habitual de cada miembro, por su clave.
    taxed: dict[tuple[int, str], float] = {}
    for r in connection.execute(
        "SELECT listing, user_id, shares, paid FROM housing_holdings WHERE guild_id = ?",
        (guild_id,),
    ):
        listing = market.listing(str(r["listing"]))
        if listing is None:
            continue
        member = int(r["user_id"])
        value = rules.fiscal_value(listing, int(r["shares"]), int(r["paid"]))
        if homes.get(member) == (finance.HOME_OWN, listing.key):
            exempt = min(value, rules.HOME_WEALTH_EXEMPT)
            taxed[(member, listing.key)] = (value - exempt) / value if value else 0.0
            value -= exempt
        out[member] = out.get(member, 0) + value
    for mortgage in finance.mortgages_in(connection, guild_id):
        if mortgage.listing.startswith("vpo:"):
            continue
        share = taxed.get((mortgage.user_id, mortgage.listing), 1.0)
        out[mortgage.user_id] = out.get(mortgage.user_id, 0) - int(mortgage.owed * share)
    for member, debt in finance.bank_debts_in(connection, guild_id).items():
        out[member] = out.get(member, 0) - debt
    # Lo que se debe a un avalista resta al deudor y es un derecho de cobro del avalista. Lo
    # avalado no resta al avalista hasta que se lo reclaman (art. 25.Dos.a): entonces es su
    # deuda con el banco, que ya está arriba.
    for (creditor, debtor), amount in finance.member_debts_in(connection, guild_id).items():
        out[debtor] = out.get(debtor, 0) - amount
        out[creditor] = out.get(creditor, 0) + amount
    for loan in finance.loans_in(connection, guild_id):
        if not loan.corporate:  # lo de una sociedad lo resta su valor (`companies_in`)
            out[loan.user_id] = out.get(loan.user_id, 0) - loan.owed
    return out


def _refund(auction: Auction) -> list[tuple[int, LedgerEntry]]:
    """Lo que se devuelve a la mejor puja de una subasta que no se la lleva."""
    if auction.best_bidder is None or auction.escrow <= 0:
        return []
    return [
        (HOUSING_AUCTION_ACCOUNT_ID, LedgerEntry(-auction.escrow, "pisos:subasta_deposito")),
        (auction.best_bidder, LedgerEntry(auction.escrow, "pisos:subasta_deposito")),
    ]


def _won(auction: Auction, quote: rules.MemberSaleQuote) -> list[tuple[int, LedgerEntry]]:
    """El depósito de quien gana vuelve a su monedero y de ahí paga la puja y la notaría.

    El ITP y el IGIC de la notaría los paga después como impuestos (`_won_taxes`): así salen
    a su nombre en `hacienda`. Lo que dejó (`escrow`) es justo lo que paga en total.
    """
    assert auction.best_bidder is not None
    return [
        (HOUSING_AUCTION_ACCOUNT_ID, LedgerEntry(-auction.escrow, "pisos:subasta_deposito")),
        (auction.best_bidder, LedgerEntry(auction.escrow, "pisos:subasta_deposito")),
        (auction.best_bidder, LedgerEntry(-auction.best_bid, "pisos:subasta_compra")),
        (auction.best_bidder, LedgerEntry(-quote.notary, "pisos:notaria")),
    ]


def _won_taxes(
    winner: int, quote: rules.MemberSaleQuote, escrow: int, price: int
) -> list[HousingTax]:
    """El ITP y el IGIC de la notaría de quien gana: lo que dejó menos la puja y la notaría.

    El ITP se fijó al pujar (el valor de referencia de ese momento), para que lo que dejó en
    el depósito sea justo lo que paga aunque el barrio se haya movido.
    """
    itp = max(0, escrow - price - quote.notary - quote.notary_igic)
    return [
        HousingTax(winner, "itp", max(price, quote.reference), itp),
        HousingTax(winner, "igic_gastos", quote.notary, quote.notary_igic),
    ]


def _shrink_mortgages_in(
    connection: sqlite3.Connection, market: Market, user_id: int, key: str, shares: int,
    held: int,
) -> int:  # fmt: skip
    """Cancela la parte de las hipotecas de alguien que corresponde a lo que vende.

    Returns:
        Lo que hay que pagar al banco de lo cobrado (cancelación registral).
    """
    mortgages = market.mortgages_of(user_id, key)
    payoff = sum(m.owed for m in mortgages) * shares // held
    left = held - shares
    for mortgage in mortgages:
        if not left:
            finance.close_mortgage_in(connection, mortgage.id, "cancelada")
            continue
        finance.save_mortgage_in(
            connection, mortgage,
            balance=mortgage.balance - mortgage.balance * shares // held,
            payment=mortgage.payment - mortgage.payment * shares // held,
            months_left=mortgage.months_left,
            unpaid_count=mortgage.unpaid_count,
            unpaid_amount=mortgage.unpaid_amount - mortgage.unpaid_amount * shares // held,
        )  # fmt: skip
    return payoff


def _move_shares_in(
    connection: sqlite3.Connection,
    guild_id: int,
    market: Market,
    key: str,
    seller: int,
    buyer: int,
    shares: int,
    *,
    paid: int,
    cost: int,
    day: date,
    now: float,
    lease: rules.Lease,
    lock: date | None = None,
) -> bool:
    """Pasa participaciones de un miembro a otro (venta, subasta o adjudicación judicial).

    No mueve el índice: no entra ni sale nada del mercado. El contrato del inquilino sigue
    (art. 14 LAU) y las deudas del piso también (art. 9.1.e LPH). Si el que vende vivía allí,
    se muda; si el que compra era el miembro de alquiler y ahora lo tiene entero, deja de
    pagarse la renta a sí mismo y pasa a ser su vivienda habitual.

    Args:
        paid: Lo que paga el comprador (para su Patrimonio).
        cost: Lo que le cuesta, con ITP y notaría (para su ganancia al vender).
        lease: El contrato de partida (ya con lo que haya cambiado antes en la transacción).
        lock: Hasta qué día no puede venderlas al mercado (subasta judicial).

    Returns:
        Si el comprador se ha mudado a lo suyo.
    """
    held = market.holdings[key][seller]
    left = held.shares - shares
    if left:
        connection.execute(
            """
            UPDATE housing_holdings SET shares = ?, paid = ?, cost = ?
            WHERE guild_id = ? AND listing = ? AND user_id = ?
            """,
            (left, held.paid - held.paid * shares // held.shares,
             held.cost - held.cost * shares // held.shares, guild_id, key, seller),
        )  # fmt: skip
    else:
        connection.execute(
            "DELETE FROM housing_holdings WHERE guild_id = ? AND listing = ? AND user_id = ?",
            (guild_id, key, seller),
        )
    connection.execute(
        """
        INSERT INTO housing_holdings
            (guild_id, listing, user_id, shares, paid, cost, since, locked_until)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (guild_id, listing, user_id) DO UPDATE SET
            shares = shares + excluded.shares, paid = paid + excluded.paid,
            cost = cost + excluded.cost,
            locked_until = CASE
                WHEN excluded.locked_until IS NULL THEN locked_until
                WHEN locked_until IS NULL OR excluded.locked_until > locked_until
                    THEN excluded.locked_until
                ELSE locked_until END
        """,
        (guild_id, key, buyer, shares, paid, cost, now, _iso(lock)),
    )
    if market.homes.get(seller) == (finance.HOME_OWN, key):
        finance.clear_home_in(connection, guild_id, user_id=seller)
        lease = replace(lease, status=rules.VACANT, since=day)
    owned = market.shares_of(key).get(buyer, 0) + shares
    moved_in = lease.member == buyer and owned == rules.SHARES
    if moved_in:
        finance.clear_home_in(connection, guild_id, user_id=buyer)
        lease = replace(
            lease, status=rules.HABITUAL, member=None, bono=False, rent=0, until=None,
            since=day, unpaid=0, eviction_at=None, claims=0, last_rent=lease.rent,
            solvent=False, bonus=False,
        )  # fmt: skip
        finance.set_home_in(connection, guild_id, buyer, finance.HOME_OWN, key, now)
    _save_lease_in(connection, guild_id, key, lease)
    connection.execute(
        "DELETE FROM housing_offers WHERE guild_id = ? AND listing = ? AND user_id = ?",
        (guild_id, key, seller),
    )
    return moved_in


def _keys_ready(market: Market, key: str, user_id: int) -> rules.Lease:
    """El contrato al que se le puede pedir al inquilino que se vaya (lo decide la mayoría)."""
    _listing(market, key)
    if market.manager(key) != user_id:
        raise HousingError("Eso lo decide quien tiene más de la mitad del inmueble.")
    lease = market.leases.get(key)
    if lease is None or not rules.recoverable(lease):
        raise HousingError("No hay a quién pedirle que se vaya.")
    return lease


def _check_keys_wait(lease: rules.Lease, day: date) -> None:
    """El inquilino que acaba de decir que no, no quiere volver a hablar del tema aún."""
    if lease.keys_wait is not None and day < lease.keys_wait:
        extra = (
            f" Su precio, {format_amount(lease.keys_ask)}, sigue en pie." if lease.keys_ask else ""
        )
        raise HousingError(
            f"No quiere ni oír hablar del tema hasta el {lease.keys_wait:%d/%m}.{extra}"
        )


def _improve_in(
    connection: sqlite3.Connection, guild_id: int, key: str, user_id: int, amount: int
) -> None:
    """Suma una mejora a lo pagado y al coste de la parte de alguien (art. 35.1.b LIRPF)."""
    connection.execute(
        "UPDATE housing_holdings SET paid = paid + ?, cost = cost + ? "
        "WHERE guild_id = ? AND listing = ? AND user_id = ?",
        (amount, amount, guild_id, key, user_id),
    )


def _indemnity_move(payer: int, member: int | None, amount: int, reason: str) -> HousingMove:
    """Lo que paga un casero a quien echa: al miembro, con el 19 % de su ganancia patrimonial
    del ahorro (DGT V3259-23) al Estado; a un inquilino del catálogo, desaparece."""
    if member is None:
        return HousingMove(entries=((payer, LedgerEntry(-amount, reason)),))
    return HousingMove(
        entries=((payer, LedgerEntry(-amount, reason)), (member, LedgerEntry(amount, reason))),
        taxes=(HousingTax(member, "plusvalia", amount, capital_gain_tax(amount)),),
    )


def _check_reclaim_wait(market: Market, user_id: int) -> None:
    """Quien espera a que se vaya el inquilino del piso que pidió para vivir no se va a
    vivir a otro sitio: la necesidad tiene que ser de verdad (art. 9.3 LAU)."""
    found = market.reclaim_of(user_id)
    if found is not None and found[1].until is None:
        raise HousingError(
            "Has pedido un piso tuyo para vivir en él: espera a que se vaya el inquilino."
        )


def _check_reclaim(market: Market, key: str, user_id: int) -> None:
    """Lo que alguien pidió para vivir (art. 9.3 LAU) no lo vende por su voluntad hasta vivir
    ahí lo que debe: si no, la necesidad sería una forma gratis de vaciarlo para venderlo. Las
    ventas forzosas (una ejecución) no miran esto."""
    reclaim = market.reclaims.get(key)
    if reclaim is None or reclaim.user_id != user_id:
        return
    if reclaim.until is None:
        raise HousingError(
            "Lo has pedido para vivir (art. 9.3 LAU): no se vende hasta que vivas ahí "
            f"{rules.NEED_STAY_DAYS} días."
        )
    raise HousingError(
        f"Lo pediste para vivir (art. 9.3 LAU): hasta el {reclaim.until:%d/%m} no se vende. "
        "Si te mudas antes, pagas la indemnización."
    )


def _check_free(
    market: Market, key: str, user_id: int, day: date, *, market_sale: bool = False
) -> None:
    """Comprueba que alguien puede vender sus participaciones de un inmueble.

    Raises:
        HousingError: Si están en una subasta abierta o, para venderlas al mercado (también
            al fondo buitre), si se las adjudicaron en una subasta judicial hace menos de
            `housing.AUCTION_LOCK_DAYS` días.
    """
    if market.auction_of(key, user_id) is not None:
        raise HousingError("Están en subasta: espera a que acabe.")
    if market.division_of(key) is not None:
        raise HousingError(
            "Hay una acción de división en marcha: el inmueble entero está en subasta."
        )
    held = market.holdings.get(key, {}).get(user_id)
    if market_sale and held is not None and held.locked_until and held.locked_until > day:
        raise HousingError(
            "Te lo adjudicaron en una subasta judicial: hasta el "
            f"{held.locked_until:%d/%m} no se puede vender al mercado (a otro miembro, sí)."
        )


def _division_parts(holders: dict[int, int], buyer: int, amount: int, total: int) -> dict[int, int]:
    """Lo que cobra cada dueño (menos quien compra) de una puja por el inmueble entero."""
    return {m: amount * s // total for m, s in holders.items() if m != buyer}


def _division_quote(
    market: Market, listing: Listing, auction: Auction, buyer: int, amount: int
) -> rules.MemberSaleQuote:
    """La cuenta de quien puja en una acción de división: paga solo la parte de los demás.

    Raises:
        HousingError: Si ya es todo suyo.
    """
    holders = market.shares_of(auction.listing)
    buying = sum(s for m, s in holders.items() if m != buyer)
    if buying <= 0:
        raise HousingError("Ya es todo tuyo.")
    price = sum(_division_parts(holders, buyer, amount, auction.shares).values())
    return rules.member_sale_quote(
        listing, buying, buying, 0, max(1, price), index=market.index(listing.barrio),
        market_value_gain=False,
    )  # fmt: skip


def _check_motion(
    market: Market, listing: Listing, key: str, kind: str, value: str, amount: int
) -> None:
    """Comprueba que una propuesta de la junta tiene sentido (al proponerla y al aplicarla).

    Raises:
        HousingError: Si no lo tiene.
    """
    lease = market.leases.get(key, rules.Lease())
    holders = market.shares_of(key)
    if kind == "renta" and value not in RENT_LEVELS:
        raise HousingError("Esa renta no existe.")
    if kind == "renta" and value == rules.VACATION_LEVEL and not lease.license:
        raise HousingError(
            "Sin licencia vacacional no se alquila a turistas: votad antes la licencia."
        )
    if kind in ("seguro", "licencia") and listing.kind not in rules.HOME_KINDS:
        raise HousingError("Eso es solo para viviendas.")
    if kind == "seguro" and value not in ("0", "1"):
        raise HousingError("El seguro se contrata o se quita.")
    if kind == "eleccion" and value not in (rules.PICK_AGENCY, rules.PICK_SELF):
        raise HousingError("Ese modo no existe.")
    if kind == "administra" and (not value.isdigit() or int(value) not in holders):
        raise HousingError("Solo puede administrar uno de los dueños.")
    if kind == "derrama" and not rules.LEVY_MIN <= amount <= rules.LEVY_MAX:
        raise HousingError(
            f"La derrama va de {format_amount(rules.LEVY_MIN)} a {format_amount(rules.LEVY_MAX)}."
        )
    if kind == "reforma":
        spec = REFORM_BY_KEY.get(value)
        if spec is None or listing.kind not in spec.kinds:
            raise HousingError("Esa reforma no se le puede hacer a este inmueble.")
        if value in market.reforms.get(key, {}):
            raise HousingError("Esa reforma ya está hecha (o en obras).")
    if kind == "licencia" and lease.license:
        raise HousingError("Ya tiene licencia.")
    if kind in rules.SPLIT_MODES:
        base = market.base(key)
        if key in market.splits or rules.is_room(key) or base is None:
            raise HousingError("Ya está dividido.")
        problem = rules.split_problem(base, kind, promotion=key in market.promotions)
        if problem:
            raise HousingError(problem)
    if lease.status == rules.BY_ROOMS and kind in ("renta", "seguro", "eleccion", "licencia"):
        raise HousingError("Se alquila por habitaciones: eso se decide en cada habitación.")


def _check_wallets_in(connection: sqlite3.Connection, guild_id: int, move: HousingMove) -> None:
    """Comprueba que a cada dueño le llega para su parte de lo que paga la junta.

    Raises:
        HousingError: Si a alguno no le llega (la propuesta se queda sin hacer).
    """
    due: dict[int, int] = {}
    for member, entry in move.entries:
        due[member] = due.get(member, 0) - entry.delta
    for tax in move.taxes:
        due[tax.user_id] = due.get(tax.user_id, 0) + tax.tax
    for member, amount in due.items():
        row = connection.execute(
            "SELECT balance FROM economy_wallets WHERE guild_id = ? AND user_id = ?",
            (guild_id, member),
        ).fetchone()
        if amount > 0 and (row is None or int(row[0]) < amount):
            raise HousingError("A uno de los dueños no le llega para su parte.")


@dataclass
class _Parts:
    """Lo que mueven varias operaciones de la junta en una misma transacción."""

    entries: list[tuple[int, LedgerEntry]] = field(default_factory=list)
    taxes: list[HousingTax] = field(default_factory=list)
    debits: list[HousingDebit] = field(default_factory=list)

    def add(self, move: HousingMove) -> None:
        self.entries.extend(move.entries)
        self.taxes.extend(move.taxes)
        self.debits.extend(move.debits)

    def extend(self, other: _Parts) -> None:
        self.entries.extend(other.entries)
        self.taxes.extend(other.taxes)
        self.debits.extend(other.debits)

    def move(self, guild_id: int) -> HousingMove:
        def settle(conn: sqlite3.Connection, unpaid: dict[str, tuple[int, int]]) -> None:
            # Lo que no llegó de una derrama se debe a la comunidad: deuda del piso.
            for tag, (_ibi, other) in unpaid.items():
                if other:
                    conn.execute(
                        "UPDATE housing_leases SET arrears = arrears + ? "
                        "WHERE guild_id = ? AND listing = ?",
                        (other, guild_id, tag),
                    )

        return HousingMove(
            entries=tuple(self.entries), taxes=tuple(self.taxes), debits=tuple(self.debits),
            settle=settle if self.debits else None,
        )  # fmt: skip


@dataclass(frozen=True, slots=True)
class VoteResult:
    """Cómo queda una votación de la junta tras un voto o al cerrarse.

    Attributes:
        vote: La votación, con su estado (`abierta` si sigue).
        owners: Los dueños del inmueble (para avisarles y los logros).
    """

    vote: Vote
    owners: tuple[int, ...] = ()


def _vpo_eligible(market: Market, user_id: int) -> bool:
    """Si alguien puede optar a una VPO: sin vivienda propia ni VPO (vivir de alquiler vale)."""
    home = market.homes.get(user_id)
    return not market.owns_home(user_id) and (home is None or home[0] == finance.HOME_RENTED)


@dataclass(frozen=True, slots=True)
class LoanDesk:
    """Lo que ve el banco de un miembro al pedir un préstamo (`banco`).

    Attributes:
        euribor: El Euríbor de esta semana.
        installments: Lo que ya paga cada mes al banco (hipotecas y préstamos).
        loans: Sus préstamos personales activos.
        asnef: Si está en el fichero de morosos.
        bank_debt: Lo que debe al banco tras un vencimiento o una ejecución.
        profile: Su perfil, para el tipo (`loans.profile_factors`).
        guarantees: Lo que avala de otros (préstamos y rehipotecas).
        purchases: Sus financiaciones de compras (💳 Financiar), que van aparte del tope de
            préstamos personales.
    """

    euribor: float
    installments: int
    loans: tuple[Loan, ...]
    asnef: bool
    bank_debt: int
    profile: lending.Profile
    guarantees: tuple[Mortgage | Loan, ...] = ()
    purchases: tuple[Loan, ...] = ()


@dataclass(frozen=True, slots=True)
class BusinessLoanDesk:
    """Lo que ve el banco para prestar a un negocio (`banco`, 🏪 Negocio).

    De un autónomo mira a su dueño, que es quien lo firma: el 35 % de lo que ingresa (las
    nóminas y lo que deja el negocio). De una sociedad, el 35 % de lo que deja ella más lo
    de su socio, que lo avala. Las cuotas de los préstamos del negocio cuentan en las del
    dueño (`installments`): las firma él o las avala (riesgo indirecto en la CIRBE).

    Attributes:
        owner: Lo que ve del dueño: su perfil, sus cuotas y si está en el fichero.
        loans: Los préstamos vivos del negocio.
        income: Lo que ingresa el dueño al día (nóminas, negocio y el 70 % de las rentas).
        company_income: Lo que deja su sociedad al día (`business_sl.ebitda` de la semana
            pasada), o `None` si es de un autónomo.
    """

    owner: LoanDesk
    loans: tuple[Loan, ...]
    income: int
    company_income: int | None = None

    @property
    def corporate(self) -> bool:
        """Si lo pide una sociedad."""
        return self.company_income is not None

    @property
    def room(self) -> int:
        """Lo que cabe aún al mes en el 35 %."""
        # Lo que deja la sociedad y lo del socio, que no resta si está en pérdidas.
        own = max(0, self.income)
        return lending.room_for(own + (self.company_income or 0), self.owner.installments)

    @property
    def factors(self) -> list[lending.Factor]:
        """Lo que mueve el tipo: el perfil del dueño (sin seguro, que es de consumo)."""
        profile = self.owner.profile
        if self.corporate:
            # Lo paga lo que deja la sociedad, no la nómina de su socio.
            profile = replace(profile, payroll=True)
        return lending.profile_factors(profile, insured=False)

    @property
    def rate(self) -> float:
        """El TIN de hoy."""
        return lending.business_rate(self.owner.euribor, self.factors)

    def most(self, months: int) -> int:
        """Lo más que presta a `months` meses."""
        return lending.max_principal(self.room, self.rate, months, insured=False)


@dataclass(frozen=True, slots=True)
class BusinessInstallment:
    """La cuota de esta noche de un préstamo al negocio.

    Attributes:
        loan: El préstamo.
        balance: Lo que quedará por devolver tras la cuota.
        pay: La cuota del mes (0 si ya no quedan: solo se debe lo atrasado).
        interest: Lo que de ella son intereses.
    """

    loan: Loan
    balance: int
    pay: int
    interest: int

    @property
    def due(self) -> int:
        """Lo que se cobra esta noche: la cuota y lo que se debía."""
        return self.pay + self.loan.unpaid_amount


@dataclass(frozen=True, slots=True)
class Collateral:
    """Lo que vale para el banco lo que alguien tiene de un inmueble (🔐 Garantías).

    Attributes:
        key: El inmueble.
        title: Su titular (también el de la obra nueva, que no está en el catálogo).
        shares: Sus participaciones.
        value: Lo que valen hoy en el mercado (la tasación).
        owed: Lo que ya debe de sus hipotecas de ese inmueble.
        dwelling: Si es vivienda (presta el 80 % y no el 60 %).
        habitual: Si vive en él.
        room: Lo más que le prestaría rehipotecándolo, sin mirar el 35 %.
        blocked: Por qué no se puede rehipotecar ahora, o `None`.
    """

    key: str
    title: str
    shares: int
    value: int
    owed: int
    dwelling: bool
    habitual: bool
    room: int
    blocked: str | None = None


@dataclass(frozen=True, slots=True)
class RemortgageDesk:
    """Lo que ve el banco de alguien para rehipotecar.

    Attributes:
        euribor: El Euríbor de esta semana.
        installments: Lo que ya paga cada mes al banco (el 35 %).
        asnef: Si está en el fichero de morosos.
        collaterals: Sus inmuebles, en el orden del catálogo.
        remortgages: Sus rehipotecas activas.
    """

    euribor: float
    installments: int
    asnef: bool
    collaterals: tuple[Collateral, ...]
    remortgages: tuple[Mortgage, ...]

    @property
    def rate(self) -> float:
        """El tipo de una rehipoteca hoy: Euríbor más el diferencial, fijo al firmar."""
        return round(self.euribor + lending.REMORTGAGE_SPREAD, 4)


@dataclass(frozen=True, slots=True)
class RentShare:
    """Tu parte de la renta de un inmueble, como la ve el banco para un anticipo.

    Attributes:
        key: El inmueble.
        title: Su titular.
        shares: Tus participaciones.
        payment: Tu parte de la renta de hoy (la cuota del anticipo); 0 si no está alquilado.
        months_left: Días (meses) que le quedan al contrato.
        blocked: Por qué no se puede adelantar ahora, o `None`.
    """

    key: str
    title: str
    shares: int
    payment: int
    months_left: int
    blocked: str | None = None


@dataclass(frozen=True, slots=True)
class AdvanceDesk:
    """Lo que ve el banco de alguien para adelantarle rentas (💸 Anticipos).

    Attributes:
        euribor: El Euríbor de esta semana.
        installments: Lo que ya paga cada mes al banco (no se mira al firmar: lo paga el
            inquilino; cuenta en el 35 % de lo siguiente que pida).
        asnef: Si está en el fichero de morosos.
        shares: Sus inmuebles, en el orden del catálogo.
        advances: Sus anticipos vivos.
    """

    euribor: float
    installments: int
    asnef: bool
    shares: tuple[RentShare, ...]
    advances: tuple[Loan, ...]

    @property
    def rate(self) -> float:
        """El tipo de un anticipo hoy: Euríbor más el diferencial, fijo al firmar."""
        return round(self.euribor + lending.ADVANCE_SPREAD, 4)


@dataclass(frozen=True, slots=True)
class SignedAdvance:
    """Un anticipo de rentas recién firmado, con su oferta."""

    loan: Loan
    quote: lending.AdvanceQuote


@dataclass(frozen=True, slots=True)
class SignedRemortgage:
    """Una rehipoteca recién firmada, con su oferta."""

    mortgage: Mortgage
    quote: lending.RemortgageQuote


@dataclass(frozen=True, slots=True)
class SignedLoan:
    """Un préstamo recién firmado, con la oferta y lo que movió el tipo."""

    loan: Loan
    quote: lending.Quote
    factors: tuple[lending.Factor, ...]


@dataclass(frozen=True, slots=True)
class LoanClosed:
    """Un préstamo cerrado a mano: amortizado (`pagado`) o desistido.

    Attributes:
        paid: Lo que salió del monedero (en un desistimiento, lo prestado menos la comisión
            que vuelve).
        fee: La compensación por amortizar antes (0 al desistir).
    """

    loan: Loan
    status: str
    paid: int
    fee: int = 0


@dataclass(frozen=True, slots=True)
class BankBook:
    """Las cuentas del banco en un servidor, para `banco`.

    Attributes:
        mortgages: Hipotecas activas (con las rehipotecas).
        mortgage_balance: Capital que queda de ellas.
        remortgages: Rehipotecas activas.
        remortgage_balance: Capital que queda de ellas.
        loans: Préstamos activos.
        loan_balance: Capital que queda de ellos.
        lent: Todo lo prestado en préstamos personales desde el primer día.
        loan_interest: Intereses cobrados en los préstamos.
        loan_fees: Comisiones de apertura que se han quedado (sin las de los desistidos).
        premiums: Primas de seguros cobradas con las cuotas (sin su IPS).
        unpaid: Cuotas de hipotecas y préstamos que se deben.
        bank_debts: Deuda con el banco tras vencimientos y ejecuciones.
        defaults: Préstamos vencidos e hipotecas ejecutadas desde el primer día.
        asnef: Miembros en el fichero de morosos ahora.
    """

    mortgages: int = 0
    mortgage_balance: int = 0
    remortgages: int = 0
    remortgage_balance: int = 0
    loans: int = 0
    loan_balance: int = 0
    lent: int = 0
    loan_interest: int = 0
    loan_fees: int = 0
    premiums: int = 0
    unpaid: int = 0
    bank_debts: int = 0
    defaults: int = 0
    asnef: int = 0


class HousingRepository:
    """Acceso SQLite a `pisos`.

    Args:
        database_path: Ruta del archivo SQLite persistente.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        return sqlite.connect(self.database_path, isolation_level=None)

    async def _run(self, operation: Callable[..., T], *args: object) -> T:
        return await asyncio.to_thread(operation, *args)

    def _write(self, operation: Callable[..., T], *args: object) -> T:
        """Corre `operation(conexión, *args)` en una transacción de escritura propia."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                result = operation(connection, *args)
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
            return result
        finally:
            connection.close()

    async def initialize(self) -> None:
        """Crea las tablas si no existen y añade las columnas que falten."""
        await self._run(self._initialize_sync)

    def _initialize_sync(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS housing_holdings (
                    guild_id INTEGER NOT NULL,
                    listing TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    shares INTEGER NOT NULL CHECK (shares > 0 AND shares <= 100),
                    paid INTEGER NOT NULL CHECK (paid >= 0),
                    cost INTEGER NOT NULL CHECK (cost >= 0),
                    since REAL NOT NULL,
                    PRIMARY KEY (guild_id, listing, user_id)
                );

                CREATE TABLE IF NOT EXISTS housing_leases (
                    guild_id INTEGER NOT NULL,
                    listing TEXT NOT NULL,
                    status TEXT NOT NULL,
                    level TEXT NOT NULL,
                    tenant TEXT,
                    rent INTEGER NOT NULL DEFAULT 0,
                    until TEXT,
                    since TEXT,
                    unpaid INTEGER NOT NULL DEFAULT 0,
                    eviction_at TEXT,
                    arrears INTEGER NOT NULL DEFAULT 0 CHECK (arrears >= 0),
                    arrears_ibi INTEGER NOT NULL DEFAULT 0 CHECK (arrears_ibi >= 0),
                    member INTEGER,
                    bono INTEGER NOT NULL DEFAULT 0,
                    license INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, listing)
                );

                CREATE TABLE IF NOT EXISTS housing_index (
                    guild_id INTEGER NOT NULL,
                    barrio TEXT NOT NULL,
                    value REAL NOT NULL,
                    PRIMARY KEY (guild_id, barrio)
                );

                CREATE TABLE IF NOT EXISTS housing_days (
                    guild_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    processed_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, day)
                );

                CREATE TABLE IF NOT EXISTS housing_news (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    listing TEXT NOT NULL,
                    day TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    amount INTEGER NOT NULL DEFAULT 0,
                    user_id INTEGER,
                    tenant TEXT,
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS housing_news_listing
                    ON housing_news (guild_id, listing, id);

                CREATE TABLE IF NOT EXISTS housing_income (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    listing TEXT NOT NULL,
                    day TEXT NOT NULL,
                    rent INTEGER NOT NULL,
                    ibi INTEGER NOT NULL,
                    costs INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id, listing, day)
                );

                CREATE TABLE IF NOT EXISTS housing_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    listing TEXT NOT NULL,
                    tenant TEXT NOT NULL,
                    until TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS housing_candidates_listing
                    ON housing_candidates (guild_id, listing);

                CREATE TABLE IF NOT EXISTS housing_barrio_news (
                    guild_id INTEGER NOT NULL,
                    barrio TEXT NOT NULL,
                    news TEXT NOT NULL,
                    start TEXT NOT NULL,
                    until TEXT NOT NULL,
                    PRIMARY KEY (guild_id, barrio)
                );

                CREATE TABLE IF NOT EXISTS housing_reforms (
                    guild_id INTEGER NOT NULL,
                    listing TEXT NOT NULL,
                    reform TEXT NOT NULL,
                    cost INTEGER NOT NULL,
                    until TEXT NOT NULL,
                    finished INTEGER NOT NULL DEFAULT 0,
                    rehab INTEGER NOT NULL DEFAULT 0,
                    user_id INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, listing, reform)
                );

                CREATE TABLE IF NOT EXISTS housing_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    listing TEXT NOT NULL,
                    event TEXT NOT NULL,
                    cost INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    until TEXT NOT NULL,
                    choice TEXT,
                    chooser INTEGER
                );

                CREATE INDEX IF NOT EXISTS housing_decisions_listing
                    ON housing_decisions (guild_id, listing);

                CREATE TABLE IF NOT EXISTS housing_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    listing TEXT NOT NULL,
                    day TEXT NOT NULL,
                    guest TEXT NOT NULL,
                    stars INTEGER NOT NULL CHECK (stars BETWEEN 1 AND 5),
                    line INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS housing_reviews_listing
                    ON housing_reviews (guild_id, listing, id);

                CREATE TABLE IF NOT EXISTS housing_rent_bonus (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    listing TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, user_id, listing)
                );

                CREATE TABLE IF NOT EXISTS housing_reclaims (
                    guild_id INTEGER NOT NULL,
                    listing TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    member INTEGER,
                    fine INTEGER NOT NULL CHECK (fine >= 0),
                    until TEXT,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, listing)
                );

                CREATE TABLE IF NOT EXISTS housing_splits (
                    guild_id INTEGER NOT NULL,
                    listing TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    units INTEGER NOT NULL CHECK (units > 1),
                    cost INTEGER NOT NULL CHECK (cost >= 0),
                    created_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, listing)
                );
                """
                + finance.SCHEMA
                + trade.SCHEMA
                + junta.SCHEMA
            )
            # Bases de datos de la primera versión de `pisos`: sin miembros de alquiler,
            # bono ni licencias; de antes de la 8.0.0, sin la renta del contrato anterior
            # ni la reducción del art. 23.2 de cada contrato (NULL: la general); y de antes
            # de la 8.1.0, sin elegir inquilino, seguro de impago ni deducción del art. 16.
            columns = {row[1] for row in connection.execute("PRAGMA table_info(housing_leases)")}
            for name, ddl in (
                ("member", "member INTEGER"),
                ("bono", "bono INTEGER NOT NULL DEFAULT 0"),
                ("license", "license INTEGER NOT NULL DEFAULT 0"),
                ("last_rent", "last_rent INTEGER NOT NULL DEFAULT 0"),
                ("reduction", "reduction REAL"),
                ("pick", f"pick TEXT NOT NULL DEFAULT '{rules.PICK_AGENCY}'"),
                ("insured", "insured INTEGER NOT NULL DEFAULT 0"),
                ("claims", "claims INTEGER NOT NULL DEFAULT 0"),
                ("solvent", "solvent INTEGER NOT NULL DEFAULT 0"),
                ("bonus", "bonus INTEGER NOT NULL DEFAULT 0"),
                ("works_until", "works_until TEXT"),
                ("mood", f"mood INTEGER NOT NULL DEFAULT {rules.MOOD_BASE}"),
                ("home_claim_until", "home_claim_until TEXT"),
                # 8.4.0: la nota del vacacional y la licencia que caduca (Ley 6/2025).
                ("rating", "rating REAL NOT NULL DEFAULT 0"),
                ("reviews", "reviews INTEGER NOT NULL DEFAULT 0"),
                ("license_until", "license_until TEXT"),
                ("license_denied_until", "license_denied_until TEXT"),
                # 9.23.0: la duración elegida, por qué se va, las llaves y la firma.
                ("term", "term INTEGER NOT NULL DEFAULT 0"),
                ("leaving", "leaving TEXT NOT NULL DEFAULT ''"),
                ("keys_ask", "keys_ask INTEGER NOT NULL DEFAULT 0"),
                ("keys_wait", "keys_wait TEXT"),
                ("signed", "signed TEXT"),
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE housing_leases ADD COLUMN {ddl}")
            # 8.2.0: lo adjudicado en una subasta judicial no se vende al mercado en 30 días.
            held = {row[1] for row in connection.execute("PRAGMA table_info(housing_holdings)")}
            if "locked_until" not in held:
                connection.execute("ALTER TABLE housing_holdings ADD COLUMN locked_until TEXT")
            # 8.5.0: la subasta de la acción de división.
            sold = {row[1] for row in connection.execute("PRAGMA table_info(housing_auctions)")}
            if "division" not in sold:
                connection.execute(
                    "ALTER TABLE housing_auctions ADD COLUMN division INTEGER NOT NULL DEFAULT 0"
                )
            # 9.1.0: rehipotecas, anticipos de rentas y avalistas (`docs/garantias.md`).
            for table, name, ddl in finance.LATER_COLUMNS:
                found = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                if name not in found:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
        finally:
            connection.close()

    # -- Lecturas ---------------------------------------------------------------------

    async def market(self, guild_id: int) -> Market:
        """Todo el mercado del servidor (200 inmuebles y 6 VPO como mucho: de una vez)."""
        return await self._run(self._market_sync, guild_id)

    def _market_sync(self, guild_id: int) -> Market:
        connection = self._connect()
        try:
            return _market_in(connection, guild_id)
        finally:
            connection.close()

    async def news(self, guild_id: int, listings: tuple[str, ...], limit: int = 8) -> list[News]:
        """Lo último que ha pasado en unos inmuebles, de lo más nuevo a lo más viejo."""
        return await self._run(self._news_sync, guild_id, listings, limit)

    def _news_sync(self, guild_id: int, listings: tuple[str, ...], limit: int) -> list[News]:
        if not listings:
            return []
        marks = ", ".join("?" * len(listings))
        connection = self._connect()
        try:
            rows = connection.execute(
                f"""
                SELECT listing, day, kind, amount, user_id, tenant FROM housing_news
                WHERE guild_id = ? AND listing IN ({marks})
                ORDER BY id DESC LIMIT ?
                """,  # solo marcadores `?`, nunca texto del usuario
                (guild_id, *listings, limit),
            ).fetchall()
        finally:
            connection.close()
        return [
            News(str(r["listing"]), date.fromisoformat(r["day"]), str(r["kind"]),
                 int(r["amount"]), r["user_id"], r["tenant"])
            for r in rows
        ]  # fmt: skip

    async def income(self, guild_id: int, user_id: int, since: date) -> MemberDay:
        """Lo que un miembro ha cobrado y pagado desde `since` (incluido)."""
        return await self._run(self._income_sync, guild_id, user_id, since)

    def _income_sync(self, guild_id: int, user_id: int, since: date) -> MemberDay:
        connection = self._connect()
        try:
            rent, ibi, costs = connection.execute(
                """
                SELECT COALESCE(SUM(rent), 0), COALESCE(SUM(ibi), 0), COALESCE(SUM(costs), 0)
                FROM housing_income WHERE guild_id = ? AND user_id = ? AND day >= ?
                """,
                (guild_id, user_id, since.isoformat()),
            ).fetchone()
        finally:
            connection.close()
        return MemberDay(int(rent), int(ibi), int(costs))

    async def member_values(self, guild_id: int) -> tuple[dict[int, int], dict[int, int]]:
        """`(valor de mercado, patrimonio neto fiscal)` de los inmuebles de cada miembro."""
        return await self._run(self._member_values_sync, guild_id)

    def _member_values_sync(self, guild_id: int) -> tuple[dict[int, int], dict[int, int]]:
        connection = self._connect()
        try:
            market = _market_in(connection, guild_id)
            fiscal = assets_in(connection, guild_id)
        finally:
            connection.close()
        values: dict[int, int] = {}
        for members in market.holdings.values():
            for member, holding in members.items():
                values[member] = values.get(member, 0) + market.value(holding)
        for unit, owner in market.vpo.items():
            values[owner.owner_id] = values.get(owner.owner_id, 0) + VPO_BY_KEY[unit].price
        return values, fiscal

    async def member_debts(self, guild_id: int) -> dict[int, int]:
        """Lo que debe cada miembro: al banco y a sus avalistas (`perfil`, `fortunas`).

        Negativo si lo que le deben sus avalados pasa de lo que debe.
        """
        market = await self.market(guild_id)
        out: dict[int, int] = dict(market.bank_debts)
        # Lo que debe su sociedad no es suyo: solo lo avala (resta del valor de la sociedad).
        for debt in (*market.mortgages, *(x for x in market.loans if not x.corporate)):
            out[debt.user_id] = out.get(debt.user_id, 0) + debt.owed
        for (creditor, debtor), amount in market.member_debts.items():
            out[debtor] = out.get(debtor, 0) + amount
            out[creditor] = out.get(creditor, 0) - amount
        return {member: owed for member, owed in out.items() if owed}

    async def home_of(self, guild_id: int, user_id: int) -> tuple[str, str] | None:
        """`(tipo, referencia)` de la vivienda habitual de alguien, o `None`."""
        return await self._run(self._home_of_sync, guild_id, user_id)

    def _home_of_sync(self, guild_id: int, user_id: int) -> tuple[str, str] | None:
        connection = self._connect()
        try:
            return finance.homes_in(connection, guild_id).get(user_id)
        finally:
            connection.close()

    async def home_status(self, guild_id: int, user_id: int) -> HomeStatus | None:
        """La casa de alguien con su Confort (`casa` y `pala`), o `None` si vive con sus padres.

        Lee solo lo que hace falta (su vivienda, las reformas, las divisiones y la obra nueva),
        no el mercado entero: `pala` lo pide en cada turno.
        """
        return await self._run(self._home_status_sync, guild_id, user_id)

    def _home_status_sync(self, guild_id: int, user_id: int) -> HomeStatus | None:
        connection = self._connect()
        try:
            found = finance.homes_in(connection, guild_id).get(user_id)
            if found is None:
                return None
            market = Market(
                reforms=_reforms_in(connection, guild_id),
                splits=_splits_in(connection, guild_id),
                promotions=finance.promotions_in(connection, guild_id),
                solars=finance.solars_in(connection, guild_id),
            )
            place = place_of(market, *found)
            if place is None:
                return None
            cut = finance.supplies_cut_in(connection, guild_id, user_id)
        finally:
            connection.close()
        return HomeStatus(place, casa.comfort(place), cut)

    async def deposits_of(self, guild_id: int, user_id: int) -> list[finance.Deposit]:
        """Las fianzas de alguien que siguen en el depósito (para `saldo`)."""
        return await self._run(self._deposits_of_sync, guild_id, user_id)

    def _deposits_of_sync(self, guild_id: int, user_id: int) -> list[finance.Deposit]:
        connection = self._connect()
        try:
            return [d for d in finance.deposits_in(connection, guild_id) if d.user_id == user_id]
        finally:
            connection.close()

    # -- Préstamos personales (`banco`) ----------------------------------------------

    async def loan_desk(self, guild_id: int, user_id: int, *, payroll: bool) -> LoanDesk:
        """Lo que ve el banco de un miembro para ofrecerle un préstamo.

        Args:
            payroll: Si cobra nómina o pensión (lo dice la economía).
        """
        return await self._run(self._loan_desk_sync, guild_id, user_id, payroll)

    def _loan_desk_sync(self, guild_id: int, user_id: int, payroll: bool) -> LoanDesk:
        connection = self._connect()
        try:
            return _desk_in(
                connection, guild_id, user_id, _market_in(connection, guild_id), payroll
            )
        finally:
            connection.close()

    def take_loan(
        self,
        guild_id: int,
        user_id: int,
        *,
        principal: int,
        months: int,
        insured: bool,
        rate: float,
        income: int,
        payroll: bool,
        day: date,
        now: float,
        guarantor: int | None = None,
        guarantor_income: int = 0,
    ) -> Callable[[sqlite3.Connection], tuple[SignedLoan, HousingMove]]:
        """Firma un préstamo personal: lo prestado entra y la comisión de apertura sale.

        Args:
            rate: El tipo que enseñaba la oferta; si el perfil o el Euríbor han cambiado
                entretanto, no se firma.
            income: Ingresos diarios que mira el banco (`housing.daily_income`).
            guarantor: El miembro que lo avala, si lo hay: suma su hueco en el 35 % al de
                quien pide y baja el tipo (`loans.GUARANTOR_DISCOUNT`).
            guarantor_income: Lo que ingresa al día el avalista.

        Raises (dentro de la transacción):
            HousingError: Fichero de morosos, demasiados préstamos, cifras fuera de los
                topes, otro tipo que el de la oferta o una cuota que no cabe en el 35 %.
        """

        def run(connection: sqlite3.Connection) -> tuple[SignedLoan, HousingMove]:
            market = _market_in(connection, guild_id)
            desk = _desk_in(connection, guild_id, user_id, market, payroll)
            if desk.asnef:
                raise HousingError(lending.ASNEF_REFUSAL)
            if (
                guarantor is not None
                and (problem := guarantor_problem(market, user_id, guarantor)) is not None
            ):
                raise HousingError(problem)
            if len(desk.loans) >= lending.MAX_LOANS:
                raise HousingError(
                    f"Ya tienes {lending.MAX_LOANS} préstamos abiertos: devuelve alguno antes."
                )
            if months not in lending.TERMS:
                raise HousingError("Ese plazo no lo ofrece el banco.")
            if not lending.MIN_PRINCIPAL <= principal <= lending.MAX_PRINCIPAL:
                raise HousingError(
                    f"El banco presta de {format_amount(lending.MIN_PRINCIPAL)} a "
                    f"{format_amount(lending.MAX_PRINCIPAL)}."
                )
            factors = lending.profile_factors(
                desk.profile, insured=insured, guaranteed=guarantor is not None
            )
            if lending.loan_rate(desk.euribor, factors) != rate:
                raise HousingError("El tipo ha cambiado desde la oferta: vuelve a mirarla.")
            offer = lending.quote(principal, months, rate, insured=insured)
            if guarantor is not None:
                room = _room_with(market, income, desk.installments, guarantor, guarantor_income)
                if offer.monthly > room:
                    raise HousingError(
                        f"El banco te dice que no: lo de cada mes ({format_amount(offer.monthly)}) "
                        f"no cabe ni sumando lo que le cabe a tu avalista ({format_amount(room)} "
                        "al mes entre los dos)."
                    )
            elif not rules.affordable(income, desk.installments + offer.monthly):
                raise HousingError(
                    f"El banco te dice que no: lo de cada mes ({format_amount(offer.monthly)}) "
                    f"más lo que ya pagas ({format_amount(desk.installments)}) pasa del 35 % "
                    f"de lo que ingresas ({format_amount(income)} al día, nóminas y el 70 % "
                    "de tus alquileres)."
                )
            loan_id = finance.add_loan_in(
                connection, guild_id, user_id, principal=principal, rate=rate,
                payment=offer.payment, months=months, fee=offer.fee, premium=offer.premium,
                premium_tax=offer.premium_tax,
                covers=lending.INSURANCE_COVERS if insured else 0,
                # La primera cuota, la noche siguiente a la de hoy: hasta entonces se desiste.
                first_day=(day + timedelta(days=1)).isoformat(), now=now,
            )  # fmt: skip
            if guarantor is not None:
                connection.execute(
                    "UPDATE housing_loans SET aval = ? WHERE id = ?",
                    (lending.guarantor_tag(guarantor), loan_id),
                )
            loan = finance.loan_in(connection, guild_id, loan_id)
            assert loan is not None
            entries = [(user_id, LedgerEntry(principal, "banco:prestamo"))]
            if offer.fee:
                entries.append((user_id, LedgerEntry(-offer.fee, "banco:apertura")))
            signed = SignedLoan(loan, offer, tuple(factors))
            return signed, HousingMove(entries=tuple(entries))

        return run

    def repay_loan(
        self, guild_id: int, user_id: int, loan_id: int, now: float
    ) -> Callable[[sqlite3.Connection], tuple[LoanClosed, HousingMove]]:
        """Amortiza un préstamo entero: lo que queda, lo que se debe y la compensación.

        Raises (dentro de la transacción):
            HousingError: Si no es suyo o ya está cerrado.
        """

        def run(connection: sqlite3.Connection) -> tuple[LoanClosed, HousingMove]:
            loan = _own_loan_in(connection, guild_id, user_id, loan_id)
            fee = lending.early_fee(loan.balance, loan.months_left)
            finance.close_loan_in(connection, loan.id, "pagado", now, paid=loan.owed + fee)
            entries = [(user_id, LedgerEntry(-loan.owed, "banco:amortizacion"))]
            if fee:
                entries.append((user_id, LedgerEntry(-fee, "banco:compensacion")))
            closed = LoanClosed(loan, "pagado", loan.owed + fee, fee)
            return closed, HousingMove(entries=tuple(entries))

        return run

    def desist_loan(
        self, guild_id: int, user_id: int, loan_id: int, now: float
    ) -> Callable[[sqlite3.Connection], tuple[LoanClosed, HousingMove]]:
        """Desiste de un préstamo antes de la primera cuota: devuelve lo prestado y la
        comisión de apertura vuelve (art. 28 de la Ley 16/2011).

        Raises (dentro de la transacción):
            HousingError: Si no es suyo, ya está cerrado o ya se ha cobrado alguna cuota.
        """

        def run(connection: sqlite3.Connection) -> tuple[LoanClosed, HousingMove]:
            loan = _own_loan_in(connection, guild_id, user_id, loan_id)
            if loan.months_left < loan.months or loan.paid or loan.unpaid_count:
                raise HousingError(
                    "Ya se ha cobrado la primera cuota: no puedes echarte atrás, pero sí "
                    "amortizarlo."
                )
            finance.close_loan_in(connection, loan.id, "desistido", now)
            # La comisión vuelve antes de pedir lo prestado: así basta con tener lo recibido.
            entries = [(user_id, LedgerEntry(-loan.principal, "banco:desistimiento"))]
            if loan.fee:
                entries.insert(0, (user_id, LedgerEntry(loan.fee, "banco:apertura_devuelta")))
            closed = LoanClosed(loan, "desistido", loan.principal - loan.fee)
            return closed, HousingMove(entries=tuple(entries))

        return run

    # -- Financiar compras (💳 Financiar, `bot.services.credit`) ----------------------------

    def finance(
        self,
        guild_id: int,
        user_id: int,
        purchase: credit.Purchase,
        *,
        plan: credit.Plan,
        months: int,
        insured: bool,
        grace: bool,
        rate: float,
        income: int,
        payroll: bool,
        day: date,
        now: float,
    ) -> Callable[[sqlite3.Connection], tuple[SignedLoan, HousingMove]]:
        """Financia una compra: entra lo que cuesta y la compra se paga justo después.

        Lo prestado es la compra más la comisión de apertura (que se financia), así que al
        monedero llega justo lo que cuesta. Es un préstamo más (`housing_loans`, `kind`
        `compra` o `plazos`) y se cobra cada noche como los demás.

        Args:
            rate: El tipo que enseñaba la oferta (solo el plan normal; los otros, 0).
            income: Ingresos diarios que mira el banco (`housing.daily_income`).

        Raises (dentro de la transacción):
            HousingError: Fichero de morosos, demasiadas financiaciones, un plan o un plazo
                que no vale para esa compra, otro tipo que el de la oferta o una cuota que no
                cabe en el 35 % (Paga en 3 no lo mira).
        """

        def run(connection: sqlite3.Connection) -> tuple[SignedLoan, HousingMove]:
            market = _market_in(connection, guild_id)
            desk = _desk_in(connection, guild_id, user_id, market, payroll)
            if desk.asnef:
                raise HousingError(lending.ASNEF_REFUSAL)
            if plan not in credit.plans_for(purchase, day):
                raise HousingError(
                    credit.refusal(purchase.amount)
                    or "Esa oferta ya no está: vuelve a mirar cómo financiarlo."
                )
            if months not in credit.terms(plan, works=purchase.works):
                raise HousingError("Ese plazo no lo ofrece el banco.")
            bnpl = plan is credit.Plan.BNPL
            kind = finance.LOAN_BNPL if bnpl else finance.LOAN_PURCHASE
            cap = credit.MAX_BNPL if bnpl else credit.MAX_FINANCED
            if sum(loan.kind == kind for loan in market.loans_of(user_id)) >= cap:
                what = "compras en Paga en 3" if bnpl else "financiaciones"
                raise HousingError(f"Ya tienes {cap} {what} abiertas: termina alguna antes.")
            factors = lending.profile_factors(desk.profile, insured=insured)
            if plan is credit.Plan.NORMAL and credit.purchase_rate(desk.euribor, factors) != rate:
                raise HousingError("El tipo ha cambiado desde la oferta: vuelve a mirarla.")
            offer = credit.offer(purchase.amount, plan, months, rate, insured=insured, grace=grace)
            quote = offer.quote
            if not bnpl and not rules.affordable(income, desk.installments + quote.monthly):
                raise HousingError(
                    f"El banco te dice que no: lo de cada mes ({format_amount(quote.monthly)}) "
                    f"más lo que ya pagas ({format_amount(desk.installments)}) pasa del 35 % "
                    f"de lo que ingresas ({format_amount(income)} al día, nóminas y el 70 % "
                    "de tus alquileres)."
                )
            wait = credit.GRACE_DAYS if offer.grace else 0
            loan_id = finance.add_loan_in(
                connection, guild_id, user_id, principal=quote.principal, rate=quote.rate,
                payment=quote.payment, months=months, fee=quote.fee, premium=quote.premium,
                premium_tax=quote.premium_tax,
                covers=lending.INSURANCE_COVERS if quote.premium else 0,
                first_day=(day + timedelta(days=1 + wait)).isoformat(), now=now, kind=kind,
                listing=purchase.label[:100], balance=offer.balance,
            )  # fmt: skip
            loan = finance.loan_in(connection, guild_id, loan_id)
            assert loan is not None
            entries = [(user_id, LedgerEntry(quote.principal, "banco:financiacion"))]
            if quote.fee:
                entries.append((user_id, LedgerEntry(-quote.fee, "banco:apertura")))
            signed = SignedLoan(loan, quote, tuple(factors))
            return signed, HousingMove(entries=tuple(entries))

        return run

    async def credit_alive(self, guild_id: int) -> tuple[int, int]:
        """`(lo que se debe de financiaciones, el dinero de los miembros)` de un servidor,
        para el IPC del crédito (`credit.ipc_pct`)."""
        return await self._run(self._credit_alive_sync, guild_id)

    def _credit_alive_sync(self, guild_id: int) -> tuple[int, int]:
        connection = self._connect()
        try:
            return _credit_alive_in(connection, guild_id)
        finally:
            connection.close()

    # -- Préstamos al negocio (`banco`, 🏪 Negocio) -------------------------------------
    #
    # Las de `_in` corren dentro de la transacción del negocio (`EconomyService.business`),
    # que mueve el dinero de su caja: la del dueño si es autónomo, la de su sociedad si no.

    async def business_desk(
        self,
        guild_id: int,
        user_id: int,
        business_id: int,
        *,
        income: int,
        payroll: bool,
        company_income: int | None,
    ) -> BusinessLoanDesk:
        """Lo que ve el banco para prestar a un negocio (ver `BusinessLoanDesk`)."""

        def read() -> BusinessLoanDesk:
            connection = self._connect()
            try:
                return self.business_desk_in(
                    connection, guild_id, user_id, business_id, income=income, payroll=payroll,
                    company_income=company_income,
                )  # fmt: skip
            finally:
                connection.close()

        return await self._run(read)

    async def business_loans(self, guild_id: int, business_id: int) -> list[Loan]:
        """Los préstamos vivos de un negocio (para su patrimonio neto)."""

        def read() -> list[Loan]:
            connection = self._connect()
            try:
                return finance.business_loans_in(connection, guild_id, business_id)
            finally:
                connection.close()

        return await self._run(read)

    def business_desk_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        business_id: int,
        *,
        income: int,
        payroll: bool,
        company_income: int | None,
    ) -> BusinessLoanDesk:
        """`business_desk` dentro de una conexión abierta."""
        market = _market_in(connection, guild_id)
        return BusinessLoanDesk(
            owner=_desk_in(connection, guild_id, user_id, market, payroll),
            loans=tuple(finance.business_loans_in(connection, guild_id, business_id)),
            income=income,
            company_income=company_income,
        )

    def take_business_loan_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        business_id: int,
        *,
        principal: int,
        months: int,
        rate: float,
        income: int,
        payroll: bool,
        company_income: int | None,
        day: date,
        now: float,
    ) -> tuple[Loan, lending.Quote]:
        """Firma un préstamo al negocio `business_id` (el dinero lo mueve quien llama).

        Args:
            rate: El tipo de la oferta; si el perfil o el Euríbor han cambiado, no se firma.
            company_income: Lo que deja su sociedad al día, o `None` si es de un autónomo.

        Raises (dentro de la transacción):
            HousingError: Fichero de morosos, demasiados préstamos del negocio, cifras fuera
                de los topes, otro tipo que el de la oferta o una cuota que no cabe.
        """
        desk = self.business_desk_in(
            connection, guild_id, user_id, business_id, income=income, payroll=payroll,
            company_income=company_income,
        )  # fmt: skip
        if desk.owner.asnef:
            raise HousingError(lending.ASNEF_REFUSAL)
        if len(desk.loans) >= lending.MAX_BUSINESS_LOANS:
            raise HousingError(
                f"Tu negocio ya tiene {lending.MAX_BUSINESS_LOANS} préstamos: devuelve alguno "
                "antes."
            )
        if months not in lending.TERMS:
            raise HousingError("Ese plazo no lo ofrece el banco.")
        if not lending.MIN_PRINCIPAL <= principal <= lending.MAX_PRINCIPAL:
            raise HousingError(
                f"El banco presta de {format_amount(lending.MIN_PRINCIPAL)} a "
                f"{format_amount(lending.MAX_PRINCIPAL)}."
            )
        if desk.rate != rate:
            raise HousingError("El tipo ha cambiado desde la oferta: vuelve a mirarla.")
        offer = lending.quote(principal, months, rate, insured=False)
        if offer.monthly > desk.room:
            raise HousingError(
                f"El banco te dice que no: la cuota ({format_amount(offer.monthly)}) no cabe en "
                f"lo que le queda a tu 35 % ({format_amount(desk.room)} al mes)."
            )
        loan_id = finance.add_loan_in(
            connection, guild_id, user_id, principal=principal, rate=rate,
            payment=offer.payment, months=months, fee=offer.fee, premium=0, premium_tax=0,
            covers=0, first_day=(day + timedelta(days=1)).isoformat(), now=now,
            kind=finance.LOAN_COMPANY if desk.corporate else finance.LOAN_BUSINESS,
            listing=str(business_id),
        )  # fmt: skip
        loan = finance.loan_in(connection, guild_id, loan_id)
        assert loan is not None
        return loan, offer

    @staticmethod
    def repay_business_loan_in(
        connection: sqlite3.Connection, guild_id: int, business_id: int, loan_id: int,
        now: float,
    ) -> tuple[Loan, int]:  # fmt: skip
        """Cierra entero un préstamo del negocio. Devuelve el préstamo y la compensación.

        Raises:
            HousingError: Si no es de ese negocio o ya está cerrado.
        """
        loan = finance.loan_in(connection, guild_id, loan_id)
        if loan is None or loan.business_id != business_id:
            raise HousingError("Ese préstamo no es de tu negocio.")
        if loan.status != "activo":
            raise HousingError("Ese préstamo ya está cerrado.")
        fee = lending.early_fee(loan.balance, loan.months_left)
        finance.close_loan_in(connection, loan.id, "pagado", now, paid=loan.owed + fee)
        return loan, fee

    @staticmethod
    def business_night_in(
        connection: sqlite3.Connection, guild_id: int, business_id: int, day: date
    ) -> list[BusinessInstallment]:
        """Las cuotas de esta noche de los préstamos de un negocio (las que ya tocan)."""
        out = []
        for loan in finance.business_loans_in(connection, guild_id, business_id):
            if day.isoformat() < loan.first_day:
                continue
            pay = loan.payment if loan.months_left > 0 else 0
            interest, principal = rules.mortgage_month(loan.balance, loan.rate, pay)
            out.append(BusinessInstallment(loan, loan.balance - principal, pay, min(interest, pay)))
        return out

    @staticmethod
    def settle_business_night_in(
        connection: sqlite3.Connection,
        guild_id: int,
        installments: list[BusinessInstallment],
        missing: dict[int, int],
        now: float,
    ) -> list[tuple[str, Loan, int]]:
        """Guarda cómo quedan los préstamos de un negocio tras cobrar su noche.

        Lo que no llegó se debe y cuenta como una cuota seguida sin pagar (sin seguro: no es
        crédito al consumo). Con `DEFAULT_INSTALLMENTS`, vence: lo que queda pasa a deuda con
        el banco de su dueño, que lo firmó como autónomo o lo avala como socio.

        Args:
            missing: Lo que no llegó de cada préstamo, por su id.

        Returns:
            `(qué, préstamo, importe)` de cada uno: `pagada` o `sin_pagar` (con lo pagado),
            `devuelto` (la última cuota) o `vencido` (con lo que pasa a deber el dueño).
        """
        out = []
        for item in installments:
            loan = item.loan
            lost = missing.get(loan.id, 0)
            unpaid_count = loan.unpaid_count + 1 if lost else 0
            finance.save_loan_in(
                connection, loan, balance=item.balance,
                months_left=max(0, loan.months_left - bool(item.pay)), covers_left=0,
                unpaid_count=unpaid_count, unpaid_amount=lost,
                paid=loan.paid + item.due - lost,
                interest_paid=loan.interest_paid + (item.interest if not lost else 0),
            )  # fmt: skip
            if not item.balance and not lost:
                finance.close_loan_in(connection, loan.id, "pagado", now)
                out.append(("devuelto", loan, item.due))
            elif unpaid_count >= lending.DEFAULT_INSTALLMENTS:
                owed = item.balance + lost
                finance.add_bank_debt_in(connection, guild_id, loan.user_id, owed)
                finance.close_loan_in(connection, loan.id, "vencido", now)
                out.append(("vencido", loan, owed))
            else:
                out.append(("sin_pagar" if lost else "pagada", loan, item.due - lost))
        return out

    @staticmethod
    def business_loans_to_owner_in(
        connection: sqlite3.Connection,
        guild_id: int,
        business_id: int,
        now: float,
        paid: int = 0,
    ) -> tuple[int, int]:
        """Al cerrar un negocio (o al hacerlo sociedad): lo que no se cancela pasa a ser un
        préstamo personal del dueño.

        Del autónomo ya eran suyos (`paid` = 0): siguen igual, ahora en las noches de `pisos`.
        También al constituir la sociedad: el dinero se lo quedó él y el banco no ha consentido
        que cambie el deudor (art. 1205 CC), así que la sociedad empieza sin deudas con él.
        De una sociedad que se liquida, `paid` es lo que pagó su caja: cancela los préstamos
        del más antiguo al más nuevo, y lo que falte lo asume su socio, que los avalaba
        (asunción de deuda con el mismo tipo y las cuotas que quedaban, art. 1205 CC).

        Returns:
            `(lo cancelado, lo que pasa al dueño)`.
        """
        left = max(0, paid)
        cancelled = moved = 0
        for loan in finance.business_loans_in(connection, guild_id, business_id):
            take = min(left, loan.owed)
            left -= take
            cancelled += take
            rest = loan.owed - take
            if not rest:
                finance.close_loan_in(connection, loan.id, "pagado", now, paid=take)
                continue
            moved += rest
            months = max(1, loan.months_left)
            # Si la caja pagó una parte o había cuotas atrasadas, la cuota se rehace con lo que
            # queda y los mismos meses.
            payment = (
                rules.installment(rest, loan.rate, months)
                if take or loan.unpaid_amount or not loan.months_left
                else loan.payment
            )
            connection.execute(
                """
                UPDATE housing_loans SET kind = ?, listing = NULL, balance = ?, unpaid_amount = 0,
                    unpaid_count = 0, months_left = ?, payment = ?, paid = paid + ?
                WHERE id = ?
                """,
                (finance.LOAN_PERSONAL, rest, months, payment, take, loan.id),
            )
        return cancelled, moved

    @staticmethod
    def move_business_loans_in(
        connection: sqlite3.Connection, guild_id: int, business_id: int, to_id: int
    ) -> int:
        """Pasa los préstamos vivos de un local de una sociedad que cierra a otro de sus locales
        (fase 5 de los negocios): la deudora es la sociedad, que sigue, así que no cambian.

        Returns:
            Cuántos préstamos ha pasado.
        """
        cursor = connection.execute(
            "UPDATE housing_loans SET listing = ? WHERE guild_id = ? AND status = 'activo' "
            "AND kind = ? AND listing = ?",
            (str(to_id), guild_id, finance.LOAN_COMPANY, str(business_id)),
        )
        return cursor.rowcount

    # -- Rehipotecar (`banco`, 🔐 Garantías) --------------------------------------------

    async def bubble_gauge(self, guild_id: int, day: date) -> tuple[int, int]:
        """`(obra empezada, lo comprado)` de las últimas semanas: el termómetro de la burbuja."""
        return await self._run(self._bubble_gauge_sync, guild_id, day)

    def _bubble_gauge_sync(self, guild_id: int, day: date) -> tuple[int, int]:
        connection = self._connect()
        try:
            market = Market(solars=finance.solars_in(connection, guild_id))
            _recent, supply, demand = _bubble_flows_in(connection, guild_id, day, market)
            return supply, demand
        finally:
            connection.close()

    async def remortgage_desk(self, guild_id: int, user_id: int) -> RemortgageDesk:
        """Lo que ve el banco de alguien para rehipotecar sus inmuebles."""
        return await self._run(self._remortgage_desk_sync, guild_id, user_id)

    def _remortgage_desk_sync(self, guild_id: int, user_id: int) -> RemortgageDesk:
        connection = self._connect()
        try:
            return _remortgage_desk_in(_market_in(connection, guild_id), user_id)
        finally:
            connection.close()

    def remortgage(
        self,
        guild_id: int,
        user_id: int,
        key: str,
        *,
        principal: int,
        months: int,
        rate: float,
        income: int,
        day: date,
        now: float,
        guarantor: int | None = None,
        guarantor_income: int = 0,
    ) -> Callable[[sqlite3.Connection], tuple[SignedRemortgage, HousingMove]]:
        """Firma una rehipoteca: una hipoteca nueva sobre lo que ya se tiene de un inmueble.

        Lo prestado entra (se crea: el banco no existe) y salen la comisión de apertura y la
        tasación (desaparecen) con el IGIC de la tasación (al Estado). La tasación es lo que
        valen hoy sus participaciones (`Market.value`); presta hasta el 80 % si es vivienda y
        el 60 % si no, menos lo que ya debe de ese inmueble (`loans.remortgage_room`).

        Args:
            rate: El tipo que enseñaba la oferta; si el Euríbor ha cambiado, no se firma.
            income: Ingresos diarios que mira el banco (`housing.daily_income`).
            guarantor: El miembro que la avala, si lo hay (suma su hueco en el 35 % y baja
                el tipo).
            guarantor_income: Lo que ingresa al día el avalista.

        Raises (dentro de la transacción):
            HousingError: Fichero de morosos, nada de ese inmueble, en subasta, cifras fuera
                de los topes, otro tipo que el de la oferta, una cuota que no cabe en el 35 %
                o un avalista que no puede avalar.
        """

        def run(connection: sqlite3.Connection) -> tuple[SignedRemortgage, HousingMove]:
            market = _market_in(connection, guild_id)
            desk = _remortgage_desk_in(market, user_id)
            if desk.asnef:
                raise HousingError(lending.ASNEF_REFUSAL)
            if (
                guarantor is not None
                and (problem := guarantor_problem(market, user_id, guarantor)) is not None
            ):
                raise HousingError(problem)
            found = next((c for c in desk.collaterals if c.key == key), None)
            if found is None:
                raise HousingError("No tienes nada en ese inmueble.")
            if found.blocked is not None:
                raise HousingError(found.blocked)
            if months not in lending.REMORTGAGE_TERMS:
                raise HousingError("Ese plazo no lo ofrece el banco.")
            expected = lending.remortgage_rate(desk.euribor, guaranteed=guarantor is not None)
            if expected != rate:
                raise HousingError("El Euríbor ha cambiado desde la oferta: vuelve a mirarla.")
            if principal < lending.REMORTGAGE_MIN:
                raise HousingError(
                    f"El banco rehipoteca desde {format_amount(lending.REMORTGAGE_MIN)}."
                )
            if principal > found.room:
                raise HousingError(
                    f"Sobre eso el banco presta como mucho {format_amount(found.room)}: el "
                    f"{'80' if found.dwelling else '60'} % de lo que vale hoy menos lo que ya "
                    "debes de él."
                )
            offer = lending.remortgage_quote(principal, months, rate, found.value)
            if offer.received <= 0:
                raise HousingError("Con la comisión y la tasación no te llegaría nada.")
            if guarantor is not None:
                room = _room_with(market, income, desk.installments, guarantor, guarantor_income)
                if offer.payment > room:
                    raise HousingError(
                        f"El banco te dice que no: la cuota ({format_amount(offer.payment)} al "
                        f"mes) no cabe ni sumando lo que le cabe a tu avalista "
                        f"({format_amount(room)} al mes entre los dos)."
                    )
            elif not rules.affordable(income, desk.installments + offer.payment):
                raise HousingError(
                    f"El banco te dice que no: la cuota ({format_amount(offer.payment)} al mes) "
                    f"más lo que ya pagas ({format_amount(desk.installments)}) pasa del 35 % de "
                    f"lo que ingresas ({format_amount(income)} al día, nóminas y el 70 % de tus "
                    "alquileres)."
                )
            mortgage_id = finance.add_mortgage_in(
                connection, guild_id, user_id, key, principal, rate, offer.payment, now,
                aval=lending.guarantor_tag(guarantor) if guarantor is not None else None,
                months=months, kind=finance.MORTGAGE_REMORTGAGE,
            )  # fmt: skip
            mortgage = next(
                m for m in finance.mortgages_in(connection, guild_id, user_id)
                if m.id == mortgage_id
            )  # fmt: skip
            _news_in(
                connection, guild_id, News(key, day, "rehipoteca", principal, user_id, None), now
            )
            entries = [
                (user_id, LedgerEntry(principal, "banco:rehipoteca")),
                (user_id, LedgerEntry(-offer.fee, "banco:apertura")),
                (user_id, LedgerEntry(-offer.appraisal, "banco:tasacion")),
            ]
            taxes = (HousingTax(user_id, "igic_gastos", offer.appraisal, offer.appraisal_igic),)
            signed = SignedRemortgage(mortgage, offer)
            return signed, HousingMove(entries=tuple(entries), taxes=taxes)

        return run

    # -- Anticipo de rentas (`banco`, 💸 Anticipos) ------------------------------------

    async def advance_desk(self, guild_id: int, user_id: int, day: date) -> AdvanceDesk:
        """Lo que ve el banco de alguien para adelantarle las rentas de sus inmuebles."""
        return await self._run(self._advance_desk_sync, guild_id, user_id, day)

    def _advance_desk_sync(self, guild_id: int, user_id: int, day: date) -> AdvanceDesk:
        connection = self._connect()
        try:
            return _advance_desk_in(_market_in(connection, guild_id), user_id, day)
        finally:
            connection.close()

    def advance(
        self,
        guild_id: int,
        user_id: int,
        key: str,
        *,
        months: int,
        rate: float,
        day: date,
        now: float,
    ) -> Callable[[sqlite3.Connection], tuple[SignedAdvance, HousingMove]]:
        """Firma un anticipo: el banco adelanta `months` cuotas de tu parte de la renta.

        Es un préstamo más de `housing_loans` (`kind = 'rentas'` y su inmueble en `listing`),
        sin seguro, con la primera cuota la noche siguiente (hasta entonces se desiste, Ley
        16/2011) y que se cobra cada noche como cualquier otro: si el inquilino no paga, lo
        pone el dueño (con recurso). Entra lo adelantado (`banco:anticipo`, se crea: el banco
        no existe) y sale la comisión de apertura (`banco:apertura`, desaparece).

        Fiscalidad: lo adelantado no es renta (es un préstamo, se devuelve); la renta del
        inmueble sigue tributando cada noche al devengarse (art. 14.1.a LIRPF), no al
        adelantarla; el coste del anticipo no deduce, porque no es interés del dinero invertido
        en comprar o mejorar el inmueble (art. 23.1.a.1.º LIRPF); el interés y la comisión,
        exentos de IGIC como operaciones financieras (art. 18 del Decreto Legislativo 1/2025
        de Canarias). No mira el 35 %: la cuota la paga el inquilino.

        Args:
            rate: El tipo que enseñaba la oferta; si el Euríbor ha cambiado, no se firma.
            day: Hoy (para los meses que le quedan al contrato).

        Raises (dentro de la transacción):
            HousingError: Fichero de morosos, inmueble que no vale (ver `_advance_desk_in`),
                plazo que no cabe en el contrato u otro tipo que el de la oferta.
        """

        def run(connection: sqlite3.Connection) -> tuple[SignedAdvance, HousingMove]:
            market = _market_in(connection, guild_id)
            desk = _advance_desk_in(market, user_id, day)
            if desk.asnef:
                raise HousingError(lending.ASNEF_REFUSAL)
            found = next((x for x in desk.shares if x.key == key), None)
            if found is None:
                raise HousingError("No tienes nada en ese inmueble.")
            if found.blocked is not None:
                raise HousingError(f"Sobre ese inmueble no se puede: {found.blocked}.")
            if months not in lending.ADVANCE_TERMS:
                raise HousingError("Ese plazo no lo ofrece el banco.")
            if months > found.months_left:
                raise HousingError(
                    f"Al contrato le quedan {found.months_left} meses: no se pueden adelantar "
                    f"{months}."
                )
            if desk.rate != rate:
                raise HousingError("El Euríbor ha cambiado desde la oferta: vuelve a mirarla.")
            offer = lending.advance_quote(found.payment, months, rate)
            if offer.received <= 0:
                raise HousingError("Con la comisión no te llegaría nada.")
            loan_id = finance.add_loan_in(
                connection, guild_id, user_id, principal=offer.principal, rate=rate,
                payment=offer.payment, months=months, fee=offer.fee, premium=0, premium_tax=0,
                covers=0, first_day=(day + timedelta(days=1)).isoformat(), now=now,
                kind=finance.LOAN_ADVANCE, listing=key,
            )  # fmt: skip
            loan = finance.loan_in(connection, guild_id, loan_id)
            assert loan is not None
            _news_in(
                connection, guild_id, News(key, day, "anticipo", offer.principal, user_id, None),
                now,
            )  # fmt: skip
            entries = [(user_id, LedgerEntry(offer.principal, "banco:anticipo"))]
            if offer.fee:
                entries.append((user_id, LedgerEntry(-offer.fee, "banco:apertura")))
            return SignedAdvance(loan, offer), HousingMove(entries=tuple(entries))

        return run

    async def bank_book(self, guild_id: int) -> BankBook:
        """Las cuentas del banco de un servidor: lo prestado, lo cobrado y lo que se debe."""
        return await self._run(self._bank_book_sync, guild_id)

    def _bank_book_sync(self, guild_id: int) -> BankBook:
        connection = self._connect()
        try:
            market = _market_in(connection, guild_id)
            lent, interest, fees, premiums = connection.execute(
                """
                SELECT COALESCE(SUM(principal), 0), COALESCE(SUM(interest_paid), 0),
                    COALESCE(SUM(CASE WHEN status = 'desistido' THEN 0 ELSE fee END), 0),
                    COALESCE(SUM(premium * (months - months_left)), 0)
                FROM housing_loans WHERE guild_id = ? AND status != 'desistido'
                """,
                (guild_id,),
            ).fetchone()
            (defaults,) = connection.execute(
                """
                SELECT (SELECT COUNT(*) FROM housing_loans WHERE guild_id = :g
                        AND status = 'vencido')
                     + (SELECT COUNT(*) FROM housing_mortgages WHERE guild_id = :g
                        AND status = 'ejecutada')
                """,
                {"g": guild_id},
            ).fetchone()
        finally:
            connection.close()
        members = {m.user_id for m in market.mortgages} | {loan.user_id for loan in market.loans}
        members |= set(market.bank_debts)
        return BankBook(
            mortgages=len(market.mortgages),
            mortgage_balance=sum(m.balance for m in market.mortgages),
            remortgages=sum(1 for m in market.mortgages if m.remortgage),
            remortgage_balance=sum(m.balance for m in market.mortgages if m.remortgage),
            loans=len(market.loans),
            loan_balance=sum(loan.balance for loan in market.loans),
            lent=int(lent),
            loan_interest=int(interest),
            loan_fees=int(fees),
            premiums=int(premiums),
            unpaid=sum(m.unpaid_amount for m in market.mortgages)
            + sum(loan.unpaid_amount for loan in market.loans),
            bank_debts=sum(market.bank_debts.values()),
            defaults=int(defaults),
            asnef=sum(1 for member in members if market.in_asnef(member)),
        )

    async def bank_exposure(self, guild_id: int) -> tuple[int, int]:
        """`(lo prestado en hipotecas de compra, lo que le deben ahora)` al banco. Solo lee.

        Lo primero es lo que el banco ha prestado sin pasar por el monedero de nadie (paga
        al vendedor o al catálogo): el capital de todas las hipotecas de compra, también las
        ya canceladas o ejecutadas. Las rehipotecas no, porque entran en el monedero
        (`banco:rehipoteca`). Lo segundo, lo que le deben hoy: el capital pendiente y las
        cuotas sin pagar de hipotecas y préstamos (también los de los negocios) y la deuda
        tras una ejecución o un vencimiento. Es lo que necesita
        `bot.services.house_books.BankBook`.
        """
        return await self._run(self._bank_exposure_sync, guild_id)

    def _bank_exposure_sync(self, guild_id: int) -> tuple[int, int]:
        connection = self._connect()
        try:
            (lent,) = connection.execute(
                "SELECT COALESCE(SUM(principal), 0) FROM housing_mortgages "
                "WHERE guild_id = ? AND kind = ?",
                (guild_id, finance.MORTGAGE_PURCHASE),
            ).fetchone()
            mortgages = finance.mortgages_in(connection, guild_id)
            loans = finance.loans_in(connection, guild_id)
            (debts,) = connection.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM housing_bank_debts WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()
        finally:
            connection.close()
        owed = sum(m.owed for m in mortgages) + sum(loan.owed for loan in loans) + int(debts)
        return int(lent), owed

    async def last_day(self, guild_id: int) -> date | None:
        """Último día cerrado en el servidor, o `None` si nunca se cerró ninguno."""
        return await self._run(self._last_day_sync, guild_id)

    def _last_day_sync(self, guild_id: int) -> date | None:
        connection = self._connect()
        try:
            (day,) = connection.execute(
                "SELECT MAX(day) FROM housing_days WHERE guild_id = ?", (guild_id,)
            ).fetchone()
        finally:
            connection.close()
        return _date(day)

    # -- Comprar y vender ---------------------------------------------------------------

    def buy(
        self,
        guild_id: int,
        user_id: int,
        key: str,
        shares: int,
        day: date,
        now: float,
        *,
        expected: int | None = None,
        financed: bool = False,
        income: int = 0,
        aval_income: int = 0,
    ) -> Callable[[sqlite3.Connection], tuple[Purchase, HousingMove]]:
        """Compra participaciones de un inmueble al índice de hoy, al contado o con hipoteca.

        Args:
            expected: Lo que decía la caja (lo que sale del bolsillo). Si ha subido
                entretanto, no se cobra.
            financed: Con hipoteca: el banco presta el 80 % del precio al Euríbor de la
                semana más el diferencial, a 360 meses.
            income: Ingresos diarios que mira el banco (`housing.daily_income`).
            aval_income: Lo que suma el aval de tus padres si lo tienes
                (`housing.parents_income`). Solo se usa si sin él no llega: entonces la
                hipoteca queda avalada.

        Raises (dentro de la transacción):
            HousingError: Si no quedan tantas libres, el número no vale o el banco no
                presta (la cuota pasa del 35 % de lo que ingresa).
            PriceChanged: Si lo que sale del bolsillo ya no es `expected`.
        """

        def run(connection: sqlite3.Connection) -> tuple[Purchase, HousingMove]:
            market = _market_in(connection, guild_id)
            listing = _whole(market, key)
            free = market.free_shares(key)
            if shares < 1:
                raise HousingError("Tienes que comprar al menos una participación.")
            if free <= 0:
                raise HousingError("Ya no queda ninguna participación libre de este inmueble.")
            if shares > free:
                raise HousingError(f"Solo quedan {free} participaciones libres.")
            index = market.index(listing.barrio)
            quote = rules.purchase_quote(listing, shares, index)
            loan = payment = 0
            rate = 0.0
            aval = False
            if financed:
                loan = round(quote.price * rules.MORTGAGE_LTV)
                rate = market.euribor + rules.MORTGAGE_SPREAD
                payment = rules.installment(loan, rate)
                if market.in_asnef(user_id):
                    raise HousingError(lending.ASNEF_REFUSAL)
                current = market.installments_of(user_id)
                if not rules.affordable(income, current + payment):
                    aval = aval_income > 0 and rules.affordable(
                        income + aval_income, current + payment
                    )
                    if not aval:
                        tip = (
                            " Ni con el aval de tus padres."
                            if aval_income
                            else " Trabaja más, pide menos o pide a tus padres que te avalen "
                            "(un favor de `pala`)."
                        )
                        raise HousingError(
                            f"El banco te dice que no: la cuota ({format_amount(payment)} al "
                            f"mes) más las que ya pagas ({format_amount(current)}) pasa del 35 % "
                            f"de lo que ingresas ({format_amount(income)} al día, nóminas y el "
                            f"70 % de tus alquileres).{tip}"
                        )
                finance.add_mortgage_in(
                    connection, guild_id, user_id, key, loan, rate, payment, now,
                    aval=rules.PARENTS_AVAL if aval else None,
                )  # fmt: skip
            out_of_pocket = quote.total - loan
            # Si sale más barato que en la caja se cobra (mejor para quien compra);
            # si sale más caro, no.
            if expected is not None and out_of_pocket > expected:
                raise PriceChanged(out_of_pocket)
            first = not any(user_id in h for h in market.holdings.values())
            held = market.holdings.get(key, {}).get(user_id)
            promotion = market.promotions.get(key)
            promoter = promotion.promoter if promotion is not None else None
            fee = rules.promoter_fee(quote.price) if promoter not in (None, user_id) else 0
            connection.execute(
                """
                INSERT INTO housing_holdings (guild_id, listing, user_id, shares, paid, cost, since)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (guild_id, listing, user_id) DO UPDATE SET
                    shares = shares + excluded.shares, paid = paid + excluded.paid,
                    cost = cost + excluded.cost
                """,
                (guild_id, key, user_id, shares, quote.price, quote.total, now),
            )
            if key not in market.leases:
                _save_lease_in(
                    connection, guild_id, key, rules.Lease(level=DEFAULT_RENT_LEVEL, since=day)
                )
            after = rules.index_after(index, listing, shares, bought=True)
            _set_index_in(connection, guild_id, listing.barrio, after)
            _news_in(
                connection, guild_id, News(key, day, "compra", quote.price, user_id, None), now
            )
            if loan:
                _news_in(connection, guild_id, News(key, day, "hipoteca", loan, user_id, None), now)
            purchase = Purchase(
                listing=key,
                quote=quote,
                shares=(held.shares if held else 0) + shares,
                index_before=index,
                index_after=after,
                first=first,
                loan=loan,
                payment=payment,
                rate=rate,
                aval=aval,
                promoter=promoter,
                promoter_fee=fee,
            )
            # La comisión del promotor sale del precio, que si no desaparecería: no crea
            # dinero. Rendimiento de su actividad (art. 27 LIRPF), con retención a cuenta.
            fees = (
                (HousingRent(promoter, fee, kind=INCOME_BUSINESS, concept=PROMOTER_CONCEPT),)
                if promoter is not None and fee
                else ()
            )
            move = HousingMove(
                rents=fees,
                entries=(
                    (user_id, LedgerEntry(-(quote.price - loan), "pisos:compra")),
                    (user_id, LedgerEntry(-quote.notary, "pisos:notaria")),
                ),
                taxes=(
                    HousingTax(user_id, "itp", quote.price, quote.itp),
                    HousingTax(user_id, "igic_gastos", quote.notary, quote.notary_igic),
                ),
            )
            return purchase, move

        return run

    def sell(
        self,
        guild_id: int,
        user_id: int,
        key: str,
        shares: int,
        day: date,
        now: float,
        *,
        expected: int | None = None,
        vulture: bool = False,
        forced: bool = False,
    ) -> Callable[[sqlite3.Connection], tuple[Sale, HousingMove]]:
        """Vende participaciones al mercado al índice de hoy (o al fondo buitre).

        Lo cobrado cancela primero la parte de su hipoteca que corresponde a lo vendido
        (cancelación registral) y las deudas del piso. Si lo vende todo, deja de vivir
        en él; si había un miembro de alquiler y no queda ningún dueño, tiene que irse.

        Args:
            expected: Lo que decía la caja que cobraría. Si ha bajado, no se vende.
            vulture: Vender entero al fondo buitre al precio de su oferta.
            forced: Si la venta no la decide él (la junta o una división sin pujas): un
                anticipo de rentas vivo no la para y sigue debiéndose.

        Raises (dentro de la transacción):
            HousingError: Si no tiene tantas, si tiene un anticipo de rentas vivo sobre el
                inmueble (venta voluntaria) o si la oferta no existe o caducó.
            PriceChanged: Si lo que cobraría ya no es `expected`.
        """

        def run(connection: sqlite3.Connection) -> tuple[Sale, HousingMove]:
            market = _market_in(connection, guild_id)
            listing = _whole(market, key)
            # shortcut: lo que deben las habitaciones no entra en la cuenta de la venta; mientras
            # deban, el piso no vuelve al mercado (a otro miembro sí: la deuda sigue al piso).
            rooms = [market.leases.get(u.key, rules.Lease()) for u in market.units_of(key)]
            if any(room.arrears for room in rooms):
                raise HousingError(
                    "Las habitaciones deben gastos: hasta que se paguen, el piso no vuelve al "
                    "mercado."
                )
            held = market.holdings.get(key, {}).get(user_id)
            if held is None:
                raise HousingError("No tienes nada en este inmueble.")
            if not 1 <= shares <= held.shares:
                raise HousingError(f"Tienes {held.shares} participaciones: no puedes vender más.")
            _check_free(market, key, user_id, day, market_sale=True)
            if not forced:
                _check_no_advance(market, key, user_id)
                _check_reclaim(market, key, user_id)
            lease = market.leases.get(key, rules.Lease())
            index = market.index(listing.barrio)
            agreed = None
            if vulture:
                offer = market.offers.get((key, user_id))
                if offer is None or offer[1] < day:
                    raise HousingError("El fondo ya no tiene ninguna oferta por este inmueble.")
                if shares != held.shares:
                    raise HousingError("El fondo lo quiere entero o nada.")
                agreed = offer[0]
            quote = rules.sale_quote(
                listing, shares, held.shares, held.cost, index=index,
                arrears=lease.arrears, arrears_ibi=lease.arrears_ibi,
                pool=sum(market.shares_of(key).values()), value=agreed,
            )  # fmt: skip
            mortgages = market.mortgages_of(user_id, key)
            payoff = sum(m.owed for m in mortgages) * shares // held.shares
            if expected is not None and quote.received - payoff < expected:
                raise PriceChanged(quote.received - payoff)
            left = held.shares - shares
            _shrink_mortgages_in(connection, market, user_id, key, shares, held.shares)
            if left:
                connection.execute(
                    """
                    UPDATE housing_holdings SET shares = ?, paid = ?, cost = ?
                    WHERE guild_id = ? AND listing = ? AND user_id = ?
                    """,
                    (left, held.paid - held.paid * shares // held.shares, held.cost - quote.cost,
                     guild_id, key, user_id),
                )  # fmt: skip
            else:
                connection.execute(
                    "DELETE FROM housing_holdings "
                    "WHERE guild_id = ? AND listing = ? AND user_id = ?",
                    (guild_id, key, user_id),
                )
            lease = replace(
                lease,
                arrears=lease.arrears - quote.arrears,
                arrears_ibi=lease.arrears_ibi - quote.arrears_ibi,
            )
            if market.homes.get(user_id) == (finance.HOME_OWN, key):
                # Vender (aunque sea una parte) la casa en la que vives: te mudas.
                finance.clear_home_in(connection, guild_id, user_id=user_id)
                lease = replace(lease, status=rules.VACANT, since=day)
            others = sum(s for m, s in market.shares_of(key).items() if m != user_id)
            if left or others:
                _save_lease_in(connection, guild_id, key, lease)
            else:
                # Sin dueños, el inmueble vuelve al mercado sin deudas. Si vivía un
                # miembro de alquiler, el comprador (el mercado) no le renueva.
                if lease.member is not None:
                    finance.clear_home_in(connection, guild_id, user_id=lease.member)
                connection.execute(
                    "DELETE FROM housing_leases WHERE guild_id = ? AND listing = ?", (guild_id, key)
                )
                _clear_reviews_in(connection, guild_id, key)
                _drop_rooms_in(connection, guild_id, market, key)
                connection.execute(
                    "DELETE FROM housing_candidates WHERE guild_id = ? AND listing = ?",
                    (guild_id, key),
                )
            connection.execute(
                "DELETE FROM housing_offers WHERE guild_id = ? AND listing = ? AND user_id = ?",
                (guild_id, key, user_id),
            )
            _set_index_in(
                connection,
                guild_id,
                listing.barrio,
                rules.index_after(index, listing, shares, bought=False),
            )
            kind = "venta_fondo" if vulture else "venta"
            _news_in(connection, guild_id, News(key, day, kind, quote.price, user_id, None), now)
            held_days = max(0, int((now - held.since) // 86_400))
            entries: list[tuple[int, LedgerEntry]] = [
                (user_id, LedgerEntry(quote.price, "pisos:venta")),
                (user_id, LedgerEntry(-quote.fee, "pisos:agencia")),
                (user_id, LedgerEntry(-(quote.arrears - quote.arrears_ibi), "pisos:comunidad")),
                (user_id, LedgerEntry(-payoff, "pisos:hipoteca_cancelada")),
            ]
            taxes = (
                HousingTax(user_id, "ibi", quote.arrears_ibi, quote.arrears_ibi),
                HousingTax(user_id, "igic_gastos", quote.fee, quote.fee_igic),
                HousingTax(user_id, "plusvalia", max(0, quote.gain), quote.gain_tax),
            )
            sale = Sale(key, quote, left, held_days, payoff=payoff, vulture=vulture)
            return sale, HousingMove(entries=tuple(entries), taxes=taxes)

        return run

    def amortize(
        self, guild_id: int, user_id: int, mortgage_id: int, day: date, now: float
    ) -> Callable[[sqlite3.Connection], tuple[int, HousingMove]]:
        """Cancela una hipoteca de golpe con el dinero del monedero.

        Sin comisión: la Ley 5/2019 la limita y aquí no se cobra (decisión del proyecto).

        Returns (dentro de la transacción):
            Lo pagado.
        """

        def run(connection: sqlite3.Connection) -> tuple[int, HousingMove]:
            mortgage = next(
                (m for m in finance.mortgages_in(connection, guild_id, user_id)
                 if m.id == mortgage_id),
                None,
            )  # fmt: skip
            if mortgage is None:
                raise HousingError("Esa hipoteca ya no existe.")
            finance.close_mortgage_in(connection, mortgage.id, "pagada")
            _news_in(
                connection, guild_id,
                News(mortgage.listing, day, "amortiza", mortgage.owed, user_id, None), now,
            )  # fmt: skip
            entries: list[tuple[int, LedgerEntry]] = [
                (user_id, LedgerEntry(-mortgage.owed, "pisos:amortizacion"))
            ]
            # Pagarlo todo antes del remate libera el inmueble de la subasta (art. 670.7 LEC).
            for auction in trade.auctions_in(connection, guild_id):
                if auction.mortgage_id == mortgage.id:
                    trade.close_auction_in(connection, auction.id, trade.CANCELLED)
                    entries.extend(_refund(auction))
            return mortgage.owed, HousingMove(entries=tuple(entries))

        return run

    # -- Cada noche ---------------------------------------------------------------------

    def close_day(
        self,
        guild_id: int,
        day: date,
        now: float,
        rng: random.Random,
        builders: tuple[int, ...] = (),
        furniture: Mapping[int, frozenset[str]] | None = None,
    ) -> Callable[[sqlite3.Connection], tuple[DayRun, HousingMove]]:
        """Cierra un día (un mes del juego) entero.

        Primero cobra la renta a los miembros de alquiler (entera o nada) y luego, sabiendo
        quién pagó: ejecuciones hipotecarias, rentas, bono, IGIC vacacional, SOCIMI,
        recibos de cada inmueble y de cada VPO, cuotas de las hipotecas y deudas con el
        banco. Los lunes, además, el Euríbor, el sorteo de VPO, las ofertas del fondo y la
        obra nueva. Es idempotente: si el día ya estaba cerrado, no mueve nada.

        Args:
            builders: Quienes han fichado esta semana en la rama Ladrillo de `pala`, del
                puesto más alto al más bajo: el lunes, uno de ellos (por turnos) levanta
                una promoción.
            furniture: Los muebles del vacacional (claves de la tienda) de cada miembro que
                decide un vacacional o un piso de nómadas.
        """
        owned = furniture or {}

        def run(connection: sqlite3.Connection) -> tuple[DayRun, HousingMove]:
            done = connection.execute(
                "SELECT 1 FROM housing_days WHERE guild_id = ? AND day = ?",
                (guild_id, day.isoformat()),
            ).fetchone()
            if done is not None:
                return DayRun(day, done_before=True), HousingMove()
            connection.execute(
                "INSERT INTO housing_days (guild_id, day, processed_at) VALUES (?, ?, ?)",
                (guild_id, day.isoformat(), now),
            )
            result = DayRun(day)
            connection.execute(
                "DELETE FROM housing_candidates WHERE guild_id = ? AND until < ?",
                (guild_id, day.isoformat()),
            )
            market = _market_in(connection, guild_id)
            for barrio in (b.key for b in BARRIOS):
                value, level = market.index(barrio), market.index_level(barrio)
                if barrio in market.indexes or value != level:
                    _set_index_in(connection, guild_id, barrio, rules.index_next_day(value, level))
            if day.weekday() == 0:
                self._monday_in(connection, guild_id, day, now, rng, market, result)
                if builders:
                    builder = builders[day.toordinal() // 7 % len(builders)]
                    self._promotion_in(connection, guild_id, day, now, rng, market, builder,
                                       result)  # fmt: skip
                if self._bubble_in(connection, guild_id, day, now, rng, market, result):
                    # Ha movido índices y obras: lo que sigue tiene que ver las fechas nuevas.
                    market = _market_in(connection, guild_id)
            result.solars_done.extend(
                (work.user_id, work.solar) for work in market.solars.values() if work.until == day
            )
            pulls = tuple(
                HousingPull(lease.member, lease.rent, key, "pisos:alquiler", home=True)
                for key, lease in market.leases.items()
                if lease.member is not None
                and lease.status in (rules.RENTED, rules.DEFAULTING)
                and market.listing(key) is not None
            )

            def then(conn: sqlite3.Connection, paid: frozenset[str]) -> HousingMove:
                return self._close_rest_in(conn, guild_id, day, now, rng, paid, result, owned)

            return result, HousingMove(pulls=pulls, then=then)

        return run

    @staticmethod
    def _bubble_in(
        connection: sqlite3.Connection,
        guild_id: int,
        day: date,
        now: float,
        rng: random.Random,
        market: Market,
        result: DayRun,
    ) -> bool:
        """La burbuja de la obra nueva (los lunes, 9.56.0): si estalla o no, y lo que hace.

        Compara lo que valen las obras empezadas en las últimas `BUBBLE_WINDOW` noches con lo
        que han pagado los miembros por pisos en ese tiempo (`housing_solars.bubble_odds`). Si
        estalla, baja el índice de los barrios con obra (`bubble_cut`) y retrasa las obras en
        marcha `BUBBLE_DELAY` días, con su bloqueo de venta al mercado. No mueve dinero.

        Returns:
            Si ha estallado.
        """
        recent, supply, demand = _bubble_flows_in(connection, guild_id, day, market)
        if supply < solar_rules.BUBBLE_SUPPLY_MIN:
            return False
        if rng.random() >= solar_rules.bubble_odds(supply, demand):
            return False
        cut = solar_rules.bubble_cut(supply, demand)
        barrios = sorted({solar_rules.SOLAR_BY_KEY[w.solar].barrio for w in recent})
        for barrio in barrios:
            value = solar_rules.burst_index(_index_in(connection, guild_id, barrio), cut)
            _set_index_in(connection, guild_id, barrio, value)
        hit = {w.solar: w for w in recent}
        for work in market.solars.values():
            if work.until <= day:
                continue
            hit[work.solar] = work
            until = work.until + timedelta(days=solar_rules.BUBBLE_DELAY)
            connection.execute(
                "UPDATE housing_solars SET until = ? WHERE guild_id = ? AND solar = ?",
                (until.isoformat(), guild_id, work.solar),
            )
            for unit in solar_units(work.solar, work.project)[1]:
                connection.execute(
                    "UPDATE housing_leases SET works_until = ? WHERE guild_id = ? AND listing = ? "
                    "AND status = ?",
                    (until.isoformat(), guild_id, unit.key, rules.OBRAS),
                )
                connection.execute(
                    "UPDATE housing_holdings SET locked_until = ? WHERE guild_id = ? "
                    "AND listing = ? AND locked_until = ?",
                    (until.isoformat(), guild_id, unit.key, work.until.isoformat()),
                )
        for work in hit.values():
            item = News(work.solar, day, "burbuja", 0, work.user_id, None)
            result.news.append(item)
            _news_in(connection, guild_id, item, now)
        result.bubble = (cut, tuple(barrios), tuple(sorted({w.user_id for w in hit.values()})))
        return True

    def _monday_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        day: date,
        now: float,
        rng: random.Random,
        market: Market,
        result: DayRun,
    ) -> None:
        """Lo de cada lunes: Euríbor, sorteo de VPO y ofertas del fondo buitre."""
        euribor = rules.next_euribor(market.euribor, rng)
        if finance.set_euribor_in(connection, guild_id, day, euribor):
            result.euribor = (market.euribor, euribor)
        # VPO: caducan las reservas que nadie compró y se sortean las libres.
        for unit, (member, until) in market.reservations.items():
            if until < day:
                connection.execute(
                    "DELETE FROM housing_vpo_reservations WHERE guild_id = ? AND unit = ?",
                    (guild_id, unit),
                )
                item = News(finance.vpo_ref(unit), day, "vpo_caducada", 0, member, None)
                result.news.append(item)
                _news_in(connection, guild_id, item, now)
        drawn = connection.execute(
            "INSERT OR IGNORE INTO housing_vpo_draws (guild_id, week) VALUES (?, ?)",
            (guild_id, day.isoformat()),
        ).rowcount
        if drawn:
            reserved = finance.vpo_reservations_in(connection, guild_id)
            candidates = [
                m
                for m in market.signups
                if _vpo_eligible(market, m) and all(owner != m for owner, _u in reserved.values())
            ]
            for unit in VPO_UNITS:
                if unit.key in market.vpo or unit.key in reserved or not candidates:
                    continue
                winner = rng.choice(candidates)
                candidates.remove(winner)
                until = day + timedelta(days=7)
                connection.execute(
                    "INSERT INTO housing_vpo_reservations (guild_id, unit, user_id, until) "
                    "VALUES (?, ?, ?, ?)",
                    (guild_id, unit.key, winner, until.isoformat()),
                )
                connection.execute(
                    "DELETE FROM housing_vpo_signups WHERE guild_id = ? AND user_id = ?",
                    (guild_id, winner),
                )
                result.draws.append((winner, unit.key))
                item = News(finance.vpo_ref(unit.key), day, "sorteo_vpo", unit.price, winner, None)
                result.news.append(item)
                _news_in(connection, guild_id, item, now)
        # Fondo buitre: caducan las ofertas viejas y llega una nueva para quien tiene mucho.
        connection.execute(
            "DELETE FROM housing_offers WHERE guild_id = ? AND until < ?",
            (guild_id, day.isoformat()),
        )
        owners = {m for members in market.holdings.values() for m in members}
        for member in sorted(owners):
            wholes = [h for h in market.of_member(member) if h.shares == rules.SHARES]
            edificio = any(market.kind_of(h.listing) == "edificio" for h in wholes)
            if market.whole_homes(member) < rules.VULTURE_HOMES and not edificio:
                continue
            target = rng.choice(wholes)
            price = round(market.value(target) * (1 + rules.VULTURE_PREMIUM), -3)
            connection.execute(
                """
                INSERT INTO housing_offers (guild_id, listing, user_id, price, until)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (guild_id, listing, user_id) DO UPDATE SET
                    price = excluded.price, until = excluded.until
                """,
                (guild_id, target.listing, member, price,
                 (day + timedelta(days=rules.VULTURE_DAYS)).isoformat()),
            )  # fmt: skip
            result.offers.append((member, target.listing, price))
            item = News(target.listing, day, "oferta_fondo", price, member, None)
            result.news.append(item)
            _news_in(connection, guild_id, item, now)
        # Noticias de barrio: caducan las viejas y salen las de esta semana.
        connection.execute(
            "DELETE FROM housing_barrio_news WHERE guild_id = ? AND until < ?",
            (guild_id, day.isoformat()),
        )
        busy = {b for b, (_k, _start, until) in market.barrio_news.items() if until >= day}
        for barrio, key in rules.monday_news(day, busy, rng):
            until = day + timedelta(days=rules.NEWS_DAYS - 1)
            connection.execute(
                """
                INSERT INTO housing_barrio_news (guild_id, barrio, news, start, until)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (guild_id, barrio) DO UPDATE SET
                    news = excluded.news, start = excluded.start, until = excluded.until
                """,
                (guild_id, barrio, key, day.isoformat(), until.isoformat()),
            )
            factor = BARRIO_NEWS_BY_KEY[key].index
            if factor != 1.0:
                value = _index_in(connection, guild_id, barrio) * factor
                _set_index_in(
                    connection, guild_id, barrio, min(rules.INDEX_MAX, max(rules.INDEX_MIN, value))
                )
            result.barrio_news.append((barrio, key))

    @staticmethod
    def _promotion_in(
        connection: sqlite3.Connection,
        guild_id: int,
        day: date,
        now: float,
        rng: random.Random,
        market: Market,
        builder: int,
        result: DayRun,
    ) -> None:
        """La obra nueva del lunes: una promoción en un barrio que aún no tiene ninguna.

        Cuando todos los barrios tienen la suya (20), no se construye más: es el tope de
        «Nada crece sin tope» de la obra nueva.
        """
        taken = {p.listing.barrio for p in market.promotions.values()}
        free = [b.key for b in BARRIOS if b.key not in taken]
        if not free:
            return
        listing = rules.new_promotion(len(market.promotions) + 1, free, rng)
        finance.add_promotion_in(connection, guild_id, Promotion(listing, builder, day), now)
        result.promotions.append((builder, listing.key))
        item = News(listing.key, day, "obra_nueva", rules.base_price(listing), builder, None)
        result.news.append(item)
        _news_in(connection, guild_id, item, now)

    def _execute_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        day: date,
        now: float,
        market: Market,
        mortgage: Mortgage,
        entries: list[tuple[int, LedgerEntry]],
        taxes: list[HousingTax],
        result: DayRun | AuctionRun,
        *,
        winner: int | None = None,
        price: int = 0,
        winner_cost: int = 0,
    ) -> None:
        """Ejecución hipotecaria: el banco cobra de la subasta y lo que falte se sigue debiendo.

        Si alguien ha ganado la subasta judicial (`winner`), sus participaciones pasan a él
        por lo que pujó (`price`) y quedan bloqueadas para venderlas al mercado. Si no, el
        banco se las queda al 70 % y vuelven al mercado. Lo que da la subasta paga primero
        las deudas del piso (el IBI, al Estado) y luego al banco; si sobra, para el dueño. No
        hay dación en pago: lo que no cubre queda como deuda con el banco y se cobra de lo
        que vaya entrando. Se cierran todas las hipotecas del dueño sobre ese inmueble (la de
        compra y las rehipotecas), de la más antigua a la más nueva: el banco es el mismo.

        Args:
            winner_cost: Lo que le cuesta al ganador, con ITP y notaría (lo paga él aparte).
        """
        member = mortgage.user_id
        if mortgage.listing.startswith("vpo:"):
            unit = mortgage.listing.removeprefix("vpo:")
            owner = market.vpo.get(unit)
            proceeds = round(VPO_BY_KEY[unit].price * rules.AUCTION_SHARE) if owner else 0
            debt_other = debt_ibi = 0
            if owner is not None:
                debt_ibi, debt_other = owner.arrears_ibi, owner.arrears - owner.arrears_ibi
                connection.execute(
                    "DELETE FROM housing_vpo WHERE guild_id = ? AND unit = ?", (guild_id, unit)
                )
            finance.clear_home_in(connection, guild_id, user_id=member)
        else:
            held = market.holdings.get(mortgage.listing, {}).get(member)
            listing = market.listing(mortgage.listing)
            if held is None or listing is None:
                proceeds = debt_ibi = debt_other = 0
            else:
                lease = market.leases.get(mortgage.listing, rules.Lease())
                pool = sum(market.shares_of(mortgage.listing).values())
                index = market.index(listing.barrio)
                proceeds = price if winner else round(market.value(held) * rules.AUCTION_SHARE)
                debt = lease.arrears * held.shares // pool
                debt_ibi = min(debt, lease.arrears_ibi * held.shares // pool)
                debt_other = debt - debt_ibi
            if held is not None and listing is not None and winner is not None:
                lease = replace(
                    lease, arrears=lease.arrears - debt, arrears_ibi=lease.arrears_ibi - debt_ibi
                )
                _move_shares_in(
                    connection, guild_id, market, mortgage.listing, member, winner, held.shares,
                    paid=price, cost=winner_cost, day=day, now=now, lease=lease,
                    lock=day + timedelta(days=rules.AUCTION_LOCK_DAYS),
                )  # fmt: skip
            elif held is not None and listing is not None:
                connection.execute(
                    "DELETE FROM housing_holdings "
                    "WHERE guild_id = ? AND listing = ? AND user_id = ?",
                    (guild_id, mortgage.listing, member),
                )
                lease = replace(
                    lease, arrears=lease.arrears - debt, arrears_ibi=lease.arrears_ibi - debt_ibi
                )
                if market.homes.get(member) == (finance.HOME_OWN, mortgage.listing):
                    finance.clear_home_in(connection, guild_id, user_id=member)
                    lease = replace(lease, status=rules.VACANT, since=day)
                if pool - held.shares > 0:
                    _save_lease_in(connection, guild_id, mortgage.listing, lease)
                else:
                    if lease.member is not None:
                        finance.clear_home_in(connection, guild_id, user_id=lease.member)
                    connection.execute(
                        "DELETE FROM housing_leases WHERE guild_id = ? AND listing = ?",
                        (guild_id, mortgage.listing),
                    )
                    _clear_reviews_in(connection, guild_id, mortgage.listing)
                    # shortcut: lo que debían las habitaciones se pierde con la ejecución.
                    _drop_rooms_in(connection, guild_id, market, mortgage.listing)
                _set_index_in(
                    connection, guild_id, listing.barrio,
                    rules.index_after(index, listing, held.shares, bought=False),
                )  # fmt: skip
        debt_ibi = min(debt_ibi, proceeds)
        debt_other = min(debt_other, proceeds - debt_ibi)
        group = market.mortgages_of(member, mortgage.listing) or [mortgage]
        owed = sum(m.owed for m in group)
        # Lo que sobre de la subasta se queda en su monedero.
        to_bank = min(owed, proceeds - debt_ibi - debt_other)
        if proceeds:
            entries.append((member, LedgerEntry(proceeds, "pisos:subasta")))
            entries.append((member, LedgerEntry(-debt_other, "pisos:comunidad")))
            entries.append((member, LedgerEntry(-to_bank, "pisos:banco")))
            taxes.append(HousingTax(member, "ibi", debt_ibi, debt_ibi))
        left = to_bank
        for each in sorted(group, key=lambda m: m.id):
            finance.close_mortgage_in(connection, each.id, "ejecutada")
            # Lo cobrado paga por rango; lo que no cubre de cada una lo debe quien la avala
            # (fianza solidaria: el avalado le debe a él) o, si no, el dueño.
            covered = min(each.owed, left)
            left -= covered
            short = each.owed - covered
            guarantor = lending.guarantor_of(each.aval)
            if short and guarantor is not None:
                finance.add_bank_debt_in(connection, guild_id, guarantor, short)
                finance.add_member_debt_in(connection, guild_id, guarantor, member, short, now)
                result.guarantee_claims.append((guarantor, member, short))
            elif short:
                finance.add_bank_debt_in(connection, guild_id, member, short)
        result.executions.append((member, mortgage.listing))
        if any(m.aval == rules.PARENTS_AVAL for m in group):
            result.aval_executions.append(member)
        if any(m.remortgage for m in group):
            result.remortgage_executions.append(member)
        kind = "ejecucion_subasta" if winner is not None else "ejecucion"
        item = News(mortgage.listing, day, kind, proceeds, member, None)
        result.news.append(item)
        _news_in(connection, guild_id, item, now)

    def _close_rest_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        day: date,
        now: float,
        rng: random.Random,
        paid: frozenset[str],
        result: DayRun,
        furniture: Mapping[int, frozenset[str]],
    ) -> HousingMove:
        """El cierre del día una vez se sabe qué miembros de alquiler pagaron."""
        rents: list[HousingRent] = []
        grants: list[HousingRent] = []
        entries: list[tuple[int, LedgerEntry]] = []
        taxes: list[HousingTax] = []
        debits: list[HousingDebit] = []
        imputations: list[tuple[int, str, int]] = []
        market = _market_in(connection, guild_id)
        # Ejecuciones hipotecarias: con lo que se debía al empezar el día.
        executed = set()
        judicial = {a.mortgage_id for a in market.auctions if a.judicial}
        # Todas las hipotecas de alguien sobre un inmueble van juntas (la de compra y las
        # rehipotecas): es el mismo banco, el vencimiento de una arrastra a las demás y la
        # subasta, una sola, las paga por rango (`_execute_in`).
        handled: set[tuple[int, str]] = set()
        for mortgage in market.mortgages:
            if not rules.must_execute(
                mortgage.unpaid_count, mortgage.unpaid_amount, mortgage.principal
            ):
                continue
            group = (mortgage.user_id, mortgage.listing)
            if group in handled:
                continue
            handled.add(group)
            if any(m.id in judicial for m in market.mortgages_of(*group)):
                continue  # ya está en subasta: la cierra `close_auctions`
            held = market.holdings.get(mortgage.listing, {}).get(mortgage.user_id)
            if held is not None and market.listing(mortgage.listing) is not None:
                # Sale a subasta judicial (art. 655 y ss. LEC): puja quien quiera durante un día.
                value = market.value(held)
                number = trade.add_auction_in(
                    connection, guild_id, listing=mortgage.listing, seller=mortgage.user_id,
                    shares=held.shares, min_bid=max(1, round(value * rules.JUDICIAL_MIN_SHARE)),
                    ends_at=now + rules.AUCTION_SECONDS, now=now, mortgage_id=mortgage.id,
                )  # fmt: skip
                result.auctions.append((mortgage.user_id, mortgage.listing, number))
                item = News(mortgage.listing, day, "subasta_judicial", value, mortgage.user_id,
                            None)  # fmt: skip
                result.news.append(item)
                _news_in(connection, guild_id, item, now)
                continue
            self._execute_in(
                connection, guild_id, day, now, market, mortgage, entries, taxes, result
            )
            executed.add(mortgage.id)
        if executed:
            market = _market_in(connection, guild_id)
        # Intereses de hoy de cada hipoteca: lo que se deduce de la renta (art. 23.1.a LIRPF).
        # Los de una rehipoteca no: solo deducen los del dinero invertido en comprar o mejorar
        # el inmueble (art. 23.1.a.1.º), y una rehipoteca no compra nada.
        interest: dict[tuple[int, str], int] = {}
        schedule: dict[int, tuple[int, int]] = {}
        for mortgage in market.mortgages:
            pay = mortgage.payment if mortgage.months_left > 0 else 0
            owed_interest, principal = rules.mortgage_month(mortgage.balance, mortgage.rate, pay)
            schedule[mortgage.id] = (mortgage.balance - principal, pay)
            key = (mortgage.user_id, mortgage.listing)
            if not mortgage.remortgage:
                interest[key] = interest.get(key, 0) + owed_interest
            due = pay + mortgage.unpaid_amount
            if due:
                # La cuota de la hipoteca de la vivienda habitual cuenta para la deducción
                # canaria por inversión en vivienda habitual (art. 14 del DL 1/2009).
                # También la de una VPO (`vpo:<unidad>`), que es vivienda habitual a la fuerza.
                # La de una rehipoteca no: no se invirtió en la vivienda.
                place = market.homes.get(mortgage.user_id)
                home = not mortgage.remortgage and (
                    place == (finance.HOME_OWN, mortgage.listing)
                    or (
                        place is not None
                        and place[0] == finance.HOME_VPO
                        and finance.vpo_ref(place[1]) == mortgage.listing
                    )
                )
                debits.append(HousingDebit(mortgage.user_id, f"hipoteca:{mortgage.id}", 0, due,
                                           "pisos:hipoteca", home=home))  # fmt: skip
        # Préstamos personales: la cuota del mes (de hoy) desde la noche siguiente a firmar,
        # con la prima del seguro y su IPS. Lo que no llegó se vuelve a pedir.
        loan_plan: dict[int, tuple[int, int, int]] = {}
        for loan in market.loans:
            # Los del negocio los cobra su noche (`business_night_in`), de su caja.
            if day.isoformat() < loan.first_day or loan.business_id is not None:
                continue
            pay = loan.payment if loan.months_left > 0 else 0
            owed_interest, principal = rules.mortgage_month(loan.balance, loan.rate, pay)
            loan_plan[loan.id] = (loan.balance - principal, pay, owed_interest)
            premium, ips = (loan.premium, loan.premium_tax) if pay else (0, 0)
            if pay + premium + ips + loan.unpaid_amount:
                debits.append(
                    HousingDebit(
                        loan.user_id, f"prestamo:{loan.id}", 0, pay + premium + loan.unpaid_amount,
                        "banco:cuota", taxes=(("ips", ips),) if ips else (),
                    )
                )  # fmt: skip
        for member, amount in market.bank_debts.items():
            debits.append(HousingDebit(member, f"banco:{member}", 0, amount, "pisos:deuda_banco"))
        # Después de su deuda con el banco, lo que debe a sus avalistas: llega a ellos.
        for (creditor, debtor), amount in market.member_debts.items():
            debits.append(
                HousingDebit(
                    debtor, f"reembolso:{creditor}:{debtor}", 0, amount, "banco:reembolso",
                    to=creditor,
                )
            )  # fmt: skip
        for unit, owner in market.vpo.items():
            spec = VPO_BY_KEY[unit]
            costs = rules.vpo_costs(spec.price, spec.community)
            debits.append(
                HousingDebit(
                    owner.owner_id, f"vpo:{unit}", costs.ibi + owner.arrears_ibi,
                    costs.community + owner.arrears - owner.arrears_ibi,
                )
            )  # fmt: skip
        # Los suministros de quien vive en cada casa (`casa`): la luz con su impuesto, el agua
        # y la fibra con su IGIC. Lo que no llegue no se debe: le cortan la luz (`settle`).
        for member, (kind, ref) in market.homes.items():
            place = place_of(market, kind, ref)
            if place is None:
                continue
            bill = casa.supplies(place)
            result.homes[member] = HomeNight(place, casa.comfort(place), bill)
            debits.append(
                HousingDebit(
                    member, f"suministros:{member}", 0, bill.gone, "casa:suministros",
                    taxes=(("electricidad", bill.power_tax), ("igic_suministros", bill.igic)),
                )
            )  # fmt: skip
        self._deposits_in(connection, guild_id, day, now, market, rents, entries, result)
        self._reclaims_in(connection, guild_id, day, market)
        for key in [*market.keys(), *market.room_keys()]:
            lease = market.leases.get(key)
            # El piso que se alquila por habitaciones lo cobran y lo pagan sus habitaciones.
            if lease is None or lease.status == rules.BY_ROOMS or not market.shares_of(key):
                continue
            self._close_listing_in(
                connection, guild_id, day, now, rng, market, key, lease, paid, interest,
                rents, grants, taxes, entries, debits, imputations, result, furniture,
            )  # fmt: skip
        horizon = (day - timedelta(days=NEWS_DAYS)).isoformat()
        connection.execute(
            "DELETE FROM housing_news WHERE guild_id = ? AND day < ?", (guild_id, horizon)
        )
        connection.execute(
            "DELETE FROM housing_income WHERE guild_id = ? AND day < ?", (guild_id, horizon)
        )
        mortgages = {m.id: m for m in market.mortgages}
        loans = {loan.id: loan for loan in market.loans}

        def settle(conn: sqlite3.Connection, unpaid: dict[str, tuple[int, int]]) -> None:
            for mortgage_id, (balance, pay) in schedule.items():
                mortgage = mortgages[mortgage_id]
                left = unpaid.get(f"hipoteca:{mortgage_id}", (0, 0))[1]
                months = max(0, mortgage.months_left - (1 if pay else 0))
                if left:
                    result.mortgage_unpaid.append(mortgage.user_id)
                elif pay:
                    member = mortgage.user_id
                    result.installments[member] = result.installments.get(member, 0) + 1
                if not balance and not left:
                    finance.close_mortgage_in(conn, mortgage_id, "pagada")
                    continue
                finance.save_mortgage_in(
                    conn, mortgage, balance=balance, payment=pay if months else 0,
                    months_left=months, unpaid_count=mortgage.unpaid_count + 1 if left else 0,
                    unpaid_amount=left,
                )  # fmt: skip
            for tag, (ibi, other) in unpaid.items():
                if tag.startswith(("hipoteca:", "prestamo:")):
                    continue
                if tag.startswith("reembolso:"):
                    continue
                if tag.startswith("banco:"):
                    member = int(tag.removeprefix("banco:"))
                    conn.execute(
                        "UPDATE housing_bank_debts SET amount = ? "
                        "WHERE guild_id = ? AND user_id = ?",
                        (other, guild_id, member),
                    )
                elif tag.startswith("vpo:"):
                    conn.execute(
                        "UPDATE housing_vpo SET arrears = ?, arrears_ibi = ? "
                        "WHERE guild_id = ? AND unit = ?",
                        (ibi + other, ibi, guild_id, tag.removeprefix("vpo:")),
                    )
                elif tag.startswith("suministros:"):
                    member = int(tag.removeprefix("suministros:"))
                    if (ibi or other) and member in result.homes:
                        result.homes[member].unpaid = ibi + other
                        finance.cut_supplies_in(conn, guild_id, member, day + timedelta(days=1))
                elif tag.startswith("bono:"):
                    member = market.leases[tag.removeprefix("bono:")].member
                    if member is not None and other:
                        result.bono.pop(member, None)
                        result.bono_unpaid.append(member)
                        item = News(tag.removeprefix("bono:"), day, "partida_agotada", other,
                                    member, None)  # fmt: skip
                        result.news.append(item)
                        _news_in(conn, guild_id, item, now)
                elif ibi or other:
                    conn.execute(
                        """
                        UPDATE housing_leases
                        SET arrears = arrears + ?, arrears_ibi = arrears_ibi + ?
                        WHERE guild_id = ? AND listing = ?
                        """,
                        (ibi + other, ibi, guild_id, tag),
                    )
            for (creditor, debtor), amount in market.member_debts.items():
                left = unpaid.get(f"reembolso:{creditor}:{debtor}", (0, 0))[1]
                finance.set_member_debt_in(conn, guild_id, creditor, debtor, left)
                if amount > left:
                    result.reimbursements.append((creditor, debtor, amount - left))
            for loan_id, (_balance, pay, _interest) in loan_plan.items():
                loan = loans[loan_id]
                if pay and loan.advance and not _tenant_paying_in(conn, guild_id, loan.listing):
                    result.advance_orphans.append(loan.user_id)
            # Después de poner al día las deudas con el banco: un préstamo que vence hoy suma.
            for loan_id, plan in loan_plan.items():
                self._settle_loan_in(conn, guild_id, now, loans[loan_id], plan, unpaid, result)

        return HousingMove(
            rents=tuple(rents),
            grants=tuple(grants),
            entries=tuple(entries),
            taxes=tuple(taxes),
            debits=tuple(debits),
            settle=settle,
            imputations=tuple(imputations),
        )

    @staticmethod
    def _settle_loan_in(
        connection: sqlite3.Connection,
        guild_id: int,
        now: float,
        loan: Loan,
        plan: tuple[int, int, int],
        unpaid: dict[str, tuple[int, int]],
        result: DayRun,
    ) -> None:
        """Guarda cómo queda un préstamo tras cobrar la cuota de la noche.

        Lo que no llegó lo paga el seguro si le quedan coberturas; si no, se debe y cuenta
        una cuota seguida sin pagar. Con `DEFAULT_INSTALLMENTS`, vencimiento anticipado: lo
        que queda pasa a deuda con el banco.
        """
        balance, pay, owed_interest = plan
        member = loan.user_id
        premium = loan.premium + loan.premium_tax if pay else 0
        missing = sum(unpaid.get(f"prestamo:{loan.id}", (0, 0)))
        paid_now = pay + premium + loan.unpaid_amount - missing
        covers = loan.covers_left
        if missing and covers:
            # El seguro paga lo que no llegó: al banco, que tampoco existe, así que no se
            # mueve dinero.
            covers -= 1
            missing = 0
            result.loan_covered.append(member)
        if missing and pay and loan.kind == finance.LOAN_BNPL:
            # Paga en 3 no cobra intereses, pero cada cuota que no llega lleva su recargo.
            missing += credit.late_fee(loan.payment)
        if missing:
            result.loan_unpaid.append(member)
        elif pay:
            result.loan_installments[member] = result.loan_installments.get(member, 0) + 1
        unpaid_count = loan.unpaid_count + 1 if missing else 0
        finance.save_loan_in(
            connection, loan, balance=balance, months_left=max(0, loan.months_left - bool(pay)),
            covers_left=covers, unpaid_count=unpaid_count, unpaid_amount=missing,
            paid=loan.paid + paid_now,
            interest_paid=loan.interest_paid + (owed_interest if not missing else 0),
        )  # fmt: skip
        if not balance and not missing:
            finance.close_loan_in(connection, loan.id, "pagado", now)
            result.loans_finished.append(member)
        elif unpaid_count >= lending.DEFAULT_INSTALLMENTS:
            owed = balance + missing
            guarantor = lending.guarantor_of(loan.aval)
            if guarantor is not None:
                # Fianza solidaria (arts. 1822, 1831 y 1144 CC): el banco reclama al avalista y
                # el avalado le debe lo que pague (acción de reembolso, art. 1838 CC).
                finance.add_bank_debt_in(connection, guild_id, guarantor, owed)
                finance.add_member_debt_in(connection, guild_id, guarantor, member, owed, now)
                result.guarantee_claims.append((guarantor, member, owed))
            else:
                finance.add_bank_debt_in(connection, guild_id, member, owed)
            finance.close_loan_in(connection, loan.id, "vencido", now)
            result.loan_defaults.append((member, owed))

    def _close_listing_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        day: date,
        now: float,
        rng: random.Random,
        market: Market,
        key: str,
        lease: rules.Lease,
        paid: frozenset[str],
        interest: dict[tuple[int, str], int],
        rents: list[HousingRent],
        grants: list[HousingRent],
        taxes: list[HousingTax],
        entries: list[tuple[int, LedgerEntry]],
        debits: list[HousingDebit],
        imputations: list[tuple[int, str, int]],
        result: DayRun,
        furniture: Mapping[int, frozenset[str]],
    ) -> None:
        """Un mes de un inmueble con dueño: estado, renta, impuestos y recibos."""
        listing = _listing(market, key)
        index = market.index(listing.barrio)
        owner = market.owner_key(key)
        # Una habitación paga su parte de la amortización y de los intereses del piso.
        share = 1 / len(market.units_of(owner)) if owner != key else 1.0
        news: list[News] = []
        all_holders = market.shares_of(key)
        boss = market.manager(key)
        mine = furniture.get(boss, frozenset()) if boss is not None else frozenset()
        # La licencia vacacional dura 5 años (60 días) y se renueva si el barrio no está
        # tensionado (Ley 6/2025 de Canarias). Las de antes de la 8.4.0 empiezan a contar hoy.
        if lease.license and lease.license_until is None:
            lease = replace(lease, license_until=day + timedelta(days=rules.LICENSE_DAYS))
        elif lease.license and lease.license_until is not None and day >= lease.license_until:
            if rules.tense(index):
                lease = replace(lease, license=False, license_until=None)
                news.append(News(key, day, "licencia_caduca", 0, boss, None))
            else:
                lease = replace(
                    lease, license_until=lease.license_until + timedelta(days=rules.LICENSE_DAYS)
                )
                news.append(News(key, day, "licencia_renovada", 0, boss, None))
        # La renta que se pide tiene que estar permitida: la vacacional pide licencia y
        # el gran tenedor tiene tope en zona tensionada (Ley 12/2023).
        if not rules.level_allowed(
            lease.level, index=index, whole_homes=market.whole_homes(boss) if boss else 0,
            license=lease.license,
        ):  # fmt: skip
            if lease.license or lease.level != rules.VACATION_LEVEL:
                news.append(News(key, day, "tope_ley", 0, boss, None))
            lease = replace(lease, level="referencia")
        # Solo cobra y paga ese mes quien ya era dueño ese día.
        holders = {
            member: holding.shares
            for member, holding in market.holdings[owner].items()
            if local_day(holding.since) <= day
        }
        barrio_news = market.news_of(listing.barrio, day)
        if lease.member is not None:
            outcome = self._member_month(
                listing, lease, key, day, rng, paid, news, barrio_news, index
            )
            if outcome.lease.member is None:
                # Desahuciado: deja de vivir aquí y el casero se queda de la fianza lo que
                # debía (art. 36 LAU); el resto se le devuelve mañana.
                finance.clear_home_in(connection, guild_id, user_id=lease.member)
                finance.forfeit_deposit_in(
                    connection, guild_id, key, lease.member, lease.unpaid * lease.rent
                )
        else:
            # La nota de las reseñas mueve la ocupación del vacacional; la fibra atrae nómadas.
            boost = 1.0
            if lease.level == rules.VACATION_LEVEL:
                boost = rules.rating_factor(lease.rating, lease.reviews)
            elif lease.level == rules.NOMAD_LEVEL:
                boost = nomad_boost(mine)
            if boss is not None:
                boost *= rules.fame_find(market.fame(boss, day))
            outcome = rules.advance_day(listing, lease, day, index, rng, barrio_news, boost)
        # Los imprevistos que se deciden no se pagan hoy: esperan al dueño (o a la gestora).
        deferred = [e for e in outcome.events if e.key in rules.DECISIONS]
        for event in deferred:
            connection.execute(
                "INSERT INTO housing_decisions (guild_id, listing, event, cost, day, until) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (guild_id, key, event.key, event.cost, day.isoformat(),
                 (day + timedelta(days=rules.DECISION_DAYS)).isoformat()),
            )  # fmt: skip
        paid_now, mood, relapses, claim = self._decide_in(connection, guild_id, day, rng, market,
                                                          key, result)  # fmt: skip
        lease_after = outcome.lease
        reclaim = market.reclaims.get(key)
        if (
            reclaim is not None and reclaim.until is None and lease.leaving == rules.LEAVE_NEED
            and lease_after.status == rules.VACANT
        ):  # fmt: skip
            lease_after = self._move_in_in(
                connection, guild_id, day, now, market, key, lease_after, reclaim, news
            )
        if mood and lease_after.status in (rules.RENTED, rules.DEFAULTING):
            lease_after = replace(lease_after, mood=max(0, min(100, lease_after.mood + mood)))
        if claim:
            lease_after = replace(
                lease_after, home_claim_until=day + timedelta(days=rules.HOME_CLAIM_DAYS)
            )
        for event_key, cost in relapses:
            connection.execute(
                "INSERT INTO housing_decisions (guild_id, listing, event, cost, day, until) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (guild_id, key, event_key, cost, day.isoformat(),
                 (day + timedelta(days=rules.DECISION_DAYS)).isoformat()),
            )  # fmt: skip
        if any(e.key == "obra_terminada" for e in outcome.events):
            connection.execute(
                "UPDATE housing_reforms SET finished = 1 WHERE guild_id = ? AND listing = ?",
                (guild_id, key),
            )
            result.works.append((tuple(market.shares_of(key)), key))
        # Cada día de estancia vacacional paga la comisión de la plataforma y, si el huésped
        # se va, la limpieza; al irse, deja reseña (7 de cada 10).
        stay = 0
        if lease.level == rules.VACATION_LEVEL and outcome.rent > 0:
            out = any(e.key == "se_va" for e in outcome.events)
            stay = rules.stay_costs(outcome.rent, out=out)
            if out:
                result.cleanings.append((tuple(all_holders), key))
            if out and lease.tenant is not None and rng.random() < rules.REVIEW_CHANCE:
                guest = lease.tenant
                stars = rules.review_stars(
                    listing, guest,
                    furniture=furniture_stars(mine, BARRIO_BY_KEY[listing.barrio].theme, guest),
                    pending=len(market.decisions.get(key, ())) + len(deferred),
                    noisy=any(e.key == "vecino" for e in outcome.events)
                    or (barrio_news is not None and barrio_news.neighbour > 1),
                    rng=rng,
                )  # fmt: skip
                line = rng.randrange(len(REVIEW_LINES[stars]))
                rating, count = _review_in(connection, guild_id, key, day, guest, stars, line)
                result.reviews.append((tuple(all_holders), key, stars))
                if not rules.superhost(lease.rating, lease.reviews) and rules.superhost(
                    rating, count
                ):
                    result.superhosts.append((tuple(all_holders), key))
                    news.append(News(key, day, "superanfitrion", 0, None, None))
                lease_after = replace(lease_after, rating=rating, reviews=count)
        extra_today = outcome.extra - sum(e.cost for e in deferred) + paid_now + stay
        # Fondo de reserva (art. 9.1.f LPH): guarda el 10 % de la comunidad de cada mes (que se
        # paga igual) y paga las derramas y la ITE hasta donde llegue.
        if holders:
            covered = min(
                market.reserves.get(key, 0),
                sum(e.cost for e in outcome.events if e.key in rules.RESERVE_EVENTS),
            )
            saved = 0
            if listing.kind in rules.BLOCK_KINDS:
                owned_share = sum(holders.values()) / rules.SHARES
                saved = round(outcome.costs.community * owned_share * rules.RESERVE_SHARE)
            if covered:
                extra_today -= covered
                news.append(News(key, day, "fondo_reserva", covered, None, None))
            if covered or saved:
                junta.add_reserve_in(connection, guild_id, key, saved - covered)
        if lease_after.tenant is not None and lease_after.status in (
            rules.RENTED,
            rules.DEFAULTING,
        ):
            for member in market.shares_of(key):
                result.moods.setdefault(member, []).append(lease_after.mood)
        if any(e.key == "firma" for e in outcome.events):
            if _bonus_ready(market, key, listing, lease, day):
                lease_after = replace(lease_after, bonus=True)
            if lease_after.reduction == rules.YOUNG_RENT_REDUCTION:
                result.young.append(key)
        self._candidates_in(connection, guild_id, day, market, key, outcome, lease_after, result)
        owned = sum(holders.values())
        if not owned:
            _save_lease_in(connection, guild_id, key, lease_after)
            self._log_news(connection, guild_id, now, news + self._event_news(key, day, outcome),
                           result)  # fmt: skip
            return
        part = partial(rules.owned_share, owned=owned)
        ibi_month = outcome.costs.ibi
        empty_for = (day - lease_after.since).days if lease_after.since else 0
        if (
            lease_after.status == rules.VACANT
            and empty_for >= rules.EMPTY_DAYS
            and boss is not None
            and market.whole_homes(boss) >= rules.EMPTY_OWNER_HOMES
        ):
            ibi_month = round(ibi_month * rules.EMPTY_IBI_FACTOR)
            if day.weekday() == 0:
                news.append(News(key, day, "recargo_ibi", ibi_month, boss, None))
        # Seguros: el del hogar siempre y el de impago si está contratado y cubre a este
        # inquilino, los dos con el impuesto de primas. Las reparaciones llevan IGIC.
        cover = rules.premium(listing, lease_after)
        home = outcome.costs.insurance
        if lease_after.home_claim_until is not None and lease_after.home_claim_until >= day:
            home = round(home * rules.HOME_CLAIM_SURCHARGE)  # el parte recarga la prima
        insurance = home + cover
        ips = round(home * rules.IPS_RATE) + round(cover * rules.IPS_RATE)
        stay_igic = igic(stay, rules.SERVICES_IGIC)
        repair_igic = igic(paid_now, rules.SERVICES_IGIC) + stay_igic
        costs_owned = rules.MonthlyCosts(
            part(outcome.costs.community), part(insurance), part(ibi_month)
        )
        rent_owned = part(outcome.rent + outcome.insurer)
        vacation = (
            rules.vacation_igic(part(outcome.rent))
            if lease_after.level == rules.VACATION_LEVEL else 0
        )  # fmt: skip
        payout = rules.settle_day(
            rent_owned - vacation, costs_owned, part(extra_today), lease.arrears,
            lease.arrears_ibi, taxes=part(ips) + part(repair_igic),
        )  # fmt: skip
        # La deducción del art. 16 se aplica con la primera renta del contrato.
        apply_bonus = lease_after.bonus and rent_owned > 0
        _save_lease_in(
            connection, guild_id, key,
            replace(
                lease_after, arrears=0, arrears_ibi=0, bonus=lease_after.bonus and not apply_bonus
            ),
        )  # fmt: skip
        owe_ibi = rules.split(payout.arrears_ibi - payout.taxes_left, holders)
        owe_other = rules.split(payout.arrears - payout.arrears_ibi, holders)
        owe_taxes = rules.split(payout.taxes_left, holders)
        due_ips = part(ips)
        due_state = due_ips + part(repair_igic)
        for member in holders:
            ips_owed = round(owe_taxes[member] * due_ips / due_state) if due_state else 0
            owed = tuple(
                (kind, amount)
                for kind, amount in (
                    ("ips", ips_owed),
                    ("igic_gastos", owe_taxes[member] - ips_owed),
                )
                if amount > 0
            )
            if owe_ibi[member] or owe_other[member] or owed:
                debits.append(
                    HousingDebit(member, key, owe_ibi[member], owe_other[member], taxes=owed)
                )
        events = self._event_news(key, day, outcome)
        if events:
            result.owners[key] = tuple(holders)
        self._log_news(connection, guild_id, now, news + events, result)
        if lease_after.member is not None and lease_after.bono and key in paid:
            amount = rules.bono_amount(lease_after.rent)
            grants.append(
                HousingRent(lease_after.member, amount, kind=INCOME_PRIZE, concept="pisos:bono",
                            tag=f"bono:{key}")
            )  # fmt: skip
            result.bono[lease_after.member] = amount
        nets = rules.split(payout.net, holders)
        ibis = rules.split(payout.ibi, holders)
        costs = rules.split(payout.costs, holders)
        igics = rules.split(vacation, holders)
        paid_taxes = rules.split(payout.taxes, holders)
        insured = rules.split(part(outcome.insurer), holders)
        # Para el IRPF, los gastos del mes que toca a cada uno, se paguen hoy o no (se
        # imputan al periodo en que se devengan, art. 14 LIRPF). Seguros y reparaciones,
        # con su impuesto: para quien no puede deducir el IGIC ni el de primas es un gasto.
        month_ibi = rules.split(costs_owned.ibi, holders)
        community = rules.split(costs_owned.community, holders)
        insurances = rules.split(costs_owned.insurance + part(ips), holders)
        # La limpieza y la plataforma son servicios de terceros (art. 13.d del Reglamento del
        # IRPF): gasto sin el tope de intereses y reparaciones.
        repairs = rules.split(part(extra_today - stay) + part(repair_igic - stay_igic), holders)
        services = rules.split(part(stay) + part(stay_igic), holders)
        premiums = rules.split(part(cover) + part(round(cover * rules.IPS_RATE)), holders)
        cheap = lease_after.rent <= INSURANCE_DEDUCTION_RENT_MAX
        reduction = rules.lease_reduction(listing, lease_after)
        dwelling = reduction > 0
        cadastral = rules.cadastral_value(listing)
        state_due = part(ips) + part(repair_igic)
        ips_share = part(ips) / state_due if state_due else 0.0
        for member, shares in holders.items():
            gross = nets[member] + ibis[member] + costs[member] + igics[member] + paid_taxes[member]
            if insured[member]:
                result.insurer[member] = result.insurer.get(member, 0) + insured[member]
            if gross <= 0:
                # Un piso vacío que no es la casa de nadie imputa renta (art. 85 LIRPF).
                if lease_after.status == rules.VACANT:
                    imputations.append((member, key, rules.imputed_rent(listing, shares)))
                continue
            held = market.holdings[owner][member]
            bonus = 0
            if apply_bonus and dwelling and member not in market.socimi:
                used = market.bonuses.get(member, frozenset())
                if key not in used and len(used) < VACANT_RENTAL_DEDUCTION_MAX:
                    bonus = vacant_rental_deduction(shares, rules.SHARES)
                    connection.execute(
                        "INSERT OR IGNORE INTO housing_rent_bonus "
                        "(guild_id, user_id, listing, created_at) VALUES (?, ?, ?, ?)",
                        (guild_id, member, key, now),
                    )
                    result.bonus[member] = result.bonus.get(member, 0) + bonus
            if member in market.socimi:
                # La SOCIMI tributa al 0 % y el dueño paga el 19 % del dividendo.
                deductible = (
                    ibis[member] + costs[member] + igics[member] + paid_taxes[member]
                    + rules.amortization(round(held.paid * share))
                    + round(interest.get((member, owner), 0) * share)
                )  # fmt: skip
                rents.append(HousingRent(member, gross, kind=None))
                profit = max(0, gross - deductible)
                taxes.append(HousingTax(member, "socimi", profit, rules.socimi_tax(profit)))
            else:
                rents.append(
                    HousingRent(
                        member,
                        gross,
                        ref=key,
                        income=gross - igics[member],
                        finance=round(interest.get((member, owner), 0) * share) + repairs[member],
                        costs=(
                            month_ibi[member] + community[member] + insurances[member]
                            + services[member] + rules.amortization(round(held.paid * share))
                        ),
                        upkeep=repairs[member] + insurances[member] if dwelling else 0,
                        premium=premiums[member] if dwelling and cheap else 0,
                        bonus=bonus,
                        reduction=reduction,
                    )
                )  # fmt: skip
            entries.append((member, LedgerEntry(-costs[member], "pisos:comunidad")))
            taxes.append(
                HousingTax(member, "ibi", cadastral * shares // rules.SHARES, ibis[member])
            )
            if igics[member]:
                taxes.append(HousingTax(member, "igic", gross - igics[member], igics[member]))
            if paid_taxes[member]:
                # Lo pagado hoy al Estado, repartido entre el impuesto de primas y el IGIC
                # de las reparaciones según lo que tocaba de cada uno.
                ips_paid = round(paid_taxes[member] * ips_share)
                if ips_paid:
                    taxes.append(HousingTax(member, "ips", insurances[member], ips_paid))
                if paid_taxes[member] - ips_paid:
                    taxes.append(HousingTax(member, "igic_gastos", repairs[member],
                                            paid_taxes[member] - ips_paid))  # fmt: skip
            before = result.members.get(member, MemberDay())
            result.members[member] = MemberDay(
                before.rent + gross, before.ibi + ibis[member], before.costs + costs[member]
            )
            connection.execute(
                """
                INSERT INTO housing_income (guild_id, user_id, listing, day, rent, ibi, costs)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (guild_id, user_id, listing, day) DO NOTHING
                """,
                (guild_id, member, key, day.isoformat(), gross, ibis[member], costs[member]),
            )

    @staticmethod
    def _move_in_in(
        connection: sqlite3.Connection, guild_id: int, day: date, now: float, market: Market,
        key: str, lease: rules.Lease, reclaim: Reclaim, news: list[News],
    ) -> rules.Lease:  # fmt: skip
        """El dueño que pidió el piso para vivir se muda en cuanto se va el inquilino.

        Tiene que vivir ahí `housing.NEED_STAY_DAYS` días (`leave_home_paying`). Si ya no
        puede (lo perdió en una subasta judicial, o la mitad es de otro), la petición se
        olvida: el piso se queda vacío.
        """
        member = reclaim.user_id
        if market.shares_of(key).get(member, 0) != rules.SHARES or member in market.homes:
            connection.execute(
                "DELETE FROM housing_reclaims WHERE guild_id = ? AND listing = ?", (guild_id, key)
            )
            return lease
        finance.set_home_in(connection, guild_id, member, finance.HOME_OWN, key, now)
        connection.execute(
            "UPDATE housing_reclaims SET until = ? WHERE guild_id = ? AND listing = ?",
            ((day + timedelta(days=rules.NEED_STAY_DAYS)).isoformat(), guild_id, key),
        )
        news.append(News(key, day, "se_muda", 0, member, None))
        return replace(lease, status=rules.HABITUAL, since=day)

    def _reclaims_in(
        self, connection: sqlite3.Connection, guild_id: int, day: date, market: Market
    ) -> None:
        """Olvida los pisos pedidos para vivir que ya no obligan a nada: quien los pidió ya
        ha vivido ahí lo que debía, se fue de otra forma (una subasta judicial) o el piso
        volvió al mercado."""
        for key, reclaim in market.reclaims.items():
            if reclaim.until is None:
                done = not market.shares_of(key)
            else:
                home = market.homes.get(reclaim.user_id)
                done = day >= reclaim.until or home != (finance.HOME_OWN, key)
            if done:
                connection.execute(
                    "DELETE FROM housing_reclaims WHERE guild_id = ? AND listing = ?",
                    (guild_id, key),
                )

    @staticmethod
    def _member_month(
        listing: Listing,
        lease: rules.Lease,
        key: str,
        day: date,
        rng: random.Random,
        paid: frozenset[str],
        news: list[News],
        barrio_news: BarrioNews | None = None,
        index: float = 1.0,
    ) -> rules.DayOutcome:
        """Un mes con un miembro de alquiler: paga si le llega; si no, impago.

        Si deja de pagar y el seguro de impago le cubre, el seguro paga desde el segundo mes
        (`housing.insure_month`). Al acabar el contrato renueva por lo que dure el que eligió
        el dueño, salvo que el dueño no quiera (`Lease.leaving`): entonces se va, aunque en
        zona tensionada pide antes la prórroga de un año de la Ley de Vivienda (un miembro
        siempre la pide: es suya).
        """
        extra, events = rules.roll_events(
            listing, rng, repairs=barrio_news.repairs if barrio_news else 1.0,
            neighbour=barrio_news.neighbour if barrio_news else 1.0,
        )  # fmt: skip
        member = lease.member
        rent = 0
        if lease.status == rules.EVICTING:
            if lease.eviction_at is not None and day >= lease.eviction_at:
                news.append(News(key, day, "desahucio", 0, member, None))
                lease = rules.member_gone(lease, day)
        elif key in paid:
            rent = lease.rent
            if lease.status == rules.DEFAULTING:
                news.append(News(key, day, "paga", rent, member, None))
            lease = replace(lease, status=rules.RENTED, unpaid=0, claims=0)
            if lease.until is not None and day >= lease.until:
                if lease.leaving == rules.LEAVE_NO_RENEW and rules.tense(index):
                    lease = replace(
                        lease, until=day + timedelta(days=rules.EXTENSION_DAYS),
                        leaving=rules.LEAVE_EXTENDED,
                    )  # fmt: skip
                    news.append(News(key, day, "prorroga", 0, member, None))
                elif lease.leaving:
                    gone = {
                        rules.LEAVE_KEYS: "sale_llaves", rules.LEAVE_NEED: "sale_necesidad"
                    }.get(lease.leaving, "no_renovado")  # fmt: skip
                    news.append(News(key, day, gone, 0, member, None))
                    lease = rules.member_gone(lease, day)
                else:
                    level = RENT_LEVELS["referencia"]
                    months = rules.contract_months(listing, level, lease.term)
                    lease = replace(lease, until=day + timedelta(days=months))
                    news.append(News(key, day, "renueva", 0, member, None))
        else:
            if lease.status == rules.RENTED:
                news.append(News(key, day, "impago", lease.rent, member, None))
            lease = replace(lease, status=rules.DEFAULTING, unpaid=lease.unpaid + 1)
        insurer, lease = rules.insure_month(listing, lease)
        if insurer:
            events = (*events, rules.Event("seguro", insurer))
        return rules.DayOutcome(
            lease=lease, rent=rent, costs=rules.monthly_costs(listing), extra=extra,
            events=events, insurer=insurer,
        )  # fmt: skip

    @staticmethod
    def _decide_in(
        connection: sqlite3.Connection,
        guild_id: int,
        day: date,
        rng: random.Random,
        market: Market,
        key: str,
        result: DayRun,
    ) -> tuple[int, int, list[tuple[str, int]], bool]:
        """Resuelve los imprevistos de un inmueble que ya se decidieron o caducaron.

        Returns:
            `(lo que se paga hoy, cambio del humor, imprevistos que vuelven, parte al seguro)`.
        """
        pay = mood = 0
        relapses: list[tuple[str, int]] = []
        claim = False
        owners = tuple(market.shares_of(key))
        for decision in market.decisions.get(key, ()):
            if decision.choice is None and decision.until >= day:
                continue
            done = rules.resolve(decision.event, decision.choice, decision.cost, rng)
            pay += done.pay
            mood += done.mood
            claim = claim or done.claim
            if done.relapse:
                relapses.append((decision.event, decision.cost))
            connection.execute("DELETE FROM housing_decisions WHERE id = ?", (decision.id,))
            choice = decision.choice or rules.DECISIONS[decision.event][0].key
            result.resolved.append((owners, decision.event, choice))
            if done.relapse:
                result.resolved.append((owners, decision.event, "vuelve"))
        return pay, mood, relapses, claim

    @staticmethod
    def _candidates_in(
        connection: sqlite3.Connection,
        guild_id: int,
        day: date,
        market: Market,
        key: str,
        outcome: rules.DayOutcome,
        lease: rules.Lease,
        result: DayRun,
    ) -> None:
        """Guarda los candidatos que llegan hoy y quita los de un piso que ya no los espera."""
        if lease.status != rules.VACANT or lease.pick != rules.PICK_SELF:
            connection.execute(
                "DELETE FROM housing_candidates WHERE guild_id = ? AND listing = ?",
                (guild_id, key),
            )
            return
        waiting = list(market.candidates_of(key, day))
        for event in outcome.events:
            if event.key != "candidato" or not event.tenant:
                continue
            if len(waiting) >= rules.MAX_CANDIDATES or event.tenant in waiting:
                continue
            until = day + timedelta(days=rules.CANDIDATE_DAYS)
            connection.execute(
                "INSERT INTO housing_candidates (guild_id, listing, tenant, until) "
                "VALUES (?, ?, ?, ?)",
                (guild_id, key, event.tenant, until.isoformat()),
            )
            waiting.append(event.tenant)
            result.candidates.append((key, event.tenant))

    @staticmethod
    def _event_news(key: str, day: date, outcome: rules.DayOutcome) -> list[News]:
        tenant = outcome.lease.tenant
        return [News(key, day, e.key, e.cost, None, e.tenant or tenant) for e in outcome.events]

    @staticmethod
    def _log_news(
        connection: sqlite3.Connection, guild_id: int, now: float, items: list[News],
        result: DayRun,
    ) -> None:  # fmt: skip
        for item in items:
            result.news.append(item)
            _news_in(connection, guild_id, item, now)

    # -- VPO ----------------------------------------------------------------------------

    async def vpo_signup(self, guild_id: int, user_id: int, now: float, join: bool) -> None:
        """Se apunta (o se borra) del sorteo de VPO.

        Raises:
            HousingError: Si ya tiene vivienda, VPO o una reserva.
        """
        await self._run(self._write, self._vpo_signup_in, guild_id, user_id, now, join)

    @staticmethod
    def _vpo_signup_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, now: float, join: bool
    ) -> None:
        if not join:
            connection.execute(
                "DELETE FROM housing_vpo_signups WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            return
        market = _market_in(connection, guild_id)
        if not _vpo_eligible(market, user_id):
            raise HousingError("La VPO es para quien no tiene casa. Tú ya tienes dónde vivir.")
        if market.reservation_of(user_id):
            raise HousingError("Ya te ha tocado una: cómprala antes de que caduque.")
        connection.execute(
            "INSERT OR IGNORE INTO housing_vpo_signups (guild_id, user_id, since) VALUES (?, ?, ?)",
            (guild_id, user_id, now),
        )

    def vpo_buy(
        self, guild_id: int, user_id: int, unit: str, day: date, now: float, *, income: int
    ) -> Callable[[sqlite3.Connection], tuple[VpoDeal, HousingMove]]:
        """Compra la VPO que le ha tocado: precio tasado, hipoteca al 100 % con aval del Estado.

        La primera entrega de una VPO de promoción pública lleva IGIC al tipo cero (art. 52
        de la Ley 4/2012): solo se paga la notaría. El precio va a la cuenta del Estado
        (la promoción es pública) y lo presta el banco: es dinero nuevo que se destruye al
        pagar las cuotas.

        Raises (dentro de la transacción):
            HousingError: Si no le ha tocado, caducó, ya tiene casa o el banco no presta.
        """
        spec = VPO_BY_KEY.get(unit)

        def run(connection: sqlite3.Connection) -> tuple[VpoDeal, HousingMove]:
            if spec is None:
                raise HousingError("Esa VPO no existe.")
            market = _market_in(connection, guild_id)
            reservation = market.reservations.get(unit)
            if reservation is None or reservation[0] != user_id or reservation[1] < day:
                raise HousingError("Esa VPO no te ha tocado (o se te pasó el plazo).")
            if not _vpo_eligible(market, user_id):
                raise HousingError("La VPO es para quien no tiene casa.")
            home = market.homes.get(user_id)
            if home is not None and home[0] == finance.HOME_RENTED:
                # Vivía de alquiler en el piso de otro miembro: se muda a su VPO.
                lease = market.leases.get(home[1])
                if lease is not None:
                    _save_lease_in(connection, guild_id, home[1], rules.member_gone(lease, day))
                    _news_in(connection, guild_id, News(home[1], day, "se_va", 0, user_id, None),
                             now)  # fmt: skip
            rate = market.euribor + rules.MORTGAGE_SPREAD
            loan = round(spec.price * rules.VPO_LTV)
            payment = rules.installment(loan, rate)
            if market.in_asnef(user_id):
                raise HousingError(lending.ASNEF_REFUSAL)
            current = market.installments_of(user_id)
            if not rules.affordable(income, current + payment):
                raise HousingError(
                    f"Ni con el aval del Estado: la cuota ({format_amount(payment)} al mes) pasa "
                    f"del 35 % de lo que ingresas ({format_amount(income)} al día). Ficha más."
                )
            notary = round(spec.price * rules.NOTARY_RATE)
            connection.execute(
                "INSERT INTO housing_vpo (guild_id, unit, owner_id, paid, since) "
                "VALUES (?, ?, ?, ?, ?)",
                (guild_id, unit, user_id, spec.price, now),
            )
            connection.execute(
                "DELETE FROM housing_vpo_reservations WHERE guild_id = ? AND unit = ?",
                (guild_id, unit),
            )
            finance.add_mortgage_in(
                connection, guild_id, user_id, finance.vpo_ref(unit), loan, rate, payment, now,
                aval="ico",
            )  # fmt: skip
            finance.set_home_in(connection, guild_id, user_id, finance.HOME_VPO, unit, now)
            _news_in(
                connection, guild_id,
                News(finance.vpo_ref(unit), day, "compra_vpo", spec.price, user_id, None), now,
            )  # fmt: skip
            deal = VpoDeal(unit, spec.price, notary=notary, loan=loan, payment=payment)
            notary_igic = igic(notary, rules.SERVICES_IGIC)
            return deal, HousingMove(
                entries=(
                    (user_id, LedgerEntry(-notary, "pisos:notaria")),
                    (STATE_ACCOUNT_ID, LedgerEntry(spec.price, "vpo:venta")),
                ),
                taxes=(HousingTax(user_id, "igic_gastos", notary, notary_igic),),
            )

        return run

    def vpo_return(
        self, guild_id: int, user_id: int, day: date, now: float
    ) -> Callable[[sqlite3.Connection], tuple[VpoDeal, HousingMove]]:
        """Devuelve la VPO: la Administración la recompra a precio tasado (derecho de tanteo).

        Lo paga la cuenta del Estado; con eso se cancela la hipoteca y las deudas de la
        VPO, y la VPO vuelve al sorteo.

        Raises (dentro de la transacción):
            HousingError: Si no tiene VPO.
            InsufficientFundsError: Si el Estado no tiene para recomprarla.
        """

        def run(connection: sqlite3.Connection) -> tuple[VpoDeal, HousingMove]:
            market = _market_in(connection, guild_id)
            unit = market.vpo_of(user_id)
            if unit is None:
                raise HousingError("No tienes ninguna VPO.")
            owner = market.vpo[unit]
            spec = VPO_BY_KEY[unit]
            mortgages = market.mortgages_of(user_id, finance.vpo_ref(unit))
            payoff = sum(m.owed for m in mortgages)
            for mortgage in mortgages:
                finance.close_mortgage_in(connection, mortgage.id, "cancelada")
            connection.execute(
                "DELETE FROM housing_vpo WHERE guild_id = ? AND unit = ?", (guild_id, unit)
            )
            finance.clear_home_in(connection, guild_id, user_id=user_id)
            _news_in(
                connection, guild_id,
                News(finance.vpo_ref(unit), day, "devuelve_vpo", spec.price, user_id, None), now,
            )  # fmt: skip
            other = owner.arrears - owner.arrears_ibi
            deal = VpoDeal(unit, spec.price, payoff=payoff, returned=True)
            return deal, HousingMove(
                entries=(
                    (STATE_ACCOUNT_ID, LedgerEntry(-spec.price, "vpo:recompra")),
                    (user_id, LedgerEntry(spec.price, "vpo:recompra")),
                    (user_id, LedgerEntry(-payoff, "pisos:hipoteca_cancelada")),
                    (user_id, LedgerEntry(-other, "pisos:comunidad")),
                ),
                taxes=(HousingTax(user_id, "ibi", owner.arrears_ibi, owner.arrears_ibi),),
            )

        return run

    # -- Vivir y alquilar a miembros ----------------------------------------------------

    async def set_home(self, guild_id: int, user_id: int, key: str, day: date, now: float) -> None:
        """Se muda a un piso suyo: deja de alquilarse y pasa a ser su vivienda habitual.

        Raises:
            HousingError: Si no es entero suyo, no es vivienda, está alquilado o ya vive
                en otro sitio.
        """
        await self._run(self._write, self._set_home_in, guild_id, user_id, key, day, now)

    @staticmethod
    def _set_home_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, key: str, day: date,
        now: float,
    ) -> None:  # fmt: skip
        market = _market_in(connection, guild_id)
        listing = _listing(market, key)
        if market.shares_of(key).get(user_id, 0) != rules.SHARES:
            raise HousingError("Solo puedes vivir en un piso que sea entero tuyo.")
        if listing.kind not in rules.HOME_KINDS:
            raise HousingError("En un trastero o un garaje no se empadrona nadie. Todavía.")
        if user_id in market.homes:
            raise HousingError("Ya vives en otro sitio: múdate primero.")
        _check_reclaim_wait(market, user_id)
        lease = market.leases[key]
        if lease.status != rules.VACANT:
            raise HousingError("Está ocupado: tiene que estar vacío para que te mudes.")
        _save_lease_in(connection, guild_id, key, replace(lease, status=rules.HABITUAL, since=day))
        finance.set_home_in(connection, guild_id, user_id, finance.HOME_OWN, key, now)
        _news_in(connection, guild_id, News(key, day, "se_muda", 0, user_id, None), now)

    # -- El negocio propio de `pala` en un local tuyo (fase 2 de los negocios) ------------
    #
    # Las llama `bot.services.negocio` con la conexión de su propia transacción, la que mueve
    # el dinero del traslado: el local y el dinero se guardan juntos o no se guarda nada.

    @staticmethod
    def business_locals_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int
    ) -> list[tuple[Listing, str]]:
        """Los locales y naves enteros de alguien, con su estado (`VACANT`, `OWN_BUSINESS`…)."""
        market = _market_in(connection, guild_id)
        out = []
        for key in market.keys():
            listing = market.listing(key)
            lease = market.leases.get(key)
            if (
                listing is None
                or lease is None
                or listing.kind not in rules.BUSINESS_SPACE_KINDS
                or market.shares_of(key).get(user_id, 0) != rules.SHARES
            ):
                continue
            out.append((listing, lease.status))
        return out

    @staticmethod
    def occupy_business_local_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, key: str, day: date,
        now: float,
    ) -> Listing:  # fmt: skip
        """Lleva el negocio propio a un local suyo: deja de alquilarse y de poder venderse.

        Raises:
            HousingError: Si no es un local o una nave entera suya, vacía y sin anticipo.
        """
        market = _market_in(connection, guild_id)
        listing = _listing(market, key)
        if listing.kind not in rules.BUSINESS_SPACE_KINDS:
            raise HousingError("Ahí no cabe un negocio: tiene que ser un local o una nave.")
        if market.shares_of(key).get(user_id, 0) != rules.SHARES:
            raise HousingError("Solo puedes llevar tu negocio a un local que sea entero tuyo.")
        lease = market.leases.get(key)
        if lease is None or lease.status != rules.VACANT or lease.member is not None:
            raise HousingError("Está ocupado: tiene que estar vacío para llevar ahí tu negocio.")
        if market.advance_of(user_id, key) is not None:
            raise HousingError("Tienes sus rentas cedidas al banco: cancela antes el anticipo.")
        _save_lease_in(
            connection, guild_id, key, replace(lease, status=rules.OWN_BUSINESS, since=day)
        )
        _news_in(connection, guild_id, News(key, day, "abre_negocio", 0, user_id, None), now)
        return listing

    @staticmethod
    def free_business_local_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, key: str, day: date,
        now: float,
    ) -> None:  # fmt: skip
        """Saca el negocio propio de un local: vuelve a estar vacío (si aún lo tenía dentro)."""
        lease = _market_in(connection, guild_id).leases.get(key)
        if lease is None or lease.status != rules.OWN_BUSINESS:
            return
        _save_lease_in(connection, guild_id, key, replace(lease, status=rules.VACANT, since=day))
        _news_in(connection, guild_id, News(key, day, "cierra_negocio", 0, user_id, None), now)

    @staticmethod
    def business_local_ok_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, key: str
    ) -> bool:
        """Si el negocio sigue teniendo su local propio: entero de su dueño y con el negocio."""
        market = _market_in(connection, guild_id)
        lease = market.leases.get(key)
        return (
            lease is not None
            and lease.status == rules.OWN_BUSINESS
            and market.shares_of(key).get(user_id, 0) == rules.SHARES
        )

    async def leave_home(self, guild_id: int, user_id: int, day: date, now: float) -> str:
        """Deja la vivienda habitual (su piso o el que alquila a otro miembro).

        Returns:
            El tipo de vivienda que deja.

        Raises:
            HousingError: Si no vive en ningún sitio, vive en una VPO (se devuelve) o pidió
                el piso para vivir y aún no ha vivido ahí lo que debe (`leave_home_paying`).
        """
        return await self._run(self._write, self._leave_home_in, guild_id, user_id, day, now)

    def leave_home_paying(
        self, guild_id: int, user_id: int, day: date, now: float
    ) -> Callable[[sqlite3.Connection], tuple[int, HousingMove]]:
        """Deja antes de tiempo el piso que pidió para vivir: paga la indemnización del
        art. 9.3 LAU al inquilino al que echó. Devuelve lo pagado.

        Si era un miembro, la cobra él como ganancia patrimonial del ahorro (arts. 33.1 y 49
        LIRPF, DGT V3259-23), con el 19 % descontado como la plusvalía del Rastro; si era del
        catálogo, desaparece (no existe). No es una mejora ni un gasto del inmueble: no compra
        nada ni sirve para obtener rentas.

        Raises (dentro de la transacción):
            HousingError: Si no hay nada que pagar o no se puede dejar la casa.
            InsufficientFundsError: Si no le llega. No se muda.
        """

        def run(connection: sqlite3.Connection) -> tuple[int, HousingMove]:
            market = _market_in(connection, guild_id)
            found = market.reclaim_of(user_id)
            home = market.homes.get(user_id)
            if (
                found is None or found[1].until is None or day >= found[1].until
                or home != (finance.HOME_OWN, found[0])
            ):  # fmt: skip
                raise HousingError("No tienes ninguna indemnización que pagar.")
            key, reclaim = found
            self._leave_home_in(connection, guild_id, user_id, day, now, paying=True)
            connection.execute(
                "DELETE FROM housing_reclaims WHERE guild_id = ? AND listing = ?", (guild_id, key)
            )
            _news_in(connection, guild_id, News(key, day, "necesidad_multa", reclaim.fine,
                                                user_id, None), now)  # fmt: skip
            return reclaim.fine, _indemnity_move(
                user_id, reclaim.member, reclaim.fine, "pisos:indemnizacion"
            )

        return run

    @staticmethod
    def _leave_home_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, day: date, now: float,
        paying: bool = False,
    ) -> str:  # fmt: skip
        market = _market_in(connection, guild_id)
        home = market.homes.get(user_id)
        if home is None:
            raise HousingError("No vives en ningún piso del mercado: vives con tus padres.")
        kind, ref = home
        if kind == finance.HOME_VPO:
            raise HousingError("De la VPO no se sale así: hay que devolverla.")
        lease = market.leases.get(ref)
        reclaim = market.reclaims.get(ref)
        if (
            kind == finance.HOME_OWN and reclaim is not None and reclaim.user_id == user_id
            and reclaim.until is not None and day < reclaim.until and not paying
        ):  # fmt: skip
            raise HousingError(
                f"Lo pediste para vivir: hasta el {reclaim.until:%d/%m} tienes que vivir aquí. "
                f"Si te vas antes, pagas {format_amount(reclaim.fine)} de indemnización."
            )
        if lease is not None and kind == finance.HOME_RENTED:
            _save_lease_in(connection, guild_id, ref, rules.member_gone(lease, day))
        elif lease is not None:
            _save_lease_in(
                connection, guild_id, ref,
                replace(lease, status=rules.VACANT, member=None, bono=False, rent=0,
                                last_rent=lease.rent,
                        until=None, since=day, unpaid=0, eviction_at=None),
            )  # fmt: skip
        finance.clear_home_in(connection, guild_id, user_id=user_id)
        _news_in(connection, guild_id, News(ref, day, "se_va", 0, user_id, None), now)
        return kind

    def rent_to_member(
        self,
        guild_id: int,
        owner_id: int,
        key: str,
        member_id: int,
        day: date,
        now: float,
        *,
        bono: bool,
        income: int,
        aval: bool = False,
    ) -> Callable[[sqlite3.Connection], tuple[TenantDeal, HousingMove]]:
        """Alquila un piso a un miembro con la renta que pide el inmueble, lo que dure el
        contrato que eligió el dueño (`Lease.term`; 5 años, 60 días, si no eligió).

        El miembro deja la fianza de un mes y, si su nómina no llega a 3 veces la renta y
        no le avalan sus padres, la garantía adicional de dos meses más
        (`housing.deposit_for`). Va al depósito de fianzas y vuelve cuando se va.

        Args:
            income: Neto medio de nómina al día del miembro (`work_income`).
            aval: Si sus padres le avalan (el favor de `pala`): no se pide la garantía.

        Raises (dentro de la transacción):
            HousingError: Si no lo decide quien lo ofrece, no está vacío, el miembro ya
                vive en otro sitio o es uno de los dueños.
            InsufficientFundsError: Si no le llega para la fianza. No se firma nada.
        """

        def run(connection: sqlite3.Connection) -> tuple[TenantDeal, HousingMove]:
            market = _market_in(connection, guild_id)
            listing = _whole(market, key)
            holders = market.shares_of(key)
            if market.manager(key) != owner_id:
                raise HousingError("Lo alquila quien tiene más de la mitad del inmueble.")
            if listing.kind not in rules.HOME_KINDS:
                raise HousingError("Eso no es una vivienda.")
            if member_id in holders:
                raise HousingError("No puedes alquilarle el piso a uno de sus dueños.")
            if member_id in market.homes:
                raise HousingError("Ya vive en otro sitio.")
            _check_reclaim_wait(market, member_id)
            lease = market.leases[key]
            if lease.status != rules.VACANT:
                raise HousingError("Tiene que estar vacío para firmar con otro inquilino.")
            level = RENT_LEVELS[
                "referencia" if lease.level == rules.VACATION_LEVEL else lease.level
            ]
            rent = rules.asking_rent(listing, level, market.index(listing.barrio))
            short = rules.needs_guarantee(rent, income, aval=False)
            guarantee = short and not aval
            deposit = rules.deposit_for(rent, guarantee=guarantee)
            # Un miembro vive en él: es alquiler de vivienda aunque el dueño pidiera otra renta.
            reduction = rules.contract_reduction(
                listing, "referencia", index=market.index(listing.barrio), rent=rent,
                last_rent=lease.last_rent, day=day,
            )  # fmt: skip
            # Pasa el estudio de solvencia de la aseguradora si su nómina llega a tres veces
            # la renta (sin aval: la aseguradora mira lo que gana él).
            bonus = _bonus_ready(market, key, listing, lease, day)
            months = rules.contract_months(listing, RENT_LEVELS["referencia"], lease.term)
            lease = replace(lease, status=rules.RENTED, tenant=None, member=member_id,
                            bono=bono, rent=rent, until=day + timedelta(days=months), since=day,
                            unpaid=0, reduction=reduction, claims=0, solvent=not short,
                            bonus=bonus, signed=day, leaving="", keys_ask=0,
                            keys_wait=None)  # fmt: skip
            connection.execute(
                "DELETE FROM housing_candidates WHERE guild_id = ? AND listing = ?",
                (guild_id, key),
            )
            _save_lease_in(connection, guild_id, key, lease)
            finance.set_home_in(connection, guild_id, member_id, finance.HOME_RENTED, key, now)
            finance.add_deposit_in(connection, guild_id, key, member_id, deposit, now)
            _news_in(connection, guild_id, News(key, day, "firma_miembro", rent, member_id,
                                                None), now)  # fmt: skip
            move = HousingMove(
                entries=(
                    (member_id, LedgerEntry(-deposit, "pisos:fianza")),
                    (HOUSING_DEPOSIT_ACCOUNT_ID, LedgerEntry(deposit, "pisos:fianza")),
                )
            )
            return TenantDeal(lease, deposit, guarantee, short and aval), move

        return run

    def _deposits_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        day: date,
        now: float,
        market: Market,
        rents: list[HousingRent],
        entries: list[tuple[int, LedgerEntry]],
        result: DayRun,
    ) -> None:
        """Devuelve las fianzas de quien ya no vive de alquiler en el piso (art. 36.4 LAU).

        Lo que se queda el casero por los meses que debía (`forfeit`) es renta del
        alquiler para los dueños de hoy, con su IRPF; si ya no tiene dueños, vuelve
        entera al inquilino. Sale todo del depósito de fianzas: no se crea dinero.
        """
        for deposit in finance.deposits_in(connection, guild_id):
            lease = market.leases.get(deposit.listing)
            if lease is not None and lease.member == deposit.user_id:
                continue
            holders = market.shares_of(deposit.listing)
            kept = deposit.forfeit if holders else 0
            # La renta que debía un miembro: vivienda, con la reducción general.
            for member, part in rules.split(kept, holders).items():
                if part > 0:
                    rents.append(
                        HousingRent(
                            member, part, ref=deposit.listing, reduction=rules.RENT_REDUCTION
                        )
                    )
            refund = deposit.amount - kept
            entries.append(
                (HOUSING_DEPOSIT_ACCOUNT_ID, LedgerEntry(-deposit.amount, "pisos:fianza"))
            )
            entries.append((deposit.user_id, LedgerEntry(refund, "pisos:fianza")))
            finance.delete_deposit_in(connection, guild_id, deposit.listing, deposit.user_id)
            items = []
            if refund:
                items.append(
                    News(deposit.listing, day, "fianza_devuelta", refund, deposit.user_id, None)
                )
            if kept:
                items.append(
                    News(deposit.listing, day, "fianza_incautada", kept, deposit.user_id, None)
                )
            self._log_news(connection, guild_id, now, items, result)

    # -- Fase 4: licencias, SOCIMI ------------------------------------------------------

    def buy_license(
        self,
        guild_id: int,
        user_id: int,
        key: str,
        day: date,
        now: float,
        rng: random.Random | None = None,
    ) -> Callable[[sqlite3.Connection], tuple[int, HousingMove]]:
        """Pide la licencia de vivienda vacacional (una por barrio); la tasa va al Estado.

        Ley 6/2025 de Canarias: no hay licencias nuevas en zona tensionada ni en obra nueva, y
        un piso en un bloque necesita que lo apruebe la comunidad (3/5, art. 17.12 LPH). Los
        vecinos votan al pedirla: si dicen que no, no se paga la tasa y no se puede volver a
        pedir en una semana. Devuelve la tasa pagada (0 si la comunidad dijo que no).

        Raises (dentro de la transacción):
            HousingError: Si no lo decide él, no es vivienda, vive alguien, la ley no deja o
                el barrio ya no tiene plazas.
        """
        dice = rng or random.Random()

        def run(connection: sqlite3.Connection) -> tuple[int, HousingMove]:
            market = _market_in(connection, guild_id)
            _whole(market, key)
            if market.manager(key) != user_id:
                raise HousingError(
                    "La licencia la pide quien administra el inmueble (en uno con varios "
                    "dueños, también la puede votar la junta)."
                )
            return self._license_in(
                connection, guild_id, market, key, {user_id: 1}, user_id, day, now, dice
            )

        return run

    @staticmethod
    def _license_in(
        connection: sqlite3.Connection,
        guild_id: int,
        market: Market,
        key: str,
        payers: dict[int, int],
        user_id: int,
        day: date,
        now: float,
        dice: random.Random,
    ) -> tuple[int, HousingMove]:
        """La licencia vacacional, con la tasa repartida entre `payers` por participaciones.

        Devuelve la tasa (0 si los vecinos votan que no). Ver `buy_license`.
        """
        listing = _listing(market, key)
        if listing.kind not in rules.HOME_KINDS:
            raise HousingError("Un garaje vacacional no lo aprueba ni el Cabildo.")
        lease = market.leases[key]
        if lease.license:
            raise HousingError("Ya tiene licencia.")
        if lease.status == rules.HABITUAL or lease.member is not None:
            raise HousingError("Vive alguien: no se puede convertir en vacacional.")
        if market.licensed_in(listing.barrio) >= rules.LICENSE_PER_BARRIO:
            raise HousingError(
                "El barrio ya no tiene plazas vacacionales: está saturado. Canarias tiene "
                "un límite (y este barrio, también)."
            )
        problem = rules.license_problem(
            listing, index=market.index(listing.barrio), promotion=rules.is_promotion(key)
        )
        if problem is not None:
            raise HousingError(problem)
        if lease.license_denied_until is not None and day < lease.license_denied_until:
            raise HousingError(
                "La comunidad ya votó que no. Hasta el "
                f"{lease.license_denied_until.strftime('%d/%m')} no se vuelve a votar."
            )
        if rules.needs_neighbours(listing) and dice.random() >= rules.NEIGHBOURS_APPROVE:
            until = day + timedelta(days=rules.LICENSE_RETRY_DAYS)
            denied = replace(lease, license_denied_until=until)
            _save_lease_in(connection, guild_id, key, denied)
            _news_in(connection, guild_id, News(key, day, "licencia_denegada", 0, user_id,
                                                None), now)  # fmt: skip
            return 0, HousingMove()
        _save_lease_in(
            connection, guild_id, key,
            replace(lease, license=True, license_until=day + timedelta(days=rules.LICENSE_DAYS),
                    license_denied_until=None),
        )  # fmt: skip
        _news_in(connection, guild_id, News(key, day, "licencia", rules.LICENSE_FEE,
                                            user_id, None), now)  # fmt: skip
        fee = rules.LICENSE_FEE
        return fee, HousingMove(
            taxes=tuple(
                HousingTax(member, "licencia", part, part)
                for member, part in rules.split(fee, payers).items()
                if part
            )
        )

    def found_socimi(
        self, guild_id: int, user_id: int, day: date, now: float
    ) -> Callable[[sqlite3.Connection], tuple[int, HousingMove]]:
        """Monta su SOCIMI: hace falta tener 3 inmuebles enteros y pagar la salida a bolsa.

        La salida (notaría, registro y el BME Growth) desaparece.

        Raises (dentro de la transacción):
            HousingError: Si ya la tiene o no tiene inmuebles suficientes.
        """

        def run(connection: sqlite3.Connection) -> tuple[int, HousingMove]:
            market = _market_in(connection, guild_id)
            if user_id in market.socimi:
                raise HousingError("Ya tienes tu SOCIMI.")
            if market.whole_homes(user_id) < rules.SOCIMI_HOMES:
                raise HousingError(
                    f"Para una SOCIMI hacen falta {rules.SOCIMI_HOMES} viviendas enteras. "
                    "Hacerse fondo cuesta."
                )
            connection.execute(
                "INSERT INTO housing_socimi (guild_id, user_id, since) VALUES (?, ?, ?)",
                (guild_id, user_id, now),
            )
            fee = rules.SOCIMI_FEE
            return fee, HousingMove(entries=((user_id, LedgerEntry(-fee, "pisos:socimi")),))

        return run

    # -- Lo que no mueve dinero -------------------------------------------------------

    async def set_level(self, guild_id: int, user_id: int, key: str, level: str) -> rules.Lease:
        """Cambia la renta que pide el inmueble (solo quien tiene la mayoría).

        Vale para el próximo contrato: a mitad de uno no se puede subir (art. 18 LAU).

        Raises:
            HousingError: Si no tiene la mayoría, el nivel no existe, pide licencia o el
                gran tenedor tiene tope en zona tensionada.
        """
        return await self._run(self._write, self._set_level_in, guild_id, user_id, key, level)

    @staticmethod
    def _set_level_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, key: str, level: str,
        *, junta: bool = False,
    ) -> rules.Lease:  # fmt: skip
        if level not in RENT_LEVELS:
            raise HousingError("Esa renta no existe.")
        market = _market_in(connection, guild_id)
        listing = _listing(market, key)
        if not junta and market.manager(key) != user_id:
            raise HousingError(
                "La renta la decide quien tiene más de la mitad del inmueble "
                "(art. 398 del Código Civil)."
            )
        lease = market.leases[key]
        if level == rules.VACATION_LEVEL and not lease.license:
            raise HousingError("Sin licencia vacacional no se alquila a turistas. Pídela antes.")
        if not rules.level_allowed(
            level, index=market.index(listing.barrio), whole_homes=market.whole_homes(user_id),
            license=lease.license,
        ):  # fmt: skip
            raise HousingError(
                "Eres gran tenedor en una zona tensionada: la Ley de Vivienda no te deja pedir "
                "más que la renta de referencia (art. 17 de la Ley 12/2023)."
            )
        lease = replace(lease, level=level)
        _save_lease_in(connection, guild_id, key, lease)
        return lease

    # -- Ampliación, fase 4: reformas e imprevistos ----------------------------------

    def start_reform(
        self, guild_id: int, user_id: int, key: str, reform: str, day: date, now: float
    ) -> Callable[[sqlite3.Connection], tuple[ReformDone, HousingMove]]:
        """Empieza una reforma: paga la obra con su IGIC, el ICIO y la licencia, y entra en obras.

        Solo la hace quien tiene el inmueble entero (en un inmueble con varios dueños, lo vota
        la junta), con él vacío. Para el IRPF es una mejora, no un gasto: suma a lo que costó
        el inmueble (art. 35.1.b LIRPF) y a lo pagado (Patrimonio y amortización).

        Raises (dentro de la transacción):
            HousingError: Si no se puede.
            InsufficientFundsError: Si no le llega.
        """

        def run(connection: sqlite3.Connection) -> tuple[ReformDone, HousingMove]:
            market = _market_in(connection, guild_id)
            if market.shares_of(key).get(user_id, 0) != rules.SHARES:
                raise HousingError(
                    "Solo reforma quien tiene el inmueble entero (con varios dueños, la vota "
                    "la junta)."
                )
            return self._reform_in(
                connection, guild_id, market, key, reform, {user_id: rules.SHARES}, user_id,
                day, now,
            )  # fmt: skip

        return run

    @staticmethod
    def _reform_in(
        connection: sqlite3.Connection,
        guild_id: int,
        market: Market,
        key: str,
        reform: str,
        payers: dict[int, int],
        user_id: int,
        day: date,
        now: float,
    ) -> tuple[ReformDone, HousingMove]:
        """Una reforma pagada por `payers` según sus participaciones (ver `start_reform`)."""
        listing = _listing(market, key)
        spec = REFORM_BY_KEY.get(reform)
        if spec is None or listing.kind not in spec.kinds:
            raise HousingError("Esa reforma no se le puede hacer a este inmueble.")
        if sum(payers.values()) != rules.SHARES:
            raise HousingError(
                "Hay participaciones sin dueño: la reforma la tiene que pagar el inmueble entero."
            )
        if reform in market.reforms.get(key, {}):
            raise HousingError("Esa reforma ya está hecha (o en obras).")
        lease = market.leases.get(key, rules.Lease())
        if lease.status != rules.VACANT:
            raise HousingError("Para reformar tiene que estar vacío.")
        for payer in payers:
            _check_free(market, key, payer, day)
        quote = rules.reform_quote(listing, spec, market.index(listing.barrio), market.ipc)
        until = day + timedelta(days=spec.days)
        connection.execute(
            """
            INSERT INTO housing_reforms
                (guild_id, listing, reform, cost, until, finished, rehab, user_id, created_at)
            VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (guild_id, key, reform, quote.cost, until.isoformat(), int(quote.rehab),
             user_id, now),
        )  # fmt: skip
        costs = rules.split(quote.cost, payers)
        igics = rules.split(quote.igic, payers)
        icios = rules.split(quote.icio, payers)
        licenses = rules.split(quote.license, payers)
        for payer in payers:
            connection.execute(
                "UPDATE housing_holdings SET paid = paid + ?, cost = cost + ? "
                "WHERE guild_id = ? AND listing = ? AND user_id = ?",
                (costs[payer], costs[payer] + igics[payer] + icios[payer] + licenses[payer],
                 guild_id, key, payer),
            )  # fmt: skip
        _save_lease_in(
            connection, guild_id, key,
            replace(lease, status=rules.OBRAS, since=day, works_until=until),
        )  # fmt: skip
        connection.execute(
            "DELETE FROM housing_candidates WHERE guild_id = ? AND listing = ?",
            (guild_id, key),
        )
        _news_in(connection, guild_id, News(key, day, "obras", quote.cost, user_id, None), now)
        move = HousingMove(
            entries=tuple((payer, LedgerEntry(-costs[payer], "pisos:reforma")) for payer in payers),
            taxes=tuple(
                tax
                for payer in payers
                for tax in (
                    HousingTax(payer, "igic_gastos", costs[payer], igics[payer]),
                    HousingTax(payer, "icio", costs[payer], icios[payer]),
                    HousingTax(payer, "licencia_obra", licenses[payer], licenses[payer]),
                )
                if tax.tax
            ),
        )
        return ReformDone(key, reform, quote, until), move

    def split(
        self, guild_id: int, user_id: int, key: str, mode: str, day: date, now: float
    ) -> Callable[[sqlite3.Connection], tuple[SplitDone, HousingMove]]:
        """Divide un inmueble por plantas o lo prepara para alquilarlo por habitaciones.

        Solo lo hace quien lo tiene entero (con varios dueños, lo vota la junta: por plantas,
        por unanimidad), con él vacío. Paga la obra (o los muebles) con su IGIC y, por plantas,
        el ICIO, la licencia de obra y el AJD de la escritura de división horizontal. Para el
        IRPF es una mejora, como una reforma: suma a lo que costó el inmueble (art. 35.1.b
        LIRPF) y a lo pagado (Patrimonio y amortización).

        Raises (dentro de la transacción):
            HousingError: Si no se puede.
            InsufficientFundsError: Si no le llega.
        """

        def run(connection: sqlite3.Connection) -> tuple[SplitDone, HousingMove]:
            market = _market_in(connection, guild_id)
            if market.shares_of(key).get(user_id, 0) != rules.SHARES:
                raise HousingError(
                    "Solo lo divide quien tiene el inmueble entero (con varios dueños, lo vota "
                    "la junta)."
                )
            return self._split_in(
                connection, guild_id, market, key, mode, {user_id: rules.SHARES}, user_id, day,
                now,
            )  # fmt: skip

        return run

    def build(
        self,
        guild_id: int,
        user_id: int,
        solar: str,
        project: str,
        level: int,
        day: date,
        now: float,
        *,
        expected: int | None = None,
    ) -> Callable[[sqlite3.Connection], tuple[Built, HousingMove]]:
        """Compra un solar y empieza a construir en él (la rama Ladrillo de `pala`, 9.56.0).

        Las unidades existen desde ya: son del promotor entero (100 participaciones de cada una)
        y están «En obras» hasta `until`. Hasta entonces no se venden al mercado
        (`locked_until`), aunque sí a otros miembros: es la venta sobre plano.

        Dinero (`docs/solares.md`): el solar y la obra desaparecen (el vendedor y la constructora
        no existen); el ITP del solar (art. 31 del DL 1/2009), el IGIC de su notaría y de la obra
        (art. 51 de la Ley 4/2012), el ICIO (art. 102 del RDL 2/2004), la tasa de la licencia de
        obra mayor (art. 20.4.h del RDL 2/2004) y el AJD de la escritura de obra nueva y
        división horizontal (art. 36 del DL 1/2009) van al Estado. Para el IRPF todo es coste de
        adquisición de las unidades (art. 35.1 LIRPF), repartido por lo que vale cada una.

        Args:
            level: El puesto del promotor en la rama Ladrillo (0 si no está en ella).
            expected: Lo que decía el cartel. Si ha subido entretanto, no se cobra.

        Raises (dentro de la transacción):
            HousingError: Si no se puede.
            PriceChanged: Si ahora cuesta más que `expected`.
        """

        def run(connection: sqlite3.Connection) -> tuple[Built, HousingMove]:
            market = _market_in(connection, guild_id)
            site = solar_rules.SOLAR_BY_KEY.get(solar)
            plan = solar_rules.PROJECT_BY_KEY.get(project)
            if site is None or plan is None:
                raise HousingError("Ese solar o ese proyecto no existen.")
            if solar in market.solars:
                raise HousingError("Ese solar ya tiene dueño: alguien se te ha adelantado.")
            if not solar_rules.allowed(site, plan):
                raise HousingError("El plan general del barrio no deja levantar tantas plantas.")
            if level < plan.level:
                raise HousingError(
                    f"Para levantar un {plan.name.lower()} hay que llegar a P{plan.level} en la "
                    "rama Ladrillo de la obra en `pala`."
                )
            busy = next(
                (w for w in market.solars.values() if w.user_id == user_id and w.until > day),
                None,
            )
            if busy is not None:
                raise HousingError(
                    f"Ya tienes una obra en marcha hasta el {busy.until:%d/%m}: una a la vez."
                )
            index = market.index(site.barrio)
            quote = solar_rules.build_quote(site, plan, index, market.ipc)
            if expected is not None and quote.total > expected:
                raise PriceChanged(quote.total)
            until = day + timedelta(days=quote.days)
            _building, units = solar_units(solar, project)
            finance.add_solar_in(
                connection, guild_id,
                SolarWork(solar, user_id, project, day, until, quote.total, quote.value), now,
            )  # fmt: skip
            for unit, (paid, cost) in zip(units, solar_rules.split_paid(quote, units), strict=True):
                connection.execute(
                    """
                    INSERT INTO housing_holdings
                        (guild_id, listing, user_id, shares, paid, cost, since, locked_until)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (guild_id, unit.key, user_id, rules.SHARES, paid, cost, now, _iso(until)),
                )
                _save_lease_in(
                    connection, guild_id, unit.key,
                    rules.Lease(status=rules.OBRAS, level=DEFAULT_RENT_LEVEL, since=day,
                                works_until=until),
                )  # fmt: skip
            _news_in(
                connection, guild_id, News(solar, day, "obra_solar", quote.total, user_id, None),
                now,
            )  # fmt: skip
            done = Built(solar, project, quote, tuple(u.key for u in units), until, index)
            move = HousingMove(
                entries=(
                    (user_id, LedgerEntry(-quote.land, "pisos:solar")),
                    (user_id, LedgerEntry(-quote.notary, "pisos:notaria")),
                    (user_id, LedgerEntry(-quote.works, "pisos:obra_nueva")),
                ),
                taxes=tuple(
                    HousingTax(user_id, kind, base, tax)
                    for kind, base, tax in (
                        ("itp", quote.land, quote.itp),
                        ("igic_gastos", quote.notary, quote.notary_igic),
                        ("igic_gastos", quote.works, quote.works_igic),
                        ("icio", quote.works, quote.icio),
                        ("licencia_obra", quote.works, quote.license),
                        ("ajd", quote.value, quote.ajd),
                    )
                    if tax
                ),
            )
            return done, move

        return run

    def _split_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        market: Market,
        key: str,
        mode: str,
        payers: dict[int, int],
        user_id: int,
        day: date,
        now: float,
    ) -> tuple[SplitDone, HousingMove]:
        """Una división pagada por `payers` según sus participaciones (ver `split`)."""
        listing = _listing(market, key)
        base = market.base(key)
        if mode not in rules.SPLIT_MODES or base is None:
            raise HousingError("Esa forma de dividir no existe.")
        if key in market.splits or rules.is_room(key):
            raise HousingError("Ya está dividido.")
        problem = rules.split_problem(base, mode, promotion=key in market.promotions)
        if problem:
            raise HousingError(problem)
        if sum(payers.values()) != rules.SHARES:
            raise HousingError(
                "Hay participaciones sin dueño: lo tiene que pagar el inmueble entero."
            )
        lease = market.leases.get(key, rules.Lease())
        if lease.status != rules.VACANT or lease.member is not None:
            raise HousingError("Para dividirlo tiene que estar vacío.")
        if market.decisions.get(key):
            raise HousingError("Antes decide los imprevistos que tiene pendientes.")
        for payer in payers:
            _check_free(market, key, payer, day)
            if market.advance_of(payer, key) is not None:
                raise HousingError("Tiene un anticipo de rentas vivo: devuélvelo antes.")
        quote = rules.split_quote(listing, mode, market.index(listing.barrio), market.ipc)
        until = day + timedelta(days=quote.days)
        amounts = {
            name: rules.split(amount, payers)
            for name, amount in (
                ("cost", quote.cost), ("igic", quote.igic), ("icio", quote.icio),
                ("license", quote.license), ("ajd", quote.ajd),
            )
        }  # fmt: skip
        for payer in payers:
            total = sum(part[payer] for part in amounts.values())
            connection.execute(
                "UPDATE housing_holdings SET paid = paid + ?, cost = cost + ? "
                "WHERE guild_id = ? AND listing = ? AND user_id = ?",
                (amounts["cost"][payer], total, guild_id, key, payer),
            )
        connection.execute(
            "INSERT INTO housing_splits (guild_id, listing, mode, units, cost, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, key, mode, quote.units, quote.cost, now),
        )
        market = _market_in(connection, guild_id)
        units = market.units_of(key)
        connection.execute(
            "DELETE FROM housing_candidates WHERE guild_id = ? AND listing = ?", (guild_id, key)
        )
        _clear_reviews_in(connection, guild_id, key)
        if mode == rules.FLOORS:
            self._floors_in(connection, guild_id, market, key, units)
            connection.execute(
                "DELETE FROM housing_leases WHERE guild_id = ? AND listing = ?", (guild_id, key)
            )
        else:
            # El piso deja su contrato «por habitaciones» (sin licencia: la vacacional es de la
            # vivienda entera) y cada habitación tiene el suyo.
            _save_lease_in(
                connection, guild_id, key,
                replace(lease, status=rules.BY_ROOMS, since=day, license=False,
                        license_until=None),
            )  # fmt: skip
        level = "referencia" if lease.level == rules.VACATION_LEVEL else lease.level
        for unit in units:
            _save_lease_in(
                connection, guild_id, unit.key,
                rules.Lease(status=rules.OBRAS, level=level, since=day, works_until=until,
                            pick=lease.pick, insured=lease.insured and mode == rules.FLOORS),
            )  # fmt: skip
        _news_in(connection, guild_id, News(key, day, mode, quote.cost, user_id, None), now)
        taxes = (
            ("igic_gastos", "cost", "igic"), ("icio", "cost", "icio"),
            ("licencia_obra", "license", "license"), ("ajd", "ajd", "ajd"),
        )  # fmt: skip
        move = HousingMove(
            entries=tuple(
                (payer, LedgerEntry(-amounts["cost"][payer], f"pisos:{mode}")) for payer in payers
            ),
            taxes=tuple(
                HousingTax(payer, kind, amounts[base_name][payer], amounts[tax_name][payer])
                for payer in payers
                for kind, base_name, tax_name in taxes
                if amounts[tax_name][payer]
            ),
        )
        return SplitDone(key, mode, quote, tuple(u.key for u in units), until), move

    @staticmethod
    def _floors_in(
        connection: sqlite3.Connection,
        guild_id: int,
        market: Market,
        key: str,
        units: tuple[Listing, ...],
    ) -> None:
        """Pasa a cada planta lo que era del inmueble entero, repartido por lo que vale cada una.

        Cada dueño se queda las mismas participaciones en todas, con lo pagado y lo que le costó
        repartido; cada hipoteca se reparte en una por planta (distribución de la hipoteca, art.
        123 de la Ley Hipotecaria), y las reformas hechas y el fondo de reserva, también.
        """
        weights = [rules.base_price(unit) for unit in units]

        def parts(amount: int) -> list[int]:
            out = [amount * weight // sum(weights) for weight in weights]
            out[-1] += amount - sum(out)  # sin perder ni un Y$: el resto, a la última
            return out

        for holding in market.holdings.get(key, {}).values():
            shares = zip(units, parts(holding.paid), parts(holding.cost), strict=True)
            for unit, paid, cost in shares:
                connection.execute(
                    """
                    INSERT INTO housing_holdings
                        (guild_id, listing, user_id, shares, paid, cost, since, locked_until)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (guild_id, unit.key, holding.user_id, holding.shares, paid, cost,
                     holding.since, _iso(holding.locked_until)),
                )  # fmt: skip
        connection.execute(
            "DELETE FROM housing_holdings WHERE guild_id = ? AND listing = ?", (guild_id, key)
        )
        for mortgage in market.mortgages:
            if mortgage.listing != key:
                continue
            split = zip(
                units, parts(mortgage.principal), parts(mortgage.balance), parts(mortgage.payment),
                parts(mortgage.unpaid_amount), strict=True,
            )  # fmt: skip
            for number, (unit, principal, balance, payment, unpaid) in enumerate(split):
                if number == 0:
                    connection.execute(
                        "UPDATE housing_mortgages SET listing = ?, principal = ?, balance = ?, "
                        "payment = ?, unpaid_amount = ? WHERE id = ?",
                        (unit.key, principal, balance, payment, unpaid, mortgage.id),
                    )
                    continue
                connection.execute(
                    """
                    INSERT INTO housing_mortgages
                        (guild_id, user_id, listing, principal, balance, rate, payment,
                         months_left, unpaid_count, unpaid_amount, aval, status, created_at, kind)
                    SELECT guild_id, user_id, ?, ?, ?, rate, ?, months_left, unpaid_count, ?,
                           aval, status, created_at, kind
                    FROM housing_mortgages WHERE id = ?
                    """,
                    (unit.key, principal, balance, payment, unpaid, mortgage.id),
                )
        for reform, (cost, _until, _finished, _rehab) in market.reforms.get(key, {}).items():
            for unit, part in zip(units, parts(cost), strict=True):
                connection.execute(
                    """
                    INSERT INTO housing_reforms
                        (guild_id, listing, reform, cost, until, finished, rehab, user_id,
                         created_at)
                    SELECT guild_id, ?, reform, ?, until, finished, rehab, user_id, created_at
                    FROM housing_reforms WHERE guild_id = ? AND listing = ? AND reform = ?
                    """,
                    (unit.key, part, guild_id, key, reform),
                )
        for unit, part in zip(units, parts(market.reserves.get(key, 0)), strict=True):
            if part:
                junta.add_reserve_in(connection, guild_id, unit.key, part)
        for table in ("housing_reforms", "housing_reserve", "housing_admins", "housing_offers"):
            # `table` sale de una tupla fija, nunca de entrada del usuario.
            connection.execute(f"DELETE FROM {table} WHERE guild_id = ? AND listing = ?",
                               (guild_id, key))  # fmt: skip
        connection.execute(
            "UPDATE housing_votes SET status = ?, note = ? "
            "WHERE guild_id = ? AND listing = ? AND status = ?",
            (junta.FAILED, "Se dividió por plantas.", guild_id, key, junta.OPEN),
        )

    async def join_rooms(self, guild_id: int, user_id: int, key: str, now: float) -> None:
        """Deja de alquilar un piso por habitaciones: vuelve a alquilarse entero.

        Lo decide quien decide en el piso, con todas las habitaciones vacías. Los muebles se
        quedan donde estaban (no se devuelve nada) y lo que debieran las habitaciones pasa a
        deberlo el piso.

        Raises:
            HousingError: Si no se puede.
        """
        await self._run(self._write, self._join_in, guild_id, user_id, key, local_day(now), now)

    @staticmethod
    def _join_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, key: str, day: date,
        now: float,
    ) -> None:  # fmt: skip
        market = _market_in(connection, guild_id)
        if market.split_mode(key) != rules.ROOMS:
            raise HousingError("No se alquila por habitaciones.")
        if market.manager(key) != user_id:
            raise HousingError("Esto lo decide quien decide en el piso.")
        rooms = [market.leases.get(unit.key, rules.Lease()) for unit in market.units_of(key)]
        if any(room.status not in (rules.VACANT, rules.OBRAS) for room in rooms):
            raise HousingError("Para juntarlas tienen que estar todas vacías.")
        if any(market.decisions.get(unit.key) for unit in market.units_of(key)):
            raise HousingError("Antes decide los imprevistos de las habitaciones.")
        lease = market.leases.get(key, rules.Lease())
        _drop_rooms_in(connection, guild_id, market, key)
        _save_lease_in(
            connection, guild_id, key,
            replace(lease, status=rules.VACANT, since=day,
                    arrears=lease.arrears + sum(room.arrears for room in rooms),
                    arrears_ibi=lease.arrears_ibi + sum(room.arrears_ibi for room in rooms)),
        )  # fmt: skip
        _news_in(connection, guild_id, News(key, day, "juntadas", 0, user_id, None), now)

    async def choose(self, guild_id: int, user_id: int, decision_id: int, choice: str) -> Decision:
        """Elige cómo resolver un imprevisto (se paga con el cierre de la noche).

        Raises:
            HousingError: Si no le toca decidir o la forma no existe.
        """
        return await self._run(self._write, self._choose_in, guild_id, user_id, decision_id, choice)

    @staticmethod
    def _choose_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, decision_id: int, choice: str
    ) -> Decision:
        market = _market_in(connection, guild_id)
        decision = next(
            (d for items in market.decisions.values() for d in items if d.id == decision_id), None
        )
        if decision is None:
            raise HousingError("Ese imprevisto ya está resuelto.")
        holders = market.shares_of(decision.listing)
        boss = market.manager(decision.listing)
        if user_id not in holders or (boss is not None and boss != user_id):
            raise HousingError("Lo decide quien tiene más de la mitad del inmueble.")
        if choice not in {c.key for c in rules.DECISIONS[decision.event]}:
            raise HousingError("Esa forma de arreglarlo no existe.")
        connection.execute(
            "UPDATE housing_decisions SET choice = ?, chooser = ? WHERE id = ?",
            (choice, user_id, decision_id),
        )
        return replace(decision, choice=choice)

    # -- Ampliación, fase 3: vender a un miembro y subastas -------------------------

    def member_sale(
        self,
        guild_id: int,
        seller: int,
        buyer: int,
        key: str,
        shares: int,
        price: int,
        day: date,
        now: float,
        *,
        expected: int | None = None,
        financed: bool = False,
        income: int = 0,
        aval_income: int = 0,
    ) -> Callable[[sqlite3.Connection], tuple[MemberSale, HousingMove]]:
        """Vende participaciones a otro miembro al precio pactado.

        Impuestos (`housing.member_sale_quote`): el comprador paga el ITP sobre el mayor entre
        el precio y el valor de referencia y la notaría con su IGIC; el vendedor, el 19 % de
        la ganancia calculada con el mayor entre el precio y el valor de mercado (art. 35.2
        LIRPF). La parte regalada no paga Donaciones (decisión del proyecto, como el Rastro).
        Lo cobrado cancela la parte de la hipoteca del vendedor. El comprador puede pagar con
        hipoteca (el 80 % del menor entre el precio y el valor). No mueve el índice.

        Args:
            expected: Lo que la oferta dijo que pagaría el comprador de su bolsillo; si ha
                subido (el valor de referencia sube con el barrio), no se vende.

        Raises (dentro de la transacción):
            HousingError: Si no tiene tantas, están en subasta, el banco no presta o el
                comprador es el vendedor.
            PriceChanged: Si al comprador le costaría más que lo que decía la oferta.
            InsufficientFundsError: Si al comprador no le llega.
        """

        def run(connection: sqlite3.Connection) -> tuple[MemberSale, HousingMove]:
            market = _market_in(connection, guild_id)
            listing = _whole(market, key)
            if buyer == seller:
                raise HousingError("No te puedes vender a ti mismo.")
            held = market.holdings.get(key, {}).get(seller)
            if held is None or not 1 <= shares <= held.shares:
                raise HousingError("El vendedor ya no tiene esas participaciones.")
            _check_free(market, key, seller, day)
            _check_no_advance(market, key, seller)
            _check_reclaim(market, key, seller)
            index = market.index(listing.barrio)
            try:
                quote = rules.member_sale_quote(
                    listing, shares, held.shares, held.cost, price, index=index
                )
            except ValueError as error:
                raise HousingError(str(error)) from error
            loan = payment = 0
            rate = 0.0
            aval = False
            if financed:
                loan = round(min(price, quote.value) * rules.MORTGAGE_LTV)
                rate = market.euribor + rules.MORTGAGE_SPREAD
                payment = rules.installment(loan, rate)
                if market.in_asnef(buyer):
                    raise HousingError(lending.ASNEF_REFUSAL)
                current = market.installments_of(buyer)
                if not rules.affordable(income, current + payment):
                    aval = aval_income > 0 and rules.affordable(
                        income + aval_income, current + payment
                    )
                    if not aval:
                        raise HousingError(
                            f"El banco no presta: la cuota ({format_amount(payment)} al mes) más "
                            f"las que ya pagas pasa del 35 % de lo que ingresas "
                            f"({format_amount(income)} al día)."
                        )
            pocket = quote.buyer_total - loan
            if expected is not None and pocket > expected:
                raise PriceChanged(pocket)
            if loan:
                finance.add_mortgage_in(
                    connection, guild_id, buyer, key, loan, rate, payment, now,
                    aval=rules.PARENTS_AVAL if aval else None,
                )  # fmt: skip
            payoff = _shrink_mortgages_in(connection, market, seller, key, shares, held.shares)
            lease = market.leases.get(key, rules.Lease(level=DEFAULT_RENT_LEVEL, since=day))
            moved_in = _move_shares_in(
                connection, guild_id, market, key, seller, buyer, shares, paid=price,
                cost=quote.buyer_total, day=day, now=now, lease=lease,
            )  # fmt: skip
            _news_in(
                connection, guild_id, News(key, day, "venta_miembro", price, seller, None), now
            )
            _news_in(
                connection, guild_id, News(key, day, "compra_miembro", price, buyer, None), now
            )
            sale = MemberSale(
                key, quote, seller, buyer, loan=loan, payment=payment, rate=rate, aval=aval,
                payoff=payoff, moved_in=moved_in,
            )  # fmt: skip
            move = HousingMove(
                entries=(
                    (buyer, LedgerEntry(-(price - loan), "pisos:compra_miembro")),
                    (seller, LedgerEntry(price, "pisos:venta_miembro")),
                    (seller, LedgerEntry(-payoff, "pisos:hipoteca_cancelada")),
                    (buyer, LedgerEntry(-quote.notary, "pisos:notaria")),
                ),
                taxes=(
                    HousingTax(buyer, "itp", max(price, quote.reference), quote.itp),
                    HousingTax(buyer, "igic_gastos", quote.notary, quote.notary_igic),
                    HousingTax(seller, "plusvalia", max(0, quote.gain), quote.gain_tax),
                ),
            )
            return sale, move

        return run

    async def create_auction(
        self, guild_id: int, seller: int, key: str, shares: int, min_bid: int, now: float
    ) -> Auction:
        """Saca a subasta unas participaciones durante un día (`housing.AUCTION_SECONDS`).

        Raises:
            HousingError: Si no las tiene, ya están en subasta o la puja mínima no vale.
        """
        return await self._run(
            self._write, self._create_auction_in, guild_id, seller, key, shares, min_bid, now
        )

    @staticmethod
    def _create_auction_in(
        connection: sqlite3.Connection, guild_id: int, seller: int, key: str, shares: int,
        min_bid: int, now: float,
    ) -> Auction:  # fmt: skip
        market = _market_in(connection, guild_id)
        _listing(market, key)
        held = market.holdings.get(key, {}).get(seller)
        if held is None or not 1 <= shares <= held.shares:
            raise HousingError(
                f"Tienes {held.shares if held else 0} participaciones: no puedes subastar más."
            )
        _check_free(market, key, seller, local_day(now))
        _check_no_advance(market, key, seller)
        _check_reclaim(market, key, seller)
        if min_bid < 1:
            raise HousingError("La puja mínima tiene que ser de 1 Y$ o más.")
        number = trade.add_auction_in(
            connection, guild_id, listing=key, seller=seller, shares=shares, min_bid=min_bid,
            ends_at=now + rules.AUCTION_SECONDS, now=now,
        )  # fmt: skip
        _news_in(
            connection, guild_id, News(key, local_day(now), "subasta", min_bid, seller, None), now
        )
        return next(a for a in trade.auctions_in(connection, guild_id) if a.id == number)

    def bid(
        self, guild_id: int, user_id: int, auction_id: int, amount: int, now: float
    ) -> Callable[[sqlite3.Connection], tuple[Bid, HousingMove]]:
        """Puja en una subasta: deja en el depósito la puja con su ITP y su notaría.

        A quien supera se le devuelve todo lo suyo en la misma transacción. Si queda menos
        de una hora, la subasta se alarga una hora (`housing.auction_end`).

        Raises (dentro de la transacción):
            HousingError: Si la subasta ya no está abierta, la puja no llega o puja el dueño.
            InsufficientFundsError: Si no le llega para el depósito.
        """

        def run(connection: sqlite3.Connection) -> tuple[Bid, HousingMove]:
            market = _market_in(connection, guild_id)
            auction = next((a for a in market.auctions if a.id == auction_id), None)
            if auction is None or now >= auction.ends_at:
                raise HousingError("Esa subasta ya ha terminado.")
            if user_id == auction.seller and not auction.division:
                raise HousingError("No puedes pujar en tu propia subasta.")
            least = rules.next_bid(auction.best_bid, auction.min_bid)
            if amount < least:
                raise HousingError(f"La puja tiene que ser de {format_amount(least)} o más.")
            listing = _listing(market, auction.listing)
            held = market.holdings.get(auction.listing, {}).get(auction.seller)
            if held is None:
                raise HousingError("Esas participaciones ya no están en subasta.")
            if auction.division:
                quote = _division_quote(market, listing, auction, user_id, amount)
            else:
                quote = rules.member_sale_quote(
                    listing, auction.shares, held.shares, held.cost, amount,
                    index=market.index(listing.barrio),
                    itp_rate=rules.AUCTION_ITP_RATE if auction.judicial else rules.ITP_RATE,
                    market_value_gain=False,
                )  # fmt: skip
            escrow = quote.buyer_total
            ends_at = rules.auction_end(auction.ends_at, now)
            trade.save_bid_in(connection, guild_id, auction, user_id, amount, escrow, ends_at, now)
            entries = [
                *_refund(auction),
                (user_id, LedgerEntry(-escrow, "pisos:subasta_deposito")),
                (HOUSING_AUCTION_ACCOUNT_ID, LedgerEntry(escrow, "pisos:subasta_deposito")),
            ]
            updated = replace(
                auction, best_bid=amount, best_bidder=user_id, escrow=escrow,
                bids=auction.bids + 1, ends_at=ends_at,
            )  # fmt: skip
            outbid = auction.best_bidder if auction.best_bidder != user_id else None
            result = Bid(updated, escrow, outbid=outbid, extended=ends_at > auction.ends_at)
            return result, HousingMove(entries=tuple(entries))

        return run

    def close_auctions(
        self, guild_id: int, now: float
    ) -> Callable[[sqlite3.Connection], tuple[AuctionRun, HousingMove]]:
        """Cierra las subastas que han terminado.

        - Voluntaria con puja: las participaciones pasan a quien pujó más, que paga su ITP y
          su notaría (lo que dejó en el depósito); el vendedor cobra la puja menos el 19 % de
          lo que gane y la parte de su hipoteca. Sin pujas, no se vende nada.
        - Judicial: si la mejor puja vale (`housing.judicial_ok`), el inmueble es para quien
          pujó, con el ITP de las subastas (7 %), y queda bloqueado 30 días para venderlo al
          mercado; lo que paga cubre las deudas del piso y al banco. Si no, el banco se lo
          queda al 70 %, como siempre.
        """

        def run(connection: sqlite3.Connection) -> tuple[AuctionRun, HousingMove]:
            market = _market_in(connection, guild_id)
            day = local_day(now)
            result = AuctionRun()
            entries: list[tuple[int, LedgerEntry]] = []
            taxes: list[HousingTax] = []
            for auction in market.auctions:
                if auction.ends_at > now:
                    continue
                self._close_auction_in(
                    connection, guild_id, day, now, market, auction, entries, taxes, result
                )
                market = _market_in(connection, guild_id)
            return result, HousingMove(entries=tuple(entries), taxes=tuple(taxes))

        return run

    def _close_auction_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        day: date,
        now: float,
        market: Market,
        auction: Auction,
        entries: list[tuple[int, LedgerEntry]],
        taxes: list[HousingTax],
        result: AuctionRun,
    ) -> None:
        """Cierra una subasta (ver `close_auctions`)."""
        if auction.division:
            self._close_division_in(
                connection, guild_id, day, now, market, auction, entries, taxes, result
            )
            return
        listing = market.listing(auction.listing)
        held = market.holdings.get(auction.listing, {}).get(auction.seller)
        mortgage = next((m for m in market.mortgages if m.id == auction.mortgage_id), None)
        gone = listing is None or held is None or held.shares < auction.shares
        if gone or (auction.judicial and mortgage is None):
            trade.close_auction_in(connection, auction.id, trade.CANCELLED)
            entries.extend(_refund(auction))
            result.results.append(AuctionResult(auction, cancelled=True))
            return
        assert listing is not None and held is not None
        index = market.index(listing.barrio)
        winner = auction.best_bidder
        price = auction.best_bid
        quote = rules.member_sale_quote(
            listing, auction.shares, held.shares, held.cost, max(1, price), index=index,
            itp_rate=rules.AUCTION_ITP_RATE if auction.judicial else rules.ITP_RATE,
            market_value_gain=False,
        )  # fmt: skip
        if auction.judicial and mortgage is not None:
            lease = market.leases.get(auction.listing, rules.Lease())
            pool = sum(market.shares_of(auction.listing).values())
            # Lo que se debe: todas sus hipotecas de ese inmueble (`_execute_in` las cierra).
            mortgages = market.mortgages_of(auction.seller, auction.listing)
            owed = sum(m.owed for m in mortgages) + lease.arrears * held.shares // max(1, pool)
            habitual = market.homes.get(auction.seller) == (finance.HOME_OWN, auction.listing)
            valid = winner is not None and rules.judicial_ok(
                price, market.value(held), owed, habitual=habitual
            )
            if valid and winner is not None:
                entries.extend(_won(auction, quote))
                taxes.extend(_won_taxes(winner, quote, auction.escrow, price))
                self._execute_in(
                    connection, guild_id, day, now, market, mortgage, entries, taxes, result,
                    winner=winner, price=price, winner_cost=auction.escrow,
                )  # fmt: skip
                trade.close_auction_in(connection, auction.id, trade.SOLD)
                result.results.append(AuctionResult(auction, winner=winner, price=price))
                return
            entries.extend(_refund(auction))
            self._execute_in(
                connection, guild_id, day, now, market, mortgage, entries, taxes, result
            )
            trade.close_auction_in(connection, auction.id, trade.DESERTED)
            result.results.append(AuctionResult(auction, bank=True))
            return
        if winner is None:
            trade.close_auction_in(connection, auction.id, trade.DESERTED)
            item = News(auction.listing, day, "subasta_desierta", auction.min_bid, auction.seller,
                        None)  # fmt: skip
            result.news.append(item)
            _news_in(connection, guild_id, item, now)
            result.results.append(AuctionResult(auction))
            return
        payoff = _shrink_mortgages_in(
            connection, market, auction.seller, auction.listing, auction.shares, held.shares
        )
        _move_shares_in(
            connection, guild_id, market, auction.listing, auction.seller, winner, auction.shares,
            paid=price, cost=auction.escrow, day=day, now=now,
            lease=market.leases.get(auction.listing, rules.Lease()),
        )  # fmt: skip
        entries.extend(_won(auction, quote))
        entries.append((auction.seller, LedgerEntry(price, "pisos:subasta")))
        entries.append((auction.seller, LedgerEntry(-payoff, "pisos:hipoteca_cancelada")))
        taxes.extend(_won_taxes(winner, quote, auction.escrow, price))
        taxes.append(HousingTax(auction.seller, "plusvalia", max(0, quote.gain), quote.gain_tax))
        trade.close_auction_in(connection, auction.id, trade.SOLD)
        item = News(auction.listing, day, "subasta_adjudicada", price, winner, None)
        result.news.append(item)
        _news_in(connection, guild_id, item, now)
        result.results.append(AuctionResult(auction, winner=winner, price=price))

    # -- Ampliación, fase 6: junta de copropietarios y acción de división ------------

    async def propose(
        self, guild_id: int, user_id: int, key: str, kind: str, value: str, amount: int,
        now: float,
    ) -> Vote:  # fmt: skip
        """Propone algo a la junta de un inmueble con varios dueños (vota sí al proponerlo).

        Raises:
            HousingError: Si no es dueño, no hay junta, ya se vota eso o la propuesta no vale.
        """
        return await self._run(
            self._write, self._propose_in, guild_id, user_id, key, kind, value, amount, now
        )

    @staticmethod
    def _propose_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, key: str, kind: str,
        value: str, amount: int, now: float,
    ) -> Vote:  # fmt: skip
        market = _market_in(connection, guild_id)
        listing = _listing(market, key)
        holders = market.shares_of(key)
        if user_id not in holders:
            raise HousingError("Solo proponen los dueños.")
        if len(holders) < 2:
            raise HousingError(
                "La junta es para los inmuebles con varios dueños: esto lo decides tú."
            )
        if kind not in rules.MOTION_BY_KEY:
            raise HousingError("Eso no se vota en la junta.")
        if any(v.kind == kind for v in market.votes_of(key)):
            raise HousingError("Ya hay una votación de eso abierta: vota en ella.")
        if market.division_of(key) is not None:
            raise HousingError("Hay una acción de división en marcha: se vende entero.")
        _check_motion(market, listing, key, kind, value, amount)
        number = junta.add_vote_in(
            connection, guild_id, listing=key, proposer=user_id, kind=kind, value=value,
            amount=amount, ends_at=now + rules.VOTE_SECONDS, now=now,
        )  # fmt: skip
        _news_in(connection, guild_id, News(key, local_day(now), "junta", amount, user_id, None),
                 now)  # fmt: skip
        return next(v for v in junta.votes_in(connection, guild_id) if v.id == number)

    def vote(
        self, guild_id: int, user_id: int, vote_id: int, yes: bool, now: float,
        rng: random.Random | None = None,
    ) -> Callable[[sqlite3.Connection], tuple[VoteResult, HousingMove]]:  # fmt: skip
        """Vota en la junta. Si ya han votado todos los dueños, se cierra en el momento.

        Raises (dentro de la transacción):
            HousingError: Si la votación ya se cerró o no es dueño.
        """
        dice = rng or random.Random()

        def run(connection: sqlite3.Connection) -> tuple[VoteResult, HousingMove]:
            market = _market_in(connection, guild_id)
            vote = next((v for v in market.votes if v.id == vote_id), None)
            if vote is None or vote.ends_at <= now:
                raise HousingError("Esa votación ya se ha cerrado.")
            holders = market.shares_of(vote.listing)
            if user_id not in holders:
                raise HousingError("Solo votan los dueños.")
            junta.ballot_in(connection, guild_id, vote_id, user_id, yes, now)
            vote = replace(vote, ballots={**vote.ballots, user_id: yes})
            if not set(holders) <= set(vote.ballots):
                return VoteResult(vote, owners=tuple(holders)), HousingMove()
            parts = _Parts()
            result = self._resolve_in(connection, guild_id, market, vote, now, dice, parts)
            return result, parts.move(guild_id)

        return run

    def close_votes(
        self, guild_id: int, now: float, rng: random.Random | None = None
    ) -> Callable[[sqlite3.Connection], tuple[list[VoteResult], HousingMove]]:
        """Cierra las votaciones de la junta que han llegado a su hora."""
        dice = rng or random.Random()

        def run(connection: sqlite3.Connection) -> tuple[list[VoteResult], HousingMove]:
            parts = _Parts()
            results = []
            for vote in _market_in(connection, guild_id).votes:
                if vote.ends_at > now:
                    continue
                market = _market_in(connection, guild_id)
                results.append(
                    self._resolve_in(connection, guild_id, market, vote, now, dice, parts)
                )
            return results, parts.move(guild_id)

        return run

    def _resolve_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        market: Market,
        vote: Vote,
        now: float,
        dice: random.Random,
        parts: _Parts,
    ) -> VoteResult:
        """Cuenta los votos y, si sale que sí, lo hace (quien no vota cuenta a favor)."""
        holders = market.shares_of(vote.listing)
        motion = rules.MOTION_BY_KEY[vote.kind]
        day = local_day(now)
        against = sum(s for m, s in holders.items() if vote.ballots.get(m) is False)
        owners = tuple(holders)
        if not holders or not rules.passes(motion.threshold, sum(holders.values()), against):
            junta.close_vote_in(connection, vote.id, junta.REJECTED)
            _news_in(connection, guild_id, News(vote.listing, day, "junta_rechaza", 0,
                                                vote.proposer, None), now)  # fmt: skip
            return VoteResult(replace(vote, status=junta.REJECTED), owners=owners)
        mine = _Parts()
        connection.execute("SAVEPOINT junta")
        try:
            note = self._apply_in(connection, guild_id, market, vote, holders, day, now, dice,
                                  mine)  # fmt: skip
        except HousingError as error:
            connection.execute("ROLLBACK TO junta")
            mine = _Parts()
            note = str(error)
        connection.execute("RELEASE junta")
        parts.extend(mine)
        status = junta.FAILED if note else junta.APPROVED
        junta.close_vote_in(connection, vote.id, status, note)
        kind = "junta_falla" if note else "junta_aprueba"
        _news_in(connection, guild_id, News(vote.listing, day, kind, vote.amount, vote.proposer,
                                            None), now)  # fmt: skip
        return VoteResult(replace(vote, status=status, note=note), owners=owners)

    def _apply_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        market: Market,
        vote: Vote,
        holders: dict[int, int],
        day: date,
        now: float,
        dice: random.Random,
        parts: _Parts,
    ) -> str:
        """Lo que hace una propuesta aprobada. Devuelve por qué no se pudo ("" si se pudo).

        Raises:
            HousingError: Si no se puede (se deshace todo lo de esta propuesta).
        """
        key = vote.listing
        listing = _listing(market, key)
        lease = market.leases.get(key, rules.Lease())
        _check_motion(market, listing, key, vote.kind, vote.value, vote.amount)
        if vote.kind == "renta":
            self._set_level_in(connection, guild_id, vote.proposer, key, vote.value, junta=True)
        elif vote.kind == "seguro":
            _save_lease_in(connection, guild_id, key, replace(lease, insured=vote.value == "1"))
        elif vote.kind == "eleccion":
            _save_lease_in(connection, guild_id, key, replace(lease, pick=vote.value))
            if vote.value == rules.PICK_AGENCY:
                connection.execute(
                    "DELETE FROM housing_candidates WHERE guild_id = ? AND listing = ?",
                    (guild_id, key),
                )
        elif vote.kind == "administra":
            junta.set_admin_in(connection, guild_id, key, int(vote.value))
        elif vote.kind == "derrama":
            # Cada dueño paga su parte; lo que no tenga queda como deuda del piso con la
            # comunidad (art. 9.1.e LPH). El fondo la apunta entera.
            for member, part in rules.split(vote.amount, holders).items():
                if part:
                    parts.debits.append(HousingDebit(member, key, 0, part, "pisos:derrama_fondo"))
            junta.add_reserve_in(connection, guild_id, key, vote.amount)
        elif vote.kind == "reforma":
            _done, move = self._reform_in(
                connection, guild_id, market, key, vote.value, holders, vote.proposer, day, now
            )
            _check_wallets_in(connection, guild_id, move)
            parts.add(move)
        elif vote.kind == "licencia":
            fee, move = self._license_in(
                connection, guild_id, market, key, holders, vote.proposer, day, now, dice
            )
            if not fee:
                return "La comunidad de vecinos ha votado que no a la vivienda vacacional."
            _check_wallets_in(connection, guild_id, move)
            parts.add(move)
        elif vote.kind in rules.SPLIT_MODES:
            _done, move = self._split_in(
                connection, guild_id, market, key, vote.kind, holders, vote.proposer, day, now
            )
            _check_wallets_in(connection, guild_id, move)
            parts.add(move)
        elif vote.kind == "venta":
            for member in holders:
                _check_free(market, key, member, day, market_sale=True)
            for move in self._sell_all_in(connection, guild_id, key, day, now):
                parts.add(move)
        return ""

    def _sell_all_in(
        self, connection: sqlite3.Connection, guild_id: int, key: str, day: date, now: float
    ) -> list[HousingMove]:
        """Cada dueño vende lo suyo al mercado (quien no puede venderlo aún, se lo queda)."""
        market = _market_in(connection, guild_id)
        moves = []
        for member, shares in sorted(market.shares_of(key).items()):
            held = market.holdings[key][member]
            if held.locked_until and held.locked_until > day:
                continue  # adjudicado en una subasta judicial: aún no puede venderlo
            _sale, move = self.sell(guild_id, member, key, shares, day, now, forced=True)(
                connection
            )
            moves.append(move)
        return moves

    async def divide(self, guild_id: int, user_id: int, key: str, now: float) -> Auction:
        """Acción de división (arts. 400 y 404 CC): saca el inmueble entero a subasta un día.

        La puja mínima es el valor de mercado de todo. Pueden pujar todos, también los dueños
        (pagan solo la parte de los demás). Si nadie puja, cada dueño vende lo suyo al mercado.

        Raises:
            HousingError: Si no es dueño, no hay varios dueños o ya hay una subasta.
        """
        return await self._run(self._write, self._divide_in, guild_id, user_id, key, now)

    @staticmethod
    def _divide_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, key: str, now: float
    ) -> Auction:
        market = _market_in(connection, guild_id)
        _listing(market, key)
        holders = market.shares_of(key)
        if user_id not in holders:
            raise HousingError("La acción de división la pide un dueño.")
        if len(holders) < 2:
            raise HousingError("Para dividir hacen falta varios dueños: ya es todo tuyo.")
        day = local_day(now)
        for member in holders:
            _check_free(market, key, member, day)
        minimum = rules.division_minimum([market.value(h) for h in market.holdings[key].values()])
        number = trade.add_auction_in(
            connection, guild_id, listing=key, seller=user_id, shares=sum(holders.values()),
            min_bid=minimum, ends_at=now + rules.AUCTION_SECONDS, now=now, division=True,
        )  # fmt: skip
        for vote in market.votes_of(key):
            junta.close_vote_in(connection, vote.id, junta.FAILED, "Acción de división.")
        _news_in(connection, guild_id, News(key, day, "division", minimum, user_id, None), now)
        return next(a for a in trade.auctions_in(connection, guild_id) if a.id == number)

    def _close_division_in(
        self,
        connection: sqlite3.Connection,
        guild_id: int,
        day: date,
        now: float,
        market: Market,
        auction: Auction,
        entries: list[tuple[int, LedgerEntry]],
        taxes: list[HousingTax],
        result: AuctionRun,
    ) -> None:
        """Cierra la subasta de una acción de división (arts. 400 y 404 CC).

        Con puja, quien gana se queda con lo de los demás dueños y paga a cada uno su parte
        de la puja (con su ITP y su notaría; cada vendedor, el 19 % de lo que gane). Sin
        pujas, cada dueño vende lo suyo al mercado.
        """
        key = auction.listing
        listing = market.listing(key)
        holders = market.shares_of(key)
        if listing is None or sum(holders.values()) != auction.shares:
            trade.close_auction_in(connection, auction.id, trade.CANCELLED)
            entries.extend(_refund(auction))
            result.results.append(AuctionResult(auction, cancelled=True))
            return
        winner = auction.best_bidder
        trade.close_auction_in(connection, auction.id, trade.SOLD if winner else trade.DESERTED)
        if winner is None:
            for move in self._sell_all_in(connection, guild_id, key, day, now):
                entries.extend(move.entries)
                taxes.extend(move.taxes)
            item = News(key, day, "division_mercado", auction.min_bid, auction.seller, None)
            result.news.append(item)
            _news_in(connection, guild_id, item, now)
            result.results.append(AuctionResult(auction))
            return
        quote = _division_quote(market, listing, auction, winner, auction.best_bid)
        parts = _division_parts(holders, winner, auction.best_bid, auction.shares)
        paid = sum(parts.values())
        entries.extend(
            [
                (
                    HOUSING_AUCTION_ACCOUNT_ID,
                    LedgerEntry(-auction.escrow, "pisos:subasta_deposito"),
                ),
                (winner, LedgerEntry(auction.escrow, "pisos:subasta_deposito")),
                (winner, LedgerEntry(-paid, "pisos:subasta_compra")),
                (winner, LedgerEntry(-quote.notary, "pisos:notaria")),
            ]
        )
        taxes.extend(_won_taxes(winner, quote, auction.escrow, paid))
        index = market.index(listing.barrio)
        for member, part in sorted(parts.items()):
            market = _market_in(connection, guild_id)
            held = market.holdings[key][member]
            sold = rules.member_sale_quote(
                listing, held.shares, held.shares, held.cost, max(1, part), index=index,
                market_value_gain=False,
            )  # fmt: skip
            payoff = _shrink_mortgages_in(connection, market, member, key, held.shares, held.shares)
            _move_shares_in(
                connection, guild_id, market, key, member, winner, held.shares, paid=part,
                cost=auction.escrow * part // max(1, paid), day=day, now=now,
                lease=market.leases.get(key, rules.Lease()),
            )  # fmt: skip
            entries.append((member, LedgerEntry(part, "pisos:subasta")))
            entries.append((member, LedgerEntry(-payoff, "pisos:hipoteca_cancelada")))
            taxes.append(HousingTax(member, "plusvalia", max(0, sold.gain), sold.gain_tax))
        item = News(key, day, "division_adjudicada", auction.best_bid, winner, None)
        result.news.append(item)
        _news_in(connection, guild_id, item, now)
        result.results.append(AuctionResult(auction, winner=winner, price=auction.best_bid))

    def evict(
        self, guild_id: int, user_id: int, key: str, day: date, now: float, rng: random.Random
    ) -> Callable[[sqlite3.Connection], tuple[Eviction, HousingMove]]:
        """Pide el desahucio de quien no paga (también de un miembro) o de los okupas.

        Lo pide quien tiene la mayoría; si nadie la tiene, cualquier copropietario
        (art. 394 del Código Civil: cada uno puede defender la cosa común). Abogado y
        procurador son obligatorios (art. 23 LEC): los paga quien lo pide, con su IGIC, salvo
        que el seguro de impago cubra a ese inquilino (contra okupas no cubre). Es un gasto
        del inmueble deducible en el IRPF (art. 13.c del Reglamento).

        Raises (dentro de la transacción):
            HousingError: Si no le toca o no hay a quién desahuciar.
            InsufficientFundsError: Si no le llega para el abogado. No se pide nada.
        """

        def run(connection: sqlite3.Connection) -> tuple[Eviction, HousingMove]:
            market = _market_in(connection, guild_id)
            listing = _listing(market, key)
            holders = market.shares_of(key)
            boss = market.manager(key)
            if user_id not in holders or (boss is not None and boss != user_id):
                raise HousingError("El desahucio lo pide quien tiene la mayoría del inmueble.")
            lease = market.leases[key]
            squatters = lease.status == rules.SQUATTED
            insured = not squatters and rules.covered(listing, lease)
            rent = lease.rent or rules.reference_rent(listing, market.index(listing.barrio))
            try:
                lease = rules.start_eviction(lease, day, rng)
            except ValueError as error:
                raise HousingError(str(error)) from error
            fee = 0 if insured else rules.eviction_fee(rent)
            fee_igic = igic(fee, rules.SERVICES_IGIC)
            _save_lease_in(connection, guild_id, key, lease)
            _news_in(connection, guild_id, News(key, day, "juicio", fee, lease.member or user_id,
                                                lease.tenant), now)  # fmt: skip
            eviction = Eviction(lease, fee, fee_igic, insured=insured, squatters=squatters)
            if not fee:
                return eviction, HousingMove()
            move = HousingMove(
                entries=((user_id, LedgerEntry(-fee, "pisos:abogado")),),
                taxes=(HousingTax(user_id, "igic_gastos", fee, fee_igic),),
                expenses=((user_id, key, fee + fee_igic),),
            )
            return eviction, move

        return run

    # -- Elegir inquilino y seguro de impago (no mueven dinero) ----------------------

    async def set_pick(self, guild_id: int, user_id: int, key: str, pick: str) -> rules.Lease:
        """Cambia quién elige al próximo inquilino: la gestora o el dueño.

        Raises:
            HousingError: Si no tiene la mayoría o el modo no existe.
        """
        return await self._run(self._write, self._set_pick_in, guild_id, user_id, key, pick)

    @staticmethod
    def _set_pick_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, key: str, pick: str
    ) -> rules.Lease:
        if pick not in (rules.PICK_AGENCY, rules.PICK_SELF):
            raise HousingError("Ese modo no existe.")
        market = _market_in(connection, guild_id)
        _listing(market, key)
        if market.manager(key) != user_id:
            raise HousingError("Al inquilino lo elige quien tiene más de la mitad del inmueble.")
        lease = replace(market.leases[key], pick=pick)
        _save_lease_in(connection, guild_id, key, lease)
        if pick == rules.PICK_AGENCY:
            connection.execute(
                "DELETE FROM housing_candidates WHERE guild_id = ? AND listing = ?",
                (guild_id, key),
            )
        return lease

    async def set_insurance(self, guild_id: int, user_id: int, key: str, on: bool) -> rules.Lease:
        """Contrata o da de baja el seguro de impago de un inmueble.

        La prima se paga cada noche con los recibos, mientras haya un contrato que cubra.

        Raises:
            HousingError: Si no tiene la mayoría o no es una vivienda.
        """
        return await self._run(self._write, self._set_insurance_in, guild_id, user_id, key, on)

    @staticmethod
    def _set_insurance_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, key: str, on: bool
    ) -> rules.Lease:
        market = _market_in(connection, guild_id)
        listing = _listing(market, key)
        if market.manager(key) != user_id:
            raise HousingError("El seguro lo contrata quien tiene más de la mitad del inmueble.")
        if listing.kind not in rules.HOME_KINDS:
            raise HousingError("El seguro de impago es para viviendas, no para negocios.")
        lease = replace(market.leases[key], insured=on)
        _save_lease_in(connection, guild_id, key, lease)
        return lease

    async def sign_candidate(
        self, guild_id: int, user_id: int, key: str, tenant: str, day: date, now: float
    ) -> Signing:
        """Firma con uno de los candidatos que esperan.

        Raises:
            HousingError: Si no tiene la mayoría, el piso no está vacío o el candidato ya
                se ha ido.
        """
        return await self._run(
            self._write, self._sign_candidate_in, guild_id, user_id, key, tenant, day, now
        )

    @staticmethod
    def _sign_candidate_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, key: str, tenant: str,
        day: date, now: float,
    ) -> Signing:  # fmt: skip
        market = _market_in(connection, guild_id)
        listing = _listing(market, key)
        boss = market.manager(key)
        if boss != user_id:
            raise HousingError("Al inquilino lo elige quien tiene más de la mitad del inmueble.")
        lease = market.leases[key]
        if lease.status != rules.VACANT:
            raise HousingError("El piso ya no está vacío.")
        if tenant not in market.candidates_of(key, day):
            raise HousingError("Ese candidato ya no espera: ha encontrado otro piso.")
        index = market.index(listing.barrio)
        if not rules.level_allowed(
            lease.level, index=index, whole_homes=market.whole_homes(boss), license=lease.license
        ):
            lease = replace(lease, level="referencia")
        first = lease.last_rent == 0
        signed = rules.sign(listing, lease, tenant, day, index)
        if _bonus_ready(market, key, listing, lease, day):
            signed = replace(signed, bonus=True)
        _save_lease_in(connection, guild_id, key, signed)
        connection.execute(
            "DELETE FROM housing_candidates WHERE guild_id = ? AND listing = ?", (guild_id, key)
        )
        _news_in(connection, guild_id, News(key, day, "firma", signed.rent, None, tenant), now)
        risk = rules.risk_tier(rules.tenant_factors(tenant, signed.level, listing.kind))
        return Signing(signed, tenant, risk, first)

    async def discard_candidate(self, guild_id: int, user_id: int, key: str, tenant: str) -> None:
        """Descarta a un candidato (se va a buscar otro piso).

        Raises:
            HousingError: Si no tiene la mayoría.
        """
        await self._run(self._write, self._discard_in, guild_id, user_id, key, tenant)

    @staticmethod
    def _discard_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, key: str, tenant: str
    ) -> None:
        market = _market_in(connection, guild_id)
        _listing(market, key)
        if market.manager(key) != user_id:
            raise HousingError("Al inquilino lo elige quien tiene más de la mitad del inmueble.")
        connection.execute(
            "DELETE FROM housing_candidates WHERE guild_id = ? AND listing = ? AND tenant = ?",
            (guild_id, key, tenant),
        )

    # -- 9.23.0: duración del contrato y recuperar el piso (`docs/pisos-llaves.md`) ---------

    async def set_term(self, guild_id: int, user_id: int, key: str, term: int) -> rules.Lease:
        """Cambia lo que dura el próximo contrato, como la renta (no mueve dinero).

        Raises:
            HousingError: Si no lo decide él o con esa renta la duración es fija.
        """
        return await self._run(self._write, self._set_term_in, guild_id, user_id, key, term)

    @staticmethod
    def _set_term_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, key: str, term: int
    ) -> rules.Lease:
        market = _market_in(connection, guild_id)
        listing = _listing(market, key)
        if market.manager(key) != user_id:
            raise HousingError("La duración la decide quien tiene más de la mitad del inmueble.")
        lease = market.leases[key]
        level = RENT_LEVELS.get(lease.level, RENT_LEVELS[DEFAULT_RENT_LEVEL])
        if term not in rules.term_choices(listing, level):
            raise HousingError("Con esa renta el contrato dura lo que dura: no se elige.")
        lease = replace(lease, term=term)
        _save_lease_in(connection, guild_id, key, lease)
        return lease

    async def set_no_renew(
        self, guild_id: int, user_id: int, key: str, on: bool, day: date, now: float
    ) -> rules.Lease:
        """Avisa de que no renovará el contrato (o se lo piensa mejor). No mueve dinero.

        El inquilino se va el día que acaba (art. 10.1 LAU); en zona tensionada puede pedir
        antes la prórroga de la Ley de Vivienda (`housing.contract_end`).

        Raises:
            HousingError: Si no lo decide él, no hay contrato o ya se va por otra razón.
        """
        return await self._run(
            self._write, self._set_no_renew_in, guild_id, user_id, key, on, day, now
        )

    @staticmethod
    def _set_no_renew_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, key: str, on: bool,
        day: date, now: float,
    ) -> rules.Lease:  # fmt: skip
        market = _market_in(connection, guild_id)
        lease = _keys_ready(market, key, user_id)
        if on:
            if lease.leaving:
                raise HousingError("Ya se va: no hace falta avisarle de nada.")
            lease = replace(lease, leaving=rules.LEAVE_NO_RENEW)
            _news_in(connection, guild_id, News(key, day, "no_renueva", 0, user_id,
                                                lease.tenant), now)  # fmt: skip
        else:
            if lease.leaving != rules.LEAVE_NO_RENEW:
                raise HousingError("Eso ya no tiene vuelta atrás.")
            lease = replace(lease, leaving="")
        _save_lease_in(connection, guild_id, key, lease)
        return lease

    def offer_keys(
        self, guild_id: int, user_id: int, key: str, offer: str, day: date, now: float,
        rng: random.Random,
    ) -> Callable[[sqlite3.Connection], tuple[KeysDeal, HousingMove]]:  # fmt: skip
        """Le ofrece al inquilino del catálogo dinero (o nada) por irse.

        Si acepta, se va en `housing.KEYS_MOVE_DAYS` días y lo pagado desaparece (el
        inquilino no existe). Sin IGIC: quien cobra es un particular, no un empresario (art. 4
        de la Ley 20/1991). Para el casero es una mejora y no un gasto deducible (DGT
        V3259-23): suma a lo pagado y al coste de su parte, como una reforma. Si no acepta, se
        enfada y contraoferta (`housing.keys_refused`), o se atrinchera.

        Raises (dentro de la transacción):
            HousingError: Si no le toca, no hay inquilino del catálogo o no quiere ni hablar.
            InsufficientFundsError: Si no le llega para lo que ofrece. No se pregunta nada.
        """

        def run(connection: sqlite3.Connection) -> tuple[KeysDeal, HousingMove]:
            market = _market_in(connection, guild_id)
            lease = _keys_ready(market, key, user_id)
            if lease.member is not None:
                raise HousingError("Vive un miembro: la oferta se le hace a él.")
            if offer not in rules.KEYS_OFFERS:
                raise HousingError("Esa oferta no existe.")
            _check_keys_wait(lease, day)
            quote = market.keys_quote(key, day)
            if quote is None:
                raise HousingError("No hay a quién pedirle que se vaya.")
            answer = rules.keys_answer(offer, quote.fair, quote.reserve, quote.free, quote.risk,
                                       rng)  # fmt: skip
            if answer.accepted:
                return self._keys_paid_in(
                    connection, guild_id, market, key, user_id, lease, answer, quote, day, now
                )
            lease = rules.keys_refused(lease, day, answer)
            _save_lease_in(connection, guild_id, key, lease)
            kind = "atrinchera" if answer.trench else "rechaza_llaves"
            _news_in(connection, guild_id, News(key, day, kind, answer.ask, user_id,
                                                lease.tenant), now)  # fmt: skip
            return KeysDeal(lease, answer, quote, lease.tenant), HousingMove()

        return run

    def accept_keys_ask(
        self, guild_id: int, user_id: int, key: str, day: date, now: float
    ) -> Callable[[sqlite3.Connection], tuple[KeysDeal, HousingMove]]:
        """Le paga al inquilino del catálogo lo que pidió al rechazar una oferta.

        Mismo tratamiento que `offer_keys`.

        Raises (dentro de la transacción):
            HousingError: Si no hay contraoferta o ya caducó.
            InsufficientFundsError: Si no le llega.
        """

        def run(connection: sqlite3.Connection) -> tuple[KeysDeal, HousingMove]:
            market = _market_in(connection, guild_id)
            lease = _keys_ready(market, key, user_id)
            if lease.member is not None or not lease.keys_ask:
                raise HousingError("No ha pedido nada: hazle una oferta.")
            if lease.keys_wait is None or day >= lease.keys_wait:
                raise HousingError("Su precio ya no vale: ha pasado una semana.")
            answer = rules.KeysAnswer(True, paid=lease.keys_ask)
            return self._keys_paid_in(
                connection, guild_id, market, key, user_id, lease, answer, None, day, now
            )

        return run

    @staticmethod
    def _keys_paid_in(
        connection: sqlite3.Connection, guild_id: int, market: Market, key: str, user_id: int,
        lease: rules.Lease, answer: rules.KeysAnswer, quote: rules.KeysQuote | None,
        day: date, now: float,
    ) -> tuple[KeysDeal, HousingMove]:  # fmt: skip
        tenant = lease.tenant
        lease = rules.leaves_in(lease, day, rules.LEAVE_KEYS, rules.KEYS_MOVE_DAYS)
        _save_lease_in(connection, guild_id, key, lease)
        kind = "llaves" if answer.paid else "llaves_gratis"
        _news_in(connection, guild_id, News(key, day, kind, answer.paid, user_id, tenant), now)
        if not answer.paid:
            return KeysDeal(lease, answer, quote, tenant), HousingMove()
        _improve_in(connection, guild_id, market.owner_key(key), user_id, answer.paid)
        move = HousingMove(entries=((user_id, LedgerEntry(-answer.paid, "pisos:llaves")),))
        return KeysDeal(lease, answer, quote, tenant), move

    async def drop_keys_ask(self, guild_id: int, user_id: int, key: str) -> rules.Lease:
        """No acepta lo que pidió el inquilino: su precio se olvida (no mueve dinero).

        Raises:
            HousingError: Si no le toca o no hay contraoferta.
        """
        return await self._run(self._write, self._drop_keys_ask_in, guild_id, user_id, key)

    @staticmethod
    def _drop_keys_ask_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, key: str
    ) -> rules.Lease:
        market = _market_in(connection, guild_id)
        lease = _keys_ready(market, key, user_id)
        if not lease.keys_ask:
            raise HousingError("No ha pedido nada.")
        lease = replace(lease, keys_ask=0)
        _save_lease_in(connection, guild_id, key, lease)
        return lease

    def member_keys(
        self, guild_id: int, owner_id: int, key: str, member_id: int, amount: int, day: date,
        now: float,
    ) -> Callable[[sqlite3.Connection], tuple[MemberKeys, HousingMove]]:  # fmt: skip
        """Le compra las llaves a un miembro de alquiler, que ha aceptado `amount`.

        Se va en `housing.KEYS_MOVE_DAYS` días y la fianza vuelve sola cuando se vaya. Para
        el miembro es una ganancia patrimonial de la base del ahorro (arts. 33.1 y 49 LIRPF,
        DGT V3259-23): sin retención en la ley; aquí se le descuenta el 19 % al cobrarla,
        como la plusvalía del Rastro. Para el casero es una mejora del inmueble, como con
        un inquilino del catálogo (`offer_keys`).

        Raises (dentro de la transacción):
            HousingError: Si ya no vive ahí, el casero ya no decide o el importe no vale.
            InsufficientFundsError: Si al casero no le llega. No se firma nada.
        """

        def run(connection: sqlite3.Connection) -> tuple[MemberKeys, HousingMove]:
            market = _market_in(connection, guild_id)
            lease = _keys_ready(market, key, owner_id)
            if lease.member != member_id:
                raise HousingError("Ya no vive ahí de alquiler.")
            if not 0 <= amount <= rules.KEYS_MAX:
                raise HousingError(f"Como mucho, {format_amount(rules.KEYS_MAX)}.")
            lease = rules.leaves_in(lease, day, rules.LEAVE_KEYS, rules.KEYS_MOVE_DAYS)
            _save_lease_in(connection, guild_id, key, lease)
            kind = "llaves" if amount else "llaves_gratis"
            _news_in(connection, guild_id, News(key, day, kind, amount, owner_id, None), now)
            if not amount:
                return MemberKeys(lease, 0, 0), HousingMove()
            _improve_in(connection, guild_id, market.owner_key(key), owner_id, amount)
            tax = capital_gain_tax(amount)
            return MemberKeys(lease, amount, tax), _indemnity_move(
                owner_id, member_id, amount, "pisos:llaves"
            )

        return run

    async def claim_need(
        self, guild_id: int, user_id: int, key: str, day: date, now: float
    ) -> tuple[rules.Lease, int]:
        """Pide el piso para vivir en él (art. 9.3 LAU): el inquilino se va en
        `housing.NEED_NOTICE_DAYS` días y esa noche se muda su dueño. No mueve dinero.

        Returns:
            `(el contrato, la indemnización si no vive ahí lo que debe)`.

        Raises:
            HousingError: Si no es todo suyo, ya vive en otro sitio, ya pidió otro o el
                contrato no lo permite (`housing.need_problem`).
        """
        return await self._run(self._write, self._claim_need_in, guild_id, user_id, key, day, now)

    @staticmethod
    def _claim_need_in(
        connection: sqlite3.Connection, guild_id: int, user_id: int, key: str, day: date,
        now: float,
    ) -> tuple[rules.Lease, int]:  # fmt: skip
        market = _market_in(connection, guild_id)
        listing = _listing(market, key)
        if market.shares_of(key).get(user_id, 0) != rules.SHARES:
            raise HousingError("Solo puedes pedirlo para vivir si es entero tuyo.")
        lease = market.leases[key]
        problem = rules.need_problem(listing, lease, day)
        if problem is not None:
            raise HousingError(problem)
        if user_id in market.homes:
            raise HousingError("Ya vives en otro sitio: la necesidad tiene que ser de verdad.")
        if market.reclaim_of(user_id) is not None:
            raise HousingError("Ya has pedido otro piso para vivir.")
        fine = rules.need_fine(lease.rent, rules.days_left(lease, day))
        tenant = lease.tenant
        lease = rules.leaves_in(lease, day, rules.LEAVE_NEED, rules.NEED_NOTICE_DAYS)
        _save_lease_in(connection, guild_id, key, lease)
        connection.execute(
            "INSERT INTO housing_reclaims (guild_id, listing, user_id, member, fine, until, "
            "created_at) VALUES (?, ?, ?, ?, ?, NULL, ?)",
            (guild_id, key, user_id, lease.member, fine, now),
        )
        _news_in(connection, guild_id, News(key, day, "necesidad", fine, user_id, tenant), now)
        return lease, fine

    async def delete_guild_data(self, guild_id: int) -> None:
        """Borra todo lo de un servidor (al salir el bot)."""
        await self._run(self._delete_guild_data_sync, guild_id)

    def _delete_guild_data_sync(self, guild_id: int) -> None:
        connection = self._connect()
        try:
            for table in (
                "housing_holdings",
                "housing_leases",
                "housing_index",
                "housing_days",
                "housing_news",
                "housing_income",
                "housing_candidates",
                "housing_barrio_news",
                "housing_rent_bonus",
                "housing_splits",
                "housing_reforms",
                "housing_decisions",
                "housing_reviews",
                "housing_reclaims",
                *finance.TABLES,
                *trade.TABLES,
                *junta.TABLES,
            ):
                # `table` sale de una tupla fija, nunca de entrada del usuario.
                connection.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
        finally:
            connection.close()


__all__ = [
    "DayRun",
    "Holding",
    "HousingError",
    "HousingRepository",
    "Market",
    "MemberDay",
    "News",
    "PriceChanged",
    "Purchase",
    "Sale",
    "VoteResult",
    "VpoDeal",
    "assets_in",
]
