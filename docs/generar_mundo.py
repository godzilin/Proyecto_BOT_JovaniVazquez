"""Genera una isla del Jovaniverso y la escribe en el borrador de un servidor.

Cómo y por qué: `docs/generador-mundo.md`.

    python docs/generar_mundo.py docs/islas/la_graciosa.json --db mundo.db --dry-run
    python docs/generar_mundo.py docs/islas/la_graciosa.json --db mundo.db [--publicar]

Lee la isla (reglas), su relieve (el PNG de `docs/reducir_dem.py`) y el kit
(`docs/islas/kit_canario.json`), pasa cada trozo por la validación del servidor y lo guarda por
tandas en el borrador con el repositorio del bot (`UniversoMundoRepository`). Solo crea celdas que
no existen o reescribe las que lleva el generador (`gen`); las tocadas a mano se respetan.
`--dry-run` dice qué haría sin guardar nada. `--publicar` publica después (para probar en local con
`docs/editor_local.py --db`; en el servidor de verdad lo deciden los desarrolladores). Con
PYTHONPATH=src.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from array import array
from pathlib import Path

from PIL import Image

from bot.repositories.universo_mundo import UniversoMundoRepository
from bot.services.universo_generador import Relieve, escribir, generar

RAIZ = Path(__file__).resolve().parent
KIT = RAIZ / "islas" / "kit_canario.json"


def cargar_relieve(png: Path) -> Relieve:
    """El PNG de `reducir_dem.py` (dm, fila 0 al norte) como `Relieve` (m, fila 0 al sur)."""
    im = Image.open(png)
    ancho, alto = im.size
    dm = array("H", im.tobytes())  # I;16: little-endian, como la máquina
    filas = [dm[j * ancho : (j + 1) * ancho] for j in range(alto)]
    return Relieve(ancho, alto, tuple(v / 10 for fila in reversed(filas) for v in fila))


async def principal(a: argparse.Namespace) -> None:
    isla = json.loads(a.isla.read_text(encoding="utf-8"))
    kit = json.loads(a.kit.read_text(encoding="utf-8"))
    relieve = cargar_relieve(a.isla.parent / isla["relieve"]["archivo"])
    repo = UniversoMundoRepository(a.db)
    await repo.initialize()
    inicio = time.perf_counter()
    plan = await escribir(
        repo, a.guild, generar(isla, kit, relieve, a.semilla), a.autor, a.tanda, a.dry_run
    )
    segundos = time.perf_counter() - inicio
    verbo = "haría" if a.dry_run else "ha hecho"
    print(f"{isla['nombre']} ({segundos:.1f} s): el generador {verbo} esto en el borrador:")
    for que in ("crear", "reescribir", "igual", "respetar"):
        claves = getattr(plan, que)
        celdas = sum(c.startswith("c/") for c in claves)
        print(f"  {que:<10} {celdas:>5} celdas, {len(claves) - celdas:>3} assets")
    if plan.respetar:
        print("  Respetadas (hechas o tocadas a mano):", ", ".join(plan.respetar[:40]))
    print(f"  {plan.objetos} objetos, {plan.bytes / 1024:.0f} KB en JSON")
    if a.publicar and not a.dry_run:
        print(f"Publicada la versión {await repo.publish(a.guild, a.autor)}.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("isla", type=Path, help="el JSON de la isla (docs/islas/)")
    ap.add_argument("--db", type=Path, required=True, help="la base de datos del mundo")
    ap.add_argument("--guild", type=int, default=1, help="el servidor (1 en editor_local.py)")
    ap.add_argument("--kit", type=Path, default=KIT)
    ap.add_argument("--semilla", type=int, default=1)
    ap.add_argument("--tanda", type=int, default=100, help="trozos por transacción")
    ap.add_argument("--autor", type=int, default=0, help="id de quien firma el guardado")
    ap.add_argument("--dry-run", action="store_true", help="dice qué haría, sin guardar")
    ap.add_argument("--publicar", action="store_true", help="publica el borrador al acabar")
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(principal(ap.parse_args()))


if __name__ == "__main__":
    main()
