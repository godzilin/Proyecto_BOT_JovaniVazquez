"""Pruebas de la fase 2 de los negocios («crecer el local», `docs/negocios.md`).

Mejoras que se amortizan, campañas, precio por producto, reseñas, traslado a otro local o
a uno propio de `pisos`, y cerrar traspasando o liquidando. Con la base de datos de verdad,
como `test_negocio_service.py`, del que se toman las piezas.
"""

from __future__ import annotations

import dataclasses
import math
import random
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from test_negocio_service import (
    DAY,
    GUILD,
    USER,
    Clock,
    activity_records,
    business_taxes,
    give,
    make,
    open_cafe,
    play,
    sin_azar_de_la_pala,  # noqa: F401  (fixture autouse: sin sorpresas en la `pala`)
    wallet,
)

from bot.repositories.economy import HOUSING_DEPOSIT_ACCOUNT_ID, STATE_ACCOUNT_ID
from bot.repositories.housing import HousingError, HousingRepository
from bot.services.achievements import business_stats
from bot.services.business import (
    LIQUIDATION_EQUIPMENT,
    LIQUIDATION_STOCK,
    RECOMMENDED_REVIEWS,
    Business,
    campaign_boost,
    closing_offer,
    nightly_bill,
    price_clients,
    recommended,
    rent,
    review_of,
    size_for,
    spoil,
    turn_clients,
)
from bot.services.business_catalog import EQUIPMENT, SECTOR_BY_KEY, SECTORS, SIZES
from bot.services.business_games import Client, Outcome
from bot.services.housing_market import HousingService
from bot.services.negocio import BusinessError
from bot.services.taxes import capital_gain_tax, igic


class Seq(random.Random):
    """Un azar que devuelve, por orden, los números que se le dan."""

    def __init__(self, *values: float) -> None:
        super().__init__(0)
        self.values = list(values)

    def random(self) -> float:
        return self.values.pop(0)


def sql(tmp_path: Path, query: str, *args: object) -> list[tuple]:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        return connection.execute(query, args).fetchall()


# -- Reglas ------------------------------------------------------------------------------


def test_cada_mejora_es_de_algun_sector_y_cada_sector_tiene_mejoras() -> None:
    keys = {s.key for s in SECTORS}
    for item in EQUIPMENT:
        assert set(item.sectors) <= keys and item.effect()
    for sector in SECTORS:
        assert any(sector.key in e.sectors for e in EQUIPMENT), sector.key


def test_el_local_la_mejora_la_campana_y_el_recomendado_suben_los_clientes() -> None:
    today = date(2026, 10, 20)
    business = Business(user_id=1, sector="cafeteria", barrio="triana", name="Bar")
    sector, barrio = business.sector_info, business.barrio_info
    raw = sector.clients * sector.fit.get(barrio.theme, 1.0) * sector.curve[9]
    assert turn_clients(business, 9, 0.0, today) == math.floor(raw)
    business.size = 2
    business.equipment = ["cafetera2", "terraza"]
    business.campaign, business.campaign_until = "radio", today.isoformat()
    business.reviews = [[today.isoformat(), "Ana G.", 5, 0]] * RECOMMENDED_REVIEWS
    boosted = raw * SIZES[2].clients * (1 + 0.15 + 0.2) * (1 + 0.3) * 1.1
    assert turn_clients(business, 9, 0.0, today) == math.floor(boosted)
    # Mañana la campaña ya no está.
    tomorrow = date(2026, 10, 21)
    assert campaign_boost(business, tomorrow) == 0


def test_el_alquiler_crece_con_el_local_y_el_propio_no_paga() -> None:
    business = Business(user_id=1, sector="tienda", barrio="schamann", name="Pino")
    small = nightly_bill(business)
    business.size = 1
    assert nightly_bill(business).rent == rent(business.sector_info, business.barrio_info, SIZES[1])
    assert nightly_bill(business).rent > small.rent
    business.own_listing = "schamann_local"
    own = nightly_bill(business)
    assert own.rent == own.rent_igic == own.withholding == 0 and own.utilities > 0


def test_un_local_propio_tiene_el_tamano_de_sus_metros() -> None:
    cafe = SECTOR_BY_KEY["cafeteria"]  # 50 m²
    assert size_for(cafe, 30) is None
    assert size_for(cafe, 40) == 0
    assert size_for(cafe, 80) == 1
    assert size_for(cafe, 120) == 2


