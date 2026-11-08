// Lógica de la Activity del Jovaniverso: login con Discord, entrada al servidor del bot, HUD y arranque del mundo 3D.
import { DiscordSDK } from "@discord/embedded-app-sdk";
import "./style.css";
import { activarCasino } from "./casino/entrada";
import type { Bote, Tragas } from "./casino/dibujo";
import { crearMundo, type Mundo } from "./mundo";
import { iniciarMenu } from "./menu";
import { iniciarGestos } from "./mundo/gestos";
import { PRUEBAS, irAMundo } from "./mundo/modo";
import { entrada as momentos, iniciarMedidas } from "./medidas";
import { iniciarPaseo } from "./paseo";
import { iniciarSala } from "./sala";
import { type Fuente, fuenteServidor } from "./mundo/cercania";

interface ConfigResponse {
  client_id: string;
}
interface TokenResponse {
  access_token: string;
  session: string;
}
interface ProfileResponse {
  name: string;
  guild: string;
  balance: number;
  balance_text: string;
  version: string;
  gestures: number[];
  /** Tiene permiso de editor (lo dan los desarrolladores; ser admin de Discord no basta). */
  editor?: boolean;
  /** Desarrollador del bot: devtools y extraplano. */
  dev?: boolean;
  /** Es desarrollador del bot: puede dar y quitar permisos. */
  permisos?: boolean;
  mundo?: number;
  /** Lo que necesitan las tragaperras del mundo; `null` si la tragaperras no está cargada (entonces no se juega). */
  tragas?: Tragas | null;
  /** Los botes del servidor para los letreros. */
  botes?: Bote[];
}
interface ErrorResponse {
  error: string;
}

const FAILURE = "No se ha podido entrar en el Jovaniverso. Cierra la Activity y vuelve a abrirla.";
const SIN_DISCORD = "Abre el Jovaniverso desde Discord para ver tu saldo";
const AYUDA_ESCRITORIO = "Tab para el menú (jugadores, mochila y viajar) · WASD para moverte · Shift para correr · Espacio para saltar · C para agacharte (corriendo, te deslizas; salta deslizándote para no perder velocidad) · Salta junto a una pared yendo en paralelo para correr por ella, y salta otra vez para saltar de ella · En el aire, Espacio otra vez es el doble salto · Q para el gancho hacia donde miras · Arrastra para girar la cámara · V o 👁 para primera persona · 1-7 o 👋 para gestos · ⚙ para capturar el ratón y cambiar el campo de visión";
const AYUDA_MOVIL = "Toca tu nombre para el menú (jugadores, mochila y viajar) · Mueve el joystick para andar; empújalo hasta el borde para correr · ⤓ para agacharte (corriendo, te deslizas) · Salta junto a una pared yendo en paralelo para correr por ella · En el aire, ⤒ otra vez es el doble salto · 🪝 para el gancho hacia donde miras · 👁 para primera persona · 👋 para gestos";

const $ = (id: string): HTMLElement => document.getElementById(id) as HTMLElement;

/** Error con un texto en español que se puede enseñar tal cual. */
class ApiError extends Error {}

async function api<T>(path: string, body?: unknown, session?: string): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (session) headers["Authorization"] = `Bearer ${session}`;
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const message = (data as ErrorResponse | null)?.error;
    throw message ? new ApiError(message) : new Error(`HTTP ${response.status}`);
  }
  return data as T;
}

function mostrarError(texto: string): void {
  const carga = $("carga");
  carga.classList.remove("fuera");
  const p = $("carga-texto");
  p.textContent = texto;
  p.className = "message error";
}

function pildora(nombre: string, saldo: string | null, texto?: string): void {
  const el = $("pildora");
  el.replaceChildren();
  if (texto) {
    el.textContent = texto;
  } else {
    el.append(nombre);
    if (saldo) {
      const s = document.createElement("span");
      s.className = "saldo";
      s.textContent = saldo;
      el.append(s);
    }
  }
  el.hidden = false;
}

/** Tocar tu nombre (la píldora de arriba a la izquierda) abre el menú: es la forma de abrirlo en el móvil. */
function menuEnPildora(menu: { alternar(): void }): void {
  const el = $("pildora");
  el.classList.add("jv-abre");
  el.setAttribute("role", "button");
  el.setAttribute("aria-label", "Abrir el menú");
  el.addEventListener("click", () => menu.alternar());
}

/** Aviso que se queda en pantalla (la sala se ha cerrado y no va a volver sola). */
function aviso(texto: string): void {
  const el = document.createElement("div");
  el.className = "ayuda";
  el.setAttribute("role", "status");
  el.textContent = texto;
  $("capa").append(el);
}

