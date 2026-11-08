// Calidad adaptativa: si los fps medios caen de ~45 unos segundos, baja la resolución (hasta 0,75) y luego quita las
// sombras; si pasan de ~55 un buen rato, lo devuelve escalón a escalón. Los tirones conocidos (montar el mundo, volver
// del editor o de otra pestaña) se callan con `callar`. La decisión es `Escalera`, sin three ni DOM, para probarla con
// Node (`tests/unit/test_universo_calidad.py`): este archivo no puede usar nada que no sea TS borrable.
import type { DirectionalLight, WebGLRenderer } from "three";

const OBJETIVO = 45; // bajo esto, se baja
const HOLGADO = 55; // sobre esto, se puede subir (entre los dos no se toca: así no oscila)
const ESPERA = 3; // segundos bajo el objetivo antes de bajar un escalón
const ESPERA_SUBIR = 15; // segundos holgados antes de subir uno
const ESPERA_SUBIR_MAX = 240;
const FALLIDA = 30; // si se baja antes de esto tras subir, la subida no compensaba: la próxima espera el doble
const ENFRIAR = 3; // segundos sin decidir tras un cambio (recompilar las sombras también da tirón)
const PASO = 0.25;
const MINIMO = 0.75;

/** La escalera de calidad: escalón 0, lo mejor; `escalones`, lo peor. */
export class Escalera {
  escalon = 0;
  fps = 60;
  readonly escalones: number;
  private mal = 0;
  private bien = 0;
  private enfriar = 2;
  private esperaSubir = ESPERA_SUBIR;
  private desdeSubida = Infinity;

  constructor(escalones: number) {
    this.escalones = escalones;
  }

  /** Durante `s` segundos no se decide nada (lo de antes tampoco cuenta). */
  callar(s: number): void {
    this.enfriar = Math.max(this.enfriar, s);
    this.mal = this.bien = 0;
  }

  /** `dt` es el tiempo real del fotograma. Devuelve 1 si hay que bajar un escalón, -1 si subir y 0 si nada. */
  medir(dt: number): number {
    if (dt <= 0 || dt > 1) return 0; // pausas (pestaña oculta) no cuentan
    this.fps += (1 / dt - this.fps) * Math.min(1, dt * 1.5);
    this.desdeSubida += dt;
    if (this.enfriar > 0) {
      this.enfriar -= dt;
      return 0;
    }
    if (this.fps < OBJETIVO) {
      this.bien = 0;
      this.mal += dt;
      if (this.mal < ESPERA) return 0;
      this.mal = 0;
      if (this.escalon >= this.escalones) return 0;
      if (this.desdeSubida < FALLIDA) this.esperaSubir = Math.min(ESPERA_SUBIR_MAX, this.esperaSubir * 2);
      this.escalon++;
      this.enfriar = ENFRIAR;
      return 1;
    }
    this.mal = 0;
    this.bien = this.fps >= HOLGADO ? this.bien + dt : 0;
    if (this.bien < this.esperaSubir || this.escalon === 0) return 0;
    this.bien = 0;
    this.escalon--;
    this.enfriar = ENFRIAR;
    this.desdeSubida = 0;
    return -1;
  }
}

export class Calidad {
  activa = true;
  ratio: number;
  sombras = true;
  private readonly renderer: WebGLRenderer;
  private readonly sol: DirectionalLight;
  private readonly inicial: number;
  private readonly pasos: number;
  private readonly escalera: Escalera;

  constructor(renderer: WebGLRenderer, sol: DirectionalLight, tope: number) {
    this.renderer = renderer;
    this.sol = sol;
    this.inicial = this.ratio = Math.min(window.devicePixelRatio || 1, tope);
    this.pasos = Math.max(0, Math.floor((this.inicial - MINIMO) / PASO + 1e-6));
    this.escalera = new Escalera(this.pasos + 1); // los de resolución y, el último, sin sombras
    renderer.setPixelRatio(this.ratio);
  }

  /** No decide nada durante `s` segundos: un tirón que se sabe que viene (montar el mundo, volver del editor). */
  callar(s: number): void {
    this.escalera.callar(s);
  }

  /** `dt` es el tiempo real del fotograma, sin recortar. */
  medir(dt: number): void {
    if (!this.activa || this.escalera.medir(dt) === 0) return;
    const e = this.escalera.escalon;
    const ratio = this.inicial - Math.min(e, this.pasos) * PASO;
    if (ratio !== this.ratio) {
      this.ratio = ratio;
      this.renderer.setPixelRatio(ratio);
    }
    const sombras = e <= this.pasos;
    if (sombras !== this.sombras) {
      this.sombras = sombras;
      this.sol.castShadow = sombras;
      this.renderer.shadowMap.enabled = sombras;
      this.renderer.shadowMap.needsUpdate = true;
    }
  }
}
