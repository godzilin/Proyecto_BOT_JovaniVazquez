// El jugador: el robot (modelo glb con animaciones) y su movimiento: velocidad, gravedad, salto, pendientes,
// colisiones con círculos, pared invisible en el mar y el borde del mundo (`borde.ts`). Las unidades son metros y segundos.
// Movimiento con técnica: agacharse corriendo es deslizarse (con impulso cada DESLIZ_RECARGA s y que acelera cuesta
// abajo), saltar desde el deslizamiento o nada más caer conserva la velocidad (bunny hop), en el aire se gira sin
// frenar (air strafe), y el salto perdona un poco tarde (coyote) y un poco pronto (buffer).
import {
  AnimationAction,
  AnimationClip,
  AnimationMixer,
  AnimationUtils,
  Box3,
  Color,
  Group,
  LoopOnce,
  Mesh,
  MeshStandardMaterial,
  Object3D,
  Vector3,
} from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { encerrar } from "./borde";
import { empujar } from "./colision";
import { GESTOS, gestoDe } from "./gestos";
import { clamp } from "./ruido";
import { SUELO_AGUA, PLAZA, enMuelle, pendienteEn, sueloEn, terrenoEn } from "./terreno";
import { MATERIAL_CUERPO, colorCuerpo, tonoDe } from "./robot";
import robotUrl from "../modelos/robot.glb?url";

export const VEL_ANDAR = 5;
export const VEL_CORRER = 9; // el servidor recorta a 12 m/s: nunca pasar de 10
const GRAVEDAD = 20;
const VEL_SALTO = Math.sqrt(2 * GRAVEDAD * 1.3); // 1,3 m de altura
const RADIO = 0.45;
const ALTO_ROBOT = 1.9;
const VEL_VUELO = 6; // m/s al subir o bajar volando, por la velocidad de las devtools
// Velocidad (m/s) a la que el clip, a escala 1, no hace patinar los pies. Medida a ojo en el modelo; ajustable.
export const PASO_ANDAR = 2.1;
export const PASO_CORRER = 4.2;
const VEL_AGACHADO = 2.5;
const VEL_MAX = 15; // tope con impulsos encadenados; el paseo para los logros se recorta a 12 m/s de media
const DESLIZ_MIN = 6; // m/s para que agacharse sea deslizarse
const DESLIZ_IMPULSO = 3.5;
const DESLIZ_RECARGA = 1; // s entre impulsos (y la sala cuenta como mucho un deslizamiento por segundo)
const DESLIZ_ROCE = 6; // m/s² en llano
const DESLIZ_CUESTA = 14; // m/s² por unidad de pendiente: cuesta abajo acelera, cuesta arriba frena
const DESLIZ_GIRO = 1.8; // rad/s que se puede torcer deslizándose
const AIRE_ACEL = 14; // m/s² de control en el aire, sin pasar de la velocidad que ya llevas
const GRACIA_BHOP = 0.12; // s tras caer sin roce: saltar ahí conserva la velocidad
const COYOTE = 0.12; // s tras dejar el suelo en los que aún se puede saltar
const BUFFER = 0.15; // s antes de tocar el suelo en los que el salto pulsado se guarda

/** Animaciones de moverse que no son clips del modelo: una pose sacada de otro clip (`cargar`). Sus ids de la sala. */
export const POSTURAS: Readonly<Record<number, string>> = { 11: "Agachado", 12: "Deslizar" };

export interface Entrada {
  /** Dirección deseada ya en el mundo (x, z) y fuerza 0..1. */
  dx: number;
  dz: number;
  fuerza: number;
  correr: boolean;
  saltar: boolean;
  /** Se mantiene pulsado: agachado, o deslizándose si se va rápido. */
  agachar: boolean;
}

