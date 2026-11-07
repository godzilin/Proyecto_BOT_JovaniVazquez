// Controles al estilo Roblox: teclado + arrastre de ratón en escritorio (o el ratón capturado, si se activa en
// ajustes); joystick dinámico, botón de salto, arrastre para la cámara y pellizco en móvil. Todo multitáctil por pointerId.
import { ajustes } from "./ajustes";

const RADIO_JOY = 56; // px que hay que empujar para el máximo
const ZONA_MUERTA = 0.12;
const UMBRAL_CORRER = 0.85;

type Rol = "joy" | "camara";

export class Controles {
  /** Movimiento deseado en pantalla: x a la derecha, y hacia delante; longitud 0..1. */
  mover = { x: 0, y: 0 };
  correr = false;
  /** Mantenido (C o el botón ⤓): agacharse, o deslizarse si se corre. */
  agachar = false;
  /** Se pone a true al pulsar saltar; el jugador lo consume. */
  saltar = false;
  /** Se pone a true con la tecla V o el botón 👁; la cámara lo consume. */
  cambiarVista = false;
  /** Giro y zoom acumulados desde la última lectura. */
  dYaw = 0;
  dPitch = 0;
  zoom = 1;
  /** Con `true` (sentado ante una máquina) el ratón no se captura y se suelta si lo estaba: hace falta el cursor. */
  libre = false;
  readonly tactil: boolean;

  private teclas = new Set<string>();
  private punteros = new Map<number, { rol: Rol; x: number; y: number }>();
  private joy: { id: number; ox: number; oy: number; x: number; y: number } | null = null;
  private joyVista: { base: HTMLElement; mando: HTMLElement };
  private botonSalto: HTMLElement;
  private botonAgachar: HTMLElement;
  private agacharTactil = false;
  private botonVista: HTMLElement;
  private punto: HTMLElement;

