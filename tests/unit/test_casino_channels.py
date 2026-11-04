"""Pruebas de los canales del casino por servidor."""

from __future__ import annotations

from pathlib import Path

from bot.repositories.casino_settings import CasinoSettingsRepository
from bot.services.casino_channels import CasinoChannels, allowed_ids

GUILD = 1
OTHER = 2


async def make(tmp_path: Path, default: frozenset[int] = frozenset()) -> CasinoChannels:
    repository = CasinoSettingsRepository(tmp_path / "bot.db")
    await repository.initialize()
    channels = CasinoChannels(repository, default)
    await channels.load()
    return channels


async def test_sin_fila_usa_el_valor_por_defecto(tmp_path: Path) -> None:
    channels = await make(tmp_path, frozenset({7}))

    assert channels.ids(GUILD) == frozenset({7})
    assert channels.ids(None) == frozenset({7})
    assert not channels.configured(GUILD)


async def test_toggle_anade_y_quita_un_canal(tmp_path: Path) -> None:
    channels = await make(tmp_path)

    assert await channels.toggle(GUILD, 10) == (True, frozenset({10}))
    assert await channels.toggle(GUILD, 11) == (True, frozenset({10, 11}))
    assert await channels.toggle(GUILD, 10) == (False, frozenset({11}))
    assert channels.configured(GUILD)


async def test_toggle_parte_del_valor_por_defecto(tmp_path: Path) -> None:
    channels = await make(tmp_path, frozenset({7}))

    assert await channels.toggle(GUILD, 8) == (True, frozenset({7, 8}))


async def test_quitar_el_ultimo_canal_deja_cualquier_canal(tmp_path: Path) -> None:
    channels = await make(tmp_path, frozenset({7}))
    await channels.toggle(GUILD, 7)

    assert channels.ids(GUILD) == frozenset()
    assert channels.configured(GUILD)


async def test_clear_anula_el_valor_por_defecto(tmp_path: Path) -> None:
    channels = await make(tmp_path, frozenset({7}))
    await channels.clear(GUILD)

    assert channels.ids(GUILD) == frozenset()


async def test_forget_vuelve_al_valor_por_defecto(tmp_path: Path) -> None:
    channels = await make(tmp_path, frozenset({7}))
    await channels.clear(GUILD)
    await channels.forget(GUILD)

    assert channels.ids(GUILD) == frozenset({7})
    assert not channels.configured(GUILD)
    nuevo = await make(tmp_path, frozenset({7}))
    assert not nuevo.configured(GUILD)


async def test_lo_guardado_sobrevive_a_un_servicio_nuevo(tmp_path: Path) -> None:
    channels = await make(tmp_path)
    await channels.toggle(GUILD, 10)
    await channels.toggle(GUILD, 11)
    await channels.clear(OTHER)

    nuevo = await make(tmp_path, frozenset({7}))

    assert nuevo.ids(GUILD) == frozenset({10, 11})
    assert nuevo.ids(OTHER) == frozenset()


async def test_los_servidores_no_se_mezclan(tmp_path: Path) -> None:
    channels = await make(tmp_path)
    await channels.toggle(GUILD, 10)
    await channels.toggle(OTHER, 20)

    assert channels.ids(GUILD) == frozenset({10})
    assert channels.ids(OTHER) == frozenset({20})


async def test_allowed_ids_con_conjunto_y_con_servicio(tmp_path: Path) -> None:
    channels = await make(tmp_path)
    await channels.toggle(GUILD, 10)

    assert allowed_ids(frozenset({1, 2}), GUILD) == frozenset({1, 2})
    assert allowed_ids(channels, GUILD) == frozenset({10})
    assert allowed_ids(channels, None) == frozenset()


async def test_dos_toggles_seguidos_ven_el_estado_nuevo(tmp_path: Path) -> None:
    channels = await make(tmp_path)
    await channels.toggle(GUILD, 5)

    assert (await channels.toggle(GUILD, 5))[0] is False
