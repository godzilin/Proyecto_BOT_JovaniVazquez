// Optimizador de polígonos de GLB en el navegador: carga, simplifica cada malla y vuelve a exportar.
import { BufferAttribute, BufferGeometry, type InterleavedBufferAttribute, type Material, Mesh, type Object3D, type SkinnedMesh } from "three";
import { GLTFExporter } from "three/addons/exporters/GLTFExporter.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { triangulosModelo } from "./formato";
import type { Malla, Resultado } from "./optimizador.worker";

export interface Analisis {
  triangulos: number;
  /** Tiene texturas: se pierden al optimizar. */
  texturas: boolean;
  /** Tiene huesos o animaciones: se queda quieto en su postura de reposo. */
  animado: boolean;
}

export interface Optimizado {
  glb: ArrayBuffer;
  antes: number;
  despues: number;
}

const triangulos = (g: BufferGeometry) => (g.index ? g.index.count : g.getAttribute("position").count) / 3;

/** Copia un atributo a un `Float32Array` propio (los del GLB pueden ir entrelazados, normalizados o compartir búfer). */
const plano = (a: BufferAttribute | InterleavedBufferAttribute): Float32Array => {
  const r = new Float32Array(a.count * a.itemSize);
  for (let i = 0; i < a.count; i++) for (let k = 0; k < a.itemSize; k++) r[i * a.itemSize + k] = a.getComponent(i, k);
  return r;
};

const materiales = (m: Mesh): Material[] => (Array.isArray(m.material) ? m.material : [m.material]);

/** Las propiedades de un material que son texturas (`map`, `normalMap`…). */
const texturasDe = (m: Material) => Object.keys(m).filter((k) => (m as unknown as Record<string, { isTexture?: boolean } | null>)[k]?.isTexture);

const mallasDe = (o: Object3D): Mesh[] => {
  const mallas: Mesh[] = [];
  o.traverse((x) => {
    if ((x as Mesh).isMesh) mallas.push(x as Mesh);
  });
  return mallas;
};

/** Triángulos de un GLB y lo que se perdería al optimizarlo. */
export async function analizar(glb: ArrayBuffer): Promise<Analisis> {
  const g = await new GLTFLoader().parseAsync(glb, "");
  const mallas = mallasDe(g.scene);
  return {
    triangulos: triangulosModelo(g.scene),
    texturas: mallas.some((m) => materiales(m).some((x) => texturasDe(x).length > 0)),
    animado: g.animations.length > 0 || mallas.some((m) => (m as SkinnedMesh).isSkinnedMesh),
  };
}

/**
 * Baja el modelo hacia `objetivo` triángulos en total, repartidos en proporción a cada malla.
 * Solo conserva posición y color por vértice (las normales se recalculan); las texturas se quitan,
 * porque sin UV no se verían, y las mallas con huesos pasan a ser fijas en su postura de reposo
 * (el mundo no dibuja las que tienen huesos) y sin animaciones.
 */
export async function optimizar(glb: ArrayBuffer, objetivo: number): Promise<Optimizado> {
  const escena: Object3D = (await new GLTFLoader().parseAsync(glb, "")).scene;
  const antes = triangulosModelo(escena);
  const trabajador = new Worker(new URL("./optimizador.worker.ts", import.meta.url), { type: "module" });
  const simplificar = (malla: Malla, meta: number) =>
    new Promise<Resultado>((ok, mal) => {
      trabajador.onmessage = (e: MessageEvent<Resultado>) => ok(e.data);
      trabajador.onerror = (e) => mal(new Error(e.message || "El optimizador ha fallado"));
      const transferir = [malla.position.buffer, ...(malla.color ? [malla.color.buffer] : []), ...(malla.index ? [malla.index.buffer] : [])];
      trabajador.postMessage({ malla, meta }, transferir as ArrayBuffer[]);
    });
  try {
    for (const m of mallasDe(escena)) await simplificarMalla(m, Math.max(4, Math.floor((objetivo * triangulos(m.geometry)) / antes)), simplificar);
  } finally {
    trabajador.terminate();
  }
  const salida = await new Promise<ArrayBuffer>((ok, mal) =>
    new GLTFExporter().parse(escena, (r) => (r instanceof ArrayBuffer ? ok(r) : mal(new Error("Exportación inválida"))), mal, { binary: true }),
  );
  return { glb: salida, antes, despues: triangulosModelo(escena) };
}

/** Cambia la geometría de `m` por la simplificada, sin texturas y, si tenía huesos, por una malla fija. */
async function simplificarMalla(m: Mesh, meta: number, simplificar: (malla: Malla, meta: number) => Promise<Resultado>): Promise<void> {
  const color = m.geometry.getAttribute("color");
  const r = await simplificar(
    {
      position: plano(m.geometry.getAttribute("position")),
      color: color ? plano(color) : null,
      colorTam: color?.itemSize ?? 3,
      index: m.geometry.index ? Uint32Array.from(m.geometry.index.array) : null,
    },
    meta,
  );
  const g = new BufferGeometry();
  g.setAttribute("position", new BufferAttribute(r.position, 3));
  g.setAttribute("normal", new BufferAttribute(r.normal, 3));
  if (r.color) g.setAttribute("color", new BufferAttribute(r.color, r.colorTam));
  if (r.index) g.setIndex(new BufferAttribute(r.index, 1));
  m.geometry.dispose();
  for (const x of materiales(m)) for (const k of texturasDe(x)) (x as unknown as Record<string, null>)[k] = null;
  if ((m as SkinnedMesh).isSkinnedMesh) {
    const fija = new Mesh(g, m.material);
    fija.name = m.name;
    fija.position.copy(m.position);
    fija.quaternion.copy(m.quaternion);
    fija.scale.copy(m.scale);
    m.parent?.add(fija);
    m.removeFromParent();
  } else m.geometry = g;
}
