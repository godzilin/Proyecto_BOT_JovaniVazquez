"""Comprueba que `aTrozos`/`deTrozos` de la página (`universo/src/editor/trozos.ts`) hacen lo mismo que `split_world`.

Compila un guion con `vite build --ssr` (como `volcar_mundo_serie.py`), lo ejecuta con Node sobre el mundo de
serie (sin terreno y con uno esculpido) y compara en Python: las claves y el JSON de cada trozo contra
`split_world`, y que juntar lo partido devuelva el mismo mundo. También da lo que tardan `aTrozos`, el
`commit` y el `guardar` del borrador frente al `JSON.stringify` del mundo entero.

Uso (desde la raíz, con `npm ci` hecho en universo/ y PYTHONPATH=src):

    python docs/comparar_trozos.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from bot.services.universo_editor import split_world

UNIVERSO = Path(__file__).resolve().parent.parent / "universo"
SRC = (UNIVERSO / "src").as_posix()

GUION = f"""
import {{ mundoDeSerie }} from "{SRC}/mundo/serie";
import {{ alturas, pinturas, terrenoDoc }} from "{SRC}/mundo/terreno";
import {{ aTrozos, deTrozos }} from "{SRC}/editor/trozos";
import {{ Borrador }} from "{SRC}/editor/borrador";

const hoy = (): number => performance.now();
const medir = (n: number, f: () => unknown): number => {{
  f();
  const t = hoy();
  for (let i = 0; i < n; i++) f();
  return (hoy() - t) / n;
}};
const objeto = (m: Map<string, string>): Record<string, string> => Object.fromEntries(m);
const parse = (m: Map<string, string>): Record<string, unknown> => Object.fromEntries([...m].map(([k, v]) => [k, JSON.parse(v)]));

const serie = mundoDeSerie();
const pura = terrenoDoc(); // la fórmula de la isla, tal cual
for (let i = 0; i < alturas.length; i += 7) alturas[i] += (i % 13) * 0.37 - 2; // unos hoyos
for (let i = 0; i < pinturas.length; i += 11) pinturas[i] = i % 14 === 0 ? 0 : (i % 13) + 1;
const esculpida = terrenoDoc();
const conTerreno = {{ ...serie, terreno: esculpida }};

const trozos = aTrozos(serie);
const trozosT = aTrozos(conTerreno);
// Sin tres celdas con terreno: deben salir de la fórmula, igual que la rejilla pura.
const purosT = aTrozos({{ ...serie, terreno: pura }});
const sinTres = parse(purosT);
for (const k of ["c/-2/-2", "c/0/1", "c/1/0"]) {{
  const c = sinTres[k] as {{ terreno?: unknown }};
  delete c.terreno;
}}

// Tiempos
const stringify = medir(50, () => JSON.stringify(serie));
const parte = medir(50, () => aTrozos(serie));
const parteT = medir(50, () => aTrozos({{ ...serie, terreno: {{ ...esculpida }} }})); // con el corte del terreno en caché
let n = 0;
const parteTFria = medir(20, () => aTrozos({{ ...serie, terreno: {{ alturas: esculpida.alturas, colores: String.fromCharCode(65 + (n++ % 2)) + esculpida.colores.slice(1) }} }}));
const junta = medir(50, () => deTrozos(parse(trozos)));