def test_la_vitrina_corta_la_merma_a_la_mitad() -> None:
    business = Business(user_id=1, sector="cafeteria", barrio="triana", name="Bar")
    business.stock = {"bocadillo": 20}
    assert spoil(business) == {"bocadillo": 10}
    business.stock = {"bocadillo": 20}
    business.equipment = ["vitrina"]
    assert spoil(business) == {"bocadillo": 5}


def test_el_precio_de_un_producto_mueve_su_parte_de_la_clientela() -> None:
    business = Business(user_id=1, sector="cafeteria", barrio="triana", name="Bar")
    assert price_clients(business) == pytest.approx(1.0)
    business.prices = {"bocadillo": "carisimo"}
    assert business.level("bocadillo").key == "carisimo"
    assert business.level("cafe").key == "normal"
    weights = {p.key: p.weight for p in business.sector_info.products}
    share = weights["bocadillo"] / sum(weights.values())
    assert price_clients(business) == pytest.approx(1 - share * (1 - 0.68))


def test_una_resena_sale_de_la_nota_del_cliente() -> None:
    client = Client(seed=1, line="Hola")
    happy = Outcome(client, "ok", speed="fast", revenue=500)
    # Deja reseña (0,1 < 25 %), sin azar en las estrellas (0,5 → +0) y por un desconocido.
    who, stars, _line = review_of(happy, Seq(0.1, 0.5, 0.0, 0.0))  # type: ignore[misc]
    assert stars == 5 and who
    angry = Outcome(client, "left")
    assert review_of(angry, Seq(0.4, 0.5, 0.0, 0.0))[1] == 1  # type: ignore[index]
    assert review_of(happy, Seq(0.3)) is None  # el contento casi nunca escribe


def test_quien_seguia_en_la_cola_al_cerrar_no_deja_resena() -> None:
    # No llegó al mostrador: ni mueve la reputación ni escribe, aunque el azar diga que sí.
    gone = Outcome(Client(seed=1, line="Hola"), "gone")
    assert review_of(gone, Seq(0.0, 0.5, 0.0, 0.0)) is None


# -- Mejoras y campañas -----------------------------------------------------------------


async def test_una_mejora_paga_su_igic_y_se_amortiza_cada_noche(tmp_path: Path) -> None:
    clock = Clock()
    service, _work = await make(tmp_path, clock)
    await open_cafe(service)
    state_before = wallet(tmp_path, STATE_ACCOUNT_ID)[1]
    business, base, tax, _balance = await service.buy_equipment(GUILD, USER, "cafetera2")
    assert (base, tax) == (60_000, igic(60_000))
    assert wallet(tmp_path, STATE_ACCOUNT_ID)[1] - state_before == tax
    # Comprarla no es gasto: se amortiza (base más IGIC, que no se deduce).
    assert all(c != "negocio:mejora" for c, _g, _w in activity_records(tmp_path))
    asset = next(a for a in business.assets if a["key"] == "cafetera2")
    assert asset == {"key": "cafetera2", "cost": base + tax, "rate": 0.12, "left": base + tax}
    with pytest.raises(BusinessError, match="Ya la tienes"):
        await service.buy_equipment(GUILD, USER, "cafetera2")
    with pytest.raises(BusinessError, match="no es para tu negocio"):
        await service.buy_equipment(GUILD, USER, "elevador")
    before = sum(a["left"] for a in business.assets)
    clock.now += DAY
    await service.close_nights(GUILD)
    after = await service.business(GUILD, USER)
    assert after is not None
    night = before - sum(a["left"] for a in after.assets)
    cafetera = next(a for a in after.assets if a["key"] == "cafetera2")
    assert cafetera["left"] == base + tax - round((base + tax) * 0.12 / 7)
    assert [g for c, g, _w in activity_records(tmp_path) if c == "negocio:amortizacion"] == [-night]
    total, balance = wallet(tmp_path, USER)
    assert total == balance  # amortizar no mueve dinero


async def test_la_terraza_paga_su_tasa_cada_noche(tmp_path: Path) -> None:
    clock = Clock()
    service, _work = await make(tmp_path, clock)
    await open_cafe(service)
    await service.buy_equipment(GUILD, USER, "terraza")
    clock.now += DAY
    await service.close_nights(GUILD)
    assert business_taxes(tmp_path)["tasa"] >= 300
    assert ("negocio:terraza", -300, 0) in activity_records(tmp_path)


