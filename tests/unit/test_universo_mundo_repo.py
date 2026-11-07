"""Repositorio del mundo editable: borrador con `rev`, versiones numeradas y GLB."""

from __future__ import annotations

from pathlib import Path

import pytest

from bot.repositories.universo_mundo import KEEP_VERSIONS, UniversoMundoRepository


@pytest.fixture
async def repo(tmp_path: Path) -> UniversoMundoRepository:
    repository = UniversoMundoRepository(tmp_path / "bot.sqlite3")
    await repository.initialize()
    return repository


async def test_sin_borrador_no_hay_nada(repo: UniversoMundoRepository) -> None:
    assert await repo.draft(1) == (None, 0)
    assert await repo.published(1) is None
    assert await repo.versions(1) == []
    assert await repo.version_doc(1, 1) is None


async def test_el_rev_sube_y_un_rev_viejo_no_pisa(repo: UniversoMundoRepository) -> None:
    assert await repo.save_draft(1, "uno", 0, 10) == 1
    assert await repo.save_draft(1, "dos", 1, 11) == 2
    assert await repo.draft(1) == ("dos", 2)
    # Quien tenía el rev 1 llega tarde, y quien creía que no había borrador también.
    assert await repo.save_draft(1, "viejo", 1, 12) is None
    assert await repo.save_draft(1, "nuevo", 0, 12) is None
    assert await repo.draft(1) == ("dos", 2)
    # Cada servidor lleva el suyo.
    assert await repo.save_draft(2, "otro", 0, 10) == 1


async def test_publicar_numera_y_guarda_solo_las_ultimas(repo: UniversoMundoRepository) -> None:
    assert await repo.publish(1, 0, 10) is None  # sin borrador
    rev = await repo.save_draft(1, "doc", 0, 10)
    assert rev is not None
    assert await repo.publish(1, rev + 1, 10) is None  # rev que no es
    assert await repo.publish(1, rev, 10) == 1
    for n in range(2, KEEP_VERSIONS + 11):
        rev = await repo.save_draft(1, f"doc{n}", rev, 10)
        assert rev is not None
        assert await repo.publish(1, rev, 10 + n) == n
    assert await repo.published(1) == (KEEP_VERSIONS + 10, f"doc{KEEP_VERSIONS + 10}")
    versions = await repo.versions(1)
    assert [v[0] for v in versions] == list(range(KEEP_VERSIONS + 10, 10, -1))
    assert versions[0][1] == 10 + KEEP_VERSIONS + 10
    assert versions[0][3] == len(f"doc{KEEP_VERSIONS + 10}")
    assert await repo.version_doc(1, 10) is None
    assert await repo.version_doc(1, 11) == "doc11"
    assert await repo.published(2) is None


async def test_volver_es_guardar_una_version_como_borrador(repo: UniversoMundoRepository) -> None:
    rev = await repo.save_draft(1, "a", 0, 10)
    assert rev is not None
    assert await repo.publish(1, rev, 10) == 1
    rev = await repo.save_draft(1, "b", rev, 10)
    assert rev is not None
    old = await repo.version_doc(1, 1)
    assert old == "a"
    assert await repo.save_draft(1, old, rev, 10) == rev + 1
    assert await repo.draft(1) == ("a", rev + 1)


async def test_glb_por_contenido(repo: UniversoMundoRepository) -> None:
    assert await repo.glb("a" * 64) is None
    assert await repo.glb_triangles(set()) == {}
    await repo.put_glb("a" * 64, b"datos", 12, 10)
    await repo.put_glb("a" * 64, b"otros", 99, 11)  # ya estaba: no cambia
    await repo.put_glb("b" * 64, b"mas", 7, 10)
    assert await repo.glb("a" * 64) == b"datos"
    assert await repo.glb_triangles({"a" * 64, "b" * 64, "c" * 64}) == {"a" * 64: 12, "b" * 64: 7}
