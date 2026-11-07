# ruff: noqa: E501  (la prosa del docstring y los trozos de JavaScript van en líneas largas)
"""Prueba las tragaperras de la plaza del Jovaniverso con Chromium sin ventana.

Uso (desde la raíz del repo, con `npm ci` hecho en universo/):

    python docs/probar_casino.py --arrancar [--capturas DIR]
    python docs/probar_casino.py --url http://localhost:5173      # con sala_local.py y `npm run dev` ya en marcha

`--arrancar` levanta `docs/sala_local.py` (el `/api/entrar` y `/api/traga` de pruebas, con las piezas reales del
bot y saldo en memoria) y `npm run dev` en universo/ (puerto 5173: si está ocupado, falla). El script se sienta en
la máquina 3, pulsa GIRAR con el ratón (escritorio) o con el dedo (móvil), saca capturas a mitad de tirada y al
final, juega hasta que sale un premio, pone la máquina 4 en Auto unas tiradas y la para, y comprueba qué suena en
cada caso (el registro de `window.__jv.casino.sonidos`, que existe con `?debug`). Hace lo mismo en 1280×720 y en
390×844 táctil, mide `renderer.info` en la vista de fuera y en la de sentado, y sale con código 1 si algo falla.
Las capturas van a docs/capturas_universo/casino/ (fuera de git).
"""

import argparse
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

RAIZ = Path(__file__).resolve().parent.parent
ARGS = [
    "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--enable-webgl",
    "--autoplay-policy=no-user-gesture-required",
]  # fmt: skip
PERFILES = {
    "escritorio": dict(viewport={"width": 1280, "height": 720}),
    "movil": dict(
        viewport={"width": 390, "height": 844},
        has_touch=True,
        is_mobile=True,
        device_scale_factor=1,
    ),
}
GIRAR, MITAD, DOBLE, AUTO, PARAR = 0, 1, 2, 3, 4
CON_PREMIO = {"premio", "granpremio", "mega", "epico", "bote", "gratis"}
fallos: list[str] = []


def comprobar(condicion: bool, texto: str) -> None:
    print(("  ok     " if condicion else "  FALLA  ") + texto)
    if not condicion:
        fallos.append(texto)


def esperar_fin(pag: Page, i: int, segundos: float = 25) -> bool:
    """Espera a que la máquina `i` termine de enseñar la tirada."""
    try:
        pag.wait_for_function(
            f"() => !window.__jv.casino.maquinas[{i}].ocupada && !window.__jv.casino.maquinas[{i}].repro",
            timeout=segundos * 1000,
        )
        return True
    except Exception:
        return False


def sonidos(pag: Page, desde: int, maquina: int | None = None) -> list[str]:
    todos = pag.evaluate(f"() => window.__jv.casino.sonidos.slice({desde})")
    return [s["nombre"] for s in todos if maquina is None or s["maquina"] == maquina]


def total(pag: Page) -> int:
    return pag.evaluate("() => window.__jv.casino.sonidos.length")


def pulsar(pag: Page, perfil: str, i: int, k: int) -> None:
    """Pulsa un botón físico de la máquina `i` con el ratón o con el dedo, donde cae en pantalla."""
    p = pag.evaluate(f"() => window.__jv.casino.pantallaDe({i}, {k})")
    if perfil == "movil":
        pag.touchscreen.tap(p["x"], p["y"])
    else:
        pag.mouse.click(p["x"], p["y"])


def medir(pag: Page) -> dict:
    return pag.evaluate(
        """async () => {
  const jv = window.__jv;
  const esperar = (n) => new Promise((r) => { const f = () => (--n <= 0 ? r() : requestAnimationFrame(f)); requestAnimationFrame(f); });
  await esperar(12);
  const total = jv.info();
  jv.renderer.shadowMap.enabled = false;
  await esperar(4);
  const sin = jv.info();
  jv.renderer.shadowMap.enabled = true;
  jv.renderer.shadowMap.needsUpdate = true;
  await esperar(2);
  return { calls: total.calls, tris: total.triangles, calls_sin_sombra: sin.calls, tris_sin_sombra: sin.triangles };
}"""
    )


