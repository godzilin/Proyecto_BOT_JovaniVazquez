// El mueble fijo del casino de la plaza: seis máquinas en arco bajo un toldo a rayas y el soporte del letrero del bote.
// Una sola malla con color por vértice (sin texturas); las pantallas, luces y botones los pone casino.ts al acercarse.
import { BoxGeometry, BufferGeometry, CylinderGeometry, Float32BufferAttribute, Mesh, MeshLambertMaterial, Vector3 } from "three";
import { añadirCirculo } from "../mundo/colision";
import { fundir, pintar, poner } from "../mundo/geom";
import { PLAZA } from "../mundo/terreno";
import { ARCO, ALTO_LETRERO, CUBIERTA, MAQUINAS, PANTALLA, PASO_ANGULO, PUESTOS, RADIO_ARCO, frenteDe } from "./sitio";

const CUERPO = 0x5a1630;
const ORO = 0xe2b441;
const NEGRO = 0x14141b;
const CUBIERTA_COLOR = 0x23232e;
const ACENTOS = [0xd8413a, 0x3a7bd8, 0x2fa760, 0x8a4fd0, 0xe08a2c, 0x20a8b8];
const RAYAS = [0xc8452f, 0xf4ecda];

type L = BufferGeometry[];
const caja = (p: L, w: number, h: number, d: number, x: number, y: number, z: number, color: number, rot?: [number, number, number]): void => {
  p.push(pintar(poner(new BoxGeometry(w, h, d), [x, y, z], rot), color));
};

/** Una máquina en su marco local. */
function maquina(i: number): L {
  const p: L = [];
  caja(p, 1.7, 0.15, 1.0, 0, 0.075, 0.05, 0x2a2a33); // zócalo
  caja(p, ANCHO_CUERPO, 0.9, 0.85, 0, 0.6, 0, CUERPO);
  caja(p, ANCHO_CUERPO, 0.8, 0.6, 0, 0.55, 0.725, CUERPO); // bajo la botonera
  caja(p, ANCHO_CUERPO, 1.15, 0.7, 0, 1.625, -0.075, CUERPO); // cuerpo de la pantalla
  for (const s of [-1, 1]) caja(p, 0.08, 1.15, 0.74, s * 0.84, 1.625, -0.075, ORO);
  caja(p, 1.3, 1.0, 0.07, 0, PANTALLA.y, 0.31, NEGRO); // marco de la pantalla (las bombillas van en él)
  caja(p, PANTALLA.w + 0.02, PANTALLA.h + 0.02, 0.004, 0, PANTALLA.y, 0.347, 0x070b16); // cristal apagado
  caja(p, 1.7, 0.45, 0.8, 0, 2.425, -0.05, ACENTOS[i % ACENTOS.length]); // rótulo superior
  caja(p, 1.76, 0.06, 0.86, 0, 2.68, -0.05, ORO);
  caja(p, 1.76, 0.05, 0.86, 0, 2.2, -0.05, ORO);
  // Botonera inclinada hacia quien juega.
  caja(p, CUBIERTA.w, CUBIERTA.grosor, CUBIERTA.d, 0, CUBIERTA.y, CUBIERTA.z, CUBIERTA_COLOR, [CUBIERTA.inclinacion, 0, 0]);
  caja(p, CUBIERTA.w + 0.04, 0.05, 0.05, 0, 1.03, 0.965, ORO); // canto delantero
  return p;
}
const ANCHO_CUERPO = 1.6;

/** Cuadrilátero con las caras hacia `hacia` (por si el orden de las esquinas sale al revés). */
function cara(c: Vector3[], hacia: Vector3, color: number): BufferGeometry {
  const n = new Vector3().crossVectors(new Vector3().subVectors(c[1], c[0]), new Vector3().subVectors(c[2], c[0]));
  const o = n.dot(hacia) < 0 ? [0, 3, 2, 1] : [0, 1, 2, 3];
  const pos: number[] = [];
  for (const k of [o[0], o[1], o[2], o[0], o[2], o[3]]) pos.push(c[k].x, c[k].y, c[k].z);
  const g = new BufferGeometry();
  g.setAttribute("position", new Float32BufferAttribute(pos, 3));
  g.computeVertexNormals();
  return pintar(g, color);
}

