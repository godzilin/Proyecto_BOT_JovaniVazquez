"""Pruebas del Impuesto sobre el Patrimonio, el IGIC y los donativos deducibles."""

from __future__ import annotations

import random as random_module
import sqlite3
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs import donations as donations_cog
from bot.cogs.donations import Donaciones
from bot.cogs.patrimonio import Patrimonio
from bot.repositories.economy import STATE_ACCOUNT_ID, EconomyRepository, LedgerEntry
from bot.services.donations import BY_KEY, ONGS, RAFFLE_MIN, find_ong
from bot.services.economy import STARTING_BALANCE, Declaration, EconomyService
from bot.services.levels import TIMEZONE
from bot.services.taxes import (
    DONATION_FULL_LIMIT,
    SOLIDARITY_WEALTH_THRESHOLD,
    WEALTH_MAX_REDUCTION,
    WEALTH_MINIMUM,
    WEALTH_SCALE,
    annual_eur,
    apply_scale,
    donation_deduction,
    igic,
    legacy_donation_deduction,
    wealth_quote,
    wealth_tax,
    weekly_yd,
)

GUILD = 1
USER = 10
RICH = 20
#: Un patrimonio que paga: el doble del mínimo exento.
BIG = 2 * WEALTH_MINIMUM
#: Lo que paga sin renta en la semana: el límite conjunto con el IRPF le rebaja el 80 %.
DUE = wealth_quote(BIG).due


class Clock:
    def __init__(self, when: datetime) -> None:
        self.now = when.timestamp()

    def at(self, when: datetime) -> None:
        self.now = when.timestamp()

    def __call__(self) -> float:
        return self.now


def day(d: int, hour: int = 18) -> datetime:
    """Octubre de 2026: los lunes son 5, 12 y 19."""
    return datetime(2026, 10, d, hour, tzinfo=TIMEZONE)


async def make_service(tmp_path: Path, clock: Clock) -> EconomyService:
    repository = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await repository.initialize()
    return EconomyService(repository, clock=clock)


def ledger_sum(tmp_path: Path, user_id: int) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            (GUILD, user_id),
        ).fetchone()
    return int(total)


# -- Reglas ----------------------------------------------------------------------------


def test_patrimonio_exento_hasta_el_minimo() -> None:
    assert wealth_tax(WEALTH_MINIMUM) == 0
    assert wealth_tax(WEALTH_MINIMUM + 1) == 0  # 10 € de base: 0,02 € de cuota
    assert wealth_tax(0) == 0


def test_patrimonio_usa_la_escala_real_a_la_escala_general() -> None:
    """13 millones de Y$ por encima del mínimo son 1,3 M€ de base con la escala del art. 30."""
    expected_eur = (
        167_129.45 * 0.002
        + (334_252.88 - 167_129.45) * 0.003
        + (668_499.75 - 334_252.88) * 0.005
        + (1_300_000 - 668_499.75) * 0.009
    )
    assert WEALTH_MINIMUM == 7_000_000  # 700.000 € a 10 Y$ por euro
    assert wealth_tax(WEALTH_MINIMUM + 13_000_000) == round(expected_eur * 10)


def test_patrimonio_es_progresivo() -> None:
    amounts = [WEALTH_MINIMUM * k for k in (2, 5, 20, 200)]
    rates = [wealth_tax(b) / b for b in amounts]
    assert rates == sorted(rates)
    assert wealth_tax(amounts[-1]) < amounts[-1] * 0.035


def test_igic_es_el_siete_por_ciento() -> None:
    assert igic(1_000) == 70
    assert igic(0) == 0


def test_deduccion_80_y_40_con_los_limites() -> None:
    # 80 % de los primeros 250 € del año (los de una semana con la vara) y 40 % del resto.
    assert DONATION_FULL_LIMIT == round(weekly_yd(250))
    gift = DONATION_FULL_LIMIT * 2
    assert donation_deduction(gift, 1_000_000, 10**6) == round(
        DONATION_FULL_LIMIT * 0.8 + DONATION_FULL_LIMIT * 0.4
    )
    # No pasa del 10 % de la base…
    assert donation_deduction(5_000, 10_000, 10_000) == 800
    # …ni de la cuota.
    assert donation_deduction(5_000, 1_000_000, 300) == 300
    # Sin cuota no se recupera nada.
    assert donation_deduction(5_000, 1_000_000, 0) == 0