def probar(nav, url: str, perfil: str, carpeta: Path) -> None:
    print(f"\n== {perfil} ==")
    ctx = nav.new_context(**PERFILES[perfil])
    pag = ctx.new_page()
    errores: list[str] = []
    pag.on("pageerror", lambda e: errores.append(str(e)))
    pag.on("console", lambda m: errores.append(m.text) if m.type == "error" else None)
    foto = lambda nombre: pag.screenshot(path=str(carpeta / f"{perfil}_{nombre}.png"))  # noqa: E731
    pag.goto(f"{url}/?debug=1", wait_until="load")
    pag.wait_for_function("window.__jv !== undefined", timeout=120000)
    pag.evaluate("async () => { await window.__jv.casinoListo; }")
    comprobar(
        pag.evaluate("() => window.__jv.casino !== undefined"),
        "el casino se monta al acercarse a la plaza",
    )

    pag.evaluate("window.__jv.irA('casino')")
    pag.wait_for_timeout(2500)
    foto("1_plaza")
    fuera = medir(pag)
    print(f"  vista de fuera: {fuera}")

    # Acercarse a la máquina 3: sale el aviso, y al tocarla (o con E) uno se sienta.
    pag.evaluate(
        "() => { const p = window.__jv.casino.puesto(2); window.__jv.tp(p.x, p.z, p.yaw, 0.3, 3); }"
    )
    pag.wait_for_timeout(1200)
    comprobar(
        pag.locator(".casino-aviso").is_visible(),
        "cerca de una máquina sale el aviso para sentarse",
    )
    print("  aviso:", pag.locator(".casino-aviso").inner_text())
    foto("1b_acercarse")
    if perfil == "escritorio":
        pag.keyboard.press("KeyE")
        pag.wait_for_timeout(800)
        comprobar(
            pag.evaluate("() => window.__jv.casino.estado().sentada") == 2,
            "E te sienta en la máquina de delante",
        )
        pag.keyboard.press("Escape")
        pag.wait_for_timeout(800)
    p = pag.evaluate("() => window.__jv.casino.pantallaDeMaquina(2)")
    if perfil == "movil":
        pag.touchscreen.tap(p["x"], p["y"])
    else:
        pag.mouse.click(p["x"], p["y"])
    pag.wait_for_timeout(1500)
    foto("2_sentado")
    dentro = medir(pag)
    print(f"  vista sentado:  {dentro}")
    comprobar(
        pag.evaluate("() => window.__jv.casino.estado().sentada") == 2,
        "al tocar la máquina te sientas (camara.anclar)",
    )

    # Una tirada normal con el botón GIRAR.
    n = total(pag)
    pulsar(pag, perfil, 2, GIRAR)
    pag.wait_for_timeout(1000)
    comprobar(
        pag.evaluate("() => window.__jv.casino.estado().maquinas[2].ocupada"),
        "GIRAR pone la máquina a girar",
    )
    foto("3_giro_a_mitad")
    comprobar(esperar_fin(pag, 2), "la tirada termina")
    pag.wait_for_timeout(300)
    foto("4_giro_final")
    normal = sonidos(pag, n, 3)
    print(f"  suena en una tirada: {normal}")
    paradas = sum(s in ("parada", "parada_lenta") for s in normal)
    comprobar(
        {"boton", "girar", "zumbido"} <= set(normal) and paradas == 3,
        "boton, girar, zumbido y una parada por rodillo",
    )
    comprobar(
        pag.locator(".casino-resultado").is_visible(), "el resultado sale en el panel del HUD"
    )
    print("  panel:", pag.locator(".casino-resultado").inner_text().replace("\n", " | "))

    # Tiradas hasta que salga un premio (el azar es el del bot).
    premio: list[str] = []
    for _ in range(80):
        n = total(pag)
        if perfil == "escritorio":
            pag.keyboard.press("Space")  # el atajo
        else:
            pulsar(pag, perfil, 2, GIRAR)
        if not esperar_fin(pag, 2, 30):
            break
        ultima = pag.evaluate("() => window.__jv.casino.ultima()")
        if ultima and ultima["won"] > 0:
            pag.wait_for_timeout(300)
            foto("5_premio")
            premio = sonidos(pag, n, 3)
            print(
                f"  suena en una tirada con premio ({ultima['won']} Y$, nivel {ultima['tier']}): {premio}"
            )
            break
        pag.wait_for_timeout(1500)
    comprobar(
        bool(premio) and bool(CON_PREMIO & set(premio)), "una tirada con premio suena a premio"
    )

    # Auto en la máquina de al lado: se pulsa desde el asiento, sin levantarse.
    pag.wait_for_timeout(1500)
    n = total(pag)
    if (
        perfil == "movil"
    ):  # en vertical la máquina vecina cae fuera de la pantalla: se mira a la derecha antes de pulsar
        pag.evaluate("() => { window.__jv.camara.yaw -= 0.8; }")
        pag.wait_for_timeout(300)
    pulsar(pag, perfil, 3, AUTO)
    pag.wait_for_timeout(2500)
    # Mirar a la derecha (arrastrando con el ratón) para ver el resto de la botonera vecina.
    if perfil == "escritorio":
        pag.mouse.move(640, 120)
        pag.mouse.down()
        pag.mouse.move(800, 120, steps=6)
        pag.mouse.up()
    pag.wait_for_timeout(900)
    foto("6_auto_vecina")
    comprobar(
        pag.evaluate("() => window.__jv.casino.estado().maquinas[3].auto"),
        "AUTO se activa en la máquina vecina",
    )
    try:
        pag.wait_for_function(
            f"() => window.__jv.casino.sonidos.slice({n}).filter((s) => s.maquina === 4 && s.nombre === 'zumbido').length >= 3",
            timeout=60000,
        )
    except Exception:
        pass
    pulsar(pag, perfil, 3, PARAR)
    pag.wait_for_timeout(300)
    en_auto = sonidos(pag, n, 4)
    print(f"  suena en el Auto: {en_auto}")
    comprobar(en_auto.count("zumbido") >= 3, "el Auto encadena tiradas solo")
    comprobar(en_auto.count("auto") >= 2, "suena `auto` al activarlo y al pararlo")
    comprobar(
        not pag.evaluate("() => window.__jv.casino.estado().maquinas[3].auto"),
        "PARAR detiene el Auto",
    )
    esperar_fin(pag, 3)

    # Levantarse con Esc.
    if perfil == "escritorio":
        pag.keyboard.press("Escape")
    else:
        pag.locator(".casino-boton:has-text('✕')").tap()
    pag.wait_for_timeout(700)
    comprobar(
        pag.evaluate("() => window.__jv.casino.estado().sentada") is None, "Esc (o ✕) te levanta"
    )
    foto("7_levantado")
    comprobar(not errores, f"sin errores en la consola ({errores[:3]})")
    ctx.close()


