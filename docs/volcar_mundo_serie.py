"""Vuelca el mundo de serie del Jovaniverso a JSON y comprueba que el servidor lo acepta.

El pueblo, los hitos y la vegetación de la isla son datos (`universo/src/mundo/serie.ts`,
`mundoDeSerie()`), en el formato que valida `bot.services.universo_editor`. Este script compila
ese módulo con `vite build --ssr` (en una carpeta temporal), lo ejecuta con `node`, pasa el
resultado por `validate_world` y cuenta cuánto pesa y qué lleva.

Uso (desde la raíz del repo, con `npm ci` hecho en universo/ y PYTHONPATH=src):

    python docs/volcar_mundo_serie.py [--salida mundo.json]

Sale con error si el mundo no vale o pesa más de `MAX_DOC_BYTES`. `volcar()` es lo que usa
`tests/integration/test_universo_mundo_serie.py`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from bot.services.universo_editor import (
    MAX_DOC_BYTES,
    asset_triangles,
    encode_world,
    validate_world,
)

RAIZ = Path(__file__).resolve().parent.parent
UNIVERSO = RAIZ / "universo"


def volcar() -> dict[str, Any]:
    """Compila `mundoDeSerie` con Vite (modo SSR), lo ejecuta con Node y devuelve el JSON.

    Raises:
        subprocess.CalledProcessError: Si falla la compilación o la ejecución.
    """
    with tempfile.TemporaryDirectory() as tmp:
        carpeta = Path(tmp)
        entrada = carpeta / "volcar.ts"
        serie = (UNIVERSO / "src/mundo/serie.ts").as_posix()
        entrada.write_text(
            f'import {{ mundoDeSerie }} from "{serie}";\n'
            "process.stdout.write(JSON.stringify(mundoDeSerie()));\n",
            encoding="utf-8",
        )
        # Una configuración sin imports (vive fuera de universo/) que empaqueta también three.
        config = carpeta / "vite.config.mjs"
        config.write_text(
            json.dumps(
                {
                    "root": UNIVERSO.as_posix(),
                    "logLevel": "error",
                    "ssr": {"noExternal": True},
                    "build": {
                        "ssr": entrada.as_posix(),
                        "outDir": (carpeta / "out").as_posix(),
                        "emptyOutDir": True,
                    },
                }
            ).join(["export default ", ";\n"]),
            encoding="utf-8",
        )
        vite = UNIVERSO / "node_modules/vite/bin/vite.js"
        subprocess.run(["node", str(vite), "build", "--config", str(config)], check=True)
        salida = subprocess.run(
            ["node", str(carpeta / "out/volcar.js")], check=True, capture_output=True, text=True
        )
    return json.loads(salida.stdout)


def resumen(doc: dict[str, Any]) -> dict[str, int]:
    """Peso en bytes y recuentos de un mundo ya validado."""
    piezas = sum(len(a.get("piezas", [])) for a in doc["assets"].values())
    triangulos = sum(asset_triangles(a, {}) for a in doc["assets"].values())
    return {
        "bytes": len(encode_world(doc).encode()),
        "assets": len(doc["assets"]),
        "objetos": len(doc["objetos"]),
        "piezas": piezas,
        "triangulos_de_assets": triangulos,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--salida", type=Path, help="guarda el JSON (ya validado) en este archivo")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    doc = validate_world(volcar(), {})
    r = resumen(doc)
    print(f"Pesa {r['bytes'] / 1024:.1f} KB de {MAX_DOC_BYTES // 1024} KB permitidos.")
    print(f"{r['assets']} assets, {r['objetos']} objetos, {r['piezas']} piezas.")
    print(f"{r['triangulos_de_assets']} triángulos en los assets (sin repetir por objeto).")
    if args.salida:
        args.salida.write_text(encode_world(doc), encoding="utf-8")


if __name__ == "__main__":
    main()
