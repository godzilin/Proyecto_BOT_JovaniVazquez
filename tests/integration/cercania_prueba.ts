// Guiones de la carga por cercanía de la página (`universo/src/mundo/cercania.ts`, `obras.ts` y `suelo.ts`) para
// `test_universo_cercania.py`: se compilan con Vite (`docs/volcar_mundo_serie.py`) y se ejecutan con Node, sin navegador ni WebGL.
// El mismo three que usa la página (desde aquí no se resuelve el nombre del paquete).
import { MeshLambertMaterial, Scene } from "../../universo/node_modules/three/build/three.module.js";
import type { Asset, Objeto } from "../../universo/src/editor/formato";
import { Caducado, Cercania, type Fuente, fuenteLocal } from "../../universo/src/mundo/cercania";
import { numeroCirculos } from "../../universo/src/mundo/colision";
import { Culling } from "../../universo/src/mundo/culling";
import { Obras } from "../../universo/src/mundo/obras";
import { Suelo } from "../../universo/src/mundo/suelo";
import { rejillas } from "../../universo/src/mundo/terreno";

const CELDA = 80;
const caja: Asset = { nombre: "Caja", tipo: "piezas", piezas: [{ forma: "caja", pos: [0, 0.5, 0], rot: [0, 0, 0], esc: [1, 1, 1], color: 0x888888 }], colision: [[0, 0, 0.5]] };
const objeto = (asset: string, x: number, z: number): Objeto => ({ asset, pos: [x, 1, z], rot: [0, 0, 0], esc: [1, 1, 1] });

/** Un mundo sintético de `lado` × `lado` celdas centrado en 0, dos objetos por celda; el asset depende de la celda. */
function fuenteSintetica(lado: number, version = 1, cambiadas: string[] = []) {
  const pedidas: string[] = [];
  const resumenes: string[] = [];
  const trozos = new Map<string, number>();
  for (let cx = -lado / 2; cx < lado / 2; cx++) for (let cz = -lado / 2; cz < lado / 2; cz++) trozos.set(`c/${cx}/${cz}`, 1);
  for (let i = 0; i < 3; i++) trozos.set(`a/a${i}`, 1);
  for (const k of cambiadas) trozos.set(k, version);
  let caducada = false;
  const f: Fuente = {
    origen: "sintetico",
    indice: { version, zonas: null, teleportes: null, trozos },
    async trozos(claves) {
      if (caducada) throw new Caducado("podada");
      pedidas.push(...claves);
      const out: Record<string, unknown> = {};
      for (const k of claves) {
        if (k.startsWith("a/")) out[k] = caja;
        else {
          const [, cx, cz] = k.split("/").map(Number);
          const a = `a${Math.abs(cx + cz) % 3}`;
          out[k] = { objetos: [objeto(a, cx * CELDA + 10, cz * CELDA + 10), objeto(a, cx * CELDA + 50, cz * CELDA + 50)] };
        }
      }
      return out;
    },
    async resumenes(claves) {
      resumenes.push(...claves);
      return Object.fromEntries(claves.map((k) => [k, ""]));
    },
  };
  return { f, pedidas, resumenes, caducar: () => (caducada = true) };
}

/** Lo que hace `Obras` con lo que le llega, sin dibujar. */
function obrasFalsas() {
  const celdas = new Set<string>();
  const assets = new Set<string>();
  const o = {
    ponerAsset: (id: string) => assets.add(id),
    quitarAsset: (id: string) => assets.delete(id),
    ponerCelda: (cx: number, cz: number) => celdas.add(`${cx}/${cz}`),
    quitarCelda: (cx: number, cz: number) => celdas.delete(`${cx}/${cz}`),
    trabajar: () => false,
  };
  return { o: o as unknown as Obras, celdas, assets };
}

async function hastaQueAcabe(c: Cercania, x: number, z: number): Promise<void> {
  for (let i = 0; i < 200; i++) {
    c.actualizar(x, z);
    await new Promise((r) => setTimeout(r, 0));
    if (!c.trabajar(Infinity)) return;
  }
  throw new Error("no acaba de cargar");
}

const lejosDe = (celdas: Set<string>, x: number, z: number): number =>
  Math.max(
    ...[...celdas].map((k) => {
      const [cx, cz] = k.split("/").map(Number);
      return Math.hypot(Math.max(cx * CELDA - x, 0, x - (cx + 1) * CELDA), Math.max(cz * CELDA - z, 0, z - (cz + 1) * CELDA));
    }),
  );

