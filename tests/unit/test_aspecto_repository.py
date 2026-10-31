"""Pruebas del repositorio y del almacén de imágenes del aspecto."""

from __future__ import annotations

from pathlib import Path

import pytest

from bot.repositories.aspecto import AspectoRepository, LookImageStore
from bot.services.aspecto import MAX_CUSTOM_LOOKS, AspectoError, AspectoState, LookOverride

GUILD = 111
OTHER = 222


@pytest.fixture
async def repo(tmp_path: Path) -> AspectoRepository:
    repository = AspectoRepository(tmp_path / "bot.sqlite3")
    await repository.initialize()
    return repository


async def test_sin_fila_el_estado_es_el_de_serie(repo: AspectoRepository) -> None:
    assert await repo.state(GUILD) == AspectoState()


async def test_el_estado_vuelve_completo_tras_guardarlo(repo: AspectoRepository) -> None:
    state = AspectoState(
        enabled=False,
        pinned_key="casino",
        pinned_until=1234.5,
        dragon_until=10.5,
        updated_until=20.5,
        last_version="9.9.9",
        applied_key="normal",
        applied_avatar="abc123",
        profile_at=30.5,
        refreshed_day="2026-10-31",
        blocked_until=40.5,
        last_error="Discord dijo que no",
    )
    await repo.save_state(GUILD, state)
    assert await repo.state(GUILD) == state
    changed = AspectoState(enabled=True, pinned_key=None)
    await repo.save_state(GUILD, changed)
    assert await repo.state(GUILD) == changed


async def test_crear_un_look_lo_deja_en_los_cambios(repo: AspectoRepository) -> None:
    look = LookOverride("playa", "Playa", "bio", "estado")
    await repo.create_look(GUILD, look)
    assert await repo.overrides(GUILD) == {"playa": look}


async def test_crear_un_look_repetido_da_error(repo: AspectoRepository) -> None:
    await repo.create_look(GUILD, LookOverride("playa", "Playa"))
    with pytest.raises(AspectoError, match="Ya existe un look con ese nombre."):
        await repo.create_look(GUILD, LookOverride("playa", "Otra"))


async def test_crear_un_look_con_la_clave_de_uno_de_serie_ajustado_da_error(
    repo: AspectoRepository,
) -> None:
    await repo.save_override(GUILD, LookOverride("casino", None, "bio", None))
    with pytest.raises(AspectoError, match="Ya existe"):
        await repo.create_look(GUILD, LookOverride("casino", "Mi casino"))


async def test_el_tope_de_looks_propios_se_respeta(repo: AspectoRepository) -> None:
    for number in range(MAX_CUSTOM_LOOKS):
        await repo.create_look(GUILD, LookOverride(f"look{number}", f"Look {number}"))
    with pytest.raises(AspectoError, match=f"Ya hay {MAX_CUSTOM_LOOKS} looks propios"):
        await repo.create_look(GUILD, LookOverride("uno-mas", "Uno más"))
    await repo.create_look(OTHER, LookOverride("uno-mas", "Uno más"))


async def test_los_ajustes_de_series_no_cuentan_para_el_tope(repo: AspectoRepository) -> None:
    for number in range(MAX_CUSTOM_LOOKS):
        await repo.save_override(GUILD, LookOverride(f"serie{number}", None, "bio", None))
    await repo.create_look(GUILD, LookOverride("propio", "Propio"))


async def test_guardar_un_ajuste_vacio_borra_la_fila(repo: AspectoRepository) -> None:
    await repo.save_override(GUILD, LookOverride("casino", None, "bio", None))
    assert "casino" in await repo.overrides(GUILD)
    await repo.save_override(GUILD, LookOverride("casino", None, "otra", "estado"))
    assert (await repo.overrides(GUILD))["casino"] == LookOverride("casino", None, "otra", "estado")
    await repo.save_override(GUILD, LookOverride("casino"))
    assert await repo.overrides(GUILD) == {}


async def test_borrar_un_look_quita_su_fila(repo: AspectoRepository) -> None:
    await repo.create_look(GUILD, LookOverride("playa", "Playa"))
    await repo.delete_look(GUILD, "playa")
    await repo.delete_look(GUILD, "no-existe")
    assert await repo.overrides(GUILD) == {}


async def test_sin_acumular_los_totales_estan_vacios(repo: AspectoRepository) -> None:
    assert await repo.totals(GUILD) == ({}, None)


async def test_los_totales_se_suman_y_guardan_el_dia(repo: AspectoRepository) -> None:
    await repo.add_totals(GUILD, "2026-10-30", {"commands": 5, "songs": 1})
    await repo.add_totals(GUILD, "2026-10-31", {"commands": 3, "drawings": 2})
    assert await repo.totals(GUILD) == (
        {"commands": 8, "songs": 1, "drawings": 2},
        "2026-10-31",
    )


@pytest.mark.parametrize("day", ["2026-10-31", "2026-10-30"])
async def test_un_dia_igual_o_anterior_no_se_suma_dos_veces(
    repo: AspectoRepository, day: str
) -> None:
    await repo.add_totals(GUILD, "2026-10-31", {"commands": 5})
    await repo.add_totals(GUILD, day, {"commands": 100})
    assert await repo.totals(GUILD) == ({"commands": 5}, "2026-10-31")


