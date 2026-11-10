// Contrato entre el editor (`editor.ts`) y sus dos modos, «Mundo» y «Assets»: lo que cada modo ofrece y lo que el editor le presta.
import type { Sesion } from "./api";
import type { Borrador } from "./borrador";

/** Lo que el editor presta a cada modo. */
export interface Anfitrion {
  readonly b: Borrador;
  readonly sesion: Sesion;
  /** Capa donde abrir diálogos. */
  readonly raiz: HTMLElement;
  /** Aviso arriba del todo; `error` falso lo deja en verde. */
  aviso(texto: string, accion?: { texto: string; fn: () => void }, error?: boolean): void;
  /** Un error del servidor o de red, como aviso (y, si es un conflicto, con el botón de recargar). */
  fallo(e: unknown): void;
  /** Pide repintar la barra de estado. */
  estado(): void;
  /** Pasa a la pestaña «Assets» con ese asset elegido. */
  editarAsset(id: string): void;
}

/** Un modo del editor: tres columnas (lista, vista, propiedades) que se enseñan o se esconden. */
export interface Panel {
  /** Lista de la izquierda, vista del centro y propiedades de la derecha, en ese orden. */
  readonly elementos: HTMLElement[];
  activar(): void;
  desactivar(): void;
  /** El documento ha cambiado desde fuera (deshacer, rehacer, recargar). */
  alCambiarDoc(): void;
  /** Han llegado celdas del servidor: hay objetos nuevos al final de `doc.objetos` y los de antes siguen en su sitio. */
  alLlegarCeldas?(): void;
  /** Cifras del modo para la barra de estado; deja también al día sus botones. */
  estado(): string[];
  /** Atajo de teclado (fuera de un campo de texto); `true` si lo ha tratado. */
  tecla(e: KeyboardEvent, k: string, ctrl: boolean): boolean;
  dispose(): void;
}
