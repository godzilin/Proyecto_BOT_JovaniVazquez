// Optimizador de polígonos de GLB en el navegador: carga, simplifica cada malla y vuelve a exportar.
import { BufferGeometry, type Mesh, type Object3D } from "three";
import { mergeVertices } from "three/addons/utils/BufferGeometryUtils.js";
import { GLTFExporter } from "three/addons/exporters/GLTFExporter.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { SimplifyModifier } from "three/addons/modifiers/SimplifyModifier.js";
import { triangulosModelo } from "./formato";

export interface Optimizado {
  glb: ArrayBuffer;
  antes: number;
  despues: number;
}

const cede = () => new Promise<void>((r) => setTimeout(r));

const triangulos = (g: BufferGeometry) => (g.index ? g.index.count : g.getAttribute("position").count) / 3;

/** Triángulos de un GLB sin optimizarlo. */
export async function contarTriangulos(glb: ArrayBuffer): Promise<number> {
  return triangulosModelo((await new GLTFLoader().parseAsync(glb, "")).scene);
}

/**
 * Baja el modelo hacia `objetivo` triángulos en total, repartidos en proporción a cada malla.
 * Solo conserva posición y color por vértice: normales y UV se pierden (se recalculan las normales).
 */
// shortcut: SimplifyModifier es O(n log n) con mucha constante y corre en el hilo principal (una malla de 100k triángulos tarda segundos); las mallas con skin o animación se simplifican sin sus huesos, y las UV se pierden, así que las texturas dejan de verse. Mover a un worker o usar meshoptimizer cuando haya modelos con texturas o muy grandes.
export async function optimizar(glb: ArrayBuffer, objetivo: number): Promise<Optimizado> {
  const escena: Object3D = (await new GLTFLoader().parseAsync(glb, "")).scene;
  const antes = triangulosModelo(escena);
  if (antes > objetivo) {
    const mallas: Mesh[] = [];
    escena.traverse((o) => {
      if ((o as Mesh).isMesh) mallas.push(o as Mesh);
    });
    const mod = new SimplifyModifier();
    for (const m of mallas) {
      await cede();
      const meta = Math.max(4, Math.floor((objetivo * triangulos(m.geometry)) / antes));
      let g = new BufferGeometry();
      g.setAttribute("position", m.geometry.getAttribute("position"));
      const color = m.geometry.getAttribute("color");
      if (color) g.setAttribute("color", color);
      if (m.geometry.index) g.setIndex(m.geometry.index);
      g = mergeVertices(g);
      // Quitar un vértice quita ~2 triángulos: se repite hasta llegar a la meta (tres pasadas como mucho).
      for (let i = 0; i < 3 && triangulos(g) > meta; i++) {
        const quitar = Math.ceil((triangulos(g) - meta) / 2);
        if (quitar >= g.getAttribute("position").count - 3) break;
        g = await mod.modify(g, quitar);
        await cede();
      }
      g.computeVertexNormals();
      m.geometry.dispose();
      m.geometry = g;
    }
  }
  const salida = await new Promise<ArrayBuffer>((ok, mal) =>
    new GLTFExporter().parse(escena, (r) => (r instanceof ArrayBuffer ? ok(r) : mal(new Error("Exportación inválida"))), mal, { binary: true }),
  );
  return { glb: salida, antes, despues: triangulosModelo(escena) };
}