async def test_los_servidores_no_se_mezclan(repo: AspectoRepository) -> None:
    await repo.save_state(GUILD, AspectoState(pinned_key="casino"))
    await repo.create_look(GUILD, LookOverride("playa", "Playa"))
    await repo.add_totals(GUILD, "2026-10-31", {"commands": 5})
    assert await repo.state(OTHER) == AspectoState()
    assert await repo.overrides(OTHER) == {}
    assert await repo.totals(OTHER) == ({}, None)
    await repo.add_totals(OTHER, "2026-10-01", {"commands": 1})
    assert await repo.totals(GUILD) == ({"commands": 5}, "2026-10-31")


async def test_borrar_los_datos_de_un_servidor_deja_intacto_el_otro(
    repo: AspectoRepository,
) -> None:
    for guild in (GUILD, OTHER):
        await repo.save_state(guild, AspectoState(pinned_key="casino"))
        await repo.create_look(guild, LookOverride("playa", "Playa"))
        await repo.add_totals(guild, "2026-10-31", {"commands": 5})
    await repo.delete_guild_data(GUILD)
    assert await repo.state(GUILD) == AspectoState()
    assert await repo.overrides(GUILD) == {}
    assert await repo.totals(GUILD) == ({}, None)
    assert (await repo.state(OTHER)).pinned_key == "casino"
    assert "playa" in await repo.overrides(OTHER)
    assert await repo.totals(OTHER) == ({"commands": 5}, "2026-10-31")


# --- Almacén de imágenes ---


async def test_el_almacen_guarda_lee_lista_y_borra(tmp_path: Path) -> None:
    store = LookImageStore(tmp_path / "looks")
    assert await store.load(GUILD, "playa", "avatar") is None
    assert await store.kinds(GUILD, "playa") == set()
    await store.save(GUILD, "playa", "avatar", b"uno")
    await store.save(GUILD, "playa", "banner", b"dos")
    await store.save(GUILD, "playa", "avatar", b"tres")
    assert await store.load(GUILD, "playa", "avatar") == b"tres"
    assert await store.kinds(GUILD, "playa") == {"avatar", "banner"}
    assert (tmp_path / "looks" / str(GUILD) / "playa.banner.img").read_bytes() == b"dos"
    await store.delete(GUILD, "playa", "avatar")
    await store.delete(GUILD, "playa", "avatar")
    assert await store.kinds(GUILD, "playa") == {"banner"}
    await store.delete_look(GUILD, "playa")
    assert await store.kinds(GUILD, "playa") == set()
    assert sorted(path.name for path in (tmp_path / "looks" / str(GUILD)).iterdir()) == []


async def test_guardar_no_deja_temporales(tmp_path: Path) -> None:
    store = LookImageStore(tmp_path)
    await store.save(GUILD, "playa", "avatar", b"x")
    assert [path.name for path in (tmp_path / str(GUILD)).iterdir()] == ["playa.avatar.img"]


async def test_el_almacen_separa_servidores_y_borra_un_servidor(tmp_path: Path) -> None:
    store = LookImageStore(tmp_path)
    await store.save(GUILD, "playa", "avatar", b"a")
    await store.save(OTHER, "playa", "avatar", b"b")
    await store.delete_guild(GUILD)
    await store.delete_guild(GUILD)
    assert await store.load(GUILD, "playa", "avatar") is None
    assert await store.load(OTHER, "playa", "avatar") == b"b"


@pytest.mark.parametrize("key", ["../../x", "a/b", "a\\b", "", "..", "A", "x" * 40])
async def test_las_claves_peligrosas_no_tocan_el_disco(tmp_path: Path, key: str) -> None:
    base = tmp_path / "looks"
    store = LookImageStore(base)
    calls = [
        store.save(GUILD, key, "avatar", b"x"),
        store.load(GUILD, key, "avatar"),
        store.delete(GUILD, key, "avatar"),
        store.kinds(GUILD, key),
        store.delete_look(GUILD, key),
    ]
    for call in calls:
        with pytest.raises(ValueError, match="Clave"):
            await call
    assert not base.exists()
    assert list(tmp_path.rglob("*")) == []


async def test_un_tipo_de_imagen_desconocido_da_error(tmp_path: Path) -> None:
    store = LookImageStore(tmp_path / "looks")
    for call in (
        store.save(GUILD, "playa", "../x", b"x"),
        store.load(GUILD, "playa", "icono"),
        store.delete(GUILD, "playa", ""),
    ):
        with pytest.raises(ValueError, match="Tipo"):
            await call
    assert not (tmp_path / "looks").exists()


async def test_el_almacen_devuelve_identicos_unos_bytes_gif(tmp_path: Path) -> None:
    gif = bytes(range(256)) + b"GIF89a"
    store = LookImageStore(tmp_path)
    await store.save(GUILD, "playa", "avatar", gif)
    assert await store.load(GUILD, "playa", "avatar") == gif