export class Jugador {
  readonly objeto = new Group();
  // Cada uno aparece en un punto algo distinto de la plaza para no salir unos encima de otros.
  x = PLAZA.x + 4 + (Math.random() - 0.5) * 6;
  z = PLAZA.z + 6 + (Math.random() - 0.5) * 6;
  y = 0;
  vy = 0;
  velH = 0;
  suelo = true;
  giro = 0;
  /** Metros recorridos sin informar y saltos hechos. */
  metros = 0;
  saltos = 0;
  deslizamientos = 0;
  agachado = false;
  deslizando = false;
  /** Cuánto bajan los ojos (m), suavizado: la cámara lo resta. */
  bajada = 0;
  // Devtools (solo desarrolladores): volar sin gravedad, atravesarlo todo y multiplicar la velocidad. Con cualquiera
  // puesto no se cuentan metros ni saltos: no son paseo para los logros.
  volar = false;
  noclip = false;
  velocidad = 1;
  /** Volando: 1 sube, -1 baja, 0 se queda a la misma altura. */
  subir = 0;

  private vx = 0;
  private vz = 0;
  private mixer: AnimationMixer;
  private acciones: Record<string, AnimationAction> = {};
  private actual = "Idle";
  /** Id (4-10) del gesto en marcha, o 0. */
  private enGesto = 0;
  private tmp = { x: 0, z: 0 };
  private recarga = 0;
  private coyote = 0;
  private buffer = 0;
  private desdeCaida = 1;

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
    for (const nombre of ["Idle", "Walking", "Running", "Jump", ...Object.values(POSTURAS)]) {
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
    // El modelo no trae agacharse ni deslizarse: son un fotograma de otro clip (a 100 fps). Agachado, el impulso del
    // salto (0,10 s); deslizándose, la caída de espaldas con los pies por delante (0,25 s de Death).
    const pose = (de: string, nombre: string, f: number): AnimationClip => {
      const clip = gltf.animations.find((c) => c.name === de);
      if (!clip) throw new Error(`Falta la animación ${de}`);
      return AnimationUtils.subclip(clip, nombre, f, f + 1, 100);
    };
    gltf.animations.push(pose("Jump", "Agachado", 10), pose("Death", "Deslizar", 25));
    return new Jugador(gltf.scene, gltf.animations);
  }

  /** Tiñe tu robot del color con que te ven los demás (sale de tu id, como en `Remotos`). */
  colorear(id: string): void {
    const color = colorCuerpo(tonoDe(id), new Color());
    const nuevos = new Map<MeshStandardMaterial, MeshStandardMaterial>();
    this.modelo.traverse((o) => {
      const m = o as Mesh;
      const mat = m.material as MeshStandardMaterial;
      if (!m.isMesh || mat.name !== MATERIAL_CUERPO) return;
      // Copia: el original lo comparte la plantilla de los demás robots.
      let n = nuevos.get(mat);
      if (!n) nuevos.set(mat, (n = mat.clone()));
      n.color.copy(color);
      m.material = n;
    });
  }

  teletransportar(x: number, z: number): void {
    this.x = x;
    this.z = z;
    encerrar(this);
    this.y = sueloEn(this.x, this.z);
    this.vy = 0;
    this.vx = this.vz = 0;
    this.suelo = true;
    this.deslizando = false;
    this.colocar();
  }

