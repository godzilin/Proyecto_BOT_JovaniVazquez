// Los demás jugadores de la isla: el mismo robot que el tuyo, con su propio color y su nombre encima. Aquí vive todo
// lo que cuesta por jugador y sus topes: solo se dibujan los TOPE más cercanos, a más de LEJOS metros no se animan ni
// llevan nombre, y solo los de más cerca proyectan sombra. Se interpola ~RETRASO ms en el pasado entre las dos
// últimas poses, así que 10 poses por segundo se ven fluidas. Nada de esto reserva memoria en cada fotograma.
import {
  AnimationAction,
  AnimationMixer,
  CanvasTexture,
  Color,
  Group,
  LoopOnce,
  Material,
  Mesh,
  MeshStandardMaterial,
  Scene,
  Sprite,
  SpriteMaterial,
  SRGBColorSpace,
} from "three";
import { clone } from "three/addons/utils/SkeletonUtils.js";
import { gestoDe } from "./gestos";
import { PASO_ANDAR, PASO_CORRER } from "./jugador";
import { MATERIAL_CUERPO, type PlantillaRobot } from "./robot";
import { clamp } from "./ruido";

const TOPE = 12; // robots dibujados a la vez
const LEJOS = 60; // m: más allá, pose quieta y sin nombre
const SOMBRAS = 2; // los que proyectan sombra
const RETRASO = 150; // ms
const SALTO_MAX = 8; // m: más que esto es un teletransporte, no se interpola
const ALTO_NOMBRE = 2.45;
const NOMBRES = ["Idle", "Walking", "Running", "Jump"] as const;
const TAU = Math.PI * 2;

class Remoto {
  id = "";
  nombre = "";
  tienePose = false;
  // Las dos últimas poses y el instante (ms) en que llegaron.
  pt = 0;
  px = 0;
  py = 0;
  pz = 0;
  pyaw = 0;
  panim = 0;
  nt = 0;
  nx = 0;
  ny = 0;
  nz = 0;
  nyaw = 0;
  nanim = 0;
  d2 = 0;
  // Se crean la primera vez que hay que dibujarlo.
  objeto: Group | null = null;
  mixer: AnimationMixer | null = null;
  acciones: AnimationAction[] = [];
  actual = 0;
  sprite: Sprite | null = null;
  meshes: Mesh[] = [];
}

/** Tono del cuerpo sacado del id (el mismo para todos los que lo vean). */
function tono(id: string): number {
  let h = 2166136261;
  for (let i = 0; i < id.length; i++) h = Math.imul(h ^ id.charCodeAt(i), 16777619);
  return ((h >>> 0) % 24) / 24;
}

function porCercania(a: Remoto, b: Remoto): number {
  return a.d2 - b.d2;
}

export class Remotos {
  private readonly todos = new Map<number, Remoto>();
  private readonly orden: Remoto[] = [];
  private readonly materiales = new Map<number, Material>();
  private readonly color = new Color();
  private miSlot = -1;

  constructor(
    private readonly escena: Scene,
    private readonly plantilla: PlantillaRobot,
  ) {}

  /** Cuántos hay conectados (dibujados o no). */
  get cuantos(): number {
    return this.todos.size;
  }

  bienvenida(slot: number, jugadores: { slot: number; id: string; nombre: string }[]): void {
    this.vaciar();
    this.miSlot = slot;
    for (const j of jugadores) this.entra(j.slot, j.id, j.nombre);
  }

  entra(slot: number, id: string, nombre: string): void {
    if (slot === this.miSlot) return;
    const r = this.obtener(slot);
    if (r.id !== id) this.quitarModelo(r); // otro jugador en el mismo hueco: nombre y color nuevos
    r.id = id;
    r.nombre = nombre;
  }

  sale(slot: number): void {
    const r = this.todos.get(slot);
    if (!r) return;
    this.quitarModelo(r);
    this.todos.delete(slot);
    this.orden.splice(this.orden.indexOf(r), 1);
  }

  vaciar(): void {
    for (const r of this.todos.values()) this.quitarModelo(r);
    this.todos.clear();
    this.orden.length = 0;
  }