def test_las_ongs_tienen_cuentas_negativas_y_distintas() -> None:
    accounts = [ong.account_id for ong in ONGS]
    assert len(ONGS) == 11
    assert all(a < 0 for a in accounts)
    assert len(set(accounts)) == 11
    assert find_ong("mares") is BY_KEY["mares"]
    assert find_ong("AYUDA") is BY_KEY["ayudante"]
    assert find_ong("cruz roja") is None


def test_apply_scale_no_cambia() -> None:
    """La escala del Patrimonio reutiliza la función del IRPF."""
    assert apply_scale(0, ((0.0, 0.1),)) == 0


# -- Patrimonio en la base de datos ----------------------------------------------------


async def test_la_primera_semana_solo_activa_y_la_siguiente_cobra(tmp_path: Path) -> None:
    clock = Clock(day(6))
    service = await make_service(tmp_path, clock)
    await service.repository.apply(GUILD, RICH, [_entry(BIG - STARTING_BALANCE)])
    await service.balance(GUILD, USER)  # 1.000 Y$: no paga

    week, first = await service.charge_wealth_tax(GUILD)
    assert first.activation and not first.charges
    assert week == date(2026, 9, 28)

    clock.at(day(12, hour=0))
    week, run = await service.charge_wealth_tax(GUILD)
    again = await service.charge_wealth_tax(GUILD)

    assert week == date(2026, 10, 5)
    assert [(c.user_id, c.balance, c.tax) for c in run.charges] == [(RICH, BIG, DUE)]
    assert run.charges[0].reduced == wealth_tax(BIG) - DUE
    assert again[1].done_before
    assert await service.balance(GUILD, RICH) == BIG - DUE
    assert ledger_sum(tmp_path, RICH) == await service.balance(GUILD, RICH)
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) == DUE
    treasury = await service.treasury(GUILD, since=0)
    assert treasury.collected_total == DUE
    assert treasury.top_contributors == ((RICH, DUE),)


async def test_ni_el_estado_ni_las_ongs_pagan_patrimonio(tmp_path: Path) -> None:
    clock = Clock(day(6))
    service = await make_service(tmp_path, clock)
    await service.repository.apply(GUILD, RICH, [_entry(4 * WEALTH_MINIMUM)])
    await service.donate(
        GUILD,
        RICH,
        ong_key="mares",
        ong_account=BY_KEY["mares"].account_id,
        amount=2 * WEALTH_MINIMUM,
    )
    await service.charge_wealth_tax(GUILD)
    clock.at(day(12))

    _, run = await service.charge_wealth_tax(GUILD)

    assert [c.user_id for c in run.charges] == [RICH]


def _entry(amount: int) -> LedgerEntry:
    return LedgerEntry(amount, "prueba")


# -- Donativos -------------------------------------------------------------------------


async def test_donar_pasa_el_dinero_a_la_ong(tmp_path: Path) -> None:
    service = await make_service(tmp_path, Clock(day(6)))
    ong = BY_KEY["hambre"]

    receipt = await service.donate(
        GUILD, USER, ong_key=ong.key, ong_account=ong.account_id, amount=400
    )

    assert receipt.balance == STARTING_BALANCE - 400
    assert ledger_sum(tmp_path, ong.account_id) == 400
    assert ledger_sum(tmp_path, USER) == receipt.balance
    assert (await service.ong_totals(GUILD))["hambre"] == (400, 1)


async def test_donativo_desgrava_en_la_renta_si_se_pago_irpf(tmp_path: Path) -> None:
    clock = Clock(day(6))
    service = await make_service(tmp_path, clock)
    income = await service.pay_income(GUILD, USER, gross=50_000, concept="nivel:50")
    assert income.tax > 0
    ong = BY_KEY["ayudante"]
    await service.donate(GUILD, USER, ong_key=ong.key, ong_account=ong.account_id, amount=3_000)

    clock.at(day(12, hour=9))
    pending = await service.pending_declarations(GUILD, USER)
    # El servidor pasa a la 8.0.0 el 12: la semana del 5 sigue la regla y el límite de antes.
    expected = legacy_donation_deduction(3_000, 50_000, income.tax)
    assert pending == [Declaration(date(2026, 10, 5), expected)]

    state_before = ledger_sum(tmp_path, STATE_ACCOUNT_ID)
    claim = await service.claim_declarations(GUILD, USER)
    assert claim.refunded == expected
    assert ledger_sum(tmp_path, STATE_ACCOUNT_ID) == state_before - expected
    assert ledger_sum(tmp_path, USER) == claim.balance