  /** Si hay alguna devtool puesta. */
  get trucos(): boolean {
    return this.volar || this.noclip || this.velocidad !== 1;
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

    const vuela = this.volar || this.noclip;
    const mueve = e.fuerza > 0.02;
    this.recarga = Math.max(0, this.recarga - dt);
    this.coyote = this.suelo ? COYOTE : Math.max(0, this.coyote - dt);
    this.buffer = e.saltar ? BUFFER : Math.max(0, this.buffer - dt);
    this.desdeCaida += dt;
    this.agachado = e.agachar && !vuela;
    let rapidez = Math.hypot(this.vx, this.vz);

    // Agacharse en el suelo yendo rápido (o caer agachado) empieza a deslizarse.
    if (this.agachado && this.suelo && !this.deslizando && rapidez >= DESLIZ_MIN && this.y > -0.1) {
      this.deslizando = true;
      if (this.recarga === 0) {
        const k = Math.min(rapidez + DESLIZ_IMPULSO, VEL_MAX) / rapidez;
        this.vx *= k;
        this.vz *= k;
        rapidez *= k;
        this.recarga = DESLIZ_RECARGA;
      }
      if (!this.trucos) this.deslizamientos++;
    }

    if (this.deslizando) {
      // Se tuerce poco, la cuesta empuja y el roce frena. Se acaba al soltar, al frenar o en el agua.
      if (mueve && rapidez > 0.1) {
        const rumbo = Math.atan2(this.vx, this.vz);
        let d = Math.atan2(e.dx, e.dz) - rumbo;
        d = Math.atan2(Math.sin(d), Math.cos(d));
        const a = rumbo + clamp(d, -DESLIZ_GIRO * dt, DESLIZ_GIRO * dt);
        this.vx = Math.sin(a) * rapidez;
        this.vz = Math.cos(a) * rapidez;
      }
      this.vx -= (sueloEn(this.x + 0.5, this.z) - sueloEn(this.x - 0.5, this.z)) * DESLIZ_CUESTA * dt;
      this.vz -= (sueloEn(this.x, this.z + 0.5) - sueloEn(this.x, this.z - 0.5)) * DESLIZ_CUESTA * dt;
      rapidez = Math.hypot(this.vx, this.vz);
      const k = Math.max(0, rapidez - DESLIZ_ROCE * dt) / Math.max(rapidez, 1e-4);
      this.vx *= k;
      this.vz *= k;
      rapidez *= k;
      if (!this.agachado || rapidez < VEL_AGACHADO + 0.5 || this.y < -0.1) this.deslizando = false;
    } else {
      // Velocidad objetivo: analógica al andar y agachado, fija al correr.
      const tope = this.agachado ? VEL_AGACHADO : e.correr ? VEL_CORRER : VEL_ANDAR;
      let vel = mueve ? (e.correr && !this.agachado ? tope : tope * clamp(e.fuerza / 0.85, 0, 1)) * this.velocidad : 0;
      if (vel > 0 && !vuela) {
        const pend = (sueloEn(this.x + e.dx, this.z + e.dz) - sueloEn(this.x, this.z)) / 1;
        if (pend > 0.55) vel *= 1 - clamp((pend - 0.55) / 0.9, 0, 0.85);
      }
      if (this.y < -0.1 && !vuela) vel *= 1 - 0.5 * clamp(-this.y / 0.9, 0, 1); // vadear cuesta
      if (this.suelo || vuela) {
        // Por encima de la velocidad objetivo (tras deslizarse o caer de un salto) se frena más despacio, y nada
        // justo al caer: si se vuelve a saltar ahí, se conserva.
        const sobra = !vuela && rapidez > vel + 0.1;
        if (!sobra || this.desdeCaida > GRACIA_BHOP) {
          const k = 1 - Math.exp(-dt * (sobra ? 4 : 12));
          this.vx += (e.dx * vel - this.vx) * k;
          this.vz += (e.dz * vel - this.vz) * k;
        }
      } else if (mueve) {
        // En el aire se tuerce hacia donde empujas sin ganar más velocidad de la que llevabas (o de la de andar).
        const nx = this.vx + e.dx * AIRE_ACEL * dt;
        const nz = this.vz + e.dz * AIRE_ACEL * dt;
        const k = Math.min(1, Math.max(rapidez, vel) / Math.max(Math.hypot(nx, nz), 1e-4));
        this.vx = nx * k;
        this.vz = nz * k;
      }
    }
    if (!this.trucos) {
      rapidez = Math.hypot(this.vx, this.vz);
      if (rapidez > VEL_MAX) {
        this.vx *= VEL_MAX / rapidez;
        this.vz *= VEL_MAX / rapidez;
      }
    }

    // Mover por ejes por separado para poder deslizar contra la pared del mar.
    const nx = this.x + this.vx * dt;
    const nz = this.z + this.vz * dt;
    if (this.noclip) {
      this.x = nx;
      this.z = nz;
    } else {
      if (this.permitido(nx, this.z, this.x, this.z)) this.x = nx;
      else this.vx = 0;
      if (this.permitido(this.x, nz, this.x, this.z)) this.z = nz;
      else this.vz = 0;
      empujar(this.x, this.z, RADIO, this.tmp);
      this.x = this.tmp.x;
      this.z = this.tmp.z;
    }

    // Vertical.
    const suelo = sueloEn(this.x, this.z);
    if (vuela) {
      // Sin gravedad; el robot anda en el aire (anima como en el suelo). Sin noclip, el suelo sigue parando.
      this.vy = this.subir * VEL_VUELO * this.velocidad;
      this.y += this.vy * dt;
      if (!this.noclip && this.y < suelo) this.y = suelo;
      this.suelo = true;
    } else {
      if (this.buffer > 0 && this.coyote > 0) {
        this.vy = VEL_SALTO;
        this.suelo = false;
        this.buffer = this.coyote = 0;
        this.deslizando = false; // saltar deslizándose conserva la velocidad
        if (!this.trucos) this.saltos++;
        this.saltarAnim();
      }
      if (!this.suelo) {
        this.vy -= GRAVEDAD * dt;
        this.y += this.vy * dt;
        if (this.y <= suelo && this.vy <= 0) {
          this.y = suelo;
          this.vy = 0;
          this.suelo = true;
          this.desdeCaida = 0;
        }
      } else if (this.y - suelo > 0.35) {
        this.suelo = false; // se cae por un borde (o acaba de dejar de volar)
        this.vy = 0;
      } else {
        this.y = suelo;
      }
    }

    encerrar(this); // también volando y con noclip: la sala no acepta poses de fuera
    const movido = Math.hypot(this.x - antesX, this.z - antesZ);
    this.velH = movido / Math.max(dt, 1e-4);
    if (!this.trucos) this.metros += movido;
    if (this.velH > 0.3) {
      const objetivo = Math.atan2(this.vx, this.vz);
      let d = objetivo - this.giro;
      d = Math.atan2(Math.sin(d), Math.cos(d));
      this.giro += d * (1 - Math.exp(-dt * 14));
    }
    this.bajada += ((this.deslizando ? 0.9 : this.agachado ? 0.55 : 0) - this.bajada) * (1 - Math.exp(-dt * 12));
    this.colocar();
    this.animar(dt, mueve);
  }

