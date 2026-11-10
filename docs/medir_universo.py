"""Mide llamadas de dibujo y triángulos de la página del Jovaniverso en cada vista.

Uso (desde la raíz del repo, con `npm ci` hecho en universo/):

    python docs/medir_universo.py [--url http://localhost:5173] [--arrancar] [--capturas DIR] [--json SALIDA]

Abre la página con `?debug=fijo` (calidad adaptativa apagada) en Chromium sin ventana, con WebGL por software
(las llamadas y los triángulos no dependen de la GPU), recorre las vistas de `__jv.irA` más la plaza en 1280x720 y
en 390x844 con táctil, y saca una tabla: llamadas y triángulos del fotograma entero y cuánto es la pasada de
sombras (se mide también con las sombras apagadas y se resta). `--arrancar` lanza `npm run dev` en universo/.
`--capturas DIR` guarda un PNG por vista de 1280x720 (solo las de CAPTURAS) para comparar antes y después.
Las vistas `casino` (fuera, con las seis tragaperras de la plaza) y `casinodentro` (sentado en la 3) necesitan el
servidor de pruebas `docs/sala_local.py` (con PYTHONPATH=src): `--arrancar` lo lanza también. Sin él no hay casino y
las vistas `plaza`, `casino` y `casinodentro` salen sin las máquinas (el script lo avisa).
`--pruebas` mide el extraplano de los desarrolladores (`?mundo=pruebas`) en sus vistas (`VISTAS_PRUEBAS`).
`--carga N` mide la entrada N veces (cada una sin caché): cuándo acaba de pintarse el primer fotograma tras quitar la
pantalla de carga (el primero que ve el jugador), cuándo acaba el primero que ya enseña el mundo publicado, cuánto
bloquea montarlo (y si pasa con la pantalla de carga ya quitada: el tirón que se ve) y cuántas peticiones se hacen a `/api/mundo/*` (el índice y los lotes). Entra con `?editor`, así que necesita `docs/editor_local.py` (con PYTHONPATH=src) en
lugar de `docs/sala_local.py`, con un mundo publicado en su base de datos.
`--recorrido` recorre `RECORRIDO` (el centro, los bordes del mundo mirando hacia fuera y el cénit) en los dos perfiles y saca,
además de llamadas y triángulos, lo que tiene cargado la página (celdas, assets, mallas), las geometrías de three y la memoria
de JavaScript de la pestaña (`performance.memory`, en Chromium). Con un mundo grande y denso (`docs/editor_local.py --sintetico`
y `?editor`) dice si la carga por cercanía aguanta el presupuesto y si suelta lo que deja atrás.
Presupuesto (Biblia, «Jovaniverso: rendimiento imperativo»): < 120 llamadas y < 250.000 triángulos en la peor vista.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

VISTAS = [
    "nublo",
    "bandama",
    "dunas",
    "faro",
    "muelle",
    "mogan",
    "canteras",
    "dunasfaro",
    "cenit",
    "nieves",
    "drago",
    "plaza",
    "pisos",
    "pisodentro",
    "casino",
    "casinodentro",
]
CAPTURAS = [
    "plaza",
    "faro",
    "nublo",
    "cenit",
    "pisos",
    "pisodentro",
    "casino",
    "casinodentro",
    "rampas",
    "escalones",
]
#: Vistas del extraplano: los sitios de `SITIOS_PISTA` (universo/src/mundo/pista.ts) y el casino.
VISTAS_PRUEBAS = [
    "plaza",
    "rampas",
    "escalones",
    "columnas",
    "muro",
    "pisos",
    "pisodentro",
    "casino",
    "casinodentro",
]
PERFILES = {
    "escritorio": dict(viewport={"width": 1280, "height": 720}),
    "movil": dict(
        viewport={"width": 390, "height": 844},
        has_touch=True,
        is_mobile=True,
        device_scale_factor=1,
    ),
}
ARGS = [
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--ignore-gpu-blocklist",
    "--enable-webgl",
]

JS_IR = """async ([nombre, px, pz]) => {
  const jv = window.__jv;
  if (jv.interior?.dentro()) jv.interior.salir();
  if (nombre === 'pisos' || nombre === 'pisodentro') {
    // Delante de la puerta del edificio de pisos (la del interior `piso-plaza`) y, en `pisodentro`, dentro. Sin
    // interiores (una versión de antes), el mismo sitio.
    const esperar = (n) => new Promise((r) => { const f = () => (--n <= 0 ? r() : requestAnimationFrame(f)); requestAnimationFrame(f); });
    const puerta = () => (jv.interior?.puertas() ?? []).findIndex((p) => p.interior === 'piso-plaza');
    const p0 = jv.interior?.puertas()[puerta()];
    jv.tp(p0 ? p0.x + 3 * Math.sin(p0.yaw) : px, p0 ? p0.z + 3 * Math.cos(p0.yaw) : pz, p0 ? p0.yaw : 0, 0.3, 8);
    for (let i = 0; i < 120 && jv.interior && puerta() < 0; i++) await esperar(1);
    if (nombre === 'pisodentro' && jv.interior && puerta() >= 0) await jv.interior.entrar(puerta());
  }
  else if (nombre === 'plaza') jv.tp(px, pz, 0.3, 0.35, 8);
  else if (nombre === 'casino' || nombre === 'casinodentro') {
    jv.irA('casino');
    await jv.casinoListo;
    if (nombre === 'casinodentro' && jv.casino) jv.casino.sentar(2);
  } else jv.irA(nombre);
}"""
JS_MEDIR = """async () => {
  const jv = window.__jv;
  const esperar = (n) => new Promise((r) => { const f = () => (--n <= 0 ? r() : requestAnimationFrame(f)); requestAnimationFrame(f); });
  await esperar(20);
  // Con la carga por cercanía, hasta que no quede nada por bajar ni montar alrededor (las versiones de antes no lo tienen).
  for (let i = 0; i < 600 && jv.cargado?.().ocupado; i++) await esperar(1);
  await esperar(jv.cargado ? 60 : 4);
  const total = jv.info();
  jv.renderer.shadowMap.enabled = false;
  await esperar(4);
  const sin = jv.info();
  jv.renderer.shadowMap.enabled = true;
  jv.renderer.shadowMap.needsUpdate = true;
  await esperar(2);
  return { total, sin };
}"""


#: Dónde está la puerta del piso de serie (x, z), para medir la misma vista con una versión sin interiores.
PUERTA_PISO = tuple(
    json.loads(
        (Path(__file__).resolve().parent.parent / "src/bot/assets/universo_piso.json").read_text(
            encoding="utf-8"
        )
    )["objetos"][1]["pos"][::2]
)


def plaza() -> tuple[float, float]:
    """Centro de PLAZA leído de terreno.ts, para no repetirlo aquí."""
    src = (Path(__file__).resolve().parent.parent / "universo/src/mundo/terreno.ts").read_text(
        encoding="utf-8"
    )
    m = re.search(r"export const PLAZA = \{ x: (-?[\d.]+), z: (-?[\d.]+)", src)
    return float(m.group(1)), float(m.group(2))


def medir(
    url: str, capturas: Path | None, extra: str = "", solo: str = "", vistas: list[str] = VISTAS
) -> dict:
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
            pag.evaluate(
                "async () => { await window.__jv.casinoListo; }"
            )  # el casino se carga al acercarse a la plaza
            if not pag.evaluate("() => window.__jv.casino !== undefined"):
                print(
                    f"AVISO: sin casino en {perfil} (¿falta docs/sala_local.py?)", file=sys.stderr
                )
            res[perfil] = {}
            for v in vistas:
                # Las vistas del piso, sin interiores (antes), delante de donde va su puerta.
                donde = (PUERTA_PISO[0] + 3, PUERTA_PISO[1]) if v.startswith("piso") else (px, pz)
                pag.evaluate(JS_IR, [v, *donde])
                m = pag.evaluate(JS_MEDIR)
                t, s = m["total"], m["sin"]
                res[perfil][v] = dict(
                    calls=t["calls"],
                    tris=t["triangles"],
                    calls_sombra=t["calls"] - s["calls"],
                    tris_sombra=t["triangles"] - s["triangles"],
                )
                if capturas and perfil == "escritorio" and v in CAPTURAS:
                    pag.screenshot(path=str(capturas / f"{v}.png"))
            ctx.close()
        nav.close()
    return res


# Antes de que cargue la página: apunta cuándo se quita la pantalla de carga, cuándo acaba el primer fotograma después
# y cuánto bloquea montar el mundo publicado: desde que llega su JSON hasta que acaba esa tarea (deTrozos y el montaje
# van seguidos en la misma), y cuándo acaba el primer fotograma que ya lo enseña.
JS_CARGA = """(() => {
  const c = (window.__carga = { fuera: null, primer: null, llega: null, listo: null, vista: null });
  const json = Response.prototype.json;
  Response.prototype.json = function () {
    const r = json.call(this);
    if (!this.url.includes('/api/mundo')) return r;
    return r.then((v) => {
      c.llega = performance.now();
      setTimeout(() => (c.listo = performance.now()), 0);
      return v;
    });
  };
  const raf = window.requestAnimationFrame.bind(window);
  window.requestAnimationFrame = (f) => raf((t) => {
    if (c.fuera === null && document.getElementById('carga')?.classList.contains('fuera')) c.fuera = t;
    const conMundo = c.fuera !== null && c.listo !== null && t >= c.listo;
    f(t);
    if (c.primer === null && c.fuera !== null) c.primer = performance.now();
    if (c.vista === null && conMundo) c.vista = performance.now();
  });
})()"""


def medir_carga(url: str, veces: int, perfil: str = "escritorio") -> dict:
    """La entrada `veces` veces, cada una en un contexto nuevo (sin caché): medianas en ms."""
    filas = []
    with sync_playwright() as p:
        nav = p.chromium.launch(headless=True, args=ARGS)
        for _ in range(veces):
            ctx = nav.new_context(**PERFILES[perfil])
            ctx.add_init_script(JS_CARGA)
            pag = ctx.new_page()
            pedidas = []
            pag.on("request", lambda r, p=pedidas: "/api/mundo" in r.url and p.append(r.url))
            pag.goto(f"{url}/?debug=fijo&editor", wait_until="load")
            pag.wait_for_function("window.__carga.vista !== null", timeout=120000)
            c = pag.evaluate("() => window.__carga")
            montaje = c["listo"] - c["llega"]
            filas.append(
                dict(
                    primer=c["primer"],
                    vista=c["vista"],
                    montaje=montaje,
                    # El tirón que se ve: montar con la pantalla de carga ya quitada.
                    tiron=montaje if c["listo"] > c["fuera"] else 0,
                    mundo=len(pedidas),
                )
            )
            ctx.close()
        nav.close()
    med = lambda k: sorted(f[k] for f in filas)[len(filas) // 2]  # noqa: E731
    return {k: round(med(k)) for k in ("primer", "vista", "montaje", "tiron", "mundo")}


#: Puntos del recorrido: (x, z, giro, inclinación, distancia de la cámara). Dentro del borde de hoy (±300 m).
RECORRIDO = {
    "centro": (0, 0, 0.6, 0.3, 9),
    "este": (290, 0, -1.57, 0.12, 8),
    "norte": (0, 290, 3.14, 0.12, 8),
    "esquina": (290, -290, -0.8, 0.12, 8),
    "oeste": (-290, 0, 1.57, 0.12, 8),
    "vuelta": (0, 0, 0.6, 0.3, 9),
    "cenit": (0, 0, 0, 1.2, 230),
}
JS_RECORRIDO = """async ([x, z, giro, inclinacion, dist]) => {
  const jv = window.__jv;
  jv.camara.distMax = Math.max(16, dist + 30);
  jv.tp(x, z, giro, inclinacion, dist);
  const esperar = (n) => new Promise((r) => { const f = () => (--n <= 0 ? r() : requestAnimationFrame(f)); requestAnimationFrame(f); });
  await esperar(10);
  for (let i = 0; i < 900 && jv.cargado().ocupado; i++) await esperar(1);
  await esperar(90); // el alcance (presupuesto de dibujo) se ajusta en unas decenas de fotogramas
  const i = jv.info();
  const mem = jv.renderer.info.memory;
  return { ...i, ...jv.cargado(), geometrias: mem.geometries, heap: performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576) : null };
}"""


def recorrido(url: str, extra: str, solo: str) -> dict:
    """Lo cargado, lo dibujado y la memoria en cada punto de `RECORRIDO`, por perfil."""
    res: dict = {}
    with sync_playwright() as p:
        nav = p.chromium.launch(headless=True, args=[*ARGS, "--enable-precise-memory-info"])
        for perfil, cfg in PERFILES.items():
            if solo and perfil != solo:
                continue
            ctx = nav.new_context(**cfg)
            pag = ctx.new_page()
            pag.on("pageerror", lambda e: print("ERROR de página:", e, file=sys.stderr))
            pag.goto(f"{url}/?debug=fijo{extra}", wait_until="load")
            pag.wait_for_function("window.__jv !== undefined", timeout=120000)
            res[perfil] = {n: pag.evaluate(JS_RECORRIDO, list(v)) for n, v in RECORRIDO.items()}
            ctx.close()
        nav.close()
    return res


def tabla_recorrido(res: dict) -> str:
    cols = ["calls", "triangles", "alcance", "montadas", "lejanas", "obras", "suelo", "geometrias", "heap"]
    out = []
    for perfil, puntos in res.items():
        out.append(f"\n{perfil}\n{'punto':<9}" + "".join(f"{c[:10]:>11}" for c in cols))
        for n, d in puntos.items():
            out.append(f"{n:<9}" + "".join(f"{str(d.get(c)):>11}" for c in cols))
    return "\n".join(out)


def tabla(res: dict) -> str:
    out = []
    for perfil, vistas in res.items():
        out.append(
            f"\n{perfil}\n{'vista':<10}{'llamadas':>9}{'(sombra)':>9}{'triángulos':>12}{'(sombra)':>10}"
        )
        for v, d in vistas.items():
            out.append(
                f"{v:<10}{d['calls']:>9}{d['calls_sombra']:>9}{d['tris']:>12}{d['tris_sombra']:>10}"
            )
        out.append(
            f"{'máximo':<10}{max(d['calls'] for d in vistas.values()):>9}{'':>9}{max(d['tris'] for d in vistas.values()):>12}"
        )
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--url", default="http://localhost:5173")
    ap.add_argument(
        "--arrancar", action="store_true", help="lanza `npm run dev` en universo/ mientras mide"
    )
    ap.add_argument("--capturas", type=Path, help="carpeta donde guardar las capturas")
    ap.add_argument("--json", type=Path, help="guarda las cifras en este archivo")
    ap.add_argument("--extra", default="", help="parámetros de URL extra, p. ej. &teselas=10")
    ap.add_argument("--solo", choices=list(PERFILES), default="", help="mide solo este perfil")
    ap.add_argument("--pruebas", action="store_true", help="mide el extraplano de pruebas")
    ap.add_argument("--carga", type=int, default=0, help="mide la entrada tantas veces")
    ap.add_argument(
        "--recorrido", action="store_true", help="recorre el mundo midiendo lo cargado y la memoria"
    )
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    proc = sala = None
    if a.arrancar:
        repo = Path(__file__).resolve().parent.parent
        env = {**os.environ, "PYTHONPATH": str(repo / "src"), "PYTHONIOENCODING": "utf-8"}
        local = "docs/editor_local.py" if a.carga or a.recorrido else "docs/sala_local.py"
        sala = subprocess.Popen([sys.executable, str(repo / local)], env=env)
        proc = subprocess.Popen("npm run dev", cwd=repo / "universo", shell=True)
        time.sleep(6)
    try:
        if a.capturas:
            a.capturas.mkdir(parents=True, exist_ok=True)
        if a.carga:
            carga = medir_carga(a.url, a.carga, a.solo or "escritorio")
            print(
                f"primer fotograma {carga['primer']} ms · con el mundo publicado "
                f"{carga['vista']} ms · montarlo bloquea {carga['montaje']} ms, "
                f"{carga['tiron']} ms a la vista · {carga['mundo']} peticiones a /api/mundo"
            )
            return
        if a.recorrido:
            res = recorrido(a.url, a.extra, a.solo)
            print(tabla_recorrido(res))
            if a.json:
                a.json.write_text(json.dumps(res, indent=1), encoding="utf-8")
            return
        if a.pruebas:
            res = medir(a.url, a.capturas, a.extra + "&mundo=pruebas", a.solo, VISTAS_PRUEBAS)
        else:
            res = medir(a.url, a.capturas, a.extra, a.solo)
    finally:
        for pr in (proc, sala):
            if pr:
                subprocess.run(f"taskkill /F /T /PID {pr.pid}", shell=True, capture_output=True)
    print(tabla(res))
    if a.json:
        a.json.write_text(json.dumps(res, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