/** La ayuda se va sola a los pocos segundos o al empezar a moverse. */
function ayuda(): void {
  const el = $("ayuda");
  el.textContent = window.matchMedia("(pointer: coarse)").matches ? AYUDA_MOVIL : AYUDA_ESCRITORIO;
  el.hidden = false;
  const quitar = (): void => {
    el.classList.add("oculta");
    setTimeout(() => el.remove(), 700);
    window.removeEventListener("keydown", quitar);
    window.removeEventListener("pointerdown", quitar);
  };
  window.addEventListener("keydown", quitar);
  window.addEventListener("pointerdown", quitar);
  setTimeout(quitar, 9000);
}

/** La versión del mundo publicado que se está viendo (0: el de serie), para no volver a pedir la misma tras un aviso de la sala. */
let versionVista = 0;
/** Tras el aviso de la sala, cada página espera al azar hasta esto antes de pedir: así no llegan todas al NAS a la vez. */
const RETARDO_AVISO_MS = 3000;

/** La fuente del mundo publicado (su índice), o `null` si no hay nada publicado o falla (entonces se queda el de serie). */
function pedirMundo(sesion: { session: string; guildId: string | null }, version?: number): Promise<Fuente | null> {
  if (!sesion.guildId) return Promise.resolve(null);
  return fuenteServidor({ session: sesion.session, guildId: sesion.guildId }, version).then(
    (f) => {
      versionVista = f?.indice.version ?? 0;
      return f;
    },
    (e: unknown) => {
      console.warn("No se ha podido cargar el mundo publicado.", e);
      return null;
    },
  );
}

/**
 * Alguien ha publicado otro mundo (o lo bajado ya no está en el servidor: `version` 0, el de ahora): se pide el índice nuevo y la página
 * vuelve a bajar solo los trozos cercanos cuya versión ha cambiado.
 */
function cargarObras(mundo: Mundo, sesion: { session: string; guildId: string | null }, version: number, espera = RETARDO_AVISO_MS): void {
  if (!sesion.guildId) return;
  window.setTimeout(() => {
    if (version && versionVista >= version) return; // ya llegó con otro aviso
    void pedirMundo(sesion, version || undefined).then((f) => {
      if (f) mundo.cargarPublicado(f);
    });
  }, Math.random() * espera);
}

/** Deja en el mundo lo publicado (o el de serie si nunca se publicó nada); al cerrar el editor, para que el borrador no se quede a la vista. */
async function restaurarObras(mundo: Mundo, sesion: { session: string; guildId: string }): Promise<void> {
  try {
    const f = await fuenteServidor(sesion);
    versionVista = f?.indice.version ?? 0;
    if (f) mundo.cargarPublicado(f);
    else mundo.cargarSerie();
  } catch (e) {
    console.warn("No se ha podido cargar el mundo publicado.", e);
    aviso("No se ha podido recargar el mundo publicado: recarga la página para dejar de ver el borrador.");
  }
}

/** Botón del HUD que abre el editor (carga perezosa: no entra en el paquete inicial). */
function botonEditor(mundo: Mundo, sesion: { session: string; guildId: string }): void {
  const b = document.createElement("button");
  b.className = "ed-abrir";
  b.textContent = "🛠 Editor";
  b.addEventListener("click", () => {
    b.disabled = true;
    import("./editor/editor")
      .then((m) => m.abrirEditor(sesion, { mundo, recargar: () => restaurarObras(mundo, sesion) }))
      .catch((e: unknown) => aviso(e instanceof Error ? e.message : "No se ha podido abrir el editor."))
      .finally(() => (b.disabled = false));
  });
  $("capa").append(b);
}

/** Las devtools (solo desarrolladores), en un chunk aparte que no se descarga nadie más. */
function cargarDevtools(mundo: Mundo, sesion: { session: string; guildId: string } | null, gestor = false): void {
  import("./dev/devtools")
    .then((m) => m.iniciarDevtools(mundo, sesion, gestor))
    .catch((e: unknown) => console.error("No se han podido cargar las devtools", e));
}

interface Entrada {
  profile: ProfileResponse;
  session: string;
  guildId: string | null;
  channelId: string | null;
  platform: string;
}

async function entrar(): Promise<Entrada> {
  const { client_id } = await api<ConfigResponse>("/api/config");
  const sdk = new DiscordSDK(client_id);
  await sdk.ready();
  const { code } = await sdk.commands.authorize({
    client_id,
    response_type: "code",
    state: "",
    prompt: "none",
    scope: ["identify"],
  });
  const { access_token, session } = await api<TokenResponse>("/api/token", { code });
  await sdk.commands.authenticate({ access_token });
  momentos.login = performance.now();
  const profile = await api<ProfileResponse>(
    "/api/entrar",
    { guild_id: sdk.guildId, channel_id: sdk.channelId, platform: sdk.platform },
    session,
  );
  momentos.api = performance.now();
  return { profile, session, guildId: sdk.guildId, channelId: sdk.channelId, platform: sdk.platform };
}

