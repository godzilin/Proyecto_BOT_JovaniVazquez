"""El paquete que instala la imagen de Docker lleva todos los `assets/`.

El Dockerfile hace `pip install .` y borra `src/`: un archivo que no entre en el
paquete no existe en el NAS. Las escenas de Chromium que faltaban no daban
error, solo hacían que el juego se dibujara con Pillow, y el resto de pruebas
no lo veían porque leen de `src/`.

Los patrones de `[tool.setuptools.package-data]` se expanden igual que lo hace
setuptools (`glob` recursivo desde la carpeta del paquete), sin construir nada.
"""

from __future__ import annotations

import glob
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "bot"


def packaged_assets() -> set[str]:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = config["tool"]["setuptools"]["package-data"]["bot"]
    found: set[str] = set()
    for pattern in patterns:
        for name in glob.glob(pattern, root_dir=PACKAGE, recursive=True):
            if (PACKAGE / name).is_file():
                found.add(Path(name).as_posix())
    return found


def test_el_paquete_lleva_todos_los_assets() -> None:
    assets = PACKAGE / "assets"
    expected = {
        path.relative_to(PACKAGE).as_posix()
        for path in assets.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    assert expected - packaged_assets() == set()


def test_el_paquete_lleva_las_escenas_de_chromium() -> None:
    packaged = packaged_assets()
    for scene in sorted(PACKAGE.glob("assets/*/escena.html")):
        assert scene.relative_to(PACKAGE).as_posix() in packaged
    assert "assets/ruleta/escena.html" in packaged


def test_el_dockerfile_copia_los_json_que_importa_la_pagina_del_universo() -> None:
    """Un JSON de `src/bot/assets` que importa `universo/src` y no se copia rompe `docker build`."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    imported = {
        name
        for ts in (root / "universo" / "src").rglob("*.ts")
        for name in re.findall(
            r'from "[./]+/src/bot/assets/([\w./-]+\.json)"', ts.read_text("utf-8")
        )
    }
    assert imported, "ya no se importa ningún JSON: revisa el patrón"
    assert not {name for name in imported if f"src/bot/assets/{name}" not in dockerfile}