async def test_donativo_sin_irpf_no_desgrava(tmp_path: Path) -> None:
    clock = Clock(day(6))
    service = await make_service(tmp_path, clock)
    ong = BY_KEY["desmontar"]
    await service.donate(GUILD, USER, ong_key=ong.key, ong_account=ong.account_id, amount=500)

    clock.at(day(12, hour=9))

    assert await service.pending_declarations(GUILD, USER) == []


# -- Cogs ------------------------------------------------------------------------------


def make_ctx(user_id: int = USER) -> MagicMock:
    ctx = MagicMock()
    ctx.guild = SimpleNamespace(id=GUILD)
    ctx.author = SimpleNamespace(id=user_id, bot=False, display_name="Diego")
    ctx.channel = MagicMock()
    ctx.send = AsyncMock()
    return ctx


async def test_donar_sin_argumentos_ensena_las_ongs(tmp_path: Path) -> None:
    cog = Donaciones(MagicMock(), await make_service(tmp_path, Clock(day(6))))
    ctx = make_ctx()

    await cog.donar_text.callback(cog, ctx)

    panel = ctx.send.await_args.kwargs["view"]
    assert isinstance(panel, donations_cog.DonarPanel) and panel.page is None
    texts = [c.content for c in panel.walk_children() if isinstance(c, discord.ui.TextDisplay)]
    assert "Muro de donantes" in texts[0]
    # Cada ficha cabe en un mensaje de Discord (4.000 caracteres de texto).
    for ong in ONGS:
        panel.page = ong
        panel.rebuild()
        text = next(
            c.content for c in panel.walk_children() if isinstance(c, discord.ui.TextDisplay)
        )
        assert ong.name in text and len(text) <= 4000


