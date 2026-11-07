// Simplifica una malla fuera del hilo principal: `mergeVertices` y `SimplifyModifier` congelaban la página ~4 s con un millón de triángulos.
import { BufferAttribute, BufferGeometry } from "three";
import { mergeVertices } from "three/addons/utils/BufferGeometryUtils.js";
import { SimplifyModifier } from "three/addons/modifiers/SimplifyModifier.js";

export interface Malla {
  position: Float32Array;
  /** Color por vértice con su tamaño (3 o 4), o `null`. */
  color: Float32Array | null;
  colorTam: number;
  index: Uint32Array | null;
}

export interface Resultado extends Malla {
  normal: Float32Array;
}

const triangulos = (g: BufferGeometry) => (g.index ? g.index.count : g.getAttribute("position").count) / 3;

const mod = new SimplifyModifier();

self.onmessage = async ({ data }: MessageEvent<{ malla: Malla; meta: number }>) => {
  const { malla, meta } = data;
  let g = new BufferGeometry();
  g.setAttribute("position", new BufferAttribute(malla.position, 3));
  if (malla.color) g.setAttribute("color", new BufferAttribute(malla.color, malla.colorTam));
  if (malla.index) g.setIndex(new BufferAttribute(malla.index, 1));
  g = mergeVertices(g);
  // `modify` pide vértices a quitar pero apunta a una proporción de triángulos: así llega a la meta de una vez.
  if (triangulos(g) > meta) g = await mod.modify(g, g.getAttribute("position").count * (1 - meta / triangulos(g)));
  g.computeVertexNormals();
  const arr = (n: string) => g.getAttribute(n)?.array as Float32Array | undefined;
  const r: Resultado = {
    position: arr("position")!,
    normal: arr("normal")!,
    color: arr("color") ?? null,
    colorTam: malla.colorTam,
    index: g.index ? Uint32Array.from(g.index.array) : null,
  };
  postMessage(r, { transfer: [r.position.buffer, r.normal.buffer, ...(r.color ? [r.color.buffer] : []), ...(r.index ? [r.index.buffer] : [])] });
};
