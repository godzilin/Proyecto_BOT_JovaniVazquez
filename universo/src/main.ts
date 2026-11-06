// Lógica de la Activity del Jovaniverso: login con Discord, entrada al servidor del bot y perfil.
import { DiscordSDK } from "@discord/embedded-app-sdk";
import "./style.css";

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
}
interface ErrorResponse {
  error: string;
}

const FAILURE = "No se ha podido entrar en el Jovaniverso. Cierra la Activity y vuelve a abrirla.";
const app = document.getElementById("app") as HTMLElement;

/** Error con un texto en español que se puede enseñar tal cual. */
class ApiError extends Error {}

function el(tag: string, className: string, text: string): HTMLElement {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}

function show(...nodes: HTMLElement[]): void {
  app.replaceChildren(...nodes);
}

function showMessage(text: string, isError = false): void {
  show(el("p", isError ? "message error" : "message", text));
}

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

function renderProfile(profile: ProfileResponse): void {
  show(
    el("h1", "greeting", `Hola, ${profile.name}`),
    el("p", "balance", profile.balance_text),
    el("p", "guild", profile.guild),
    el("p", "footer", `Jovaniverso · versión ${profile.version} · prueba de concepto`),
  );
}

async function start(): Promise<void> {
  if (!new URLSearchParams(location.search).has("frame_id")) {
    showMessage("Esta página se abre desde Discord, con /universo o .universo.");
    return;
  }
  showMessage("Entrando en el Jovaniverso…");
  try {
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
    renderProfile(profile);
  } catch (error) {
    console.error(error);
    showMessage(error instanceof ApiError ? error.message : FAILURE, true);
  }
}

void start();
