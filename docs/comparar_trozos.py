"""Comprueba que `aTrozos`/`deTrozos` de la página (`universo/src/editor/trozos.ts`) hacen lo mismo que `split_world`.

Compila un guion con `vite build --ssr` (como `volcar_mundo_serie.py`), lo ejecuta con Node sobre el mundo de
serie (sin terreno y con uno esculpido, también fuera de la isla y con `base`) y compara en Python: las claves y
el JSON de cada trozo contra `split_world` (más el terreno de cada celda, que en la página ya va por celdas), que
el servidor acepte el terreno tal cual y que juntar lo partido devuelva el mismo mundo. También da lo que tardan `aTrozos`, el
`commit` y el `guardar` del borrador frente al `JSON.stringify` del mundo entero.

Después monta un mundo sintético grande (`CELDAS_GRANDE` celdas llenas, con terreno) y comprueba que el
`commit` por trozos tocados y el deshacer por trozos dan lo mismo que el camino entero en cada clase de
cambio (y que un cambio que no dice lo que toca se pilla), y mide un `commit`, un deshacer y un guardado.

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

from bot.services.universo_editor import split_world, validate_piece

UNIVERSO = Path(__file__).resolve().parent.parent / "universo"
SRC = (UNIVERSO / "src").as_posix()

GUION = f"""
import {{ mundoDeSerie }} from "{SRC}/mundo/serie";
import {{ rejillaEditable, rejillas, terrenoDe }} from "{SRC}/mundo/terreno";
import {{ aTrozos, deTrozos }} from "{SRC}/editor/trozos";
import {{ Borrador }} from "{SRC}/editor/borrador";
import type {{ Terreno }} from "{SRC}/editor/formato";

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
// Las 16 celdas de la isla con unos hoyos y colores, y una celda lejos y alta (con `base`).
const esculpida: Record<string, Terreno> = {{}};
for (const [cx, cz] of [...Array.from({{ length: 16 }}, (_, i) => [(i % 4) - 2, Math.floor(i / 4) - 2]), [30, -12]]) {{
  const r = rejillaEditable(cx, cz);
  if (cx === 30) r.alt.fill(2400);
  for (let i = 0; i < r.alt.length; i += 7) r.alt[i] += (i % 13) * 0.37 - 2;
  for (let i = 0; i < r.pin.length; i += 11) r.pin[i] = i % 14 === 0 ? 0 : (i % 13) + 1;
  esculpida[`c/${{cx}}/${{cz}}`] = terrenoDe(r).terreno;
}}
rejillas.clear();
const conTerreno = {{ ...serie, terreno: esculpida }};

const trozos = aTrozos(serie);
const trozosT = aTrozos(conTerreno);

// Tiempos
const stringify = medir(50, () => JSON.stringify(serie));
const parte = medir(50, () => aTrozos(serie));
const parteT = medir(50, () => aTrozos({{ ...serie, terreno: esculpida }})); // con el corte del terreno en caché
const parteTFria = medir(20, () => aTrozos({{ ...serie, terreno: {{ ...esculpida }} }}));
const junta = medir(50, () => deTrozos(parse(trozos)));

(async () => {{
const cuerpos: string[] = [];
(globalThis as any).fetch = async (_u: string, o: {{ body: string }}) => {{
  cuerpos.push(o.body);
  const t = JSON.parse(o.body).trozos;
  return {{ ok: true, json: async () => ({{ revs: Object.fromEntries(Object.keys(t).map((k) => [k, 1])) }}) }};
}};
const nada = () => undefined;
const b = new Borrador({{ session: "s", guildId: "1" }}, {{ version: 0, trozos: {{}}, borrados: {{}}, docs: {{}} }}, {{ fallo: nada, estado: nada, reemplazado: nada }}, structuredClone(serie));
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
  juntoSerie: deTrozos(parse(trozos)), juntoT: deTrozos(parse(trozosT)),
  tiempos: {{ stringify, parte, parteT, parteTFria, junta, commit, cambio, guardarTodo, guardarUno, kbTodo, kbUno, deshecho }},
}}));
}})();
"""


#: Celdas de 80 m llenas del mundo sintético y objetos en cada una.
CELDAS_GRANDE = 300
OBJETOS_CELDA = 400

GUION_GRANDE = """
import { mundoDeSerie } from "{SRC}/mundo/serie";
import { rejillaEditable, rejillas, terrenoDe } from "{SRC}/mundo/terreno";
import { aTrozos, deTrozos } from "{SRC}/editor/trozos";
import { Borrador } from "{SRC}/editor/borrador";
import type { MundoDoc, Objeto, Terreno } from "{SRC}/editor/formato";

