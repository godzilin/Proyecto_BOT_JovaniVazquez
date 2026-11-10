// Monta un interior (`Interior` de `formato.ts`) en el sitio de su puerta: la habitación (suelo, cuatro paredes que solo se ven
// desde dentro, para que la cámara en tercera persona mire a través de la que tiene detrás, y la puerta de salida), sus objetos
// fijos fundidos en una sola malla, los usables cada uno en la suya (cambian con su estado) y sus círculos de colisión. Todo va en
// la capa del interior (`mundo/capas.ts`): mientras se está dentro, la cámara solo ve esa capa.
//
// Coste: la habitación, 1 llamada de dibujo; lo fijo de piezas, 1; cada usable, 1; cada GLB, las mallas de su modelo; y la
// pasada de sombras, otro tanto de lo que proyecta sombra (los muebles; la habitación no). El piso de serie, 11 llamadas y ~1.100
// triángulos (con sombras), muy por debajo del presupuesto (120 y 250 000), y el mundo de fuera no se dibuja.
import { type BufferGeometry, Group, type Material, Mesh, MeshBasicMaterial, MeshLambertMaterial, type Object3D, PlaneGeometry } from "three";
import { type Asset, cargarGlb, geometriaAsset, type Interior, type Objeto, type Uso } from "../editor/formato";
import { CAPA_INTERIOR } from "../mundo/capas";
import type { Circulo } from "../mundo/colision";
import type { Caja } from "../mundo/dentro";
import { fundir, pintar, poner } from "../mundo/geom";
import { matriz } from "../mundo/obras";

/** Un usable montado: su número, para qué sirve, dónde está (en el mundo) y lo que lo dibuja. */
export interface UsableMontado {
  n: number;
  uso: Uso;
  nombre: string;
  x: number;
  z: number;
  estado: number;
  objeto: Object3D | null;
  circulos: Circulo[];
}

export interface Montado {
  grupo: Group;
  /** Los círculos con los que se choca ahora (los de un mueble quitado salen); `soloEstos` lee esta misma lista. */
  circulos: Circulo[];
  usables: Map<number, UsableMontado>;
  /** Dónde se aparece al entrar (mirando hacia -z) y dónde está la puerta de salida. */
  entrada: { x: number; z: number };
  salida: { x: number; z: number };
  caja: Caja;
  /** Cambia el estado de un usable (lo que se ve y si choca). */
  poner(n: number, estado: number): void;
  soltar(): void;
}

const material = new MeshLambertMaterial({ vertexColors: true, flatShading: true });
/** Un mueble quitado: su silueta, para saber dónde volver a ponerlo. */
const fantasma = new MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.2, depthWrite: false });
/** Algo roto: más oscuro. */
const roto = new MeshLambertMaterial({ vertexColors: true, flatShading: true, color: 0x4a4a4a });
const PUERTA = 0x5b3a29;
/** Lo que se separa el jugador de las paredes (su radio) y lo que mide de pies a cabeza (para el techo). */
const RADIO = 0.35;
const ALTO_ROBOT = 1.7;

/** Un objeto del interior en coordenadas del mundo. */
const enMundo = (o: Objeto, origen: [number, number, number]): Objeto => ({ ...o, pos: [origen[0] + o.pos[0], origen[1] + o.pos[1], origen[2] + o.pos[2]] });

/** La geometría de la habitación: el suelo, las paredes mirando hacia dentro y la puerta de salida en la pared +z. */
function habitacion(i: Interior, [ox, oy, oz]: [number, number, number]): BufferGeometry {
  const a = i.ancho / 2;
  const f = i.fondo / 2;
  const h = i.alto;
  const pared = (ancho: number, x: number, z: number, giro: number): BufferGeometry => poner(pintar(new PlaneGeometry(ancho, h), i.pared), [ox + x, oy + h / 2, oz + z], [0, giro, 0]);
  return fundir([
    poner(pintar(new PlaneGeometry(i.ancho, i.fondo), i.suelo), [ox, oy, oz], [-Math.PI / 2, 0, 0]),
    pared(i.ancho, 0, -f, 0),
    pared(i.ancho, 0, f, Math.PI),
    pared(i.fondo, -a, 0, Math.PI / 2),
    pared(i.fondo, a, 0, -Math.PI / 2),
    poner(pintar(new PlaneGeometry(1.3, 2.3), PUERTA), [ox, oy + 1.15, oz + f - 0.02], [0, Math.PI, 0]),
  ]);
}

/** Los círculos de colisión de un objeto ya en el mundo (como `Obras.colisiones`). */
function circulosDe(a: Asset, o: Objeto): Circulo[] {
  if (!a.colision) return [];
  const cos = Math.cos(o.rot[1]);
  const sin = Math.sin(o.rot[1]);
  const radio = Math.max(o.esc[0], o.esc[2]);
  return a.colision.map(([cx, cz, rc]) => {
    const x = cx * o.esc[0];
    const z = cz * o.esc[2];
    return { x: o.pos[0] + x * cos + z * sin, z: o.pos[2] - x * sin + z * cos, r: rc * radio };
  });
}

/** Pone en la capa del interior y con sombras todo lo de `o`. */
function enCapa(o: Object3D, sombra: boolean): void {
  o.traverse((n) => {
    n.layers.set(CAPA_INTERIOR);
    n.castShadow = sombra;
    n.receiveShadow = true;
  });
}