  /** Pose recibida (metros y radianes). Si llega antes que su `entra`, espera a saber quién es. */
  pose(slot: number, x: number, y: number, z: number, yaw: number, anim: number, ahora: number): void {
    if (slot === this.miSlot) return;
    const r = this.obtener(slot);
    if (!r.tienePose || Math.hypot(x - r.nx, z - r.nz) > SALTO_MAX || Math.abs(y - r.ny) > SALTO_MAX) {
      r.px = x;
      r.py = y;
      r.pz = z;
      r.pyaw = yaw;
      r.panim = anim;
      r.pt = ahora;
      r.tienePose = true;
    } else {
      r.px = r.nx;
      r.py = r.ny;
      r.pz = r.nz;
      r.pyaw = r.nyaw;
      r.panim = r.nanim;
      r.pt = r.nt;
    }
    r.nx = x;
    r.ny = y;
    r.nz = z;
    r.nyaw = yaw;
    r.nanim = anim;
    r.nt = ahora;
    // Tras una pausa (quieto, sin poses) el tramo no puede durar lo que duró la pausa: se supone el ritmo normal.
    if (r.nt - r.pt > 250) r.pt = r.nt - 100;
  }

  /** Coloca, anima y enseña a los TOPE más cercanos a (cx, cz); el resto se oculta. */
  actualizar(dt: number, ahora: number, cx: number, cz: number, sombras: boolean): void {
    const orden = this.orden;
    for (const r of orden) r.d2 = r.tienePose && r.nombre ? (r.nx - cx) ** 2 + (r.nz - cz) ** 2 : Infinity;
    orden.sort(porCercania);
    const t = ahora - RETRASO;
    for (let i = 0; i < orden.length; i++) {
      const r = orden[i];
      if (i >= TOPE || r.d2 === Infinity) {
        if (r.objeto) r.objeto.visible = false;
        continue;
      }
      if (!r.objeto) this.montar(r);
      const o = r.objeto as Group;
      const sprite = r.sprite as Sprite;
      o.visible = true;
      const span = Math.max(r.nt - r.pt, 1);
      const a = clamp((t - r.pt) / span, 0, 1);
      o.position.set(r.px + (r.nx - r.px) * a, r.py + (r.ny - r.py) * a, r.pz + (r.nz - r.pz) * a);
      let dy = r.nyaw - r.pyaw;
      dy -= TAU * Math.round(dy / TAU);
      o.rotation.y = r.pyaw + dy * a;
      const cerca = r.d2 < LEJOS * LEJOS;
      sprite.visible = cerca;
      if (cerca) {
        // Los nombres crecen con la distancia para que se sigan leyendo.
        const k = clamp(Math.sqrt(r.d2) / 12, 0.6, 2.6);
        sprite.scale.set(2.4 * k, 0.6 * k, 1);
        sprite.position.y = ALTO_NOMBRE + 0.12 * k;
        this.animar(r, dt, a, span);
      }
      const proyecta = sombras && i < SOMBRAS;
      for (const m of r.meshes) m.castShadow = proyecta;
    }
  }

  private obtener(slot: number): Remoto {
    let r = this.todos.get(slot);
    if (!r) {
      r = new Remoto();
      this.todos.set(slot, r);
      this.orden.push(r);
    }
    return r;
  }

  private animar(r: Remoto, dt: number, a: number, span: number): void {
    const quiero = a < 0.5 ? r.panim : r.nanim;
    const q = quiero > 10 ? 0 : quiero;
    if (q !== r.actual) {
      const de = r.acciones[r.actual];
      const hacia = r.acciones[q] ?? this.accionGesto(r, q);
      hacia.reset().play();
      hacia.crossFadeFrom(de, q === 3 ? 0.1 : q > 3 ? 0.2 : 0.22, false);
      r.actual = q;
    }
    const v = Math.hypot(r.nx - r.px, r.nz - r.pz) / (span / 1000);
    r.acciones[1].timeScale = clamp(v / PASO_ANDAR, 0.4, 3);
    r.acciones[2].timeScale = clamp(v / PASO_CORRER, 0.5, 3);
    (r.mixer as AnimationMixer).update(dt);
  }

