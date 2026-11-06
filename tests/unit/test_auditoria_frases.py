"""La auditoría de frases (`docs/auditoria_frases.py`) pasa con las frases del bot.

Así una lista nueva con una coletilla en cada frase, o una broma copiada de otro
juego, se ve en las pruebas y no en Discord. El script vive en `docs/` (no es
paquete), así que se carga por ruta.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "docs" / "auditoria_frases.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("auditoria_frases", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # `dataclass` busca el módulo en `sys.modules` al crear la clase.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


af = _load()


# Recorrer todo el código tarda ~10 s: una sola pasada para todo lo que mira el código real.
@pytest.mark.lenta
def test_las_frases_del_bot_pasan_la_auditoria(capsys) -> None:  # noqa: ANN001
    lists, sources = af.collect()
    places = {pl.where for pl in lists}
    assert "cogs/errors:EXPIRED_CLICK" in places
    assert "services/pets:CARE_LINES[Care.PET]" in places
    assert sources["Netanyahu (Bibi)"] > 0 and "Groypers (Nick Fuentes)" in sources

    rows = af.part_table(lists, ["cogs/slots"])
    assert all("cogs/blackjack" not in row for row in rows)
    assert any(row.startswith("\nTotal:") for row in rows)

    # Incluye lo boricua fuera del contenido (`af.boricua_texts`).
    warnings = af.report(lists, sources)
    capsys.readouterr()
    assert warnings == []


def test_cuenta_las_coletillas_de_jovani() -> None:
    assert af.catchphrases_in("¿Qué? ¿Qué? ¡Toma mangó!") == ["Toma mangó"]
    assert af.catchphrases_in("Hazme café… ¡Quiero café!") == ["Quiero café"]
    assert af.catchphrases_in("La bola no quiso.") == []


def test_reconoce_las_familias_de_temas_y_las_frases_normales() -> None:
    assert af.families_in("La banca pide el rescate.") == ["Política"]
    assert "Cine y series" in af.families_in("Te han hecho un Ocean's Eleven.")
    assert af.is_plain("La banca gana.")
    assert not af.is_plain("¿Con pulpa? ¡Sin pulpa!")
    assert not af.is_plain("Te pasas como un arancel de Trump.")
    assert not af.is_plain("¡Eso es, mi amor!")


#: Frases hechas o de Jovani que llevan «como» y no son la muleta de la Biblia.
COMO_IDIOMS = {"¡Azul, azul, como el mar azul!", "Al amanecer, como Dios manda"}


def test_ningun_nombre_de_logro_usa_la_muleta_como_referencia() -> None:
    """Biblia, «Logros»: nada de «X, como [referencia]» en los nombres."""
    import re

    from bot.services.achievements import AVAILABLE

    bad = [
        a.name for a in AVAILABLE if re.search(af.COMPARISON, a.name) and a.name not in COMO_IDIOMS
    ]
    assert bad == []


def test_la_vigilancia_de_lo_boricua_salta_con_un_texto_nuevo(tmp_path) -> None:  # noqa: ANN001
    (tmp_path / "cogs").mkdir()
    # En UTF-8 a la fuerza: en Windows `write_text` escribe en cp1252 y el script lee UTF-8.
    (tmp_path / "cogs" / "nuevo.py").write_text(
        'LINES = ("¡Wepa! Ganas.", "La banca gana.")\n', encoding="utf-8"
    )
    (tmp_path / "cogs" / "beernight.py").write_text('X = "Quien diga «wepa»."\n', encoding="utf-8")
    assert af.boricua_texts(tmp_path) == ["cogs/nuevo: ¡Wepa! Ganas."]