(async () => {{
const cuerpos: string[] = [];
(globalThis as any).fetch = async (_u: string, o: {{ body: string }}) => {{
  cuerpos.push(o.body);
  const t = JSON.parse(o.body).trozos;
  return {{ ok: true, json: async () => ({{ revs: Object.fromEntries(Object.keys(t).map((k) => [k, 1])) }}) }};
}};
const nada = () => undefined;
const b = new Borrador({{ session: "s", guildId: "1" }}, {{ version: 0, trozos: {{}} }}, {{ fallo: nada, estado: nada, reemplazado: nada }}, structuredClone(serie));
const tGuardarTodo = hoy();
await b.guardar();
const guardarTodo = hoy() - tGuardarTodo;
const kbTodo = cuerpos[0].length / 1024;
b.doc.objetos[0].pos = [b.doc.objetos[0].pos[0] + 0.5, b.doc.objetos[0].pos[1], b.doc.objetos[0].pos[2]];
const tCommit = hoy();
const cambio = b.commit();
const commit = hoy() - tCommit;
const tGuardar = hoy();
await b.guardar();
const guardarUno = hoy() - tGuardar;
const kbUno = cuerpos[1].length / 1024;
b.irA(0);
const deshecho = JSON.stringify(b.doc.objetos[0].pos);

process.stdout.write(JSON.stringify({{
  serie, conTerreno, trozos: objeto(trozos), trozosT: objeto(trozosT),
  juntoSerie: deTrozos(parse(trozos)), juntoT: deTrozos(parse(trozosT)), juntoSinTres: deTrozos(sinTres), pura,
  tiempos: {{ stringify, parte, parteT, parteTFria, junta, commit, cambio, guardarTodo, guardarUno, kbTodo, kbUno, deshecho }},
}}));
}})();
"""


def ejecutar() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        carpeta = Path(tmp)
        entrada = carpeta / "comparar.ts"
        entrada.write_text(GUION, encoding="utf-8")
        config = carpeta / "vite.config.mjs"
        config.write_text(
            "export default "
            + json.dumps(
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
            )
            + ";\n",
            encoding="utf-8",
        )
        vite = UNIVERSO / "node_modules/vite/bin/vite.js"
        subprocess.run(["node", str(vite), "build", "--config", str(config)], check=True)
        salida = subprocess.run(
            ["node", str(carpeta / "out/comparar.js")],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
    return json.loads(salida.stdout)


def mismo_mundo(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Igual salvo el orden de los objetos (salen por celdas)."""
    clave = lambda o: json.dumps(o, sort_keys=True)  # noqa: E731
    return (
        a["assets"] == b["assets"]
        and a["zonas"] == b["zonas"]
        and a.get("terreno") == b.get("terreno")
        and sorted(map(clave, a["objetos"])) == sorted(map(clave, b["objetos"]))
    )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    r = ejecutar()
    for nombre, doc, trozos, junto in (
        ("serie", r["serie"], r["trozos"], r["juntoSerie"]),
        ("serie con terreno", r["conTerreno"], r["trozosT"], r["juntoT"]),
    ):
        ts = {k: json.loads(v) for k, v in trozos.items()}
        py = split_world(doc)
        distintos = sorted(k for k in ts.keys() | py.keys() if ts.get(k) != py.get(k))
        assert not distintos, (
            f"{nombre}: {len(distintos)} trozos no coinciden, p. ej. {distintos[:5]}"
        )
        assert mismo_mundo(junto, doc), f"{nombre}: juntar lo partido no devuelve el mismo mundo"
        print(f"{nombre}: {len(ts)} trozos iguales a split_world y juntar los devuelve al mundo")
    pura = r["pura"]
    assert r["juntoSinTres"]["terreno"] == pura, (
        "las celdas sin terreno no salen de la fórmula de la isla"
    )
    print("celdas sin terreno: la rejilla sale igual que la fórmula pura")
    t = r["tiempos"]
    assert t["cambio"] is True and t["deshecho"] != "null"
    print(f"JSON.stringify del mundo: {t['stringify']:.2f} ms | aTrozos: {t['parte']:.2f} ms")
    print(
        f"aTrozos con terreno: {t['parteT']:.2f} ms en caché, {t['parteTFria']:.2f} ms sin caché | deTrozos: {t['junta']:.2f} ms"
    )
    print(f"commit de un objeto movido: {t['commit']:.2f} ms")
    print(
        f"guardar todo: {t['guardarTodo']:.1f} ms ({t['kbTodo']:.0f} KB) | guardar un objeto: {t['guardarUno']:.2f} ms ({t['kbUno']:.1f} KB)"
    )


if __name__ == "__main__":
    main()
