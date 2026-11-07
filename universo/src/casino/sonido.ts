// Sonido de las tragaperras con Web Audio: MP3 decodificados una vez, cada máquina suena desde su sitio (PannerNode),
// un tope de voces, el zumbido de los rodillos sintetizado en directo y un registro de lo que suena para las pruebas.
import type { Camera, Vector3 } from "three";
import { Vector3 as V3 } from "three";

const CLAVE = "jv.sonido";
const MAX_VOCES = 12;
const ALCANCE = 28; // más lejos que esto no suena
const VOLUMEN = 0.8;

const archivos = import.meta.glob("../sonidos/*.mp3", { query: "?url", import: "default", eager: true }) as Record<string, string>;

export interface Manejo {
  parar(): void;
}

export interface Apunte {
  /** ms desde que se creó el sonido. */
  t: number;
  nombre: string;
  maquina: number;
  /** false si no ha llegado a sonar (silenciado, pestaña oculta o sin gesto todavía). */
  suena: boolean;
}

function leerSilencio(): boolean {
  try {
    return localStorage.getItem(CLAVE) === "no";
  } catch {
    return false;
  }
}

export class Sonido {
  readonly registro: Apunte[] = [];
  private ctx: AudioContext | null = null;
  private master: GainNode | null = null;
  private buffers = new Map<string, AudioBuffer>();
  private ruido: AudioBuffer | null = null;
  private voces = 0;
  private _silencio = leerSilencio();
  private cargado: Promise<void> | null = null;
  private oyente = new V3();
  private ref = performance.now();

  constructor(private readonly tactil: boolean) {
    document.addEventListener("visibilitychange", () => this.pausar(document.hidden));
  }

  get silenciado(): boolean {
    return this._silencio;
  }

  set silenciado(si: boolean) {
    this._silencio = si;
    try {
      localStorage.setItem(CLAVE, si ? "no" : "si");
    } catch {
      /* sin almacenamiento: se queda en memoria */
    }
    if (this.master) this.master.gain.value = si ? 0 : VOLUMEN;
  }

  /** Descarga y decodifica los MP3 (sin AudioContext real: vale un contexto sin conexión, así no hace falta un gesto). */
  cargar(): Promise<void> {
    this.cargado ??= (async () => {
      const decodificador = new OfflineAudioContext(1, 1, 44100);
      await Promise.all(
        Object.entries(archivos).map(async ([ruta, url]) => {
          try {
            const datos = await (await fetch(url)).arrayBuffer();
            this.buffers.set(ruta.replace(/^.*\/|\.mp3$/g, ""), await decodificador.decodeAudioData(datos));
          } catch (error) {
            console.warn("No se ha podido cargar un sonido", ruta, error);
          }
        }),
      );
    })();
    return this.cargado;
  }

  /** Se llama con un gesto del usuario (el primer toque en la máquina): crea o reanuda el contexto. */
  activar(): void {
    if (!this.ctx) {
      const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      if (!Ctx) return;
      this.ctx = new Ctx();
      this.master = this.ctx.createGain();
      this.master.gain.value = this._silencio ? 0 : VOLUMEN;
      const comp = this.ctx.createDynamicsCompressor();
      this.master.connect(comp).connect(this.ctx.destination);
      this.ruido = this.ctx.createBuffer(1, this.ctx.sampleRate, this.ctx.sampleRate);
      const d = this.ruido.getChannelData(0);
      for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1; // solo ambiente: no decide nada de la tirada
    }
    if (this.ctx.state === "suspended" && !document.hidden) void this.ctx.resume();
  }

  private pausar(si: boolean): void {
    if (!this.ctx) return;
    if (si) void this.ctx.suspend();
    else if (!this._silencio) void this.ctx.resume();
  }

  /** Coloca al oyente donde está la cámara (cada fotograma). */
  escuchar(camara: Camera): void {
    camara.getWorldPosition(this.oyente);
    const l = this.ctx?.listener;
    if (!l) return;
    const f = new V3(0, 0, -1).applyQuaternion(camara.quaternion);
    if (l.positionX) {
      l.positionX.value = this.oyente.x;
      l.positionY.value = this.oyente.y;
      l.positionZ.value = this.oyente.z;
      l.forwardX.value = f.x;
      l.forwardY.value = f.y;
      l.forwardZ.value = f.z;
      l.upX.value = 0;
      l.upY.value = 1;
      l.upZ.value = 0;
    }
  }

  private panner(pos: Vector3): PannerNode {
    const p = this.ctx!.createPanner();
    p.panningModel = this.tactil ? "equalpower" : "HRTF";
    p.distanceModel = "inverse";
    p.refDistance = 1.5;
    p.rolloffFactor = 1.1;
    p.maxDistance = ALCANCE;
    p.positionX.value = pos.x;
    p.positionY.value = pos.y;
    p.positionZ.value = pos.z;
    return p;
  }

