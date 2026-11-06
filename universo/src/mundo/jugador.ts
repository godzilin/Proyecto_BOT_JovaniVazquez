// El jugador: el robot (modelo glb con animaciones) y su movimiento: velocidad, gravedad, salto, pendientes,
// colisiones con círculos y pared invisible en el mar. Las unidades son metros y segundos.
import { AnimationAction, AnimationClip, AnimationMixer, Box3, Group, LoopOnce, Mesh, MeshStandardMaterial, Object3D, Vector3 } from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { empujar } from "./colision";
import { clamp } from "./ruido";
import { SUELO_AGUA, PLAZA, enMuelle, pendienteEn, sueloEn, terrenoEn } from "./terreno";
import robotUrl from "../modelos/robot.glb?url";

export const VEL_ANDAR = 5;
export const VEL_CORRER = 9; // el servidor recorta a 12 m/s: nunca pasar de 10
const GRAVEDAD = 20;
const VEL_SALTO = Math.sqrt(2 * GRAVEDAD * 1.3); // 1,3 m de altura
const RADIO = 0.45;
const ALTO_ROBOT = 1.9;
// Velocidad (m/s) a la que el clip, a escala 1, no hace patinar los pies. Medida a ojo en el modelo; ajustable.
export const PASO_ANDAR = 2.1;
export const PASO_CORRER = 4.2;

export interface Entrada {
  /** Dirección deseada ya en el mundo (x, z) y fuerza 0..1. */
  dx: number;
  dz: number;
  fuerza: number;
  correr: boolean;
  saltar: boolean;
}

export class Jugador {
  readonly objeto = new Group();
  x = PLAZA.x + 4;
  z = PLAZA.z + 6;
  y = 0;
  vy = 0;
  velH = 0;
  suelo = true;
  giro = 0;
  /** Metros recorridos sin informar y saltos hechos. */
  metros = 0;
  saltos = 0;

  private vx = 0;
  private vz = 0;
  private mixer: AnimationMixer;
  private acciones: Record<string, AnimationAction> = {};
  private actual = "Idle";
  private tmp = { x: 0, z: 0 };

  private constructor(
    readonly modelo: Object3D,
    readonly clips: AnimationClip[],
  ) {
    modelo.updateMatrixWorld(true);
    const caja = new Box3().setFromObject(modelo);
    const esc = ALTO_ROBOT / (caja.max.y - caja.min.y);
    modelo.scale.setScalar(esc);
    modelo.position.y = -caja.min.y * esc;
    modelo.traverse((o) => {
      const m = o as Mesh;
      if (m.isMesh) {
        m.castShadow = true;
        m.frustumCulled = false;
        const mat = m.material as MeshStandardMaterial;
        if (mat.metalness !== undefined) {
          mat.metalness = Math.min(mat.metalness, 0.15);
          mat.roughness = Math.max(mat.roughness, 0.6);
        }
      }
    });
    this.objeto.add(modelo);
    this.mixer = new AnimationMixer(modelo);
    for (const nombre of ["Idle", "Walking", "Running", "Jump"]) {
      const clip = clips.find((c) => c.name === nombre);
      if (!clip) throw new Error(`Falta la animación ${nombre}`);
      const a = this.mixer.clipAction(clip);
      if (nombre === "Jump") {
        a.setLoop(LoopOnce, 1);
        a.clampWhenFinished = true;
      }
      this.acciones[nombre] = a;
    }
    this.acciones.Idle.play();
    this.y = sueloEn(this.x, this.z);
    this.colocar();
  }

  static async cargar(): Promise<Jugador> {
    const gltf = await new GLTFLoader().loadAsync(robotUrl);
    return new Jugador(gltf.scene, gltf.animations);
  }

  teletransportar(x: number, z: number): void {
    this.x = x;
    this.z = z;
    this.y = sueloEn(x, z);
    this.vy = 0;
    this.vx = this.vz = 0;
    this.suelo = true;
    this.colocar();
  }

  /** ¿Se puede estar en (x, z)? Mar profundo y laderas imposibles lo impiden salvo que se vaya a menos profundidad. */
  private permitido(x: number, z: number, desdeX: number, desdeZ: number): boolean {
    const t = terrenoEn(x, z);
    if (t < SUELO_AGUA && !enMuelle(x, z) && t < terrenoEn(desdeX, desdeZ)) return false;
    // Paredes (acantilados): no se sube ni se baja por pendientes de más de ~56º.
    if (!enMuelle(x, z)) {
      const p = pendienteEn(x, z);
      if (p > 1.5 && p > pendienteEn(desdeX, desdeZ)) return false;
    }
    return true;
  }

