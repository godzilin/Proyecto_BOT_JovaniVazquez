// Menú del tabulador del Jovaniverso (Tab, o tocar tu nombre en el móvil): Jugadores (quién está dentro y el resto del servidor,
// con su saldo y su nivel), Mochila (lo que tienes de la tienda) y Viajar (los teleportes del mundo, que pone el editor).
// Lo que sale del servidor (`/api/jugadores`, `/api/mochila`) se pide al abrir la pestaña y vale `FRESCO_MS`.
import { h } from "./editor/dom";
import type { Mundo } from "./mundo";
import { PRUEBAS } from "./mundo/modo";
import type { Sesion } from "./paseo";
import { Ventana, cifra, ficha, fila, seccion, vacio } from "./ui/ventana";

interface Jugador {
  nombre: string;
  saldo: string;
  nivel: number | null;
  dentro: boolean;
  yo: boolean;
}
interface Jugadores {
  jugadores: Jugador[];
  miembros: number;
  dentro: number;
}
interface Objeto {
  nombre: string;
  emoji: string;
  tipo: string;
  cantidad: number;
  puesto: boolean;
  /** Segundos que le quedan; `null` si es para siempre. */
  caduca: number | null;
}

const FRESCO_MS = 10_000;
const SIN_DISCORD = "Abre el Jovaniverso desde Discord para ver esto.";

/** Lo que dura algo, en corto: 3 d, 5 h, 12 min. */
export function plazo(segundos: number): string {
  if (segundos >= 86_400) return `${Math.floor(segundos / 86_400)} d`;
  if (segundos >= 3600) return `${Math.floor(segundos / 3600)} h`;
  return `${Math.max(1, Math.floor(segundos / 60))} min`;
}

/** Datos de una pestaña: se piden si están viejos y, al llegar, se repinta la pestaña. */
class Datos<T> {
  valor: T | null = null;
  error: string | null = null;
  private hora = -Infinity;
  private cargando = false;

  constructor(
    private readonly url: () => string,
    private readonly sesion: Sesion,
    private readonly listo: () => void,
  ) {}

  pedir(): void {
    if (this.cargando || performance.now() - this.hora < FRESCO_MS) return;
    this.cargando = true;
    fetch(this.url(), { headers: { Authorization: `Bearer ${this.sesion.session}` } })
      .then(async (r) => {
        const d: unknown = await r.json().catch(() => null);
        if (!r.ok) throw new Error((d as { error?: string } | null)?.error ?? `Error ${r.status} del servidor.`);
        this.valor = d as T;
        this.error = null;
      })
      .catch((e: unknown) => (this.error = e instanceof Error ? e.message : "No se ha podido cargar."))
      .finally(() => {
        this.cargando = false;
        this.hora = performance.now();
        this.listo();
      });
  }
}

export interface Menu {
  alternar(): void;
}

