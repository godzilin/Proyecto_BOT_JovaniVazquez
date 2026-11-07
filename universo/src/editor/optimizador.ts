// Optimizador de polígonos de GLB en el navegador: carga, simplifica cada malla y vuelve a exportar.
import {
  BufferAttribute,
  BufferGeometry,
  Color,
  type InterleavedBufferAttribute,
  type Material,
  Mesh,
  type MeshStandardMaterial,
  type Object3D,
  type SkinnedMesh,
  type Texture,
  Vector2,
} from "three";
import { GLTFExporter } from "three/addons/exporters/GLTFExporter.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { triangulosModelo } from "./formato";
import type { Malla, Resultado } from "./optimizador.worker";

export interface Analisis {
  triangulos: number;
  /** Tiene texturas: su color pasa a los vértices y se pierde el detalle fino. */
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

/** La imagen de una textura, a 512 px como mucho: reducirla promedia lo que rodea a cada vértice. */
function pixeles(t: Texture): ImageData | null {
  const img = t.image as (CanvasImageSource & { width: number; height: number }) | undefined;
  if (!img?.width || !img.height) return null;
  const k = Math.min(1, 512 / Math.max(img.width, img.height));
  const lienzo = new OffscreenCanvas(Math.max(1, Math.round(img.width * k)), Math.max(1, Math.round(img.height * k)));
  const ctx = lienzo.getContext("2d")!;
  ctx.drawImage(img, 0, 0, lienzo.width, lienzo.height);
  return ctx.getImageData(0, 0, lienzo.width, lienzo.height);
}

/** Media de un canal (0 rojo… 2 azul) de una textura, de 0 a 1; 1 si no se puede leer. */
function media(t: Texture, canal: number): number {
  const px = pixeles(t);
  if (!px) return 1;
  let suma = 0;
  for (let i = canal; i < px.data.length; i += 4) suma += px.data[i];
  return suma / (px.data.length / 4) / 255;
}

/**
 * Quita las texturas de un material. Antes, los factores que multiplicaban por un mapa pasan a valer su media:
 * sin eso, un casco de metal con su mapa de metalicidad quedaría todo metal (casi negro), y el brillo, blanco entero.
 */
function quitarTexturas(x: Material): void {
  const m = x as MeshStandardMaterial;
  if (m.metalnessMap) m.metalness *= media(m.metalnessMap, 2); // glTF: metal en el azul, rugosidad en el verde
  if (m.roughnessMap) m.roughness *= media(m.roughnessMap, 1);
  // shortcut: lo que brilla (luces, pantallas) se apaga; hornear el brillo pediría un segundo color por vértice.
  if (m.emissiveMap) m.emissive?.setRGB(0, 0, 0);
  for (const k of texturasDe(x)) (x as unknown as Record<string, null>)[k] = null;
}

/**
 * Hornea la textura de color (`map`) en colores por vértice: cada vértice se queda con el píxel de sus UV,
 * multiplicado por el color que ya tuviera. `null` si la malla no tiene textura o UV.
 */
function hornear(m: Mesh): Float32Array | null {
  const map = Array.isArray(m.material) ? null : (m.material as MeshStandardMaterial).map;
  const uv = map && m.geometry.getAttribute(map.channel ? `uv${map.channel}` : "uv");
  const px = uv && pixeles(map);
  if (!px) return null;
  map.updateMatrix();
  const previo = m.geometry.getAttribute("color");
  const r = new Float32Array(uv.count * 3);
  const p = new Vector2();
  const c = new Color();
  for (let i = 0; i < uv.count; i++) {
    map.transformUv(p.set(uv.getX(i), uv.getY(i))); // repetición, desplazamiento y orientación: deja (0, 0) arriba a la izquierda
    const o = (Math.min(px.height - 1, Math.floor(p.y * px.height)) * px.width + Math.min(px.width - 1, Math.floor(p.x * px.width))) * 4;
    c.setRGB(px.data[o] / 255, px.data[o + 1] / 255, px.data[o + 2] / 255, map.colorSpace);
    r[i * 3] = c.r * (previo?.getX(i) ?? 1);
    r[i * 3 + 1] = c.g * (previo?.getY(i) ?? 1);
    r[i * 3 + 2] = c.b * (previo?.getZ(i) ?? 1);
  }
  return r;
}

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
 * Solo conserva posición y color por vértice (las normales se recalculan). La textura de color se hornea en
 * los colores por vértice y luego se quitan todas, y las mallas con huesos pasan a ser fijas en su postura de reposo
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

/** Cambia la geometría de `m` por la simplificada, con la textura horneada en los vértices y, si tenía huesos, por una malla fija. */
async function simplificarMalla(m: Mesh, meta: number, simplificar: (malla: Malla, meta: number) => Promise<Resultado>): Promise<void> {
  const color = m.geometry.getAttribute("color");
  const horneado = hornear(m);
  if (horneado) {
    // Copia: el material puede ser de otra malla sin color por vértice, que se vería negra.
    m.material = (m.material as Material).clone();
    (m.material as Material).vertexColors = true;
  }
  const r = await simplificar(
    {
      position: plano(m.geometry.getAttribute("position")),
      color: horneado ?? (color ? plano(color) : null),
      colorTam: horneado ? 3 : (color?.itemSize ?? 3),
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
  for (const x of materiales(m)) quitarTexturas(x);
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
