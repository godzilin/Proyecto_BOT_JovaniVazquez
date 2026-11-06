// Ayudas para montar geometrías con color por vértice y fundirlas en una sola malla (pocas llamadas de dibujo).
import { BufferAttribute, BufferGeometry, Color, Euler, Matrix4, Quaternion, Vector3 } from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

export type ColorFn = (x: number, y: number, z: number) => Color;

const m = new Matrix4();
const q = new Quaternion();
const e = new Euler();

/** Quita uv, la pasa a no indexada (aspecto facetado) y le pone color fijo o calculado por vértice. */
export function pintar(g: BufferGeometry, color: number | Color | ColorFn): BufferGeometry {
  const r = g.index ? g.toNonIndexed() : g;
  r.deleteAttribute("uv");
  const pos = r.getAttribute("position");
  const col = new Float32Array(pos.count * 3);
  const fijo = typeof color === "function" ? null : new Color(color);
  for (let i = 0; i < pos.count; i++) {
    const c = fijo ?? (color as ColorFn)(pos.getX(i), pos.getY(i), pos.getZ(i));
    col[i * 3] = c.r;
    col[i * 3 + 1] = c.g;
    col[i * 3 + 2] = c.b;
  }
  r.setAttribute("color", new BufferAttribute(col, 3));
  return r;
}

/** Coloca una geometría: posición, rotación (x, y, z en radianes) y escala. Devuelve la misma. */
export function poner(
  g: BufferGeometry,
  p: [number, number, number],
  rot: [number, number, number] = [0, 0, 0],
  s: number | [number, number, number] = 1,
): BufferGeometry {
  const esc = typeof s === "number" ? new Vector3(s, s, s) : new Vector3(...s);
  q.setFromEuler(e.set(rot[0], rot[1], rot[2], "YXZ"));
  m.compose(new Vector3(...p), q, esc);
  return g.applyMatrix4(m);
}

export function fundir(gs: BufferGeometry[]): BufferGeometry {
  const f = mergeGeometries(gs, false);
  gs.forEach((g) => g.dispose());
  if (!f) throw new Error("No se han podido fundir las geometrías");
  return f;
}

/** Variación determinista de brillo para que no todo sea plano. */
export function tono(hex: number, v: number): Color {
  return new Color(hex).multiplyScalar(v);
}