/** Toldo: un trozo de corona por máquina, más alto atrás y con rayas que alternan. */
function toldo(): L {
  const p: L = [];
  const R0 = RADIO_ARCO - 2.0;
  const R1 = RADIO_ARCO + 0.85;
  const [yF, yB, e] = [2.85, 3.35, 0.07];
  const pt = (a: number, r: number, y: number): Vector3 => new Vector3(PLAZA.x + Math.cos(a) * r, y, PLAZA.z + Math.sin(a) * r);
  for (let k = 0; k < MAQUINAS; k++) {
    const [a0, a1] = [PUESTOS[k].angulo - PASO_ANGULO / 2, PUESTOS[k].angulo + PASO_ANGULO / 2];
    const alto = [pt(a0, R0, yF), pt(a1, R0, yF), pt(a1, R1, yB), pt(a0, R1, yB)];
    const bajo = alto.map((q) => q.clone().setY(q.y - e));
    const color = RAYAS[k % 2];
    p.push(cara(alto, new Vector3(0, 1, 0), color), cara(bajo, new Vector3(0, -1, 0), 0xb59a78));
    p.push(cara([alto[0], alto[1], bajo[1], bajo[0]], new Vector3(Math.cos(PUESTOS[k].angulo), 0, Math.sin(PUESTOS[k].angulo)).negate(), color));
    if (k === 0) p.push(cara([alto[0], alto[3], bajo[3], bajo[0]], new Vector3(1, 0, 0), color));
    if (k === MAQUINAS - 1) p.push(cara([alto[1], alto[2], bajo[2], bajo[1]], new Vector3(1, 0, 0), color));
  }
  // Postes detrás de las máquinas, uno entre cada dos.
  for (let j = 0; j <= MAQUINAS; j++) {
    const a = PUESTOS[0].angulo - PASO_ANGULO / 2 + j * PASO_ANGULO;
    const q = pt(a, R1 - 0.1, 0);
    p.push(pintar(poner(new CylinderGeometry(0.07, 0.08, yB + 0.1, 6), [q.x, yB / 2, q.z]), 0x3a2a22));
    añadirCirculo(q.x, q.z, 0.15);
  }
  return p;
}

/** Marco y postes del letrero del bote (la imagen la pone casino.ts, delante del marco). */
function marcoLetrero(): L {
  const p: L = [];
  const [w, h] = [7, 0.875];
  caja(p, w + 0.3, h + 0.3, 0.12, 0, ALTO_LETRERO, 0, ORO);
  caja(p, w + 0.1, h + 0.1, 0.14, 0, ALTO_LETRERO, 0.01, NEGRO);
  for (const s of [-1, 1]) caja(p, 0.14, ALTO_LETRERO + h / 2, 0.14, s * 2.6, (ALTO_LETRERO + h / 2) / 2, -0.16, 0x3a2a22);
  return p.map((g) => poner(g, [ARCO.x, ARCO.y, ARCO.z], [0, ARCO.yaw, 0]));
}

/** El mueble entero, listo para la escena (y sus colisiones registradas). */
export function crearMueble(): Mesh {
  const piezas: L = [...toldo(), ...marcoLetrero()];
  for (let i = 0; i < MAQUINAS; i++) {
    const pu = PUESTOS[i];
    piezas.push(...maquina(i).map((g) => poner(g, [pu.x, pu.y, pu.z], [0, pu.yaw, 0])));
    const f = frenteDe(i);
    añadirCirculo(pu.x + f.x * 0.2, pu.z + f.z * 0.2, 1.0);
  }
  const malla = new Mesh(fundir(piezas), new MeshLambertMaterial({ vertexColors: true, flatShading: true }));
  malla.castShadow = true;
  malla.receiveShadow = true;
  malla.geometry.computeBoundingSphere();
  return malla;
}
