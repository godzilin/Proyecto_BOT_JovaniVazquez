// Lógica de la Activity del Jovaniverso: login con Discord, entrada al servidor del bot, HUD y arranque del mundo 3D.
import { DiscordSDK } from "@discord/embedded-app-sdk";
import "./style.css";
import { activarCasino } from "./casino/entrada";
import type { Tragas } from "./casino/dibujo";
import { crearMundo } from "./mundo";
import { iniciarGestos } from "./mundo/gestos";
import { iniciarPaseo } from "./paseo";
import { iniciarSala } from "./sala";

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
  /** La fila de tragaperras de la plaza; `null` si la tragaperras no está cargada (entonces no hay casino). */
  tragas?: Tragas | null;
}
interface ErrorResponse {
  error: string;
}

const FAILURE = "No se ha podido entrar en el Jovaniverso. Cierra la Activity y vuelve a abrirla.";
const SIN_DISCORD = "Abre el Jovaniverso desde Discord para ver tu saldo";
const AYUDA_ESCRITORIO = "WASD para moverte · Shift para correr · Espacio para saltar · Arrastra para girar la cámara · V o 👁 para primera persona · 1-7 o 👋 para gestos";
const AYUDA_MOVIL = "Mueve el joystick para andar; empújalo hasta el borde para correr · 👁 para primera persona · 👋 para gestos";

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

/** La ayuda se va sola a los pocos segundos o al empezar a moverse. */
/** Aviso que se queda en pantalla (la sala se ha cerrado y no va a volver sola). */
function aviso(texto: string): void {
  const el = document.createElement("div");
  el.className = "ayuda";
  el.setAttribute("role", "status");
  el.textContent = texto;
  $("capa").append(el);
}

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

interface Entrada {
  profile: ProfileResponse;
  session: string;
  guildId: string | null;
  channelId: string | null;
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
  const profile = await api<ProfileResponse>(
    "/api/entrar",
    { guild_id: sdk.guildId, channel_id: sdk.channelId, platform: sdk.platform },
    session,
  );
  return { profile, session, guildId: sdk.guildId, channelId: sdk.channelId };
}

async function start(): Promise<void> {
  const enDiscord = new URLSearchParams(location.search).has("frame_id");
  try {
    const [mundo, entrada] = await Promise.all([crearMundo($("mundo"), $("capa")), enDiscord ? entrar() : Promise.resolve(null)]);
    if (entrada) {
      pildora(entrada.profile.name, entrada.profile.balance_text);
      const sesion = { session: entrada.session, guildId: entrada.guildId, channelId: entrada.channelId };
      iniciarPaseo(mundo, sesion);
      iniciarSala(mundo.yo, mundo.remotos, sesion, aviso);
      iniciarGestos($("capa"), mundo.yo, entrada.profile.gestures ?? []);
      if (entrada.profile.tragas) {
        const nombre = entrada.profile.name;
        activarCasino(mundo, { tragas: entrada.profile.tragas, sesion, saldo: (t) => pildora(nombre, t) });
      }
    } else {
      iniciarSala(mundo.yo, mundo.remotos, null);
      iniciarGestos($("capa"), mundo.yo, [4, 5, 6, 7, 8, 9, 10]); // sin Discord (desarrollo) están todos
      pildora("", null, SIN_DISCORD);
      // Pruebas: con ?debug y sin Discord, el casino juega contra el `/api/entrar` de docs/sala_local.py.
      if (new URLSearchParams(location.search).has("debug")) {
        const local = await api<ProfileResponse>("/api/entrar", {}).catch(() => null);
        if (local?.tragas) activarCasino(mundo, { tragas: local.tragas, sesion: null, saldo: (t) => pildora("Prueba", t) });
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