const hoy = (): number => performance.now();
const serie = mundoDeSerie();
const ids = Object.keys(serie.assets).filter((id) => !serie.assets[id].funcion);
let semilla = 7;
const azar = (): number => ((semilla = (semilla * 16807) % 2147483647) - 1) / 2147483646;
const objetos: Objeto[] = [];
for (let n = 0; n < {CELDAS}; n++) {
  const cx = (n % 20) - 10;
  const cz = Math.floor(n / 20) - 8;
  for (let k = 0; k < {POR_CELDA}; k++) {
    const x = Math.round((cx + azar()) * 8000) / 100;
    const z = Math.round((cz + azar()) * 8000) / 100;
    objetos.push({ asset: ids[k % ids.length], pos: [x, 1, z], rot: [0, Math.round(azar() * 600) / 100, 0], esc: [1, 1, 1] });
  }
}
const terreno: Record<string, Terreno> = {};
for (let i = 0; i < 16; i++) {
  const r = rejillaEditable((i % 4) - 2, Math.floor(i / 4) - 2);
  for (let j = 0; j < r.alt.length; j += 5) r.alt[j] += 1.5;
  terreno[`c/${(i % 4) - 2}/${Math.floor(i / 4) - 2}`] = terrenoDe(r).terreno;
}
const grande: MundoDoc = { ...serie, objetos, terreno };
const nada = (): undefined => undefined;
(globalThis as any).fetch = async (_u: string, o: { body: string }) => {
  const t = JSON.parse(o.body).trozos;
  return { ok: true, json: async () => ({ revs: Object.fromEntries(Object.keys(t).map((k) => [k, 1])) }) };
};
const nuevo = (): Borrador =>
  new Borrador({ session: "s", guildId: "1" }, { version: 0, trozos: {}, borrados: {}, docs: {} }, { fallo: nada, estado: nada, reemplazado: nada }, structuredClone(grande));
const actual = (b: Borrador): Map<string, string> => (b as any).actual;
const igual = (b: Borrador): boolean => {
  const todo = aTrozos(b.doc);
  const a = actual(b);
  return todo.size === a.size && [...todo].every(([k, v]) => a.get(k) === v);
};

