"""Vuelca el mundo de serie del Jovaniverso a JSON y comprueba que el servidor lo acepta.

El pueblo, los hitos y la vegetación de la isla son datos (`universo/src/mundo/serie.ts`,
`mundoDeSerie()`), en el formato que valida `bot.services.universo_editor`. Este script compila
ese módulo con `vite build --ssr` (en una carpeta temporal), lo ejecuta con `node`, pasa el
resultado en trozos (`split_world`) por `validate_world` y cuenta cuánto pesa y qué lleva.

Uso (desde la raíz del repo, con `npm ci` hecho en universo/ y PYTHONPATH=src):

    python docs/volcar_mundo_serie.py [--salida mundo.json]

Sale con error si el mundo no vale o algún trozo pasa de sus topes. `volcar()` es lo que usa
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
    MAX_CELL_OBJECTS,
    MAX_PIECE_BYTES,
    asset_triangles,
    encode_piece,
    split_world,
    validate_world,
)

RAIZ = Path(__file__).resolve().parent.parent
UNIVERSO = RAIZ / "universo"


def volcar() -> dict[str, Any]:
    """Compila `mundoDeSerie` con Vite (modo SSR), lo ejecuta con Node y devuelve el JSON.

    Raises:
        subprocess.CalledProcessError: Si falla la compilación o la ejecución.
    """
    return evaluar("src/mundo/serie.ts", "m.mundoDeSerie()")


def evaluar(modulo: str, expresion: str) -> Any:
    """Compila un módulo de `universo/` (importado como `m`) y devuelve `expresion` en JSON.

    Raises:
        subprocess.CalledProcessError: Si falla la compilación o la ejecución.
    """
    with tempfile.TemporaryDirectory() as tmp:
        carpeta = Path(tmp)
        entrada = carpeta / "volcar.ts"
        ruta = (UNIVERSO / modulo).as_posix()
        entrada.write_text(
            f'import * as m from "{ruta}";\nprocess.stdout.write(JSON.stringify({expresion}));\n',
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


def resumen(trozos: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Peso en bytes y recuentos de un mundo ya validado, en trozos."""
    assets = {k: t for k, t in trozos.items() if k.startswith("a/")}
    celdas = {k: t for k, t in trozos.items() if k.startswith("c/")}
    return {
        "bytes": sum(len(encode_piece(t).encode()) for t in trozos.values()),
        "trozo_mayor": max(len(encode_piece(t).encode()) for t in trozos.values()),
        "assets": len(assets),
        "celdas": len(celdas),
        "objetos": sum(len(t["objetos"]) for t in celdas.values()),
        "celda_densa": max(len(t["objetos"]) for t in celdas.values()),
        "piezas": sum(len(a.get("piezas", [])) for a in assets.values()),
        "triangulos_de_assets": sum(asset_triangles(a, {}) for a in assets.values()),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--salida", type=Path, help="guarda los trozos (ya validados) en este archivo")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    trozos = validate_world(split_world(volcar()), {})
    r = resumen(trozos)
    print(f"Pesa {r['bytes'] / 1024:.1f} KB en {len(trozos)} trozos.")
    print(
        f"El mayor, {r['trozo_mayor'] / 1024:.1f} KB de {MAX_PIECE_BYTES // 1024} KB; "
        f"la celda más llena, {r['celda_densa']} objetos de {MAX_CELL_OBJECTS}."
    )
    print(
        f"{r['assets']} assets, {r['celdas']} celdas, {r['objetos']} objetos, {r['piezas']} piezas."
    )
    print(f"{r['triangulos_de_assets']} triángulos en los assets (sin repetir por objeto).")
    if args.salida:
        texto = json.dumps({"trozos": trozos}, ensure_ascii=False, separators=(",", ":"))
        args.salida.write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
