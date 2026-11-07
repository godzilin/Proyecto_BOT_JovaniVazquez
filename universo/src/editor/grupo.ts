// Multiselección del editor: arrastre de un grupo alrededor de un pivote, conjunto de elegidos y selección por caja.
// No sabe nada de piezas ni de assets: lo reusan el editor de assets y, más adelante, el del mundo.
import { Euler, Matrix4, type Object3D, Quaternion, Vector3 } from "three";
import type { Vec3 } from "./formato";

/** Posición, giro (radianes, Euler "YXZ" como `poner` de `mundo/geom.ts`) y escala de algo colocable. */
export interface Transformacion {
  pos: Vec3;
  rot: Vec3;
  esc: Vec3;
}

/**
 * Mueve un grupo entero con un solo gizmo: el gizmo mueve el `pivote` y cada elemento aplica el cambio del
 * pivote desde que empezó el arrastre. Con escala no uniforme y giro puede salir cizalla: se acepta lo que dé `decompose`.
 */
export class ArrastreGrupo {
  private readonly inversaInicio: Matrix4;
  private readonly matrices: Matrix4[];

  constructor(
    private readonly pivote: Object3D,
    inicio: Transformacion[],
  ) {
    pivote.updateMatrix();
    this.inversaInicio = pivote.matrix.clone().invert();
    this.matrices = inicio.map((t) => new Matrix4().compose(new Vector3(...t.pos), new Quaternion().setFromEuler(new Euler(...t.rot, "YXZ")), new Vector3(...t.esc)));
  }

  /** La transformación de cada elemento (en el orden de partida) con el pivote donde está ahora. */
  ahora(): Transformacion[] {
    this.pivote.updateMatrix();
    const cambio = this.pivote.matrix.clone().multiply(this.inversaInicio);
    const pos = new Vector3();
    const q = new Quaternion();
    const esc = new Vector3();
    const euler = new Euler();
    return this.matrices.map((m) => {
      cambio.clone().multiply(m).decompose(pos, q, esc);
      euler.setFromQuaternion(q, "YXZ");
      return { pos: pos.toArray(), rot: [euler.x, euler.y, euler.z], esc: esc.toArray() };
    });
  }
}

/** Índices elegidos y el principal (el último elegido, -1 si no hay nada). */
export class Seleccion {
  private readonly indices = new Set<number>();
  principal = -1;

  /** Los elegidos, de menor a mayor. */
  get lista(): number[] {
    return [...this.indices].sort((a, b) => a - b);
  }

  tiene(i: number): boolean {
    return this.indices.has(i);
  }

  vaciar(): void {
    this.indices.clear();
    this.principal = -1;
  }

  /** Deja solo `i` (o nada, si es negativo). */
  solo(i: number): void {
    this.poner(i >= 0 ? [i] : []);
  }

  /** Deja exactamente esos; el principal es el último. */
  poner(lista: number[]): void {
    this.vaciar();
    for (const i of lista) this.indices.add(i);
    this.principal = lista.length ? lista[lista.length - 1] : -1;
  }

  /** Añade `i` (y lo hace principal) o lo quita. */
  alternar(i: number): void {
    if (this.indices.delete(i)) {
      if (this.principal === i) this.principal = this.lista.pop() ?? -1;
    } else {
      this.indices.add(i);
      this.principal = i;
    }
  }

  /** Todos los de entre el principal e `i`, y los demás se sueltan (Mayús+clic en una lista). */
  rango(i: number): void {
    if (this.principal < 0) return this.solo(i);
    const [a, b] = [Math.min(this.principal, i), Math.max(this.principal, i)];
    this.poner(Array.from({ length: b - a + 1 }, (_, k) => a + k));
    this.principal = i;
  }

  /** Los `n` primeros, salvo los que `excluir` rechace. */
  todos(n: number, excluir: (i: number) => boolean = () => false): void {
    this.poner(Array.from({ length: n }, (_, i) => i).filter((i) => !excluir(i)));
  }

  /** Quita los índices que ya no existen (tras deshacer o borrar). */
  ajustar(n: number): void {
    for (const i of this.indices) if (i >= n) this.indices.delete(i);
    if (!this.indices.has(this.principal)) this.principal = this.lista.pop() ?? -1;
  }

  /** Reindexa tras quitar los índices `borrados` de la lista de origen; los borrados dejan de estar elegidos. */
  trasBorrar(borrados: number[]): void {
    const fuera = new Set(borrados);
    const nuevo = (i: number): number => i - borrados.filter((b) => b < i).length;
    const principal = this.principal;
    this.poner(this.lista.filter((i) => !fuera.has(i)).map(nuevo));
    if (principal >= 0 && !fuera.has(principal)) this.principal = nuevo(principal);
  }
}

/**
 * Selección por caja: Mayús+arrastrar con el botón izquierdo en `contenedor`. Para el evento antes de que lo vean los
 * OrbitControls (que con Mayús harían paneo) y al soltar llama a `alTerminar` con la caja en coordenadas de cliente.
 */
export class Recuadro {
  /** Verdadero si el último gesto con el puntero fue una caja (para que el clic de soltar no elija nada). */
  reciente = false;
  private inicio: { x: number; y: number } | null = null;
  private caja: HTMLDivElement | null = null;

  constructor(
    private readonly contenedor: HTMLElement,
    private readonly alTerminar: (rect: DOMRect, aditivo: boolean) => void,
    /** Si devuelve falso, la caja no arranca (p. ej. otra herramienta se queda el Mayús+arrastrar). */
    private readonly activo: () => boolean = () => true,
  ) {
    contenedor.addEventListener("pointerdown", this.alBajar, true);
  }

  private readonly alBajar = (e: PointerEvent): void => {
    this.reciente = false;
    if (e.button !== 0 || !e.shiftKey || !this.activo()) return;
    e.stopPropagation();
    e.stopImmediatePropagation(); // si los controles de la cámara escuchan en este mismo elemento, stopPropagation no basta
    e.preventDefault();
    this.reciente = true;
    this.inicio = { x: e.clientX, y: e.clientY };
    this.caja = document.createElement("div");
    Object.assign(this.caja.style, { position: "fixed", pointerEvents: "none", border: "1px solid #6cb6ff", background: "rgba(108,182,255,0.18)", display: "none", zIndex: "20" });
    this.contenedor.append(this.caja);
    window.addEventListener("pointermove", this.alMover, true);
    window.addEventListener("pointerup", this.alSoltar, true);
  };

  private rect(e: PointerEvent): DOMRect {
    const { x, y } = this.inicio!;
    return new DOMRect(Math.min(x, e.clientX), Math.min(y, e.clientY), Math.abs(e.clientX - x), Math.abs(e.clientY - y));
  }

  private readonly alMover = (e: PointerEvent): void => {
    const r = this.rect(e);
    Object.assign(this.caja!.style, { display: r.width < 4 && r.height < 4 ? "none" : "block", left: `${r.x}px`, top: `${r.y}px`, width: `${r.width}px`, height: `${r.height}px` });
  };

  private readonly alSoltar = (e: PointerEvent): void => {
    const r = this.rect(e);
    this.cerrar();
    if (r.width >= 4 || r.height >= 4) this.alTerminar(r, e.ctrlKey || e.metaKey);
  };

  private cerrar(): void {
    window.removeEventListener("pointermove", this.alMover, true);
    window.removeEventListener("pointerup", this.alSoltar, true);
    this.caja?.remove();
    this.caja = null;
    this.inicio = null;
  }

  dispose(): void {
    this.contenedor.removeEventListener("pointerdown", this.alBajar, true);
    this.cerrar();
  }
}