/** Carga alrededor de un punto, se va lejos y vuelve a cambiar de fuente con una celda cambiada. */
export async function paseo() {
  const s = fuenteSintetica(60); // 3600 celdas: 4,8 km de lado
  const { o, celdas, assets } = obrasFalsas();
  const c = new Cercania(o, null);
  c.cambiarFuente(s.f);
  await hastaQueAcabe(c, 0, 0);
  const alPrincipio = { celdas: celdas.size, lejos: lejosDe(celdas, 0, 0), pedidas: s.pedidas.filter((k) => k.startsWith("c/")).length, assets: assets.size, resumenes: s.resumenes.length, objetos: c.doc().objetos.length };
  await hastaQueAcabe(c, 1500, 0);
  const alMoverse = { celdas: celdas.size, lejos: lejosDe(celdas, 1500, 0), cerca0: [...celdas].some((k) => k === "0/0") };
  // Se publica otra versión con una sola celda cercana cambiada: solo se vuelve a pedir esa.
  const antes = s.pedidas.length;
  const otra = fuenteSintetica(60, 2, ["c/18/0"]);
  const pedidasOtra = otra.pedidas;
  c.cambiarFuente(otra.f);
  await hastaQueAcabe(c, 1500, 0);
  const alCambiar = { pedidas: pedidasOtra, viejas: s.pedidas.length - antes, celdas: celdas.size };
  // El servidor ya no tiene esa versión: se avisa para pedir otro índice.
  let avisos = 0;
  c.alCaducar = () => avisos++;
  otra.caducar();
  for (let i = 0; i < 5; i++) {
    c.actualizar(0, 0);
    await new Promise((r) => setTimeout(r, 0));
    c.trabajar(Infinity);
  }
  return { alPrincipio, alMoverse, alCambiar, avisos };
}

/** Las obras de verdad (three sin WebGL): montan regiones y círculos por celda y lo sueltan todo al irse. */
export async function obras() {
  const escena = new Scene();
  const culling = new Culling();
  const o = new Obras(escena, culling, new MeshLambertMaterial());
  const s = fuenteSintetica(20);
  const c = new Cercania(o, null);
  c.cambiarFuente(s.f);
  await hastaQueAcabe(c, 0, 0);
  const cargado = { mallas: o.mallas, hijos: escena.children.length, circulos: numeroCirculos(), objetos: c.doc().objetos.length };
  await hastaQueAcabe(c, 5000, 5000); // fuera del mundo: no queda nada
  const vacio = { mallas: o.mallas, hijos: escena.children.length, circulos: numeroCirculos() };
  return { cargado, vacio };
}

/** El suelo de una isla local: mallas finas cerca, horizonte lejos, y nada al soltarlo. */
export async function suelo() {
  const escena = new Scene();
  const culling = new Culling();
  const material = new MeshLambertMaterial();
  const suelo = new Suelo(escena, culling, material);
  const c = new Cercania(new Obras(escena, culling, material), suelo);
  const objetos: Objeto[] = [];
  c.cambiarFuente(fuenteLocal({ v: 2, assets: { caja }, objetos, zonas: {} }));
  await hastaQueAcabe(c, 0, 0);
  const isla = { rejillas: rejillas.size, mallas: suelo.mallas };
  await hastaQueAcabe(c, 5000, 0);
  const fuera = { rejillas: rejillas.size, mallas: suelo.mallas, hijos: escena.children.length };
  return { isla, fuera };
}

/** Lo dibujado, sin el orden: tipo, vértices, instancias y esfera de cada malla. */
function foto(escena: Scene): string[] {
  return escena.children
    .map((m) => {
      const malla = m as unknown as { isInstancedMesh?: boolean; count?: number; geometry: { attributes: { position: { count: number } }; boundingSphere: { center: { x: number; z: number }; radius: number } | null } };
      const e = malla.geometry.boundingSphere;
      const esfera = malla.isInstancedMesh ? "" : `${e?.center.x.toFixed(1)},${e?.center.z.toFixed(1)},${e?.radius.toFixed(1)}`;
      return `${malla.isInstancedMesh ? `i${malla.count}` : "m"}:${malla.geometry.attributes.position.count}:${esfera}`;
    })
    .sort();
}

/** Montar el mundo de serie de una vez da lo mismo que llegar a él desde otro con objetos movidos y borrados (solo esas celdas). */
export async function mismaEscena() {
  const { mundoDeSerie } = await import("../../universo/src/mundo/serie");
  const montar = async (docs: ReturnType<typeof mundoDeSerie>[]) => {
    const escena = new Scene();
    const material = new MeshLambertMaterial();
    const culling = new Culling();
    const c = new Cercania(new Obras(escena, culling, material), new Suelo(escena, culling, material));
    let f: Fuente | null = null;
    for (const d of docs) {
      f = fuenteLocal(d, f);
      c.cambiarFuente(f);
      await hastaQueAcabe(c, -8, 60);
    }
    const r = { foto: foto(escena), circulos: numeroCirculos() };
    c.vaciar();
    await hastaQueAcabe(c, -8, 60);
    return r;
  };
  const serie = mundoDeSerie();
  const otro = mundoDeSerie();
  otro.objetos = otro.objetos.filter((_, i) => i % 7).map((o, i) => (i % 5 ? o : { ...o, pos: [o.pos[0] + 30, o.pos[1], o.pos[2]] as Objeto["pos"] }));
  const entero = await montar([serie]);
  const porPartes = await montar([otro, serie]);
  return { iguales: JSON.stringify(entero) === JSON.stringify(porPartes), mallas: entero.foto.length, circulos: entero.circulos, distintas: entero.foto.filter((x) => !porPartes.foto.includes(x)).slice(0, 5) };
}