async def test_una_campana_es_gasto_sube_el_cupo_y_se_acaba(tmp_path: Path) -> None:
    clock = Clock()
    service, _work = await make(tmp_path, clock)
    await open_cafe(service)
    plain = await service.status(GUILD, USER)
    assert plain is not None
    business, base, tax, _balance = await service.start_campaign(GUILD, USER, "buzoneo")
    assert ("negocio:publicidad", -(base + tax), 0) in activity_records(tmp_path)
    assert business.week_totals["marketing"] == -(base + tax)
    boosted = await service.status(GUILD, USER)
    assert boosted is not None and boosted.pool_left == pytest.approx(plain.pool_left * 1.1)
    with pytest.raises(BusinessError, match="campaña en marcha"):
        await service.start_campaign(GUILD, USER, "radio")
    for _ in range(3):
        clock.now += DAY
        await service.close_nights(GUILD)
    done = await service.business(GUILD, USER)
    assert done is not None and done.campaign == ""
    assert any("Se acabó tu campaña" in line for line in done.news)


async def test_el_precio_de_un_producto_se_pone_y_se_quita(tmp_path: Path) -> None:
    service, _work = await make(tmp_path, Clock())
    await open_cafe(service)
    business = await service.set_price(GUILD, USER, "bocadillo", "caro")
    assert business.prices == {"bocadillo": "caro"}
    business = await service.set_price(GUILD, USER, "bocadillo", None)
    assert business.prices == {}
    with pytest.raises(BusinessError):
        await service.set_price(GUILD, USER, "neumatico", "caro")


async def test_un_turno_deja_resenas_que_se_guardan(tmp_path: Path) -> None:
    clock = Clock()
    service, _work = await make(tmp_path, clock)
    await open_cafe(service)
    for product in SECTOR_BY_KEY["cafeteria"].products:
        await service.buy(GUILD, USER, product.key, 2)
    for _ in range(3):
        turn = await service.start_turn(GUILD, USER)
        clock.now = play(turn.game, clock.now) + 600
        result = await service.finish_turn(GUILD, USER, turn)
    business = await service.business(GUILD, USER)
    assert business is not None and business.reviews
    assert all(1 <= row[2] <= 5 for row in business.reviews)
    if result.reviews:
        assert [list(r) for r in result.reviews] == [
            r[1:] for r in business.reviews[-len(result.reviews) :]
        ]
    # Los logros cuentan las reseñas del turno por estrellas.
    mixed = dataclasses.replace(result, reviews=(("Ana", 5, 0), ("Leo", 1, 0), ("Eva", 5, 1)))
    delta = business_stats(mixed)
    assert (delta.add["biz_reviews"], delta.add["biz_reviews_5"], delta.add["biz_reviews_1"]) == (
        3,
        2,
        1,
    )
    assert delta.peak["biz_recommended"] == int(recommended(result.business))


# -- Trasladarse ------------------------------------------------------------------------


async def test_trasladarse_cambia_la_fianza_y_cuesta_clientela_si_cambias_de_barrio(
    tmp_path: Path,
) -> None:
    clock = Clock()
    service, _work = await make(tmp_path, clock)
    await open_cafe(service)
    business = await service.business(GUILD, USER)
    assert business is not None
    business.reputation, business.regulars = 90.0, {"Yeray": 8}
    await service.repository.save(GUILD, business)
    old_deposit = business.deposit
    with pytest.raises(BusinessError, match="Ya estás"):
        await service.move(GUILD, USER, business.barrio, 0)
    moved, quote, _balance = await service.move(GUILD, USER, "schamann", 1)
    assert quote.refund == old_deposit and not quote.same_barrio
    assert moved.barrio == "schamann" and moved.size == 1
    assert moved.deposit == quote.opening.deposit
    assert wallet(tmp_path, HOUSING_DEPOSIT_ACCOUNT_ID)[1] == quote.opening.deposit
    assert moved.reputation == pytest.approx(70.0) and moved.regulars == {"Yeray": 4}
    obras = [a for a in moved.assets if a["key"] == "obra"]
    assert len(obras) == 1
    assert obras[0]["cost"] == quote.opening.setup + quote.opening.setup_igic


