"""Mide llamadas de dibujo y triángulos de la página del Jovaniverso en cada vista.

Uso (desde la raíz del repo, con `npm ci` hecho en universo/):

    python docs/medir_universo.py [--url http://localhost:5173] [--arrancar] [--capturas DIR] [--json SALIDA]

Abre la página con `?debug=fijo` (calidad adaptativa apagada) en Chromium sin ventana, con WebGL por software
(las llamadas y los triángulos no dependen de la GPU), recorre las vistas de `__jv.irA` más la plaza en 1280x720 y
en 390x844 con táctil, y saca una tabla: llamadas y triángulos del fotograma entero y cuánto es la pasada de
sombras (se mide también con las sombras apagadas y se resta). `--arrancar` lanza `npm run dev` en universo/.
`--capturas DIR` guarda un PNG por vista de 1280x720 (solo las de CAPTURAS) para comparar antes y después.
Presupuesto (Biblia, «Jovaniverso: rendimiento imperativo»): < 120 llamadas y < 250.000 triángulos en la peor vista.
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

VISTAS = ["nublo", "bandama", "dunas", "faro", "muelle", "mogan", "canteras", "dunasfaro", "cenit", "nieves", "drago", "plaza"]
CAPTURAS = ["plaza", "faro", "nublo", "cenit"]
PERFILES = {
    "escritorio": dict(viewport={"width": 1280, "height": 720}),
    "movil": dict(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True, device_scale_factor=1),
}
ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--enable-webgl"]

JS_IR = """([nombre, px, pz]) => {
  const jv = window.__jv;
  if (nombre === 'plaza') jv.tp(px, pz, 0.3, 0.35, 8);
  else jv.irA(nombre);
}"""
JS_MEDIR = """async () => {
  const jv = window.__jv;
  const esperar = (n) => new Promise((r) => { const f = () => (--n <= 0 ? r() : requestAnimationFrame(f)); requestAnimationFrame(f); });
  await esperar(20);
  const total = jv.info();
  jv.renderer.shadowMap.enabled = false;
  await esperar(4);
  const sin = jv.info();
  jv.renderer.shadowMap.enabled = true;
  jv.renderer.shadowMap.needsUpdate = true;
  await esperar(2);
  return { total, sin };
}"""


def plaza() -> tuple[float, float]:
    """Centro de PLAZA leído de terreno.ts, para no repetirlo aquí."""
    src = (Path(__file__).resolve().parent.parent / "universo/src/mundo/terreno.ts").read_text(encoding="utf-8")
    m = re.search(r"export const PLAZA = \{ x: (-?[\d.]+), z: (-?[\d.]+)", src)
    return float(m.group(1)), float(m.group(2))


def medir(url: str, capturas: Path | None, extra: str = "", solo: str = "") -> dict:
    px, pz = plaza()
    res: dict = {}
    with sync_playwright() as p:
        nav = p.chromium.launch(headless=True, args=ARGS)
        for perfil, cfg in PERFILES.items():
            if solo and perfil != solo:
                continue
            ctx = nav.new_context(**cfg)
            pag = ctx.new_page()
            pag.on("pageerror", lambda e: print("ERROR de página:", e, file=sys.stderr))
            pag.goto(f"{url}/?debug=fijo{extra}", wait_until="load")
            pag.wait_for_function("window.__jv !== undefined", timeout=120000)
            res[perfil] = {}
            for v in VISTAS:
                pag.evaluate(JS_IR, [v, px, pz])
                m = pag.evaluate(JS_MEDIR)
                t, s = m["total"], m["sin"]
                res[perfil][v] = dict(
                    calls=t["calls"], tris=t["triangles"], calls_sombra=t["calls"] - s["calls"], tris_sombra=t["triangles"] - s["triangles"]
                )
                if capturas and perfil == "escritorio" and v in CAPTURAS:
                    pag.screenshot(path=str(capturas / f"{v}.png"))
            ctx.close()
        nav.close()
    return res


def tabla(res: dict) -> str:
    out = []
    for perfil, vistas in res.items():
        out.append(f"\n{perfil}\n{'vista':<10}{'llamadas':>9}{'(sombra)':>9}{'triángulos':>12}{'(sombra)':>10}")
        for v, d in vistas.items():
            out.append(f"{v:<10}{d['calls']:>9}{d['calls_sombra']:>9}{d['tris']:>12}{d['tris_sombra']:>10}")
        out.append(f"{'máximo':<10}{max(d['calls'] for d in vistas.values()):>9}{'':>9}{max(d['tris'] for d in vistas.values()):>12}")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:5173")
    ap.add_argument("--arrancar", action="store_true", help="lanza `npm run dev` en universo/ mientras mide")
    ap.add_argument("--capturas", type=Path, help="carpeta donde guardar las capturas")
    ap.add_argument("--json", type=Path, help="guarda las cifras en este archivo")
    ap.add_argument("--extra", default="", help="parámetros de URL extra, p. ej. &celda=48&teselas=10")
    ap.add_argument("--solo", choices=list(PERFILES), default="", help="mide solo este perfil")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    proc = None
    if a.arrancar:
        raiz = Path(__file__).resolve().parent.parent / "universo"
        proc = subprocess.Popen("npm run dev", cwd=raiz, shell=True)
        time.sleep(6)
    try:
        if a.capturas:
            a.capturas.mkdir(parents=True, exist_ok=True)
        res = medir(a.url, a.capturas, a.extra, a.solo)
    finally:
        if proc:
            subprocess.run(f"taskkill /F /T /PID {proc.pid}", shell=True, capture_output=True)
    print(tabla(res))
    if a.json:
        a.json.write_text(json.dumps(res, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
