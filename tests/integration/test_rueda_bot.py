"""Pruebas de la Rueda (`rueda`) con el bot real: base de datos, economía, tienda y logros.

El dibujo es un doble (la escena de verdad se prueba en `tests/unit/test_rueda_scene.py`):
aquí se mira qué se cobra, qué llega a la mochila, qué se apunta en la Renta y que solo
haya una vuelta al día. Cada prueba fuerza lo que toca guardando la vuelta del día antes
de abrir el panel, como haría el sorteo.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from interaction_fakes import fake_interaction
from test_button_speed import GUILD_ID, OWNER_ID, load_bot, make_owner, module_of

from bot.app import BotClient
from bot.services.economy import STATE_ACCOUNT_ID
from bot.services.rueda import SHOWCASE_FALLBACK, Draw, day_of, layout
from bot.services.rueda_catalog import PIECES, TITLE_KEYS, TITLE_STOCK


def _wheel(client: BotClient):  # noqa: ANN202
    cog = client.get_cog("Rueda")
    module = module_of(client, "Rueda")
    module.REVEAL_MARGIN_SECONDS = 0
    module.REVEAL_PER_MB = 0
    cog.renderer = MagicMock()
    cog.renderer.spin = AsyncMock(return_value=module.Media(gif=b"GIF", png=b"PNG", seconds=0.0))
    cog.renderer.board = AsyncMock(return_value=b"PNG")
    cog.renderer.close = AsyncMock()
    cog.ahead = False
    return cog


async def _force(client: BotClient, prize: str, *, item: str | None = None, spin: int = 0) -> None:
    """Guarda como vuelta de hoy la primera porción con `prize`."""
    cog = client.get_cog("Rueda")
    wedge = next(w for w in layout(levels=False) if w.prize.key == prize)
    today = day_of(cog.clock())
    await client.rueda.create(
        GUILD_ID, OWNER_ID, today, spin, Draw(wedge.index, prize, 0.5, 1234, item), cog.clock()
    )


async def _spin(client: BotClient, owner: MagicMock):  # noqa: ANN202
    cog = client.get_cog("Rueda")
    view, _ = await cog.open_panel(owner.guild, None, owner)
    interaction = fake_interaction(owner)
    await view._spin(interaction)
    return view, interaction


def _ledger(client: BotClient, user_id: int = OWNER_ID) -> dict[str, int]:
    """Lo movido por la Rueda en el libro de un miembro, por motivo (sin los logros)."""
    rows = _rows(
        client,
        "SELECT reason, SUM(delta) AS total FROM economy_ledger "
        "WHERE guild_id = ? AND user_id = ? AND reason LIKE '%rueda%' GROUP BY reason",
        GUILD_ID,
        user_id,
    )
    return {r["reason"]: int(r["total"]) for r in rows}


def _rows(client: BotClient, sql: str, *args: object) -> list[sqlite3.Row]:
    connection = sqlite3.connect(client.database_path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(sql, args).fetchall()
    finally:
        connection.close()


async def test_el_dinero_se_paga_con_su_irpf_y_solo_hay_una_vuelta(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    await client.rueda.initialize()
    _wheel(client)
    owner = make_owner()
    await _force(client, "y2500")
    state_before = await client.economy.balance(GUILD_ID, STATE_ACCOUNT_ID)

    view, interaction = await _spin(client, owner)

    moved = _ledger(client)
    withheld = -moved.get("irpf:rueda:premio", 0)
    assert moved["rueda:premio"] == 2_500
    assert 0 <= withheld < 2_500
    state_after = await client.economy.balance(GUILD_ID, STATE_ACCOUNT_ID)
    assert state_after - state_before >= withheld, "lo retenido va al Estado"
    assert "2.500 Y$" in view.text
    assert "IRPF" in view.text or "Sin retención" in view.text
    assert view.pending is None and view.spin_button.disabled
    # Otra vez: ya no hay vuelta pendiente y no se cobra nada más.
    view2, _ = await client.get_cog("Rueda").open_panel(owner.guild, None, owner)
    assert view2.pending is None
    await view2._spin(fake_interaction(owner))
    assert _ledger(client) == moved
    await client.close()


async def test_dos_paneles_a_la_vez_solo_cobran_una_vez(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    await client.rueda.initialize()
    cog = _wheel(client)
    owner = make_owner()
    await _force(client, "y1000")
    first, _ = await cog.open_panel(owner.guild, None, owner)
    second, _ = await cog.open_panel(owner.guild, None, owner)
    await first._spin(fake_interaction(owner))
    await second._spin(fake_interaction(owner))
    assert _ledger(client)["rueda:premio"] == 1_000
    await client.close()


async def test_otra_vuelta_deja_girar_otra_vez_el_mismo_dia(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    await client.rueda.initialize()
    _wheel(client)
    owner = make_owner()
    await _force(client, "otra")
    view, _ = await _spin(client, owner)
    assert view.pending is not None and view.pending.spin == 1
    assert view.spin_button.label == "Otra vuelta" and not view.spin_button.disabled
    rows = await client.rueda.day(GUILD_ID, OWNER_ID, view.pending.day)
    assert [r.spin for r in rows] == [0, 1] and rows[0].claimed and not rows[1].claimed
    await client.close()


async def test_inspeccion_no_da_nada(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    await client.rueda.initialize()
    _wheel(client)
    owner = make_owner()
    await _force(client, "nada")
    view, _ = await _spin(client, owner)
    assert _ledger(client) == {}
    assert "Inspección" in view.text
    await client.close()


async def test_la_pieza_va_a_la_mochila_y_cuenta_en_la_renta(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    await client.rueda.initialize()
    _wheel(client)
    owner = make_owner()
    piece = PIECES[6]
    await _force(client, "pieza", item=piece.key)
    view, _ = await _spin(client, owner)
    owned = await client.get_cog("Tienda").owned_keys(GUILD_ID, OWNER_ID)
    assert piece.key in owned
    assert _ledger(client) == {}, "en especie no se cobra"
    rows = _rows(
        client,
        "SELECT gross, withheld, kind FROM economy_tax_records WHERE concept = 'rueda:especie'",
    )
    assert [(r["gross"], r["withheld"], r["kind"]) for r in rows] == [(piece.price, 0, "premio")]
    assert piece.name in view.text
    await client.close()


async def test_la_caja_va_a_la_mochila(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    await client.rueda.initialize()
    _wheel(client)
    owner = make_owner()
    await _force(client, "regalo")
    await _spin(client, owner)
    assert "caja_regalo" in await client.get_cog("Tienda").owned_keys(GUILD_ID, OWNER_ID)
    await client.close()


async def test_el_expositor_da_un_titulo_numerado_y_si_ya_lo_tienes_paga_dinero(
    tmp_path: Path,
) -> None:
    client = await load_bot(tmp_path)
    await client.rueda.initialize()
    cog = _wheel(client)
    owner = make_owner()
    key = TITLE_KEYS[0]
    await _force(client, "expositor", item=key)
    view, _ = await _spin(client, owner)
    entries = await client.shop.inventory(GUILD_ID, OWNER_ID, cog.clock())
    titles = [e for e in entries if e.catalog_key == key]
    assert len(titles) == 1 and titles[0].serial == 1 and titles[0].edition == TITLE_STOCK
    assert "n.º 1 de 10" in view.text
    left = await client.get_cog("Tienda").units_left(GUILD_ID, [key])
    assert left[key] == TITLE_STOCK - 1

    # Mañana le vuelve a tocar el mismo título: ya lo tiene, así que cobra en dinero.
    cog.clock = lambda: time.time() + 86_400
    tomorrow = day_of(cog.clock())
    wedge = next(w for w in layout(levels=False) if w.prize.key == "expositor")
    await client.rueda.create(
        GUILD_ID, OWNER_ID, tomorrow, 0, Draw(wedge.index, "expositor", 0.5, 9, key), cog.clock()
    )
    assert _ledger(client) == {}
    view2, _ = await _spin(client, owner)
    assert _ledger(client)["rueda:premio"] == SHOWCASE_FALLBACK
    assert "ya no estaba" in view2.text
    rows = await client.rueda.day(GUILD_ID, OWNER_ID, tomorrow)
    assert rows[0].draw.prize == "y15000", "la vuelta queda apuntada con lo que se dio"
    await client.close()


async def test_los_logros_de_la_rueda_se_alimentan(tmp_path: Path) -> None:
    client = await load_bot(tmp_path)
    await client.rueda.initialize()
    _wheel(client)
    owner = make_owner()
    await _force(client, "y300")
    await _spin(client, owner)
    stats = (await client.achievements.profile(GUILD_ID, OWNER_ID)).stats
    assert stats.get("rueda_spins") == 1
    assert stats.get("rueda_prize_y300") == 1
    assert stats.get("rueda_money_total") == 300
    assert stats.get("rueda_streak_max") == 1
    await client.close()