  actualizar(dt: number, e: Entrada): void {
    const antesX = this.x;
    const antesZ = this.z;

    // Velocidad objetivo: analógica al andar, fija al correr.
    let vel = e.fuerza > 0.02 ? (e.correr ? VEL_CORRER : VEL_ANDAR * clamp(e.fuerza / 0.85, 0, 1)) : 0;
    if (vel > 0) {
      const pend = (sueloEn(this.x + e.dx, this.z + e.dz) - sueloEn(this.x, this.z)) / 1;
      if (pend > 0.55) vel *= 1 - clamp((pend - 0.55) / 0.9, 0, 0.85);
    }
    if (this.y < -0.1) vel *= 1 - 0.5 * clamp(-this.y / 0.9, 0, 1); // vadear cuesta
    const tx = e.dx * vel;
    const tz = e.dz * vel;
    const k = 1 - Math.exp(-dt * (this.suelo ? 12 : 3));
    this.vx += (tx - this.vx) * k;
    this.vz += (tz - this.vz) * k;

    // Mover por ejes por separado para poder deslizar contra la pared del mar.
    const nx = this.x + this.vx * dt;
    const nz = this.z + this.vz * dt;
    if (this.permitido(nx, this.z, this.x, this.z)) this.x = nx;
    else this.vx = 0;
    if (this.permitido(this.x, nz, this.x, this.z)) this.z = nz;
    else this.vz = 0;
    empujar(this.x, this.z, RADIO, this.tmp);
    this.x = this.tmp.x;
    this.z = this.tmp.z;

    // Vertical.
    const suelo = sueloEn(this.x, this.z);
    if (e.saltar && this.suelo) {
      this.vy = VEL_SALTO;
      this.suelo = false;
      this.saltos++;
      this.saltarAnim();
    }
    if (!this.suelo) {
      this.vy -= GRAVEDAD * dt;
      this.y += this.vy * dt;
      if (this.y <= suelo && this.vy <= 0) {
        this.y = suelo;
        this.vy = 0;
        this.suelo = true;
      }
    } else if (this.y - suelo > 0.35) {
      this.suelo = false; // se cae por un borde
    } else {
      this.y = suelo;
    }

    const movido = Math.hypot(this.x - antesX, this.z - antesZ);
    this.velH = movido / Math.max(dt, 1e-4);
    this.metros += movido;
    if (this.velH > 0.3) {
      const objetivo = Math.atan2(this.vx, this.vz);
      let d = objetivo - this.giro;
      d = Math.atan2(Math.sin(d), Math.cos(d));
      this.giro += d * (1 - Math.exp(-dt * 14));
    }
    this.colocar();
    this.animar(dt);
  }

  private colocar(): void {
    this.objeto.position.set(this.x, this.y, this.z);
    this.objeto.rotation.y = this.giro;
  }

  private saltarAnim(): void {
    const a = this.acciones.Jump;
    a.reset().play();
    this.fundir("Jump", 0.1);
  }

  private fundir(nombre: string, dur: number): void {
    if (nombre === this.actual) return;
    const de = this.acciones[this.actual];
    const a = this.acciones[nombre];
    if (nombre !== "Jump") a.reset().play();
    a.enabled = true;
    a.crossFadeFrom(de, dur, false);
    this.actual = nombre;
  }

  private animar(dt: number): void {
    let quiero: string;
    if (!this.suelo) quiero = "Jump";
    else if (this.velH > 7) quiero = "Running";
    else if (this.velH > 0.4) quiero = "Walking";
    else quiero = "Idle";
    if (quiero === "Jump" && this.actual !== "Jump") this.saltarAnim();
    else if (quiero !== "Jump") this.fundir(quiero, 0.22);
    this.acciones.Walking.timeScale = clamp(this.velH / PASO_ANDAR, 0.4, 3);
    this.acciones.Running.timeScale = clamp(this.velH / PASO_CORRER, 0.5, 3);
    this.mixer.update(dt);
  }

  /** Animación que se manda a los demás: 0 quieto, 1 andar, 2 correr, 3 saltar. */
  get anim(): number {
    return { Idle: 0, Walking: 1, Running: 2, Jump: 3 }[this.actual] ?? 0;
  }

  get posicion(): Vector3 {
    return this.objeto.position;
  }
}
