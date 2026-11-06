"""Pruebas del cog `casa`: el panel con un repositorio falso (sin base de datos ni dibujo)."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs import casa as casa_cog
from bot.cogs.casa import Casa, home_text
from bot.repositories.housing import HomeStatus
from bot.services import home
from bot.services.economy import format_amount

GUILD_ID = 1
TODAY = date(2026, 11, 6)


def place(m2: int = 60, energy: str = "D", reforms: int = 0, theme: str = "centro",
          kind: str = home.OWN) -> home.Place:  # fmt: skip
    return home.Place(kind, "k", "Piso en Vegueta", "piso", m2, energy, reforms, theme, "2.º")


def status(p: home.Place | None = None, cut: date | None = None) -> HomeStatus:
    p = p or place()
    return HomeStatus(p, home.comfort(p), cut)


class FakeRepo:
    def __init__(self, result: HomeStatus | None = None, error: Exception | None = None) -> None:
        self.result, self.error = result, error

    async def home_status(self, guild_id: int, user_id: int) -> HomeStatus | None:
        if self.error:
            raise self.error
        return self.result


def member(user_id: int = 10, name: str = "Ana") -> MagicMock:
    m = MagicMock(spec=discord.Member)
    m.id, m.display_name = user_id, name
    return m


def text(embed: discord.Embed) -> str:
    return "\n".join(f"{f.name}\n{f.value}" for f in embed.fields)


async def test_quien_vive_con_sus_padres_ve_como_emanciparse() -> None:
    embed = await Casa(MagicMock(), FakeRepo(None)).home_embed(GUILD_ID, member())
    assert embed.title == "🏡 Casa de Ana"
    assert "padres" in text(embed)
    assert "Vivir aquí" in text(embed)
    assert "Equipamiento" not in text(embed)


async def test_el_panel_de_una_casa_enseña_tramo_pilares_y_suministros() -> None:
    st = status()
    embed = await Casa(MagicMock(), FakeRepo(st)).home_embed(GUILD_ID, member())
    body = text(embed)
    assert f"**{st.comfort.total}/100**" in body
    assert st.comfort.tier.name in body
    for pillar in ("Espacio", "Calidad", "Equipamiento", "Entorno"):
        assert pillar in body
    assert "llega con los muebles del hogar" in body
    assert format_amount(st.bill.total) in body
    assert "Tu casa" in body


@pytest.mark.parametrize(("kind", "label"), [(home.VPO, "Tu VPO"), (home.RENTED, "De alquiler")])
async def test_el_panel_dice_si_es_vpo_o_alquiler(kind: str, label: str) -> None:
    embed = await Casa(MagicMock(), FakeRepo(status(place(kind=kind)))).home_embed(
        GUILD_ID, member()
    )
    assert label in text(embed)


def test_la_linea_de_corte_solo_sale_si_estan_cortados() -> None:
    assert "cortado" not in text_of(status(), TODAY)
    assert "cortado" in text_of(status(cut=TODAY), TODAY)
    assert "cortado" not in text_of(status(cut=TODAY - timedelta(days=1)), TODAY)


def text_of(st: HomeStatus, today: date) -> str:
    return "\n".join(v for _n, v in home_text(st, today))


def test_la_pista_apunta_al_pilar_mas_lejos_de_su_maximo() -> None:
    # Un estudio con certificado A y reformas: la calidad casi llena y el espacio, no.
    small = status(place(m2=25, energy="A", reforms=3, theme="lujo"))
    assert "espacio" in text_of(small, TODAY)
    # Una casona en un barrio obrero y sin reformas: lo que falta es calidad o entorno.
    big = status(place(m2=300, energy="G", reforms=0, theme="obrero"))
    assert "calidad" in text_of(big, TODAY)
    maxed = HomeStatus(place(), home.Comfort(40, 25, 0, 10))
    assert "al máximo" in text_of(maxed, TODAY)


async def test_un_error_de_base_de_datos_da_un_aviso_claro() -> None:
    repo = FakeRepo(error=sqlite3.OperationalError("locked"))
    embed = await Casa(MagicMock(), repo).home_embed(GUILD_ID, member())
    assert "No he podido leer la casa" in embed.description
    assert not embed.fields


@pytest.mark.parametrize("target_id", [10, 11])
async def test_el_comando_apunta_los_logros_segun_sea_la_casa_propia_o_ajena(
    target_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = MagicMock()
    monkeypatch.setattr(casa_cog.logros, "note", note)
    monkeypatch.setattr(casa_cog.mascotas, "cameo", AsyncMock(return_value="🐱 miau"))
    bot = MagicMock()
    responder = MagicMock()
    responder.guild = MagicMock(id=GUILD_ID)
    responder.member = member(10)
    responder.finish = AsyncMock()
    await Casa(bot, FakeRepo(status()))._casa_impl(responder, member(target_id, "Otro"))
    delta = note.call_args.args[3]
    assert ("casa_open" in delta.add) == (target_id == 10)
    assert ("casa_look" in delta.add) == (target_id != 10)
    embed = responder.finish.call_args.kwargs["embed"]
    # La mascota solo comenta en la casa propia.
    assert (embed.footer.text == "🐱 miau") == (target_id == 10)
