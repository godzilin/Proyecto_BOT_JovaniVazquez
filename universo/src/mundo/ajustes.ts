// Ajustes del jugador (ratón capturado, campo de visión, sensibilidad, invertir el eje vertical) y su panel ⚙.
// Se guardan en este navegador; si no hay almacenamiento, valen mientras dure la partida.

const CLAVE = "jv.ajustes";

export interface Ajustes {
  /** Escritorio: el ratón se queda dentro del mundo y gira la cámara sin arrastrar (Esc lo suelta). */
  raton: boolean;
  /** Campo de visión vertical en horizontal, en grados (en vertical se abre más, ver `mundo.ts`). */
  fov: number;
  /** Multiplica el giro de la cámara con el ratón o el dedo. */
  sensibilidad: number;
  invertirY: boolean;
}

const DEFECTO: Ajustes = { raton: false, fov: 55, sensibilidad: 1, invertirY: false };
const FOV = { min: 45, max: 90 }; // más de 90 deforma los bordes y deja entrar más cosas en el culling
const SENS = { min: 0.3, max: 3 };

const limitar = (v: unknown, min: number, max: number, defecto: number): number =>
  typeof v === "number" && Number.isFinite(v) ? Math.min(max, Math.max(min, v)) : defecto;

function leer(): Ajustes {
  try {
    const a = JSON.parse(localStorage.getItem(CLAVE) ?? "{}") as Partial<Ajustes>;
    return {
      raton: a.raton === true,
      fov: limitar(a.fov, FOV.min, FOV.max, DEFECTO.fov),
      sensibilidad: limitar(a.sensibilidad, SENS.min, SENS.max, DEFECTO.sensibilidad),
      invertirY: a.invertirY === true,
    };
  } catch {
    return { ...DEFECTO };
  }
}

/** Los ajustes en vigor; el panel los cambia en el sitio. */
export const ajustes: Ajustes = leer();

function guardar(): void {
  try {
    localStorage.setItem(CLAVE, JSON.stringify(ajustes));
  } catch {
    /* sin almacenamiento: vale con recordarlo en memoria */
  }
}

/** Crea el botón ⚙ y su panel en `capa`. `alCambiar` se llama tras cada cambio (para recalcular la cámara). */
export function iniciarAjustes(capa: HTMLElement, tactil: boolean, alCambiar: () => void): void {
  const boton = document.createElement("button");
  boton.className = "boton-ajustes";
  boton.type = "button";
  boton.setAttribute("aria-label", "Ajustes");
  boton.setAttribute("aria-expanded", "false");
  boton.textContent = "⚙";

  const panel = document.createElement("div");
  panel.className = "panel-ajustes";
  panel.hidden = true;
  const titulo = document.createElement("h2");
  titulo.textContent = "Ajustes";
  panel.append(titulo);

  const cambiar = (): void => {
    guardar();
    alCambiar();
  };

  const casilla = (texto: string, nota: string, clave: "raton" | "invertirY"): void => {
    const fila = document.createElement("label");
    fila.className = "ajuste ajuste-casilla";
    const caja = document.createElement("input");
    caja.type = "checkbox";
    caja.checked = ajustes[clave];
    caja.addEventListener("change", () => {
      ajustes[clave] = caja.checked;
      cambiar();
    });
    const t = document.createElement("span");
    t.textContent = texto;
    const n = document.createElement("small");
    n.textContent = nota;
    t.append(n);
    fila.append(caja, t);
    panel.append(fila);
  };

  const barra = (texto: string, clave: "fov" | "sensibilidad", min: number, max: number, paso: number, formato: (v: number) => string): void => {
    const fila = document.createElement("label");
    fila.className = "ajuste";
    const cabeza = document.createElement("span");
    cabeza.className = "ajuste-cabeza";
    const nombre = document.createElement("span");
    nombre.textContent = texto;
    const valor = document.createElement("output");
    valor.textContent = formato(ajustes[clave]);
    cabeza.append(nombre, valor);
    const r = document.createElement("input");
    r.type = "range";
    r.min = String(min);
    r.max = String(max);
    r.step = String(paso);
    r.value = String(ajustes[clave]);
    r.addEventListener("input", () => {
      ajustes[clave] = Number(r.value);
      valor.textContent = formato(ajustes[clave]);
      cambiar();
    });
    fila.append(cabeza, r);
    panel.append(fila);
  };

  if (!tactil) casilla("Capturar el ratón", "Haz clic en el mundo para girar sin arrastrar; Esc lo suelta", "raton");
  barra("Campo de visión", "fov", FOV.min, FOV.max, 1, (v) => `${v}°`);
  barra("Sensibilidad", "sensibilidad", SENS.min, SENS.max, 0.1, (v) => `×${v.toFixed(1)}`);
  casilla("Invertir el eje vertical", "Subir el ratón o el dedo mira hacia abajo", "invertirY");

  const abrir = (si: boolean): void => {
    panel.hidden = !si;
    boton.setAttribute("aria-expanded", String(si));
  };
  boton.addEventListener("click", () => {
    abrir(panel.hidden === true);
    boton.blur(); // si no, la barra espaciadora (saltar) lo volvería a pulsar
  });
  capa.append(panel, boton);

  // Las flechas mueven las barras, no al robot (los controles cogen las teclas en `window`).
  panel.addEventListener("keydown", (e) => {
    if (e.key === "Escape") abrir(false);
    e.stopPropagation();
  });
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") abrir(false);
  });
  window.addEventListener(
    "pointerdown",
    (e) => {
      const t = e.target as Node;
      if (!panel.hidden && !panel.contains(t) && !boton.contains(t)) abrir(false);
    },
    true,
  );
}