export function iniciarMenu(capa: HTMLElement, mundo: Mundo, sesion: Sesion | null): Menu {
  const q = (): string => `guild_id=${encodeURIComponent(sesion?.guildId ?? "")}${PRUEBAS ? "&mundo=pruebas" : ""}`;
  let ventana: Ventana | null = null;
  const lista = sesion && new Datos<Jugadores>(() => `/api/jugadores?${q()}`, sesion, () => ventana?.repintar("jugadores"));
  const mochila = sesion && new Datos<{ objetos: Objeto[] }>(() => `/api/mochila?${q()}`, sesion, () => ventana?.repintar("mochila"));

  const pintarJugadores = (cuerpo: HTMLElement): void => {
    if (!lista) return void cuerpo.append(vacio(SIN_DISCORD));
    lista.pedir();
    const d = lista.valor;
    if (!d) return void cuerpo.append(vacio(lista.error ?? "Cargando…", lista.error !== null));
    const linea = (j: Jugador): HTMLLIElement => {
      const f = fila(
        j.dentro ? "🤖" : "👤",
        j.nombre,
        ...(j.yo ? [ficha("tú", "acento")] : []),
        ...(j.nivel !== null ? [ficha(`Nv ${j.nivel}`)] : []),
        cifra(j.saldo),
      );
      if (j.yo) f.classList.add("tuya");
      return f;
    };
    const dentro = d.jugadores.filter((j) => j.dentro);
    const fuera = d.jugadores.filter((j) => !j.dentro);
    cuerpo.append(seccion(`En el Jovaniverso · ${d.dentro}`));
    cuerpo.append(dentro.length ? h("ul", { className: "jv-lista" }, ...dentro.map(linea)) : vacio("Ahora mismo no hay nadie conectado."));
    cuerpo.append(seccion(`Del servidor · ${d.miembros} miembros`), h("ul", { className: "jv-lista" }, ...fuera.map(linea)));
    if (d.miembros - dentro.length > fuera.length) cuerpo.append(vacio(`Salen los ${fuera.length} con más saldo.`));
    if (d.jugadores.every((j) => j.nivel === null)) cuerpo.append(vacio("Este servidor no tiene los niveles activados."));
  };

  const pintarMochila = (cuerpo: HTMLElement): void => {
    if (!mochila) return void cuerpo.append(vacio(SIN_DISCORD));
    mochila.pedir();
    const d = mochila.valor;
    if (!d) return void cuerpo.append(vacio(mochila.error ?? "Cargando…", mochila.error !== null));
    if (!d.objetos.length) return void cuerpo.append(vacio("Tu mochila está vacía. Lo que compres en la tienda del bot sale aquí."));
    const tipos = new Map<string, Objeto[]>();
    for (const o of d.objetos) tipos.set(o.tipo, [...(tipos.get(o.tipo) ?? []), o]);
    for (const [tipo, objetos] of tipos) {
      cuerpo.append(
        seccion(tipo),
        h(
          "ul",
          { className: "jv-lista" },
          ...objetos.map((o) =>
            fila(
              o.emoji,
              o.nombre,
              ...(o.cantidad > 1 ? [ficha(`×${o.cantidad}`)] : []),
              ...(o.puesto ? [ficha("puesto", "acento")] : []),
              ...(o.caduca !== null ? [ficha(`caduca en ${plazo(o.caduca)}`)] : []),
            ),
          ),
        ),
      );
    }
    cuerpo.append(vacio("Para usar objetos o ponerte roles, abre la mochila desde /tienda en Discord."));
  };

  const pintarViajes = (cuerpo: HTMLElement): void => {
    const sitios = mundo.teleportes;
    cuerpo.append(seccion("Teleportes"));
    if (!sitios.length) return void cuerpo.append(vacio("Este mundo no tiene teleportes."));
    cuerpo.append(
      h(
        "div",
        { className: "jv-rejilla" },
        ...sitios.map((t) =>
          h("button", {
            className: "jv-boton",
            type: "button",
            textContent: t.nombre,
            onclick: () => {
              window.dispatchEvent(new Event("jv:viaje")); // el casino te levanta del asiento
              mundo.viajar(t);
              ventana?.cerrar();
            },
          }),
        ),
      ),
    );
  };

  ventana = new Ventana(
    capa,
    "Jovaniverso",
    [
      { clave: "jugadores", titulo: "Jugadores", pintar: pintarJugadores },
      { clave: "mochila", titulo: "Mochila", pintar: pintarMochila },
      { clave: "viajar", titulo: "Viajar", pintar: pintarViajes },
    ],
    "Tab o Esc para cerrar · ← → para cambiar de pestaña",
  );
  const v = ventana;
  const alternar = (): void => (v.abierta ? v.cerrar() : v.abrir());

  window.addEventListener(
    "keydown",
    (e) => {
      if (e.code !== "Tab" || e.ctrlKey || e.altKey || e.metaKey) return;
      // Con el editor abierto o escribiendo en un campo, el tabulador es el de siempre.
      const t = e.target as HTMLElement | null;
      if (document.querySelector(".ed") || (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      e.preventDefault();
      if (!e.repeat) alternar();
    },
    true,
  );
  return { alternar };
}
