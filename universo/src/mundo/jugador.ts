// El jugador: el robot (modelo glb con animaciones) y su movimiento: velocidad, gravedad, salto, pendientes,
// colisiones con círculos, pared invisible en el mar y el borde del mundo (`borde.ts`). Las unidades son metros y segundos.
// Movimiento con técnica: agacharse corriendo es deslizarse (con impulso cada DESLIZ_RECARGA s y que acelera cuesta
// abajo), saltar desde el deslizamiento o nada más caer conserva la velocidad (bunny hop), en el aire se gira sin
// frenar (air strafe), y el salto perdona un poco tarde (coyote) y un poco pronto (buffer).
// Y de piloto: en el aire, pegado a una pared (un círculo de colisión gordo o una pared del terreno) y yendo en
// paralelo, se corre por ella solo (wallrun); saltar ahí te despega hacia fuera (salto de pared), en el aire queda un
// doble salto que se recarga en el suelo o en una pared, y el gancho tira de ti hacia donde apuntas.
import {
  AnimationAction,
  AnimationClip,
  AnimationMixer,
  AnimationUtils,
  Box3,
  Color,
  BufferGeometry,
  Float32BufferAttribute,
  Group,
  Line,
  LineBasicMaterial,
  LoopOnce,
  Mesh,
  MeshStandardMaterial,
  Object3D,
  Vector3,
} from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { encerrar } from "./borde";
import { circulos, empujar } from "./colision";
import { GESTOS, gestoDe } from "./gestos";
import { clamp } from "./ruido";
import { NIVEL_MAR, SUELO_AGUA, PLAZA, enMuelle, pendienteEn, sueloEn, terrenoEn } from "./terreno";
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
/** Tope con impulsos encadenados (y del logro de velocidad); el paseo para los logros se recorta a 12 m/s de media. */
export const VEL_MAX = 20;
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
const PARED_MIN = 5; // m/s en horizontal para engancharse a una pared
const PARED_VEL = 11.5; // m/s a los que se acelera corriendo por la pared (corriendo por el suelo, VEL_CORRER)
const PARED_ACEL = 8;
const PARED_DUR = 1.75; // s como mucho en una pared: la gravedad vuelve poco a poco y al final te suelta
const PARED_ALCANCE = 0.35; // m entre el robot y la pared para engancharse
const PARED_RADIO = 0.5; // m: los círculos más finos (troncos, farolas, la columna de 0,3) no son pared
// shortcut: los círculos de colisión no tienen altura; se da por hecho que llegan a ALTO_CIRCULO sobre su suelo. Si
// se ve correr por el aire encima de una roca baja, guardar la altura de cada asset en el círculo.
const ALTO_CIRCULO = 8;
const PARED_EMPUJE = 5.5; // m/s hacia fuera al saltar desde la pared
const PARED_COYOTE = 0.15; // s tras dejar la pared en los que aún se salta desde ella
const PARED_REPETIR = 0.4; // s antes de volver a engancharse a la misma pared
const DOBLE_SALTO = 0.9; // del salto normal
const GANCHO_ALCANCE = 40;
const GANCHO_TIRON = 34; // m/s² hacia el ancla, contra 20 de gravedad
const GANCHO_DUR = 2;
const GANCHO_RECARGA = 3; // s desde que se suelta
const GANCHO_LLEGAR = 1.5; // m del ancla en los que se suelta solo, con el impulso que lleves
/** Lados a los que se busca pared del terreno, girando hacia donde vas: (seno, coseno) de ±90° y ±50°. */
const LADOS: [number, number][] = [90, -90, 50, -50].map((g) => [Math.sin((g * Math.PI) / 180), Math.cos((g * Math.PI) / 180)]);

/** A qué distancias del centro se busca pared del terreno. */
const SONDAS = [0.25, 0.55, RADIO + PARED_ALCANCE];

/** Normal (x, z) de una pared, hacia fuera; `circulo` si es un círculo de colisión (se rodea pegándose a él). */
interface Pared {
  x: number;
  z: number;
  circulo: boolean;
}

