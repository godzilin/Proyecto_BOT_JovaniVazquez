// Prepara el robot para dibujar a los demás jugadores barato: las 19 piezas del modelo (rígidas y de las manos) se
// funden en 3 mallas con esqueleto, una por material, y quedan 3 llamadas de dibujo por robot en vez de 19.
import { AnimationClip, Bone, BufferAttribute, BufferGeometry, Color, Matrix4, Mesh, Object3D, Skeleton, SkinnedMesh, Sphere, Vector3 } from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { clone } from "three/addons/utils/SkeletonUtils.js";

export interface PlantillaRobot {
  /** Esqueleto con 3 mallas fundidas; se clona con `SkeletonUtils.clone` para cada jugador. */
  raiz: Object3D;
  clips: AnimationClip[];
}

/** Nombre del material que se tiñe de un color distinto para cada jugador. */
export const MATERIAL_CUERPO = "Main";

/** Tono (0-1, 24 posibles) del cuerpo sacado del id de Discord: el mismo en todas las pantallas, la tuya incluida. */
export function tonoDe(id: string): number {
  let h = 2166136261;
  for (let i = 0; i < id.length; i++) h = Math.imul(h ^ id.charCodeAt(i), 16777619);
  return ((h >>> 0) % 24) / 24;
}

/** Pone en `c` el color del cuerpo de un tono. */
export const colorCuerpo = (t: number, c: Color): Color => c.setHSL(t, 0.7, 0.5);

export function fundirRobot(modelo: Object3D, clips: AnimationClip[]): PlantillaRobot {
  const raiz = clone(modelo);
  raiz.updateMatrixWorld(true);
  const piezas: Mesh[] = [];
  let esqueleto: Skeleton | null = null;
  raiz.traverse((o) => {
    const m = o as Mesh;
    if (!m.isMesh) return;
    piezas.push(m);
    esqueleto ??= (m as SkinnedMesh).skeleton ?? null;
  });
  if (!esqueleto) throw new Error("El robot no tiene esqueleto");
  const huesos: Bone[] = (esqueleto as Skeleton).bones;
  const indice = new Map<string, number>();
  huesos.forEach((h, i) => indice.set(h.name, i));

  const aRaiz = new Matrix4().copy(raiz.matrixWorld).invert();
  const m = new Matrix4();
  const v = new Vector3();
  const grupos = new Map<string, { geo: BufferGeometry[]; mat: Mesh["material"] }>();

  for (const p of piezas) {
    const piel = p as SkinnedMesh;
    const g = p.geometry.clone();
    const pos = g.getAttribute("position");
    const n = pos.count;
    const skinIndex = new Uint16Array(n * 4);
    const skinWeight = new Float32Array(n * 4);
    m.multiplyMatrices(aRaiz, p.matrixWorld);
    if (piel.isSkinnedMesh) {
      // Posición en reposo tras aplicar el esqueleto, ya en el espacio de la raíz; huesos por nombre.
      piel.skeleton.update();
      const si = g.getAttribute("skinIndex");
      const sw = g.getAttribute("skinWeight");
      for (let i = 0; i < n; i++) {
        piel.getVertexPosition(i, v).applyMatrix4(m);
        pos.setXYZ(i, v.x, v.y, v.z);
        for (let k = 0; k < 4; k++) {
          skinIndex[i * 4 + k] = indice.get(piel.skeleton.bones[si.getComponent(i, k)].name) ?? 0;
          skinWeight[i * 4 + k] = sw.getComponent(i, k);
        }
      }
    } else {
      // Pieza rígida: va pegada al hueso del que cuelga, con peso 1.
      let hueso: Object3D | null = p.parent;
      while (hueso && !(hueso as Bone).isBone) hueso = hueso.parent;
      const h = hueso ? (indice.get(hueso.name) ?? 0) : 0;
      for (let i = 0; i < n; i++) {
        v.fromBufferAttribute(pos, i).applyMatrix4(m);
        pos.setXYZ(i, v.x, v.y, v.z);
        skinIndex[i * 4] = h;
        skinWeight[i * 4] = 1;
      }
    }
    g.setAttribute("skinIndex", new BufferAttribute(skinIndex, 4));
    g.setAttribute("skinWeight", new BufferAttribute(skinWeight, 4));
    g.morphAttributes = {};
    g.morphTargetsRelative = false;
    g.deleteAttribute("uv");
    g.deleteAttribute("normal");
    g.deleteAttribute("tangent");
    g.deleteAttribute("color");
    g.computeVertexNormals();
    const mat = Array.isArray(p.material) ? p.material[0] : p.material;
    const grupo = grupos.get(mat.name) ?? { geo: [], mat };
    grupo.geo.push(g);
    grupos.set(mat.name, grupo);
  }

  for (const p of piezas) p.removeFromParent();
  const sk = new Skeleton(huesos);
  for (const { geo, mat } of grupos.values()) {
    const malla = new SkinnedMesh(mergeGeometries(geo, false), mat);
    malla.name = (mat as { name: string }).name;
    raiz.add(malla);
    malla.updateMatrixWorld(true);
    malla.bind(sk, malla.matrixWorld);
    // Caja fija y holgada (la cuenta del esqueleto se haría en cada fotograma): el robot mide ~2 m y salta 1,3.
    malla.boundingSphere = new Sphere(new Vector3(0, 1, 0), 2.2);
  }
  // Sin cara animada (morphs) no hay nodo al que apuntar esas pistas: se quitan para no avisar en cada robot.
  const utiles = clips.map((c) => {
    const k = c.clone();
    k.tracks = k.tracks.filter((t) => !t.name.endsWith(".morphTargetInfluences"));
    return k;
  });
  return { raiz, clips: utiles };
}