  /** La acción de un gesto (4-10), creada la primera vez que ese jugador lo hace. */
  private accionGesto(r: Remoto, q: number): AnimationAction {
    const g = gestoDe(q) as NonNullable<ReturnType<typeof gestoDe>>;
    const clip = this.plantilla.clips.find((c) => c.name === g.clip);
    if (!clip) throw new Error(`Falta la animación ${g.clip}`);
    const acc = (r.mixer as AnimationMixer).clipAction(clip);
    if (g.modo !== "bucle") {
      acc.setLoop(LoopOnce, 1);
      acc.clampWhenFinished = true;
    }
    return (r.acciones[q] = acc);
  }

  private montar(r: Remoto): void {
    const raiz = clone(this.plantilla.raiz);
    const o = new Group();
    o.add(raiz);
    raiz.traverse((m) => {
      const malla = m as Mesh;
      if (!malla.isMesh) return;
      r.meshes.push(malla);
      if (malla.name === MATERIAL_CUERPO) malla.material = this.materialDe(malla.material as MeshStandardMaterial, tono(r.id));
    });
    const mixer = new AnimationMixer(raiz);
    NOMBRES.forEach((n, i) => {
      const clip = this.plantilla.clips.find((c) => c.name === n);
      if (!clip) throw new Error(`Falta la animación ${n}`);
      const acc = mixer.clipAction(clip);
      if (n === "Jump") {
        acc.setLoop(LoopOnce, 1);
        acc.clampWhenFinished = true;
      }
      r.acciones[i] = acc;
    });
    r.acciones[0].play();
    r.actual = 0;
    r.mixer = mixer;
    r.objeto = o;
    r.sprite = crearNombre(r.nombre, tono(r.id));
    o.add(r.sprite);
    this.escena.add(o);
  }

  /** Un material por tono, compartido por todos los que lo tengan. */
  private materialDe(base: MeshStandardMaterial, t: number): Material {
    const clave = Math.round(t * 24);
    let m = this.materiales.get(clave);
    if (!m) {
      m = base.clone();
      (m as MeshStandardMaterial).color.copy(this.color.setHSL(t, 0.7, 0.5));
      this.materiales.set(clave, m);
    }
    return m;
  }

  private quitarModelo(r: Remoto): void {
    if (!r.objeto) return;
    this.escena.remove(r.objeto);
    r.mixer?.stopAllAction();
    r.sprite?.material.map?.dispose();
    r.sprite?.material.dispose();
    r.objeto = null;
    r.mixer = null;
    r.sprite = null;
    r.acciones.length = 0;
    r.meshes.length = 0;
  }
}

/** El nombre, dibujado una sola vez en un lienzo que hace de textura de un `Sprite`. */
function crearNombre(nombre: string, t: number): Sprite {
  const lienzo = document.createElement("canvas");
  lienzo.width = 256;
  lienzo.height = 64;
  const c = lienzo.getContext("2d") as CanvasRenderingContext2D;
  let px = 34;
  c.font = `bold ${px}px system-ui, sans-serif`;
  while (px > 16 && c.measureText(nombre).width > 224) c.font = `bold ${--px}px system-ui, sans-serif`;
  const ancho = Math.min(250, c.measureText(nombre).width + 30);
  c.fillStyle = "rgba(10,14,24,0.72)";
  c.strokeStyle = `hsl(${Math.round(t * 360)} 70% 60%)`;
  c.lineWidth = 3;
  c.beginPath();
  c.roundRect((256 - ancho) / 2, 6, ancho, 52, 26);
  c.fill();
  c.stroke();
  c.fillStyle = "#fff";
  c.textAlign = "center";
  c.textBaseline = "middle";
  c.fillText(nombre, 128, 33);
  const textura = new CanvasTexture(lienzo);
  textura.colorSpace = SRGBColorSpace;
  const s = new Sprite(new SpriteMaterial({ map: textura, fog: false, depthWrite: false, toneMapped: false }));
  s.renderOrder = 10;
  return s;
}