async function start(): Promise<void> {
  const params = new URLSearchParams(location.search);
  const enDiscord = params.has("frame_id");
  try {
    const entrando = enDiscord ? entrar() : Promise.resolve(null);
    // Solo en desarrollo: `docs/editor_local.py` da una sesión con permiso de editor sin Discord.
    const dev =
      !enDiscord && import.meta.env.DEV && (params.has("editor") || params.has("devtools"))
        ? api<{ session: string; guild_id: string }>("/api/dev/sesion").catch(() => null)
        : Promise.resolve(null);
    // El mundo publicado se pide en cuanto el login dice su versión, mientras se monta lo demás, y el mundo se monta una
    // sola vez con él (o con el de serie). Hasta entonces sigue la pantalla de carga. El extraplano no tiene.
    const inicial: Promise<Fuente | null> = PRUEBAS
      ? Promise.resolve(null)
      : enDiscord
        ? entrando.then(
            (e) => (e && (e.profile.mundo ?? 0) > 0 ? pedirMundo(e, e.profile.mundo) : null),
            () => null, // el fallo del login lo enseña el `Promise.all` de abajo
          )
        : params.has("editor")
          ? dev.then((d) => (d ? pedirMundo({ session: d.session, guildId: d.guild_id }) : null))
          : Promise.resolve(null);
    const montado = crearMundo($("mundo"), $("capa"), inicial).then((m) => {
      momentos.mundo = performance.now();
      return m;
    });
    const [mundo, entrada] = await Promise.all([montado, entrando]);
    if (entrada) {
      if (PRUEBAS && !entrada.profile.dev) {
        irAMundo(false); // el extraplano es solo de los desarrolladores
        return;
      }
      pildora(entrada.profile.name, entrada.profile.balance_text);
      const sesion = { session: entrada.session, guildId: entrada.guildId, channelId: entrada.channelId };
      menuEnPildora(iniciarMenu($("capa"), mundo, sesion));
      // En el extraplano no hay paseo para los logros ni mundo publicado: solo la pista (y el casino).
      if (!PRUEBAS) {
        iniciarPaseo(mundo, sesion);
        iniciarMedidas(mundo, sesion, entrada.platform);
      }
      iniciarSala(mundo.yo, mundo.remotos, sesion, aviso, (version) => {
        if (!PRUEBAS) cargarObras(mundo, sesion, version);
      });
      // Si el servidor ya no tiene las celdas del índice que se tiene (se podó esa versión), se pide el de ahora.
      if (!PRUEBAS) mundo.alCaducar(() => cargarObras(mundo, sesion, 0, 500));
      iniciarGestos($("capa"), mundo.yo, entrada.profile.gestures ?? []);
      if (!PRUEBAS && entrada.profile.editor && entrada.guildId && !window.matchMedia("(pointer: coarse)").matches) {
        botonEditor(mundo, { session: entrada.session, guildId: entrada.guildId });
      }
      if (entrada.profile.dev) {
        const s = entrada.guildId ? { session: entrada.session, guildId: entrada.guildId } : null;
        cargarDevtools(mundo, s, entrada.profile.permisos === true);
      }
      const nombre = entrada.profile.name;
      activarCasino(mundo, { tragas: entrada.profile.tragas ?? null, botes: entrada.profile.botes ?? [], sesion, saldo: (t) => pildora(nombre, t) });
    } else {
      iniciarSala(mundo.yo, mundo.remotos, null);
      menuEnPildora(iniciarMenu($("capa"), mundo, null));
      iniciarGestos($("capa"), mundo.yo, [4, 5, 6, 7, 8, 9, 10]); // sin Discord (desarrollo) están todos
      pildora("", null, SIN_DISCORD);
      const local = await dev;
      if (import.meta.env.DEV && params.has("editor")) {
        if (!local) throw new Error("Sin sesión de docs/editor_local.py");
        botonEditor(mundo, { session: local.session, guildId: local.guild_id });
      }
      // Solo en desarrollo: `?devtools` las abre sin Discord (y `?mundo=pruebas`, el extraplano). Con `docs/editor_local.py`
      // usan su sesión y sale la sección de permisos (el servidor decide si vale).
      if (import.meta.env.DEV && params.has("devtools")) {
        cargarDevtools(mundo, local && { session: local.session, guildId: local.guild_id }, local !== null);
      }
      // Pruebas: con ?debug y sin Discord, el casino juega contra el `/api/entrar` de docs/sala_local.py.
      if (params.has("debug")) {
        const local = await api<ProfileResponse>("/api/entrar", {}).catch(() => null);
        if (local?.tragas) activarCasino(mundo, { tragas: local.tragas, botes: local.botes ?? [], sesion: null, saldo: (t) => pildora("Prueba", t) });
      }
    }
    $("carga").classList.add("fuera");
    ayuda();
  } catch (error) {
    console.error(error);
    mostrarError(error instanceof ApiError ? error.message : FAILURE);
  }
}

void start();