(async () => {
  // Cada clase de cambio por el camino rápido, con la comprobación puesta: no tiene que descuadrar nunca.
  Borrador.comprobar = true;
  const b = nuevo();
  await b.guardar();
  const d = (): MundoDoc => b.doc;
  const pasos: [string, () => boolean][] = [
    ["mover en su celda", () => ((d().objetos[10].pos[0] += 0.5), b.commit({ objetos: [d().objetos[10]] }))],
    ["mover a otra celda", () => ((d().objetos[11].pos[0] += 80), b.commit({ objetos: [d().objetos[11]] }))],
    ["tinte", () => ((d().objetos[12].tinte = 0.9), b.commit({ objetos: [d().objetos[12]] }))],
    ["poner", () => (d().objetos.push({ asset: ids[0], pos: [3, 1, 3], rot: [0, 0, 0], esc: [1, 1, 1] }), b.commit({}))],
    ["borrar", () => (d().objetos.splice(20, 3), b.commit({}))],
    ["asset editado", () => ((d().assets[ids[1]].nombre = "Otro nombre"), b.commit({ assets: [ids[1]] }))],
    ["asset nuevo", () => ((d().assets.nuevo = structuredClone(d().assets[ids[2]])), b.commit({}))],
    ["asset borrado", () => (delete d().assets.nuevo, b.commit({}))],
    ["zona", () => ((Object.values(d().zonas)[0].r += 1), b.commit({}))],
    ["teleportes", () => ((d().teleportes = [{ nombre: "Aquí", x: 1, z: 2 }]), b.commit({}))],
    ["terreno", () => {
      const r = rejillaEditable(-2, -2);
      for (let i = 0; i < 400; i++) r.alt[i] -= 1;
      d().terreno = { ...d().terreno, "c/-2/-2": terrenoDe(r).terreno };
      return b.commit({});
    }],
    ["sin terreno", () => (delete d().terreno, b.commit({}))],
  ];
  const fallos: string[] = [];
  for (const [nombre, f] of pasos) if (!f() || !igual(b)) fallos.push(nombre);
  for (let i = b.hi - 1; i >= 0; i--) {
    b.irA(i);
    if (!igual(b)) fallos.push(`deshacer a ${i}`);
  }
  b.irA(b.hist.length - 1);
  if (!igual(b)) fallos.push("rehacer todo");
  // Un cambio que no dice lo que toca: la comprobación lo pilla y se usa el camino entero.
  const descuadres = Borrador.descuadres;
  b.doc.objetos[5].tinte = 0.7;
  const pillado = b.commit({}) && igual(b) && Borrador.descuadres === descuadres + 1;

  // Tiempos, sin la comprobación.
  Borrador.comprobar = false;
  const c = nuevo();
  await c.guardar();
  const o = c.doc.objetos[1000];
  const medir = (n: number, f: () => void): number => {
    const t = hoy();
    for (let i = 0; i < n; i++) f();
    return (hoy() - t) / n;
  };
  const commitEntero = medir(5, () => ((o.pos[0] += 0.01), c.commit()));
  const commitRapido = medir(50, () => ((o.pos[0] -= 0.01), c.commit({ objetos: [o] })));
  const deshacer = medir(40, () => c.irA(c.hi - 1));
  // El deshacer de antes: leer todos los trozos y juntarlos.
  const todos = [...actual(c)];
  const deshacerEntero = medir(3, () => deTrozos(Object.fromEntries(todos.map(([k, v]) => [k, JSON.parse(v)]))));
  const p = c.doc.objetos[2000];
  p.pos[0] += 0.02;
  c.commit({ objetos: [p] });
  const t = hoy();
  await c.guardar();
  const guardarUno = hoy() - t;
  process.stdout.write(JSON.stringify({
    objetos: grande.objetos.length, trozos: actual(c).size, fallos, pillado, descuadres, pasos: pasos.length,
    tiempos: { commitEntero, commitRapido, deshacer, deshacerEntero, guardarUno },
  }));
})();
"""


def ejecutar(guion: str = GUION) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        carpeta = Path(tmp)
        entrada = carpeta / "comparar.ts"
        entrada.write_text(guion, encoding="utf-8")
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


def partir(doc: dict[str, Any]) -> dict[str, Any]:
    """Lo que tiene que dar `aTrozos`: `split_world` sin el terreno y el de cada celda, tal cual."""
    trozos = split_world({k: v for k, v in doc.items() if k != "terreno"})
    for clave, terreno in doc.get("terreno", {}).items():
        trozos.setdefault(clave, {"objetos": []})["terreno"] = terreno
    return trozos


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    r = ejecutar()
    for nombre, doc, trozos, junto in (
        ("serie", r["serie"], r["trozos"], r["juntoSerie"]),
        ("serie con terreno", r["conTerreno"], r["trozosT"], r["juntoT"]),
    ):
        ts = {k: json.loads(v) for k, v in trozos.items()}
        py = partir(doc)
        distintos = sorted(k for k in ts.keys() | py.keys() if ts.get(k) != py.get(k))
        assert not distintos, (
            f"{nombre}: {len(distintos)} trozos no coinciden, p. ej. {distintos[:5]}"
        )
        assert mismo_mundo(junto, doc), f"{nombre}: juntar lo partido no devuelve el mismo mundo"
        print(f"{nombre}: {len(ts)} trozos iguales a split_world y juntar los devuelve al mundo")
    for clave, terreno in r["conTerreno"]["terreno"].items():
        limpio, _ = validate_piece(clave, {"terreno": terreno}, {})
        assert limpio["terreno"] == terreno, f"{clave}: el servidor no guarda el terreno tal cual"
    assert r["conTerreno"]["terreno"]["c/30/-12"].get("base"), "la celda alta no lleva base"
    print("terreno por celdas: el servidor lo acepta tal cual, también la celda alta con base")
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
    grande()


def grande() -> None:
    """El mundo sintético grande: el camino rápido da lo mismo que el entero, y lo que tarda cada uno."""
    guion = (
        GUION_GRANDE.replace("{SRC}", SRC)
        .replace("{CELDAS}", str(CELDAS_GRANDE))
        .replace("{POR_CELDA}", str(OBJETOS_CELDA))
    )
    r = ejecutar(guion)
    assert not r["fallos"], f"el borrador por trozos no da lo mismo que el entero en: {r['fallos']}"
    assert r["descuadres"] == 0, f"{r['descuadres']} commits o deshacer por trozos descuadraron"
    assert r["pillado"], "un commit que no dice lo que toca no se ha pillado"
    print(
        f"mundo grande ({r['objetos']} objetos, {r['trozos']} trozos): {r['pasos']} clases de cambio, "
        "deshacer y rehacer, iguales que por el camino entero; el que no dice lo que toca se pilla"
    )
    t = r["tiempos"]
    print(
        f"commit: {t['commitEntero']:.0f} ms entero, {t['commitRapido']:.2f} ms por trozos | "
        f"deshacer: {t['deshacerEntero']:.0f} ms entero, {t['deshacer']:.2f} ms por trozos | "
        f"guardar un objeto: {t['guardarUno']:.2f} ms"
    )


if __name__ == "__main__":
    main()