async def test_un_local_propio_no_paga_alquiler_no_se_vende_y_si_lo_pierdes_cierra(
    tmp_path: Path,
) -> None:
    clock = Clock()
    service, _work = await make(tmp_path, clock)
    housing_repo = HousingRepository(tmp_path / "bot.db")
    await housing_repo.initialize()
    service.housing = housing_repo
    housing = HousingService(housing_repo, service.economy, clock=clock, rng=random.Random(3))
    await open_cafe(service)
    await give(service, 5_000_000)
    await housing.buy(GUILD, USER, "triana_local", 100)
    locals_ = await service.own_locals(GUILD, USER)
    triana = next(o for o in locals_ if o.listing.key == "triana_local")
    assert triana.fits and triana.size == 1  # 80 m² para una cafetería de 50: mediano
    business, quote, _balance = await service.move_to_own(GUILD, USER, "triana_local")
    assert business.own_listing == "triana_local" and business.deposit == 0
    assert quote.same_barrio and nightly_bill(business).rent == 0
    assert wallet(tmp_path, HOUSING_DEPOSIT_ACCOUNT_ID)[1] == 0
    status = sql(tmp_path, "SELECT status FROM housing_leases WHERE listing = 'triana_local'")[0][0]
    assert status == "negocio"
    with pytest.raises(HousingError, match="negocio"):
        await housing.sell(GUILD, USER, "triana_local", 100)
    # Si el local deja de ser suyo (una ejecución, un reparto), el negocio cierra esa noche.
    sql(tmp_path, "UPDATE housing_leases SET status = 'vacio' WHERE listing = 'triana_local'")
    clock.now += DAY
    runs = await service.close_nights(GUILD)
    assert runs and runs[-1].evicted
    assert await service.business(GUILD, USER) is None
    reason = sql(tmp_path, "SELECT reason FROM business_closed")[0][0]
    assert reason == "sin_local"


# -- Cerrar -----------------------------------------------------------------------------


async def test_traspasar_cobra_el_stock_las_mejoras_y_la_clientela_con_su_irpf(
    tmp_path: Path,
) -> None:
    clock = Clock()
    service, _work = await make(tmp_path, clock)
    await open_cafe(service)
    await service.buy(GUILD, USER, "cafe", 2)
    await service.buy_equipment(GUILD, USER, "vitrina")
    business = await service.business(GUILD, USER)
    assert business is not None
    with pytest.raises(BusinessError, match="Nadie quiere"):
        await service.close(GUILD, USER, traspaso=True)
    business.opened_at -= 8 * DAY
    business.reputation = 75.0  # 4 estrellas
    business.last_week = {"ventas": 140_000, "compras": -40_000}
    await service.repository.save(GUILD, business)
    offer = closing_offer(business, clock.now)
    assert offer.can_traspasar and offer.goodwill == round(100_000 / 7 * 3 * 4 / 5, -2)
    state_before = wallet(tmp_path, STATE_ACCOUNT_ID)[1]
    _total, balance_before = wallet(tmp_path, USER)
    closing = await service.close(GUILD, USER, traspaso=True)
    tax = capital_gain_tax(offer.goodwill)
    assert closing.traspaso and closing.gain_tax == tax
    assert closing.stock == offer.stock and closing.proceeds == offer.book + offer.goodwill
    assert wallet(tmp_path, STATE_ACCOUNT_ID)[1] - state_before == tax
    total, balance = wallet(tmp_path, USER)
    assert total == balance
    assert balance - balance_before == closing.refund + offer.traspaso - tax
    assert business_taxes(tmp_path)["plusvalia"] == tax


async def test_cerrar_sin_traspaso_liquida_a_la_baja(tmp_path: Path) -> None:
    clock = Clock()
    service, _work = await make(tmp_path, clock)
    await open_cafe(service)
    await service.buy(GUILD, USER, "cafe", 2)
    await service.buy_equipment(GUILD, USER, "vitrina")
    business = await service.business(GUILD, USER)
    assert business is not None
    offer = closing_offer(business, clock.now)
    closing = await service.close(GUILD, USER)
    assert not closing.traspaso and closing.gain_tax == 0
    assert closing.stock == int(offer.stock * LIQUIDATION_STOCK)
    assert closing.proceeds == int(offer.equipment_book * LIQUIDATION_EQUIPMENT)