  /** Hace un gesto (4-10) si estás en el suelo. Moverte o saltar lo corta. */
  gesto(id: number): void {
    const g = gestoDe(id);
    if (!g || !this.suelo) return;
    let a = this.acciones[g.clip];
    if (!a) {
      const clip = this.clips.find((c) => c.name === g.clip);
      if (!clip) return;
      a = this.acciones[g.clip] = this.mixer.clipAction(clip);
      if (g.modo !== "bucle") {
        a.setLoop(LoopOnce, 1);
        a.clampWhenFinished = true;
      }
    }
    if (this.actual === g.clip) a.reset().play();
    else this.fundir(g.clip, 0.2);
    this.enGesto = id;
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

  private animar(dt: number, mueve: boolean): void {
    if (this.enGesto) {
      const g = GESTOS[this.enGesto - 4];
      const a = this.acciones[g.clip];
      if (mueve || !this.suelo || this.agachado) this.enGesto = 0;
      else if (g.modo === "una" && a.time >= a.getClip().duration) {
        this.enGesto = 0;
        this.fundir("Idle", 0.2);
      }
    }
    let quiero: string;
    if (this.enGesto) quiero = this.actual;
    else if (!this.suelo) quiero = "Jump";
    else if (this.deslizando) quiero = "Deslizar";
    else if (this.agachado) quiero = "Agachado";
    else if (this.velH > 7) quiero = "Running";
    else if (this.velH > 0.4) quiero = "Walking";
    else quiero = "Idle";
    if (quiero === "Jump" && this.actual !== "Jump") this.saltarAnim();
    else if (quiero !== "Jump") this.fundir(quiero, quiero === "Deslizar" ? 0.1 : quiero === "Agachado" ? 0.15 : 0.22);
    this.acciones.Walking.timeScale = clamp(this.velH / PASO_ANDAR, 0.4, 3);
    this.acciones.Running.timeScale = clamp(this.velH / PASO_CORRER, 0.5, 3);
    this.mixer.update(dt);
  }

  /** Animación que se manda a los demás: 0 quieto, 1 andar, 2 correr, 3 saltar, 4-10 gestos, 11-12 `POSTURAS`. */
  get anim(): number {
    return this.enGesto || ({ Idle: 0, Walking: 1, Running: 2, Jump: 3, Agachado: 11, Deslizar: 12 }[this.actual] ?? 0);
  }

  get posicion(): Vector3 {
    return this.objeto.position;
  }
}