def esperar_url(url: str, segundos: float = 60) -> None:
    fin = time.time() + segundos
    while time.time() < fin:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:
            time.sleep(0.5)
    raise SystemExit(f"No contesta {url}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--url", default="http://localhost:5173")
    ap.add_argument("--arrancar", action="store_true", help="lanza sala_local.py y `npm run dev`")
    ap.add_argument("--capturas", type=Path, default=RAIZ / "docs/capturas_universo/casino")
    ap.add_argument("--solo", choices=list(PERFILES), default="")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    a.capturas.mkdir(parents=True, exist_ok=True)
    procs: list[subprocess.Popen] = []
    try:
        if a.arrancar:
            env = {**os.environ, "PYTHONPATH": str(RAIZ / "src"), "PYTHONIOENCODING": "utf-8"}
            procs.append(
                subprocess.Popen([sys.executable, str(RAIZ / "docs/sala_local.py")], env=env)
            )
            procs.append(
                subprocess.Popen(
                    "npm run dev -- --port 5173 --strictPort", cwd=RAIZ / "universo", shell=True
                )
            )
            esperar_url(a.url)
            time.sleep(2)
        with sync_playwright() as p:
            nav = p.chromium.launch(headless=True, args=ARGS)
            for perfil in PERFILES:
                if not a.solo or a.solo == perfil:
                    probar(nav, a.url, perfil, a.capturas)
            nav.close()
    finally:
        for pr in procs:
            subprocess.run(
                f"taskkill /F /T /PID {pr.pid}" if os.name == "nt" else f"kill {pr.pid}",
                shell=True,
                capture_output=True,
            )
    print("\n" + ("TODO BIEN" if not fallos else f"FALLAN {len(fallos)}: " + "; ".join(fallos)))
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()
