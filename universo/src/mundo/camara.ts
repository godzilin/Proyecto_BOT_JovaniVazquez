// Cámara del jugador: tercera persona (orbita al robot), primera persona (a la altura de sus ojos) y anclada (fija en un punto, p. ej. sentado ante una máquina).
// Corriendo por una pared se ladea hacia fuera de ella (`inclinar`), suavizado.
import { PerspectiveCamera, Quaternion, Vector3 } from "three";
import type { LightShadow, Object3D } from "three";
import { CAPA_INTERIOR } from "./capas";
import { clamp } from "./ruido";
import { terrenoEn } from "./dentro"; // dentro de un interior, su suelo

const DIST_MIN = 3;
const OJOS = 1.65; // metros sobre los pies; el robot mide 1,9
const PITCH_PRIMERA = 1.3;
const TRANSICION = 0.4; // segundos que tarda en llegar a un ancla (o en volver de ella)
const EMPUJE_PRIMERA = 0.25; // zoom acumulado contra el mínimo de la tercera para pasar a primera
const CLAVE = "jv.camara";
/** Capa del propio robot cuando no se ve: la cámara no la dibuja, la del sol sí (ver `enlazarSombra`). */
const CAPA_PROPIA = 1;
const Z = new Vector3(0, 0, 1);

export type Modo = "tercera" | "primera";

export interface LimitesAncla {
  /** Radianes que se puede girar a cada lado del yaw del ancla. */
  yaw: number;
  pitchMin: number;
  pitchMax: number;
}

const envolver = (a: number): number => Math.atan2(Math.sin(a), Math.cos(a));

function leerModo(): Modo {
  try {
    return localStorage.getItem(CLAVE) === "primera" ? "primera" : "tercera";
  } catch {
    return "tercera";
  }
}

export class Camara {
  yaw = 0.3;
  /** Positivo = mira hacia abajo (en tercera, la cámara sube). */
  pitch = 0.38;
  dist = 7.5;
  distMax = 16;
  /** Devtools (noclip): la cámara atraviesa el terreno en vez de acercarse o subir por encima. */
  atraviesa = false;
  /** Ladeo buscado (radianes, + hacia la izquierda de la imagen); la cámara llega a él poco a poco. */
  inclinar = 0;
  private ladeo = 0;
  private giroZ = new Quaternion();
  private _modo: Modo = leerModo();
  private foco = new Vector3();
  private deseada = new Vector3();
  private inicio = true;
  private ancla: { pos: Vector3; yaw: number; limites?: LimitesAncla } | null = null;
  private trans = 1;
  private desdePos = new Vector3();
  private desdeQuat = new Quaternion();
  private pos = new Vector3();
  private quat = new Quaternion();
  private mira = new PerspectiveCamera();
  private propios: Object3D[] = [];
  private propioOculto: boolean | null = null;
  private empuje = 0;

  constructor(readonly camara: PerspectiveCamera) {}

  get modo(): Modo {
    return this._modo;
  }

  set modo(m: Modo) {
    if (m === this._modo) return;
    this._modo = m;
    this.pitch = this.limitarPitch(this.pitch);
    try {
      localStorage.setItem(CLAVE, m);
    } catch {
      /* sin almacenamiento: vale con recordarlo en memoria */
    }
  }

  alternar(): void {
    this.modo = this._modo === "primera" ? "tercera" : "primera";
  }

  /** ¿Está fija en un ancla? Mientras lo esté, el jugador no debe moverse con el joystick. */
  get anclada(): boolean {
    return this.ancla !== null;
  }

  /** Objetos del propio jugador: se esconden a la cámara en primera persona (pero siguen proyectando sombra). */
  proteger(objeto: Object3D): void {
    this.propios.push(objeto);
  }

  /**
   * La pasada de sombras de three comprueba las capas contra la cámara principal, así que un objeto que ella no ve
   * tampoco proyectaría sombra. Se lo damos solo mientras se dibuja la sombra (después de recoger lo visible).
   */
  enlazarSombra(sombra: LightShadow): void {
    const original = sombra.updateMatrices.bind(sombra);
    sombra.updateMatrices = (luz) => {
      this.camara.layers.enable(CAPA_PROPIA);
      original(luz);
    };
  }

  /** Primera persona fija en `pos`, mirando a (yaw, pitch), con transición suave y mirar alrededor dentro de `limites`. */
  anclar(pos: Vector3, yaw: number, pitch: number, limites?: LimitesAncla): void {
    this.iniciarTransicion();
    this.ancla = { pos: pos.clone(), yaw, limites };
    this.yaw = yaw;
    this.pitch = this.limitarPitch(pitch);
  }

  /** Vuelve al modo de antes, también con suavidad. */
  soltar(): void {
    if (!this.ancla) return;
    this.iniciarTransicion();
    this.ancla = null;
    this.pitch = this.limitarPitch(this.pitch);
  }

