// Los demás jugadores de la isla: el mismo robot que el tuyo, con su propio color y su nombre encima. Aquí vive todo
// lo que cuesta por jugador y sus topes: solo se dibujan los TOPE más cercanos, a más de LEJOS metros no se animan ni
// llevan nombre, y solo los de más cerca proyectan sombra. Se dibuja RETRASO ms en el pasado sobre las últimas poses
// (`Estela`), así que 10 poses por segundo se ven fluidas aunque la red se salte una. Nada de esto reserva memoria en
// cada fotograma.
import {
  AnimationAction,
  AnimationMixer,
  CanvasTexture,
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
import { Estela } from "./estela";
import { gestoDe } from "./gestos";
import { ANIMS, PASO_ANDAR, PASO_CORRER, POSTURAS } from "./jugador";
import { MATERIAL_CUERPO, colorCuerpo, tonoDe, type PlantillaRobot } from "./robot";
import { clamp } from "./ruido";

const TOPE = 12; // robots dibujados a la vez
const LEJOS = 60; // m: más allá, pose quieta y sin nombre
const SOMBRAS = 2; // los que proyectan sombra
const RETRASO = 250; // ms: dos ticks y medio, para que un tick perdido o con retraso no pare al robot
const ALTO_NOMBRE = 2.45;
const NOMBRES = ["Idle", "Walking", "Running", "Jump"] as const;

class Remoto {
  id = "";
  nombre = "";
  readonly estela = new Estela();
  d2 = 0;
  // Se crean la primera vez que hay que dibujarlo.
  objeto: Group | null = null;
  mixer: AnimationMixer | null = null;
  acciones: AnimationAction[] = [];
  actual = 0;
  sprite: Sprite | null = null;
  meshes: Mesh[] = [];
}

function porCercania(a: Remoto, b: Remoto): number {
  return a.d2 - b.d2;
}

export class Remotos {
  private readonly todos = new Map<number, Remoto>();
  private readonly orden: Remoto[] = [];
  private readonly materiales = new Map<number, Material>();
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

  /** Ha salido de lo que te manda la sala: deja de dibujarse (sigue en la sala) hasta su próxima pose. */
  lejos(slot: number): void {
    this.todos.get(slot)?.estela.vaciar();
  }

  /** Tras un mensaje con todo lo que ves: los que no venían en él dejan de dibujarse. */
  dejarSolo(vistos: Set<number>): void {
    for (const [slot, r] of this.todos) if (!vistos.has(slot)) r.estela.vaciar();
  }

  /** Pose recibida (metros y radianes). Si llega antes que su `entra`, espera a saber quién es. */
  pose(slot: number, x: number, y: number, z: number, yaw: number, anim: number, ahora: number): void {
    if (slot === this.miSlot) return;
    this.obtener(slot).estela.meter(x, y, z, yaw, anim, ahora);
  }

  /** Coloca, anima y enseña a los TOPE más cercanos a (cx, cz); el resto se oculta. */
  actualizar(dt: number, ahora: number, cx: number, cz: number, sombras: boolean): void {
    const orden = this.orden;
    for (const r of orden) r.d2 = !r.estela.vacia && r.nombre ? (r.estela.ultimaX - cx) ** 2 + (r.estela.ultimaZ - cz) ** 2 : Infinity;
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
      const e = r.estela;
      e.muestrear(t);
      o.position.set(e.x, e.y, e.z);
      o.rotation.y = e.yaw;
      const cerca = r.d2 < LEJOS * LEJOS;
      sprite.visible = cerca;
      if (cerca) {
        // Los nombres crecen con la distancia para que se sigan leyendo.
        const k = clamp(Math.sqrt(r.d2) / 12, 0.6, 2.6);
        sprite.scale.set(2.4 * k, 0.6 * k, 1);
        sprite.position.y = ALTO_NOMBRE + 0.12 * k;
        this.animar(r, dt);
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

  private animar(r: Remoto, dt: number): void {
    const quiero = r.estela.anim;
    const q = quiero > ANIMS.Deslizar ? ANIMS.Idle : quiero;
    if (q !== r.actual) {
      const de = r.acciones[r.actual];
      const hacia = r.acciones[q] ?? this.accionExtra(r, q);
      hacia.reset().play();
      hacia.crossFadeFrom(de, q === ANIMS.Jump || q === ANIMS.Deslizar ? 0.1 : q > ANIMS.Jump ? 0.2 : 0.22, false);
      r.actual = q;
    }
    const v = r.estela.v;
    r.acciones[1].timeScale = clamp(v / PASO_ANDAR, 0.4, 3);
    r.acciones[2].timeScale = clamp(v / PASO_CORRER, 0.5, 3);
    (r.mixer as AnimationMixer).update(dt);
  }

  /** La acción de un gesto (4-10) o una postura (11-12), creada la primera vez que ese jugador la hace. */
  private accionExtra(r: Remoto, q: number): AnimationAction {
    const g = gestoDe(q);
    const nombre = g?.clip ?? POSTURAS[q];
    const clip = this.plantilla.clips.find((c) => c.name === nombre);
    if (!clip) throw new Error(`Falta la animación ${nombre}`);
    const acc = (r.mixer as AnimationMixer).clipAction(clip);
    if (g && g.modo !== "bucle") {
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
      if (malla.name === MATERIAL_CUERPO) malla.material = this.materialDe(malla.material as MeshStandardMaterial, tonoDe(r.id));
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
    r.sprite = crearNombre(r.nombre, tonoDe(r.id));
    o.add(r.sprite);
    this.escena.add(o);
  }

  /** Un material por tono, compartido por todos los que lo tengan. */
  private materialDe(base: MeshStandardMaterial, t: number): Material {
    const clave = Math.round(t * 24);
    let m = this.materiales.get(clave);
    if (!m) {
      m = base.clone();
      colorCuerpo(t, (m as MeshStandardMaterial).color);
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
