// La conexión del multijugador: abre el WebSocket de la sala (/api/sala), manda tu pose en binario (13 bytes, como mucho
// 10 veces por segundo y solo si cambia), recibe la de los que tienes cerca (por regiones) y se la pasa a `Remotos`. Si se
// corta sola, reconecta con espera creciente. El protocolo (versión `PROTOCOLO`) está en `src/bot/services/universo_sala.py`. La sala no deja ir más rápido de lo
// que deja la física: antes de la pose de un teleporte manda `{t: "salto"}` y, al poner o quitar una devtool,
// `{t: "trucos", on}` (con ellas no cuenta el paseo, que también lo cuenta la sala).
import { MIN_Y_DM } from "./mundo/borde";
import { PRUEBAS } from "./mundo/modo";
import type { Remotos } from "./mundo/remotos";
import type { Sesion } from "./paseo";

/** Lo que la sala necesita saber de tu jugador. */
export interface PoseLocal {
  readonly x: number;
  readonly y: number;
  readonly z: number;
  readonly giro: number;
  readonly anim: number;
  /** Teleportes hechos desde que se abrió la página (sube con cada uno). */
  readonly viajes: number;
  /** Si hay alguna devtool puesta. */
  readonly trucos: boolean;
  /** Tiñe tu robot con el color que sale de tu id, el mismo con que te ven los demás. */
  colorear(id: string): void;
}

/**
 * Versión del protocolo (va en el saludo), tipos del mensaje binario (tu pose o lo cambiado del tick; todo lo que ves) y lo
 * que mide cada parte: tu pose, la cabecera de un grupo de una región y la pose de cada jugador de un grupo. El lado de una
 * región, en dm. Los vigila `test_universo_protocolo_ts.py`.
 */
export const PROTOCOLO = 2;
export const POSE = 2;
export const COMPLETO = 3;
export const POSE_BYTES = 13;
export const GRUPO_BYTES = 5;
export const ENTRADA_BYTES = 9;
export const REGION_DM = 1600;
const ESPERAS = [1000, 2000, 5000, 10000]; // ms; después, cada 10 s
const TAU = Math.PI * 2;

interface Jugadores {
  slot: number;
  id: string;
  nombre: string;
}

/**
 * Abre la sala y devuelve la función que la cierra. Sin `sesion` (fuera de Discord) usa el saludo de desarrollo.
 * `avisar` recibe el texto de un `error` del servidor (por ejemplo, que has entrado desde otro sitio).
 * `alMundo` se llama cuando alguien publica un mundo nuevo (`{t: "mundo", version}`), con su versión.
 */
/** Veces que la sala se ha cortado sola y se reconecta (la telemetría, `medidas.ts`). */
export let reconexiones = 0;

