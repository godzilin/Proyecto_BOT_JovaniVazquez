// Cámara en tercera persona que orbita al jugador (yaw/pitch/distancia) sin meterse dentro del terreno.
import { PerspectiveCamera, Vector3 } from "three";
import { clamp } from "./ruido";
import { terrenoEn } from "./terreno";

const DIST_MIN = 3;


export class Camara {
  yaw = 0.3;
  pitch = 0.38;
  dist = 7.5;
  distMax = 16;
  private foco = new Vector3();
  private deseada = new Vector3();
  private primera = true;

  constructor(readonly camara: PerspectiveCamera) {}

  girar(dYaw: number, dPitch: number, zoom: number): void {
    this.yaw += dYaw;
    this.pitch = clamp(this.pitch + dPitch, -0.15, 1.35);
    this.dist = clamp(this.dist * zoom, DIST_MIN, this.distMax);
  }

  /** Dirección horizontal hacia la que mira la cámara (para mover "hacia delante"). */
  get adelante(): { x: number; z: number } {
    return { x: -Math.sin(this.yaw), z: -Math.cos(this.yaw) };
  }

  actualizar(dt: number, x: number, y: number, z: number): void {
    const objetivo = this.deseada.set(x, y + 1.6, z);
    if (this.primera) this.foco.copy(objetivo);
    else this.foco.lerp(objetivo, 1 - Math.exp(-dt * 14));
    this.primera = false;

    const cp = Math.cos(this.pitch);
    const dx = Math.sin(this.yaw) * cp;
    const dy = Math.sin(this.pitch);
    const dz = Math.cos(this.yaw) * cp;
    // Acerca la cámara si el rayo atraviesa el terreno.
    let d = this.dist;
    for (let t = 1.2; t <= this.dist; t += 0.6) {
      const px = this.foco.x + dx * t;
      const pz = this.foco.z + dz * t;
      if (this.foco.y + dy * t < Math.max(terrenoEn(px, pz), 0) + 0.5) {
        d = Math.max(1.2, t - 0.6);
        break;
      }
    }
    const cx = this.foco.x + dx * d;
    const cz = this.foco.z + dz * d;
    const cy = Math.max(this.foco.y + dy * d, Math.max(terrenoEn(cx, cz), 0) + 0.45);
    this.camara.position.set(cx, cy, cz);
    this.camara.lookAt(this.foco);
  }
}