/** Trucos de piloto desde el último informe de paseo (los nombres son los del informe). `speed` y `chain`, máximos. */
export interface Tecnica {
  wallruns: number;
  walljumps: number;
  doublejumps: number;
  grapples: number;
  /** m/s en horizontal. */
  speed: number;
  /** Paredes seguidas sin tocar el suelo. */
  chain: number;
}

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
  /** La pared por la que corres, o null. La cámara se ladea con ella. */
  pared: Pared | null = null;
  /** Punto al que tira el gancho, o null. */
  gancho: Vector3 | null = null;
  /** Segundos hasta poder volver a lanzar el gancho. */
  recargaGancho = 0;
  /** Sin informar (sin devtools): lo recoge el informe de paseo. */
  readonly tecnica: Tecnica = { wallruns: 0, walljumps: 0, doublejumps: 0, grapples: 0, speed: 0, chain: 0 };
  /** La cuerda del gancho, en coordenadas del mundo: el mundo la añade a la escena. */
  readonly cuerda = new Line(new BufferGeometry(), new LineBasicMaterial({ color: 0x2a2a2a }));

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
  private tPared = 0;
  private desdePared = 9;
  private ultimaPared: Pared = { x: 0, z: 0, circulo: false };
  private cadena = 0;
  private doble = true;
  private tGancho = 0;
  private ladeo = 0;

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
    this.objeto.rotation.order = "YXZ"; // girar y luego ladear sobre su eje (en la pared)
    this.cuerda.geometry.setAttribute("position", new Float32BufferAttribute(6, 3));
    this.cuerda.frustumCulled = false;
    this.cuerda.visible = false;
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
    this.pared = null;
    this.soltarGancho();
    this.recargaGancho = 0;
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
    // Un muro fino o un escalón alto: más de un paso (0,6 m) por encima de los pies, más lo que deja subir la pendiente
    // máxima en lo que se avanza. La pendiente de abajo, medida a ±1 m, ve igual un muro fino desde fuera que desde dentro.
    if (t > this.y + 0.6 + 1.5 * Math.hypot(x - desdeX, z - desdeZ)) return false;
    // En el aire y por encima del suelo de destino no hay pendiente que valga: se salta a lo alto de un acantilado.
    if (!this.suelo && this.y >= t) return true;
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
    if (!this.gancho) this.recargaGancho = Math.max(0, this.recargaGancho - dt);
    this.desdePared += dt;
    this.coyote = this.suelo ? COYOTE : Math.max(0, this.coyote - dt);
    this.buffer = e.saltar ? BUFFER : Math.max(0, this.buffer - dt);
    this.desdeCaida += dt;
    this.agachado = e.agachar && !vuela;
    if (vuela) {
      this.pared = null;
      this.soltarGancho();
    }
    let rapidez = Math.hypot(this.vx, this.vz);
    if (!this.suelo && !vuela && !this.gancho) this.buscarPared(e, mueve, rapidez);

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

    if (this.gancho) {
      this.tirarGancho(dt, e);
    } else if (this.pared) {
      this.correrPared(dt);
    } else if (this.deslizando) {
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
        rapidez = VEL_MAX;
      }
      if (rapidez > this.tecnica.speed) this.tecnica.speed = rapidez;
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
      if (this.buffer > 0) {
        // Desde la pared (o nada más dejarla), desde el suelo (o nada más dejarlo) y, si no, el doble salto. Muy cerca
        // del suelo y cayendo, el salto se guarda para el bunny hop en vez de gastar el doble.
        if (this.pared || this.desdePared < PARED_COYOTE) this.saltarPared(e, mueve);
        else if (this.coyote > 0) {
          this.vy = VEL_SALTO;
          this.suelo = false;
          this.buffer = this.coyote = 0;
          this.deslizando = false; // saltar deslizándose conserva la velocidad
          if (!this.trucos) this.saltos++;
          this.saltarAnim();
        } else if (e.saltar && this.doble && (this.vy > 0 || this.y - suelo > 0.6)) this.saltarDoble(e, mueve);
      }
      if (!this.suelo) {
        // En la pared casi no se cae al principio y la gravedad vuelve según se acaba el tiempo.
        const g = this.pared ? GRAVEDAD * (0.12 + 0.88 * (this.tPared / PARED_DUR) ** 2) : GRAVEDAD;
        this.vy -= g * dt;
        if (this.pared) this.vy = Math.max(this.vy, -4);
        this.y += this.vy * dt;
        if (this.y <= suelo && this.vy <= 0) {
          // Al caer fuerte la cámara se hunde un poco.
          this.bajada = Math.max(this.bajada, clamp((-this.vy - 8) * 0.04, 0, 0.5));
          this.y = suelo;
          this.vy = 0;
          this.suelo = true;
          this.desdeCaida = 0;
          this.pared = null;
        }
      } else if (this.y - suelo > 0.35) {
        this.suelo = false; // se cae por un borde (o acaba de dejar de volar)
        this.vy = 0;
      } else {
        this.y = suelo;
      }
      if (this.suelo) {
        this.doble = true;
        this.cadena = 0;
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
    // En la pared el robot se ladea hacia fuera, con los pies contra ella. La derecha del robot es (-cos giro, sin giro)
    // y un ladeo positivo lo inclina hacia ella.
    const p = this.pared;
    const aDerecha = p ? p.x * Math.cos(this.giro) - p.z * Math.sin(this.giro) : 0;
    this.ladeo += (-0.35 * aDerecha - this.ladeo) * (1 - Math.exp(-dt * 12));
    this.bajada += ((this.deslizando ? 0.9 : this.agachado ? 0.55 : 0) - this.bajada) * (1 - Math.exp(-dt * 12));
    this.colocar();
    this.animar(dt, mueve);
  }

  /**
   * Lanza el gancho desde los ojos del robot en la dirección (dx, dy, dz), de longitud 1, contra el terreno y los
   * círculos de colisión. Con el gancho puesto, lo suelta. Devuelve si se ha enganchado.
   */
  lanzarGancho(dx: number, dy: number, dz: number): boolean {
    if (this.gancho) {
      this.soltarGancho();
      return false;
    }
    if (this.recargaGancho > 0 || this.volar || this.noclip) return false;
    const ancla = this.rayo(this.x, this.y + 1.6, this.z, dx, dy, dz);
    if (!ancla) return false;
    this.gancho = ancla;
    this.tGancho = 0;
    this.pared = null;
    this.deslizando = false;
    this.cuerda.visible = true;
    if (!this.trucos) this.tecnica.grapples++;
    return true;
  }

  /** Primer punto del terreno (no del mar) o de un círculo de colisión a menos de GANCHO_ALCANCE por el rayo. */
  private rayo(ox: number, oy: number, oz: number, dx: number, dy: number, dz: number): Vector3 | null {
    // Solo los círculos que pasan cerca del rayo visto desde arriba: se filtran una vez y no en cada paso.
    const lx = dx * GANCHO_ALCANCE;
    const lz = dz * GANCHO_ALCANCE;
    const l2 = lx * lx + lz * lz;
    const cerca = circulos.filter((c) => {
      const t = l2 > 1e-6 ? clamp(((c.x - ox) * lx + (c.z - oz) * lz) / l2, 0, 1) : 0;
      return Math.hypot(ox + lx * t - c.x, oz + lz * t - c.z) < c.r;
    });
    let antes = 0;
    for (let t = 0.5; t <= GANCHO_ALCANCE; t += 0.4) {
      const x = ox + dx * t;
      const y = oy + dy * t;
      const z = oz + dz * t;
      for (const c of cerca) {
        if (Math.hypot(x - c.x, z - c.z) < c.r && y < sueloEn(c.x, c.z) + ALTO_CIRCULO) return new Vector3(x, y, z);
      }
      if (y < sueloEn(x, z)) {
        if (terrenoEn(x, z) < NIVEL_MAR && !enMuelle(x, z)) return null; // en el agua no se engancha
        // Afinar entre el paso de antes y este: así la cuerda llega justo a la ladera.
        let a = antes;
        let b = t;
        for (let i = 0; i < 6; i++) {
          const m = (a + b) / 2;
          if (oy + dy * m < sueloEn(ox + dx * m, oz + dz * m)) b = m;
          else a = m;
        }
        return new Vector3(ox + dx * b, oy + dy * b, oz + dz * b);
      }
      antes = t;
    }
    return null;
  }

  private soltarGancho(): void {
    if (!this.gancho) return;
    this.gancho = null;
    this.recargaGancho = GANCHO_RECARGA;
    this.cuerda.visible = false;
  }

  /** Tira hacia el ancla (la gravedad sigue) y deja torcer un poco; se suelta al llegar o al acabarse el tiempo. */
  private tirarGancho(dt: number, e: Entrada): void {
    const a = this.gancho as Vector3;
    this.tGancho += dt;
    const dx = a.x - this.x;
    const dy = a.y - (this.y + 1);
    const dz = a.z - this.z;
    const d = Math.hypot(dx, dy, dz);
    if (d < GANCHO_LLEGAR || this.tGancho > GANCHO_DUR) {
      this.soltarGancho();
      return;
    }
    const k = (GANCHO_TIRON * dt) / d;
    this.vx += dx * k + e.dx * e.fuerza * AIRE_ACEL * 0.5 * dt;
    this.vz += dz * k + e.dz * e.fuerza * AIRE_ACEL * 0.5 * dt;
    this.vy = Math.min(this.vy + dy * k, 14);
    if (this.suelo && this.vy > 0) this.suelo = false;
  }

  /** Engancha, sigue o suelta la pared por la que se corre. */
  private buscarPared(e: Entrada, mueve: boolean, rapidez: number): void {
    if (this.pared) {
      const n = this.paredCerca();
      const fuera = mueve && e.dx * this.pared.x + e.dz * this.pared.z > 0.6; // empujar para separarse
      if (!n || this.agachado || this.tPared > PARED_DUR || fuera || rapidez < 3) {
        this.ultimaPared = this.pared;
        this.pared = null;
        this.desdePared = 0;
      } else this.pared = n;
      return;
    }
    if (!mueve || this.agachado || rapidez < PARED_MIN || this.y - sueloEn(this.x, this.z) < 0.4) return;
    const n = this.paredCerca();
    // De frente contra la pared no se engancha: hay que ir más o menos en paralelo (a menos de ~55°).
    if (!n || Math.abs(this.vx * n.x + this.vz * n.z) / rapidez > 0.82) return;
    if (this.desdePared < PARED_REPETIR && n.x * this.ultimaPared.x + n.z * this.ultimaPared.z > 0.8) return;
    this.pared = n;
    this.tPared = 0;
    this.vy = clamp(this.vy, 0, 2) + 1; // un pelín hacia arriba al engancharse
    this.doble = true;
    this.deslizando = false;
    this.cadena++;
    if (!this.trucos) {
      this.tecnica.wallruns++;
      this.tecnica.chain = Math.max(this.tecnica.chain, this.cadena);
    }
  }

  /** La pared más cerca al alcance: un círculo gordo o, a los lados de hacia donde vas, un muro del terreno. */
  private paredCerca(): Pared | null {
    let hueco = PARED_ALCANCE;
    let mejor: Pared | null = null;
    for (const c of circulos) {
      if (c.r < PARED_RADIO) continue;
      const dx = this.x - c.x;
      const dz = this.z - c.z;
      const d = Math.hypot(dx, dz);
      const h = d - c.r - RADIO;
      if (h < hueco && d > 1e-4 && this.y < sueloEn(c.x, c.z) + ALTO_CIRCULO) {
        hueco = h;
        mejor = { x: dx / d, z: dz / d, circulo: true };
      }
    }
    if (mejor) return mejor;
    const r = Math.hypot(this.vx, this.vz);
    if (r < 0.1) return null;
    const fx = this.vx / r;
    const fz = this.vz / r;
    for (const [sen, cos] of LADOS) {
      const px = fx * cos + fz * sen;
      const pz = fz * cos - fx * sen;
      // El terreno choca con el centro del robot, que puede llegar a la cara del muro: se mira cerca y al alcance,
      // para no pasarse de un muro fino.
      for (const d of SONDAS) {
        const qx = this.x + px * d;
        const qz = this.z + pz * d;
        if (terrenoEn(qx, qz) < this.y + 1) continue; // la pared tiene que llegar al pecho
        // La normal va cuesta abajo; dentro de un bloque (sin cuesta), hacia ti.
        const gx = terrenoEn(qx + 0.5, qz) - terrenoEn(qx - 0.5, qz);
        const gz = terrenoEn(qx, qz + 0.5) - terrenoEn(qx, qz - 0.5);
        const g = Math.hypot(gx, gz);
        const nx = g > 0.5 ? -gx / g : -px;
        const nz = g > 0.5 ? -gz / g : -pz;
        if (nx * px + nz * pz < -0.3) return { x: nx, z: nz, circulo: false };
        break;
      }
    }
    return null;
  }

  /** Corre a lo largo de la pared: quita lo que va contra ella y acelera hasta PARED_VEL sin frenar si se va más rápido. */
  private correrPared(dt: number): void {
    const n = this.pared as Pared;
    this.tPared += dt;
    const vn = this.vx * n.x + this.vz * n.z;
    let tx = this.vx - vn * n.x;
    let tz = this.vz - vn * n.z;
    let t = Math.hypot(tx, tz);
    if (t < 1e-3) {
      tx = -n.z;
      tz = n.x;
      t = 1;
    }
    const v = t < PARED_VEL ? Math.min(PARED_VEL, t + PARED_ACEL * dt) : t - 0.5 * dt;
    const pegar = n.circulo ? 1.5 : 0; // al rodear un círculo hay que ir girando hacia él; `empujar` no deja entrar
    this.vx = (tx / t) * v - n.x * pegar;
    this.vz = (tz / t) * v - n.z * pegar;
  }

  /** Salto desde la pared: hacia fuera, hacia donde empujas y un poco más rápido que antes. Recarga el doble salto. */
  private saltarPared(e: Entrada, mueve: boolean): void {
    const n = this.pared ?? this.ultimaPared;
    const antes = Math.hypot(this.vx, this.vz);
    let vx = this.vx + n.x * PARED_EMPUJE + (mueve ? e.dx * 2 : 0);
    let vz = this.vz + n.z * PARED_EMPUJE + (mueve ? e.dz * 2 : 0);
    const r = Math.max(Math.hypot(vx, vz), 1e-4);
    const v = Math.min(VEL_MAX, Math.max(r, antes + 0.8));
    vx *= v / r;
    vz *= v / r;
    this.vx = vx;
    this.vz = vz;
    this.vy = VEL_SALTO * 0.95;
    this.ultimaPared = n;
    this.pared = null;
    this.desdePared = PARED_COYOTE; // ya no se salta otra vez de ella, y aún tarda en volver a engancharse
    this.buffer = 0;
    this.doble = true;
    if (!this.trucos) this.tecnica.walljumps++;
    this.saltarAnim();
  }

  /** Doble salto: vuelve a subir y lleva lo que corrías hacia donde empujas. */
  private saltarDoble(e: Entrada, mueve: boolean): void {
    if (this.gancho) this.soltarGancho();
    this.vy = VEL_SALTO * DOBLE_SALTO;
    if (mueve) {
      const r = Math.max(Math.hypot(this.vx, this.vz), VEL_ANDAR);
      this.vx = e.dx * r;
      this.vz = e.dz * r;
    }
    this.doble = false;
    this.buffer = 0;
    if (!this.trucos) this.tecnica.doublejumps++;
    this.saltarAnim();
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
    this.objeto.rotation.z = this.ladeo;
    if (this.gancho) {
      const pos = this.cuerda.geometry.getAttribute("position") as Float32BufferAttribute;
      pos.setXYZ(0, this.x, this.y + 1.2, this.z);
      pos.setXYZ(1, this.gancho.x, this.gancho.y, this.gancho.z);
      pos.needsUpdate = true;
    }
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
    else if (this.pared) quiero = "Running"; // por la pared (los demás también lo ven correr)
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
