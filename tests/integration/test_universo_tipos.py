"""El TypeScript del Jovaniverso compila: `tsc --noEmit` en `universo/`.

Solo `docker build` ejecutaba `tsc` (`npm run build`), y un error de tipos rompe la imagen
del NAS: `actualizar.sh` vuelve a la anterior sin avisar y no se despliega nada, tampoco lo
de Python. Necesita Node y `universo/node_modules` (`npm ci`); sin ellos, se salta.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

UNIVERSO = Path(__file__).resolve().parents[2] / "universo"
TSC = UNIVERSO / "node_modules" / "typescript" / "bin" / "tsc"

pytestmark = pytest.mark.lenta


def test_el_typescript_del_jovaniverso_no_tiene_errores_de_tipos() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("no hay Node en el PATH")
    if not TSC.is_file():
        pytest.skip("falta universo/node_modules (npm ci en universo/)")
    # `-p` lee el tsconfig de `universo/`, que ya lleva `noEmit`: no pisa nada compilado.
    done = subprocess.run(
        [node, str(TSC), "--noEmit", "-p", str(UNIVERSO)],
        cwd=UNIVERSO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    assert done.returncode == 0, done.stdout + done.stderr
