"""Pruebas de `patrimonio`, `fortunas` y la factura fiscal de `hacienda`.

Cubren el valor de los bienes de la tienda, los boletos pendientes, las
cuentas de la riqueza (Gini, parte del más rico), el desglose de impuestos
por miembro (directos, indirectos, devoluciones y Hong Kong), el panel de
`fortunas` (páginas, botones y su copia para los demás) y que todo quepa en
Discord.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs import hacienda_panel as hacienda_panel_module
from bot.cogs import patrimonio as patrimonio_cog
from bot.cogs.hacienda_panel import HaciendaPanel, bill_card
from bot.cogs.patrimonio import (
    FortunasPanel,
    Fortunes,
    Patrimonio,
    fortunes_summary,
    you_line,
)
from bot.repositories.economy import EconomyRepository, Treasury
from bot.repositories.lottery import LotteryRepository
from bot.repositories.shop import ShopRepository, wealth_goods_in
from bot.services.achievements import (
    fortunas_click_stats,
    hacienda_page_stats,
    hacienda_stats,
    patrimonio_stats,
)
from bot.services.economy import STARTING_BALANCE, STATE_ACCOUNT_ID, EconomyService
from bot.services.levels import TIMEZONE
from bot.services.net_worth import Holding, NetWorth, build, gini, top_share
from bot.services.shop import Kind, quote
from bot.services.tax_report import (
    BUSINESS_TAXES,
    HOUSING_TAXES,
    PAGE_LIMIT,
    PAGES,
    TAX_KINDS,
    Group,
    Snapshot,
    TaxBill,
    bar,
    bills,
    leader,
    member_page,
    solidarity_line,
    summary_page,
)
from bot.services.taxes import wealth_quote

GUILD = 1
ANA = 10
BEA = 20
NOW = datetime(2026, 10, 6, 18, tzinfo=TIMEZONE).timestamp()


class Clock:
    """Reloj controlable."""

    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


async def stores(tmp_path: Path) -> tuple[EconomyService, ShopRepository, LotteryRepository]:
    economy_repo = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await economy_repo.initialize()
    shop = ShopRepository(tmp_path / "bot.db")
    await shop.initialize()
    lottery = LotteryRepository(tmp_path / "bot.db")
    await lottery.initialize()
    return EconomyService(economy_repo, clock=Clock()), shop, lottery


async def buy(economy: EconomyService, shop: ShopRepository, item_id: int, user_id: int) -> None:
    current = await shop.item(GUILD, item_id)
    assert current is not None
    price = quote(current, NOW)
    await economy.purchase(
        GUILD,
        user_id,
        base=price.base,
        tax=price.tax,
        concept=current.kind.value,
        reserve=shop.reserve(GUILD, user_id, item_id, expected=price, level=0, now=NOW),
    )


# -- Riqueza ------------------------------------------------------------------------------


def test_gini_y_parte_del_mas_rico() -> None:
    assert gini([100, 100, 100]) == pytest.approx(0.0)
    assert gini([0, 0, 0, 1_000]) == pytest.approx(0.75)
    assert gini([]) == 0.0
    assert top_share([900, 50, 50], 0) == pytest.approx(0.9)
    assert top_share([], 0.1) == 0.0


def test_el_patrimonio_suma_efectivo_bienes_boletos_y_renta() -> None:
    worths = build(
        {ANA: 1_000, BEA: 50},
        [(BEA, "Yate", "🛥️", "objeto", 5_000, None)],
        {ANA: 200},
        {ANA: 30},
    )
    assert [w.user_id for w in worths] == [BEA, ANA]
    assert worths[0].total == 5_050 and worths[0].illiquid_share > 0.9
    assert worths[1].total == 1_230


async def test_los_bienes_valen_lo_pagado_sin_igic_y_lo_que_caduca_se_deprecia(
    tmp_path: Path,
) -> None:
    economy, shop, _lottery = await stores(tmp_path)
    await economy.grant(GUILD, ANA, amount=10_000, reason="prueba")
    yacht = await shop.create_item(
        GUILD, kind=Kind.TROPHY, name="Yate", emoji="🛥️", description="", price=500,
        igic="lujo", now=NOW,
    )  # fmt: skip
    boost = await shop.create_item(
        GUILD, kind=Kind.BOOST, name="Doble XP", emoji="⚡", description="", price=1_000,
        igic="general", duration=3_600, multiplier=2, now=NOW,
    )  # fmt: skip
    await buy(economy, shop, yacht.id, ANA)
    await buy(economy, shop, boost.id, ANA)

    full = {row[1]: row[4] for row in await shop.holdings(GUILD, ANA, NOW)}
    half = {row[1]: row[4] for row in await shop.holdings(GUILD, None, NOW + 1_800)}
    gone = {row[1]: row[4] for row in await shop.holdings(GUILD, ANA, NOW + 3_601)}

    assert full == {"Yate": 500, "Doble XP": 1_000}
    assert half["Doble XP"] == 500
    assert gone == {"Yate": 500}


async def test_el_patrimonio_cuenta_los_objetos_de_la_tienda_y_no_los_potenciadores(
    tmp_path: Path,
) -> None:
    economy, shop, _lottery = await stores(tmp_path)
    await economy.grant(GUILD, ANA, amount=20_000_000, reason="prueba")
    sorolla = await shop.create_item(
        GUILD, kind=Kind.TROPHY, name="Sorolla", emoji="🖼️", description="", price=12_000_000,
        igic="lujo", now=NOW,
    )  # fmt: skip
    boost = await shop.create_item(
        GUILD, kind=Kind.BOOST, name="Doble XP", emoji="⚡", description="", price=1_000,
        igic="general", duration=3_600, multiplier=2, now=NOW,
    )  # fmt: skip
    await buy(economy, shop, sorolla.id, ANA)
    await buy(economy, shop, boost.id, ANA)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.row_factory = sqlite3.Row
        assert wealth_goods_in(connection, GUILD, NOW) == {ANA: 12_000_000}

    economy._clock = Clock(datetime(2026, 10, 5, 12, tzinfo=TIMEZONE).timestamp())
    await economy.charge_wealth_tax(GUILD)  # activación
    economy._clock = Clock(datetime(2026, 10, 12, 12, tzinfo=TIMEZONE).timestamp())
    _, run = await economy.charge_wealth_tax(
        GUILD, goods=lambda connection: wealth_goods_in(connection, GUILD, NOW)
    )
    (charge,) = run.charges
    cash = await economy.balance(GUILD, ANA) + charge.tax
    assert charge.goods == 12_000_000 and charge.balance == cash + 12_000_000
    # El cuadro no da rentas: su parte de la cuota no la rebaja el límite con el IRPF.
    assert charge.tax == wealth_quote(charge.balance, unproductive=12_000_000).due
    assert charge.tax > wealth_quote(charge.balance).due


def test_la_linea_de_las_grandes_fortunas_dice_que_sale_a_cero() -> None:
    text = solidarity_line(3, 1, 0)
    assert "3 declaraciones" in text and "1 fortuna " in text and "0 Y$" in text
    assert "Ley 38/2022" in text
    assert any(kind.key == "grandes_fortunas" for kind in TAX_KINDS)


async def test_boletos_de_sorteos_abiertos_cuentan_y_los_celebrados_no(tmp_path: Path) -> None:
    _economy, _shop, lottery = await stores(tmp_path)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.execute(
            "INSERT INTO lottery_draws (id, guild_id, game, draw_at, status) VALUES "
            "(1, ?, 'primitiva', 1, 'open'), (2, ?, 'primitiva', 0, 'drawn')",
            (GUILD, GUILD),
        )
        connection.execute(
            "INSERT INTO lottery_tickets (draw_id, guild_id, user_id, pick, quantity, cost, "
            "created_at) VALUES (1, ?, ?, 'x', 1, 300, 0), (2, ?, ?, 'x', 1, 999, 0)",
            (GUILD, ANA, GUILD, ANA),
        )
    assert await lottery.open_stakes(GUILD) == {ANA: 300}


# -- Impuestos ----------------------------------------------------------------------------


async def test_la_factura_separa_directos_indirectos_y_no_duplica_el_irpf_de_la_nomina(
    tmp_path: Path,
) -> None:
    economy, shop, _lottery = await stores(tmp_path)
    salary = await economy.pay_salary(GUILD, ANA, gross=200_000, concept="pala:celador")
    await economy.pay_income(GUILD, ANA, gross=100_000, concept="nivel:30")
    item = await shop.create_item(
        GUILD, kind=Kind.TROPHY, name="Yate", emoji="🛥️", description="", price=500,
        igic="lujo", now=NOW,
    )  # fmt: skip
    await buy(economy, shop, item.id, ANA)

    breakdown = await economy.tax_breakdown(GUILD)
    bill = bills(breakdown)[0]
    slip = salary.payslip

    assert bill.amounts["irpf_trabajo"] == slip.irpf
    assert bill.amounts["ss_trabajador"] == slip.ss_worker
    assert bill.amounts["ss_empresa"] == slip.ss_employer
    assert bill.amounts["igic"] == 75
    assert bill.amounts.get("irpf_otros", 0) > 0
    assert bill.indirect == slip.ss_employer + 75
    # Todo lo de la factura es lo que ha entrado en el Estado.
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (state,) = connection.execute(
            "SELECT SUM(delta) FROM economy_ledger WHERE user_id = ?", (STATE_ACCOUNT_ID,)
        ).fetchone()
    assert bill.total == state


async def test_las_devoluciones_de_igic_y_de_la_renta_restan(tmp_path: Path) -> None:
    economy, _shop, _lottery = await stores(tmp_path)
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        connection.executemany(
            "INSERT INTO economy_consumption_tax (guild_id, user_id, concept, base, tax, "
            "created_at) VALUES (?, ?, 'objeto', ?, ?, ?)",
            [(GUILD, ANA, 1_000, 70, NOW), (GUILD, ANA, -1_000, -70, NOW)],
        )
        connection.execute(
            "INSERT INTO economy_ledger (guild_id, user_id, delta, balance_after, reason, "
            "created_at) VALUES (?, ?, 40, 40, 'devolucion:renta', ?)",
            (GUILD, ANA, NOW),
        )
        connection.execute(
            "INSERT INTO economy_gambling_days (guild_id, user_id, day, net, withheld, "
            "updated_at) VALUES (?, ?, '2026-10-05', 1000, 100, ?)",
            (GUILD, ANA, NOW),
        )
    (bill,) = bills(await economy.tax_breakdown(GUILD))
    assert "igic" not in bill.amounts
    assert bill.refunded == 40
    assert bill.total == 60


def test_hong_kong_se_ve_pero_no_suma_al_total() -> None:
    bill = TaxBill(ANA, {"extranjero": 500, "irpf_trabajo": 100})
    assert bill.total == 100 and bill.foreign == 500
    assert {kind.group for kind in TAX_KINDS} == set(Group)


def snapshot_of(
    all_bills: list[TaxBill],
    *,
    housing: dict[int, dict[str, int]] | None = None,
    business: dict[int, dict[str, int]] | None = None,
    names: dict[int, str] | None = None,
) -> Snapshot:
    return Snapshot(
        guild_id=GUILD,
        year=2026,
        treasury=Treasury(123_456_789, 987_654_321, 123_456_789, (), debt=5_000),
        bills=all_bills,
        housing=housing or {},
        business=business or {},
        solidarity=(3, 1, 0),
        names=names or {},
    )


def texts(view: discord.ui.LayoutView) -> list[str]:
    return [c.content for c in view.walk_children() if isinstance(c, discord.ui.TextDisplay)]


def test_las_pestañas_de_hacienda_caben_con_muchos_miembros() -> None:
    every = {kind.key: 123_456_789 for kind in TAX_KINDS}
    all_bills = bills({1_000 + i: dict(every) for i in range(40)})
    names = {b.user_id: "Nombre_larguísimo_de_alguien_" + str(b.user_id) for b in all_bills}
    homes = {line.key: 9_876_543 for line in HOUSING_TAXES}
    shops = {line.key: 9_876_543 for line in BUSINESS_TAXES}
    snapshot = snapshot_of(
        all_bills,
        housing={b.user_id: dict(homes) for b in all_bills},
        business={b.user_id: dict(shops) for b in all_bills},
        names=names,
    )
    owner = MagicMock(spec=discord.Member, id=1_000, bot=False)
    for key, _emoji, _label in PAGES:
        panel = HaciendaPanel(MagicMock(), snapshot, owner, page=key)
        shown = texts(panel)
        assert len(shown[1]) <= PAGE_LIMIT, key
        # Discord no admite más de 4.000 caracteres entre todos los textos ni 40 componentes.
        assert sum(map(len, shown)) <= 4_000, key
        assert len(list(panel.walk_children())) <= 40
        ids = [c.custom_id for c in panel.walk_children() if isinstance(c, discord.ui.Button)]
        assert len(ids) == len(set(ids)) == len(PAGES)
    # Las listas que no caben dicen cuántos faltan.
    panel = HaciendaPanel(MagicMock(), snapshot, owner, page="empresas")
    assert "empresarios más." in texts(panel)[1]
    personal = member_page(
        member_name="Ana", all_bills=all_bills, year_bills=all_bills, user_id=1_000, year=2026,
        homes=homes, business=shops,
    )  # fmt: skip
    assert len(personal) <= 4_000
    assert "### 🏪 Por sus negocios" in personal and "### 🏠 Por sus pisos" in personal
    empty = member_page(
        member_name="Nadie", all_bills=[], year_bills=[], user_id=5, year=2026, homes={},
        business={},
    )  # fmt: skip
    assert "Nada que declarar" in texts(bill_card(empty))[0]


def test_el_resumen_reparte_lo_recaudado_en_barras() -> None:
    all_bills = bills({ANA: {"irpf_trabajo": 750, "igic": 250, "devuelto": 100}, BEA: {}})
    page = summary_page(snapshot_of(all_bills, names={ANA: "Ana"}))
    assert f"{bar(0.75)} **75,0 %** 🪏 IRPF nóm." in page
    assert f"{bar(0.25)} **25,0 %** 🛒 IGIC" in page
    assert "devuelto −100 Y$" in page and "🥇 Ana · **900 Y$**" in page
    assert bar(0.75) == "`▰▰▰▰▰▰▰▰▱▱`" and bar(2) == "`▰▰▰▰▰▰▰▰▰▰`"
    assert bar(0.01) == "`▰▱▱▱▱▱▱▱▱▱`" and bar(0) == "`▱▱▱▱▱▱▱▱▱▱`"
    assert "Nadie ha pagado" in summary_page(snapshot_of([]))


async def test_otro_miembro_que_pulsa_una_pestaña_recibe_su_copia() -> None:
    from interaction_fakes import fake_interaction

    all_bills = bills({ANA: {"igic": 300}, BEA: {"igic": 100}})
    snapshot = snapshot_of(
        all_bills,
        business={ANA: {"igic": 300}, BEA: {"igic": 100}},
        names={ANA: "Ana", BEA: "Bea"},
    )
    owner = MagicMock(spec=discord.Member, id=BEA, bot=False)
    panel = HaciendaPanel(MagicMock(), snapshot, owner)
    track = AsyncMock()
    stranger = fake_interaction()
    stranger.user = MagicMock(spec=discord.Member, id=ANA, bot=False)
    tab = next(
        c for c in panel.walk_children()
        if isinstance(c, discord.ui.Button) and c.custom_id == "hacienda:pagina:empresas"
    )  # fmt: skip
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(hacienda_panel_module.logros, "track", track)
        await tab.callback(stranger)
        assert panel.page == "resumen"  # el de Bea no se mueve
        sent = stranger.response.send_message.await_args.kwargs
        assert sent["ephemeral"] is True and sent["view"].page == "empresas"
        assert "Ana" in texts(sent["view"])[1]
        stats = track.await_args.args[-1]
        assert stats.add == {"hacienda_business": 1, "hacienda_business_top": 1}
        mine = fake_interaction()
        mine.user = owner
        await tab.callback(mine)
        assert panel.page == "empresas"
        assert mine.response.edit_message.await_args.kwargs["view"] is panel
        assert track.await_args.args[-1].add == {"hacienda_business": 1}


def check(embed: discord.Embed) -> None:
    assert len(embed) <= 6_000
    assert len(embed.fields) <= 25
    for field in embed.fields:
        assert 0 < len(field.value) <= 1_024


def test_quien_encabeza_cada_pestaña() -> None:
    all_bills = bills({ANA: {"igic": 300}, BEA: {"igic": 100}})
    alone = snapshot_of(all_bills, housing={ANA: {"ibi": 5}}, business={BEA: {"igic": 1}})
    assert leader(alone, "gente") == ANA
    assert leader(alone, "caseros") is None and leader(alone, "empresas") is None
    both = snapshot_of(all_bills, housing={ANA: {"ibi": 5}, BEA: {"ibi": 9}})
    assert leader(both, "caseros") == BEA


# -- Logros -------------------------------------------------------------------------------


def test_logros_de_patrimonio_fortunas_y_hacienda() -> None:
    mine = patrimonio_stats(
        listing=False, snooping=False, net_worth=60_000, illiquid=True, rank=1, people=3
    )
    assert mine.add == {"patrimonio_views": 1, "patrimonio_illiquid": 1}
    assert mine.peak == {"net_worth_max": 60_000}
    listing = patrimonio_stats(
        listing=True, snooping=False, net_worth=0, illiquid=False, rank=3, people=3
    )
    assert listing.add == {"fortunas_views": 1, "fortunas_last": 1}
    fourth = patrimonio_stats(
        listing=True, snooping=False, net_worth=0, illiquid=False, rank=4, people=5
    )
    assert fourth.add == {"fortunas_views": 1, "fortunas_fourth": 1}
    assert fortunas_click_stats(
        turned=True, last_page=True, me=False, rank=30, borrowed=False
    ).add == {"fortunas_pages": 1, "fortunas_last_page": 1}
    assert fortunas_click_stats(
        turned=False, last_page=False, me=True, rank=20, borrowed=True
    ).add == {"fortunas_me": 1, "fortunas_borrowed": 1}
    tax = hacienda_stats(own=True, snooping=False, share=0.6, indirect_over_direct=True)
    assert tax.add == {
        "hacienda_views": 1,
        "hacienda_self": 1,
        "hacienda_pillar": 1,
        "hacienda_hidden": 1,
    }
    tab = hacienda_page_stats(page="caseros", top=True, seen_all=True)
    assert tab.add == {"hacienda_landlord_top": 1, "hacienda_all_tabs": 1}
    assert hacienda_page_stats(page="gente", top=True, seen_all=False).add == {}


# -- Cog ----------------------------------------------------------------------------------


def member(user_id: int, name: str) -> MagicMock:
    found = MagicMock(spec=discord.Member)
    found.id = user_id
    found.bot = False
    found.display_name = name
    found.display_avatar.url = f"https://cdn.discordapp.com/avatars/{user_id}/a.png"
    return found


def guild_of(*names: tuple[int, str]) -> MagicMock:
    """Servidor falso: los de `names` con su nombre y cualquier otro como `m<id>`."""
    known = dict(names)
    guild = MagicMock(spec=discord.Guild)
    guild.id = GUILD
    guild.get_member = MagicMock(side_effect=lambda uid: member(uid, known.get(uid, f"m{uid}")))
    return guild


async def test_patrimonio_responde_con_un_embed_y_fortunas_con_un_panel(tmp_path: Path) -> None:
    economy, shop, lottery = await stores(tmp_path)
    await economy.grant(GUILD, ANA, amount=80_000, reason="prueba")
    await economy.balance(GUILD, BEA)
    cog = Patrimonio(MagicMock(), economy, shop=shop, lottery=lottery)
    guild = guild_of()
    ana = member(ANA, "Ana")
    track = AsyncMock()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(patrimonio_cog.logros, "track", track)
        embed = await cog.patrimonio_embed(guild, ana, ana, None)
        assert "Ana" in embed.title
        assert "Patrimonio" in embed.fields[-1].value
        check(embed)
        panel = await cog.fortunas_panel(guild, ana)
        await cog._fortunas_logros(ana, None, panel.fortunes)
    shown = "\n".join(texts(panel))
    # El banco custodia el dinero de las dos: siempre va por delante.
    assert "🥇 **🏦 El Banco**" in shown
    assert f"🥈 📍 m{ANA}" in shown and f"🥉 m{BEA}" in shown
    assert "📍 Tú: puesto 2 de 3" in shown
    # Los dos del podio llevan su avatar al lado.
    thumbs = [c for c in panel.walk_children() if isinstance(c, discord.ui.Thumbnail)]
    assert [t.media.url for t in thumbs] == [
        f"https://cdn.discordapp.com/avatars/{ANA}/a.png",
        f"https://cdn.discordapp.com/avatars/{BEA}/a.png",
    ]
    # Con una sola página no hay botones.
    assert not [c for c in panel.walk_children() if isinstance(c, discord.ui.Button)]
    first = track.await_args_list[0].args[4]
    assert first.peak["net_worth_max"] == STARTING_BALANCE + 80_000
    second = track.await_args_list[1].args[4]
    assert second.add["fortunas_first"] == 1


def test_quien_no_tiene_nada_tiene_patrimonio_cero() -> None:
    assert NetWorth(ANA).total == 0
    assert NetWorth(ANA).illiquid_share == 0.0


async def test_fortunas_enseña_el_titulo_que_luce_cada_uno(tmp_path: Path) -> None:
    economy, shop, lottery = await stores(tmp_path)
    await economy.grant(GUILD, ANA, amount=80_000, reason="prueba")
    cog = Patrimonio(MagicMock(), economy, shop=shop, lottery=lottery)

    def title_of(_bot: object, _guild_id: int, user_id: int) -> str | None:
        return "🏰 Marquesado de Mogán" if user_id == ANA else None

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(patrimonio_cog.tienda, "title_of", title_of)
        panel = await cog.fortunas_panel(guild_of(), member(BEA, "Bea"))
    assert f"m{ANA} · *🏰 Marquesado de Mogán*" in "\n".join(texts(panel))


def rich(user_id: int, total: int, **parts: int) -> NetWorth:
    """Alguien con `total` en efectivo (más lo que se pase aparte)."""
    return NetWorth(user_id=user_id, cash=total, **parts)


def fortunes_of(*everyone: NetWorth, names: dict[int, str] | None = None) -> Fortunes:
    ordered = tuple(sorted(everyone, key=lambda w: -w.total))
    return Fortunes(
        guild_id=GUILD,
        everyone=ordered,
        names=names or {w.user_id: f"m{w.user_id}" for w in ordered},
        avatars={
            w.user_id: f"https://cdn.discordapp.com/avatars/{w.user_id}/a.png" for w in ordered[:3]
        },
        read_at=NOW,
    )


def buttons(panel: discord.ui.LayoutView) -> dict[str, discord.ui.Button]:
    return {
        c.custom_id: c for c in panel.walk_children() if isinstance(c, discord.ui.Button)
    }  # fmt: skip


def test_las_paginas_de_fortunas_caben_en_discord_con_muchos_miembros() -> None:
    """35 miembros con nombre y título largos y todo el desglose: 4 páginas que Discord acepta."""
    everyone = [
        rich(
            1_000 + i, 9_876_543_210 - i, homes=1_234_567_890, companies=98_765_432,
            lottery=12_345, renta=67_890, debts=5_432_100,
            goods=(Holding("Trono", "👑", Kind.TROPHY.value, 1_000_000, None),),
        )
        for i in range(35)
    ]  # fmt: skip
    names = {
        w.user_id: "Nombre_larguísimo_de_32_letras__ · *👑 Grandeza de España*" for w in everyone
    }
    fortunes = fortunes_of(*everyone, names=names)
    owner = MagicMock(spec=discord.Member, id=1_034, bot=False)
    seen = []
    for page in range(4):
        panel = FortunasPanel(MagicMock(), fortunes, owner, page=page)
        assert panel.pages == 4
        shown = texts(panel)
        assert sum(map(len, shown)) <= 4_000, page
        assert panel.total_children_count <= 40, page
        ids = list(buttons(panel))
        assert ids == ["fortunas:anterior", "fortunas:pagina", "fortunas:siguiente", "fortunas:yo"]
        assert buttons(panel)["fortunas:pagina"].label == f"{page + 1}/4"
        assert buttons(panel)["fortunas:anterior"].disabled is (page == 0)
        assert buttons(panel)["fortunas:siguiente"].disabled is (page == 3)
        panel.to_components()  # lo que se manda a Discord se monta sin errores
        seen += [line for text in shown for line in text.splitlines() if line.startswith("`")]
    sections = [c for c in FortunasPanel(MagicMock(), fortunes, owner).walk_children()]
    assert sum(isinstance(c, discord.ui.Section) for c in sections) == 3
    # Del 4.º al 35.º, cada uno una vez y en orden; el último es el dueño, marcado.
    assert [int(line[1 : line.index(".")]) for line in seen] == list(range(4, 36))
    assert seen[-1].startswith("`35.` 📍 ")


def test_la_cabecera_de_fortunas_dice_de_que_es_y_como_se_reparte() -> None:
    ranked = [
        rich(ANA, 600, homes=1_200, companies=200, debts=100),
        rich(BEA, 0, goods=(Holding("Gorra", "🧢", Kind.TROPHY.value, 5, None),)),
    ]
    summary = fortunes_summary(ranked)
    lines = summary.splitlines()
    assert lines[0] == "## 1.905 Y$"
    assert lines[1] == "🏠 60 % pisos · 💵 30 % efectivo · 🏢 10 % sociedades · 🛍️ <1 % tienda"
    assert lines[2] == "🏦 −100 Y$ de deudas"
    assert lines[3].startswith("▰▰▰▰▰▱▱▱▱▱ Gini **0,50**")
    assert "el más rico tiene el 100 %" in lines[3]


def test_la_linea_de_tu_puesto_en_fortunas() -> None:
    everyone = fortunes_of(rich(ANA, 900), rich(BEA, 500), rich(30, 500), NetWorth(40, debts=50))
    assert you_line(everyone.everyone, ANA) == (
        "📍 Tú: puesto 1 de 3 · **900 Y$** · le sacas 400 Y$ al 2.º"
    )
    assert you_line(everyone.everyone, BEA).endswith("te faltan 400 Y$ para el 1.º")
    assert you_line(everyone.everyone, 30).endswith("empatas con el 2.º")
    assert you_line(everyone.everyone, 40) == (
        "📍 Tú: no sales en la lista (patrimonio neto de -50 Y$)"
    )
    assert you_line(everyone.everyone, 99).endswith("(patrimonio neto de 0 Y$)")
    assert you_line(fortunes_of(rich(ANA, 1)).everyone, ANA) == "📍 Tú: puesto 1 de 1 · **1 Y$**"
    empty = FortunasPanel(MagicMock(), fortunes_of(), member(ANA, "Ana"))
    assert "Nadie tiene nada" in "\n".join(texts(empty))


async def test_los_botones_de_fortunas_pasan_pagina_y_dan_copia_a_los_demas() -> None:
    from interaction_fakes import fake_interaction

    fortunes = fortunes_of(*(rich(1_000 + i, 10_000 - i) for i in range(25)))
    owner = member(1_000, "Ana")
    panel = FortunasPanel(MagicMock(), fortunes, owner)
    track = AsyncMock()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(patrimonio_cog.logros, "track", track)
        mine = fake_interaction(owner)
        await buttons(panel)["fortunas:siguiente"].callback(mine)
        assert panel.page == 1
        assert mine.response.edit_message.await_args.kwargs["view"] is panel
        assert track.await_args.args[-1].add == {"fortunas_pages": 1}
        mine = fake_interaction(owner)
        await buttons(panel)["fortunas:siguiente"].callback(mine)
        assert panel.page == 2
        assert track.await_args.args[-1].add == {"fortunas_pages": 1, "fortunas_last_page": 1}
        # El 23.º pulsa 📍 Yo en el panel de Ana: se lleva su copia en su página.
        stranger = fake_interaction(member(1_022, "Bea"))
        await buttons(panel)["fortunas:yo"].callback(stranger)
        assert panel.page == 2  # el de Ana no se mueve
        sent = stranger.response.send_message.await_args.kwargs
        assert sent["ephemeral"] is True and sent["view"].page == 2
        assert sent["view"].owner.id == 1_022
        assert "📍 Tú: puesto 23 de 25" in "\n".join(texts(sent["view"]))
        assert track.await_args.args[-1].add == {
            "fortunas_last_page": 1, "fortunas_me": 1, "fortunas_far": 1, "fortunas_borrowed": 1,
        }  # fmt: skip
        # Ana vuelve a su sitio con 📍 Yo; quien no sale en la lista va a la última.
        mine = fake_interaction(owner)
        await buttons(panel)["fortunas:yo"].callback(mine)
        assert panel.page == 0
        assert track.await_args.args[-1].add == {"fortunas_me": 1}
        nobody = fake_interaction(member(5, "Nadie"))
        await buttons(panel)["fortunas:yo"].callback(nobody)
        assert nobody.response.send_message.await_args.kwargs["view"].page == 2
