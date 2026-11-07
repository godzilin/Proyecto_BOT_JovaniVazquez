// Ayudas de DOM del editor: crear elementos y diálogos propios (en una Activity de Discord alert/confirm/prompt pueden estar bloqueados).

/** Crea un elemento con sus propiedades y sus hijos. */
export function h<K extends keyof HTMLElementTagNameMap>(
  etiqueta: K,
  props: Partial<HTMLElementTagNameMap[K]> = {},
  ...hijos: (Node | string)[]
): HTMLElementTagNameMap[K] {
  const el = Object.assign(document.createElement(etiqueta), props);
  el.append(...hijos);
  return el;
}

/** Ventana encima del editor. Devuelve el fondo (quitarlo la cierra) y la caja donde poner el contenido. */
export function modal(raiz: HTMLElement, titulo: string): { fondo: HTMLElement; caja: HTMLElement } {
  const caja = h("div", { className: "ed-caja" }, h("h2", { textContent: titulo }));
  caja.setAttribute("role", "dialog");
  caja.setAttribute("aria-label", titulo);
  const fondo = h("div", { className: "ed-fondo" }, caja);
  raiz.append(fondo);
  return { fondo, caja };
}

/**
 * Pregunta algo. Con `valor` pide un texto (devuelve lo escrito); sin él, solo confirma (devuelve "").
 * Devuelve `null` si se cancela.
 */
export function dialogo(raiz: HTMLElement, titulo: string, texto: string, ok: string, valor?: string): Promise<string | null> {
  return new Promise((resolver) => {
    const { fondo, caja } = modal(raiz, titulo);
    const campo = valor === undefined ? null : h("input", { type: "text", value: valor, maxLength: 40 });
    const fin = (v: string | null): void => {
      fondo.remove();
      resolver(v);
    };
    const aceptar = h("button", { className: "ed-b ed-primario", textContent: ok, onclick: () => fin(campo ? campo.value : "") });
    caja.append(
      h("p", { textContent: texto }),
      ...(campo ? [campo] : []),
      h("div", { className: "ed-fila" }, aceptar, h("button", { className: "ed-b", textContent: "Cancelar", onclick: () => fin(null) })),
    );
    caja.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Escape") fin(null);
      else if (e.key === "Enter" && campo) fin(campo.value);
    });
    (campo ?? aceptar).focus();
    campo?.select();
  });
}