async def test_donar_por_texto_dona_y_cuenta_para_logros(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    track = AsyncMock()
    monkeypatch.setattr(donations_cog.logros, "track", track)
    monkeypatch.setattr(donations_cog.renta, "hint", AsyncMock(return_value=None))

    class Noon(datetime):
        # El cog mira la hora de verdad: de madrugada sumaba `donation_dawn` y fallaba.
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return datetime(2026, 11, 4, 12, tzinfo=tz)

    monkeypatch.setattr(donations_cog, "datetime", Noon)
    service = await make_service(tmp_path, Clock(day(6)))
    cog = Donaciones(MagicMock(), service)
    ctx = make_ctx()

    await cog.donar_text.callback(cog, ctx, "mares", "mitad")

    assert await service.balance(GUILD, USER) == STARTING_BALANCE // 2
    text = ctx.send.await_args.kwargs["embed"].description
    assert "Mares Limpios" in text
    delta = track.await_args.args[4]
    half = STARTING_BALANCE // 2
    # Es el único donante: encabeza la ONG y el muro.
    assert delta.add == {
        "donated": half,
        "donations": 1,
        "donated_mares": half,
        "donation_top_ong": 1,
        "donation_top_all": 1,
    }
    assert delta.peak == {
        "ongs_supported": 1,
        "donation_max": half,
        "patron_ongs": 0,
        "patron_gold_ongs": 0,
    }


async def test_donar_mas_de_lo_que_tienes_no_mueve_nada(tmp_path: Path) -> None:
    service = await make_service(tmp_path, Clock(day(6)))
    cog = Donaciones(MagicMock(), service)
    ctx = make_ctx()

    await cog.donar_text.callback(cog, ctx, "hambre", "5k")

    assert "No te llega" in ctx.send.await_args.args[0]
    assert await service.balance(GUILD, USER) == STARTING_BALANCE


async def test_el_patrimonio_se_anuncia_y_suma_logros(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock(day(6))
    service = await make_service(tmp_path, clock)
    await service.repository.apply(GUILD, RICH, [_entry(BIG - STARTING_BALANCE)])
    await service.charge_wealth_tax(GUILD)  # activación
    clock.at(day(12))
    monkeypatch.setattr("bot.cogs.patrimonio.time.time", clock)
    note = MagicMock()
    monkeypatch.setattr("bot.cogs.patrimonio.logros.note", note)
    channel = MagicMock(spec=discord.TextChannel)
    channel.name = "chat-general"
    channel.id = 5
    channel.send = AsyncMock()
    guild = SimpleNamespace(
        id=GUILD,
        text_channels=[channel],
        system_channel=None,
        get_member=lambda _id: SimpleNamespace(display_name="Ricachón"),
    )
    cog = Patrimonio(MagicMock(), service)

    await cog.run_guild(guild)  # type: ignore[arg-type]
    await cog.run_guild(guild)  # type: ignore[arg-type]

    channel.send.assert_awaited_once()
    (embed,) = channel.send.await_args.kwargs["embeds"]
    assert "Ricachón" in (embed.description or "")
    delta = note.call_args.args[3]
    assert delta.add == {"wealth_tax_paid": DUE, "wealth_tax_weeks": 1, "wealth_joint_limit": 1}


def test_el_limite_conjunto_con_el_irpf_rebaja_hasta_el_80_por_ciento() -> None:
    assert WEALTH_MAX_REDUCTION == 0.8  # el de la ley (art. 31.c de la Ley 19/1991)
    full = wealth_tax(BIG)
    # Sin renta: Patrimonio + IRPF no pueden pasar del 60 % de nada; se paga lo mínimo.
    broke = wealth_quote(BIG)
    assert broke.full == full and broke.due == full - round(full * WEALTH_MAX_REDUCTION)
    # Con mucha renta en la semana, el límite no toca nada.
    rich = wealth_quote(BIG, irpf_base=10_000_000, irpf_quota=1_000_000)
    assert rich.due == full and rich.reduced == 0
    # Se compara con la cuota del IRPF de la misma semana: si es alta, rebaja más.
    taxed = wealth_quote(BIG, irpf_base=10_000_000, irpf_quota=6_000_000)
    assert taxed.due == full - round(full * WEALTH_MAX_REDUCTION)
    # Lo que no da rentas (los objetos de la tienda) no se rebaja.
    goods = wealth_quote(BIG, unproductive=BIG)
    assert goods.due == full
    mostly = wealth_quote(BIG, unproductive=BIG * 3 // 4)
    assert broke.due < mostly.due < full


def test_el_limite_conjunto_compara_irpf_y_patrimonio_en_euros_del_ano() -> None:
    # El IRPF de la semana va a 250 Y$ por euro con la semana como año; el Patrimonio, a
    # 10 Y$ por euro. Antes de compararlos se pasan los dos a euros del año.
    worth, base, quota = 40_000_000, 364_385, 100_000
    full = wealth_tax(worth)
    base_eur = annual_eur(base) * WEALTH_SCALE
    quota_eur = annual_eur(quota) * WEALTH_SCALE
    excess = round(quota_eur) + full - round(base_eur * 0.60)
    expected = full - min(max(0, excess), full, round(full * WEALTH_MAX_REDUCTION))
    assert wealth_quote(worth, irpf_base=base, irpf_quota=quota).due == expected
    # Con las cifras sin pasar, el límite rebajaba de más.
    assert expected > full - min(quota + full - round(base * 0.60), round(full * 0.8))


def test_las_grandes_fortunas_se_declaran_desde_37_millones_y_salen_a_cero() -> None:
    assert SOLIDARITY_WEALTH_THRESHOLD == 37_000_000
    assert not wealth_quote(SOLIDARITY_WEALTH_THRESHOLD).subject
    for worth in (SOLIDARITY_WEALTH_THRESHOLD + 1, 60_000_000, 500_000_000):
        for income in (0, 10_000_000):
            quote = wealth_quote(worth, irpf_base=income, irpf_quota=income // 10)
            assert quote.subject and quote.solidarity == 0  # se descuenta el Patrimonio


async def test_quien_pasa_de_37_millones_declara_las_grandes_fortunas(tmp_path: Path) -> None:
    clock = Clock(day(6))
    service = await make_service(tmp_path, clock)
    worth = SOLIDARITY_WEALTH_THRESHOLD + 1_000_000
    await service.repository.apply(GUILD, RICH, [_entry(worth - STARTING_BALANCE)])
    await service.charge_wealth_tax(GUILD)  # activación
    clock.at(day(12))
    _, run = await service.charge_wealth_tax(GUILD)
    (charge,) = run.charges
    assert charge.solidarity_subject and charge.solidarity == 0
    assert charge.tax == wealth_quote(worth).due
    assert await service.solidarity_summary(GUILD) == (1, 1, 0)


def test_los_logros_de_un_donativo_con_cifras_y_fechas_con_guasa() -> None:
    from datetime import datetime

    from bot.services.achievements import donation_stats
    from bot.services.levels import TIMEZONE

    xmas = datetime(2026, 12, 24, 20, tzinfo=TIMEZONE)
    delta = donation_stats(ong="hambre", amount=666, ongs_supported=4, balance_after=0, when=xmas)
    assert delta.add["donation_666"] == delta.add["donation_allin"] == 1
    assert delta.add["donation_christmas"] == 1 and delta.add["donated_hambre"] == 666
    assert delta.peak == {"ongs_supported": 4, "donation_max": 666}
    corona = donation_stats(
        ong="corona",
        amount=4_000,
        ongs_supported=2,
        balance_after=5,
        when=datetime(2026, 4, 14, 3, tzinfo=TIMEZONE),
        given={"corona": 5_000, "mares": 100_000},
    )
    # 1.000 + 4.000 = justo el Bronce, y de madrugada.
    for stat in ("donation_exact_level", "donation_dawn"):
        assert corona.add[stat] == 1, stat
    assert corona.peak["patron_level_max"] == 1
    assert corona.peak["patron_ongs"] == 2 and corona.peak["patron_gold_ongs"] == 1
    plain = donation_stats(
        ong="mares", amount=1, ongs_supported=1, balance_after=5, when=xmas.replace(month=3)
    )
    assert plain.add == {"donated": 1, "donations": 1, "donated_mares": 1, "donation_one": 1}


# -- Mecenas y sorteos de las ONGs --------------------------------------------------------


def test_niveles_de_mecenas_y_boletos() -> None:
    from bot.services.donations import (
        GIFTS_BY_ONG,
        PATRON_LEVELS,
        next_patron_level,
        patron_level,
        raffle_tickets,
    )

    assert patron_level(4_999) is None and raffle_tickets(4_999) == 1
    assert patron_level(5_000) is PATRON_LEVELS[0] and raffle_tickets(5_000) == 2
    assert next_patron_level(30_000) is PATRON_LEVELS[2]
    assert patron_level(10**9) is PATRON_LEVELS[-1] and next_patron_level(10**9) is None
    # Cada nivel da más boletos y pide más que el anterior.
    pairs = list(zip(PATRON_LEVELS, PATRON_LEVELS[1:], strict=False))
    assert all(a.threshold < b.threshold and a.tickets < b.tickets for a, b in pairs)
    # Diez regalos exclusivos por ONG.
    assert all(len(GIFTS_BY_ONG[ong.key]) == 10 for ong in ONGS)


def test_el_sorteo_es_el_viernes_a_las_ocho_de_la_tarde() -> None:
    from bot.services.donations import raffle_window

    # Viernes 23/10/2026: antes de las 20:00 vale el sorteo del viernes anterior.
    start, end = raffle_window(datetime(2026, 10, 23, 19, 59, tzinfo=TIMEZONE))
    assert end == datetime(2026, 10, 16, 20, tzinfo=TIMEZONE)
    assert start == datetime(2026, 10, 9, 20, tzinfo=TIMEZONE)
    _start, end = raffle_window(datetime(2026, 10, 23, 20, 0, tzinfo=TIMEZONE))
    assert end == datetime(2026, 10, 23, 20, tzinfo=TIMEZONE)
    _start, end = raffle_window(datetime(2026, 10, 26, 9, tzinfo=TIMEZONE))  # lunes
    assert end == datetime(2026, 10, 23, 20, tzinfo=TIMEZONE)


def test_los_mecenas_llevan_mas_boletos() -> None:
    import random

    from bot.services.donations import RAFFLE_MIN, draw_winner

    rng = random.Random(3)
    entries = {1: (RAFFLE_MIN, RAFFLE_MIN), 2: (RAFFLE_MIN, 500_000)}  # 2 es Patrono: 8 a 1
    wins = sum(draw_winner(entries, rng) == 2 for _ in range(900))
    assert 700 < wins < 900
    # Quien no llega al tramo del 80 % en la semana no entra, aunque sea Patrono.
    assert draw_winner({1: (RAFFLE_MIN - 1, 10**9)}, rng) is None
    assert draw_winner({1: (RAFFLE_MIN - 1, 10**9), 2: (RAFFLE_MIN, 0)}, rng) == 2


@pytest.mark.lenta
def test_las_rarezas_de_suerte_de_las_ongs_salen_de_la_simulacion() -> None:
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "docs" / "auditoria_ongs.py"
    spec = importlib.util.spec_from_file_location("auditoria_ongs", path)
    assert spec is not None and spec.loader is not None
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    rng = random_module.Random(1)
    runs = [audit.simulate(10, 2, 520, rng) for _ in range(100)]
    for achievement in audit.CATALOG:
        if len(achievement.conditions) != 1 or achievement.stat not in audit.STATS:
            continue
        stat, goal = achievement.conditions[0]
        days = audit.days_to([r[stat] for r in runs], goal)
        wanted = audit.rarity_for(days) if days is not None else audit.Rarity.MYTHIC
        assert wanted is achievement.rarity, (achievement.id, days)


async def test_cada_ong_sortea_una_sola_vez_entre_los_donantes_de_la_semana(
    tmp_path: Path,
) -> None:
    from bot.services.donations import raffle_window

    clock = Clock(day(20))  # martes
    service = await make_service(tmp_path, clock)
    for user in (USER, RICH):
        await service.repository.apply(GUILD, user, (LedgerEntry(10_000, "prueba"),))
    await service.donate(GUILD, USER, ong_key="mares", ong_account=-3, amount=100)
    await service.donate(GUILD, RICH, ong_key="hambre", ong_account=-4, amount=100)
    clock.at(day(23, 21))  # viernes, después del sorteo: esta ya no entra
    await service.donate(GUILD, RICH, ong_key="mares", ong_account=-3, amount=100)

    window = raffle_window(day(23, 21))
    seen: dict[str, dict[int, tuple[int, int]]] = {}

    def draw(ong: str, entries: dict[int, tuple[int, int]]) -> int:
        seen[ong] = entries
        return min(entries)

    drawn = await service.draw_ong_raffles(GUILD, window, draw)
    assert sorted(drawn) == [("hambre", RICH), ("mares", USER)]
    assert seen["mares"] == {USER: (100, 100)}
    assert await service.draw_ong_raffles(GUILD, window, draw) == [], "no se repite"


async def test_subir_de_nivel_de_mecenas_trae_un_regalo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bot.services.donations import PATRON_LEVELS

    track = AsyncMock()
    gift = SimpleNamespace(catalog_key="ong_mares_gorra", emoji="🧢", name="Gorra")
    grant = AsyncMock(return_value=gift)
    monkeypatch.setattr(donations_cog.logros, "track", track)
    monkeypatch.setattr(donations_cog.renta, "hint", AsyncMock(return_value=None))
    monkeypatch.setattr(donations_cog.tienda, "grant_ong_gift", grant)
    service = await make_service(tmp_path, Clock(day(6)))
    await service.repository.apply(GUILD, USER, (LedgerEntry(30_000, "prueba"),))
    cog = Donaciones(MagicMock(), service)
    ctx = make_ctx()

    await cog.donar_text.callback(cog, ctx, "mares", "4k")
    assert grant.await_count == 0
    # De 4.000 a 26.000: cruza Bronce y Plata de golpe, un regalo por cada uno.
    await cog.donar_text.callback(cog, ctx, "mares", "22k")
    levels = [c.kwargs["exclusive"] for c in grant.await_args_list]
    assert levels == [PATRON_LEVELS[0].exclusive, PATRON_LEVELS[1].exclusive]
    text = ctx.send.await_args.kwargs["embed"].description
    assert "Plata" in text and "Gorra" in text and "exclusivo" in text
    delta = track.await_args.args[4]
    assert delta.peak["patron_level_max"] == 2
    assert delta.add["ong_gifts"] == delta.add["ong_exclusive_gifts"] == 2


async def test_los_sorteos_se_anuncian_y_dan_logros(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = MagicMock()
    gift = SimpleNamespace(catalog_key="cosa_barata", emoji="🧦", name="Calcetín")
    monkeypatch.setattr(donations_cog.logros, "note", note)
    monkeypatch.setattr(donations_cog.tienda, "grant_ong_gift", AsyncMock(return_value=gift))
    service = MagicMock()

    async def draw_ong_raffles(_guild: int, _window: object, draw: object) -> list:
        # Entran USER (Bronce) y RICH (Patrono): gana USER con un boleto de 2 contra 8.
        entries = {USER: (RAFFLE_MIN, 5_000), RICH: (RAFFLE_MIN, 500_000), 99: (1, 10**9)}
        draw("platano", entries)  # type: ignore[operator]
        return [("platano", USER)]

    service.draw_ong_raffles = AsyncMock(side_effect=draw_ong_raffles)
    cog = Donaciones(MagicMock(), service)
    channel = MagicMock()
    channel.send = AsyncMock()
    guild = MagicMock()
    guild.id = GUILD
    guild.text_channels = [channel]
    channel.name = donations_cog.ANNOUNCE_CHANNEL_NAME
    guild.get_member.return_value = SimpleNamespace(display_name="Diego")

    await cog.run_raffles(guild)
    await cog.run_raffles(guild)  # la misma semana no vuelve a sortear

    assert service.draw_ong_raffles.await_count == 1
    text = channel.send.await_args.kwargs["embed"].description
    assert "Plátano" in text and "Diego" in text and "Calcetín" in text
    deltas = {c.args[2]: c.args[3] for c in note.call_args_list}
    assert set(deltas) == {USER, RICH}, "quien no llega al mínimo no juega"
    assert deltas[USER].add == {
        "ong_raffle_entered": 1,
        "ong_raffle_lost": 0,
        "ong_raffle_won": 1,
        "ong_gifts": 1,
        "ong_exclusive_gifts": 0,
    }
    assert deltas[USER].peak["ong_raffle_week_max"] == 1
    assert deltas[RICH].add == {"ong_raffle_entered": 1, "ong_raffle_lost": 1}


def test_las_colecciones_de_regalos_exclusivos() -> None:
    from bot.services.achievements import ong_gift_stats
    from bot.services.donations import GIFTS_BY_ONG

    corona = [g.catalog_key for g in GIFTS_BY_ONG["corona"]]
    owned = {*corona, GIFTS_BY_ONG["mares"][0].catalog_key, "caja_carton"}
    delta = ong_gift_stats(exclusive=True, owned=owned)
    assert delta.add == {"ong_gifts": 1, "ong_exclusive_gifts": 1}
    assert delta.peak == {"ong_exclusives_owned": 11, "ong_exclusive_set_max": 10}
    assert ong_gift_stats(exclusive=False, owned={"caja_carton"}).peak == {}


def test_los_logros_de_los_sorteos_de_las_ongs() -> None:
    from bot.services.achievements import ong_raffle_stats

    alone = ong_raffle_stats(won=True, entrants=1, tickets=1, rival_tickets=0)
    assert alone.add["ong_raffle_alone"] == 1 and "ong_raffle_underdog" not in alone.add
    david = ong_raffle_stats(won=True, entrants=3, tickets=1, rival_tickets=8)
    assert david.add["ong_raffle_underdog"] == 1 and "ong_raffle_alone" not in david.add
    lost = ong_raffle_stats(won=False, entrants=3, tickets=1, rival_tickets=8)
    assert lost.add == {"ong_raffle_entered": 1, "ong_raffle_lost": 1}


def test_fechas_y_cifras_con_guasa_de_los_donativos() -> None:
    from bot.services.achievements import donation_stats

    def stats(ong: str, amount: int, when: datetime, **kwargs: bool) -> dict[str, int]:
        return donation_stats(
            ong=ong, amount=amount, ongs_supported=1, balance_after=9, when=when, **kwargs
        ).add

    assert stats("corona", 65_000, datetime(2026, 11, 13, 10, tzinfo=TIMEZONE))["donation_friday13"]
    assert stats("corona", 5_300, day(6))["donation_corona_rescue"] == 1
    assert stats("corona", 14_999, day(6))["donation_corona_minor"] == 1
    assert stats("buitre", 1_200, day(6))["donation_buitre_rent"] == 1
    assert stats("gamers", 1_337, day(6))["donation_gamers_1337"] == 1
    assert stats("hambre", 5, day(6, 15))["donation_hambre_lunch"] == 1
    assert stats("mares", RAFFLE_MIN, day(31))["donation_halloween"] == 1
    assert stats("mares", RAFFLE_MIN, day(6))["donation_ticket_exact"] == 1
    top = stats("mares", 5, day(6), top_ong=True, top_all=True)
    assert top["donation_top_ong"] == top["donation_top_all"] == 1
