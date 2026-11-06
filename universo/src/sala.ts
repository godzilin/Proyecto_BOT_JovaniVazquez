// La conexión del multijugador: abre el WebSocket de la sala (/api/sala), manda tu pose en binario (9 bytes, como mucho
// 10 veces por segundo y solo si cambia), recibe la de los demás y se la pasa a `Remotos`. Si se corta sola, reconecta
// con espera creciente. El protocolo está en `src/bot/services/universo_sala.py`.
import type { Remotos } from "./mundo/remotos";
import type { Sesion } from "./paseo";

/** Lo que la sala necesita saber de tu jugador. */
export interface PoseLocal {
  readonly x: number;
  readonly y: number;
  readonly z: number;
  readonly giro: number;
  readonly anim: number;
}

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
 */
export function iniciarSala(
  yo: PoseLocal,
  remotos: Remotos,
  sesion: Sesion | null,
  avisar: (texto: string) => void = () => {},
): () => void {
  const url = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/sala`;
  const buffer = new ArrayBuffer(9); // se reutiliza en cada envío
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

  const hola = (): string =>
    JSON.stringify(
      sesion ? { t: "hola", session: sesion.session, guild_id: sesion.guildId, channel_id: sesion.channelId } : { t: "hola", dev: "Prueba" },
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
    };
    socket.onmessage = (e) => {
      if (typeof e.data === "string") {
        const m = JSON.parse(e.data) as { t: string; slot?: number; id?: string; nombre?: string; jugadores?: Jugadores[]; error?: string };
        if (m.t === "bienvenida") {
          intento = 0;
          remotos.bienvenida(m.slot as number, m.jugadores ?? []);
        } else if (m.t === "entra") remotos.entra(m.slot as number, m.id as string, m.nombre as string);
        else if (m.t === "sale") remotos.sale(m.slot as number);
        else if (m.t === "error") {
          error = true;
          console.info("Sala del Jovaniverso:", m.error);
          if (m.error) avisar(m.error);
        }
        return;
      }
      const d = new DataView(e.data as ArrayBuffer);
      if (d.byteLength < 2 || d.getUint8(0) !== 1) return;
      const n = Math.min(d.getUint8(1), Math.floor((d.byteLength - 2) / 9));
      const ahora = performance.now();
      for (let i = 0; i < n; i++) {
        const o = 2 + i * 9;
        remotos.pose(
          d.getUint8(o),
          d.getInt16(o + 1, true) / 10,
          d.getInt16(o + 3, true) / 10,
          d.getInt16(o + 5, true) / 10,
          (d.getUint8(o + 7) / 256) * TAU,
          d.getUint8(o + 8),
          ahora,
        );
      }
    };
    socket.onclose = () => {
      if (ws === socket) ws = null;
      remotos.vaciar();
      if (parado || error) return;
      timer = setTimeout(conectar, ESPERAS[Math.min(intento++, ESPERAS.length - 1)]);
    };
  };

  const enviar = (): void => {
    if (!ws || ws.readyState !== WebSocket.OPEN || ws.bufferedAmount > 1024) return;
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
    vista.setUint8(0, 1);
    vista.setInt16(1, x, true);
    vista.setInt16(3, y, true);
    vista.setInt16(5, z, true);
    vista.setUint8(7, g);
    vista.setUint8(8, a);
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
