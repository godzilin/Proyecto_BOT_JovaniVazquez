// Cliente de las rutas del editor del mundo (`/api/editor/*`, solo administradores): lanza el texto de error del servidor tal cual.
import type { MundoDoc } from "./formato";

export interface Sesion {
  session: string;
  guildId: string;
}
export interface Version {
  version: number;
  autor: string;
  fecha: string;
  kb: number;
}

/** Otro administrador ha guardado antes (409): hay que recargar. */
export class Conflicto extends Error {}

async function llamar<T>(s: Sesion, metodo: string, ruta: string, cuerpo?: unknown, crudo?: ArrayBuffer): Promise<T> {
  const headers: Record<string, string> = { Authorization: `Bearer ${s.session}` };
  let body: BodyInit | undefined;
  if (crudo) body = crudo;
  else if (cuerpo !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(cuerpo);
  }
  const r = await fetch(`/api/editor/${ruta}`, { method: metodo, headers, body });
  const datos: unknown = await r.json().catch(() => null);
  if (!r.ok) {
    const texto = (datos as { error?: string } | null)?.error ?? `Error ${r.status} del servidor.`;
    throw r.status === 409 ? new Conflicto(texto) : new Error(texto);
  }
  return datos as T;
}

const q = (s: Sesion): string => `?guild_id=${encodeURIComponent(s.guildId)}`;

export const borrador = (s: Sesion) => llamar<{ doc: MundoDoc; rev: number; version: number }>(s, "GET", `borrador${q(s)}`);
export const guardar = (s: Sesion, rev: number, doc: MundoDoc) =>
  llamar<{ rev: number }>(s, "PUT", "borrador", { guild_id: s.guildId, rev, doc });
export const publicar = (s: Sesion, rev: number) => llamar<{ version: number }>(s, "POST", "publicar", { guild_id: s.guildId, rev });
export const versiones = (s: Sesion) => llamar<{ versiones: Version[] }>(s, "GET", `versiones${q(s)}`);
export const volver = (s: Sesion, rev: number, version: number) =>
  llamar<{ rev: number }>(s, "POST", "volver", { guild_id: s.guildId, rev, version });
export const subirGlb = (s: Sesion, datos: ArrayBuffer) =>
  llamar<{ sha256: string; triangles: number }>(s, "POST", `glb${q(s)}`, undefined, datos);
