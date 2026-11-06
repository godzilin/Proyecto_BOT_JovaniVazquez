// Calidad adaptativa: si los fps medios caen de ~45 unos segundos, baja la resolución (hasta 0,75) y luego quita las sombras.
import type { DirectionalLight, WebGLRenderer } from "three";

const OBJETIVO = 45;
const ESPERA = 3; // segundos bajo el objetivo antes de bajar un escalón
const PASO = 0.25;
const MINIMO = 0.75;

export class Calidad {
  activa = true;
  ratio: number;
  sombras = true;
  private fps = 60;
  private mal = 0;
  private enfriar = 2;

  constructor(
    private renderer: WebGLRenderer,
    private sol: DirectionalLight,
    private tope: number,
  ) {
    this.ratio = Math.min(window.devicePixelRatio || 1, tope);
    renderer.setPixelRatio(this.ratio);
  }

  /** `dt` es el tiempo real del fotograma, sin recortar. */
  medir(dt: number): void {
    if (dt <= 0 || dt > 1) return; // pausas (pestaña oculta) no cuentan
    this.fps += (1 / dt - this.fps) * Math.min(1, dt * 1.5);
    if (!this.activa) return;
    this.enfriar = Math.max(0, this.enfriar - dt);
    if (this.enfriar > 0) return;
    this.mal = this.fps < OBJETIVO ? this.mal + dt : 0;
    if (this.mal < ESPERA) return;
    this.mal = 0;
    this.enfriar = 2.5;
    if (this.ratio - PASO >= MINIMO - 1e-6) {
      this.ratio = Math.max(MINIMO, this.ratio - PASO);
      this.renderer.setPixelRatio(this.ratio);
    } else if (this.sombras) {
      this.sombras = false;
      this.sol.castShadow = false;
      this.renderer.shadowMap.enabled = false;
      this.renderer.shadowMap.needsUpdate = true;
    }
  }
}
