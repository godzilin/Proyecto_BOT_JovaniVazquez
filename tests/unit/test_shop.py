"""Pruebas de la tienda: reglas, venta atómica con IGIC, inventario y vistas.

Se usa la economía y la tienda reales sobre un SQLite temporal; Discord se
sustituye por dobles mínimos (roles con posición, miembros con `add_roles`).
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import ui
from interaction_fakes import fake_interaction

from bot.cogs import shop as shop_cog
from bot.cogs.shop import Backpack, Checkout, Tienda, item_card
from bot.cogs.shop_admin import AdminPanel, ItemEditor, admin_card
from bot.cogs.shop_front import PAGE_SIZE
from bot.repositories.economy import (
    SHOP_ACCOUNT_ID,
    STATE_ACCOUNT_ID,
    EconomyRepository,
    InsufficientFundsError,
    LedgerEntry,
)
from bot.repositories.shop import ShopRepository
from bot.services.achievements import shop_stats
from bot.services.economy import STARTING_BALANCE, EconomyService
from bot.services.levels import TIMEZONE
from bot.services.shop import (
    MAX_BOOST,
    Kind,
    ShopError,
    ShopItem,
    active_multiplier,
    boost_window,
    format_multiplier,
    format_span,
    ineligibility,
    parse_discount,
    parse_limit,
    parse_multiplier,
    parse_price,
    parse_span,
    quote,
    receipt,
    rental_end,
    split_emoji,
)
from bot.services.shop_catalog import CATALOG_BY_KEY
from bot.services.taxes import IGIC_BY_KEY

GUILD = 1
BUYER = 10
OTHER = 11
NOW = datetime(2026, 10, 5, 18, tzinfo=TIMEZONE).timestamp()
DAY = 86400


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def item(**fields: object) -> ShopItem:
    base: dict[str, object] = {
        "id": 1,
        "guild_id": GUILD,
        "kind": Kind.TROPHY,
        "name": "Yate de Perro Sanxe",
        "emoji": "🛥️",
        "description": "Para salir en la prensa del corazón.",
        "price": 1_000,
        "created_at": NOW - 10 * DAY,
    }
    base.update(fields)
    return ShopItem(**base)  # type: ignore[arg-type]


# -- Reglas ----------------------------------------------------------------------------


def test_igic_general_del_7_sobre_el_precio() -> None:
    price = quote(item(), NOW)
    assert (price.base, price.tax, price.total) == (1_000, 70, 1_070)


def test_rebaja_se_resta_antes_del_igic() -> None:
    price = quote(item(discount=20, igic_key="lujo"), NOW)
    assert price.discount == 200 and price.base == 800
    assert price.tax == 120  # 15 % de 800, no de 1.000
    assert price.total == 920


def test_rebaja_caducada_no_se_aplica() -> None:
    price = quote(item(discount=50, discount_until=NOW - 1), NOW)
    assert price.discount == 0 and price.base == 1_000


def test_tipo_cero_no_paga_igic_y_clave_desconocida_usa_el_general() -> None:
    assert quote(item(igic_key="cero"), NOW).tax == 0
    assert quote(item(igic_key="inventado"), NOW).rate is IGIC_BY_KEY["general"]


def test_tipos_de_igic_son_los_de_la_ley_canaria() -> None:
    rates = {key: rate.rate for key, rate in IGIC_BY_KEY.items()}
    assert rates == {
        "cero": 0.0,
        "reducido": 0.03,
        "general": 0.07,
        "incrementado": 0.095,
        "lujo": 0.15,
        # Los seguros no llevan IGIC: Impuesto sobre las Primas de Seguros (8 %).
        "primas": 0.08,
    }
    assert IGIC_BY_KEY["primas"].tax == "IPS"
    assert {r.tax for k, r in IGIC_BY_KEY.items() if k != "primas"} == {"IGIC"}


@pytest.mark.parametrize(
    ("text", "unit", "seconds"),
    [
        ("7d", "d", 7 * DAY),
        ("7", "d", 7 * DAY),
        ("12h", "d", 12 * 3600),
        ("2", "h", 2 * 3600),
        ("1d12h", "d", DAY + 12 * 3600),
        ("2 semanas", "d", 14 * DAY),
        ("3 Días", "d", 3 * DAY),
        ("", "d", None),
        ("permanente", "d", None),
    ],
)
def test_parse_span_entiende_duraciones_en_castellano(
    text: str, unit: str, seconds: int | None
) -> None:
    assert parse_span(text, unit) == seconds


@pytest.mark.parametrize("text", ["30m", "400d", "mañana", "7x"])
def test_parse_span_rechaza_lo_que_no_es_de_una_hora_a_un_ano(text: str) -> None:
    with pytest.raises(ValueError):
        parse_span(text)


def test_format_span_en_castellano() -> None:
    assert format_span(7 * DAY) == "7 días"
    assert format_span(DAY + 12 * 3600) == "1 día y 12 h"
    assert format_span(3600) == "1 h"
    assert format_span(None) == "para siempre"


@pytest.mark.parametrize(
    ("text", "pct"), [("x2", 200), ("×1,5", 150), ("2", 200), ("150%", 150), ("1.25", 125)]
)
def test_parse_multiplier(text: str, pct: int) -> None:
    assert parse_multiplier(text) == pct


def test_multiplicador_fuera_de_rango_y_formato() -> None:
    with pytest.raises(ValueError):
        parse_multiplier("x5")
    assert format_multiplier(200) == "×2"
    assert format_multiplier(150) == "×1,5"
    assert format_multiplier(MAX_BOOST) == "×3"


@pytest.mark.parametrize(("text", "value"), [("5000", 5000), ("5.000", 5000), ("5k", 5000)])
def test_parse_price(text: str, value: int) -> None:
    assert parse_price(text) == value


@pytest.mark.parametrize("text", ["0", "gratis", "-5", "all"])
def test_parse_price_rechaza_precios_raros(text: str) -> None:
    with pytest.raises(ValueError):
        parse_price(text)


def test_limites_y_rebaja_opcionales() -> None:
    assert parse_limit("", "x", 10) is None
    assert parse_limit("ilimitado", "x", 10) is None
    assert parse_limit("3", "x", 10) == 3
    with pytest.raises(ValueError):
        parse_limit("11", "x", 10)
    assert parse_discount("20%") == 20
    assert parse_discount("") == 0
    with pytest.raises(ValueError):
        parse_discount("95")


def test_split_emoji_separa_el_icono_del_nombre() -> None:
    assert split_emoji("🛥️ Yate", Kind.TROPHY) == ("🛥️", "Yate")
    assert split_emoji("<:jovani:123456789012345678> VIP", Kind.ROLE) == (
        "<:jovani:123456789012345678>",
        "VIP",
    )
    assert split_emoji("VIP Dorado", Kind.ROLE) == ("🎭", "VIP Dorado")


def test_ineligibility_explica_cada_motivo() -> None:
    def check(it: ShopItem, **kwargs: object) -> str | None:
        args: dict[str, object] = {"now": NOW, "level": 5, "bought": 0, "owns_role": False}
        args.update(kwargs)
        return ineligibility(it, **args)  # type: ignore[arg-type]

    assert check(item()) is None
    assert "venta" in (check(item(visible=False)) or "")
    # Las razas son ocultas, pero se compran desde el panel de la mascota.
    assert check(item(visible=False, catalog_key="raza_schnauzer_mini_negro")) is None
    assert "Agotado" in (check(item(stock=2, sold=2)) or "")
    assert "nivel 10" in (check(item(min_level=10)) or "")
    assert "ya es tuyo" in (check(item(kind=Kind.ROLE, role_id=5), owns_role=True) or "")
    assert "1 vez" in (check(item(per_user=1), bought=1) or "")
    # Un alquiler sí se puede volver a comprar aunque ya lo tengas.
    assert check(item(kind=Kind.ROLE, role_id=5, duration=DAY), owns_role=True) is None


def test_potenciadores_en_cola_y_alquileres_que_se_suman() -> None:
    assert boost_window(NOW, None, 3600) == (NOW, NOW + 3600)
    assert boost_window(NOW, NOW + 600, 3600) == (NOW + 600, NOW + 4200)
    assert rental_end(NOW, NOW + DAY, 7 * DAY) == NOW + 8 * DAY
    assert rental_end(NOW, None, DAY) == NOW + DAY
    windows = [(NOW - 10, NOW + 10, 200), (NOW + 10, NOW + 20, 300)]
    assert active_multiplier(windows, NOW) == 2.0
    assert active_multiplier(windows, NOW + 15) == 3.0
    assert active_multiplier(windows, NOW + 30) == 1.0


def test_factura_simplificada_cabe_en_el_movil() -> None:
    it = item(discount=10, stock=10)
    text = receipt(it, quote(it, NOW), invoice=42, when=NOW, buyer="Diego", serial=3, edition=10)
    assert "T2026-000042" in text
    assert "Unidad nº 3 de 10" in text
    assert "103.m" in text
    body = text.strip("`\n").splitlines()
    assert all(len(line) <= 30 for line in body)


def test_shop_stats_cuenta_lo_importante() -> None:
    delta = shop_stats(
        kind="objeto",
        total=920,
        tax=120,
        discount_pct=20,
        luxury=True,
        serial=1,
        last_unit=False,
        renewed=False,
        collection=3,
        queued_boosts=0,
        balance_after=0,
    )
    assert delta.add["shop_purchases"] == 1
    assert delta.add["shop_spent"] == 920
    assert delta.add["shop_igic"] == 120
    assert delta.add["shop_luxury"] == 1
    assert delta.add["shop_first_serial"] == 1
    assert delta.add["shop_broke_buy"] == 1
    assert delta.peak["shop_collection_max"] == 3
    assert delta.peak["shop_discount_max"] == 20


# -- Venta y dinero --------------------------------------------------------------------


async def make_stores(tmp_path: Path, clock: Clock) -> tuple[EconomyService, ShopRepository]:
    economy_repo = EconomyRepository(tmp_path / "bot.db", starting_balance=STARTING_BALANCE)
    await economy_repo.initialize()
    shop = ShopRepository(tmp_path / "bot.db")
    await shop.initialize()
    return EconomyService(economy_repo, clock=clock), shop


def wallet(tmp_path: Path, user_id: int) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        row = connection.execute(
            "SELECT balance FROM economy_wallets WHERE guild_id = ? AND user_id = ?",
            (GUILD, user_id),
        ).fetchone()
    return int(row[0]) if row else 0


def ledger_sum(tmp_path: Path, user_id: int) -> int:
    with sqlite3.connect(tmp_path / "bot.db") as connection:
        (total,) = connection.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM economy_ledger WHERE guild_id = ? AND user_id = ?",
            (GUILD, user_id),
        ).fetchone()
    return int(total)


async def buy(
    economy: EconomyService,
    shop: ShopRepository,
    it: ShopItem,
    *,
    user_id: int = BUYER,
    level: int = 0,
    now: float = NOW,
):
    current = await shop.item(GUILD, it.id)
    assert current is not None
    price = quote(current, now)
    (sold_item, sale), balance = await economy.purchase(
        GUILD,
        user_id,
        base=price.base,
        tax=price.tax,
        concept=current.kind.value,
        reserve=shop.reserve(GUILD, user_id, it.id, expected=price, level=level, now=now),
    )
    return sold_item, sale, balance, price


async def test_compra_cobra_base_a_la_caja_e_igic_al_estado(tmp_path: Path) -> None:
    clock = Clock()
    economy, shop = await make_stores(tmp_path, clock)
    it = await shop.create_item(
        GUILD, kind=Kind.TROPHY, name="Yate", emoji="🛥️", description="", price=500,
        igic="lujo", now=NOW,
    )  # fmt: skip
    _item, sale, balance, price = await buy(economy, shop, it)
    assert price.total == 575
    assert balance == STARTING_BALANCE - 575
    assert ledger_sum(tmp_path, BUYER) == balance == wallet(tmp_path, BUYER)
    assert wallet(tmp_path, STATE_ACCOUNT_ID) == 75
    assert wallet(tmp_path, SHOP_ACCOUNT_ID) == 500
    assert sale.invoice == 1 and sale.collection == 1
    treasury = await economy.treasury(GUILD, since=0)
    assert treasury.collected_total == 75
    assert treasury.top_contributors == ((BUYER, 75),)


async def test_sin_saldo_no_se_mueve_nada_ni_se_gasta_la_unidad(tmp_path: Path) -> None:
    economy, shop = await make_stores(tmp_path, Clock())
    it = await shop.create_item(
        GUILD, kind=Kind.TROPHY, name="Joya", emoji="💍", description="", price=5_000,
        igic="general", now=NOW, stock=1,
    )  # fmt: skip
    with pytest.raises(InsufficientFundsError):
        await buy(economy, shop, it)
    after = await shop.item(GUILD, it.id)
    assert after is not None and after.sold == 0
    assert await economy.balance(GUILD, BUYER) == STARTING_BALANCE
    assert wallet(tmp_path, STATE_ACCOUNT_ID) == 0


async def test_edicion_limitada_numera_y_se_agota(tmp_path: Path) -> None:
    economy, shop = await make_stores(tmp_path, Clock())
    it = await shop.create_item(
        GUILD, kind=Kind.TROPHY, name="Pin", emoji="📌", description="", price=10,
        igic="general", now=NOW, stock=2,
    )  # fmt: skip
    _, first, _, _ = await buy(economy, shop, it)
    _, second, _, _ = await buy(economy, shop, it, user_id=OTHER)
    assert (first.serial, first.edition, first.last_unit) == (1, 2, False)
    assert (second.serial, second.last_unit) == (2, True)
    with pytest.raises(ShopError, match="Agotado"):
        await buy(economy, shop, it)
    assert await economy.balance(GUILD, BUYER) == STARTING_BALANCE - 11


async def test_limite_por_persona(tmp_path: Path) -> None:
    economy, shop = await make_stores(tmp_path, Clock())
    it = await shop.create_item(
        GUILD, kind=Kind.TROPHY, name="Pin", emoji="📌", description="", price=10,
        igic="general", now=NOW,
    )  # fmt: skip
    await shop.update_item(GUILD, it.id, per_user=1)
    await buy(economy, shop, it)
    with pytest.raises(ShopError, match="por persona"):
        await buy(economy, shop, it)


async def test_cambio_de_precio_entre_la_caja_y_el_pago_no_cobra(tmp_path: Path) -> None:
    economy, shop = await make_stores(tmp_path, Clock())
    it = await shop.create_item(
        GUILD, kind=Kind.TROPHY, name="Pin", emoji="📌", description="", price=10,
        igic="general", now=NOW,
    )  # fmt: skip
    stale = quote(it, NOW)
    await shop.update_item(GUILD, it.id, price=20)
    with pytest.raises(ShopError, match="precio ha cambiado"):
        await economy.purchase(
            GUILD,
            BUYER,
            base=stale.base,
            tax=stale.tax,
            concept="objeto",
            reserve=shop.reserve(GUILD, BUYER, it.id, expected=stale, level=0, now=NOW),
        )
    assert await economy.balance(GUILD, BUYER) == STARTING_BALANCE


async def test_nivel_minimo_se_comprueba_al_pagar(tmp_path: Path) -> None:
    economy, shop = await make_stores(tmp_path, Clock())
    it = await shop.create_item(
        GUILD, kind=Kind.TROPHY, name="Pin", emoji="📌", description="", price=10,
        igic="general", now=NOW,
    )  # fmt: skip
    await shop.update_item(GUILD, it.id, min_level=10)
    with pytest.raises(ShopError, match="nivel 10"):
        await buy(economy, shop, it, level=3)
    await buy(economy, shop, it, level=10)


async def test_rol_para_siempre_solo_una_vez(tmp_path: Path) -> None:
    economy, shop = await make_stores(tmp_path, Clock())
    it = await shop.create_item(
        GUILD, kind=Kind.ROLE, name="VIP", emoji="🎭", description="", price=100,
        igic="general", now=NOW, role_id=555,
    )  # fmt: skip
    await buy(economy, shop, it)
    with pytest.raises(ShopError, match="ya es tuyo"):
        await buy(economy, shop, it)


async def test_alquiler_se_renueva_y_la_devolucion_lo_deja_como_estaba(tmp_path: Path) -> None:
    economy, shop = await make_stores(tmp_path, Clock())
    it = await shop.create_item(
        GUILD, kind=Kind.ROLE, name="VIP", emoji="🎭", description="", price=100,
        igic="general", now=NOW, role_id=555, duration=7 * DAY,
    )  # fmt: skip
    _, first, _, _ = await buy(economy, shop, it)
    _, second, _, price = await buy(economy, shop, it, now=NOW + DAY)
    assert first.expires_at == NOW + 7 * DAY
    assert second.renewed and second.expires_at == NOW + 14 * DAY
    (entry,) = await shop.inventory(GUILD, BUYER, NOW + DAY)
    assert entry.expires_at == NOW + 14 * DAY

    balance = await economy.refund_purchase(
        GUILD,
        BUYER,
        base=price.base,
        tax=price.tax,
        concept="rol",
        release=shop.release(second.purchase_id),
    )
    (entry,) = await shop.inventory(GUILD, BUYER, NOW + DAY)
    assert entry.expires_at == NOW + 7 * DAY
    assert balance == STARTING_BALANCE - 107
    assert wallet(tmp_path, STATE_ACCOUNT_ID) == 7
    assert ledger_sum(tmp_path, BUYER) == balance
    assert (await economy.treasury(GUILD, since=0)).collected_total == 7


async def test_alquileres_vencidos_salen_en_due_rentals(tmp_path: Path) -> None:
    economy, shop = await make_stores(tmp_path, Clock())
    it = await shop.create_item(
        GUILD, kind=Kind.ROLE, name="VIP", emoji="🎭", description="", price=100,
        igic="general", now=NOW, role_id=555, duration=DAY,
    )  # fmt: skip
    await buy(economy, shop, it)
    assert await shop.due_rentals(NOW + DAY - 1) == []
    due = await shop.due_rentals(NOW + DAY)
    assert [(g, u, r) for _, g, u, r in due] == [(GUILD, BUYER, 555)]
    await shop.mark_expired([due[0][0]])
    assert await shop.due_rentals(NOW + 2 * DAY) == []


async def test_potenciadores_se_ponen_a_la_cola(tmp_path: Path) -> None:
    economy, shop = await make_stores(tmp_path, Clock())
    it = await shop.create_item(
        GUILD, kind=Kind.BOOST, name="Turbo", emoji="⚡", description="", price=100,
        igic="general", now=NOW, duration=3600, multiplier=200,
    )  # fmt: skip
    _, first, _, _ = await buy(economy, shop, it)
    _, second, _, _ = await buy(economy, shop, it, now=NOW + 600)
    assert (first.starts_at, first.expires_at) == (NOW, NOW + 3600)
    assert (second.starts_at, second.expires_at) == (NOW + 3600, NOW + 7200)
    boosts = await shop.active_boosts(NOW + 600)
    assert len(boosts) == 2 and all(b[4] == 200 for b in boosts)


async def test_no_se_pueden_dejar_menos_existencias_que_vendidas(tmp_path: Path) -> None:
    economy, shop = await make_stores(tmp_path, Clock())
    it = await shop.create_item(
        GUILD, kind=Kind.TROPHY, name="Pin", emoji="📌", description="", price=10,
        igic="general", now=NOW, stock=5,
    )  # fmt: skip
    await buy(economy, shop, it)
    await buy(economy, shop, it)
    with pytest.raises(ShopError, match="vendido 2"):
        await shop.update_item(GUILD, it.id, stock=1)
    with pytest.raises(ValueError):
        await shop.update_item(GUILD, it.id, sold=0)


# -- Cog y vistas ----------------------------------------------------------------------


class FakeRole:
    """Rol mínimo: posición para compararse y permisos."""

    def __init__(self, role_id: int, position: int, **perms: bool) -> None:
        self.id = role_id
        self.position = position
        self.managed = False
        self.name = f"rol{role_id}"
        self.permissions = discord.Permissions(**perms)

    def is_default(self) -> bool:
        return False

    def __ge__(self, other: FakeRole) -> bool:
        return self.position >= other.position


def make_guild(*roles: FakeRole) -> MagicMock:
    guild = MagicMock()
    guild.id = GUILD
    guild.me.guild_permissions.manage_roles = True
    guild.me.top_role = FakeRole(1, 50)
    by_id = {r.id: r for r in roles}
    guild.get_role = lambda role_id: by_id.get(role_id)
    return guild


def make_member(guild: MagicMock, user_id: int = BUYER) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.display_name = "Diego"
    member.bot = False
    member.guild = guild
    member.roles = []
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    member.guild_permissions.administrator = True
    return member


def make_interaction(member: MagicMock) -> MagicMock:
    interaction = fake_interaction(member)
    interaction.guild = member.guild
    return interaction


async def make_cog(tmp_path: Path, clock: Clock, catalog: tuple = ()) -> Tienda:
    economy, shop = await make_stores(tmp_path, clock)
    levels = MagicMock()
    levels.member_xp = AsyncMock(return_value=0)
    return Tienda(MagicMock(), economy, shop, levels=levels, clock=clock, catalog=catalog)


def texts(view: ui.LayoutView) -> str:
    return "\n".join(c.content for c in view.walk_children() if isinstance(c, ui.TextDisplay))


def test_role_problem_bloquea_roles_peligrosos_o_por_encima() -> None:
    guild = make_guild()
    assert Tienda.role_problem(guild, FakeRole(2, 10)) is None
    assert "encima" in (Tienda.role_problem(guild, FakeRole(2, 60)) or "")
    assert "administrator" in (
        Tienda.role_problem(guild, FakeRole(2, 10, administrator=True)) or ""
    )
    assert "ban_members" in (Tienda.role_problem(guild, FakeRole(2, 10, ban_members=True)) or "")
    assert "no existe" in (Tienda.role_problem(guild, None) or "")
    guild.me.guild_permissions.manage_roles = False
    assert "Gestionar roles" in (Tienda.role_problem(guild, FakeRole(2, 10)) or "")


async def test_escaparate_cabe_en_los_limites_de_discord(tmp_path: Path) -> None:
    clock = Clock()
    cog = await make_cog(tmp_path, clock)
    for n in range(PAGE_SIZE + 5):
        it = await cog.repository.create_item(
            GUILD, kind=Kind.TROPHY, name=f"Objeto con un nombre largo {n:02d}"[:40],
            emoji="<:jovani_moneda_de_oro:123456789012345678>", description="x" * 200,
            price=1_000_000 + n, igic="general", now=NOW, stock=10,
        )  # fmt: skip
        # Lo más largo que puede salir en una línea: rebaja, existencias y nivel.
        await cog.repository.update_item(GUILD, it.id, min_level=50, discount=25)
    guild = make_guild()
    view = await cog.storefront(guild, make_member(guild))
    assert view.total_children_count <= 40
    assert len(texts(view)) <= 4000
    picker = next(
        c
        for c in view.walk_children()
        if isinstance(c, ui.Select) and c.custom_id == "tienda:elegir"
    )
    assert len(picker.options) == PAGE_SIZE and picker.max_values == PAGE_SIZE
    assert view.pages == 2


async def test_escaparate_vacio_invita_a_usar_catalogo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    guild = make_guild()
    view = await cog.storefront(guild, make_member(guild))
    assert "catalogo" in texts(view)


def test_ficha_enseña_rebaja_existencias_y_etiquetas() -> None:
    card = item_card(item(discount=25, stock=10, sold=7, min_level=5, created_at=NOW), NOW)
    assert "~~1.000 Y$~~ **750 Y$**" in card
    assert "Quedan 3 de 10" in card
    assert "Nivel 5" in card
    assert "Nuevo" in card


async def test_comprar_un_coleccionable_desde_la_caja(tmp_path: Path) -> None:
    clock = Clock()
    cog = await make_cog(tmp_path, clock)
    it = await cog.repository.create_item(
        GUILD, kind=Kind.TROPHY, name="Yate", emoji="🛥️", description="", price=500,
        igic="general", now=NOW, stock=3,
    )  # fmt: skip
    guild = make_guild()
    member = make_member(guild)
    opening = make_interaction(member)
    await cog.open_checkout(opening, it.id)
    checkout = opening.edit_original_response.await_args.kwargs["view"]
    assert isinstance(checkout, Checkout)
    assert "IGIC 7 %" in texts(checkout)

    paying = make_interaction(member)
    await checkout._pay(paying)
    assert checkout.done and not checkout.failed
    shown = texts(checkout)
    assert "FACTURA SIMPLIFICADA" in shown and "Unidad nº 1 de 3" in shown
    assert await cog.economy.balance(GUILD, BUYER) == STARTING_BALANCE - 535
    announcement = paying.followup.send.await_args.args[0]
    assert "Yate" in announcement and "35 Y$" in announcement

    backpack = await cog.backpack_view(guild, member, member)
    assert "nº 1 de 3" in texts(backpack)


def test_la_ficha_dice_lo_que_ya_tienes() -> None:
    assert "Lo tienes" not in item_card(item(), NOW)
    assert "**✅ Lo tienes**" in item_card(item(), NOW, owned=1)
    assert "✅ Tienes 3" in item_card(item(), NOW, owned=3)
    assert "✅ Ya lo tienes" in item_card(item(per_user=1), NOW, owned=1)
    assert "✅ Tienes 2, el máximo" in item_card(item(per_user=2), NOW, owned=2)


async def test_el_escaparate_marca_lo_que_ya_has_comprado(tmp_path: Path) -> None:
    clock = Clock()
    cog = await make_cog(tmp_path, clock)
    it = await cog.repository.create_item(
        GUILD, kind=Kind.TROPHY, name="Yate", emoji="🛥️", description="", price=500,
        igic="general", now=NOW,
    )  # fmt: skip
    guild = make_guild()
    member = make_member(guild)
    view = await cog.storefront(guild, member)
    assert "✅" not in yate_line(view)
    await cog.repository.grant(GUILD, BUYER, it, NOW)
    view = await cog.storefront(guild, member)
    assert "· ✅" in yate_line(view)


def yate_line(view: ui.LayoutView) -> str:
    return next(line for line in texts(view).splitlines() if "**Yate**" in line)


async def test_rol_que_no_se_puede_dar_se_devuelve_entero(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    role = FakeRole(555, 10)
    guild = make_guild(role)
    it = await cog.repository.create_item(
        GUILD, kind=Kind.ROLE, name="VIP", emoji="🎭", description="", price=100,
        igic="general", now=NOW, role_id=role.id,
    )  # fmt: skip
    member = make_member(guild)
    member.add_roles.side_effect = discord.Forbidden(MagicMock(status=403), "no")
    opening = make_interaction(member)
    await cog.open_checkout(opening, it.id)
    checkout = opening.edit_original_response.await_args.kwargs["view"]
    await checkout._pay(make_interaction(member))
    assert checkout.failed and "devuelto" in (checkout.result or "")
    assert await cog.economy.balance(GUILD, BUYER) == STARTING_BALANCE
    assert wallet(tmp_path, STATE_ACCOUNT_ID) == 0
    after = await cog.repository.item(GUILD, it.id)
    assert after is not None and after.sold == 0


async def test_rol_comprado_se_da_y_se_puede_quitar_desde_la_mochila(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    role = FakeRole(555, 10)
    guild = make_guild(role)
    it = await cog.repository.create_item(
        GUILD, kind=Kind.ROLE, name="VIP", emoji="🎭", description="", price=100,
        igic="general", now=NOW, role_id=role.id,
    )  # fmt: skip
    member = make_member(guild)
    opening = make_interaction(member)
    await cog.open_checkout(opening, it.id)
    checkout = opening.edit_original_response.await_args.kwargs["view"]
    await checkout._pay(make_interaction(member))
    member.add_roles.assert_awaited_once()

    backpack = await cog.backpack_view(guild, member, member)
    assert isinstance(backpack, Backpack)
    assert any(isinstance(c, ui.Select) for c in backpack.walk_children())
    await cog.toggle_role(make_interaction(member), backpack, backpack.entries[0].id)
    member.remove_roles.assert_awaited_once()
    assert not backpack.entries[0].equipped


async def test_no_se_vende_un_rol_que_ya_llevas_de_fuera(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    role = FakeRole(555, 10)
    guild = make_guild(role)
    it = await cog.repository.create_item(
        GUILD, kind=Kind.ROLE, name="VIP", emoji="🎭", description="", price=100,
        igic="general", now=NOW, role_id=role.id, duration=DAY,
    )  # fmt: skip
    member = make_member(guild)
    member.roles = [role]
    opening = make_interaction(member)
    await cog.open_checkout(opening, it.id)
    assert "Ya llevas ese rol" in opening.edit_original_response.await_args.kwargs["content"]


async def test_potenciador_multiplica_el_xp_y_caduca(tmp_path: Path) -> None:
    clock = Clock()
    cog = await make_cog(tmp_path, clock)
    it = await cog.repository.create_item(
        GUILD, kind=Kind.BOOST, name="Turbo", emoji="⚡", description="", price=100,
        igic="general", now=NOW, duration=3600, multiplier=200,
    )  # fmt: skip
    guild = make_guild()
    member = make_member(guild)
    opening = make_interaction(member)
    await cog.open_checkout(opening, it.id)
    await opening.edit_original_response.await_args.kwargs["view"]._pay(make_interaction(member))
    assert cog.xp_multiplier(GUILD, BUYER, NOW + 10) == 2.0
    assert cog.xp_multiplier(GUILD, OTHER, NOW + 10) == 1.0

    bot = MagicMock()
    bot.get_cog.return_value = cog
    assert shop_cog.xp_multiplier(bot, GUILD, BUYER, NOW + 20) == 2.0
    bot.get_cog.return_value = None
    assert shop_cog.xp_multiplier(bot, GUILD, BUYER, NOW + 20) == 1.0

    assert cog.xp_multiplier(GUILD, BUYER, NOW + 3600) == 1.0


async def test_potenciadores_se_recuperan_al_arrancar(tmp_path: Path) -> None:
    clock = Clock()
    economy, shop = await make_stores(tmp_path, clock)
    it = await shop.create_item(
        GUILD, kind=Kind.BOOST, name="Turbo", emoji="⚡", description="", price=100,
        igic="general", now=NOW, duration=3600, multiplier=150,
    )  # fmt: skip
    await buy(economy, shop, it)
    cog = Tienda(MagicMock(), economy, shop, clock=clock)
    cog._expire = MagicMock()  # sin bucle real en las pruebas
    await cog.cog_load()
    assert cog.xp_multiplier(GUILD, BUYER, NOW + 1) == 1.5


async def test_caducidad_quita_el_rol_alquilado(tmp_path: Path) -> None:
    clock = Clock()
    cog = await make_cog(tmp_path, clock)
    role = FakeRole(555, 10)
    guild = make_guild(role)
    member = make_member(guild)
    member.roles = [role]
    guild.get_member = lambda user_id: member if user_id == BUYER else None
    cog.bot.get_guild = lambda guild_id: guild if guild_id == GUILD else None
    it = await cog.repository.create_item(
        GUILD, kind=Kind.ROLE, name="VIP", emoji="🎭", description="", price=100,
        igic="general", now=NOW, role_id=role.id, duration=DAY,
    )  # fmt: skip
    await buy(cog.economy, cog.repository, it)
    clock.now = NOW + DAY + 1
    assert await cog.expire_rentals() == 1
    member.remove_roles.assert_awaited_once()
    assert await cog.expire_rentals() == 0


async def test_trastienda_y_editor_caben_y_solo_para_admins(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    guild = make_guild(FakeRole(555, 10))
    for n in range(6):
        await cog.repository.create_item(
            GUILD, kind=Kind.ROLE, name=f"Rol {n}", emoji="🎭", description="d", price=100,
            igic="general", now=NOW, role_id=555, duration=DAY,
        )  # fmt: skip
    admin = make_member(guild)
    panel = AdminPanel(cog, guild, admin)
    await panel.load()
    assert panel.total_children_count <= 40
    assert panel.pages == 2
    assert await panel.interaction_check(make_interaction(admin))
    intruder = make_member(guild, OTHER)
    intruder.guild_permissions.administrator = False
    assert not await panel.interaction_check(make_interaction(intruder))

    first = panel.items_all[0]
    editor = ItemEditor(panel, first)
    assert editor.total_children_count <= 40
    await editor._save(make_interaction(admin), "ok", igic="lujo", discount=30)
    assert editor.item.igic_key == "lujo" and editor.item.discount == 30
    assert "🏷️ -30 %" in admin_card(editor.item, guild, NOW)


async def test_formulario_de_coleccionable_crea_el_articulo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    guild = make_guild()
    admin = make_member(guild)
    panel = AdminPanel(cog, guild, admin)
    await panel.load()
    opening = make_interaction(admin)
    await panel._new_trophy(opening)
    form = opening.response.send_modal.await_args.args[0]
    await form.handler(
        make_interaction(admin),
        {"name": "🛥️ Yate de Perro Sanxe", "price": "50k", "stock": "10", "description": "Ñam"},
    )
    (created,) = await cog.repository.items(GUILD)
    assert (created.emoji, created.name, created.price, created.stock) == (
        "🛥️",
        "Yate de Perro Sanxe",
        50_000,
        10,
    )


async def test_formulario_con_error_lo_explica(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock())
    guild = make_guild()
    admin = make_member(guild)
    panel = AdminPanel(cog, guild, admin)
    await panel.load()
    opening = make_interaction(admin)
    await panel._new_boost(opening)
    form = opening.response.send_modal.await_args.args[0]
    for key, value in {
        "name": "Turbo",
        "price": "1k",
        "multiplier": "x9",
        "duration": "2h",
        "description": "",
    }.items():
        form.inputs[key]._value = value
    submit = make_interaction(admin)
    await form.on_submit(submit)
    assert "multiplicador" in submit.followup.send.await_args.args[0]
    assert await cog.repository.items(GUILD) == []


# -- Títulos ---------------------------------------------------------------------------

#: Dos títulos del surtido, baratos para no tener que dar dinero en cada prueba.
TITLES = (
    replace(CATALOG_BY_KEY["verificado"], price=100),
    replace(CATALOG_BY_KEY["carne_partido"], price=100),
)


async def pay_for(cog: Tienda, guild: MagicMock, member: MagicMock, key: str) -> MagicMock:
    """Compra un artículo del surtido por la caja y devuelve la interacción de pagar."""
    await cog.stock_up(GUILD)
    it = await cog.repository.item_by_key(GUILD, key)
    assert it is not None
    opening = make_interaction(member)
    await cog.open_checkout(opening, it.id)
    paying = make_interaction(member)
    await opening.edit_original_response.await_args.kwargs["view"]._pay(paying)
    return paying


def title_select(view: Backpack) -> ui.Select:
    return next(
        c for c in view.walk_children()
        if isinstance(c, ui.Select) and c.placeholder == "🎖️ Qué título luces"
    )  # fmt: skip


async def test_comprar_un_titulo_te_lo_pone_y_lo_anuncia(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock(), catalog=TITLES)
    guild = make_guild()
    member = make_member(guild)
    paying = await pay_for(cog, guild, member, "verificado")
    assert cog.title(GUILD, BUYER) == "☑️ Cuenta verificada"
    assert "☑️ **Cuenta verificada**" in paying.followup.send.await_args.args[0]
    assert not paying.followup.send.await_args.args[0].startswith("📣"), "100 Y$ no es gorda"

    bot = MagicMock()
    bot.get_cog.return_value = cog
    assert shop_cog.title_of(bot, GUILD, BUYER) == "☑️ Cuenta verificada"
    assert shop_cog.title_of(bot, GUILD, OTHER) is None
    bot.get_cog.return_value = None
    assert shop_cog.title_of(bot, GUILD, BUYER) is None


async def test_el_ultimo_titulo_comprado_es_el_que_se_luce(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock(), catalog=TITLES)
    guild = make_guild()
    member = make_member(guild)
    await pay_for(cog, guild, member, "verificado")
    await pay_for(cog, guild, member, "carne_partido")
    assert cog.title(GUILD, BUYER) == "🪪 Carné del partido"
    entries = await cog.repository.inventory(GUILD, BUYER, NOW)
    assert [e.catalog_key for e in entries if e.equipped] == ["carne_partido"]


async def test_cambiar_y_quitarse_el_titulo_desde_la_mochila(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock(), catalog=TITLES)
    guild = make_guild()
    member = make_member(guild)
    await pay_for(cog, guild, member, "verificado")
    await pay_for(cog, guild, member, "carne_partido")
    backpack = await cog.backpack_view(guild, member, member)
    select = title_select(backpack)
    assert [o.value for o in select.options if o.default] == ["carne_partido"]
    assert "lo luces" in texts(backpack)

    await cog.choose_title(make_interaction(member), backpack, "verificado")
    assert cog.title(GUILD, BUYER) == "☑️ Cuenta verificada"
    assert [o.value for o in title_select(backpack).options if o.default] == ["verificado"]

    await cog.choose_title(make_interaction(member), backpack, shop_cog.NO_TITLE)
    assert cog.title(GUILD, BUYER) is None
    entries = await cog.repository.inventory(GUILD, BUYER, NOW)
    assert not any(e.equipped for e in entries), "sin título, ninguno queda marcado"


async def test_la_mochila_ajena_no_deja_cambiar_el_titulo(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock(), catalog=TITLES)
    guild = make_guild()
    member = make_member(guild)
    await pay_for(cog, guild, member, "verificado")
    other = await cog.backpack_view(guild, member, make_member(guild, OTHER))
    assert not any(
        isinstance(c, ui.Select) and c.placeholder == "🎖️ Qué título luces"
        for c in other.walk_children()
    )


async def test_los_titulos_se_recuperan_al_arrancar(tmp_path: Path) -> None:
    clock = Clock()
    cog = await make_cog(tmp_path, clock, catalog=TITLES)
    guild = make_guild()
    await pay_for(cog, guild, make_member(guild), "verificado")
    fresh = Tienda(MagicMock(), cog.economy, cog.repository, clock=clock)
    fresh._expire = MagicMock()  # sin bucle real en las pruebas
    await fresh.cog_load()
    assert fresh.title(GUILD, BUYER) == "☑️ Cuenta verificada"


async def test_una_compra_gorda_se_anuncia_con_pregon(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock(), catalog=(CATALOG_BY_KEY["moto_agua"],))
    await cog.economy.repository.apply(GUILD, BUYER, [LedgerEntry(200_000, "test")])
    guild = make_guild()
    member = make_member(guild)
    cog.member_level = AsyncMock(return_value=99)  # type: ignore[method-assign]
    paying = await pay_for(cog, guild, member, "moto_agua")
    announcement = paying.followup.send.await_args.args[0]
    assert announcement.startswith("📣") and "Moto de agua" in announcement
    assert cog.title(GUILD, BUYER) is None, "un capricho no es un título"


async def test_la_caja_botin_no_regala_titulos(tmp_path: Path) -> None:
    cog = await make_cog(tmp_path, Clock(), catalog=(*TITLES, CATALOG_BY_KEY["caja_botin"]))
    await cog.stock_up(GUILD)
    box = await cog.repository.item_by_key(GUILD, "caja_botin")
    assert box is not None
    entry = await cog.repository.grant(GUILD, BUYER, box, NOW)
    assert await cog._open_mystery(GUILD, BUYER, "Diego", entry[0], NOW) is None