  constructor(private lienzo: HTMLElement, capa: HTMLElement) {
    this.tactil = window.matchMedia("(pointer: coarse)").matches;

    const base = document.createElement("div");
    base.className = "joy-base";
    const mando = document.createElement("div");
    mando.className = "joy-mando";
    base.append(mando);
    capa.append(base);
    this.joyVista = { base, mando };

    const salto = document.createElement("button");
    salto.className = "boton-salto";
    salto.type = "button";
    salto.setAttribute("aria-label", "Saltar");
    salto.textContent = "⤒";
    salto.hidden = !this.tactil;
    salto.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.saltar = true;
      salto.classList.add("activo");
    });
    for (const ev of ["pointerup", "pointercancel", "pointerleave"]) salto.addEventListener(ev, () => salto.classList.remove("activo"));
    capa.append(salto);
    this.botonSalto = salto;

    const agachar = document.createElement("button");
    agachar.className = "boton-agachar";
    agachar.type = "button";
    agachar.setAttribute("aria-label", "Agacharse (corriendo, deslizarse)");
    agachar.textContent = "⤓";
    agachar.hidden = !this.tactil;
    agachar.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.agacharTactil = true;
      agachar.classList.add("activo");
    });
    for (const ev of ["pointerup", "pointercancel", "pointerleave"])
      agachar.addEventListener(ev, () => {
        this.agacharTactil = false;
        agachar.classList.remove("activo");
      });
    capa.append(agachar);
    this.botonAgachar = agachar;

    const vista = document.createElement("button");
    vista.className = "boton-vista";
    vista.type = "button";
    vista.setAttribute("aria-label", "Cambiar entre primera y tercera persona");
    vista.textContent = "👁";
    vista.addEventListener("click", () => {
      this.cambiarVista = true;
      vista.blur(); // si no, la barra espaciadora (saltar) lo volvería a pulsar
    });
    capa.append(vista);
    this.botonVista = vista;

    // Punto de mira mientras el ratón está capturado: los clics (las máquinas del casino) apuntan al centro.
    const punto = document.createElement("div");
    punto.className = "punto-mira";
    punto.hidden = true;
    capa.append(punto);
    this.punto = punto;

    document.addEventListener("pointerlockchange", this.alCapturar);
    window.addEventListener("keydown", this.alBajarTecla);
    window.addEventListener("keyup", this.alSubirTecla);
    window.addEventListener("blur", this.soltarTodo);
    lienzo.addEventListener("pointerdown", this.alBajar);
    lienzo.addEventListener("pointermove", this.alMover);
    lienzo.addEventListener("pointerup", this.alSoltar);
    lienzo.addEventListener("pointercancel", this.alSoltar);
    lienzo.addEventListener("contextmenu", (e) => e.preventDefault());
    lienzo.addEventListener(
      "wheel",
      (e) => {
        e.preventDefault();
        this.zoom *= Math.exp(Math.sign(e.deltaY) * 0.1);
      },
      { passive: false },
    );
  }

  destruir(): void {
    window.removeEventListener("keydown", this.alBajarTecla);
    window.removeEventListener("keyup", this.alSubirTecla);
    window.removeEventListener("blur", this.soltarTodo);
    document.removeEventListener("pointerlockchange", this.alCapturar);
    this.soltarRaton();
    this.punto.remove();
    this.botonSalto.remove();
    this.botonAgachar.remove();
    this.botonVista.remove();
    this.joyVista.base.remove();
  }

  /** Recalcula `mover` y `correr` a partir de teclas y joystick. Se llama cada fotograma. */
  actualizar(): void {
    if (this.capturado && (this.libre || !ajustes.raton)) this.soltarRaton();
    const t = this.teclas;
    let x = (t.has("KeyD") || t.has("ArrowRight") ? 1 : 0) - (t.has("KeyA") || t.has("ArrowLeft") ? 1 : 0);
    let y = (t.has("KeyW") || t.has("ArrowUp") ? 1 : 0) - (t.has("KeyS") || t.has("ArrowDown") ? 1 : 0);
    const l = Math.hypot(x, y);
    if (l > 0) {
      x /= l;
      y /= l;
    }
    let correr = t.has("ShiftLeft") || t.has("ShiftRight");
    if (this.joy) {
      const jx = this.joy.x - this.joy.ox;
      const jy = this.joy.y - this.joy.oy;
      const m = Math.min(1, Math.hypot(jx, jy) / RADIO_JOY);
      if (m > ZONA_MUERTA) {
        const a = Math.atan2(jy, jx);
        x += Math.cos(a) * m;
        y += -Math.sin(a) * m;
        if (m > UMBRAL_CORRER) correr = true;
      }
    }
    const n = Math.hypot(x, y);
    if (n > 1) {
      x /= n;
      y /= n;
    }
    this.mover.x = x;
    this.mover.y = y;
    this.correr = correr;
    this.agachar = this.agacharTactil || t.has("KeyC");
  }

  consumirCamara(): { dYaw: number; dPitch: number; zoom: number } {
    const r = { dYaw: this.dYaw, dPitch: this.dPitch, zoom: this.zoom };
    this.dYaw = 0;
    this.dPitch = 0;
    this.zoom = 1;
    return r;
  }

  /** ¿Está el ratón capturado por el mundo? */
  get capturado(): boolean {
    return document.pointerLockElement === this.lienzo;
  }

  soltarRaton(): void {
    if (this.capturado) document.exitPointerLock();
  }

  private alCapturar = (): void => {
    this.punto.hidden = !this.capturado;
    this.punteros.clear();
  };

  /** Giro de cámara por píxeles movidos, con la sensibilidad y el eje vertical de los ajustes. */
  private girar(dx: number, dy: number, k: number): void {
    k *= ajustes.sensibilidad;
    this.dYaw -= dx * k;
    this.dPitch += (ajustes.invertirY ? -dy : dy) * k;
  }

  private alBajarTecla = (e: KeyboardEvent): void => {
    if (e.code === "Space") {
      if (!e.repeat) this.saltar = true;
      e.preventDefault();
    }
    if (e.code === "KeyV" && !e.repeat && !e.ctrlKey && !e.metaKey && !e.altKey) this.cambiarVista = true;
    if (/^(Key[WASDC]|Arrow(Up|Down|Left|Right)|Shift(Left|Right))$/.test(e.code)) {
      this.teclas.add(e.code);
      e.preventDefault();
    }
  };
  private alSubirTecla = (e: KeyboardEvent): void => {
    this.teclas.delete(e.code);
  };
  private soltarTodo = (): void => {
    this.teclas.clear();
    this.joy = null;
    this.joyVista.base.classList.remove("visible");
    this.punteros.clear();
  };

  private alBajar = (e: PointerEvent): void => {
    if (this.capturado) return;
    if (ajustes.raton && !this.libre && e.pointerType === "mouse" && e.button === 0) {
      // Si el navegador (o Discord) no deja capturar, sigue valiendo arrastrar.
      Promise.resolve(this.lienzo.requestPointerLock()).catch(() => undefined);
    }
    this.lienzo.setPointerCapture(e.pointerId);
    const mitadIzquierda = e.clientX < window.innerWidth / 2;
    if (e.pointerType === "touch" && mitadIzquierda && !this.joy) {
      this.joy = { id: e.pointerId, ox: e.clientX, oy: e.clientY, x: e.clientX, y: e.clientY };
      this.punteros.set(e.pointerId, { rol: "joy", x: e.clientX, y: e.clientY });
      const { base, mando } = this.joyVista;
      base.style.transform = `translate(${e.clientX}px, ${e.clientY}px)`;
      mando.style.transform = "translate(0px, 0px)";
      base.classList.add("visible");
      return;
    }
    this.punteros.set(e.pointerId, { rol: "camara", x: e.clientX, y: e.clientY });
  };

  private alMover = (e: PointerEvent): void => {
    if (this.capturado) return this.girar(e.movementX, e.movementY, 0.0025);
    const p = this.punteros.get(e.pointerId);
    if (!p) return;
    if (p.rol === "joy" && this.joy) {
      this.joy.x = e.clientX;
      this.joy.y = e.clientY;
      const dx = e.clientX - this.joy.ox;
      const dy = e.clientY - this.joy.oy;
      const l = Math.hypot(dx, dy);
      const k = l > RADIO_JOY ? RADIO_JOY / l : 1;
      this.joyVista.mando.style.transform = `translate(${dx * k}px, ${dy * k}px)`;
      return;
    }
    const camaras = [...this.punteros.entries()].filter(([, v]) => v.rol === "camara");
    if (camaras.length >= 2) {
      // Pellizco: la distancia entre los dos dedos de la cámara.
      const otro = camaras.find(([id]) => id !== e.pointerId)?.[1];
      if (otro) {
        const antes = Math.hypot(p.x - otro.x, p.y - otro.y);
        const ahora = Math.hypot(e.clientX - otro.x, e.clientY - otro.y);
        if (antes > 1 && ahora > 1) this.zoom *= antes / ahora;
      }
    } else {
      this.girar(e.clientX - p.x, e.clientY - p.y, e.pointerType === "touch" ? 0.006 : 0.0045);
    }
    p.x = e.clientX;
    p.y = e.clientY;
  };

  private alSoltar = (e: PointerEvent): void => {
    const p = this.punteros.get(e.pointerId);
    this.punteros.delete(e.pointerId);
    if (p?.rol === "joy") {
      this.joy = null;
      this.joyVista.base.classList.remove("visible");
    }
  };
}