/**
 * Monta `interior` con el centro del suelo en `origen` (el sitio de la puerta), con los assets que usa y el estado de cada usable
 * (`estados`, número → 0 | 1; uno que no venga, el de serie). Los GLB llegan cuando se bajan.
 */
export function montar(interior: Interior, assets: Record<string, Asset>, origen: [number, number, number], estados: Record<string, number>): Montado {
  const grupo = new Group();
  const geos: BufferGeometry[] = [];
  const geoAsset = new Map<string, BufferGeometry>();
  const fijos: Circulo[] = [];
  const usables = new Map<number, UsableMontado>();
  let vivo = true;
  const base = (id: string, a: Extract<Asset, { tipo: "piezas" }>): BufferGeometry => {
    let g = geoAsset.get(id);
    if (!g) geoAsset.set(id, (g = geometriaAsset(a)));
    return g;
  };
  const copia = (id: string, a: Extract<Asset, { tipo: "piezas" }>, o: Objeto): BufferGeometry => {
    const c = base(id, a).clone().applyMatrix4(matriz(o));
    if (o.tinte !== undefined && o.tinte !== 1) {
      const col = c.getAttribute("color");
      for (let i = 0; i < col.count; i++) col.setXYZ(i, col.getX(i) * o.tinte, col.getY(i) * o.tinte, col.getZ(i) * o.tinte);
    }
    return c;
  };
  for (const local of interior.objetos) {
    const a = assets[local.asset];
    if (!a) continue;
    const o = enMundo(local, origen);
    const f = a.funcion;
    if (f?.tipo === "usable" && local.datos?.n !== undefined) {
      const u: UsableMontado = { n: local.datos.n, uso: f.uso, nombre: a.nombre, x: o.pos[0], z: o.pos[2], estado: 1, objeto: null, circulos: circulosDe(a, o) };
      usables.set(u.n, u);
      if (a.tipo === "piezas") {
        u.objeto = new Mesh(copia(local.asset, a, o), material);
        grupo.add(u.objeto);
      } else {
        void cargarGlb(a.glb).then((m) => {
          if (!vivo) return;
          const copiaGlb = m.clone(true);
          copiaGlb.applyMatrix4(matriz(o));
          enCapa(copiaGlb, true);
          u.objeto = copiaGlb;
          grupo.add(copiaGlb);
          ponerEstado(u, u.estado);
        });
      }
      continue;
    }
    fijos.push(...circulosDe(a, o));
    if (a.tipo === "piezas") geos.push(copia(local.asset, a, o));
    else {
      void cargarGlb(a.glb).then((m) => {
        if (!vivo) return;
        const c = m.clone(true);
        c.applyMatrix4(matriz(o));
        enCapa(c, true);
        grupo.add(c);
      });
    }
  }
  for (const g of geoAsset.values()) g.dispose();
  const sala = new Mesh(habitacion(interior, origen), material);
  enCapa(sala, false);
  grupo.add(sala);
  if (geos.length) {
    const resto = new Mesh(fundir(geos), material);
    enCapa(resto, true);
    grupo.add(resto);
  }
  for (const u of usables.values()) if (u.objeto) enCapa(u.objeto, true);

  const circulos: Circulo[] = [];
  const rehacerCirculos = (): void => {
    circulos.length = 0;
    circulos.push(...fijos);
    for (const u of usables.values()) if (u.uso !== "mueble" || u.estado === 1) circulos.push(...u.circulos);
  };
  /** Lo que se ve de un usable según su estado: un mueble quitado, en silueta (un GLB, sin dibujar); algo roto, más oscuro. */
  function ponerEstado(u: UsableMontado, estado: number): void {
    u.estado = estado;
    const o = u.objeto;
    if (!o) return;
    if ((o as Mesh).isMesh) {
      const m = o as Mesh;
      const quitado = u.uso === "mueble" && estado === 0;
      m.material = (quitado ? fantasma : u.uso === "reparar" && estado === 0 ? roto : material) as Material;
      m.castShadow = !quitado;
    } else o.visible = !(u.uso === "mueble" && estado === 0);
  }
  for (const u of usables.values()) ponerEstado(u, estados[String(u.n)] ?? (u.uso === "mueble" ? 1 : 0));
  rehacerCirculos();

  const [ox, oy, oz] = origen;
  const a = interior.ancho / 2;
  const f = interior.fondo / 2;
  return {
    grupo,
    circulos,
    usables,
    entrada: { x: ox, z: oz + f - 1.4 },
    salida: { x: ox, z: oz + f - 0.4 },
    caja: { x0: ox - a + RADIO, x1: ox + a - RADIO, z0: oz - f + RADIO, z1: oz + f - RADIO, y: oy, techo: oy + Math.max(0, interior.alto - ALTO_ROBOT) },
    poner(n, estado) {
      const u = usables.get(n);
      if (!u) return;
      ponerEstado(u, estado);
      rehacerCirculos();
    },
    soltar() {
      vivo = false;
      grupo.removeFromParent();
      grupo.traverse((n) => {
        const m = n as Mesh;
        // Las geometrías de los GLB son de su modelo (en caché): solo se sueltan las nuestras.
        if (m.isMesh && m.material !== undefined && (m.material === material || m.material === fantasma || m.material === roto)) m.geometry.dispose();
      });
    },
  };
}