  girar(dYaw: number, dPitch: number, zoom: number): void {
    this.yaw += dYaw;
    this.pitch += dPitch;
    if (this.ancla) {
      const l = this.ancla.limites;
      if (l) this.yaw = this.ancla.yaw + clamp(envolver(this.yaw - this.ancla.yaw), -l.yaw, l.yaw);
    } else if (this._modo === "tercera") {
      this.dist = clamp(this.dist * zoom, DIST_MIN, this.distMax);
      // Seguir acercando contra el mínimo pasa a primera persona.
      this.empuje = this.dist <= DIST_MIN + 1e-3 && zoom < 1 ? this.empuje - Math.log(zoom) : 0;
      if (this.empuje > EMPUJE_PRIMERA) {
        this.empuje = 0;
        this.modo = "primera";
      }
    }
    this.pitch = this.limitarPitch(this.pitch);
  }

  /** Hacia dónde apunta (longitud 1); en tercera persona, algo más arriba que el centro de la imagen para apuntar desde el robot. */
  apuntar(): { x: number; y: number; z: number } {
    const p = this._modo === "primera" ? this.pitch : this.pitch - 0.3;
    const c = Math.cos(p);
    return { x: -Math.sin(this.yaw) * c, y: -Math.sin(p), z: -Math.cos(this.yaw) * c };
  }

  /** Derecha de la imagen en horizontal. */
  get derecha(): { x: number; z: number } {
    return { x: Math.cos(this.yaw), z: -Math.sin(this.yaw) };
  }

  /** Dirección horizontal hacia la que mira la cámara (para mover "hacia delante"). */
  get adelante(): { x: number; z: number } {
    return { x: -Math.sin(this.yaw), z: -Math.cos(this.yaw) };
  }

  actualizar(dt: number, x: number, y: number, z: number): void {
    const objetivo = this.deseada.set(x, y + 1.6, z);
    if (this.inicio) this.foco.copy(objetivo);
    else this.foco.lerp(objetivo, 1 - Math.exp(-dt * 14));
    this.inicio = false;

    const primera = this.ancla !== null || this._modo === "primera";
    this.ocultarPropios(primera);
    this.camara.layers.disable(CAPA_PROPIA);

    const cp = Math.cos(this.pitch);
    const dx = Math.sin(this.yaw) * cp;
    const dy = Math.sin(this.pitch);
    const dz = Math.cos(this.yaw) * cp;
    if (primera) {
      if (this.ancla) this.pos.copy(this.ancla.pos);
      else this.pos.set(x, y + OJOS, z);
      this.mira.position.copy(this.pos);
      this.mira.lookAt(this.pos.x - dx, this.pos.y - dy, this.pos.z - dz);
    } else {
      // Acerca la cámara si el rayo atraviesa el terreno.
      let d = this.dist;
      for (let t = 1.2; !this.atraviesa && t <= this.dist; t += 0.6) {
        const px = this.foco.x + dx * t;
        const pz = this.foco.z + dz * t;
        if (this.foco.y + dy * t < Math.max(terrenoEn(px, pz), 0) + 0.5) {
          d = Math.max(1.2, t - 0.6);
          break;
        }
      }
      const cx = this.foco.x + dx * d;
      const cz = this.foco.z + dz * d;
      const cy = this.atraviesa ? this.foco.y + dy * d : Math.max(this.foco.y + dy * d, Math.max(terrenoEn(cx, cz), 0) + 0.45);
      this.pos.set(cx, cy, cz);
      this.mira.position.copy(this.pos);
      this.mira.lookAt(this.foco);
    }
    this.quat.copy(this.mira.quaternion);
    this.ladeo += ((this.ancla ? 0 : this.inclinar) - this.ladeo) * (1 - Math.exp(-dt * 8));
    if (Math.abs(this.ladeo) > 1e-4) this.quat.multiply(this.giroZ.setFromAxisAngle(Z, this.ladeo));

    if (this.trans < 1) {
      this.trans = Math.min(1, this.trans + dt / TRANSICION);
      const s = this.trans * this.trans * (3 - 2 * this.trans);
      this.pos.lerpVectors(this.desdePos, this.pos, s);
      this.quat.copy(this.desdeQuat).slerp(this.quat, s);
    }
    this.camara.position.copy(this.pos);
    this.camara.quaternion.copy(this.quat);
    this.camara.updateMatrixWorld();
  }

  private iniciarTransicion(): void {
    this.desdePos.copy(this.camara.position);
    this.desdeQuat.copy(this.camara.quaternion);
    this.trans = 0;
  }

  private limitarPitch(p: number): number {
    const l = this.ancla?.limites;
    if (l) return clamp(p, l.pitchMin, l.pitchMax);
    return this.ancla || this._modo === "primera" ? clamp(p, -PITCH_PRIMERA, PITCH_PRIMERA) : clamp(p, -0.15, 1.35);
  }

  private ocultarPropios(si: boolean): void {
    if (si === this.propioOculto) return;
    this.propioOculto = si;
    for (const o of this.propios) {
      o.traverse((c) => {
        c.layers.set(si ? CAPA_PROPIA : 0);
        if (!si) c.layers.enable(CAPA_INTERIOR); // visible también dentro de un interior, donde la cámara solo ve esa capa
      });
    }
  }
}
