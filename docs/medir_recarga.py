"""Mide el tirón de la página al publicarse un cambio: rehacer unas celdas contra montar el mundo entero.

Compila un guion con `vite build --ssr` (como `comparar_trozos.py`) y lo ejecuta con Node, sin
navegador: `Obras` monta las mallas, el culling y las colisiones igual que en la página (lo que no
mide es lo que tarda la GPU en subir las mallas nuevas, que también es menos con menos mallas).
Sobre el mundo de serie y sobre uno sintético grande (`CELDAS` celdas llenas):

- antes: bajar todo, `deTrozos` y `Obras.cargar` del mundo entero;
- ahora: bajar los trozos cambiados, `parchear` y `Obras.actualizar` de esas celdas, con 1, 4, 12 y
  24 celdas cambiadas (de ahí el tope `MAX_CELDAS_PARCIAL` de `mundo.ts`).

Comprueba también que lo rehecho por celdas deja la escena y las colisiones igual que montar entero
el mundo nuevo. Los bytes son del JSON de `/api/mundo` y de `?desde=` comprimidos con gzip.

Uso (desde la raíz, con `npm ci` hecho en universo/ y PYTHONPATH=src):

    python docs/medir_recarga.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
CELDAS = 300
POR_CELDA = 200

GUION = """
import { gzipSync } from "node:zlib";
import { MeshLambertMaterial, Scene } from "{SRC}/../node_modules/three/build/three.module.js";
import { mundoDeSerie } from "{SRC}/mundo/serie";
import { aTrozos, deTrozos, parchear } from "{SRC}/editor/trozos";
import { Obras } from "{SRC}/mundo/obras";
import { Culling } from "{SRC}/mundo/culling";
import { circulos } from "{SRC}/mundo/colision";
import type { MundoDoc, Objeto } from "{SRC}/editor/formato";

const hoy = (): number => performance.now();
const serie = mundoDeSerie();
const ids = Object.keys(serie.assets).filter((id) => serie.assets[id].tipo !== "glb" && !serie.assets[id].funcion);
let semilla = 11;
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
const grande: MundoDoc = { ...serie, objetos };

const material = new MeshLambertMaterial({ vertexColors: true });
const nuevas = (): { obras: Obras; escena: Scene } => {
  const escena = new Scene();
  return { obras: new Obras(escena, new Culling(), material), escena };
};
// Lo que hay en la escena y en las colisiones, sin orden: para comparar lo rehecho con lo montado entero.
const foto = (escena: Scene, lista = circulos): string => {
  const mallas = escena.children.map((m: any) => {
    const p = m.geometry.getAttribute("position");
    let suma = 0;
    for (let i = 0; i < p.array.length; i += 7) suma += p.array[i];
    const inst = m.isInstancedMesh ? Array.from(m.instanceMatrix.array as Float32Array).reduce((a, b) => a + b, 0) : 0;
    return `${m.isInstancedMesh ? m.count : 0}:${p.count}:${suma.toFixed(2)}:${inst.toFixed(2)}`;
  });
  const cs = lista.map((c) => `${c.x.toFixed(3)},${c.z.toFixed(3)},${c.r.toFixed(3)}`);
  return JSON.stringify([mallas.sort(), cs.sort()]);
};
const gz = (s: string): number => gzipSync(s).length;
const texto = (t: Record<string, unknown>): string => JSON.stringify({ version: 2, trozos: t });

const medir = (nombre: string, mundo: MundoDoc) => {
  const trozos = Object.fromEntries([...aTrozos(mundo)].map(([k, v]) => [k, JSON.parse(v)]));
  const doc = deTrozos(trozos);
  const celdas = Object.keys(trozos).filter((k) => k.startsWith("c/") && (trozos[k] as any).objetos.length);
  // Antes: el mundo entero (bajarlo ya está en caché del navegador; aquí, leerlo, juntarlo y montarlo).
  const { obras, escena } = nuevas();
  obras.cargar(doc);
  const textoEntero = texto(trozos);
  let t = hoy();
  const docEntero = deTrozos(JSON.parse(textoEntero).trozos);
  obras.cargar(docEntero);
  const entero = hoy() - t;
  const filas = [];
  let base = docEntero;
  for (const n of [1, 4, 12, 24]) {
    if (n > celdas.length) break;
    // Cada celda cambiada: su primer objeto se mueve medio metro (sin salir de ella).
    const cambio: Record<string, unknown> = {};
    for (const k of celdas.slice(0, n)) {
      const c = structuredClone(trozos[k]) as { objetos: Objeto[] };
      c.objetos[0].pos[0] += c.objetos[0].pos[0] % 80 < 79 ? 0.5 : -0.5;
      cambio[k] = c;
      trozos[k] = c;
    }
    const textoCambio = JSON.stringify({ version: 3, desde: 2, trozos: cambio });
    t = hoy();
    const r = JSON.parse(textoCambio);
    const doc2 = parchear(base, r.trozos);
    const ok = obras.actualizar(doc2, Object.keys(r.trozos));
    const parcial = hoy() - t;
    // Lo rehecho, igual que montar entero el mundo nuevo.
    const fotoParcial = foto(escena);
    const otra = nuevas();
    const antes = circulos.length;
    otra.obras.cargar(doc2);
    const otraFoto = foto(otra.escena, circulos.slice(antes));
    otra.obras.vaciar();
    const igual = ok && fotoParcial === otraFoto && circulos.length === antes;
    if (!igual) console.error(nombre, n, ok, fotoParcial.length, otraFoto.length);
    filas.push({ n, parcial, igual, kb: gz(textoCambio) / 1024 });
    base = doc2;
  }
  t = hoy();
  obras.cargar(base);
  const entero2 = hoy() - t;
  obras.vaciar();
  return { nombre, objetos: mundo.objetos.length, celdas: celdas.length, entero: Math.min(entero, entero2), kbEntero: gz(textoEntero) / 1024, filas };
};

process.stdout.write(JSON.stringify([medir("serie", serie), medir("grande", grande)]));
"""


def _comparar():
    spec = importlib.util.spec_from_file_location("comparar_trozos", RAIZ / "comparar_trozos.py")
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def ejecutar() -> list[dict]:
    comparar = _comparar()
    guion = (
        GUION.replace("{SRC}", comparar.SRC)
        .replace("{CELDAS}", str(CELDAS))
        .replace("{POR_CELDA}", str(POR_CELDA))
    )
    return comparar.ejecutar(guion)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    for r in ejecutar():
        print(
            f"{r['nombre']} ({r['objetos']} objetos en {r['celdas']} celdas): "
            f"entero {r['entero']:.0f} ms, {r['kbEntero']:.0f} KB"
        )
        for f in r["filas"]:
            assert f["igual"], f"{r['nombre']}: rehacer {f['n']} celdas no deja lo mismo que montar entero"
            print(f"  {f['n']:>2} celdas: {f['parcial']:.1f} ms, {f['kb']:.1f} KB")
    print(json.dumps({"ok": True}))


if __name__ == "__main__":
    main()