export function iniciarSala(
  yo: PoseLocal,
  remotos: Remotos,
  sesion: Sesion | null,
  avisar: (texto: string) => void = () => {},
  alMundo: (version: number) => void = () => {},
): () => void {
  const url = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/sala`;
  const buffer = new ArrayBuffer(POSE_BYTES); // se reutiliza en cada envío
  const vista = new DataView(buffer);
  let ws: WebSocket | null = null;
  let intento = 0;
  let parado = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  // Última pose enviada, ya cuantizada.
  let ux = NaN;
  let uy = NaN;
  let uz = NaN;
  let ug = -1;
  let ua = -1;
  let uv = 0; // teleportes ya avisados
  let ut = false; // devtools que sabe la sala

  // En el extraplano se entra en su propia sala (solo desarrolladores): los de la isla no ven a nadie volar.
  const mundo = PRUEBAS ? "pruebas" : undefined;
  const hola = (): string =>
    JSON.stringify(
      sesion
        ? { t: "hola", v: PROTOCOLO, session: sesion.session, guild_id: sesion.guildId, channel_id: sesion.channelId, mundo }
        : { t: "hola", v: PROTOCOLO, dev: "Prueba" },
    );

  const conectar = (): void => {
    if (parado) return;
    const socket = new WebSocket(url);
    socket.binaryType = "arraybuffer";
    ws = socket;
    let error = false;
    socket.onopen = () => {
      socket.send(hola());
      ux = NaN; // la pose se manda entera tras (re)conectar
      uv = yo.viajes; // la primera pose de cada conexión va sin mirar
      ut = false;
    };
    socket.onmessage = (e) => {
      if (typeof e.data === "string") {
        const m = JSON.parse(e.data) as {
          t: string;
          slot?: number;
          slots?: number[];
          id?: string;
          nombre?: string;
          jugadores?: Jugadores[];
          error?: string;
          version?: number;
        };
        if (m.t === "bienvenida") {
          intento = 0;
          if (m.id) yo.colorear(m.id);
          remotos.bienvenida(m.slot as number, m.jugadores ?? []);
        } else if (m.t === "entra") remotos.entra(m.slot as number, m.id as string, m.nombre as string);
        else if (m.t === "sale") remotos.sale(m.slot as number);
        else if (m.t === "lejos") for (const s of m.slots ?? []) remotos.lejos(s);
        else if (m.t === "mundo") alMundo(m.version ?? 0);
        // La tirada de otro en una tragaperras: la pinta el casino si está montado (casino.ts).
        else if (m.t === "traga") window.dispatchEvent(new CustomEvent("jv:traga", { detail: m }));
        else if (m.t === "error") {
          error = true;
          console.info("Sala del Jovaniverso:", m.error);
          if (m.error) avisar(m.error);
        }
        return;
      }
      const d = new DataView(e.data as ArrayBuffer);
      const tipo = d.byteLength > 0 ? d.getUint8(0) : 0;
      if (tipo !== POSE && tipo !== COMPLETO) return;
      // Con COMPLETO, quien no venga ya no se ve (has cambiado de región o acabas de entrar). Es raro: el Set no pesa.
      const vistos = tipo === COMPLETO ? new Set<number>() : null;
      const ahora = performance.now();
      let o = 1;
      while (o + GRUPO_BYTES <= d.byteLength) {
        // Grupo de una región: su esquina y cuántos trae; cada pose, desde esa esquina.
        const x0 = d.getInt16(o, true) * REGION_DM;
        const z0 = d.getInt16(o + 2, true) * REGION_DM;
        const n = d.getUint8(o + 4);
        o += GRUPO_BYTES;
        for (let i = 0; i < n && o + ENTRADA_BYTES <= d.byteLength; i++, o += ENTRADA_BYTES) {
          const slot = d.getUint8(o);
          vistos?.add(slot);
          remotos.pose(
            slot,
            (x0 + d.getInt16(o + 1, true)) / 10,
            (d.getUint16(o + 3, true) + MIN_Y_DM) / 10,
            (z0 + d.getInt16(o + 5, true)) / 10,
            (d.getUint8(o + 7) / 256) * TAU,
            d.getUint8(o + 8),
            ahora,
          );
        }
      }
      if (vistos) remotos.dejarSolo(vistos);
    };
    socket.onclose = () => {
      if (ws === socket) ws = null;
      remotos.vaciar();
      if (parado || error) return;
      reconexiones++;
      timer = setTimeout(conectar, ESPERAS[Math.min(intento++, ESPERAS.length - 1)]);
    };
  };

  const enviar = (): void => {
    if (!ws || ws.readyState !== WebSocket.OPEN || ws.bufferedAmount > 1024) return;
    if (yo.trucos !== ut) {
      ut = yo.trucos;
      ws.send(JSON.stringify({ t: "trucos", on: ut }));
    }
    if (yo.viajes !== uv) {
      uv = yo.viajes;
      ws.send('{"t":"salto"}');
    }
    const x = Math.round(yo.x * 10);
    const y = Math.round(yo.y * 10);
    const z = Math.round(yo.z * 10);
    const g = Math.round((yo.giro / TAU) * 256) & 255;
    const a = yo.anim;
    if (x === ux && y === uy && z === uz && g === ug && a === ua) return;
    ux = x;
    uy = y;
    uz = z;
    ug = g;
    ua = a;
    vista.setUint8(0, POSE);
    vista.setInt32(1, x, true);
    vista.setUint16(5, Math.max(0, y - MIN_Y_DM), true); // la altura, desde lo más bajo
    vista.setInt32(7, z, true);
    vista.setUint8(11, g);
    vista.setUint8(12, a);
    ws.send(buffer);
  };

  conectar();
  const latido = setInterval(enviar, 100);
  return () => {
    parado = true;
    clearInterval(latido);
    if (timer) clearTimeout(timer);
    ws?.close();
    remotos.vaciar();
  };
}