  /** ¿Se oye un sonido en `pos`? (lejos o sin voces libres, no). Los grandes premios pasan por encima del tope. */
  private cabe(pos: Vector3, prioridad: boolean): boolean {
    if (this.oyente.distanceTo(pos) > ALCANCE) return false;
    return this.voces < MAX_VOCES + (prioridad ? 3 : 0);
  }

  private apuntar(nombre: string, maquina: number, suena: boolean): void {
    this.registro.push({ t: Math.round(performance.now() - this.ref), nombre, maquina, suena });
    if (this.registro.length > 400) this.registro.shift();
  }

  /** Suena un MP3 desde `pos`. Devuelve un manejo para cortarlo (la tensión). */
  poner(nombre: string, maquina: number, pos: Vector3, opciones: { volumen?: number; prioridad?: boolean } = {}): Manejo | null {
    if (this._silencio || !this.cabe(pos, !!opciones.prioridad)) return null;
    const buffer = this.buffers.get(nombre);
    const ctx = this.ctx;
    const suena = !!(buffer && ctx && ctx.state === "running");
    this.apuntar(nombre, maquina, suena);
    if (!suena || !buffer || !ctx || !this.master) return null;
    const fuente = ctx.createBufferSource();
    fuente.buffer = buffer;
    const g = ctx.createGain();
    g.gain.value = opciones.volumen ?? 1;
    const p = this.panner(pos);
    fuente.connect(g).connect(p).connect(this.master);
    this.voces++;
    fuente.onended = () => {
      this.voces--;
      p.disconnect();
    };
    fuente.start();
    return {
      parar: () => {
        g.gain.setTargetAtTime(0, ctx.currentTime, 0.04);
        fuente.stop(ctx.currentTime + 0.2);
      },
    };
  }

  /** Zumbido de rodillos: ruido filtrado con un ritmo de tics, más lento y grave cuanto menos giran. */
  zumbido(maquina: number, pos: Vector3): Zumbido | null {
    if (this._silencio || !this.cabe(pos, false)) return null;
    const ctx = this.ctx;
    const suena = !!(ctx && ctx.state === "running" && this.ruido && this.master);
    this.apuntar("zumbido", maquina, suena);
    if (!suena || !ctx || !this.ruido || !this.master) return null;
    this.voces++;
    return new Zumbido(ctx, this.ruido, this.panner(pos), this.master, () => this.voces--);
  }
}

export class Zumbido {
  private fuente: AudioBufferSourceNode;
  private filtro: BiquadFilterNode;
  private tics: OscillatorNode;
  private nivel: GainNode;
  private fin = false;

  constructor(
    private ctx: AudioContext,
    ruido: AudioBuffer,
    private panner: PannerNode,
    master: GainNode,
    private alTerminar: () => void,
  ) {
    this.fuente = ctx.createBufferSource();
    this.fuente.buffer = ruido;
    this.fuente.loop = true;
    this.filtro = ctx.createBiquadFilter();
    this.filtro.type = "bandpass";
    this.filtro.Q.value = 1.4;
    this.nivel = ctx.createGain();
    this.nivel.gain.value = 0;
    const tics = ctx.createGain(); // el ritmo de tics modula el volumen
    tics.gain.value = 0.5;
    this.tics = ctx.createOscillator();
    this.tics.type = "square";
    const profundidad = ctx.createGain();
    profundidad.gain.value = 0.5;
    this.tics.connect(profundidad).connect(tics.gain);
    this.fuente.connect(this.filtro).connect(tics).connect(this.nivel).connect(panner).connect(master);
    this.ajustar(1);
    this.fuente.start();
    this.tics.start();
  }

  /** `velocidad` de 0 (parado) a 1 (los tres girando). */
  ajustar(velocidad: number): void {
    if (this.fin) return;
    const t = this.ctx.currentTime;
    this.tics.frequency.setTargetAtTime(5 + 15 * velocidad, t, 0.12);
    this.filtro.frequency.setTargetAtTime(260 + 700 * velocidad, t, 0.12);
    this.nivel.gain.setTargetAtTime(0.05 + 0.1 * velocidad, t, 0.08);
  }

  parar(): void {
    if (this.fin) return;
    this.fin = true;
    const t = this.ctx.currentTime;
    this.nivel.gain.setTargetAtTime(0, t, 0.06);
    this.fuente.stop(t + 0.4);
    this.tics.stop(t + 0.4);
    this.fuente.onended = () => {
      this.panner.disconnect();
      this.alTerminar();
    };
  }
}
